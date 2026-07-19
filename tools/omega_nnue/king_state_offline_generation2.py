#!/usr/bin/env python3
"""Capture sufficient statistics during the one generation-2 offline access.

This wrapper is intentionally narrower than the frozen training orchestrator:

* it accepts no path overrides;
* it requires the canonical selection and all three one-time outputs;
* it invokes the installed, unchanged generation-2 offline function once;
* it observes, but does not alter, the four calls to ``_group_phase_metrics``;
* it publishes one no-clobber sufficient-statistics attestation only after the
  unchanged offline function has successfully returned.

The attestation permits later audits to recompute every group/phase metric and
the preregistered paired bootstrap without decoding the held-out targets a
second time.  A failed or interrupted offline invocation cannot be retried
after the underlying access claim has been published.
"""

from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import math
import sys
import tempfile

import king_state_train as sealed
import king_state_train_amended as phase_incidence
import king_state_train_generation2 as generation2


SCHEMA_VERSION = 1
WRAPPER_KIND = "omega-nnue-king-state-v2-offline-sufficient-statistics"

REPO = sealed._repo_root()
OUTPUT_DIR = REPO / "build-msvc" / "king-state-v2"
DATA_DIR = REPO / "build-msvc" / "data-generation" / "deep-hce-v3"
READINESS_SEAL_PATH = (
    REPO
    / "build-king-state-v2"
    / "readiness"
    / "king-state-v2-match-readiness.seal.json"
)
FUTURE_RUN_ROOT = REPO.parent / "match-runs" / "output" / "king-state-v2"
SELECTION_PATH = OUTPUT_DIR / "validation-selection.seal.json"
REPORT_PATH = OUTPUT_DIR / "offline-test.json"
CLAIM_PATH = OUTPUT_DIR / "offline-test.json.access.json"
ATTESTATION_PATH = OUTPUT_DIR / "offline-test.sufficient.json"
CORPUS_PATH = DATA_DIR / "deep-hce-v2-residual.jsonl"

ACTUAL_METRIC_INVOCATION_ORDER = (
    "robustness",
    "selected",
    "K0",
    "zeroResidual",
)
STATISTICS_ORDER = ("selected", "K0", "robustness", "zeroResidual")
PHASE_ORDER = tuple(sealed.PHASES)

GENERATION2_MARKER = {
    "protocolGeneration": 2,
    "dataProfile": "deep-hce-v3",
    "healthMetricSubject": "deployment-equivalent float checkpoint",
}
SELECTION_GENERATION2_MARKER = {
    **GENERATION2_MARKER,
    "heldOutTargetsDecoded": False,
}
ZERO_RESIDUAL_DESCRIPTOR = {
    "kind": "constant-centipawn-prediction",
    "valueCp": 0,
}
SOURCE_PATHS = {
    "wrapper": Path(__file__).resolve(),
    "generation2Orchestrator": Path(generation2.__file__).resolve(),
    "sealedTrainer": Path(sealed.__file__).resolve(),
    "phaseIncidenceImplementation": Path(phase_incidence.__file__).resolve(),
}
TOP_LEVEL_FIELDS = {
    "schemaVersion",
    "kind",
    "createdUtc",
    "generation2",
    "sources",
    "matchReadinessSeal",
    "selection",
    "offlineReport",
    "testAccessClaim",
    "corpus",
    "networks",
    "actualMetricInvocationOrder",
    "statisticsOrder",
    "phaseOrder",
    "statistics",
    "access",
}
STATISTIC_FIELDS = {"rows", "groupLoss", "groupMae"}
IDENTITY_FIELDS = {"path", "bytes", "sha256"}
ACCESS_RECORD = {
    "unchangedGeneration2OfflineInvocations": 1,
    "metricAggregationCallsCaptured": 4,
    "sufficientStatisticsCapturedDuringSameInvocation": True,
    "targetsReopenedAfterOfflineInvocation": False,
    "readinessAndSelectionIdentitiesCapturedBeforeVerification": True,
    "identityDriftCheckedImmediatelyBeforeHeldOutInvocation": True,
}


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"{label} field inventory changed; missing={missing}, extra={extra}"
        )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    return sealed._mapping(value, label)


def _sequence(value: Any, label: str) -> list[Any]:
    return sealed._sequence(value, label)


def _number(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if number < 0.0:
        raise ValueError(f"{label} must be nonnegative")
    return number


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _same_number(left: Any, right: Any, label: str) -> None:
    actual = _number(left, label)
    expected = _number(right, f"expected {label}")
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} is not an ISO-8601 timestamp") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{label} must use UTC")


def _canonical_path(actual: Path, expected: Path, label: str) -> None:
    if actual.expanduser().resolve() != expected.expanduser().resolve():
        raise ValueError(f"{label} must be the canonical path {expected.resolve()}")


def _identity_object(value: Any, label: str) -> dict[str, Any]:
    pin = _mapping(value, label)
    _exact_fields(pin, IDENTITY_FIELDS, label)
    if (
        isinstance(pin.get("bytes"), bool)
        or not isinstance(pin.get("bytes"), int)
        or int(pin["bytes"]) < 0
    ):
        raise ValueError(f"{label} bytes must be a nonnegative integer")
    digest = pin.get("sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != sealed.SHA256_LENGTH
        or any(character not in "0123456789abcdefABCDEF" for character in digest)
    ):
        raise ValueError(f"{label} sha256 is invalid")
    return pin


