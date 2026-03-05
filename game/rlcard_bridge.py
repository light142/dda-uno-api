"""Translation layer between human-readable card objects and RLCard's format.

RLCard action mapping (61 actions):
    - Actions  0-14: red   (0-9, skip, reverse, draw_2, wild, wild_draw_4)
    - Actions 15-29: green (same pattern)
    - Actions 30-44: blue  (same pattern)
    - Actions 45-59: yellow (same pattern)
    - Action 60: draw

Format translations:
    - Colors: red<->r, green<->g, blue<->b, yellow<->y
    - Values: block<->skip, plus2<->draw_2, plus4<->wild_draw_4
    - Wilds: In RLCard, wild cards encode chosen color (r-wild = play wild choosing red)
"""

import numpy as np
from collections import OrderedDict
from typing import Optional

from rlcard.games.uno.card import UnoCard

from .cards import Card, is_wild, is_valid_play

# ---------------------------------------------------------------------------
# Color mappings
# ---------------------------------------------------------------------------
COLOR_TO_RL = {"red": "r", "green": "g", "blue": "b", "yellow": "y"}
RL_TO_COLOR = {v: k for k, v in COLOR_TO_RL.items()}

# ---------------------------------------------------------------------------
# Value mappings (API <-> RLCard)
# ---------------------------------------------------------------------------
VALUE_TO_RL = {
    "block": "skip",
    "plus2": "draw_2",
    "plus4": "wild_draw_4",
    "wild": "wild",
    "reverse": "reverse",
}
RL_TO_VALUE = {v: k for k, v in VALUE_TO_RL.items()}

# Numbers don't change
for _i in range(10):
    VALUE_TO_RL[str(_i)] = str(_i)
    RL_TO_VALUE[str(_i)] = str(_i)

# ---------------------------------------------------------------------------
# RLCard action space: 4 colors x 15 values + 1 draw = 61
# ---------------------------------------------------------------------------
COLORS = ["r", "g", "b", "y"]
TRAITS = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "skip", "reverse", "draw_2", "wild", "wild_draw_4",
]

# Build action ID tables
ACTION_LIST: list[str] = []
ACTION_TO_ID: dict[str, int] = {}

for _ci, _color in enumerate(COLORS):
    for _ti, _trait in enumerate(TRAITS):
        _action_str = f"{_color}-{_trait}"
        _action_id = _ci * 15 + _ti
        ACTION_LIST.append(_action_str)
        ACTION_TO_ID[_action_str] = _action_id

ACTION_LIST.append("draw")
ACTION_TO_ID["draw"] = 60


# ---------------------------------------------------------------------------
# Public conversion functions
# ---------------------------------------------------------------------------

def action_id_to_card(action_id: int) -> tuple[Optional[Card], Optional[str]]:
    """Convert RLCard action ID to (Card, chosen_color).

    Returns (None, None) for draw action (ID 60).
    For wild cards, the color in the action IS the chosen color.
    """
    if action_id == 60:
        return None, None

    action_str = ACTION_LIST[action_id]
    rl_color, rl_trait = action_str.split("-", 1)

    api_value = RL_TO_VALUE.get(rl_trait, rl_trait)
    api_color = RL_TO_COLOR.get(rl_color)
    chosen_color = None

    if rl_trait in ("wild", "wild_draw_4"):
        chosen_color = api_color   # The color in the action = chosen color
        api_color = None           # Wild cards have null suit

    return Card(suit=api_color, value=api_value), chosen_color


def card_to_action_id(card: Card, chosen_color: Optional[str] = None) -> int:
    """Convert a card play to RLCard action ID.

    For wild cards, *chosen_color* determines the action ID.
    """
    rl_value = VALUE_TO_RL.get(card.value, card.value)

    if is_wild(card):
        if not chosen_color:
            chosen_color = "red"
        rl_color = COLOR_TO_RL.get(chosen_color, "r")
    else:
        rl_color = COLOR_TO_RL.get(card.suit, "r")

    action_str = f"{rl_color}-{rl_value}"
    return ACTION_TO_ID.get(action_str, 60)


