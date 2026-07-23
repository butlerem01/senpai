#!/usr/bin/env python3
"""Focused Python/C++ parity checks for OMNNUE1 architecture 4."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "omega_nnue"))

import omega_nnue as nn  # noqa: E402


def _rank_text(values: list[str | None]) -> str:
    result: list[str] = []
    empty = 0
    for value in values:
        if value is None:
            empty += 1
        else:
            if empty:
                result.append(str(empty))
                empty = 0
            result.append(value)
    if empty:
        result.append(str(empty))
    return "".join(result)


def _ofen(
    board: dict[int, str],
    corners: tuple[str, str, str, str] = ("-", "-", "-", "-"),
    *,
    turn: str = "w",
    castling: str = "-",
    ep: str = "-",
    halfmove: int = 0,
) -> str:
    ranks = []
    for rank in range(9, -1, -1):
        ranks.append(
            _rank_text([board.get(file * 10 + rank) for file in range(10)])
        )
    return (
        "/".join(ranks)
        + "["
        + "/".join(corners)
        + f"] {turn} {castling} {ep} {halfmove} 1"
    )


def _fixtures() -> tuple[str, ...]:
    start = (
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/"
        "CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
    )
    return (
        start,
        _ofen({50: "K", 59: "k"}, ep="d2,d3", halfmove=74),
        _ofen(
            {
                50: "K",
                42: "k",
                0: "C",
                22: "C",
                31: "W",
                89: "c",
                67: "w",
            },
            ("W", "-", "-", "w"),
            turn="b",
            halfmove=17,
        ),
        _ofen(
            {
                40: "K",
                69: "k",
                12: "N",
                32: "B",
                52: "C",
                72: "W",
                87: "n",
                77: "b",
                57: "c",
                37: "w",
            },
            turn="w",
            halfmove=91,
        ),
    )


def _cpp_features(
    executable: Path, ofen: str, perspective: int
) -> tuple[int, ...]:
    completed = subprocess.run(
        [
            str(executable),
            "--dump-features",
            str(nn.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL),
            str(perspective),
            ofen,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    text = completed.stdout.strip()
    return tuple(int(value) for value in text.split(",")) if text else ()


def _feature_matrix(ofen: str) -> tuple[np.ndarray, np.ndarray]:
    white = nn.active_features(
        ofen, 0, nn.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
    )
    black = nn.active_features(
        ofen, 1, nn.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
    )
    width = max(len(white), len(black))
    pad = nn.OMEGA_INTERACTION_FEATURE_COUNT
    white_row = np.full((1, width), pad, dtype=np.uint16)
    black_row = np.full((1, width), pad, dtype=np.uint16)
    white_row[0, : len(white)] = white
    black_row[0, : len(black)] = black
    side_to_move = ofen.split()[1]
    return (
        (white_row, black_row)
        if side_to_move == "w"
        else (black_row, white_row)
    )


def _network(output_cp: int | None = None) -> nn.QuantizedNetwork:
    ft_bias = np.zeros(nn.ACCUMULATOR_SIZE, dtype=np.int16)
    ft_weights = np.zeros(
        (nn.OMEGA_INTERACTION_FEATURE_COUNT, nn.ACCUMULATOR_SIZE),
        dtype=np.int16,
    )
    dense_bias = np.zeros(nn.HIDDEN_SIZE, dtype=np.int32)
    dense_weights = np.zeros(
        (nn.HIDDEN_SIZE, nn.ACCUMULATOR_SIZE * 2), dtype=np.int8
    )
    output_weights = np.zeros(nn.HIDDEN_SIZE, dtype=np.int8)
    output_bias = 0

    if output_cp is None:
        rows = np.arange(nn.OMEGA_INTERACTION_FEATURES, dtype=np.int16)
        ft_weights[
            nn.OMEGA_INTERACTION_DEVELOPMENT_FEATURE_BASE :, 0
        ] = rows % 5 + 1
        dense_weights[0, 0] = nn.HIDDEN_DIVISOR
        output_weights[0] = nn.OUTPUT_DIVISOR
    else:
        output_bias = output_cp * nn.OUTPUT_DIVISOR

    return nn.QuantizedNetwork(
        ft_bias=ft_bias,
        ft_weights=ft_weights,
        dense_bias=dense_bias,
        dense_weights=dense_weights,
        output_bias=output_bias,
        output_weights=output_weights,
        architecture=nn.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
    )


def _cpp_evaluate(executable: Path, network: Path, ofen: str) -> int:
    completed = subprocess.run(
        [str(executable), "--evaluate-network", str(network), ofen],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(completed.stdout.strip())


def run(executable: Path) -> None:
    if not executable.is_file():
        raise ValueError(f"C++ evaluator does not exist: {executable}")
    if nn.OMEGA_INTERACTION_FEATURE_COUNT != 48_440:
        raise AssertionError("architecture-4 feature count changed")
    if nn.OMEGA_INTERACTION_PAYLOAD_BYTES != 12_409_252:
        raise AssertionError("architecture-4 payload size changed")
    if nn.empty_board_leaper_distance(
        nn.PIECE_INDEX["c"], 0, 22
    ) != 1:
        raise AssertionError("Champion empty-board geometry changed")
    if nn.empty_board_leaper_distance(
        nn.PIECE_INDEX["w"], 100, 0
    ) != 1:
        raise AssertionError("Wizard corner geometry changed")
    if nn.empty_board_leaper_distance(
        nn.PIECE_INDEX["w"], 100, 1
    ) <= nn.SQUARE_COUNT:
        raise AssertionError("Wizard colour-complex reachability changed")

    fixtures = _fixtures()
    for ofen in fixtures:
        for perspective in (0, 1):
            python = nn.active_features(
                ofen,
                perspective,
                nn.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
            )
            cpp = _cpp_features(executable, ofen, perspective)
            if cpp != python:
                raise AssertionError(
                    f"feature mismatch for perspective {perspective}: "
                    f"Python={python}, C++={cpp}, OFEN={ofen}"
                )

    with tempfile.TemporaryDirectory(prefix="omega-nnue-arch4-") as temporary:
        directory = Path(temporary)
        interaction = _network()
        interaction_path = directory / "interaction.omnnue"
        interaction.write(interaction_path)
        if interaction_path.stat().st_size != 12_409_324:
            raise AssertionError("architecture-4 file size changed")
        for ofen in fixtures:
            stm, opponent = _feature_matrix(ofen)
            expected = int(interaction.predict_features(stm, opponent)[0])
            actual = _cpp_evaluate(executable, interaction_path, ofen)
            if actual != expected:
                raise AssertionError(
                    f"inference mismatch: Python={expected}, C++={actual}"
                )

        for raw, expected in ((1000, 600), (-1000, -600)):
            bounded = _network(raw)
            bounded_path = directory / f"bounded-{raw}.omnnue"
            bounded.write(bounded_path)
            stm, opponent = _feature_matrix(fixtures[0])
            if int(bounded.predict_features(stm, opponent)[0]) != expected:
                raise AssertionError("Python residual clamp changed")
            if _cpp_evaluate(
                executable, bounded_path, fixtures[0]
            ) != expected:
                raise AssertionError("C++ residual clamp changed")

    print(
        "Architecture-4 feature and inference parity passed for "
        f"{len(fixtures)} OFEN fixtures."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpp-evaluator", type=Path, required=True)
    args = parser.parse_args()
    run(args.cpp_evaluator.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
