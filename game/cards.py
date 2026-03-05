"""Card model and Deck class for a standard 108-card UNO game.

Provides the core Card dataclass, a Deck that builds and shuffles the standard
108-card set, and helper functions for card validation and gameplay logic.
"""

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Card:
    """A single UNO card.

    Attributes:
        suit: Card color ("red", "green", "blue", "yellow") or None for wilds.
        value: Card face value ("0"-"9", "block", "reverse", "plus2", "wild", "plus4").
    """

    suit: Optional[str]  # "red", "green", "blue", "yellow", or None for wilds
    value: str           # "0"-"9", "block", "reverse", "plus2", "wild", "plus4"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(suit=d.get("suit"), value=d["value"])


class Deck:
    """A standard 108-card UNO deck with draw and shuffle operations."""

    def __init__(self):
        self.cards: list[Card] = []
        self.build()
        self.shuffle()

    def build(self):
        """Build standard 108-card UNO deck."""
        self.cards = []
        colors = ["red", "green", "blue", "yellow"]
        for color in colors:
            # One 0 per color
            self.cards.append(Card(suit=color, value="0"))
            # Two each of 1-9, block, reverse, plus2
            for value in ["1", "2", "3", "4", "5", "6", "7", "8", "9",
                          "block", "reverse", "plus2"]:
                self.cards.append(Card(suit=color, value=value))
                self.cards.append(Card(suit=color, value=value))
        # 4 wilds and 4 plus4s (suit=None)
        for _ in range(4):
            self.cards.append(Card(suit=None, value="wild"))
            self.cards.append(Card(suit=None, value="plus4"))

    def shuffle(self):
        """Shuffle the deck in place."""
        import random
        random.shuffle(self.cards)

    def draw(self) -> Optional[Card]:
        """Draw (pop) the top card from the deck, or None if empty."""
        if not self.cards:
            return None
        return self.cards.pop()

    def remaining(self) -> int:
        """Return the number of cards left in the deck."""
        return len(self.cards)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def is_wild(card: Card) -> bool:
    """Return True if the card is a wild or plus4."""
    return card.value in ("wild", "plus4")


def is_action_card(card: Card) -> bool:
    """Return True if the card is an action card (block, reverse, plus2, plus4)."""
    return card.value in ("block", "reverse", "plus2", "plus4")


def is_valid_play(card: Card, top_card: Card, active_color: str) -> bool:
    """Check if *card* can be played on *top_card* with the given *active_color*."""
    if is_wild(card):
        return True
    if card.suit == active_color:
        return True
    if card.value == top_card.value:
        return True
    return False


def get_playable_cards(hand: list[Card], top_card: Card, active_color: str) -> list[Card]:
    """Return the subset of *hand* that can legally be played."""
    return [c for c in hand if is_valid_play(c, top_card, active_color)]


def resolve_active_color(card: Card, chosen_color: Optional[str]) -> str:
    """Determine the active color after *card* is played.

    For wild cards the caller must supply *chosen_color*.
    """
    if is_wild(card) and chosen_color:
        return chosen_color
    return card.suit


def resolve_direction(is_clockwise: bool, card: Card) -> bool:
    """Return the new direction after *card* is played."""
    if card.value == "reverse":
        return not is_clockwise
    return is_clockwise


def get_next_player(current: int, is_clockwise: bool, num_players: int = 4) -> int:
    """Return the index of the next player in turn order."""
    if is_clockwise:
        return (current + 1) % num_players
    return (current - 1) % num_players


def pick_best_color(hand: list[Card]) -> str:
    """Pick the most common color in *hand* (for bot wild-card choices)."""
    counts = {"red": 0, "green": 0, "blue": 0, "yellow": 0}
    for card in hand:
        if card.suit and card.suit in counts:
            counts[card.suit] += 1
    return max(counts, key=counts.get) if any(counts.values()) else "red"
