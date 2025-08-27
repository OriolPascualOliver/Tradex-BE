import os
import sys
import pathlib
import sqlite3
import stat

os.environ.setdefault("SECRET_KEY", "testing")

sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

import pytest
from app import database
from scripts import db_backup


def _setup(tmp_path, monkeypatch, env="development"):
    monkeypatch.setenv("TRADEX_ENV", env)
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", db_file)
    monkeypatch.setenv("TRADEX_DB_PATH", str(db_file))
    monkeypatch.setattr(db_backup, "DEFAULT_DB_PATH", db_file)
    return db_file


def test_create_tables_seeds_demo_users(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, env="development")
    database.create_tables()
    user = database.get_user("demo@fixhub.es")
    assert user is not None
    assert user["username"] == "demo@fixhub.es"


def test_create_tables_no_demo_in_production(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, env="production")
    database.create_tables()
    assert database.get_user("demo@fixhub.es") is None


def test_get_all_users_returns_seeded_accounts(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, env="development")
    database.create_tables()
    users = database.get_all_users()
    usernames = {u["username"] for u in users}
    assert {"demo@fixhub.es", "demo2@fixhub.es"} <= usernames


def test_increment_device_usage(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, env="development")
    database.create_tables()
    database.increment_device_usage("demo@fixhub.es", "device1")
    database.increment_device_usage("demo@fixhub.es", "device1")
    usage = database.get_device_usage("demo@fixhub.es", "device1")
    assert usage["quote_count"] == 2


def test_add_login_records_entry(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, env="development")
    database.create_tables()
    database.add_login("demo@fixhub.es", "deviceA")
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM logins WHERE username=? AND device_id=?",
        ("demo@fixhub.es", "deviceA"),
    )
    count = cur.fetchone()[0]
    conn.close()
    assert count == 1


def test_db_permissions(tmp_path, monkeypatch):
    db_file = _setup(tmp_path, monkeypatch, env="development")
    database.create_tables()
    hold_conn = database.get_connection()
    database.add_login("demo@fixhub.es", "permcheck")
    wal_path = db_file.with_name("test.db-wal")
    assert wal_path.exists()
    db_mode = stat.S_IMODE(os.stat(db_file).st_mode)
    wal_mode = stat.S_IMODE(os.stat(wal_path).st_mode)
    hold_conn.close()
    assert db_mode == 0o600
    assert wal_mode == 0o600


def test_backup_and_restore(tmp_path, monkeypatch):
    db_file = _setup(tmp_path, monkeypatch, env="development")
    database.create_tables()
    conn = database.get_connection()
    conn.execute(
        "INSERT INTO users (username, hashed_password) VALUES (?, ?)",
        ("alice", "pw"),
    )
    conn.commit()
    conn.close()

    backup_file = tmp_path / "backup.db"
    db_backup.backup_db(str(backup_file))
    os.remove(db_file)
    db_backup.restore_db(str(backup_file), str(db_file))

    user = database.get_user("alice")
    assert user is not None
