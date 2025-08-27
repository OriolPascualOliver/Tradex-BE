"""Application entry point with optional modules.

Environment variables control which features are enabled:

```
ENABLE_USER_AUTH  - user database and login endpoints (default: "0")
ENABLE_INVOICE    - invoice routes (default: "0")
ENABLE_QUOTE      - quote routes (default: "0")
```
"""

import os
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


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
@app.post("/api/status")
def health_status():
    """Simple endpoint to verify the service is running."""
    return {"status": "alive"}


# ---------------------------------------------------------------------------
# Optional: user database and authentication
# ---------------------------------------------------------------------------
if ENABLE_USER_AUTH:
    from . import auth, database
    from .dependencies import get_current_user

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
