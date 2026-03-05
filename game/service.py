"""
Game service — orchestrates GameSession + BotManager + DB persistence.

Handles game lifecycle: create, play, pass, get state.
Uses tier-based adaptive difficulty: the controller selects an agent tier
per game based on the player's cumulative win rate vs their target.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models import User, Game
from config import get_settings
from .session import GameSession
from .bot_manager import BotManager
from .cards import Card
from .rlcard_bridge import api_card_to_rlcard
from engine.game_logic.tiers.tier_config import TIER_SEAT_OVERRIDE
from .schemas import (
    CardSchema,
    BotTurnSchema,
    GameStateSchema,
    ModelInfoSchema,
    DebugCardsRequest,
    DebugCardsResponse,
    StartGameResponse,
    PlayResponse,
    PassResponse,
    ActiveGameResponse,
)

settings = get_settings()
_bot_manager = BotManager(settings.MODEL_DIR)

# In-memory debug card overrides, keyed by user_id.
# When set, create_game uses these instead of random dealing.
_debug_cards: dict[str, dict] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_game_state(game: Game, session: GameSession) -> GameStateSchema:
    """Build a GameStateSchema from a Game row and live session."""
    state = session.get_state_for_player(0)
    return GameStateSchema(
        gameId=game.id,
        status=game.status,
        playerHands=state["playerHands"],
        topCard=CardSchema(**state["topCard"]) if state["topCard"] else None,
        discardPile=[CardSchema(**c) for c in state["discardPile"]],
        activeColor=state["activeColor"],
        isClockwise=state["isClockwise"],
        deckRemaining=state["deckRemaining"],
        currentPlayer=session.current_player,
        winner=state["winner"],
    )


def _build_bot_turns(raw_turns: list[dict]) -> list[BotTurnSchema]:
    """Convert raw bot turn dicts to schema objects."""
    result = []
    for t in raw_turns:
        card = CardSchema(**t["card"]) if t.get("card") else None
        raw_drawn = t.get("drawnCards", 0)
        # Human penalty draws come as list of card dicts; bot draws as int
        if isinstance(raw_drawn, list):
            drawn_cards = [CardSchema(**c) for c in raw_drawn]
        else:
            drawn_cards = raw_drawn
        result.append(BotTurnSchema(
            playerIndex=t["playerIndex"],
            action=t["action"],
            card=card,
            drawnCards=drawn_cards,
            chosenColor=t.get("chosenColor"),
        ))
    return result


def _model_info() -> ModelInfoSchema:
    """Get current model info from manifest."""
    m = _bot_manager.get_manifest()
    return ModelInfoSchema(
        version=m.get("version", "unknown"),
        trainedAt=m.get("trained_at"),
    )


def _make_decision_fn(tier: str):
    """Create a bot decision callback that uses the given tier agent.

    When TIER_SEAT_OVERRIDE applies (e.g. hyper_adversarial), each bot
    seat may use a different agent tier instead of all using the same one.
    """
    seat_override = TIER_SEAT_OVERRIDE.get(tier)

    def decide(player_index: int, hand: list[Card],
               top_card: Card, active_color: str, **context):
        # seats 1-3 map to override indices 0-2
        bot_tier = seat_override[player_index - 1] if seat_override else tier
        return _bot_manager.get_bot_decision(
            bot_tier, hand, top_card, active_color, **context)
    return decide


def _apply_debug_cards(session: GameSession, debug: dict):
    """Override session state with debug card configuration.

    Manipulates RLCard game internals through the session.
    """
    game = session._game

    # Determine active color (from explicit setting, starter card, or current game)
    sc = debug.get("starterCard")
    if sc:
        starter = Card(suit=sc["suit"], value=sc["value"])
        active_color = debug.get("activeColor") or starter.suit or "red"
        game.round.target = api_card_to_rlcard(starter, active_color)
        game.round.played_cards = [api_card_to_rlcard(starter, active_color)]
    else:
        active_color = debug.get("activeColor") or session.get_state_for_player(0).get("activeColor") or "red"

    # Override player hands if provided
    player_hands = debug.get("playerHands")
    if player_hands:
        # Return current hand cards to deck
        for player in game.players:
            game.dealer.deck.extend(player.hand)
            player.hand = []

        for i, hand_data in enumerate(player_hands):
            if i >= len(game.players):
                break
            for c in hand_data:
                card = Card(suit=c["suit"], value=c["value"])
                rl_card = api_card_to_rlcard(card, active_color)
                # Remove from deck if present (keep totals consistent)
                idx = next(
                    (j for j, dc in enumerate(game.dealer.deck)
                     if dc.color == rl_card.color and dc.trait == rl_card.trait),
                    None,
                )
                if idx is not None:
                    game.dealer.deck.pop(idx)
                game.players[i].hand.append(rl_card)

        # Fill each hand up to 7 cards by drawing from the deck
        for player in game.players:
            while len(player.hand) < 7 and game.dealer.deck:
                player.hand.append(game.dealer.deck.pop())

    game.round.current_player = 0
    session.current_player = 0


# ── Debug cards ──────────────────────────────────────────────────────────


def set_debug_cards(user_id: str, body: DebugCardsRequest) -> DebugCardsResponse:
    """Store fixed card configuration for this user."""
    data: dict = {}
    if body.starterCard is not None:
        data["starterCard"] = body.starterCard.model_dump()
    if body.activeColor is not None:
        data["activeColor"] = body.activeColor
    if body.playerHands is not None:
        data["playerHands"] = [
            [c.model_dump() for c in hand] for hand in body.playerHands
        ]
    _debug_cards[user_id] = data
    return DebugCardsResponse(active=True, config=data)


def clear_debug_cards(user_id: str) -> DebugCardsResponse:
    """Remove fixed card configuration for this user."""
    _debug_cards.pop(user_id, None)
    return DebugCardsResponse(active=False, config=None)


def get_debug_cards(user_id: str) -> DebugCardsResponse:
    """Return current debug card configuration."""
    data = _debug_cards.get(user_id)
    return DebugCardsResponse(active=data is not None, config=data)


# ── Public service functions ─────────────────────────────────────────────


async def create_game(user: User, db: AsyncSession) -> StartGameResponse:
    """Start a new game for the given user.

    If debug cards are set for this user, uses them instead of random dealing.
    """
    # Abandon any existing in-progress game
    result = await db.execute(
        select(Game).where(and_(
            Game.user_id == user.id,
            Game.status == "in_progress",
        ))
    )
    active_game = result.scalar_one_or_none()
    if active_game:
        active_game.status = "abandoned"
        active_game.finished_at = _utcnow()

    # Create session and start game
    session = GameSession()
    session.start_game()

    # Apply debug card overrides if set
    debug = _debug_cards.get(user.id)
    if debug:
        _apply_debug_cards(session, debug)

    # Select tier for this game based on player's win rate and mode
    games_played = user.games_played or 0
    wins = user.wins or 0
    win_rate = wins / games_played if games_played > 0 else 0.0
    tier = _bot_manager.select_tier(
        win_rate, games_played, user.bot_mode, user.target_win_rate,
    )

    # Capture deal state before any bot turns
    deal_state = session.get_state_for_player(0)
    deal_hands = deal_state["playerHands"]
    deal_starter = deal_state["topCard"]
    deal_color = deal_state["activeColor"]
    deal_clockwise = deal_state["isClockwise"]

    # Run initial bot turns if player 0 doesn't go first
    initial_bot_turns_raw = []
    if session.current_player != 0:
        decision_fn = _make_decision_fn(tier)
        initial_bot_turns_raw = session.run_bot_turns(decision_fn)

    # Save game to DB
    manifest = _bot_manager.get_manifest()
    game = Game(
        user_id=user.id,
        status="in_progress",
        state_json=json.dumps(session.serialize()),
        turns=session.turns,
        bot_tier=tier,
        bot_mode=user.bot_mode or "adaptive",
        player_win_rate_at_game=round(win_rate, 4),
        model_version=manifest.get("version", "unknown"),
    )
    db.add(game)
    await db.commit()
    await db.refresh(game)

    game_state = _build_game_state(game, session)
    return StartGameResponse(
        gameState=game_state,
        dealHands=deal_hands,
        dealStarterCard=CardSchema(**deal_starter) if deal_starter else None,
        dealActiveColor=deal_color,
        dealIsClockwise=deal_clockwise,
        initialBotTurns=_build_bot_turns(initial_bot_turns_raw),
        botTier=tier,
        botMode=user.bot_mode or "adaptive",
        modelInfo=_model_info(),
    )


async def play_card(
    game_id: str,
    user: User,
    card_data: CardSchema,
    chosen_color: Optional[str],
    db: AsyncSession,
) -> PlayResponse:
    """Human plays a card, then bots respond.

    After a game ends, adjusts bot strength based on outcome.
    """
    game = await _load_game(game_id, user.id, db)
    session = GameSession.deserialize(json.loads(game.state_json))

    # Validate it's the human's turn
    if session.current_player != 0:
        return PlayResponse(
            valid=False,
            botTurns=[],
            gameState=_build_game_state(game, session),
        )

    # Play the card
    result = session.play_card(0, card_data.model_dump(), chosen_color)

    if not result.get("valid"):
        return PlayResponse(
            valid=False,
            botTurns=[],
            gameState=_build_game_state(game, session),
        )

    # Prepend penalty draw (plus2/plus4) as a bot turn so the frontend animates it
    bot_turns_raw = []
    penalty = result.get("penalty_draw")
    if penalty:
        bot_turns_raw.append({
            "playerIndex": penalty["playerIndex"],
            "action": "draw",
            "card": None,
            "drawnCards": penalty["drawnCards"],
            "chosenColor": None,
        })

    # Check if human just won
    if result.get("winner") is None:
        # Run bot turns using the tier stored on this game
        decision_fn = _make_decision_fn(game.bot_tier)
        bot_turns_raw += session.run_bot_turns(decision_fn)

    # Check for winner (human or bot)
    winner = session.get_winner()
    if winner is not None:
        await _finish_game(game, session, user, winner, db)
    else:
        # Save updated state
        game.state_json = json.dumps(session.serialize())
        game.turns = session.turns

    await db.commit()

    return PlayResponse(
        valid=True,
        botTurns=_build_bot_turns(bot_turns_raw),
        gameState=_build_game_state(game, session),
    )


async def pass_turn(
    game_id: str,
    user: User,
    db: AsyncSession,
) -> PassResponse:
    """Human draws a card and passes, then bots respond."""
    game = await _load_game(game_id, user.id, db)
    session = GameSession.deserialize(json.loads(game.state_json))

    # Validate it's the human's turn
    if session.current_player != 0:
        return PassResponse(
            drawnCard=None,
            botTurns=[],
            gameState=_build_game_state(game, session),
        )

    # Human draws (auto-plays if the drawn card is playable)
    result = session.pass_turn(0)
    drawn_card = CardSchema(**result["drawn_card"]) if result.get("drawn_card") else None

    # If auto-played, prepend any penalty draw as a bot turn
    bot_turns_raw = []
    if result.get("auto_played"):
        penalty = result.get("penalty_draw")
        if penalty:
            bot_turns_raw.append({
                "playerIndex": penalty["playerIndex"],
                "action": "draw",
                "card": None,
                "drawnCards": penalty["drawnCards"],
                "chosenColor": None,
            })

    # Run bot turns (unless the auto-play already won the game)
    if result.get("winner") is None:
        decision_fn = _make_decision_fn(game.bot_tier)
        bot_turns_raw += session.run_bot_turns(decision_fn)

    # Check for winner
    winner = session.get_winner()
    if winner is not None:
        await _finish_game(game, session, user, winner, db)
    else:
        game.state_json = json.dumps(session.serialize())
        game.turns = session.turns

    await db.commit()

    return PassResponse(
        drawnCard=drawn_card,
        autoPlayed=result.get("auto_played", False),
        chosenColor=result.get("chosen_color"),
        botTurns=_build_bot_turns(bot_turns_raw),
        gameState=_build_game_state(game, session),
    )


async def get_game(game_id: str, user: User, db: AsyncSession) -> GameStateSchema:
    """Load and return current game state (for reconnection)."""
    game = await _load_game(game_id, user.id, db)
    session = GameSession.deserialize(json.loads(game.state_json))
    return _build_game_state(game, session)


async def find_active_game(user: User, db: AsyncSession) -> ActiveGameResponse:
    """Check if the player has an in-progress game and return its state."""
    result = await db.execute(
        select(Game).where(and_(
            Game.user_id == user.id,
            Game.status == "in_progress",
        ))
    )
    game = result.scalar_one_or_none()

    if game is None:
        return ActiveGameResponse(hasActiveGame=False, gameState=None)

    session = GameSession.deserialize(json.loads(game.state_json))
    return ActiveGameResponse(
        hasActiveGame=True,
        gameState=_build_game_state(game, session),
        botTier=game.bot_tier,
        botMode=game.bot_mode or "adaptive",
        nextMode=user.bot_mode or "adaptive",
    )


# ── Private helpers ──────────────────────────────────────────────────────


async def _load_game(game_id: str, user_id: str, db: AsyncSession) -> Game:
    """Load a game by ID, verify ownership and status."""
    from fastapi import HTTPException, status

    result = await db.execute(
        select(Game).where(and_(Game.id == game_id, Game.user_id == user_id))
    )
    game = result.scalar_one_or_none()

    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game not found.",
        )

    if game.status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Game is already {game.status}.",
        )

    return game


async def _finish_game(
    game: Game,
    session: GameSession,
    user: User,
    winner: int,
    db: AsyncSession,
):
    """Finalize a game: update records.

    The next game's create_game() will call select_tier() with the
    updated win rate — no strength adjustment needed here.
    """
    game.status = "finished"
    game.winner = winner
    game.turns = session.turns
    game.state_json = json.dumps(session.serialize())
    game.finished_at = _utcnow()

    # Update user stats
    user.games_played = (user.games_played or 0) + 1
    if winner == 0:
        user.wins = (user.wins or 0) + 1

    # Store win rate snapshot for analytics
    win_rate = user.wins / user.games_played if user.games_played > 0 else 0.0
    game.player_win_rate_at_game = round(win_rate, 4)
