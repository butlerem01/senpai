#!/usr/bin/env python3
"""Compare the standalone KRK/KCK graph with native Senpai move generation."""

from __future__ import annotations

import argparse
import random
import subprocess
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import three_man_wdl as tb


def _chunks(values: Sequence[int], size: int) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _curated_indices() -> set[int]:
    states = {
        tb.State(42, 0, 40, tb.WEAK_TO_MOVE),       # concrete KRK mate
        tb.State(45, 96, 100, tb.WEAK_TO_MOVE),    # detached-corner refuge
        tb.State(101, 80, 56, tb.WEAK_TO_MOVE),    # g099 after Rxi0
        tb.State(71, 49, 55, tb.WEAK_TO_MOVE),     # g116 after Kxh1
        tb.State(103, 27, 43, tb.WEAK_TO_MOVE),    # rotated g116 after Rxc7
    }
    anchors = (0, 9, 45, 90, 99)
    for corner in range(100, 104):
        for turn in (tb.STRONG_TO_MOVE, tb.WEAK_TO_MOVE):
            states.add(tb.State(corner, 45, 0, turn))
            states.add(tb.State(45, corner, 99, turn))
            states.add(tb.State(0, 45, corner, turn))
    for strong_king in anchors:
        for turn in (tb.STRONG_TO_MOVE, tb.WEAK_TO_MOVE):
            states.add(tb.State(strong_king, 54, 99, turn))
    return {
        tb.dense_rank(state)
        for state in states
        if len(set(state.squares())) == 3
    }


def selected_indices(samples: int, seed: int, exhaustive: bool) -> list[int]:
    if exhaustive:
        return list(range(tb.INDEX.state_count))
    chosen = _curated_indices()
    chosen.update((0, 1, tb.INDEX.state_count - 2, tb.INDEX.state_count - 1))
    rng = random.Random(seed)
    population = tb.INDEX.state_count
    target = min(population, samples + len(chosen))
    while len(chosen) < target:
        chosen.add(rng.randrange(population))
    return sorted(chosen)


def parse_native(line: str) -> tuple[bool, bool, bool, bool, set[int]]:
    parts = line.strip().split()
    if not parts or parts[0] == "error" or len(parts) < 5:
        raise RuntimeError(f"native oracle rejected a generated state: {line!r}")
    legal, check, rules_draw, capture_draw, count = map(int, parts[:5])
    if len(parts) != 5 + count:
        raise RuntimeError(f"native oracle returned a malformed successor list: {line!r}")
    successors = set()
    for encoded in parts[5:]:
        values = tuple(map(int, encoded.split(",")))
        if len(values) != 4:
            raise RuntimeError(f"native oracle returned a malformed state: {encoded!r}")
        successors.add(tb.dense_rank(tb.State(*values)))
    return bool(legal), bool(check), bool(rules_draw), bool(capture_draw), successors


def compare_material(
    process: subprocess.Popen[str], material: str, indices: Sequence[int], batch_size: int
) -> int:
    checked = 0
    assert process.stdin is not None and process.stdout is not None
    for batch in _chunks(indices, batch_size):
        states = [tb.dense_unrank(index) for index in batch]
        process.stdin.writelines(
            f"{material} {state.strong_king} {state.piece} {state.weak_king} {state.turn}\n"
            for state in states
        )
        process.stdin.flush()

        for index, state in zip(batch, states):
            line = process.stdout.readline()
            if line == "":
                raise RuntimeError("native oracle closed its output unexpectedly")
            native_legal, native_check, native_draw, native_capture, native_successors = (
                parse_native(line)
            )
            python_legal = tb.is_legal(state, material)
            python_check = tb.in_check(state, material)
            python_draw = python_legal and material == "kck"
            if python_legal:
                python_successors, python_capture = tb.successor_indices(state, material)
                python_successors = set(python_successors)
            else:
                python_successors, python_capture = set(), False

            observed = (
                native_legal,
                native_check,
                native_draw,
                native_capture,
                native_successors,
            )
            expected = (
                python_legal,
                python_check,
                python_draw,
                python_capture,
                python_successors,
            )
            if observed != expected:
                raise AssertionError(
                    f"{material.upper()} parity mismatch at dense {index}, state={state}:\n"
                    f"native={observed}\npython={expected}"
                )
            checked += 1
    return checked


def run(native: Path, samples: int, seed: int, exhaustive: bool, batch_size: int) -> None:
    indices = selected_indices(samples, seed, exhaustive)
    process = subprocess.Popen(
        [str(native)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        total = 0
        for offset, material in enumerate(tb.MATERIALS):
            total += compare_material(process, material, indices, batch_size)
        assert process.stdin is not None
        process.stdin.close()
        return_code = process.wait(timeout=10)
        if return_code != 0:
            assert process.stderr is not None
            raise RuntimeError(f"native oracle exited {return_code}: {process.stderr.read()}")
    finally:
        if process.poll() is None:
            process.kill()
    mode = "exhaustive" if exhaustive else "sampled"
    print(
        f"native/Python three-man graph parity: PASS "
        f"({total:,} {mode} material-states; {len(indices):,} per family)"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=5000, help="random dense states per family")
    parser.add_argument("--seed", type=int, default=0x4F4D5041)
    # Windows anonymous pipes can be as small as 4 KiB.  Keep each request
    # batch below the largest possible successor response so neither side can
    # block while the other is still writing its pipe.
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--exhaustive", action="store_true")
    args = parser.parse_args()
    if args.samples < 0 or not 1 <= args.batch_size <= 8:
        parser.error("samples must be nonnegative and batch-size must be in 1..8")
    if not args.native.is_file():
        parser.error(f"native oracle not found: {args.native}")
    run(args.native.resolve(), args.samples, args.seed, args.exhaustive, args.batch_size)


if __name__ == "__main__":
    main()
