#!/usr/bin/env python3
"""Train and export the first dependency-free Omega NNUE network."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import platform
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np
import omega_nnue as omega_nnue_module

from omega_nnue import (
    ACCUMULATOR_SIZE,
    ACTIVATION_MAX,
    ARCHITECTURE_ABSOLUTE,
    ARCHITECTURE_KING_STATE_RESIDUAL,
    ARCHITECTURE_NAMES,
    ARCHITECTURE_RESIDUAL,
    FEATURE_COUNT,
    FORMAT_VERSION,
    HEADER_BYTES,
    HIDDEN_DIVISOR,
    HIDDEN_SIZE,
    KING_BUCKET_COUNT,
    KING_STATE_CASTLING_FEATURE_BASE,
    KING_STATE_EP_FEATURE_BASE,
    KING_STATE_FEATURE_COUNT,
    KING_STATE_HALFMOVE_FEATURE_BASE,
    KING_STATE_PHASE_FEATURE_BASE,
    OUTPUT_DIVISOR,
    PAYLOAD_BYTES,
    Dataset,
    QuantizedNetwork,
    active_features,
    deterministic_split,
    feature_count_for_architecture,
    halfmove_clock_bin,
    is_residual_architecture,
    load_dataset,
    make_dataset_from_records,
    make_synthetic_records,
    payload_bytes_for_architecture,
)


CP_NORMALIZER = 100.0
CP_HUBER_DELTA = 2.0
OUTCOME_LOGISTIC_SCALE = 400.0 / math.log(10.0)
FLOAT_CHECKPOINT_MAGIC = b"OMFNET1\0"
FLOAT_CHECKPOINT_HEADER = struct.Struct("<8sIIIII32s")

INITIAL_OFEN = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
    "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
)
SPARSE_OFEN = (
    "5k4/10/10/10/10/10/10/10/10/4K5[-/-/-/-] w - - 0 1"
)
CHAMPION_C3_OFEN = (
    "5k4/10/10/10/10/10/2C7/10/10/4K5[-/-/-/-] w - - 0 1"
)
CROSS_RUNTIME_OFENS = (
    INITIAL_OFEN,
    INITIAL_OFEN.replace(" w KQkq ", " b KQkq "),
    CHAMPION_C3_OFEN,
    "4k5/10/10/10/10/10/10/10/10/4K5[W/-/-/w] b - - 17 42",
    "5k4/10/10/10/10/10/10/10/10/4K5[-/-/-/-] w - d2,d3 74 42",
)


def _file_pin(path: Path) -> dict[str, Any]:
    """Return a content identity suitable for a reproducibility manifest."""

    resolved = path.resolve(strict=True)
    digest = hashlib.sha256()
    byte_count = 0
    with resolved.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            byte_count += len(block)
    return {
        "path": str(resolved),
        "bytes": byte_count,
        "sha256": digest.hexdigest(),
    }


def _skip_json_string(text: str, start: int) -> int:
    """Return the first index after one JSON string without decoding it."""

    if start >= len(text) or text[start] != '"':
        raise ValueError("expected JSON string")
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == '"':
            return index + 1
        if character == "\\":
            index += 2
        else:
            if ord(character) < 0x20:
                raise ValueError("unescaped control character in JSON string")
            index += 1
    raise ValueError("unterminated JSON string")


def _skip_json_value(text: str, start: int) -> int:
    """Lexically skip a JSON value while deliberately not decoding it."""

    if start >= len(text):
        raise ValueError("missing JSON value")
    if text[start] == '"':
        return _skip_json_string(text, start)
    if text[start] in "[{":
        stack = [text[start]]
        index = start + 1
        while index < len(text) and stack:
            character = text[index]
            if character == '"':
                index = _skip_json_string(text, index)
                continue
            if character in "[{":
                stack.append(character)
            elif character in "]}":
                opener = stack.pop()
                if (opener, character) not in (("[", "]"), ("{", "}")):
                    raise ValueError("mismatched JSON container")
            index += 1
        if stack:
            raise ValueError("unterminated JSON container")
        return index
    index = start
    while index < len(text) and text[index] not in ",}":
        index += 1
    if not text[start:index].strip():
        raise ValueError("empty JSON scalar")
    return index


def _selective_top_level_string(
    line: str, field: str, *, location: str
) -> str:
    """Read one top-level string field without decoding any other value.

    Protocol pre-test filtering uses this before ``json.loads``.  A held-out
    row is routed by ``groupId`` and discarded while its target fields remain
    opaque text; malformed or adversarial test labels therefore cannot affect
    training, validation, manifests, or candidate selection.
    """

    text = line.lstrip("\ufeff \t\r\n")
    if not text.startswith("{"):
        raise ValueError(f"{location}: JSONL row is not an object")
    index = 1
    decoder = json.JSONDecoder()
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            raise ValueError(f"{location}: unterminated JSON object")
        if text[index] == "}":
            break
        if text[index] != '"':
            raise ValueError(f"{location}: expected a JSON object key")
        key_end = _skip_json_string(text, index)
        try:
            key = decoder.decode(text[index:key_end])
        except json.JSONDecodeError as error:
            raise ValueError(f"{location}: invalid JSON object key") from error
        index = key_end
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index] != ":":
            raise ValueError(f"{location}: missing colon after JSON key")
        index += 1
        while index < len(text) and text[index].isspace():
            index += 1
        value_end = _skip_json_value(text, index)
        if key == field:
            if index >= len(text) or text[index] != '"':
                raise ValueError(
                    f"{location}: {field!r} must be a nonempty string"
                )
            try:
                value = decoder.decode(text[index:value_end])
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{location}: invalid JSON string in {field!r}"
                ) from error
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{location}: {field!r} must be a nonempty string"
                )
            return value
        index = value_end
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            break
        raise ValueError(f"{location}: malformed JSON object")
    raise ValueError(f"{location}: missing top-level string field {field!r}")


def _iter_selective_string(
    paths: list[Path], field: str
) -> Iterator[tuple[str, str]]:
    for path in paths:
        resolved = path.resolve(strict=True)
        with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                location = f"{resolved}:{line_number}"
                yield (
                    _selective_top_level_string(
                        line, field, location=location
                    ),
                    location,
                )


def _make_protocol_pretest_input(
    paths: list[Path],
    *,
    split_seed: int,
    train_percent: float,
    validation_percent: float,
    group_field: str | None,
) -> tuple[tempfile.TemporaryDirectory[str], list[Path], dict[str, Any]]:
    """Stage only split 0/1 rows; split-2 labels are never JSON-decoded."""

    if group_field != "groupId":
        raise ValueError(
            "--protocol-pretest requires the frozen --group-field groupId"
        )
    temporary = tempfile.TemporaryDirectory(
        prefix="omega-nnue-protocol-pretest-"
    )
    output = Path(temporary.name) / "train-validation.jsonl"
    counts = [0, 0, 0]
    with output.open("w", encoding="utf-8", newline="\n") as destination:
        for path in paths:
            resolved = path.resolve(strict=True)
            with resolved.open(
                "r", encoding="utf-8-sig", newline=""
            ) as source:
                for line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    location = f"{resolved}:{line_number}"
                    group = _selective_top_level_string(
                        line, "groupId", location=location
                    )
                    split = deterministic_split(
                        group,
                        split_seed,
                        train_percent,
                        validation_percent,
                    )
                    counts[split] += 1
                    if split != 2:
                        # Full decoding is allowed only after routing proves
                        # that this is a train or validation row.
                        destination.write(line.rstrip("\r\n") + "\n")
    if counts[0] == 0 or counts[1] == 0 or counts[2] == 0:
        temporary.cleanup()
        raise ValueError(
            "protocol split must contain train, validation, and held-out rows"
        )
    return temporary, [output], {
        "enabled": True,
        "routingField": "groupId",
        "splitSeed": split_seed,
        "rows": {
            "train": counts[0],
            "validation": counts[1],
            "testWithheld": counts[2],
        },
        "heldOutTargetFieldsDecoded": 0,
        "heldOutRowsWritten": 0,
        "heldOutMetricsComputed": False,
        "allowedSplits": [0, 1],
    }


def _experiment_context(args: argparse.Namespace) -> dict[str, Any] | None:
    paths = {
        "protocol": args.protocol,
        "prelabelSeal": args.prelabel_seal,
        "corpusManifest": args.corpus_manifest,
        "staticHceEvaluator": args.static_hce_evaluator,
    }
    supplied = {name: path for name, path in paths.items() if path is not None}
    if not supplied and args.candidate_id is None:
        return None
    if args.candidate_id not in ("K0", "K1", "K2"):
        raise ValueError(
            "protocol training requires --candidate-id K0, K1, or K2"
        )
    missing = [name for name, path in paths.items() if path is None]
    if missing:
        raise ValueError(
            "protocol training is missing context: " + ", ".join(missing)
        )
    return {
        "candidateId": args.candidate_id,
        "protocol": _file_pin(args.protocol),
        "prelabelSeal": _file_pin(args.prelabel_seal),
        "corpusManifest": _file_pin(args.corpus_manifest),
        "staticHceEvaluator": _file_pin(args.static_hce_evaluator),
        "rawArgv": list(args.raw_argv),
        "strict": bool(args.strict),
        "modelSeed": args.seed,
        "splitSeed": args.seed if args.split_seed is None else args.split_seed,
    }


def _validate_residual_targets(paths: list[Path]) -> int:
    """Reject an absolute-score corpus mislabeled as residual training data."""

    checked = 0
    for path in paths:
        with path.resolve(strict=True).open("r", encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}:{line_number}: {error}") from error
                if not isinstance(record, dict):
                    raise ValueError(
                        f"{path}:{line_number}: residual row is not an object"
                    )
                if record.get("targetSemantics") != "search-minus-handcrafted":
                    raise ValueError(
                        f"{path}:{line_number}: residual training requires "
                        "targetSemantics='search-minus-handcrafted'"
                    )
                search = record.get("searchTargetCpStm")
                handcrafted = record.get("handcraftedCpStm")
                residual = record.get("targetCpStm")
                if (
                    isinstance(search, bool)
                    or not isinstance(search, (int, float))
                    or isinstance(handcrafted, bool)
                    or not isinstance(handcrafted, (int, float))
                    or isinstance(residual, bool)
                    or not isinstance(residual, (int, float))
                    or not all(
                        math.isfinite(float(value))
                        for value in (search, handcrafted, residual)
                    )
                ):
                    raise ValueError(
                        f"{path}:{line_number}: residual target components "
                        "must be finite numbers"
                    )
                expected = float(search) - float(handcrafted)
                if not math.isclose(
                    float(residual), expected, rel_tol=0.0, abs_tol=1e-9
                ):
                    raise ValueError(
                        f"{path}:{line_number}: residual target {residual} "
                        f"does not equal search-HCE {expected}"
                    )
                search_outcome = record.get("searchOutcomeStm")
                search_side_score = record.get("searchSideToMoveScore")
                for name, value in (
                    ("searchOutcomeStm", search_outcome),
                    ("searchSideToMoveScore", search_side_score),
                ):
                    if value is None:
                        continue
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                        or not 0.0 <= float(value) <= 1.0
                    ):
                        raise ValueError(
                            f"{path}:{line_number}: {name} must be a finite "
                            "number in 0..1"
                        )
                if (
                    search_outcome is not None
                    and search_side_score is not None
                    and float(search_outcome) != float(search_side_score)
                ):
                    raise ValueError(
                        f"{path}:{line_number}: search outcome fields disagree"
                    )
                checked += 1
    if checked == 0:
        raise ValueError("residual training corpus contains no labeled rows")
    return checked


def _numpy_build_configuration() -> Any:
    try:
        return np.__config__.show(mode="dicts")
    except (TypeError, ValueError):
        # Older NumPy versions only print this information.
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            np.__config__.show()
        return {"text": capture.getvalue().strip()}


def _runtime_manifest() -> dict[str, Any]:
    numpy_path = Path(np.__file__).resolve()
    return {
        "python": {
            "version": sys.version,
            "versionInfo": list(sys.version_info),
            "implementation": platform.python_implementation(),
            "executable": str(Path(sys.executable).resolve()),
            "compiler": platform.python_compiler(),
            "build": list(platform.python_build()),
        },
        "numpy": {
            "version": np.__version__,
            "module": _file_pin(numpy_path),
            "buildConfiguration": _numpy_build_configuration(),
        },
        "platform": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "tools": {
            "train.py": _file_pin(Path(__file__)),
            "omega_nnue.py": _file_pin(Path(omega_nnue_module.__file__)),
        },
    }


def _blank_quantized_network(
    *,
    output_bias: int = 0,
    architecture: int = ARCHITECTURE_ABSOLUTE,
) -> QuantizedNetwork:
    feature_count = feature_count_for_architecture(architecture)
    return QuantizedNetwork(
        ft_bias=np.zeros(ACCUMULATOR_SIZE, dtype=np.int16),
        ft_weights=np.zeros(
            (feature_count, ACCUMULATOR_SIZE), dtype=np.int16
        ),
        dense_bias=np.zeros(HIDDEN_SIZE, dtype=np.int32),
        dense_weights=np.zeros(
            (HIDDEN_SIZE, ACCUMULATOR_SIZE * 2), dtype=np.int8
        ),
        output_bias=output_bias,
        output_weights=np.zeros(HIDDEN_SIZE, dtype=np.int8),
        architecture=architecture,
    )


def expand_residual_to_king_state(
    source: QuantizedNetwork,
) -> QuantizedNetwork:
    """Lift architecture 2 into architecture 3 without changing any score.

    Every legacy piece-square row is copied into all 29 friendly-king buckets.
    The four castling rows stay global. Newly observable EP, halfmove, and
    phase rows start at zero, and all downstream tensors are copied exactly.
    """

    source.validate()
    if source.architecture != ARCHITECTURE_RESIDUAL:
        raise ValueError(
            "king-state migration source must be architecture-2 residual"
        )
    occupancy_features = FEATURE_COUNT - 4
    expanded = _blank_quantized_network(
        output_bias=source.output_bias,
        architecture=ARCHITECTURE_KING_STATE_RESIDUAL
    )
    expanded.ft_bias[:] = source.ft_bias
    for bucket in range(KING_BUCKET_COUNT):
        begin = bucket * occupancy_features
        expanded.ft_weights[begin : begin + occupancy_features] = (
            source.ft_weights[:occupancy_features]
        )
    expanded.ft_weights[
        KING_STATE_CASTLING_FEATURE_BASE
        : KING_STATE_CASTLING_FEATURE_BASE + 4
    ] = source.ft_weights[occupancy_features:FEATURE_COUNT]
    expanded.dense_bias[:] = source.dense_bias
    expanded.dense_weights[:] = source.dense_weights
    expanded.output_weights[:] = source.output_weights
    expanded.validate()
    return expanded


def _predict_ofen(network: QuantizedNetwork, ofen: str) -> int:
    white = np.asarray(
        [active_features(ofen, 0, network.architecture)], dtype=np.uint16
    )
    black = np.asarray(
        [active_features(ofen, 1, network.architecture)], dtype=np.uint16
    )
    if ofen.split()[1] == "w":
        stm, opponent = white, black
    else:
        stm, opponent = black, white
    return int(network.predict_features(stm, opponent)[0])


def _golden_runtime_cases() -> list[dict[str, Any]]:
    """Build small literal networks that pin tricky integer semantics."""

    # Feature 647 is own Champion on c3:
    # (own * 8 + Champion(6)) * 104 + c3(file 2 * 10 + rank 3).
    feature_row = 647
    if feature_row not in active_features(CHAMPION_C3_OFEN, 0):
        raise AssertionError("golden feature-row fixture no longer maps to 647")
    if feature_row in active_features(SPARSE_OFEN, 0):
        raise AssertionError("golden absent-feature fixture unexpectedly has row 647")

    row_order = _blank_quantized_network()
    row_order.ft_weights[feature_row, 0] = 1
    row_order.dense_weights[0, 0] = 64
    row_order.output_weights[0] = 64

    negative = _blank_quantized_network()
    negative.ft_bias[0] = 10
    negative.ft_weights[feature_row, 0] = -3
    negative.dense_weights[0, 0] = 64
    negative.output_weights[0] = -64

    positive_half = _blank_quantized_network(output_bias=32)

    negative_half = _blank_quantized_network(output_bias=-32)

    saturation = _blank_quantized_network()
    saturation.ft_bias[0] = 200
    saturation.dense_weights[0, 0] = 127
    saturation.output_weights[0] = 64

    residual = _blank_quantized_network(
        output_bias=64,
        architecture=ARCHITECTURE_RESIDUAL,
    )
    king_state = _blank_quantized_network(
        architecture=ARCHITECTURE_KING_STATE_RESIDUAL
    )
    king_state.ft_weights[KING_STATE_HALFMOVE_FEATURE_BASE + 3, 0] = 1
    king_state.ft_weights[KING_STATE_EP_FEATURE_BASE + 32, 0] = 2
    king_state.ft_weights[KING_STATE_EP_FEATURE_BASE + 33, 0] = 4
    king_state.ft_weights[KING_STATE_PHASE_FEATURE_BASE + 3, 0] = 8
    king_state.ft_weights[
        10 * (2 * 8 * 104) + feature_row, 0
    ] = 16
    king_state.ft_weights[KING_STATE_CASTLING_FEATURE_BASE, 0] = 32
    king_state.dense_weights[0, 0] = 64
    king_state.output_weights[0] = 64

    return [
        {
            "name": "feature-row-order",
            "network": row_order,
            "vectors": [
                {
                    "name": "own-champion-c3-row-647",
                    "ofen": CHAMPION_C3_OFEN,
                    "expected": 1,
                },
                {
                    "name": "row-647-absent",
                    "ofen": SPARSE_OFEN,
                    "expected": 0,
                },
            ],
        },
        {
            "name": "negative-weights",
            "network": negative,
            "vectors": [
                {
                    "name": "negative-ft-and-output-weights",
                    "ofen": CHAMPION_C3_OFEN,
                    "expected": -7,
                }
            ],
        },
        {
            "name": "positive-half-away",
            "network": positive_half,
            "vectors": [
                {
                    "name": "output-plus-32-over-64",
                    "ofen": SPARSE_OFEN,
                    "expected": 1,
                }
            ],
        },
        {
            "name": "negative-half-away",
            "network": negative_half,
            "vectors": [
                {
                    "name": "output-minus-32-over-64",
                    "ofen": SPARSE_OFEN,
                    "expected": -1,
                }
            ],
        },
        {
            "name": "activation-saturation",
            "network": saturation,
            "vectors": [
                {
                    "name": "feature-and-hidden-clip-at-127",
                    "ofen": SPARSE_OFEN,
                    "expected": 127,
                }
            ],
        },
        {
            "name": "residual-header-semantics",
            "network": residual,
            "vectors": [
                {
                    "name": "residual-raw-correction",
                    "ofen": SPARSE_OFEN,
                    "expected": 1,
                }
            ],
        },
        {
            "name": "king-state-halfmove",
            "network": king_state,
            "vectors": [
                {
                    "name": "halfmove-bin-17",
                    "ofen": SPARSE_OFEN.replace(" 0 1", " 17 1"),
                    "expected": 9,
                },
                {
                    "name": "phase-only-at-halfmove-zero",
                    "ofen": SPARSE_OFEN,
                    "expected": 8,
                },
                {
                    "name": "two-target-en-passant",
                    "ofen": SPARSE_OFEN.replace(" - - 0 1", " - d2,d3 74 1"),
                    "expected": 14,
                },
                {
                    "name": "king-conditioned-champion",
                    "ofen": CHAMPION_C3_OFEN.replace(" 0 1", " 17 1"),
                    "expected": 25,
                },
                {
                    "name": "global-castling-row",
                    "ofen": INITIAL_OFEN,
                    "expected": 32,
                },
            ],
        },
    ]


def _golden_python_self_test(
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    networks: list[dict[str, Any]] = []
    vectors: list[dict[str, Any]] = []
    for case in cases:
        network = case["network"]
        encoded = network.to_bytes()
        networks.append(
            {
                "name": case["name"],
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
        for vector in case["vectors"]:
            actual = _predict_ofen(network, vector["ofen"])
            if actual != vector["expected"]:
                raise AssertionError(
                    f"golden vector {vector['name']} produced {actual}, "
                    f"expected {vector['expected']}"
                )
            vectors.append(
                {
                    "network": case["name"],
                    "name": vector["name"],
                    "ofen": vector["ofen"],
                    "expected": vector["expected"],
                    "python": actual,
                }
            )
    return {
        "status": "passed",
        "networkCount": len(networks),
        "vectorCount": len(vectors),
        "networks": networks,
        "vectors": vectors,
    }


def _cpp_evaluate(
    helper: Path, network_path: Path, ofen: str
) -> int:
    completed = subprocess.run(
        [
            str(helper),
            "--evaluate-network",
            str(network_path.resolve()),
            ofen,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"C++ evaluator exited {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    output = completed.stdout.strip()
    try:
        return int(output)
    except ValueError as error:
        raise AssertionError(
            f"C++ evaluator did not emit one bare integer: {output!r}"
        ) from error


def _run_cpp_cross_runtime(
    helper_path: Path | None,
    exported_path: Path,
    exported_network: QuantizedNetwork,
    golden_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    if helper_path is None:
        return {
            "status": "not-requested",
            "positionsChecked": 0,
            "goldenVectorsChecked": 0,
        }
    helper = helper_path.resolve(strict=True)
    if not helper.is_file():
        raise ValueError(f"C++ evaluator is not a file: {helper}")

    exported_vectors: list[dict[str, Any]] = []
    for ofen in CROSS_RUNTIME_OFENS:
        expected = _predict_ofen(exported_network, ofen)
        actual = _cpp_evaluate(helper, exported_path, ofen)
        if actual != expected:
            raise AssertionError(
                "C++/Python mismatch for exported network: "
                f"{ofen}: C++ {actual}, Python {expected}"
            )
        exported_vectors.append(
            {"ofen": ofen, "python": expected, "cpp": actual}
        )

    golden_vectors: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="omega-nnue-golden-") as directory:
        root = Path(directory)
        for case in golden_cases:
            network_path = root / f"{case['name']}.nnue"
            case["network"].write(network_path)
            for vector in case["vectors"]:
                actual = _cpp_evaluate(helper, network_path, vector["ofen"])
                if actual != vector["expected"]:
                    raise AssertionError(
                        f"C++ golden vector {vector['name']} produced {actual}, "
                        f"expected {vector['expected']}"
                    )
                golden_vectors.append(
                    {
                        "network": case["name"],
                        "name": vector["name"],
                        "ofen": vector["ofen"],
                        "expected": vector["expected"],
                        "cpp": actual,
                    }
                )
    return {
        "status": "passed",
        "helper": _file_pin(helper),
        "positionsChecked": len(exported_vectors),
        "goldenVectorsChecked": len(golden_vectors),
        "exportedNetwork": exported_vectors,
        "goldenVectors": golden_vectors,
    }


def _clip_round(values: np.ndarray, low: int, high: int, dtype: Any) -> np.ndarray:
    return np.clip(np.rint(values), low, high).astype(dtype)


@dataclass
class FloatNetwork:
    ft_bias: np.ndarray
    ft_weights: np.ndarray
    dense_bias: np.ndarray
    dense_weights: np.ndarray
    output_bias: np.ndarray
    output_weights: np.ndarray

    @classmethod
    def initialize(
        cls, seed: int, feature_count: int = FEATURE_COUNT
    ) -> "FloatNetwork":
        if feature_count not in (FEATURE_COUNT, KING_STATE_FEATURE_COUNT):
            raise ValueError(f"unsupported float-network feature count {feature_count}")
        rng = np.random.default_rng(seed)
        return cls(
            ft_bias=np.full(ACCUMULATOR_SIZE, 24.0, dtype=np.float32),
            ft_weights=rng.normal(
                0.0, 0.50, size=(feature_count, ACCUMULATOR_SIZE)
            ).astype(np.float32),
            dense_bias=np.full(HIDDEN_SIZE, 24.0, dtype=np.float32),
            dense_weights=rng.normal(
                0.0,
                0.025,
                size=(HIDDEN_SIZE, ACCUMULATOR_SIZE * 2),
            ).astype(np.float32),
            output_bias=np.zeros(1, dtype=np.float32),
            output_weights=rng.normal(0.0, 0.10, size=HIDDEN_SIZE).astype(
                np.float32
            ),
        )

    def clone(self) -> "FloatNetwork":
        return FloatNetwork(
            ft_bias=self.ft_bias.copy(),
            ft_weights=self.ft_weights.copy(),
            dense_bias=self.dense_bias.copy(),
            dense_weights=self.dense_weights.copy(),
            output_bias=self.output_bias.copy(),
            output_weights=self.output_weights.copy(),
        )

    def checkpoint_bytes(self) -> bytes:
        arrays = (
            self.ft_bias,
            self.ft_weights,
            self.dense_bias,
            self.dense_weights,
            self.output_bias,
            self.output_weights,
        )
        if not all(np.all(np.isfinite(value)) for value in arrays):
            raise ValueError("float checkpoint contains a non-finite parameter")
        payload = b"".join(
            np.asarray(value, dtype=np.dtype("<f4")).tobytes(order="C")
            for value in arrays
        )
        header = FLOAT_CHECKPOINT_HEADER.pack(
            FLOAT_CHECKPOINT_MAGIC,
            FORMAT_VERSION,
            int(self.ft_weights.shape[0]),
            ACCUMULATOR_SIZE,
            HIDDEN_SIZE,
            len(payload),
            hashlib.sha256(payload).digest(),
        )
        return header + payload

    def write_checkpoint(self, path: Path) -> None:
        _atomic_bytes(path, self.checkpoint_bytes())

    @classmethod
    def read_checkpoint(cls, path: Path) -> "FloatNetwork":
        data = path.read_bytes()
        if len(data) < FLOAT_CHECKPOINT_HEADER.size:
            raise ValueError("float checkpoint is truncated")
        (
            magic,
            format_version,
            features,
            accumulator,
            hidden,
            payload_bytes,
            expected_hash,
        ) = FLOAT_CHECKPOINT_HEADER.unpack_from(data)
        expected = (
            (magic, FLOAT_CHECKPOINT_MAGIC, "magic"),
            (format_version, FORMAT_VERSION, "format version"),
            (accumulator, ACCUMULATOR_SIZE, "accumulator size"),
            (hidden, HIDDEN_SIZE, "hidden size"),
        )
        for actual, wanted, label in expected:
            if actual != wanted:
                raise ValueError(
                    f"bad float-checkpoint {label}: {actual!r}; expected {wanted!r}"
                )
        if features not in (FEATURE_COUNT, KING_STATE_FEATURE_COUNT):
            raise ValueError(
                f"bad float-checkpoint feature count: {features!r}"
            )
        payload = data[FLOAT_CHECKPOINT_HEADER.size :]
        if len(payload) != payload_bytes:
            raise ValueError(
                f"float checkpoint payload is {len(payload)} bytes; "
                f"expected {payload_bytes}"
            )
        actual_hash = hashlib.sha256(payload).digest()
        if actual_hash != expected_hash:
            raise ValueError("float checkpoint payload SHA-256 mismatch")

        offset = 0

        def take(shape: tuple[int, ...]) -> np.ndarray:
            nonlocal offset
            count = math.prod(shape)
            size = count * np.dtype("<f4").itemsize
            value = np.frombuffer(
                payload, dtype=np.dtype("<f4"), count=count, offset=offset
            ).reshape(shape)
            offset += size
            return value.astype(np.float32, copy=True)

        result = cls(
            ft_bias=take((ACCUMULATOR_SIZE,)),
            ft_weights=take((features, ACCUMULATOR_SIZE)),
            dense_bias=take((HIDDEN_SIZE,)),
            dense_weights=take((HIDDEN_SIZE, ACCUMULATOR_SIZE * 2)),
            output_bias=take((1,)),
            output_weights=take((HIDDEN_SIZE,)),
        )
        if offset != len(payload):
            raise ValueError("float checkpoint has trailing payload bytes")
        if not all(
            np.all(np.isfinite(value)) for value in result.parameters().values()
        ):
            raise ValueError("float checkpoint contains a non-finite parameter")
        return result

    @classmethod
    def from_quantized(cls, network: QuantizedNetwork) -> "FloatNetwork":
        """Resume training from an exactly representable exported checkpoint."""

        result = cls(
            ft_bias=network.ft_bias.astype(np.float32),
            ft_weights=network.ft_weights.astype(np.float32),
            dense_bias=(
                network.dense_bias.astype(np.float32) / HIDDEN_DIVISOR
            ),
            dense_weights=(
                network.dense_weights.astype(np.float32) / HIDDEN_DIVISOR
            ),
            output_bias=np.asarray(
                [network.output_bias / OUTPUT_DIVISOR], dtype=np.float32
            ),
            output_weights=(
                network.output_weights.astype(np.float32) / OUTPUT_DIVISOR
            ),
        )
        recovered = result.quantize(network.architecture)
        exact = (
            np.array_equal(recovered.ft_bias, network.ft_bias)
            and np.array_equal(recovered.ft_weights, network.ft_weights)
            and np.array_equal(recovered.dense_bias, network.dense_bias)
            and np.array_equal(recovered.dense_weights, network.dense_weights)
            and recovered.output_bias == network.output_bias
            and np.array_equal(
                recovered.output_weights, network.output_weights
            )
        )
        if not exact:
            raise ValueError(
                "quantized checkpoint cannot be represented losslessly by "
                "the float32 shadow model"
            )
        return result

    def parameters(self) -> dict[str, np.ndarray]:
        return {
            "ft_bias": self.ft_bias,
            "ft_weights": self.ft_weights,
            "dense_bias": self.dense_bias,
            "dense_weights": self.dense_weights,
            "output_bias": self.output_bias,
            "output_weights": self.output_weights,
        }

    def quantize(
        self, architecture: int = ARCHITECTURE_ABSOLUTE
    ) -> QuantizedNetwork:
        return QuantizedNetwork(
            ft_bias=_clip_round(
                self.ft_bias, -(1 << 15), (1 << 15) - 1, np.int16
            ),
            ft_weights=_clip_round(
                self.ft_weights, -(1 << 15), (1 << 15) - 1, np.int16
            ),
            dense_bias=_clip_round(
                self.dense_bias * HIDDEN_DIVISOR,
                -(1 << 31),
                (1 << 31) - 1,
                np.int32,
            ),
            dense_weights=_clip_round(
                self.dense_weights * HIDDEN_DIVISOR, -128, 127, np.int8
            ),
            output_bias=int(
                _clip_round(
                    self.output_bias * OUTPUT_DIVISOR,
                    -(1 << 31),
                    (1 << 31) - 1,
                    np.int32,
                )[0]
            ),
            output_weights=_clip_round(
                self.output_weights * OUTPUT_DIVISOR, -128, 127, np.int8
            ),
            architecture=architecture,
        )

    def _effective_parameters(
        self, quantization_aware: bool
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, np.ndarray]:
        if not quantization_aware:
            return (
                self.ft_bias,
                self.ft_weights,
                self.dense_bias,
                self.dense_weights,
                float(self.output_bias[0]),
                self.output_weights,
            )
        architecture = (
            ARCHITECTURE_KING_STATE_RESIDUAL
            if self.ft_weights.shape[0] == KING_STATE_FEATURE_COUNT
            else ARCHITECTURE_ABSOLUTE
        )
        quantized = self.quantize(architecture)
        return (
            quantized.ft_bias.astype(np.float32),
            quantized.ft_weights.astype(np.float32),
            quantized.dense_bias.astype(np.float32) / HIDDEN_DIVISOR,
            quantized.dense_weights.astype(np.float32) / HIDDEN_DIVISOR,
            float(quantized.output_bias) / OUTPUT_DIVISOR,
            quantized.output_weights.astype(np.float32) / OUTPUT_DIVISOR,
        )

    def forward(
        self,
        stm_features: np.ndarray,
        opponent_features: np.ndarray,
        *,
        quantization_aware: bool,
        need_cache: bool,
    ) -> tuple[np.ndarray, dict[str, np.ndarray] | None]:
        (
            ft_bias,
            ft_weights,
            dense_bias,
            dense_weights,
            output_bias,
            output_weights,
        ) = self._effective_parameters(quantization_aware)
        extended = np.vstack(
            (
                ft_weights,
                np.zeros((1, ACCUMULATOR_SIZE), dtype=np.float32),
            )
        )
        stm_z = ft_bias + extended[stm_features].sum(axis=1)
        opponent_z = ft_bias + extended[opponent_features].sum(axis=1)
        stm_a = np.clip(stm_z, 0.0, ACTIVATION_MAX)
        opponent_a = np.clip(opponent_z, 0.0, ACTIVATION_MAX)
        joined = np.concatenate((stm_a, opponent_a), axis=1)
        dense_z = dense_bias + joined @ dense_weights.T
        if quantization_aware:
            # The quantized weights make dense_z an exact integer-divisor
            # result. Senpai's nearest rounding is modeled with a
            # straight-through gradient below.
            dense_z = np.where(
                dense_z >= 0.0,
                np.floor(dense_z + 0.5),
                -np.floor(-dense_z + 0.5),
            )
        dense_a = np.clip(dense_z, 0.0, ACTIVATION_MAX)
        prediction = output_bias + dense_a @ output_weights
        if quantization_aware:
            prediction = np.where(
                prediction >= 0.0,
                np.floor(prediction + 0.5),
                -np.floor(-prediction + 0.5),
            )
        if not need_cache:
            return prediction.astype(np.float32), None
        return prediction.astype(np.float32), {
            "stm_z": stm_z,
            "opponent_z": opponent_z,
            "joined": joined,
            "dense_z": dense_z,
            "dense_a": dense_a,
            "dense_weights": dense_weights,
            "output_weights": output_weights,
        }


class Adam:
    def __init__(
        self,
        parameters: dict[str, np.ndarray],
        learning_rate: float,
        learning_rate_scales: dict[str, float] | None = None,
        beta1: float = 0.9,
        beta2: float = 0.999,
        epsilon: float = 1e-8,
    ) -> None:
        self.parameters = parameters
        self.learning_rate = learning_rate
        self.learning_rate_scales = learning_rate_scales or {}
        unknown_scales = set(self.learning_rate_scales) - set(parameters)
        if unknown_scales:
            raise ValueError(
                "learning-rate scales name unknown parameters: "
                + ", ".join(sorted(unknown_scales))
            )
        if any(value <= 0.0 for value in self.learning_rate_scales.values()):
            raise ValueError("learning-rate scales must be positive")
        self.beta1 = beta1
        self.beta2 = beta2
        self.epsilon = epsilon
        self.first = {
            name: np.zeros_like(value, dtype=np.float32)
            for name, value in parameters.items()
        }
        self.second = {
            name: np.zeros_like(value, dtype=np.float32)
            for name, value in parameters.items()
        }
        self.step_count = 0

    def step(
        self, gradients: dict[str, np.ndarray], learning_rate_scale: float = 1.0
    ) -> None:
        if learning_rate_scale <= 0.0:
            raise ValueError("Adam step learning-rate scale must be positive")
        self.step_count += 1
        correction1 = 1.0 - self.beta1**self.step_count
        correction2 = 1.0 - self.beta2**self.step_count
        for name, parameter in self.parameters.items():
            gradient = gradients[name]
            first = self.first[name]
            second = self.second[name]
            first *= self.beta1
            first += (1.0 - self.beta1) * gradient
            second *= self.beta2
            second += (1.0 - self.beta2) * gradient * gradient
            parameter -= (
                self.learning_rate
                * learning_rate_scale
                * self.learning_rate_scales.get(name, 1.0)
                * (first / correction1)
                / (np.sqrt(second / correction2) + self.epsilon)
            )
        self._clamp()

    def _clamp(self) -> None:
        np.clip(
            self.parameters["ft_bias"],
            -(1 << 15),
            (1 << 15) - 1,
            out=self.parameters["ft_bias"],
        )
        np.clip(
            self.parameters["ft_weights"],
            -(1 << 15),
            (1 << 15) - 1,
            out=self.parameters["ft_weights"],
        )
        dense_limit_low = -128.0 / HIDDEN_DIVISOR
        dense_limit_high = 127.0 / HIDDEN_DIVISOR
        np.clip(
            self.parameters["dense_weights"],
            dense_limit_low,
            dense_limit_high,
            out=self.parameters["dense_weights"],
        )
        np.clip(
            self.parameters["output_weights"],
            -128.0 / OUTPUT_DIVISOR,
            127.0 / OUTPUT_DIVISOR,
            out=self.parameters["output_weights"],
        )


def _outcome_supervision(
    dataset: Dataset,
    indices: np.ndarray,
    architecture: int,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Select outcome labels and the score presented to outcome BCE.

    Absolute networks retain the original fields and use their network output
    directly. Residual networks use search outcomes and add the fixed
    side-to-move handcrafted score before the logistic conversion.
    """

    if architecture == ARCHITECTURE_ABSOLUTE:
        return dataset.outcome[indices], None
    if not is_residual_architecture(architecture):
        raise ValueError(f"unsupported architecture semantics {architecture}")

    outcome = dataset.search_outcome[indices]
    baseline = dataset.handcrafted_cp[indices]
    supervised = np.isfinite(outcome)
    if np.any(supervised & ~np.isfinite(baseline)):
        raise ValueError(
            "residual outcome supervision requires a finite "
            "handcraftedCpStm baseline for every search outcome"
        )
    return outcome, baseline


