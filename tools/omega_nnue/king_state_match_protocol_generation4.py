#!/usr/bin/env python3
"""Shared, target-blind Generation-4 Omega match protocol support.

This module contains only protocol/runtime/suite mechanics.  In particular it
does not import the Generation-4 trainer and cannot decode a teacher,
validation, or held-out target.  The post-held-out authorization command in
``king_state_match_readiness_generation4.py`` performs that separate step.
"""

from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

import king_state_matches as core
import king_state_dotnet_runtime_generation4 as dotnet_runtime


_IMPORTED_CORE_MODULE = core
_IMPORTED_DOTNET_RUNTIME_MODULE = dotnet_runtime
_CORE_CALLABLE_BINDINGS = {
    name: getattr(core, name)
    for name in (
        "Root",
        "_assess",
        "_harness_bundle_identity",
        "_match_config",
        "_position_meta",
        "_score_for_candidate",
        "_select_roots",
        "_sequential_gate",
        "_shuffled_indices",
        "_suite",
        "_verify_config",
        "_verify_seal",
        "_verify_suite",
        "parse_ofen",
    )
}
_DOTNET_CALLABLE_BINDINGS = {
    "verify_manifest": dotnet_runtime.verify_manifest,
}


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v4-omega-decision-v1"
PROTOCOL_KIND = "omega-nnue-king-state-v4-match-protocol"
PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
GATES = ("development", "equal-node", "equal-time")
HEX_256 = re.compile(r"^[0-9a-f]{64}$")

REPO = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = (
    REPO / "validation" / "omega-nnue-king-state-v4-match-protocol.json"
)
FINAL_PROFILE = (
    REPO / "validation" / "omega-nnue-king-state-v4-preregistration.json"
)
FINAL_FREEZE = (
    REPO / "validation" / "omega-nnue-king-state-v4-freeze.seal.json"
)


def resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a canonical UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} must carry UTC timezone information")
    return parsed.astimezone(timezone.utc)


def exact_json_equal(actual: Any, expected: Any) -> bool:
    """Compare JSON values recursively without Python's bool/int coercions."""

    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return set(actual) == set(expected) and all(
            exact_json_equal(actual[key], item) for key, item in expected.items()
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            exact_json_equal(left, right) for left, right in zip(actual, expected)
        )
    if type(expected) is float and (
        not math.isfinite(actual) or not math.isfinite(expected)
    ):
        return False
    return actual == expected


def require_exact_json(actual: Any, expected: Any, label: str) -> None:
    if not exact_json_equal(actual, expected):
        raise ValueError(
            f"{label} changed or changed JSON type: "
            f"expected {expected!r}, got {actual!r}"
        )


def verify_module_binding(
    module: Any,
    imported_module: Any,
    expected_path: Path,
    label: str,
    *,
    identity_record: Mapping[str, Any] | None = None,
    callable_bindings: Mapping[str, Any] | None = None,
) -> Path:
    """Bind an exercised module object, its import slot, source path and bytes."""

    if module is not imported_module:
        raise ValueError(f"{label} module object was substituted in memory")
    name = getattr(imported_module, "__name__", None)
    if type(name) is not str or sys.modules.get(name) is not imported_module:
        raise ValueError(f"{label} import slot was substituted in memory")
    file_value = getattr(imported_module, "__file__", None)
    if type(file_value) is not str:
        raise ValueError(f"{label} module has no canonical __file__")
    actual_path = resolve(Path(file_value))
    expected = resolve(expected_path)
    if actual_path != expected:
        raise ValueError(f"{label} imported from a noncanonical path")
    if identity_record is not None:
        record = mapping(identity_record, f"{label} identity")
        if not same_identity(record, identity(actual_path)):
            raise ValueError(f"{label} imported source differs from its frozen pin")
    for binding_name, binding in (callable_bindings or {}).items():
        if getattr(imported_module, binding_name, None) is not binding:
            raise ValueError(
                f"{label}.{binding_name} was substituted in memory"
            )
        # Re-exported helpers are locked by object identity here and by their
        # owning module's separate freeze pin.  Locally defined functions must
        # additionally carry the canonical source filename in their code.
        if getattr(binding, "__module__", None) != name:
            continue
        code = getattr(binding, "__code__", None)
        code_filename = None if code is None else getattr(code, "co_filename", None)
        if code_filename is not None:
            if type(code_filename) is not str or resolve(Path(code_filename)) != expected:
                raise ValueError(
                    f"{label}.{binding_name} originates from a noncanonical source"
                )
    return actual_path


_BOUND_VERIFY_MODULE_BINDING = verify_module_binding


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def strict_load(path: Path, label: str) -> dict[str, Any]:
    path = resolve(path)
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


def mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def exact_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def exact_number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite")
    number = float(value)
    if number < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return number


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    path = resolve(path)
    stat = path.stat()
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": str(path), "bytes": stat.st_size, "sha256": sha256(path)}


def same_identity(left: Any, right: Any) -> bool:
    if type(left) is not dict or type(right) is not dict:
        return False
    try:
        left_path = Path(str(left.get("path", "")))
        right_path = Path(str(right.get("path", "")))
        if not left_path.is_absolute():
            left_path = REPO / left_path
        if not right_path.is_absolute():
            right_path = REPO / right_path
        return (
            resolve(left_path) == resolve(right_path)
            and type(left.get("bytes")) is int
            and left["bytes"] == right.get("bytes")
            and type(left.get("sha256")) is str
            and left["sha256"].lower() == str(right.get("sha256", "")).lower()
        )
    except (OSError, TypeError, ValueError):
        return False


