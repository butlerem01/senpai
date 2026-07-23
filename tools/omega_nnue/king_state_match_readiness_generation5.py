#!/usr/bin/env python3
"""Build and seal target-blind Generation-5 Omega match readiness.

``sample`` and ``seal-suites`` cannot observe a candidate or any target.
They create three mutually orbit-disjoint, colour-paired rules-only suites and
bind every root byte before match authorization.  ``authorize`` is a separate
post-held-out transition: it accepts only the exact selected-primary network
authorized by the canonical passed offline report and publishes immutable
OmegaMatch configs plus a core compatibility seal.

This tool never launches or assesses a match.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Iterator, Mapping, Sequence

import king_state_match_protocol_generation5 as contract
import king_state_dotnet_runtime_generation5 as dotnet_runtime
import king_state_matches as core
import validate_king_state_v5_preregistration as preregistration_validator


_IMPORTED_CONTRACT = contract
_IMPORTED_DOTNET_RUNTIME = dotnet_runtime
_IMPORTED_CORE = core
_IMPORTED_PREREGISTRATION_VALIDATOR = preregistration_validator
_CONTRACT_BINDINGS = {
    name: getattr(contract, name)
    for name in (
        "atomic_json",
        "identity",
        "install_core_profile",
        "strict_load",
        "validate_protocol",
        "verify_identity",
        "verify_module_binding",
    )
}
_DOTNET_BINDINGS = {"verify_manifest": dotnet_runtime.verify_manifest}
_CORE_BINDINGS = {
    name: getattr(core, name)
    for name in (
        "Root",
        "_harness_bundle_identity",
        "_match_config",
        "_position_meta",
        "_select_roots",
        "_shuffled_indices",
        "_suite",
        "_verify_config",
        "_verify_seal",
        "_verify_suite",
        "parse_ofen",
    )
}
_PREREGISTRATION_BINDINGS = {
    "validate_profile": preregistration_validator.validate_profile,
}
_PREREGISTRATION_TOP_LEVEL = frozenset(
    preregistration_validator.EXPECTED_TOP_LEVEL
)
_LAZY_TRAINER: Any = None
_LAZY_TRAINER_BINDINGS: dict[str, Any] | None = None


SCHEMA_VERSION = 1
PROFILE_ID = contract.PROFILE_ID
SUITE_SEAL_KIND = "omega-nnue-king-state-v5-suite-seal"
AUTHORIZATION_KIND = "omega-nnue-king-state-v5-match-authorization"
AUDIT_KIND = "omega-nnue-king-state-v5-match-audit"
FORBIDDEN_WORKER_KIND = "omega-nnue-g5-forbidden-catalog-worker-result"
GATES = contract.GATES
PHASES = contract.PHASES
SAMPLER_RECORD_FIELDS = {
    "schemaVersion",
    "kind",
    "generatorSeed",
    "trajectorySeed",
    "trajectoryPairId",
    "trajectoryId",
    "flavor",
    "ply",
    "phase",
    "sideToMove",
    "ofen",
    "pieceCount",
    "whitePieces",
    "blackPieces",
    "champions",
    "wizards",
    "halfmoveClock",
    "selectionRank",
}
SAMPLER_PAIR_ID = re.compile(r"^random-pair-([0-9]{6})$")
UINT64_MASK = (1 << 64) - 1
RAW_CURRENT_ROOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "groupId",
        "sourceOpeningId",
        "sourcePairId",
        "sourceProvenanceTag",
        "sourceGameId",
        "sourceRunId",
        "sourceAttempt",
        "sourcePly",
        "sourceLine",
        "sourceEngineId",
        "sourceEngineSha256",
        "phase",
        "sideToMove",
        "ofen",
        "rootPvMove",
        "selectionRank",
        "candidateRole",
    }
)
FILTERED_CURRENT_ROOT_FIELDS = RAW_CURRENT_ROOT_FIELDS | frozenset(
    {"rawCandidateRole", "rawLeakageComponentId"}
)
RAW_CURRENT_CHILD_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "groupId",
        "sourceGameId",
        "phase",
        "rootPvMove",
        "candidateRole",
        "selectionRank",
        "parentOfen",
        "parentSideToMove",
        "childId",
        "move",
        "moveOrdinal",
        "childOfen",
        "childSideToMove",
        "isPromotion",
    }
)
FILTERED_CURRENT_CHILD_FIELDS = RAW_CURRENT_CHILD_FIELDS | frozenset(
    {"rawCandidateRole", "rawLeakageComponentId"}
)
CURRENT_FEASIBILITY_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "groupId",
        "phase",
        "sideToMove",
        "selectionRank",
        "distinctLegalChildren",
        "sourcePvOccurrences",
        "structurallyFeasible",
        "retained",
        "feasibilityOrdinal",
        "rejectionReasons",
        "rawLeakageComponentId",
    }
)
RAW_ROOTS_PER_PHASE_SIDE = 896
FEASIBLE_ROOTS_PER_PHASE_SIDE = 768
GENERATION4_CLOSURE_PATH = (
    "validation/omega-nnue-king-state-v4-structural-abort.seal.json"
)
GENERATION4_ABORT_TOOL_PATH = "tools/omega_nnue/king_state_generation4_abort.py"
GENERATION4_CLOSURE_IDENTITIES = {
    "finalPreregistration": "validation/omega-nnue-king-state-v4-preregistration.json",
    "finalFreezeSeal": "validation/omega-nnue-king-state-v4-freeze.seal.json",
    "forbiddenPositionCatalog": (
        "build-msvc/data-generation/omega-decision-v1/forbidden-positions.jsonl"
    ),
    "forbiddenPositionCatalogManifest": (
        "build-msvc/data-generation/omega-decision-v1/"
        "forbidden-positions.jsonl.manifest.json"
    ),
    "sourceRootPool": (
        "build-msvc/data-generation/omega-decision-v1/source/rules-only-pool.jsonl"
    ),
    "sourceRootPoolManifest": (
        "build-msvc/data-generation/omega-decision-v1/source/"
        "rules-only-pool.jsonl.manifest.json"
    ),
    "sourceRootPoolSeal": (
        "build-msvc/data-generation/omega-decision-v1/source/"
        "rules-only-pool.jsonl.complete.seal.json"
    ),
    "sourceEvents": "build-msvc/data-generation/omega-decision-v1/source/events.jsonl",
    "sourceOpeningSuite": (
        "build-msvc/data-generation/omega-decision-v1/source/openings.json"
    ),
    "sourceMatchConfig": (
        "build-msvc/data-generation/omega-decision-v1/source/source-match.json"
    ),
    "roots": "build-msvc/data-generation/omega-decision-v1/roots.jsonl",
    "rootsManifest": (
        "build-msvc/data-generation/omega-decision-v1/roots.jsonl.manifest.json"
    ),
    "children": "build-msvc/data-generation/omega-decision-v1/children.jsonl",
    "childrenManifest": (
        "build-msvc/data-generation/omega-decision-v1/children.jsonl.manifest.json"
    ),
    "samplerCompletionSeal": (
        "build-msvc/data-generation/omega-decision-v1/"
        "children.jsonl.complete.seal.json"
    ),
}
CURRENT_CORPUS_PATHS = {
    "rawRoots": "build-msvc/data-generation/omega-decision-v2/raw-roots.jsonl",
    "rawRootsManifest": (
        "build-msvc/data-generation/omega-decision-v2/"
        "raw-roots.jsonl.manifest.json"
    ),
    "rawChildren": (
        "build-msvc/data-generation/omega-decision-v2/raw-children.jsonl"
    ),
    "rawChildrenManifest": (
        "build-msvc/data-generation/omega-decision-v2/"
        "raw-children.jsonl.manifest.json"
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
        "build-msvc/data-generation/omega-decision-v2/children.jsonl.manifest.json"
    ),
    "samplerCompletionSeal": (
        "build-msvc/data-generation/omega-decision-v2/"
        "children.jsonl.complete.seal.json"
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
}
CURRENT_RUNTIME_PATHS = {
    "rootSamplerAssembly": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/"
        "OmegaRootSampler.dll"
    ),
    "decisionSamplerAssembly": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/decision-sampler/"
        "OmegaDecisionSampler.dll"
    ),
    "chessLibAssembly": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/decision-sampler/"
        "ChessLib.dll"
    ),
}
CURRENT_REPLAY_PATHS = {
    "sourceEvents": "build-msvc/data-generation/omega-decision-v2/source/events.jsonl",
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
    "sourceMatchHarnessAssembly": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/OmegaMatch.dll"
    ),
    "sourceOpeningBuilderSource": (
        "tools/omega_nnue/king_state_generation5_source.py"
    ),
    "decisionTeacherSource": "tools/omega_nnue/omega_decision_teacher_generation5.py",
    "deepHceV2Source": "tools/omega_nnue/deep_hce_v2.py",
    "networkFormatPythonSource": "tools/omega_nnue/omega_nnue.py",
    "selectScreenSource": "tools/omega_nnue/select_screen.py",
    # Narrow audited exception: this recognizes historical G3 projection
    # manifests only; it is not a current G5 data or experiment authority.
    "priorProjectionSource": (
        "tools/omega_nnue/king_state_generation4_prior_projection.py"
    ),
    "trainerSource": "tools/omega_nnue/king_state_train_generation5.py",
    "preregistrationValidatorSource": (
        "tools/omega_nnue/validate_king_state_v5_preregistration.py"
    ),
    "pythonRuntimeToolSource": (
        "tools/omega_nnue/king_state_generation5_runtime.py"
    ),
    "generation4AbortVerifierSource": GENERATION4_ABORT_TOOL_PATH,
    "dotnetRuntimeToolSource": (
        "tools/omega_nnue/king_state_dotnet_runtime_generation5.py"
    ),
    "teacherEngineExecutable": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/engine/senpai.exe"
    ),
    "pythonRuntimeManifest": (
        "validation/omega-nnue-king-state-v5-python-runtime.json"
    ),
    "dotnetRuntimeHostExecutable": (
        "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime/dotnet.exe"
    ),
}
SOURCE_DATA_INPUT_IDENTITY_FIELDS = (
    "source",
    "openingSuite",
    "sourceMatchConfig",
    "sourceRootPool",
    "sourceRootPoolManifest",
    "sourceRootPoolSeal",
)
REUSABLE_SOURCE_INFRASTRUCTURE_IDENTITY_FIELDS = (
    "sourceMatchHarnessAssembly",
    "rootSamplerAssembly",
    "rootSamplerChessLibAssembly",
    "sourceOpeningBuilderSource",
)
SOURCE_LAUNCH_PROVENANCE_IDENTITY_FIELDS = ("sourceMatchCompletionSeal",)
EXPECTED_SOURCE_DATA_DISJOINTNESS = {
    "policyId": "omega-source-data-input-disjointness-v1",
    "dataInputIdentityFields": list(SOURCE_DATA_INPUT_IDENTITY_FIELDS),
    "reusableInfrastructureIdentityFields": list(
        REUSABLE_SOURCE_INFRASTRUCTURE_IDENTITY_FIELDS
    ),
    "launchProvenanceIdentityFields": list(
        SOURCE_LAUNCH_PROVENANCE_IDENTITY_FIELDS
    ),
    "currentAndPriorDataInputDomainsIdentical": True,
    "priorDataInputsWithoutOfenAuthenticatedByStructuralClosure": True,
    "reusableInfrastructureContentAuthenticated": True,
    "reusableInfrastructureExcludedFromCollisionSet": True,
    "catalogSourceArtifactHashesExcludedFromDataCollisionSet": True,
    "legacyG3TransitiveArtifactHashesAuthenticatedButExcludedFromDataCollisionSet": True,
    "launchProvenanceAuthenticatedAndExcludedFromCollisionSet": True,
}
CURRENT_SOURCE_PAIR_TAG = re.compile(r"^rules-only-pair:(random-pair-[0-9]{6})$")
CURRENT_SOURCE_TRAJECTORY = re.compile(
    r"^(random-pair-[0-9]{6})-(ab|ba)$"
)
OMEGA_COORDINATE_MOVE = re.compile(
    r"^(?:[a-j][0-9]|w[1-4])(?:[a-j][0-9]|w[1-4])(?:[qrbncw])?$"
)
CURRENT_TARGET_LIKE_KEY = re.compile(
    r"(?:^|[_-])(?:scores?|targets?|labels?|evaluations?|evals?|outcomes?|results?|winners?|mates?)(?:$|[_-])",
    re.IGNORECASE,
)
CANONICAL_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,7})?Z$"
)
ROOT_HCE_OPTIONS = {
    "Threads": "1",
    "Hash": "128",
    "Ponder": "false",
    "OwnBook": "false",
    "UCI_Chess960": "false",
    "UCI_Variant": "omega",
    "OmegaNNUEFile": "<empty>",
    "UseOmegaNNUE": "false",
}
SOURCE_POOL_PHASE_WINDOWS = {
    "opening": [6, 48],
    "middlegame": [20, 140],
    "late": [40, 260],
    "endgame": [60, 400],
}
CHILD_POLICY = {
    "variant": "omega",
    "legalMoveAuthority": "ChessLib.Game.GetAvailableSquares",
    "childApplication": (
        "fresh Game initialized from parent OFEN; DoMove(checkEndGame=false)"
    ),
    "promotionSuffixOrder": "qrbncw",
    "moveOrder": "coordinate ordinal",
    "stableIdDomain": "omega-decision-child-v1",
    "fields": {
        "rootId": "rootId",
        "groupId": "groupId",
        "ofen": "ofen",
        "phase": "phase",
        "sourceGameId": "sourceGameId",
        "rootPvMove": "rootPvMove",
        "candidateRole": "candidateRole",
        "selectionRank": "selectionRank",
    },
}


def _verify_import_bindings(
    protocol: Mapping[str, Any], profile: Mapping[str, Any] | None = None
) -> None:
    verify_binding = _CONTRACT_BINDINGS["verify_module_binding"]
    runtime = _IMPORTED_CONTRACT.mapping(protocol.get("runtime"), "match runtime")
    identities = (
        None
        if profile is None
        else _IMPORTED_CONTRACT.mapping(
            profile.get("finalFreezeIdentities"), "final-freeze identities"
        )
    )
    verify_binding(
        contract,
        _IMPORTED_CONTRACT,
        _IMPORTED_CONTRACT.REPO
        / "tools/omega_nnue/king_state_match_protocol_generation5.py",
        "Generation-5 match protocol module",
        identity_record=None if identities is None else identities.get("matchProtocolTool"),
        callable_bindings=_CONTRACT_BINDINGS,
    )
    verify_binding(
        core,
        _IMPORTED_CORE,
        _IMPORTED_CONTRACT.REPO / "tools/omega_nnue/king_state_matches.py",
        "Generation-5 shared match core",
        identity_record=runtime.get("matchCoreSource"),
        callable_bindings=_CORE_BINDINGS,
    )
    verify_binding(
        dotnet_runtime,
        _IMPORTED_DOTNET_RUNTIME,
        _IMPORTED_CONTRACT.REPO
        / "tools/omega_nnue/king_state_dotnet_runtime_generation5.py",
        "Generation-5 .NET runtime module",
        identity_record=runtime.get("dotnetRuntimeTool"),
        callable_bindings=_DOTNET_BINDINGS,
    )
    verify_binding(
        preregistration_validator,
        _IMPORTED_PREREGISTRATION_VALIDATOR,
        _IMPORTED_CONTRACT.REPO
        / "tools/omega_nnue/validate_king_state_v5_preregistration.py",
        "Generation-5 preregistration validator",
        identity_record=(
            None
            if identities is None
            else identities.get("preregistrationValidatorSource")
        ),
        callable_bindings=_PREREGISTRATION_BINDINGS,
    )


def _install_core_profile(protocol: Mapping[str, Any]) -> None:
    _verify_import_bindings(protocol)
    _IMPORTED_CONTRACT.install_core_profile(protocol)


def _verify_lazy_frozen_module(
    module: Any,
    *,
    identity_name: str,
    label: str,
    binding_names: Sequence[str],
    allow_template: bool = False,
) -> None:
    global _LAZY_TRAINER, _LAZY_TRAINER_BINDINGS
    if identity_name != "trainerSource":
        raise ValueError(
            "only the post-held-out trainer may use in-process lazy binding"
        )
    profile_path = _IMPORTED_CONTRACT.FINAL_PROFILE
    if not profile_path.is_file() and allow_template:
        profile_path = (
            _IMPORTED_CONTRACT.REPO
            / "validation/omega-nnue-king-state-v5-preregistration.template.json"
        )
    profile = _IMPORTED_CONTRACT.strict_load(
        profile_path, "Generation-5 preregistration for module binding"
    )
    identities = _IMPORTED_CONTRACT.mapping(
        profile.get("finalFreezeIdentities"), "final-freeze identities"
    )
    record = _IMPORTED_CONTRACT.mapping(identities.get(identity_name), label)
    expected_path = _profile_path(record.get("path"), f"{label} path")
    identity_record = record if type(record.get("bytes")) is int else None

    def capture_local_bindings() -> dict[str, Any]:
        module_name = getattr(module, "__name__", None)
        if type(module_name) is not str or not module_name:
            raise ValueError(f"{label} has no canonical module name")
        captured: dict[str, Any] = {}
        for binding_name in binding_names:
            binding = getattr(module, binding_name, None)
            if not callable(binding):
                raise ValueError(f"{label}.{binding_name} is not callable")
            if getattr(binding, "__module__", None) != module_name:
                raise ValueError(
                    f"{label}.{binding_name} is not defined by its frozen module"
                )
            code = getattr(binding, "__code__", None)
            code_filename = None if code is None else getattr(code, "co_filename", None)
            if (
                type(code_filename) is not str
                or _IMPORTED_CONTRACT.resolve(Path(code_filename)) != expected_path
            ):
                raise ValueError(
                    f"{label}.{binding_name} originates from a noncanonical source"
                )
            captured[binding_name] = binding
        return captured

    if (_LAZY_TRAINER is None) != (_LAZY_TRAINER_BINDINGS is None):
        raise ValueError("trainer lazy binding cache is inconsistent")
    if _LAZY_TRAINER is None:
        captured = capture_local_bindings()
        _CONTRACT_BINDINGS["verify_module_binding"](
            module,
            module,
            expected_path,
            label,
            identity_record=identity_record,
            callable_bindings=captured,
        )
        _LAZY_TRAINER = module
        _LAZY_TRAINER_BINDINGS = captured
    imported = _LAZY_TRAINER
    bindings = _LAZY_TRAINER_BINDINGS
    _CONTRACT_BINDINGS["verify_module_binding"](
        module,
        imported,
        expected_path,
        label,
        identity_record=identity_record,
        callable_bindings=bindings,
    )


def _profile_path(value: Any, label: str) -> Path:
    return contract.protocol_path(value, label)


def _identity_shape(record: Any, label: str) -> dict[str, Any]:
    """Validate an identity capsule's exact JSON types without opening it."""

    value = contract.mapping(record, label)
    if (
        set(value) != {"path", "bytes", "sha256"}
        or type(value.get("path")) is not str
        or not value["path"]
        or value["path"] != value["path"].strip()
        or type(value.get("bytes")) is not int
        or value["bytes"] < 0
        or type(value.get("sha256")) is not str
        or contract.HEX_256.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(value)


def _profile_identity(
    record: Any, *, path: Path, label: str
) -> dict[str, Any]:
    value = contract.mapping(record, label)
    _identity_shape(value, label)
    if _profile_path(value.get("path"), f"{label} path") != contract.resolve(path):
        raise ValueError(f"{label} path changed")
    actual = contract.identity(path)
    if value["bytes"] != actual["bytes"] or value["sha256"] != actual["sha256"]:
        raise ValueError(f"{label} identity changed")
    return actual


def _validate_source_data_disjointness(profile: Mapping[str, Any]) -> None:
    """Require the exact six-data/four-infrastructure/one-launch contract."""

    fresh = contract.mapping(
        profile.get("freshDecisionCorpus"), "source-data fresh decision corpus"
    )
    source = contract.mapping(fresh.get("source"), "source-data source policy")
    declaration = contract.mapping(
        source.get("sourceDataDisjointness"), "source-data disjointness policy"
    )
    contract.require_exact_json(
        declaration,
        EXPECTED_SOURCE_DATA_DISJOINTNESS,
        "source-data disjointness policy",
    )


def _validate_profile(
    path: Path, protocol: Mapping[str, Any], *, template: bool = False
) -> dict[str, Any]:
    path = contract.resolve(path)
    expected_path = (
        contract.resolve(
            contract.REPO
            / protocol["authority"]["preregistrationTemplate"]
        )
        if template
        else contract.resolve(contract.FINAL_PROFILE)
    )
    if path != expected_path:
        raise ValueError("Generation-5 preregistration path is not canonical")
    value = contract.strict_load(path, "Generation-5 preregistration")
    _verify_import_bindings(protocol, None if template else value)
    preregistration_validator.validate_profile(
        value,
        mode="template" if template else "frozen",
        verify_external=not template,
    )
    expected_kind = (
        "omega-nnue-king-state-v5-preregistration-template"
        if template
        else "omega-nnue-king-state-v5-preregistration"
    )
    expected_status = (
        "draft-not-executable-until-development-grid-and-implementation-identities-are-sealed"
        if template
        else "target-blind-generation-5-design-frozen-before-teacher-labels"
    )
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != expected_kind
        or value.get("profileId") != PROFILE_ID
        or value.get("status") != expected_status
    ):
        raise ValueError("Generation-5 preregistration envelope changed")
    _validate_source_data_disjointness(value)
    authority = contract.mapping(protocol.get("authority"), "match authority")
    expected = authority["requiredPostSelectionDeclaration"]
    actual = contract.mapping(
        contract.mapping(value.get("postSelection"), "post selection").get(
            "matches"
        ),
        "post-selection matches",
    )
    contract.require_exact_json(
        actual, expected, "preregistration match declaration"
    )
    seeds = contract.mapping(value.get("seeds"), "preregistration seeds")
    if (
        type(seeds.get("match")) is not int
        or seeds.get("match") != protocol["suiteGeneration"]["baseSeed"]
    ):
        raise ValueError("preregistration/protocol match seed differs")
    namespaces = contract.mapping(value.get("namespaces"), "preregistration namespaces")
    protocol_namespaces = contract.mapping(protocol.get("namespaces"), "protocol namespaces")
    if _profile_path(namespaces.get("matchRoot"), "profile match root") != contract.namespace(
        protocol, "root"
    ):
        raise ValueError("preregistration match root changed")
    stages = contract.mapping(namespaces.get("matchStages"), "profile match stages")
    expected_stage_paths = {
        "development": protocol_namespaces["development"],
        "equalNode": protocol_namespaces["equalNode"],
        "equalTime": protocol_namespaces["equalTime"],
    }
    contract.require_exact_json(
        stages, expected_stage_paths, "preregistration match-stage namespaces"
    )

    identities = contract.mapping(
        value.get("finalFreezeIdentities"), "final-freeze identities"
    )
    runtime = contract.mapping(protocol.get("runtime"), "match runtime")
    if not template:
        mappings = {
            "teacherEngineExecutable": "engine",
            "sourceMatchHarnessAssembly": "omegaMatchAssembly",
            "rootSamplerAssembly": "rootSamplerAssembly",
            "matchCoreSource": "matchCoreSource",
            "dotnetRuntimeHostExecutable": "dotnetHost",
            "dotnetRuntimeManifest": "dotnetRuntimeManifest",
            "dotnetRuntimeToolSource": "dotnetRuntimeTool",
        }
        for profile_key, protocol_key in mappings.items():
            expected_record = contract.mapping(runtime[protocol_key], protocol_key)
            actual_path = _profile_path(expected_record["path"], f"{protocol_key} path")
            actual_record = _profile_identity(
                identities.get(profile_key), path=actual_path, label=profile_key
            )
            if (
                actual_record["bytes"] != expected_record["bytes"]
                or actual_record["sha256"] != expected_record["sha256"]
            ):
                raise ValueError(f"{profile_key} differs from match protocol")
        local_match_identities = {
            "matchProtocol": contract.PROTOCOL_PATH,
            "matchProtocolTool": Path(contract.__file__),
            "matchReadinessTool": Path(__file__),
            "matchOrchestrator": (
                contract.REPO
                / "tools/omega_nnue/king_state_matches_generation5.py"
            ),
        }
        for profile_key, local_path in local_match_identities.items():
            _profile_identity(
                identities.get(profile_key),
                path=local_path,
                label=profile_key,
            )
        forbidden = identities.get("forbiddenPositionCatalogManifests")
        if type(forbidden) is not list or not forbidden:
            raise ValueError("final profile lacks forbidden catalog manifests")
        contract.strict_identity_list(forbidden, "profile forbidden manifest")
        _verify_source_match_completion(value)
    return value


def _validate_final_freeze(path: Path, profile_path: Path) -> dict[str, Any]:
    path = contract.resolve(path)
    profile_path = contract.resolve(profile_path)
    if path != contract.resolve(contract.FINAL_FREEZE):
        raise ValueError("Generation-5 final-freeze path is not canonical")
    if profile_path != contract.resolve(contract.FINAL_PROFILE):
        raise ValueError("Generation-5 final profile path is not canonical")
    value = contract.strict_load(path, "Generation-5 final-freeze seal")
    profile = contract.strict_load(profile_path, "Generation-5 final preregistration")
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "validator",
        "finalFreezeIdentities",
        "finalFreezeIdentitiesSha256",
        "declaration",
        "finalStageSeal",
    }
    if set(value) != expected_fields:
        raise ValueError("final-freeze seal field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "omega-nnue-king-state-v5-final-freeze-seal"
        or value.get("profileId") != PROFILE_ID
        or value.get("status")
        != "target-blind-generation-5-design-frozen-before-teacher-labels"
        or value.get("finalStageSeal") is not True
        or not contract.exact_json_equal(
            value.get("preregistration"), contract.identity(profile_path)
        )
        or not contract.exact_json_equal(
            value.get("validator"),
            contract.identity(
                contract.REPO
                / "tools/omega_nnue/validate_king_state_v5_preregistration.py"
            ),
        )
        or not contract.exact_json_equal(
            value.get("finalFreezeIdentities"),
            profile.get("finalFreezeIdentities"),
        )
    ):
        raise ValueError("final-freeze seal does not authorize this profile")
    contract.parse_utc(value.get("createdUtc"), "final-freeze createdUtc")
    if value.get("createdUtc") != profile.get("createdUtc"):
        raise ValueError("final-freeze timestamp differs from its profile")
    declaration = contract.mapping(value.get("declaration"), "freeze declaration")
    zero_fields = {
        "teacherSearchesPresentAtFreeze": False,
        "generation5TeacherTargetsDecoded": 0,
        "generation5ValidationTargetsDecoded": 0,
        "generation5HeldOutTargetsDecoded": 0,
    }
    contract.require_exact_json(
        declaration, zero_fields, "final-freeze information boundary"
    )
    digest = hashlib.sha256(
        json.dumps(
            profile.get("finalFreezeIdentities"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if value.get("finalFreezeIdentitiesSha256") != digest:
        raise ValueError("final-freeze identity digest changed")
    return value


def _validate_runtime_authority_capsule(
    profile_path: Path,
    freeze_path: Path,
    protocol: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check the already-deep-verified authority capsule without corpus replay."""

    profile_path = contract.resolve(profile_path)
    freeze_path = contract.resolve(freeze_path)
    if profile_path != contract.resolve(contract.FINAL_PROFILE):
        raise ValueError("runtime preregistration path is not canonical")
    if freeze_path != contract.resolve(contract.FINAL_FREEZE):
        raise ValueError("runtime final-freeze path is not canonical")
    profile = contract.strict_load(profile_path, "runtime final preregistration")
    if (
        set(profile) != _PREREGISTRATION_TOP_LEVEL
        or type(profile.get("schemaVersion")) is not int
        or profile.get("schemaVersion") != SCHEMA_VERSION
        or profile.get("kind")
        != "omega-nnue-king-state-v5-preregistration"
        or profile.get("profileId") != PROFILE_ID
        or profile.get("status")
        != "target-blind-generation-5-design-frozen-before-teacher-labels"
    ):
        raise ValueError("runtime preregistration envelope changed")
    contract.parse_utc(profile.get("createdUtc"), "runtime preregistration createdUtc")
    _validate_source_data_disjointness(profile)
    if profile_path == contract.resolve(
        contract.REPO
        / "validation/omega-nnue-king-state-v5-preregistration.json"
    ):
        runtime_identities = contract.mapping(
            profile.get("finalFreezeIdentities"), "runtime final-freeze identities"
        )
        for name, relative in (
            (
                "sourceMatchCompletionSeal",
                CURRENT_REPLAY_PATHS["sourceMatchCompletionSeal"],
            ),
            (
                "generation4AbortVerifierSource",
                GENERATION4_ABORT_TOOL_PATH,
            ),
        ):
            _profile_identity(
                runtime_identities.get(name),
                path=contract.REPO / relative,
                label=f"runtime {name}",
            )
    _verify_import_bindings(protocol, profile)

    freeze = contract.strict_load(freeze_path, "runtime final-freeze seal")
    expected_freeze_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "validator",
        "finalFreezeIdentities",
        "finalFreezeIdentitiesSha256",
        "declaration",
        "finalStageSeal",
    }
    if (
        set(freeze) != expected_freeze_fields
        or type(freeze.get("schemaVersion")) is not int
        or freeze.get("schemaVersion") != SCHEMA_VERSION
        or freeze.get("kind") != "omega-nnue-king-state-v5-final-freeze-seal"
        or freeze.get("profileId") != PROFILE_ID
        or freeze.get("status")
        != "target-blind-generation-5-design-frozen-before-teacher-labels"
        or freeze.get("finalStageSeal") is not True
    ):
        raise ValueError("runtime final-freeze envelope changed")
    contract.parse_utc(freeze.get("createdUtc"), "runtime final-freeze createdUtc")
    contract.require_exact_json(
        freeze.get("preregistration"),
        contract.identity(profile_path),
        "runtime final-freeze preregistration",
    )
    contract.require_exact_json(
        freeze.get("validator"),
        contract.identity(
            contract.REPO
            / "tools/omega_nnue/validate_king_state_v5_preregistration.py"
        ),
        "runtime final-freeze validator",
    )
    contract.require_exact_json(
        freeze.get("finalFreezeIdentities"),
        profile.get("finalFreezeIdentities"),
        "runtime final-freeze identities",
    )
    contract.require_exact_json(
        freeze.get("declaration"),
        {
            "teacherSearchesPresentAtFreeze": False,
            "generation5TeacherTargetsDecoded": 0,
            "generation5ValidationTargetsDecoded": 0,
            "generation5HeldOutTargetsDecoded": 0,
        },
        "runtime final-freeze information boundary",
    )
    if freeze.get("createdUtc") != profile.get("createdUtc"):
        raise ValueError("runtime final-freeze timestamp differs from profile")
    digest = hashlib.sha256(
        json.dumps(
            profile.get("finalFreezeIdentities"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if freeze.get("finalFreezeIdentitiesSha256") != digest:
        raise ValueError("runtime final-freeze identity digest changed")
    return profile, freeze


def _target_access_counters(value: Any, prefix: str = "") -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    if type(value) is dict:
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            lowered = str(key).casefold()
            if (
                type(item) is int
                and ("target" in lowered or "label" in lowered)
                and ("decoded" in lowered or "accessed" in lowered)
            ):
                result.append((name, item))
            result.extend(_target_access_counters(item, name))
    elif type(value) is list:
        for index, item in enumerate(value):
            result.extend(_target_access_counters(item, f"{prefix}[{index}]"))
    return result


def _sanitized_python_environment() -> dict[str, str]:
    """Return an environment which cannot inject parent-process Python code."""

    blocked_prefixes = (
        "COVERAGE_",
        "DD_",
        "PYDEVD_",
    )
    blocked_names = {
        "PYTHONBREAKPOINT",
        "PYTHONCASEOK",
        "PYTHONDEBUG",
        "PYTHONDUMPREFS",
        "PYTHONEXECUTABLE",
        "PYTHONFAULTHANDLER",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONMALLOC",
        "PYTHONPATH",
        "PYTHONPLATLIBDIR",
        "PYTHONPROFILEIMPORTTIME",
        "PYTHONSTARTUP",
        "PYTHONTRACEMALLOC",
        "PYTHONUSERBASE",
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_PROMPT",
    }
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in blocked_names
        and not name.upper().startswith(blocked_prefixes)
    }
    environment.update(
        {
            "BLIS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
        }
    )
    return environment


def _forbidden_worker_authority(
    profile: Mapping[str, Any] | None,
    *,
    allow_template: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Bind the worker source, teacher source, and Python executable."""

    if allow_template:
        return (
            contract.identity(Path(__file__)),
            contract.identity(
                contract.REPO / "tools/omega_nnue/omega_decision_teacher_generation5.py"
            ),
            contract.identity(Path(sys.executable)),
        )
    if profile is None:
        raise ValueError("a frozen profile is required for forbidden-catalog parsing")
    identities = contract.mapping(
        profile.get("finalFreezeIdentities"), "forbidden-worker final identities"
    )
    worker = _profile_identity(
        identities.get("matchReadinessTool"),
        path=Path(__file__),
        label="forbidden-worker readiness source",
    )
    teacher = _profile_identity(
        identities.get("decisionTeacherSource"),
        path=contract.REPO / "tools/omega_nnue/omega_decision_teacher_generation5.py",
        label="forbidden-worker decision-teacher source",
    )
    runtime_manifest_pin = _profile_identity(
        identities.get("pythonRuntimeManifest"),
        path=contract.REPO
        / "validation/omega-nnue-king-state-v5-python-runtime.json",
        label="forbidden-worker Python runtime manifest",
    )
    runtime_manifest = _load_pinned_top_level_allowlist(
        Path(runtime_manifest_pin["path"]),
        runtime_manifest_pin,
        "forbidden-worker Python runtime manifest",
        field_inventory={
            "schemaVersion",
            "kind",
            "profileId",
            "status",
            "createdUtc",
            "runtime",
            "informationBoundary",
            "finalStageSeal",
        },
        decode_fields={
            "schemaVersion",
            "kind",
            "profileId",
            "status",
            "createdUtc",
            "runtime",
            "finalStageSeal",
        },
    )
    if (
        set(runtime_manifest)
        != {
            "schemaVersion",
            "kind",
            "profileId",
            "status",
            "createdUtc",
            "runtime",
            "finalStageSeal",
        }
        or runtime_manifest.get("schemaVersion") != SCHEMA_VERSION
        or runtime_manifest.get("kind") != "omega-nnue-king-state-v5-python-runtime"
        or runtime_manifest.get("profileId") != PROFILE_ID
        or runtime_manifest.get("finalStageSeal") is not True
    ):
        raise ValueError("forbidden-worker Python runtime manifest changed")
    _require_canonical_utc(
        runtime_manifest.get("createdUtc"), "forbidden-worker runtime createdUtc"
    )
    runtime = contract.mapping(
        runtime_manifest.get("runtime"), "forbidden-worker runtime"
    )
    python = contract.mapping(runtime.get("python"), "forbidden-worker Python")
    executable = _identity_shape(
        python.get("executable"), "forbidden-worker Python executable"
    )
    contract.verify_identity(executable, "forbidden-worker Python executable")
    return worker, teacher, executable


def _forbidden_worker_command(
    python: Mapping[str, Any],
    worker: Mapping[str, Any],
    teacher: Mapping[str, Any],
    manifests: Sequence[Path],
    output: Path,
    *,
    allow_noncanonical_inventory_for_self_test: bool = False,
) -> list[str]:
    source_path = contract.resolve(Path(str(worker["path"])))
    pycache_prefix = contract.resolve(output.parent / "isolated-python-cache")
    if pycache_prefix.exists():
        raise FileExistsError("forbidden-worker bytecode namespace already exists")
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(source_path.parent)!r});"
        f"runpy.run_path({str(source_path)!r},run_name='__main__')"
    )
    command = [
        str(python["path"]),
        "-I",
        "-B",
        "-X",
        f"pycache_prefix={pycache_prefix}",
        "-c",
        bootstrap,
        "forbidden-catalog-worker",
        "--teacher-source",
        str(teacher["path"]),
        "--teacher-bytes",
        str(teacher["bytes"]),
        "--teacher-sha256",
        str(teacher["sha256"]),
        "--output",
        str(output),
    ]
    for manifest in manifests:
        command.extend(("--manifest", str(manifest)))
    if allow_noncanonical_inventory_for_self_test:
        command.append("--self-test-allow-noncanonical-inventory")
    return command


def _forbidden_catalog_worker(args: argparse.Namespace) -> None:
    """Internal clean-process entry point; never consumes parent module state."""

    teacher = {
        "path": str(contract.resolve(args.teacher_source)),
        "bytes": args.teacher_bytes,
        "sha256": args.teacher_sha256,
    }
    _identity_shape(teacher, "forbidden worker teacher source")
    teacher_path = contract.verify_identity(
        teacher, "forbidden worker teacher source"
    )
    canonical_teacher = contract.resolve(
        contract.REPO / "tools/omega_nnue/omega_decision_teacher_generation5.py"
    )
    if teacher_path != canonical_teacher:
        raise ValueError("forbidden worker teacher source is noncanonical")
    import omega_decision_teacher_generation5 as decision_teacher

    if (
        contract.resolve(Path(decision_teacher.__file__)) != teacher_path
        or contract.identity(teacher_path) != teacher
    ):
        raise ValueError("forbidden worker imported a different teacher module")
    loader = getattr(decision_teacher, "_load_forbidden_catalogs", None)
    if (
        not callable(loader)
        or getattr(loader, "__module__", None) != decision_teacher.__name__
        or contract.resolve(Path(loader.__code__.co_filename)) != teacher_path
    ):
        raise ValueError("forbidden worker teacher loader binding changed")
    manifests = sorted(
        (contract.resolve(item) for item in args.manifest),
        key=lambda item: str(item).casefold(),
    )
    if args.self_test_allow_noncanonical_inventory:
        forbidden, pins = loader(
            manifests, enforce_canonical_source_inventory=False
        )
    else:
        forbidden, pins = loader(manifests)
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": FORBIDDEN_WORKER_KIND,
        "teacher": teacher,
        "manifests": [dict(item["manifest"]) for item in pins],
        "catalogs": sorted(
            (dict(item["catalog"]) for item in pins),
            key=lambda item: str(item["path"]).casefold(),
        ),
        "positionCounts": [int(item["positionCount"]) for item in pins],
        "exact": sorted(forbidden["exact"]),
        "signatures": sorted(forbidden["signatures"]),
        "sourceGameIds": sorted(forbidden["sourceGameIds"]),
        "sourceRunIds": sorted(forbidden["sourceRunIds"]),
        "sourceDataInputSha256": sorted(forbidden["sourceDataInputSha256"]),
    }
    contract.atomic_json(contract.resolve(args.output), result, exclusive=True)


def _validate_forbidden_inputs(
    manifests: Sequence[Path],
    position_files: Sequence[Path],
    *,
    profile: Mapping[str, Any] | None = None,
    _allow_template_module_for_self_test: bool = False,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    set[str],
    dict[str, int],
]:
    if not manifests or not position_files:
        raise ValueError("at least one forbidden manifest and position file is required")
    resolved_manifests = [contract.resolve(item) for item in manifests]
    resolved_positions = [contract.resolve(item) for item in position_files]
    if len(set(resolved_manifests)) != len(resolved_manifests):
        raise ValueError("forbidden manifest input is duplicated")
    if len(set(resolved_positions)) != len(resolved_positions):
        raise ValueError("forbidden catalog input is duplicated")

    ordered_manifests = sorted(resolved_manifests, key=lambda item: str(item).casefold())
    worker, teacher, python = _forbidden_worker_authority(
        profile, allow_template=_allow_template_module_for_self_test
    )
    with tempfile.TemporaryDirectory(prefix="omega-g5-forbidden-worker-") as directory:
        output = Path(directory) / "result.json"
        command = _forbidden_worker_command(
            python,
            worker,
            teacher,
            ordered_manifests,
            output,
            allow_noncanonical_inventory_for_self_test=(
                _allow_template_module_for_self_test
            ),
        )
        completed = subprocess.run(
            command,
            cwd=Path(directory),
            env=_sanitized_python_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(
                "clean forbidden-catalog worker failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        if completed.stdout.strip() or completed.stderr.strip():
            raise ValueError("clean forbidden-catalog worker emitted unexpected output")
        result_pin = contract.identity(output)
        result = _load_pinned_json(
            output, result_pin, "clean forbidden-catalog worker result"
        )
    expected_result_fields = {
        "schemaVersion",
        "kind",
        "teacher",
        "manifests",
        "catalogs",
        "positionCounts",
        "exact",
        "signatures",
        "sourceGameIds",
        "sourceRunIds",
        "sourceDataInputSha256",
    }
    if (
        set(result) != expected_result_fields
        or result.get("schemaVersion") != SCHEMA_VERSION
        or result.get("kind") != FORBIDDEN_WORKER_KIND
        or not contract.exact_json_equal(result.get("teacher"), teacher)
    ):
        raise ValueError("clean forbidden-catalog worker result changed")
    manifest_records = contract.strict_identity_list(
        result.get("manifests"), "forbidden worker manifest"
    )
    expected_catalogs = contract.strict_identity_list(
        result.get("catalogs"), "forbidden worker catalog"
    )
    counts = result.get("positionCounts")
    if (
        type(counts) is not list
        or len(counts) != len(manifest_records)
        or any(type(item) is not int or item < 0 for item in counts)
    ):
        raise ValueError("clean forbidden-catalog worker counts changed")
    set_fields: dict[str, set[str]] = {}
    for name in (
        "exact",
        "signatures",
        "sourceGameIds",
        "sourceRunIds",
        "sourceDataInputSha256",
    ):
        values = result.get(name)
        if (
            type(values) is not list
            or any(type(item) is not str or not item for item in values)
            or values != sorted(set(values))
        ):
            raise ValueError(f"clean forbidden-catalog worker {name} changed")
        set_fields[name] = set(values)
    positions = sorted(
        (contract.identity(item) for item in resolved_positions),
        key=lambda item: str(item["path"]).casefold(),
    )
    if not contract.exact_json_equal(positions, expected_catalogs):
        raise ValueError(
            "supplied forbidden position files are not exactly the canonical catalogs"
        )
    signatures = set_fields["exact"] | set_fields["signatures"]
    audit = {
        "catalogs": len(manifest_records),
        "positions": sum(counts),
        "exactPositionKeys": len(set_fields["exact"]),
        "orbitSignatures": len(signatures),
        "sourceGameIds": len(set_fields["sourceGameIds"]),
        "sourceRunIds": len(set_fields["sourceRunIds"]),
        "sourceDataInputSha256": len(set_fields["sourceDataInputSha256"]),
    }
    return manifest_records, positions, signatures, audit


def _assert_profile_forbidden_manifests(
    profile: Mapping[str, Any], supplied: Sequence[Mapping[str, Any]]
) -> None:
    identities = contract.mapping(
        profile.get("finalFreezeIdentities"), "final-freeze identities"
    )
    declared = contract.strict_identity_list(
        identities.get("forbiddenPositionCatalogManifests"),
        "profile forbidden manifest",
    )
    def normalize(values: Sequence[Mapping[str, Any]]) -> list[tuple[str, int, str]]:
        normalized: list[tuple[str, int, str]] = []
        for item in values:
            path = Path(str(item["path"]))
            if not path.is_absolute():
                path = contract.REPO / path
            normalized.append(
                (
                    str(path.resolve()).casefold(),
                    int(item["bytes"]),
                    str(item["sha256"]),
                )
            )
        return sorted(normalized)
    if normalize(declared) != normalize(supplied):
        raise ValueError("supplied forbidden manifests differ from final profile")


class _TargetBlindJsonParser:
    """Grammar-complete JSON scanner which can quarantine sensitive subtrees.

    Values are never materialized here.  Object keys are the sole decoded
    lexemes, because recognizing a forbidden key before visiting its value is
    the information-boundary guarantee this module needs.
    """

    _NUMBER = re.compile(
        r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
    )

    def __init__(self, text: str) -> None:
        self.text = text
        self.length = len(text)
        self.index = 0

    def _whitespace(self) -> None:
        while self.index < self.length and self.text[self.index] in " \t\r\n":
            self.index += 1

    def _string(self, *, decode: bool) -> str | None:
        start = self.index
        if self.index >= self.length or self.text[self.index] != '"':
            raise ValueError("expected JSON string")
        self.index += 1
        while self.index < self.length:
            character = self.text[self.index]
            if character == '"':
                self.index += 1
                if not decode:
                    return None
                try:
                    value = json.loads(self.text[start:self.index])
                except json.JSONDecodeError as error:
                    raise ValueError("invalid JSON string") from error
                if type(value) is not str:
                    raise ValueError("JSON object key is not a string")
                return value
            if ord(character) < 0x20:
                raise ValueError("control character in JSON string")
            if character != "\\":
                self.index += 1
                continue
            self.index += 1
            if self.index >= self.length:
                raise ValueError("unterminated JSON escape")
            escape = self.text[self.index]
            if escape in '"\\/bfnrt':
                self.index += 1
                continue
            if escape != "u" or self.index + 4 >= self.length:
                raise ValueError("invalid JSON escape")
            digits = self.text[self.index + 1:self.index + 5]
            if len(digits) != 4 or any(item not in "0123456789abcdefABCDEF" for item in digits):
                raise ValueError("invalid JSON unicode escape")
            self.index += 5
        raise ValueError("unterminated JSON string")

    def _value(self, *, reject_sensitive: bool) -> None:
        self._whitespace()
        if self.index >= self.length:
            raise ValueError("missing JSON value")
        character = self.text[self.index]
        if character == '"':
            self._string(decode=False)
            return
        if character == "{":
            self._object(reject_sensitive=reject_sensitive, collect=False)
            return
        if character == "[":
            self.index += 1
            self._whitespace()
            if self.index < self.length and self.text[self.index] == "]":
                self.index += 1
                return
            while True:
                self._value(reject_sensitive=reject_sensitive)
                self._whitespace()
                if self.index < self.length and self.text[self.index] == ",":
                    self.index += 1
                    continue
                if self.index < self.length and self.text[self.index] == "]":
                    self.index += 1
                    return
                raise ValueError("missing comma/end in JSON array")
        for literal in ("true", "false", "null"):
            if self.text.startswith(literal, self.index):
                self.index += len(literal)
                return
        match = self._NUMBER.match(self.text, self.index)
        if match is None:
            raise ValueError("invalid JSON primitive")
        self.index = match.end()

    def _object(
        self, *, reject_sensitive: bool, collect: bool
    ) -> dict[str, tuple[str, str]]:
        if self.index >= self.length or self.text[self.index] != "{":
            raise ValueError("JSON value is not an object")
        self.index += 1
        fields: dict[str, tuple[str, str]] = {}
        self._whitespace()
        if self.index < self.length and self.text[self.index] == "}":
            self.index += 1
            return fields
        while True:
            key = self._string(decode=True)
            assert type(key) is str
            lowered = key.casefold()
            if lowered in fields:
                raise ValueError("case-colliding or duplicate JSON field")
            if reject_sensitive and _target_like_current_field(key):
                # Deliberately fail before even grammar-scanning the value.
                raise ValueError("target-like JSON field was quarantined")
            self._whitespace()
            if self.index >= self.length or self.text[self.index] != ":":
                raise ValueError("missing colon after JSON field")
            self.index += 1
            self._whitespace()
            value_start = self.index
            self._value(reject_sensitive=reject_sensitive)
            if collect:
                fields[lowered] = (key, self.text[value_start:self.index])
            else:
                # The inventory is still needed to reject duplicate keys.
                fields[lowered] = (key, "")
            self._whitespace()
            if self.index < self.length and self.text[self.index] == ",":
                self.index += 1
                self._whitespace()
                continue
            if self.index < self.length and self.text[self.index] == "}":
                self.index += 1
                return fields
            raise ValueError("missing comma/end in JSON object")

    def parse_value(self, *, reject_sensitive: bool) -> None:
        self._value(reject_sensitive=reject_sensitive)
        self._whitespace()
        if self.index != self.length:
            raise ValueError("trailing text after JSON value")

    def parse_top_object(
        self, *, reject_sensitive: bool
    ) -> dict[str, tuple[str, str]]:
        self._whitespace()
        fields = self._object(reject_sensitive=reject_sensitive, collect=True)
        self._whitespace()
        if self.index != self.length:
            raise ValueError("trailing text after target-blind corpus row")
        return fields


def _validate_target_blind_json_lexeme(
    text: str, *, reject_sensitive: bool
) -> None:
    _TargetBlindJsonParser(text).parse_value(reject_sensitive=reject_sensitive)


def _target_blind_top_level_fields(
    text: str, *, reject_sensitive: bool = False
) -> dict[str, tuple[str, str]]:
    """Return top-level spans after recursively validating complete JSON."""

    return _TargetBlindJsonParser(text).parse_top_object(
        reject_sensitive=reject_sensitive
    )


def _target_like_current_field(name: str) -> bool:
    # Preserve uppercase runs as words (``TARGETS`` -> ``targets``) while
    # still splitting ordinary camel case and acronym-to-word boundaries
    # (``heldOutTARGETSDecoded`` -> ``held_out_targets_decoded``).  Inserting
    # an underscore before every capital would turn an all-caps sensitive key
    # into ``t_a_r_g_e_t_s`` and let it evade the word-boundary quarantine.
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized).lower()
    return CURRENT_TARGET_LIKE_KEY.search(normalized) is not None


def _iter_target_blind_jsonl(
    path: Path, expected_fields: frozenset[str], label: str
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Decode a row only after its lexical key inventory is allowlisted."""

    try:
        with contract.resolve(path).open(
            "r", encoding="utf-8", errors="strict", newline=""
        ) as stream:
            for line_number, raw_line in enumerate(stream, 1):
                if not raw_line.endswith("\n"):
                    raise ValueError(f"{label}:{line_number}: row is not line-complete")
                text = raw_line[:-1]
                if text.endswith("\r"):
                    text = text[:-1]
                if not text or text != text.strip():
                    raise ValueError(f"{label}:{line_number}: row is blank or padded")
                fields = _target_blind_top_level_fields(
                    text, reject_sensitive=True
                )
                originals = {item[0] for item in fields.values()}
                sensitive = sorted(
                    field for field in originals if _target_like_current_field(field)
                )
                if sensitive:
                    raise ValueError(
                        f"{label}:{line_number}: target-like fields were rejected "
                        "without decoding their values"
                    )
                if originals != set(expected_fields):
                    raise ValueError(
                        f"{label}:{line_number}: field inventory changed"
                    )
                try:
                    value = json.loads(
                        text,
                        object_pairs_hook=contract._unique_object,
                        parse_constant=lambda token: (_ for _ in ()).throw(
                            ValueError(f"non-finite JSON token {token}")
                        ),
                    )
                except (json.JSONDecodeError, ValueError) as error:
                    raise ValueError(
                        f"{label}:{line_number}: invalid strict JSON"
                    ) from error
                if type(value) is not dict:
                    raise ValueError(f"{label}:{line_number}: row is not an object")
                yield line_number, value
    except UnicodeError as error:
        raise ValueError(f"{label} is not strict UTF-8") from error


def _digest_string_set(domain: str, values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    digest.update(domain.encode("utf-8"))
    digest.update(b"\0")
    for value in sorted(set(values)):
        payload = value.encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _canonical_ofen(value: Any, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} is not a canonical OFEN string")
    normalized = " ".join(value.split())
    if normalized != value:
        raise ValueError(f"{label} has noncanonical whitespace")
    return value


def _sampler_pair_identity(seed: int, pair_id: str) -> str:
    return f"omega-root-sampler-pair-v1\0{seed}\0{pair_id}"


def _sampler_trajectory_identity(seed: int, trajectory_id: str) -> str:
    return f"omega-root-sampler-trajectory-v1\0{seed}\0{trajectory_id}"


def _identity_snapshot(
    path: Path, expected: Mapping[str, Any], label: str
) -> dict[str, Any]:
    _identity_shape(expected, label)
    before = contract.identity(path)
    if not contract.exact_json_equal(before, expected):
        raise ValueError(f"{label} differs from its final-profile pin")
    return before


def _load_pinned_json(
    path: Path, expected: Mapping[str, Any], label: str
) -> dict[str, Any]:
    before = _identity_snapshot(path, expected, label)
    try:
        text = contract.resolve(path).read_text(encoding="utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError(f"{label} is not strict UTF-8") from error
    _validate_target_blind_json_lexeme(text, reject_sensitive=True)
    try:
        value = json.loads(
            text,
            object_pairs_hook=contract._unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token {token}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is invalid strict JSON") from error
    if type(value) is not dict:
        raise ValueError(f"{label} is not a JSON object")
    after = contract.identity(path)
    if not contract.exact_json_equal(before, after):
        raise ValueError(f"{label} changed while being read")
    return value


def _load_pinned_top_level_allowlist(
    path: Path,
    expected: Mapping[str, Any],
    label: str,
    *,
    field_inventory: set[str],
    decode_fields: set[str],
) -> dict[str, Any]:
    """Decode only named top-level structural fields from a pinned object."""

    if not decode_fields.issubset(field_inventory):
        raise AssertionError("top-level decode allowlist escapes its field inventory")
    before = _identity_snapshot(path, expected, label)
    try:
        text = contract.resolve(path).read_text(encoding="utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError(f"{label} is not strict UTF-8") from error
    # Opaque fields are fully grammar-checked recursively, but never decoded.
    # Decoded fields receive an additional recursive sensitive-key quarantine.
    spans = _target_blind_top_level_fields(text, reject_sensitive=False)
    originals = {item[0] for item in spans.values()}
    if originals != field_inventory:
        raise ValueError(f"{label} field inventory changed")
    result: dict[str, Any] = {}
    for name in decode_fields:
        entry = spans.get(name.casefold())
        if entry is None or entry[0] != name:
            raise ValueError(f"{label} field spelling changed: {name}")
        _validate_target_blind_json_lexeme(
            entry[1], reject_sensitive=True
        )
        try:
            result[name] = json.loads(
                entry[1],
                object_pairs_hook=contract._unique_object,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON token {token}")
                ),
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"{label}.{name} is invalid strict JSON") from error
    after = contract.identity(path)
    if not contract.exact_json_equal(before, after):
        raise ValueError(f"{label} changed while being read")
    return result


def _validate_generation4_closure(
    profile: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authenticate the terminal, target-opaque G4 structural-abort capsule.

    The closure is the only G4 document that G5 may decode.  Every G4 corpus
    artifact nested inside it is checked only by path/bytes/SHA-256 identity;
    its rows are never opened by match readiness.
    """

    closure_path = contract.REPO / GENERATION4_CLOSURE_PATH
    closure_pin = _profile_identity(
        profile.get("generation4Closure"),
        path=closure_path,
        label="Generation-4 structural-abort closure",
    )
    before = dict(closure_pin)
    closure = contract.strict_load(closure_path, "Generation-4 structural-abort closure")
    if not contract.exact_json_equal(contract.identity(closure_path), before):
        raise ValueError("Generation-4 closure changed while being authenticated")
    if set(closure) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "status",
        "profileId",
        "abortReason",
        "structuralEvidence",
        "absenceEvidence",
        "identities",
        "producer",
        "finalStageSeal",
    }:
        raise ValueError("Generation-4 closure field inventory changed")
    if (
        type(closure.get("schemaVersion")) is not int
        or closure.get("schemaVersion") != SCHEMA_VERSION
        or closure.get("kind")
        != "omega-nnue-king-state-v4-target-opaque-structural-abort"
        or closure.get("status") != "closed-before-prelabel-or-teacher-search"
        or closure.get("profileId") != "king-state-v4-omega-decision-v1"
        or closure.get("abortReason") != "insufficient-rules-feasible-root-slack"
        or closure.get("finalStageSeal") is not True
    ):
        raise ValueError("Generation-4 closure envelope changed")
    _require_canonical_utc(closure.get("createdUtc"), "Generation-4 closure createdUtc")

    identities = contract.mapping(
        closure.get("identities"), "Generation-4 closure identities"
    )
    if set(identities) != set(GENERATION4_CLOSURE_IDENTITIES):
        raise ValueError("Generation-4 closure identity inventory changed")
    authenticated: dict[str, dict[str, Any]] = {}
    for name, relative in GENERATION4_CLOSURE_IDENTITIES.items():
        authenticated[name] = _profile_identity(
            identities.get(name),
            path=contract.REPO / relative,
            label=f"Generation-4 closure {name}",
        )

    final_identities = contract.mapping(
        profile.get("finalFreezeIdentities"), "Generation-4 verifier identities"
    )
    verifier_pin = _profile_identity(
        final_identities.get("generation4AbortVerifierSource"),
        path=contract.REPO / GENERATION4_ABORT_TOOL_PATH,
        label="Generation-4 structural-abort verifier",
    )
    producer = _validate_generation4_closure_producer(closure.get("producer"))
    if not contract.exact_json_equal(producer, verifier_pin):
        raise ValueError("Generation-4 closure producer differs from final freeze")
    _run_generation4_closure_verifier(profile, verifier_pin, before)

    structural = contract.mapping(
        closure.get("structuralEvidence"), "Generation-4 structural evidence"
    )
    if set(structural) != {
        "rootCount",
        "childCount",
        "minimumLegalChildrenForGuaranteedSelection",
        "guaranteedEligibleRoots",
        "shortfallToDeepQuota",
        "childCountHistogram",
        "phaseSideEvidence",
        "sourcePvMissing",
        "duplicateChildMoves",
        "targetOrScoreFieldsDecoded",
        "proof",
    }:
        raise ValueError("Generation-4 structural evidence inventory changed")
    exact_counts = {
        "rootCount": 5120,
        "childCount": 183881,
        "minimumLegalChildrenForGuaranteedSelection": 5,
        "guaranteedEligibleRoots": 4994,
        "shortfallToDeepQuota": 126,
        "duplicateChildMoves": 0,
        "targetOrScoreFieldsDecoded": 0,
    }
    for name, expected in exact_counts.items():
        if type(structural.get(name)) is not int or structural[name] != expected:
            raise ValueError(f"Generation-4 closure {name} changed")
    if type(structural.get("sourcePvMissing")) is not int or structural["sourcePvMissing"] < 0:
        raise ValueError("Generation-4 closure source-PV evidence changed")
    histogram = contract.mapping(
        structural.get("childCountHistogram"), "Generation-4 child histogram"
    )
    if not histogram or any(
        type(key) is not str
        or not key
        or type(count) is not int
        or count < 0
        for key, count in histogram.items()
    ):
        raise ValueError("Generation-4 closure child histogram changed")
    phase_side = contract.mapping(
        structural.get("phaseSideEvidence"), "Generation-4 phase/side evidence"
    )
    if not phase_side or structural.get("proof") in (None, "", [], {}):
        raise ValueError("Generation-4 closure lacks its structural proof")

    absence = contract.mapping(
        closure.get("absenceEvidence"), "Generation-4 absence evidence"
    )
    if set(absence) != {
        "prohibitedArtifactPaths",
        "existingArtifactCount",
        "matchSamplerDirectoryEmpty",
    }:
        raise ValueError("Generation-4 closure absence evidence inventory changed")
    prohibited = absence.get("prohibitedArtifactPaths")
    if (
        type(prohibited) is not list
        or not prohibited
        or any(type(item) is not str or not item for item in prohibited)
        or len(prohibited) != len(set(prohibited))
        or type(absence.get("existingArtifactCount")) is not int
        or absence.get("existingArtifactCount") != 0
        or absence.get("matchSamplerDirectoryEmpty") is not True
    ):
        raise ValueError("Generation-4 closure absence declaration changed")
    for item in prohibited:
        item_path = _profile_path(item, "Generation-4 prohibited artifact path")
        normalized = item_path.as_posix().lower()
        if "king-state-v4" not in normalized and "omega-decision-v1" not in normalized:
            raise ValueError("Generation-4 prohibited path escaped its namespace")
        if item_path.exists():
            raise ValueError(f"prohibited Generation-4 artifact now exists: {item_path}")
    return closure, before


def _validate_generation4_closure_producer(record: Any) -> dict[str, Any]:
    """Authenticate the closure builder while preserving a relative capsule.

    Closure identities are portable repo-relative records.  ``identity``
    reports an absolute path, so path normalization and content comparison
    must be separate operations rather than an impossible whole-dict equality.
    """

    producer = _identity_shape(record, "Generation-4 closure producer")
    if producer["path"] != GENERATION4_ABORT_TOOL_PATH:
        raise ValueError("Generation-4 closure producer path changed")
    producer_path = _profile_path(
        producer["path"], "Generation-4 closure producer path"
    )
    expected = contract.resolve(contract.REPO / GENERATION4_ABORT_TOOL_PATH)
    if producer_path != expected:
        raise ValueError("Generation-4 closure producer escaped its canonical path")
    actual = contract.identity(producer_path)
    if (
        producer["bytes"] != actual["bytes"]
        or producer["sha256"] != actual["sha256"]
    ):
        raise ValueError("Generation-4 closure producer content changed")
    return actual


def _current_corpus_pins(profile: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    identities = contract.mapping(
        profile.get("finalFreezeIdentities"), "current-corpus final identities"
    )
    pins: dict[str, dict[str, Any]] = {}
    for name, relative in {
        **CURRENT_CORPUS_PATHS,
        **CURRENT_RUNTIME_PATHS,
        **CURRENT_REPLAY_PATHS,
    }.items():
        pins[name] = _profile_identity(
            identities.get(name),
            path=contract.REPO / relative,
            label=f"current-corpus {name}",
        )
        normalized = contract.resolve(Path(pins[name]["path"])).as_posix().lower()
        if name not in {
            "priorProjectionSource",
            "generation4AbortVerifierSource",
        } and (
            "king-state-v4" in normalized
            or "omega-decision-v1" in normalized
            or "generation4" in normalized
        ):
            raise ValueError(f"G5 authority {name} points into a G4 namespace")
    _, pins["generation4Closure"] = _validate_generation4_closure(profile)
    return pins


def _same_content_identity(left: Any, right: Any) -> bool:
    return (
        type(left) is dict
        and type(right) is dict
        and type(left.get("bytes")) is int
        and left.get("bytes") == right.get("bytes")
        and type(left.get("sha256")) is str
        and left.get("sha256") == right.get("sha256")
    )


def _assert_exact_file_reproduction(
    original: Path,
    expected: Mapping[str, Any],
    replay: Path,
    label: str,
) -> dict[str, Any]:
    original_before = _identity_snapshot(original, expected, f"{label} original")
    replay_identity = contract.identity(replay)
    if not _same_content_identity(original_before, replay_identity):
        raise ValueError(f"{label} replay bytes/SHA-256 differ")
    with contract.resolve(original).open("rb") as left, contract.resolve(replay).open(
        "rb"
    ) as right:
        while True:
            left_block = left.read(1024 * 1024)
            right_block = right.read(1024 * 1024)
            if left_block != right_block:
                raise ValueError(f"{label} replay is not byte-for-byte identical")
            if not left_block:
                break
    if not contract.exact_json_equal(
        contract.identity(original), original_before
    ):
        raise ValueError(f"{label} original changed during replay comparison")
    if not contract.exact_json_equal(
        contract.identity(replay), replay_identity
    ):
        raise ValueError(f"{label} replay changed during comparison")
    return {
        "bytes": original_before["bytes"],
        "sha256": original_before["sha256"],
    }


def _root_manifest_semantics(
    path: Path, expected: Mapping[str, Any], label: str
) -> tuple[dict[str, str], str]:
    before = _identity_snapshot(path, expected, label)
    try:
        text = contract.resolve(path).read_text(encoding="utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError(f"{label} is not strict UTF-8") from error
    spans = _target_blind_top_level_fields(text, reject_sensitive=False)
    values = {entry[0]: entry[1].strip() for entry in spans.values()}
    if "createdUtc" not in values or "output" not in values:
        raise ValueError(f"{label} lacks replay-normalized fields")
    _validate_target_blind_json_lexeme(values["createdUtc"], reject_sensitive=True)
    created = json.loads(values["createdUtc"])
    _require_canonical_utc(created, f"{label}.createdUtc")
    values["createdUtc"] = json.dumps("<REPLAY-UTC>")
    _validate_target_blind_json_lexeme(values["output"], reject_sensitive=True)
    output = json.loads(
        values["output"], object_pairs_hook=contract._unique_object
    )
    output_identity = _identity_shape(output, f"{label}.output")
    output_identity["path"] = "<REPLAY-OUTPUT>"
    values["output"] = json.dumps(
        output_identity, sort_keys=True, separators=(",", ":")
    )
    if not contract.exact_json_equal(contract.identity(path), before):
        raise ValueError(f"{label} changed while its semantics were read")
    digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return values, digest


def _safe_manifest_semantics(
    path: Path,
    expected: Mapping[str, Any],
    label: str,
    *,
    normalize_output: bool,
    normalize_manifest: bool = False,
) -> tuple[dict[str, Any], str]:
    value = copy.deepcopy(_load_pinned_json(path, expected, label))
    _require_canonical_utc(value.get("createdUtc"), f"{label}.createdUtc")
    value["createdUtc"] = "<REPLAY-UTC>"
    for field, enabled in (
        ("output", normalize_output),
        ("manifest", normalize_manifest),
    ):
        if not enabled:
            continue
        identity = _identity_shape(value.get(field), f"{label}.{field}")
        identity["path"] = f"<REPLAY-{field.upper()}>"
        if field == "manifest":
            # The manifest's raw identity necessarily changes when its
            # createdUtc/output path changes.  Its independently compared
            # semantic digest above is the authority for those bytes.
            identity["bytes"] = 0
            identity["sha256"] = "0" * 64
        value[field] = identity
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return value, digest


def _python_script_command(
    python: Mapping[str, Any],
    source: Mapping[str, Any],
    arguments: Sequence[str],
    *,
    pycache_prefix: Path,
) -> list[str]:
    source_path = contract.resolve(Path(str(source["path"])))
    cache_path = contract.resolve(pycache_prefix)
    if cache_path.exists():
        raise FileExistsError("replay bytecode namespace already exists")
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(source_path.parent)!r});"
        f"runpy.run_path({str(source_path)!r},run_name='__main__')"
    )
    return [
        str(python["path"]),
        "-I",
        "-B",
        "-X",
        f"pycache_prefix={cache_path}",
        "-c",
        bootstrap,
        *arguments,
    ]


def _run_generation4_closure_verifier(
    profile: Mapping[str, Any],
    verifier: Mapping[str, Any],
    closure: Mapping[str, Any],
) -> None:
    """Independently recompute the G4 structural-abort seal before use."""

    _, _, python = _forbidden_worker_authority(profile, allow_template=False)
    with tempfile.TemporaryDirectory(prefix="omega-g4-closure-verify-") as directory:
        command = _python_script_command(
            python,
            verifier,
            ["verify", "--output", str(closure["path"])],
            pycache_prefix=Path(directory) / "isolated-python-cache",
        )
        try:
            completed = subprocess.run(
                command,
                cwd=contract.REPO,
                env=_sanitized_python_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=1800,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError(
                "Generation-4 structural-abort verification exceeded 30 minutes"
            ) from error
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError(
            "Generation-4 structural-abort verifier failed: "
            + (completed.stderr.strip() or completed.stdout.strip())[-4000:]
        )
    if "Verified target-opaque Generation-4 structural abort:" not in completed.stdout:
        raise ValueError("Generation-4 verifier omitted its success attestation")
    for label, before in (("verifier", verifier), ("closure", closure)):
        if not contract.exact_json_equal(
            contract.identity(Path(str(before["path"]))), before
        ):
            raise ValueError(f"Generation-4 {label} changed during verification")


def _verify_source_match_completion(profile: Mapping[str, Any]) -> None:
    """Recompute the source seal with its frozen wrapper in pinned Python."""

    identities = contract.mapping(
        profile.get("finalFreezeIdentities"), "source-match final identities"
    )
    expected_paths = {
        "sourceOpeningBuilderSource": CURRENT_REPLAY_PATHS[
            "sourceOpeningBuilderSource"
        ],
        "sourceOpeningSuite": CURRENT_REPLAY_PATHS["sourceOpeningSuite"],
        "sourceMatchConfig": CURRENT_REPLAY_PATHS["sourceMatchConfig"],
        "sourceMatchCompletionSeal": CURRENT_REPLAY_PATHS[
            "sourceMatchCompletionSeal"
        ],
        "sourceMatchHarnessAssembly": CURRENT_REPLAY_PATHS[
            "sourceMatchHarnessAssembly"
        ],
        "rootSamplerAssembly": CURRENT_RUNTIME_PATHS["rootSamplerAssembly"],
        "sourceRootPool": CURRENT_CORPUS_PATHS["sourceRootPool"],
        "sourceRootPoolManifest": CURRENT_CORPUS_PATHS[
            "sourceRootPoolManifest"
        ],
        "sourceRootPoolSeal": CURRENT_CORPUS_PATHS["sourceRootPoolSeal"],
        "sourceEvents": CURRENT_REPLAY_PATHS["sourceEvents"],
    }
    pins = {
        name: _profile_identity(
            identities.get(name),
            path=contract.REPO / relative,
            label=f"source-match {name}",
        )
        for name, relative in expected_paths.items()
    }
    decision_chesslib = _profile_identity(
        identities.get("chessLibAssembly"),
        path=contract.REPO / CURRENT_RUNTIME_PATHS["chessLibAssembly"],
        label="source-match decision ChessLib",
    )
    root_sampler_chesslib_path = contract.resolve(
        Path(str(pins["rootSamplerAssembly"]["path"])).parent / "ChessLib.dll"
    )
    root_sampler_chesslib = contract.identity(root_sampler_chesslib_path)
    if not _same_content_identity(root_sampler_chesslib, decision_chesslib):
        raise ValueError("source-match root-sampler ChessLib differs from frozen ChessLib")

    _, _, python = _forbidden_worker_authority(profile, allow_template=False)
    arguments = [
        "verify",
        "--config",
        str(pins["sourceMatchConfig"]["path"]),
        "--harness",
        str(pins["sourceMatchHarnessAssembly"]["path"]),
        "--root-sampler",
        str(pins["rootSamplerAssembly"]["path"]),
        "--root-sampler-chesslib",
        str(root_sampler_chesslib["path"]),
        "--pool",
        str(pins["sourceRootPool"]["path"]),
        "--pool-manifest",
        str(pins["sourceRootPoolManifest"]["path"]),
        "--pool-seal",
        str(pins["sourceRootPoolSeal"]["path"]),
        "--completion-seal",
        str(pins["sourceMatchCompletionSeal"]["path"]),
    ]
    with tempfile.TemporaryDirectory(prefix="omega-g5-source-verify-") as directory:
        command = _python_script_command(
            python,
            pins["sourceOpeningBuilderSource"],
            arguments,
            pycache_prefix=Path(directory) / "isolated-python-cache",
        )
        try:
            completed = subprocess.run(
                command,
                cwd=contract.REPO,
                env=_sanitized_python_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=3600,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError("source-match completion verification exceeded one hour") from error
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError(
            "frozen source-match completion verifier failed: "
            + (completed.stderr.strip() or completed.stdout.strip())[-4000:]
        )
    if "Verified complete Generation 5 source match:" not in completed.stdout:
        raise ValueError("source-match verifier omitted its completion attestation")
    for name, before in pins.items():
        if not contract.exact_json_equal(
            contract.identity(Path(str(before["path"]))), before
        ):
            raise ValueError(f"source-match {name} changed during verification")
    if not contract.exact_json_equal(
        contract.identity(root_sampler_chesslib_path), root_sampler_chesslib
    ):
        raise ValueError("source-match root-sampler ChessLib changed during verification")


def _prepare_roots_replay_command(
    profile: Mapping[str, Any],
    pins: Mapping[str, Mapping[str, Any]],
    python: Mapping[str, Any],
    output: Path,
) -> tuple[list[str], dict[str, Any]]:
    fresh = contract.mapping(profile.get("freshDecisionCorpus"), "replay fresh corpus")
    source = contract.mapping(fresh.get("source"), "replay source policy")
    quota = contract.mapping(fresh.get("rootQuota"), "replay root quota")
    source_seed = source.get("requiredRunSeed")
    root_seed = source.get("rootSelectionUsesSeed")
    data_profile = source.get("requiredDataProfile")
    roots_per_phase = quota.get("completeRootsPerPhase")
    reserve_per_phase = quota.get("reserveRootsPerPhase")
    if (
        type(source_seed) is not int
        or type(root_seed) is not int
        or type(data_profile) is not str
        or not data_profile
        or type(roots_per_phase) is not int
        or type(reserve_per_phase) is not int
    ):
        raise ValueError("frozen prepare-roots CLI policy changed")
    teacher = _identity_shape(
        pins["decisionTeacherSource"], "prepare-roots teacher source"
    )
    root_sampler_chesslib_path = contract.resolve(
        Path(str(pins["rootSamplerAssembly"]["path"])).parent / "ChessLib.dll"
    )
    root_sampler_chesslib = contract.identity(root_sampler_chesslib_path)
    if not _same_content_identity(root_sampler_chesslib, pins["chessLibAssembly"]):
        raise ValueError("root-sampler ChessLib differs from frozen decision ChessLib")
    engine_sha = pins["teacherEngineExecutable"]["sha256"]
    manifest = Path(str(output) + ".manifest.json")
    arguments = [
        "prepare-roots",
        "--events", str(pins["sourceEvents"]["path"]),
        "--opening-suite", str(pins["sourceOpeningSuite"]["path"]),
        "--source-match-config", str(pins["sourceMatchConfig"]["path"]),
        "--source-match-completion-seal",
        str(pins["sourceMatchCompletionSeal"]["path"]),
        "--source-match-harness", str(pins["sourceMatchHarnessAssembly"]["path"]),
        "--root-sampler", str(pins["rootSamplerAssembly"]["path"]),
        "--root-sampler-chesslib", str(root_sampler_chesslib["path"]),
        "--source-root-pool", str(pins["sourceRootPool"]["path"]),
        "--source-root-pool-manifest", str(pins["sourceRootPoolManifest"]["path"]),
        "--source-root-pool-seal", str(pins["sourceRootPoolSeal"]["path"]),
        "--output", str(output),
        "--manifest", str(manifest),
        "--seed", str(root_seed),
        "--source-seed", str(source_seed),
        "--profile-id", PROFILE_ID,
        "--data-profile-id", data_profile,
        "--freshness-marker", f"g5-source-{source_seed}",
        "--roots-per-phase", str(roots_per_phase),
        "--reserve-per-phase", str(reserve_per_phase),
        "--required-engine-sha256", str(engine_sha),
    ]
    return (
        _python_script_command(
            python,
            teacher,
            arguments,
            pycache_prefix=output.parent / "isolated-python-cache",
        ),
        root_sampler_chesslib,
    )


def _reproduce_current_corpus(
    profile: Mapping[str, Any], pins: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    _, teacher, python = _forbidden_worker_authority(profile, allow_template=False)
    if not contract.exact_json_equal(teacher, pins["decisionTeacherSource"]):
        raise ValueError("prepare-roots teacher authority differs from corpus pin")
    with tempfile.TemporaryDirectory(prefix="omega-g5-corpus-replay-") as directory:
        temporary = Path(directory)
        replay_roots = temporary / "roots.jsonl"
        root_command, root_sampler_chesslib = _prepare_roots_replay_command(
            profile, pins, python, replay_roots
        )
        completed = subprocess.run(
            root_command,
            cwd=temporary,
            env=_sanitized_python_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
        if completed.returncode != 0 or completed.stderr.strip():
            raise ValueError(
                "deterministic prepare-roots replay failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        root_exact = _assert_exact_file_reproduction(
            Path(str(pins["roots"]["path"])),
            pins["roots"],
            replay_roots,
            "current roots",
        )
        original_root_semantics, root_manifest_digest = _root_manifest_semantics(
            Path(str(pins["rootsManifest"]["path"])),
            pins["rootsManifest"],
            "current root manifest",
        )
        replay_root_manifest = Path(str(replay_roots) + ".manifest.json")
        replay_root_pin = contract.identity(replay_root_manifest)
        replay_root_semantics, _ = _root_manifest_semantics(
            replay_root_manifest,
            replay_root_pin,
            "replayed root manifest",
        )
        if not contract.exact_json_equal(
            original_root_semantics, replay_root_semantics
        ):
            raise ValueError("deterministic root manifest semantics changed")

        replay_children = temporary / "children.jsonl"
        child_command = [
            str(pins["dotnetRuntimeHostExecutable"]["path"]),
            str(pins["decisionSamplerAssembly"]["path"]),
            "--input",
            str(pins["roots"]["path"]),
            "--output",
            str(replay_children),
        ]
        child_completed = subprocess.run(
            child_command,
            cwd=temporary,
            env=_sanitized_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
        if child_completed.returncode != 0 or child_completed.stderr.strip():
            raise ValueError(
                "deterministic decision-sampler replay failed: "
                + (child_completed.stderr.strip() or child_completed.stdout.strip())
            )
        child_exact = _assert_exact_file_reproduction(
            Path(str(pins["children"]["path"])),
            pins["children"],
            replay_children,
            "current legal children",
        )
        original_child_semantics, child_manifest_digest = _safe_manifest_semantics(
            Path(str(pins["childrenManifest"]["path"])),
            pins["childrenManifest"],
            "current child manifest",
            normalize_output=True,
        )
        replay_child_manifest = Path(str(replay_children) + ".manifest.json")
        replay_child_pin = contract.identity(replay_child_manifest)
        replay_child_semantics, _ = _safe_manifest_semantics(
            replay_child_manifest,
            replay_child_pin,
            "replayed child manifest",
            normalize_output=True,
        )
        if not contract.exact_json_equal(
            original_child_semantics, replay_child_semantics
        ):
            raise ValueError("deterministic child manifest semantics changed")

        original_completion = Path(
            str(pins["children"]["path"]) + ".complete.seal.json"
        )
        original_completion_pin = contract.identity(original_completion)
        original_completion_semantics, completion_digest = _safe_manifest_semantics(
            original_completion,
            original_completion_pin,
            "current child completion seal",
            normalize_output=True,
            normalize_manifest=True,
        )
        replay_completion = Path(str(replay_children) + ".complete.seal.json")
        replay_completion_pin = contract.identity(replay_completion)
        replay_completion_semantics, _ = _safe_manifest_semantics(
            replay_completion,
            replay_completion_pin,
            "replayed child completion seal",
            normalize_output=True,
            normalize_manifest=True,
        )
        if not contract.exact_json_equal(
            original_completion_semantics, replay_completion_semantics
        ):
            raise ValueError("deterministic child completion semantics changed")
    return {
        "roots": {
            **root_exact,
            "manifestSemanticSha256": root_manifest_digest,
        },
        "children": {
            **child_exact,
            "manifestSemanticSha256": child_manifest_digest,
            "completionSemanticSha256": completion_digest,
        },
        "python": dict(python),
        "decisionTeacher": dict(teacher),
        "rootSamplerChessLib": root_sampler_chesslib,
        "dotnetHost": dict(pins["dotnetRuntimeHostExecutable"]),
    }


def _synthetic_current_corpus_replay_evidence(
    pins: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Shape-compatible evidence for the local fixture; not production trust."""

    _, root_digest = _root_manifest_semantics(
        Path(str(pins["rootsManifest"]["path"])),
        pins["rootsManifest"],
        "synthetic root manifest",
    )
    _, child_digest = _safe_manifest_semantics(
        Path(str(pins["childrenManifest"]["path"])),
        pins["childrenManifest"],
        "synthetic child manifest",
        normalize_output=True,
    )
    completion_path = Path(
        str(pins["children"]["path"]) + ".complete.seal.json"
    )
    completion_pin = contract.identity(completion_path)
    _, completion_digest = _safe_manifest_semantics(
        completion_path,
        completion_pin,
        "synthetic child completion",
        normalize_output=True,
        normalize_manifest=True,
    )
    return {
        "roots": {
            "bytes": pins["roots"]["bytes"],
            "sha256": pins["roots"]["sha256"],
            "manifestSemanticSha256": root_digest,
        },
        "children": {
            "bytes": pins["children"]["bytes"],
            "sha256": pins["children"]["sha256"],
            "manifestSemanticSha256": child_digest,
            "completionSemanticSha256": completion_digest,
        },
        "python": contract.identity(Path(sys.executable)),
        "decisionTeacher": contract.identity(
            contract.REPO / "tools/omega_nnue/omega_decision_teacher_generation5.py"
        ),
        "rootSamplerChessLib": dict(pins["rootSamplerChessLibAssembly"]),
        "dotnetHost": dict(pins["decisionSamplerAssembly"]),
    }


G5_REPLAY_DYNAMIC_NAMES = frozenset(
    {
        "raw-roots.jsonl",
        "raw-roots.jsonl.manifest.json",
        "raw-children.jsonl",
        "raw-children.jsonl.manifest.json",
        "raw-children.jsonl.complete.seal.json",
        "root-feasibility.jsonl",
        "root-feasibility.jsonl.manifest.json",
        "root-feasibility.seal.json",
        "roots.jsonl",
        "roots.jsonl.manifest.json",
        "children.jsonl",
        "children.jsonl.manifest.json",
        "children.jsonl.complete.seal.json",
    }
)


def _normalize_g5_replay_semantics(value: Any, *, key: str = "") -> Any:
    """Remove only nondeterministic timestamps and replay-local identities."""

    if type(value) is dict:
        if set(value) == {"path", "bytes", "sha256"}:
            identity = _identity_shape(value, "replay semantic identity")
            basename = Path(identity["path"]).name
            if basename in G5_REPLAY_DYNAMIC_NAMES:
                return {
                    "path": f"<REPLAY:{basename}>",
                    "bytes": 0,
                    "sha256": "0" * 64,
                }
            return identity
        return {
            name: (
                "<REPLAY-UTC>"
                if name == "createdUtc"
                else _normalize_g5_replay_semantics(item, key=name)
            )
            for name, item in value.items()
        }
    if type(value) is list:
        return [_normalize_g5_replay_semantics(item, key=key) for item in value]
    return value


def _g5_replay_json_semantics(
    path: Path, expected: Mapping[str, Any], label: str
) -> tuple[Any, str]:
    before = _identity_snapshot(path, expected, label)
    value = contract.strict_load(path, label)
    normalized = _normalize_g5_replay_semantics(value)
    if not contract.exact_json_equal(contract.identity(path), before):
        raise ValueError(f"{label} changed while replay semantics were read")
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return normalized, hashlib.sha256(payload).hexdigest()


def _g5_replay_file(
    name: str,
    pins: Mapping[str, Mapping[str, Any]],
    replay: Path,
    *,
    manifest: str | None = None,
    seal: str | None = None,
) -> dict[str, Any]:
    exact = _assert_exact_file_reproduction(
        Path(str(pins[name]["path"])), pins[name], replay, f"current {name}"
    )
    result: dict[str, Any] = dict(exact)
    for suffix, pin_name, field in (
        (".manifest.json", manifest, "manifestSemanticSha256"),
        (".complete.seal.json", seal, "completionSemanticSha256"),
    ):
        if pin_name is None:
            continue
        semantic_reader = (
            _root_manifest_semantics
            if name in {"rawRoots", "roots"} and field == "manifestSemanticSha256"
            else _g5_replay_json_semantics
        )
        original_semantics, digest = semantic_reader(
            Path(str(pins[pin_name]["path"])), pins[pin_name], f"current {pin_name}"
        )
        replay_path = Path(str(replay) + suffix)
        replay_pin = contract.identity(replay_path)
        replay_semantics, _ = semantic_reader(
            replay_path, replay_pin, f"replayed {pin_name}"
        )
        if not contract.exact_json_equal(original_semantics, replay_semantics):
            raise ValueError(f"deterministic {pin_name} semantics changed")
        result[field] = digest
    return result


def _run_clean_replay(command: Sequence[str], cwd: Path, label: str, *, python: bool) -> None:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=_sanitized_python_environment() if python else _sanitized_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError(
            f"deterministic {label} replay failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )


def _reproduce_current_corpus_g5(
    profile: Mapping[str, Any], pins: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Reproduce raw roots, raw children, feasibility, and filtered graph."""

    _, teacher, python = _forbidden_worker_authority(profile, allow_template=False)
    if not contract.exact_json_equal(teacher, pins["decisionTeacherSource"]):
        raise ValueError("G5 decision-teacher authority differs from corpus pin")
    final_identities = contract.mapping(
        profile.get("finalFreezeIdentities"), "G5 replay final identities"
    )
    forbidden_records = final_identities.get("forbiddenPositionCatalogManifests")
    if type(forbidden_records) is not list or not forbidden_records:
        raise ValueError("G5 replay lacks frozen forbidden catalog manifests")
    forbidden_paths: list[str] = []
    for index, record in enumerate(forbidden_records):
        shaped = _identity_shape(record, f"G5 replay forbidden manifest {index}")
        path = _profile_path(shaped["path"], f"G5 replay forbidden manifest {index} path")
        if not contract.exact_json_equal(contract.identity(path), shaped):
            raise ValueError("G5 replay forbidden manifest identity changed")
        forbidden_paths.append(str(path))
    with tempfile.TemporaryDirectory(prefix="omega-g5-corpus-replay-") as directory:
        temporary = Path(directory)
        replay_raw_roots = temporary / "raw-roots.jsonl"
        root_command, root_sampler_chesslib = _prepare_roots_replay_command(
            profile, pins, python, replay_raw_roots
        )
        _run_clean_replay(root_command, temporary, "prepare-roots", python=True)
        raw_roots = _g5_replay_file(
            "rawRoots",
            pins,
            replay_raw_roots,
            manifest="rawRootsManifest",
        )

        replay_raw_children = temporary / "raw-children.jsonl"
        child_command = [
            str(pins["dotnetRuntimeHostExecutable"]["path"]),
            str(pins["decisionSamplerAssembly"]["path"]),
            "--input",
            str(replay_raw_roots),
            "--output",
            str(replay_raw_children),
        ]
        _run_clean_replay(child_command, temporary, "raw-child expansion", python=False)
        raw_children = _g5_replay_file(
            "rawChildren",
            pins,
            replay_raw_children,
            manifest="rawChildrenManifest",
            seal="rawSamplerCompletionSeal",
        )

        replay_feasibility = temporary / "root-feasibility.jsonl"
        replay_feasibility_seal = temporary / "root-feasibility.seal.json"
        replay_roots = temporary / "roots.jsonl"
        replay_children = temporary / "children.jsonl"
        feasibility_arguments = [
            "feasibility-freeze",
            "--raw-roots",
            str(replay_raw_roots),
            "--raw-roots-manifest",
            str(Path(str(replay_raw_roots) + ".manifest.json")),
            "--raw-children",
            str(replay_raw_children),
            "--raw-children-manifest",
            str(Path(str(replay_raw_children) + ".manifest.json")),
            "--raw-sampler-seal",
            str(Path(str(replay_raw_children) + ".complete.seal.json")),
            "--root-feasibility",
            str(replay_feasibility),
            "--root-feasibility-seal",
            str(replay_feasibility_seal),
        ]
        for forbidden_path in forbidden_paths:
            feasibility_arguments.extend(
                ["--forbidden-position-manifest", forbidden_path]
            )
        feasibility_arguments.extend(
            ["--roots", str(replay_roots), "--children", str(replay_children)]
        )
        feasibility_command = _python_script_command(
            python,
            teacher,
            feasibility_arguments,
            pycache_prefix=temporary / "isolated-python-cache-feasibility",
        )
        _run_clean_replay(
            feasibility_command, temporary, "feasibility-freeze", python=True
        )
        feasibility = _g5_replay_file(
            "rootFeasibility",
            pins,
            replay_feasibility,
            manifest="rootFeasibilityManifest",
        )
        original_seal_semantics, feasibility_seal_digest = _g5_replay_json_semantics(
            Path(str(pins["rootFeasibilitySeal"]["path"])),
            pins["rootFeasibilitySeal"],
            "current rootFeasibilitySeal",
        )
        replay_seal_pin = contract.identity(replay_feasibility_seal)
        replay_seal_semantics, _ = _g5_replay_json_semantics(
            replay_feasibility_seal,
            replay_seal_pin,
            "replayed rootFeasibilitySeal",
        )
        if not contract.exact_json_equal(
            original_seal_semantics, replay_seal_semantics
        ):
            raise ValueError("deterministic root-feasibility seal semantics changed")
        feasibility["sealSemanticSha256"] = feasibility_seal_digest
        roots = _g5_replay_file(
            "roots", pins, replay_roots, manifest="rootsManifest"
        )
        children = _g5_replay_file(
            "children",
            pins,
            replay_children,
            manifest="childrenManifest",
            seal="samplerCompletionSeal",
        )

    authority_names = (
        "sourceOpeningBuilderSource",
        "decisionTeacherSource",
        "deepHceV2Source",
        "networkFormatPythonSource",
        "selectScreenSource",
        "priorProjectionSource",
        "trainerSource",
        "preregistrationValidatorSource",
        "generation4AbortVerifierSource",
        "pythonRuntimeToolSource",
        "dotnetRuntimeToolSource",
        "pythonRuntimeManifest",
    )
    return {
        "rawRoots": raw_roots,
        "rawChildren": raw_children,
        "rootFeasibility": feasibility,
        "roots": roots,
        "children": children,
        "python": dict(python),
        "authorities": {name: dict(pins[name]) for name in authority_names},
        "rootSamplerChessLib": root_sampler_chesslib,
        "dotnetHost": dict(pins["dotnetRuntimeHostExecutable"]),
    }


def _synthetic_current_corpus_replay_evidence_g5(
    pins: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    def evidence(
        name: str, *, manifest: str | None = None, seal: str | None = None
    ) -> dict[str, Any]:
        result = {
            "bytes": pins[name]["bytes"],
            "sha256": pins[name]["sha256"],
        }
        if manifest is not None:
            semantic_reader = (
                _root_manifest_semantics
                if name in {"rawRoots", "roots"}
                else _g5_replay_json_semantics
            )
            _, digest = semantic_reader(
                Path(str(pins[manifest]["path"])),
                pins[manifest],
                f"synthetic {manifest}",
            )
            result["manifestSemanticSha256"] = digest
        if seal is not None:
            _, digest = _g5_replay_json_semantics(
                Path(str(pins[seal]["path"])),
                pins[seal],
                f"synthetic {seal}",
            )
            result["completionSemanticSha256"] = digest
        return result

    feasibility = evidence(
        "rootFeasibility", manifest="rootFeasibilityManifest"
    )
    _, seal_digest = _g5_replay_json_semantics(
        Path(str(pins["rootFeasibilitySeal"]["path"])),
        pins["rootFeasibilitySeal"],
        "synthetic rootFeasibilitySeal",
    )
    feasibility["sealSemanticSha256"] = seal_digest
    authority = contract.identity(Path(sys.executable))
    return {
        "rawRoots": evidence("rawRoots", manifest="rawRootsManifest"),
        "rawChildren": evidence(
            "rawChildren",
            manifest="rawChildrenManifest",
            seal="rawSamplerCompletionSeal",
        ),
        "rootFeasibility": feasibility,
        "roots": evidence("roots", manifest="rootsManifest"),
        "children": evidence(
            "children",
            manifest="childrenManifest",
            seal="samplerCompletionSeal",
        ),
        "python": authority,
        "authorities": {
            name: dict(pins.get(name, authority))
            for name in (
                "sourceOpeningBuilderSource",
                "decisionTeacherSource",
                "deepHceV2Source",
                "networkFormatPythonSource",
                "selectScreenSource",
                "priorProjectionSource",
                "trainerSource",
                "preregistrationValidatorSource",
                "generation4AbortVerifierSource",
                "pythonRuntimeToolSource",
                "dotnetRuntimeToolSource",
                "pythonRuntimeManifest",
            )
        },
        "rootSamplerChessLib": dict(pins["rootSamplerChessLibAssembly"]),
        "dotnetHost": dict(pins["decisionSamplerAssembly"]),
    }


class _CurrentCorpusDisjointSet:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            if left_root > right_root:
                left_root, right_root = right_root, left_root
            self.parent[right_root] = left_root


def _expected_g5_root_rank(
    root_id: str, seed: int, phase: str, side: str, group_id: str
) -> str:
    return hashlib.sha256(
        (
            f"omega-g5-feasible-root-v1\0{seed}\0{phase}\0{side}\0"
            f"{group_id}\0{root_id}"
        ).encode("utf-8")
    ).hexdigest()


def _g5_component_ids(
    roots: Mapping[str, Mapping[str, Any]],
    children_by_root: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, str]:
    groups = sorted(str(root["groupId"]) for root in roots.values())
    dsu = _CurrentCorpusDisjointSet(groups)
    source_owner: dict[str, str] = {}
    signature_owner: dict[str, str] = {}

    def observe(group: str, source_game: str, ofen: str) -> None:
        prior_source = source_owner.setdefault(source_game, group)
        dsu.union(group, prior_source)
        _, _, _, _, signatures = core._position_meta(ofen)
        for signature in signatures:
            prior = signature_owner.setdefault(signature, group)
            dsu.union(group, prior)

    for root in roots.values():
        group = str(root["groupId"])
        source_game = str(root["sourceGameId"])
        observe(group, source_game, str(root["ofen"]))
        for child in children_by_root.get(str(root["rootId"]), ()):
            observe(group, source_game, str(child["parentOfen"]))
            observe(group, source_game, str(child["childOfen"]))
    members: dict[str, list[str]] = {}
    for group in groups:
        members.setdefault(dsu.find(group), []).append(group)
    component_for_root = {
        representative: "raw-decision-component:"
        + hashlib.sha256(
            (
                "omega-raw-decision-component-v2\0"
                + "\0".join(sorted(component_members))
            ).encode("utf-8")
        ).hexdigest()
        for representative, component_members in members.items()
    }
    return {
        group: component_for_root[dsu.find(group)] for group in groups
    }


def _expected_g5_producer(
    profile: Mapping[str, Any],
    pins: Mapping[str, Mapping[str, Any]],
    python: Mapping[str, Any],
    *,
    synthetic: bool,
) -> dict[str, Any]:
    if synthetic:
        version_detail = sys.version
    else:
        manifest = _load_pinned_top_level_allowlist(
            Path(str(pins["pythonRuntimeManifest"]["path"])),
            pins["pythonRuntimeManifest"],
            "G5 producer Python runtime manifest",
            field_inventory={
                "schemaVersion",
                "kind",
                "profileId",
                "status",
                "createdUtc",
                "runtime",
                "informationBoundary",
                "finalStageSeal",
            },
            decode_fields={"schemaVersion", "kind", "profileId", "runtime"},
        )
        runtime = contract.mapping(manifest.get("runtime"), "G5 producer runtime")
        python_record = contract.mapping(runtime.get("python"), "G5 producer Python")
        version_detail = _require_nonempty_string(
            python_record.get("versionDetail"), "G5 producer Python versionDetail"
        )
        if not contract.exact_json_equal(python_record.get("executable"), python):
            raise ValueError("G5 producer Python differs from frozen runtime")
    return {
        "omegaDecisionTeacher": dict(pins["decisionTeacherSource"]),
        "deepHceV2": dict(pins["deepHceV2Source"]),
        "omegaNnue": dict(pins["networkFormatPythonSource"]),
        "selectScreen": dict(pins["selectScreenSource"]),
        "generation5PreregistrationValidator": dict(
            pins["preregistrationValidatorSource"]
        ),
        "generation4AbortVerifier": dict(
            pins["generation4AbortVerifierSource"]
        ),
        "priorProjection": dict(pins["priorProjectionSource"]),
        "python": {**dict(python), "version": version_detail},
    }


def _scan_pinned_jsonl(
    path: Path,
    expected_identity: Mapping[str, Any],
    expected_fields: frozenset[str],
    label: str,
) -> Iterator[tuple[int, dict[str, Any]]]:
    before = _identity_snapshot(path, expected_identity, label)
    yield from _iter_target_blind_jsonl(path, expected_fields, label)
    after = contract.identity(path)
    if not contract.exact_json_equal(before, after):
        raise ValueError(f"{label} changed while being scanned")


def _require_nonempty_string(value: Any, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty canonical string")
    return value


def _require_sha256_string(value: Any, label: str) -> str:
    if type(value) is not str or contract.HEX_256.fullmatch(value) is None:
        raise ValueError(f"{label} must be an exact lowercase SHA-256 string")
    return value


def _require_canonical_utc(value: Any, label: str) -> str:
    if type(value) is not str or CANONICAL_UTC.fullmatch(value) is None:
        raise ValueError(f"{label} must use canonical UTC JSON syntax")
    contract.parse_utc(value, label)
    return value


def _current_source_pool(
    pins: Mapping[str, Mapping[str, Any]],
    *,
    source_seed: int,
    exact_keys: set[str],
    exclusion_signatures: set[str],
    pool_exact_keys: set[str],
    pool_signatures: set[str],
) -> dict[str, Any]:
    manifest = _load_pinned_json(
        Path(pins["sourceRootPoolManifest"]["path"]),
        pins["sourceRootPoolManifest"],
        "current-corpus source-root-pool manifest",
    )
    if set(manifest) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "policy",
        "coverage",
        "runtime",
        "output",
        "finalStageSeal",
    }:
        raise ValueError("current-corpus source-pool manifest fields changed")
    policy = contract.mapping(manifest.get("policy"), "source-pool policy")
    expected_policy = {
        "deterministicPrng": "SplitMix64",
        "seed": str(source_seed),
        "trajectoryPairs": 10240,
        "independentTrajectoriesPerPair": 2,
        "workers": 4,
        "maxPlies": 220,
        "positionsPerPhaseAndSide": 2,
        "captureSelectionPercent": 72,
        "terminalRootsEmitted": 0,
        "maximumHalfmoveClock": 89,
        "minimumPieces": 7,
        "minimumPiecesPerSide": 2,
        "phasePlyWindows": SOURCE_POOL_PHASE_WINDOWS,
    }
    if (
        type(manifest.get("schemaVersion")) is not int
        or manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("kind") != "omega-rules-only-random-root-manifest"
        or manifest.get("finalStageSeal") is not False
        or not contract.exact_json_equal(policy, expected_policy)
        or not contract.exact_json_equal(manifest.get("output"), pins["sourceRootPool"])
    ):
        raise ValueError("current-corpus source-pool manifest contract changed")
    _require_canonical_utc(
        manifest.get("createdUtc"), "source-root-pool manifest createdUtc"
    )
    runtime = contract.mapping(manifest.get("runtime"), "source-pool runtime")
    if set(runtime) != {"framework", "samplerAssembly", "chessLibAssembly"}:
        raise ValueError("current-corpus source-pool runtime fields changed")
    if (
        type(runtime.get("framework")) is not str
        or not runtime["framework"]
        or not contract.exact_json_equal(
            runtime.get("samplerAssembly"), pins["rootSamplerAssembly"]
        )
        or not _same_content_identity(
            runtime.get("chessLibAssembly"), pins["chessLibAssembly"]
        )
    ):
        raise ValueError("current-corpus source-pool runtime identity changed")

    seal = _load_pinned_json(
        Path(pins["sourceRootPoolSeal"]["path"]),
        pins["sourceRootPoolSeal"],
        "current-corpus source-root-pool completion seal",
    )
    if set(seal) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "output",
        "manifest",
        "producer",
        "finalStageSeal",
    }:
        raise ValueError("current-corpus source-pool seal fields changed")
    producer = contract.mapping(seal.get("producer"), "source-pool producer")
    if (
        type(seal.get("schemaVersion")) is not int
        or seal.get("schemaVersion") != SCHEMA_VERSION
        or seal.get("kind")
        != "omega-rules-only-random-root-completion-seal"
        or seal.get("finalStageSeal") is not True
        or not contract.exact_json_equal(seal.get("output"), pins["sourceRootPool"])
        or not contract.exact_json_equal(
            seal.get("manifest"), pins["sourceRootPoolManifest"]
        )
        or set(producer) != {"samplerAssembly", "chessLibAssembly", "framework"}
        or type(producer.get("framework")) is not str
        or not producer["framework"]
        or not contract.exact_json_equal(
            producer.get("samplerAssembly"), pins["rootSamplerAssembly"]
        )
        or not _same_content_identity(
            producer.get("chessLibAssembly"), pins["chessLibAssembly"]
        )
    ):
        raise ValueError("current-corpus source-pool completion contract changed")
    _require_canonical_utc(
        seal.get("createdUtc"), "source-root-pool seal createdUtc"
    )

    pairs: set[str] = set()
    trajectories: set[str] = set()
    raw_pairs: set[str] = set()
    raw_trajectories: set[str] = set()
    rows = 0
    phase_counts: Counter[str] = Counter()
    side_counts: Counter[str] = Counter()
    maximum_ply = 0
    path = Path(pins["sourceRootPool"]["path"])
    for line_number, record in _scan_pinned_jsonl(
        path,
        pins["sourceRootPool"],
        frozenset(SAMPLER_RECORD_FIELDS),
        "current-corpus source-root pool",
    ):
        if (
            type(record.get("schemaVersion")) is not int
            or record.get("schemaVersion") != SCHEMA_VERSION
            or record.get("kind") != "omega-rules-only-random-root"
            or record.get("generatorSeed") != str(source_seed)
        ):
            raise ValueError(
                f"{path}:{line_number}: source-pool row envelope changed"
            )
        if (
            type(record.get("generatorSeed")) is not str
            or record.get("generatorSeed") != str(source_seed)
            or type(record.get("trajectorySeed")) is not str
            or not record["trajectorySeed"].isdigit()
            or not 0 <= int(record["trajectorySeed"]) <= UINT64_MASK
            or type(record.get("ply")) is not int
            or not 0 <= record["ply"] <= 220
            or type(record.get("halfmoveClock")) is not int
            or not 0 <= record["halfmoveClock"] <= 89
            or type(record.get("selectionRank")) is not str
            or contract.HEX_256.fullmatch(record["selectionRank"]) is None
        ):
            raise ValueError(
                f"{path}:{line_number}: source-pool scalar metadata changed"
            )
        pair_id = _require_nonempty_string(
            record.get("trajectoryPairId"), "source-pool trajectoryPairId"
        )
        trajectory_id = _require_nonempty_string(
            record.get("trajectoryId"), "source-pool trajectoryId"
        )
        flavor = record.get("flavor")
        match = CURRENT_SOURCE_TRAJECTORY.fullmatch(trajectory_id)
        if (
            SAMPLER_PAIR_ID.fullmatch(pair_id) is None
            or match is None
            or match.group(1) != pair_id
            or flavor not in {"ab", "ba"}
            or match.group(2) != flavor
        ):
            raise ValueError(
                f"{path}:{line_number}: source-pool trajectory identity changed"
            )
        pair_number = int(pair_id.rsplit("-", 1)[1])
        if not 1 <= pair_number <= 10240:
            raise ValueError(
                f"{path}:{line_number}: source-pool pair is outside frozen range"
            )
        ofen = _canonical_ofen(record.get("ofen"), "source-pool OFEN")
        phase, side, exact, _, signatures = core._position_meta(ofen)
        phase_minimum, phase_maximum = SOURCE_POOL_PHASE_WINDOWS[phase]
        if record.get("phase") != phase or record.get("sideToMove") != side:
            raise ValueError(
                f"{path}:{line_number}: source-pool OFEN metadata changed"
            )
        if (
            not phase_minimum <= record["ply"] <= min(phase_maximum, 220)
            or record["halfmoveClock"] != int(ofen.split()[4])
        ):
            raise ValueError(
                f"{path}:{line_number}: source-pool ply/clock metadata changed"
            )
        pieces, _, _ = core.parse_ofen(ofen)
        expected_piece_counts = {
            "pieceCount": len(pieces),
            "whitePieces": sum(piece_side == 0 for _, piece_side, _ in pieces),
            "blackPieces": sum(piece_side != 0 for _, piece_side, _ in pieces),
            "champions": sum(piece == 6 for piece, _, _ in pieces),
            "wizards": sum(piece == 7 for piece, _, _ in pieces),
        }
        if any(
            type(record.get(name)) is not int or record.get(name) != expected
            for name, expected in expected_piece_counts.items()
        ):
            raise ValueError(
                f"{path}:{line_number}: source-pool piece metadata changed"
            )
        exact_keys.add(exact)
        exclusion_signatures.update(signatures)
        exclusion_signatures.add(exact)
        pool_exact_keys.add(exact)
        pool_signatures.update(signatures)
        pool_signatures.add(exact)
        raw_pairs.add(pair_id)
        raw_trajectories.add(trajectory_id)
        pairs.add(_sampler_pair_identity(source_seed, pair_id))
        trajectories.add(_sampler_trajectory_identity(source_seed, trajectory_id))
        rows += 1
        phase_counts[phase] += 1
        side_counts[side] += 1
        maximum_ply = max(maximum_ply, record["ply"])
    if rows == 0:
        raise ValueError("current-corpus source-root pool is empty")
    coverage = contract.mapping(manifest.get("coverage"), "source-pool coverage")
    if set(coverage) != {
        "records",
        "phaseCounts",
        "sideToMoveCounts",
        "terminalTrajectories",
        "maxPlyReached",
        "promotionSelections",
        "enPassantClassification",
    }:
        raise ValueError("current-corpus source-pool coverage fields changed")
    promotions = contract.mapping(
        coverage.get("promotionSelections"), "source-pool promotion selections"
    )
    if (
        type(coverage.get("records")) is not int
        or coverage.get("records") != rows
        or not contract.exact_json_equal(
            coverage.get("phaseCounts"),
            {phase: phase_counts[phase] for phase in PHASES},
        )
        or not contract.exact_json_equal(
            coverage.get("sideToMoveCounts"),
            {side: side_counts[side] for side in ("w", "b")},
        )
        or type(coverage.get("terminalTrajectories")) is not int
        or not 0 <= coverage["terminalTrajectories"] <= 16384
        or type(coverage.get("maxPlyReached")) is not int
        or not maximum_ply <= coverage["maxPlyReached"] <= 220
        or set(promotions) != set("qrbncw")
        or any(type(value) is not int or value < 0 for value in promotions.values())
        or coverage.get("enPassantClassification")
        != (
            "An en-passant move lands on an empty target and remains in the "
            "ordinary move pool; legality still comes from ChessLib."
        )
    ):
        raise ValueError("current-corpus source-pool coverage changed")
    return {
        "rows": rows,
        "pairs": pairs,
        "trajectories": trajectories,
        "rawPairs": raw_pairs,
        "rawTrajectories": raw_trajectories,
    }


def _current_corpus_exclusion(
    profile: Mapping[str, Any], historical: set[str]
) -> tuple[set[str], dict[str, set[str]], dict[str, Any]]:
    """Recompute the complete final-profile-pinned G5 position boundary."""

    pins = _current_corpus_pins(profile)
    return _current_corpus_exclusion_from_pins(profile, historical, pins)


def _current_corpus_exclusion_from_pins(
    profile: Mapping[str, Any],
    historical: set[str],
    pins: Mapping[str, Mapping[str, Any]],
) -> tuple[set[str], dict[str, set[str]], dict[str, Any]]:
    fresh = contract.mapping(
        profile.get("freshDecisionCorpus"), "fresh decision corpus"
    )
    source_contract = contract.mapping(fresh.get("source"), "fresh corpus source")
    quota = contract.mapping(fresh.get("rootQuota"), "fresh corpus root quota")
    source_seed = source_contract.get("requiredRunSeed")
    expected_roots = quota.get("maximumCandidateRoots")
    if type(source_seed) is not int or type(expected_roots) is not int:
        raise ValueError("fresh-corpus source seed/root quota has the wrong JSON type")
    identities_value = profile.get("finalFreezeIdentities")
    if type(identities_value) is dict:
        identities = contract.mapping(
            identities_value, "current-corpus final identities"
        )
        teacher_engine = _profile_identity(
            identities.get("teacherEngineExecutable"),
            path=contract.REPO
            / "tools/omega_nnue/frozen_runtime/king-state-v5/engine/senpai.exe",
            label="current-corpus teacher engine",
        )
        expected_engine_sha = teacher_engine["sha256"]
        deterministic_replay = _reproduce_current_corpus(profile, pins)
    else:
        expected_engine_sha = profile.get("_selfTestTeacherEngineSha256")
        if (
            type(expected_engine_sha) is not str
            or contract.HEX_256.fullmatch(expected_engine_sha) is None
        ):
            raise ValueError("current-corpus teacher engine identity is absent")
        deterministic_replay = _synthetic_current_corpus_replay_evidence(pins)

    root_manifest_fields = {
        "schemaVersion",
        "kind",
        "createdUtc",
        "profileId",
        "freshnessMarker",
        "policy",
        "coverage",
        "sources",
        "producer",
        "finalStageSeal",
        "output",
    }
    root_manifest = _load_pinned_top_level_allowlist(
        Path(pins["rootsManifest"]["path"]),
        pins["rootsManifest"],
        "current-corpus root manifest",
        field_inventory=root_manifest_fields,
        decode_fields={
            "schemaVersion",
            "kind",
            "createdUtc",
            "profileId",
            "freshnessMarker",
            "policy",
            "coverage",
            "finalStageSeal",
            "output",
        },
    )
    root_policy = contract.mapping(root_manifest.get("policy"), "root policy")
    root_seed = source_contract.get("rootSelectionUsesSeed")
    complete_per_phase = quota.get("completeRootsPerPhase")
    reserve_per_phase = quota.get("reserveRootsPerPhase")
    if any(
        type(item) is not int
        for item in (root_seed, complete_per_phase, reserve_per_phase)
    ):
        raise ValueError("root-selection policy is absent from the profile")
    expected_root_policy = {
        "sourceSeed": source_seed,
        "seed": root_seed,
        "rootsPerPhase": complete_per_phase,
        "reservePerPhase": reserve_per_phase,
        "maximumRootsPerSourceGroup": 1,
        "primaryPerPhaseAndSide": complete_per_phase // 2,
        "reservePerPhaseAndSide": reserve_per_phase // 2,
        "selection": "target-blind SHA-256 rank",
        "source": "latest complete HCE-only OmegaMatch attempt",
        "sourceGrouping": (
            "one root maximum per pinned opening Source provenance tag; "
            "the complete AB/BA pair stays indivisible"
        ),
        "equivalentAbBaPolicy": (
            "same-group exact inputs with the same move are deterministically "
            "collapsed; conflicts abort"
        ),
        "requiredHceOptions": ROOT_HCE_OPTIONS,
        "requiredEngineSha256": expected_engine_sha,
    }
    if (
        type(root_manifest.get("schemaVersion")) is not int
        or root_manifest.get("schemaVersion") != SCHEMA_VERSION
        or root_manifest.get("kind") != "omega-decision-root-manifest"
        or root_manifest.get("profileId") != PROFILE_ID
        or root_manifest.get("freshnessMarker") != f"g5-source-{source_seed}"
        or root_manifest.get("finalStageSeal") is not True
        or not contract.exact_json_equal(root_policy, expected_root_policy)
        or not contract.exact_json_equal(root_manifest.get("output"), pins["roots"])
    ):
        raise ValueError("current-corpus root manifest contract changed")
    _require_canonical_utc(
        root_manifest.get("createdUtc"), "current root manifest createdUtc"
    )

    roots: dict[str, dict[str, Any]] = {}
    root_exact: dict[str, str] = {}
    exact_keys: set[str] = set()
    exclusion_signatures: set[str] = set()
    root_signatures: set[str] = set()
    child_signatures: set[str] = set()
    pool_exact_keys: set[str] = set()
    pool_signatures: set[str] = set()
    group_ids: set[str] = set()
    source_game_ids: set[str] = set()
    source_pair_ids: set[str] = set()
    source_run_ids: set[str] = set()
    provenance_pairs: set[str] = set()
    role_phase_counts: Counter[tuple[str, str]] = Counter()
    role_phase_side_counts: Counter[tuple[str, str, str]] = Counter()
    exact_owner: dict[str, str] = {}
    root_path = Path(pins["roots"]["path"])
    for line_number, root in _scan_pinned_jsonl(
        root_path,
        pins["roots"],
        CURRENT_ROOT_FIELDS,
        "current-corpus roots",
    ):
        if (
            type(root.get("schemaVersion")) is not int
            or root.get("schemaVersion") != SCHEMA_VERSION
            or root.get("kind") != "omega-hce-on-policy-root"
        ):
            raise ValueError(f"{root_path}:{line_number}: root envelope changed")
        root_id = _require_nonempty_string(root.get("rootId"), "rootId")
        group_id = _require_nonempty_string(root.get("groupId"), "groupId")
        source_game = _require_nonempty_string(
            root.get("sourceGameId"), "sourceGameId"
        )
        source_pair = _require_nonempty_string(
            root.get("sourcePairId"), "sourcePairId"
        )
        source_run = _require_nonempty_string(root.get("sourceRunId"), "sourceRunId")
        provenance = _require_nonempty_string(
            root.get("sourceProvenanceTag"), "sourceProvenanceTag"
        )
        provenance_match = CURRENT_SOURCE_PAIR_TAG.fullmatch(provenance)
        if provenance_match is None:
            raise ValueError(
                f"{root_path}:{line_number}: source provenance tag changed"
            )
        if root_id in roots:
            raise ValueError(f"{root_path}:{line_number}: duplicate rootId")
        if any(
            type(root.get(name)) is not int or root[name] < 0
            for name in ("sourceAttempt", "sourcePly", "sourceLine")
        ):
            raise ValueError(f"{root_path}:{line_number}: root integer metadata changed")
        for name in (
            "sourceOpeningId",
            "sourceEngineId",
            "rootPvMove",
            "selectionRank",
        ):
            _require_nonempty_string(root.get(name), f"root {name}")
        if (
            _require_sha256_string(
                root.get("sourceEngineSha256"), "root sourceEngineSha256"
            ) != expected_engine_sha
            or type(root.get("selectionRank")) is not str
            or contract.HEX_256.fullmatch(root["selectionRank"]) is None
            or root.get("candidateRole") not in {"primary", "reserve"}
        ):
            raise ValueError(f"{root_path}:{line_number}: root rank/role changed")
        ofen = _canonical_ofen(root.get("ofen"), "current root OFEN")
        phase, side, exact, _, signatures = core._position_meta(ofen)
        if root.get("phase") != phase or root.get("sideToMove") != side:
            raise ValueError(f"{root_path}:{line_number}: root OFEN metadata changed")
        prior_owner = exact_owner.setdefault(exact, group_id)
        if prior_owner != group_id:
            raise ValueError("current exact root position crosses source groups")
        roots[root_id] = dict(root)
        root_exact[root_id] = exact
        exact_keys.add(exact)
        exclusion_signatures.add(exact)
        exclusion_signatures.update(signatures)
        root_signatures.add(exact)
        root_signatures.update(signatures)
        group_ids.add(group_id)
        source_game_ids.add(source_game)
        source_pair_ids.add(source_pair)
        source_run_ids.add(source_run)
        provenance_pairs.add(
            _sampler_pair_identity(source_seed, provenance_match.group(1))
        )
        role_phase_counts[(phase, str(root["candidateRole"]))] += 1
        role_phase_side_counts[(
            phase, side, str(root["candidateRole"])
        )] += 1
    if len(roots) != expected_roots:
        raise ValueError(
            f"current root count changed: {len(roots)} != {expected_roots}"
        )
    if len(group_ids) != len(roots) or len(provenance_pairs) != len(roots):
        raise ValueError("current roots are not one-per-source-group/trajectory-pair")
    expected_primary = quota.get("completeRootsPerPhase")
    expected_reserve = quota.get("reserveRootsPerPhase")
    if type(expected_primary) is not int or type(expected_reserve) is not int:
        raise ValueError("current root role quotas have the wrong JSON type")
    for phase in PHASES:
        if (
            role_phase_counts[(phase, "primary")] != expected_primary
            or role_phase_counts[(phase, "reserve")] != expected_reserve
        ):
            raise ValueError(f"current {phase} root role quota changed")
        for side in ("w", "b"):
            if (
                role_phase_side_counts[(phase, side, "primary")]
                != expected_primary // 2
                or role_phase_side_counts[(phase, side, "reserve")]
                != expected_reserve // 2
            ):
                raise ValueError(
                    f"current {phase}/{side} root role quota changed"
                )
    root_coverage = contract.mapping(root_manifest.get("coverage"), "root coverage")
    if set(root_coverage) != {
        "records",
        "phaseCounts",
        "phaseSideCounts",
        "primaryPhaseSideCounts",
        "primary",
        "reserve",
        "uniqueRootIds",
        "sourceGroups",
        "oneRootPerSourceGroup",
        "collapsedEquivalentAbBaRecords",
    }:
        raise ValueError("current root manifest coverage fields changed")
    if (
        type(root_coverage.get("records")) is not int
        or root_coverage.get("records") != len(roots)
        or type(root_coverage.get("uniqueRootIds")) is not int
        or root_coverage.get("uniqueRootIds") != len(roots)
        or type(root_coverage.get("sourceGroups")) is not int
        or root_coverage.get("sourceGroups") != len(group_ids)
        or root_coverage.get("oneRootPerSourceGroup") is not True
        or not contract.exact_json_equal(
            root_coverage.get("phaseCounts"),
            {
                phase: complete_per_phase + reserve_per_phase
                for phase in PHASES
            },
        )
        or not contract.exact_json_equal(
            root_coverage.get("phaseSideCounts"),
            {
                f"{phase}/{side}": (
                    complete_per_phase + reserve_per_phase
                ) // 2
                for phase in PHASES
                for side in ("w", "b")
            },
        )
        or not contract.exact_json_equal(
            root_coverage.get("primaryPhaseSideCounts"),
            {
                f"{phase}/{side}": role_phase_side_counts[
                    (phase, side, "primary")
                ]
                for phase in PHASES
                for side in ("w", "b")
            },
        )
        or type(root_coverage.get("primary")) is not int
        or root_coverage.get("primary") != complete_per_phase * len(PHASES)
        or type(root_coverage.get("reserve")) is not int
        or root_coverage.get("reserve") != reserve_per_phase * len(PHASES)
        or type(root_coverage.get("collapsedEquivalentAbBaRecords")) is not int
        or root_coverage["collapsedEquivalentAbBaRecords"] < 0
    ):
        raise ValueError("current root manifest coverage changed")

    child_manifest = _load_pinned_json(
        Path(pins["childrenManifest"]["path"]),
        pins["childrenManifest"],
        "current-corpus child manifest",
    )
    if set(child_manifest) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "policy",
        "coverage",
        "input",
        "output",
        "runtime",
        "finalStageSeal",
    }:
        raise ValueError("current-corpus child manifest fields changed")
    child_runtime = contract.mapping(child_manifest.get("runtime"), "child runtime")
    if (
        type(child_manifest.get("schemaVersion")) is not int
        or child_manifest.get("schemaVersion") != SCHEMA_VERSION
        or child_manifest.get("kind") != "omega-decision-sampler-manifest"
        or child_manifest.get("finalStageSeal") is not False
        or not contract.exact_json_equal(
            child_manifest.get("policy"), CHILD_POLICY
        )
        or not contract.exact_json_equal(child_manifest.get("input"), pins["roots"])
        or not contract.exact_json_equal(
            child_manifest.get("output"), pins["children"]
        )
        or set(child_runtime) != {"framework", "samplerAssembly", "chessLibAssembly"}
        or type(child_runtime.get("framework")) is not str
        or not child_runtime["framework"]
        or not contract.exact_json_equal(
            child_runtime.get("samplerAssembly"), pins["decisionSamplerAssembly"]
        )
        or not contract.exact_json_equal(
            child_runtime.get("chessLibAssembly"), pins["chessLibAssembly"]
        )
    ):
        raise ValueError("current-corpus child manifest contract changed")
    _require_canonical_utc(
        child_manifest.get("createdUtc"), "current child manifest createdUtc"
    )

    child_ids: set[str] = set()
    child_counts: Counter[str] = Counter()
    child_ordinals: dict[str, set[int]] = {}
    child_moves: dict[str, list[tuple[int, str]]] = {}
    child_path = Path(pins["children"]["path"])
    for line_number, child in _scan_pinned_jsonl(
        child_path,
        pins["children"],
        CURRENT_CHILD_FIELDS,
        "current-corpus children",
    ):
        if (
            type(child.get("schemaVersion")) is not int
            or child.get("schemaVersion") != SCHEMA_VERSION
            or child.get("kind") != "omega-legal-child"
        ):
            raise ValueError(f"{child_path}:{line_number}: child envelope changed")
        child_id = _require_nonempty_string(child.get("childId"), "childId")
        root_id = _require_nonempty_string(child.get("rootId"), "child rootId")
        root = roots.get(root_id)
        if root is None or child_id in child_ids:
            raise ValueError(f"{child_path}:{line_number}: unknown root/duplicate child")
        if any(
            child.get(field) != root.get(root_field)
            for field, root_field in (
                ("groupId", "groupId"),
                ("sourceGameId", "sourceGameId"),
                ("phase", "phase"),
                ("rootPvMove", "rootPvMove"),
                ("candidateRole", "candidateRole"),
                ("selectionRank", "selectionRank"),
                ("parentOfen", "ofen"),
            )
        ):
            raise ValueError(f"{child_path}:{line_number}: child/root binding changed")
        parent = _canonical_ofen(child.get("parentOfen"), "child parent OFEN")
        parent_phase, parent_side, parent_exact, _, _ = core._position_meta(parent)
        if (
            parent_exact != root_exact[root_id]
            or parent_phase != root["phase"]
            or child.get("parentSideToMove") != parent_side
        ):
            raise ValueError(f"{child_path}:{line_number}: child parent metadata changed")
        child_ofen = _canonical_ofen(child.get("childOfen"), "child OFEN")
        _, child_side, exact, _, signatures = core._position_meta(child_ofen)
        ordinal = child.get("moveOrdinal")
        move = _require_nonempty_string(child.get("move"), "child move")
        if (
            child.get("childSideToMove") != child_side
            or type(ordinal) is not int
            or ordinal < 0
            or type(child.get("isPromotion")) is not bool
            or child.get("isPromotion") != (len(move) == 5)
        ):
            raise ValueError(f"{child_path}:{line_number}: child metadata changed")
        expected_child_id = hashlib.sha256(
            (
                "omega-decision-child-v1\0"
                f"{root_id}\0{root['groupId']}\0{move}\0{child_ofen}"
            ).encode("utf-8")
        ).hexdigest()
        if child_id != expected_child_id:
            raise ValueError(
                f"{child_path}:{line_number}: stable childId changed"
            )
        child_ids.add(child_id)
        child_counts[root_id] += 1
        child_ordinals.setdefault(root_id, set()).add(ordinal)
        child_moves.setdefault(root_id, []).append((ordinal, move))
        exact_keys.add(exact)
        exclusion_signatures.add(exact)
        exclusion_signatures.update(signatures)
        child_signatures.add(exact)
        child_signatures.update(signatures)
    if set(child_counts) != set(roots) or any(count <= 0 for count in child_counts.values()):
        raise ValueError("current legal-child expansion omitted a root")
    if any(
        child_ordinals[root_id] != set(range(child_counts[root_id]))
        for root_id in roots
    ):
        raise ValueError("current legal-child ordinals are not complete")
    if any(
        [move for _, move in sorted(child_moves[root_id])]
        != sorted(move for _, move in child_moves[root_id])
        for root_id in roots
    ):
        raise ValueError("current legal-child move order changed")
    child_coverage = contract.mapping(child_manifest.get("coverage"), "child coverage")
    if set(child_coverage) != {
        "roots",
        "children",
        "zeroChildRoots",
        "minimumChildrenPerRoot",
        "maximumChildrenPerRoot",
        "phaseCounts",
        "sideToMoveCounts",
        "uniqueRootIds",
        "uniqueChildIds",
    }:
        raise ValueError("current child manifest coverage fields changed")
    minimum_children = min(child_counts.values())
    maximum_children = max(child_counts.values())
    root_phase_counts = Counter(str(root["phase"]) for root in roots.values())
    root_side_counts = Counter(str(root["sideToMove"]) for root in roots.values())
    if (
        type(child_coverage.get("roots")) is not int
        or child_coverage.get("roots") != len(roots)
        or type(child_coverage.get("uniqueRootIds")) is not int
        or child_coverage.get("uniqueRootIds") != len(roots)
        or type(child_coverage.get("children")) is not int
        or child_coverage.get("children") != len(child_ids)
        or type(child_coverage.get("uniqueChildIds")) is not int
        or child_coverage.get("uniqueChildIds") != len(child_ids)
        or type(child_coverage.get("zeroChildRoots")) is not int
        or child_coverage.get("zeroChildRoots") != 0
        or type(child_coverage.get("minimumChildrenPerRoot")) is not int
        or child_coverage.get("minimumChildrenPerRoot") != minimum_children
        or type(child_coverage.get("maximumChildrenPerRoot")) is not int
        or child_coverage.get("maximumChildrenPerRoot") != maximum_children
        or not contract.exact_json_equal(
            child_coverage.get("phaseCounts"),
            {phase: root_phase_counts[phase] for phase in PHASES},
        )
        or not contract.exact_json_equal(
            child_coverage.get("sideToMoveCounts"),
            {side: root_side_counts[side] for side in ("w", "b")},
        )
    ):
        raise ValueError("current child manifest coverage changed")

    completion_path = Path(str(child_path) + ".complete.seal.json")
    completion_before = contract.identity(completion_path)
    completion = _load_pinned_json(
        completion_path,
        completion_before,
        "current-corpus sampler completion seal",
    )
    completion_after = contract.identity(completion_path)
    completion_producer = contract.mapping(
        completion.get("producer"), "current sampler completion producer"
    )
    if (
        not contract.exact_json_equal(completion_before, completion_after)
        or set(completion)
        != {
            "schemaVersion",
            "kind",
            "createdUtc",
            "input",
            "output",
            "manifest",
            "producer",
            "finalStageSeal",
        }
        or type(completion.get("schemaVersion")) is not int
        or completion.get("schemaVersion") != SCHEMA_VERSION
        or completion.get("kind") != "omega-decision-sampler-completion-seal"
        or completion.get("finalStageSeal") is not True
        or not contract.exact_json_equal(completion.get("input"), pins["roots"])
        or not contract.exact_json_equal(completion.get("output"), pins["children"])
        or not contract.exact_json_equal(
            completion.get("manifest"), pins["childrenManifest"]
        )
        or set(completion_producer)
        != {"samplerAssembly", "chessLibAssembly", "framework"}
        or type(completion_producer.get("framework")) is not str
        or not completion_producer["framework"]
        or not contract.exact_json_equal(
            completion_producer.get("samplerAssembly"),
            pins["decisionSamplerAssembly"],
        )
        or not contract.exact_json_equal(
            completion_producer.get("chessLibAssembly"), pins["chessLibAssembly"]
        )
    ):
        raise ValueError("current sampler completion seal changed")
    _require_canonical_utc(
        completion.get("createdUtc"), "current sampler completion createdUtc"
    )

    pool = _current_source_pool(
        pins,
        source_seed=source_seed,
        exact_keys=exact_keys,
        exclusion_signatures=exclusion_signatures,
        pool_exact_keys=pool_exact_keys,
        pool_signatures=pool_signatures,
    )
    if not provenance_pairs.issubset(pool["pairs"]):
        raise ValueError("current roots do not map into the pinned source-root pool")
    historical_intersection = historical.intersection(exclusion_signatures)
    union = historical | exclusion_signatures
    identity_audit = {
        name: dict(pins[name]) for name in CURRENT_CORPUS_PATHS
    }
    identity_audit["samplerCompletionSeal"] = completion_after
    public = {
        "identities": identity_audit,
        "deterministicReplay": deterministic_replay,
        "records": {
            "sourcePoolStates": pool["rows"],
            "roots": len(roots),
            "children": len(child_ids),
            "sourceGroups": len(group_ids),
            "sourceGameIds": len(source_game_ids),
            "sourcePairIds": len(source_pair_ids),
            "sourceRunIds": len(source_run_ids),
            "sourceTrajectoryPairs": len(pool["pairs"]),
            "sourceTrajectories": len(pool["trajectories"]),
        },
        "positions": {
            "sourcePoolExactPositionKeys": len(pool_exact_keys),
            "sourcePoolExclusionSignatures": len(pool_signatures),
            "rootExclusionSignatures": len(root_signatures),
            "childExclusionSignatures": len(child_signatures),
            "uniqueExactPositionKeys": len(exact_keys),
            "exactPositionKeysSha256": _digest_string_set(
                "g5-current-exact-position-v1", exact_keys
            ),
            "uniqueExclusionSignatures": len(exclusion_signatures),
            "exclusionSignaturesSha256": _digest_string_set(
                "g5-current-exclusion-signature-v1", exclusion_signatures
            ),
        },
        "sourceProvenance": {
            "generatorSeed": source_seed,
            "groupIdsSha256": _digest_string_set("g5-current-group-id-v1", group_ids),
            "sourceGameIdsSha256": _digest_string_set(
                "g5-current-source-game-id-v1", source_game_ids
            ),
            "sourcePairIdsSha256": _digest_string_set(
                "g5-current-source-pair-id-v1", source_pair_ids
            ),
            "sourceRunIdsSha256": _digest_string_set(
                "g5-current-source-run-id-v1", source_run_ids
            ),
            "trajectoryPairIdsSha256": _digest_string_set(
                "g5-current-trajectory-pair-v1", pool["pairs"]
            ),
            "trajectoryIdsSha256": _digest_string_set(
                "g5-current-trajectory-v1", pool["trajectories"]
            ),
        },
        "historicalComparison": {
            "historicalSignatures": len(historical),
            "historicalSignaturesSha256": _digest_string_set(
                "g5-historical-forbidden-v1", historical
            ),
            "currentSignatures": len(exclusion_signatures),
            "currentSignaturesSha256": _digest_string_set(
                "g5-current-exclusion-signature-v1", exclusion_signatures
            ),
            "intersectionSignatures": len(historical_intersection),
            "intersectionSignaturesSha256": _digest_string_set(
                "g5-historical-current-intersection-v1", historical_intersection
            ),
            "selectionForbiddenSignatures": len(union),
            "selectionForbiddenSignaturesSha256": _digest_string_set(
                "g5-selection-forbidden-v1", union
            ),
        },
        "informationBoundary": {
            "sourcePoolRootsAndChildrenOnly": True,
            "targetFieldsDecoded": 0,
            "scoreFieldsDecoded": 0,
            "resultFieldsDecoded": 0,
        },
    }
    private = {
        "signatures": exclusion_signatures,
        "selectionForbidden": union,
        "sourcePairs": set(pool["pairs"]),
        "sourceTrajectories": set(pool["trajectories"]),
        "rawTrajectoryPairs": set(pool["rawPairs"]),
        "rawTrajectories": set(pool["rawTrajectories"]),
        "groupIds": group_ids,
        "sourceGameIds": source_game_ids,
        "sourcePairIds": source_pair_ids,
        "sourceRunIds": source_run_ids,
    }
    # Public digests/counts are sealed above; these category-only working sets
    # are not consumers of root selection and can be released immediately.
    exact_keys.clear()
    pool_exact_keys.clear()
    pool_signatures.clear()
    root_signatures.clear()
    child_signatures.clear()
    return union, private, public


def _current_corpus_exclusion_from_pins_g5(
    profile: Mapping[str, Any],
    historical: set[str],
    pins: Mapping[str, Mapping[str, Any]],
) -> tuple[set[str], dict[str, set[str]], dict[str, Any]]:
    """Authenticate and exclude the complete raw and filtered G5 graph."""

    fresh = contract.mapping(profile.get("freshDecisionCorpus"), "fresh decision corpus")
    source_contract = contract.mapping(fresh.get("source"), "fresh corpus source")
    quota = contract.mapping(fresh.get("rootQuota"), "fresh corpus root quota")
    source_seed = source_contract.get("requiredRunSeed")
    root_seed = source_contract.get("rootSelectionUsesSeed")
    complete_per_phase = quota.get("completeRootsPerPhase")
    reserve_per_phase = quota.get("reserveRootsPerPhase")
    maximum_roots = quota.get("maximumCandidateRoots")
    synthetic = type(profile.get("finalFreezeIdentities")) is not dict
    raw_per_bucket = (
        profile.get("_selfTestRawRootsPerPhaseSide", 2)
        if synthetic
        else RAW_ROOTS_PER_PHASE_SIDE
    )
    retained_per_bucket = (
        profile.get("_selfTestFeasibleRootsPerPhaseSide", 1)
        if synthetic
        else FEASIBLE_ROOTS_PER_PHASE_SIDE
    )
    if (
        type(source_seed) is not int
        or type(root_seed) is not int
        or type(raw_per_bucket) is not int
        or type(retained_per_bucket) is not int
        or type(complete_per_phase) is not int
        or type(reserve_per_phase) is not int
        or type(maximum_roots) is not int
        or raw_per_bucket <= retained_per_bucket
        or complete_per_phase != retained_per_bucket * 2
        or reserve_per_phase != (raw_per_bucket - retained_per_bucket) * 2
        or maximum_roots != raw_per_bucket * len(PHASES) * 2
    ):
        raise ValueError("G5 raw/feasible root quotas changed")

    if synthetic:
        expected_engine_sha = _require_sha256_string(
            profile.get("_selfTestTeacherEngineSha256"), "self-test teacher engine"
        )
        deterministic_replay = _synthetic_current_corpus_replay_evidence_g5(pins)
        forbidden_catalogs = list(profile.get("_selfTestForbiddenCatalogs", []))
    else:
        expected_engine_sha = pins["teacherEngineExecutable"]["sha256"]
        deterministic_replay = _reproduce_current_corpus_g5(profile, pins)
        final_identities = contract.mapping(
            profile.get("finalFreezeIdentities"), "G5 current-corpus identities"
        )
        forbidden_value = final_identities.get("forbiddenPositionCatalogManifests")
        if type(forbidden_value) is not list or not forbidden_value:
            raise ValueError("G5 current corpus lacks forbidden catalog pins")
        forbidden_catalogs = []
        for index, record in enumerate(forbidden_value):
            shaped = _identity_shape(record, f"G5 forbidden manifest {index}")
            path = _profile_path(shaped["path"], f"G5 forbidden manifest {index} path")
            if not contract.exact_json_equal(contract.identity(path), shaped):
                raise ValueError("G5 forbidden catalog manifest identity changed")
            forbidden_catalogs.append(shaped)
    expected_producer = _expected_g5_producer(
        profile,
        pins,
        contract.mapping(deterministic_replay.get("python"), "G5 replay Python"),
        synthetic=synthetic,
    )

    raw_root_policy = {
        "sourceSeed": source_seed,
        "seed": root_seed,
        "rootsPerPhase": complete_per_phase,
        "reservePerPhase": reserve_per_phase,
        "maximumRootsPerSourceGroup": 1,
        "primaryPerPhaseAndSide": retained_per_bucket,
        "reservePerPhaseAndSide": raw_per_bucket - retained_per_bucket,
        "selection": "target-blind SHA-256 rank",
        "source": "latest complete HCE-only OmegaMatch attempt",
        "sourceGrouping": (
            "one root maximum per pinned opening Source provenance tag; "
            "the complete AB/BA pair stays indivisible"
        ),
        "equivalentAbBaPolicy": (
            "same-group exact inputs with the same move are deterministically "
            "collapsed; conflicts abort"
        ),
        "requiredHceOptions": ROOT_HCE_OPTIONS,
        "requiredEngineSha256": expected_engine_sha,
    }
    root_manifest_fields = {
        "schemaVersion",
        "kind",
        "createdUtc",
        "profileId",
        "freshnessMarker",
        "policy",
        "coverage",
        "sources",
        "producer",
        "finalStageSeal",
        "output",
    }

    def root_manifest(name: str, *, filtered: bool) -> dict[str, Any]:
        manifest = _load_pinned_top_level_allowlist(
            Path(str(pins[name]["path"])),
            pins[name],
            f"current-corpus {name}",
            field_inventory=root_manifest_fields,
            decode_fields=root_manifest_fields - {"sources"},
        )
        expected_policy = dict(raw_root_policy)
        expected_output = pins["roots" if filtered else "rawRoots"]
        if filtered:
            expected_policy.update(
                {
                    "rootsPerPhase": retained_per_bucket * 2,
                    "reservePerPhase": 0,
                    "primaryPerPhaseAndSide": retained_per_bucket,
                    "reservePerPhaseAndSide": 0,
                    "selection": (
                        "target-blind frozen root rank after structural feasibility"
                    ),
                    "rawRootsPerPhaseAndSide": raw_per_bucket,
                    "minimumDistinctLegalChildren": 5,
                    "sourcePvOccurrencesRequired": 1,
                }
            )
        if (
            type(manifest.get("schemaVersion")) is not int
            or manifest.get("schemaVersion") != SCHEMA_VERSION
            or manifest.get("kind") != "omega-decision-root-manifest"
            or manifest.get("profileId") != PROFILE_ID
            or manifest.get("freshnessMarker") != f"g5-source-{source_seed}"
            or manifest.get("finalStageSeal") is not True
            or not contract.exact_json_equal(manifest.get("policy"), expected_policy)
            or not contract.exact_json_equal(manifest.get("output"), expected_output)
            or not contract.exact_json_equal(manifest.get("producer"), expected_producer)
        ):
            raise ValueError(f"current-corpus {name} contract changed")
        _require_canonical_utc(manifest.get("createdUtc"), f"{name}.createdUtc")
        return manifest

    raw_root_manifest = root_manifest("rawRootsManifest", filtered=False)
    filtered_root_manifest = root_manifest("rootsManifest", filtered=True)

    exact_keys: set[str] = set()
    exclusion_signatures: set[str] = set()
    raw_root_signatures: set[str] = set()
    raw_child_signatures: set[str] = set()
    filtered_root_signatures: set[str] = set()
    filtered_child_signatures: set[str] = set()
    pool_exact_keys: set[str] = set()
    pool_signatures: set[str] = set()
    group_ids: set[str] = set()
    source_game_ids: set[str] = set()
    source_pair_ids: set[str] = set()
    source_run_ids: set[str] = set()
    provenance_pairs: set[str] = set()
    exact_owner: dict[str, str] = {}
    rank_inventory: set[str] = set()
    raw_role_bucket_counts: Counter[tuple[str, str, str]] = Counter()
    raw_roots: dict[str, dict[str, Any]] = {}
    raw_root_exact: dict[str, str] = {}
    raw_root_path = Path(str(pins["rawRoots"]["path"]))
    for line_number, row in _scan_pinned_jsonl(
        raw_root_path,
        pins["rawRoots"],
        RAW_CURRENT_ROOT_FIELDS,
        "current-corpus raw roots",
    ):
        if (
            type(row.get("schemaVersion")) is not int
            or row.get("schemaVersion") != SCHEMA_VERSION
            or row.get("kind") != "omega-hce-on-policy-root"
        ):
            raise ValueError(f"{raw_root_path}:{line_number}: root envelope changed")
        root_id = _require_nonempty_string(row.get("rootId"), "raw rootId")
        group = _require_nonempty_string(row.get("groupId"), "raw groupId")
        source_game = _require_nonempty_string(row.get("sourceGameId"), "sourceGameId")
        source_pair = _require_nonempty_string(row.get("sourcePairId"), "sourcePairId")
        source_run = _require_nonempty_string(row.get("sourceRunId"), "sourceRunId")
        provenance = _require_nonempty_string(
            row.get("sourceProvenanceTag"), "sourceProvenanceTag"
        )
        provenance_match = CURRENT_SOURCE_PAIR_TAG.fullmatch(provenance)
        rank = _require_sha256_string(row.get("selectionRank"), "root selectionRank")
        role = row.get("candidateRole")
        if (
            contract.HEX_256.fullmatch(root_id) is None
            or re.fullmatch(r"trajectory:[0-9a-f]{64}", group) is None
            or root_id in raw_roots
            or group in group_ids
            or rank in rank_inventory
            or rank
            != _expected_g5_root_rank(
                root_id,
                root_seed,
                str(row.get("phase")),
                str(row.get("sideToMove")),
                group,
            )
            or role not in {"primary", "reserve"}
            or provenance_match is None
            or _require_sha256_string(
                row.get("sourceEngineSha256"), "root sourceEngineSha256"
            )
            != expected_engine_sha
            or any(
                type(row.get(name)) is not int or row[name] < 0
                for name in ("sourceAttempt", "sourcePly", "sourceLine")
            )
        ):
            raise ValueError(f"{raw_root_path}:{line_number}: raw root metadata changed")
        for name in ("sourceOpeningId", "sourceEngineId", "rootPvMove"):
            _require_nonempty_string(row.get(name), f"raw root {name}")
        if OMEGA_COORDINATE_MOVE.fullmatch(str(row["rootPvMove"])) is None:
            raise ValueError(f"{raw_root_path}:{line_number}: root PV move changed")
        ofen = _canonical_ofen(row.get("ofen"), "raw root OFEN")
        phase, side, exact, _, signatures = core._position_meta(ofen)
        if row.get("phase") != phase or row.get("sideToMove") != side:
            raise ValueError(f"{raw_root_path}:{line_number}: root OFEN metadata changed")
        if exact_owner.setdefault(exact, group) != group:
            raise ValueError("raw exact root position crosses source groups")
        raw_roots[root_id] = dict(row)
        raw_root_exact[root_id] = exact
        group_ids.add(group)
        source_game_ids.add(source_game)
        source_pair_ids.add(source_pair)
        source_run_ids.add(source_run)
        rank_inventory.add(rank)
        provenance_pairs.add(
            _sampler_pair_identity(source_seed, provenance_match.group(1))
        )
        raw_role_bucket_counts[(phase, side, str(role))] += 1
        exact_keys.add(exact)
        exclusion_signatures.add(exact)
        exclusion_signatures.update(signatures)
        raw_root_signatures.add(exact)
        raw_root_signatures.update(signatures)
    if len(raw_roots) != maximum_roots or len(provenance_pairs) != len(raw_roots):
        raise ValueError("raw root count/provenance uniqueness changed")
    for phase in PHASES:
        for side in ("w", "b"):
            if (
                raw_role_bucket_counts[(phase, side, "primary")] != retained_per_bucket
                or raw_role_bucket_counts[(phase, side, "reserve")]
                != raw_per_bucket - retained_per_bucket
            ):
                raise ValueError(f"raw root quota changed for {phase}/{side}")

    def validate_root_coverage(
        manifest: Mapping[str, Any], *, filtered: bool, count: int
    ) -> None:
        coverage = contract.mapping(manifest.get("coverage"), "root coverage")
        if set(coverage) != {
            "records",
            "phaseCounts",
            "phaseSideCounts",
            "primaryPhaseSideCounts",
            "primary",
            "reserve",
            "uniqueRootIds",
            "sourceGroups",
            "oneRootPerSourceGroup",
            "collapsedEquivalentAbBaRecords",
        }:
            raise ValueError("root coverage field inventory changed")
        per_bucket = retained_per_bucket if filtered else raw_per_bucket
        reserve = 0 if filtered else raw_per_bucket - retained_per_bucket
        if (
            coverage.get("records") != count
            or coverage.get("uniqueRootIds") != count
            or coverage.get("sourceGroups") != count
            or coverage.get("oneRootPerSourceGroup") is not True
            or coverage.get("phaseCounts")
            != {phase: per_bucket * 2 for phase in PHASES}
            or coverage.get("phaseSideCounts")
            != {
                f"{phase}/{side}": per_bucket
                for phase in PHASES
                for side in ("w", "b")
            }
            or coverage.get("primaryPhaseSideCounts")
            != {
                f"{phase}/{side}": retained_per_bucket
                for phase in PHASES
                for side in ("w", "b")
            }
            or coverage.get("primary") != retained_per_bucket * len(PHASES) * 2
            or coverage.get("reserve") != reserve * len(PHASES) * 2
            or type(coverage.get("collapsedEquivalentAbBaRecords")) is not int
            or coverage["collapsedEquivalentAbBaRecords"] < 0
        ):
            raise ValueError("root coverage changed")

    validate_root_coverage(raw_root_manifest, filtered=False, count=len(raw_roots))

    def child_envelopes(prefix: str, root_pin: str) -> tuple[dict[str, Any], dict[str, Any]]:
        child_name = f"{prefix}Children" if prefix else "children"
        manifest_name = f"{prefix}ChildrenManifest" if prefix else "childrenManifest"
        seal_name = (
            "rawSamplerCompletionSeal" if prefix else "samplerCompletionSeal"
        )
        manifest = _load_pinned_json(
            Path(str(pins[manifest_name]["path"])),
            pins[manifest_name],
            f"current-corpus {manifest_name}",
        )
        expected_policy = dict(CHILD_POLICY)
        if not prefix:
            expected_policy["derivation"] = (
                "exact child subset selected by root-feasibility-v2"
            )
        runtime = contract.mapping(manifest.get("runtime"), "child runtime")
        if (
            set(manifest)
            != {
                "schemaVersion",
                "kind",
                "createdUtc",
                "policy",
                "coverage",
                "input",
                "output",
                "runtime",
                "finalStageSeal",
            }
            or manifest.get("schemaVersion") != SCHEMA_VERSION
            or manifest.get("kind") != "omega-decision-sampler-manifest"
            or manifest.get("finalStageSeal") is not False
            or manifest.get("policy") != expected_policy
            or manifest.get("input") != pins[root_pin]
            or manifest.get("output") != pins[child_name]
            or set(runtime) != {"framework", "samplerAssembly", "chessLibAssembly"}
            or type(runtime.get("framework")) is not str
            or not runtime["framework"]
            or runtime.get("samplerAssembly") != pins["decisionSamplerAssembly"]
            or runtime.get("chessLibAssembly") != pins["chessLibAssembly"]
        ):
            raise ValueError(f"{manifest_name} contract changed")
        _require_canonical_utc(manifest.get("createdUtc"), f"{manifest_name}.createdUtc")
        seal = _load_pinned_json(
            Path(str(pins[seal_name]["path"])),
            pins[seal_name],
            f"current-corpus {seal_name}",
        )
        producer = contract.mapping(seal.get("producer"), "sampler producer")
        if (
            set(seal)
            != {
                "schemaVersion",
                "kind",
                "createdUtc",
                "input",
                "output",
                "manifest",
                "producer",
                "finalStageSeal",
            }
            or seal.get("schemaVersion") != SCHEMA_VERSION
            or seal.get("kind") != "omega-decision-sampler-completion-seal"
            or seal.get("finalStageSeal") is not True
            or seal.get("input") != pins[root_pin]
            or seal.get("output") != pins[child_name]
            or seal.get("manifest") != pins[manifest_name]
            or set(producer) != {"samplerAssembly", "chessLibAssembly", "framework"}
            or producer.get("samplerAssembly") != pins["decisionSamplerAssembly"]
            or producer.get("chessLibAssembly") != pins["chessLibAssembly"]
            or type(producer.get("framework")) is not str
            or not producer["framework"]
        ):
            raise ValueError(f"{seal_name} contract changed")
        _require_canonical_utc(seal.get("createdUtc"), f"{seal_name}.createdUtc")
        return manifest, seal

    raw_child_manifest, _ = child_envelopes("raw", "rawRoots")
    filtered_child_manifest, _ = child_envelopes("", "roots")

    def scan_children(
        name: str,
        roots: Mapping[str, Mapping[str, Any]],
        fields: frozenset[str],
        signature_set: set[str],
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        path = Path(str(pins[name]["path"]))
        records: list[dict[str, Any]] = []
        by_root: dict[str, list[dict[str, Any]]] = {root_id: [] for root_id in roots}
        child_ids: set[str] = set()
        for line_number, child in _scan_pinned_jsonl(
            path, pins[name], fields, f"current-corpus {name}"
        ):
            root_id = _require_nonempty_string(child.get("rootId"), "child rootId")
            root = roots.get(root_id)
            child_id = _require_nonempty_string(child.get("childId"), "childId")
            if (
                child.get("schemaVersion") != SCHEMA_VERSION
                or child.get("kind") != "omega-legal-child"
                or root is None
                or child_id in child_ids
            ):
                raise ValueError(f"{path}:{line_number}: child envelope changed")
            if any(
                child.get(field) != root.get(root_field)
                for field, root_field in (
                    ("groupId", "groupId"),
                    ("sourceGameId", "sourceGameId"),
                    ("phase", "phase"),
                    ("rootPvMove", "rootPvMove"),
                    ("candidateRole", "candidateRole"),
                    ("selectionRank", "selectionRank"),
                    ("parentOfen", "ofen"),
                )
            ):
                raise ValueError(f"{path}:{line_number}: child/root binding changed")
            parent = _canonical_ofen(child.get("parentOfen"), "child parent OFEN")
            parent_phase, parent_side, _, _, _ = core._position_meta(parent)
            child_ofen = _canonical_ofen(child.get("childOfen"), "child OFEN")
            _, child_side, exact, _, signatures = core._position_meta(child_ofen)
            move = _require_nonempty_string(child.get("move"), "child move")
            ordinal = child.get("moveOrdinal")
            expected_id = hashlib.sha256(
                (
                    "omega-decision-child-v1\0"
                    f"{root_id}\0{root['groupId']}\0{move}\0{child_ofen}"
                ).encode("utf-8")
            ).hexdigest()
            if (
                child.get("parentSideToMove") != parent_side
                or child.get("phase") != parent_phase
                or child.get("childSideToMove") != child_side
                or type(ordinal) is not int
                or ordinal < 0
                or type(child.get("isPromotion")) is not bool
                or child.get("isPromotion") != (len(move) == 5)
                or OMEGA_COORDINATE_MOVE.fullmatch(move) is None
                or child_id != expected_id
            ):
                raise ValueError(f"{path}:{line_number}: child metadata changed")
            child_ids.add(child_id)
            records.append(dict(child))
            by_root[root_id].append(dict(child))
            exact_keys.add(exact)
            exclusion_signatures.add(exact)
            exclusion_signatures.update(signatures)
            signature_set.add(exact)
            signature_set.update(signatures)
        for root_id, items in by_root.items():
            ordinals = {int(item["moveOrdinal"]) for item in items}
            moves = [str(item["move"]) for item in items]
            if (
                ordinals != set(range(len(items)))
                or moves != sorted(moves)
                or len(moves) != len(set(moves))
            ):
                raise ValueError(f"{name}: legal-child order changed for {root_id}")
        return records, by_root

    raw_children, raw_children_by_root = scan_children(
        "rawChildren", raw_roots, RAW_CURRENT_CHILD_FIELDS, raw_child_signatures
    )

    def validate_child_coverage(
        manifest: Mapping[str, Any], roots: Mapping[str, Any], by_root: Mapping[str, Sequence[Any]], count: int
    ) -> None:
        coverage = contract.mapping(manifest.get("coverage"), "child coverage")
        if set(coverage) != {
            "roots",
            "children",
            "zeroChildRoots",
            "minimumChildrenPerRoot",
            "maximumChildrenPerRoot",
            "phaseCounts",
            "sideToMoveCounts",
            "uniqueRootIds",
            "uniqueChildIds",
        }:
            raise ValueError("child coverage field inventory changed")
        counts = [len(by_root[root_id]) for root_id in roots]
        phase_counts = Counter(str(root["phase"]) for root in roots.values())
        side_counts = Counter(str(root["sideToMove"]) for root in roots.values())
        expected = {
            "roots": len(roots),
            "children": count,
            "zeroChildRoots": sum(item == 0 for item in counts),
            "minimumChildrenPerRoot": min(counts),
            "maximumChildrenPerRoot": max(counts),
            "phaseCounts": {phase: phase_counts[phase] for phase in PHASES},
            "sideToMoveCounts": {side: side_counts[side] for side in ("w", "b")},
            "uniqueRootIds": len(roots),
            "uniqueChildIds": count,
        }
        if coverage != expected:
            raise ValueError("child coverage changed")

    validate_child_coverage(
        raw_child_manifest, raw_roots, raw_children_by_root, len(raw_children)
    )

    components = _g5_component_ids(raw_roots, raw_children_by_root)
    expected_feasibility: list[dict[str, Any]] = []
    feasibility_by_id: dict[str, dict[str, Any]] = {}
    for root_id, root in raw_roots.items():
        items = raw_children_by_root[root_id]
        pv_occurrences = sum(item["move"] == root["rootPvMove"] for item in items)
        reasons: list[str] = []
        if len(items) < 5:
            reasons.append("fewer-than-five-distinct-legal-children")
        if pv_occurrences != 1:
            reasons.append("source-pv-not-exactly-once")
        feasibility_by_id[root_id] = {
            "schemaVersion": 1,
            "kind": "omega-decision-root-feasibility",
            "rootId": root_id,
            "groupId": root["groupId"],
            "phase": root["phase"],
            "sideToMove": root["sideToMove"],
            "selectionRank": root["selectionRank"],
            "distinctLegalChildren": len(items),
            "sourcePvOccurrences": pv_occurrences,
            "structurallyFeasible": not reasons,
            "retained": False,
            "feasibilityOrdinal": None,
            "rejectionReasons": reasons,
            "rawLeakageComponentId": components[str(root["groupId"])],
        }
    retained_ids: set[str] = set()
    for phase in PHASES:
        for side in ("w", "b"):
            eligible = sorted(
                (
                    row
                    for row in feasibility_by_id.values()
                    if row["phase"] == phase
                    and row["sideToMove"] == side
                    and row["structurallyFeasible"] is True
                ),
                key=lambda row: (str(row["selectionRank"]), str(row["rootId"])),
            )
            if len(eligible) < retained_per_bucket:
                raise ValueError(f"insufficient feasible roots for {phase}/{side}")
            for ordinal, row in enumerate(eligible[:retained_per_bucket], 1):
                row["retained"] = True
                row["feasibilityOrdinal"] = ordinal
                retained_ids.add(str(row["rootId"]))
    expected_feasibility = sorted(
        feasibility_by_id.values(),
        key=lambda row: (
            PHASES.index(str(row["phase"])),
            str(row["sideToMove"]),
            str(row["selectionRank"]),
            str(row["rootId"]),
        ),
    )
    actual_feasibility = [
        row
        for _, row in _scan_pinned_jsonl(
            Path(str(pins["rootFeasibility"]["path"])),
            pins["rootFeasibility"],
            CURRENT_FEASIBILITY_FIELDS,
            "current-corpus root feasibility",
        )
    ]
    if actual_feasibility != expected_feasibility:
        raise ValueError("root feasibility does not reproduce from the raw graph")

    feasibility_manifest = contract.strict_load(
        Path(str(pins["rootFeasibilityManifest"]["path"])),
        "current root-feasibility manifest",
    )
    if contract.identity(Path(str(pins["rootFeasibilityManifest"]["path"]))) != pins[
        "rootFeasibilityManifest"
    ]:
        raise ValueError("root-feasibility manifest identity changed")
    feasible_counts = Counter(
        (str(row["phase"]), str(row["sideToMove"]), bool(row["structurallyFeasible"]))
        for row in expected_feasibility
    )
    rejection_counts = Counter(
        reason for row in expected_feasibility for reason in row["rejectionReasons"]
    )
    expected_feasibility_policy = {
        "sourceSeed": source_seed,
        "rootSelectionSeed": root_seed,
        "rankDomain": "omega-g5-feasible-root-v1",
        "rawRootsPerPhaseAndSide": raw_per_bucket,
        "retainedRootsPerPhaseAndSide": retained_per_bucket,
        "minimumDistinctLegalChildren": 5,
        "sourcePvOccurrencesRequired": 1,
        "targetInformationRead": False,
        "crossBucketBorrowing": False,
        "rawGraphComponentsBuiltBeforeFiltering": True,
    }
    expected_feasibility_coverage = {
        "rawRoots": len(raw_roots),
        "rawChildren": len(raw_children),
        "structurallyFeasibleRoots": sum(
            row["structurallyFeasible"] is True for row in expected_feasibility
        ),
        "retainedRoots": len(retained_ids),
        "retainedChildren": sum(
            len(raw_children_by_root[root_id]) for root_id in retained_ids
        ),
        "rawComponents": len(set(components.values())),
        "phaseSide": {
            f"{phase}/{side}": {
                "raw": raw_per_bucket,
                "feasible": feasible_counts[(phase, side, True)],
                "retained": retained_per_bucket,
            }
            for phase in PHASES
            for side in ("w", "b")
        },
        "rejectionReasons": dict(rejection_counts),
    }
    raw_identity_names = (
        "rawRoots",
        "rawRootsManifest",
        "rawChildren",
        "rawChildrenManifest",
        "rawSamplerCompletionSeal",
    )
    if (
        set(feasibility_manifest)
        != {
            "schemaVersion",
            "kind",
            "createdUtc",
            "profileId",
            "policy",
            "coverage",
            "inputs",
            "priorReuse",
            "producer",
            "finalStageSeal",
            "output",
        }
        or feasibility_manifest.get("schemaVersion") != SCHEMA_VERSION
        or feasibility_manifest.get("kind")
        != "omega-decision-root-feasibility-manifest"
        or feasibility_manifest.get("profileId") != PROFILE_ID
        or feasibility_manifest.get("policy") != expected_feasibility_policy
        or feasibility_manifest.get("coverage") != expected_feasibility_coverage
        or feasibility_manifest.get("inputs")
        != {name: pins[name] for name in raw_identity_names}
        or feasibility_manifest.get("priorReuse")
        != {
            "forbiddenCatalogs": forbidden_catalogs,
            "collisionCounts": {
                "sourceDataInputCollisions": 0,
                "sourceGameCollisions": 0,
                "sourceRunCollisions": 0,
                "exactPositionCollisions": 0,
                "conservativeOrbitCollisions": 0,
            },
        }
        or feasibility_manifest.get("producer") != expected_producer
        or feasibility_manifest.get("finalStageSeal") is not False
        or feasibility_manifest.get("output") != pins["rootFeasibility"]
    ):
        raise ValueError("root-feasibility manifest contract changed")
    _require_canonical_utc(
        feasibility_manifest.get("createdUtc"), "root-feasibility manifest createdUtc"
    )

    feasibility_seal = contract.strict_load(
        Path(str(pins["rootFeasibilitySeal"]["path"])),
        "current root-feasibility seal",
    )
    if contract.identity(Path(str(pins["rootFeasibilitySeal"]["path"]))) != pins[
        "rootFeasibilitySeal"
    ]:
        raise ValueError("root-feasibility seal identity changed")
    output_identity_names = (
        "rootFeasibility",
        "rootFeasibilityManifest",
        "roots",
        "rootsManifest",
        "children",
        "childrenManifest",
        "samplerCompletionSeal",
    )
    if (
        set(feasibility_seal)
        != {
            "schemaVersion",
            "kind",
            "profileId",
            "status",
            "createdUtc",
            "declaration",
            "forbiddenCatalogs",
            "priorReuseCollisionCounts",
            "identities",
            "producer",
            "finalStageSeal",
        }
        or feasibility_seal.get("schemaVersion") != SCHEMA_VERSION
        or feasibility_seal.get("kind") != "omega-decision-root-feasibility-seal"
        or feasibility_seal.get("profileId") != PROFILE_ID
        or feasibility_seal.get("status")
        != "target-opaque-raw-graph-authenticated-and-feasibility-frozen"
        or feasibility_seal.get("declaration")
        != {
            "teacherSearchesPresentAtFreeze": False,
            "targetOrScoreFieldsDecoded": 0,
            "targetOrScoreFieldsEmitted": 0,
            "rawGraphAuthenticatedBeforeFiltering": True,
            "rawComponentsInheritedByFilteredRows": True,
        }
        or feasibility_seal.get("forbiddenCatalogs") != forbidden_catalogs
        or feasibility_seal.get("priorReuseCollisionCounts")
        != {
            "sourceDataInputCollisions": 0,
            "sourceGameCollisions": 0,
            "sourceRunCollisions": 0,
            "exactPositionCollisions": 0,
            "conservativeOrbitCollisions": 0,
        }
        or feasibility_seal.get("identities")
        != {
            **{name: pins[name] for name in raw_identity_names},
            **{name: pins[name] for name in output_identity_names},
        }
        or feasibility_seal.get("producer") != expected_producer
        or feasibility_seal.get("finalStageSeal") is not True
    ):
        raise ValueError("root-feasibility seal contract changed")
    _require_canonical_utc(
        feasibility_seal.get("createdUtc"), "root-feasibility seal createdUtc"
    )

    expected_filtered_roots: list[dict[str, Any]] = []
    expected_filtered_children: list[dict[str, Any]] = []
    for root_id in sorted(
        retained_ids,
        key=lambda value: (
            PHASES.index(str(raw_roots[value]["phase"])),
            str(raw_roots[value]["sideToMove"]),
            str(raw_roots[value]["selectionRank"]),
            value,
        ),
    ):
        raw_root = raw_roots[root_id]
        component = feasibility_by_id[root_id]["rawLeakageComponentId"]
        expected_filtered_roots.append(
            {
                **raw_root,
                "rawCandidateRole": raw_root["candidateRole"],
                "candidateRole": "primary",
                "rawLeakageComponentId": component,
            }
        )
        expected_filtered_children.extend(
            {
                **child,
                "rawCandidateRole": child["candidateRole"],
                "candidateRole": "primary",
                "rawLeakageComponentId": component,
            }
            for child in sorted(
                raw_children_by_root[root_id], key=lambda row: str(row["move"])
            )
        )
    actual_filtered_roots = [
        row
        for _, row in _scan_pinned_jsonl(
            Path(str(pins["roots"]["path"])),
            pins["roots"],
            FILTERED_CURRENT_ROOT_FIELDS,
            "current-corpus filtered roots",
        )
    ]
    if actual_filtered_roots != expected_filtered_roots:
        raise ValueError("filtered roots are not the exact feasibility derivation")
    filtered_roots = {str(row["rootId"]): row for row in actual_filtered_roots}
    validate_root_coverage(
        filtered_root_manifest, filtered=True, count=len(filtered_roots)
    )
    actual_filtered_children, filtered_children_by_root = scan_children(
        "children",
        filtered_roots,
        FILTERED_CURRENT_CHILD_FIELDS,
        filtered_child_signatures,
    )
    if actual_filtered_children != expected_filtered_children:
        raise ValueError("filtered children are not the exact feasibility derivation")
    validate_child_coverage(
        filtered_child_manifest,
        filtered_roots,
        filtered_children_by_root,
        len(actual_filtered_children),
    )
    for row in actual_filtered_roots:
        _, _, exact, _, signatures = core._position_meta(str(row["ofen"]))
        filtered_root_signatures.add(exact)
        filtered_root_signatures.update(signatures)

    pool = _current_source_pool(
        pins,
        source_seed=source_seed,
        exact_keys=exact_keys,
        exclusion_signatures=exclusion_signatures,
        pool_exact_keys=pool_exact_keys,
        pool_signatures=pool_signatures,
    )
    if not provenance_pairs.issubset(pool["pairs"]):
        raise ValueError("raw G5 roots do not map into the pinned source pool")
    historical_intersection = historical & exclusion_signatures
    if historical_intersection:
        raise ValueError("raw G5 graph intersects the authenticated prior-data quarantine")
    union = historical | exclusion_signatures
    identity_names = {*CURRENT_CORPUS_PATHS, "generation4Closure"}
    public = {
        "identities": {name: dict(pins[name]) for name in identity_names},
        "deterministicReplay": deterministic_replay,
        "records": {
            "sourcePoolStates": pool["rows"],
            "rawRoots": len(raw_roots),
            "rawChildren": len(raw_children),
            "structurallyFeasibleRoots": sum(
                row["structurallyFeasible"] is True for row in expected_feasibility
            ),
            "retainedRoots": len(filtered_roots),
            "retainedChildren": len(actual_filtered_children),
            "rawLeakageComponents": len(set(components.values())),
            "sourceGroups": len(group_ids),
            "sourceGameIds": len(source_game_ids),
            "sourcePairIds": len(source_pair_ids),
            "sourceRunIds": len(source_run_ids),
            "sourceTrajectoryPairs": len(pool["pairs"]),
            "sourceTrajectories": len(pool["trajectories"]),
        },
        "positions": {
            "sourcePoolExactPositionKeys": len(pool_exact_keys),
            "sourcePoolExclusionSignatures": len(pool_signatures),
            "rawRootExclusionSignatures": len(raw_root_signatures),
            "rawChildExclusionSignatures": len(raw_child_signatures),
            "filteredRootExclusionSignatures": len(filtered_root_signatures),
            "filteredChildExclusionSignatures": len(filtered_child_signatures),
            "uniqueExactPositionKeys": len(exact_keys),
            "exactPositionKeysSha256": _digest_string_set(
                "g5-current-exact-position-v2", exact_keys
            ),
            "uniqueExclusionSignatures": len(exclusion_signatures),
            "exclusionSignaturesSha256": _digest_string_set(
                "g5-current-exclusion-signature-v2", exclusion_signatures
            ),
        },
        "sourceProvenance": {
            "generatorSeed": source_seed,
            "groupIdsSha256": _digest_string_set("g5-current-group-id-v2", group_ids),
            "sourceGameIdsSha256": _digest_string_set(
                "g5-current-source-game-id-v2", source_game_ids
            ),
            "sourcePairIdsSha256": _digest_string_set(
                "g5-current-source-pair-id-v2", source_pair_ids
            ),
            "sourceRunIdsSha256": _digest_string_set(
                "g5-current-source-run-id-v2", source_run_ids
            ),
            "trajectoryPairIdsSha256": _digest_string_set(
                "g5-current-trajectory-pair-v2", pool["pairs"]
            ),
            "trajectoryIdsSha256": _digest_string_set(
                "g5-current-trajectory-v2", pool["trajectories"]
            ),
        },
        "historicalComparison": {
            "historicalSignatures": len(historical),
            "historicalSignaturesSha256": _digest_string_set(
                "g5-historical-forbidden-v2", historical
            ),
            "currentSignatures": len(exclusion_signatures),
            "currentSignaturesSha256": _digest_string_set(
                "g5-current-exclusion-signature-v2", exclusion_signatures
            ),
            "intersectionSignatures": len(historical_intersection),
            "intersectionSignaturesSha256": _digest_string_set(
                "g5-historical-current-intersection-v2", historical_intersection
            ),
            "selectionForbiddenSignatures": len(union),
            "selectionForbiddenSignaturesSha256": _digest_string_set(
                "g5-selection-forbidden-v2", union
            ),
        },
        "informationBoundary": {
            "rawAndFilteredRootsAndChildrenOnly": True,
            "targetFieldsDecoded": 0,
            "scoreFieldsDecoded": 0,
            "resultFieldsDecoded": 0,
        },
    }
    private = {
        "signatures": exclusion_signatures,
        "selectionForbidden": union,
        "sourcePairs": set(pool["pairs"]),
        "sourceTrajectories": set(pool["trajectories"]),
        "rawTrajectoryPairs": set(pool["rawPairs"]),
        "rawTrajectories": set(pool["rawTrajectories"]),
        "groupIds": group_ids,
        "sourceGameIds": source_game_ids,
        "sourcePairIds": source_pair_ids,
        "sourceRunIds": source_run_ids,
    }
    return union, private, public


# The public entry point deliberately resolves to the G5 raw-graph verifier;
# the copied G4 implementation above remains only as reviewable provenance.
_current_corpus_exclusion_from_pins = _current_corpus_exclusion_from_pins_g5


def _gate_current_corpus_evidence(
    roots: Sequence[core.Root],
    selected: Sequence[core.Root],
    current: Mapping[str, set[str]],
) -> dict[str, Any]:
    candidate_pairs = {
        _sampler_pair_identity(root.generator_seed, root.trajectory_pair_id)
        for root in roots
    }
    candidate_trajectories = {
        _sampler_trajectory_identity(root.generator_seed, root.trajectory_id)
        for root in roots
    }
    candidate_source_groups = {root.source_group for root in roots}
    candidate_pair_aliases = {root.trajectory_pair_id for root in roots}
    candidate_trajectory_aliases = {root.trajectory_id for root in roots}
    formal_pair_intersection = candidate_pairs & current["sourcePairs"]
    formal_trajectory_intersection = (
        candidate_trajectories & current["sourceTrajectories"]
    )
    current_root_identifiers = (
        current["groupIds"]
        | current["sourceGameIds"]
        | current["sourcePairIds"]
        | current["sourceRunIds"]
    )
    root_identifier_intersection = candidate_source_groups & current_root_identifiers
    if (
        formal_pair_intersection
        or formal_trajectory_intersection
        or root_identifier_intersection
    ):
        raise ValueError(
            "match sampler reuses a current-corpus source group or trajectory"
        )
    candidate_signatures: set[str] = set()
    for root in roots:
        candidate_signatures.update(root.orbit_signatures)
        candidate_signatures.add(root.identity)
    selected_signatures: set[str] = set()
    for root in selected:
        selected_signatures.update(root.orbit_signatures)
        selected_signatures.add(root.identity)
    candidate_position_intersection = candidate_signatures & current["signatures"]
    selected_position_intersection = selected_signatures & current["signatures"]
    selected_forbidden_intersection = (
        selected_signatures & current["selectionForbidden"]
    )
    selected_pairs = {
        _sampler_pair_identity(root.generator_seed, root.trajectory_pair_id)
        for root in selected
    }
    selected_trajectories = {
        _sampler_trajectory_identity(root.generator_seed, root.trajectory_id)
        for root in selected
    }
    if (
        selected_forbidden_intersection
        or selected_pairs & current["sourcePairs"]
        or selected_trajectories & current["sourceTrajectories"]
    ):
        raise ValueError("selected match roots intersect the current Generation-5 corpus")
    return {
        "candidateRoots": len(roots),
        "candidateSourceGroups": len(candidate_source_groups),
        "candidateSourceGroupsSha256": _digest_string_set(
            "g5-match-candidate-source-group-v1", candidate_source_groups
        ),
        "candidateTrajectoryPairs": len(candidate_pairs),
        "candidateTrajectoryPairsSha256": _digest_string_set(
            "g5-match-candidate-trajectory-pair-v1", candidate_pairs
        ),
        "candidateTrajectories": len(candidate_trajectories),
        "candidateTrajectoriesSha256": _digest_string_set(
            "g5-match-candidate-trajectory-v1", candidate_trajectories
        ),
        "formalSourcePairIntersections": 0,
        "formalTrajectoryIntersections": 0,
        "rootIdentifierIntersections": 0,
        "rawTrajectoryPairAliases": len(
            candidate_pair_aliases & current["rawTrajectoryPairs"]
        ),
        "rawTrajectoryAliases": len(
            candidate_trajectory_aliases & current["rawTrajectories"]
        ),
        "candidatePositionIntersectionSignatures": len(
            candidate_position_intersection
        ),
        "candidatePositionIntersectionSha256": _digest_string_set(
            "g5-match-candidate-current-position-intersection-v1",
            candidate_position_intersection,
        ),
        "selectedRoots": len(selected),
        "selectedPositionIntersectionSignatures": 0,
        "selectedPositionIntersectionSha256": _digest_string_set(
            "g5-match-selected-current-position-intersection-v1",
            selected_position_intersection,
        ),
        "selectedForbiddenIntersectionSignatures": 0,
        "selectedForbiddenIntersectionSha256": _digest_string_set(
            "g5-match-selected-forbidden-intersection-v1",
            selected_forbidden_intersection,
        ),
        "selectedSourcePairIntersections": 0,
        "selectedTrajectoryIntersections": 0,
    }


def _validate_current_corpus_audit(value: Any) -> dict[str, Any]:
    audit = contract.mapping(value, "suite current-corpus audit")
    if set(audit) != {
        "identities",
        "deterministicReplay",
        "records",
        "positions",
        "sourceProvenance",
        "historicalComparison",
        "informationBoundary",
    }:
        raise ValueError("suite current-corpus audit fields changed")
    identities = contract.mapping(audit.get("identities"), "current identities")
    if set(identities) != {*CURRENT_CORPUS_PATHS, "generation4Closure"}:
        raise ValueError("suite current-corpus identity inventory changed")
    for name, identity in identities.items():
        shaped = _identity_shape(identity, f"current-corpus {name}")
        normalized = contract.resolve(Path(shaped["path"])).as_posix().lower()
        if name != "generation4Closure" and (
            "king-state-v4" in normalized
            or "omega-decision-v1" in normalized
            or "generation4" in normalized
        ):
            raise ValueError("a G5 current-corpus identity points into G4")
    replay = contract.mapping(
        audit.get("deterministicReplay"), "current deterministic replay"
    )
    if set(replay) != {
        "rawRoots",
        "rawChildren",
        "rootFeasibility",
        "roots",
        "children",
        "python",
        "authorities",
        "rootSamplerChessLib",
        "dotnetHost",
    }:
        raise ValueError("suite deterministic-replay fields changed")
    for name in ("python", "rootSamplerChessLib", "dotnetHost"):
        _identity_shape(replay.get(name), f"current replay {name}")
    authorities = contract.mapping(replay.get("authorities"), "current replay authorities")
    if set(authorities) != {
        "sourceOpeningBuilderSource",
        "decisionTeacherSource",
        "deepHceV2Source",
        "networkFormatPythonSource",
        "selectScreenSource",
        "priorProjectionSource",
        "trainerSource",
        "preregistrationValidatorSource",
        "generation4AbortVerifierSource",
        "pythonRuntimeToolSource",
        "dotnetRuntimeToolSource",
        "pythonRuntimeManifest",
    }:
        raise ValueError("current replay authority inventory changed")
    for name, identity in authorities.items():
        shaped = _identity_shape(identity, f"current replay authority {name}")
        authority_path = contract.resolve(Path(shaped["path"]))
        normalized = authority_path.as_posix().lower()
        if name == "priorProjectionSource":
            if authority_path != contract.resolve(
                contract.REPO / CURRENT_REPLAY_PATHS["priorProjectionSource"]
            ):
                raise ValueError("historical prior-projection exception widened")
        elif name == "generation4AbortVerifierSource":
            if authority_path != contract.resolve(
                contract.REPO / GENERATION4_ABORT_TOOL_PATH
            ):
                raise ValueError("Generation-4 abort-verifier exception widened")
        elif "generation4" in normalized or "king-state-v4" in normalized:
            raise ValueError("a current G5 replay authority points into G4")
    replay_fields = {
        "rawRoots": {"bytes", "sha256", "manifestSemanticSha256"},
        "rawChildren": {
            "bytes",
            "sha256",
            "manifestSemanticSha256",
            "completionSemanticSha256",
        },
        "rootFeasibility": {
            "bytes",
            "sha256",
            "manifestSemanticSha256",
            "sealSemanticSha256",
        },
        "roots": {"bytes", "sha256", "manifestSemanticSha256"},
        "children": {
            "bytes",
            "sha256",
            "manifestSemanticSha256",
            "completionSemanticSha256",
        },
    }
    for name, expected_fields in replay_fields.items():
        item = contract.mapping(replay.get(name), f"current replay {name}")
        if set(item) != expected_fields:
            raise ValueError(f"current {name} replay fields changed")
        contract.exact_int(item.get("bytes"), f"current replay {name} bytes")
        for key, value in item.items():
            if key == "bytes":
                continue
            if type(value) is not str or contract.HEX_256.fullmatch(value) is None:
                raise ValueError(f"current replay {name} digest {key} changed")
    records = contract.mapping(audit.get("records"), "current records")
    if set(records) != {
        "sourcePoolStates",
        "rawRoots",
        "rawChildren",
        "structurallyFeasibleRoots",
        "retainedRoots",
        "retainedChildren",
        "rawLeakageComponents",
        "sourceGroups",
        "sourceGameIds",
        "sourcePairIds",
        "sourceRunIds",
        "sourceTrajectoryPairs",
        "sourceTrajectories",
    }:
        raise ValueError("suite current-corpus record fields changed")
    positions = contract.mapping(audit.get("positions"), "current positions")
    if set(positions) != {
        "sourcePoolExactPositionKeys",
        "sourcePoolExclusionSignatures",
        "rawRootExclusionSignatures",
        "rawChildExclusionSignatures",
        "filteredRootExclusionSignatures",
        "filteredChildExclusionSignatures",
        "uniqueExactPositionKeys",
        "exactPositionKeysSha256",
        "uniqueExclusionSignatures",
        "exclusionSignaturesSha256",
    }:
        raise ValueError("suite current-corpus position fields changed")
    source = contract.mapping(audit.get("sourceProvenance"), "current source")
    if set(source) != {
        "generatorSeed",
        "groupIdsSha256",
        "sourceGameIdsSha256",
        "sourcePairIdsSha256",
        "sourceRunIdsSha256",
        "trajectoryPairIdsSha256",
        "trajectoryIdsSha256",
    }:
        raise ValueError("suite current-corpus source fields changed")
    comparison = contract.mapping(
        audit.get("historicalComparison"), "current historical comparison"
    )
    if set(comparison) != {
        "historicalSignatures",
        "historicalSignaturesSha256",
        "currentSignatures",
        "currentSignaturesSha256",
        "intersectionSignatures",
        "intersectionSignaturesSha256",
        "selectionForbiddenSignatures",
        "selectionForbiddenSignaturesSha256",
    }:
        raise ValueError("suite historical/current comparison fields changed")
    for mapping_value in (records, positions, comparison):
        for key, item in mapping_value.items():
            if key.endswith("Sha256"):
                if type(item) is not str or contract.HEX_256.fullmatch(item) is None:
                    raise ValueError(f"suite current-corpus digest {key} changed")
            else:
                contract.exact_int(item, f"suite current-corpus count {key}")
    if comparison.get("intersectionSignatures") != 0:
        raise ValueError("suite raw G5 corpus intersects historical quarantine")
    contract.exact_int(source.get("generatorSeed"), "current source generator seed")
    for key, item in source.items():
        if key == "generatorSeed":
            continue
        if type(item) is not str or contract.HEX_256.fullmatch(item) is None:
            raise ValueError(f"suite current source digest {key} changed")
    if not contract.exact_json_equal(
        audit.get("informationBoundary"),
        {
            "rawAndFilteredRootsAndChildrenOnly": True,
            "targetFieldsDecoded": 0,
            "scoreFieldsDecoded": 0,
            "resultFieldsDecoded": 0,
        },
    ):
        raise ValueError("suite current-corpus information boundary changed")
    return audit


def _validate_gate_current_evidence(value: Any, gate: str) -> dict[str, Any]:
    evidence = contract.mapping(value, f"{gate} current-corpus disjointness")
    expected = {
        "candidateRoots",
        "candidateSourceGroups",
        "candidateSourceGroupsSha256",
        "candidateTrajectoryPairs",
        "candidateTrajectoryPairsSha256",
        "candidateTrajectories",
        "candidateTrajectoriesSha256",
        "formalSourcePairIntersections",
        "formalTrajectoryIntersections",
        "rootIdentifierIntersections",
        "rawTrajectoryPairAliases",
        "rawTrajectoryAliases",
        "candidatePositionIntersectionSignatures",
        "candidatePositionIntersectionSha256",
        "selectedRoots",
        "selectedPositionIntersectionSignatures",
        "selectedPositionIntersectionSha256",
        "selectedForbiddenIntersectionSignatures",
        "selectedForbiddenIntersectionSha256",
        "selectedSourcePairIntersections",
        "selectedTrajectoryIntersections",
    }
    if set(evidence) != expected:
        raise ValueError(f"{gate} current-corpus evidence fields changed")
    for key, item in evidence.items():
        if key.endswith("Sha256"):
            if type(item) is not str or contract.HEX_256.fullmatch(item) is None:
                raise ValueError(f"{gate} current-corpus digest {key} changed")
        else:
            contract.exact_int(item, f"{gate} current-corpus count {key}")
    for key in (
        "formalSourcePairIntersections",
        "formalTrajectoryIntersections",
        "rootIdentifierIntersections",
        "selectedPositionIntersectionSignatures",
        "selectedForbiddenIntersectionSignatures",
        "selectedSourcePairIntersections",
        "selectedTrajectoryIntersections",
    ):
        if evidence.get(key) != 0:
            raise ValueError(f"{gate} has a sealed current-corpus intersection")
    return evidence


def _runtime_identity(protocol: Mapping[str, Any], key: str) -> dict[str, Any]:
    runtime = contract.mapping(protocol.get("runtime"), "match runtime")
    record = contract.mapping(runtime.get(key), f"runtime {key}")
    path = _profile_path(record["path"], f"runtime {key} path")
    actual = contract.identity(path)
    if not contract.same_identity(record, actual):
        raise ValueError(f"runtime {key} changed")
    return actual


def _dotnet_runtime_bundle(protocol: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _runtime_identity(protocol, "dotnetRuntimeManifest")
    value = dotnet_runtime.verify_manifest(Path(manifest["path"]))
    runtime = contract.mapping(protocol.get("runtime"), "match runtime")
    if (
        value.get("runtimeVersion") != runtime.get("dotnetRuntimeVersion")
        or value.get("bundleSha256")
        != runtime.get("dotnetRuntimeBundleSha256")
    ):
        raise ValueError("frozen .NET runtime bundle changed")
    return value


def _sampler_trajectory_seed(seed: int, pair_index: int, flavor: str) -> int:
    flavor_salt = (
        0xA0761D6478BD642F if flavor == "ab" else 0xE7037ED1A0B428DB
    )
    value = (
        int(seed)
        ^ (((pair_index + 1) * 0x9E3779B97F4A7C15) & UINT64_MASK)
        ^ flavor_salt
    ) & UINT64_MASK
    value = (value + 0x9E3779B97F4A7C15) & UINT64_MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & UINT64_MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & UINT64_MASK
    return (value ^ (value >> 31)) & UINT64_MASK


def _exact_sampler_identity(record: Any, path: Path, label: str) -> dict[str, Any]:
    value = _identity_shape(record, label)
    actual = contract.identity(path)
    if not contract.exact_json_equal(value, actual):
        raise ValueError(f"{label} differs from the canonical file identity")
    contract.verify_identity(value, label)
    return actual


def _strict_sampler_records(
    path: Path,
    gate: str,
    source_identity: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> tuple[list[core.Root], dict[str, int], dict[str, int]]:
    spec = core.GATE_SPECS[gate]
    sampler = contract.mapping(protocol.get("suiteGeneration"), "suite generation")
    phase_windows = {
        "opening": (6, 48),
        "middlegame": (20, 140),
        "late": (40, 260),
        "endgame": (60, 400),
    }
    roots: list[core.Root] = []
    phase_counts = Counter()
    side_counts = Counter()
    seen_source_ranks: set[str] = set()
    try:
        with contract.resolve(path).open(
            "r", encoding="utf-8", errors="strict", newline=""
        ) as stream:
            for line_number, raw_line in enumerate(stream, 1):
                if not raw_line.endswith("\n"):
                    raise ValueError(
                        f"{path}:{line_number}: sampler record is not line-complete"
                    )
                text = raw_line[:-1]
                if text.endswith("\r"):
                    text = text[:-1]
                if not text or text != text.strip():
                    raise ValueError(
                        f"{path}:{line_number}: sampler record is blank or padded"
                    )
                try:
                    record = json.loads(
                        text,
                        object_pairs_hook=contract._unique_object,
                        parse_constant=lambda token: (_ for _ in ()).throw(
                            ValueError(f"non-finite JSON token {token}")
                        ),
                    )
                except (json.JSONDecodeError, ValueError) as error:
                    raise ValueError(
                        f"{path}:{line_number}: sampler record is not strict JSON"
                    ) from error
                if type(record) is not dict or set(record) != SAMPLER_RECORD_FIELDS:
                    raise ValueError(
                        f"{path}:{line_number}: sampler record field inventory changed"
                    )
                if (
                    type(record.get("schemaVersion")) is not int
                    or record.get("schemaVersion") != SCHEMA_VERSION
                    or record.get("kind") != "omega-rules-only-random-root"
                    or record.get("generatorSeed") != str(spec["seed"])
                ):
                    raise ValueError(
                        f"{path}:{line_number}: sampler record envelope changed"
                    )
                pair_match = SAMPLER_PAIR_ID.fullmatch(
                    str(record.get("trajectoryPairId", ""))
                )
                flavor = record.get("flavor")
                if pair_match is None or flavor not in {"ab", "ba"}:
                    raise ValueError(
                        f"{path}:{line_number}: malformed trajectory identity"
                    )
                pair_number = int(pair_match.group(1))
                if not 1 <= pair_number <= sampler["trajectoryPairsPerStage"]:
                    raise ValueError(
                        f"{path}:{line_number}: trajectory pair is out of range"
                    )
                pair_id = pair_match.group(0)
                trajectory_id = f"{pair_id}-{flavor}"
                if record.get("trajectoryId") != trajectory_id:
                    raise ValueError(
                        f"{path}:{line_number}: trajectory/flavor binding changed"
                    )
                pair_index = pair_number - 1
                trajectory_seed = _sampler_trajectory_seed(
                    int(spec["seed"]), pair_index, str(flavor)
                )
                if record.get("trajectorySeed") != str(trajectory_seed):
                    raise ValueError(
                        f"{path}:{line_number}: trajectory seed changed"
                    )
                ply = record.get("ply")
                ofen = record.get("ofen")
                if type(ply) is not int or type(ofen) is not str or not ofen:
                    raise ValueError(
                        f"{path}:{line_number}: sampler ply/OFEN is malformed"
                    )
                phase, side, identity, orbit, signatures = core._position_meta(ofen)
                if record.get("phase") != phase or record.get("sideToMove") != side:
                    raise ValueError(
                        f"{path}:{line_number}: sampler OFEN metadata changed"
                    )
                minimum_ply, maximum_ply = phase_windows[phase]
                if not minimum_ply <= ply <= min(
                    maximum_ply, sampler["maxPlies"]
                ):
                    raise ValueError(
                        f"{path}:{line_number}: sampler ply is outside its phase"
                    )
                pieces, _, _ = core.parse_ofen(ofen)
                integer_metadata = {
                    "pieceCount": len(pieces),
                    "whitePieces": sum(piece_side == 0 for _, piece_side, _ in pieces),
                    "blackPieces": sum(piece_side != 0 for _, piece_side, _ in pieces),
                    "champions": sum(piece == 6 for piece, _, _ in pieces),
                    "wizards": sum(piece == 7 for piece, _, _ in pieces),
                    "halfmoveClock": int(ofen.split()[4]),
                }
                if any(
                    type(record.get(key)) is not int or record.get(key) != expected
                    for key, expected in integer_metadata.items()
                ):
                    raise ValueError(
                        f"{path}:{line_number}: sampler position metadata changed"
                    )
                source_rank = record.get("selectionRank")
                expected_source_rank = hashlib.sha256(
                    (
                        f"omega-root-sampler-v1\0{trajectory_seed}\0{pair_index}\0"
                        f"{flavor}\0{ply}\0{ofen}"
                    ).encode("utf-8")
                ).hexdigest()
                if (
                    type(source_rank) is not str
                    or source_rank != expected_source_rank
                    or source_rank in seen_source_ranks
                ):
                    raise ValueError(
                        f"{path}:{line_number}: sampler selection rank changed"
                    )
                seen_source_ranks.add(source_rank)
                match_rank = hashlib.sha256(
                    (
                        f"king-state-match-root-v1\0{spec['seed']}\0{pair_id}\0"
                        f"{trajectory_id}\0{ply}\0{orbit}"
                    ).encode("utf-8")
                ).hexdigest()
                roots.append(
                    core.Root(
                        gate=gate,
                        source_path=contract.resolve(path),
                        source_sha256=str(source_identity["sha256"]),
                        line=line_number,
                        generator_seed=int(spec["seed"]),
                        trajectory_pair_id=pair_id,
                        trajectory_id=trajectory_id,
                        flavor=str(flavor),
                        ply=ply,
                        phase=phase,
                        side=side,
                        ofen=ofen,
                        identity=identity,
                        orbit=orbit,
                        orbit_signatures=signatures,
                        rank=match_rank,
                    )
                )
                phase_counts[phase] += 1
                side_counts[side] += 1
    except UnicodeError as error:
        raise ValueError(f"{path}: sampler source is not strict UTF-8") from error
    if not roots:
        raise ValueError(f"{path}: sampler source is empty")
    return roots, dict(phase_counts), dict(side_counts)


def _verify_sampler_source(
    path: Path,
    gate: str,
    protocol: Mapping[str, Any],
    *,
    allow_reproduction_path: bool = False,
) -> tuple[list[core.Root], dict[str, Any]]:
    path = contract.resolve(path)
    if not allow_reproduction_path and path != _source_paths(protocol)[gate]:
        raise ValueError(f"{gate} sampler source escaped its canonical namespace")
    manifest_path = Path(str(path) + ".manifest.json")
    completion_path = Path(str(path) + ".complete.seal.json")
    source_before = contract.identity(path)
    manifest_before = contract.identity(manifest_path)
    completion_before = contract.identity(completion_path)
    manifest = contract.strict_load(manifest_path, f"{gate} sampler manifest")
    if set(manifest) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "policy",
        "coverage",
        "runtime",
        "output",
        "finalStageSeal",
    }:
        raise ValueError(f"{gate} sampler manifest field inventory changed")
    manifest_created = contract.parse_utc(
        manifest.get("createdUtc"), f"{gate} sampler manifest createdUtc"
    )
    if (
        type(manifest.get("schemaVersion")) is not int
        or manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("kind") != "omega-rules-only-random-root-manifest"
        or manifest.get("finalStageSeal") is not False
    ):
        raise ValueError(f"{gate} sampler manifest envelope changed")
    sampler = contract.mapping(protocol.get("suiteGeneration"), "suite generation")
    spec = core.GATE_SPECS[gate]
    expected_policy = {
        "deterministicPrng": "SplitMix64",
        "seed": str(spec["seed"]),
        "trajectoryPairs": sampler["trajectoryPairsPerStage"],
        "independentTrajectoriesPerPair": sampler["independentTrajectoriesPerPair"],
        "workers": sampler["workers"],
        "maxPlies": sampler["maxPlies"],
        "positionsPerPhaseAndSide": sampler["positionsPerPhaseAndSide"],
        "captureSelectionPercent": sampler["captureSelectionPercent"],
        "terminalRootsEmitted": 0,
        "maximumHalfmoveClock": 89,
        "minimumPieces": 7,
        "minimumPiecesPerSide": 2,
        "phasePlyWindows": {
            "opening": [6, 48],
            "middlegame": [20, 140],
            "late": [40, 260],
            "endgame": [60, 400],
        },
    }
    contract.require_exact_json(
        manifest.get("policy"), expected_policy, f"{gate} sampler policy"
    )
    _exact_sampler_identity(manifest.get("output"), path, f"{gate} sampler output")
    runtime = contract.mapping(manifest.get("runtime"), f"{gate} sampler runtime")
    if set(runtime) != {"framework", "samplerAssembly", "chessLibAssembly"}:
        raise ValueError(f"{gate} sampler runtime field inventory changed")
    framework = runtime.get("framework")
    if framework != f".NET {dotnet_runtime.RUNTIME_VERSION}":
        raise ValueError(f"{gate} sampler framework differs from the frozen runtime")
    sampler_assembly = _runtime_identity(protocol, "rootSamplerAssembly")
    rules_assembly = _runtime_identity(protocol, "rootSamplerRulesAssembly")
    if (
        not contract.exact_json_equal(
            runtime.get("samplerAssembly"), sampler_assembly
        )
        or not contract.exact_json_equal(
            runtime.get("chessLibAssembly"), rules_assembly
        )
    ):
        raise ValueError(f"{gate} sampler runtime identities changed")

    roots, phase_counts, side_counts = _strict_sampler_records(
        path, gate, source_before, protocol
    )
    coverage = contract.mapping(manifest.get("coverage"), f"{gate} sampler coverage")
    if set(coverage) != {
        "records",
        "phaseCounts",
        "sideToMoveCounts",
        "terminalTrajectories",
        "maxPlyReached",
        "promotionSelections",
        "enPassantClassification",
    }:
        raise ValueError(f"{gate} sampler coverage field inventory changed")
    expected_phase_counts = {phase: phase_counts.get(phase, 0) for phase in PHASES}
    expected_side_counts = {side: side_counts.get(side, 0) for side in contract.SIDES}
    terminal = coverage.get("terminalTrajectories")
    maximum_ply = coverage.get("maxPlyReached")
    promotions = coverage.get("promotionSelections")
    if (
        type(coverage.get("records")) is not int
        or coverage.get("records") != len(roots)
        or not contract.exact_json_equal(
            coverage.get("phaseCounts"), expected_phase_counts
        )
        or not contract.exact_json_equal(
            coverage.get("sideToMoveCounts"), expected_side_counts
        )
        or type(terminal) is not int
        or not 0 <= terminal <= 2 * sampler["trajectoryPairsPerStage"]
        or type(maximum_ply) is not int
        or not max(root.ply for root in roots) <= maximum_ply <= sampler["maxPlies"]
        or type(promotions) is not dict
        or set(promotions) != set("qrbncw")
        or any(type(item) is not int or item < 0 for item in promotions.values())
        or coverage.get("enPassantClassification")
        != (
            "An en-passant move lands on an empty target and remains in the "
            "ordinary move pool; legality still comes from ChessLib."
        )
    ):
        raise ValueError(f"{gate} sampler coverage changed")
    for phase in PHASES:
        for side in contract.SIDES:
            count = sum(
                1 for root in roots if root.phase == phase and root.side == side
            )
            if count < int(spec["rootsPerPhaseSide"]):
                raise ValueError(
                    f"{gate} has insufficient {phase}/{side} sampler roots"
                )

    completion = contract.strict_load(
        completion_path, f"{gate} sampler completion seal"
    )
    if set(completion) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "output",
        "manifest",
        "producer",
        "finalStageSeal",
    }:
        raise ValueError(f"{gate} sampler completion-seal fields changed")
    completion_created = contract.parse_utc(
        completion.get("createdUtc"), f"{gate} sampler completion createdUtc"
    )
    if (
        type(completion.get("schemaVersion")) is not int
        or completion.get("schemaVersion") != SCHEMA_VERSION
        or completion.get("kind")
        != "omega-rules-only-random-root-completion-seal"
        or completion.get("finalStageSeal") is not True
        or completion_created < manifest_created
    ):
        raise ValueError(f"{gate} sampler completion-seal envelope changed")
    _exact_sampler_identity(completion.get("output"), path, f"{gate} sealed output")
    _exact_sampler_identity(
        completion.get("manifest"), manifest_path, f"{gate} sealed manifest"
    )
    producer = contract.mapping(
        completion.get("producer"), f"{gate} sampler producer"
    )
    if (
        set(producer) != {"samplerAssembly", "chessLibAssembly", "framework"}
        or not contract.exact_json_equal(
            producer.get("samplerAssembly"), sampler_assembly
        )
        or not contract.exact_json_equal(
            producer.get("chessLibAssembly"), rules_assembly
        )
        or producer.get("framework") != framework
    ):
        raise ValueError(f"{gate} sampler completion producer changed")
    if (
        not contract.exact_json_equal(contract.identity(path), source_before)
        or not contract.exact_json_equal(
            contract.identity(manifest_path), manifest_before
        )
        or not contract.exact_json_equal(
            contract.identity(completion_path), completion_before
        )
    ):
        raise ValueError(f"{gate} sampler evidence changed during verification")
    return roots, {
        "source": source_before,
        "manifest": manifest_before,
        "completionSeal": completion_before,
        "runtime": [sampler_assembly, rules_assembly],
        "framework": framework,
        "workers": sampler["workers"],
        "records": len(roots),
        "phaseSideCounts": {
            f"{phase}:{side}": sum(
                1 for root in roots if root.phase == phase and root.side == side
            )
            for phase in PHASES
            for side in contract.SIDES
        },
    }


def _sampler_command(
    protocol: Mapping[str, Any], gate: str, output: Path
) -> tuple[list[str], Path, dict[str, Any], dict[str, Any]]:
    spec = core.GATE_SPECS[gate]
    dotnet = _runtime_identity(protocol, "dotnetHost")
    assembly = _runtime_identity(protocol, "rootSamplerAssembly")
    command = [
        str(dotnet["path"]),
        str(assembly["path"]),
        "--output",
        str(contract.resolve(output)),
        "--seed",
        str(spec["seed"]),
        "--trajectory-pairs",
        str(core.SAMPLER_TRAJECTORY_PAIRS),
        "--workers",
        str(protocol["suiteGeneration"]["workers"]),
        "--max-plies",
        str(core.SAMPLER_MAX_PLIES),
        "--positions-per-phase-side",
        str(core.SAMPLER_POSITIONS_PER_PHASE_SIDE),
        "--capture-percent",
        str(core.SAMPLER_CAPTURE_PERCENT),
    ]
    return command, Path(str(assembly["path"])).parent, dotnet, assembly


def _reproduce_sampler_source(
    source: Path, gate: str, protocol: Mapping[str, Any]
) -> dict[str, Any]:
    source_identity = contract.identity(source)
    dotnet_before = _dotnet_runtime_bundle(protocol)
    sampler_before = contract.root_sampler_bundle()
    with tempfile.TemporaryDirectory(prefix=f"omega-g5-{gate}-replay-") as directory:
        replay = Path(directory) / f"{gate}.jsonl"
        command, cwd, dotnet, assembly = _sampler_command(protocol, gate, replay)
        subprocess.run(
            command,
            cwd=cwd,
            check=True,
            env=_sanitized_environment(),
        )
        replay_roots, replay_audit = _verify_sampler_source(
            replay,
            gate,
            protocol,
            allow_reproduction_path=True,
        )
        replay_identity = contract.identity(replay)
        if (
            replay_identity["bytes"] != source_identity["bytes"]
            or replay_identity["sha256"] != source_identity["sha256"]
        ):
            raise ValueError(
                f"{gate} canonical sampler source differs from deterministic replay"
            )
        if (
            not contract.exact_json_equal(
                _dotnet_runtime_bundle(protocol), dotnet_before
            )
            or not contract.exact_json_equal(
                contract.root_sampler_bundle(), sampler_before
            )
            or not contract.exact_json_equal(
                contract.identity(Path(dotnet["path"])), dotnet
            )
            or not contract.exact_json_equal(
                contract.identity(Path(assembly["path"])), assembly
            )
        ):
            raise ValueError(f"{gate} sampler runtime changed during replay")
        return {
            "exactSourceBytes": replay_identity["bytes"],
            "exactSourceSha256": replay_identity["sha256"],
            "records": len(replay_roots),
            "runtime": replay_audit["runtime"],
            "framework": replay_audit["framework"],
            "workers": replay_audit["workers"],
            "dotnetRuntimeBundleSha256": dotnet_before["bundleSha256"],
            "rootSamplerBundleSha256": sampler_before["sha256"],
        }


def _sample(args: argparse.Namespace) -> None:
    protocol = contract.validate_protocol(args.protocol)
    _install_core_profile(protocol)
    output_dir = contract.namespace(protocol, "sampler")
    if args.output_dir is not None and contract.resolve(args.output_dir) != output_dir:
        raise ValueError("--output-dir differs from the frozen sampler namespace")
    output_dir.mkdir(parents=True, exist_ok=True)
    dotnet = _runtime_identity(protocol, "dotnetHost")
    dotnet_path = Path(dotnet["path"])
    sampler_assembly = _runtime_identity(protocol, "rootSamplerAssembly")
    sampler_path = Path(sampler_assembly["path"])
    dotnet_bundle_before = _dotnet_runtime_bundle(protocol)
    bundle_before = contract.root_sampler_bundle()
    for gate in GATES:
        spec = core.GATE_SPECS[gate]
        output = output_dir / core.SOURCE_NAMES[gate]
        manifest = Path(str(output) + ".manifest.json")
        completion = Path(str(output) + ".complete.seal.json")
        if output.exists() or manifest.exists() or completion.exists():
            raise FileExistsError(f"refusing to replace rules-only source: {output}")
        command, cwd, command_dotnet, command_assembly = _sampler_command(
            protocol, gate, output
        )
        if (
            not contract.exact_json_equal(command_dotnet, dotnet)
            or not contract.exact_json_equal(command_assembly, sampler_assembly)
        ):
            raise ValueError("root-sampler command identities changed")
        subprocess.run(
            command,
            cwd=cwd,
            check=True,
            env=_sanitized_environment(),
        )
        _verify_sampler_source(output, gate, protocol)
    bundle_after = contract.root_sampler_bundle()
    if (
        not contract.exact_json_equal(bundle_after, bundle_before)
        or not contract.exact_json_equal(
            _dotnet_runtime_bundle(protocol), dotnet_bundle_before
        )
        or not contract.exact_json_equal(contract.identity(dotnet_path), dotnet)
        or not contract.exact_json_equal(
            contract.identity(sampler_path), sampler_assembly
        )
    ):
        raise ValueError("root-sampler runtime changed during suite sampling")


def _sanitized_environment() -> dict[str, str]:
    env = dict(os.environ)
    forbidden_prefixes = (
        "dotnet_",
        "coreclr_",
        "cor_",
        "complus_",
        "corehost_",
    )
    for key in list(env):
        lowered = key.casefold()
        if lowered.startswith(forbidden_prefixes) or lowered in {
            "msbuildexepath",
            "msbuildsdkspath",
        }:
            del env[key]
    env.update(
        {
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_NOLOGO": "1",
            "DOTNET_MULTILEVEL_LOOKUP": "0",
            "DOTNET_ROOT": str(contract.resolve(dotnet_runtime.RUNTIME_ROOT)),
            "DOTNET_ROOT_X64": str(contract.resolve(dotnet_runtime.RUNTIME_ROOT)),
            "DOTNET_ROLL_FORWARD": "LatestPatch",
            "DOTNET_EnableDiagnostics": "0",
        }
    )
    return env


def _suite_paths(protocol: Mapping[str, Any]) -> dict[str, Path]:
    sealed = contract.namespace(protocol, "sealed")
    return {gate: sealed / f"king-state-v5-{gate}-suite.json" for gate in GATES}


def _source_paths(protocol: Mapping[str, Any]) -> dict[str, Path]:
    sampler = contract.namespace(protocol, "sampler")
    return {gate: sampler / core.SOURCE_NAMES[gate] for gate in GATES}


def _stage_paths(protocol: Mapping[str, Any]) -> dict[str, Path]:
    return {
        "development": contract.namespace(protocol, "development"),
        "equal-node": contract.namespace(protocol, "equalNode"),
        "equal-time": contract.namespace(protocol, "equalTime"),
    }


def _config_paths(protocol: Mapping[str, Any]) -> dict[str, Path]:
    sealed = contract.namespace(protocol, "sealed")
    return {
        gate: sealed / f"king-state-v5-{gate}-match.json" for gate in GATES
    }


def _expected_match_config(
    protocol: Mapping[str, Any],
    gate: str,
    suite_identity: Mapping[str, Any],
    engine: Mapping[str, Any],
    network: Mapping[str, Any],
    harness: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    stage = _stage_paths(protocol)[gate]
    value = core._match_config(
        gate,
        Path(str(suite_identity["path"])),
        str(suite_identity["sha256"]),
        dict(engine),
        dict(network),
        dict(harness),
        dict(bundle),
        stage.parent,
    )
    value["runId"] = f"king-state-v5-{gate}-{str(network['sha256'])[:12]}"
    value["profileId"] = PROFILE_ID
    value["freshnessMarker"] = (
        f"{PROFILE_ID}:{gate}:{str(network['sha256'])[:12]}"
    )
    value["outputDirectory"] = str(stage)
    value["kingStateMatchExecution"]["initialPairBudget"] = protocol["stages"][
        gate
    ]["initialPairBudget"]
    value["kingStateMatchExecution"]["resumePairBudget"] = protocol["stages"][gate][
        "resumePairBudget"
    ]
    return value


def _selected_summary(roots: Sequence[core.Root]) -> dict[str, Any]:
    return {
        "roots": len(roots),
        "phaseCounts": dict(sorted(Counter(root.phase for root in roots).items())),
        "sideToMoveCounts": dict(sorted(Counter(root.side for root in roots).items())),
        "uniqueTrajectoryPairs": len({root.source_group for root in roots}),
        "uniqueOrbits": len({root.orbit for root in roots}),
    }


def _strict_suite_value(path: Path, gate: str) -> dict[str, Any]:
    value = contract.strict_load(path, f"{gate} match suite")
    if set(value) != {"schemaVersion", "name", "kingStateMatchSuite", "openings"}:
        raise ValueError(f"{gate} suite field inventory changed")
    metadata = contract.mapping(
        value.get("kingStateMatchSuite"), f"{gate} suite metadata"
    )
    if set(metadata) != {
        "schemaVersion",
        "gate",
        "seed",
        "roots",
        "rootsPerPhase",
        "rootsPerPhaseSide",
        "balancedBlockSizePairs",
        "schedulePermutation",
    }:
        raise ValueError(f"{gate} suite metadata fields changed")
    for key in (
        "schemaVersion",
        "seed",
        "roots",
        "rootsPerPhase",
        "rootsPerPhaseSide",
        "balancedBlockSizePairs",
    ):
        contract.exact_int(metadata.get(key), f"{gate} suite metadata {key}")
    contract.exact_int(value.get("schemaVersion"), f"{gate} suite schemaVersion")
    openings = value.get("openings")
    if type(openings) is not list:
        raise ValueError(f"{gate} suite openings are not a list")
    for index, opening in enumerate(openings, 1):
        item = contract.mapping(opening, f"{gate} opening {index}")
        if set(item) != {"id", "source", "initialOfen", "moves", "kingStateMatch"}:
            raise ValueError(f"{gate} opening {index} fields changed")
        match = contract.mapping(
            item.get("kingStateMatch"), f"{gate} opening {index} metadata"
        )
        if set(match) != {
            "gate",
            "phase",
            "rootSideToMove",
            "balancedBlock",
            "generatorSeed",
            "trajectoryPairId",
            "trajectoryId",
            "sourceLine",
            "sourceSha256",
            "selectionRank",
            "identityKey",
            "symmetryOrbitKey",
            "orbitSignatures",
        }:
            raise ValueError(f"{gate} opening {index} metadata fields changed")
        for key in ("balancedBlock", "sourceLine"):
            contract.exact_int(
                match.get(key), f"{gate} opening {index} metadata {key}"
            )
    return value


def _seal_suites(args: argparse.Namespace) -> dict[str, Any]:
    protocol = contract.validate_protocol(args.protocol)
    _install_core_profile(protocol)
    profile_path = contract.resolve(args.profile)
    freeze_path = contract.resolve(args.final_freeze)
    profile = _validate_profile(profile_path, protocol)
    _validate_final_freeze(freeze_path, profile_path)
    manifests, positions, forbidden, exclusion_stats = _validate_forbidden_inputs(
        args.forbidden_catalog_manifest,
        args.forbidden_position_file,
        profile=profile,
    )
    _assert_profile_forbidden_manifests(profile, manifests)

    suite_seal_path = contract.namespace(protocol, "suiteSeal")
    suite_paths = _suite_paths(protocol)
    stage_paths = [
        contract.namespace(protocol, key)
        for key in ("development", "equalNode", "equalTime")
    ]
    outputs = [suite_seal_path, *suite_paths.values()]
    if any(path.exists() for path in outputs):
        raise FileExistsError(
            f"refusing to replace match-suite artifact: {next(path for path in outputs if path.exists())}"
        )
    if any(path.exists() for path in stage_paths):
        raise FileExistsError("a canonical match stage already exists before suite seal")

    selection_forbidden, current_private, current_audit = (
        _current_corpus_exclusion(profile, forbidden)
    )

    source_audits: dict[str, Any] = {}
    selected: dict[str, list[core.Root]] = {}
    rejected: dict[str, dict[str, int]] = {}
    used_fresh: set[str] = set()
    for gate, source in _source_paths(protocol).items():
        roots, audit = _verify_sampler_source(source, gate, protocol)
        runtime_identities = audit["runtime"]
        expected_runtime = [
            _runtime_identity(protocol, "rootSamplerAssembly"),
            _runtime_identity(protocol, "rootSamplerRulesAssembly"),
        ]
        if not contract.exact_json_equal(runtime_identities, expected_runtime):
            raise ValueError(f"{gate} sampler manifest runtime changed")
        audit["deterministicReplay"] = _reproduce_sampler_source(
            source, gate, protocol
        )
        # Fail on source-group/trajectory reuse before deterministic root
        # selection.  Position collisions remain candidates for the unioned
        # forbidden set to reject below.
        _gate_current_corpus_evidence(roots, (), current_private)
        selected[gate], rejected[gate] = core._select_roots(
            roots, core.GATE_SPECS[gate], selection_forbidden, used_fresh
        )
        audit["currentCorpusDisjointness"] = _gate_current_corpus_evidence(
            roots, selected[gate], current_private
        )
        source_audits[gate] = audit

    suite_seal_path.parent.mkdir(parents=True, exist_ok=True)
    schedules: dict[str, list[dict[str, Any]]] = {}
    try:
        for gate in GATES:
            suite, schedule = core._suite(gate, selected[gate])
            suite["name"] = f"Omega NNUE king-state v5 {gate}"
            suite["kingStateMatchSuite"]["schemaVersion"] = SCHEMA_VERSION
            schedules[gate] = schedule
            contract.atomic_json(suite_paths[gate], suite, exclusive=True)
            _strict_suite_value(suite_paths[gate], gate)
            core._verify_suite(gate, suite_paths[gate])
        root_digest = contract.root_digest(suite_paths)
        seal = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": SUITE_SEAL_KIND,
            "profileId": PROFILE_ID,
            "createdUtc": contract.utc_now(),
            "protocol": contract.identity(args.protocol),
            "preregistration": contract.identity(profile_path),
            "finalFreeze": contract.identity(freeze_path),
            "dotnetHost": _runtime_identity(protocol, "dotnetHost"),
            "dotnetRuntimeManifest": _runtime_identity(
                protocol, "dotnetRuntimeManifest"
            ),
            "dotnetRuntimeBundle": _dotnet_runtime_bundle(protocol),
            "matchCoreSource": _runtime_identity(protocol, "matchCoreSource"),
            "engine": _runtime_identity(protocol, "engine"),
            "omegaMatchAssembly": _runtime_identity(protocol, "omegaMatchAssembly"),
            "omegaMatchAppHost": _runtime_identity(protocol, "omegaMatchAppHost"),
            "omegaMatchBundle": core._harness_bundle_identity(
                Path(_runtime_identity(protocol, "omegaMatchAssembly")["path"])
            ),
            "rootSamplerBundle": contract.root_sampler_bundle(),
            "forbiddenCatalogManifests": manifests,
            "forbiddenPositionFiles": positions,
            "forbiddenAudit": {
                "uniqueOrbitSignatures": len(forbidden),
                "scan": exclusion_stats,
            },
            "currentCorpusAudit": current_audit,
            "samplerSources": source_audits,
            "suites": {
                gate: {
                    **_selected_summary(selected[gate]),
                    "rejections": rejected[gate],
                    "scheduledOpeningIds": [item["id"] for item in schedules[gate]],
                    "identity": contract.identity(suite_paths[gate]),
                }
                for gate in GATES
            },
            "rootDigest": root_digest,
            "crossSuite": {
                "roots": sum(len(items) for items in selected.values()),
                "uniqueOrbitSignatures": len(used_fresh),
                "mutuallyOrbitDisjoint": True,
            },
            "stageOutputsAbsentAtSeal": {
                gate: str(path)
                for gate, path in zip(GATES, stage_paths)
            },
            "candidateIdentity": None,
            "informationBoundary": {
                "rulesOnlySuiteConstruction": True,
                "targetFieldsDecoded": 0,
                "matchResultsAccessed": 0,
                "heldOutReportAccessed": False,
            },
        }
        contract.atomic_json(suite_seal_path, seal, exclusive=True)
    except BaseException:
        # Fail closed.  Any partially published suite blocks an automatic
        # retry and remains available for forensic comparison.
        raise
    # The immediate deep verification deliberately re-derives these sets.
    # Drop the first-pass multi-hundred-megabyte catalogs before entering it.
    del selection_forbidden, current_private, current_audit
    del forbidden, manifests, positions, exclusion_stats
    del source_audits, selected, rejected, schedules, used_fresh
    del seal, root_digest
    return _verify_suite_seal(suite_seal_path, protocol=protocol)


def _verify_suite_seal(
    path: Path,
    *,
    protocol: Mapping[str, Any] | None = None,
    deep: bool = True,
    reproduce_sources: bool = False,
    runtime_authority: bool = False,
) -> dict[str, Any]:
    if reproduce_sources and not deep:
        raise ValueError("sampler reproduction requires deep suite verification")
    if runtime_authority and (deep or reproduce_sources):
        raise ValueError("runtime authority mode is shallow verification only")
    protocol = contract.validate_protocol() if protocol is None else dict(protocol)
    _install_core_profile(protocol)
    path = contract.resolve(path)
    if path != contract.namespace(protocol, "suiteSeal"):
        raise ValueError("suite seal is outside its frozen namespace")
    value = contract.strict_load(path, "Generation-5 suite seal")
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "protocol",
        "preregistration",
        "finalFreeze",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeBundle",
        "matchCoreSource",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "omegaMatchBundle",
        "rootSamplerBundle",
        "forbiddenCatalogManifests",
        "forbiddenPositionFiles",
        "forbiddenAudit",
        "currentCorpusAudit",
        "samplerSources",
        "suites",
        "rootDigest",
        "crossSuite",
        "stageOutputsAbsentAtSeal",
        "candidateIdentity",
        "informationBoundary",
    }
    if set(value) != expected_fields:
        raise ValueError("suite-seal field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != SUITE_SEAL_KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("candidateIdentity") is not None
        or not contract.exact_json_equal(
            value.get("informationBoundary"),
            {
            "rulesOnlySuiteConstruction": True,
            "targetFieldsDecoded": 0,
            "matchResultsAccessed": 0,
            "heldOutReportAccessed": False,
            },
        )
    ):
        raise ValueError("suite-seal envelope/information boundary changed")
    contract.parse_utc(value.get("createdUtc"), "suite-seal createdUtc")
    if not contract.exact_json_equal(
        value.get("protocol"), contract.identity(contract.PROTOCOL_PATH)
    ):
        raise ValueError("suite seal protocol identity changed")
    profile_path = contract.verify_identity(value.get("preregistration"), "suite profile")
    freeze_path = contract.verify_identity(value.get("finalFreeze"), "suite final freeze")
    if runtime_authority:
        profile, freeze = _validate_runtime_authority_capsule(
            profile_path, freeze_path, protocol
        )
    else:
        profile = _validate_profile(profile_path, protocol)
        freeze = _validate_final_freeze(freeze_path, profile_path)
    suite_created = contract.parse_utc(value.get("createdUtc"), "suite-seal createdUtc")
    if suite_created < contract.parse_utc(
        freeze.get("createdUtc"), "final-freeze createdUtc"
    ):
        raise ValueError("suite seal predates the final freeze")
    for key in (
        "dotnetHost",
        "dotnetRuntimeManifest",
        "matchCoreSource",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
    ):
        if not contract.exact_json_equal(
            value.get(key), _runtime_identity(protocol, key)
        ):
            raise ValueError(f"suite-seal {key} changed")
    if not contract.exact_json_equal(
        value.get("dotnetRuntimeBundle"), _dotnet_runtime_bundle(protocol)
    ):
        raise ValueError("suite-seal .NET runtime bundle changed")
    bundle = value.get("omegaMatchBundle")
    current_bundle = core._harness_bundle_identity(Path(value["omegaMatchAssembly"]["path"]))
    if not contract.exact_json_equal(bundle, current_bundle):
        raise ValueError("suite-seal OmegaMatch bundle changed")
    if not contract.exact_json_equal(
        value.get("rootSamplerBundle"), contract.root_sampler_bundle()
    ):
        raise ValueError("suite-seal root-sampler bundle changed")
    forbidden: set[str] = set()
    selection_forbidden: set[str] = set()
    current_private: dict[str, set[str]] = {}
    expected_selected: dict[str, list[core.Root]] = {}
    expected_rejections: dict[str, dict[str, int]] = {}
    for list_name in ("forbiddenCatalogManifests", "forbiddenPositionFiles"):
        records = value.get(list_name)
        if type(records) is not list or not records or any(
            type(item) is not dict
            or set(item) != {"path", "bytes", "sha256"}
            for item in records
        ):
            raise ValueError(f"suite {list_name} inventory changed")
        for index, item in enumerate(records, 1):
            _identity_shape(item, f"suite {list_name} {index}")
    forbidden_audit = contract.mapping(
        value.get("forbiddenAudit"), "suite forbidden audit"
    )
    forbidden_scan = contract.mapping(
        forbidden_audit.get("scan"), "suite forbidden scan"
    )
    if (
        set(forbidden_audit) != {"uniqueOrbitSignatures", "scan"}
        or set(forbidden_scan)
        != {
            "catalogs",
            "positions",
            "exactPositionKeys",
            "orbitSignatures",
            "sourceGameIds",
            "sourceRunIds",
            "sourceDataInputSha256",
        }
    ):
        raise ValueError("suite forbidden-audit fields changed")
    contract.exact_int(
        forbidden_audit.get("uniqueOrbitSignatures"),
        "suite forbidden unique-orbit count",
    )
    for key, item in forbidden_scan.items():
        contract.exact_int(item, f"suite forbidden scan {key}")
    recorded_current_audit = _validate_current_corpus_audit(
        value.get("currentCorpusAudit")
    )
    source_audits = contract.mapping(
        value.get("samplerSources"), "suite sampler sources"
    )
    if set(source_audits) != set(GATES):
        raise ValueError("suite sampler-source inventory changed")
    for gate in GATES:
        recorded = contract.mapping(
            source_audits.get(gate), f"{gate} recorded sampler audit"
        )
        if set(recorded) != {
            "source",
            "manifest",
            "completionSeal",
            "runtime",
            "framework",
            "workers",
            "records",
            "phaseSideCounts",
            "deterministicReplay",
            "currentCorpusDisjointness",
        }:
            raise ValueError(f"{gate} sampler-audit field inventory changed")
        contract.exact_int(recorded.get("workers"), f"{gate} sampler workers")
        contract.exact_int(recorded.get("records"), f"{gate} sampler records")
        for key in ("source", "manifest", "completionSeal"):
            _identity_shape(recorded.get(key), f"{gate} sampler {key}")
        runtime_identities = recorded.get("runtime")
        if type(runtime_identities) is not list or len(runtime_identities) != 2:
            raise ValueError(f"{gate} sampler runtime identity inventory changed")
        for index, item in enumerate(runtime_identities, 1):
            _identity_shape(item, f"{gate} sampler runtime identity {index}")
        if type(recorded.get("framework")) is not str or not recorded["framework"]:
            raise ValueError(f"{gate} sampler framework changed")
        phase_side_counts = contract.mapping(
            recorded.get("phaseSideCounts"), f"{gate} phase/side counts"
        )
        for bucket, count in phase_side_counts.items():
            contract.exact_int(count, f"{gate} {bucket} count")
        replay_record = contract.mapping(
            recorded.get("deterministicReplay"),
            f"{gate} deterministic sampler replay",
        )
        if set(replay_record) != {
            "exactSourceBytes",
            "exactSourceSha256",
            "records",
            "runtime",
            "framework",
            "workers",
            "dotnetRuntimeBundleSha256",
            "rootSamplerBundleSha256",
        }:
            raise ValueError(f"{gate} deterministic-replay fields changed")
        for key in ("exactSourceBytes", "records", "workers"):
            contract.exact_int(
                replay_record.get(key), f"{gate} deterministic replay {key}"
            )
        for key in (
            "exactSourceSha256",
            "dotnetRuntimeBundleSha256",
            "rootSamplerBundleSha256",
        ):
            digest = replay_record.get(key)
            if type(digest) is not str or contract.HEX_256.fullmatch(digest) is None:
                raise ValueError(f"{gate} deterministic replay {key} changed")
        replay_runtime = replay_record.get("runtime")
        if type(replay_runtime) is not list or len(replay_runtime) != 2:
            raise ValueError(f"{gate} deterministic replay runtime changed")
        for index, item in enumerate(replay_runtime, 1):
            _identity_shape(
                item, f"{gate} deterministic replay runtime identity {index}"
            )
        if (
            type(replay_record.get("framework")) is not str
            or not replay_record["framework"]
        ):
            raise ValueError(f"{gate} deterministic replay framework changed")
        _validate_gate_current_evidence(
            recorded.get("currentCorpusDisjointness"), gate
        )
    if deep:
        manifests = contract.strict_identity_list(
            value.get("forbiddenCatalogManifests"), "suite forbidden manifest"
        )
        positions = contract.strict_identity_list(
            value.get("forbiddenPositionFiles"), "suite forbidden position"
        )
        _assert_profile_forbidden_manifests(profile, manifests)
        replay_manifests, replay_positions, forbidden, exclusion_stats = (
            _validate_forbidden_inputs(
                [Path(item["path"]) for item in manifests],
                [Path(item["path"]) for item in positions],
                profile=profile,
            )
        )
        if (
            not contract.exact_json_equal(replay_manifests, manifests)
            or not contract.exact_json_equal(replay_positions, positions)
        ):
            raise ValueError("suite-seal forbidden catalog identities changed")
        if not contract.exact_json_equal(
            value.get("forbiddenAudit"),
            {
                "uniqueOrbitSignatures": len(forbidden),
                "scan": exclusion_stats,
            },
        ):
            raise ValueError("suite-seal forbidden-position audit changed")
        selection_forbidden, current_private, replay_current_audit = (
            _current_corpus_exclusion(profile, forbidden)
        )
        if not contract.exact_json_equal(
            recorded_current_audit, replay_current_audit
        ):
            raise ValueError("suite-seal current-corpus audit changed")
        used_expected: set[str] = set()
        for gate, source in _source_paths(protocol).items():
            recorded = contract.mapping(
                source_audits.get(gate), f"{gate} recorded sampler audit"
            )
            if "deterministicReplay" not in recorded:
                raise ValueError(f"{gate} lacks deterministic sampler replay")
            recorded_base = dict(recorded)
            recorded_replay = contract.mapping(
                recorded_base.pop("deterministicReplay"),
                f"{gate} deterministic sampler replay",
            )
            recorded_disjointness = contract.mapping(
                recorded_base.pop("currentCorpusDisjointness"),
                f"{gate} current-corpus disjointness",
            )
            roots, recomputed_audit = _verify_sampler_source(source, gate, protocol)
            if not contract.exact_json_equal(
                recomputed_audit.get("runtime"),
                [
                    _runtime_identity(protocol, "rootSamplerAssembly"),
                    _runtime_identity(protocol, "rootSamplerRulesAssembly"),
                ],
            ):
                raise ValueError(f"{gate} sampler manifest runtime changed")
            if not contract.exact_json_equal(recorded_base, recomputed_audit):
                raise ValueError(f"{gate} sampler-source audit changed")
            expected_replay = {
                "exactSourceBytes": recomputed_audit["source"]["bytes"],
                "exactSourceSha256": recomputed_audit["source"]["sha256"],
                "records": recomputed_audit["records"],
                "runtime": recomputed_audit["runtime"],
                "framework": recomputed_audit["framework"],
                "workers": recomputed_audit["workers"],
                "dotnetRuntimeBundleSha256": value["dotnetRuntimeBundle"][
                    "bundleSha256"
                ],
                "rootSamplerBundleSha256": value["rootSamplerBundle"]["sha256"],
            }
            if not contract.exact_json_equal(recorded_replay, expected_replay):
                raise ValueError(f"{gate} deterministic replay audit changed")
            if reproduce_sources and _reproduce_sampler_source(
                source, gate, protocol
            ) != recorded_replay:
                raise ValueError(f"{gate} deterministic replay changed")
            _gate_current_corpus_evidence(roots, (), current_private)
            expected_selected[gate], expected_rejections[gate] = core._select_roots(
                roots,
                core.GATE_SPECS[gate],
                selection_forbidden,
                used_expected,
            )
            expected_disjointness = _gate_current_corpus_evidence(
                roots, expected_selected[gate], current_private
            )
            if not contract.exact_json_equal(
                recorded_disjointness, expected_disjointness
            ):
                raise ValueError(
                    f"{gate} current-corpus disjointness evidence changed"
                )
    suites = contract.mapping(value.get("suites"), "sealed suites")
    if set(suites) != set(GATES):
        raise ValueError("suite-seal gate inventory changed")
    suite_paths: dict[str, Path] = {}
    fresh: set[str] = set()
    for gate in GATES:
        entry = contract.mapping(suites.get(gate), f"{gate} suite entry")
        if set(entry) != {
            "roots",
            "phaseCounts",
            "sideToMoveCounts",
            "uniqueTrajectoryPairs",
            "uniqueOrbits",
            "rejections",
            "scheduledOpeningIds",
            "identity",
        }:
            raise ValueError(f"{gate} suite-entry field inventory changed")
        suite_path = contract.verify_identity(entry.get("identity"), f"{gate} suite")
        expected_path = _suite_paths(protocol)[gate]
        if suite_path != expected_path:
            raise ValueError(f"{gate} suite path changed")
        suite_value = _strict_suite_value(suite_path, gate)
        openings = core._verify_suite(gate, suite_path)
        expected_schedule: list[dict[str, Any]] | None = None
        if deep:
            expected_suite, expected_schedule = core._suite(
                gate, expected_selected[gate]
            )
            expected_suite["name"] = f"Omega NNUE king-state v5 {gate}"
            expected_suite["kingStateMatchSuite"]["schemaVersion"] = SCHEMA_VERSION
            if not contract.exact_json_equal(suite_value, expected_suite):
                raise ValueError(
                    f"{gate} suite differs from deterministic selection replay"
                )
        expected_summary = {
            "roots": len(openings),
            "phaseCounts": dict(
                sorted(Counter(item["kingStateMatch"]["phase"] for item in openings.values()).items())
            ),
            "sideToMoveCounts": dict(
                sorted(
                    Counter(
                        item["kingStateMatch"]["rootSideToMove"]
                        for item in openings.values()
                    ).items()
                )
            ),
            "uniqueTrajectoryPairs": len(
                {
                    item["kingStateMatch"]["trajectoryPairId"]
                    for item in openings.values()
                }
            ),
            "uniqueOrbits": len(
                {
                    item["kingStateMatch"]["symmetryOrbitKey"]
                    for item in openings.values()
                }
            ),
        }
        for key, expected in expected_summary.items():
            if not contract.exact_json_equal(entry.get(key), expected):
                raise ValueError(f"{gate} suite summary {key} changed")
        if deep and not contract.exact_json_equal(
            entry.get("rejections"), expected_rejections[gate]
        ):
            raise ValueError(f"{gate} deterministic rejection audit changed")
        if not deep:
            rejections = contract.mapping(
                entry.get("rejections"), f"{gate} rejection audit"
            )
            for key, count in rejections.items():
                contract.exact_int(count, f"{gate} rejection count {key}")
        source_openings = suite_value.get("openings")
        if type(source_openings) is not list:
            raise ValueError(f"{gate} suite opening list changed")
        scheduled_openings = [
            source_openings[index]
            for index in core._shuffled_indices(
                len(source_openings), int(core.GATE_SPECS[gate]["seed"])
            )
        ]
        scheduled_ids = [item["id"] for item in scheduled_openings]
        if deep and [item["id"] for item in expected_schedule or []] != scheduled_ids:
            raise ValueError(f"{gate} replayed schedule permutation changed")
        if not contract.exact_json_equal(
            entry.get("scheduledOpeningIds"), scheduled_ids
        ):
            raise ValueError(f"{gate} scheduled opening order changed")
        for opening in openings.values():
            signatures = set(opening["kingStateMatch"]["orbitSignatures"])
            if deep and selection_forbidden.intersection(signatures):
                raise ValueError(
                    f"{gate} suite intersects historical/current forbidden data"
                )
            if fresh.intersection(signatures):
                raise ValueError("sealed suites are not mutually orbit disjoint")
            fresh.update(signatures)
        suite_paths[gate] = suite_path
    if not contract.exact_json_equal(
        value.get("rootDigest"), contract.root_digest(suite_paths)
    ):
        raise ValueError("sealed root digest changed")
    cross = contract.mapping(value.get("crossSuite"), "cross-suite audit")
    if (
        set(cross)
        != {"roots", "uniqueOrbitSignatures", "mutuallyOrbitDisjoint"}
        or
        type(cross.get("roots")) is not int
        or cross.get("roots") != sum(core.GATE_SPECS[gate]["roots"] for gate in GATES)
        or type(cross.get("uniqueOrbitSignatures")) is not int
        or cross.get("uniqueOrbitSignatures") != len(fresh)
        or cross.get("mutuallyOrbitDisjoint") is not True
    ):
        raise ValueError("cross-suite audit changed")
    expected_stage_paths = {
        gate: str(
            contract.namespace(
                protocol,
                {"development": "development", "equal-node": "equalNode", "equal-time": "equalTime"}[gate],
            )
        )
        for gate in GATES
    }
    contract.require_exact_json(
        value.get("stageOutputsAbsentAtSeal"),
        expected_stage_paths,
        "suite-seal stage namespace claims",
    )
    return value


def _load_selected_primary(offline_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    # Deliberately lazy: target-blind sample/seal/verify commands cannot even
    # import the trainer.  This call is authorized only after the one-time
    # held-out report already exists.
    import king_state_train_generation5 as training

    _verify_lazy_frozen_module(
        training,
        identity_name="trainerSource",
        label="Generation-5 trainer module",
        binding_names=("_verify_offline_report",),
    )

    report = training._verify_offline_report(offline_path)
    authorization = contract.mapping(report.get("matchAuthorization"), "offline authorization")
    if authorization.get("authorized") is not True or report["gate"]["passed"] is not True:
        raise ValueError("one-time held-out report did not authorize matches")
    network = contract.mapping(
        authorization.get("selectedPrimaryNetwork"), "selected primary network"
    )
    if not contract.exact_json_equal(
        report["evaluatedNetworks"]["selectedPrimary"], network
    ):
        raise ValueError("offline selected-network identities disagree")
    contract.verify_identity(network, "selected primary network")
    selection_path = contract.verify_identity(report.get("selection"), "offline selection")
    selection = contract.strict_load(selection_path, "Generation-5 validation selection")
    if not contract.exact_json_equal(selection.get("winnerNetwork"), network):
        raise ValueError("validation selection/offline network identities disagree")
    manifest = contract.mapping(selection.get("winnerManifest"), "winner manifest")
    contract.verify_identity(manifest, "winner manifest")
    return report, {"network": network, "manifest": manifest}


def _authorize(args: argparse.Namespace) -> dict[str, Any]:
    protocol = contract.validate_protocol(args.protocol)
    _install_core_profile(protocol)
    suite_seal_path = contract.resolve(args.suite_seal)
    suite_seal = _verify_suite_seal(
        suite_seal_path,
        protocol=protocol,
        deep=True,
        reproduce_sources=True,
    )
    offline_path = contract.resolve(args.offline_report)
    offline_before = contract.identity(offline_path)
    report, selected = _load_selected_primary(offline_path)
    if not contract.exact_json_equal(
        contract.identity(offline_path), offline_before
    ):
        raise ValueError("offline report changed during match authorization")
    network = selected["network"]
    engine = suite_seal["engine"]
    harness = suite_seal["omegaMatchAssembly"]
    bundle = suite_seal["omegaMatchBundle"]

    authorization_path = contract.namespace(protocol, "authorization")
    core_seal_path = contract.namespace(protocol, "coreSeal")
    sealed_dir = contract.namespace(protocol, "sealed")
    audit_path = sealed_dir / "match-audit.json"
    configs = _config_paths(protocol)
    stage_paths = _stage_paths(protocol)
    outputs = [authorization_path, core_seal_path, audit_path, *configs.values()]
    if any(path.exists() for path in outputs):
        raise FileExistsError(
            f"refusing to replace match authorization artifact: {next(path for path in outputs if path.exists())}"
        )
    if any(path.exists() for path in stage_paths.values()):
        raise FileExistsError("a match stage exists before authorization")

    suite_entries = contract.mapping(suite_seal.get("suites"), "sealed suites")
    config_values: dict[str, dict[str, Any]] = {}
    for gate in GATES:
        suite_identity = suite_entries[gate]["identity"]
        value = _expected_match_config(
            protocol,
            gate,
            suite_identity,
            engine,
            network,
            harness,
            bundle,
        )
        config_values[gate] = value
        contract.atomic_json(configs[gate], value, exclusive=True)
        core._verify_config(
            gate,
            configs[gate],
            suite_identity,
            network["sha256"],
            engine["sha256"],
            harness["sha256"],
            bundle["sha256"],
        )

    audit = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUDIT_KIND,
        "profileId": PROFILE_ID,
        "createdUtc": contract.utc_now(),
        "protocol": contract.identity(args.protocol),
        "suiteSeal": contract.identity(suite_seal_path),
        "offlineReport": contract.identity(offline_path),
        "selectedNetwork": network,
        "selectedManifest": selected["manifest"],
        "dotnetHost": suite_seal["dotnetHost"],
        "dotnetRuntimeManifest": suite_seal["dotnetRuntimeManifest"],
        "dotnetRuntimeBundle": suite_seal["dotnetRuntimeBundle"],
        "matchCoreSource": suite_seal["matchCoreSource"],
        "engine": engine,
        "omegaMatchAssembly": harness,
        "omegaMatchBundle": bundle,
        "rootDigest": suite_seal["rootDigest"],
        "gates": {
            gate: {
                "suite": suite_entries[gate]["identity"],
                "config": contract.identity(configs[gate]),
                "runId": config_values[gate]["runId"],
                "outputDirectory": config_values[gate]["outputDirectory"],
            }
            for gate in GATES
        },
        "candidateControlIsolation": {
            "sameExecutable": True,
            "candidateUseOmegaNNUE": True,
            "candidateAssetSha256": network["sha256"],
            "controlUseOmegaNNUE": False,
            "controlOmegaNNUEFile": "<empty>",
        },
        "heldOutReportConsumedOnlyForAuthorizationAndVerification": True,
        "matchResultsAccessed": 0,
    }
    contract.atomic_json(audit_path, audit, exclusive=True)

    pinned = [
        contract.identity(args.protocol),
        suite_seal["preregistration"],
        suite_seal["finalFreeze"],
        contract.identity(suite_seal_path),
        contract.identity(offline_path),
        report["selection"],
        report["robustness"],
        report["accessClaim"],
        network,
        selected["manifest"],
        suite_seal["dotnetHost"],
        suite_seal["dotnetRuntimeManifest"],
        _runtime_identity(protocol, "dotnetRuntimeTool"),
        suite_seal["matchCoreSource"],
        engine,
        harness,
        suite_seal["omegaMatchAppHost"],
        *[suite_entries[gate]["identity"] for gate in GATES],
        *[contract.identity(configs[gate]) for gate in GATES],
        contract.identity(audit_path),
    ]
    by_path = {Path(item["path"]).resolve(): item for item in pinned}
    core_seal = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v1-match-seal",
        "sealedUtc": contract.utc_now(),
        "generationId": "omega-nnue-king-state-v5",
        "protocolSha256": contract.identity(args.protocol)["sha256"],
        "candidateNetworkSha256": network["sha256"],
        "engineExecutableSha256": engine["sha256"],
        "matchCoreSourceSha256": suite_seal["matchCoreSource"]["sha256"],
        "omegaMatchAssemblySha256": harness["sha256"],
        "pinnedFiles": sorted(by_path.values(), key=lambda item: item["path"].casefold()),
        "omegaMatchBundle": bundle,
        "dotnetRuntimeBundle": suite_seal["dotnetRuntimeBundle"],
        "gates": audit["gates"],
        "audit": contract.identity(audit_path),
    }
    contract.atomic_json(core_seal_path, core_seal, exclusive=True)
    core._verify_seal(core_seal_path)
    authorization = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUTHORIZATION_KIND,
        "profileId": PROFILE_ID,
        "createdUtc": contract.utc_now(),
        "protocol": contract.identity(args.protocol),
        "suiteSeal": contract.identity(suite_seal_path),
        "offlineReport": contract.identity(offline_path),
        "offlineMatchAuthorization": copy.deepcopy(report["matchAuthorization"]),
        "selectedNetwork": network,
        "selectedManifest": selected["manifest"],
        "dotnetHost": suite_seal["dotnetHost"],
        "dotnetRuntimeManifest": suite_seal["dotnetRuntimeManifest"],
        "dotnetRuntimeBundle": suite_seal["dotnetRuntimeBundle"],
        "matchCoreSource": suite_seal["matchCoreSource"],
        "engine": engine,
        "omegaMatchAssembly": harness,
        "omegaMatchAppHost": suite_seal["omegaMatchAppHost"],
        "omegaMatchBundle": bundle,
        "coreSeal": contract.identity(core_seal_path),
        "stageOrder": list(GATES),
        "stageOutputsAbsentAtAuthorization": {
            gate: str(path) for gate, path in stage_paths.items()
        },
        "runnerUpFallback": False,
        "launchOnlyThrough": "tools/omega_nnue/king_state_matches_generation5.py",
    }
    contract.atomic_json(authorization_path, authorization, exclusive=True)
    return _verify_authorization(authorization_path, protocol=protocol)


def _verify_authorization(
    path: Path, *, protocol: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    protocol = contract.validate_protocol() if protocol is None else dict(protocol)
    _install_core_profile(protocol)
    path = contract.resolve(path)
    if path != contract.namespace(protocol, "authorization"):
        raise ValueError("match authorization is outside its frozen namespace")
    value = contract.strict_load(path, "Generation-5 match authorization")
    expected = {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "protocol",
        "suiteSeal",
        "offlineReport",
        "offlineMatchAuthorization",
        "selectedNetwork",
        "selectedManifest",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeBundle",
        "matchCoreSource",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "omegaMatchBundle",
        "coreSeal",
        "stageOrder",
        "stageOutputsAbsentAtAuthorization",
        "runnerUpFallback",
        "launchOnlyThrough",
    }
    if set(value) != expected:
        raise ValueError("match-authorization field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != AUTHORIZATION_KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("stageOrder") != list(GATES)
        or value.get("runnerUpFallback") is not False
        or value.get("launchOnlyThrough")
        != "tools/omega_nnue/king_state_matches_generation5.py"
    ):
        raise ValueError("match-authorization envelope changed")
    contract.parse_utc(value.get("createdUtc"), "match authorization createdUtc")
    if not contract.exact_json_equal(
        value.get("protocol"), contract.identity(contract.PROTOCOL_PATH)
    ):
        raise ValueError("authorization protocol identity changed")
    suite_path = contract.verify_identity(value.get("suiteSeal"), "authorization suite seal")
    suite = _verify_suite_seal(suite_path, protocol=protocol, deep=False)
    offline_path = contract.verify_identity(
        value.get("offlineReport"), "authorization offline report"
    )
    offline_before = contract.identity(offline_path)
    report, selected = _load_selected_primary(offline_path)
    if not contract.exact_json_equal(
        contract.identity(offline_path), offline_before
    ):
        raise ValueError("offline report changed during authorization verification")
    offline_authorization = contract.mapping(
        value.get("offlineMatchAuthorization"), "offline match authorization capsule"
    )
    contract.require_exact_json(
        offline_authorization,
        report.get("matchAuthorization"),
        "offline match authorization capsule",
    )
    created = contract.parse_utc(value.get("createdUtc"), "match authorization createdUtc")
    if created < contract.parse_utc(suite.get("createdUtc"), "suite seal createdUtc"):
        raise ValueError("match authorization predates the suite seal")
    identity_bindings = {
        "selectedNetwork": selected["network"],
        "selectedManifest": selected["manifest"],
        "dotnetHost": suite["dotnetHost"],
        "dotnetRuntimeManifest": suite["dotnetRuntimeManifest"],
        "dotnetRuntimeBundle": suite["dotnetRuntimeBundle"],
        "matchCoreSource": suite["matchCoreSource"],
        "engine": suite["engine"],
        "omegaMatchAssembly": suite["omegaMatchAssembly"],
        "omegaMatchAppHost": suite["omegaMatchAppHost"],
        "omegaMatchBundle": suite["omegaMatchBundle"],
    }
    if any(
        not contract.exact_json_equal(value.get(key), expected_value)
        for key, expected_value in identity_bindings.items()
    ):
        raise ValueError("authorization identity binding changed")
    core_path = contract.verify_identity(value.get("coreSeal"), "authorization core seal")
    if core_path != contract.namespace(protocol, "coreSeal"):
        raise ValueError("authorization core seal path changed")
    strict_core_seal = contract.strict_load(core_path, "Generation-5 core match seal")
    if set(strict_core_seal) != {
        "schemaVersion",
        "kind",
        "sealedUtc",
        "generationId",
        "protocolSha256",
        "candidateNetworkSha256",
        "engineExecutableSha256",
        "matchCoreSourceSha256",
        "omegaMatchAssemblySha256",
        "pinnedFiles",
        "omegaMatchBundle",
        "dotnetRuntimeBundle",
        "gates",
        "audit",
    }:
        raise ValueError("core match-seal field inventory changed")
    if (
        type(strict_core_seal.get("schemaVersion")) is not int
        or strict_core_seal.get("schemaVersion") != SCHEMA_VERSION
        or strict_core_seal.get("kind") != "omega-nnue-king-state-v1-match-seal"
    ):
        raise ValueError("core match-seal envelope changed")
    contract.parse_utc(strict_core_seal.get("sealedUtc"), "core match-seal sealedUtc")
    core_seal = core._verify_seal(core_path)
    if not contract.exact_json_equal(core_seal, strict_core_seal):
        raise ValueError("strict/core match-seal parses differ")
    if (
        core_seal.get("candidateNetworkSha256") != selected["network"]["sha256"]
        or core_seal.get("engineExecutableSha256") != suite["engine"]["sha256"]
        or core_seal.get("omegaMatchAssemblySha256")
        != suite["omegaMatchAssembly"]["sha256"]
        or core_seal.get("protocolSha256")
        != contract.identity(contract.PROTOCOL_PATH)["sha256"]
        or core_seal.get("generationId") != "omega-nnue-king-state-v5"
        or core_seal.get("matchCoreSourceSha256")
        != suite["matchCoreSource"]["sha256"]
        or not contract.exact_json_equal(
            core_seal.get("omegaMatchBundle"), suite["omegaMatchBundle"]
        )
        or not contract.exact_json_equal(
            core_seal.get("dotnetRuntimeBundle"), suite["dotnetRuntimeBundle"]
        )
    ):
        raise ValueError("core seal differs from match authorization")
    suite_entries = contract.mapping(suite.get("suites"), "authorization suites")
    config_paths = _config_paths(protocol)
    expected_gates: dict[str, dict[str, Any]] = {}
    for gate in GATES:
        suite_identity = contract.mapping(
            suite_entries[gate].get("identity"), f"{gate} authorized suite"
        )
        expected_config = _expected_match_config(
            protocol,
            gate,
            suite_identity,
            suite["engine"],
            selected["network"],
            suite["omegaMatchAssembly"],
            suite["omegaMatchBundle"],
        )
        config_path = config_paths[gate]
        contract.require_exact_json(
            contract.strict_load(config_path, f"{gate} exact match config"),
            expected_config,
            f"{gate} exact deterministic match config",
        )
        config_identity = contract.identity(config_path)
        expected_gates[gate] = {
            "suite": suite_identity,
            "config": config_identity,
            "runId": expected_config["runId"],
            "outputDirectory": expected_config["outputDirectory"],
        }
    contract.require_exact_json(
        core_seal.get("gates"), expected_gates, "core seal gate/config bindings"
    )
    audit_path = contract.namespace(protocol, "sealed") / "match-audit.json"
    if not contract.exact_json_equal(
        core_seal.get("audit"), contract.identity(audit_path)
    ):
        raise ValueError("core seal audit path or identity changed")
    audit = contract.strict_load(audit_path, "Generation-5 match audit")
    expected_audit_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "protocol",
        "suiteSeal",
        "offlineReport",
        "selectedNetwork",
        "selectedManifest",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeBundle",
        "matchCoreSource",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchBundle",
        "rootDigest",
        "gates",
        "candidateControlIsolation",
        "heldOutReportConsumedOnlyForAuthorizationAndVerification",
        "matchResultsAccessed",
    }
    if set(audit) != expected_audit_fields:
        raise ValueError("match-audit field inventory changed")
    contract.parse_utc(audit.get("createdUtc"), "match-audit createdUtc")
    expected_audit = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUDIT_KIND,
        "profileId": PROFILE_ID,
        "createdUtc": audit["createdUtc"],
        "protocol": contract.identity(contract.PROTOCOL_PATH),
        "suiteSeal": contract.identity(suite_path),
        "offlineReport": contract.identity(offline_path),
        "selectedNetwork": selected["network"],
        "selectedManifest": selected["manifest"],
        "dotnetHost": suite["dotnetHost"],
        "dotnetRuntimeManifest": suite["dotnetRuntimeManifest"],
        "dotnetRuntimeBundle": suite["dotnetRuntimeBundle"],
        "matchCoreSource": suite["matchCoreSource"],
        "engine": suite["engine"],
        "omegaMatchAssembly": suite["omegaMatchAssembly"],
        "omegaMatchBundle": suite["omegaMatchBundle"],
        "rootDigest": suite["rootDigest"],
        "gates": expected_gates,
        "candidateControlIsolation": {
            "sameExecutable": True,
            "candidateUseOmegaNNUE": True,
            "candidateAssetSha256": selected["network"]["sha256"],
            "controlUseOmegaNNUE": False,
            "controlOmegaNNUEFile": "<empty>",
        },
        "heldOutReportConsumedOnlyForAuthorizationAndVerification": True,
        "matchResultsAccessed": 0,
    }
    contract.require_exact_json(
        audit, expected_audit, "match audit exact authorization replay"
    )
    expected_pinned = [
        contract.identity(contract.PROTOCOL_PATH),
        suite["preregistration"],
        suite["finalFreeze"],
        contract.identity(suite_path),
        contract.identity(offline_path),
        report["selection"],
        report["robustness"],
        report["accessClaim"],
        selected["network"],
        selected["manifest"],
        suite["dotnetHost"],
        suite["dotnetRuntimeManifest"],
        _runtime_identity(protocol, "dotnetRuntimeTool"),
        suite["matchCoreSource"],
        suite["engine"],
        suite["omegaMatchAssembly"],
        suite["omegaMatchAppHost"],
        *[suite_entries[gate]["identity"] for gate in GATES],
        *[contract.identity(config_paths[gate]) for gate in GATES],
        contract.identity(audit_path),
    ]
    by_path: dict[Path, dict[str, Any]] = {}
    for item in expected_pinned:
        item_path = Path(str(item["path"]))
        if not item_path.is_absolute():
            item_path = contract.REPO / item_path
        by_path[contract.resolve(item_path)] = item
    exact_pinned = sorted(
        by_path.values(), key=lambda item: str(item["path"]).casefold()
    )
    if not contract.exact_json_equal(core_seal.get("pinnedFiles"), exact_pinned):
        raise ValueError("core match-seal pinned-file inventory changed")
    expected_stage_paths = {
        gate: str(
            contract.namespace(
                protocol,
                {"development": "development", "equal-node": "equalNode", "equal-time": "equalTime"}[gate],
            )
        )
        for gate in GATES
    }
    contract.require_exact_json(
        value.get("stageOutputsAbsentAtAuthorization"),
        expected_stage_paths,
        "authorization stage namespace claims",
    )
    return value


def _verify_runtime_authorization(
    path: Path, *, protocol: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Verify launch authority without reopening held-out/offline derivation chains."""

    protocol = contract.validate_protocol() if protocol is None else dict(protocol)
    _install_core_profile(protocol)
    path = contract.resolve(path)
    if path != contract.namespace(protocol, "authorization"):
        raise ValueError("runtime authorization is outside its frozen namespace")
    value = contract.strict_load(path, "Generation-5 runtime match authorization")
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "protocol",
        "suiteSeal",
        "offlineReport",
        "offlineMatchAuthorization",
        "selectedNetwork",
        "selectedManifest",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeBundle",
        "matchCoreSource",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "omegaMatchBundle",
        "coreSeal",
        "stageOrder",
        "stageOutputsAbsentAtAuthorization",
        "runnerUpFallback",
        "launchOnlyThrough",
    }
    if set(value) != expected_fields:
        raise ValueError("runtime match-authorization field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != AUTHORIZATION_KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("stageOrder") != list(GATES)
        or value.get("runnerUpFallback") is not False
        or value.get("launchOnlyThrough")
        != "tools/omega_nnue/king_state_matches_generation5.py"
    ):
        raise ValueError("runtime match-authorization envelope changed")
    contract.parse_utc(value.get("createdUtc"), "runtime authorization createdUtc")
    contract.require_exact_json(
        value.get("protocol"),
        contract.identity(contract.PROTOCOL_PATH),
        "runtime authorization protocol",
    )
    suite_path = contract.verify_identity(
        value.get("suiteSeal"), "runtime authorization suite seal"
    )
    suite = _verify_suite_seal(
        suite_path,
        protocol=protocol,
        deep=False,
        runtime_authority=True,
    )
    for key in (
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeBundle",
        "matchCoreSource",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "omegaMatchBundle",
    ):
        contract.require_exact_json(
            value.get(key), suite.get(key), f"runtime authorization {key}"
        )
    selected_network = contract.mapping(
        value.get("selectedNetwork"), "runtime selected network"
    )
    selected_manifest = contract.mapping(
        value.get("selectedManifest"), "runtime selected manifest"
    )
    contract.verify_identity(selected_network, "runtime selected network")
    contract.verify_identity(selected_manifest, "runtime selected manifest")

    offline = contract.mapping(value.get("offlineReport"), "sealed offline-report identity")
    if (
        set(offline) != {"path", "bytes", "sha256"}
        or type(offline.get("path")) is not str
        or not offline["path"]
        or type(offline.get("bytes")) is not int
        or offline["bytes"] <= 0
        or type(offline.get("sha256")) is not str
        or contract.HEX_256.fullmatch(offline["sha256"]) is None
    ):
        raise ValueError("sealed offline-report identity is malformed")
    contract.verify_identity(offline, "sealed offline report")
    offline_authorization = contract.mapping(
        value.get("offlineMatchAuthorization"),
        "sealed offline authorization capsule",
    )
    expected_offline_authorization = {
        "authorized": True,
        "selectedPrimaryNetwork": selected_network,
        "runnerUpFallback": False,
    }
    contract.require_exact_json(
        offline_authorization,
        expected_offline_authorization,
        "sealed offline authorization capsule",
    )

    core_path = contract.verify_identity(value.get("coreSeal"), "runtime core seal")
    if core_path != contract.namespace(protocol, "coreSeal"):
        raise ValueError("runtime core-seal path changed")
    core_seal = contract.strict_load(core_path, "Generation-5 runtime core seal")
    expected_core_fields = {
        "schemaVersion",
        "kind",
        "sealedUtc",
        "generationId",
        "protocolSha256",
        "candidateNetworkSha256",
        "engineExecutableSha256",
        "matchCoreSourceSha256",
        "omegaMatchAssemblySha256",
        "pinnedFiles",
        "omegaMatchBundle",
        "dotnetRuntimeBundle",
        "gates",
        "audit",
    }
    if set(core_seal) != expected_core_fields:
        raise ValueError("runtime core-seal field inventory changed")
    if (
        type(core_seal.get("schemaVersion")) is not int
        or core_seal.get("schemaVersion") != SCHEMA_VERSION
        or core_seal.get("kind") != "omega-nnue-king-state-v1-match-seal"
        or core_seal.get("generationId") != "omega-nnue-king-state-v5"
        or core_seal.get("protocolSha256")
        != contract.identity(contract.PROTOCOL_PATH)["sha256"]
        or core_seal.get("candidateNetworkSha256") != selected_network["sha256"]
        or core_seal.get("engineExecutableSha256") != suite["engine"]["sha256"]
        or core_seal.get("matchCoreSourceSha256")
        != suite["matchCoreSource"]["sha256"]
        or core_seal.get("omegaMatchAssemblySha256")
        != suite["omegaMatchAssembly"]["sha256"]
    ):
        raise ValueError("runtime core-seal binding changed")
    contract.parse_utc(core_seal.get("sealedUtc"), "runtime core-seal sealedUtc")
    contract.require_exact_json(
        core_seal.get("omegaMatchBundle"),
        suite["omegaMatchBundle"],
        "runtime core-seal OmegaMatch bundle",
    )
    contract.require_exact_json(
        core_seal.get("dotnetRuntimeBundle"),
        suite["dotnetRuntimeBundle"],
        "runtime core-seal .NET bundle",
    )

    pinned = core_seal.get("pinnedFiles")
    if type(pinned) is not list or not pinned:
        raise ValueError("runtime core seal lacks pinned files")
    pinned_by_path: dict[Path, dict[str, Any]] = {}
    for index, item in enumerate(pinned, 1):
        record = contract.mapping(item, f"runtime pinned file {index}")
        if (
            set(record) != {"path", "bytes", "sha256"}
            or type(record.get("path")) is not str
            or not record["path"]
            or type(record.get("bytes")) is not int
            or record["bytes"] < 0
            or type(record.get("sha256")) is not str
            or contract.HEX_256.fullmatch(record["sha256"]) is None
        ):
            raise ValueError(f"runtime pinned file {index} is malformed")
        item_path = Path(record["path"])
        if not item_path.is_absolute():
            item_path = contract.REPO / item_path
        resolved = contract.resolve(item_path)
        if resolved in pinned_by_path:
            raise ValueError("runtime core seal repeats a pinned path")
        pinned_by_path[resolved] = dict(record)

    suite_entries = contract.mapping(suite.get("suites"), "runtime suites")
    config_paths = _config_paths(protocol)
    expected_gates: dict[str, dict[str, Any]] = {}
    for gate in GATES:
        suite_identity = contract.mapping(
            suite_entries[gate].get("identity"), f"{gate} runtime suite"
        )
        expected_config = _expected_match_config(
            protocol,
            gate,
            suite_identity,
            suite["engine"],
            selected_network,
            suite["omegaMatchAssembly"],
            suite["omegaMatchBundle"],
        )
        config_path = config_paths[gate]
        contract.require_exact_json(
            contract.strict_load(config_path, f"{gate} runtime exact config"),
            expected_config,
            f"{gate} runtime deterministic config",
        )
        expected_gates[gate] = {
            "suite": suite_identity,
            "config": contract.identity(config_path),
            "runId": expected_config["runId"],
            "outputDirectory": expected_config["outputDirectory"],
        }
    contract.require_exact_json(
        core_seal.get("gates"), expected_gates, "runtime core-seal gates"
    )
    required_pins = [
        contract.identity(contract.PROTOCOL_PATH),
        contract.identity(suite_path),
        offline,
        selected_network,
        selected_manifest,
        suite["matchCoreSource"],
        suite["engine"],
        suite["omegaMatchAssembly"],
        *[suite_entries[gate]["identity"] for gate in GATES],
        *[contract.identity(config_paths[gate]) for gate in GATES],
    ]
    for record in required_pins:
        item_path = Path(str(record["path"]))
        if not item_path.is_absolute():
            item_path = contract.REPO / item_path
        actual = pinned_by_path.get(contract.resolve(item_path))
        if not contract.exact_json_equal(actual, record):
            raise ValueError("runtime core seal lacks an exact required pin")
    audit_path = contract.verify_identity(core_seal.get("audit"), "runtime match audit")
    if audit_path != contract.namespace(protocol, "sealed") / "match-audit.json":
        raise ValueError("runtime match-audit path changed")
    expected_stage_paths = {
        gate: str(
            contract.namespace(
                protocol,
                {
                    "development": "development",
                    "equal-node": "equalNode",
                    "equal-time": "equalTime",
                }[gate],
            )
        )
        for gate in GATES
    }
    contract.require_exact_json(
        value.get("stageOutputsAbsentAtAuthorization"),
        expected_stage_paths,
        "runtime authorization stage namespaces",
    )
    return value


def _current_corpus_self_test_fixture(
    root: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str]]:
    placements = {
        "opening": (
            "krrrrrrrrr/pppppppppp/10/10/10/10/10/10/"
            "PPPPPPPPPP/RRRRRRRRRK[-/-/-/-]"
        ),
        "middlegame": (
            "krrrrrrrrr/pppppppppp/10/10/10/10/10/10/10/"
            "RRRRRRRRRK[-/-/-/-]"
        ),
        "late": (
            "krrrrrrrrr/10/10/10/10/10/10/10/10/"
            "RRRRRRRRRK[-/-/-/-]"
        ),
        "endgame": "krrr6/10/10/10/10/10/10/10/10/6RRRK[-/-/-/-]",
    }
    source_only = (
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
        "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
    )
    source_seed = 17
    paths = {
        "roots": root / "roots.jsonl",
        "rootsManifest": root / "roots.jsonl.manifest.json",
        "children": root / "children.jsonl",
        "childrenManifest": root / "children.jsonl.manifest.json",
        "sourceRootPool": root / "rules-only-pool.jsonl",
        "sourceRootPoolManifest": root / "rules-only-pool.jsonl.manifest.json",
        "sourceRootPoolSeal": root / "rules-only-pool.jsonl.complete.seal.json",
        "rootSamplerAssembly": root / "root-sampler/OmegaRootSampler.dll",
        "rootSamplerChessLibAssembly": root / "root-sampler/ChessLib.dll",
        "decisionSamplerAssembly": root / "decision-sampler/OmegaDecisionSampler.dll",
        "chessLibAssembly": root / "decision-sampler/ChessLib.dll",
    }
    paths["rootSamplerAssembly"].parent.mkdir(parents=True)
    paths["decisionSamplerAssembly"].parent.mkdir(parents=True)
    paths["rootSamplerAssembly"].write_bytes(b"root-sampler")
    paths["decisionSamplerAssembly"].write_bytes(b"decision-sampler")
    paths["chessLibAssembly"].write_bytes(b"chesslib")
    paths["rootSamplerChessLibAssembly"].write_bytes(b"chesslib")

    def jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
        path.write_text(
            "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in rows
            ),
            encoding="utf-8",
            newline="",
        )

    roots: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []
    pool_rows: list[dict[str, Any]] = []
    root_ofens: dict[str, str] = {}
    ordinal = 0
    for phase in PHASES:
      for side in ("w", "b"):
       for role in ("primary", "reserve"):
        ordinal += 1
        placement = placements[phase]
        if role == "reserve":
            board, pockets = placement.split("[", 1)
            ranks = board.split("/")
            ranks[-1] = ranks[-1][:-2] + ranks[-1][-1] + ranks[-1][-2]
            placement = "/".join(ranks) + "[" + pockets
        ofen = f"{placement} {side} - - 0 1"
        child_side = "b" if side == "w" else "w"
        child_ofen = f"{placement} {child_side} - - 1 1"
        pair_id = f"random-pair-{ordinal:06d}"
        root_id = f"root-{ordinal}"
        group_id = f"group-{ordinal}"
        source_game = f"source-run:game-{ordinal}:a1"
        selection_rank = f"{ordinal:064x}"
        root_row = {
            "schemaVersion": 1,
            "kind": "omega-hce-on-policy-root",
            "rootId": root_id,
            "groupId": group_id,
            "sourceOpeningId": f"opening-{ordinal}",
            "sourcePairId": f"source-pair-{ordinal}",
            "sourceProvenanceTag": f"rules-only-pair:{pair_id}",
            "sourceGameId": source_game,
            "sourceRunId": "source-run",
            "sourceAttempt": 1,
            "sourcePly": 1,
            "sourceLine": ordinal,
            "sourceEngineId": "hce-a",
            "sourceEngineSha256": "a" * 64,
            "phase": phase,
            "sideToMove": side,
            "ofen": ofen,
            "rootPvMove": "a0a1",
            "selectionRank": selection_rank,
            "candidateRole": role,
        }
        roots.append(root_row)
        root_ofens[phase] = ofen
        for move_ordinal, move in enumerate(("a0a1", "b0b1")):
          child_id = hashlib.sha256(
              (
                  "omega-decision-child-v1\0"
                  f"{root_id}\0{group_id}\0{move}\0{child_ofen}"
              ).encode("utf-8")
          ).hexdigest()
          children.append(
           {
                "schemaVersion": 1,
                "kind": "omega-legal-child",
                "rootId": root_id,
                "groupId": group_id,
                "sourceGameId": source_game,
                "phase": phase,
                "rootPvMove": "a0a1",
                "candidateRole": role,
                "selectionRank": selection_rank,
                "parentOfen": ofen,
                "parentSideToMove": side,
                "childId": child_id,
                "move": move,
                "moveOrdinal": move_ordinal,
                "childOfen": child_ofen,
                "childSideToMove": child_side,
                "isPromotion": False,
           }
          )
        pieces, _, _ = core.parse_ofen(ofen)
        pool_rows.append(
            {
                "schemaVersion": 1,
                "kind": "omega-rules-only-random-root",
                "generatorSeed": str(source_seed),
                "trajectorySeed": str(1000 + ordinal),
                "trajectoryPairId": pair_id,
                "trajectoryId": f"{pair_id}-ab",
                "flavor": "ab",
                "ply": {"opening": 10, "middlegame": 20, "late": 40, "endgame": 60}[phase],
                "phase": phase,
                "sideToMove": side,
                "ofen": ofen,
                "pieceCount": len(pieces),
                "whitePieces": sum(side == 0 for _, side, _ in pieces),
                "blackPieces": sum(side != 0 for _, side, _ in pieces),
                "champions": sum(piece == 6 for piece, _, _ in pieces),
                "wizards": sum(piece == 7 for piece, _, _ in pieces),
                "halfmoveClock": 0,
                "selectionRank": f"{ordinal + 20:064x}",
            }
        )
    pieces, _, _ = core.parse_ofen(source_only)
    pool_rows.append(
        {
            "schemaVersion": 1,
            "kind": "omega-rules-only-random-root",
            "generatorSeed": str(source_seed),
            "trajectorySeed": "2000",
            "trajectoryPairId": "random-pair-000017",
            "trajectoryId": "random-pair-000017-ba",
            "flavor": "ba",
            "ply": 12,
            "phase": "opening",
            "sideToMove": "w",
            "ofen": source_only,
            "pieceCount": len(pieces),
            "whitePieces": sum(side == 0 for _, side, _ in pieces),
            "blackPieces": sum(side != 0 for _, side, _ in pieces),
            "champions": sum(piece == 6 for piece, _, _ in pieces),
            "wizards": sum(piece == 7 for piece, _, _ in pieces),
            "halfmoveClock": 0,
            "selectionRank": "f" * 64,
        }
    )
    jsonl(paths["roots"], roots)
    jsonl(paths["children"], children)
    jsonl(paths["sourceRootPool"], pool_rows)
    pins = {
        name: contract.identity(path)
        for name, path in paths.items()
        if name not in {
            "rootsManifest",
            "childrenManifest",
            "sourceRootPoolManifest",
            "sourceRootPoolSeal",
        }
    }
    contract.atomic_json(
        paths["rootsManifest"],
        {
            "schemaVersion": 1,
            "kind": "omega-decision-root-manifest",
            "createdUtc": contract.utc_now(),
            "profileId": PROFILE_ID,
            "freshnessMarker": f"g5-source-{source_seed}",
            "policy": {
                "sourceSeed": source_seed,
                "seed": 23,
                "rootsPerPhase": 2,
                "reservePerPhase": 2,
                "maximumRootsPerSourceGroup": 1,
                "primaryPerPhaseAndSide": 1,
                "reservePerPhaseAndSide": 1,
                "selection": "target-blind SHA-256 rank",
                "source": "latest complete HCE-only OmegaMatch attempt",
                "sourceGrouping": (
                    "one root maximum per pinned opening Source provenance tag; "
                    "the complete AB/BA pair stays indivisible"
                ),
                "equivalentAbBaPolicy": (
                    "same-group exact inputs with the same move are "
                    "deterministically collapsed; conflicts abort"
                ),
                "requiredHceOptions": ROOT_HCE_OPTIONS,
                "requiredEngineSha256": "a" * 64,
            },
            "coverage": {
                "records": len(roots),
                "phaseCounts": {phase: 4 for phase in PHASES},
                "phaseSideCounts": {
                    f"{phase}/{side}": 2
                    for phase in PHASES for side in ("w", "b")
                },
                "primaryPhaseSideCounts": {
                    f"{phase}/{side}": 1
                    for phase in PHASES for side in ("w", "b")
                },
                "primary": 8,
                "reserve": 8,
                "uniqueRootIds": len(roots),
                "sourceGroups": len(roots),
                "oneRootPerSourceGroup": True,
                "collapsedEquivalentAbBaRecords": 0,
            },
            "sources": [],
            "producer": {},
            "finalStageSeal": True,
            "output": pins["roots"],
        },
        exclusive=True,
    )
    contract.atomic_json(
        paths["childrenManifest"],
        {
            "schemaVersion": 1,
            "kind": "omega-decision-sampler-manifest",
            "createdUtc": contract.utc_now(),
            "policy": CHILD_POLICY,
            "coverage": {
                "roots": len(roots),
                "uniqueRootIds": len(roots),
                "children": len(children),
                "uniqueChildIds": len(children),
                "zeroChildRoots": 0,
                "minimumChildrenPerRoot": 2,
                "maximumChildrenPerRoot": 2,
                "phaseCounts": {phase: 4 for phase in PHASES},
                "sideToMoveCounts": {"w": 8, "b": 8},
            },
            "input": pins["roots"],
            "output": pins["children"],
            "runtime": {
                "framework": ".NET synthetic",
                "samplerAssembly": pins["decisionSamplerAssembly"],
                "chessLibAssembly": pins["chessLibAssembly"],
            },
            "finalStageSeal": False,
        },
        exclusive=True,
    )
    contract.atomic_json(
        paths["sourceRootPoolManifest"],
        {
            "schemaVersion": 1,
            "kind": "omega-rules-only-random-root-manifest",
            "createdUtc": contract.utc_now(),
            "policy": {
                "deterministicPrng": "SplitMix64",
                "seed": str(source_seed),
                "trajectoryPairs": 10240,
                "independentTrajectoriesPerPair": 2,
                "workers": 4,
                "maxPlies": 220,
                "positionsPerPhaseAndSide": 2,
                "captureSelectionPercent": 72,
                "terminalRootsEmitted": 0,
                "maximumHalfmoveClock": 89,
                "minimumPieces": 7,
                "minimumPiecesPerSide": 2,
                "phasePlyWindows": SOURCE_POOL_PHASE_WINDOWS,
            },
            "coverage": {
                "records": len(pool_rows),
                "phaseCounts": {
                    phase: sum(row["phase"] == phase for row in pool_rows)
                    for phase in PHASES
                },
                "sideToMoveCounts": {
                    side: sum(row["sideToMove"] == side for row in pool_rows)
                    for side in ("w", "b")
                },
                "terminalTrajectories": 0,
                "maxPlyReached": 60,
                "promotionSelections": {piece: 0 for piece in "qrbncw"},
                "enPassantClassification": (
                    "An en-passant move lands on an empty target and remains "
                    "in the ordinary move pool; legality still comes from ChessLib."
                ),
            },
            "runtime": {
                "framework": ".NET synthetic",
                "samplerAssembly": pins["rootSamplerAssembly"],
                "chessLibAssembly": pins["rootSamplerChessLibAssembly"],
            },
            "output": pins["sourceRootPool"],
            "finalStageSeal": False,
        },
        exclusive=True,
    )
    pins.update(
        {
            "rootsManifest": contract.identity(paths["rootsManifest"]),
            "childrenManifest": contract.identity(paths["childrenManifest"]),
            "sourceRootPoolManifest": contract.identity(
                paths["sourceRootPoolManifest"]
            ),
        }
    )
    contract.atomic_json(
        paths["sourceRootPoolSeal"],
        {
            "schemaVersion": 1,
            "kind": "omega-rules-only-random-root-completion-seal",
            "createdUtc": contract.utc_now(),
            "output": pins["sourceRootPool"],
            "manifest": pins["sourceRootPoolManifest"],
            "producer": {
                "samplerAssembly": pins["rootSamplerAssembly"],
                "chessLibAssembly": pins["rootSamplerChessLibAssembly"],
                "framework": ".NET synthetic",
            },
            "finalStageSeal": True,
        },
        exclusive=True,
    )
    pins["sourceRootPoolSeal"] = contract.identity(paths["sourceRootPoolSeal"])
    completion = Path(str(paths["children"]) + ".complete.seal.json")
    contract.atomic_json(
        completion,
        {
            "schemaVersion": 1,
            "kind": "omega-decision-sampler-completion-seal",
            "createdUtc": contract.utc_now(),
            "input": pins["roots"],
            "output": pins["children"],
            "manifest": pins["childrenManifest"],
            "producer": {
                "samplerAssembly": pins["decisionSamplerAssembly"],
                "chessLibAssembly": pins["chessLibAssembly"],
                "framework": ".NET synthetic",
            },
            "finalStageSeal": True,
        },
        exclusive=True,
    )
    profile = {
        "_selfTestTeacherEngineSha256": "a" * 64,
        "freshDecisionCorpus": {
            "source": {
                "requiredRunSeed": source_seed,
                "rootSelectionUsesSeed": 23,
                "requiredDataProfile": "omega-decision-v2",
            },
            "rootQuota": {
                "completeRootsPerPhase": 2,
                "reserveRootsPerPhase": 2,
                "maximumCandidateRoots": 16,
            },
        }
    }
    return profile, pins, {
        "root": root_ofens["opening"],
        "child": children[0]["childOfen"],
        "sourceOnly": source_only,
    }


def _current_corpus_self_test_fixture_g5(
    root: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str]]:
    """Small but structurally complete raw-to-filtered G5 fixture."""

    import omega_decision_teacher_generation5 as decision_teacher

    placements = {
        "opening": (
            "krrrrrrrrr/pppppppppp/10/10/10/10/10/10/"
            "PPPPPPPPPP/RRRRRRRRRK[-/-/-/-]"
        ),
        "middlegame": (
            "krrrrrrrrr/pppppppppp/10/10/10/10/10/10/10/"
            "RRRRRRRRRK[-/-/-/-]"
        ),
        "late": (
            "krrrrrrrrr/10/10/10/10/10/10/10/10/"
            "RRRRRRRRRK[-/-/-/-]"
        ),
        "endgame": "krrr6/10/10/10/10/10/10/10/10/6RRRK[-/-/-/-]",
    }
    source_only = (
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
        "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
    )
    source_seed = 17
    root_seed = 2026072302
    paths = {
        "rawRoots": root / "raw-roots.jsonl",
        "rawRootsManifest": root / "raw-roots.jsonl.manifest.json",
        "rawChildren": root / "raw-children.jsonl",
        "rawChildrenManifest": root / "raw-children.jsonl.manifest.json",
        "rawSamplerCompletionSeal": root / "raw-children.jsonl.complete.seal.json",
        "rootFeasibility": root / "root-feasibility.jsonl",
        "rootFeasibilityManifest": root / "root-feasibility.jsonl.manifest.json",
        "rootFeasibilitySeal": root / "root-feasibility.seal.json",
        "roots": root / "roots.jsonl",
        "rootsManifest": root / "roots.jsonl.manifest.json",
        "children": root / "children.jsonl",
        "childrenManifest": root / "children.jsonl.manifest.json",
        "samplerCompletionSeal": root / "children.jsonl.complete.seal.json",
        "sourceRootPool": root / "rules-only-pool.jsonl",
        "sourceRootPoolManifest": root / "rules-only-pool.jsonl.manifest.json",
        "sourceRootPoolSeal": root / "rules-only-pool.jsonl.complete.seal.json",
        "rootSamplerAssembly": root / "root-sampler/OmegaRootSampler.dll",
        "rootSamplerChessLibAssembly": root / "root-sampler/ChessLib.dll",
        "decisionSamplerAssembly": root / "decision-sampler/OmegaDecisionSampler.dll",
        "chessLibAssembly": root / "decision-sampler/ChessLib.dll",
        "decisionTeacherSource": root / "omega_decision_teacher_generation5.py",
        "sourceOpeningBuilderSource": root / "king_state_generation5_source.py",
        "deepHceV2Source": root / "deep_hce_v2.py",
        "networkFormatPythonSource": root / "omega_nnue.py",
        "selectScreenSource": root / "select_screen.py",
        "priorProjectionSource": (
            contract.REPO
            / "tools/omega_nnue/king_state_generation4_prior_projection.py"
        ),
        "trainerSource": root / "king_state_train_generation5.py",
        "preregistrationValidatorSource": root / "validate_king_state_v5_preregistration.py",
        "pythonRuntimeToolSource": root / "king_state_generation5_runtime.py",
        "dotnetRuntimeToolSource": root / "king_state_dotnet_runtime_generation5.py",
        "pythonRuntimeManifest": root / "omega-nnue-king-state-v5-python-runtime.json",
        "generation4AbortVerifierSource": (
            contract.REPO / GENERATION4_ABORT_TOOL_PATH
        ),
        "sourceMatchCompletionSeal": root / "source-match.complete.seal.json",
        "generation4Closure": root / "g4-closure.json",
    }
    paths["rootSamplerAssembly"].parent.mkdir(parents=True)
    paths["decisionSamplerAssembly"].parent.mkdir(parents=True)
    for name, payload in (
        ("rootSamplerAssembly", b"root-sampler"),
        ("decisionSamplerAssembly", b"decision-sampler"),
        ("chessLibAssembly", b"chesslib"),
        ("rootSamplerChessLibAssembly", b"chesslib"),
        ("decisionTeacherSource", b"synthetic-g5-teacher"),
        ("sourceOpeningBuilderSource", b"synthetic-g5-source"),
        ("deepHceV2Source", b"synthetic-deep-hce-v2"),
        ("networkFormatPythonSource", b"synthetic-omega-nnue"),
        ("selectScreenSource", b"synthetic-select-screen"),
        ("trainerSource", b"synthetic-g5-trainer"),
        ("preregistrationValidatorSource", b"synthetic-g5-validator"),
        ("pythonRuntimeToolSource", b"synthetic-g5-runtime"),
        ("dotnetRuntimeToolSource", b"synthetic-g5-dotnet-runtime"),
        ("pythonRuntimeManifest", b"synthetic-g5-python-manifest"),
        ("sourceMatchCompletionSeal", b"synthetic-source-match-seal"),
        ("generation4Closure", b"synthetic-g4-closure"),
    ):
        paths[name].write_bytes(payload)

    def jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
        path.write_text(
            "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in rows
            ),
            encoding="utf-8",
            newline="",
        )

    raw_roots: list[dict[str, Any]] = []
    raw_children: list[dict[str, Any]] = []
    pool_rows: list[dict[str, Any]] = []
    ordinal = 0
    for phase in PHASES:
        for side in ("w", "b"):
            for role in ("primary", "reserve"):
                ordinal += 1
                placement = placements[phase]
                if role == "reserve":
                    board, pockets = placement.split("[", 1)
                    ranks = board.split("/")
                    ranks[-1] = ranks[-1][:-2] + ranks[-1][-1] + ranks[-1][-2]
                    placement = "/".join(ranks) + "[" + pockets
                ofen = f"{placement} {side} - - 0 1"
                child_side = "b" if side == "w" else "w"
                pair_id = f"random-pair-{ordinal:06d}"
                root_id = hashlib.sha256(
                    f"synthetic-g5-root-{ordinal}".encode("utf-8")
                ).hexdigest()
                group_id = "trajectory:" + hashlib.sha256(
                    f"synthetic-g5-group-{ordinal}".encode("utf-8")
                ).hexdigest()
                source_game = f"source-run:game-{ordinal}:a1"
                rank = _expected_g5_root_rank(
                    root_id, root_seed, phase, side, group_id
                )
                root_row = {
                    "schemaVersion": 1,
                    "kind": "omega-hce-on-policy-root",
                    "rootId": root_id,
                    "groupId": group_id,
                    "sourceOpeningId": f"opening-{ordinal}",
                    "sourcePairId": f"source-pair-{ordinal}",
                    "sourceProvenanceTag": f"rules-only-pair:{pair_id}",
                    "sourceGameId": source_game,
                    "sourceRunId": "source-run",
                    "sourceAttempt": 1,
                    "sourcePly": 1,
                    "sourceLine": ordinal,
                    "sourceEngineId": "hce-a",
                    "sourceEngineSha256": "a" * 64,
                    "phase": phase,
                    "sideToMove": side,
                    "ofen": ofen,
                    "rootPvMove": "a0a1",
                    "selectionRank": rank,
                    "candidateRole": role,
                }
                raw_roots.append(root_row)
                move_count = 5 if role == "primary" else 4
                for move_ordinal in range(move_count):
                    move = f"{chr(ord('a') + move_ordinal)}0{chr(ord('a') + move_ordinal)}1"
                    child_ofen = f"{placement} {child_side} - - {move_ordinal + 1} 1"
                    child_id = hashlib.sha256(
                        (
                            "omega-decision-child-v1\0"
                            f"{root_id}\0{group_id}\0{move}\0{child_ofen}"
                        ).encode("utf-8")
                    ).hexdigest()
                    raw_children.append(
                        {
                            "schemaVersion": 1,
                            "kind": "omega-legal-child",
                            "rootId": root_id,
                            "groupId": group_id,
                            "sourceGameId": source_game,
                            "phase": phase,
                            "rootPvMove": "a0a1",
                            "candidateRole": role,
                            "selectionRank": rank,
                            "parentOfen": ofen,
                            "parentSideToMove": side,
                            "childId": child_id,
                            "move": move,
                            "moveOrdinal": move_ordinal,
                            "childOfen": child_ofen,
                            "childSideToMove": child_side,
                            "isPromotion": False,
                        }
                    )
                pieces, _, _ = core.parse_ofen(ofen)
                pool_rows.append(
                    {
                        "schemaVersion": 1,
                        "kind": "omega-rules-only-random-root",
                        "generatorSeed": str(source_seed),
                        "trajectorySeed": str(1000 + ordinal),
                        "trajectoryPairId": pair_id,
                        "trajectoryId": f"{pair_id}-ab",
                        "flavor": "ab",
                        "ply": {
                            "opening": 10,
                            "middlegame": 20,
                            "late": 40,
                            "endgame": 60,
                        }[phase],
                        "phase": phase,
                        "sideToMove": side,
                        "ofen": ofen,
                        "pieceCount": len(pieces),
                        "whitePieces": sum(piece_side == 0 for _, piece_side, _ in pieces),
                        "blackPieces": sum(piece_side != 0 for _, piece_side, _ in pieces),
                        "champions": sum(piece == 6 for piece, _, _ in pieces),
                        "wizards": sum(piece == 7 for piece, _, _ in pieces),
                        "halfmoveClock": 0,
                        "selectionRank": f"{ordinal + 20:064x}",
                    }
                )
    pieces, _, _ = core.parse_ofen(source_only)
    pool_rows.append(
        {
            "schemaVersion": 1,
            "kind": "omega-rules-only-random-root",
            "generatorSeed": str(source_seed),
            "trajectorySeed": "2000",
            "trajectoryPairId": "random-pair-000017",
            "trajectoryId": "random-pair-000017-ba",
            "flavor": "ba",
            "ply": 12,
            "phase": "opening",
            "sideToMove": "w",
            "ofen": source_only,
            "pieceCount": len(pieces),
            "whitePieces": sum(piece_side == 0 for _, piece_side, _ in pieces),
            "blackPieces": sum(piece_side != 0 for _, piece_side, _ in pieces),
            "champions": sum(piece == 6 for piece, _, _ in pieces),
            "wizards": sum(piece == 7 for piece, _, _ in pieces),
            "halfmoveClock": 0,
            "selectionRank": "f" * 64,
        }
    )
    feasibility, children_by_root = decision_teacher._audit_raw_graph(
        raw_roots, raw_children, raw_per_phase_side=2
    )
    filtered_roots, filtered_children, feasibility = (
        decision_teacher._retain_feasible_roots(
            raw_roots, children_by_root, feasibility, retain_per_phase_side=1
        )
    )
    for name, rows in (
        ("rawRoots", raw_roots),
        ("rawChildren", raw_children),
        ("rootFeasibility", feasibility),
        ("roots", filtered_roots),
        ("children", filtered_children),
        ("sourceRootPool", pool_rows),
    ):
        jsonl(paths[name], rows)
    pins = {
        name: contract.identity(path)
        for name, path in paths.items()
        if "Manifest" not in name and "Seal" not in name
    }
    synthetic_producer = _expected_g5_producer(
        {}, pins, contract.identity(Path(sys.executable)), synthetic=True
    )
    raw_policy = {
        "sourceSeed": source_seed,
        "seed": root_seed,
        "rootsPerPhase": 2,
        "reservePerPhase": 2,
        "maximumRootsPerSourceGroup": 1,
        "primaryPerPhaseAndSide": 1,
        "reservePerPhaseAndSide": 1,
        "selection": "target-blind SHA-256 rank",
        "source": "latest complete HCE-only OmegaMatch attempt",
        "sourceGrouping": (
            "one root maximum per pinned opening Source provenance tag; "
            "the complete AB/BA pair stays indivisible"
        ),
        "equivalentAbBaPolicy": (
            "same-group exact inputs with the same move are deterministically "
            "collapsed; conflicts abort"
        ),
        "requiredHceOptions": ROOT_HCE_OPTIONS,
        "requiredEngineSha256": "a" * 64,
    }

    def root_manifest_payload(filtered: bool) -> dict[str, Any]:
        rows = filtered_roots if filtered else raw_roots
        policy = dict(raw_policy)
        per_bucket = 1 if filtered else 2
        if filtered:
            policy.update(
                {
                    "rootsPerPhase": 2,
                    "reservePerPhase": 0,
                    "primaryPerPhaseAndSide": 1,
                    "reservePerPhaseAndSide": 0,
                    "selection": "target-blind frozen root rank after structural feasibility",
                    "rawRootsPerPhaseAndSide": 2,
                    "minimumDistinctLegalChildren": 5,
                    "sourcePvOccurrencesRequired": 1,
                }
            )
        return {
            "schemaVersion": 1,
            "kind": "omega-decision-root-manifest",
            "createdUtc": contract.utc_now(),
            "profileId": PROFILE_ID,
            "freshnessMarker": f"g5-source-{source_seed}",
            "policy": policy,
            "coverage": {
                "records": len(rows),
                "phaseCounts": {phase: per_bucket * 2 for phase in PHASES},
                "phaseSideCounts": {
                    f"{phase}/{side}": per_bucket
                    for phase in PHASES
                    for side in ("w", "b")
                },
                "primaryPhaseSideCounts": {
                    f"{phase}/{side}": 1
                    for phase in PHASES
                    for side in ("w", "b")
                },
                "primary": 8,
                "reserve": 0 if filtered else 8,
                "uniqueRootIds": len(rows),
                "sourceGroups": len(rows),
                "oneRootPerSourceGroup": True,
                "collapsedEquivalentAbBaRecords": 0,
            },
            "sources": [],
            "producer": synthetic_producer,
            "finalStageSeal": True,
            "output": pins["roots" if filtered else "rawRoots"],
        }

    contract.atomic_json(paths["rawRootsManifest"], root_manifest_payload(False), exclusive=True)
    contract.atomic_json(paths["rootsManifest"], root_manifest_payload(True), exclusive=True)
    pins["rawRootsManifest"] = contract.identity(paths["rawRootsManifest"])
    pins["rootsManifest"] = contract.identity(paths["rootsManifest"])

    def child_coverage(roots: Sequence[Mapping[str, Any]], children: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        counts = Counter(str(child["rootId"]) for child in children)
        values = [counts[str(item["rootId"])] for item in roots]
        return {
            "roots": len(roots),
            "children": len(children),
            "zeroChildRoots": sum(value == 0 for value in values),
            "minimumChildrenPerRoot": min(values),
            "maximumChildrenPerRoot": max(values),
            "phaseCounts": dict(Counter(str(item["phase"]) for item in roots)),
            "sideToMoveCounts": dict(Counter(str(item["sideToMove"]) for item in roots)),
            "uniqueRootIds": len(roots),
            "uniqueChildIds": len(children),
        }

    def write_child_bundle(filtered: bool) -> None:
        child_name = "children" if filtered else "rawChildren"
        root_name = "roots" if filtered else "rawRoots"
        manifest_name = "childrenManifest" if filtered else "rawChildrenManifest"
        seal_name = "samplerCompletionSeal" if filtered else "rawSamplerCompletionSeal"
        rows = filtered_children if filtered else raw_children
        root_rows = filtered_roots if filtered else raw_roots
        policy = dict(CHILD_POLICY)
        if filtered:
            policy["derivation"] = "exact child subset selected by root-feasibility-v2"
        contract.atomic_json(
            paths[manifest_name],
            {
                "schemaVersion": 1,
                "kind": "omega-decision-sampler-manifest",
                "createdUtc": contract.utc_now(),
                "policy": policy,
                "coverage": child_coverage(root_rows, rows),
                "input": pins[root_name],
                "output": pins[child_name],
                "runtime": {
                    "framework": ".NET synthetic",
                    "samplerAssembly": pins["decisionSamplerAssembly"],
                    "chessLibAssembly": pins["chessLibAssembly"],
                },
                "finalStageSeal": False,
            },
            exclusive=True,
        )
        pins[manifest_name] = contract.identity(paths[manifest_name])
        contract.atomic_json(
            paths[seal_name],
            {
                "schemaVersion": 1,
                "kind": "omega-decision-sampler-completion-seal",
                "createdUtc": contract.utc_now(),
                "input": pins[root_name],
                "output": pins[child_name],
                "manifest": pins[manifest_name],
                "producer": {
                    "samplerAssembly": pins["decisionSamplerAssembly"],
                    "chessLibAssembly": pins["chessLibAssembly"],
                    "framework": ".NET synthetic",
                },
                "finalStageSeal": True,
            },
            exclusive=True,
        )
        pins[seal_name] = contract.identity(paths[seal_name])

    write_child_bundle(False)
    write_child_bundle(True)
    feasible_counts = Counter(
        (row["phase"], row["sideToMove"], row["structurallyFeasible"])
        for row in feasibility
    )
    rejection_counts = Counter(
        reason for row in feasibility for reason in row["rejectionReasons"]
    )
    components = {row["rawLeakageComponentId"] for row in feasibility}
    collision_counts = {
        "sourceDataInputCollisions": 0,
        "sourceGameCollisions": 0,
        "sourceRunCollisions": 0,
        "exactPositionCollisions": 0,
        "conservativeOrbitCollisions": 0,
    }
    raw_identity_names = (
        "rawRoots",
        "rawRootsManifest",
        "rawChildren",
        "rawChildrenManifest",
        "rawSamplerCompletionSeal",
    )
    contract.atomic_json(
        paths["rootFeasibilityManifest"],
        {
            "schemaVersion": 1,
            "kind": "omega-decision-root-feasibility-manifest",
            "createdUtc": contract.utc_now(),
            "profileId": PROFILE_ID,
            "policy": {
                "sourceSeed": source_seed,
                "rootSelectionSeed": root_seed,
                "rankDomain": "omega-g5-feasible-root-v1",
                "rawRootsPerPhaseAndSide": 2,
                "retainedRootsPerPhaseAndSide": 1,
                "minimumDistinctLegalChildren": 5,
                "sourcePvOccurrencesRequired": 1,
                "targetInformationRead": False,
                "crossBucketBorrowing": False,
                "rawGraphComponentsBuiltBeforeFiltering": True,
            },
            "coverage": {
                "rawRoots": len(raw_roots),
                "rawChildren": len(raw_children),
                "structurallyFeasibleRoots": 8,
                "retainedRoots": len(filtered_roots),
                "retainedChildren": len(filtered_children),
                "rawComponents": len(components),
                "phaseSide": {
                    f"{phase}/{side}": {
                        "raw": 2,
                        "feasible": feasible_counts[(phase, side, True)],
                        "retained": 1,
                    }
                    for phase in PHASES
                    for side in ("w", "b")
                },
                "rejectionReasons": dict(rejection_counts),
            },
            "inputs": {name: pins[name] for name in raw_identity_names},
            "priorReuse": {"forbiddenCatalogs": [], "collisionCounts": collision_counts},
            "producer": synthetic_producer,
            "finalStageSeal": False,
            "output": pins["rootFeasibility"],
        },
        exclusive=True,
    )
    pins["rootFeasibilityManifest"] = contract.identity(paths["rootFeasibilityManifest"])
    output_names = (
        "rootFeasibility",
        "rootFeasibilityManifest",
        "roots",
        "rootsManifest",
        "children",
        "childrenManifest",
        "samplerCompletionSeal",
    )
    contract.atomic_json(
        paths["rootFeasibilitySeal"],
        {
            "schemaVersion": 1,
            "kind": "omega-decision-root-feasibility-seal",
            "profileId": PROFILE_ID,
            "status": "target-opaque-raw-graph-authenticated-and-feasibility-frozen",
            "createdUtc": contract.utc_now(),
            "declaration": {
                "teacherSearchesPresentAtFreeze": False,
                "targetOrScoreFieldsDecoded": 0,
                "targetOrScoreFieldsEmitted": 0,
                "rawGraphAuthenticatedBeforeFiltering": True,
                "rawComponentsInheritedByFilteredRows": True,
            },
            "forbiddenCatalogs": [],
            "priorReuseCollisionCounts": collision_counts,
            "identities": {
                **{name: pins[name] for name in raw_identity_names},
                **{name: pins[name] for name in output_names},
            },
            "producer": synthetic_producer,
            "finalStageSeal": True,
        },
        exclusive=True,
    )
    pins["rootFeasibilitySeal"] = contract.identity(paths["rootFeasibilitySeal"])
    contract.atomic_json(
        paths["sourceRootPoolManifest"],
        {
            "schemaVersion": 1,
            "kind": "omega-rules-only-random-root-manifest",
            "createdUtc": contract.utc_now(),
            "policy": {
                "deterministicPrng": "SplitMix64",
                "seed": str(source_seed),
                "trajectoryPairs": 10240,
                "independentTrajectoriesPerPair": 2,
                "workers": 4,
                "maxPlies": 220,
                "positionsPerPhaseAndSide": 2,
                "captureSelectionPercent": 72,
                "terminalRootsEmitted": 0,
                "maximumHalfmoveClock": 89,
                "minimumPieces": 7,
                "minimumPiecesPerSide": 2,
                "phasePlyWindows": SOURCE_POOL_PHASE_WINDOWS,
            },
            "coverage": {
                "records": len(pool_rows),
                "phaseCounts": dict(Counter(row["phase"] for row in pool_rows)),
                "sideToMoveCounts": dict(Counter(row["sideToMove"] for row in pool_rows)),
                "terminalTrajectories": 0,
                "maxPlyReached": 60,
                "promotionSelections": {piece: 0 for piece in "qrbncw"},
                "enPassantClassification": (
                    "An en-passant move lands on an empty target and remains "
                    "in the ordinary move pool; legality still comes from ChessLib."
                ),
            },
            "runtime": {
                "framework": ".NET synthetic",
                "samplerAssembly": pins["rootSamplerAssembly"],
                "chessLibAssembly": pins["rootSamplerChessLibAssembly"],
            },
            "output": pins["sourceRootPool"],
            "finalStageSeal": False,
        },
        exclusive=True,
    )
    pins["sourceRootPoolManifest"] = contract.identity(paths["sourceRootPoolManifest"])
    contract.atomic_json(
        paths["sourceRootPoolSeal"],
        {
            "schemaVersion": 1,
            "kind": "omega-rules-only-random-root-completion-seal",
            "createdUtc": contract.utc_now(),
            "output": pins["sourceRootPool"],
            "manifest": pins["sourceRootPoolManifest"],
            "producer": {
                "samplerAssembly": pins["rootSamplerAssembly"],
                "chessLibAssembly": pins["rootSamplerChessLibAssembly"],
                "framework": ".NET synthetic",
            },
            "finalStageSeal": True,
        },
        exclusive=True,
    )
    pins["sourceRootPoolSeal"] = contract.identity(paths["sourceRootPoolSeal"])
    profile = {
        "_selfTestTeacherEngineSha256": "a" * 64,
        "_selfTestRawRootsPerPhaseSide": 2,
        "_selfTestFeasibleRootsPerPhaseSide": 1,
        "_selfTestForbiddenCatalogs": [],
        "freshDecisionCorpus": {
            "source": {
                "requiredRunSeed": source_seed,
                "rootSelectionUsesSeed": root_seed,
                "requiredDataProfile": "omega-decision-v2",
            },
            "rootQuota": {
                "completeRootsPerPhase": 2,
                "reserveRootsPerPhase": 2,
                "maximumCandidateRoots": 16,
            },
        },
    }
    return profile, pins, {
        "root": filtered_roots[0]["ofen"],
        "child": filtered_children[0]["childOfen"],
        "rawOnly": raw_roots[1]["ofen"],
        "rawChildOnly": next(
            row["childOfen"]
            for row in raw_children
            if row["rootId"] == raw_roots[1]["rootId"]
        ),
        "sourceOnly": source_only,
    }


_current_corpus_self_test_fixture = _current_corpus_self_test_fixture_g5


def _self_test() -> None:
    global _LAZY_TRAINER, _LAZY_TRAINER_BINDINGS

    protocol = contract.validate_protocol()
    _install_core_profile(protocol)
    policy_fixture = {
        "freshDecisionCorpus": {
            "source": {
                "sourceDataDisjointness": copy.deepcopy(
                    EXPECTED_SOURCE_DATA_DISJOINTNESS
                )
            }
        }
    }
    _validate_source_data_disjointness(policy_fixture)
    changed_policy = copy.deepcopy(policy_fixture)
    changed_policy["freshDecisionCorpus"]["source"]["sourceDataDisjointness"][
        "reusableInfrastructureIdentityFields"
    ][0] = "source"
    try:
        _validate_source_data_disjointness(changed_policy)
    except ValueError:
        pass
    else:
        raise AssertionError("source-data disjointness domain substitution was accepted")
    synthetic_identity = {
        "path": "synthetic.bin",
        "bytes": 1,
        "sha256": "0" * 64,
    }
    _identity_shape(synthetic_identity, "synthetic readiness identity")
    for replacement in (1.0, True):
        changed_identity = dict(synthetic_identity)
        changed_identity["bytes"] = replacement
        try:
            _identity_shape(
                changed_identity,
                "synthetic readiness identity numeric substitution",
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "readiness identity accepted integer substitution "
                f"{replacement!r}"
            )
    producer_path = contract.REPO / GENERATION4_ABORT_TOOL_PATH
    producer_actual = contract.identity(producer_path)
    producer_identity = {
        "path": GENERATION4_ABORT_TOOL_PATH,
        "bytes": producer_actual["bytes"],
        "sha256": producer_actual["sha256"],
    }
    _validate_generation4_closure_producer(producer_identity)
    producer_attacks = (
        {**producer_identity, "path": str(producer_path.resolve())},
        {**producer_identity, "path": "tools/omega_nnue/not-the-abort-tool.py"},
        {**producer_identity, "sha256": "0" * 64},
    )
    for attack in producer_attacks:
        try:
            _validate_generation4_closure_producer(attack)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "Generation-4 closure producer accepted a path/digest substitution"
            )
    synthetic_capsule = {
        "authorized": True,
        "selectedPrimaryNetwork": synthetic_identity,
        "runnerUpFallback": False,
    }
    changed_capsule = copy.deepcopy(synthetic_capsule)
    changed_capsule["selectedPrimaryNetwork"]["bytes"] = 1.0
    if contract.exact_json_equal(changed_capsule, synthetic_capsule):
        raise AssertionError("readiness capsule accepted a recursive numeric substitution")
    template = contract.REPO / protocol["authority"]["preregistrationTemplate"]
    _validate_profile(template, protocol, template=True)
    if core.GATE_SPECS["equal-time"]["roots"] != 512:
        raise AssertionError("frozen equal-time root budget changed")
    if protocol["stages"]["equal-node"]["resumePairBudget"] != 4:
        raise AssertionError("frozen resume budget changed")
    if protocol["pairedSchedule"]["roles"][1] != {
        "gameSuffix": "ba",
        "white": "engineB",
        "black": "engineA",
    }:
        raise AssertionError("paired-colour swap changed")
    synthetic_command, _, command_dotnet, command_sampler = _sampler_command(
        protocol, "development", Path(tempfile.gettempdir()) / "g5-command-test.jsonl"
    )
    if (
        synthetic_command[:2]
        != [str(command_dotnet["path"]), str(command_sampler["path"])]
        or synthetic_command[synthetic_command.index("--workers") + 1] != "4"
        or Path(synthetic_command[0])
        != contract.resolve(dotnet_runtime.RUNTIME_ROOT / "dotnet.exe")
    ):
        raise AssertionError("sampler command is not bound to the frozen runtime")
    with tempfile.TemporaryDirectory(prefix="omega-g5-readiness-") as directory:
        import omega_decision_teacher_generation5 as decision_teacher
        import king_state_train_generation5 as training

        def require_preinitialization_rejection(
            module: Any,
            *,
            identity_name: str,
            label: str,
            binding_name: str,
            replacement: Any,
            cache_names: tuple[str, str],
        ) -> None:
            original = getattr(module, binding_name)
            for cache_name in cache_names:
                globals()[cache_name] = None
            try:
                setattr(module, binding_name, replacement)
                try:
                    _verify_lazy_frozen_module(
                        module,
                        identity_name=identity_name,
                        label=label,
                        binding_names=(binding_name,),
                        allow_template=True,
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError(
                        f"preinitialization substitution of {label}.{binding_name} "
                        "was accepted"
                    )
                if any(globals()[cache_name] is not None for cache_name in cache_names):
                    raise AssertionError(
                        f"failed {label} verification populated its lazy cache"
                    )
            finally:
                setattr(module, binding_name, original)

        lazy_cases = (
            (
                training,
                "trainerSource",
                "Generation-5 trainer module",
                "_verify_offline_report",
                ("_LAZY_TRAINER", "_LAZY_TRAINER_BINDINGS"),
            ),
        )
        for module, identity_name, label, binding_name, cache_names in lazy_cases:
            wrong_module = lambda *args, **kwargs: None
            spoofed_module = lambda *args, **kwargs: None
            spoofed_module.__module__ = module.__name__
            for replacement in (object(), wrong_module, spoofed_module):
                require_preinitialization_rejection(
                    module,
                    identity_name=identity_name,
                    label=label,
                    binding_name=binding_name,
                    replacement=replacement,
                    cache_names=cache_names,
                )
            _verify_lazy_frozen_module(
                module,
                identity_name=identity_name,
                label=label,
                binding_names=(binding_name,),
                allow_template=True,
            )
            original = getattr(module, binding_name)
            try:
                setattr(module, binding_name, lambda *args, **kwargs: None)
                try:
                    _verify_lazy_frozen_module(
                        module,
                        identity_name=identity_name,
                        label=label,
                        binding_names=(binding_name,),
                        allow_template=True,
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError(
                        f"post-cache substitution of {label}.{binding_name} was accepted"
                    )
            finally:
                setattr(module, binding_name, original)

        root = Path(directory)
        runtime_profile_path = root / "runtime-profile.json"
        runtime_freeze_path = root / "runtime-freeze.json"
        runtime_profile = contract.strict_load(template, "runtime profile fixture")
        runtime_profile["kind"] = "omega-nnue-king-state-v5-preregistration"
        runtime_profile["status"] = (
            "target-blind-generation-5-design-frozen-before-teacher-labels"
        )
        runtime_profile["createdUtc"] = contract.utc_now()
        runtime_identities = runtime_profile["finalFreezeIdentities"]
        for key, source in {
            "matchProtocolTool": (
                contract.REPO
                / "tools/omega_nnue/king_state_match_protocol_generation5.py"
            ),
            "matchCoreSource": (
                contract.REPO / "tools/omega_nnue/king_state_matches.py"
            ),
            "dotnetRuntimeToolSource": (
                contract.REPO
                / "tools/omega_nnue/king_state_dotnet_runtime_generation5.py"
            ),
            "preregistrationValidatorSource": (
                contract.REPO
                / "tools/omega_nnue/validate_king_state_v5_preregistration.py"
            ),
        }.items():
            runtime_identities[key] = contract.identity(source)
        contract.atomic_json(
            runtime_profile_path, runtime_profile, exclusive=True
        )
        identities_digest = hashlib.sha256(
            json.dumps(
                runtime_identities,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        runtime_freeze = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "omega-nnue-king-state-v5-final-freeze-seal",
            "profileId": PROFILE_ID,
            "status": (
                "target-blind-generation-5-design-frozen-before-teacher-labels"
            ),
            "createdUtc": runtime_profile["createdUtc"],
            "preregistration": contract.identity(runtime_profile_path),
            "validator": contract.identity(
                contract.REPO
                / "tools/omega_nnue/validate_king_state_v5_preregistration.py"
            ),
            "finalFreezeIdentities": runtime_identities,
            "finalFreezeIdentitiesSha256": identities_digest,
            "declaration": {
                "teacherSearchesPresentAtFreeze": False,
                "generation5TeacherTargetsDecoded": 0,
                "generation5ValidationTargetsDecoded": 0,
                "generation5HeldOutTargetsDecoded": 0,
            },
            "finalStageSeal": True,
        }
        contract.atomic_json(runtime_freeze_path, runtime_freeze, exclusive=True)
        original_final_profile = contract.FINAL_PROFILE
        original_final_freeze = contract.FINAL_FREEZE
        try:
            contract.FINAL_PROFILE = runtime_profile_path
            contract.FINAL_FREEZE = runtime_freeze_path
            verified_profile, verified_freeze = _validate_runtime_authority_capsule(
                runtime_profile_path, runtime_freeze_path, protocol
            )
            if (
                verified_profile["profileId"] != PROFILE_ID
                or verified_freeze["finalStageSeal"] is not True
            ):
                raise AssertionError("runtime authority capsule fixture changed")
        finally:
            contract.FINAL_PROFILE = original_final_profile
            contract.FINAL_FREEZE = original_final_freeze
        copied_template = root / "copied-template.json"
        contract.atomic_json(
            copied_template,
            contract.strict_load(template, "canonical template"),
            exclusive=True,
        )
        try:
            _validate_profile(copied_template, protocol, template=True)
        except ValueError:
            pass
        else:
            raise AssertionError("noncanonical preregistration path was accepted")
        payload, manifest = decision_teacher._build_synthetic_forbidden_catalog(
            root,
            [
                {
                    "ofen": (
                        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
                        "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
                    ),
                    "sourceGameId": "synthetic-game",
                    "sourceRunId": "synthetic-run",
                }
            ],
        )
        original_forbidden_loader = decision_teacher._load_forbidden_catalogs

        def forged_forbidden_loader(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("parent-memory forged loader executed")

        forged_forbidden_loader.__module__ = decision_teacher.__name__
        forged_forbidden_loader.__code__ = forged_forbidden_loader.__code__.replace(
            co_filename=str(contract.REPO / "tools/omega_nnue/omega_decision_teacher_generation5.py")
        )
        try:
            decision_teacher._load_forbidden_catalogs = forged_forbidden_loader
            manifests, positions, signatures, scan = _validate_forbidden_inputs(
                [manifest],
                [payload],
                _allow_template_module_for_self_test=True,
            )
        finally:
            decision_teacher._load_forbidden_catalogs = original_forbidden_loader
        if manifests != [contract.identity(manifest)] or positions != [
            contract.identity(payload)
        ] or not signatures or scan["positions"] != 1:
            raise AssertionError("target-opaque manifest binding changed")
        poisoned = root / "poisoned.json"
        poisoned_value = contract.strict_load(manifest, "synthetic forbidden manifest")
        poisoned_value["targetOrScoreFieldsDecoded"] = 1
        contract.atomic_json(
            poisoned,
            poisoned_value,
            exclusive=True,
        )
        try:
            _validate_forbidden_inputs(
                [poisoned],
                [payload],
                _allow_template_module_for_self_test=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("non-target-opaque manifest was accepted")
        unbound = root / "unbound.jsonl"
        unbound.write_text('{"not":"the canonical catalog"}\n', encoding="utf-8")
        try:
            _validate_forbidden_inputs(
                [manifest],
                [unbound],
                _allow_template_module_for_self_test=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("unbound forbidden-position payload was accepted")
        corpus_root = root / "current-corpus"
        corpus_root.mkdir()
        corpus_profile, corpus_pins, corpus_ofens = (
            _current_corpus_self_test_fixture(corpus_root)
        )
        command_pins = dict(corpus_pins)
        for name in (
            "sourceEvents",
            "sourceOpeningSuite",
            "sourceMatchConfig",
            "sourceMatchCompletionSeal",
            "sourceMatchHarnessAssembly",
            "teacherEngineExecutable",
        ):
            source_path = corpus_root / f"{name}.bin"
            source_path.write_bytes(name.encode("utf-8"))
            command_pins[name] = contract.identity(source_path)
        command_pins["decisionTeacherSource"] = contract.identity(
            contract.REPO / "tools/omega_nnue/omega_decision_teacher_generation5.py"
        )
        command_python = contract.identity(Path(sys.executable))
        command_output = corpus_root / "command-roots.jsonl"
        prepare_command, command_chesslib = _prepare_roots_replay_command(
            corpus_profile, command_pins, command_python, command_output
        )
        if (
            prepare_command[0] != command_python["path"]
            or prepare_command[1:3] != ["-I", "-B"]
            or prepare_command[3:5]
            != [
                "-X",
                f"pycache_prefix={contract.resolve(corpus_root / 'isolated-python-cache')}",
            ]
            or prepare_command[prepare_command.index("--seed") + 1]
            != "2026072302"
            or prepare_command[
                prepare_command.index("--source-match-completion-seal") + 1
            ]
            != command_pins["sourceMatchCompletionSeal"]["path"]
            or prepare_command[
                prepare_command.index("--required-engine-sha256") + 1
            ]
            != command_pins["teacherEngineExecutable"]["sha256"]
            or command_chesslib["path"]
            == corpus_pins["chessLibAssembly"]["path"]
            or not _same_content_identity(
                command_chesslib, corpus_pins["chessLibAssembly"]
            )
        ):
            raise AssertionError("prepare-roots replay command escaped frozen inputs")

        # A timestamp/size-valid malicious legacy cache must be observable in
        # the control and ignored by the exact command used for clean workers.
        import importlib.util
        import py_compile

        pyc_root = corpus_root / "pyc-isolation"
        pyc_root.mkdir()
        victim = pyc_root / "victim.py"
        malicious = pyc_root / "malicious.py"
        driver = pyc_root / "driver.py"
        victim.write_text("VALUE='source'\n", encoding="utf-8")
        malicious.write_text("VALUE='cache!'\n", encoding="utf-8")
        victim_stat = victim.stat()
        os.utime(
            malicious,
            ns=(victim_stat.st_atime_ns, victim_stat.st_mtime_ns),
        )
        cache_path = Path(importlib.util.cache_from_source(str(victim)))
        cache_path.parent.mkdir()
        py_compile.compile(
            str(malicious),
            cfile=str(cache_path),
            dfile=str(victim),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
        )
        driver.write_text(
            "import pathlib,sys,victim\n"
            "pathlib.Path(sys.argv[1]).write_text(victim.VALUE,encoding='utf-8')\n",
            encoding="utf-8",
        )
        driver_path = contract.resolve(driver)
        bootstrap = (
            "import runpy,sys;"
            f"sys.path.insert(0,{str(driver_path.parent)!r});"
            f"runpy.run_path({str(driver_path)!r},run_name='__main__')"
        )
        control_result = pyc_root / "control.txt"
        subprocess.run(
            [
                str(command_python["path"]),
                "-I",
                "-B",
                "-c",
                bootstrap,
                str(control_result),
            ],
            cwd=pyc_root,
            env=_sanitized_python_environment(),
            check=True,
        )
        isolated_result = pyc_root / "isolated.txt"
        isolated_command = _python_script_command(
            command_python,
            contract.identity(driver),
            [str(isolated_result)],
            pycache_prefix=pyc_root / "fresh-cache",
        )
        subprocess.run(
            isolated_command,
            cwd=pyc_root,
            env=_sanitized_python_environment(),
            check=True,
        )
        if (
            control_result.read_text(encoding="utf-8") != "cache!"
            or isolated_result.read_text(encoding="utf-8") != "source"
        ):
            raise AssertionError("fresh Python bytecode namespace was bypassed")
        selection_forbidden, current_private, current_audit = (
            _current_corpus_exclusion_from_pins(
                corpus_profile, set(), corpus_pins
            )
        )
        _validate_current_corpus_audit(current_audit)
        if (
            not selection_forbidden
            or current_audit["records"]["sourcePoolStates"] != 17
            or current_audit["records"]["rawRoots"] != 16
            or current_audit["records"]["rawChildren"] != 72
            or current_audit["records"]["retainedRoots"] != 8
            or current_audit["records"]["retainedChildren"] != 40
            or current_audit["historicalComparison"]["intersectionSignatures"]
            != 0
        ):
            raise AssertionError("synthetic current-corpus exclusion audit changed")

        def synthetic_match_root(
            ofen: str,
            *,
            seed: int = 99,
            pair_id: str = "random-pair-000100",
            trajectory_id: str | None = None,
        ) -> core.Root:
            phase, side, identity, orbit, orbit_signatures = core._position_meta(ofen)
            actual_trajectory = trajectory_id or f"{pair_id}-ab"
            return core.Root(
                gate="development",
                source_path=corpus_root / "match.jsonl",
                source_sha256="b" * 64,
                line=1,
                generator_seed=seed,
                trajectory_pair_id=pair_id,
                trajectory_id=actual_trajectory,
                flavor="ab",
                ply=10,
                phase=phase,
                side=side,
                ofen=ofen,
                identity=identity,
                orbit=orbit,
                orbit_signatures=orbit_signatures,
                rank="c" * 64,
            )

        collision_cases = (
            ("root", corpus_ofens["root"]),
            ("child", corpus_ofens["child"]),
            ("raw-only root", corpus_ofens["rawOnly"]),
            ("raw-only child", corpus_ofens["rawChildOnly"]),
            ("source-state", corpus_ofens["sourceOnly"]),
        )
        for label, ofen in collision_cases:
            candidate = synthetic_match_root(ofen)
            candidate_keys = {candidate.identity, *candidate.orbit_signatures}
            if not candidate_keys.intersection(current_private["signatures"]):
                raise AssertionError(f"synthetic {label} collision fixture changed")
            try:
                _gate_current_corpus_evidence(
                    [candidate], [candidate], current_private
                )
            except ValueError:
                pass
            else:
                raise AssertionError(f"selected current {label} collision was accepted")
        source_group_collision = synthetic_match_root(
            corpus_ofens["child"],
            seed=17,
            pair_id="random-pair-000001",
            trajectory_id="random-pair-000001-ab",
        )
        try:
            _gate_current_corpus_evidence(
                [source_group_collision], [], current_private
            )
        except ValueError:
            pass
        else:
            raise AssertionError("current source-group/trajectory reuse was accepted")

        target_like = corpus_root / "target-like.jsonl"
        target_like.write_text(
            '{"schemaVersion":1,"score":THIS_VALUE_MUST_NOT_BE_DECODED}\n',
            encoding="utf-8",
            newline="",
        )
        try:
            list(
                _iter_target_blind_jsonl(
                    target_like,
                    frozenset({"schemaVersion", "score"}),
                    "synthetic target-like row",
                )
            )
        except ValueError:
            pass
        else:
            raise AssertionError("target-like current-corpus field was decoded")
        recursive_lexical_cases = (
            (
                '{"schemaVersion":1,"selectionRank":{"target":{"score":123}}}',
                True,
            ),
            (
                '{"schemaVersion":1,"selectionRank":{"targets":{"scores":123}}}',
                True,
            ),
            ('{"schemaVersion":1,"selectionRank":{"TARGETS":123}}', True),
            ('{"schemaVersion":1,"selectionRank":{"SCORES":123}}', True),
            ('{"heldOutTARGETSDecoded":BROKEN}', True),
            ('{"generation5HeldOutTargetsDecoded":BROKEN}', True),
            ('{"policy":{"target":THIS_MUST_NOT_BE_DECODED}}', True),
            ('{"sources":[BROKEN]}', False),
        )
        for text, reject_sensitive in recursive_lexical_cases:
            try:
                _target_blind_top_level_fields(
                    text, reject_sensitive=reject_sensitive
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "recursive target quarantine/JSON grammar accepted poison"
                )
        nested_created = corpus_root / "nested-created.json"
        nested_created.write_text(
            '{"createdUtc":{"target":{"score":1}}}', encoding="utf-8"
        )
        try:
            _load_pinned_json(
                nested_created,
                contract.identity(nested_created),
                "synthetic nested createdUtc",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("nested createdUtc target was decoded")
        try:
            _require_sha256_string(
                int("1" * 64), "synthetic integer engine SHA-256"
            )
        except ValueError:
            pass
        else:
            raise AssertionError("integer sourceEngineSha256 was accepted")

        impossible_replay = corpus_root / "impossible-replay-children.jsonl"
        impossible_replay.write_bytes(
            Path(corpus_pins["children"]["path"])
            .read_bytes()
            .replace(b'"move":"a0a1"', b'"move":"zzzz"', 1)
        )
        try:
            _assert_exact_file_reproduction(
                Path(corpus_pins["children"]["path"]),
                corpus_pins["children"],
                impossible_replay,
                "synthetic impossible-move replay",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("impossible-move replay bytes were accepted")

        root_semantics, _ = _root_manifest_semantics(
            Path(corpus_pins["rootsManifest"]["path"]),
            corpus_pins["rootsManifest"],
            "synthetic root semantics",
        )
        normalized_manifest = contract.strict_load(
            Path(corpus_pins["rootsManifest"]["path"]),
            "synthetic normalized manifest source",
        )
        normalized_manifest["createdUtc"] = "2026-07-23T00:00:00Z"
        normalized_manifest["output"] = {
            **normalized_manifest["output"],
            "path": str(corpus_root / "different-output.jsonl"),
        }
        normalized_path = corpus_root / "normalized-root-manifest.json"
        contract.atomic_json(normalized_path, normalized_manifest, exclusive=True)
        normalized_semantics, _ = _root_manifest_semantics(
            normalized_path,
            contract.identity(normalized_path),
            "synthetic normalized root semantics",
        )
        if not contract.exact_json_equal(root_semantics, normalized_semantics):
            raise AssertionError("root replay normalization changed stable semantics")
        normalized_manifest["policy"]["seed"] += 1
        tampered_manifest = corpus_root / "tampered-root-manifest.json"
        contract.atomic_json(tampered_manifest, normalized_manifest, exclusive=True)
        tampered_semantics, _ = _root_manifest_semantics(
            tampered_manifest,
            contract.identity(tampered_manifest),
            "synthetic tampered root semantics",
        )
        if contract.exact_json_equal(root_semantics, tampered_semantics):
            raise AssertionError("root semantic replay ignored policy tampering")
        malformed_identity = dict(corpus_pins["roots"])
        malformed_identity["bytes"] = float(malformed_identity["bytes"])
        for label, path, identity in (
            ("bad identity type", Path(corpus_pins["roots"]["path"]), malformed_identity),
            ("bad identity path", Path(corpus_pins["children"]["path"]), corpus_pins["roots"]),
            (
                "bad identity hash",
                Path(corpus_pins["roots"]["path"]),
                {**corpus_pins["roots"], "sha256": "0" * 64},
            ),
        ):
            try:
                _identity_snapshot(path, identity, f"synthetic {label}")
            except ValueError:
                pass
            else:
                raise AssertionError(f"synthetic {label} was accepted")
        historical_collision = next(iter(current_private["signatures"]))
        try:
            _current_corpus_exclusion_from_pins(
                corpus_profile, {historical_collision}, corpus_pins
            )
        except ValueError:
            pass
        else:
            raise AssertionError("historical/raw-G5 collision was accepted")

        original_identity = contract.identity
        roots_path = contract.resolve(Path(corpus_pins["roots"]["path"]))
        root_identity_calls = 0

        def mutating_identity(path: Path) -> dict[str, Any]:
            nonlocal root_identity_calls
            resolved = contract.resolve(path)
            if resolved == roots_path:
                root_identity_calls += 1
                if root_identity_calls == 2:
                    roots_path.write_bytes(roots_path.read_bytes() + b"\n")
            return original_identity(path)

        try:
            contract.identity = mutating_identity
            try:
                _current_corpus_exclusion_from_pins(
                    corpus_profile, set(), corpus_pins
                )
            except ValueError:
                pass
            else:
                raise AssertionError("current-corpus TOCTOU mutation was accepted")
        finally:
            contract.identity = original_identity
    poison = {
        "CORECLR_ENABLE_PROFILING": "1",
        "CORECLR_PROFILER": "{00000000-0000-0000-0000-000000000000}",
        "COR_ENABLE_PROFILING": "1",
        "COMPlus_ReadyToRun": "0",
        "COREHOST_TRACE": "1",
        "DOTNET_ROOT": "poisoned",
    }
    prior_environment = {key: os.environ.get(key) for key in poison}
    try:
        os.environ.update(poison)
        managed = _sanitized_environment()
    finally:
        for key, prior in prior_environment.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
    if (
        managed.get("DOTNET_MULTILEVEL_LOOKUP") != "0"
        or managed.get("DOTNET_ROOT")
        != str(contract.resolve(dotnet_runtime.RUNTIME_ROOT))
        or any(key in managed for key in poison if key != "DOTNET_ROOT")
    ):
        raise AssertionError("managed environment is not isolated")
    dotnet = _runtime_identity(protocol, "dotnetHost")
    listed = subprocess.run(
        [str(dotnet["path"]), "--list-runtimes"],
        cwd=dotnet_runtime.RUNTIME_ROOT,
        env=managed,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    runtime_lines = [line for line in listed.stdout.splitlines() if line.strip()]
    if (
        len(runtime_lines) != 1
        or not runtime_lines[0].startswith(
            f"Microsoft.NETCore.App {dotnet_runtime.RUNTIME_VERSION} ["
        )
        or str(contract.resolve(dotnet_runtime.RUNTIME_ROOT / "shared/Microsoft.NETCore.App"))
        not in runtime_lines[0]
    ):
        raise AssertionError("pinned dotnet resolved an unexpected runtime")
    for global_name in (
        "contract",
        "core",
        "dotnet_runtime",
        "preregistration_validator",
    ):
        original = globals()[global_name]
        try:
            globals()[global_name] = object()
            try:
                _verify_import_bindings(protocol)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"in-memory {global_name} module substitution was accepted"
                )
        finally:
            globals()[global_name] = original
    original_core_callable = core._verify_suite
    try:
        core._verify_suite = lambda gate, path: {}
        try:
            _verify_import_bindings(protocol)
        except ValueError:
            pass
        else:
            raise AssertionError("in-memory shared-core callable substitution was accepted")
    finally:
        core._verify_suite = original_core_callable
    original_binding_verifier = contract.verify_module_binding
    try:
        contract.verify_module_binding = lambda *args, **kwargs: None
        try:
            _verify_import_bindings(protocol)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "in-memory contract binding-verifier substitution was accepted"
            )
    finally:
        contract.verify_module_binding = original_binding_verifier
def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample")
    sample.add_argument("--protocol", type=Path, default=contract.PROTOCOL_PATH)
    sample.add_argument("--output-dir", type=Path)
    seal = commands.add_parser("seal-suites")
    seal.add_argument("--protocol", type=Path, default=contract.PROTOCOL_PATH)
    seal.add_argument("--profile", type=Path, default=contract.FINAL_PROFILE)
    seal.add_argument("--final-freeze", type=Path, default=contract.FINAL_FREEZE)
    seal.add_argument(
        "--forbidden-catalog-manifest", type=Path, action="append", required=True
    )
    seal.add_argument(
        "--forbidden-position-file", type=Path, action="append", required=True
    )
    verify_suites = commands.add_parser("verify-suites")
    verify_suites.add_argument("--protocol", type=Path, default=contract.PROTOCOL_PATH)
    verify_suites.add_argument(
        "--suite-seal", type=Path, default=None
    )
    authorize = commands.add_parser("authorize")
    authorize.add_argument("--protocol", type=Path, default=contract.PROTOCOL_PATH)
    authorize.add_argument("--suite-seal", type=Path, default=None)
    authorize.add_argument("--offline-report", type=Path, required=True)
    verify = commands.add_parser("verify-authorization")
    verify.add_argument("--protocol", type=Path, default=contract.PROTOCOL_PATH)
    verify.add_argument("--authorization", type=Path, default=None)
    worker = commands.add_parser("forbidden-catalog-worker", help=argparse.SUPPRESS)
    worker.add_argument("--teacher-source", type=Path, required=True)
    worker.add_argument("--teacher-bytes", type=int, required=True)
    worker.add_argument("--teacher-sha256", required=True)
    worker.add_argument("--manifest", type=Path, action="append", required=True)
    worker.add_argument("--output", type=Path, required=True)
    worker.add_argument(
        "--self-test-allow-noncanonical-inventory",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "sample":
        _sample(args)
    elif args.command == "seal-suites":
        value = _seal_suites(args)
        print(f"Generation-5 suite seal: {contract.namespace(contract.validate_protocol(args.protocol), 'suiteSeal')}")
        print(f"Root digest: {value['rootDigest']}")
    elif args.command == "verify-suites":
        protocol = contract.validate_protocol(args.protocol)
        path = args.suite_seal or contract.namespace(protocol, "suiteSeal")
        value = _verify_suite_seal(
            path, protocol=protocol, deep=True, reproduce_sources=True
        )
        print(f"Generation-5 suite seal verified: {value['rootDigest']}")
    elif args.command == "authorize":
        protocol = contract.validate_protocol(args.protocol)
        if args.suite_seal is None:
            args.suite_seal = contract.namespace(protocol, "suiteSeal")
        value = _authorize(args)
        print(f"Generation-5 match authorization: {contract.namespace(protocol, 'authorization')}")
        print(f"Selected network: {value['selectedNetwork']['sha256']}")
    elif args.command == "verify-authorization":
        protocol = contract.validate_protocol(args.protocol)
        path = args.authorization or contract.namespace(protocol, "authorization")
        value = _verify_authorization(path, protocol=protocol)
        print(f"Generation-5 match authorization verified: {value['selectedNetwork']['sha256']}")
    elif args.command == "forbidden-catalog-worker":
        _forbidden_catalog_worker(args)
    else:
        contract.self_test()
        _self_test()
        print("Generation-5 match-readiness self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
