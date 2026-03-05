"""Manages AI bot agents using the tier-based adaptive difficulty system.

Replaces the legacy AdaptiveAgent coin-flip system with discrete agent tiers
loaded via TierModelPool. The AdaptiveTierController selects which tier to
use based on the player's cumulative win rate vs their target.
"""

import os
import json
import random
from typing import Optional

from .cards import Card, get_playable_cards, is_wild, pick_best_color
from .rlcard_bridge import encode_game_state, action_id_to_card

from engine.game_logic.tiers import TierModelPool, AdaptiveTierController
from engine.game_logic.tiers.tier_config import (
    TIER_ORDER, TIER_NAMES, TARGET_SEAT_TIERS, FIXED_TARGET,
    VOLUNTARY_DRAW_POLICY,
)


class BotManager:
    """Manages AI bot agents using tier-based difficulty."""

    def __init__(self, model_dir: str):
        """Initialize the bot manager.

        Args:
            model_dir: Path to model directory containing tier subdirectories.
        """
        self.model_dir = model_dir
        self._pool = TierModelPool(
            tiers_to_load=list(TIER_ORDER),
            model_dir=model_dir,
        )
        self._manifest = self._load_manifest()

    def select_tier(
        self,
        win_rate: float,
        games_played: int,
        bot_mode: str = "adaptive",
        target_win_rate: float = 0.25,
    ) -> str:
        """Determine which tier to use for the next game.

        Args:
            win_rate: Player's accumulated win rate (wins / games_played).
            games_played: Total games played by the player.
            bot_mode: "adaptive" uses the controller; any tier name forces that tier.
            target_win_rate: The player's desired win rate target.

        Returns:
            Tier name string.
        """
        if bot_mode != "adaptive" and bot_mode in TIER_NAMES:
            return bot_mode

        controller = AdaptiveTierController(target_win_rate=target_win_rate)
        return controller.select_tier(win_rate, games_played)

    def get_target_seat_for_tier(self, tier: str) -> Optional[int]:
        """Get the target seat for a tier (for plane 11).

        Uses FIXED_TARGET values matching training:
          - altruistic/hyper_altruistic: help seat 0 (trained that way)
          - hyper_adversarial: cooperate with seat 2 (trained with
            selfish star at seat 2, so hadv hurts seat 0 indirectly)

        Returns:
            Fixed target seat, or None for tiers that don't need one.
        """
        if tier in FIXED_TARGET:
            return FIXED_TARGET[tier]
        if tier in TARGET_SEAT_TIERS:
            return 0
        return None

    def get_voluntary_draw_cap(self, tier: str) -> int:
        """Get the voluntary draw cap for a tier."""
        return VOLUNTARY_DRAW_POLICY.get(tier, 0)

    def get_bot_decision(
        self,
        tier: str,
        hand: list[Card],
        top_card: Card,
        active_color: str,
        **context,
    ) -> tuple[Optional[Card], Optional[str]]:
        """Query an agent for a bot decision.

        Args:
            tier: Which tier agent to use.
            hand: Bot's hand cards.
            top_card: Current top card.
            active_color: Current active color.
            **context: Extra game context forwarded to encode_game_state
                (bot_seat, hand_sizes, direction, played_cards, deck_remaining).

        Returns (card_to_play, chosen_color) or (None, None) for draw.
        """
        agent = self._pool.get(tier)

        target_seat = self.get_target_seat_for_tier(tier)
        context.setdefault('target_seat', target_seat)

        # Voluntary draw: allow if tier policy permits and cap not reached
        vd_cap = self.get_voluntary_draw_cap(tier)
        vd_count = context.pop('voluntary_draw_count', 0)
        context.setdefault('allow_voluntary_draw', vd_cap > 0 and vd_count < vd_cap)

        state = encode_game_state(hand, top_card, active_color, **context)
        action_id, _ = agent.eval_step(state)
        card, chosen_color = action_id_to_card(action_id)

        if card is None:
            return None, None

        # Verify the card is actually in the bot's hand
        match = next(
            (c for c in hand if c.suit == card.suit and c.value == card.value),
            None,
        )
        if match is None:
            # Fallback: pick a random playable card or draw
            playable = get_playable_cards(hand, top_card, active_color)
            if playable:
                card = random.choice(playable)
                if is_wild(card):
                    chosen_color = pick_best_color(hand)
                else:
                    chosen_color = None
                return card, chosen_color
            return None, None

        return card, chosen_color

    def make_random_decision(
        self, hand: list[Card], top_card: Card, active_color: str
    ) -> tuple[Optional[Card], Optional[str]]:
        """Fallback: random bot decision when models are not available."""
        playable = get_playable_cards(hand, top_card, active_color)
        if not playable:
            return None, None
        card = random.choice(playable)
        chosen_color = pick_best_color(hand) if is_wild(card) else None
        return card, chosen_color

    def get_manifest(self) -> dict:
        """Return model version info from manifest.json."""
        return self._manifest

    def _load_manifest(self) -> dict:
        """Load the model manifest file, or return a default dict."""
        manifest_path = os.path.join(self.model_dir, "manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path) as f:
                return json.load(f)
        return {
            "version": "unknown",
            "trained_at": None,
            "notes": "No manifest found",
        }