def verify_identity(value: Any, label: str) -> Path:
    record = mapping(value, label)
    if set(record) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity fields changed")
    if (
        type(record.get("path")) is not str
        or not record["path"]
        or record["path"] != record["path"].strip()
        or type(record.get("bytes")) is not int
        or record["bytes"] < 0
        or type(record.get("sha256")) is not str
        or HEX_256.fullmatch(record["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    path = Path(record["path"])
    if not path.is_absolute():
        path = REPO / path
    path = resolve(path)
    actual = identity(path)
    if not same_identity(record, actual):
        raise ValueError(f"{label} identity changed")
    return path


def protocol_path(value: Any, label: str) -> Path:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} escapes the repository")
    resolved = resolve(REPO / path)
    try:
        resolved.relative_to(REPO)
    except ValueError as error:
        raise ValueError(f"{label} escapes the repository") from error
    return resolved


def _runtime_bundle_identity(
    app_root: Path, *, apphost_name: str, assembly_name: str
) -> dict[str, Any]:
    root = resolve(app_root)

    def included(path: Path) -> bool:
        name = path.name.casefold()
        return (
            path.suffix.casefold() in {".dll", ".so", ".dylib"}
            or name == apphost_name.casefold()
            or name.endswith(".deps.json")
            or name.endswith(".runtimeconfig.json")
        )

    files: list[dict[str, Any]] = []
    canonical = bytearray()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file() and included(item)),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        item = identity(path)
        relative = path.relative_to(root).as_posix()
        files.append(
            {
                "relativePath": relative,
                "bytes": item["bytes"],
                "sha256": item["sha256"],
            }
        )
        canonical.extend(
            f"{relative}\t{item['bytes']}\t{item['sha256']}\n".encode("utf-8")
        )
    if not any(item["relativePath"] == assembly_name for item in files):
        raise ValueError(f"runtime bundle lacks {assembly_name}")
    if not any(item["relativePath"] == apphost_name for item in files):
        raise ValueError(f"runtime bundle lacks {apphost_name}")
    return {
        "root": str(root),
        "appHostRelativePath": apphost_name,
        "assemblyRelativePath": assembly_name,
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def omega_match_bundle() -> dict[str, Any]:
    return _runtime_bundle_identity(
        REPO / "tools/omega_nnue/frozen_runtime/king-state-v4/omegamatch",
        apphost_name="OmegaMatch.exe",
        assembly_name="OmegaMatch.dll",
    )


def root_sampler_bundle() -> dict[str, Any]:
    return _runtime_bundle_identity(
        REPO / "tools/omega_nnue/frozen_runtime/king-state-v4/root-sampler",
        apphost_name="OmegaRootSampler.exe",
        assembly_name="OmegaRootSampler.dll",
    )


def _expected_stage_seeds(base: int) -> dict[str, int]:
    return {gate: base + index for index, gate in enumerate(GATES)}


def _protocol_scalar_type_schema() -> dict[tuple[Any, ...], type | None]:
    """Return the exhaustive non-string scalar schema for the frozen JSON."""

    schema: dict[tuple[Any, ...], type | None] = {}

    def add(kind: type | None, *paths: tuple[Any, ...]) -> None:
        for path in paths:
            if path in schema:
                raise AssertionError(f"duplicate protocol scalar schema path: {path}")
            schema[path] = kind

    add(
        int,
        ("schemaVersion",),
        ("authority", "requiredPostSelectionDeclaration", "matchSeed"),
        ("informationBoundary", "targetFieldsDecodedByProtocol"),
        ("informationBoundary", "matchResultsAccessedByProtocol"),
        ("suiteGeneration", "baseSeed"),
        *(("suiteGeneration", "stageSeeds", gate) for gate in GATES),
        ("suiteGeneration", "trajectoryPairsPerStage"),
        ("suiteGeneration", "independentTrajectoriesPerPair"),
        ("suiteGeneration", "workers"),
        ("suiteGeneration", "maxPlies"),
        ("suiteGeneration", "positionsPerPhaseAndSide"),
        ("suiteGeneration", "captureSelectionPercent"),
        ("pairedSchedule", "gamesPerRoot"),
        ("pairedSchedule", "balancedPairBlockSize"),
        ("pairedSchedule", "pairBudgetMustBeMultipleOf"),
        ("sequentialTest", "nullElo"),
        ("sequentialTest", "promotionEValue"),
        ("sequentialTest", "futilityEValue"),
        ("sequentialTest", "minimumPairs"),
        ("sequentialTest", "maximumPairs"),
        ("execution", "maximumConcurrentGames"),
        ("execution", "repeats"),
        ("execution", "maxPlies"),
        ("execution", "absoluteMaxPlies"),
        ("execution", "stopGraceMs"),
    )
    for key in (
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeTool",
        "matchCoreSource",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "rootSamplerAssembly",
        "rootSamplerAppHost",
        "rootSamplerRulesAssembly",
    ):
        add(int, ("runtime", key, "bytes"))
    for gate in GATES:
        add(
            int,
            ("stages", gate, "roots"),
            ("stages", gate, "rootsPerPhase"),
            ("stages", gate, "rootsPerPhaseAndSideToMove"),
            ("stages", gate, "searchTimeoutMs"),
            ("stages", gate, "initialPairBudget"),
            ("stages", gate, "resumePairBudget"),
            ("stages", gate, "maximumPairs"),
            ("stages", gate, "nodesPerMove")
            if gate != "equal-time"
            else ("stages", gate, "moveTimeMs"),
        )
        if gate != "development":
            add(
                int,
                ("stages", gate, "minimumPairsBeforeDecision"),
                ("stages", gate, "nullElo"),
                ("stages", gate, "promotionEValue"),
                ("stages", gate, "futilityEValue"),
            )
    add(
        float,
        ("stages", "development", "minimumCandidateScore"),
        ("stages", "equal-node", "promotionAnytimePValueAtMost"),
        ("stages", "equal-node", "futilityAnytimePValueAtMost"),
        ("stages", "equal-time", "promotionAnytimePValueAtMost"),
        ("stages", "equal-time", "futilityAnytimePValueAtMost"),
        *(("sequentialTest", "betFractions", index) for index in range(8)),
    )
    add(
        bool,
        (
            "authority",
            "requiredPostSelectionDeclaration",
            "rootAndSourceGameDisjointFromTrainValidationHeldOut",
        ),
        (
            "authority",
            "requiredPostSelectionDeclaration",
            "zeroIllegalMovesPvsProtocolFailuresTimeForfeitsOrAbandonedAttempts",
        ),
        (
            "authority",
            "requiredPostSelectionDeclaration",
            "formalPromotionRuleMustBePinnedBeforeMatchLaunch",
        ),
        ("informationBoundary", "createdBeforeHeldOutAccess"),
        ("informationBoundary", "suiteConstructionIsRulesOnly"),
        ("informationBoundary", "candidateIdentityUnavailableToSuiteSelection"),
        ("informationBoundary", "allSuitesSealedBeforeMatchAuthorization"),
        ("runtimeProvenance", "futureSamplerAndMatchLaunchesRequireFrozenDotnetBundle"),
        ("runtime", "sameExecutableForCandidateAndControl"),
        ("runtime", "candidate", "startupDiagnosticsRequireLoadedRecord"),
        ("runtime", "candidate", "externalAssetMustEqualSelectedNetworkSha256"),
        ("runtime", "control", "emptyOptionTransmittedExplicitly"),
        ("suiteGeneration", "candidateBlind"),
        ("pairedSchedule", "onePairFromEachPhasePerBlock"),
        ("pairedSchedule", "rootSideToMoveConstantWithinBlock"),
        ("pairedSchedule", "rootSideToMoveAlternatesBetweenBlocks"),
        ("pairedSchedule", "inverseArrangeForSeededDotNetRandomShuffle"),
        ("stages", "equal-node", "balancedBlockChecksOnly"),
        ("stages", "equal-time", "balancedBlockChecksOnly"),
        ("stages", "equal-time", "oneGameAtATime"),
        ("stages", "equal-time", "idleMachineAttestationRequired"),
        ("sequentialTest", "inspectOnlyAtCompleteFourPhaseBlocks"),
        ("sequentialTest", "preMinimumThresholdCrossingsAreForgotten"),
        ("sequentialTest", "firstEligibleThresholdCrossingLatches"),
        ("sequentialTest", "promotionRequiredAtBothEqualNodeAndEqualTime"),
        ("execution", "freshProcessPerGame"),
        ("execution", "oneGameAtATime"),
        ("execution", "zeroSafetyFailuresRequired"),
        ("execution", "launchOnlyThroughOrchestrator"),
        ("execution", "appendOnlyIntentCompletionAssessmentChain"),
        ("execution", "assessmentRequiredBetweenLaunches"),
        ("execution", "resumeRequiresExactLatestNonterminalCheckpoint"),
        ("execution", "eventLogMustBeAppendOnly"),
        ("execution", "terminalDecisionBlocksRetry"),
        ("execution", "failedOrInconclusiveStageAuthorizesSuccessor"),
        ("execution", "equalTimeIdleAttestationMustPostdateEqualNodePromotion"),
        ("execution", "equalTimeAuditRequiresSerializedNonoverlappingGameIntervals"),
        (
            "execution",
            "rehashEngineNetworkSuiteConfigHarnessAndAuthorizationBeforeAndAfterLaunch",
        ),
        ("promotionRule", "runnerUpFallback"),
        ("promotionRule", "retrySameGenerationAfterTerminalFailure"),
    )
    add(None, ("runtimeProvenance", "completedSourceProbeDotnetRuntimeBundleSha256"))
    return schema


def _assert_protocol_scalar_types(value: Mapping[str, Any]) -> None:
    expected = _protocol_scalar_type_schema()
    actual: dict[tuple[Any, ...], type | None] = {}

    def walk(item: Any, path: tuple[Any, ...] = ()) -> None:
        if type(item) is dict:
            for key, child in item.items():
                walk(child, (*path, key))
            return
        if type(item) is list:
            for index, child in enumerate(item):
                walk(child, (*path, index))
            return
        if item is None:
            actual[path] = None
        elif type(item) in {bool, int, float}:
            actual[path] = type(item)

    walk(value)
    if actual != expected:
        changed = sorted(
            (
                path,
                None if expected.get(path) is None else expected.get(path).__name__,
                None if actual.get(path) is None else actual.get(path).__name__,
            )
            for path in set(actual) | set(expected)
            if actual.get(path) is not expected.get(path)
        )
        raise ValueError(f"match protocol scalar JSON types changed: {changed!r}")


def validate_protocol(
    path: Path = PROTOCOL_PATH, *, _allow_noncanonical_for_self_test: bool = False
) -> dict[str, Any]:
    if verify_module_binding is not _BOUND_VERIFY_MODULE_BINDING:
        raise ValueError("match-protocol module-binding verifier was substituted")
    path = resolve(path)
    if not _allow_noncanonical_for_self_test and path != resolve(PROTOCOL_PATH):
        raise ValueError(
            "Generation-4 match protocol must be the canonical frozen protocol"
        )
    value = strict_load(path, "Generation-4 match protocol")
    _assert_protocol_scalar_types(value)
    expected_top = {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "authority",
        "informationBoundary",
        "runtimeProvenance",
        "namespaces",
        "runtime",
        "suiteGeneration",
        "pairedSchedule",
        "stages",
        "sequentialTest",
        "execution",
        "promotionRule",
    }
    if set(value) != expected_top:
        raise ValueError("Generation-4 match protocol field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != PROTOCOL_KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("status")
        != "target-blind protocol frozen before any generation-4 held-out access"
    ):
        raise ValueError("Generation-4 match protocol envelope changed")

    boundary = mapping(value.get("informationBoundary"), "information boundary")
    if boundary != {
        "createdBeforeHeldOutAccess": True,
        "targetFieldsDecodedByProtocol": 0,
        "matchResultsAccessedByProtocol": 0,
        "suiteConstructionIsRulesOnly": True,
        "candidateIdentityUnavailableToSuiteSelection": True,
        "allSuitesSealedBeforeMatchAuthorization": True,
    }:
        raise ValueError("match protocol information boundary changed")

    provenance = mapping(value.get("runtimeProvenance"), "runtime provenance")
    if provenance != {
        "completedSourceProbeFrameworkObserved": ".NET 10.0.9",
        "completedSourceProbeDotnetRuntimeBundleSha256": None,
        "completedSourceProbeLimitation": (
            "the already completed source probe recorded the framework version "
            "but did not seal a complete .NET runtime inventory; it is not regenerated"
        ),
        "futureSamplerAndMatchLaunchesRequireFrozenDotnetBundle": True,
    }:
        raise ValueError("match runtime-provenance declaration changed")

    authority = mapping(value.get("authority"), "match authority")
    if set(authority) != {
        "preregistrationTemplate",
        "finalPreregistration",
        "requiredPostSelectionDeclaration",
    }:
        raise ValueError("match authority fields changed")
    if protocol_path(authority["preregistrationTemplate"], "template") != resolve(
        REPO / "validation/omega-nnue-king-state-v4-preregistration.template.json"
    ) or protocol_path(authority["finalPreregistration"], "profile") != FINAL_PROFILE:
        raise ValueError("match authority paths changed")
    required_matches = mapping(
        authority.get("requiredPostSelectionDeclaration"),
        "required match declaration",
    )
    if required_matches != {
        "rootAndSourceGameDisjointFromTrainValidationHeldOut": True,
        "matchSeed": 2026072208,
        "development": "paired fixed-root safety gate",
        "equalNode": "paired sequential strength gate",
        "equalTime": "paired serialized practical-strength gate",
        "zeroIllegalMovesPvsProtocolFailuresTimeForfeitsOrAbandonedAttempts": True,
        "formalPromotionRuleMustBePinnedBeforeMatchLaunch": True,
    }:
        raise ValueError("required preregistered match declaration changed")

    namespaces = mapping(value.get("namespaces"), "match namespaces")
    expected_namespace_keys = {
        "root",
        "sampler",
        "sealed",
        "suiteSeal",
        "authorization",
        "coreSeal",
        "development",
        "equalNode",
        "equalTime",
    }
    if set(namespaces) != expected_namespace_keys:
        raise ValueError("match namespace inventory changed")
    resolved_namespaces = {
        key: protocol_path(item, f"namespace {key}")
        for key, item in namespaces.items()
    }
    root = resolved_namespaces["root"]
    if any(
        path != root and root not in path.parents
        for path in resolved_namespaces.values()
    ):
        raise ValueError("a match namespace escapes its root")
    if len(set(resolved_namespaces.values())) != len(resolved_namespaces):
        raise ValueError("match namespaces overlap exactly")

    runtime = mapping(value.get("runtime"), "match runtime")
    if set(runtime) != {
        "sameExecutableForCandidateAndControl",
        "dotnetRuntimeVersion",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeTool",
        "dotnetRuntimeBundleSha256",
        "matchCoreSource",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "omegaMatchBundleSha256",
        "rootSamplerAssembly",
        "rootSamplerAppHost",
        "rootSamplerBundleSha256",
        "rootSamplerRulesAssembly",
        "commonEngineOptions",
        "candidate",
        "control",
    }:
        raise ValueError("match runtime field inventory changed")
    runtime_identity_keys = {
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeTool",
        "matchCoreSource",
        "engine",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "rootSamplerAssembly",
        "rootSamplerAppHost",
        "rootSamplerRulesAssembly",
    }
    expected_runtime_paths = {
        "dotnetHost": "tools/omega_nnue/frozen_runtime/king-state-v4/dotnet-runtime/dotnet.exe",
        "dotnetRuntimeManifest": "tools/omega_nnue/frozen_runtime/king-state-v4/dotnet-runtime.manifest.json",
        "dotnetRuntimeTool": "tools/omega_nnue/king_state_dotnet_runtime_generation4.py",
        "matchCoreSource": "tools/omega_nnue/king_state_matches.py",
        "engine": "tools/omega_nnue/frozen_runtime/king-state-v4/engine/senpai.exe",
        "omegaMatchAssembly": "tools/omega_nnue/frozen_runtime/king-state-v4/omegamatch/OmegaMatch.dll",
        "omegaMatchAppHost": "tools/omega_nnue/frozen_runtime/king-state-v4/omegamatch/OmegaMatch.exe",
        "rootSamplerAssembly": "tools/omega_nnue/frozen_runtime/king-state-v4/root-sampler/OmegaRootSampler.dll",
        "rootSamplerAppHost": "tools/omega_nnue/frozen_runtime/king-state-v4/root-sampler/OmegaRootSampler.exe",
        "rootSamplerRulesAssembly": "tools/omega_nnue/frozen_runtime/king-state-v4/root-sampler/ChessLib.dll",
    }
    for key in runtime_identity_keys:
        record = mapping(runtime.get(key), f"runtime {key}")
        if set(record) != {"path", "bytes", "sha256"}:
            raise ValueError(f"runtime {key} identity fields changed")
        expected_path = protocol_path(record["path"], f"runtime {key} path")
        if expected_path != resolve(REPO / expected_runtime_paths[key]):
            raise ValueError(f"runtime {key} escaped its canonical frozen path")
        actual = identity(expected_path)
        if actual["bytes"] != record["bytes"] or actual["sha256"] != record["sha256"]:
            raise ValueError(f"runtime {key} differs from its protocol pin")
    _BOUND_VERIFY_MODULE_BINDING(
        core,
        _IMPORTED_CORE_MODULE,
        REPO / expected_runtime_paths["matchCoreSource"],
        "shared match core",
        identity_record=runtime["matchCoreSource"],
        callable_bindings=_CORE_CALLABLE_BINDINGS,
    )
    _BOUND_VERIFY_MODULE_BINDING(
        dotnet_runtime,
        _IMPORTED_DOTNET_RUNTIME_MODULE,
        REPO / expected_runtime_paths["dotnetRuntimeTool"],
        "Generation-4 .NET runtime verifier",
        identity_record=runtime["dotnetRuntimeTool"],
        callable_bindings=_DOTNET_CALLABLE_BINDINGS,
    )
    if omega_match_bundle()["sha256"] != runtime.get("omegaMatchBundleSha256"):
        raise ValueError("OmegaMatch bundle differs from protocol pin")
    if root_sampler_bundle()["sha256"] != runtime.get("rootSamplerBundleSha256"):
        raise ValueError("root-sampler bundle differs from protocol pin")
    dotnet_bundle = dotnet_runtime.verify_manifest(
        protocol_path(
            runtime["dotnetRuntimeManifest"]["path"],
            "runtime dotnet manifest path",
        )
    )
    if (
        runtime.get("dotnetRuntimeVersion") != dotnet_runtime.RUNTIME_VERSION
        or dotnet_bundle.get("bundleSha256")
        != runtime.get("dotnetRuntimeBundleSha256")
        or dotnet_bundle.get("runtimeVersion") != runtime.get("dotnetRuntimeVersion")
        or protocol_path(dotnet_bundle["root"], "dotnet runtime root")
        != resolve(dotnet_runtime.RUNTIME_ROOT)
    ):
        raise ValueError(".NET runtime bundle differs from protocol pin")
    if runtime.get("sameExecutableForCandidateAndControl") is not True:
        raise ValueError("candidate/control executable policy changed")
    if runtime.get("commonEngineOptions") != {
        "Threads": "1",
        "Hash": "128",
        "Ponder": "false",
        "OwnBook": "false",
        "UCI_Chess960": "false",
        "UCI_Variant": "omega",
    }:
        raise ValueError("common engine options changed")
    candidate = mapping(runtime.get("candidate"), "candidate runtime")
    control = mapping(runtime.get("control"), "control runtime")
    if set(candidate) != {
        "UseOmegaNNUE",
        "OmegaNNUEFile",
        "startupDiagnosticsRequireLoadedRecord",
        "finalStartupDiagnostic",
        "externalAssetMustEqualSelectedNetworkSha256",
    } or set(control) != {
        "UseOmegaNNUE",
        "OmegaNNUEFile",
        "emptyOptionTransmittedExplicitly",
        "externalAssets",
        "finalStartupDiagnostic",
    }:
        raise ValueError("candidate/control runtime field inventory changed")
    if candidate != {
        "UseOmegaNNUE": "true",
        "OmegaNNUEFile": (
            "exact selected-primary network path and SHA-256 from the passed "
            "one-time offline report"
        ),
        "startupDiagnosticsRequireLoadedRecord": True,
        "finalStartupDiagnostic": "info string Omega NNUE evaluation active",
        "externalAssetMustEqualSelectedNetworkSha256": True,
    } or control != {
        "UseOmegaNNUE": "false",
        "OmegaNNUEFile": "<empty>",
        "emptyOptionTransmittedExplicitly": True,
        "externalAssets": [],
        "finalStartupDiagnostic": (
            "info string Omega NNUE disabled; handcrafted evaluation active"
        ),
    }:
        raise ValueError("candidate/control activation contract changed")

    sampler = mapping(value.get("suiteGeneration"), "suite generation")
    if set(sampler) != {
        "baseSeed",
        "stageSeedDerivation",
        "stageSeeds",
        "deterministicPrng",
        "trajectoryPairsPerStage",
        "independentTrajectoriesPerPair",
        "workers",
        "maxPlies",
        "positionsPerPhaseAndSide",
        "captureSelectionPercent",
        "phaseOrder",
        "sideOrder",
        "forbiddenOrbitPolicy",
        "crossSuitePolicy",
        "selectionOrder",
        "candidateBlind",
    }:
        raise ValueError("suite-generation field inventory changed")
    base = exact_int(sampler.get("baseSeed"), "suite base seed", minimum=1)
    if sampler.get("stageSeeds") != _expected_stage_seeds(base):
        raise ValueError("stage seed derivation changed")
    if (
        base != 2026072208
        or sampler.get("phaseOrder") != list(PHASES)
        or sampler.get("sideOrder") != list(SIDES)
        or sampler.get("candidateBlind") is not True
        or sampler.get("deterministicPrng") != "SplitMix64"
        or sampler.get("trajectoryPairsPerStage") != 8192
        or sampler.get("independentTrajectoriesPerPair") != 2
        or sampler.get("workers") != 4
        or sampler.get("maxPlies") != 220
        or sampler.get("positionsPerPhaseAndSide") != 2
        or sampler.get("captureSelectionPercent") != 72
        or sampler.get("stageSeedDerivation")
        != "baseSeed + zero-based stage ordinal in development,equal-node,equal-time order"
        or sampler.get("forbiddenOrbitPolicy")
        != "exclude every symmetry-orbit signature occurring in any final-freeze target-opaque forbidden-position catalog"
        or sampler.get("crossSuitePolicy")
        != "all selected root symmetry-orbit signatures are mutually disjoint"
        or sampler.get("selectionOrder")
        != "SHA-256 king-state-match-root-v1 rank within exact phase/side buckets"
    ):
        raise ValueError("suite-generation degrees changed")

    paired = mapping(value.get("pairedSchedule"), "paired schedule")
    if set(paired) != {
        "gamesPerRoot",
        "roles",
        "engineA",
        "engineB",
        "candidatePairScore",
        "balancedPairBlockSize",
        "onePairFromEachPhasePerBlock",
        "rootSideToMoveConstantWithinBlock",
        "rootSideToMoveAlternatesBetweenBlocks",
        "inverseArrangeForSeededDotNetRandomShuffle",
        "pairBudgetMustBeMultipleOf",
    }:
        raise ValueError("paired schedule field inventory changed")
    if (
        paired.get("gamesPerRoot") != 2
        or paired.get("engineA") != "nnue-candidate"
        or paired.get("engineB") != "hce-control"
        or paired.get("balancedPairBlockSize") != len(PHASES)
        or paired.get("onePairFromEachPhasePerBlock") is not True
        or paired.get("rootSideToMoveConstantWithinBlock") is not True
        or paired.get("rootSideToMoveAlternatesBetweenBlocks") is not True
        or paired.get("inverseArrangeForSeededDotNetRandomShuffle") is not True
        or paired.get("pairBudgetMustBeMultipleOf") != len(PHASES)
        or paired.get("candidatePairScore")
        != "mean of the candidate's two colour-swapped game scores"
    ):
        raise ValueError("paired-colour schedule changed")
    if paired.get("roles") != [
        {"gameSuffix": "ab", "white": "engineA", "black": "engineB"},
        {"gameSuffix": "ba", "white": "engineB", "black": "engineA"},
    ]:
        raise ValueError("paired-colour role swap changed")

    stages = mapping(value.get("stages"), "match stages")
    if set(stages) != set(GATES):
        raise ValueError("match stage inventory changed")
    expected = {
        "development": (64, 16, 8, "nodes", 30_000, 60_000, 64, 4),
        "equal-node": (512, 128, 64, "nodes", 60_000, 90_000, 128, 4),
        "equal-time": (512, 128, 64, "moveTime", 1_000, 5_000, 128, 4),
    }
    for gate in GATES:
        stage = mapping(stages.get(gate), f"{gate} stage")
        common_stage_fields = {
            "roots",
            "rootsPerPhase",
            "rootsPerPhaseAndSideToMove",
            "mode",
            "searchTimeoutMs",
            "initialPairBudget",
            "resumePairBudget",
            "maximumPairs",
        }
        expected_stage_fields = (
            common_stage_fields
            | {"nodesPerMove", "minimumCandidateScore", "decision"}
            if gate == "development"
            else common_stage_fields
            | {
                "minimumPairsBeforeDecision",
                "nullElo",
                "promotionEValue",
                "futilityEValue",
                "promotionAnytimePValueAtMost",
                "futilityAnytimePValueAtMost",
                "balancedBlockChecksOnly",
            }
            | ({"moveTimeMs", "oneGameAtATime", "idleMachineAttestationRequired"} if gate == "equal-time" else {"nodesPerMove"})
        )
        if set(stage) != expected_stage_fields:
            raise ValueError(f"{gate} stage field inventory changed")
        (
            roots,
            per_phase,
            per_side,
            mode,
            search_budget,
            search_timeout,
            initial,
            resume,
        ) = expected[gate]
        budget_key = "moveTimeMs" if gate == "equal-time" else "nodesPerMove"
        if (
            stage.get("roots") != roots
            or stage.get("rootsPerPhase") != per_phase
            or stage.get("rootsPerPhaseAndSideToMove") != per_side
            or stage.get("mode") != mode
            or stage.get(budget_key) != search_budget
            or stage.get("searchTimeoutMs") != search_timeout
            or stage.get("initialPairBudget") != initial
            or stage.get("resumePairBudget") != resume
            or stage.get("maximumPairs") != roots
            or initial % len(PHASES)
            or resume % len(PHASES)
        ):
            raise ValueError(f"{gate} frozen budgets changed")
        if gate != "development" and (
            stage.get("minimumPairsBeforeDecision") != 128
            or stage.get("nullElo") != 15
            or stage.get("promotionEValue") != 100
            or stage.get("futilityEValue") != 20
            or stage.get("promotionAnytimePValueAtMost") != 0.01
            or stage.get("futilityAnytimePValueAtMost") != 0.05
            or stage.get("balancedBlockChecksOnly") is not True
        ):
            raise ValueError(f"{gate} sequential thresholds changed")
    if (
        stages["development"].get("minimumCandidateScore") != 0.5
        or stages["development"].get("decision")
        != (
            "pass only after all 64 complete pairs, no in-progress attempt, "
            "zero safety failures, and candidateScore >= 0.5"
        )
    ):
        raise ValueError("development score floor changed")
    if (
        stages["equal-time"].get("oneGameAtATime") is not True
        or stages["equal-time"].get("idleMachineAttestationRequired") is not True
    ):
        raise ValueError("equal-time serialization contract changed")

    sequential = mapping(value.get("sequentialTest"), "sequential test")
    if set(sequential) != {
        "method",
        "nullElo",
        "nullScoreFormula",
        "betFractions",
        "promotionEValue",
        "futilityEValue",
        "minimumPairs",
        "maximumPairs",
        "inspectOnlyAtCompleteFourPhaseBlocks",
        "preMinimumThresholdCrossingsAreForgotten",
        "firstEligibleThresholdCrossingLatches",
        "maximumPairsWithoutSignal",
        "promotionRequiredAtBothEqualNodeAndEqualTime",
    }:
        raise ValueError("sequential-test field inventory changed")
    if (
        sequential.get("method") != "mixture e-process over bounded paired scores"
        or sequential.get("nullElo") != 15
        or sequential.get("nullScoreFormula")
        != "1 / (1 + 10^(-nullElo/400))"
        or sequential.get("promotionEValue") != 100
        or sequential.get("futilityEValue") != 20
        or sequential.get("minimumPairs") != 128
        or sequential.get("maximumPairs") != 512
        or sequential.get("betFractions")
        != [1 / 128, 1 / 64, 1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 3 / 4]
        or sequential.get("inspectOnlyAtCompleteFourPhaseBlocks") is not True
        or sequential.get("preMinimumThresholdCrossingsAreForgotten") is not True
        or sequential.get("firstEligibleThresholdCrossingLatches") is not True
        or sequential.get("maximumPairsWithoutSignal") != "inconclusive"
        or sequential.get("promotionRequiredAtBothEqualNodeAndEqualTime") is not True
    ):
        raise ValueError("sequential test changed")

    execution = mapping(value.get("execution"), "execution contract")
    if set(execution) != {
        "gateOrder",
        "freshProcessPerGame",
        "oneGameAtATime",
        "maximumConcurrentGames",
        "repeats",
        "maxPlies",
        "absoluteMaxPlies",
        "stopGraceMs",
        "zeroSafetyFailuresRequired",
        "safetyFields",
        "launchOnlyThroughOrchestrator",
        "appendOnlyIntentCompletionAssessmentChain",
        "assessmentRequiredBetweenLaunches",
        "resumeRequiresExactLatestNonterminalCheckpoint",
        "eventLogMustBeAppendOnly",
        "terminalDecisionBlocksRetry",
        "failedOrInconclusiveStageAuthorizesSuccessor",
        "equalTimeIdleAttestationMustPostdateEqualNodePromotion",
        "equalTimeAuditRequiresSerializedNonoverlappingGameIntervals",
        "rehashEngineNetworkSuiteConfigHarnessAndAuthorizationBeforeAndAfterLaunch",
    }:
        raise ValueError("execution field inventory changed")
    if (
        execution.get("gateOrder") != list(GATES)
        or execution.get("freshProcessPerGame") is not True
        or execution.get("oneGameAtATime") is not True
        or execution.get("maximumConcurrentGames") != 1
        or execution.get("repeats") != 1
        or execution.get("maxPlies") != 300
        or execution.get("absoluteMaxPlies") != 400
        or execution.get("stopGraceMs") != 2000
        or execution.get("zeroSafetyFailuresRequired") is not True
        or execution.get("safetyFields")
        != [
            "illegalMoves",
            "illegalPvs",
            "protocolFailures",
            "timeForfeits",
            "abandonedAttempts",
        ]
        or execution.get("launchOnlyThroughOrchestrator") is not True
        or execution.get("appendOnlyIntentCompletionAssessmentChain") is not True
        or execution.get("assessmentRequiredBetweenLaunches") is not True
        or execution.get("resumeRequiresExactLatestNonterminalCheckpoint") is not True
        or execution.get("eventLogMustBeAppendOnly") is not True
        or execution.get("terminalDecisionBlocksRetry") is not True
        or execution.get("failedOrInconclusiveStageAuthorizesSuccessor") is not False
        or execution.get("equalTimeIdleAttestationMustPostdateEqualNodePromotion") is not True
        or execution.get("equalTimeAuditRequiresSerializedNonoverlappingGameIntervals")
        is not True
        or execution.get("rehashEngineNetworkSuiteConfigHarnessAndAuthorizationBeforeAndAfterLaunch") is not True
    ):
        raise ValueError("execution safety contract changed")
    promotion = mapping(value.get("promotionRule"), "promotion rule")
    if set(promotion) != {
        "clearlySuperior",
        "runnerUpFallback",
        "retrySameGenerationAfterTerminalFailure",
        "nextActionAfterFailure",
    }:
        raise ValueError("promotion-rule field inventory changed")
    if promotion != {
        "clearlySuperior": (
            "development pass followed by promotion against +15 Elo null at "
            "anytime p <= 0.01 in both equal-node and equal-time, with zero "
            "safety failures"
        ),
        "runnerUpFallback": False,
        "retrySameGenerationAfterTerminalFailure": False,
        "nextActionAfterFailure": (
            "close this generation and create a fresh preregistered generation"
        ),
    }:
        raise ValueError("promotion/failure policy changed")
    return value


def namespace(protocol: Mapping[str, Any], key: str) -> Path:
    return protocol_path(
        mapping(protocol.get("namespaces"), "match namespaces").get(key),
        f"match namespace {key}",
    )


def gate_specs(protocol: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    stages = mapping(protocol.get("stages"), "match stages")
    seeds = mapping(
        mapping(protocol.get("suiteGeneration"), "suite generation").get(
            "stageSeeds"
        ),
        "stage seeds",
    )
    result: dict[str, dict[str, Any]] = {}
    for gate in GATES:
        stage = mapping(stages.get(gate), f"{gate} stage")
        item: dict[str, Any] = {
            "seed": seeds[gate],
            "roots": stage["roots"],
            "rootsPerPhase": stage["rootsPerPhase"],
            "rootsPerPhaseSide": stage["rootsPerPhaseAndSideToMove"],
            "mode": stage["mode"],
            "searchTimeoutMs": stage["searchTimeoutMs"],
        }
        if gate == "equal-time":
            item["moveTimeMs"] = stage["moveTimeMs"]
        else:
            item["nodes"] = stage["nodesPerMove"]
        if gate == "development":
            item["minimumCandidateScore"] = stage["minimumCandidateScore"]
        result[gate] = item
    return result


def install_core_profile(protocol: Mapping[str, Any]) -> None:
    """Install the frozen G4 constants into the exercised match core."""

    sampler = mapping(protocol.get("suiteGeneration"), "suite generation")
    sequential = mapping(protocol.get("sequentialTest"), "sequential test")
    execution = mapping(protocol.get("execution"), "execution")
    runtime = mapping(protocol.get("runtime"), "runtime")
    core.GATE_SPECS = gate_specs(protocol)
    core.SAMPLER_TRAJECTORY_PAIRS = sampler["trajectoryPairsPerStage"]
    core.SAMPLER_MAX_PLIES = sampler["maxPlies"]
    core.SAMPLER_POSITIONS_PER_PHASE_SIDE = sampler["positionsPerPhaseAndSide"]
    core.SAMPLER_CAPTURE_PERCENT = sampler["captureSelectionPercent"]
    core.MAX_PLIES = execution["maxPlies"]
    core.ABSOLUTE_MAX_PLIES = execution["absoluteMaxPlies"]
    core.STOP_GRACE_MS = execution["stopGraceMs"]
    core.MINIMUM_GATE_PAIRS = sequential["minimumPairs"]
    core.MAXIMUM_GATE_PAIRS = sequential["maximumPairs"]
    core.NULL_ELO = float(sequential["nullElo"])
    core.PROMOTION_ALPHA = 1.0 / float(sequential["promotionEValue"])
    core.FUTILITY_BETA = 1.0 / float(sequential["futilityEValue"])
    core.PROMOTION_E_VALUE = float(sequential["promotionEValue"])
    core.FUTILITY_E_VALUE = float(sequential["futilityEValue"])
    core.BET_FRACTIONS = tuple(float(item) for item in sequential["betFractions"])
    core.ENGINE_OPTIONS = dict(runtime["commonEngineOptions"])
    core.SOURCE_NAMES = {
        gate: f"king-state-v4-{gate}-rules-only.jsonl" for gate in GATES
    }


def strict_identity_list(values: Any, label: str) -> list[dict[str, Any]]:
    if type(values) is not list or not values:
        raise ValueError(f"{label} must be a nonempty identity list")
    result: list[dict[str, Any]] = []
    paths: set[Path] = set()
    for index, value in enumerate(values, 1):
        path = verify_identity(value, f"{label} {index}")
        if path in paths:
            raise ValueError(f"{label} repeats {path}")
        paths.add(path)
        result.append(dict(value))
    return result


def nested_identities(value: Any) -> Iterable[dict[str, Any]]:
    if type(value) is dict:
        if set(value) == {"path", "bytes", "sha256"}:
            yield value
        for item in value.values():
            yield from nested_identities(item)
    elif type(value) is list:
        for item in value:
            yield from nested_identities(item)


def root_digest(suites: Mapping[str, Path]) -> str:
    canonical = bytearray()
    for gate in GATES:
        value = strict_load(suites[gate], f"{gate} match suite")
        openings = value.get("openings")
        if type(openings) is not list:
            raise ValueError(f"{gate} suite lacks openings")
        for opening in openings:
            item = mapping(opening, f"{gate} opening")
            metadata = mapping(item.get("kingStateMatch"), f"{gate} metadata")
            canonical.extend(
                (
                    f"{gate}\t{item.get('id')}\t{metadata.get('phase')}\t"
                    f"{metadata.get('rootSideToMove')}\t"
                    f"{metadata.get('identityKey')}\t"
                    f"{metadata.get('symmetryOrbitKey')}\n"
                ).encode("utf-8")
            )
    return hashlib.sha256(canonical).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any], *, exclusive: bool) -> None:
    path = resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    if exclusive:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
        return
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def self_test() -> None:
    protocol = validate_protocol()
    install_core_profile(protocol)
    if core.GATE_SPECS != gate_specs(protocol):
        raise AssertionError("core profile installation changed")
    if core._sequential_gate(
        [(f"p{index}", 1.0) for index in range(1, 129)]
    )["decision"] != "promote":
        raise AssertionError("decisive synthetic strength did not promote")
    if core._sequential_gate(
        [(f"p{index}", 0.0) for index in range(1, 129)]
    )["decision"] != "futility":
        raise AssertionError("decisive synthetic weakness did not stop")
    early = core._sequential_gate(
        [(f"p{index}", 1.0) for index in range(1, 125)]
    )
    if early["decision"] != "continue" or early["signalPair"] is not None:
        raise AssertionError("pre-minimum evidence leaked into a decision")
    if omega_match_bundle()["sha256"] != protocol["runtime"][
        "omegaMatchBundleSha256"
    ]:
        raise AssertionError("OmegaMatch bundle self-test changed")
    if root_sampler_bundle()["sha256"] != protocol["runtime"][
        "rootSamplerBundleSha256"
    ]:
        raise AssertionError("root-sampler bundle self-test changed")
    with tempfile.TemporaryDirectory(prefix="omega-g4-protocol-") as directory:
        path = Path(directory) / "protocol.json"
        atomic_json(path, protocol, exclusive=True)
        try:
            validate_protocol(path)
        except ValueError:
            pass
        else:
            raise AssertionError("noncanonical protocol path was accepted")
        mutations = (
            (
                "equal-node budget",
                lambda value: value["stages"]["equal-node"].__setitem__(
                    "maximumPairs", 128
                ),
            ),
            (
                "promotion threshold",
                lambda value: value["sequentialTest"].__setitem__(
                    "promotionEValue", 20
                ),
            ),
            (
                "paired colors",
                lambda value: value["pairedSchedule"]["roles"][1].__setitem__(
                    "white", "engineA"
                ),
            ),
            (
                "runtime hash",
                lambda value: value["runtime"]["engine"].__setitem__(
                    "sha256", "0" * 64
                ),
            ),
            (
                "shared match core",
                lambda value: value["runtime"]["matchCoreSource"].__setitem__(
                    "sha256", "0" * 64
                ),
            ),
            (
                "dotnet runtime bundle",
                lambda value: value["runtime"].__setitem__(
                    "dotnetRuntimeBundleSha256", "0" * 64
                ),
            ),
            (
                "assessment interlock",
                lambda value: value["execution"].__setitem__(
                    "assessmentRequiredBetweenLaunches", False
                ),
            ),
            (
                "candidate diagnostic",
                lambda value: value["runtime"]["candidate"].__setitem__(
                    "finalStartupDiagnostic", "changed"
                ),
            ),
            (
                "unregistered execution field",
                lambda value: value["execution"].__setitem__(
                    "unregistered", True
                ),
            ),
            (
                "search timeout",
                lambda value: value["stages"]["equal-time"].__setitem__(
                    "searchTimeoutMs", 10_000
                ),
            ),
            (
                "maximum plies",
                lambda value: value["execution"].__setitem__(
                    "absoluteMaxPlies", 500
                ),
            ),
            (
                "integer root count changed to float",
                lambda value: value["stages"]["development"].__setitem__(
                    "roots", 64.0
                ),
            ),
            (
                "integer seed changed to float",
                lambda value: value["suiteGeneration"].__setitem__(
                    "baseSeed", 2026072208.0
                ),
            ),
            (
                "integer seed changed to bool",
                lambda value: value["suiteGeneration"].__setitem__(
                    "baseSeed", True
                ),
            ),
            (
                "float threshold changed to int",
                lambda value: value["stages"]["development"].__setitem__(
                    "minimumCandidateScore", 0
                ),
            ),
            (
                "boolean changed to integer",
                lambda value: value["execution"].__setitem__(
                    "maximumConcurrentGames", True
                ),
            ),
        )
        for label, mutate in mutations:
            changed = copy.deepcopy(protocol)
            mutate(changed)
            atomic_json(path, changed, exclusive=False)
            try:
                validate_protocol(path, _allow_noncanonical_for_self_test=True)
            except ValueError:
                pass
            else:
                raise AssertionError(f"protocol accepted changed {label}")
    original_core = core
    try:
        globals()["core"] = object()
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("in-memory shared-core substitution was accepted")
    finally:
        globals()["core"] = original_core
    original_callable = core._sequential_gate
    try:
        core._sequential_gate = lambda observations: {"decision": "promote"}
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("in-memory shared-core callable substitution was accepted")
    finally:
        core._sequential_gate = original_callable
    original_binding_verifier = verify_module_binding
    try:
        globals()["verify_module_binding"] = lambda *args, **kwargs: None
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError(
                "in-memory module-binding verifier substitution was accepted"
            )
    finally:
        globals()["verify_module_binding"] = original_binding_verifier


if __name__ == "__main__":
    self_test()
    print("Generation-4 match protocol self-test passed")
