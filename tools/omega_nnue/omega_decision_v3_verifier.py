#!/usr/bin/env python3
"""Fresh semantic verifier for the frozen Omega decision-v3 capsule.

The verifier is intentionally a separate executable from both the data
producers and the Generation-6 trainer.  It authenticates every bound file,
replays the target-free and target-bearing closure, and emits exactly one
small result object.  Target rows and game results are never written to
stdout.

Large JSONL authorities are streamed through descriptor-stable readers.  The
only cross-row state is stored in a private temporary SQLite database, so the
working set remains bounded for the production-sized corpus.
"""

from __future__ import annotations

import builtins
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import types
from typing import Any, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v6-move-decision-v1"
CAPSULE_KIND = "omega-decision-v3-capsule-closure"
VERIFIER_OPTIONS_KIND = "omega-decision-v3-verifier-options"
VERIFICATION_KIND = "omega-decision-v3-fresh-verification"
ROUTING_KIND = "omega-decision-v3-target-free-routing"
COMPONENT_KIND = "omega-nnue-king-state-v6-component-authority"
PRELABEL_KIND = "omega-nnue-king-state-v6-upstream-prelabel-seal"
INITIALIZER_KIND = "omega-nnue-king-state-v6-initializer-manifest"
INITIALIZER_SELECTION_KIND = "omega-decision-v3-pre-g6-initializer-selection"
FORBIDDEN_REGISTRY_KIND = "omega-decision-v3-prior-forbidden-registry"
HCE_CLAIM_KIND = "omega-decision-v3-pretarget-hce-claim"
HCE_COMPLETION_KIND = "omega-decision-v3-pretarget-hce-completion"
HCE_ROW_KIND = "omega-nnue-king-state-v6-static-hce"
HCE_OPTIONS_KIND = "omega-nnue-king-state-v6-static-hce-options"
HCE_MANIFEST_KIND = "omega-nnue-king-state-v6-static-hce-manifest"
TEACHER_CLAIM_KIND = "omega-decision-v3-teacher-claim"
TEACHER_COMPLETION_KIND = "omega-decision-v3-teacher-completion"
TEACHER_ATTEMPT_KIND = "omega-decision-v3-teacher-attempt"
LEDGER_COMPLETION_KIND = "omega-decision-v3-teacher-attempt-ledger-completion"
TEACHER_LABEL_KIND = "omega-nnue-king-state-v6-upstream-teacher-label"
TEACHER_MANIFEST_KIND = "omega-nnue-king-state-v6-upstream-teacher-completion"
PROJECTED_LABEL_KIND = "omega-nnue-king-state-v6-decision-label"
PROJECTED_MANIFEST_KIND = "omega-nnue-king-state-v6-label-manifest"

PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
SPLITS = ("train", "validation", "heldOut")
CELLS = tuple(f"{phase}:{side}" for phase in PHASES for side in SIDES)
ROUTING_QUOTAS_PER_PHASE_SIDE: Mapping[str, int] = {
    "train": 512,
    "validation": 128,
    "heldOut": 128,
}
CHILDREN_PER_ROOT = 4
INITIALIZER_CATALOG = ("G5", "G2-K2")
PRIOR_SOURCE_IDS = ("G3", "G4", "G5")
STAGES = ("shallow", "deep")
STAGE_NODES = {"shallow": 2_000, "deep": 50_000}
TEACHER_BUDGETS = {
    "shallowNodes": 2_000,
    "deepNodes": 50_000,
    "timeoutSeconds": 180,
    "maximumAttempts": 3,
    "workers": 4,
    "childrenPerRoot": 4,
}
STATIC_HCE_PERSPECTIVE = (
    "integer centipawns from child side-to-move; child side is opposite parent side"
)
SCORE_LIMIT_CP = 1_000_000
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
MAX_JSON_DOCUMENT_BYTES = 64 * 1024 * 1024
# One teacher row can retain up to 8 MiB of raw transcript; JSON escaping can
# expand control-heavy text by roughly six times.  The 64 MiB lexical cap is
# therefore finite while admitting every producer-valid bounded transcript.
MAX_JSONL_LINE_BYTES = 64 * 1024 * 1024
VERIFIER_RUNNER_OVERRIDE: Path | None = None

G2_UNAVAILABLE_PROTOCOL = {
    "sourceId": "G2-K2",
    "catalogStatus": "unavailable",
    "model": None,
    "reason": "untouched frozen-source replays reject current dependency identities",
    "compatibilityPatchesApplied": False,
    "freshReplayRequired": True,
    "artifacts": {
        "selectionSeal": {
            "relativePath": "build-msvc/data-generation/deep-hce-v4/generation3-initializer-resolution.seal.json",
            "bytes": 5_286,
            "sha256": "ec3c53c6bd2093e1f48d06852440436226ee492beef5b7171829ce774097517c",
        },
        "closure": {
            "relativePath": "build-msvc/data-generation/deep-hce-v4/king-state-v1-prelabel.seal.json",
            "bytes": 42_342,
            "sha256": "14bf17732ac78bd96c4e093e6e983e3c4ca704c7eaca1075f0634bc90d037735",
        },
        "deepFreeze": {
            "relativePath": "build-msvc/data-generation/deep-hce-v4/deep-hce-v2.freeze.json",
            "bytes": 38_940_062,
            "sha256": "a1f01135c8ee45950cd92a586ad74d02ea2d46ad9a6f9edd5744d266ea0ba53f",
        },
        "generation2Incident": {
            "relativePath": "validation/omega-nnue-king-state-v2-offline-incident.json",
            "bytes": 22_747,
            "sha256": "df4b440ec625e3beb33735f3e89908c1414208f11d42a60944f8089075821678",
        },
    },
    "currentSources": {
        "g2Verifier": {
            "relativePath": "tools/omega_nnue/king_state_v3.py",
            "bytes": 362_242,
            "sha256": "ffe8625903269567eee083b96dbf38b18f1be6c1a0d94b8fabcb98bdba12e2db",
        },
        "g2Trainer": {
            "relativePath": "tools/omega_nnue/king_state_train_generation3.py",
            "bytes": 214_271,
            "sha256": "cdae1319d2880207b2ff4a632df90fc9dc26662ec4eb785694afddaa8b533de1",
        },
        "g2Deep": {
            "relativePath": "tools/omega_nnue/deep_hce_v2.py",
            "bytes": 148_725,
            "sha256": "9e9911b0a56191e5209d6276c004cf36a94fe1530eb915baf1ad5a72d32f7702",
        },
        "g2Incidence": {
            "relativePath": "tools/omega_nnue/phase_incidence_preflight.py",
            "bytes": 31_219,
            "sha256": "d58f1cb9150c79910c460dea7e95aefd2153e9ce3c8866cd2da3f5a953d591b9",
        },
        "g2BaseTrainer": {
            "relativePath": "tools/omega_nnue/train.py",
            "bytes": 124_508,
            "sha256": "ac64a0941c176c0d0b2a9024e31955739635d4e958d5f9fda06394d239e3f1a3",
        },
        "omegaNnue": {
            "relativePath": "tools/omega_nnue/omega_nnue.py",
            "bytes": 53_900,
            "sha256": "efc55715895f32e948db35428372393c711f701f2e84c69256bdf689065422aa",
        },
        "selectScreen": {
            "relativePath": "tools/omega_nnue/select_screen.py",
            "bytes": 39_442,
            "sha256": "304172e583b4c963718191017b4f8d2426aea325dd42337c197496ee738677ee",
        },
        "rootSamplerSource": {
            "relativePath": "tools/omega_nnue/OmegaRootSampler/Program.cs",
            "bytes": 17_898,
            "sha256": "3e6fc7efc4a54ad4ca8b000dba2d40bae021e3d1a91c768d061f533f5462c012",
        },
    },
    "frozenDependencies": {
        "rootSamplerSource": {
            "relativePath": "tools/omega_nnue/OmegaRootSampler/Program.cs",
            "bytes": 13_271,
            "sha256": "289d2452fdb63c2737c72b4b3634e8d1b2023126346f3fb1a662f752e64df3d2",
        },
        "omegaNnue": {
            "relativePath": "tools/omega_nnue/omega_nnue.py",
            "bytes": 44_266,
            "sha256": "bbab323356cb1f7194852af13c0ea632d7262f90d14d94032a1bcf88da2c0eb2",
        },
    },
    "requiredFreshFailures": {
        "fullSeal": "rootSampler source identity changed",
        "deepFreeze": "omega_nnue.py changed after freeze",
    },
    "resultInformationRead": False,
}

FALLBACK_PROTOCOL = {
    "architecture": "king-state-v6-move-decision-initializer-v1",
    "omegaNnueArchitectureId": 4,
    "omegaNnueArchitecture": "omega-interaction-residual",
    "generator": "exact-pinned omega_decision_v3_initializer.py",
    "seed": 2026072400,
    "prng": "numpy.random.default_rng-PCG64",
    "drawOrder": [
        "ftWeights",
        "denseWeights",
        "outputWeights",
    ],
    "floatInitialization": {
        "ftBias": {"fill": 24.0, "dtype": "float32"},
        "ftWeights": {
            "distribution": "normal",
            "mean": 0.0,
            "standardDeviation": 0.5,
            "shape": ["OMEGA_INTERACTION_FEATURE_COUNT", "ACCUMULATOR_SIZE"],
            "cast": "float32-after-draw",
        },
        "denseBias": {"fill": 24.0, "dtype": "float32"},
        "denseWeights": {
            "distribution": "normal",
            "mean": 0.0,
            "standardDeviation": 0.025,
            "shape": ["HIDDEN_SIZE", "2*ACCUMULATOR_SIZE"],
            "cast": "float32-after-draw",
        },
        "outputBias": {"fill": 0.0, "dtype": "float32"},
        "outputWeights": {
            "distribution": "normal",
            "mean": 0.0,
            "standardDeviation": 0.1,
            "shape": ["HIDDEN_SIZE"],
            "cast": "float32-after-draw",
        },
    },
    "quantization": {
        "rounding": "numpy.rint",
        "clipping": "destination integer range before cast",
        "ftScale": 1,
        "denseScale": 64,
        "outputScale": 64,
        "serialization": "OMNNUE1 exact-pinned omega_nnue.py architecture 4",
    },
    "selectionRule": (
        "use only after authenticated terminal G5 failure or abort and exact "
        "authenticated G2-K2 unavailability"
    ),
    "g6TargetRowsDecoded": 0,
    "gameResultsRead": False,
}

VERIFIER_OPTIONS = {
    "schemaVersion": 1,
    "kind": VERIFIER_OPTIONS_KIND,
    "profileId": PROFILE_ID,
    "mode": "--verify-omega-decision-v3-capsule",
    "stdout": "one exact JSON omega-decision-v3 fresh-verification object",
    "initializerPolicy": {
        "orderedCatalog": list(INITIALIZER_CATALOG),
        "selectionModes": ["promoted-prior", "deterministic-fallback"],
        "fallbackProtocol": dict(FALLBACK_PROTOCOL),
        "requireFirstIndependentlyPromotedEntry": True,
        "requireFreshHealthForPromotedEntry": True,
        "g5OnlyPromotableSource": True,
        "requireTerminalFailureOrAbortClosureForSkippedG5": True,
        "g2UnavailableProtocol": dict(G2_UNAVAILABLE_PROTOCOL),
    },
    "requiredPriorForbiddenSourceIds": list(PRIOR_SOURCE_IDS),
    "requiredSemanticReplays": [
        "G5-only initializer promotion, embedded health, source closure, exact G2 unavailability, and model cross-links",
        "terminal rules manifest/transcript/completion and pre-teacher exclusions",
        "every prior-forbidden manifest/catalog and zero current overlap",
        "component-map coverage and whole-component split assignment",
        "pre-target static-HCE claim, exact completion receipt, and fresh engine replay",
        "teacher claim/attempt ledger/completion coverage and budgets",
        "planned projection producer/path against realized corpus/manifest",
    ],
    "resultInformationRead": False,
}

CAPSULE_DECLARATION = {
    "componentAndSplitMapFrozenBeforeTeacher": True,
    "terminalClassifierLineageFrozenBeforeTeacher": True,
    "prelabelSealBindsTerminalClassifierAndPriorForbiddenRegistry": True,
    "prelabelSealBindsInitializerAuthority": True,
    "plannedProjectionProducerAndPathsFrozenBeforeTeacher": True,
    "priorForbiddenCatalogFrozenBeforeTeacher": True,
    "staticHceCompletedBeforeTeacherTargets": True,
    "teacherClaimBindsCompletedStaticHceReceipt": True,
    "heldOutTargetsDecodedByGeneration6AtClosure": 0,
    "gameResultsRead": False,
}

# Stable reviewed pins.  Terminal and routing are deliberately isolated in one
# table because their sibling reviews are still in flight.  Production refuses
# to run while either entry remains unset; no moving hash is represented as a
# final pin.
DEPENDENCY_PINS: dict[str, tuple[str, int | None, str | None]] = {
    "trainer": (
        "king_state_train_generation6.py",
        330_787,
        "81b9e0c5ffa5d78a4cf2198781ceffea7649bdaed3e827556a5e3deaeba8a2e0",
    ),
    "teacher": (
        "omega_decision_v3_teacher.py",
        88_168,
        "825ac1a5fdd2ee742769f022f36d500c505c8aaa90b95cd320feeb408ac6a4e8",
    ),
    "evaluatorRunner": (
        "omega_decision_v3_evaluator_runner.py",
        13_659,
        "091bbdabc28ae99a44ea8c351f00bbefc34ff917e6140547922bbabc3631925c",
    ),
    "omegaNnue": (
        "omega_nnue.py",
        53_900,
        "efc55715895f32e948db35428372393c711f701f2e84c69256bdf689065422aa",
    ),
    "selectScreen": (
        "select_screen.py",
        39_442,
        "304172e583b4c963718191017b4f8d2426aea325dd42337c197496ee738677ee",
    ),
    "terminal": (
        "omega_decision_v3_terminal_lineage.py",
        141_573,
        "c227d5011c55bd6f7f52e61b02e67a358ff4594ff3ec4244b54002a5b1277e38",
    ),
    "routing": (
        "omega_decision_v3_routing.py",
        133_260,
        "ed327826154256137694b955aaf23ede07c91374ea665be48df55360f858e25c",
    ),
    "initializerGenerator": (
        "omega_decision_v3_initializer.py",
        19_564,
        "38d81f665d0bccb4939e3a1707b7dbfbe4c9c29795e0699f51d92bf30af94be0",
    ),
    "g5Readiness": (
        "king_state_confirmation_readiness_v2.py",
        359_123,
        "b8b3716910665f626b61e2f247df95e12f382408a5b7d23daa66f18ab22bf81e",
    ),
    "g5Matches": (
        "king_state_matches_generation5_compat_v2.py",
        202_522,
        "3f99f67c09d9203e5358fe9fde380e9a05be47823cf658e9484427ac7ad74ef2",
    ),
    "g5MatchReadiness": (
        "king_state_match_readiness_generation5_compat_v2.py",
        123_792,
        "fca7346bd95bc312c4832af354e9db4abd427013dcc791cfe5c43083fa4e132a",
    ),
    "g2Verifier": (
        "king_state_v3.py",
        362_242,
        "ffe8625903269567eee083b96dbf38b18f1be6c1a0d94b8fabcb98bdba12e2db",
    ),
    "g2Trainer": (
        "king_state_train_generation3.py",
        214_271,
        "cdae1319d2880207b2ff4a632df90fc9dc26662ec4eb785694afddaa8b533de1",
    ),
    "g2Deep": (
        "deep_hce_v2.py",
        148_725,
        "9e9911b0a56191e5209d6276c004cf36a94fe1530eb915baf1ad5a72d32f7702",
    ),
    "g2Incidence": (
        "phase_incidence_preflight.py",
        31_219,
        "d58f1cb9150c79910c460dea7e95aefd2153e9ce3c8866cd2da3f5a953d591b9",
    ),
    "g2BaseTrainer": (
        "train.py",
        124_508,
        "ac64a0941c176c0d0b2a9024e31955739635d4e958d5f9fda06394d239e3f1a3",
    ),
}

