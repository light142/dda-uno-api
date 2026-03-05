"""
Async SQLAlchemy database setup.

Provides the async engine, session factory, declarative base,
a FastAPI dependency for obtaining sessions, and a helper to
create all tables on startup.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


async def get_db():
    """FastAPI dependency — yields an async DB session and ensures cleanup."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def create_tables():
    """Create all tables defined on Base.metadata (idempotent)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)


async def _migrate(conn):
    """Lightweight column migrations for existing databases."""
    migrations = [
        ("games", "bot_mode", "VARCHAR(30) DEFAULT 'adaptive'"),
    ]
    for table, column, col_def in migrations:
        exists = await conn.scalar(
            text(f"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name='{column}'")
        )
        if not exists:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
