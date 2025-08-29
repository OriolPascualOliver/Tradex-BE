import pytest
import sys
from pathlib import Path
from fastapi import HTTPException

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app import auth, database, main
from app.main import LoginRequest, RefreshRequest


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
    auth._revoked_tokens.clear()


def test_refresh_token_rotation_rejects_reuse():
    login_payload = LoginRequest(org_id="org1", username="alice", password="secret", device_id="dev1")
    tokens = main.login(login_payload)
    refresh_req = RefreshRequest(refresh_token=tokens.refresh_token)
    new_tokens = main.refresh(refresh_req)
    assert new_tokens.access_token and new_tokens.refresh_token
    # Reusing old refresh token should now fail
    with pytest.raises(HTTPException) as exc:
        main.refresh(refresh_req)
    assert exc.value.status_code == 401


def test_logout_revokes_tokens():
    login_payload = LoginRequest(org_id="org1", username="alice", password="secret", device_id="dev1")
    tokens = main.login(login_payload)
    refresh_req = RefreshRequest(refresh_token=tokens.refresh_token)
    main.logout(refresh_req, tokens.access_token)
    # Access token should be revoked
    with pytest.raises(HTTPException) as exc:
        main.get_current_user(tokens.access_token)
    assert exc.value.status_code == 401
    # Refresh token should also be revoked
    with pytest.raises(HTTPException) as exc:
        main.refresh(refresh_req)
    assert exc.value.status_code == 401
