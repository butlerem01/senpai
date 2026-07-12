#!/usr/bin/env python3
"""Exact dense D4 indexing prototype for the Omega KR-vs-KC tablebase.

The four labelled squares are ordered as

    rook-side king, rook, Champion-side king, Champion

and ``turn`` is relative to the material roles (0 = rook side, 1 = Champion
side).  This deliberately has no dependency on Senpai's evaluator or search.

The prototype ranks every distinct four-piece placement, including placements
that cannot occur legally.  Illegal placements get a reserved WDL code in the
production table.  Keeping them in the index makes probing deterministic and
cheap while adding only about 18 percent to the table.
"""

from __future__ import annotations

import argparse
import bisect
import random
from array import array
from dataclasses import dataclass
from functools import lru_cache
from itertools import permutations
from typing import Dict, Iterable, List, Sequence, Tuple


BOARD_SIDE = 10
REGULAR_SQUARES = 100
SQUARES = 104

ROOK_TO_MOVE = 0
CHAMPION_TO_MOVE = 1

# Senpai numbers regular squares file-major.  The four Wizard squares then run
# SW, SE, NE, NW, matching Corner_SW ... Corner_NW in src/common.hpp.
COORDINATES: Tuple[Tuple[int, int], ...] = tuple(
    (file, rank) for file in range(BOARD_SIDE) for rank in range(BOARD_SIDE)
) + ((-1, -1), (10, -1), (10, 10), (-1, 10))
SQUARE_FROM_COORD = {coordinate: square for square, coordinate in enumerate(COORDINATES)}

RAW_SPATIAL_STATES = 104 * 103 * 102 * 101
RAW_STATES = RAW_SPATIAL_STATES * 2


@dataclass(frozen=True)
class State:
    rook_king: int
    rook: int
    champion_king: int
    champion: int
    turn: int

    def squares(self) -> Tuple[int, int, int, int]:
        return self.rook_king, self.rook, self.champion_king, self.champion


def _transform_coordinate(transform: int, coordinate: Tuple[int, int]) -> Tuple[int, int]:
    """Apply one of the eight D4 symmetries about the centre (4.5, 4.5)."""

    x, y = coordinate
    if transform >= 4:  # reflect in the vertical axis, then rotate
        x = 9 - x
        transform -= 4
    for _ in range(transform):
        x, y = 9 - y, x
    return x, y


TRANSFORM: Tuple[Tuple[int, ...], ...] = tuple(
    tuple(SQUARE_FROM_COORD[_transform_coordinate(t, coordinate)] for coordinate in COORDINATES)
    for t in range(8)
)


def transform_state(state: State, transform: int) -> State:
    table = TRANSFORM[transform]
    return State(*(table[square] for square in state.squares()), state.turn)


def _rank_ordered(items: Sequence[int], universe_size: int) -> int:
    """Rank an ordered sample without replacement in lexicographic order."""

    excluded: List[int] = []
    rank = 0
    radix = universe_size
    for item in items:
        if not 0 <= item < universe_size or item in excluded:
            raise ValueError("items must be distinct members of the universe")
        compressed = item - sum(previous < item for previous in excluded)
        rank = rank * radix + compressed
        excluded.append(item)
        radix -= 1
    return rank


def _unrank_ordered(rank: int, universe_size: int, length: int) -> Tuple[int, ...]:
    radices = [universe_size - i for i in range(length)]
    size = 1
    for radix in radices:
        size *= radix
    if not 0 <= rank < size:
        raise ValueError("ordered-sample rank is out of range")

    compressed = [0] * length
    for index in range(length - 1, -1, -1):
        compressed[index] = rank % radices[index]
        rank //= radices[index]

    selected: List[int] = []
    for compressed_value in compressed:
        # Select the compressed_value-th member while skipping the already
        # selected symbols.  Adjusting across the sorted exclusions is O(k)
        # instead of constructing an O(universe_size) candidate list.  This
        # matters when building the million-entry reflected-block quotient.
        value = compressed_value
        for excluded in sorted(selected):
            if value >= excluded:
                value += 1
        selected.append(value)
    return tuple(selected)


def raw_rank(state: State) -> int:
    if state.turn not in (ROOK_TO_MOVE, CHAMPION_TO_MOVE):
        raise ValueError("turn must be 0 or 1")
    return _rank_ordered(state.squares(), SQUARES) * 2 + state.turn


def raw_unrank(rank: int) -> State:
    if not 0 <= rank < RAW_STATES:
        raise ValueError("raw rank is out of range")
    spatial, turn = divmod(rank, 2)
    return State(*_unrank_ordered(spatial, SQUARES, 4), turn)


