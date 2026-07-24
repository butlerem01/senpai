#!/usr/bin/env python3
"""Authenticate immutable pre-match Generation-5 terminal outcomes.

This authority is additive.  It descriptor-loads the already-frozen G5
trainer and never changes its profile, freeze, source, or normal match path.
Only this module's ``run-offline`` command may start the one-time held-out
evaluation: an immutable lifetime claim and an OS lock then make a post-claim
interruption distinguishable from a live evaluator.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from types import ModuleType
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
PROTOCOL_ID = "king-state-v5-early-terminal-v1"
PROFILE_ID = "king-state-v5-omega-decision-v2"
PROTOCOL_KIND = "omega-nnue-king-state-v5-early-terminal-protocol"
SOURCE_SELECTION_KIND = "omega-nnue-king-state-v5-early-terminal-selection"
SOURCE_CLOSURE_KIND = "omega-nnue-king-state-v5-early-terminal-closure"
LIFETIME_CLAIM_KIND = "omega-nnue-king-state-v5-offline-evaluation-lifetime-claim"
SOURCE_CLOSURE_STATUS = "terminal-generation-5-pre-match-closure"
# Updated only after the one-way protocol -> implementation boundary is frozen.
# The protocol never embeds this module's identity, so this is not a hash cycle.
PROTOCOL_BYTES = 7_991
PROTOCOL_SHA256 = "ae0852673fe1b9e54a0bda182219f4268278252dd2aa9fb067b882a1e69ce352"
HEX64 = re.compile(r"[0-9a-f]{64}")
MAX_JSON_BYTES = 64 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400

REPO = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = (
    REPO / "validation" / "omega-nnue-king-state-v5-early-terminal-protocol.json"
)

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
SELECTION_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "protocol",
        "terminalClass",
        "promotionStatus",
        "generation5Selection",
        "selectedCandidateId",
        "selectedModel",
        "operatorAuthorization",
        "terminalEvidence",
        "informationBoundary",
        "finalStageSeal",
    }
)
CLOSURE_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "protocol",
        "selectionSeal",
        "status",
        "terminalClass",
        "promotionStatus",
        "selectedCandidateId",
        "selectedModel",
        "operatorAuthorization",
        "terminalEvidence",
        "matchAuthorizationPresent",
        "compatibilityClosurePresent",
        "retryPermitted",
        "targetValuesExported",
        "generation6TargetRowsDecoded",
        "gameResultsRead",
        "resultInformationRead",
        "finalStageSeal",
    }
)
NO_ELIGIBLE_SELECTION_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "plan",
        "status",
        "winner",
        "evaluations",
        "heldOutTargetFieldsDecoded",
        "validationTargetFieldsDecodedForVerification",
        "robustnessAuthorized",
        "outputs",
    }
)
ROBUSTNESS_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "createdUtc",
        "profileId",
        "plan",
        "selection",
        "candidateId",
        "recipe",
        "trainingSeedPurpose",
        "trainingSeed",
        "factorBasis",
        "initializer",
        "selectedPrimaryNetwork",
        "robustnessNetwork",
        "robustnessManifest",
        "commonValidation",
        "baselines",
        "deploymentHealth",
        "gates",
        "passed",
        "mayReplacePrimary",
        "informationBoundary",
    }
)
SHORT_ROBUSTNESS_FAILURE_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "candidateId",
        "trainingPurpose",
        "reason",
        "exitCode",
        "plan",
        "selection",
        "heldOutTargetFieldsDecoded",
    }
)
LIFETIME_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "protocol",
        "robustness",
        "status",
        "resumePolicy",
        "heldOutTargetRowsDecodedAtClaim",
        "heldOutTargetFieldsDecodedAtClaim",
        "resultInformationRead",
    }
)

# Tests may override these only in-process.  Production CLI entry points reject
# a non-empty override and G6 executes this file in a fresh isolated process.
_TEST_REPOSITORY: Path | None = None
_TEST_PROTOCOL: dict[str, Any] | None = None
_TEST_TRAINER: ModuleType | Any | None = None


def _repository() -> Path:
    return (_TEST_REPOSITORY or REPO).resolve()


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _strict_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError(f"{label} is oversized")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error

    def reject_constant(value: str) -> Any:
        raise ValueError(f"{label} contains non-finite {value}")

    value = json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=reject_constant,
    )
    if type(value) is not dict:
        raise ValueError(f"{label} is not an object")
    return value


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _lexical_absolute(path: Path) -> Path:
    """Return an absolute path without following any filesystem entry."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _safe_parent(path: Path) -> None:
    absolute = _lexical_absolute(path)
    existing: list[Path] = []
    cursor = absolute.parent
    while True:
        if os.path.lexists(cursor):
            existing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for item in reversed(existing):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"path traverses reparse point: {item}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"path parent is not a directory: {item}")


def _file_state(info: os.stat_result) -> tuple[int, int, int, int | None, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        getattr(info, "st_mtime_ns", None),
        getattr(info, "st_nlink", 1),
    )


def _private_regular(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and not _is_reparse(info)
        and getattr(info, "st_nlink", 1) == 1
    )


