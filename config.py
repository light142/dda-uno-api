"""
Application configuration using Pydantic Settings.

Reads from environment variables with sensible defaults for local development.
"""

import sys
import os
from functools import lru_cache

from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Make the engine/ package importable when running from the api/ directory.
# engine/ sits alongside api/ in the project root.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class Settings(BaseSettings):
    """Central configuration — every value can be overridden via env vars."""

    SECRET_KEY: str = "dev-secret-key-change-in-production"
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/dda_uno.db"
    MODEL_DIR: str = "./models_lite"
    CORS_ORIGINS: str = "*"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ADMIN_PASSWORD: str = "admin"

    # Data-driven target win rate bounds (from 10k-game simulations)
    # Min: pro vs hyper_adversarial ≈ 21% (even best player can't go lower)
    # Max: noob vs hyper_altruistic ≈ 60% (even weakest player can't go higher)
    MIN_TARGET_WIN_RATE: float = 0.20
    MAX_TARGET_WIN_RATE: float = 0.60

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


@lru_cache()
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()
