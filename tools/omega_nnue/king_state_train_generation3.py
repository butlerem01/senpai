#!/usr/bin/env python3
"""Train and select the preregistered generation-3 Omega NNUE candidates.

This module is deliberately additive.  Generation-2 artifacts pin the
existing trainer and orchestration sources, so this file imports those
sources as read-only libraries and never monkey-patches them.

The public workflow is:

* ``plan`` verifies the target-opaque finalized-corpus preflight and freezes
  exact G3A/G3B/G3C commands.
* ``run`` executes one or all frozen primary commands.  Split 2 is routed and
  discarded before JSON target decoding.
* ``select`` recomputes the common group-balanced validation metric, applies
  the preregistered gates, and exclusively seals one winner.
* ``run-robustness`` reruns only the selected recipe with the robustness seed
  and seals its directional validation checks.

No command in this module decodes split-2 target fields.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

# The training worker is single-threaded by contract.  Set these before this
# module imports NumPy so OpenBLAS/OMP cannot choose a scheduler-dependent
# reduction order.  Frozen worker commands repeat the same environment.
DETERMINISTIC_WORKER_ENVIRONMENT = {
    "BLIS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "PYTHONHASHSEED": "0",
}
NUMPY_WAS_PRELOADED = "numpy" in sys.modules
for _environment_name, _environment_value in (
    DETERMINISTIC_WORKER_ENVIRONMENT.items()
):
    os.environ[_environment_name] = _environment_value

import numpy as np
import numpy.core._multiarray_umath as numpy_core

import omega_nnue as omega_nnue_module
import train as base
from omega_nnue import (
    ACCUMULATOR_SIZE,
    ACTIVATION_MAX,
    ARCHITECTURE_KING_STATE_RESIDUAL,
    HEADER_BYTES,
    HIDDEN_DIVISOR,
    HIDDEN_SIZE,
    KING_BUCKET_COUNT,
    KING_STATE_FEATURE_COUNT,
    KING_STATE_OCCUPANCY_FEATURES,
    OUTPUT_DIVISOR,
    QuantizedNetwork,
    active_features,
    deterministic_split,
    nnue_input_signature,
    payload_bytes_for_architecture,
)

try:
    import phase_incidence_preflight
except ImportError:
    phase_incidence_preflight = None  # type: ignore[assignment]


SCHEMA_VERSION = 1
PLAN_KIND = "omega-nnue-king-state-v3-training-plan"
CANDIDATE_KIND = "omega-nnue-king-state-v3-candidate"
SELECTION_KIND = "omega-nnue-king-state-v3-validation-selection"
ROBUSTNESS_KIND = "omega-nnue-king-state-v3-robustness"
FAILURE_KIND = "omega-nnue-king-state-v3-candidate-failure"
PROFILE_KIND = "omega-nnue-king-state-v3-preregistration"
PROFILE_ID = "king-state-v3-deep-hce-v4"
PHASES = ("opening", "middlegame", "late", "endgame")
CANDIDATES = ("G3A", "G3B", "G3C")
TIE_PRIORITY = ("G3B", "G3C", "G3A")
PRIMARY_SEED = 20260731
ROBUSTNESS_SEED = 20260732
SPLIT_SEED = 4989
TRAIN_PERCENT = 80.0
VALIDATION_PERCENT = 10.0
EPOCHS = 48
QAT_EPOCHS = 12
FIRST_QAT_EPOCH = 37
BATCH_SIZE = 256
LEARNING_RATE = 0.003
QAT_LR_SCALE = 0.1
DELTA_THRESHOLD = 8
DELTA_FIRST_EPOCH = 9
CP_CLIP = 2000.0
CP_NORMALIZER = 100.0
HUBER_DELTA = 2.0
SOFT_WDL_WEIGHT = 0.25
SOFT_WDL_SCALE = 173.71779276130073
BASE_PIECE_FEATURES = KING_STATE_OCCUPANCY_FEATURES // KING_BUCKET_COUNT
REFERENCE_BUCKET = 10
EXPECTED_NETWORK_BYTES = HEADER_BYTES + payload_bytes_for_architecture(
    ARCHITECTURE_KING_STATE_RESIDUAL
)
CPP_STREAM_MODE = "--evaluate-network-stream"

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO.parent
FROZEN_RUNTIME = (
    REPO / "tools" / "omega_nnue" / "frozen_runtime" / "king-state-v3"
)
DATA_DIR = REPO / "build-msvc" / "data-generation" / "deep-hce-v4"
OUTPUT_DIR = REPO / "build-msvc" / "king-state-v3"
PREREGISTRATION = (
    REPO / "validation" / "omega-nnue-king-state-v3-preregistration.json"
)
AMENDMENT = (
    REPO / "validation" / "omega-nnue-king-state-v3-amendment-001.json"
)
AMENDMENT_BYTES = 8729
AMENDMENT_SHA256 = (
    "846f43487558246edec871359f9f0a5dbd0377e69a3b95baee6ab6f47f32f78b"
)
AMENDMENT_002 = (
    REPO / "validation" / "omega-nnue-king-state-v3-amendment-002.json"
)
AMENDMENT_002_BYTES = 7192
AMENDMENT_002_SHA256 = (
    "833886a638ebda8d062dfd195d22cfc482152730284d7da5f73a5066fb69c266"
)
AMENDMENT_003 = (
    REPO / "validation" / "omega-nnue-king-state-v3-amendment-003.json"
)
# Frozen after the complete target-opaque historical inventory.  The
# fixed-width placeholders are replaced once, before any G3 sampling.
AMENDMENT_003_BYTES = 68249
AMENDMENT_003_SHA256 = (
    "30d4eeb080d7a8d07d20b11abe8f3b2df19dc8c5e3c4ab354c523fcdce642469"
)
PRELABEL_SEAL = DATA_DIR / "king-state-v1-prelabel.seal.json"
CORPUS = DATA_DIR / "deep-hce-v2-residual.jsonl"
CORPUS_MANIFEST = DATA_DIR / "deep-hce-v2-residual.jsonl.manifest.json"
# This is the post-label residual-corpus audit.  It remains target-opaque and
# is distinct from both the pre-label final-root audit and the later,
# independently sealed preclaim audit used immediately before held-out access.
PHASE_AUDIT = DATA_DIR / "phase-incidence-preflight.seal.json"
INITIALIZER_RESOLUTION = DATA_DIR / "generation3-initializer-resolution.seal.json"
CPP_EVALUATOR = FROZEN_RUNTIME / "evaluator" / "omega_nnue.exe"
PLAN_PATH = OUTPUT_DIR / "training-plan.json"
SELECTION_PATH = OUTPUT_DIR / "validation-selection.seal.json"
ROBUSTNESS_PATH = OUTPUT_DIR / "robustness.seal.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    resolved = _resolve(path)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "bytes": stat.st_size,
        "sha256": _sha256(resolved),
    }


def _runtime_record() -> dict[str, Any]:
    executable = _resolve(Path(sys.executable))
    if not executable.is_file():
        raise ValueError("Python executable cannot be identity-pinned")
    numpy_module = _resolve(Path(str(np.__file__)))
    if not numpy_module.is_file():
        raise ValueError("NumPy module cannot be identity-pinned")
    return {
        "pythonExecutable": _identity(executable),
        "pythonImplementation": platform.python_implementation(),
        "pythonVersion": platform.python_version(),
        "pythonVersionDetail": sys.version,
        "numpyVersion": np.__version__,
        "numpyModule": _identity(numpy_module),
        "numpyCore": _identity(Path(str(numpy_core.__file__))),
        "numpyBuildConfiguration": np.__config__.CONFIG,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "byteOrder": sys.byteorder,
        "numpyPreloadedBeforeTrainingWorkerContract": NUMPY_WAS_PRELOADED,
        "workerEnvironment": dict(DETERMINISTIC_WORKER_ENVIRONMENT),
    }


def _worker_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(DETERMINISTIC_WORKER_ENVIRONMENT)
    return environment


def _require_deterministic_runtime_environment() -> None:
    for name, expected in DETERMINISTIC_WORKER_ENVIRONMENT.items():
        if os.environ.get(name) != expected:
            raise ValueError(
                f"deterministic runtime requires {name}={expected}"
            )
    if NUMPY_WAS_PRELOADED:
        raise ValueError(
            "NumPy was imported before the generation-3 thread contract"
        )


def _same_identity(left: Any, right: Any) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    try:
        return (
            _resolve(Path(str(left["path"])))
            == _resolve(Path(str(right["path"])))
            and int(left["bytes"]) == int(right["bytes"])
            and str(left["sha256"]).lower()
            == str(right["sha256"]).lower()
        )
    except (KeyError, TypeError, ValueError):
        return False


def _verify_identity(value: Any, label: str) -> Path:
    pin = _mapping(value, label)
    actual = _identity(Path(str(pin.get("path", ""))))
    if not _same_identity(pin, actual):
        raise ValueError(f"{label} identity changed: {actual['path']}")
    return Path(actual["path"])


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label}: expected an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected an array")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: expected a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label}: expected a finite number")
    return result


def _strict_equal(actual: Any, expected: Any) -> bool:
    """Compare frozen contract values without Python's bool/int coercion."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        if len(actual) != len(expected):
            return False
        unmatched = list(expected.items())
        for actual_key, actual_value in actual.items():
            for index, (expected_key, expected_value) in enumerate(unmatched):
                if _strict_equal(actual_key, expected_key):
                    if not _strict_equal(actual_value, expected_value):
                        return False
                    unmatched.pop(index)
                    break
            else:
                return False
        return not unmatched
    if type(expected) in (list, tuple):
        return len(actual) == len(expected) and all(
            _strict_equal(left, right)
            for left, right in zip(actual, expected)
        )
    if type(expected) in (set, frozenset):
        if len(actual) != len(expected):
            return False
        unmatched = list(expected)
        for actual_value in actual:
            for index, expected_value in enumerate(unmatched):
                if _strict_equal(actual_value, expected_value):
                    unmatched.pop(index)
                    break
            else:
                return False
        return not unmatched
    result = actual == expected
    return type(result) is bool and result


def _expect(actual: Any, expected: Any, label: str) -> None:
    if not _strict_equal(actual, expected):
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    resolved = _resolve(path)
    before = _identity(resolved)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is invalid JSON: {error}") from error
    after = _identity(resolved)
    if before != after:
        raise ValueError(f"{label} changed while read")
    return _mapping(value, label)


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _exclusive_json(path: Path, value: Any) -> None:
    _exclusive_bytes(path, _canonical_json(value))


def _walk_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _contains_identity(value: Any, wanted: Mapping[str, Any]) -> bool:
    return any(_same_identity(item, wanted) for item in _walk_mappings(value))


def _verify_embedded_identities(value: Any, label: str) -> int:
    count = 0
    for index, item in enumerate(_walk_mappings(value)):
        if {"path", "bytes", "sha256"}.issubset(item):
            _verify_identity(dict(item), f"{label} identity {index}")
            count += 1
    if count == 0:
        raise ValueError(f"{label} contains no pinned identities")
    return count


def _validate_preregistration(path: Path = PREREGISTRATION) -> dict[str, Any]:
    profile = _load_json(path, "generation-3 preregistration")
    _expect(profile.get("schemaVersion"), 1, "preregistration schema")
    _expect(profile.get("kind"), PROFILE_KIND, "preregistration kind")
    _expect(profile.get("profileId"), PROFILE_ID, "preregistration profile")
    fresh = _mapping(
        profile.get("freshTeacherGeneration"), "fresh teacher declaration"
    )
    for key, expected in {
        "dataProfile": "deep-hce-v4",
        "directoryName": "deep-hce-v4",
        "samplerAndSelectorSeed": 2026072104,
        "targetPairs": 8192,
        "targetPairsPerPhase": 2048,
        "targetRoots": 16384,
        "reservePairsPerPhase": 128,
        "maximumCandidatePairs": 8704,
        "maximumCandidateRoots": 17408,
    }.items():
        _expect(fresh.get(key), expected, f"fresh teacher {key}")
    gate = _mapping(
        profile.get("featureOnlyPrelabelAbortGates"), "feature-only gates"
    )
    split = _mapping(gate.get("split"), "feature-only split")
    for key, expected in {
        "splitSeed": SPLIT_SEED,
        "trainPercent": 80,
        "validationPercent": 10,
        "heldOutPercent": 10,
        "collisionPolicy": "error",
    }.items():
        _expect(split.get(key), expected, f"feature-only split {key}")
    required = _mapping(gate.get("requiredChecks"), "feature-only checks")
    _expect(
        required.get("minimumGroupsPerObservedPhaseIncidenceStratum"),
        2,
        "incidence minimum",
    )
    seeds = _mapping(profile.get("modelSeeds"), "model seeds")
    _expect(seeds.get("primarySharedAcrossAllCandidates"), PRIMARY_SEED, "primary seed")
    _expect(
        seeds.get("robustnessSelectedRecipeOnly"),
        ROBUSTNESS_SEED,
        "robustness seed",
    )
    matrix = _mapping(profile.get("candidateMatrix"), "candidate matrix")
    _expect(matrix.get("executionOrder"), list(CANDIDATES), "candidate order")
    common = _mapping(matrix.get("commonTraining"), "common training")
    for key, expected in {
        "epochs": EPOCHS,
        "qatEpochs": QAT_EPOCHS,
        "batchSize": BATCH_SIZE,
        "learningRate": LEARNING_RATE,
        "denseBiasLearningRateScale": 0.1,
        "denseWeightLearningRateScale": 0.01,
        "outputLearningRateScale": 0.25,
        "qatGlobalLearningRateScale": QAT_LR_SCALE,
        "actualOutcomeWeight": 0,
    }.items():
        _expect(common.get(key), expected, f"common training {key}")
    _expect(
        common.get("candidateCheckpointEligibleEpochs"),
        list(range(FIRST_QAT_EPOCH, EPOCHS + 1)),
        "eligible epochs",
    )
    candidates = {
        str(_mapping(item, "candidate").get("id")): _mapping(item, "candidate")
        for item in _sequence(matrix.get("candidates"), "candidates")
    }
    _expect(set(candidates), set(CANDIDATES), "candidate IDs")
    occupancy = _mapping(
        candidates["G3B"].get("conditionedOccupancyParameterization"),
        "G3B occupancy",
    )
    _expect(occupancy.get("deltaOccurrenceThreshold"), DELTA_THRESHOLD, "delta threshold")
    _expect(
        occupancy.get("deltaFrozenEpochsInclusive"),
        [1, 8],
        "delta frozen epochs",
    )
    objective = _mapping(candidates["G3C"].get("objective"), "G3C objective")
    _expect(objective.get("softWdlWeight"), SOFT_WDL_WEIGHT, "soft-WDL weight")
    _expect(
        objective.get("logisticScaleCp"), SOFT_WDL_SCALE, "soft-WDL scale"
    )
    return profile


def _amendment_identity(
    value: Any, *, expected: Path, label: str
) -> None:
    pin = _mapping(value, label)
    pinned_path = Path(str(pin.get("path", "")))
    if not pinned_path.is_absolute():
        pinned_path = REPO / pinned_path
    actual = _identity(expected)
    if (
        _resolve(pinned_path) != _resolve(expected)
        or int(pin.get("bytes", -1)) != int(actual["bytes"])
        or str(pin.get("sha256", "")).lower()
        != str(actual["sha256"]).lower()
    ):
        raise ValueError(f"{label} identity does not match current bytes")


def _validate_amendment(
    path: Path = AMENDMENT,
    *,
    preregistration: Path = PREREGISTRATION,
) -> dict[str, Any]:
    amendment_identity = _identity(path)
    if (
        int(amendment_identity["bytes"]) != AMENDMENT_BYTES
        or str(amendment_identity["sha256"]).lower() != AMENDMENT_SHA256
    ):
        raise ValueError(
            "generation-3 amendment differs from its frozen exact identity"
        )
    amendment = _load_json(path, "generation-3 amendment")
    _expect(amendment.get("schemaVersion"), 1, "amendment schema")
    _expect(
        amendment.get("kind"),
        "omega-nnue-king-state-v3-preregistration-amendment",
        "amendment kind",
    )
    _expect(
        amendment.get("amendmentId"),
        "king-state-v3-amendment-001",
        "amendment ID",
    )
    _expect(amendment.get("profileId"), PROFILE_ID, "amendment profile")
    scope = _mapping(amendment.get("scope"), "amendment scope")
    for key in (
        "changesSeeds",
        "changesQuotas",
        "changesCandidateMatrix",
        "changesThresholds",
        "changesMatchSuites",
        "changesHeldoutPolicy",
    ):
        _expect(scope.get(key), False, f"amendment scope {key}")
    pins = _mapping(
        amendment.get("pinnedDeclarations"), "amendment declarations"
    )
    _amendment_identity(
        pins.get("preregistration"),
        expected=_resolve(preregistration),
        label="amendment preregistration",
    )
    _amendment_identity(
        pins.get("generation2OfflineIncident"),
        expected=REPO
        / "validation"
        / "omega-nnue-king-state-v2-offline-incident.json",
        label="amendment generation-2 incident",
    )
    if phase_incidence_preflight is None:
        raise ValueError("phase-incidence implementation is unavailable")
    _amendment_identity(
        pins.get("phaseIncidenceImplementationAtAmendment"),
        expected=Path(phase_incidence_preflight.__file__).resolve(),
        label="amendment phase-incidence implementation",
    )
    clarification = _mapping(
        amendment.get("phaseIncidenceClarification"),
        "phase-incidence clarification",
    )
    for key, expected in {
        "splitSeed": SPLIT_SEED,
        "trainPercent": 80,
        "validationPercent": 10,
        "heldoutPercent": 10,
        "minimumGroupsPerObservedStratumPerSplit": 2,
        "candidatePoolAndFinalChecksMayDecodeTargets": False,
        "preclaimCheckMayDecodeTargets": False,
        "phaseFieldDecoded": False,
        "phaseDerivedFromOfen": True,
    }.items():
        _expect(clarification.get(key), expected, f"amendment {key}")
    _expect(
        clarification.get("minimumAppliesSeparatelyTo"),
        ["train", "validation", "heldout"],
        "amendment split-specific incidence",
    )
    checkpoint = _mapping(
        amendment.get("qatCheckpointClarification"),
        "QAT checkpoint clarification",
    )
    _expect(
        checkpoint.get("eligibleEpochsInclusive"),
        [FIRST_QAT_EPOCH, EPOCHS],
        "amendment eligible epochs",
    )
    _expect(
        checkpoint.get("relativeToleranceUsedForWithinCandidateTie"),
        0,
        "amendment exact checkpoint tie",
    )
    canonical = _mapping(
        amendment.get("canonicalPaths"), "amendment canonical paths"
    )
    for key, expected in {
        "finalRootAudit": (
            "build-msvc/data-generation/deep-hce-v4/"
            "phase-incidence-final-roots.seal.json"
        ),
        "finalResidualAudit": (
            "build-msvc/data-generation/deep-hce-v4/"
            "phase-incidence-preflight.seal.json"
        ),
        "preclaimAudit": (
            "build-msvc/data-generation/deep-hce-v4/"
            "phase-incidence-preclaim.seal.json"
        ),
    }.items():
        _expect(canonical.get(key), expected, f"amendment {key}")
    _expect(
        canonical.get("prelabelSeal"),
        "build-msvc/data-generation/deep-hce-v4/"
        "king-state-v1-prelabel.seal.json",
        "amendment prelabel path",
    )
    return amendment


