#!/usr/bin/env python3
"""Additive, fail-closed Generation-5 match compatibility authority.

The frozen Generation-5 experiment is immutable.  This module authenticates
it, records a sampler-only/idle prelaunch baseline, builds equivalent suites
under a sibling namespace, and authorizes the selected network without ever
writing the original ``build-king-state-v5/matches`` namespace.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
COMPAT_ID = "king-state-v5-color-compat-v2"
PROTOCOL_KIND = "omega-nnue-king-state-v5-color-compat-protocol"
PREREG_KIND = "omega-nnue-king-state-v5-color-compat-preregistration"
SUITE_SEAL_KIND = "omega-nnue-king-state-v5-color-compat-suite-seal"
AUTHORIZATION_KIND = "omega-nnue-king-state-v5-color-compat-authorization"
CORE_SEAL_FIELDS = frozenset(
    {
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
        "rulesParity",
        "dotnetRuntimeBundle",
        "gates",
        "audit",
    }
)
AUTHORIZATION_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "createdUtc",
        "protocol",
        "preregistration",
        "suiteSeal",
        "preauthorizationState",
        "offlineReport",
        "offlineMatchAuthorization",
        "selectedNetwork",
        "selectedManifest",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeBundle",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "omegaMatchBundle",
        "rulesParity",
        "coreSeal",
        "gates",
        "stageOrder",
        "stageOutputsAbsentAtAuthorization",
        "runnerUpFallback",
        "launchOnlyThrough",
        "adapterPolicy",
        "finalStageSeal",
    }
)
GATES = ("development", "equal-node", "equal-time")
RELEVANT_PROCESS_NAMES = (
    "dotnet",
    "dotnet.exe",
    "omegamatch",
    "omegamatch.exe",
    "senpai",
    "senpai.exe",
)
RELEVANT_PRODUCER_PATTERNS = (
    "omega_decision_teacher_generation5.py",
    "omega_decision_teacher_generation5",
)
HEX_256 = re.compile(r"^[0-9a-f]{64}$")

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools" / "omega_nnue"
PROTOCOL_PATH = (
    REPO
    / "validation"
    / "omega-nnue-king-state-v5-color-compat-protocol.json"
)


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def strict_load(path: Path, label: str) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token {token}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not strict UTF-8 JSON: {path}") from error
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.resolve().open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "sha256": _sha256(path)}


def _repo_path(value: Any, label: str) -> Path:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty path")
    path = Path(value)
    if not path.is_absolute():
        if ".." in path.parts:
            raise ValueError(f"{label} escapes the repository")
        path = REPO / path
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(REPO)
    except ValueError as error:
        raise ValueError(f"{label} escapes the repository") from error
    return resolved


def _identity_shape(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity fields changed")
    if (
        type(value.get("path")) is not str
        or not value["path"]
        or value["path"] != value["path"].strip()
        or type(value.get("bytes")) is not int
        or value["bytes"] < 0
        or type(value.get("sha256")) is not str
        or HEX_256.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(value)


def verify_identity(
    value: Any, label: str, *, expected_path: Path | None = None
) -> Path:
    record = _identity_shape(value, label)
    path = _repo_path(record["path"], f"{label} path")
    if expected_path is not None and path != expected_path.resolve():
        raise ValueError(f"{label} resolved to the wrong file")
    actual = identity(path)
    if (
        actual["bytes"] != record["bytes"]
        or actual["sha256"] != record["sha256"]
    ):
        raise ValueError(f"{label} bytes or SHA-256 changed")
    return path


def _same_identity(left: Any, right: Any) -> bool:
    try:
        a = _identity_shape(left, "left")
        b = _identity_shape(right, "right")
        return (
            _repo_path(a["path"], "left path")
            == _repo_path(b["path"], "right path")
            and a["bytes"] == b["bytes"]
            and a["sha256"] == b["sha256"]
        )
    except (OSError, TypeError, ValueError):
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} is not UTC")
    return parsed


def _require_utc_at_or_after(
    later: Any,
    earlier: Any,
    *,
    later_label: str,
    earlier_label: str,
) -> tuple[datetime, datetime]:
    later_value = _parse_utc(later, later_label)
    earlier_value = _parse_utc(earlier, earlier_label)
    if later_value < earlier_value:
        raise ValueError(f"{later_label} predates {earlier_label}")
    return later_value, earlier_value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _namespace(protocol: Mapping[str, Any], key: str) -> Path:
    namespaces = protocol.get("namespaces")
    if type(namespaces) is not dict or key not in namespaces:
        raise ValueError(f"compatibility namespace {key} is absent")
    raw = Path(namespaces[key])
    if not raw.is_absolute():
        raw = REPO / raw
    lexical = Path(os.path.abspath(raw.expanduser()))
    try:
        relative = lexical.relative_to(REPO)
    except ValueError as error:
        raise ValueError(
            f"compatibility namespace {key} escapes the repository"
        ) from error
    current = REPO
    for part in relative.parts:
        current /= part
        if current.is_symlink() or (
            hasattr(current, "is_junction") and current.is_junction()
        ):
            raise ValueError(
                f"compatibility namespace {key} traverses a link or junction"
            )
    return _repo_path(str(lexical), f"compatibility namespace {key}")


def _exact_file_inventory(root: Path, label: str) -> tuple[list[dict[str, Any]], str]:
    root = root.resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"{label} root is invalid")
    files: list[dict[str, Any]] = []
    canonical = bytearray()
    for item in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        if item.is_symlink() or not item.is_file():
            raise ValueError(f"{label} contains a link or non-file entry: {item}")
        relative = item.relative_to(root).as_posix()
        record = identity(item)
        entry = {
            "relativePath": relative,
            "bytes": record["bytes"],
            "sha256": record["sha256"],
        }
        files.append(entry)
        canonical.extend(
            f"{relative}\t{record['bytes']}\t{record['sha256']}\n".encode("utf-8")
        )
    return files, hashlib.sha256(canonical).hexdigest()


def verify_rules_replay(protocol: Mapping[str, Any]) -> dict[str, Any]:
    value = protocol.get("rulesReplay")
    if type(value) is not dict or set(value) != {
        "source",
        "project",
        "runtimeBundle",
        "reproducibleBuild",
    }:
        raise ValueError("rules-replay protocol field inventory changed")
    verify_identity(
        value["source"],
        "rules-replay source",
        expected_path=TOOLS / "OmegaOpeningPrefixReplay" / "Program.cs",
    )
    verify_identity(
        value["project"],
        "rules-replay project",
        expected_path=(
            TOOLS
            / "OmegaOpeningPrefixReplay"
            / "OmegaOpeningPrefixReplayG5Compat.csproj"
        ),
    )
    bundle = value.get("runtimeBundle")
    if type(bundle) is not dict or set(bundle) != {
        "root",
        "assemblyRelativePath",
        "appHostRelativePath",
        "sha256",
        "files",
    }:
        raise ValueError("rules-replay runtime-bundle fields changed")
    root = _repo_path(bundle["root"], "rules-replay runtime root")
    full_files, full_digest = _exact_file_inventory(root, "rules-replay runtime")
    reproducible = value.get("reproducibleBuild")
    if type(reproducible) is not dict or set(reproducible) != {
        "sdkVersion",
        "hostRuntimeVersion",
        "configuration",
        "sdkHost",
        "sdkEntrypoint",
        "hostFxr",
        "coreClr",
        "buildArguments",
        "cleanRootsBeforeBuild",
        "canonicalRoot",
        "independentRoot",
        "builds",
        "fullInventorySha256",
        "byteIdentical",
        "files",
    }:
        raise ValueError("rules-replay reproducible-build fields changed")
    if (
        reproducible.get("sdkVersion") != "10.0.301"
        or reproducible.get("hostRuntimeVersion") != "10.0.9"
        or reproducible.get("configuration") != "Release"
        or reproducible.get("buildArguments")
        != [
            "build",
            "tools/omega_nnue/OmegaOpeningPrefixReplay/OmegaOpeningPrefixReplayG5Compat.csproj",
            "-c",
            "Release",
            "-o",
            "<OUTPUT_ROOT>",
            "--nologo",
            "-v:minimal",
            "/p:RestoreIgnoreFailedSources=true",
        ]
        or reproducible.get("cleanRootsBeforeBuild") is not True
        or reproducible.get("byteIdentical") is not True
        or _repo_path(reproducible.get("canonicalRoot"), "canonical replay root")
        != root
        or reproducible.get("files") != full_files
        or reproducible.get("fullInventorySha256") != full_digest
    ):
        raise ValueError("rules-replay canonical reproducibility evidence changed")
    for key, version_path in (
        ("sdkHost", "C:/Users/whate/.dotnet/dotnet.exe"),
        ("sdkEntrypoint", "C:/Users/whate/.dotnet/sdk/10.0.301/dotnet.dll"),
        ("hostFxr", "C:/Users/whate/.dotnet/host/fxr/10.0.9/hostfxr.dll"),
        (
            "coreClr",
            "C:/Users/whate/.dotnet/shared/Microsoft.NETCore.App/10.0.9/coreclr.dll",
        ),
    ):
        record = _identity_shape(reproducible.get(key), f"rules-replay {key}")
        actual = identity(Path(version_path))
        if (
            Path(record["path"]).resolve() != Path(version_path).resolve()
            or actual["bytes"] != record["bytes"]
            or actual["sha256"] != record["sha256"]
        ):
            raise ValueError(f"rules-replay {key} identity changed")
    if reproducible.get("builds") != [
        {
            "ordinal": 1,
            "outputRoot": reproducible["canonicalRoot"],
            "exitCode": 0,
            "warnings": 0,
            "errors": 0,
        },
        {
            "ordinal": 2,
            "outputRoot": reproducible["independentRoot"],
            "exitCode": 0,
            "warnings": 0,
            "errors": 0,
        },
    ]:
        raise ValueError("rules-replay build execution evidence changed")
    independent = _repo_path(
        reproducible.get("independentRoot"), "independent replay root"
    )
    independent_files, independent_digest = _exact_file_inventory(
        independent, "independent rules-replay build"
    )
    if independent_files != full_files or independent_digest != full_digest:
        raise ValueError("rules-replay independent build is not byte-identical")

    runtime_files = [
        item
        for item in full_files
        if (
            Path(item["relativePath"]).suffix.casefold()
            in {".dll", ".so", ".dylib"}
            or Path(item["relativePath"]).name.casefold()
            == str(bundle["appHostRelativePath"]).casefold()
            or Path(item["relativePath"]).name.casefold().endswith(".deps.json")
            or Path(item["relativePath"])
            .name.casefold()
            .endswith(".runtimeconfig.json")
        )
    ]
    canonical = bytearray()
    for item in runtime_files:
        canonical.extend(
            f"{item['relativePath']}\t{item['bytes']}\t{item['sha256']}\n".encode(
                "utf-8"
            )
        )
    if (
        bundle.get("assemblyRelativePath") != "OmegaOpeningPrefixReplay.dll"
        or bundle.get("appHostRelativePath") != "OmegaOpeningPrefixReplay.exe"
        or bundle.get("files") != runtime_files
        or bundle.get("sha256") != hashlib.sha256(canonical).hexdigest()
    ):
        raise ValueError("rules-replay runtime bundle changed")
    by_name = {item["relativePath"]: item for item in runtime_files}
    if by_name.get("ChessLib.dll") != {
        "relativePath": "ChessLib.dll",
        "bytes": 248832,
        "sha256": "16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c",
    }:
        raise ValueError("rules-replay bundle does not contain exact G5 ChessLib")
    return {**bundle, "root": str(root)}


def require_rules_match_harness(
    protocol: Mapping[str, Any], harness_bundle: Mapping[str, Any]
) -> dict[str, Any]:
    """Require replay and OmegaMatch to load byte-identical ChessLib rules."""

    replay = verify_rules_replay(protocol)
    replay_rules = [
        item for item in replay["files"] if item["relativePath"] == "ChessLib.dll"
    ]
    harness_files = harness_bundle.get("files")
    if type(harness_files) is not list:
        raise ValueError("OmegaMatch bundle lacks a file inventory")
    harness_rules = [
        item
        for item in harness_files
        if type(item) is dict
        and Path(str(item.get("relativePath", ""))).name.casefold()
        == "chesslib.dll"
    ]
    if len(replay_rules) != 1 or len(harness_rules) != 1:
        raise ValueError("rules assembly inventory is ambiguous")
    for item in (*replay_rules, *harness_rules):
        if set(item) != {"relativePath", "bytes", "sha256"}:
            raise ValueError("rules assembly identity fields changed")
    if (
        replay_rules[0]["bytes"] != harness_rules[0]["bytes"]
        or replay_rules[0]["sha256"] != harness_rules[0]["sha256"]
    ):
        raise ValueError("rules replay ChessLib differs from OmegaMatch ChessLib")
    return {
        "replayRules": replay_rules[0],
        "omegaMatchRules": harness_rules[0],
        "sameBytesAndSha256": True,
    }


def validate_protocol(
    path: Path = PROTOCOL_PATH, *, allow_tool_placeholders: bool = False
) -> dict[str, Any]:
    path = path.resolve()
    if path != PROTOCOL_PATH.resolve():
        raise ValueError("compatibility protocol must use its canonical path")
    value = strict_load(path, "compatibility protocol")
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "status",
        "template",
        "tools",
        "namespaces",
        "execution",
        "adapter",
        "rulesReplay",
        "authority",
        "publication",
    }
    if set(value) != expected:
        raise ValueError("compatibility protocol field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != PROTOCOL_KIND
        or value.get("compatibilityId") != COMPAT_ID
    ):
        raise ValueError("compatibility protocol envelope changed")
    template = _identity_shape(value.get("template"), "compatibility template")
    if not allow_tool_placeholders or template["bytes"] != 0:
        verify_identity(
            template,
            "compatibility template",
            expected_path=(
                REPO
                / "validation"
                / "omega-nnue-king-state-v5-color-compat-preregistration.template.json"
            ),
        )
    tools = value.get("tools")
    if type(tools) is not dict or set(tools) != {"readiness", "matches"}:
        raise ValueError("compatibility tool inventory changed")
    expected_tools = {
        "readiness": TOOLS / "king_state_match_readiness_generation5_compat_v2.py",
        "matches": TOOLS / "king_state_matches_generation5_compat_v2.py",
    }
    for name, expected_path in expected_tools.items():
        record = _identity_shape(tools.get(name), f"compatibility {name} tool")
        if allow_tool_placeholders and record["bytes"] == 0:
            continue
        verify_identity(
            record, f"compatibility {name} tool", expected_path=expected_path
        )
    namespace_keys = {
        "root",
        "preregistration",
        "sealed",
        "suiteSeal",
        "preauthorizationState",
        "authorization",
        "coreSeal",
        "development",
        "equalNode",
        "equalTime",
        "evidence",
        "decisions",
        "historyProjection",
        "historyManifest",
        "closure",
    }
    namespaces = value.get("namespaces")
    if type(namespaces) is not dict or set(namespaces) != namespace_keys:
        raise ValueError("compatibility namespace inventory changed")
    resolved = {key: _namespace(value, key) for key in namespace_keys}
    root = resolved["root"]
    if any(path != root and root not in path.parents for path in resolved.values()):
        raise ValueError("compatibility namespace escapes its root")
    original_root = _repo_path(
        value["authority"]["originalMatchRoot"], "original match root"
    )
    if root == original_root or root in original_root.parents or original_root in root.parents:
        raise ValueError("compatibility and original match namespaces overlap")
    adapter = value.get("adapter")
    if adapter != {
        "acceptedRawTokens": ["white", "black"],
        "derivedTokens": ["w", "b"],
        "mapping": {"white": "w", "black": "b"},
        "recordType": "ply",
        "field": "Color",
        "otherFieldsChanged": 0,
        "rawEventsImmutable": True,
        "rawPrefixIdentityRetained": True,
        "terminalProcessExitCompatibility": {
            "acceptedRawShape": (
                "authenticated terminal failed-search ply with absent/null Error, "
                "Search.ProcessExited true, and absent/null PostOfen"
            ),
            "derivedError": (
                "Authenticated OmegaMatch process exit (derived for frozen "
                "assessor compatibility)."
            ),
            "nonErrorFieldsChanged": 0,
            "transformCountMustEqualAuthenticatedRawSchedule": True,
        },
    }:
        raise ValueError("compatibility adapter policy changed")
    if value.get("execution") != {
        "gateOrder": ["development", "equal-node", "equal-time"],
        "initialPairBudget": {
            "development": 64,
            "equal-node": 128,
            "equal-time": 128,
        },
        "resumePairBudget": {
            "development": 4,
            "equal-node": 4,
            "equal-time": 4,
        },
        "freshProcessPerGame": True,
        "oneGameAtATime": True,
        "maximumConcurrentGames": 1,
        "idleSnapshotBeforePreregistration": True,
        "idleSnapshotBeforeEveryLaunch": True,
        "appendOnlyRawEvents": True,
        "assessmentBetweenLaunches": True,
        "terminalDecisionBlocksRetry": True,
        "preauthorizationStateRequiredBeforeAuthorization": True,
        "pendingIntentMayOnlyResolveToCompletionOrTerminalAbort": True,
        "partialJsonTailRetainedAndTerminallyAborted": True,
        "abortDecisionRecoveryRequired": True,
        "launchOnlyThrough": (
            "tools/omega_nnue/king_state_matches_generation5_compat_v2.py"
        ),
    }:
        raise ValueError("compatibility execution policy changed")
    if value.get("authority") != {
        "originalProfilePath": (
            "validation/omega-nnue-king-state-v5-preregistration.json"
        ),
        "originalFreezePath": (
            "validation/omega-nnue-king-state-v5-freeze.seal.json"
        ),
        "originalProtocolPath": (
            "validation/omega-nnue-king-state-v5-match-protocol.json"
        ),
        "originalMatchRoot": "build-king-state-v5/matches",
        "immutablePinsComeFromTemplate": True,
        "allThreeSamplerTriplesRequired": True,
        "canonicalRelativeAbsoluteEquivalenceRequiresSamePathBytesSha256": True,
    }:
        raise ValueError("compatibility authority policy changed")
    if value.get("publication") != {
        "exclusiveCreateOnly": True,
        "preauthorizationStatePublishedBeforeAuthorization": True,
        "rawAndDerivedEvidenceBothRequired": True,
        "assessmentBindsAdapterEvidence": True,
        "normalDecisionBindsAssessment": True,
        "abortDecisionBindsAbortCompletion": True,
        "postAuthorizationExactGlobalInventoryRequired": True,
        "closureBindsAuthenticatedPositionHistory": True,
        "closureBindsAllTerminalDecisions": True,
        "originalFilesOrDirectoriesMayNeverBeCreatedOrModified": True,
    }:
        raise ValueError("compatibility publication policy changed")
    verify_rules_replay(value)
    return value


def _template(protocol: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    path = verify_identity(protocol["template"], "compatibility template")
    value = strict_load(path, "compatibility preregistration template")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind")
        != "omega-nnue-king-state-v5-color-compat-preregistration-template"
        or value.get("compatibilityId") != COMPAT_ID
    ):
        raise ValueError("compatibility preregistration template changed")
    if set(value) != {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "status",
        "purpose",
        "immutableGeneration5Authority",
        "frozenSamplerPools",
        "frozenForbiddenBoundary",
        "prelaunchZeroState",
        "adapterPolicy",
        "rulesReplay",
        "teacherQuiescence",
        "informationBoundary",
        "promotionRule",
    }:
        raise ValueError("compatibility template field inventory changed")
    if value.get("adapterPolicy") != {
        "manifestPathCompatibility": "a frozen repository-relative identity and the same canonical absolute file are equivalent only when resolved path, byte count, and SHA-256 all match",
        "acceptedRawTokens": ["white", "black"],
        "derivedTokens": ["w", "b"],
        "mapping": {"white": "w", "black": "b"},
        "scope": "ply.Color only",
        "otherFieldsChanged": 0,
        "rawEventsImmutable": True,
        "rawPrefixIdentityRetained": True,
        "malformedOrOtherColorRejects": True,
        "derivedEvidenceMustBeExclusive": True,
        "terminalProcessExitCompatibility": {
            "acceptedRawShape": (
                "one authenticated terminal failed-search ply with absent/null "
                "Error, Search.ProcessExited true, and absent/null PostOfen"
            ),
            "derivedError": (
                "Authenticated OmegaMatch process exit (derived for frozen "
                "assessor compatibility)."
            ),
            "scope": "terminal ply.Error only",
            "nonErrorFieldsChanged": 0,
            "transformCountMustEqualAuthenticatedRawSchedule": True,
        },
    }:
        raise ValueError("compatibility template adapter policy changed")
    if value.get("rulesReplay") != {
        "protocolBindingRequired": True,
        "exactGeneration5ChessLibSha256": (
            "16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c"
        ),
        "runtimeBundleSha256": (
            "1c0ab2019a473f63dad90c92351cfdea569fd0048c60da329eaaf62c15f03dab"
        ),
        "reproducibleFullInventorySha256": (
            "d8704a21d5ed7cae06e91f11de2e08bf266443d740f9efb1fa6c19d7f06474e8"
        ),
        "immutablePerAssessmentRawPrefixRequired": True,
        "successfulPlyTranscriptParityRequired": True,
        "zeroSuccessfulPlyAttemptsRequireInitialStateReplay": True,
    }:
        raise ValueError("compatibility template rules-replay policy changed")
    replay = verify_rules_replay(protocol)
    if (
        replay["sha256"] != value["rulesReplay"]["runtimeBundleSha256"]
        or next(
            item["sha256"]
            for item in replay["files"]
            if item["relativePath"] == "ChessLib.dll"
        )
        != value["rulesReplay"]["exactGeneration5ChessLibSha256"]
    ):
        raise ValueError("template and protocol rules-replay bindings differ")
    teacher = value.get("teacherQuiescence")
    if type(teacher) is not dict or set(teacher) != {
        "prelabelFreeze",
        "requiredCompletedStages",
        "activeLedgerClaimsMustBeAbsent",
        "selectedChildrenAndLabelsMustBeFinalSealed",
        "knownTeacherCommandLinesMustBeAbsent",
        "completionSealsMakeSubsequentTeacherInvocationReturnBeforeEngineSpawn",
    }:
        raise ValueError("compatibility template teacher-quiescence fields changed")
    if (
        teacher.get("requiredCompletedStages") != ["shallow", "deep"]
        or teacher.get("activeLedgerClaimsMustBeAbsent") is not True
        or teacher.get("selectedChildrenAndLabelsMustBeFinalSealed") is not True
        or teacher.get("knownTeacherCommandLinesMustBeAbsent") is not True
        or teacher.get(
            "completionSealsMakeSubsequentTeacherInvocationReturnBeforeEngineSpawn"
        )
        is not True
    ):
        raise ValueError("compatibility template teacher-quiescence policy changed")
    verify_identity(teacher["prelabelFreeze"], "teacher prelabel freeze")
    return path, value


def _import_frozen_modules(template: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    authority = template["immutableGeneration5Authority"]
    for name in (
        "matchReadinessSource",
        "matchCoreSource",
        "matchOrchestratorSource",
    ):
        verify_identity(authority[name], f"frozen {name}")
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    # The frozen Python/NumPy contract must be the first G5 dependency loaded;
    # the match protocol imports the core before its own .NET runtime helper.
    importlib.import_module("king_state_generation5_runtime")
    contract = importlib.import_module("king_state_match_protocol_generation5")
    # G5 readiness imports and captures the frozen runtime contract before it
    # imports the match core (which can transitively import NumPy).  Preserve
    # that authenticated order in the compatibility process.
    readiness = importlib.import_module("king_state_match_readiness_generation5")
    core = importlib.import_module("king_state_matches")
    expected = {
        core: verify_identity(authority["matchCoreSource"], "frozen match core"),
        readiness: verify_identity(
            authority["matchReadinessSource"], "frozen match readiness"
        ),
    }
    for module, path in expected.items():
        if Path(str(module.__file__)).resolve() != path:
            raise ValueError(f"frozen module {module.__name__} imported from wrong path")
    protocol = contract.validate_protocol()
    readiness._verify_import_bindings(protocol)
    return contract, core, readiness


def _pool_records(template: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    pools = template.get("frozenSamplerPools")
    if type(pools) is not dict or set(pools) != set(GATES):
        raise ValueError("frozen sampler pool inventory changed")
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for gate in GATES:
        triple = pools[gate]
        if type(triple) is not dict or set(triple) != {
            "source",
            "manifest",
            "completionSeal",
        }:
            raise ValueError(f"{gate} sampler triple changed")
        result[gate] = {}
        for name, record in triple.items():
            verify_identity(record, f"{gate} sampler {name}")
            result[gate][name] = dict(record)
    return result


def _sampler_only_inventory(
    template: Mapping[str, Any], *, verify_bytes: bool = True
) -> list[dict[str, Any]]:
    root = REPO / "build-king-state-v5" / "matches"
    sampler = root / "sampler"
    pools = _pool_records(template) if verify_bytes else template["frozenSamplerPools"]
    allowed: dict[Path, dict[str, Any]] = {}
    for triple in pools.values():
        for record in triple.values():
            allowed[_repo_path(record["path"], "sampler path")] = dict(record)
    if (
        not root.is_dir()
        or not sampler.is_dir()
        or root.is_symlink()
        or sampler.is_symlink()
    ):
        raise ValueError("original G5 match sampler namespace is absent")
    found_files: set[Path] = set()
    for item in root.rglob("*"):
        if item.is_symlink():
            raise ValueError("original G5 match namespace contains a link")
        resolved = item.resolve()
        if item.is_dir():
            if resolved != sampler.resolve():
                raise ValueError(f"unexpected original G5 directory: {resolved}")
        elif item.is_file():
            if resolved not in allowed:
                raise ValueError(f"unexpected original G5 match artifact: {resolved}")
            found_files.add(resolved)
        else:
            raise ValueError(f"unexpected original G5 filesystem entry: {resolved}")
    if found_files != set(allowed):
        raise ValueError("original G5 sampler inventory is incomplete")
    return [
        dict(allowed[path]) for path in sorted(allowed, key=lambda p: str(p).casefold())
    ]


def _classify_process_rows(
    rows: Sequence[tuple[int, str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relevant: list[dict[str, Any]] = []
    producers: list[dict[str, Any]] = []
    for pid, raw_name, raw_command in rows:
        name = Path(raw_name).name.casefold()
        if name in RELEVANT_PROCESS_NAMES:
            relevant.append({"name": name, "pid": pid})
        command = raw_command.casefold()
        matched = next(
            (pattern for pattern in RELEVANT_PRODUCER_PATTERNS if pattern in command),
            None,
        )
        if matched is not None:
            producers.append({"name": name, "pid": pid, "pattern": matched})
    return (
        sorted(relevant, key=lambda item: (item["name"], item["pid"])),
        sorted(
            producers,
            key=lambda item: (item["name"], item["pid"], item["pattern"]),
        ),
    )


def _process_snapshot() -> dict[str, Any]:
    if platform.system() == "Windows":
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    "Select-Object ProcessId,Name,CommandLine | "
                    "ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
        )
        rows: list[tuple[int, str, str]] = []
        decoded = json.loads(completed.stdout or "[]")
        if type(decoded) is dict:
            decoded = [decoded]
        if type(decoded) is not list:
            raise ValueError("Windows process snapshot is not a JSON array")
        for row in decoded:
            if type(row) is not dict:
                raise ValueError("Windows process snapshot entry changed")
            name = str(row.get("Name") or "").strip().casefold()
            try:
                pid = int(row.get("ProcessId"))
            except (TypeError, ValueError):
                continue
            rows.append((pid, name, str(row.get("CommandLine") or "")))
        method = "Get-CimInstance Win32_Process"
    else:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,comm=,args="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
        )
        rows = []
        for line in completed.stdout.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) < 2:
                continue
            name = Path(fields[1]).name.casefold()
            command = "" if len(fields) < 3 else fields[2].casefold()
            rows.append((int(fields[0]), name, command))
        method = "ps -eo pid=,comm=,args="
    relevant, producers = _classify_process_rows(rows)
    return {
        "capturedUtc": _utc_now(),
        "method": method,
        "relevantNames": list(RELEVANT_PROCESS_NAMES),
        "relevantProducerPatterns": list(RELEVANT_PRODUCER_PATTERNS),
        "relevantProcesses": relevant,
        "relevantProducerProcesses": producers,
    }


def require_idle_processes(label: str) -> dict[str, Any]:
    snapshot = _process_snapshot()
    if snapshot["relevantProcesses"] or snapshot["relevantProducerProcesses"]:
        raise RuntimeError(
            f"{label} requires no match or G5 teacher process: "
            f"matches={snapshot['relevantProcesses']} "
            f"teachers={snapshot['relevantProducerProcesses']}"
        )
    return snapshot


def _teacher_quiescence(template: Mapping[str, Any]) -> dict[str, Any]:
    """Prove frozen G5 teacher stages cannot later spawn Senpai.

    Both search stages are complete.  The frozen teacher checks each immutable
    completion manifest before constructing worker sessions, so a later
    invocation returns before any engine process can be spawned.  Command-line
    scanning remains a second, explicit defense against an already-running
    producer.
    """

    policy = template.get("teacherQuiescence")
    if type(policy) is not dict:
        raise ValueError("teacher-quiescence policy is absent")
    prelabel_path = verify_identity(
        policy["prelabelFreeze"], "teacher-quiescence prelabel freeze"
    )
    prelabel = strict_load(prelabel_path, "teacher-quiescence prelabel freeze")
    planned = prelabel.get("plannedArtifacts")
    identities = prelabel.get("identities")
    if (
        type(planned) is not dict
        or set(planned) != {"shallowLedger", "selectedChildren", "deepLedger", "labels"}
        or type(identities) is not dict
        or type(identities.get("producer")) is not dict
        or prelabel.get("finalStageSeal") is not True
        or prelabel.get("declaration", {}).get("teacherSearchesPresentAtFreeze")
        is not False
    ):
        raise ValueError("teacher-quiescence prelabel contract changed")
    stages: dict[str, Any] = {}
    for stage, key in (("shallow", "shallowLedger"), ("deep", "deepLedger")):
        ledger = _repo_path(planned[key], f"{stage} teacher ledger")
        lock = Path(str(ledger) + ".lock.json").resolve()
        completion_path = Path(
            str(ledger) + ".complete.manifest.json"
        ).resolve()
        claim = Path(str(ledger) + ".active.claim.json").resolve()
        if claim.exists():
            raise RuntimeError(f"{stage} teacher active-ledger claim exists")
        completion = strict_load(
            completion_path, f"{stage} teacher completion manifest"
        )
        expected_fields = {
            "schemaVersion",
            "kind",
            "createdUtc",
            "stage",
            "lockSha256",
            "recordsRequired",
            "successfulChildren",
            "rejectedChildren",
            "rejectedChildIds",
            "maximumAttempts",
            "workerCount",
            "sessionLifecycle",
            "ledger",
            "lock",
            "preregistration",
            "finalFreezeSeal",
            "prelabelFreeze",
            "producer",
            "finalStageSeal",
        }
        if set(completion) != expected_fields:
            raise ValueError(f"{stage} teacher completion fields changed")
        required = completion.get("recordsRequired")
        successes = completion.get("successfulChildren")
        rejected = completion.get("rejectedChildren")
        rejected_ids = completion.get("rejectedChildIds")
        if (
            completion.get("schemaVersion") != 1
            or completion.get("kind") != "omega-decision-search-completion"
            or completion.get("stage") != stage
            or type(required) is not int
            or required <= 0
            or type(successes) is not int
            or successes < 0
            or type(rejected) is not int
            or rejected < 0
            or successes + rejected != required
            or type(rejected_ids) is not list
            or len(rejected_ids) != rejected
            or len(set(rejected_ids)) != len(rejected_ids)
            or any(type(item) is not str or not item for item in rejected_ids)
            or completion.get("finalStageSeal") is not True
            or completion.get("producer") != identities["producer"]
            or not _same_identity(completion.get("ledger"), identity(ledger))
            or not _same_identity(completion.get("lock"), identity(lock))
            or not _same_identity(
                completion.get("prelabelFreeze"), identity(prelabel_path)
            )
            or completion.get("preregistration") != identities.get("preregistration")
            or completion.get("finalFreezeSeal") != identities.get("finalFreezeSeal")
        ):
            raise ValueError(f"{stage} teacher completion binding changed")
        for name, producer_identity in identities["producer"].items():
            if name == "python":
                shaped = {
                    key: producer_identity[key]
                    for key in ("path", "bytes", "sha256")
                }
                _identity_shape(shaped, f"{stage} teacher producer python")
                actual_python = identity(Path(str(shaped["path"])))
                if (
                    Path(actual_python["path"]).resolve()
                    != Path(str(shaped["path"])).resolve()
                    or actual_python["bytes"] != shaped["bytes"]
                    or actual_python["sha256"] != shaped["sha256"]
                ):
                    raise ValueError(f"{stage} teacher producer python changed")
            else:
                verify_identity(
                    producer_identity, f"{stage} teacher producer {name}"
                )
        stages[stage] = {
            "ledger": identity(ledger),
            "lock": identity(lock),
            "completion": identity(completion_path),
            "activeClaimPath": str(claim),
            "activeClaimAbsent": True,
            "subsequentInvocationReturnsBeforeEngineSpawn": True,
        }

    finalized: dict[str, Any] = {}
    for name, key in (("selectedChildren", "selectedChildren"), ("labels", "labels")):
        output = _repo_path(planned[key], f"teacher {name} output")
        manifest_path = Path(str(output) + ".manifest.json").resolve()
        manifest = strict_load(manifest_path, f"teacher {name} manifest")
        common_fields = {
            "schemaVersion",
            "kind",
            "createdUtc",
            "policy",
            "coverage",
            "inputs",
            "producer",
            "finalStageSeal",
            "output",
        }
        if (
            set(manifest) != common_fields
            or manifest.get("schemaVersion") != 1
            or manifest.get("finalStageSeal") is not True
            or not _same_identity(manifest.get("output"), identity(output))
            or manifest.get("producer") != identities["producer"]
        ):
            raise ValueError(f"teacher {name} final seal changed")
        inputs = manifest.get("inputs")
        if name == "selectedChildren":
            if (
                manifest.get("kind") != "omega-decision-four-child-selection"
                or type(inputs) is not dict
                or set(inputs)
                != {
                    "children",
                    "shallowLedger",
                    "preregistration",
                    "finalFreezeSeal",
                    "prelabelFreeze",
                }
                or not _same_identity(inputs.get("children"), identities["children"])
                or not _same_identity(
                    inputs.get("shallowLedger"), stages["shallow"]["ledger"]
                )
            ):
                raise ValueError("teacher selectedChildren binding changed")
        else:
            selected = finalized.get("selectedChildren")
            if (
                manifest.get("kind") != "omega-decision-label-manifest"
                or selected is None
                or type(inputs) is not dict
                or set(inputs)
                != {
                    "selected",
                    "selectedManifest",
                    "deepLedger",
                    "preregistration",
                    "finalFreezeSeal",
                    "prelabelFreeze",
                    "componentMap",
                }
                or not _same_identity(inputs.get("selected"), selected["output"])
                or not _same_identity(
                    inputs.get("selectedManifest"), selected["manifest"]
                )
                or not _same_identity(
                    inputs.get("deepLedger"), stages["deep"]["ledger"]
                )
                or not _same_identity(
                    inputs.get("componentMap"), identities["componentMap"]
                )
            ):
                raise ValueError("teacher labels binding changed")
        if (
            inputs.get("preregistration") != identities.get("preregistration")
            or inputs.get("finalFreezeSeal") != identities.get("finalFreezeSeal")
            or not _same_identity(inputs.get("prelabelFreeze"), identity(prelabel_path))
        ):
            raise ValueError(f"teacher {name} authority binding changed")
        for producer_name, producer_identity in identities["producer"].items():
            if producer_name != "python":
                verify_identity(
                    producer_identity,
                    f"teacher {name} producer {producer_name}",
                )
        finalized[name] = {
            "output": identity(output),
            "manifest": identity(manifest_path),
        }
    return {
        "policy": copy.deepcopy(policy),
        "prelabelFreeze": identity(prelabel_path),
        "completedStages": stages,
        "finalizedOutputs": finalized,
        "activeClaimsAbsent": True,
        "teacherEngineSpawnStructurallyClosed": True,
    }


def verify_foundation(
    protocol_path: Path = PROTOCOL_PATH,
) -> dict[str, Any]:
    protocol = validate_protocol(protocol_path)
    template_path, template = _template(protocol)
    authority = template.get("immutableGeneration5Authority")
    if type(authority) is not dict or set(authority) != {
        "profile",
        "finalFreeze",
        "matchProtocol",
        "matchReadinessSource",
        "matchCoreSource",
        "matchOrchestratorSource",
    }:
        raise ValueError("frozen Generation-5 authority inventory changed")
    verified_authority = {
        name: identity(verify_identity(record, f"frozen {name}"))
        for name, record in authority.items()
    }
    pool_inventory = _sampler_only_inventory(template)
    boundary = template.get("frozenForbiddenBoundary")
    if type(boundary) is not dict or set(boundary) != {"manifest", "catalog"}:
        raise ValueError("frozen forbidden boundary changed")
    forbidden = {
        name: identity(verify_identity(record, f"frozen forbidden {name}"))
        for name, record in boundary.items()
    }
    contract, _core, readiness = _import_frozen_modules(template)
    original_protocol = contract.validate_protocol()
    profile_path = verify_identity(authority["profile"], "frozen G5 profile")
    freeze_path = verify_identity(authority["finalFreeze"], "frozen G5 freeze")
    profile = readiness._validate_profile(profile_path, original_protocol)
    readiness._validate_final_freeze(freeze_path, profile_path)
    readiness._verify_import_bindings(original_protocol, profile)
    freeze = strict_load(freeze_path, "frozen G5 freeze")
    if not _same_identity(freeze.get("preregistration"), authority["profile"]):
        raise ValueError("frozen G5 freeze/profile binding changed")
    return {
        "protocol": identity(protocol_path),
        "template": identity(template_path),
        "originalAuthority": verified_authority,
        "samplerPools": pool_inventory,
        "forbiddenBoundary": forbidden,
        "originalNamespace": {
            "root": str((REPO / "build-king-state-v5" / "matches").resolve()),
            "samplerOnly": True,
            "files": len(pool_inventory),
            "suites": 0,
            "events": 0,
            "results": 0,
            "claims": 0,
            "closures": 0,
        },
    }


def _verify_preregistration(
    path: Path, protocol: Mapping[str, Any]
) -> dict[str, Any]:
    path = path.resolve()
    if path != _namespace(protocol, "preregistration"):
        raise ValueError("compatibility preregistration path changed")
    value = strict_load(path, "compatibility preregistration")
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "createdUtc",
        "protocol",
        "template",
        "compatibilityTools",
        "originalAuthority",
        "frozenSamplerPools",
        "frozenForbiddenBoundary",
        "prelaunchZeroState",
        "adapterPolicy",
        "rulesReplay",
        "teacherQuiescence",
        "informationBoundary",
        "finalStageSeal",
    }
    if set(value) != expected:
        raise ValueError("compatibility preregistration fields changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != PREREG_KIND
        or value.get("compatibilityId") != COMPAT_ID
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError("compatibility preregistration envelope changed")
    created = _parse_utc(
        value.get("createdUtc"), "compatibility preregistration createdUtc"
    )
    if not _same_identity(value.get("protocol"), identity(PROTOCOL_PATH)):
        raise ValueError("compatibility preregistration protocol changed")
    template_path, template = _template(protocol)
    if not _same_identity(value.get("template"), identity(template_path)):
        raise ValueError("compatibility preregistration template changed")
    tools = value.get("compatibilityTools")
    if type(tools) is not dict or set(tools) != {"readiness", "matches"}:
        raise ValueError("compatibility preregistration tool inventory changed")
    for name in tools:
        if not _same_identity(tools[name], protocol["tools"][name]):
            raise ValueError(f"compatibility preregistration {name} tool changed")
        verify_identity(tools[name], f"preregistered {name} tool")
    authority = value.get("originalAuthority")
    if type(authority) is not dict or set(authority) != set(
        template["immutableGeneration5Authority"]
    ):
        raise ValueError("compatibility preregistration authority inventory changed")
    for name, expected_record in template["immutableGeneration5Authority"].items():
        if not _same_identity(authority[name], expected_record):
            raise ValueError(f"compatibility preregistration authority {name} changed")
        verify_identity(authority[name], f"preregistered frozen {name}")
    boundary = value.get("frozenForbiddenBoundary")
    if type(boundary) is not dict or set(boundary) != {"manifest", "catalog"}:
        raise ValueError("compatibility preregistration forbidden boundary changed")
    for name, expected_record in template["frozenForbiddenBoundary"].items():
        if not _same_identity(boundary[name], expected_record):
            raise ValueError(f"preregistered forbidden {name} changed")
        verify_identity(boundary[name], f"preregistered forbidden {name}")
    zero = value.get("prelaunchZeroState")
    if (
        type(zero) is not dict
        or set(zero)
        != {
            "observedUtc",
            "compatibilityNamespace",
            "compatibilityNamespacePreexistingEntries",
            "originalMatchRoot",
            "originalSamplerOnly",
            "originalMatchArtifacts",
            "idleProcessSnapshot",
            "precedesAllGeneration5CompatibilityLaunches",
        }
        or zero.get("compatibilityNamespacePreexistingEntries") != []
        or zero.get("originalSamplerOnly") is not True
        or zero.get("originalMatchArtifacts")
        != {"suites": 0, "events": 0, "results": 0, "claims": 0, "closures": 0}
        or type(zero.get("idleProcessSnapshot")) is not dict
        or zero["idleProcessSnapshot"].get("relevantProcesses") != []
        or zero["idleProcessSnapshot"].get("relevantNames")
        != list(RELEVANT_PROCESS_NAMES)
        or zero["idleProcessSnapshot"].get("relevantProducerPatterns")
        != list(RELEVANT_PRODUCER_PATTERNS)
        or zero["idleProcessSnapshot"].get("relevantProducerProcesses") != []
        or type(zero["idleProcessSnapshot"].get("capturedUtc")) is not str
        or zero.get("precedesAllGeneration5CompatibilityLaunches") is not True
    ):
        raise ValueError("compatibility prelaunch zero-state changed")
    observed = _parse_utc(
        zero.get("observedUtc"), "compatibility zero-state observedUtc"
    )
    captured = _parse_utc(
        zero["idleProcessSnapshot"].get("capturedUtc"),
        "compatibility zero-state idle snapshot capturedUtc",
    )
    if captured > observed or observed > created:
        raise ValueError("compatibility prelaunch zero-state chronology changed")
    if value.get("adapterPolicy") != template["adapterPolicy"]:
        raise ValueError("compatibility preregistration adapter policy changed")
    if value.get("rulesReplay") != {
        "policy": template["rulesReplay"],
        "source": protocol["rulesReplay"]["source"],
        "project": protocol["rulesReplay"]["project"],
        "runtimeBundle": protocol["rulesReplay"]["runtimeBundle"],
        "reproducibleBuild": protocol["rulesReplay"]["reproducibleBuild"],
    }:
        raise ValueError("compatibility preregistration rules-replay binding changed")
    verify_rules_replay(protocol)
    current_quiescence = _teacher_quiescence(template)
    if value.get("teacherQuiescence") != current_quiescence:
        raise ValueError("compatibility teacher-quiescence evidence changed")
    if value.get("informationBoundary") != template["informationBoundary"]:
        raise ValueError("compatibility preregistration boundary changed")
    expected_pools = [
        record
        for gate in GATES
        for record in template["frozenSamplerPools"][gate].values()
    ]
    actual_pools = value.get("frozenSamplerPools")
    if type(actual_pools) is not list or len(actual_pools) != 9:
        raise ValueError("compatibility preregistration lacks nine pool pins")
    if sorted(
        (_repo_path(item["path"], "pool").as_posix(), item["bytes"], item["sha256"])
        for item in actual_pools
    ) != sorted(
        (_repo_path(item["path"], "pool").as_posix(), item["bytes"], item["sha256"])
        for item in expected_pools
    ):
        raise ValueError("compatibility preregistration pool pins changed")
    for record in actual_pools:
        verify_identity(record, "preregistered sampler pool")
    return value


def _preregister(args: argparse.Namespace) -> dict[str, Any]:
    protocol = validate_protocol(args.protocol)
    root = _namespace(protocol, "root")
    if root.exists():
        raise FileExistsError(
            "compatibility namespace must be absent before preregistration"
        )
    foundation = verify_foundation(args.protocol)
    snapshot = require_idle_processes("compatibility preregistration")
    template_path, template = _template(protocol)
    teacher_quiescence = _teacher_quiescence(template)
    observed_utc = _utc_now()
    created_utc = _utc_now()
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PREREG_KIND,
        "compatibilityId": COMPAT_ID,
        "createdUtc": created_utc,
        "protocol": identity(args.protocol),
        "template": identity(template_path),
        "compatibilityTools": {
            name: identity(verify_identity(record, f"compatibility {name} tool"))
            for name, record in protocol["tools"].items()
        },
        "originalAuthority": foundation["originalAuthority"],
        "frozenSamplerPools": foundation["samplerPools"],
        "frozenForbiddenBoundary": foundation["forbiddenBoundary"],
        "prelaunchZeroState": {
            "observedUtc": observed_utc,
            "compatibilityNamespace": str(root),
            "compatibilityNamespacePreexistingEntries": [],
            "originalMatchRoot": foundation["originalNamespace"]["root"],
            "originalSamplerOnly": True,
            "originalMatchArtifacts": {
                "suites": 0,
                "events": 0,
                "results": 0,
                "claims": 0,
                "closures": 0,
            },
            "idleProcessSnapshot": snapshot,
            "precedesAllGeneration5CompatibilityLaunches": True,
        },
        "adapterPolicy": copy.deepcopy(template["adapterPolicy"]),
        "rulesReplay": {
            "policy": copy.deepcopy(template["rulesReplay"]),
            "source": copy.deepcopy(protocol["rulesReplay"]["source"]),
            "project": copy.deepcopy(protocol["rulesReplay"]["project"]),
            "runtimeBundle": copy.deepcopy(
                protocol["rulesReplay"]["runtimeBundle"]
            ),
            "reproducibleBuild": copy.deepcopy(
                protocol["rulesReplay"]["reproducibleBuild"]
            ),
        },
        "teacherQuiescence": teacher_quiescence,
        "informationBoundary": copy.deepcopy(template["informationBoundary"]),
        "finalStageSeal": True,
    }
    path = _namespace(protocol, "preregistration")
    _atomic_json(path, value)
    return _verify_preregistration(path, protocol)


def _canonical_profile_for_compat(
    profile: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = copy.deepcopy(dict(profile))
    identities = result.get("finalFreezeIdentities")
    if type(identities) is not dict:
        raise ValueError("G5 profile has no final identities")
    records = identities.get("forbiddenPositionCatalogManifests")
    if type(records) is not list or not records:
        raise ValueError("G5 profile has no forbidden manifests")
    transformations: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        shaped = _identity_shape(raw, f"G5 forbidden manifest {index}")
        path = verify_identity(shaped, f"G5 forbidden manifest {index}")
        canonical = identity(path)
        transformations.append(
            {
                "index": index,
                "frozenPath": shaped["path"],
                "canonicalPath": canonical["path"],
                "bytes": canonical["bytes"],
                "sha256": canonical["sha256"],
                "sameResolvedFile": True,
            }
        )
        records[index] = canonical
    return result, {
        "policy": "relative and absolute identities are equivalent only for the same resolved file, byte count, and SHA-256",
        "transformations": transformations,
        "filesChanged": 0,
        "inMemoryPathRepresentationsChanged": len(transformations),
    }


def _suite_paths(protocol: Mapping[str, Any]) -> dict[str, Path]:
    sealed = _namespace(protocol, "sealed")
    return {
        gate: sealed / f"king-state-v5-color-compat-v2-{gate}-suite.json"
        for gate in GATES
    }


def _stage_paths(protocol: Mapping[str, Any]) -> dict[str, Path]:
    return {
        "development": _namespace(protocol, "development"),
        "equal-node": _namespace(protocol, "equalNode"),
        "equal-time": _namespace(protocol, "equalTime"),
    }


def _config_paths(protocol: Mapping[str, Any]) -> dict[str, Path]:
    sealed = _namespace(protocol, "sealed")
    return {
        gate: sealed / f"king-state-v5-color-compat-v2-{gate}-match.json"
        for gate in GATES
    }


def _selected_summary(roots: Sequence[Any]) -> dict[str, Any]:
    return {
        "roots": len(roots),
        "phaseCounts": dict(sorted(Counter(root.phase for root in roots).items())),
        "sideToMoveCounts": dict(
            sorted(Counter(root.side for root in roots).items())
        ),
        "uniqueTrajectoryPairs": len({root.source_group for root in roots}),
        "uniqueOrbits": len({root.orbit for root in roots}),
    }


def _seal_suites(args: argparse.Namespace) -> dict[str, Any]:
    protocol = validate_protocol(args.protocol)
    prereg_path = _namespace(protocol, "preregistration")
    _verify_preregistration(prereg_path, protocol)
    _sampler_only_inventory(_template(protocol)[1])
    outputs = [_namespace(protocol, "suiteSeal"), *_suite_paths(protocol).values()]
    if any(path.exists() for path in outputs):
        raise FileExistsError("refusing to replace a compatibility suite artifact")
    if any(path.exists() for path in _stage_paths(protocol).values()):
        raise FileExistsError("a compatibility match stage exists before suite seal")

    template = _template(protocol)[1]
    before_pools = _sampler_only_inventory(template)
    contract, core, readiness = _import_frozen_modules(template)
    original_protocol = contract.validate_protocol()
    readiness._install_core_profile(original_protocol)
    authority = template["immutableGeneration5Authority"]
    profile_path = verify_identity(authority["profile"], "frozen G5 profile")
    freeze_path = verify_identity(authority["finalFreeze"], "frozen G5 freeze")
    profile = readiness._validate_profile(profile_path, original_protocol)
    readiness._validate_final_freeze(freeze_path, profile_path)
    boundary = template["frozenForbiddenBoundary"]
    manifest_path = verify_identity(boundary["manifest"], "forbidden manifest")
    catalog_path = verify_identity(boundary["catalog"], "forbidden catalog")
    manifests, positions, forbidden, exclusion_stats = (
        readiness._validate_forbidden_inputs(
            [manifest_path], [catalog_path], profile=profile
        )
    )
    readiness._assert_profile_forbidden_manifests(profile, manifests)
    compatible_profile, path_evidence = _canonical_profile_for_compat(profile)
    selection_forbidden, current_private, current_audit = (
        readiness._current_corpus_exclusion(compatible_profile, forbidden)
    )

    source_audits: dict[str, Any] = {}
    selected: dict[str, list[Any]] = {}
    rejected: dict[str, dict[str, int]] = {}
    used_fresh: set[str] = set()
    sources = readiness._source_paths(original_protocol)
    for gate in GATES:
        roots, audit = readiness._verify_sampler_source(
            sources[gate], gate, original_protocol
        )
        if not args.skip_sampler_replay:
            audit["deterministicReplay"] = readiness._reproduce_sampler_source(
                sources[gate], gate, original_protocol
            )
        else:
            audit["deterministicReplay"] = {
                "performed": False,
                "reason": "explicit diagnostic-only skip; seal is non-authorizable",
            }
        readiness._gate_current_corpus_evidence(roots, (), current_private)
        selected[gate], rejected[gate] = core._select_roots(
            roots, core.GATE_SPECS[gate], selection_forbidden, used_fresh
        )
        audit["currentCorpusDisjointness"] = (
            readiness._gate_current_corpus_evidence(
                roots, selected[gate], current_private
            )
        )
        source_audits[gate] = audit

    suite_paths = _suite_paths(protocol)
    schedules: dict[str, list[dict[str, Any]]] = {}
    for gate in GATES:
        suite, schedule = core._suite(gate, selected[gate])
        suite["name"] = f"Omega NNUE king-state v5 color-compat-v2 {gate}"
        schedules[gate] = schedule
        _atomic_json(suite_paths[gate], suite)
        core._verify_suite(gate, suite_paths[gate])
    omega_match_bundle = core._harness_bundle_identity(
        Path(
            readiness._runtime_identity(
                original_protocol, "omegaMatchAssembly"
            )["path"]
        )
    )
    rules_parity = require_rules_match_harness(protocol, omega_match_bundle)
    after_pools = _sampler_only_inventory(template)
    if before_pools != after_pools:
        raise ValueError("frozen sampler pools changed during compatibility sealing")
    suite_seal = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SUITE_SEAL_KIND,
        "compatibilityId": COMPAT_ID,
        "createdUtc": _utc_now(),
        "protocol": identity(args.protocol),
        "preregistration": identity(prereg_path),
        "originalAuthority": {
            name: identity(verify_identity(record, f"frozen {name}"))
            for name, record in authority.items()
        },
        "frozenSamplerPoolsBefore": before_pools,
        "frozenSamplerPoolsAfter": after_pools,
        "forbiddenCatalogManifests": manifests,
        "forbiddenPositionFiles": positions,
        "forbiddenAudit": {
            "uniqueOrbitSignatures": len(forbidden),
            "scan": exclusion_stats,
        },
        "pathCompatibilityEvidence": path_evidence,
        "currentCorpusAudit": current_audit,
        "samplerSources": source_audits,
        "dotnetHost": readiness._runtime_identity(original_protocol, "dotnetHost"),
        "dotnetRuntimeManifest": readiness._runtime_identity(
            original_protocol, "dotnetRuntimeManifest"
        ),
        "dotnetRuntimeBundle": readiness._dotnet_runtime_bundle(original_protocol),
        "engine": readiness._runtime_identity(original_protocol, "engine"),
        "omegaMatchAssembly": readiness._runtime_identity(
            original_protocol, "omegaMatchAssembly"
        ),
        "omegaMatchAppHost": readiness._runtime_identity(
            original_protocol, "omegaMatchAppHost"
        ),
        "omegaMatchBundle": omega_match_bundle,
        "rulesParity": rules_parity,
        "suites": {
            gate: {
                **_selected_summary(selected[gate]),
                "rejections": rejected[gate],
                "scheduledOpeningIds": [item["id"] for item in schedules[gate]],
                "identity": identity(suite_paths[gate]),
            }
            for gate in GATES
        },
        "rootDigest": contract.root_digest(suite_paths),
        "crossSuite": {
            "roots": sum(len(items) for items in selected.values()),
            "uniqueOrbitSignatures": len(used_fresh),
            "mutuallyOrbitDisjoint": True,
        },
        "stageOutputsAbsentAtSeal": {
            gate: str(path) for gate, path in _stage_paths(protocol).items()
        },
        "candidateIdentity": None,
        "informationBoundary": {
            "rulesOnlySuiteConstruction": True,
            "targetFieldsDecoded": 0,
            "matchResultsAccessed": 0,
            "originalArtifactsRewritten": 0,
        },
        "authorizable": not args.skip_sampler_replay,
        "finalStageSeal": True,
    }
    _atomic_json(_namespace(protocol, "suiteSeal"), suite_seal)
    # Construction above already performed the full current-corpus replay,
    # sampler verification, deterministic root selection, and (unless the
    # diagnostic flag was used) independent sampler reproduction.  Repeating
    # that multi-hour replay immediately would add no independent state.
    return verify_suite_seal(
        _namespace(protocol, "suiteSeal"), protocol, deep=False
    )


def _recompute_candidate_blind_selection(
    protocol: Mapping[str, Any],
    template: Mapping[str, Any],
    *,
    reproduce_sources: bool,
) -> dict[str, Any]:
    """Re-derive every selected root and exclusion audit from frozen inputs."""

    contract, core, readiness = _import_frozen_modules(template)
    original_protocol = contract.validate_protocol()
    readiness._install_core_profile(original_protocol)
    authority = template["immutableGeneration5Authority"]
    profile_path = verify_identity(authority["profile"], "replay G5 profile")
    freeze_path = verify_identity(authority["finalFreeze"], "replay G5 freeze")
    profile = readiness._validate_profile(profile_path, original_protocol)
    readiness._validate_final_freeze(freeze_path, profile_path)
    boundary = template["frozenForbiddenBoundary"]
    manifest_path = verify_identity(
        boundary["manifest"], "replay forbidden manifest"
    )
    catalog_path = verify_identity(boundary["catalog"], "replay forbidden catalog")
    manifests, positions, forbidden, exclusion_stats = (
        readiness._validate_forbidden_inputs(
            [manifest_path], [catalog_path], profile=profile
        )
    )
    readiness._assert_profile_forbidden_manifests(profile, manifests)
    compatible_profile, path_evidence = _canonical_profile_for_compat(profile)
    selection_forbidden, current_private, current_audit = (
        readiness._current_corpus_exclusion(compatible_profile, forbidden)
    )
    sources = readiness._source_paths(original_protocol)
    selected: dict[str, list[Any]] = {}
    rejected: dict[str, dict[str, int]] = {}
    source_audits: dict[str, Any] = {}
    used_fresh: set[str] = set()
    expected_suites: dict[str, dict[str, Any]] = {}
    schedules: dict[str, list[dict[str, Any]]] = {}
    for gate in GATES:
        roots, audit = readiness._verify_sampler_source(
            sources[gate], gate, original_protocol
        )
        if reproduce_sources:
            audit["deterministicReplay"] = readiness._reproduce_sampler_source(
                sources[gate], gate, original_protocol
            )
        readiness._gate_current_corpus_evidence(roots, (), current_private)
        selected[gate], rejected[gate] = core._select_roots(
            roots, core.GATE_SPECS[gate], selection_forbidden, used_fresh
        )
        audit["currentCorpusDisjointness"] = (
            readiness._gate_current_corpus_evidence(
                roots, selected[gate], current_private
            )
        )
        suite, schedule = core._suite(gate, selected[gate])
        suite["name"] = f"Omega NNUE king-state v5 color-compat-v2 {gate}"
        expected_suites[gate] = suite
        schedules[gate] = schedule
        source_audits[gate] = audit
    return {
        "contract": contract,
        "core": core,
        "readiness": readiness,
        "originalProtocol": original_protocol,
        "manifests": manifests,
        "positions": positions,
        "forbiddenAudit": {
            "uniqueOrbitSignatures": len(forbidden),
            "scan": exclusion_stats,
        },
        "pathCompatibilityEvidence": path_evidence,
        "currentCorpusAudit": current_audit,
        "samplerSources": source_audits,
        "selected": selected,
        "rejected": rejected,
        "expectedSuites": expected_suites,
        "schedules": schedules,
        "usedFresh": used_fresh,
    }


def verify_suite_seal(
    path: Path,
    protocol: Mapping[str, Any] | None = None,
    *,
    deep: bool = True,
    reproduce_sources: bool = False,
) -> dict[str, Any]:
    protocol = validate_protocol() if protocol is None else dict(protocol)
    path = path.resolve()
    if path != _namespace(protocol, "suiteSeal"):
        raise ValueError("compatibility suite seal path changed")
    value = strict_load(path, "compatibility suite seal")
    expected_fields = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "createdUtc",
        "protocol",
        "preregistration",
        "originalAuthority",
        "frozenSamplerPoolsBefore",
        "frozenSamplerPoolsAfter",
        "forbiddenCatalogManifests",
        "forbiddenPositionFiles",
        "forbiddenAudit",
        "pathCompatibilityEvidence",
        "currentCorpusAudit",
        "samplerSources",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeBundle",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "omegaMatchBundle",
        "rulesParity",
        "suites",
        "rootDigest",
        "crossSuite",
        "stageOutputsAbsentAtSeal",
        "candidateIdentity",
        "informationBoundary",
        "authorizable",
        "finalStageSeal",
    }
    if set(value) != expected_fields:
        raise ValueError("compatibility suite-seal field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != SUITE_SEAL_KIND
        or value.get("compatibilityId") != COMPAT_ID
        or value.get("finalStageSeal") is not True
        or value.get("candidateIdentity") is not None
        or type(value.get("authorizable")) is not bool
    ):
        raise ValueError("compatibility suite seal envelope changed")
    if not _same_identity(value.get("protocol"), identity(PROTOCOL_PATH)):
        raise ValueError("compatibility suite protocol changed")
    prereg = verify_identity(value.get("preregistration"), "suite preregistration")
    preregistration = _verify_preregistration(prereg, protocol)
    _require_utc_at_or_after(
        value.get("createdUtc"),
        preregistration.get("createdUtc"),
        later_label="compatibility suite-seal createdUtc",
        earlier_label="compatibility preregistration createdUtc",
    )
    template = _template(protocol)[1]
    expected_authority = {
        name: identity(verify_identity(record, f"suite frozen {name}"))
        for name, record in template["immutableGeneration5Authority"].items()
    }
    if value.get("originalAuthority") != expected_authority:
        raise ValueError("compatibility suite original authority changed")
    current_pools = _sampler_only_inventory(template)
    if value.get("frozenSamplerPoolsBefore") != current_pools or value.get(
        "frozenSamplerPoolsAfter"
    ) != current_pools:
        raise ValueError("compatibility suite pool bindings changed")
    contract, core, readiness = _import_frozen_modules(template)
    original_protocol = contract.validate_protocol()
    readiness._install_core_profile(original_protocol)
    if value.get("rulesParity") != require_rules_match_harness(
        protocol, value.get("omegaMatchBundle")
    ):
        raise ValueError("compatibility suite rules parity changed")
    suites = value.get("suites")
    if type(suites) is not dict or set(suites) != set(GATES):
        raise ValueError("compatibility suite inventory changed")
    suite_paths: dict[str, Path] = {}
    fresh: set[str] = set()
    for gate in GATES:
        entry = suites[gate]
        if type(entry) is not dict or "identity" not in entry:
            raise ValueError(f"{gate} compatibility suite entry changed")
        suite_path = verify_identity(
            entry["identity"],
            f"{gate} compatibility suite",
            expected_path=_suite_paths(protocol)[gate],
        )
        openings = core._verify_suite(gate, suite_path)
        if entry.get("roots") != len(openings):
            raise ValueError(f"{gate} compatibility suite root count changed")
        for opening in openings.values():
            signatures = set(opening["kingStateMatch"]["orbitSignatures"])
            if fresh.intersection(signatures):
                raise ValueError("compatibility suites are not mutually orbit-disjoint")
            fresh.update(signatures)
        suite_paths[gate] = suite_path
    if value.get("rootDigest") != contract.root_digest(suite_paths):
        raise ValueError("compatibility suite root digest changed")
    if value.get("crossSuite") != {
        "roots": sum(core.GATE_SPECS[gate]["roots"] for gate in GATES),
        "uniqueOrbitSignatures": len(fresh),
        "mutuallyOrbitDisjoint": True,
    }:
        raise ValueError("compatibility cross-suite audit changed")
    if value.get("informationBoundary") != {
        "rulesOnlySuiteConstruction": True,
        "targetFieldsDecoded": 0,
        "matchResultsAccessed": 0,
        "originalArtifactsRewritten": 0,
    }:
        raise ValueError("compatibility suite information boundary changed")
    for key in (
        "dotnetHost",
        "dotnetRuntimeManifest",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
    ):
        verify_identity(value.get(key), f"suite runtime {key}")
    if value.get("omegaMatchBundle") != core._harness_bundle_identity(
        Path(str(value["omegaMatchAssembly"]["path"]))
    ):
        raise ValueError("compatibility OmegaMatch bundle changed")
    stage_outputs = {
        gate: str(stage) for gate, stage in _stage_paths(protocol).items()
    }
    if value.get("stageOutputsAbsentAtSeal") != stage_outputs:
        raise ValueError("compatibility suite stage-absence claims changed")
    sources = value.get("samplerSources")
    if type(sources) is not dict or set(sources) != set(GATES):
        raise ValueError("compatibility sampler-source audit inventory changed")
    for gate in GATES:
        audit = sources[gate]
        if type(audit) is not dict or set(audit) != {
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
            raise ValueError(f"{gate} sampler-source audit fields changed")
        triple = template["frozenSamplerPools"][gate]
        for source_key, template_key in (
            ("source", "source"),
            ("manifest", "manifest"),
            ("completionSeal", "completionSeal"),
        ):
            if not _same_identity(audit[source_key], triple[template_key]):
                raise ValueError(f"{gate} sampler audit pin changed")
        replay = audit["deterministicReplay"]
        if value["authorizable"]:
            if type(replay) is not dict or set(replay) != {
                "exactSourceBytes",
                "exactSourceSha256",
                "records",
                "runtime",
                "framework",
                "workers",
                "dotnetRuntimeBundleSha256",
                "rootSamplerBundleSha256",
            }:
                raise ValueError(f"{gate} deterministic replay evidence changed")
            if (
                replay["exactSourceBytes"] != audit["source"]["bytes"]
                or replay["exactSourceSha256"] != audit["source"]["sha256"]
                or replay["records"] != audit["records"]
                or replay["runtime"] != audit["runtime"]
                or replay["framework"] != audit["framework"]
                or replay["workers"] != audit["workers"]
            ):
                raise ValueError(f"{gate} deterministic replay binding changed")
        elif replay != {
            "performed": False,
            "reason": "explicit diagnostic-only skip; seal is non-authorizable",
        }:
            raise ValueError(f"{gate} diagnostic replay declaration changed")
    if deep:
        replayed = _recompute_candidate_blind_selection(
            protocol, template, reproduce_sources=reproduce_sources
        )
        for key, expected_value in (
            ("forbiddenCatalogManifests", replayed["manifests"]),
            ("forbiddenPositionFiles", replayed["positions"]),
            ("forbiddenAudit", replayed["forbiddenAudit"]),
            ("pathCompatibilityEvidence", replayed["pathCompatibilityEvidence"]),
            ("currentCorpusAudit", replayed["currentCorpusAudit"]),
        ):
            if value.get(key) != expected_value:
                raise ValueError(f"compatibility suite {key} replay changed")
        for gate in GATES:
            source_expected = replayed["samplerSources"][gate]
            source_actual = dict(value["samplerSources"][gate])
            deterministic = source_actual.pop("deterministicReplay")
            if reproduce_sources:
                source_expected = dict(source_expected)
                expected_deterministic = source_expected.pop("deterministicReplay")
                if deterministic != expected_deterministic:
                    raise ValueError(f"{gate} sampler reproduction replay changed")
            if source_actual != source_expected:
                raise ValueError(f"{gate} sampler-source provenance replay changed")
            actual_suite = strict_load(
                suite_paths[gate], f"{gate} compatibility suite exact replay"
            )
            if actual_suite != replayed["expectedSuites"][gate]:
                raise ValueError(
                    f"{gate} suite differs from candidate-blind selection replay"
                )
            expected_entry = {
                **_selected_summary(replayed["selected"][gate]),
                "rejections": replayed["rejected"][gate],
                "scheduledOpeningIds": [
                    item["id"] for item in replayed["schedules"][gate]
                ],
                "identity": identity(suite_paths[gate]),
            }
            if value["suites"][gate] != expected_entry:
                raise ValueError(f"{gate} suite-selection summary replay changed")
        if value["crossSuite"] != {
            "roots": sum(
                len(replayed["selected"][gate]) for gate in GATES
            ),
            "uniqueOrbitSignatures": len(replayed["usedFresh"]),
            "mutuallyOrbitDisjoint": True,
        }:
            raise ValueError("compatibility cross-suite selection replay changed")
    return value


def _verify_runtime_suite_provenance(
    path: Path, protocol: Mapping[str, Any]
) -> dict[str, Any]:
    """Runtime authorization can never downgrade candidate-blind replay."""

    return verify_suite_seal(
        path,
        protocol,
        deep=True,
        reproduce_sources=False,
    )


def _preauthorization_state_value(
    protocol: Mapping[str, Any],
    suite_path: Path,
    suite: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify and describe the exact inventory before any authorization output."""

    root = _namespace(protocol, "root")
    sealed = _namespace(protocol, "sealed")
    expected_files = {
        _namespace(protocol, "preregistration").resolve(),
        _namespace(protocol, "suiteSeal").resolve(),
        *[path.resolve() for path in _suite_paths(protocol).values()],
    }
    actual_files: set[Path] = set()
    actual_directories: set[Path] = set()
    for item in root.rglob("*"):
        if item.is_symlink():
            raise ValueError("preauthorization compatibility namespace contains a link")
        if item.is_file():
            actual_files.add(item.resolve())
        elif item.is_dir():
            actual_directories.add(item.resolve())
        else:
            raise ValueError("preauthorization namespace contains a special entry")
    if actual_files != expected_files or actual_directories != {sealed.resolve()}:
        raise ValueError("preauthorization compatibility inventory changed")
    forbidden = [
        _namespace(protocol, "preauthorizationState"),
        _namespace(protocol, "authorization"),
        _namespace(protocol, "coreSeal"),
        *_config_paths(protocol).values(),
        *_stage_paths(protocol).values(),
        _namespace(protocol, "evidence"),
        _namespace(protocol, "decisions"),
        _namespace(protocol, "historyProjection"),
        _namespace(protocol, "historyManifest"),
        _namespace(protocol, "closure"),
    ]
    if any(path.exists() for path in forbidden):
        raise ValueError("preauthorization state contains result-bearing artifacts")
    original = _sampler_only_inventory(_template(protocol)[1])
    inventory = [identity(path) for path in sorted(expected_files, key=str)]
    return {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v5-color-compat-preauthorization-state",
        "compatibilityId": COMPAT_ID,
        "createdUtc": _utc_now(),
        "protocol": identity(PROTOCOL_PATH),
        "root": str(root),
        "allowedDirectories": [str(sealed.resolve())],
        "allowedFiles": inventory,
        "originalSamplerFiles": original,
        "suiteSeal": identity(suite_path),
        "suiteRootDigest": suite["rootDigest"],
        "authorizationAbsent": True,
        "resultsAbsent": True,
        "originalArtifactsRewritten": 0,
        "finalStageSeal": True,
    }


