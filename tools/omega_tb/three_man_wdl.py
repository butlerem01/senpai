#!/usr/bin/env python3
"""Exact standalone Omega KRK/KCK WDL generator.

KRK is solved by retrograde analysis over a dense D4 quotient.  KCK is encoded
as an exact rules-policy table: Senpai's current Omega implementation declares
king plus one N/B/C/W versus a bare king drawn before searching legal moves.

The generated WDL is theoretical for KRK.  DTZ and the 100-ply counter are a
later layer and are intentionally not guessed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, Sequence, Tuple

from omega_geometry import (
    CHAMPION_MOVES,
    KING_MOVES,
    ROOK_RAYS,
    SQUARES,
    D4Indexer,
    champion_attacks,
    king_attacks,
    rook_attacks,
    transform_squares,
)


STRONG_TO_MOVE = 0
WEAK_TO_MOVE = 1

INVALID = 0
UNKNOWN = 1
LOSS = 2
DRAW = 3
WIN = 4

MATERIALS = ("krk", "kck")
INDEX = D4Indexer(3)

WDL_NAMES = {
    INVALID: "invalid",
    LOSS: "loss",
    DRAW: "draw",
    WIN: "win",
}

LABELLED_ORDER = ["strong_king", "role_piece", "weak_king", "turn"]
INDEX_NAME = "D4-first-piece-v1"
FILE_CODES = {"invalid": INVALID, "loss": LOSS, "draw": DRAW, "win": WIN}

RULES_DESCRIPTION = (
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "krk-theoretical-wdl;kck-insufficient-material-v1"
)
RULES_FINGERPRINT = hashlib.sha256(RULES_DESCRIPTION.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class State:
    strong_king: int
    piece: int
    weak_king: int
    turn: int

    def squares(self) -> Tuple[int, int, int]:
        return self.strong_king, self.piece, self.weak_king


@dataclass
class WdlTable:
    material: str
    status: bytearray
    legal_count: int
    counts: Dict[str, int]


def transform_state(state: State, transform: int) -> State:
    return State(*transform_squares(state.squares(), transform), state.turn)


def dense_rank(state: State) -> int:
    return INDEX.rank(state.squares(), state.turn)


def dense_unrank(rank: int) -> State:
    squares, turn = INDEX.unrank(rank)
    return State(*squares, turn)


def _piece_attacks(material: str, state: State, target: int) -> bool:
    if material == "kck":
        return champion_attacks(state.piece, target)
    if material == "krk":
        return rook_attacks(state.piece, target, (state.strong_king,))
    raise ValueError(f"unsupported material: {material}")


def is_legal(state: State, material: str) -> bool:
    """Match Senpai's historical-legality invariant.

    The side that just moved may not be in check; the current side may be.
    """

    if material not in MATERIALS or state.turn not in (STRONG_TO_MOVE, WEAK_TO_MOVE):
        return False
    if len(set(state.squares())) != 3 or not all(0 <= square < SQUARES for square in state.squares()):
        return False
    if king_attacks(state.strong_king, state.weak_king):
        return False
    if state.turn == STRONG_TO_MOVE:
        # The bare-king side just moved and may not have remained in check.
        return not _piece_attacks(material, state, state.weak_king)
    # The strong side just moved.  Its king can only be attacked by the other
    # king, already excluded above.
    return True


def in_check(state: State, material: str) -> bool:
    if state.turn != WEAK_TO_MOVE:
        return False
    return king_attacks(state.strong_king, state.weak_king) or _piece_attacks(
        material, state, state.weak_king
    )


def _rook_destinations(origin: int, occupied: Iterable[int]) -> Iterator[int]:
    occupied_set = set(occupied)
    for ray in ROOK_RAYS[origin]:
        for target in ray:
            if target in occupied_set:
                break
            yield target


def _piece_destinations(material: str, state: State) -> Iterator[int]:
    occupied = (state.strong_king, state.weak_king)
    if material == "kck":
        for target in CHAMPION_MOVES[state.piece]:
            if target not in occupied:
                yield target
    elif material == "krk":
        yield from _rook_destinations(state.piece, occupied)
    else:
        raise ValueError(f"unsupported material: {material}")


def successor_indices(state: State, material: str) -> Tuple[frozenset[int], bool]:
    """Return quiet same-class successors and whether Kxpiece gives a draw."""

    if not is_legal(state, material):
        raise ValueError("successors requested for an illegal state")

    successors = set()
    capture_draw = False

    if state.turn == STRONG_TO_MOVE:
        for target in KING_MOVES[state.strong_king]:
            if target in (state.piece, state.weak_king):
                continue
            child = State(target, state.piece, state.weak_king, WEAK_TO_MOVE)
            if is_legal(child, material):
                successors.add(dense_rank(child))

        for target in _piece_destinations(material, state):
            child = State(state.strong_king, target, state.weak_king, WEAK_TO_MOVE)
            if is_legal(child, material):
                successors.add(dense_rank(child))
    else:
        for target in KING_MOVES[state.weak_king]:
            if target == state.strong_king:
                continue
            if target == state.piece:
                # Capturing the unprotected role piece leaves K versus K.
                if not king_attacks(state.strong_king, target):
                    capture_draw = True
                continue
            child = State(state.strong_king, state.piece, target, STRONG_TO_MOVE)
            if is_legal(child, material):
                successors.add(dense_rank(child))

    return frozenset(successors), capture_draw


def predecessor_indices(state: State, material: str) -> frozenset[int]:
    """Generate quiet same-class predecessors without storing a reverse graph."""

    if not is_legal(state, material):
        raise ValueError("predecessors requested for an illegal state")

    predecessors = set()
    if state.turn == WEAK_TO_MOVE:
        # The strong side made the previous quiet move.
        for origin in KING_MOVES[state.strong_king]:
            if origin in (state.piece, state.weak_king):
                continue
            parent = State(origin, state.piece, state.weak_king, STRONG_TO_MOVE)
            if is_legal(parent, material):
                predecessors.add(dense_rank(parent))

        if material == "kck":
            origins: Iterable[int] = CHAMPION_MOVES[state.piece]
        else:
            origins = _rook_destinations(
                state.piece, (state.strong_king, state.weak_king)
            )
        for origin in origins:
            if origin in (state.strong_king, state.weak_king):
                continue
            parent = State(state.strong_king, origin, state.weak_king, STRONG_TO_MOVE)
            if is_legal(parent, material):
                predecessors.add(dense_rank(parent))
    else:
        # The bare king made the previous non-capturing move.
        for origin in KING_MOVES[state.weak_king]:
            if origin in (state.strong_king, state.piece):
                continue
            parent = State(state.strong_king, state.piece, origin, WEAK_TO_MOVE)
            if is_legal(parent, material):
                predecessors.add(dense_rank(parent))

    return frozenset(predecessors)


def _outcome_counts(status: bytearray) -> Dict[str, int]:
    return {
        name: status.count(code)
        for code, name in ((INVALID, "invalid"), (LOSS, "loss"), (DRAW, "draw"), (WIN, "win"))
    }


def solve(material: str, verify: bool = False) -> WdlTable:
    """Generate an exact role-relative WDL table for one three-man family."""

    if material not in MATERIALS:
        raise ValueError(f"unsupported material: {material}")

    status = bytearray(INDEX.state_count)
    remaining = bytearray(INDEX.state_count)
    legal_count = 0
    queue: deque[int] = deque()

    for index in range(INDEX.state_count):
        state = dense_unrank(index)
        if not is_legal(state, material):
            continue
        legal_count += 1

        if material == "kck":
            # Pos::is_draw() applies this policy before checking mate.
            status[index] = DRAW
            continue

        status[index] = UNKNOWN
        successors, capture_draw = successor_indices(state, material)
        move_classes = len(successors) + int(capture_draw)
        if move_classes > 255:
            raise AssertionError("three-man successor count no longer fits in one byte")
        remaining[index] = move_classes

        if move_classes == 0:
            if in_check(state, material):
                status[index] = LOSS
                queue.append(index)
            else:
                status[index] = DRAW

    if material == "krk":
        while queue:
            child = queue.popleft()
            child_status = status[child]
            child_state = dense_unrank(child)
            for parent in predecessor_indices(child_state, material):
                if status[parent] != UNKNOWN:
                    continue
                if child_status == LOSS:
                    status[parent] = WIN
                    queue.append(parent)
                elif child_status == WIN:
                    if remaining[parent] == 0:
                        raise AssertionError("retrograde successor counter underflow")
                    remaining[parent] -= 1
                    if remaining[parent] == 0:
                        status[parent] = LOSS
                        queue.append(parent)

        for index, outcome in enumerate(status):
            if outcome == UNKNOWN:
                status[index] = DRAW

    counts = _outcome_counts(status)
    if sum(counts.values()) != INDEX.state_count:
        raise AssertionError("generated payload still contains an unresolved WDL code")
    table = WdlTable(material, status, legal_count, counts)
    if verify:
        verify_table(table)
    return table


def verify_table(table: WdlTable) -> None:
    """Check legality, symmetry, and every WDL Bellman equation."""

    material = table.material
    if len(table.status) != INDEX.state_count:
        raise AssertionError("table has the wrong state count")

    legal_count = 0
    for index, actual in enumerate(table.status):
        state = dense_unrank(index)
        legal = is_legal(state, material)
        if not legal:
            if actual != INVALID:
                raise AssertionError(f"illegal state {index} has a WDL value")
            continue

        legal_count += 1
        if material == "kck":
            if actual != DRAW:
                raise AssertionError(f"KCK rules-policy state {index} is not drawn")
            continue

        successors, capture_draw = successor_indices(state, material)
        outcomes = [table.status[child] for child in successors]
        if capture_draw:
            outcomes.append(DRAW)

        if not outcomes:
            expected = LOSS if in_check(state, material) else DRAW
        elif LOSS in outcomes:
            expected = WIN
        elif all(outcome == WIN for outcome in outcomes):
            expected = LOSS
        else:
            expected = DRAW

        if actual != expected:
            raise AssertionError(
                f"Bellman mismatch at {index}: {WDL_NAMES.get(actual, actual)} != "
                f"{WDL_NAMES[expected]}"
            )

    if legal_count != table.legal_count:
        raise AssertionError("legal-state count changed during verification")


def write_table(path: Path, table: WdlTable) -> None:
    """Write a small versioned foundation file and verify its payload hash."""

    if table.material not in MATERIALS:
        raise ValueError("unsupported three-man material")
    payload = bytes(table.status)
    payload_counts = _validated_payload_counts(payload)
    if payload_counts != table.counts:
        raise ValueError("three-man table counts do not match its payload")
    if table.legal_count != sum(payload_counts[name] for name in ("loss", "draw", "win")):
        raise ValueError("three-man legal count does not match its payload")
    if table.material == "kck" and (payload_counts["loss"] != 0 or payload_counts["win"] != 0):
        raise ValueError("KCK payload violates the current insufficient-material policy")
    header = {
        "magic": "OMTB3WDL",
        "version": 1,
        "material": table.material.upper(),
        "labelled_order": LABELLED_ORDER,
        "index": INDEX_NAME,
        "square_count": SQUARES,
        "state_count": INDEX.state_count,
        "legal_count": table.legal_count,
        "rules": RULES_DESCRIPTION,
        "rules_sha256": RULES_FINGERPRINT,
        "codes": FILE_CODES,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "counts": table.counts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as stream:
            stream.write(json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii"))
            stream.write(b"\n")
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        read_table(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_table(path: Path) -> Tuple[dict, bytes]:
    with path.open("rb") as stream:
        header_line = stream.readline(64 * 1024)
        if not header_line.endswith(b"\n"):
            raise ValueError("missing or oversized OMTB3WDL header")
        header = json.loads(header_line)
        payload = stream.read()

    if header.get("magic") != "OMTB3WDL" or header.get("version") != 1:
        raise ValueError("unsupported OMTB3WDL file")
    if str(header.get("material", "")).lower() not in MATERIALS:
        raise ValueError("unsupported three-man material signature")
    if header.get("labelled_order") != LABELLED_ORDER:
        raise ValueError("three-man labelled-piece order mismatch")
    if header.get("index") != INDEX_NAME or header.get("square_count") != SQUARES:
        raise ValueError("three-man index metadata mismatch")
    if header.get("rules") != RULES_DESCRIPTION or header.get("rules_sha256") != RULES_FINGERPRINT:
        raise ValueError("three-man rules fingerprint mismatch")
    if header.get("codes") != FILE_CODES:
        raise ValueError("three-man WDL code map mismatch")
    if header.get("state_count") != INDEX.state_count or len(payload) != INDEX.state_count:
        raise ValueError("three-man payload size mismatch")
    if hashlib.sha256(payload).hexdigest() != header.get("payload_sha256"):
        raise ValueError("three-man payload checksum mismatch")
    counts = _validated_payload_counts(payload)
    if header.get("counts") != counts:
        raise ValueError("three-man payload outcome counts mismatch")
    legal_count = sum(counts[name] for name in ("loss", "draw", "win"))
    if header.get("legal_count") != legal_count:
        raise ValueError("three-man legal-state count mismatch")
    if str(header["material"]).lower() == "kck" and (counts["loss"] != 0 or counts["win"] != 0):
        raise ValueError("KCK payload violates the current insufficient-material policy")
    return header, payload


def _validated_payload_counts(payload: bytes) -> Dict[str, int]:
    allowed = (INVALID, LOSS, DRAW, WIN)
    if any(outcome not in allowed for outcome in payload):
        raise ValueError("three-man payload contains an unsupported WDL code")
    return {
        name: payload.count(code)
        for code, name in ((INVALID, "invalid"), (LOSS, "loss"), (DRAW, "draw"), (WIN, "win"))
    }


def indexing_self_test(samples: int = 2000) -> None:
    assert INDEX.raw_state_count == 2_185_248
    assert INDEX.state_count == 273_816
    rng = random.Random(0x4F4D5433)
    for _ in range(samples):
        state = State(*rng.sample(range(SQUARES), 3), rng.randrange(2))
        rank = dense_rank(state)
        assert dense_rank(dense_unrank(rank)) == rank
        for transform in range(8):
            assert dense_rank(transform_state(state, transform)) == rank


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--material", choices=("krk", "kck", "both"), default="both")
    parser.add_argument("--verify", action="store_true", help="check every WDL Bellman equation")
    parser.add_argument("--output", type=Path, help="optional output directory")
    parser.add_argument("--samples", type=int, default=2000)
    args = parser.parse_args()

    indexing_self_test(args.samples)
    print(f"three-man raw states:                 {INDEX.raw_state_count:,}")
    print(f"three-man D4-canonical states:        {INDEX.state_count:,}")
    print("dense indexing self-test:             PASS")

    materials: Sequence[str] = MATERIALS if args.material == "both" else (args.material,)
    for material in materials:
        started = time.perf_counter()
        table = solve(material, verify=args.verify)
        elapsed = time.perf_counter() - started
        print(
            f"{material.upper()}: legal={table.legal_count:,} "
            f"loss={table.counts['loss']:,} draw={table.counts['draw']:,} "
            f"win={table.counts['win']:,} time={elapsed:.2f}s"
        )
        if args.verify:
            print(f"{material.upper()} Bellman verification:          PASS")
        if args.output is not None:
            output = args.output / f"omega-{material}-wdl-v1.omtb3"
            write_table(output, table)
            print(f"wrote {output}")


if __name__ == "__main__":
    main()