def _validate_amendment_002(
    path: Path = AMENDMENT_002,
    *,
    prior_amendment: Path = AMENDMENT,
) -> dict[str, Any]:
    identity = _identity(path)
    if (
        int(identity["bytes"]) != AMENDMENT_002_BYTES
        or str(identity["sha256"]).lower() != AMENDMENT_002_SHA256
    ):
        raise ValueError(
            "generation-3 amendment-002 differs from its frozen identity"
        )
    amendment = _load_json(path, "generation-3 amendment-002")
    _expect(amendment.get("schemaVersion"), 1, "amendment-002 schema")
    _expect(
        amendment.get("kind"),
        "omega-nnue-king-state-v3-preregistration-amendment",
        "amendment-002 kind",
    )
    _expect(
        amendment.get("amendmentId"),
        "king-state-v3-amendment-002",
        "amendment-002 ID",
    )
    _expect(amendment.get("profileId"), PROFILE_ID, "amendment-002 profile")
    _expect(
        amendment.get("status"),
        "target-free inventory field clarification frozen before "
        "generation-3 sampling and teacher labels",
        "amendment-002 frozen status",
    )
    try:
        datetime.fromisoformat(
            str(amendment.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("amendment-002 createdUtc is invalid") from error
    scope = _mapping(amendment.get("scope"), "amendment-002 scope")
    for key in (
        "supersedesAmendment001",
        "changesSeeds",
        "changesQuotas",
        "changesCandidateMatrix",
        "changesThresholds",
        "changesInitializerRule",
        "changesMatchSuites",
        "changesHeldoutPolicy",
    ):
        _expect(scope.get(key), False, f"amendment-002 scope {key}")
    _expect(
        scope.get("clarifiesAmendment001InventoryFieldContractOnly"),
        True,
        "amendment-002 narrow scope",
    )
    _amendment_identity(
        amendment.get("pinnedPriorAmendment"),
        expected=_resolve(prior_amendment),
        label="amendment-002 prior amendment",
    )
    contract = _mapping(
        amendment.get("targetOpaqueInventoryFieldContract"),
        "amendment-002 inventory contract",
    )
    _expect(
        contract.get("caseInsensitiveScalarOfenFields"),
        [
            "ofen",
            "fen",
            "preofen",
            "postofen",
            "initialofen",
            "finalofen",
            "canonicalrootofen",
            "position",
            "positionbefore",
            "positionafter",
            "stopposition",
            "sourceofen",
            "positionkey",
        ],
        "amendment-002 scalar fields",
    )
    _expect(
        contract.get("selectedStringLeafContainers"),
        ["positions", "sourceofens"],
        "amendment-002 selected containers",
    )
    _expect(
        contract.get("selectedContainerSemantics"),
        {
            "fieldNameComparisonCaseInsensitive": True,
            "escapedJsonKeySpellingDecodedBeforeComparison": True,
            "directArrayStringLeavesDecodedAsOfenCandidates": True,
            "nestedArrayStringLeavesDecodedAsOfenCandidates": True,
            "objectElementsRemainKeyDirected": True,
            "ordinaryStringValuesInsideObjectElementsDecoded": False,
            "nonStringLeavesDecoded": False,
            "invalidOfenCandidateContributesNoExclusionSignature": True,
        },
        "amendment-002 selected-container semantics",
    )
    metadata = _sequence(
        contract.get("explicitMetadataLookalikesKeptOpaque"),
        "amendment-002 metadata lookalikes",
    )
    if (
        metadata != sorted(set(metadata))
        or not all(
            isinstance(value, str) and value == value.casefold()
            for value in metadata
        )
    ):
        raise ValueError(
            "amendment-002 opaque metadata allowlist is not sorted, "
            "unique, and case-folded"
        )
    required_metadata = {
        "candidatepositions",
        "endgamepositions",
        "excludedpositionartifacts",
        "fenheader",
        "normalizedofenexact",
        "positionheader",
        "recognizedofenkeys",
        "trainingpositions",
    }
    if not required_metadata.issubset(metadata):
        raise ValueError(
            "amendment-002 opaque metadata allowlist is incomplete"
        )
    _expect(
        contract.get("positionLikeKeyCompleteness"),
        {
            "comparison": "case-folded exact field name",
            "inventoryTriggerSubstrings": ["ofen", "fen", "position"],
            "everyTriggeredObjectKeyCounted": True,
            "recognizedClasses": [
                "caseInsensitiveScalarOfenFields",
                "selectedStringLeafContainers",
                "explicitMetadataLookalikesKeptOpaque",
            ],
            "genericSubstringValueDecodingForbidden": True,
            "unknownTriggeredKeyAbortsBeforeLabels": True,
            "catalogMustRecordObservedKeyCounts": True,
            "catalogMustRecordEmptyUnknownKeySet": True,
        },
        "amendment-002 position-like-key completeness",
    )
    _expect(
        contract.get("skippedOrdinaryStringGuard"),
        {
            "inspectRawLexicalBytesOnly": True,
            "jsonDecodeOrdinaryString": False,
            "recognizedOmegaOfenShape": (
                "exactly nine board-rank separators followed by pocket "
                "brackets and a white-or-black side-to-move delimiter"
            ),
            "recognizesJsonEscapedSlashAndUnicodeEscapedSlashOrBrackets": (
                True
            ),
            "matchingOrdinaryStringAbortsAsUnclassifiedPotentialPosition": (
                True
            ),
            "ordinaryTargetAndPvStringsRemainOpaque": True,
        },
        "amendment-002 skipped ordinary-string guard",
    )
    for key, expected in {
        "genericSubstringMatchingForbidden": True,
        "allOtherScalarValuesSkippedWithoutMaterialization": True,
        "completeJsonStructureMustValidate": True,
        "everyInputByteMustBeConsumed": True,
        "malformedOrTrailingJsonAbortsBeforeLabels": True,
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
    }.items():
        _expect(contract.get(key), expected, f"amendment-002 {key}")
    return amendment


def _validate_amendment_003(
    path: Path = AMENDMENT_003,
    *,
    prior_amendment: Path = AMENDMENT_002,
) -> dict[str, Any]:
    """Validate the frozen correction without importing the pre-label module.

    ``king_state_v3`` imports this trainer to freeze its execution contract, so
    importing it here would create a cycle.  The exact identity plus the
    ordered prior-amendment pin is sufficient at this layer; the pre-label
    orchestrator validates the complete correction contract before sealing it.
    """

    identity = _identity(path)
    if (
        int(identity["bytes"]) != AMENDMENT_003_BYTES
        or str(identity["sha256"]).lower() != AMENDMENT_003_SHA256
    ):
        raise ValueError(
            "generation-3 amendment-003 differs from its frozen identity"
        )
    amendment = _load_json(path, "generation-3 amendment-003")
    _expect(amendment.get("schemaVersion"), 1, "amendment-003 schema")
    _expect(
        amendment.get("kind"),
        "omega-nnue-king-state-v3-preregistration-amendment",
        "amendment-003 kind",
    )
    _expect(
        amendment.get("amendmentId"),
        "king-state-v3-amendment-003",
        "amendment-003 ID",
    )
    _expect(amendment.get("profileId"), PROFILE_ID, "amendment-003 profile")
    _expect(
        amendment.get("status"),
        "target-free inventory correction frozen before generation-3 "
        "sampling and teacher labels",
        "amendment-003 frozen status",
    )
    try:
        datetime.fromisoformat(
            str(amendment.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("amendment-003 createdUtc is invalid") from error
    scope = _mapping(amendment.get("scope"), "amendment-003 scope")
    for key in (
        "supersedesEarlierAmendments",
        "changesSeeds",
        "changesQuotas",
        "changesCandidateMatrix",
        "changesThresholds",
        "changesInitializerRule",
        "changesMatchSuites",
        "changesHeldoutPolicy",
    ):
        _expect(scope.get(key), False, f"amendment-003 scope {key}")
    _expect(
        scope.get("clarifiesTargetOpaqueInventoryOnly"),
        True,
        "amendment-003 narrow scope",
    )
    _amendment_identity(
        amendment.get("pinnedPriorAmendment"),
        expected=_resolve(prior_amendment),
        label="amendment-003 prior amendment",
    )
    for key in (
        "targetOpaqueInventoryCorrections",
        "selectiveOpeningAndTranscriptReplay",
        "pinnedOpeningReplayImplementation",
        "classifiedExistingEvidence",
    ):
        _mapping(amendment.get(key), f"amendment-003 {key}")
    return amendment


def _require_ordered_amendment_chain(
    identities_value: Any,
    *,
    label: str,
) -> None:
    identities = _mapping(identities_value, f"{label} identities")
    _expect(
        identities.get("orderedAmendments"),
        [
            _identity(AMENDMENT),
            _identity(AMENDMENT_002),
            _identity(AMENDMENT_003),
        ],
        f"{label} ordered amendment chain",
    )


def _validate_prelabel_envelope(
    path: Path,
) -> dict[str, Any]:
    prelabel = _load_json(path, "generation-3 pre-label seal")
    _expect(prelabel.get("schemaVersion"), 1, "pre-label schema")
    _expect(
        prelabel.get("kind"),
        "omega-nnue-king-state-v1-prelabel-seal",
        "pre-label compatibility kind",
    )
    _expect(
        prelabel.get("generationId"),
        "king-state-v1-deep-hce-v2",
        "pre-label compatibility generation",
    )
    _expect(
        prelabel.get("profile"),
        {
            "kind": "omega-nnue-king-state-v3-preregistration",
            "profileId": PROFILE_ID,
            "dataProfile": "deep-hce-v4",
            "protocolGeneration": 3,
            "compatibilityEnvelope": (
                "legacy kind/generation retained solely for sealed "
                "deep_hce_v2 run/finalize transport"
            ),
        },
        "pre-label generation-3 profile envelope",
    )
    _expect(
        prelabel.get("declaration"),
        {
            "effectiveBeforeTeacherLabels": True,
            "teacherArtifactsAbsentAtDeclaration": True,
            "rulesOnlySources": True,
            "targetFieldsDecodedByPrelabelAudits": 0,
        },
        "pre-label declaration",
    )
    _expect(
        prelabel.get("contracts"),
        {
            "labelsPermittedOnlyAfterSeal": True,
            "deepHceTargetPairs": 8192,
            "deepHceTargetRoots": 16384,
            "deepHceReservePairsPerPhase": 128,
            "teacherNodesPerRoot": 100000,
            "minimumGroupsPerObservedPhaseIncidenceStratumPerSplit": 2,
            "preclaimReverificationRequired": True,
            "inProfileStructuralRepairAllowed": False,
        },
        "pre-label contracts",
    )
    _expect(
        prelabel.get("trainingContract"),
        prelabel_training_contract(),
        "pre-label exact generation-3 training contract",
    )
    _require_ordered_amendment_chain(
        prelabel.get("identities"),
        label="pre-label seal",
    )
    _verify_embedded_identities(prelabel, "pre-label seal")
    return prelabel


@dataclass(frozen=True)
class FeatureCorpus:
    ofens: tuple[str, ...]
    groups: tuple[str, ...]
    phases: tuple[str, ...]
    splits: np.ndarray
    white_features: np.ndarray
    black_features: np.ndarray
    side_to_move_white: np.ndarray
    input_signatures: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.ofens)

    @property
    def pad_feature(self) -> int:
        return KING_STATE_FEATURE_COUNT

    def indices(self, split: int) -> np.ndarray:
        return np.flatnonzero(self.splits == split)

    def perspective(
        self, indices: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        white = self.white_features[indices]
        black = self.black_features[indices]
        stm_white = self.side_to_move_white[indices, None]
        return np.where(stm_white, white, black), np.where(
            stm_white, black, white
        )


@dataclass(frozen=True)
class LabeledCorpus:
    features: FeatureCorpus
    target_cp: np.ndarray
    search_cp: np.ndarray
    handcrafted_cp: np.ndarray
    source_rows: int
    withheld_rows: int

    @property
    def white_features(self) -> np.ndarray:
        return self.features.white_features

    @property
    def black_features(self) -> np.ndarray:
        return self.features.black_features

    @property
    def pad_feature(self) -> int:
        return self.features.pad_feature

    def indices(self, split: int) -> np.ndarray:
        return self.features.indices(split)

    def perspective_features(
        self, indices: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        return self.features.perspective(indices)


def _target_information_boundary(dataset: LabeledCorpus) -> dict[str, Any]:
    return {
        "trainTargetsDecoded": int(dataset.indices(0).size),
        "validationTargetsDecoded": int(dataset.indices(1).size),
        "heldOutRowsWithheld": dataset.withheld_rows,
        "heldOutTargetFieldsDecoded": 0,
        "heldOutMetricsComputed": False,
    }


def _selective_string(line: str, field: str, location: str) -> str:
    return base._selective_top_level_string(line, field, location=location)


def _phase_from_ofen(ofen: str, location: str) -> str:
    if phase_incidence_preflight is None:
        raise ValueError("phase-incidence implementation is unavailable")
    phase, _side_to_move = phase_incidence_preflight._phase_from_ofen(
        ofen, location=location
    )
    if phase not in PHASES:
        raise AssertionError("phase-incidence implementation returned bad phase")
    return str(phase)


def _feature_rows(
    records: Sequence[tuple[str, str, str]]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[str, ...]]:
    white_rows: list[tuple[int, ...]] = []
    black_rows: list[tuple[int, ...]] = []
    stm: list[bool] = []
    signatures: list[str] = []
    for ofen, _group, _phase in records:
        white = active_features(ofen, 0, ARCHITECTURE_KING_STATE_RESIDUAL)
        black = active_features(ofen, 1, ARCHITECTURE_KING_STATE_RESIDUAL)
        side = ofen.split()[1]
        if side not in {"w", "b"}:
            raise ValueError("OFEN has invalid side to move")
        white_rows.append(white)
        black_rows.append(black)
        stm.append(side == "w")
        signatures.append(
            nnue_input_signature(
                white,
                black,
                side == "w",
                ARCHITECTURE_KING_STATE_RESIDUAL,
            )
        )
    width = max(
        max(map(len, white_rows), default=0),
        max(map(len, black_rows), default=0),
    )
    if width <= 0:
        raise ValueError("feature corpus has no active features")
    white_array = np.full(
        (len(records), width), KING_STATE_FEATURE_COUNT, dtype=np.uint16
    )
    black_array = np.full_like(white_array, KING_STATE_FEATURE_COUNT)
    for index, (white, black) in enumerate(zip(white_rows, black_rows)):
        white_array[index, : len(white)] = white
        black_array[index, : len(black)] = black
    return (
        white_array,
        black_array,
        np.asarray(stm, dtype=np.bool_),
        tuple(signatures),
    )


def _load_feature_corpus(path: Path) -> FeatureCorpus:
    """Decode only groupId and OFEN; derive phase from the OFEN."""

    records: list[tuple[str, str, str]] = []
    splits: list[int] = []
    resolved = _resolve(path)
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            location = f"{resolved}:{line_number}"
            group = _selective_string(line, "groupId", location)
            ofen = " ".join(_selective_string(line, "ofen", location).split())
            phase = _phase_from_ofen(ofen, location)
            records.append((ofen, group, phase))
            splits.append(
                deterministic_split(
                    group, SPLIT_SEED, TRAIN_PERCENT, VALIDATION_PERCENT
                )
            )
    if not records:
        raise ValueError("corpus is empty")
    white, black, stm, signatures = _feature_rows(records)
    return FeatureCorpus(
        ofens=tuple(item[0] for item in records),
        groups=tuple(item[1] for item in records),
        phases=tuple(item[2] for item in records),
        splits=np.asarray(splits, dtype=np.int8),
        white_features=white,
        black_features=black,
        side_to_move_white=stm,
        input_signatures=signatures,
    )


def _load_train_validation(path: Path) -> LabeledCorpus:
    """Decode targets only after routing proves a row is split 0 or 1."""

    records: list[tuple[str, str, str]] = []
    splits: list[int] = []
    targets: list[float] = []
    searches: list[float] = []
    handcrafted: list[float] = []
    source_rows = 0
    withheld = 0
    resolved = _resolve(path)
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            source_rows += 1
            location = f"{resolved}:{line_number}"
            group = _selective_string(line, "groupId", location)
            split = deterministic_split(
                group, SPLIT_SEED, TRAIN_PERCENT, VALIDATION_PERCENT
            )
            if split == 2:
                withheld += 1
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{location}: selected train/validation row is invalid JSON"
                ) from error
            record = _mapping(value, location)
            ofen_value = record.get("ofen")
            phase = record.get("phase")
            if not isinstance(ofen_value, str) or not ofen_value.strip():
                raise ValueError(f"{location}: missing OFEN")
            if phase not in PHASES:
                raise ValueError(f"{location}: invalid phase")
            normalized_ofen = " ".join(ofen_value.split())
            derived_phase = _phase_from_ofen(normalized_ofen, location)
            if phase != derived_phase:
                raise ValueError(
                    f"{location}: stored phase {phase!r} does not match "
                    f"OFEN-derived phase {derived_phase!r}"
                )
            if record.get("targetSemantics") != "search-minus-handcrafted":
                raise ValueError(f"{location}: wrong residual target semantics")
            target = _number(record.get("targetCpStm"), f"{location} residual")
            search = _number(
                record.get("searchTargetCpStm"), f"{location} search target"
            )
            hce = _number(
                record.get("handcraftedCpStm"), f"{location} handcrafted"
            )
            if not math.isclose(search - hce, target, rel_tol=0.0, abs_tol=1e-6):
                raise ValueError(f"{location}: residual identity changed")
            records.append((normalized_ofen, group, derived_phase))
            splits.append(split)
            targets.append(float(np.clip(target, -CP_CLIP, CP_CLIP)))
            searches.append(search)
            handcrafted.append(hce)
    if not records or withheld == 0:
        raise ValueError("corpus must contain train/validation and withheld rows")
    white, black, stm, signatures = _feature_rows(records)
    features = FeatureCorpus(
        ofens=tuple(item[0] for item in records),
        groups=tuple(item[1] for item in records),
        phases=tuple(item[2] for item in records),
        splits=np.asarray(splits, dtype=np.int8),
        white_features=white,
        black_features=black,
        side_to_move_white=stm,
        input_signatures=signatures,
    )
    if features.indices(0).size == 0 or features.indices(1).size == 0:
        raise ValueError("train and validation splits must both be nonempty")
    return LabeledCorpus(
        features=features,
        target_cp=np.asarray(targets, dtype=np.float32),
        search_cp=np.asarray(searches, dtype=np.float32),
        handcrafted_cp=np.asarray(handcrafted, dtype=np.float32),
        source_rows=source_rows,
        withheld_rows=withheld,
    )


def _phase_incidence_audit(features: FeatureCorpus) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for split in range(3):
        phase_groups: dict[str, set[str]] = {
            phase: set() for phase in PHASES
        }
        indices = features.indices(split)
        if indices.size == 0:
            raise ValueError(f"split {split} is empty")
        for index in indices:
            phase_groups[features.phases[int(index)]].add(
                features.groups[int(index)]
            )
        if any(not values for values in phase_groups.values()):
            raise ValueError(f"split {split} is missing a phase")
        groups = sorted(set().union(*phase_groups.values()))
        strata: dict[int, list[str]] = defaultdict(list)
        for group in groups:
            mask = sum(
                1 << phase_index
                for phase_index, phase in enumerate(PHASES)
                if group in phase_groups[phase]
            )
            strata[mask].append(group)
        minimum = min(map(len, strata.values()))
        if minimum < 2:
            raise ValueError(
                f"split {split} has a singleton phase-incidence stratum"
            )
        result[str(split)] = {
            "rows": int(indices.size),
            "groups": len(groups),
            "strata": {
                str(mask): len(values)
                for mask, values in sorted(strata.items())
            },
            "minimumGroupsPerObservedStratum": minimum,
        }
    return {
        "splitSeed": SPLIT_SEED,
        "trainPercent": TRAIN_PERCENT,
        "validationPercent": VALIDATION_PERCENT,
        "minimumRequired": 2,
        "splits": result,
        "targetFieldsDecoded": 0,
        "passed": True,
    }


def _verify_external_phase_audit(corpus: Path, audit_path: Path) -> Any:
    if phase_incidence_preflight is None:
        raise ValueError("phase_incidence_preflight.py is unavailable")
    module_path = Path(phase_incidence_preflight.__file__).resolve()
    if module_path != Path(__file__).with_name(
        "phase_incidence_preflight.py"
    ).resolve():
        raise ValueError("phase-incidence preflight imported from a wrong path")
    if not audit_path.is_file():
        raise FileNotFoundError(audit_path)
    return phase_incidence_preflight.verify_audit(
        corpus,
        audit_path,
        split_seed=SPLIT_SEED,
        train_percent=TRAIN_PERCENT,
        validation_percent=VALIDATION_PERCENT,
        minimum_groups=2,
    )


def _predict_quantized(
    network: QuantizedNetwork,
    features: FeatureCorpus,
    indices: np.ndarray | None = None,
    batch_size: int = BATCH_SIZE,
) -> np.ndarray:
    wanted = (
        np.arange(features.count, dtype=np.int64)
        if indices is None
        else np.asarray(indices, dtype=np.int64)
    )
    output = np.empty(wanted.size, dtype=np.int32)
    for start in range(0, wanted.size, batch_size):
        selected = wanted[start : start + batch_size]
        stm, opponent = features.perspective(selected)
        output[start : start + selected.size] = network.predict_features(
            stm, opponent
        )
    return output


def _huber(error_cp: np.ndarray) -> np.ndarray:
    normalized = error_cp.astype(np.float64) / CP_NORMALIZER
    absolute = np.abs(normalized)
    return np.where(
        absolute <= HUBER_DELTA,
        0.5 * normalized * normalized,
        HUBER_DELTA * (absolute - 0.5 * HUBER_DELTA),
    )


def _common_metrics(
    dataset: LabeledCorpus,
    split: int,
    predictions: np.ndarray,
) -> dict[str, Any]:
    indices = dataset.indices(split)
    if predictions.shape == (dataset.features.count,):
        selected_predictions = predictions[indices]
    elif predictions.shape == (indices.size,):
        selected_predictions = predictions
    else:
        raise ValueError("prediction shape does not match metric split")
    targets = dataset.target_cp[indices].astype(np.float64)
    error = selected_predictions.astype(np.float64) - targets
    losses = _huber(error)
    absolute = np.abs(error)
    cells_loss: dict[str, dict[str, list[float]]] = {
        phase: defaultdict(list) for phase in PHASES
    }
    cells_mae: dict[str, dict[str, list[float]]] = {
        phase: defaultdict(list) for phase in PHASES
    }
    for local, corpus_index in enumerate(indices):
        phase = dataset.features.phases[int(corpus_index)]
        group = dataset.features.groups[int(corpus_index)]
        cells_loss[phase][group].append(float(losses[local]))
        cells_mae[phase][group].append(float(absolute[local]))
    phase_metrics: dict[str, Any] = {}
    group_loss: dict[str, dict[str, float]] = {}
    group_mae: dict[str, dict[str, float]] = {}
    for phase in PHASES:
        if not cells_loss[phase]:
            raise ValueError(f"metric split {split} is missing {phase}")
        group_loss[phase] = {
            group: float(np.mean(values))
            for group, values in sorted(cells_loss[phase].items())
        }
        group_mae[phase] = {
            group: float(np.mean(values))
            for group, values in sorted(cells_mae[phase].items())
        }
        phase_metrics[phase] = {
            "rows": sum(map(len, cells_loss[phase].values())),
            "groups": len(group_loss[phase]),
            "huberLoss": float(np.mean(list(group_loss[phase].values()))),
            "cpMae": float(np.mean(list(group_mae[phase].values()))),
        }
    return {
        "huberLoss": float(
            np.mean([phase_metrics[phase]["huberLoss"] for phase in PHASES])
        ),
        "cpMae": float(
            np.mean([phase_metrics[phase]["cpMae"] for phase in PHASES])
        ),
        "phase": phase_metrics,
        "_groupLoss": group_loss,
        "_groupMae": group_mae,
    }


def _public_metrics(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item for key, item in value.items()
        if not str(key).startswith("_")
    }


def _phase_group_weights(dataset: LabeledCorpus) -> np.ndarray:
    indices = dataset.indices(0)
    n = int(indices.size)
    cells: Counter[tuple[str, str]] = Counter()
    groups_by_phase: dict[str, set[str]] = {
        phase: set() for phase in PHASES
    }
    for index in indices:
        phase = dataset.features.phases[int(index)]
        group = dataset.features.groups[int(index)]
        cells[(phase, group)] += 1
        groups_by_phase[phase].add(group)
    if any(not values for values in groups_by_phase.values()):
        raise ValueError("training split is missing a phase")
    weights = np.ones(dataset.features.count, dtype=np.float32)
    for index in indices:
        phase = dataset.features.phases[int(index)]
        group = dataset.features.groups[int(index)]
        weights[int(index)] = (
            n
            / (
                len(PHASES)
                * len(groups_by_phase[phase])
                * cells[(phase, group)]
            )
        )
    if not math.isclose(
        float(np.sum(weights[indices])),
        float(n),
        rel_tol=1e-6,
        abs_tol=1e-4,
    ):
        raise AssertionError("phase/group training weights do not sum to N")
    return weights


def _conditioned_occurrences(dataset: LabeledCorpus) -> np.ndarray:
    counts = np.zeros(KING_STATE_OCCUPANCY_FEATURES, dtype=np.int64)
    for indices in (dataset.indices(0),):
        stm, opponent = dataset.perspective_features(indices)
        for values in (stm, opponent):
            active = values[values < KING_STATE_OCCUPANCY_FEATURES]
            counts += np.bincount(
                active.astype(np.int64),
                minlength=KING_STATE_OCCUPANCY_FEATURES,
            )
    return counts


def _clip_round(
    values: np.ndarray, low: int, high: int, dtype: Any
) -> np.ndarray:
    return np.clip(np.rint(values), low, high).astype(dtype)


def _quantized_effective(
    value: np.ndarray, low: int, high: int, divisor: float = 1.0
) -> np.ndarray:
    return (
        np.clip(np.rint(value * divisor), low, high).astype(np.float32)
        / divisor
    )


@dataclass
class SharedDeltaNetwork:
    ft_bias: np.ndarray
    shared: np.ndarray
    delta: np.ndarray
    state_weights: np.ndarray
    dense_bias: np.ndarray
    dense_weights: np.ndarray
    output_bias: np.ndarray
    output_weights: np.ndarray

    @classmethod
    def from_float(cls, model: base.FloatNetwork) -> "SharedDeltaNetwork":
        if model.ft_weights.shape != (
            KING_STATE_FEATURE_COUNT,
            ACCUMULATOR_SIZE,
        ):
            raise ValueError("I0 has the wrong feature transformer shape")
        conditioned = model.ft_weights[
            :KING_STATE_OCCUPANCY_FEATURES
        ].reshape(
            KING_BUCKET_COUNT, BASE_PIECE_FEATURES, ACCUMULATOR_SIZE
        )
        shared = conditioned[REFERENCE_BUCKET].copy()
        delta = conditioned - shared[None, :, :]
        result = cls(
            ft_bias=model.ft_bias.copy(),
            shared=shared,
            delta=delta.reshape(
                KING_STATE_OCCUPANCY_FEATURES, ACCUMULATOR_SIZE
            ).copy(),
            state_weights=model.ft_weights[
                KING_STATE_OCCUPANCY_FEATURES:
            ].copy(),
            dense_bias=model.dense_bias.copy(),
            dense_weights=model.dense_weights.copy(),
            output_bias=model.output_bias.copy(),
            output_weights=model.output_weights.copy(),
        )
        if not np.array_equal(result.materialize().ft_weights, model.ft_weights):
            raise AssertionError("S+D initialization did not reconstruct I0")
        return result

    def clone(self) -> "SharedDeltaNetwork":
        return SharedDeltaNetwork(
            ft_bias=self.ft_bias.copy(),
            shared=self.shared.copy(),
            delta=self.delta.copy(),
            state_weights=self.state_weights.copy(),
            dense_bias=self.dense_bias.copy(),
            dense_weights=self.dense_weights.copy(),
            output_bias=self.output_bias.copy(),
            output_weights=self.output_weights.copy(),
        )

    def materialize(self) -> base.FloatNetwork:
        rows = (
            self.shared[
                np.arange(KING_STATE_OCCUPANCY_FEATURES)
                % BASE_PIECE_FEATURES
            ]
            + self.delta
        )
        return base.FloatNetwork(
            ft_bias=self.ft_bias.copy(),
            ft_weights=np.concatenate(
                (rows, self.state_weights), axis=0
            ).astype(np.float32, copy=False),
            dense_bias=self.dense_bias.copy(),
            dense_weights=self.dense_weights.copy(),
            output_bias=self.output_bias.copy(),
            output_weights=self.output_weights.copy(),
        )

    def quantize(self) -> QuantizedNetwork:
        return self.materialize().quantize(
            ARCHITECTURE_KING_STATE_RESIDUAL
        )

    def main_parameters(self) -> dict[str, np.ndarray]:
        return {
            "ft_bias": self.ft_bias,
            "shared": self.shared,
            "state_weights": self.state_weights,
            "dense_bias": self.dense_bias,
            "dense_weights": self.dense_weights,
            "output_bias": self.output_bias,
            "output_weights": self.output_weights,
        }


class AdamState:
    def __init__(
        self,
        parameters: Mapping[str, np.ndarray],
        *,
        learning_rate: float,
        scales: Mapping[str, float],
    ) -> None:
        if set(parameters) != set(scales):
            raise ValueError("Adam scale inventory differs from parameters")
        self.parameters = dict(parameters)
        self.learning_rate = float(learning_rate)
        self.scales = dict(scales)
        self.first = {
            name: np.zeros_like(value, dtype=np.float32)
            for name, value in self.parameters.items()
        }
        self.second = {
            name: np.zeros_like(value, dtype=np.float32)
            for name, value in self.parameters.items()
        }
        self.step_count = 0

    def step(
        self,
        gradients: Mapping[str, np.ndarray],
        *,
        global_scale: float,
    ) -> None:
        if set(gradients) != set(self.parameters):
            raise ValueError("Adam gradient inventory differs from parameters")
        self.step_count += 1
        beta1, beta2, epsilon = 0.9, 0.999, 1e-8
        correction1 = 1.0 - beta1**self.step_count
        correction2 = 1.0 - beta2**self.step_count
        for name, parameter in self.parameters.items():
            gradient = np.asarray(gradients[name], dtype=np.float32)
            if not np.all(np.isfinite(gradient)):
                raise FloatingPointError(f"nonfinite gradient in {name}")
            first = self.first[name]
            second = self.second[name]
            first *= beta1
            first += (1.0 - beta1) * gradient
            second *= beta2
            second += (1.0 - beta2) * gradient * gradient
            parameter -= (
                self.learning_rate
                * global_scale
                * self.scales[name]
                * (first / correction1)
                / (np.sqrt(second / correction2) + epsilon)
            )
        self._clamp()

    def _clamp(self) -> None:
        if "ft_bias" in self.parameters:
            np.clip(
                self.parameters["ft_bias"],
                -(1 << 15),
                (1 << 15) - 1,
                out=self.parameters["ft_bias"],
            )
        if "ft_weights" in self.parameters:
            np.clip(
                self.parameters["ft_weights"],
                -(1 << 15),
                (1 << 15) - 1,
                out=self.parameters["ft_weights"],
            )
        if "state_weights" in self.parameters:
            np.clip(
                self.parameters["state_weights"],
                -(1 << 15),
                (1 << 15) - 1,
                out=self.parameters["state_weights"],
            )
        if "dense_weights" in self.parameters:
            np.clip(
                self.parameters["dense_weights"],
                -128.0 / HIDDEN_DIVISOR,
                127.0 / HIDDEN_DIVISOR,
                out=self.parameters["dense_weights"],
            )
        if "output_weights" in self.parameters:
            np.clip(
                self.parameters["output_weights"],
                -128.0 / OUTPUT_DIVISOR,
                127.0 / OUTPUT_DIVISOR,
                out=self.parameters["output_weights"],
            )


class DeltaAdam:
    """Adam state only for threshold-eligible delta rows.

    The clock and both moment tensors remain untouched through epoch 8.
    Starting at epoch 9, the compact eligible tensor receives one Adam step
    per batch.  Ineligible rows have neither moment storage nor a clock.
    """

    def __init__(self, delta: np.ndarray, eligible_rows: np.ndarray) -> None:
        self.delta = delta
        self.rows = np.asarray(eligible_rows, dtype=np.int64)
        self.lookup = np.full(
            KING_STATE_OCCUPANCY_FEATURES, -1, dtype=np.int32
        )
        self.lookup[self.rows] = np.arange(self.rows.size, dtype=np.int32)
        self.first = np.zeros(
            (self.rows.size, ACCUMULATOR_SIZE), dtype=np.float32
        )
        self.second = np.zeros_like(self.first)
        self.step_count = 0

    def zeros(self) -> np.ndarray:
        return np.zeros_like(self.first)

    def step(self, gradient: np.ndarray, *, global_scale: float) -> None:
        if gradient.shape != self.first.shape:
            raise ValueError("delta gradient shape changed")
        self.step_count += 1
        beta1, beta2, epsilon = 0.9, 0.999, 1e-8
        self.first *= beta1
        self.first += (1.0 - beta1) * gradient
        self.second *= beta2
        self.second += (1.0 - beta2) * gradient * gradient
        correction1 = 1.0 - beta1**self.step_count
        correction2 = 1.0 - beta2**self.step_count
        self.delta[self.rows] -= (
            LEARNING_RATE
            * global_scale
            * (self.first / correction1)
            / (np.sqrt(self.second / correction2) + epsilon)
        )


def _step_delta_for_epoch(
    optimizer: DeltaAdam,
    gradient: np.ndarray,
    *,
    epoch: int,
    quantization_aware: bool,
) -> None:
    if epoch < DELTA_FIRST_EPOCH:
        return
    optimizer.step(
        gradient,
        global_scale=QAT_LR_SCALE if quantization_aware else 1.0,
    )


def _round_engine(values: np.ndarray) -> np.ndarray:
    return np.where(
        values >= 0.0,
        np.floor(values + 0.5),
        -np.floor(-values + 0.5),
    )


def _shared_accumulator(
    model: SharedDeltaNetwork,
    features: np.ndarray,
    *,
    quantization_aware: bool,
) -> np.ndarray:
    if quantization_aware:
        bias = _quantized_effective(
            model.ft_bias, -(1 << 15), (1 << 15) - 1
        )
    else:
        bias = model.ft_bias
    result = np.repeat(bias[None, :], features.shape[0], axis=0)
    for index, encoded in enumerate(features):
        real = encoded[encoded != KING_STATE_FEATURE_COUNT].astype(np.int64)
        conditioned = real[real < KING_STATE_OCCUPANCY_FEATURES]
        states = real[real >= KING_STATE_OCCUPANCY_FEATURES]
        if conditioned.size:
            values = (
                model.shared[conditioned % BASE_PIECE_FEATURES]
                + model.delta[conditioned]
            )
            if quantization_aware:
                values = _quantized_effective(
                    values, -(1 << 15), (1 << 15) - 1
                )
            result[index] += values.sum(axis=0)
        if states.size:
            values = model.state_weights[
                states - KING_STATE_OCCUPANCY_FEATURES
            ]
            if quantization_aware:
                values = _quantized_effective(
                    values, -(1 << 15), (1 << 15) - 1
                )
            result[index] += values.sum(axis=0)
    return result


def _shared_forward(
    model: SharedDeltaNetwork,
    stm_features: np.ndarray,
    opponent_features: np.ndarray,
    *,
    quantization_aware: bool,
    need_cache: bool,
) -> tuple[np.ndarray, dict[str, np.ndarray] | None]:
    stm_z = _shared_accumulator(
        model, stm_features, quantization_aware=quantization_aware
    )
    opponent_z = _shared_accumulator(
        model, opponent_features, quantization_aware=quantization_aware
    )
    stm_a = np.clip(stm_z, 0.0, ACTIVATION_MAX)
    opponent_a = np.clip(opponent_z, 0.0, ACTIVATION_MAX)
    joined = np.concatenate((stm_a, opponent_a), axis=1)
    if quantization_aware:
        dense_bias = _quantized_effective(
            model.dense_bias,
            -(1 << 31),
            (1 << 31) - 1,
            HIDDEN_DIVISOR,
        )
        dense_weights = _quantized_effective(
            model.dense_weights, -128, 127, HIDDEN_DIVISOR
        )
        output_bias = float(
            _quantized_effective(
                model.output_bias,
                -(1 << 31),
                (1 << 31) - 1,
                OUTPUT_DIVISOR,
            )[0]
        )
        output_weights = _quantized_effective(
            model.output_weights, -128, 127, OUTPUT_DIVISOR
        )
    else:
        dense_bias = model.dense_bias
        dense_weights = model.dense_weights
        output_bias = float(model.output_bias[0])
        output_weights = model.output_weights
    dense_z = dense_bias + joined @ dense_weights.T
    if quantization_aware:
        dense_z = _round_engine(dense_z)
    dense_a = np.clip(dense_z, 0.0, ACTIVATION_MAX)
    prediction = output_bias + dense_a @ output_weights
    if quantization_aware:
        prediction = _round_engine(prediction)
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


def _sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    output = np.empty_like(values)
    positive = values >= 0.0
    output[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exponential = np.exp(values[~positive])
    output[~positive] = exponential / (1.0 + exponential)
    return output


def _loss_and_output_gradient(
    *,
    prediction: np.ndarray,
    target_cp: np.ndarray,
    search_cp: np.ndarray,
    handcrafted_cp: np.ndarray,
    row_weights: np.ndarray,
    soft_wdl: bool,
) -> tuple[float, np.ndarray, dict[str, float]]:
    if not (
        prediction.shape
        == target_cp.shape
        == search_cp.shape
        == handcrafted_cp.shape
        == row_weights.shape
    ):
        raise ValueError("loss arrays have different shapes")
    weights = row_weights.astype(np.float64)
    denominator = float(np.sum(weights))
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ValueError("batch row weights have a nonpositive sum")
    normalized_weights = weights / denominator
    error = (
        prediction.astype(np.float64) - target_cp.astype(np.float64)
    ) / CP_NORMALIZER
    absolute = np.abs(error)
    quadratic = absolute <= HUBER_DELTA
    huber = np.where(
        quadratic,
        0.5 * error * error,
        HUBER_DELTA * (absolute - 0.5 * HUBER_DELTA),
    )
    derivative = np.where(
        quadratic, error, HUBER_DELTA * np.sign(error)
    ) / CP_NORMALIZER
    loss = float(np.sum(normalized_weights * huber))
    gradient = normalized_weights * derivative
    soft_loss = 0.0
    if soft_wdl:
        teacher_probability = _sigmoid(
            search_cp.astype(np.float64) / SOFT_WDL_SCALE
        )
        student_logit = (
            handcrafted_cp.astype(np.float64)
            + prediction.astype(np.float64)
        ) / SOFT_WDL_SCALE
        bce = (
            np.maximum(student_logit, 0.0)
            - student_logit * teacher_probability
            + np.log1p(np.exp(-np.abs(student_logit)))
        )
        student_probability = _sigmoid(student_logit)
        soft_loss = float(np.sum(normalized_weights * bce))
        loss += SOFT_WDL_WEIGHT * soft_loss
        gradient += (
            SOFT_WDL_WEIGHT
            * normalized_weights
            * (student_probability - teacher_probability)
            / SOFT_WDL_SCALE
        )
    if not math.isfinite(loss) or not np.all(np.isfinite(gradient)):
        raise FloatingPointError("objective became nonfinite")
    return loss, gradient.astype(np.float32), {
        "weightedHuber": float(np.sum(normalized_weights * huber)),
        "weightedSoftWdl": soft_loss,
        "weightSum": denominator,
    }


def _dense_backprop(
    cache: Mapping[str, np.ndarray],
    output_gradient: np.ndarray,
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]:
    output_weights = cache["output_weights"]
    dense_weights = cache["dense_weights"]
    dense_a = cache["dense_a"]
    joined = cache["joined"]
    output_bias_gradient = np.asarray(
        [output_gradient.sum()], dtype=np.float32
    )
    output_weights_gradient = dense_a.T @ output_gradient
    dense_activation_gradient = (
        output_gradient[:, None] * output_weights[None, :]
    )
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
    return {
        "dense_bias": dense_bias_gradient.astype(np.float32),
        "dense_weights": dense_weights_gradient.astype(np.float32),
        "output_bias": output_bias_gradient,
        "output_weights": output_weights_gradient.astype(np.float32),
    }, stm_gradient, opponent_gradient


def _direct_gradients(
    dataset: LabeledCorpus,
    batch: np.ndarray,
    model: base.FloatNetwork,
    *,
    row_weights: np.ndarray,
    soft_wdl: bool,
    quantization_aware: bool,
) -> tuple[float, dict[str, np.ndarray], dict[str, float]]:
    stm, opponent = dataset.perspective_features(batch)
    prediction, cache = model.forward(
        stm,
        opponent,
        quantization_aware=quantization_aware,
        need_cache=True,
    )
    assert cache is not None
    loss, output_gradient, metrics = _loss_and_output_gradient(
        prediction=prediction,
        target_cp=dataset.target_cp[batch],
        search_cp=dataset.search_cp[batch],
        handcrafted_cp=dataset.handcrafted_cp[batch],
        row_weights=row_weights[batch],
        soft_wdl=soft_wdl,
    )
    dense, stm_gradient, opponent_gradient = _dense_backprop(
        cache, output_gradient
    )
    ft_bias = (stm_gradient + opponent_gradient).sum(axis=0)
    ft_weights = np.zeros_like(model.ft_weights, dtype=np.float32)
    for encoded, gradient in zip(stm, stm_gradient):
        real = encoded[encoded != dataset.pad_feature]
        np.add.at(ft_weights, real, gradient)
    for encoded, gradient in zip(opponent, opponent_gradient):
        real = encoded[encoded != dataset.pad_feature]
        np.add.at(ft_weights, real, gradient)
    return loss, {
        "ft_bias": ft_bias.astype(np.float32),
        "ft_weights": ft_weights,
        **dense,
    }, metrics


def _scatter_shared(
    *,
    encoded_rows: np.ndarray,
    activation_gradient: np.ndarray,
    shared_gradient: np.ndarray,
    state_gradient: np.ndarray,
    delta_gradient: np.ndarray,
    delta_optimizer: DeltaAdam,
) -> None:
    for encoded, gradient in zip(encoded_rows, activation_gradient):
        real = encoded[encoded != KING_STATE_FEATURE_COUNT].astype(np.int64)
        conditioned = real[real < KING_STATE_OCCUPANCY_FEATURES]
        states = real[real >= KING_STATE_OCCUPANCY_FEATURES]
        if conditioned.size:
            np.add.at(
                shared_gradient,
                conditioned % BASE_PIECE_FEATURES,
                gradient,
            )
            compact = delta_optimizer.lookup[conditioned]
            eligible = compact >= 0
            if np.any(eligible):
                np.add.at(delta_gradient, compact[eligible], gradient)
        if states.size:
            np.add.at(
                state_gradient,
                states - KING_STATE_OCCUPANCY_FEATURES,
                gradient,
            )


def _shared_gradients(
    dataset: LabeledCorpus,
    batch: np.ndarray,
    model: SharedDeltaNetwork,
    *,
    row_weights: np.ndarray,
    soft_wdl: bool,
    quantization_aware: bool,
    delta_optimizer: DeltaAdam,
) -> tuple[
    float,
    dict[str, np.ndarray],
    np.ndarray,
    dict[str, float],
]:
    stm, opponent = dataset.perspective_features(batch)
    prediction, cache = _shared_forward(
        model,
        stm,
        opponent,
        quantization_aware=quantization_aware,
        need_cache=True,
    )
    assert cache is not None
    loss, output_gradient, metrics = _loss_and_output_gradient(
        prediction=prediction,
        target_cp=dataset.target_cp[batch],
        search_cp=dataset.search_cp[batch],
        handcrafted_cp=dataset.handcrafted_cp[batch],
        row_weights=row_weights[batch],
        soft_wdl=soft_wdl,
    )
    dense, stm_gradient, opponent_gradient = _dense_backprop(
        cache, output_gradient
    )
    shared_gradient = np.zeros_like(model.shared, dtype=np.float32)
    state_gradient = np.zeros_like(model.state_weights, dtype=np.float32)
    delta_gradient = delta_optimizer.zeros()
    _scatter_shared(
        encoded_rows=stm,
        activation_gradient=stm_gradient,
        shared_gradient=shared_gradient,
        state_gradient=state_gradient,
        delta_gradient=delta_gradient,
        delta_optimizer=delta_optimizer,
    )
    _scatter_shared(
        encoded_rows=opponent,
        activation_gradient=opponent_gradient,
        shared_gradient=shared_gradient,
        state_gradient=state_gradient,
        delta_gradient=delta_gradient,
        delta_optimizer=delta_optimizer,
    )
    return loss, {
        "ft_bias": (stm_gradient + opponent_gradient).sum(axis=0).astype(
            np.float32
        ),
        "shared": shared_gradient,
        "state_weights": state_gradient,
        **dense,
    }, delta_gradient, metrics


def _model_is_finite(
    model: base.FloatNetwork | SharedDeltaNetwork,
) -> bool:
    if isinstance(model, SharedDeltaNetwork):
        values = (
            model.ft_bias,
            model.shared,
            model.delta,
            model.state_weights,
            model.dense_bias,
            model.dense_weights,
            model.output_bias,
            model.output_weights,
        )
    else:
        values = tuple(model.parameters().values())
    return all(np.all(np.isfinite(value)) for value in values)


def _model_clone(
    model: base.FloatNetwork | SharedDeltaNetwork,
) -> base.FloatNetwork | SharedDeltaNetwork:
    return model.clone()


def _model_quantize(
    model: base.FloatNetwork | SharedDeltaNetwork,
) -> QuantizedNetwork:
    if isinstance(model, SharedDeltaNetwork):
        return model.quantize()
    return model.quantize(ARCHITECTURE_KING_STATE_RESIDUAL)


def _model_materialize(
    model: base.FloatNetwork | SharedDeltaNetwork,
) -> base.FloatNetwork:
    return model.materialize() if isinstance(model, SharedDeltaNetwork) else model.clone()


def _model_forward(
    model: base.FloatNetwork | SharedDeltaNetwork,
    stm: np.ndarray,
    opponent: np.ndarray,
    *,
    quantization_aware: bool,
    need_cache: bool,
) -> tuple[np.ndarray, dict[str, np.ndarray] | None]:
    if isinstance(model, SharedDeltaNetwork):
        return _shared_forward(
            model,
            stm,
            opponent,
            quantization_aware=quantization_aware,
            need_cache=need_cache,
        )
    return model.forward(
        stm,
        opponent,
        quantization_aware=quantization_aware,
        need_cache=need_cache,
    )


def _dense_health(
    dataset: LabeledCorpus,
    model: base.FloatNetwork | SharedDeltaNetwork,
) -> dict[str, Any]:
    indices = dataset.indices(0)[: min(1024, dataset.indices(0).size)]
    stm, opponent = dataset.perspective_features(indices)
    _prediction, cache = _model_forward(
        model,
        stm,
        opponent,
        quantization_aware=True,
        need_cache=True,
    )
    assert cache is not None
    dense = cache["dense_z"]
    ever_positive = np.any(dense > 0.0, axis=0)
    ever_below = np.any(dense < ACTIVATION_MAX, axis=0)
    active = (dense > 0.0) & (dense < ACTIVATION_MAX)
    return {
        "sampleRows": int(indices.size),
        "deadDenseUnits": int((~ever_positive).sum()),
        "saturatedDenseUnits": int((~ever_below).sum()),
        "denseActiveFraction": float(np.mean(active)),
    }


@dataclass(frozen=True)
class TrainingResult:
    network: QuantizedNetwork
    canonical_float: base.FloatNetwork
    optimizer_shadow: base.FloatNetwork
    selected_epoch: int
    history: tuple[dict[str, Any], ...]
    occupancy: dict[str, Any]
    delta_optimizer_steps: int


def _select_checkpoint_epoch(
    observations: Sequence[tuple[int, float]],
) -> int:
    eligible = [
        (epoch, metric)
        for epoch, metric in observations
        if FIRST_QAT_EPOCH <= epoch <= EPOCHS
        and math.isfinite(metric)
    ]
    if not eligible:
        raise ValueError("no eligible QAT checkpoint")
    # min is stable: an exact metric tie retains the earliest epoch.
    return min(eligible, key=lambda item: item[1])[0]


def _train_candidate(
    *,
    candidate_id: str,
    dataset: LabeledCorpus,
    whole_features: FeatureCorpus,
    initializer: QuantizedNetwork,
    seed: int,
    epochs: int = EPOCHS,
    qat_epochs: int = QAT_EPOCHS,
    batch_size: int = BATCH_SIZE,
    quiet: bool = False,
) -> TrainingResult:
    if candidate_id not in CANDIDATES:
        raise ValueError(f"unknown generation-3 candidate {candidate_id}")
    if epochs != EPOCHS or qat_epochs != QAT_EPOCHS:
        raise ValueError("generation-3 epoch schedule is frozen at 48/12")
    if initializer.architecture != ARCHITECTURE_KING_STATE_RESIDUAL:
        raise ValueError("I0 is not architecture 3")
    initial_float = base.FloatNetwork.from_quantized(initializer)
    if initial_float.quantize(
        ARCHITECTURE_KING_STATE_RESIDUAL
    ).to_bytes() != initializer.to_bytes():
        raise AssertionError("I0 float reconstruction is not byte-identical")
    shared = candidate_id in {"G3B", "G3C"}
    model: base.FloatNetwork | SharedDeltaNetwork = (
        SharedDeltaNetwork.from_float(initial_float)
        if shared
        else initial_float.clone()
    )
    train_indices = dataset.indices(0)
    validation_indices = dataset.indices(1)
    if train_indices.size == 0 or validation_indices.size == 0:
        raise ValueError("training requires nonempty train and validation splits")
    occurrences = _conditioned_occurrences(dataset)
    eligible_rows = np.flatnonzero(occurrences >= DELTA_THRESHOLD)
    occupancy = {
        "conditionedRows": KING_STATE_OCCUPANCY_FEATURES,
        "seenRows": int(np.count_nonzero(occurrences)),
        "eligibleDeltaRows": int(eligible_rows.size),
        "threshold": DELTA_THRESHOLD,
        "occurrencesAcrossBothPerspectives": int(occurrences.sum()),
        "targetFieldsRead": 0,
    }
    if shared:
        assert isinstance(model, SharedDeltaNetwork)
        main_optimizer = AdamState(
            model.main_parameters(),
            learning_rate=LEARNING_RATE,
            scales={
                "ft_bias": 1.0,
                "shared": 0.5,
                "state_weights": 1.0,
                "dense_bias": 0.1,
                "dense_weights": 0.01,
                "output_bias": 0.25,
                "output_weights": 0.25,
            },
        )
        delta_optimizer = DeltaAdam(model.delta, eligible_rows)
    else:
        assert isinstance(model, base.FloatNetwork)
        main_optimizer = AdamState(
            model.parameters(),
            learning_rate=LEARNING_RATE,
            scales={
                "ft_bias": 1.0,
                "ft_weights": 1.0,
                "dense_bias": 0.1,
                "dense_weights": 0.01,
                "output_bias": 0.25,
                "output_weights": 0.25,
            },
        )
        delta_optimizer = None
    row_weights = (
        _phase_group_weights(dataset)
        if candidate_id == "G3C"
        else np.ones(dataset.features.count, dtype=np.float32)
    )
    strata, strata_metadata = base._training_strata(dataset, train_indices)
    rng = np.random.default_rng(seed)
    history: list[dict[str, Any]] = []
    checkpoint_models: dict[int, base.FloatNetwork | SharedDeltaNetwork] = {}
    checkpoint_networks: dict[int, QuantizedNetwork] = {}
    observations: list[tuple[int, float]] = []
    for epoch in range(1, epochs + 1):
        order = base._interleaved_stratified_order(
            strata, rng, batch_size
        )
        quantization_aware = epoch >= FIRST_QAT_EPOCH
        losses: list[float] = []
        for start in range(0, order.size, batch_size):
            batch = order[start : start + batch_size]
            if isinstance(model, SharedDeltaNetwork):
                assert delta_optimizer is not None
                (
                    loss,
                    gradients,
                    delta_gradient,
                    _batch_metrics,
                ) = _shared_gradients(
                    dataset,
                    batch,
                    model,
                    row_weights=row_weights,
                    soft_wdl=candidate_id == "G3C",
                    quantization_aware=quantization_aware,
                    delta_optimizer=delta_optimizer,
                )
                main_optimizer.step(
                    gradients,
                    global_scale=(
                        QAT_LR_SCALE if quantization_aware else 1.0
                    ),
                )
                _step_delta_for_epoch(
                    delta_optimizer,
                    delta_gradient,
                    epoch=epoch,
                    quantization_aware=quantization_aware,
                )
            else:
                loss, gradients, _batch_metrics = _direct_gradients(
                    dataset,
                    batch,
                    model,
                    row_weights=row_weights,
                    soft_wdl=False,
                    quantization_aware=quantization_aware,
                )
                main_optimizer.step(
                    gradients,
                    global_scale=(
                        QAT_LR_SCALE if quantization_aware else 1.0
                    ),
                )
            if not math.isfinite(loss) or not _model_is_finite(model):
                raise FloatingPointError(
                    f"{candidate_id} became nonfinite in epoch {epoch}"
                )
            losses.append(loss)
        network = _model_quantize(model)
        validation_predictions = _predict_quantized(
            network,
            dataset.features,
            indices=validation_indices,
        )
        validation = _common_metrics(
            dataset, 1, validation_predictions
        )
        health = _dense_health(dataset, model)
        if health["deadDenseUnits"] == HIDDEN_SIZE:
            raise FloatingPointError("all dense units died")
        row = {
            "epoch": epoch,
            "quantizationAware": quantization_aware,
            "optimizerSteps": main_optimizer.step_count,
            "deltaOptimizerSteps": (
                0
                if delta_optimizer is None
                else delta_optimizer.step_count
            ),
            "meanBatchLoss": float(np.mean(losses)),
            "commonValidation": _public_metrics(validation),
            "eligibleCheckpoint": epoch >= FIRST_QAT_EPOCH,
            "activationHealth": health,
            "batchStrata": strata_metadata,
        }
        history.append(row)
        if epoch >= FIRST_QAT_EPOCH:
            observations.append((epoch, float(validation["huberLoss"])))
            checkpoint_models[epoch] = _model_clone(model)
            checkpoint_networks[epoch] = network
        if not quiet:
            print(
                f"{candidate_id} epoch {epoch:2d}/{epochs}: "
                f"validation={validation['huberLoss']:.8f} "
                f"qat={'yes' if quantization_aware else 'no'}",
                flush=True,
            )
    selected_epoch = _select_checkpoint_epoch(observations)
    selected_model = checkpoint_models[selected_epoch]
    selected_network = checkpoint_networks[selected_epoch]
    optimizer_shadow = _model_materialize(selected_model)
    canonical = base.FloatNetwork.from_quantized(selected_network)
    if canonical.quantize(
        ARCHITECTURE_KING_STATE_RESIDUAL
    ).to_bytes() != selected_network.to_bytes():
        raise AssertionError("canonical deployment float does not requantize")
    initial_predictions = _predict_quantized(initializer, whole_features)
    reconstructed_predictions = _predict_quantized(
        initial_float.quantize(ARCHITECTURE_KING_STATE_RESIDUAL),
        whole_features,
    )
    if not np.array_equal(initial_predictions, reconstructed_predictions):
        raise AssertionError("I0 whole-corpus migration parity failed")
    return TrainingResult(
        network=selected_network,
        canonical_float=canonical,
        optimizer_shadow=optimizer_shadow,
        selected_epoch=selected_epoch,
        history=tuple(history),
        occupancy=occupancy,
        delta_optimizer_steps=(
            0 if delta_optimizer is None else delta_optimizer.step_count
        ),
    )


def _cpp_stream_predictions(
    helper: Path, network: Path, ofens: Sequence[str]
) -> np.ndarray:
    payload = "".join(ofen + "\n" for ofen in ofens)
    completed = subprocess.run(
        [
            str(_resolve(helper)),
            CPP_STREAM_MODE,
            str(_resolve(network)),
        ],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=600,
    )
    if completed.returncode != 0:
        raise ValueError(
            "C++ parity helper failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    lines = completed.stdout.splitlines()
    if len(lines) != len(ofens):
        raise ValueError(
            f"C++ helper returned {len(lines)}/{len(ofens)} predictions"
        )
    values: list[int] = []
    for ordinal, line in enumerate(lines, 1):
        text = line.strip()
        if not text or not text.lstrip("+-").isdigit():
            raise ValueError(f"C++ helper row {ordinal} is not an integer")
        values.append(int(text))
    return np.asarray(values, dtype=np.int32)


def _runtime_health(
    *,
    network_path: Path,
    canonical_float_path: Path,
    initializer: QuantizedNetwork,
    features: FeatureCorpus,
    cpp_evaluator: Path,
    profile: Mapping[str, Any],
) -> tuple[dict[str, Any], np.ndarray]:
    network = QuantizedNetwork.read(_resolve(network_path))
    if (
        network.architecture != ARCHITECTURE_KING_STATE_RESIDUAL
        or _resolve(network_path).stat().st_size != EXPECTED_NETWORK_BYTES
    ):
        raise ValueError("candidate has the wrong architecture or file size")
    canonical = base.FloatNetwork.read_checkpoint(
        _resolve(canonical_float_path)
    )
    requantized = canonical.quantize(ARCHITECTURE_KING_STATE_RESIDUAL)
    requant_exact = requantized.to_bytes() == network.to_bytes()
    parameter_round_trip = all(
        np.array_equal(
            value,
            base.FloatNetwork.from_quantized(network).parameters()[name],
        )
        for name, value in canonical.parameters().items()
    )
    predictions = np.empty(features.count, dtype=np.int32)
    penalties: list[np.ndarray] = []
    ever_positive = np.zeros(HIDDEN_SIZE, dtype=np.bool_)
    ever_below = np.zeros(HIDDEN_SIZE, dtype=np.bool_)
    active_cells = 0
    total_cells = 0
    finite = True
    for start in range(0, features.count, BATCH_SIZE):
        indices = np.arange(
            start, min(start + BATCH_SIZE, features.count), dtype=np.int64
        )
        stm, opponent = features.perspective(indices)
        integer = network.predict_features(stm, opponent)
        floating, cache = canonical.forward(
            stm,
            opponent,
            quantization_aware=False,
            need_cache=True,
        )
        assert cache is not None
        predictions[start : start + indices.size] = integer
        penalties.append(
            np.abs(floating.astype(np.float64) - integer.astype(np.float64))
        )
        dense = cache["dense_z"]
        active = (dense > 0.0) & (dense < ACTIVATION_MAX)
        active_cells += int(active.sum())
        total_cells += int(active.size)
        ever_positive |= np.any(dense > 0.0, axis=0)
        ever_below |= np.any(dense < ACTIVATION_MAX, axis=0)
        finite = finite and bool(
            np.all(np.isfinite(floating)) and np.all(np.isfinite(dense))
        )
    errors = np.concatenate(penalties)
    cpp = _cpp_stream_predictions(
        cpp_evaluator, network_path, features.ofens
    )
    cpp_mismatches = int(np.count_nonzero(cpp != predictions))
    dead = int((~ever_positive).sum())
    saturated = int((~ever_below).sum())
    active_fraction = active_cells / total_cells

    initializer_float = base.FloatNetwork.from_quantized(initializer)
    initializer_dead = np.zeros(HIDDEN_SIZE, dtype=np.bool_)
    initializer_positive = np.zeros(HIDDEN_SIZE, dtype=np.bool_)
    for start in range(0, features.count, BATCH_SIZE):
        indices = np.arange(
            start, min(start + BATCH_SIZE, features.count), dtype=np.int64
        )
        stm, opponent = features.perspective(indices)
        _values, cache = initializer_float.forward(
            stm,
            opponent,
            quantization_aware=False,
            need_cache=True,
        )
        assert cache is not None
        initializer_positive |= np.any(cache["dense_z"] > 0.0, axis=0)
    initializer_dead = ~initializer_positive
    i0_dead = int(initializer_dead.sum())

    gates = _mapping(
        profile.get("deploymentHealthGate"), "deployment health gate"
    )
    active_range = _sequence(
        gates.get("denseActiveFractionRangeInclusive"),
        "dense active range",
    )
    checks = {
        "meanFloatQuantizationPenalty": float(errors.mean())
        <= _number(
            gates.get("maximumMeanFloatQuantizationPenaltyCp"),
            "mean penalty gate",
        ),
        "singleFloatQuantizationPenalty": float(errors.max())
        <= _number(
            gates.get("maximumSingleFloatQuantizationPenaltyCp"),
            "single penalty gate",
        ),
        "saturatedDenseUnits": saturated
        <= int(gates.get("maximumSaturatedDenseUnits")),
        "deadDenseUnits": dead <= int(gates.get("maximumDeadDenseUnits")),
        "deadDenseUnitsVsI0": (
            dead <= i0_dead
            if gates.get("deadDenseUnitsMayExceedI0") is False
            else True
        ),
        "denseActiveFraction": (
            _number(active_range[0], "active lower")
            <= active_fraction
            <= _number(active_range[1], "active upper")
        ),
        "maximumAbsoluteCorrection": int(np.max(np.abs(predictions)))
        <= int(gates.get("maximumAbsoluteCorrectionCp")),
        "finitePredictions": finite,
        "expectedFileSize": _resolve(network_path).stat().st_size
        == EXPECTED_NETWORK_BYTES,
        "roundTripHash": QuantizedNetwork.read(
            _resolve(network_path)
        ).to_bytes()
        == network.to_bytes(),
        "deploymentFloatParameterRoundTripExact": parameter_round_trip,
        "deploymentFloatRequantizationByteIdentical": requant_exact,
        "wholeCorpusPythonCppIntegerAgreement": cpp_mismatches == 0,
    }
    return {
        "positions": features.count,
        "meanFloatQuantizationPenaltyCp": float(errors.mean()),
        "maximumFloatQuantizationPenaltyCp": float(errors.max()),
        "deadDenseUnits": dead,
        "i0DeadDenseUnits": i0_dead,
        "saturatedDenseUnits": saturated,
        "denseActiveFraction": active_fraction,
        "maximumAbsoluteCorrectionCp": int(np.max(np.abs(predictions))),
        "pythonCppMismatches": cpp_mismatches,
        "checks": checks,
        "passed": all(checks.values()),
    }, predictions


def _identity_from_manifest_output(
    manifest: Mapping[str, Any], corpus: Path
) -> None:
    output = _mapping(manifest.get("output"), "corpus manifest output")
    actual = _identity(corpus)
    path = output.get("path")
    bytes_value = output.get("bytes")
    sha = output.get("sha256")
    if path is None:
        path = actual["path"]
    if bytes_value is None:
        bytes_value = output.get("snapshotBytes")
    if sha is None:
        sha = output.get("snapshotSha256")
    if (
        _resolve(Path(str(path))) != _resolve(corpus)
        or int(bytes_value) != int(actual["bytes"])
        or str(sha).lower() != str(actual["sha256"]).lower()
    ):
        raise ValueError("residual manifest does not pin the corpus")


def _validate_corpus_manifest(
    manifest: Mapping[str, Any], corpus: Path
) -> None:
    _expect(manifest.get("schemaVersion"), 1, "residual manifest schema")
    _expect(
        manifest.get("targetSemantics"),
        "search-minus-handcrafted",
        "residual manifest target semantics",
    )
    _expect(
        manifest.get("networkSemantics"),
        "residual",
        "residual manifest network semantics",
    )
    _expect(
        manifest.get("alignment"),
        {
            "key": "sampleId",
            "normalizedOfenExact": True,
            "nnueInputSignatureExact": True,
            "coverage": "one-to-one",
            "order": "search-teacher",
        },
        "residual manifest alignment",
    )
    _verify_embedded_identities(manifest, "residual manifest")
    _identity_from_manifest_output(manifest, corpus)


def _initializer_from_resolution(
    resolution: Mapping[str, Any],
    explicit: Path | None,
) -> Path:
    if explicit is not None:
        path = _resolve(explicit)
        wanted = _identity(path)
        if not _contains_identity(resolution, wanted):
            raise ValueError(
                "initializer resolution does not pin the explicit I0 network"
            )
        return path
    candidates: list[Path] = []
    for value in _walk_mappings(resolution):
        if set(("path", "bytes", "sha256")).issubset(value):
            path = Path(str(value["path"]))
            if path.suffix.lower() != ".nnue" or not path.is_file():
                continue
            try:
                network = QuantizedNetwork.read(path)
            except (OSError, ValueError):
                continue
            if network.architecture == ARCHITECTURE_KING_STATE_RESIDUAL:
                candidates.append(_resolve(path))
    unique = sorted(set(candidates), key=str)
    if len(unique) != 1:
        raise ValueError(
            "initializer resolution must identify exactly one architecture-3 "
            f"NNUE; found {len(unique)}"
        )
    return unique[0]


def _validate_initializer_resolution(
    resolution: Mapping[str, Any],
) -> tuple[Path, Path]:
    _expect(
        resolution.get("schemaVersion"), 1, "initializer resolution schema"
    )
    _expect(
        resolution.get("kind"),
        "omega-nnue-king-state-v3-initializer-resolution",
        "initializer resolution kind",
    )
    _expect(
        resolution.get("profileId"),
        PROFILE_ID,
        "initializer resolution profile",
    )
    _expect(
        resolution.get("decisionMoment"),
        "before generation-3 teacher labels",
        "initializer resolution timing",
    )
    selected = resolution.get("selectedInitializer")
    if selected not in {"K2", "K0"}:
        raise ValueError("initializer resolution selected an unknown control")
    _expect(
        resolution.get("selectedPriority"),
        1 if selected == "K2" else 2,
        "initializer resolution priority",
    )
    _expect(
        resolution.get("informationBoundary"),
        {
            "generation2TargetFieldsDecoded": 0,
            "generation2NumericMetricsUsed": False,
            "publicStatusAndIdentityMetadataOnly": True,
            "generation3LabelsExisted": False,
        },
        "initializer resolution information boundary",
    )
    _expect(
        resolution.get("immutableAfterPublication"),
        True,
        "initializer resolution immutability",
    )
    _verify_embedded_identities(resolution, "initializer resolution")
    network_path = _verify_identity(
        resolution.get("network"), "initializer resolution network"
    )
    manifest_path = _verify_identity(
        resolution.get("manifest"), "initializer resolution manifest"
    )
    unique_network = _initializer_from_resolution(resolution, None)
    if unique_network != network_path:
        raise ValueError(
            "initializer resolution contains an ambiguous architecture-3 "
            "network inventory"
        )
    return network_path, manifest_path


def _candidate_paths(
    candidate_id: str,
    *,
    robustness: bool = False,
    output_dir: Path = OUTPUT_DIR,
) -> dict[str, Path]:
    suffix = "-robustness" if robustness else ""
    bundle = _candidate_bundle_dir(
        candidate_id,
        robustness=robustness,
        output_dir=output_dir,
    )
    stem = bundle / f"{candidate_id}{suffix}"
    return {
        "network": stem.with_suffix(".nnue"),
        "canonicalFloat": stem.with_suffix(".float"),
        "optimizerShadow": bundle
        / f"{candidate_id}{suffix}.optimizer-shadow.float",
        "manifest": stem.with_suffix(".manifest.json"),
        "failure": output_dir / f"{candidate_id}{suffix}.failure.json",
    }


def _candidate_bundle_dir(
    candidate_id: str,
    *,
    robustness: bool = False,
    output_dir: Path = OUTPUT_DIR,
) -> Path:
    suffix = "-robustness" if robustness else ""
    return output_dir / f"{candidate_id}{suffix}.bundle"


def _payload_identity(path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "path": str(_resolve(path)),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _verify_staged_candidate_bundle(
    *,
    staging: Path,
    paths: Mapping[str, Path],
    payloads: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> None:
    artifact_keys = (
        "network",
        "canonicalFloat",
        "optimizerShadow",
        "manifest",
    )
    expected_files = {
        _resolve(staging / paths[key].name) for key in artifact_keys
    }
    actual_files = {
        _resolve(path)
        for path in staging.iterdir()
        if path.is_file()
    }
    if actual_files != expected_files or any(
        path.is_dir() for path in staging.iterdir()
    ):
        raise ValueError("staged candidate bundle file inventory changed")
    manifest_fields = {
        "network": "network",
        "canonicalFloat": "canonicalDeploymentFloat",
        "optimizerShadow": "optimizerShadow",
    }
    for key, manifest_key in manifest_fields.items():
        staged = staging / paths[key].name
        expected = _payload_identity(paths[key], payloads[key])
        if manifest.get(manifest_key) != expected:
            raise ValueError(
                f"staged candidate manifest has a wrong {manifest_key}"
            )
        actual = _identity(staged)
        if (
            int(actual["bytes"]) != int(expected["bytes"])
            or str(actual["sha256"]).lower()
            != str(expected["sha256"]).lower()
        ):
            raise ValueError(f"staged candidate {key} bytes changed")
    staged_manifest = staging / paths["manifest"].name
    if staged_manifest.read_bytes() != payloads["manifest"]:
        raise ValueError("staged candidate manifest bytes changed")
    if _load_json(staged_manifest, "staged candidate manifest") != dict(
        manifest
    ):
        raise ValueError("staged candidate manifest content changed")


def _publish_candidate_bundle(
    *,
    paths: Mapping[str, Path],
    payloads: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    interruption_hook: Callable[[str, Path, Path], None] | None = None,
) -> None:
    """Stage and atomically publish one complete candidate artifact bundle.

    The final directory is the commit record.  Before the single directory
    rename, no canonical candidate artifact exists.  After it, all four do.
    A hook exists only for deterministic adversarial self-tests.
    """

    artifact_keys = (
        "network",
        "canonicalFloat",
        "optimizerShadow",
        "manifest",
    )
    if set(payloads) != set(artifact_keys):
        raise ValueError("candidate bundle payload inventory changed")
    final_parents = {paths[key].parent for key in artifact_keys}
    if len(final_parents) != 1:
        raise ValueError("candidate artifacts do not share one bundle")
    bundle = _resolve(next(iter(final_parents)))
    output_dir = bundle.parent
    failure = _resolve(paths["failure"])
    if failure.parent != output_dir:
        raise ValueError("candidate failure path left the output directory")
    if bundle.exists() or failure.exists():
        raise FileExistsError(bundle if bundle.exists() else failure)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=f".{bundle.name}-staging-",
        dir=output_dir,
    ) as staging_directory:
        staging = _resolve(Path(staging_directory))
        for key in artifact_keys:
            staged = staging / paths[key].name
            _exclusive_bytes(staged, payloads[key])
            if interruption_hook is not None:
                interruption_hook(f"after-{key}-stage", staging, bundle)
        _verify_staged_candidate_bundle(
            staging=staging,
            paths=paths,
            payloads=payloads,
            manifest=manifest,
        )
        if interruption_hook is not None:
            interruption_hook("before-commit", staging, bundle)
        if bundle.exists():
            raise FileExistsError(bundle)
        # On the pinned Windows runtime this is one atomic, no-replace
        # directory rename.  A destination race fails without touching it.
        os.rename(staging, bundle)
        if interruption_hook is not None:
            interruption_hook("after-commit", staging, bundle)


def _worker_argv(
    *,
    plan: Path,
    candidate_id: str,
    seed: int,
    robustness: bool,
    output_dir: Path,
) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "train-worker",
        "--plan",
        str(_resolve(plan)),
        "--candidate",
        candidate_id,
        "--seed",
        str(seed),
        "--output-dir",
        str(_resolve(output_dir)),
        *(("--robustness",) if robustness else ()),
    ]


def _command_record(
    *,
    plan: Path,
    candidate_id: str,
    seed: int,
    robustness: bool,
    output_dir: Path,
) -> dict[str, Any]:
    paths = _candidate_paths(
        candidate_id, robustness=robustness, output_dir=output_dir
    )
    bundle = _candidate_bundle_dir(
        candidate_id, robustness=robustness, output_dir=output_dir
    )
    argv = _worker_argv(
        plan=plan,
        candidate_id=candidate_id,
        seed=seed,
        robustness=robustness,
        output_dir=output_dir,
    )
    return {
        "candidateId": candidate_id,
        "seed": seed,
        "robustness": robustness,
        "argv": argv,
        "environment": dict(DETERMINISTIC_WORKER_ENVIRONMENT),
        "bundle": str(_resolve(bundle)),
        "network": str(_resolve(paths["network"])),
        "canonicalFloat": str(_resolve(paths["canonicalFloat"])),
        "optimizerShadow": str(_resolve(paths["optimizerShadow"])),
        "manifest": str(_resolve(paths["manifest"])),
        "failure": str(_resolve(paths["failure"])),
    }


def prelabel_training_contract() -> dict[str, Any]:
    """Return the complete target-free generation-3 execution contract."""

    common_scales = {
        "ftBias": 1.0,
        "denseBias": 0.1,
        "denseWeight": 0.01,
        "outputBias": 0.25,
        "outputWeight": 0.25,
    }
    primary_commands = {
        candidate_id: _command_record(
            plan=PLAN_PATH,
            candidate_id=candidate_id,
            seed=PRIMARY_SEED,
            robustness=False,
            output_dir=OUTPUT_DIR,
        )
        for candidate_id in CANDIDATES
    }
    robustness_commands = {
        candidate_id: _command_record(
            plan=PLAN_PATH,
            candidate_id=candidate_id,
            seed=ROBUSTNESS_SEED,
            robustness=True,
            output_dir=OUTPUT_DIR,
        )
        for candidate_id in CANDIDATES
    }
    return {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-prelabel-training-contract",
        "profileId": PROFILE_ID,
        "paths": {
            "trainer": str(Path(__file__).resolve()),
            "preregistration": str(_resolve(PREREGISTRATION)),
            "amendment001": str(_resolve(AMENDMENT)),
            "amendment002": str(_resolve(AMENDMENT_002)),
            "amendment003": str(_resolve(AMENDMENT_003)),
            "trainingPlan": str(_resolve(PLAN_PATH)),
            "outputDirectory": str(_resolve(OUTPUT_DIR)),
            "residualCorpus": str(_resolve(CORPUS)),
            "residualManifest": str(_resolve(CORPUS_MANIFEST)),
            "finalResidualPhaseAudit": str(_resolve(PHASE_AUDIT)),
            "initializerResolution": str(
                _resolve(INITIALIZER_RESOLUTION)
            ),
            "prelabelSeal": str(_resolve(PRELABEL_SEAL)),
            "cppEvaluator": str(_resolve(CPP_EVALUATOR)),
            "validationSelection": str(_resolve(SELECTION_PATH)),
            "robustnessSeal": str(_resolve(ROBUSTNESS_PATH)),
        },
        "runtime": _runtime_record(),
        "candidateOrder": list(CANDIDATES),
        "seeds": {
            "split": SPLIT_SEED,
            "primarySharedAcrossCandidates": PRIMARY_SEED,
            "selectedRecipeRobustness": ROBUSTNESS_SEED,
        },
        "split": {
            "trainPercent": TRAIN_PERCENT,
            "validationPercent": VALIDATION_PERCENT,
            "heldoutPercent": 10.0,
            "routeUnit": "global groupId",
            "heldoutTargetsDecoded": 0,
        },
        "network": {
            "architecture": ARCHITECTURE_KING_STATE_RESIDUAL,
            "kingBuckets": KING_BUCKET_COUNT,
            "featureRows": KING_STATE_FEATURE_COUNT,
            "conditionedOccupancyRows": (
                KING_STATE_OCCUPANCY_FEATURES
            ),
            "accumulatorSize": ACCUMULATOR_SIZE,
            "hiddenSize": HIDDEN_SIZE,
            "expectedExportBytes": EXPECTED_NETWORK_BYTES,
        },
        "commonTraining": {
            "epochs": EPOCHS,
            "floatEpochsInclusive": [1, FIRST_QAT_EPOCH - 1],
            "qatEpochsInclusive": [FIRST_QAT_EPOCH, EPOCHS],
            "batchSize": BATCH_SIZE,
            "optimizer": "Adam",
            "learningRate": LEARNING_RATE,
            "adamBeta1": 0.9,
            "adamBeta2": 0.999,
            "adamEpsilon": 1e-8,
            "learningRateScales": common_scales,
            "qatGlobalLearningRateScale": QAT_LR_SCALE,
            "batchOrdering": "deterministic phase-score interleaving",
            "eachTrainRowExactlyOncePerEpoch": True,
            "targetField": "targetCpStm",
            "targetSemantics": "search-minus-handcrafted",
            "targetClipCpInclusive": [-2000, 2000],
            "cpNormalizer": CP_NORMALIZER,
            "huberDeltaNormalized": HUBER_DELTA,
            "actualOutcomeWeight": 0,
            "strictFinite": True,
        },
        "candidateRecipes": {
            "G3A": {
                "parameterization": "direct architecture-3 feature table",
                "featureTransformerLearningRateScale": 1.0,
                "rowWeighting": "one equal weight per train row",
                "softWdlWeight": 0.0,
            },
            "G3B": {
                "parameterization": "W[b,p]=S[p]+D[b,p]",
                "referenceBucket": REFERENCE_BUCKET,
                "basePieceFeatures": BASE_PIECE_FEATURES,
                "sharedLearningRateScale": 0.5,
                "deltaLearningRateScale": 1.0,
                "stateRowLearningRateScale": 1.0,
                "deltaFrozenEpochsInclusive": [1, 8],
                "deltaTrainableEpochsInclusive": [
                    DELTA_FIRST_EPOCH,
                    EPOCHS,
                ],
                "deltaOccurrenceThresholdBothSplit0Perspectives": (
                    DELTA_THRESHOLD
                ),
                "ineligibleDeltaMomentsAllocated": False,
                "rowWeighting": "one equal weight per train row",
                "softWdlWeight": 0.0,
            },
            "G3C": {
                "parameterizationAndScales": "exactly G3B",
                "rowWeightFormula": "N/(4*G_p*n_pg)",
                "batchNormalization": "sum weighted loss / batch weight sum",
                "softWdlWeight": SOFT_WDL_WEIGHT,
                "softWdlScaleCp": SOFT_WDL_SCALE,
                "teacherScore": "searchTargetCpStm",
                "studentScore": "handcraftedCpStm + predicted residual",
            },
        },
        "checkpoint": {
            "metric": (
                "phase-macro group-balanced quantized residual Huber loss"
            ),
            "eligibleEpochs": list(
                range(FIRST_QAT_EPOCH, EPOCHS + 1)
            ),
            "minimumRule": "exact minimum",
            "exactTieRule": "earliest eligible epoch",
            "relativeTieTolerance": 0,
            "canonicalFloatReconstructedFromQuantizedExport": True,
            "rawOptimizerShadowPreserved": True,
        },
        "validationSelection": {
            "minimumRelativeHuberImprovementOverI0": 0.005,
            "mustLowerPhaseMacroCpMaeVersusI0": True,
            "mustLowerHuberVersusZeroResidual": True,
            "maximumAnyPhaseCpMaeRegressionVersusI0": 5,
            "mustPassEveryDeploymentHealthGate": True,
            "winner": "lowest common validation metric",
            "candidateNearTieRelativeLoss": 0.0025,
            "candidateNearTiePriority": list(TIE_PRIORITY),
        },
        "deploymentHealth": {
            "maximumMeanFloatQuantizationPenaltyCp": 2,
            "maximumSingleFloatQuantizationPenaltyCp": 10,
            "maximumSaturatedDenseUnits": 0,
            "maximumDeadDenseUnits": 8,
            "deadDenseUnitsMayExceedI0": False,
            "denseActiveFractionRangeInclusive": [0.2, 0.75],
            "maximumAbsoluteCorrectionCp": 2500,
            "finitePredictionsRequired": True,
            "expectedFileSizeRequired": True,
            "roundTripHashRequired": True,
            "deploymentFloatParameterRoundTripExact": True,
            "deploymentFloatRequantizationByteIdentical": True,
            "wholeCorpusPythonCppIntegerAgreement": True,
        },
        "robustnessGate": {
            "seed": ROBUSTNESS_SEED,
            "sameRecipeAndHyperparametersExceptSeed": True,
            "mustPassDeploymentHealth": True,
            "mustLowerCommonValidationHuberVersusI0": True,
            "mustLowerCommonValidationHuberVersusZeroResidual": True,
            "mayReplacePrimary": False,
        },
        "artifactPublication": {
            "unit": "one per-candidate directory bundle",
            "bundleArtifacts": [
                "network",
                "canonicalDeploymentFloat",
                "optimizerShadow",
                "manifest",
            ],
            "allPayloadsAndManifestVerifiedInStaging": True,
            "commitBoundary": (
                "single atomic no-replace directory rename on the pinned "
                "Windows runtime"
            ),
            "canonicalArtifactsVisibleBeforeCommit": 0,
            "postCommitWorkerInterruptionRecoveredByExactBundleVerification": (
                True
            ),
            "failureSealOutsideBundleAndMutuallyExclusive": True,
        },
        "commands": {
            "primary": primary_commands,
            "robustnessByPossibleSelectedCandidate": robustness_commands,
            "robustnessExecution": "selected candidate recipe only",
        },
    }


def _prepare_plan(args: argparse.Namespace) -> dict[str, Any]:
    _require_deterministic_runtime_environment()
    canonical_paths = {
        "preregistration": (args.preregistration, PREREGISTRATION),
        "amendment": (args.amendment, AMENDMENT),
        "amendment-002": (args.amendment_002, AMENDMENT_002),
        "amendment-003": (args.amendment_003, AMENDMENT_003),
        "pre-label seal": (args.prelabel_seal, PRELABEL_SEAL),
        "residual corpus": (args.corpus, CORPUS),
        "residual manifest": (args.corpus_manifest, CORPUS_MANIFEST),
        "final residual phase audit": (args.phase_audit, PHASE_AUDIT),
        "initializer resolution": (
            args.initializer_resolution,
            INITIALIZER_RESOLUTION,
        ),
        "C++ evaluator": (args.cpp_evaluator, CPP_EVALUATOR),
        "output directory": (args.output_dir, OUTPUT_DIR),
        "training plan": (args.plan, PLAN_PATH),
    }
    for label, (actual, expected) in canonical_paths.items():
        if _resolve(actual) != _resolve(expected):
            raise ValueError(
                f"{label} must use the canonical generation-3 path: "
                f"{_resolve(expected)}"
            )
    _validate_preregistration(args.preregistration)
    _validate_amendment(
        args.amendment, preregistration=args.preregistration
    )
    _validate_amendment_002(
        args.amendment_002, prior_amendment=args.amendment
    )
    _validate_amendment_003(
        args.amendment_003, prior_amendment=args.amendment_002
    )
    for path, label in (
        (args.amendment, "preregistration amendment"),
        (args.amendment_002, "preregistration amendment-002"),
        (args.amendment_003, "preregistration amendment-003"),
        (args.prelabel_seal, "pre-label seal"),
        (args.corpus, "residual corpus"),
        (args.corpus_manifest, "residual manifest"),
        (args.phase_audit, "phase-incidence audit"),
        (args.initializer_resolution, "initializer resolution"),
        (args.cpp_evaluator, "C++ evaluator"),
    ):
        if not _resolve(path).is_file():
            raise FileNotFoundError(f"{label}: {_resolve(path)}")
    external_audit = _verify_external_phase_audit(
        args.corpus, args.phase_audit
    )
    if external_audit.get("passed") is not True:
        raise ValueError("phase-incidence preflight did not pass")
    features = _load_feature_corpus(args.corpus)
    phase_rows = Counter(features.phases)
    if features.count != 16384 or phase_rows != Counter(
        {phase: 4096 for phase in PHASES}
    ):
        raise ValueError(
            "final residual corpus differs from the frozen 8192-pair "
            "equal-phase quota"
        )
    local_incidence = _phase_incidence_audit(features)
    signatures: dict[str, set[int]] = defaultdict(set)
    for signature, split in zip(features.input_signatures, features.splits):
        signatures[signature].add(int(split))
    crossing = sum(len(values) > 1 for values in signatures.values())
    if crossing:
        raise ValueError(f"{crossing} exact NNUE inputs cross splits")
    if len(signatures) != features.count:
        raise ValueError("finalized corpus contains duplicate exact inputs")
    manifest = _load_json(args.corpus_manifest, "residual manifest")
    _validate_corpus_manifest(manifest, args.corpus)
    resolution = _load_json(
        args.initializer_resolution, "initializer resolution"
    )
    initializer, initializer_manifest = _validate_initializer_resolution(
        resolution
    )
    if (
        args.initializer is not None
        and _resolve(args.initializer) != initializer
    ):
        raise ValueError(
            "explicit initializer differs from the unique sealed resolution"
        )
    initializer_network = QuantizedNetwork.read(initializer)
    if initializer_network.architecture != ARCHITECTURE_KING_STATE_RESIDUAL:
        raise ValueError("resolved I0 is not architecture 3")
    canonical = base.FloatNetwork.from_quantized(initializer_network)
    if canonical.quantize(
        ARCHITECTURE_KING_STATE_RESIDUAL
    ).to_bytes() != initializer_network.to_bytes():
        raise ValueError("resolved I0 does not reconstruct losslessly")
    prelabel = _validate_prelabel_envelope(args.prelabel_seal)
    prereg_pin = _identity(args.preregistration)
    amendment_pin = _identity(args.amendment)
    amendment_002_pin = _identity(args.amendment_002)
    amendment_003_pin = _identity(args.amendment_003)
    if not _contains_identity(prelabel, prereg_pin):
        raise ValueError(
            "pre-label seal does not pin the generation-3 preregistration"
        )
    if not _contains_identity(prelabel, amendment_pin):
        raise ValueError(
            "pre-label seal does not pin the generation-3 amendment"
        )
    if not _contains_identity(prelabel, amendment_002_pin):
        raise ValueError(
            "pre-label seal does not pin generation-3 amendment-002"
        )
    if not _contains_identity(prelabel, amendment_003_pin):
        raise ValueError(
            "pre-label seal does not pin generation-3 amendment-003"
        )
    output_dir = _resolve(args.output_dir)
    plan_path = _resolve(args.plan)
    if plan_path != output_dir / "training-plan.json":
        raise ValueError(
            "--plan must be <output-dir>/training-plan.json"
        )
    commands = {
        candidate_id: _command_record(
            plan=plan_path,
            candidate_id=candidate_id,
            seed=PRIMARY_SEED,
            robustness=False,
            output_dir=output_dir,
        )
        for candidate_id in CANDIDATES
    }
    artifacts = [
        plan_path,
        output_dir / SELECTION_PATH.name,
        output_dir / ROBUSTNESS_PATH.name,
    ]
    for candidate_id in CANDIDATES:
        for robustness in (False, True):
            candidate_paths = _candidate_paths(
                candidate_id,
                robustness=robustness,
                output_dir=output_dir,
            )
            artifacts.append(
                _candidate_bundle_dir(
                    candidate_id,
                    robustness=robustness,
                    output_dir=output_dir,
                )
            )
            artifacts.extend(candidate_paths.values())
    existing = [str(path) for path in artifacts if path.exists()]
    if existing:
        raise ValueError(
            "generation-3 plan would overlap existing artifacts: "
            + ", ".join(existing)
        )
    source_paths = {
        "orchestrator": Path(__file__).resolve(),
        "baseTrainer": Path(base.__file__).resolve(),
        "networkFormat": Path(omega_nnue_module.__file__).resolve(),
        "phaseIncidencePreflight": Path(
            phase_incidence_preflight.__file__
        ).resolve(),
    }
    required_prelabel_identities = {
        "preregistration": prereg_pin,
        "amendment": amendment_pin,
        "amendment-002": amendment_002_pin,
        "amendment-003": amendment_003_pin,
        "initializer resolution": _identity(args.initializer_resolution),
        "resolved initializer": _identity(initializer),
        "resolved initializer manifest": _identity(initializer_manifest),
        "generation-3 trainer": _identity(source_paths["orchestrator"]),
        "float trainer": _identity(source_paths["baseTrainer"]),
        "network format": _identity(source_paths["networkFormat"]),
        "phase-incidence guard": _identity(
            source_paths["phaseIncidencePreflight"]
        ),
        "C++ evaluator": _identity(args.cpp_evaluator),
    }
    for label, required_identity in required_prelabel_identities.items():
        if not _contains_identity(prelabel, required_identity):
            raise ValueError(f"pre-label seal does not pin exact {label}")
    plan = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "createdUtc": _utc_now(),
        "profileId": PROFILE_ID,
        "informationBoundary": {
            "featureAuditTargetFieldsDecoded": 0,
            "trainingPermittedSplits": [0, 1],
            "heldOutTargetFieldsDecoded": 0,
            "heldOutMetricsComputed": False,
        },
        "runtime": _runtime_record(),
        "identities": {
            "preregistration": prereg_pin,
            "amendment": amendment_pin,
            "amendment002": amendment_002_pin,
            "amendment003": amendment_003_pin,
            "prelabelSeal": _identity(args.prelabel_seal),
            "corpus": _identity(args.corpus),
            "corpusManifest": _identity(args.corpus_manifest),
            "phaseIncidenceAudit": _identity(args.phase_audit),
            "initializerResolution": _identity(args.initializer_resolution),
            "initializer": _identity(initializer),
            "initializerManifest": _identity(initializer_manifest),
            "cppEvaluator": _identity(args.cpp_evaluator),
            "sources": {
                name: _identity(path)
                for name, path in sorted(source_paths.items())
            },
        },
        "featureAudit": {
            "rows": features.count,
            "phaseRows": {
                phase: phase_rows[phase] for phase in PHASES
            },
            "exactInputUnique": True,
            "crossSplitExactInputCollisions": 0,
            "localPhaseIncidence": local_incidence,
            "externalPhaseIncidence": external_audit,
            "targetsDecoded": 0,
            "targetsEmitted": 0,
        },
        "initializer": {
            "network": _identity(initializer),
            "manifest": _identity(initializer_manifest),
            "initialFloatReconstruction": "FloatNetwork.from_quantized",
            "requantizesByteIdentically": True,
            "adamStateReset": True,
        },
        "frozenTraining": {
            "candidateOrder": list(CANDIDATES),
            "primarySeed": PRIMARY_SEED,
            "robustnessSeed": ROBUSTNESS_SEED,
            "splitSeed": SPLIT_SEED,
            "epochs": EPOCHS,
            "qatEpochs": QAT_EPOCHS,
            "eligibleEpochs": list(range(FIRST_QAT_EPOCH, EPOCHS + 1)),
            "exactCheckpointTie": "earliest eligible epoch",
            "batchSize": BATCH_SIZE,
            "learningRate": LEARNING_RATE,
            "deltaThreshold": DELTA_THRESHOLD,
            "deltaFirstTrainableEpoch": DELTA_FIRST_EPOCH,
        },
        "commands": commands,
        "outputs": {
            "selection": str(output_dir / SELECTION_PATH.name),
            "robustness": str(output_dir / ROBUSTNESS_PATH.name),
        },
    }
    _exclusive_json(plan_path, plan)
    return plan


def _verify_plan(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_path = _resolve(path)
    plan = _load_json(plan_path, "generation-3 training plan")
    _expect(
        set(plan),
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "profileId",
            "informationBoundary",
            "runtime",
            "identities",
            "featureAudit",
            "initializer",
            "frozenTraining",
            "commands",
            "outputs",
        },
        "plan top-level fields",
    )
    _expect(plan.get("schemaVersion"), SCHEMA_VERSION, "plan schema")
    _expect(plan.get("kind"), PLAN_KIND, "plan kind")
    _expect(plan.get("profileId"), PROFILE_ID, "plan profile")
    if plan_path != _resolve(PLAN_PATH):
        raise ValueError(
            f"plan is not at the canonical generation-3 path: {PLAN_PATH}"
        )
    try:
        datetime.fromisoformat(
            str(plan.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("plan createdUtc is invalid") from error
    _expect(
        plan.get("informationBoundary"),
        {
            "featureAuditTargetFieldsDecoded": 0,
            "trainingPermittedSplits": [0, 1],
            "heldOutTargetFieldsDecoded": 0,
            "heldOutMetricsComputed": False,
        },
        "plan information boundary",
    )
    _require_deterministic_runtime_environment()
    _expect(plan.get("runtime"), _runtime_record(), "plan runtime")
    identities = _mapping(plan.get("identities"), "plan identities")
    canonical_paths = {
        "preregistration": PREREGISTRATION,
        "amendment": AMENDMENT,
        "amendment002": AMENDMENT_002,
        "amendment003": AMENDMENT_003,
        "prelabelSeal": PRELABEL_SEAL,
        "corpus": CORPUS,
        "corpusManifest": CORPUS_MANIFEST,
        "phaseIncidenceAudit": PHASE_AUDIT,
        "initializerResolution": INITIALIZER_RESOLUTION,
        "cppEvaluator": CPP_EVALUATOR,
    }
    _expect(
        set(identities),
        {
            *canonical_paths,
            "initializer",
            "initializerManifest",
            "sources",
        },
        "plan identity inventory",
    )
    for key, canonical_path in canonical_paths.items():
        pinned = _verify_identity(identities.get(key), f"plan {key}")
        if pinned != _resolve(canonical_path):
            raise ValueError(f"plan {key} does not use its canonical path")
    initializer_path = _verify_identity(
        identities.get("initializer"), "plan initializer"
    )
    initializer_manifest_path = _verify_identity(
        identities.get("initializerManifest"),
        "plan initializer manifest",
    )
    sources = _mapping(identities.get("sources"), "plan sources")
    current_sources = {
        "orchestrator": Path(__file__).resolve(),
        "baseTrainer": Path(base.__file__).resolve(),
        "networkFormat": Path(omega_nnue_module.__file__).resolve(),
        "phaseIncidencePreflight": Path(
            phase_incidence_preflight.__file__
        ).resolve()
        if phase_incidence_preflight is not None
        else Path("missing"),
    }
    _expect(
        set(sources), set(current_sources), "plan source inventory"
    )
    for name, expected_path in current_sources.items():
        pin = sources.get(name)
        _verify_identity(pin, f"plan source {name}")
        if not _same_identity(pin, _identity(expected_path)):
            raise ValueError(f"plan source {name} differs from current bytes")
    profile = _validate_preregistration(
        Path(str(identities["preregistration"]["path"]))
    )
    _validate_amendment(
        Path(str(identities["amendment"]["path"])),
        preregistration=Path(
            str(identities["preregistration"]["path"])
        ),
    )
    _validate_amendment_002(
        Path(str(identities["amendment002"]["path"])),
        prior_amendment=Path(
            str(identities["amendment"]["path"])
        ),
    )
    _validate_amendment_003(
        Path(str(identities["amendment003"]["path"])),
        prior_amendment=Path(
            str(identities["amendment002"]["path"])
        ),
    )

    prelabel = _validate_prelabel_envelope(
        Path(str(identities["prelabelSeal"]["path"]))
    )
    required_prelabel = {
        "preregistration": identities["preregistration"],
        "amendment": identities["amendment"],
        "amendment-002": identities["amendment002"],
        "amendment-003": identities["amendment003"],
        "initializer resolution": identities["initializerResolution"],
        "resolved initializer": identities["initializer"],
        "resolved initializer manifest": identities[
            "initializerManifest"
        ],
        "generation-3 trainer": sources["orchestrator"],
        "float trainer": sources["baseTrainer"],
        "network format": sources["networkFormat"],
        "phase-incidence guard": sources["phaseIncidencePreflight"],
        "C++ evaluator": identities["cppEvaluator"],
    }
    for label, wanted in required_prelabel.items():
        if not _contains_identity(prelabel, wanted):
            raise ValueError(f"pre-label seal does not pin exact {label}")

    resolution = _load_json(
        Path(str(identities["initializerResolution"]["path"])),
        "initializer resolution",
    )
    (
        resolved_initializer,
        resolved_initializer_manifest,
    ) = _validate_initializer_resolution(resolution)
    if resolved_initializer != initializer_path:
        raise ValueError("plan initializer differs from sealed resolution")
    if resolved_initializer_manifest != initializer_manifest_path:
        raise ValueError(
            "plan initializer manifest differs from sealed resolution"
        )
    initializer_network = QuantizedNetwork.read(initializer_path)
    if initializer_network.architecture != ARCHITECTURE_KING_STATE_RESIDUAL:
        raise ValueError("plan initializer is not architecture 3")
    initializer_float = base.FloatNetwork.from_quantized(
        initializer_network
    )
    if initializer_float.quantize(
        ARCHITECTURE_KING_STATE_RESIDUAL
    ).to_bytes() != initializer_network.to_bytes():
        raise ValueError("plan initializer does not reconstruct losslessly")
    _expect(
        plan.get("initializer"),
        {
            "network": identities["initializer"],
            "manifest": identities["initializerManifest"],
            "initialFloatReconstruction": "FloatNetwork.from_quantized",
            "requantizesByteIdentically": True,
            "adamStateReset": True,
        },
        "plan initializer block",
    )

    corpus = Path(str(identities["corpus"]["path"]))
    corpus_manifest = _load_json(
        Path(str(identities["corpusManifest"]["path"])),
        "residual corpus manifest",
    )
    _validate_corpus_manifest(corpus_manifest, corpus)
    audit = Path(str(identities["phaseIncidenceAudit"]["path"]))
    external = _verify_external_phase_audit(corpus, audit)
    if external.get("passed") is not True:
        raise ValueError("planned phase-incidence audit no longer passes")
    features = _load_feature_corpus(corpus)
    phase_rows = Counter(features.phases)
    if features.count != 16384 or phase_rows != Counter(
        {phase: 4096 for phase in PHASES}
    ):
        raise ValueError("planned finalized-corpus phase quota changed")
    local_incidence = _phase_incidence_audit(features)
    signatures: dict[str, set[int]] = defaultdict(set)
    for signature, split in zip(features.input_signatures, features.splits):
        signatures[signature].add(int(split))
    crossing = sum(len(values) > 1 for values in signatures.values())
    if crossing or len(signatures) != features.count:
        raise ValueError("planned finalized-corpus exact-input gate changed")
    _expect(
        plan.get("featureAudit"),
        {
            "rows": features.count,
            "phaseRows": {
                phase: phase_rows[phase] for phase in PHASES
            },
            "exactInputUnique": True,
            "crossSplitExactInputCollisions": 0,
            "localPhaseIncidence": local_incidence,
            "externalPhaseIncidence": external,
            "targetsDecoded": 0,
            "targetsEmitted": 0,
        },
        "plan recomputed feature audit",
    )
    _expect(
        plan.get("frozenTraining"),
        {
            "candidateOrder": list(CANDIDATES),
            "primarySeed": PRIMARY_SEED,
            "robustnessSeed": ROBUSTNESS_SEED,
            "splitSeed": SPLIT_SEED,
            "epochs": EPOCHS,
            "qatEpochs": QAT_EPOCHS,
            "eligibleEpochs": list(range(FIRST_QAT_EPOCH, EPOCHS + 1)),
            "exactCheckpointTie": "earliest eligible epoch",
            "batchSize": BATCH_SIZE,
            "learningRate": LEARNING_RATE,
            "deltaThreshold": DELTA_THRESHOLD,
            "deltaFirstTrainableEpoch": DELTA_FIRST_EPOCH,
        },
        "plan frozen training",
    )
    commands = _mapping(plan.get("commands"), "plan commands")
    _expect(set(commands), set(CANDIDATES), "plan candidate commands")
    output_dir = Path(str(commands["G3A"]["bundle"])).resolve().parent
    if output_dir != _resolve(OUTPUT_DIR):
        raise ValueError("candidate outputs are not in canonical output dir")
    for candidate_id in CANDIDATES:
        expected = _command_record(
            plan=plan_path,
            candidate_id=candidate_id,
            seed=PRIMARY_SEED,
            robustness=False,
            output_dir=output_dir,
        )
        _expect(
            commands[candidate_id],
            expected,
            f"{candidate_id} exact frozen command",
        )
    _expect(
        plan.get("outputs"),
        {
            "selection": str(_resolve(SELECTION_PATH)),
            "robustness": str(_resolve(ROBUSTNESS_PATH)),
        },
        "plan canonical outputs",
    )
    return plan, profile


def _write_candidate_artifacts(
    *,
    plan_path: Path,
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
    candidate_id: str,
    seed: int,
    robustness: bool,
    output_dir: Path,
    result: TrainingResult,
    dataset: LabeledCorpus,
    whole_features: FeatureCorpus,
) -> dict[str, Any]:
    paths = _candidate_paths(
        candidate_id, robustness=robustness, output_dir=output_dir
    )
    bundle = _candidate_bundle_dir(
        candidate_id,
        robustness=robustness,
        output_dir=output_dir,
    )
    if bundle.exists():
        raise FileExistsError(bundle)
    if paths["failure"].exists():
        raise FileExistsError(paths["failure"])
    network_payload = result.network.to_bytes()
    canonical_payload = result.canonical_float.checkpoint_bytes()
    optimizer_payload = result.optimizer_shadow.checkpoint_bytes()
    identities = _mapping(plan.get("identities"), "plan identities")
    initializer = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{candidate_id}-staging-", dir=output_dir
    ) as staging_directory:
        staging = Path(staging_directory)
        staged_network = staging / "candidate.nnue"
        staged_float = staging / "candidate.float"
        staged_network.write_bytes(network_payload)
        staged_float.write_bytes(canonical_payload)
        health, _whole_predictions = _runtime_health(
            network_path=staged_network,
            canonical_float_path=staged_float,
            initializer=initializer,
            features=whole_features,
            cpp_evaluator=Path(str(identities["cppEvaluator"]["path"])),
            profile=profile,
        )
    # The candidate metric is recomputed directly on the target-bearing
    # train/validation view; the whole-corpus prediction above is health-only.
    validation_predictions = _predict_quantized(
        result.network,
        dataset.features,
        indices=dataset.indices(1),
    )
    validation = _common_metrics(dataset, 1, validation_predictions)
    i0_predictions = _predict_quantized(
        initializer, dataset.features, indices=dataset.indices(1)
    )
    i0_validation = _common_metrics(dataset, 1, i0_predictions)
    zero_validation = _common_metrics(
        dataset,
        1,
        np.zeros(dataset.features.count, dtype=np.int32),
    )
    selected_history = next(
        row for row in result.history
        if int(row["epoch"]) == result.selected_epoch
    )
    if not math.isclose(
        float(validation["huberLoss"]),
        float(selected_history["commonValidation"]["huberLoss"]),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise AssertionError("selected checkpoint metric changed before export")
    command = _command_record(
        plan=plan_path,
        candidate_id=candidate_id,
        seed=seed,
        robustness=robustness,
        output_dir=output_dir,
    )
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": CANDIDATE_KIND,
        "createdUtc": plan.get("createdUtc"),
        "profileId": PROFILE_ID,
        "candidateId": candidate_id,
        "seed": seed,
        "robustness": robustness,
        "plan": _identity(plan_path),
        "command": command,
        "informationBoundary": _target_information_boundary(dataset),
        "initializer": identities["initializer"],
        "initializerManifest": identities["initializerManifest"],
        "network": _payload_identity(paths["network"], network_payload),
        "canonicalDeploymentFloat": _payload_identity(
            paths["canonicalFloat"], canonical_payload
        ),
        "optimizerShadow": _payload_identity(
            paths["optimizerShadow"], optimizer_payload
        ),
        "deploymentFloatDerivedFromExportedQuantizedNetwork": True,
        "deploymentFloatRequantizesByteIdentically": True,
        "selectedEpoch": result.selected_epoch,
        "eligibleEpochs": list(range(FIRST_QAT_EPOCH, EPOCHS + 1)),
        "checkpointTiePolicy": "exact ties retain earliest eligible epoch",
        "history": list(result.history),
        "occupancy": result.occupancy,
        "deltaOptimizerSteps": result.delta_optimizer_steps,
        "commonValidation": _public_metrics(validation),
        "i0Validation": _public_metrics(i0_validation),
        "zeroResidualValidation": _public_metrics(zero_validation),
        "health": health,
    }
    manifest_payload = _canonical_json(manifest)
    # The final bundle directory is published only after export,
    # canonical-float, whole-corpus health, C++ parity, all payload hashes, and
    # the complete manifest have been verified in staging.  The directory
    # rename is the sole commit boundary, so a fatal candidate is either
    # wholly absent or has a complete, independently verifiable bundle.
    _publish_candidate_bundle(
        paths=paths,
        payloads={
            "network": network_payload,
            "canonicalFloat": canonical_payload,
            "optimizerShadow": optimizer_payload,
            "manifest": manifest_payload,
        },
        manifest=manifest,
    )
    return manifest


def _run_worker(args: argparse.Namespace) -> dict[str, Any]:
    _require_deterministic_runtime_environment()
    if NUMPY_WAS_PRELOADED:
        raise ValueError(
            "NumPy was imported before the training-worker thread contract"
        )
    if sys.flags.hash_randomization != 0:
        raise ValueError(
            "training worker must start with PYTHONHASHSEED=0"
        )
    plan, profile = _verify_plan(args.plan)
    output_dir = _resolve(args.output_dir)
    expected = _command_record(
        plan=_resolve(args.plan),
        candidate_id=args.candidate,
        seed=args.seed,
        robustness=bool(args.robustness),
        output_dir=output_dir,
    )
    if args.robustness:
        selection = _verify_selection(
            Path(str(_mapping(plan.get("outputs"), "outputs")["selection"]))
        )[0]
        if selection.get("selectedCandidateId") != args.candidate:
            raise ValueError("robustness worker is not the selected recipe")
        frozen = _mapping(
            selection.get("robustnessCommand"), "robustness command"
        )
    else:
        frozen = _mapping(
            _mapping(plan.get("commands"), "commands").get(args.candidate),
            "candidate command",
        )
    _expect(frozen, expected, "worker exact frozen command")
    identities = _mapping(plan.get("identities"), "plan identities")
    corpus = Path(str(identities["corpus"]["path"]))
    whole_features = _load_feature_corpus(corpus)
    local_audit = _phase_incidence_audit(whole_features)
    if local_audit.get("passed") is not True:
        raise ValueError("local phase-incidence audit failed")
    _verify_external_phase_audit(
        corpus, Path(str(identities["phaseIncidenceAudit"]["path"]))
    )
    dataset = _load_train_validation(corpus)
    initializer = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    result = _train_candidate(
        candidate_id=args.candidate,
        dataset=dataset,
        whole_features=whole_features,
        initializer=initializer,
        seed=args.seed,
        quiet=bool(args.quiet),
    )
    return _write_candidate_artifacts(
        plan_path=_resolve(args.plan),
        plan=plan,
        profile=profile,
        candidate_id=args.candidate,
        seed=args.seed,
        robustness=bool(args.robustness),
        output_dir=output_dir,
        result=result,
        dataset=dataset,
        whole_features=whole_features,
    )


def _verify_primary_failure(
    *,
    plan_path: Path,
    plan: Mapping[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any], Path]:
    command = _mapping(
        _mapping(plan.get("commands"), "plan commands").get(candidate_id),
        f"{candidate_id} command",
    )
    failure_path = _resolve(Path(str(command["failure"])))
    bundle = _resolve(Path(str(command["bundle"])))
    artifact_paths = [
        _resolve(Path(str(command[key])))
        for key in (
            "network",
            "canonicalFloat",
            "optimizerShadow",
            "manifest",
        )
    ]
    if not failure_path.is_file():
        raise ValueError(
            f"{candidate_id} failure seal is absent or not a file"
        )
    if bundle.exists() or any(path.exists() for path in artifact_paths):
        raise ValueError(
            f"{candidate_id} failure seal conflicts with candidate outputs"
        )
    failure = _load_json(
        failure_path, f"{candidate_id} fatal failure"
    )
    _expect(
        set(failure),
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "candidateId",
            "plan",
            "command",
            "exitCode",
            "heldOutTargetFieldsDecodedByOrchestrator",
        },
        f"{candidate_id} failure fields",
    )
    _expect(
        failure.get("schemaVersion"),
        SCHEMA_VERSION,
        f"{candidate_id} failure schema",
    )
    _expect(
        failure.get("kind"),
        FAILURE_KIND,
        f"{candidate_id} failure kind",
    )
    _expect(
        failure.get("candidateId"),
        candidate_id,
        f"{candidate_id} failure candidate",
    )
    _expect(
        failure.get("command"),
        command,
        f"{candidate_id} failure exact command",
    )
    if not _same_identity(failure.get("plan"), _identity(plan_path)):
        raise ValueError(f"{candidate_id} failure pins wrong plan")
    created_utc = failure.get("createdUtc")
    try:
        if (
            not isinstance(created_utc, str)
            or not created_utc.endswith("Z")
            or "T" not in created_utc
        ):
            raise ValueError
        parsed_created_utc = datetime.fromisoformat(
            created_utc[:-1] + "+00:00"
        )
        if (
            parsed_created_utc.tzinfo is None
            or parsed_created_utc.utcoffset()
            != timezone.utc.utcoffset(parsed_created_utc)
        ):
            raise ValueError
    except ValueError as error:
        raise ValueError(
            f"{candidate_id} failure createdUtc is invalid"
        ) from error
    exit_code = failure.get("exitCode")
    if type(exit_code) is not int or exit_code == 0:
        raise ValueError(
            f"{candidate_id} failure exit code is not a nonzero integer"
        )
    _expect(
        failure.get("heldOutTargetFieldsDecodedByOrchestrator"),
        0,
        f"{candidate_id} failure held-out target reads",
    )
    return failure, failure_path


def _run_frozen(args: argparse.Namespace) -> None:
    plan, _profile = _verify_plan(args.plan)
    commands = _mapping(plan.get("commands"), "plan commands")
    candidates = (
        CANDIDATES if args.candidate == "all" else (args.candidate,)
    )
    for candidate_id in candidates:
        command = _mapping(commands[candidate_id], f"{candidate_id} command")
        bundle = Path(str(command["bundle"])).resolve()
        paths = [
            Path(str(command[key]))
            for key in (
                "network",
                "canonicalFloat",
                "optimizerShadow",
                "manifest",
                "failure",
            )
        ]
        failure_path = Path(str(command["failure"]))
        if failure_path.exists():
            _verify_primary_failure(
                plan_path=_resolve(args.plan),
                plan=plan,
                candidate_id=candidate_id,
            )
            print(
                f"{candidate_id} already has a verified terminal failure "
                "seal; resuming with the next candidate",
                flush=True,
            )
            continue
        if bundle.exists():
            if failure_path.exists():
                raise ValueError(
                    f"{candidate_id} has both a committed bundle and a "
                    "failure seal"
                )
            _verify_candidate(plan, candidate_id)
            print(
                f"{candidate_id} already has a complete verified bundle; "
                "resuming with the next candidate",
                flush=True,
            )
            continue
        if any(path.exists() for path in paths):
            raise ValueError(
                f"{candidate_id} already has an output or failure artifact"
            )
        completed = subprocess.run(
            [str(value) for value in command["argv"]],
            cwd=REPO,
            env=_worker_environment(),
            check=False,
        )
        if completed.returncode == 0:
            _verify_candidate(plan, candidate_id)
            continue
        try:
            # The worker may have been interrupted after the atomic directory
            # commit but before returning success.  A complete verified bundle
            # is success evidence and must not be paired with a failure seal.
            _verify_candidate(plan, candidate_id)
        except Exception as verification_error:
            if bundle.exists():
                raise RuntimeError(
                    f"{candidate_id} worker failed with an invalid committed "
                    "bundle; preserving it as integrity evidence"
                ) from verification_error
            failure = {
                "schemaVersion": SCHEMA_VERSION,
                "kind": FAILURE_KIND,
                "createdUtc": _utc_now(),
                "candidateId": candidate_id,
                "plan": _identity(args.plan),
                "command": command,
                "exitCode": completed.returncode,
                "heldOutTargetFieldsDecodedByOrchestrator": 0,
            }
            _exclusive_json(Path(str(command["failure"])), failure)
        else:
            print(
                f"{candidate_id} committed a complete bundle before worker "
                "interruption; verified and retained",
                flush=True,
            )


def _verify_candidate(
    plan: Mapping[str, Any],
    candidate_id: str,
    *,
    robustness: bool = False,
) -> tuple[dict[str, Any], dict[str, Path]]:
    if robustness:
        selection_path = Path(
            str(_mapping(plan.get("outputs"), "outputs")["selection"])
        )
        selection = _load_json(selection_path, "selection")
        command = _mapping(
            selection.get("robustnessCommand"), "robustness command"
        )
    else:
        command = _mapping(
            _mapping(plan.get("commands"), "commands").get(candidate_id),
            f"{candidate_id} command",
        )
    paths = {
        key: _resolve(Path(str(command[key])))
        for key in (
            "network",
            "canonicalFloat",
            "optimizerShadow",
            "manifest",
            "failure",
        )
    }
    artifact_keys = (
        "network",
        "canonicalFloat",
        "optimizerShadow",
        "manifest",
    )
    bundle_parents = {paths[key].parent for key in artifact_keys}
    if len(bundle_parents) != 1:
        raise ValueError(f"{candidate_id} artifacts do not share one bundle")
    bundle = next(iter(bundle_parents))
    expected_bundle = _resolve(
        _candidate_bundle_dir(
            candidate_id,
            robustness=robustness,
            output_dir=bundle.parent,
        )
    )
    declared_bundle = _resolve(Path(str(command.get("bundle", ""))))
    if (
        bundle != expected_bundle
        or declared_bundle != expected_bundle
        or not bundle.is_dir()
    ):
        raise ValueError(f"{candidate_id} has no canonical committed bundle")
    expected_files = {paths[key] for key in artifact_keys}
    actual_entries = {_resolve(path) for path in bundle.iterdir()}
    if actual_entries != expected_files or any(
        not path.is_file() for path in bundle.iterdir()
    ):
        raise ValueError(f"{candidate_id} committed bundle inventory changed")
    if paths["failure"].exists():
        raise ValueError(f"{candidate_id} has a fatal training record")
    manifest = _load_json(paths["manifest"], f"{candidate_id} manifest")
    _expect(
        manifest.get("schemaVersion"),
        SCHEMA_VERSION,
        "candidate manifest schema",
    )
    _expect(manifest.get("kind"), CANDIDATE_KIND, "candidate manifest kind")
    _expect(manifest.get("profileId"), PROFILE_ID, "candidate profile")
    _expect(manifest.get("candidateId"), candidate_id, "candidate manifest id")
    _expect(manifest.get("robustness"), robustness, "candidate robustness flag")
    _expect(
        manifest.get("seed"),
        ROBUSTNESS_SEED if robustness else PRIMARY_SEED,
        "candidate seed",
    )
    _expect(
        manifest.get("createdUtc"),
        plan.get("createdUtc"),
        "candidate deterministic creation identity",
    )
    manifest_plan = _verify_identity(
        manifest.get("plan"), "candidate manifest plan"
    )
    if manifest_plan != _resolve(PLAN_PATH):
        raise ValueError("candidate manifest pins the wrong plan")
    _expect(manifest.get("command"), command, "candidate exact command")
    _expect(
        manifest.get("initializer"),
        _mapping(plan.get("identities"), "identities")["initializer"],
        "candidate initializer",
    )
    _expect(
        manifest.get("initializerManifest"),
        _mapping(plan.get("identities"), "identities")[
            "initializerManifest"
        ],
        "candidate initializer manifest",
    )
    _expect(
        manifest.get("deploymentFloatDerivedFromExportedQuantizedNetwork"),
        True,
        "candidate canonical deployment float",
    )
    _expect(
        manifest.get("deploymentFloatRequantizesByteIdentically"),
        True,
        "candidate deployment requantization",
    )
    _expect(
        manifest.get("eligibleEpochs"),
        list(range(FIRST_QAT_EPOCH, EPOCHS + 1)),
        "candidate eligible epochs",
    )
    _expect(
        manifest.get("checkpointTiePolicy"),
        "exact ties retain earliest eligible epoch",
        "candidate checkpoint tie",
    )
    history = [
        _mapping(row, "candidate history row")
        for row in _sequence(manifest.get("history"), "candidate history")
    ]
    _expect(
        [row.get("epoch") for row in history],
        list(range(1, EPOCHS + 1)),
        "candidate epoch history",
    )
    for epoch, row in enumerate(history, 1):
        _expect(
            row.get("quantizationAware"),
            epoch >= FIRST_QAT_EPOCH,
            f"candidate epoch {epoch} QAT",
        )
        _expect(
            row.get("eligibleCheckpoint"),
            epoch >= FIRST_QAT_EPOCH,
            f"candidate epoch {epoch} eligibility",
        )
    selected_epoch = _select_checkpoint_epoch(
        [
            (
                int(row["epoch"]),
                _number(
                    _mapping(
                        row.get("commonValidation"),
                        "candidate epoch validation",
                    ).get("huberLoss"),
                    "candidate epoch validation loss",
                ),
            )
            for row in history
        ]
    )
    _expect(
        manifest.get("selectedEpoch"),
        selected_epoch,
        "candidate selected epoch",
    )
    for key, manifest_key in (
        ("network", "network"),
        ("canonicalFloat", "canonicalDeploymentFloat"),
        ("optimizerShadow", "optimizerShadow"),
    ):
        if not _same_identity(
            manifest.get(manifest_key), _identity(paths[key])
        ):
            raise ValueError(f"{candidate_id} manifest has a stale {key}")
    optimizer_shadow = base.FloatNetwork.read_checkpoint(
        paths["optimizerShadow"]
    )
    if optimizer_shadow.ft_weights.shape != (
        KING_STATE_FEATURE_COUNT,
        ACCUMULATOR_SIZE,
    ) or not _model_is_finite(optimizer_shadow):
        raise ValueError(f"{candidate_id} optimizer shadow is invalid")
    boundary = _mapping(
        manifest.get("informationBoundary"), "candidate boundary"
    )
    _expect(
        boundary.get("heldOutTargetFieldsDecoded"),
        0,
        "candidate held-out target reads",
    )
    _expect(
        boundary.get("heldOutMetricsComputed"),
        False,
        "candidate held-out metrics",
    )
    return manifest, paths


def _relative_improvement(candidate: float, baseline: float) -> float:
    if not math.isfinite(candidate) or not math.isfinite(baseline):
        raise ValueError("selection metric is nonfinite")
    if baseline <= 0.0:
        raise ValueError("selection baseline loss must be positive")
    return (baseline - candidate) / baseline


def _selection_snapshot(
    *,
    plan_path: Path,
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute the entire validation-only selection decision.

    The feature-only view sees all rows for runtime/parity checks.  The
    target-bearing view routes split 2 away before calling ``json.loads``.
    """

    identities = _mapping(plan.get("identities"), "plan identities")
    corpus = Path(str(identities["corpus"]["path"]))
    features = _load_feature_corpus(corpus)
    local_audit = _phase_incidence_audit(features)
    if local_audit.get("passed") is not True:
        raise ValueError("local phase-incidence audit failed at selection")
    _verify_external_phase_audit(
        corpus, Path(str(identities["phaseIncidenceAudit"]["path"]))
    )
    dataset = _load_train_validation(corpus)
    initializer = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    i0_metrics = _common_metrics(
        dataset,
        1,
        _predict_quantized(
            initializer,
            dataset.features,
            indices=dataset.indices(1),
        ),
    )
    zero_metrics = _common_metrics(
        dataset,
        1,
        np.zeros(dataset.features.count, dtype=np.int32),
    )
    baselines = {
        "I0": _public_metrics(i0_metrics),
        "zeroResidual": _public_metrics(zero_metrics),
    }
    commands = _mapping(plan.get("commands"), "plan commands")
    records: list[dict[str, Any]] = []
    eligible_records: list[dict[str, Any]] = []
    for candidate_id in CANDIDATES:
        command = _mapping(
            commands.get(candidate_id), f"{candidate_id} command"
        )
        failure_path = Path(str(command["failure"]))
        if failure_path.exists():
            _failure, verified_failure_path = _verify_primary_failure(
                plan_path=plan_path,
                plan=plan,
                candidate_id=candidate_id,
            )
            record = {
                "candidateId": candidate_id,
                "status": "fatal-training-failure",
                "eligible": False,
                "failure": _identity(verified_failure_path),
                "gates": {"fatalTrainingFailure": False},
            }
            records.append(record)
            continue

        manifest, paths = _verify_candidate(plan, candidate_id)
        _expect(
            manifest.get("informationBoundary"),
            _target_information_boundary(dataset),
            f"{candidate_id} information boundary",
        )
        health, _whole_predictions = _runtime_health(
            network_path=paths["network"],
            canonical_float_path=paths["canonicalFloat"],
            initializer=initializer,
            features=features,
            cpp_evaluator=Path(str(identities["cppEvaluator"]["path"])),
            profile=profile,
        )
        network = QuantizedNetwork.read(paths["network"])
        metrics = _common_metrics(
            dataset,
            1,
            _predict_quantized(
                network,
                dataset.features,
                indices=dataset.indices(1),
            ),
        )
        public_metrics = _public_metrics(metrics)
        _expect(
            manifest.get("commonValidation"),
            public_metrics,
            f"{candidate_id} recomputed validation",
        )
        _expect(
            manifest.get("i0Validation"),
            baselines["I0"],
            f"{candidate_id} recomputed I0",
        )
        _expect(
            manifest.get("zeroResidualValidation"),
            baselines["zeroResidual"],
            f"{candidate_id} recomputed zero residual",
        )
        _expect(
            manifest.get("health"),
            health,
            f"{candidate_id} recomputed health",
        )
        candidate_loss = float(metrics["huberLoss"])
        i0_loss = float(i0_metrics["huberLoss"])
        zero_loss = float(zero_metrics["huberLoss"])
        relative_i0 = _relative_improvement(candidate_loss, i0_loss)
        phase_regressions = {
            phase: (
                float(metrics["phase"][phase]["cpMae"])
                - float(i0_metrics["phase"][phase]["cpMae"])
            )
            for phase in PHASES
        }
        max_phase_regression = max(phase_regressions.values())
        gates = {
            "minimumRelativeHuberImprovementOverI0": (
                relative_i0 >= 0.005
            ),
            "lowerPhaseMacroCpMaeVersusI0": (
                float(metrics["cpMae"]) < float(i0_metrics["cpMae"])
            ),
            "lowerHuberLossThanZeroResidual": candidate_loss < zero_loss,
            "maximumAnyPhaseCpMaeRegressionVersusI0": (
                max_phase_regression <= 5.0
            ),
            "deploymentHealth": health.get("passed") is True,
        }
        record = {
            "candidateId": candidate_id,
            "status": "completed",
            "manifest": _identity(paths["manifest"]),
            "network": _identity(paths["network"]),
            "canonicalDeploymentFloat": _identity(
                paths["canonicalFloat"]
            ),
            "selectedEpoch": int(manifest["selectedEpoch"]),
            "commonValidation": public_metrics,
            "relativeHuberImprovementOverI0": relative_i0,
            "phaseCpMaeRegressionVersusI0": phase_regressions,
            "maximumAnyPhaseCpMaeRegressionVersusI0": (
                max_phase_regression
            ),
            "health": health,
            "gates": gates,
            "eligible": all(gates.values()),
        }
        records.append(record)
        if record["eligible"]:
            eligible_records.append(record)

    winner: dict[str, Any] | None = None
    tied_ids: list[str] = []
    if eligible_records:
        best_loss = min(
            float(record["commonValidation"]["huberLoss"])
            for record in eligible_records
        )
        tied = []
        for record in eligible_records:
            loss = float(record["commonValidation"]["huberLoss"])
            denominator = min(loss, best_loss)
            relative = (
                0.0
                if loss == best_loss
                else (
                    math.inf
                    if denominator <= 0.0
                    else abs(loss - best_loss) / denominator
                )
            )
            if relative <= 0.0025:
                tied.append(record)
        tied_ids = [
            candidate_id
            for candidate_id in TIE_PRIORITY
            if any(
                record["candidateId"] == candidate_id for record in tied
            )
        ]
        selected_id = tied_ids[0]
        winner = next(
            record
            for record in eligible_records
            if record["candidateId"] == selected_id
        )
    return {
        "informationBoundary": _target_information_boundary(dataset),
        "baselines": baselines,
        "candidates": records,
        "decision": {
            "eligibleCandidates": [
                str(record["candidateId"]) for record in eligible_records
            ],
            "lowestLossTieCandidatesInPriorityOrder": tied_ids,
            "tieRelativeLoss": 0.0025,
            "tiePriority": list(TIE_PRIORITY),
            "selectedCandidateId": (
                None if winner is None else winner["candidateId"]
            ),
            "selectedValidationHuberLoss": (
                None
                if winner is None
                else winner["commonValidation"]["huberLoss"]
            ),
        },
    }


def _require_robustness_outputs_absent(
    *,
    plan_path: Path,
    output_dir: Path,
    candidate_ids: Iterable[str],
) -> None:
    for candidate_id in candidate_ids:
        command = _command_record(
            plan=plan_path,
            candidate_id=candidate_id,
            seed=ROBUSTNESS_SEED,
            robustness=True,
            output_dir=output_dir,
        )
        paths = {
            "bundle": Path(str(command["bundle"])),
            **{
                key: Path(str(command[key]))
                for key in (
                    "network",
                    "canonicalFloat",
                    "optimizerShadow",
                    "manifest",
                    "failure",
                )
            },
        }
        existing = [
            f"{name}={path}"
            for name, path in paths.items()
            if path.exists()
        ]
        if existing:
            raise ValueError(
                f"{candidate_id} has an unexpected robustness output: "
                + ", ".join(existing)
            )


def _select(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = _resolve(args.plan)
    plan, profile = _verify_plan(plan_path)
    selection_path = Path(
        str(_mapping(plan.get("outputs"), "outputs")["selection"])
    )
    if selection_path.exists():
        raise FileExistsError(selection_path)
    _require_robustness_outputs_absent(
        plan_path=plan_path,
        output_dir=selection_path.parent,
        candidate_ids=CANDIDATES,
    )
    snapshot = _selection_snapshot(
        plan_path=plan_path, plan=plan, profile=profile
    )
    decision = _mapping(snapshot.get("decision"), "selection decision")
    selected_id = decision.get("selectedCandidateId")
    if selected_id not in CANDIDATES:
        raise ValueError(
            "no generation-3 candidate passed every validation gate"
        )
    selected_manifest, selected_paths = _verify_candidate(
        plan, str(selected_id)
    )
    robustness_command = _command_record(
        plan=plan_path,
        candidate_id=str(selected_id),
        seed=ROBUSTNESS_SEED,
        robustness=True,
        output_dir=selection_path.parent,
    )
    selection = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SELECTION_KIND,
        "createdUtc": _utc_now(),
        "profileId": PROFILE_ID,
        "plan": _identity(plan_path),
        **snapshot,
        "selectedCandidateId": selected_id,
        "selectedNetwork": _identity(selected_paths["network"]),
        "selectedManifest": _identity(selected_paths["manifest"]),
        "selectedCanonicalDeploymentFloat": _identity(
            selected_paths["canonicalFloat"]
        ),
        "selectedEpoch": int(selected_manifest["selectedEpoch"]),
        "robustnessCommand": robustness_command,
        "outputs": {
            "selection": str(selection_path),
            "robustness": str(
                _mapping(plan.get("outputs"), "outputs")["robustness"]
            ),
        },
    }
    _exclusive_json(selection_path, selection)
    return selection


def _verify_selection(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    selection_path = _resolve(path)
    selection = _load_json(selection_path, "generation-3 selection")
    _expect(
        set(selection),
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "profileId",
            "plan",
            "informationBoundary",
            "baselines",
            "candidates",
            "decision",
            "selectedCandidateId",
            "selectedNetwork",
            "selectedManifest",
            "selectedCanonicalDeploymentFloat",
            "selectedEpoch",
            "robustnessCommand",
            "outputs",
        },
        "selection top-level fields",
    )
    _expect(selection.get("schemaVersion"), SCHEMA_VERSION, "selection schema")
    _expect(selection.get("kind"), SELECTION_KIND, "selection kind")
    _expect(selection.get("profileId"), PROFILE_ID, "selection profile")
    try:
        datetime.fromisoformat(
            str(selection.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("selection createdUtc is invalid") from error
    plan_path = _verify_identity(selection.get("plan"), "selection plan")
    plan, profile = _verify_plan(plan_path)
    expected_path = Path(
        str(_mapping(plan.get("outputs"), "outputs")["selection"])
    ).resolve()
    _expect(selection_path, expected_path, "selection canonical path")
    snapshot = _selection_snapshot(
        plan_path=plan_path, plan=plan, profile=profile
    )
    for key in (
        "informationBoundary",
        "baselines",
        "candidates",
        "decision",
    ):
        _expect(selection.get(key), snapshot[key], f"selection {key}")
    selected_id = _mapping(
        snapshot.get("decision"), "selection decision"
    ).get("selectedCandidateId")
    _expect(
        selection.get("selectedCandidateId"),
        selected_id,
        "selected candidate",
    )
    if selected_id not in CANDIDATES:
        raise ValueError("selection seal has no eligible winner")
    _require_robustness_outputs_absent(
        plan_path=plan_path,
        output_dir=selection_path.parent,
        candidate_ids=(
            candidate_id
            for candidate_id in CANDIDATES
            if candidate_id != selected_id
        ),
    )
    manifest, paths = _verify_candidate(plan, str(selected_id))
    for key, actual in (
        ("selectedNetwork", paths["network"]),
        ("selectedManifest", paths["manifest"]),
        ("selectedCanonicalDeploymentFloat", paths["canonicalFloat"]),
    ):
        if not _same_identity(selection.get(key), _identity(actual)):
            raise ValueError(f"selection {key} identity changed")
    _expect(
        selection.get("selectedEpoch"),
        int(manifest["selectedEpoch"]),
        "selection epoch",
    )
    command = _command_record(
        plan=plan_path,
        candidate_id=str(selected_id),
        seed=ROBUSTNESS_SEED,
        robustness=True,
        output_dir=selection_path.parent,
    )
    _expect(
        selection.get("robustnessCommand"),
        command,
        "selection robustness command",
    )
    _expect(
        selection.get("outputs"),
        _mapping(plan.get("outputs"), "plan outputs"),
        "selection outputs",
    )
    return selection, plan, profile


def _run_robustness(args: argparse.Namespace) -> dict[str, Any]:
    selection, plan, profile = _verify_selection(args.selection)
    selection_path = _resolve(args.selection)
    outputs = _mapping(plan.get("outputs"), "plan outputs")
    robustness_path = Path(str(outputs["robustness"])).resolve()
    if robustness_path.exists():
        raise FileExistsError(robustness_path)
    command = _mapping(
        selection.get("robustnessCommand"), "robustness command"
    )
    candidate_id = str(selection["selectedCandidateId"])
    bundle = Path(str(command["bundle"])).resolve()
    paths = [
        Path(str(command[key]))
        for key in (
            "network",
            "canonicalFloat",
            "optimizerShadow",
            "manifest",
            "failure",
        )
    ]
    if bundle.exists():
        if Path(str(command["failure"])).exists():
            raise ValueError(
                "robustness candidate has both a committed bundle and a "
                "failure seal"
            )
        _verify_candidate(plan, candidate_id, robustness=True)
        print(
            "resuming robustness finalization from a complete verified "
            "candidate bundle",
            flush=True,
        )
    else:
        if any(path.exists() for path in paths):
            raise ValueError("a robustness output or failure already exists")
        completed = subprocess.run(
            [str(value) for value in command["argv"]],
            cwd=REPO,
            env=_worker_environment(),
            check=False,
        )
        if completed.returncode != 0:
            try:
                _verify_candidate(plan, candidate_id, robustness=True)
            except Exception as verification_error:
                if bundle.exists():
                    raise RuntimeError(
                        "robustness worker failed with an invalid committed "
                        "bundle; preserving it as integrity evidence"
                    ) from verification_error
                failure = {
                    "schemaVersion": SCHEMA_VERSION,
                    "kind": FAILURE_KIND,
                    "createdUtc": _utc_now(),
                    "candidateId": selection["selectedCandidateId"],
                    "plan": selection["plan"],
                    "selection": _identity(selection_path),
                    "command": command,
                    "exitCode": completed.returncode,
                    "heldOutTargetFieldsDecodedByOrchestrator": 0,
                }
                _exclusive_json(Path(str(command["failure"])), failure)
                raise RuntimeError(
                    "selected-recipe robustness training failed"
                ) from verification_error
            else:
                print(
                    "robustness worker committed a complete bundle before "
                    "interruption; verified and retained",
                    flush=True,
                )
    manifest, candidate_paths = _verify_candidate(
        plan, candidate_id, robustness=True
    )
    identities = _mapping(plan.get("identities"), "plan identities")
    corpus = Path(str(identities["corpus"]["path"]))
    features = _load_feature_corpus(corpus)
    dataset = _load_train_validation(corpus)
    _expect(
        manifest.get("informationBoundary"),
        _target_information_boundary(dataset),
        "robustness candidate information boundary",
    )
    initializer = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    health, _predictions = _runtime_health(
        network_path=candidate_paths["network"],
        canonical_float_path=candidate_paths["canonicalFloat"],
        initializer=initializer,
        features=features,
        cpp_evaluator=Path(str(identities["cppEvaluator"]["path"])),
        profile=profile,
    )
    metrics = _common_metrics(
        dataset,
        1,
        _predict_quantized(
            QuantizedNetwork.read(candidate_paths["network"]),
            dataset.features,
            indices=dataset.indices(1),
        ),
    )
    i0 = _common_metrics(
        dataset,
        1,
        _predict_quantized(
            initializer,
            dataset.features,
            indices=dataset.indices(1),
        ),
    )
    zero = _common_metrics(
        dataset,
        1,
        np.zeros(dataset.features.count, dtype=np.int32),
    )
    _expect(
        manifest.get("commonValidation"),
        _public_metrics(metrics),
        "robustness recomputed validation",
    )
    _expect(manifest.get("health"), health, "robustness recomputed health")
    gates = {
        "deploymentHealth": health.get("passed") is True,
        "lowerCommonHuberThanI0": (
            float(metrics["huberLoss"]) < float(i0["huberLoss"])
        ),
        "lowerCommonHuberThanZeroResidual": (
            float(metrics["huberLoss"]) < float(zero["huberLoss"])
        ),
    }
    robustness = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ROBUSTNESS_KIND,
        "createdUtc": _utc_now(),
        "profileId": PROFILE_ID,
        "plan": selection["plan"],
        "selection": _identity(selection_path),
        "selectedPrimaryNetwork": selection["selectedNetwork"],
        "candidateId": candidate_id,
        "seed": ROBUSTNESS_SEED,
        "robustnessNetwork": _identity(candidate_paths["network"]),
        "robustnessManifest": _identity(candidate_paths["manifest"]),
        "commonValidation": _public_metrics(metrics),
        "baselines": {
            "I0": _public_metrics(i0),
            "zeroResidual": _public_metrics(zero),
        },
        "health": health,
        "gates": gates,
        "passed": all(gates.values()),
        "mayReplacePrimary": False,
        "informationBoundary": _target_information_boundary(dataset),
    }
    _exclusive_json(robustness_path, robustness)
    if robustness["passed"] is not True:
        raise ValueError(
            "selected recipe failed the frozen robustness direction/health gate"
        )
    return robustness


def _verify_robustness(
    path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    robustness_path = _resolve(path)
    robustness = _load_json(robustness_path, "generation-3 robustness")
    _expect(
        set(robustness),
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "profileId",
            "plan",
            "selection",
            "selectedPrimaryNetwork",
            "candidateId",
            "seed",
            "robustnessNetwork",
            "robustnessManifest",
            "commonValidation",
            "baselines",
            "health",
            "gates",
            "passed",
            "mayReplacePrimary",
            "informationBoundary",
        },
        "robustness top-level fields",
    )
    _expect(
        robustness.get("schemaVersion"),
        SCHEMA_VERSION,
        "robustness schema",
    )
    _expect(robustness.get("kind"), ROBUSTNESS_KIND, "robustness kind")
    _expect(
        robustness.get("profileId"), PROFILE_ID, "robustness profile"
    )
    try:
        datetime.fromisoformat(
            str(robustness.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("robustness createdUtc is invalid") from error
    selection_path = _verify_identity(
        robustness.get("selection"), "robustness selection"
    )
    selection, plan, profile = _verify_selection(selection_path)
    expected_path = Path(
        str(_mapping(plan.get("outputs"), "outputs")["robustness"])
    ).resolve()
    _expect(robustness_path, expected_path, "robustness canonical path")
    _expect(robustness.get("plan"), selection["plan"], "robustness plan")
    _expect(
        robustness.get("selectedPrimaryNetwork"),
        selection["selectedNetwork"],
        "robustness immutable primary",
    )
    candidate_id = str(selection["selectedCandidateId"])
    _expect(
        robustness.get("candidateId"),
        candidate_id,
        "robustness candidate",
    )
    _expect(robustness.get("seed"), ROBUSTNESS_SEED, "robustness seed")
    manifest, candidate_paths = _verify_candidate(
        plan, candidate_id, robustness=True
    )
    for key, actual in (
        ("robustnessNetwork", candidate_paths["network"]),
        ("robustnessManifest", candidate_paths["manifest"]),
    ):
        if not _same_identity(robustness.get(key), _identity(actual)):
            raise ValueError(f"robustness {key} identity changed")
    identities = _mapping(plan.get("identities"), "plan identities")
    corpus = Path(str(identities["corpus"]["path"]))
    features = _load_feature_corpus(corpus)
    dataset = _load_train_validation(corpus)
    _expect(
        manifest.get("informationBoundary"),
        _target_information_boundary(dataset),
        "robustness candidate information boundary",
    )
    initializer = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    health, _whole_predictions = _runtime_health(
        network_path=candidate_paths["network"],
        canonical_float_path=candidate_paths["canonicalFloat"],
        initializer=initializer,
        features=features,
        cpp_evaluator=Path(str(identities["cppEvaluator"]["path"])),
        profile=profile,
    )
    metrics = _common_metrics(
        dataset,
        1,
        _predict_quantized(
            QuantizedNetwork.read(candidate_paths["network"]),
            dataset.features,
            indices=dataset.indices(1),
        ),
    )
    i0 = _common_metrics(
        dataset,
        1,
        _predict_quantized(
            initializer,
            dataset.features,
            indices=dataset.indices(1),
        ),
    )
    zero = _common_metrics(
        dataset,
        1,
        np.zeros(dataset.features.count, dtype=np.int32),
    )
    _expect(
        manifest.get("commonValidation"),
        _public_metrics(metrics),
        "robustness candidate validation",
    )
    _expect(manifest.get("health"), health, "robustness candidate health")
    gates = {
        "deploymentHealth": health.get("passed") is True,
        "lowerCommonHuberThanI0": (
            float(metrics["huberLoss"]) < float(i0["huberLoss"])
        ),
        "lowerCommonHuberThanZeroResidual": (
            float(metrics["huberLoss"]) < float(zero["huberLoss"])
        ),
    }
    _expect(
        robustness.get("commonValidation"),
        _public_metrics(metrics),
        "robustness validation",
    )
    _expect(
        robustness.get("baselines"),
        {
            "I0": _public_metrics(i0),
            "zeroResidual": _public_metrics(zero),
        },
        "robustness baselines",
    )
    _expect(robustness.get("health"), health, "robustness health")
    _expect(robustness.get("gates"), gates, "robustness gates")
    _expect(
        robustness.get("passed"),
        all(gates.values()),
        "robustness pass decision",
    )
    _expect(
        robustness.get("mayReplacePrimary"),
        False,
        "robustness replacement policy",
    )
    _expect(
        robustness.get("informationBoundary"),
        _target_information_boundary(dataset),
        "robustness information boundary",
    )
    return robustness, selection, plan, profile


def _self_test_group(split: int, prefix: str) -> str:
    for ordinal in range(1_000_000):
        group = f"{prefix}-{ordinal}"
        if deterministic_split(
            group, SPLIT_SEED, TRAIN_PERCENT, VALIDATION_PERCENT
        ) == split:
            return group
    raise AssertionError(f"could not synthesize a split-{split} group")


def _self_test_ordered_amendment_chain() -> None:
    ordered = [
        _identity(AMENDMENT),
        _identity(AMENDMENT_002),
        _identity(AMENDMENT_003),
    ]
    _require_ordered_amendment_chain(
        {"orderedAmendments": ordered},
        label="self-test pre-label seal",
    )
    for malformed in (
        list(reversed(ordered)),
        ordered[:-1],
        [ordered[0], ordered[1], ordered[1], ordered[2]],
    ):
        try:
            _require_ordered_amendment_chain(
                {"orderedAmendments": malformed},
                label="self-test malformed pre-label seal",
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "pre-label envelope accepted a malformed ordered "
                "amendment chain"
            )


def _self_test_feature_corpus(
    *,
    rows: int,
    phases: Sequence[str],
    groups: Sequence[str],
    splits: Sequence[int],
    white_features: np.ndarray,
    black_features: np.ndarray,
) -> FeatureCorpus:
    return FeatureCorpus(
        ofens=tuple(f"synthetic-{index}" for index in range(rows)),
        groups=tuple(groups),
        phases=tuple(phases),
        splits=np.asarray(splits, dtype=np.int8),
        white_features=np.asarray(white_features, dtype=np.uint16),
        black_features=np.asarray(black_features, dtype=np.uint16),
        side_to_move_white=np.asarray(
            [index % 2 == 0 for index in range(rows)], dtype=np.bool_
        ),
        input_signatures=tuple(
            f"synthetic-signature-{index}" for index in range(rows)
        ),
    )


def _self_test_routing(temp: Path) -> None:
    groups = {
        split: _self_test_group(split, "gen3-routing")
        for split in range(3)
    }
    rows = []
    for split in range(3):
        rows.append(
            {
                "groupId": groups[split],
                "ofen": base.INITIAL_OFEN,
                "phase": "opening",
                "targetSemantics": "search-minus-handcrafted",
                "targetCpStm": 1,
                "searchTargetCpStm": 3,
                "handcraftedCpStm": 2,
                "routingMarker": (
                    "SPLIT2_POISON" if split == 2 else f"split-{split}"
                ),
            }
        )
    path = temp / "routing.jsonl"
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    original_loads = json.loads

    def guarded_loads(value: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(value, str) and "SPLIT2_POISON" in value:
            raise AssertionError("held-out row reached json.loads")
        return original_loads(value, *args, **kwargs)

    json.loads = guarded_loads
    try:
        dataset = _load_train_validation(path)
    finally:
        json.loads = original_loads
    if dataset.source_rows != 3 or dataset.withheld_rows != 1:
        raise AssertionError("routing self-test row counts changed")
    if dataset.features.count != 2:
        raise AssertionError("held-out row entered target-bearing dataset")

    bad_rows = [dict(row) for row in rows]
    bad_rows[0]["phase"] = "late"
    mismatch = temp / "phase-mismatch.jsonl"
    mismatch.write_text(
        "".join(
            json.dumps(row, sort_keys=True) + "\n" for row in bad_rows
        ),
        encoding="utf-8",
    )
    try:
        _load_train_validation(mismatch)
    except ValueError as error:
        if "does not match OFEN-derived phase" not in str(error):
            raise
    else:
        raise AssertionError("stored/derived phase mismatch was accepted")


def _self_test_weights_and_occurrences() -> None:
    pad = KING_STATE_FEATURE_COUNT
    occurrence_features = _self_test_feature_corpus(
        rows=4,
        phases=("opening",) * 4,
        groups=("g",) * 4,
        splits=(0,) * 4,
        white_features=np.asarray([[5, pad]] * 4),
        black_features=np.asarray([[5, pad]] * 4),
    )
    occurrence_dataset = LabeledCorpus(
        features=occurrence_features,
        target_cp=np.zeros(4, dtype=np.float32),
        search_cp=np.zeros(4, dtype=np.float32),
        handcrafted_cp=np.zeros(4, dtype=np.float32),
        source_rows=4,
        withheld_rows=0,
    )
    occurrences = _conditioned_occurrences(occurrence_dataset)
    if occurrences[5] != 8 or int(occurrences.sum()) != 8:
        raise AssertionError("occurrences did not count both perspectives")

    phases: list[str] = []
    groups: list[str] = []
    for phase in PHASES:
        phases.extend((phase, phase, phase))
        groups.extend((f"{phase}-a", f"{phase}-b", f"{phase}-b"))
    rows = len(phases)
    weight_features = _self_test_feature_corpus(
        rows=rows,
        phases=phases,
        groups=groups,
        splits=(0,) * rows,
        white_features=np.full((rows, 1), pad, dtype=np.uint16),
        black_features=np.full((rows, 1), pad, dtype=np.uint16),
    )
    weight_dataset = LabeledCorpus(
        features=weight_features,
        target_cp=np.zeros(rows, dtype=np.float32),
        search_cp=np.zeros(rows, dtype=np.float32),
        handcrafted_cp=np.zeros(rows, dtype=np.float32),
        source_rows=rows,
        withheld_rows=0,
    )
    weights = _phase_group_weights(weight_dataset)
    if not np.allclose(
        weights.reshape(len(PHASES), 3),
        np.asarray([[1.5, 0.75, 0.75]] * len(PHASES)),
        rtol=0.0,
        atol=1e-7,
    ):
        raise AssertionError("phase/group weighting formula changed")
    if not math.isclose(float(weights.sum()), float(rows), abs_tol=1e-6):
        raise AssertionError("phase/group weights do not sum to N")


def _self_test_loss_gradient() -> None:
    prediction = np.asarray([-125.0, 38.0, 401.0], dtype=np.float32)
    target = np.asarray([-90.0, 75.0, 150.0], dtype=np.float32)
    search = np.asarray([-120.0, 100.0, 300.0], dtype=np.float32)
    handcrafted = np.asarray([-15.0, 22.0, 75.0], dtype=np.float32)
    weights = np.asarray([0.75, 1.5, 2.0], dtype=np.float32)
    for soft_wdl in (False, True):
        _loss, analytic, _parts = _loss_and_output_gradient(
            prediction=prediction,
            target_cp=target,
            search_cp=search,
            handcrafted_cp=handcrafted,
            row_weights=weights,
            soft_wdl=soft_wdl,
        )
        numeric = np.empty_like(prediction)
        epsilon = 0.01
        for index in range(prediction.size):
            left = prediction.copy()
            right = prediction.copy()
            left[index] -= epsilon
            right[index] += epsilon
            left_loss = _loss_and_output_gradient(
                prediction=left,
                target_cp=target,
                search_cp=search,
                handcrafted_cp=handcrafted,
                row_weights=weights,
                soft_wdl=soft_wdl,
            )[0]
            right_loss = _loss_and_output_gradient(
                prediction=right,
                target_cp=target,
                search_cp=search,
                handcrafted_cp=handcrafted,
                row_weights=weights,
                soft_wdl=soft_wdl,
            )[0]
            numeric[index] = (right_loss - left_loss) / (2.0 * epsilon)
        if not np.allclose(analytic, numeric, rtol=2e-3, atol=2e-6):
            raise AssertionError(
                f"{'soft-WDL' if soft_wdl else 'Huber'} gradient mismatch: "
                f"{analytic} vs {numeric}"
            )


def _self_test_atomic_candidate_bundles(temp: Path) -> None:
    output_dir = temp / "candidate-bundle-publication"
    output_dir.mkdir()

    def fixture(
        candidate_id: str,
    ) -> tuple[dict[str, Path], dict[str, bytes], dict[str, Any]]:
        paths = _candidate_paths(candidate_id, output_dir=output_dir)
        binary_payloads = {
            "network": f"{candidate_id}-network".encode("ascii"),
            "canonicalFloat": f"{candidate_id}-canonical".encode("ascii"),
            "optimizerShadow": f"{candidate_id}-optimizer".encode("ascii"),
        }
        manifest = {
            "candidateId": candidate_id,
            "network": _payload_identity(
                paths["network"], binary_payloads["network"]
            ),
            "canonicalDeploymentFloat": _payload_identity(
                paths["canonicalFloat"],
                binary_payloads["canonicalFloat"],
            ),
            "optimizerShadow": _payload_identity(
                paths["optimizerShadow"],
                binary_payloads["optimizerShadow"],
            ),
        }
        payloads = {
            **binary_payloads,
            "manifest": _canonical_json(manifest),
        }
        return paths, payloads, manifest

    def assert_complete(paths: Mapping[str, Path]) -> None:
        artifact_keys = (
            "network",
            "canonicalFloat",
            "optimizerShadow",
            "manifest",
        )
        bundle = paths["network"].parent
        if not bundle.is_dir():
            raise AssertionError("atomic candidate bundle was not committed")
        if {_resolve(path) for path in bundle.iterdir()} != {
            _resolve(paths[key]) for key in artifact_keys
        }:
            raise AssertionError("committed candidate bundle is partial")
        if any(not paths[key].is_file() for key in artifact_keys):
            raise AssertionError("committed candidate artifact is absent")

    precommit_boundaries = (
        "after-network-stage",
        "after-canonicalFloat-stage",
        "after-optimizerShadow-stage",
        "after-manifest-stage",
        "before-commit",
    )
    for ordinal, boundary in enumerate(precommit_boundaries):
        paths, payloads, manifest = fixture(f"interrupt-{ordinal}")

        def interrupt(
            observed: str,
            _staging: Path,
            _bundle: Path,
            *,
            wanted: str = boundary,
        ) -> None:
            if observed == wanted:
                raise RuntimeError(f"simulated interruption at {wanted}")

        try:
            _publish_candidate_bundle(
                paths=paths,
                payloads=payloads,
                manifest=manifest,
                interruption_hook=interrupt,
            )
        except RuntimeError as error:
            if boundary not in str(error):
                raise
        else:
            raise AssertionError(
                f"simulated interruption at {boundary} was ignored"
            )
        if paths["network"].parent.exists():
            raise AssertionError(
                f"{boundary} exposed a partial canonical bundle"
            )
        if any(output_dir.glob(".*-staging-*")):
            raise AssertionError(
                f"{boundary} left an invocation-owned staging directory"
            )

    committed_paths, committed_payloads, committed_manifest = fixture(
        "post-commit"
    )

    def interrupt_after_commit(
        observed: str, _staging: Path, _bundle: Path
    ) -> None:
        if observed == "after-commit":
            raise RuntimeError("simulated interruption after commit")

    try:
        _publish_candidate_bundle(
            paths=committed_paths,
            payloads=committed_payloads,
            manifest=committed_manifest,
            interruption_hook=interrupt_after_commit,
        )
    except RuntimeError as error:
        if "after commit" not in str(error):
            raise
    else:
        raise AssertionError("post-commit interruption was ignored")
    assert_complete(committed_paths)
    if _load_json(
        committed_paths["manifest"], "post-commit self-test manifest"
    ) != committed_manifest:
        raise AssertionError(
            "post-commit interruption changed the complete manifest"
        )

    corrupt_paths, corrupt_payloads, corrupt_manifest = fixture(
        "corrupt-staging"
    )

    def corrupt_staging(
        observed: str, staging: Path, _bundle: Path
    ) -> None:
        if observed == "after-manifest-stage":
            (staging / corrupt_paths["network"].name).write_bytes(
                b"corrupt"
            )

    try:
        _publish_candidate_bundle(
            paths=corrupt_paths,
            payloads=corrupt_payloads,
            manifest=corrupt_manifest,
            interruption_hook=corrupt_staging,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("corrupt staged candidate bytes were committed")
    if corrupt_paths["network"].parent.exists():
        raise AssertionError("corrupt staging exposed a canonical bundle")

    partial_paths, partial_payloads, partial_manifest = fixture(
        "preexisting-partial"
    )
    partial_bundle = partial_paths["network"].parent
    partial_bundle.mkdir()
    foreign = partial_bundle / "foreign-owner"
    foreign.write_bytes(b"foreign")
    try:
        _publish_candidate_bundle(
            paths=partial_paths,
            payloads=partial_payloads,
            manifest=partial_manifest,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("pre-existing partial bundle was overwritten")
    if foreign.read_bytes() != b"foreign" or set(
        partial_bundle.iterdir()
    ) != {foreign}:
        raise AssertionError("pre-existing partial bundle was modified")

    race_paths, race_payloads, race_manifest = fixture("destination-race")
    race_marker = race_paths["network"].parent / "foreign-race"

    def create_destination_race(
        observed: str, _staging: Path, bundle: Path
    ) -> None:
        if observed == "before-commit":
            bundle.mkdir()
            race_marker.write_bytes(b"racer")

    try:
        _publish_candidate_bundle(
            paths=race_paths,
            payloads=race_payloads,
            manifest=race_manifest,
            interruption_hook=create_destination_race,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("destination race was overwritten")
    if race_marker.read_bytes() != b"racer" or set(
        race_marker.parent.iterdir()
    ) != {race_marker}:
        raise AssertionError("destination race was modified or rolled back")

    failure_paths, failure_payloads, failure_manifest = fixture(
        "failure-race"
    )
    failure_paths["failure"].write_bytes(b"foreign failure")
    try:
        _publish_candidate_bundle(
            paths=failure_paths,
            payloads=failure_payloads,
            manifest=failure_manifest,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("pre-existing failure seal was ignored")
    if (
        failure_paths["failure"].read_bytes() != b"foreign failure"
        or failure_paths["network"].parent.exists()
    ):
        raise AssertionError("pre-existing failure seal was modified")

    normal_paths, normal_payloads, normal_manifest = fixture("normal")
    _publish_candidate_bundle(
        paths=normal_paths,
        payloads=normal_payloads,
        manifest=normal_manifest,
    )
    assert_complete(normal_paths)
    identities_before = {
        key: _identity(normal_paths[key])
        for key in (
            "network",
            "canonicalFloat",
            "optimizerShadow",
            "manifest",
        )
    }
    try:
        _publish_candidate_bundle(
            paths=normal_paths,
            payloads=normal_payloads,
            manifest=normal_manifest,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("committed candidate bundle was overwritten")
    identities_after = {
        key: _identity(normal_paths[key])
        for key in identities_before
    }
    if identities_after != identities_before:
        raise AssertionError("no-clobber retry changed a committed bundle")


def _self_test_candidate_command_paths(temp: Path) -> None:
    output_dir = _resolve(temp / "candidate-command-paths")
    output_dir.mkdir()
    plan = _resolve(temp / "training-plan.json")
    artifact_keys = (
        "network",
        "canonicalFloat",
        "optimizerShadow",
        "manifest",
    )
    for robustness, seed in (
        (False, PRIMARY_SEED),
        (True, ROBUSTNESS_SEED),
    ):
        command = _command_record(
            plan=plan,
            candidate_id="G3A",
            seed=seed,
            robustness=robustness,
            output_dir=output_dir,
        )
        bundle = _resolve(Path(str(command["bundle"])))
        if bundle.parent != output_dir:
            raise AssertionError(
                "candidate command bundle left the canonical output directory"
            )
        if any(
            _resolve(Path(str(command[key]))).parent != bundle
            for key in artifact_keys
        ):
            raise AssertionError(
                "candidate command artifact left its atomic bundle"
            )
        if _resolve(Path(str(command["failure"]))).parent != output_dir:
            raise AssertionError(
                "candidate command failure seal left the output directory"
            )
    _require_robustness_outputs_absent(
        plan_path=plan,
        output_dir=output_dir,
        candidate_ids=CANDIDATES,
    )
    unexpected = _command_record(
        plan=plan,
        candidate_id="G3B",
        seed=ROBUSTNESS_SEED,
        robustness=True,
        output_dir=output_dir,
    )
    unexpected_bundle = Path(str(unexpected["bundle"]))
    unexpected_bundle.mkdir()
    try:
        _require_robustness_outputs_absent(
            plan_path=plan,
            output_dir=output_dir,
            candidate_ids=("G3B",),
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "unexpected unselected robustness bundle was accepted"
        )
    unexpected_bundle.rmdir()
    unexpected_failure = Path(str(unexpected["failure"]))
    unexpected_failure.write_bytes(b"unexpected")
    try:
        _require_robustness_outputs_absent(
            plan_path=plan,
            output_dir=output_dir,
            candidate_ids=("G3B",),
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "unexpected unselected robustness failure was accepted"
        )


def _self_test_primary_failure_resume(temp: Path) -> None:
    output_dir = _resolve(temp / "primary-failure-resume")
    output_dir.mkdir()
    plan_path = _resolve(temp / "failure-resume-plan.json")
    plan_path.write_text("{}\n", encoding="utf-8")
    commands = {
        candidate_id: _command_record(
            plan=plan_path,
            candidate_id=candidate_id,
            seed=PRIMARY_SEED,
            robustness=False,
            output_dir=output_dir,
        )
        for candidate_id in CANDIDATES
    }
    plan = {"commands": commands}
    failed_command = commands["G3A"]
    failure_path = Path(str(failed_command["failure"]))
    failure = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": FAILURE_KIND,
        "createdUtc": _utc_now(),
        "candidateId": "G3A",
        "plan": _identity(plan_path),
        "command": failed_command,
        "exitCode": 1,
        "heldOutTargetFieldsDecodedByOrchestrator": 0,
    }
    _exclusive_json(failure_path, failure)
    failure_identity = _identity(failure_path)

    original_verify_plan = globals()["_verify_plan"]
    original_verify_candidate = globals()["_verify_candidate"]
    original_run = subprocess.run
    original_worker_environment = globals()["_worker_environment"]
    launched: list[tuple[str, ...]] = []
    verified: list[str] = []

    def synthetic_verify_plan(
        path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if _resolve(path) != plan_path:
            raise AssertionError("failure-resume plan path changed")
        return plan, {}

    def synthetic_run(
        argv: Sequence[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        launched.append(tuple(str(value) for value in argv))
        return subprocess.CompletedProcess(list(argv), 0)

    def synthetic_verify_candidate(
        _plan: Mapping[str, Any],
        candidate_id: str,
        *,
        robustness: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Path]]:
        if robustness:
            raise AssertionError(
                "primary-failure resume invoked robustness verification"
            )
        verified.append(candidate_id)
        return {}, {}

    try:
        globals()["_verify_plan"] = synthetic_verify_plan
        globals()["_verify_candidate"] = synthetic_verify_candidate
        globals()["_worker_environment"] = lambda: {}
        subprocess.run = synthetic_run
        _run_frozen(
            argparse.Namespace(
                plan=plan_path,
                candidate="all",
            )
        )
    finally:
        globals()["_verify_plan"] = original_verify_plan
        globals()["_verify_candidate"] = original_verify_candidate
        globals()["_worker_environment"] = original_worker_environment
        subprocess.run = original_run
    if (
        len(launched) != 2
        or verified != ["G3B", "G3C"]
        or _identity(failure_path) != failure_identity
    ):
        raise AssertionError(
            "verified primary failure did not resume later candidates"
        )

    malformed = dict(failure)
    malformed["candidateId"] = "G3B"
    failure_path.write_bytes(_canonical_json(malformed))
    try:
        _verify_primary_failure(
            plan_path=plan_path,
            plan=plan,
            candidate_id="G3A",
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "foreign primary candidate failure seal was accepted"
        )

    malformed = dict(failure)
    malformed["createdUtc"] = "2026-07-19"
    failure_path.write_bytes(_canonical_json(malformed))
    try:
        _verify_primary_failure(
            plan_path=plan_path,
            plan=plan,
            candidate_id="G3A",
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "non-UTC primary candidate failure timestamp was accepted"
        )


def _self_test_model_core(temp: Path, *, cpp: bool) -> None:
    initializer = base._blank_quantized_network(
        architecture=ARCHITECTURE_KING_STATE_RESIDUAL
    )
    initializer.ft_bias[:] = 17
    initializer.ft_weights[0, :4] = np.asarray(
        [1, -2, 3, -4], dtype=np.int16
    )
    initializer.ft_weights[
        REFERENCE_BUCKET * BASE_PIECE_FEATURES, :4
    ] = np.asarray([5, 6, -7, 8], dtype=np.int16)
    initializer.dense_bias[:] = 9
    initializer.dense_weights[:, :4] = 1
    initializer.output_weights[:4] = np.asarray(
        [1, -2, 3, -4], dtype=np.int8
    )
    canonical = base.FloatNetwork.from_quantized(initializer)
    if canonical.quantize(
        ARCHITECTURE_KING_STATE_RESIDUAL
    ).to_bytes() != initializer.to_bytes():
        raise AssertionError("canonical float reconstruction changed bytes")
    shared = SharedDeltaNetwork.from_float(canonical)
    if (
        shared.materialize().checkpoint_bytes()
        != canonical.checkpoint_bytes()
        or shared.quantize().to_bytes() != initializer.to_bytes()
    ):
        raise AssertionError("S+D initialization/materialization changed I0")

    records = [
        (base.INITIAL_OFEN, "initial", "opening"),
        # SPARSE_OFEN intentionally has only two kings and lies below the
        # frozen production phase floor; its synthetic phase is never used.
        (base.SPARSE_OFEN, "sparse", "endgame"),
    ]
    white, black, stm_white, signatures = _feature_rows(records)
    feature_corpus = FeatureCorpus(
        ofens=tuple(record[0] for record in records),
        groups=tuple(record[1] for record in records),
        phases=tuple(record[2] for record in records),
        splits=np.asarray([0, 1], dtype=np.int8),
        white_features=white,
        black_features=black,
        side_to_move_white=stm_white,
        input_signatures=signatures,
    )
    indices = np.arange(2, dtype=np.int64)
    stm, opponent = feature_corpus.perspective(indices)
    shared_prediction = _shared_forward(
        shared,
        stm,
        opponent,
        quantization_aware=True,
        need_cache=False,
    )[0]
    direct_prediction = shared.materialize().forward(
        stm,
        opponent,
        quantization_aware=True,
        need_cache=False,
    )[0]
    if not np.array_equal(shared_prediction, direct_prediction):
        raise AssertionError("shared QAT forward differs after materialization")

    gradient_dataset = LabeledCorpus(
        features=feature_corpus,
        target_cp=np.asarray([15.0, -20.0], dtype=np.float32),
        search_cp=np.asarray([25.0, -35.0], dtype=np.float32),
        handcrafted_cp=np.asarray([10.0, -15.0], dtype=np.float32),
        source_rows=2,
        withheld_rows=0,
    )
    batch = np.asarray([0], dtype=np.int64)
    row_weights = np.ones(2, dtype=np.float32)
    direct_loss, direct_gradients, _direct_parts = _direct_gradients(
        gradient_dataset,
        batch,
        canonical.clone(),
        row_weights=row_weights,
        soft_wdl=False,
        quantization_aware=True,
    )
    gradient_delta_optimizer = DeltaAdam(
        shared.delta, np.asarray([0, 7], dtype=np.int64)
    )
    (
        shared_loss,
        shared_gradients,
        shared_delta_gradient,
        _shared_parts,
    ) = _shared_gradients(
        gradient_dataset,
        batch,
        shared,
        row_weights=row_weights,
        soft_wdl=True,
        quantization_aware=True,
        delta_optimizer=gradient_delta_optimizer,
    )
    if (
        not math.isfinite(direct_loss)
        or not math.isfinite(shared_loss)
        or not all(
            np.all(np.isfinite(value))
            for value in (
                *direct_gradients.values(),
                *shared_gradients.values(),
                shared_delta_gradient,
            )
        )
    ):
        raise AssertionError("direct/shared training gradients are nonfinite")

    delta_before = shared.delta.copy()
    delta_optimizer = DeltaAdam(
        shared.delta, np.asarray([0, 7], dtype=np.int64)
    )
    first_before = delta_optimizer.first.copy()
    second_before = delta_optimizer.second.copy()
    frozen_gradient = np.ones_like(delta_optimizer.first)
    for frozen_epoch in range(1, DELTA_FIRST_EPOCH):
        _step_delta_for_epoch(
            delta_optimizer,
            frozen_gradient,
            epoch=frozen_epoch,
            quantization_aware=False,
        )
    if (
        delta_optimizer.step_count != 0
        or not np.array_equal(delta_optimizer.first, first_before)
        or not np.array_equal(delta_optimizer.second, second_before)
        or not np.array_equal(shared.delta, delta_before)
    ):
        raise AssertionError("frozen delta state changed before epoch 9")
    _step_delta_for_epoch(
        delta_optimizer,
        frozen_gradient,
        epoch=DELTA_FIRST_EPOCH,
        quantization_aware=False,
    )
    if delta_optimizer.step_count != 1:
        raise AssertionError("delta optimizer clock did not start at epoch 9")
    if np.array_equal(shared.delta[[0, 7]], delta_before[[0, 7]]):
        raise AssertionError("eligible delta rows did not update")
    ineligible = np.ones(shared.delta.shape[0], dtype=np.bool_)
    ineligible[[0, 7]] = False
    if not np.array_equal(
        shared.delta[ineligible], delta_before[ineligible]
    ):
        raise AssertionError("ineligible delta rows changed")

    if _select_checkpoint_epoch(
        [(1, -100.0), (36, -50.0), (37, 2.0), (38, 2.0), (48, 3.0)]
    ) != 37:
        raise AssertionError("checkpoint eligibility/tie rule changed")

    def deterministic_update() -> tuple[bytes, bytes]:
        model = SharedDeltaNetwork.from_float(canonical)
        optimizer = DeltaAdam(
            model.delta, np.asarray([0, 7], dtype=np.int64)
        )
        rng = np.random.default_rng(20260731)
        for _step in range(220):
            gradient = rng.normal(
                0.5, 0.1, size=optimizer.first.shape
            ).astype(np.float32)
            optimizer.step(gradient, global_scale=1.0)
        network_bytes = model.quantize().to_bytes()
        deterministic_manifest = _canonical_json(
            {
                "candidateId": "self-test",
                "seed": 20260731,
                "networkBytes": len(network_bytes),
                "networkSha256": hashlib.sha256(
                    network_bytes
                ).hexdigest(),
                "deltaOptimizerSteps": optimizer.step_count,
            }
        )
        return network_bytes, deterministic_manifest

    first_network, first_manifest = deterministic_update()
    second_network, second_manifest = deterministic_update()
    if (
        first_network != second_network
        or first_manifest != second_manifest
    ):
        raise AssertionError("same-seed replay was not byte-identical")

    if cpp:
        helper = CPP_EVALUATOR
        if not helper.is_file():
            raise FileNotFoundError(
                f"C++ parity helper requested but absent: {helper}"
            )
        network_path = temp / "self-test.nnue"
        network_path.write_bytes(initializer.to_bytes())
        python_predictions = _predict_quantized(
            initializer, feature_corpus
        )
        cpp_predictions = _cpp_stream_predictions(
            helper, network_path, feature_corpus.ofens
        )
        if not np.array_equal(python_predictions, cpp_predictions):
            raise AssertionError("whole-corpus Python/C++ parity failed")


def _self_test(args: argparse.Namespace) -> dict[str, Any]:
    _require_deterministic_runtime_environment()
    runtime = _runtime_record()
    if runtime["numpyPreloadedBeforeTrainingWorkerContract"] is not False:
        raise AssertionError("self-test missed the NumPy preload contract")
    for actual, expected in (
        (True, 1),
        (1, True),
        ({"contract": [True]}, {"contract": [1]}),
        ({"contract": [1]}, {"contract": [True]}),
        ({True}, {1}),
        ({1}, {True}),
    ):
        try:
            _expect(actual, expected, "strict-equality self-test")
        except ValueError:
            pass
        else:
            raise AssertionError(
                "frozen contract equality accepted bool/int coercion"
            )
    if _canonical_json(
        prelabel_training_contract()
    ) != _canonical_json(prelabel_training_contract()):
        raise AssertionError("pre-label training contract is nondeterministic")
    _self_test_ordered_amendment_chain()
    _validate_preregistration(args.preregistration)
    _validate_amendment(
        args.amendment, preregistration=args.preregistration
    )
    _validate_amendment_002(
        args.amendment_002, prior_amendment=args.amendment
    )
    _validate_amendment_003(
        args.amendment_003, prior_amendment=args.amendment_002
    )
    if phase_incidence_preflight is None:
        raise ValueError("phase-incidence preflight unavailable")
    phase_incidence_preflight._self_test()
    with tempfile.TemporaryDirectory(
        prefix="omega-nnue-gen3-self-test-"
    ) as directory:
        temp = Path(directory)
        _self_test_routing(temp)
        _self_test_atomic_candidate_bundles(temp)
        _self_test_candidate_command_paths(temp)
        _self_test_primary_failure_resume(temp)
        _self_test_weights_and_occurrences()
        _self_test_loss_gradient()
        _self_test_model_core(temp, cpp=not args.skip_cpp)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v3-self-test",
        "passed": True,
        "tests": [
            "type-strict recursive frozen-contract equality",
            "split-2 target-opaque routing",
            "atomic all-or-nothing candidate bundle publication",
            "precommit interruption cleanup and postcommit recovery",
            "staged payload and manifest identity verification",
            "partial/racing destination no-clobber preservation",
            "candidate command bundle/output path integration",
            "verified primary failure resumes later candidates",
            "exact ordered pre-label amendment chain",
            "stored/OFEN-derived phase agreement",
            "split-0 both-perspective occurrence threshold",
            "S+D initialization/materialization/requantization",
            "delta freeze/moments/independent clock",
            "shared QAT/materialized-direct forward equality",
            "direct/shared finite training backpropagation",
            "weighted Huber finite-difference gradient",
            "soft-WDL finite-difference gradient",
            "phase/global-group weighting formula",
            "QAT-only checkpoint eligibility and exact tie",
            "canonical float byte-identical requantization",
            "same-seed byte-identical network/manifest replay",
            *(
                ["whole-corpus Python/C++ integer parity"]
                if not args.skip_cpp
                else []
            ),
            "phase-incidence target-opacity/incidence guard",
            "Python/NumPy/platform/thread runtime pin",
            "complete deterministic pre-label training contract",
        ],
    }


def _path_argument(value: str) -> Path:
    return Path(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser(
        "plan", help="verify pre-label inputs and freeze exact commands"
    )
    plan.add_argument("--preregistration", type=_path_argument, default=PREREGISTRATION)
    plan.add_argument("--amendment", type=_path_argument, default=AMENDMENT)
    plan.add_argument(
        "--amendment-002", type=_path_argument, default=AMENDMENT_002
    )
    plan.add_argument(
        "--amendment-003", type=_path_argument, default=AMENDMENT_003
    )
    plan.add_argument("--prelabel-seal", type=_path_argument, default=PRELABEL_SEAL)
    plan.add_argument("--corpus", type=_path_argument, default=CORPUS)
    plan.add_argument(
        "--corpus-manifest", type=_path_argument, default=CORPUS_MANIFEST
    )
    plan.add_argument("--phase-audit", type=_path_argument, default=PHASE_AUDIT)
    plan.add_argument(
        "--initializer-resolution",
        type=_path_argument,
        default=INITIALIZER_RESOLUTION,
    )
    plan.add_argument("--initializer", type=_path_argument)
    plan.add_argument("--cpp-evaluator", type=_path_argument, default=CPP_EVALUATOR)
    plan.add_argument("--output-dir", type=_path_argument, default=OUTPUT_DIR)
    plan.add_argument("--plan", type=_path_argument, default=PLAN_PATH)
    plan.set_defaults(handler=_prepare_plan)

    run = commands.add_parser(
        "run", help="execute one or all frozen primary candidate commands"
    )
    run.add_argument("--plan", type=_path_argument, default=PLAN_PATH)
    run.add_argument(
        "--candidate", choices=(*CANDIDATES, "all"), default="all"
    )
    run.set_defaults(handler=_run_frozen)

    worker = commands.add_parser("train-worker", help=argparse.SUPPRESS)
    worker.add_argument("--plan", type=_path_argument, required=True)
    worker.add_argument("--candidate", choices=CANDIDATES, required=True)
    worker.add_argument("--seed", type=int, required=True)
    worker.add_argument("--output-dir", type=_path_argument, required=True)
    worker.add_argument("--robustness", action="store_true")
    worker.add_argument("--quiet", action="store_true")
    worker.set_defaults(handler=_run_worker)

    select = commands.add_parser(
        "select", help="apply frozen validation gates and seal one recipe"
    )
    select.add_argument("--plan", type=_path_argument, default=PLAN_PATH)
    select.set_defaults(handler=_select)

    robustness = commands.add_parser(
        "run-robustness",
        help="rerun only the validation-selected recipe with seed 20260732",
    )
    robustness.add_argument(
        "--selection", type=_path_argument, default=SELECTION_PATH
    )
    robustness.set_defaults(handler=_run_robustness)

    self_test = commands.add_parser(
        "self-test", help="run deterministic target-free implementation tests"
    )
    self_test.add_argument(
        "--preregistration", type=_path_argument, default=PREREGISTRATION
    )
    self_test.add_argument("--amendment", type=_path_argument, default=AMENDMENT)
    self_test.add_argument(
        "--amendment-002", type=_path_argument, default=AMENDMENT_002
    )
    self_test.add_argument(
        "--amendment-003", type=_path_argument, default=AMENDMENT_003
    )
    self_test.add_argument(
        "--skip-cpp",
        action="store_true",
        help="skip the compiled C++ parity helper (not for pre-label freeze)",
    )
    self_test.set_defaults(handler=_self_test)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    result = args.handler(args)
    if isinstance(result, Mapping):
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
