#!/usr/bin/env python3
"""Shared data, feature, and binary-format code for the Omega NNUE bootstrap.

This module intentionally depends only on Python's standard library and NumPy.
The C++ engine is the authority for runtime inference; ``QuantizedNetwork`` is
an independent implementation used to catch exporter and arithmetic drift.
"""

from __future__ import annotations

from dataclasses import dataclass
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
# Preserve the original public constant for callers that construct an
# absolute OMNNUE1 network. The header's architecture field now also
# self-describes residual/correction semantics without changing the tensor
# layout or invalidating any architecture-1 file.
ARCHITECTURE = ARCHITECTURE_ABSOLUTE
ARCHITECTURE_NAMES = {
    ARCHITECTURE_ABSOLUTE: "absolute",
    ARCHITECTURE_RESIDUAL: "residual",
}
SQUARE_COUNT = 104
PIECE_COUNT = 8
FEATURE_COUNT = 1668
ACCUMULATOR_SIZE = 128
HIDDEN_SIZE = 32
ACTIVATION_MAX = 127
HIDDEN_DIVISOR = 64
OUTPUT_DIVISOR = 64
PAYLOAD_BYTES = 435620

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
        expected = (
            ("ft_bias", self.ft_bias, (ACCUMULATOR_SIZE,), np.int16),
            (
                "ft_weights",
                self.ft_weights,
                (FEATURE_COUNT, ACCUMULATOR_SIZE),
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
        if len(payload) != PAYLOAD_BYTES:
            raise AssertionError(
                f"payload is {len(payload)} bytes, expected {PAYLOAD_BYTES}"
            )
        return payload

    def to_bytes(self) -> bytes:
        payload = self.payload()
        header = HEADER_STRUCT.pack(
            MAGIC,
            ENDIAN_TAG,
            FORMAT_VERSION,
            HEADER_BYTES,
            self.architecture,
            SQUARE_COUNT,
            PIECE_COUNT,
            FEATURE_COUNT,
            ACCUMULATOR_SIZE,
            HIDDEN_SIZE,
            ACTIVATION_MAX,
            HIDDEN_DIVISOR,
            OUTPUT_DIVISOR,
            PAYLOAD_BYTES,
            fnv1a64(payload),
        )
        if len(header) != HEADER_BYTES:
            raise AssertionError("internal header-size error")
        return header + payload

    def write(self, path: Path) -> None:
        _atomic_write(path, self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> "QuantizedNetwork":
        if len(data) != HEADER_BYTES + PAYLOAD_BYTES:
            raise ValueError(
                f"network is {len(data)} bytes; expected "
                f"{HEADER_BYTES + PAYLOAD_BYTES}"
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
        expected = (
            (magic, MAGIC, "magic"),
            (endian, ENDIAN_TAG, "endian tag"),
            (version, FORMAT_VERSION, "format version"),
            (header_bytes, HEADER_BYTES, "header size"),
            (squares, SQUARE_COUNT, "square count"),
            (pieces, PIECE_COUNT, "piece count"),
            (features, FEATURE_COUNT, "feature count"),
            (accumulator, ACCUMULATOR_SIZE, "accumulator size"),
            (hidden, HIDDEN_SIZE, "hidden size"),
            (activation_max, ACTIVATION_MAX, "activation maximum"),
            (hidden_divisor, HIDDEN_DIVISOR, "hidden divisor"),
            (output_divisor, OUTPUT_DIVISOR, "output divisor"),
            (payload_bytes, PAYLOAD_BYTES, "payload size"),
        )
        for actual, wanted, label in expected:
            if actual != wanted:
                raise ValueError(f"bad {label}: {actual!r}; expected {wanted!r}")
        if architecture not in ARCHITECTURE_NAMES:
            raise ValueError(
                f"unsupported architecture semantics {architecture}"
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
        ft_weights = take(FEATURE_COUNT * ACCUMULATOR_SIZE, "<i2")
        ft_weights = ft_weights.reshape(FEATURE_COUNT, ACCUMULATOR_SIZE).astype(
            np.int16, copy=False
        )
        dense_bias = take(HIDDEN_SIZE, "<i4").astype(np.int32, copy=False)
        dense_weights = take(HIDDEN_SIZE * ACCUMULATOR_SIZE * 2, "i1")
        dense_weights = dense_weights.reshape(
            HIDDEN_SIZE, ACCUMULATOR_SIZE * 2
        ).astype(np.int8, copy=False)
        output_bias = int(take(1, "<i4")[0])
        output_weights = take(HIDDEN_SIZE, "i1").astype(np.int8, copy=False)
        if offset != PAYLOAD_BYTES:
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
        return _divide_round(output_raw, OUTPUT_DIVISOR).astype(np.int32)


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


def parse_ofen(ofen: str) -> tuple[list[tuple[int, int, int]], str, str]:
    """Return ``(piece, side, square)`` triples, side to move, and castling."""
    fields = ofen.split()
    if len(fields) != 6:
        raise ValueError("Omega OFEN must contain exactly six fields")
    placement, side_to_move, castling = fields[0], fields[1], fields[2]
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


def _active_features_from_parsed(
    pieces: Sequence[tuple[int, int, int]],
    castling: str,
    perspective: int,
) -> tuple[int, ...]:
    result: list[int] = []
    for piece, side, square in pieces:
        relation = 0 if side == perspective else 1
        feature = (
            (relation * PIECE_COUNT + piece) * SQUARE_COUNT
            + orient_square(square, perspective)
        )
        result.append(feature)

    if perspective not in (0, 1):
        raise ValueError(f"invalid perspective {perspective}")

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
                CASTLING_FEATURE_BASE
                + relation * 2
                + (1 if right_of_king else 0)
            )
    if len(set(result)) != len(result):
        raise ValueError("OFEN produced duplicate NNUE features")
    if any(feature < 0 or feature >= FEATURE_COUNT for feature in result):
        raise AssertionError("internal feature-index error")
    return tuple(sorted(result))


def active_features(ofen: str, perspective: int) -> tuple[int, ...]:
    pieces, _, castling = parse_ofen(ofen)
    return _active_features_from_parsed(pieces, castling, perspective)


def nnue_input_signature(
    white_features: Sequence[int],
    black_features: Sequence[int],
    side_to_move_white: bool,
) -> str:
    """Hash the exact ordered pair of feature sets presented to the network.

    This deliberately ignores OFEN state that the frozen v1 architecture
    cannot observe (for example move counters and en-passant metadata).  It
    therefore catches leakage that a text-OFEN duplicate check would miss.
    """

    stm = white_features if side_to_move_white else black_features
    opponent = black_features if side_to_move_white else white_features
    digest = hashlib.sha256()
    digest.update(b"OMNNUE1-input\0")
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

    @property
    def count(self) -> int:
        return int(self.side_to_move_white.size)

    @property
    def width(self) -> int:
        return int(self.white_features.shape[1])

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
            pieces, ofen_side, castling = parse_ofen(ofen)
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
                _active_features_from_parsed(pieces, castling, 0)
            )
            black_rows.append(
                _active_features_from_parsed(pieces, castling, 1)
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
        signature = nnue_input_signature(white_row, black_row, stm_white)
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
    white = np.full((len(stm_rows), max_width), PAD_FEATURE, dtype=np.uint16)
    black = np.full((len(stm_rows), max_width), PAD_FEATURE, dtype=np.uint16)
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
    )


def make_dataset_from_records(
    records: Iterable[dict[str, Any]],
    seed: int = 1,
    collision_policy: str = "drop",
    residual_outcomes: bool = False,
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
