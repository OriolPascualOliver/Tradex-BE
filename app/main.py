"""Application entry point with optional modules.

Environment variables control which features are enabled:
- ENABLE_USER_AUTH  - user database and login endpoints (default: "1")
- ENABLE_INVOICE    - invoice routes (default: "1")
- ENABLE_QUOTE      - quote routes (default: "1")
"""

import os
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


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
@app.post("/api/status")
def health_status():
    """Simple endpoint to verify the service is running."""
    return {"status": "alive"}


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
        refresh_token = auth.create_refresh_token({"sub": data.email})
        return TokenPair(access_token=access_token, refresh_token=refresh_token)

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


# ---------------------------------------------------------------------------
# Optional: invoice and quote routers
# ---------------------------------------------------------------------------
if ENABLE_INVOICE:
    from .invoice import router as invoice_router

    app.include_router(invoice_router, prefix="/facturas", tags=["facturas"])

if ENABLE_QUOTE:
    from .quote import router as quote_router

    app.include_router(quote_router)
