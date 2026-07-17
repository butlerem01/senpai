#!/usr/bin/env python3
"""Compare an independent KCCK graph model with native Senpai move generation."""

from __future__ import annotations

import argparse
import hashlib
import random
import subprocess
from collections import Counter
from pathlib import Path
from typing import Callable, Iterator, NamedTuple, Sequence

from omega_geometry import (
    CHAMPION_MOVES,
    KING_MOVES,
    SQUARES,
    TRANSFORM,
    champion_attacks,
    king_attacks,
)


ATTACKER_TO_MOVE = 0
DEFENDER_TO_MOVE = 1
DEFAULT_SEED = 0x4B43434B
DEFAULT_UNIFORM = 10_000
DEFAULT_CAPTURE_PER_LABEL = 2_000
DEFAULT_ILLEGAL_HISTORY = 2_000
DEFAULT_CORNER = 1_000

# Filled after the version-one corpus is generated.  The driver refuses to run
# the default acceptance corpus if its serialized state list changes.
FROZEN_CORPUS_SHA256 = "300f9a25f2b6592b0322e79382acd7a9e5d819a7dc8ca8d2c44598b216471729"
FROZEN_EXPECTED_RECORDS_SHA256 = (
    "2c1cf4bf03837b43ac58b1c5acd1fe5e15029eaa974d490e08f1f6f5e44b92ab"
)


class State(NamedTuple):
    attacker_king: int
    champion_a: int
    defender_king: int
    champion_b: int
    turn: int

    def squares(self) -> tuple[int, int, int, int]:
        return self[:4]


class Native_Record(NamedTuple):
    legal: bool
    check: bool
    root_draw: bool
    capture_mask: int
    capture_draw_mask: int
    successors: frozenset[State]


class Curated(NamedTuple):
    name: str
    state: State
    legal: bool
    check: bool
    successor_count: int
    capture_mask: int


CURATED = (
    Curated("detached-corner-mate", State(1, 0, 100, 11, 1), True, True, 0, 0),
    Curated("detached-corner-stalemate", State(1, 0, 100, 2, 1), True, False, 0, 0),
    Curated("illegal-defender-history", State(0, 22, 24, 55, 0), False, False, 0, 0),
    Curated("capture-champion-a", State(2, 10, 0, 1, 1), True, True, 1, 1),
    Curated("capture-champion-b", State(2, 1, 0, 10, 1), True, True, 1, 2),
    Curated("both-label-quiet-moves", State(0, 22, 99, 55, 0), True, False, 27, 0),
    Curated("both-champions-capturable", State(99, 43, 44, 34, 1), True, True, 1, 3),
    Curated("capture-protected-by-champion", State(99, 43, 44, 41, 1), True, True, 4, 0),
    Curated("capture-protected-by-king", State(42, 43, 44, 0, 1), True, True, 4, 0),
    Curated("detached-corner-capture", State(99, 100, 0, 50, 1), True, False, 2, 1),
)


def structurally_valid(state: State) -> bool:
    return (
        state.turn in (ATTACKER_TO_MOVE, DEFENDER_TO_MOVE)
        and len(set(state.squares())) == 4
        and all(0 <= square < SQUARES for square in state.squares())
    )


def is_legal(state: State) -> bool:
    """Apply the frozen historical-legality rule used by the KCCK graph."""

    if not structurally_valid(state):
        return False
    if king_attacks(state.attacker_king, state.defender_king):
        return False
    if state.turn == DEFENDER_TO_MOVE:
        return True
    return not (
        champion_attacks(state.champion_a, state.defender_king)
        or champion_attacks(state.champion_b, state.defender_king)
    )


def in_check(state: State) -> bool:
    if not is_legal(state) or state.turn == ATTACKER_TO_MOVE:
        return False
    return (
        champion_attacks(state.champion_a, state.defender_king)
        or champion_attacks(state.champion_b, state.defender_king)
    )