IDENTITY_FIELDS = frozenset({"path", "bytes", "sha256"})
ROUTING_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "rootId", "sourceRootId",
        "sourceGroupId", "phase", "parentSideToMove", "children",
    }
)
ROUTING_CHILD_FIELDS = frozenset({"childId", "normalizedChildOfen"})
COMPONENT_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "rootId", "leakageComponentId",
        "split", "sourceRootId", "sourceGroupId",
    }
)
PRELABEL_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "componentMap", "targetFreeRouting", "terminalClassifierLineage",
        "priorForbiddenRegistry", "priorForbiddenCatalogs", "initializerManifest",
        "sourceRootManifest", "sourceChildrenManifest", "producer",
        "componentRows", "targetFieldsDecodedAtSeal", "targetFieldsEmittedAtSeal",
    }
)
INITIALIZER_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "resultInformationRead", "model", "producer", "selectionMode",
        "selectedCatalogIndex", "selectionSeal", "sourceClosure",
        "orderedCatalog", "fallbackProtocol",
    }
)
INITIALIZER_SELECTION_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "selectionMode",
        "selectedCatalogIndex", "selectedModel", "orderedCatalog",
        "fallbackProtocol", "g6TargetRowsDecoded", "resultInformationRead",
    }
)
INITIALIZER_ENTRY_FIELDS = frozenset(
    {"sourceId", "selectionSeal", "closure", "model", "promotionStatus"}
)
FORBIDDEN_REGISTRY_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "requiredSourceIds", "catalogs", "producer", "targetFieldsDecodedAtSeal",
        "resultInformationRead", "finalStageSeal",
    }
)
FORBIDDEN_REGISTRY_ENTRY_FIELDS = frozenset({"coveredSourceIds", "manifest"})
HCE_CLAIM_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "prelabelSeal", "targetFreeRouting", "engine", "runner", "options",
        "plannedTranscriptPath", "targetRowsDecodedAtClaim",
        "targetFieldsDecodedAtClaim", "resultInformationRead",
    }
)
HCE_COMPLETION_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc", "claim",
        "prelabelSeal", "targetFreeRouting", "engine", "runner", "options",
        "transcript", "inputOrderSha256", "rows", "perspective",
        "targetRowsDecodedAtCompletion", "targetFieldsDecodedAtCompletion",
        "resultInformationRead", "finalStageSeal",
    }
)
HCE_FIELDS = frozenset(
    {"schemaVersion", "kind", "profileId", "childId", "handcraftedCpChildStm"}
)
HCE_OPTIONS_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "engineProtocol",
        "executableMode", "uciVariant", "omegaNnueFile", "perspective",
        "commandArgumentsBeforeRunner", "stdinProtocol", "stdoutProtocol",
    }
)
TEACHER_CLAIM_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "prelabelSeal", "targetFreeRouting", "componentMap", "engine", "runner",
        "options", "budgets", "inputOrderSha256", "plannedProjectionProducer",
        "plannedProjectedCorpusPath", "plannedProjectionManifestPath",
        "preTargetHceCompletion", "targetRowsDecodedAtClaim",
        "targetFieldsDecodedAtClaim", "resultInformationRead",
    }
)
LEDGER_COMPLETION_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc", "claim",
        "attemptLedger", "budgets", "inputOrderSha256", "routedChildren",
        "attemptRecords", "successfulChildren", "rejectedChildren",
        "unresolvedChildren", "deepScoresSha256", "resultInformationRead",
        "finalStageSeal",
    }
)
TEACHER_LABEL_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "rootId", "childId", "childOfen",
        "phase", "parentSideToMove", "deepRank", "deepRegretCp",
        "deepScoreCpRoot", "deepScoreCpChildStm",
    }
)
TEACHER_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "prelabelSeal", "componentMap", "labels", "producer", "rows",
        "childrenPerRoot",
    }
)
PROJECTED_LABEL_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "rootId", "leakageComponentId", "split",
        "childId", "childOfen", "phase", "parentSideToMove", "deepRank",
        "deepRegretCp", "deepScoreCpRoot", "deepScoreCpChildStm",
    }
)
PROJECTED_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc", "corpus",
        "componentMap", "rows", "childrenPerRoot", "rootInventories",
        "phaseSideInventories", "upstreamPrelabelSeal", "upstreamTeacherLabels",
        "upstreamTeacherManifest", "projectionProducer",
    }
)
TEACHER_COMPLETION_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc", "claim",
        "prelabelSeal", "engine", "runner", "options", "budgets",
        "inputOrderSha256", "attemptLedger", "attemptLedgerCompletion",
        "teacherLabels", "teacherManifest", "projectionProducer",
        "projectedCorpus", "labelManifest", "finalStageSeal",
        "resultInformationRead",
    }
)
HCE_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "labelManifest", "corpus", "componentMap", "projection", "engine",
        "runner", "options", "perspective", "rows", "rootInventories",
    }
)
CAPSULE_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "upstreamVerifierExecutable", "upstreamVerifierRunner",
        "upstreamVerifierOptions", "targetFreeRouting", "componentMap",
        "prelabelSeal", "terminalClassifierLineage", "initializerSelection",
        "initializerClosure", "initializerModel", "initializerManifest",
        "plannedProjectionProducer", "plannedProjectedCorpusPath",
        "plannedProjectionManifestPath", "priorForbiddenRegistry",
        "priorForbiddenCatalogs", "teacherClaim", "teacherEngine",
        "teacherRunner", "teacherOptions", "teacherBudgets",
        "teacherInputOrderSha256", "teacherAttemptLedger",
        "teacherAttemptLedgerCompletion", "teacherCompletion",
        "projectionProducer", "teacherLabels", "teacherManifest",
        "projectedCorpus", "labelManifest", "preTargetHceClaim",
        "preTargetHceCompletion", "staticHceEngine", "staticHceRunner",
        "staticHceOptions", "staticHceTranscript", "staticHceManifest",
        "rootInventories", "phaseSideInventories", "closureDeclaration",
        "resultInformationRead", "finalStageSeal",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


def _canonical_json(value: Any, *, newline: bool = True) -> bytes:
    text = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    if newline:
        text += "\n"
    return text.encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _exact_keys(value: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    if set(value) != set(fields):
        missing = sorted(set(fields) - set(value))
        extra = sorted(set(value) - set(fields))
        raise ValueError(
            f"{label} field inventory changed: missing={missing} extra={extra}"
        )


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


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _strict_json_bytes(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not strict UTF-8") from error
    if text.startswith("\ufeff"):
        raise ValueError(f"{label} has a UTF-8 BOM")
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"{label} contains non-finite JSON {token}")
            ),
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is invalid JSON") from error


def _parse_timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{label} is not canonical RFC3339 microsecond UTC")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ValueError(f"{label} is not a valid timestamp") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        raise ValueError(f"{label} is not canonical")
    return parsed


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path.expanduser()))))


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _safe_existing(path: Path) -> Path:
    absolute = _absolute(path)
    for item in (*reversed(absolute.parents), absolute):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"symlink/junction/reparse path is forbidden: {item}")
        if item != absolute and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"artifact parent is not a directory: {item}")
    info = os.lstat(absolute)
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1:
        raise ValueError(f"artifact is not one regular unlinked file: {absolute}")
    return absolute


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int | None]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        getattr(info, "st_mtime_ns", None),
    )