def encode_game_state(
    hand: list[Card],
    top_card: Card,
    active_color: str,
    *,
    bot_seat: int = 1,
    hand_sizes: list[int] = None,
    direction: int = 1,
    played_cards: list = None,
    deck_remaining: int = 50,
    action_log: list = None,
    target_seat: int = None,
    allow_voluntary_draw: bool = True,
) -> dict:
    """Build RLCard-compatible enriched state dict for querying trained agents.

    Returns dict with 'obs' (numpy matching STATE_SHAPE) and 'legal_actions'.

    Args:
        hand: Bot's hand cards.
        top_card: Current top card on discard pile.
        active_color: Current active color.
        bot_seat: This bot's seat index (for seat identity plane).
        hand_sizes: List of hand sizes per player [p0, p1, p2, p3].
        direction: Play direction (+1 clockwise, -1 counter-clockwise).
        played_cards: List of RLCard UnoCard objects from discard pile.
        deck_remaining: Cards remaining in draw pile.
        action_log: List of (player_id, action_str) tuples from session.
        target_seat: Which seat this agent should help win (None = no target).
        allow_voluntary_draw: If True, draw (action 60) is always legal.
            If False, draw is only legal when no playable cards exist.
    """
    from engine.config.game import STATE_SHAPE, NUM_PLAYERS
    obs = np.zeros(STATE_SHAPE, dtype=int)

    # Plane 0-2: encode hand (matches RLCard's encode_hand exactly)
    # Plane 0 = absence plane: starts all-ones, zeroed where cards exist
    # Plane 1 = 1 copy present (or wild present, any count)
    # Plane 2 = 2+ copies present
    obs[0] = np.ones((4, 15), dtype=int)

    card_counts: dict[str, int] = {}
    for card in hand:
        key = _card_to_rl_key(card)
        if key:
            card_counts[key] = card_counts.get(key, 0) + 1

    wild_encoded: set[int] = set()
    for key, count in card_counts.items():
        rl_color, rl_trait = key.split("-", 1)
        ci = COLORS.index(rl_color)
        ti = TRAITS.index(rl_trait)

        if rl_trait in ("wild", "wild_draw_4"):
            # RLCard encodes wilds once on plane 1 for all 4 colors,
            # regardless of count (guarded by plane[1][0][trait]==0)
            if ti not in wild_encoded:
                wild_encoded.add(ti)
                for c in range(4):
                    obs[0][c][ti] = 0
                    obs[1][c][ti] = 1
        else:
            obs[0][ci][ti] = 0
            obs[min(count, 2)][ci][ti] = 1

    # Plane 3: encode target (top card)
    target_key = _card_to_rl_key(top_card, active_color)
    if target_key:
        rl_color, rl_trait = target_key.split("-", 1)
        ci = COLORS.index(rl_color)
        ti = TRAITS.index(rl_trait)
        obs[3][ci][ti] = 1

    # Plane 4: Seat identity
    obs[4, bot_seat % 4, :] = 1

    # Plane 5: Card counts per player
    if hand_sizes is None:
        hand_sizes = [7] * NUM_PLAYERS
    for i, count in enumerate(hand_sizes):
        normalized = min(count / 15.0, 1.0)
        obs[5, i % 4, :] = int(round(normalized * 14))

    # Plane 6: Next player + direction
    next_player = (bot_seat + direction) % NUM_PLAYERS
    obs[6, next_player % 4, :] = 1
    if direction == -1:
        obs[6, :, 14] = 1

    # Plane 7: Discard pile card counting
    COLOR_MAP = {'r': 0, 'g': 1, 'b': 2, 'y': 3}
    TRAIT_MAP = {
        '0': 0, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6,
        '7': 7, '8': 8, '9': 9, 'skip': 10, 'reverse': 11,
        'draw_2': 12, 'wild': 13, 'wild_draw_4': 14,
    }
    MAX_COPIES = {
        0: 1, 1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 2, 9: 2,
        10: 2, 11: 2, 12: 2, 13: 1, 14: 1,
    }
    if played_cards:
        discard_counts = {}
        for rl_card in played_cards:
            c = COLOR_MAP.get(rl_card.color)
            t = TRAIT_MAP.get(rl_card.trait)
            if c is not None and t is not None:
                key = (c, t)
                discard_counts[key] = discard_counts.get(key, 0) + 1
        for (c, t), count in discard_counts.items():
            max_c = MAX_COPIES.get(t, 2)
            obs[7, c, t] = min(count, max_c)

    # Plane 8: Last card played per player
    # Plane 9: Draw vulnerability per target color
    # Both reconstructed from action_log (mirrors env.action_recorder)
    if action_log:
        # Plane 8: last non-draw action per player
        last_action_per_player = {}
        for pid, action_str in action_log:
            if isinstance(action_str, str) and action_str != 'draw':
                last_action_per_player[pid] = action_str
        for pid, card_str in last_action_per_player.items():
            parts = card_str.split('-')
            if len(parts) == 2:
                color, trait = parts
                c = COLOR_MAP.get(color)
                t = TRAIT_MAP.get(trait)
                if c is not None and t is not None:
                    obs[8, pid % 4, t] = c + 1  # 1=r, 2=g, 3=b, 4=y

        # Plane 9: draw counts per target color, reset on play
        COLOR_IDX = {'r': 0, 'g': 1, 'b': 2, 'y': 3}
        draw_per_color = {i: [0, 0, 0, 0] for i in range(NUM_PLAYERS)}
        # Start tracking color from first discard card
        tracking_color = None
        if played_cards:
            tracking_color = played_cards[0].color if hasattr(played_cards[0], 'color') else None
        for pid, action_str in action_log:
            if action_str == 'draw':
                if tracking_color and tracking_color in COLOR_IDX:
                    draw_per_color[pid][COLOR_IDX[tracking_color]] += 1
            else:
                draw_per_color[pid] = [0, 0, 0, 0]
                parts = action_str.split('-')
                if len(parts) == 2:
                    tracking_color = parts[0]
        for pid in range(NUM_PLAYERS):
            for ci in range(4):
                obs[9, pid % 4, ci] = min(draw_per_color[pid][ci], 14)

    # Plane 10: Deck size
    deck_val = min(int(deck_remaining / 8), 14)
    obs[10, :, :] = deck_val

    # Plane 11: Target seat (which seat to help win)
    if target_seat is not None:
        obs[11, target_seat % 4, :] = 1

    # Legal actions
    legal = get_legal_action_ids(hand, top_card, active_color)
    if allow_voluntary_draw and 60 not in legal:
        legal[60] = None  # Voluntary draw available (human player / VD-enabled bots)

    return {
        "obs": obs,
        "legal_actions": legal,
        "raw_obs": {},
        "raw_legal_actions": list(legal.keys()),
    }