def canonical_raw_rank(state: State) -> int:
    """Return a sparse but useful reference key for the state's D4 orbit."""

    return min(raw_rank(transform_state(state, transform)) for transform in range(8))


def _orbits() -> Tuple[Tuple[int, ...], ...]:
    unseen = set(range(SQUARES))
    result = []
    while unseen:
        seed = min(unseen)
        orbit = tuple(sorted({TRANSFORM[t][seed] for t in range(8)}))
        result.append(orbit)
        unseen.difference_update(orbit)
    return tuple(result)


SQUARE_ORBITS = _orbits()
assert len(SQUARE_ORBITS) == 16


@dataclass
class FirstPieceBlock:
    representative: int
    orbit: Tuple[int, ...]
    base_transform: Dict[int, int]
    stabilizer: Tuple[int, ...]
    offset: int
    size: int
    to_local: Dict[int, int]
    from_local: Tuple[int, ...]

    @property
    def symmetric(self) -> bool:
        return len(self.stabilizer) == 2


GENERIC_BLOCK_SIZE = 103 * 102 * 101
SYMMETRIC_FIXED_TRIPLES = 11 * 10 * 9
SYMMETRIC_BLOCK_SIZE = (GENERIC_BLOCK_SIZE + SYMMETRIC_FIXED_TRIPLES) // 2


def _make_blocks() -> Tuple[FirstPieceBlock, ...]:
    blocks: List[FirstPieceBlock] = []
    offset = 0
    for orbit in sorted(SQUARE_ORBITS, key=lambda members: min(members)):
        representative = min(orbit)
        stabilizer = tuple(t for t in range(8) if TRANSFORM[t][representative] == representative)
        if len(stabilizer) not in (1, 2):
            raise AssertionError("the even Omega board should have stabilizers of order one or two")

        base_transform = {}
        for square in orbit:
            base_transform[square] = min(t for t in range(8) if TRANSFORM[t][square] == representative)

        if len(stabilizer) == 1:
            local_squares = tuple(square for square in range(SQUARES) if square != representative)
        else:
            reflection = stabilizer[1]
            fixed = sorted(
                square for square in range(SQUARES)
                if square != representative and TRANSFORM[reflection][square] == square
            )
            pairs = []
            seen = set(fixed)
            seen.add(representative)
            for square in range(SQUARES):
                if square in seen:
                    continue
                partner = TRANSFORM[reflection][square]
                pair = (min(square, partner), max(square, partner))
                pairs.append(pair)
                seen.update(pair)
            pairs.sort()
            if len(fixed) != 11 or len(pairs) != 46:
                raise AssertionError("unexpected reflection orbit structure")
            # Local reflection is i -> i for 0..10 and swaps 11+i with 57+i.
            local_squares = tuple(fixed + [pair[0] for pair in pairs] + [pair[1] for pair in pairs])

        to_local = {square: local for local, square in enumerate(local_squares)}
        size = SYMMETRIC_BLOCK_SIZE if len(stabilizer) == 2 else GENERIC_BLOCK_SIZE
        blocks.append(FirstPieceBlock(
            representative=representative,
            orbit=orbit,
            base_transform=base_transform,
            stabilizer=stabilizer,
            offset=offset,
            size=size,
            to_local=to_local,
            from_local=local_squares,
        ))
        offset += size
    return tuple(blocks)


BLOCKS = _make_blocks()
BLOCK_FOR_SQUARE = {
    square: block for block in BLOCKS for square in block.orbit
}
CANONICAL_SPATIAL_STATES = sum(block.size for block in BLOCKS)
CANONICAL_STATES = CANONICAL_SPATIAL_STATES * 2
assert sum(not block.symmetric for block in BLOCKS) == 10
assert sum(block.symmetric for block in BLOCKS) == 6
assert CANONICAL_SPATIAL_STATES == 13_797_348
assert CANONICAL_STATES == 27_594_696


def _reflect_local(local: int) -> int:
    if local < 11:
        return local
    if local < 57:
        return local + 46
    return local - 46


@lru_cache(maxsize=1)
def _symmetric_canonical_ranks() -> array:
    """Sorted local raw ranks that represent C2 orbits of three labels."""

    result = array("I")
    for raw in range(GENERIC_BLOCK_SIZE):
        triple = _unrank_ordered(raw, 103, 3)
        reflected = tuple(_reflect_local(item) for item in triple)
        if raw <= _rank_ordered(reflected, 103):
            result.append(raw)
    if len(result) != SYMMETRIC_BLOCK_SIZE:
        raise AssertionError("C2 quotient has the wrong size")
    return result