def _snapshot(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    path = _lexical_absolute(path)
    _safe_parent(path)
    before = os.lstat(path)
    if not _private_regular(before):
        raise ValueError(f"{label} is not one private regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or not _private_regular(opened)
        ):
            raise ValueError(f"{label} changed before descriptor open")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise ValueError(f"{label} ended before its descriptor size")
            chunks.append(block)
            digest.update(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        if (
            _file_state(after) != _file_state(opened)
            or _file_state(os.lstat(path)) != _file_state(before)
        ):
            raise ValueError(f"{label} changed while read")
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    return {
        "path": str(path),
        "bytes": len(payload),
        "sha256": digest.hexdigest(),
    }, payload


def _identity(path: Path, label: str = "artifact") -> dict[str, Any]:
    return _snapshot(path, label)[0]


def _verify_identity_record(value: Any, label: str) -> Path:
    if type(value) is not dict or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity shape changed")
    if (
        type(value["path"]) is not str
        or type(value["bytes"]) is not int
        or value["bytes"] < 0
        or type(value["sha256"]) is not str
        or HEX64.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity values changed")
    path = _lexical_absolute(Path(value["path"]))
    if _identity(path, label) != value:
        raise ValueError(f"{label} content changed")
    return path


def _load_json(path: Path, label: str) -> dict[str, Any]:
    _, payload = _snapshot(path, label)
    return _strict_json_bytes(payload, label)


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value
    ) is None:
        raise ValueError(f"{label} is not canonical UTC")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _repo_path(relative: Any, label: str) -> Path:
    if type(relative) is not str:
        raise ValueError(f"{label} is not a path string")
    pure = Path(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"{label} escapes the repository")
    root = _lexical_absolute(_repository())
    path = _lexical_absolute(root / pure)
    if path != root and root not in path.parents:
        raise ValueError(f"{label} escapes the repository")
    _safe_parent(path)
    return path


def _artifact_paths(protocol: Mapping[str, Any]) -> dict[str, Path]:
    namespaces = protocol["namespaces"]
    return {
        key: _repo_path(value, f"namespace {key}")
        for key, value in namespaces.items()
    }


def _validate_protocol(value: dict[str, Any], identity: Mapping[str, Any]) -> dict[str, Any]:
    expected_top = {
        "schemaVersion",
        "kind",
        "protocolId",
        "profileId",
        "createdUtc",
        "status",
        "purpose",
        "runtime",
        "frozenGeneration5",
        "namespaces",
        "branches",
        "branchPolicy",
        "publication",
        "informationBoundary",
        "schemas",
    }
    if set(value) != expected_top:
        raise ValueError("early-terminal protocol field inventory changed")
    if (
        value["schemaVersion"] != SCHEMA_VERSION
        or value["kind"] != PROTOCOL_KIND
        or value["protocolId"] != PROTOCOL_ID
        or value["profileId"] != PROFILE_ID
    ):
        raise ValueError("early-terminal protocol envelope changed")
    _parse_utc(value["createdUtc"], "protocol createdUtc")
    if identity["bytes"] != PROTOCOL_BYTES or identity["sha256"] != PROTOCOL_SHA256:
        raise ValueError("early-terminal protocol identity changed")
    runtime = value.get("runtime")
    if type(runtime) is not dict or set(runtime) != {
        "isolated",
        "dontWriteBytecode",
        "python",
        "manifest",
    }:
        raise ValueError("early-terminal runtime contract changed")
    if runtime["isolated"] is not True or runtime["dontWriteBytecode"] is not True:
        raise ValueError("early-terminal isolated runtime policy changed")
    frozen = value.get("frozenGeneration5")
    if type(frozen) is not dict or set(frozen) != {
        "trainer",
        "runtimeAuthority",
        "baseTrainer",
        "profileValidator",
        "networkFormat",
        "profile",
        "finalFreeze",
    }:
        raise ValueError("early-terminal frozen G5 inventory changed")
    namespace_keys = {
        "trainingRoot",
        "trainingPlan",
        "validationSelection",
        "robustnessClaim",
        "robustnessFailure",
        "robustnessBundle",
        "robustnessSeal",
        "offlineLifetimeClaim",
        "offlineAccessClaim",
        "offlineReport",
        "authorityLifecycleLock",
        "terminalRoot",
        "sourceSelection",
        "sourceClosure",
        "compatibilityAuthorization",
        "compatibilityClosure",
    }
    if type(value.get("namespaces")) is not dict or set(value["namespaces"]) != namespace_keys:
        raise ValueError("early-terminal namespace inventory changed")
    paths = _artifact_paths(value)
    if paths["sourceSelection"].parent != paths["terminalRoot"] or paths[
        "sourceClosure"
    ].parent != paths["terminalRoot"]:
        raise ValueError("early-terminal outputs leave their canonical root")
    branches = value.get("branches")
    expected_classes = [
        "selection-no-eligible",
        "robustness-training-failed",
        "robustness-worker-aborted",
        "robustness-gate-failed",
        "heldout-gate-failed",
        "heldout-access-aborted",
    ]
    if (
        type(branches) is not list
        or [item.get("terminalClass") for item in branches] != expected_classes
        or value["branchPolicy"].get("precedence") != expected_classes
    ):
        raise ValueError("early-terminal branch order changed")
    expected_status = ["failed", "failed", "aborted", "failed", "failed", "aborted"]
    for branch, status in zip(branches, expected_status, strict=True):
        if set(branch) != {
            "terminalClass",
            "promotionStatus",
            "requiredEvidence",
            "internalReplaySplits",
        } or branch["promotionStatus"] != status:
            raise ValueError("early-terminal branch schema changed")
    if value.get("branchPolicy") != {
        "precedence": expected_classes,
        "ambiguousTerminalEvidenceRejects": True,
        "unfinishedPrimaryOrRobustnessWorkIsNotTerminal": True,
        "claimOnlyHeldoutAbortRequiresLifetimeLockAndExplicitOperator": True,
        "sharedLifecycleLockCoversOfflineEvaluationTerminalPublicationAndCompatibilityAuthorization": True,
        "terminalRootBlocksOfflineRetry": True,
        "legacyAccessBeforeLifetimeClaimRejects": True,
        "strictHeldOutChronology": ["robustness", "lifetime", "access", "report"],
        "canonicalHeldOutChronologyUtc": (
            "python-datetime-isoformat-utc-z-round-trip"
        ),
        "compatibilityAuthorizationOrClosureBlocksEarlyPublication": True,
        "earlyNamespaceBlocksLaterCompatibilityAuthorization": True,
        "normalCompatibilityPromotionFailureAndAbortRemainAuthoritative": True,
    }:
        raise ValueError("early-terminal branch policy changed")
    if value.get("publication") != {
        "exactTerminalFileInventory": [
            "source-selection.seal.json",
            "source-closure.json",
        ],
        "exclusiveCreateOnly": True,
        "noOutputPathArguments": True,
        "partialPublicationFailsClosed": True,
        "singleLinkRegularFilesOnly": True,
        "reparseSymlinkAndHardlinkReject": True,
        "lexicalNoFollowDescriptorIdentityRequired": True,
        "sourceSelectionPrecedesSourceClosure": True,
        "terminalClosureIsIrrevocable": True,
    }:
        raise ValueError("early-terminal publication policy changed")
    schemas = value.get("schemas")
    if (
        type(schemas) is not dict
        or set(schemas) != {"sourceSelectionFields", "sourceClosureFields"}
        or set(schemas["sourceSelectionFields"]) != SELECTION_FIELDS
        or set(schemas["sourceClosureFields"]) != CLOSURE_FIELDS
    ):
        raise ValueError("early-terminal output schema changed")
    boundary = value.get("informationBoundary")
    if boundary != {
        "generation6TargetRowsDecoded": 0,
        "generation6TargetValuesExported": 0,
        "generation5TargetValuesExported": 0,
        "gameResultsRead": False,
        "resultInformationRead": False,
        "metricsChecksPredictionsAndTargetsMayNotAppearInTerminalOutputs": True,
        "failedOrAbortedSourceReplayRawModel": None,
        "failedOrAbortedSourceReplayHealthPassed": False,
    }:
        raise ValueError("early-terminal information boundary changed")
    return value


def load_protocol() -> tuple[dict[str, Any], dict[str, Any]]:
    if _TEST_PROTOCOL is not None:
        return dict(_TEST_PROTOCOL), {
            "path": str((_repository() / "protocol.json").resolve()),
            "bytes": 1,
            "sha256": "0" * 64,
        }
    identity, payload = _snapshot(PROTOCOL_PATH, "early-terminal protocol")
    value = _strict_json_bytes(payload, "early-terminal protocol")
    return _validate_protocol(value, identity), identity


def _verify_pinned_record(record: Any, label: str) -> tuple[Path, bytes]:
    if type(record) is not dict or set(record) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} pin shape changed")
    raw_path = record["path"]
    path = Path(raw_path) if Path(str(raw_path)).is_absolute() else _repo_path(raw_path, label)
    identity, payload = _snapshot(path, label)
    if identity["bytes"] != record["bytes"] or identity["sha256"] != record["sha256"]:
        raise ValueError(f"{label} pin changed")
    return path, payload


class _ExactLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, sources: Mapping[str, tuple[str, bytes]]) -> None:
        self.sources = dict(sources)

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname in self.sources:
            return importlib.util.spec_from_loader(
                fullname, self, origin=self.sources[fullname][0]
            )
        return None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        source_path, payload = self.sources[module.__name__]
        module.__file__ = source_path
        module.__package__ = ""
        exec(compile(payload, source_path, "exec", dont_inherit=True), module.__dict__)


def _require_runtime(protocol: Mapping[str, Any]) -> None:
    if _TEST_TRAINER is not None:
        return
    if sys.flags.isolated != 1 or not sys.dont_write_bytecode:
        raise RuntimeError("early-terminal authority requires Python -I -B")
    python_path, _ = _verify_pinned_record(protocol["runtime"]["python"], "Python runtime")
    if Path(sys.executable).resolve() != python_path.resolve():
        raise ValueError("early-terminal Python runtime changed")
    _verify_pinned_record(protocol["runtime"]["manifest"], "G5 runtime manifest")


