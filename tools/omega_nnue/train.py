#!/usr/bin/env python3
"""Train and export the first dependency-free Omega NNUE network."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time

import numpy as np
import omega_nnue as omega_nnue_module

from omega_nnue import (
    ACCUMULATOR_SIZE,
    ACTIVATION_MAX,
    FEATURE_COUNT,
    FORMAT_VERSION,
    HEADER_BYTES,
    HIDDEN_DIVISOR,
    HIDDEN_SIZE,
    OUTPUT_DIVISOR,
    PAD_FEATURE,
    PAYLOAD_BYTES,
    Dataset,
    QuantizedNetwork,
    active_features,
    deterministic_split,
    load_dataset,
    make_dataset_from_records,
    make_synthetic_records,
)


CP_NORMALIZER = 100.0
CP_HUBER_DELTA = 2.0
OUTCOME_LOGISTIC_SCALE = 400.0 / math.log(10.0)

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


def _blank_quantized_network(*, output_bias: int = 0) -> QuantizedNetwork:
    return QuantizedNetwork(
        ft_bias=np.zeros(ACCUMULATOR_SIZE, dtype=np.int16),
        ft_weights=np.zeros(
            (FEATURE_COUNT, ACCUMULATOR_SIZE), dtype=np.int16
        ),
        dense_bias=np.zeros(HIDDEN_SIZE, dtype=np.int32),
        dense_weights=np.zeros(
            (HIDDEN_SIZE, ACCUMULATOR_SIZE * 2), dtype=np.int8
        ),
        output_bias=output_bias,
        output_weights=np.zeros(HIDDEN_SIZE, dtype=np.int8),
    )


def _predict_ofen(network: QuantizedNetwork, ofen: str) -> int:
    white = np.asarray([active_features(ofen, 0)], dtype=np.uint16)
    black = np.asarray([active_features(ofen, 1)], dtype=np.uint16)
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
    def initialize(cls, seed: int) -> "FloatNetwork":
        rng = np.random.default_rng(seed)
        return cls(
            ft_bias=np.full(ACCUMULATOR_SIZE, 24.0, dtype=np.float32),
            ft_weights=rng.normal(
                0.0, 0.20, size=(FEATURE_COUNT, ACCUMULATOR_SIZE)
            ).astype(np.float32),
            dense_bias=np.full(HIDDEN_SIZE, 24.0, dtype=np.float32),
            dense_weights=rng.normal(
                0.0,
                0.025,
                size=(HIDDEN_SIZE, ACCUMULATOR_SIZE * 2),
            ).astype(np.float32),
            output_bias=np.zeros(1, dtype=np.float32),
            output_weights=rng.normal(0.0, 0.025, size=HIDDEN_SIZE).astype(
                np.float32
            ),
        )

    def parameters(self) -> dict[str, np.ndarray]:
        return {
            "ft_bias": self.ft_bias,
            "ft_weights": self.ft_weights,
            "dense_bias": self.dense_bias,
            "dense_weights": self.dense_weights,
            "output_bias": self.output_bias,
            "output_weights": self.output_weights,
        }

    def quantize(self) -> QuantizedNetwork:
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
        quantized = self.quantize()
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
        beta1: float = 0.9,
        beta2: float = 0.999,
        epsilon: float = 1e-8,
    ) -> None:
        self.parameters = parameters
        self.learning_rate = learning_rate
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

    def step(self, gradients: dict[str, np.ndarray]) -> None:
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


def loss_and_gradient(
    prediction: np.ndarray,
    target_cp: np.ndarray,
    outcome: np.ndarray,
    cp_weight: float,
    outcome_weight: float,
) -> tuple[float, np.ndarray, dict[str, float | int | None]]:
    gradient = np.zeros_like(prediction, dtype=np.float32)
    total_loss = 0.0
    cp_mask = np.isfinite(target_cp)
    outcome_mask = np.isfinite(outcome)
    cp_count = int(cp_mask.sum())
    outcome_count = int(outcome_mask.sum())
    cp_mae: float | None = None
    outcome_bce: float | None = None

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
        logits = prediction[outcome_mask] / OUTCOME_LOGISTIC_SCALE
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
        total_loss += outcome_weight * outcome_bce
        gradient[outcome_mask] += (
            outcome_weight * derivatives.astype(np.float32) / outcome_count
        )

    return total_loss, gradient, {
        "cpCount": cp_count,
        "cpMae": cp_mae,
        "outcomeCount": outcome_count,
        "outcomeBce": outcome_bce,
    }


def gradients(
    dataset: Dataset,
    indices: np.ndarray,
    model: FloatNetwork,
    cp_weight: float,
    outcome_weight: float,
    quantization_aware: bool,
) -> tuple[float, dict[str, np.ndarray], dict[str, float | int | None]]:
    stm_features, opponent_features = dataset.perspective_features(indices)
    prediction, cache = model.forward(
        stm_features,
        opponent_features,
        quantization_aware=quantization_aware,
        need_cache=True,
    )
    assert cache is not None
    loss, output_gradient, metrics = loss_and_gradient(
        prediction,
        dataset.target_cp[indices],
        dataset.outcome[indices],
        cp_weight,
        outcome_weight,
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
        real = row[row != PAD_FEATURE]
        np.add.at(ft_weights_gradient, real, row_gradient)
    for row, row_gradient in zip(opponent_features, opponent_gradient):
        real = row[row != PAD_FEATURE]
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


def evaluate(
    dataset: Dataset,
    indices: np.ndarray,
    network: QuantizedNetwork,
    batch_size: int,
    cp_weight: float,
    outcome_weight: float,
) -> dict[str, Any]:
    if indices.size == 0:
        return {"samples": 0, "loss": None, "cpMae": None, "outcomeBce": None}
    prediction = predict_dataset(dataset, indices, network, batch_size)
    loss, _, details = loss_and_gradient(
        prediction.astype(np.float32),
        dataset.target_cp[indices],
        dataset.outcome[indices],
        cp_weight,
        outcome_weight,
    )
    return {
        "samples": int(indices.size),
        "loss": float(loss),
        "cpMae": details["cpMae"],
        "outcomeBce": details["outcomeBce"],
        "predictionMin": int(prediction.min()),
        "predictionMax": int(prediction.max()),
    }


def train(
    dataset: Dataset,
    *,
    seed: int,
    epochs: int,
    qat_epochs: int,
    batch_size: int,
    learning_rate: float,
    cp_weight: float,
    outcome_weight: float,
    quiet: bool,
) -> tuple[FloatNetwork, list[dict[str, Any]]]:
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

    model = FloatNetwork.initialize(seed)
    optimizer = Adam(model.parameters(), learning_rate)
    rng = np.random.default_rng(seed)
    history: list[dict[str, Any]] = []
    for epoch in range(epochs):
        started = time.perf_counter()
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
            )
            if not math.isfinite(loss):
                raise FloatingPointError("training loss became non-finite")
            optimizer.step(batch_gradients)
            batch_losses.append(loss)
        network = model.quantize()
        train_metrics = evaluate(
            dataset,
            train_indices,
            network,
            batch_size,
            cp_weight,
            outcome_weight,
        )
        row = {
            "epoch": epoch + 1,
            "quantizationAware": quantization_aware,
            "meanBatchLoss": float(np.mean(batch_losses)),
            "quantizedTrain": train_metrics,
            "elapsedSeconds": time.perf_counter() - started,
        }
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
                f"qat={'yes' if quantization_aware else 'no'} "
                f"({row['elapsedSeconds']:.2f}s)",
                flush=True,
            )
    return model, history


def _atomic_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
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


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train and export Senpai's frozen PS104 Omega NNUE architecture "
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
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--qat-epochs", type=int)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--cp-weight", type=float, default=1.0)
    parser.add_argument("--outcome-weight", type=float, default=1.0)
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    if args.cp_weight < 0.0 or args.outcome_weight < 0.0:
        raise ValueError("loss weights cannot be negative")
    if args.cp_weight == 0.0 and args.outcome_weight == 0.0:
        raise ValueError("at least one loss weight must be positive")

    input_pins = [_file_pin(path) for path in args.input]
    _feature_self_test()
    _collision_self_test()
    golden_cases = _golden_runtime_cases()
    golden_python = _golden_python_self_test(golden_cases)
    temporary_output: Path | None = None
    if args.self_test:
        if args.input:
            dataset = load_dataset(
                args.input,
                seed=args.seed,
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
            )
        else:
            dataset = make_dataset_from_records(
                make_synthetic_records(),
                seed=args.seed,
                collision_policy=args.collision_policy,
            )
        epochs = args.epochs if args.epochs is not None else 100
        qat_epochs = (
            args.qat_epochs if args.qat_epochs is not None else min(20, epochs)
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
            args.input,
            seed=args.seed,
            train_percent=args.train_percent,
            validation_percent=args.validation_percent,
            explicit_group_field=args.group_field,
            max_records=args.max_records,
            strict=args.strict,
            collision_policy=args.collision_policy,
        )
        epochs = args.epochs if args.epochs is not None else 12
        qat_epochs = (
            args.qat_epochs if args.qat_epochs is not None else min(3, epochs)
        )
        output = args.output

    train_indices = dataset.indices(0)
    initial = FloatNetwork.initialize(args.seed).quantize()
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
    model, history = train(
        dataset,
        seed=args.seed,
        epochs=epochs,
        qat_epochs=qat_epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        cp_weight=args.cp_weight,
        outcome_weight=args.outcome_weight,
        quiet=args.quiet,
    )
    network = model.quantize()
    all_indices = np.arange(dataset.count, dtype=np.int64)
    round_trip = run_round_trip(
        output, network, dataset, all_indices, args.batch_size
    )
    cross_runtime = _run_cpp_cross_runtime(
        args.cpp_evaluator, output, network, golden_cases
    )
    final_metrics = {
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
        "test": evaluate(
            dataset,
            dataset.indices(2),
            network,
            args.batch_size,
            args.cp_weight,
            args.outcome_weight,
        ),
    }

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

    current_input_pins = [_file_pin(path) for path in args.input]
    if current_input_pins != input_pins:
        raise ValueError("an input file changed while training was in progress")
    manifest = {
        "schemaVersion": 2,
        "formatVersion": FORMAT_VERSION,
        "architecture": {
            "name": "PS104-shared-1668x128-256x32x1",
            "headerBytes": HEADER_BYTES,
            "payloadBytes": PAYLOAD_BYTES,
            "features": FEATURE_COUNT,
            "accumulator": ACCUMULATOR_SIZE,
            "hidden": HIDDEN_SIZE,
            "activationMax": ACTIVATION_MAX,
            "hiddenDivisor": HIDDEN_DIVISOR,
            "outputDivisor": OUTPUT_DIVISOR,
        },
        "inputs": input_pins,
        "environment": _runtime_manifest(),
        "seed": args.seed,
        "groups": len(set(dataset.groups)),
        "records": {
            "read": dataset.records_read,
            "accepted": dataset.count,
            "skipped": dataset.records_skipped,
            "train": int(dataset.indices(0).size),
            "validation": int(dataset.indices(1).size),
            "test": int(dataset.indices(2).size),
        },
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
            "cpWeight": args.cp_weight,
            "outcomeWeight": args.outcome_weight,
            "cpNormalizer": CP_NORMALIZER,
            "cpHuberDeltaNormalized": CP_HUBER_DELTA,
            "outcomeLogisticScale": OUTCOME_LOGISTIC_SCALE,
            "groupField": args.group_field,
            "collisionPolicy": args.collision_policy,
            "trainPercent": 100.0 if args.self_test else args.train_percent,
            "validationPercent": (
                0.0 if args.self_test else args.validation_percent
            ),
        },
        "initialQuantizedTrain": initial_metrics,
        "finalQuantized": final_metrics,
        "roundTrip": round_trip,
        "goldenPython": golden_python,
        "crossRuntime": cross_runtime,
        "history": history,
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
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, AssertionError, FloatingPointError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
