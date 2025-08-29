import pytest
from fastapi import HTTPException
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app import auth, database, main
from app.main import LoginRequest


def setup_module(module):
    if database.DB_PATH.exists():
        database.DB_PATH.unlink()
    database.create_tables()
    conn = database.get_connection()
    cur = conn.cursor()
    password_hash = auth.get_password_hash("secret")
    cur.execute(
        "INSERT INTO users (org_id, username, hashed_password) VALUES (?, ?, ?)",
        ("org1", "alice", password_hash),
    )
    conn.commit()
    conn.close()


def test_login_success_and_wrong_org_rejected():
    payload = LoginRequest(
        org_id="org1", username="alice", password="secret", device_id="dev1"
    )
    token_model = main.login(payload)
    assert token_model.access_token

    payload_bad = LoginRequest(
        org_id="org2", username="alice", password="secret", device_id="dev1"
    )
    with pytest.raises(HTTPException) as exc:
        main.login(payload_bad)
    assert exc.value.status_code == 401


def test_secure_data_access_with_correct_org():
    token = auth.create_access_token({"sub": "alice", "org": "org1"})
    ctx = main.get_current_user(token)
    data = main.read_secure_data(ctx)
    assert data["org"] == "org1"
    assert data["user"] == "alice"


def test_cross_org_query_returns_no_user():
    assert database.get_user("org2", "alice") is None


def test_cross_org_access_forbidden():
    token = auth.create_access_token({"sub": "alice", "org": "org2"})
    with pytest.raises(HTTPException) as exc:
        main.get_current_user(token)
    assert exc.value.status_code == 403
