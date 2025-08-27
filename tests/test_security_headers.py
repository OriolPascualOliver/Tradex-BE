import sys, pathlib, json, asyncio, os
from http.cookies import SimpleCookie

sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

os.environ.setdefault("ENABLE_INVOICE", "0")
os.environ.setdefault("ENABLE_QUOTE", "0")
os.environ.setdefault("SECRET_KEY", "testsecret")

from app.main import app, database


async def asgi_request(app, method, path, headers=None, body=b""):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "scheme": "http",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    response = {"body": b"", "headers_list": []}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
            response["headers_list"] = [
                (k.decode(), v.decode()) for k, v in message.get("headers", [])
            ]
        elif message["type"] == "http.response.body":
            response["body"] += message.get("body", b"")

    await app(scope, receive, send)
    response["headers"] = dict(response["headers_list"])
    return response


def _setup_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.create_tables()


def test_security_headers_present():
    resp = asyncio.run(asgi_request(app, "POST", "/api/status"))
    headers = resp["headers"]
    assert headers["X-Content-Type-Options".lower()] == "nosniff"
    assert headers["X-Frame-Options".lower()] == "DENY"
    assert headers["Referrer-Policy".lower()] == "same-origin"
    assert "Strict-Transport-Security".lower() in headers


def test_cors_allows_configured_origin():
    headers = {
        "Origin": "https://fixhub.opotek.es",
        "Access-Control-Request-Method": "POST",
    }
    resp = asyncio.run(asgi_request(app, "OPTIONS", "/api/status", headers=headers))
    assert resp["status"] == 200
    assert resp["headers"].get("access-control-allow-origin") == "https://fixhub.opotek.es"


def test_cors_blocks_unconfigured_origin():
    headers = {
        "Origin": "https://evil.com",
        "Access-Control-Request-Method": "POST",
    }
    resp = asyncio.run(asgi_request(app, "OPTIONS", "/api/status", headers=headers))
    assert resp["status"] == 400


def test_csrf_double_submit(monkeypatch, tmp_path):
    _setup_db(monkeypatch, tmp_path)
    login_body = json.dumps({"email": "demo@fixhub.es", "password": "demo123!"}).encode()
    login = asyncio.run(
        asgi_request(
            app,
            "POST",
            "/api/auth/login",
            headers={"content-type": "application/json"},
            body=login_body,
        )
    )
    cookies = SimpleCookie()
    for name, value in login["headers_list"]:
        if name.lower() == "set-cookie":
            cookies.load(value)
    csrf = cookies["csrf_token"].value
    cookie_header = f"auth_token={cookies['auth_token'].value}; csrf_token={csrf}"

    ok = asyncio.run(
        asgi_request(
            app,
            "POST",
            "/api/status",
            headers={"cookie": cookie_header, "x-csrf-token": csrf},
        )
    )
    assert ok["status"] == 200

    bad = asyncio.run(
        asgi_request(
            app,
            "POST",
            "/api/status",
            headers={"cookie": cookie_header, "x-csrf-token": "bad"},
        )
    )
    assert bad["status"] == 403
