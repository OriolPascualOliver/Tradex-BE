import importlib
import importlib.util
import sys
import pathlib
import pytest
from fastapi import HTTPException

# Ensure package import
sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))


def reload_app(monkeypatch, **envs):
    for key in [
        "ENABLE_USER_AUTH",
        "ENABLE_INVOICE",
        "ENABLE_QUOTE",
        "OPENAI_API_KEY",
        "INTERNAL_USERS",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in envs.items():
        monkeypatch.setenv(key, value)
    # Provide required secret for auth module even if unused
    monkeypatch.setenv("SECRET_KEY", "testing")
    import app.main
    importlib.reload(app.main)
    return app.main.app


def test_quote_router_disabled_by_default(monkeypatch):
    app = reload_app(monkeypatch)
    paths = [route.path for route in app.routes]
    assert "/api/quotes/generate" not in paths
    assert "/api/status" in paths


@pytest.mark.skipif(importlib.util.find_spec("sqlalchemy") is None, reason="sqlalchemy not installed")
def test_invoice_router_toggle(monkeypatch):
    app_disabled = reload_app(monkeypatch)
    paths_disabled = [route.path for route in app_disabled.routes]
    assert not any(p.startswith("/facturas") for p in paths_disabled)

    app_enabled = reload_app(monkeypatch, ENABLE_INVOICE="1")
    paths_enabled = [route.path for route in app_enabled.routes]
    assert any(p.startswith("/facturas") for p in paths_enabled)


def test_invalid_flag_value(monkeypatch):
    with pytest.raises(ValueError):
        reload_app(monkeypatch, ENABLE_QUOTE="maybe")


def test_quote_requires_openai_key(monkeypatch):
    with pytest.raises(RuntimeError):
        reload_app(monkeypatch, ENABLE_QUOTE="1")


def test_internal_endpoint(monkeypatch):
    app = reload_app(
        monkeypatch,
        ENABLE_USER_AUTH="1",
        INTERNAL_USERS="admin@example.com",
    )
    from app.main import list_active_modules

    # Authorized user
    data = list_active_modules(current_user="admin@example.com")
    assert data["user_auth"] and not data["invoice"] and not data["quote"]

    # Unauthorized user
    with pytest.raises(HTTPException):
        list_active_modules(current_user="other@example.com")

    # Hidden from OpenAPI
    openapi = app.openapi()
    assert "/internal/active-modules" not in openapi["paths"]

