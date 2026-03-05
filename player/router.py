"""
Player router — profile, stats, game history, and bot mode settings.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from dependencies import get_current_user
from models import User, Game
from engine.game_logic.tiers.tier_config import TIER_NAMES
from .schemas import (
    PlayerStatsSchema,
    PlayerProfileResponse,
    GameHistoryItem,
    GameHistoryResponse,
    PaginationSchema,
)

router = APIRouter(prefix="/api/users", tags=["Users"])

# Valid bot mode choices: "adaptive" + all 6 tier names
_VALID_BOT_MODES = {"adaptive"} | TIER_NAMES


# ── GET /api/users/me ────────────────────────────────────────────────────


@router.get(
    "/me",
    response_model=PlayerProfileResponse,
    summary="Get player profile and stats",
    description="Returns the authenticated user's profile with gameplay statistics.",
)
async def get_profile(user: User = Depends(get_current_user)):
    games = user.games_played or 0
    wins = user.wins or 0
    win_rate = wins / games if games > 0 else 0.0

    return PlayerProfileResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        stats=PlayerStatsSchema(
            gamesPlayed=games,
            gamesWon=wins,
            winRate=round(win_rate, 4),
            currentBotStrength=user.bot_strength,
            targetWinRate=user.target_win_rate,
            botMode=user.bot_mode,
        ),
    )


# ── GET /api/users/me/history ────────────────────────────────────────────


@router.get(
    "/me/history",
    response_model=GameHistoryResponse,
    summary="Get game history",
    description="Returns a paginated list of the player's past games with per-game analytics.",
)
async def get_history(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Count total games
    count_result = await db.execute(
        select(func.count(Game.id)).where(Game.user_id == user.id)
    )
    total = count_result.scalar() or 0

    # Fetch page
    offset = (page - 1) * limit
    result = await db.execute(
        select(Game)
        .where(Game.user_id == user.id)
        .order_by(Game.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    games = result.scalars().all()

    items = []
    for g in games:
        if g.status == "finished" and g.winner is not None:
            result_str = "win" if g.winner == 0 else "loss"
        else:
            result_str = None

        items.append(GameHistoryItem(
            gameId=g.id,
            status=g.status,
            result=result_str,
            botTier=g.bot_tier,
            botStrengthStart=g.bot_strength_start,
            botStrengthEnd=g.bot_strength_end,
            playerWinRate=g.player_win_rate_at_game,
            turns=g.turns,
            modelVersion=g.model_version,
            finishedAt=g.finished_at,
            createdAt=g.created_at,
        ))

    return GameHistoryResponse(
        games=items,
        pagination=PaginationSchema(page=page, limit=limit, total=total),
    )


# ── PUT /api/users/me/bot-mode ───────────────────────────────────────────


class SetBotModeRequest(BaseModel):
    mode: str = Field(..., description="'adaptive' or a tier name")


class SetBotModeResponse(BaseModel):
    botMode: str


class SetTargetWinRateRequest(BaseModel):
    targetWinRate: float = Field(..., description="Desired win rate (server validates against config bounds)")


class SetTargetWinRateResponse(BaseModel):
    targetWinRate: float


@router.put(
    "/me/bot-mode",
    response_model=SetBotModeResponse,
    summary="Set bot difficulty mode",
    description=(
        "Set to 'adaptive' for automatic tier selection based on win rate, "
        "or a specific tier name to fix all bots to that tier."
    ),
)
async def set_bot_mode(
    body: SetBotModeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mode = body.mode
    if mode not in _VALID_BOT_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid mode '{mode}'. Use 'adaptive' or one of: {sorted(TIER_NAMES)}",
        )
    user.bot_mode = mode
    await db.commit()
    return SetBotModeResponse(botMode=mode)


# ── PUT /api/users/me/target-win-rate ──────────────────────────────────


@router.put(
    "/me/target-win-rate",
    response_model=SetTargetWinRateResponse,
    summary="Set target win rate",
    description="Set the player's desired win rate. Bounds enforced from simulation data (default 10%–80%).",
)
async def set_target_win_rate(
    body: SetTargetWinRateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    lo, hi = settings.MIN_TARGET_WIN_RATE, settings.MAX_TARGET_WIN_RATE
    if not (lo <= body.targetWinRate <= hi):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"targetWinRate must be between {lo} and {hi} (data-driven bounds).",
        )
    user.target_win_rate = body.targetWinRate
    await db.commit()
    return SetTargetWinRateResponse(targetWinRate=user.target_win_rate)


# ── DELETE /api/users/me/history ─────────────────────────────────────────


@router.delete(
    "/me/history",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear game history and reset stats",
    description=(
        "Deletes all game records for the player and resets stats "
        "(games_played, wins, bot_mode) to defaults."
    ),
)
async def clear_history(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Delete all games for this user
    await db.execute(delete(Game).where(Game.user_id == user.id))

    # Reset user stats
    user.games_played = 0
    user.wins = 0
    user.bot_strength = 0.5
    user.bot_mode = "adaptive"

    await db.commit()
    return None