def verify_preauthorization_suite_state(
    protocol: Mapping[str, Any], suite_path: Path
) -> dict[str, Any]:
    """Verify the persisted exact state consumed by the v2 base projection."""

    suite_path = suite_path.resolve()
    suite = _verify_runtime_suite_provenance(suite_path, protocol)
    return _verify_preauthorization_published_state(
        protocol, suite_path, suite
    )


def _verify_preauthorization_state_record(
    path: Path,
    protocol: Mapping[str, Any],
    suite_path: Path,
    suite: Mapping[str, Any],
) -> dict[str, Any]:
    path = path.resolve()
    if path != _namespace(protocol, "preauthorizationState"):
        raise ValueError("preauthorization-state path changed")
    value = strict_load(path, "compatibility preauthorization state")
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "createdUtc",
        "protocol",
        "root",
        "allowedDirectories",
        "allowedFiles",
        "originalSamplerFiles",
        "suiteSeal",
        "suiteRootDigest",
        "authorizationAbsent",
        "resultsAbsent",
        "originalArtifactsRewritten",
        "finalStageSeal",
    }
    if set(value) != expected:
        raise ValueError("preauthorization-state fields changed")
    try:
        created = datetime.fromisoformat(
            str(value.get("createdUtc")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("preauthorization-state createdUtc is invalid") from error
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind")
        != "omega-nnue-king-state-v5-color-compat-preauthorization-state"
        or value.get("compatibilityId") != COMPAT_ID
        or created.tzinfo is None
        or created.utcoffset() != timezone.utc.utcoffset(None)
        or value.get("root") != str(_namespace(protocol, "root"))
        or value.get("allowedDirectories")
        != [str(_namespace(protocol, "sealed").resolve())]
        or value.get("suiteRootDigest") != suite.get("rootDigest")
        or value.get("authorizationAbsent") is not True
        or value.get("resultsAbsent") is not True
        or value.get("originalArtifactsRewritten") != 0
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError("preauthorization-state envelope changed")
    if not _same_identity(value.get("protocol"), identity(PROTOCOL_PATH)):
        raise ValueError("preauthorization-state protocol changed")
    if not _same_identity(value.get("suiteSeal"), identity(suite_path)):
        raise ValueError("preauthorization-state suite changed")
    expected_files = {
        _namespace(protocol, "preregistration").resolve(),
        _namespace(protocol, "suiteSeal").resolve(),
        *[item.resolve() for item in _suite_paths(protocol).values()],
    }
    expected_inventory = [
        identity(item) for item in sorted(expected_files, key=str)
    ]
    allowed = value.get("allowedFiles")
    if type(allowed) is not list or allowed != expected_inventory:
        raise ValueError("preauthorization-state allowed-file inventory changed")
    for item in allowed:
        verify_identity(item, "preauthorization allowed file")
    expected_original = _sampler_only_inventory(_template(protocol)[1])
    if value.get("originalSamplerFiles") != expected_original:
        raise ValueError("preauthorization-state original sampler inventory changed")
    for item in expected_original:
        verify_identity(item, "preauthorization original sampler")
    return value


def _verify_preauthorization_published_state(
    protocol: Mapping[str, Any],
    suite_path: Path,
    suite: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-scan the exact namespace after exclusive preauth publication."""

    path = _namespace(protocol, "preauthorizationState")
    value = _verify_preauthorization_state_record(
        path, protocol, suite_path, suite
    )
    root = _namespace(protocol, "root")
    sealed = _namespace(protocol, "sealed")
    expected_files = {
        _namespace(protocol, "preregistration").resolve(),
        _namespace(protocol, "suiteSeal").resolve(),
        path.resolve(),
        *[item.resolve() for item in _suite_paths(protocol).values()],
    }
    actual_files: set[Path] = set()
    actual_directories: set[Path] = set()
    for item in root.rglob("*"):
        if item.is_symlink():
            raise ValueError("published preauthorization namespace contains a link")
        if item.is_file():
            actual_files.add(item.resolve())
        elif item.is_dir():
            actual_directories.add(item.resolve())
        else:
            raise ValueError("published preauthorization state has a special entry")
    if actual_files != expected_files or actual_directories != {sealed.resolve()}:
        raise ValueError("published preauthorization inventory changed")
    forbidden = [
        _namespace(protocol, "authorization"),
        _namespace(protocol, "coreSeal"),
        *_config_paths(protocol).values(),
        *_stage_paths(protocol).values(),
        _namespace(protocol, "evidence"),
        _namespace(protocol, "decisions"),
        _namespace(protocol, "historyProjection"),
        _namespace(protocol, "historyManifest"),
        _namespace(protocol, "closure"),
    ]
    if any(item.exists() for item in forbidden):
        raise ValueError("published preauthorization state contains descendants")
    return value


def _seal_preauthorization(args: argparse.Namespace) -> dict[str, Any]:
    """Publish the exclusive base-projection boundary before authorization."""

    protocol = validate_protocol(args.protocol)
    suite_path = (
        _namespace(protocol, "suiteSeal")
        if args.suite_seal is None
        else args.suite_seal.resolve()
    )
    suite = _verify_runtime_suite_provenance(suite_path, protocol)
    if suite.get("authorizable") is not True:
        raise ValueError("diagnostic suite seal is not authorizable")
    _verify_preregistration(_namespace(protocol, "preregistration"), protocol)
    path = _namespace(protocol, "preauthorizationState")
    if path.exists():
        raise FileExistsError("preauthorization state already exists")
    value = _preauthorization_state_value(protocol, suite_path, suite)
    _atomic_json(path, value)
    return _verify_preauthorization_published_state(
        protocol, suite_path, suite
    )


def _expected_config(
    protocol: Mapping[str, Any],
    gate: str,
    suite: Mapping[str, Any],
    engine: Mapping[str, Any],
    network: Mapping[str, Any],
    harness: Mapping[str, Any],
    bundle: Mapping[str, Any],
    readiness: Any,
    original_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    value = readiness._expected_match_config(
        original_protocol, gate, suite, engine, network, harness, bundle
    )
    value["runId"] = f"king-state-v5-color-compat-v2-{gate}-{str(network['sha256'])[:12]}"
    value["freshnessMarker"] = (
        f"{COMPAT_ID}:{gate}:{str(network['sha256'])[:12]}"
    )
    value["outputDirectory"] = str(_stage_paths(protocol)[gate])
    return value


def _assert_authorization_document_schemas(
    core_seal: Mapping[str, Any], authorization: Mapping[str, Any]
) -> None:
    """Keep the author and verifier on one exact compat schema."""

    if set(core_seal) != CORE_SEAL_FIELDS:
        raise ValueError("authored compatibility core-seal fields changed")
    if set(authorization) != AUTHORIZATION_FIELDS:
        raise ValueError("authored compatibility authorization fields changed")
    if (
        core_seal.get("rulesParity") != authorization.get("rulesParity")
        or core_seal.get("gates") != authorization.get("gates")
        or core_seal.get("omegaMatchBundle")
        != authorization.get("omegaMatchBundle")
    ):
        raise ValueError("authored core-seal/authorization bindings disagree")


def _prospective_json_identity(
    path: Path, value: Mapping[str, Any]
) -> dict[str, Any]:
    payload = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _authorize(args: argparse.Namespace) -> dict[str, Any]:
    protocol = validate_protocol(args.protocol)
    suite_path = args.suite_seal.resolve()
    suite = _verify_runtime_suite_provenance(suite_path, protocol)
    if suite.get("authorizable") is not True:
        raise ValueError("diagnostic suite seal is not authorizable")
    _verify_preregistration(_namespace(protocol, "preregistration"), protocol)
    if any(path.exists() for path in _stage_paths(protocol).values()):
        raise FileExistsError("a compatibility match stage exists before authorization")
    outputs = [
        _namespace(protocol, "authorization"),
        _namespace(protocol, "coreSeal"),
        *_config_paths(protocol).values(),
    ]
    if any(path.exists() for path in outputs):
        raise FileExistsError("refusing to replace compatibility authorization")
    preauthorization_path = _namespace(protocol, "preauthorizationState")
    _verify_preauthorization_published_state(
        protocol, suite_path, suite
    )
    template = _template(protocol)[1]
    contract, core, readiness = _import_frozen_modules(template)
    original_protocol = contract.validate_protocol()
    readiness._install_core_profile(original_protocol)
    offline_path = args.offline_report.resolve()
    offline_before = identity(offline_path)
    report, selected = readiness._load_selected_primary(offline_path)
    if identity(offline_path) != offline_before:
        raise ValueError("offline report changed during compatibility authorization")
    network = selected["network"]
    engine = suite["engine"]
    harness = suite["omegaMatchAssembly"]
    bundle = suite["omegaMatchBundle"]
    rules_parity = require_rules_match_harness(protocol, bundle)
    if suite.get("rulesParity") != rules_parity:
        raise ValueError("suite rules parity changed before authorization")
    configs = _config_paths(protocol)
    config_values: dict[str, dict[str, Any]] = {}
    for gate in GATES:
        suite_identity = suite["suites"][gate]["identity"]
        config = _expected_config(
            protocol,
            gate,
            suite_identity,
            engine,
            network,
            harness,
            bundle,
            readiness,
            original_protocol,
        )
        config_values[gate] = config
        _atomic_json(configs[gate], config)
        core._verify_config(
            gate,
            configs[gate],
            suite_identity,
            network["sha256"],
            engine["sha256"],
            harness["sha256"],
            bundle["sha256"],
        )
    pinned = [
        identity(args.protocol),
        identity(_template(protocol)[0]),
        identity(_namespace(protocol, "preregistration")),
        identity(suite_path),
        identity(preauthorization_path),
        offline_before,
        report["selection"],
        report["robustness"],
        report["accessClaim"],
        selected["network"],
        selected["manifest"],
        suite["dotnetHost"],
        suite["dotnetRuntimeManifest"],
        suite["engine"],
        suite["omegaMatchAssembly"],
        suite["omegaMatchAppHost"],
        protocol["tools"]["readiness"],
        protocol["tools"]["matches"],
        *[suite["suites"][gate]["identity"] for gate in GATES],
        *[identity(configs[gate]) for gate in GATES],
    ]
    by_path = {
        _repo_path(item["path"], "core pin"): item for item in pinned
    }
    gates = {
        gate: {
            "suite": suite["suites"][gate]["identity"],
            "config": identity(configs[gate]),
            "runId": config_values[gate]["runId"],
            "outputDirectory": config_values[gate]["outputDirectory"],
        }
        for gate in GATES
    }
    core_seal = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v1-match-seal",
        "sealedUtc": _utc_now(),
        "generationId": COMPAT_ID,
        "protocolSha256": identity(args.protocol)["sha256"],
        "candidateNetworkSha256": network["sha256"],
        "engineExecutableSha256": engine["sha256"],
        "matchCoreSourceSha256": suite["originalAuthority"]["matchCoreSource"]["sha256"],
        "omegaMatchAssemblySha256": harness["sha256"],
        "pinnedFiles": sorted(
            by_path.values(), key=lambda item: str(item["path"]).casefold()
        ),
        "omegaMatchBundle": bundle,
        "rulesParity": rules_parity,
        "dotnetRuntimeBundle": suite["dotnetRuntimeBundle"],
        "gates": gates,
        "audit": identity(suite_path),
    }
    if set(core_seal) != CORE_SEAL_FIELDS:
        raise ValueError("authored compatibility core-seal fields changed")
    core_path = _namespace(protocol, "coreSeal")
    prospective_core = _prospective_json_identity(core_path, core_seal)
    authorization = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUTHORIZATION_KIND,
        "compatibilityId": COMPAT_ID,
        "createdUtc": _utc_now(),
        "protocol": identity(args.protocol),
        "preregistration": identity(_namespace(protocol, "preregistration")),
        "suiteSeal": identity(suite_path),
        "preauthorizationState": identity(preauthorization_path),
        "offlineReport": offline_before,
        "offlineMatchAuthorization": copy.deepcopy(report["matchAuthorization"]),
        "selectedNetwork": network,
        "selectedManifest": selected["manifest"],
        "dotnetHost": suite["dotnetHost"],
        "dotnetRuntimeManifest": suite["dotnetRuntimeManifest"],
        "dotnetRuntimeBundle": suite["dotnetRuntimeBundle"],
        "engine": engine,
        "omegaMatchAssembly": harness,
        "omegaMatchAppHost": suite["omegaMatchAppHost"],
        "omegaMatchBundle": bundle,
        "rulesParity": rules_parity,
        "coreSeal": prospective_core,
        "gates": gates,
        "stageOrder": list(GATES),
        "stageOutputsAbsentAtAuthorization": {
            gate: str(path) for gate, path in _stage_paths(protocol).items()
        },
        "runnerUpFallback": False,
        "launchOnlyThrough": protocol["execution"]["launchOnlyThrough"],
        "adapterPolicy": copy.deepcopy(protocol["adapter"]),
        "finalStageSeal": True,
    }
    _assert_authorization_document_schemas(core_seal, authorization)
    _atomic_json(core_path, core_seal)
    if identity(core_path) != prospective_core:
        raise RuntimeError("published compatibility core seal differs from preview")
    core._verify_seal(core_path)
    _atomic_json(_namespace(protocol, "authorization"), authorization)
    return verify_authorization(_namespace(protocol, "authorization"), runtime=True)


def verify_authorization(
    path: Path,
    *,
    protocol_path: Path = PROTOCOL_PATH,
    runtime: bool = True,
) -> dict[str, Any]:
    protocol = validate_protocol(protocol_path)
    path = path.resolve()
    if path != _namespace(protocol, "authorization"):
        raise ValueError("compatibility authorization path changed")
    value = strict_load(path, "compatibility authorization")
    if set(value) != AUTHORIZATION_FIELDS:
        raise ValueError("compatibility authorization field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != AUTHORIZATION_KIND
        or value.get("compatibilityId") != COMPAT_ID
        or value.get("stageOrder") != list(GATES)
        or value.get("runnerUpFallback") is not False
        or value.get("launchOnlyThrough")
        != protocol["execution"]["launchOnlyThrough"]
        or value.get("adapterPolicy") != protocol["adapter"]
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError("compatibility authorization envelope changed")
    try:
        authorization_created = datetime.fromisoformat(
            str(value.get("createdUtc")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("compatibility authorization createdUtc is invalid") from error
    if (
        authorization_created.tzinfo is None
        or authorization_created.utcoffset() != timezone.utc.utcoffset(None)
    ):
        raise ValueError("compatibility authorization createdUtc is not UTC")
    if not _same_identity(value.get("protocol"), identity(protocol_path)):
        raise ValueError("compatibility authorization protocol changed")
    _verify_preregistration(
        verify_identity(value.get("preregistration"), "authorization preregistration"),
        protocol,
    )
    # The compatibility suite and authorization live in the same local
    # namespace.  A shallow check would therefore let a forged but internally
    # consistent hand-picked suite authorize itself.
    suite = _verify_runtime_suite_provenance(
        verify_identity(value.get("suiteSeal"), "authorization suite seal"),
        protocol,
    )
    if suite.get("authorizable") is not True:
        raise ValueError("authorization cites a diagnostic-only suite seal")
    try:
        suite_created = datetime.fromisoformat(
            str(suite.get("createdUtc")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("authorization suite createdUtc is invalid") from error
    if (
        suite_created.tzinfo is None
        or suite_created.utcoffset() != timezone.utc.utcoffset(None)
        or authorization_created < suite_created
    ):
        raise ValueError("authorization predates its suite seal")
    preauthorization_path = verify_identity(
        value.get("preauthorizationState"),
        "authorization preauthorization state",
    )
    preauthorization = _verify_preauthorization_state_record(
        preauthorization_path,
        protocol,
        verify_identity(value["suiteSeal"], "preauthorization suite seal"),
        suite,
    )
    preauthorization_created = datetime.fromisoformat(
        str(preauthorization["createdUtc"]).replace("Z", "+00:00")
    )
    if (
        preauthorization_created < suite_created
        or preauthorization_created > authorization_created
    ):
        raise ValueError("authorization predates preauthorization-state evidence")
    offline_path = verify_identity(
        value.get("offlineReport"), "authorization offline report"
    )
    network = value.get("selectedNetwork")
    manifest = value.get("selectedManifest")
    verify_identity(network, "authorization selected network")
    verify_identity(manifest, "authorization selected manifest")
    for key in (
        "dotnetHost",
        "dotnetRuntimeManifest",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
    ):
        if value.get(key) != suite.get(key):
            raise ValueError(f"authorization {key} differs from suite seal")
        verify_identity(value[key], f"authorization {key}")
    if value.get("dotnetRuntimeBundle") != suite.get("dotnetRuntimeBundle"):
        raise ValueError("authorization .NET runtime bundle differs from suite seal")
    if value.get("omegaMatchBundle") != suite.get("omegaMatchBundle"):
        raise ValueError("authorization OmegaMatch bundle differs from suite seal")
    if value.get("rulesParity") != require_rules_match_harness(
        protocol, value["omegaMatchBundle"]
    ) or value.get("rulesParity") != suite.get("rulesParity"):
        raise ValueError("authorization rules parity changed")
    template = _template(protocol)[1]
    contract, core, readiness = _import_frozen_modules(template)
    original_protocol = contract.validate_protocol()
    readiness._install_core_profile(original_protocol)
    core_path = verify_identity(value.get("coreSeal"), "authorization core seal")
    if core_path != _namespace(protocol, "coreSeal"):
        raise ValueError("authorization core seal path changed")
    strict_core_seal = strict_load(core_path, "compatibility core seal")
    core_seal = core._verify_seal(core_path)
    if core_seal != strict_core_seal:
        raise ValueError("strict/frozen core-seal parses differ")
    if set(core_seal) != CORE_SEAL_FIELDS:
        raise ValueError("compatibility core-seal field inventory changed")
    try:
        core_sealed = datetime.fromisoformat(
            str(core_seal.get("sealedUtc")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("compatibility core-seal sealedUtc is invalid") from error
    if (
        core_seal.get("schemaVersion") != SCHEMA_VERSION
        or core_seal.get("kind") != "omega-nnue-king-state-v1-match-seal"
        or core_sealed.tzinfo is None
        or core_sealed.utcoffset() != timezone.utc.utcoffset(None)
        or core_sealed < preauthorization_created
        or core_sealed > authorization_created
        or core_seal.get("generationId") != COMPAT_ID
        or core_seal.get("protocolSha256") != identity(protocol_path)["sha256"]
        or core_seal.get("candidateNetworkSha256") != network["sha256"]
        or core_seal.get("engineExecutableSha256") != suite["engine"]["sha256"]
        or core_seal.get("matchCoreSourceSha256")
        != suite["originalAuthority"]["matchCoreSource"]["sha256"]
        or core_seal.get("omegaMatchAssemblySha256")
        != suite["omegaMatchAssembly"]["sha256"]
        or core_seal.get("omegaMatchBundle") != value.get("omegaMatchBundle")
        or core_seal.get("dotnetRuntimeBundle")
        != value.get("dotnetRuntimeBundle")
        or core_seal.get("rulesParity") != value.get("rulesParity")
        or core_seal.get("rulesParity") != suite.get("rulesParity")
        or not _same_identity(core_seal.get("audit"), value.get("suiteSeal"))
    ):
        raise ValueError("compatibility core seal binding changed")
    gates = value.get("gates")
    if type(gates) is not dict or set(gates) != set(GATES):
        raise ValueError("compatibility authorization gate inventory changed")
    for gate in GATES:
        if type(gates[gate]) is not dict or set(gates[gate]) != {
            "suite",
            "config",
            "runId",
            "outputDirectory",
        }:
            raise ValueError(f"{gate} authorization gate fields changed")
        expected = _expected_config(
            protocol,
            gate,
            suite["suites"][gate]["identity"],
            suite["engine"],
            network,
            suite["omegaMatchAssembly"],
            suite["omegaMatchBundle"],
            readiness,
            original_protocol,
        )
        config_path = verify_identity(gates[gate]["config"], f"{gate} config")
        if strict_load(config_path, f"{gate} config") != expected:
            raise ValueError(f"{gate} compatibility config changed")
        core._verify_config(
            gate,
            config_path,
            suite["suites"][gate]["identity"],
            network["sha256"],
            suite["engine"]["sha256"],
            suite["omegaMatchAssembly"]["sha256"],
            suite["omegaMatchBundle"]["sha256"],
        )
        expected_gate = {
            "suite": suite["suites"][gate]["identity"],
            "config": identity(config_path),
            "runId": expected["runId"],
            "outputDirectory": expected["outputDirectory"],
        }
        if gates[gate] != expected_gate:
            raise ValueError(f"{gate} authorization gate binding changed")
    if core_seal.get("gates") != gates:
        raise ValueError("core-seal gate bindings differ from authorization")
    expected_stage_paths = {
        gate: str(stage) for gate, stage in _stage_paths(protocol).items()
    }
    if value.get("stageOutputsAbsentAtAuthorization") != expected_stage_paths:
        raise ValueError("authorization stage-absence claims changed")

    # This replay is mandatory in both authoring and runtime modes.  The
    # parameter remains only for CLI/API compatibility; it may never weaken
    # the selected-winner or matchAuthorization binding.
    offline_before = identity(offline_path)
    report, selected = readiness._load_selected_primary(offline_path)
    if identity(offline_path) != offline_before:
        raise ValueError("offline report changed during authorization replay")
    if selected["network"] != network or selected["manifest"] != manifest:
        raise ValueError("offline selection differs from authorization")
    if value.get("offlineMatchAuthorization") != report.get("matchAuthorization"):
        raise ValueError("offline match authorization capsule changed")
    expected_pinned = [
        identity(protocol_path),
        identity(_template(protocol)[0]),
        identity(_namespace(protocol, "preregistration")),
        identity(verify_identity(value["suiteSeal"], "pinned suite seal")),
        identity(preauthorization_path),
        offline_before,
        report["selection"],
        report["robustness"],
        report["accessClaim"],
        selected["network"],
        selected["manifest"],
        suite["dotnetHost"],
        suite["dotnetRuntimeManifest"],
        suite["engine"],
        suite["omegaMatchAssembly"],
        suite["omegaMatchAppHost"],
        protocol["tools"]["readiness"],
        protocol["tools"]["matches"],
        *[suite["suites"][gate]["identity"] for gate in GATES],
        *[identity(Path(gates[gate]["config"]["path"])) for gate in GATES],
    ]
    expected_by_path = {
        _repo_path(item["path"], "expected core pin"): item
        for item in expected_pinned
    }
    expected_pinned = sorted(
        expected_by_path.values(), key=lambda item: str(item["path"]).casefold()
    )
    if core_seal.get("pinnedFiles") != expected_pinned:
        raise ValueError("compatibility core-seal pinned-file inventory changed")
    return value


def _self_test() -> None:
    protocol = validate_protocol()
    for non_utc in (
        "2026-07-23T12:00:00",
        "2026-07-23T05:00:00-07:00",
    ):
        try:
            _parse_utc(non_utc, "synthetic createdUtc")
        except ValueError:
            pass
        else:
            raise AssertionError("non-UTC compatibility timestamp was accepted")
    try:
        _require_utc_at_or_after(
            "2026-07-23T11:59:59Z",
            "2026-07-23T12:00:00Z",
            later_label="synthetic suite createdUtc",
            earlier_label="synthetic preregistration createdUtc",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("suite seal predating preregistration was accepted")
    replay = verify_rules_replay(protocol)
    if replay["sha256"] != protocol["rulesReplay"]["runtimeBundle"]["sha256"]:
        raise AssertionError("rules replay bundle verification changed")
    parity = {"sameBytesAndSha256": True}
    synthetic_core = {key: None for key in CORE_SEAL_FIELDS}
    synthetic_authorization = {key: None for key in AUTHORIZATION_FIELDS}
    synthetic_core.update(
        {"rulesParity": parity, "gates": {}, "omegaMatchBundle": {}}
    )
    synthetic_authorization.update(
        {"rulesParity": parity, "gates": {}, "omegaMatchBundle": {}}
    )
    _assert_authorization_document_schemas(
        synthetic_core, synthetic_authorization
    )
    missing_parity = dict(synthetic_authorization)
    missing_parity.pop("rulesParity")
    try:
        _assert_authorization_document_schemas(
            synthetic_core, missing_parity
        )
    except ValueError:
        pass
    else:
        raise AssertionError("authorization without rulesParity was authored")
    matches, producers = _classify_process_rows(
        [
            (
                4242,
                "python.exe",
                "python omega_decision_teacher_generation5.py run-shallow --jobs 4",
            )
        ]
    )
    if matches or producers != [
        {
            "name": "python.exe",
            "pid": 4242,
            "pattern": "omega_decision_teacher_generation5.py",
        }
    ]:
        raise AssertionError("between-child G5 teacher process was not detected")

    # Adversarial call-path fixture: even a core-valid/disjoint-looking local
    # suite must reach the deep candidate-blind verifier and be rejected when
    # it is not the frozen selection.
    original_suite_verifier = globals()["verify_suite_seal"]
    observed: dict[str, Any] = {}

    def reject_forged(
        path: Path,
        protocol_value: Mapping[str, Any] | None = None,
        *,
        deep: bool = True,
        reproduce_sources: bool = False,
    ) -> dict[str, Any]:
        observed.update(
            {
                "path": path.name,
                "deep": deep,
                "reproduceSources": reproduce_sources,
            }
        )
        raise ValueError("forged core-valid nonselected suite")

    globals()["verify_suite_seal"] = reject_forged
    try:
        try:
            _verify_runtime_suite_provenance(
                Path("forged-core-valid-nonselected-suite.json"), protocol
            )
        except ValueError as error:
            if "nonselected" not in str(error):
                raise
        else:
            raise AssertionError("forged nonselected suite was authorized")
    finally:
        globals()["verify_suite_seal"] = original_suite_verifier
    if observed != {
        "path": "forged-core-valid-nonselected-suite.json",
        "deep": True,
        "reproduceSources": False,
    }:
        raise AssertionError("runtime suite provenance downgraded deep replay")
    with tempfile.TemporaryDirectory(prefix="omega-g5-color-compat-selftest-") as directory:
        root = Path(directory)
        good = root / "good.bin"
        wrong = root / "wrong.bin"
        good.write_bytes(b"compatibility-identity")
        wrong.write_bytes(b"wrong-file")
        record = identity(good)
        # Exercise the canonical-equivalence predicate without allowing a
        # temporary file to escape the production repository verifier.
        relative = {
            **record,
            "path": os.path.relpath(good, REPO),
        }
        absolute = dict(record)
        if Path(relative["path"]).is_absolute() or not (
            Path(REPO / relative["path"]).resolve() == Path(absolute["path"]).resolve()
            and relative["bytes"] == absolute["bytes"]
            and relative["sha256"] == absolute["sha256"]
        ):
            raise AssertionError("relative/absolute canonical equivalence failed")
        for changed in (
            {**record, "bytes": record["bytes"] + 1},
            {**record, "sha256": "0" * 64},
            {**record, "path": str(wrong)},
        ):
            try:
                actual_path = Path(changed["path"]).resolve()
                actual = identity(actual_path)
                accepted = (
                    actual["bytes"] == changed["bytes"]
                    and actual["sha256"] == changed["sha256"]
                    and actual_path == good.resolve()
                )
            except (FileNotFoundError, ValueError):
                accepted = False
            if accepted:
                raise AssertionError("identity tamper was accepted")
        escape = {**record, "path": "../outside.bin"}
        try:
            _repo_path(escape["path"], "synthetic escape")
        except ValueError:
            pass
        else:
            raise AssertionError("repository path escape was accepted")
        exclusive = root / "exclusive.json"
        _atomic_json(exclusive, {"value": 1})
        try:
            _atomic_json(exclusive, {"value": 2})
        except FileExistsError:
            pass
        else:
            raise AssertionError("exclusive artifact was clobbered")

    # Synthetic exclusive preauthorization transition: the persisted record
    # must be observable before auth/config/core outputs, and any extra file
    # must fail the immediate post-publication namespace scan.
    with tempfile.TemporaryDirectory(
        prefix="omega-g5-compat-preauth-selftest-", dir=REPO
    ) as directory:
        root = Path(directory).resolve()

        def repo_value(path: Path) -> str:
            return path.resolve().relative_to(REPO).as_posix()

        sealed = root / "sealed"
        sealed.mkdir()
        synthetic = copy.deepcopy(protocol)
        synthetic["namespaces"] = {
            "root": repo_value(root),
            "preregistration": repo_value(root / "preregistration.json"),
            "sealed": repo_value(sealed),
            "suiteSeal": repo_value(sealed / "suite-seal.json"),
            "preauthorizationState": repo_value(
                sealed / "preauthorization-state.json"
            ),
            "authorization": repo_value(sealed / "authorization.json"),
            "coreSeal": repo_value(sealed / "core-seal.json"),
            "development": repo_value(root / "development"),
            "equalNode": repo_value(root / "equal-node"),
            "equalTime": repo_value(root / "equal-time"),
            "evidence": repo_value(root / "evidence"),
            "decisions": repo_value(root / "decisions"),
            "historyProjection": repo_value(root / "position-history.jsonl"),
            "historyManifest": repo_value(
                root / "position-history.manifest.json"
            ),
            "closure": repo_value(root / "closure.json"),
        }
        for item in (
            _namespace(synthetic, "preregistration"),
            _namespace(synthetic, "suiteSeal"),
            *_suite_paths(synthetic).values(),
        ):
            _atomic_json(item, {"synthetic": item.name})
        suite_path = _namespace(synthetic, "suiteSeal")
        suite_value = {"rootDigest": "synthetic-root-digest"}
        original_sampler_inventory = globals()["_sampler_only_inventory"]
        globals()["_sampler_only_inventory"] = lambda template: []
        try:
            observed = _preauthorization_state_value(
                synthetic, suite_path, suite_value
            )
            preauth_path = _namespace(synthetic, "preauthorizationState")
            _atomic_json(preauth_path, observed)
            _verify_preauthorization_published_state(
                synthetic, suite_path, suite_value
            )
            extra = sealed / "unbound-extra.bin"
            extra.write_bytes(b"forbidden")
            try:
                _verify_preauthorization_published_state(
                    synthetic, suite_path, suite_value
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "extra preauthorization namespace file was accepted"
                )
        finally:
            globals()["_sampler_only_inventory"] = original_sampler_inventory

    template = _template(protocol)[1]
    original_before = _sampler_only_inventory(template)
    profile = strict_load(
        verify_identity(
            template["immutableGeneration5Authority"]["profile"],
            "self-test G5 profile",
        ),
        "self-test G5 profile",
    )
    compatible, evidence = _canonical_profile_for_compat(profile)
    original_record = profile["finalFreezeIdentities"][
        "forbiddenPositionCatalogManifests"
    ][0]
    compatible_record = compatible["finalFreezeIdentities"][
        "forbiddenPositionCatalogManifests"
    ][0]
    if not _same_identity(original_record, compatible_record):
        raise AssertionError("canonical manifest identity equivalence failed")
    if evidence["filesChanged"] != 0:
        raise AssertionError("path compatibility claims a file change")
    if _sampler_only_inventory(template) != original_before:
        raise AssertionError("self-test changed a frozen sampler pool")
    foundation = verify_foundation()
    if foundation["originalNamespace"]["files"] != 9:
        raise AssertionError("foundation does not bind all nine pool files")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify-foundation")
    verify.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    prereg = commands.add_parser("preregister")
    prereg.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    seal = commands.add_parser("seal-suites")
    seal.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    seal.add_argument(
        "--skip-sampler-replay",
        action="store_true",
        help="diagnostic only; resulting suite seal cannot authorize matches",
    )
    verify_suites = commands.add_parser("verify-suites")
    verify_suites.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    verify_suites.add_argument("--suite-seal", type=Path)
    verify_suites.add_argument(
        "--require-preauthorization-state", action="store_true"
    )
    seal_preauth = commands.add_parser("seal-preauthorization")
    seal_preauth.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    seal_preauth.add_argument("--suite-seal", type=Path)
    authorize = commands.add_parser("authorize")
    authorize.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    authorize.add_argument("--suite-seal", type=Path)
    authorize.add_argument("--offline-report", type=Path, required=True)
    verify_authorize = commands.add_parser("verify-authorization")
    verify_authorize.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    verify_authorize.add_argument("--authorization", type=Path)
    verify_authorize.add_argument("--full-offline-replay", action="store_true")
    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "verify-foundation":
        value = verify_foundation(args.protocol)
        print(
            "Generation-5 compatibility foundation verified: "
            f"{value['protocol']['sha256']}"
        )
    elif args.command == "preregister":
        _preregister(args)
        print(f"Compatibility preregistration: {_namespace(validate_protocol(), 'preregistration')}")
    elif args.command == "seal-suites":
        value = _seal_suites(args)
        print(f"Compatibility suite seal: {_namespace(validate_protocol(), 'suiteSeal')}")
        print(f"Root digest: {value['rootDigest']}")
    elif args.command == "verify-suites":
        protocol = validate_protocol(args.protocol)
        path = args.suite_seal or _namespace(protocol, "suiteSeal")
        value = verify_suite_seal(path, protocol)
        if args.require_preauthorization_state:
            preauth = verify_preauthorization_suite_state(protocol, path)
            print(
                "Compatibility preauthorization inventory verified: "
                f"{len(preauth['allowedFiles'])} files"
            )
        print(f"Compatibility suites verified: {value['rootDigest']}")
    elif args.command == "seal-preauthorization":
        value = _seal_preauthorization(args)
        print(
            "Compatibility preauthorization state: "
            f"{_namespace(validate_protocol(args.protocol), 'preauthorizationState')}"
        )
        print(f"Root digest: {value['suiteRootDigest']}")
    elif args.command == "authorize":
        protocol = validate_protocol(args.protocol)
        args.suite_seal = args.suite_seal or _namespace(protocol, "suiteSeal")
        value = _authorize(args)
        print(f"Compatibility authorization: {_namespace(protocol, 'authorization')}")
        print(f"Selected network: {value['selectedNetwork']['sha256']}")
    elif args.command == "verify-authorization":
        protocol = validate_protocol(args.protocol)
        path = args.authorization or _namespace(protocol, "authorization")
        value = verify_authorization(
            path,
            protocol_path=args.protocol,
            runtime=not args.full_offline_replay,
        )
        print(f"Compatibility authorization verified: {value['selectedNetwork']['sha256']}")
    else:
        _self_test()
        print("Generation-5 color-compat readiness self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
