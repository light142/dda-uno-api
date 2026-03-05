"""Admin API router — aggregates all admin sub-routers and endpoints."""

import csv
import os
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models import User, Game
from admin.auth import (
    LoginRequest, LoginResponse, require_admin,
    verify_admin_password, create_admin_token,
)
from admin.simulation.router import router as simulation_router

settings = get_settings()
router = APIRouter()

# Include simulation sub-router
router.include_router(simulation_router)

# ── Training data paths ─────────────────────────────────────────────

TRAINING_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'simulator', 'models')
TRAINING_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'simulator', 'config')

TIER_REWARD_PARAMS = {
    'selfish': {
        'POWER_CARD_WASTE': -0.2,
        'POWER_CARD_BLOCK': 0.3,
        'WILD_WASTE': -0.15,
        'TERMINAL_BOT_WIN': 1.0,
        'TERMINAL_SEAT0_WIN': -1.0,
    },
    'adversarial': {
        'SEAT0_HIT_BONUS': 0.5,
        'FRIENDLY_FIRE_PENALTY': -0.5,
        'TERMINAL_BOT_WIN': 1.0,
        'TERMINAL_SEAT0_WIN': -1.0,
    },
    'altruistic': {
        'TARGET_HIT_PENALTY': -0.5,
        'OPPONENT_HIT_BONUS': 0.5,
        'TERMINAL_SEAT0_WIN': 1.0,
        'TERMINAL_OTHER_WIN': -1.0,
    },
    'hyper_adversarial': {
        'TARGET_WIN_REWARD': 5.0,
        'BOT_WIN_REWARD': 2.0,
        'SEAT0_WIN_PENALTY': -10.0,
        'TARGET_HIT_PENALTY': -0.8,
        'OPPONENT_HIT_BONUS': 0.6,
        'FRIENDLY_HIT_PENALTY': -0.4,
        'DANGER_OPPONENT_BONUS': 1.2,
    },
    'hyper_altruistic': {
        'WIN_BONUS': 2.0,
        'SELF_WIN_PENALTY': -1.0,
        'PASS_PENALTY': -0.5,
        'TARGET_HIT_PENALTY': -0.5,
        'OPPONENT_HIT_BONUS': 0.5,
    },
}


# ── Auth ─────────────────────────────────────────────────────────────


@router.post("/api/admin/login", tags=["Admin Auth"])
async def admin_login(body: LoginRequest):
    if not verify_admin_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid password")
    token = create_admin_token()
    return LoginResponse(token=token)


# ── Dashboard Stats ──────────────────────────────────────────────────


@router.get("/api/admin/stats", tags=["Admin Dashboard"], dependencies=[Depends(require_admin)])
async def admin_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate stats for the dashboard home page."""
    total_players = await db.scalar(select(func.count(User.id)))
    total_games = await db.scalar(select(func.count(Game.id)).where(Game.status == 'completed'))
    total_wins = await db.scalar(select(func.sum(User.wins))) or 0
    total_played = await db.scalar(select(func.sum(User.games_played))) or 0
    avg_wr = total_wins / total_played if total_played > 0 else 0.0

    # Tier distribution from games
    tiers = await db.execute(
        select(Game.bot_tier, func.count(Game.id))
        .where(Game.bot_tier.isnot(None))
        .group_by(Game.bot_tier)
    )
    tier_dist = {row[0]: row[1] for row in tiers}

    from admin.simulation.service import list_simulations
    sims = list_simulations()

    return {
        'total_players': total_players,
        'total_games': total_games,
        'avg_win_rate': round(avg_wr, 4),
        'total_simulations': len(sims),
        'tier_distribution': tier_dist,
    }


# ── Training Data ────────────────────────────────────────────────────


def _read_tier_csv(tier_name: str) -> list[dict]:
    csv_path = os.path.join(TRAINING_DIR, tier_name, 'metrics.csv')
    if not os.path.exists(csv_path):
        return []
    rows = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                'episode': int(row['episode']),
                'seat0_wr': float(row['seat0_wr']),
                'bot_wr': float(row['bot_wr']),
                'loss': float(row['loss']),
                'epsilon': float(row['epsilon']),
                'avg_game_length': float(row['avg_game_length']),
                'vd_s0': float(row.get('vd_s0', 0)),
                'vd_s1': float(row.get('vd_s1', 0)),
                'vd_s2': float(row.get('vd_s2', 0)),
                'vd_s3': float(row.get('vd_s3', 0)),
                'buffer_size': int(row.get('buffer_size', 0)),
            })
    return rows


@router.get("/api/admin/training/tiers", tags=["Admin Training"], dependencies=[Depends(require_admin)])
async def list_training_tiers():
    """List all trained tiers with summary stats."""
    tier_names = ['selfish', 'adversarial', 'altruistic', 'hyper_adversarial', 'hyper_altruistic']
    result = []
    for name in tier_names:
        rows = _read_tier_csv(name)
        if not rows:
            continue
        last = rows[-1]
        result.append({
            'tier': name,
            'episodes': last['episode'],
            'final_bot_wr': last['bot_wr'],
            'final_seat0_wr': last['seat0_wr'],
            'final_loss': last['loss'],
            'avg_game_length': last['avg_game_length'],
        })
    return result


@router.get("/api/admin/training/tiers/{tier_name}", tags=["Admin Training"], dependencies=[Depends(require_admin)])
async def get_tier_metrics(tier_name: str):
    """Get full CSV metrics for a training tier."""
    rows = _read_tier_csv(tier_name)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No metrics found for tier: {tier_name}")
    return rows


@router.get("/api/admin/training/tiers/{tier_name}/config", tags=["Admin Training"], dependencies=[Depends(require_admin)])
async def get_tier_config(tier_name: str):
    """Get training configuration and reward parameters for a tier."""
    reward_params = TIER_REWARD_PARAMS.get(tier_name, {})
    if not reward_params:
        raise HTTPException(status_code=404, detail=f"Unknown tier: {tier_name}")

    # Read common training config
    return {
        'learning_rate': 0.0001,
        'batch_size': 32,
        'discount_factor': 0.99,
        'epsilon_start': 1.0,
        'epsilon_end': 0.1,
        'epsilon_decay_steps': 1_000_000,
        'replay_memory_size': 100_000,
        'episodes': 300_000 if tier_name == 'hyper_adversarial' else 100_000,
        'reward_params': reward_params,
    }


# ── Players ──────────────────────────────────────────────────────────


@router.get("/api/admin/players", tags=["Admin Players"], dependencies=[Depends(require_admin)])
async def list_players(db: AsyncSession = Depends(get_db)):
    """List all players with stats."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return [
        {
            'id': u.id,
            'username': u.username,
            'email': u.email,
            'games_played': u.games_played,
            'wins': u.wins,
            'win_rate': u.wins / u.games_played if u.games_played > 0 else 0.0,
            'bot_mode': u.bot_mode,
            'target_win_rate': u.target_win_rate,
            'created_at': u.created_at.isoformat(),
        }
        for u in users
    ]


