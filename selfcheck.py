"""Headless self-check for Dark Souls: Card Chronicles.

Runs core game logic (data loading, dice maths, deck flow, player combat)
without touching pygame or any GUI module. Exit code 0 = all checks passed.

Usage: python selfcheck.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from models.card import Card
from models.deck_manager import DeckManager
from models.dice_engine import DiceEngine
from models.player import Player
from services.data_loader import DataLoader


class Checker:
    """Collects failures so every check runs before exiting."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed: list[str] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed.append(label)
            print(f"  ok   {label}")
        else:
            message = f"{label}: {detail}" if detail else label
            self.failures.append(message)
            print(f"  FAIL {message}")

    def section(self, title: str) -> None:
        print(f"[{title}]")


def check_data_loading(c: Checker) -> None:
    """JSON data must parse and build real objects."""
    c.section("data loading")
    loader = DataLoader(ROOT)

    cards = loader.load_cards()
    c.check("cards.json loads 100+ cards", len(cards) >= 100, f"got {len(cards)}")

    required = {"id", "name", "cost", "type", "source"}
    broken = [d.get("id") for d in cards if not required.issubset(d)]
    c.check("every card has required fields", not broken, f"bad ids: {broken[:5]}")

    card = loader.create_card("C001")
    c.check(
        "create_card('C001') builds an Attack card",
        card is not None and card.is_attack() and card.cost == 1 and card.damage == "2d6+STR",
        f"got {card!r}",
    )

    enemy = loader.create_enemy(loader.load_enemies()[0]["id"])
    c.check(
        "create_enemy keeps hp consistent",
        enemy is not None and enemy.hp == enemy.max_hp and enemy.hp > 0,
        f"got {enemy!r}",
    )

    relics = loader.load_relics()
    c.check("relics.json loads relics", len(relics) > 0, f"got {len(relics)}")
    c.check("acts.json covers act 1", loader.get_act_data(1) is not None)

    missing = [i for i in ("C007", "C006") if loader.create_card(i) is None]
    c.check("starting-deck card ids exist", not missing, f"missing: {missing}")


def check_dice_math(c: Checker) -> None:
    """Dice notation must resolve modifiers and crits deterministically."""
    c.section("dice engine")

    fixed = DiceEngine.parse_dice_notation("3d1")
    c.check(
        "3d1 rolls three fixed dice",
        fixed.total == 3 and fixed.rolls == [1, 1, 1],
        f"got {fixed!r}",
    )

    flat = DiceEngine.parse_dice_notation("2d6+3")
    c.check(
        "2d6+3 stays within 5..15",
        flat.modifier == 3 and 5 <= flat.total <= 15 and len(flat.rolls) == 2,
        f"got {flat!r}",
    )

    stat = DiceEngine.parse_dice_notation("1d6+STR", str_val=4)
    c.check(
        "1d6+STR applies the STR stat",
        stat.modifier == 4 and 5 <= stat.total <= 10,
        f"got {stat!r}",
    )

    empty = DiceEngine.parse_dice_notation("0")
    c.check("'0' deals no damage", empty.total == 0 and empty.rolls == [], f"got {empty!r}")

    crit = DiceEngine.roll_attack_damage("2d6", is_crit=True)
    normal = DiceEngine.roll_attack_damage("2d6", is_crit=False)
    c.check(
        "crit doubles the damage dice",
        len(crit.rolls) == 4 and len(normal.rolls) == 2,
        f"crit={len(crit.rolls)} normal={len(normal.rolls)}",
    )

    # A d20 can only land in 1..20, so roll a sample and verify the flags/env.
    rolls = [DiceEngine.roll_d20(2) for _ in range(200)]
    c.check(
        "d20 totals stay in 3..22 with crit/mishap only on 20/1",
        all(3 <= r.total <= 22 for r in rolls)
        and all(r.is_crit == (r.rolls[0] == 20) for r in rolls)
        and all(r.is_mishap == (r.rolls[0] == 1) for r in rolls),
        "out-of-range or misflagged d20 result",
    )


