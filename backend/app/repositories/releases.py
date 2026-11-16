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
    # 左连清单运行，取所绑运行自身的规则版本，供「留痕版本 vs 清单绑定版本」互证。
    rows = conn.execute(
        """
        SELECT d.id, d.pack_id, d.decision, d.reason, d.rule_version_id, d.rule_version_no,
               d.checklist_run_id, d.voided, d.created_at,
               r.rule_version_id   AS checklist_rule_version_id,
               r.rule_version_no   AS checklist_rule_version_no
        FROM release_decisions d
        LEFT JOIN checklist_runs r ON r.id = d.checklist_run_id
        ORDER BY d.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def void_lying_passes(conn: sqlite3.Connection, pack_id: int) -> int:
    """作废该包「留痕版本≠所绑清单版本」（或未绑运行）的通过留痕。

    这是策略 ``invalidate_old_passes`` 的收口动作：发现版本撒谎即作废旧通过，
    包落到须复检，绝不在其上叠加新的通过。返回被作废的条数。
    """
    cur = conn.execute(
        """
        UPDATE release_decisions
        SET voided=1
        WHERE pack_id=?
          AND decision='pass'
          AND voided=0
          AND (
                checklist_run_id IS NULL
             OR rule_version_id IS NULL
             OR NOT EXISTS (
                    SELECT 1 FROM checklist_runs r
                    WHERE r.id = release_decisions.checklist_run_id
                      AND r.rule_version_id = release_decisions.rule_version_id
                      AND r.rule_version_no = release_decisions.rule_version_no
                )
          )
        """,
        (pack_id,),
    )
    return int(cur.rowcount)


def count_unvoided_lying_passes(conn: sqlite3.Connection, pack_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) c FROM release_decisions d
        LEFT JOIN checklist_runs r ON r.id = d.checklist_run_id
        WHERE d.pack_id=?
          AND d.decision='pass'
          AND d.voided=0
          AND (
                d.checklist_run_id IS NULL
             OR d.rule_version_id IS NULL
             OR r.id IS NULL
             OR r.rule_version_id IS NULL
             OR d.rule_version_id != r.rule_version_id
             OR d.rule_version_no  != r.rule_version_no
          )
        """,
        (pack_id,),
    ).fetchone()
    return int(row["c"])


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
