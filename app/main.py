"""Application entry point with optional modules.

Environment variables control which features are enabled:

```
ENABLE_USER_AUTH  - user database and login endpoints (default: "1")
ENABLE_INVOICE    - invoice routes (default: "1")
ENABLE_QUOTE      - quote routes (default: "1")
```
"""

import os
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------
ENABLE_USER_AUTH = os.getenv("ENABLE_USER_AUTH", "1") == "1"
ENABLE_INVOICE = os.getenv("ENABLE_INVOICE", "1") == "1"
ENABLE_QUOTE = os.getenv("ENABLE_QUOTE", "1") == "1"

app = FastAPI(title="Tradex Backend")


# ---------------------------------------------------------------------------
# Optional: user database and authentication
# ---------------------------------------------------------------------------
if ENABLE_USER_AUTH:
    from . import auth, database
    from .dependencies import get_current_user

    database.create_tables()

    class LoginRequest(BaseModel):
        username: str
        password: str
        device_id: str

    class Token(BaseModel):
        access_token: str
        token_type: str = "bearer"

    @app.post("/login", response_model=Token)
    def login(data: LoginRequest):
        user = database.get_user(data.username)
        if not user or not auth.verify_password(data.password, user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        database.add_login(data.username, data.device_id)
        access_token = auth.create_access_token({"sub": data.username})
        return Token(access_token=access_token)

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
