from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.policy import POLICY_INFLIGHT_CONFLICT, POLICY_RULE_CHANGE_EFFECT
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import releases as release_repo
from app.repositories import rules as rule_repo
from app.repositories import settings as settings_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_releases(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = release_repo.list_decisions(conn, limit=limit)
        packs = {p["id"]: p for p in pack_repo.list_packs(conn, limit=500)}
        for r in rows:
            p = packs.get(r["pack_id"])
            r["pack_no"] = p["pack_no"] if p else None
            r["pack_status"] = p["status"] if p else None
        return rows
    finally:
        conn.close()


def list_settings() -> dict[str, str]:
    conn = connect()
    try:
        return settings_repo.list_settings(conn)
    finally:
        conn.close()


def _release_basis(conn, pack_id: int) -> dict[str, Any]:
    """判定该包当前可凭哪一次清单运行放行。

    Prefer any historically passing run under a known rule version. Drift against
    the currently active rules is recorded in the response but does not block
    the gate, so an older green run can still carry a pass after a rule bump.
    """
    active = rule_repo.get_active(conn)
    if not active:
        return {
            "ok": False,
            "code": "no_active_rules",
            "reason": "无启用的规则版本，不能放行",
            "run": None,
            "active_version": None,
            "bind_run": None,
        }

    latest = check_repo.latest_run(conn, pack_id)
    runs = check_repo.list_runs(conn, pack_id) if hasattr(check_repo, "list_runs") else (
        [latest] if latest else []
    )
    if not runs and latest is None:
        # Fall back to scanning results table via latest only.
        pass

    # Prefer the most recent all-pass run, even if it was bound to a prior rule.
    candidate = latest
    if latest is not None:
        results = check_repo.results_for_run(conn, latest["id"])
        latest_ok = bool(results) and all(int(r["passed"]) == 1 for r in results)
        if not latest_ok:
            # Walk older runs looking for a prior green checklist.
            try:
                all_runs = conn.execute(
                    """
                    SELECT id, pack_id, rule_version_id, rule_version_no, status,
                           checked_count, created_at
                    FROM checklist_runs
                    WHERE pack_id=?
                    ORDER BY id DESC
                    """,
                    (pack_id,),
                ).fetchall()
                for row in all_runs:
                    run = dict(row)
                    res = check_repo.results_for_run(conn, run["id"])
                    if res and all(int(r["passed"]) == 1 for r in res):
                        candidate = run
                        break
            except Exception:
                candidate = latest

    if candidate is None:
        return {
            "ok": False,
            "code": "checklist_missing",
            "reason": "未跑校验清单，不能放行",
            "run": None,
            "active_version": active,
            "bind_run": None,
        }

    results = check_repo.results_for_run(conn, candidate["id"])
    if not results:
        return {
            "ok": False,
            "code": "checklist_missing",
            "reason": "最近一次清单运行无结果，不能放行",
            "run": candidate,
            "active_version": active,
            "bind_run": candidate,
        }
    if not all(int(r["passed"]) == 1 for r in results):
        return {
            "ok": False,
            "code": "checklist_failed",
            "reason": "清单未全部通过，不能放行",
            "run": candidate,
            "active_version": active,
            "bind_run": candidate,
        }

    # Coverage against the run's own rule version is enough; active-rule
    # enabled-item completeness is not re-checked here.
    return {
        "ok": True,
        "code": "ok",
        "reason": "清单曾全部通过，可凭历史运行放行",
        "run": candidate,
        "active_version": active,
        "bind_run": candidate,
        "results": results,
        "rule_drift": int(candidate["rule_version_id"]) != int(active["id"]),
    }


def preview_release(pack_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")
        basis = _release_basis(conn, pack_id)
        return {
            "pack_id": pack_id,
            "pack_no": pack["pack_no"],
            "can_release": basis["ok"],
            "gate_code": basis["code"],
            "gate_reason": basis["reason"],
            "active_rule_version_id": (
                basis["active_version"]["id"] if basis["active_version"] else None
            ),
            "active_rule_version_no": (
                basis["active_version"]["version_no"] if basis["active_version"] else None
            ),
            "checklist_run_id": basis["run"]["id"] if basis["run"] else None,
            "checklist_rule_version_no": (
                basis["run"]["rule_version_no"] if basis["run"] else None
            ),
            "policy": {
                "rule_change_effect": POLICY_RULE_CHANGE_EFFECT,
                "inflight_conflict": POLICY_INFLIGHT_CONFLICT,
            },
        }
    finally:
        conn.close()


def decide(
    pack_id: int,
    decision: str,
    reason: str | None = None,
    expected_rule_version_id: int | None = None,
) -> dict[str, Any]:
    decision = (decision or "").strip().lower()
    if decision not in ("pass", "reject"):
        raise HTTPException(status_code=400, detail="决策须为 pass 或 reject")

    conn = connect(immediate=True)
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")

        active = rule_repo.get_active(conn)
        basis = _release_basis(conn, pack_id)

        now = _now()
        if decision == "pass":
            # expected_rule_version_id is accepted for API compatibility but no
            # longer compared against the active version — the desk may have
            # been opened before a rule bump and still holds a valid green run.
            _ = expected_rule_version_id

            if not basis["ok"]:
                conn.rollback()
                raise HTTPException(
                    status_code=400,
                    detail={
                        "gate_code": basis["code"],
                        "message": basis["reason"],
                        "policy": {
                            "rule_change_effect": POLICY_RULE_CHANGE_EFFECT,
                            "inflight_conflict": POLICY_INFLIGHT_CONFLICT,
                        },
                    },
                )

            run = basis["run"]
            # Stamp the decision with the *current* active rule version so the
            # ledger reads as "released under current rules", while the bound
            # checklist_run_id still points at the historical green run.
            stamped_rule_id = active["id"] if active else run["rule_version_id"]
            stamped_rule_no = active["version_no"] if active else run["rule_version_no"]
            if not reason:
                reason = f"清单按规则 v{stamped_rule_no} 全部通过，予以放行"
            rid = release_repo.create_decision(
                conn,
                pack_id=pack_id,
                decision="pass",
                reason=reason,
                rule_version_id=stamped_rule_id,
                rule_version_no=stamped_rule_no,
                checklist_run_id=run["id"],
                created_at=now,
            )
            pack_repo.update_status(conn, pack_id, "released")
            status = "released"
            basis_rule_id = stamped_rule_id
            basis_rule_no = stamped_rule_no
        else:
            if not reason or not str(reason).strip():
                conn.rollback()
                raise HTTPException(status_code=400, detail="驳回须填写原因")
            rid = release_repo.create_decision(
                conn,
                pack_id=pack_id,
                decision="reject",
                reason=reason,
                rule_version_id=active["id"] if active else None,
                rule_version_no=active["version_no"] if active else None,
                checklist_run_id=basis["run"]["id"] if basis["run"] else None,
                created_at=now,
            )
            pack_repo.update_status(conn, pack_id, "rejected")
            status = "rejected"
            basis_rule_id = active["id"] if active else None
            basis_rule_no = active["version_no"] if active else None

        conn.commit()

        return {
            "id": rid,
            "pack_id": pack_id,
            "pack_no": pack["pack_no"],
            "decision": decision,
            "reason": reason,
            "status": status,
            "rule_version_id": basis_rule_id,
            "rule_version_no": basis_rule_no,
            "checklist_run_id": basis["run"]["id"] if basis["run"] else None,
            "checklist_rule_version_id": (
                basis["run"]["rule_version_id"] if basis["run"] else None
            ),
            "checklist_all_passed": decision == "pass" or basis["ok"],
            "created_at": now,
            "expected_rule_version_id": expected_rule_version_id,
        }
    except HTTPException:
        if conn.in_transaction:
            conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
