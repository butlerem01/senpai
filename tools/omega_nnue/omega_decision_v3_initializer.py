#!/usr/bin/env python3
"""Generate and verify the exact Generation-6 fallback initializer.

The fallback is deliberately independent of training or result data.  It is
the legacy FloatNetwork initialization distribution, extended to the complete
architecture-4 feature inventory, with an exact seed, draw order, float32
casts, quantization, and OMNNUE1 serialization.  Generation 6 separately pins
the Python/NumPy runtime; this source and ``omega_nnue.py`` are exact-pinned.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types
from typing import Any, Sequence

import numpy as np


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v6-move-decision-v1"
KIND = "omega-nnue-king-state-v6-deterministic-fallback-initializer"
ARCHITECTURE_ID = "king-state-v6-move-decision-initializer-v1"
SEED = 2026072400
OMEGA_NNUE_BYTES = 53_900
OMEGA_NNUE_SHA256 = (
    "efc55715895f32e948db35428372393c711f701f2e84c69256bdf689065422aa"
)
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
PARITY_OFENS = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/"
    "CRNBQKBNRC[W/W/w/w] w KQkq - 0 1",
    "5k4/10/10/10/10/4P5/10/10/10/4K5[-/-/-/-] w - - 0 1",
    "5k4/10/10/10/10/4P5/10/10/10/4K5[-/-/-/-] b - - 0 1",
    "w9/10/10/10/4k5/5K4/10/10/10/9W[-/-/-/-] w - - 12 37",
    "c9/10/10/10/4k5/5K4/10/10/10/9C[-/-/-/-] b - - 3 19",
)


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int | None]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        getattr(info, "st_mtime_ns", None),
    )


def _snapshot(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    for item in (*reversed(absolute.parents), absolute):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"{label} traverses a reparse path: {item}")
    before = os.lstat(absolute)
    if (
        not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_nlink", 1) != 1
    ):
        raise ValueError(f"{label} is not one private regular file")
    descriptor = os.open(
        absolute,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_nlink", 1) != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError(f"{label} changed before descriptor open")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
            digest.update(block)
        if _stat_key(os.fstat(descriptor)) != _stat_key(opened):
            raise ValueError(f"{label} changed while read")
    finally:
        os.close(descriptor)
    if _stat_key(os.lstat(absolute)) != _stat_key(before):
        raise ValueError(f"{label} path changed while read")
    payload = b"".join(chunks)
    return {
        "path": str(absolute),
        "bytes": len(payload),
        "sha256": digest.hexdigest(),
    }, payload


def _load_omega_nnue() -> types.ModuleType:
    path = Path(__file__).resolve().with_name("omega_nnue.py")
    identity, payload = _snapshot(path, "omega_nnue implementation")
    if (
        identity["bytes"] != OMEGA_NNUE_BYTES
        or identity["sha256"] != OMEGA_NNUE_SHA256
    ):
        raise RuntimeError("omega_nnue implementation differs from its exact pin")
    name = "_omega_decision_v3_initializer_pinned_omega_nnue"
    if name in sys.modules:
        raise RuntimeError("refusing a preloaded fallback encoder")
    module = types.ModuleType(name)
    module.__file__ = identity["path"]
    module.__package__ = ""
    module.__loader__ = None
    module.__spec__ = None
    sys.modules[name] = module
    try:
        exec(
            compile(payload, str(path), "exec", dont_inherit=True),
            module.__dict__,
        )
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if _snapshot(path, "omega_nnue implementation after load")[0] != identity:
        sys.modules.pop(name, None)
        raise ValueError("omega_nnue implementation changed while loaded")
    return module


omega_nnue = _load_omega_nnue()


def _clip_round(
    values: np.ndarray, low: int, high: int, dtype: Any
) -> np.ndarray:
    return np.clip(np.rint(values), low, high).astype(dtype)


def fallback_bytes() -> bytes:
    """Return the one protocol-defined architecture-4 fallback network."""

    feature_count = omega_nnue.OMEGA_INTERACTION_FEATURE_COUNT
    accumulator = omega_nnue.ACCUMULATOR_SIZE
    hidden = omega_nnue.HIDDEN_SIZE
    rng = np.random.default_rng(SEED)
    ft_bias = np.full(accumulator, 24.0, dtype=np.float32)
    ft_weights = rng.normal(
        0.0, 0.50, size=(feature_count, accumulator)
    ).astype(np.float32)
    dense_bias = np.full(hidden, 24.0, dtype=np.float32)
    dense_weights = rng.normal(
        0.0, 0.025, size=(hidden, accumulator * 2)
    ).astype(np.float32)
    output_bias = np.zeros(1, dtype=np.float32)
    output_weights = rng.normal(0.0, 0.10, size=hidden).astype(np.float32)
    network = omega_nnue.QuantizedNetwork(
        ft_bias=_clip_round(
            ft_bias, -(1 << 15), (1 << 15) - 1, np.int16
        ),
        ft_weights=_clip_round(
            ft_weights, -(1 << 15), (1 << 15) - 1, np.int16
        ),
        dense_bias=_clip_round(
            dense_bias * omega_nnue.HIDDEN_DIVISOR,
            -(1 << 31),
            (1 << 31) - 1,
            np.int32,
        ),
        dense_weights=_clip_round(
            dense_weights * omega_nnue.HIDDEN_DIVISOR, -128, 127, np.int8
        ),
        output_bias=int(
            _clip_round(
                output_bias * omega_nnue.OUTPUT_DIVISOR,
                -(1 << 31),
                (1 << 31) - 1,
                np.int32,
            )[0]
        ),
        output_weights=_clip_round(
            output_weights * omega_nnue.OUTPUT_DIVISOR, -128, 127, np.int8
        ),
        architecture=omega_nnue.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
    )
    payload = network.to_bytes()
    if omega_nnue.QuantizedNetwork.from_bytes(payload).to_bytes() != payload:
        raise AssertionError("fallback OMNNUE1 encoding is not round-trip exact")
    return payload


def _fallback_description(payload: bytes) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "profileId": PROFILE_ID,
        "architectureId": ARCHITECTURE_ID,
        "omegaNnueArchitectureId": 4,
        "omegaNnueArchitecture": "omega-interaction-residual",
        "seed": SEED,
        "prng": "numpy.random.default_rng-PCG64",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "gameResultsRead": False,
        "targetRowsDecoded": 0,
    }


def fallback_description() -> dict[str, Any]:
    return _fallback_description(fallback_bytes())


def _feature_rows(
    ofens: Sequence[str], architecture: int
) -> tuple[np.ndarray, np.ndarray]:
    white = [
        omega_nnue.active_features(ofen, 0, architecture) for ofen in ofens
    ]
    black = [
        omega_nnue.active_features(ofen, 1, architecture) for ofen in ofens
    ]
    width = max(max(map(len, white)), max(map(len, black)))
    pad = omega_nnue.feature_count_for_architecture(architecture)
    white_matrix = np.full((len(ofens), width), pad, dtype=np.int64)
    black_matrix = np.full((len(ofens), width), pad, dtype=np.int64)
    for row, features in enumerate(white):
        white_matrix[row, : len(features)] = features
    for row, features in enumerate(black):
        black_matrix[row, : len(features)] = features
    stm_white = np.asarray(
        [ofen.split()[1] == "w" for ofen in ofens], dtype=np.bool_
    )[:, None]
    return (
        np.where(stm_white, white_matrix, black_matrix),
        np.where(stm_white, black_matrix, white_matrix),
    )


def mapped_g2_bytes(raw_payload: bytes) -> tuple[bytes, dict[str, Any]]:
    """Map exact architecture-3 K2 bytes to the architecture-4 G6 layout."""

    raw = omega_nnue.QuantizedNetwork.from_bytes(raw_payload)
    if raw.architecture != omega_nnue.ARCHITECTURE_KING_STATE_RESIDUAL:
        raise ValueError("G2-K2 mapping source is not architecture 3")
    if raw.ft_weights.shape != (
        omega_nnue.KING_STATE_FEATURE_COUNT,
        omega_nnue.ACCUMULATOR_SIZE,
    ):
        raise ValueError("G2-K2 mapping source feature inventory changed")
    extra = np.zeros(
        (omega_nnue.OMEGA_INTERACTION_FEATURES, omega_nnue.ACCUMULATOR_SIZE),
        dtype=np.int16,
    )
    mapped = omega_nnue.QuantizedNetwork(
        ft_bias=raw.ft_bias.copy(),
        ft_weights=np.concatenate((raw.ft_weights, extra), axis=0),
        dense_bias=raw.dense_bias.copy(),
        dense_weights=raw.dense_weights.copy(),
        output_bias=raw.output_bias,
        output_weights=raw.output_weights.copy(),
        architecture=omega_nnue.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
    )
    mapped_payload = mapped.to_bytes()
    if mapped.ft_weights.shape != (
        omega_nnue.OMEGA_INTERACTION_FEATURE_COUNT,
        omega_nnue.ACCUMULATOR_SIZE,
    ):
        raise AssertionError("mapped architecture-4 feature inventory changed")
    if not np.array_equal(
        mapped.ft_weights[: omega_nnue.KING_STATE_FEATURE_COUNT],
        raw.ft_weights,
    ) or not np.all(
        mapped.ft_weights[omega_nnue.KING_STATE_FEATURE_COUNT :] == 0
    ):
        raise AssertionError("mapped feature tensors violate append-zero contract")
    if not (
        np.array_equal(mapped.ft_bias, raw.ft_bias)
        and np.array_equal(mapped.dense_bias, raw.dense_bias)
        and np.array_equal(mapped.dense_weights, raw.dense_weights)
        and mapped.output_bias == raw.output_bias
        and np.array_equal(mapped.output_weights, raw.output_weights)
    ):
        raise AssertionError("mapped non-feature tensor changed")
    raw_stm, raw_opponent = _feature_rows(
        PARITY_OFENS, omega_nnue.ARCHITECTURE_KING_STATE_RESIDUAL
    )
    mapped_stm, mapped_opponent = _feature_rows(
        PARITY_OFENS, omega_nnue.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
    )
    raw_predictions = raw.predict_features(raw_stm, raw_opponent)
    mapped_predictions = mapped.predict_features(mapped_stm, mapped_opponent)
    mismatches = int(np.count_nonzero(raw_predictions != mapped_predictions))
    if mismatches != 0:
        raise AssertionError("G2-K2 architecture mapping changed epoch-zero scores")
    proof = {
        "sourceArchitectureId": 3,
        "mappedArchitectureId": 4,
        "architecture3RowsByteIdentical": True,
        "appendedInteractionRows": omega_nnue.OMEGA_INTERACTION_FEATURES,
        "appendedInteractionRowsAllZero": True,
        "nonFeatureTensorsByteIdentical": True,
        "epochZeroParityPositions": len(PARITY_OFENS),
        "epochZeroPredictionMismatches": mismatches,
    }
    return mapped_payload, proof


def verify_g2_mapping(raw_path: Path, mapped_path: Path) -> dict[str, Any]:
    raw_identity, raw_payload = _snapshot(raw_path, "raw G2-K2 initializer")
    mapped_identity, actual = _snapshot(
        mapped_path, "mapped G2-K2 initializer"
    )
    expected, proof = mapped_g2_bytes(raw_payload)
    if actual != expected:
        raise ValueError(
            "mapped G2-K2 initializer differs from exact architecture-4 bytes"
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v6-g2-k2-architecture-mapping",
        "profileId": PROFILE_ID,
        "rawModel": raw_identity,
        "mappedModel": mapped_identity,
        "mappingProof": proof,
        "exactBytesMatch": True,
        "gameResultsRead": False,
        "targetRowsDecoded": 0,
    }


def write_g2_mapping(raw_path: Path, output_path: Path) -> dict[str, Any]:
    raw_identity, raw_payload = _snapshot(raw_path, "raw G2-K2 initializer")
    mapped_payload, proof = mapped_g2_bytes(raw_payload)
    output = _safe_output(output_path)
    descriptor = os.open(
        output,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    opened: os.stat_result | None = None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError("G2-K2 mapping publisher opened an unsafe file")
        offset = 0
        while offset < len(mapped_payload):
            offset += os.write(descriptor, mapped_payload[offset:])
        os.fsync(descriptor)
        completed = os.fstat(descriptor)
        if (
            _stat_key(completed)[:3]
            != (opened.st_dev, opened.st_ino, len(mapped_payload))
            or getattr(completed, "st_nlink", 1) != 1
        ):
            raise ValueError("G2-K2 mapping publisher wrote non-exact bytes")
    except BaseException:
        os.close(descriptor)
        try:
            named = os.lstat(output)
            if opened is not None and (named.st_dev, named.st_ino) == (
                opened.st_dev,
                opened.st_ino,
            ):
                output.unlink()
        except OSError:
            pass
        raise
    else:
        os.close(descriptor)
    mapped_identity, actual = _snapshot(output, "published G2-K2 mapping")
    if actual != mapped_payload:
        raise ValueError("published G2-K2 mapping changed")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v6-g2-k2-architecture-mapping",
        "profileId": PROFILE_ID,
        "rawModel": raw_identity,
        "mappedModel": mapped_identity,
        "mappingProof": proof,
        "exactBytesMatch": True,
        "gameResultsRead": False,
        "targetRowsDecoded": 0,
    }


def _safe_output(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    parent = absolute.parent
    for item in (*reversed(parent.parents), parent):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"fallback output traverses a reparse path: {item}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"fallback output parent is not a directory: {item}")
    if os.path.lexists(absolute):
        raise FileExistsError(absolute)
    return absolute


def write_fallback(
    path: Path, *, payload: bytes | None = None
) -> dict[str, Any]:
    output = _safe_output(path)
    payload = fallback_bytes() if payload is None else payload
    descriptor = os.open(
        output,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    opened: os.stat_result | None = None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError("fallback publisher opened an unsafe file")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        completed = os.fstat(descriptor)
        if (
            _stat_key(completed)[:3]
            != (opened.st_dev, opened.st_ino, len(payload))
            or getattr(completed, "st_nlink", 1) != 1
        ):
            raise ValueError("fallback publisher did not create exact bytes")
    except BaseException:
        os.close(descriptor)
        try:
            named = os.lstat(output)
            if opened is not None and (named.st_dev, named.st_ino) == (
                opened.st_dev,
                opened.st_ino,
            ):
                output.unlink()
        except OSError:
            pass
        raise
    else:
        os.close(descriptor)
    identity, actual = _snapshot(output, "published fallback initializer")
    if actual != payload:
        raise ValueError("published fallback initializer changed")
    return identity


def verify_fallback(path: Path) -> dict[str, Any]:
    identity, actual = _snapshot(path, "fallback initializer")
    expected = fallback_bytes()
    if actual != expected:
        raise ValueError("fallback initializer differs from exact generated bytes")
    description = _fallback_description(expected)
    if (
        identity["bytes"] != description["bytes"]
        or identity["sha256"] != description["sha256"]
    ):
        raise AssertionError("fallback byte comparison and identity disagree")
    return {
        **description,
        "model": identity,
        "exactBytesMatch": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--describe-fallback"]:
        sys.stdout.buffer.write(_canonical_json(fallback_description()))
        return 0
    if len(arguments) == 2 and arguments[0] == "--write-fallback":
        payload = fallback_bytes()
        model = write_fallback(Path(arguments[1]), payload=payload)
        sys.stdout.buffer.write(
            _canonical_json(
                {
                    **_fallback_description(payload),
                    "model": model,
                }
            )
        )
        return 0
    if len(arguments) == 2 and arguments[0] == "--verify-fallback":
        sys.stdout.buffer.write(
            _canonical_json(verify_fallback(Path(arguments[1])))
        )
        return 0
    if len(arguments) == 3 and arguments[0] == "--verify-g2-mapping":
        sys.stdout.buffer.write(
            _canonical_json(
                verify_g2_mapping(Path(arguments[1]), Path(arguments[2]))
            )
        )
        return 0
    if len(arguments) == 3 and arguments[0] == "--write-g2-mapping":
        sys.stdout.buffer.write(
            _canonical_json(
                write_g2_mapping(Path(arguments[1]), Path(arguments[2]))
            )
        )
        return 0
    raise ValueError(
        "usage: omega_decision_v3_initializer.py "
        "(--describe-fallback | --write-fallback OUTPUT | "
        "--verify-fallback MODEL | --verify-g2-mapping RAW MAPPED | "
        "--write-g2-mapping RAW OUTPUT)"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


__all__ = [
    "ARCHITECTURE_ID",
    "KIND",
    "PROFILE_ID",
    "SCHEMA_VERSION",
    "SEED",
    "fallback_bytes",
    "fallback_description",
    "main",
    "mapped_g2_bytes",
    "verify_fallback",
    "verify_g2_mapping",
    "write_fallback",
    "write_g2_mapping",
]
