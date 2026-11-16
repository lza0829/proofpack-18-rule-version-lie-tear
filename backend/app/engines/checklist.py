"""校验清单引擎：可配置检查项的实现与默认配置。

检查项以 ``code`` 标识实现，规则版本保存 ``items`` 列表。
当前支持的实现固定为条数一致、键唯一、必填非空；规则层允许增删、启停。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# 支持的校验项实现：code -> 默认标题
SUPPORTED_CHECKS: dict[str, str] = {
    "count_match": "条数一致",
    "key_unique": "键唯一",
    "required_fields": "必填非空",
}

DEFAULT_REQUIRED_FIELDS = (
    "business_key",
    "title",
    "amount",
    "qty",
    "event_ts",
    "revision",
)


def default_items() -> list[dict[str, Any]]:
    """内置初始规则：三项全部启用。"""
    return [
        {"code": "count_match", "title": "条数一致", "enabled": True, "params": {}},
        {"code": "key_unique", "title": "键唯一", "enabled": True, "params": {}},
        {
            "code": "required_fields",
            "title": "必填非空",
            "enabled": True,
            "params": {"fields": list(DEFAULT_REQUIRED_FIELDS)},
        },
    ]


def _count_match(
    *,
    items: list[dict[str, Any]],
    sample_rows: list[dict[str, Any]],
    pack: dict[str, Any],
    params: dict[str, Any],
) -> tuple[bool, str]:
    passed = len(items) == len(sample_rows) and len(items) == int(pack["item_count"])
    detail = f"包条目={len(items)}，样本行={len(sample_rows)}，包记录条数={pack['item_count']}"
    return passed, detail


def _key_unique(
    *,
    items: list[dict[str, Any]],
    params: dict[str, Any],
    **_: Any,
) -> tuple[bool, str]:
    keys = [str(it.get("business_key")) for it in items]
    duplicates = sorted(k for k, n in Counter(keys).items() if n > 1)
    if duplicates:
        return False, f"重复键: {', '.join(duplicates)}"
    return True, "全部业务键唯一"


def _required_fields(
    *,
    items: list[dict[str, Any]],
    params: dict[str, Any],
    **_: Any,
) -> tuple[bool, str]:
    fields = params.get("fields") or list(DEFAULT_REQUIRED_FIELDS)
    missing: list[str] = []
    for it in items:
        summary = it.get("summary") if isinstance(it.get("summary"), dict) else {}
        for field in fields:
            val = it.get("business_key") if field == "business_key" else summary.get(field)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                missing.append(f"{it.get('business_key')}.{field}")
    if missing:
        return False, f"缺失: {', '.join(missing[:20])}"
    return True, "必填字段齐全"


_RUNNERS = {
    "count_match": _count_match,
    "key_unique": _key_unique,
    "required_fields": _required_fields,
}


def run_check(
    code: str,
    *,
    items: list[dict[str, Any]],
    sample_rows: list[dict[str, Any]],
    pack: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """执行单个检查项，返回 (是否通过, 说明)。"""
    runner = _RUNNERS.get(code)
    if runner is None:
        raise ValueError(f"不支持的校验项: {code}")
    return runner(items=items, sample_rows=sample_rows, pack=pack, params=params or {})
