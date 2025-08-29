import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent / "users.db"


def get_connection():
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables() -> None:
    """Create required tables if they do not already exist."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            org_id TEXT NOT NULL,
            username TEXT NOT NULL,
            hashed_password TEXT NOT NULL,
            PRIMARY KEY (org_id, username)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id TEXT NOT NULL,
            username TEXT NOT NULL,
            device_id TEXT NOT NULL,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (org_id, username)
                REFERENCES users (org_id, username)
                ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


def get_user(org_id: str, username: str) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM users WHERE org_id = ? AND username = ?",
        (org_id, username),
    )
    user = cur.fetchone()
    conn.close()
    return user


def add_login(org_id: str, username: str, device_id: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO logins (org_id, username, device_id) VALUES (?, ?, ?)",
        (org_id, username, device_id),
    )
    conn.commit()
    conn.close()
