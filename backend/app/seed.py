from __future__ import annotations

import json
from datetime import datetime, timezone

from app.db import connect
from app.engines.checklist import default_items

# 举证对象种子
SUBJECT_SEEDS = [
    {
        "code": "store_dayend",
        "title": "门店日结抽检",
        "domain": "store",
        "fixture": "store_dayend.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
    {
        "code": "channel_settle",
        "title": "渠道结算抽检",
        "domain": "channel",
        "fixture": "channel_settle.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
    {
        "code": "price_change",
        "title": "价目变更核证",
        "domain": "price",
        "fixture": "price_change.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
]

# 已废弃的设置项：放行门槛不再可开关，恒以当前启用项全过为准。
OBSOLETE_SETTINGS = ("require_checklist_pass",)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """兼容旧库：缺列则补列。"""
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate(conn) -> None:
    # 规则版本：旧底座为“空挂”表，补齐版本号与启用标记。
    _ensure_column(conn, "rule_versions", "version_no", "INTEGER")
    _ensure_column(conn, "rule_versions", "payload_json", "TEXT")
    _ensure_column(conn, "rule_versions", "active", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "rule_versions", "created_at", "TEXT")
    _ensure_column(conn, "rule_versions", "title", "TEXT")
    conn.execute("UPDATE rule_versions SET version_no = id WHERE version_no IS NULL")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_rule_versions_no ON rule_versions(version_no)"
    )

    # 清单结果：绑定到一次运行与一个规则版本。
    _ensure_column(conn, "checklist_results", "run_id", "INTEGER")
    _ensure_column(conn, "checklist_results", "rule_version_id", "INTEGER")

    # 放行留痕：记录依据的规则版本与清单运行。
    _ensure_column(conn, "release_decisions", "rule_version_id", "INTEGER")
    _ensure_column(conn, "release_decisions", "rule_version_no", "INTEGER")
    _ensure_column(conn, "release_decisions", "checklist_run_id", "INTEGER")
    # 作废标记：旧通过留痕在规则变更/版本撒谎清理时被作废，不再作为放行依据。
    _ensure_column(conn, "release_decisions", "voided", "INTEGER NOT NULL DEFAULT 0")

    # 一次性清理历史「混版通过」：留痕规则版本 != 所绑清单运行版本（或未绑运行）
    # 的通过留痕，一律作废，并把这些包降为待复检，避免它们继续冒充可放行。
    conn.execute(
        """
        UPDATE release_decisions
        SET voided=1
        WHERE decision='pass'
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
        """
    )
    conn.execute(
        """
        UPDATE evidence_packs
        SET status='recheck_required'
        WHERE status IN ('checked_ok', 'released')
          AND EXISTS (
                SELECT 1 FROM release_decisions d
                WHERE d.pack_id = evidence_packs.id
                  AND d.decision='pass'
                  AND d.voided=1
          )
        """
    )

    for key in OBSOLETE_SETTINGS:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(
            """
            -- 举证对象
            CREATE TABLE IF NOT EXISTS subjects(
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                domain TEXT NOT NULL,
                fixture TEXT NOT NULL,
                sample_rate_default REAL NOT NULL DEFAULT 0.3,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            -- 采样批次
            CREATE TABLE IF NOT EXISTS sample_batches(
                id INTEGER PRIMARY KEY,
                batch_no TEXT NOT NULL UNIQUE,
                subject_id INTEGER NOT NULL,
                seed TEXT NOT NULL,
                rate REAL NOT NULL,
                source_count INTEGER NOT NULL,
                sample_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );
            -- 样本行
            CREATE TABLE IF NOT EXISTS sample_rows(
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL,
                business_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                event_ts TEXT,
                revision INTEGER,
                FOREIGN KEY(batch_id) REFERENCES sample_batches(id)
            );
            -- 证据包
            CREATE TABLE IF NOT EXISTS evidence_packs(
                id INTEGER PRIMARY KEY,
                pack_no TEXT NOT NULL UNIQUE,
                batch_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                item_count INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES sample_batches(id),
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );
            -- 证据包条目
            CREATE TABLE IF NOT EXISTS pack_items(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                business_key TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                item_fingerprint TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 规则版本：payload_json 保存检查项配置
            CREATE TABLE IF NOT EXISTS rule_versions(
                id INTEGER PRIMARY KEY,
                version_no INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            -- 校验清单运行：一次运行绑定一个规则版本
            CREATE TABLE IF NOT EXISTS checklist_runs(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                rule_version_id INTEGER NOT NULL,
                rule_version_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                checked_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id),
                FOREIGN KEY(rule_version_id) REFERENCES rule_versions(id)
            );
            -- 校验清单结果
            CREATE TABLE IF NOT EXISTS checklist_results(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                run_id INTEGER,
                rule_version_id INTEGER,
                check_code TEXT NOT NULL,
                check_title TEXT NOT NULL,
                passed INTEGER NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id),
                FOREIGN KEY(run_id) REFERENCES checklist_runs(id),
                FOREIGN KEY(rule_version_id) REFERENCES rule_versions(id)
            );
            -- 放行决策（留痕含规则版本）
            CREATE TABLE IF NOT EXISTS release_decisions(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                rule_version_id INTEGER,
                rule_version_no INTEGER,
                checklist_run_id INTEGER,
                voided INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id),
                FOREIGN KEY(rule_version_id) REFERENCES rule_versions(id)
            );
            -- 设置
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            -- 影子试跑（后题用）
            CREATE TABLE IF NOT EXISTS shadow_runs(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER,
                rule_version_id INTEGER,
                status TEXT,
                note TEXT,
                created_at TEXT
            );
            """
        )
        _migrate(conn)
        if conn.execute("SELECT COUNT(*) c FROM subjects").fetchone()["c"] == 0:
            now = _now()
            for s in SUBJECT_SEEDS:
                conn.execute(
                    """
                    INSERT INTO subjects(
                        code, title, domain, fixture, sample_rate_default, enabled, created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        s["code"],
                        s["title"],
                        s["domain"],
                        s["fixture"],
                        s["sample_rate_default"],
                        s["enabled"],
                        now,
                    ),
                )
        if conn.execute("SELECT COUNT(*) c FROM settings").fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("default_sample_rate", "0.3"),
            )
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("sample_mode", "seeded"),
            )

        # 至少存在一个可用的启用规则版本：内置三项检查。
        active = conn.execute(
            "SELECT id, payload_json FROM rule_versions WHERE active=1 LIMIT 1"
        ).fetchone()

        def _has_enabled(row) -> bool:
            try:
                items = json.loads(row["payload_json"] or "{}").get("items", [])
            except (TypeError, json.JSONDecodeError):
                return False
            return any(it.get("enabled") for it in items if isinstance(it, dict))

        if active is None or not _has_enabled(active):
            next_no = (
                conn.execute(
                    "SELECT COALESCE(MAX(version_no), 0) + 1 AS n FROM rule_versions"
                ).fetchone()["n"]
            )
            conn.execute("UPDATE rule_versions SET active=0 WHERE active=1")
            conn.execute(
                """
                INSERT INTO rule_versions(
                    version_no, title, payload_json, active, created_at
                ) VALUES (?,?,?,?,?)
                """,
                (
                    next_no,
                    f"内置基础清单 v{next_no}",
                    json.dumps({"items": default_items()}, ensure_ascii=False),
                    1,
                    _now(),
                ),
            )
        conn.commit()
    finally:
        conn.close()