def _verify_pin(
    value: Any, label: str, *, expected_path: Path | None = None
) -> Path:
    pin = _identity_object(value, label)
    path = sealed._verify_identity(pin, label)
    if expected_path is not None:
        _canonical_path(path, expected_path, label)
    return path


def _same_pin(left: Any, right: Any, label: str) -> None:
    left_pin = _identity_object(left, label)
    right_pin = _identity_object(right, f"expected {label}")
    if not sealed._same_identity(left_pin, right_pin):
        raise ValueError(f"{label} identity differs")


def _group_map(value: Any, label: str) -> dict[str, float]:
    groups = _mapping(value, label)
    if not groups:
        raise ValueError(f"{label} must not be empty")
    if list(groups) != sorted(groups):
        raise ValueError(f"{label} group ids are not canonically ordered")
    result: dict[str, float] = {}
    for group, raw in groups.items():
        if not isinstance(group, str) or not group:
            raise ValueError(f"{label} contains an invalid group id")
        result[group] = _number(raw, f"{label}.{group}")
    return result


def _validate_statistic(
    value: Any, model: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    statistic = _mapping(value, f"{model} statistics")
    _exact_fields(statistic, STATISTIC_FIELDS, f"{model} statistics")
    rows_value = _mapping(statistic.get("rows"), f"{model} rows")
    loss_value = _mapping(statistic.get("groupLoss"), f"{model} group loss")
    mae_value = _mapping(statistic.get("groupMae"), f"{model} group MAE")
    for label, phase_map in (
        ("rows", rows_value),
        ("group loss", loss_value),
        ("group MAE", mae_value),
    ):
        if set(phase_map) != set(PHASE_ORDER):
            raise ValueError(f"{model} {label} phase inventory changed")

    rows: dict[str, int] = {}
    group_loss: dict[str, dict[str, float]] = {}
    group_mae: dict[str, dict[str, float]] = {}
    phase_metrics: dict[str, dict[str, Any]] = {}
    for phase in PHASE_ORDER:
        rows[phase] = _positive_integer(
            rows_value[phase], f"{model} {phase} rows"
        )
        group_loss[phase] = _group_map(
            loss_value[phase], f"{model} {phase} group loss"
        )
        group_mae[phase] = _group_map(
            mae_value[phase], f"{model} {phase} group MAE"
        )
        if set(group_loss[phase]) != set(group_mae[phase]):
            raise ValueError(
                f"{model} {phase} loss and MAE group inventories differ"
            )
        if rows[phase] < len(group_loss[phase]):
            raise ValueError(f"{model} {phase} has fewer rows than groups")
        phase_metrics[phase] = {
            "groups": len(group_loss[phase]),
            "rows": rows[phase],
            "huberLoss": math.fsum(group_loss[phase].values())
            / len(group_loss[phase]),
            "cpMae": math.fsum(group_mae[phase].values())
            / len(group_mae[phase]),
        }
    public = {
        "huberLoss": math.fsum(
            phase_metrics[phase]["huberLoss"] for phase in PHASE_ORDER
        )
        / len(PHASE_ORDER),
        "cpMae": math.fsum(
            phase_metrics[phase]["cpMae"] for phase in PHASE_ORDER
        )
        / len(PHASE_ORDER),
        "phase": phase_metrics,
    }
    normalized = {
        "rows": rows,
        "groupLoss": group_loss,
        "groupMae": group_mae,
    }
    return normalized, public


def _validate_statistics(value: Any) -> dict[str, dict[str, Any]]:
    statistics = _mapping(value, "sufficient statistics")
    if set(statistics) != set(STATISTICS_ORDER):
        raise ValueError("sufficient-statistics model inventory changed")
    normalized: dict[str, dict[str, Any]] = {}
    public: dict[str, dict[str, Any]] = {}
    reference_groups: dict[str, set[str]] | None = None
    reference_rows: dict[str, int] | None = None
    for model in STATISTICS_ORDER:
        normalized[model], public[model] = _validate_statistic(
            statistics[model], model
        )
        groups = {
            phase: set(normalized[model]["groupLoss"][phase])
            for phase in PHASE_ORDER
        }
        if reference_groups is None:
            reference_groups = groups
            reference_rows = normalized[model]["rows"]
        elif groups != reference_groups:
            raise ValueError(f"{model} uses a different held-out group inventory")
        elif normalized[model]["rows"] != reference_rows:
            raise ValueError(f"{model} uses different held-out phase row counts")
    return public


def _captured_statistic(value: Any, ordinal: int) -> dict[str, Any]:
    label = f"captured metric call {ordinal}"
    metrics = _mapping(value, label)
    _exact_fields(
        metrics,
        {"huberLoss", "cpMae", "phase", "_groupLoss", "_groupMae"},
        label,
    )
    phases = _mapping(metrics.get("phase"), f"{label} phases")
    loss = _mapping(metrics.get("_groupLoss"), f"{label} group loss")
    mae = _mapping(metrics.get("_groupMae"), f"{label} group MAE")
    for phase_map, phase_label in (
        (phases, "public phases"),
        (loss, "group loss"),
        (mae, "group MAE"),
    ):
        if set(phase_map) != set(PHASE_ORDER):
            raise ValueError(f"{label} {phase_label} inventory changed")

    statistic: dict[str, Any] = {
        "rows": {},
        "groupLoss": {},
        "groupMae": {},
    }
    for phase in PHASE_ORDER:
        phase_public = _mapping(
            phases[phase], f"{label} public phase {phase}"
        )
        _exact_fields(
            phase_public,
            {"groups", "rows", "huberLoss", "cpMae"},
            f"{label} public phase {phase}",
        )
        statistic["rows"][phase] = _positive_integer(
            phase_public["rows"], f"{label} {phase} rows"
        )
        for field, source in (
            ("groupLoss", loss),
            ("groupMae", mae),
        ):
            raw = _mapping(source[phase], f"{label} {phase} {field}")
            statistic[field][phase] = {
                str(group): _number(
                    amount, f"{label} {phase} {field}.{group}"
                )
                for group, amount in sorted(raw.items())
            }

    normalized, recomputed = _validate_statistic(statistic, label)
    _same_number(
        metrics["huberLoss"], recomputed["huberLoss"], f"{label} Huber loss"
    )
    _same_number(metrics["cpMae"], recomputed["cpMae"], f"{label} MAE")
    for phase in PHASE_ORDER:
        for field in ("groups", "rows"):
            sealed._expect(
                phases[phase][field],
                recomputed["phase"][phase][field],
                f"{label} {phase} {field}",
            )
        for field in ("huberLoss", "cpMae"):
            _same_number(
                phases[phase][field],
                recomputed["phase"][phase][field],
                f"{label} {phase} {field}",
            )
    return normalized


def _invoke_offline_once(
    offline: Callable[[Any], dict[str, Any]], args: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    original_group_phase_metrics = sealed._group_phase_metrics
    captured: list[dict[str, Any]] = []

    def retaining_group_phase_metrics(
        labeled: Any, predictions: Any
    ) -> dict[str, Any]:
        result = original_group_phase_metrics(labeled, predictions)
        captured.append(_captured_statistic(result, len(captured) + 1))
        return result

    sealed._group_phase_metrics = retaining_group_phase_metrics
    invocations = 0
    try:
        invocations += 1
        report = offline(args)
    finally:
        sealed._group_phase_metrics = original_group_phase_metrics
    if invocations != 1:
        raise AssertionError("generation-2 offline function invocation count changed")
    if not isinstance(report, dict):
        raise ValueError("generation-2 offline function did not return a report")
    if len(captured) != len(ACTUAL_METRIC_INVOCATION_ORDER):
        raise ValueError(
            "unchanged offline metric call count changed: "
            f"expected {len(ACTUAL_METRIC_INVOCATION_ORDER)}, "
            f"got {len(captured)}"
        )
    return report, captured


def _ordered_statistics(
    captured: Sequence[Mapping[str, Any]], report: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    if len(captured) != len(ACTUAL_METRIC_INVOCATION_ORDER):
        raise ValueError("captured metric inventory is incomplete")
    by_model = {
        model: deepcopy(dict(statistic))
        for model, statistic in zip(ACTUAL_METRIC_INVOCATION_ORDER, captured)
    }
    public = _validate_statistics(by_model)
    report_metrics = _mapping(report.get("metrics"), "offline report metrics")
    if set(report_metrics) != set(STATISTICS_ORDER):
        raise ValueError("offline report metric inventory changed")
    for model in ACTUAL_METRIC_INVOCATION_ORDER:
        actual = _mapping(report_metrics[model], f"offline {model} metrics")
        expected = public[model]
        _exact_fields(
            actual,
            {"huberLoss", "cpMae", "phase"},
            f"offline {model} metrics",
        )
        _same_number(
            actual["huberLoss"],
            expected["huberLoss"],
            f"offline {model} Huber loss",
        )
        _same_number(
            actual["cpMae"], expected["cpMae"], f"offline {model} MAE"
        )
        actual_phases = _mapping(
            actual["phase"], f"offline {model} phases"
        )
        if set(actual_phases) != set(PHASE_ORDER):
            raise ValueError(f"offline {model} phase inventory changed")
        for phase in PHASE_ORDER:
            actual_phase = _mapping(
                actual_phases[phase], f"offline {model} {phase}"
            )
            _exact_fields(
                actual_phase,
                {"groups", "rows", "huberLoss", "cpMae"},
                f"offline {model} {phase}",
            )
            for field in ("groups", "rows"):
                sealed._expect(
                    actual_phase[field],
                    expected["phase"][phase][field],
                    f"offline {model} {phase} {field}",
                )
            for field in ("huberLoss", "cpMae"):
                _same_number(
                    actual_phase[field],
                    expected["phase"][phase][field],
                    f"offline {model} {phase} {field}",
                )
    return {model: by_model[model] for model in STATISTICS_ORDER}


def _ensure_generation2_installed() -> None:
    offline = sealed._offline_test
    if (
        getattr(offline, "__module__", "") == generation2.__name__
        and getattr(offline, "__name__", "") == "generation2_offline_test"
    ):
        return
    if (
        getattr(offline, "__module__", "") != sealed.__name__
        or getattr(offline, "__name__", "") != "_offline_test"
    ):
        raise ValueError("sealed offline implementation was unexpectedly replaced")
    generation2._verify_declarations()
    generation2._install_generation2()
    installed = sealed._offline_test
    if (
        getattr(installed, "__module__", "") != generation2.__name__
        or getattr(installed, "__name__", "") != "generation2_offline_test"
    ):
        raise AssertionError("generation-2 offline implementation was not installed")


def _require_absent(path: Path, label: str) -> None:
    if path.exists():
        raise ValueError(f"{label} already exists; held-out access cannot be repeated")


def _verified_preaccess_authority(
    *,
    readiness_verifier: Callable[[], Mapping[str, Any]] | None = None,
    selection_verifier: Callable[
        [Path],
        tuple[
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
        ],
    ]
    | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    """Authenticate validation and readiness before the one-time target gate.

    The local import avoids the adapter/wrapper import cycle.  Selection is
    recomputed from validation labels and candidate outputs first.  The
    readiness verifier then rehashes and replay-audits the preselection seal
    immediately before this function returns.
    """

    if (readiness_verifier is None) != (selection_verifier is None):
        raise ValueError("preaccess verifier overrides must be supplied together")
    if readiness_verifier is None or selection_verifier is None:
        import king_state_matches_generation2 as match_adapter

        readiness_verifier = lambda: match_adapter._verify_readiness(
            allow_future_run=False
        )
        selection_verifier = match_adapter._verify_selection_exact

    selection_result = selection_verifier(SELECTION_PATH)
    if not isinstance(selection_result, tuple) or len(selection_result) != 4:
        raise ValueError("exact selection verifier returned the wrong context")
    selection, plan, protocol, parsed = (
        _mapping(selection_result[0], "exact validation selection"),
        _mapping(selection_result[1], "exact validation training plan"),
        _mapping(selection_result[2], "exact validation protocol"),
        _mapping(selection_result[3], "exact validation parsed protocol"),
    )
    readiness_seal = _mapping(
        readiness_verifier(),
        "preaccess match-readiness seal",
    )
    return readiness_seal, selection, plan, protocol, parsed


def _verify_gate_identities_unchanged(
    *,
    readiness_identity: Mapping[str, Any],
    selection_identity: Mapping[str, Any],
) -> None:
    _same_pin(
        readiness_identity,
        sealed._identity(READINESS_SEAL_PATH),
        "preaccess match-readiness seal",
    )
    _same_pin(
        selection_identity,
        sealed._identity(SELECTION_PATH),
        "preaccess validation selection",
    )


def _identity_guarded_preaccess_authority(
    authority_verifier: Callable[
        [],
        tuple[
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
        ],
    ] = _verified_preaccess_authority,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    """Pre-capture both gate identities, deeply verify, then rehash."""

    readiness_identity = sealed._identity(READINESS_SEAL_PATH)
    selection_identity = sealed._identity(SELECTION_PATH)
    authority = authority_verifier()
    if not isinstance(authority, tuple) or len(authority) != 5:
        raise ValueError("preaccess authority returned the wrong context")
    _verify_gate_identities_unchanged(
        readiness_identity=readiness_identity,
        selection_identity=selection_identity,
    )
    return readiness_identity, selection_identity, *authority


def _preaccess_context() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    _canonical_path(
        READINESS_SEAL_PATH,
        REPO
        / "build-king-state-v2"
        / "readiness"
        / READINESS_SEAL_PATH.name,
        "match-readiness seal",
    )
    _canonical_path(SELECTION_PATH, OUTPUT_DIR / SELECTION_PATH.name, "selection")
    _canonical_path(REPORT_PATH, OUTPUT_DIR / REPORT_PATH.name, "offline report")
    _canonical_path(CLAIM_PATH, OUTPUT_DIR / CLAIM_PATH.name, "access claim")
    _canonical_path(
        ATTESTATION_PATH,
        OUTPUT_DIR / ATTESTATION_PATH.name,
        "sufficient-statistics attestation",
    )
    for path, label in (
        (REPORT_PATH, "offline report"),
        (CLAIM_PATH, "offline access claim"),
        (ATTESTATION_PATH, "offline sufficient-statistics attestation"),
    ):
        _require_absent(path, label)

    (
        readiness_identity,
        selection_identity,
        readiness_seal,
        selection,
        plan,
        protocol,
        parsed,
    ) = (
        _identity_guarded_preaccess_authority()
    )
    if not readiness_seal:
        raise ValueError("verified match-readiness seal is empty")
    sealed._expect(
        selection.get("generation2"),
        SELECTION_GENERATION2_MARKER,
        "selection generation-2 marker",
    )
    sealed._expect(
        selection.get("offlineTestOutput"),
        str(REPORT_PATH.resolve()),
        "canonical offline report path",
    )
    sealed._expect(
        selection.get("offlineTestAccessClaim"),
        str(CLAIM_PATH.resolve()),
        "canonical offline access-claim path",
    )
    sealed._expect(
        selection.get("testAccessPermittedOnlyAfterThisHashIsRecorded"),
        True,
        "selection one-time access policy",
    )
    corpus_pin = _identity_object(
        _mapping(plan.get("identities"), "training plan identities").get(
            "corpus"
        ),
        "planned corpus",
    )
    corpus = sealed._verify_identity(corpus_pin, "planned corpus")
    _canonical_path(corpus, CORPUS_PATH, "generation-2 corpus")
    if not sealed._same_identity(corpus_pin, sealed._identity(CORPUS_PATH)):
        raise ValueError("generation-2 plan pins a different corpus")
    for path, label in (
        (REPORT_PATH, "offline report"),
        (CLAIM_PATH, "offline access claim"),
        (ATTESTATION_PATH, "offline sufficient-statistics attestation"),
    ):
        _require_absent(path, label)
    if FUTURE_RUN_ROOT.exists():
        raise ValueError(
            "future generation-2 match output exists before held-out access"
        )
    _verify_gate_identities_unchanged(
        readiness_identity=readiness_identity,
        selection_identity=selection_identity,
    )
    return (
        readiness_identity,
        selection_identity,
        readiness_seal,
        selection,
        plan,
        protocol,
        parsed,
    )


def _final_preaccess_guard(
    *,
    readiness_identity: Mapping[str, Any],
    selection_identity: Mapping[str, Any],
) -> None:
    """Rehash the authenticated gate inputs immediately before target access."""

    _verify_gate_identities_unchanged(
        readiness_identity=readiness_identity,
        selection_identity=selection_identity,
    )
    for path, label in (
        (REPORT_PATH, "offline report"),
        (CLAIM_PATH, "offline access claim"),
        (ATTESTATION_PATH, "offline sufficient-statistics attestation"),
    ):
        _require_absent(path, label)
    if FUTURE_RUN_ROOT.exists():
        raise ValueError(
            "future generation-2 match output appeared before held-out access"
        )


def _postaccess_artifacts(
    *,
    plan: Mapping[str, Any],
    selection_identity: Mapping[str, Any],
    returned_report: Mapping[str, Any],
) -> dict[str, Any]:
    report = sealed._load_json(REPORT_PATH, "generation-2 offline report")
    claim = sealed._load_json(CLAIM_PATH, "generation-2 offline access claim")
    sealed._expect(
        report.get("generation2"),
        GENERATION2_MARKER,
        "offline report generation-2 marker",
    )
    for field in (
        "schemaVersion",
        "kind",
        "generationId",
        "selectedCandidateId",
        "metrics",
        "comparisons",
        "passed",
        "fallbackAllowed",
        "heldOutAccessCount",
    ):
        sealed._expect(
            returned_report.get(field),
            report.get(field),
            f"returned/published offline {field}",
        )
    sealed._expect(
        report.get("heldOutAccessCount"), 1, "offline held-out access count"
    )
    sealed._expect(
        report.get("fallbackAllowed"), False, "offline fallback policy"
    )
    _same_pin(
        report.get("selection"),
        selection_identity,
        "offline selection",
    )
    _same_pin(
        report.get("testAccessClaim"),
        sealed._identity(CLAIM_PATH),
        "offline access claim",
    )
    sealed._expect(
        claim.get("heldOutLabelsDecodedBeforeClaim"),
        False,
        "claim prepublication target reads",
    )
    sealed._expect(
        claim.get("secondAccessAllowed"), False, "claim second-access policy"
    )
    sealed._expect(
        claim.get("output"), str(REPORT_PATH.resolve()), "claim output path"
    )
    _same_pin(
        claim.get("selection"),
        selection_identity,
        "claim selection",
    )
    corpus_pin = _identity_object(
        _mapping(plan.get("identities"), "training plan identities").get(
            "corpus"
        ),
        "planned corpus",
    )
    _same_pin(corpus_pin, sealed._identity(CORPUS_PATH), "planned corpus")
    return report


def _network_inventory(
    selection: Mapping[str, Any], report: Mapping[str, Any]
) -> dict[str, Any]:
    candidates = _mapping(selection.get("candidates"), "selection candidates")
    k0 = _mapping(candidates.get("K0"), "selection K0")
    robustness = _mapping(
        selection.get("robustnessCommand"), "selection robustness command"
    )
    networks = {
        "selected": deepcopy(
            _identity_object(selection.get("selectedNetwork"), "selected network")
        ),
        "K0": deepcopy(_identity_object(k0.get("network"), "K0 network")),
        "robustness": sealed._identity(
            Path(str(robustness.get("network"))).resolve(strict=True)
        ),
        "zeroResidual": dict(ZERO_RESIDUAL_DESCRIPTOR),
    }
    for model in ("selected", "K0", "robustness"):
        _verify_pin(networks[model], f"{model} network")
    _same_pin(
        report.get("selectedNetwork"),
        networks["selected"],
        "reported selected network",
    )
    _same_pin(
        report.get("robustnessNetwork"),
        networks["robustness"],
        "reported robustness network",
    )
    return networks


def _attestation(
    *,
    readiness_identity: Mapping[str, Any],
    selection_identity: Mapping[str, Any],
    selection: Mapping[str, Any],
    plan: Mapping[str, Any],
    report: Mapping[str, Any],
    statistics: Mapping[str, Any],
) -> dict[str, Any]:
    corpus_pin = _identity_object(
        _mapping(plan.get("identities"), "training plan identities").get(
            "corpus"
        ),
        "planned corpus",
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": WRAPPER_KIND,
        "createdUtc": sealed._utc_now(),
        "generation2": dict(GENERATION2_MARKER),
        "sources": {
            name: sealed._identity(path)
            for name, path in SOURCE_PATHS.items()
        },
        "matchReadinessSeal": deepcopy(dict(readiness_identity)),
        "selection": deepcopy(dict(selection_identity)),
        "offlineReport": sealed._identity(REPORT_PATH),
        "testAccessClaim": sealed._identity(CLAIM_PATH),
        "corpus": deepcopy(corpus_pin),
        "networks": _network_inventory(selection, report),
        "actualMetricInvocationOrder": list(
            ACTUAL_METRIC_INVOCATION_ORDER
        ),
        "statisticsOrder": list(STATISTICS_ORDER),
        "phaseOrder": list(PHASE_ORDER),
        "statistics": deepcopy(dict(statistics)),
        "access": dict(ACCESS_RECORD),
    }


def _verify_attestation(path: Path) -> dict[str, Any]:
    """Verify a sufficient-statistics attestation without decoding targets."""

    _canonical_path(path, ATTESTATION_PATH, "attestation")
    value = sealed._load_json(path, "offline sufficient-statistics attestation")
    _exact_fields(value, TOP_LEVEL_FIELDS, "attestation")
    sealed._expect(value.get("schemaVersion"), SCHEMA_VERSION, "attestation schema")
    sealed._expect(value.get("kind"), WRAPPER_KIND, "attestation kind")
    _timestamp(value.get("createdUtc"), "attestation createdUtc")
    sealed._expect(
        value.get("generation2"),
        GENERATION2_MARKER,
        "attestation generation-2 marker",
    )

    sources = _mapping(value.get("sources"), "attestation sources")
    if set(sources) != set(SOURCE_PATHS):
        raise ValueError("attestation source inventory changed")
    for name, expected in SOURCE_PATHS.items():
        _verify_pin(sources[name], f"attestation source {name}", expected_path=expected)

    _verify_pin(
        value.get("matchReadinessSeal"),
        "attestation match-readiness seal",
        expected_path=READINESS_SEAL_PATH,
    )
    _verify_pin(
        value.get("selection"),
        "attestation selection",
        expected_path=SELECTION_PATH,
    )
    _verify_pin(
        value.get("offlineReport"),
        "attestation offline report",
        expected_path=REPORT_PATH,
    )
    _verify_pin(
        value.get("testAccessClaim"),
        "attestation access claim",
        expected_path=CLAIM_PATH,
    )
    _verify_pin(
        value.get("corpus"),
        "attestation corpus",
        expected_path=CORPUS_PATH,
    )

    networks = _mapping(value.get("networks"), "attestation networks")
    if set(networks) != set(STATISTICS_ORDER):
        raise ValueError("attestation network inventory changed")
    for model in ("selected", "K0", "robustness"):
        _verify_pin(networks[model], f"attestation {model} network")
    sealed._expect(
        networks.get("zeroResidual"),
        ZERO_RESIDUAL_DESCRIPTOR,
        "attestation zero-residual descriptor",
    )
    sealed._expect(
        _sequence(
            value.get("actualMetricInvocationOrder"),
            "actual metric invocation order",
        ),
        list(ACTUAL_METRIC_INVOCATION_ORDER),
        "actual metric invocation order",
    )
    sealed._expect(
        _sequence(value.get("statisticsOrder"), "statistics order"),
        list(STATISTICS_ORDER),
        "statistics order",
    )
    sealed._expect(
        _sequence(value.get("phaseOrder"), "phase order"),
        list(PHASE_ORDER),
        "phase order",
    )
    _validate_statistics(value.get("statistics"))
    sealed._expect(
        value.get("access"), ACCESS_RECORD, "attestation access record"
    )
    return value


def _run() -> dict[str, Any]:
    (
        readiness_identity,
        selection_identity,
        _readiness_seal,
        selection,
        plan,
        _protocol,
        _parsed,
    ) = _preaccess_context()
    _ensure_generation2_installed()
    _final_preaccess_guard(
        readiness_identity=readiness_identity,
        selection_identity=selection_identity,
    )
    offline = sealed._offline_test
    returned, captured = _invoke_offline_once(
        offline,
        Namespace(selection=SELECTION_PATH, output=REPORT_PATH),
    )
    _verify_gate_identities_unchanged(
        readiness_identity=readiness_identity,
        selection_identity=selection_identity,
    )
    report = _postaccess_artifacts(
        plan=plan,
        selection_identity=selection_identity,
        returned_report=returned,
    )
    statistics = _ordered_statistics(captured, report)
    attestation = _attestation(
        readiness_identity=readiness_identity,
        selection_identity=selection_identity,
        selection=selection,
        plan=plan,
        report=report,
        statistics=statistics,
    )
    # This is deliberately the final operation.  If anything above fails,
    # the attestation does not exist and the already-published access claim
    # prevents any second target access.
    sealed._atomic_json(ATTESTATION_PATH, attestation, no_clobber=True)
    return _verify_attestation(ATTESTATION_PATH)


def _synthetic_statistic(model_index: int) -> dict[str, Any]:
    rows: dict[str, int] = {}
    loss: dict[str, dict[str, float]] = {}
    mae: dict[str, dict[str, float]] = {}
    for phase_index, phase in enumerate(PHASE_ORDER):
        groups = [
            f"global-{phase}",
            "global-shared",
        ]
        rows[phase] = 3
        loss[phase] = {
            group: float(10 * model_index + phase_index + group_index + 1)
            for group_index, group in enumerate(groups)
        }
        mae[phase] = {
            group: float(20 * model_index + phase_index + group_index + 1)
            for group_index, group in enumerate(groups)
        }
    return {"rows": rows, "groupLoss": loss, "groupMae": mae}


def _expanded_synthetic_metric(statistic: Mapping[str, Any]) -> dict[str, Any]:
    normalized, public = _validate_statistic(statistic, "synthetic")
    return {
        **public,
        "_groupLoss": normalized["groupLoss"],
        "_groupMae": normalized["groupMae"],
    }


def _self_test_capture() -> dict[str, dict[str, Any]]:
    statistics = {
        model: _synthetic_statistic(index)
        for index, model in enumerate(STATISTICS_ORDER)
    }
    expanded = {
        model: _expanded_synthetic_metric(statistics[model])
        for model in STATISTICS_ORDER
    }
    fake_invocations = 0
    original = sealed._group_phase_metrics

    def synthetic_group_phase_metrics(model: str, _predictions: Any) -> dict[str, Any]:
        return deepcopy(expanded[model])

    def fake_offline(_args: Any) -> dict[str, Any]:
        nonlocal fake_invocations
        fake_invocations += 1
        metrics: dict[str, Any] = {}
        for model in ACTUAL_METRIC_INVOCATION_ORDER:
            result = sealed._group_phase_metrics(model, None)
            metrics[model] = sealed._public_metrics(result)
        return {"metrics": metrics}

    sealed._group_phase_metrics = synthetic_group_phase_metrics
    try:
        report, captured = _invoke_offline_once(fake_offline, object())
    finally:
        sealed._group_phase_metrics = original
    if fake_invocations != 1:
        raise AssertionError("synthetic offline function was not invoked once")
    ordered = _ordered_statistics(captured, report)
    if ordered != statistics:
        raise AssertionError("captured statistics changed during canonical ordering")

    def wrong_order(_args: Any) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for model in STATISTICS_ORDER:
            result = sealed._group_phase_metrics(model, None)
            metrics[model] = sealed._public_metrics(result)
        return {"metrics": metrics}

    sealed._group_phase_metrics = synthetic_group_phase_metrics
    try:
        wrong_report, wrong_captured = _invoke_offline_once(
            wrong_order, object()
        )
        try:
            _ordered_statistics(wrong_captured, wrong_report)
        except ValueError:
            pass
        else:
            raise AssertionError("wrong frozen metric call order was accepted")
    finally:
        sealed._group_phase_metrics = original

    def incomplete(_args: Any) -> dict[str, Any]:
        result = sealed._group_phase_metrics("selected", None)
        return {"metrics": {"selected": sealed._public_metrics(result)}}

    sealed._group_phase_metrics = synthetic_group_phase_metrics
    try:
        try:
            _invoke_offline_once(incomplete, object())
        except ValueError:
            pass
        else:
            raise AssertionError("incomplete metric capture was accepted")
    finally:
        sealed._group_phase_metrics = original
    return ordered


def _self_test_preaccess_authority() -> None:
    events: list[str] = []
    exact_context = ({}, {}, {}, {})

    def valid_selection(_path: Path) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        events.append("exact-selection")
        return exact_context

    def valid_readiness() -> dict[str, Any]:
        events.append("readiness")
        return {"verified": True}

    result = _verified_preaccess_authority(
        readiness_verifier=valid_readiness,
        selection_verifier=valid_selection,
    )
    if events != ["exact-selection", "readiness"] or result[0] != {
        "verified": True
    }:
        raise AssertionError("preaccess authority verification order changed")

    events.clear()

    def forged_selection(_path: Path) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        events.append("exact-selection")
        raise ValueError("forged validation metrics")

    try:
        _verified_preaccess_authority(
            readiness_verifier=valid_readiness,
            selection_verifier=forged_selection,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("forged selection reached the held-out gate")
    if events != ["exact-selection"]:
        raise AssertionError("forged selection did not fail before readiness/access")

    for label, exception in (
        ("missing readiness", FileNotFoundError("missing readiness")),
        ("tampered readiness", ValueError("tampered readiness")),
    ):
        events.clear()

        def rejected_readiness(
            error: Exception = exception,
        ) -> dict[str, Any]:
            events.append("readiness")
            raise error

        try:
            _verified_preaccess_authority(
                readiness_verifier=rejected_readiness,
                selection_verifier=valid_selection,
            )
        except (FileNotFoundError, ValueError):
            pass
        else:
            raise AssertionError(f"{label} reached the held-out gate")
        if events != ["exact-selection", "readiness"]:
            raise AssertionError(f"{label} verification order changed")


def _self_test_identity_guard() -> None:
    global READINESS_SEAL_PATH, SELECTION_PATH

    original_paths = (READINESS_SEAL_PATH, SELECTION_PATH)
    with tempfile.TemporaryDirectory(
        prefix="omega-king-state-preaccess-identity-self-test-"
    ) as directory:
        root = Path(directory)
        READINESS_SEAL_PATH = root / "readiness.json"
        SELECTION_PATH = root / "selection.json"
        try:
            def reset() -> None:
                READINESS_SEAL_PATH.write_text(
                    '{"readiness":1}\n', encoding="utf-8"
                )
                SELECTION_PATH.write_text(
                    '{"selection":1}\n', encoding="utf-8"
                )

            def authority() -> tuple[
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
            ]:
                return {"verified": True}, {}, {}, {}, {}

            reset()
            guarded = _identity_guarded_preaccess_authority(authority)
            if (
                guarded[0] != sealed._identity(READINESS_SEAL_PATH)
                or guarded[1] != sealed._identity(SELECTION_PATH)
            ):
                raise AssertionError("preaccess identities were not pre-captured")

            for label, target in (
                ("readiness", READINESS_SEAL_PATH),
                ("selection", SELECTION_PATH),
            ):
                reset()

                def mutate_then_return(
                    changed: Path = target,
                ) -> tuple[
                    dict[str, Any],
                    dict[str, Any],
                    dict[str, Any],
                    dict[str, Any],
                    dict[str, Any],
                ]:
                    changed.write_text(
                        '{"replaced":true}\n',
                        encoding="utf-8",
                    )
                    return authority()

                try:
                    _identity_guarded_preaccess_authority(
                        mutate_then_return
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError(
                        f"{label} replacement after verification was accepted"
                    )
        finally:
            READINESS_SEAL_PATH, SELECTION_PATH = original_paths


def _self_test_attestation(statistics: Mapping[str, Any]) -> None:
    global READINESS_SEAL_PATH
    global SELECTION_PATH, REPORT_PATH, CLAIM_PATH, ATTESTATION_PATH, CORPUS_PATH

    original_paths = (
        READINESS_SEAL_PATH,
        SELECTION_PATH,
        REPORT_PATH,
        CLAIM_PATH,
        ATTESTATION_PATH,
        CORPUS_PATH,
    )
    with tempfile.TemporaryDirectory(
        prefix="omega-king-state-offline-sufficient-self-test-"
    ) as directory:
        root = Path(directory)
        READINESS_SEAL_PATH = root / "king-state-v2-match-readiness.seal.json"
        SELECTION_PATH = root / "validation-selection.seal.json"
        REPORT_PATH = root / "offline-test.json"
        CLAIM_PATH = root / "offline-test.json.access.json"
        ATTESTATION_PATH = root / "offline-test.sufficient.json"
        CORPUS_PATH = root / "deep-hce-v2-residual.jsonl"
        selected = root / "selected.nnue"
        k0 = root / "K0.nnue"
        robustness = root / "robustness.nnue"
        try:
            for path, payload in (
                (READINESS_SEAL_PATH, "{}\n"),
                (SELECTION_PATH, "{}\n"),
                (REPORT_PATH, "{}\n"),
                (CLAIM_PATH, "{}\n"),
                (CORPUS_PATH, "synthetic feature corpus; no targets\n"),
                (selected, "selected\n"),
                (k0, "K0\n"),
                (robustness, "robustness\n"),
            ):
                path.write_text(payload, encoding="utf-8", newline="\n")
            attestation = {
                "schemaVersion": SCHEMA_VERSION,
                "kind": WRAPPER_KIND,
                "createdUtc": sealed._utc_now(),
                "generation2": dict(GENERATION2_MARKER),
                "sources": {
                    name: sealed._identity(path)
                    for name, path in SOURCE_PATHS.items()
                },
                "matchReadinessSeal": sealed._identity(
                    READINESS_SEAL_PATH
                ),
                "selection": sealed._identity(SELECTION_PATH),
                "offlineReport": sealed._identity(REPORT_PATH),
                "testAccessClaim": sealed._identity(CLAIM_PATH),
                "corpus": sealed._identity(CORPUS_PATH),
                "networks": {
                    "selected": sealed._identity(selected),
                    "K0": sealed._identity(k0),
                    "robustness": sealed._identity(robustness),
                    "zeroResidual": dict(ZERO_RESIDUAL_DESCRIPTOR),
                },
                "actualMetricInvocationOrder": list(
                    ACTUAL_METRIC_INVOCATION_ORDER
                ),
                "statisticsOrder": list(STATISTICS_ORDER),
                "phaseOrder": list(PHASE_ORDER),
                "statistics": deepcopy(dict(statistics)),
                "access": dict(ACCESS_RECORD),
            }
            sealed._atomic_json(
                ATTESTATION_PATH, attestation, no_clobber=True
            )
            _verify_attestation(ATTESTATION_PATH)

            first_hash = sealed._sha256(ATTESTATION_PATH)
            try:
                sealed._atomic_json(
                    ATTESTATION_PATH, attestation, no_clobber=True
                )
            except ValueError:
                pass
            else:
                raise AssertionError("attestation no-clobber guard failed")
            if sealed._sha256(ATTESTATION_PATH) != first_hash:
                raise AssertionError("failed no-clobber changed attestation")

            tampered = deepcopy(attestation)
            tampered["statistics"]["selected"]["groupLoss"]["opening"][
                "global-opening"
            ] = -1.0
            invalid = root / "invalid.sufficient.json"
            sealed._atomic_json(invalid, tampered, no_clobber=True)
            previous = ATTESTATION_PATH
            ATTESTATION_PATH = invalid
            try:
                try:
                    _verify_attestation(invalid)
                except ValueError:
                    pass
                else:
                    raise AssertionError("negative sufficient statistic was accepted")
            finally:
                ATTESTATION_PATH = previous

            drifted = deepcopy(attestation)
            drifted["actualMetricInvocationOrder"] = list(STATISTICS_ORDER)
            invalid_order = root / "invalid-order.sufficient.json"
            sealed._atomic_json(invalid_order, drifted, no_clobber=True)
            previous = ATTESTATION_PATH
            ATTESTATION_PATH = invalid_order
            try:
                try:
                    _verify_attestation(invalid_order)
                except ValueError:
                    pass
                else:
                    raise AssertionError("wrong invocation order was accepted")
            finally:
                ATTESTATION_PATH = previous
        finally:
            (
                READINESS_SEAL_PATH,
                SELECTION_PATH,
                REPORT_PATH,
                CLAIM_PATH,
                ATTESTATION_PATH,
                CORPUS_PATH,
            ) = original_paths


def _self_test() -> None:
    _self_test_preaccess_authority()
    _self_test_identity_guard()
    statistics = _self_test_capture()
    _self_test_attestation(statistics)
    print("king_state_offline_generation2 self-test passed", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["self-test"]:
        _self_test()
        return 0
    if arguments == ["run"]:
        attestation = _run()
        print(
            "offline sufficient-statistics attestation: "
            + str(ATTESTATION_PATH.resolve()),
            flush=True,
        )
        print(
            "offline gate passed="
            + str(
                sealed._load_json(
                    Path(attestation["offlineReport"]["path"]),
                    "offline report",
                ).get("passed")
            ).lower(),
            flush=True,
        )
        return 0
    raise ValueError("usage: king_state_offline_generation2.py {run|self-test}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
