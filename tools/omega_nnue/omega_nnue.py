#!/usr/bin/env python3
"""Shared data, feature, and binary-format code for the Omega NNUE bootstrap.

This module intentionally depends only on Python's standard library and NumPy.
The C++ engine is the authority for runtime inference; ``QuantizedNetwork`` is
an independent implementation used to catch exporter and arithmetic drift.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
import hashlib
import json
import math
import os
import struct
import tempfile

import numpy as np


MAGIC = b"OMNNUE1\0"
ENDIAN_TAG = 0x01020304
FORMAT_VERSION = 1
HEADER_BYTES = 72
ARCHITECTURE_ABSOLUTE = 1
ARCHITECTURE_RESIDUAL = 2
ARCHITECTURE_KING_STATE_RESIDUAL = 3
ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL = 4
# Preserve the original public constant for callers that construct an
# absolute OMNNUE1 network. The header's architecture field now also
# self-describes residual/correction semantics without changing the tensor
# layout or invalidating any architecture-1 file.
ARCHITECTURE = ARCHITECTURE_ABSOLUTE
ARCHITECTURE_NAMES = {
    ARCHITECTURE_ABSOLUTE: "absolute",
    ARCHITECTURE_RESIDUAL: "residual",
    ARCHITECTURE_KING_STATE_RESIDUAL: "king-state-residual",
    ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL: "omega-interaction-residual",
}
SQUARE_COUNT = 104
PIECE_COUNT = 8
FEATURE_COUNT = 1668
KING_BUCKET_COUNT = 29
KING_STATE_OCCUPANCY_FEATURES = (
    KING_BUCKET_COUNT * 2 * PIECE_COUNT * SQUARE_COUNT
)
KING_STATE_CASTLING_FEATURE_BASE = KING_STATE_OCCUPANCY_FEATURES
KING_STATE_EP_FEATURE_BASE = KING_STATE_CASTLING_FEATURE_BASE + 4
KING_STATE_HALFMOVE_FEATURE_BASE = KING_STATE_EP_FEATURE_BASE + SQUARE_COUNT
KING_STATE_HALFMOVE_BINS = 8
KING_STATE_PHASE_FEATURE_BASE = (
    KING_STATE_HALFMOVE_FEATURE_BASE + KING_STATE_HALFMOVE_BINS
)
KING_STATE_PHASE_BINS = 4
KING_STATE_FEATURE_COUNT = KING_STATE_PHASE_FEATURE_BASE + KING_STATE_PHASE_BINS
ACCUMULATOR_SIZE = 128
HIDDEN_SIZE = 32
ACTIVATION_MAX = 127
HIDDEN_DIVISOR = 64
OUTPUT_DIVISOR = 64
PAYLOAD_BYTES = 435620
KING_STATE_PAYLOAD_BYTES = (
    ACCUMULATOR_SIZE * 2
    + KING_STATE_FEATURE_COUNT * ACCUMULATOR_SIZE * 2
    + HIDDEN_SIZE * 4
    + HIDDEN_SIZE * ACCUMULATOR_SIZE * 2
    + 4
    + HIDDEN_SIZE
)
OMEGA_INTERACTION_DEVELOPMENT_FEATURE_BASE = KING_STATE_FEATURE_COUNT
OMEGA_INTERACTION_DEVELOPMENT_FEATURES = 18
OMEGA_INTERACTION_LEAPER_COUNT_FEATURE_BASE = (
    OMEGA_INTERACTION_DEVELOPMENT_FEATURE_BASE
    + OMEGA_INTERACTION_DEVELOPMENT_FEATURES
)
OMEGA_INTERACTION_LEAPER_COUNT_FEATURES = 12
OMEGA_INTERACTION_COEXISTENCE_FEATURE_BASE = (
    OMEGA_INTERACTION_LEAPER_COUNT_FEATURE_BASE
    + OMEGA_INTERACTION_LEAPER_COUNT_FEATURES
)
OMEGA_INTERACTION_COEXISTENCE_FEATURES = 4
OMEGA_INTERACTION_KING_DISTANCE_FEATURE_BASE = (
    OMEGA_INTERACTION_COEXISTENCE_FEATURE_BASE
    + OMEGA_INTERACTION_COEXISTENCE_FEATURES
)
OMEGA_INTERACTION_KING_DISTANCE_FEATURES = 16
OMEGA_INTERACTION_ACTIVATED_WIZARD_FEATURE_BASE = (
    OMEGA_INTERACTION_KING_DISTANCE_FEATURE_BASE
    + OMEGA_INTERACTION_KING_DISTANCE_FEATURES
)
OMEGA_INTERACTION_ACTIVATED_WIZARD_FEATURES = 6
OMEGA_INTERACTION_CHAMPION_PAIR_FEATURE_BASE = (
    OMEGA_INTERACTION_ACTIVATED_WIZARD_FEATURE_BASE
    + OMEGA_INTERACTION_ACTIVATED_WIZARD_FEATURES
)
OMEGA_INTERACTION_CHAMPION_PAIR_FEATURES = 8
OMEGA_INTERACTION_FEATURES = (
    OMEGA_INTERACTION_DEVELOPMENT_FEATURES
    + OMEGA_INTERACTION_LEAPER_COUNT_FEATURES
    + OMEGA_INTERACTION_COEXISTENCE_FEATURES
    + OMEGA_INTERACTION_KING_DISTANCE_FEATURES
    + OMEGA_INTERACTION_ACTIVATED_WIZARD_FEATURES
    + OMEGA_INTERACTION_CHAMPION_PAIR_FEATURES
)
OMEGA_INTERACTION_FEATURE_COUNT = (
    KING_STATE_FEATURE_COUNT + OMEGA_INTERACTION_FEATURES
)
OMEGA_INTERACTION_PAYLOAD_BYTES = (
    ACCUMULATOR_SIZE * 2
    + OMEGA_INTERACTION_FEATURE_COUNT * ACCUMULATOR_SIZE * 2
    + HIDDEN_SIZE * 4
    + HIDDEN_SIZE * ACCUMULATOR_SIZE * 2
    + 4
    + HIDDEN_SIZE
)
OMEGA_INTERACTION_RESIDUAL_LIMIT_CP = 600

FNV64_OFFSET_BASIS = 14695981039346656037
FNV64_PRIME = 1099511628211
FNV64_MASK = (1 << 64) - 1

PIECE_INDEX = {
    "p": 0,
    "n": 1,
    "b": 2,
    "r": 3,
    "q": 4,
    "k": 5,
    "c": 6,
    "w": 7,
}
CASTLING_FEATURE_BASE = 2 * PIECE_COUNT * SQUARE_COUNT
PAD_FEATURE = FEATURE_COUNT

HEADER_STRUCT = struct.Struct("<8s12I2Q")


def is_residual_architecture(architecture: int) -> bool:
    return architecture in (
        ARCHITECTURE_RESIDUAL,
        ARCHITECTURE_KING_STATE_RESIDUAL,
        ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
    )


def feature_count_for_architecture(architecture: int) -> int:
    if architecture in (ARCHITECTURE_ABSOLUTE, ARCHITECTURE_RESIDUAL):
        return FEATURE_COUNT
    if architecture == ARCHITECTURE_KING_STATE_RESIDUAL:
        return KING_STATE_FEATURE_COUNT
    if architecture == ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL:
        return OMEGA_INTERACTION_FEATURE_COUNT
    raise ValueError(f"unsupported architecture semantics {architecture}")


def payload_bytes_for_architecture(architecture: int) -> int:
    if architecture in (ARCHITECTURE_ABSOLUTE, ARCHITECTURE_RESIDUAL):
        return PAYLOAD_BYTES
    if architecture == ARCHITECTURE_KING_STATE_RESIDUAL:
        return KING_STATE_PAYLOAD_BYTES
    if architecture == ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL:
        return OMEGA_INTERACTION_PAYLOAD_BYTES
    raise ValueError(f"unsupported architecture semantics {architecture}")


def fnv1a64(data: bytes) -> int:
    value = FNV64_OFFSET_BASIS
    for byte in data:
        value ^= byte
        value = (value * FNV64_PRIME) & FNV64_MASK
    return value


def _atomic_write(path: Path, data: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _little_bytes(values: np.ndarray, dtype: str) -> bytes:
    return np.ascontiguousarray(values, dtype=np.dtype(dtype)).tobytes(order="C")


def _divide_round(values: np.ndarray, divisor: int) -> np.ndarray:
    """Integer division matching Senpai's nearest, sign-symmetric rounding."""
    values = np.asarray(values, dtype=np.int64)
    return np.where(
        values >= 0,
        (values + divisor // 2) // divisor,
        -((-values + divisor // 2) // divisor),
    )


@dataclass(frozen=True)
class QuantizedNetwork:
    ft_bias: np.ndarray
    ft_weights: np.ndarray
    dense_bias: np.ndarray
    dense_weights: np.ndarray
    output_bias: int
    output_weights: np.ndarray
    architecture: int = ARCHITECTURE_ABSOLUTE

    def validate(self) -> None:
        if self.architecture not in ARCHITECTURE_NAMES:
            raise ValueError(
                f"unsupported architecture semantics {self.architecture}"
            )
        feature_count = feature_count_for_architecture(self.architecture)
        expected = (
            ("ft_bias", self.ft_bias, (ACCUMULATOR_SIZE,), np.int16),
            (
                "ft_weights",
                self.ft_weights,
                (feature_count, ACCUMULATOR_SIZE),
                np.int16,
            ),
            ("dense_bias", self.dense_bias, (HIDDEN_SIZE,), np.int32),
            (
                "dense_weights",
                self.dense_weights,
                (HIDDEN_SIZE, ACCUMULATOR_SIZE * 2),
                np.int8,
            ),
            ("output_weights", self.output_weights, (HIDDEN_SIZE,), np.int8),
        )
        for name, value, shape, dtype in expected:
            if value.shape != shape:
                raise ValueError(f"{name} has shape {value.shape}, expected {shape}")
            if value.dtype != dtype:
                raise ValueError(f"{name} has dtype {value.dtype}, expected {dtype}")
        if not -(1 << 31) <= int(self.output_bias) < (1 << 31):
            raise ValueError("output_bias is outside int32 range")

    def payload(self) -> bytes:
        self.validate()
        payload_bytes = payload_bytes_for_architecture(self.architecture)
        payload = b"".join(
            (
                _little_bytes(self.ft_bias, "<i2"),
                _little_bytes(self.ft_weights, "<i2"),
                _little_bytes(self.dense_bias, "<i4"),
                _little_bytes(self.dense_weights, "i1"),
                struct.pack("<i", int(self.output_bias)),
                _little_bytes(self.output_weights, "i1"),
            )
        )
        if len(payload) != payload_bytes:
            raise AssertionError(
                f"payload is {len(payload)} bytes, expected {payload_bytes}"
            )
        return payload

    def to_bytes(self) -> bytes:
        payload = self.payload()
        feature_count = feature_count_for_architecture(self.architecture)
        payload_bytes = payload_bytes_for_architecture(self.architecture)
        header = HEADER_STRUCT.pack(
            MAGIC,
            ENDIAN_TAG,
            FORMAT_VERSION,
            HEADER_BYTES,
            self.architecture,
            SQUARE_COUNT,
            PIECE_COUNT,
            feature_count,
            ACCUMULATOR_SIZE,
            HIDDEN_SIZE,
            ACTIVATION_MAX,
            HIDDEN_DIVISOR,
            OUTPUT_DIVISOR,
            payload_bytes,
            fnv1a64(payload),
        )
        if len(header) != HEADER_BYTES:
            raise AssertionError("internal header-size error")
        return header + payload

    def write(self, path: Path) -> None:
        _atomic_write(path, self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> "QuantizedNetwork":
        if len(data) < HEADER_BYTES:
            raise ValueError(
                f"network is {len(data)} bytes; shorter than the {HEADER_BYTES}-byte header"
            )
        fields = HEADER_STRUCT.unpack_from(data)
        (
            magic,
            endian,
            version,
            header_bytes,
            architecture,
            squares,
            pieces,
            features,
            accumulator,
            hidden,
            activation_max,
            hidden_divisor,
            output_divisor,
            payload_bytes,
            payload_hash,
        ) = fields
        if architecture not in ARCHITECTURE_NAMES:
            raise ValueError(
                f"unsupported architecture semantics {architecture}"
            )
        expected_feature_count = feature_count_for_architecture(architecture)
        expected_payload_bytes = payload_bytes_for_architecture(architecture)
        expected = (
            (magic, MAGIC, "magic"),
            (endian, ENDIAN_TAG, "endian tag"),
            (version, FORMAT_VERSION, "format version"),
            (header_bytes, HEADER_BYTES, "header size"),
            (squares, SQUARE_COUNT, "square count"),
            (pieces, PIECE_COUNT, "piece count"),
            (features, expected_feature_count, "feature count"),
            (accumulator, ACCUMULATOR_SIZE, "accumulator size"),
            (hidden, HIDDEN_SIZE, "hidden size"),
            (activation_max, ACTIVATION_MAX, "activation maximum"),
            (hidden_divisor, HIDDEN_DIVISOR, "hidden divisor"),
            (output_divisor, OUTPUT_DIVISOR, "output divisor"),
            (payload_bytes, expected_payload_bytes, "payload size"),
        )
        for actual, wanted, label in expected:
            if actual != wanted:
                raise ValueError(f"bad {label}: {actual!r}; expected {wanted!r}")
        if len(data) != HEADER_BYTES + expected_payload_bytes:
            raise ValueError(
                f"network is {len(data)} bytes; expected "
                f"{HEADER_BYTES + expected_payload_bytes}"
            )

        payload = data[HEADER_BYTES:]
        actual_hash = fnv1a64(payload)
        if actual_hash != payload_hash:
            raise ValueError(
                f"payload FNV-1a mismatch: {actual_hash:016x}; "
                f"expected {payload_hash:016x}"
            )

        offset = 0

        def take(count: int, dtype: str) -> np.ndarray:
            nonlocal offset
            item_size = np.dtype(dtype).itemsize
            size = count * item_size
            result = np.frombuffer(payload, dtype=np.dtype(dtype), count=count, offset=offset)
            offset += size
            return result.copy()

        ft_bias = take(ACCUMULATOR_SIZE, "<i2").astype(np.int16, copy=False)
        ft_weights = take(expected_feature_count * ACCUMULATOR_SIZE, "<i2")
        ft_weights = ft_weights.reshape(
            expected_feature_count, ACCUMULATOR_SIZE
        ).astype(np.int16, copy=False)
        dense_bias = take(HIDDEN_SIZE, "<i4").astype(np.int32, copy=False)
        dense_weights = take(HIDDEN_SIZE * ACCUMULATOR_SIZE * 2, "i1")
        dense_weights = dense_weights.reshape(
            HIDDEN_SIZE, ACCUMULATOR_SIZE * 2
        ).astype(np.int8, copy=False)
        output_bias = int(take(1, "<i4")[0])
        output_weights = take(HIDDEN_SIZE, "i1").astype(np.int8, copy=False)
        if offset != expected_payload_bytes:
            raise AssertionError("internal payload-offset error")
        result = cls(
            ft_bias=ft_bias,
            ft_weights=ft_weights,
            dense_bias=dense_bias,
            dense_weights=dense_weights,
            output_bias=output_bias,
            output_weights=output_weights,
            architecture=architecture,
        )
        result.validate()
        return result

    @classmethod
    def read(cls, path: Path) -> "QuantizedNetwork":
        return cls.from_bytes(path.read_bytes())

    def predict_features(
        self,
        stm_features: np.ndarray,
        opponent_features: np.ndarray,
    ) -> np.ndarray:
        """Evaluate padded perspective-feature matrices exactly like the engine."""
        if stm_features.shape != opponent_features.shape:
            raise ValueError("perspective feature matrices have different shapes")
        if stm_features.ndim != 2:
            raise ValueError("feature matrices must be two-dimensional")
        weights = np.vstack(
            (
                self.ft_weights.astype(np.int64),
                np.zeros((1, ACCUMULATOR_SIZE), dtype=np.int64),
            )
        )
        stm_acc = self.ft_bias.astype(np.int64) + weights[stm_features].sum(axis=1)
        opp_acc = self.ft_bias.astype(np.int64) + weights[opponent_features].sum(
            axis=1
        )
        ft = np.concatenate(
            (
                np.clip(stm_acc, 0, ACTIVATION_MAX),
                np.clip(opp_acc, 0, ACTIVATION_MAX),
            ),
            axis=1,
        )
        dense_raw = (
            self.dense_bias.astype(np.int64)
            + ft @ self.dense_weights.astype(np.int64).T
        )
        dense = np.clip(
            _divide_round(dense_raw, HIDDEN_DIVISOR),
            0,
            ACTIVATION_MAX,
        )
        output_raw = int(self.output_bias) + dense @ self.output_weights.astype(
            np.int64
        )
        result = _divide_round(output_raw, OUTPUT_DIVISOR)
        if self.architecture == ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL:
            result = np.clip(
                result,
                -OMEGA_INTERACTION_RESIDUAL_LIMIT_CP,
                OMEGA_INTERACTION_RESIDUAL_LIMIT_CP,
            )
        return result.astype(np.int32)


def _parse_rank(rank_text: str) -> list[str | None]:
    squares: list[str | None] = []
    index = 0
    while index < len(rank_text):
        character = rank_text[index]
        if character.isdigit():
            end = index + 1
            while end < len(rank_text) and rank_text[end].isdigit():
                end += 1
            empty = int(rank_text[index:end])
            if empty <= 0:
                raise ValueError(f"invalid empty-square count in rank {rank_text!r}")
            squares.extend([None] * empty)
            index = end
            continue
        if character.lower() not in PIECE_INDEX:
            raise ValueError(f"unknown piece {character!r} in rank {rank_text!r}")
        squares.append(character)
        index += 1
    if len(squares) != 10:
        raise ValueError(f"rank {rank_text!r} expands to {len(squares)}, not 10")
    return squares


def _ofen_regular_square(text: str) -> int:
    if len(text) != 2:
        raise ValueError(f"invalid OFEN square {text!r}")
    file = ord(text[0].lower()) - ord("a")
    rank = ord(text[1]) - ord("0")
    if not 0 <= file < 10 or not 0 <= rank < 10:
        raise ValueError(f"invalid OFEN square {text!r}")
    return file * 10 + rank


def _parse_ofen_extended(
    ofen: str,
) -> tuple[list[tuple[int, int, int]], str, str, tuple[int, ...], int]:
    """Return every position field consumed by an OMNNUE architecture."""

    fields = ofen.split()
    if len(fields) != 6:
        raise ValueError("Omega OFEN must contain exactly six fields")
    placement, side_to_move, castling, ep_text, halfmove_text, fullmove_text = fields
    if side_to_move not in ("w", "b"):
        raise ValueError(f"invalid side-to-move field {side_to_move!r}")
    bracket = placement.find("[")
    if bracket < 0 or not placement.endswith("]"):
        raise ValueError("Omega OFEN placement is missing detached corners")
    rank_texts = placement[:bracket].split("/")
    corner_texts = placement[bracket + 1 : -1].split("/")
    if len(rank_texts) != 10:
        raise ValueError("Omega OFEN must contain ten regular ranks")
    if len(corner_texts) != 4:
        raise ValueError("Omega OFEN must contain four detached corners")

    pieces: list[tuple[int, int, int]] = []
    for text_index, rank_text in enumerate(rank_texts):
        rank = 9 - text_index
        for file, character in enumerate(_parse_rank(rank_text)):
            if character is None:
                continue
            piece = PIECE_INDEX[character.lower()]
            side = 0 if character.isupper() else 1
            pieces.append((piece, side, file * 10 + rank))
    for corner, character in enumerate(corner_texts):
        if character == "-":
            continue
        if len(character) != 1 or character.lower() not in PIECE_INDEX:
            raise ValueError(f"invalid detached-corner token {character!r}")
        piece = PIECE_INDEX[character.lower()]
        side = 0 if character.isupper() else 1
        pieces.append((piece, side, 100 + corner))

    if castling != "-":
        unknown = set(castling) - set("KQkq")
        if unknown:
            raise ValueError(f"invalid castling rights {castling!r}")
        if len(set(castling)) != len(castling):
            raise ValueError(f"duplicate castling rights {castling!r}")

    if ep_text == "-":
        ep_squares: tuple[int, ...] = ()
    else:
        ep_parts = ep_text.split(",")
        if not 1 <= len(ep_parts) <= 2 or any(not item for item in ep_parts):
            raise ValueError(f"invalid en-passant field {ep_text!r}")
        ep_squares = tuple(_ofen_regular_square(item) for item in ep_parts)
        if len(set(ep_squares)) != len(ep_squares):
            raise ValueError(f"duplicate en-passant targets {ep_text!r}")
        if len(ep_squares) == 2:
            first_file, first_rank = divmod(ep_squares[0], 10)
            second_file, second_rank = divmod(ep_squares[1], 10)
            if first_file != second_file or abs(first_rank - second_rank) != 1:
                raise ValueError(
                    "two Omega en-passant targets must be adjacent on one file"
                )

    try:
        halfmove_clock = int(halfmove_text)
        fullmove_number = int(fullmove_text)
    except ValueError as error:
        raise ValueError("OFEN move counters must be integers") from error
    if halfmove_clock < 0:
        raise ValueError("OFEN halfmove clock cannot be negative")
    if fullmove_number < 1:
        raise ValueError("OFEN fullmove number must be positive")
    return pieces, side_to_move, castling, ep_squares, halfmove_clock


def parse_ofen(ofen: str) -> tuple[list[tuple[int, int, int]], str, str]:
    """Return triples, side to move, and castling (legacy public contract)."""

    pieces, side_to_move, castling, _, _ = _parse_ofen_extended(ofen)
    return pieces, side_to_move, castling


def orient_square(square: int, perspective: int) -> int:
    if not 0 <= square < SQUARE_COUNT:
        raise ValueError(f"invalid square index {square}")
    if perspective == 0:
        return square
    if perspective != 1:
        raise ValueError(f"invalid perspective {perspective}")
    if square < 100:
        file, rank = divmod(square, 10)
        return file * 10 + (9 - rank)
    return 100 + (3 - (square - 100))


def king_bucket(square: int, perspective: int) -> int:
    """Map the friendly king to 25 regular 2x2 or four corner buckets."""

    oriented = orient_square(square, perspective)
    if oriented < 100:
        file, rank = divmod(oriented, 10)
        return (file // 2) * 5 + rank // 2
    return 25 + (oriented - 100)


def halfmove_clock_bin(halfmove_clock: int) -> int:
    """Return one of eight bins, with extra resolution near the draw limit."""

    if halfmove_clock < 0:
        raise ValueError("halfmove clock cannot be negative")
    boundaries = (1, 4, 16, 32, 50, 75, 90)
    return sum(halfmove_clock >= boundary for boundary in boundaries)


def material_phase_bin(pieces: Sequence[tuple[int, int, int]]) -> int:
    """Bucket the remaining non-pawn force using Senpai's Omega phase weights."""

    weights = {
        PIECE_INDEX["n"]: 1,
        PIECE_INDEX["b"]: 1,
        PIECE_INDEX["c"]: 1,
        PIECE_INDEX["w"]: 1,
        PIECE_INDEX["r"]: 2,
        PIECE_INDEX["q"]: 4,
    }
    remaining = min(32, sum(weights.get(piece, 0) for piece, _, _ in pieces))
    if remaining >= 24:
        return 0
    if remaining >= 16:
        return 1
    if remaining >= 8:
        return 2
    return 3


_CHAMPION_DELTAS = (
    (+1, 0),
    (-1, 0),
    (0, +1),
    (0, -1),
    (+2, 0),
    (-2, 0),
    (0, +2),
    (0, -2),
    (+2, +2),
    (+2, -2),
    (-2, +2),
    (-2, -2),
)
_WIZARD_DELTAS = (
    (+1, +1),
    (+1, -1),
    (-1, +1),
    (-1, -1),
    (+1, +3),
    (+1, -3),
    (-1, +3),
    (-1, -3),
    (+3, +1),
    (+3, -1),
    (-3, +1),
    (-3, -1),
)
_CORNER_COORDINATES = ((-1, -1), (10, -1), (10, 10), (-1, 10))
_COORDINATE_CORNERS = {
    coordinates: 100 + corner
    for corner, coordinates in enumerate(_CORNER_COORDINATES)
}


def _square_coordinates(square: int) -> tuple[int, int]:
    if not 0 <= square < SQUARE_COUNT:
        raise ValueError(f"invalid square index {square}")
    if square < 100:
        return divmod(square, 10)
    return _CORNER_COORDINATES[square - 100]


def _square_from_coordinates(file: int, rank: int) -> int | None:
    if 0 <= file < 10 and 0 <= rank < 10:
        return file * 10 + rank
    return _COORDINATE_CORNERS.get((file, rank))


@lru_cache(maxsize=2)
def _empty_board_leaper_distances(piece: int) -> tuple[tuple[int, ...], ...]:
    if piece == PIECE_INDEX["c"]:
        deltas = _CHAMPION_DELTAS
    elif piece == PIECE_INDEX["w"]:
        deltas = _WIZARD_DELTAS
    else:
        raise ValueError("empty-board distance requires Champion or Wizard")

    unreachable = SQUARE_COUNT + 1
    rows: list[tuple[int, ...]] = []
    for source in range(SQUARE_COUNT):
        distances = [unreachable] * SQUARE_COUNT
        distances[source] = 0
        pending: deque[int] = deque((source,))
        while pending:
            square = pending.popleft()
            file, rank = _square_coordinates(square)
            next_distance = distances[square] + 1
            for file_delta, rank_delta in deltas:
                target = _square_from_coordinates(
                    file + file_delta, rank + rank_delta
                )
                if target is None or distances[target] != unreachable:
                    continue
                distances[target] = next_distance
                pending.append(target)
        rows.append(tuple(distances))
    return tuple(rows)


def empty_board_leaper_distance(piece: int, source: int, target: int) -> int:
    """Return exact distance on the empty 104-square C/W movement graph."""

    if not 0 <= source < SQUARE_COUNT or not 0 <= target < SQUARE_COUNT:
        raise ValueError("empty-board distance square is out of range")
    return _empty_board_leaper_distances(piece)[source][target]


def _undeveloped_units(
    pieces: Sequence[tuple[int, int, int]], side: int
) -> int:
    developed = 0
    home_rank_pieces = {
        PIECE_INDEX["n"],
        PIECE_INDEX["b"],
        PIECE_INDEX["c"],
    }
    for piece, piece_side, square in pieces:
        if piece_side != side:
            continue
        if piece in home_rank_pieces:
            if square >= 100:
                developed += 1
            else:
                _, rank = divmod(square, 10)
                relative_rank = rank if side == 0 else 9 - rank
                if relative_rank != 0:
                    developed += 1
        elif piece == PIECE_INDEX["w"] and square < 100:
            developed += 1
    return max(0, 8 - developed)


def _interaction_features(
    pieces: Sequence[tuple[int, int, int]], perspective: int
) -> list[int]:
    by_piece_side: dict[tuple[int, int], list[int]] = {}
    kings: list[list[int]] = [[], []]
    for piece, side, square in pieces:
        by_piece_side.setdefault((piece, side), []).append(square)
        if piece == PIECE_INDEX["k"]:
            kings[side].append(square)

    result: list[int] = []
    champion = PIECE_INDEX["c"]
    wizard = PIECE_INDEX["w"]
    leapers = (champion, wizard)

    for relation in (0, 1):
        side = perspective if relation == 0 else 1 - perspective
        result.append(
            OMEGA_INTERACTION_DEVELOPMENT_FEATURE_BASE
            + relation * 9
            + _undeveloped_units(pieces, side)
        )

    for relation in (0, 1):
        side = perspective if relation == 0 else 1 - perspective
        for kind, piece in enumerate(leapers):
            count = min(len(by_piece_side.get((piece, side), ())), 2)
            group = relation * 2 + kind
            result.append(
                OMEGA_INTERACTION_LEAPER_COUNT_FEATURE_BASE
                + group * 3
                + count
            )

    for relation in (0, 1):
        side = perspective if relation == 0 else 1 - perspective
        coexist = bool(by_piece_side.get((champion, side))) and bool(
            by_piece_side.get((wizard, side))
        )
        result.append(
            OMEGA_INTERACTION_COEXISTENCE_FEATURE_BASE
            + relation * 2
            + int(coexist)
        )

    for relation in (0, 1):
        side = perspective if relation == 0 else 1 - perspective
        defender = 1 - side
        if len(kings[defender]) != 1:
            raise ValueError(
                "Omega-interaction NNUE requires one opposing king"
            )
        target = kings[defender][0]
        for kind, piece in enumerate(leapers):
            sources = by_piece_side.get((piece, side), ())
            if not sources:
                bin_index = 0
            else:
                distance = min(
                    empty_board_leaper_distance(piece, source, target)
                    for source in sources
                )
                bin_index = 1 if distance <= 1 else 2 if distance == 2 else 3
            group = relation * 2 + kind
            result.append(
                OMEGA_INTERACTION_KING_DISTANCE_FEATURE_BASE
                + group * 4
                + bin_index
            )

    wizard_home = ({100, 101}, {102, 103})
    for relation in (0, 1):
        side = perspective if relation == 0 else 1 - perspective
        activated = sum(
            square not in wizard_home[side]
            for square in by_piece_side.get((wizard, side), ())
        )
        result.append(
            OMEGA_INTERACTION_ACTIVATED_WIZARD_FEATURE_BASE
            + relation * 3
            + min(activated, 2)
        )

    for relation in (0, 1):
        side = perspective if relation == 0 else 1 - perspective
        champions = by_piece_side.get((champion, side), ())
        if len(champions) < 2:
            bin_index = 0
        else:
            distance = min(
                empty_board_leaper_distance(champion, left, right)
                for index, left in enumerate(champions)
                for right in champions[index + 1 :]
            )
            bin_index = 1 if distance <= 1 else 2 if distance == 2 else 3
        result.append(
            OMEGA_INTERACTION_CHAMPION_PAIR_FEATURE_BASE
            + relation * 4
            + bin_index
        )

    if len(result) != 16:
        raise AssertionError("Omega interaction feature-group count changed")
    return result


def _active_features_from_parsed(
    pieces: Sequence[tuple[int, int, int]],
    castling: str,
    perspective: int,
    ep_squares: Sequence[int] = (),
    halfmove_clock: int = 0,
    architecture: int = ARCHITECTURE_ABSOLUTE,
) -> tuple[int, ...]:
    if perspective not in (0, 1):
        raise ValueError(f"invalid perspective {perspective}")
    feature_count = feature_count_for_architecture(architecture)

    # Pos stores a castling rook square, then runtime feature extraction
    # intersects that metadata with a real same-side Rook and determines its
    # flank relative to the king.  Replicate that behavior instead of blindly
    # treating a stale K/Q character as an active feature.
    by_side: list[dict[int, int]] = [{}, {}]
    kings: list[list[int]] = [[], []]
    for piece, side, square in pieces:
        by_side[side][square] = piece
        if piece == PIECE_INDEX["k"]:
            kings[side].append(square)

    result: list[int] = []
    king_state = architecture in (
        ARCHITECTURE_KING_STATE_RESIDUAL,
        ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
    )
    if king_state:
        if len(kings[perspective]) != 1:
            raise ValueError(
                "king-state NNUE requires exactly one friendly king per perspective"
            )
        bucket = king_bucket(kings[perspective][0], perspective)
        for piece, side, square in pieces:
            relation = 0 if side == perspective else 1
            base = (
                (relation * PIECE_COUNT + piece) * SQUARE_COUNT
                + orient_square(square, perspective)
            )
            result.append(bucket * (2 * PIECE_COUNT * SQUARE_COUNT) + base)
        castling_feature_base = KING_STATE_CASTLING_FEATURE_BASE
    else:
        for piece, side, square in pieces:
            relation = 0 if side == perspective else 1
            result.append(
                (relation * PIECE_COUNT + piece) * SQUARE_COUNT
                + orient_square(square, perspective)
            )
        castling_feature_base = CASTLING_FEATURE_BASE

    castling_rooks = (
        # side, right character, fixed Omega castling-rook square
        (0, "K", 80),
        (0, "Q", 10),
        (1, "k", 89),
        (1, "q", 19),
    )
    for side in (0, 1):
        if not kings[side]:
            continue
        king_file = kings[side][0] // 10
        flanks: set[bool] = set()
        for rook_side, right, square in castling_rooks:
            if (
                rook_side == side
                and right in castling
                and by_side[side].get(square) == PIECE_INDEX["r"]
            ):
                rook_file = square // 10
                if rook_file != king_file:
                    flanks.add(rook_file > king_file)
        for right_of_king in sorted(flanks):
            relation = 0 if side == perspective else 1
            result.append(
                castling_feature_base
                + relation * 2
                + (1 if right_of_king else 0)
            )

    if king_state:
        for square in ep_squares:
            if not 0 <= square < 100:
                raise ValueError("en-passant target must be on the regular board")
            result.append(
                KING_STATE_EP_FEATURE_BASE
                + orient_square(square, perspective)
            )
        result.append(
            KING_STATE_HALFMOVE_FEATURE_BASE
            + halfmove_clock_bin(halfmove_clock)
        )
        result.append(
            KING_STATE_PHASE_FEATURE_BASE + material_phase_bin(pieces)
        )

    if architecture == ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL:
        result.extend(_interaction_features(pieces, perspective))

    if len(set(result)) != len(result):
        raise ValueError("OFEN produced duplicate NNUE features")
    if any(feature < 0 or feature >= feature_count for feature in result):
        raise AssertionError("internal feature-index error")
    return tuple(sorted(result))


def active_features(
    ofen: str,
    perspective: int,
    architecture: int = ARCHITECTURE_ABSOLUTE,
) -> tuple[int, ...]:
    pieces, _, castling, ep_squares, halfmove_clock = _parse_ofen_extended(ofen)
    return _active_features_from_parsed(
        pieces,
        castling,
        perspective,
        ep_squares,
        halfmove_clock,
        architecture,
    )


def nnue_input_signature(
    white_features: Sequence[int],
    black_features: Sequence[int],
    side_to_move_white: bool,
    architecture: int = ARCHITECTURE_ABSOLUTE,
) -> str:
    """Hash the exact ordered pair of feature sets presented to the network.

    This deliberately ignores OFEN state that the frozen v1 architecture
    cannot observe (for example move counters and en-passant metadata).  It
    therefore catches leakage that a text-OFEN duplicate check would miss.
    """

    stm = white_features if side_to_move_white else black_features
    opponent = black_features if side_to_move_white else white_features
    digest = hashlib.sha256()
    feature_count_for_architecture(architecture)
    digest.update(b"OMNNUE1-input\0")
    # Preserve every existing architecture-1/2 signature. Architectures 3/4
    # have distinct feature namespaces and therefore receive explicit tags.
    if architecture in (
        ARCHITECTURE_KING_STATE_RESIDUAL,
        ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
    ):
        digest.update(struct.pack("<I", architecture))
    for perspective in (stm, opponent):
        if len(perspective) > 0xFFFF:
            raise ValueError("NNUE feature set is too large to sign")
        digest.update(struct.pack("<H", len(perspective)))
        digest.update(
            np.asarray(tuple(perspective), dtype=np.dtype("<u2")).tobytes(
                order="C"
            )
        )
    return digest.hexdigest()


def _finite_number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number or null")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _nested(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for component in path.split("."):
        if not isinstance(value, dict) or component not in value:
            return None
        value = value[component]
    return value


def group_id(record: dict[str, Any], ofen: str, explicit_field: str | None) -> str:
    if explicit_field:
        value = _nested(record, explicit_field)
        if value is None or str(value).strip() == "":
            raise ValueError(f"record is missing group field {explicit_field!r}")
        return str(value)
    candidates = (
        "groupId",
        "splitGroup",
        "pairId",
        "provenance.pairId",
        "gameId",
        "provenance.gameId",
        "sourceFamily",
        "sampleId",
    )
    for candidate in candidates:
        value = _nested(record, candidate)
        if value is not None and str(value).strip() != "":
            return f"{candidate}:{value}"
    normalized = " ".join(ofen.split())
    return "position:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def deterministic_split(
    group: str,
    seed: int,
    train_percent: float,
    validation_percent: float,
) -> int:
    digest = hashlib.sha256(f"{seed}\0{group}".encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:8], "big") / float(1 << 64)
    if fraction < train_percent / 100.0:
        return 0
    if fraction < (train_percent + validation_percent) / 100.0:
        return 1
    return 2


@dataclass
class Dataset:
    white_features: np.ndarray
    black_features: np.ndarray
    side_to_move_white: np.ndarray
    target_cp: np.ndarray
    outcome: np.ndarray
    search_outcome: np.ndarray
    handcrafted_cp: np.ndarray
    split: np.ndarray
    groups: list[str]
    sample_ids: list[str]
    records_read: int
    records_skipped: int
    collision_policy: str
    input_collision_signatures: int
    input_collision_rows: int
    input_collision_rows_dropped: int
    input_collision_dropped_by_split: tuple[int, int, int]
    input_collision_examples: list[dict[str, Any]]
    architecture: int = ARCHITECTURE_ABSOLUTE

    @property
    def count(self) -> int:
        return int(self.side_to_move_white.size)

    @property
    def width(self) -> int:
        return int(self.white_features.shape[1])

    @property
    def pad_feature(self) -> int:
        return feature_count_for_architecture(self.architecture)

    def indices(self, split: int) -> np.ndarray:
        return np.flatnonzero(self.split == split)

    def perspective_features(
        self, indices: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        stm_white = self.side_to_move_white[indices, None]
        white = self.white_features[indices]
        black = self.black_features[indices]
        return np.where(stm_white, white, black), np.where(stm_white, black, white)


def _iter_jsonl(paths: Sequence[Path]) -> Iterator[tuple[Path, int, dict[str, Any]]]:
    for path in paths:
        with path.open("r", encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {error}") from error
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{line_number}: JSONL row is not an object")
                yield path, line_number, value


def load_dataset(
    paths: Sequence[Path],
    *,
    seed: int,
    train_percent: float,
    validation_percent: float,
    explicit_group_field: str | None = None,
    max_records: int | None = None,
    strict: bool = False,
    all_train: bool = False,
    collision_policy: str = "drop",
    residual_outcomes: bool = False,
    architecture: int = ARCHITECTURE_ABSOLUTE,
) -> Dataset:
    if not paths:
        raise ValueError("at least one JSONL input is required")
    if not 0.0 <= train_percent <= 100.0:
        raise ValueError("train percent is outside 0..100")
    if not 0.0 <= validation_percent <= 100.0:
        raise ValueError("validation percent is outside 0..100")
    if train_percent + validation_percent > 100.0:
        raise ValueError("train and validation percentages exceed 100")
    if max_records is not None and max_records <= 0:
        raise ValueError("max_records must be positive")
    if collision_policy not in ("drop", "error", "allow"):
        raise ValueError("collision_policy must be drop, error, or allow")
    feature_count = feature_count_for_architecture(architecture)

    white_rows: list[tuple[int, ...]] = []
    black_rows: list[tuple[int, ...]] = []
    stm_rows: list[bool] = []
    cp_rows: list[float] = []
    outcome_rows: list[float] = []
    search_outcome_rows: list[float] = []
    handcrafted_cp_rows: list[float] = []
    split_rows: list[int] = []
    groups: list[str] = []
    sample_ids: list[str] = []
    records_read = 0
    records_skipped = 0

    for path, line_number, record in _iter_jsonl(paths):
        records_read += 1
        location = f"{path}:{line_number}"
        try:
            ofen_value = record.get("ofen")
            if not isinstance(ofen_value, str) or not ofen_value.strip():
                raise ValueError("missing nonempty 'ofen'")
            ofen = " ".join(ofen_value.split())
            (
                pieces,
                ofen_side,
                castling,
                ep_squares,
                halfmove_clock,
            ) = _parse_ofen_extended(ofen)
            record_side = record.get("sideToMove")
            if record_side is not None:
                normalized_side = str(record_side).strip().lower()
                normalized_side = {"white": "w", "black": "b"}.get(
                    normalized_side, normalized_side
                )
                if normalized_side != ofen_side:
                    raise ValueError(
                        f"sideToMove {record_side!r} disagrees with OFEN"
                    )

            cp = _finite_number(record.get("targetCpStm"), "targetCpStm")
            if cp is None:
                cp = _finite_number(record.get("searchCpStm"), "searchCpStm")
            outcome = _finite_number(record.get("outcomeStm"), "outcomeStm")
            if outcome is None:
                outcome = _finite_number(
                    record.get("sideToMoveScore"), "sideToMoveScore"
                )
            if outcome is not None and not 0.0 <= outcome <= 1.0:
                raise ValueError("outcome target is outside 0..1")
            if residual_outcomes:
                search_outcome = _finite_number(
                    record.get("searchOutcomeStm"), "searchOutcomeStm"
                )
                search_side_score = _finite_number(
                    record.get("searchSideToMoveScore"),
                    "searchSideToMoveScore",
                )
                if (
                    search_outcome is not None
                    and search_side_score is not None
                    and search_outcome != search_side_score
                ):
                    raise ValueError(
                        "searchOutcomeStm and searchSideToMoveScore disagree"
                    )
                if search_outcome is None:
                    search_outcome = search_side_score
                if (
                    search_outcome is not None
                    and not 0.0 <= search_outcome <= 1.0
                ):
                    raise ValueError("search outcome target is outside 0..1")
                handcrafted_cp = _finite_number(
                    record.get("handcraftedCpStm"), "handcraftedCpStm"
                )
                if search_outcome is not None and handcrafted_cp is None:
                    raise ValueError(
                        "search outcome supervision requires handcraftedCpStm"
                    )
            else:
                search_outcome = None
                handcrafted_cp = None
            if cp is None and outcome is None:
                records_skipped += 1
                if strict:
                    raise ValueError(
                        "record has neither a CP nor outcome target"
                    )
                continue

            group = group_id(record, ofen, explicit_group_field)
            normalized_key = " ".join(ofen.split())
            supplied_sample = record.get("sampleId")
            sample = (
                str(supplied_sample)
                if supplied_sample is not None
                else hashlib.sha256(
                    f"{group}\0{normalized_key}".encode("utf-8")
                ).hexdigest()
            )
            white_rows.append(
                _active_features_from_parsed(
                    pieces,
                    castling,
                    0,
                    ep_squares,
                    halfmove_clock,
                    architecture,
                )
            )
            black_rows.append(
                _active_features_from_parsed(
                    pieces,
                    castling,
                    1,
                    ep_squares,
                    halfmove_clock,
                    architecture,
                )
            )
            stm_rows.append(ofen_side == "w")
            cp_rows.append(float("nan") if cp is None else cp)
            outcome_rows.append(float("nan") if outcome is None else outcome)
            search_outcome_rows.append(
                float("nan") if search_outcome is None else search_outcome
            )
            handcrafted_cp_rows.append(
                float("nan") if handcrafted_cp is None else handcrafted_cp
            )
            split_rows.append(
                0
                if all_train
                else deterministic_split(
                    group, seed, train_percent, validation_percent
                )
            )
            groups.append(group)
            sample_ids.append(sample)
        except ValueError as error:
            raise ValueError(f"{location}: {error}") from error
        if max_records is not None and len(stm_rows) >= max_records:
            break

    if not stm_rows:
        raise ValueError("no labeled records were loaded")

    # The deterministic split is group-based, so every retained row from a
    # game remains in one split.  A second, feature-level pass catches exact
    # inputs that crossed splits under different group identities.  This is
    # intentionally performed before padding, over the frozen network input.
    group_splits: dict[str, int] = {}
    for group, split in zip(groups, split_rows):
        previous = group_splits.setdefault(group, split)
        if previous != split:
            raise AssertionError(f"group {group!r} crossed dataset splits")

    signature_rows: dict[str, list[int]] = {}
    for index, (white_row, black_row, stm_white) in enumerate(
        zip(white_rows, black_rows, stm_rows)
    ):
        signature = nnue_input_signature(
            white_row, black_row, stm_white, architecture
        )
        signature_rows.setdefault(signature, []).append(index)
    collisions = {
        signature: indices
        for signature, indices in signature_rows.items()
        if len({split_rows[index] for index in indices}) > 1
    }
    collision_rows = sum(len(indices) for indices in collisions.values())
    collision_examples = []
    for signature, indices in sorted(collisions.items())[:16]:
        counts = [0, 0, 0]
        for index in indices:
            counts[split_rows[index]] += 1
        collision_examples.append(
            {
                "signature": signature,
                "rows": len(indices),
                "train": counts[0],
                "validation": counts[1],
                "test": counts[2],
            }
        )
    if collisions and collision_policy == "error":
        raise ValueError(
            "identical NNUE inputs crossed dataset splits: "
            f"{len(collisions)} signatures, {collision_rows} rows"
        )

    dropped_by_split = [0, 0, 0]
    if collisions and collision_policy == "drop":
        keep = [True] * len(stm_rows)
        for indices in collisions.values():
            # Split numbers encode the frozen priority: train (0), then
            # validation (1), then test (2).
            canonical_split = min(split_rows[index] for index in indices)
            for index in indices:
                if split_rows[index] != canonical_split:
                    keep[index] = False
                    dropped_by_split[split_rows[index]] += 1

        def retain(rows: list[Any]) -> list[Any]:
            return [row for row, wanted in zip(rows, keep) if wanted]

        white_rows = retain(white_rows)
        black_rows = retain(black_rows)
        stm_rows = retain(stm_rows)
        cp_rows = retain(cp_rows)
        outcome_rows = retain(outcome_rows)
        search_outcome_rows = retain(search_outcome_rows)
        handcrafted_cp_rows = retain(handcrafted_cp_rows)
        split_rows = retain(split_rows)
        groups = retain(groups)
        sample_ids = retain(sample_ids)

    collision_rows_dropped = sum(dropped_by_split)
    if not stm_rows:
        raise ValueError("collision filtering removed every labeled record")
    max_width = max(
        max(map(len, white_rows), default=0),
        max(map(len, black_rows), default=0),
    )
    if max_width <= 0:
        raise ValueError("loaded positions contain no active features")
    white = np.full(
        (len(stm_rows), max_width), feature_count, dtype=np.uint16
    )
    black = np.full(
        (len(stm_rows), max_width), feature_count, dtype=np.uint16
    )
    for index, (white_row, black_row) in enumerate(zip(white_rows, black_rows)):
        white[index, : len(white_row)] = white_row
        black[index, : len(black_row)] = black_row
    return Dataset(
        white_features=white,
        black_features=black,
        side_to_move_white=np.asarray(stm_rows, dtype=np.bool_),
        target_cp=np.asarray(cp_rows, dtype=np.float32),
        outcome=np.asarray(outcome_rows, dtype=np.float32),
        search_outcome=np.asarray(search_outcome_rows, dtype=np.float32),
        handcrafted_cp=np.asarray(handcrafted_cp_rows, dtype=np.float32),
        split=np.asarray(split_rows, dtype=np.int8),
        groups=groups,
        sample_ids=sample_ids,
        records_read=records_read,
        records_skipped=records_skipped,
        collision_policy=collision_policy,
        input_collision_signatures=len(collisions),
        input_collision_rows=collision_rows,
        input_collision_rows_dropped=collision_rows_dropped,
        input_collision_dropped_by_split=tuple(dropped_by_split),
        input_collision_examples=collision_examples,
        architecture=architecture,
    )


def make_dataset_from_records(
    records: Iterable[dict[str, Any]],
    seed: int = 1,
    collision_policy: str = "drop",
    residual_outcomes: bool = False,
    architecture: int = ARCHITECTURE_ABSOLUTE,
) -> Dataset:
    handle, name = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        return load_dataset(
            [Path(name)],
            seed=seed,
            train_percent=100.0,
            validation_percent=0.0,
            all_train=True,
            collision_policy=collision_policy,
            residual_outcomes=residual_outcomes,
            architecture=architecture,
        )
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def _encode_rank(characters: Sequence[str | None]) -> str:
    result: list[str] = []
    empty = 0
    for character in characters:
        if character is None:
            empty += 1
            continue
        if empty:
            result.append(str(empty))
            empty = 0
        result.append(character)
    if empty:
        result.append(str(empty))
    return "".join(result)


def make_synthetic_records() -> list[dict[str, Any]]:
    """Create deterministic material examples for the smoke overfit."""
    white_pawn_squares = (11, 21, 31, 41)
    black_pawn_squares = (88, 78, 68, 58)
    records: list[dict[str, Any]] = []
    for material_index in range(20):
        board: dict[int, str] = {40: "K", 59: "k"}
        white_pawns = material_index % 5
        black_pawns = (material_index // 5) % 4
        for square in white_pawn_squares[:white_pawns]:
            board[square] = "P"
        for square in black_pawn_squares[:black_pawns]:
            board[square] = "p"
        white_champion = (material_index >> 2) & 1
        black_champion = (material_index >> 3) & 1
        if white_champion:
            board[0] = "C"
        if black_champion:
            board[99] = "c"
        white_wizard = (material_index >> 1) & 1
        black_wizard = (material_index >> 4) & 1
        corners: list[str] = ["-", "-", "-", "-"]
        if white_wizard:
            corners[0] = "W"
        if black_wizard:
            corners[3] = "w"

        ranks: list[str] = []
        for rank in range(9, -1, -1):
            row = [board.get(file * 10 + rank) for file in range(10)]
            ranks.append(_encode_rank(row))
        placement = "/".join(ranks) + "[" + "/".join(corners) + "]"
        # These are deliberately modest fixture values, not proposed Omega
        # piece values.  Keeping the range narrow makes the smoke test exercise
        # exact quantized fitting instead of spending its time on scale.
        white_cp = (
            25 * (white_pawns - black_pawns)
            + 100 * (white_champion - black_champion)
            + 90 * (white_wizard - black_wizard)
        )
        for side in ("w", "b"):
            target = white_cp if side == "w" else -white_cp
            records.append(
                {
                    "schemaVersion": 1,
                    "sampleId": f"synthetic-{material_index:02d}-{side}",
                    "groupId": f"synthetic-{material_index:02d}",
                    "ofen": f"{placement} {side} - - 0 1",
                    "sideToMove": side,
                    "targetCpStm": target,
                }
            )
    return records
