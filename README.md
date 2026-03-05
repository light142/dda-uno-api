# DDA UNO — API

Deployable FastAPI service that wraps the `engine/` package to serve the UNO game over HTTP.

## Architecture

```
dda-uno/
├── engine/       ← Pure core package (game logic, agents, bots)
├── api/          ← This folder: FastAPI service
├── simulator/    ← Offline training & simulation
└── app/          ← Phaser.js frontend (connects here)
```

```
┌──────────────┐
│    app/      │  ← Phaser.js frontend
│  (browser)   │
└──────┬───────┘
       │ HTTP / JSON
       ▼
┌──────────────┐
│    api/      │  ← FastAPI (this folder)
│  (serve)     │
└──────┬───────┘
       │ imports
       ▼
┌──────────────┐
│   engine/    │
│  game_logic/ │  ← Core game logic, agents, controller, store
└──────────────┘
```

## Structure

```
api/
├── main.py                  # FastAPI app entry point, CORS, health check
├── config.py                # Pydantic Settings (env vars: DB_URL, MODEL_DIR, CORS)
├── database.py              # SQLAlchemy async engine + session factory
├── models.py                # ORM models: User, Game
├── dependencies.py          # FastAPI deps: get_current_user (JWT)
├── requirements.txt
├── .env.example
│
├── auth/                    # Authentication
│   ├── router.py            # POST /register, /login, /refresh, /logout
│   ├── schemas.py           # RegisterRequest, LoginRequest, TokenResponse, etc.
│   └── service.py           # Password hashing, JWT create/decode
│
├── game/                    # Game endpoints + engine bridge
│   ├── router.py            # POST /games, /games/{id}/play, /games/{id}/pass, GET /games/active
│   ├── schemas.py           # CardSchema, GameStateSchema, PlayRequest, PlayResponse, etc.
│   ├── service.py           # Orchestrates GameSession + BotManager + DB
│   ├── session.py           # RLCard-backed GameSession (serialize/deserialize)
│   ├── bot_manager.py       # Tier-based bot decisions via TierModelPool + AdaptiveTierController
│   ├── cards.py             # Card dataclass, validation helpers
│   └── rlcard_bridge.py     # Translates API Card <-> RLCard action IDs
│
├── player/                  # Player profile, history & bot mode
│   ├── router.py            # GET /me, GET /me/history, DELETE /me/history, PUT /me/bot-mode
│   └── schemas.py           # PlayerStatsSchema, GameHistoryItem, etc.
│
├── models/                  # Model metadata
│   └── manifest.json        # Trained model version info
│
└── data/                    # Runtime data
    └── dda_uno.db           # SQLite database (gitignored)
```

## Endpoints

### Auth (`/api/auth`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/register` | Create account (email + password) |
| POST | `/login` | Login, returns access + refresh tokens |
| POST | `/refresh` | Refresh access token |
| POST | `/logout` | Invalidate refresh token |

### Game (`/api/games`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/` | Start a new game |
| GET | `/active` | Check for in-progress game |
| GET | `/{gameId}` | Get current game state |
| POST | `/{gameId}/play` | Play a card (bots respond) |
| POST | `/{gameId}/pass` | Draw + pass turn (bots respond) |

### Player (`/api/users`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/me` | Player profile + stats (includes `botMode`) |
| GET | `/me/history` | Paginated game history (includes `botTier` per game) |
| PUT | `/me/bot-mode` | Set bot difficulty mode (`adaptive` or a tier name) |
| DELETE | `/me/history` | Reset stats, history, and bot mode |

### Other

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/models/info` | Model version + training metadata |

## Tech Stack

- **FastAPI** — async HTTP framework
- **SQLAlchemy** (async) — ORM + database layer
- **Pydantic** — request/response schemas + settings
- **SQLite** (dev) → **Postgres** (production)
- **Uvicorn** — ASGI server
- **JWT** — access + refresh token auth

## Running

```bash
cd api
pip install -r requirements.txt
PYTHONPATH=.. uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

> **Note:** `PYTHONPATH=..` is required so Python can resolve `engine.*` imports
> from the parent directory.

Or via Docker from the project root:

```bash
docker build -t dda-uno-api .
docker run -p 8000:8000 dda-uno-api
```

## Engine Imports

The API imports from the shared engine package (no duplication):

```python
from engine.game_logic.tiers import TierModelPool, AdaptiveTierController
from engine.game_logic.tiers.tier_config import TIER_ORDER, TIER_NAMES, VOLUNTARY_DRAW_POLICY
from engine.game_logic.game import UnoGame
from engine.config.game import NUM_PLAYERS, PLAYER_SEAT
```

## Tier-Based Adaptive Difficulty

The API uses a tier-based system (replacing the old AdaptiveAgent coin-flip):

- **6 tiers** (hardest to easiest): hyper_adversarial, adversarial, selfish, random, altruistic, hyper_altruistic
- **AdaptiveTierController** selects a tier per-game based on `error = win_rate - target_win_rate`
- **Fixed mode**: players can lock all bots to a specific tier via `PUT /me/bot-mode`
- **BotManager** loads all tier agents at startup via `TierModelPool` — no per-game disk I/O
- Missing model files fall back to random agent (graceful degradation)
