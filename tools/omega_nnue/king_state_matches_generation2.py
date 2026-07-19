#!/usr/bin/env python3
"""Generation-2 provenance adapter for the sealed king-state match core.

The pre-label-pinned ``king_state_matches.py`` implementation remains the
only selector, scheduler, config builder, seal verifier, and assessor.  This
adapter changes only the three match-suite seeds already preregistered for
generation 2, requires the deep-HCE-v3 provenance/exclusion context, and
publishes an outer seal that pins both layers.

It deliberately cannot create a candidate, inspect training/test targets, or
start OmegaMatch.  The generated configs are still run with the pinned
OmegaMatch assembly after this adapter's ``verify`` command succeeds.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Sequence

import king_state_matches as core
import king_state_match_readiness as readiness
import king_state_offline_generation2 as offline_wrapper
import king_state_train_generation2 as training_v2
import king_state_v2 as prelabel


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v2-deep-hce-v3"
OUTER_SEAL_KIND = "omega-nnue-king-state-v2-match-seal"
INNER_SEAL_NAME = "king-state-v1-matches.seal.json"
OUTER_SEAL_NAME = "king-state-v2-matches.seal.json"

EXPECTED_SEEDS = {
    "development": 2026072001,
    "equal-node": 2026072002,
    "equal-time": 2026072003,
}

SELECTION_GENERATION2_MARKER = {
    "protocolGeneration": 2,
    "dataProfile": "deep-hce-v3",
    "healthMetricSubject": "deployment-equivalent float checkpoint",
    "heldOutTargetsDecoded": False,
}

OFFLINE_GENERATION2_MARKER = {
    "protocolGeneration": 2,
    "dataProfile": "deep-hce-v3",
    "healthMetricSubject": "deployment-equivalent float checkpoint",
}

OUTER_CONTRACTS = {
    "onlySeedAndProfileSubstituted": True,
    "allThreeSuitesSealedTogether": True,
    "candidateManifestClosedTrainingInputsEnforcedByCore": True,
    "sourceAndPlanIdentityDriftFailsClosed": True,
    "everyRavFreePgnMainlineAndCcsfReplayedBeforeSelection": True,
    "recursiveAnnotationVariationsRejectedBeforeReplay": True,
    "historyReplaySnapshotFedToCoreForbiddenSelection": True,
    "validationSelectionRecomputedFromLabelsAndNetworks": True,
    "validationSelectionRecomputedBeforeHeldOutAccess": True,
    "matchReadinessVerifiedBeforeHeldOutAccess": True,
    "preaccessGateIdentitiesCapturedBeforeDeepVerification": True,
    "preaccessGateIdentityDriftFailsBeforeHeldOutAccess": True,
    "offlineAccessClaimCanonicalAndSingleUse": True,
    "offlineMetricsAndBootstrapRecomputedFromAttestation": True,
    "unattestedDirectOfflineTestRejected": True,
    "futureRunRootBoundAndAbsentThroughSealPublication": True,
    "outerSealDescriptorHeldThroughVerification": True,
    "failedOuterSealRetainedForDiagnosisAndBlocksRetry": True,
}

OUTER_SEAL_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "protocolGeneration",
    "dataProfile",
    "seeds",
    "matchOutputRoot",
    "adapter",
    "adapterContract",
    "compatibilityCore",
    "compatibilityProtocol",
    "generation2Preregistration",
    "generation2Amendment",
    "activePrelabelSeal",
    "activePrelabelVerification",
    "matchReadinessSeal",
    "historyReplaySnapshot",
    "historyReplayManifest",
    "offlineSufficientWrapper",
    "offlineSufficientAttestation",
    "trainingPlan",
    "validationSelection",
    "offlineReport",
    "offlineAccessClaim",
    "matchReadinessAudit",
    "innerCoreSeal",
    "explicitExclusionInventory",
    "contracts",
}

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO.parent
CONTRACT_PATH = (
    REPO / "validation" / "omega-nnue-king-state-v2-match-adapter.json"
)
COMPATIBILITY_PROTOCOL = (
    REPO / "validation" / "omega-nnue-king-state-v1-protocol.json"
)
V2_PREREGISTRATION = (
    REPO / "validation" / "omega-nnue-king-state-v2-preregistration.json"
)
V2_AMENDMENT = (
    REPO / "validation" / "omega-nnue-king-state-v2-amendment.json"
)
ACTIVE_PRELABEL_SEAL = (
    REPO
    / "build-msvc"
    / "data-generation"
    / "deep-hce-v3"
    / "king-state-v1-prelabel.seal.json"
)
FRESH_CORPUS = (
    REPO
    / "build-msvc"
    / "data-generation"
    / "deep-hce-v3"
    / "deep-hce-v2-residual.jsonl"
)
GENERATION2_OUTPUT_DIR = REPO / "build-msvc" / "king-state-v2"
TRAINING_PLAN = GENERATION2_OUTPUT_DIR / "training-plan.json"
VALIDATION_SELECTION = (
    GENERATION2_OUTPUT_DIR / "validation-selection.seal.json"
)
OFFLINE_REPORT = GENERATION2_OUTPUT_DIR / "offline-test.json"
OFFLINE_ACCESS_CLAIM = (
    GENERATION2_OUTPUT_DIR / "offline-test.json.access.json"
)
OFFLINE_SUFFICIENT = (
    GENERATION2_OUTPUT_DIR / "offline-test.sufficient.json"
)
OFFLINE_WRAPPER = Path(__file__).with_name(
    "king_state_offline_generation2.py"
)

MANDATORY_EXCLUSIONS = (
    REPO / "build-msvc",
    REPO / "validation",
    WORKSPACE / "match-runs" / "configs",
    WORKSPACE / "match-runs" / "output",
    WORKSPACE / "omega-lab" / "regressions",
)

_ORIGINAL_SPECS = copy.deepcopy(core.GATE_SPECS)
_ORIGINAL_VALIDATE_PROTOCOL = core._validate_protocol
_TRAINING_V2_INSTALLED = False


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    path = _resolve(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object: {path}")
    return value


def _expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label} changed: expected {expected!r}, got {actual!r}")


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        return (
            _resolve(Path(str(left["path"])))
            == _resolve(Path(str(right["path"])))
            and int(left["bytes"]) == int(right["bytes"])
            and str(left["sha256"]).lower() == str(right["sha256"]).lower()
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _contract_identity(
    record: Mapping[str, Any], expected_path: Path, label: str
) -> dict[str, Any]:
    expected_path = _resolve(expected_path)
    declared_path = _resolve(REPO / str(record.get("path", "")))
    if declared_path != expected_path:
        raise ValueError(f"{label} path changed: {declared_path}")
    actual = core._identity(expected_path)
    if (
        int(record.get("bytes", -1)) != actual["bytes"]
        or str(record.get("sha256", "")).lower() != actual["sha256"]
    ):
        raise ValueError(f"{label} identity drift")
    return actual


def _validate_seed_profile(
    preregistration: Mapping[str, Any],
    amendment: Mapping[str, Any],
    seeds: Mapping[str, int] = EXPECTED_SEEDS,
) -> None:
    _expect(
        preregistration.get("kind"),
        "omega-nnue-king-state-v2-preregistration",
        "generation-2 preregistration kind",
    )
    _expect(preregistration.get("profileId"), PROFILE_ID, "generation-2 profile")
    _expect(preregistration.get("protocolGeneration"), 2, "protocol generation")
    _expect(
        preregistration.get("dataProfile"), "deep-hce-v3", "generation-2 data profile"
    )
    fresh = preregistration.get("freshMatchSuites")
    if not isinstance(fresh, dict):
        raise ValueError("generation-2 preregistration lacks freshMatchSuites")
    for key, gate in (
        ("developmentScreenSeed", "development"),
        ("equalNodeConfirmationSeed", "equal-node"),
        ("equalTimeConfirmationSeed", "equal-time"),
    ):
        _expect(fresh.get(key), seeds[gate], f"preregistered {gate} seed")
    _expect(fresh.get("configSeedEqualsSuiteSeed"), True, "suite/config seed policy")
    _expect(
        fresh.get("orbitDisjointFromBothTrainingGenerationsAndPriorMatches"),
        True,
        "generation-2 orbit policy",
    )

    _expect(
        amendment.get("kind"),
        "omega-nnue-king-state-v2-protocol-amendment",
        "generation-2 amendment kind",
    )
    policy = amendment.get("generation2DataPolicy")
    if not isinstance(policy, dict):
        raise ValueError("generation-2 amendment lacks data policy")
    _expect(
        policy.get("trainingCorpusMayReuseGeneration1Rows"),
        False,
        "generation-1 row reuse policy",
    )
    match_seeds = policy.get("freshMatchSuiteSeeds")
    if not isinstance(match_seeds, dict):
        raise ValueError("generation-2 amendment lacks fresh match seeds")
    for key, gate in (
        ("developmentScreen", "development"),
        ("equalNodeConfirmation", "equal-node"),
        ("equalTimeConfirmation", "equal-time"),
    ):
        _expect(match_seeds.get(key), seeds[gate], f"amended {gate} seed")
    _expect(
        match_seeds.get("configSeedEqualsSuiteSeed"),
        True,
        "amended suite/config seed policy",
    )


def _validate_static_contract() -> dict[str, dict[str, Any]]:
    contract = _load_object(CONTRACT_PATH, "generation-2 match adapter contract")
    _expect(contract.get("schemaVersion"), SCHEMA_VERSION, "adapter contract schema")
    _expect(
        contract.get("kind"),
        "omega-nnue-king-state-v2-match-adapter-contract",
        "adapter contract kind",
    )
    _expect(contract.get("profileId"), PROFILE_ID, "adapter contract profile")

    compatibility = contract.get("compatibilityCore")
    declarations = contract.get("declarations")
    substitution = contract.get("permittedSubstitution")
    if not all(isinstance(value, dict) for value in (compatibility, declarations, substitution)):
        raise ValueError("adapter contract is structurally incomplete")
    identities = {
        "adapterContract": core._identity(CONTRACT_PATH),
        "compatibilityCore": _contract_identity(
            compatibility, Path(core.__file__), "compatibility match core"
        ),
        "compatibilityProtocol": _contract_identity(
            declarations["compatibilityProtocol"],
            COMPATIBILITY_PROTOCOL,
            "compatibility protocol",
        ),
        "generation2Preregistration": _contract_identity(
            declarations["generation2Preregistration"],
            V2_PREREGISTRATION,
            "generation-2 preregistration",
        ),
        "generation2Amendment": _contract_identity(
            declarations["generation2Amendment"],
            V2_AMENDMENT,
            "generation-2 amendment",
        ),
    }

    _expect(substitution.get("protocolGeneration"), 2, "adapter protocol generation")
    _expect(substitution.get("dataProfile"), "deep-hce-v3", "adapter data profile")
    _expect(substitution.get("seeds"), EXPECTED_SEEDS, "adapter seed inventory")
    _expect(
        _resolve(REPO / str(contract.get("freshTrainingCorpus", ""))),
        _resolve(FRESH_CORPUS),
        "fresh training corpus",
    )
    _expect(
        tuple(
            _resolve(REPO / str(item))
            for item in contract.get("requiredExplicitExclusionRoots", [])
        ),
        tuple(_resolve(path) for path in MANDATORY_EXCLUSIONS),
        "mandatory exclusion inventory",
    )

    preregistration = _load_object(
        V2_PREREGISTRATION, "generation-2 preregistration"
    )
    amendment = _load_object(V2_AMENDMENT, "generation-2 amendment")
    _validate_seed_profile(preregistration, amendment)
    _ORIGINAL_VALIDATE_PROTOCOL(COMPATIBILITY_PROTOCOL)

    for gate, seed in EXPECTED_SEEDS.items():
        original = _ORIGINAL_SPECS.get(gate)
        if not isinstance(original, dict):
            raise ValueError(f"compatibility core lacks gate {gate}")
        adapted = dict(original)
        adapted["seed"] = seed
        for key, value in original.items():
            if key != "seed" and adapted.get(key) != value:
                raise AssertionError(f"unexpected core degree changed: {gate}.{key}")
    return identities


def _verify_active_prelabel(
    identities: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    report = prelabel._verify_seal(ACTIVE_PRELABEL_SEAL)
    seal = _load_object(ACTIVE_PRELABEL_SEAL, "active generation-2 prelabel seal")
    pinned = seal.get("identities")
    if not isinstance(pinned, dict):
        raise ValueError("active prelabel seal lacks identities")
    tooling = pinned.get("tooling")
    if not isinstance(tooling, dict):
        raise ValueError("active prelabel seal lacks tooling identities")
    match_core = tooling.get("kingStateMatches")
    if not isinstance(match_core, dict) or not _same_identity(
        match_core, identities["compatibilityCore"]
    ):
        raise ValueError("active prelabel seal pins a different match core")
    for seal_key, identity_key in (
        ("protocol", "compatibilityProtocol"),
        ("v2Preregistration", "generation2Preregistration"),
        ("generation2Amendment", "generation2Amendment"),
    ):
        record = pinned.get(seal_key)
        if not isinstance(record, dict) or not _same_identity(
            record, identities[identity_key]
        ):
            raise ValueError(f"active prelabel seal pins a different {seal_key}")
    return report


def _install_generation2_profile() -> None:
    specs = copy.deepcopy(_ORIGINAL_SPECS)
    if set(specs) != set(EXPECTED_SEEDS):
        raise ValueError("compatibility gate inventory changed")
    for gate, seed in EXPECTED_SEEDS.items():
        specs[gate]["seed"] = seed
    core.GATE_SPECS = specs

    def validate_compatibility(path: Path) -> dict[str, Any]:
        if _resolve(path) != _resolve(COMPATIBILITY_PROTOCOL):
            raise ValueError("generation-2 adapter requires the pinned compatibility protocol")
        _validate_static_contract()
        return _ORIGINAL_VALIDATE_PROTOCOL(COMPATIBILITY_PROTOCOL)

    core._validate_protocol = validate_compatibility


def _require_explicit_exclusions(paths: Sequence[Path]) -> list[Path]:
    resolved = [_resolve(path) for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("--exclude contains a duplicate resolved path")
    missing = [
        path
        for path in MANDATORY_EXCLUSIONS
        if _resolve(path) not in set(resolved)
    ]
    if missing:
        raise ValueError(
            "missing mandatory explicit --exclude root(s): "
            + ", ".join(str(_resolve(path)) for path in missing)
        )
    absent = [path for path in resolved if not path.exists()]
    if absent:
        raise FileNotFoundError(f"exclusion root does not exist: {absent[0]}")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        _resolve(path).relative_to(_resolve(root))
        return True
    except ValueError:
        return False


def _coverage_inventory(
    roots: Sequence[Path], output_dir: Path
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for root in roots:
        root = _resolve(root)
        if root.is_file() and root.suffix.lower() not in {".json", ".jsonl"}:
            raise ValueError(
                "explicit exclusions must be OFEN-bearing JSON/JSONL, not "
                f"{root.suffix or 'an extensionless file'}: {root}"
            )
        files = core._expand_json_files([root], output_dir)
        position_files = 0
        identities: list[dict[str, Any]] = []
        for path in files:
            identities.append(core._identity(path))
            try:
                next(core._file_ofens(path, strict=False))
            except StopIteration:
                continue
            position_files += 1
        inventory.append(
            {
                "root": str(root),
                "jsonFiles": len(files),
                "positionBearingJsonFiles": position_files,
                "files": identities,
            }
        )
    return inventory


def _check_fresh_layout(sampler_dir: Path, output_dir: Path) -> None:
    sampler_dir = _resolve(sampler_dir)
    output_dir = _resolve(output_dir)
    for path, label in ((sampler_dir, "sampler"), (output_dir, "match seal")):
        for excluded in MANDATORY_EXCLUSIONS:
            if _is_within(path, excluded):
                raise ValueError(
                    f"{label} directory must be outside mandatory history: {path}"
                )
    if _is_within(sampler_dir, output_dir) or _is_within(output_dir, sampler_dir):
        raise ValueError("sampler and match-seal directories must be disjoint")


def _verify_readiness(
    *, allow_future_run: bool = True
) -> dict[str, Any]:
    return readiness._verify(
        readiness.SEAL_PATH,
        allow_future_run=allow_future_run,
    )


def _canonical_match_output_root(path: Path | None) -> Path:
    if path is None:
        raise ValueError(
            f"--match-output-root must be {_resolve(readiness.FUTURE_RUN_ROOT)}"
        )
    resolved = _resolve(path)
    _expect(
        resolved,
        _resolve(readiness.FUTURE_RUN_ROOT),
        "generation-2 match output root",
    )
    return resolved


def _require_future_run_absent(label: str) -> None:
    if _resolve(readiness.FUTURE_RUN_ROOT).exists():
        raise FileExistsError(
            f"future generation-2 match output exists {label}: "
            f"{_resolve(readiness.FUTURE_RUN_ROOT)}"
        )


def _install_training_generation2() -> None:
    global _TRAINING_V2_INSTALLED
    if not _TRAINING_V2_INSTALLED:
        training_v2._install_generation2()
        _TRAINING_V2_INSTALLED = True


def _verify_selection_exact(
    selection_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    _install_training_generation2()
    sealed = training_v2.sealed
    selection_path = _resolve(selection_path)
    _expect(
        selection_path,
        _resolve(VALIDATION_SELECTION),
        "generation-2 validation selection path",
    )
    selection, plan, protocol, parsed = sealed._verify_selection(
        selection_path
    )
    expected_fields = {
        "schemaVersion",
        "kind",
        "createdUtc",
        "generationId",
        "plan",
        "protocol",
        "prelabelSeal",
        "corpus",
        "orchestrator",
        "informationBoundary",
        "validationBalance",
        "candidates",
        "decision",
        "selectedCandidateId",
        "selectedNetwork",
        "selectedManifest",
        "robustnessCommand",
        "offlineTestOutput",
        "offlineTestAccessClaim",
        "testAccessPermittedOnlyAfterThisHashIsRecorded",
        "generation2",
    }
    _expect(
        set(selection),
        expected_fields,
        "validation selection field inventory",
    )
    if not isinstance(selection.get("createdUtc"), str) or not selection["createdUtc"]:
        raise ValueError("validation selection lacks a creation timestamp")
    _generation2_marker(
        selection.get("generation2"),
        SELECTION_GENERATION2_MARKER,
        "validation selection",
    )

    corpus_path = Path(str(plan["identities"]["corpus"]["path"]))
    feature_corpus = sealed._feature_corpus(
        corpus_path,
        split_seed=int(parsed["split"]["splitSeed"]),
        train_percent=float(parsed["split"]["trainPercent"]),
        validation_percent=float(parsed["split"]["validationPercent"]),
    )
    validation = sealed._load_labeled_split(
        corpus_path,
        feature_corpus,
        wanted_split=1,
        target_clip=float(parsed["common"]["targetCpClip"]),
    )
    candidate_values: dict[str, dict[str, Any]] = {}
    k0_dead: int | None = None
    for candidate_id in ("K0", "K1", "K2"):
        manifest, network_path, manifest_path, float_path = (
            sealed._verify_candidate(plan, candidate_id)
        )
        health, predictions = sealed._runtime_health(
            candidate_id=candidate_id,
            network_path=network_path,
            float_path=float_path,
            helper_path=Path(
                str(plan["identities"]["staticHceEvaluator"]["path"])
            ),
            corpus=feature_corpus,
            runtime_protocol=parsed["runtime"],
            batch_size=int(parsed["common"]["batchSize"]),
            k0_dead_units=k0_dead,
        )
        if candidate_id == "K0":
            k0_dead = int(health["deadDenseUnits"])
            migration = sealed._mapping(
                manifest.get("migrationParity"), "K0 migration"
            )
            _expect(
                migration.get("positionsChecked"),
                feature_corpus.count,
                "K0 all-corpus parity count",
            )
        candidate_values[candidate_id] = {
            "network": sealed._identity(network_path),
            "manifest": sealed._identity(manifest_path),
            "floatCheckpoint": (
                None if float_path is None else sealed._identity(float_path)
            ),
            "validation": sealed._row_metrics(validation, predictions),
            "health": health,
        }
    _expect(
        candidate_values["K0"]["health"]["passed"],
        True,
        "K0 frozen runtime health",
    )
    winner, decision = sealed._choose_candidate(
        candidate_values,
        parsed["selection"],
    )
    selected = candidate_values[winner]
    robustness_argv = sealed._trainer_arguments(
        candidate_id=winner,
        protocol=protocol,
        parsed=parsed,
        context=plan["identities"],
        corpus=corpus_path,
        corpus_manifest=Path(
            str(plan["identities"]["corpusManifest"]["path"])
        ),
        initializer=Path(str(plan["identities"]["initializer"]["path"])),
        evaluator=Path(
            str(plan["identities"]["staticHceEvaluator"]["path"])
        ),
        output_dir=Path(str(plan["commands"][winner]["network"])).parent,
        seed=int(parsed["seeds"]["robustness"]),
        robustness=True,
    )
    expected_values = {
        "schemaVersion": sealed.SCHEMA_VERSION,
        "kind": sealed.SELECTION_KIND,
        "generationId": protocol["generationId"],
        "plan": sealed._identity(TRAINING_PLAN),
        "protocol": plan["identities"]["protocol"],
        "prelabelSeal": plan["identities"]["prelabelSeal"],
        "corpus": plan["identities"]["corpus"],
        "orchestrator": sealed._identity(Path(training_v2.__file__)),
        "informationBoundary": {
            "trainLabelsDecoded": False,
            "validationLabelsDecoded": True,
            "heldOutLabelsDecoded": False,
            "heldOutMetricsRead": False,
            "heldOutMetricsEmitted": False,
            "featureOnlyWholeCorpusHealthAllowed": True,
        },
        "validationBalance": sealed._balance_audit(
            feature_corpus,
            1,
            parsed["split"],
        ),
        "candidates": candidate_values,
        "decision": decision,
        "selectedCandidateId": winner,
        "selectedNetwork": selected["network"],
        "selectedManifest": selected["manifest"],
        "robustnessCommand": sealed._command_record(
            winner,
            robustness_argv,
        ),
        "offlineTestOutput": str(_resolve(OFFLINE_REPORT)),
        "offlineTestAccessClaim": str(_resolve(OFFLINE_ACCESS_CLAIM)),
        "testAccessPermittedOnlyAfterThisHashIsRecorded": True,
        "generation2": dict(SELECTION_GENERATION2_MARKER),
    }
    for key, expected in expected_values.items():
        _expect(
            selection.get(key),
            expected,
            f"recomputed validation selection {key}",
        )
    return selection, plan, protocol, parsed


def _number(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{label} is not a finite number")
    return float(value)


def _metrics_from_sufficient_statistics(
    statistics: Any,
    test_balance: Mapping[str, Any],
    *,
    group_order: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    sealed = training_v2.sealed
    statistics = sealed._mapping(
        statistics,
        "offline sufficient statistics",
    )
    model_order = ("selected", "K0", "robustness", "zeroResidual")
    _expect(set(statistics), set(model_order), "sufficient-statistics models")
    phase_counts = sealed._mapping(
        test_balance.get("phaseCounts"),
        "offline test phase counts",
    )
    _expect(
        set(phase_counts),
        set(sealed.PHASES),
        "offline test phase-count inventory",
    )
    if group_order is not None:
        _expect(
            set(group_order),
            set(sealed.PHASES),
            "offline test group-order phase inventory",
        )
    reference_groups: dict[str, set[str]] = {}
    result: dict[str, dict[str, Any]] = {}
    for model in model_order:
        record = sealed._mapping(
            statistics.get(model),
            f"sufficient statistics {model}",
        )
        _expect(
            set(record),
            {"rows", "groupLoss", "groupMae"},
            f"{model} sufficient-statistics fields",
        )
        rows = sealed._mapping(record.get("rows"), f"{model} rows")
        loss_phases = sealed._mapping(
            record.get("groupLoss"), f"{model} group loss"
        )
        mae_phases = sealed._mapping(
            record.get("groupMae"), f"{model} group MAE"
        )
        for label, value in (
            ("rows", rows),
            ("group loss", loss_phases),
            ("group MAE", mae_phases),
        ):
            _expect(
                set(value),
                set(sealed.PHASES),
                f"{model} {label} phase inventory",
            )
        phase_values: dict[str, dict[str, Any]] = {}
        private_loss: dict[str, dict[str, float]] = {}
        private_mae: dict[str, dict[str, float]] = {}
        for phase in sealed.PHASES:
            row_count = rows[phase]
            if type(row_count) is not int or row_count <= 0:
                raise ValueError(f"{model} {phase} row count is invalid")
            _expect(
                row_count,
                phase_counts[phase],
                f"{model} {phase} attested row count",
            )
            losses = sealed._mapping(
                loss_phases[phase],
                f"{model} {phase} group loss",
            )
            maes = sealed._mapping(
                mae_phases[phase],
                f"{model} {phase} group MAE",
            )
            if not losses or set(losses) != set(maes):
                raise ValueError(
                    f"{model} {phase} loss/MAE group inventory changed"
                )
            if list(losses) != sorted(losses) or list(maes) != sorted(maes):
                raise ValueError(
                    f"{model} {phase} group statistics are not canonical"
                )
            if model == model_order[0]:
                reference_groups[phase] = set(losses)
            elif set(losses) != reference_groups[phase]:
                raise ValueError(
                    f"{model} {phase} group inventory differs across models"
                )
            ordered_groups = (
                list(losses)
                if group_order is None
                else list(group_order[phase])
            )
            if (
                len(ordered_groups) != len(set(ordered_groups))
                or set(ordered_groups) != set(losses)
            ):
                raise ValueError(
                    f"{model} {phase} target-opaque group order changed"
                )
            private_loss[phase] = {}
            private_mae[phase] = {}
            for group in ordered_groups:
                if not isinstance(group, str) or not group:
                    raise ValueError(f"{model} {phase} has an invalid group id")
                loss = _number(losses[group], f"{model} {phase} {group} loss")
                mae = _number(maes[group], f"{model} {phase} {group} MAE")
                if loss < 0.0 or mae < 0.0:
                    raise ValueError(
                        f"{model} {phase} {group} has negative statistics"
                    )
                private_loss[phase][group] = loss
                private_mae[phase][group] = mae
            phase_values[phase] = {
                "groups": len(private_loss[phase]),
                "rows": row_count,
                "huberLoss": float(
                    sealed.np.mean(list(private_loss[phase].values()))
                ),
                "cpMae": float(
                    sealed.np.mean(list(private_mae[phase].values()))
                ),
            }
        result[model] = {
            "huberLoss": float(
                sealed.np.mean(
                    [
                        phase_values[phase]["huberLoss"]
                        for phase in sealed.PHASES
                    ]
                )
            ),
            "cpMae": float(
                sealed.np.mean(
                    [
                        phase_values[phase]["cpMae"]
                        for phase in sealed.PHASES
                    ]
                )
            ),
            "phase": phase_values,
            "_groupLoss": private_loss,
            "_groupMae": private_mae,
        }
    return result


def _heldout_group_order(feature_corpus: Any) -> dict[str, list[str]]:
    sealed = training_v2.sealed
    result = {phase: [] for phase in sealed.PHASES}
    seen = {phase: set() for phase in sealed.PHASES}
    for index, split in enumerate(feature_corpus.splits):
        if int(split) != 2:
            continue
        phase = feature_corpus.phases[index]
        group = feature_corpus.groups[index]
        if group not in seen[phase]:
            seen[phase].add(group)
            result[phase].append(group)
    if any(not groups for groups in result.values()):
        raise ValueError("target-opaque held-out group order lacks a phase")
    return result


def _verify_public_metrics(
    reported_value: Any,
    recomputed: Mapping[str, Mapping[str, Any]],
) -> None:
    sealed = training_v2.sealed
    reported = sealed._mapping(reported_value, "offline metrics")
    _expect(
        set(reported),
        set(recomputed),
        "offline metric identities",
    )
    for model, expected in recomputed.items():
        metric = sealed._mapping(
            reported.get(model),
            f"offline metric {model}",
        )
        _expect(
            set(metric),
            {"huberLoss", "cpMae", "phase"},
            f"{model} metric field inventory",
        )
        _expect(
            metric.get("huberLoss"),
            float(expected["huberLoss"]),
            f"{model} recomputed Huber loss",
        )
        _expect(
            metric.get("cpMae"),
            float(expected["cpMae"]),
            f"{model} recomputed MAE",
        )
        phases = sealed._mapping(metric.get("phase"), f"{model} phases")
        _expect(set(phases), set(sealed.PHASES), f"{model} phase inventory")
        for phase in sealed.PHASES:
            cell = sealed._mapping(phases[phase], f"{model} {phase}")
            _expect(
                set(cell),
                {"groups", "rows", "huberLoss", "cpMae"},
                f"{model} {phase} metric field inventory",
            )
            expected_cell = expected["phase"][phase]
            _expect(
                cell.get("groups"),
                expected_cell["groups"],
                f"{model} {phase} group count",
            )
            _expect(
                cell.get("rows"),
                expected_cell["rows"],
                f"{model} {phase} row count",
            )
            _expect(
                cell.get("huberLoss"),
                float(expected_cell["huberLoss"]),
                f"{model} {phase} recomputed Huber loss",
            )
            _expect(
                cell.get("cpMae"),
                float(expected_cell["cpMae"]),
                f"{model} {phase} recomputed MAE",
            )


def _verify_sufficient_attestation(
    *,
    selection_path: Path,
    offline_path: Path,
    claim_path: Path,
    plan: Mapping[str, Any],
    selection: Mapping[str, Any],
    robustness_network: Path,
) -> dict[str, Any]:
    sealed = training_v2.sealed
    _expect(
        _resolve(OFFLINE_SUFFICIENT),
        _resolve(offline_wrapper.ATTESTATION_PATH),
        "offline sufficient attestation path",
    )
    attestation = offline_wrapper._verify_attestation(OFFLINE_SUFFICIENT)
    expected_pins = {
        "matchReadinessSeal": sealed._identity(readiness.SEAL_PATH),
        "selection": sealed._identity(selection_path),
        "offlineReport": sealed._identity(offline_path),
        "testAccessClaim": sealed._identity(claim_path),
        "corpus": sealed._mapping(
            plan["identities"].get("corpus"),
            "planned corpus identity",
        ),
    }
    for key, expected in expected_pins.items():
        _expect(
            attestation.get(key),
            expected,
            f"offline attestation {key}",
        )
    sources = sealed._mapping(
        attestation.get("sources"),
        "offline attestation sources",
    )
    _expect(
        sources.get("wrapper"),
        sealed._identity(OFFLINE_WRAPPER),
        "offline attestation wrapper",
    )
    candidates = sealed._mapping(
        selection.get("candidates"),
        "selection candidates",
    )
    k0 = sealed._mapping(candidates.get("K0"), "selection K0")
    networks = sealed._mapping(
        attestation.get("networks"),
        "offline attestation networks",
    )
    _expect(
        networks.get("selected"),
        selection["selectedNetwork"],
        "offline attestation selected network",
    )
    _expect(
        networks.get("K0"),
        k0["network"],
        "offline attestation K0 network",
    )
    _expect(
        networks.get("robustness"),
        sealed._identity(robustness_network),
        "offline attestation robustness network",
    )
    _expect(
        networks.get("zeroResidual"),
        offline_wrapper.ZERO_RESIDUAL_DESCRIPTOR,
        "offline attestation zero residual",
    )
    return attestation


def _canonical_offline_claim_path(
    selection: Mapping[str, Any],
) -> Path:
    path = _resolve(Path(str(selection.get("offlineTestAccessClaim", ""))))
    _expect(
        path,
        _resolve(OFFLINE_ACCESS_CLAIM),
        "canonical offline access claim path",
    )
    return path


def _generation2_marker(
    value: Any, expected: Mapping[str, Any], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} lacks generation2 provenance")
    _expect(value, dict(expected), f"{label} generation2 provenance")
    return value


def _verify_offline_report_exact(
    selection_path: Path, offline_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    _install_training_generation2()
    sealed = training_v2.sealed
    selection, plan, protocol, parsed = _verify_selection_exact(
        selection_path
    )
    report = sealed._load_json(offline_path, "generation-2 offline report")
    expected_fields = {
        "schemaVersion",
        "kind",
        "createdUtc",
        "generationId",
        "testAccessClaim",
        "selection",
        "selectedCandidateId",
        "selectedNetwork",
        "robustnessNetwork",
        "robustnessManifest",
        "robustnessFloatCheckpoint",
        "robustnessHealth",
        "testBalance",
        "targetAndLoss",
        "aggregation",
        "metrics",
        "comparisons",
        "robustnessDirectionalImprovementOverK0",
        "robustnessDirectionalImprovementOverZeroResidual",
        "passed",
        "fallbackAllowed",
        "heldOutAccessCount",
        "generation2",
    }
    _expect(set(report), expected_fields, "offline report field inventory")
    _expect(report.get("schemaVersion"), sealed.SCHEMA_VERSION, "offline schema")
    _expect(report.get("kind"), sealed.TEST_REPORT_KIND, "offline kind")
    _expect(report.get("generationId"), protocol["generationId"], "offline generation")
    _generation2_marker(
        report.get("generation2"),
        OFFLINE_GENERATION2_MARKER,
        "offline report",
    )
    _expect(
        _resolve(offline_path),
        _resolve(Path(str(selection["offlineTestOutput"]))),
        "offline output path",
    )
    _expect(
        report.get("selection"),
        sealed._identity(selection_path),
        "offline selection identity",
    )
    winner = str(selection["selectedCandidateId"])
    _expect(report.get("selectedCandidateId"), winner, "offline winner")
    _expect(
        report.get("selectedNetwork"),
        selection.get("selectedNetwork"),
        "offline selected network",
    )

    command = sealed._mapping(
        selection.get("robustnessCommand"), "selection robustness command"
    )
    robustness_network = Path(str(command["network"])).resolve(strict=True)
    robustness_manifest_path = Path(str(command["manifest"])).resolve(strict=True)
    robustness_float = Path(str(command["floatCheckpoint"])).resolve(strict=True)
    expected_robustness = {
        "robustnessNetwork": sealed._identity(robustness_network),
        "robustnessManifest": sealed._identity(robustness_manifest_path),
        "robustnessFloatCheckpoint": sealed._identity(robustness_float),
    }
    for key, identity in expected_robustness.items():
        _expect(report.get(key), identity, f"offline {key}")
    robustness_manifest = sealed._load_json(
        robustness_manifest_path, "generation-2 robustness manifest"
    )
    training_v2._verify_canonical_manifest(
        plan=plan,
        candidate_id=winner,
        manifest=robustness_manifest,
        network_path=robustness_network,
        float_path=robustness_float,
        command_override=command,
    )

    claim_path = sealed._verify_identity(
        sealed._mapping(report.get("testAccessClaim"), "offline access claim"),
        "offline access claim",
    )
    expected_claim_path = _canonical_offline_claim_path(selection)
    _expect(
        _resolve(claim_path),
        expected_claim_path,
        "reported offline access claim path",
    )
    _expect(
        report.get("testAccessClaim"),
        sealed._identity(expected_claim_path),
        "reported offline access claim identity",
    )
    claim = sealed._load_json(claim_path, "offline access claim")
    _expect(
        set(claim),
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "selection",
            "selectedCandidateId",
            "selectedNetwork",
            "robustnessNetwork",
            "output",
            "heldOutLabelsDecodedBeforeClaim",
            "secondAccessAllowed",
        },
        "offline access claim field inventory",
    )
    _expect(claim.get("schemaVersion"), sealed.SCHEMA_VERSION, "access claim schema")
    _expect(claim.get("kind"), sealed.TEST_ACCESS_KIND, "access claim kind")
    _expect(claim.get("selectedCandidateId"), winner, "access claim winner")
    _expect(claim.get("output"), str(offline_path.resolve()), "access claim output")
    _expect(
        claim.get("heldOutLabelsDecodedBeforeClaim"),
        False,
        "pre-claim held-out access",
    )
    _expect(claim.get("secondAccessAllowed"), False, "second held-out access")
    for key, expected in (
        ("selection", sealed._identity(selection_path)),
        ("selectedNetwork", selection["selectedNetwork"]),
        ("robustnessNetwork", sealed._identity(robustness_network)),
    ):
        _expect(claim.get(key), expected, f"access claim {key}")

    feature_corpus = sealed._feature_corpus(
        Path(plan["identities"]["corpus"]["path"]),
        split_seed=int(parsed["split"]["splitSeed"]),
        train_percent=float(parsed["split"]["trainPercent"]),
        validation_percent=float(parsed["split"]["validationPercent"]),
    )
    k0 = sealed._mapping(
        sealed._mapping(
            selection.get("candidates"), "selection candidates"
        ).get("K0"),
        "selection K0",
    )
    k0_health = sealed._mapping(k0.get("health"), "selection K0 health")
    expected_health, _ = sealed._runtime_health(
        candidate_id=winner + "-robustness",
        network_path=robustness_network,
        float_path=robustness_float,
        helper_path=Path(plan["identities"]["staticHceEvaluator"]["path"]),
        corpus=feature_corpus,
        runtime_protocol=parsed["runtime"],
        batch_size=int(parsed["common"]["batchSize"]),
        k0_dead_units=int(k0_health["deadDenseUnits"]),
    )
    _expect(
        report.get("robustnessHealth"),
        expected_health,
        "offline robustness feature-only health",
    )
    _expect(expected_health.get("passed"), True, "robustness health")
    test_balance = sealed._balance_audit(
        feature_corpus,
        2,
        parsed["split"],
    )
    _expect(
        report.get("testBalance"),
        test_balance,
        "offline test balance",
    )
    _expect(report.get("targetAndLoss"), parsed["targetAndLoss"], "offline target/loss")
    _expect(report.get("aggregation"), parsed["aggregation"], "offline aggregation")

    attestation = _verify_sufficient_attestation(
        selection_path=selection_path,
        offline_path=offline_path,
        claim_path=claim_path,
        plan=plan,
        selection=selection,
        robustness_network=robustness_network,
    )
    metrics = _metrics_from_sufficient_statistics(
        attestation.get("statistics"),
        test_balance,
        group_order=_heldout_group_order(feature_corpus),
    )
    _verify_public_metrics(report.get("metrics"), metrics)
    recomputed_bootstrap = sealed._paired_bootstrap(
        metrics["selected"],
        {
            "K0": metrics["K0"],
            "zeroResidual": metrics["zeroResidual"],
        },
        iterations=int(parsed["bootstrap"]["iterations"]),
        seed=int(parsed["bootstrap"]["seed"]),
    )

    comparisons = sealed._mapping(report.get("comparisons"), "offline comparisons")
    _expect(set(comparisons), {"K0", "zeroResidual"}, "offline comparison inventory")
    offline = parsed["offline"]
    comparison_passes = []
    for baseline in ("K0", "zeroResidual"):
        candidate = metrics["selected"]
        other = metrics[baseline]
        relative = (
            _number(other["huberLoss"], f"{baseline} huber")
            - _number(candidate["huberLoss"], "selected huber")
        ) / _number(other["huberLoss"], f"{baseline} huber")
        mae = _number(other["cpMae"], f"{baseline} MAE") - _number(
            candidate["cpMae"], "selected MAE"
        )
        phase_regressions = {
            phase: _number(
                candidate["phase"][phase]["cpMae"], f"selected {phase} MAE"
            )
            - _number(other["phase"][phase]["cpMae"], f"{baseline} {phase} MAE")
            for phase in sealed.PHASES
        }
        comparison = sealed._mapping(
            comparisons[baseline], f"offline comparison {baseline}"
        )
        _expect(
            set(comparison),
            {
                "relativeLossImprovement",
                "maeImprovementCp",
                "phaseMaeRegressionCp",
                "bootstrap",
                "checks",
                "passed",
            },
            f"{baseline} comparison field inventory",
        )
        _expect(
            comparison.get("relativeLossImprovement"),
            relative,
            f"{baseline} relative improvement",
        )
        _expect(
            comparison.get("maeImprovementCp"),
            mae,
            f"{baseline} MAE improvement",
        )
        reported_phase = sealed._mapping(
            comparison.get("phaseMaeRegressionCp"), f"{baseline} phase regressions"
        )
        _expect(set(reported_phase), set(sealed.PHASES), f"{baseline} phases")
        for phase, expected in phase_regressions.items():
            _expect(
                reported_phase[phase],
                expected,
                f"{baseline} {phase} regression",
            )
        bootstrap = sealed._mapping(
            comparison.get("bootstrap"), f"{baseline} bootstrap"
        )
        expected_bootstrap = sealed._mapping(
            recomputed_bootstrap[baseline],
            f"recomputed {baseline} bootstrap",
        )
        _expect(
            set(bootstrap),
            set(expected_bootstrap),
            f"{baseline} bootstrap field inventory",
        )
        for key, expected in expected_bootstrap.items():
            if key == "oneSided95LowerRelativeLossImprovement":
                _expect(
                    bootstrap.get(key),
                    float(expected),
                    f"{baseline} bootstrap lower bound",
                )
            else:
                _expect(
                    bootstrap.get(key),
                    expected,
                    f"{baseline} bootstrap {key}",
                )
        lower = _number(
            expected_bootstrap.get(
                "oneSided95LowerRelativeLossImprovement"
            ),
            f"{baseline} bootstrap lower bound",
        )
        checks = {
            "minimumRelativeLossImprovement": relative
            >= float(offline["minimumRelativeLossImprovementAgainstEach"]),
            "minimumMaeImprovement": mae
            >= float(offline["minimumMaeImprovementCpAgainstEach"]),
            "positiveOneSidedBootstrap": (
                lower > 0.0
                if bool(offline["requirePositiveFifthPercentileAgainstEach"])
                else True
            ),
            "maximumPhaseMaeRegression": max(phase_regressions.values())
            <= float(offline["maximumPhaseMaeRegressionCp"]),
        }
        reported_checks = sealed._mapping(
            comparison.get("checks"), f"{baseline} offline checks"
        )
        _expect(
            set(reported_checks),
            set(checks),
            f"{baseline} offline check inventory",
        )
        _expect(reported_checks, checks, f"{baseline} offline checks")
        passed = all(checks.values())
        _expect(comparison.get("passed"), passed, f"{baseline} comparison result")
        comparison_passes.append(passed)

    directional_k0 = _number(
        metrics["robustness"]["huberLoss"], "robustness huber"
    ) < _number(metrics["K0"]["huberLoss"], "K0 huber")
    directional_zero = _number(
        metrics["robustness"]["huberLoss"], "robustness huber"
    ) < _number(metrics["zeroResidual"]["huberLoss"], "zero residual huber")
    _expect(
        report.get("robustnessDirectionalImprovementOverK0"),
        directional_k0,
        "robustness direction over K0",
    )
    _expect(
        report.get("robustnessDirectionalImprovementOverZeroResidual"),
        directional_zero,
        "robustness direction over zero residual",
    )
    policy = sealed._mapping(offline.get("robustness"), "offline robustness policy")
    passed = all(comparison_passes) and (
        directional_k0
        if bool(policy["mustImproveDirectionallyAgainstK0"])
        else True
    ) and (
        directional_zero
        if bool(policy["mustImproveDirectionallyAgainstZeroResidual"])
        else True
    )
    _expect(report.get("passed"), passed, "offline aggregate decision")
    _expect(report.get("passed"), True, "offline promotion gate")
    _expect(report.get("fallbackAllowed"), False, "offline fallback")
    _expect(report.get("heldOutAccessCount"), 1, "offline access count")
    return selection, report


def _verify_training_selection_inputs(paths: Sequence[Path]) -> None:
    expected = (
        TRAINING_PLAN,
        VALIDATION_SELECTION,
        OFFLINE_REPORT,
    )
    resolved = [_resolve(path) for path in paths]
    if len(resolved) != len(set(resolved)):
        raise ValueError("--training-selection contains a duplicate resolved path")
    if set(resolved) != {_resolve(path) for path in expected}:
        raise ValueError(
            "generation-2 match sealing requires exactly training-plan.json, "
            "validation-selection.seal.json, and offline-test.json from "
            f"{_resolve(GENERATION2_OUTPUT_DIR)}"
        )

    plan_path, selection_path, offline_path = map(_resolve, expected)
    plan = _load_object(plan_path, "generation-2 training plan")
    training_v2._verify_generation2_plan_contract(plan, plan_path)
    _verify_offline_report_exact(selection_path, offline_path)


def _publish_outer_value(
    output: Path,
    value: Mapping[str, Any],
    verifier: Callable[[Path], Any],
) -> None:
    """Publish exclusively and retain any failed file as a hard blocker."""

    output = _resolve(output)
    descriptor = -1
    published_stat: os.stat_result | None = None
    _require_future_run_absent("immediately before outer-seal publication")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            output,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        # Capture the file ID from the exclusive descriptor before any other
        # code can observe and replace the published path.
        published_stat = os.fstat(descriptor)
        remaining = memoryview(core._canonical_json(dict(value)))
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("outer-seal publication made no write progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        _require_future_run_absent(
            "immediately after outer-seal publication"
        )
        # Keep the exclusive descriptor open throughout verification.  On
        # platforms that deny replacement of an open file this also closes
        # the replacement window.
        verifier(output)
        try:
            current_stat = output.stat()
        except OSError as error:
            raise ValueError(
                "outer seal disappeared during verification"
            ) from error
        if not os.path.samestat(published_stat, current_stat):
            raise ValueError("outer seal was replaced during verification")
        _require_future_run_absent(
            "after successful outer-seal verification"
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_outer_seal(
    output: Path,
    inner_seal: Path,
    identities: Mapping[str, Mapping[str, Any]],
    prelabel_report: Mapping[str, Any],
    exclusion_inventory: Sequence[Mapping[str, Any]],
    readiness_seal: Mapping[str, Any],
) -> Path:
    output = _resolve(output)
    inner_seal = _resolve(inner_seal)
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": OUTER_SEAL_KIND,
        "profileId": PROFILE_ID,
        "protocolGeneration": 2,
        "dataProfile": "deep-hce-v3",
        "seeds": dict(EXPECTED_SEEDS),
        "matchOutputRoot": str(_resolve(readiness.FUTURE_RUN_ROOT)),
        "adapter": core._identity(Path(__file__)),
        "adapterContract": dict(identities["adapterContract"]),
        "compatibilityCore": dict(identities["compatibilityCore"]),
        "compatibilityProtocol": dict(identities["compatibilityProtocol"]),
        "generation2Preregistration": dict(
            identities["generation2Preregistration"]
        ),
        "generation2Amendment": dict(identities["generation2Amendment"]),
        "activePrelabelSeal": core._identity(ACTIVE_PRELABEL_SEAL),
        "activePrelabelVerification": dict(prelabel_report),
        "matchReadinessSeal": core._identity(readiness.SEAL_PATH),
        "historyReplaySnapshot": core._identity(readiness.SNAPSHOT_PATH),
        "historyReplayManifest": core._identity(readiness.MANIFEST_PATH),
        "offlineSufficientWrapper": core._identity(OFFLINE_WRAPPER),
        "offlineSufficientAttestation": core._identity(OFFLINE_SUFFICIENT),
        "trainingPlan": core._identity(TRAINING_PLAN),
        "validationSelection": core._identity(VALIDATION_SELECTION),
        "offlineReport": core._identity(OFFLINE_REPORT),
        "offlineAccessClaim": core._identity(OFFLINE_ACCESS_CLAIM),
        "matchReadinessAudit": dict(readiness_seal["audit"]),
        "innerCoreSeal": core._identity(inner_seal),
        "explicitExclusionInventory": list(exclusion_inventory),
        "contracts": dict(OUTER_CONTRACTS),
    }
    _publish_outer_value(
        output,
        value,
        lambda path: _verify_outer_seal(
            path,
            allow_future_run=False,
        ),
    )
    return output


def _verify_outer_seal(
    path: Path,
    *,
    allow_future_run: bool = True,
) -> dict[str, Any]:
    path = _resolve(path)
    value = _load_object(path, "generation-2 match outer seal")
    _expect(set(value), OUTER_SEAL_FIELDS, "outer seal field inventory")
    _expect(value.get("schemaVersion"), SCHEMA_VERSION, "outer seal schema")
    _expect(value.get("kind"), OUTER_SEAL_KIND, "outer seal kind")
    _expect(value.get("profileId"), PROFILE_ID, "outer seal profile")
    _expect(value.get("protocolGeneration"), 2, "outer seal protocol generation")
    _expect(value.get("dataProfile"), "deep-hce-v3", "outer seal data profile")
    _expect(value.get("seeds"), EXPECTED_SEEDS, "outer seal seeds")
    _expect(
        value.get("matchOutputRoot"),
        str(_resolve(readiness.FUTURE_RUN_ROOT)),
        "outer seal match output root",
    )

    identities = _validate_static_contract()
    prelabel_report = _verify_active_prelabel(identities)
    _expect(
        value.get("activePrelabelVerification"),
        prelabel_report,
        "outer active prelabel verification",
    )
    readiness_seal = _verify_readiness(
        allow_future_run=allow_future_run
    )
    _verify_training_selection_inputs(
        (TRAINING_PLAN, VALIDATION_SELECTION, OFFLINE_REPORT)
    )
    _expect(
        value.get("contracts"),
        OUTER_CONTRACTS,
        "outer seal contracts",
    )
    expected = {
        "adapter": core._identity(Path(__file__)),
        "adapterContract": identities["adapterContract"],
        "compatibilityCore": identities["compatibilityCore"],
        "compatibilityProtocol": identities["compatibilityProtocol"],
        "generation2Preregistration": identities["generation2Preregistration"],
        "generation2Amendment": identities["generation2Amendment"],
        "activePrelabelSeal": core._identity(ACTIVE_PRELABEL_SEAL),
        "matchReadinessSeal": core._identity(readiness.SEAL_PATH),
        "historyReplaySnapshot": core._identity(readiness.SNAPSHOT_PATH),
        "historyReplayManifest": core._identity(readiness.MANIFEST_PATH),
        "offlineSufficientWrapper": core._identity(OFFLINE_WRAPPER),
        "offlineSufficientAttestation": core._identity(OFFLINE_SUFFICIENT),
        "trainingPlan": core._identity(TRAINING_PLAN),
        "validationSelection": core._identity(VALIDATION_SELECTION),
        "offlineReport": core._identity(OFFLINE_REPORT),
        "offlineAccessClaim": core._identity(OFFLINE_ACCESS_CLAIM),
    }
    for key, identity in expected.items():
        record = value.get(key)
        if not isinstance(record, dict) or not _same_identity(record, identity):
            raise ValueError(f"outer seal identity drift: {key}")
        core._verify_identity(record, f"outer seal {key}")

    inner = value.get("innerCoreSeal")
    if not isinstance(inner, dict):
        raise ValueError("outer seal lacks innerCoreSeal")
    inner_path = core._verify_identity(inner, "outer seal inner core seal")
    _install_generation2_profile()
    core._verify_seal(inner_path)

    inventory = value.get("explicitExclusionInventory")
    if not isinstance(inventory, list) or not inventory:
        raise ValueError("outer seal lacks explicit exclusion inventory")
    roots = {
        _resolve(Path(str(item.get("root", ""))))
        for item in inventory
        if isinstance(item, dict)
    }
    missing = {_resolve(path) for path in MANDATORY_EXCLUSIONS}.difference(roots)
    if missing:
        raise ValueError("outer seal omits a mandatory exclusion root")
    for item in inventory:
        if not isinstance(item, dict) or not isinstance(item.get("files"), list):
            raise ValueError("outer seal has malformed exclusion inventory")
        for identity in item["files"]:
            if not isinstance(identity, dict):
                raise ValueError("outer seal has malformed exclusion identity")
            core._verify_identity(identity, "outer seal historical exclusion")
    _expect(
        value.get("matchReadinessAudit"),
        readiness_seal["audit"],
        "outer match-readiness audit",
    )
    audit = _load_object(
        Path(str(value["innerCoreSeal"]["path"])).with_name(
            "king-state-v1-matches.audit.json"
        ),
        "inner match audit",
    )
    excluded = audit.get("exclusions")
    files = excluded.get("files") if isinstance(excluded, dict) else None
    if not isinstance(files, list) or not any(
        isinstance(item, dict)
        and _same_identity(item, core._identity(readiness.SNAPSHOT_PATH))
        for item in files
    ):
        raise ValueError(
            "inner core forbidden-orbit selection did not consume the "
            "pinned history replay snapshot"
        )
    return value


def _inner_from_outer(path: Path) -> Path:
    value = _verify_outer_seal(path)
    return _resolve(Path(str(value["innerCoreSeal"]["path"])))


def _sample(args: argparse.Namespace) -> None:
    identities = _validate_static_contract()
    _verify_active_prelabel(identities)
    _verify_readiness(allow_future_run=False)
    output_dir = _resolve(args.output_dir)
    for excluded in MANDATORY_EXCLUSIONS:
        if _is_within(output_dir, excluded):
            raise ValueError("fresh sampler directory must be outside historical roots")
    _install_generation2_profile()
    core._sample(
        argparse.Namespace(
            output_dir=output_dir,
            dotnet=_resolve(args.dotnet),
            sampler_project=core._sampler_project_path(),
            protocol=COMPATIBILITY_PROTOCOL,
        )
    )


def _seal(args: argparse.Namespace) -> None:
    identities = _validate_static_contract()
    prelabel_report = _verify_active_prelabel(identities)
    match_output_root = _canonical_match_output_root(args.match_output_root)
    _require_future_run_absent("before match sealing")
    readiness_seal = _verify_readiness(allow_future_run=False)
    exclusions = _require_explicit_exclusions(args.exclude)
    output_dir = _resolve(args.output_dir)
    sampler_dir = _resolve(args.sampler_dir)
    _check_fresh_layout(sampler_dir, output_dir)

    corpora = [_resolve(path) for path in args.training_corpus]
    if corpora != [_resolve(FRESH_CORPUS)]:
        raise ValueError(
            "generation-2 training corpus must be exactly the fresh deep-HCE-v3 "
            f"residual corpus: {_resolve(FRESH_CORPUS)}"
        )
    _verify_training_selection_inputs(args.training_selection)
    outer = _resolve(args.outer_seal or (output_dir / OUTER_SEAL_NAME))
    expected_outer = output_dir / OUTER_SEAL_NAME
    if outer != _resolve(expected_outer):
        raise ValueError(f"outer seal path must be {expected_outer}")
    if outer.exists():
        raise FileExistsError(f"refusing to replace outer match seal: {outer}")

    inventory = _coverage_inventory(
        [*exclusions, readiness.SNAPSHOT_PATH], output_dir
    )
    _install_generation2_profile()
    _require_future_run_absent(
        "immediately before inner-seal publication"
    )
    inner = core._seal(
        argparse.Namespace(
            output_dir=output_dir,
            match_output_root=match_output_root,
            sampler_dir=sampler_dir,
            sampler_project=core._sampler_project_path(),
            protocol=COMPATIBILITY_PROTOCOL,
            engine_executable=args.engine_executable,
            candidate_network=args.candidate_network,
            candidate_manifest=args.candidate_manifest,
            training_selection=args.training_selection,
            training_corpus=args.training_corpus,
            omega_match_assembly=args.omega_match_assembly,
            exclude=[*exclusions, readiness.SNAPSHOT_PATH],
        ),
        default_exclusions=False,
    )
    _require_future_run_absent(
        "immediately after inner-seal publication"
    )
    _write_outer_seal(
        outer,
        inner,
        identities,
        prelabel_report,
        inventory,
        readiness_seal,
    )
    _require_future_run_absent(
        "after successful generation-2 seal publication"
    )
    print(f"Published generation-2 match seal: {outer}", flush=True)
    print(f"Seal SHA-256: {core._sha256(outer)}", flush=True)


def _self_test() -> None:
    identities = _validate_static_contract()
    _verify_active_prelabel(identities)

    drifted = dict(EXPECTED_SEEDS)
    drifted["development"] += 1
    preregistration = _load_object(V2_PREREGISTRATION, "generation-2 preregistration")
    amendment = _load_object(V2_AMENDMENT, "generation-2 amendment")
    try:
        _validate_seed_profile(preregistration, amendment, drifted)
    except ValueError:
        pass
    else:
        raise AssertionError("seed drift was accepted")

    try:
        _require_explicit_exclusions(list(MANDATORY_EXCLUSIONS[:-1]))
    except ValueError:
        pass
    else:
        raise AssertionError("missing mandatory exclusion was accepted")

    with tempfile.TemporaryDirectory(prefix="king-state-v2-match-adapter-") as raw:
        root = Path(raw)
        original_future_root = readiness.FUTURE_RUN_ROOT
        readiness.FUTURE_RUN_ROOT = root / "future-match-output"
        try:
            _require_future_run_absent("during synthetic prepublication check")
            readiness.FUTURE_RUN_ROOT.mkdir(parents=True)
            try:
                _require_future_run_absent(
                    "during synthetic postpublication check"
                )
            except FileExistsError:
                pass
            else:
                raise AssertionError(
                    "existing future match root bypassed publication guard"
                )
        finally:
            readiness.FUTURE_RUN_ROOT = original_future_root

        publication_future_root = root / "publication-future-output"
        readiness.FUTURE_RUN_ROOT = publication_future_root
        try:
            successful = root / "successful-outer.json"
            _publish_outer_value(
                successful,
                {"verified": True},
                lambda path: _load_object(path, "synthetic outer seal"),
            )
            if _load_object(successful, "synthetic outer seal") != {
                "verified": True
            }:
                raise AssertionError(
                    "descriptor-held outer publication changed"
                )

            preexisting = root / "preexisting-outer.json"
            preexisting.write_text(
                '{"preexisting":true}\n',
                encoding="utf-8",
            )
            try:
                _publish_outer_value(
                    preexisting,
                    {"new": True},
                    lambda _path: None,
                )
            except FileExistsError:
                pass
            else:
                raise AssertionError("preexisting outer seal was replaced")
            if preexisting.read_text(encoding="utf-8") != (
                '{"preexisting":true}\n'
            ):
                raise AssertionError(
                    "failed exclusive publication deleted a raced-in file"
                )

            failed_owned = root / "failed-owned-outer.json"

            def reject_owned(_path: Path) -> None:
                raise ValueError("synthetic verification failure")

            try:
                _publish_outer_value(
                    failed_owned,
                    {"owned": True},
                    reject_owned,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("failed owned outer seal was accepted")
            if (
                not failed_owned.is_file()
                or failed_owned.read_bytes()
                != core._canonical_json({"owned": True})
            ):
                raise AssertionError(
                    "failed outer seal was not retained for diagnosis"
                )
            try:
                _publish_outer_value(
                    failed_owned,
                    {"retry": True},
                    lambda _path: None,
                )
            except FileExistsError:
                pass
            else:
                raise AssertionError(
                    "retained failed outer seal did not block retry"
                )

            replacement = root / "replacement-outer.json"
            replacement_payload = root / "replacement-payload.json"
            replacement_payload.write_bytes(
                core._canonical_json({"owned": True})
            )
            replacement_succeeded = False

            def replace_then_reject(path: Path) -> None:
                nonlocal replacement_succeeded
                try:
                    os.replace(replacement_payload, path)
                except OSError as error:
                    raise ValueError(
                        "platform prevented replacement of open outer seal"
                    ) from error
                replacement_succeeded = True
                raise ValueError(
                    "synthetic identical-byte replacement after publication"
                )

            try:
                _publish_outer_value(
                    replacement,
                    {"owned": True},
                    replace_then_reject,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("replacement outer seal was accepted")
            if (
                not replacement.is_file()
                or replacement.read_bytes()
                != core._canonical_json({"owned": True})
            ):
                raise AssertionError(
                    "failed or replaced outer seal was not retained"
                )
            if (
                not replacement_succeeded
                and not replacement_payload.is_file()
            ):
                raise AssertionError(
                    "platform-denied replacement payload unexpectedly vanished"
                )
        finally:
            readiness.FUTURE_RUN_ROOT = original_future_root

        try:
            _canonical_match_output_root(root / "wrong-match-output")
        except ValueError:
            pass
        else:
            raise AssertionError("mismatched generation-2 match root was accepted")
        try:
            _canonical_match_output_root(None)
        except ValueError:
            pass
        else:
            raise AssertionError("missing generation-2 match root was accepted")

        pgn_only = root / "game.pgn"
        pgn_only.write_text("[Event \"synthetic\"]\n", encoding="utf-8")
        try:
            _coverage_inventory([pgn_only], root / "out")
        except ValueError:
            pass
        else:
            raise AssertionError("raw PGN exclusion bypassed the replay snapshot")

        for label in ("forged selection", "forged offline report"):
            try:
                _generation2_marker(
                    {"protocolGeneration": 2, "dataProfile": "forged"},
                    (
                        SELECTION_GENERATION2_MARKER
                        if label == "forged selection"
                        else OFFLINE_GENERATION2_MARKER
                    ),
                    label,
                )
            except ValueError:
                pass
            else:
                raise AssertionError(f"{label} provenance was accepted")

        try:
            _verify_selection_exact(root / "wrong-selection-path.json")
        except ValueError:
            pass
        else:
            raise AssertionError("noncanonical validation selection was accepted")
        try:
            offline_wrapper._verify_attestation(
                root / "wrong-offline-attestation-path.json"
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "noncanonical offline attestation path was accepted"
            )
        try:
            _canonical_offline_claim_path(
                {"offlineTestAccessClaim": str(root / "wrong-claim.json")}
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "noncanonical offline access claim path was accepted"
            )

        sealed = training_v2.sealed
        phase_counts = {phase: 2 for phase in sealed.PHASES}
        synthetic_statistics: dict[str, Any] = {}
        for model_index, model in enumerate(
            ("selected", "K0", "robustness", "zeroResidual"),
            1,
        ):
            synthetic_statistics[model] = {
                "rows": dict(phase_counts),
                "groupLoss": {
                    phase: {
                        "group-a": float(model_index),
                        "group-b": float(model_index + 1),
                    }
                    for phase in sealed.PHASES
                },
                "groupMae": {
                    phase: {
                        "group-a": float(model_index + 2),
                        "group-b": float(model_index + 3),
                    }
                    for phase in sealed.PHASES
                },
            }
        recomputed = _metrics_from_sufficient_statistics(
            synthetic_statistics,
            {"phaseCounts": phase_counts},
        )
        if (
            recomputed["selected"]["phase"][sealed.PHASES[0]]["groups"] != 2
            or recomputed["selected"]["huberLoss"] != 1.5
        ):
            raise AssertionError("sufficient-statistics recomputation changed")
        synthetic_bootstrap = sealed._paired_bootstrap(
            recomputed["selected"],
            {
                "K0": recomputed["K0"],
                "zeroResidual": recomputed["zeroResidual"],
            },
            iterations=32,
            seed=20260727,
        )
        if (
            set(synthetic_bootstrap) != {"K0", "zeroResidual"}
            or synthetic_bootstrap["K0"].get("clusterUnit")
            != "global leakage component groupId"
            or synthetic_bootstrap["K0"].get("stratification")
            != "phase-incidence bitmask"
        ):
            raise AssertionError(
                "phase-incidence bootstrap from sufficient statistics changed"
            )
        forged_statistics = copy.deepcopy(synthetic_statistics)
        del forged_statistics["K0"]["groupMae"][sealed.PHASES[0]]["group-b"]
        try:
            _metrics_from_sufficient_statistics(
                forged_statistics,
                {"phaseCounts": phase_counts},
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "mismatched sufficient-statistics group maps were accepted"
            )

        expected_argv = ["trainer", "--input", "corpus", "--quiet"]
        forged_argv = list(expected_argv)
        forged_argv[2] = "other-corpus"
        try:
            training_v2._exact_argv(
                forged_argv, expected_argv, "forged candidate manifest argv"
            )
        except ValueError:
            pass
        else:
            raise AssertionError("forged candidate manifest argv was accepted")

        identity_target = root / "identity.json"
        identity_target.write_text("{}\n", encoding="utf-8")
        identity = core._identity(identity_target)
        identity_target.write_text('{"tampered":true}\n', encoding="utf-8")
        try:
            core._verify_identity(identity, "synthetic tamper target")
        except ValueError:
            pass
        else:
            raise AssertionError("identity tampering was accepted")

        exclusive = root / "exclusive.json"
        core._exclusive_json(exclusive, {"first": True})
        try:
            core._exclusive_json(exclusive, {"second": True})
        except FileExistsError:
            pass
        else:
            raise AssertionError("no-clobber publication was bypassed")

    _install_generation2_profile()
    for gate, seed in EXPECTED_SEEDS.items():
        _expect(core.GATE_SPECS[gate]["seed"], seed, f"installed {gate} seed")
        for key, value in _ORIGINAL_SPECS[gate].items():
            if key != "seed":
                _expect(
                    core.GATE_SPECS[gate][key],
                    value,
                    f"unchanged core degree {gate}.{key}",
                )
    print("king_state_matches_generation2 self-test passed", flush=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    sample = commands.add_parser("sample", help="create the three rules-only pools")
    sample.add_argument("--output-dir", required=True, type=Path)
    sample.add_argument("--dotnet", required=True, type=Path)

    seal = commands.add_parser("seal", help="select and seal all generation-2 gates")
    seal.add_argument("--output-dir", required=True, type=Path)
    seal.add_argument("--outer-seal", type=Path)
    seal.add_argument("--match-output-root", type=Path)
    seal.add_argument("--sampler-dir", required=True, type=Path)
    seal.add_argument("--engine-executable", required=True, type=Path)
    seal.add_argument("--candidate-network", required=True, type=Path)
    seal.add_argument("--candidate-manifest", required=True, type=Path)
    seal.add_argument(
        "--training-selection", action="append", required=True, type=Path
    )
    seal.add_argument(
        "--training-corpus", action="append", required=True, type=Path
    )
    seal.add_argument("--omega-match-assembly", required=True, type=Path)
    seal.add_argument("--exclude", action="append", required=True, type=Path)

    verify = commands.add_parser("verify", help="verify both match seals")
    verify.add_argument("--seal", required=True, type=Path)

    attest = commands.add_parser("attest", help="record equal-time idle attestation")
    attest.add_argument("--seal", required=True, type=Path)
    attest.add_argument("--output", required=True, type=Path)
    attest.add_argument("--operator")

    assess = commands.add_parser("assess", help="assess a sealed match run")
    assess.add_argument("--seal", required=True, type=Path)
    assess.add_argument("--gate", required=True, choices=tuple(EXPECTED_SEEDS))
    assess.add_argument("--events", required=True, type=Path)
    assess.add_argument("--output", required=True, type=Path)
    assess.add_argument("--idle-attestation", type=Path)

    commands.add_parser("self-test", help="run target-free adapter checks")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "sample":
        _sample(args)
    elif args.command == "seal":
        _seal(args)
    elif args.command == "verify":
        value = _verify_outer_seal(args.seal)
        inner = _load_object(
            Path(str(value["innerCoreSeal"]["path"])), "inner core match seal"
        )
        print(f"Verified generation-2 match seal: {_resolve(args.seal)}", flush=True)
        print(f"Candidate: {inner['candidateNetworkSha256']}", flush=True)
    elif args.command == "attest":
        inner = _inner_from_outer(args.seal)
        core._attest(
            argparse.Namespace(
                seal=inner,
                output=args.output,
                operator=args.operator,
            )
        )
    elif args.command == "assess":
        inner = _inner_from_outer(args.seal)
        core._assess(
            argparse.Namespace(
                seal=inner,
                gate=args.gate,
                events=args.events,
                output=args.output,
                idle_attestation=args.idle_attestation,
            )
        )
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
        raise SystemExit(2)
