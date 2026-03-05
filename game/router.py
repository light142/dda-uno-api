"""
Game router — create, play, pass, retrieve games, and debug card overrides.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models import User
from .schemas import (
    PlayRequest,
    DebugCardsRequest,
    DebugCardsResponse,
    StartGameResponse,
    PlayResponse,
    PassResponse,
    GameStateSchema,
    ActiveGameResponse,
)
from . import service

router = APIRouter(prefix="/api/games", tags=["Games"])


# ── POST /api/games ──────────────────────────────────────────────────────


@router.post(
    "",
    response_model=StartGameResponse,
    summary="Start a new game",
    description=(
        "Creates a new UNO game with AI bots calibrated to the player's "
        "current bot strength. Abandons any existing in-progress game. "
        "If debug cards are set, uses those instead of random dealing."
    ),
)
async def create_game(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.create_game(user, db)


# ── Debug cards (set / get / unset) ──────────────────────────────────────


@router.put(
    "/debug/cards",
    response_model=DebugCardsResponse,
    summary="Set fixed cards for next games",
    description=(
        "Sets a fixed starter card and optionally fixed player hands. "
        "All subsequent POST /api/games calls will use these cards "
        "until cleared. Stored in server memory (resets on restart)."
    ),
)
async def set_debug_cards(
    body: DebugCardsRequest,
    user: User = Depends(get_current_user),
):
    return service.set_debug_cards(user.id, body)


@router.get(
    "/debug/cards",
    response_model=DebugCardsResponse,
    summary="Get current debug card config",
    description="Check whether debug cards are active and view the configuration.",
)
async def get_debug_cards(user: User = Depends(get_current_user)):
    return service.get_debug_cards(user.id)


@router.delete(
    "/debug/cards",
    response_model=DebugCardsResponse,
    summary="Clear fixed cards",
    description="Remove debug card overrides. Games will deal randomly again.",
)
async def clear_debug_cards(user: User = Depends(get_current_user)):
    return service.clear_debug_cards(user.id)


# ── POST /api/games/{game_id}/play ───────────────────────────────────────


@router.post(
    "/{game_id}/play",
    response_model=PlayResponse,
    summary="Play a card",
    description=(
        "Human player plays a card. Bot turns run automatically after. "
        "If the game ends, bot strength is adjusted based on outcome."
    ),
)
async def play_card(
    game_id: str,
    body: PlayRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.play_card(game_id, user, body.card, body.chosenColor, db)


# ── POST /api/games/{game_id}/pass ───────────────────────────────────────


@router.post(
    "/{game_id}/pass",
    response_model=PassResponse,
    summary="Draw and pass",
    description=(
        "Human player draws a card from the deck and passes their turn. "
        "Bot turns run automatically after."
    ),
)
async def pass_turn(
    game_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.pass_turn(game_id, user, db)


# ── GET /api/games/active ───────────────────────────────────────────────


@router.get(
    "/active",
    response_model=ActiveGameResponse,
    summary="Check for in-progress game",
    description="Returns the player's in-progress game state if one exists.",
)
async def get_active_game(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.find_active_game(user, db)


# ── GET /api/games/{game_id} ─────────────────────────────────────────────


@router.get(
    "/{game_id}",
    response_model=GameStateSchema,
    summary="Get game state",
    description="Retrieve the current game state — useful for reconnection.",
)
async def get_game(
    game_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_game(game_id, user, db)
