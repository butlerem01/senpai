#!/usr/bin/env python3
"""Shared 104-square Omega geometry and dense D4 indexing.

This module is deliberately independent of Senpai's evaluator and search.  A
table generator can therefore be verified without trusting the code that will
eventually consume its output.
"""

from __future__ import annotations

import bisect
from array import array
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Iterable, List, Sequence, Tuple


BOARD_SIDE = 10
REGULAR_SQUARES = 100
SQUARES = 104

# Senpai numbers regular squares file-major.  Detached Wizard squares follow
# in SW, SE, NE, NW order, matching Corner_SW ... Corner_NW in common.hpp.
COORDINATES: Tuple[Tuple[int, int], ...] = tuple(
    (file, rank) for file in range(BOARD_SIDE) for rank in range(BOARD_SIDE)
) + ((-1, -1), (10, -1), (10, 10), (-1, 10))
SQUARE_FROM_COORD = {coordinate: square for square, coordinate in enumerate(COORDINATES)}


def _transform_coordinate(transform: int, coordinate: Tuple[int, int]) -> Tuple[int, int]:
    """Apply one of the eight D4 symmetries about the centre (4.5, 4.5)."""

    x, y = coordinate
    if transform >= 4:  # reflect vertically, then rotate
        x = 9 - x
        transform -= 4
    for _ in range(transform):
        x, y = 9 - y, x
    return x, y


TRANSFORM: Tuple[Tuple[int, ...], ...] = tuple(
    tuple(SQUARE_FROM_COORD[_transform_coordinate(t, coordinate)] for coordinate in COORDINATES)
    for t in range(8)
)


def transform_squares(squares: Sequence[int], transform: int) -> Tuple[int, ...]:
    table = TRANSFORM[transform]
    return tuple(table[square] for square in squares)


def ordered_size(universe_size: int, length: int) -> int:
    size = 1
    for index in range(length):
        size *= universe_size - index
    return size


def rank_ordered(items: Sequence[int], universe_size: int) -> int:
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


def unrank_ordered(rank: int, universe_size: int, length: int) -> Tuple[int, ...]:
    size = ordered_size(universe_size, length)
    if not 0 <= rank < size:
        raise ValueError("ordered-sample rank is out of range")

    radices = [universe_size - index for index in range(length)]
    compressed = [0] * length
    for index in range(length - 1, -1, -1):
        compressed[index] = rank % radices[index]
        rank //= radices[index]

    selected: List[int] = []
    for compressed_value in compressed:
        value = compressed_value
        for excluded in sorted(selected):
            if value >= excluded:
                value += 1
        selected.append(value)
    return tuple(selected)


def _square_orbits() -> Tuple[Tuple[int, ...], ...]:
    unseen = set(range(SQUARES))
    result = []
    while unseen:
        seed = min(unseen)
        orbit = tuple(sorted({TRANSFORM[t][seed] for t in range(8)}))
        result.append(orbit)
        unseen.difference_update(orbit)
    return tuple(result)


SQUARE_ORBITS = _square_orbits()
assert len(SQUARE_ORBITS) == 16


@dataclass(frozen=True)
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


def _reflect_local(local: int) -> int:
    # A reflected first-piece block has eleven other fixed squares followed by
    # two parallel lists containing the members of 46 reflection pairs.
    if local < 11:
        return local
    if local < 57:
        return local + 46
    return local - 46


@lru_cache(maxsize=None)
def _symmetric_canonical_ranks(tail_length: int) -> array:
    """Sorted local raw ranks representing C2 orbits of labelled tails."""

    generic_size = ordered_size(103, tail_length)
    expected = (generic_size + ordered_size(11, tail_length)) // 2
    result = array("I")
    for raw in range(generic_size):
        tail = unrank_ordered(raw, 103, tail_length)
        reflected = tuple(_reflect_local(item) for item in tail)
        if raw <= rank_ordered(reflected, 103):
            result.append(raw)
    if len(result) != expected:
        raise AssertionError("C2 quotient has the wrong size")
    return result


