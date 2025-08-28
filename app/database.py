
import sqlite3
import json
import copy
import os
from datetime import datetime

from pathlib import Path
from typing import Optional, Any, Dict, List

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

# Retention policy for audit logs (days)
RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "30"))


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
            hashed_password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'User'
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            actor TEXT,
            ip TEXT,
            user_agent TEXT,
            action TEXT,
            object TEXT,
            before TEXT,
            after TEXT
        )
        """
    )


    if os.getenv("TRADEX_ENV") != "production":
        demo_users = [
            ("demo@fixhub.es", "demo123!", "Owner"),
            ("demo2@fixhub.es", "demo456!", "User"),
        ]
        for username, password, role in demo_users:
            cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
                    (username, get_password_hash(password), role),
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



SENSITIVE_KEYS = {
    "password",
    "hashed_password",
    "email",
    "nif",
    "receptor_nif",
    "emisor_nif",
    "username",
    "phone",
}


def redact_pii(data: Optional[Any]) -> Optional[Any]:
    """Return a deep copy of *data* with sensitive fields redacted."""
    if data is None:
        return None

    def _redact(obj: Any) -> Any:
        if isinstance(obj, dict):
            result: Dict[str, Any] = {}
            for k, v in obj.items():
                if k.lower() in SENSITIVE_KEYS:
                    result[k] = "[REDACTED]"
                else:
                    result[k] = _redact(v)
            return result
        if isinstance(obj, list):
            return [_redact(v) for v in obj]
        return obj

    return _redact(copy.deepcopy(data))


def add_audit_log(
    actor: str,
    ip: str,
    user_agent: str,
    action: str,
    obj: str,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
) -> None:
    """Insert a record into the audit log and enforce retention."""
    before_json = json.dumps(redact_pii(before), ensure_ascii=False) if before is not None else None
    after_json = json.dumps(redact_pii(after), ensure_ascii=False) if after is not None else None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO audit_log (actor, ip, user_agent, action, object, before, after)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (actor, ip, user_agent, action, obj, before_json, after_json),
    )
    cur.execute(
        "DELETE FROM audit_log WHERE timestamp < datetime('now', ?)",
        (f'-{RETENTION_DAYS} days',),
    )
    conn.commit()
    conn.close()


def query_audit_logs(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    user: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return audit log entries filtered by optional range and user."""
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM audit_log WHERE 1=1"
    params: List[Any] = []
    if user:
        query += " AND actor = ?"
        params.append(user)
    if start:
        query += " AND timestamp >= ?"
        params.append(start.isoformat(sep=" "))
    if end:
        query += " AND timestamp <= ?"
        params.append(end.isoformat(sep=" "))
    cur.execute(query + " ORDER BY id", params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def export_audit_logs_csv(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    user: Optional[str] = None,
) -> str:
    """Return audit logs as a CSV string with basic CSV injection protection."""
    import csv
    import io

    logs = query_audit_logs(start, end, user)
    output = io.StringIO()
    fieldnames = [
        "id",
        "timestamp",
        "actor",
        "ip",
        "user_agent",
        "action",
        "object",
        "before",
        "after",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    def sanitize(value: Optional[str]) -> str:
        if value is None:
            return ""
        if value and value[0] in ("=", "+", "-", "@"):  # CSV injection guard
            return "'" + value
        return value

    for row in logs:
        writer.writerow({k: sanitize(str(row.get(k, ""))) for k in fieldnames})

    return output.getvalue()


    _ensure_permissions()

