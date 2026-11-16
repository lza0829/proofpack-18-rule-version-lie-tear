from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.engines.checklist import SUPPORTED_CHECKS, default_items
from app.repositories import rules as rule_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_item(raw: Any, seen: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="检查项须为对象")
    code = str(raw.get("code") or "").strip()
    if code not in SUPPORTED_CHECKS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的校验项: {code}（可用: {', '.join(SUPPORTED_CHECKS)}）",
        )
    if code in seen:
        raise HTTPException(status_code=400, detail=f"校验项重复: {code}")
    seen.add(code)

    title = str(raw.get("title") or SUPPORTED_CHECKS[code]).strip() or SUPPORTED_CHECKS[code]
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail=f"校验项 {code} 的 enabled 须为布尔值")

    params: dict[str, Any] = {}
    if code == "required_fields":
        fields = raw.get("params", {}).get("fields") if isinstance(raw.get("params"), dict) else None
        if fields is None:
            fields = list(default_items()[2]["params"]["fields"])
        if not isinstance(fields, list) or not fields:
            raise HTTPException(status_code=400, detail="必填非空须给出至少一个字段")
        clean: list[str] = []
        for f in fields:
            name = str(f).strip()
            if not name:
                raise HTTPException(status_code=400, detail="必填字段名不得为空")
            if name not in clean:
                clean.append(name)
        params["fields"] = clean

    return {"code": code, "title": title, "enabled": enabled, "params": params}


def normalize_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(status_code=400, detail="检查项清单不得为空")
    seen: set[str] = set()
    items = [_normalize_item(raw, seen) for raw in raw_items]
    if not any(it["enabled"] for it in items):
        raise HTTPException(status_code=400, detail="至少须启用一个校验项")
    return items


def supported_checks() -> list[dict[str, str]]:
    return [{"code": code, "title": title} for code, title in SUPPORTED_CHECKS.items()]


def get_current() -> dict[str, Any]:
    conn = connect()
    try:
        version = rule_repo.get_active(conn)
        if not version:
            raise HTTPException(status_code=409, detail="无启用的规则版本")
        return version
    finally:
        conn.close()


def list_versions(limit: int = 50) -> dict[str, Any]:
    conn = connect()
    try:
        versions = rule_repo.list_versions(conn, limit=limit)
        active = next((v for v in versions if int(v["active"]) == 1), None)
        return {
            "active": active,
            "versions": versions,
            "supported_checks": supported_checks(),
        }
    finally:
        conn.close()


def get_version(version_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        version = rule_repo.get_version(conn, version_id)
        if not version:
            raise HTTPException(status_code=404, detail="规则版本不存在")
        return version
    finally:
        conn.close()


def create_version(title: str, raw_items: Any) -> dict[str, Any]:
    """新增规则版本并启用；旧版本保留供历史留痕追溯。

    Deactivate and create run as separate short transactions so a concurrent
    reader can still resolve an active version between the two steps, and so a
    failure creating the new row does not automatically revive the previous
    active flag inside the same lock.
    """
    items = normalize_items(raw_items)
    clean_title = str(title or "").strip()

    # Step 1: clear the active flag on its own connection / commit.
    conn_clear = connect(immediate=True)
    try:
        version_no = rule_repo.next_version_no(conn_clear)
        rule_repo.deactivate_all(conn_clear)
        conn_clear.commit()
    except Exception:
        conn_clear.rollback()
        raise
    finally:
        conn_clear.close()

    if not clean_title:
        enabled = [it["title"] for it in items if it["enabled"]]
        clean_title = f"规则 v{version_no}（启用: {'、'.join(enabled)}）"

    # Step 2: insert the new active version on a fresh connection. If this
    # fails after step 1 committed, the subject may temporarily have no active
    # rules until the caller retries.
    conn = connect(immediate=True)
    try:
        version_id = rule_repo.create_version(
            conn,
            version_no=version_no,
            title=clean_title,
            items=items,
            active=True,
            created_at=_now(),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_version(version_id)
