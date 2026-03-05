"""
Pydantic schemas for game request / response bodies.

All schemas include Swagger examples via model_config.
"""

from typing import Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field


# ── Card ─────────────────────────────────────────────────────────────────


class CardSchema(BaseModel):
    suit: Optional[str] = Field(None, description="Card color: red, green, blue, yellow, or null for wilds")
    value: str = Field(..., description="Card face: 0-9, block, reverse, plus2, wild, plus4")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"suit": "red", "value": "5"},
                {"suit": None, "value": "wild"},
            ]
        }
    }


# ── Bot turn ─────────────────────────────────────────────────────────────


class BotTurnSchema(BaseModel):
    playerIndex: int = Field(..., description="Seat index (0-3, 0 = human for penalty draws)")
    action: str = Field(..., description="'play' or 'draw'")
    card: Optional[CardSchema] = Field(None, description="Card played (null for draw)")
    drawnCards: Union[int, list[CardSchema]] = Field(0, description="Count for bots, card array for human penalty draws")
    chosenColor: Optional[str] = Field(None, description="Color chosen for wild cards")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "playerIndex": 1,
                    "action": "play",
                    "card": {"suit": "blue", "value": "7"},
                    "drawnCards": 0,
                    "chosenColor": None,
                }
            ]
        }
    }


# ── Game state ───────────────────────────────────────────────────────────


class GameStateSchema(BaseModel):
    gameId: str
    status: str = Field(..., description="in_progress, finished, or abandoned")
    playerHands: list = Field(..., description="Card[] for human (index 0), int for bots (indices 1-3)")
    topCard: Optional[CardSchema] = None
    discardPile: list[CardSchema] = []
    activeColor: Optional[str] = None
    isClockwise: bool = True
    deckRemaining: int = 0
    currentPlayer: int = 0
    winner: Optional[int] = Field(None, description="Winning seat index (0-3) or null")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "gameId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "status": "in_progress",
                    "playerHands": [
                        [{"suit": "red", "value": "5"}, {"suit": "blue", "value": "3"}],
                        7, 7, 7,
                    ],
                    "topCard": {"suit": "red", "value": "2"},
                    "discardPile": [],
                    "activeColor": "red",
                    "isClockwise": True,
                    "deckRemaining": 70,
                    "currentPlayer": 0,
                    "winner": None,
                }
            ]
        }
    }


# ── Model info ───────────────────────────────────────────────────────────


class ModelInfoSchema(BaseModel):
    version: str = Field(..., description="Model version from manifest.json")
    trainedAt: Optional[str] = Field(None, description="ISO timestamp of training date")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"version": "1.0.0", "trainedAt": "2026-02-24T10:00:00Z"}
            ]
        }
    }


# ── Requests ─────────────────────────────────────────────────────────────


class PlayRequest(BaseModel):
    card: CardSchema
    chosenColor: Optional[str] = Field(None, description="Required for wild/plus4 cards")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"card": {"suit": "red", "value": "5"}, "chosenColor": None},
                {"card": {"suit": None, "value": "wild"}, "chosenColor": "blue"},
            ]
        }
    }


class DebugCardsRequest(BaseModel):
    starterCard: Optional[CardSchema] = Field(None, description="The initial top card (optional)")
    activeColor: Optional[str] = Field(None, description="Active color (required if starter is wild)")
    playerHands: Optional[list[list[CardSchema]]] = Field(
        None,
        description="Fixed hands for all 4 players. Omit to keep random.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "starterCard": {"suit": "red", "value": "3"},
                    "activeColor": "red",
                    "playerHands": [
                        [{"suit": "red", "value": "5"}, {"suit": "blue", "value": "7"}],
                        [{"suit": "green", "value": "2"}],
                        [{"suit": "yellow", "value": "9"}],
                        [{"suit": "red", "value": "0"}],
                    ],
                },
                {
                    "playerHands": [
                        [{"value": "wild"}, {"value": "wild"}],
                    ],
                },
            ]
        }
    }


class DebugCardsResponse(BaseModel):
    active: bool = Field(..., description="Whether debug cards are currently set")
    config: Optional[dict] = Field(None, description="Current debug card configuration")


# ── Responses ────────────────────────────────────────────────────────────


class StartGameResponse(BaseModel):
    gameState: GameStateSchema
    dealHands: Optional[list] = Field(None, description="Original hands at deal time (before initial bot turns)")
    dealStarterCard: Optional[CardSchema] = Field(None, description="Original starter card at deal time")
    dealActiveColor: Optional[str] = Field(None, description="Original active color at deal time")
    dealIsClockwise: Optional[bool] = Field(None, description="Original direction at deal time")
    initialBotTurns: list[BotTurnSchema] = Field([], description="Bot turns that happened before human's first turn")
    botTier: str = Field(..., description="Agent tier used for bots in this game")
    botMode: str = Field("adaptive", description="Player's bot mode at game start")
    modelInfo: ModelInfoSchema


class PlayResponse(BaseModel):
    valid: bool
    botTurns: list[BotTurnSchema] = []
    gameState: GameStateSchema


class PassResponse(BaseModel):
    drawnCard: Optional[CardSchema] = None
    autoPlayed: bool = Field(False, description="Whether the drawn card was auto-played")
    chosenColor: Optional[str] = Field(None, description="Chosen color if auto-played wild")
    botTurns: list[BotTurnSchema] = []
    gameState: GameStateSchema


class ActiveGameResponse(BaseModel):
    hasActiveGame: bool = Field(..., description="Whether the player has an in-progress game")
    gameState: Optional[GameStateSchema] = Field(None, description="Full game state if active")
    botTier: Optional[str] = Field(None, description="Agent tier used for bots in this game")
    botMode: Optional[str] = Field(None, description="Bot mode the game was started with")
    nextMode: Optional[str] = Field(None, description="Player's current bot mode setting (may differ from botMode)")