def get_legal_action_ids(hand: list[Card], top_card: Card,
                         active_color: str) -> OrderedDict:
    """Compute legal RLCard action IDs from game state."""
    legal: OrderedDict[int, None] = OrderedDict()
    has_playable = False

    for card in hand:
        if is_valid_play(card, top_card, active_color):
            has_playable = True
            if is_wild(card):
                # Wild cards generate 4 actions (one per color choice)
                for color in ["red", "green", "blue", "yellow"]:
                    aid = card_to_action_id(card, color)
                    legal[aid] = None
            else:
                aid = card_to_action_id(card)
                legal[aid] = None

    if not has_playable:
        legal[60] = None  # Draw

    return legal


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _card_to_rl_key(card: Card, active_color: str = None) -> Optional[str]:
    """Convert card to RLCard key string like 'r-0', 'g-skip', etc."""
    rl_value = VALUE_TO_RL.get(card.value, card.value)

    if is_wild(card):
        color = active_color or (card.suit if card.suit else "red")
        rl_color = COLOR_TO_RL.get(color, "r")
    else:
        if card.suit is None:
            return None
        rl_color = COLOR_TO_RL.get(card.suit)
        if not rl_color:
            return None

    return f"{rl_color}-{rl_value}"


# ---------------------------------------------------------------------------
# RLCard UnoCard <-> API Card conversion
# ---------------------------------------------------------------------------

def rlcard_card_to_api(rlcard_card: UnoCard) -> Card:
    """Convert RLCard UnoCard to API Card.

    RLCard UnoCard has .type ('number','action','wild'), .color ('r','g','b','y'),
    .trait ('0'-'9','skip','reverse','draw_2','wild','wild_draw_4').
    """
    api_value = RL_TO_VALUE.get(rlcard_card.trait, rlcard_card.trait)
    if rlcard_card.type == 'wild':
        return Card(suit=None, value=api_value)
    else:
        api_color = RL_TO_COLOR.get(rlcard_card.color)
        return Card(suit=api_color, value=api_value)


def api_card_to_rlcard(card: Card, active_color: Optional[str] = None) -> UnoCard:
    """Convert API Card to RLCard UnoCard.

    For wild cards, active_color sets the .color attribute on the UnoCard.
    """
    rl_trait = VALUE_TO_RL.get(card.value, card.value)

    if card.value in ("wild", "plus4"):
        rl_color = COLOR_TO_RL.get(active_color, "r") if active_color else "r"
        return UnoCard("wild", rl_color, rl_trait)
    else:
        rl_color = COLOR_TO_RL.get(card.suit, "r")
        card_type = "number" if card.value.isdigit() else "action"
        return UnoCard(card_type, rl_color, rl_trait)
