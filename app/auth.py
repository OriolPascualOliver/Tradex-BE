from datetime import datetime, timedelta
from typing import Optional
import os
import time
import uuid

from jose import JWTError, jwt
from passlib.context import CryptContext

# ---------------------------------------------------------------------------
# Secret management and token expiry
# ---------------------------------------------------------------------------
SECRET_KEYS_ENV = os.getenv("SECRET_KEYS")
if SECRET_KEYS_ENV:
    SECRET_KEYS = [k.strip() for k in SECRET_KEYS_ENV.split(",") if k.strip()]
else:
    SECRET_KEYS = [os.getenv("SECRET_KEY", "dev-secret")]

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "5"))
REFRESH_TOKEN_EXPIRE_MINUTES = int(os.getenv("REFRESH_TOKEN_EXPIRE_MINUTES", str(60 * 24)))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------
COMMON_PASSWORDS = {
    "password",
    "123456",
    "123456789",
    "qwerty",
    "abc123",
    "letmein",
}

def validate_password(password: str) -> bool:
    """Basic password policy validation."""
    if len(password) < 8:
        return False
    if password.lower() in COMMON_PASSWORDS:
        return False
    if password.isdigit() or password.isalpha():
        return False
    return True

# ---------------------------------------------------------------------------
# Brute force protection
# ---------------------------------------------------------------------------
FAILED_LOGINS: dict[str, dict] = {}
MAX_FAILED_ATTEMPTS = 5
BACKOFF_SECONDS = 60


def is_ip_blocked(ip: str) -> bool:
    entry = FAILED_LOGINS.get(ip)
    return bool(entry and entry.get("lock_until", 0) > time.time())


def record_failed_login(ip: str) -> None:
    entry = FAILED_LOGINS.get(ip, {"count": 0, "lock_until": 0})
    if entry.get("lock_until", 0) > time.time():
        FAILED_LOGINS[ip] = entry
        return
    entry["count"] += 1
    if entry["count"] >= MAX_FAILED_ATTEMPTS:
        entry["lock_until"] = time.time() + BACKOFF_SECONDS
        entry["count"] = 0
    FAILED_LOGINS[ip] = entry


def reset_failed_logins(ip: str) -> None:
    FAILED_LOGINS.pop(ip, None)

# ---------------------------------------------------------------------------
# Token revocation and refresh tracking
# ---------------------------------------------------------------------------
revoked_tokens: set[str] = set()
active_refresh_tokens: dict[str, str] = {}


def _create_token(data: dict, expires_delta: timedelta, token_type: str) -> tuple[str, str]:
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    jti = str(uuid.uuid4())
    to_encode.update({"exp": expire, "jti": jti, "type": token_type})
    token = jwt.encode(to_encode, SECRET_KEYS[0], algorithm=ALGORITHM)
    return token, jti


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    token, _ = _create_token(
        data,
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "access",
    )
    return token


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    token, jti = _create_token(
        data,
        expires_delta or timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES),
        "refresh",
    )
    active_refresh_tokens[jti] = data["sub"]
    return token


def decode_token(token: str, token_type: str) -> Optional[dict]:
    for secret in SECRET_KEYS:
        try:
            payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
            if payload.get("type") != token_type:
                continue
            if payload.get("jti") in revoked_tokens:
                return None
            return payload
        except JWTError:
            continue
    return None


def decode_access_token(token: str) -> Optional[dict]:
    return decode_token(token, "access")


def decode_refresh_token(token: str) -> Optional[dict]:
    payload = decode_token(token, "refresh")
    if payload and payload.get("jti") in active_refresh_tokens:
        return payload
    return None


def revoke_token(token: str) -> None:
    payload = decode_access_token(token)
    if payload:
        revoked_tokens.add(payload["jti"])


def use_refresh_token(token: str) -> Optional[str]:
    payload = decode_refresh_token(token)
    if not payload:
        return None
    jti = payload["jti"]
    active_refresh_tokens.pop(jti, None)
    revoked_tokens.add(jti)
    return payload.get("sub")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def revoke_refresh_tokens_for_user(username: str) -> None:
    to_remove = [jti for jti, user in active_refresh_tokens.items() if user == username]
    for jti in to_remove:
        active_refresh_tokens.pop(jti, None)
        revoked_tokens.add(jti)
