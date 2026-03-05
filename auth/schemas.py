"""
Pydantic schemas for authentication request / response bodies.
"""

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ── Requests ──────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, description="Minimum 6 characters")
    username: str = Field(..., min_length=1, max_length=100)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "email": "player@example.com",
                    "password": "secret123",
                    "username": "PlayerOne",
                }
            ]
        }
    }


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "email": "player@example.com",
                    "password": "secret123",
                }
            ]
        }
    }


class RefreshRequest(BaseModel):
    refresh_token: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
                }
            ]
        }
    }


# ── Responses ─────────────────────────────────────────────────────────────


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIs...",
                    "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
                    "token_type": "bearer",
                }
            ]
        }
    }


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    games_played: int
    wins: int
    win_rate: float
    bot_strength: float
    created_at: datetime

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "email": "player@example.com",
                    "username": "PlayerOne",
                    "games_played": 10,
                    "wins": 5,
                    "win_rate": 0.5,
                    "bot_strength": 0.5,
                    "created_at": "2026-01-01T00:00:00",
                }
            ]
        }
    }

    @classmethod
    def from_user(cls, user) -> "UserResponse":
        """Build a UserResponse from a SQLAlchemy User model instance."""
        games = user.games_played or 0
        wins = user.wins or 0
        win_rate = wins / games if games > 0 else 0.0
        return cls(
            id=user.id,
            email=user.email,
            username=user.username,
            games_played=games,
            wins=wins,
            win_rate=round(win_rate, 4),
            bot_strength=user.bot_strength,
            created_at=user.created_at,
        )