def _require_residual_outcomes(
    dataset: Dataset,
    architecture: int,
    outcome_weight: float,
) -> None:
    if (
        is_residual_architecture(architecture)
        and outcome_weight > 0.0
        and not np.any(np.isfinite(dataset.search_outcome))
    ):
        raise ValueError(
            "residual outcome weight is positive but the selected input has "
            "no searchOutcomeStm or searchSideToMoveScore labels"
        )


def loss_and_gradient(
    prediction: np.ndarray,
    target_cp: np.ndarray,
    outcome: np.ndarray,
    cp_weight: float,
    outcome_weight: float,
    outcome_score_offset: np.ndarray | None = None,
) -> tuple[float, np.ndarray, dict[str, float | int | None]]:
    if prediction.shape != target_cp.shape or prediction.shape != outcome.shape:
        raise ValueError("prediction and target arrays must have equal shapes")
    if (
        outcome_score_offset is not None
        and outcome_score_offset.shape != prediction.shape
    ):
        raise ValueError("outcome score offset must match prediction shape")
    gradient = np.zeros_like(prediction, dtype=np.float32)
    total_loss = 0.0
    cp_mask = np.isfinite(target_cp)
    outcome_mask = np.isfinite(outcome)
    cp_count = int(cp_mask.sum())
    outcome_count = int(outcome_mask.sum())
    cp_mae: float | None = None
    outcome_bce: float | None = None
    outcome_score_min: float | None = None
    outcome_score_max: float | None = None

    if cp_count:
        error = (prediction[cp_mask] - target_cp[cp_mask]) / CP_NORMALIZER
        absolute = np.abs(error)
        quadratic = absolute <= CP_HUBER_DELTA
        losses = np.where(
            quadratic,
            0.5 * error * error,
            CP_HUBER_DELTA * (absolute - 0.5 * CP_HUBER_DELTA),
        )
        derivatives = np.where(
            quadratic, error, CP_HUBER_DELTA * np.sign(error)
        ) / CP_NORMALIZER
        total_loss += cp_weight * float(losses.mean())
        gradient[cp_mask] += (
            cp_weight * derivatives.astype(np.float32) / cp_count
        )
        cp_mae = float(np.abs(prediction[cp_mask] - target_cp[cp_mask]).mean())

    if outcome_count:
        if outcome_score_offset is None:
            outcome_scores = prediction[outcome_mask]
        else:
            selected_offset = outcome_score_offset[outcome_mask]
            if not np.all(np.isfinite(selected_offset)):
                raise ValueError(
                    "outcome score offset is non-finite on a supervised row"
                )
            outcome_scores = prediction[outcome_mask] + selected_offset
        logits = outcome_scores / OUTCOME_LOGISTIC_SCALE
        targets = outcome[outcome_mask]
        losses = np.maximum(logits, 0.0) - logits * targets + np.log1p(
            np.exp(-np.abs(logits))
        )
        probabilities = np.where(
            logits >= 0.0,
            1.0 / (1.0 + np.exp(-logits)),
            np.exp(logits) / (1.0 + np.exp(logits)),
        )
        derivatives = (probabilities - targets) / OUTCOME_LOGISTIC_SCALE
        outcome_bce = float(losses.mean())
        outcome_score_min = float(outcome_scores.min())
        outcome_score_max = float(outcome_scores.max())
        total_loss += outcome_weight * outcome_bce
        gradient[outcome_mask] += (
            outcome_weight * derivatives.astype(np.float32) / outcome_count
        )

    return total_loss, gradient, {
        "cpCount": cp_count,
        "cpMae": cp_mae,
        "outcomeCount": outcome_count,
        "outcomeBce": outcome_bce,
        "outcomeBaselineCount": (
            outcome_count if outcome_score_offset is not None else 0
        ),
        "outcomeScoreMin": outcome_score_min,
        "outcomeScoreMax": outcome_score_max,
    }