class D4Indexer:
    """Dense D4 rank/unrank for labelled pieces plus a role-relative turn."""

    def __init__(self, piece_count: int):
        if piece_count not in (3, 4):
            raise ValueError("the current Omega tablebase index supports three or four pieces")
        self.piece_count = piece_count
        self.tail_length = piece_count - 1
        self.generic_block_size = ordered_size(103, self.tail_length)
        self.symmetric_block_size = (
            self.generic_block_size + ordered_size(11, self.tail_length)
        ) // 2

        blocks = []
        offset = 0
        for orbit in sorted(SQUARE_ORBITS, key=lambda members: min(members)):
            representative = min(orbit)
            stabilizer = tuple(
                transform for transform in range(8)
                if TRANSFORM[transform][representative] == representative
            )
            if len(stabilizer) not in (1, 2):
                raise AssertionError("unexpected first-square stabilizer")

            base_transform = {
                square: min(
                    transform for transform in range(8)
                    if TRANSFORM[transform][square] == representative
                )
                for square in orbit
            }

            if len(stabilizer) == 1:
                local_squares = tuple(
                    square for square in range(SQUARES) if square != representative
                )
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
                    pairs.append((min(square, partner), max(square, partner)))
                    seen.update((square, partner))
                pairs.sort()
                if len(fixed) != 11 or len(pairs) != 46:
                    raise AssertionError("unexpected reflection orbit structure")
                local_squares = tuple(
                    fixed + [pair[0] for pair in pairs] + [pair[1] for pair in pairs]
                )

            size = self.symmetric_block_size if len(stabilizer) == 2 else self.generic_block_size
            blocks.append(FirstPieceBlock(
                representative=representative,
                orbit=orbit,
                base_transform=base_transform,
                stabilizer=stabilizer,
                offset=offset,
                size=size,
                to_local={square: local for local, square in enumerate(local_squares)},
                from_local=local_squares,
            ))
            offset += size

        self.blocks = tuple(blocks)
        self.block_for_square = {
            square: block for block in self.blocks for square in block.orbit
        }
        self.offsets = tuple(block.offset for block in self.blocks)
        self.spatial_count = offset
        self.state_count = offset * 2
        self.raw_spatial_count = ordered_size(SQUARES, piece_count)
        self.raw_state_count = self.raw_spatial_count * 2

    def rank(self, squares: Sequence[int], turn: int) -> int:
        if turn not in (0, 1):
            raise ValueError("turn must be zero or one")
        if len(squares) != self.piece_count:
            raise ValueError("wrong number of labelled pieces")
        if len(set(squares)) != self.piece_count or not all(0 <= square < SQUARES for square in squares):
            raise ValueError("piece squares must be distinct Omega squares")

        block = self.block_for_square[squares[0]]
        transform = block.base_transform[squares[0]]
        transformed = tuple(TRANSFORM[transform][square] for square in squares[1:])
        local = tuple(block.to_local[square] for square in transformed)
        local_raw = rank_ordered(local, 103)

        if block.symmetric:
            reflected = tuple(_reflect_local(item) for item in local)
            local_raw = min(local_raw, rank_ordered(reflected, 103))
            canonical = _symmetric_canonical_ranks(self.tail_length)
            local_rank = bisect.bisect_left(canonical, local_raw)
            if local_rank == len(canonical) or canonical[local_rank] != local_raw:
                raise AssertionError("canonical C2 tuple was not indexed")
        else:
            local_rank = local_raw

        return (block.offset + local_rank) * 2 + turn

    def unrank(self, rank: int) -> Tuple[Tuple[int, ...], int]:
        if not 0 <= rank < self.state_count:
            raise ValueError("dense rank is out of range")
        spatial, turn = divmod(rank, 2)
        block_index = bisect.bisect_right(self.offsets, spatial) - 1
        block = self.blocks[block_index]
        local_rank = spatial - block.offset
        if not 0 <= local_rank < block.size:
            raise AssertionError("block lookup failed")

        local_raw = (
            _symmetric_canonical_ranks(self.tail_length)[local_rank]
            if block.symmetric else local_rank
        )
        local = unrank_ordered(local_raw, 103, self.tail_length)
        tail = tuple(block.from_local[item] for item in local)
        return (block.representative, *tail), turn


