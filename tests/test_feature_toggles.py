import importlib
import importlib.util
import sys
import pathlib
import pytest

# Ensure package import
sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))


def reload_app(monkeypatch, **envs):
    for key, value in envs.items():
        monkeypatch.setenv(key, value)
    # Provide required secret for auth module even if unused
    monkeypatch.setenv("SECRET_KEY", "testing")
    import app.main
    importlib.reload(app.main)
    return app.main.app


def test_quote_router_disabled(monkeypatch):
    app = reload_app(monkeypatch, ENABLE_QUOTE="0", ENABLE_USER_AUTH="0", ENABLE_INVOICE="0")
    paths = [route.path for route in app.routes]
    assert "/api/status" not in paths


@pytest.mark.skipif(importlib.util.find_spec("sqlalchemy") is None, reason="sqlalchemy not installed")
def test_invoice_router_toggle(monkeypatch):
    app_disabled = reload_app(monkeypatch, ENABLE_INVOICE="0", ENABLE_QUOTE="0", ENABLE_USER_AUTH="0")
    paths_disabled = [route.path for route in app_disabled.routes]
    assert not any(p.startswith("/facturas") for p in paths_disabled)

    app_enabled = reload_app(monkeypatch, ENABLE_INVOICE="1", ENABLE_QUOTE="0", ENABLE_USER_AUTH="0")
    paths_enabled = [route.path for route in app_enabled.routes]
    assert any(p.startswith("/facturas") for p in paths_enabled)

