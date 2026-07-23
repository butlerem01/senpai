#!/usr/bin/env python3
"""Publish a target-blind, fail-closed record for Omega NNUE generation 3.

Generation 3 ended at validation because every candidate failed at least one
frozen eligibility gate.  The canonical selector correctly refused to make a
winner seal, but that leaves the absence of a winner implicit.  This tool
authenticates the frozen plan and its original candidate bundles, derives the
decision only from already-published aggregate validation fields, proves that
no later-stage artifact exists, and atomically publishes a no-winner closure.

The tool deliberately does not import the generation-3 trainer and never opens
the residual corpus.  In particular, it cannot decode split-2 target rows.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
KIND = "omega-nnue-king-state-v3-closure"
PROFILE_ID = "king-state-v3-deep-hce-v4"
OUTCOME = "closed-no-eligible-validation-candidate"
PINNED_COMMIT = "39a2519adda56a2f0bb0a8df77d9a4276f180c70"
CANDIDATES = ("G3A", "G3B", "G3C")
PHASES = ("opening", "middlegame", "late", "endgame")
TIE_PRIORITY = ("G3B", "G3C", "G3A")

REPO = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO / "build-msvc" / "king-state-v3"
DEFAULT_OUTPUT = REPO / "validation" / "omega-nnue-king-state-v3-closure.json"

PLAN_IDENTITY = {
    "path": "build-msvc/king-state-v3/training-plan.json",
    "bytes": 36868,
    "sha256": "6b6015c9c2b568c0dc62bb291fa21ca51ff71db319518c750a0c03f6f4a6f50d",
}
DECLARATIONS = {
    "preregistration": {
        "path": "validation/omega-nnue-king-state-v3-preregistration.json",
        "bytes": 33071,
        "sha256": "7ed714f11e370bb183815bd597a58cce5090396d1b6fe1b0a07849994b48c10c",
    },
    "amendment001": {
        "path": "validation/omega-nnue-king-state-v3-amendment-001.json",
        "bytes": 8729,
        "sha256": "846f43487558246edec871359f9f0a5dbd0377e69a3b95baee6ab6f47f32f78b",
    },
    "amendment002": {
        "path": "validation/omega-nnue-king-state-v3-amendment-002.json",
        "bytes": 7192,
        "sha256": "833886a638ebda8d062dfd195d22cfc482152730284d7da5f73a5066fb69c266",
    },
    "amendment003": {
        "path": "validation/omega-nnue-king-state-v3-amendment-003.json",
        "bytes": 68249,
        "sha256": "30d4eeb080d7a8d07d20b11abe8f3b2df19dc8c5e3c4ab354c523fcdce642469",
    },
}
PLAN_DECLARATION_KEYS = {
    "preregistration": "preregistration",
    "amendment001": "amendment",
    "amendment002": "amendment002",
    "amendment003": "amendment003",
}
PLAN_SOURCE_PATHS = {
    "baseTrainer": "tools/omega_nnue/train.py",
    "networkFormat": "tools/omega_nnue/omega_nnue.py",
    "orchestrator": "tools/omega_nnue/king_state_train_generation3.py",
    "phaseIncidencePreflight": "tools/omega_nnue/phase_incidence_preflight.py",
}
MANIFEST_IDENTITIES = {
    "G3A": {
        "path": "build-msvc/king-state-v3/G3A.bundle/G3A.manifest.json",
        "bytes": 86353,
        "sha256": "e7872e45f5fffdd6d370fe347b441169ebb63a2422b0ad571598969f80aac972",
    },
    "G3B": {
        "path": "build-msvc/king-state-v3/G3B.bundle/G3B.manifest.json",
        "bytes": 86458,
        "sha256": "1b9383a2389b01e834e6e1dcb1d28c6200d57fc29319bf03ccb428920bbd27ff",
    },
    "G3C": {
        "path": "build-msvc/king-state-v3/G3C.bundle/G3C.manifest.json",
        "bytes": 86501,
        "sha256": "e3d6d42f8dba466149482839140b2015f815e051bedec2f8ea89bbdb8c91bb2e",
    },
}
INITIALIZER_IDENTITIES = {
    "initializer": {
        "path": "build-msvc/king-state-v2/K2.nnue",
        "bytes": 12392940,
        "sha256": "74cfefdad87ce4e620597f1153bf03aa88b301cf66b2c959700b07e85acafe33",
    },
    "initializerManifest": {
        "path": "build-msvc/king-state-v2/K2.manifest.json",
        "bytes": 116217,
        "sha256": "48e0666d6a86bc253d435ee0b42908667dfb200d3cfc27bcbef9dbeced677b99",
    },
}
EXPECTED_MANIFEST_IDENTITY_FIELDS = {
    "canonicalDeploymentFloat",
    "initializer",
    "initializerManifest",
    "network",
    "optimizerShadow",
    "plan",
}
EXPECTED_HEALTH_CHECKS = {
    "meanFloatQuantizationPenalty",
    "singleFloatQuantizationPenalty",
    "saturatedDenseUnits",
    "deadDenseUnits",
    "deadDenseUnitsVsI0",
    "denseActiveFraction",
    "maximumAbsoluteCorrection",
    "finitePredictions",
    "expectedFileSize",
    "roundTripHash",
    "deploymentFloatParameterRoundTripExact",
    "deploymentFloatRequantizationByteIdentical",
    "wholeCorpusPythonCppIntegerAgreement",
}
HEALTH_THRESHOLDS = {
    "maximumMeanFloatQuantizationPenaltyCp": 2.0,
    "maximumSingleFloatQuantizationPenaltyCp": 10.0,
    "maximumSaturatedDenseUnits": 0,
    "maximumDeadDenseUnits": 8,
    "deadDenseUnitsMayExceedI0": False,
    "denseActiveFractionRangeInclusive": [0.2, 0.75],
    "maximumAbsoluteCorrectionCp": 2500,
}
ELIGIBILITY_THRESHOLDS = {
    "minimumRelativeHuberImprovementOverI0": 0.005,
    "maximumAnyPhaseCpMaeRegressionVersusI0": 5.0,
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
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_path(path: str | Path) -> Path:
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (REPO / candidate).resolve()
    try:
        resolved.relative_to(REPO)
    except ValueError as error:
        raise ValueError(f"identity escapes the repository: {path}") from error
    return resolved


def _portable_path(path: str | Path) -> str:
    return _repo_path(path).relative_to(REPO).as_posix()


def _actual_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": resolved.relative_to(REPO).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _normalize_identity(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}: expected an identity object")
    if set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label}: unexpected identity field inventory")
    path = value.get("path")
    size = value.get("bytes")
    sha = value.get("sha256")
    if not isinstance(path, str) or not path:
        raise ValueError(f"{label}: invalid identity path")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError(f"{label}: invalid identity byte count")
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in sha)
    ):
        raise ValueError(f"{label}: invalid SHA-256")
    return {
        "path": _portable_path(path),
        "bytes": size,
        "sha256": sha.lower(),
    }


def _verify_identity(
    value: Any,
    label: str,
    *,
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_identity(value, label)
    if expected is not None and normalized != dict(expected):
        raise ValueError(f"{label}: identity does not match the frozen pin")
    actual = _actual_identity(_repo_path(normalized["path"]))
    if normalized != actual:
        raise ValueError(f"{label}: artifact identity changed")
    return normalized


def _git_blob_identity(repo_path: str) -> dict[str, Any]:
    posix = PurePosixPath(repo_path).as_posix()
    try:
        commit = subprocess.run(
            ["git", "rev-parse", f"{PINNED_COMMIT}^{{commit}}"],
            cwd=REPO,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        payload = subprocess.run(
            ["git", "show", f"{PINNED_COMMIT}:{posix}"],
            cwd=REPO,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"cannot authenticate pinned git blob {posix}: {detail}") from error
    if commit != PINNED_COMMIT:
        raise ValueError("pinned generation-3 commit does not resolve exactly")
    return {
        "path": posix,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _verify_git_backed_identity(
    value: Any,
    label: str,
    *,
    expected_path: str,
) -> dict[str, Any]:
    normalized = _normalize_identity(value, label)
    if normalized["path"] != expected_path:
        raise ValueError(f"{label}: unexpected source path")
    blob = _git_blob_identity(expected_path)
    if normalized != blob:
        raise ValueError(f"{label}: plan identity is not the pinned commit blob")
    # The declaration files themselves remain immutable working-tree inputs.
    # Source files may evolve after G3, so their blob authentication is enough.
    return normalized


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


def _metric(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"cpMae", "huberLoss", "phase"}:
        raise ValueError(f"{label}: invalid metric field inventory")
    phase_value = value.get("phase")
    if not isinstance(phase_value, Mapping) or set(phase_value) != set(PHASES):
        raise ValueError(f"{label}: invalid phase inventory")
    result_phase: dict[str, Any] = {}
    for phase in PHASES:
        row = phase_value[phase]
        if not isinstance(row, Mapping) or set(row) != {
            "cpMae",
            "groups",
            "huberLoss",
            "rows",
        }:
            raise ValueError(f"{label}.{phase}: invalid metric field inventory")
        groups = _int(row["groups"], f"{label}.{phase}.groups")
        rows = _int(row["rows"], f"{label}.{phase}.rows")
        if groups <= 0 or rows <= 0:
            raise ValueError(f"{label}.{phase}: non-positive support")
        result_phase[phase] = {
            "cpMae": _number(row["cpMae"], f"{label}.{phase}.cpMae"),
            "groups": groups,
            "huberLoss": _number(row["huberLoss"], f"{label}.{phase}.huberLoss"),
            "rows": rows,
        }
    return {
        "cpMae": _number(value["cpMae"], f"{label}.cpMae"),
        "huberLoss": _number(value["huberLoss"], f"{label}.huberLoss"),
        "phase": result_phase,
    }


def _expected_health_checks(health: Mapping[str, Any], network_bytes: int) -> dict[str, bool]:
    lower, upper = HEALTH_THRESHOLDS["denseActiveFractionRangeInclusive"]
    return {
        "meanFloatQuantizationPenalty": _number(
            health.get("meanFloatQuantizationPenaltyCp"), "health mean quantization penalty"
        )
        <= HEALTH_THRESHOLDS["maximumMeanFloatQuantizationPenaltyCp"],
        "singleFloatQuantizationPenalty": _number(
            health.get("maximumFloatQuantizationPenaltyCp"), "health maximum quantization penalty"
        )
        <= HEALTH_THRESHOLDS["maximumSingleFloatQuantizationPenaltyCp"],
        "saturatedDenseUnits": _int(
            health.get("saturatedDenseUnits"), "health saturated units"
        )
        <= HEALTH_THRESHOLDS["maximumSaturatedDenseUnits"],
        "deadDenseUnits": _int(health.get("deadDenseUnits"), "health dead units")
        <= HEALTH_THRESHOLDS["maximumDeadDenseUnits"],
        "deadDenseUnitsVsI0": _int(health.get("deadDenseUnits"), "health dead units")
        <= _int(health.get("i0DeadDenseUnits"), "health I0 dead units"),
        "denseActiveFraction": lower
        <= _number(health.get("denseActiveFraction"), "health active fraction")
        <= upper,
        "maximumAbsoluteCorrection": _int(
            health.get("maximumAbsoluteCorrectionCp"), "health maximum correction"
        )
        <= HEALTH_THRESHOLDS["maximumAbsoluteCorrectionCp"],
        "finitePredictions": True,
        "expectedFileSize": network_bytes == 12392940,
        "roundTripHash": True,
        "deploymentFloatParameterRoundTripExact": True,
        "deploymentFloatRequantizationByteIdentical": True,
        "wholeCorpusPythonCppIntegerAgreement": _int(
            health.get("pythonCppMismatches"), "health Python/C++ mismatches"
        )
        == 0,
    }


def _verify_health(manifest: Mapping[str, Any], network_bytes: int, label: str) -> dict[str, Any]:
    health = manifest.get("health")
    if not isinstance(health, Mapping):
        raise ValueError(f"{label}: health is not an object")
    expected_fields = {
        "positions",
        "meanFloatQuantizationPenaltyCp",
        "maximumFloatQuantizationPenaltyCp",
        "deadDenseUnits",
        "i0DeadDenseUnits",
        "saturatedDenseUnits",
        "denseActiveFraction",
        "maximumAbsoluteCorrectionCp",
        "pythonCppMismatches",
        "checks",
        "passed",
    }
    if set(health) != expected_fields:
        raise ValueError(f"{label}: unexpected health field inventory")
    checks = health.get("checks")
    if not isinstance(checks, Mapping) or set(checks) != EXPECTED_HEALTH_CHECKS:
        raise ValueError(f"{label}: unexpected health-check inventory")
    expected = _expected_health_checks(health, network_bytes)
    # The four checks without an exposed numeric scalar are authenticated
    # aggregate booleans.  All other checks are independently rederived.
    for key in (
        "finitePredictions",
        "roundTripHash",
        "deploymentFloatParameterRoundTripExact",
        "deploymentFloatRequantizationByteIdentical",
    ):
        if checks.get(key) is not True:
            expected[key] = False
    if dict(checks) != expected:
        raise ValueError(f"{label}: health checks disagree with frozen thresholds")
    passed = all(expected.values())
    if health.get("passed") is not passed:
        raise ValueError(f"{label}: aggregate health outcome is inconsistent")
    if _int(health.get("positions"), f"{label}.positions") != 16384:
        raise ValueError(f"{label}: unexpected health position count")
    return {
        "passed": passed,
        "checks": expected,
        "denseActiveFraction": _number(health["denseActiveFraction"], f"{label}.active"),
        "deadDenseUnits": _int(health["deadDenseUnits"], f"{label}.dead"),
        "saturatedDenseUnits": _int(health["saturatedDenseUnits"], f"{label}.saturated"),
        "maximumAbsoluteCorrectionCp": _int(
            health["maximumAbsoluteCorrectionCp"], f"{label}.maximum correction"
        ),
        "meanFloatQuantizationPenaltyCp": _number(
            health["meanFloatQuantizationPenaltyCp"], f"{label}.mean penalty"
        ),
        "maximumFloatQuantizationPenaltyCp": _number(
            health["maximumFloatQuantizationPenaltyCp"], f"{label}.maximum penalty"
        ),
        "pythonCppMismatches": _int(
            health["pythonCppMismatches"], f"{label}.mismatches"
        ),
    }


def _manifest_artifact_expectations(candidate_id: str) -> dict[str, dict[str, Any]]:
    stem = f"build-msvc/king-state-v3/{candidate_id}.bundle/{candidate_id}"
    return {
        "network": {"path": f"{stem}.nnue"},
        "canonicalDeploymentFloat": {"path": f"{stem}.float"},
        "optimizerShadow": {"path": f"{stem}.optimizer-shadow.float"},
        "plan": PLAN_IDENTITY,
        **INITIALIZER_IDENTITIES,
    }


def _verify_manifest_artifacts(
    manifest: Mapping[str, Any], candidate_id: str
) -> dict[str, dict[str, Any]]:
    identity_fields = {
        key
        for key, value in manifest.items()
        if isinstance(value, Mapping) and {"path", "bytes", "sha256"} <= set(value)
    }
    if identity_fields != EXPECTED_MANIFEST_IDENTITY_FIELDS:
        raise ValueError(
            f"{candidate_id}: unexpected manifest-pinned artifact inventory {sorted(identity_fields)}"
        )
    expected = _manifest_artifact_expectations(candidate_id)
    verified: dict[str, dict[str, Any]] = {}
    for field in sorted(identity_fields):
        frozen = expected[field]
        identity = _normalize_identity(manifest[field], f"{candidate_id}.{field}")
        if identity["path"] != frozen["path"]:
            raise ValueError(f"{candidate_id}.{field}: unexpected artifact path")
        if set(frozen) == {"path", "bytes", "sha256"} and identity != frozen:
            raise ValueError(f"{candidate_id}.{field}: artifact pin changed")
        verified[field] = _verify_identity(identity, f"{candidate_id}.{field}")
    return verified


def _candidate_summary(
    candidate_id: str,
    manifest: Mapping[str, Any],
    manifest_identity: Mapping[str, Any],
) -> dict[str, Any]:
    if manifest.get("schemaVersion") != 1:
        raise ValueError(f"{candidate_id}: unexpected manifest schema")
    if manifest.get("kind") != "omega-nnue-king-state-v3-candidate":
        raise ValueError(f"{candidate_id}: unexpected manifest kind")
    if manifest.get("profileId") != PROFILE_ID or manifest.get("candidateId") != candidate_id:
        raise ValueError(f"{candidate_id}: profile or candidate id changed")
    if manifest.get("robustness") is not False or manifest.get("seed") != 20260731:
        raise ValueError(f"{candidate_id}: not a primary frozen-seed manifest")
    selected_epoch = _int(manifest.get("selectedEpoch"), f"{candidate_id}.selectedEpoch")
    if selected_epoch not in range(37, 49):
        raise ValueError(f"{candidate_id}: checkpoint is outside the frozen eligible epochs")
    boundary = manifest.get("informationBoundary")
    expected_boundary = {
        "heldOutMetricsComputed": False,
        "heldOutRowsWithheld": 1666,
        "heldOutTargetFieldsDecoded": 0,
        "trainTargetsDecoded": 13088,
        "validationTargetsDecoded": 1630,
    }
    if boundary != expected_boundary:
        raise ValueError(f"{candidate_id}: information boundary changed")
    if manifest.get("deploymentFloatDerivedFromExportedQuantizedNetwork") is not True:
        raise ValueError(f"{candidate_id}: deployment float provenance failed")
    if manifest.get("deploymentFloatRequantizesByteIdentically") is not True:
        raise ValueError(f"{candidate_id}: deployment float requantization failed")

    artifacts = _verify_manifest_artifacts(manifest, candidate_id)
    common = _metric(manifest.get("commonValidation"), f"{candidate_id}.commonValidation")
    i0 = _metric(manifest.get("i0Validation"), f"{candidate_id}.i0Validation")
    zero = _metric(
        manifest.get("zeroResidualValidation"), f"{candidate_id}.zeroResidualValidation"
    )
    health = _verify_health(manifest, artifacts["network"]["bytes"], f"{candidate_id}.health")
    candidate_loss = common["huberLoss"]
    i0_loss = i0["huberLoss"]
    if i0_loss <= 0.0:
        raise ValueError(f"{candidate_id}: non-positive I0 validation loss")
    relative_i0 = (i0_loss - candidate_loss) / i0_loss
    regressions = {
        phase: common["phase"][phase]["cpMae"] - i0["phase"][phase]["cpMae"]
        for phase in PHASES
    }
    maximum_regression = max(regressions.values())
    gates = {
        "minimumRelativeHuberImprovementOverI0": relative_i0
        >= ELIGIBILITY_THRESHOLDS["minimumRelativeHuberImprovementOverI0"],
        "lowerPhaseMacroCpMaeVersusI0": common["cpMae"] < i0["cpMae"],
        "lowerHuberLossThanZeroResidual": candidate_loss < zero["huberLoss"],
        "maximumAnyPhaseCpMaeRegressionVersusI0": maximum_regression
        <= ELIGIBILITY_THRESHOLDS["maximumAnyPhaseCpMaeRegressionVersusI0"],
        "deploymentHealth": health["passed"],
    }
    return {
        "candidateId": candidate_id,
        "manifest": dict(manifest_identity),
        "verifiedArtifacts": artifacts,
        "selectedEpoch": selected_epoch,
        "informationBoundary": dict(boundary),
        "commonValidation": common,
        "i0Validation": i0,
        "zeroResidualValidation": zero,
        "relativeHuberImprovementOverI0": relative_i0,
        "phaseCpMaeRegressionVersusI0": regressions,
        "maximumAnyPhaseCpMaeRegressionVersusI0": maximum_regression,
        "health": health,
        "gates": gates,
        "eligible": all(gates.values()),
    }


def _verify_plan() -> tuple[dict[str, Any], dict[str, Any]]:
    plan_path = _repo_path(PLAN_IDENTITY["path"])
    plan_identity = _verify_identity(PLAN_IDENTITY, "generation-3 plan", expected=PLAN_IDENTITY)
    plan = _load_json(plan_path)
    if (
        plan.get("schemaVersion") != 1
        or plan.get("kind") != "omega-nnue-king-state-v3-training-plan"
        or plan.get("profileId") != PROFILE_ID
    ):
        raise ValueError("generation-3 plan header changed")
    if plan.get("informationBoundary") != {
        "featureAuditTargetFieldsDecoded": 0,
        "heldOutMetricsComputed": False,
        "heldOutTargetFieldsDecoded": 0,
        "trainingPermittedSplits": [0, 1],
    }:
        raise ValueError("generation-3 plan crossed its information boundary")
    frozen = plan.get("frozenTraining")
    if not isinstance(frozen, Mapping) or frozen.get("candidateOrder") != list(CANDIDATES):
        raise ValueError("generation-3 frozen candidate order changed")
    if frozen.get("primarySeed") != 20260731 or frozen.get("robustnessSeed") != 20260732:
        raise ValueError("generation-3 frozen seeds changed")

    identities = plan.get("identities")
    if not isinstance(identities, Mapping):
        raise ValueError("generation-3 plan has no identity inventory")
    declarations: dict[str, Any] = {}
    for name, expected in DECLARATIONS.items():
        key = PLAN_DECLARATION_KEYS[name]
        normalized = _normalize_identity(identities.get(key), f"plan {name}")
        if normalized != expected:
            raise ValueError(f"plan {name}: identity differs from frozen declaration")
        _verify_git_backed_identity(normalized, f"plan {name}", expected_path=expected["path"])
        declarations[name] = _verify_identity(normalized, f"working-tree {name}", expected=expected)

    sources = identities.get("sources")
    if not isinstance(sources, Mapping) or set(sources) != set(PLAN_SOURCE_PATHS):
        raise ValueError("generation-3 plan source inventory changed")
    source_blobs = {
        name: _verify_git_backed_identity(
            sources[name], f"plan source {name}", expected_path=path
        )
        for name, path in PLAN_SOURCE_PATHS.items()
    }
    return plan, {
        "pinnedCommit": PINNED_COMMIT,
        "plan": plan_identity,
        "declarations": declarations,
        "planSourceBlobs": source_blobs,
        "closureGenerator": _actual_identity(Path(__file__)),
    }


def _post_validation_absence(plan: Mapping[str, Any]) -> dict[str, str]:
    paths: dict[str, Path] = {
        "validationSelection": OUTPUT_DIR / "validation-selection.seal.json",
        "robustnessSeal": OUTPUT_DIR / "robustness.seal.json",
        "offlineAccessClaim": OUTPUT_DIR / "offline-test.json.access.json",
        "offlineReport": OUTPUT_DIR / "offline-test.json",
        "offlineSufficientStatistics": OUTPUT_DIR / "offline-test.sufficient.json",
        "offlineFailure": OUTPUT_DIR / "offline-test.failure.json",
        "matchOuterSeal": REPO
        / "build-king-state-v3"
        / "matches"
        / "sealed"
        / "king-state-v3-matches.seal.json",
        "matchInnerSeal": REPO
        / "build-king-state-v3"
        / "matches"
        / "sealed"
        / "king-state-v1-matches.seal.json",
    }
    commands = plan.get("commands")
    if not isinstance(commands, Mapping) or set(commands) != set(CANDIDATES):
        raise ValueError("generation-3 plan command inventory changed")
    for candidate_id in CANDIDATES:
        command = commands[candidate_id]
        if not isinstance(command, Mapping):
            raise ValueError(f"{candidate_id}: plan command is not an object")
        paths[f"{candidate_id}PrimaryFailure"] = _repo_path(str(command.get("failure", "")))
        robust_stem = OUTPUT_DIR / f"{candidate_id}-robustness.bundle" / f"{candidate_id}-robustness"
        paths.update(
            {
                f"{candidate_id}RobustnessBundle": robust_stem.parent,
                f"{candidate_id}RobustnessFailure": OUTPUT_DIR
                / f"{candidate_id}-robustness.failure.json",
            }
        )
    present = {name: _portable_path(path) for name, path in paths.items() if path.exists()}
    if present:
        raise ValueError(f"post-validation generation-3 artifacts exist: {present}")
    return {name: _portable_path(path) for name, path in sorted(paths.items())}


def _build_closure(*, created_utc: str | None = None) -> dict[str, Any]:
    plan, authenticated = _verify_plan()
    summaries: list[dict[str, Any]] = []
    for candidate_id in CANDIDATES:
        manifest_pin = MANIFEST_IDENTITIES[candidate_id]
        manifest_identity = _verify_identity(
            manifest_pin, f"{candidate_id} original manifest", expected=manifest_pin
        )
        manifest = _load_json(_repo_path(manifest_pin["path"]))
        summaries.append(_candidate_summary(candidate_id, manifest, manifest_identity))

    if any(summary["eligible"] for summary in summaries):
        raise ValueError("generation 3 has an eligible validation candidate")
    i0 = summaries[0]["i0Validation"]
    zero = summaries[0]["zeroResidualValidation"]
    for summary in summaries[1:]:
        if summary["i0Validation"] != i0 or summary["zeroResidualValidation"] != zero:
            raise ValueError("generation-3 manifests disagree on validation baselines")
    absence = _post_validation_absence(plan)
    created = created_utc or _utc_now()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "profileId": PROFILE_ID,
        "createdUtc": created,
        "outcome": OUTCOME,
        "authenticatedInputs": authenticated,
        "informationBoundary": {
            "sourceCorpusOpenedByClosure": False,
            "sourceRowsDecodedByClosure": 0,
            "generation3HeldOutTargetFieldsDecoded": 0,
            "generation3HeldOutMetricsComputed": False,
            "candidateManifestHeldOutTargetFieldsDecoded": 0,
            "selectionUsesOnlyAuthenticatedAggregateManifestFields": [
                "commonValidation",
                "i0Validation",
                "zeroResidualValidation",
                "health",
            ],
        },
        "frozenThresholds": {
            "deploymentHealth": HEALTH_THRESHOLDS,
            "validationEligibility": ELIGIBILITY_THRESHOLDS,
            "tieRelativeLoss": 0.0025,
            "tiePriority": list(TIE_PRIORITY),
        },
        "baselines": {"I0": i0, "zeroResidual": zero},
        "candidateGateSummary": summaries,
        "postValidationArtifactsCheckedAbsent": absence,
        "decision": {
            "eligibleCandidates": [],
            "selectedCandidateId": None,
            "selectionSealCreated": False,
            "robustnessAuthorized": False,
            "heldOutAuthorized": False,
            "matchesAuthorized": False,
            "reason": "no candidate passed every frozen validation and deployment-health gate",
        },
        "nextGenerationRule": {
            "runnerUpFallbackPermitted": False,
            "freshPreregistrationRequired": True,
            "freshNamespacesRequired": True,
            "freshSeedsRequired": True,
            "generation3HeldOutMayBeReused": False,
        },
    }


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("closure createdUtc is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("closure createdUtc is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("closure createdUtc must be timezone-aware")
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
        # A hard-link commit is atomic and fails if the destination exists.
        os.link(staging, output)
    finally:
        staging.unlink(missing_ok=True)


def _create(path: Path) -> dict[str, Any]:
    closure = _build_closure()
    _atomic_no_clobber(path, _canonical_json(closure))
    return closure


def _verify(path: Path) -> dict[str, Any]:
    closure_path = path.resolve()
    recorded = _load_json(closure_path)
    created = _validate_timestamp(recorded.get("createdUtc"))
    recomputed = _build_closure(created_utc=created)
    if recorded != recomputed:
        raise ValueError("generation-3 closure differs from a fresh recomputation")
    if closure_path.read_bytes() != _canonical_json(recorded):
        raise ValueError("generation-3 closure is not canonical JSON")
    return recorded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--closure", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        closure = _create(args.output)
        print(
            f"Published generation-3 closure: {args.output.resolve()} "
            f"({len(closure['candidateGateSummary'])} candidates; held-out untouched)"
        )
        return 0
    if args.command == "verify":
        closure = _verify(args.closure)
        print(f"Verified generation-3 closure: {closure['outcome']}")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
