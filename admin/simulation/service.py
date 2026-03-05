"""Background simulation runner with SSE event publishing."""

import asyncio
import glob
import json
import os
import uuid
import time
from datetime import datetime, timezone
from typing import Any

from engine.game_logic.game import UnoGame
from engine.game_logic.tiers.tier_pool import TierModelPool
from engine.game_logic.tiers.tier_controller import AdaptiveTierController
from engine.game_logic.tiers.tier_config import (
    TIER_ORDER, AGENT_CHOICES, VOLUNTARY_DRAW_POLICY,
    TIER_SEAT_OVERRIDE, TARGET_SEAT_TIERS, FIXED_TARGET,
    resolve_agent_name,
)
from engine.config.game import NUM_PLAYERS, PLAYER_SEAT


# ── In-memory state ────────────────────────────────────────────────

# Simulation records (would be DB in production)
_simulations: dict[str, dict] = {}

# SSE channels: sim_id -> list of asyncio.Queue
_channels: dict[str, list[asyncio.Queue]] = {}

# Shared model pool (lazy init)
_pool: TierModelPool | None = None


def _get_pool(model_dir: str) -> TierModelPool:
    global _pool
    if _pool is None:
        _pool = TierModelPool(model_dir=model_dir)
    return _pool


# ── Channel management ──────────────────────────────────────────────

def subscribe(sim_id: str) -> asyncio.Queue:
    if sim_id not in _channels:
        _channels[sim_id] = []
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _channels[sim_id].append(q)
    return q


def unsubscribe(sim_id: str, q: asyncio.Queue):
    if sim_id in _channels:
        _channels[sim_id] = [x for x in _channels[sim_id] if x is not q]
        if not _channels[sim_id]:
            del _channels[sim_id]


def _publish(sim_id: str, event_type: str, data: dict):
    for q in _channels.get(sim_id, []):
        try:
            q.put_nowait({"event": event_type, "data": data})
        except asyncio.QueueFull:
            pass  # Drop events for slow consumers


# ── Helpers ──────────────────────────────────────────────────────────

def _build_target_dict(seats: list[str], cli_target: int | None) -> dict:
    targets = {}
    for i, agent in enumerate(seats):
        if agent in FIXED_TARGET:
            targets[i] = FIXED_TARGET[agent]
        elif agent in TARGET_SEAT_TIERS:
            targets[i] = cli_target
        else:
            targets[i] = None
    return targets


def _get_draw_caps(seats: list[str]) -> dict:
    return {i: VOLUNTARY_DRAW_POLICY.get(seats[i], 0) for i in range(len(seats))}


# ── Simulation runners ──────────────────────────────────────────────

