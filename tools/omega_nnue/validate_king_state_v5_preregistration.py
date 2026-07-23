"""Validate the generation-5 Omega NNUE preregistration template or freeze.

Template mode requires the deliberately unresolved development-grid fields.
Frozen mode rejects every placeholder and requires the activation strengths to
match an independently hashed grid-selection seal.  The checks here focus on
the immutable experiment envelope: fresh namespaces and seeds, decision-data
quotas and fail-closed gates, the A/B/C architecture matrix, health-first
checkpointing, and the unchanged deployment-health thresholds.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

import king_state_generation5_runtime as runtime_contract


_IMPORTED_RUNTIME_CONTRACT = runtime_contract
_RUNTIME_VERIFY_MANIFEST = runtime_contract.verify_manifest


REPO = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = (
    REPO
    / "validation"
    / "omega-nnue-king-state-v5-preregistration.template.json"
)
SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v5-omega-decision-v2"
TEMPLATE_KIND = "omega-nnue-king-state-v5-preregistration-template"
FROZEN_KIND = "omega-nnue-king-state-v5-preregistration"
TEMPLATE_STATUS = (
    "draft-not-executable-until-development-grid-and-implementation-"
    "identities-are-sealed"
)
FROZEN_STATUS = "target-blind-generation-5-design-frozen-before-teacher-labels"
GRID_PLACEHOLDER = "<FROM_FROZEN_DEVELOPMENT_GRID>"
CLOSURE_PLACEHOLDER = "<FROM_VERIFIED_G4_CLOSURE>"
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
GENERATION4_CLOSURE_PATH = (
    "validation/omega-nnue-king-state-v4-structural-abort.seal.json"
)
GENERATION4_ABORT_VERIFIER_PATH = (
    "tools/omega_nnue/king_state_generation4_abort.py"
)

EXPECTED_TOP_LEVEL = {
    "schemaVersion",
    "kind",
    "profileId",
    "createdUtc",
    "status",
    "purpose",
    "generation4Closure",
    "informationBoundary",
    "immutability",
    "namespaces",
    "seeds",
    "freshDecisionCorpus",
    "preLabelAbortGates",
    "finalFreezeIdentities",
    "deploymentCompatibility",
    "initializer",
    "activationControlDevelopmentGrid",
    "rankingObjective",
    "candidateMatrix",
    "deploymentHealthGate",
    "healthFirstCheckpointSelection",
    "validationSelection",
    "postSelection",
}

EXPECTED_NAMESPACES = {
    "dataProfile": "omega-decision-v2",
    "dataRoot": "build-msvc/data-generation/omega-decision-v2",
    "trainingRoot": "build-msvc/king-state-v5",
    "developmentGridRoot": "build-msvc/king-state-v4-development",
    "offlineRoot": "build-msvc/king-state-v5/offline",
    "matchRoot": "build-king-state-v5/matches",
    "candidateBundles": {
        "G5A": "build-msvc/king-state-v5/G5A.bundle",
        "G5B": "build-msvc/king-state-v5/G5B.bundle",
        "G5C": "build-msvc/king-state-v5/G5C.bundle",
    },
    "robustnessBundle": "build-msvc/king-state-v5/robustness.bundle",
    "robustnessClaim": (
        "build-msvc/king-state-v5/.robustness.training.claim.json"
    ),
    "robustnessFailure": "build-msvc/king-state-v5/robustness.failure.json",
    "selectionSeal": (
        "build-msvc/king-state-v5/validation-selection.seal.json"
    ),
    "robustnessSeal": "build-msvc/king-state-v5/robustness.seal.json",
    "preLabelSeal": (
        "build-msvc/data-generation/omega-decision-v2/"
        "prelabel-freeze.seal.json"
    ),
    "finalPreregistration": (
        "validation/omega-nnue-king-state-v5-preregistration.json"
    ),
    "finalFreezeSeal": "validation/omega-nnue-king-state-v5-freeze.seal.json",
    "offlineAccessClaim": (
        "build-msvc/king-state-v5/offline/access-claim.json"
    ),
    "offlineReport": "build-msvc/king-state-v5/offline/report.json",
    "matchStages": {
        "development": "build-king-state-v5/matches/development",
        "equalNode": "build-king-state-v5/matches/equal-node",
        "equalTime": "build-king-state-v5/matches/equal-time",
    },
}

EXPECTED_SEEDS = {
    "source": 2026072301,
    "rootSample": 2026072302,
    "siblingExploration": 2026072303,
    "componentSplit": 2026072304,
    "teacher": 2026072305,
    "training": 2026072306,
    "offlineBootstrap": 2026072307,
    "match": 2026072308,
}

# This is deliberately an independent, in-code copy of the complete frozen
# decision-corpus recipe.  Loading the expected value from the profile being
# validated would let a profile mutation redefine its own contract.  Keep the
# comparison recursive and type-strict so removed, added, renamed, or weakened
# fields all fail closed (including JSON ``true`` being replaced by ``1``).
EXPECTED_FRESH_DECISION_CORPUS = {
    "rootQuota": {
        "completeRoots": 6144,
        "completeRootsPerPhase": 1536,
        "reserveRootsPerPhase": 256,
        "reserveFraction": 0.14285714285714285,
        "maximumCandidateRoots": 7168,
        "phasesInFrozenOrder": [
            "opening",
            "middlegame",
            "late",
            "endgame",
        ],
    },
    "selectionFunnel": {
        "rawRootsPerPhaseAndSide": 896,
        "feasibleShallowRootsPerPhaseAndSide": 768,
        "deepRootsPerPhaseAndSide": 640,
        "finalRootsPerPhaseAndSide": 512,
        "bucketLocalSlackAtEachTransition": 128,
        "crossBucketBorrowing": False,
        "minimumDistinctLegalChildren": 5,
        "sourcePvOccurrencesRequired": 1,
        "rawLeakageGraphBuiltBeforeFiltering": True,
        "rootAdvancementOrder": (
            "frozen target-blind feasible-root rank; shallow scores are used "
            "only to select four children inside one root"
        ),
    },
    "source": {
        "freshRunRequired": True,
        "hceOnly": True,
        "requiredRunSeed": 2026072301,
        "requiredDataProfile": "omega-decision-v2",
        "requiredEngineSha256MustBeExplicit": True,
        "sourceGamesMustBeAbsentFromEveryPriorNnueCorpus": True,
        "sourceGameIsAnIndivisibleLeakageComponent": True,
        "colorSwappedGamesFromOneOpeningAreOneSourceGroup": True,
        "atMostOneRootPerOpeningSourceGroup": True,
        "duplicatesInsideOneOpeningGroupAreDeterministicallyCollapsed": True,
        "duplicateExactOrOrbitPositionsAcrossDifferentSourceGroups": "abort",
        "rootSelectionMayReadTargetsOrScores": False,
        "rootSelectionUsesSeed": 2026072302,
        "sourceDataDisjointness": {
            "policyId": "omega-source-data-input-disjointness-v1",
            "dataInputIdentityFields": [
                "source",
                "openingSuite",
                "sourceMatchConfig",
                "sourceRootPool",
                "sourceRootPoolManifest",
                "sourceRootPoolSeal",
            ],
            "reusableInfrastructureIdentityFields": [
                "sourceMatchHarnessAssembly",
                "rootSamplerAssembly",
                "rootSamplerChessLibAssembly",
                "sourceOpeningBuilderSource",
            ],
            "launchProvenanceIdentityFields": [
                "sourceMatchCompletionSeal",
            ],
            "currentAndPriorDataInputDomainsIdentical": True,
            "priorDataInputsWithoutOfenAuthenticatedByStructuralClosure": True,
            "reusableInfrastructureContentAuthenticated": True,
            "reusableInfrastructureExcludedFromCollisionSet": True,
            "catalogSourceArtifactHashesExcludedFromDataCollisionSet": True,
            "legacyG3TransitiveArtifactHashesAuthenticatedButExcludedFromDataCollisionSet": True,
            "launchProvenanceAuthenticatedAndExcludedFromCollisionSet": True,
        },
        "rulesOnlyOpeningPool": {
            "generatorSeed": 2026072301,
            "trajectoryPairs": 10240,
            "independentTrajectoriesPerPair": 2,
            "workers": 4,
            "maximumPliesPerTrajectory": 220,
            "positionsRetainedPerPhaseAndSide": 2,
            "captureSelectionPercent": 72,
            "selectedOpeningsPerPhaseAndSide": 896,
            "atMostOneOpeningPerTrajectoryPair": True,
            "selectionReadsEvaluationTargetsOrScores": False,
            "zeroOutputTrajectoryPolicy": {
                "pinnedSamplerMustExecuteBothFlavorsPerPair": True,
                "absenceOfRowsMeansNoEligiblePositionWasRetained": True,
                "everyPairMustStillEmitAtLeastOneFlavor": True,
                "zeroOutputCountMustNotExceedAuthenticatedTerminalTrajectories": True,
            },
            "crossPairDuplicateExclusion": {
                "policy": "exclude-all-copies-before-pair-assignment",
                "mustBeRecomputedFromPinnedRawPool": True,
            },
            "selectedOpeningContract": {
                "initialOfensCanonicalAndUnique": True,
                "movesExactlyEmpty": True,
                "trajectoryPairTagsUnique": True,
            },
        },
        "choiceProbe": {
            "colorSwappedGamesPerOpening": 2,
            "sameFrozenHceBinaryForBothEngineIds": True,
            "fixedNodesPerMove": 2000,
            "absoluteMaximumPliesPerGame": 1,
            "exactlyOnePlyRequiredForEveryAcceptedGame": True,
            "completeAbBaPairRequiredForEveryAcceptedOpening": True,
            "sourceOpeningSuiteAndHarnessBundleHashesMustMatchRunRecord": True,
        },
    },
    "teacherEngine": {
        "sameFrozenHceExecutableForEverySearch": True,
        "UseOmegaNNUE": False,
        "OmegaNNUEFile": "<empty>",
        "Threads": 1,
        "HashMiB": 128,
        "Ponder": False,
        "OwnBook": False,
        "UCI_Variant": "omega",
    },
    "legalChildExpansion": {
        "expandAllRawRootsBeforeFiltering": True,
        "enumerateEveryLegalChildExactlyOnce": True,
        "legalityMustMatchPinnedChessLib": True,
        "childIdentityCollisionPolicy": "abort",
        "missingOrExtraChildPolicy": "abort",
        "minimumDistinctLegalChildrenForRetention": 5,
        "sourcePvMustOccurExactlyOnce": True,
        "filteredChildrenMustEqualExactRawSubset": True,
        "historicalExactOrbitAndSourceCollisions": "abort before retention",
    },
    "shallowScreen": {
        "fixedNodesPerChild": 2000,
        "acceptExactCpOnly": True,
        "rejectMateScores": True,
        "rejectBoundScores": True,
        "rejectShortNodeSearches": True,
        "appendOnlyFsyncedLedger": True,
        "preLabelSealRequired": True,
    },
    "deepSelection": {
        "selectedChildrenPerRoot": 4,
        "principalVariationChildIncludedWhenAvailable": True,
        "seriousCandidates": 3,
        "seriousCandidateRule": (
            "best shallow-ranked distinct legal children until the "
            "principal-variation child plus serious set contains three "
            "children"
        ),
        "hardNegativeRule": (
            "one deterministic sibling sampled from shallow ranks 4 through "
            "12 inclusive using siblingExploration seed; if fewer than twelve "
            "exist use every available rank in that interval; if the interval "
            "is empty reject the root"
        ),
        "deepRootsPerPhaseAndSide": 640,
        "primaryRootsPerPhaseAndSide": 512,
        "deepReserveRootsPerPhaseAndSide": 128,
        "rootAdvancementUsesShallowScores": False,
        "rootAdvancementOrder": "frozen target-blind feasible-root rank",
        "crossBucketBorrowing": False,
        "selectionUsesDeepTargets": False,
        "selectionUsesGameOutcome": False,
    },
    "deepTeacher": {
        "fixedNodesPerSelectedChild": 50000,
        "acceptExactCpOnly": True,
        "rejectMateScores": True,
        "rejectBoundScores": True,
        "rejectShortNodeSearches": True,
        "appendOnlyFsyncedLedger": True,
        "wholeRootRejectedIfAnySelectedChildIsMissingOrInvalid": True,
        "preLabelSealRequired": True,
    },
    "teacherSearchRuntime": {
        "workers": 4,
        "onePersistentUciProcessPerWorker": True,
        "uciNewGameBeforeEveryChild": True,
        "clearHashBeforeEveryChild": True,
        "restartWorkerProcessAfterAnyProtocolOrSearchError": True,
        "workerCountAndSessionPolicyPinnedInEveryLedgerLock": True,
    },
    "artifacts": [
        "raw-roots.jsonl",
        "raw-children.jsonl",
        "root-feasibility.jsonl",
        "root-feasibility.seal.json",
        "roots.jsonl",
        "children.jsonl",
        "shallow-results.jsonl",
        "selected-children.jsonl",
        "deep-results.jsonl",
        "decision-labels.jsonl",
    ],
    "artifactPolicy": {
        "everyArtifactHasANoClobberManifest": True,
        "resultLedgersAreAppendOnlyAndFsynced": True,
        "publishedDatasetsUseAtomicNonReplacingRename": True,
        "manifestsPinPathBytesSha256RowCountAndProducerIdentity": True,
        "finalStageSealIsThePublicationCommitPoint": True,
    },
    "leakageComponent": {
        "unit": "global decision component id",
        "graphBuiltFromCompleteRawRootAndChildInventoryBeforeFiltering": True,
        "filteredRootsInheritRawComponentIds": True,
        "mustUnify": [
            "all selected siblings from one root",
            "all ancestors and descendants from one source trajectory",
            "all roots from one source game",
            "all exact transpositions",
            "all conservative Omega symmetry-orbit equivalents",
        ],
        "crossComponentExactOfenCollisions": 0,
        "crossComponentConservativeOrbitCollisions": 0,
    },
    "split": {
        "seed": 2026072304,
        "unit": "whole global decision component id",
        "namesInFrozenOrder": ["train", "validation", "heldOut"],
        "trainPercent": 80,
        "validationPercent": 10,
        "heldOutPercent": 10,
        "routeWholeSourceGameTogether": True,
        "collisionPolicy": "abort",
        "everyPhaseMustBeNonemptyInEverySplit": True,
        "componentAndSplitMapFrozenBeforeFirstTeacherSearch": True,
        "finalizerMustReuseExactPreLabelMap": True,
    },
}

EXPECTED_HEALTH = {
    "thresholdsChangedFromGeneration3": False,
    "metricSubject": (
        "deployment-equivalent float checkpoint reconstructed from the "
        "exported quantized network"
    ),
    "maximumMeanFloatQuantizationPenaltyCp": 2,
    "maximumSingleFloatQuantizationPenaltyCp": 10,
    "maximumSaturatedDenseUnits": 0,
    "maximumDeadDenseUnits": 8,
    "deadDenseUnitsMayExceedI0": False,
    "denseActiveFractionRangeInclusive": [0.2, 0.75],
    "maximumAbsoluteCorrectionCp": 2500,
    "requireFinitePredictions": True,
    "requireExpectedFileSize": True,
    "requireRoundTripHash": True,
    "requireDeploymentFloatParameterRoundTripExact": True,
    "requireDeploymentFloatRequantizationByteIdentical": True,
    "wholeCorpusExactPythonCppIntegerAgreement": True,
    "wholeCorpusMigrationParityForI0": True,
}

EXPECTED_ELIGIBILITY = {
    "minimumRelativeHuberImprovementOverI0": 0.005,
    "mustLowerPhaseMacroCpMaeVersusI0": True,
    "mustHaveLowerHuberLossThanZeroResidual": True,
    "maximumAnyPhaseCpMaeRegressionVersusI0": 5,
    "mustPassEveryDeploymentHealthGate": True,
}

EXPECTED_POST_SELECTION = {
    "robustness": {
        "requiredBeforeHeldOut": True,
        "selectedValidationWinnerRecipeOnly": True,
        "initializer": "mapped K2 architecture-4 migration I0",
        "factorBasis": (
            "exact selected-candidate factor-basis bytes already sealed in "
            "the pre-target training plan"
        ),
        "trainingSeedBase": 2026072306,
        "trainingSeedPurpose": "robustness-training",
        "trainingSeedDerivation": (
            "SHA-256 of UTF-8 '<base-seed>|<purpose>|<candidate-id>|<rank>'; "
            "first eight digest bytes interpreted as unsigned little-endian"
        ),
        "runAll48EpochsWithIdenticalHealthFirstCheckpointing": True,
        "mustPassDeploymentHealth": True,
        "commonValidationGate": (
            "strictly lower phase-macro group-balanced quantized residual "
            "Huber loss than both I0 and zero residual"
        ),
        "mayReplaceSelectedPrimary": False,
        "outputs": {
            "bundle": "build-msvc/king-state-v5/robustness.bundle",
            "claim": (
                "build-msvc/king-state-v5/.robustness.training.claim.json"
            ),
            "failure": "build-msvc/king-state-v5/robustness.failure.json",
            "seal": "build-msvc/king-state-v5/robustness.seal.json",
        },
        "publication": (
            "exclusive no-clobber files and atomically renamed complete "
            "bundle; interrupted claim or committed artifacts are preserved"
        ),
    },
    "oneTimeHeldOut": {
        "accessClaimMustBeAtomicallyPublishedBeforeTargetDecode": True,
        "accessClaim": "build-msvc/king-state-v5/offline/access-claim.json",
        "report": "build-msvc/king-state-v5/offline/report.json",
        "resumePolicy": (
            "reuse the canonical immutable access claim after interruption; "
            "never publish a second claim"
        ),
        "passedRobustnessSealRequired": True,
        "selectedCandidateOnly": True,
        "minimumRelativeHuberImprovementAgainstI0": 0.01,
        "minimumRelativeHuberImprovementAgainstZeroResidual": 0.01,
        "minimumPhaseMacroCpMaeImprovementVersusI0": 2,
        "maximumAnyPhaseCpMaeRegressionVersusI0": 5,
        "bootstrapReplicates": 10000,
        "bootstrapSeed": 2026072307,
        "bootstrapRng": "NumPy Generator PCG64",
        "bootstrapResamplingUnit": "whole four-child root",
        "bootstrapStratification": (
            "phase in frozen opening,middlegame,late,endgame order"
        ),
        "bootstrapMetric": "phase-macro group-balanced Huber loss",
        "bootstrapStatistic": (
            "(I0 phase-macro group-balanced Huber - candidate) / I0"
        ),
        "bootstrapQuantile": 0.05,
        "bootstrapQuantileMethod": "linear",
        "bootstrapOneSidedConfidenceLevel": 0.95,
        "lowerConfidenceBoundMustBePositive": True,
        "onlySelectedPrimaryNetworkIsEvaluated": True,
        "robustnessNetworkHeldOutTargetEvaluations": 0,
        "runnerUpNetworkHeldOutTargetEvaluations": 0,
        "runnerUpFallback": False,
    },
    "matches": {
        "rootAndSourceGameDisjointFromTrainValidationHeldOut": True,
        "matchSeed": 2026072308,
        "development": "paired fixed-root safety gate",
        "equalNode": "paired sequential strength gate",
        "equalTime": "paired serialized practical-strength gate",
        "zeroIllegalMovesPvsProtocolFailuresTimeForfeitsOrAbandonedAttempts": True,
        "formalPromotionRuleMustBePinnedBeforeMatchLaunch": True,
    },
}

EXPECTED_TEMPLATE_PLACEHOLDERS = {
    "generation4Closure.bytes": CLOSURE_PLACEHOLDER,
    "generation4Closure.sha256": CLOSURE_PLACEHOLDER,
    "activationControlDevelopmentGrid.seal.bytes": GRID_PLACEHOLDER,
    "activationControlDevelopmentGrid.seal.sha256": GRID_PLACEHOLDER,
}

EXPECTED_FINAL_IDENTITY_KEYS = {
    "mappedK2Initializer",
    "mappedK2InitializerManifest",
    "teacherEngineExecutable",
    "cppStaticAndNnueEvaluator",
    "sourceMatchHarnessAssembly",
    "sourceOpeningBuilderSource",
    "finalFreezeBuilderSource",
    "sourceRootPool",
    "sourceRootPoolManifest",
    "sourceRootPoolSeal",
    "decisionSamplerAssembly",
    "chessLibAssembly",
    "rootSamplerAssembly",
    "decisionTeacherSource",
    "deepHceV2Source",
    "selectScreenSource",
    "trainerSource",
    "baseTrainerSource",
    "pythonRuntimeManifest",
    "pythonRuntimeToolSource",
    "generation4AbortVerifierSource",
    "priorProjectionSource",
    "matchCoreSource",
    "dotnetRuntimeHostExecutable",
    "dotnetRuntimeManifest",
    "dotnetRuntimeToolSource",
    "matchProtocol",
    "matchProtocolTool",
    "matchReadinessTool",
    "matchOrchestrator",
    "networkFormatPythonSource",
    "networkRuntimeCppSource",
    "networkRuntimeHeaderSource",
    "preregistrationValidatorSource",
    "sourceEvents",
    "sourceOpeningSuite",
    "sourceMatchConfig",
    "sourceMatchCompletionSeal",
    "rawRoots",
    "rawRootsManifest",
    "rawChildren",
    "rawChildrenManifest",
    "rawSamplerCompletionSeal",
    "rootFeasibility",
    "rootFeasibilityManifest",
    "rootFeasibilitySeal",
    "roots",
    "rootsManifest",
    "children",
    "childrenManifest",
    "samplerCompletionSeal",
    "forbiddenPositionCatalogManifests",
}

EXPECTED_FINAL_IDENTITY_PATHS = {
    "mappedK2Initializer": "build-msvc/king-state-v2/K2.nnue",
    "mappedK2InitializerManifest": (
        "build-msvc/king-state-v2/K2.manifest.json"
    ),
    "teacherEngineExecutable": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/engine/senpai.exe"
    ),
    "cppStaticAndNnueEvaluator": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/evaluator/omega_nnue.exe"
    ),
    "sourceMatchHarnessAssembly": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/"
        "OmegaMatch.dll"
    ),
    "sourceOpeningBuilderSource": (
        "tools/omega_nnue/king_state_generation5_source.py"
    ),
    "finalFreezeBuilderSource": (
        "tools/omega_nnue/king_state_generation5_freeze.py"
    ),
    "sourceRootPool": (
        "build-msvc/data-generation/omega-decision-v2/source/"
        "rules-only-pool.jsonl"
    ),
    "sourceRootPoolManifest": (
        "build-msvc/data-generation/omega-decision-v2/source/"
        "rules-only-pool.jsonl.manifest.json"
    ),
    "sourceRootPoolSeal": (
        "build-msvc/data-generation/omega-decision-v2/source/"
        "rules-only-pool.jsonl.complete.seal.json"
    ),
    "decisionSamplerAssembly": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/decision-sampler/"
        "OmegaDecisionSampler.dll"
    ),
    "chessLibAssembly": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/decision-sampler/"
        "ChessLib.dll"
    ),
    "rootSamplerAssembly": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/"
        "OmegaRootSampler.dll"
    ),
    "decisionTeacherSource": (
        "tools/omega_nnue/omega_decision_teacher_generation5.py"
    ),
    "deepHceV2Source": "tools/omega_nnue/deep_hce_v2.py",
    "selectScreenSource": "tools/omega_nnue/select_screen.py",
    "trainerSource": "tools/omega_nnue/king_state_train_generation5.py",
    "baseTrainerSource": "tools/omega_nnue/train.py",
    "pythonRuntimeManifest": (
        "validation/omega-nnue-king-state-v5-python-runtime.json"
    ),
    "pythonRuntimeToolSource": (
        "tools/omega_nnue/king_state_generation5_runtime.py"
    ),
    "generation4AbortVerifierSource": GENERATION4_ABORT_VERIFIER_PATH,
    "priorProjectionSource": (
        "tools/omega_nnue/king_state_generation4_prior_projection.py"
    ),
    "matchCoreSource": "tools/omega_nnue/king_state_matches.py",
    "dotnetRuntimeHostExecutable": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime/"
        "dotnet.exe"
    ),
    "dotnetRuntimeManifest": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/"
        "dotnet-runtime.manifest.json"
    ),
    "dotnetRuntimeToolSource": (
        "tools/omega_nnue/king_state_dotnet_runtime_generation5.py"
    ),
    "matchProtocol": (
        "validation/omega-nnue-king-state-v5-match-protocol.json"
    ),
    "matchProtocolTool": (
        "tools/omega_nnue/king_state_match_protocol_generation5.py"
    ),
    "matchReadinessTool": (
        "tools/omega_nnue/king_state_match_readiness_generation5.py"
    ),
    "matchOrchestrator": (
        "tools/omega_nnue/king_state_matches_generation5.py"
    ),
    "networkFormatPythonSource": "tools/omega_nnue/omega_nnue.py",
    "networkRuntimeCppSource": "src/omega_nnue.cpp",
    "networkRuntimeHeaderSource": "src/omega_nnue.hpp",
    "preregistrationValidatorSource": (
        "tools/omega_nnue/validate_king_state_v5_preregistration.py"
    ),
    "sourceEvents": (
        "build-msvc/data-generation/omega-decision-v2/source/events.jsonl"
    ),
    "sourceOpeningSuite": (
        "build-msvc/data-generation/omega-decision-v2/source/openings.json"
    ),
    "sourceMatchConfig": (
        "build-msvc/data-generation/omega-decision-v2/source/source-match.json"
    ),
    "sourceMatchCompletionSeal": (
        "build-msvc/data-generation/omega-decision-v2/source/"
        "source-match.complete.seal.json"
    ),
    "rawRoots": "build-msvc/data-generation/omega-decision-v2/raw-roots.jsonl",
    "rawRootsManifest": (
        "build-msvc/data-generation/omega-decision-v2/raw-roots.jsonl.manifest.json"
    ),
    "rawChildren": (
        "build-msvc/data-generation/omega-decision-v2/raw-children.jsonl"
    ),
    "rawChildrenManifest": (
        "build-msvc/data-generation/omega-decision-v2/raw-children.jsonl.manifest.json"
    ),
    "rawSamplerCompletionSeal": (
        "build-msvc/data-generation/omega-decision-v2/"
        "raw-children.jsonl.complete.seal.json"
    ),
    "rootFeasibility": (
        "build-msvc/data-generation/omega-decision-v2/root-feasibility.jsonl"
    ),
    "rootFeasibilityManifest": (
        "build-msvc/data-generation/omega-decision-v2/"
        "root-feasibility.jsonl.manifest.json"
    ),
    "rootFeasibilitySeal": (
        "build-msvc/data-generation/omega-decision-v2/root-feasibility.seal.json"
    ),
    "roots": "build-msvc/data-generation/omega-decision-v2/roots.jsonl",
    "rootsManifest": (
        "build-msvc/data-generation/omega-decision-v2/roots.jsonl.manifest.json"
    ),
    "children": "build-msvc/data-generation/omega-decision-v2/children.jsonl",
    "childrenManifest": (
        "build-msvc/data-generation/omega-decision-v2/"
        "children.jsonl.manifest.json"
    ),
    "samplerCompletionSeal": (
        "build-msvc/data-generation/omega-decision-v2/"
        "children.jsonl.complete.seal.json"
    ),
}

for _identity_name in EXPECTED_FINAL_IDENTITY_PATHS:
    EXPECTED_TEMPLATE_PLACEHOLDERS[
        f"finalFreezeIdentities.{_identity_name}.bytes"
    ] = "<PIN_BEFORE_FINAL_FREEZE>"
    EXPECTED_TEMPLATE_PLACEHOLDERS[
        f"finalFreezeIdentities.{_identity_name}.sha256"
    ] = "<PIN_BEFORE_FINAL_FREEZE>"
EXPECTED_TEMPLATE_PLACEHOLDERS[
    "finalFreezeIdentities.forbiddenPositionCatalogManifests[0]"
] = "<ONE_OR_MORE_TARGET_OPAQUE_MANIFEST_IDENTITIES_AT_FINAL_FREEZE>"

EXPECTED_TEMPLATE_PROFILE_TYPE_INVENTORY = {
    "nodes": 794,
    "scalarLeaves": 665,
    "numericOrBooleanLeaves": 320,
    "sha256": "8517c78242351dafa3f575eb8c1d03ac5e7db201e4cb37d5ed0e52e0af46953b",
}
EXPECTED_FROZEN_PROFILE_TYPE_INVENTORY = {
    "nodesExcludingForbiddenCatalogs": 792,
    "sha256": "5426030a7c9bb3d258a6049a44c4675af4f9d3b6173177c6f4535183e5b37b46",
}
FORBIDDEN_CATALOG_POINTER = (
    "/finalFreezeIdentities/forbiddenPositionCatalogManifests"
)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be an array")
    return value


def _expect(actual: Any, expected: Any, label: str) -> None:
    _expect_structure(actual, expected, label)


def _expect_structure(actual: Any, expected: Any, label: str) -> None:
    """Require an exact recursively typed JSON value with useful paths."""

    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            raise ValueError(f"{label} must be an object")
        actual_keys = set(actual)
        expected_keys = set(expected)
        if actual_keys != expected_keys:
            missing = sorted(expected_keys - actual_keys)
            extra = sorted(actual_keys - expected_keys)
            raise ValueError(
                f"{label} field inventory changed: missing={missing!r}, "
                f"extra={extra!r}"
            )
        for key, expected_item in expected.items():
            _expect_structure(actual[key], expected_item, f"{label}.{key}")
        return
    if isinstance(expected, list):
        if not isinstance(actual, list):
            raise ValueError(f"{label} must be an array")
        if len(actual) != len(expected):
            raise ValueError(
                f"{label} length changed: expected {len(expected)}, "
                f"got {len(actual)}"
            )
        for index, expected_item in enumerate(expected):
            _expect_structure(actual[index], expected_item, f"{label}[{index}]")
        return
    if type(actual) is not type(expected) or actual != expected:
        raise ValueError(
            f"{label} changed: expected {expected!r} "
            f"({type(expected).__name__}), got {actual!r} "
            f"({type(actual).__name__})"
        )


def _json_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _profile_type_inventory(
    value: Any, *, excluded_subtree: str | None = None
) -> tuple[list[str], int]:
    records: list[str] = []
    scalar_count = 0

    def walk(item: Any, pointer: str) -> None:
        nonlocal scalar_count
        if pointer == excluded_subtree:
            return
        if type(item) is dict:
            records.append(f"{pointer}\tobject")
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError(
                        f"profile object key at {pointer or '/'} is not a string"
                    )
                walk(child, f"{pointer}/{_json_pointer_part(key)}")
            return
        if type(item) is list:
            records.append(f"{pointer}\tarray")
            for index, child in enumerate(item):
                walk(child, f"{pointer}/{index}")
            return
        if type(item) is bool:
            tag = "bool"
        elif type(item) is int:
            tag = "int"
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError(f"non-finite JSON number at {pointer or '/'}")
            tag = "float"
        elif type(item) is str:
            tag = "str"
        else:
            raise ValueError(
                f"unsupported JSON value at {pointer or '/'}: "
                f"{type(item).__name__}"
            )
        records.append(f"{pointer}\t{tag}")
        scalar_count += 1

    walk(value, "")
    records.sort()
    return records, scalar_count


def _type_inventory_sha256(records: Sequence[str]) -> str:
    payload = ("\n".join(records) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_frozen_catalog_scalar_types(profile: Mapping[str, Any]) -> None:
    identities = profile.get("finalFreezeIdentities")
    if type(identities) is not dict:
        raise ValueError("frozen finalFreezeIdentities must be an object")
    catalogs = identities.get("forbiddenPositionCatalogManifests")
    if type(catalogs) is not list or not catalogs:
        raise ValueError(
            "frozen forbiddenPositionCatalogManifests must be a nonempty array"
        )
    expected_keys = {"path", "bytes", "sha256"}
    for index, identity in enumerate(catalogs):
        label = f"frozen forbidden catalog identity {index}"
        if type(identity) is not dict or set(identity) != expected_keys:
            raise ValueError(
                f"{label} must contain exactly path, bytes, and sha256"
            )
        if type(identity["path"]) is not str:
            raise ValueError(f"{label}.path must be a string")
        if type(identity["bytes"]) is not int:
            raise ValueError(f"{label}.bytes must be an integer")
        if type(identity["sha256"]) is not str:
            raise ValueError(f"{label}.sha256 must be a string")


def _validate_whole_profile_scalar_types(
    profile: Mapping[str, Any], *, mode: str
) -> None:
    if type(profile) is not dict:
        raise ValueError("preregistration profile must be a JSON object")
    if mode == "template":
        expected_nodes = EXPECTED_TEMPLATE_PROFILE_TYPE_INVENTORY["nodes"]
        expected_digest = EXPECTED_TEMPLATE_PROFILE_TYPE_INVENTORY["sha256"]
        excluded_subtree = None
    elif mode == "frozen":
        _validate_frozen_catalog_scalar_types(profile)
        expected_nodes = EXPECTED_FROZEN_PROFILE_TYPE_INVENTORY[
            "nodesExcludingForbiddenCatalogs"
        ]
        expected_digest = EXPECTED_FROZEN_PROFILE_TYPE_INVENTORY["sha256"]
        excluded_subtree = FORBIDDEN_CATALOG_POINTER
    else:
        raise ValueError(f"unknown validation mode {mode!r}")
    records, scalar_count = _profile_type_inventory(
        profile, excluded_subtree=excluded_subtree
    )
    actual_digest = _type_inventory_sha256(records)
    if len(records) != expected_nodes or actual_digest != expected_digest:
        raise ValueError(
            f"{mode} whole-profile JSON type inventory changed: "
            f"nodes={len(records)} scalarLeaves={scalar_count} "
            f"sha256={actual_digest}"
        )


def _verify_runtime_contract_binding() -> None:
    expected = (
        REPO / "tools/omega_nnue/king_state_generation5_runtime.py"
    ).resolve()
    if runtime_contract is not _IMPORTED_RUNTIME_CONTRACT:
        raise ValueError("Generation-5 Python runtime module was substituted in memory")
    if sys.modules.get("king_state_generation5_runtime") is not _IMPORTED_RUNTIME_CONTRACT:
        raise ValueError("Generation-5 Python runtime import slot was substituted")
    file_value = getattr(_IMPORTED_RUNTIME_CONTRACT, "__file__", None)
    if type(file_value) is not str or Path(file_value).resolve() != expected:
        raise ValueError("imported Python runtime verifier is noncanonical")
    if runtime_contract.verify_manifest is not _RUNTIME_VERIFY_MANIFEST:
        raise ValueError("Python runtime manifest verifier was substituted in memory")


def _load_json(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number {value!r} in {path}")

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError(f"profile is not an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} path must be a nonempty string")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\\" in value:
        raise ValueError(f"{label} path must be repo-relative POSIX: {value!r}")
    return (REPO / Path(*pure.parts)).resolve()


def _validate_identity(
    value: Any,
    label: str,
    *,
    verify_file: bool,
) -> dict[str, Any]:
    identity = dict(_mapping(value, label))
    _expect(set(identity), {"path", "bytes", "sha256"}, f"{label} fields")
    path = _repo_path(identity.get("path"), label)
    size = identity.get("bytes")
    sha = identity.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError(f"{label} bytes must be a positive integer")
    if not isinstance(sha, str) or HEX_SHA256.fullmatch(sha) is None:
        raise ValueError(f"{label} sha256 must be lowercase hexadecimal")
    if verify_file:
        if not path.is_file():
            raise ValueError(f"{label} file is absent: {path}")
        if path.stat().st_size != size or _sha256(path) != sha:
            raise ValueError(f"{label} identity does not match {path}")
    return identity


def _fully_authenticate_generation4_closure(
    profile: Mapping[str, Any],
) -> None:
    """Recompute the G4 closure in a fresh, isolated pinned-Python process."""

    closure_identity = _validate_identity(
        profile.get("generation4Closure"),
        "generation-4 structural-abort closure",
        verify_file=True,
    )
    _expect(
        closure_identity["path"],
        GENERATION4_CLOSURE_PATH,
        "generation-4 structural-abort closure path",
    )
    closure_path = _repo_path(
        closure_identity["path"], "generation-4 structural-abort closure"
    )
    closure = _load_json(closure_path)

    final_identities = _mapping(
        profile.get("finalFreezeIdentities"), "final freeze identities"
    )
    verifier_identity = _validate_identity(
        final_identities.get("generation4AbortVerifierSource"),
        "generation-4 structural-abort verifier",
        verify_file=True,
    )
    _expect(
        verifier_identity["path"],
        GENERATION4_ABORT_VERIFIER_PATH,
        "generation-4 structural-abort verifier path",
    )
    _expect_structure(
        closure.get("producer"),
        verifier_identity,
        "generation-4 closure producer binding",
    )
    verifier_path = _repo_path(
        verifier_identity["path"], "generation-4 structural-abort verifier"
    )

    runtime_manifest_path = _repo_path(
        EXPECTED_FINAL_IDENTITY_PATHS["pythonRuntimeManifest"],
        "Python runtime manifest",
    )
    frozen_runtime = runtime_contract.verify_manifest(runtime_manifest_path)["runtime"]
    python_record = _mapping(
        _mapping(frozen_runtime, "frozen Python runtime").get("python"),
        "frozen Python executable record",
    )
    executable_identity = _mapping(
        python_record.get("executable"), "frozen Python executable identity"
    )
    executable_text = executable_identity.get("path")
    if type(executable_text) is not str or not executable_text:
        raise ValueError("frozen Python executable path is invalid")
    executable = Path(executable_text).expanduser().resolve(strict=True)
    if not executable.is_file():
        raise ValueError("frozen Python executable is not a file")

    # -I excludes the caller's Python environment and user site.  The bootstrap
    # adds exactly the pinned verifier's directory so its frozen sibling imports
    # remain usable; the frozen G4 runtime verifier then authenticates NumPy.
    bootstrap = (
        "import runpy,sys;"
        "tool=sys.argv.pop(1);"
        "tool_dir=sys.argv.pop(1);"
        "sys.path.insert(0,tool_dir);"
        "sys.argv[0]=tool;"
        "runpy.run_path(tool,run_name='__main__')"
    )
    command = [
        str(executable),
        "-I",
        "-B",
        "-c",
        bootstrap,
        str(verifier_path),
        str(verifier_path.parent),
        "verify",
        "--output",
        str(closure_path),
    ]
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.upper().startswith("PYTHON"):
            environment.pop(name, None)
    try:
        completed = subprocess.run(
            command,
            cwd=REPO,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(
            "generation-4 structural-abort verifier exceeded 900 seconds"
        ) from error
    if completed.returncode != 0:
        stdout = completed.stdout[-4000:]
        stderr = completed.stderr[-4000:]
        raise ValueError(
            "generation-4 structural-abort verifier failed in pinned Python "
            f"(exit {completed.returncode}); stdout={stdout!r}; stderr={stderr!r}"
        )
    if "Verified target-opaque Generation-4 structural abort:" not in completed.stdout:
        raise ValueError(
            "generation-4 structural-abort verifier omitted its success attestation"
        )

    # Detect replacement during or immediately after the independent audit.
    _validate_identity(
        closure_identity,
        "generation-4 structural-abort closure after verification",
        verify_file=True,
    )
    _validate_identity(
        verifier_identity,
        "generation-4 structural-abort verifier after verification",
        verify_file=True,
    )


def _walk_placeholders(value: Any, prefix: str = "") -> dict[str, str]:
    found: dict[str, str] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found.update(_walk_placeholders(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]"
            found.update(_walk_placeholders(item, path))
    elif (
        isinstance(value, str)
        and value != "<empty>"
        and value.startswith("<")
        and value.endswith(">")
    ):
        found[prefix] = value
    return found


def _validate_namespaces(value: Any) -> None:
    namespaces = dict(_mapping(value, "namespaces"))
    _expect(namespaces, EXPECTED_NAMESPACES, "fresh generation-5 namespaces")
    paths: list[str] = []
    for key, item in namespaces.items():
        if key == "dataProfile":
            continue
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, Mapping):
            paths.extend(str(path) for path in item.values())
    for path in paths:
        _repo_path(path, "namespace")
    if len(paths) != len(set(paths)):
        raise ValueError("generation-5 namespace paths are not unique")
    if any("king-state-v3" in path or "deep-hce-v4" in path for path in paths):
        raise ValueError("generation-5 namespace overlaps generation 3")


def _validate_seeds(value: Any) -> None:
    seeds = _mapping(value, "seeds")
    expected_fields = {
        *EXPECTED_SEEDS,
        "domainSeparation",
        "factorBasisSeedPurpose",
        "candidateTrainingSeedPurpose",
        "robustnessTrainingSeedPurpose",
        "developmentGridSeedPurpose",
    }
    if set(seeds) != expected_fields:
        raise ValueError("generation-5 seed field inventory changed")
    for name, expected in EXPECTED_SEEDS.items():
        _expect(seeds.get(name), expected, f"{name} seed")
    numeric = [seeds[name] for name in EXPECTED_SEEDS]
    if len(numeric) != len(set(numeric)):
        raise ValueError("generation-5 seeds are not unique")
    _expect(
        seeds.get("domainSeparation"),
        "SHA-256 of UTF-8 '<base-seed>|<purpose>|<candidate-id>|<rank>'; "
        "first eight digest bytes interpreted as unsigned little-endian",
        "seed domain-separation formula",
    )
    _expect(
        seeds.get("factorBasisSeedPurpose"),
        "factor-basis",
        "factor basis domain separator",
    )
    _expect(
        seeds.get("candidateTrainingSeedPurpose"),
        "candidate-training",
        "training domain separator",
    )
    _expect(
        seeds.get("robustnessTrainingSeedPurpose"),
        "robustness-training",
        "robustness domain separator",
    )
    _expect(
        seeds.get("developmentGridSeedPurpose"),
        "activation-development-grid",
        "development-grid domain separator",
    )


def _validate_corpus(value: Any) -> None:
    corpus = _mapping(value, "freshDecisionCorpus")
    _expect_structure(
        corpus,
        EXPECTED_FRESH_DECISION_CORPUS,
        "freshDecisionCorpus",
    )


def _validate_prelabel_gates(value: Any) -> None:
    gates = _mapping(value, "preLabelAbortGates")
    checks = _mapping(gates.get("requiredChecks"), "pre-label checks")
    expected = {
        "sourceRunIsFreshAndHceOnly": True,
        "duplicateRootIds": 0,
        "invalidSixFieldOfens": 0,
        "phaseQuotaAndReserveInventoryExactlyMatchesPreregistration": True,
        "rawRootsPerPhaseAndSide": 896,
        "feasibleRootsPerPhaseAndSide": 768,
        "everyFeasibleRootHasAtLeastFiveDistinctLegalChildren": True,
        "everyFeasibleRootSourcePvOccursExactlyOnce": True,
        "rawLeakageGraphComputedBeforeFiltering": True,
        "filteredRootsAndChildrenReproduceAsExactRawSubsets": True,
        "forbiddenCatalogRecomputedAtFeasibilityAndPrelabel": True,
        "crossBucketBorrowing": False,
        "legalExpansionCompleteAgainstPinnedManifest": True,
        "childIdCollisions": 0,
        "targetOrScoreFieldsDecoded": 0,
        "targetOrScoreFieldsEmitted": 0,
        "priorSourceGameIdCollisions": 0,
        "priorSourceRunIdCollisions": 0,
        "priorSourceDataInputSha256Collisions": 0,
        "sourceDataInputDomainSymmetric": True,
        "reusableInfrastructureAuthenticationComplete": True,
        "launchProvenanceAuthenticationComplete": True,
        "priorExactPositionCollisions": 0,
        "priorOfflineOrMatchRootOrbitCollisions": 0,
        "crossSplitComponentCollisions": 0,
        "crossSplitSourceGameCollisions": 0,
        "crossSplitExactPositionCollisions": 0,
        "crossSplitConservativeOrbitCollisions": 0,
        "everyPhaseNonemptyInEverySplit": True,
    }
    _expect(dict(checks), expected, "pre-label abort checks")
    target_opaque = _mapping(
        gates.get("requiredTargetOpaqueInputs"),
        "pre-label target-opaque inputs",
    )
    _expect(
        dict(target_opaque),
        {
            "priorForbiddenPositionCatalogManifests": (
                "one or more explicit catalog manifests; catalogs may contain "
                "only recognized source identifiers and exact/conservative "
                "position signatures and may not contain target, score, result, "
                "or outcome fields"
            ),
            "priorSourceAuditClosure": (
                "the exact verified Generation-4 target-opaque structural-abort "
                "closure authenticates the complete prior source-data and "
                "reusable-infrastructure identity domains"
            ),
            "componentSplitMap": (
                "computed from the complete target-opaque root and legal-child "
                "inventory before any search"
            ),
            "finalFreezeSeal": (
                "pins the final preregistration, validator, implementations, "
                "source telemetry, roots, children, teacher, and trainer"
            ),
        },
        "pre-label target-opaque inputs",
    )
    enforcement = _mapping(gates.get("enforcement"), "pre-label enforcement")
    _expect(
        dict(enforcement),
        {
            "preLabelFreezeSealMustExistBeforeRunShallow": True,
            "preLabelFreezeSealIdentityIncludedInEveryShallowAndDeepLock": True,
            "finalPreregistrationAndFreezeSealReverifiedBeforeEveryTeacherProcessStarts": True,
        },
        "pre-label enforcement",
    )
    failure = _mapping(gates.get("failureAction"), "pre-label failure action")
    for key, expected_value in {
        "abortBeforeTeacherSearch": True,
        "mayChangeAnySeedInsideThisProfile": False,
        "mayChangeQuotaInsideThisProfile": False,
        "mayInspectTargetsToRepairFailure": False,
    }.items():
        _expect(failure.get(key), expected_value, f"pre-label failure {key}")


def _validate_final_identities(
    value: Any,
    *,
    mode: str,
    verify_external: bool,
) -> None:
    identities = _mapping(value, "final freeze identities")
    _expect(set(identities), EXPECTED_FINAL_IDENTITY_KEYS | {"bindingRule"},
            "final identity inventory")
    _expect(
        identities.get("bindingRule"),
        "the adjacent final-freeze seal pins this completed preregistration "
        "itself; every identity below is embedded in the completed "
        "preregistration and reverified by prelabel-freeze before any teacher "
        "search",
        "final identity binding rule",
    )
    seen_paths: set[str] = set()
    for name, expected_path in EXPECTED_FINAL_IDENTITY_PATHS.items():
        record = _mapping(identities.get(name), f"final identity {name}")
        _expect(set(record), {"path", "bytes", "sha256"},
                f"final identity {name} fields")
        _expect(record.get("path"), expected_path, f"final identity {name} path")
        _repo_path(expected_path, f"final identity {name}")
        if expected_path in seen_paths:
            raise ValueError("final implementation identity paths are not unique")
        seen_paths.add(expected_path)
        if mode == "template":
            _expect(record.get("bytes"), "<PIN_BEFORE_FINAL_FREEZE>",
                    f"final identity {name} bytes placeholder")
            _expect(record.get("sha256"), "<PIN_BEFORE_FINAL_FREEZE>",
                    f"final identity {name} hash placeholder")
        else:
            _validate_identity(
                record,
                f"final identity {name}",
                verify_file=verify_external,
            )
    catalogs = list(
        _sequence(
            identities.get("forbiddenPositionCatalogManifests"),
            "forbidden catalog manifests",
        )
    )
    if mode == "template":
        _expect(
            catalogs,
            ["<ONE_OR_MORE_TARGET_OPAQUE_MANIFEST_IDENTITIES_AT_FINAL_FREEZE>"],
            "forbidden catalog template placeholder",
        )
    else:
        if not catalogs:
            raise ValueError("at least one frozen forbidden catalog is required")
        for index, record in enumerate(catalogs):
            identity = _validate_identity(
                record,
                f"forbidden catalog manifest {index}",
                verify_file=verify_external,
            )
            if identity["path"] in seen_paths:
                raise ValueError("duplicate final identity path")
            seen_paths.add(identity["path"])
    if mode == "frozen" and verify_external:
        runtime_manifest = _repo_path(
            EXPECTED_FINAL_IDENTITY_PATHS["pythonRuntimeManifest"],
            "Python runtime manifest",
        )
        runtime_contract.verify_manifest(runtime_manifest)
        expected_tool = _repo_path(
            EXPECTED_FINAL_IDENTITY_PATHS["pythonRuntimeToolSource"],
            "Python runtime tool",
        )
        if Path(runtime_contract.__file__).resolve() != expected_tool:
            raise ValueError("imported Python runtime verifier is noncanonical")


def _validate_ranking(value: Any) -> None:
    ranking = _mapping(value, "ranking objective")
    for key, expected in {
        "decisionGroup": (
            "exactly four siblings from one root, kept together in every "
            "optimizer batch"
        ),
        "teacherScorePerspective": "root side to move",
        "studentChildScorePerspective": "side to move in the child OFEN",
        "studentChildTotalCp": (
            "pinned static HCE child-side score plus NNUE residual prediction"
        ),
        "studentRootPreferenceGap": (
            "studentChildTotalCp(other) minus studentChildTotalCp(best), equal "
            "to rootScore(best) minus rootScore(other)"
        ),
        "pairConstruction": (
            "teacher-best child versus each non-best sibling whose uncapped "
            "teacher root-side gap is at least 20 cp"
        ),
        "gapIgnoreBelowCp": 20,
        "teacherGapCapCp": 600,
        "loss": (
            "Huber regression of student root-preference gap against capped "
            "teacher root-side gap"
        ),
        "cpNormalizer": 100,
        "huberDeltaNormalized": 2,
        "normalization": (
            "mean eligible best-versus-child pairs inside each root, then mean "
            "eligible roots inside the complete-root batch, then multiply by "
            "the candidate siblingRankingLossWeight"
        ),
        "rootWithNoEligiblePair": (
            "contributes pointwise loss but zero ranking loss"
        ),
    }.items():
        _expect(ranking.get(key), expected, f"ranking {key}")
    static = _mapping(ranking.get("staticHcePolicy"), "static HCE policy")
    _expect(
        dict(static),
        {
            "labelsDoNotEmbedStaticHce": True,
            "computeLazilyWithPinnedCppEvaluatorMode": (
                "--evaluate-handcrafted-stream"
            ),
            "trainingSplitMayBeDecodedDuringTraining": True,
            "validationStaticHceMayBeComputedOnlyAfterAHealthPassingCheckpoint": True,
            "heldOutRowsMayNeverBeJsonDecodedDuringTrainingOrSelection": True,
        },
        "static HCE policy",
    )


def _validate_architectures(value: Any) -> None:
    deployment = _mapping(value, "deploymentCompatibility")
    common = _mapping(deployment.get("commonDimensions"), "common dimensions")
    _expect(
        dict(common),
        {
            "squareCount": 104,
            "pieceCount": 8,
            "accumulatorSize": 128,
            "denseInputSize": 256,
            "hiddenSize": 32,
            "activationMaximum": 127,
            "hiddenDivisor": 64,
            "outputDivisor": 64,
        },
        "runtime dimensions",
    )
    arch3 = _mapping(deployment.get("architecture3"), "architecture 3")
    for key, expected in {
        "architectureId": 3,
        "semantics": "king-state-residual",
        "featureRows": 48376,
        "fileBytes": 12392940,
    }.items():
        _expect(arch3.get(key), expected, f"architecture-3 {key}")
    arch4 = _mapping(deployment.get("architecture4"), "architecture 4")
    for key, expected in {
        "architectureId": 4,
        "semantics": "omega-interaction-residual",
        "featureRows": 48440,
        "architecture3Rows": 48376,
        "interactionRows": 64,
        "payloadBytes": 12409252,
        "fileBytes": 12409324,
        "rawResidualClampCpInclusive": [-600, 600],
        "cppIdentifier": "Architecture_Omega_Interaction_Residual",
        "pythonIdentifier": "ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL",
    }.items():
        _expect(arch4.get(key), expected, f"architecture-4 {key}")
    _expect(
        deployment.get("allExportsAreOrdinaryMaterializedRuntimeTables"),
        True,
        "materialized runtime export",
    )
    _expect(
        deployment.get("latentLowRankFactorsAreNeverDeployed"),
        True,
        "no deployed latent factors",
    )


def _validate_candidates(value: Any) -> None:
    matrix = _mapping(value, "candidateMatrix")
    _expect(matrix.get("maximumTrainedCandidates"), 3, "candidate count")
    _expect(
        matrix.get("executionOrder"),
        ["G5A", "G5B", "G5C"],
        "candidate order",
    )
    _expect(
        matrix.get("allCandidatesStartPredictionIdenticalToMappedK2"),
        True,
        "candidate epoch-zero parity",
    )
    _expect(matrix.get("generation3ReplayRows"), 0, "no G3 replay")
    common = _mapping(matrix.get("commonTraining"), "common training")
    for key, expected in {
        "epochs": 48,
        "qatEpochs": 12,
        "qatEpochsInclusive": [37, 48],
        "batchSize": 256,
        "optimizer": "Adam",
        "learningRate": 0.003,
        "pointwiseTarget": (
            "clamp(deepScoreCpChildStm - pinnedStaticHceCpChildStm, "
            "-2000, 2000)"
        ),
        "rankingObjective": "exactly top-level rankingObjective",
        "trainingMayDecodeHeldOutTargets": False,
    }.items():
        _expect(common.get(key), expected, f"common training {key}")
    _expect(
        common.get("activationSettings"),
        "exactly activationControlDevelopmentGrid.activationSettings",
        "activation setting source",
    )
    candidates = list(_sequence(matrix.get("candidates"), "candidates"))
    _expect(len(candidates), 3, "candidate inventory size")
    expected = [
        ("G5A", 4, 4, 0.25),
        ("G5B", 4, 4, 0.5),
        ("G5C", 4, 8, 0.5),
    ]
    actual: list[tuple[Any, Any, Any, Any]] = []
    for candidate in candidates:
        record = _mapping(candidate, "candidate")
        actual.append(
            (
                record.get("id"),
                record.get("architectureId"),
                record.get("rank"),
                record.get("siblingRankingLossWeight"),
            )
        )
    _expect(actual, expected, "candidate architecture/rank matrix")
    g4b = _mapping(candidates[1], "G5B")
    update = _mapping(g4b.get("conditionedUpdate"), "G5B update")
    _expect(
        update.get("formula"),
        "W[k,f,:] = K2-mapped[k,f,:] + sum_r A[k,r] * D[r,f,:]",
        "low-rank fixed-remainder formula",
    )
    _expect(update.get("DInitialValue"), 0, "zero low-rank update")
    _expect(
        update.get("epochZeroMaterializationByteIdenticalToMappedK2Rows"),
        True,
        "low-rank epoch-zero parity",
    )


def _validate_health_and_selection(profile: Mapping[str, Any]) -> None:
    health = dict(
        _mapping(profile.get("deploymentHealthGate"), "deployment health")
    )
    _expect(health, EXPECTED_HEALTH, "unchanged deployment health thresholds")
    health_first = _mapping(
        profile.get("healthFirstCheckpointSelection"),
        "health-first checkpoint selection",
    )
    for key, expected in {
        "candidateCheckpointEligibleEpochs": "QAT epochs 37 through 48 only",
        "runEveryEpochThrough48": True,
        "candidateWithNoHealthPassingCheckpoint": "ineligible",
        "lowerValidationLossMayOverrideFailedHealth": False,
        "healthMayReadTargetFields": False,
        "healthThresholds": "exactly deploymentHealthGate",
    }.items():
        _expect(health_first.get(key), expected, f"health-first {key}")
    validation = _mapping(
        profile.get("validationSelection"), "validation selection"
    )
    _expect(
        dict(_mapping(validation.get("eligibility"), "eligibility")),
        EXPECTED_ELIGIBILITY,
        "validation eligibility gates",
    )
    _expect(
        validation.get("validationMaySelectActivationPenaltyStrength"),
        False,
        "validation cannot select activation strength",
    )


def _validate_grid(
    value: Any,
    *,
    mode: str,
    verify_external: bool,
) -> None:
    grid = _mapping(value, "activationControlDevelopmentGrid")
    _expect(
        grid.get("namespace"),
        "build-msvc/king-state-v4-development",
        "development grid namespace",
    )
    _expect(
        grid.get("selectionRuleMustBeFrozenInsideGrid"),
        True,
        "development-grid selection rule freeze",
    )
    _expect(
        grid.get("mayUseGeneration5Targets"),
        False,
        "development grid generation-5 target access",
    )
    _expect(
        grid.get("mustBeSealedBeforeGeneration5PrimaryTraining"),
        True,
        "grid seal timing",
    )
    settings = dict(
        _mapping(grid.get("activationSettings"), "activation settings")
    )
    expected_settings = {
        "penaltyWeight": 32.0,
        "targetActiveFraction": 0.62,
        "temperature": 16.0,
        "source": "exact values supplied by the frozen development grid",
    }
    _expect(settings, expected_settings, "frozen activation settings")
    if mode == "template":
        _expect(
            grid.get("status"),
            "calibration-values-frozen-grid-identity-unresolved-in-template",
            "grid status",
        )
        return

    _expect(grid.get("status"), "sealed-before-primary-training", "grid status")
    seal = _validate_identity(
        grid.get("seal"),
        "development-grid seal",
        verify_file=verify_external,
    )
    if verify_external:
        seal_path = _repo_path(seal["path"], "development-grid seal")
        seal_value = _load_json(seal_path)
        _expect(
            seal_value.get("kind"),
            "omega-nnue-king-state-v4-activation-grid-selection",
            "development-grid seal kind",
        )
        boundary = _mapping(
            seal_value.get("informationBoundary"), "grid information boundary"
        )
        _expect(
            boundary.get("generation3HeldOutTargetFieldsDecoded"),
            0,
            "grid generation-3 held-out reads",
        )
        _expect(
            boundary.get("generation4TargetFieldsDecoded"),
            0,
            "grid generation-4 target reads",
        )
        _expect(
            seal_value.get("selectedActivationSettings"),
            settings,
            "activation settings copied from grid seal",
        )


def validate_profile(
    profile: Mapping[str, Any],
    *,
    mode: str,
    verify_external: bool = True,
) -> None:
    _verify_runtime_contract_binding()
    if mode not in {"template", "frozen"}:
        raise ValueError(f"unknown validation mode {mode!r}")
    _validate_whole_profile_scalar_types(profile, mode=mode)
    _expect(set(profile), EXPECTED_TOP_LEVEL, "top-level field inventory")
    _expect(profile.get("schemaVersion"), SCHEMA_VERSION, "schema version")
    _expect(profile.get("profileId"), PROFILE_ID, "profile id")
    expected_kind = TEMPLATE_KIND if mode == "template" else FROZEN_KIND
    expected_status = TEMPLATE_STATUS if mode == "template" else FROZEN_STATUS
    _expect(profile.get("kind"), expected_kind, "profile kind")
    _expect(profile.get("status"), expected_status, "profile status")
    created = profile.get("createdUtc")
    if not isinstance(created, str):
        raise ValueError("createdUtc must be a string")
    try:
        datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("createdUtc is invalid") from error

    placeholders = _walk_placeholders(profile)
    if mode == "template":
        _expect(
            placeholders,
            EXPECTED_TEMPLATE_PLACEHOLDERS,
            "template placeholder inventory",
        )
        closure = _mapping(profile.get("generation4Closure"), "G4 closure")
        _expect(
            closure.get("path"),
            GENERATION4_CLOSURE_PATH,
            "G4 closure path",
        )
    else:
        if placeholders:
            raise ValueError(f"frozen profile still has placeholders: {placeholders}")
        closure_identity = _validate_identity(
            profile.get("generation4Closure"),
            "generation-4 structural-abort closure",
            verify_file=verify_external,
        )
        _expect(
            closure_identity.get("path"),
            GENERATION4_CLOSURE_PATH,
            "G4 closure path",
        )
        if verify_external:
            closure_path = _repo_path(
                closure_identity["path"],
                "G4 closure",
            )
            closure_value = _load_json(closure_path)
            _expect(
                closure_value.get("kind"),
                "omega-nnue-king-state-v4-target-opaque-structural-abort",
                "G4 closure kind",
            )
            _expect(
                closure_value.get("status"),
                "closed-before-prelabel-or-teacher-search",
                "G4 closure status",
            )
            _expect(
                closure_value.get("finalStageSeal"),
                True,
                "G4 closure commit point",
            )

    boundary = _mapping(profile.get("informationBoundary"), "boundary")
    _expect(
        dict(boundary),
        {
            "generation4TeacherTargetsAvailableToThisDesign": False,
            "generation4ValidationTargetsAvailableToThisDesign": False,
            "generation4HeldOutTargetsAvailableToThisDesign": False,
            "generation4MatchResultsAvailableToThisDesign": False,
            "allowedGeneration4Evidence": [
                "target-opaque structural-abort closure",
                "frozen activation-control development-grid selection",
                "target-opaque predecessor projection verifier",
                "unchanged preregistered candidate recipes",
            ],
            "generation5TeacherTargetsAvailableAtFinalFreeze": False,
            "generation5ValidationTargetsAvailableAtFinalFreeze": False,
            "generation5HeldOutTargetsAvailableAtFinalFreeze": False,
            "heldOutTargetsMaySelectRecipe": False,
            "matchResultsMaySelectRecipe": False,
        },
        "generation-5 information boundary",
    )
    immutability = _mapping(profile.get("immutability"), "immutability")
    _expect(
        dict(immutability),
        {
            "thisTemplateIsAnExecutablePreregistration": False,
            "finalFreezeRequiredBeforeAnyGeneration5TeacherLabel": True,
            "finalFreezeMustPinEveryPlaceholderAndImplementationIdentity": True,
            "dataNamespacesSeedsQuotasAndAbortGatesMayChangeAfterTemplate": False,
            "candidateArchitectureAndRankMatrixMayChangeAfterTemplate": False,
            "deploymentHealthThresholdsMayChangeAfterTemplate": False,
            "activationPenaltyStrengthsMustComeOnlyFromFrozenDevelopmentGrid": True,
            "activationPenaltyStrengthsMayBeChosenAfterGeneration5TargetsExist": False,
            "failedGenerationRequiresSeparateFreshProfileNamespacesAndSeeds": True,
            "runnerUpFallbackAfterHeldOutOrMatchAccess": False,
        },
        "generation-5 immutability contract",
    )

    _validate_namespaces(profile.get("namespaces"))
    _validate_seeds(profile.get("seeds"))
    _validate_corpus(profile.get("freshDecisionCorpus"))
    _validate_prelabel_gates(profile.get("preLabelAbortGates"))
    _validate_final_identities(
        profile.get("finalFreezeIdentities"),
        mode=mode,
        verify_external=verify_external,
    )
    _validate_architectures(profile.get("deploymentCompatibility"))
    _validate_ranking(profile.get("rankingObjective"))
    _validate_candidates(profile.get("candidateMatrix"))
    _validate_health_and_selection(profile)
    _validate_grid(
        profile.get("activationControlDevelopmentGrid"),
        mode=mode,
        verify_external=verify_external,
    )

    initializer = _mapping(profile.get("initializer"), "initializer")
    _expect(
        initializer.get("generation3CandidateWeightsMayInitializeGeneration5"),
        False,
        "failed G3 candidates cannot initialize G5",
    )
    migration = _mapping(
        initializer.get("architecture4Migration"), "architecture-4 migration"
    )
    _expect(
        migration.get("epochZeroPredictionsMustMatchK2ExactlyOnWholeFeatureCorpus"),
        True,
        "architecture-4 initializer parity",
    )
    _expect(initializer.get("generation3CorpusReplayRows"), 0, "no G3 replay")
    _expect_structure(
        profile.get("postSelection"),
        EXPECTED_POST_SELECTION,
        "postSelection",
    )
    if mode == "frozen" and verify_external:
        _fully_authenticate_generation4_closure(profile)


def _self_test(profile: Mapping[str, Any]) -> None:
    validate_profile(profile, mode="template", verify_external=False)

    def must_fail(mutator: Any, label: str, mode: str = "template") -> None:
        changed = deepcopy(profile)
        mutator(changed)
        try:
            validate_profile(changed, mode=mode, verify_external=False)
        except ValueError:
            return
        raise AssertionError(f"self-test mutation did not fail: {label}")

    def set_nested(root: Any, path: tuple[Any, ...], replacement: Any) -> None:
        cursor = root
        for part in path[:-1]:
            cursor = cursor[part]
        cursor[path[-1]] = replacement

    def leaves(value: Any, path: tuple[Any, ...] = ()) -> Any:
        if isinstance(value, Mapping):
            for key, item in value.items():
                yield from leaves(item, (*path, key))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                yield from leaves(item, (*path, index))
            return
        yield path, value

    def changed_leaf(value: Any) -> Any:
        if type(value) is bool:
            return not value
        if type(value) is int:
            return value + 1
        if type(value) is float:
            return value + 0.125
        if type(value) is str:
            return value + "-mutation"
        raise AssertionError(f"unhandled preregistration leaf type: {type(value)}")

    def wrong_type_variants(value: Any) -> tuple[Any, ...]:
        if type(value) is bool:
            return (int(value),)
        if type(value) is int:
            return (float(value), bool(value))
        if type(value) is float:
            return (repr(value), int(value))
        if type(value) is str:
            return (0,)
        raise AssertionError(
            f"unhandled preregistration leaf type: {type(value)}"
        )

    template_leaves = list(leaves(profile))
    if len(template_leaves) != EXPECTED_TEMPLATE_PROFILE_TYPE_INVENTORY[
        "scalarLeaves"
    ]:
        raise AssertionError("canonical template scalar-leaf count changed")
    numeric_or_boolean = sum(
        type(item) in {bool, int, float} for _, item in template_leaves
    )
    if numeric_or_boolean != EXPECTED_TEMPLATE_PROFILE_TYPE_INVENTORY[
        "numericOrBooleanLeaves"
    ]:
        raise AssertionError("canonical numeric/bool leaf count changed")
    for leaf_path, leaf_value in template_leaves:
        path_text = "/" + "/".join(str(part) for part in leaf_path)
        for replacement in wrong_type_variants(leaf_value):
            must_fail(
                lambda value, path=leaf_path, changed=replacement: set_nested(
                    value, path, changed
                ),
                f"wrong JSON type at canonical template leaf {path_text}: "
                f"{type(leaf_value).__name__}->{type(replacement).__name__}",
            )

    synthetic_frozen = deepcopy(profile)
    synthetic_frozen["kind"] = FROZEN_KIND
    synthetic_frozen["status"] = FROZEN_STATUS
    synthetic_frozen["createdUtc"] = "2026-07-22T00:00:00Z"
    synthetic_frozen["generation4Closure"]["bytes"] = 1
    synthetic_frozen["generation4Closure"]["sha256"] = "0" * 64
    grid = synthetic_frozen["activationControlDevelopmentGrid"]
    grid["status"] = "sealed-before-primary-training"
    grid["seal"]["bytes"] = 1
    grid["seal"]["sha256"] = "0" * 64
    identities = synthetic_frozen["finalFreezeIdentities"]
    for identity_name in EXPECTED_FINAL_IDENTITY_PATHS:
        identities[identity_name]["bytes"] = 1
        identities[identity_name]["sha256"] = "0" * 64
    identities["forbiddenPositionCatalogManifests"] = [
        {
            "path": "validation/synthetic-forbidden-a.json",
            "bytes": 1,
            "sha256": "0" * 64,
        },
        {
            "path": "validation/synthetic-forbidden-b.json",
            "bytes": 2,
            "sha256": "1" * 64,
        },
    ]
    _validate_whole_profile_scalar_types(synthetic_frozen, mode="frozen")
    frozen_leaves = list(leaves(synthetic_frozen))
    expected_frozen_leaves = (
        EXPECTED_TEMPLATE_PROFILE_TYPE_INVENTORY["scalarLeaves"] + 5
    )
    if len(frozen_leaves) != expected_frozen_leaves:
        raise AssertionError("synthetic frozen scalar-leaf count changed")
    for leaf_path, leaf_value in frozen_leaves:
        path_text = "/" + "/".join(str(part) for part in leaf_path)
        for replacement in wrong_type_variants(leaf_value):
            changed = deepcopy(synthetic_frozen)
            set_nested(changed, leaf_path, replacement)
            try:
                _validate_whole_profile_scalar_types(changed, mode="frozen")
            except ValueError:
                continue
            raise AssertionError(
                f"wrong frozen JSON type was accepted at {path_text}: "
                f"{type(leaf_value).__name__}->{type(replacement).__name__}"
            )

    must_fail(
        lambda value: value["deploymentHealthGate"].__setitem__(
            "denseActiveFractionRangeInclusive", [0.2, 0.95]
        ),
        "weakened activation-health gate",
    )
    must_fail(
        lambda value: value["seeds"].__setitem__("training", 2026072305),
        "reused seed",
    )
    must_fail(
        lambda value: value["seeds"].__setitem__("match", 2026072308.0),
        "integer match seed changed to float",
    )
    must_fail(
        lambda value: value["seeds"].__setitem__("match", True),
        "integer match seed changed to bool",
    )
    must_fail(
        lambda value: value.__setitem__("schemaVersion", 1.0),
        "integer profile schema changed to float",
    )
    must_fail(
        lambda value: value["candidateMatrix"]["candidates"][1].__setitem__(
            "rank", 6
        ),
        "changed candidate rank",
    )
    must_fail(
        lambda value: value["freshDecisionCorpus"]["deepTeacher"].__setitem__(
            "fixedNodesPerSelectedChild", 10000
        ),
        "changed deep teacher budget",
    )
    # These are the concrete fail-open examples found by the independent
    # source/corpus audit.  Keep them explicit even though the exhaustive leaf
    # loop below covers them too, so a regression reports the safety contract
    # that was weakened rather than only a generic path.
    must_fail(
        lambda value: value["freshDecisionCorpus"]["teacherEngine"].__setitem__(
            "sameFrozenHceExecutableForEverySearch", False
        ),
        "teacher executable may vary between searches",
    )
    must_fail(
        lambda value: value["freshDecisionCorpus"][
            "legalChildExpansion"
        ].__setitem__("legalityMustMatchPinnedChessLib", False),
        "legal expansion may differ from pinned ChessLib",
    )
    must_fail(
        lambda value: value["freshDecisionCorpus"]["shallowScreen"].__setitem__(
            "rejectMateScores", False
        ),
        "shallow screen accepts mate scores",
    )
    must_fail(
        lambda value: value["freshDecisionCorpus"]["deepTeacher"].__setitem__(
            "rejectShortNodeSearches", False
        ),
        "deep teacher accepts short searches",
    )
    must_fail(
        lambda value: value["freshDecisionCorpus"][
            "leakageComponent"
        ].__setitem__("mustUnify", []),
        "leakage component unification removed",
    )
    must_fail(
        lambda value: value["rankingObjective"].__setitem__(
            "gapIgnoreBelowCp", 0
        ),
        "changed ranking noise floor",
    )
    must_fail(
        lambda value: value["freshDecisionCorpus"]["split"].__setitem__(
            "namesInFrozenOrder", ["train", "validation", "test"]
        ),
        "legacy test split name",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["trainerSource"].__setitem__(
            "path", "tools/omega_nnue/train.py"
        ),
        "changed frozen trainer path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["deepHceV2Source"].__setitem__(
            "path", "tools/omega_nnue/omega_decision_teacher.py"
        ),
        "changed frozen deep-HCE dependency path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["selectScreenSource"].__setitem__(
            "path", "tools/omega_nnue/deep_hce_v2.py"
        ),
        "changed frozen selector dependency path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["baseTrainerSource"].__setitem__(
            "path", "tools/omega_nnue/king_state_train_generation5.py"
        ),
        "changed frozen base-trainer dependency path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["pythonRuntimeManifest"].__setitem__(
            "path", "validation/omega-nnue-king-state-v5-preregistration.json"
        ),
        "changed frozen Python/NumPy runtime manifest path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["pythonRuntimeToolSource"].__setitem__(
            "path", "tools/omega_nnue/king_state_train_generation5.py"
        ),
        "changed frozen runtime-verifier source path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["priorProjectionSource"].__setitem__(
            "path", "tools/omega_nnue/omega_decision_teacher.py"
        ),
        "changed frozen prior-projection source path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["matchCoreSource"].__setitem__(
            "path", "tools/omega_nnue/king_state_matches_generation5.py"
        ),
        "changed frozen shared match-core source path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"][
            "dotnetRuntimeHostExecutable"
        ].__setitem__("path", "tools/omega_nnue/frozen_runtime/king-state-v5/engine/senpai.exe"),
        "changed frozen .NET host path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["dotnetRuntimeManifest"].__setitem__(
            "path", "validation/omega-nnue-king-state-v5-python-runtime.json"
        ),
        "changed frozen .NET runtime manifest path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"][
            "dotnetRuntimeToolSource"
        ].__setitem__("path", "tools/omega_nnue/king_state_generation5_runtime.py"),
        "changed frozen .NET runtime tool path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["matchProtocol"].__setitem__(
            "path", "validation/omega-nnue-king-state-v5-preregistration.template.json"
        ),
        "changed frozen match protocol path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["matchProtocolTool"].__setitem__(
            "path", "tools/omega_nnue/king_state_matches_generation5.py"
        ),
        "changed frozen match-protocol tool path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["matchReadinessTool"].__setitem__(
            "path", "tools/omega_nnue/king_state_match_protocol_generation5.py"
        ),
        "changed frozen match-readiness tool path",
    )
    must_fail(
        lambda value: value["finalFreezeIdentities"]["matchOrchestrator"].__setitem__(
            "path", "tools/omega_nnue/king_state_match_readiness_generation5.py"
        ),
        "changed frozen match orchestrator path",
    )
    must_fail(
        lambda value: value["freshDecisionCorpus"].__setitem__(
            "unregisteredPolicy", True
        ),
        "added unregistered corpus field",
    )
    must_fail(
        lambda value: value["freshDecisionCorpus"]["artifactPolicy"].pop(
            "finalStageSealIsThePublicationCommitPoint"
        ),
        "removed corpus field",
    )

    # Mutate every scalar in the registered corpus contract.  This protects
    # new source-pool/probe fields and less prominent acceptance, publication,
    # component, and split settings from silently becoming unvalidated.
    for leaf_path, leaf_value in leaves(EXPECTED_FRESH_DECISION_CORPUS):
        path_text = "freshDecisionCorpus" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in leaf_path
        )
        must_fail(
            lambda value, path=leaf_path, replacement=changed_leaf(
                leaf_value
            ): set_nested(value["freshDecisionCorpus"], path, replacement),
            f"changed registered corpus leaf {path_text}",
        )
    for leaf_path, leaf_value in leaves(EXPECTED_POST_SELECTION):
        path_text = "postSelection" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in leaf_path
        )
        must_fail(
            lambda value, path=leaf_path, replacement=changed_leaf(
                leaf_value
            ): set_nested(value["postSelection"], path, replacement),
            f"changed registered post-selection leaf {path_text}",
        )
    must_fail(
        lambda value: value["postSelection"]["oneTimeHeldOut"].__setitem__(
            "unregisteredBootstrapPolicy", True
        ),
        "added unregistered held-out policy",
    )
    must_fail(
        lambda value: value["postSelection"]["robustness"]["outputs"].pop(
            "claim"
        ),
        "removed robustness claim namespace",
    )
    must_fail(
        lambda value: value["seeds"].__setitem__(
            "robustnessTrainingSeedPurpose", "candidate-training"
        ),
        "reused primary training seed domain",
    )
    must_fail(lambda value: None, "unresolved frozen template", mode="frozen")
    original_runtime = runtime_contract
    try:
        globals()["runtime_contract"] = object()
        try:
            validate_profile(profile, mode="template", verify_external=False)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "in-memory Python runtime verifier substitution was accepted"
            )
    finally:
        globals()["runtime_contract"] = original_runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("profile", type=Path, nargs="?", default=DEFAULT_PROFILE)
    validate.add_argument("--mode", choices=("template", "frozen"), default="template")
    validate.add_argument(
        "--skip-external-identities",
        action="store_true",
        help="validate structure only; intended for preregistration drafting",
    )
    self_test = subparsers.add_parser("self-test")
    self_test.add_argument("profile", type=Path, nargs="?", default=DEFAULT_PROFILE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    profile_path = args.profile.expanduser().resolve()
    profile = _load_json(profile_path)
    if args.command == "validate":
        validate_profile(
            profile,
            mode=args.mode,
            verify_external=not args.skip_external_identities,
        )
        print(
            f"Validated generation-5 {args.mode} profile: {profile_path} "
            f"({profile_path.stat().st_size} bytes, {_sha256(profile_path)})"
        )
        return 0
    if args.command == "self-test":
        _self_test(profile)
        print("Generation-5 preregistration validator self-tests passed.")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
