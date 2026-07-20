#!/usr/bin/env python3
"""One-time generation-3 held-out gate with sufficient-statistics sealing.

The canonical validation selection is recomputed exactly before access.  The
canonical robustness run and preselection match-readiness seal must also pass.
The phase-incidence preclaim audit is then recomputed target-blind from only
``groupId`` and OFEN.  Only after every identity is rehashed does this tool
publish an exclusive access claim and route split-2 rows to JSON decoding.

If anything fails after the claim exists, the claim and an exclusive failure
seal are retained.  A failed or interrupted access can never be retried.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Sequence

# The frozen trainer records and rejects a preloaded NumPy module.  It must be
# the first project/NumPy-dependent import in this process.
import king_state_train_generation3 as training

import numpy as np

import king_state_match_readiness_generation3 as readiness
import king_state_v3 as prelabel
from omega_nnue import QuantizedNetwork, deterministic_split
import phase_incidence_preflight as incidence


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v3-deep-hce-v4"
CLAIM_KIND = "omega-nnue-king-state-v3-offline-access-claim"
REPORT_KIND = "omega-nnue-king-state-v3-offline-report"
ATTESTATION_KIND = (
    "omega-nnue-king-state-v3-offline-sufficient-statistics"
)
FAILURE_KIND = "omega-nnue-king-state-v3-offline-failure"

REPO = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO / "build-msvc" / "king-state-v3"
DATA_DIR = REPO / "build-msvc" / "data-generation" / "deep-hce-v4"
PLAN_PATH = OUTPUT_DIR / "training-plan.json"
SELECTION_PATH = OUTPUT_DIR / "validation-selection.seal.json"
ROBUSTNESS_PATH = OUTPUT_DIR / "robustness.seal.json"
CORPUS_PATH = DATA_DIR / "deep-hce-v2-residual.jsonl"
PRECLAIM_AUDIT_PATH = DATA_DIR / "phase-incidence-preclaim.seal.json"
READINESS_PATH = readiness.SEAL_PATH
FUTURE_RUN_ROOT = readiness.FUTURE_RUN_ROOT
CLAIM_PATH = OUTPUT_DIR / "offline-test.json.access.json"
REPORT_PATH = OUTPUT_DIR / "offline-test.json"
ATTESTATION_PATH = OUTPUT_DIR / "offline-test.sufficient.json"
FAILURE_PATH = OUTPUT_DIR / "offline-test.failure.json"
PREREGISTRATION = (
    REPO / "validation" / "omega-nnue-king-state-v3-preregistration.json"
)
AMENDMENT_001 = training.AMENDMENT
AMENDMENT_002 = training.AMENDMENT_002
AMENDMENT_003 = training.AMENDMENT_003

MODEL_ORDER = ("selected", "I0", "robustness", "zeroResidual")
BASELINES = ("I0", "zeroResidual")
PHASES = tuple(training.PHASES)
BOOTSTRAP_SEED = 20260733
BOOTSTRAP_ITERATIONS = 10_000
MIN_RELATIVE_IMPROVEMENT = 0.01
MIN_CP_MAE_IMPROVEMENT = 2.0
MAX_PHASE_CP_MAE_REGRESSION = 5.0

IDENTITY_FIELDS = {"path", "bytes", "sha256"}
CAPTURE_PATHS = {
    "wrapper": Path(__file__).resolve(),
    "trainingOrchestrator": Path(training.__file__).resolve(),
    "prelabelOrchestrator": Path(prelabel.__file__).resolve(),
    "phaseIncidenceImplementation": Path(incidence.__file__).resolve(),
    "readinessImplementation": Path(readiness.__file__).resolve(),
    "matchAdapter": readiness.ADAPTER,
    "matchAdapterContract": readiness.ADAPTER_CONTRACT,
    "matchProtocol": readiness.COMPATIBILITY_PROTOCOL,
    "compatibilityReplayCore": Path(readiness.replay.__file__).resolve(),
    "compatibilityMatchCore": Path(readiness.core.__file__).resolve(),
    "historyConverterProject": readiness.replay.CONVERTER_PROJECT,
    "historyConverterSource": readiness.replay.CONVERTER_SOURCE,
    "preregistration": PREREGISTRATION,
    "amendment001": AMENDMENT_001,
    "amendment002": AMENDMENT_002,
    "amendment003": AMENDMENT_003,
    "trainingPlan": PLAN_PATH,
    "validationSelection": SELECTION_PATH,
    "robustnessSeal": ROBUSTNESS_PATH,
    "matchReadinessSeal": READINESS_PATH,
    "corpus": CORPUS_PATH,
    "phaseIncidencePreclaim": PRECLAIM_AUDIT_PATH,
}
DYNAMIC_CAPTURE_FIELDS = {
    "selectedNetwork",
    "selectedManifest",
    "selectedCanonicalDeploymentFloat",
    "initializerNetwork",
    "initializerManifest",
    "robustnessNetwork",
    "robustnessManifest",
}
OUTPUT_PATHS = {
    "accessClaim": CLAIM_PATH,
    "report": REPORT_PATH,
    "sufficientStatistics": ATTESTATION_PATH,
    "failure": FAILURE_PATH,
}
CLAIM_FIELDS = {
    "schemaVersion",
    "kind",
    "createdUtc",
    "profileId",
    "selectionCandidateId",
    "selectedNetwork",
    "amendmentChain",
    "preaccessIdentities",
    "futureMatchRoot",
    "phaseIncidence",
    "outputs",
    "access",
}
ATTESTATION_FIELDS = {
    "schemaVersion",
    "kind",
    "createdUtc",
    "profileId",
    "accessClaim",
    "preaccessIdentities",
    "selectionCandidateId",
    "selectedNetwork",
    "networks",
    "corpus",
    "phaseIncidence",
    "statisticsOrder",
    "phaseOrder",
    "statistics",
    "decision",
    "access",
}
REPORT_FIELDS = {
    "schemaVersion",
    "kind",
    "createdUtc",
    "profileId",
    "accessClaim",
    "sufficientStatistics",
    "selectionCandidateId",
    "selectedNetwork",
    "metrics",
    "comparisons",
    "robustness",
    "passed",
    "failureAction",
}
FAILURE_FIELDS = {
    "schemaVersion",
    "kind",
    "createdUtc",
    "profileId",
    "accessClaim",
    "stage",
    "errorType",
    "error",
    "preaccessIdentities",
    "secondAccessAllowed",
}


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strict_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"{label} must be a UTC JSON timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} must be a UTC JSON timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(
        parsed
    ):
        raise ValueError(f"{label} must be a UTC JSON timestamp")
    return parsed.astimezone(timezone.utc)


def _unique_object(
    pairs: list[tuple[str, Any]], *, label: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"{label} repeats JSON key {key!r}")
        result[key] = value
    return result


def _strict_loads(text: str, label: str) -> Any:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"{label} contains non-finite JSON number {value}")

    return json.loads(
        text,
        parse_constant=reject_constant,
        object_pairs_hook=lambda pairs: _unique_object(pairs, label=label),
    )


def _strict_object(path: Path, label: str) -> dict[str, Any]:
    value = _strict_loads(_resolve(path).read_text(encoding="utf-8"), label)
    if type(value) is not dict:
        raise ValueError(f"{label} is not a JSON object")
    return value


def _exact_fields(
    value: Mapping[str, Any], fields: set[str], label: str
) -> None:
    if set(value) != fields:
        raise ValueError(
            f"{label} field inventory changed; "
            f"missing={sorted(fields - set(value))}, "
            f"extra={sorted(set(value) - fields)}"
        )


def _strict_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return (
            set(actual) == set(expected)
            and all(
                _strict_equal(actual[key], expected[key]) for key in expected
            )
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _strict_equal(left, right)
            for left, right in zip(actual, expected)
        )
    return bool(actual == expected)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"{label} must be a JSON array")
    return value


def _finite(value: Any, label: str, *, nonnegative: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite JSON number")
    number = float(value)
    if nonnegative and number < 0.0:
        raise ValueError(f"{label} must be nonnegative")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative JSON integer")
    return value


def _identity(value: Any, label: str) -> dict[str, Any]:
    record = _mapping(value, label)
    _exact_fields(record, IDENTITY_FIELDS, label)
    if type(record.get("path")) is not str or not record["path"]:
        raise ValueError(f"{label}.path must be a nonempty JSON string")
    _nonnegative_int(record.get("bytes"), f"{label}.bytes")
    digest = record.get("sha256")
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in digest)
    ):
        raise ValueError(f"{label}.sha256 is invalid")
    return record


def _same_identity(left: Any, right: Any) -> bool:
    try:
        first = _identity(left, "left identity")
        second = _identity(right, "right identity")
        return (
            _resolve(Path(first["path"])) == _resolve(Path(second["path"]))
            and first["bytes"] == second["bytes"]
            and first["sha256"].lower() == second["sha256"].lower()
        )
    except (OSError, TypeError, ValueError):
        return False


def _pinned_bytes(value: Any, label: str) -> bytes:
    identity = _identity(value, label)
    path = _resolve(Path(identity["path"]))
    payload = path.read_bytes()
    actual = {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if not _same_identity(identity, actual):
        raise ValueError(f"{label} bytes differ from the pinned identity")
    return payload


def _declared_path(value: Any, label: str) -> Path:
    record = _identity(value, label)
    return _resolve(Path(record["path"]))


def _capture_paths() -> dict[str, Path]:
    paths = dict(CAPTURE_PATHS)
    selection = _strict_object(
        SELECTION_PATH, "preverification validation selection"
    )
    plan = _strict_object(PLAN_PATH, "preverification training plan")
    robustness = _strict_object(
        ROBUSTNESS_PATH, "preverification robustness seal"
    )
    identities = _mapping(
        plan.get("identities"), "preverification plan identities"
    )
    plan_sources = _mapping(
        identities.get("sources"), "preverification plan sources"
    )
    paths.update(
        {
            "selectedNetwork": _declared_path(
                selection.get("selectedNetwork"), "declared selected network"
            ),
            "selectedManifest": _declared_path(
                selection.get("selectedManifest"), "declared selected manifest"
            ),
            "selectedCanonicalDeploymentFloat": _declared_path(
                selection.get("selectedCanonicalDeploymentFloat"),
                "declared selected canonical float",
            ),
            "initializerNetwork": _declared_path(
                identities.get("initializer"), "declared initializer network"
            ),
            "initializerManifest": _declared_path(
                identities.get("initializerManifest"),
                "declared initializer manifest",
            ),
            "robustnessNetwork": _declared_path(
                robustness.get("robustnessNetwork"),
                "declared robustness network",
            ),
            "robustnessManifest": _declared_path(
                robustness.get("robustnessManifest"),
                "declared robustness manifest",
            ),
        }
    )
    for name, value in sorted(plan_sources.items()):
        if type(name) is not str or not name:
            raise ValueError("preverification plan source has an invalid name")
        paths[f"planSource:{name}"] = _declared_path(
            value, f"declared plan source {name}"
        )
    expected_dynamic = {
        *DYNAMIC_CAPTURE_FIELDS,
        *(f"planSource:{name}" for name in plan_sources),
    }
    if set(paths) - set(CAPTURE_PATHS) != expected_dynamic:
        raise AssertionError("dynamic preaccess identity inventory changed")
    return paths


def _capture_identities() -> dict[str, dict[str, Any]]:
    return {
        name: training._identity(_resolve(path))
        for name, path in _capture_paths().items()
    }


def _verify_captures(captures: Mapping[str, Any], label: str) -> None:
    paths = _capture_paths()
    if set(captures) != set(paths):
        raise ValueError(f"{label} identity inventory changed")
    for name, path in paths.items():
        expected = _identity(captures[name], f"{label}.{name}")
        actual = training._identity(_resolve(path))
        if not _same_identity(expected, actual):
            raise ValueError(f"{label} identity drift: {name}")


def _require_canonical_paths() -> None:
    expected = {
        PLAN_PATH: training.PLAN_PATH,
        SELECTION_PATH: training.SELECTION_PATH,
        ROBUSTNESS_PATH: training.ROBUSTNESS_PATH,
        CORPUS_PATH: training.CORPUS,
        PRECLAIM_AUDIT_PATH: prelabel.PRECLAIM_AUDIT,
        READINESS_PATH: readiness.SEAL_PATH,
    }
    for actual, wanted in expected.items():
        if _resolve(actual) != _resolve(wanted):
            raise ValueError(f"noncanonical generation-3 path: {actual}")
    for path in OUTPUT_PATHS.values():
        if _resolve(path).parent != _resolve(OUTPUT_DIR):
            raise ValueError(f"offline output left the canonical directory: {path}")


def _require_outputs_absent() -> None:
    present = [
        (name, _resolve(path))
        for name, path in OUTPUT_PATHS.items()
        if _resolve(path).exists()
    ]
    if present:
        name, path = present[0]
        raise FileExistsError(
            f"one-time generation-3 offline artifact already exists "
            f"({name}): {path}"
        )


def _strict_artifact_inputs() -> None:
    for path, label in (
        (PLAN_PATH, "generation-3 training plan"),
        (SELECTION_PATH, "generation-3 validation selection"),
        (ROBUSTNESS_PATH, "generation-3 robustness seal"),
        (READINESS_PATH, "generation-3 match readiness"),
        (PRECLAIM_AUDIT_PATH, "generation-3 phase preclaim audit"),
        (PREREGISTRATION, "generation-3 preregistration"),
        (AMENDMENT_001, "generation-3 amendment-001"),
        (AMENDMENT_002, "generation-3 amendment-002"),
        (AMENDMENT_003, "generation-3 amendment-003"),
    ):
        value = _strict_object(path, label)
        if path in (PLAN_PATH, SELECTION_PATH, ROBUSTNESS_PATH):
            if type(value.get("schemaVersion")) is not int:
                raise ValueError(f"{label}.schemaVersion must be a JSON integer")
            if type(value.get("kind")) is not str:
                raise ValueError(f"{label}.kind must be a JSON string")
            if type(value.get("profileId")) is not str:
                raise ValueError(f"{label}.profileId must be a JSON string")
    selection = _strict_object(SELECTION_PATH, "validation selection")
    if (
        type(selection.get("selectedCandidateId")) is not str
        or selection["selectedCandidateId"] not in training.CANDIDATES
    ):
        raise ValueError("validation selection candidate has a wrong JSON type")
    for field in (
        "selectedNetwork",
        "selectedManifest",
        "selectedCanonicalDeploymentFloat",
    ):
        _identity(selection.get(field), f"validation selection {field}")
    robustness = _strict_object(ROBUSTNESS_PATH, "robustness seal")
    if type(robustness.get("passed")) is not bool:
        raise ValueError("robustness passed must be a JSON boolean")


def _verify_preclaim_phase_incidence() -> dict[str, Any]:
    audit = incidence.verify_audit(
        CORPUS_PATH,
        PRECLAIM_AUDIT_PATH,
        split_seed=training.SPLIT_SEED,
        train_percent=training.TRAIN_PERCENT,
        validation_percent=training.VALIDATION_PERCENT,
        minimum_groups=2,
    )
    if audit.get("passed") is not True:
        raise ValueError("phase-incidence preclaim audit did not pass")
    if audit.get("minimumGroupsPerObservedStratum") != 2:
        raise ValueError("phase-incidence preclaim minimum changed")
    splits = _sequence(audit.get("splits"), "phase-incidence splits")
    if len(splits) != 3:
        raise ValueError("phase-incidence audit must cover exactly three splits")
    for split_id, raw in enumerate(splits):
        split = _mapping(raw, f"phase-incidence split {split_id}")
        if (
            type(split.get("splitId")) is not int
            or split["splitId"] != split_id
            or split.get("passed") is not True
        ):
            raise ValueError(f"phase-incidence split {split_id} did not pass")
        strata = _sequence(
            split.get("observedStrata"),
            f"phase-incidence split {split_id} strata",
        )
        if not strata:
            raise ValueError(f"phase-incidence split {split_id} has no strata")
        for ordinal, raw_stratum in enumerate(strata):
            stratum = _mapping(
                raw_stratum,
                f"phase-incidence split {split_id} stratum {ordinal}",
            )
            if (
                type(stratum.get("groupCount")) is not int
                or stratum["groupCount"] < 2
            ):
                raise ValueError(
                    f"phase-incidence split {split_id} has a singleton stratum"
                )
    # A second, independently implemented target-opaque scan derives phase
    # from OFEN and checks the same minimum over all three splits.
    features = training._load_feature_corpus(CORPUS_PATH)
    internal = training._phase_incidence_audit(features)
    internal_splits = _mapping(
        internal.get("splits"), "trainer phase-incidence splits"
    )
    for split_id in range(3):
        split = _mapping(
            internal_splits.get(str(split_id)),
            f"trainer phase-incidence split {split_id}",
        )
        strata = _mapping(
            split.get("strata"),
            f"trainer phase-incidence split {split_id} strata",
        )
        if not strata or min(int(value) for value in strata.values()) < 2:
            raise ValueError(
                f"trainer phase-incidence split {split_id} minimum failed"
            )
    return audit


def _verify_preaccess() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
]:
    _require_canonical_paths()
    _require_outputs_absent()
    if _resolve(FUTURE_RUN_ROOT).exists():
        raise FileExistsError(
            f"future generation-3 match root must be absent: {FUTURE_RUN_ROOT}"
        )
    # These identities are deliberately captured before any deep verification.
    captures = _capture_identities()
    _strict_artifact_inputs()
    prelabel._verify_seal()
    training._validate_preregistration(PREREGISTRATION)
    readiness._amendment_chain_identities()
    selection, plan, profile = training._verify_selection(SELECTION_PATH)
    verifier = getattr(training, "_verify_robustness", None)
    if not callable(verifier):
        raise ValueError(
            "generation-3 trainer lacks the exact robustness verifier required "
            "before held-out access"
        )
    robustness_result = verifier(ROBUSTNESS_PATH)
    if type(robustness_result) is not tuple or len(robustness_result) != 4:
        raise ValueError("generation-3 robustness verifier contract changed")
    robustness, robust_selection, robust_plan, robust_profile = (
        robustness_result
    )
    robustness = _mapping(robustness, "verified robustness seal")
    if (
        robust_selection != selection
        or robust_plan != plan
        or robust_profile != profile
    ):
        raise ValueError(
            "robustness exact verifier returned a different selection/plan"
        )
    if robustness.get("passed") is not True:
        raise ValueError("generation-3 robustness run did not pass")
    ready = readiness._verify(
        READINESS_PATH,
        allow_future_run=False,
    )
    phase_audit = _verify_preclaim_phase_incidence()
    _verify_captures(captures, "preaccess")
    _require_outputs_absent()
    if _resolve(FUTURE_RUN_ROOT).exists():
        raise FileExistsError(
            "future generation-3 match root appeared before access claim"
        )
    return selection, plan, profile, robustness, captures | {
        "_phaseAudit": phase_audit,
        "_readinessAudit": ready,
    }


def _number_field(record: Mapping[str, Any], name: str, label: str) -> float:
    return _finite(record.get(name), f"{label}.{name}")


def _load_heldout(
    path: Path,
    *,
    expected_identity: Mapping[str, Any],
    loads: Callable[[str, str], Any] = _strict_loads,
) -> training.LabeledCorpus:
    """Route by selectively decoded groupId before parsing target-bearing rows."""

    records: list[tuple[str, str, str]] = []
    targets: list[float] = []
    searches: list[float] = []
    handcrafted: list[float] = []
    source_rows = 0
    heldout_rows = 0
    resolved = _resolve(path)
    expected = _identity(expected_identity, "held-out corpus identity")
    if _resolve(Path(expected["path"])) != resolved:
        raise ValueError("held-out corpus identity names a different path")
    payload = _pinned_bytes(expected, "held-out corpus")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("held-out corpus is not valid UTF-8") from error
    # Parse the exact in-memory bytes that were just identity-checked.  A path
    # replacement after this point cannot change which targets are decoded.
    with io.StringIO(text, newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            source_rows += 1
            location = f"{resolved}:{line_number}"
            group = training._selective_string(line, "groupId", location)
            split = deterministic_split(
                group,
                training.SPLIT_SEED,
                training.TRAIN_PERCENT,
                training.VALIDATION_PERCENT,
            )
            if split != 2:
                continue
            heldout_rows += 1
            value = loads(line, location)
            record = _mapping(value, location)
            if record.get("groupId") != group:
                raise ValueError(f"{location}: groupId changed during decode")
            ofen_value = record.get("ofen")
            phase = record.get("phase")
            semantics = record.get("targetSemantics")
            if type(ofen_value) is not str or not ofen_value.strip():
                raise ValueError(f"{location}: missing OFEN")
            if type(phase) is not str or phase not in PHASES:
                raise ValueError(f"{location}: invalid phase")
            if type(semantics) is not str or semantics != "search-minus-handcrafted":
                raise ValueError(f"{location}: wrong target semantics")
            ofen = " ".join(ofen_value.split())
            derived_phase = training._phase_from_ofen(ofen, location)
            if phase != derived_phase:
                raise ValueError(f"{location}: stored phase differs from OFEN")
            target = _number_field(record, "targetCpStm", location)
            search = _number_field(record, "searchTargetCpStm", location)
            hce = _number_field(record, "handcraftedCpStm", location)
            if not math.isclose(
                search - hce, target, rel_tol=0.0, abs_tol=1e-6
            ):
                raise ValueError(f"{location}: residual identity changed")
            records.append((ofen, group, derived_phase))
            targets.append(float(np.clip(target, -training.CP_CLIP, training.CP_CLIP)))
            searches.append(search)
            handcrafted.append(hce)
    if source_rows == 0 or heldout_rows == 0:
        raise ValueError("corpus has no held-out rows")
    white, black, stm, signatures = training._feature_rows(records)
    features = training.FeatureCorpus(
        ofens=tuple(item[0] for item in records),
        groups=tuple(item[1] for item in records),
        phases=tuple(item[2] for item in records),
        splits=np.full(len(records), 2, dtype=np.int8),
        white_features=white,
        black_features=black,
        side_to_move_white=stm,
        input_signatures=signatures,
    )
    if features.indices(2).size == 0:
        raise ValueError("held-out feature corpus is empty")
    return training.LabeledCorpus(
        features=features,
        target_cp=np.asarray(targets, dtype=np.float32),
        search_cp=np.asarray(searches, dtype=np.float32),
        handcrafted_cp=np.asarray(handcrafted, dtype=np.float32),
        source_rows=source_rows,
        withheld_rows=source_rows - heldout_rows,
    )


def _statistic(metrics: Mapping[str, Any]) -> dict[str, Any]:
    phases = _mapping(metrics.get("phase"), "metric phases")
    group_loss = _mapping(metrics.get("_groupLoss"), "metric group loss")
    group_mae = _mapping(metrics.get("_groupMae"), "metric group MAE")
    return {
        "rows": sum(
            _nonnegative_int(
                _mapping(phases[phase], f"metric {phase}").get("rows"),
                f"metric {phase} rows",
            )
            for phase in PHASES
        ),
        "groupLoss": {
            phase: {
                group: _finite(value, f"{phase}.{group} loss", nonnegative=True)
                for group, value in sorted(
                    _mapping(group_loss[phase], f"{phase} group loss").items()
                )
            }
            for phase in PHASES
        },
        "groupMae": {
            phase: {
                group: _finite(value, f"{phase}.{group} MAE", nonnegative=True)
                for group, value in sorted(
                    _mapping(group_mae[phase], f"{phase} group MAE").items()
                )
            }
            for phase in PHASES
        },
    }


def _validate_statistic(value: Any, label: str) -> dict[str, Any]:
    statistic = _mapping(value, label)
    _exact_fields(statistic, {"rows", "groupLoss", "groupMae"}, label)
    _nonnegative_int(statistic.get("rows"), f"{label}.rows")
    for field in ("groupLoss", "groupMae"):
        phases = _mapping(statistic.get(field), f"{label}.{field}")
        _exact_fields(
            phases,
            set(PHASES),
            f"{label}.{field} phases",
        )
        for phase in PHASES:
            groups = _mapping(phases[phase], f"{label}.{field}.{phase}")
            if not groups or list(groups) != sorted(groups):
                raise ValueError(
                    f"{label}.{field}.{phase} groups are empty or unordered"
                )
            for group, raw in groups.items():
                if type(group) is not str or not group:
                    raise ValueError(f"{label} has an invalid group id")
                _finite(
                    raw,
                    f"{label}.{field}.{phase}.{group}",
                    nonnegative=True,
                )
    loss = statistic["groupLoss"]
    mae = statistic["groupMae"]
    for phase in PHASES:
        if set(loss[phase]) != set(mae[phase]):
            raise ValueError(f"{label}.{phase} loss/MAE group sets differ")
    return statistic


def _metrics_from_statistic(value: Any, label: str) -> dict[str, Any]:
    statistic = _validate_statistic(value, label)
    phase_metrics: dict[str, Any] = {}
    for phase in PHASES:
        losses = list(statistic["groupLoss"][phase].values())
        maes = list(statistic["groupMae"][phase].values())
        phase_metrics[phase] = {
            "groups": len(losses),
            "huberLoss": float(np.mean(losses)),
            "cpMae": float(np.mean(maes)),
        }
    return {
        "huberLoss": float(
            np.mean([phase_metrics[phase]["huberLoss"] for phase in PHASES])
        ),
        "cpMae": float(
            np.mean([phase_metrics[phase]["cpMae"] for phase in PHASES])
        ),
        "phase": phase_metrics,
    }


def _validate_statistics(value: Any) -> dict[str, dict[str, Any]]:
    statistics = _mapping(value, "offline statistics")
    _exact_fields(
        statistics,
        set(MODEL_ORDER),
        "offline statistics models",
    )
    result = {
        model: _validate_statistic(statistics[model], f"statistics.{model}")
        for model in MODEL_ORDER
    }
    reference = result["selected"]
    for model in MODEL_ORDER[1:]:
        for field in ("groupLoss", "groupMae"):
            for phase in PHASES:
                if set(result[model][field][phase]) != set(
                    reference[field][phase]
                ):
                    raise ValueError(
                        f"statistics.{model}.{phase} group set differs"
                    )
    return result


def _heldout_strata(
    statistics: Mapping[str, Mapping[str, Any]]
) -> dict[int, list[str]]:
    selected = statistics["selected"]["groupLoss"]
    groups = sorted(
        set().union(*(set(selected[phase]) for phase in PHASES))
    )
    strata: dict[int, list[str]] = {}
    for group in groups:
        mask = sum(
            1 << index
            for index, phase in enumerate(PHASES)
            if group in selected[phase]
        )
        strata.setdefault(mask, []).append(group)
    if not strata or min(map(len, strata.values())) < 2:
        raise ValueError(
            "held-out bootstrap has an observed phase-incidence stratum "
            "with fewer than two groups"
        )
    return strata


def _bootstrap(
    statistics: Mapping[str, Mapping[str, Any]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, dict[str, Any]]:
    if type(iterations) is not int or iterations <= 0:
        raise ValueError("bootstrap iterations must be a positive integer")
    if type(seed) is not int:
        raise ValueError("bootstrap seed must be an integer")
    statistics = _validate_statistics(statistics)
    strata = _heldout_strata(statistics)
    rng = np.random.Generator(np.random.PCG64(seed))
    improvements = {
        baseline: np.empty(iterations, dtype=np.float64)
        for baseline in BASELINES
    }
    for iteration in range(iterations):
        multiplicities: dict[str, int] = {}
        for groups in strata.values():
            draws = rng.integers(0, len(groups), size=len(groups))
            counts = np.bincount(draws, minlength=len(groups))
            multiplicities.update(
                {
                    group: int(count)
                    for group, count in zip(groups, counts)
                }
            )
        losses: dict[str, float] = {}
        for model in ("selected", *BASELINES):
            phase_values: list[float] = []
            for phase in PHASES:
                cells = statistics[model]["groupLoss"][phase]
                numerator = sum(
                    multiplicities[group] * float(value)
                    for group, value in cells.items()
                )
                denominator = sum(
                    multiplicities[group] for group in cells
                )
                if denominator <= 0:
                    raise ValueError(
                        "bootstrap resample omitted every group in a phase"
                    )
                phase_values.append(numerator / denominator)
            losses[model] = float(np.mean(phase_values))
        for baseline in BASELINES:
            baseline_loss = losses[baseline]
            if baseline_loss <= 0.0:
                raise ValueError("bootstrap baseline loss must be positive")
            improvements[baseline][iteration] = (
                baseline_loss - losses["selected"]
            ) / baseline_loss
    return {
        baseline: {
            "paired": True,
            "clusterUnit": "global leakage component groupId",
            "stratification": "exact frozen phase-incidence set",
            "iterations": iterations,
            "rng": "NumPy Generator PCG64",
            "seed": seed,
            "quantile": 0.05,
            "quantileMethod": "linear",
            "lowerBound": float(
                np.quantile(
                    improvements[baseline],
                    0.05,
                    method="linear",
                )
            ),
        }
        for baseline in BASELINES
    }


def _decision(
    raw_statistics: Mapping[str, Mapping[str, Any]],
    *,
    bootstrap_iterations: int = BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    statistics = _validate_statistics(raw_statistics)
    metrics = {
        model: _metrics_from_statistic(
            statistics[model], f"statistics.{model}"
        )
        for model in MODEL_ORDER
    }
    bootstraps = _bootstrap(
        statistics,
        iterations=bootstrap_iterations,
        seed=BOOTSTRAP_SEED,
    )
    comparisons: dict[str, Any] = {}
    selected = metrics["selected"]
    for baseline in BASELINES:
        control = metrics[baseline]
        baseline_loss = float(control["huberLoss"])
        if baseline_loss <= 0.0:
            raise ValueError(f"{baseline} Huber loss must be positive")
        relative = (
            baseline_loss - float(selected["huberLoss"])
        ) / baseline_loss
        cp_improvement = float(control["cpMae"]) - float(selected["cpMae"])
        phase_regression = {
            phase: (
                float(selected["phase"][phase]["cpMae"])
                - float(control["phase"][phase]["cpMae"])
            )
            for phase in PHASES
        }
        gates = {
            "minimumRelativeHuberImprovement": (
                relative >= MIN_RELATIVE_IMPROVEMENT
            ),
            "minimumPhaseMacroCpMaeImprovement": (
                cp_improvement >= MIN_CP_MAE_IMPROVEMENT
            ),
            "maximumAnyPhaseCpMaeRegression": (
                max(phase_regression.values())
                <= MAX_PHASE_CP_MAE_REGRESSION
            ),
            "bootstrapLowerBoundExclusive": (
                float(bootstraps[baseline]["lowerBound"]) > 0.0
            ),
        }
        comparisons[baseline] = {
            "relativeHuberImprovement": relative,
            "phaseMacroCpMaeImprovementCp": cp_improvement,
            "phaseCpMaeRegressionCp": phase_regression,
            "maximumAnyPhaseCpMaeRegressionCp": max(
                phase_regression.values()
            ),
            "bootstrap": bootstraps[baseline],
            "gates": gates,
            "passed": all(gates.values()),
        }
    robustness_gates = {
        baseline: (
            float(metrics["robustness"]["huberLoss"])
            < float(metrics[baseline]["huberLoss"])
        )
        for baseline in BASELINES
    }
    robustness = {
        "mayReplacePrimary": False,
        "directionalHuberImprovement": robustness_gates,
        "passed": all(robustness_gates.values()),
    }
    passed = (
        all(comparisons[baseline]["passed"] for baseline in BASELINES)
        and robustness["passed"]
    )
    return {
        "metrics": metrics,
        "comparisons": comparisons,
        "robustness": robustness,
        "passed": passed,
    }


def _claim(
    *,
    selection: Mapping[str, Any],
    captures: Mapping[str, Any],
    phase_audit: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_id = selection.get("selectedCandidateId")
    if type(candidate_id) is not str or candidate_id not in training.CANDIDATES:
        raise ValueError("selection has no canonical generation-3 winner")
    selected_network = _identity(
        selection.get("selectedNetwork"), "selected network"
    )
    claim = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": CLAIM_KIND,
        "createdUtc": _utc_now(),
        "profileId": PROFILE_ID,
        "selectionCandidateId": candidate_id,
        "selectedNetwork": dict(selected_network),
        "amendmentChain": readiness._amendment_chain_identities(),
        "preaccessIdentities": {
            name: dict(value) for name, value in captures.items()
        },
        "futureMatchRoot": {
            "path": str(_resolve(FUTURE_RUN_ROOT)),
            "absentImmediatelyBeforeAccessClaim": True,
            "mustRemainAbsentThroughOuterMatchSeal": True,
        },
        "phaseIncidence": {
            "audit": training._identity(PRECLAIM_AUDIT_PATH),
            "corpus": training._identity(CORPUS_PATH),
            "minimumGroupsPerObservedStratumPerSplit": 2,
            "allThreeSplitsPassed": phase_audit.get("passed") is True,
            "targetFieldsDecoded": 0,
        },
        "outputs": {
            name: str(_resolve(path)) for name, path in OUTPUT_PATHS.items()
        },
        "access": {
            "exclusiveNoClobberClaimBeforeFirstTargetDecode": True,
            "heldOutLabelsDecodedBeforeClaim": 0,
            "oneTimeAccess": True,
            "secondAccessAllowed": False,
            "runnerUpAllowedAfterAccess": False,
        },
    }
    _exact_fields(claim, CLAIM_FIELDS, "offline access claim")
    return claim


def _verify_claim(
    path: Path = CLAIM_PATH, *, allow_future_run: bool = True
) -> dict[str, Any]:
    if _resolve(path) != _resolve(CLAIM_PATH):
        raise ValueError(f"access claim path must be {_resolve(CLAIM_PATH)}")
    claim = _strict_object(path, "generation-3 offline access claim")
    _exact_fields(claim, CLAIM_FIELDS, "offline access claim")
    if (
        type(claim.get("schemaVersion")) is not int
        or claim["schemaVersion"] != SCHEMA_VERSION
        or type(claim.get("kind")) is not str
        or claim["kind"] != CLAIM_KIND
        or type(claim.get("profileId")) is not str
        or claim["profileId"] != PROFILE_ID
    ):
        raise ValueError("offline access claim schema/kind/profile changed")
    _strict_utc(claim.get("createdUtc"), "offline claim createdUtc")
    selection = _strict_object(
        SELECTION_PATH, "offline claim validation selection"
    )
    if (
        type(claim.get("selectionCandidateId")) is not str
        or claim["selectionCandidateId"]
        != selection.get("selectedCandidateId")
    ):
        raise ValueError("offline claim selected candidate changed")
    selected_network = _identity(
        claim.get("selectedNetwork"), "offline claim selected network"
    )
    if not _same_identity(
        selected_network, selection.get("selectedNetwork")
    ):
        raise ValueError("offline claim selected network differs from selection")
    actual_network = training._identity(Path(selected_network["path"]))
    if not _same_identity(selected_network, actual_network):
        raise ValueError("offline claim selected network bytes changed")
    amendment_chain = readiness._amendment_chain_identities()
    if not _strict_equal(claim.get("amendmentChain"), amendment_chain):
        raise ValueError("offline claim amendment chain changed")
    for ordinal, identity in enumerate(amendment_chain, start=1):
        readiness.core._verify_identity(
            identity, f"offline claim amendment-{ordinal:03d}"
        )
    if not _strict_equal(
        claim.get("futureMatchRoot"),
        {
            "path": str(_resolve(FUTURE_RUN_ROOT)),
            "absentImmediatelyBeforeAccessClaim": True,
            "mustRemainAbsentThroughOuterMatchSeal": True,
        },
    ):
        raise ValueError("offline claim future match-root binding changed")
    if not allow_future_run and _resolve(FUTURE_RUN_ROOT).exists():
        raise ValueError("future match root appeared before held-out access")
    access = _mapping(claim.get("access"), "offline claim access")
    expected_access = {
        "exclusiveNoClobberClaimBeforeFirstTargetDecode": True,
        "heldOutLabelsDecodedBeforeClaim": 0,
        "oneTimeAccess": True,
        "secondAccessAllowed": False,
        "runnerUpAllowedAfterAccess": False,
    }
    if not _strict_equal(access, expected_access):
        raise ValueError("offline claim one-time semantics changed")
    captures = _mapping(
        claim.get("preaccessIdentities"), "offline claim identities"
    )
    _verify_captures(captures, "offline claim")
    phase = _mapping(claim.get("phaseIncidence"), "offline claim incidence")
    _exact_fields(
        phase,
        {
            "audit",
            "corpus",
            "minimumGroupsPerObservedStratumPerSplit",
            "allThreeSplitsPassed",
            "targetFieldsDecoded",
        },
        "offline claim phase incidence",
    )
    if (
        type(phase.get("minimumGroupsPerObservedStratumPerSplit")) is not int
        or phase["minimumGroupsPerObservedStratumPerSplit"] != 2
        or phase.get("allThreeSplitsPassed") is not True
        or type(phase.get("targetFieldsDecoded")) is not int
        or phase["targetFieldsDecoded"] != 0
    ):
        raise ValueError("offline claim phase-incidence contract changed")
    if not _same_identity(
        phase.get("audit"), training._identity(PRECLAIM_AUDIT_PATH)
    ) or not _same_identity(
        phase.get("corpus"), training._identity(CORPUS_PATH)
    ):
        raise ValueError("offline claim phase-incidence identities changed")
    recomputed_phase = _verify_preclaim_phase_incidence()
    if recomputed_phase.get("passed") is not True:
        raise ValueError("offline claim phase-incidence recomputation failed")
    for name, expected_path in OUTPUT_PATHS.items():
        if _mapping(claim.get("outputs"), "offline claim outputs").get(name) != str(
            _resolve(expected_path)
        ):
            raise ValueError(f"offline claim output path changed: {name}")
    return claim


def _network_inventory(
    selection: Mapping[str, Any],
    plan: Mapping[str, Any],
    robustness: Mapping[str, Any],
) -> dict[str, Any]:
    identities = _mapping(plan.get("identities"), "training plan identities")
    return {
        "selected": dict(
            _identity(selection.get("selectedNetwork"), "selected network")
        ),
        "I0": dict(
            _identity(identities.get("initializer"), "initializer network")
        ),
        "robustness": dict(
            _identity(
                robustness.get("robustnessNetwork"),
                "robustness network",
            )
        ),
        "zeroResidual": {
            "kind": "constant-centipawn-prediction",
            "valueCp": 0,
        },
    }


def _predict_statistics(
    dataset: training.LabeledCorpus,
    networks: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    statistics: dict[str, dict[str, Any]] = {}
    for model in MODEL_ORDER:
        if model == "zeroResidual":
            predictions = np.zeros(
                dataset.features.count, dtype=np.int32
            )
        else:
            identity = _identity(networks[model], f"{model} network")
            network = QuantizedNetwork.from_bytes(
                _pinned_bytes(identity, f"{model} network")
            )
            predictions = training._predict_quantized(
                network, dataset.features
            )
        metrics = training._common_metrics(dataset, 2, predictions)
        statistics[model] = _statistic(metrics)
    return statistics


def _attestation(
    *,
    claim: Mapping[str, Any],
    captures: Mapping[str, Any],
    selection: Mapping[str, Any],
    networks: Mapping[str, Any],
    phase_audit: Mapping[str, Any],
    statistics: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ATTESTATION_KIND,
        "createdUtc": _utc_now(),
        "profileId": PROFILE_ID,
        "accessClaim": training._identity(CLAIM_PATH),
        "preaccessIdentities": {
            name: dict(value) for name, value in captures.items()
        },
        "selectionCandidateId": selection["selectedCandidateId"],
        "selectedNetwork": dict(
            _identity(selection["selectedNetwork"], "selected network")
        ),
        "networks": dict(networks),
        "corpus": training._identity(CORPUS_PATH),
        "phaseIncidence": {
            "audit": training._identity(PRECLAIM_AUDIT_PATH),
            "minimumGroupsPerObservedStratumPerSplit": 2,
            "allThreeSplitsPassed": phase_audit.get("passed") is True,
        },
        "statisticsOrder": list(MODEL_ORDER),
        "phaseOrder": list(PHASES),
        "statistics": dict(statistics),
        "decision": dict(decision),
        "access": {
            "heldOutAccessCount": 1,
            "targetsReopenedAfterThisInvocation": False,
            "sufficientStatisticsCapturedDuringSameInvocation": True,
            "sourceSelectionReadinessIdentitiesRecheckedAfterDecode": True,
            "secondAccessAllowed": False,
        },
    }


def _report(
    *,
    claim: Mapping[str, Any],
    selection: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "createdUtc": _utc_now(),
        "profileId": PROFILE_ID,
        "accessClaim": training._identity(CLAIM_PATH),
        "sufficientStatistics": training._identity(ATTESTATION_PATH),
        "selectionCandidateId": selection["selectedCandidateId"],
        "selectedNetwork": dict(
            _identity(selection["selectedNetwork"], "selected network")
        ),
        "metrics": decision["metrics"],
        "comparisons": decision["comparisons"],
        "robustness": decision["robustness"],
        "passed": decision["passed"],
        "failureAction": (
            "proceed to fresh preregistered match suites"
            if decision["passed"]
            else (
                "fail generation; do not test a runner-up and do not create "
                "or run a match suite"
            )
        ),
    }


def _publish_failure(
    *,
    stage: str,
    error: BaseException,
    captures: Mapping[str, Any],
) -> None:
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": FAILURE_KIND,
        "createdUtc": _utc_now(),
        "profileId": PROFILE_ID,
        "accessClaim": (
            training._identity(CLAIM_PATH) if CLAIM_PATH.is_file() else None
        ),
        "stage": stage,
        "errorType": type(error).__name__,
        "error": str(error),
        "preaccessIdentities": {
            name: dict(value) for name, value in captures.items()
        },
        "secondAccessAllowed": False,
    }
    _exact_fields(value, FAILURE_FIELDS, "offline failure seal")
    try:
        training._exclusive_json(FAILURE_PATH, value)
    except FileExistsError:
        # A racing or previous failure seal is evidence, never scratch state.
        pass


def _verify_attestation(path: Path = ATTESTATION_PATH) -> dict[str, Any]:
    if _resolve(path) != _resolve(ATTESTATION_PATH):
        raise ValueError(
            f"offline attestation path must be {_resolve(ATTESTATION_PATH)}"
        )
    value = _strict_object(path, "generation-3 offline attestation")
    _exact_fields(value, ATTESTATION_FIELDS, "offline attestation")
    if (
        type(value.get("schemaVersion")) is not int
        or value["schemaVersion"] != SCHEMA_VERSION
        or value.get("kind") != ATTESTATION_KIND
        or value.get("profileId") != PROFILE_ID
    ):
        raise ValueError("offline attestation schema/kind/profile changed")
    _strict_utc(
        value.get("createdUtc"), "offline attestation createdUtc"
    )
    claim = _verify_claim(CLAIM_PATH)
    if not _same_identity(
        value.get("accessClaim"), training._identity(CLAIM_PATH)
    ):
        raise ValueError("offline attestation claim identity changed")
    captures = _mapping(
        value.get("preaccessIdentities"), "attestation identities"
    )
    if captures != claim["preaccessIdentities"]:
        raise ValueError("attestation preaccess identities differ from claim")
    _verify_captures(captures, "offline attestation")
    if value.get("selectionCandidateId") != claim["selectionCandidateId"]:
        raise ValueError("attestation selected candidate changed")
    if not _same_identity(
        value.get("selectedNetwork"), claim["selectedNetwork"]
    ):
        raise ValueError("attestation selected network changed")
    if not _same_identity(
        value.get("corpus"), training._identity(CORPUS_PATH)
    ):
        raise ValueError("attestation corpus identity changed")
    networks = _mapping(value.get("networks"), "attestation networks")
    _exact_fields(
        networks,
        set(MODEL_ORDER),
        "attestation networks",
    )
    plan = _strict_object(PLAN_PATH, "attestation training plan")
    plan_identities = _mapping(plan.get("identities"), "plan identities")
    robustness = _strict_object(
        ROBUSTNESS_PATH, "attestation robustness seal"
    )
    expected_networks = {
        "selected": claim["selectedNetwork"],
        "I0": plan_identities["initializer"],
        "robustness": robustness["robustnessNetwork"],
        "zeroResidual": {
            "kind": "constant-centipawn-prediction",
            "valueCp": 0,
        },
    }
    if not _strict_equal(networks, expected_networks):
        raise ValueError("attestation network inventory changed")
    for model in ("selected", "I0", "robustness"):
        identity = _identity(networks[model], f"attestation {model} network")
        if not _same_identity(
            identity, training._identity(Path(identity["path"]))
        ):
            raise ValueError(f"attestation {model} network bytes changed")
    incidence_record = _mapping(
        value.get("phaseIncidence"), "attestation phase incidence"
    )
    if not _strict_equal(
        incidence_record,
        {
            "audit": training._identity(PRECLAIM_AUDIT_PATH),
            "minimumGroupsPerObservedStratumPerSplit": 2,
            "allThreeSplitsPassed": True,
        },
    ):
        raise ValueError("attestation phase-incidence record changed")
    if value.get("statisticsOrder") != list(MODEL_ORDER):
        raise ValueError("attestation statistics order changed")
    if value.get("phaseOrder") != list(PHASES):
        raise ValueError("attestation phase order changed")
    statistics = _validate_statistics(value.get("statistics"))
    expected = _decision(statistics)
    if not _strict_equal(value.get("decision"), expected):
        raise ValueError("offline attestation decision recomputation differs")
    access = _mapping(value.get("access"), "attestation access")
    if not _strict_equal(
        access,
        {
            "heldOutAccessCount": 1,
            "targetsReopenedAfterThisInvocation": False,
            "sufficientStatisticsCapturedDuringSameInvocation": True,
            "sourceSelectionReadinessIdentitiesRecheckedAfterDecode": True,
            "secondAccessAllowed": False,
        },
    ):
        raise ValueError("offline attestation access semantics changed")
    return value


def _verify_report(
    path: Path = REPORT_PATH, *, require_pass: bool = False
) -> dict[str, Any]:
    if _resolve(path) != _resolve(REPORT_PATH):
        raise ValueError(f"offline report path must be {_resolve(REPORT_PATH)}")
    if _resolve(FAILURE_PATH).exists():
        raise ValueError(
            "generation-3 offline failure seal exists; held-out access is "
            "terminal and cannot authorize matches"
        )
    value = _strict_object(path, "generation-3 offline report")
    _exact_fields(value, REPORT_FIELDS, "offline report")
    if (
        type(value.get("schemaVersion")) is not int
        or value["schemaVersion"] != SCHEMA_VERSION
        or value.get("kind") != REPORT_KIND
        or value.get("profileId") != PROFILE_ID
    ):
        raise ValueError("offline report schema/kind/profile changed")
    _strict_utc(value.get("createdUtc"), "offline report createdUtc")
    claim = _verify_claim(CLAIM_PATH)
    attestation = _verify_attestation(ATTESTATION_PATH)
    if not _same_identity(
        value.get("accessClaim"), training._identity(CLAIM_PATH)
    ):
        raise ValueError("offline report claim identity changed")
    if not _same_identity(
        value.get("sufficientStatistics"),
        training._identity(ATTESTATION_PATH),
    ):
        raise ValueError("offline report attestation identity changed")
    decision = attestation["decision"]
    for field in ("metrics", "comparisons", "robustness", "passed"):
        if not _strict_equal(value.get(field), decision[field]):
            raise ValueError(f"offline report {field} differs from attestation")
    if value.get("selectionCandidateId") != claim["selectionCandidateId"]:
        raise ValueError("offline report selected candidate changed")
    if not _same_identity(
        value.get("selectedNetwork"), claim["selectedNetwork"]
    ):
        raise ValueError("offline report selected network changed")
    expected_action = (
        "proceed to fresh preregistered match suites"
        if value.get("passed") is True
        else (
            "fail generation; do not test a runner-up and do not create "
            "or run a match suite"
        )
    )
    if type(value.get("failureAction")) is not str or value[
        "failureAction"
    ] != expected_action:
        raise ValueError("offline report failure action changed")
    if require_pass and value.get("passed") is not True:
        raise ValueError("generation-3 offline gate did not pass")
    return value


def _run() -> dict[str, Any]:
    stage = "preaccess"
    claimed = False
    captures: dict[str, Any] = {}
    try:
        (
            selection,
            plan,
            _profile,
            robustness,
            context,
        ) = _verify_preaccess()
        phase_audit = _mapping(
            context.pop("_phaseAudit"), "preclaim phase audit"
        )
        context.pop("_readinessAudit")
        captures = context
        claim = _claim(
            selection=selection,
            captures=captures,
            phase_audit=phase_audit,
        )
        stage = "access-claim"
        _verify_captures(captures, "immediately preclaim")
        _require_outputs_absent()
        if _resolve(FUTURE_RUN_ROOT).exists():
            raise FileExistsError(
                "future generation-3 match root appeared before access claim"
            )
        training._exclusive_json(CLAIM_PATH, claim)
        claimed = True
        _verify_claim(CLAIM_PATH, allow_future_run=False)

        stage = "heldout-decode"
        dataset = _load_heldout(
            CORPUS_PATH,
            expected_identity=captures["corpus"],
        )
        networks = _network_inventory(selection, plan, robustness)
        stage = "heldout-metrics"
        statistics = _predict_statistics(dataset, networks)
        decision = _decision(statistics)

        stage = "postdecode-identity-check"
        _verify_captures(captures, "postdecode")
        if _resolve(FUTURE_RUN_ROOT).exists():
            raise FileExistsError(
                "future generation-3 match root appeared during offline access"
            )
        if REPORT_PATH.exists() or ATTESTATION_PATH.exists():
            raise FileExistsError(
                "offline report/attestation appeared during held-out access"
            )

        stage = "attestation-publication"
        attestation = _attestation(
            claim=claim,
            captures=captures,
            selection=selection,
            networks=networks,
            phase_audit=phase_audit,
            statistics=statistics,
            decision=decision,
        )
        training._exclusive_json(ATTESTATION_PATH, attestation)
        _verify_attestation(ATTESTATION_PATH)

        stage = "report-publication"
        report = _report(
            claim=claim,
            selection=selection,
            decision=decision,
        )
        training._exclusive_json(REPORT_PATH, report)
        return _verify_report(REPORT_PATH)
    except BaseException as error:
        if claimed:
            _publish_failure(stage=stage, error=error, captures=captures)
        raise


def _self_test_group(split: int, ordinal: int) -> str:
    for attempt in range(1_000_000):
        group = f"offline-v3-self-test-{split}-{ordinal}-{attempt}"
        if deterministic_split(
            group,
            training.SPLIT_SEED,
            training.TRAIN_PERCENT,
            training.VALIDATION_PERCENT,
        ) == split:
            return group
    raise AssertionError("could not synthesize deterministic split group")


def _synthetic_statistics() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for model, base_loss, base_mae in (
        ("selected", 0.70, 70.0),
        ("I0", 1.00, 80.0),
        ("robustness", 0.75, 72.0),
        ("zeroResidual", 1.20, 90.0),
    ):
        result[model] = {
            "rows": 8,
            "groupLoss": {
                phase: {
                    "group-a": base_loss + phase_index * 0.01,
                    "group-b": base_loss + phase_index * 0.01 + 0.02,
                }
                for phase_index, phase in enumerate(PHASES)
            },
            "groupMae": {
                phase: {
                    "group-a": base_mae + phase_index,
                    "group-b": base_mae + phase_index + 1.0,
                }
                for phase_index, phase in enumerate(PHASES)
            },
        }
    return result


def _self_test() -> None:
    if training.NUMPY_WAS_PRELOADED:
        raise AssertionError("trainer observed NumPy before its thread contract")
    if len(readiness._amendment_chain_identities()) != 3:
        raise AssertionError("offline ordered amendment chain is incomplete")
    expected_utc = datetime(2026, 7, 18, tzinfo=timezone.utc)
    if (
        _strict_utc(
            "2026-07-18T00:00:00Z", "valid timestamp self-test"
        )
        != expected_utc
    ):
        raise AssertionError("valid UTC timestamp was not preserved")
    for malformed in (
        "Z",
        "garbageZ",
        "2026-07-18T00:00:00",
        "2026-07-18T00:00:00+00:00",
        True,
        1,
    ):
        try:
            _strict_utc(malformed, "malformed timestamp self-test")
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"malformed UTC timestamp was accepted: {malformed!r}"
            )
    duplicate = '{"groupId":"a","groupId":"b"}'
    try:
        _strict_loads(duplicate, "duplicate")
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate target-bearing JSON key was accepted")
    for raw in (True, False):
        try:
            _finite(raw, "boolean number")
        except ValueError:
            pass
        else:
            raise AssertionError("JSON boolean was accepted as a number")

    synthetic_statistics = _synthetic_statistics()
    decision = _decision(
        synthetic_statistics, bootstrap_iterations=64
    )
    if decision["passed"] is not True:
        raise AssertionError("strong synthetic offline candidate did not pass")
    canonical_statistics = _strict_loads(
        training._canonical_json(synthetic_statistics).decode("utf-8"),
        "canonical statistics round trip",
    )
    canonical_networks = _strict_loads(
        training._canonical_json(
            {model: {} for model in MODEL_ORDER}
        ).decode("utf-8"),
        "canonical network inventory round trip",
    )
    _exact_fields(
        _mapping(canonical_networks, "canonical network inventory"),
        set(MODEL_ORDER),
        "canonical network inventory",
    )
    canonical_decision = _decision(
        canonical_statistics, bootstrap_iterations=64
    )
    if canonical_decision != decision:
        raise AssertionError(
            "canonical statistics serialization changed the decision"
        )
    repeated = _decision(
        _synthetic_statistics(), bootstrap_iterations=64
    )
    if decision != repeated:
        raise AssertionError("offline bootstrap is not deterministic")
    weak = _synthetic_statistics()
    weak["selected"] = {
        **weak["selected"],
        "groupLoss": weak["I0"]["groupLoss"],
        "groupMae": weak["I0"]["groupMae"],
    }
    if _decision(weak, bootstrap_iterations=64)["passed"] is not False:
        raise AssertionError("non-improving synthetic candidate passed")
    singleton = _synthetic_statistics()
    for model in MODEL_ORDER:
        for field in ("groupLoss", "groupMae"):
            for phase in PHASES:
                del singleton[model][field][phase]["group-b"]
    try:
        _decision(singleton, bootstrap_iterations=8)
    except ValueError:
        pass
    else:
        raise AssertionError("singleton phase-incidence stratum was accepted")

    with tempfile.TemporaryDirectory(
        prefix="omega-king-state-v3-offline-self-test-"
    ) as raw:
        root = Path(raw)
        groups = {
            split: _self_test_group(split, 0) for split in range(3)
        }
        ofen = incidence._synthetic_ofen("endgame", "w", 0)
        lines = []
        for split in range(3):
            record = {
                "groupId": groups[split],
                "ofen": ofen,
                "phase": "endgame",
                "targetSemantics": "search-minus-handcrafted",
                "targetCpStm": 10,
                "searchTargetCpStm": 30,
                "handcraftedCpStm": 20,
            }
            lines.append(json.dumps(record) + "\n")
        corpus = root / "routing.jsonl"
        corpus.write_text("".join(lines), encoding="utf-8")
        decoded: list[str] = []

        def observing_loads(line: str, label: str) -> Any:
            decoded.append(label)
            return _strict_loads(line, label)

        heldout = _load_heldout(
            corpus,
            expected_identity=training._identity(corpus),
            loads=observing_loads,
        )
        if len(decoded) != 1 or heldout.features.count != 1:
            raise AssertionError(
                "non-held-out row reached target-bearing json.loads"
            )
        bad_record = {
            "groupId": groups[2],
            "ofen": ofen,
            "phase": "endgame",
            "targetSemantics": "search-minus-handcrafted",
            "targetCpStm": True,
            "searchTargetCpStm": 30,
            "handcraftedCpStm": 20,
        }
        bad_corpus = root / "boolean-target.jsonl"
        bad_corpus.write_text(json.dumps(bad_record) + "\n", encoding="utf-8")
        try:
            _load_heldout(
                bad_corpus,
                expected_identity=training._identity(bad_corpus),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("boolean held-out target was accepted")

        original_outputs = dict(OUTPUT_PATHS)
        try:
            globals()["OUTPUT_PATHS"] = {
                name: root / path.name
                for name, path in original_outputs.items()
            }
            _require_outputs_absent()
            training._exclusive_json(
                OUTPUT_PATHS["accessClaim"], {"first": True}
            )
            try:
                _require_outputs_absent()
            except FileExistsError:
                pass
            else:
                raise AssertionError("existing one-time claim was ignored")
            try:
                training._exclusive_json(
                    OUTPUT_PATHS["accessClaim"], {"second": True}
                )
            except FileExistsError:
                pass
            else:
                raise AssertionError("one-time claim was overwritten")
        finally:
            globals()["OUTPUT_PATHS"] = original_outputs

        original_report = REPORT_PATH
        original_failure = FAILURE_PATH
        try:
            globals()["REPORT_PATH"] = root / "terminal-report.json"
            globals()["FAILURE_PATH"] = root / "terminal-failure.json"
            FAILURE_PATH.write_text("{}\n", encoding="utf-8")
            try:
                _verify_report(REPORT_PATH)
            except ValueError as error:
                if "terminal" not in str(error):
                    raise
            else:
                raise AssertionError(
                    "preserved offline failure did not block authorization"
                )
        finally:
            globals()["REPORT_PATH"] = original_report
            globals()["FAILURE_PATH"] = original_failure

        source = root / "source.json"
        source.write_text("{}\n", encoding="utf-8")
        pin = training._identity(source)
        source.write_text('{"drift":true}\n', encoding="utf-8")
        if _same_identity(pin, training._identity(source)):
            raise AssertionError("source identity drift was accepted")
    print("king_state_offline_generation3 self-test passed", flush=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    verify = commands.add_parser("verify")
    verify.add_argument("--require-pass", action="store_true")
    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "run":
        report = _run()
        print(f"Offline gate passed={report['passed']}", flush=True)
    elif args.command == "verify":
        report = _verify_report(
            REPORT_PATH, require_pass=bool(args.require_pass)
        )
        print(f"Verified offline gate passed={report['passed']}", flush=True)
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=__import__("sys").stderr, flush=True)
        raise SystemExit(1)
