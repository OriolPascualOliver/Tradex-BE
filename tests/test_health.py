import os, sys, pathlib, importlib

sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

os.environ.setdefault("SECRET_KEY", "testing")

from fastapi.testclient import TestClient
from app import auth, database

import pytest

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.create_tables()
    import app.main
    importlib.reload(app.main)
    return TestClient(app.main.app)


def auth_header(username: str) -> dict:
    token = auth.create_access_token({"sub": username})
    return {"Authorization": f"Bearer {token}"}


def test_public_health(client):
    res = client.get("/api/health-status/public")
    assert res.status_code == 200
    assert res.json() == {"status": "alive"}


def test_health_requires_auth(client):
    res = client.post("/api/health-status")
    assert res.status_code == 401


def test_health_with_owner_role(client):
    res = client.post("/api/health-status", headers=auth_header("demo@fixhub.es"))
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "alive"
    assert body["checks"]["database"] == "ok"


def test_health_forbidden_role(client):
    res = client.post("/api/health-status", headers=auth_header("demo2@fixhub.es"))
    assert res.status_code == 403
