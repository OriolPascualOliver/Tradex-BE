from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from . import auth, database

app = FastAPI(title="Tradex Backend")

database.create_tables()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


class LoginRequest(BaseModel):
    org_id: str
    username: str
    password: str
    device_id: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserContext(BaseModel):
    org_id: str
    username: str


@app.post("/login", response_model=Token)
def login(data: LoginRequest):
    user = database.get_user(data.org_id, data.username)
    if not user or not auth.verify_password(data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    database.add_login(data.org_id, data.username, data.device_id)
    access_token = auth.create_access_token({"sub": data.username, "org": data.org_id})
    return Token(access_token=access_token)


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
