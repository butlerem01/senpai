#!/usr/bin/env python3
"""Build and seal target-blind Generation-4 Omega match readiness.

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
from typing import Any, Mapping, Sequence

import king_state_match_protocol_generation4 as contract
import king_state_dotnet_runtime_generation4 as dotnet_runtime
import king_state_matches as core
import validate_king_state_v4_preregistration as preregistration_validator


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
_LAZY_DECISION_TEACHER: Any = None
_LAZY_DECISION_TEACHER_BINDINGS: dict[str, Any] | None = None
_LAZY_TRAINER: Any = None
_LAZY_TRAINER_BINDINGS: dict[str, Any] | None = None


SCHEMA_VERSION = 1
PROFILE_ID = contract.PROFILE_ID
SUITE_SEAL_KIND = "omega-nnue-king-state-v4-suite-seal"
AUTHORIZATION_KIND = "omega-nnue-king-state-v4-match-authorization"
AUDIT_KIND = "omega-nnue-king-state-v4-match-audit"
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
        / "tools/omega_nnue/king_state_match_protocol_generation4.py",
        "Generation-4 match protocol module",
        identity_record=None if identities is None else identities.get("matchProtocolTool"),
        callable_bindings=_CONTRACT_BINDINGS,
    )
    verify_binding(
        core,
        _IMPORTED_CORE,
        _IMPORTED_CONTRACT.REPO / "tools/omega_nnue/king_state_matches.py",
        "Generation-4 shared match core",
        identity_record=runtime.get("matchCoreSource"),
        callable_bindings=_CORE_BINDINGS,
    )
    verify_binding(
        dotnet_runtime,
        _IMPORTED_DOTNET_RUNTIME,
        _IMPORTED_CONTRACT.REPO
        / "tools/omega_nnue/king_state_dotnet_runtime_generation4.py",
        "Generation-4 .NET runtime module",
        identity_record=runtime.get("dotnetRuntimeTool"),
        callable_bindings=_DOTNET_BINDINGS,
    )
    verify_binding(
        preregistration_validator,
        _IMPORTED_PREREGISTRATION_VALIDATOR,
        _IMPORTED_CONTRACT.REPO
        / "tools/omega_nnue/validate_king_state_v4_preregistration.py",
        "Generation-4 preregistration validator",
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
    global _LAZY_DECISION_TEACHER, _LAZY_DECISION_TEACHER_BINDINGS
    global _LAZY_TRAINER, _LAZY_TRAINER_BINDINGS
    profile_path = _IMPORTED_CONTRACT.FINAL_PROFILE
    if not profile_path.is_file() and allow_template:
        profile_path = (
            _IMPORTED_CONTRACT.REPO
            / "validation/omega-nnue-king-state-v4-preregistration.template.json"
        )
    profile = _IMPORTED_CONTRACT.strict_load(
        profile_path, "Generation-4 preregistration for module binding"
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

    if identity_name == "decisionTeacherSource":
        if (_LAZY_DECISION_TEACHER is None) != (
            _LAZY_DECISION_TEACHER_BINDINGS is None
        ):
            raise ValueError("decision-teacher lazy binding cache is inconsistent")
        if _LAZY_DECISION_TEACHER is None:
            captured = capture_local_bindings()
            _CONTRACT_BINDINGS["verify_module_binding"](
                module,
                module,
                expected_path,
                label,
                identity_record=identity_record,
                callable_bindings=captured,
            )
            _LAZY_DECISION_TEACHER = module
            _LAZY_DECISION_TEACHER_BINDINGS = captured
        imported = _LAZY_DECISION_TEACHER
        bindings = _LAZY_DECISION_TEACHER_BINDINGS
    else:
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
        raise ValueError("Generation-4 preregistration path is not canonical")
    value = contract.strict_load(path, "Generation-4 preregistration")
    _verify_import_bindings(protocol, None if template else value)
    preregistration_validator.validate_profile(
        value,
        mode="template" if template else "frozen",
        verify_external=not template,
    )
    expected_kind = (
        "omega-nnue-king-state-v4-preregistration-template"
        if template
        else "omega-nnue-king-state-v4-preregistration"
    )
    expected_status = (
        "draft-not-executable-until-development-grid-and-implementation-identities-are-sealed"
        if template
        else "target-blind-generation-4-design-frozen-before-teacher-labels"
    )
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != expected_kind
        or value.get("profileId") != PROFILE_ID
        or value.get("status") != expected_status
    ):
        raise ValueError("Generation-4 preregistration envelope changed")
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
                / "tools/omega_nnue/king_state_matches_generation4.py"
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
    return value


def _validate_final_freeze(path: Path, profile_path: Path) -> dict[str, Any]:
    path = contract.resolve(path)
    profile_path = contract.resolve(profile_path)
    if path != contract.resolve(contract.FINAL_FREEZE):
        raise ValueError("Generation-4 final-freeze path is not canonical")
    if profile_path != contract.resolve(contract.FINAL_PROFILE):
        raise ValueError("Generation-4 final profile path is not canonical")
    value = contract.strict_load(path, "Generation-4 final-freeze seal")
    profile = contract.strict_load(profile_path, "Generation-4 final preregistration")
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
        or value.get("kind") != "omega-nnue-king-state-v4-final-freeze-seal"
        or value.get("profileId") != PROFILE_ID
        or value.get("status")
        != "target-blind-generation-4-design-frozen-before-teacher-labels"
        or value.get("finalStageSeal") is not True
        or not contract.exact_json_equal(
            value.get("preregistration"), contract.identity(profile_path)
        )
        or not contract.exact_json_equal(
            value.get("validator"),
            contract.identity(
                contract.REPO
                / "tools/omega_nnue/validate_king_state_v4_preregistration.py"
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
        "generation4TeacherTargetsDecoded": 0,
        "generation4ValidationTargetsDecoded": 0,
        "generation4HeldOutTargetsDecoded": 0,
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
        != "omega-nnue-king-state-v4-preregistration"
        or profile.get("profileId") != PROFILE_ID
        or profile.get("status")
        != "target-blind-generation-4-design-frozen-before-teacher-labels"
    ):
        raise ValueError("runtime preregistration envelope changed")
    contract.parse_utc(profile.get("createdUtc"), "runtime preregistration createdUtc")
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
        or freeze.get("kind") != "omega-nnue-king-state-v4-final-freeze-seal"
        or freeze.get("profileId") != PROFILE_ID
        or freeze.get("status")
        != "target-blind-generation-4-design-frozen-before-teacher-labels"
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
            / "tools/omega_nnue/validate_king_state_v4_preregistration.py"
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
            "generation4TeacherTargetsDecoded": 0,
            "generation4ValidationTargetsDecoded": 0,
            "generation4HeldOutTargetsDecoded": 0,
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


def _validate_forbidden_inputs(
    manifests: Sequence[Path],
    position_files: Sequence[Path],
    *,
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

    # This is the canonical target-blind catalog parser used by data sealing.
    # It recognizes exact/orbit keys even when a row deliberately omits OFEN,
    # rejects target/score-like fields, and verifies every catalog/source pin.
    import omega_decision_teacher as decision_teacher

    _verify_lazy_frozen_module(
        decision_teacher,
        identity_name="decisionTeacherSource",
        label="Generation-4 decision-teacher module",
        binding_names=("_load_forbidden_catalogs",),
        allow_template=_allow_template_module_for_self_test,
    )

    ordered_manifests = sorted(resolved_manifests, key=lambda item: str(item).casefold())
    forbidden, pins = decision_teacher._load_forbidden_catalogs(ordered_manifests)
    manifest_records = [dict(item["manifest"]) for item in pins]
    expected_catalogs = sorted(
        (dict(item["catalog"]) for item in pins),
        key=lambda item: str(item["path"]).casefold(),
    )
    positions = sorted(
        (contract.identity(item) for item in resolved_positions),
        key=lambda item: str(item["path"]).casefold(),
    )
    if not contract.exact_json_equal(positions, expected_catalogs):
        raise ValueError(
            "supplied forbidden position files are not exactly the canonical catalogs"
        )
    signatures = set(forbidden["exact"]) | set(forbidden["signatures"])
    audit = {
        "catalogs": len(pins),
        "positions": sum(int(item["positionCount"]) for item in pins),
        "exactPositionKeys": len(forbidden["exact"]),
        "orbitSignatures": len(signatures),
        "sourceGameIds": len(forbidden["sourceGameIds"]),
        "sourceRunIds": len(forbidden["sourceRunIds"]),
        "sourceInputSha256": len(forbidden["sourceInputSha256"]),
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
    with tempfile.TemporaryDirectory(prefix=f"omega-g4-{gate}-replay-") as directory:
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
    return {gate: sealed / f"king-state-v4-{gate}-suite.json" for gate in GATES}


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
        gate: sealed / f"king-state-v4-{gate}-match.json" for gate in GATES
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
    value["runId"] = f"king-state-v4-{gate}-{str(network['sha256'])[:12]}"
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
        source_audits[gate] = audit
        selected[gate], rejected[gate] = core._select_roots(
            roots, core.GATE_SPECS[gate], forbidden, used_fresh
        )

    suite_seal_path.parent.mkdir(parents=True, exist_ok=True)
    schedules: dict[str, list[dict[str, Any]]] = {}
    try:
        for gate in GATES:
            suite, schedule = core._suite(gate, selected[gate])
            suite["name"] = f"Omega NNUE king-state v4 {gate}"
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
    value = contract.strict_load(path, "Generation-4 suite seal")
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
            "sourceInputSha256",
        }
    ):
        raise ValueError("suite forbidden-audit fields changed")
    contract.exact_int(
        forbidden_audit.get("uniqueOrbitSignatures"),
        "suite forbidden unique-orbit count",
    )
    for key, item in forbidden_scan.items():
        contract.exact_int(item, f"suite forbidden scan {key}")
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
            expected_selected[gate], expected_rejections[gate] = core._select_roots(
                roots,
                core.GATE_SPECS[gate],
                forbidden,
                used_expected,
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
            expected_suite["name"] = f"Omega NNUE king-state v4 {gate}"
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
            if deep and forbidden.intersection(signatures):
                raise ValueError(f"{gate} suite intersects a forbidden orbit")
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
    import king_state_train_generation4 as training

    _verify_lazy_frozen_module(
        training,
        identity_name="trainerSource",
        label="Generation-4 trainer module",
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
    selection = contract.strict_load(selection_path, "Generation-4 validation selection")
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
        "generationId": "omega-nnue-king-state-v4",
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
        "launchOnlyThrough": "tools/omega_nnue/king_state_matches_generation4.py",
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
    value = contract.strict_load(path, "Generation-4 match authorization")
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
        != "tools/omega_nnue/king_state_matches_generation4.py"
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
    strict_core_seal = contract.strict_load(core_path, "Generation-4 core match seal")
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
        or core_seal.get("generationId") != "omega-nnue-king-state-v4"
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
    audit = contract.strict_load(audit_path, "Generation-4 match audit")
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
    value = contract.strict_load(path, "Generation-4 runtime match authorization")
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
        != "tools/omega_nnue/king_state_matches_generation4.py"
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
    core_seal = contract.strict_load(core_path, "Generation-4 runtime core seal")
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
        or core_seal.get("generationId") != "omega-nnue-king-state-v4"
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


def _self_test() -> None:
    global _LAZY_DECISION_TEACHER, _LAZY_DECISION_TEACHER_BINDINGS
    global _LAZY_TRAINER, _LAZY_TRAINER_BINDINGS

    protocol = contract.validate_protocol()
    _install_core_profile(protocol)
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
        protocol, "development", Path(tempfile.gettempdir()) / "g4-command-test.jsonl"
    )
    if (
        synthetic_command[:2]
        != [str(command_dotnet["path"]), str(command_sampler["path"])]
        or synthetic_command[synthetic_command.index("--workers") + 1] != "4"
        or Path(synthetic_command[0])
        != contract.resolve(dotnet_runtime.RUNTIME_ROOT / "dotnet.exe")
    ):
        raise AssertionError("sampler command is not bound to the frozen runtime")
    with tempfile.TemporaryDirectory(prefix="omega-g4-readiness-") as directory:
        import omega_decision_teacher as decision_teacher
        import king_state_train_generation4 as training

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
                decision_teacher,
                "decisionTeacherSource",
                "Generation-4 decision-teacher module",
                "_load_forbidden_catalogs",
                ("_LAZY_DECISION_TEACHER", "_LAZY_DECISION_TEACHER_BINDINGS"),
            ),
            (
                training,
                "trainerSource",
                "Generation-4 trainer module",
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
        runtime_profile["kind"] = "omega-nnue-king-state-v4-preregistration"
        runtime_profile["status"] = (
            "target-blind-generation-4-design-frozen-before-teacher-labels"
        )
        runtime_profile["createdUtc"] = contract.utc_now()
        runtime_identities = runtime_profile["finalFreezeIdentities"]
        for key, source in {
            "matchProtocolTool": (
                contract.REPO
                / "tools/omega_nnue/king_state_match_protocol_generation4.py"
            ),
            "matchCoreSource": (
                contract.REPO / "tools/omega_nnue/king_state_matches.py"
            ),
            "dotnetRuntimeToolSource": (
                contract.REPO
                / "tools/omega_nnue/king_state_dotnet_runtime_generation4.py"
            ),
            "preregistrationValidatorSource": (
                contract.REPO
                / "tools/omega_nnue/validate_king_state_v4_preregistration.py"
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
            "kind": "omega-nnue-king-state-v4-final-freeze-seal",
            "profileId": PROFILE_ID,
            "status": (
                "target-blind-generation-4-design-frozen-before-teacher-labels"
            ),
            "createdUtc": runtime_profile["createdUtc"],
            "preregistration": contract.identity(runtime_profile_path),
            "validator": contract.identity(
                contract.REPO
                / "tools/omega_nnue/validate_king_state_v4_preregistration.py"
            ),
            "finalFreezeIdentities": runtime_identities,
            "finalFreezeIdentitiesSha256": identities_digest,
            "declaration": {
                "teacherSearchesPresentAtFreeze": False,
                "generation4TeacherTargetsDecoded": 0,
                "generation4ValidationTargetsDecoded": 0,
                "generation4HeldOutTargetsDecoded": 0,
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
        manifests, positions, signatures, scan = _validate_forbidden_inputs(
            [manifest],
            [payload],
            _allow_template_module_for_self_test=True,
        )
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
    original_teacher_slot = sys.modules.get("omega_decision_teacher")
    try:
        sys.modules["omega_decision_teacher"] = object()
        try:
            _validate_forbidden_inputs(
                [manifest],
                [payload],
                _allow_template_module_for_self_test=True,
            )
        except (ImportError, AttributeError, ValueError):
            pass
        else:
            raise AssertionError(
                "in-memory decision-teacher module substitution was accepted"
            )
    finally:
        if original_teacher_slot is None:
            sys.modules.pop("omega_decision_teacher", None)
        else:
            sys.modules["omega_decision_teacher"] = original_teacher_slot


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
    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "sample":
        _sample(args)
    elif args.command == "seal-suites":
        value = _seal_suites(args)
        print(f"Generation-4 suite seal: {contract.namespace(contract.validate_protocol(args.protocol), 'suiteSeal')}")
        print(f"Root digest: {value['rootDigest']}")
    elif args.command == "verify-suites":
        protocol = contract.validate_protocol(args.protocol)
        path = args.suite_seal or contract.namespace(protocol, "suiteSeal")
        value = _verify_suite_seal(
            path, protocol=protocol, deep=True, reproduce_sources=True
        )
        print(f"Generation-4 suite seal verified: {value['rootDigest']}")
    elif args.command == "authorize":
        protocol = contract.validate_protocol(args.protocol)
        if args.suite_seal is None:
            args.suite_seal = contract.namespace(protocol, "suiteSeal")
        value = _authorize(args)
        print(f"Generation-4 match authorization: {contract.namespace(protocol, 'authorization')}")
        print(f"Selected network: {value['selectedNetwork']['sha256']}")
    elif args.command == "verify-authorization":
        protocol = contract.validate_protocol(args.protocol)
        path = args.authorization or contract.namespace(protocol, "authorization")
        value = _verify_authorization(path, protocol=protocol)
        print(f"Generation-4 match authorization verified: {value['selectedNetwork']['sha256']}")
    else:
        contract.self_test()
        _self_test()
        print("Generation-4 match-readiness self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
