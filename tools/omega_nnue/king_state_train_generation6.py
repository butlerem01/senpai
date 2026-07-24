#!/usr/bin/env python3
"""Sealed, result-blind Generation-6 Omega NNUE training foundation.

Generation 6 trains on complete four-child move decisions.  The module owns
the exact projected-label schema, the static-HCE projection authority, the
root-grouped loss, result-blind validation metrics, deterministic selection,
and the no-clobber seal chain that gates held-out target access.

It never accepts an evaluator callback or an embedded HCE test shortcut.  A
production load requires exact corpus, label-manifest, component-map,
static-HCE projection, engine, options, and perspective identities.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence

import numpy as np


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v6-move-decision-v1"
PROTOCOL_KIND = "omega-nnue-king-state-v6-training-protocol"
LABEL_KIND = "omega-nnue-king-state-v6-decision-label"
COMPONENT_ROW_KIND = "omega-nnue-king-state-v6-component-authority"
UPSTREAM_PRELABEL_SEAL_KIND = "omega-nnue-king-state-v6-upstream-prelabel-seal"
UPSTREAM_TEACHER_LABEL_KIND = "omega-nnue-king-state-v6-upstream-teacher-label"
UPSTREAM_TEACHER_MANIFEST_KIND = (
    "omega-nnue-king-state-v6-upstream-teacher-completion"
)
UPSTREAM_CAPSULE_KIND = "omega-decision-v3-capsule-closure"
UPSTREAM_ROUTING_KIND = "omega-decision-v3-target-free-routing"
UPSTREAM_TEACHER_CLAIM_KIND = "omega-decision-v3-teacher-claim"
UPSTREAM_TEACHER_COMPLETION_KIND = "omega-decision-v3-teacher-completion"
UPSTREAM_HCE_CLAIM_KIND = "omega-decision-v3-pretarget-hce-claim"
UPSTREAM_VERIFIER_OPTIONS_KIND = "omega-decision-v3-verifier-options"
UPSTREAM_VERIFICATION_KIND = "omega-decision-v3-fresh-verification"
LABEL_MANIFEST_KIND = "omega-nnue-king-state-v6-label-manifest"
HCE_ROW_KIND = "omega-nnue-king-state-v6-static-hce"
HCE_OPTIONS_KIND = "omega-nnue-king-state-v6-static-hce-options"
HCE_MANIFEST_KIND = "omega-nnue-king-state-v6-static-hce-manifest"
METRIC_KIND = "omega-nnue-king-state-v6-result-blind-metrics"
VALIDATION_SELECTION_KIND = "omega-nnue-king-state-v6-validation-selection-seal"
ROBUSTNESS_SEAL_KIND = "omega-nnue-king-state-v6-robustness-seal"
HELDOUT_SEAL_KIND = "omega-nnue-king-state-v6-heldout-access-seal"

PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
SPLITS = ("train", "validation", "heldOut")
CANDIDATES = ("G6A", "G6B", "G6C")
TIE_PRIORITY = ("G6A", "G6B", "G6C")

CANDIDATE_RECIPES: Mapping[str, Mapping[str, float]] = {
    "G6A": {"listwiseSoftmaxWeight": 0.15, "nearEqualTopSetWeight": 0.15},
    "G6B": {"listwiseSoftmaxWeight": 0.30, "nearEqualTopSetWeight": 0.30},
    "G6C": {"listwiseSoftmaxWeight": 0.45, "nearEqualTopSetWeight": 0.45},
}

RESIDUAL_CLIP_CP = 2000.0
HUBER_NORMALIZER_CP = 100.0
HUBER_DELTA_NORMALIZED = 2.0
LISTWISE_TEMPERATURE_CP = 200.0
TOP_SET_MAX_REGRET_CP = 25
SCORE_ABS_LIMIT_CP = 1_000_000
REGRET_MAX_CP = SCORE_ABS_LIMIT_CP * 2
SCORE_COHERENCE_ABS_TOLERANCE_CP = 0.0

PRIMARY_TRAINING_SEED_BASE = 2026072401
ROBUSTNESS_TRAINING_SEED_BASE = 2026072402
INITIALIZER_ORDERED_CATALOG = ("G5", "G2-K2")
INITIALIZER_SELECTION_MODES = ("promoted-prior", "deterministic-fallback")
INITIALIZER_FALLBACK_PROTOCOL = {
    "architecture": "king-state-v6-move-decision-initializer-v1",
    "generator": "pinned upstream initializer verifier",
    "seed": 2026072400,
    "selectionRule": "use only when no catalog entry is independently promoted",
    "g6TargetRowsDecoded": 0,
    "gameResultsRead": False,
}
SEED_DERIVATION = (
    "SHA-256 of UTF-8 '<base-seed>|<purpose>|<candidate-id>'; "
    "first eight digest bytes as unsigned little-endian"
)

NON_REGRESSION_GATES: Mapping[str, float] = {
    "maximumMacroTopSetAccuracyRegression": 0.0,
    "maximumMacroChosenMoveRegretIncreaseCp": 0.0,
    "maximumMacroListwiseCrossEntropyIncrease": 0.0,
    "maximumMacroPointwiseHuberIncrease": 0.0,
    "maximumCellTopSetAccuracyRegression": 0.01,
    "maximumCellChosenMoveRegretIncreaseCp": 5.0,
}
VALIDATION_LEXICOGRAPHIC_ORDER = (
    "maximum macro top-set accuracy",
    "minimum macro chosen-move regret cp",
    "minimum macro listwise cross-entropy",
    "minimum macro pointwise clipped-residual Huber",
    "fixed candidate priority G6A,G6B,G6C",
)

STATIC_HCE_PERSPECTIVE = (
    "integer centipawns from child side-to-move; child side is opposite parent side"
)
METRIC_AGGREGATION = (
    "equal mean of eight phase-by-root-side cells; equal complete roots within cell"
)
TIMESTAMP_FORMAT = "RFC3339 UTC with exactly six fractional digits and terminal Z"
HELDOUT_DECLARATION = (
    "published after exact validation-selection and passed robustness seals, "
    "before the first held-out target-bearing JSON decode"
)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_NAMESPACE_ROOT = REPO / "build-msvc" / "king-state-v6"
DEFAULT_PREREGISTRATION = DEFAULT_NAMESPACE_ROOT / "00-preregistration.json"
PREREGISTRATION_KIND = "omega-nnue-king-state-v6-preregistration"
TRAINING_MANIFEST_KIND = "omega-nnue-king-state-v6-training-manifest"
INITIALIZER_MANIFEST_KIND = "omega-nnue-king-state-v6-initializer-manifest"
HEALTH_EVIDENCE_KIND = "omega-nnue-king-state-v6-health-evidence"
PREDICTION_ROW_KIND = "omega-nnue-king-state-v6-prediction"
PREDICTION_MANIFEST_KIND = "omega-nnue-king-state-v6-prediction-manifest"
HELDOUT_CLAIM_KIND = "omega-nnue-king-state-v6-heldout-consumption-claim"
HELDOUT_REPORT_KIND = "omega-nnue-king-state-v6-heldout-aggregate-report"
HELDOUT_CLOSURE_KIND = "omega-nnue-king-state-v6-heldout-closure"

OPTIMIZER_PROTOCOL = {
    "optimizer": "Adam",
    "epochs": 48,
    "qatEpochs": 12,
    "firstQatEpoch": 37,
    "rootsPerBatch": 64,
    "learningRate": 0.003,
    "qatLearningRateScale": 0.1,
    "checkpointRule": (
        "final epoch only; deployment health and frozen validation are external"
    ),
    "completeRootDivisibilityRequired": True,
    "batchOrderDerivation": (
        "per epoch SHA-256(seed|epoch|index), first 8 bytes little-endian, "
        "NumPy PCG64 permutation of ascending rootId"
    ),
}
HEALTH_THRESHOLDS = {
    "requireFinitePredictions": True,
    "requireQuantizationRoundTripExact": True,
    "requireExpectedNetworkBytes": True,
    "requireRuntimeParity": True,
    "maximumAbsResidualCp": 2000,
}

# Every formal Generation-6 artifact has one preregistered slot.  The
# preregistration is deliberately not part of this mapping because it is the
# unique genesis file and cannot contain its own content hash.  No formal API
# accepts an output path.
CANONICAL_ARTIFACT_PATHS: Mapping[str, str] = {
    "authorityClaim": "00-authority/materialization.claim.json",
    "authorityUpstreamVerification": "00-authority/upstream-verification.json",
    "authorityTrainCorpus": "00-authority/train.labels.jsonl",
    "authorityValidationCorpus": "00-authority/validation.labels.jsonl",
    "authorityTrainHce": "00-authority/train.hce.jsonl",
    "authorityValidationHce": "00-authority/validation.hce.jsonl",
    "authorityInitializerModel": "00-authority/I0.nnue",
    "authorityManifest": "00-authority/materialization.manifest.json",
    **{
        f"trainingClaim:{model_id}": f"01-training/{model_id}.claim.json"
        for model_id in (*CANDIDATES, *(f"{item}-robustness" for item in CANDIDATES))
    },
    **{
        f"model:{model_id}": f"01-training/{model_id}.nnue"
        for model_id in (*CANDIDATES, *(f"{item}-robustness" for item in CANDIDATES))
    },
    **{
        f"trainingHistory:{model_id}": f"01-training/{model_id}.history.json"
        for model_id in (*CANDIDATES, *(f"{item}-robustness" for item in CANDIDATES))
    },
    **{
        f"trainingBatchOrder:{model_id}": f"01-training/{model_id}.batch-order.jsonl"
        for model_id in (*CANDIDATES, *(f"{item}-robustness" for item in CANDIDATES))
    },
    **{
        f"trainingManifest:{model_id}": f"01-training/{model_id}.manifest.json"
        for model_id in (*CANDIDATES, *(f"{item}-robustness" for item in CANDIDATES))
    },
    **{
        f"evidenceClaim:{model_id}": f"02-validation/{model_id}.claim.json"
        for model_id in (
            "I0",
            *CANDIDATES,
            *(f"{item}-robustness" for item in CANDIDATES),
        )
    },
    **{
        f"health:{model_id}": f"02-validation/{model_id}.health.json"
        for model_id in (
            "I0",
            *CANDIDATES,
            *(f"{item}-robustness" for item in CANDIDATES),
        )
    },
    **{
        f"predictions:{model_id}": f"02-validation/{model_id}.predictions.jsonl"
        for model_id in (
            "I0",
            *CANDIDATES,
            *(f"{item}-robustness" for item in CANDIDATES),
        )
    },
    **{
        f"predictionManifest:{model_id}": f"02-validation/{model_id}.predictions.manifest.json"
        for model_id in (
            "I0",
            *CANDIDATES,
            *(f"{item}-robustness" for item in CANDIDATES),
        )
    },
    **{
        f"metric:{model_id}": f"02-validation/{model_id}.metrics.json"
        for model_id in (
            "I0",
            *CANDIDATES,
            *(f"{item}-robustness" for item in CANDIDATES),
        )
    },
    **{
        f"evidenceManifest:{model_id}": f"02-validation/{model_id}.evidence.json"
        for model_id in (
            "I0",
            *CANDIDATES,
            *(f"{item}-robustness" for item in CANDIDATES),
        )
    },
    "validationSelection": "03-selection/validation-selection.json",
    "robustnessSeal": "04-robustness/robustness.json",
    "heldoutAccess": "05-heldout/access.json",
    "heldoutClaim": "05-heldout/claim.json",
    "heldoutReport": "05-heldout/aggregate-report.json",
    "heldoutClosure": "05-heldout/closure.json",
}

TRAINING_CLAIM_KIND = "omega-nnue-king-state-v6-training-claim"
AUTHORITY_CLAIM_KIND = "omega-nnue-king-state-v6-authority-materialization-claim"
AUTHORITY_MANIFEST_KIND = (
    "omega-nnue-king-state-v6-authority-materialization-manifest"
)
EVIDENCE_CLAIM_KIND = "omega-nnue-king-state-v6-evidence-claim"
EVIDENCE_MANIFEST_KIND = "omega-nnue-king-state-v6-evidence-manifest"
TRAINING_HISTORY_KIND = "omega-nnue-king-state-v6-training-history"
TRAINING_BATCH_ORDER_KIND = "omega-nnue-king-state-v6-actual-batch-order"
RUNTIME_MANIFEST_KIND = "omega-nnue-king-state-v6-runtime-manifest"
EVALUATOR_OPTIONS_KIND = "omega-nnue-king-state-v6-evaluator-options"
TRAINER_COMMAND_PROTOCOL = {
    "mode": "--train-generation6",
    "arguments": [
        "model-id",
        "candidate-id",
        "purpose",
        "unsigned-64-bit-seed",
        "canonical-recipe-json",
        "canonical-optimizer-json",
        "train-only-projected-corpus",
        "train-only-static-hce-projection",
        "initializer-model",
        "output-actual-batch-order-transcript",
        "output-model",
        "output-history",
    ],
    "environment": "inherited environment forbidden; deterministic runner contract",
}
EVALUATOR_OPTIONS = {
    "schemaVersion": SCHEMA_VERSION,
    "kind": EVALUATOR_OPTIONS_KIND,
    "profileId": PROFILE_ID,
    "commandArgumentsBeforeRunner": ["-I", "-B"],
    "networkMode": "--evaluate-network-stream",
    "healthMode": "--validate-network-health",
    "networkStdin": "one normalized child OFEN per line",
    "networkStdout": "one exact bounded integer residual cp per line",
    "healthStdout": "one exact JSON health object",
    "perspective": "integer residual centipawns from child side-to-move",
}
UPSTREAM_VERIFIER_OPTIONS = {
    "schemaVersion": SCHEMA_VERSION,
    "kind": UPSTREAM_VERIFIER_OPTIONS_KIND,
    "profileId": PROFILE_ID,
    "mode": "--verify-omega-decision-v3-capsule",
    "stdout": "one exact JSON omega-decision-v3 fresh-verification object",
    "initializerPolicy": {
        "orderedCatalog": list(INITIALIZER_ORDERED_CATALOG),
        "selectionModes": list(INITIALIZER_SELECTION_MODES),
        "fallbackProtocol": dict(INITIALIZER_FALLBACK_PROTOCOL),
        "requireFirstIndependentlyPromotedEntry": True,
        "requireFreshHealthForPromotedEntry": True,
        "requireFailureOrAbortClosureForSkippedEntry": True,
    },
    "requiredSemanticReplays": [
        "initializer selection, embedded health, source closure, and model cross-links",
        "terminal rules manifest/transcript/completion and pre-teacher exclusions",
        "every prior-forbidden manifest/catalog and zero current overlap",
        "component-map coverage and whole-component split assignment",
        "teacher lock/attempt ledger/completion coverage and budgets",
        "planned projection producer/path against realized corpus/manifest",
    ],
    "resultInformationRead": False,
}

LABEL_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "leakageComponentId",
        "split",
        "childId",
        "childOfen",
        "phase",
        "parentSideToMove",
        "deepRank",
        "deepRegretCp",
        "deepScoreCpRoot",
        "deepScoreCpChildStm",
    }
)
LABEL_STRING_FIELDS = (
    "kind",
    "rootId",
    "leakageComponentId",
    "split",
    "childId",
    "childOfen",
    "phase",
    "parentSideToMove",
)
RETAINED_FIELDS = (
    "rootId",
    "leakageComponentId",
    "split",
    "childId",
    "childOfen",
    "phase",
    "parentSideToMove",
    "deepRank",
    "deepRegretCp",
    "deepScoreCpRoot",
    "deepScoreCpChildStm",
    "handcraftedCpChildStm",
    "residualTargetCp",
)

COMPONENT_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "rootId",
        "leakageComponentId",
        "split",
        "sourceRootId",
        "sourceGroupId",
    }
)
UPSTREAM_TEACHER_LABEL_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "rootId",
        "childId",
        "childOfen",
        "phase",
        "parentSideToMove",
        "deepRank",
        "deepRegretCp",
        "deepScoreCpRoot",
        "deepScoreCpChildStm",
    }
)
HCE_FIELDS = frozenset(
    {"schemaVersion", "kind", "profileId", "childId", "handcraftedCpChildStm"}
)
HCE_OPTIONS_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "engineProtocol",
        "executableMode",
        "uciVariant",
        "omegaNnueFile",
        "perspective",
        "commandArgumentsBeforeRunner",
        "stdinProtocol",
        "stdoutProtocol",
    }
)
_METRIC_NAMES = (
    "topSetAccuracy",
    "meanChosenMoveRegretCp",
    "listwiseCrossEntropy",
    "pointwiseHuber",
)
_CELL_KEYS = tuple(f"{phase}:{side}" for phase in PHASES for side in SIDES)

_JSON_NUMBER = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
)
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class HeldoutAccessError(ValueError):
    """Raised before any held-out target-bearing row is decoded."""


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _safe_path(path: Path, *, regular_file: bool) -> Path:
    """Reject links/reparse points before resolution, including every parent."""

    absolute = _lexical_absolute(path)
    chain = list(reversed(absolute.parents)) + [absolute]
    leaf_seen = False
    for item in chain:
        try:
            info = os.lstat(item)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"symlink/junction/reparse path is forbidden: {item}")
        if item == absolute:
            leaf_seen = True
            if regular_file and not stat.S_ISREG(info.st_mode):
                raise ValueError(f"artifact is not a regular file: {absolute}")
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"artifact parent is not a directory: {item}")
    if regular_file and not leaf_seen:
        raise FileNotFoundError(absolute)
    return absolute


def _safe_existing_file(path: Path) -> Path:
    return _safe_path(path, regular_file=True)


def _safe_new_file(path: Path) -> Path:
    absolute = _safe_path(path, regular_file=False)
    parent = _safe_path(absolute.parent, regular_file=False)
    if not parent.is_dir():
        raise FileNotFoundError(f"publisher parent does not exist: {parent}")
    if absolute.exists() or os.path.lexists(absolute):
        raise FileExistsError(absolute)
    return absolute


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    if left.st_dev != right.st_dev or left.st_ino != right.st_ino:
        return False
    return (
        left.st_size == right.st_size
        and getattr(left, "st_mtime_ns", None)
        == getattr(right, "st_mtime_ns", None)
    )


def _snapshot_file(path: Path) -> tuple[dict[str, Any], bytes]:
    """Read one descriptor and reject link/path swaps before and after it."""

    safe = _safe_existing_file(path)
    before = os.lstat(safe)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(safe, flags)
    payload = bytearray()
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError(f"opened artifact is not a regular non-reparse file: {safe}")
        if not _same_file_snapshot(before, opened):
            raise ValueError(f"artifact changed between lstat and descriptor open: {safe}")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            payload.extend(block)
            digest.update(block)
        after_open = os.fstat(descriptor)
        if not _same_file_snapshot(opened, after_open):
            raise ValueError(f"artifact changed while descriptor was read: {safe}")
    finally:
        os.close(descriptor)
    # Recheck every parent plus the leaf and ensure the path still names the
    # exact descriptor snapshot.  This converts detectable rename/reparse
    # races into a hard failure instead of authenticating a stale pathname.
    _safe_existing_file(safe)
    after_path = os.lstat(safe)
    if not _same_file_snapshot(before, after_path):
        raise ValueError(f"artifact path changed during snapshot: {safe}")
    identity = {
        "path": str(safe),
        "bytes": len(payload),
        "sha256": digest.hexdigest(),
    }
    return identity, bytes(payload)


def _sha256(path: Path) -> str:
    return _snapshot_file(path)[0]["sha256"]


def _identity(path: Path) -> dict[str, Any]:
    return _snapshot_file(path)[0]


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


def _document_identity(value: Any) -> dict[str, Any]:
    payload = _canonical_json(value)
    return {"bytes": len(payload), "sha256": _sha256_bytes(payload)}


def _exclusive_bytes(path: Path, payload: bytes) -> dict[str, Any]:
    safe = _safe_new_file(path)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(safe, flags, 0o644)
    opened: os.stat_result | None = None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError("O_EXCL publisher did not create one regular file")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        completed = os.fstat(descriptor)
        if (
            completed.st_dev != opened.st_dev
            or completed.st_ino != opened.st_ino
            or completed.st_size != len(payload)
            or getattr(completed, "st_nlink", 1) != 1
        ):
            raise ValueError("published descriptor identity/content changed")
        os.close(descriptor)
        descriptor = -1
        _safe_existing_file(safe)
        named = os.lstat(safe)
        if not _same_file_snapshot(completed, named):
            raise ValueError("published path no longer names the O_EXCL descriptor")
        identity, actual = _snapshot_file(safe)
        if actual != payload:
            raise ValueError("published bytes changed after descriptor close")
        return identity
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            named = os.lstat(safe)
            if (
                opened is not None
                and named.st_dev == opened.st_dev
                and named.st_ino == opened.st_ino
                and stat.S_ISREG(named.st_mode)
            ):
                safe.unlink()
        except OSError:
            pass
        raise


def _exclusive_json(path: Path, value: Any) -> dict[str, Any]:
    return _exclusive_bytes(path, _canonical_json(value))


def _strict_json_loads(text: str, *, location: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{location}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError(f"{location}: non-finite JSON number {value!r}")

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{location}: invalid JSON") from error


def _load_json(path: Path, label: str) -> dict[str, Any]:
    identity, payload = _snapshot_file(path)
    safe = Path(identity["path"])
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    value = _strict_json_loads(text, location=str(safe))
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def _snapshot_utf8_lines(path: Path, label: str) -> tuple[Path, tuple[str, ...]]:
    identity, payload = _snapshot_file(path)
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    return Path(identity["path"]), tuple(text.splitlines(keepends=True))


def _exact_keys(value: Mapping[str, Any], expected: set[str] | frozenset[str], label: str) -> None:
    if set(value) != set(expected):
        raise ValueError(f"{label} field inventory changed")


def _exact_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(f"{label} is not an exact bounded integer")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} is not finite")
    return result


def _parse_timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or _UTC_TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{label} is not canonical {TIMESTAMP_FORMAT}")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ValueError(f"{label} is not a valid timestamp") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        raise ValueError(f"{label} is not canonical")
    return parsed


def _type_exact_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            type(key) is str and _type_exact_equal(left[key], right[key])
            for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _type_exact_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _verify_identity_record(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an identity")
    _exact_keys(value, {"path", "bytes", "sha256"}, label)
    if type(value["path"]) is not str or type(value["bytes"]) is not int or type(value["sha256"]) is not str:
        raise ValueError(f"{label} identity types changed")
    actual = _identity(Path(value["path"]))
    if not _type_exact_equal(value, actual):
        raise ValueError(f"{label} identity changed")
    return actual


@dataclass(frozen=True)
class _OpaqueRoute:
    strings: Mapping[str, str]
    raw: Mapping[str, str]
    keys: frozenset[str]


class _OpaqueJsonRouter:
    """Validate JSON and expose only requested strings/raw lexical values."""

    def __init__(
        self,
        text: str,
        *,
        wanted_strings: Sequence[str],
        wanted_raw: Sequence[str],
        location: str,
    ) -> None:
        self.text = text
        self.length = len(text)
        self.wanted_strings = frozenset(wanted_strings)
        self.wanted_raw = frozenset(wanted_raw)
        self.location = location
        self.strings: dict[str, str] = {}
        self.raw: dict[str, str] = {}
        self.top_keys: set[str] = set()

    def error(self, message: str) -> ValueError:
        return ValueError(f"{self.location}: {message}")

    def whitespace(self, index: int) -> int:
        while index < self.length and self.text[index] in " \t\r\n":
            index += 1
        return index

    def string_end(self, index: int) -> int:
        if index >= self.length or self.text[index] != '"':
            raise self.error("expected JSON string")
        index += 1
        while index < self.length:
            character = self.text[index]
            if character == '"':
                return index + 1
            if ord(character) < 0x20:
                raise self.error("unescaped control character in JSON string")
            if character != "\\":
                index += 1
                continue
            index += 1
            if index >= self.length:
                raise self.error("truncated JSON escape")
            escape = self.text[index]
            if escape == "u":
                digits = self.text[index + 1 : index + 5]
                if len(digits) != 4 or any(
                    digit not in "0123456789abcdefABCDEF" for digit in digits
                ):
                    raise self.error("invalid JSON unicode escape")
                index += 5
            elif escape in '"\\/bfnrt':
                index += 1
            else:
                raise self.error("invalid JSON escape")
        raise self.error("unterminated JSON string")

    def decoded_string(self, index: int) -> tuple[str, int]:
        end = self.string_end(index)
        try:
            value = json.loads(self.text[index:end])
        except json.JSONDecodeError as error:
            raise self.error("invalid JSON string") from error
        if type(value) is not str:
            raise AssertionError("string decoder returned a non-string")
        return value, end

    def value(self, index: int, *, top_level: bool = False) -> int:
        index = self.whitespace(index)
        if index >= self.length:
            raise self.error("missing JSON value")
        character = self.text[index]
        if character == "{":
            return self.object(index, top_level=top_level)
        if character == "[":
            return self.array(index)
        if character == '"':
            return self.string_end(index)
        for literal in ("true", "false", "null"):
            if self.text.startswith(literal, index):
                return index + len(literal)
        match = _JSON_NUMBER.match(self.text, index)
        if match is not None:
            return match.end()
        raise self.error("invalid or non-finite JSON value")

    def array(self, index: int) -> int:
        index = self.whitespace(index + 1)
        if index < self.length and self.text[index] == "]":
            return index + 1
        while True:
            index = self.whitespace(self.value(index))
            if index >= self.length:
                raise self.error("unterminated JSON array")
            if self.text[index] == "]":
                return index + 1
            if self.text[index] != ",":
                raise self.error("expected comma in JSON array")
            index = self.whitespace(index + 1)

    def object(self, index: int, *, top_level: bool) -> int:
        seen: set[str] = set()
        index = self.whitespace(index + 1)
        if index < self.length and self.text[index] == "}":
            return index + 1
        while True:
            key, index = self.decoded_string(index)
            if key in seen:
                raise self.error(f"duplicate JSON key {key!r}")
            seen.add(key)
            if top_level:
                self.top_keys.add(key)
            index = self.whitespace(index)
            if index >= self.length or self.text[index] != ":":
                raise self.error("expected colon after JSON key")
            index = self.whitespace(index + 1)
            if top_level and key in self.wanted_strings:
                if index >= self.length or self.text[index] != '"':
                    raise self.error(f"routing field {key!r} is not a string")
                value, index = self.decoded_string(index)
                self.strings[key] = value
            else:
                start = index
                index = self.value(index)
                if top_level and key in self.wanted_raw:
                    self.raw[key] = self.text[start:index]
            index = self.whitespace(index)
            if index >= self.length:
                raise self.error("unterminated JSON object")
            if self.text[index] == "}":
                return index + 1
            if self.text[index] != ",":
                raise self.error("expected comma in JSON object")
            index = self.whitespace(index + 1)

    def route(self) -> _OpaqueRoute:
        start = self.whitespace(0)
        if start >= self.length or self.text[start] != "{":
            raise self.error("row is not a JSON object")
        end = self.whitespace(self.object(start, top_level=True))
        if end != self.length:
            raise self.error("trailing data after JSON object")
        missing_strings = self.wanted_strings - self.strings.keys()
        missing_raw = self.wanted_raw - self.raw.keys()
        if missing_strings or missing_raw:
            raise self.error("missing opaque routing fields")
        return _OpaqueRoute(
            strings=dict(self.strings),
            raw=dict(self.raw),
            keys=frozenset(self.top_keys),
        )


def _child_side(ofen: str, label: str) -> str:
    tokens = " ".join(ofen.split()).split(" ")
    if len(tokens) < 2 or tokens[1] not in SIDES:
        raise ValueError(f"{label} has no valid side-to-move field")
    return tokens[1]


@dataclass(frozen=True)
class _OpaqueLabelRow:
    location: str
    line: str
    routing: Mapping[str, str]


@dataclass(frozen=True)
class _StructuralRoot:
    root_id: str
    component_id: str
    split: str
    phase: str
    root_side: str
    sibling_ids: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        return {
            "rootId": self.root_id,
            "leakageComponentId": self.component_id,
            "split": self.split,
            "phase": self.phase,
            "parentSideToMove": self.root_side,
            "childIds": list(self.sibling_ids),
        }


@dataclass(frozen=True)
class _OpaqueCorpus:
    rows: tuple[_OpaqueLabelRow, ...]
    roots: tuple[_StructuralRoot, ...]
    root_inventories: Mapping[str, Mapping[str, Any]]
    phase_side_inventories: Mapping[str, Mapping[str, Mapping[str, Any]]]


def _inventory(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: str(item["rootId"]))
    return {
        "roots": len(ordered),
        "children": len(ordered) * 4,
        "sha256": _sha256_bytes(_canonical_json(ordered)),
    }


def _cell_inventory(root_ids: Sequence[str]) -> dict[str, Any]:
    ordered = sorted(root_ids)
    return {
        "roots": len(ordered),
        "sha256": _sha256_bytes(_canonical_json(ordered)),
    }


def _opaque_corpus_rows(path: Path) -> _OpaqueCorpus:
    safe, lines = _snapshot_utf8_lines(path, "decision-label corpus")
    rows: list[_OpaqueLabelRow] = []
    by_root: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    component_splits: dict[str, set[str]] = defaultdict(set)
    sibling_roots: dict[str, set[str]] = defaultdict(set)
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        location = f"{safe}:{line_number}"
        routed = _OpaqueJsonRouter(
            line,
            wanted_strings=LABEL_STRING_FIELDS,
            wanted_raw=("schemaVersion",),
            location=location,
        ).route()
        if routed.keys != LABEL_FIELDS:
            raise ValueError(f"{location}: exact label field inventory changed")
        if routed.raw["schemaVersion"] != "1":
            raise ValueError(f"{location}: schemaVersion is not exact integer 1")
        routing = dict(routed.strings)
        if routing["kind"] != LABEL_KIND:
            raise ValueError(f"{location}: wrong label kind")
        if routing["split"] not in SPLITS or routing["phase"] not in PHASES:
            raise ValueError(f"{location}: invalid split or phase")
        if routing["parentSideToMove"] not in SIDES:
            raise ValueError(f"{location}: invalid parent side")
        if any(not routing[field].strip() for field in LABEL_STRING_FIELDS):
            raise ValueError(f"{location}: empty label identity")
        if _child_side(routing["childOfen"], location) == routing["parentSideToMove"]:
            raise ValueError(f"{location}: child side is not opposite parent side")
        by_root[routing["rootId"]].append(routing)
        component_splits[routing["leakageComponentId"]].add(routing["split"])
        sibling_roots[routing["childId"]].add(routing["rootId"])
        rows.append(_OpaqueLabelRow(location, line, routing))
    if not rows:
        raise ValueError("decision-label corpus is empty")
    roots: list[_StructuralRoot] = []
    for root_id, root_rows in by_root.items():
        if len(root_rows) != 4:
            raise ValueError(f"decision root {root_id!r} has {len(root_rows)}/4 siblings")
        for field in (
            "rootId",
            "leakageComponentId",
            "split",
            "phase",
            "parentSideToMove",
        ):
            if len({row[field] for row in root_rows}) != 1:
                raise ValueError(f"decision root {root_id!r} crosses {field}")
        sibling_ids = tuple(sorted(row["childId"] for row in root_rows))
        if len(set(sibling_ids)) != 4:
            raise ValueError(f"decision root {root_id!r} repeats a sibling id")
        first = root_rows[0]
        roots.append(
            _StructuralRoot(
                root_id=root_id,
                component_id=first["leakageComponentId"],
                split=first["split"],
                phase=first["phase"],
                root_side=first["parentSideToMove"],
                sibling_ids=sibling_ids,
            )
        )
    if any(len(values) != 1 for values in component_splits.values()):
        raise ValueError("leakage component occurs in multiple splits")
    if any(len(values) != 1 for values in sibling_roots.values()):
        raise ValueError("childId occurs in multiple roots")
    roots.sort(key=lambda item: item.root_id)
    root_inventories = {
        split: _inventory([root.record() for root in roots if root.split == split])
        for split in SPLITS
    }
    phase_side = {
        split: {
            cell: _cell_inventory(
                [
                    root.root_id
                    for root in roots
                    if root.split == split
                    and f"{root.phase}:{root.root_side}" == cell
                ]
            )
            for cell in _CELL_KEYS
        }
        for split in SPLITS
    }
    return _OpaqueCorpus(tuple(rows), tuple(roots), root_inventories, phase_side)


def _parse_component_map(path: Path) -> dict[str, dict[str, str]]:
    safe, lines = _snapshot_utf8_lines(path, "component map")
    result: dict[str, dict[str, str]] = {}
    source_roots: set[str] = set()
    source_group_routes: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        location = f"{safe}:{line_number}"
        value = _strict_json_loads(line, location=location)
        if not isinstance(value, dict):
            raise ValueError(f"{location}: component row is not an object")
        _exact_keys(value, COMPONENT_FIELDS, location)
        if (
            type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != SCHEMA_VERSION
            or type(value["kind"]) is not str
            or value["kind"] != COMPONENT_ROW_KIND
            or type(value["profileId"]) is not str
            or value["profileId"] != PROFILE_ID
        ):
            raise ValueError(f"{location}: component schema changed")
        for field in (
            "rootId",
            "leakageComponentId",
            "split",
            "sourceRootId",
            "sourceGroupId",
        ):
            if type(value[field]) is not str or not value[field]:
                raise ValueError(f"{location}: invalid {field}")
        if (
            value["split"] not in SPLITS
            or value["rootId"] in result
            or value["sourceRootId"] in source_roots
        ):
            raise ValueError(f"{location}: duplicate/invalid component row")
        result[value["rootId"]] = {
            field: value[field]
            for field in (
                "rootId",
                "leakageComponentId",
                "split",
                "sourceRootId",
                "sourceGroupId",
            )
        }
        source_roots.add(value["sourceRootId"])
        source_group_routes[value["sourceGroupId"]].add(
            (value["leakageComponentId"], value["split"])
        )
    if not result:
        raise ValueError("component map is empty")
    if any(len(routes) != 1 for routes in source_group_routes.values()):
        raise ValueError("one frozen source group was relabeled across components/splits")
    return result


UPSTREAM_PRELABEL_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "componentMap",
        "targetFreeRouting",
        "sourceRootManifest",
        "sourceChildrenManifest",
        "producer",
        "componentRows",
        "targetFieldsDecodedAtSeal",
        "targetFieldsEmittedAtSeal",
    }
)


def expected_upstream_prelabel_seal(
    *,
    component_map: Path,
    target_free_routing: Path,
    source_root_manifest: Path,
    source_children_manifest: Path,
    producer: Path,
    created_utc: str,
) -> dict[str, Any]:
    _parse_timestamp(created_utc, "upstream prelabel createdUtc")
    components = _parse_component_map(component_map)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": UPSTREAM_PRELABEL_SEAL_KIND,
        "profileId": PROFILE_ID,
        "status": "frozen-before-any-teacher-target-decode",
        "createdUtc": created_utc,
        "componentMap": _identity(component_map),
        "targetFreeRouting": _identity(target_free_routing),
        "sourceRootManifest": _identity(source_root_manifest),
        "sourceChildrenManifest": _identity(source_children_manifest),
        "producer": _identity(producer),
        "componentRows": len(components),
        "targetFieldsDecodedAtSeal": 0,
        "targetFieldsEmittedAtSeal": 0,
    }


def publish_upstream_prelabel_seal(path: Path, **kwargs: Any) -> dict[str, Any]:
    return _exclusive_json(path, expected_upstream_prelabel_seal(**kwargs))


def _verify_upstream_prelabel_seal(
    path: Path, *, component_map: Path, target_free_routing: Path | None = None
) -> dict[str, Any]:
    document = _load_json(path, "upstream prelabel seal")
    _exact_keys(document, UPSTREAM_PRELABEL_FIELDS, "upstream prelabel seal")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != UPSTREAM_PRELABEL_SEAL_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "frozen-before-any-teacher-target-decode"
        or type(document["targetFieldsDecodedAtSeal"]) is not int
        or document["targetFieldsDecodedAtSeal"] != 0
        or type(document["targetFieldsEmittedAtSeal"]) is not int
        or document["targetFieldsEmittedAtSeal"] != 0
    ):
        raise ValueError("upstream prelabel schema/status changed")
    expected = expected_upstream_prelabel_seal(
        component_map=component_map,
        target_free_routing=(
            target_free_routing
            if target_free_routing is not None
            else Path(
                _verify_identity_record(
                    document["targetFreeRouting"], "upstream target-free routing"
                )["path"]
            )
        ),
        source_root_manifest=Path(
            _verify_identity_record(
                document["sourceRootManifest"], "upstream source-root manifest"
            )["path"]
        ),
        source_children_manifest=Path(
            _verify_identity_record(
                document["sourceChildrenManifest"],
                "upstream source-children manifest",
            )["path"]
        ),
        producer=Path(
            _verify_identity_record(
                document["producer"], "upstream prelabel producer"
            )["path"]
        ),
        created_utc=document["createdUtc"],
    )
    if not _type_exact_equal(document, expected):
        raise ValueError("upstream prelabel seal differs from recomputation")
    return document


def _parse_upstream_teacher_labels(path: Path) -> dict[str, dict[str, Any]]:
    safe, lines = _snapshot_utf8_lines(path, "upstream teacher labels")
    rows: dict[str, dict[str, Any]] = {}
    by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        location = f"{safe}:{line_number}"
        value = _strict_json_loads(line, location=location)
        if not isinstance(value, dict):
            raise ValueError(f"{location}: upstream teacher row is not an object")
        _exact_keys(value, UPSTREAM_TEACHER_LABEL_FIELDS, location)
        if (
            type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != SCHEMA_VERSION
            or value["kind"] != UPSTREAM_TEACHER_LABEL_KIND
            or value["profileId"] != PROFILE_ID
        ):
            raise ValueError(f"{location}: upstream teacher schema changed")
        for field in (
            "rootId",
            "childId",
            "childOfen",
            "phase",
            "parentSideToMove",
        ):
            if type(value[field]) is not str or not value[field]:
                raise ValueError(f"{location}: invalid {field}")
        if value["phase"] not in PHASES or value["parentSideToMove"] not in SIDES:
            raise ValueError(f"{location}: invalid phase/side")
        if _child_side(value["childOfen"], location) == value["parentSideToMove"]:
            raise ValueError(f"{location}: child side is not opposite parent")
        _exact_int(value["deepRank"], f"{location} rank", 1, 4)
        _exact_int(value["deepRegretCp"], f"{location} regret", 0, REGRET_MAX_CP)
        root_score = _exact_int(
            value["deepScoreCpRoot"],
            f"{location} root score",
            -SCORE_ABS_LIMIT_CP,
            SCORE_ABS_LIMIT_CP,
        )
        child_score = _exact_int(
            value["deepScoreCpChildStm"],
            f"{location} child score",
            -SCORE_ABS_LIMIT_CP,
            SCORE_ABS_LIMIT_CP,
        )
        if root_score != -child_score or value["childId"] in rows:
            raise ValueError(f"{location}: duplicate child or incoherent scores")
        rows[value["childId"]] = value
        by_root[value["rootId"]].append(value)
    for root_id, siblings in by_root.items():
        if len(siblings) != 4 or sorted(row["deepRank"] for row in siblings) != [1, 2, 3, 4]:
            raise ValueError(f"upstream teacher root {root_id!r} is not exact four-child")
        ranked = sorted(siblings, key=lambda row: row["deepRank"])
        best = ranked[0]["deepScoreCpRoot"]
        for row in siblings:
            if best - row["deepScoreCpRoot"] != row["deepRegretCp"]:
                raise ValueError(f"upstream teacher root {root_id!r} regret changed")
    if not rows:
        raise ValueError("upstream teacher labels are empty")
    return rows


UPSTREAM_TEACHER_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "prelabelSeal",
        "componentMap",
        "labels",
        "producer",
        "rows",
        "childrenPerRoot",
    }
)


def expected_upstream_teacher_manifest(
    *,
    prelabel_seal: Path,
    component_map: Path,
    labels: Path,
    producer: Path,
    created_utc: str,
) -> dict[str, Any]:
    prelabel = _verify_upstream_prelabel_seal(
        prelabel_seal, component_map=component_map
    )
    created = _parse_timestamp(created_utc, "upstream teacher createdUtc")
    if created <= _parse_timestamp(prelabel["createdUtc"], "prelabel createdUtc"):
        raise ValueError("teacher completion must follow prelabel freeze")
    rows = _parse_upstream_teacher_labels(labels)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": UPSTREAM_TEACHER_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "complete-after-frozen-prelabel-authority",
        "createdUtc": created_utc,
        "prelabelSeal": _identity(prelabel_seal),
        "componentMap": _identity(component_map),
        "labels": _identity(labels),
        "producer": _identity(producer),
        "rows": len(rows),
        "childrenPerRoot": 4,
    }


def publish_upstream_teacher_manifest(path: Path, **kwargs: Any) -> dict[str, Any]:
    return _exclusive_json(path, expected_upstream_teacher_manifest(**kwargs))


def _verify_upstream_teacher_manifest(
    path: Path, *, prelabel_seal: Path, component_map: Path, labels: Path
) -> dict[str, Any]:
    document = _load_json(path, "upstream teacher completion")
    _exact_keys(
        document,
        UPSTREAM_TEACHER_MANIFEST_FIELDS,
        "upstream teacher completion",
    )
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != UPSTREAM_TEACHER_MANIFEST_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "complete-after-frozen-prelabel-authority"
        or type(document["rows"]) is not int
        or type(document["childrenPerRoot"]) is not int
        or document["childrenPerRoot"] != 4
    ):
        raise ValueError("upstream teacher completion schema/status changed")
    expected = expected_upstream_teacher_manifest(
        prelabel_seal=prelabel_seal,
        component_map=component_map,
        labels=labels,
        producer=Path(
            _verify_identity_record(
                document["producer"], "upstream teacher producer"
            )["path"]
        ),
        created_utc=document["createdUtc"],
    )
    if not _type_exact_equal(document, expected):
        raise ValueError("upstream teacher completion differs from recomputation")
    return document


LABEL_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "corpus",
        "componentMap",
        "rows",
        "childrenPerRoot",
        "rootInventories",
        "phaseSideInventories",
        "upstreamPrelabelSeal",
        "upstreamTeacherLabels",
        "upstreamTeacherManifest",
        "projectionProducer",
    }
)


def expected_label_manifest(
    *,
    corpus: Path,
    component_map: Path,
    upstream_prelabel_seal: Path,
    upstream_teacher_labels: Path,
    upstream_teacher_manifest: Path,
    projection_producer: Path,
    created_utc: str,
) -> dict[str, Any]:
    created = _parse_timestamp(created_utc, "label manifest createdUtc")
    prelabel = _verify_upstream_prelabel_seal(
        upstream_prelabel_seal, component_map=component_map
    )
    teacher_manifest = _verify_upstream_teacher_manifest(
        upstream_teacher_manifest,
        prelabel_seal=upstream_prelabel_seal,
        component_map=component_map,
        labels=upstream_teacher_labels,
    )
    if created <= _parse_timestamp(
        teacher_manifest["createdUtc"], "teacher completion createdUtc"
    ):
        raise ValueError("label projection manifest must follow teacher completion")
    opaque = _opaque_corpus_rows(corpus)
    mapped = _parse_component_map(component_map)
    teacher = _parse_upstream_teacher_labels(upstream_teacher_labels)
    projected: dict[str, dict[str, Any]] = {}
    for child_id, source in teacher.items():
        component = mapped.get(source["rootId"])
        if component is None:
            raise ValueError("teacher root is absent from frozen component map")
        projected[child_id] = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": LABEL_KIND,
            "rootId": source["rootId"],
            "leakageComponentId": component["leakageComponentId"],
            "split": component["split"],
            "childId": source["childId"],
            "childOfen": source["childOfen"],
            "phase": source["phase"],
            "parentSideToMove": source["parentSideToMove"],
            "deepRank": source["deepRank"],
            "deepRegretCp": source["deepRegretCp"],
            "deepScoreCpRoot": source["deepScoreCpRoot"],
            "deepScoreCpChildStm": source["deepScoreCpChildStm"],
        }
    actual: dict[str, dict[str, Any]] = {}
    for row in opaque.rows:
        value = _strict_json_loads(row.line, location=row.location)
        if not isinstance(value, dict) or value["childId"] in actual:
            raise ValueError("projected label row is invalid/duplicate")
        actual[value["childId"]] = value
    if not _type_exact_equal(actual, projected):
        raise ValueError("projected labels are not an exact upstream projection")
    structural = {
        root.root_id: (root.component_id, root.split) for root in opaque.roots
    }
    expected_structural = {
        root_id: (row["leakageComponentId"], row["split"])
        for root_id, row in mapped.items()
    }
    if structural != expected_structural:
        raise ValueError("projection root authority differs from frozen component map")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LABEL_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "sealed-exact-projected-label-authority",
        "createdUtc": created_utc,
        "corpus": _identity(corpus),
        "componentMap": _identity(component_map),
        "rows": len(opaque.rows),
        "childrenPerRoot": 4,
        "rootInventories": {
            split: dict(opaque.root_inventories[split]) for split in SPLITS
        },
        "phaseSideInventories": {
            split: {
                cell: dict(opaque.phase_side_inventories[split][cell])
                for cell in _CELL_KEYS
            }
            for split in SPLITS
        },
        "upstreamPrelabelSeal": _identity(upstream_prelabel_seal),
        "upstreamTeacherLabels": _identity(upstream_teacher_labels),
        "upstreamTeacherManifest": _identity(upstream_teacher_manifest),
        "projectionProducer": _identity(projection_producer),
    }


def publish_label_manifest(
    path: Path, **kwargs: Any
) -> dict[str, Any]:
    return _exclusive_json(path, expected_label_manifest(**kwargs))


def _verify_label_authority(
    *, corpus: Path, label_manifest: Path, component_map: Path
) -> tuple[_OpaqueCorpus, dict[str, Any]]:
    """Verify the frozen projection without decoding held-out target fields.

    The exact upstream projection was completed before preregistration.  Once
    that event is frozen, downstream consumers authenticate its byte identities
    and opaque structural inventories.  Re-running the target-bearing teacher
    projection here would itself reveal held-out ranks/scores before the
    one-shot claim, invalidating the information boundary.
    """

    document = _load_json(label_manifest, "label manifest")
    _exact_keys(document, LABEL_MANIFEST_FIELDS, "label manifest")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != LABEL_MANIFEST_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "sealed-exact-projected-label-authority"
    ):
        raise ValueError("label manifest schema/status changed")
    created = _parse_timestamp(document["createdUtc"], "label manifest createdUtc")
    corpus_identity = _identity(corpus)
    component_identity = _identity(component_map)
    if not _type_exact_equal(document["corpus"], corpus_identity):
        raise ValueError("label manifest corpus identity changed")
    if not _type_exact_equal(document["componentMap"], component_identity):
        raise ValueError("label manifest component-map identity changed")
    prelabel_path = Path(
        _verify_identity_record(
            document["upstreamPrelabelSeal"], "label upstream prelabel"
        )["path"]
    )
    teacher_labels = _verify_identity_record(
        document["upstreamTeacherLabels"], "label upstream teacher labels"
    )
    teacher_manifest_path = Path(
        _verify_identity_record(
            document["upstreamTeacherManifest"], "label upstream teacher manifest"
        )["path"]
    )
    _verify_identity_record(document["projectionProducer"], "label projection producer")
    prelabel = _verify_upstream_prelabel_seal(
        prelabel_path, component_map=component_map
    )
    teacher_manifest = _load_json(
        teacher_manifest_path, "upstream teacher completion"
    )
    _exact_keys(
        teacher_manifest,
        UPSTREAM_TEACHER_MANIFEST_FIELDS,
        "upstream teacher completion",
    )
    if (
        type(teacher_manifest["schemaVersion"]) is not int
        or teacher_manifest["schemaVersion"] != SCHEMA_VERSION
        or teacher_manifest["kind"] != UPSTREAM_TEACHER_MANIFEST_KIND
        or teacher_manifest["profileId"] != PROFILE_ID
        or teacher_manifest["status"]
        != "complete-after-frozen-prelabel-authority"
        or type(teacher_manifest["rows"]) is not int
        or type(teacher_manifest["childrenPerRoot"]) is not int
        or teacher_manifest["childrenPerRoot"] != 4
        or not _type_exact_equal(
            teacher_manifest["prelabelSeal"], _identity(prelabel_path)
        )
        or not _type_exact_equal(
            teacher_manifest["componentMap"], component_identity
        )
        or not _type_exact_equal(teacher_manifest["labels"], teacher_labels)
    ):
        raise ValueError("upstream teacher completion authority changed")
    _verify_identity_record(
        teacher_manifest["producer"], "upstream teacher producer"
    )
    teacher_created = _parse_timestamp(
        teacher_manifest["createdUtc"], "upstream teacher createdUtc"
    )
    if (
        teacher_created
        <= _parse_timestamp(prelabel["createdUtc"], "prelabel createdUtc")
        or created <= teacher_created
    ):
        raise ValueError("frozen label authority chronology changed")

    opaque = _opaque_corpus_rows(corpus)
    components = _parse_component_map(component_map)
    actual_structural = {
        root.root_id: (root.component_id, root.split) for root in opaque.roots
    }
    expected_structural = {
        root_id: (row["leakageComponentId"], row["split"])
        for root_id, row in components.items()
    }
    if actual_structural != expected_structural:
        raise ValueError("label projection differs from frozen component authority")
    expected_inventories = {
        "rows": len(opaque.rows),
        "rootInventories": {
            split: dict(opaque.root_inventories[split]) for split in SPLITS
        },
        "phaseSideInventories": {
            split: {
                cell: dict(opaque.phase_side_inventories[split][cell])
                for cell in _CELL_KEYS
            }
            for split in SPLITS
        },
    }
    if (
        type(document["rows"]) is not int
        or document["rows"] != expected_inventories["rows"]
        or type(document["childrenPerRoot"]) is not int
        or document["childrenPerRoot"] != 4
        or teacher_manifest["rows"] != len(opaque.rows)
        or not _type_exact_equal(
            document["rootInventories"], expected_inventories["rootInventories"]
        )
        or not _type_exact_equal(
            document["phaseSideInventories"],
            expected_inventories["phaseSideInventories"],
        )
    ):
        raise ValueError("label manifest opaque inventories changed")
    return opaque, document


def static_hce_options_document() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HCE_OPTIONS_KIND,
        "profileId": PROFILE_ID,
        "engineProtocol": "UCI",
        "executableMode": "--evaluate-handcrafted-stream",
        "uciVariant": "omega",
        "omegaNnueFile": "",
        "perspective": STATIC_HCE_PERSPECTIVE,
        "commandArgumentsBeforeRunner": ["-I", "-B"],
        "stdinProtocol": "one normalized child OFEN per line",
        "stdoutProtocol": "one exact bounded integer centipawn score per line",
    }


def _verify_hce_options(path: Path) -> dict[str, Any]:
    document = _load_json(path, "static-HCE options")
    _exact_keys(document, HCE_OPTIONS_FIELDS, "static-HCE options")
    expected = static_hce_options_document()
    if not _type_exact_equal(document, expected):
        raise ValueError("static-HCE options differ from exact frozen contract")
    return document


def _parse_hce_projection(
    path: Path, expected_child_ids: set[str]
) -> dict[str, int]:
    safe, lines = _snapshot_utf8_lines(path, "static-HCE projection")
    result: dict[str, int] = {}
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        location = f"{safe}:{line_number}"
        value = _strict_json_loads(line, location=location)
        if not isinstance(value, dict):
            raise ValueError(f"{location}: HCE row is not an object")
        _exact_keys(value, HCE_FIELDS, location)
        if (
            type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != SCHEMA_VERSION
            or value["kind"] != HCE_ROW_KIND
            or value["profileId"] != PROFILE_ID
            or type(value["childId"]) is not str
            or not value["childId"]
        ):
            raise ValueError(f"{location}: HCE row schema changed")
        score = _exact_int(
            value["handcraftedCpChildStm"],
            f"{location} handcraftedCpChildStm",
            -SCORE_ABS_LIMIT_CP,
            SCORE_ABS_LIMIT_CP,
        )
        if value["childId"] in result:
            raise ValueError(f"{location}: duplicate HCE childId")
        result[value["childId"]] = score
    if set(result) != expected_child_ids:
        raise ValueError("static-HCE projection child inventory differs from corpus")
    return result


def _run_exact_evaluator(
    *,
    engine: Path,
    runner: Path,
    mode: str,
    ofens: Sequence[str],
    model: Path | None = None,
) -> dict[str, int]:
    engine_identity = _identity(engine)
    runner_identity = _identity(runner)
    engine_path = Path(engine_identity["path"])
    runner_path = Path(runner_identity["path"])
    model_identity = _identity(model) if model is not None else None
    command = [
        str(engine_path),
        "-I",
        "-B",
        str(runner_path),
        mode,
    ]
    if model is not None:
        command.append(str(model_identity["path"]))  # type: ignore[index]
    completed = subprocess.run(
        command,
        input="".join(ofen + "\n" for ofen in ofens),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=max(60, len(ofens) // 4),
        cwd=runner_path.parent,
        env={},
    )
    if (
        not _type_exact_equal(_identity(engine_path), engine_identity)
        or not _type_exact_equal(_identity(runner_path), runner_identity)
        or (
            model_identity is not None
            and not _type_exact_equal(
                _identity(Path(model_identity["path"])), model_identity
            )
        )
    ):
        raise ValueError("pinned evaluator dependency changed during execution")
    if completed.returncode != 0:
        raise ValueError(
            "pinned evaluator failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    if completed.stderr.strip():
        raise ValueError("pinned evaluator emitted stderr")
    lines = completed.stdout.splitlines()
    if len(lines) != len(ofens):
        raise ValueError(
            f"pinned evaluator returned {len(lines)}/{len(ofens)} rows"
        )
    result: dict[str, int] = {}
    for index, line in enumerate(lines):
        text = line.strip()
        if not text or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", text):
            raise ValueError(f"pinned evaluator row {index + 1} is not an exact integer")
        value = int(text)
        if value < -SCORE_ABS_LIMIT_CP or value > SCORE_ABS_LIMIT_CP:
            raise ValueError(f"pinned evaluator row {index + 1} is out of bounds")
        result[str(index)] = value
    return result


HCE_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "labelManifest",
        "corpus",
        "componentMap",
        "projection",
        "engine",
        "runner",
        "options",
        "perspective",
        "rows",
        "rootInventories",
    }
)


def expected_static_hce_manifest(
    *,
    corpus: Path,
    label_manifest: Path,
    component_map: Path,
    projection: Path,
    engine: Path,
    runner: Path,
    options: Path,
    created_utc: str,
) -> dict[str, Any]:
    created = _parse_timestamp(created_utc, "static-HCE manifest createdUtc")
    opaque, label_document = _verify_label_authority(
        corpus=corpus,
        label_manifest=label_manifest,
        component_map=component_map,
    )
    if created <= _parse_timestamp(label_document["createdUtc"], "label createdUtc"):
        raise ValueError("static-HCE manifest must follow label manifest")
    _verify_hce_options(options)
    child_ids = {row.routing["childId"] for row in opaque.rows}
    scores = _parse_hce_projection(projection, child_ids)
    ordered_rows = sorted(opaque.rows, key=lambda row: row.routing["childId"])
    replay = _run_exact_evaluator(
        engine=engine,
        runner=runner,
        mode="--evaluate-handcrafted-stream",
        ofens=[" ".join(row.routing["childOfen"].split()) for row in ordered_rows],
    )
    replay_by_child = {
        row.routing["childId"]: replay[str(index)]
        for index, row in enumerate(ordered_rows)
    }
    if replay_by_child != scores:
        raise ValueError("static-HCE projection differs from fresh pinned-engine replay")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HCE_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "sealed-exact-static-hce-projection",
        "createdUtc": created_utc,
        "labelManifest": _identity(label_manifest),
        "corpus": _identity(corpus),
        "componentMap": _identity(component_map),
        "projection": _identity(projection),
        "engine": _identity(engine),
        "runner": _identity(runner),
        "options": _identity(options),
        "perspective": STATIC_HCE_PERSPECTIVE,
        "rows": len(scores),
        "rootInventories": {
            split: dict(opaque.root_inventories[split]) for split in SPLITS
        },
    }


def publish_static_hce_manifest(path: Path, **kwargs: Any) -> dict[str, Any]:
    return _exclusive_json(path, expected_static_hce_manifest(**kwargs))


@dataclass(frozen=True)
class TrainingAuthority:
    label_manifest: Path
    component_map: Path
    hce_projection: Path
    hce_manifest: Path
    hce_engine: Path
    hce_runner: Path
    hce_options: Path


@dataclass(frozen=True)
class VerifiedAuthority:
    source: TrainingAuthority
    corpus_path: Path
    opaque: _OpaqueCorpus
    hce_by_child: Mapping[str, int]
    binding: Mapping[str, Any]
    binding_sha256: str
    root_ids_by_split: Mapping[str, tuple[str, ...]]
    label_created_utc: str
    hce_created_utc: str


def verify_training_authority(
    *, corpus: Path, authority: TrainingAuthority
) -> VerifiedAuthority:
    opaque, label_document = _verify_label_authority(
        corpus=corpus,
        label_manifest=authority.label_manifest,
        component_map=authority.component_map,
    )
    hce_document = _load_json(authority.hce_manifest, "static-HCE manifest")
    _exact_keys(hce_document, HCE_MANIFEST_FIELDS, "static-HCE manifest")
    if (
        type(hce_document["schemaVersion"]) is not int
        or hce_document["schemaVersion"] != SCHEMA_VERSION
        or hce_document["kind"] != HCE_MANIFEST_KIND
        or hce_document["profileId"] != PROFILE_ID
        or hce_document["status"] != "sealed-exact-static-hce-projection"
    ):
        raise ValueError("static-HCE manifest schema/status changed")
    expected = expected_static_hce_manifest(
        corpus=corpus,
        label_manifest=authority.label_manifest,
        component_map=authority.component_map,
        projection=authority.hce_projection,
        engine=authority.hce_engine,
        runner=authority.hce_runner,
        options=authority.hce_options,
        created_utc=hce_document["createdUtc"],
    )
    if not _type_exact_equal(hce_document, expected):
        raise ValueError("static-HCE manifest differs from recomputation")
    child_ids = {row.routing["childId"] for row in opaque.rows}
    hce = _parse_hce_projection(authority.hce_projection, child_ids)
    binding = {
        "corpus": _identity(corpus),
        "labelManifest": _identity(authority.label_manifest),
        "componentMap": _identity(authority.component_map),
        "staticHceProjection": _identity(authority.hce_projection),
        "staticHceManifest": _identity(authority.hce_manifest),
        "staticHceEngine": _identity(authority.hce_engine),
        "staticHceRunner": _identity(authority.hce_runner),
        "staticHceOptions": _identity(authority.hce_options),
        "staticHcePerspective": STATIC_HCE_PERSPECTIVE,
        "rootInventories": {
            split: dict(opaque.root_inventories[split]) for split in SPLITS
        },
        "phaseSideInventories": {
            split: {
                cell: dict(opaque.phase_side_inventories[split][cell])
                for cell in _CELL_KEYS
            }
            for split in SPLITS
        },
    }
    return VerifiedAuthority(
        source=authority,
        corpus_path=_safe_existing_file(corpus),
        opaque=opaque,
        hce_by_child=hce,
        binding=binding,
        binding_sha256=_document_identity(binding)["sha256"],
        root_ids_by_split={
            split: tuple(root.root_id for root in opaque.roots if root.split == split)
            for split in SPLITS
        },
        label_created_utc=label_document["createdUtc"],
        hce_created_utc=hce_document["createdUtc"],
    )


def _reverify_authority(authority: VerifiedAuthority) -> VerifiedAuthority:
    """Reject constructed or mutated snapshots before any gated operation."""

    current = verify_training_authority(
        corpus=authority.corpus_path, authority=authority.source
    )
    if (
        authority.corpus_path != current.corpus_path
        or authority.binding_sha256 != current.binding_sha256
        or not _type_exact_equal(authority.binding, current.binding)
        or not _type_exact_equal(
            dict(authority.root_ids_by_split), dict(current.root_ids_by_split)
        )
        or not _type_exact_equal(dict(authority.hce_by_child), dict(current.hce_by_child))
        or authority.label_created_utc != current.label_created_utc
        or authority.hce_created_utc != current.hce_created_utc
    ):
        raise ValueError("verified training-authority snapshot was constructed or mutated")
    return current


INITIALIZER_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "resultInformationRead",
        "model",
        "producer",
        "selectionMode",
        "selectedCatalogIndex",
        "selectionSeal",
        "sourceClosure",
        "orderedCatalog",
        "fallbackProtocol",
    }
)
INITIALIZER_CATALOG_ENTRY_FIELDS = frozenset(
    {"sourceId", "selectionSeal", "closure", "model", "promotionStatus"}
)
PREREGISTRATION_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "namespace",
        "artifactPaths",
        "protocol",
        "contractSource",
        "upstreamCapsule",
        "upstreamVerifierExecutable",
        "upstreamVerifierRunner",
        "upstreamVerifierOptions",
        "corpus",
        "labelManifest",
        "componentMap",
        "staticHceProjection",
        "staticHceManifest",
        "staticHceEngine",
        "staticHceRunner",
        "staticHceOptions",
        "trainerExecutable",
        "trainerRunner",
        "runtimeManifest",
        "trainerCommandProtocol",
        "evaluatorExecutable",
        "evaluatorRunner",
        "evaluatorOptions",
        "initializerModel",
        "initializerManifest",
        "optimizerProtocol",
        "candidateRecipes",
        "primaryTrainingSeeds",
        "robustnessTrainingSeeds",
        "authoritySha256",
        "rootInventories",
        "phaseSideInventories",
        "resultInformationRead",
        "heldOutTargetRowsDecodedAtFreeze",
        "heldOutTargetFieldsDecodedAtFreeze",
    }
)


@dataclass(frozen=True)
class Generation6Registry:
    namespace: Path
    preregistration: Path
    document: Mapping[str, Any]
    authority: VerifiedAuthority
    upstream_verification: Mapping[str, Any]
    batch_order_rows_by_model: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict, compare=False, repr=False
    )
    batch_order_payload_by_model: dict[str, bytes] = field(
        default_factory=dict, compare=False, repr=False
    )
    batch_order_inputs_by_model: dict[str, tuple[dict[str, Any], dict[str, Any]]] = field(
        default_factory=dict, compare=False, repr=False
    )


def _canonical_slot(registry: Generation6Registry, key: str) -> Path:
    if key not in CANONICAL_ARTIFACT_PATHS:
        raise ValueError(f"unknown Generation-6 artifact slot {key!r}")
    relative = Path(CANONICAL_ARTIFACT_PATHS[key])
    path = _lexical_absolute(registry.namespace / relative)
    try:
        path.relative_to(registry.namespace)
    except ValueError as error:
        raise ValueError("canonical artifact escaped namespace") from error
    return path


def _namespace_inventory(namespace: Path) -> tuple[set[str], set[str]]:
    root = _safe_path(namespace, regular_file=False)
    if not root.is_dir():
        raise FileNotFoundError(f"Generation-6 namespace does not exist: {root}")
    files: set[str] = set()
    directories: set[str] = set()
    casefolded: set[str] = set()
    stack = [root]
    while stack:
        parent = stack.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                absolute = Path(entry.path)
                # On Windows some DirEntry.stat() builds report zeroed inode /
                # link fields; lstat on the lexical path is the authority used
                # everywhere else in this module.
                info = os.lstat(absolute)
                relative = absolute.relative_to(root).as_posix()
                folded = relative.casefold()
                if folded in casefolded:
                    raise ValueError(f"case-fold collision in namespace: {relative}")
                casefolded.add(folded)
                if entry.is_symlink() or _is_reparse(info):
                    raise ValueError(f"reparse artifact in namespace: {relative}")
                if stat.S_ISDIR(info.st_mode):
                    directories.add(relative)
                    stack.append(absolute)
                elif stat.S_ISREG(info.st_mode):
                    if getattr(info, "st_nlink", 1) != 1:
                        raise ValueError(f"hard-linked artifact in namespace: {relative}")
                    files.add(relative)
                else:
                    raise ValueError(f"special artifact in namespace: {relative}")
    return files, directories


def _audit_namespace(
    registry: Generation6Registry, *, exact_files: set[str] | None = None
) -> set[str]:
    files, directories = _namespace_inventory(registry.namespace)
    allowed_files = {"00-preregistration.json", *CANONICAL_ARTIFACT_PATHS.values()}
    allowed_directories = {
        parent.as_posix()
        for relative in allowed_files
        for parent in Path(relative).parents
        if parent.as_posix() != "."
    }
    if not files <= allowed_files:
        raise ValueError(
            f"unregistered namespace artifacts: {sorted(files - allowed_files)}"
        )
    if not directories <= allowed_directories:
        raise ValueError(
            f"unregistered namespace directories: "
            f"{sorted(directories - allowed_directories)}"
        )
    required = {"00-preregistration.json"} if exact_files is None else exact_files
    if exact_files is not None and files != required:
        raise ValueError(
            "namespace stage inventory changed: "
            f"missing={sorted(required - files)} extra={sorted(files - required)}"
        )
    if exact_files is not None:
        required_directories = {
            parent.as_posix()
            for relative in required
            for parent in Path(relative).parents
            if parent.as_posix() != "."
        }
        if directories != required_directories:
            raise ValueError(
                "namespace directory inventory changed: "
                f"missing={sorted(required_directories - directories)} "
                f"extra={sorted(directories - required_directories)}"
            )
    if "00-preregistration.json" not in files:
        raise ValueError("canonical preregistration is absent")
    return files


def evaluator_options_document() -> dict[str, Any]:
    return dict(EVALUATOR_OPTIONS)


def upstream_verifier_options_document() -> dict[str, Any]:
    return dict(UPSTREAM_VERIFIER_OPTIONS)


RUNTIME_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "pythonExecutable",
        "pythonVersion",
        "pythonImplementation",
        "byteOrder",
        "numpyVersion",
        "numpyModule",
        "numpyCompiledCore",
        "deterministicEnvironment",
    }
)
DETERMINISTIC_SUBPROCESS_ENVIRONMENT: Mapping[str, str] = {}


def runtime_manifest_document() -> dict[str, Any]:
    compiled_core = Path(np._core._multiarray_umath.__file__)  # type: ignore[attr-defined]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RUNTIME_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "pythonExecutable": _identity(Path(sys.executable)),
        "pythonVersion": sys.version,
        "pythonImplementation": sys.implementation.name,
        "byteOrder": sys.byteorder,
        "numpyVersion": np.__version__,
        "numpyModule": _identity(Path(np.__file__)),
        "numpyCompiledCore": _identity(compiled_core),
        "deterministicEnvironment": dict(DETERMINISTIC_SUBPROCESS_ENVIRONMENT),
    }


def _verify_runtime_manifest(path: Path) -> dict[str, Any]:
    document = _load_json(path, "Generation-6 runtime manifest")
    _exact_keys(document, RUNTIME_MANIFEST_FIELDS, "Generation-6 runtime manifest")
    if not _type_exact_equal(document, runtime_manifest_document()):
        raise ValueError("Generation-6 runtime/dependency manifest changed")
    return document


def _verify_initializer_manifest(path: Path, model: Path) -> dict[str, Any]:
    document = _load_json(path, "initializer manifest")
    _exact_keys(document, INITIALIZER_MANIFEST_FIELDS, "initializer manifest")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != INITIALIZER_MANIFEST_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "frozen-pre-g6-initializer-selection"
        or type(document["resultInformationRead"]) is not bool
        or document["resultInformationRead"] is not False
        or document["selectionMode"] not in INITIALIZER_SELECTION_MODES
        or not _type_exact_equal(document["model"], _identity(model))
        or not _type_exact_equal(
            document["fallbackProtocol"], INITIALIZER_FALLBACK_PROTOCOL
        )
        or type(document["orderedCatalog"]) is not list
        or len(document["orderedCatalog"]) != len(INITIALIZER_ORDERED_CATALOG)
    ):
        raise ValueError("initializer manifest changed")
    _parse_timestamp(document["createdUtc"], "initializer createdUtc")
    _verify_identity_record(document["producer"], "initializer producer")
    _verify_identity_record(document["selectionSeal"], "initializer selection seal")
    _verify_identity_record(document["sourceClosure"], "initializer source closure")
    for index, (entry, source_id) in enumerate(
        zip(document["orderedCatalog"], INITIALIZER_ORDERED_CATALOG)
    ):
        if not isinstance(entry, dict):
            raise ValueError(f"initializer catalog entry {index} is not an object")
        _exact_keys(
            entry,
            INITIALIZER_CATALOG_ENTRY_FIELDS,
            f"initializer catalog entry {index}",
        )
        if (
            entry["sourceId"] != source_id
            or entry["promotionStatus"]
            not in ("promoted", "failed", "aborted", "unavailable")
        ):
            raise ValueError("initializer ordered catalog changed")
        _verify_identity_record(
            entry["closure"], f"initializer catalog {source_id} closure"
        )
        for optional in ("selectionSeal", "model"):
            if entry[optional] is not None:
                _verify_identity_record(
                    entry[optional], f"initializer catalog {source_id} {optional}"
                )
    selected_index = document["selectedCatalogIndex"]
    if document["selectionMode"] == "promoted-prior":
        if (
            type(selected_index) is not int
            or selected_index < 0
            or selected_index >= len(document["orderedCatalog"])
        ):
            raise ValueError("promoted initializer has no selected catalog index")
        selected = document["orderedCatalog"][selected_index]
        first_promoted = next(
            (
                index
                for index, entry in enumerate(document["orderedCatalog"])
                if entry["promotionStatus"] == "promoted"
            ),
            None,
        )
        if (
            selected["promotionStatus"] != "promoted"
            or selected_index != first_promoted
            or not _type_exact_equal(selected["model"], document["model"])
            or not _type_exact_equal(
                selected["closure"], document["sourceClosure"]
            )
        ):
            raise ValueError("promoted initializer catalog cross-links changed")
    elif selected_index is not None or any(
        entry["promotionStatus"] == "promoted"
        for entry in document["orderedCatalog"]
    ):
        raise ValueError("fallback initializer is not the no-promoted-entry case")
    return document


def verify_canonical_namespace() -> Generation6Registry:
    """Authenticate the unique preregistered Generation-6 lineage.

    Production callers cannot supply or replace a namespace/preregistration
    path.  Isolation tests import an unchanged copy of this module beneath a
    temporary repository root, so the same immutable path derivation applies.
    """

    repository = Path(__file__).resolve().parents[2]
    namespace = _lexical_absolute(repository / "build-msvc" / "king-state-v6")
    preregistration = namespace / "00-preregistration.json"
    if preregistration != namespace / "00-preregistration.json":
        raise ValueError("preregistration is not the unique namespace genesis slot")
    document = _load_json(preregistration, "Generation-6 preregistration")
    _exact_keys(document, PREREGISTRATION_FIELDS, "Generation-6 preregistration")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != PREREGISTRATION_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"]
        != "frozen-single-lineage-before-generation6-training"
        or type(document["namespace"]) is not str
        or document["namespace"] != str(namespace)
        or type(document["resultInformationRead"]) is not bool
        or document["resultInformationRead"] is not False
        or type(document["heldOutTargetRowsDecodedAtFreeze"]) is not int
        or document["heldOutTargetRowsDecodedAtFreeze"] != 0
        or type(document["heldOutTargetFieldsDecodedAtFreeze"]) is not int
        or document["heldOutTargetFieldsDecodedAtFreeze"] != 0
        or not _type_exact_equal(
            document["artifactPaths"], dict(CANONICAL_ARTIFACT_PATHS)
        )
        or not _type_exact_equal(document["optimizerProtocol"], OPTIMIZER_PROTOCOL)
        or not _type_exact_equal(
            document["candidateRecipes"],
            {candidate: dict(CANDIDATE_RECIPES[candidate]) for candidate in CANDIDATES},
        )
        or not _type_exact_equal(
            document["trainerCommandProtocol"], TRAINER_COMMAND_PROTOCOL
        )
    ):
        raise ValueError("Generation-6 preregistration contract changed")
    created = _parse_timestamp(document["createdUtc"], "preregistration createdUtc")
    expected_primary = {
        candidate: _domain_seed(
            PRIMARY_TRAINING_SEED_BASE, "primary-training", candidate
        )
        for candidate in CANDIDATES
    }
    expected_robust = {
        candidate: _domain_seed(
            ROBUSTNESS_TRAINING_SEED_BASE, "robustness-training", candidate
        )
        for candidate in CANDIDATES
    }
    if not _type_exact_equal(document["primaryTrainingSeeds"], expected_primary):
        raise ValueError("preregistered primary seeds changed")
    if not _type_exact_equal(document["robustnessTrainingSeeds"], expected_robust):
        raise ValueError("preregistered robustness seeds changed")

    identities = {}
    for field in (
        "protocol",
        "contractSource",
        "upstreamCapsule",
        "upstreamVerifierExecutable",
        "upstreamVerifierRunner",
        "upstreamVerifierOptions",
        "corpus",
        "labelManifest",
        "componentMap",
        "staticHceProjection",
        "staticHceManifest",
        "staticHceEngine",
        "staticHceRunner",
        "staticHceOptions",
        "trainerExecutable",
        "trainerRunner",
        "runtimeManifest",
        "evaluatorExecutable",
        "evaluatorRunner",
        "evaluatorOptions",
        "initializerModel",
        "initializerManifest",
    ):
        identities[field] = _verify_identity_record(
            document[field], f"preregistered {field}"
        )
    if not _type_exact_equal(identities["contractSource"], _identity(Path(__file__))):
        raise ValueError("preregistration does not bind this exact contract source")
    expected_capsule = (
        repository
        / "build-msvc"
        / "data-generation"
        / "omega-decision-v3"
        / "capsule.closure.json"
    )
    if Path(identities["upstreamCapsule"]["path"]) != expected_capsule:
        raise ValueError("upstream capsule is outside its canonical v3 path")
    expected_upstream_verifier = (
        repository
        / "tools"
        / "omega_nnue"
        / "verify_omega_decision_v3_upstream.py"
    )
    if Path(identities["upstreamVerifierRunner"]["path"]) != expected_upstream_verifier:
        raise ValueError("upstream verifier is outside its reviewed canonical path")
    protocol = _load_json(Path(identities["protocol"]["path"]), "G6 protocol")
    if not _type_exact_equal(protocol, protocol_document()):
        raise ValueError("preregistered protocol document changed")
    evaluator_options = _load_json(
        Path(identities["evaluatorOptions"]["path"]), "evaluator options"
    )
    if not _type_exact_equal(evaluator_options, evaluator_options_document()):
        raise ValueError("preregistered evaluator options changed")
    upstream_verifier_options = _load_json(
        Path(identities["upstreamVerifierOptions"]["path"]),
        "upstream verifier options",
    )
    if not _type_exact_equal(
        upstream_verifier_options, upstream_verifier_options_document()
    ):
        raise ValueError("preregistered upstream verifier options changed")
    runtime_document = _verify_runtime_manifest(
        Path(identities["runtimeManifest"]["path"])
    )
    for executable_field in (
        "upstreamVerifierExecutable",
        "trainerExecutable",
        "evaluatorExecutable",
    ):
        if not _type_exact_equal(
            identities[executable_field], runtime_document["pythonExecutable"]
        ):
            raise ValueError(
                f"{executable_field} differs from the pinned runtime executable"
            )
    initializer = _verify_initializer_manifest(
        Path(identities["initializerManifest"]["path"]),
        Path(identities["initializerModel"]["path"]),
    )
    authority = verify_training_authority(
        corpus=Path(identities["corpus"]["path"]),
        authority=TrainingAuthority(
            label_manifest=Path(identities["labelManifest"]["path"]),
            component_map=Path(identities["componentMap"]["path"]),
            hce_projection=Path(identities["staticHceProjection"]["path"]),
            hce_manifest=Path(identities["staticHceManifest"]["path"]),
            hce_engine=Path(identities["staticHceEngine"]["path"]),
            hce_runner=Path(identities["staticHceRunner"]["path"]),
            hce_options=Path(identities["staticHceOptions"]["path"]),
        ),
    )
    _, upstream_verification = _verify_upstream_capsule(
        Path(identities["upstreamCapsule"]["path"]),
        authority=authority,
        preregistration=document,
    )
    if (
        document["authoritySha256"] != authority.binding_sha256
        or not _type_exact_equal(
            document["rootInventories"], authority.binding["rootInventories"]
        )
        or not _type_exact_equal(
            document["phaseSideInventories"],
            authority.binding["phaseSideInventories"],
        )
        or created
        <= _parse_timestamp(authority.hce_created_utc, "HCE createdUtc")
        or created
        <= _parse_timestamp(initializer["createdUtc"], "initializer createdUtc")
    ):
        raise ValueError("preregistered authority/chronology changed")
    registry = Generation6Registry(
        namespace=namespace,
        preregistration=preregistration,
        document=document,
        authority=authority,
        upstream_verification=upstream_verification,
    )
    _audit_namespace(registry)
    return registry


def _utc_after(*values: str) -> str:
    now = datetime.now(timezone.utc)
    latest = max(
        (_parse_timestamp(value, "predecessor createdUtc") for value in values),
        default=now - timedelta(microseconds=1),
    )
    if now <= latest:
        now = latest + timedelta(microseconds=1)
    return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _relative_slot(key: str) -> str:
    return CANONICAL_ARTIFACT_PATHS[key]


_GENESIS_FILES = {"00-preregistration.json"}
_AUTHORITY_KEYS = (
    "authorityClaim",
    "authorityUpstreamVerification",
    "authorityTrainCorpus",
    "authorityValidationCorpus",
    "authorityTrainHce",
    "authorityValidationHce",
    "authorityInitializerModel",
    "authorityManifest",
)
_AUTHORITY_FILES = _GENESIS_FILES | {_relative_slot(key) for key in _AUTHORITY_KEYS}


def _ensure_slot_parent(registry: Generation6Registry, key: str) -> Path:
    path = _canonical_slot(registry, key)
    parent = path.parent
    if parent == registry.namespace:
        return path
    if parent.parent != registry.namespace:
        raise ValueError("nested canonical parent creation is not supported")
    if parent.exists():
        _safe_path(parent, regular_file=False)
        if not parent.is_dir():
            raise ValueError("canonical artifact parent is not a directory")
    else:
        os.mkdir(parent, 0o755)
        _safe_path(parent, regular_file=False)
    return path


AUTHORITY_CLAIM_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "upstreamCapsule",
        "authoritySha256",
        "resultInformationRead",
        "heldOutTargetRowsDecodedAtClaim",
        "heldOutTargetFieldsDecodedAtClaim",
    }
)
AUTHORITY_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "upstreamCapsule",
        "upstreamVerification",
        "claim",
        "sourceCorpus",
        "sourceStaticHce",
        "sourceInitializerModel",
        "trainCorpus",
        "validationCorpus",
        "trainStaticHce",
        "validationStaticHce",
        "initializerModel",
        "rootInventories",
        "phaseSideInventories",
        "heldOutMaterialized",
        "targetFieldsDecodedDuringMaterialization",
    }
)


def _authority_claim_document(registry: Generation6Registry, created: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUTHORITY_CLAIM_KIND,
        "profileId": PROFILE_ID,
        "status": "claimed-before-authority-source-read",
        "createdUtc": created,
        "preregistration": _identity(registry.preregistration),
        "upstreamCapsule": registry.document["upstreamCapsule"],
        "authoritySha256": registry.authority.binding_sha256,
        "resultInformationRead": False,
        "heldOutTargetRowsDecodedAtClaim": 0,
        "heldOutTargetFieldsDecodedAtClaim": 0,
    }


def materialize_canonical_authority() -> dict[str, Any]:
    """Copy only train/validation targets into the unique formal namespace."""

    registry = verify_canonical_namespace()
    _audit_namespace(registry, exact_files=set(_GENESIS_FILES))
    claim_created = _utc_after(registry.document["createdUtc"])
    claim_path = _ensure_slot_parent(registry, "authorityClaim")
    _exclusive_json(claim_path, _authority_claim_document(registry, claim_created))
    upstream_verification_path = _canonical_slot(
        registry, "authorityUpstreamVerification"
    )
    _exclusive_json(
        upstream_verification_path, dict(registry.upstream_verification)
    )

    # Opaque routing identifies the split without decoding deepRank/regret/scores.
    source = registry.authority.opaque
    corpus_payloads: dict[str, bytes] = {}
    for split in ("train", "validation"):
        corpus_payloads[split] = "".join(
            row.line for row in source.rows if row.routing["split"] == split
        ).encode("utf-8")
    _exclusive_bytes(
        _canonical_slot(registry, "authorityTrainCorpus"), corpus_payloads["train"]
    )
    _exclusive_bytes(
        _canonical_slot(registry, "authorityValidationCorpus"),
        corpus_payloads["validation"],
    )

    child_split = {
        row.routing["childId"]: row.routing["split"] for row in source.rows
    }
    hce_rows = {
        split: [
            {
                "schemaVersion": SCHEMA_VERSION,
                "kind": HCE_ROW_KIND,
                "profileId": PROFILE_ID,
                "childId": child_id,
                "handcraftedCpChildStm": registry.authority.hce_by_child[child_id],
            }
            for child_id in sorted(registry.authority.hce_by_child)
            if child_split[child_id] == split
        ]
        for split in ("train", "validation")
    }
    for split, key in (
        ("train", "authorityTrainHce"),
        ("validation", "authorityValidationHce"),
    ):
        payload = b"".join(_canonical_json(row) for row in hce_rows[split])
        _exclusive_bytes(_canonical_slot(registry, key), payload)
    _, initializer_payload = _snapshot_file(
        Path(registry.document["initializerModel"]["path"])
    )
    _exclusive_bytes(
        _canonical_slot(registry, "authorityInitializerModel"), initializer_payload
    )
    manifest_created = _utc_after(claim_created)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUTHORITY_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "committed-target-isolated-authority",
        "createdUtc": manifest_created,
        "preregistration": _identity(registry.preregistration),
        "upstreamCapsule": registry.document["upstreamCapsule"],
        "upstreamVerification": _identity(upstream_verification_path),
        "claim": _identity(claim_path),
        "sourceCorpus": registry.document["corpus"],
        "sourceStaticHce": registry.document["staticHceProjection"],
        "sourceInitializerModel": registry.document["initializerModel"],
        "trainCorpus": _identity(
            _canonical_slot(registry, "authorityTrainCorpus")
        ),
        "validationCorpus": _identity(
            _canonical_slot(registry, "authorityValidationCorpus")
        ),
        "trainStaticHce": _identity(
            _canonical_slot(registry, "authorityTrainHce")
        ),
        "validationStaticHce": _identity(
            _canonical_slot(registry, "authorityValidationHce")
        ),
        "initializerModel": _identity(
            _canonical_slot(registry, "authorityInitializerModel")
        ),
        "rootInventories": {
            split: registry.authority.binding["rootInventories"][split]
            for split in ("train", "validation")
        },
        "phaseSideInventories": {
            split: registry.authority.binding["phaseSideInventories"][split]
            for split in ("train", "validation")
        },
        "heldOutMaterialized": False,
        "targetFieldsDecodedDuringMaterialization": 0,
    }
    manifest_path = _canonical_slot(registry, "authorityManifest")
    identity = _exclusive_json(manifest_path, manifest)
    _audit_namespace(registry, exact_files=set(_AUTHORITY_FILES))
    return identity


def _verify_materialized_authority(
    registry: Generation6Registry,
) -> dict[str, Any]:
    claim_path = _canonical_slot(registry, "authorityClaim")
    claim = _load_json(claim_path, "authority materialization claim")
    _exact_keys(claim, AUTHORITY_CLAIM_FIELDS, "authority materialization claim")
    if not _type_exact_equal(
        claim,
        _authority_claim_document(registry, claim.get("createdUtc", "")),
    ):
        raise ValueError("authority materialization claim changed")
    claim_created = _parse_timestamp(claim["createdUtc"], "authority claim createdUtc")
    if claim_created <= _parse_timestamp(
        registry.document["createdUtc"], "preregistration createdUtc"
    ):
        raise ValueError("authority claim chronology changed")
    manifest_path = _canonical_slot(registry, "authorityManifest")
    manifest = _load_json(manifest_path, "authority materialization manifest")
    _exact_keys(
        manifest, AUTHORITY_MANIFEST_FIELDS, "authority materialization manifest"
    )
    if (
        type(manifest["schemaVersion"]) is not int
        or manifest["schemaVersion"] != SCHEMA_VERSION
        or manifest["kind"] != AUTHORITY_MANIFEST_KIND
        or manifest["profileId"] != PROFILE_ID
        or manifest["status"] != "committed-target-isolated-authority"
        or manifest["heldOutMaterialized"] is not False
        or type(manifest["targetFieldsDecodedDuringMaterialization"]) is not int
        or manifest["targetFieldsDecodedDuringMaterialization"] != 0
        or not _type_exact_equal(
            manifest["preregistration"], _identity(registry.preregistration)
        )
        or not _type_exact_equal(
            manifest["upstreamCapsule"], registry.document["upstreamCapsule"]
        )
        or not _type_exact_equal(
            manifest["upstreamVerification"],
            _identity(_canonical_slot(registry, "authorityUpstreamVerification")),
        )
        or not _type_exact_equal(manifest["claim"], _identity(claim_path))
        or not _type_exact_equal(manifest["sourceCorpus"], registry.document["corpus"])
        or not _type_exact_equal(
            manifest["sourceStaticHce"], registry.document["staticHceProjection"]
        )
        or not _type_exact_equal(
            manifest["sourceInitializerModel"], registry.document["initializerModel"]
        )
    ):
        raise ValueError("authority materialization manifest changed")
    upstream_verification = _load_json(
        _canonical_slot(registry, "authorityUpstreamVerification"),
        "materialized upstream verification",
    )
    if not _type_exact_equal(
        upstream_verification, registry.upstream_verification
    ):
        raise ValueError("materialized upstream verification differs from fresh replay")
    manifest_created = _parse_timestamp(
        manifest["createdUtc"], "authority materialization createdUtc"
    )
    if manifest_created <= claim_created:
        raise ValueError("authority materialization chronology changed")

    # Recompute exact raw split copies without decoding target-bearing fields.
    for split, corpus_key, hce_key in (
        ("train", "authorityTrainCorpus", "authorityTrainHce"),
        ("validation", "authorityValidationCorpus", "authorityValidationHce"),
    ):
        corpus_path = _canonical_slot(registry, corpus_key)
        expected_payload = "".join(
            row.line
            for row in registry.authority.opaque.rows
            if row.routing["split"] == split
        ).encode("utf-8")
        actual_identity, actual_payload = _snapshot_file(corpus_path)
        if actual_payload != expected_payload or not _type_exact_equal(
            manifest[
                "trainCorpus" if split == "train" else "validationCorpus"
            ],
            actual_identity,
        ):
            raise ValueError(f"materialized {split} corpus changed")
        opaque = _opaque_corpus_rows(corpus_path)
        if any(root.split != split for root in opaque.roots):
            raise ValueError(f"materialized {split} corpus crosses splits")
        expected_inventory = registry.authority.binding["rootInventories"][split]
        if not _type_exact_equal(opaque.root_inventories[split], expected_inventory):
            raise ValueError(f"materialized {split} root inventory changed")
        child_ids = {row.routing["childId"] for row in opaque.rows}
        hce_path = _canonical_slot(registry, hce_key)
        hce = _parse_hce_projection(hce_path, child_ids)
        if hce != {
            child_id: registry.authority.hce_by_child[child_id]
            for child_id in child_ids
        }:
            raise ValueError(f"materialized {split} HCE changed")
        manifest_hce_key = (
            "trainStaticHce" if split == "train" else "validationStaticHce"
        )
        if not _type_exact_equal(manifest[manifest_hce_key], _identity(hce_path)):
            raise ValueError(f"materialized {split} HCE identity changed")
    initializer = _canonical_slot(registry, "authorityInitializerModel")
    if (
        _snapshot_file(initializer)[1]
        != _snapshot_file(Path(registry.document["initializerModel"]["path"]))[1]
        or not _type_exact_equal(manifest["initializerModel"], _identity(initializer))
    ):
        raise ValueError("materialized initializer changed")
    expected_root_inventories = {
        split: registry.authority.binding["rootInventories"][split]
        for split in ("train", "validation")
    }
    expected_cell_inventories = {
        split: registry.authority.binding["phaseSideInventories"][split]
        for split in ("train", "validation")
    }
    if not _type_exact_equal(manifest["rootInventories"], expected_root_inventories):
        raise ValueError("materialized root inventories changed")
    if not _type_exact_equal(
        manifest["phaseSideInventories"], expected_cell_inventories
    ):
        raise ValueError("materialized cell inventories changed")
    return manifest


UPSTREAM_ROUTING_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "rootId",
        "sourceRootId",
        "sourceGroupId",
        "phase",
        "parentSideToMove",
        "children",
    }
)
UPSTREAM_ROUTING_CHILD_FIELDS = frozenset({"childId", "normalizedChildOfen"})
UPSTREAM_CAPSULE_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "upstreamVerifierExecutable",
        "upstreamVerifierRunner",
        "upstreamVerifierOptions",
        "targetFreeRouting",
        "componentMap",
        "prelabelSeal",
        "terminalClassifierLineage",
        "initializerSelection",
        "initializerClosure",
        "initializerModel",
        "initializerManifest",
        "plannedProjectionProducer",
        "plannedProjectedCorpusPath",
        "plannedProjectionManifestPath",
        "priorForbiddenCatalogs",
        "teacherClaim",
        "teacherEngine",
        "teacherRunner",
        "teacherOptions",
        "teacherBudgets",
        "teacherInputOrderSha256",
        "teacherAttemptLedger",
        "teacherAttemptLedgerCompletion",
        "teacherCompletion",
        "projectionProducer",
        "teacherLabels",
        "teacherManifest",
        "projectedCorpus",
        "labelManifest",
        "preTargetHceClaim",
        "staticHceEngine",
        "staticHceRunner",
        "staticHceOptions",
        "staticHceTranscript",
        "staticHceManifest",
        "rootInventories",
        "phaseSideInventories",
        "closureDeclaration",
        "resultInformationRead",
        "finalStageSeal",
    }
)
UPSTREAM_TEACHER_CLAIM_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "prelabelSeal",
        "targetFreeRouting",
        "componentMap",
        "engine",
        "runner",
        "options",
        "budgets",
        "inputOrderSha256",
        "plannedProjectionProducer",
        "plannedProjectedCorpusPath",
        "plannedProjectionManifestPath",
        "targetRowsDecodedAtClaim",
        "targetFieldsDecodedAtClaim",
        "resultInformationRead",
    }
)
UPSTREAM_TEACHER_COMPLETION_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "claim",
        "prelabelSeal",
        "engine",
        "runner",
        "options",
        "budgets",
        "inputOrderSha256",
        "attemptLedger",
        "attemptLedgerCompletion",
        "teacherLabels",
        "teacherManifest",
        "projectionProducer",
        "projectedCorpus",
        "labelManifest",
        "finalStageSeal",
        "resultInformationRead",
    }
)
UPSTREAM_HCE_CLAIM_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "prelabelSeal",
        "targetFreeRouting",
        "engine",
        "runner",
        "options",
        "plannedTranscriptPath",
        "targetRowsDecodedAtClaim",
        "targetFieldsDecodedAtClaim",
        "resultInformationRead",
    }
)
UPSTREAM_TEACHER_BUDGETS = {
    "shallowNodes": 2000,
    "deepNodes": 50000,
    "timeoutSeconds": 180,
    "maximumAttempts": 3,
    "workers": 4,
    "childrenPerRoot": 4,
}
UPSTREAM_CAPSULE_DECLARATION = {
    "componentAndSplitMapFrozenBeforeTeacher": True,
    "terminalClassifierLineageFrozenBeforeTeacher": True,
    "plannedProjectionProducerAndPathsFrozenBeforeTeacher": True,
    "priorForbiddenCatalogFrozenBeforeTeacher": True,
    "staticHceCompletedBeforeTeacherTargets": True,
    "heldOutTargetsDecodedByGeneration6AtClosure": 0,
    "gameResultsRead": False,
}
UPSTREAM_VERIFICATION_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "capsule",
        "verifierExecutable",
        "verifierRunner",
        "verifierOptions",
        "initializerAuthority",
        "terminalAuthority",
        "priorForbiddenAuthority",
        "componentAuthority",
        "teacherLedgerAuthority",
        "projectionAuthority",
        "resultInformationRead",
    }
)
UPSTREAM_INITIALIZER_AUTHORITY_FIELDS = frozenset(
    {
        "manifest",
        "selectionMode",
        "selectedCatalogIndex",
        "selectedModel",
        "catalogSourceIds",
        "firstEligibleSelected",
        "selectionSemanticsVerified",
        "selectedPromotionHealthPassed",
        "sourceClosureSemanticsVerified",
        "fallbackProtocolReplayed",
        "g6TargetRowsDecoded",
        "resultInformationRead",
    }
)
UPSTREAM_TERMINAL_AUTHORITY_FIELDS = frozenset(
    {
        "lineage",
        "routedChildren",
        "terminalChildrenExcludedBeforeRouting",
        "unclassifiedChildren",
        "errorTextAcceptedAsTerminal",
        "rulesSemanticsReplayed",
        "completionSemanticsVerified",
    }
)
UPSTREAM_FORBIDDEN_AUTHORITY_FIELDS = frozenset(
    {
        "catalogs",
        "catalogPositions",
        "manifestsSemanticallyReplayed",
        "exactPositionOverlaps",
        "conservativeSignatureOverlaps",
        "sourceArtifactOverlaps",
    }
)
UPSTREAM_COMPONENT_AUTHORITY_FIELDS = frozenset(
    {"componentMap", "roots", "components", "wholeComponentSplits", "semanticsReplayed"}
)
UPSTREAM_TEACHER_LEDGER_AUTHORITY_FIELDS = frozenset(
    {
        "claim",
        "attemptLedger",
        "attemptLedgerCompletion",
        "completion",
        "budgets",
        "routedChildren",
        "attemptRecords",
        "successfulChildren",
        "rejectedChildren",
        "unresolvedChildren",
        "semanticsReplayed",
    }
)
UPSTREAM_PROJECTION_AUTHORITY_FIELDS = frozenset(
    {
        "plannedProducer",
        "plannedCorpusPath",
        "plannedManifestPath",
        "actualProducer",
        "actualCorpus",
        "actualManifest",
        "producerMatches",
        "pathsMatch",
        "semanticsReplayed",
    }
)


def _parse_target_free_routing(
    path: Path,
) -> dict[str, dict[str, Any]]:
    safe, lines = _snapshot_utf8_lines(path, "target-free source routing")
    routes: dict[str, dict[str, Any]] = {}
    child_ids: set[str] = set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        location = f"{safe}:{number}"
        row = _strict_json_loads(line, location=location)
        if not isinstance(row, dict):
            raise ValueError(f"{location}: routing row is not an object")
        _exact_keys(row, UPSTREAM_ROUTING_FIELDS, location)
        if (
            type(row["schemaVersion"]) is not int
            or row["schemaVersion"] != SCHEMA_VERSION
            or row["kind"] != UPSTREAM_ROUTING_KIND
            or row["profileId"] != PROFILE_ID
            or row["phase"] not in PHASES
            or row["parentSideToMove"] not in SIDES
            or type(row["children"]) is not list
            or len(row["children"]) != 4
        ):
            raise ValueError(f"{location}: routing schema changed")
        for field in ("rootId", "sourceRootId", "sourceGroupId"):
            if type(row[field]) is not str or not row[field]:
                raise ValueError(f"{location}: invalid {field}")
        if row["rootId"] in routes:
            raise ValueError(f"{location}: duplicate routing root")
        normalized_children: list[dict[str, str]] = []
        for child in row["children"]:
            if not isinstance(child, dict):
                raise ValueError(f"{location}: routing child is not an object")
            _exact_keys(child, UPSTREAM_ROUTING_CHILD_FIELDS, location)
            if (
                type(child["childId"]) is not str
                or not child["childId"]
                or child["childId"] in child_ids
                or type(child["normalizedChildOfen"]) is not str
                or not child["normalizedChildOfen"]
            ):
                raise ValueError(f"{location}: invalid/duplicate routing child")
            normalized_ofen = " ".join(child["normalizedChildOfen"].split())
            if normalized_ofen != child["normalizedChildOfen"]:
                raise ValueError(f"{location}: child OFEN is not normalized")
            if _child_side(normalized_ofen, location) == row["parentSideToMove"]:
                raise ValueError(f"{location}: child side is not opposite parent")
            child_ids.add(child["childId"])
            normalized_children.append(dict(child))
        routes[row["rootId"]] = {
            **{key: row[key] for key in UPSTREAM_ROUTING_FIELDS if key != "children"},
            "children": sorted(normalized_children, key=lambda item: item["childId"]),
        }
    if not routes:
        raise ValueError("target-free routing is empty")
    return routes


def _opaque_teacher_structural(path: Path) -> dict[str, dict[str, str]]:
    safe, lines = _snapshot_utf8_lines(path, "upstream teacher labels")
    result: dict[str, dict[str, str]] = {}
    wanted = (
        "kind",
        "profileId",
        "rootId",
        "childId",
        "childOfen",
        "phase",
        "parentSideToMove",
    )
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        location = f"{safe}:{number}"
        route = _OpaqueJsonRouter(
            line,
            wanted_strings=wanted,
            wanted_raw=("schemaVersion",),
            location=location,
        ).route()
        if route.keys != UPSTREAM_TEACHER_LABEL_FIELDS or route.raw["schemaVersion"] != "1":
            raise ValueError(f"{location}: teacher structural schema changed")
        if (
            route.strings["kind"] != UPSTREAM_TEACHER_LABEL_KIND
            or route.strings["profileId"] != PROFILE_ID
            or route.strings["childId"] in result
        ):
            raise ValueError(f"{location}: teacher structural identity changed")
        result[route.strings["childId"]] = dict(route.strings)
    return result


def _run_upstream_verifier(
    *, capsule_path: Path, preregistration: Mapping[str, Any]
) -> dict[str, Any]:
    dependencies = (
        Path(preregistration["upstreamVerifierExecutable"]["path"]),
        Path(preregistration["upstreamVerifierRunner"]["path"]),
        Path(preregistration["upstreamVerifierOptions"]["path"]),
        capsule_path,
        Path(preregistration["initializerManifest"]["path"]),
    )
    snapshots = {str(path): _identity(path) for path in dependencies}
    command = [
        preregistration["upstreamVerifierExecutable"]["path"],
        "-I",
        "-B",
        preregistration["upstreamVerifierRunner"]["path"],
        UPSTREAM_VERIFIER_OPTIONS["mode"],
        str(capsule_path),
        preregistration["upstreamVerifierOptions"]["path"],
        preregistration["initializerManifest"]["path"],
    ]
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=3600,
        cwd=Path(preregistration["upstreamVerifierRunner"]["path"]).parent,
        env={},
    )
    for dependency in dependencies:
        if not _type_exact_equal(
            _identity(dependency), snapshots[str(dependency)]
        ):
            raise ValueError("upstream verifier dependency changed during replay")
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError(
            "pinned upstream verifier failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    result = _strict_json_loads(
        completed.stdout, location="upstream verifier stdout"
    )
    if not isinstance(result, dict):
        raise ValueError("upstream verifier did not return one JSON object")
    return result


def _verify_upstream_verifier_result(
    result: Mapping[str, Any],
    *,
    capsule_path: Path,
    capsule: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
    preregistration: Mapping[str, Any],
    initializer: Mapping[str, Any],
    routed_children: int,
    root_count: int,
    component_count: int,
) -> None:
    _exact_keys(result, UPSTREAM_VERIFICATION_FIELDS, "upstream verifier result")
    if (
        type(result["schemaVersion"]) is not int
        or result["schemaVersion"] != SCHEMA_VERSION
        or result["kind"] != UPSTREAM_VERIFICATION_KIND
        or result["profileId"] != PROFILE_ID
        or result["status"] != "passed-fresh-semantic-replay"
        or not _type_exact_equal(result["capsule"], _identity(capsule_path))
        or not _type_exact_equal(
            result["verifierExecutable"],
            preregistration["upstreamVerifierExecutable"],
        )
        or not _type_exact_equal(
            result["verifierRunner"], preregistration["upstreamVerifierRunner"]
        )
        or not _type_exact_equal(
            result["verifierOptions"], preregistration["upstreamVerifierOptions"]
        )
        or result["resultInformationRead"] is not False
    ):
        raise ValueError("upstream fresh-verification header changed")

    initial = result["initializerAuthority"]
    if not isinstance(initial, dict):
        raise ValueError("upstream initializer authority is not an object")
    _exact_keys(
        initial,
        UPSTREAM_INITIALIZER_AUTHORITY_FIELDS,
        "upstream initializer authority",
    )
    promoted = initializer["selectionMode"] == "promoted-prior"
    if (
        not _type_exact_equal(initial["manifest"], identities["initializerManifest"])
        or initial["selectionMode"] != initializer["selectionMode"]
        or initial["selectedCatalogIndex"] != initializer["selectedCatalogIndex"]
        or not _type_exact_equal(initial["selectedModel"], identities["initializerModel"])
        or initial["catalogSourceIds"] != list(INITIALIZER_ORDERED_CATALOG)
        or initial["firstEligibleSelected"] is not True
        or initial["selectionSemanticsVerified"] is not True
        or initial["sourceClosureSemanticsVerified"] is not True
        or type(initial["g6TargetRowsDecoded"]) is not int
        or initial["g6TargetRowsDecoded"] != 0
        or initial["resultInformationRead"] is not False
        or (
            promoted
            and (
                initial["selectedPromotionHealthPassed"] is not True
                or initial["fallbackProtocolReplayed"] is not False
            )
        )
        or (
            not promoted
            and (
                initial["selectedPromotionHealthPassed"] is not None
                or initial["fallbackProtocolReplayed"] is not True
            )
        )
    ):
        raise ValueError("upstream initializer semantic replay changed")

    terminal = result["terminalAuthority"]
    if not isinstance(terminal, dict):
        raise ValueError("upstream terminal authority is not an object")
    _exact_keys(
        terminal, UPSTREAM_TERMINAL_AUTHORITY_FIELDS, "upstream terminal authority"
    )
    if (
        not _type_exact_equal(terminal["lineage"], identities["terminalClassifierLineage"])
        or type(terminal["routedChildren"]) is not int
        or terminal["routedChildren"] != routed_children
        or type(terminal["terminalChildrenExcludedBeforeRouting"]) is not int
        or terminal["terminalChildrenExcludedBeforeRouting"] < 0
        or type(terminal["unclassifiedChildren"]) is not int
        or terminal["unclassifiedChildren"] != 0
        or terminal["errorTextAcceptedAsTerminal"] is not False
        or terminal["rulesSemanticsReplayed"] is not True
        or terminal["completionSemanticsVerified"] is not True
    ):
        raise ValueError("upstream terminal-classifier replay changed")

    forbidden = result["priorForbiddenAuthority"]
    if not isinstance(forbidden, dict):
        raise ValueError("upstream forbidden authority is not an object")
    _exact_keys(
        forbidden,
        UPSTREAM_FORBIDDEN_AUTHORITY_FIELDS,
        "upstream forbidden authority",
    )
    if (
        not _type_exact_equal(forbidden["catalogs"], capsule["priorForbiddenCatalogs"])
        or type(forbidden["catalogPositions"]) is not int
        or forbidden["catalogPositions"] <= 0
        or forbidden["manifestsSemanticallyReplayed"] is not True
        or type(forbidden["exactPositionOverlaps"]) is not int
        or forbidden["exactPositionOverlaps"] != 0
        or type(forbidden["conservativeSignatureOverlaps"]) is not int
        or forbidden["conservativeSignatureOverlaps"] != 0
        or type(forbidden["sourceArtifactOverlaps"]) is not int
        or forbidden["sourceArtifactOverlaps"] != 0
    ):
        raise ValueError("upstream prior-forbidden replay changed")

    component = result["componentAuthority"]
    if not isinstance(component, dict):
        raise ValueError("upstream component authority is not an object")
    _exact_keys(
        component,
        UPSTREAM_COMPONENT_AUTHORITY_FIELDS,
        "upstream component authority",
    )
    if (
        not _type_exact_equal(component["componentMap"], identities["componentMap"])
        or type(component["roots"]) is not int
        or component["roots"] != root_count
        or type(component["components"]) is not int
        or component["components"] != component_count
        or component["wholeComponentSplits"] is not True
        or component["semanticsReplayed"] is not True
    ):
        raise ValueError("upstream component-map semantic replay changed")

    teacher = result["teacherLedgerAuthority"]
    if not isinstance(teacher, dict):
        raise ValueError("upstream teacher-ledger authority is not an object")
    _exact_keys(
        teacher,
        UPSTREAM_TEACHER_LEDGER_AUTHORITY_FIELDS,
        "upstream teacher-ledger authority",
    )
    attempts = teacher["attemptRecords"]
    if (
        not _type_exact_equal(teacher["claim"], identities["teacherClaim"])
        or not _type_exact_equal(
            teacher["attemptLedger"], identities["teacherAttemptLedger"]
        )
        or not _type_exact_equal(
            teacher["attemptLedgerCompletion"],
            identities["teacherAttemptLedgerCompletion"],
        )
        or not _type_exact_equal(teacher["completion"], identities["teacherCompletion"])
        or not _type_exact_equal(teacher["budgets"], UPSTREAM_TEACHER_BUDGETS)
        or type(teacher["routedChildren"]) is not int
        or teacher["routedChildren"] != routed_children
        or type(attempts) is not int
        or attempts < routed_children
        or attempts > routed_children * UPSTREAM_TEACHER_BUDGETS["maximumAttempts"]
        or type(teacher["successfulChildren"]) is not int
        or teacher["successfulChildren"] != routed_children
        or type(teacher["rejectedChildren"]) is not int
        or teacher["rejectedChildren"] != 0
        or type(teacher["unresolvedChildren"]) is not int
        or teacher["unresolvedChildren"] != 0
        or teacher["semanticsReplayed"] is not True
    ):
        raise ValueError("upstream teacher ledger/completion replay changed")

    projection = result["projectionAuthority"]
    if not isinstance(projection, dict):
        raise ValueError("upstream projection authority is not an object")
    _exact_keys(
        projection,
        UPSTREAM_PROJECTION_AUTHORITY_FIELDS,
        "upstream projection authority",
    )
    if (
        not _type_exact_equal(
            projection["plannedProducer"], identities["plannedProjectionProducer"]
        )
        or projection["plannedCorpusPath"] != capsule["plannedProjectedCorpusPath"]
        or projection["plannedManifestPath"] != capsule["plannedProjectionManifestPath"]
        or not _type_exact_equal(
            projection["actualProducer"], identities["projectionProducer"]
        )
        or not _type_exact_equal(projection["actualCorpus"], identities["projectedCorpus"])
        or not _type_exact_equal(projection["actualManifest"], identities["labelManifest"])
        or projection["producerMatches"] is not True
        or projection["pathsMatch"] is not True
        or projection["semanticsReplayed"] is not True
    ):
        raise ValueError("upstream projection replay changed")


def _verify_upstream_capsule(
    path: Path,
    *,
    authority: VerifiedAuthority,
    preregistration: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    capsule = _load_json(path, "omega-decision-v3 capsule")
    _exact_keys(capsule, UPSTREAM_CAPSULE_FIELDS, "omega-decision-v3 capsule")
    if (
        type(capsule["schemaVersion"]) is not int
        or capsule["schemaVersion"] != SCHEMA_VERSION
        or capsule["kind"] != UPSTREAM_CAPSULE_KIND
        or capsule["profileId"] != PROFILE_ID
        or capsule["status"] != "closed-pretarget-to-final-projection-lineage"
        or capsule["finalStageSeal"] is not True
        or capsule["resultInformationRead"] is not False
        or not _type_exact_equal(
            capsule["closureDeclaration"], UPSTREAM_CAPSULE_DECLARATION
        )
        or not _type_exact_equal(capsule["teacherBudgets"], UPSTREAM_TEACHER_BUDGETS)
    ):
        raise ValueError("omega-decision-v3 capsule contract changed")
    _parse_timestamp(capsule["createdUtc"], "upstream capsule createdUtc")
    identity_fields = (
        "upstreamVerifierExecutable",
        "upstreamVerifierRunner",
        "upstreamVerifierOptions",
        "targetFreeRouting",
        "componentMap",
        "prelabelSeal",
        "terminalClassifierLineage",
        "initializerSelection",
        "initializerClosure",
        "initializerModel",
        "initializerManifest",
        "plannedProjectionProducer",
        "teacherClaim",
        "teacherEngine",
        "teacherRunner",
        "teacherOptions",
        "teacherAttemptLedger",
        "teacherAttemptLedgerCompletion",
        "teacherCompletion",
        "projectionProducer",
        "teacherLabels",
        "teacherManifest",
        "projectedCorpus",
        "labelManifest",
        "preTargetHceClaim",
        "staticHceEngine",
        "staticHceRunner",
        "staticHceOptions",
        "staticHceTranscript",
        "staticHceManifest",
    )
    identities = {
        field: _verify_identity_record(capsule[field], f"capsule {field}")
        for field in identity_fields
    }
    if (
        type(capsule["priorForbiddenCatalogs"]) is not list
        or not capsule["priorForbiddenCatalogs"]
    ):
        raise ValueError("capsule prior forbidden catalog is empty")
    for index, identity in enumerate(capsule["priorForbiddenCatalogs"]):
        _verify_identity_record(identity, f"capsule forbidden catalog {index}")
    if (
        type(capsule["plannedProjectedCorpusPath"]) is not str
        or capsule["plannedProjectedCorpusPath"]
        != identities["projectedCorpus"]["path"]
        or type(capsule["plannedProjectionManifestPath"]) is not str
        or capsule["plannedProjectionManifestPath"]
        != identities["labelManifest"]["path"]
        or not _type_exact_equal(
            identities["plannedProjectionProducer"],
            identities["projectionProducer"],
        )
        or not _type_exact_equal(capsule["rootInventories"], authority.binding["rootInventories"])
        or not _type_exact_equal(
            capsule["phaseSideInventories"], authority.binding["phaseSideInventories"]
        )
    ):
        raise ValueError("capsule planned output/inventories changed")
    prereg_links = {
        "upstreamVerifierExecutable": "upstreamVerifierExecutable",
        "upstreamVerifierRunner": "upstreamVerifierRunner",
        "upstreamVerifierOptions": "upstreamVerifierOptions",
        "componentMap": "componentMap",
        "projectedCorpus": "corpus",
        "labelManifest": "labelManifest",
        "staticHceEngine": "staticHceEngine",
        "staticHceRunner": "staticHceRunner",
        "staticHceOptions": "staticHceOptions",
        "staticHceTranscript": "staticHceProjection",
        "staticHceManifest": "staticHceManifest",
        "initializerModel": "initializerModel",
        "initializerManifest": "initializerManifest",
    }
    for capsule_field, prereg_field in prereg_links.items():
        if not _type_exact_equal(identities[capsule_field], preregistration[prereg_field]):
            raise ValueError(f"capsule {capsule_field} differs from preregistration")
    label_document = _load_json(
        Path(preregistration["labelManifest"]["path"]),
        "capsule-bound label manifest",
    )
    for capsule_field, manifest_field in (
        ("prelabelSeal", "upstreamPrelabelSeal"),
        ("teacherLabels", "upstreamTeacherLabels"),
        ("teacherManifest", "upstreamTeacherManifest"),
        ("projectionProducer", "projectionProducer"),
    ):
        if not _type_exact_equal(
            identities[capsule_field], label_document[manifest_field]
        ):
            raise ValueError(
                f"capsule {capsule_field} differs from projected label manifest"
            )
    initializer_document = _load_json(
        Path(preregistration["initializerManifest"]["path"]),
        "capsule-bound initializer manifest",
    )
    for capsule_field, manifest_field in (
        ("initializerSelection", "selectionSeal"),
        ("initializerClosure", "sourceClosure"),
        ("initializerModel", "model"),
        ("initializerManifest", None),
    ):
        expected_initializer_identity = (
            preregistration["initializerManifest"]
            if manifest_field is None
            else initializer_document[manifest_field]
        )
        if not _type_exact_equal(
            identities[capsule_field], expected_initializer_identity
        ):
            raise ValueError(
                f"capsule {capsule_field} differs from initializer lineage"
            )

    routes = _parse_target_free_routing(Path(identities["targetFreeRouting"]["path"]))
    components = _parse_component_map(Path(identities["componentMap"]["path"]))
    structural = {root.root_id: root for root in authority.opaque.roots}
    if set(routes) != set(components) or set(routes) != set(structural):
        raise ValueError("capsule routing/component/projected root inventories differ")
    verifier_result = _run_upstream_verifier(
        capsule_path=path, preregistration=preregistration
    )
    _verify_upstream_verifier_result(
        verifier_result,
        capsule_path=path,
        capsule=capsule,
        identities=identities,
        preregistration=preregistration,
        initializer=initializer_document,
        routed_children=len(authority.opaque.rows),
        root_count=len(routes),
        component_count=len(
            {row["leakageComponentId"] for row in components.values()}
        ),
    )
    routing_digest_rows: list[dict[str, Any]] = []
    for root_id in sorted(routes):
        route = routes[root_id]
        component = components[root_id]
        root = structural[root_id]
        route_children = {
            item["childId"]: item["normalizedChildOfen"]
            for item in route["children"]
        }
        projected_children = {
            row.routing["childId"]: " ".join(row.routing["childOfen"].split())
            for row in authority.opaque.rows
            if row.routing["rootId"] == root_id
        }
        if (
            route["sourceRootId"] != component["sourceRootId"]
            or route["sourceGroupId"] != component["sourceGroupId"]
            or route["phase"] != root.phase
            or route["parentSideToMove"] != root.root_side
            or route_children != projected_children
        ):
            raise ValueError(f"capsule routing mismatch for {root_id}")
        routing_digest_rows.append(route)
    routing_digest = _sha256_bytes(_canonical_json(routing_digest_rows))
    if capsule["teacherInputOrderSha256"] != routing_digest:
        raise ValueError("capsule teacher input order digest changed")
    teacher_structural = _opaque_teacher_structural(
        Path(identities["teacherLabels"]["path"])
    )
    expected_teacher = {
        child["childId"]: {
            "kind": UPSTREAM_TEACHER_LABEL_KIND,
            "profileId": PROFILE_ID,
            "rootId": root_id,
            "childId": child["childId"],
            "childOfen": child["normalizedChildOfen"],
            "phase": route["phase"],
            "parentSideToMove": route["parentSideToMove"],
        }
        for root_id, route in routes.items()
        for child in route["children"]
    }
    if not _type_exact_equal(teacher_structural, expected_teacher):
        raise ValueError("teacher labels substituted target-free routing fields")

    prelabel = _verify_upstream_prelabel_seal(
        Path(identities["prelabelSeal"]["path"]),
        component_map=Path(identities["componentMap"]["path"]),
        target_free_routing=Path(identities["targetFreeRouting"]["path"]),
    )
    teacher_claim = _load_json(
        Path(identities["teacherClaim"]["path"]), "capsule teacher claim"
    )
    _exact_keys(teacher_claim, UPSTREAM_TEACHER_CLAIM_FIELDS, "capsule teacher claim")
    expected_claim_links = {
        "prelabelSeal": identities["prelabelSeal"],
        "targetFreeRouting": identities["targetFreeRouting"],
        "componentMap": identities["componentMap"],
        "engine": identities["teacherEngine"],
        "runner": identities["teacherRunner"],
        "options": identities["teacherOptions"],
        "budgets": UPSTREAM_TEACHER_BUDGETS,
        "inputOrderSha256": routing_digest,
        "plannedProjectionProducer": identities["plannedProjectionProducer"],
        "plannedProjectedCorpusPath": capsule["plannedProjectedCorpusPath"],
        "plannedProjectionManifestPath": capsule["plannedProjectionManifestPath"],
    }
    if (
        teacher_claim.get("schemaVersion") != SCHEMA_VERSION
        or teacher_claim.get("kind") != UPSTREAM_TEACHER_CLAIM_KIND
        or teacher_claim.get("profileId") != PROFILE_ID
        or teacher_claim.get("status") != "claimed-before-first-teacher-target-decode"
        or teacher_claim.get("targetRowsDecodedAtClaim") != 0
        or type(teacher_claim.get("targetRowsDecodedAtClaim")) is not int
        or teacher_claim.get("targetFieldsDecodedAtClaim") != 0
        or type(teacher_claim.get("targetFieldsDecodedAtClaim")) is not int
        or teacher_claim.get("resultInformationRead") is not False
        or any(
            not _type_exact_equal(teacher_claim[field], value)
            for field, value in expected_claim_links.items()
        )
    ):
        raise ValueError("capsule teacher claim is not the frozen plan")
    completion = _load_json(
        Path(identities["teacherCompletion"]["path"]),
        "capsule teacher completion",
    )
    _exact_keys(
        completion,
        UPSTREAM_TEACHER_COMPLETION_FIELDS,
        "capsule teacher completion",
    )
    completion_links = {
        "claim": identities["teacherClaim"],
        "prelabelSeal": identities["prelabelSeal"],
        "engine": identities["teacherEngine"],
        "runner": identities["teacherRunner"],
        "options": identities["teacherOptions"],
        "budgets": UPSTREAM_TEACHER_BUDGETS,
        "inputOrderSha256": routing_digest,
        "attemptLedger": identities["teacherAttemptLedger"],
        "attemptLedgerCompletion": identities["teacherAttemptLedgerCompletion"],
        "teacherLabels": identities["teacherLabels"],
        "teacherManifest": identities["teacherManifest"],
        "projectionProducer": identities["projectionProducer"],
        "projectedCorpus": identities["projectedCorpus"],
        "labelManifest": identities["labelManifest"],
    }
    if (
        completion.get("schemaVersion") != SCHEMA_VERSION
        or completion.get("kind") != UPSTREAM_TEACHER_COMPLETION_KIND
        or completion.get("profileId") != PROFILE_ID
        or completion.get("status") != "completed-exact-claimed-teacher-and-projection"
        or completion.get("finalStageSeal") is not True
        or completion.get("resultInformationRead") is not False
        or any(
            not _type_exact_equal(completion[field], value)
            for field, value in completion_links.items()
        )
    ):
        raise ValueError("capsule teacher completion changed")
    hce_claim = _load_json(
        Path(identities["preTargetHceClaim"]["path"]), "capsule HCE claim"
    )
    _exact_keys(hce_claim, UPSTREAM_HCE_CLAIM_FIELDS, "capsule HCE claim")
    if (
        hce_claim.get("schemaVersion") != SCHEMA_VERSION
        or hce_claim.get("kind") != UPSTREAM_HCE_CLAIM_KIND
        or hce_claim.get("profileId") != PROFILE_ID
        or hce_claim.get("status") != "claimed-before-teacher-and-target-decode"
        or not _type_exact_equal(hce_claim.get("prelabelSeal"), identities["prelabelSeal"])
        or not _type_exact_equal(
            hce_claim.get("targetFreeRouting"), identities["targetFreeRouting"]
        )
        or not _type_exact_equal(hce_claim.get("engine"), identities["staticHceEngine"])
        or not _type_exact_equal(hce_claim.get("runner"), identities["staticHceRunner"])
        or not _type_exact_equal(hce_claim.get("options"), identities["staticHceOptions"])
        or hce_claim.get("plannedTranscriptPath")
        != identities["staticHceTranscript"]["path"]
        or type(hce_claim.get("targetRowsDecodedAtClaim")) is not int
        or hce_claim.get("targetRowsDecodedAtClaim") != 0
        or type(hce_claim.get("targetFieldsDecodedAtClaim")) is not int
        or hce_claim.get("targetFieldsDecodedAtClaim") != 0
        or hce_claim.get("resultInformationRead") is not False
    ):
        raise ValueError("capsule pre-target HCE claim changed")
    if _parse_timestamp(teacher_claim["createdUtc"], "teacher claim createdUtc") <= _parse_timestamp(
        prelabel["createdUtc"], "prelabel createdUtc"
    ):
        raise ValueError("teacher claim does not follow prelabel freeze")
    return capsule, verifier_result


TRAINING_CLAIM_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "authorityManifest",
        "modelId",
        "candidateId",
        "purpose",
        "seed",
        "recipe",
        "optimizerProtocol",
        "plannedBatchOrderTranscriptPath",
        "trainerExecutable",
        "trainerRunner",
        "runtimeManifest",
        "initializerModel",
        "trainCorpus",
        "trainStaticHce",
        "command",
        "resultInformationRead",
        "validationTargetRowsDecodedAtClaim",
        "heldOutTargetRowsDecodedAtClaim",
        "heldOutTargetFieldsDecodedAtClaim",
    }
)
TRAINING_HISTORY_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "modelId",
        "candidateId",
        "purpose",
        "seed",
        "recipe",
        "optimizerProtocol",
        "batchOrderSha256",
        "actualBatchOrderTranscript",
        "epochs",
        "resultInformationRead",
        "validationRootsDecoded",
        "heldOutRootsDecoded",
        "heldOutTargetFieldsDecoded",
    }
)
TRAINING_EPOCH_FIELDS = frozenset(
    {
        "epoch",
        "qatEnabled",
        "learningRate",
        "optimizerSteps",
        "rootsSeen",
        "completeRootBatches",
        "validationRootsDecoded",
        "heldOutRootsDecoded",
        "heldOutTargetFieldsDecoded",
    }
)
TRAINING_BATCH_ORDER_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "modelId",
        "candidateId",
        "purpose",
        "seed",
        "recipe",
        "epoch",
        "batchIndex",
        "optimizerStep",
        "qatEnabled",
        "rootIds",
        "childIds",
        "batchContentSha256",
    }
)
TRAINING_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "predecessor",
        "claim",
        "authorityManifest",
        "modelId",
        "candidateId",
        "purpose",
        "seed",
        "recipe",
        "optimizerProtocol",
        "batchOrderSha256",
        "actualBatchOrderTranscript",
        "trainerExecutable",
        "trainerRunner",
        "runtimeManifest",
        "contractSource",
        "initializerModel",
        "trainCorpus",
        "trainStaticHce",
        "history",
        "model",
        "command",
        "resultInformationRead",
        "validationTargetRowsDecoded",
        "heldOutTargetRowsDecoded",
        "heldOutTargetFieldsDecoded",
    }
)


def _training_spec(
    registry: Generation6Registry, model_id: str
) -> tuple[str, str, int, Mapping[str, float]]:
    if model_id in CANDIDATES:
        candidate = model_id
        purpose = "primary-training"
        seed = registry.document["primaryTrainingSeeds"][candidate]
    elif model_id.endswith("-robustness") and model_id.removesuffix(
        "-robustness"
    ) in CANDIDATES:
        candidate = model_id.removesuffix("-robustness")
        purpose = "robustness-training"
        seed = registry.document["robustnessTrainingSeeds"][candidate]
    else:
        raise ValueError("training model id is not a frozen G6 candidate")
    return candidate, purpose, seed, CANDIDATE_RECIPES[candidate]


def _training_batch_order_rows(
    registry: Generation6Registry, model_id: str, seed: int
) -> list[dict[str, Any]]:
    candidate, purpose, expected_seed, recipe = _training_spec(registry, model_id)
    if seed != expected_seed:
        raise ValueError("batch-order seed differs from preregistration")
    corpus_path = _canonical_slot(registry, "authorityTrainCorpus")
    hce_path = _canonical_slot(registry, "authorityTrainHce")
    input_identities = (_identity(corpus_path), _identity(hce_path))
    if model_id in registry.batch_order_rows_by_model:
        if not _type_exact_equal(
            input_identities, registry.batch_order_inputs_by_model[model_id]
        ):
            raise ValueError("training batch inputs changed after order replay")
        return registry.batch_order_rows_by_model[model_id]
    opaque = _opaque_corpus_rows(corpus_path)
    _, source_lines = _snapshot_utf8_lines(corpus_path, "training corpus")
    label_by_child: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(source_lines, 1):
        if not line.strip():
            continue
        value = _strict_json_loads(line, location=f"{corpus_path}:{line_number}")
        if not isinstance(value, dict):
            raise ValueError("training label row is not an object")
        _exact_keys(value, LABEL_FIELDS, f"{corpus_path}:{line_number}")
        child_id = value["childId"]
        if type(child_id) is not str or child_id in label_by_child:
            raise ValueError("training label child inventory changed")
        label_by_child[child_id] = value
    child_ids = set(label_by_child)
    hce_values = _parse_hce_projection(
        _canonical_slot(registry, "authorityTrainHce"), child_ids
    )
    children_by_root: dict[str, list[str]] = defaultdict(list)
    for child_id, label in label_by_child.items():
        children_by_root[label["rootId"]].append(child_id)
    for root_id in children_by_root:
        children_by_root[root_id].sort()
        if len(children_by_root[root_id]) != 4:
            raise ValueError("training batch root is not a complete four-child decision")
    root_ids = sorted(root.root_id for root in opaque.roots)
    if root_ids != sorted(children_by_root):
        raise ValueError("training batch root inventory differs from corpus")
    roots_per_batch = OPTIMIZER_PROTOCOL["rootsPerBatch"]
    if len(root_ids) % roots_per_batch != 0:
        raise ValueError("train roots are not batch-order divisible")
    rows: list[dict[str, Any]] = []
    for epoch in range(1, OPTIMIZER_PROTOCOL["epochs"] + 1):
        epoch_digest = hashlib.sha256(
            f"{seed}|epoch|{epoch}".encode("utf-8")
        ).digest()
        epoch_seed = int.from_bytes(epoch_digest[:8], "little", signed=False)
        permutation = np.random.default_rng(epoch_seed).permutation(len(root_ids))
        ordered = [root_ids[int(index)] for index in permutation]
        for batch_index, start in enumerate(
            range(0, len(ordered), roots_per_batch), 1
        ):
            batch_root_ids = ordered[start : start + roots_per_batch]
            batch_child_ids = [
                child_id
                for root_id in batch_root_ids
                for child_id in children_by_root[root_id]
            ]
            label_payload = b"".join(
                _canonical_json(label_by_child[child_id])
                for child_id in batch_child_ids
            )
            hce_payload = b"".join(
                _canonical_json(
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "kind": HCE_ROW_KIND,
                        "profileId": PROFILE_ID,
                        "childId": child_id,
                        "handcraftedCpChildStm": hce_values[child_id],
                    }
                )
                for child_id in batch_child_ids
            )
            optimizer_step = (
                (epoch - 1) * (len(root_ids) // roots_per_batch) + batch_index
            )
            rows.append(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "kind": TRAINING_BATCH_ORDER_KIND,
                    "profileId": PROFILE_ID,
                    "modelId": model_id,
                    "candidateId": candidate,
                    "purpose": purpose,
                    "seed": seed,
                    "recipe": dict(recipe),
                    "epoch": epoch,
                    "batchIndex": batch_index,
                    "optimizerStep": optimizer_step,
                    "qatEnabled": epoch >= OPTIMIZER_PROTOCOL["firstQatEpoch"],
                    "rootIds": batch_root_ids,
                    "childIds": batch_child_ids,
                    "batchContentSha256": _sha256_bytes(
                        label_payload + hce_payload
                    ),
                }
            )
    registry.batch_order_rows_by_model[model_id] = rows
    registry.batch_order_inputs_by_model[model_id] = input_identities
    return rows


def _training_batch_order_payload(
    registry: Generation6Registry, model_id: str, seed: int
) -> bytes:
    rows = _training_batch_order_rows(registry, model_id, seed)
    if model_id in registry.batch_order_payload_by_model:
        return registry.batch_order_payload_by_model[model_id]
    payload = b"".join(
        _canonical_json(row)
        for row in rows
    )
    registry.batch_order_payload_by_model[model_id] = payload
    return payload


def _training_batch_order_sha256(
    registry: Generation6Registry, model_id: str, seed: int
) -> str:
    return _sha256_bytes(
        _training_batch_order_payload(registry, model_id, seed)
    )


def _verify_training_batch_order(
    path: Path, *, registry: Generation6Registry, model_id: str
) -> dict[str, Any]:
    candidate, purpose, seed, recipe = _training_spec(registry, model_id)
    safe, lines = _snapshot_utf8_lines(path, f"{model_id} actual batch order")
    actual: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise ValueError(f"{safe}:{line_number}: blank batch-order row")
        value = _strict_json_loads(line, location=f"{safe}:{line_number}")
        if not isinstance(value, dict):
            raise ValueError(f"{safe}:{line_number}: batch-order row is not an object")
        _exact_keys(
            value,
            TRAINING_BATCH_ORDER_FIELDS,
            f"{safe}:{line_number} batch-order row",
        )
        if (
            value["schemaVersion"] != SCHEMA_VERSION
            or value["kind"] != TRAINING_BATCH_ORDER_KIND
            or value["profileId"] != PROFILE_ID
            or value["modelId"] != model_id
            or value["candidateId"] != candidate
            or value["purpose"] != purpose
            or type(value["seed"]) is not int
            or value["seed"] != seed
            or not _type_exact_equal(value["recipe"], dict(recipe))
            or type(value["epoch"]) is not int
            or type(value["batchIndex"]) is not int
            or type(value["optimizerStep"]) is not int
            or type(value["qatEnabled"]) is not bool
            or type(value["rootIds"]) is not list
            or any(type(root_id) is not str for root_id in value["rootIds"])
            or type(value["childIds"]) is not list
            or any(type(child_id) is not str for child_id in value["childIds"])
            or type(value["batchContentSha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value["batchContentSha256"])
            is None
        ):
            raise ValueError(f"{safe}:{line_number}: batch-order contract changed")
        actual.append(value)
    expected = _training_batch_order_rows(registry, model_id, seed)
    if not _type_exact_equal(actual, expected):
        raise ValueError(f"{model_id} actual consumed batch order differs from replay")
    identity = _identity(path)
    if identity["sha256"] != _training_batch_order_sha256(
        registry, model_id, seed
    ):
        raise ValueError(f"{model_id} batch-order transcript digest changed")
    return identity


def _training_command(
    registry: Generation6Registry,
    *,
    model_id: str,
    candidate: str,
    purpose: str,
    seed: int,
    recipe: Mapping[str, float],
) -> list[str]:
    return [
        registry.document["trainerExecutable"]["path"],
        "-I",
        "-B",
        registry.document["trainerRunner"]["path"],
        TRAINER_COMMAND_PROTOCOL["mode"],
        model_id,
        candidate,
        purpose,
        str(seed),
        _canonical_json(dict(recipe)).decode("utf-8").strip(),
        _canonical_json(OPTIMIZER_PROTOCOL).decode("utf-8").strip(),
        str(_canonical_slot(registry, "authorityTrainCorpus")),
        str(_canonical_slot(registry, "authorityTrainHce")),
        str(_canonical_slot(registry, "authorityInitializerModel")),
        str(_canonical_slot(registry, f"trainingBatchOrder:{model_id}")),
        str(_canonical_slot(registry, f"model:{model_id}")),
        str(_canonical_slot(registry, f"trainingHistory:{model_id}")),
    ]


def _training_claim_document(
    registry: Generation6Registry, model_id: str, created: str
) -> dict[str, Any]:
    candidate, purpose, seed, recipe = _training_spec(registry, model_id)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TRAINING_CLAIM_KIND,
        "profileId": PROFILE_ID,
        "status": "claimed-before-trainer-execution",
        "createdUtc": created,
        "preregistration": _identity(registry.preregistration),
        "authorityManifest": _identity(
            _canonical_slot(registry, "authorityManifest")
        ),
        "modelId": model_id,
        "candidateId": candidate,
        "purpose": purpose,
        "seed": seed,
        "recipe": dict(recipe),
        "optimizerProtocol": dict(OPTIMIZER_PROTOCOL),
        # Freeze only the destination before launch.  The expected transcript
        # digest is deliberately computed by the orchestrator *after* the
        # trainer returns; publishing it in this pre-launch claim would give
        # the trainer the value that its actual-consumption transcript must
        # independently earn.
        "plannedBatchOrderTranscriptPath": str(
            _canonical_slot(registry, f"trainingBatchOrder:{model_id}")
        ),
        "trainerExecutable": registry.document["trainerExecutable"],
        "trainerRunner": registry.document["trainerRunner"],
        "runtimeManifest": registry.document["runtimeManifest"],
        "initializerModel": _identity(
            _canonical_slot(registry, "authorityInitializerModel")
        ),
        "trainCorpus": _identity(
            _canonical_slot(registry, "authorityTrainCorpus")
        ),
        "trainStaticHce": _identity(
            _canonical_slot(registry, "authorityTrainHce")
        ),
        "command": _training_command(
            registry,
            model_id=model_id,
            candidate=candidate,
            purpose=purpose,
            seed=seed,
            recipe=recipe,
        ),
        "resultInformationRead": False,
        "validationTargetRowsDecodedAtClaim": 0,
        "heldOutTargetRowsDecodedAtClaim": 0,
        "heldOutTargetFieldsDecodedAtClaim": 0,
    }


def _verify_training_history(
    path: Path,
    *,
    registry: Generation6Registry,
    model_id: str,
) -> dict[str, Any]:
    document = _load_json(path, f"{model_id} training history")
    _exact_keys(document, TRAINING_HISTORY_FIELDS, f"{model_id} training history")
    candidate, purpose, seed, recipe = _training_spec(registry, model_id)
    train_roots = registry.authority.binding["rootInventories"]["train"]["roots"]
    roots_per_batch = OPTIMIZER_PROTOCOL["rootsPerBatch"]
    if train_roots % roots_per_batch != 0:
        raise ValueError("frozen train inventory is not complete-batch divisible")
    batches = train_roots // roots_per_batch
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != TRAINING_HISTORY_KIND
        or document["profileId"] != PROFILE_ID
        or document["modelId"] != model_id
        or document["candidateId"] != candidate
        or document["purpose"] != purpose
        or type(document["seed"]) is not int
        or document["seed"] != seed
        or not _type_exact_equal(document["recipe"], dict(recipe))
        or not _type_exact_equal(document["optimizerProtocol"], OPTIMIZER_PROTOCOL)
        or type(document["batchOrderSha256"]) is not str
        or document["batchOrderSha256"]
        != _training_batch_order_sha256(registry, model_id, seed)
        or not _type_exact_equal(
            document["actualBatchOrderTranscript"],
            _identity(_canonical_slot(registry, f"trainingBatchOrder:{model_id}")),
        )
        or type(document["resultInformationRead"]) is not bool
        or document["resultInformationRead"] is not False
        or type(document["validationRootsDecoded"]) is not int
        or document["validationRootsDecoded"] != 0
        or type(document["heldOutRootsDecoded"]) is not int
        or document["heldOutRootsDecoded"] != 0
        or type(document["heldOutTargetFieldsDecoded"]) is not int
        or document["heldOutTargetFieldsDecoded"] != 0
        or type(document["epochs"]) is not list
        or len(document["epochs"]) != OPTIMIZER_PROTOCOL["epochs"]
    ):
        raise ValueError(f"{model_id} training history contract changed")
    for index, epoch in enumerate(document["epochs"], 1):
        if not isinstance(epoch, dict):
            raise ValueError(f"{model_id} epoch {index} is not an object")
        _exact_keys(epoch, TRAINING_EPOCH_FIELDS, f"{model_id} epoch {index}")
        qat = index >= OPTIMIZER_PROTOCOL["firstQatEpoch"]
        learning_rate = OPTIMIZER_PROTOCOL["learningRate"] * (
            OPTIMIZER_PROTOCOL["qatLearningRateScale"] if qat else 1.0
        )
        expected = {
            "epoch": index,
            "qatEnabled": qat,
            "learningRate": learning_rate,
            "optimizerSteps": index * batches,
            "rootsSeen": train_roots,
            "completeRootBatches": batches,
            "validationRootsDecoded": 0,
            "heldOutRootsDecoded": 0,
            "heldOutTargetFieldsDecoded": 0,
        }
        if not _type_exact_equal(epoch, expected):
            raise ValueError(f"{model_id} epoch {index} schedule changed")
    return document


def _completed_primary_files(count: int) -> set[str]:
    files = set(_AUTHORITY_FILES)
    for candidate in CANDIDATES[:count]:
        for prefix in (
            "trainingClaim",
            "model",
            "trainingHistory",
            "trainingBatchOrder",
            "trainingManifest",
        ):
            files.add(_relative_slot(f"{prefix}:{candidate}"))
    return files


def _training_predecessor(
    registry: Generation6Registry, model_id: str
) -> dict[str, Any]:
    if model_id in CANDIDATES:
        index = CANDIDATES.index(model_id)
        if index == 0:
            return _identity(_canonical_slot(registry, "authorityManifest"))
        return _identity(
            _canonical_slot(
                registry, f"trainingManifest:{CANDIDATES[index - 1]}"
            )
        )
    return _identity(_canonical_slot(registry, "validationSelection"))


def _verify_training_run(
    registry: Generation6Registry, model_id: str
) -> dict[str, Any]:
    candidate, purpose, seed, recipe = _training_spec(registry, model_id)
    claim_path = _canonical_slot(registry, f"trainingClaim:{model_id}")
    claim = _load_json(claim_path, f"{model_id} training claim")
    _exact_keys(claim, TRAINING_CLAIM_FIELDS, f"{model_id} training claim")
    if not _type_exact_equal(
        claim, _training_claim_document(registry, model_id, claim.get("createdUtc", ""))
    ):
        raise ValueError(f"{model_id} training claim changed")
    history_path = _canonical_slot(registry, f"trainingHistory:{model_id}")
    _verify_training_history(history_path, registry=registry, model_id=model_id)
    batch_order_path = _canonical_slot(
        registry, f"trainingBatchOrder:{model_id}"
    )
    batch_order_identity = _verify_training_batch_order(
        batch_order_path, registry=registry, model_id=model_id
    )
    model_path = _canonical_slot(registry, f"model:{model_id}")
    manifest_path = _canonical_slot(registry, f"trainingManifest:{model_id}")
    manifest = _load_json(manifest_path, f"{model_id} training manifest")
    _exact_keys(manifest, TRAINING_MANIFEST_FIELDS, f"{model_id} training manifest")
    expected = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TRAINING_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "committed-exact-preregistered-training-run",
        "createdUtc": manifest.get("createdUtc"),
        "preregistration": _identity(registry.preregistration),
        "predecessor": _training_predecessor(registry, model_id),
        "claim": _identity(claim_path),
        "authorityManifest": _identity(
            _canonical_slot(registry, "authorityManifest")
        ),
        "modelId": model_id,
        "candidateId": candidate,
        "purpose": purpose,
        "seed": seed,
        "recipe": dict(recipe),
        "optimizerProtocol": dict(OPTIMIZER_PROTOCOL),
        "batchOrderSha256": _training_batch_order_sha256(
            registry, model_id, seed
        ),
        "actualBatchOrderTranscript": batch_order_identity,
        "trainerExecutable": registry.document["trainerExecutable"],
        "trainerRunner": registry.document["trainerRunner"],
        "runtimeManifest": registry.document["runtimeManifest"],
        "contractSource": registry.document["contractSource"],
        "initializerModel": _identity(
            _canonical_slot(registry, "authorityInitializerModel")
        ),
        "trainCorpus": _identity(
            _canonical_slot(registry, "authorityTrainCorpus")
        ),
        "trainStaticHce": _identity(
            _canonical_slot(registry, "authorityTrainHce")
        ),
        "history": _identity(history_path),
        "model": _identity(model_path),
        "command": _training_command(
            registry,
            model_id=model_id,
            candidate=candidate,
            purpose=purpose,
            seed=seed,
            recipe=recipe,
        ),
        "resultInformationRead": False,
        "validationTargetRowsDecoded": 0,
        "heldOutTargetRowsDecoded": 0,
        "heldOutTargetFieldsDecoded": 0,
    }
    if not _type_exact_equal(manifest, expected):
        raise ValueError(f"{model_id} training manifest changed")
    created = _parse_timestamp(manifest["createdUtc"], f"{model_id} manifest createdUtc")
    if created <= _parse_timestamp(claim["createdUtc"], f"{model_id} claim createdUtc"):
        raise ValueError(f"{model_id} training chronology changed")
    return manifest


def _execute_training(registry: Generation6Registry, model_id: str) -> dict[str, Any]:
    candidate, purpose, seed, recipe = _training_spec(registry, model_id)
    predecessor = _training_predecessor(registry, model_id)
    predecessor_document = _load_json(
        Path(predecessor["path"]), f"{model_id} predecessor"
    )
    before_files = _audit_namespace(registry)
    created = _utc_after(predecessor_document["createdUtc"])
    claim_path = _ensure_slot_parent(registry, f"trainingClaim:{model_id}")
    _exclusive_json(claim_path, _training_claim_document(registry, model_id, created))
    command = _training_command(
        registry,
        model_id=model_id,
        candidate=candidate,
        purpose=purpose,
        seed=seed,
        recipe=recipe,
    )
    dependency_paths = (
        Path(registry.document["trainerExecutable"]["path"]),
        Path(registry.document["trainerRunner"]["path"]),
        Path(registry.document["runtimeManifest"]["path"]),
        Path(registry.document["contractSource"]["path"]),
        _canonical_slot(registry, "authorityTrainCorpus"),
        _canonical_slot(registry, "authorityTrainHce"),
        _canonical_slot(registry, "authorityInitializerModel"),
    )
    dependency_identities = {
        str(path): _identity(path) for path in dependency_paths
    }
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=3600,
        cwd=registry.namespace,
        env={},
    )
    if completed.returncode != 0:
        raise ValueError(
            f"pinned trainer failed for {model_id}: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    if completed.stdout.strip() or completed.stderr.strip():
        raise ValueError("pinned trainer emitted unregistered output")
    for path in dependency_paths:
        if not _type_exact_equal(
            _identity(path), dependency_identities[str(path)]
        ):
            raise ValueError("pinned training dependency changed during execution")
    partial_files = before_files | {
        _relative_slot(f"trainingClaim:{model_id}"),
        _relative_slot(f"model:{model_id}"),
        _relative_slot(f"trainingHistory:{model_id}"),
        _relative_slot(f"trainingBatchOrder:{model_id}"),
    }
    _audit_namespace(registry, exact_files=partial_files)
    history_path = _canonical_slot(registry, f"trainingHistory:{model_id}")
    _verify_training_history(history_path, registry=registry, model_id=model_id)
    batch_order_path = _canonical_slot(
        registry, f"trainingBatchOrder:{model_id}"
    )
    batch_order_identity = _verify_training_batch_order(
        batch_order_path, registry=registry, model_id=model_id
    )
    model_path = _safe_existing_file(
        _canonical_slot(registry, f"model:{model_id}")
    )
    if _identity(model_path)["bytes"] <= 0:
        raise ValueError("pinned trainer produced an empty model")
    manifest_created = _utc_after(created)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TRAINING_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "committed-exact-preregistered-training-run",
        "createdUtc": manifest_created,
        "preregistration": _identity(registry.preregistration),
        "predecessor": predecessor,
        "claim": _identity(claim_path),
        "authorityManifest": _identity(
            _canonical_slot(registry, "authorityManifest")
        ),
        "modelId": model_id,
        "candidateId": candidate,
        "purpose": purpose,
        "seed": seed,
        "recipe": dict(recipe),
        "optimizerProtocol": dict(OPTIMIZER_PROTOCOL),
        "batchOrderSha256": _training_batch_order_sha256(
            registry, model_id, seed
        ),
        "actualBatchOrderTranscript": batch_order_identity,
        "trainerExecutable": registry.document["trainerExecutable"],
        "trainerRunner": registry.document["trainerRunner"],
        "runtimeManifest": registry.document["runtimeManifest"],
        "contractSource": registry.document["contractSource"],
        "initializerModel": _identity(
            _canonical_slot(registry, "authorityInitializerModel")
        ),
        "trainCorpus": _identity(
            _canonical_slot(registry, "authorityTrainCorpus")
        ),
        "trainStaticHce": _identity(
            _canonical_slot(registry, "authorityTrainHce")
        ),
        "history": _identity(history_path),
        "model": _identity(model_path),
        "command": command,
        "resultInformationRead": False,
        "validationTargetRowsDecoded": 0,
        "heldOutTargetRowsDecoded": 0,
        "heldOutTargetFieldsDecoded": 0,
    }
    manifest_path = _canonical_slot(registry, f"trainingManifest:{model_id}")
    identity = _exclusive_json(manifest_path, manifest)
    _verify_training_run(registry, model_id)
    _audit_namespace(
        registry,
        exact_files=partial_files | {
            _relative_slot(f"trainingManifest:{model_id}")
        },
    )
    return identity


def train_primary(candidate_id: str) -> dict[str, Any]:
    """Run the next frozen primary recipe; candidates are append-only A/B/C."""

    registry = verify_canonical_namespace()
    _verify_materialized_authority(registry)
    if candidate_id not in CANDIDATES:
        raise ValueError("unknown primary candidate")
    index = CANDIDATES.index(candidate_id)
    _audit_namespace(registry, exact_files=_completed_primary_files(index))
    for previous in CANDIDATES[:index]:
        _verify_training_run(registry, previous)
    identity = _execute_training(registry, candidate_id)
    _audit_namespace(registry, exact_files=_completed_primary_files(index + 1))
    return identity


def _domain_seed(base_seed: int, purpose: str, candidate: str) -> int:
    if candidate not in CANDIDATES:
        raise ValueError(f"unknown candidate {candidate!r}")
    digest = hashlib.sha256(
        f"{base_seed}|{purpose}|{candidate}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "little", signed=False)


@dataclass(frozen=True)
class LabelAccess:
    purpose: str
    split: str
    model_id: str
    candidate_id: str | None
    training_seed: int | None
    authority_sha256: str
    root_inventory_sha256: str
    root_ids: tuple[str, ...]

    @classmethod
    def primary_training(
        cls, candidate_id: str, authority: VerifiedAuthority
    ) -> "LabelAccess":
        return cls._bound(
            purpose="primary-training",
            split="train",
            model_id=candidate_id,
            candidate_id=candidate_id,
            training_seed=_domain_seed(
                PRIMARY_TRAINING_SEED_BASE, "primary-training", candidate_id
            ),
            authority=authority,
        )

    @classmethod
    def validation_selection(
        cls, model_id: str, authority: VerifiedAuthority
    ) -> "LabelAccess":
        if model_id != "I0" and model_id not in CANDIDATES:
            raise ValueError("validation model must be I0 or a frozen G6 candidate")
        return cls._bound(
            purpose="validation-selection",
            split="validation",
            model_id=model_id,
            candidate_id=None if model_id == "I0" else model_id,
            training_seed=None,
            authority=authority,
        )

    @classmethod
    def robustness_training(
        cls, candidate_id: str, authority: VerifiedAuthority
    ) -> "LabelAccess":
        return cls._bound(
            purpose="robustness-training",
            split="train",
            model_id=f"{candidate_id}-robustness",
            candidate_id=candidate_id,
            training_seed=_domain_seed(
                ROBUSTNESS_TRAINING_SEED_BASE,
                "robustness-training",
                candidate_id,
            ),
            authority=authority,
        )

    @classmethod
    def robustness_validation(
        cls, candidate_id: str, authority: VerifiedAuthority
    ) -> "LabelAccess":
        return cls._bound(
            purpose="robustness-validation",
            split="validation",
            model_id=f"{candidate_id}-robustness",
            candidate_id=candidate_id,
            training_seed=_domain_seed(
                ROBUSTNESS_TRAINING_SEED_BASE,
                "robustness-training",
                candidate_id,
            ),
            authority=authority,
        )

    @classmethod
    def _bound(
        cls,
        *,
        purpose: str,
        split: str,
        model_id: str,
        candidate_id: str | None,
        training_seed: int | None,
        authority: VerifiedAuthority,
    ) -> "LabelAccess":
        inventory = authority.binding["rootInventories"][split]
        return cls(
            purpose=purpose,
            split=split,
            model_id=model_id,
            candidate_id=candidate_id,
            training_seed=training_seed,
            authority_sha256=authority.binding_sha256,
            root_inventory_sha256=inventory["sha256"],
            root_ids=authority.root_ids_by_split[split],
        )


def _expected_access(access: LabelAccess, authority: VerifiedAuthority) -> LabelAccess:
    if access.purpose == "primary-training":
        return LabelAccess.primary_training(str(access.candidate_id), authority)
    if access.purpose == "validation-selection":
        return LabelAccess.validation_selection(access.model_id, authority)
    if access.purpose == "robustness-training":
        return LabelAccess.robustness_training(str(access.candidate_id), authority)
    if access.purpose == "robustness-validation":
        return LabelAccess.robustness_validation(str(access.candidate_id), authority)
    raise ValueError("unknown label-access purpose")


def _validate_access(
    access: LabelAccess, authority: VerifiedAuthority
) -> VerifiedAuthority:
    authority = _reverify_authority(authority)
    try:
        expected = _expected_access(access, authority)
    except (ValueError, FileNotFoundError):
        raise
    if access != expected:
        error = "label access differs from sealed authority/root inventory/seed"
        raise ValueError(error)
    return authority


@dataclass(frozen=True)
class DecisionLabel:
    root_id: str
    component_id: str
    split: str
    sibling_id: str
    child_ofen: str
    phase: str
    root_side: str
    deep_rank: int
    deep_regret_cp: int
    deep_score_cp_root: int
    deep_score_cp_child_stm: int
    handcrafted_cp_child_stm: int
    residual_target_cp: float


@dataclass(frozen=True)
class DecisionRoot:
    root_id: str
    component_id: str
    split: str
    phase: str
    root_side: str
    siblings: tuple[DecisionLabel, ...]

    @property
    def sibling_ids(self) -> tuple[str, ...]:
        return tuple(label.sibling_id for label in self.siblings)


@dataclass(frozen=True)
class DecisionObjective:
    total_loss: float
    pointwise_huber: float
    listwise_cross_entropy: float
    near_equal_top_set_loss: float
    gradient_by_sibling: Mapping[str, float]
    top_set_sibling_ids: tuple[str, ...]


def _decode_label_record(
    record: Mapping[str, Any], routing: Mapping[str, str], hce_cp: int, location: str
) -> DecisionLabel:
    _exact_keys(record, LABEL_FIELDS, location)
    if type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1:
        raise ValueError(f"{location}: schemaVersion is not exact integer 1")
    for field in LABEL_STRING_FIELDS:
        if type(record[field]) is not str or record[field] != routing[field]:
            raise ValueError(f"{location}: routed field {field} changed")
    rank = _exact_int(record["deepRank"], f"{location} deepRank", 1, 4)
    regret = _exact_int(
        record["deepRegretCp"], f"{location} deepRegretCp", 0, REGRET_MAX_CP
    )
    root_score = _exact_int(
        record["deepScoreCpRoot"],
        f"{location} deepScoreCpRoot",
        -SCORE_ABS_LIMIT_CP,
        SCORE_ABS_LIMIT_CP,
    )
    child_score = _exact_int(
        record["deepScoreCpChildStm"],
        f"{location} deepScoreCpChildStm",
        -SCORE_ABS_LIMIT_CP,
        SCORE_ABS_LIMIT_CP,
    )
    if not math.isclose(
        float(root_score),
        float(-child_score),
        rel_tol=0.0,
        abs_tol=SCORE_COHERENCE_ABS_TOLERANCE_CP,
    ):
        raise ValueError(f"{location}: root/child teacher score sign changed")
    hce = _exact_int(
        hce_cp,
        f"{location} projected static HCE",
        -SCORE_ABS_LIMIT_CP,
        SCORE_ABS_LIMIT_CP,
    )
    return DecisionLabel(
        root_id=routing["rootId"],
        component_id=routing["leakageComponentId"],
        split=routing["split"],
        sibling_id=routing["childId"],
        child_ofen=" ".join(routing["childOfen"].split()),
        phase=routing["phase"],
        root_side=routing["parentSideToMove"],
        deep_rank=rank,
        deep_regret_cp=regret,
        deep_score_cp_root=root_score,
        deep_score_cp_child_stm=child_score,
        handcrafted_cp_child_stm=hce,
        residual_target_cp=float(
            np.clip(child_score - hce, -RESIDUAL_CLIP_CP, RESIDUAL_CLIP_CP)
        ),
    )


def _canonical_siblings(root: DecisionRoot) -> tuple[DecisionLabel, ...]:
    siblings = tuple(sorted(root.siblings, key=lambda label: label.sibling_id))
    if len(siblings) != 4 or len({label.sibling_id for label in siblings}) != 4:
        raise ValueError("decision root requires exactly four unique siblings")
    if any(
        label.root_id != root.root_id
        or label.component_id != root.component_id
        or label.split != root.split
        or label.phase != root.phase
        or label.root_side != root.root_side
        for label in siblings
    ):
        raise ValueError("decision root contains a sibling from another root")
    ranks = sorted(label.deep_rank for label in siblings)
    if ranks != [1, 2, 3, 4]:
        raise ValueError("decision root does not contain exact ranks 1..4")
    ranked = sorted(siblings, key=lambda label: label.deep_rank)
    if ranked[0].deep_regret_cp != 0:
        raise ValueError("teacher best has nonzero regret")
    if any(
        left.deep_regret_cp > right.deep_regret_cp
        for left, right in zip(ranked, ranked[1:])
    ):
        raise ValueError("deep ranks and regrets disagree")
    best_score = ranked[0].deep_score_cp_root
    for label in siblings:
        expected = best_score - label.deep_score_cp_root
        if not math.isclose(
            float(expected),
            float(label.deep_regret_cp),
            rel_tol=0.0,
            abs_tol=SCORE_COHERENCE_ABS_TOLERANCE_CP,
        ):
            raise ValueError("teacher score and regret disagree")
        if _child_side(label.child_ofen, label.sibling_id) == root.root_side:
            raise ValueError("child OFEN side is not opposite parent side")
    return siblings


def load_decision_roots(
    path: Path,
    *,
    authority: TrainingAuthority,
    access: LabelAccess,
) -> tuple[DecisionRoot, ...]:
    """Load train/validation labels; held-out is never exposed by this API."""

    verified = verify_training_authority(corpus=path, authority=authority)
    verified = _validate_access(access, verified)
    if access.split == "heldOut" or access.purpose == "heldout-evaluation":
        raise HeldoutAccessError(
            "raw held-out roots are unavailable; use consume_heldout_once()"
        )
    selected: list[DecisionLabel] = []
    for opaque_row in verified.opaque.rows:
        if opaque_row.routing["split"] != access.split:
            continue
        record = _strict_json_loads(
            opaque_row.line, location=opaque_row.location
        )
        if not isinstance(record, dict):
            raise ValueError(f"{opaque_row.location}: label row is not an object")
        child_id = opaque_row.routing["childId"]
        selected.append(
            _decode_label_record(
                record,
                opaque_row.routing,
                verified.hce_by_child[child_id],
                opaque_row.location,
            )
        )
    by_root: dict[str, list[DecisionLabel]] = defaultdict(list)
    for label in selected:
        by_root[label.root_id].append(label)
    if set(by_root) != set(access.root_ids):
        raise ValueError("decoded root inventory differs from sealed access")
    roots: list[DecisionRoot] = []
    for root_id in sorted(by_root):
        first = by_root[root_id][0]
        root = DecisionRoot(
            root_id=root_id,
            component_id=first.component_id,
            split=first.split,
            phase=first.phase,
            root_side=first.root_side,
            siblings=tuple(by_root[root_id]),
        )
        siblings = _canonical_siblings(root)
        roots.append(
            DecisionRoot(
                root_id=root.root_id,
                component_id=root.component_id,
                split=root.split,
                phase=root.phase,
                root_side=root.root_side,
                siblings=siblings,
            )
        )
    if len(roots) * 4 != len(selected):
        raise AssertionError("selected rows are not exact four-child roots")
    return tuple(roots)


def _decode_opaque_split(
    opaque: _OpaqueCorpus,
    hce_by_child: Mapping[str, int],
    split: str,
) -> tuple[DecisionRoot, ...]:
    """Internal target decoder used only after the relevant stage claim."""

    selected: list[DecisionLabel] = []
    for opaque_row in opaque.rows:
        if opaque_row.routing["split"] != split:
            continue
        record = _strict_json_loads(opaque_row.line, location=opaque_row.location)
        if not isinstance(record, dict):
            raise ValueError(f"{opaque_row.location}: label row is not an object")
        child_id = opaque_row.routing["childId"]
        selected.append(
            _decode_label_record(
                record,
                opaque_row.routing,
                hce_by_child[child_id],
                opaque_row.location,
            )
        )
    by_root: dict[str, list[DecisionLabel]] = defaultdict(list)
    for label in selected:
        by_root[label.root_id].append(label)
    roots: list[DecisionRoot] = []
    for root_id in sorted(by_root):
        first = by_root[root_id][0]
        candidate = DecisionRoot(
            root_id=root_id,
            component_id=first.component_id,
            split=first.split,
            phase=first.phase,
            root_side=first.root_side,
            siblings=tuple(by_root[root_id]),
        )
        roots.append(
            DecisionRoot(
                root_id=root_id,
                component_id=first.component_id,
                split=first.split,
                phase=first.phase,
                root_side=first.root_side,
                siblings=_canonical_siblings(candidate),
            )
        )
    expected_ids = {
        root.root_id for root in opaque.roots if root.split == split
    }
    if {root.root_id for root in roots} != expected_ids or len(selected) != len(roots) * 4:
        raise ValueError(f"decoded {split} root inventory changed")
    return tuple(roots)


def _load_canonical_validation_roots(
    registry: Generation6Registry,
) -> tuple[DecisionRoot, ...]:
    corpus = _canonical_slot(registry, "authorityValidationCorpus")
    opaque = _opaque_corpus_rows(corpus)
    child_ids = {row.routing["childId"] for row in opaque.rows}
    hce = _parse_hce_projection(
        _canonical_slot(registry, "authorityValidationHce"), child_ids
    )
    roots = _decode_opaque_split(opaque, hce, "validation")
    if tuple(root.root_id for root in roots) != tuple(
        sorted(registry.authority.root_ids_by_split["validation"])
    ):
        raise ValueError("canonical validation decode differs from preregistration")
    return roots


def _huber_loss_and_gradient(error_cp: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    normalized = error_cp.astype(np.float64) / HUBER_NORMALIZER_CP
    absolute = np.abs(normalized)
    quadratic = absolute <= HUBER_DELTA_NORMALIZED
    loss = np.where(
        quadratic,
        0.5 * normalized * normalized,
        HUBER_DELTA_NORMALIZED
        * (absolute - 0.5 * HUBER_DELTA_NORMALIZED),
    )
    gradient = np.where(
        quadratic,
        normalized,
        HUBER_DELTA_NORMALIZED * np.sign(normalized),
    ) / HUBER_NORMALIZER_CP
    return loss, gradient


def _log_softmax(logits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    maximum = float(np.max(logits))
    shifted = logits.astype(np.float64) - maximum
    log_denominator = math.log(float(np.exp(shifted).sum()))
    log_probabilities = shifted - log_denominator
    return log_probabilities, np.exp(log_probabilities)


def _recipe(candidate_or_recipe: str | Mapping[str, float]) -> Mapping[str, float]:
    recipe = (
        CANDIDATE_RECIPES[candidate_or_recipe]
        if isinstance(candidate_or_recipe, str)
        else candidate_or_recipe
    )
    if set(recipe) != {"listwiseSoftmaxWeight", "nearEqualTopSetWeight"}:
        raise ValueError("candidate recipe inventory changed")
    values = {key: _finite_number(value, key) for key, value in recipe.items()}
    if any(value < 0.0 for value in values.values()):
        raise ValueError("candidate loss weight is negative")
    return values


def decision_root_objective(
    root: DecisionRoot,
    residual_predictions: Mapping[str, float],
    candidate_or_recipe: str | Mapping[str, float],
) -> DecisionObjective:
    recipe = _recipe(candidate_or_recipe)
    siblings = _canonical_siblings(root)
    if set(residual_predictions) != {label.sibling_id for label in siblings}:
        raise ValueError("prediction sibling inventory differs from root")
    predictions = np.asarray(
        [
            _finite_number(
                residual_predictions[label.sibling_id],
                f"prediction {root.root_id}/{label.sibling_id}",
            )
            for label in siblings
        ],
        dtype=np.float64,
    )
    targets = np.asarray(
        [label.residual_target_cp for label in siblings], dtype=np.float64
    )
    point_losses, point_gradient = _huber_loss_and_gradient(predictions - targets)
    point_loss = float(point_losses.mean())
    point_gradient /= 4.0

    regrets = np.asarray([label.deep_regret_cp for label in siblings], dtype=np.float64)
    _, teacher_probabilities = _log_softmax(
        -regrets / LISTWISE_TEMPERATURE_CP
    )
    child_totals = np.asarray(
        [label.handcrafted_cp_child_stm for label in siblings], dtype=np.float64
    ) + predictions
    student_logits = -child_totals / LISTWISE_TEMPERATURE_CP
    student_log, student_probabilities = _log_softmax(student_logits)
    listwise_loss = float(-np.sum(teacher_probabilities * student_log))
    listwise_gradient = (
        teacher_probabilities - student_probabilities
    ) / LISTWISE_TEMPERATURE_CP

    top_mask = regrets <= TOP_SET_MAX_REGRET_CP
    top_maximum = float(np.max(student_logits[top_mask]))
    top_log_sum = top_maximum + math.log(
        float(np.exp(student_logits[top_mask] - top_maximum).sum())
    )
    all_maximum = float(np.max(student_logits))
    all_log_sum = all_maximum + math.log(
        float(np.exp(student_logits - all_maximum).sum())
    )
    top_loss = all_log_sum - top_log_sum
    conditional_top = np.zeros(4, dtype=np.float64)
    conditional_top[top_mask] = np.exp(student_logits[top_mask] - top_log_sum)
    top_gradient = (
        conditional_top - student_probabilities
    ) / LISTWISE_TEMPERATURE_CP

    gradient = (
        point_gradient
        + recipe["listwiseSoftmaxWeight"] * listwise_gradient
        + recipe["nearEqualTopSetWeight"] * top_gradient
    )
    total = (
        point_loss
        + recipe["listwiseSoftmaxWeight"] * listwise_loss
        + recipe["nearEqualTopSetWeight"] * top_loss
    )
    if not math.isfinite(total) or not np.all(np.isfinite(gradient)):
        raise FloatingPointError("G6 decision objective became non-finite")
    return DecisionObjective(
        total_loss=float(total),
        pointwise_huber=point_loss,
        listwise_cross_entropy=listwise_loss,
        near_equal_top_set_loss=float(top_loss),
        gradient_by_sibling={
            label.sibling_id: float(gradient[index])
            for index, label in enumerate(siblings)
        },
        top_set_sibling_ids=tuple(
            label.sibling_id
            for index, label in enumerate(siblings)
            if bool(top_mask[index])
        ),
    )


def decision_batch_objective(
    roots: Sequence[DecisionRoot],
    residual_predictions: Mapping[str, Mapping[str, float]],
    candidate_or_recipe: str | Mapping[str, float],
) -> tuple[float, dict[str, dict[str, float]], dict[str, float]]:
    if not roots:
        raise ValueError("decision batch is empty")
    root_ids = [root.root_id for root in roots]
    if len(set(root_ids)) != len(root_ids) or set(residual_predictions) != set(root_ids):
        raise ValueError("batch root inventory changed")
    for root in roots:
        _canonical_siblings(root)
    objectives = [
        decision_root_objective(
            root, residual_predictions[root.root_id], candidate_or_recipe
        )
        for root in roots
    ]
    scale = 1.0 / len(objectives)
    gradients = {
        root.root_id: {
            sibling: value * scale
            for sibling, value in objective.gradient_by_sibling.items()
        }
        for root, objective in zip(roots, objectives)
    }
    components = {
        "pointwiseHuber": float(np.mean([item.pointwise_huber for item in objectives])),
        "listwiseCrossEntropy": float(
            np.mean([item.listwise_cross_entropy for item in objectives])
        ),
        "nearEqualTopSetLoss": float(
            np.mean([item.near_equal_top_set_loss for item in objectives])
        ),
        "completeRootCount": len(objectives),
        "rootWeightEach": scale,
    }
    return float(np.mean([item.total_loss for item in objectives])), gradients, components


def complete_root_batches(
    roots: Sequence[DecisionRoot], *, roots_per_batch: int, seed: int
) -> tuple[tuple[DecisionRoot, ...], ...]:
    """Return equal-sized complete-root batches; never overweight a short tail."""

    if type(roots_per_batch) is not int or roots_per_batch <= 0:
        raise ValueError("roots_per_batch must be positive")
    if type(seed) is not int or seed < 0 or seed > (1 << 64) - 1:
        raise ValueError("batch seed must be an unsigned 64-bit integer")
    if not roots or len(roots) % roots_per_batch != 0:
        raise ValueError(
            "root count must be exactly divisible by roots_per_batch; no short final batch"
        )
    root_ids = [root.root_id for root in roots]
    if len(set(root_ids)) != len(root_ids):
        raise ValueError("batch source repeats a root")
    for root in roots:
        _canonical_siblings(root)
    ordered = sorted(roots, key=lambda root: root.root_id)
    permutation = np.random.default_rng(seed).permutation(len(ordered))
    shuffled = [ordered[int(index)] for index in permutation]
    batches = tuple(
        tuple(shuffled[start : start + roots_per_batch])
        for start in range(0, len(shuffled), roots_per_batch)
    )
    if any(len(batch) != roots_per_batch for batch in batches):
        raise AssertionError("complete-root divisibility contract failed")
    return batches


def _root_metric(
    root: DecisionRoot, residual_predictions: Mapping[str, float]
) -> dict[str, float]:
    siblings = _canonical_siblings(root)
    objective = decision_root_objective(
        root,
        residual_predictions,
        {"listwiseSoftmaxWeight": 0.0, "nearEqualTopSetWeight": 0.0},
    )
    predictions = np.asarray(
        [float(residual_predictions[label.sibling_id]) for label in siblings],
        dtype=np.float64,
    )
    totals = np.asarray(
        [label.handcrafted_cp_child_stm for label in siblings], dtype=np.float64
    ) + predictions
    chosen = int(np.argmax(-totals))
    top_set = {
        label.sibling_id
        for label in siblings
        if label.deep_regret_cp <= TOP_SET_MAX_REGRET_CP
    }
    regrets = np.asarray(
        [label.deep_regret_cp for label in siblings], dtype=np.float64
    )
    _, teacher = _log_softmax(-regrets / LISTWISE_TEMPERATURE_CP)
    student_log, _ = _log_softmax(-totals / LISTWISE_TEMPERATURE_CP)
    return {
        "topSetAccuracy": float(siblings[chosen].sibling_id in top_set),
        "meanChosenMoveRegretCp": float(siblings[chosen].deep_regret_cp),
        "listwiseCrossEntropy": float(-np.sum(teacher * student_log)),
        "pointwiseHuber": objective.pointwise_huber,
    }


METRIC_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "modelId",
        "resultInformationRead",
        "aggregation",
        "decisionRoots",
        "bindings",
        "cells",
        "macro",
    }
)
CELL_METRIC_FIELDS = frozenset(
    {"roots", "rootIdsSha256", *_METRIC_NAMES}
)


def publish_metric_report(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError(
        "caller-supplied metric publication was removed; use evaluate_validation()"
    )


def _non_regression_gate_report(
    candidate: Mapping[str, Any], baseline: Mapping[str, Any], health: bool
) -> dict[str, bool]:
    return {
        "deploymentHealth": health,
        "macroTopSetAccuracy": (
            candidate["macro"]["topSetAccuracy"]
            >= baseline["macro"]["topSetAccuracy"]
            - NON_REGRESSION_GATES["maximumMacroTopSetAccuracyRegression"]
        ),
        "macroChosenMoveRegret": (
            candidate["macro"]["meanChosenMoveRegretCp"]
            <= baseline["macro"]["meanChosenMoveRegretCp"]
            + NON_REGRESSION_GATES["maximumMacroChosenMoveRegretIncreaseCp"]
        ),
        "macroListwiseCrossEntropy": (
            candidate["macro"]["listwiseCrossEntropy"]
            <= baseline["macro"]["listwiseCrossEntropy"]
            + NON_REGRESSION_GATES[
                "maximumMacroListwiseCrossEntropyIncrease"
            ]
        ),
        "macroPointwiseHuber": (
            candidate["macro"]["pointwiseHuber"]
            <= baseline["macro"]["pointwiseHuber"]
            + NON_REGRESSION_GATES["maximumMacroPointwiseHuberIncrease"]
        ),
        "everyCellTopSetAccuracy": all(
            candidate["cells"][cell]["topSetAccuracy"]
            >= baseline["cells"][cell]["topSetAccuracy"]
            - NON_REGRESSION_GATES["maximumCellTopSetAccuracyRegression"]
            for cell in _CELL_KEYS
        ),
        "everyCellChosenMoveRegret": all(
            candidate["cells"][cell]["meanChosenMoveRegretCp"]
            <= baseline["cells"][cell]["meanChosenMoveRegretCp"]
            + NON_REGRESSION_GATES[
                "maximumCellChosenMoveRegretIncreaseCp"
            ]
            for cell in _CELL_KEYS
        ),
    }


def _select_validation_candidate_math(
    *,
    authority: VerifiedAuthority,
    initializer_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Mapping[str, Any]],
    deployment_health_passed: Mapping[str, bool],
) -> dict[str, Any]:
    authority = _reverify_authority(authority)
    initializer = _validated_metric(
        initializer_metrics,
        "initializer",
        authority=authority,
        expected_model_id="I0",
        expected_purpose="validation-selection",
    )
    if set(candidate_metrics) != set(CANDIDATES):
        raise ValueError("selection requires all three frozen candidates")
    if set(deployment_health_passed) != set(CANDIDATES) or any(
        type(value) is not bool for value in deployment_health_passed.values()
    ):
        raise ValueError("deployment-health inventory/types changed")
    evaluated: dict[str, Any] = {}
    eligible: list[str] = []
    for candidate in CANDIDATES:
        metric = _validated_metric(
            candidate_metrics[candidate],
            candidate,
            authority=authority,
            expected_model_id=candidate,
            expected_purpose="validation-selection",
        )
        gates = _non_regression_gate_report(
            metric, initializer, deployment_health_passed[candidate]
        )
        passed = all(gates.values())
        key = [
            -float(metric["macro"]["topSetAccuracy"]),
            float(metric["macro"]["meanChosenMoveRegretCp"]),
            float(metric["macro"]["listwiseCrossEntropy"]),
            float(metric["macro"]["pointwiseHuber"]),
            TIE_PRIORITY.index(candidate),
        ]
        evaluated[candidate] = {
            "eligible": passed,
            "gates": gates,
            "lexicographicKey": key,
        }
        if passed:
            eligible.append(candidate)
    winner = (
        min(
            eligible,
            key=lambda candidate: tuple(
                evaluated[candidate]["lexicographicKey"]
            ),
        )
        if eligible
        else None
    )
    return {
        "fixedNonRegressionGates": dict(NON_REGRESSION_GATES),
        "lexicographicOrder": list(VALIDATION_LEXICOGRAPHIC_ORDER),
        "candidateTiePriority": list(TIE_PRIORITY),
        "candidates": evaluated,
        "selectedCandidateId": winner,
    }


SELECTION_SEAL_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "resultInformationRead",
        "authority",
        "initializerMetric",
        "candidateMetrics",
        "models",
        "primaryTrainingSeeds",
        "robustnessTrainingSeeds",
        "deploymentHealthPassed",
        "decision",
        "selectedCandidateId",
        "selectedModel",
        "heldOutTargetRowsDecodedAtSeal",
        "heldOutTargetFieldsDecodedAtSeal",
    }
)


def _load_metric_artifact(
    path: Path,
    *,
    authority: VerifiedAuthority,
    model_id: str,
    purpose: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _load_json(path, f"{model_id} metric")
    _validated_metric(
        value,
        f"{model_id} metric",
        authority=authority,
        expected_model_id=model_id,
        expected_purpose=purpose,
    )
    return value, _identity(path)


def _legacy_expected_validation_selection_seal(
    *,
    authority: VerifiedAuthority,
    initializer_metric: Path,
    candidate_metrics: Mapping[str, Path],
    deployment_health_passed: Mapping[str, bool],
    created_utc: str,
) -> dict[str, Any]:
    authority = _reverify_authority(authority)
    created = _parse_timestamp(created_utc, "validation selection createdUtc")
    if created <= _parse_timestamp(authority.hce_created_utc, "HCE createdUtc"):
        raise ValueError("validation selection must follow static-HCE authority")
    if set(candidate_metrics) != set(CANDIDATES):
        raise ValueError("selection metric artifact inventory changed")
    initializer, initializer_identity = _load_metric_artifact(
        initializer_metric,
        authority=authority,
        model_id="I0",
        purpose="validation-selection",
    )
    reports: dict[str, dict[str, Any]] = {}
    identities: dict[str, dict[str, Any]] = {}
    for candidate in CANDIDATES:
        reports[candidate], identities[candidate] = _load_metric_artifact(
            candidate_metrics[candidate],
            authority=authority,
            model_id=candidate,
            purpose="validation-selection",
        )
    decision = _select_validation_candidate_math(
        authority=authority,
        initializer_metrics=initializer,
        candidate_metrics=reports,
        deployment_health_passed=deployment_health_passed,
    )
    selected = decision["selectedCandidateId"]
    if selected is None:
        raise ValueError("no validation candidate is eligible for a selection seal")
    models = {
        "I0": initializer["bindings"]["model"],
        **{
            candidate: reports[candidate]["bindings"]["model"]
            for candidate in CANDIDATES
        },
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": VALIDATION_SELECTION_KIND,
        "profileId": PROFILE_ID,
        "status": "selected-after-exact-result-blind-validation",
        "createdUtc": created_utc,
        "resultInformationRead": False,
        "authority": authority.binding,
        "initializerMetric": initializer_identity,
        "candidateMetrics": identities,
        "models": models,
        "primaryTrainingSeeds": {
            candidate: _domain_seed(
                PRIMARY_TRAINING_SEED_BASE, "primary-training", candidate
            )
            for candidate in CANDIDATES
        },
        "robustnessTrainingSeeds": {
            candidate: _domain_seed(
                ROBUSTNESS_TRAINING_SEED_BASE,
                "robustness-training",
                candidate,
            )
            for candidate in CANDIDATES
        },
        "deploymentHealthPassed": dict(deployment_health_passed),
        "decision": decision,
        "selectedCandidateId": selected,
        "selectedModel": models[selected],
        "heldOutTargetRowsDecodedAtSeal": 0,
        "heldOutTargetFieldsDecodedAtSeal": 0,
    }


def publish_validation_selection_seal(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError(
        "caller-supplied selection seals were removed; use select_primary()"
    )


def _legacy_verify_validation_selection_seal(
    path: Path, *, authority: VerifiedAuthority
) -> dict[str, Any]:
    authority = _reverify_authority(authority)
    document = _load_json(path, "validation-selection seal")
    _exact_keys(document, SELECTION_SEAL_FIELDS, "validation-selection seal")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != VALIDATION_SELECTION_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"]
        != "selected-after-exact-result-blind-validation"
        or type(document["resultInformationRead"]) is not bool
        or document["resultInformationRead"] is not False
        or document["heldOutTargetRowsDecodedAtSeal"] != 0
        or type(document["heldOutTargetRowsDecodedAtSeal"]) is not int
        or document["heldOutTargetFieldsDecodedAtSeal"] != 0
        or type(document["heldOutTargetFieldsDecodedAtSeal"]) is not int
    ):
        raise ValueError("validation-selection seal schema/status changed")
    if not isinstance(document["initializerMetric"], dict):
        raise ValueError("validation-selection initializer metric is not an identity")
    initializer_path = Path(
        _verify_identity_record(
            document["initializerMetric"], "selection initializer metric"
        )["path"]
    )
    candidate_identities = document["candidateMetrics"]
    if not isinstance(candidate_identities, dict) or set(candidate_identities) != set(CANDIDATES):
        raise ValueError("selection candidate metric identities changed")
    candidate_paths = {
        candidate: Path(
            _verify_identity_record(
                candidate_identities[candidate], f"selection {candidate} metric"
            )["path"]
        )
        for candidate in CANDIDATES
    }
    health = document["deploymentHealthPassed"]
    if not isinstance(health, dict):
        raise ValueError("selection health record is not an object")
    expected = _legacy_expected_validation_selection_seal(
        authority=authority,
        initializer_metric=initializer_path,
        candidate_metrics=candidate_paths,
        deployment_health_passed=health,
        created_utc=document["createdUtc"],
    )
    if not _type_exact_equal(document, expected):
        raise ValueError("validation-selection seal differs from recomputation")
    return document


ROBUSTNESS_SEAL_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "resultInformationRead",
        "authority",
        "validationSelectionSeal",
        "selectedCandidateId",
        "primarySelectedModel",
        "robustnessModel",
        "primaryMetric",
        "robustnessMetric",
        "primaryTrainingSeed",
        "robustnessTrainingSeed",
        "deploymentHealthPassed",
        "validationNonRegressionGates",
        "heldOutTargetRowsDecodedAtSeal",
        "heldOutTargetFieldsDecodedAtSeal",
    }
)


def _legacy_expected_robustness_seal(
    *,
    authority: VerifiedAuthority,
    validation_selection_seal: Path,
    robustness_metric: Path,
    deployment_health_passed: bool,
    created_utc: str,
) -> dict[str, Any]:
    authority = _reverify_authority(authority)
    if type(deployment_health_passed) is not bool or not deployment_health_passed:
        raise ValueError("robustness seal requires a passed deployment-health gate")
    selection = _legacy_verify_validation_selection_seal(
        validation_selection_seal, authority=authority
    )
    created = _parse_timestamp(created_utc, "robustness seal createdUtc")
    if created <= _parse_timestamp(selection["createdUtc"], "selection createdUtc"):
        raise ValueError("robustness seal must follow validation selection")
    candidate = selection["selectedCandidateId"]
    robustness_model_id = f"{candidate}-robustness"
    robust_report, robust_identity = _load_metric_artifact(
        robustness_metric,
        authority=authority,
        model_id=robustness_model_id,
        purpose="robustness-validation",
    )
    primary_identity = selection["candidateMetrics"][candidate]
    primary_report = _load_json(
        Path(primary_identity["path"]), f"{candidate} primary metric"
    )
    _validated_metric(
        primary_report,
        f"{candidate} primary metric",
        authority=authority,
        expected_model_id=candidate,
        expected_purpose="validation-selection",
    )
    gates = _non_regression_gate_report(
        robust_report, primary_report, deployment_health_passed
    )
    if not all(gates.values()):
        raise ValueError("robustness validation regressed against selected primary")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ROBUSTNESS_SEAL_KIND,
        "profileId": PROFILE_ID,
        "status": "passed-exact-second-seed-validation",
        "createdUtc": created_utc,
        "resultInformationRead": False,
        "authority": authority.binding,
        "validationSelectionSeal": _identity(validation_selection_seal),
        "selectedCandidateId": candidate,
        "primarySelectedModel": selection["selectedModel"],
        "robustnessModel": robust_report["bindings"]["model"],
        "primaryMetric": primary_identity,
        "robustnessMetric": robust_identity,
        "primaryTrainingSeed": selection["primaryTrainingSeeds"][candidate],
        "robustnessTrainingSeed": selection["robustnessTrainingSeeds"][candidate],
        "deploymentHealthPassed": True,
        "validationNonRegressionGates": gates,
        "heldOutTargetRowsDecodedAtSeal": 0,
        "heldOutTargetFieldsDecodedAtSeal": 0,
    }


def publish_robustness_seal(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError(
        "caller-supplied robustness seals were removed; use seal_robustness()"
    )


def _legacy_verify_robustness_seal(
    path: Path,
    *,
    authority: VerifiedAuthority,
    validation_selection_seal: Path,
) -> dict[str, Any]:
    authority = _reverify_authority(authority)
    document = _load_json(path, "robustness seal")
    _exact_keys(document, ROBUSTNESS_SEAL_FIELDS, "robustness seal")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != ROBUSTNESS_SEAL_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "passed-exact-second-seed-validation"
        or type(document["resultInformationRead"]) is not bool
        or document["resultInformationRead"] is not False
        or type(document["deploymentHealthPassed"]) is not bool
        or document["deploymentHealthPassed"] is not True
        or type(document["heldOutTargetRowsDecodedAtSeal"]) is not int
        or document["heldOutTargetRowsDecodedAtSeal"] != 0
        or type(document["heldOutTargetFieldsDecodedAtSeal"]) is not int
        or document["heldOutTargetFieldsDecodedAtSeal"] != 0
    ):
        raise ValueError("robustness seal schema/status changed")
    robust_identity = _verify_identity_record(
        document["robustnessMetric"], "robustness metric"
    )
    expected = _legacy_expected_robustness_seal(
        authority=authority,
        validation_selection_seal=validation_selection_seal,
        robustness_metric=Path(robust_identity["path"]),
        deployment_health_passed=document["deploymentHealthPassed"],
        created_utc=document["createdUtc"],
    )
    if not _type_exact_equal(document, expected):
        raise ValueError("robustness seal differs from recomputation")
    return document


HELDOUT_SEAL_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "resultInformationRead",
        "declaration",
        "authority",
        "corpus",
        "validationSelectionSeal",
        "robustnessSeal",
        "selectedCandidateId",
        "primarySelectedModel",
        "robustnessModel",
        "primaryMetric",
        "robustnessMetric",
        "primaryTrainingSeed",
        "robustnessTrainingSeed",
        "heldOutRootInventory",
        "heldOutTargetRowsDecodedAtSeal",
        "heldOutTargetFieldsDecodedAtSeal",
    }
)


def _legacy_expected_heldout_access_seal(
    *,
    authority: VerifiedAuthority,
    validation_selection_seal: Path,
    robustness_seal: Path,
    created_utc: str,
) -> dict[str, Any]:
    authority = _reverify_authority(authority)
    selection = _legacy_verify_validation_selection_seal(
        validation_selection_seal, authority=authority
    )
    robustness = _legacy_verify_robustness_seal(
        robustness_seal,
        authority=authority,
        validation_selection_seal=validation_selection_seal,
    )
    created = _parse_timestamp(created_utc, "held-out access createdUtc")
    if created <= _parse_timestamp(robustness["createdUtc"], "robustness createdUtc"):
        raise ValueError("held-out access seal must follow robustness seal")
    if robustness["selectedCandidateId"] != selection["selectedCandidateId"]:
        raise ValueError("held-out prerequisites disagree on selected candidate")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HELDOUT_SEAL_KIND,
        "profileId": PROFILE_ID,
        "status": "sealed-before-heldout-target-decode",
        "createdUtc": created_utc,
        "resultInformationRead": False,
        "declaration": HELDOUT_DECLARATION,
        "authority": authority.binding,
        "corpus": authority.binding["corpus"],
        "validationSelectionSeal": _identity(validation_selection_seal),
        "robustnessSeal": _identity(robustness_seal),
        "selectedCandidateId": selection["selectedCandidateId"],
        "primarySelectedModel": selection["selectedModel"],
        "robustnessModel": robustness["robustnessModel"],
        "primaryMetric": robustness["primaryMetric"],
        "robustnessMetric": robustness["robustnessMetric"],
        "primaryTrainingSeed": robustness["primaryTrainingSeed"],
        "robustnessTrainingSeed": robustness["robustnessTrainingSeed"],
        "heldOutRootInventory": authority.binding["rootInventories"]["heldOut"],
        "heldOutTargetRowsDecodedAtSeal": 0,
        "heldOutTargetFieldsDecodedAtSeal": 0,
    }


def publish_heldout_access_seal(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError(
        "caller-supplied held-out seals were removed; use arm_heldout()"
    )


def _legacy_verify_heldout_access_seal(
    *,
    authority: VerifiedAuthority,
    heldout_seal: Path,
    validation_selection_seal: Path,
    robustness_seal: Path,
) -> dict[str, Any]:
    try:
        authority = _reverify_authority(authority)
        document = _load_json(heldout_seal, "held-out access seal")
        _exact_keys(document, HELDOUT_SEAL_FIELDS, "held-out access seal")
        if (
            type(document["schemaVersion"]) is not int
            or document["schemaVersion"] != SCHEMA_VERSION
            or document["kind"] != HELDOUT_SEAL_KIND
            or document["profileId"] != PROFILE_ID
            or document["status"] != "sealed-before-heldout-target-decode"
            or type(document["resultInformationRead"]) is not bool
            or document["resultInformationRead"] is not False
            or type(document["declaration"]) is not str
            or document["declaration"] != HELDOUT_DECLARATION
            or type(document["heldOutTargetRowsDecodedAtSeal"]) is not int
            or document["heldOutTargetRowsDecodedAtSeal"] != 0
            or type(document["heldOutTargetFieldsDecodedAtSeal"]) is not int
            or document["heldOutTargetFieldsDecodedAtSeal"] != 0
        ):
            raise ValueError("held-out access seal schema/status changed")
        expected = _legacy_expected_heldout_access_seal(
            authority=authority,
            validation_selection_seal=validation_selection_seal,
            robustness_seal=robustness_seal,
            created_utc=document["createdUtc"],
        )
        if not _type_exact_equal(document, expected):
            raise ValueError("held-out access seal differs from recomputation")
        return document
    except (OSError, UnicodeError, ValueError, FileNotFoundError) as error:
        raise HeldoutAccessError(
            "held-out target access lacks an exact valid prerequisite seal chain"
        ) from error


# ---------------------------------------------------------------------------
# Canonical production evidence pipeline.  These are the only APIs permitted
# to create artifacts consumed by formal selection/robustness/held-out stages.

HEALTH_RUNNER_FIELDS = frozenset(
    {
        "modelSha256",
        "modelBytes",
        "finitePredictions",
        "quantizationRoundTripExact",
        "expectedNetworkBytes",
        "runtimeParity",
        "maximumAbsResidualCp",
    }
)
EVIDENCE_CLAIM_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "predecessor",
        "authorityManifest",
        "modelId",
        "model",
        "modelProvenance",
        "evaluatorExecutable",
        "evaluatorRunner",
        "evaluatorOptions",
        "runtimeManifest",
        "validationCorpus",
        "validationStaticHce",
        "resultInformationRead",
        "heldOutTargetRowsDecodedAtClaim",
        "heldOutTargetFieldsDecodedAtClaim",
    }
)
HEALTH_EVIDENCE_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "claim",
        "modelId",
        "model",
        "modelProvenance",
        "evaluatorExecutable",
        "evaluatorRunner",
        "evaluatorOptions",
        "runtimeManifest",
        "thresholds",
        "runnerResult",
        "passed",
        "resultInformationRead",
    }
)
PREDICTION_FIELDS = frozenset(
    {"schemaVersion", "kind", "profileId", "modelId", "childId", "residualCp"}
)
PREDICTION_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "claim",
        "modelId",
        "model",
        "modelProvenance",
        "evaluatorExecutable",
        "evaluatorRunner",
        "evaluatorOptions",
        "runtimeManifest",
        "validationCorpus",
        "validationStaticHce",
        "orderedOfenInventory",
        "predictionRows",
        "predictions",
        "resultInformationRead",
        "heldOutTargetRowsDecoded",
        "heldOutTargetFieldsDecoded",
    }
)
CANONICAL_METRIC_BINDING_FIELDS = frozenset(
    {
        "authoritySha256",
        "authorityManifest",
        "validationCorpus",
        "validationStaticHce",
        "model",
        "modelProvenance",
        "healthEvidence",
        "predictionManifest",
        "accessPurpose",
        "candidateId",
        "trainingSeed",
    }
)
EVIDENCE_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "predecessor",
        "claim",
        "authorityManifest",
        "modelId",
        "model",
        "modelProvenance",
        "healthEvidence",
        "predictions",
        "predictionManifest",
        "metric",
        "passedDeploymentHealth",
        "resultInformationRead",
        "heldOutTargetRowsDecoded",
        "heldOutTargetFieldsDecoded",
    }
)

_EVIDENCE_PREFIXES = (
    "evidenceClaim",
    "health",
    "predictions",
    "predictionManifest",
    "metric",
    "evidenceManifest",
)


def _model_artifacts(
    registry: Generation6Registry, model_id: str
) -> tuple[Path, dict[str, Any]]:
    if model_id == "I0":
        return (
            _canonical_slot(registry, "authorityInitializerModel"),
            registry.document["initializerManifest"],
        )
    manifest = _verify_training_run(registry, model_id)
    return (
        _canonical_slot(registry, f"model:{model_id}"),
        _identity(_canonical_slot(registry, f"trainingManifest:{model_id}")),
    )


def _evidence_spec(
    registry: Generation6Registry, model_id: str
) -> tuple[str, str | None, int | None]:
    if model_id == "I0":
        return "validation-selection", None, None
    candidate, training_purpose, seed, _ = _training_spec(registry, model_id)
    if training_purpose == "primary-training":
        return "validation-selection", candidate, seed
    return "robustness-validation", candidate, seed


def _evidence_predecessor(
    registry: Generation6Registry, model_id: str
) -> dict[str, Any]:
    primary_order = ("I0", *CANDIDATES)
    if model_id in primary_order:
        index = primary_order.index(model_id)
        if index == 0:
            return _identity(
                _canonical_slot(registry, f"trainingManifest:{CANDIDATES[-1]}")
            )
        return _identity(
            _canonical_slot(
                registry, f"evidenceManifest:{primary_order[index - 1]}"
            )
        )
    return _identity(_canonical_slot(registry, f"trainingManifest:{model_id}"))


def _evidence_claim_document(
    registry: Generation6Registry, model_id: str, created: str
) -> dict[str, Any]:
    model, provenance = _model_artifacts(registry, model_id)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": EVIDENCE_CLAIM_KIND,
        "profileId": PROFILE_ID,
        "status": "claimed-before-health-inference-or-validation-target-decode",
        "createdUtc": created,
        "preregistration": _identity(registry.preregistration),
        "predecessor": _evidence_predecessor(registry, model_id),
        "authorityManifest": _identity(
            _canonical_slot(registry, "authorityManifest")
        ),
        "modelId": model_id,
        "model": _identity(model),
        "modelProvenance": provenance,
        "evaluatorExecutable": registry.document["evaluatorExecutable"],
        "evaluatorRunner": registry.document["evaluatorRunner"],
        "evaluatorOptions": registry.document["evaluatorOptions"],
        "runtimeManifest": registry.document["runtimeManifest"],
        "validationCorpus": _identity(
            _canonical_slot(registry, "authorityValidationCorpus")
        ),
        "validationStaticHce": _identity(
            _canonical_slot(registry, "authorityValidationHce")
        ),
        "resultInformationRead": False,
        "heldOutTargetRowsDecodedAtClaim": 0,
        "heldOutTargetFieldsDecodedAtClaim": 0,
    }


def _health_command(registry: Generation6Registry, model: Path) -> list[str]:
    return [
        registry.document["evaluatorExecutable"]["path"],
        "-I",
        "-B",
        registry.document["evaluatorRunner"]["path"],
        EVALUATOR_OPTIONS["healthMode"],
        str(model),
    ]


def _run_health(
    registry: Generation6Registry, model: Path
) -> dict[str, Any]:
    dependencies = (
        Path(registry.document["evaluatorExecutable"]["path"]),
        Path(registry.document["evaluatorRunner"]["path"]),
        Path(registry.document["evaluatorOptions"]["path"]),
        Path(registry.document["runtimeManifest"]["path"]),
        model,
    )
    snapshots = {str(path): _identity(path) for path in dependencies}
    completed = subprocess.run(
        _health_command(registry, model),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
        cwd=registry.namespace,
        env={},
    )
    for path in dependencies:
        if not _type_exact_equal(_identity(path), snapshots[str(path)]):
            raise ValueError("health evaluator dependency changed during execution")
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError(
            "pinned health evaluator failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    result = _strict_json_loads(completed.stdout, location="health evaluator stdout")
    if not isinstance(result, dict):
        raise ValueError("health evaluator did not return one JSON object")
    _exact_keys(result, HEALTH_RUNNER_FIELDS, "health evaluator result")
    model_identity = _identity(model)
    if (
        type(result["modelSha256"]) is not str
        or result["modelSha256"] != model_identity["sha256"]
        or type(result["modelBytes"]) is not int
        or result["modelBytes"] != model_identity["bytes"]
        or any(
            type(result[field]) is not bool
            for field in (
                "finitePredictions",
                "quantizationRoundTripExact",
                "expectedNetworkBytes",
                "runtimeParity",
            )
        )
    ):
        raise ValueError("health evaluator model/type binding changed")
    _exact_int(
        result["maximumAbsResidualCp"],
        "health maximumAbsResidualCp",
        0,
        SCORE_ABS_LIMIT_CP,
    )
    return result


def _health_passed(result: Mapping[str, Any]) -> bool:
    return bool(
        result["finitePredictions"]
        and result["quantizationRoundTripExact"]
        and result["expectedNetworkBytes"]
        and result["runtimeParity"]
        and result["maximumAbsResidualCp"]
        <= HEALTH_THRESHOLDS["maximumAbsResidualCp"]
    )


def _ordered_validation_children(
    roots: Sequence[DecisionRoot],
) -> tuple[DecisionLabel, ...]:
    return tuple(
        label
        for root in sorted(roots, key=lambda item: item.root_id)
        for label in _canonical_siblings(root)
    )


def _network_predictions(
    registry: Generation6Registry,
    model: Path,
    roots: Sequence[DecisionRoot],
) -> dict[str, dict[str, int]]:
    children = _ordered_validation_children(roots)
    contracts = (
        Path(registry.document["evaluatorOptions"]["path"]),
        Path(registry.document["runtimeManifest"]["path"]),
    )
    snapshots = {str(path): _identity(path) for path in contracts}
    replay = _run_exact_evaluator(
        engine=Path(registry.document["evaluatorExecutable"]["path"]),
        runner=Path(registry.document["evaluatorRunner"]["path"]),
        mode=EVALUATOR_OPTIONS["networkMode"],
        model=model,
        ofens=[label.child_ofen for label in children],
    )
    for path in contracts:
        if not _type_exact_equal(_identity(path), snapshots[str(path)]):
            raise ValueError("network evaluation contract changed during replay")
    by_child = {
        label.sibling_id: replay[str(index)]
        for index, label in enumerate(children)
    }
    return {
        root.root_id: {
            label.sibling_id: by_child[label.sibling_id]
            for label in _canonical_siblings(root)
        }
        for root in roots
    }


def _prediction_rows(
    model_id: str,
    roots: Sequence[DecisionRoot],
    predictions: Mapping[str, Mapping[str, int | float]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in sorted(roots, key=lambda item: item.root_id):
        for label in _canonical_siblings(root):
            value = predictions[root.root_id][label.sibling_id]
            score = _exact_int(
                value,
                f"prediction {model_id}/{label.sibling_id}",
                -SCORE_ABS_LIMIT_CP,
                SCORE_ABS_LIMIT_CP,
            )
            rows.append(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "kind": PREDICTION_ROW_KIND,
                    "profileId": PROFILE_ID,
                    "modelId": model_id,
                    "childId": label.sibling_id,
                    "residualCp": score,
                }
            )
    return rows


def _parse_prediction_rows(
    path: Path, *, model_id: str
) -> list[dict[str, Any]]:
    safe, lines = _snapshot_utf8_lines(path, f"{model_id} predictions")
    rows: list[dict[str, Any]] = []
    child_ids: set[str] = set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        value = _strict_json_loads(line, location=f"{safe}:{number}")
        if not isinstance(value, dict):
            raise ValueError("prediction row is not an object")
        _exact_keys(value, PREDICTION_FIELDS, f"{safe}:{number}")
        if (
            type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != SCHEMA_VERSION
            or value["kind"] != PREDICTION_ROW_KIND
            or value["profileId"] != PROFILE_ID
            or value["modelId"] != model_id
            or type(value["childId"]) is not str
            or not value["childId"]
            or value["childId"] in child_ids
        ):
            raise ValueError("prediction row schema/inventory changed")
        _exact_int(
            value["residualCp"],
            f"{safe}:{number} residualCp",
            -SCORE_ABS_LIMIT_CP,
            SCORE_ABS_LIMIT_CP,
        )
        child_ids.add(value["childId"])
        rows.append(value)
    if not rows:
        raise ValueError("prediction transcript is empty")
    return rows


def _canonical_metric_document(
    *,
    registry: Generation6Registry,
    model_id: str,
    model: Path,
    model_provenance: Mapping[str, Any],
    health_path: Path,
    prediction_manifest_path: Path,
    roots: Sequence[DecisionRoot],
    predictions: Mapping[str, Mapping[str, int | float]],
) -> dict[str, Any]:
    purpose, candidate, seed = _evidence_spec(registry, model_id)
    root_ids = [root.root_id for root in roots]
    if (
        any(root.split != "validation" for root in roots)
        or tuple(sorted(root_ids))
        != tuple(sorted(registry.authority.root_ids_by_split["validation"]))
        or set(predictions) != set(root_ids)
    ):
        raise ValueError("canonical metric validation inventory changed")
    cells: dict[str, list[tuple[str, dict[str, float]]]] = defaultdict(list)
    for root in roots:
        cells[f"{root.phase}:{root.root_side}"].append(
            (root.root_id, _root_metric(root, predictions[root.root_id]))
        )
    if set(cells) != set(_CELL_KEYS):
        raise ValueError("canonical validation omits a phase-by-side cell")
    cell_report: dict[str, Any] = {}
    for cell in _CELL_KEYS:
        rows = cells[cell]
        inventory = _cell_inventory([root_id for root_id, _ in rows])
        expected_inventory = registry.authority.binding["phaseSideInventories"][
            "validation"
        ][cell]
        if not _type_exact_equal(inventory, expected_inventory):
            raise ValueError(f"canonical metric cell {cell} inventory changed")
        cell_report[cell] = {
            "roots": len(rows),
            "rootIdsSha256": inventory["sha256"],
            **{
                metric: float(np.mean([value[metric] for _, value in rows]))
                for metric in _METRIC_NAMES
            },
        }
    macro = {
        metric: float(
            np.mean([cell_report[cell][metric] for cell in _CELL_KEYS])
        )
        for metric in _METRIC_NAMES
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": METRIC_KIND,
        "profileId": PROFILE_ID,
        "modelId": model_id,
        "resultInformationRead": False,
        "aggregation": METRIC_AGGREGATION,
        "decisionRoots": len(roots),
        "bindings": {
            "authoritySha256": registry.authority.binding_sha256,
            "authorityManifest": _identity(
                _canonical_slot(registry, "authorityManifest")
            ),
            "validationCorpus": _identity(
                _canonical_slot(registry, "authorityValidationCorpus")
            ),
            "validationStaticHce": _identity(
                _canonical_slot(registry, "authorityValidationHce")
            ),
            "model": _identity(model),
            "modelProvenance": dict(model_provenance),
            "healthEvidence": _identity(health_path),
            "predictionManifest": _identity(prediction_manifest_path),
            "accessPurpose": purpose,
            "candidateId": candidate,
            "trainingSeed": seed,
        },
        "cells": cell_report,
        "macro": macro,
    }


def _validate_canonical_metric_shape(
    value: Mapping[str, Any], *, registry: Generation6Registry, model_id: str
) -> None:
    _exact_keys(value, METRIC_FIELDS, f"{model_id} canonical metric")
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != SCHEMA_VERSION
        or value["kind"] != METRIC_KIND
        or value["profileId"] != PROFILE_ID
        or value["modelId"] != model_id
        or type(value["resultInformationRead"]) is not bool
        or value["resultInformationRead"] is not False
        or value["aggregation"] != METRIC_AGGREGATION
        or type(value["bindings"]) is not dict
    ):
        raise ValueError(f"{model_id} canonical metric header changed")
    _exact_keys(
        value["bindings"],
        CANONICAL_METRIC_BINDING_FIELDS,
        f"{model_id} canonical metric bindings",
    )
    roots = _exact_int(
        value["decisionRoots"], f"{model_id} decisionRoots", 1, 10_000_000
    )
    if roots != registry.authority.binding["rootInventories"]["validation"]["roots"]:
        raise ValueError(f"{model_id} metric root count changed")
    if not isinstance(value["cells"], dict) or set(value["cells"]) != set(_CELL_KEYS):
        raise ValueError(f"{model_id} metric cells changed")
    if not isinstance(value["macro"], dict) or set(value["macro"]) != set(_METRIC_NAMES):
        raise ValueError(f"{model_id} metric macro changed")
    for cell in _CELL_KEYS:
        row = value["cells"][cell]
        if not isinstance(row, dict):
            raise ValueError(f"{model_id}/{cell} metric is not an object")
        _exact_keys(row, CELL_METRIC_FIELDS, f"{model_id}/{cell}")
        for metric in _METRIC_NAMES:
            _finite_number(row[metric], f"{model_id}/{cell}/{metric}")
    for metric in _METRIC_NAMES:
        reported = _finite_number(value["macro"][metric], f"{model_id}/macro/{metric}")
        recomputed = float(
            np.mean([value["cells"][cell][metric] for cell in _CELL_KEYS])
        )
        if not math.isclose(reported, recomputed, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(f"{model_id} canonical macro changed")


def _evidence_bundle_files(model_ids: Sequence[str]) -> set[str]:
    files = _completed_primary_files(len(CANDIDATES))
    for model_id in model_ids:
        for prefix in _EVIDENCE_PREFIXES:
            files.add(_relative_slot(f"{prefix}:{model_id}"))
    return files


def _expected_health_document(
    *,
    registry: Generation6Registry,
    model_id: str,
    claim_path: Path,
    model: Path,
    provenance: Mapping[str, Any],
    result: Mapping[str, Any],
    created: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HEALTH_EVIDENCE_KIND,
        "profileId": PROFILE_ID,
        "status": "replayed-exact-deployment-health",
        "createdUtc": created,
        "claim": _identity(claim_path),
        "modelId": model_id,
        "model": _identity(model),
        "modelProvenance": dict(provenance),
        "evaluatorExecutable": registry.document["evaluatorExecutable"],
        "evaluatorRunner": registry.document["evaluatorRunner"],
        "evaluatorOptions": registry.document["evaluatorOptions"],
        "runtimeManifest": registry.document["runtimeManifest"],
        "thresholds": dict(HEALTH_THRESHOLDS),
        "runnerResult": dict(result),
        "passed": _health_passed(result),
        "resultInformationRead": False,
    }


def _expected_prediction_manifest(
    *,
    registry: Generation6Registry,
    model_id: str,
    claim_path: Path,
    model: Path,
    provenance: Mapping[str, Any],
    roots: Sequence[DecisionRoot],
    predictions_path: Path,
    created: str,
) -> dict[str, Any]:
    children = _ordered_validation_children(roots)
    order_records = [
        {
            "rootId": label.root_id,
            "childId": label.sibling_id,
            "childOfen": label.child_ofen,
        }
        for label in children
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PREDICTION_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "fresh-pinned-network-inference",
        "createdUtc": created,
        "claim": _identity(claim_path),
        "modelId": model_id,
        "model": _identity(model),
        "modelProvenance": dict(provenance),
        "evaluatorExecutable": registry.document["evaluatorExecutable"],
        "evaluatorRunner": registry.document["evaluatorRunner"],
        "evaluatorOptions": registry.document["evaluatorOptions"],
        "runtimeManifest": registry.document["runtimeManifest"],
        "validationCorpus": _identity(
            _canonical_slot(registry, "authorityValidationCorpus")
        ),
        "validationStaticHce": _identity(
            _canonical_slot(registry, "authorityValidationHce")
        ),
        "orderedOfenInventory": {
            "children": len(order_records),
            "sha256": _sha256_bytes(_canonical_json(order_records)),
        },
        "predictionRows": len(order_records),
        "predictions": _identity(predictions_path),
        "resultInformationRead": False,
        "heldOutTargetRowsDecoded": 0,
        "heldOutTargetFieldsDecoded": 0,
    }


def _expected_evidence_manifest(
    *,
    registry: Generation6Registry,
    model_id: str,
    claim_path: Path,
    model: Path,
    provenance: Mapping[str, Any],
    health_path: Path,
    predictions_path: Path,
    prediction_manifest_path: Path,
    metric_path: Path,
    created: str,
) -> dict[str, Any]:
    health = _load_json(health_path, f"{model_id} health evidence")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": EVIDENCE_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "committed-replayable-validation-evidence",
        "createdUtc": created,
        "preregistration": _identity(registry.preregistration),
        "predecessor": _evidence_predecessor(registry, model_id),
        "claim": _identity(claim_path),
        "authorityManifest": _identity(
            _canonical_slot(registry, "authorityManifest")
        ),
        "modelId": model_id,
        "model": _identity(model),
        "modelProvenance": dict(provenance),
        "healthEvidence": _identity(health_path),
        "predictions": _identity(predictions_path),
        "predictionManifest": _identity(prediction_manifest_path),
        "metric": _identity(metric_path),
        "passedDeploymentHealth": health["passed"],
        "resultInformationRead": False,
        "heldOutTargetRowsDecoded": 0,
        "heldOutTargetFieldsDecoded": 0,
    }


def _evaluate_model(registry: Generation6Registry, model_id: str) -> dict[str, Any]:
    model, provenance = _model_artifacts(registry, model_id)
    predecessor = _evidence_predecessor(registry, model_id)
    predecessor_document = _load_json(
        Path(predecessor["path"]), f"{model_id} evidence predecessor"
    )
    claim_created = _utc_after(predecessor_document["createdUtc"])
    claim_path = _ensure_slot_parent(registry, f"evidenceClaim:{model_id}")
    _exclusive_json(
        claim_path, _evidence_claim_document(registry, model_id, claim_created)
    )
    health_result = _run_health(registry, model)
    health_created = _utc_after(claim_created)
    health_path = _canonical_slot(registry, f"health:{model_id}")
    _exclusive_json(
        health_path,
        _expected_health_document(
            registry=registry,
            model_id=model_id,
            claim_path=claim_path,
            model=model,
            provenance=provenance,
            result=health_result,
            created=health_created,
        ),
    )
    validation_inputs = (
        _canonical_slot(registry, "authorityValidationCorpus"),
        _canonical_slot(registry, "authorityValidationHce"),
        Path(registry.document["evaluatorOptions"]["path"]),
        Path(registry.document["runtimeManifest"]["path"]),
    )
    before_inputs = {str(path): _identity(path) for path in validation_inputs}
    roots = _load_canonical_validation_roots(registry)
    predictions = _network_predictions(registry, model, roots)
    for path in validation_inputs:
        if not _type_exact_equal(_identity(path), before_inputs[str(path)]):
            raise ValueError("validation input changed during fresh inference")
    rows = _prediction_rows(model_id, roots, predictions)
    predictions_path = _canonical_slot(registry, f"predictions:{model_id}")
    _exclusive_bytes(
        predictions_path, b"".join(_canonical_json(row) for row in rows)
    )
    prediction_manifest_created = _utc_after(health_created)
    prediction_manifest_path = _canonical_slot(
        registry, f"predictionManifest:{model_id}"
    )
    _exclusive_json(
        prediction_manifest_path,
        _expected_prediction_manifest(
            registry=registry,
            model_id=model_id,
            claim_path=claim_path,
            model=model,
            provenance=provenance,
            roots=roots,
            predictions_path=predictions_path,
            created=prediction_manifest_created,
        ),
    )
    metric_path = _canonical_slot(registry, f"metric:{model_id}")
    metric = _canonical_metric_document(
        registry=registry,
        model_id=model_id,
        model=model,
        model_provenance=provenance,
        health_path=health_path,
        prediction_manifest_path=prediction_manifest_path,
        roots=roots,
        predictions=predictions,
    )
    _exclusive_json(metric_path, metric)
    evidence_created = _utc_after(prediction_manifest_created)
    evidence_path = _canonical_slot(registry, f"evidenceManifest:{model_id}")
    identity = _exclusive_json(
        evidence_path,
        _expected_evidence_manifest(
            registry=registry,
            model_id=model_id,
            claim_path=claim_path,
            model=model,
            provenance=provenance,
            health_path=health_path,
            predictions_path=predictions_path,
            prediction_manifest_path=prediction_manifest_path,
            metric_path=metric_path,
            created=evidence_created,
        ),
    )
    return identity


def _verify_evidence(
    registry: Generation6Registry, model_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    model, provenance = _model_artifacts(registry, model_id)
    claim_path = _canonical_slot(registry, f"evidenceClaim:{model_id}")
    claim = _load_json(claim_path, f"{model_id} evidence claim")
    _exact_keys(claim, EVIDENCE_CLAIM_FIELDS, f"{model_id} evidence claim")
    if not _type_exact_equal(
        claim, _evidence_claim_document(registry, model_id, claim.get("createdUtc", ""))
    ):
        raise ValueError(f"{model_id} evidence claim changed")
    predecessor_document = _load_json(
        Path(claim["predecessor"]["path"]), f"{model_id} evidence predecessor"
    )
    if _parse_timestamp(claim["createdUtc"], f"{model_id} claim createdUtc") <= _parse_timestamp(
        predecessor_document["createdUtc"], f"{model_id} predecessor createdUtc"
    ):
        raise ValueError(f"{model_id} evidence claim chronology changed")

    health_path = _canonical_slot(registry, f"health:{model_id}")
    health = _load_json(health_path, f"{model_id} health")
    _exact_keys(health, HEALTH_EVIDENCE_FIELDS, f"{model_id} health")
    replay_health = _run_health(registry, model)
    expected_health = _expected_health_document(
        registry=registry,
        model_id=model_id,
        claim_path=claim_path,
        model=model,
        provenance=provenance,
        result=replay_health,
        created=health.get("createdUtc", ""),
    )
    if not _type_exact_equal(health, expected_health):
        raise ValueError(f"{model_id} health differs from fresh replay")
    if _parse_timestamp(health["createdUtc"], f"{model_id} health createdUtc") <= _parse_timestamp(
        claim["createdUtc"], f"{model_id} claim createdUtc"
    ):
        raise ValueError(f"{model_id} health chronology changed")

    roots = _load_canonical_validation_roots(registry)
    replay_predictions = _network_predictions(registry, model, roots)
    replay_rows = _prediction_rows(model_id, roots, replay_predictions)
    predictions_path = _canonical_slot(registry, f"predictions:{model_id}")
    stored_rows = _parse_prediction_rows(predictions_path, model_id=model_id)
    if not _type_exact_equal(stored_rows, replay_rows):
        raise ValueError(f"{model_id} predictions differ from fresh replay")
    prediction_manifest_path = _canonical_slot(
        registry, f"predictionManifest:{model_id}"
    )
    prediction_manifest = _load_json(
        prediction_manifest_path, f"{model_id} prediction manifest"
    )
    _exact_keys(
        prediction_manifest,
        PREDICTION_MANIFEST_FIELDS,
        f"{model_id} prediction manifest",
    )
    expected_prediction_manifest = _expected_prediction_manifest(
        registry=registry,
        model_id=model_id,
        claim_path=claim_path,
        model=model,
        provenance=provenance,
        roots=roots,
        predictions_path=predictions_path,
        created=prediction_manifest.get("createdUtc", ""),
    )
    if not _type_exact_equal(prediction_manifest, expected_prediction_manifest):
        raise ValueError(f"{model_id} prediction manifest changed")
    if _parse_timestamp(
        prediction_manifest["createdUtc"], f"{model_id} prediction createdUtc"
    ) <= _parse_timestamp(health["createdUtc"], f"{model_id} health createdUtc"):
        raise ValueError(f"{model_id} prediction chronology changed")

    metric_path = _canonical_slot(registry, f"metric:{model_id}")
    metric = _load_json(metric_path, f"{model_id} metric")
    _validate_canonical_metric_shape(metric, registry=registry, model_id=model_id)
    expected_metric = _canonical_metric_document(
        registry=registry,
        model_id=model_id,
        model=model,
        model_provenance=provenance,
        health_path=health_path,
        prediction_manifest_path=prediction_manifest_path,
        roots=roots,
        predictions=replay_predictions,
    )
    if not _type_exact_equal(metric, expected_metric):
        raise ValueError(f"{model_id} metric differs from fresh replay")

    evidence_path = _canonical_slot(registry, f"evidenceManifest:{model_id}")
    evidence = _load_json(evidence_path, f"{model_id} evidence manifest")
    _exact_keys(evidence, EVIDENCE_MANIFEST_FIELDS, f"{model_id} evidence manifest")
    expected_evidence = _expected_evidence_manifest(
        registry=registry,
        model_id=model_id,
        claim_path=claim_path,
        model=model,
        provenance=provenance,
        health_path=health_path,
        predictions_path=predictions_path,
        prediction_manifest_path=prediction_manifest_path,
        metric_path=metric_path,
        created=evidence.get("createdUtc", ""),
    )
    if not _type_exact_equal(evidence, expected_evidence):
        raise ValueError(f"{model_id} evidence manifest changed")
    if _parse_timestamp(evidence["createdUtc"], f"{model_id} evidence createdUtc") <= _parse_timestamp(
        prediction_manifest["createdUtc"], f"{model_id} prediction createdUtc"
    ):
        raise ValueError(f"{model_id} evidence chronology changed")
    return evidence, metric


def evaluate_validation(model_id: str) -> dict[str, Any]:
    """Create replayable validation evidence for the next fixed model."""

    registry = verify_canonical_namespace()
    _verify_materialized_authority(registry)
    for candidate in CANDIDATES:
        _verify_training_run(registry, candidate)
    order = ("I0", *CANDIDATES)
    if model_id not in order:
        raise ValueError("validation model must be I0/G6A/G6B/G6C")
    index = order.index(model_id)
    for previous in order[:index]:
        _verify_evidence(registry, previous)
    _audit_namespace(
        registry, exact_files=_evidence_bundle_files(order[:index])
    )
    identity = _evaluate_model(registry, model_id)
    _verify_evidence(registry, model_id)
    _audit_namespace(
        registry, exact_files=_evidence_bundle_files(order[: index + 1])
    )
    return identity


CANONICAL_SELECTION_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "predecessor",
        "authorityManifest",
        "initializerEvidence",
        "candidateEvidence",
        "metrics",
        "healthEvidence",
        "trainingManifests",
        "fixedNonRegressionGates",
        "lexicographicOrder",
        "candidateTiePriority",
        "decision",
        "selectedCandidateId",
        "selectedModel",
        "selectedTrainingManifest",
        "resultInformationRead",
        "heldOutTargetRowsDecodedAtSelection",
        "heldOutTargetFieldsDecodedAtSelection",
    }
)


def _canonical_selection_decision(
    initializer: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
    health: Mapping[str, bool],
) -> dict[str, Any]:
    if set(candidates) != set(CANDIDATES) or set(health) != set(CANDIDATES):
        raise ValueError("canonical selection inventory changed")
    evaluated: dict[str, Any] = {}
    eligible: list[str] = []
    for candidate in CANDIDATES:
        gates = _non_regression_gate_report(
            candidates[candidate], initializer, health[candidate]
        )
        key = [
            -float(candidates[candidate]["macro"]["topSetAccuracy"]),
            float(candidates[candidate]["macro"]["meanChosenMoveRegretCp"]),
            float(candidates[candidate]["macro"]["listwiseCrossEntropy"]),
            float(candidates[candidate]["macro"]["pointwiseHuber"]),
            TIE_PRIORITY.index(candidate),
        ]
        passed = all(gates.values())
        evaluated[candidate] = {
            "eligible": passed,
            "gates": gates,
            "lexicographicKey": key,
        }
        if passed:
            eligible.append(candidate)
    selected = min(
        eligible,
        key=lambda candidate: tuple(evaluated[candidate]["lexicographicKey"]),
        default=None,
    )
    return {"candidates": evaluated, "selectedCandidateId": selected}


def _canonical_selection_document(
    registry: Generation6Registry, created: str
) -> dict[str, Any]:
    initializer_evidence, initializer_metric = _verify_evidence(registry, "I0")
    if initializer_evidence["passedDeploymentHealth"] is not True:
        raise ValueError("initializer failed replayed deployment health")
    evidence: dict[str, dict[str, Any]] = {}
    metrics: dict[str, dict[str, Any]] = {}
    health: dict[str, bool] = {}
    for candidate in CANDIDATES:
        evidence[candidate], metrics[candidate] = _verify_evidence(
            registry, candidate
        )
        health[candidate] = evidence[candidate]["passedDeploymentHealth"]
    decision = _canonical_selection_decision(initializer_metric, metrics, health)
    selected = decision["selectedCandidateId"]
    if selected is None:
        raise ValueError("no primary candidate passed frozen validation gates")
    model, _ = _model_artifacts(registry, selected)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": VALIDATION_SELECTION_KIND,
        "profileId": PROFILE_ID,
        "status": "selected-from-fresh-canonical-replay",
        "createdUtc": created,
        "preregistration": _identity(registry.preregistration),
        "predecessor": _identity(
            _canonical_slot(registry, f"evidenceManifest:{CANDIDATES[-1]}")
        ),
        "authorityManifest": _identity(
            _canonical_slot(registry, "authorityManifest")
        ),
        "initializerEvidence": _identity(
            _canonical_slot(registry, "evidenceManifest:I0")
        ),
        "candidateEvidence": {
            candidate: _identity(
                _canonical_slot(registry, f"evidenceManifest:{candidate}")
            )
            for candidate in CANDIDATES
        },
        "metrics": {
            "I0": _identity(_canonical_slot(registry, "metric:I0")),
            **{
                candidate: _identity(
                    _canonical_slot(registry, f"metric:{candidate}")
                )
                for candidate in CANDIDATES
            },
        },
        "healthEvidence": {
            "I0": _identity(_canonical_slot(registry, "health:I0")),
            **{
                candidate: _identity(
                    _canonical_slot(registry, f"health:{candidate}")
                )
                for candidate in CANDIDATES
            },
        },
        "trainingManifests": {
            candidate: _identity(
                _canonical_slot(registry, f"trainingManifest:{candidate}")
            )
            for candidate in CANDIDATES
        },
        "fixedNonRegressionGates": dict(NON_REGRESSION_GATES),
        "lexicographicOrder": list(VALIDATION_LEXICOGRAPHIC_ORDER),
        "candidateTiePriority": list(TIE_PRIORITY),
        "decision": decision,
        "selectedCandidateId": selected,
        "selectedModel": _identity(model),
        "selectedTrainingManifest": _identity(
            _canonical_slot(registry, f"trainingManifest:{selected}")
        ),
        "resultInformationRead": False,
        "heldOutTargetRowsDecodedAtSelection": 0,
        "heldOutTargetFieldsDecodedAtSelection": 0,
    }


def _verify_primary_selection(registry: Generation6Registry) -> dict[str, Any]:
    path = _canonical_slot(registry, "validationSelection")
    document = _load_json(path, "canonical primary selection")
    _exact_keys(document, CANONICAL_SELECTION_FIELDS, "canonical primary selection")
    expected = _canonical_selection_document(registry, document.get("createdUtc", ""))
    if not _type_exact_equal(document, expected):
        raise ValueError("canonical primary selection differs from fresh replay")
    predecessor = _load_json(
        Path(document["predecessor"]["path"]), "selection predecessor"
    )
    if _parse_timestamp(document["createdUtc"], "selection createdUtc") <= _parse_timestamp(
        predecessor["createdUtc"], "selection predecessor createdUtc"
    ):
        raise ValueError("canonical primary selection chronology changed")
    return document


def select_primary() -> dict[str, Any]:
    """Select one primary model from internally replayed canonical evidence."""

    registry = verify_canonical_namespace()
    order = ("I0", *CANDIDATES)
    expected_before = _evidence_bundle_files(order)
    _audit_namespace(registry, exact_files=expected_before)
    for model_id in order:
        _verify_evidence(registry, model_id)
    predecessor = _load_json(
        _canonical_slot(registry, f"evidenceManifest:{CANDIDATES[-1]}"),
        "selection predecessor",
    )
    created = _utc_after(predecessor["createdUtc"])
    document = _canonical_selection_document(registry, created)
    path = _ensure_slot_parent(registry, "validationSelection")
    identity = _exclusive_json(path, document)
    _verify_primary_selection(registry)
    _audit_namespace(
        registry,
        exact_files=expected_before | {_relative_slot("validationSelection")},
    )
    return identity


def _post_selection_files(registry: Generation6Registry) -> set[str]:
    del registry
    return _evidence_bundle_files(("I0", *CANDIDATES)) | {
        _relative_slot("validationSelection")
    }


def train_robustness_selected() -> dict[str, Any]:
    """Run the selected recipe once more from I0 with its second frozen seed."""

    registry = verify_canonical_namespace()
    selection = _verify_primary_selection(registry)
    expected_before = _post_selection_files(registry)
    _audit_namespace(registry, exact_files=expected_before)
    model_id = f"{selection['selectedCandidateId']}-robustness"
    identity = _execute_training(registry, model_id)
    expected_after = expected_before | {
        _relative_slot(f"{prefix}:{model_id}")
        for prefix in (
            "trainingClaim",
            "model",
            "trainingHistory",
            "trainingBatchOrder",
            "trainingManifest",
        )
    }
    _audit_namespace(registry, exact_files=expected_after)
    return identity


def _post_robust_training_files(
    registry: Generation6Registry, model_id: str
) -> set[str]:
    return _post_selection_files(registry) | {
        _relative_slot(f"{prefix}:{model_id}")
        for prefix in (
            "trainingClaim",
            "model",
            "trainingHistory",
            "trainingBatchOrder",
            "trainingManifest",
        )
    }


def evaluate_robustness_selected() -> dict[str, Any]:
    registry = verify_canonical_namespace()
    selection = _verify_primary_selection(registry)
    model_id = f"{selection['selectedCandidateId']}-robustness"
    _verify_training_run(registry, model_id)
    expected_before = _post_robust_training_files(registry, model_id)
    _audit_namespace(registry, exact_files=expected_before)
    identity = _evaluate_model(registry, model_id)
    _verify_evidence(registry, model_id)
    expected_after = expected_before | {
        _relative_slot(f"{prefix}:{model_id}") for prefix in _EVIDENCE_PREFIXES
    }
    _audit_namespace(registry, exact_files=expected_after)
    return identity


CANONICAL_ROBUSTNESS_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "predecessor",
        "authorityManifest",
        "validationSelection",
        "selectedCandidateId",
        "primaryModel",
        "primaryTrainingManifest",
        "primaryEvidence",
        "robustnessModel",
        "robustnessTrainingManifest",
        "robustnessEvidence",
        "primaryMetric",
        "robustnessMetric",
        "primaryTrainingSeed",
        "robustnessTrainingSeed",
        "validationNonRegressionGates",
        "passedDeploymentHealth",
        "resultInformationRead",
        "heldOutTargetRowsDecodedAtSeal",
        "heldOutTargetFieldsDecodedAtSeal",
    }
)


def _canonical_robustness_document(
    registry: Generation6Registry, created: str
) -> dict[str, Any]:
    selection = _verify_primary_selection(registry)
    candidate = selection["selectedCandidateId"]
    robust_id = f"{candidate}-robustness"
    primary_evidence, primary_metric = _verify_evidence(registry, candidate)
    robust_evidence, robust_metric = _verify_evidence(registry, robust_id)
    gates = _non_regression_gate_report(
        robust_metric,
        primary_metric,
        robust_evidence["passedDeploymentHealth"],
    )
    if not all(gates.values()):
        raise ValueError("robustness replicate regressed against selected primary")
    primary_model, _ = _model_artifacts(registry, candidate)
    robust_model, _ = _model_artifacts(registry, robust_id)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ROBUSTNESS_SEAL_KIND,
        "profileId": PROFILE_ID,
        "status": "passed-replayed-second-seed-confirmation",
        "createdUtc": created,
        "preregistration": _identity(registry.preregistration),
        "predecessor": _identity(
            _canonical_slot(registry, f"evidenceManifest:{robust_id}")
        ),
        "authorityManifest": _identity(
            _canonical_slot(registry, "authorityManifest")
        ),
        "validationSelection": _identity(
            _canonical_slot(registry, "validationSelection")
        ),
        "selectedCandidateId": candidate,
        "primaryModel": _identity(primary_model),
        "primaryTrainingManifest": _identity(
            _canonical_slot(registry, f"trainingManifest:{candidate}")
        ),
        "primaryEvidence": _identity(
            _canonical_slot(registry, f"evidenceManifest:{candidate}")
        ),
        "robustnessModel": _identity(robust_model),
        "robustnessTrainingManifest": _identity(
            _canonical_slot(registry, f"trainingManifest:{robust_id}")
        ),
        "robustnessEvidence": _identity(
            _canonical_slot(registry, f"evidenceManifest:{robust_id}")
        ),
        "primaryMetric": _identity(
            _canonical_slot(registry, f"metric:{candidate}")
        ),
        "robustnessMetric": _identity(
            _canonical_slot(registry, f"metric:{robust_id}")
        ),
        "primaryTrainingSeed": registry.document["primaryTrainingSeeds"][candidate],
        "robustnessTrainingSeed": registry.document["robustnessTrainingSeeds"][candidate],
        "validationNonRegressionGates": gates,
        "passedDeploymentHealth": True,
        "resultInformationRead": False,
        "heldOutTargetRowsDecodedAtSeal": 0,
        "heldOutTargetFieldsDecodedAtSeal": 0,
    }


def _verify_canonical_robustness(registry: Generation6Registry) -> dict[str, Any]:
    path = _canonical_slot(registry, "robustnessSeal")
    document = _load_json(path, "canonical robustness seal")
    _exact_keys(document, CANONICAL_ROBUSTNESS_FIELDS, "canonical robustness seal")
    expected = _canonical_robustness_document(registry, document.get("createdUtc", ""))
    if not _type_exact_equal(document, expected):
        raise ValueError("canonical robustness seal differs from replay")
    predecessor = _load_json(
        Path(document["predecessor"]["path"]), "robustness predecessor"
    )
    if _parse_timestamp(document["createdUtc"], "robustness createdUtc") <= _parse_timestamp(
        predecessor["createdUtc"], "robustness predecessor createdUtc"
    ):
        raise ValueError("canonical robustness chronology changed")
    return document


def seal_robustness() -> dict[str, Any]:
    registry = verify_canonical_namespace()
    selection = _verify_primary_selection(registry)
    robust_id = f"{selection['selectedCandidateId']}-robustness"
    expected_before = _post_robust_training_files(registry, robust_id) | {
        _relative_slot(f"{prefix}:{robust_id}") for prefix in _EVIDENCE_PREFIXES
    }
    _audit_namespace(registry, exact_files=expected_before)
    robust_evidence = _load_json(
        _canonical_slot(registry, f"evidenceManifest:{robust_id}"),
        "robustness evidence predecessor",
    )
    created = _utc_after(robust_evidence["createdUtc"])
    document = _canonical_robustness_document(registry, created)
    path = _ensure_slot_parent(registry, "robustnessSeal")
    identity = _exclusive_json(path, document)
    _verify_canonical_robustness(registry)
    _audit_namespace(
        registry,
        exact_files=expected_before | {_relative_slot("robustnessSeal")},
    )
    return identity


CANONICAL_HELDOUT_ACCESS_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "predecessor",
        "authorityManifest",
        "validationSelection",
        "robustnessSeal",
        "selectedCandidateId",
        "initializerModel",
        "initializerEvidence",
        "selectedPrimaryModel",
        "selectedPrimaryTrainingManifest",
        "selectedPrimaryEvidence",
        "heldOutRootInventory",
        "declaration",
        "resultInformationRead",
        "heldOutTargetRowsDecodedAtArm",
        "heldOutTargetFieldsDecodedAtArm",
    }
)
HELDOUT_CLAIM_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "heldoutAccess",
        "validationSelection",
        "robustnessSeal",
        "selectedCandidateId",
        "initializerModel",
        "selectedPrimaryModel",
        "evaluatorExecutable",
        "evaluatorRunner",
        "evaluatorOptions",
        "runtimeManifest",
        "heldOutRootInventory",
        "resultInformationRead",
        "heldOutTargetRowsDecodedAtClaim",
        "heldOutTargetFieldsDecodedAtClaim",
    }
)
HELDOUT_AGGREGATE_CELL_FIELDS = frozenset({"roots", *_METRIC_NAMES})
HELDOUT_AGGREGATE_MODEL_FIELDS = frozenset(
    {"decisionRoots", "cells", "macro"}
)
HELDOUT_REPORT_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "claim",
        "heldoutAccess",
        "validationSelection",
        "robustnessSeal",
        "selectedCandidateId",
        "models",
        "evaluatorExecutable",
        "evaluatorRunner",
        "evaluatorOptions",
        "runtimeManifest",
        "aggregation",
        "modelMetrics",
        "resultInformationRead",
        "decodedHeldOutTargetRows",
        "rawRootsEmitted",
        "rawChildrenEmitted",
        "rawOfensEmitted",
        "predictionsEmitted",
        "perRootMetricsEmitted",
    }
)
HELDOUT_CLOSURE_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "claim",
        "heldoutAccess",
        "report",
        "selectedCandidateId",
        "heldOutRootInventory",
        "spentForFutureGenerations",
        "retryPermitted",
        "resultInformationRead",
    }
)


def _robust_stage_files(
    registry: Generation6Registry, *, include_seal: bool
) -> set[str]:
    selection = _verify_primary_selection(registry)
    robust_id = f"{selection['selectedCandidateId']}-robustness"
    files = _post_robust_training_files(registry, robust_id) | {
        _relative_slot(f"{prefix}:{robust_id}") for prefix in _EVIDENCE_PREFIXES
    }
    if include_seal:
        files.add(_relative_slot("robustnessSeal"))
    return files


def _heldout_access_document(
    registry: Generation6Registry, created: str
) -> dict[str, Any]:
    selection = _verify_primary_selection(registry)
    robustness = _verify_canonical_robustness(registry)
    candidate = selection["selectedCandidateId"]
    if robustness["selectedCandidateId"] != candidate:
        raise ValueError("selection/robustness candidate mismatch")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HELDOUT_SEAL_KIND,
        "profileId": PROFILE_ID,
        "status": "armed-before-first-heldout-target-decode",
        "createdUtc": created,
        "preregistration": _identity(registry.preregistration),
        "predecessor": _identity(_canonical_slot(registry, "robustnessSeal")),
        "authorityManifest": _identity(
            _canonical_slot(registry, "authorityManifest")
        ),
        "validationSelection": _identity(
            _canonical_slot(registry, "validationSelection")
        ),
        "robustnessSeal": _identity(
            _canonical_slot(registry, "robustnessSeal")
        ),
        "selectedCandidateId": candidate,
        "initializerModel": _identity(
            _canonical_slot(registry, "authorityInitializerModel")
        ),
        "initializerEvidence": _identity(
            _canonical_slot(registry, "evidenceManifest:I0")
        ),
        "selectedPrimaryModel": selection["selectedModel"],
        "selectedPrimaryTrainingManifest": selection["selectedTrainingManifest"],
        "selectedPrimaryEvidence": _identity(
            _canonical_slot(registry, f"evidenceManifest:{candidate}")
        ),
        "heldOutRootInventory": registry.authority.binding["rootInventories"][
            "heldOut"
        ],
        "declaration": (
            "one-shot aggregate-only evaluation of frozen I0 and selected primary"
        ),
        "resultInformationRead": False,
        "heldOutTargetRowsDecodedAtArm": 0,
        "heldOutTargetFieldsDecodedAtArm": 0,
    }


def _verify_canonical_heldout_access(
    registry: Generation6Registry,
) -> dict[str, Any]:
    path = _canonical_slot(registry, "heldoutAccess")
    document = _load_json(path, "canonical held-out access")
    _exact_keys(
        document, CANONICAL_HELDOUT_ACCESS_FIELDS, "canonical held-out access"
    )
    expected = _heldout_access_document(registry, document.get("createdUtc", ""))
    if not _type_exact_equal(document, expected):
        raise ValueError("canonical held-out access changed")
    predecessor = _load_json(
        Path(document["predecessor"]["path"]), "held-out access predecessor"
    )
    if _parse_timestamp(document["createdUtc"], "held-out arm createdUtc") <= _parse_timestamp(
        predecessor["createdUtc"], "held-out predecessor createdUtc"
    ):
        raise ValueError("held-out arm chronology changed")
    return document


def arm_heldout() -> dict[str, Any]:
    registry = verify_canonical_namespace()
    robustness = _verify_canonical_robustness(registry)
    expected_before = _robust_stage_files(registry, include_seal=True)
    _audit_namespace(registry, exact_files=expected_before)
    created = _utc_after(robustness["createdUtc"])
    path = _ensure_slot_parent(registry, "heldoutAccess")
    identity = _exclusive_json(path, _heldout_access_document(registry, created))
    _verify_canonical_heldout_access(registry)
    _audit_namespace(
        registry,
        exact_files=expected_before | {_relative_slot("heldoutAccess")},
    )
    return identity


def _heldout_claim_document(
    registry: Generation6Registry, access: Mapping[str, Any], created: str
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HELDOUT_CLAIM_KIND,
        "profileId": PROFILE_ID,
        "status": "permanently-claimed-before-first-target-decode",
        "createdUtc": created,
        "preregistration": _identity(registry.preregistration),
        "heldoutAccess": _identity(_canonical_slot(registry, "heldoutAccess")),
        "validationSelection": _identity(
            _canonical_slot(registry, "validationSelection")
        ),
        "robustnessSeal": _identity(
            _canonical_slot(registry, "robustnessSeal")
        ),
        "selectedCandidateId": access["selectedCandidateId"],
        "initializerModel": access["initializerModel"],
        "selectedPrimaryModel": access["selectedPrimaryModel"],
        "evaluatorExecutable": registry.document["evaluatorExecutable"],
        "evaluatorRunner": registry.document["evaluatorRunner"],
        "evaluatorOptions": registry.document["evaluatorOptions"],
        "runtimeManifest": registry.document["runtimeManifest"],
        "heldOutRootInventory": access["heldOutRootInventory"],
        "resultInformationRead": False,
        "heldOutTargetRowsDecodedAtClaim": 0,
        "heldOutTargetFieldsDecodedAtClaim": 0,
    }


def _aggregate_only_metrics(
    roots: Sequence[DecisionRoot],
    predictions: Mapping[str, Mapping[str, int | float]],
) -> dict[str, Any]:
    cells: dict[str, list[dict[str, float]]] = defaultdict(list)
    for root in roots:
        cells[f"{root.phase}:{root.root_side}"].append(
            _root_metric(root, predictions[root.root_id])
        )
    if set(cells) != set(_CELL_KEYS):
        raise ValueError("held-out aggregation omits a frozen cell")
    report_cells = {
        cell: {
            "roots": len(cells[cell]),
            **{
                metric: float(
                    np.mean([row[metric] for row in cells[cell]])
                )
                for metric in _METRIC_NAMES
            },
        }
        for cell in _CELL_KEYS
    }
    return {
        "decisionRoots": len(roots),
        "cells": report_cells,
        "macro": {
            metric: float(
                np.mean([report_cells[cell][metric] for cell in _CELL_KEYS])
            )
            for metric in _METRIC_NAMES
        },
    }


def _verify_heldout_aggregate_shape(
    value: Mapping[str, Any], registry: Generation6Registry
) -> None:
    _exact_keys(value, HELDOUT_AGGREGATE_MODEL_FIELDS, "held-out model aggregate")
    if (
        type(value["decisionRoots"]) is not int
        or value["decisionRoots"]
        != registry.authority.binding["rootInventories"]["heldOut"]["roots"]
        or not isinstance(value["cells"], dict)
        or set(value["cells"]) != set(_CELL_KEYS)
        or not isinstance(value["macro"], dict)
        or set(value["macro"]) != set(_METRIC_NAMES)
    ):
        raise ValueError("held-out aggregate inventory changed")
    for cell in _CELL_KEYS:
        row = value["cells"][cell]
        if not isinstance(row, dict):
            raise ValueError("held-out aggregate cell is not an object")
        _exact_keys(row, HELDOUT_AGGREGATE_CELL_FIELDS, f"held-out/{cell}")
        expected_roots = registry.authority.binding["phaseSideInventories"][
            "heldOut"
        ][cell]["roots"]
        if type(row["roots"]) is not int or row["roots"] != expected_roots:
            raise ValueError("held-out aggregate cell count changed")
        for metric in _METRIC_NAMES:
            _finite_number(row[metric], f"held-out/{cell}/{metric}")
    for metric in _METRIC_NAMES:
        reported = _finite_number(value["macro"][metric], f"held-out/macro/{metric}")
        expected = float(
            np.mean([value["cells"][cell][metric] for cell in _CELL_KEYS])
        )
        if not math.isclose(reported, expected, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("held-out macro is inconsistent")


def _verify_heldout_closure(registry: Generation6Registry) -> dict[str, Any]:
    access = _verify_canonical_heldout_access(registry)
    claim_path = _canonical_slot(registry, "heldoutClaim")
    claim = _load_json(claim_path, "held-out claim")
    _exact_keys(claim, HELDOUT_CLAIM_FIELDS, "held-out claim")
    if not _type_exact_equal(
        claim,
        _heldout_claim_document(registry, access, claim.get("createdUtc", "")),
    ):
        raise ValueError("held-out claim changed")
    if _parse_timestamp(claim["createdUtc"], "held-out claim createdUtc") <= _parse_timestamp(
        access["createdUtc"], "held-out access createdUtc"
    ):
        raise ValueError("held-out claim chronology changed")
    report_path = _canonical_slot(registry, "heldoutReport")
    report = _load_json(report_path, "held-out aggregate report")
    _exact_keys(report, HELDOUT_REPORT_FIELDS, "held-out aggregate report")
    candidate = access["selectedCandidateId"]
    initializer = _identity(_canonical_slot(registry, "authorityInitializerModel"))
    selected = _identity(_canonical_slot(registry, f"model:{candidate}"))
    if (
        type(report["schemaVersion"]) is not int
        or report["schemaVersion"] != SCHEMA_VERSION
        or report["kind"] != HELDOUT_REPORT_KIND
        or report["profileId"] != PROFILE_ID
        or report["status"] != "consumed-once-aggregate-only"
        or not _type_exact_equal(report["claim"], _identity(claim_path))
        or not _type_exact_equal(
            report["heldoutAccess"], _identity(_canonical_slot(registry, "heldoutAccess"))
        )
        or not _type_exact_equal(
            report["validationSelection"],
            _identity(_canonical_slot(registry, "validationSelection")),
        )
        or not _type_exact_equal(
            report["robustnessSeal"],
            _identity(_canonical_slot(registry, "robustnessSeal")),
        )
        or report["selectedCandidateId"] != candidate
        or not _type_exact_equal(
            report["models"], {"I0": initializer, candidate: selected}
        )
        or not _type_exact_equal(
            report["evaluatorExecutable"], registry.document["evaluatorExecutable"]
        )
        or not _type_exact_equal(
            report["evaluatorRunner"], registry.document["evaluatorRunner"]
        )
        or not _type_exact_equal(
            report["evaluatorOptions"], registry.document["evaluatorOptions"]
        )
        or not _type_exact_equal(
            report["runtimeManifest"], registry.document["runtimeManifest"]
        )
        or not _type_exact_equal(report["aggregation"], METRIC_AGGREGATION)
        or not isinstance(report["modelMetrics"], dict)
        or set(report["modelMetrics"]) != {"I0", candidate}
        or report["rawRootsEmitted"] is not False
        or report["rawChildrenEmitted"] is not False
        or report["rawOfensEmitted"] is not False
        or report["predictionsEmitted"] is not False
        or report["perRootMetricsEmitted"] is not False
        or report["resultInformationRead"] is not False
        or type(report["decodedHeldOutTargetRows"]) is not int
        or report["decodedHeldOutTargetRows"]
        != registry.authority.binding["rootInventories"]["heldOut"]["children"]
    ):
        raise ValueError("held-out report header/leakage declaration changed")
    if _parse_timestamp(report["createdUtc"], "held-out report createdUtc") <= _parse_timestamp(
        claim["createdUtc"], "held-out claim createdUtc"
    ):
        raise ValueError("held-out report chronology changed")
    for aggregate in report["modelMetrics"].values():
        _verify_heldout_aggregate_shape(aggregate, registry)
    closure_path = _canonical_slot(registry, "heldoutClosure")
    closure = _load_json(closure_path, "held-out closure")
    _exact_keys(closure, HELDOUT_CLOSURE_FIELDS, "held-out closure")
    expected_closure = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HELDOUT_CLOSURE_KIND,
        "profileId": PROFILE_ID,
        "status": "heldout-consumed-and-permanently-closed",
        "createdUtc": closure.get("createdUtc"),
        "preregistration": _identity(registry.preregistration),
        "claim": _identity(claim_path),
        "heldoutAccess": _identity(
            _canonical_slot(registry, "heldoutAccess")
        ),
        "report": _identity(report_path),
        "selectedCandidateId": candidate,
        "heldOutRootInventory": access["heldOutRootInventory"],
        "spentForFutureGenerations": True,
        "retryPermitted": False,
        "resultInformationRead": False,
    }
    if not _type_exact_equal(closure, expected_closure):
        raise ValueError("held-out closure changed")
    if _parse_timestamp(closure["createdUtc"], "held-out closure createdUtc") <= _parse_timestamp(
        report["createdUtc"], "held-out report createdUtc"
    ):
        raise ValueError("held-out closure chronology changed")
    final_files = _robust_stage_files(registry, include_seal=True) | {
        _relative_slot("heldoutAccess"),
        _relative_slot("heldoutClaim"),
        _relative_slot("heldoutReport"),
        _relative_slot("heldoutClosure"),
    }
    _audit_namespace(registry, exact_files=final_files)
    return closure


def consume_heldout_once() -> dict[str, Any]:
    """Consume held-out labels exactly once and return only report identity."""

    registry = verify_canonical_namespace()
    access = _verify_canonical_heldout_access(registry)
    expected_before = _robust_stage_files(registry, include_seal=True) | {
        _relative_slot("heldoutAccess")
    }
    try:
        _audit_namespace(registry, exact_files=expected_before)
    except ValueError as error:
        if _canonical_slot(registry, "heldoutClaim").exists():
            raise HeldoutAccessError(
                "held-out set was already claimed and can never be retried"
            ) from error
        raise
    claim_created = _utc_after(access["createdUtc"])
    claim_path = _canonical_slot(registry, "heldoutClaim")
    _exclusive_json(
        claim_path, _heldout_claim_document(registry, access, claim_created)
    )
    # The claim identity is authenticated immediately before the first target
    # JSON decode.  A crash from here onward permanently spends the set.
    _verify_identity_record(_identity(claim_path), "held-out predecode claim")
    roots = _decode_opaque_split(
        registry.authority.opaque,
        registry.authority.hce_by_child,
        "heldOut",
    )
    if tuple(root.root_id for root in roots) != tuple(
        sorted(registry.authority.root_ids_by_split["heldOut"])
    ):
        raise HeldoutAccessError("held-out root inventory changed at consumption")
    candidate = access["selectedCandidateId"]
    initializer = _canonical_slot(registry, "authorityInitializerModel")
    selected = _canonical_slot(registry, f"model:{candidate}")
    model_predictions = {
        "I0": _network_predictions(registry, initializer, roots),
        candidate: _network_predictions(registry, selected, roots),
    }
    report_created = _utc_after(claim_created)
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HELDOUT_REPORT_KIND,
        "profileId": PROFILE_ID,
        "status": "consumed-once-aggregate-only",
        "createdUtc": report_created,
        "claim": _identity(claim_path),
        "heldoutAccess": _identity(
            _canonical_slot(registry, "heldoutAccess")
        ),
        "validationSelection": _identity(
            _canonical_slot(registry, "validationSelection")
        ),
        "robustnessSeal": _identity(
            _canonical_slot(registry, "robustnessSeal")
        ),
        "selectedCandidateId": candidate,
        "models": {
            "I0": _identity(initializer),
            candidate: _identity(selected),
        },
        "evaluatorExecutable": registry.document["evaluatorExecutable"],
        "evaluatorRunner": registry.document["evaluatorRunner"],
        "evaluatorOptions": registry.document["evaluatorOptions"],
        "runtimeManifest": registry.document["runtimeManifest"],
        "aggregation": METRIC_AGGREGATION,
        "modelMetrics": {
            model_id: _aggregate_only_metrics(roots, predictions)
            for model_id, predictions in model_predictions.items()
        },
        "resultInformationRead": False,
        "decodedHeldOutTargetRows": len(roots) * 4,
        "rawRootsEmitted": False,
        "rawChildrenEmitted": False,
        "rawOfensEmitted": False,
        "predictionsEmitted": False,
        "perRootMetricsEmitted": False,
    }
    report_path = _canonical_slot(registry, "heldoutReport")
    report_identity = _exclusive_json(report_path, report)
    closure_created = _utc_after(report_created)
    closure = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HELDOUT_CLOSURE_KIND,
        "profileId": PROFILE_ID,
        "status": "heldout-consumed-and-permanently-closed",
        "createdUtc": closure_created,
        "preregistration": _identity(registry.preregistration),
        "claim": _identity(claim_path),
        "heldoutAccess": _identity(
            _canonical_slot(registry, "heldoutAccess")
        ),
        "report": report_identity,
        "selectedCandidateId": candidate,
        "heldOutRootInventory": access["heldOutRootInventory"],
        "spentForFutureGenerations": True,
        "retryPermitted": False,
        "resultInformationRead": False,
    }
    _exclusive_json(_canonical_slot(registry, "heldoutClosure"), closure)
    _verify_heldout_closure(registry)
    final_files = expected_before | {
        _relative_slot("heldoutClaim"),
        _relative_slot("heldoutReport"),
        _relative_slot("heldoutClosure"),
    }
    _audit_namespace(registry, exact_files=final_files)
    return report_identity


def protocol_document() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PROTOCOL_KIND,
        "profileId": PROFILE_ID,
        "status": "single-lineage-replayable-generation6-protocol",
        "upstreamAuthority": {
            "requiredCapsuleKind": UPSTREAM_CAPSULE_KIND,
            "canonicalRelativePath": (
                "build-msvc/data-generation/omega-decision-v3/"
                "capsule.closure.json"
            ),
            "targetFreeRoutingKind": UPSTREAM_ROUTING_KIND,
            "teacherClaimKind": UPSTREAM_TEACHER_CLAIM_KIND,
            "teacherCompletionKind": UPSTREAM_TEACHER_COMPLETION_KIND,
            "preTargetHceClaimKind": UPSTREAM_HCE_CLAIM_KIND,
            "freshVerifier": {
                "canonicalRelativePath": "tools/omega_nnue/verify_omega_decision_v3_upstream.py",
                "options": upstream_verifier_options_document(),
                "receiptMaterializedInAuthority": True,
            },
            "teacherBudgets": dict(UPSTREAM_TEACHER_BUDGETS),
            "closureDeclaration": dict(UPSTREAM_CAPSULE_DECLARATION),
            "requiredCrossChecks": [
                "source root/group, child id/OFEN, phase, side",
                "independent component and whole-component split projection",
                "fresh terminal rules manifest/transcript/completion coverage replay",
                "fresh semantic replay of every prior-forbidden catalog with zero overlap",
                "planned projection producer/corpus/manifest against realized projection",
                "teacher claim, engine, runner, options, budget, order, ledger, and completion replay",
                "controlled pre-target HCE claim/transcript and fresh engine replay",
                "fixed prior-promoted initializer catalog or deterministic fallback",
                "semantic G5 success/failure/abort closure without requiring G5 success",
                "exact projection and closure identities",
            ],
            "downstreamHeldOutVerification": "opaque structural routing only",
        },
        "canonicalNamespace": {
            "root": "derived immutably from this source repository",
            "preregistration": "00-preregistration.json",
            "artifactPathInventory": {
                "slots": len(CANONICAL_ARTIFACT_PATHS),
                "sha256": _document_identity(
                    dict(CANONICAL_ARTIFACT_PATHS)
                )["sha256"],
            },
            "unknownFutureOrAlternateArtifactsAccepted": False,
            "symlinkJunctionReparseHardlinkSpecialAccepted": False,
            "caseFoldCollisionsAccepted": False,
            "publishMode": "descriptor-verified O_EXCL append-only bundles",
            "stageOrder": [
                "materialize-canonical-authority",
                "train-primary-G6A",
                "train-primary-G6B",
                "train-primary-G6C",
                "evaluate-validation-I0",
                "evaluate-validation-G6A",
                "evaluate-validation-G6B",
                "evaluate-validation-G6C",
                "select-primary",
                "train-selected-robustness",
                "evaluate-selected-robustness",
                "seal-robustness",
                "arm-heldout",
                "consume-heldout-once-and-close",
            ],
        },
        "formalApi": [
            "materialize_canonical_authority()",
            "train_primary(G6A|G6B|G6C)",
            "evaluate_validation(I0|G6A|G6B|G6C)",
            "select_primary()",
            "train_robustness_selected()",
            "evaluate_robustness_selected()",
            "seal_robustness()",
            "arm_heldout()",
            "consume_heldout_once()",
        ],
        "formalApiForbiddenInputs": [
            "output path",
            "model path",
            "root objects",
            "predictions",
            "metric report",
            "health boolean",
            "selection decision",
        ],
        "labelProjection": {
            "schemaVersion": SCHEMA_VERSION,
            "kind": LABEL_KIND,
            "exactFieldInventory": sorted(LABEL_FIELDS),
            "scoreType": "exact JSON integer; booleans/floats forbidden",
            "scoreRangeInclusiveCp": [-SCORE_ABS_LIMIT_CP, SCORE_ABS_LIMIT_CP],
            "regretRangeInclusiveCp": [0, REGRET_MAX_CP],
            "rootChildScoreSign": "exact negation",
            "childSide": "opposite parentSideToMove",
            "childrenPerRoot": 4,
        },
        "training": {
            "candidateRecipes": {
                candidate: dict(CANDIDATE_RECIPES[candidate])
                for candidate in CANDIDATES
            },
            "optimizerProtocol": dict(OPTIMIZER_PROTOCOL),
            "primarySeedBase": PRIMARY_TRAINING_SEED_BASE,
            "robustnessSeedBase": ROBUSTNESS_TRAINING_SEED_BASE,
            "seedDerivation": SEED_DERIVATION,
            "initializerRule": "all primary and robustness runs start from exact I0",
            "initializerAuthority": {
                "orderedCatalog": list(INITIALIZER_ORDERED_CATALOG),
                "selectionModes": list(INITIALIZER_SELECTION_MODES),
                "fallbackProtocol": dict(INITIALIZER_FALLBACK_PROTOCOL),
                "rule": "first semantically verified independently promoted healthy compatible model; otherwise exact fallback",
                "g5FailureAbortIsVerifiedButNotFatal": True,
            },
            "runtimeManifestRequired": True,
            "batchOrderDigestRequired": True,
            "actualBatchOrderTranscript": {
                "kind": TRAINING_BATCH_ORDER_KIND,
                "exactFieldInventory": sorted(TRAINING_BATCH_ORDER_FIELDS),
                "precomputedExpectedDigestInCommandOrPrelaunchClaim": False,
                "publicationPoint": "append row only after optimizer step consumes ordered complete batch",
                "batchContent": "canonical ordered full label rows followed by canonical ordered HCE rows",
                "freshReplayRequired": True,
            },
            "formalCommandInputBoundary": "train-only corpus and train-only static HCE",
            "validationTargetsProvidedToTrainerCommand": False,
            "heldOutTargetsProvidedToTrainerCommand": False,
            "trainerFilesystemIsolationProvidedByOrchestrator": False,
            "resultBlindnessTrustBoundary": (
                "exact hash-pinned separately reviewed trainer runner; formal command and "
                "prelaunch claim provide no validation/held-out targets or precomputed "
                "expected batch digest"
            ),
            "objective": {
                "pointwise": {
                    "loss": "Huber on clipped deep-HCE residual",
                    "residualClipCp": RESIDUAL_CLIP_CP,
                    "normalizerCp": HUBER_NORMALIZER_CP,
                    "deltaNormalized": HUBER_DELTA_NORMALIZED,
                },
                "listwise": {
                    "temperatureCp": LISTWISE_TEMPERATURE_CP,
                    "teacherLogit": "-deepRegretCp / temperature",
                },
                "nearEqualTopSet": {
                    "maximumRegretCpInclusive": TOP_SET_MAX_REGRET_CP
                },
            },
        },
        "validation": {
            "aggregation": METRIC_AGGREGATION,
            "cells": list(_CELL_KEYS),
            "metrics": list(_METRIC_NAMES),
            "networkInference": "fresh pinned runtime replay over canonical OFEN order",
            "health": {
                "thresholds": dict(HEALTH_THRESHOLDS),
                "callerBooleanAccepted": False,
                "freshReplayRequiredAtSelection": True,
            },
            "nonRegressionGates": dict(NON_REGRESSION_GATES),
            "lexicographicOrder": list(VALIDATION_LEXICOGRAPHIC_ORDER),
            "candidateTiePriority": list(TIE_PRIORITY),
            "gameResultsRead": False,
        },
        "heldOut": {
            "formalApiRawRootAccess": False,
            "internalVerifierObjectBoundary": (
                "trusted in-process implementation; not part of the formal API"
            ),
            "claimBeforeFirstTargetDecode": True,
            "models": ["I0", "selected primary only"],
            "output": "eight-cell and macro aggregates only",
            "rawRootsChildrenOfensPredictionsPerRootMetricsEmitted": False,
            "claimOnlyCrashRetryPermitted": False,
            "spentForFutureGenerations": True,
            "closureIsTerminal": True,
        },
        "ioHardening": {
            "descriptorBackedSingleSnapshotParsing": True,
            "subprocessDependenciesSnapshottedBeforeAndAfter": True,
            "publisherDescriptorAndFinalPathCompared": True,
            "runtimeAndCompiledDependenciesFrozen": True,
            "freshPinnedUpstreamSemanticReplay": True,
            "recursiveTypeExactComparison": True,
            "duplicateJsonKeysAccepted": False,
        },
    }


def _legacy_protocol_document() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PROTOCOL_KIND,
        "profileId": PROFILE_ID,
        "status": "target-blind-generation-6-training-design-declared-before-labels",
        "informationBoundary": {
            "teacherLabelsAvailableWhenRecipeDeclared": False,
            "gameResultsMaySelectRecipeOrCandidate": False,
            "metricResultInformationRead": False,
            "extraLabelOrMetricFieldsRejected": True,
        },
        "labelProjection": {
            "schemaVersion": SCHEMA_VERSION,
            "kind": LABEL_KIND,
            "exactFieldInventory": sorted(LABEL_FIELDS),
            "scoreType": "exact JSON integer; booleans and floats forbidden",
            "scoreRangeInclusiveCp": [
                -SCORE_ABS_LIMIT_CP,
                SCORE_ABS_LIMIT_CP,
            ],
            "regretRangeInclusiveCp": [0, REGRET_MAX_CP],
            "coherenceRelativeTolerance": 0.0,
            "coherenceAbsoluteToleranceCp": SCORE_COHERENCE_ABS_TOLERANCE_CP,
            "childSide": "opposite parentSideToMove",
        },
        "decisionGrouping": {
            "childrenPerRoot": 4,
            "canonicalSiblingOrder": "ascending childId",
            "retainedFields": list(RETAINED_FIELDS),
            "permutationInvariant": True,
            "crossRootSiblingMixing": False,
            "optimizerAndMetricUnit": "one complete four-sibling root",
        },
        "candidateRecipes": {
            candidate: dict(CANDIDATE_RECIPES[candidate])
            for candidate in CANDIDATES
        },
        "objective": {
            "pointwise": {
                "loss": "Huber on clipped deep-HCE residual target",
                "weight": 1.0,
                "residualClipCp": RESIDUAL_CLIP_CP,
                "normalizerCp": HUBER_NORMALIZER_CP,
                "deltaNormalized": HUBER_DELTA_NORMALIZED,
            },
            "listwise": {
                "loss": "teacher softmax cross-entropy over all four siblings",
                "teacherLogit": "-deepRegretCp / temperatureCp",
                "studentLogit": "-(staticHceChildStm + residual) / temperatureCp",
                "temperatureCp": LISTWISE_TEMPERATURE_CP,
            },
            "nearEqualTopSet": {
                "loss": "negative log student probability mass on teacher top set",
                "membership": "deepRegretCp <= maximumRegretCp inclusive",
                "maximumRegretCp": TOP_SET_MAX_REGRET_CP,
            },
            "normalization": "mean siblings within root, then equal mean complete roots",
        },
        "batching": {
            "completeRootsOnly": True,
            "everyRootStructurallyRevalidated": True,
            "rootCountMustBeDivisibleByRootsPerBatch": True,
            "undersizedFinalBatch": "fail closed; never emitted or overweighted",
            "allAcceptedRootsPreservedExactlyOnce": True,
        },
        "staticHceAuthority": {
            "callableEvaluatorAccepted": False,
            "embeddedTestScoreAccepted": False,
            "requiredIdentities": [
                "exact corpus",
                "label manifest",
                "component map",
                "static-HCE projection",
                "static-HCE manifest",
                "engine executable",
                "options",
                "perspective",
            ],
            "perspective": STATIC_HCE_PERSPECTIVE,
        },
        "validationMetrics": {
            "aggregation": METRIC_AGGREGATION,
            "cellOrder": list(_CELL_KEYS),
            "metrics": list(_METRIC_NAMES),
            "topSetMembershipInclusiveRegretCp": TOP_SET_MAX_REGRET_CP,
            "studentMoveTieBreak": "ascending childId",
            "usesGameResults": False,
            "exactSchemaAndCountsRecomputed": True,
            "macroRecomputedFromCells": True,
            "requiresExactAuthorizedValidationRootInventory": True,
            "requiresModelAndAuthorityBindings": True,
        },
        "validationSelection": {
            "fixedNonRegressionGates": dict(NON_REGRESSION_GATES),
            "lexicographicOrder": list(VALIDATION_LEXICOGRAPHIC_ORDER),
            "candidateTiePriority": list(TIE_PRIORITY),
            "ifNoneEligible": "close generation without held-out or match access",
            "allReportsMustShareExactAuthorityBindings": True,
        },
        "accessBoundaries": {
            "routingBeforeTargetDecode": True,
            "opaquePassChecksExactSchemaAndDuplicateKeys": True,
            "primaryTrainingSplit": "train",
            "robustnessTrainingSplit": "train",
            "validationSelectionSplit": "validation",
            "heldOutSplit": "heldOut",
            "allAccessBindsExactLabelManifestComponentMapAndRootInventory": True,
            "heldOutRequiresPreexistingSeal": True,
            "sealChronology": [
                "validation-selection",
                "robustness",
                "held-out-access",
            ],
            "timestampFormat": TIMESTAMP_FORMAT,
            "publishers": "exclusive O_EXCL no-clobber regular files",
            "heldOutTargetRowsDecodedAtEveryPrerequisiteSeal": 0,
            "heldOutTargetFieldsDecodedAtEveryPrerequisiteSeal": 0,
            "symlinkJunctionReparseLeafOrParentAccepted": False,
        },
        "seeds": {
            "primaryTrainingBase": PRIMARY_TRAINING_SEED_BASE,
            "robustnessTrainingBase": ROBUSTNESS_TRAINING_SEED_BASE,
            "derivation": SEED_DERIVATION,
            "primaryPurpose": "primary-training",
            "robustnessPurpose": "robustness-training",
            "sameSeedAcrossPrimaryAndRobustness": False,
        },
        "protocolValidation": {
            "recursiveTypeExactComparison": True,
            "booleanNumericSubstitutionAccepted": False,
        },
    }


def _synthetic_root(
    root_id: str,
    *,
    phase: str = "opening",
    side: str = "w",
    split: str = "validation",
    regrets: Sequence[int] = (0, 25, 26, 100),
) -> DecisionRoot:
    if len(regrets) != 4 or any(type(value) is not int for value in regrets):
        raise ValueError("synthetic regrets must be four exact integers")
    child_side = "b" if side == "w" else "w"
    labels: list[DecisionLabel] = []
    best = 200
    for index, regret in enumerate(regrets):
        score = best - regret
        child_score = -score
        hce = -20 + index
        labels.append(
            DecisionLabel(
                root_id=root_id,
                component_id=f"component-{root_id}",
                split=split,
                sibling_id=f"{root_id}-s{index}",
                child_ofen=(
                    "10/10/10/10/10/10/10/10/10/10[-/-/-/-] "
                    f"{child_side} - - 0 1"
                ),
                phase=phase,
                root_side=side,
                deep_rank=index + 1,
                deep_regret_cp=regret,
                deep_score_cp_root=score,
                deep_score_cp_child_stm=child_score,
                handcrafted_cp_child_stm=hce,
                residual_target_cp=float(
                    np.clip(
                        child_score - hce,
                        -RESIDUAL_CLIP_CP,
                        RESIDUAL_CLIP_CP,
                    )
                ),
            )
        )
    return DecisionRoot(
        root_id=root_id,
        component_id=f"component-{root_id}",
        split=split,
        phase=phase,
        root_side=side,
        siblings=tuple(labels),
    )


def _self_test() -> dict[str, Any]:
    root = _synthetic_root("root-a")
    predictions = {
        label.sibling_id: label.residual_target_cp + (index - 1.5) * 9.0
        for index, label in enumerate(root.siblings)
    }
    objective = decision_root_objective(root, predictions, "G6B")
    if objective.top_set_sibling_ids != ("root-a-s0", "root-a-s1"):
        raise AssertionError("inclusive 25 cp top set changed")
    epsilon = 1e-4
    for sibling in root.sibling_ids:
        plus = dict(predictions)
        minus = dict(predictions)
        plus[sibling] += epsilon
        minus[sibling] -= epsilon
        numerical = (
            decision_root_objective(root, plus, "G6B").total_loss
            - decision_root_objective(root, minus, "G6B").total_loss
        ) / (2.0 * epsilon)
        if not math.isclose(
            numerical,
            objective.gradient_by_sibling[sibling],
            rel_tol=2e-5,
            abs_tol=2e-7,
        ):
            raise AssertionError("G6 objective gradient changed")
    permuted = DecisionRoot(
        root_id=root.root_id,
        component_id=root.component_id,
        split=root.split,
        phase=root.phase,
        root_side=root.root_side,
        siblings=tuple(reversed(root.siblings)),
    )
    if not math.isclose(
        objective.total_loss,
        decision_root_objective(permuted, predictions, "G6B").total_loss,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise AssertionError("objective depends on sibling input order")
    if TIE_PRIORITY != ("G6A", "G6B", "G6C"):
        raise AssertionError("candidate tie priority changed")
    if (
        NON_REGRESSION_GATES["maximumCellTopSetAccuracyRegression"] != 0.01
        or NON_REGRESSION_GATES["maximumCellChosenMoveRegretIncreaseCp"] != 5.0
    ):
        raise AssertionError("cell non-regression gates changed")
    return {
        "status": "passed",
        "tieTopSetInclusive": True,
        "permutationInvariant": True,
        "finiteDifferenceGradients": True,
        "candidateTiePriority": list(TIE_PRIORITY),
        "resultInformationRead": False,
        "productionStaticHceBypass": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("self-test", "print-protocol"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = _self_test() if args.command == "self-test" else protocol_document()
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
