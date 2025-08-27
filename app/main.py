"""Application entry point with optional modules.

Environment variables control which features are enabled:

```
ENABLE_USER_AUTH  - user database and login endpoints (default: "1")
ENABLE_INVOICE    - invoice routes (default: "1")
ENABLE_QUOTE      - quote routes (default: "1")
```
"""

import os
import uuid
from fastapi import Depends, FastAPI, HTTPException, status, Request, Response
from fastapi.middleware.cors import CORSMiddleware
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


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------
ENABLE_USER_AUTH = os.getenv("ENABLE_USER_AUTH", "1") == "1"
ENABLE_INVOICE = os.getenv("ENABLE_INVOICE", "1") == "1"
ENABLE_QUOTE = os.getenv("ENABLE_QUOTE", "1") == "1"

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


@app.get("/metrics")
def metrics():
    """Expose Prometheus metrics."""
    return Response(generate_metrics(), media_type=CONTENT_TYPE_LATEST)


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
            inc_login_failure()
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
