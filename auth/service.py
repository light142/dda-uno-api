"""
Authentication business logic: password hashing and JWT token management.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from config import get_settings

settings = get_settings()


# ── Password helpers ──────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the given plaintext password."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Return True if *password* matches the bcrypt *hashed* value."""
    return bcrypt.checkpw(
        password.encode("utf-8"),
        hashed.encode("utf-8"),
    )


# ── JWT helpers ───────────────────────────────────────────────────────────


def create_access_token(user_id: str) -> str:
    """Create a short-lived JWT access token (default 15 min)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def create_refresh_token(user_id: str) -> str:
    """Create a longer-lived JWT refresh token (default 7 days)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    """
    Verify and decode a JWT token.

    Raises ``jwt.ExpiredSignatureError`` or ``jwt.InvalidTokenError``
    when the token is expired or otherwise invalid.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
