"""
DDA UNO API — FastAPI application entry point.

Run with:
    uvicorn main:app --reload
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from database import create_tables
from auth.router import router as auth_router
from game.router import router as game_router
from game.bot_manager import BotManager
from player.router import router as player_router
from admin.router import router as admin_router

settings = get_settings()


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────


async def _keep_alive():
    """Self-ping every 14 minutes to prevent HF Spaces from sleeping."""
    import httpx
    url = "http://localhost:7860/"
    while True:
        await asyncio.sleep(14 * 60)
        try:
            async with httpx.AsyncClient() as client:
                await client.get(url, timeout=10)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables and preload models on startup."""
    await create_tables()
    # Force model loading at startup so first request isn't slow
    from game.service import _bot_manager  # noqa: F401
    # Import existing simulation results from simulator/data/
    from admin.simulation.service import import_existing_simulations
    count = import_existing_simulations()
    if count:
        print(f"  Imported {count} existing simulation results")
    task = asyncio.create_task(_keep_alive())
    yield
    task.cancel()


# ── App ───────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DDA UNO API",
    version="1.0.0",
    description="Dynamic difficulty adjustment UNO game API with AI bots that adjust to each player's skill level.",
    lifespan=lifespan,
)

# CORS — split comma-separated origins or use wildcard
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)
app.include_router(game_router)
app.include_router(player_router)
app.include_router(admin_router)


# ── Health check ──────────────────────────────────────────────────────────


@app.get("/", tags=["Health"])
async def root():
    """Basic health check."""
    return {"status": "ok", "version": "1.0.0"}


# ── Model info ────────────────────────────────────────────────────────────


@app.get("/api/models/info", tags=["Models"], summary="Get model version info")
async def model_info():
    """Return the current model version and training metadata from manifest.json."""
    manager = BotManager(settings.MODEL_DIR)
    return manager.get_manifest()