def _load_trainer(protocol: Mapping[str, Any]) -> Any:
    if _TEST_TRAINER is not None:
        return _TEST_TRAINER
    _require_runtime(protocol)
    frozen = protocol["frozenGeneration5"]
    roles = (
        "runtimeAuthority",
        "networkFormat",
        "baseTrainer",
        "profileValidator",
        "trainer",
    )
    records: dict[str, dict[str, Any]] = {}
    sources: dict[str, tuple[str, bytes]] = {}
    for role in roles:
        path, payload = _verify_pinned_record(frozen[role], f"frozen G5 {role}")
        name = path.stem
        if name in sys.modules:
            raise RuntimeError(f"frozen G5 module was preloaded: {name}")
        sources[name] = (str(path), payload)
        records[role] = _identity(path, f"frozen G5 {role}")
    _verify_pinned_record(frozen["profile"], "frozen G5 profile")
    _verify_pinned_record(frozen["finalFreeze"], "frozen G5 final freeze")
    loader = _ExactLoader(sources)
    sys.meta_path.insert(0, loader)
    try:
        module = importlib.import_module("king_state_train_generation5")
    finally:
        sys.meta_path.remove(loader)
    for role, identity in records.items():
        if _identity(Path(identity["path"]), f"frozen G5 {role} recheck") != identity:
            raise ValueError(f"frozen G5 {role} changed during import")
    if module.PROFILE_ID != PROFILE_ID:
        raise ValueError("frozen G5 trainer profile changed")
    return module


def _branch(protocol: Mapping[str, Any], terminal_class: str) -> dict[str, Any]:
    for item in protocol["branches"]:
        if item["terminalClass"] == terminal_class:
            return dict(item)
    raise ValueError(f"unknown early-terminal class {terminal_class}")


