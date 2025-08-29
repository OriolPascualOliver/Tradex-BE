from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from . import auth, database

app = FastAPI(title="Tradex Backend")

database.create_tables()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


class LoginRequest(BaseModel):
    org_id: str
    username: str
    password: str
    device_id: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserContext(BaseModel):
    org_id: str
    username: str


@app.post("/auth/login", response_model=TokenPair)
def login(data: LoginRequest):
    user = database.get_user(data.org_id, data.username)
    if not user or not auth.verify_password(data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    database.add_login(data.org_id, data.username, data.device_id)
    payload = {"sub": data.username, "org": data.org_id}
    access_token = auth.create_access_token(payload)
    refresh_token = auth.create_refresh_token(payload)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@app.post("/auth/refresh", response_model=TokenPair)
def refresh(data: RefreshRequest):
    payload = auth.decode_refresh_token(data.refresh_token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    auth.revoke_token(payload["jti"], payload["exp"])
    new_payload = {"sub": payload["sub"], "org": payload["org"]}
    access_token = auth.create_access_token(new_payload)
    refresh_token = auth.create_refresh_token(new_payload)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@app.post("/auth/logout")
def logout(data: RefreshRequest, token: str = Depends(oauth2_scheme)):
    access_payload = auth.decode_access_token(token)
    refresh_payload = auth.decode_refresh_token(data.refresh_token)
    if access_payload is None or refresh_payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    auth.revoke_token(access_payload["jti"], access_payload["exp"])
    auth.revoke_token(refresh_payload["jti"], refresh_payload["exp"])
    return {"detail": "Logged out"}


def get_current_user(token: str = Depends(oauth2_scheme)) -> UserContext:
    payload = auth.decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    username = payload.get("sub")
    org_id = payload.get("org")
    if username is None or org_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    user = database.get_user(org_id, username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid org context"
        )
    return UserContext(org_id=org_id, username=username)


@app.get("/secure-data")
def read_secure_data(current_user: UserContext = Depends(get_current_user)):
    return {"user": current_user.username, "org": current_user.org_id, "message": "Secure content"}
