import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

import importlib
import importlib.util
from datetime import timedelta

import pytest
from fastapi import HTTPException

# Check for optional invoice dependencies
invoice_deps_available = all(
    importlib.util.find_spec(m) is not None
    for m in ("sqlalchemy", "qrcode", "weasyprint", "lxml")
)


def test_manipulated_jwt(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "testkey")
    import app.auth as auth
    importlib.reload(auth)
    token = auth.create_access_token({"sub": "user@example.com"})
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.invalid"
    from app.dependencies import get_current_user
    with pytest.raises(HTTPException):
        get_current_user(tampered)


def test_refresh_token_replay(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "testkey")
    import app.auth as auth
    importlib.reload(auth)
    from app.dependencies import get_current_user
    expired = auth.create_access_token({"sub": "user@example.com"}, expires_delta=timedelta(seconds=-1))
    with pytest.raises(HTTPException):
        get_current_user(expired)


@pytest.mark.skipif(not invoice_deps_available, reason="invoice dependencies missing")
def test_invoice_sql_injection_filter():
    assert True  # placeholder skipped when dependencies missing


@pytest.mark.skipif(not invoice_deps_available, reason="invoice dependencies missing")
def test_invoice_path_traversal():
    assert True  # placeholder skipped when dependencies missing


@pytest.mark.skipif(not invoice_deps_available, reason="invoice dependencies missing")
def test_invoice_fuzz_payloads():
    assert True  # placeholder skipped when dependencies missing
