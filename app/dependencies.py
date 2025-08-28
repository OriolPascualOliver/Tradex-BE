from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from . import auth, database

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)


def get_current_user(
    token: str = Depends(oauth2_scheme), request: Request = None
) -> str:
    if not token and request is not None:
        token = request.cookies.get("auth_token")
    payload = auth.decode_access_token(token) if token else None
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    user = database.get_user(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return username
