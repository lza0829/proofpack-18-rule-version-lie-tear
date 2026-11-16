from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.engines.checklist import run_check
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import rules as rule_repo
from app.repositories import samples as sample_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _enabled_items(version: dict[str, Any]) -> list[dict[str, Any]]:
    return [it for it in version.get("items", []) if it.get("enabled")]


def run_checklist(pack_id: int) -> dict[str, Any]:
    """按当前启用的规则版本跑清单；结果整批绑定该版本，历史运行只增不改。"""
    conn = connect(immediate=True)
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")

        version = rule_repo.get_active(conn)
        if not version:
            raise HTTPException(status_code=409, detail="无启用的规则版本，不能跑清单")
        enabled = _enabled_items(version)
        if not enabled:
            # 规则层已保证至少一项启用；此处防御，避免“零项全过”的放行漏洞。
            raise HTTPException(status_code=409, detail="当前规则版本无启用的校验项")

        items = pack_repo.list_items(conn, pack_id)
        sample_rows = sample_repo.list_rows(conn, pack["batch_id"])
        now = _now()

        results: list[dict[str, Any]] = []
        for item in enabled:
            try:
                passed, detail = run_check(
                    item["code"],
                    items=items,
                    sample_rows=sample_rows,
                    pack=pack,
                    params=item.get("params") or {},
                )
            except ValueError as exc:
                # 规则里出现不可执行的检查项：视为未通过，绝不跳过。
                passed, detail = False, str(exc)
            results.append(
                {
                    "check_code": item["code"],
                    "check_title": item.get("title") or item["code"],
                    "passed": passed,
                    "detail": detail,
                }
            )

        all_ok = bool(results) and all(r["passed"] for r in results)
        run_id = check_repo.create_run(
            conn,
            pack_id=pack_id,
            rule_version_id=version["id"],
            rule_version_no=version["version_no"],
            status="passed" if all_ok else "failed",
            checked_count=len(results),
            created_at=now,
        )
        for r in results:
            check_repo.insert_result(
                conn,
                pack_id=pack_id,
                run_id=run_id,
                rule_version_id=version["id"],
                check_code=r["check_code"],
                check_title=r["check_title"],
                passed=r["passed"],
                detail=r["detail"],
                created_at=now,
            )

        pack_repo.update_status(conn, pack_id, "checked_ok" if all_ok else "checked_fail")
        stored = check_repo.results_for_run(conn, run_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "pack_id": pack_id,
        "run_id": run_id,
        "rule_version_id": version["id"],
        "rule_version_no": version["version_no"],
        "all_passed": all_ok,
        "item_count": len(items),
        "results": stored,
    }


def list_results(
    pack_id: int | None = None,
    run_id: int | None = None,
    all_runs: bool = False,
) -> dict[str, Any]:
    """默认返回该包最近一次运行的清单结果；``all_runs`` 时返回全部历史。"""
    conn = connect()
    try:
        target_run = None
        if run_id is not None:
            target_run = check_repo.get_run(conn, run_id)
            if not target_run:
                raise HTTPException(status_code=404, detail="清单运行不存在")
        elif pack_id is not None and not all_runs:
            target_run = check_repo.latest_run(conn, pack_id)

        if target_run is not None:
            rows = check_repo.results_for_run(conn, target_run["id"])
        else:
            rows = check_repo.list_results(conn, pack_id=pack_id)
        return {"run": target_run, "results": rows}
    finally:
        conn.close()


def list_runs(pack_id: int, limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT id, pack_id, rule_version_id, rule_version_no, status,
                   checked_count, created_at
            FROM checklist_runs
            WHERE pack_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (pack_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