def _run_single_sync(sim_id: str, config: dict, model_dir: str):
    """Run a single-combo simulation (blocking, called from thread)."""
    pool = _get_pool(model_dir)
    seats = [
        resolve_agent_name(config['seat0']),
        resolve_agent_name(config.get('seat1', 'selfish')),
        resolve_agent_name(config.get('seat2', 'selfish')),
        resolve_agent_name(config.get('seat3', 'selfish')),
    ]
    num_games = config['games']
    game = UnoGame()
    agents = [pool.get(s) for s in seats]
    game.set_agents(agents)
    game.set_target_seat(_build_target_dict(seats, 0))
    game.set_max_voluntary_draws(_get_draw_caps(seats))

    wins = {i: 0 for i in range(NUM_PLAYERS)}
    progress_every = max(1, num_games // 100)

    for g in range(num_games):
        result = game.run_game(is_training=False)
        winner = result['winner']
        wins[winner] += 1

        _simulations[sim_id]['games_done'] = g + 1

        if (g + 1) % progress_every == 0:
            wr = wins[PLAYER_SEAT] / (g + 1)
            _publish(sim_id, 'progress', {
                'game': g + 1,
                'win_rate': round(wr, 4),
                'winner': winner,
            })

    win_rates = {f's{i}': round(wins[i] / num_games, 4) for i in range(NUM_PLAYERS)}
    return {
        'mode': 'single',
        'seats': {f's{i}': seats[i] for i in range(NUM_PLAYERS)},
        'games': num_games,
        'wins': wins,
        'win_rates': win_rates,
        'final_win_rate': wins[PLAYER_SEAT] / num_games,
    }


def _run_adaptive_sync(sim_id: str, config: dict, model_dir: str):
    """Run an adaptive simulation (blocking, called from thread)."""
    pool = _get_pool(model_dir)
    seat0 = resolve_agent_name(config['seat0'])
    num_games = config['games']
    target_wr = config.get('target_win_rate', 0.25)

    controller = AdaptiveTierController(target_win_rate=target_wr)
    game = UnoGame()

    s0_wins = 0
    total = 0
    tier_usage: dict[str, int] = {}
    tier_wins: dict[str, int] = {}
    wr_trajectory: list[float] = []
    progress_every = max(1, num_games // 100)

    # Convergence tracking
    convergence_game = None
    converged_streak = 0
    convergence_threshold = 0.02
    convergence_window = 50

    for g in range(num_games):
        win_rate = s0_wins / total if total > 0 else 0.0
        tier, is_var = controller.select_tier_detailed(win_rate, total)
        tier_usage[tier] = tier_usage.get(tier, 0) + 1

        if tier in TIER_SEAT_OVERRIDE:
            bot_tiers = TIER_SEAT_OVERRIDE[tier]
        else:
            bot_tiers = [tier, tier, tier]

        seats = [seat0] + bot_tiers
        agents = [pool.get(s) for s in seats]
        game.set_agents(agents)
        game.set_target_seat(_build_target_dict(seats, 0))
        game.set_max_voluntary_draws(_get_draw_caps(seats))

        result = game.run_game(is_training=False)
        winner = result['winner']
        total += 1
        if winner == PLAYER_SEAT:
            s0_wins += 1
            tier_wins[tier] = tier_wins.get(tier, 0) + 1

        wr = s0_wins / total
        wr_trajectory.append(round(wr, 4))

        # Convergence detection
        if convergence_game is None:
            error = wr - target_wr
            if abs(error) <= convergence_threshold:
                converged_streak += 1
                if converged_streak >= convergence_window:
                    convergence_game = total - convergence_window + 1
            else:
                converged_streak = 0

        _simulations[sim_id]['games_done'] = g + 1

        if (g + 1) % progress_every == 0:
            _publish(sim_id, 'progress', {
                'game': g + 1,
                'win_rate': round(wr, 4),
                'tier': tier,
                'winner': winner,
                'error': round(wr - target_wr, 4),
            })

    final_wr = s0_wins / total if total > 0 else 0.0
    tier_seat0_wr = {
        t: round(tier_wins.get(t, 0) / tier_usage[t], 4) if tier_usage.get(t, 0) > 0 else None
        for t in TIER_ORDER
    }

    return {
        'mode': 'adaptive',
        'metadata': {
            'seat0': seat0,
            'target_win_rate': target_wr,
            'games': num_games,
            'convergence_threshold': convergence_threshold,
            'convergence_window': convergence_window,
        },
        'final_win_rate': round(final_wr, 4),
        'final_error': round(final_wr - target_wr, 4),
        'convergence': {
            'converged': convergence_game is not None,
            'game': convergence_game,
        },
        'wr_trajectory': wr_trajectory,
        'tier_usage': {t: tier_usage.get(t, 0) for t in TIER_ORDER},
        'tier_seat0_win_rates': tier_seat0_wr,
    }


# ── Public API ───────────────────────────────────────────────────────

def create_simulation(config: dict) -> dict:
    sim_id = str(uuid.uuid4())
    sim = {
        'id': sim_id,
        'mode': config['mode'],
        'status': 'pending',
        'config': config,
        'games_total': config['games'],
        'games_done': 0,
        'result': None,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'finished_at': None,
    }
    _simulations[sim_id] = sim
    return sim


async def run_simulation(sim_id: str, model_dir: str):
    """Run simulation in a background thread."""
    sim = _simulations.get(sim_id)
    if not sim:
        return

    sim['status'] = 'running'
    config = sim['config']

    try:
        if config['mode'] == 'adaptive':
            runner = _run_adaptive_sync
        else:
            runner = _run_single_sync

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, runner, sim_id, config, model_dir
        )

        sim['result'] = result
        sim['status'] = 'completed'
        sim['finished_at'] = datetime.now(timezone.utc).isoformat()

        # Extract final_win_rate for list view
        if 'final_win_rate' in result:
            sim['final_win_rate'] = result['final_win_rate']

        _publish(sim_id, 'complete', result)

    except Exception as e:
        sim['status'] = 'failed'
        sim['error_message'] = str(e)
        _publish(sim_id, 'error', {'message': str(e)})


def get_simulation(sim_id: str) -> dict | None:
    return _simulations.get(sim_id)


def list_simulations() -> list[dict]:
    return sorted(
        _simulations.values(),
        key=lambda s: s['created_at'],
        reverse=True,
    )


def delete_simulation(sim_id: str) -> bool:
    if sim_id in _simulations:
        del _simulations[sim_id]
        if sim_id in _channels:
            del _channels[sim_id]
        return True
    return False


# ── Import existing simulation data ──────────────────────────────────

SIMULATOR_DATA_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', '..', 'simulator', 'data',
))


def import_existing_simulations():
    """Load existing JSON results from simulator/data/ into the in-memory store."""
    if not os.path.isdir(SIMULATOR_DATA_DIR):
        return 0

    count = 0
    for path in sorted(glob.glob(os.path.join(SIMULATOR_DATA_DIR, '*_results*.json'))):
        try:
            with open(path, 'r') as f:
                result = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        filename = os.path.basename(path)
        mode = result.get('mode', 'single')

        # Build config from result metadata
        if mode == 'adaptive':
            meta = result.get('metadata', {})
            config = {
                'mode': 'adaptive',
                'seat0': meta.get('seat0', 'unknown'),
                'games': meta.get('games', 0),
                'target_win_rate': meta.get('target_win_rate', 0.25),
            }
            games_total = meta.get('games', 0)
        else:
            seats = result.get('seats', {})
            config = {
                'mode': 'single',
                'seat0': seats.get('s0', 'unknown'),
                'seat1': seats.get('s1', 'unknown'),
                'seat2': seats.get('s2', 'unknown'),
                'seat3': seats.get('s3', 'unknown'),
                'games': result.get('games', 0),
            }
            games_total = result.get('games', 0)

        # Extract final win rate
        if mode == 'adaptive':
            final_wr = result.get('final_win_rate')
        else:
            wr = result.get('win_rates', {})
            final_wr = wr.get('s0')

        # Use file modification time as creation timestamp
        mtime = os.path.getmtime(path)
        created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

        sim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, filename))
        _simulations[sim_id] = {
            'id': sim_id,
            'mode': mode,
            'status': 'completed',
            'config': config,
            'games_total': games_total,
            'games_done': games_total,
            'result': result,
            'final_win_rate': final_wr,
            'created_at': created_at,
            'finished_at': created_at,
            'source': filename,  # track origin
        }
        count += 1

    return count
