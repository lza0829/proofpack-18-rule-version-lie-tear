from __future__ import annotations

import sqlite3

from app.config import DATA_DIR, DB_FILENAME

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / DB_FILENAME


def connect(immediate: bool = False, timeout: float = 10.0) -> sqlite3.Connection:
    """打开连接；``immediate`` 用于写路径，立即取写锁以串行化并发写入。"""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if immediate:
        conn.execute("BEGIN IMMEDIATE")
    return conn
