import os
from pathlib import Path
from typing import Optional

use_sqlcipher = os.getenv("TRADEX_USE_SQLCIPHER") == "1"
if use_sqlcipher:
    from pysqlcipher3 import dbapi2 as sqlite3  # type: ignore
else:
    import sqlite3

from .auth import get_password_hash

DB_PATH = Path(
    os.getenv("TRADEX_DB_PATH", str(Path.home() / ".tradex" / "users.db"))
)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _ensure_permissions() -> None:
    for p in [DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal")]:
        if p.exists():
            p.chmod(0o600)


def get_connection():
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    if use_sqlcipher:
        key = os.getenv("TRADEX_DB_KEY", "")
        if key:
            conn.execute(f"PRAGMA key = '{key}'")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    _ensure_permissions()
    return conn


def create_tables() -> None:
    """Create required tables if they do not already exist and seed demo users."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            hashed_password TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            device_id TEXT NOT NULL,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS device_usage (
            username TEXT NOT NULL,
            device_id TEXT NOT NULL,
            quote_count INTEGER DEFAULT 0,
            first_access TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (username, device_id)
        )
        """
    )

    if os.getenv("TRADEX_ENV") != "production":
        demo_users = [
            ("demo@fixhub.es", "demo123!"),
            ("demo2@fixhub.es", "demo456!"),
        ]
        for username, password in demo_users:
            cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO users (username, hashed_password) VALUES (?, ?)",
                    (username, get_password_hash(password)),
                )

    conn.commit()
    conn.close()
    _ensure_permissions()


def get_user(username: str) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cur.fetchone()
    conn.close()
    return user


def get_all_users() -> list[dict]:
    """Return all user records as a list of dictionaries."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_login(username: str, device_id: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO logins (username, device_id) VALUES (?, ?)",
        (username, device_id),
    )
    conn.commit()
    conn.close()
    _ensure_permissions()


def get_device_usage(username: str, device_id: str) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM device_usage WHERE username = ? AND device_id = ?",
        (username, device_id),
    )
    row = cur.fetchone()
    conn.close()
    return row


def increment_device_usage(username: str, device_id: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO device_usage (username, device_id, quote_count)
        VALUES (?, ?, 1)
        ON CONFLICT(username, device_id)
        DO UPDATE SET quote_count = quote_count + 1
        """,
        (username, device_id),
    )
    conn.commit()
    conn.close()
    _ensure_permissions()