def reference_record(state: State) -> Native_Record:
    if not is_legal(state):
        return Native_Record(False, False, False, 0, 0, frozenset())

    successors: set[State] = set()
    capture_mask = 0

    if state.turn == ATTACKER_TO_MOVE:
        for target in KING_MOVES[state.attacker_king]:
            if target in (state.champion_a, state.defender_king, state.champion_b):
                continue
            child = State(
                target,
                state.champion_a,
                state.defender_king,
                state.champion_b,
                DEFENDER_TO_MOVE,
            )
            if is_legal(child):
                successors.add(child)

        for target in CHAMPION_MOVES[state.champion_a]:
            if target in (state.attacker_king, state.defender_king, state.champion_b):
                continue
            child = State(
                state.attacker_king,
                target,
                state.defender_king,
                state.champion_b,
                DEFENDER_TO_MOVE,
            )
            if is_legal(child):
                successors.add(child)

        for target in CHAMPION_MOVES[state.champion_b]:
            if target in (state.attacker_king, state.champion_a, state.defender_king):
                continue
            child = State(
                state.attacker_king,
                state.champion_a,
                state.defender_king,
                target,
                DEFENDER_TO_MOVE,
            )
            if is_legal(child):
                successors.add(child)
    else:
        for target in KING_MOVES[state.defender_king]:
            if target == state.attacker_king:
                continue
            if target == state.champion_a:
                if (
                    not king_attacks(target, state.attacker_king)
                    and not champion_attacks(state.champion_b, target)
                ):
                    capture_mask |= 1
                continue
            if target == state.champion_b:
                if (
                    not king_attacks(target, state.attacker_king)
                    and not champion_attacks(state.champion_a, target)
                ):
                    capture_mask |= 2
                continue
            child = State(
                state.attacker_king,
                state.champion_a,
                target,
                state.champion_b,
                ATTACKER_TO_MOVE,
            )
            if is_legal(child):
                successors.add(child)

    # Every legal Champion capture leaves K+C versus K.  The independent model
    # freezes that boundary as a draw; the native draw mask verifies the live
    # Senpai insufficient-material implementation separately.
    return Native_Record(
        True,
        in_check(state),
        False,
        capture_mask,
        capture_mask,
        frozenset(successors),
    )


def transform_state(state: State, transform: int) -> State:
    table = TRANSFORM[transform]
    return State(*(table[square] for square in state.squares()), state.turn)


def swap_champions(state: State) -> State:
    return State(
        state.attacker_king,
        state.champion_b,
        state.defender_king,
        state.champion_a,
        state.turn,
    )


def _add_unique(
    result: list[State], seen: set[State], state: State
) -> bool:
    if state in seen:
        return False
    seen.add(state)
    result.append(state)
    return True


def _add_generated(
    result: list[State],
    seen: set[State],
    count: int,
    producer: Callable[[], State],
) -> None:
    added = 0
    while added < count:
        if _add_unique(result, seen, producer()):
            added += 1


def build_corpus(
    seed: int = DEFAULT_SEED,
    uniform: int = DEFAULT_UNIFORM,
    capture_per_label: int = DEFAULT_CAPTURE_PER_LABEL,
    illegal_history: int = DEFAULT_ILLEGAL_HISTORY,
    corner: int = DEFAULT_CORNER,
) -> list[State]:
    result: list[State] = []
    seen: set[State] = set()

    for fixture in CURATED:
        for transform in range(8):
            transformed = transform_state(fixture.state, transform)
            _add_unique(result, seen, transformed)
            _add_unique(result, seen, swap_champions(transformed))

    rng = random.Random(seed)

    def uniform_state() -> State:
        squares = rng.sample(range(SQUARES), 4)
        return State(*squares, rng.randrange(2))

    _add_generated(result, seen, uniform, uniform_state)

    def capture_adjacent(label: int) -> State:
        defender_king = rng.randrange(SQUARES)
        champion = rng.choice(KING_MOVES[defender_king])
        occupied = {defender_king, champion}
        attacker_candidates = [
            square for square in range(SQUARES)
            if square not in occupied and not king_attacks(square, defender_king)
        ]
        attacker_king = rng.choice(attacker_candidates)
        occupied.add(attacker_king)
        other = rng.choice([square for square in range(SQUARES) if square not in occupied])
        if label == 0:
            return State(attacker_king, champion, defender_king, other, DEFENDER_TO_MOVE)
        return State(attacker_king, other, defender_king, champion, DEFENDER_TO_MOVE)

    _add_generated(result, seen, capture_per_label, lambda: capture_adjacent(0))
    _add_generated(result, seen, capture_per_label, lambda: capture_adjacent(1))

    def illegal_history_state() -> State:
        champion_a = rng.randrange(SQUARES)
        defender_king = rng.choice(CHAMPION_MOVES[champion_a])
        occupied = {champion_a, defender_king}
        attacker_candidates = [
            square for square in range(SQUARES)
            if square not in occupied and not king_attacks(square, defender_king)
        ]
        attacker_king = rng.choice(attacker_candidates)
        occupied.add(attacker_king)
        champion_b = rng.choice(
            [square for square in range(SQUARES) if square not in occupied]
        )
        return State(
            attacker_king,
            champion_a,
            defender_king,
            champion_b,
            ATTACKER_TO_MOVE,
        )

    _add_generated(result, seen, illegal_history, illegal_history_state)

    def corner_state() -> State:
        role = rng.randrange(4)
        values = [-1, -1, -1, -1]
        values[role] = rng.randrange(100, 104)
        available = [square for square in range(SQUARES) if square != values[role]]
        other = iter(rng.sample(available, 3))
        for index in range(4):
            if values[index] < 0:
                values[index] = next(other)
        return State(*values, rng.randrange(2))

    _add_generated(result, seen, corner, corner_state)
    return result