def dense_rank(state: State) -> int:
    """Return a dense rank in [0, 27,594,696) for the state's D4 orbit."""

    if state.turn not in (ROOK_TO_MOVE, CHAMPION_TO_MOVE):
        raise ValueError("turn must be 0 or 1")
    if len(set(state.squares())) != 4 or not all(0 <= square < SQUARES for square in state.squares()):
        raise ValueError("piece squares must be distinct Omega squares")

    block = BLOCK_FOR_SQUARE[state.rook_king]
    transform = block.base_transform[state.rook_king]
    transformed = tuple(TRANSFORM[transform][square] for square in state.squares()[1:])
    local = tuple(block.to_local[square] for square in transformed)
    local_raw = _rank_ordered(local, 103)

    if block.symmetric:
        reflected = tuple(_reflect_local(item) for item in local)
        local_raw = min(local_raw, _rank_ordered(reflected, 103))
        canonical = _symmetric_canonical_ranks()
        local_rank = bisect.bisect_left(canonical, local_raw)
        if local_rank == len(canonical) or canonical[local_rank] != local_raw:
            raise AssertionError("canonical C2 tuple was not indexed")
    else:
        local_rank = local_raw

    return (block.offset + local_rank) * 2 + state.turn


def dense_unrank(rank: int) -> State:
    """Return one canonical representative for a dense orbit rank."""

    if not 0 <= rank < CANONICAL_STATES:
        raise ValueError("dense rank is out of range")
    spatial, turn = divmod(rank, 2)
    offsets = [block.offset for block in BLOCKS]
    block_index = bisect.bisect_right(offsets, spatial) - 1
    block = BLOCKS[block_index]
    local_rank = spatial - block.offset
    if not 0 <= local_rank < block.size:
        raise AssertionError("block lookup failed")

    local_raw = _symmetric_canonical_ranks()[local_rank] if block.symmetric else local_rank
    local = _unrank_ordered(local_raw, 103, 3)
    actual = tuple(block.from_local[item] for item in local)
    return State(block.representative, *actual, turn)


def _king_attacks(first: int, second: int) -> bool:
    x1, y1 = COORDINATES[first]
    x2, y2 = COORDINATES[second]
    return max(abs(x1 - x2), abs(y1 - y2)) == 1


CHAMPION_DELTAS = {
    (+1, 0), (-1, 0), (0, +1), (0, -1),
    (+2, 0), (-2, 0), (0, +2), (0, -2),
    (+2, +2), (+2, -2), (-2, +2), (-2, -2),
}


def _champion_attacks(first: int, second: int) -> bool:
    x1, y1 = COORDINATES[first]
    x2, y2 = COORDINATES[second]
    return (x2 - x1, y2 - y1) in CHAMPION_DELTAS


def _rook_between(first: int, second: int) -> frozenset[int] | None:
    """Return strict between-squares, or None when no Omega rook ray exists."""

    if first >= REGULAR_SQUARES or second >= REGULAR_SQUARES:
        return None  # rooks have no route into a detached Wizard square
    x1, y1 = COORDINATES[first]
    x2, y2 = COORDINATES[second]
    if x1 == x2:
        low, high = sorted((y1, y2))
        return frozenset(SQUARE_FROM_COORD[(x1, y)] for y in range(low + 1, high))
    if y1 == y2:
        low, high = sorted((x1, x2))
        return frozenset(SQUARE_FROM_COORD[(x, y1)] for x in range(low + 1, high))
    return None


def is_legal(state: State) -> bool:
    """Match Senpai is_legal(): the side that just moved may not be in check."""

    if len(set(state.squares())) != 4:
        return False
    if _king_attacks(state.rook_king, state.champion_king):
        return False
    if state.turn == CHAMPION_TO_MOVE:
        # The rook side just moved.  Its king may not be attacked.
        return not _champion_attacks(state.champion, state.rook_king)
    if state.turn == ROOK_TO_MOVE:
        # The Champion side just moved.  Its king may not be attacked.
        between = _rook_between(state.rook, state.champion_king)
        rook_checks = between is not None and not (
            state.rook_king in between or state.champion in between
        )
        return not rook_checks
    return False