def gradients(
    dataset: Dataset,
    indices: np.ndarray,
    model: FloatNetwork,
    cp_weight: float,
    outcome_weight: float,
    quantization_aware: bool,
    architecture: int = ARCHITECTURE_ABSOLUTE,
) -> tuple[float, dict[str, np.ndarray], dict[str, float | int | None]]:
    stm_features, opponent_features = dataset.perspective_features(indices)
    prediction, cache = model.forward(
        stm_features,
        opponent_features,
        quantization_aware=quantization_aware,
        need_cache=True,
    )
    assert cache is not None
    outcome, outcome_score_offset = _outcome_supervision(
        dataset, indices, architecture
    )
    loss, output_gradient, metrics = loss_and_gradient(
        prediction,
        dataset.target_cp[indices],
        outcome,
        cp_weight,
        outcome_weight,
        outcome_score_offset,
    )

    output_weights = cache["output_weights"]
    dense_weights = cache["dense_weights"]
    dense_a = cache["dense_a"]
    joined = cache["joined"]

    output_bias_gradient = np.asarray(
        [output_gradient.sum()], dtype=np.float32
    )
    output_weights_gradient = dense_a.T @ output_gradient
    dense_activation_gradient = output_gradient[:, None] * output_weights[None, :]
    dense_mask = (cache["dense_z"] > 0.0) & (
        cache["dense_z"] < ACTIVATION_MAX
    )
    dense_z_gradient = dense_activation_gradient * dense_mask
    dense_bias_gradient = dense_z_gradient.sum(axis=0)
    dense_weights_gradient = dense_z_gradient.T @ joined
    joined_gradient = dense_z_gradient @ dense_weights
    stm_gradient = joined_gradient[:, :ACCUMULATOR_SIZE]
    opponent_gradient = joined_gradient[:, ACCUMULATOR_SIZE:]
    stm_gradient *= (cache["stm_z"] > 0.0) & (
        cache["stm_z"] < ACTIVATION_MAX
    )
    opponent_gradient *= (cache["opponent_z"] > 0.0) & (
        cache["opponent_z"] < ACTIVATION_MAX
    )

    ft_bias_gradient = (stm_gradient + opponent_gradient).sum(axis=0)
    ft_weights_gradient = np.zeros_like(model.ft_weights, dtype=np.float32)
    for row, row_gradient in zip(stm_features, stm_gradient):
        real = row[row != dataset.pad_feature]
        np.add.at(ft_weights_gradient, real, row_gradient)
    for row, row_gradient in zip(opponent_features, opponent_gradient):
        real = row[row != dataset.pad_feature]
        np.add.at(ft_weights_gradient, real, row_gradient)

    return loss, {
        "ft_bias": ft_bias_gradient.astype(np.float32),
        "ft_weights": ft_weights_gradient,
        "dense_bias": dense_bias_gradient.astype(np.float32),
        "dense_weights": dense_weights_gradient.astype(np.float32),
        "output_bias": output_bias_gradient,
        "output_weights": output_weights_gradient.astype(np.float32),
    }, metrics


