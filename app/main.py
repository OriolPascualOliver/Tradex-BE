"""Application entry point with optional modules."""
"""
Application entry point with optional modules.

Environment variables control which features are enabled:

ENABLE_USER_AUTH  - user database and login endpoints (default: "1")
ENABLE_INVOICE    - invoice routes (default: "0")
ENABLE_QUOTE      - quote routes (default: "0")
"""

import os
import uuid
import secrets
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from .observability import (
    CONTENT_TYPE_LATEST,
    correlation_id_ctx,
    generate_metrics,
    inc_http_403,
    inc_http_429,
    inc_login_failure,
    logger,
)

# The database module is required by the test suite regardless of whether
# authentication features are enabled, so we import it unconditionally and
# expose it via ``app.main``.
from . import database

# ---------------------------------------------------------------------------
# Required secrets
# ---------------------------------------------------------------------------
# List of environment variables that must be present for the application to
# start. Additional secrets can be added here as needed.
REQUIRED_ENV_VARS = ["SECRET_KEY"]


def validate_required_env_vars() -> None:
    """Ensure that all required environment variables are defined.

    The application should fail fast during start-up if any critical
    configuration is missing. This helps surface misconfigurations early in
    the deployment process and avoids running with insecure defaults.
    """

    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        missing_vars = ", ".join(missing)
        raise RuntimeError(
            f"Missing required environment variables: {missing_vars}"
        )


# Validate secrets before continuing with application setup
validate_required_env_vars()


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

def _get_flag(name: str, default: str = "0") -> bool:
    """Return boolean value from env var (`"0"`/`"1"`) with validation.

    Parameters
    ----------
    name:
        Environment variable to read.
    default:
        Value to fall back to when the variable is unset.  The application
        enables user authentication by default during testing, so callers may
        specify ``"1"`` for that flag.
    """
    value = os.getenv(name, default)
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be '0' or '1', got {value!r}")
    return value == "1"


ENABLE_USER_AUTH = _get_flag("ENABLE_USER_AUTH", "1")
ENABLE_INVOICE = _get_flag("ENABLE_INVOICE")
ENABLE_QUOTE = _get_flag("ENABLE_QUOTE")

# Configuration validation
if ENABLE_QUOTE and not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError("OPENAI_API_KEY required when ENABLE_QUOTE=1")
if ENABLE_USER_AUTH and not os.getenv("SECRET_KEY"):
    raise RuntimeError("SECRET_KEY required when ENABLE_USER_AUTH=1")

INTERNAL_USERS = {
    u.strip()
    for u in os.getenv("INTERNAL_USERS", "").split(",")
    if u.strip()
}

app = FastAPI(title="Tradex Backend")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        correlation_id_ctx.set(correlation_id)
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        if response.status_code == status.HTTP_403_FORBIDDEN:
            inc_http_403()
        elif response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            inc_http_429()
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": str(request.url.path),
                "status_code": response.status_code,
            },
        )
        return response


app.add_middleware(ObservabilityMiddleware)

# Restrict CORS to production and staging domains
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fixhub.opotek.es"],
    allow_origin_regex=r"https://.*\.staging\.opotek\.es",
    allow_credentials=True,
    allow_methods=["*"],  # ensures OPTIONS is permitted
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Inject common security headers into every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers[
        "Strict-Transport-Security"
    ] = "max-age=63072000; includeSubDomains; preload"
    return response