def _legal_raw_counts() -> Tuple[int, int]:
    """Count legal raw placements without an O(104P4) scan."""

    champion_turn = 0
    for rook_king in range(SQUARES):
        for champion_king in range(SQUARES):
            if champion_king == rook_king or _king_attacks(champion_king, rook_king):
                continue
            for champion in range(SQUARES):
                if champion in (rook_king, champion_king):
                    continue
                if not _champion_attacks(champion, rook_king):
                    champion_turn += 101  # any remaining rook square

    rook_turn = 0
    for champion_king in range(SQUARES):
        for rook_king in range(SQUARES):
            if rook_king == champion_king or _king_attacks(rook_king, champion_king):
                continue
            for rook in range(SQUARES):
                if rook in (champion_king, rook_king):
                    continue
                between = _rook_between(rook, champion_king)
                if between is None or rook_king in between:
                    rook_turn += 101
                else:
                    # Only a Champion interposed on the strict ray makes the
                    # previous Champion-side move legal.
                    rook_turn += len(between)
    return rook_turn, champion_turn


def _fixed_legal_counts() -> Tuple[int, int]:
    # Either diagonal reflection fixes twelve squares (ten board diagonal
    # squares plus the two matching detached corners).  Both give equal counts.
    fixed = [square for square, (x, y) in enumerate(COORDINATES) if x == y]
    rook_turn = champion_turn = 0
    for rook_king, rook, champion_king, champion in permutations(fixed, 4):
        champion_state = State(rook_king, rook, champion_king, champion, CHAMPION_TO_MOVE)
        rook_state = State(rook_king, rook, champion_king, champion, ROOK_TO_MOVE)
        champion_turn += is_legal(champion_state)
        rook_turn += is_legal(rook_state)
    return rook_turn, champion_turn


def legal_counts() -> Tuple[int, int, int, int]:
    """Return raw/canonical legal counts for rook-turn and Champion-turn."""

    raw_rook, raw_champion = _legal_raw_counts()
    fixed_rook, fixed_champion = _fixed_legal_counts()
    # Burnside: identity plus the two diagonal reflections.  The other five
    # D4 elements fix no square, hence no placement of four labelled pieces.
    canonical_rook = (raw_rook + 2 * fixed_rook) // 8
    canonical_champion = (raw_champion + 2 * fixed_champion) // 8
    return raw_rook, raw_champion, canonical_rook, canonical_champion


def self_test(samples: int = 2000) -> None:
    assert raw_unrank(0) == State(0, 1, 2, 3, 0)
    assert raw_rank(raw_unrank(RAW_STATES - 1)) == RAW_STATES - 1

    # Game regression: Kw2/Rj6 versus Ke5/Ch4, rook side to move.
    reported_final = State(101, 96, 45, 74, ROOK_TO_MOVE)
    assert is_legal(reported_final)
    assert dense_rank(reported_final) == 26_750_996

    rng = random.Random(0x4F4D454741)
    for _ in range(samples):
        squares = rng.sample(range(SQUARES), 4)
        state = State(*squares, rng.randrange(2))
        assert raw_unrank(raw_rank(state)) == state
        dense = dense_rank(state)
        assert dense_rank(dense_unrank(dense)) == dense
        for transform in range(8):
            transformed = transform_state(state, transform)
            assert dense_rank(transformed) == dense
            assert is_legal(transformed) == is_legal(state)

    for boundary in (0, 1, CANONICAL_STATES - 2, CANONICAL_STATES - 1):
        assert dense_rank(dense_unrank(boundary)) == boundary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-counts", action="store_true", help="enumerate exact legal-state counts")
    parser.add_argument("--samples", type=int, default=2000, help="random indexing self-tests")
    args = parser.parse_args()

    self_test(args.samples)
    print(f"raw states (with side to move):       {RAW_STATES:,}")
    print(f"D4-canonical states:                  {CANONICAL_STATES:,}")
    print(f"first-piece blocks:                   {len(BLOCKS)} (10 generic, 6 reflected)")
    print("dense indexing self-test:             PASS")

    if args.verify_counts:
        raw_r, raw_c, canonical_r, canonical_c = legal_counts()
        print(f"legal raw, rook side to move:         {raw_r:,}")
        print(f"legal raw, Champion side to move:     {raw_c:,}")
        print(f"legal canonical, rook side to move:   {canonical_r:,}")
        print(f"legal canonical, Champion to move:    {canonical_c:,}")
        print(f"legal canonical, total:               {canonical_r + canonical_c:,}")
        assert (raw_r, raw_c) == (86_677_248, 94_143_716)
        assert (canonical_r, canonical_c) == (10_837_131, 11_770_075)
        print("legal-state count verification:       PASS")


if __name__ == "__main__":
    main()