def predict_dataset(
    dataset: Dataset,
    indices: np.ndarray,
    network: QuantizedNetwork,
    batch_size: int,
) -> np.ndarray:
    result = np.empty(indices.size, dtype=np.int32)
    for start in range(0, indices.size, batch_size):
        part = indices[start : start + batch_size]
        stm, opponent = dataset.perspective_features(part)
        result[start : start + part.size] = network.predict_features(stm, opponent)
    return result


def quantization_penalty(
    dataset: Dataset,
    indices: np.ndarray,
    model: FloatNetwork,
    network: QuantizedNetwork,
    batch_size: int,
) -> dict[str, Any]:
    if indices.size == 0:
        return {"samples": 0, "maeCp": None, "maxCp": None}
    absolute_errors: list[np.ndarray] = []
    for start in range(0, indices.size, batch_size):
        part = indices[start : start + batch_size]
        stm, opponent = dataset.perspective_features(part)
        float_prediction, _ = model.forward(
            stm,
            opponent,
            quantization_aware=False,
            need_cache=False,
        )
        quantized_prediction = network.predict_features(stm, opponent)
        absolute_errors.append(
            np.abs(
                float_prediction.astype(np.float64)
                - quantized_prediction.astype(np.float64)
            )
        )
    errors = np.concatenate(absolute_errors)
    return {
        "samples": int(indices.size),
        "maeCp": float(errors.mean()),
        "maxCp": float(errors.max()),
    }


def evaluate(
    dataset: Dataset,
    indices: np.ndarray,
    network: QuantizedNetwork,
    batch_size: int,
    cp_weight: float,
    outcome_weight: float,
) -> dict[str, Any]:
    output_semantics = ARCHITECTURE_NAMES[network.architecture]
    outcome_score_semantics = (
        "handcrafted-plus-network-correction"
        if is_residual_architecture(network.architecture)
        else "network"
    )
    if indices.size == 0:
        return {
            "samples": 0,
            "loss": None,
            "cpMae": None,
            "outcomeBce": None,
            "networkOutputSemantics": output_semantics,
            "outcomeScoreSemantics": outcome_score_semantics,
        }
    prediction = predict_dataset(dataset, indices, network, batch_size)
    outcome, outcome_score_offset = _outcome_supervision(
        dataset, indices, network.architecture
    )
    loss, _, details = loss_and_gradient(
        prediction.astype(np.float32),
        dataset.target_cp[indices],
        outcome,
        cp_weight,
        outcome_weight,
        outcome_score_offset,
    )
    result: dict[str, Any] = {
        "samples": int(indices.size),
        "loss": float(loss),
        "cpMae": details["cpMae"],
        "outcomeBce": details["outcomeBce"],
        "outcomeCount": details["outcomeCount"],
        "outcomeBaselineCount": details["outcomeBaselineCount"],
        "outcomeScoreMin": details["outcomeScoreMin"],
        "outcomeScoreMax": details["outcomeScoreMax"],
        "networkOutputSemantics": output_semantics,
        "outcomeScoreSemantics": outcome_score_semantics,
        "predictionMin": int(prediction.min()),
        "predictionMax": int(prediction.max()),
        "predictionUnique": int(np.unique(prediction).size),
        "predictionStdDev": float(prediction.std()),
    }
    cp_mask = np.isfinite(dataset.target_cp[indices])
    if np.any(cp_mask):
        target = dataset.target_cp[indices][cp_mask].astype(np.float64)
        predicted = prediction[cp_mask].astype(np.float64)
        error = predicted - target
        result["cpBias"] = float(error.mean())
        result["cpRmse"] = float(np.sqrt(np.mean(error * error)))
        if np.std(target) > 0.0 and np.std(predicted) > 0.0:
            result["cpCorrelation"] = float(np.corrcoef(target, predicted)[0, 1])
        else:
            result["cpCorrelation"] = None
        decisive = np.abs(target) >= 100.0
        result["decisiveSamples"] = int(decisive.sum())
        result["decisiveSignAccuracy"] = (
            float(np.mean(np.sign(predicted[decisive]) == np.sign(target[decisive])))
            if np.any(decisive)
            else None
        )

        feature_counts = np.sum(
            dataset.white_features[indices][cp_mask] != dataset.pad_feature,
            axis=1,
        )
        phase_metrics: dict[str, Any] = {}
        for name, phase_mask in (
            ("ending", feature_counts <= 12),
            ("middlegame", (feature_counts > 12) & (feature_counts < 29)),
            ("opening", feature_counts >= 29),
        ):
            phase_metrics[name] = {
                "samples": int(phase_mask.sum()),
                "cpMae": (
                    float(np.mean(np.abs(error[phase_mask])))
                    if np.any(phase_mask)
                    else None
                ),
            }
        result["phase"] = phase_metrics
    else:
        result.update(
            {
                "cpBias": None,
                "cpRmse": None,
                "cpCorrelation": None,
                "decisiveSamples": 0,
                "decisiveSignAccuracy": None,
                "phase": {},
            }
        )
    return result


def _training_strata(
    dataset: Dataset, indices: np.ndarray
) -> tuple[list[np.ndarray], dict[str, Any]]:
    """Create deterministic phase/score strata without changing sample weight."""

    active_features_per_position = np.sum(
        dataset.white_features[indices] != dataset.pad_feature, axis=1
    )
    phase_cuts = np.quantile(active_features_per_position, [1.0 / 3.0, 2.0 / 3.0])
    phase = np.digitize(active_features_per_position, phase_cuts, right=True)
    cp = dataset.target_cp[indices]
    # Outcome-only rows occupy the neutral score band. CP distillation uses
    # explicit quiet/negative/positive strata to prevent common-mode batches.
    score = np.where(
        np.isfinite(cp),
        np.digitize(cp, [-100.0, 100.0], right=True),
        1,
    )
    strata = []
    counts: dict[str, int] = {}
    for phase_index in range(3):
        for score_index in range(3):
            selected = indices[(phase == phase_index) & (score == score_index)]
            if selected.size:
                strata.append(selected.copy())
                counts[f"phase{phase_index}-score{score_index}"] = int(
                    selected.size
                )
    return strata, {
        "phaseFeatureCuts": [float(value) for value in phase_cuts],
        "counts": counts,
    }