@router.get("/api/admin/players/{player_id}", tags=["Admin Players"], dependencies=[Depends(require_admin)])
async def get_player_detail(player_id: str, db: AsyncSession = Depends(get_db)):
    """Get player detail with game history."""
    user = await db.get(User, player_id)
    if not user:
        raise HTTPException(status_code=404, detail="Player not found")

    games_result = await db.execute(
        select(Game)
        .where(Game.user_id == player_id, Game.status == 'completed')
        .order_by(Game.created_at.asc())
    )
    games = games_result.scalars().all()

    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'games_played': user.games_played,
        'wins': user.wins,
        'win_rate': user.wins / user.games_played if user.games_played > 0 else 0.0,
        'bot_mode': user.bot_mode,
        'target_win_rate': user.target_win_rate,
        'created_at': user.created_at.isoformat(),
        'games': [
            {
                'id': g.id,
                'winner': g.winner,
                'turns': g.turns,
                'bot_tier': g.bot_tier,
                'bot_mode': g.bot_mode,
                'player_win_rate_at_game': g.player_win_rate_at_game,
                'created_at': g.created_at.isoformat(),
            }
            for g in games
        ],
    }


# ── Analytics ────────────────────────────────────────────────────────


@router.get("/api/admin/analytics", tags=["Admin Analytics"], dependencies=[Depends(require_admin)])
async def analytics(db: AsyncSession = Depends(get_db)):
    """Cross-cutting analytics data."""
    # Get all users
    users_result = await db.execute(select(User))
    users = users_result.scalars().all()

    # Win rate distribution (buckets of 5%)
    wr_buckets: Counter = Counter()
    for u in users:
        if u.games_played > 0:
            wr = u.wins / u.games_played
            bucket = f"{int(wr * 100 // 5) * 5}-{int(wr * 100 // 5) * 5 + 5}%"
            wr_buckets[bucket] += 1
    wr_distribution = [{'bucket': b, 'count': c} for b, c in sorted(wr_buckets.items())]

    # Tier effectiveness
    tier_result = await db.execute(
        select(
            Game.bot_tier,
            func.count(Game.id),
            func.sum(func.cast(Game.winner == 0, type_=None)),
        )
        .where(Game.bot_tier.isnot(None), Game.status == 'completed')
        .group_by(Game.bot_tier)
    )
    tier_effectiveness = []
    for row in tier_result:
        tier, count, wins = row
        wins = wins or 0
        tier_effectiveness.append({
            'tier': tier,
            'avg_wr': round(wins / count * 100, 1) if count > 0 else 0,
            'games': count,
        })

    # Adaptive accuracy
    adaptive_accuracy = []
    for u in users:
        if u.games_played >= 5 and u.bot_mode == 'adaptive':
            actual = u.wins / u.games_played
            adaptive_accuracy.append({
                'player': u.username,
                'target': u.target_win_rate,
                'actual': round(actual, 4),
                'error': round(actual - u.target_win_rate, 4),
            })

    # Engagement
    engagement = [
        {'player': u.username, 'games': u.games_played}
        for u in sorted(users, key=lambda x: x.games_played, reverse=True)
        if u.games_played > 0
    ]

    return {
        'win_rate_distribution': wr_distribution,
        'tier_effectiveness': tier_effectiveness,
        'adaptive_accuracy': adaptive_accuracy,
        'engagement': engagement,
    }