def check_deck_flow(c: Checker) -> None:
    """Draw / discard / reshuffle must conserve the card count."""
    c.section("deck flow")
    deck = DeckManager()
    starting = [
        Card(id=f"C{i:03d}", name=f"card{i}", cost=1, type="Attack")
        for i in range(10)
    ]
    deck.build_initial_deck(starting)
    c.check("initial deck has 10 cards", deck.draw_pile_size == 10, f"{deck.draw_pile_size}")

    drawn = deck.draw(5)
    c.check(
        "draw(5) moves 5 cards to hand",
        len(drawn) == 5 and deck.hand_size == 5 and deck.draw_pile_size == 5,
        f"hand={deck.hand_size} draw={deck.draw_pile_size}",
    )

    for card in list(deck.hand):
        deck.discard_card(card)
    c.check(
        "discarding the hand fills the discard pile",
        deck.hand_size == 0 and deck.discard_pile_size == 5,
        f"hand={deck.hand_size} discard={deck.discard_pile_size}",
    )

    deck.draw(6)
    c.check(
        "drawing past the draw pile reshuffles the discard pile",
        deck.hand_size == 6 and deck.draw_pile_size == 4 and deck.discard_pile_size == 0,
        f"hand={deck.hand_size} draw={deck.draw_pile_size} discard={deck.discard_pile_size}",
    )

    total = deck.hand_size + deck.draw_pile_size + deck.discard_pile_size + deck.exhaust_pile_size
    c.check("card count is conserved", total == 10, f"total={total}")

    deck.reset_for_battle()
    c.check(
        "reset_for_battle returns every card to the draw pile",
        deck.draw_pile_size == 10 and deck.hand_size == 0,
        f"draw={deck.draw_pile_size} hand={deck.hand_size}",
    )


def check_player_combat(c: Checker) -> None:
    """Stats, block absorption and healing must behave per the rules."""
    c.section("player combat")
    player = Player()
    c.check(
        "level-1 player has 60 HP and 4 FP",
        player.max_hp == 60 and player.max_fp == 4 and player.hp == 60,
        f"hp={player.max_hp} fp={player.max_fp}",
    )

    player.gain_block(5)
    taken = player.take_damage(8)
    c.check(
        "block absorbs 5 of 8 damage",
        player.block == 0 and taken == 3 and player.hp == 57,
        f"block={player.block} taken={taken} hp={player.hp}",
    )

    healed = player.heal(2)
    c.check("heal restores capped HP", healed == 2 and player.hp == 59, f"hp={player.hp}")

    player.hp = 55
    player.heal(1000)
    c.check("heal never exceeds max HP", player.hp == player.max_hp, f"hp={player.hp}")

    hp_before = player.hp
    player.add_stat_point("vit")
    c.check(
        "VIT point raises max HP without touching current HP",
        player.max_hp == 70 and player.hp == hp_before,
        f"max={player.max_hp} hp={player.hp}",
    )

    rich = Player()
    rich.souls = 10
    c.check(
        "spend_souls refuses an unaffordable cost",
        rich.spend_souls(20) is False and rich.souls == 10,
        f"souls={rich.souls}",
    )
    c.check(
        "spend_souls deducts an affordable cost",
        rich.spend_souls(4) is True and rich.souls == 6,
        f"souls={rich.souls}",
    )


def main() -> int:
    c = Checker()
    try:
        check_data_loading(c)
        check_dice_math(c)
        check_deck_flow(c)
        check_player_combat(c)
    except Exception as exc:  # noqa: BLE001 - report any crash as a failure
        print(f"FAIL unexpected {type(exc).__name__}: {exc}")
        return 1

    if c.failures:
        print(f"FAIL {len(c.failures)} check(s) failed: {c.failures[0]}")
        return 1

    print(
        f"selfcheck ok: {len(c.passed)} checks - JSON data loads, dice notation/crit maths, "
        "deck draw-discard-reshuffle flow, player block/heal/stat rules"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
