#!/usr/bin/env python3
"""Generation-3 adapter for the sealed Omega king-state match core.

The adapter performs no model selection.  It requires the exact validation
selection, robustness seal, one-time offline pass, and preselection history
readiness before sampling.  At sealing time it creates a deterministic
compatibility envelope for the older exercised match core, seals all three
fresh suites together, and publishes an outer seal while the canonical future
match root is still absent.  Its ``launch`` command is the sole authorized
OmegaMatch entry point: it fixes per-invocation pair budgets and verifies the
predecessor authorization before starting or resuming a gate.  An append-only
intent/completion chain binds every assessment to exactly one launch and its
exact event bytes.  Canonical assessments, recoverable authorizations,
terminal decisions, and run timestamps enforce
development -> equal-node -> equal-time.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Sequence

# The trainer owns the deterministic NumPy preload check and must be the first
# project/NumPy-dependent import in this process.
import king_state_train_generation3 as training

import king_state_match_readiness_generation3 as readiness
import king_state_matches as core
import king_state_offline_generation3 as offline
import king_state_v3 as prelabel


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v3-deep-hce-v4"
OUTER_SEAL_KIND = "omega-nnue-king-state-v3-match-seal"
ASSESSMENT_KIND = "omega-nnue-king-state-v3-gate-assessment"
DECISION_KIND = "omega-nnue-king-state-v3-gate-decision"
AUTHORIZATION_KIND = "omega-nnue-king-state-v3-gate-authorization"
LAUNCH_INTENT_KIND = "omega-nnue-king-state-v3-launch-intent"
LAUNCH_COMPLETION_KIND = "omega-nnue-king-state-v3-launch-completion"
INNER_SEAL_NAME = "king-state-v1-matches.seal.json"
OUTER_SEAL_NAME = "king-state-v3-matches.seal.json"

EXPECTED_SEEDS = {
    "development": 2026072201,
    "equal-node": 2026072202,
    "equal-time": 2026072203,
}
INITIAL_PAIR_BUDGETS = {
    "development": 32,
    "equal-node": 64,
    "equal-time": 64,
}
RESUME_PAIR_BUDGET = 4
GATE_ORDER = ("development", "equal-node", "equal-time")
GATE_PREDECESSOR = {
    "development": None,
    "equal-node": "development",
    "equal-time": "equal-node",
}
SUCCESS_DECISIONS = {
    "development": "pass",
    "equal-node": "promote",
    "equal-time": "promote",
}
TERMINAL_DECISIONS = {
    "development": {"pass", "fail", "safety-fail"},
    "equal-node": {"promote", "futility", "inconclusive", "safety-fail"},
    "equal-time": {"promote", "futility", "inconclusive", "safety-fail"},
}
EXPECTED_ENGINE = {
    "path": "tools/omega_nnue/frozen_runtime/king-state-v3/engine/senpai.exe",
    "bytes": 619520,
    "sha256": "37b4846f6f12cd795730d6472279007a0f53d8653e2b9a0071c1d13d2eed09e0",
}
CONTRACTS = {
    "canonicalReadinessRequiredBeforeSuiteSampling": True,
    "canonicalOfflinePassRequiredBeforeSuiteSampling": True,
    "validationSelectionExactlyRecomputedBeforeSamplingAndSealing": True,
    "futureMatchRootBoundAndAbsentThroughOuterSealPublication": True,
    "recursiveAnnotationVariationsRejectedBeforeHistoryReplay": True,
    "allThreeSuitesRulesOnlyCandidateBlindAndSealedTogether": True,
    "compatibilityEnvelopeIsDerivedOnlyAfterOfflinePass": True,
    "failedOuterSealRetainedAndBlocksRetry": True,
    "canonicalGateEvidenceInsideFutureMatchRoot": True,
    "gateOrderMachineEnforcedByAuthorizationAndRunTime": True,
    "adapterIsSoleAuthorizedOmegaMatchLauncher": True,
    "initialAndResumePairBudgetsAreFixedByGate": True,
    "appendOnlyLaunchIntentAndCompletionChainRequired": True,
    "assessmentRequiredBetweenLaunches": True,
    "assessmentEventIdentityMustEqualLatestLaunchCompletion": True,
    "assessmentProgressDeltaBoundedByLaunchPairBudget": True,
    "authorizationRepairIsIdempotentOnlyBeforeEvents": True,
    "equalTimeIdleAttestationMustPostdateEqualNodeDecision": True,
    "managedLauncherUsesPinnedDotnetSelectionNamespaces": True,
    "managedLauncherSanitizesAmbientDotnetEnvironment": True,
    "managedLauncherRehashesAppBundleBeforeAndAfter": True,
    "terminalGateDecisionExclusiveAndBlocksRetry": True,
    "runnerUpForbidden": True,
}

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO.parent
CONTRACT_PATH = (
    REPO / "validation" / "omega-nnue-king-state-v3-match-adapter.json"
)
MATCH_PROTOCOL = (
    REPO / "validation" / "omega-nnue-king-state-v3-match-protocol.json"
)
PREREGISTRATION = training.PREREGISTRATION
AMENDMENT_001 = training.AMENDMENT
AMENDMENT_002 = training.AMENDMENT_002
AMENDMENT_003 = training.AMENDMENT_003
AMENDMENT_CHAIN = (AMENDMENT_001, AMENDMENT_002, AMENDMENT_003)
DECLARED_AMENDMENT_CHAIN = [
    {
        "path": "validation/omega-nnue-king-state-v3-amendment-001.json",
        "bytes": 8729,
        "sha256": (
            "846f43487558246edec871359f9f0a5dbd0377e69a3b95baee6ab6f47f32f78b"
        ),
    },
    {
        "path": "validation/omega-nnue-king-state-v3-amendment-002.json",
        "bytes": 7192,
        "sha256": (
            "833886a638ebda8d062dfd195d22cfc482152730284d7da5f73a5066fb69c266"
        ),
    },
    {
        "path": "validation/omega-nnue-king-state-v3-amendment-003.json",
        "bytes": training.AMENDMENT_003_BYTES,
        "sha256": training.AMENDMENT_003_SHA256,
    },
]
TRAINING_PLAN = training.PLAN_PATH
VALIDATION_SELECTION = training.SELECTION_PATH
ROBUSTNESS_SEAL = training.ROBUSTNESS_PATH
OFFLINE_REPORT = offline.REPORT_PATH
OFFLINE_CLAIM = offline.CLAIM_PATH
OFFLINE_ATTESTATION = offline.ATTESTATION_PATH
FRESH_CORPUS = training.CORPUS
READINESS_SEAL = readiness.SEAL_PATH
HISTORY_SNAPSHOT = readiness.SNAPSHOT_PATH
HISTORY_MANIFEST = readiness.MANIFEST_PATH
OPENING_REPLAY_SNAPSHOT = readiness.OPENING_SNAPSHOT_PATH
OPENING_REPLAY_MANIFEST = readiness.OPENING_MANIFEST_PATH
FUTURE_RUN_ROOT = readiness.FUTURE_RUN_ROOT

SAMPLER_DIR = REPO / "build-king-state-v3" / "matches" / "sampler"
SEALED_DIR = REPO / "build-king-state-v3" / "matches" / "sealed"
COMPATIBILITY_DIR = (
    REPO / "build-king-state-v3" / "matches" / "core-compatibility"
)
COMPAT_PLAN = COMPATIBILITY_DIR / "training-plan.compat.json"
COMPAT_MANIFEST = COMPATIBILITY_DIR / "candidate-manifest.compat.json"
COMPAT_SELECTION = COMPATIBILITY_DIR / "validation-selection.compat.json"
COMPAT_OFFLINE = COMPATIBILITY_DIR / "offline-test.compat.json"
OUTER_SEAL = SEALED_DIR / OUTER_SEAL_NAME
INNER_SEAL = SEALED_DIR / INNER_SEAL_NAME

ASSESSMENT_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "gate",
    "sequence",
    "createdUtc",
    "outerSeal",
    "innerCoreSeal",
    "events",
    "priorAssessment",
    "predecessorDecision",
    "gateAuthorization",
    "launchCompletion",
    "coreAssessment",
    "progress",
    "decision",
    "terminal",
    "authorizedSuccessor",
}
DECISION_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "gate",
    "createdUtc",
    "outerSeal",
    "innerCoreSeal",
    "assessment",
    "events",
    "predecessorDecision",
    "gateAuthorization",
    "decision",
    "passed",
    "authorizedSuccessor",
    "retryAllowed",
}
AUTHORIZATION_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "gate",
    "createdUtc",
    "outerSeal",
    "predecessorDecision",
    "idleMachineAttestation",
    "eventsAbsentAtAuthorization",
}
LAUNCH_INTENT_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "gate",
    "sequence",
    "createdUtc",
    "outerSeal",
    "innerCoreSeal",
    "predecessorDecision",
    "gateAuthorization",
    "priorAssessment",
    "priorCompletion",
    "eventsBefore",
    "action",
    "pairBudget",
}
LAUNCH_COMPLETION_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "gate",
    "sequence",
    "createdUtc",
    "intent",
    "eventsAfter",
}

ENGINE_EXECUTABLE = REPO / EXPECTED_ENGINE["path"]
OMEGA_MATCH_ASSEMBLY = (
    REPO
    / "tools"
    / "omega_nnue"
    / "frozen_runtime"
    / "king-state-v3"
    / "omegamatch"
    / "OmegaMatch.dll"
)
DOTNET_HOST = WORKSPACE / ".dotnet" / "dotnet.exe"
MANDATORY_EXCLUSIONS = (
    REPO / "build-msvc",
    REPO / "build-king-state-v2",
    REPO / "validation",
    WORKSPACE / "match-runs",
    WORKSPACE / "match-runs" / "configs",
    WORKSPACE / "match-runs" / "output",
    WORKSPACE / "opening-audit",
    WORKSPACE / "fixtures",
    WORKSPACE / "omega-lab",
    WORKSPACE / "omega-lab" / "regressions",
    (
        WORKSPACE
        / "corechess-arena"
        / "Tools"
        / "OmegaMatch"
        / "Openings"
    ),
)

OUTER_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "createdFromOfflinePass",
    "seeds",
    "gateOrder",
    "assessmentEvidence",
    "matchOutputRoot",
    "engine",
    "adapter",
    "adapterContract",
    "matchProtocol",
    "compatibilityCore",
    "readinessWrapper",
    "offlineWrapper",
    "trainingOrchestrator",
    "prelabelOrchestrator",
    "preregistration",
    "amendmentChain",
    "prelabelSeal",
    "readinessSeal",
    "historySnapshot",
    "historyManifest",
    "openingReplaySnapshot",
    "openingReplayManifest",
    "trainingPlan",
    "validationSelection",
    "robustnessSeal",
    "offlineAccessClaim",
    "offlineSufficientStatistics",
    "offlineReport",
    "selectedNetwork",
    "selectedManifest",
    "compatibilityFiles",
    "innerCoreSeal",
    "explicitExclusionInventory",
    "contracts",
}

_ORIGINAL_SPECS = copy.deepcopy(core.GATE_SPECS)
_ORIGINAL_VALIDATE_PROTOCOL = core._validate_protocol
_ORIGINAL_SAMPLER_PAIRS = core.SAMPLER_TRAJECTORY_PAIRS


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _strict_object(path: Path, label: str) -> dict[str, Any]:
    return offline._strict_object(path, label)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


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


def _expect(actual: Any, expected: Any, label: str) -> None:
    if not _strict_equal(actual, expected):
        raise ValueError(f"{label} changed: expected {expected!r}, got {actual!r}")


def _same_identity(left: Any, right: Any) -> bool:
    return offline._same_identity(left, right)


def _verify_identity(value: Any, path: Path, label: str) -> None:
    expected = core._identity(_resolve(path))
    if not _same_identity(value, expected):
        raise ValueError(f"{label} identity changed")
    core._verify_identity(value, label)


def _strict_file_identity(
    value: Any, path: Path, label: str
) -> dict[str, Any]:
    record = offline._identity(value, label)
    path = _resolve(path)
    if _resolve(Path(record["path"])) != path:
        raise ValueError(f"{label} path changed")
    expected = core._identity(path)
    if not _strict_equal(record, expected):
        raise ValueError(f"{label} identity changed")
    return record


def _assessment_evidence_contract() -> dict[str, Any]:
    return {
        "gateOrder": list(GATE_ORDER),
        "eventsRelativePathByGate": {
            gate: f"{gate}/events.jsonl" for gate in GATE_ORDER
        },
        "assessmentDirectoryRelativePathByGate": {
            gate: f"{gate}/assessments" for gate in GATE_ORDER
        },
        "assessmentSequenceFilenameFormat": "%06d.json starting at 000001",
        "launchDirectoryRelativePathByGate": {
            gate: f"{gate}/launches" for gate in GATE_ORDER
        },
        "launchIntentFilenameFormat": (
            "%06d.intent.json starting at 000001"
        ),
        "launchCompletionFilenameFormat": (
            "%06d.completion.json starting at 000001"
        ),
        "decisionRelativePathByGate": {
            gate: f"{gate}/decision.json" for gate in GATE_ORDER
        },
        "authorizationRelativePathByGate": {
            "equal-node": "equal-node/authorization.json",
            "equal-time": "equal-time/authorization.json",
        },
        "equalTimeIdleAttestationRelativePath": (
            "equal-time/idle-machine.attestation.json"
        ),
        "advanceRequiresDecision": {
            "equal-node": {
                "gate": "development",
                "decision": "pass",
            },
            "equal-time": {
                "gate": "equal-node",
                "decision": "promote",
            },
            "promotion": {
                "gate": "equal-time",
                "decision": "promote",
            },
        },
        "terminalDecisions": {
            gate: sorted(TERMINAL_DECISIONS[gate]) for gate in GATE_ORDER
        },
        "launchCommandFormat": (
            "python tools/omega_nnue/"
            "king_state_matches_generation3.py launch "
            "--gate <gate> --action <run-or-resume>"
        ),
        "fixedPairBudgets": {
            gate: {
                "run": INITIAL_PAIR_BUDGETS[gate],
                "resume": RESUME_PAIR_BUDGET,
            }
            for gate in GATE_ORDER
        },
        "directOmegaMatchRunOrResumeForbidden": True,
        "appendOnlyLaunchIntentAndCompletionChainRequired": True,
        "assessmentRequiresExactlyOneNewCompletedLaunch": True,
        "assessmentEventIdentityMustEqualLatestLaunchCompletion": True,
        "assessmentProgressDeltaBoundedByLaunchPairBudget": True,
        "resumeRequiresLatestNonterminalExactAssessment": True,
        "pendingLaunchIntentRecoveryUsesSameLaunchCommand": True,
        "authorizationRepairCommandFormat": (
            "python tools/omega_nnue/"
            "king_state_matches_generation3.py repair-transition "
            "--gate <equal-node-or-equal-time>"
        ),
        "authorizationRepairRequiresNoSuccessorEvidence": True,
        "existingAuthorizationRepairIsVerificationOnly": True,
        "terminalPublicationRecoveryCreatesNoNewAssessment": True,
        "eventLogMustBeAppendOnlyBeforeTerminalDecision": True,
        "nonterminalReassessmentRequiresStrictMonotonicProgress": True,
        "equalNodeAuthorizationPublishedOnlyByDevelopmentPass": True,
        "equalTimeAuthorizationRequiresPostEqualNodeIdleAttestation": True,
        "idleAttestationCreatedUtcMustNotPrecedeEqualNodeDecision": True,
        "nextGateRunCreatedUtcMustNotPrecedeAuthorization": True,
        "assessmentAndDecisionArtifactsExclusiveNoClobber": True,
        "terminalDecisionBlocksFurtherAssessment": True,
        "failedOrInconclusiveDecisionAuthorizesSuccessor": False,
    }


def _validate_match_protocol() -> dict[str, Any]:
    value = _strict_object(MATCH_PROTOCOL, "generation-3 match protocol")
    expected_top = {
        "schemaVersion",
        "kind",
        "generationId",
        "profileId",
        "authority",
        "amendmentChain",
        "assessmentEvidence",
        "rulesOnlySampler",
        "seeds",
        "engineAssignment",
        "development",
        "equalNode",
        "equalTime",
        "common",
    }
    _expect(set(value), expected_top, "match protocol field inventory")
    _expect(value.get("schemaVersion"), 1, "match protocol schema")
    _expect(
        value.get("kind"),
        "omega-nnue-king-state-v3-match-compatibility-protocol",
        "match protocol kind",
    )
    _expect(
        value.get("generationId"),
        "omega-nnue-king-state-v3",
        "match protocol generation",
    )
    _expect(value.get("profileId"), PROFILE_ID, "match protocol profile")
    _expect(
        value.get("authority"),
        "validation/omega-nnue-king-state-v3-preregistration.json",
        "match protocol preregistration authority",
    )
    _expect(
        value.get("amendmentChain"),
        DECLARED_AMENDMENT_CHAIN,
        "match protocol amendment chain",
    )
    readiness._amendment_chain_identities()
    _expect(
        value.get("assessmentEvidence"),
        _assessment_evidence_contract(),
        "match assessment evidence contract",
    )
    _expect(value.get("seeds"), EXPECTED_SEEDS, "match protocol seeds")
    _expect(
        value.get("engineAssignment"),
        {
            "sameExecutable": True,
            "candidate": {
                "UseOmegaNNUE": True,
                "OmegaNNUEFile": "exact offline-passing winner hash",
                "runtimeProof": {
                    "OmegaNnueActiveVerified": True,
                    "startupDiagnosticsRequireLoadedRecord": True,
                    "finalStartupDiagnostic": (
                        "info string Omega NNUE evaluation active"
                    ),
                    "externalAssetExactlyCandidateNetworkHash": True,
                },
            },
            "control": {
                "UseOmegaNNUE": False,
                "OmegaNNUEFile": "<empty>",
                "emptyOptionTransmittedExplicitly": True,
                "runtimeProof": {
                    "OmegaNnueActiveVerified": False,
                    "ExternalAssets": [],
                    "startupDiagnosticsForbidLoadedOrActive": True,
                    "finalStartupDiagnostic": (
                        "info string Omega NNUE disabled; handcrafted "
                        "evaluation active"
                    ),
                },
            },
        },
        "match protocol engine assignment",
    )
    sampler = _mapping(value.get("rulesOnlySampler"), "match sampler")
    _expect(
        sampler,
        {
            "deterministicPrng": "SplitMix64",
            "trajectoryPairs": 4096,
            "independentTrajectoriesPerPair": 2,
            "maxPlies": 220,
            "positionsPerPhaseAndSide": 2,
            "captureSelectionPercent": 72,
        },
        "match sampler",
    )
    _expect(
        value.get("development"),
        {
            "roots": 32,
            "rootsPerPhase": 8,
            "rootsPerPhaseAndSideToMove": 4,
            "gamesPerRoot": 2,
            "mode": "nodes",
            "nodesPerMove": 20000,
            "initialPairBudget": 32,
            "resumePairBudget": 4,
            "minimumCandidateScore": 0.4,
            "maximumSafetyFailures": 0,
        },
        "match development protocol",
    )
    common_gate = {
        "maximumPairs": 128,
        "pairsPerPhase": 32,
        "pairsPerPhaseAndSideToMove": 16,
        "gamesPerPair": 2,
        "minimumPairsBeforeDecision": 64,
        "nullElo": 10,
        "alpha": 0.05,
        "beta": 0.1,
        "promotionEValue": 20,
        "futilityEValue": 10,
        "balancedBlockChecksOnly": True,
        "maximumSafetyFailures": 0,
    }
    _expect(
        value.get("equalNode"),
        {
            **common_gate,
            "mode": "nodes",
            "nodesPerMove": 50000,
            "initialPairBudget": 64,
            "resumePairBudget": 4,
        },
        "match equal-node protocol",
    )
    _expect(
        value.get("equalTime"),
        {
            **common_gate,
            "mode": "movetime",
            "moveTimeMs": 1000,
            "initialPairBudget": 64,
            "resumePairBudget": 4,
            "oneGameAtATime": True,
            "idleMachineAttestationRequired": True,
        },
        "match equal-time protocol",
    )
    _expect(
        value.get("common"),
        {
            "balancedPairBlockSize": 4,
            "onePairFromEachPhasePerBlock": True,
            "rootSideToMoveConstantWithinBlock": True,
            "rootSideToMoveAlternatesBetweenBlocks": True,
            "inverseArrangeForSeededDotNetRandomShuffle": True,
            "pairBudgetRequired": True,
            "pairBudgetMustBeMultipleOf": 4,
            "executionOnlyThroughAdapterLaunch": True,
            "appendOnlyLaunchIntentCompletionChainRequired": True,
            "assessmentRequiredBetweenLaunches": True,
            "assessmentProgressDeltaBoundedByPairBudget": True,
            "authorizationRepairIsIdempotentBeforeEvents": True,
            "managedLauncherUsesPinnedDotnetSelectionNamespaces": True,
            "managedLauncherSanitizesAmbientDotnetEnvironment": True,
            "managedLauncherRehashesAppBundleBeforeAndAfter": True,
            "freshProcessPerGame": True,
            "repeats": 1,
            "maxPlies": 300,
            "absoluteMaxPlies": 400,
            "stopGraceMs": 2000,
        },
        "match common protocol",
    )
    return value


def _validate_static_contract() -> dict[str, dict[str, Any]]:
    contract = _strict_object(CONTRACT_PATH, "generation-3 match adapter")
    _expect(
        set(contract),
        {
            "schemaVersion",
            "kind",
            "profileId",
            "status",
            "compatibility",
            "declarations",
            "canonicalPaths",
            "historicalPositionRoots",
            "engine",
            "seeds",
            "contracts",
        },
        "adapter contract field inventory",
    )
    _expect(contract.get("schemaVersion"), 1, "adapter contract schema")
    _expect(
        contract.get("kind"),
        "omega-nnue-king-state-v3-match-adapter-contract",
        "adapter contract kind",
    )
    _expect(contract.get("profileId"), PROFILE_ID, "adapter contract profile")
    _expect(
        contract.get("status"),
        (
            "target-blind adapter contract; match suites may be created only "
            "after the canonical one-time offline gate passes"
        ),
        "adapter contract status",
    )
    _expect(contract.get("contracts"), CONTRACTS, "adapter contract guarantees")
    _expect(contract.get("seeds"), EXPECTED_SEEDS, "adapter contract seeds")
    compatibility = _mapping(
        contract.get("compatibility"), "adapter compatibility"
    )
    _expect(
        compatibility,
        {
            "matchCore": "tools/omega_nnue/king_state_matches.py",
            "replayCore": "tools/omega_nnue/king_state_match_readiness.py",
            "protocol": (
                "validation/omega-nnue-king-state-v3-match-protocol.json"
            ),
            "compatibilityEnvelopeChangesModelOrDecision": False,
        },
        "adapter compatibility",
    )
    declarations = _mapping(
        contract.get("declarations"), "adapter declarations"
    )
    expected_declaration_strings = {
        "preregistration": (
            "validation/omega-nnue-king-state-v3-preregistration.json"
        ),
        "amendmentChain": [
            dict(value) for value in DECLARED_AMENDMENT_CHAIN
        ],
        "trainingOrchestrator": (
            "tools/omega_nnue/king_state_train_generation3.py"
        ),
        "offlineWrapper": (
            "tools/omega_nnue/king_state_offline_generation3.py"
        ),
        "readinessWrapper": (
            "tools/omega_nnue/king_state_match_readiness_generation3.py"
        ),
    }
    _expect(
        declarations,
        expected_declaration_strings,
        "adapter declarations",
    )
    expected_declaration_paths = {
        "preregistration": PREREGISTRATION,
        "trainingOrchestrator": Path(training.__file__),
        "offlineWrapper": Path(offline.__file__),
        "readinessWrapper": Path(readiness.__file__),
    }
    for name, expected_path in expected_declaration_paths.items():
        _expect(
            _resolve(REPO / str(declarations[name])),
            _resolve(expected_path),
            f"adapter declaration {name}",
        )
    declared_chain = declarations.get("amendmentChain")
    resolved_chain = [
        _resolve(REPO / item["path"]) for item in declared_chain
    ]
    _expect(
        resolved_chain,
        [_resolve(path) for path in AMENDMENT_CHAIN],
        "adapter ordered amendment chain",
    )
    amendment_chain = readiness._amendment_chain_identities()
    engine = _mapping(contract.get("engine"), "adapter engine")
    _expect(
        engine,
        {
            **EXPECTED_ENGINE,
            "sameExecutableForCandidateAndControl": True,
            "architecture3RuntimeVerified": True,
            "candidateOmegaNNUEFile": (
                "exact offline-passing winner hash"
            ),
            "candidateUseOmegaNNUE": True,
            "candidateRuntimeProof": {
                "OmegaNnueActiveVerified": True,
                "startupDiagnosticsRequireLoadedRecord": True,
                "finalStartupDiagnostic": (
                    "info string Omega NNUE evaluation active"
                ),
                "externalAssetExactlyCandidateNetworkHash": True,
            },
            "controlOmegaNNUEFile": "<empty>",
            "controlUseOmegaNNUE": False,
            "emptyControlOptionTransmittedExplicitly": True,
            "controlRuntimeProof": {
                "OmegaNnueActiveVerified": False,
                "ExternalAssets": [],
                "startupDiagnosticsForbidLoadedOrActive": True,
                "finalStartupDiagnostic": (
                    "info string Omega NNUE disabled; handcrafted "
                    "evaluation active"
                ),
            },
        },
        "adapter engine",
    )
    canonical = _mapping(contract.get("canonicalPaths"), "canonical paths")
    canonical_strings = {
        "samplerDirectory": "build-king-state-v3/matches/sampler",
        "sealedDirectory": "build-king-state-v3/matches/sealed",
        "compatibilityDirectory": (
            "build-king-state-v3/matches/core-compatibility"
        ),
        "futureMatchRoot": "../match-runs/output/king-state-v3",
    }
    _expect(canonical, canonical_strings, "canonical paths")
    expected_paths = {
        "samplerDirectory": SAMPLER_DIR,
        "sealedDirectory": SEALED_DIR,
        "compatibilityDirectory": COMPATIBILITY_DIR,
        "futureMatchRoot": FUTURE_RUN_ROOT,
    }
    for name, path in expected_paths.items():
        declared = _resolve(REPO / str(canonical.get(name, "")))
        _expect(declared, _resolve(path), f"canonical path {name}")
    historical_roots = contract.get("historicalPositionRoots")
    _expect(
        historical_roots,
        [
            "build-msvc",
            "build-king-state-v2",
            "validation",
            "../match-runs",
            "../match-runs/configs",
            "../match-runs/output",
            "../opening-audit",
            "../fixtures",
            "../omega-lab",
            "../omega-lab/regressions",
            "../corechess-arena/Tools/OmegaMatch/Openings",
        ],
        "historical position roots",
    )
    _expect(
        [_resolve(REPO / path) for path in historical_roots],
        [_resolve(path) for path in MANDATORY_EXCLUSIONS],
        "resolved historical position roots",
    )

    profile = training._validate_preregistration(PREREGISTRATION)
    readiness._amendment_chain_identities()
    fresh = _mapping(profile.get("freshMatchSuites"), "fresh match suites")
    seeds = _mapping(fresh.get("seeds"), "fresh match seeds")
    _expect(
        seeds,
        {
            "developmentScreen": EXPECTED_SEEDS["development"],
            "equalNodeConfirmation": EXPECTED_SEEDS["equal-node"],
            "equalTimeConfirmation": EXPECTED_SEEDS["equal-time"],
            "configSeedEqualsSuiteSeed": True,
        },
        "preregistered match seeds",
    )
    sampler = _mapping(fresh.get("sampler"), "preregistered match sampler")
    _expect(sampler.get("trajectoryPairs"), 4096, "sampler trajectory pairs")
    protocol = _validate_match_protocol()
    _expect(
        protocol["rulesOnlySampler"],
        {
            "deterministicPrng": sampler["deterministicPrng"],
            "trajectoryPairs": sampler["trajectoryPairs"],
            "independentTrajectoriesPerPair": sampler[
                "independentTrajectoriesPerPair"
            ],
            "maxPlies": sampler["maxPlies"],
            "positionsPerPhaseAndSide": sampler["positionsPerPhaseAndSide"],
            "captureSelectionPercent": sampler["captureSelectionPercent"],
        },
        "protocol/preregistration sampler",
    )
    _expect(
        protocol["development"],
        {
            **{
                key: fresh["developmentScreen"][source]
                for key, source in (
                    ("roots", "roots"),
                    ("rootsPerPhase", "rootsPerPhase"),
                    (
                        "rootsPerPhaseAndSideToMove",
                        "rootsPerPhaseAndSideToMove",
                    ),
                    ("gamesPerRoot", "gamesPerRoot"),
                    ("mode", "mode"),
                    ("nodesPerMove", "nodesPerMove"),
                    ("minimumCandidateScore", "minimumCandidateScore"),
                    ("maximumSafetyFailures", "maximumSafetyFailures"),
                )
            },
            "initialPairBudget": INITIAL_PAIR_BUDGETS["development"],
            "resumePairBudget": RESUME_PAIR_BUDGET,
        },
        "protocol/preregistration development",
    )
    equal_node = _mapping(
        fresh.get("equalNodeGate"), "preregistered equal-node gate"
    )
    _expect(
        protocol["equalNode"],
        {
            **{
                key: equal_node[source]
                for key, source in (
                    ("maximumPairs", "maximumPairs"),
                    ("pairsPerPhase", "pairsPerPhase"),
                    (
                        "pairsPerPhaseAndSideToMove",
                        "pairsPerPhaseAndSideToMove",
                    ),
                    ("gamesPerPair", "gamesPerPair"),
                    ("mode", "mode"),
                    ("nodesPerMove", "nodesPerMove"),
                    (
                        "minimumPairsBeforeDecision",
                        "minimumPairsBeforeDecision",
                    ),
                    ("nullElo", "nullElo"),
                    ("alpha", "alpha"),
                    ("beta", "beta"),
                    ("promotionEValue", "promotionEValue"),
                    ("futilityEValue", "futilityEValue"),
                    ("balancedBlockChecksOnly", "balancedBlockChecksOnly"),
                    ("maximumSafetyFailures", "maximumSafetyFailures"),
                )
            },
            "initialPairBudget": INITIAL_PAIR_BUDGETS["equal-node"],
            "resumePairBudget": RESUME_PAIR_BUDGET,
        },
        "protocol/preregistration equal-node",
    )
    equal_time = _mapping(
        fresh.get("equalTimeGate"), "preregistered equal-time gate"
    )
    _expect(
        protocol["equalTime"],
        {
            **{
                key: equal_time[source]
                for key, source in (
                    ("maximumPairs", "maximumPairs"),
                    ("pairsPerPhase", "pairsPerPhase"),
                    (
                        "pairsPerPhaseAndSideToMove",
                        "pairsPerPhaseAndSideToMove",
                    ),
                    ("gamesPerPair", "gamesPerPair"),
                    ("mode", "mode"),
                    ("moveTimeMs", "moveTimeMs"),
                    ("oneGameAtATime", "oneGameAtATime"),
                    (
                        "idleMachineAttestationRequired",
                        "idleMachineAttestationRequired",
                    ),
                    (
                        "minimumPairsBeforeDecision",
                        "minimumPairsBeforeDecision",
                    ),
                    ("nullElo", "nullElo"),
                    ("alpha", "alpha"),
                    ("beta", "beta"),
                    ("promotionEValue", "promotionEValue"),
                    ("futilityEValue", "futilityEValue"),
                    ("balancedBlockChecksOnly", "balancedBlockChecksOnly"),
                    ("maximumSafetyFailures", "maximumSafetyFailures"),
                )
            },
            "initialPairBudget": INITIAL_PAIR_BUDGETS["equal-time"],
            "resumePairBudget": RESUME_PAIR_BUDGET,
        },
        "protocol/preregistration equal-time",
    )
    schedule = _mapping(fresh.get("schedule"), "preregistered schedule")
    _expect(
        fresh.get("engineAssignment"),
        {
            "sameExecutable": True,
            "candidate": {
                "UseOmegaNNUE": True,
                "OmegaNNUEFile": "exact offline-passing winner hash",
            },
            "control": {
                "UseOmegaNNUE": False,
                "OmegaNNUEFile": "<empty>",
            },
        },
        "preregistered engine assignment",
    )
    engine_options = _mapping(
        fresh.get("commonEngineOptions"), "preregistered engine options"
    )
    _expect(
        protocol["common"],
        {
            "balancedPairBlockSize": schedule["balancedPairBlockSize"],
            "onePairFromEachPhasePerBlock": schedule[
                "onePairFromEachPhasePerBlock"
            ],
            "rootSideToMoveConstantWithinBlock": schedule[
                "rootSideToMoveConstantWithinBlock"
            ],
            "rootSideToMoveAlternatesBetweenBlocks": schedule[
                "rootSideToMoveAlternatesBetweenBlocks"
            ],
            "inverseArrangeForSeededDotNetRandomShuffle": schedule[
                "inverseArrangeForSeededDotNetRandomShuffle"
            ],
            "pairBudgetRequired": True,
            "pairBudgetMustBeMultipleOf": 4,
            "executionOnlyThroughAdapterLaunch": True,
            "appendOnlyLaunchIntentCompletionChainRequired": True,
            "assessmentRequiredBetweenLaunches": True,
            "assessmentProgressDeltaBoundedByPairBudget": True,
            "authorizationRepairIsIdempotentBeforeEvents": True,
            "managedLauncherUsesPinnedDotnetSelectionNamespaces": True,
            "managedLauncherSanitizesAmbientDotnetEnvironment": True,
            "managedLauncherRehashesAppBundleBeforeAndAfter": True,
            "freshProcessPerGame": engine_options["freshProcessPerGame"],
            "repeats": engine_options["repeats"],
            "maxPlies": engine_options["maxPlies"],
            "absoluteMaxPlies": engine_options["absoluteMaxPlies"],
            "stopGraceMs": engine_options["stopGraceMs"],
        },
        "protocol/preregistration common match degrees",
    )
    _expect(
        core.ENGINE_OPTIONS,
        {
            "Threads": "1",
            "Hash": "128",
            "Ponder": "false",
            "OwnBook": "false",
            "UCI_Chess960": "false",
            "UCI_Variant": "omega",
        },
        "installed common match engine options",
    )
    return {
        "adapterContract": core._identity(CONTRACT_PATH),
        "matchProtocol": core._identity(MATCH_PROTOCOL),
        "compatibilityCore": core._identity(Path(core.__file__)),
        "readinessWrapper": core._identity(Path(readiness.__file__)),
        "offlineWrapper": core._identity(Path(offline.__file__)),
        "trainingOrchestrator": core._identity(Path(training.__file__)),
        "prelabelOrchestrator": core._identity(Path(prelabel.__file__)),
        "preregistration": core._identity(PREREGISTRATION),
        "amendment001": amendment_chain[0],
        "amendment002": amendment_chain[1],
        "amendment003": amendment_chain[2],
    }


def _install_profile() -> None:
    specs = copy.deepcopy(_ORIGINAL_SPECS)
    if set(specs) != set(EXPECTED_SEEDS):
        raise ValueError("compatibility match gate inventory changed")
    for gate, seed in EXPECTED_SEEDS.items():
        specs[gate]["seed"] = seed
    core.GATE_SPECS = specs
    core.SAMPLER_TRAJECTORY_PAIRS = 4096

    def validate(path: Path) -> dict[str, Any]:
        if _resolve(path) != _resolve(MATCH_PROTOCOL):
            raise ValueError(
                f"generation-3 requires match protocol {_resolve(MATCH_PROTOCOL)}"
            )
        return _validate_match_protocol()

    core._validate_protocol = validate


def _verify_engine() -> dict[str, Any]:
    actual = core._identity(ENGINE_EXECUTABLE)
    expected = {
        "path": str(_resolve(ENGINE_EXECUTABLE)),
        "bytes": EXPECTED_ENGINE["bytes"],
        "sha256": EXPECTED_ENGINE["sha256"],
    }
    if actual != expected:
        raise ValueError(
            "architecture-3 match executable identity changed: "
            f"expected {expected}, got {actual}"
        )
    return actual


def _authority_paths() -> dict[str, Path]:
    return {
        "adapter": Path(__file__).resolve(),
        "adapterContract": CONTRACT_PATH,
        "matchProtocol": MATCH_PROTOCOL,
        "compatibilityCore": Path(core.__file__).resolve(),
        "readinessWrapper": Path(readiness.__file__).resolve(),
        "offlineWrapper": Path(offline.__file__).resolve(),
        "trainingOrchestrator": Path(training.__file__).resolve(),
        "prelabelOrchestrator": Path(prelabel.__file__).resolve(),
        "preregistration": PREREGISTRATION,
        "amendment001": AMENDMENT_001,
        "amendment002": AMENDMENT_002,
        "amendment003": AMENDMENT_003,
        "prelabelSeal": prelabel.PRELABEL_SEAL,
        "readinessSeal": READINESS_SEAL,
        "historySnapshot": HISTORY_SNAPSHOT,
        "historyManifest": HISTORY_MANIFEST,
        "openingReplaySnapshot": OPENING_REPLAY_SNAPSHOT,
        "openingReplayManifest": OPENING_REPLAY_MANIFEST,
        "trainingPlan": TRAINING_PLAN,
        "validationSelection": VALIDATION_SELECTION,
        "robustnessSeal": ROBUSTNESS_SEAL,
        "offlineAccessClaim": OFFLINE_CLAIM,
        "offlineSufficientStatistics": OFFLINE_ATTESTATION,
        "offlineReport": OFFLINE_REPORT,
        "engine": ENGINE_EXECUTABLE,
        "corpus": FRESH_CORPUS,
    }


def _capture_authority() -> dict[str, dict[str, Any]]:
    return {
        name: core._identity(path)
        for name, path in _authority_paths().items()
    }


def _verify_authority_captures(
    captures: Mapping[str, Any], label: str
) -> None:
    paths = _authority_paths()
    if set(captures) != set(paths):
        raise ValueError(f"{label} authority identity inventory changed")
    for name, path in paths.items():
        if not _same_identity(captures[name], core._identity(path)):
            raise ValueError(f"{label} authority identity drift: {name}")


def _verify_offline_authority(
    *, allow_future_run: bool
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
]:
    captures = _capture_authority()
    identities = _validate_static_contract()
    prelabel._verify_seal()
    selection, plan, profile = training._verify_selection(
        VALIDATION_SELECTION
    )
    robustness, robust_selection, robust_plan, robust_profile = (
        training._verify_robustness(ROBUSTNESS_SEAL)
    )
    if (
        robust_selection != selection
        or robust_plan != plan
        or robust_profile != profile
        or robustness.get("passed") is not True
    ):
        raise ValueError("robustness authority differs from exact selection")
    ready = readiness._verify(
        READINESS_SEAL,
        allow_future_run=allow_future_run,
    )
    report = offline._verify_report(OFFLINE_REPORT, require_pass=True)
    if report.get("selectionCandidateId") != selection.get(
        "selectedCandidateId"
    ):
        raise ValueError("offline winner differs from validation winner")
    if not _same_identity(
        report.get("selectedNetwork"), selection.get("selectedNetwork")
    ):
        raise ValueError("offline network differs from validation winner")
    _verify_engine()
    _verify_authority_captures(captures, "match preauthorization")
    for name, identity in identities.items():
        if name not in captures or not _same_identity(
            identity, captures[name]
        ):
            raise ValueError(
                f"static authority differs from pre-capture: {name}"
            )
    if not allow_future_run and _resolve(FUTURE_RUN_ROOT).exists():
        raise FileExistsError(
            f"future match root must remain absent: {FUTURE_RUN_ROOT}"
        )
    return selection, plan, robustness, ready, captures


def _canonical_payload(value: Any) -> bytes:
    return core._canonical_json(value)


def _payload_identity(path: Path, value: Any) -> dict[str, Any]:
    payload = _canonical_payload(value)
    return {
        "path": str(_resolve(path)),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _compatibility_values(
    *,
    selection: Mapping[str, Any],
    plan: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[Path, dict[str, Any]]:
    corpus = core._identity(FRESH_CORPUS)
    selected_network = _mapping(
        selection.get("selectedNetwork"), "selected network"
    )
    selected_manifest = _mapping(
        selection.get("selectedManifest"), "selected manifest"
    )
    plan_value = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v1-training-plan",
        "profileId": PROFILE_ID,
        "generation3Plan": core._identity(TRAINING_PLAN),
        "identities": {
            "corpus": corpus,
            "generation3Selection": core._identity(VALIDATION_SELECTION),
        },
        "inputs": [corpus],
        "compatibilityOnly": True,
    }
    plan_identity = _payload_identity(COMPAT_PLAN, plan_value)
    manifest_value = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v3-match-manifest-compatibility",
        "profileId": PROFILE_ID,
        "generation3CandidateId": selection["selectedCandidateId"],
        "generation3Manifest": dict(selected_manifest),
        "network": dict(selected_network),
        "roundTrip": {"sha256": selected_network["sha256"]},
        "inputs": [corpus],
        "compatibilityOnly": True,
    }
    manifest_identity = _payload_identity(COMPAT_MANIFEST, manifest_value)
    selection_value = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v1-validation-selection",
        "profileId": PROFILE_ID,
        "plan": plan_identity,
        "selectedCandidateId": "K2",
        "selectedNetwork": dict(selected_network),
        "selectedManifest": manifest_identity,
        "candidates": {
            "K2": {
                "health": {"passed": True},
                "generation3CandidateId": selection["selectedCandidateId"],
            }
        },
        "decision": {
            "winner": "K2",
            "eligibility": {"K2": {"eligible": True}},
        },
        "generation3Selection": core._identity(VALIDATION_SELECTION),
        "compatibilityOnly": True,
    }
    selection_identity = _payload_identity(
        COMPAT_SELECTION, selection_value
    )
    offline_value = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v1-offline-test",
        "profileId": PROFILE_ID,
        "selection": selection_identity,
        "selectedNetwork": dict(selected_network),
        "comparisons": {
            "K0": {"passed": True, "generation3Baseline": "I0"},
            "zeroResidual": {"passed": True},
        },
        "robustnessDirectionalImprovementOverK0": True,
        "robustnessDirectionalImprovementOverZeroResidual": True,
        "passed": True,
        "generation3OfflineReport": core._identity(OFFLINE_REPORT),
        "compatibilityOnly": True,
    }
    if report.get("passed") is not True:
        raise ValueError("cannot derive compatibility files from failed offline gate")
    return {
        COMPAT_PLAN: plan_value,
        COMPAT_MANIFEST: manifest_value,
        COMPAT_SELECTION: selection_value,
        COMPAT_OFFLINE: offline_value,
    }


def _require_absent_or_empty(path: Path, label: str) -> None:
    path = _resolve(path)
    if not path.exists():
        return
    if not path.is_dir():
        raise FileExistsError(f"{label} is not a directory: {path}")
    first = next(path.iterdir(), None)
    if first is not None:
        raise FileExistsError(f"{label} must be absent or empty: {first}")


def _prepare_compatibility(
    selection: Mapping[str, Any],
    plan: Mapping[str, Any],
    report: Mapping[str, Any],
) -> None:
    _require_absent_or_empty(COMPATIBILITY_DIR, "compatibility directory")
    values = _compatibility_values(
        selection=selection, plan=plan, report=report
    )
    COMPATIBILITY_DIR.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".king-state-v3-compat-stage-",
        dir=COMPATIBILITY_DIR.parent,
    ) as raw:
        stage = Path(raw) / "publication"
        stage.mkdir()
        for public_path, value in values.items():
            core._exclusive_json(stage / public_path.name, value)
        if COMPATIBILITY_DIR.exists():
            COMPATIBILITY_DIR.rmdir()
        os.rename(stage, COMPATIBILITY_DIR)
    _verify_compatibility(selection, plan, report)


def _verify_compatibility(
    selection: Mapping[str, Any],
    plan: Mapping[str, Any],
    report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected = _compatibility_values(
        selection=selection, plan=plan, report=report
    )
    identities: list[dict[str, Any]] = []
    for path, value in expected.items():
        actual = _strict_object(path, f"compatibility file {path.name}")
        if actual != value:
            raise ValueError(f"compatibility file changed: {path}")
        identity = core._identity(path)
        if identity != _payload_identity(path, value):
            raise ValueError(f"compatibility file identity changed: {path}")
        identities.append(identity)
    return identities


def _explicit_exclusions() -> list[Path]:
    missing = [path for path in MANDATORY_EXCLUSIONS if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"mandatory historical exclusion root is absent: {missing[0]}"
        )
    return [_resolve(path) for path in MANDATORY_EXCLUSIONS]


def _coverage_inventory(
    roots: Sequence[Path], output_dir: Path
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for root in roots:
        files = core._expand_json_files([root], output_dir)
        inventory.append(
            {
                "root": str(_resolve(root)),
                "files": [core._identity(path) for path in files],
            }
        )
    return inventory


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        _resolve(candidate).relative_to(_resolve(root))
        return True
    except ValueError:
        return False


def _reconstruct_exclusion_inventory(
    roots: Sequence[Path],
    flat_identities: Any,
    *,
    verify_files: bool = True,
) -> list[dict[str, Any]]:
    if type(flat_identities) is not list:
        raise ValueError("inner audit exclusion files must be a JSON array")
    identities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ordinal, value in enumerate(flat_identities):
        identity = offline._identity(
            value, f"inner audit exclusion {ordinal + 1}"
        )
        path = _resolve(Path(identity["path"]))
        key = str(path).casefold()
        if key in seen:
            raise ValueError(
                f"inner audit repeats an exclusion identity: {path}"
            )
        seen.add(key)
        if verify_files:
            _strict_file_identity(
                identity, path, f"inner audit exclusion {ordinal + 1}"
            )
        identities.append(identity)
    identities.sort(key=lambda item: str(item["path"]).lower())

    resolved_roots = [_resolve(root) for root in roots]
    unmatched = [
        identity
        for identity in identities
        if not any(
            _is_within(Path(identity["path"]), root)
            for root in resolved_roots
        )
    ]
    if unmatched:
        raise ValueError(
            "inner audit has an exclusion outside every fixed root: "
            f"{unmatched[0]['path']}"
        )
    return [
        {
            "root": str(root),
            "files": [
                dict(identity)
                for identity in identities
                if _is_within(Path(identity["path"]), root)
            ],
        }
        for root in resolved_roots
    ]


def _expected_exclusion_inventory(
    inner: Mapping[str, Any],
) -> list[dict[str, Any]]:
    audit_identity = offline._identity(
        inner.get("audit"), "inner match audit"
    )
    audit_path = _resolve(Path(audit_identity["path"]))
    _strict_file_identity(audit_identity, audit_path, "inner match audit")
    audit = _strict_object(audit_path, "inner match audit")
    exclusions = _mapping(
        audit.get("exclusions"), "inner match audit exclusions"
    )
    return _reconstruct_exclusion_inventory(
        [
            *MANDATORY_EXCLUSIONS,
            HISTORY_SNAPSHOT,
            OPENING_REPLAY_SNAPSHOT,
        ],
        exclusions.get("files"),
    )


def _outer_value(
    *,
    selection: Mapping[str, Any],
    authority: Mapping[str, Any],
    compatibility: Sequence[Mapping[str, Any]],
    inner: Path,
    exclusions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    selected_manifest = _mapping(
        selection.get("selectedManifest"), "selected manifest"
    )
    selected_network = _mapping(
        selection.get("selectedNetwork"), "selected network"
    )
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": OUTER_SEAL_KIND,
        "profileId": PROFILE_ID,
        "createdFromOfflinePass": True,
        "seeds": dict(EXPECTED_SEEDS),
        "gateOrder": list(GATE_ORDER),
        "assessmentEvidence": _assessment_evidence_contract(),
        "matchOutputRoot": str(_resolve(FUTURE_RUN_ROOT)),
        "engine": dict(authority["engine"]),
        "adapter": dict(authority["adapter"]),
        "adapterContract": dict(authority["adapterContract"]),
        "matchProtocol": dict(authority["matchProtocol"]),
        "compatibilityCore": dict(authority["compatibilityCore"]),
        "readinessWrapper": dict(authority["readinessWrapper"]),
        "offlineWrapper": dict(authority["offlineWrapper"]),
        "trainingOrchestrator": dict(authority["trainingOrchestrator"]),
        "prelabelOrchestrator": dict(authority["prelabelOrchestrator"]),
        "preregistration": dict(authority["preregistration"]),
        "amendmentChain": [
            dict(authority["amendment001"]),
            dict(authority["amendment002"]),
            dict(authority["amendment003"]),
        ],
        "prelabelSeal": dict(authority["prelabelSeal"]),
        "readinessSeal": dict(authority["readinessSeal"]),
        "historySnapshot": dict(authority["historySnapshot"]),
        "historyManifest": dict(authority["historyManifest"]),
        "openingReplaySnapshot": dict(
            authority["openingReplaySnapshot"]
        ),
        "openingReplayManifest": dict(
            authority["openingReplayManifest"]
        ),
        "trainingPlan": dict(authority["trainingPlan"]),
        "validationSelection": dict(authority["validationSelection"]),
        "robustnessSeal": dict(authority["robustnessSeal"]),
        "offlineAccessClaim": dict(authority["offlineAccessClaim"]),
        "offlineSufficientStatistics": dict(
            authority["offlineSufficientStatistics"]
        ),
        "offlineReport": dict(authority["offlineReport"]),
        "selectedNetwork": dict(selected_network),
        "selectedManifest": dict(selected_manifest),
        "compatibilityFiles": [dict(item) for item in compatibility],
        "innerCoreSeal": core._identity(inner),
        "explicitExclusionInventory": list(exclusions),
        "contracts": dict(CONTRACTS),
    }
    _expect(set(value), OUTER_FIELDS, "outer seal fields")
    return value


def _publish_outer(
    value: Mapping[str, Any],
    verifier: Callable[[Path], Any],
) -> None:
    path = _resolve(OUTER_SEAL)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _resolve(FUTURE_RUN_ROOT).exists():
        raise FileExistsError("future match root exists before outer seal")
    descriptor = -1
    published_stat: os.stat_result | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        published_stat = os.fstat(descriptor)
        remaining = memoryview(core._canonical_json(dict(value)))
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("outer seal write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        if _resolve(FUTURE_RUN_ROOT).exists():
            raise FileExistsError("future match root appeared during outer seal")
        verifier(path)
        if not os.path.samestat(published_stat, path.stat()):
            raise ValueError("outer seal was replaced during verification")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_outer(
    path: Path = OUTER_SEAL, *, allow_future_run: bool = True
) -> dict[str, Any]:
    if _resolve(path) != _resolve(OUTER_SEAL):
        raise ValueError(f"outer seal path must be {_resolve(OUTER_SEAL)}")
    value = _strict_object(path, "generation-3 outer match seal")
    _expect(set(value), OUTER_FIELDS, "outer seal fields")
    _expect(value.get("schemaVersion"), 1, "outer seal schema")
    _expect(value.get("kind"), OUTER_SEAL_KIND, "outer seal kind")
    _expect(value.get("profileId"), PROFILE_ID, "outer seal profile")
    _expect(
        value.get("createdFromOfflinePass"),
        True,
        "outer seal offline-pass provenance",
    )
    _expect(value.get("seeds"), EXPECTED_SEEDS, "outer seal seeds")
    _expect(value.get("gateOrder"), list(GATE_ORDER), "outer seal gate order")
    _expect(
        value.get("assessmentEvidence"),
        _assessment_evidence_contract(),
        "outer seal assessment evidence",
    )
    _expect(value.get("contracts"), CONTRACTS, "outer seal contracts")
    _expect(
        value.get("matchOutputRoot"),
        str(_resolve(FUTURE_RUN_ROOT)),
        "outer seal future match root",
    )
    if not allow_future_run and _resolve(FUTURE_RUN_ROOT).exists():
        raise ValueError("future match root appeared before outer seal verification")
    selection, plan, _robustness, _ready, authority = (
        _verify_offline_authority(allow_future_run=allow_future_run)
    )
    report = offline._verify_report(OFFLINE_REPORT, require_pass=True)
    compatibility = _verify_compatibility(selection, plan, report)
    expected_identities = {
        "engine": core._identity(ENGINE_EXECUTABLE),
        "adapter": core._identity(Path(__file__)),
        "adapterContract": core._identity(CONTRACT_PATH),
        "matchProtocol": core._identity(MATCH_PROTOCOL),
        "compatibilityCore": core._identity(Path(core.__file__)),
        "readinessWrapper": core._identity(Path(readiness.__file__)),
        "offlineWrapper": core._identity(Path(offline.__file__)),
        "trainingOrchestrator": core._identity(Path(training.__file__)),
        "prelabelOrchestrator": core._identity(Path(prelabel.__file__)),
        "preregistration": core._identity(PREREGISTRATION),
        "prelabelSeal": core._identity(prelabel.PRELABEL_SEAL),
        "readinessSeal": core._identity(READINESS_SEAL),
        "historySnapshot": core._identity(HISTORY_SNAPSHOT),
        "historyManifest": core._identity(HISTORY_MANIFEST),
        "openingReplaySnapshot": core._identity(
            OPENING_REPLAY_SNAPSHOT
        ),
        "openingReplayManifest": core._identity(
            OPENING_REPLAY_MANIFEST
        ),
        "trainingPlan": core._identity(TRAINING_PLAN),
        "validationSelection": core._identity(VALIDATION_SELECTION),
        "robustnessSeal": core._identity(ROBUSTNESS_SEAL),
        "offlineAccessClaim": core._identity(OFFLINE_CLAIM),
        "offlineSufficientStatistics": core._identity(OFFLINE_ATTESTATION),
        "offlineReport": core._identity(OFFLINE_REPORT),
    }
    for name, expected in expected_identities.items():
        if not _same_identity(value.get(name), expected):
            raise ValueError(f"outer seal identity drift: {name}")
        core._verify_identity(value[name], f"outer seal {name}")
    amendment_chain = readiness._amendment_chain_identities()
    if not _strict_equal(value.get("amendmentChain"), amendment_chain):
        raise ValueError("outer seal amendment chain drift")
    for ordinal, identity in enumerate(amendment_chain, start=1):
        core._verify_identity(identity, f"outer seal amendment-{ordinal:03d}")
    if value.get("compatibilityFiles") != compatibility:
        raise ValueError("outer seal compatibility inventory changed")
    if not _same_identity(
        value.get("selectedNetwork"), selection.get("selectedNetwork")
    ):
        raise ValueError("outer seal selected network changed")
    if not _same_identity(
        value.get("selectedManifest"), selection.get("selectedManifest")
    ):
        raise ValueError("outer seal selected manifest changed")
    inner_identity = _strict_file_identity(
        value.get("innerCoreSeal"), INNER_SEAL, "inner core seal"
    )
    inner_path = core._verify_identity(
        dict(inner_identity), "inner core seal"
    )
    _install_profile()
    inner = core._verify_seal(inner_path)
    if inner.get("candidateNetworkSha256") != value["selectedNetwork"]["sha256"]:
        raise ValueError("inner core seal uses a different candidate network")
    if inner.get("engineExecutableSha256") != EXPECTED_ENGINE["sha256"]:
        raise ValueError("inner core seal uses a different executable")
    expected_exclusions = _expected_exclusion_inventory(inner)
    _expect(
        value.get("explicitExclusionInventory"),
        expected_exclusions,
        "outer explicit exclusion inventory",
    )
    return value


def _sample() -> None:
    prelabel._verify_prebuilt_tooling()
    _verify_offline_authority(allow_future_run=False)
    _require_absent_or_empty(SAMPLER_DIR, "generation-3 sampler directory")
    _install_profile()
    core._sample(
        argparse.Namespace(
            output_dir=SAMPLER_DIR,
            dotnet=DOTNET_HOST,
            sampler_project=core._sampler_project_path(),
            sampler_assembly=prelabel.ROOT_SAMPLER_ASSEMBLY,
            managed_runner=lambda command, **kwargs: (
                prelabel._run_pinned_dotnet(
                    command,
                    runtime_bundle_pin=prelabel.ROOT_SAMPLER_PINS[
                        "appLocalRuntimeBundle"
                    ],
                    **kwargs,
                )
            ),
            protocol=MATCH_PROTOCOL,
        )
    )


def _seal() -> None:
    prelabel._verify_prebuilt_tooling()
    selection, plan, _robustness, _ready, authority = (
        _verify_offline_authority(allow_future_run=False)
    )
    report = offline._verify_report(OFFLINE_REPORT, require_pass=True)
    if _resolve(FUTURE_RUN_ROOT).exists():
        raise FileExistsError("future match root exists before match sealing")
    _require_absent_or_empty(SEALED_DIR, "generation-3 sealed directory")
    _prepare_compatibility(selection, plan, report)
    compatibility = _verify_compatibility(selection, plan, report)
    exclusions = _explicit_exclusions()
    exclusion_inventory = _coverage_inventory(
        [
            *exclusions,
            HISTORY_SNAPSHOT,
            OPENING_REPLAY_SNAPSHOT,
        ],
        SEALED_DIR,
    )
    selected_network = Path(str(selection["selectedNetwork"]["path"]))
    _install_profile()
    inner = core._seal(
        argparse.Namespace(
            output_dir=SEALED_DIR,
            match_output_root=FUTURE_RUN_ROOT,
            sampler_dir=SAMPLER_DIR,
            sampler_project=core._sampler_project_path(),
            protocol=MATCH_PROTOCOL,
            engine_executable=ENGINE_EXECUTABLE,
            candidate_network=selected_network,
            candidate_manifest=COMPAT_MANIFEST,
            training_selection=[
                COMPAT_PLAN,
                COMPAT_SELECTION,
                COMPAT_OFFLINE,
            ],
            training_corpus=[FRESH_CORPUS],
            omega_match_assembly=OMEGA_MATCH_ASSEMBLY,
            exclude=[
                *exclusions,
                HISTORY_SNAPSHOT,
                OPENING_REPLAY_SNAPSHOT,
            ],
        ),
        default_exclusions=False,
    )
    if _resolve(FUTURE_RUN_ROOT).exists():
        raise FileExistsError("future match root appeared during inner sealing")
    (
        repeated_selection,
        repeated_plan,
        _repeated_robustness,
        _repeated_ready,
        repeated_authority,
    ) = _verify_offline_authority(allow_future_run=False)
    if (
        not _strict_equal(repeated_selection, selection)
        or not _strict_equal(repeated_plan, plan)
        or not _strict_equal(repeated_authority, authority)
    ):
        raise ValueError(
            "match authority changed between pre-capture and outer sealing"
        )
    value = _outer_value(
        selection=selection,
        authority=repeated_authority,
        compatibility=compatibility,
        inner=inner,
        exclusions=exclusion_inventory,
    )
    _publish_outer(
        value,
        lambda path: _verify_outer(path, allow_future_run=False),
    )
    print(f"Published generation-3 match seal: {_resolve(OUTER_SEAL)}", flush=True)


def _gate_root(gate: str) -> Path:
    if type(gate) is not str or gate not in GATE_ORDER:
        raise ValueError(f"unknown generation-3 match gate: {gate!r}")
    return _resolve(FUTURE_RUN_ROOT / gate)


def _gate_events(gate: str) -> Path:
    return _gate_root(gate) / "events.jsonl"


def _gate_assessment_dir(gate: str) -> Path:
    return _gate_root(gate) / "assessments"


def _gate_launch_dir(gate: str) -> Path:
    return _gate_root(gate) / "launches"


def _gate_launch_intent_path(gate: str, sequence: int) -> Path:
    if type(sequence) is not int or sequence <= 0:
        raise ValueError("launch sequence must be a positive integer")
    return _gate_launch_dir(gate) / f"{sequence:06d}.intent.json"


def _gate_launch_completion_path(gate: str, sequence: int) -> Path:
    if type(sequence) is not int or sequence <= 0:
        raise ValueError("launch sequence must be a positive integer")
    return _gate_launch_dir(gate) / f"{sequence:06d}.completion.json"


def _gate_assessment_path(gate: str, sequence: int) -> Path:
    if type(sequence) is not int or sequence <= 0:
        raise ValueError("assessment sequence must be a positive integer")
    return _gate_assessment_dir(gate) / f"{sequence:06d}.json"


def _gate_decision_path(gate: str) -> Path:
    return _gate_root(gate) / "decision.json"


def _gate_authorization_path(gate: str) -> Path:
    if gate == "development":
        raise ValueError("development has no predecessor authorization")
    return _gate_root(gate) / "authorization.json"


def _idle_attestation_path() -> Path:
    return _gate_root("equal-time") / "idle-machine.attestation.json"


def _successor(gate: str) -> str:
    index = GATE_ORDER.index(gate)
    return GATE_ORDER[index + 1] if index + 1 < len(GATE_ORDER) else "promotion"


def _strict_nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative JSON integer")
    return value


def _strict_utc(value: Any, label: str) -> Any:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"{label} must be a UTC JSON string")
    return core._parse_utc(value)


def _require_idle_not_before_predecessor(
    idle_created_utc: Any,
    predecessor_created_utc: Any,
    *,
    label: str,
) -> None:
    idle_created = _strict_utc(
        idle_created_utc, f"{label} idle createdUtc"
    )
    predecessor_created = _strict_utc(
        predecessor_created_utc,
        f"{label} predecessor decision createdUtc",
    )
    if idle_created < predecessor_created:
        raise ValueError(
            "equal-time idle attestation predates the equal-node decision"
        )


def _canonical_argument(
    supplied: Path | None, expected: Path, label: str
) -> Path:
    expected = _resolve(expected)
    if supplied is not None and _resolve(supplied) != expected:
        raise ValueError(f"{label} must be the canonical path {expected}")
    return expected


def _same_or_none_identity(
    actual: Any, expected: Mapping[str, Any] | None, label: str
) -> None:
    if expected is None:
        if actual is not None:
            raise ValueError(f"{label} must be JSON null")
        return
    if not _same_identity(actual, expected):
        raise ValueError(f"{label} identity changed")


def _verify_event_prefix(
    events_path: Path,
    recorded: Mapping[str, Any],
    *,
    exact: bool,
    label: str,
) -> None:
    identity = offline._identity(recorded, label)
    events_path = _resolve(events_path)
    if _resolve(Path(identity["path"])) != events_path:
        raise ValueError(f"{label} names a noncanonical event log")
    current_size = events_path.stat().st_size
    recorded_size = int(identity["bytes"])
    if current_size < recorded_size or (exact and current_size != recorded_size):
        raise ValueError(f"{label} event log was truncated or extended")
    digest = hashlib.sha256()
    remaining = recorded_size
    with events_path.open("rb") as stream:
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError(f"{label} event log ended inside its prefix")
            digest.update(block)
            remaining -= len(block)
    if digest.hexdigest() != str(identity["sha256"]).lower():
        raise ValueError(f"{label} event log is not append-only")


def _core_decision(
    gate: str, report: Mapping[str, Any]
) -> tuple[str, Mapping[str, Any]]:
    if gate == "development":
        section = _mapping(
            report.get("developmentScreen"), "development screen"
        )
        decision = section.get("decision")
        allowed = TERMINAL_DECISIONS[gate] | {"continue", "in-progress"}
    else:
        section = _mapping(
            report.get("sequentialGate"), f"{gate} sequential gate"
        )
        decision = section.get("decision")
        allowed = TERMINAL_DECISIONS[gate] | {"continue", "in-progress"}
    if type(decision) is not str or decision not in allowed:
        raise ValueError(f"{gate} core assessment has an unknown decision")
    return decision, section


def _validated_progress(value: Any, label: str) -> dict[str, Any]:
    progress = _mapping(value, label)
    expected = {
        "finishedGames",
        "completePairs",
        "incompleteOpenings",
        "inProgressAttempts",
        "completeBalancedPrefixBlocks",
        "balancedPrefixPairs",
        "completePairsOutsideBalancedPrefix",
    }
    _expect(set(progress), expected, f"{label} fields")
    for field in (
        "finishedGames",
        "completePairs",
        "completeBalancedPrefixBlocks",
        "balancedPrefixPairs",
        "completePairsOutsideBalancedPrefix",
    ):
        _strict_nonnegative_int(progress.get(field), f"{label}.{field}")
    if type(progress.get("incompleteOpenings")) is not list or not all(
        type(value) is str and value
        for value in progress["incompleteOpenings"]
    ):
        raise ValueError(f"{label}.incompleteOpenings is malformed")
    attempts = progress.get("inProgressAttempts")
    if type(attempts) is not list:
        raise ValueError(f"{label}.inProgressAttempts must be a JSON array")
    for ordinal, item in enumerate(attempts):
        attempt = _mapping(item, f"{label}.inProgressAttempts[{ordinal}]")
        _expect(
            set(attempt),
            {"gameId", "attempt"},
            f"{label}.inProgressAttempts[{ordinal}] fields",
        )
        if type(attempt.get("gameId")) is not str or not attempt["gameId"]:
            raise ValueError(f"{label} has an invalid in-progress game ID")
        _strict_nonnegative_int(
            attempt.get("attempt"), f"{label} in-progress attempt"
        )
    return progress


def _progress_key(progress: Mapping[str, Any]) -> tuple[int, int, int]:
    return (
        int(progress["finishedGames"]),
        int(progress["completePairs"]),
        int(progress["balancedPrefixPairs"]),
    )


def _validate_core_assessment(
    value: Any,
    *,
    gate: str,
    inner_identity: Mapping[str, Any],
    events_identity: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    report = _mapping(value, f"{gate} core assessment")
    fields = {
        "schemaVersion",
        "kind",
        "createdUtc",
        "gate",
        "seal",
        "events",
        "runId",
        "progress",
        "safety",
    }
    fields.add("developmentScreen" if gate == "development" else "sequentialGate")
    if gate == "equal-time":
        fields.update({"idleMachine", "equalTimeAudit"})
    _expect(set(report), fields, f"{gate} core assessment fields")
    _expect(report.get("schemaVersion"), 1, f"{gate} core assessment schema")
    _expect(
        report.get("kind"),
        "omega-nnue-king-state-v1-match-assessment",
        f"{gate} core assessment kind",
    )
    _expect(report.get("gate"), gate, f"{gate} core assessment gate")
    _strict_utc(report.get("createdUtc"), f"{gate} core assessment createdUtc")
    if type(report.get("runId")) is not str or not report["runId"]:
        raise ValueError(f"{gate} core assessment runId is invalid")
    if not _same_identity(report.get("seal"), inner_identity):
        raise ValueError(f"{gate} core assessment inner seal changed")
    if not _same_identity(report.get("events"), events_identity):
        raise ValueError(f"{gate} core assessment events changed")
    _validated_progress(
        report.get("progress"), f"{gate} core progress"
    )
    safety = _mapping(report.get("safety"), f"{gate} core safety")
    _expect(
        set(safety),
        {
            "illegalMoves",
            "illegalPvs",
            "protocolFailures",
            "timeForfeits",
            "abandonedAttempts",
            "failures",
            "passes",
        },
        f"{gate} core safety fields",
    )
    for field in (
        "illegalMoves",
        "illegalPvs",
        "protocolFailures",
        "timeForfeits",
        "abandonedAttempts",
        "failures",
    ):
        _strict_nonnegative_int(safety.get(field), f"{gate} safety.{field}")
    if type(safety.get("passes")) is not bool:
        raise ValueError(f"{gate} safety.passes must be a JSON boolean")
    decision, _section = _core_decision(gate, report)
    return dict(report), decision


def _core_semantics(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in report.items()
        if key != "createdUtc"
    }


def _verify_terminal_core_assessment(
    report: Mapping[str, Any],
    *,
    gate: str,
    inner_identity: Mapping[str, Any],
    events_identity: Mapping[str, Any],
) -> None:
    recorded, recorded_decision = _validate_core_assessment(
        report,
        gate=gate,
        inner_identity=inner_identity,
        events_identity=events_identity,
    )
    if recorded_decision not in TERMINAL_DECISIONS[gate]:
        raise ValueError(
            f"{gate} terminal semantic verification received "
            "a nonterminal assessment"
        )

    inner = offline._identity(
        inner_identity, f"{gate} terminal inner seal"
    )
    inner_path = _resolve(Path(inner["path"]))
    _strict_file_identity(
        inner, inner_path, f"{gate} terminal inner seal"
    )
    _install_profile()
    sealed = core._verify_seal(inner_path)
    gates = _mapping(sealed.get("gates"), f"{gate} inner seal gates")
    gate_entry = _mapping(
        gates.get(gate), f"{gate} inner seal gate entry"
    )
    expected_run_id = gate_entry.get("runId")
    if type(expected_run_id) is not str or not expected_run_id:
        raise ValueError(f"{gate} inner seal runId is invalid")
    _expect(
        recorded.get("runId"),
        expected_run_id,
        f"{gate} core assessment runId",
    )

    events = offline._identity(
        events_identity, f"{gate} terminal events"
    )
    events_path = _resolve(Path(events["path"]))
    _strict_file_identity(
        events, events_path, f"{gate} terminal events"
    )
    idle_path = _idle_attestation_path() if gate == "equal-time" else None
    with tempfile.TemporaryDirectory(
        prefix=f"omega-king-state-v3-{gate}-terminal-recheck-"
    ) as raw:
        output = Path(raw) / "core-assessment.json"
        with contextlib.redirect_stdout(io.StringIO()):
            recomputed = core._assess(
                argparse.Namespace(
                    seal=inner_path,
                    gate=gate,
                    events=events_path,
                    output=output,
                    idle_attestation=idle_path,
                )
            )
        disk = _strict_object(
            output, f"{gate} recomputed terminal assessment"
        )
        if not _strict_equal(recomputed, disk):
            raise ValueError(
                f"{gate} recomputed terminal assessment changed on disk"
            )
    _strict_file_identity(
        inner, inner_path, f"{gate} terminal inner seal after assessment"
    )
    _strict_file_identity(
        events, events_path, f"{gate} terminal events after assessment"
    )
    authoritative, authoritative_decision = _validate_core_assessment(
        recomputed,
        gate=gate,
        inner_identity=inner,
        events_identity=events,
    )
    _expect(
        authoritative_decision,
        recorded_decision,
        f"{gate} terminal decision",
    )
    _expect(
        _core_semantics(recorded),
        _core_semantics(authoritative),
        f"{gate} terminal core assessment semantics",
    )


def _authorization_value(
    *,
    gate: str,
    outer_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any],
    idle_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUTHORIZATION_KIND,
        "profileId": PROFILE_ID,
        "gate": gate,
        "createdUtc": core._utc_now(),
        "outerSeal": dict(outer_identity),
        "predecessorDecision": dict(predecessor_identity),
        "idleMachineAttestation": (
            None if idle_identity is None else dict(idle_identity)
        ),
        "eventsAbsentAtAuthorization": True,
    }


def _validated_equal_time_idle_record(label: str) -> dict[str, Any]:
    outer_value = _strict_object(OUTER_SEAL, f"{label} outer match seal")
    inner_path = core._verify_identity(
        _mapping(
            outer_value.get("innerCoreSeal"), f"{label} inner match seal"
        ),
        f"{label} inner match seal",
    )
    _install_profile()
    inner = core._verify_seal(inner_path)
    return core._validate_idle_attestation(
        _idle_attestation_path(),
        str(inner["gates"]["equal-time"]["runId"]),
    )


def _verify_authorization(
    gate: str,
    *,
    outer_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any],
) -> dict[str, Any]:
    path = _gate_authorization_path(gate)
    value = _strict_object(path, f"{gate} authorization")
    _expect(set(value), AUTHORIZATION_FIELDS, f"{gate} authorization fields")
    _expect(value.get("schemaVersion"), 1, f"{gate} authorization schema")
    _expect(value.get("kind"), AUTHORIZATION_KIND, f"{gate} authorization kind")
    _expect(value.get("profileId"), PROFILE_ID, f"{gate} authorization profile")
    _expect(value.get("gate"), gate, f"{gate} authorization gate")
    created = _strict_utc(
        value.get("createdUtc"), f"{gate} authorization createdUtc"
    )
    if value.get("eventsAbsentAtAuthorization") is not True:
        raise ValueError(f"{gate} authorization absence claim changed")
    if not _same_identity(value.get("outerSeal"), outer_identity):
        raise ValueError(f"{gate} authorization outer seal changed")
    if _resolve(Path(str(outer_identity["path"]))) != _resolve(OUTER_SEAL):
        raise ValueError(f"{gate} authorization names a noncanonical outer seal")
    core._verify_identity(
        _mapping(value["outerSeal"], f"{gate} authorization outer seal"),
        f"{gate} authorization outer seal",
    )
    if not _same_identity(
        value.get("predecessorDecision"), predecessor_identity
    ):
        raise ValueError(f"{gate} authorization predecessor changed")
    predecessor_path = core._verify_identity(
        dict(predecessor_identity), f"{gate} authorization predecessor"
    )
    predecessor_gate = GATE_PREDECESSOR[gate]
    if (
        predecessor_gate is None
        or predecessor_path != _gate_decision_path(predecessor_gate)
    ):
        raise ValueError(f"{gate} authorization predecessor path changed")
    predecessor = _strict_object(
        predecessor_path, f"{gate} authorization predecessor"
    )
    if (
        predecessor.get("kind") != DECISION_KIND
        or predecessor.get("gate") != predecessor_gate
        or predecessor.get("passed") is not True
        or predecessor.get("decision")
        != SUCCESS_DECISIONS[predecessor_gate]
    ):
        raise ValueError(f"{gate} authorization predecessor did not pass")
    if _strict_utc(
        predecessor.get("createdUtc"),
        f"{gate} predecessor decision createdUtc",
    ) > created:
        raise ValueError(f"{gate} authorization predates its predecessor")
    idle = value.get("idleMachineAttestation")
    if gate == "equal-node":
        if idle is not None:
            raise ValueError("equal-node authorization must not have idle evidence")
    elif gate == "equal-time":
        expected_idle = core._identity(_idle_attestation_path())
        if not _same_identity(idle, expected_idle):
            raise ValueError("equal-time authorization idle evidence changed")
        if not _same_identity(
            core._identity(OUTER_SEAL), outer_identity
        ):
            raise ValueError("authorization outer match seal bytes changed")
        idle_record = _validated_equal_time_idle_record("authorization")
        idle_created = core._parse_utc(
            str(idle_record["attestation"]["createdUtc"])
        )
        _require_idle_not_before_predecessor(
            idle_record["attestation"]["createdUtc"],
            predecessor.get("createdUtc"),
            label="equal-time authorization",
        )
        if idle_created > created:
            raise ValueError(
                "equal-time authorization predates its idle attestation"
            )
    else:
        raise AssertionError(gate)
    return value


def _publish_authorization(
    gate: str,
    *,
    outer_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any],
    idle_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if gate == "equal-time":
        if idle_identity is None:
            raise ValueError(
                "equal-time authorization requires idle-attestation evidence"
            )
        if not _same_identity(
            idle_identity, core._identity(_idle_attestation_path())
        ):
            raise ValueError(
                "equal-time authorization idle-attestation identity changed"
            )
        predecessor_path = core._verify_identity(
            dict(predecessor_identity),
            "equal-time authorization predecessor preflight",
        )
        predecessor = _strict_object(
            predecessor_path,
            "equal-time authorization predecessor preflight",
        )
        idle_record = _validated_equal_time_idle_record(
            "equal-time authorization preflight"
        )
        _require_idle_not_before_predecessor(
            idle_record["attestation"]["createdUtc"],
            predecessor.get("createdUtc"),
            label="equal-time authorization preflight",
        )
    events = _gate_events(gate)
    if events.exists():
        raise FileExistsError(
            f"{gate} event log predates its predecessor authorization"
        )
    if _gate_decision_path(gate).exists():
        raise FileExistsError(
            f"{gate} decision predates its predecessor authorization"
        )
    if _gate_assessment_dir(gate).exists():
        raise FileExistsError(
            f"{gate} assessment history predates its authorization"
        )
    if _gate_launch_dir(gate).exists():
        raise FileExistsError(
            f"{gate} launch evidence predates its authorization"
        )
    path = _gate_authorization_path(gate)
    value = _authorization_value(
        gate=gate,
        outer_identity=outer_identity,
        predecessor_identity=predecessor_identity,
        idle_identity=idle_identity,
    )
    core._exclusive_json(path, value)
    published = _verify_authorization(
        gate,
        outer_identity=outer_identity,
        predecessor_identity=predecessor_identity,
    )
    if not _strict_equal(published, value):
        raise ValueError(f"{gate} authorization changed during publication")
    return published


def _assessment_value(
    *,
    gate: str,
    sequence: int,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
    events_identity: Mapping[str, Any],
    prior_identity: Mapping[str, Any] | None,
    predecessor_identity: Mapping[str, Any] | None,
    authorization_identity: Mapping[str, Any] | None,
    launch_completion_identity: Mapping[str, Any],
    core_report: Mapping[str, Any],
    decision: str,
) -> dict[str, Any]:
    terminal = decision in TERMINAL_DECISIONS[gate]
    success = terminal and decision == SUCCESS_DECISIONS[gate]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ASSESSMENT_KIND,
        "profileId": PROFILE_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": core._utc_now(),
        "outerSeal": dict(outer_identity),
        "innerCoreSeal": dict(inner_identity),
        "events": dict(events_identity),
        "priorAssessment": (
            None if prior_identity is None else dict(prior_identity)
        ),
        "predecessorDecision": (
            None
            if predecessor_identity is None
            else dict(predecessor_identity)
        ),
        "gateAuthorization": (
            None
            if authorization_identity is None
            else dict(authorization_identity)
        ),
        "launchCompletion": dict(launch_completion_identity),
        "coreAssessment": copy.deepcopy(dict(core_report)),
        "progress": copy.deepcopy(dict(core_report["progress"])),
        "decision": decision,
        "terminal": terminal,
        "authorizedSuccessor": _successor(gate) if success else None,
    }


def _validate_assessment(
    path: Path,
    *,
    gate: str,
    sequence: int,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
    prior_identity: Mapping[str, Any] | None,
    predecessor_identity: Mapping[str, Any] | None,
    authorization_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    expected_path = _gate_assessment_path(gate, sequence)
    if _resolve(path) != expected_path:
        raise ValueError(f"{gate} assessment path is not canonical")
    value = _strict_object(path, f"{gate} assessment {sequence}")
    _expect(
        set(value), ASSESSMENT_FIELDS, f"{gate} assessment {sequence} fields"
    )
    _expect(value.get("schemaVersion"), 1, f"{gate} assessment schema")
    _expect(value.get("kind"), ASSESSMENT_KIND, f"{gate} assessment kind")
    _expect(value.get("profileId"), PROFILE_ID, f"{gate} assessment profile")
    _expect(value.get("gate"), gate, f"{gate} assessment gate")
    _expect(value.get("sequence"), sequence, f"{gate} assessment sequence")
    created = _strict_utc(
        value.get("createdUtc"), f"{gate} assessment createdUtc"
    )
    if not _same_identity(value.get("outerSeal"), outer_identity):
        raise ValueError(f"{gate} assessment outer seal changed")
    if not _same_identity(value.get("innerCoreSeal"), inner_identity):
        raise ValueError(f"{gate} assessment inner seal changed")
    core._verify_identity(
        _mapping(value["outerSeal"], f"{gate} assessment outer seal"),
        f"{gate} assessment outer seal",
    )
    core._verify_identity(
        _mapping(value["innerCoreSeal"], f"{gate} assessment inner seal"),
        f"{gate} assessment inner seal",
    )
    _same_or_none_identity(
        value.get("priorAssessment"),
        prior_identity,
        f"{gate} assessment prior",
    )
    _same_or_none_identity(
        value.get("predecessorDecision"),
        predecessor_identity,
        f"{gate} assessment predecessor",
    )
    _same_or_none_identity(
        value.get("gateAuthorization"),
        authorization_identity,
        f"{gate} assessment authorization",
    )
    launch_completion_path = _gate_launch_completion_path(gate, sequence)
    launch_completion_identity = core._identity(launch_completion_path)
    if not _same_identity(
        value.get("launchCompletion"), launch_completion_identity
    ):
        raise ValueError(f"{gate} assessment launch completion changed")
    core._verify_identity(
        _mapping(
            value["launchCompletion"],
            f"{gate} assessment launch completion",
        ),
        f"{gate} assessment launch completion",
    )
    events = offline._identity(
        value.get("events"), f"{gate} assessment events"
    )
    if _resolve(Path(events["path"])) != _gate_events(gate):
        raise ValueError(f"{gate} assessment event path changed")
    report, decision = _validate_core_assessment(
        value.get("coreAssessment"),
        gate=gate,
        inner_identity=inner_identity,
        events_identity=events,
    )
    if _strict_utc(
        report.get("createdUtc"), f"{gate} core assessment createdUtc"
    ) > created:
        raise ValueError(f"{gate} wrapper assessment predates core assessment")
    progress = _validated_progress(
        value.get("progress"), f"{gate} assessment progress"
    )
    if not _strict_equal(progress, report["progress"]):
        raise ValueError(f"{gate} wrapper/core progress differs")
    _expect(value.get("decision"), decision, f"{gate} assessment decision")
    terminal = decision in TERMINAL_DECISIONS[gate]
    _expect(value.get("terminal"), terminal, f"{gate} terminal flag")
    success = terminal and decision == SUCCESS_DECISIONS[gate]
    _expect(
        value.get("authorizedSuccessor"),
        _successor(gate) if success else None,
        f"{gate} authorized successor",
    )
    _verify_event_prefix(
        _gate_events(gate),
        events,
        exact=terminal,
        label=f"{gate} assessment {sequence}",
    )
    if terminal:
        _verify_terminal_core_assessment(
            report,
            gate=gate,
            inner_identity=inner_identity,
            events_identity=events,
        )
    return value


def _verify_assessment_budget(
    gate: str,
    report: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    *,
    label: str,
) -> None:
    progress = _validated_progress(report.get("progress"), f"{label} progress")
    pair_budget = (
        INITIAL_PAIR_BUDGETS[gate]
        if prior is None
        else RESUME_PAIR_BUDGET
    )
    prior_progress = (
        {
            "finishedGames": 0,
            "completePairs": 0,
            "balancedPrefixPairs": 0,
        }
        if prior is None
        else _validated_progress(
            prior.get("progress"), f"{label} prior progress"
        )
    )
    limits = {
        "finishedGames": pair_budget * 2,
        "completePairs": pair_budget,
        "balancedPrefixPairs": pair_budget,
    }
    for field, maximum_delta in limits.items():
        delta = int(progress[field]) - int(prior_progress[field])
        if delta < 0:
            raise ValueError(f"{label} {field} regressed")
        if delta > maximum_delta:
            raise ValueError(
                f"{label} {field} advanced by {delta}, exceeding "
                f"the launch budget bound {maximum_delta}"
            )
    if gate != "development":
        section = _mapping(
            report.get("sequentialGate"), f"{label} sequential gate"
        )
        signal_pair = section.get("signalPair")
        if signal_pair is not None:
            if type(signal_pair) is not int or signal_pair <= 0:
                raise ValueError(f"{label} signalPair is malformed")
            if signal_pair != int(progress["balancedPrefixPairs"]):
                raise ValueError(
                    f"{label} contains evidence after its first terminal "
                    "balanced checkpoint"
                )


def _assessment_history(
    gate: str,
    *,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any] | None,
    authorization_identity: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    directory = _gate_assessment_dir(gate)
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError(f"{gate} assessment directory is not a directory")
    paths = sorted(directory.iterdir(), key=lambda path: path.name)
    for path in paths:
        if (
            not path.is_file()
            or path.suffix != ".json"
            or len(path.stem) != 6
            or not path.stem.isdigit()
        ):
            raise ValueError(
                f"{gate} assessment directory has an unexpected artifact: {path}"
            )
    expected_names = [
        f"{sequence:06d}.json" for sequence in range(1, len(paths) + 1)
    ]
    if [path.name for path in paths] != expected_names:
        raise ValueError(f"{gate} assessment sequence has a gap")
    history: list[dict[str, Any]] = []
    prior_identity: dict[str, Any] | None = None
    prior_progress: tuple[int, int, int] | None = None
    prior_event_bytes = -1
    for sequence, path in enumerate(paths, 1):
        value = _validate_assessment(
            path,
            gate=gate,
            sequence=sequence,
            outer_identity=outer_identity,
            inner_identity=inner_identity,
            prior_identity=prior_identity,
            predecessor_identity=predecessor_identity,
            authorization_identity=authorization_identity,
        )
        _verify_assessment_budget(
            gate,
            _mapping(
                value.get("coreAssessment"),
                f"{gate} assessment {sequence} core report",
            ),
            None if not history else history[-1],
            label=f"{gate} assessment {sequence}",
        )
        progress = _progress_key(value["progress"])
        event_bytes = int(value["events"]["bytes"])
        if prior_progress is not None:
            if any(
                current < previous
                for current, previous in zip(progress, prior_progress)
            ) or progress == prior_progress:
                raise ValueError(f"{gate} assessment progress is not monotonic")
            if event_bytes <= prior_event_bytes:
                raise ValueError(f"{gate} assessment event log did not grow")
        if value["terminal"] is True and sequence != len(paths):
            raise ValueError(f"{gate} has evidence after a terminal assessment")
        history.append(value)
        prior_identity = core._identity(path)
        prior_progress = progress
        prior_event_bytes = event_bytes
    return history


def _launch_intent_value(
    *,
    gate: str,
    sequence: int,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any] | None,
    authorization_identity: Mapping[str, Any] | None,
    history: Sequence[Mapping[str, Any]],
    completed_launches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if sequence != len(history) + 1:
        raise ValueError(f"{gate} launch sequence does not follow assessment history")
    if len(completed_launches) != len(history):
        raise ValueError(f"{gate} launch sequence does not follow completion history")
    action = "run" if sequence == 1 else "resume"
    prior_assessment = (
        None
        if sequence == 1
        else core._identity(_gate_assessment_path(gate, sequence - 1))
    )
    prior_completion = (
        None
        if sequence == 1
        else core._identity(
            _gate_launch_completion_path(gate, sequence - 1)
        )
    )
    events_before = (
        None
        if sequence == 1
        else core._identity(_gate_events(gate))
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LAUNCH_INTENT_KIND,
        "profileId": PROFILE_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": core._utc_now(),
        "outerSeal": dict(outer_identity),
        "innerCoreSeal": dict(inner_identity),
        "predecessorDecision": (
            None
            if predecessor_identity is None
            else dict(predecessor_identity)
        ),
        "gateAuthorization": (
            None
            if authorization_identity is None
            else dict(authorization_identity)
        ),
        "priorAssessment": prior_assessment,
        "priorCompletion": prior_completion,
        "eventsBefore": events_before,
        "action": action,
        "pairBudget": (
            INITIAL_PAIR_BUDGETS[gate]
            if action == "run"
            else RESUME_PAIR_BUDGET
        ),
    }


def _validate_launch_intent(
    path: Path,
    *,
    gate: str,
    sequence: int,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any] | None,
    authorization_identity: Mapping[str, Any] | None,
    history: Sequence[Mapping[str, Any]],
    completed_launches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    expected_path = _gate_launch_intent_path(gate, sequence)
    if _resolve(path) != expected_path:
        raise ValueError(f"{gate} launch intent path is not canonical")
    if sequence != len(completed_launches) + 1:
        raise ValueError(f"{gate} launch intent sequence has a gap")
    if sequence > len(history) + 1:
        raise ValueError(f"{gate} launch intent skipped an assessment")
    value = _strict_object(path, f"{gate} launch intent {sequence}")
    _expect(
        set(value),
        LAUNCH_INTENT_FIELDS,
        f"{gate} launch intent {sequence} fields",
    )
    _expect(value.get("schemaVersion"), 1, f"{gate} launch intent schema")
    _expect(value.get("kind"), LAUNCH_INTENT_KIND, f"{gate} launch intent kind")
    _expect(value.get("profileId"), PROFILE_ID, f"{gate} launch intent profile")
    _expect(value.get("gate"), gate, f"{gate} launch intent gate")
    _expect(value.get("sequence"), sequence, f"{gate} launch intent sequence")
    created = _strict_utc(
        value.get("createdUtc"), f"{gate} launch intent createdUtc"
    )
    if not _same_identity(value.get("outerSeal"), outer_identity):
        raise ValueError(f"{gate} launch intent outer seal changed")
    if not _same_identity(value.get("innerCoreSeal"), inner_identity):
        raise ValueError(f"{gate} launch intent inner seal changed")
    _same_or_none_identity(
        value.get("predecessorDecision"),
        predecessor_identity,
        f"{gate} launch intent predecessor",
    )
    _same_or_none_identity(
        value.get("gateAuthorization"),
        authorization_identity,
        f"{gate} launch intent authorization",
    )
    if authorization_identity is not None:
        authorization = _strict_object(
            _gate_authorization_path(gate),
            f"{gate} launch intent authorization",
        )
        if _strict_utc(
            authorization.get("createdUtc"),
            f"{gate} launch authorization createdUtc",
        ) > created:
            raise ValueError(f"{gate} launch intent predates authorization")

    expected_action = "run" if sequence == 1 else "resume"
    expected_budget = (
        INITIAL_PAIR_BUDGETS[gate]
        if sequence == 1
        else RESUME_PAIR_BUDGET
    )
    _expect(value.get("action"), expected_action, f"{gate} launch action")
    _expect(value.get("pairBudget"), expected_budget, f"{gate} launch pair budget")
    if sequence == 1:
        for field in (
            "priorAssessment",
            "priorCompletion",
            "eventsBefore",
        ):
            if value.get(field) is not None:
                raise ValueError(
                    f"{gate} initial launch {field} must be JSON null"
                )
    else:
        if sequence - 1 > len(history):
            raise ValueError(f"{gate} resume launch has no prior assessment")
        prior_assessment = core._identity(
            _gate_assessment_path(gate, sequence - 1)
        )
        if not _same_identity(
            value.get("priorAssessment"), prior_assessment
        ):
            raise ValueError(f"{gate} launch prior assessment changed")
        prior_value = history[sequence - 2]
        if prior_value.get("terminal") is True:
            raise ValueError(f"{gate} launch follows a terminal assessment")
        if _strict_utc(
            prior_value.get("createdUtc"),
            f"{gate} prior assessment createdUtc",
        ) > created:
            raise ValueError(f"{gate} launch predates its prior assessment")
        prior_completion = core._identity(
            _gate_launch_completion_path(gate, sequence - 1)
        )
        if not _same_identity(
            value.get("priorCompletion"), prior_completion
        ):
            raise ValueError(f"{gate} launch prior completion changed")
        events_before = offline._identity(
            value.get("eventsBefore"),
            f"{gate} launch {sequence} events before",
        )
        if not _same_identity(events_before, prior_value.get("events")):
            raise ValueError(
                f"{gate} resume launch is not based on its latest assessment"
            )
        previous_completion = completed_launches[sequence - 2]
        if not _same_identity(
            events_before,
            previous_completion["value"].get("eventsAfter"),
        ):
            raise ValueError(
                f"{gate} resume launch is not based on its prior completion"
            )
        _verify_event_prefix(
            _gate_events(gate),
            events_before,
            exact=False,
            label=f"{gate} launch {sequence} events before",
        )
    return value


def _validate_launch_completion(
    path: Path,
    *,
    gate: str,
    sequence: int,
    intent: Mapping[str, Any],
) -> dict[str, Any]:
    expected_path = _gate_launch_completion_path(gate, sequence)
    if _resolve(path) != expected_path:
        raise ValueError(f"{gate} launch completion path is not canonical")
    value = _strict_object(path, f"{gate} launch completion {sequence}")
    _expect(
        set(value),
        LAUNCH_COMPLETION_FIELDS,
        f"{gate} launch completion {sequence} fields",
    )
    _expect(value.get("schemaVersion"), 1, f"{gate} launch completion schema")
    _expect(
        value.get("kind"),
        LAUNCH_COMPLETION_KIND,
        f"{gate} launch completion kind",
    )
    _expect(
        value.get("profileId"),
        PROFILE_ID,
        f"{gate} launch completion profile",
    )
    _expect(value.get("gate"), gate, f"{gate} launch completion gate")
    _expect(
        value.get("sequence"), sequence, f"{gate} launch completion sequence"
    )
    created = _strict_utc(
        value.get("createdUtc"), f"{gate} launch completion createdUtc"
    )
    intent_identity = core._identity(
        _gate_launch_intent_path(gate, sequence)
    )
    if not _same_identity(value.get("intent"), intent_identity):
        raise ValueError(f"{gate} launch completion intent changed")
    if _strict_utc(
        intent.get("createdUtc"), f"{gate} launch intent createdUtc"
    ) > created:
        raise ValueError(f"{gate} launch completion predates its intent")
    events_after = offline._identity(
        value.get("eventsAfter"),
        f"{gate} launch {sequence} events after",
    )
    if _resolve(Path(events_after["path"])) != _gate_events(gate):
        raise ValueError(f"{gate} launch completion event path changed")
    before = intent.get("eventsBefore")
    before_bytes = (
        0
        if before is None
        else int(
            offline._identity(
                before, f"{gate} launch {sequence} events before"
            )["bytes"]
        )
    )
    if int(events_after["bytes"]) <= before_bytes:
        raise ValueError(f"{gate} launch completion recorded no event growth")
    _verify_event_prefix(
        _gate_events(gate),
        events_after,
        exact=False,
        label=f"{gate} launch completion {sequence}",
    )
    return value


def _launch_history(
    gate: str,
    *,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any] | None,
    authorization_identity: Mapping[str, Any] | None,
    history: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    directory = _gate_launch_dir(gate)
    if not directory.exists():
        return [], None
    if not directory.is_dir():
        raise ValueError(f"{gate} launch evidence path is not a directory")
    paths: dict[tuple[int, str], Path] = {}
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        parts = path.name.split(".")
        if (
            not path.is_file()
            or len(parts) != 3
            or len(parts[0]) != 6
            or not parts[0].isdigit()
            or parts[1] not in {"intent", "completion"}
            or parts[2] != "json"
        ):
            raise ValueError(
                f"{gate} launch directory has an unexpected artifact: {path}"
            )
        key = (int(parts[0]), parts[1])
        if key in paths:
            raise ValueError(f"{gate} launch evidence has a duplicate sequence")
        paths[key] = path
    intent_sequences = sorted(
        sequence for sequence, kind in paths if kind == "intent"
    )
    completion_sequences = sorted(
        sequence for sequence, kind in paths if kind == "completion"
    )
    if intent_sequences != list(range(1, len(intent_sequences) + 1)):
        raise ValueError(f"{gate} launch intent sequence has a gap")
    if completion_sequences != list(
        range(1, len(completion_sequences) + 1)
    ):
        raise ValueError(f"{gate} launch completion sequence has a gap")
    if len(completion_sequences) not in {
        len(intent_sequences),
        max(0, len(intent_sequences) - 1),
    }:
        raise ValueError(f"{gate} launch intent/completion chain is malformed")
    if len(intent_sequences) > len(history) + 1:
        raise ValueError(f"{gate} has multiple launches without assessment")

    completed: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for sequence in intent_sequences:
        intent_path = paths[(sequence, "intent")]
        intent = _validate_launch_intent(
            intent_path,
            gate=gate,
            sequence=sequence,
            outer_identity=outer_identity,
            inner_identity=inner_identity,
            predecessor_identity=predecessor_identity,
            authorization_identity=authorization_identity,
            history=history,
            completed_launches=completed,
        )
        intent_record = {
            "path": intent_path,
            "identity": core._identity(intent_path),
            "value": intent,
        }
        completion_path = paths.get((sequence, "completion"))
        if completion_path is None:
            if sequence != intent_sequences[-1]:
                raise ValueError(
                    f"{gate} launch chain has evidence after a pending intent"
                )
            pending = intent_record
            continue
        completion = _validate_launch_completion(
            completion_path,
            gate=gate,
            sequence=sequence,
            intent=intent,
        )
        completed.append(
            {
                "intent": intent_record,
                "path": completion_path,
                "identity": core._identity(completion_path),
                "value": completion,
            }
        )
    return completed, pending


def _publish_launch_intent(
    *,
    gate: str,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any] | None,
    authorization_identity: Mapping[str, Any] | None,
    history: Sequence[Mapping[str, Any]],
    completed_launches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sequence = len(history) + 1
    value = _launch_intent_value(
        gate=gate,
        sequence=sequence,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
        history=history,
        completed_launches=completed_launches,
    )
    path = _gate_launch_intent_path(gate, sequence)
    core._exclusive_json(path, value)
    published = _validate_launch_intent(
        path,
        gate=gate,
        sequence=sequence,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
        history=history,
        completed_launches=completed_launches,
    )
    if not _strict_equal(published, value):
        raise ValueError(f"{gate} launch intent changed during publication")
    return {
        "path": path,
        "identity": core._identity(path),
        "value": published,
    }


def _publish_launch_completion(
    gate: str,
    intent_record: Mapping[str, Any],
) -> dict[str, Any]:
    intent = _mapping(intent_record.get("value"), f"{gate} launch intent")
    sequence = _strict_nonnegative_int(
        intent.get("sequence"), f"{gate} launch sequence"
    )
    if sequence <= 0:
        raise ValueError(f"{gate} launch sequence must be positive")
    events = _gate_events(gate)
    if not events.is_file():
        raise FileNotFoundError(
            f"{gate} launch has no canonical event log: {events}"
        )
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LAUNCH_COMPLETION_KIND,
        "profileId": PROFILE_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": core._utc_now(),
        "intent": dict(
            _mapping(intent_record.get("identity"), f"{gate} launch intent")
        ),
        "eventsAfter": core._identity(events),
    }
    path = _gate_launch_completion_path(gate, sequence)
    core._exclusive_json(path, value)
    published = _validate_launch_completion(
        path,
        gate=gate,
        sequence=sequence,
        intent=intent,
    )
    if not _strict_equal(published, value):
        raise ValueError(f"{gate} launch completion changed during publication")
    return {
        "intent": dict(intent_record),
        "path": path,
        "identity": core._identity(path),
        "value": published,
    }


def _launch_intent_has_event_growth(
    gate: str, intent: Mapping[str, Any]
) -> bool:
    events = _gate_events(gate)
    if not events.is_file():
        return False
    before = intent.get("eventsBefore")
    if before is None:
        return events.stat().st_size > 0
    before_identity = offline._identity(
        before, f"{gate} pending launch events before"
    )
    _verify_event_prefix(
        events,
        before_identity,
        exact=False,
        label=f"{gate} pending launch events",
    )
    return events.stat().st_size > int(before_identity["bytes"])


def _require_exact_resume_checkpoint(
    gate: str, history: Sequence[Mapping[str, Any]]
) -> None:
    if not history:
        raise ValueError(
            f"{gate} resume requires a prior nonterminal assessment"
        )
    if history[-1].get("terminal") is True:
        raise ValueError(f"{gate} resume follows a terminal assessment")
    events = _gate_events(gate)
    if not events.is_file():
        raise FileNotFoundError(
            f"{gate} resume requires its existing event log: {events}"
        )
    _verify_event_prefix(
        events,
        _mapping(
            history[-1]["events"], f"{gate} latest assessment events"
        ),
        exact=True,
        label=f"{gate} resume checkpoint",
    )


def _require_latest_launch_event_identity(
    gate: str, completion: Mapping[str, Any]
) -> dict[str, Any]:
    events = _gate_events(gate)
    if not events.is_file():
        raise FileNotFoundError(events)
    current = core._identity(events)
    if not _same_identity(current, completion.get("eventsAfter")):
        raise ValueError(
            f"{gate} event log differs from its latest launch completion"
        )
    return current


def _decision_value(
    *,
    gate: str,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
    assessment_identity: Mapping[str, Any],
    assessment: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any] | None,
    authorization_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    decision = str(assessment["decision"])
    passed = decision == SUCCESS_DECISIONS[gate]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": DECISION_KIND,
        "profileId": PROFILE_ID,
        "gate": gate,
        "createdUtc": core._utc_now(),
        "outerSeal": dict(outer_identity),
        "innerCoreSeal": dict(inner_identity),
        "assessment": dict(assessment_identity),
        "events": dict(assessment["events"]),
        "predecessorDecision": (
            None
            if predecessor_identity is None
            else dict(predecessor_identity)
        ),
        "gateAuthorization": (
            None
            if authorization_identity is None
            else dict(authorization_identity)
        ),
        "decision": decision,
        "passed": passed,
        "authorizedSuccessor": _successor(gate) if passed else None,
        "retryAllowed": False,
    }


def _verify_gate_context(
    gate: str,
    *,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    predecessor = GATE_PREDECESSOR[gate]
    if predecessor is None:
        return None, None, None
    predecessor_value = _verify_gate_decision(
        predecessor,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
    )
    if (
        predecessor_value.get("passed") is not True
        or predecessor_value.get("decision")
        != SUCCESS_DECISIONS[predecessor]
    ):
        raise ValueError(
            f"{gate} is forbidden because {predecessor} did not pass"
        )
    predecessor_identity = core._identity(
        _gate_decision_path(predecessor)
    )
    authorization = _verify_authorization(
        gate,
        outer_identity=outer_identity,
        predecessor_identity=predecessor_identity,
    )
    authorization_identity = core._identity(
        _gate_authorization_path(gate)
    )
    return predecessor_value, predecessor_identity, {
        "value": authorization,
        "identity": authorization_identity,
    }


def _verify_gate_decision(
    gate: str,
    *,
    outer_identity: Mapping[str, Any] | None = None,
    inner_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if outer_identity is None or inner_identity is None:
        outer = _verify_outer(OUTER_SEAL)
        outer_identity = core._identity(OUTER_SEAL)
        inner_identity = _mapping(
            outer.get("innerCoreSeal"), "gate-decision inner seal"
        )
    (
        _predecessor_value,
        predecessor_identity,
        authorization_record,
    ) = _verify_gate_context(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
    )
    authorization_identity = (
        None
        if authorization_record is None
        else authorization_record["identity"]
    )
    history = _assessment_history(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
    )
    completed_launches, pending_launch = _launch_history(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
        history=history,
    )
    if pending_launch is not None or len(completed_launches) != len(history):
        raise ValueError(
            f"{gate} decision is not backed by one completed launch "
            "per assessment"
        )
    if not history or history[-1].get("terminal") is not True:
        raise ValueError(f"{gate} has no terminal assessment")
    path = _gate_decision_path(gate)
    value = _strict_object(path, f"{gate} gate decision")
    _expect(set(value), DECISION_FIELDS, f"{gate} decision fields")
    _expect(value.get("schemaVersion"), 1, f"{gate} decision schema")
    _expect(value.get("kind"), DECISION_KIND, f"{gate} decision kind")
    _expect(value.get("profileId"), PROFILE_ID, f"{gate} decision profile")
    _expect(value.get("gate"), gate, f"{gate} decision gate")
    created = _strict_utc(value.get("createdUtc"), f"{gate} decision createdUtc")
    if not _same_identity(value.get("outerSeal"), outer_identity):
        raise ValueError(f"{gate} decision outer seal changed")
    if not _same_identity(value.get("innerCoreSeal"), inner_identity):
        raise ValueError(f"{gate} decision inner seal changed")
    latest_path = _gate_assessment_path(gate, len(history))
    latest_identity = core._identity(latest_path)
    if not _same_identity(value.get("assessment"), latest_identity):
        raise ValueError(f"{gate} decision assessment changed")
    latest = history[-1]
    if _strict_utc(
        latest.get("createdUtc"), f"{gate} terminal assessment createdUtc"
    ) > created:
        raise ValueError(f"{gate} decision predates its assessment")
    if not _same_identity(value.get("events"), latest["events"]):
        raise ValueError(f"{gate} decision event identity changed")
    _same_or_none_identity(
        value.get("predecessorDecision"),
        predecessor_identity,
        f"{gate} decision predecessor",
    )
    _same_or_none_identity(
        value.get("gateAuthorization"),
        authorization_identity,
        f"{gate} decision authorization",
    )
    _expect(value.get("decision"), latest["decision"], f"{gate} decision")
    passed = latest["decision"] == SUCCESS_DECISIONS[gate]
    _expect(value.get("passed"), passed, f"{gate} decision passed")
    _expect(
        value.get("authorizedSuccessor"),
        _successor(gate) if passed else None,
        f"{gate} decision successor",
    )
    if value.get("retryAllowed") is not False:
        raise ValueError(f"{gate} decision retry policy changed")
    _verify_event_prefix(
        _gate_events(gate),
        latest["events"],
        exact=True,
        label=f"{gate} terminal decision",
    )
    return value


def _publish_gate_decision(
    *,
    gate: str,
    outer_identity: Mapping[str, Any],
    inner_identity: Mapping[str, Any],
    assessment: Mapping[str, Any],
    assessment_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any] | None,
    authorization_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    value = _decision_value(
        gate=gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        assessment_identity=assessment_identity,
        assessment=assessment,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
    )
    path = _gate_decision_path(gate)
    core._exclusive_json(path, value)
    verified = _verify_gate_decision(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
    )
    if not _strict_equal(verified, value):
        raise ValueError(f"{gate} decision changed during publication")
    return verified


def _casefold_field(value: Mapping[str, Any], name: str, label: str) -> Any:
    matches = [
        item
        for key, item in value.items()
        if type(key) is str and key.casefold() == name.casefold()
    ]
    if len(matches) != 1:
        raise ValueError(f"{label} must contain exactly one {name} field")
    return matches[0]


def _run_created_utc(events_path: Path) -> Any:
    runs: list[dict[str, Any]] = []
    with _resolve(events_path).open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            label = f"{_resolve(events_path)}:{line_number}"
            value = offline._strict_loads(line, label)
            record = _mapping(value, label)
            record_type = _casefold_field(
                record, "RecordType", f"{label} record"
            )
            if type(record_type) is not str:
                raise ValueError(f"{label}.RecordType must be a JSON string")
            if record_type == "run":
                runs.append(record)
    if len(runs) != 1:
        raise ValueError("canonical events must contain exactly one run record")
    created = _casefold_field(runs[0], "CreatedUtc", "run record")
    return _strict_utc(created, "run record CreatedUtc")


def _gate_config_path(
    gate: str, inner: Mapping[str, Any]
) -> Path:
    gates = _mapping(inner.get("gates"), "inner match gates")
    entry = _mapping(gates.get(gate), f"{gate} inner match gate")
    identity = offline._identity(
        entry.get("config"), f"{gate} sealed match config"
    )
    path = core._verify_identity(
        identity, f"{gate} sealed match config"
    )
    expected = SEALED_DIR / f"king-state-v1-{gate}-match.json"
    if _resolve(path) != _resolve(expected):
        raise ValueError(f"{gate} sealed match config path changed")
    return _resolve(path)


def _ordered_launch(args: argparse.Namespace) -> None:
    gate = str(args.gate)
    action = str(args.action)
    if gate not in GATE_ORDER or action not in {"run", "resume"}:
        raise ValueError("unknown generation-3 match launch request")
    prelabel._verify_prebuilt_tooling()
    outer = _verify_outer(OUTER_SEAL, allow_future_run=True)
    outer_identity = core._identity(OUTER_SEAL)
    inner_identity = _mapping(
        outer.get("innerCoreSeal"), "match launch inner seal"
    )
    inner_path = core._verify_identity(
        inner_identity, "match launch inner seal"
    )
    _install_profile()
    inner = core._verify_seal(inner_path)
    (
        _predecessor,
        predecessor_identity,
        authorization_record,
    ) = _verify_gate_context(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
    )
    authorization_identity = (
        None
        if authorization_record is None
        else authorization_record["identity"]
    )
    history = _assessment_history(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
    )
    completed_launches, pending_launch = _launch_history(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
        history=history,
    )
    if _gate_decision_path(gate).exists():
        _verify_gate_decision(
            gate,
            outer_identity=outer_identity,
            inner_identity=inner_identity,
        )
        raise FileExistsError(f"{gate} already has a terminal decision")
    if history and history[-1].get("terminal") is True:
        raise FileExistsError(f"{gate} already has a terminal assessment")
    if len(completed_launches) > len(history):
        raise FileExistsError(
            f"{gate} has an unassessed launch completion; assess before "
            "launching again"
        )
    if len(completed_launches) != len(history):
        raise ValueError(
            f"{gate} launch/assessment evidence counts are inconsistent"
        )

    events = _gate_events(gate)
    expected_action = "run" if not history else "resume"
    if action != expected_action:
        raise ValueError(
            f"{gate} next authorized action is {expected_action}, not {action}"
        )
    if pending_launch is None:
        if action == "run" and events.exists():
            raise FileExistsError(
                f"{gate} initial launch requires an absent event log"
            )
        if action == "resume":
            _require_exact_resume_checkpoint(gate, history)
    else:
        pending_value = _mapping(
            pending_launch.get("value"), f"{gate} pending launch intent"
        )
        _expect(
            pending_value.get("action"),
            action,
            f"{gate} pending launch action",
        )
        if _launch_intent_has_event_growth(gate, pending_value):
            completion = _publish_launch_completion(gate, pending_launch)
            if not _same_identity(
                outer_identity, core._identity(OUTER_SEAL)
            ):
                raise ValueError(
                    "outer match seal changed during launch recovery"
                )
            if not _same_identity(
                inner_identity, core._identity(inner_path)
            ):
                raise ValueError(
                    "inner match seal changed during launch recovery"
                )
            print(
                f"Recovered generation-3 {gate} launch completion "
                f"{completion['value']['sequence']} from pending "
                "intent evidence; assess next.",
                flush=True,
            )
            return

    config_path = _gate_config_path(gate, inner)
    config = _strict_object(config_path, f"{gate} sealed match config")
    execution = _mapping(
        config.get("kingStateMatchExecution"),
        f"{gate} serialized execution contract",
    )
    expected_budget = (
        INITIAL_PAIR_BUDGETS[gate]
        if action == "run"
        else RESUME_PAIR_BUDGET
    )
    expected_execution = {
        "oneGameAtATime": True,
        "maximumConcurrentGames": 1,
        "pairBudgetRequired": True,
        "pairBudgetMustBeMultipleOf": 4,
        "initialPairBudget": INITIAL_PAIR_BUDGETS[gate],
        "resumePairBudget": RESUME_PAIR_BUDGET,
        "executionOnlyThroughAdapterLaunch": True,
        "idleMachineRequired": gate == "equal-time",
    }
    _expect(
        execution,
        expected_execution,
        f"{gate} execution launch contract",
    )
    if expected_budget % int(execution["pairBudgetMustBeMultipleOf"]):
        raise ValueError(f"{gate} pair budget is not block aligned")

    command = [
        str(_resolve(DOTNET_HOST)),
        str(_resolve(OMEGA_MATCH_ASSEMBLY)),
        "validate",
        "--config",
        str(config_path),
    ]
    prelabel._run_pinned_dotnet(
        command,
        runtime_bundle_pin=prelabel.OMEGA_MATCH_PINS[
            "appLocalRuntimeBundle"
        ],
        cwd=REPO,
        check=True,
    )
    intent_record = (
        pending_launch
        if pending_launch is not None
        else _publish_launch_intent(
            gate=gate,
            outer_identity=outer_identity,
            inner_identity=inner_identity,
            predecessor_identity=predecessor_identity,
            authorization_identity=authorization_identity,
            history=history,
            completed_launches=completed_launches,
        )
    )
    command[2] = action
    command.extend(("--pair-budget", str(expected_budget)))
    result = prelabel._run_pinned_dotnet(
        command,
        runtime_bundle_pin=prelabel.OMEGA_MATCH_PINS[
            "appLocalRuntimeBundle"
        ],
        cwd=REPO,
        check=False,
    )

    if not _launch_intent_has_event_growth(
        gate,
        _mapping(intent_record["value"], f"{gate} launch intent"),
    ):
        raise FileNotFoundError(
            f"{gate} launcher returned without canonical event growth; "
            "the append-only intent remains pending for an exact retry"
        )
    completion = _publish_launch_completion(gate, intent_record)
    if not _same_identity(outer_identity, core._identity(OUTER_SEAL)):
        raise ValueError("outer match seal changed during match launch")
    if not _same_identity(inner_identity, core._identity(inner_path)):
        raise ValueError("inner match seal changed during match launch")
    if authorization_record is not None:
        authorized = _strict_utc(
            authorization_record["value"].get("createdUtc"),
            f"{gate} authorization createdUtc",
        )
        if _run_created_utc(events) < authorized:
            raise ValueError(f"{gate} run predates its authorization")
    if result.returncode != 0:
        raise RuntimeError(
            f"{gate} OmegaMatch {action} exited {result.returncode}; "
            f"launch completion {completion['value']['sequence']} was "
            "preserved and must be assessed"
        )
    print(
        f"Generation-3 {gate} {action} completed "
        f"with fixed pair budget {expected_budget} and launch evidence "
        f"{completion['value']['sequence']}; assess next.",
        flush=True,
    )


def _ordered_repair_transition(args: argparse.Namespace) -> None:
    gate = str(args.gate)
    if gate not in {"equal-node", "equal-time"}:
        raise ValueError(
            "only equal-node and equal-time have repairable authorizations"
        )
    outer = _verify_outer(OUTER_SEAL, allow_future_run=True)
    outer_identity = core._identity(OUTER_SEAL)
    inner_identity = _mapping(
        outer.get("innerCoreSeal"), "transition repair inner seal"
    )
    inner_path = core._verify_identity(
        inner_identity, "transition repair inner seal"
    )
    _install_profile()
    inner = core._verify_seal(inner_path)
    predecessor_gate = GATE_PREDECESSOR[gate]
    if predecessor_gate is None:
        raise AssertionError(gate)
    predecessor = _verify_gate_decision(
        predecessor_gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
    )
    if predecessor.get("passed") is not True:
        raise ValueError(
            f"{gate} repair requires a passing {predecessor_gate} decision"
        )
    predecessor_identity = core._identity(
        _gate_decision_path(predecessor_gate)
    )
    authorization_path = _gate_authorization_path(gate)
    if authorization_path.exists():
        _verify_authorization(
            gate,
            outer_identity=outer_identity,
            predecessor_identity=predecessor_identity,
        )
        print(
            f"Generation-3 {gate} authorization already valid: "
            f"{authorization_path}",
            flush=True,
        )
        return
    idle_identity: dict[str, Any] | None = None
    if gate == "equal-time":
        core._validate_idle_attestation(
            _idle_attestation_path(),
            str(inner["gates"]["equal-time"]["runId"]),
        )
        idle_identity = core._identity(_idle_attestation_path())
    _publish_authorization(
        gate,
        outer_identity=outer_identity,
        predecessor_identity=predecessor_identity,
        idle_identity=idle_identity,
    )
    print(
        f"Repaired generation-3 {gate} authorization before events: "
        f"{authorization_path}",
        flush=True,
    )


def _ordered_assess(args: argparse.Namespace) -> dict[str, Any]:
    gate = str(args.gate)
    if gate not in GATE_ORDER:
        raise ValueError(f"unknown generation-3 gate: {gate}")
    outer = _verify_outer(OUTER_SEAL)
    outer_identity = core._identity(OUTER_SEAL)
    inner_identity = _mapping(
        outer.get("innerCoreSeal"), "ordered assessment inner seal"
    )
    inner_path = core._verify_identity(
        inner_identity, "ordered assessment inner seal"
    )
    _install_profile()
    inner = core._verify_seal(inner_path)
    (
        _predecessor,
        predecessor_identity,
        authorization_record,
    ) = _verify_gate_context(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
    )
    authorization_identity = (
        None
        if authorization_record is None
        else authorization_record["identity"]
    )
    history = _assessment_history(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
    )
    completed_launches, pending_launch = _launch_history(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
        history=history,
    )
    if _gate_decision_path(gate).exists():
        decision_value = _verify_gate_decision(
            gate,
            outer_identity=outer_identity,
            inner_identity=inner_identity,
        )
        if (
            gate == "development"
            and decision_value["passed"] is True
            and not _gate_authorization_path("equal-node").exists()
        ):
            _publish_authorization(
                "equal-node",
                outer_identity=outer_identity,
                predecessor_identity=core._identity(
                    _gate_decision_path("development")
                ),
                idle_identity=None,
            )
        elif gate == "development" and decision_value["passed"] is True:
            _verify_authorization(
                "equal-node",
                outer_identity=outer_identity,
                predecessor_identity=core._identity(
                    _gate_decision_path("development")
                ),
            )
        print(
            f"Generation-3 {gate} terminal publication already valid; "
            "any missing successor authorization was repaired.",
            flush=True,
        )
        return history[-1]
    if history and history[-1].get("terminal") is True:
        if (
            pending_launch is not None
            or len(completed_launches) != len(history)
        ):
            raise ValueError(
                f"{gate} terminal assessment launch chain changed"
            )
        published = history[-1]
        decision_value = _publish_gate_decision(
            gate=gate,
            outer_identity=outer_identity,
            inner_identity=inner_identity,
            assessment=published,
            assessment_identity=core._identity(
                _gate_assessment_path(gate, len(history))
            ),
            predecessor_identity=predecessor_identity,
            authorization_identity=authorization_identity,
        )
        if decision_value["passed"] is True and gate == "development":
            _publish_authorization(
                "equal-node",
                outer_identity=outer_identity,
                predecessor_identity=core._identity(
                    _gate_decision_path("development")
                ),
                idle_identity=None,
            )
        print(
            f"Recovered generation-3 {gate} decision publication from "
            "its immutable terminal assessment.",
            flush=True,
        )
        return published
    if pending_launch is not None:
        raise ValueError(
            f"{gate} launch intent is pending completion; rerun the exact "
            "launch command before assessment"
        )
    if len(completed_launches) != len(history) + 1:
        raise ValueError(
            f"{gate} assessment requires exactly one new completed "
            "adapter launch"
        )
    launch_completion = completed_launches[-1]
    events_path = _canonical_argument(
        getattr(args, "events", None),
        _gate_events(gate),
        f"{gate} events",
    )
    if not events_path.is_file():
        raise FileNotFoundError(events_path)
    events_before = _require_latest_launch_event_identity(
        gate,
        _mapping(
            launch_completion["value"],
            f"{gate} latest launch completion",
        ),
    )
    if history and _same_identity(events_before, history[-1]["events"]):
        raise ValueError(
            f"{gate} event log has not grown since its last assessment"
        )
    sequence = len(history) + 1
    output_path = _canonical_argument(
        getattr(args, "output", None),
        _gate_assessment_path(gate, sequence),
        f"{gate} assessment output",
    )
    if output_path.exists():
        raise FileExistsError(output_path)
    idle_path: Path | None
    if gate == "equal-time":
        idle_path = _canonical_argument(
            getattr(args, "idle_attestation", None),
            _idle_attestation_path(),
            "equal-time idle attestation",
        )
        core._validate_idle_attestation(
            idle_path, str(inner["gates"]["equal-time"]["runId"])
        )
    else:
        if getattr(args, "idle_attestation", None) is not None:
            raise ValueError(
                "--idle-attestation is valid only for equal-time"
            )
        idle_path = None

    run_created = _run_created_utc(events_path)
    if authorization_record is not None:
        authorized = _strict_utc(
            authorization_record["value"].get("createdUtc"),
            f"{gate} authorization createdUtc",
        )
        if run_created < authorized:
            raise ValueError(
                f"{gate} run predates its predecessor authorization"
            )

    with tempfile.TemporaryDirectory(
        prefix=f"omega-king-state-v3-{gate}-assessment-"
    ) as raw:
        core_output = Path(raw) / "core-assessment.json"
        _install_profile()
        core_report = core._assess(
            argparse.Namespace(
                seal=inner_path,
                gate=gate,
                events=events_path,
                output=core_output,
                idle_attestation=idle_path,
            )
        )
        strict_core = _strict_object(
            core_output, f"{gate} staged core assessment"
        )
        if not _strict_equal(core_report, strict_core):
            raise ValueError(f"{gate} core assessment changed on disk")

    events_after = core._identity(events_path)
    if not _same_identity(events_before, events_after):
        raise ValueError(f"{gate} event log changed during assessment")
    if not _same_identity(outer_identity, core._identity(OUTER_SEAL)):
        raise ValueError("outer match seal changed during assessment")
    if not _same_identity(inner_identity, core._identity(inner_path)):
        raise ValueError("inner match seal changed during assessment")
    (
        _repeated_predecessor,
        repeated_predecessor_identity,
        repeated_authorization,
    ) = _verify_gate_context(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
    )
    _same_or_none_identity(
        repeated_predecessor_identity,
        predecessor_identity,
        f"{gate} repeated predecessor",
    )
    repeated_authorization_identity = (
        None
        if repeated_authorization is None
        else repeated_authorization["identity"]
    )
    _same_or_none_identity(
        repeated_authorization_identity,
        authorization_identity,
        f"{gate} repeated authorization",
    )
    validated_core, decision = _validate_core_assessment(
        strict_core,
        gate=gate,
        inner_identity=inner_identity,
        events_identity=events_after,
    )
    _verify_assessment_budget(
        gate,
        validated_core,
        None if not history else history[-1],
        label=f"{gate} assessment {len(history) + 1}",
    )
    repeated_launches, repeated_pending = _launch_history(
        gate,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
        history=history,
    )
    if (
        repeated_pending is not None
        or len(repeated_launches) != len(completed_launches)
        or not _same_identity(
            repeated_launches[-1]["identity"],
            launch_completion["identity"],
        )
    ):
        raise ValueError(f"{gate} launch evidence changed during assessment")
    progress = _progress_key(validated_core["progress"])
    if history:
        previous = _progress_key(history[-1]["progress"])
        if any(
            current < prior
            for current, prior in zip(progress, previous)
        ) or progress == previous:
            raise ValueError(f"{gate} assessment made no monotonic progress")
    prior_identity = (
        None
        if not history
        else core._identity(_gate_assessment_path(gate, len(history)))
    )
    assessment = _assessment_value(
        gate=gate,
        sequence=sequence,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        events_identity=events_after,
        prior_identity=prior_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
        launch_completion_identity=launch_completion["identity"],
        core_report=validated_core,
        decision=decision,
    )
    core._exclusive_json(output_path, assessment)
    published = _validate_assessment(
        output_path,
        gate=gate,
        sequence=sequence,
        outer_identity=outer_identity,
        inner_identity=inner_identity,
        prior_identity=prior_identity,
        predecessor_identity=predecessor_identity,
        authorization_identity=authorization_identity,
    )
    if not _strict_equal(published, assessment):
        raise ValueError(f"{gate} assessment changed during publication")
    if published["terminal"] is True:
        assessment_identity = core._identity(output_path)
        decision_value = _publish_gate_decision(
            gate=gate,
            outer_identity=outer_identity,
            inner_identity=inner_identity,
            assessment=published,
            assessment_identity=assessment_identity,
            predecessor_identity=predecessor_identity,
            authorization_identity=authorization_identity,
        )
        if decision_value["passed"] is True and gate == "development":
            _publish_authorization(
                "equal-node",
                outer_identity=outer_identity,
                predecessor_identity=core._identity(
                    _gate_decision_path("development")
                ),
                idle_identity=None,
            )
    print(
        f"Generation-3 {gate} assessment {sequence}: "
        f"decision={published['decision']}",
        flush=True,
    )
    return published


def _ordered_attest(args: argparse.Namespace) -> None:
    outer = _verify_outer(OUTER_SEAL)
    outer_identity = core._identity(OUTER_SEAL)
    inner_identity = _mapping(
        outer.get("innerCoreSeal"), "ordered attestation inner seal"
    )
    inner_path = core._verify_identity(
        inner_identity, "ordered attestation inner seal"
    )
    _install_profile()
    inner = core._verify_seal(inner_path)
    predecessor = _verify_gate_decision(
        "equal-node",
        outer_identity=outer_identity,
        inner_identity=inner_identity,
    )
    if predecessor.get("passed") is not True:
        raise ValueError("equal-time attestation requires equal-node promotion")
    predecessor_identity = core._identity(
        _gate_decision_path("equal-node")
    )
    output = _canonical_argument(
        getattr(args, "output", None),
        _idle_attestation_path(),
        "equal-time idle attestation",
    )
    authorization_path = _gate_authorization_path("equal-time")
    if output.exists():
        core._validate_idle_attestation(
            output,
            str(inner["gates"]["equal-time"]["runId"]),
        )
        idle_identity = core._identity(output)
        if authorization_path.exists():
            _verify_authorization(
                "equal-time",
                outer_identity=outer_identity,
                predecessor_identity=predecessor_identity,
            )
            print(
                "Generation-3 equal-time idle attestation and "
                f"authorization already valid: {authorization_path}",
                flush=True,
            )
            return
        _publish_authorization(
            "equal-time",
            outer_identity=outer_identity,
            predecessor_identity=predecessor_identity,
            idle_identity=idle_identity,
        )
        print(
            "Recovered generation-3 equal-time authorization from its "
            f"existing idle attestation: {authorization_path}",
            flush=True,
        )
        return
    for path, label in (
        (output, "equal-time idle attestation"),
        (authorization_path, "equal-time authorization"),
        (_gate_events("equal-time"), "equal-time event log"),
        (_gate_decision_path("equal-time"), "equal-time decision"),
        (_gate_launch_dir("equal-time"), "equal-time launch evidence"),
    ):
        if path.exists():
            raise FileExistsError(f"{label} already exists: {path}")
    if _gate_assessment_dir("equal-time").exists():
        raise FileExistsError(
            "equal-time assessment history predates its authorization"
        )
    core._attest(
        argparse.Namespace(
            seal=inner_path,
            output=output,
            operator=args.operator,
        )
    )
    if not _same_identity(outer_identity, core._identity(OUTER_SEAL)):
        raise ValueError("outer match seal changed during idle attestation")
    repeated = _verify_gate_decision(
        "equal-node",
        outer_identity=outer_identity,
        inner_identity=inner_identity,
    )
    if not _strict_equal(repeated, predecessor):
        raise ValueError(
            "equal-node decision changed during equal-time attestation"
        )
    if _gate_events("equal-time").exists():
        raise FileExistsError(
            "equal-time run began during idle-attestation publication"
        )
    idle_identity = core._identity(output)
    _publish_authorization(
        "equal-time",
        outer_identity=outer_identity,
        predecessor_identity=predecessor_identity,
        idle_identity=idle_identity,
    )
    print(
        f"Generation-3 equal-time authorization: {authorization_path}",
        flush=True,
    )


def _self_test() -> None:
    if training.NUMPY_WAS_PRELOADED:
        raise AssertionError("trainer observed NumPy before its thread contract")
    if tuple(EXPECTED_SEEDS) != GATE_ORDER:
        raise AssertionError("seed and stopping-order gate inventories differ")
    if (
        tuple(INITIAL_PAIR_BUDGETS) != GATE_ORDER
        or INITIAL_PAIR_BUDGETS
        != {
            "development": 32,
            "equal-node": 64,
            "equal-time": 64,
        }
        or RESUME_PAIR_BUDGET != 4
        or any(
            budget % RESUME_PAIR_BUDGET
            for budget in INITIAL_PAIR_BUDGETS.values()
        )
    ):
        raise AssertionError("fixed adapter pair budgets drifted")
    identities = _validate_static_contract()
    if set(identities) != {
        "adapterContract",
        "matchProtocol",
        "compatibilityCore",
        "readinessWrapper",
        "offlineWrapper",
        "trainingOrchestrator",
        "prelabelOrchestrator",
        "preregistration",
        "amendment001",
        "amendment002",
        "amendment003",
    }:
        raise AssertionError("static match authority inventory changed")
    _verify_engine()
    contract = _strict_object(CONTRACT_PATH, "adapter contract")
    _expect(contract.get("contracts"), CONTRACTS, "self-test contracts")
    if _strict_equal(True, 1):
        raise AssertionError("strict match JSON equality accepted bool as int")
    _install_profile()
    _expect(core.SAMPLER_TRAJECTORY_PAIRS, 4096, "installed sampler pairs")
    for gate, seed in EXPECTED_SEEDS.items():
        _expect(core.GATE_SPECS[gate]["seed"], seed, f"installed {gate} seed")
        for key, value in _ORIGINAL_SPECS[gate].items():
            if key != "seed":
                _expect(
                    core.GATE_SPECS[gate][key],
                    value,
                    f"unchanged {gate}.{key}",
                )
    payload = {"strict": True, "value": 3}
    with tempfile.TemporaryDirectory(
        prefix="omega-king-state-v3-match-self-test-"
    ) as raw:
        root = Path(raw)
        identity = _payload_identity(root / "payload.json", payload)
        actual_payload = _canonical_payload(payload)
        if (
            identity["bytes"] != len(actual_payload)
            or identity["sha256"]
            != hashlib.sha256(actual_payload).hexdigest()
        ):
            raise AssertionError("compatibility payload identity changed")

        duplicate = root / "duplicate.json"
        duplicate.write_text('{"value":1,"value":2}', encoding="utf-8")
        try:
            _strict_object(duplicate, "duplicate")
        except ValueError:
            pass
        else:
            raise AssertionError("duplicate match JSON key was accepted")

        strict_file = root / "strict-identity.json"
        strict_file.write_text('{"strict":true}\n', encoding="utf-8")
        strict_identity = core._identity(strict_file)
        _strict_file_identity(
            strict_identity, strict_file, "synthetic strict identity"
        )
        for mutation in (
            {**strict_identity, "bytes": str(strict_identity["bytes"])},
            {**strict_identity, "extra": True},
        ):
            try:
                _strict_file_identity(
                    mutation, strict_file, "mutated strict identity"
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "non-exact inner-seal identity shape was accepted"
                )

        inventory_root = root / "inventory"
        nested_root = inventory_root / "nested"
        nested_root.mkdir(parents=True)
        unique_file = inventory_root / "unique.json"
        overlap_file = nested_root / "overlap.json"
        snapshot_file = inventory_root / "snapshot.json"
        for path, payload_text in (
            (unique_file, '{"unique":true}\n'),
            (overlap_file, '{"overlap":true}\n'),
            (snapshot_file, '{"snapshot":true}\n'),
        ):
            path.write_text(payload_text, encoding="utf-8")
        flat_exclusions = [
            core._identity(path)
            for path in sorted(
                (unique_file, overlap_file, snapshot_file),
                key=lambda item: str(item).lower(),
            )
        ]
        expected_inventory = _reconstruct_exclusion_inventory(
            [inventory_root, nested_root, snapshot_file],
            flat_exclusions,
        )
        if [len(item["files"]) for item in expected_inventory] != [3, 1, 1]:
            raise AssertionError(
                "overlapping exclusion-root reconstruction changed"
            )
        removed_unique = copy.deepcopy(expected_inventory)
        removed_unique[0]["files"] = [
            identity
            for identity in removed_unique[0]["files"]
            if _resolve(Path(identity["path"])) != _resolve(unique_file)
        ]
        removed_overlap_membership = copy.deepcopy(expected_inventory)
        removed_overlap_membership[1]["files"].clear()
        for label, mutation in (
            ("unique exclusion membership", removed_unique),
            ("duplicate exclusion membership", removed_overlap_membership),
        ):
            try:
                _expect(
                    mutation,
                    expected_inventory,
                    f"mutated {label}",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"removed {label} was accepted"
                )

        original_outer = globals()["OUTER_SEAL"]
        original_future = globals()["FUTURE_RUN_ROOT"]
        try:
            globals()["OUTER_SEAL"] = root / "failed-outer.json"
            globals()["FUTURE_RUN_ROOT"] = root / "absent-future"
            try:
                _publish_outer(
                    {"synthetic": True},
                    lambda _path: (_ for _ in ()).throw(
                        ValueError("synthetic verification failure")
                    ),
                )
            except ValueError:
                pass
            else:
                raise AssertionError("failed outer verification was accepted")
            if not OUTER_SEAL.is_file():
                raise AssertionError("failed outer seal was not preserved")
            try:
                _publish_outer({"retry": True}, lambda _path: None)
            except FileExistsError:
                pass
            else:
                raise AssertionError("failed outer seal did not block retry")

            globals()["FUTURE_RUN_ROOT"] = root / "evidence-root"
            if _gate_events("development") != _resolve(
                FUTURE_RUN_ROOT / "development" / "events.jsonl"
            ):
                raise AssertionError("canonical gate event path changed")
            if _assessment_evidence_contract()["gateOrder"] != list(
                GATE_ORDER
            ):
                raise AssertionError("assessment gate order changed")
            if _core_decision(
                "development",
                {"developmentScreen": {"decision": "pass"}},
            )[0] != "pass":
                raise AssertionError("development pass was not terminal")
            if _core_decision(
                "equal-node",
                {"sequentialGate": {"decision": "promote"}},
            )[0] != "promote":
                raise AssertionError("equal-node promotion was not recognized")
            try:
                _core_decision(
                    "equal-time",
                    {"sequentialGate": {"decision": "retry"}},
                )
            except ValueError:
                pass
            else:
                raise AssertionError("unknown formal decision was accepted")
            try:
                _require_idle_not_before_predecessor(
                    "2026-07-19T00:00:00Z",
                    "2026-07-19T00:00:01Z",
                    label="synthetic stale idle attestation",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "pre-equal-node idle attestation was accepted"
                )
            _require_idle_not_before_predecessor(
                "2026-07-19T00:00:01Z",
                "2026-07-19T00:00:01Z",
                label="synthetic ordered idle attestation",
            )

            budget_progress = {
                "finishedGames": 128,
                "completePairs": 64,
                "incompleteOpenings": [],
                "inProgressAttempts": [],
                "completeBalancedPrefixBlocks": 16,
                "balancedPrefixPairs": 64,
                "completePairsOutsideBalancedPrefix": 0,
            }
            budget_prior = {"progress": copy.deepcopy(budget_progress)}
            allowed_budget_report = {
                "progress": {
                    **budget_progress,
                    "finishedGames": 136,
                    "completePairs": 68,
                    "completeBalancedPrefixBlocks": 17,
                    "balancedPrefixPairs": 68,
                },
                "sequentialGate": {"signalPair": None},
            }
            _verify_assessment_budget(
                "equal-node",
                allowed_budget_report,
                budget_prior,
                label="synthetic bounded resume",
            )
            oversized_budget_report = copy.deepcopy(allowed_budget_report)
            oversized_budget_report["progress"].update(
                {
                    "finishedGames": 144,
                    "completePairs": 72,
                    "completeBalancedPrefixBlocks": 18,
                    "balancedPrefixPairs": 72,
                }
            )
            for label, report in (
                ("oversized resume", oversized_budget_report),
                (
                    "overrun terminal checkpoint",
                    {
                        **copy.deepcopy(allowed_budget_report),
                        "sequentialGate": {"signalPair": 64},
                    },
                ),
            ):
                try:
                    _verify_assessment_budget(
                        "equal-node",
                        report,
                        budget_prior,
                        label=f"synthetic {label}",
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError(
                        f"{label} escaped launch-budget enforcement"
                    )

            events = _gate_events("development")
            events.parent.mkdir(parents=True)
            events.write_bytes(b'{"RecordType":"run"}\n')
            prefix = core._identity(events)
            events.write_bytes(
                b'{"RecordType":"run"}\n{"RecordType":"gameStart"}\n'
            )
            _verify_event_prefix(
                events,
                prefix,
                exact=False,
                label="synthetic append-only events",
            )
            try:
                _verify_event_prefix(
                    events,
                    prefix,
                    exact=True,
                    label="synthetic terminal events",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "terminal event identity allowed an appended log"
                )
            try:
                _require_exact_resume_checkpoint(
                    "development",
                    [{"events": prefix, "terminal": False}],
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "resume accepted growth after the latest assessment"
                )
            try:
                _require_exact_resume_checkpoint("development", [])
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "resume without a prior assessment was accepted"
                )
            events.write_bytes(
                b'{"RecordType":"bad"}\n{"RecordType":"gameStart"}\n'
            )
            try:
                _verify_event_prefix(
                    events,
                    prefix,
                    exact=False,
                    label="synthetic tampered events",
                )
            except ValueError:
                pass
            else:
                raise AssertionError("tampered event prefix was accepted")

            run_events = root / "strict-run-events.jsonl"
            run_events.write_text(
                (
                    '{"RecordType":"run",'
                    '"CreatedUtc":"2026-07-19T00:00:00Z"}\n'
                ),
                encoding="utf-8",
            )
            _run_created_utc(run_events)
            run_events.write_text(
                (
                    '{"RecordType":"run","recordtype":"run",'
                    '"CreatedUtc":"2026-07-19T00:00:00Z"}\n'
                ),
                encoding="utf-8",
            )
            try:
                _run_created_utc(run_events)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "case-insensitive duplicate event field was accepted"
                )

            globals()["FUTURE_RUN_ROOT"] = root / "envelope-root"
            outer_file = root / "synthetic-outer.json"
            inner_file = root / "synthetic-inner.json"
            outer_file.write_text('{"outer":true}\n', encoding="utf-8")
            inner_file.write_text('{"inner":true}\n', encoding="utf-8")
            outer_identity = core._identity(outer_file)
            inner_identity = core._identity(inner_file)
            envelope_events = _gate_events("development")
            envelope_events.parent.mkdir(parents=True)
            launch_intent = _publish_launch_intent(
                gate="development",
                outer_identity=outer_identity,
                inner_identity=inner_identity,
                predecessor_identity=None,
                authorization_identity=None,
                history=[],
                completed_launches=[],
            )
            envelope_events.write_text(
                '{"RecordType":"run"}\n', encoding="utf-8"
            )
            launch_completion = _publish_launch_completion(
                "development", launch_intent
            )
            event_identity = core._identity(envelope_events)
            _require_latest_launch_event_identity(
                "development", launch_completion["value"]
            )
            envelope_bytes = envelope_events.read_bytes()
            with envelope_events.open("ab") as stream:
                stream.write(b'{"RecordType":"direct-bypass"}\n')
            try:
                _require_latest_launch_event_identity(
                    "development", launch_completion["value"]
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "direct event growth after launch completion was accepted"
                )
            envelope_events.write_bytes(envelope_bytes)
            _require_latest_launch_event_identity(
                "development", launch_completion["value"]
            )
            progress = {
                "finishedGames": 64,
                "completePairs": 32,
                "incompleteOpenings": [],
                "inProgressAttempts": [],
                "completeBalancedPrefixBlocks": 8,
                "balancedPrefixPairs": 32,
                "completePairsOutsideBalancedPrefix": 0,
            }
            core_report = {
                "schemaVersion": 1,
                "kind": "omega-nnue-king-state-v1-match-assessment",
                "createdUtc": core._utc_now(),
                "gate": "development",
                "seal": inner_identity,
                "events": event_identity,
                "runId": "synthetic-development",
                "progress": progress,
                "safety": {
                    "illegalMoves": 0,
                    "illegalPvs": 0,
                    "protocolFailures": 0,
                    "timeForfeits": 0,
                    "abandonedAttempts": 0,
                    "failures": 0,
                    "passes": True,
                },
                "developmentScreen": {
                    "requiredPairs": 32,
                    "candidateScore": 0.5,
                    "minimumCandidateScore": 0.4,
                    "complete": True,
                    "decision": "pass",
                },
            }
            envelope = _assessment_value(
                gate="development",
                sequence=1,
                outer_identity=outer_identity,
                inner_identity=inner_identity,
                events_identity=event_identity,
                prior_identity=None,
                predecessor_identity=None,
                authorization_identity=None,
                launch_completion_identity=launch_completion["identity"],
                core_report=core_report,
                decision="pass",
            )
            assessment_path = _gate_assessment_path("development", 1)
            core._exclusive_json(assessment_path, envelope)

            original_core_assess = core._assess
            original_core_verify_seal = core._verify_seal

            def synthetic_verify_seal(path: Path) -> dict[str, Any]:
                if _resolve(path) != _resolve(inner_file):
                    raise AssertionError(
                        "terminal recheck used a noncanonical inner seal"
                    )
                return {
                    "gates": {
                        "development": {
                            "runId": "synthetic-development",
                        }
                    }
                }

            def synthetic_assess(
                args: argparse.Namespace,
            ) -> dict[str, Any]:
                if (
                    args.gate != "development"
                    or _resolve(args.seal) != _resolve(inner_file)
                    or _resolve(args.events) != _resolve(envelope_events)
                    or args.idle_attestation is not None
                ):
                    raise AssertionError(
                        "terminal recheck was not bound to sealed inputs"
                    )
                authoritative = copy.deepcopy(core_report)
                authoritative["createdUtc"] = core._utc_now()
                core._exclusive_json(args.output, authoritative)
                return authoritative

            core._verify_seal = synthetic_verify_seal
            core._assess = synthetic_assess
            try:
                verified_envelope = _validate_assessment(
                    assessment_path,
                    gate="development",
                    sequence=1,
                    outer_identity=outer_identity,
                    inner_identity=inner_identity,
                    prior_identity=None,
                    predecessor_identity=None,
                    authorization_identity=None,
                )
                if verified_envelope["authorizedSuccessor"] != "equal-node":
                    raise AssertionError(
                        "development pass did not authorize equal-node"
                    )

                semantic_mutations: list[tuple[str, dict[str, Any]]] = []
                for label in (
                    "runId",
                    "safety",
                    "progress",
                    "developmentScreen",
                    "decision",
                ):
                    mutated = copy.deepcopy(core_report)
                    if label == "runId":
                        mutated["runId"] = "forged-development"
                    elif label == "safety":
                        mutated["safety"]["illegalMoves"] = 1
                    elif label == "progress":
                        mutated["progress"]["finishedGames"] = 62
                    elif label == "developmentScreen":
                        mutated["developmentScreen"]["candidateScore"] = 0.75
                    else:
                        mutated["developmentScreen"]["decision"] = "fail"
                    semantic_mutations.append((label, mutated))
                for label, mutated in semantic_mutations:
                    try:
                        _verify_terminal_core_assessment(
                            mutated,
                            gate="development",
                            inner_identity=inner_identity,
                            events_identity=event_identity,
                        )
                    except ValueError:
                        pass
                    else:
                        raise AssertionError(
                            "terminal assessment accepted forged "
                            f"{label} semantics"
                        )

                history = _assessment_history(
                    "development",
                    outer_identity=outer_identity,
                    inner_identity=inner_identity,
                    predecessor_identity=None,
                    authorization_identity=None,
                )
                if len(history) != 1 or history[0]["terminal"] is not True:
                    raise AssertionError("terminal assessment history changed")
                launches, pending = _launch_history(
                    "development",
                    outer_identity=outer_identity,
                    inner_identity=inner_identity,
                    predecessor_identity=None,
                    authorization_identity=None,
                    history=history,
                )
                if len(launches) != 1 or pending is not None:
                    raise AssertionError(
                        "append-only launch evidence chain changed"
                    )
                decision = _decision_value(
                    gate="development",
                    outer_identity=outer_identity,
                    inner_identity=inner_identity,
                    assessment_identity=core._identity(assessment_path),
                    assessment=verified_envelope,
                    predecessor_identity=None,
                    authorization_identity=None,
                )
                if (
                    decision["passed"] is not True
                    or decision["retryAllowed"] is not False
                ):
                    raise AssertionError(
                        "terminal no-retry decision changed"
                    )
                try:
                    core._exclusive_json(assessment_path, envelope)
                except FileExistsError:
                    pass
                else:
                    raise AssertionError(
                        "canonical assessment evidence was overwritten"
                    )
            finally:
                core._assess = original_core_assess
                core._verify_seal = original_core_verify_seal
        finally:
            globals()["OUTER_SEAL"] = original_outer
            globals()["FUTURE_RUN_ROOT"] = original_future
    print("king_state_matches_generation3 self-test passed", flush=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sample")
    commands.add_parser("seal")
    verify = commands.add_parser("verify")
    verify.add_argument("--seal", type=Path, default=OUTER_SEAL)
    attest = commands.add_parser("attest")
    attest.add_argument("--output", type=Path)
    attest.add_argument("--operator")
    launch = commands.add_parser("launch")
    launch.add_argument("--gate", required=True, choices=GATE_ORDER)
    launch.add_argument(
        "--action", required=True, choices=("run", "resume")
    )
    repair = commands.add_parser("repair-transition")
    repair.add_argument(
        "--gate",
        required=True,
        choices=("equal-node", "equal-time"),
    )
    assess = commands.add_parser("assess")
    assess.add_argument(
        "--gate", required=True, choices=GATE_ORDER
    )
    assess.add_argument("--events", type=Path)
    assess.add_argument("--output", type=Path)
    assess.add_argument("--idle-attestation", type=Path)
    verify_decision = commands.add_parser("verify-decision")
    verify_decision.add_argument("--gate", required=True, choices=GATE_ORDER)
    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "sample":
        _sample()
    elif args.command == "seal":
        _seal()
    elif args.command == "verify":
        value = _verify_outer(args.seal)
        print(
            "Verified generation-3 match seal for "
            f"{value['selectedNetwork']['sha256']}",
            flush=True,
        )
    elif args.command == "attest":
        _ordered_attest(args)
    elif args.command == "launch":
        _ordered_launch(args)
    elif args.command == "repair-transition":
        _ordered_repair_transition(args)
    elif args.command == "assess":
        _ordered_assess(args)
    elif args.command == "verify-decision":
        value = _verify_gate_decision(args.gate)
        print(
            f"Verified generation-3 {args.gate} decision="
            f"{value['decision']}",
            flush=True,
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
        raise SystemExit(1)
