from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.engines.checklist import SUPPORTED_CHECKS, default_items
from app.policy import STATUS_RECHECK
from app.repositories import packs as pack_repo
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

    停用旧版本与写入新版本必须在**同一事务**内完成：任何读方都不会看到
    「零个启用版本」或半发布的中间态，避免变更与在飞放行共同留下混版通过。
    """
    items = normalize_items(raw_items)
    clean_title = str(title or "").strip()

    conn = connect(immediate=True)
    try:
        version_no = rule_repo.next_version_no(conn)
        rule_repo.deactivate_all(conn)
        if not clean_title:
            enabled = [it["title"] for it in items if it["enabled"]]
            clean_title = f"规则 v{version_no}（启用: {'、'.join(enabled)}）"
        version_id = rule_repo.create_version(
            conn,
            version_no=version_no,
            title=clean_title,
            items=items,
            active=True,
            created_at=_now(),
        )
        # 同一事务内作废旧「清单通过」布尔（策略：invalidate_old_passes），
        # 使旧绿运行不能凭状态字段继续被当作可放行。已放行/已驳回的历史留痕不动；
        # 闸门另以「运行绑定版本 = 当前启用版本」拒绝任何凭旧运行的再放行。
        pack_repo.demote_checked_passes(conn, STATUS_RECHECK)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_version(version_id)
