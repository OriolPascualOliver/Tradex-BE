from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel

from . import auth, database
from .invoice import router as invoice_router
from .quote import router as quote_router
from .dependencies import get_current_user

app = FastAPI(title="Tradex Backend")

database.create_tables()
app.include_router(invoice_router, prefix="/facturas", tags=["facturas"])
app.include_router(quote_router)


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
