#!/usr/bin/env python3
"""Development-only activation probes for the next Omega NNUE generation.

Generation 3 stopped before held-out access because every candidate exceeded
the preregistered dense-active ceiling.  This helper is deliberately confined
to the already-consumed generation-3 train/validation corpus.  It may be used
to calibrate a generation-4 activation regularizer, but it never reads split 2
targets and it is not a generation-4 trainer or selector.

The sweep monkey-patches only the dense back-propagation step in the frozen G3
trainer.  The additional differentiable hinge penalizes a unit only when its
batch-average soft active rate exceeds ``--target``.  Reported validation
metrics remain the ordinary frozen G3 metrics, making the accuracy/health
trade-off directly comparable with the failed G3 candidates.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator, Mapping

import numpy as np

import king_state_train_generation3 as g3
from omega_nnue import ACTIVATION_MAX, QuantizedNetwork


REPO = Path(__file__).resolve().parents[2]
DEFAULT_PLAN = REPO / "build-msvc" / "king-state-v3" / "training-plan.json"
DEFAULT_REPORT_DIR = REPO / "build-msvc" / "king-state-v4-development"


def _load_inputs(plan_path: Path) -> tuple[
    dict[str, Any], g3.LabeledCorpus, g3.FeatureCorpus, QuantizedNetwork
]:
    plan = g3._load_json(plan_path, "generation-3 training plan")
    identities = g3._mapping(plan.get("identities"), "plan identities")
    corpus = Path(str(identities["corpus"]["path"]))
    whole = g3._load_feature_corpus(corpus)
    dataset = g3._load_train_validation(corpus)
    # The development helper must not gain access to the held-out labels.
    if dataset.withheld_rows <= 0:
        raise ValueError("development corpus has no withheld split")
    initializer = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    return plan, dataset, whole, initializer


def _dense_statistics(
    model: Any,
    features: g3.FeatureCorpus,
    *,
    indices: np.ndarray | None = None,
    batch_size: int = 256,
) -> dict[str, Any]:
    wanted = (
        np.arange(features.count, dtype=np.int64)
        if indices is None
        else np.asarray(indices, dtype=np.int64)
    )
    active = 0
    total = 0
    values: list[np.ndarray] = []
    ever_positive = np.zeros(g3.HIDDEN_SIZE, dtype=bool)
    ever_below = np.zeros(g3.HIDDEN_SIZE, dtype=bool)
    for start in range(0, wanted.size, batch_size):
        selected = wanted[start : start + batch_size]
        stm, opponent = features.perspective(selected)
        _prediction, cache = g3._model_forward(
            model,
            stm,
            opponent,
            quantization_aware=True,
            need_cache=True,
        )
        assert cache is not None
        dense = np.asarray(cache["dense_z"], dtype=np.float64)
        mask = (dense > 0.0) & (dense < ACTIVATION_MAX)
        active += int(mask.sum())
        total += int(mask.size)
        ever_positive |= np.any(dense > 0.0, axis=0)
        ever_below |= np.any(dense < ACTIVATION_MAX, axis=0)
        values.append(dense.reshape(-1))
    flat = np.concatenate(values) if values else np.empty(0, dtype=np.float64)
    percentiles = {
        str(value): float(np.percentile(flat, value))
        for value in (1, 5, 10, 25, 50, 75, 90, 95, 99)
    }
    return {
        "rows": int(wanted.size),
        "denseActiveFraction": float(active / total),
        "deadDenseUnits": int((~ever_positive).sum()),
        "saturatedDenseUnits": int((~ever_below).sum()),
        "denseZ": {
            "minimum": float(flat.min()),
            "maximum": float(flat.max()),
            "mean": float(flat.mean()),
            "percentiles": percentiles,
        },
    }


@contextmanager
def _activation_regularizer(
    *, penalty: float, target: float, temperature: float
) -> Iterator[None]:
    if penalty < 0.0 or not math.isfinite(penalty):
        raise ValueError("penalty must be finite and nonnegative")
    if not 0.0 < target < 1.0:
        raise ValueError("target must be between zero and one")
    if temperature <= 0.0 or not math.isfinite(temperature):
        raise ValueError("temperature must be finite and positive")

    original = g3._dense_backprop

    def regularized(
        cache: Mapping[str, np.ndarray], output_gradient: np.ndarray
    ) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]:
        output_weights = cache["output_weights"]
        dense_weights = cache["dense_weights"]
        dense_a = cache["dense_a"]
        joined = cache["joined"]
        dense_z = cache["dense_z"]

        output_bias_gradient = np.asarray(
            [output_gradient.sum()], dtype=np.float32
        )
        output_weights_gradient = dense_a.T @ output_gradient
        dense_activation_gradient = (
            output_gradient[:, None] * output_weights[None, :]
        )

        # A smooth proxy is necessary because the deployment check itself is
        # the discontinuous predicate 0 < z < 127.  Penalizing only excess
        # per-unit occupancy preserves useful sparse units and supplies no
        # force toward the all-dead solution.
        scaled = np.clip(
            dense_z.astype(np.float64) / temperature, -40.0, 40.0
        )
        soft = 1.0 / (1.0 + np.exp(-scaled))
        rates = soft.mean(axis=0)
        excess = np.maximum(rates - target, 0.0)
        if penalty:
            regularizer_gradient = (
                (2.0 * penalty / g3.HIDDEN_SIZE)
                * excess[None, :]
                * soft
                * (1.0 - soft)
                / (temperature * dense_z.shape[0])
            )
            dense_activation_gradient = (
                dense_activation_gradient
                + regularizer_gradient.astype(np.float32)
            )

        dense_mask = (dense_z > 0.0) & (dense_z < ACTIVATION_MAX)
        dense_z_gradient = dense_activation_gradient * dense_mask
        dense_bias_gradient = dense_z_gradient.sum(axis=0)
        dense_weights_gradient = dense_z_gradient.T @ joined
        joined_gradient = dense_z_gradient @ dense_weights
        stm_gradient = joined_gradient[:, : g3.ACCUMULATOR_SIZE]
        opponent_gradient = joined_gradient[:, g3.ACCUMULATOR_SIZE :]
        stm_gradient *= (cache["stm_z"] > 0.0) & (
            cache["stm_z"] < ACTIVATION_MAX
        )
        opponent_gradient *= (cache["opponent_z"] > 0.0) & (
            cache["opponent_z"] < ACTIVATION_MAX
        )
        return {
            "dense_bias": dense_bias_gradient.astype(np.float32),
            "dense_weights": dense_weights_gradient.astype(np.float32),
            "output_bias": output_bias_gradient,
            "output_weights": output_weights_gradient.astype(np.float32),
        }, stm_gradient, opponent_gradient

    g3._dense_backprop = regularized
    try:
        yield
    finally:
        g3._dense_backprop = original


def _probe(args: argparse.Namespace) -> dict[str, Any]:
    plan, dataset, whole, initializer = _load_inputs(args.plan)
    models: dict[str, Any] = {
        "I0": g3.base.FloatNetwork.from_quantized(initializer)
    }
    for candidate in ("G3A", "G3B", "G3C"):
        network_path = (
            args.plan.parent / f"{candidate}.bundle" / f"{candidate}.nnue"
        )
        models[candidate] = g3.base.FloatNetwork.from_quantized(
            QuantizedNetwork.read(network_path)
        )
    train = dataset.indices(0)
    validation = dataset.indices(1)
    return {
        "kind": "omega-nnue-generation4-development-activation-probe",
        "informationBoundary": {
            "trainTargetsDecoded": 0,
            "validationTargetsDecoded": 0,
            "heldOutTargetsDecoded": 0,
            "featureOnly": True,
        },
        "plan": g3._identity(args.plan),
        "models": {
            name: {
                "train": _dense_statistics(model, whole, indices=train),
                "validation": _dense_statistics(
                    model, whole, indices=validation
                ),
                "wholeFeatureOnly": _dense_statistics(model, whole),
            }
            for name, model in models.items()
        },
    }


def _sweep(args: argparse.Namespace) -> dict[str, Any]:
    plan, dataset, whole, initializer = _load_inputs(args.plan)
    with _activation_regularizer(
        penalty=args.penalty,
        target=args.target,
        temperature=args.temperature,
    ):
        result = g3._train_candidate(
            candidate_id="G3B",
            dataset=dataset,
            whole_features=whole,
            initializer=initializer,
            seed=args.seed,
            quiet=args.quiet,
        )
    predictions = g3._predict_quantized(result.network, dataset.features)
    validation = g3._common_metrics(dataset, 1, predictions)
    canonical = g3.base.FloatNetwork.from_quantized(result.network)
    return {
        "kind": "omega-nnue-generation4-development-activation-sweep",
        "developmentOnly": True,
        "recipe": {
            "base": "G3B",
            "penalty": args.penalty,
            "target": args.target,
            "temperature": args.temperature,
            "seed": args.seed,
        },
        "informationBoundary": {
            "trainTargetsDecoded": int(dataset.indices(0).size),
            "validationTargetsDecoded": int(dataset.indices(1).size),
            "heldOutTargetsDecoded": 0,
        },
        "selectedEpoch": result.selected_epoch,
        "validation": g3._public_metrics(validation),
        "activation": {
            "train": _dense_statistics(
                canonical, whole, indices=dataset.indices(0)
            ),
            "validation": _dense_statistics(
                canonical, whole, indices=dataset.indices(1)
            ),
            "wholeFeatureOnly": _dense_statistics(canonical, whole),
        },
        "networkSha256": g3.hashlib.sha256(
            result.network.to_bytes()
        ).hexdigest(),
    }


def _grid(args: argparse.Namespace) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for penalty in args.penalties:
        sweep_args = argparse.Namespace(
            plan=args.plan,
            penalty=penalty,
            target=args.target,
            temperature=args.temperature,
            seed=args.seed,
            quiet=True,
        )
        result = _sweep(sweep_args)
        rows.append(result)
        validation = result["validation"]
        activation = result["activation"]["wholeFeatureOnly"]
        print(
            "generation-4 development sweep: "
            f"penalty={penalty:g} "
            f"huber={validation['huberLoss']:.8f} "
            f"mae={validation['cpMae']:.3f} "
            f"active={activation['denseActiveFraction']:.6f}",
            flush=True,
        )
    return {
        "kind": "omega-nnue-generation4-development-activation-grid",
        "developmentOnly": True,
        "plan": g3._identity(args.plan),
        "informationBoundary": {
            "heldOutTargetsDecoded": 0,
            "freshGeneration4TargetsDecoded": 0,
        },
        "common": {
            "target": args.target,
            "temperature": args.temperature,
            "seed": args.seed,
        },
        "sweeps": rows,
    }


def _write_report(path: Path, value: Mapping[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(dict(value), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    print(json.dumps(value, indent=2, sort_keys=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe")
    probe.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_DIR / "activation-probe.json",
    )

    sweep = subparsers.add_parser("sweep")
    sweep.add_argument("--penalty", type=float, required=True)
    sweep.add_argument("--target", type=float, default=0.60)
    sweep.add_argument("--temperature", type=float, default=16.0)
    sweep.add_argument("--seed", type=int, default=20260801)
    sweep.add_argument("--quiet", action="store_true")
    sweep.add_argument("--output", type=Path)

    grid = subparsers.add_parser("grid")
    grid.add_argument(
        "--penalties", type=float, nargs="+", default=[8.0, 32.0, 128.0]
    )
    grid.add_argument("--target", type=float, default=0.62)
    grid.add_argument("--temperature", type=float, default=16.0)
    grid.add_argument("--seed", type=int, default=20260801)
    grid.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_DIR / "activation-grid.json",
    )

    args = parser.parse_args()
    if args.command == "probe":
        result = _probe(args)
        output = args.output
    elif args.command == "sweep":
        result = _sweep(args)
        output = args.output or (
            DEFAULT_REPORT_DIR
            / (
                "activation-sweep-"
                f"p{args.penalty:g}-t{args.target:g}-"
                f"temp{args.temperature:g}-s{args.seed}.json"
            )
        )
    else:
        result = _grid(args)
        output = args.output
    _write_report(output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
