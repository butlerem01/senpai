#!/usr/bin/env python3
"""Proof-oriented dense D4 indexing prototype for Omega KR-vs-KC.

The labelled order is rook-side king, rook, Champion-side king, Champion.
Turn is relative to those material roles.  All spatial placements are indexed;
the eventual table reserves a WDL code for positions that fail the historical
legality test.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from itertools import permutations
from typing import Tuple

from omega_geometry import (
    CHAMPION_MOVES,
    COORDINATES,
    D4Indexer,
    SQUARES,
    TRANSFORM,
    champion_attacks,
    king_attacks,
    rook_between,
    transform_squares,
)


ROOK_TO_MOVE = 0
CHAMPION_TO_MOVE = 1
INDEX = D4Indexer(4)


@dataclass(frozen=True)
class State:
    rook_king: int
    rook: int
    champion_king: int
    champion: int
    turn: int

    def squares(self) -> Tuple[int, int, int, int]:
        return self.rook_king, self.rook, self.champion_king, self.champion


def transform_state(state: State, transform: int) -> State:
    return State(*transform_squares(state.squares(), transform), state.turn)


def dense_rank(state: State) -> int:
    return INDEX.rank(state.squares(), state.turn)


def dense_unrank(rank: int) -> State:
    squares, turn = INDEX.unrank(rank)
    return State(*squares, turn)


def is_legal(state: State) -> bool:
    """Match Senpai's invariant: the side that just moved is not in check."""

    if len(set(state.squares())) != 4:
        return False
    if king_attacks(state.rook_king, state.champion_king):
        return False
    if state.turn == CHAMPION_TO_MOVE:
        # The rook side just moved; its king may not be attacked.
        return not champion_attacks(state.champion, state.rook_king)
    if state.turn == ROOK_TO_MOVE:
        # The Champion side just moved; its king may not be attacked.
        between = rook_between(state.rook, state.champion_king)
        rook_checks = between is not None and not (
            state.rook_king in between or state.champion in between
        )
        return not rook_checks
    return False


def _legal_raw_counts() -> Tuple[int, int]:
    champion_turn = 0
    for rook_king in range(SQUARES):
        for champion_king in range(SQUARES):
            if champion_king == rook_king or king_attacks(champion_king, rook_king):
                continue
            for champion in range(SQUARES):
                if champion in (rook_king, champion_king):
                    continue
                if not champion_attacks(champion, rook_king):
                    champion_turn += 101

    rook_turn = 0
    for champion_king in range(SQUARES):
        for rook_king in range(SQUARES):
            if rook_king == champion_king or king_attacks(rook_king, champion_king):
                continue
            for rook in range(SQUARES):
                if rook in (champion_king, rook_king):
                    continue
                between = rook_between(rook, champion_king)
                if between is None or rook_king in between:
                    rook_turn += 101
                else:
                    rook_turn += len(between)
    return rook_turn, champion_turn


def _fixed_legal_counts() -> Tuple[int, int]:
    fixed = [square for square, (x, y) in enumerate(COORDINATES) if x == y]
    rook_turn = champion_turn = 0
    for rook_king, rook, champion_king, champion in permutations(fixed, 4):
        rook_turn += is_legal(State(
            rook_king, rook, champion_king, champion, ROOK_TO_MOVE
        ))
        champion_turn += is_legal(State(
            rook_king, rook, champion_king, champion, CHAMPION_TO_MOVE
        ))
    return rook_turn, champion_turn


def legal_counts() -> Tuple[int, int, int, int]:
    raw_rook, raw_champion = _legal_raw_counts()
    fixed_rook, fixed_champion = _fixed_legal_counts()
    canonical_rook = (raw_rook + 2 * fixed_rook) // 8
    canonical_champion = (raw_champion + 2 * fixed_champion) // 8
    return raw_rook, raw_champion, canonical_rook, canonical_champion


def self_test(samples: int = 2000) -> None:
    assert INDEX.raw_state_count == 220_710_048
    assert INDEX.state_count == 27_594_696
    assert sum(not block.symmetric for block in INDEX.blocks) == 10
    assert sum(block.symmetric for block in INDEX.blocks) == 6

    # Reported game after 116...Ch4: Kw2/Rj6 versus Ke5/Ch4.
    reported_final = State(101, 96, 45, 74, ROOK_TO_MOVE)
    assert is_legal(reported_final)
    assert dense_rank(reported_final) == 26_750_996

    rng = random.Random(0x4F4D454741)
    for _ in range(samples):
        state = State(*rng.sample(range(SQUARES), 4), rng.randrange(2))
        dense = dense_rank(state)
        assert dense_rank(dense_unrank(dense)) == dense
        for transform in range(8):
            transformed = transform_state(state, transform)
            assert dense_rank(transformed) == dense
            assert is_legal(transformed) == is_legal(state)

    for boundary in (0, 1, INDEX.state_count - 2, INDEX.state_count - 1):
        assert dense_rank(dense_unrank(boundary)) == boundary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-counts", action="store_true")
    parser.add_argument("--samples", type=int, default=2000)
    args = parser.parse_args()

    self_test(args.samples)
    print(f"raw states (with side to move):       {INDEX.raw_state_count:,}")
    print(f"D4-canonical states:                  {INDEX.state_count:,}")
    print("first-piece blocks:                   16 (10 generic, 6 reflected)")
    print("dense indexing self-test:             PASS")

    if args.verify_counts:
        counts = legal_counts()
        print(f"legal raw, rook side to move:         {counts[0]:,}")
        print(f"legal raw, Champion side to move:     {counts[1]:,}")
        print(f"legal canonical, rook side to move:   {counts[2]:,}")
        print(f"legal canonical, Champion to move:    {counts[3]:,}")
        print(f"legal canonical, total:               {counts[2] + counts[3]:,}")
        assert counts == (86_677_248, 94_143_716, 10_837_131, 11_770_075)
        print("legal-state count verification:       PASS")


if __name__ == "__main__":
    main()