def _interleaved_stratified_order(
    strata: list[np.ndarray], rng: np.random.Generator, batch_size: int
) -> np.ndarray:
    shuffled = []
    for stratum in strata:
        values = stratum.copy()
        rng.shuffle(values)
        shuffled.append(values)
    positions = [0] * len(shuffled)
    output: list[np.ndarray] = []
    while True:
        active = [
            index
            for index, values in enumerate(shuffled)
            if positions[index] < values.size
        ]
        if not active:
            break
        quota = max(1, batch_size // len(active))
        for index in active:
            start = positions[index]
            end = min(start + quota, shuffled[index].size)
            output.append(shuffled[index][start:end])
            positions[index] = end
    return np.concatenate(output)


def _activation_health(
    dataset: Dataset,
    train_indices: np.ndarray,
    model: FloatNetwork,
    *,
    quantization_aware: bool,
) -> dict[str, Any]:
    health_count = min(2048, train_indices.size)
    health_indices = train_indices[:health_count]
    health_stm, health_opponent = dataset.perspective_features(health_indices)
    health_prediction, health_cache = model.forward(
        health_stm,
        health_opponent,
        quantization_aware=quantization_aware,
        need_cache=True,
    )
    assert health_cache is not None
    dense_positive = health_cache["dense_z"] > 0.0
    dense_below_clip = health_cache["dense_z"] < ACTIVATION_MAX
    return {
        "samples": int(health_count),
        "denseActiveFraction": float(
            np.mean(dense_positive & dense_below_clip)
        ),
        "denseDeadUnits": int((~np.any(dense_positive, axis=0)).sum()),
        "denseSaturatedUnits": int(
            (~np.any(dense_below_clip, axis=0)).sum()
        ),
        "predictionMin": float(health_prediction.min()),
        "predictionMax": float(health_prediction.max()),
        "predictionStdDev": float(health_prediction.std()),
    }


def train(
    dataset: Dataset,
    *,
    architecture: int,
    seed: int,
    epochs: int,
    qat_epochs: int,
    batch_size: int,
    learning_rate: float,
    feature_transformer_learning_rate_scale: float,
    dense_bias_learning_rate_scale: float,
    dense_weight_learning_rate_scale: float,
    output_learning_rate_scale: float,
    qat_learning_rate_scale: float,
    initial_model: FloatNetwork | None,
    stratified_batches: bool,
    cp_weight: float,
    outcome_weight: float,
    quiet: bool,
) -> tuple[FloatNetwork, list[dict[str, Any]], int]:
    train_indices = dataset.indices(0)
    if train_indices.size == 0:
        raise ValueError("deterministic split produced no training samples")
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    if not 0 <= qat_epochs <= epochs:
        raise ValueError("QAT epochs must be between zero and total epochs")
    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    if learning_rate <= 0.0:
        raise ValueError("learning rate must be positive")

    model = (
        FloatNetwork.initialize(
            seed, feature_count_for_architecture(architecture)
        )
        if initial_model is None
        else initial_model.clone()
    )
    expected_features = feature_count_for_architecture(architecture)
    if model.ft_weights.shape != (expected_features, ACCUMULATOR_SIZE):
        raise ValueError(
            "initial model feature map does not match architecture "
            f"{ARCHITECTURE_NAMES[architecture]}"
        )
    optimizer = Adam(
        model.parameters(),
        learning_rate,
        learning_rate_scales={
            # Adam's first update is approximately one signed learning-rate
            # step regardless of gradient magnitude. A dense row sees 256
            # joined activations near 24, so an unscaled 0.01 step can move
            # its pre-activation by roughly 61 and kill every ReLU at once.
            "ft_bias": feature_transformer_learning_rate_scale,
            "ft_weights": feature_transformer_learning_rate_scale,
            "dense_bias": dense_bias_learning_rate_scale,
            "dense_weights": dense_weight_learning_rate_scale,
            "output_bias": output_learning_rate_scale,
            "output_weights": output_learning_rate_scale,
        },
    )
    rng = np.random.default_rng(seed)
    strata, strata_metadata = _training_strata(dataset, train_indices)
    validation_indices = dataset.indices(1)
    initial_network = model.quantize(architecture)
    initial_train_metrics = evaluate(
        dataset,
        train_indices,
        initial_network,
        batch_size,
        cp_weight,
        outcome_weight,
    )
    initial_validation_metrics = evaluate(
        dataset,
        validation_indices,
        initial_network,
        batch_size,
        cp_weight,
        outcome_weight,
    )
    initial_selection_metrics = (
        initial_validation_metrics
        if validation_indices.size
        else initial_train_metrics
    )
    best_model = model.clone()
    best_epoch = 0
    best_selection_loss = float(initial_selection_metrics["loss"])
    history: list[dict[str, Any]] = [
        {
            "epoch": 0,
            "stage": "initial",
            "quantizationAware": True,
            "optimizerSteps": 0,
            "meanBatchLoss": None,
            "quantizedTrain": initial_train_metrics,
            "quantizedValidation": initial_validation_metrics,
            "elapsedSeconds": 0.0,
            "batchStrata": strata_metadata,
            "activationHealth": _activation_health(
                dataset,
                train_indices,
                model,
                quantization_aware=True,
            ),
        }
    ]
    if not quiet:
        selection_split = "validation" if validation_indices.size else "train"
        print(
            f"epoch   0/{epochs}: "
            f"initial q-loss={best_selection_loss:.6f} "
            f"selection={selection_split}",
            flush=True,
        )
    for epoch in range(epochs):
        started = time.perf_counter()
        if stratified_batches:
            order = _interleaved_stratified_order(strata, rng, batch_size)
        else:
            order = train_indices.copy()
            rng.shuffle(order)
        quantization_aware = epoch >= epochs - qat_epochs
        batch_losses: list[float] = []
        for start in range(0, order.size, batch_size):
            batch = order[start : start + batch_size]
            loss, batch_gradients, _ = gradients(
                dataset,
                batch,
                model,
                cp_weight,
                outcome_weight,
                quantization_aware,
                architecture,
            )
            if not math.isfinite(loss):
                raise FloatingPointError("training loss became non-finite")
            optimizer.step(
                batch_gradients,
                qat_learning_rate_scale if quantization_aware else 1.0,
            )
            batch_losses.append(loss)
        network = model.quantize(architecture)
        train_metrics = evaluate(
            dataset,
            train_indices,
            network,
            batch_size,
            cp_weight,
            outcome_weight,
        )
        validation_metrics = evaluate(
            dataset,
            validation_indices,
            network,
            batch_size,
            cp_weight,
            outcome_weight,
        )
        row = {
            "epoch": epoch + 1,
            "stage": "training",
            "quantizationAware": quantization_aware,
            "optimizerSteps": optimizer.step_count,
            "meanBatchLoss": float(np.mean(batch_losses)),
            "quantizedTrain": train_metrics,
            "quantizedValidation": validation_metrics,
            "elapsedSeconds": time.perf_counter() - started,
            "batchStrata": strata_metadata,
            "activationHealth": _activation_health(
                dataset,
                train_indices,
                model,
                quantization_aware=quantization_aware,
            ),
        }
        if row["activationHealth"]["denseDeadUnits"] == HIDDEN_SIZE:
            raise FloatingPointError(
                "all dense ReLU units died; reduce the dense-weight "
                "learning-rate scale"
            )
        selection_metrics = (
            validation_metrics if validation_indices.size else train_metrics
        )
        selection_loss = float(selection_metrics["loss"])
        if selection_loss < best_selection_loss:
            best_selection_loss = selection_loss
            best_model = model.clone()
            best_epoch = epoch + 1
        history.append(row)
        if not quiet:
            cp_text = (
                "n/a"
                if train_metrics["cpMae"] is None
                else f"{train_metrics['cpMae']:.2f}"
            )
            outcome_text = (
                "n/a"
                if train_metrics["outcomeBce"] is None
                else f"{train_metrics['outcomeBce']:.5f}"
            )
            print(
                f"epoch {epoch + 1:3d}/{epochs}: "
                f"q-loss={train_metrics['loss']:.6f} "
                f"cp-mae={cp_text} outcome-bce={outcome_text} "
                f"dead={row['activationHealth']['denseDeadUnits']}/{HIDDEN_SIZE} "
                f"qat={'yes' if quantization_aware else 'no'} "
                f"({row['elapsedSeconds']:.2f}s)",
                flush=True,
            )
    return best_model, history, best_epoch


def _atomic_bytes(path: Path, encoded: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _atomic_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_bytes(path, encoded)


def run_round_trip(
    path: Path,
    network: QuantizedNetwork,
    dataset: Dataset,
    indices: np.ndarray,
    batch_size: int,
) -> dict[str, Any]:
    before_bytes = network.to_bytes()
    network.write(path)
    loaded = QuantizedNetwork.read(path)
    after_bytes = loaded.to_bytes()
    if before_bytes != after_bytes:
        raise AssertionError("network changed across export/import round trip")
    before = predict_dataset(dataset, indices, network, batch_size)
    after = predict_dataset(dataset, indices, loaded, batch_size)
    if not np.array_equal(before, after):
        raise AssertionError("quantized inference changed across round trip")
    return {
        "fileBytes": len(before_bytes),
        "sha256": hashlib.sha256(before_bytes).hexdigest(),
        "payloadFnv1a64": f"{int.from_bytes(before_bytes[64:72], 'little'):016x}",
        "predictionsChecked": int(indices.size),
    }


def corrupt_file_must_fail(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[HEADER_BYTES + 17] ^= 0x80
    corrupt = path.with_name(path.name + ".corrupt")
    try:
        corrupt.write_bytes(data)
        try:
            QuantizedNetwork.read(corrupt)
        except ValueError:
            return
        raise AssertionError("corrupted payload passed FNV validation")
    finally:
        try:
            corrupt.unlink()
        except OSError:
            pass


def _feature_self_test() -> None:
    white = active_features(INITIAL_OFEN, 0)
    black = active_features(INITIAL_OFEN, 1)
    if len(white) != 48 or len(black) != 48:
        raise AssertionError("initial OFEN feature count is not 48")
    if max(white) >= FEATURE_COUNT or max(black) >= FEATURE_COUNT:
        raise AssertionError("feature index exceeds frozen feature count")
    if len(set(white)) != len(white) or len(set(black)) != len(black):
        raise AssertionError("initial OFEN contains duplicate active features")
    king_state_white = active_features(
        INITIAL_OFEN, 0, ARCHITECTURE_KING_STATE_RESIDUAL
    )
    king_state_black = active_features(
        INITIAL_OFEN, 1, ARCHITECTURE_KING_STATE_RESIDUAL
    )
    if len(king_state_white) != 50 or len(king_state_black) != 50:
        raise AssertionError("king-state initial OFEN feature count is not 50")
    if (
        max(king_state_white) >= KING_STATE_FEATURE_COUNT
        or max(king_state_black) >= KING_STATE_FEATURE_COUNT
    ):
        raise AssertionError("king-state feature index exceeds architecture")
    clock_changed = active_features(
        INITIAL_OFEN.replace(" 0 1", " 17 1"),
        0,
        ARCHITECTURE_KING_STATE_RESIDUAL,
    )
    removed = set(king_state_white) - set(clock_changed)
    added = set(clock_changed) - set(king_state_white)
    if removed != {KING_STATE_HALFMOVE_FEATURE_BASE} or added != {
        KING_STATE_HALFMOVE_FEATURE_BASE + 3
    }:
        raise AssertionError("halfmove clock did not change exactly one state feature")
    clock_cases = (
        (0, 0), (1, 1), (3, 1), (4, 2), (15, 2), (16, 3),
        (31, 3), (32, 4), (49, 4), (50, 5), (74, 5), (75, 6),
        (89, 6), (90, 7), (99, 7), (100, 7),
    )
    for clock, expected in clock_cases:
        if halfmove_clock_bin(clock) != expected:
            raise AssertionError(
                f"halfmove bin mismatch at {clock}: "
                f"{halfmove_clock_bin(clock)} != {expected}"
            )


def _format_semantics_self_test() -> None:
    absolute = _blank_quantized_network(output_bias=64)
    residual = _blank_quantized_network(
        output_bias=64,
        architecture=ARCHITECTURE_RESIDUAL,
    )
    absolute_bytes = absolute.to_bytes()
    residual_bytes = residual.to_bytes()
    if absolute_bytes[HEADER_BYTES:] != residual_bytes[HEADER_BYTES:]:
        raise AssertionError("network semantics changed the tensor payload")
    if absolute_bytes == residual_bytes:
        raise AssertionError("network semantics are not encoded in the header")
    absolute_round_trip = QuantizedNetwork.from_bytes(absolute_bytes)
    if absolute_round_trip.architecture != ARCHITECTURE_ABSOLUTE:
        raise AssertionError("absolute architecture did not round-trip")
    if absolute_round_trip.to_bytes() != absolute_bytes:
        raise AssertionError("legacy architecture-1 bytes changed on round trip")
    residual_round_trip = QuantizedNetwork.from_bytes(residual_bytes)
    if residual_round_trip.architecture != ARCHITECTURE_RESIDUAL:
        raise AssertionError("residual architecture did not round-trip")
    if residual_round_trip.to_bytes() != residual_bytes:
        raise AssertionError("residual architecture bytes changed on round trip")
    king_state = _blank_quantized_network(
        architecture=ARCHITECTURE_KING_STATE_RESIDUAL
    )
    king_state_bytes = king_state.to_bytes()
    king_state_round_trip = QuantizedNetwork.from_bytes(king_state_bytes)
    if (
        king_state_round_trip.architecture
        != ARCHITECTURE_KING_STATE_RESIDUAL
        or king_state_round_trip.to_bytes() != king_state_bytes
    ):
        raise AssertionError("king-state residual architecture did not round-trip")
    bad = bytearray(absolute_bytes)
    struct.pack_into("<I", bad, 20, 99)
    try:
        QuantizedNetwork.from_bytes(bytes(bad))
    except ValueError as error:
        if "unsupported architecture semantics" not in str(error):
            raise
    else:
        raise AssertionError("unknown network semantics were accepted")

    unsafe_float_resume = _blank_quantized_network(
        output_bias=100_000_001,
        architecture=ARCHITECTURE_RESIDUAL,
    )
    try:
        FloatNetwork.from_quantized(unsafe_float_resume)
    except ValueError as error:
        if "cannot be represented losslessly" not in str(error):
            raise
    else:
        raise AssertionError("lossy float32 checkpoint resume was accepted")


def _king_state_migration_self_test() -> None:
    source = FloatNetwork.initialize(314159).quantize(ARCHITECTURE_RESIDUAL)
    expanded = expand_residual_to_king_state(source)
    occupancy_features = FEATURE_COUNT - 4
    for bucket in range(KING_BUCKET_COUNT):
        begin = bucket * occupancy_features
        if not np.array_equal(
            expanded.ft_weights[begin : begin + occupancy_features],
            source.ft_weights[:occupancy_features],
        ):
            raise AssertionError("king-state migration changed a piece-square row")
    if not np.array_equal(
        expanded.ft_weights[
            KING_STATE_CASTLING_FEATURE_BASE
            : KING_STATE_CASTLING_FEATURE_BASE + 4
        ],
        source.ft_weights[occupancy_features:FEATURE_COUNT],
    ):
        raise AssertionError("king-state migration changed castling rows")
    if np.any(
        expanded.ft_weights[
            KING_STATE_CASTLING_FEATURE_BASE + 4 :
        ]
    ):
        raise AssertionError("new king-state rule features did not start at zero")
    for name in (
        "ft_bias",
        "dense_bias",
        "dense_weights",
        "output_weights",
    ):
        if not np.array_equal(getattr(expanded, name), getattr(source, name)):
            raise AssertionError(f"king-state migration changed {name}")
    if expanded.output_bias != source.output_bias:
        raise AssertionError("king-state migration changed output bias")
    for ofen in CROSS_RUNTIME_OFENS:
        legacy = _predict_ofen(source, ofen)
        migrated = _predict_ofen(expanded, ofen)
        if legacy != migrated:
            raise AssertionError(
                "king-state migration changed initial inference for "
                f"{ofen!r}: {legacy} != {migrated}"
            )
    if QuantizedNetwork.from_bytes(expanded.to_bytes()).to_bytes() != expanded.to_bytes():
        raise AssertionError("migrated king-state network did not round-trip")

    training_dataset = make_dataset_from_records(
        [
            {
                "sampleId": "king-state-gradient-a",
                "ofen": SPARSE_OFEN,
                "targetCpStm": -25,
            },
            {
                "sampleId": "king-state-gradient-b",
                "ofen": CHAMPION_C3_OFEN.replace(
                    " - - 0 1", " - d2,d3 17 1"
                ),
                "targetCpStm": 75,
            },
        ],
        architecture=ARCHITECTURE_KING_STATE_RESIDUAL,
    )
    indices = training_dataset.indices(0)
    float_model = FloatNetwork.from_quantized(expanded)
    loss, parameter_gradients, _ = gradients(
        training_dataset,
        indices,
        float_model,
        cp_weight=1.0,
        outcome_weight=0.0,
        quantization_aware=False,
        architecture=ARCHITECTURE_KING_STATE_RESIDUAL,
    )
    if not math.isfinite(loss):
        raise AssertionError("king-state training gradient produced non-finite loss")
    state_gradient = parameter_gradients["ft_weights"][
        KING_STATE_CASTLING_FEATURE_BASE:
    ]
    if state_gradient.shape[0] != (
        KING_STATE_FEATURE_COUNT - KING_STATE_CASTLING_FEATURE_BASE
    ) or not np.any(state_gradient):
        raise AssertionError("king-state rule features received no training gradient")


def _residual_outcome_supervision_self_test() -> None:
    """Pin residual outcome arithmetic without involving network training."""

    no_cp = np.asarray([float("nan")], dtype=np.float32)
    win = np.asarray([1.0], dtype=np.float32)

    # A +250 handcrafted score and -50 correction must present +200 to BCE.
    residual_loss, residual_gradient, residual_metrics = loss_and_gradient(
        np.asarray([-50.0], dtype=np.float32),
        no_cp,
        win,
        0.0,
        1.0,
        np.asarray([250.0], dtype=np.float32),
    )
    reference_loss, reference_gradient, _ = loss_and_gradient(
        np.asarray([200.0], dtype=np.float32),
        no_cp,
        win,
        0.0,
        1.0,
    )
    if residual_loss != reference_loss or not np.array_equal(
        residual_gradient, reference_gradient
    ):
        raise AssertionError("residual outcome BCE did not add the HCE baseline")
    if residual_gradient[0] == 0.0:
        raise AssertionError("outcome BCE did not flow into the correction")
    if (
        residual_metrics["outcomeBaselineCount"] != 1
        or residual_metrics["outcomeScoreMin"] != 200.0
        or residual_metrics["outcomeScoreMax"] != 200.0
    ):
        raise AssertionError("residual outcome score telemetry is incorrect")

    # Preserve the side-to-move sign: +50 plus a -250 HCE score is -200.
    negative_loss, negative_gradient, _ = loss_and_gradient(
        np.asarray([50.0], dtype=np.float32),
        no_cp,
        win,
        0.0,
        1.0,
        np.asarray([-250.0], dtype=np.float32),
    )
    negative_reference_loss, negative_reference_gradient, _ = loss_and_gradient(
        np.asarray([-200.0], dtype=np.float32),
        no_cp,
        win,
        0.0,
        1.0,
    )
    if negative_loss != negative_reference_loss or not np.array_equal(
        negative_gradient, negative_reference_gradient
    ):
        raise AssertionError("negative HCE outcome baseline changed sign")

    paired_loss, paired_gradient, _ = loss_and_gradient(
        np.asarray([0.0, 0.0], dtype=np.float32),
        np.asarray([float("nan"), float("nan")], dtype=np.float32),
        np.asarray([1.0, 0.0], dtype=np.float32),
        0.0,
        1.0,
        np.asarray([400.0, -400.0], dtype=np.float32),
    )
    if (
        not math.isclose(paired_loss, math.log(1.1), abs_tol=1e-7)
        or not math.isclose(
            float(paired_gradient[0]),
            -float(paired_gradient[1]),
            rel_tol=0.0,
            abs_tol=2e-10,
        )
        or paired_gradient[0] >= 0.0
    ):
        raise AssertionError(
            "side-to-move HCE signs did not produce symmetric outcome gradients"
        )

    # CP supervision remains on the correction and ignores the outcome offset.
    cp_target = np.asarray([75.0], dtype=np.float32)
    no_outcome = np.asarray([float("nan")], dtype=np.float32)
    cp_loss, cp_gradient, _ = loss_and_gradient(
        np.asarray([25.0], dtype=np.float32),
        cp_target,
        no_outcome,
        1.0,
        0.0,
        np.asarray([900.0], dtype=np.float32),
    )
    cp_reference_loss, cp_reference_gradient, _ = loss_and_gradient(
        np.asarray([25.0], dtype=np.float32),
        cp_target,
        no_outcome,
        1.0,
        0.0,
    )
    if cp_loss != cp_reference_loss or not np.array_equal(
        cp_gradient, cp_reference_gradient
    ):
        raise AssertionError("HCE outcome baseline leaked into correction CP loss")

    # Selection is architecture-specific. Extra residual fields cannot alter
    # the legacy absolute outcome path, including for Black to move.
    black_ofen = INITIAL_OFEN.replace(" w KQkq ", " b KQkq ")
    outcome_records = [
        {
            "sampleId": "residual-outcome-black",
            "groupId": "residual-outcome",
            "ofen": black_ofen,
            "targetCpStm": 12,
            "outcomeStm": 0.25,
            "searchOutcomeStm": 0.75,
            "handcraftedCpStm": -300,
        }
    ]
    absolute_dataset = make_dataset_from_records(outcome_records)
    if (
        np.any(np.isfinite(absolute_dataset.search_outcome))
        or np.any(np.isfinite(absolute_dataset.handcrafted_cp))
    ):
        raise AssertionError("absolute loading consumed residual-only fields")
    dataset = make_dataset_from_records(
        outcome_records, residual_outcomes=True
    )
    indices = np.asarray([0], dtype=np.int64)
    absolute_outcome, absolute_offset = _outcome_supervision(
        dataset, indices, ARCHITECTURE_ABSOLUTE
    )
    if absolute_offset is not None or absolute_outcome[0] != 0.25:
        raise AssertionError("residual metadata altered absolute supervision")
    residual_outcome, residual_offset = _outcome_supervision(
        dataset, indices, ARCHITECTURE_RESIDUAL
    )
    if residual_outcome[0] != 0.75:
        raise AssertionError("residual search outcome was not selected")
    assert residual_offset is not None
    if residual_offset[0] != -300.0:
        raise AssertionError("Black-to-move handcrafted baseline changed sign")

    model = FloatNetwork.initialize(718)
    stm, opponent = dataset.perspective_features(indices)
    prediction, _ = model.forward(
        stm,
        opponent,
        quantization_aware=False,
        need_cache=False,
    )
    expected_loss, expected_output_gradient, _ = loss_and_gradient(
        prediction,
        dataset.target_cp[indices],
        residual_outcome,
        0.0,
        1.0,
        residual_offset,
    )
    actual_loss, parameter_gradients, _ = gradients(
        dataset,
        indices,
        model,
        0.0,
        1.0,
        False,
        ARCHITECTURE_RESIDUAL,
    )
    if expected_loss != actual_loss or not np.isclose(
        parameter_gradients["output_bias"][0],
        expected_output_gradient.sum(),
        rtol=0.0,
        atol=1e-12,
    ):
        raise AssertionError(
            "residual outcome gradient did not reach the network output"
        )

    try:
        make_dataset_from_records(
            [
                {
                    "sampleId": "missing-outcome-baseline",
                    "ofen": black_ofen,
                    "targetCpStm": 0,
                    "searchOutcomeStm": 0.5,
                }
            ],
            residual_outcomes=True,
        )
    except ValueError as error:
        if "requires handcraftedCpStm" not in str(error):
            raise
    else:
        raise AssertionError("residual outcome without an HCE baseline was accepted")

    cp_only = make_dataset_from_records(
        [
            {
                "sampleId": "cp-only-residual",
                "ofen": black_ofen,
                "targetCpStm": 0,
                "handcraftedCpStm": -10,
            }
        ],
        residual_outcomes=True,
    )
    try:
        _require_residual_outcomes(
            cp_only, ARCHITECTURE_RESIDUAL, outcome_weight=1.0
        )
    except ValueError as error:
        if "no searchOutcomeStm" not in str(error):
            raise
    else:
        raise AssertionError("positive residual outcome weight became a no-op")
    _require_residual_outcomes(
        cp_only, ARCHITECTURE_RESIDUAL, outcome_weight=0.0
    )
    _require_residual_outcomes(
        cp_only, ARCHITECTURE_ABSOLUTE, outcome_weight=1.0
    )


def _collision_self_test() -> None:
    """Pin cross-split collision policy without relying on corpus contents."""

    seed = 99173
    train_percent = 34.0
    validation_percent = 33.0

    def raw_group_for(split: int) -> str:
        for number in range(10000):
            raw = f"collision-self-test-{split}-{number}"
            effective = f"groupId:{raw}"
            if (
                deterministic_split(
                    effective, seed, train_percent, validation_percent
                )
                == split
            ):
                return raw
        raise AssertionError(f"could not construct split-{split} test group")

    group_values = [raw_group_for(split) for split in range(3)]
    records = [
        {
            "sampleId": f"collision-{split}",
            "groupId": group_values[split],
            "ofen": SPARSE_OFEN,
            "targetCpStm": split,
        }
        for split in range(3)
    ]
    # A non-colliding row in the validation game confirms that filtering does
    # not reassign the rest of a whole-game group to the canonical split.
    records.append(
        {
            "sampleId": "validation-unique",
            "groupId": group_values[1],
            "ofen": CHAMPION_C3_OFEN,
            "targetCpStm": 17,
        }
    )

    with tempfile.TemporaryDirectory(prefix="omega-nnue-collision-") as directory:
        path = Path(directory) / "rows.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(
                    json.dumps(record, separators=(",", ":")) + "\n"
                )
        arguments = {
            "seed": seed,
            "train_percent": train_percent,
            "validation_percent": validation_percent,
        }
        dropped = load_dataset([path], **arguments)
        if (
            dropped.count != 2
            or dropped.input_collision_signatures != 1
            or dropped.input_collision_rows != 3
            or dropped.input_collision_rows_dropped != 2
            or dropped.input_collision_dropped_by_split != (0, 1, 1)
        ):
            raise AssertionError("default collision-drop accounting changed")
        if sorted(dropped.split.tolist()) != [0, 1]:
            raise AssertionError("collision filtering reassigned a game split")

        allowed = load_dataset(
            [path], collision_policy="allow", **arguments
        )
        if (
            allowed.count != 4
            or allowed.input_collision_signatures != 1
            or allowed.input_collision_rows != 3
            or allowed.input_collision_rows_dropped != 0
        ):
            raise AssertionError("collision-allow policy accounting changed")

        try:
            load_dataset([path], collision_policy="error", **arguments)
        except ValueError as error:
            if "identical NNUE inputs crossed" not in str(error):
                raise
        else:
            raise AssertionError("collision-error policy accepted a collision")


def _float_checkpoint_self_test() -> None:
    model = FloatNetwork.initialize(77123)
    with tempfile.TemporaryDirectory(prefix="omega-nnue-float-checkpoint-") as directory:
        path = Path(directory) / "model.float"
        model.write_checkpoint(path)
        first = path.read_bytes()
        model.write_checkpoint(path)
        if path.read_bytes() != first:
            raise AssertionError("float checkpoint bytes are not deterministic")
        loaded = FloatNetwork.read_checkpoint(path)
        for name, value in model.parameters().items():
            if not np.array_equal(value, loaded.parameters()[name]):
                raise AssertionError(
                    f"float checkpoint changed parameter {name!r}"
                )
        corrupt = bytearray(first)
        corrupt[-1] ^= 1
        path.write_bytes(corrupt)
        try:
            FloatNetwork.read_checkpoint(path)
        except ValueError as error:
            if "SHA-256 mismatch" not in str(error):
                raise
        else:
            raise AssertionError("corrupt float checkpoint was accepted")


def _epoch_zero_selection_self_test() -> None:
    """A worsening continuation must export its unchanged initial checkpoint."""

    dataset = make_dataset_from_records(
        [
            {
                "sampleId": "epoch-zero-train",
                "groupId": "epoch-zero-train",
                "ofen": SPARSE_OFEN,
                "targetCpStm": 200,
            },
            {
                "sampleId": "epoch-zero-validation",
                "groupId": "epoch-zero-validation",
                "ofen": CHAMPION_C3_OFEN,
                "targetCpStm": 0,
            },
        ],
        seed=88421,
    )
    dataset.split = np.asarray([0, 1], dtype=dataset.split.dtype)

    initial = _blank_quantized_network()
    initial.dense_bias.fill(HIDDEN_DIVISOR)
    initial_model = FloatNetwork.from_quantized(initial)
    selected, history, selected_epoch = train(
        dataset,
        architecture=ARCHITECTURE_ABSOLUTE,
        seed=88421,
        epochs=1,
        qat_epochs=1,
        batch_size=1,
        learning_rate=0.1,
        feature_transformer_learning_rate_scale=0.1,
        dense_bias_learning_rate_scale=0.1,
        dense_weight_learning_rate_scale=0.1,
        output_learning_rate_scale=1.0,
        qat_learning_rate_scale=1.0,
        initial_model=initial_model,
        stratified_batches=False,
        cp_weight=1.0,
        outcome_weight=0.0,
        quiet=True,
    )
    if selected_epoch != 0:
        raise AssertionError(
            f"worsening continuation selected epoch {selected_epoch}, not epoch 0"
        )
    if [row["epoch"] for row in history] != [0, 1]:
        raise AssertionError("epoch-zero selection history is not explicit")
    initial_loss = float(history[0]["quantizedValidation"]["loss"])
    worsened_loss = float(history[1]["quantizedValidation"]["loss"])
    if not worsened_loss > initial_loss:
        raise AssertionError(
            "epoch-zero selection fixture did not worsen validation loss: "
            f"{initial_loss:.6f} -> {worsened_loss:.6f}"
        )
    selected_network = selected.quantize()
    if selected_network.to_bytes() != initial.to_bytes():
        raise AssertionError("epoch-zero selection changed the initial network")
    with tempfile.TemporaryDirectory(prefix="omega-nnue-epoch-zero-") as directory:
        output = Path(directory) / "selected.nnue"
        selected_network.write(output)
        if QuantizedNetwork.read(output).to_bytes() != initial.to_bytes():
            raise AssertionError(
                "worsening continuation did not export the initial checkpoint"
            )


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train and export Senpai's self-described Omega NNUE architectures "
            "using only NumPy."
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        default=[],
        help="JSONL input; may be repeated",
    )
    parser.add_argument("--output", type=Path, help="output .nnue file")
    parser.add_argument("--manifest", type=Path, help="output manifest JSON")
    parser.add_argument(
        "--network-semantics",
        choices=("absolute", "residual", "king-state-residual"),
        default="absolute",
        help=(
            "self-described runtime meaning stored in the OMNNUE1 architecture "
            "field: absolute replaces HCE; residual adds a learned correction "
            "to HCE; king-state-residual adds king-conditioned piece-square "
            "and exact rule-state features"
        ),
    )
    parser.add_argument(
        "--initial-network",
        type=Path,
        help="quantized checkpoint used to initialize a fine-tuning run",
    )
    parser.add_argument(
        "--expand-residual-to-king-state",
        action="store_true",
        help=(
            "explicitly migrate an architecture-2 --initial-network into "
            "king-state-residual by replicating legacy piece-square rows"
        ),
    )
    parser.add_argument(
        "--initial-float-checkpoint",
        type=Path,
        help="lossless float shadow checkpoint used to resume training",
    )
    parser.add_argument(
        "--float-checkpoint",
        type=Path,
        help="output lossless float shadow checkpoint (normal default: <output>.float)",
    )
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help=(
            "export the exact architecture-2 to architecture-3 migration "
            "without optimization and prove prediction parity on every input OFEN"
        ),
    )
    parser.add_argument(
        "--protocol-pretest",
        action="store_true",
        help=(
            "route by top-level groupId before JSON decoding, then train and "
            "report only split 0/1; held-out split-2 labels remain opaque"
        ),
    )
    parser.add_argument(
        "--candidate-id",
        choices=("K0", "K1", "K2"),
        help="frozen king-state-v1 candidate identity",
    )
    parser.add_argument("--protocol", type=Path, help="sealed protocol JSON")
    parser.add_argument(
        "--prelabel-seal", type=Path, help="pre-label experiment seal"
    )
    parser.add_argument(
        "--corpus-manifest", type=Path, help="frozen residual-corpus manifest"
    )
    parser.add_argument(
        "--static-hce-evaluator",
        type=Path,
        help="frozen static-HCE/C++ parity helper",
    )
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument(
        "--split-seed",
        type=int,
        help="dataset-group split seed (defaults to --seed)",
    )
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--qat-epochs", type=int)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument(
        "--feature-transformer-learning-rate-scale", type=float, default=0.1
    )
    parser.add_argument(
        "--dense-bias-learning-rate-scale", type=float, default=0.1
    )
    parser.add_argument(
        "--dense-weight-learning-rate-scale",
        type=float,
        default=0.005,
        help=(
            "Adam step multiplier for the 256-input dense matrix; the default "
            "prevents a coherent first step from killing every clipped ReLU"
        ),
    )
    parser.add_argument("--output-learning-rate-scale", type=float, default=1.0)
    parser.add_argument(
        "--qat-learning-rate-scale",
        type=float,
        default=0.1,
        help="global Adam step multiplier during quantization-aware epochs",
    )
    parser.add_argument(
        "--unstratified-batches",
        action="store_true",
        help="use a plain epoch shuffle instead of phase/score interleaving",
    )
    parser.add_argument("--cp-weight", type=float, default=1.0)
    parser.add_argument("--outcome-weight", type=float, default=1.0)
    parser.add_argument(
        "--target-cp-clip",
        type=float,
        help="symmetrically clip finite CP labels before training",
    )
    parser.add_argument("--train-percent", type=float, default=80.0)
    parser.add_argument("--validation-percent", type=float, default=10.0)
    parser.add_argument(
        "--group-field",
        help="explicit field (dotted paths allowed) used for leakage-safe splitting",
    )
    parser.add_argument(
        "--collision-policy",
        choices=("drop", "error", "allow"),
        default="drop",
        help=(
            "handling for identical NNUE inputs assigned to different splits "
            "(default: drop lower-priority validation/test rows)"
        ),
    )
    parser.add_argument(
        "--cpp-evaluator",
        type=Path,
        help=(
            "optional C++ test helper supporting "
            "--evaluate-network <network> <ofen>"
        ),
    )
    parser.add_argument("--max-records", type=int)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="reject rather than skip rows with no usable target",
    )
    parser.add_argument(
        "--self-test",
        "--smoke",
        dest="self_test",
        action="store_true",
        help=(
            "overfit a small synthetic or supplied dataset, export it, reload it, "
            "and verify exact quantized inference"
        ),
    )
    parser.add_argument(
        "--smoke-records",
        type=int,
        default=64,
        help="maximum supplied records used by --self-test",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="suppress per-epoch progress"
    )
    result = parser.parse_args(argv)
    result.raw_argv = list(argv)
    return result


