"""放行闸门：留痕规则版本必须与所绑清单结果版本一致。

全局互斥策略（见 ``app.policy``）：唯一贯彻「作废旧通过 + 强制整表复检」——
- 规则变更使历史「清单通过」及其通过留痕作废，只能凭**绑定当前启用版本**的复检放行；
- 放行写入时若发现版本已变（或发起时的版本已不是当前版本），拒绝该次放行、
  不写出通过留痕，包落到待复检。

闸门不认「历史上曾经全过」，也不按当前版本号重盖旧运行；判定只依据最近一次
清单运行，并要求：运行绑定版本 == 当前启用版本、结果逐项绑定同一版本、
结果覆盖当前启用项且全过。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.policy import (
    BLOCKING_PACK_STATUSES,
    POLICY_INFLIGHT_CONFLICT,
    POLICY_RULE_CHANGE_EFFECT,
    STATUS_RECHECK,
)
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import releases as release_repo
from app.repositories import rules as rule_repo

# 闸门结论
GATE_OK = "ok"
GATE_NO_ACTIVE = "no_active_rules"
GATE_MISSING = "checklist_missing"
GATE_FAILED = "checklist_failed"
GATE_DRIFT = "rule_version_drift"
GATE_BINDING = "checklist_binding_broken"
GATE_COVERAGE = "checklist_coverage_mismatch"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _policy_payload() -> dict[str, str]:
    return {
        "rule_change_effect": POLICY_RULE_CHANGE_EFFECT,
        "inflight_conflict": POLICY_INFLIGHT_CONFLICT,
    }


def list_releases(limit: int = 50) -> list[dict[str, Any]]:
    """放行留痕；附带所绑清单运行的版本，供互证。"""
    conn = connect()
    try:
        rows = release_repo.list_decisions(conn, limit=limit)
        packs = {p["id"]: p for p in pack_repo.list_packs(conn, limit=500)}
        for r in rows:
            p = packs.get(r["pack_id"])
            r["pack_no"] = p["pack_no"] if p else None
            r["pack_status"] = p["status"] if p else None
            # 绑定互证：通过留痕的规则版本必须与清单运行绑定的版本一致，
            # 且运行本身必须存在。
            if r["checklist_run_id"] is None:
                r["binding_consistent"] = r["decision"] != "pass"
            else:
                r["binding_consistent"] = (
                    r.get("checklist_rule_version_id") is not None
                    and r["rule_version_id"] is not None
                    and int(r["rule_version_id"]) == int(r["checklist_rule_version_id"])
                    and r["rule_version_no"] == r.get("checklist_rule_version_no")
                )
        return rows
    finally:
        conn.close()


def _release_basis(conn, pack_id: int) -> dict[str, Any]:
    """判定该包能否凭其**最近一次**清单运行放行。

    只认绑定当前启用版本、覆盖当前启用项且全过的运行；任何版本漂移都不放行。
    """
    active = rule_repo.get_active(conn)
    if not active:
        return {
            "ok": False,
            "code": GATE_NO_ACTIVE,
            "reason": "无启用的规则版本，不能放行",
            "run": None,
            "results": [],
            "active_version": None,
            "rule_drift": False,
            "missing_check_codes": [],
            "extra_check_codes": [],
        }

    run = check_repo.latest_run(conn, pack_id)
    if run is None:
        return {
            "ok": False,
            "code": GATE_MISSING,
            "reason": "未跑校验清单，不能放行",
            "run": None,
            "results": [],
            "active_version": active,
            "rule_drift": False,
            "missing_check_codes": [],
            "extra_check_codes": [],
        }

    results = check_repo.results_for_run(conn, run["id"])
    base = {
        "ok": False,
        "run": run,
        "results": results,
        "active_version": active,
        "rule_drift": int(run["rule_version_id"]) != int(active["id"]),
        "missing_check_codes": [],
        "extra_check_codes": [],
    }

    if not results:
        return {**base, "code": GATE_MISSING, "reason": "最近一次清单运行无结果，不能放行"}

    # 结果必须逐项绑定该运行自己的规则版本；否则视为清单绑定被破坏，不可放行。
    for r in results:
        if r["rule_version_id"] is None or int(r["rule_version_id"]) != int(run["rule_version_id"]):
            return {
                **base,
                "code": GATE_BINDING,
                "reason": "清单结果绑定的规则版本与该次运行不一致，不能放行",
            }

    # 已有未作废的「撒谎」通过留痕：版本争议未清，须先按当前启用项整表复检。
    if release_repo.count_unvoided_lying_passes(conn, pack_id):
        return {**base, "code": GATE_DRIFT, "reason": "该包存在留痕版本与清单绑定版本不一致的历史通过，须作废并按当前启用项整表复检"}

    # 版本漂移优先于「旧运行曾经全过」：历史通过一律作废。
    if base["rule_drift"]:
        return {
            **base,
            "code": GATE_DRIFT,
            "reason": (
                f"清单依据规则 v{run['rule_version_no']}，当前启用规则为 "
                f"v{active['version_no']}；旧通过已作废，须按当前启用项复检"
            ),
        }

    enabled_codes = [it["code"] for it in active.get("items", []) if it.get("enabled")]
    result_codes = [r["check_code"] for r in results]
    missing = sorted(set(enabled_codes) - set(result_codes))
    extra = sorted(set(result_codes) - set(enabled_codes))
    base["missing_check_codes"] = missing
    base["extra_check_codes"] = extra
    if not enabled_codes or missing or extra:
        return {
            **base,
            "code": GATE_COVERAGE,
            "reason": (
                "最近一次清单未覆盖当前启用项"
                + (f"（缺跑: {', '.join(missing)}）" if missing else "")
                + (f"（多余: {', '.join(extra)}）" if extra else "")
                + "，须按当前启用项整表复检"
            ),
        }

    if run.get("status") != "passed" or not all(int(r["passed"]) == 1 for r in results):
        return {**base, "code": GATE_FAILED, "reason": "清单未全部通过，不能放行"}

    return {
        **base,
        "ok": True,
        "code": GATE_OK,
        "reason": f"清单按规则 v{run['rule_version_no']} 全部通过，可放行",
    }


def preview_release(pack_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")
        basis = _release_basis(conn, pack_id)
        run = basis["run"]
        active = basis["active_version"]
        return {
            "pack_id": pack_id,
            "pack_no": pack["pack_no"],
            "pack_status": pack["status"],
            "can_release": basis["ok"],
            "gate_code": basis["code"],
            "gate_reason": basis["reason"],
            "active_rule_version_id": active["id"] if active else None,
            "active_rule_version_no": active["version_no"] if active else None,
            "checklist_run_id": run["id"] if run else None,
            "checklist_rule_version_id": run["rule_version_id"] if run else None,
            "checklist_rule_version_no": run["rule_version_no"] if run else None,
            "rule_drift": basis["rule_drift"],
            "missing_check_codes": basis["missing_check_codes"],
            "extra_check_codes": basis["extra_check_codes"],
            "binding_consistent": bool(
                run and active and int(run["rule_version_id"]) == int(active["id"])
            ),
            "policy": _policy_payload(),
        }
    finally:
        conn.close()


def _block_pass(
    conn,
    pack_id: int,
    status_code: int,
    gate_code: str,
    message: str,
) -> None:
    """拒绝放行：与判定同一事务内把包落到「须复检」，绝不写通过留痕。

    唯一贯彻的收口是作废旧通过态/旧通过留痕并强制复检，不另设永久争议态。
    """
    conn.execute(
        "UPDATE evidence_packs SET status=? WHERE id=?", (STATUS_RECHECK, pack_id)
    )
    conn.commit()
    raise HTTPException(
        status_code=status_code,
        detail={
            "gate_code": gate_code,
            "message": message,
            "pack_status": STATUS_RECHECK,
            "policy": _policy_payload(),
        },
    )


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
            # 历史混版通过：作废之，并按当前启用项整表复检后才可再申请。
            if release_repo.void_lying_passes(conn, pack_id):
                _block_pass(
                    conn,
                    pack_id,
                    409,
                    GATE_DRIFT,
                    "该包存在留痕版本与清单绑定版本不一致的历史通过，已作废；须按当前启用项整表复检",
                )

            # 在飞冲突：发起放行时依据的版本必须仍是当前启用版本。
            if expected_rule_version_id is not None and (
                active is None or int(expected_rule_version_id) != int(active["id"])
            ):
                _block_pass(
                    conn,
                    pack_id,
                    409,
                    GATE_DRIFT,
                    (
                        "发起放行时依据的规则版本已不是当前启用版本；"
                        "拒绝该次放行，须按当前启用项复检"
                    ),
                )

            if not basis["ok"]:
                status_code = (
                    409
                    if basis["code"] in (GATE_DRIFT, GATE_BINDING, GATE_COVERAGE)
                    else 400
                )
                _block_pass(conn, pack_id, status_code, basis["code"], basis["reason"])

            # 此前已被判须复检且本次没有新的合格复检 → 不得写出通过。
            if pack["status"] in BLOCKING_PACK_STATUSES:
                _block_pass(
                    conn,
                    pack_id,
                    409,
                    GATE_DRIFT,
                    "该包旧通过已作废，须按当前启用项整表复检全过后才可再申请",
                )
            run = basis["run"]
            # 留痕版本 = 所绑清单运行版本；basis 已保证它与当前启用版本一致。
            rule_id = int(run["rule_version_id"])
            rule_no = int(run["rule_version_no"])
            if active is None or rule_id != int(active["id"]) or rule_no != int(active["version_no"]):
                _block_pass(
                    conn,
                    pack_id,
                    409,
                    GATE_BINDING,
                    "留痕版本与清单绑定版本不一致，拒绝放行",
                )

            if not reason:
                reason = f"清单按规则 v{rule_no} 全部通过，予以放行"
            rid = release_repo.create_decision(
                conn,
                pack_id=pack_id,
                decision="pass",
                reason=reason,
                rule_version_id=rule_id,
                rule_version_no=rule_no,
                checklist_run_id=run["id"],
                created_at=now,
            )
            pack_repo.update_status(conn, pack_id, "released")
            status = "released"
        else:
            if not reason or not str(reason).strip():
                conn.rollback()
                raise HTTPException(status_code=400, detail="驳回须填写原因")
            # 驳回留痕写当前启用版本；只有运行与其版本一致时才绑定运行，
            # 避免留下「留痕版本≠清单绑定版本」的混版留痕。
            bind_run = basis["run"]
            if bind_run is not None and (
                active is None or int(bind_run["rule_version_id"]) != int(active["id"])
            ):
                bind_run = None
            rid = release_repo.create_decision(
                conn,
                pack_id=pack_id,
                decision="reject",
                reason=reason,
                rule_version_id=active["id"] if active else None,
                rule_version_no=active["version_no"] if active else None,
                checklist_run_id=bind_run["id"] if bind_run else None,
                created_at=now,
            )
            pack_repo.update_status(conn, pack_id, "rejected")
            status = "rejected"
            run = bind_run

        conn.commit()

        return {
            "id": rid,
            "pack_id": pack_id,
            "pack_no": pack["pack_no"],
            "decision": decision,
            "reason": reason,
            "status": status,
            "rule_version_id": run["rule_version_id"] if run else None,
            "rule_version_no": run["rule_version_no"] if run else None,
            "checklist_run_id": run["id"] if run else None,
            "checklist_rule_version_id": run["rule_version_id"] if run else None,
            "checklist_rule_version_no": run["rule_version_no"] if run else None,
            "checklist_all_passed": decision == "pass" and basis["ok"],
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
