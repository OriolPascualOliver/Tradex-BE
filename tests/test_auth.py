import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

import pytest
from fastapi import HTTPException

from app import auth, database
from app.dependencies import get_current_user


def test_password_hash_and_verify():
    password = "secret"
    hashed = auth.get_password_hash(password)
    assert auth.verify_password(password, hashed)


def test_create_and_decode_access_token():
    token = auth.create_access_token({"sub": "user@example.com"})
    decoded = auth.decode_access_token(token)
    assert decoded["sub"] == "user@example.com"


def test_get_current_user_with_valid_token():
    database.create_tables()
    token = auth.create_access_token({"sub": "demo@fixhub.es"})
    user = get_current_user(token)
    assert user == "demo@fixhub.es"


def test_get_current_user_with_invalid_token():
    with pytest.raises(HTTPException):
        get_current_user("invalid.token")

def test_list_users_endpoint(monkeypatch, tmp_path):
    """The troubleshooting endpoint should list all users."""
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.db')
    database.create_tables()
    monkeypatch.setenv("ENABLE_INVOICE", "0")
    monkeypatch.setenv("ENABLE_QUOTE", "0")
    from app.main import list_users

    users = list_users()
    usernames = {u['username'] for u in users}
    assert 'demo@fixhub.es' in usernames

