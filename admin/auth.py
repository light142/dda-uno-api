"""Admin authentication — simple password-based token auth."""

import hashlib
import secrets
import time

from fastapi import HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from config import get_settings

security = HTTPBearer(auto_error=False)

# In-memory token store (simple for admin, no DB needed)
_active_tokens: dict[str, float] = {}
TOKEN_EXPIRY = 24 * 3600  # 24 hours


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str


def verify_admin_password(password: str) -> bool:
    settings = get_settings()
    admin_pw = getattr(settings, 'ADMIN_PASSWORD', 'admin')
    return password == admin_pw


def create_admin_token() -> str:
    token = secrets.token_urlsafe(32)
    _active_tokens[token] = time.time()
    return token


def validate_token(token: str) -> bool:
    if token not in _active_tokens:
        return False
    created = _active_tokens[token]
    if time.time() - created > TOKEN_EXPIRY:
        del _active_tokens[token]
        return False
    return True


async def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    token: str = Query(None),
):
    """Dependency: require valid admin token (header or query param)."""
    tk = None
    if credentials:
        tk = credentials.credentials
    elif token:
        tk = token  # For SSE (EventSource can't set headers)

    if not tk or not validate_token(tk):
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")
    return tk