def _run_migrate_only(
    args: argparse.Namespace,
    *,
    architecture: int,
    runtime_manifest: dict[str, Any],
    input_pins: list[dict[str, Any]],
    experiment_context: dict[str, Any] | None,
    golden_cases: list[dict[str, Any]],
) -> int:
    if architecture != ARCHITECTURE_KING_STATE_RESIDUAL:
        raise ValueError(
            "--migrate-only requires --network-semantics king-state-residual"
        )
    if not args.expand_residual_to_king_state or args.initial_network is None:
        raise ValueError(
            "--migrate-only requires --initial-network and "
            "--expand-residual-to-king-state"
        )
    if args.initial_float_checkpoint is not None:
        raise ValueError("--migrate-only cannot use --initial-float-checkpoint")
    if args.float_checkpoint is not None:
        raise ValueError("--migrate-only does not emit a float checkpoint")
    if not args.input or args.output is None:
        raise ValueError("--migrate-only requires --input and --output")
    if args.protocol_pretest:
        raise ValueError(
            "--migrate-only examines every OFEN for parity and must not use "
            "--protocol-pretest"
        )

    source_pin = _file_pin(args.initial_network)
    source = QuantizedNetwork.read(args.initial_network)
    migrated = expand_residual_to_king_state(source)
    checked = 0
    ofen_digest = hashlib.sha256()
    for ofen, location in _iter_selective_string(args.input, "ofen"):
        normalized = " ".join(ofen.split())
        try:
            source_score = _predict_ofen(source, normalized)
            migrated_score = _predict_ofen(migrated, normalized)
        except ValueError as error:
            raise ValueError(f"{location}: invalid Omega OFEN: {error}") from error
        if source_score != migrated_score:
            raise AssertionError(
                f"{location}: architecture migration changed prediction "
                f"{source_score} -> {migrated_score}"
            )
        ofen_digest.update(normalized.encode("utf-8"))
        ofen_digest.update(b"\n")
        checked += 1
    if checked == 0:
        raise ValueError("--migrate-only input contains no OFEN rows")

    encoded = migrated.to_bytes()
    _atomic_bytes(args.output, encoded)
    reloaded = QuantizedNetwork.read(args.output)
    if reloaded.to_bytes() != encoded:
        raise AssertionError("migrated network changed on disk round trip")
    cross_runtime = _run_cpp_cross_runtime(
        args.cpp_evaluator, args.output, migrated, golden_cases
    )

    if [_file_pin(path) for path in args.input] != input_pins:
        raise ValueError("an input file changed during migration parity")
    if _file_pin(args.initial_network) != source_pin:
        raise ValueError("the initializer changed during migration parity")
    if _runtime_manifest()["tools"] != runtime_manifest["tools"]:
        raise ValueError("trainer source changed during migration parity")

    manifest: dict[str, Any] = {
        "schemaVersion": 3,
        "kind": "omega-nnue-migrate-only",
        "formatVersion": FORMAT_VERSION,
        "networkSemantics": "king-state-residual",
        "candidateId": args.candidate_id,
        "experiment": experiment_context,
        "rawArgv": list(args.raw_argv),
        "inputs": input_pins,
        "initialNetwork": source_pin,
        "initialNetworkMigration": {
            "requested": True,
            "applied": True,
            "sourceArchitecture": ARCHITECTURE_RESIDUAL,
            "targetArchitecture": ARCHITECTURE_KING_STATE_RESIDUAL,
            "pieceSquareRows": (
                "replicated-identically-across-29-king-buckets"
            ),
            "newStateRows": "zero",
            "denseLayers": "copied-exactly",
        },
        "migrationParity": {
            "status": "passed",
            "scope": "all-corpus-ofens",
            "positionsChecked": checked,
            "ofenSequenceSha256": ofen_digest.hexdigest(),
            "sourcePredictionSemantics": "architecture-2 residual correction",
            "targetPredictionSemantics": "architecture-3 residual correction",
            "mismatches": 0,
            "targetFieldsDecoded": 0,
            "targetFieldsEmitted": 0,
        },
        "roundTrip": {
            "fileBytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "payloadFnv1a64": f"{int.from_bytes(encoded[64:72], 'little'):016x}",
            "predictionsChecked": checked,
        },
        "crossRuntime": cross_runtime,
        "environment": runtime_manifest,
        "seed": args.seed,
        "modelSeed": args.seed,
        "splitSeed": args.seed if args.split_seed is None else args.split_seed,
        "strict": bool(args.strict),
        "heldOut": {
            "labelsLoaded": False,
            "labelsEvaluated": False,
            "labelsEmitted": False,
            "featureOnlyMigrationParityAllowed": True,
        },
        "training": {
            "performed": False,
            "epochs": 0,
            "selectedEpoch": 0,
        },
    }
    manifest_path = args.manifest or args.output.with_suffix(
        args.output.suffix + ".json"
    )
    _atomic_json(manifest_path, manifest)
    print(
        f"migrated architecture 2 -> 3 with exact parity on {checked} OFENs",
        flush=True,
    )
    print(f"wrote manifest: {manifest_path.resolve()}", flush=True)
    print(f"wrote network: {args.output.resolve()}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    architecture = {
        "absolute": ARCHITECTURE_ABSOLUTE,
        "residual": ARCHITECTURE_RESIDUAL,
        "king-state-residual": ARCHITECTURE_KING_STATE_RESIDUAL,
    }[args.network_semantics]
    if args.initial_network is not None and args.initial_float_checkpoint is not None:
        raise ValueError(
            "--initial-network and --initial-float-checkpoint are mutually exclusive"
        )
    if args.expand_residual_to_king_state:
        if args.initial_network is None:
            raise ValueError(
                "--expand-residual-to-king-state requires --initial-network"
            )
        if architecture != ARCHITECTURE_KING_STATE_RESIDUAL:
            raise ValueError(
                "--expand-residual-to-king-state requires "
                "--network-semantics king-state-residual"
            )
    if args.cp_weight < 0.0 or args.outcome_weight < 0.0:
        raise ValueError("loss weights cannot be negative")
    if args.cp_weight == 0.0 and args.outcome_weight == 0.0:
        raise ValueError("at least one loss weight must be positive")
    if args.target_cp_clip is not None and args.target_cp_clip <= 0.0:
        raise ValueError("--target-cp-clip must be positive")
    learning_rate_scales = {
        "--feature-transformer-learning-rate-scale": (
            args.feature_transformer_learning_rate_scale
        ),
        "--dense-bias-learning-rate-scale": args.dense_bias_learning_rate_scale,
        "--dense-weight-learning-rate-scale": (
            args.dense_weight_learning_rate_scale
        ),
        "--output-learning-rate-scale": args.output_learning_rate_scale,
        "--qat-learning-rate-scale": args.qat_learning_rate_scale,
    }
    for name, value in learning_rate_scales.items():
        if value <= 0.0:
            raise ValueError(f"{name} must be positive")

    runtime_manifest = _runtime_manifest()
    input_pins = [_file_pin(path) for path in args.input]
    experiment_context = _experiment_context(args)
    residual_targets_validated = 0
    split_seed = args.seed if args.split_seed is None else args.split_seed
    initial_network_pin = (
        _file_pin(args.initial_network) if args.initial_network is not None else None
    )
    initial_float_checkpoint_pin = (
        _file_pin(args.initial_float_checkpoint)
        if args.initial_float_checkpoint is not None
        else None
    )
    _feature_self_test()
    _format_semantics_self_test()
    _king_state_migration_self_test()
    _residual_outcome_supervision_self_test()
    _collision_self_test()
    _float_checkpoint_self_test()
    _epoch_zero_selection_self_test()
    golden_cases = _golden_runtime_cases()
    golden_python = _golden_python_self_test(golden_cases)
    if args.migrate_only:
        return _run_migrate_only(
            args,
            architecture=architecture,
            runtime_manifest=runtime_manifest,
            input_pins=input_pins,
            experiment_context=experiment_context,
            golden_cases=golden_cases,
        )
    if args.protocol_pretest and args.self_test:
        raise ValueError("--protocol-pretest cannot be combined with --self-test")
    protocol_temporary: tempfile.TemporaryDirectory[str] | None = None
    dataset_inputs = args.input
    pretest_audit: dict[str, Any] | None = None
    if args.protocol_pretest:
        if experiment_context is None:
            raise ValueError(
                "--protocol-pretest requires the complete sealed experiment context"
            )
        protocol_temporary, dataset_inputs, pretest_audit = (
            _make_protocol_pretest_input(
                args.input,
                split_seed=split_seed,
                train_percent=args.train_percent,
                validation_percent=args.validation_percent,
                group_field=args.group_field,
            )
        )
    residual_targets_validated = (
        _validate_residual_targets(dataset_inputs)
        if is_residual_architecture(architecture) and dataset_inputs
        else 0
    )
    temporary_output: Path | None = None
    if args.self_test:
        if dataset_inputs:
            dataset = load_dataset(
                dataset_inputs,
                seed=split_seed,
                train_percent=100.0,
                validation_percent=0.0,
                explicit_group_field=args.group_field,
                max_records=min(
                    args.smoke_records,
                    args.max_records
                    if args.max_records is not None
                    else args.smoke_records,
                ),
                strict=args.strict,
                all_train=True,
                collision_policy=args.collision_policy,
                residual_outcomes=is_residual_architecture(architecture),
                architecture=architecture,
            )
        else:
            dataset = make_dataset_from_records(
                make_synthetic_records(),
                seed=split_seed,
                collision_policy=args.collision_policy,
                residual_outcomes=is_residual_architecture(architecture),
                architecture=architecture,
            )
        epochs = args.epochs if args.epochs is not None else 200
        qat_epochs = (
            args.qat_epochs
            if args.qat_epochs is not None
            else min(20, max(1, epochs // 5))
        )
        if args.output is None:
            handle, name = tempfile.mkstemp(suffix=".nnue")
            os.close(handle)
            temporary_output = Path(name)
            output = temporary_output
        else:
            output = args.output
    else:
        if not args.input:
            raise ValueError("normal training requires at least one --input")
        if args.output is None:
            raise ValueError("normal training requires --output")
        dataset = load_dataset(
            dataset_inputs,
            seed=split_seed,
            train_percent=args.train_percent,
            validation_percent=args.validation_percent,
            explicit_group_field=args.group_field,
            max_records=args.max_records,
            strict=args.strict,
            collision_policy=args.collision_policy,
            residual_outcomes=is_residual_architecture(architecture),
            architecture=architecture,
        )
        epochs = args.epochs if args.epochs is not None else 15
        qat_epochs = (
            args.qat_epochs
            if args.qat_epochs is not None
            else min(3, max(1, epochs // 4))
        )
        output = args.output

    if args.input:
        _require_residual_outcomes(
            dataset, architecture, args.outcome_weight
        )

    float_checkpoint_path = args.float_checkpoint
    if float_checkpoint_path is None and not args.self_test:
        float_checkpoint_path = output.with_suffix(output.suffix + ".float")

    clipped_target_count = 0
    if args.target_cp_clip is not None:
        finite_cp = np.isfinite(dataset.target_cp)
        clipped_target_count = int(
            np.sum(np.abs(dataset.target_cp[finite_cp]) > args.target_cp_clip)
        )
        dataset.target_cp[finite_cp] = np.clip(
            dataset.target_cp[finite_cp],
            -args.target_cp_clip,
            args.target_cp_clip,
        )

    train_indices = dataset.indices(0)
    migration_applied = False
    if args.initial_float_checkpoint is not None:
        initial_model = FloatNetwork.read_checkpoint(args.initial_float_checkpoint)
        expected_features = feature_count_for_architecture(architecture)
        if initial_model.ft_weights.shape[0] != expected_features:
            raise ValueError(
                "--initial-float-checkpoint feature map does not match "
                f"--network-semantics {args.network_semantics}"
            )
    elif args.initial_network is not None:
        initial_quantized = QuantizedNetwork.read(args.initial_network)
        if args.expand_residual_to_king_state:
            initial_quantized = expand_residual_to_king_state(
                initial_quantized
            )
            migration_applied = True
        elif initial_quantized.architecture != architecture:
            raise ValueError(
                "--initial-network semantics do not match "
                f"--network-semantics {args.network_semantics}: "
                f"network is {ARCHITECTURE_NAMES[initial_quantized.architecture]}"
            )
        initial_model = FloatNetwork.from_quantized(initial_quantized)
    else:
        initial_model = FloatNetwork.initialize(
            args.seed, feature_count_for_architecture(architecture)
        )
    initial = initial_model.quantize(architecture)
    initial_metrics = evaluate(
        dataset,
        train_indices,
        initial,
        args.batch_size,
        args.cp_weight,
        args.outcome_weight,
    )
    print(
        f"loaded {dataset.count} labeled rows "
        f"(read={dataset.records_read}, skipped={dataset.records_skipped}); "
        f"split train/validation/test="
        f"{dataset.indices(0).size}/{dataset.indices(1).size}/{dataset.indices(2).size}; "
        f"cross-split NNUE inputs={dataset.input_collision_signatures} "
        f"signatures/{dataset.input_collision_rows} rows, "
        f"dropped={dataset.input_collision_rows_dropped}",
        flush=True,
    )
    model, history, selected_epoch = train(
        dataset,
        architecture=architecture,
        seed=args.seed,
        epochs=epochs,
        qat_epochs=qat_epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        feature_transformer_learning_rate_scale=(
            args.feature_transformer_learning_rate_scale
        ),
        dense_bias_learning_rate_scale=args.dense_bias_learning_rate_scale,
        dense_weight_learning_rate_scale=args.dense_weight_learning_rate_scale,
        output_learning_rate_scale=args.output_learning_rate_scale,
        qat_learning_rate_scale=args.qat_learning_rate_scale,
        initial_model=initial_model,
        stratified_batches=not args.unstratified_batches,
        cp_weight=args.cp_weight,
        outcome_weight=args.outcome_weight,
        quiet=args.quiet,
    )
    network = model.quantize(architecture)

    current_input_pins = [_file_pin(path) for path in args.input]
    if current_input_pins != input_pins:
        raise ValueError("an input file changed while training was in progress")
    current_initial_network_pin = (
        _file_pin(args.initial_network) if args.initial_network is not None else None
    )
    if current_initial_network_pin != initial_network_pin:
        raise ValueError("the initial network changed while training was in progress")
    current_initial_float_checkpoint_pin = (
        _file_pin(args.initial_float_checkpoint)
        if args.initial_float_checkpoint is not None
        else None
    )
    if current_initial_float_checkpoint_pin != initial_float_checkpoint_pin:
        raise ValueError(
            "the initial float checkpoint changed while training was in progress"
        )
    if _runtime_manifest()["tools"] != runtime_manifest["tools"]:
        raise ValueError("trainer source changed while training was in progress")

    all_indices = np.arange(dataset.count, dtype=np.int64)
    round_trip = run_round_trip(
        output, network, dataset, all_indices, args.batch_size
    )
    cross_runtime = _run_cpp_cross_runtime(
        args.cpp_evaluator, output, network, golden_cases
    )
    final_metrics: dict[str, Any] = {
        "train": evaluate(
            dataset,
            dataset.indices(0),
            network,
            args.batch_size,
            args.cp_weight,
            args.outcome_weight,
        ),
        "validation": evaluate(
            dataset,
            dataset.indices(1),
            network,
            args.batch_size,
            args.cp_weight,
            args.outcome_weight,
        ),
    }
    quantization_metrics: dict[str, Any] = {
        "train": quantization_penalty(
            dataset,
            dataset.indices(0),
            model,
            network,
            args.batch_size,
        ),
        "validation": quantization_penalty(
            dataset,
            dataset.indices(1),
            model,
            network,
            args.batch_size,
        ),
    }
    if not args.protocol_pretest:
        final_metrics["test"] = evaluate(
            dataset,
            dataset.indices(2),
            network,
            args.batch_size,
            args.cp_weight,
            args.outcome_weight,
        )
        quantization_metrics["test"] = quantization_penalty(
            dataset,
            dataset.indices(2),
            model,
            network,
            args.batch_size,
        )
    float_checkpoint_pin = None
    if float_checkpoint_path is not None:
        model.write_checkpoint(float_checkpoint_path)
        reloaded_float = FloatNetwork.read_checkpoint(float_checkpoint_path)
        for name, value in model.parameters().items():
            if not np.array_equal(
                value, reloaded_float.parameters()[name], equal_nan=False
            ):
                raise AssertionError(
                    f"float checkpoint changed parameter {name!r} on round trip"
                )
        float_checkpoint_pin = _file_pin(float_checkpoint_path)

    if args.self_test:
        corrupt_file_must_fail(output)
        initial_loss = float(initial_metrics["loss"])
        final_loss = float(final_metrics["train"]["loss"])
        if not final_loss < initial_loss * 0.80:
            raise AssertionError(
                "smoke overfit did not improve quantized training loss by 20%: "
                f"{initial_loss:.6f} -> {final_loss:.6f}"
            )
        print(
            f"self-test passed: quantized loss {initial_loss:.6f} -> "
            f"{final_loss:.6f}; exact round trip checked "
            f"{round_trip['predictionsChecked']} predictions",
            flush=True,
        )

    record_counts: dict[str, Any] = {
        "read": dataset.records_read,
        "accepted": dataset.count,
        "skipped": dataset.records_skipped,
        "train": int(dataset.indices(0).size),
        "validation": int(dataset.indices(1).size),
        "absoluteOutcomeTargets": int(np.isfinite(dataset.outcome).sum()),
        "searchOutcomeTargets": int(
            np.isfinite(dataset.search_outcome).sum()
        ),
        "handcraftedBaselines": int(
            np.isfinite(dataset.handcrafted_cp).sum()
        ),
    }
    if args.protocol_pretest:
        assert pretest_audit is not None
        record_counts["testWithheld"] = pretest_audit["rows"]["testWithheld"]
    else:
        record_counts["test"] = int(dataset.indices(2).size)

    manifest = {
        "schemaVersion": 3 if args.protocol_pretest else 2,
        "formatVersion": FORMAT_VERSION,
        "networkSemantics": args.network_semantics,
        "candidateId": args.candidate_id,
        "experiment": experiment_context,
        "rawArgv": list(args.raw_argv),
        "strict": bool(args.strict),
        "protocolPretest": pretest_audit,
        "architecture": {
            "id": architecture,
            "name": (
                "KingPS104-state-48376x128-256x32x1"
                if architecture == ARCHITECTURE_KING_STATE_RESIDUAL
                else "PS104-shared-1668x128-256x32x1"
            ),
            "outputSemantics": args.network_semantics,
            "runtimeEvaluation": (
                "handcrafted-plus-network-correction"
                if is_residual_architecture(architecture)
                else "network"
            ),
            "headerBytes": HEADER_BYTES,
            "payloadBytes": payload_bytes_for_architecture(architecture),
            "features": feature_count_for_architecture(architecture),
            "accumulator": ACCUMULATOR_SIZE,
            "hidden": HIDDEN_SIZE,
            "activationMax": ACTIVATION_MAX,
            "hiddenDivisor": HIDDEN_DIVISOR,
            "outputDivisor": OUTPUT_DIVISOR,
        },
        "inputs": input_pins,
        "initialNetwork": initial_network_pin,
        "initialNetworkMigration": {
            "requested": args.expand_residual_to_king_state,
            "applied": migration_applied,
            "sourceArchitecture": (
                None
                if initial_network_pin is None
                else (
                    ARCHITECTURE_RESIDUAL
                    if migration_applied
                    else architecture
                )
            ),
            "targetArchitecture": architecture,
            "pieceSquareRows": (
                "replicated-identically-across-29-king-buckets"
                if migration_applied
                else None
            ),
            "newStateRows": "zero" if migration_applied else None,
            "denseLayers": "copied-exactly" if migration_applied else None,
        },
        "initialFloatCheckpoint": initial_float_checkpoint_pin,
        "floatCheckpoint": float_checkpoint_pin,
        "environment": runtime_manifest,
        "seed": args.seed,
        "modelSeed": args.seed,
        "splitSeed": split_seed,
        "groups": len(set(dataset.groups)),
        "records": record_counts,
        "inputCollisions": {
            "policy": dataset.collision_policy,
            "signaturesDetected": dataset.input_collision_signatures,
            "rowsDetected": dataset.input_collision_rows,
            "rowsDropped": dataset.input_collision_rows_dropped,
            "droppedBySplit": {
                "train": dataset.input_collision_dropped_by_split[0],
                "validation": dataset.input_collision_dropped_by_split[1],
                "test": dataset.input_collision_dropped_by_split[2],
            },
            "examples": dataset.input_collision_examples,
            "splitPriority": ["train", "validation", "test"],
            "warning": (
                "Exact NNUE-input deduplication cannot detect related opening "
                "families or color/rank symmetry families; those require "
                "upstream group IDs."
            ),
        },
        "training": {
            "epochs": epochs,
            "qatEpochs": qat_epochs,
            "batchSize": args.batch_size,
            "learningRate": args.learning_rate,
            "featureTransformerLearningRateScale": (
                args.feature_transformer_learning_rate_scale
            ),
            "denseBiasLearningRateScale": args.dense_bias_learning_rate_scale,
            "denseWeightLearningRateScale": (
                args.dense_weight_learning_rate_scale
            ),
            "outputLearningRateScale": args.output_learning_rate_scale,
            "qatLearningRateScale": args.qat_learning_rate_scale,
            "batchOrdering": (
                "plain-shuffle"
                if args.unstratified_batches
                else "phase-score-interleaved"
            ),
            "cpWeight": args.cp_weight,
            "outcomeWeight": args.outcome_weight,
            "outcomeSupervision": {
                "targetFields": (
                    ["searchOutcomeStm", "searchSideToMoveScore"]
                    if is_residual_architecture(architecture)
                    else ["outcomeStm", "sideToMoveScore"]
                ),
                "score": (
                    "handcraftedCpStm + network correction"
                    if is_residual_architecture(architecture)
                    else "network output"
                ),
                "fixedBaselineField": (
                    "handcraftedCpStm"
                    if is_residual_architecture(architecture)
                    else None
                ),
                "gradientDestination": "network output",
            },
            "targetCpClip": args.target_cp_clip,
            "targetsClipped": clipped_target_count,
            "cpNormalizer": CP_NORMALIZER,
            "cpHuberDeltaNormalized": CP_HUBER_DELTA,
            "outcomeLogisticScale": OUTCOME_LOGISTIC_SCALE,
            "groupField": args.group_field,
            "collisionPolicy": args.collision_policy,
            "strict": bool(args.strict),
            "residualTargetsValidated": residual_targets_validated,
            "trainPercent": 100.0 if args.self_test else args.train_percent,
            "validationPercent": (
                0.0 if args.self_test else args.validation_percent
            ),
        },
        "initialQuantizedTrain": initial_metrics,
        "initialQuantizedValidation": history[0]["quantizedValidation"],
        "finalQuantized": final_metrics,
        "quantizationPenalty": quantization_metrics,
        "roundTrip": round_trip,
        "goldenPython": golden_python,
        "crossRuntime": cross_runtime,
        "history": history,
        "selectedEpoch": selected_epoch,
        "selection": {
            "metric": "quantized loss",
            "split": (
                "validation" if dataset.indices(1).size else "train"
            ),
            "baselineEpoch": 0,
            "selectedEpoch": selected_epoch,
            "selectedInitialCheckpoint": selected_epoch == 0,
        },
        "selfTest": bool(args.self_test),
    }
    manifest_path = args.manifest
    if manifest_path is None and temporary_output is None:
        manifest_path = output.with_suffix(output.suffix + ".json")
    if manifest_path is not None:
        _atomic_json(manifest_path, manifest)
        print(f"wrote manifest: {manifest_path.resolve()}", flush=True)
    print(f"wrote network: {output.resolve()}", flush=True)

    if temporary_output is not None:
        try:
            temporary_output.unlink()
        except OSError:
            pass
    if protocol_temporary is not None:
        protocol_temporary.cleanup()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, AssertionError, FloatingPointError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
