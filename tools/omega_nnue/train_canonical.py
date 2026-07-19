#!/usr/bin/env python3
"""Run the sealed trainer and publish a deployment-equivalent float checkpoint.

The optimizer shadow and its manifest remain available as explicit audit
artifacts.  The planned float checkpoint is a lossless float view of the
exported quantized NNUE and must requantize to identical bytes.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence
import json
import math
import os
import sys
import tempfile

import numpy as np

import train as sealed


KIND = "omega-nnue-canonical-deployment-float"


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is invalid JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object: {path}")
    return value


def _option(argv: Sequence[str], name: str, *, required: bool = True) -> Path | None:
    positions = [index for index, value in enumerate(argv) if value == name]
    if not positions:
        if required:
            raise ValueError(f"missing required option {name}")
        return None
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise ValueError(f"{name} must occur exactly once with a value")
    return Path(argv[positions[0] + 1]).resolve()


def _replace_option(argv: list[str], name: str, value: Path) -> None:
    index = argv.index(name)
    argv[index + 1] = str(value)


def _derived(path: Path, marker: str) -> Path:
    return path.with_name(f"{path.stem}.{marker}{path.suffix}")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _reject_artifact_aliases(paths: Mapping[str, Path]) -> None:
    """Reject duplicate paths after symlink, case, and relative normalization."""

    seen: dict[str, str] = {}
    for label, path in paths.items():
        resolved = path.expanduser().resolve()
        key = os.path.normcase(str(resolved))
        previous = seen.setdefault(key, label)
        if previous != label:
            raise ValueError(
                f"artifact path alias: {previous} and {label} both resolve "
                f"to {resolved}"
            )


def _assert_raw_argv(
    manifest: Mapping[str, Any],
    expected: Sequence[str],
    *,
    label: str,
) -> None:
    wanted = list(expected)
    if manifest.get("rawArgv") != wanted:
        raise ValueError(f"{label} top-level rawArgv does not match invocation")
    experiment = _mapping(manifest.get("experiment"), f"{label} experiment")
    if experiment.get("rawArgv") != wanted:
        raise ValueError(f"{label} experiment.rawArgv does not match invocation")


def _same_parameters(
    left: sealed.FloatNetwork, right: sealed.FloatNetwork
) -> bool:
    return all(
        np.array_equal(value, right.parameters()[name], equal_nan=False)
        for name, value in left.parameters().items()
    )


def _read_lossless_float_checkpoint(
    path: Path, *, label: str
) -> tuple[sealed.FloatNetwork, dict[str, Any]]:
    """Authenticate, structurally decode, and exactly round-trip a checkpoint."""

    pin = sealed._file_pin(path)
    raw = path.read_bytes()
    model = sealed.FloatNetwork.read_checkpoint(path)
    if model.checkpoint_bytes() != raw:
        raise ValueError(f"{label} is not byte-lossless after structural decoding")
    with tempfile.TemporaryDirectory(
        prefix="omega-float-checkpoint-round-trip-"
    ) as directory:
        round_trip_path = Path(directory) / "checkpoint.float"
        model.write_checkpoint(round_trip_path)
        reloaded = sealed.FloatNetwork.read_checkpoint(round_trip_path)
        if not _same_parameters(model, reloaded):
            raise ValueError(f"{label} parameters changed after write/read")
        if round_trip_path.read_bytes() != raw:
            raise ValueError(f"{label} bytes changed after write/read")
    if sealed._file_pin(path) != pin:
        raise ValueError(f"{label} changed during structural validation")
    return model, pin


def _legacy_quantization_penalty(value: Any) -> dict[str, Any]:
    """Validate the exact sealed-trainer penalty schema without broadening it."""

    penalty = _mapping(value, "quantizationPenalty")
    expected_splits = {"train", "validation"}
    if set(penalty) != expected_splits:
        raise ValueError(
            "quantizationPenalty must contain exactly train and validation"
        )
    expected_metrics = {"samples", "maeCp", "maxCp"}
    for split in ("train", "validation"):
        metrics = _mapping(
            penalty[split], f"quantizationPenalty.{split}"
        )
        if set(metrics) != expected_metrics:
            raise ValueError(
                f"quantizationPenalty.{split} must contain exactly "
                "samples, maeCp, and maxCp"
            )
        samples = metrics["samples"]
        if (
            isinstance(samples, bool)
            or not isinstance(samples, int)
            or samples <= 0
        ):
            raise ValueError(
                f"quantizationPenalty.{split}.samples must be a positive integer"
            )
        for metric in ("maeCp", "maxCp"):
            value = metrics[metric]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"quantizationPenalty.{split}.{metric} must be numeric"
                )
            if not math.isfinite(float(value)) or value < 0:
                raise ValueError(
                    f"quantizationPenalty.{split}.{metric} must be finite "
                    "and nonnegative"
                )
    return deepcopy(penalty)


def _prediction_batch(
    *,
    ofens: Sequence[str],
    network: sealed.QuantizedNetwork,
    canonical: sealed.FloatNetwork,
) -> tuple[np.ndarray, np.ndarray]:
    white_rows = [
        sealed.active_features(ofen, 0, network.architecture) for ofen in ofens
    ]
    black_rows = [
        sealed.active_features(ofen, 1, network.architecture) for ofen in ofens
    ]
    width = max(
        max((len(row) for row in white_rows), default=0),
        max((len(row) for row in black_rows), default=0),
    )
    if width <= 0:
        raise ValueError("deployment-float corpus position has no active features")
    pad = sealed.feature_count_for_architecture(network.architecture)
    white = np.full((len(ofens), width), pad, dtype=np.uint16)
    black = np.full_like(white, pad)
    for index, (white_row, black_row) in enumerate(zip(white_rows, black_rows)):
        white[index, : len(white_row)] = white_row
        black[index, : len(black_row)] = black_row
    stm_white = np.asarray(
        [ofen.split()[1] == "w" for ofen in ofens], dtype=np.bool_
    )[:, None]
    stm = np.where(stm_white, white, black)
    opponent = np.where(stm_white, black, white)
    floating, _ = canonical.forward(
        stm, opponent, quantization_aware=False, need_cache=False
    )
    quantized = network.predict_features(stm, opponent)
    return floating.astype(np.float64), quantized.astype(np.float64)


def _deployment_float_penalty(
    *,
    raw_manifest: Mapping[str, Any],
    network: sealed.QuantizedNetwork,
    canonical: sealed.FloatNetwork,
    batch_size: int = 256,
) -> dict[str, Any]:
    """Compare deployment float and integer runtime using only corpus OFENs."""

    inputs = raw_manifest.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError(
            "deployment-equivalent float penalty requires pinned trainer inputs"
        )
    input_pins: list[dict[str, Any]] = []
    input_paths: list[Path] = []
    for index, value in enumerate(inputs):
        pin = _mapping(value, f"trainer input {index}")
        path = Path(str(pin.get("path", ""))).resolve(strict=True)
        actual = sealed._file_pin(path)
        if pin != actual:
            raise ValueError(f"trainer input {index} changed before canonicalization")
        input_pins.append(dict(pin))
        input_paths.append(path)

    count = 0
    absolute_sum = 0.0
    maximum = 0.0
    exact = 0
    floating_min: float | None = None
    floating_max: float | None = None
    batch: list[str] = []

    def consume() -> None:
        nonlocal count, absolute_sum, maximum, exact
        nonlocal floating_min, floating_max
        if not batch:
            return
        floating, quantized = _prediction_batch(
            ofens=batch, network=network, canonical=canonical
        )
        errors = np.abs(floating - quantized)
        count += int(errors.size)
        absolute_sum += float(errors.sum())
        maximum = max(maximum, float(errors.max()))
        exact += int(np.count_nonzero(errors == 0.0))
        current_min = float(floating.min())
        current_max = float(floating.max())
        floating_min = (
            current_min if floating_min is None else min(floating_min, current_min)
        )
        floating_max = (
            current_max if floating_max is None else max(floating_max, current_max)
        )
        batch.clear()

    for ofen, _location in sealed._iter_selective_string(input_paths, "ofen"):
        batch.append(" ".join(ofen.split()))
        if len(batch) >= batch_size:
            consume()
    consume()
    if count == 0:
        raise ValueError("deployment-float corpus contains no OFEN rows")
    if [sealed._file_pin(path) for path in input_paths] != input_pins:
        raise ValueError("trainer input changed during deployment-float evaluation")
    return {
        "schemaVersion": 1,
        "semantics": (
            "absolute difference between the deployment-equivalent float "
            "checkpoint and quantized integer runtime"
        ),
        "scope": "all pinned corpus OFENs",
        "samples": count,
        "maeCp": absolute_sum / count,
        "maxCp": maximum,
        "exactPredictionFraction": exact / count,
        "floatPredictionMinCp": floating_min,
        "floatPredictionMaxCp": floating_max,
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
        "inputs": input_pins,
    }


def _canonicalize(
    *,
    original_argv: list[str],
    internal_argv: list[str],
    network_path: Path,
    canonical_float_path: Path,
    planned_manifest_path: Path,
    shadow_float_path: Path,
    shadow_manifest_path: Path,
    wrapper_pin: dict[str, Any],
    trainer_pin: dict[str, Any],
) -> None:
    if sealed._file_pin(Path(__file__)) != wrapper_pin:
        raise ValueError("canonicalizing wrapper identity does not match its pin")
    if sealed._file_pin(Path(sealed.__file__)) != trainer_pin:
        raise ValueError("sealed underlying trainer identity does not match its pin")

    shadow_manifest_pin = sealed._file_pin(shadow_manifest_path)
    raw_manifest = _load_json(shadow_manifest_path, "optimizer-shadow manifest")
    _assert_raw_argv(
        raw_manifest, internal_argv, label="optimizer-shadow manifest"
    )
    shadow, shadow_pin = _read_lossless_float_checkpoint(
        shadow_float_path, label="optimizer-shadow checkpoint"
    )
    if raw_manifest.get("floatCheckpoint") != shadow_pin:
        raise ValueError("optimizer-shadow manifest does not pin its checkpoint")
    if "quantizationPenalty" not in raw_manifest:
        raise ValueError("optimizer-shadow manifest lacks quantizationPenalty")
    original_penalty = _legacy_quantization_penalty(
        raw_manifest["quantizationPenalty"]
    )

    network_pin = sealed._file_pin(network_path)
    round_trip = _mapping(
        raw_manifest.get("roundTrip"), "optimizer-shadow roundTrip"
    )
    if str(round_trip.get("sha256", "")).lower() != network_pin["sha256"]:
        raise ValueError("optimizer-shadow manifest does not pin its network")
    network = sealed.QuantizedNetwork.read(network_path)
    expected_features = sealed.feature_count_for_architecture(
        network.architecture
    )
    if shadow.ft_weights.shape[0] != expected_features:
        raise ValueError(
            "optimizer-shadow checkpoint feature count does not match its network"
        )
    canonical = sealed.FloatNetwork.from_quantized(network)
    if canonical.quantize(network.architecture).to_bytes() != network.to_bytes():
        raise AssertionError("canonical float view does not requantize identically")
    deployment_penalty = _deployment_float_penalty(
        raw_manifest=raw_manifest,
        network=network,
        canonical=canonical,
    )
    canonical.write_checkpoint(canonical_float_path)
    reloaded = sealed.FloatNetwork.read_checkpoint(canonical_float_path)
    if not _same_parameters(canonical, reloaded):
        raise AssertionError("canonical float checkpoint changed on round trip")
    if reloaded.quantize(network.architecture).to_bytes() != network.to_bytes():
        raise AssertionError("reloaded canonical float does not match the NNUE")
    if sealed._file_pin(Path(__file__)) != wrapper_pin:
        raise ValueError("canonicalizing wrapper changed during canonicalization")
    if sealed._file_pin(Path(sealed.__file__)) != trainer_pin:
        raise ValueError("sealed trainer changed during canonicalization")
    if sealed._file_pin(network_path) != network_pin:
        raise ValueError("quantized network changed during canonicalization")
    if sealed._file_pin(shadow_float_path) != shadow_pin:
        raise ValueError("optimizer shadow changed during canonicalization")
    if sealed._file_pin(shadow_manifest_path) != shadow_manifest_pin:
        raise ValueError("optimizer-shadow manifest changed during canonicalization")

    manifest = deepcopy(raw_manifest)
    manifest["rawArgv"] = list(original_argv)
    experiment = _mapping(manifest.get("experiment"), "final experiment")
    experiment["rawArgv"] = list(original_argv)
    manifest["experiment"] = experiment
    manifest["canonicalWrapper"] = wrapper_pin
    manifest["sealedUnderlyingTrainer"] = trainer_pin
    manifest["optimizerShadowCheckpoint"] = shadow_pin
    manifest["optimizerShadowManifest"] = shadow_manifest_pin
    # Preserve the trainer's legacy quantizationPenalty object byte-for-value.
    # It measures the optimizer shadow against integer runtime.  The explicit
    # alias and labels remove the previous ambiguity without breaking readers.
    manifest["quantizationPenalty"] = original_penalty
    manifest["optimizerShadowQuantizationPenalty"] = deepcopy(original_penalty)
    manifest["deploymentFloatQuantizationPenalty"] = deployment_penalty
    manifest["quantizationPenaltyLabels"] = {
        "quantizationPenalty": (
            "legacy schema: optimizer-shadow float versus quantized runtime"
        ),
        "optimizerShadowQuantizationPenalty": (
            "explicit alias of legacy optimizer-shadow penalty"
        ),
        "deploymentFloatQuantizationPenalty": (
            "deployment-equivalent float view versus quantized runtime; "
            "feature-only over all pinned corpus OFENs"
        ),
    }
    manifest["floatCheckpoint"] = sealed._file_pin(canonical_float_path)
    manifest["deploymentFloatCanonicalization"] = {
        "schemaVersion": 1,
        "kind": KIND,
        "wrapper": wrapper_pin,
        "sealedUnderlyingTrainer": trainer_pin,
        "sourceNetwork": network_pin,
        "sourceOptimizerShadow": shadow_pin,
        "canonicalFloat": sealed._file_pin(canonical_float_path),
        "outwardFrozenArgv": list(original_argv),
        "internalSubstitutedArgv": list(internal_argv),
        "requantizedByteIdentical": True,
        "parameterRoundTripExact": True,
        "heldOutTargetsDecoded": False,
    }
    _assert_raw_argv(manifest, original_argv, label="final canonical manifest")
    sealed._atomic_json(planned_manifest_path, manifest)


def _publish_migrate_provenance(
    *,
    manifest_path: Path,
    network_path: Path,
    outward_argv: list[str],
    wrapper_pin: dict[str, Any],
    trainer_pin: dict[str, Any],
) -> None:
    if sealed._file_pin(Path(__file__)) != wrapper_pin:
        raise ValueError("canonicalizing wrapper changed during migration")
    if sealed._file_pin(Path(sealed.__file__)) != trainer_pin:
        raise ValueError("sealed underlying trainer changed during migration")
    raw_manifest = _load_json(manifest_path, "migrate-only manifest")
    _assert_raw_argv(raw_manifest, outward_argv, label="migrate-only manifest")
    network_pin = sealed._file_pin(network_path)
    round_trip = _mapping(raw_manifest.get("roundTrip"), "migrate-only roundTrip")
    if str(round_trip.get("sha256", "")).lower() != network_pin["sha256"]:
        raise ValueError("migrate-only manifest does not pin its network")
    manifest = deepcopy(raw_manifest)
    manifest["canonicalWrapper"] = wrapper_pin
    manifest["sealedUnderlyingTrainer"] = trainer_pin
    manifest["canonicalTrainerProvenance"] = {
        "schemaVersion": 1,
        "mode": "migrate-only-delegation",
        "wrapper": wrapper_pin,
        "sealedUnderlyingTrainer": trainer_pin,
        "sourceNetwork": network_pin,
        "outwardFrozenArgv": list(outward_argv),
        "internalSubstitutedArgv": list(outward_argv),
    }
    _assert_raw_argv(manifest, outward_argv, label="final migrate-only manifest")
    sealed._atomic_json(manifest_path, manifest)


def _self_test() -> None:
    with tempfile.TemporaryDirectory(
        prefix="omega-train-canonical-self-test-"
    ) as directory:
        root = Path(directory)
        network_path = root / "candidate.nnue"
        float_path = root / "candidate.float"
        shadow_path = root / "candidate.optimizer-shadow.float"
        manifest_path = root / "candidate.manifest.json"
        shadow_manifest_path = root / "candidate.optimizer-shadow.manifest.json"
        network = sealed._blank_quantized_network(
            architecture=sealed.ARCHITECTURE_KING_STATE_RESIDUAL,
            output_bias=64,
        )
        network.write(network_path)
        shadow = sealed.FloatNetwork.initialize(
            77123, sealed.KING_STATE_FEATURE_COUNT
        )
        shadow.write_checkpoint(shadow_path)
        corpus_path = root / "target-opaque.jsonl"
        corpus_payload = (
            json.dumps(
                {
                    "groupId": "held-out-poison",
                    "ofen": sealed.INITIAL_OFEN,
                    "targetCpStm": "DO-NOT-DECODE",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        sealed._atomic_bytes(corpus_path, corpus_payload)
        outward_argv = [
            "--manifest",
            str(manifest_path),
            "--float-checkpoint",
            str(float_path),
        ]
        internal_argv = [
            "--manifest",
            str(shadow_manifest_path),
            "--float-checkpoint",
            str(shadow_path),
        ]
        original_penalty = {
            "train": {"samples": 128, "maeCp": 0.75, "maxCp": 3.5},
            "validation": {"samples": 32, "maeCp": 0.875, "maxCp": 4.0},
        }
        raw_manifest: dict[str, Any] = {
            "rawArgv": list(internal_argv),
            "experiment": {"rawArgv": list(internal_argv)},
            "inputs": [sealed._file_pin(corpus_path)],
            "floatCheckpoint": sealed._file_pin(shadow_path),
            "roundTrip": {"sha256": sealed._file_pin(network_path)["sha256"]},
            "quantizationPenalty": deepcopy(original_penalty),
        }
        sealed._atomic_json(shadow_manifest_path, raw_manifest)
        wrapper_pin = sealed._file_pin(Path(__file__))
        trainer_pin = sealed._file_pin(Path(sealed.__file__))
        _canonicalize(
            original_argv=outward_argv,
            internal_argv=internal_argv,
            network_path=network_path,
            canonical_float_path=float_path,
            planned_manifest_path=manifest_path,
            shadow_float_path=shadow_path,
            shadow_manifest_path=shadow_manifest_path,
            wrapper_pin=wrapper_pin,
            trainer_pin=trainer_pin,
        )
        result = _load_json(manifest_path, "canonical self-test manifest")
        if (
            result["rawArgv"] != outward_argv
            or result["experiment"]["rawArgv"] != outward_argv
            or not result["deploymentFloatCanonicalization"][
                "requantizedByteIdentical"
            ]
            or result["canonicalWrapper"] != wrapper_pin
            or result["sealedUnderlyingTrainer"] != trainer_pin
            or result["quantizationPenalty"] != original_penalty
            or result["optimizerShadowQuantizationPenalty"] != original_penalty
            or result["deploymentFloatQuantizationPenalty"]["samples"] != 1
            or result["deploymentFloatQuantizationPenalty"][
                "targetFieldsDecoded"
            ]
            != 0
        ):
            raise AssertionError("canonical manifest rewrite failed")
        if (
            result["deploymentFloatQuantizationPenalty"]["maeCp"] != 0.0
            or result["deploymentFloatQuantizationPenalty"]["maxCp"] != 0.0
        ):
            raise AssertionError("deployment-equivalent float penalty is not exact")

        bad_experiment = deepcopy(raw_manifest)
        bad_experiment["experiment"]["rawArgv"] = ["--wrong"]
        try:
            _assert_raw_argv(
                bad_experiment, internal_argv, label="bad experiment fixture"
            )
        except ValueError as error:
            if "experiment.rawArgv" not in str(error):
                raise
        else:
            raise AssertionError("mismatched experiment.rawArgv was accepted")

        rejected_float = root / "must-not-exist.float"
        rejected_manifest = root / "must-not-exist.manifest.json"
        try:
            _canonicalize(
                original_argv=outward_argv,
                internal_argv=["--wrong"],
                network_path=network_path,
                canonical_float_path=rejected_float,
                planned_manifest_path=rejected_manifest,
                shadow_float_path=shadow_path,
                shadow_manifest_path=shadow_manifest_path,
                wrapper_pin=wrapper_pin,
                trainer_pin=trainer_pin,
            )
        except ValueError as error:
            if "rawArgv" not in str(error):
                raise
        else:
            raise AssertionError("mismatched internal argv was accepted")
        if rejected_float.exists() or rejected_manifest.exists():
            raise AssertionError("argv rejection wrote canonical artifacts")

        corrupt_shadow_path = root / "corrupt.optimizer-shadow.float"
        corrupt_shadow_bytes = bytearray(shadow_path.read_bytes())
        corrupt_shadow_bytes[-1] ^= 0x01
        sealed._atomic_bytes(corrupt_shadow_path, bytes(corrupt_shadow_bytes))
        corrupt_manifest_path = root / "corrupt.optimizer-shadow.manifest.json"
        corrupt_internal_argv = [
            "--manifest",
            str(corrupt_manifest_path),
            "--float-checkpoint",
            str(corrupt_shadow_path),
        ]
        corrupt_raw_manifest = deepcopy(raw_manifest)
        corrupt_raw_manifest["rawArgv"] = list(corrupt_internal_argv)
        corrupt_raw_manifest["experiment"]["rawArgv"] = list(
            corrupt_internal_argv
        )
        # The manifest intentionally authenticates the corrupt bytes.  A pin
        # match must not substitute for structural checkpoint validation.
        corrupt_raw_manifest["floatCheckpoint"] = sealed._file_pin(
            corrupt_shadow_path
        )
        sealed._atomic_json(corrupt_manifest_path, corrupt_raw_manifest)
        corrupt_output = root / "corrupt-result.float"
        corrupt_output_manifest = root / "corrupt-result.manifest.json"
        try:
            _canonicalize(
                original_argv=outward_argv,
                internal_argv=corrupt_internal_argv,
                network_path=network_path,
                canonical_float_path=corrupt_output,
                planned_manifest_path=corrupt_output_manifest,
                shadow_float_path=corrupt_shadow_path,
                shadow_manifest_path=corrupt_manifest_path,
                wrapper_pin=wrapper_pin,
                trainer_pin=trainer_pin,
            )
        except ValueError as error:
            if "float checkpoint payload SHA-256 mismatch" not in str(error):
                raise
        else:
            raise AssertionError("corrupt matching-pinned shadow was accepted")
        if corrupt_output.exists() or corrupt_output_manifest.exists():
            raise AssertionError("corrupt shadow rejection wrote artifacts")

        malformed_penalties: dict[str, Any] = {
            "test split": {
                **deepcopy(original_penalty),
                "test": {"samples": 1, "maeCp": 0.0, "maxCp": 0.0},
            },
            "missing metric": {
                "train": {"samples": 1, "maeCp": 0.0},
                "validation": deepcopy(original_penalty["validation"]),
            },
            "extra metric": {
                "train": {
                    **deepcopy(original_penalty["train"]),
                    "medianCp": 0.0,
                },
                "validation": deepcopy(original_penalty["validation"]),
            },
            "zero samples": {
                "train": {"samples": 0, "maeCp": 0.0, "maxCp": 0.0},
                "validation": deepcopy(original_penalty["validation"]),
            },
            "boolean samples": {
                "train": {"samples": True, "maeCp": 0.0, "maxCp": 0.0},
                "validation": deepcopy(original_penalty["validation"]),
            },
            "negative metric": {
                "train": {"samples": 1, "maeCp": -0.1, "maxCp": 0.0},
                "validation": deepcopy(original_penalty["validation"]),
            },
            "nonfinite metric": {
                "train": {
                    "samples": 1,
                    "maeCp": float("nan"),
                    "maxCp": 0.0,
                },
                "validation": deepcopy(original_penalty["validation"]),
            },
            "boolean metric": {
                "train": {"samples": 1, "maeCp": False, "maxCp": 0.0},
                "validation": deepcopy(original_penalty["validation"]),
            },
        }
        for label, malformed in malformed_penalties.items():
            try:
                _legacy_quantization_penalty(malformed)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"malformed quantization penalty was accepted: {label}"
                )

        try:
            _reject_artifact_aliases(
                {
                    "network": network_path,
                    "manifest": network_path.parent
                    / "subdirectory"
                    / ".."
                    / network_path.name,
                }
            )
        except ValueError as error:
            if "artifact path alias" not in str(error):
                raise
        else:
            raise AssertionError("resolved artifact aliases were accepted")

        migrate_manifest_path = root / "migrate.manifest.json"
        migrate_argv = [
            "--migrate-only",
            "--output",
            str(network_path),
            "--manifest",
            str(migrate_manifest_path),
        ]
        sealed._atomic_json(
            migrate_manifest_path,
            {
                "rawArgv": list(migrate_argv),
                "experiment": {"rawArgv": list(migrate_argv)},
                "roundTrip": {
                    "sha256": sealed._file_pin(network_path)["sha256"]
                },
            },
        )
        _publish_migrate_provenance(
            manifest_path=migrate_manifest_path,
            network_path=network_path,
            outward_argv=migrate_argv,
            wrapper_pin=wrapper_pin,
            trainer_pin=trainer_pin,
        )
        migrate_result = _load_json(
            migrate_manifest_path, "migrate provenance fixture"
        )
        if (
            migrate_result["canonicalWrapper"] != wrapper_pin
            or migrate_result["sealedUnderlyingTrainer"] != trainer_pin
            or migrate_result["canonicalTrainerProvenance"]["mode"]
            != "migrate-only-delegation"
        ):
            raise AssertionError("migrate-only provenance was not pinned")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--self-test"]:
        _self_test()
        print("train_canonical self-test passed", flush=True)
        return 0
    if "--migrate-only" in arguments:
        network_path = _option(arguments, "--output")
        manifest_path = _option(arguments, "--manifest")
        assert network_path is not None
        assert manifest_path is not None
        migrate_artifacts = {
            "network": network_path,
            "manifest": manifest_path,
        }
        _reject_artifact_aliases(migrate_artifacts)
        existing = [
            str(path) for path in migrate_artifacts.values() if path.exists()
        ]
        if existing:
            raise ValueError(
                "refusing to overwrite migrate-only artifact: "
                + ", ".join(existing)
            )
        wrapper_pin = sealed._file_pin(Path(__file__))
        trainer_pin = sealed._file_pin(Path(sealed.__file__))
        result = sealed.main(arguments)
        if result != 0:
            return result
        _publish_migrate_provenance(
            manifest_path=manifest_path,
            network_path=network_path,
            outward_argv=arguments,
            wrapper_pin=wrapper_pin,
            trainer_pin=trainer_pin,
        )
        return 0

    network_path = _option(arguments, "--output")
    manifest_path = _option(arguments, "--manifest")
    canonical_float_path = _option(arguments, "--float-checkpoint")
    assert network_path is not None
    assert manifest_path is not None
    assert canonical_float_path is not None
    shadow_float_path = _derived(canonical_float_path, "optimizer-shadow")
    shadow_manifest_path = _derived(manifest_path, "optimizer-shadow")
    planned = {
        "network": network_path,
        "manifest": manifest_path,
        "canonicalFloat": canonical_float_path,
        "optimizerShadowFloat": shadow_float_path,
        "optimizerShadowManifest": shadow_manifest_path,
    }
    _reject_artifact_aliases(planned)
    existing = [str(path) for path in planned.values() if path.exists()]
    if existing:
        raise ValueError("refusing to overwrite generation-2 artifact: " + ", ".join(existing))

    wrapper_pin = sealed._file_pin(Path(__file__))
    trainer_pin = sealed._file_pin(Path(sealed.__file__))
    trainer_argv = list(arguments)
    _replace_option(trainer_argv, "--manifest", shadow_manifest_path)
    _replace_option(trainer_argv, "--float-checkpoint", shadow_float_path)
    result = sealed.main(trainer_argv)
    if result != 0:
        return result
    if sealed._file_pin(Path(__file__)) != wrapper_pin:
        raise ValueError("canonicalizing wrapper changed during training")
    if sealed._file_pin(Path(sealed.__file__)) != trainer_pin:
        raise ValueError("sealed trainer changed during training")
    _canonicalize(
        original_argv=arguments,
        internal_argv=trainer_argv,
        network_path=network_path,
        canonical_float_path=canonical_float_path,
        planned_manifest_path=manifest_path,
        shadow_float_path=shadow_float_path,
        shadow_manifest_path=shadow_manifest_path,
        wrapper_pin=wrapper_pin,
        trainer_pin=trainer_pin,
    )
    print(f"wrote canonical float: {canonical_float_path}", flush=True)
    print(f"wrote canonical manifest: {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
