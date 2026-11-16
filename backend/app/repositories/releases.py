from __future__ import annotations

import sqlite3
from typing import Any


def create_decision(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    decision: str,
    reason: str | None,
    rule_version_id: int | None,
    rule_version_no: int | None,
    checklist_run_id: int | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO release_decisions(
            pack_id, decision, reason, rule_version_id, rule_version_no,
            checklist_run_id, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            pack_id,
            decision,
            reason,
            rule_version_id,
            rule_version_no,
            checklist_run_id,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def list_decisions(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, pack_id, decision, reason, rule_version_id, rule_version_no,
               checklist_run_id, created_at
        FROM release_decisions
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_for_pack(conn: sqlite3.Connection, pack_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, pack_id, decision, reason, rule_version_id, rule_version_no,
               checklist_run_id, created_at
        FROM release_decisions
        WHERE pack_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (pack_id,),
    ).fetchone()
    return dict(row) if row else None