@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    """Simple double submit CSRF protection for cookie-based auth."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        auth_cookie = request.cookies.get("auth_token")
        if auth_cookie:
            csrf_cookie = request.cookies.get("csrf_token")
            csrf_header = request.headers.get("X-CSRF-Token")
            if not csrf_cookie or csrf_header != csrf_cookie:
                return Response(status_code=status.HTTP_403_FORBIDDEN)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health-status/public")
def health_status_public():
    """Public endpoint exposing minimal liveness information."""
    return {"status": "alive"}


@app.get("/metrics")
def metrics():
    """Expose Prometheus metrics."""
    return Response(generate_metrics(), media_type=CONTENT_TYPE_LATEST)


@app.api_route("/api/status", methods=["GET", "POST"])
def api_status():
    """Simple status endpoint used for CORS and CSRF tests."""
    return {"status": "ok"}




# ---------------------------------------------------------------------------
# Optional: user database and authentication
# ---------------------------------------------------------------------------
if ENABLE_USER_AUTH:

    from . import auth, audit
    from .dependencies import get_current_user, oauth2_scheme

    database.create_tables()
    app.include_router(audit.router)

    class LoginRequest(BaseModel):
        email: str
        password: str

    class Token(BaseModel):
        token: str

    class TokenPair(BaseModel):
        access_token: str
        refresh_token: str

    class RefreshRequest(BaseModel):
        refresh_token: str

    @app.post("/api/auth/login", response_model=TokenPair)
    def login(data: LoginRequest, request: Request):
        ip = request.client.host if request.client else ""
        if auth.is_ip_blocked(ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts",
            )
        user = database.get_user(data.email)
        if not user or not auth.verify_password(data.password, user["hashed_password"]):
            auth.record_failed_login(ip)
            inc_login_failure()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        auth.reset_failed_logins(ip)
        database.add_login(data.email, "web")
        access_token = auth.create_access_token({"sub": data.email})

        csrf_token = secrets.token_urlsafe(16)
        response = JSONResponse(content=Token(token=access_token).dict())
        response.set_cookie(
            "auth_token",
            access_token,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        response.set_cookie(
            "csrf_token",
            csrf_token,
            secure=True,
            samesite="lax",
        )
        return response

    @app.post("/api/auth/refresh", response_model=TokenPair)
    def refresh_tokens(data: RefreshRequest):
        username = auth.use_refresh_token(data.refresh_token)
        if not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )
        access_token = auth.create_access_token({"sub": username})
        refresh_token = auth.create_refresh_token({"sub": username})
        return TokenPair(access_token=access_token, refresh_token=refresh_token)

    @app.post("/api/auth/logout")
    def logout(token: str = Depends(oauth2_scheme)):
        payload = auth.decode_access_token(token)
        if payload:
            auth.revoke_token(token)
            auth.revoke_refresh_tokens_for_user(payload.get("sub"))
        return {"detail": "Logged out"}

    @app.get("/api/auth/users")
    def list_users():
        """Return all users for troubleshooting purposes."""
        return database.get_all_users()

    @app.get("/secure-data")
    def read_secure_data(request: Request, current_user: str = Depends(get_current_user)):
        database.add_audit_log(
            actor=current_user,
            ip=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", ""),
            action="access",
            obj="/secure-data",
            before=None,
            after=None,
        )
        return {"user": current_user, "message": "Secure content"}
else:  # pragma: no cover - runtime check
    def get_current_user():  # type: ignore[override]
        """Fallback when authentication is disabled."""
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Authentication disabled"
        )


@app.post("/api/health-status")
def health_status(current_user: str = Depends(get_current_user)):
    """Authenticated health check returning diagnostic details.

    Only users with the ``Owner`` role are authorised to access the detailed
    health information.  The check verifies database connectivity and reports
    the overall status.
    """
    user = database.get_user(current_user)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if user["role"].lower() != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    db_status = "ok"
    try:
        conn = database.get_connection()
        conn.execute("SELECT 1")
        conn.close()
    except Exception:
        db_status = "error"
    return {"status": "alive", "checks": {"database": db_status}}


# ---------------------------------------------------------------------------
# Internal debugging endpoint
# ---------------------------------------------------------------------------
@app.get("/internal/active-modules", include_in_schema=False)
def list_active_modules(current_user: str = Depends(get_current_user)):
    if current_user not in INTERNAL_USERS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return {
        "user_auth": ENABLE_USER_AUTH,
        "invoice": ENABLE_INVOICE,
        "quote": ENABLE_QUOTE,
    }


# ---------------------------------------------------------------------------
# Optional: invoice and quote routers
# ---------------------------------------------------------------------------
if ENABLE_INVOICE:
    from .invoice import router as invoice_router

    app.include_router(invoice_router, prefix="/api/invoices", tags=["invoices"])

if ENABLE_QUOTE:
    from .quote import router as quote_router

    app.include_router(quote_router)