def _verify_no_eligible_selection(
    trainer: Any, selection_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    selection = trainer._load_json(selection_path, "generation-5 no-eligible selection")
    if set(selection) != NO_ELIGIBLE_SELECTION_FIELDS:
        raise ValueError("generation-5 no-eligible selection fields changed")
    if (
        selection.get("schemaVersion") != trainer.SCHEMA_VERSION
        or selection.get("kind") != trainer.SELECTION_KIND
        or selection.get("profileId") != trainer.PROFILE_ID
        or selection.get("status") != "closed-no-eligible-candidate"
        or selection.get("winner") is not None
        or selection.get("robustnessAuthorized") is not False
        or selection.get("heldOutTargetFieldsDecoded") != 0
    ):
        raise ValueError("generation-5 no-eligible selection envelope changed")
    datetime.fromisoformat(str(selection["createdUtc"]).replace("Z", "+00:00"))
    plan_path = trainer._verify_identity(selection["plan"], "no-eligible plan")
    plan, profile = trainer._verify_plan(plan_path)
    if selection_path.resolve() != Path(str(plan["selectionPath"])).resolve():
        raise ValueError("no-eligible selection is outside the frozen namespace")
    if selection.get("outputs") != plan.get("outputs"):
        raise ValueError("no-eligible post-selection outputs changed")
    split_rows = trainer._planned_split_rows(plan)
    output_dir = Path(str(plan["outputDirectory"])).resolve()
    manifests: dict[str, dict[str, Any]] = {}
    failures: dict[str, dict[str, Any]] = {}
    networks: dict[str, Any] = {}
    for candidate in trainer.CANDIDATES:
        paths = trainer._candidate_paths(candidate, output_dir)
        if trainer._candidate_claim_path(candidate, output_dir).exists():
            raise RuntimeError(f"candidate {candidate} still has an active claim")
        if paths["manifest"].exists() == paths["failure"].exists():
            raise ValueError(f"candidate {candidate} completion is ambiguous")
        if paths["manifest"].exists():
            manifest = trainer._load_json(paths["manifest"], f"{candidate} manifest")
            networks[candidate] = trainer._verify_manifest_record(
                manifest,
                candidate=candidate,
                plan=plan,
                profile=profile,
                plan_path=plan_path,
                output_dir=output_dir,
                split_rows=split_rows,
            )
            manifests[candidate] = manifest
        else:
            failure = trainer._load_json(paths["failure"], f"{candidate} failure")
            trainer._verify_failure_record(
                failure,
                candidate=candidate,
                plan=plan,
                profile=profile,
                plan_path=plan_path,
                split_rows=split_rows,
            )
            if trainer._candidate_bundle(candidate, output_dir).exists():
                raise ValueError(f"{candidate} failure coexists with a bundle")
            failures[candidate] = failure
    context = None
    if manifests:
        context = trainer._selection_verification_context(plan, profile, split_rows)
        for candidate, manifest in manifests.items():
            trainer._verify_recomputed_candidate_selection(
                candidate=candidate,
                manifest=manifest,
                network=networks[candidate],
                context=context,
            )
    gate = trainer._mapping(
        trainer._mapping(profile["validationSelection"], "selection")["eligibility"],
        "eligibility",
    )
    evaluations: dict[str, Any] = {}
    eligible: list[str] = []
    for candidate in trainer.CANDIDATES:
        if candidate in failures:
            evaluations[candidate] = {
                "eligible": False,
                "reason": failures[candidate]["reason"],
            }
            continue
        manifest = manifests[candidate]
        metrics = manifest["commonValidation"]
        i0 = manifest["i0Validation"]
        zero = manifest["zeroResidualValidation"]
        checks = {
            "minimumRelativeHuberImprovementOverI0": trainer._relative_improvement(
                float(metrics["huberLoss"]), float(i0["huberLoss"])
            )
            >= float(gate["minimumRelativeHuberImprovementOverI0"]),
            "lowerPhaseMacroCpMaeVersusI0": float(metrics["cpMae"])
            < float(i0["cpMae"]),
            "lowerHuberLossThanZeroResidual": float(metrics["huberLoss"])
            < float(zero["huberLoss"]),
            "maximumAnyPhaseCpMaeRegressionVersusI0": all(
                float(metrics["phase"][phase]["cpMae"])
                <= float(i0["phase"][phase]["cpMae"])
                + float(gate["maximumAnyPhaseCpMaeRegressionVersusI0"])
                for phase in trainer.PHASES
            ),
            "deploymentHealth": bool(manifest["health"]["passed"]),
        }
        evaluations[candidate] = {
            "eligible": all(checks.values()),
            "checks": checks,
            "commonValidation": metrics,
        }
        if all(checks.values()):
            eligible.append(candidate)
    if eligible:
        raise ValueError("no-eligible selection hides an eligible candidate")
    expected_validation = (
        split_rows["validation"] * len(trainer.TARGET_FIELDS_DECODED_PER_ROW)
        if manifests
        else 0
    )
    if (
        selection.get("evaluations") != evaluations
        or selection.get("validationTargetFieldsDecodedForVerification")
        != expected_validation
    ):
        raise ValueError("no-eligible selection differs from recomputation")
    return selection, plan, profile


def _selection_context(
    trainer: Any, selection_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
    raw = _load_json(selection_path, "generation-5 validation selection")
    if raw.get("status") == "closed-no-eligible-candidate":
        selection, plan, profile = _verify_no_eligible_selection(trainer, selection_path)
        return selection, plan, profile, True
    selection, plan, profile = trainer._verify_selection(selection_path)
    return selection, plan, profile, False


def _verify_robustness_failure_any(
    trainer: Any,
    path: Path,
    selection: Mapping[str, Any],
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> str:
    failure = trainer._load_json(path, "generation-5 robustness failure")
    candidate = str(selection["winner"])
    output_dir = Path(str(plan["outputDirectory"])).resolve()
    full_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "candidateId",
        "trainingPurpose",
        "createdUtc",
        "reason",
        "recipe",
        "trainingSeed",
        "factorSeed",
        "history",
        "basis",
        "initializerMigration",
        "inputs",
        "informationBoundary",
    }
    if set(failure) == full_fields:
        trainer._verify_failure_record(
            failure,
            candidate=candidate,
            plan=plan,
            profile=profile,
            plan_path=Path(str(selection["plan"]["path"])),
            split_rows=trainer._planned_split_rows(plan),
            robustness=True,
        )
        terminal_class = "robustness-training-failed"
    elif set(failure) == SHORT_ROBUSTNESS_FAILURE_FIELDS:
        if (
            failure.get("schemaVersion") != trainer.SCHEMA_VERSION
            or failure.get("kind") != trainer.ROBUSTNESS_FAILURE_KIND
            or failure.get("profileId") != trainer.PROFILE_ID
            or failure.get("candidateId") != candidate
            or failure.get("trainingPurpose") != "robustness-training"
            or failure.get("reason")
            != "robustness worker exited before bundle publication"
            or type(failure.get("exitCode")) is not int
            or failure["exitCode"] == 0
            or failure.get("plan") != selection["plan"]
            or failure.get("selection") != _identity(
                Path(str(plan["selectionPath"])), "robustness selection"
            )
            or failure.get("heldOutTargetFieldsDecoded") != 0
        ):
            raise ValueError("robustness worker-abort record changed")
        datetime.fromisoformat(str(failure["createdUtc"]).replace("Z", "+00:00"))
        terminal_class = "robustness-worker-aborted"
    else:
        raise ValueError("robustness failure schema is unknown")
    paths = trainer._candidate_paths(candidate, output_dir, robustness=True)
    if trainer._candidate_bundle(candidate, output_dir, robustness=True).exists() or any(
        paths[key].exists() for key in ("network", "canonical", "shadow", "manifest")
    ):
        raise ValueError("robustness failure coexists with success output")
    return terminal_class


def _verify_robustness_seal_any(
    trainer: Any, path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    value = trainer._load_json(path, "generation-5 robustness seal")
    if set(value) != ROBUSTNESS_FIELDS:
        raise ValueError("robustness seal field inventory changed")
    if (
        value.get("schemaVersion") != trainer.SCHEMA_VERSION
        or value.get("kind") != trainer.ROBUSTNESS_KIND
    ):
        raise ValueError("robustness seal envelope changed")
    datetime.fromisoformat(str(value["createdUtc"]).replace("Z", "+00:00"))
    selection_path = trainer._verify_identity(value["selection"], "robustness selection")
    selection, plan, profile = trainer._verify_selection(selection_path)
    expected_path = Path(str(plan["outputs"]["robustnessSeal"])).resolve()
    if path.resolve() != expected_path:
        raise ValueError("robustness seal is outside its frozen namespace")
    snapshot = trainer._robustness_snapshot(
        selection_path=selection_path,
        selection=selection,
        plan=plan,
        profile=profile,
    )
    for key, expected in snapshot.items():
        if value.get(key) != expected:
            raise ValueError(f"robustness {key} differs from recomputation")
    return value, selection, plan, profile


def _verify_lifetime_claim(
    path: Path,
    protocol_identity: Mapping[str, Any],
    robustness_identity: Mapping[str, Any],
) -> dict[str, Any]:
    value = _load_json(path, "offline evaluation lifetime claim")
    return _verify_lifetime_claim_value(
        value, protocol_identity, robustness_identity
    )


def _verify_lifetime_claim_value(
    value: Mapping[str, Any],
    protocol_identity: Mapping[str, Any],
    robustness_identity: Mapping[str, Any],
) -> dict[str, Any]:
    if set(value) != LIFETIME_FIELDS:
        raise ValueError("offline lifetime-claim fields changed")
    expected = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LIFETIME_CLAIM_KIND,
        "profileId": PROFILE_ID,
        "protocol": dict(protocol_identity),
        "robustness": dict(robustness_identity),
        "status": "claimed-before-any-held-out-target-decode",
        "resumePolicy": "reuse this immutable claim and lifetime lock; never publish a second claim",
        "heldOutTargetRowsDecodedAtClaim": 0,
        "heldOutTargetFieldsDecodedAtClaim": 0,
        "resultInformationRead": False,
    }
    for key, item in expected.items():
        if value.get(key) != item:
            raise ValueError(f"offline lifetime-claim {key} changed")
    _parse_utc(value.get("createdUtc"), "offline lifetime claim createdUtc")
    return dict(value)


def _artifact_utc(value: Any, label: str) -> datetime:
    return _parse_chronology_utc(value, label)


def _parse_chronology_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} is not UTC")
    canonical = parsed.astimezone(timezone.utc)
    if canonical.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{label} is not canonical UTC")
    return canonical


def _verify_heldout_chronology(
    *,
    robustness: Mapping[str, Any],
    lifetime: Mapping[str, Any],
    access: Mapping[str, Any],
    report: Mapping[str, Any] | None,
) -> None:
    ordered = [
        (
            "robustness",
            _parse_chronology_utc(
                robustness.get("createdUtc"), "robustness createdUtc"
            ),
        ),
        (
            "lifetime",
            _parse_chronology_utc(lifetime.get("createdUtc"), "lifetime createdUtc"),
        ),
        (
            "access",
            _parse_chronology_utc(access.get("createdUtc"), "access createdUtc"),
        ),
    ]
    if report is not None:
        ordered.append(
            (
                "report",
                _parse_chronology_utc(report.get("createdUtc"), "report createdUtc"),
            )
        )
    for (earlier_name, earlier), (later_name, later) in zip(
        ordered, ordered[1:], strict=False
    ):
        if not earlier < later:
            raise ValueError(
                "held-out authority chronology changed: "
                f"{earlier_name} must strictly precede {later_name}"
            )


def _replay_offline_report(
    trainer: Any, report_path: Path
) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]
]:
    report = trainer._verify_offline_report(report_path)
    robustness_path = trainer._verify_identity(report["robustness"], "offline robustness")
    robustness, selection, plan, profile = trainer._verify_robustness(robustness_path)
    claim_path = trainer._verify_identity(report["accessClaim"], "offline access claim")
    trainer._verify_offline_claim(
        claim_path, selection=selection, robustness=robustness, plan=plan
    )
    claim_identity = _identity(claim_path, "offline access claim")
    identities = trainer._mapping(plan["identities"], "plan identities")
    corpus = Path(str(identities["corpus"]["path"]))
    cpp_evaluator = Path(str(identities["cppEvaluator"]["path"]))
    heldout = trainer._load_decision_splits(
        corpus,
        hce_evaluator=cpp_evaluator,
        decode_splits=("heldOut",),
        heldout_access_claim=claim_identity,
    )
    indices = heldout.indices(trainer.SPLITS["heldOut"])
    selected_network = trainer.QuantizedNetwork.read(
        Path(str(selection["winnerNetwork"]["path"]))
    )
    initializer3 = trainer.QuantizedNetwork.read(Path(str(identities["initializer"]["path"])))
    initializer4 = trainer._migrate_initializer(initializer3)
    selected_predictions = trainer._predict_network(
        selected_network, heldout.features, indices
    )
    i0_predictions = trainer._predict_network(initializer4, heldout.features, indices)
    zero_predictions = trainer.np.zeros(indices.size, dtype=trainer.np.int32)
    metrics = {
        "selectedPrimary": trainer._common_metrics(
            heldout, trainer.SPLITS["heldOut"], selected_predictions
        ),
        "I0": trainer._common_metrics(
            heldout, trainer.SPLITS["heldOut"], i0_predictions
        ),
        "zeroResidual": trainer._common_metrics(
            heldout, trainer.SPLITS["heldOut"], zero_predictions
        ),
    }
    bootstrap = trainer._whole_root_bootstrap(
        heldout,
        selected_predictions,
        i0_predictions,
        replicates=trainer.OFFLINE_BOOTSTRAP_REPLICATES,
        seed=trainer.OFFLINE_BOOTSTRAP_SEED,
    )
    gate = trainer._offline_gate(metrics=metrics, bootstrap=bootstrap, profile=profile)
    split_rows = trainer._planned_split_rows(plan)
    expected = {
        "schemaVersion": trainer.SCHEMA_VERSION,
        "kind": trainer.OFFLINE_REPORT_KIND,
        "createdUtc": report["createdUtc"],
        "profileId": trainer.PROFILE_ID,
        "plan": selection["plan"],
        "selection": robustness["selection"],
        "robustness": _identity(robustness_path, "offline robustness"),
        "accessClaim": claim_identity,
        "selectedCandidateId": selection["winner"],
        "evaluatedNetworks": {
            "selectedPrimary": selection["winnerNetwork"],
            "I0": identities["initializer"],
            "zeroResidual": "constant zero residual; no network artifact",
            "robustnessNetworkHeldOutTargetEvaluations": 0,
            "runnerUpNetworkHeldOutTargetEvaluations": 0,
        },
        "metrics": metrics,
        "bootstrap": bootstrap,
        "gate": gate,
        "targetAccess": {
            "accessClaimPublishedBeforeHeldOutDecode": True,
            "targetFieldNames": list(trainer.TARGET_FIELDS_DECODED_PER_ROW),
            "trainTargetRowsDecoded": 0,
            "validationTargetRowsDecoded": 0,
            "heldOutTargetRowsDecoded": heldout.target_rows_decoded,
            "heldOutTargetFieldsDecoded": heldout.target_fields_decoded,
            "heldOutHandcraftedScoresComputed": heldout.handcrafted_scores_computed,
            "heldOutResidualTargetsComputed": heldout.residual_targets_computed,
            "selectedPrimaryNetworkPredictions": indices.size,
            "i0Predictions": indices.size,
            "zeroResidualPredictions": indices.size,
            "robustnessNetworkHeldOutTargetMetricPredictions": 0,
            "runnerUpNetworkHeldOutTargetMetricPredictions": 0,
            "matchResultsAccessed": False,
        },
        "matchAuthorization": {
            "authorized": gate["passed"],
            "selectedPrimaryNetwork": selection["winnerNetwork"],
            "runnerUpFallback": False,
        },
        "failureAction": (
            "authorize preregistered matches for the selected primary only"
            if gate["passed"]
            else "close generation; do not test a runner-up or launch matches"
        ),
    }
    if (
        heldout.target_rows_decoded != split_rows["heldOut"]
        or heldout.target_fields_decoded
        != split_rows["heldOut"] * len(trainer.TARGET_FIELDS_DECODED_PER_ROW)
        or report != expected
    ):
        raise ValueError("offline report differs from full held-out replay")
    if gate["passed"] is not False:
        raise ValueError("offline report is not a terminal failed gate")
    return report, robustness, selection, plan, profile