def corpus_sha256(states: Sequence[State]) -> str:
    encoded = "".join(
        f"{state.attacker_king},{state.champion_a},{state.defender_king},"
        f"{state.champion_b},{state.turn}\n"
        for state in states
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def expected_records_sha256(states: Sequence[State]) -> str:
    digest = hashlib.sha256()
    for state in states:
        record = reference_record(state)
        children = sorted(record.successors)
        encoded = (
            f"{int(record.legal)},{int(record.check)},{int(record.root_draw)},"
            f"{record.capture_mask},{record.capture_draw_mask},{len(children)}"
        )
        for child in children:
            encoded += ";" + ",".join(map(str, child))
        digest.update((encoded + "\n").encode("ascii"))
    return digest.hexdigest()


def verify_curated_reference() -> None:
    for fixture in CURATED:
        record = reference_record(fixture.state)
        observed = (
            record.legal,
            record.check,
            len(record.successors),
            record.capture_mask,
        )
        expected = (
            fixture.legal,
            fixture.check,
            fixture.successor_count,
            fixture.capture_mask,
        )
        if observed != expected or record.capture_draw_mask != fixture.capture_mask:
            raise AssertionError(
                f"curated reference fixture changed: {fixture.name}\n"
                f"state={fixture.state}\nobserved={observed}\nexpected={expected}"
            )


def _chunks(values: Sequence[State], size: int) -> Iterator[Sequence[State]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def parse_native(line: str) -> Native_Record:
    parts = line.strip().split()
    if not parts or parts[0] == "error" or len(parts) < 6:
        raise RuntimeError(f"native oracle rejected a generated state: {line!r}")
    legal, check, root_draw, capture_mask, capture_draw_mask, count = map(
        int, parts[:6]
    )
    if (
        legal not in (0, 1)
        or check not in (0, 1)
        or root_draw not in (0, 1)
        or capture_mask not in range(4)
        or capture_draw_mask not in range(4)
        or count < 0
        or len(parts) != 6 + count
    ):
        raise RuntimeError(f"native oracle returned a malformed record: {line!r}")
    successors: set[State] = set()
    for encoded in parts[6:]:
        values = tuple(map(int, encoded.split(",")))
        if len(values) != 5:
            raise RuntimeError(f"native oracle returned a malformed state: {encoded!r}")
        child = State(*values)
        if not structurally_valid(child):
            raise RuntimeError(f"native oracle returned an invalid child: {child}")
        successors.add(child)
    if len(successors) != count:
        raise RuntimeError(f"native oracle returned duplicate successors: {line!r}")
    return Native_Record(
        bool(legal),
        bool(check),
        bool(root_draw),
        capture_mask,
        capture_draw_mask,
        frozenset(successors),
    )


def _format_difference(expected: frozenset[State], observed: frozenset[State]) -> str:
    missing = sorted(expected - observed)[:8]
    extra = sorted(observed - expected)[:8]
    return f"missing={missing}\nextra={extra}"


def run_native(native: Path, states: Sequence[State], batch_size: int) -> Counter[str]:
    process = subprocess.Popen(
        [str(native)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    counts: Counter[str] = Counter()
    try:
        assert process.stdin is not None and process.stdout is not None
        for batch in _chunks(states, batch_size):
            process.stdin.writelines(
                f"kcck {state.attacker_king} {state.champion_a} "
                f"{state.defender_king} {state.champion_b} {state.turn}\n"
                for state in batch
            )
            process.stdin.flush()

            for state in batch:
                line = process.stdout.readline()
                if line == "":
                    raise RuntimeError("native oracle closed its output unexpectedly")
                observed = parse_native(line)
                expected = reference_record(state)
                if observed != expected:
                    detail = ""
                    if observed.successors != expected.successors:
                        detail = "\n" + _format_difference(
                            expected.successors, observed.successors
                        )
                    raise AssertionError(
                        f"KCCK native parity mismatch at state={state}:\n"
                        f"native={observed._replace(successors=frozenset())}\n"
                        f"reference={expected._replace(successors=frozenset())}"
                        f"{detail}"
                    )
                counts["legal" if expected.legal else "illegal"] += 1
                if expected.check:
                    counts["check"] += 1
                if expected.capture_mask:
                    counts[f"capture-mask-{expected.capture_mask}"] += 1
                if any(square >= 100 for square in state.squares()):
                    counts["corner-root"] += 1

        process.stdin.close()
        return_code = process.wait(timeout=30)
        if return_code != 0:
            assert process.stderr is not None
            raise RuntimeError(
                f"native oracle exited {return_code}: {process.stderr.read()}"
            )
    finally:
        if process.poll() is None:
            process.kill()
    return counts


def is_frozen_configuration(args: argparse.Namespace) -> bool:
    return (
        args.seed == DEFAULT_SEED
        and args.uniform == DEFAULT_UNIFORM
        and args.capture_per_label == DEFAULT_CAPTURE_PER_LABEL
        and args.illegal_history == DEFAULT_ILLEGAL_HISTORY
        and args.corner == DEFAULT_CORNER
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", type=Path)
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=DEFAULT_SEED)
    parser.add_argument("--uniform", type=int, default=DEFAULT_UNIFORM)
    parser.add_argument(
        "--capture-per-label", type=int, default=DEFAULT_CAPTURE_PER_LABEL
    )
    parser.add_argument("--illegal-history", type=int, default=DEFAULT_ILLEGAL_HISTORY)
    parser.add_argument("--corner", type=int, default=DEFAULT_CORNER)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument(
        "--corpus-only",
        action="store_true",
        help="print the deterministic corpus identity without launching Senpai",
    )
    args = parser.parse_args()

    if (
        min(
            args.uniform,
            args.capture_per_label,
            args.illegal_history,
            args.corner,
        ) < 0
        or not 1 <= args.batch_size <= 8
    ):
        parser.error("sample counts must be nonnegative and batch-size must be in 1..8")

    verify_curated_reference()
    states = build_corpus(
        args.seed,
        args.uniform,
        args.capture_per_label,
        args.illegal_history,
        args.corner,
    )
    digest = corpus_sha256(states)
    expected_digest = expected_records_sha256(states)
    if is_frozen_configuration(args):
        if not FROZEN_CORPUS_SHA256:
            print(f"unfrozen KCCK corpus: states={len(states):,} sha256={digest}")
            if args.corpus_only:
                return
            raise RuntimeError("FROZEN_CORPUS_SHA256 has not been recorded")
        if digest != FROZEN_CORPUS_SHA256:
            raise AssertionError(
                f"frozen KCCK corpus changed: {digest} != {FROZEN_CORPUS_SHA256}"
            )
        if expected_digest != FROZEN_EXPECTED_RECORDS_SHA256:
            raise AssertionError(
                "frozen KCCK expected records changed: "
                f"{expected_digest} != {FROZEN_EXPECTED_RECORDS_SHA256}"
            )

    print(
        f"KCCK parity corpus: states={len(states):,} sha256={digest} "
        f"expected-records-sha256={expected_digest}"
    )
    if args.corpus_only:
        return
    if args.native is None or not args.native.is_file():
        parser.error("--native must name the built native KCCK oracle")

    counts = run_native(args.native.resolve(), states, args.batch_size)
    print(
        "native/independent KCCK graph parity: PASS "
        f"({len(states):,} roots; legal={counts['legal']:,}; "
        f"illegal={counts['illegal']:,}; checks={counts['check']:,}; "
        f"capture-A={counts['capture-mask-1']:,}; "
        f"capture-B={counts['capture-mask-2']:,}; "
        f"capture-both={counts['capture-mask-3']:,}; "
        f"corner-roots={counts['corner-root']:,})"
    )


if __name__ == "__main__":
    main()
