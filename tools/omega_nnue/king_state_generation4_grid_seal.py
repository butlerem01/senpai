#!/usr/bin/env python3
"""Seal the target-blind activation calibration selected for generation 4.

This command consumes only the two completed generation-4 *development*
reports.  Those reports were produced from already-consumed generation-3
train/validation data.  It cannot read a corpus and it imports neither the G3
trainer nor the G4 trainer.  The frozen rule is reapplied from scratch on every
create or verify operation, then the result is committed atomically without
overwriting an existing seal.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
KIND = "omega-nnue-king-state-v4-activation-grid-selection"
REPO = Path(__file__).resolve().parents[2]
DEVELOPMENT_DIR = REPO / "build-msvc" / "king-state-v4-development"
GRID_PATH = DEVELOPMENT_DIR / "activation-grid.json"
CONFIRMATION_PATH = (
    DEVELOPMENT_DIR / "activation-sweep-p32-t0.62-temp16-s20260802.json"
)
PLAN_PATH = REPO / "build-msvc" / "king-state-v3" / "training-plan.json"
DEVELOPMENT_SOURCE = REPO / "tools" / "omega_nnue" / "king_state_generation4_dev.py"
DEFAULT_OUTPUT = DEVELOPMENT_DIR / "selection.seal.json"

PLAN_IDENTITY = {
    "path": "build-msvc/king-state-v3/training-plan.json",
    "bytes": 36868,
    "sha256": "6b6015c9c2b568c0dc62bb291fa21ca51ff71db319518c750a0c03f6f4a6f50d",
}
GRID_KIND = "omega-nnue-generation4-development-activation-grid"
SWEEP_KIND = "omega-nnue-generation4-development-activation-sweep"
PENALTIES = (8.0, 32.0, 128.0)
PRIMARY_SEED = 20260801
CONFIRMATION_SEED = 20260802
TARGET = 0.62
TEMPERATURE = 16.0
ACTIVE_RANGE = (0.2, 0.75)
SELECTED_PENALTY = 32.0
SELECTED_SETTINGS = {
    "penaltyWeight": SELECTED_PENALTY,
    "targetActiveFraction": TARGET,
    "temperature": TEMPERATURE,
    "source": "exact values supplied by the frozen development grid",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"non-finite JSON number {text!r}")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number {value!r} in {path}")

    with path.open("r", encoding="utf-8") as stream:
        value = json.load(
            stream,
            object_pairs_hook=reject_duplicate,
            parse_float=_finite_float,
            parse_constant=reject_constant,
        )
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    _require_finite_tree(value, str(path))
    return value


def _require_finite_tree(value: Any, label: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label}: non-finite value")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            _require_finite_tree(child, f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _require_finite_tree(child, f"{label}[{index}]")
        return
    raise ValueError(f"{label}: unsupported JSON value")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError as error:
        raise ValueError(f"input escapes the repository: {path}") from error


def _identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": _portable_path(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _normalize_identity(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label}: invalid identity field inventory")
    path = value.get("path")
    size = value.get("bytes")
    sha = value.get("sha256")
    if not isinstance(path, str):
        raise ValueError(f"{label}: invalid identity path")
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (REPO / candidate).resolve()
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError(f"{label}: invalid byte count")
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in sha)
    ):
        raise ValueError(f"{label}: invalid SHA-256")
    return {"path": _portable_path(resolved), "bytes": size, "sha256": sha.lower()}


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: expected a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label}: expected a finite number")
    return result


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}: expected an integer")
    return value


def _validation_metrics(value: Any, label: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}: expected an object")
    # Phase details are authenticated by the report identity and checked for
    # finiteness globally.  Only the two frozen ordering keys are consumed.
    return {
        "huberLoss": _number(value.get("huberLoss"), f"{label}.huberLoss"),
        "cpMae": _number(value.get("cpMae"), f"{label}.cpMae"),
    }


def _activation_summary(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}: expected an object")
    rows = _int(value.get("rows"), f"{label}.rows")
    if rows <= 0:
        raise ValueError(f"{label}: non-positive row count")
    return {
        "rows": rows,
        "denseActiveFraction": _number(
            value.get("denseActiveFraction"), f"{label}.denseActiveFraction"
        ),
        "deadDenseUnits": _int(value.get("deadDenseUnits"), f"{label}.deadDenseUnits"),
        "saturatedDenseUnits": _int(
            value.get("saturatedDenseUnits"), f"{label}.saturatedDenseUnits"
        ),
    }


def _eligible(whole: Mapping[str, Any]) -> bool:
    return (
        ACTIVE_RANGE[0]
        <= _number(whole.get("denseActiveFraction"), "whole active fraction")
        <= ACTIVE_RANGE[1]
        and _int(whole.get("deadDenseUnits"), "whole dead units") == 0
        and _int(whole.get("saturatedDenseUnits"), "whole saturated units") == 0
    )


def _sweep_summary(value: Any, *, expected_seed: int, expected_penalty: float) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("development sweep is not an object")
    if value.get("kind") != SWEEP_KIND or value.get("developmentOnly") is not True:
        raise ValueError("development sweep header changed")
    recipe = value.get("recipe")
    expected_recipe = {
        "base": "G3B",
        "penalty": expected_penalty,
        "seed": expected_seed,
        "target": TARGET,
        "temperature": TEMPERATURE,
    }
    if recipe != expected_recipe:
        raise ValueError(f"development sweep recipe changed: expected {expected_recipe}")
    boundary = value.get("informationBoundary")
    if boundary != {
        "heldOutTargetsDecoded": 0,
        "trainTargetsDecoded": 13088,
        "validationTargetsDecoded": 1630,
    }:
        raise ValueError("development sweep information boundary changed")
    activation = value.get("activation")
    if not isinstance(activation, Mapping) or set(activation) != {
        "train",
        "validation",
        "wholeFeatureOnly",
    }:
        raise ValueError("development sweep activation inventory changed")
    activation_summary = {
        name: _activation_summary(activation[name], f"activation.{name}")
        for name in ("train", "validation", "wholeFeatureOnly")
    }
    if activation_summary["train"]["rows"] != 13088:
        raise ValueError("development sweep train row count changed")
    if activation_summary["validation"]["rows"] != 1630:
        raise ValueError("development sweep validation row count changed")
    if activation_summary["wholeFeatureOnly"]["rows"] != 16384:
        raise ValueError("development sweep whole-feature row count changed")
    validation = _validation_metrics(value.get("validation"), "sweep validation")
    selected_epoch = _int(value.get("selectedEpoch"), "sweep selected epoch")
    if selected_epoch not in range(37, 49):
        raise ValueError("development sweep selected epoch is outside QAT")
    network_sha = value.get("networkSha256")
    if (
        not isinstance(network_sha, str)
        or len(network_sha) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in network_sha)
    ):
        raise ValueError("development sweep network hash is invalid")
    whole = activation_summary["wholeFeatureOnly"]
    return {
        "penaltyWeight": expected_penalty,
        "seed": expected_seed,
        "selectedEpoch": selected_epoch,
        "networkSha256": network_sha.lower(),
        "validation": validation,
        "wholeFeatureOnly": whole,
        "eligible": _eligible(whole),
    }


def _verify_grid_plan_identity(grid: Mapping[str, Any]) -> None:
    embedded = _normalize_identity(grid.get("plan"), "grid plan")
    if embedded != PLAN_IDENTITY:
        raise ValueError("development grid does not pin the frozen G3 plan")


def _read_inputs() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]
]:
    plan_identity = _identity(PLAN_PATH)
    if plan_identity != PLAN_IDENTITY:
        raise ValueError("generation-3 plan identity changed")
    plan = _load_json(PLAN_PATH)
    if (
        plan.get("kind") != "omega-nnue-king-state-v3-training-plan"
        or plan.get("profileId") != "king-state-v3-deep-hce-v4"
    ):
        raise ValueError("generation-3 plan header changed")
    plan_boundary = plan.get("informationBoundary")
    if not isinstance(plan_boundary, Mapping):
        raise ValueError("generation-3 plan has no information boundary")
    if (
        plan_boundary.get("heldOutTargetFieldsDecoded") != 0
        or plan_boundary.get("heldOutMetricsComputed") is not False
    ):
        raise ValueError("generation-3 plan crossed the held-out boundary")

    grid = _load_json(GRID_PATH)
    if grid.get("kind") != GRID_KIND or grid.get("developmentOnly") is not True:
        raise ValueError("development grid header changed")
    if grid.get("informationBoundary") != {
        "freshGeneration4TargetsDecoded": 0,
        "heldOutTargetsDecoded": 0,
    }:
        raise ValueError("development grid information boundary changed")
    if grid.get("common") != {
        "seed": PRIMARY_SEED,
        "target": TARGET,
        "temperature": TEMPERATURE,
    }:
        raise ValueError("development grid common settings changed")
    _verify_grid_plan_identity(grid)
    sweeps = grid.get("sweeps")
    if not isinstance(sweeps, list) or len(sweeps) != len(PENALTIES):
        raise ValueError("development grid must contain exactly three sweeps")
    summaries = [
        _sweep_summary(value, expected_seed=PRIMARY_SEED, expected_penalty=penalty)
        for value, penalty in zip(sweeps, PENALTIES)
    ]
    eligible = [summary for summary in summaries if summary["eligible"]]
    if not eligible:
        raise ValueError("development grid has no eligible activation recipe")
    winner = min(
        eligible,
        key=lambda row: (
            row["validation"]["huberLoss"],
            row["validation"]["cpMae"],
            row["penaltyWeight"],
        ),
    )
    if winner["penaltyWeight"] != SELECTED_PENALTY:
        raise ValueError("frozen grid rule did not select penalty 32")

    confirmation = _load_json(CONFIRMATION_PATH)
    confirmation_summary = _sweep_summary(
        confirmation,
        expected_seed=CONFIRMATION_SEED,
        expected_penalty=SELECTED_PENALTY,
    )
    if not confirmation_summary["eligible"]:
        raise ValueError("second-seed confirmation failed whole-feature health")
    if (
        confirmation_summary["validation"]["huberLoss"]
        > winner["validation"]["huberLoss"]
        or confirmation_summary["validation"]["cpMae"]
        > winner["validation"]["cpMae"]
    ):
        raise ValueError("second-seed confirmation regressed versus the selected primary sweep")
    return plan, grid, winner, summaries, confirmation_summary


def _build_seal(*, created_utc: str | None = None) -> dict[str, Any]:
    plan, _grid, winner, summaries, confirmation = _read_inputs()
    runtime = plan.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("generation-3 plan runtime metadata is missing")
    worker_environment = runtime.get("workerEnvironment")
    if worker_environment != {
        "BLIS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "PYTHONHASHSEED": "0",
        "VECLIB_MAXIMUM_THREADS": "1",
    }:
        raise ValueError("development runtime worker environment changed")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "createdUtc": created_utc or _utc_now(),
        "status": "sealed-before-generation-4-primary-training",
        "informationBoundary": {
            "generation3HeldOutTargetFieldsDecoded": 0,
            "generation4TargetFieldsDecoded": 0,
        },
        "inputs": {
            "primaryGridReport": _identity(GRID_PATH),
            "secondSeedConfirmationReport": _identity(CONFIRMATION_PATH),
            "developmentSource": _identity(DEVELOPMENT_SOURCE),
            "generation3Plan": _identity(PLAN_PATH),
        },
        "runtimeEnvironment": {
            "source": "authenticated generation-3 plan runtime metadata",
            "planCreatedUtc": plan.get("createdUtc"),
            "runtime": dict(runtime),
        },
        "selectionRule": {
            "eligibleIff": {
                "wholeFeatureOnlyDenseActiveFractionRangeInclusive": list(ACTIVE_RANGE),
                "wholeFeatureOnlyDeadDenseUnitsEquals": 0,
                "wholeFeatureOnlySaturatedDenseUnitsEquals": 0,
            },
            "ordering": [
                "ascending validation.huberLoss",
                "ascending validation.cpMae",
                "ascending penaltyWeight",
            ],
            "numericComparison": "exact finite report values; no tolerance or rounding",
            "requiredWinnerPenaltyWeight": SELECTED_PENALTY,
            "secondSeedRequirement": (
                "same recipe except seed; whole-feature health eligible; validation Huber "
                "loss and cpMae each no worse than the primary winner"
            ),
        },
        "primaryGridCandidates": summaries,
        "primaryWinner": winner,
        "secondSeedConfirmation": confirmation,
        "selectedActivationSettings": dict(SELECTED_SETTINGS),
        "immutability": {
            "mayBeChangedFromGeneration4Targets": False,
            "mayBeChangedFromGeneration4Validation": False,
            "mayBeChangedFromGeneration4HeldOut": False,
            "mayBeChangedFromMatchResults": False,
        },
    }


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("grid seal createdUtc is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("grid seal createdUtc is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("grid seal createdUtc must be timezone-aware")
    return value


def _atomic_no_clobber(path: Path, payload: bytes) -> None:
    output = path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    staging = Path(staging_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staging, output)
    finally:
        staging.unlink(missing_ok=True)


def _create(path: Path) -> dict[str, Any]:
    seal = _build_seal()
    _atomic_no_clobber(path, _canonical_json(seal))
    return seal


def _verify(path: Path) -> dict[str, Any]:
    seal_path = path.resolve()
    recorded = _load_json(seal_path)
    created = _validate_timestamp(recorded.get("createdUtc"))
    recomputed = _build_seal(created_utc=created)
    if recorded != recomputed:
        raise ValueError("activation-grid selection seal differs from recomputation")
    if seal_path.read_bytes() != _canonical_json(recorded):
        raise ValueError("activation-grid selection seal is not canonical JSON")
    return recorded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--seal", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        seal = _create(args.output)
        print(
            f"Published generation-4 activation-grid seal: {args.output.resolve()} "
            f"(penalty {seal['selectedActivationSettings']['penaltyWeight']})"
        )
        return 0
    if args.command == "verify":
        seal = _verify(args.seal)
        print(
            "Verified generation-4 activation-grid seal: "
            f"penalty {seal['selectedActivationSettings']['penaltyWeight']}"
        )
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
