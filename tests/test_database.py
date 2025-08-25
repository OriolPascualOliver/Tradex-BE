import sys, pathlib, sqlite3
sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

import pytest
from app import database


def test_create_tables_seeds_demo_users(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.db')
    database.create_tables()
    user = database.get_user('demo@fixhub.es')
    assert user is not None
    assert user['username'] == 'demo@fixhub.es'


def test_increment_device_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.db')
    database.create_tables()
    database.increment_device_usage('demo@fixhub.es', 'device1')
    database.increment_device_usage('demo@fixhub.es', 'device1')
    usage = database.get_device_usage('demo@fixhub.es', 'device1')
    assert usage['quote_count'] == 2


def test_add_login_records_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.db')
    database.create_tables()
    database.add_login('demo@fixhub.es', 'deviceA')
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM logins WHERE username=? AND device_id=?', ('demo@fixhub.es', 'deviceA'))
    count = cur.fetchone()[0]
    conn.close()
    assert count == 1
