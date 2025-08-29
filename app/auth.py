from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

SECRET_KEY = "change-me"  # In production, load from environment
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 14
CLOCK_SKEW_SECONDS = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# In-memory revocation list keyed by token jti
_revoked_tokens: Dict[str, datetime] = {}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def _create_token(data: dict, expires_delta: timedelta, token_type: str) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode.update({"exp": expire, "type": token_type, "jti": str(uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    return _create_token(
        data,
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "access",
    )


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    return _create_token(
        data,
        expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "refresh",
    )


def _is_token_revoked(jti: str) -> bool:
    exp = _revoked_tokens.get(jti)
    if exp is None:
        return False
    if exp < datetime.now(timezone.utc):
        # Cleanup expired entries
        del _revoked_tokens[jti]
        return False
    return True


def revoke_token(jti: str, exp: int) -> None:
    _revoked_tokens[jti] = datetime.fromtimestamp(exp, tz=timezone.utc)


def _decode_token(token: str, token_type: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"leeway": CLOCK_SKEW_SECONDS},
        )
    except JWTError:
        return None
    if payload.get("type") != token_type:
        return None
    jti = payload.get("jti")
    if jti is None or _is_token_revoked(jti):
        return None
    return payload


def decode_access_token(token: str) -> Optional[dict]:
    return _decode_token(token, "access")


def decode_refresh_token(token: str) -> Optional[dict]:
    return _decode_token(token, "refresh")
