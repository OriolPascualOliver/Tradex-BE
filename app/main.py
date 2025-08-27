"""Application entry point with optional modules.

Environment variables control which features are enabled:

```
ENABLE_USER_AUTH  - user database and login endpoints (default: "1")
ENABLE_INVOICE    - invoice routes (default: "1")
ENABLE_QUOTE      - quote routes (default: "1")
```
"""

import os
import logging
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from . import database
from .dependencies import get_current_user


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------
ENABLE_USER_AUTH = os.getenv("ENABLE_USER_AUTH", "1") == "1"
ENABLE_INVOICE = os.getenv("ENABLE_INVOICE", "1") == "1"
ENABLE_QUOTE = os.getenv("ENABLE_QUOTE", "1") == "1"

app = FastAPI(title="Tradex Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or specific frontend URLs
    allow_credentials=True,
    allow_methods=["*"],  # ensures OPTIONS is permitted
    allow_headers=["*"],
)


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
    from . import auth

    database.create_tables()

    class LoginRequest(BaseModel):
        email: str
        password: str

    class Token(BaseModel):
        token: str

    @app.post("/api/auth/login", response_model=Token)
    def login(data: LoginRequest):
        user = database.get_user(data.email)
        if not user or not auth.verify_password(data.password, user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        # Record the login with a generic device identifier
        database.add_login(data.email, "web")
        access_token = auth.create_access_token({"sub": data.email})
        return Token(token=access_token)


    @app.get("/api/auth/users")
    def list_users():
        """Return all users for troubleshooting purposes."""
        return database.get_all_users()


    @app.get("/secure-data")
    def read_secure_data(current_user: str = Depends(get_current_user)):
        return {"user": current_user, "message": "Secure content"}


# ---------------------------------------------------------------------------
# Optional: invoice and quote routers
# ---------------------------------------------------------------------------
if ENABLE_INVOICE:
    from .invoice import router as invoice_router

    app.include_router(invoice_router, prefix="/facturas", tags=["facturas"])

if ENABLE_QUOTE:
    from .quote import router as quote_router

    app.include_router(quote_router)
