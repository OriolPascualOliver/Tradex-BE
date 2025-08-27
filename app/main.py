"""Application entry point with optional modules.

Environment variables control which features are enabled:

```
ENABLE_USER_AUTH  - user database and login endpoints (default: "1")
ENABLE_INVOICE    - invoice routes (default: "1")
ENABLE_QUOTE      - quote routes (default: "1")
```
"""

import os
import secrets
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------
ENABLE_USER_AUTH = os.getenv("ENABLE_USER_AUTH", "1") == "1"
ENABLE_INVOICE = os.getenv("ENABLE_INVOICE", "1") == "1"
ENABLE_QUOTE = os.getenv("ENABLE_QUOTE", "1") == "1"

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
