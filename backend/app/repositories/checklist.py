from __future__ import annotations

import sqlite3
from typing import Any


def create_run(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    rule_version_id: int,
    rule_version_no: int,
    status: str,
    checked_count: int,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO checklist_runs(
            pack_id, rule_version_id, rule_version_no, status, checked_count, created_at
        ) VALUES (?,?,?,?,?,?)
        """,
        (pack_id, rule_version_id, rule_version_no, status, checked_count, created_at),
    )
    return int(cur.lastrowid)


def insert_result(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    run_id: int,
    rule_version_id: int,
    check_code: str,
    check_title: str,
    passed: bool,
    detail: str,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO checklist_results(
            pack_id, run_id, rule_version_id, check_code, check_title, passed, detail, created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            pack_id,
            run_id,
            rule_version_id,
            check_code,
            check_title,
            1 if passed else 0,
            detail,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def latest_run(conn: sqlite3.Connection, pack_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, pack_id, rule_version_id, rule_version_no, status, checked_count, created_at
        FROM checklist_runs
        WHERE pack_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (pack_id,),
    ).fetchone()
    return dict(row) if row else None


def get_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, pack_id, rule_version_id, rule_version_no, status, checked_count, created_at
        FROM checklist_runs WHERE id=?
        """,
        (run_id,),
    ).fetchone()
    return dict(row) if row else None


def list_results(
    conn: sqlite3.Connection,
    pack_id: int | None = None,
    run_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    where: list[str] = []
    args: list[Any] = []
    if pack_id is not None:
        where.append("pack_id=?")
        args.append(pack_id)
    if run_id is not None:
        where.append("run_id=?")
        args.append(run_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"""
        SELECT id, pack_id, run_id, rule_version_id, check_code, check_title,
               passed, detail, created_at
        FROM checklist_results
        {clause}
        ORDER BY id
        LIMIT ?
        """,
        (*args, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def results_for_run(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, pack_id, run_id, rule_version_id, check_code, check_title,
               passed, detail, created_at
        FROM checklist_results
        WHERE run_id=?
        ORDER BY id
        """,
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]
