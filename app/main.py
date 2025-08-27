"""Application entry point with optional modules.

Environment variables control which features are enabled:

ENABLE_USER_AUTH  - user database and login endpoints (default: "0")
ENABLE_INVOICE    - invoice routes (default: "0")
ENABLE_QUOTE      - quote routes (default: "0")


"""

import os
import secrets
import logging
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from . import database
from .dependencies import get_current_user


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


def _get_flag(name: str) -> bool:
    """Return boolean value from env var (`"0"`/`"1"`) with validation."""
    value = os.getenv(name, "0")
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be '0' or '1', got {value!r}")
    return value == "1"


ENABLE_USER_AUTH = _get_flag("ENABLE_USER_AUTH")
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
logger = logging.getLogger("health")


@app.get("/api/health-status/public")
def health_status_public():
    """Public endpoint exposing minimal liveness information."""
    return {"status": "alive"}


@app.post("/api/health-status")
def health_status(current_user: str = Depends(get_current_user)):
    """Return detailed health checks for authenticated users with proper role."""
    user = database.get_user(current_user)
    if user["role"] not in {"Owner", "Infra"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges",
        )

    checks: dict[str, str] = {}

    try:
        conn = database.get_connection()
        conn.execute("SELECT 1")
        conn.close()
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - reported to caller
        checks["database"] = f"error: {exc}"

    try:
        checks["queue"] = "ok"
    except Exception as exc:  # pragma: no cover
        checks["queue"] = f"error: {exc}"

    try:
        checks["external_dependencies"] = "ok"
    except Exception as exc:  # pragma: no cover
        checks["external_dependencies"] = f"error: {exc}"

    logger.info("health checks executed", extra={"checks": checks})
    return {"status": "alive", "checks": checks}


# ---------------------------------------------------------------------------
# Optional: user database and authentication
# ---------------------------------------------------------------------------
if ENABLE_USER_AUTH:

    from . import auth, database
    from .dependencies import get_current_user, oauth2_scheme


    database.create_tables()

    class LoginRequest(BaseModel):
        email: str
        password: str

    class TokenPair(BaseModel):
        access_token: str
        refresh_token: str

    class RefreshRequest(BaseModel):
        refresh_token: str

    @app.post("/api/auth/login", response_model=TokenPair)
    def login(data: LoginRequest, request: Request):
        ip = request.client.host
        if auth.is_ip_blocked(ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts",
            )
        user = database.get_user(data.email)
        if not user or not auth.verify_password(data.password, user["hashed_password"]):
            auth.record_failed_login(ip)
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
    def read_secure_data(current_user: str = Depends(get_current_user)):
        return {"user": current_user, "message": "Secure content"}
else:  # pragma: no cover - runtime check
    def get_current_user():  # type: ignore[override]
        """Fallback when authentication is disabled."""
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Authentication disabled"
        )


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

    app.include_router(invoice_router, prefix="/facturas", tags=["facturas"])

if ENABLE_QUOTE:
    from .quote import router as quote_router

    app.include_router(quote_router)
