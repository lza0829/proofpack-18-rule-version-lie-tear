from __future__ import annotations

import json
import sqlite3
from typing import Any


def _to_version(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    try:
        payload = json.loads(data.get("payload_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    data["items"] = payload.get("items", [])
    return data


def get_active(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, version_no, title, payload_json, active, created_at
        FROM rule_versions
        WHERE active=1
        ORDER BY version_no DESC
        LIMIT 1
        """
    ).fetchone()
    return _to_version(row) if row else None


def get_version(conn: sqlite3.Connection, version_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, version_no, title, payload_json, active, created_at
        FROM rule_versions WHERE id=?
        """,
        (version_id,),
    ).fetchone()
    return _to_version(row) if row else None


def list_versions(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, version_no, title, payload_json, active, created_at
        FROM rule_versions
        ORDER BY version_no DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_to_version(r) for r in rows]


def next_version_no(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version_no), 0) + 1 AS n FROM rule_versions").fetchone()
    return int(row["n"])


def deactivate_all(conn: sqlite3.Connection) -> None:
    # Clear active flags without taking an exclusive lock beyond the row update
    # itself; callers that split deactivate / create across connections rely on
    # this being a single statement that can commit independently.
    conn.execute("UPDATE rule_versions SET active=0 WHERE active=1")


def create_version(
    conn: sqlite3.Connection,
    *,
    version_no: int,
    title: str,
    items: list[dict[str, Any]],
    active: bool,
    created_at: str,
) -> int:
    payload = json.dumps({"items": items}, ensure_ascii=False)
    # Insert first; callers that already cleared active=1 elsewhere may leave a
    # window with zero active rows until this statement commits.
    cur = conn.execute(
        """
        INSERT INTO rule_versions(version_no, title, payload_json, active, created_at)
        VALUES (?,?,?,?,?)
        """,
        (version_no, title, payload, 1 if active else 0, created_at),
    )
    return int(cur.lastrowid)


def activate_version(conn: sqlite3.Connection, version_id: int) -> None:
    conn.execute("UPDATE rule_versions SET active=0 WHERE active=1")
    conn.execute("UPDATE rule_versions SET active=1 WHERE id=?", (version_id,))
