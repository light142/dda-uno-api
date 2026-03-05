"""
SQLAlchemy ORM models for the DDA UNO database.

UUID primary keys are stored as CHAR(36) for SQLite compatibility.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Text,
    ForeignKey,
)
from sqlalchemy.dialects.sqlite import CHAR

from database import Base


def _utcnow() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    """Return a new UUID4 as a 36-character string."""
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(CHAR(36), primary_key=True, default=_new_uuid)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=False)
    games_played = Column(Integer, default=0, nullable=False)
    wins = Column(Integer, default=0, nullable=False)
    bot_strength = Column(Float, default=0.5, nullable=False)
    target_win_rate = Column(Float, default=0.25, nullable=False)
    bot_mode = Column(String(30), default="adaptive", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class Game(Base):
    __tablename__ = "games"

    id = Column(CHAR(36), primary_key=True, default=_new_uuid)
    user_id = Column(CHAR(36), ForeignKey("users.id"), nullable=False)
    status = Column(String(20), default="in_progress", nullable=False)
    state_json = Column(Text, nullable=True)
    winner = Column(Integer, nullable=True)
    turns = Column(Integer, default=0, nullable=False)
    bot_strength_start = Column(Float, nullable=True)
    bot_strength_end = Column(Float, nullable=True)
    bot_tier = Column(String(30), nullable=True)
    bot_mode = Column(String(30), default="adaptive", nullable=True)
    player_win_rate_at_game = Column(Float, nullable=True)
    model_version = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