def king_attacks(first: int, second: int) -> bool:
    x1, y1 = COORDINATES[first]
    x2, y2 = COORDINATES[second]
    return max(abs(x1 - x2), abs(y1 - y2)) == 1


CHAMPION_DELTAS = frozenset({
    (+1, 0), (-1, 0), (0, +1), (0, -1),
    (+2, 0), (-2, 0), (0, +2), (0, -2),
    (+2, +2), (+2, -2), (-2, +2), (-2, -2),
})

WIZARD_DELTAS = frozenset({
    (+1, +1), (+1, -1), (-1, +1), (-1, -1),
    (+1, +3), (+1, -3), (-1, +3), (-1, -3),
    (+3, +1), (+3, -1), (-3, +1), (-3, -1),
})

KNIGHT_DELTAS = frozenset({
    (+1, +2), (+1, -2), (-1, +2), (-1, -2),
    (+2, +1), (+2, -1), (-2, +1), (-2, -1),
})


def champion_attacks(first: int, second: int) -> bool:
    x1, y1 = COORDINATES[first]
    x2, y2 = COORDINATES[second]
    return (x2 - x1, y2 - y1) in CHAMPION_DELTAS


def wizard_attacks(first: int, second: int) -> bool:
    x1, y1 = COORDINATES[first]
    x2, y2 = COORDINATES[second]
    return (x2 - x1, y2 - y1) in WIZARD_DELTAS


def knight_attacks(first: int, second: int) -> bool:
    x1, y1 = COORDINATES[first]
    x2, y2 = COORDINATES[second]
    return (x2 - x1, y2 - y1) in KNIGHT_DELTAS


def rook_between(first: int, second: int) -> frozenset[int] | None:
    """Return strict between-squares, or None when no Omega rook ray exists."""

    if first >= REGULAR_SQUARES or second >= REGULAR_SQUARES:
        return None
    x1, y1 = COORDINATES[first]
    x2, y2 = COORDINATES[second]
    if x1 == x2:
        low, high = sorted((y1, y2))
        return frozenset(SQUARE_FROM_COORD[(x1, rank)] for rank in range(low + 1, high))
    if y1 == y2:
        low, high = sorted((x1, x2))
        return frozenset(SQUARE_FROM_COORD[(file, y1)] for file in range(low + 1, high))
    return None


def rook_attacks(first: int, second: int, blockers: Iterable[int] = ()) -> bool:
    between = rook_between(first, second)
    return between is not None and not any(blocker in between for blocker in blockers)


KING_MOVES: Tuple[Tuple[int, ...], ...] = tuple(
    tuple(target for target in range(SQUARES) if target != origin and king_attacks(origin, target))
    for origin in range(SQUARES)
)
CHAMPION_MOVES: Tuple[Tuple[int, ...], ...] = tuple(
    tuple(target for target in range(SQUARES) if target != origin and champion_attacks(origin, target))
    for origin in range(SQUARES)
)
WIZARD_MOVES: Tuple[Tuple[int, ...], ...] = tuple(
    tuple(target for target in range(SQUARES) if target != origin and wizard_attacks(origin, target))
    for origin in range(SQUARES)
)
KNIGHT_MOVES: Tuple[Tuple[int, ...], ...] = tuple(
    tuple(target for target in range(SQUARES) if target != origin and knight_attacks(origin, target))
    for origin in range(SQUARES)
)


def _make_rook_rays(origin: int) -> Tuple[Tuple[int, ...], ...]:
    if origin >= REGULAR_SQUARES:
        return ()
    x, y = COORDINATES[origin]
    rays = []
    for dx, dy in ((+1, 0), (-1, 0), (0, +1), (0, -1)):
        ray = []
        tx, ty = x + dx, y + dy
        while 0 <= tx < BOARD_SIDE and 0 <= ty < BOARD_SIDE:
            ray.append(SQUARE_FROM_COORD[(tx, ty)])
            tx += dx
            ty += dy
        rays.append(tuple(ray))
    return tuple(rays)


ROOK_RAYS: Tuple[Tuple[Tuple[int, ...], ...], ...] = tuple(
    _make_rook_rays(origin) for origin in range(SQUARES)
)
