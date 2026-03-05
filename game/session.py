"""GameSession: RLCard-backed game session for API live play.

Wraps engine.UnoGame (RLCard) and translates between RLCard's internal
state and human-readable Card objects for client communication.

RLCard's draw action auto-plays matching drawn cards (non-standard UNO).
For human pass_turn(), we bypass this and manually draw to hand.
"""

import random
from typing import Optional, Callable

from rlcard.games.uno.card import UnoCard

from engine.game_logic.game import UnoGame
from engine.config.game import NUM_PLAYERS, PLAYER_SEAT

from .cards import Card, is_wild, is_valid_play, pick_best_color, get_playable_cards
from .rlcard_bridge import (
    card_to_action_id, action_id_to_card,
    rlcard_card_to_api, api_card_to_rlcard,
    ACTION_LIST, ACTION_TO_ID,
    RL_TO_COLOR, COLOR_TO_RL,
)


class GameSession:
    """RLCard-backed UNO game session for live API play.

    Manages the RLCard environment internally and exposes a Card-based
    interface matching what the API service layer expects.
    """

    def __init__(self):
        self._uno = UnoGame(seed=None)
        self.num_players = NUM_PLAYERS
        self.current_player = 0
        self.turns = 0
        self._action_log = []  # [(player_id, action_str), ...] mirrors env.action_recorder
        self._voluntary_draw_counts = {}  # {player_index: count} per game

    # ------------------------------------------------------------------
    # Game lifecycle
    # ------------------------------------------------------------------

    def start_game(self) -> dict:
        """Start a new game via RLCard env.reset().

        Returns dict with player_hands, starter_card, active_color, etc.
        """
        # Reset the RLCard environment (deals cards, flips starter)
        _state, player_id = self._uno.env.reset()
        self.current_player = player_id

        game = self._game
        return {
            "player_hands": self._external_hands(),
            "starter_card": rlcard_card_to_api(game.round.target).to_dict(),
            "active_color": self._get_active_color(),
            "deck_remaining": len(game.dealer.deck),
            "deck_total": self._total_card_count(),
        }

    def play_card(self, player_index: int, card_data: dict,
                  chosen_color: Optional[str] = None) -> dict:
        """Human plays a card. Translates to RLCard action and steps.

        Args:
            player_index: Which player (0 for human).
            card_data: {"suit": str|None, "value": str}.
            chosen_color: Color choice for wild cards.

        Returns dict with valid, top_card, active_color, etc.
        """
        card = Card.from_dict(card_data)
        action_id = card_to_action_id(card, chosen_color)

        # Check if legal
        game = self._game
        legal_actions = game.get_legal_actions()
        action_str = ACTION_LIST[action_id]
        if action_str not in legal_actions:
            return {"valid": False}

        # Snapshot hand sizes before stepping (for penalty detection)
        hand_sizes_before = [len(p.hand) for p in game.players]

        # Step the RLCard game
        game.step(action_str)
        self._action_log.append((player_index, action_str))
        self.turns += 1

        # Check for winner
        if game.round.is_over:
            winner = game.round.winner[0] if game.round.winner else None
            return {
                "valid": True,
                "winner": winner,
                "top_card": rlcard_card_to_api(game.round.target).to_dict(),
                "active_color": self._get_active_color(),
                "is_clockwise": game.round.direction == 1,
                "deck_remaining": len(game.dealer.deck),
            }

        # Detect penalty draws (plus2/plus4 effects)
        penalty_draw = self._detect_penalty(hand_sizes_before, card)

        self.current_player = game.round.current_player

        # Advance: determine next player after card effects
        next_player = game.round.current_player

        return {
            "valid": True,
            "winner": None,
            "top_card": rlcard_card_to_api(game.round.target).to_dict(),
            "active_color": self._get_active_color(),
            "is_clockwise": game.round.direction == 1,
            "deck_remaining": len(game.dealer.deck),
            "next_player": next_player,
            "penalty_draw": penalty_draw,
        }

    def pass_turn(self, player_index: int) -> dict:
        """Human draws a card. If the drawn card is playable, auto-plays it.

        Bypasses RLCard's draw action to give the human the card first.
        If the card matches the top card / active color, it is played
        automatically (matching UNO rules and bot behaviour).
        """
        game = self._game

        # Reshuffle if deck empty
        if not game.dealer.deck:
            game.round.replace_deck()

        if not game.dealer.deck:
            # No cards left at all — just advance
            self._action_log.append((player_index, "draw"))
            self.turns += 1
            self.current_player = self._next_player(player_index)
            game.round.current_player = self.current_player
            return {
                "drawn_card": None, "auto_played": False,
                "chosen_color": None, "penalty_draw": None,
                "winner": None, "next_player": self.current_player,
                "deck_remaining": 0,
            }

        rl_card = game.dealer.deck.pop()
        drawn_card = rlcard_card_to_api(rl_card)

        # Check if drawn card can be played
        top_card = rlcard_card_to_api(game.round.target)
        active_color = self._get_active_color()

        if is_valid_play(drawn_card, top_card, active_color):
            # Auto-play: add to hand so game.step() can find it
            game.players[player_index].hand.append(rl_card)

            chosen_color = None
            if is_wild(drawn_card):
                hand_cards = [rlcard_card_to_api(c)
                              for c in game.players[player_index].hand]
                chosen_color = pick_best_color(hand_cards)

            action_id = card_to_action_id(drawn_card, chosen_color)
            action_str = ACTION_LIST[action_id]

            hand_sizes_before = [len(p.hand) for p in game.players]
            game.step(action_str)
            # Record as "draw" to match training (auto-play is invisible to action_recorder)
            self._action_log.append((player_index, "draw"))
            self.turns += 1

            penalty_draw = self._detect_penalty(
                hand_sizes_before, drawn_card)

            winner = None
            if game.round.is_over:
                winner = (game.round.winner[0]
                          if game.round.winner else None)

            self.current_player = game.round.current_player

            return {
                "drawn_card": drawn_card.to_dict(),
                "auto_played": True,
                "chosen_color": chosen_color,
                "penalty_draw": penalty_draw,
                "winner": winner,
                "next_player": self.current_player,
                "deck_remaining": len(game.dealer.deck),
            }
        else:
            # Not playable — keep in hand and advance turn
            game.players[player_index].hand.append(rl_card)
            self._action_log.append((player_index, "draw"))
            self.turns += 1

            self.current_player = self._next_player(player_index)
            game.round.current_player = self.current_player

            return {
                "drawn_card": drawn_card.to_dict(),
                "auto_played": False,
                "chosen_color": None,
                "penalty_draw": None,
                "winner": None,
                "next_player": self.current_player,
                "deck_remaining": len(game.dealer.deck),
            }

    def run_bot_turns(self, bot_decision_fn: Callable) -> list[dict]:
        """Run all bot turns until it's the human's turn or game ends.

        Args:
            bot_decision_fn: Callable(player_index, hand, top_card, active_color)
                -> (card_to_play: Card|None, chosen_color: str|None)
                If card_to_play is None, bot draws.

        Returns list of bot turn dicts for client animation.
        """
        bot_turns = []
        game = self._game
        safety = 0

        while self.current_player != 0 and not game.round.is_over and safety < 30:
            safety += 1
            p = self.current_player
            hand = game.players[p].hand

            if not hand:
                self.current_player = self._next_player(p)
                game.round.current_player = self.current_player
                continue

            # Get bot decision via callback (with enriched game context)
            hand_cards = [rlcard_card_to_api(c) for c in hand]
            top_card = rlcard_card_to_api(game.round.target)
            active_color = self._get_active_color()

            context = {
                "bot_seat": p,
                "hand_sizes": [len(pl.hand) for pl in game.players],
                "direction": game.round.direction,
                "played_cards": game.round.played_cards,
                "deck_remaining": len(game.dealer.deck),
                "action_log": self._action_log,
                "voluntary_draw_count": self._voluntary_draw_counts.get(p, 0),
            }

            card, chosen_color = bot_decision_fn(
                p, hand_cards, top_card, active_color, **context)

            if card is not None:
                # Bot plays a card — step RLCard
                action_id = card_to_action_id(card, chosen_color)
                action_str = ACTION_LIST[action_id]

                # Snapshot for penalty detection
                hand_sizes_before = [len(pl.hand) for pl in game.players]

                game.step(action_str)
                self._action_log.append((p, action_str))
                self.turns += 1

                bot_turns.append({
                    "playerIndex": p,
                    "action": "play",
                    "card": card.to_dict(),
                    "drawnCards": 0,
                    "chosenColor": chosen_color,
                })

                # Detect penalty draws from card effects
                penalty = self._detect_penalty(hand_sizes_before, card)
                if penalty:
                    target_idx = penalty["playerIndex"]
                    drawn_count = penalty["drawnCards"]
                    if isinstance(drawn_count, list):
                        drawn_display = drawn_count if target_idx == 0 else len(drawn_count)
                    else:
                        drawn_display = drawn_count
                    bot_turns.append({
                        "playerIndex": target_idx,
                        "action": "draw",
                        "card": None,
                        "drawnCards": drawn_display,
                        "chosenColor": None,
                    })

                # Check if bot won
                if game.round.is_over:
                    self.current_player = game.round.current_player
                    return bot_turns

                self.current_player = game.round.current_player

            else:
                # Track voluntary draw (had playable cards but chose draw)
                playable = get_playable_cards(hand_cards, top_card, active_color)
                if playable:
                    self._voluntary_draw_counts[p] = (
                        self._voluntary_draw_counts.get(p, 0) + 1
                    )

                # Bot draws — RLCard may auto-play the drawn card if it
                # matches the target color or is wild.  We detect this by
                # comparing the target object before / after the step.
                target_before = game.round.target
                hand_sizes_before = [len(pl.hand) for pl in game.players]

                game.step("draw")
                self._action_log.append((p, "draw"))
                self.turns += 1

                target_after = game.round.target

                if target_after is not target_before:
                    # Auto-play happened: drawn card went straight to
                    # discard pile and became the new target.
                    # Report as draw + play so the frontend's visual
                    # hand count stays correct (draw +1, play -1 = net 0).
                    auto_card = rlcard_card_to_api(target_after)
                    auto_chosen = None
                    if is_wild(auto_card):
                        auto_chosen = RL_TO_COLOR.get(
                            target_after.color, "red")

                    bot_turns.append({
                        "playerIndex": p,
                        "action": "draw",
                        "card": None,
                        "drawnCards": 1,
                        "chosenColor": None,
                    })
                    bot_turns.append({
                        "playerIndex": p,
                        "action": "play",
                        "card": auto_card.to_dict(),
                        "drawnCards": 0,
                        "chosenColor": auto_chosen,
                    })

                    # Detect penalty draws (plus2/plus4 effects)
                    penalty = self._detect_penalty(
                        hand_sizes_before, auto_card)
                    if penalty:
                        target_idx = penalty["playerIndex"]
                        drawn_count = penalty["drawnCards"]
                        if isinstance(drawn_count, list):
                            drawn_display = (drawn_count
                                             if target_idx == 0
                                             else len(drawn_count))
                        else:
                            drawn_display = drawn_count
                        bot_turns.append({
                            "playerIndex": target_idx,
                            "action": "draw",
                            "card": None,
                            "drawnCards": drawn_display,
                            "chosenColor": None,
                        })
                else:
                    # Card kept in hand (didn't match target)
                    bot_turns.append({
                        "playerIndex": p,
                        "action": "draw",
                        "card": None,
                        "drawnCards": 1,
                        "chosenColor": None,
                    })

                if game.round.is_over:
                    self.current_player = game.round.current_player
                    return bot_turns

                self.current_player = game.round.current_player

        return bot_turns

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def get_winner(self) -> Optional[int]:
        """Check if any player has an empty hand."""
        for i, player in enumerate(self._game.players):
            if len(player.hand) == 0:
                return i
        return None

    def get_state_for_player(self, player_index: int = 0) -> dict:
        """Build game state visible to a specific player."""
        game = self._game
        return {
            "playerHands": self._external_hands(),
            "topCard": rlcard_card_to_api(game.round.target).to_dict(),
            "discardPile": [rlcard_card_to_api(c).to_dict()
                           for c in game.round.played_cards],
            "activeColor": self._get_active_color(),
            "isClockwise": game.round.direction == 1,
            "deckRemaining": len(game.dealer.deck),
            "winner": self.get_winner(),
        }

    # ------------------------------------------------------------------
    # Serialization (for DB persistence)
    # ------------------------------------------------------------------

    def serialize(self) -> dict:
        """Serialize game state to a dict of human-readable Card data."""
        game = self._game
        return {
            "hands": [
                [rlcard_card_to_api(c).to_dict() for c in p.hand]
                for p in game.players
            ],
            "deck": [rlcard_card_to_api(c).to_dict() for c in game.dealer.deck],
            "discard_pile": [rlcard_card_to_api(c).to_dict()
                            for c in game.round.played_cards],
            "top_card": rlcard_card_to_api(game.round.target).to_dict(),
            "active_color": self._get_active_color(),
            "is_clockwise": game.round.direction == 1,
            "current_player": self.current_player,
            "turns": self.turns,
            "action_log": self._action_log,
            "voluntary_draw_counts": self._voluntary_draw_counts,
        }

    @classmethod
    def deserialize(cls, data: dict) -> "GameSession":
        """Reconstruct a GameSession from serialized data.

        Creates a fresh RLCard env, then manually sets internal state
        to match the serialized data.
        """
        session = cls()

        # Reset env to get valid internal objects
        session._uno.env.reset()
        game = session._game

        # Reconstruct hands
        for i, hand_data in enumerate(data["hands"]):
            game.players[i].hand = [
                api_card_to_rlcard(Card.from_dict(c), data.get("active_color"))
                for c in hand_data
            ]

        # Reconstruct deck
        game.dealer.deck = [
            api_card_to_rlcard(Card.from_dict(c))
            for c in data["deck"]
        ]

        # Reconstruct discard pile
        game.round.played_cards = [
            api_card_to_rlcard(Card.from_dict(c), data.get("active_color"))
            for c in data["discard_pile"]
        ]

        # Reconstruct target (top card)
        if data["top_card"]:
            top = Card.from_dict(data["top_card"])
            active = data.get("active_color")
            game.round.target = api_card_to_rlcard(top, active)

        # Reconstruct game state
        game.round.direction = 1 if data.get("is_clockwise", True) else -1
        game.round.current_player = data.get("current_player", 0)
        game.round.is_over = False
        game.round.winner = None

        session.current_player = data.get("current_player", 0)
        session.turns = data.get("turns", 0)
        session._action_log = [
            tuple(rec) for rec in data.get("action_log", [])
        ]
        # Restore voluntary draw counts (keys stored as strings in JSON)
        raw_vdc = data.get("voluntary_draw_counts", {})
        session._voluntary_draw_counts = {
            int(k): v for k, v in raw_vdc.items()
        }

        return session

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @property
    def _game(self):
        """Access the RLCard UnoGame object."""
        return self._uno.env.game

    def _get_active_color(self) -> str:
        """Get the current active color from the RLCard target card."""
        target = self._game.round.target
        return RL_TO_COLOR.get(target.color, "red")

    def _external_hands(self) -> list:
        """Human (index 0) sees full card arrays; bots see counts."""
        result = []
        for i, player in enumerate(self._game.players):
            if i == 0:
                result.append([rlcard_card_to_api(c).to_dict() for c in player.hand])
            else:
                result.append(len(player.hand))
        return result

    def _total_card_count(self) -> int:
        """Total cards across all hands, deck, and discard pile."""
        game = self._game
        hand_cards = sum(len(p.hand) for p in game.players)
        return len(game.dealer.deck) + len(game.round.played_cards) + hand_cards

    def _next_player(self, current: int) -> int:
        """Get next player index based on current direction."""
        return (current + self._game.round.direction) % self.num_players

    def _detect_penalty(self, hand_sizes_before: list, card_played: Card) -> Optional[dict]:
        """Detect if a card play caused a penalty draw (plus2/plus4).

        Compares hand sizes before and after the step to detect draws.
        """
        if card_played.value not in ("plus2", "plus4"):
            return None

        game = self._game
        draw_count = 2 if card_played.value == "plus2" else 4

        # Find who got penalized (hand grew)
        for i, player in enumerate(game.players):
            current_size = len(player.hand)
            before_size = hand_sizes_before[i]
            if current_size > before_size:
                # This player drew cards
                drawn_cards = current_size - before_size
                if i == 0:
                    # Human: show actual cards (last N added to hand)
                    new_cards = [rlcard_card_to_api(c).to_dict()
                                for c in player.hand[-drawn_cards:]]
                    return {"playerIndex": i, "drawnCards": new_cards}
                else:
                    return {"playerIndex": i, "drawnCards": drawn_cards}

        return None