class _LifetimeLock:
    LOCK_OFFSET = 1 << 30

    def __init__(
        self, descriptor: int, *, label: str = "offline evaluation lifetime lock"
    ) -> None:
        self.descriptor = descriptor
        self.label = label
        self.locked = False
        self.lock_offset = 0

    def acquire(self) -> None:
        self.lock_offset = self.LOCK_OFFSET
        os.lseek(self.descriptor, self.lock_offset, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(self.descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError(f"{self.label} is active") from error
        else:
            import fcntl

            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError(f"{self.label} is active") from error
        self.locked = True

    def close(self) -> None:
        if self.locked:
            os.lseek(self.descriptor, self.lock_offset, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            self.locked = False
        os.close(self.descriptor)

    def json_value(self) -> dict[str, Any]:
        os.lseek(self.descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            block = os.read(self.descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return _strict_json_bytes(
            b"".join(chunks), "locked offline evaluation lifetime claim"
        )

    def __enter__(self) -> "_LifetimeLock":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def _open_lifetime_lock(path: Path) -> _LifetimeLock:
    path = _lexical_absolute(path)
    _safe_parent(path)
    before = os.lstat(path)
    if not _private_regular(before):
        raise ValueError("offline lifetime claim is not one private regular file")
    descriptor = os.open(
        path,
        os.O_RDWR
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    lock = _LifetimeLock(descriptor)
    try:
        opened = os.fstat(descriptor)
        current = os.lstat(path)
        if (
            not _private_regular(opened)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or _file_state(current) != _file_state(before)
        ):
            raise ValueError("offline lifetime claim changed before descriptor open")
        lock.acquire()
        current = os.lstat(path)
        if _file_state(os.fstat(descriptor)) != _file_state(current):
            raise ValueError("offline lifetime claim changed while lock was acquired")
    except BaseException:
        os.close(descriptor)
        raise
    return lock


def _exclusive_locked_claim(path: Path, value: Mapping[str, Any]) -> _LifetimeLock:
    path = _lexical_absolute(path)
    _safe_parent(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_parent(path)
    descriptor = os.open(
        path,
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    lock = _LifetimeLock(descriptor)
    try:
        opened = os.fstat(descriptor)
        current = os.lstat(path)
        if not _private_regular(opened) or _file_state(opened) != _file_state(current):
            raise ValueError("offline lifetime claim publication changed its inode")
        os.write(descriptor, b" ")
        os.fsync(descriptor)
        lock.acquire()
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        payload = _canonical_json(value)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        return lock
    except BaseException:
        lock.close()
        raise


def _exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    path = _lexical_absolute(path)
    _safe_parent(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_parent(path)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        opened = os.fstat(descriptor)
        current = os.lstat(path)
        if not _private_regular(opened) or _file_state(opened) != _file_state(current):
            raise ValueError("exclusive JSON publication changed its inode")
        payload = _canonical_json(value)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_authority_lifecycle_lock(path: Path) -> _LifetimeLock:
    """Open the shared G5 terminal/compatibility lifecycle mutex.

    The marker is deliberately persistent.  It carries no decision; the
    canonical terminal root and compatibility authorization remain the
    fail-closed decision records.
    """

    path = _lexical_absolute(path)
    _safe_parent(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_parent(path)
    before: os.stat_result | None = None
    if os.path.lexists(path):
        before = os.lstat(path)
        if not _private_regular(before):
            raise ValueError("authority lifecycle lock is not one private regular file")
    descriptor = os.open(
        path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    lock = _LifetimeLock(descriptor, label="Generation-5 authority lifecycle lock")
    try:
        opened = os.fstat(descriptor)
        current = os.lstat(path)
        if (
            not _private_regular(opened)
            or _file_state(opened) != _file_state(current)
            or (
                before is not None
                and (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            )
        ):
            raise ValueError("authority lifecycle lock changed before descriptor open")
        if opened.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"L")
            os.fsync(descriptor)
        elif opened.st_size != 1:
            raise ValueError("authority lifecycle lock marker changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, 1) != b"L":
            raise ValueError("authority lifecycle lock marker changed")
        lock.acquire()
        current = os.lstat(path)
        if _file_state(os.fstat(descriptor)) != _file_state(current):
            raise ValueError("authority lifecycle lock changed while acquired")
        return lock
    except BaseException:
        lock.close()
        raise


def _lifetime_claim_value(
    protocol_identity: Mapping[str, Any], robustness_identity: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LIFETIME_CLAIM_KIND,
        "profileId": PROFILE_ID,
        "createdUtc": _utc_now(),
        "protocol": dict(protocol_identity),
        "robustness": dict(robustness_identity),
        "status": "claimed-before-any-held-out-target-decode",
        "resumePolicy": "reuse this immutable claim and lifetime lock; never publish a second claim",
        "heldOutTargetRowsDecodedAtClaim": 0,
        "heldOutTargetFieldsDecodedAtClaim": 0,
        "resultInformationRead": False,
    }


def run_offline_guarded() -> dict[str, Any]:
    protocol, protocol_identity, trainer, paths = _authority_context()
    with _open_authority_lifecycle_lock(paths["authorityLifecycleLock"]):
        if os.path.lexists(paths["terminalRoot"]):
            raise ValueError("early-terminal namespace blocks held-out evaluation")
        _reject_match_authority(paths)
        robustness, selection, plan, _profile = trainer._verify_robustness(
            paths["robustnessSeal"]
        )
        if robustness["passed"] is not True:
            raise ValueError("held-out evaluation requires passed robustness")
        robustness_identity = _identity(paths["robustnessSeal"], "robustness seal")
        claim_path = paths["offlineLifetimeClaim"]
        if os.path.lexists(claim_path):
            _verify_lifetime_claim(claim_path, protocol_identity, robustness_identity)
            lock = _open_lifetime_lock(claim_path)
        else:
            if os.path.lexists(paths["offlineAccessClaim"]) or os.path.lexists(
                paths["offlineReport"]
            ):
                raise ValueError(
                    "legacy held-out access/report predates the lifetime authority"
                )
            lock = _exclusive_locked_claim(
                claim_path, _lifetime_claim_value(protocol_identity, robustness_identity)
            )
        with lock:
            lifetime = _verify_lifetime_claim_value(
                lock.json_value(), protocol_identity, robustness_identity
            )
            if not _artifact_utc(
                robustness.get("createdUtc"), "robustness createdUtc"
            ) < _artifact_utc(lifetime.get("createdUtc"), "lifetime createdUtc"):
                raise ValueError("robustness must strictly precede the lifetime claim")
            if os.path.lexists(paths["terminalRoot"]):
                raise ValueError("early-terminal namespace blocks held-out evaluation")
            _reject_match_authority(paths)
            access: dict[str, Any] | None = None
            if os.path.lexists(paths["offlineAccessClaim"]):
                access = trainer._verify_offline_claim(
                    paths["offlineAccessClaim"],
                    selection=selection,
                    robustness=robustness,
                    plan=plan,
                )
            report: dict[str, Any] | None = None
            if os.path.lexists(paths["offlineReport"]):
                if access is None:
                    raise ValueError("offline report lacks its immutable access claim")
                report = trainer._verify_offline_report(paths["offlineReport"])
            if access is not None:
                _verify_heldout_chronology(
                    robustness=robustness,
                    lifetime=lifetime,
                    access=access,
                    report=report,
                )
            result = trainer._offline_evaluate(
                argparse.Namespace(robustness=paths["robustnessSeal"])
            )
            access = trainer._verify_offline_claim(
                paths["offlineAccessClaim"],
                selection=selection,
                robustness=robustness,
                plan=plan,
            )
            _verify_heldout_chronology(
                robustness=robustness,
                lifetime=lifetime,
                access=access,
                report=result,
            )
            return result


def _reject_match_authority(paths: Mapping[str, Path]) -> None:
    if os.path.lexists(paths["compatibilityAuthorization"]) or os.path.lexists(
        paths["compatibilityClosure"]
    ):
        raise ValueError("compatibility authorization/closure blocks early terminal authority")


def _unexpected_postselection(
    paths: Mapping[str, Path], permitted: set[str]
) -> None:
    roles = {
        "robustnessClaim",
        "robustnessFailure",
        "robustnessBundle",
        "robustnessSeal",
        "offlineLifetimeClaim",
        "offlineAccessClaim",
        "offlineReport",
    }
    for role in roles - permitted:
        if os.path.lexists(paths[role]):
            raise ValueError(f"ambiguous/unexpected terminal artifact exists: {role}")


def _classify_terminal_locked(
    protocol: Mapping[str, Any],
    protocol_identity: Mapping[str, Any],
    trainer: Any,
    paths: Mapping[str, Path],
    *,
    operator_id: str | None = None,
    abort_reason: str | None = None,
    abort_lifetime_lock: _LifetimeLock | None = None,
) -> dict[str, Any]:
    _reject_match_authority(paths)
    if not paths["validationSelection"].is_file():
        raise FileNotFoundError("generation-5 validation selection is absent")
    selection, plan, profile, no_eligible = _selection_context(
        trainer, paths["validationSelection"]
    )
    selection_identity = _identity(paths["validationSelection"], "G5 selection")
    if no_eligible:
        _unexpected_postselection(paths, set())
        terminal_class = "selection-no-eligible"
        evidence = {"validationSelection": selection_identity}
        selected_candidate = None
        selected_model = None
    else:
        selected_candidate = str(selection["winner"])
        selected_model = dict(selection["winnerNetwork"])
        failure_exists = os.path.lexists(paths["robustnessFailure"])
        seal_exists = os.path.lexists(paths["robustnessSeal"])
        if failure_exists and seal_exists:
            raise ValueError("robustness failure and seal coexist")
        if failure_exists:
            _unexpected_postselection(
                paths, {"robustnessFailure"}
            )
            terminal_class = _verify_robustness_failure_any(
                trainer,
                paths["robustnessFailure"],
                selection,
                plan,
                profile,
            )
            evidence = {
                "validationSelection": selection_identity,
                "robustnessFailure": _identity(
                    paths["robustnessFailure"], "robustness failure"
                ),
            }
        elif seal_exists:
            robustness, verified_selection, verified_plan, _verified_profile = (
                _verify_robustness_seal_any(trainer, paths["robustnessSeal"])
            )
            if verified_selection != selection or verified_plan != plan:
                raise ValueError("robustness and selection lineages differ")
            robustness_identity = _identity(paths["robustnessSeal"], "robustness seal")
            if robustness["passed"] is False:
                _unexpected_postselection(
                    paths, {"robustnessBundle", "robustnessSeal"}
                )
                terminal_class = "robustness-gate-failed"
                evidence = {
                    "validationSelection": selection_identity,
                    "robustnessSeal": robustness_identity,
                }
            elif robustness["passed"] is True:
                report_exists = os.path.lexists(paths["offlineReport"])
                access_exists = os.path.lexists(paths["offlineAccessClaim"])
                lifetime_exists = os.path.lexists(paths["offlineLifetimeClaim"])
                if report_exists:
                    if not access_exists or not lifetime_exists:
                        raise ValueError("offline report lacks both immutable claims")
                    report, replayed_robustness, replayed_selection, replayed_plan, _ = (
                        _replay_offline_report(trainer, paths["offlineReport"])
                    )
                    if (
                        replayed_robustness != robustness
                        or replayed_selection != selection
                        or replayed_plan != plan
                    ):
                        raise ValueError("offline replay lineage differs from canonical G5 state")
                    lifetime = _verify_lifetime_claim(
                        paths["offlineLifetimeClaim"],
                        protocol_identity,
                        robustness_identity,
                    )
                    access = trainer._verify_offline_claim(
                        paths["offlineAccessClaim"],
                        selection=selection,
                        robustness=robustness,
                        plan=plan,
                    )
                    _verify_heldout_chronology(
                        robustness=robustness,
                        lifetime=lifetime,
                        access=access,
                        report=report,
                    )
                    terminal_class = "heldout-gate-failed"
                    evidence = {
                        "validationSelection": selection_identity,
                        "robustnessSeal": robustness_identity,
                        "offlineLifetimeClaim": _identity(
                            paths["offlineLifetimeClaim"], "offline lifetime claim"
                        ),
                        "offlineAccessClaim": _identity(
                            paths["offlineAccessClaim"], "offline access claim"
                        ),
                        "offlineReport": _identity(paths["offlineReport"], "offline report"),
                    }
                elif access_exists:
                    if not lifetime_exists:
                        raise ValueError(
                            "claim-only abort lacks the preregistered lifetime claim"
                        )
                    if operator_id is None or abort_reason != "offline-evaluation-interrupted":
                        raise ValueError(
                            "claim-only abort requires operator-id and fixed abort reason"
                        )
                    owned_lock = abort_lifetime_lock is None
                    lifetime_lock = abort_lifetime_lock or _open_lifetime_lock(
                        paths["offlineLifetimeClaim"]
                    )
                    try:
                        _verify_lifetime_claim_value(
                            lifetime_lock.json_value(),
                            protocol_identity,
                            robustness_identity,
                        )
                        access = trainer._verify_offline_claim(
                            paths["offlineAccessClaim"],
                            selection=selection,
                            robustness=robustness,
                            plan=plan,
                        )
                        _verify_heldout_chronology(
                            robustness=robustness,
                            lifetime=lifetime_lock.json_value(),
                            access=access,
                            report=None,
                        )
                    finally:
                        if owned_lock:
                            lifetime_lock.close()
                    terminal_class = "heldout-access-aborted"
                    evidence = {
                        "validationSelection": selection_identity,
                        "robustnessSeal": robustness_identity,
                        "offlineLifetimeClaim": _identity(
                            paths["offlineLifetimeClaim"], "offline lifetime claim"
                        ),
                        "offlineAccessClaim": _identity(
                            paths["offlineAccessClaim"], "offline access claim"
                        ),
                    }
                else:
                    raise RuntimeError("passed robustness is not an early terminal outcome")
            else:
                raise ValueError("robustness passed is not Boolean")
        else:
            raise RuntimeError("selected G5 winner has no terminal post-selection state")
    branch = _branch(protocol, terminal_class)
    if set(evidence) != set(branch["requiredEvidence"]):
        raise ValueError("terminal evidence role inventory changed")
    return {
        "terminalClass": terminal_class,
        "promotionStatus": branch["promotionStatus"],
        "generation5Selection": selection_identity,
        "selectedCandidateId": selected_candidate,
        "selectedModel": selected_model,
        "terminalEvidence": evidence,
        "internalReplaySplits": list(branch["internalReplaySplits"]),
        "operatorId": operator_id if terminal_class == "heldout-access-aborted" else None,
        "abortReason": abort_reason if terminal_class == "heldout-access-aborted" else None,
    }


def _authority_context() -> tuple[
    dict[str, Any], dict[str, Any], Any, dict[str, Path]
]:
    protocol, protocol_identity = load_protocol()
    trainer = _load_trainer(protocol)
    return protocol, protocol_identity, trainer, _artifact_paths(protocol)


def _claim_only_abort_lock(paths: Mapping[str, Path]) -> _LifetimeLock | None:
    if (
        not os.path.lexists(paths["offlineReport"])
        and os.path.lexists(paths["offlineAccessClaim"])
        and os.path.lexists(paths["offlineLifetimeClaim"])
    ):
        return _open_lifetime_lock(paths["offlineLifetimeClaim"])
    return None


def classify_terminal(
    *, operator_id: str | None = None, abort_reason: str | None = None
) -> dict[str, Any]:
    protocol, protocol_identity, trainer, paths = _authority_context()
    with _open_authority_lifecycle_lock(paths["authorityLifecycleLock"]):
        abort_lock = _claim_only_abort_lock(paths)
        try:
            return _classify_terminal_locked(
                protocol,
                protocol_identity,
                trainer,
                paths,
                operator_id=operator_id,
                abort_reason=abort_reason,
                abort_lifetime_lock=abort_lock,
            )
        finally:
            if abort_lock is not None:
                abort_lock.close()


def _information_boundary(classification: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "internalGeneration5ReplaySplits": list(classification["internalReplaySplits"]),
        "generation5TargetValuesExported": 0,
        "generation6TargetRowsDecoded": 0,
        "generation6TargetValuesExported": 0,
        "gameResultsRead": False,
        "resultInformationRead": False,
    }


def _operator_authorization(classification: Mapping[str, Any]) -> dict[str, str] | None:
    if classification["terminalClass"] != "heldout-access-aborted":
        return None
    operator = classification.get("operatorId")
    reason = classification.get("abortReason")
    if (
        type(operator) is not str
        or not operator.strip()
        or reason != "offline-evaluation-interrupted"
    ):
        raise ValueError("heldout abort lacks an explicit operator authorization")
    return {"operatorId": operator, "abortReason": reason}


def _evidence_latest(value: Mapping[str, Any], protocol: Mapping[str, Any]) -> datetime:
    latest = _parse_utc(protocol["createdUtc"], "protocol createdUtc")
    for role, record in value["terminalEvidence"].items():
        path = _verify_identity_record(record, f"terminal evidence {role}")
        document = _load_json(path, f"terminal evidence {role}")
        created = document.get("createdUtc", document.get("sealedUtc"))
        if created is None:
            continue
        try:
            stamp = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"terminal evidence {role} timestamp is invalid") from error
        if stamp.tzinfo is None:
            raise ValueError(f"terminal evidence {role} timestamp lacks timezone")
        latest = max(latest, stamp.astimezone(timezone.utc))
    return latest


def _after(latest: datetime) -> str:
    now = datetime.now(timezone.utc)
    value = max(now, latest + timedelta(microseconds=1))
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _require_terminal_namespace(paths: Mapping[str, Path], *, complete: bool) -> None:
    root = paths["terminalRoot"]
    expected = {paths["sourceSelection"].name, paths["sourceClosure"].name}
    if not os.path.lexists(root):
        if complete:
            raise FileNotFoundError("early-terminal namespace is absent")
        return
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise ValueError("early-terminal root is not one safe directory")
    actual = {item.name for item in root.iterdir()}
    required = expected if complete else set()
    if actual != required:
        raise ValueError(
            f"early-terminal namespace inventory changed: actual={sorted(actual)} expected={sorted(required)}"
        )
    if complete:
        _identity(paths["sourceSelection"], "early source selection")
        _identity(paths["sourceClosure"], "early source closure")


def _publish_terminal_locked(
    protocol: Mapping[str, Any],
    protocol_identity: Mapping[str, Any],
    trainer: Any,
    paths: Mapping[str, Path],
    *,
    operator_id: str | None = None,
    abort_reason: str | None = None,
    abort_lifetime_lock: _LifetimeLock | None = None,
) -> dict[str, Any]:
    _require_terminal_namespace(paths, complete=False)
    classification = _classify_terminal_locked(
        protocol,
        protocol_identity,
        trainer,
        paths,
        operator_id=operator_id,
        abort_reason=abort_reason,
        abort_lifetime_lock=abort_lifetime_lock,
    )
    latest = _evidence_latest(classification, protocol)
    selection_created = _after(latest)
    selection = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SOURCE_SELECTION_KIND,
        "profileId": PROFILE_ID,
        "createdUtc": selection_created,
        "protocol": protocol_identity,
        "terminalClass": classification["terminalClass"],
        "promotionStatus": classification["promotionStatus"],
        "generation5Selection": classification["generation5Selection"],
        "selectedCandidateId": classification["selectedCandidateId"],
        "selectedModel": classification["selectedModel"],
        "operatorAuthorization": _operator_authorization(classification),
        "terminalEvidence": classification["terminalEvidence"],
        "informationBoundary": _information_boundary(classification),
        "finalStageSeal": True,
    }
    paths["terminalRoot"].mkdir(parents=True, exist_ok=False)
    _exclusive_json(paths["sourceSelection"], selection)
    selection_identity = _identity(paths["sourceSelection"], "early source selection")
    closure_created = (
        _parse_utc(selection_created, "source selection createdUtc")
        + timedelta(microseconds=1)
    ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    closure = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SOURCE_CLOSURE_KIND,
        "profileId": PROFILE_ID,
        "createdUtc": closure_created,
        "protocol": protocol_identity,
        "selectionSeal": selection_identity,
        "status": SOURCE_CLOSURE_STATUS,
        "terminalClass": classification["terminalClass"],
        "promotionStatus": classification["promotionStatus"],
        "selectedCandidateId": classification["selectedCandidateId"],
        "selectedModel": classification["selectedModel"],
        "operatorAuthorization": _operator_authorization(classification),
        "terminalEvidence": classification["terminalEvidence"],
        "matchAuthorizationPresent": False,
        "compatibilityClosurePresent": False,
        "retryPermitted": False,
        "targetValuesExported": 0,
        "generation6TargetRowsDecoded": 0,
        "gameResultsRead": False,
        "resultInformationRead": False,
        "finalStageSeal": True,
    }
    _exclusive_json(paths["sourceClosure"], closure)
    return _verify_terminal_locked(
        protocol,
        protocol_identity,
        trainer,
        paths,
        operator_id=operator_id,
        abort_reason=abort_reason,
        abort_lifetime_lock=abort_lifetime_lock,
    )


def _verify_terminal_locked(
    protocol: Mapping[str, Any],
    protocol_identity: Mapping[str, Any],
    trainer: Any,
    paths: Mapping[str, Path],
    *,
    operator_id: str | None = None,
    abort_reason: str | None = None,
    abort_lifetime_lock: _LifetimeLock | None = None,
) -> dict[str, Any]:
    _reject_match_authority(paths)
    _require_terminal_namespace(paths, complete=True)
    selection = _load_json(paths["sourceSelection"], "early source selection")
    closure = _load_json(paths["sourceClosure"], "early source closure")
    if set(selection) != SELECTION_FIELDS or set(closure) != CLOSURE_FIELDS:
        raise ValueError("early-terminal output field inventory changed")
    selection_identity = _identity(paths["sourceSelection"], "early source selection")
    bound_operator = selection.get("operatorAuthorization")
    if operator_id is None and bound_operator is not None:
        if type(bound_operator) is not dict or set(bound_operator) != {
            "operatorId",
            "abortReason",
        }:
            raise ValueError("bound operator authorization changed")
        operator_id = bound_operator["operatorId"]
        abort_reason = bound_operator["abortReason"]
    classification = _classify_terminal_locked(
        protocol,
        protocol_identity,
        trainer,
        paths,
        operator_id=operator_id,
        abort_reason=abort_reason,
        abort_lifetime_lock=abort_lifetime_lock,
    )
    expected_selection = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SOURCE_SELECTION_KIND,
        "profileId": PROFILE_ID,
        "protocol": protocol_identity,
        "terminalClass": classification["terminalClass"],
        "promotionStatus": classification["promotionStatus"],
        "generation5Selection": classification["generation5Selection"],
        "selectedCandidateId": classification["selectedCandidateId"],
        "selectedModel": classification["selectedModel"],
        "operatorAuthorization": _operator_authorization(classification),
        "terminalEvidence": classification["terminalEvidence"],
        "informationBoundary": _information_boundary(classification),
        "finalStageSeal": True,
    }
    for key, value in expected_selection.items():
        if selection.get(key) != value:
            raise ValueError(f"early source selection {key} changed")
    selection_created = _parse_utc(selection["createdUtc"], "source selection createdUtc")
    if selection_created <= _evidence_latest(classification, protocol):
        raise ValueError("early source selection does not follow its evidence")
    expected_closure = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SOURCE_CLOSURE_KIND,
        "profileId": PROFILE_ID,
        "protocol": protocol_identity,
        "selectionSeal": selection_identity,
        "status": SOURCE_CLOSURE_STATUS,
        "terminalClass": classification["terminalClass"],
        "promotionStatus": classification["promotionStatus"],
        "selectedCandidateId": classification["selectedCandidateId"],
        "selectedModel": classification["selectedModel"],
        "operatorAuthorization": _operator_authorization(classification),
        "terminalEvidence": classification["terminalEvidence"],
        "matchAuthorizationPresent": False,
        "compatibilityClosurePresent": False,
        "retryPermitted": False,
        "targetValuesExported": 0,
        "generation6TargetRowsDecoded": 0,
        "gameResultsRead": False,
        "resultInformationRead": False,
        "finalStageSeal": True,
    }
    for key, value in expected_closure.items():
        if closure.get(key) != value:
            raise ValueError(f"early source closure {key} changed")
    closure_created = _parse_utc(closure["createdUtc"], "source closure createdUtc")
    if closure_created <= selection_created:
        raise ValueError("early source closure does not follow source selection")
    result = {
        "sourceId": "G5",
        "promotionStatus": classification["promotionStatus"],
        "selectionSeal": selection_identity,
        "closure": _identity(paths["sourceClosure"], "early source closure"),
        "rawModel": None,
        "healthPassed": False,
        "sourceVerifier": _identity(Path(__file__).resolve(), "early-terminal verifier"),
        "unavailabilityEvidence": None,
        "resultInformationRead": False,
    }
    if set(result) != SOURCE_REPLAY_FIELDS:
        raise AssertionError("early source replay field inventory changed")
    return result


def publish_terminal(
    *, operator_id: str | None = None, abort_reason: str | None = None
) -> dict[str, Any]:
    protocol, protocol_identity, trainer, paths = _authority_context()
    with _open_authority_lifecycle_lock(paths["authorityLifecycleLock"]):
        abort_lock = _claim_only_abort_lock(paths)
        try:
            return _publish_terminal_locked(
                protocol,
                protocol_identity,
                trainer,
                paths,
                operator_id=operator_id,
                abort_reason=abort_reason,
                abort_lifetime_lock=abort_lock,
            )
        finally:
            if abort_lock is not None:
                abort_lock.close()


def verify_terminal(
    *, operator_id: str | None = None, abort_reason: str | None = None
) -> dict[str, Any]:
    protocol, protocol_identity, trainer, paths = _authority_context()
    with _open_authority_lifecycle_lock(paths["authorityLifecycleLock"]):
        abort_lock = _claim_only_abort_lock(paths)
        try:
            return _verify_terminal_locked(
                protocol,
                protocol_identity,
                trainer,
                paths,
                operator_id=operator_id,
                abort_reason=abort_reason,
                abort_lifetime_lock=abort_lock,
            )
        finally:
            if abort_lock is not None:
                abort_lock.close()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("run-offline")
    publish = commands.add_parser("publish")
    publish.add_argument("--operator-id")
    publish.add_argument("--abort-reason", choices=("offline-evaluation-interrupted",))
    verify = commands.add_parser("verify")
    verify.add_argument("--operator-id")
    verify.add_argument("--abort-reason", choices=("offline-evaluation-interrupted",))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if any(value is not None for value in (_TEST_REPOSITORY, _TEST_PROTOCOL, _TEST_TRAINER)):
        raise RuntimeError("test override reached the production CLI")
    args = _parse_args(argv)
    if args.command == "status":
        value = classify_terminal()
    elif args.command == "run-offline":
        value = run_offline_guarded()
    elif args.command == "publish":
        value = publish_terminal(
            operator_id=args.operator_id, abort_reason=args.abort_reason
        )
    else:
        value = verify_terminal(
            operator_id=args.operator_id, abort_reason=args.abort_reason
        )
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