@contextmanager
def _open_stable(path: Path, label: str) -> Iterator[tuple[Path, Any, os.stat_result]]:
    safe = _safe_existing(path)
    before = os.lstat(safe)
    descriptor = os.open(
        safe,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    stream = None
    try:
        opened = os.fstat(descriptor)
        if (
            _stat_key(opened) != _stat_key(before)
            or not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError(f"{label} changed before descriptor open")
        stream = os.fdopen(descriptor, "rb", closefd=False)
        yield safe, stream, opened
        after_open = os.fstat(descriptor)
        if _stat_key(after_open) != _stat_key(opened):
            raise ValueError(f"{label} changed while its descriptor was read")
    finally:
        if stream is not None:
            stream.close()
        os.close(descriptor)
    _safe_existing(safe)
    if _stat_key(os.lstat(safe)) != _stat_key(before):
        raise ValueError(f"{label} path changed during read")


def _identity(path: Path, label: str = "artifact") -> dict[str, Any]:
    digest = hashlib.sha256()
    length = 0
    with _open_stable(path, label) as (safe, stream, _):
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            length += len(block)
            digest.update(block)
    return {"path": str(safe), "bytes": length, "sha256": digest.hexdigest()}


def _snapshot_payload(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    """Return bytes and identity from the same descriptor-stable read."""

    digest = hashlib.sha256()
    chunks: list[bytes] = []
    length = 0
    with _open_stable(path, label) as (safe, stream, _):
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            length += len(block)
            digest.update(block)
            chunks.append(block)
    return {
        "path": str(safe),
        "bytes": length,
        "sha256": digest.hexdigest(),
    }, b"".join(chunks)


def _read_document(path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    length = 0
    with _open_stable(path, label) as (safe, stream, _):
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            length += len(block)
            if length > MAX_JSON_DOCUMENT_BYTES:
                raise ValueError(f"{label} exceeds the bounded document size")
            digest.update(block)
            chunks.append(block)
    value = _strict_json_bytes(b"".join(chunks), label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object")
    return value, {
        "path": str(safe),
        "bytes": length,
        "sha256": digest.hexdigest(),
    }


class StableJsonl:
    def __init__(
        self,
        path: Path,
        label: str,
        *,
        expected_identity: Mapping[str, Any] | None = None,
    ) -> None:
        self.path = path
        self.label = label
        self.expected_identity = (
            None if expected_identity is None else dict(expected_identity)
        )
        self.rows = 0
        self.identity: dict[str, Any] | None = None

    def __iter__(self) -> Iterator[tuple[int, dict[str, Any]]]:
        digest = hashlib.sha256()
        length = 0
        with _open_stable(self.path, self.label) as (safe, stream, _):
            number = 0
            while True:
                line = stream.readline(MAX_JSONL_LINE_BYTES + 1)
                if not line:
                    break
                number += 1
                if len(line) > MAX_JSONL_LINE_BYTES:
                    raise ValueError(f"{self.label}:{number} exceeds the line bound")
                length += len(line)
                digest.update(line)
                if not line.strip():
                    continue
                value = _strict_json_bytes(line, f"{self.label}:{number}")
                if not isinstance(value, dict):
                    raise ValueError(f"{self.label}:{number} is not an object")
                self.rows += 1
                yield number, value
        self.identity = {
            "path": str(safe),
            "bytes": length,
            "sha256": digest.hexdigest(),
        }
        if (
            self.expected_identity is not None
            and self.identity != self.expected_identity
        ):
            raise ValueError(f"{self.label} identity changed")


def _identity_shape(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an identity object")
    _exact_keys(value, IDENTITY_FIELDS, label)
    if (
        type(value["path"]) is not str
        or not value["path"]
        or type(value["bytes"]) is not int
        or value["bytes"] < 0
        or type(value["sha256"]) is not str
        or _SHA256.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity shape changed")
    return dict(value)


def _verified_identity(value: Any, label: str) -> dict[str, Any]:
    expected = _identity_shape(value, label)
    actual = _identity(Path(expected["path"]), label)
    if actual != expected:
        raise ValueError(f"{label} differs from its frozen identity")
    return actual


def _same_identity(left: Any, right: Any, label: str) -> None:
    if not _type_exact_equal(left, right):
        raise ValueError(f"{label} identity/link changed")


def _assert_distinct_roles(roles: Mapping[str, Mapping[str, Any]]) -> None:
    paths: dict[str, str] = {}
    inodes: dict[tuple[int, int], str] = {}
    for role, identity in roles.items():
        path = _safe_existing(Path(str(identity["path"])))
        key = os.path.normcase(os.path.normpath(str(path)))
        info = os.lstat(path)
        inode = (info.st_dev, info.st_ino)
        if key in paths:
            raise ValueError(f"roles {paths[key]!r} and {role!r} share a path")
        if inode in inodes:
            raise ValueError(f"roles {inodes[inode]!r} and {role!r} share an inode")
        paths[key] = role
        inodes[inode] = role


@dataclass(frozen=True)
class PinnedModule:
    identity: Mapping[str, Any]
    module: types.ModuleType


_PINNED_CACHE: dict[str, PinnedModule] = {}


def _dependency_path(filename: str) -> Path:
    return Path(__file__).resolve().parent / filename


def _verifier_runner_path() -> Path:
    selected = (
        Path(__file__)
        if VERIFIER_RUNNER_OVERRIDE is None
        else VERIFIER_RUNNER_OVERRIDE
    )
    return _absolute(selected)


def _pinned_dependency_snapshot(
    name: str,
) -> tuple[dict[str, Any], bytes]:
    filename, expected_bytes, expected_sha256 = DEPENDENCY_PINS[name]
    if (
        type(expected_bytes) is not int
        or expected_bytes <= 0
        or type(expected_sha256) is not str
        or _SHA256.fullmatch(expected_sha256) is None
    ):
        raise RuntimeError(
            f"{name} execution pin is not finalized; install its reviewed bytes/hash"
        )
    path = _dependency_path(filename)
    identity, payload = _snapshot_payload(path, f"{name} dependency")
    if (
        identity["bytes"] != expected_bytes
        or identity["sha256"] != expected_sha256
    ):
        raise RuntimeError(
            f"{name} dependency differs from its execution pin: expected "
            f"{expected_bytes} B/{expected_sha256}, got "
            f"{identity['bytes']} B/{identity['sha256']}"
        )
    return identity, payload


def _load_pinned(name: str) -> PinnedModule:
    cached = _PINNED_CACHE.get(name)
    if cached is not None:
        if _identity(Path(str(cached.identity["path"])), name) != dict(cached.identity):
            raise ValueError(f"pinned dependency {name} changed after load")
        return cached
    filename, _, _ = DEPENDENCY_PINS[name]
    path = _dependency_path(filename)
    identity, payload = _pinned_dependency_snapshot(name)
    module_name = f"_omega_decision_v3_verifier_pinned_{name}"
    module = types.ModuleType(module_name)
    module.__file__ = identity["path"]
    module.__package__ = ""
    module.__loader__ = None
    module.__spec__ = None
    original_import = builtins.__import__
    pinned_imports: dict[str, types.ModuleType] = {}
    if name == "routing":
        pinned_imports = {
            "omega_nnue": _load_pinned("omegaNnue").module,
            "select_screen": _load_pinned("selectScreen").module,
        }
    elif name == "selectScreen":
        pinned_imports = {"omega_nnue": _load_pinned("omegaNnue").module}

    def exact_import(
        import_name: str,
        globals: Mapping[str, Any] | None = None,
        locals: Mapping[str, Any] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if level == 0 and import_name in pinned_imports:
            return pinned_imports[import_name]
        return original_import(import_name, globals, locals, fromlist, level)

    execution_builtins = dict(vars(builtins))
    execution_builtins["__import__"] = exact_import
    module.__dict__["__builtins__"] = execution_builtins
    sys.modules[module_name] = module
    try:
        exec(compile(payload, str(path), "exec", dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    result = PinnedModule(identity, module)
    _PINNED_CACHE[name] = result
    if _identity(path, f"{name} dependency") != identity:
        raise ValueError(f"pinned dependency {name} changed while loaded")
    return result


def _hce_options_document() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
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


def _verify_contract_alignment() -> tuple[types.ModuleType, types.ModuleType]:
    trainer = _load_pinned("trainer").module
    teacher = _load_pinned("teacher").module
    routing = _load_pinned("routing").module
    if (
        trainer.UPSTREAM_VERIFIER_OPTIONS != VERIFIER_OPTIONS
        or set(trainer.UPSTREAM_CAPSULE_FIELDS) != set(CAPSULE_FIELDS)
        or trainer.UPSTREAM_CAPSULE_DECLARATION != CAPSULE_DECLARATION
        or trainer.UPSTREAM_TEACHER_BUDGETS != TEACHER_BUDGETS
        or not _type_exact_equal(
            trainer.UPSTREAM_ROUTING_QUOTAS_PER_PHASE_SIDE,
            ROUTING_QUOTAS_PER_PHASE_SIDE,
        )
        or trainer.static_hce_options_document() != _hce_options_document()
        or teacher.BUDGETS != TEACHER_BUDGETS
        or teacher.STAGE_NODES != STAGE_NODES
        or tuple(routing.PHASES) != PHASES
        or tuple(routing.SIDES) != SIDES
        or tuple(routing.SPLITS) != SPLITS
        or tuple(routing.CELLS) != CELLS
        or not _type_exact_equal(
            dict(routing.QUOTAS), ROUTING_QUOTAS_PER_PHASE_SIDE
        )
        or routing.CHILDREN_PER_ROOT != CHILDREN_PER_ROOT
    ):
        raise RuntimeError(
            "verifier/trainer/teacher/routing contract alignment changed"
        )
    expected_result_sets = {
        "UPSTREAM_VERIFICATION_FIELDS": {
            "schemaVersion", "kind", "profileId", "status", "capsule",
            "verifierExecutable", "verifierRunner", "verifierOptions",
            "initializerAuthority", "terminalAuthority", "priorForbiddenAuthority",
            "componentAuthority", "staticHceAuthority", "teacherLedgerAuthority",
            "projectionAuthority", "resultInformationRead",
        },
        "UPSTREAM_INITIALIZER_AUTHORITY_FIELDS": {
            "manifest", "selectionMode", "selectedCatalogIndex", "selectedModel",
            "catalogSourceIds", "firstEligibleSelected",
            "selectionSemanticsVerified", "selectedPromotionHealthPassed",
            "sourceClosureSemanticsVerified", "fallbackProtocolReplayed",
            "g6TargetRowsDecoded", "resultInformationRead",
        },
        "UPSTREAM_TERMINAL_AUTHORITY_FIELDS": {
            "lineage", "routedChildren", "terminalChildrenExcludedBeforeRouting",
            "unclassifiedChildren", "errorTextAcceptedAsTerminal",
            "rulesSemanticsReplayed", "completionSemanticsVerified",
        },
        "UPSTREAM_FORBIDDEN_AUTHORITY_FIELDS": {
            "catalogs", "registry", "requiredSourceIds", "catalogPositions",
            "manifestsSemanticallyReplayed", "exactPositionOverlaps",
            "conservativeSignatureOverlaps", "sourceArtifactOverlaps",
        },
        "UPSTREAM_COMPONENT_AUTHORITY_FIELDS": {
            "componentMap", "roots", "components", "wholeComponentSplits",
            "semanticsReplayed",
        },
        "UPSTREAM_STATIC_HCE_AUTHORITY_FIELDS": {
            "claim", "completion", "teacherClaim", "prelabelSeal",
            "targetFreeRouting", "engine", "runner", "options", "transcript",
            "inputOrderSha256", "rows", "perspective", "freshReplayMatches",
            "completedBeforeTeacherClaim", "semanticsReplayed",
        },
        "UPSTREAM_TEACHER_LEDGER_AUTHORITY_FIELDS": {
            "claim", "attemptLedger", "attemptLedgerCompletion", "completion",
            "budgets", "routedChildren", "attemptRecords", "successfulChildren",
            "rejectedChildren", "unresolvedChildren", "semanticsReplayed",
        },
        "UPSTREAM_PROJECTION_AUTHORITY_FIELDS": {
            "plannedProducer", "plannedCorpusPath", "plannedManifestPath",
            "actualProducer", "actualCorpus", "actualManifest", "producerMatches",
            "pathsMatch", "semanticsReplayed",
        },
    }
    for field_name, expected in expected_result_sets.items():
        if set(getattr(trainer, field_name)) != expected:
            raise RuntimeError(f"trainer {field_name} changed")
    return trainer, teacher


class AuthorityDatabase:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="omega-decision-v3-verifier-"
        )
        self.path = Path(self.temporary.name) / "authority.sqlite3"
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA cache_size=-32768")
        self.connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        self.connection.executescript(
            """
            CREATE TABLE routes (
                root_id TEXT PRIMARY KEY,
                source_root_id TEXT NOT NULL UNIQUE,
                source_group_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                parent_side TEXT NOT NULL,
                canonical_json TEXT NOT NULL,
                terminal_seen INTEGER NOT NULL DEFAULT 0
            ) WITHOUT ROWID;
            CREATE TABLE children (
                child_id TEXT PRIMARY KEY,
                root_id TEXT NOT NULL,
                ofen TEXT NOT NULL,
                terminal_seen INTEGER NOT NULL DEFAULT 0,
                deep_score INTEGER
            ) WITHOUT ROWID;
            CREATE INDEX children_order ON children(root_id, child_id);
            CREATE TABLE components (
                root_id TEXT PRIMARY KEY,
                component_id TEXT NOT NULL,
                split TEXT NOT NULL,
                source_root_id TEXT NOT NULL,
                source_group_id TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE INDEX component_ids ON components(component_id, split);
            CREATE TABLE stage_success (
                child_id TEXT PRIMARY KEY
            ) WITHOUT ROWID;
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def __enter__(self) -> "AuthorityDatabase":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _normalized_ofen(value: Any, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} is not a nonempty OFEN")
    fields = value.split(" ")
    if len(fields) != 6 or " ".join(fields) != value or fields[1] not in SIDES:
        raise ValueError(f"{label} is not normalized six-field OFEN")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} is not ASCII") from error
    return value


def _stream_list_digest(rows: Iterable[Any]) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    first = True
    for row in rows:
        if not first:
            digest.update(b",")
        digest.update(_canonical_json(row, newline=False))
        first = False
    digest.update(b"]\n")
    return digest.hexdigest()


def _parse_routes(
    path: Path,
    expected_identity: Mapping[str, Any],
    database: AuthorityDatabase,
) -> tuple[int, int, str]:
    reader = StableJsonl(path, "target-free routing", expected_identity=expected_identity)
    roots = 0
    children = 0
    try:
        with database.connection:
            for number, row in reader:
                label = f"target-free routing:{number}"
                _exact_keys(row, ROUTING_FIELDS, label)
                if (
                    type(row["schemaVersion"]) is not int
                    or row["schemaVersion"] != 1
                    or row["kind"] != ROUTING_KIND
                    or row["profileId"] != PROFILE_ID
                    or type(row["rootId"]) is not str
                    or not row["rootId"]
                    or type(row["sourceRootId"]) is not str
                    or not row["sourceRootId"]
                    or type(row["sourceGroupId"]) is not str
                    or not row["sourceGroupId"]
                    or row["phase"] not in PHASES
                    or row["parentSideToMove"] not in SIDES
                    or type(row["children"]) is not list
                    or len(row["children"]) != 4
                ):
                    raise ValueError(f"{label} schema changed")
                normalized_children: list[dict[str, str]] = []
                for child_index, child in enumerate(row["children"]):
                    child_label = f"{label} child {child_index}"
                    if not isinstance(child, dict):
                        raise ValueError(f"{child_label} is not an object")
                    _exact_keys(child, ROUTING_CHILD_FIELDS, child_label)
                    child_id = child["childId"]
                    ofen = _normalized_ofen(
                        child["normalizedChildOfen"], f"{child_label} OFEN"
                    )
                    if (
                        type(child_id) is not str
                        or not child_id
                        or ofen.split(" ")[1] == row["parentSideToMove"]
                    ):
                        raise ValueError(f"{child_label} identity/side changed")
                    database.connection.execute(
                        "INSERT INTO children(child_id,root_id,ofen) VALUES (?,?,?)",
                        (child_id, row["rootId"], ofen),
                    )
                    normalized_children.append(
                        {"childId": child_id, "normalizedChildOfen": ofen}
                    )
                    children += 1
                normalized_children.sort(key=lambda item: item["childId"])
                canonical = {
                    "schemaVersion": 1,
                    "kind": ROUTING_KIND,
                    "profileId": PROFILE_ID,
                    "rootId": row["rootId"],
                    "sourceRootId": row["sourceRootId"],
                    "sourceGroupId": row["sourceGroupId"],
                    "phase": row["phase"],
                    "parentSideToMove": row["parentSideToMove"],
                    "children": normalized_children,
                }
                database.connection.execute(
                    "INSERT INTO routes(root_id,source_root_id,source_group_id,phase,"
                    "parent_side,canonical_json) VALUES (?,?,?,?,?,?)",
                    (
                        row["rootId"],
                        row["sourceRootId"],
                        row["sourceGroupId"],
                        row["phase"],
                        row["parentSideToMove"],
                        _canonical_json(canonical, newline=False).decode("utf-8"),
                    ),
                )
                roots += 1
    except sqlite3.IntegrityError as error:
        raise ValueError("target-free routing repeats a root/child/source root") from error
    if roots == 0 or children != roots * 4 or reader.identity is None:
        raise ValueError("target-free routing is empty or incomplete")
    route_digest = _stream_list_digest(
        json.loads(row[0])
        for row in database.connection.execute(
            "SELECT canonical_json FROM routes ORDER BY root_id"
        )
    )
    return roots, children, route_digest


def _parse_components(
    path: Path,
    expected_identity: Mapping[str, Any],
    database: AuthorityDatabase,
    root_count: int,
) -> int:
    reader = StableJsonl(path, "component map", expected_identity=expected_identity)
    try:
        with database.connection:
            for number, row in reader:
                label = f"component map:{number}"
                _exact_keys(row, COMPONENT_FIELDS, label)
                if (
                    type(row["schemaVersion"]) is not int
                    or row["schemaVersion"] != 1
                    or row["kind"] != COMPONENT_KIND
                    or row["profileId"] != PROFILE_ID
                    or type(row["rootId"]) is not str
                    or type(row["leakageComponentId"]) is not str
                    or not row["leakageComponentId"]
                    or row["split"] not in SPLITS
                    or type(row["sourceRootId"]) is not str
                    or type(row["sourceGroupId"]) is not str
                ):
                    raise ValueError(f"{label} schema changed")
                route = database.connection.execute(
                    "SELECT source_root_id,source_group_id FROM routes WHERE root_id=?",
                    (row["rootId"],),
                ).fetchone()
                if route != (row["sourceRootId"], row["sourceGroupId"]):
                    raise ValueError(f"{label} differs from target-free routing")
                database.connection.execute(
                    "INSERT INTO components(root_id,component_id,split,source_root_id,"
                    "source_group_id) VALUES (?,?,?,?,?)",
                    (
                        row["rootId"],
                        row["leakageComponentId"],
                        row["split"],
                        row["sourceRootId"],
                        row["sourceGroupId"],
                    ),
                )
    except sqlite3.IntegrityError as error:
        raise ValueError("component map repeats a root") from error
    rows = int(database.connection.execute("SELECT count(*) FROM components").fetchone()[0])
    if rows != root_count or reader.identity is None:
        raise ValueError("component/routing root inventories differ")
    missing = database.connection.execute(
        "SELECT 1 FROM routes r LEFT JOIN components c ON c.root_id=r.root_id "
        "WHERE c.root_id IS NULL LIMIT 1"
    ).fetchone()
    cross_component = database.connection.execute(
        "SELECT component_id FROM components GROUP BY component_id "
        "HAVING count(DISTINCT split)<>1 LIMIT 1"
    ).fetchone()
    cross_group = database.connection.execute(
        "SELECT source_group_id FROM components GROUP BY source_group_id "
        "HAVING count(DISTINCT component_id||char(0)||split)<>1 LIMIT 1"
    ).fetchone()
    if missing or cross_component or cross_group:
        raise ValueError("component coverage/whole-component split semantics changed")
    return int(
        database.connection.execute(
            "SELECT count(DISTINCT component_id) FROM components"
        ).fetchone()[0]
    )


CAPSULE_IDENTITY_FIELDS = (
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
    "priorForbiddenRegistry",
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
    "preTargetHceCompletion",
    "staticHceEngine",
    "staticHceRunner",
    "staticHceOptions",
    "staticHceTranscript",
    "staticHceManifest",
)


def _parse_capsule(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    capsule, capsule_identity = _read_document(path, "Omega decision-v3 capsule")
    _exact_keys(capsule, CAPSULE_FIELDS, "Omega decision-v3 capsule")
    if (
        type(capsule["schemaVersion"]) is not int
        or capsule["schemaVersion"] != 1
        or capsule["kind"] != CAPSULE_KIND
        or capsule["profileId"] != PROFILE_ID
        or capsule["status"] != "closed-pretarget-to-final-projection-lineage"
        or not _type_exact_equal(capsule["teacherBudgets"], TEACHER_BUDGETS)
        or not _type_exact_equal(
            capsule["closureDeclaration"], CAPSULE_DECLARATION
        )
        or capsule["resultInformationRead"] is not False
        or capsule["finalStageSeal"] is not True
    ):
        raise ValueError("Omega decision-v3 capsule header/closure changed")
    _parse_timestamp(capsule["createdUtc"], "capsule createdUtc")
    identities = {
        field: _verified_identity(capsule[field], f"capsule {field}")
        for field in CAPSULE_IDENTITY_FIELDS
    }
    catalogs = capsule["priorForbiddenCatalogs"]
    if type(catalogs) is not list or not catalogs:
        raise ValueError("capsule prior-forbidden catalog list is empty")
    verified_catalogs = [
        _verified_identity(value, f"capsule priorForbiddenCatalogs[{index}]")
        for index, value in enumerate(catalogs)
    ]
    if not _type_exact_equal(verified_catalogs, catalogs):
        raise ValueError("capsule prior-forbidden catalog identities changed")
    _assert_distinct_roles(
        {
            role: identities[role]
            for role in (
                "preTargetHceClaim",
                "preTargetHceCompletion",
                "staticHceTranscript",
                "teacherClaim",
                "teacherAttemptLedger",
                "teacherAttemptLedgerCompletion",
                "teacherLabels",
                "teacherManifest",
                "projectedCorpus",
                "labelManifest",
            )
        }
    )
    return capsule, capsule_identity, identities


SOURCE_REPLAY_FIELDS = frozenset(
    {
        "sourceId",
        "promotionStatus",
        "selectionSeal",
        "closure",
        "rawModel",
        "healthPassed",
        "sourceVerifier",
        "unavailabilityEvidence",
        "resultInformationRead",
    }
)
G2_UNAVAILABLE_EVIDENCE_FIELDS = frozenset(
    {
        "protocol",
        "fullReplayRejected",
        "deepReplayRejected",
        "compatibilityPatchesApplied",
        "deepFreeze",
        "generation2Incident",
        "currentRootSamplerSource",
        "frozenRootSamplerSource",
        "currentOmegaNnueModule",
        "frozenOmegaNnueModule",
        "resultInformationRead",
    }
)
SOURCE_REPLAY_TIMEOUT_SECONDS = 30 * 60
MAX_SOURCE_REPLAY_STDOUT_BYTES = 1024 * 1024
MAX_SOURCE_REPLAY_STDERR_BYTES = 1024 * 1024

# Tests exercise the same fresh-process and exact-pin boundary with a tiny
# synthetic source authority.  Production CLI processes never populate this
# private mapping and therefore can only execute the canonical source modules.
_TEST_INITIALIZER_SOURCE_RUNNERS: dict[
    str, tuple[Path, int, str]
] = {}


def _run_json_process(
    command: Sequence[str], *, label: str, timeout: int
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="omega-decision-v3-source-replay-"
    ) as directory:
        completed = subprocess.run(
            list(command),
            cwd=Path(directory),
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    if len(completed.stdout) > MAX_SOURCE_REPLAY_STDOUT_BYTES:
        raise ValueError(f"{label} emitted oversized stdout")
    if completed.returncode != 0 or completed.stderr:
        detail = completed.stderr[-4000:].decode("utf-8", errors="replace")
        raise ValueError(f"{label} failed: {detail or 'nonzero exit'}")
    value = _strict_json_bytes(completed.stdout, f"{label} stdout")
    if type(value) is not dict:
        raise ValueError(f"{label} did not emit one JSON object")
    return value


def _run_process_expect_failure(
    command: Sequence[str],
    *,
    label: str,
    timeout: int,
    required_text: str,
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="omega-decision-v3-expected-failure-"
    ) as directory:
        completed = subprocess.run(
            list(command),
            cwd=Path(directory),
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    if (
        len(completed.stdout) > MAX_SOURCE_REPLAY_STDOUT_BYTES
        or len(completed.stderr) > MAX_SOURCE_REPLAY_STDERR_BYTES
    ):
        raise ValueError(f"{label} emitted oversized output")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode == 0:
        raise ValueError(f"{label} unexpectedly passed")
    if completed.stdout or required_text not in stderr:
        raise ValueError(f"{label} did not fail for the frozen identity mismatch")


def _source_pin_records(names: Sequence[str]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name in names:
        identity, _ = _pinned_dependency_snapshot(name)
        records[name] = identity
    return records


def _recheck_source_pins(records: Mapping[str, Mapping[str, Any]]) -> None:
    for name, identity in records.items():
        if _identity(Path(str(identity["path"])), f"{name} source recheck") != dict(
            identity
        ):
            raise ValueError(f"initializer source verifier {name} changed during replay")


def _source_bridge_prelude(
    records: Mapping[str, Mapping[str, Any]], tool_directory: Path
) -> str:
    serialized = json.dumps(records, sort_keys=True, separators=(",", ":"))
    return f"""
import hashlib,json,pathlib,sys
pins=json.loads({serialized!r})
for name,record in pins.items():
    path=pathlib.Path(record['path'])
    payload=path.read_bytes()
    if len(payload)!=record['bytes'] or hashlib.sha256(payload).hexdigest()!=record['sha256']:
        raise RuntimeError('initializer source pin changed: '+name)
sys.path.insert(0,{str(tool_directory)!r})
"""


def _validate_g2_unavailability_evidence(
    value: Any,
    *,
    authenticate_protocol: bool,
    selection_seal: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("initializer G2-K2 unavailability evidence is not an object")
    _exact_keys(
        value,
        G2_UNAVAILABLE_EVIDENCE_FIELDS,
        "initializer G2-K2 unavailability evidence",
    )
    if (
        not _type_exact_equal(value["protocol"], G2_UNAVAILABLE_PROTOCOL)
        or value["fullReplayRejected"] is not True
        or value["deepReplayRejected"] is not True
        or value["compatibilityPatchesApplied"] is not False
        or value["resultInformationRead"] is not False
    ):
        raise ValueError("initializer G2-K2 unavailability protocol changed")
    result = dict(value)
    for field in (
        "deepFreeze",
        "generation2Incident",
        "currentRootSamplerSource",
        "currentOmegaNnueModule",
    ):
        result[field] = _verified_identity(
            value[field], f"initializer G2-K2 unavailability {field}"
        )
    for field in ("frozenRootSamplerSource", "frozenOmegaNnueModule"):
        result[field] = _identity_shape(
            value[field], f"initializer G2-K2 unavailability {field}"
        )
    if authenticate_protocol:
        repository = _g2_authority_repository(selection_seal)

        def expected(record: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "path": str(
                    _absolute(repository / Path(str(record["relativePath"])))
                ),
                "bytes": record["bytes"],
                "sha256": record["sha256"],
            }

        current_root = result["currentRootSamplerSource"]
        current_omega = result["currentOmegaNnueModule"]
        frozen_root = result["frozenRootSamplerSource"]
        frozen_omega = result["frozenOmegaNnueModule"]
        current_root_pin = G2_UNAVAILABLE_PROTOCOL["currentSources"][
            "rootSamplerSource"
        ]
        current_omega_pin = G2_UNAVAILABLE_PROTOCOL["currentSources"]["omegaNnue"]
        frozen_root_pin = G2_UNAVAILABLE_PROTOCOL["frozenDependencies"][
            "rootSamplerSource"
        ]
        frozen_omega_pin = G2_UNAVAILABLE_PROTOCOL["frozenDependencies"][
            "omegaNnue"
        ]
        if (
            not _type_exact_equal(current_root, expected(current_root_pin))
            or not _type_exact_equal(current_omega, expected(current_omega_pin))
            or not _type_exact_equal(
                result["deepFreeze"],
                expected(G2_UNAVAILABLE_PROTOCOL["artifacts"]["deepFreeze"]),
            )
            or not _type_exact_equal(
                result["generation2Incident"],
                expected(
                    G2_UNAVAILABLE_PROTOCOL["artifacts"][
                        "generation2Incident"
                    ]
                ),
            )
            or not _type_exact_equal(frozen_root, expected(frozen_root_pin))
            or not _type_exact_equal(frozen_omega, expected(frozen_omega_pin))
        ):
            raise ValueError("initializer G2-K2 forensic dependency pins changed")
    return result


def _validate_source_report(
    value: Mapping[str, Any], source_id: str, *, authenticate_g2: bool = False
) -> dict[str, Any]:
    _exact_keys(value, SOURCE_REPLAY_FIELDS, f"initializer {source_id} source replay")
    permitted_statuses = (
        {"promoted", "failed", "aborted"}
        if source_id == "G5"
        else {"unavailable"}
    )
    if (
        value["sourceId"] != source_id
        or value["promotionStatus"] not in permitted_statuses
        or type(value["healthPassed"]) is not bool
        or value["resultInformationRead"] is not False
    ):
        raise ValueError(f"initializer {source_id} source replay envelope changed")
    result = dict(value)
    for field in ("selectionSeal", "closure", "sourceVerifier"):
        result[field] = _verified_identity(
            value[field], f"initializer {source_id} replay {field}"
        )
    if value["rawModel"] is None:
        result["rawModel"] = None
    else:
        result["rawModel"] = _verified_identity(
            value["rawModel"], f"initializer {source_id} replay rawModel"
        )
    if (
        value["promotionStatus"] == "promoted"
        and value["healthPassed"] is not True
    ):
        raise ValueError(f"initializer {source_id} promotion failed source health")
    if source_id == "G2-K2":
        if value["rawModel"] is not None or value["healthPassed"] is not False:
            raise ValueError("initializer G2-K2 unavailable source names a model or health")
        result["unavailabilityEvidence"] = _validate_g2_unavailability_evidence(
            value["unavailabilityEvidence"],
            authenticate_protocol=authenticate_g2,
            selection_seal=result["selectionSeal"],
        )
    elif value["unavailabilityEvidence"] is not None:
        raise ValueError("initializer G5 source replay names G2 unavailability evidence")
    return result


def _run_test_source_replay(source_id: str) -> dict[str, Any] | None:
    override = _TEST_INITIALIZER_SOURCE_RUNNERS.get(source_id)
    if override is None:
        return None
    path, expected_bytes, expected_sha256 = override
    identity, _ = _snapshot_payload(path, f"test {source_id} source verifier")
    if (
        identity["bytes"] != expected_bytes
        or identity["sha256"] != expected_sha256
    ):
        raise RuntimeError(f"test {source_id} source verifier pin changed")
    value = _run_json_process(
        [sys.executable, "-I", "-B", str(path), source_id],
        label=f"test {source_id} source verifier",
        timeout=120,
    )
    if _identity(path, f"test {source_id} source verifier recheck") != identity:
        raise ValueError(f"test {source_id} source verifier changed during replay")
    return _validate_source_report(value, source_id)


def _run_g5_source_replay(expected_status: str) -> dict[str, Any]:
    synthetic = _run_test_source_replay("G5")
    if synthetic is not None:
        return synthetic
    names = ("g5Readiness", "g5Matches", "g5MatchReadiness")
    records = _source_pin_records(names)
    prelude = _source_bridge_prelude(records, Path(__file__).resolve().parent)
    if expected_status == "promoted":
        body = r"""
import king_state_confirmation_readiness_v2 as readiness
evidence=readiness._g5_nomination_evidence(run_fresh_verifier=True)
value={
 'sourceId':'G5',
 'promotionStatus':'promoted',
 'selectionSeal':evidence['authorization'],
 'closure':evidence['lineage']['compatibilityClosure'],
 'rawModel':evidence['selectedNetwork'],
 'healthPassed':True,
 'sourceVerifier':evidence['freshVerifier'],
 'unavailabilityEvidence':None,
 'resultInformationRead':False,
}
print(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False))
"""
    else:
        body = r"""
import king_state_match_readiness_generation5_compat_v2 as readiness
import king_state_matches_generation5_compat_v2 as matches
protocol,authorization,authorization_path=matches._authorization(None,fresh=True)
matches._verify_global_inventory(protocol,authorization,authorization_path)
closure_path=readiness._namespace(protocol,'closure')
closure=matches._verify_closure(closure_path,protocol,authorization,authorization_path)
statuses=[]
for gate in matches.GATES:
    path=matches._decision_path(protocol,gate)
    if path.is_file():
        decision=matches._verify_decision(path,protocol,gate)
        statuses.append('aborted' if decision.get('kind')==matches.ABORT_DECISION_KIND else ('failed' if decision.get('passed') is False else 'passed'))
status='promoted' if closure['clearlySuperior'] is True else ('aborted' if 'aborted' in statuses else 'failed')
value={
 'sourceId':'G5',
 'promotionStatus':status,
 'selectionSeal':readiness.identity(authorization_path),
 'closure':readiness.identity(closure_path),
 'rawModel':authorization['selectedNetwork'],
 'healthPassed':status=='promoted',
 'sourceVerifier':readiness.identity(pathlib.Path(matches.__file__)),
 'unavailabilityEvidence':None,
 'resultInformationRead':False,
}
print(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False))
"""
    value = _run_json_process(
        [sys.executable, "-I", "-B", "-c", prelude + body],
        label="canonical G5 initializer source replay",
        timeout=SOURCE_REPLAY_TIMEOUT_SECONDS,
    )
    _recheck_source_pins(records)
    return _validate_source_report(value, "G5")


def _g2_authority_repository(selection_seal: Mapping[str, Any]) -> Path:
    selection_path = _absolute(Path(str(selection_seal["path"])))
    relative = Path(
        str(
            G2_UNAVAILABLE_PROTOCOL["artifacts"]["selectionSeal"][
                "relativePath"
            ]
        )
    )
    repository = selection_path
    for _ in relative.parts:
        repository = repository.parent
    expected = _absolute(repository / relative)
    if os.path.normcase(str(expected)) != os.path.normcase(str(selection_path)):
        raise ValueError("initializer G2-K2 selection seal is not canonical")
    return repository


def _g2_protocol_snapshot(
    repository: Path, record: Mapping[str, Any], label: str
) -> dict[str, Any]:
    path = _absolute(repository / Path(str(record["relativePath"])))
    try:
        path.relative_to(repository)
    except ValueError as error:
        raise ValueError(f"initializer G2-K2 {label} escaped its repository") from error
    identity = _identity(path, f"initializer G2-K2 {label}")
    if (
        identity["bytes"] != record["bytes"]
        or identity["sha256"] != record["sha256"]
    ):
        raise ValueError(f"initializer G2-K2 {label} differs from its forensic pin")
    return identity


def _run_g2_source_replay(entry: Mapping[str, Any]) -> dict[str, Any]:
    synthetic = _run_test_source_replay("G2-K2")
    if synthetic is not None:
        return synthetic
    repository = _g2_authority_repository(entry["selectionSeal"])
    artifacts = G2_UNAVAILABLE_PROTOCOL["artifacts"]
    current_sources = G2_UNAVAILABLE_PROTOCOL["currentSources"]
    records = {
        name: _g2_protocol_snapshot(repository, record, name)
        for name, record in current_sources.items()
    }
    records.update(
        {
            name: _g2_protocol_snapshot(repository, record, name)
            for name, record in artifacts.items()
        }
    )
    if (
        not _type_exact_equal(records["selectionSeal"], entry["selectionSeal"])
        or not _type_exact_equal(records["closure"], entry["closure"])
    ):
        raise ValueError("initializer G2-K2 forensic artifact cross-link changed")

    resolution, resolution_identity = _read_document(
        Path(str(records["selectionSeal"]["path"])),
        "initializer G2-K2 resolution seal",
    )
    if (
        resolution_identity != records["selectionSeal"]
        or resolution.get("kind") != "omega-nnue-king-state-v3-initializer-resolution"
        or resolution.get("selectedInitializer") != "K2"
        or resolution.get("informationBoundary", {}).get(
            "generation2TargetFieldsDecoded"
        ) != 0
        or resolution.get("informationBoundary", {}).get(
            "generation2NumericMetricsUsed"
        ) is not False
    ):
        raise ValueError("initializer G2-K2 resolution seal semantics changed")
    deep_lock, deep_identity = _read_document(
        Path(str(records["deepFreeze"]["path"])),
        "initializer G2-K2 deep freeze",
    )
    if deep_identity != records["deepFreeze"]:
        raise ValueError("initializer G2-K2 deep freeze changed during parse")
    try:
        frozen_omega = _identity_shape(
            deep_lock["freeze"]["omegaNnueModule"],
            "initializer G2-K2 frozen omega_nnue.py",
        )
    except (KeyError, TypeError) as error:
        raise ValueError("initializer G2-K2 deep freeze semantics changed") from error
    frozen_omega_pin = G2_UNAVAILABLE_PROTOCOL["frozenDependencies"]["omegaNnue"]
    expected_frozen_omega = {
        "path": records["omegaNnue"]["path"],
        "bytes": frozen_omega_pin["bytes"],
        "sha256": frozen_omega_pin["sha256"],
    }
    if not _type_exact_equal(frozen_omega, expected_frozen_omega):
        raise ValueError("initializer G2-K2 frozen omega_nnue.py pin changed")
    frozen_root_pin = G2_UNAVAILABLE_PROTOCOL["frozenDependencies"][
        "rootSamplerSource"
    ]
    frozen_root = {
        "path": records["rootSamplerSource"]["path"],
        "bytes": frozen_root_pin["bytes"],
        "sha256": frozen_root_pin["sha256"],
    }

    tool_directory = repository / "tools" / "omega_nnue"
    prelude = _source_bridge_prelude(records, tool_directory)
    failures = G2_UNAVAILABLE_PROTOCOL["requiredFreshFailures"]
    _run_process_expect_failure(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            prelude + "\nimport king_state_v3 as generation3\ngeneration3._verify_seal()\n",
        ],
        label="untouched G2-K2 full source replay",
        timeout=SOURCE_REPLAY_TIMEOUT_SECONDS,
        required_text=str(failures["fullSeal"]),
    )
    deep_path = records["deepFreeze"]["path"]
    _run_process_expect_failure(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            prelude
            + "\nimport deep_hce_v2 as deep\n"
            + f"deep.verify_freeze(pathlib.Path({deep_path!r}))\n",
        ],
        label="untouched G2-K2 deep-freeze replay",
        timeout=SOURCE_REPLAY_TIMEOUT_SECONDS,
        required_text=str(failures["deepFreeze"]),
    )
    _recheck_source_pins(records)
    value = {
        "sourceId": "G2-K2",
        "promotionStatus": "unavailable",
        "selectionSeal": records["selectionSeal"],
        "closure": records["closure"],
        "rawModel": None,
        "healthPassed": False,
        "sourceVerifier": records["g2Verifier"],
        "unavailabilityEvidence": {
            "protocol": dict(G2_UNAVAILABLE_PROTOCOL),
            "fullReplayRejected": True,
            "deepReplayRejected": True,
            "compatibilityPatchesApplied": False,
            "deepFreeze": records["deepFreeze"],
            "generation2Incident": records["generation2Incident"],
            "currentRootSamplerSource": records["rootSamplerSource"],
            "frozenRootSamplerSource": frozen_root,
            "currentOmegaNnueModule": records["omegaNnue"],
            "frozenOmegaNnueModule": frozen_omega,
            "resultInformationRead": False,
        },
        "resultInformationRead": False,
    }
    return _validate_source_report(
        value, "G2-K2", authenticate_g2=True
    )


def _run_initializer_generator(arguments: Sequence[str]) -> dict[str, Any]:
    identity, _ = _pinned_dependency_snapshot("initializerGenerator")
    result = _run_json_process(
        [sys.executable, "-I", "-B", str(identity["path"]), *arguments],
        label="deterministic initializer generator",
        timeout=10 * 60,
    )
    if _identity(
        Path(str(identity["path"])), "initializer generator recheck"
    ) != identity:
        raise ValueError("initializer generator changed during replay")
    return result


def _verify_initializer_health(model: Mapping[str, Any]) -> None:
    model_identity = _verified_identity(model, "initializer health model")
    runner_identity, _ = _pinned_dependency_snapshot("evaluatorRunner")
    result = _run_json_process(
        [
            sys.executable,
            "-I",
            "-B",
            str(runner_identity["path"]),
            "--validate-network-health",
            str(model_identity["path"]),
        ],
        label="initializer deployment-health replay",
        timeout=10 * 60,
    )
    fields = {
        "modelSha256",
        "modelBytes",
        "finitePredictions",
        "quantizationRoundTripExact",
        "expectedNetworkBytes",
        "runtimeParity",
        "maximumAbsResidualCp",
    }
    if set(result) != fields or (
        result["modelSha256"] != model_identity["sha256"]
        or type(result["modelBytes"]) is not int
        or result["modelBytes"] != model_identity["bytes"]
        or any(
            result[field] is not True
            for field in (
                "finitePredictions",
                "quantizationRoundTripExact",
                "expectedNetworkBytes",
                "runtimeParity",
            )
        )
        or type(result["maximumAbsResidualCp"]) is not int
        or isinstance(result["maximumAbsResidualCp"], bool)
        or not 0 <= result["maximumAbsResidualCp"] <= 2_000
    ):
        raise ValueError("initializer failed fresh deployment-health replay")
    if _identity(
        Path(str(runner_identity["path"])), "initializer health runner recheck"
    ) != runner_identity:
        raise ValueError("initializer health runner changed during replay")


def _verify_fallback_model(model: Mapping[str, Any]) -> None:
    expected = _verified_identity(model, "deterministic fallback model")
    result = _run_initializer_generator(
        ["--verify-fallback", str(expected["path"])]
    )
    required = {
        "schemaVersion",
        "kind",
        "profileId",
        "architectureId",
        "omegaNnueArchitectureId",
        "omegaNnueArchitecture",
        "seed",
        "prng",
        "bytes",
        "sha256",
        "gameResultsRead",
        "targetRowsDecoded",
        "model",
        "exactBytesMatch",
    }
    if set(result) != required or (
        result["schemaVersion"] != 1
        or result["kind"]
        != "omega-nnue-king-state-v6-deterministic-fallback-initializer"
        or result["profileId"] != PROFILE_ID
        or result["architectureId"] != FALLBACK_PROTOCOL["architecture"]
        or result["omegaNnueArchitectureId"] != 4
        or result["omegaNnueArchitecture"] != "omega-interaction-residual"
        or result["seed"] != FALLBACK_PROTOCOL["seed"]
        or result["prng"] != FALLBACK_PROTOCOL["prng"]
        or not _type_exact_equal(result["model"], expected)
        or result["bytes"] != expected["bytes"]
        or result["sha256"] != expected["sha256"]
        or result["exactBytesMatch"] is not True
        or result["gameResultsRead"] is not False
        or result["targetRowsDecoded"] != 0
    ):
        raise ValueError("deterministic fallback generator replay changed")
    _verify_initializer_health(expected)


def _verify_g2_mapping(
    raw_model: Mapping[str, Any], mapped_model: Mapping[str, Any]
) -> None:
    raw = _verified_identity(raw_model, "raw G2-K2 model")
    mapped = _verified_identity(mapped_model, "mapped G2-K2 model")
    result = _run_initializer_generator(
        ["--verify-g2-mapping", str(raw["path"]), str(mapped["path"])]
    )
    required = {
        "schemaVersion",
        "kind",
        "profileId",
        "rawModel",
        "mappedModel",
        "mappingProof",
        "exactBytesMatch",
        "gameResultsRead",
        "targetRowsDecoded",
    }
    proof = result.get("mappingProof")
    if set(result) != required or type(proof) is not dict or (
        result["schemaVersion"] != 1
        or result["kind"]
        != "omega-nnue-king-state-v6-g2-k2-architecture-mapping"
        or result["profileId"] != PROFILE_ID
        or not _type_exact_equal(result["rawModel"], raw)
        or not _type_exact_equal(result["mappedModel"], mapped)
        or result["exactBytesMatch"] is not True
        or result["gameResultsRead"] is not False
        or result["targetRowsDecoded"] != 0
        or not _type_exact_equal(
            proof,
            {
                "sourceArchitectureId": 3,
                "mappedArchitectureId": 4,
                "architecture3RowsByteIdentical": True,
                "appendedInteractionRows": 64,
                "appendedInteractionRowsAllZero": True,
                "nonFeatureTensorsByteIdentical": True,
                "epochZeroParityPositions": 5,
                "epochZeroPredictionMismatches": 0,
            },
        )
    ):
        raise ValueError("G2-K2 architecture-4 mapping replay changed")
    _verify_initializer_health(mapped)


def _verify_initializer_source_entry(
    entry: Mapping[str, Any], source_id: str
) -> tuple[bool, bool]:
    status = entry["promotionStatus"]
    if source_id == "G5" and status == "unavailable":
        raise ValueError(
            "initializer G5 has no terminal failure, abort, or promotion closure"
        )
    if source_id == "G2-K2" and (
        status != "unavailable" or entry["model"] is not None
    ):
        raise ValueError(
            "initializer G2-K2 must be authenticated unavailable with a null model"
        )
    report = (
        _run_g5_source_replay(status)
        if source_id == "G5"
        else _run_g2_source_replay(entry)
    )
    if (
        report["promotionStatus"] != status
        or not _type_exact_equal(report["selectionSeal"], entry["selectionSeal"])
        or not _type_exact_equal(report["closure"], entry["closure"])
    ):
        raise ValueError(
            f"initializer {source_id} source selection/closure status changed"
        )
    promoted = status == "promoted"
    if source_id == "G5":
        expected_model = report["rawModel"] if promoted else None
        if not _type_exact_equal(entry["model"], expected_model):
            raise ValueError("initializer G5 source model cross-link changed")
        if promoted:
            _verify_initializer_health(report["rawModel"])
    elif (
        report["rawModel"] is not None
        or report["healthPassed"] is not False
        or report["unavailabilityEvidence"] is None
    ):
        raise ValueError("initializer G2-K2 unavailable source evidence changed")
    return promoted, bool(report["healthPassed"] and promoted)


def _verify_initializer(
    manifest_path: Path,
    capsule: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, manifest_identity = _read_document(
        manifest_path, "initializer manifest"
    )
    _same_identity(
        manifest_identity, identities["initializerManifest"], "initializer manifest"
    )
    _exact_keys(manifest, INITIALIZER_FIELDS, "initializer manifest")
    if (
        type(manifest["schemaVersion"]) is not int
        or manifest["schemaVersion"] != 1
        or manifest["kind"] != INITIALIZER_KIND
        or manifest["profileId"] != PROFILE_ID
        or manifest["status"] != "frozen-pre-g6-initializer-selection"
        or manifest["selectionMode"]
        not in {"promoted-prior", "deterministic-fallback"}
        or not _type_exact_equal(manifest["fallbackProtocol"], FALLBACK_PROTOCOL)
        or manifest["resultInformationRead"] is not False
        or type(manifest["orderedCatalog"]) is not list
        or len(manifest["orderedCatalog"]) != len(INITIALIZER_CATALOG)
    ):
        raise ValueError("initializer manifest header/policy changed")
    _parse_timestamp(manifest["createdUtc"], "initializer manifest createdUtc")
    for field in ("model", "producer", "selectionSeal", "sourceClosure"):
        _verified_identity(manifest[field], f"initializer manifest {field}")
    _same_identity(manifest["model"], identities["initializerModel"], "initializer model")
    _same_identity(
        manifest["selectionSeal"], identities["initializerSelection"],
        "initializer selection",
    )
    _same_identity(
        manifest["sourceClosure"], identities["initializerClosure"],
        "initializer closure",
    )
    selection, selection_identity = _read_document(
        Path(str(manifest["selectionSeal"]["path"])), "initializer selection"
    )
    _same_identity(selection_identity, manifest["selectionSeal"], "initializer selection")
    _exact_keys(selection, INITIALIZER_SELECTION_FIELDS, "initializer selection")
    if (
        type(selection["schemaVersion"]) is not int
        or selection["schemaVersion"] != 1
        or selection["kind"] != INITIALIZER_SELECTION_KIND
        or selection["profileId"] != PROFILE_ID
        or selection["selectionMode"] != manifest["selectionMode"]
        or not _type_exact_equal(
            selection["selectedCatalogIndex"], manifest["selectedCatalogIndex"]
        )
        or not _type_exact_equal(selection["selectedModel"], manifest["model"])
        or not _type_exact_equal(
            selection["orderedCatalog"], manifest["orderedCatalog"]
        )
        or not _type_exact_equal(selection["fallbackProtocol"], FALLBACK_PROTOCOL)
        or type(selection["g6TargetRowsDecoded"]) is not int
        or selection["g6TargetRowsDecoded"] != 0
        or selection["resultInformationRead"] is not False
    ):
        raise ValueError("initializer selection differs from frozen manifest")

    promoted_indexes: list[int] = []
    selected_health: bool | None = None
    for index, (entry, source_id) in enumerate(
        zip(manifest["orderedCatalog"], INITIALIZER_CATALOG, strict=True)
    ):
        if not isinstance(entry, dict):
            raise ValueError(f"initializer catalog {source_id} is not an object")
        _exact_keys(entry, INITIALIZER_ENTRY_FIELDS, f"initializer catalog {source_id}")
        permitted_statuses = (
            {"promoted", "failed", "aborted"}
            if source_id == "G5"
            else {"unavailable"}
        )
        if (
            entry["sourceId"] != source_id
            or entry["promotionStatus"] not in permitted_statuses
        ):
            raise ValueError(f"initializer catalog {source_id} status changed")
        _verified_identity(entry["closure"], f"initializer {source_id} closure")
        if entry["model"] is not None:
            _verified_identity(entry["model"], f"initializer {source_id} model")
        promoted, health = _verify_initializer_source_entry(entry, source_id)
        if promoted:
            promoted_indexes.append(index)
            if index == manifest["selectedCatalogIndex"]:
                selected_health = health

    selected_index = manifest["selectedCatalogIndex"]
    if manifest["selectionMode"] == "promoted-prior":
        if (
            type(selected_index) is not int
            or isinstance(selected_index, bool)
            or not promoted_indexes
            or selected_index != promoted_indexes[0]
        ):
            raise ValueError("initializer did not select the first promoted catalog")
        selected = manifest["orderedCatalog"][selected_index]
        if (
            not _type_exact_equal(selected["model"], manifest["model"])
            or not _type_exact_equal(
                selected["closure"], manifest["sourceClosure"]
            )
            or selected_health is not True
        ):
            raise ValueError("initializer selected model/closure/health changed")
        fallback_replayed = False
    else:
        if selected_index is not None or promoted_indexes:
            raise ValueError("fallback initializer is not the no-promoted-entry case")
        if (
            manifest["orderedCatalog"][0]["promotionStatus"]
            not in {"failed", "aborted"}
            or manifest["orderedCatalog"][1]["promotionStatus"]
            != "unavailable"
        ):
            raise ValueError(
                "fallback initializer lacks terminal G5 closure or exact G2 unavailability"
            )
        if not _type_exact_equal(selection["selectedModel"], manifest["model"]):
            raise ValueError("fallback initializer model changed")
        _verify_fallback_model(manifest["model"])
        selected_health = None
        fallback_replayed = True

    return manifest, {
        "manifest": dict(manifest_identity),
        "selectionMode": manifest["selectionMode"],
        "selectedCatalogIndex": selected_index,
        "selectedModel": dict(identities["initializerModel"]),
        "catalogSourceIds": list(INITIALIZER_CATALOG),
        "firstEligibleSelected": True,
        "selectionSemanticsVerified": True,
        "selectedPromotionHealthPassed": selected_health,
        "sourceClosureSemanticsVerified": True,
        "fallbackProtocolReplayed": fallback_replayed,
        "g6TargetRowsDecoded": 0,
        "resultInformationRead": False,
    }


def _verify_prelabel(
    identities: Mapping[str, Mapping[str, Any]],
    capsule: Mapping[str, Any],
    root_count: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prelabel, prelabel_identity = _read_document(
        Path(str(identities["prelabelSeal"]["path"])), "prelabel seal"
    )
    _same_identity(prelabel_identity, identities["prelabelSeal"], "prelabel seal")
    _exact_keys(prelabel, PRELABEL_FIELDS, "prelabel seal")
    if (
        type(prelabel["schemaVersion"]) is not int
        or prelabel["schemaVersion"] != 1
        or prelabel["kind"] != PRELABEL_KIND
        or prelabel["profileId"] != PROFILE_ID
        or prelabel["status"] != "frozen-before-any-teacher-target-decode"
        or type(prelabel["componentRows"]) is not int
        or prelabel["componentRows"] != root_count
        or type(prelabel["targetFieldsDecodedAtSeal"]) is not int
        or prelabel["targetFieldsDecodedAtSeal"] != 0
        or type(prelabel["targetFieldsEmittedAtSeal"]) is not int
        or prelabel["targetFieldsEmittedAtSeal"] != 0
    ):
        raise ValueError("prelabel seal header/target boundary changed")
    _parse_timestamp(prelabel["createdUtc"], "prelabel createdUtc")
    expected_links = {
        "componentMap": identities["componentMap"],
        "targetFreeRouting": identities["targetFreeRouting"],
        "terminalClassifierLineage": identities["terminalClassifierLineage"],
        "priorForbiddenRegistry": identities["priorForbiddenRegistry"],
        "initializerManifest": identities["initializerManifest"],
    }
    for field, expected in expected_links.items():
        _same_identity(prelabel[field], expected, f"prelabel {field}")
    if not _type_exact_equal(
        prelabel["priorForbiddenCatalogs"], capsule["priorForbiddenCatalogs"]
    ):
        raise ValueError("prelabel prior-forbidden catalog list changed")
    for field in ("sourceRootManifest", "sourceChildrenManifest", "producer"):
        _verified_identity(prelabel[field], f"prelabel {field}")
    return prelabel, prelabel_identity


def _verify_terminal_and_selected_sources(
    identities: Mapping[str, Mapping[str, Any]],
    database: AuthorityDatabase,
    routed_children: int,
) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    terminal_module = _load_pinned("terminal").module
    routing_module = _load_pinned("routing").module
    lineage_path = Path(str(identities["terminalClassifierLineage"]["path"]))
    lineage = terminal_module.verify_terminal_lineage(lineage_path)
    if _identity(lineage_path, "terminal lineage") != identities["terminalClassifierLineage"]:
        raise ValueError("terminal lineage changed during rules replay")
    if (
        lineage.get("unclassifiedChildren") != 0
        or type(lineage.get("unclassifiedChildren")) is not int
        or lineage.get("errorTextAcceptedAsTerminal") is not False
        or type(lineage.get("terminalChildrenExcludedBeforeRouting")) is not int
        or lineage.get("terminalChildrenExcludedBeforeRouting") < 0
        or lineage.get("resultInformationRead") is not False
        or lineage.get("finalStageSeal") is not True
    ):
        raise ValueError("terminal lineage completion policy changed")
    roots_identity = _identity_shape(lineage["eligibleRoots"], "terminal eligible roots")
    children_identity = _identity_shape(
        lineage["eligibleChildren"], "terminal eligible children"
    )

    # Forbidden lookup is installed after registry replay below.  This pass
    # first proves that every selected root/child comes from the fully verified
    # terminal-safe inventory and stores its derived leakage identity.
    database.connection.executescript(
        """
        CREATE TABLE selected_positions (
            position_key TEXT PRIMARY KEY,
            exact_key TEXT NOT NULL,
            signatures_json TEXT NOT NULL
        ) WITHOUT ROWID;
        """
    )
    roots_reader = StableJsonl(
        Path(str(roots_identity["path"])),
        "terminal eligible roots",
        expected_identity=roots_identity,
    )
    with database.connection:
        for number, row in roots_reader:
            root_id = row.get("rootId")
            if type(root_id) is not str:
                continue
            selected = database.connection.execute(
                "SELECT source_root_id,source_group_id,phase,parent_side FROM routes "
                "WHERE root_id=?",
                (root_id,),
            ).fetchone()
            if selected is None:
                continue
            root = routing_module._root_from_row(row, f"terminal eligible roots:{number}")
            if (
                selected[0] != root.root_id
                or selected[1] != root.group_id
                or selected[2] != root.phase
                or selected[3] != root.side
            ):
                raise ValueError(f"selected root {root_id!r} differs from terminal source")
            database.connection.execute(
                "UPDATE routes SET terminal_seen=1 WHERE root_id=?", (root_id,)
            )
            database.connection.execute(
                "INSERT INTO selected_positions(position_key,exact_key,signatures_json) "
                "VALUES (?,?,?)",
                (
                    "root:" + root_id,
                    root.exact,
                    json.dumps(list(root.signatures), separators=(",", ":")),
                ),
            )
    if roots_reader.identity is None:
        raise ValueError("terminal eligible-root inventory was not read")

    children_reader = StableJsonl(
        Path(str(children_identity["path"])),
        "terminal eligible children",
        expected_identity=children_identity,
    )
    with database.connection:
        for number, row in children_reader:
            child_id = row.get("childId")
            if type(child_id) is not str:
                continue
            selected = database.connection.execute(
                "SELECT root_id,ofen FROM children WHERE child_id=?", (child_id,)
            ).fetchone()
            if selected is None:
                continue
            ofen = _normalized_ofen(row.get("childOfen"), f"terminal child:{number}")
            if row.get("rootId") != selected[0] or ofen != selected[1]:
                raise ValueError(
                    f"selected child {child_id!r} differs from terminal source"
                )
            exact, _, signatures = routing_module._leakage_keys(ofen)
            database.connection.execute(
                "UPDATE children SET terminal_seen=1 WHERE child_id=?", (child_id,)
            )
            database.connection.execute(
                "INSERT INTO selected_positions(position_key,exact_key,signatures_json) "
                "VALUES (?,?,?)",
                (
                    "child:" + child_id,
                    exact,
                    json.dumps(list(signatures), separators=(",", ":")),
                ),
            )
    if children_reader.identity is None:
        raise ValueError("terminal eligible-child inventory was not read")
    unseen_root = database.connection.execute(
        "SELECT root_id FROM routes WHERE terminal_seen<>1 LIMIT 1"
    ).fetchone()
    unseen_child = database.connection.execute(
        "SELECT child_id FROM children WHERE terminal_seen<>1 LIMIT 1"
    ).fetchone()
    selected_positions = int(
        database.connection.execute("SELECT count(*) FROM selected_positions").fetchone()[0]
    )
    root_count = int(database.connection.execute("SELECT count(*) FROM routes").fetchone()[0])
    if unseen_root or unseen_child or selected_positions != root_count + routed_children:
        raise ValueError("routing is not a subset of terminal-safe root/child outputs")
    return (
        lineage,
        {
            "lineage": dict(identities["terminalClassifierLineage"]),
            "routedChildren": routed_children,
            "terminalChildrenExcludedBeforeRouting": lineage[
                "terminalChildrenExcludedBeforeRouting"
            ],
            "unclassifiedChildren": 0,
            "errorTextAcceptedAsTerminal": False,
            "rulesSemanticsReplayed": True,
            "completionSemanticsVerified": True,
        },
        0,
        0,
    )


def _verify_forbidden(
    capsule: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
    lineage: Mapping[str, Any],
    database: AuthorityDatabase,
) -> dict[str, Any]:
    registry, registry_identity = _read_document(
        Path(str(identities["priorForbiddenRegistry"]["path"])),
        "prior-forbidden registry",
    )
    _same_identity(
        registry_identity, identities["priorForbiddenRegistry"],
        "prior-forbidden registry",
    )
    _exact_keys(registry, FORBIDDEN_REGISTRY_FIELDS, "prior-forbidden registry")
    if (
        type(registry["schemaVersion"]) is not int
        or registry["schemaVersion"] != 1
        or registry["kind"] != FORBIDDEN_REGISTRY_KIND
        or registry["profileId"] != PROFILE_ID
        or registry["status"] != "frozen-complete-prior-source-registry"
        or registry["requiredSourceIds"] != list(PRIOR_SOURCE_IDS)
        or type(registry["catalogs"]) is not list
        or not registry["catalogs"]
        or type(registry["targetFieldsDecodedAtSeal"]) is not int
        or registry["targetFieldsDecodedAtSeal"] != 0
        or registry["resultInformationRead"] is not False
        or registry["finalStageSeal"] is not True
    ):
        raise ValueError("prior-forbidden registry header changed")
    _parse_timestamp(registry["createdUtc"], "prior-forbidden registry createdUtc")
    _verified_identity(registry["producer"], "prior-forbidden registry producer")
    sources: list[str] = []
    manifests: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_inodes: set[tuple[int, int]] = set()
    for index, entry in enumerate(registry["catalogs"]):
        if not isinstance(entry, dict):
            raise ValueError(f"prior-forbidden registry catalog {index} is not an object")
        _exact_keys(
            entry,
            FORBIDDEN_REGISTRY_ENTRY_FIELDS,
            f"prior-forbidden registry catalog {index}",
        )
        covered = entry["coveredSourceIds"]
        if (
            type(covered) is not list
            or not covered
            or any(type(value) is not str or not value for value in covered)
            or len(set(covered)) != len(covered)
        ):
            raise ValueError("prior-forbidden source coverage changed")
        identity = _verified_identity(
            entry["manifest"], f"prior-forbidden registry manifest {index}"
        )
        path = _safe_existing(Path(str(identity["path"])))
        path_key = os.path.normcase(os.path.normpath(str(path)))
        info = os.lstat(path)
        inode = (info.st_dev, info.st_ino)
        if path_key in seen_paths or inode in seen_inodes:
            raise ValueError("prior-forbidden registry repeats a manifest path/inode")
        seen_paths.add(path_key)
        seen_inodes.add(inode)
        sources.extend(covered)
        manifests.append(identity)
    if sources != list(PRIOR_SOURCE_IDS) or not _type_exact_equal(
        manifests, capsule["priorForbiddenCatalogs"]
    ):
        raise ValueError("prior-forbidden registry coverage/catalog order changed")

    routing_module = _load_pinned("routing").module
    forbidden = routing_module._load_forbidden(
        [Path(str(identity["path"])) for identity in manifests]
    )
    try:
        if not _type_exact_equal(list(forbidden.manifests), manifests) or forbidden.positions <= 0:
            raise ValueError("prior-forbidden manifests differ after semantic replay")
        source_hashes = set(forbidden.source_artifact_sha256)
        source_overlap = len(
            {
                lineage["eligibleRoots"]["sha256"],
                lineage["eligibleChildren"]["sha256"],
            }
            & source_hashes
        )
        exact_overlap = 0
        conservative_overlap = 0
        for exact_key, signatures_json in database.connection.execute(
            "SELECT exact_key,signatures_json FROM selected_positions ORDER BY position_key"
        ):
            if exact_key in forbidden.exact:
                exact_overlap += 1
            signatures = json.loads(signatures_json)
            if any(signature in forbidden.signatures for signature in signatures):
                conservative_overlap += 1
        if exact_overlap or conservative_overlap or source_overlap:
            raise ValueError(
                "current decision-v3 positions/source artifacts overlap prior-forbidden data"
            )
        return {
            "catalogs": [dict(value) for value in manifests],
            "registry": dict(registry_identity),
            "requiredSourceIds": list(PRIOR_SOURCE_IDS),
            "catalogPositions": int(forbidden.positions),
            "manifestsSemanticallyReplayed": True,
            "exactPositionOverlaps": 0,
            "conservativeSignatureOverlaps": 0,
            "sourceArtifactOverlaps": 0,
        }
    finally:
        forbidden.close()


def _hce_order_rows(database: AuthorityDatabase) -> Iterator[dict[str, str]]:
    for child_id, ofen in database.connection.execute(
        "SELECT child_id,ofen FROM children ORDER BY child_id"
    ):
        yield {"childId": str(child_id), "normalizedChildOfen": str(ofen)}


def _run_hce_replay(
    *,
    engine_identity: Mapping[str, Any],
    runner_identity: Mapping[str, Any],
    transcript_identity: Mapping[str, Any],
    database: AuthorityDatabase,
    routed_children: int,
) -> None:
    engine = Path(str(engine_identity["path"]))
    runner = Path(str(runner_identity["path"]))
    before_engine = _identity(engine, "static-HCE engine")
    before_runner = _identity(runner, "static-HCE runner")
    if before_engine != dict(engine_identity) or before_runner != dict(runner_identity):
        raise ValueError("static-HCE executable/runner changed before replay")
    with tempfile.TemporaryDirectory(
        prefix="omega-decision-v3-hce-replay-"
    ) as temporary_name:
        temporary = Path(temporary_name)
        input_path = temporary / "input.ofen"
        output_path = temporary / "stdout.txt"
        error_path = temporary / "stderr.txt"
        with input_path.open("xb") as stream:
            for _, ofen in database.connection.execute(
                "SELECT child_id,ofen FROM children ORDER BY child_id"
            ):
                stream.write(str(ofen).encode("ascii") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        command = [
            str(engine),
            "-I",
            "-B",
            str(runner),
            "--evaluate-handcrafted-stream",
        ]
        with input_path.open("rb") as stdin, output_path.open("xb") as stdout, error_path.open("xb") as stderr:
            completed = subprocess.run(
                command,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                cwd=runner.parent,
                env={},
                timeout=max(60, routed_children // 4),
                check=False,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        if completed.returncode != 0:
            raise ValueError("pinned static-HCE evaluator replay failed")
        if error_path.stat().st_size != 0:
            raise ValueError("pinned static-HCE evaluator replay emitted stderr")

        transcript_reader = StableJsonl(
            Path(str(transcript_identity["path"])),
            "static-HCE transcript",
            expected_identity=transcript_identity,
        )
        expected = database.connection.execute(
            "SELECT child_id FROM children ORDER BY child_id"
        )
        output_count = 0
        with output_path.open("rb") as replay:
            for (number, row), (expected_child,) in zip(
                transcript_reader, expected, strict=True
            ):
                label = f"static-HCE transcript:{number}"
                _exact_keys(row, HCE_FIELDS, label)
                score = row["handcraftedCpChildStm"]
                if (
                    type(row["schemaVersion"]) is not int
                    or row["schemaVersion"] != 1
                    or row["kind"] != HCE_ROW_KIND
                    or row["profileId"] != PROFILE_ID
                    or row["childId"] != expected_child
                    or type(score) is not int
                    or isinstance(score, bool)
                    or not -SCORE_LIMIT_CP <= score <= SCORE_LIMIT_CP
                ):
                    raise ValueError(f"{label} schema/order/score changed")
                line = replay.readline(1025)
                if not line or len(line) > 1024:
                    raise ValueError("static-HCE evaluator replay row is absent/oversized")
                try:
                    text = line.decode("ascii").rstrip("\r\n")
                except UnicodeDecodeError as error:
                    raise ValueError("static-HCE evaluator emitted non-ASCII") from error
                if _INTEGER.fullmatch(text) is None:
                    raise ValueError("static-HCE evaluator emitted a non-integer")
                replayed = int(text)
                if not -SCORE_LIMIT_CP <= replayed <= SCORE_LIMIT_CP or replayed != score:
                    raise ValueError("static-HCE transcript differs from fresh replay")
                output_count += 1
            if replay.read(1):
                raise ValueError("static-HCE evaluator emitted extra rows")
        if (
            transcript_reader.identity is None
            or transcript_reader.rows != routed_children
            or output_count != routed_children
        ):
            raise ValueError("static-HCE transcript/replay coverage changed")
    if (
        _identity(engine, "static-HCE engine") != before_engine
        or _identity(runner, "static-HCE runner") != before_runner
    ):
        raise ValueError("static-HCE executable/runner changed during replay")


def _verify_static_hce(
    capsule: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
    prelabel: Mapping[str, Any],
    database: AuthorityDatabase,
    routed_children: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    options, options_identity = _read_document(
        Path(str(identities["staticHceOptions"]["path"])), "static-HCE options"
    )
    _same_identity(options_identity, identities["staticHceOptions"], "static-HCE options")
    _exact_keys(options, HCE_OPTIONS_FIELDS, "static-HCE options")
    if not _type_exact_equal(options, _hce_options_document()):
        raise ValueError("static-HCE options differ from the fixed contract")
    pinned_runner = _load_pinned("evaluatorRunner").identity
    _same_identity(
        identities["staticHceRunner"], pinned_runner, "static-HCE evaluator runner"
    )

    claim, claim_identity = _read_document(
        Path(str(identities["preTargetHceClaim"]["path"])),
        "pre-target HCE claim",
    )
    _same_identity(claim_identity, identities["preTargetHceClaim"], "HCE claim")
    _exact_keys(claim, HCE_CLAIM_FIELDS, "pre-target HCE claim")
    expected_claim_links = {
        "prelabelSeal": identities["prelabelSeal"],
        "targetFreeRouting": identities["targetFreeRouting"],
        "engine": identities["staticHceEngine"],
        "runner": identities["staticHceRunner"],
        "options": identities["staticHceOptions"],
    }
    if (
        type(claim["schemaVersion"]) is not int
        or claim["schemaVersion"] != 1
        or claim["kind"] != HCE_CLAIM_KIND
        or claim["profileId"] != PROFILE_ID
        or claim["status"] != "claimed-before-teacher-and-target-decode"
        or claim["plannedTranscriptPath"] != identities["staticHceTranscript"]["path"]
        or type(claim["targetRowsDecodedAtClaim"]) is not int
        or claim["targetRowsDecodedAtClaim"] != 0
        or type(claim["targetFieldsDecodedAtClaim"]) is not int
        or claim["targetFieldsDecodedAtClaim"] != 0
        or claim["resultInformationRead"] is not False
        or any(
            not _type_exact_equal(claim[field], value)
            for field, value in expected_claim_links.items()
        )
    ):
        raise ValueError("pre-target HCE claim changed")
    claim_created = _parse_timestamp(claim["createdUtc"], "HCE claim createdUtc")

    completion, completion_identity = _read_document(
        Path(str(identities["preTargetHceCompletion"]["path"])),
        "pre-target HCE completion",
    )
    _same_identity(
        completion_identity, identities["preTargetHceCompletion"], "HCE completion"
    )
    _exact_keys(completion, HCE_COMPLETION_FIELDS, "pre-target HCE completion")
    hce_order_sha256 = _stream_list_digest(_hce_order_rows(database))
    expected_completion_links = {
        "claim": identities["preTargetHceClaim"],
        "prelabelSeal": identities["prelabelSeal"],
        "targetFreeRouting": identities["targetFreeRouting"],
        "engine": identities["staticHceEngine"],
        "runner": identities["staticHceRunner"],
        "options": identities["staticHceOptions"],
        "transcript": identities["staticHceTranscript"],
    }
    completion_created = _parse_timestamp(
        completion["createdUtc"], "HCE completion createdUtc"
    )
    if (
        type(completion["schemaVersion"]) is not int
        or completion["schemaVersion"] != 1
        or completion["kind"] != HCE_COMPLETION_KIND
        or completion["profileId"] != PROFILE_ID
        or completion["status"] != "completed-before-teacher-and-target-decode"
        or completion["inputOrderSha256"] != hce_order_sha256
        or type(completion["rows"]) is not int
        or completion["rows"] != routed_children
        or completion["perspective"] != STATIC_HCE_PERSPECTIVE
        or type(completion["targetRowsDecodedAtCompletion"]) is not int
        or completion["targetRowsDecodedAtCompletion"] != 0
        or type(completion["targetFieldsDecodedAtCompletion"]) is not int
        or completion["targetFieldsDecodedAtCompletion"] != 0
        or completion["resultInformationRead"] is not False
        or completion["finalStageSeal"] is not True
        or completion_created <= claim_created
        or any(
            not _type_exact_equal(completion[field], value)
            for field, value in expected_completion_links.items()
        )
    ):
        raise ValueError("pre-target HCE completion changed")
    if claim_created <= _parse_timestamp(prelabel["createdUtc"], "prelabel createdUtc"):
        raise ValueError("pre-target HCE claim does not follow prelabel seal")
    _run_hce_replay(
        engine_identity=identities["staticHceEngine"],
        runner_identity=identities["staticHceRunner"],
        transcript_identity=identities["staticHceTranscript"],
        database=database,
        routed_children=routed_children,
    )
    return claim, completion, {
        "claim": dict(identities["preTargetHceClaim"]),
        "completion": dict(identities["preTargetHceCompletion"]),
        "teacherClaim": dict(identities["teacherClaim"]),
        "prelabelSeal": dict(identities["prelabelSeal"]),
        "targetFreeRouting": dict(identities["targetFreeRouting"]),
        "engine": dict(identities["staticHceEngine"]),
        "runner": dict(identities["staticHceRunner"]),
        "options": dict(identities["staticHceOptions"]),
        "transcript": dict(identities["staticHceTranscript"]),
        "inputOrderSha256": completion["inputOrderSha256"],
        "rows": routed_children,
        "perspective": STATIC_HCE_PERSPECTIVE,
        "freshReplayMatches": True,
        "completedBeforeTeacherClaim": True,
        "semanticsReplayed": True,
    }


def _verify_teacher_claim(
    capsule: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
    route_digest: str,
    hce_completion: Mapping[str, Any],
    teacher_module: types.ModuleType,
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    claim, claim_identity = _read_document(
        Path(str(identities["teacherClaim"]["path"])), "teacher claim"
    )
    _same_identity(claim_identity, identities["teacherClaim"], "teacher claim")
    _exact_keys(claim, TEACHER_CLAIM_FIELDS, "teacher claim")
    _same_identity(identities["teacherRunner"], _load_pinned("teacher").identity, "teacher runner")
    options, options_identity = _read_document(
        Path(str(identities["teacherOptions"]["path"])), "teacher options"
    )
    _same_identity(options_identity, identities["teacherOptions"], "teacher options")
    if not _type_exact_equal(options, teacher_module.teacher_options_document()):
        raise ValueError("teacher options differ from the fixed contract")
    expected_links = {
        "prelabelSeal": identities["prelabelSeal"],
        "targetFreeRouting": identities["targetFreeRouting"],
        "componentMap": identities["componentMap"],
        "engine": identities["teacherEngine"],
        "runner": identities["teacherRunner"],
        "options": identities["teacherOptions"],
        "budgets": TEACHER_BUDGETS,
        "inputOrderSha256": route_digest,
        "plannedProjectionProducer": identities["plannedProjectionProducer"],
        "plannedProjectedCorpusPath": capsule["plannedProjectedCorpusPath"],
        "plannedProjectionManifestPath": capsule["plannedProjectionManifestPath"],
        "preTargetHceCompletion": identities["preTargetHceCompletion"],
    }
    if (
        type(claim["schemaVersion"]) is not int
        or claim["schemaVersion"] != 1
        or claim["kind"] != TEACHER_CLAIM_KIND
        or claim["profileId"] != PROFILE_ID
        or claim["status"] != "claimed-before-first-teacher-target-decode"
        or type(claim["targetRowsDecodedAtClaim"]) is not int
        or claim["targetRowsDecodedAtClaim"] != 0
        or type(claim["targetFieldsDecodedAtClaim"]) is not int
        or claim["targetFieldsDecodedAtClaim"] != 0
        or claim["resultInformationRead"] is not False
        or any(
            not _type_exact_equal(claim[field], value)
            for field, value in expected_links.items()
        )
    ):
        raise ValueError("teacher claim differs from frozen pre-target plan")
    created = _parse_timestamp(claim["createdUtc"], "teacher claim createdUtc")
    if created <= _parse_timestamp(hce_completion["createdUtc"], "HCE completion createdUtc"):
        raise ValueError("teacher claim does not follow completed static HCE")
    context = teacher_module.ClaimContext(
        Path(str(claim_identity["path"])), claim_identity, claim, (), {}
    )
    return claim, claim_identity, context


def _next_batch(
    database: AuthorityDatabase,
    last_root: str,
    last_child: str,
    limit: int = 2048,
) -> list[tuple[str, str, str]]:
    return [
        (str(root_id), str(child_id), str(ofen))
        for root_id, child_id, ofen in database.connection.execute(
            "SELECT c.root_id,c.child_id,c.ofen FROM children c "
            "LEFT JOIN stage_success s ON s.child_id=c.child_id "
            "WHERE s.child_id IS NULL AND "
            "(c.root_id>? OR (c.root_id=? AND c.child_id>?)) "
            "ORDER BY c.root_id,c.child_id LIMIT ?",
            (last_root, last_root, last_child, limit),
        )
    ]


def _replay_teacher_ledger(
    *,
    identities: Mapping[str, Mapping[str, Any]],
    claim: Mapping[str, Any],
    claim_context: Any,
    database: AuthorityDatabase,
    routed_children: int,
    route_digest: str,
    teacher_module: types.ModuleType,
) -> tuple[dict[str, Any], int, int, datetime]:
    reader = StableJsonl(
        Path(str(identities["teacherAttemptLedger"]["path"])),
        "teacher attempt ledger",
        expected_identity=identities["teacherAttemptLedger"],
    )
    iterator = iter(reader)
    sequence = 0
    deep_attempts = 0
    latest = _parse_timestamp(claim["createdUtc"], "teacher claim createdUtc")
    for stage in STAGES:
        with database.connection:
            database.connection.execute("DELETE FROM stage_success")
        complete = False
        for attempt in range(1, TEACHER_BUDGETS["maximumAttempts"] + 1):
            last_root = ""
            last_child = ""
            wave_rows = 0
            while True:
                batch = _next_batch(database, last_root, last_child)
                if not batch:
                    break
                with database.connection:
                    for root_id, child_id, ofen in batch:
                        try:
                            _, row = next(iterator)
                        except StopIteration as error:
                            raise ValueError(
                                f"teacher attempt ledger ended during {stage} attempt {attempt}"
                            ) from error
                        child = teacher_module.Child(
                            root_id,
                            child_id,
                            ofen,
                            database.connection.execute(
                                "SELECT phase FROM routes WHERE root_id=?", (root_id,)
                            ).fetchone()[0],
                            database.connection.execute(
                                "SELECT parent_side FROM routes WHERE root_id=?", (root_id,)
                            ).fetchone()[0],
                        )
                        teacher_module._validate_attempt(
                            row,
                            context=claim_context,
                            child=child,
                            stage=stage,
                            attempt=attempt,
                            sequence=sequence,
                        )
                        completed = _parse_timestamp(
                            row["completedUtc"], f"teacher attempt {sequence} completedUtc"
                        )
                        latest = max(latest, completed)
                        if row["outcome"] == "success":
                            database.connection.execute(
                                "INSERT INTO stage_success(child_id) VALUES (?)",
                                (child_id,),
                            )
                            if stage == "deep":
                                database.connection.execute(
                                    "UPDATE children SET deep_score=? WHERE child_id=?",
                                    (row["scoreCpChildStm"], child_id),
                                )
                        sequence += 1
                        if stage == "deep":
                            deep_attempts += 1
                        wave_rows += 1
                        last_root, last_child = root_id, child_id
            successful = int(
                database.connection.execute("SELECT count(*) FROM stage_success").fetchone()[0]
            )
            if successful == routed_children:
                complete = True
                break
            if wave_rows == 0:
                raise ValueError(f"teacher {stage} retry wave is empty before coverage")
        if not complete:
            raise ValueError(f"teacher {stage} stage exhausted without full coverage")
    try:
        next(iterator)
    except StopIteration:
        pass
    else:
        raise ValueError("teacher ledger has records after complete deep coverage")
    if reader.identity is None or reader.rows != sequence:
        raise ValueError("teacher ledger identity/count changed")
    missing_score = database.connection.execute(
        "SELECT child_id FROM children WHERE deep_score IS NULL LIMIT 1"
    ).fetchone()
    if missing_score is not None:
        raise ValueError("teacher ledger has unresolved deep scores")

    deep_scores_sha256 = _stream_list_digest(
        {"childId": str(child_id), "scoreCpChildStm": int(score)}
        for child_id, score in database.connection.execute(
            "SELECT child_id,deep_score FROM children ORDER BY child_id"
        )
    )
    receipt, receipt_identity = _read_document(
        Path(str(identities["teacherAttemptLedgerCompletion"]["path"])),
        "teacher attempt-ledger completion",
    )
    _same_identity(
        receipt_identity,
        identities["teacherAttemptLedgerCompletion"],
        "teacher attempt-ledger completion",
    )
    _exact_keys(receipt, LEDGER_COMPLETION_FIELDS, "teacher attempt-ledger completion")
    receipt_created = _parse_timestamp(
        receipt["createdUtc"], "teacher attempt-ledger completion createdUtc"
    )
    expected = {
        "schemaVersion": 1,
        "kind": LEDGER_COMPLETION_KIND,
        "profileId": PROFILE_ID,
        "status": "complete-exact-child-coverage",
        "createdUtc": receipt["createdUtc"],
        "claim": dict(identities["teacherClaim"]),
        "attemptLedger": dict(identities["teacherAttemptLedger"]),
        "budgets": dict(TEACHER_BUDGETS),
        "inputOrderSha256": route_digest,
        "routedChildren": routed_children,
        "attemptRecords": sequence,
        "successfulChildren": routed_children,
        "rejectedChildren": 0,
        "unresolvedChildren": 0,
        "deepScoresSha256": deep_scores_sha256,
        "resultInformationRead": False,
        "finalStageSeal": True,
    }
    if not _type_exact_equal(receipt, expected) or receipt_created <= latest:
        raise ValueError("teacher attempt-ledger completion differs from raw replay")
    return receipt, sequence, deep_attempts, receipt_created


def _root_rows(database: AuthorityDatabase) -> Iterator[tuple[Any, ...]]:
    yield from database.connection.execute(
        "SELECT r.root_id,r.phase,r.parent_side,c.component_id,c.split "
        "FROM routes r JOIN components c ON c.root_id=r.root_id ORDER BY r.root_id"
    )


def _child_rows_for_root(
    database: AuthorityDatabase, root_id: str
) -> list[tuple[str, str, int]]:
    return [
        (str(child_id), str(ofen), int(score))
        for child_id, ofen, score in database.connection.execute(
            "SELECT child_id,ofen,deep_score FROM children WHERE root_id=? "
            "ORDER BY child_id",
            (root_id,),
        )
    ]


def _teacher_expected_rows(
    database: AuthorityDatabase,
) -> Iterator[dict[str, Any]]:
    for root_id, phase, parent_side, _, _ in _root_rows(database):
        children = _child_rows_for_root(database, str(root_id))
        if len(children) != 4:
            raise ValueError(f"teacher root {root_id!r} no longer has four children")
        ranked = sorted(children, key=lambda item: (item[2], item[0]))
        ranks = {child_id: index + 1 for index, (child_id, _, _) in enumerate(ranked)}
        root_scores = {child_id: -score for child_id, _, score in children}
        best = max(root_scores.values())
        for child_id, ofen, child_score in children:
            root_score = -child_score
            yield {
                "schemaVersion": 1,
                "kind": TEACHER_LABEL_KIND,
                "profileId": PROFILE_ID,
                "rootId": str(root_id),
                "childId": child_id,
                "childOfen": ofen,
                "phase": str(phase),
                "parentSideToMove": str(parent_side),
                "deepRank": ranks[child_id],
                "deepRegretCp": best - root_score,
                "deepScoreCpRoot": root_score,
                "deepScoreCpChildStm": child_score,
            }


def _verify_teacher_labels(
    identities: Mapping[str, Mapping[str, Any]],
    database: AuthorityDatabase,
    routed_children: int,
) -> dict[str, Any]:
    reader = StableJsonl(
        Path(str(identities["teacherLabels"]["path"])),
        "teacher labels",
        expected_identity=identities["teacherLabels"],
    )
    actual = iter(reader)
    count = 0
    for expected in _teacher_expected_rows(database):
        try:
            number, row = next(actual)
        except StopIteration as error:
            raise ValueError("teacher labels ended before complete child coverage") from error
        _exact_keys(row, TEACHER_LABEL_FIELDS, f"teacher labels:{number}")
        if not _type_exact_equal(row, expected):
            raise ValueError(
                f"teacher labels:{number} differs from deep-attempt replay"
            )
        count += 1
    try:
        next(actual)
    except StopIteration:
        pass
    else:
        raise ValueError("teacher labels contain extra rows")
    if reader.identity is None or count != routed_children:
        raise ValueError("teacher label identity/coverage changed")
    return dict(reader.identity)


def _projected_expected_rows(
    database: AuthorityDatabase,
) -> Iterator[dict[str, Any]]:
    teacher_rows = _teacher_expected_rows(database)
    for teacher in teacher_rows:
        component = database.connection.execute(
            "SELECT component_id,split FROM components WHERE root_id=?",
            (teacher["rootId"],),
        ).fetchone()
        if component is None:
            raise ValueError("projected root has no component authority")
        yield {
            "schemaVersion": 1,
            "kind": PROJECTED_LABEL_KIND,
            "rootId": teacher["rootId"],
            "leakageComponentId": str(component[0]),
            "split": str(component[1]),
            "childId": teacher["childId"],
            "childOfen": teacher["childOfen"],
            "phase": teacher["phase"],
            "parentSideToMove": teacher["parentSideToMove"],
            "deepRank": teacher["deepRank"],
            "deepRegretCp": teacher["deepRegretCp"],
            "deepScoreCpRoot": teacher["deepScoreCpRoot"],
            "deepScoreCpChildStm": teacher["deepScoreCpChildStm"],
        }


def _verify_projected_corpus(
    identities: Mapping[str, Mapping[str, Any]],
    database: AuthorityDatabase,
    routed_children: int,
) -> dict[str, Any]:
    reader = StableJsonl(
        Path(str(identities["projectedCorpus"]["path"])),
        "projected corpus",
        expected_identity=identities["projectedCorpus"],
    )
    actual = iter(reader)
    count = 0
    for expected in _projected_expected_rows(database):
        try:
            number, row = next(actual)
        except StopIteration as error:
            raise ValueError("projected corpus ended before complete coverage") from error
        _exact_keys(row, PROJECTED_LABEL_FIELDS, f"projected corpus:{number}")
        if not _type_exact_equal(row, expected):
            raise ValueError(
                f"projected corpus:{number} is not the exact teacher/component projection"
            )
        count += 1
    try:
        next(actual)
    except StopIteration:
        pass
    else:
        raise ValueError("projected corpus contains extra rows")
    if reader.identity is None or count != routed_children:
        raise ValueError("projected corpus identity/coverage changed")
    return dict(reader.identity)


def _root_record(
    database: AuthorityDatabase,
    root_id: str,
    phase: str,
    parent_side: str,
    component_id: str,
    split: str,
) -> dict[str, Any]:
    child_ids = [
        str(row[0])
        for row in database.connection.execute(
            "SELECT child_id FROM children WHERE root_id=? ORDER BY child_id",
            (root_id,),
        )
    ]
    if len(child_ids) != 4:
        raise ValueError(f"root inventory {root_id!r} changed")
    return {
        "rootId": root_id,
        "leakageComponentId": component_id,
        "split": split,
        "phase": phase,
        "parentSideToMove": parent_side,
        "childIds": child_ids,
    }


def _root_inventory(
    database: AuthorityDatabase, split: str
) -> dict[str, Any]:
    count = int(
        database.connection.execute(
            "SELECT count(*) FROM components WHERE split=?", (split,)
        ).fetchone()[0]
    )

    def records() -> Iterator[dict[str, Any]]:
        for root_id, phase, parent_side, component_id, actual_split in _root_rows(database):
            if actual_split == split:
                yield _root_record(
                    database,
                    str(root_id),
                    str(phase),
                    str(parent_side),
                    str(component_id),
                    str(actual_split),
                )

    return {"roots": count, "children": count * 4, "sha256": _stream_list_digest(records())}


def _cell_inventory(
    database: AuthorityDatabase, split: str, phase: str, side: str
) -> dict[str, Any]:
    rows = database.connection.execute(
        "SELECT r.root_id FROM routes r JOIN components c ON c.root_id=r.root_id "
        "WHERE c.split=? AND r.phase=? AND r.parent_side=? ORDER BY r.root_id",
        (split, phase, side),
    )
    count = 0

    def values() -> Iterator[str]:
        nonlocal count
        for (root_id,) in rows:
            count += 1
            yield str(root_id)

    digest = _stream_list_digest(values())
    return {"roots": count, "sha256": digest}


def _inventories(database: AuthorityDatabase) -> tuple[dict[str, Any], dict[str, Any]]:
    root_inventories = {
        split: _root_inventory(database, split) for split in SPLITS
    }
    phase_side = {
        split: {
            f"{phase}:{side}": _cell_inventory(database, split, phase, side)
            for phase in PHASES
            for side in SIDES
        }
        for split in SPLITS
    }
    return root_inventories, phase_side


def _verify_fixed_routing_quota_inventories(
    root_inventories: Mapping[str, Any],
    phase_side_inventories: Mapping[str, Any],
) -> None:
    """Require the exact preregistered routing quota in every split/cell.

    Divisibility by the trainer batch size is not an authority statement.  The
    numeric runner and result-blind metric protocol were reviewed for exactly
    512 train roots and 128 validation/held-out roots in each of the eight
    phase-by-root-side cells, so both totals and individual cells are frozen.
    """

    if set(root_inventories) != set(SPLITS) or set(
        phase_side_inventories
    ) != set(SPLITS):
        raise ValueError("routing quota split inventory changed")
    for split in SPLITS:
        quota = ROUTING_QUOTAS_PER_PHASE_SIDE[split]
        expected_roots = quota * len(CELLS)
        root_inventory = root_inventories[split]
        cells = phase_side_inventories[split]
        if (
            not isinstance(root_inventory, Mapping)
            or set(root_inventory) != {"roots", "children", "sha256"}
            or type(root_inventory["roots"]) is not int
            or root_inventory["roots"] != expected_roots
            or type(root_inventory["children"]) is not int
            or root_inventory["children"] != expected_roots * CHILDREN_PER_ROOT
            or type(root_inventory["sha256"]) is not str
            or _SHA256.fullmatch(root_inventory["sha256"]) is None
            or not isinstance(cells, Mapping)
            or set(cells) != set(CELLS)
        ):
            raise ValueError(f"routing quota root inventory changed for {split}")
        for cell in CELLS:
            inventory = cells[cell]
            if (
                not isinstance(inventory, Mapping)
                or set(inventory) != {"roots", "sha256"}
                or type(inventory["roots"]) is not int
                or inventory["roots"] != quota
                or type(inventory["sha256"]) is not str
                or _SHA256.fullmatch(inventory["sha256"]) is None
            ):
                raise ValueError(
                    f"routing quota phase/side inventory changed for {split}/{cell}"
                )


def _verify_teacher_projection_completion(
    *,
    capsule: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
    prelabel: Mapping[str, Any],
    hce_claim: Mapping[str, Any],
    hce_completion: Mapping[str, Any],
    claim: Mapping[str, Any],
    route_digest: str,
    database: AuthorityDatabase,
    routed_children: int,
    total_attempts: int,
    deep_attempts: int,
    receipt_created: datetime,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], datetime]:
    labels_identity = _verify_teacher_labels(identities, database, routed_children)
    _same_identity(labels_identity, identities["teacherLabels"], "teacher labels")

    teacher_manifest, teacher_manifest_identity = _read_document(
        Path(str(identities["teacherManifest"]["path"])), "teacher manifest"
    )
    _same_identity(
        teacher_manifest_identity, identities["teacherManifest"], "teacher manifest"
    )
    _exact_keys(teacher_manifest, TEACHER_MANIFEST_FIELDS, "teacher manifest")
    teacher_manifest_created = _parse_timestamp(
        teacher_manifest["createdUtc"], "teacher manifest createdUtc"
    )
    expected_teacher_manifest = {
        "schemaVersion": 1,
        "kind": TEACHER_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "complete-after-frozen-prelabel-authority",
        "createdUtc": teacher_manifest["createdUtc"],
        "prelabelSeal": dict(identities["prelabelSeal"]),
        "componentMap": dict(identities["componentMap"]),
        "labels": dict(identities["teacherLabels"]),
        "producer": dict(identities["teacherRunner"]),
        "rows": routed_children,
        "childrenPerRoot": 4,
    }
    if (
        not _type_exact_equal(teacher_manifest, expected_teacher_manifest)
        or teacher_manifest_created <= receipt_created
    ):
        raise ValueError("teacher manifest differs from ledger replay/chronology")

    projected_identity = _verify_projected_corpus(
        identities, database, routed_children
    )
    _same_identity(projected_identity, identities["projectedCorpus"], "projected corpus")
    root_inventories, phase_side_inventories = _inventories(database)
    label_manifest, label_manifest_identity = _read_document(
        Path(str(identities["labelManifest"]["path"])), "projection manifest"
    )
    _same_identity(label_manifest_identity, identities["labelManifest"], "projection manifest")
    _exact_keys(label_manifest, PROJECTED_MANIFEST_FIELDS, "projection manifest")
    label_manifest_created = _parse_timestamp(
        label_manifest["createdUtc"], "projection manifest createdUtc"
    )
    expected_label_manifest = {
        "schemaVersion": 1,
        "kind": PROJECTED_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "sealed-exact-projected-label-authority",
        "createdUtc": label_manifest["createdUtc"],
        "corpus": dict(identities["projectedCorpus"]),
        "componentMap": dict(identities["componentMap"]),
        "rows": routed_children,
        "childrenPerRoot": 4,
        "rootInventories": root_inventories,
        "phaseSideInventories": phase_side_inventories,
        "upstreamPrelabelSeal": dict(identities["prelabelSeal"]),
        "upstreamTeacherLabels": dict(identities["teacherLabels"]),
        "upstreamTeacherManifest": dict(identities["teacherManifest"]),
        "projectionProducer": dict(identities["plannedProjectionProducer"]),
    }
    if (
        not _type_exact_equal(label_manifest, expected_label_manifest)
        or label_manifest_created <= teacher_manifest_created
    ):
        raise ValueError("projection manifest differs from exact projection/chronology")

    completion, completion_identity = _read_document(
        Path(str(identities["teacherCompletion"]["path"])), "teacher completion"
    )
    _same_identity(completion_identity, identities["teacherCompletion"], "teacher completion")
    _exact_keys(completion, TEACHER_COMPLETION_FIELDS, "teacher completion")
    completion_created = _parse_timestamp(
        completion["createdUtc"], "teacher completion createdUtc"
    )
    expected_completion = {
        "schemaVersion": 1,
        "kind": TEACHER_COMPLETION_KIND,
        "profileId": PROFILE_ID,
        "status": "completed-exact-claimed-teacher-and-projection",
        "createdUtc": completion["createdUtc"],
        "claim": dict(identities["teacherClaim"]),
        "prelabelSeal": dict(identities["prelabelSeal"]),
        "engine": dict(identities["teacherEngine"]),
        "runner": dict(identities["teacherRunner"]),
        "options": dict(identities["teacherOptions"]),
        "budgets": dict(TEACHER_BUDGETS),
        "inputOrderSha256": route_digest,
        "attemptLedger": dict(identities["teacherAttemptLedger"]),
        "attemptLedgerCompletion": dict(identities["teacherAttemptLedgerCompletion"]),
        "teacherLabels": dict(identities["teacherLabels"]),
        "teacherManifest": dict(identities["teacherManifest"]),
        "projectionProducer": dict(identities["projectionProducer"]),
        "projectedCorpus": dict(identities["projectedCorpus"]),
        "labelManifest": dict(identities["labelManifest"]),
        "finalStageSeal": True,
        "resultInformationRead": False,
    }
    if (
        not _type_exact_equal(completion, expected_completion)
        or completion_created <= max(
            receipt_created, teacher_manifest_created, label_manifest_created
        )
    ):
        raise ValueError("teacher completion differs from replayed closure")

    hce_manifest, hce_manifest_identity = _read_document(
        Path(str(identities["staticHceManifest"]["path"])), "static-HCE manifest"
    )
    _same_identity(hce_manifest_identity, identities["staticHceManifest"], "static-HCE manifest")
    _exact_keys(hce_manifest, HCE_MANIFEST_FIELDS, "static-HCE manifest")
    hce_manifest_created = _parse_timestamp(
        hce_manifest["createdUtc"], "static-HCE manifest createdUtc"
    )
    expected_hce_manifest = {
        "schemaVersion": 1,
        "kind": HCE_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "sealed-exact-static-hce-projection",
        "createdUtc": hce_manifest["createdUtc"],
        "labelManifest": dict(identities["labelManifest"]),
        "corpus": dict(identities["projectedCorpus"]),
        "componentMap": dict(identities["componentMap"]),
        "projection": dict(identities["staticHceTranscript"]),
        "engine": dict(identities["staticHceEngine"]),
        "runner": dict(identities["staticHceRunner"]),
        "options": dict(identities["staticHceOptions"]),
        "perspective": STATIC_HCE_PERSPECTIVE,
        "rows": routed_children,
        "rootInventories": root_inventories,
    }
    if (
        not _type_exact_equal(hce_manifest, expected_hce_manifest)
        or hce_manifest_created <= label_manifest_created
    ):
        raise ValueError("static-HCE manifest differs from fresh replay authority")

    if (
        not _type_exact_equal(capsule["rootInventories"], root_inventories)
        or not _type_exact_equal(
            capsule["phaseSideInventories"], phase_side_inventories
        )
    ):
        raise ValueError("capsule structural inventories changed")
    capsule_created = _parse_timestamp(capsule["createdUtc"], "capsule createdUtc")
    prelabel_created = _parse_timestamp(prelabel["createdUtc"], "prelabel createdUtc")
    hce_claim_created = _parse_timestamp(hce_claim["createdUtc"], "HCE claim createdUtc")
    hce_completion_created = _parse_timestamp(
        hce_completion["createdUtc"], "HCE completion createdUtc"
    )
    teacher_claim_created = _parse_timestamp(claim["createdUtc"], "teacher claim createdUtc")
    if not (
        prelabel_created
        < hce_claim_created
        < hce_completion_created
        < teacher_claim_created
        < completion_created
        < capsule_created
    ):
        raise ValueError("prelabel/HCE/teacher/capsule chronology changed")
    if not (
        teacher_claim_created
        < teacher_manifest_created
        < label_manifest_created
        < completion_created
    ):
        raise ValueError("teacher target/projection chronology changed")
    if not label_manifest_created < hce_manifest_created < capsule_created:
        raise ValueError("static-HCE/capsule chronology changed")

    teacher_authority = {
        "claim": dict(identities["teacherClaim"]),
        "attemptLedger": dict(identities["teacherAttemptLedger"]),
        "attemptLedgerCompletion": dict(identities["teacherAttemptLedgerCompletion"]),
        "completion": dict(identities["teacherCompletion"]),
        "budgets": dict(TEACHER_BUDGETS),
        "routedChildren": routed_children,
        # The trainer's bounded inventory is per deep stage.  All shallow and
        # deep raw attempts were nevertheless replayed above.
        "attemptRecords": deep_attempts,
        "successfulChildren": routed_children,
        "rejectedChildren": 0,
        "unresolvedChildren": 0,
        "semanticsReplayed": True,
    }
    projection_authority = {
        "plannedProducer": dict(identities["plannedProjectionProducer"]),
        "plannedCorpusPath": capsule["plannedProjectedCorpusPath"],
        "plannedManifestPath": capsule["plannedProjectionManifestPath"],
        "actualProducer": dict(identities["projectionProducer"]),
        "actualCorpus": dict(identities["projectedCorpus"]),
        "actualManifest": dict(identities["labelManifest"]),
        "producerMatches": True,
        "pathsMatch": True,
        "semanticsReplayed": True,
    }
    return teacher_authority, projection_authority, label_manifest, completion_created


def verify_capsule(
    capsule_path: Path,
    options_path: Path,
    initializer_manifest_path: Path,
) -> dict[str, Any]:
    _, teacher_module = _verify_contract_alignment()
    options, options_identity = _read_document(options_path, "verifier options")
    if not _type_exact_equal(options, VERIFIER_OPTIONS):
        raise ValueError("verifier options differ from the exact frozen contract")
    capsule, capsule_identity, identities = _parse_capsule(capsule_path)

    executable_identity = _identity(Path(sys.executable), "verifier executable")
    runner_identity = _identity(_verifier_runner_path(), "verifier runner")
    for actual, expected, label in (
        (executable_identity, identities["upstreamVerifierExecutable"], "verifier executable"),
        (runner_identity, identities["upstreamVerifierRunner"], "verifier runner"),
        (options_identity, identities["upstreamVerifierOptions"], "verifier options"),
    ):
        _same_identity(actual, expected, label)

    initializer, initializer_authority = _verify_initializer(
        initializer_manifest_path, capsule, identities
    )
    with AuthorityDatabase() as database:
        root_count, routed_children, route_digest = _parse_routes(
            Path(str(identities["targetFreeRouting"]["path"])),
            identities["targetFreeRouting"],
            database,
        )
        expected_root_count = len(CELLS) * sum(
            ROUTING_QUOTAS_PER_PHASE_SIDE.values()
        )
        if root_count != expected_root_count:
            raise ValueError(
                "routing root count differs from the exact phase/side quota"
            )
        component_count = _parse_components(
            Path(str(identities["componentMap"]["path"])),
            identities["componentMap"],
            database,
            root_count,
        )
        quota_root_inventories, quota_phase_side_inventories = _inventories(database)
        _verify_fixed_routing_quota_inventories(
            quota_root_inventories, quota_phase_side_inventories
        )
        if capsule["teacherInputOrderSha256"] != route_digest:
            raise ValueError("capsule teacher input-order digest changed")
        prelabel, _ = _verify_prelabel(
            identities, capsule, root_count
        )
        initializer_created = _parse_timestamp(
            initializer["createdUtc"], "initializer createdUtc"
        )
        if _parse_timestamp(prelabel["createdUtc"], "prelabel createdUtc") <= initializer_created:
            raise ValueError("prelabel seal does not follow initializer freeze")

        lineage, terminal_authority, _, _ = _verify_terminal_and_selected_sources(
            identities, database, routed_children
        )
        if _parse_timestamp(prelabel["createdUtc"], "prelabel createdUtc") <= _parse_timestamp(
            lineage["createdUtc"], "terminal lineage createdUtc"
        ):
            raise ValueError("prelabel seal does not follow terminal-lineage completion")
        forbidden_authority = _verify_forbidden(
            capsule, identities, lineage, database
        )
        registry, _ = _read_document(
            Path(str(identities["priorForbiddenRegistry"]["path"])),
            "prior-forbidden registry chronology",
        )
        if _parse_timestamp(prelabel["createdUtc"], "prelabel createdUtc") <= _parse_timestamp(
            registry["createdUtc"], "prior-forbidden registry createdUtc"
        ):
            raise ValueError("prelabel seal does not follow forbidden-registry freeze")

        hce_claim, hce_completion, static_hce_authority = _verify_static_hce(
            capsule,
            identities,
            prelabel,
            database,
            routed_children,
        )
        claim, _, claim_context = _verify_teacher_claim(
            capsule,
            identities,
            route_digest,
            hce_completion,
            teacher_module,
        )
        receipt, total_attempts, deep_attempts, receipt_created = _replay_teacher_ledger(
            identities=identities,
            claim=claim,
            claim_context=claim_context,
            database=database,
            routed_children=routed_children,
            route_digest=route_digest,
            teacher_module=teacher_module,
        )
        del receipt

        if (
            capsule["plannedProjectedCorpusPath"]
            != identities["projectedCorpus"]["path"]
            or capsule["plannedProjectionManifestPath"]
            != identities["labelManifest"]["path"]
            or not _type_exact_equal(
                identities["plannedProjectionProducer"],
                identities["projectionProducer"],
            )
        ):
            raise ValueError("planned projection producer/path differs from realization")
        teacher_authority, projection_authority, _, _ = (
            _verify_teacher_projection_completion(
                capsule=capsule,
                identities=identities,
                prelabel=prelabel,
                hce_claim=hce_claim,
                hce_completion=hce_completion,
                claim=claim,
                route_digest=route_digest,
                database=database,
                routed_children=routed_children,
                total_attempts=total_attempts,
                deep_attempts=deep_attempts,
                receipt_created=receipt_created,
            )
        )

        component_authority = {
            "componentMap": dict(identities["componentMap"]),
            "roots": root_count,
            "components": component_count,
            "wholeComponentSplits": True,
            "semanticsReplayed": True,
        }

    # Recheck every identity after all external replays, including the capsule
    # and verifier itself.  A detectable path/content race invalidates the run.
    if _identity(capsule_path, "capsule final recheck") != capsule_identity:
        raise ValueError("capsule changed during verification")
    for field, identity in identities.items():
        if _identity(Path(str(identity["path"])), f"capsule {field} final recheck") != identity:
            raise ValueError(f"capsule dependency {field} changed during verification")
    if (
        _identity(Path(sys.executable), "verifier executable final recheck")
        != executable_identity
        or _identity(
            _verifier_runner_path(), "verifier runner final recheck"
        )
        != runner_identity
        or _identity(options_path, "verifier options final recheck") != options_identity
    ):
        raise ValueError("verifier executable/runner/options changed during replay")

    return {
        "schemaVersion": 1,
        "kind": VERIFICATION_KIND,
        "profileId": PROFILE_ID,
        "status": "passed-fresh-semantic-replay",
        "capsule": dict(capsule_identity),
        "verifierExecutable": dict(executable_identity),
        "verifierRunner": dict(runner_identity),
        "verifierOptions": dict(options_identity),
        "initializerAuthority": initializer_authority,
        "terminalAuthority": terminal_authority,
        "priorForbiddenAuthority": forbidden_authority,
        "componentAuthority": component_authority,
        "staticHceAuthority": static_hce_authority,
        "teacherLedgerAuthority": teacher_authority,
        "projectionAuthority": projection_authority,
        "resultInformationRead": False,
    }


def _safe_new_file(path: Path) -> Path:
    absolute = _absolute(path)
    parent = absolute.parent
    for item in (*reversed(parent.parents), parent):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"publisher reparse parent is forbidden: {item}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"publisher parent is not a directory: {item}")
    if os.path.lexists(absolute):
        raise FileExistsError(absolute)
    return absolute


def publish_verifier_options(path: Path) -> dict[str, Any]:
    output = _safe_new_file(path)
    payload = _canonical_json(VERIFIER_OPTIONS)
    descriptor = os.open(
        output,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    opened: os.stat_result | None = None
    completed: os.stat_result | None = None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError("verifier options publisher opened an unsafe file")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        completed = os.fstat(descriptor)
        if (
            _stat_key(completed)[:3]
            != (opened.st_dev, opened.st_ino, len(payload))
            or getattr(completed, "st_nlink", 1) != 1
        ):
            raise ValueError("verifier options publisher did not create one regular file")
    except BaseException:
        os.close(descriptor)
        try:
            named = os.lstat(output)
            if opened is not None and (named.st_dev, named.st_ino) == (
                opened.st_dev,
                opened.st_ino,
            ):
                output.unlink()
        except OSError:
            pass
        raise
    else:
        os.close(descriptor)
    assert completed is not None
    if _stat_key(os.lstat(_safe_existing(output))) != _stat_key(completed):
        raise ValueError("published options path no longer names the O_EXCL file")
    identity = _identity(output, "published verifier options")
    if identity["sha256"] != hashlib.sha256(payload).hexdigest():
        raise ValueError("published verifier options changed")
    return identity


def dependency_status_document() -> dict[str, Any]:
    dependencies: dict[str, Any] = {}
    all_ready = True
    for name, (filename, expected_bytes, expected_sha256) in DEPENDENCY_PINS.items():
        path = _dependency_path(filename)
        actual = _identity(path, f"self-test dependency {name}")
        finalized = type(expected_bytes) is int and type(expected_sha256) is str
        matches = finalized and actual["bytes"] == expected_bytes and actual["sha256"] == expected_sha256
        all_ready = all_ready and bool(matches)
        dependencies[name] = {
            "expectedBytes": expected_bytes,
            "expectedSha256": expected_sha256,
            "actual": actual,
            "pinFinalized": finalized,
            "matches": bool(matches),
        }
    return {
        "schemaVersion": 1,
        "kind": "omega-decision-v3-verifier-self-test",
        "profileId": PROFILE_ID,
        "status": "ready" if all_ready else "awaiting-stable-pins",
        "dependencies": dependencies,
        "resultInformationRead": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 2 and arguments[0] == "--publish-verifier-options":
        publish_verifier_options(Path(arguments[1]))
        return 0
    if arguments == ["--self-test"]:
        sys.stdout.buffer.write(_canonical_json(dependency_status_document()))
        return 0
    if len(arguments) == 4 and arguments[0] == "--verify-omega-decision-v3-capsule":
        result = verify_capsule(
            Path(arguments[1]), Path(arguments[2]), Path(arguments[3])
        )
        sys.stdout.buffer.write(_canonical_json(result))
        return 0
    raise ValueError(
        "usage: omega_decision_v3_verifier.py "
        "--verify-omega-decision-v3-capsule CAPSULE OPTIONS INITIALIZER"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


__all__ = [
    "DEPENDENCY_PINS",
    "VERIFIER_OPTIONS",
    "dependency_status_document",
    "main",
    "publish_verifier_options",
    "verify_capsule",
]
