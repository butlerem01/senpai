#!/usr/bin/env python3
"""Fail-closed orchestration for the Generation-6 Omega decision-v3 capsule.

This module is deliberately an orchestrator, not another data producer.  It
publishes one immutable path/command plan, delegates semantic artifacts to the
already-reviewed producers, and enforces explicit STOP boundaries between
target-free work, the pre-target HCE freeze, teacher decoding, projection, and
capsule closure.  Long sampler, classifier, routing, and teacher commands are
printed in the plan but are never launched by ``plan``, ``claim``, or
``finalize``.

The bounded producer actions supplied here are ``materialize-hce`` and
``materialize-projection``.  The former turns exact frozen routing into the
already-defined static-HCE row schema without reading teacher targets or game
results.  The latter is target-bearing, runs only after the teacher ledger is
complete, and delegates every projection semantic to the exact-pinned teacher
authority.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import types
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v6-move-decision-v1"
INPUT_KIND = "omega-decision-v3-pipeline-inputs"
PLAN_KIND = "omega-decision-v3-pipeline-plan"
RECEIPT_KIND = "omega-decision-v3-terminal-execution-receipt"
CAPSULE_KIND = "omega-decision-v3-capsule-closure"
VERIFICATION_KIND = "omega-decision-v3-fresh-verification"
HCE_CLAIM_KIND = "omega-decision-v3-pretarget-hce-claim"
HCE_ROW_KIND = "omega-nnue-king-state-v6-static-hce"

HISTORY_SEED = 2026072201
ROUTING_SEED = 2026072403
TRAJECTORY_PAIRS = 8192
MAX_PLIES = 220
POSITIONS_PER_PHASE_SIDE = 2
CAPTURE_PERCENT = 72
WORKERS = 4
PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
SPLITS = ("train", "validation", "heldOut")
CELLS = tuple(f"{phase}:{side}" for phase in PHASES for side in SIDES)
ROOTS_PER_CELL = {"train": 512, "validation": 128, "heldOut": 128}
ROOTS_PER_SPLIT = {split: ROOTS_PER_CELL[split] * len(CELLS) for split in SPLITS}
CHILDREN_PER_ROOT = 4
PRODUCTION_INVENTORY_CONTRACT = {
    "phases": list(PHASES),
    "parentSides": list(SIDES),
    "cells": list(CELLS),
    "rootsPerCell": dict(ROOTS_PER_CELL),
    "rootsPerSplit": dict(ROOTS_PER_SPLIT),
    "childrenPerRoot": CHILDREN_PER_ROOT,
    "childrenPerSplit": {
        split: ROOTS_PER_SPLIT[split] * CHILDREN_PER_ROOT for split in SPLITS
    },
    "totalRoots": sum(ROOTS_PER_SPLIT.values()),
    "totalChildren": sum(ROOTS_PER_SPLIT.values()) * CHILDREN_PER_ROOT,
}

FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_PROCESS_STDOUT_BYTES = 16 * 1024 * 1024
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_EXTERNAL_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z$"
)
IDENTITY_FIELDS = frozenset({"path", "bytes", "sha256"})
DEPENDENCY_RECORD_FIELDS = frozenset(
    {"expectedBytes", "expectedSha256", "actual", "pinFinalized", "matches"}
)

VERIFICATION_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "capsule",
        "verifierExecutable", "verifierRunner", "verifierOptions",
        "initializerAuthority", "terminalAuthority", "priorForbiddenAuthority",
        "componentAuthority", "staticHceAuthority", "teacherLedgerAuthority",
        "projectionAuthority", "resultInformationRead",
    }
)
INITIALIZER_AUTHORITY_FIELDS = frozenset(
    {
        "manifest", "selectionMode", "selectedCatalogIndex", "selectedModel",
        "catalogSourceIds", "firstEligibleSelected", "selectionSemanticsVerified",
        "selectedPromotionHealthPassed", "sourceClosureSemanticsVerified",
        "fallbackProtocolReplayed", "g6TargetRowsDecoded", "resultInformationRead",
    }
)
TERMINAL_AUTHORITY_FIELDS = frozenset(
    {
        "lineage", "routedChildren", "terminalChildrenExcludedBeforeRouting",
        "unclassifiedChildren", "errorTextAcceptedAsTerminal",
        "rulesSemanticsReplayed", "completionSemanticsVerified",
    }
)
FORBIDDEN_AUTHORITY_FIELDS = frozenset(
    {
        "catalogs", "registry", "requiredSourceIds", "catalogPositions",
        "manifestsSemanticallyReplayed", "exactPositionOverlaps",
        "conservativeSignatureOverlaps", "sourceArtifactOverlaps",
    }
)
COMPONENT_AUTHORITY_FIELDS = frozenset(
    {"componentMap", "roots", "components", "wholeComponentSplits", "semanticsReplayed"}
)
STATIC_HCE_AUTHORITY_FIELDS = frozenset(
    {
        "claim", "completion", "teacherClaim", "prelabelSeal",
        "targetFreeRouting", "engine", "runner", "options", "transcript",
        "inputOrderSha256", "rows", "perspective", "freshReplayMatches",
        "completedBeforeTeacherClaim", "semanticsReplayed",
    }
)
TEACHER_LEDGER_AUTHORITY_FIELDS = frozenset(
    {
        "claim", "attemptLedger", "attemptLedgerCompletion", "completion",
        "budgets", "routedChildren", "attemptRecords", "successfulChildren",
        "rejectedChildren", "unresolvedChildren", "semanticsReplayed",
    }
)
PROJECTION_AUTHORITY_FIELDS = frozenset(
    {
        "plannedProducer", "plannedCorpusPath", "plannedManifestPath",
        "actualProducer", "actualCorpus", "actualManifest", "producerMatches",
        "pathsMatch", "semanticsReplayed",
    }
)
SELF_TEST_FIELDS = frozenset(
    {"schemaVersion", "kind", "profileId", "status", "dependencies", "resultInformationRead"}
)
STATIC_HCE_PERSPECTIVE = (
    "integer centipawns from child side-to-move; child side is opposite parent side"
)

# Every authority below is an exact final-reviewed size/hash pin.  A future
# unset size/hash remains fail-closed and can never act as a wildcard.
REVIEWED_PINS: dict[str, tuple[str, int | None, str | None]] = {
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
    "trainerAuthority": (
        "king_state_train_generation6.py",
        330_787,
        "81b9e0c5ffa5d78a4cf2198781ceffea7649bdaed3e827556a5e3deaeba8a2e0",
    ),
    "initializerGenerator": (
        "omega_decision_v3_initializer.py",
        19_564,
        "38d81f665d0bccb4939e3a1707b7dbfbe4c9c29795e0699f51d92bf30af94be0",
    ),
    "verifierImplementation": (
        "omega_decision_v3_verifier.py",
        151_065,
        "e0704ade94df4c7d957413101f45fed8ec3c2d2662a7a81e9dfabffa9be10576",
    ),
    "verifierRunner": (
        "verify_omega_decision_v3_upstream.py",
        4_856,
        "5f6fb24275d7dc71eb6fa3ef757a1b3ef789211ad3c4346cade941dd63c85300",
    ),
}
_LOADED_EXACT: dict[str, tuple[types.ModuleType, dict[str, Any]]] = {}

INPUT_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "authorityDirectory",
        "historySamplerExecutable", "classifierBundleManifest",
        "classifierExecutable", "classifierRunner",
        "classifierRuntimeManifest", "chessLibAssembly",
        "priorForbiddenGroups", "teacherEngine", "staticHceExecutable",
    }
)
BINDING_FIELDS = frozenset(
    {
        "historySamplerExecutable", "classifierBundleManifest",
        "classifierExecutable", "classifierRunner", "classifierRuntimeManifest",
        "chessLibAssembly", "teacherEngine", "staticHceExecutable",
    }
)
FORBIDDEN_GROUP_FIELDS = frozenset({"coveredSourceIds", "manifest"})

CANONICAL_RELATIVE_PATHS: Mapping[str, str] = {
    "plan": "00-plan/pipeline.plan.json",
    "historyRoots": "10-history/history-roots.jsonl",
    "historyRootsManifest": "10-history/history-roots.jsonl.manifest.json",
    "terminalClaim": "20-terminal/terminal.claim.json",
    "terminalNativeManifest": "20-terminal/terminal.native-manifest.json",
    "terminalTranscript": "20-terminal/terminal.transcript.jsonl",
    "terminalEligibleRoots": "20-terminal/terminal.eligible-roots.jsonl",
    "terminalEligibleChildren": "20-terminal/terminal.eligible-children.jsonl",
    "terminalStdout": "20-terminal/terminal.stdout.txt",
    "terminalStderr": "20-terminal/terminal.stderr.txt",
    "terminalLineage": "20-terminal/terminal.lineage.json",
    "targetFreeRouting": "30-routing/target-free-routing.jsonl",
    "componentMap": "30-routing/component-map.jsonl",
    "sourceRootManifest": "30-routing/source-root.manifest.json",
    "sourceChildrenManifest": "30-routing/source-children.manifest.json",
    "routingCompletion": "30-routing/routing.completion.json",
    "initializerSelection": "40-initializer/initializer.selection.json",
    "initializerClosure": "40-initializer/initializer.closure.json",
    "initializerModel": "40-initializer/initializer.nnue",
    "initializerManifest": "40-initializer/initializer.manifest.json",
    "priorForbiddenRegistry": "50-pretarget/prior-forbidden.registry.json",
    "prelabelSeal": "50-pretarget/prelabel.seal.json",
    "staticHceOptions": "50-pretarget/static-hce.options.json",
    "preTargetHceClaim": "50-pretarget/static-hce.claim.json",
    "staticHceTranscript": "50-pretarget/static-hce.jsonl",
    "preTargetHceCompletion": "50-pretarget/static-hce.completion.json",
    "teacherOptions": "60-teacher/teacher.options.json",
    "teacherClaim": "60-teacher/teacher.claim.json",
    "teacherAttemptLedger": "60-teacher/teacher.attempts.jsonl",
    "teacherAttemptLedgerCompletion": "60-teacher/teacher.attempts.completion.json",
    "teacherLabels": "60-teacher/teacher.labels.jsonl",
    "teacherManifest": "60-teacher/teacher.manifest.json",
    "teacherCompletion": "60-teacher/teacher.completion.json",
    "projectedCorpus": "70-projection/decision-v3.projected.jsonl",
    "labelManifest": "70-projection/decision-v3.label-manifest.json",
    "staticHceManifest": "70-projection/static-hce.manifest.json",
    "verifierOptions": "80-capsule/verifier.options.json",
    "capsule": "80-capsule/decision-v3.capsule.json",
    "verificationReceipt": "80-capsule/decision-v3.verification.json",
}

PLAN_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "authorityDirectory", "producer", "dependencies", "bindings",
        "priorForbiddenGroups", "paths", "runbook", "productionInventoryContract",
        "targetRowsDecodedAtPlan",
        "targetFieldsDecodedAtPlan", "resultInformationRead", "finalStageSeal",
    }
)


def _canonical_json(value: Any, *, newline: bool = True) -> bytes:
    text = json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    )
    if newline:
        text += "\n"
    return text.encode("utf-8")


def _exact_keys(value: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    if type(value) is not dict or set(value) != set(fields):
        missing = sorted(set(fields) - set(value)) if isinstance(value, dict) else []
        extra = sorted(set(value) - set(fields)) if isinstance(value, dict) else []
        raise ValueError(f"{label} field inventory changed: missing={missing} extra={extra}")


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


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _safe_path(path: Path, *, must_exist: bool) -> Path:
    absolute = _absolute(path)
    if not path.is_absolute() or os.fspath(path) != os.fspath(absolute):
        raise ValueError(f"path is not lexical canonical absolute: {path}")
    existing = absolute if must_exist else next(
        (parent for parent in (absolute, *absolute.parents) if os.path.lexists(parent)),
        None,
    )
    if existing is None:
        raise ValueError(f"path has no existing ancestor: {absolute}")
    for item in (*reversed(existing.parents), existing):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"path traverses a link/reparse point: {item}")
    if must_exist:
        if not os.path.lexists(absolute):
            raise ValueError(f"required regular file is missing: {absolute}")
        info = os.lstat(absolute)
        if (
            not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or _is_reparse(info) or getattr(info, "st_nlink", 1) != 1
        ):
            raise ValueError(f"required path is not one private regular file: {absolute}")
    return absolute


def _safe_directory(path: Path) -> Path:
    absolute = _absolute(path)
    if not path.is_absolute() or os.fspath(path) != os.fspath(absolute):
        raise ValueError(f"directory is not lexical canonical absolute: {path}")
    if not os.path.isdir(absolute):
        raise ValueError(f"required directory is missing: {absolute}")
    for item in (*reversed(absolute.parents), absolute):
        info = os.lstat(item)
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or _is_reparse(info)
        ):
            raise ValueError(f"directory traverses a link/reparse point: {item}")
    return absolute


def _stat_state(info: os.stat_result) -> tuple[Any, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_size,
        getattr(info, "st_mtime_ns", None), getattr(info, "st_ctime_ns", None),
        getattr(info, "st_nlink", 1), getattr(info, "st_file_attributes", 0),
    )


def _validate_private_descriptor(
    info: os.stat_result, absolute: Path, label: str,
) -> None:
    if (
        not stat.S_ISREG(info.st_mode) or _is_reparse(info)
        or getattr(info, "st_nlink", 1) != 1
    ):
        raise ValueError(f"{label} is not one private regular file: {absolute}")


def _read_stable(
    path: Path, *, capture: bool, max_bytes: int | None = None,
) -> tuple[dict[str, Any], bytes | None]:
    absolute = _safe_path(path, must_exist=True)
    before = os.lstat(absolute)
    _validate_private_descriptor(before, absolute, "authority")
    descriptor = os.open(
        absolute,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        _validate_private_descriptor(opened, absolute, "opened authority")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"authority changed before open: {absolute}")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            if capture:
                chunks.append(block)
            digest.update(block)
            total += len(block)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(f"authority is oversized: {absolute}")
        after = os.fstat(descriptor)
        _validate_private_descriptor(after, absolute, "completed authority")
        if _stat_state(after) != _stat_state(opened):
            raise ValueError(f"authority changed while read: {absolute}")
    finally:
        os.close(descriptor)
    _safe_directory(absolute.parent)
    named = os.lstat(absolute)
    _validate_private_descriptor(named, absolute, "named authority")
    if _stat_state(named) != _stat_state(before):
        raise ValueError(f"authority path changed while read: {absolute}")
    identity = {"path": str(absolute), "bytes": total, "sha256": digest.hexdigest()}
    return identity, b"".join(chunks) if capture else None


def _snapshot(path: Path) -> tuple[dict[str, Any], bytes]:
    identity, payload = _read_stable(path, capture=True, max_bytes=MAX_JSON_BYTES)
    assert payload is not None
    return identity, payload


def _identity(path: Path) -> dict[str, Any]:
    identity, _ = _read_stable(path, capture=False)
    return identity


def _verify_identity(value: Any, label: str) -> dict[str, Any]:
    _exact_keys(value, IDENTITY_FIELDS, label)
    if (
        type(value["path"]) is not str or type(value["bytes"]) is not int
        or value["bytes"] < 0 or type(value["sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity changed")
    actual = _identity(Path(value["path"]))
    if not _type_exact_equal(actual, value):
        raise ValueError(f"{label} changed after freeze")
    return actual


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    identity, payload = _snapshot(path)
    try:
        text = payload.decode("utf-8")
        value = json.loads(text, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from error
    if type(value) is not dict or _canonical_json(value) != payload:
        raise ValueError(f"{label} is not one canonical JSON object")
    return value, identity


def _exclusive_bytes(path: Path, payload: bytes) -> dict[str, Any]:
    absolute = _safe_path(path, must_exist=False)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    _safe_directory(absolute.parent)
    descriptor = os.open(
        absolute,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        opened = os.fstat(descriptor)
        _validate_private_descriptor(opened, absolute, "publication descriptor")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("exclusive publication made no forward progress")
            offset += written
        os.fsync(descriptor)
        completed = os.fstat(descriptor)
        _validate_private_descriptor(completed, absolute, "completed publication")
        if (
            (completed.st_dev, completed.st_ino) != (opened.st_dev, opened.st_ino)
            or completed.st_size != len(payload)
        ):
            raise ValueError(f"exclusive publication descriptor changed: {absolute}")
    finally:
        os.close(descriptor)
    # Never remove a failed or partial O_EXCL publication: after publication
    # starts, the pathname is immutable evidence and deletion would race an
    # adversarial replacement.  A retry must inspect and reject that evidence.
    _safe_directory(absolute.parent)
    named = os.lstat(absolute)
    _validate_private_descriptor(named, absolute, "published pathname")
    if (named.st_dev, named.st_ino) != (completed.st_dev, completed.st_ino):
        raise ValueError(f"exclusive publication pathname changed: {absolute}")
    identity = _identity(absolute)
    expected = {
        "path": str(absolute), "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if identity != expected:
        raise ValueError(f"exclusive publication changed: {absolute}")
    return identity


def _exclusive_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    return _exclusive_bytes(path, _canonical_json(dict(value)))


def _publish_or_verify(path: Path, value: Mapping[str, Any], label: str) -> dict[str, Any]:
    absolute = _absolute(path)
    if os.path.lexists(absolute):
        actual, identity = _load_json(absolute, label)
        if not _type_exact_equal(actual, dict(value)):
            raise ValueError(f"existing {label} differs from deterministic replay")
        return identity
    return _exclusive_json(absolute, value)


def _publish_or_verify_bytes(path: Path, payload: bytes, label: str) -> dict[str, Any]:
    absolute = _absolute(path)
    expected = {
        "path": str(absolute), "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if os.path.lexists(absolute):
        actual = _identity(absolute)
        if actual != expected:
            raise ValueError(f"existing {label} differs from deterministic replay")
        return actual
    return _exclusive_bytes(absolute, payload)


def _parse_timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{label} must be canonical UTC with six fractional digits")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return parsed


def _timestamp_after(value: str, microseconds: int) -> str:
    result = _parse_timestamp(value, "base createdUtc") + timedelta(microseconds=microseconds)
    return result.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_external_timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or _EXTERNAL_TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{label} is not a canonical UTC timestamp")
    main = value[:-1]
    if "." in main:
        prefix, fraction = main.rsplit(".", 1)
        # .NET may emit seven fractional digits; Python datetime stores six.
        main = prefix + "." + (fraction + "000000")[:6]
    return datetime.fromisoformat(main).replace(tzinfo=timezone.utc)


def _require_after(value: str, documents: Sequence[tuple[Path, str]]) -> None:
    candidate = _parse_timestamp(value, "stage createdUtc")
    for path, label in documents:
        document, _ = _load_json(path, label)
        created = _parse_external_timestamp(document.get("createdUtc"), f"{label} createdUtc")
        if candidate <= created:
            raise ValueError(f"stage createdUtc must follow {label}")


def _tool_dir() -> Path:
    return Path(__file__).resolve().parent


def _dependency_records() -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name, (filename, expected_bytes, expected_sha) in REVIEWED_PINS.items():
        actual = _identity(_tool_dir() / filename)
        finalized = type(expected_bytes) is int and type(expected_sha) is str
        matches = bool(
            finalized and actual["bytes"] == expected_bytes and actual["sha256"] == expected_sha
        )
        records[name] = {
            "expectedBytes": expected_bytes,
            "expectedSha256": expected_sha,
            "actual": actual,
            "pinFinalized": finalized,
            "matches": matches,
        }
    return records


def _assert_distinct_bound_inputs(
    bindings: Mapping[str, Mapping[str, Any]],
    groups: Sequence[Mapping[str, Any]],
    planned_paths: Mapping[str, str],
) -> None:
    roles = {
        **{name: Path(str(identity["path"])) for name, identity in bindings.items()},
        **{
            f"priorForbiddenManifest:{index}": Path(str(group["manifest"]["path"]))
            for index, group in enumerate(groups)
        },
    }
    lexical: set[str] = set()
    inodes: set[tuple[int, int]] = set()
    planned = {
        os.path.normcase(os.path.normpath(value))
        for value in planned_paths.values()
    }
    for role, path in roles.items():
        key = os.path.normcase(os.path.normpath(str(path)))
        info = os.lstat(path)
        inode = (info.st_dev, info.st_ino)
        if key in lexical or inode in inodes:
            raise ValueError(f"pipeline bound input roles share a path/inode: {role}")
        if key in planned:
            raise ValueError(f"pipeline bound input aliases a planned output: {role}")
        lexical.add(key)
        inodes.add(inode)


def _require_dependencies(plan: Mapping[str, Any], names: Sequence[str]) -> None:
    for name in names:
        record = plan["dependencies"].get(name)
        if type(record) is not dict or record.get("pinFinalized") is not True or record.get("matches") is not True:
            raise RuntimeError(f"dependency pin is not final and matching: {name}")
        _verify_identity(record["actual"], f"pipeline dependency {name}")


def _derived_plan_status(dependencies: Mapping[str, Any]) -> str:
    required = ("terminal", "routing", "teacher", "evaluatorRunner")
    ready = all(
        type(dependencies.get(name)) is dict
        and dependencies[name].get("pinFinalized") is True
        and dependencies[name].get("matches") is True
        for name in required
    )
    return (
        "ready-for-target-free-runbook"
        if ready else "awaiting-reviewed-target-free-pins"
    )


def _canonical_paths(directory: Path) -> dict[str, str]:
    absolute = _safe_path(directory, must_exist=False)
    return {role: str(_absolute(absolute / relative)) for role, relative in CANONICAL_RELATIVE_PATHS.items()}


def _command(executable: Path, script: Path, *arguments: str) -> list[str]:
    return [str(executable), "-I", "-B", str(script), *arguments]


def _runbook(paths: Mapping[str, str], bindings: Mapping[str, Any], groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    python = Path(bindings["staticHceExecutable"]["path"])
    pipeline = Path(__file__).resolve()
    terminal = _tool_dir() / "omega_decision_v3_terminal_lineage.py"
    routing = _tool_dir() / "omega_decision_v3_routing.py"
    teacher = _tool_dir() / "omega_decision_v3_teacher.py"
    history_command = [
        bindings["classifierRunner"]["path"], bindings["historySamplerExecutable"]["path"],
        "--output", paths["historyRoots"], "--seed", str(HISTORY_SEED),
        "--trajectory-pairs", str(TRAJECTORY_PAIRS), "--max-plies", str(MAX_PLIES),
        "--positions-per-phase-side", str(POSITIONS_PER_PHASE_SIDE),
        "--capture-percent", str(CAPTURE_PERCENT), "--workers", str(WORKERS),
    ]
    terminal_command = [
        bindings["classifierRunner"]["path"], bindings["classifierExecutable"]["path"],
        "--input", paths["historyRoots"], "--transcript", paths["terminalTranscript"],
        "--eligible-roots", paths["terminalEligibleRoots"], "--eligible-children", paths["terminalEligibleChildren"],
        "--manifest", paths["terminalNativeManifest"],
    ]
    routing_command = _command(
        python, routing, "build", "--eligible-roots", paths["terminalEligibleRoots"],
        "--eligible-children", paths["terminalEligibleChildren"], "--terminal-lineage", paths["terminalLineage"],
        *sum((["--prior-forbidden-manifest", item["manifest"]["path"]] for item in groups), []),
        "--routing-output", paths["targetFreeRouting"], "--component-output", paths["componentMap"],
        "--source-root-manifest-output", paths["sourceRootManifest"],
        "--source-children-manifest-output", paths["sourceChildrenManifest"],
        "--completion-output", paths["routingCompletion"], "--seed", str(ROUTING_SEED),
        "--created-utc", "<canonical-utc-after-terminal-lineage>",
    )
    plan_path = paths["plan"]
    return [
        {"sequence": 10, "stage": "history", "action": "external-command", "argv": history_command,
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: authenticate history roots and sidecar manifest before terminal claim."},
        {"sequence": 20, "stage": "terminal-claim", "action": "pipeline-command",
         "argv": _command(python, pipeline, "claim", "--plan", plan_path, "--stage", "terminal", "--created-utc", "<canonical-utc>"),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: terminal claim must exist before classifier execution."},
        {"sequence": 30, "stage": "terminal-classifier", "action": "external-command", "argv": terminal_command,
         "stdoutPath": paths["terminalStdout"], "stderrPath": paths["terminalStderr"],
         "stopAfter": "STOP: record exact start/completion UTC, exit 0, and no timeout before terminal finalize."},
        {"sequence": 40, "stage": "terminal-finalize", "action": "pipeline-command",
         "argv": _command(python, pipeline, "finalize", "--plan", plan_path, "--stage", "terminal",
                          "--started-utc", "<canonical-utc>", "--completed-utc", "<canonical-utc>", "--created-utc", "<canonical-utc>"),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: terminal lineage must freshly replay before routing."},
        {"sequence": 50, "stage": "routing", "action": "external-command", "argv": routing_command,
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: run routing finalize; do not decode teacher targets."},
        {"sequence": 60, "stage": "routing-finalize", "action": "pipeline-command",
         "argv": _command(python, pipeline, "finalize", "--plan", plan_path, "--stage", "routing"),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: independently publish the four exact initializer authority files at their canonical paths."},
        {"sequence": 70, "stage": "initializer", "action": "required-artifacts", "argv": [],
         "requiredPaths": [paths[name] for name in ("initializerSelection", "initializerClosure", "initializerModel", "initializerManifest")],
         "artifactContract": {
             "manifestKind": "omega-nnue-king-state-v6-initializer-manifest",
             "manifestStatus": "frozen-pre-g6-initializer-selection",
             "orderedCatalog": ["G5", "G2-K2"],
             "selectionModes": ["promoted-prior", "deterministic-fallback"],
             "validation": "fresh source-specific exact-pinned verifier replay before prelabel publication",
             "g6TargetRowsDecoded": 0,
             "gameResultsRead": False,
         },
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: initializer manifest must pass source-specific fresh replay before pretarget claim."},
        {"sequence": 80, "stage": "pretarget-claim", "action": "pipeline-command",
         "argv": _command(python, pipeline, "claim", "--plan", plan_path, "--stage", "pretarget", "--created-utc", "<canonical-utc>"),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: prelabel and HCE claim must precede any HCE/teacher target row."},
        {"sequence": 90, "stage": "pretarget-hce", "action": "pipeline-command",
         "argv": _command(python, pipeline, "materialize-hce", "--plan", plan_path),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: seal and freshly replay pretarget HCE before teacher claim."},
        {"sequence": 100, "stage": "pretarget-finalize", "action": "pipeline-command",
         "argv": _command(python, pipeline, "finalize", "--plan", plan_path, "--stage", "pretarget", "--created-utc", "<canonical-utc>"),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP BOUNDARY: teacher target-bearing work remains forbidden until this succeeds."},
        {"sequence": 110, "stage": "teacher-claim", "action": "pipeline-command",
         "argv": _command(python, pipeline, "claim", "--plan", plan_path, "--stage", "teacher", "--created-utc", "<canonical-utc>"),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: inspect immutable teacher claim before search."},
        {"sequence": 120, "stage": "teacher-run", "action": "external-command",
         "argv": _command(python, teacher, "run", "--claim", paths["teacherClaim"], "--ledger", paths["teacherAttemptLedger"]),
         "resumeArgv": _command(python, teacher, "resume", "--claim", paths["teacherClaim"], "--ledger", paths["teacherAttemptLedger"]),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: require complete shallow/deep ledger coverage before finalize."},
        {"sequence": 130, "stage": "materialize-projection", "action": "pipeline-command",
         "argv": _command(python, pipeline, "materialize-projection", "--plan", plan_path,
                          "--created-utc", "<canonical-utc-after-teacher-manifest>"),
         "stdoutPath": None, "stderrPath": None,
         "stopAfter": "STOP: require the exact-pinned teacher projection and manifest before teacher closure."},
        {"sequence": 140, "stage": "teacher-finalize", "action": "pipeline-command",
         "argv": _command(python, pipeline, "finalize", "--plan", plan_path, "--stage", "teacher"),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "STOP: teacher completion must bind the exact projection before capsule closure."},
        {"sequence": 150, "stage": "capsule-finalize", "action": "pipeline-command",
         "argv": _command(python, pipeline, "finalize", "--plan", plan_path, "--stage", "capsule", "--created-utc", "<canonical-utc>"),
         "stdoutPath": None, "stderrPath": None, "stopAfter": "FINAL STOP: accept only if a fresh exact-pinned verifier receipt is published."},
    ]


def _parse_inputs(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    document, identity = _load_json(path, "pipeline inputs")
    _exact_keys(document, INPUT_FIELDS, "pipeline inputs")
    if document["schemaVersion"] != 1 or type(document["schemaVersion"]) is not int or document["kind"] != INPUT_KIND or document["profileId"] != PROFILE_ID:
        raise ValueError("pipeline input header changed")
    directory = Path(document["authorityDirectory"])
    _safe_path(directory, must_exist=False)
    return document, identity


def expected_plan(inputs_path: Path, *, created_utc: str) -> dict[str, Any]:
    _parse_timestamp(created_utc, "plan createdUtc")
    inputs, _ = _parse_inputs(inputs_path)
    directory = Path(inputs["authorityDirectory"])
    path_map = _canonical_paths(directory)
    bindings: dict[str, dict[str, Any]] = {}
    for field in (
        "historySamplerExecutable", "classifierBundleManifest", "classifierExecutable",
        "classifierRunner", "classifierRuntimeManifest", "chessLibAssembly",
        "teacherEngine", "staticHceExecutable",
    ):
        raw = inputs[field]
        if type(raw) is not str:
            raise ValueError(f"pipeline input {field} is not a path")
        bindings[field] = _identity(Path(raw))
    runtime_executable = _identity(Path(sys.executable))
    if not _type_exact_equal(bindings["staticHceExecutable"], runtime_executable):
        raise ValueError(
            "static-HCE/verifier executable must be the exact Python runtime "
            "that publishes the immutable plan"
        )
    groups_value = inputs["priorForbiddenGroups"]
    if type(groups_value) is not list or not groups_value:
        raise ValueError("pipeline inputs have no prior-forbidden groups")
    groups: list[dict[str, Any]] = []
    flattened: list[str] = []
    for index, value in enumerate(groups_value):
        _exact_keys(value, FORBIDDEN_GROUP_FIELDS, f"prior-forbidden group {index}")
        sources = value["coveredSourceIds"]
        if type(sources) is not list or not sources or any(type(item) is not str or not item for item in sources):
            raise ValueError("prior-forbidden source IDs changed")
        flattened.extend(sources)
        groups.append({"coveredSourceIds": list(sources), "manifest": _identity(Path(value["manifest"]))})
    if flattened != ["G3", "G4", "G5"]:
        raise ValueError("prior-forbidden groups must cover exactly G3,G4,G5 in order")
    dependencies = _dependency_records()
    _assert_distinct_bound_inputs(bindings, groups, path_map)
    status = _derived_plan_status(dependencies)
    return {
        "schemaVersion": 1,
        "kind": PLAN_KIND,
        "profileId": PROFILE_ID,
        "status": status,
        "createdUtc": created_utc,
        "authorityDirectory": str(_absolute(directory)),
        "producer": _identity(Path(__file__).resolve()),
        "dependencies": dependencies,
        "bindings": bindings,
        "priorForbiddenGroups": groups,
        "paths": path_map,
        "runbook": _runbook(path_map, bindings, groups),
        "productionInventoryContract": dict(PRODUCTION_INVENTORY_CONTRACT),
        "targetRowsDecodedAtPlan": 0,
        "targetFieldsDecodedAtPlan": 0,
        "resultInformationRead": False,
        "finalStageSeal": True,
    }


def publish_plan(inputs: Path, output: Path, *, created_utc: str) -> dict[str, Any]:
    document = expected_plan(inputs, created_utc=created_utc)
    if str(_absolute(output)) != document["paths"]["plan"]:
        raise ValueError("plan output is not the canonical authority path")
    if os.path.lexists(_absolute(output)):
        existing = verify_plan(output)
        if not _type_exact_equal(existing, document):
            raise ValueError("existing plan differs from requested immutable plan")
        return existing
    occupied = [
        value for role, value in document["paths"].items()
        if role != "plan" and os.path.lexists(value)
    ]
    if occupied:
        raise FileExistsError(
            "new pipeline plan requires absent canonical outputs: "
            + ", ".join(sorted(occupied)[:8])
        )
    _exclusive_json(output, document)
    return verify_plan(output)


def verify_plan(path: Path) -> dict[str, Any]:
    document, _ = _load_json(path, "pipeline plan")
    _exact_keys(document, PLAN_FIELDS, "pipeline plan")
    if (
        document["schemaVersion"] != 1 or type(document["schemaVersion"]) is not int
        or document["kind"] != PLAN_KIND or document["profileId"] != PROFILE_ID
        or document["status"] not in {"ready-for-target-free-runbook", "awaiting-reviewed-target-free-pins"}
        or document["targetRowsDecodedAtPlan"] != 0 or type(document["targetRowsDecodedAtPlan"]) is not int
        or document["targetFieldsDecodedAtPlan"] != 0 or type(document["targetFieldsDecodedAtPlan"]) is not int
        or document["resultInformationRead"] is not False or document["finalStageSeal"] is not True
        or not _type_exact_equal(document["productionInventoryContract"], PRODUCTION_INVENTORY_CONTRACT)
    ):
        raise ValueError("pipeline plan header changed")
    _parse_timestamp(document["createdUtc"], "plan createdUtc")
    _verify_identity(document["producer"], "pipeline plan producer")
    if document["producer"] != _identity(Path(__file__).resolve()):
        raise ValueError("pipeline plan was not produced by this exact module")
    directory = Path(document["authorityDirectory"])
    expected_paths = _canonical_paths(directory)
    if not _type_exact_equal(document["paths"], expected_paths) or document["paths"]["plan"] != str(_absolute(path)):
        raise ValueError("pipeline plan canonical paths changed")
    for name, record in document["dependencies"].items():
        if name not in REVIEWED_PINS or type(record) is not dict:
            raise ValueError("pipeline dependency inventory changed")
        _exact_keys(record, DEPENDENCY_RECORD_FIELDS, f"pipeline dependency {name}")
        _verify_identity(record["actual"], f"pipeline dependency {name}")
        filename, expected_bytes, expected_sha = REVIEWED_PINS[name]
        expected = {
            "expectedBytes": expected_bytes, "expectedSha256": expected_sha,
            "actual": _identity(_tool_dir() / filename),
            "pinFinalized": type(expected_bytes) is int and type(expected_sha) is str,
            "matches": bool(type(expected_bytes) is int and type(expected_sha) is str
                            and record["actual"]["bytes"] == expected_bytes
                            and record["actual"]["sha256"] == expected_sha),
        }
        if not _type_exact_equal(record, expected):
            raise ValueError(f"pipeline dependency record changed: {name}")
    if set(document["dependencies"]) != set(REVIEWED_PINS):
        raise ValueError("pipeline dependency inventory changed")
    if document["status"] != _derived_plan_status(document["dependencies"]):
        raise ValueError("pipeline plan status differs from exact dependency pins")
    if type(document["bindings"]) is not dict or set(document["bindings"]) != set(BINDING_FIELDS):
        raise ValueError("pipeline binding inventory changed")
    for name, identity in document["bindings"].items():
        _verify_identity(identity, f"pipeline binding {name}")
    if type(document["priorForbiddenGroups"]) is not list:
        raise ValueError("pipeline prior-forbidden group inventory changed")
    covered_sources: list[str] = []
    for index, group in enumerate(document["priorForbiddenGroups"]):
        _exact_keys(group, FORBIDDEN_GROUP_FIELDS, f"plan prior group {index}")
        sources = group["coveredSourceIds"]
        if (
            type(sources) is not list or not sources
            or any(type(source) is not str or not source for source in sources)
        ):
            raise ValueError("pipeline prior-forbidden source inventory changed")
        covered_sources.extend(sources)
        _verify_identity(group["manifest"], f"plan prior manifest {index}")
    if covered_sources != ["G3", "G4", "G5"]:
        raise ValueError("pipeline prior-forbidden groups changed")
    _assert_distinct_bound_inputs(
        document["bindings"], document["priorForbiddenGroups"], expected_paths
    )
    if not _type_exact_equal(document["runbook"], _runbook(expected_paths, document["bindings"], document["priorForbiddenGroups"])):
        raise ValueError("pipeline runbook changed")
    return document


def _paths(plan: Mapping[str, Any]) -> dict[str, Path]:
    return {name: Path(value) for name, value in plan["paths"].items()}


def _require_routing_inventory(trainer: Any, paths: Mapping[str, Path]) -> None:
    if hasattr(trainer, "_parse_target_free_routing"):
        routes = trainer._parse_target_free_routing(paths["targetFreeRouting"])
        components = trainer._parse_component_map(paths["componentMap"])
    else:
        route_rows, _ = trainer._parse_routing_output(paths["targetFreeRouting"])
        component_rows, _ = trainer._parse_component_output(paths["componentMap"])
        routes = {row["rootId"]: row for row in route_rows}
        components = {row["rootId"]: row for row in component_rows}
    if set(routes) != set(components):
        raise ValueError("production routing/component root inventories differ")
    counts = {
        split: {cell: 0 for cell in CELLS}
        for split in SPLITS
    }
    children = {split: 0 for split in SPLITS}
    for root_id, route in routes.items():
        component = components[root_id]
        split = component["split"]
        cell = f"{route['phase']}:{route['parentSideToMove']}"
        if split not in counts or cell not in counts[split]:
            raise ValueError("production routing has an unknown split/cell")
        if type(route["children"]) is not list or len(route["children"]) != CHILDREN_PER_ROOT:
            raise ValueError("production routing root does not have exactly four children")
        counts[split][cell] += 1
        children[split] += len(route["children"])
    for split in SPLITS:
        if any(counts[split][cell] != ROOTS_PER_CELL[split] for cell in CELLS):
            raise ValueError(f"production routing {split} phase/side quotas changed")
        if sum(counts[split].values()) != ROOTS_PER_SPLIT[split]:
            raise ValueError(f"production routing {split} root total changed")
        if children[split] != ROOTS_PER_SPLIT[split] * CHILDREN_PER_ROOT:
            raise ValueError(f"production routing {split} child total changed")


def _require_capsule_inventories(label: Mapping[str, Any]) -> None:
    if type(label.get("childrenPerRoot")) is not int or label["childrenPerRoot"] != CHILDREN_PER_ROOT:
        raise ValueError("label manifest children-per-root changed")
    if type(label.get("rows")) is not int or label["rows"] != PRODUCTION_INVENTORY_CONTRACT["totalChildren"]:
        raise ValueError("label manifest total child rows changed")
    roots = label.get("rootInventories")
    cells = label.get("phaseSideInventories")
    if type(roots) is not dict or set(roots) != set(SPLITS):
        raise ValueError("label manifest split inventory changed")
    if type(cells) is not dict or set(cells) != set(SPLITS):
        raise ValueError("label manifest cell inventory changed")
    for split in SPLITS:
        inventory = roots[split]
        if (
            type(inventory) is not dict
            or type(inventory.get("roots")) is not int
            or inventory["roots"] != ROOTS_PER_SPLIT[split]
            or type(inventory.get("children")) is not int
            or inventory["children"] != ROOTS_PER_SPLIT[split] * CHILDREN_PER_ROOT
        ):
            raise ValueError(f"label manifest {split} totals changed")
        if type(cells[split]) is not dict or set(cells[split]) != set(CELLS):
            raise ValueError(f"label manifest {split} cell keys changed")
        for cell in CELLS:
            cell_inventory = cells[split][cell]
            if (
                type(cell_inventory) is not dict
                or type(cell_inventory.get("roots")) is not int
                or cell_inventory["roots"] != ROOTS_PER_CELL[split]
            ):
                raise ValueError(f"label manifest {split}/{cell} quota changed")


def _load_exact_reviewed(key: str) -> types.ModuleType:
    filename, expected_bytes, expected_sha = REVIEWED_PINS[key]
    if type(expected_bytes) is not int or type(expected_sha) is not str:
        raise RuntimeError(f"dependency pin is not finalized: {key}")
    path = _tool_dir() / filename
    identity, payload = _snapshot(path)
    if identity["bytes"] != expected_bytes or identity["sha256"] != expected_sha:
        raise RuntimeError(f"reviewed dependency differs from exact pin: {key}")
    if key in _LOADED_EXACT:
        module, loaded_identity = _LOADED_EXACT[key]
        if loaded_identity != identity:
            raise ValueError(f"reviewed dependency changed after exact load: {key}")
        return module
    name = f"_omega_decision_v3_pipeline_{key}_{identity['sha256'][:16]}"
    if name in sys.modules:
        raise RuntimeError(f"refusing a preloaded reviewed dependency: {key}")
    module = types.ModuleType(name)
    module.__file__ = identity["path"]
    module.__package__ = ""
    module.__loader__ = None
    module.__spec__ = None
    sys.modules[name] = module
    try:
        exec(compile(payload, identity["path"], "exec", dont_inherit=True), module.__dict__)
        if _identity(path) != identity:
            raise ValueError(f"reviewed dependency changed during load: {key}")
        _LOADED_EXACT[key] = (module, identity)
        return module
    except BaseException:
        sys.modules.pop(name, None)
        raise


def _import_authorities(
    required: Sequence[str] = ("trainerAuthority", "routing", "teacher", "terminal"),
) -> tuple[Any | None, Any | None, Any | None, Any | None]:
    # Execute exact descriptor snapshots under private module names.  This
    # prevents a preloaded same-named module or a check/use source race from
    # crossing the orchestration boundary.
    wanted = set(required)
    return tuple(
        _load_exact_reviewed(key) if key in wanted else None
        for key in ("trainerAuthority", "routing", "teacher", "terminal")
    )  # type: ignore[return-value]


def _verify_initializer_source_authority(paths: Mapping[str, Path]) -> dict[str, Any]:
    # This verifier routine performs the source-specific G5/G2-K2 closure and
    # health replays (or regenerates the exact fallback bytes).  It does not
    # need a capsule and decodes zero G6 target rows, so it is safe—and
    # mandatory—before the prelabel seal.
    verifier = _load_exact_reviewed("verifierImplementation")
    identities = {
        "initializerManifest": _identity(paths["initializerManifest"]),
        "initializerSelection": _identity(paths["initializerSelection"]),
        "initializerClosure": _identity(paths["initializerClosure"]),
        "initializerModel": _identity(paths["initializerModel"]),
    }
    manifest, authority = verifier._verify_initializer(
        paths["initializerManifest"], {}, identities
    )
    if (
        authority.get("sourceClosureSemanticsVerified") is not True
        or authority.get("selectionSemanticsVerified") is not True
        or authority.get("g6TargetRowsDecoded") != 0
        or authority.get("resultInformationRead") is not False
    ):
        raise ValueError("initializer fresh source-specific authority replay changed")
    return manifest


def claim_stage(plan_path: Path, stage: str, *, created_utc: str) -> dict[str, Any]:
    plan = verify_plan(plan_path)
    paths = _paths(plan)
    if stage == "terminal":
        _require_dependencies(plan, ("terminal",))
        _, _, _, terminal = _import_authorities(("terminal",))
        assert terminal is not None
        _require_after(
            created_utc,
            ((paths["historyRootsManifest"], "history-root sampler manifest"),),
        )
        if os.path.lexists(paths["terminalClaim"]):
            return terminal.verify_terminal_claim(paths["terminalClaim"])
        return terminal.publish_terminal_claim(
            paths["terminalClaim"],
            history_roots=paths["historyRoots"], history_roots_manifest=paths["historyRootsManifest"],
            classifier_bundle_manifest=Path(plan["bindings"]["classifierBundleManifest"]["path"]),
            classifier_executable=Path(plan["bindings"]["classifierExecutable"]["path"]),
            classifier_runner=Path(plan["bindings"]["classifierRunner"]["path"]),
            classifier_runtime_manifest=Path(plan["bindings"]["classifierRuntimeManifest"]["path"]),
            chesslib_assembly=Path(plan["bindings"]["chessLibAssembly"]["path"]),
            producer=Path(plan["dependencies"]["terminal"]["actual"]["path"]),
            planned_native_manifest=paths["terminalNativeManifest"], planned_transcript=paths["terminalTranscript"],
            planned_eligible_roots=paths["terminalEligibleRoots"], planned_eligible_children=paths["terminalEligibleChildren"],
            planned_stdout=paths["terminalStdout"], planned_stderr=paths["terminalStderr"], created_utc=created_utc,
        )
    if stage == "pretarget":
        _require_dependencies(
            plan,
            ("terminal", "routing", "trainerAuthority", "initializerGenerator",
             "verifierImplementation", "evaluatorRunner"),
        )
        trainer, routing, _, _ = _import_authorities(
            ("trainerAuthority", "routing")
        )
        assert trainer is not None and routing is not None
        routing.verify_completion(paths["routingCompletion"])
        _require_routing_inventory(trainer, paths)
        initializer = trainer._verify_initializer_manifest(paths["initializerManifest"], paths["initializerModel"])
        if initializer["selectionSeal"] != _identity(paths["initializerSelection"]) or initializer["sourceClosure"] != _identity(paths["initializerClosure"]):
            raise ValueError("initializer manifest does not bind canonical selection/closure")
        verified_initializer = _verify_initializer_source_authority(paths)
        if not _type_exact_equal(initializer, verified_initializer):
            raise ValueError("trainer/verifier initializer interpretations differ")
        _require_after(
            created_utc,
            (
                (paths["routingCompletion"], "target-free routing completion"),
                (paths["terminalLineage"], "terminal-classifier lineage"),
                (paths["initializerManifest"], "initializer manifest"),
            ),
        )
        registry_document = trainer.expected_upstream_forbidden_registry(
            catalog_groups=[(group["coveredSourceIds"], Path(group["manifest"]["path"])) for group in plan["priorForbiddenGroups"]],
            producer=Path(__file__).resolve(), created_utc=created_utc,
        )
        _publish_or_verify(paths["priorForbiddenRegistry"], registry_document, "prior-forbidden registry")
        prelabel_time = _timestamp_after(created_utc, 1)
        prelabel_document = trainer.expected_upstream_prelabel_seal(
            component_map=paths["componentMap"], target_free_routing=paths["targetFreeRouting"],
            terminal_classifier_lineage=paths["terminalLineage"], prior_forbidden_registry=paths["priorForbiddenRegistry"],
            prior_forbidden_catalogs=[Path(group["manifest"]["path"]) for group in plan["priorForbiddenGroups"]],
            initializer_manifest=paths["initializerManifest"], source_root_manifest=paths["sourceRootManifest"],
            source_children_manifest=paths["sourceChildrenManifest"], producer=Path(__file__).resolve(), created_utc=prelabel_time,
        )
        _publish_or_verify(paths["prelabelSeal"], prelabel_document, "prelabel seal")
        _publish_or_verify(paths["staticHceOptions"], trainer.static_hce_options_document(), "static-HCE options")
        hce_claim = {
            "schemaVersion": 1, "kind": HCE_CLAIM_KIND, "profileId": PROFILE_ID,
            "status": "claimed-before-teacher-and-target-decode", "createdUtc": _timestamp_after(created_utc, 2),
            "prelabelSeal": _identity(paths["prelabelSeal"]), "targetFreeRouting": _identity(paths["targetFreeRouting"]),
            "engine": dict(plan["bindings"]["staticHceExecutable"]),
            "runner": dict(plan["dependencies"]["evaluatorRunner"]["actual"]),
            "options": _identity(paths["staticHceOptions"]),
            "plannedTranscriptPath": str(paths["staticHceTranscript"]),
            "targetRowsDecodedAtClaim": 0, "targetFieldsDecodedAtClaim": 0,
            "resultInformationRead": False,
        }
        _publish_or_verify(paths["preTargetHceClaim"], hce_claim, "pre-target HCE claim")
        trainer._verify_upstream_hce_claim(
            paths["preTargetHceClaim"], prelabel_seal=paths["prelabelSeal"],
            target_free_routing=paths["targetFreeRouting"],
            engine=Path(plan["bindings"]["staticHceExecutable"]["path"]),
            runner=Path(plan["dependencies"]["evaluatorRunner"]["actual"]["path"]),
            options=paths["staticHceOptions"], planned_transcript=paths["staticHceTranscript"],
        )
        return hce_claim
    if stage == "teacher":
        _require_dependencies(plan, ("teacher", "trainerAuthority"))
        trainer, _, teacher, _ = _import_authorities(
            ("trainerAuthority", "teacher")
        )
        assert trainer is not None and teacher is not None
        trainer._verify_upstream_hce_completion(
            paths["preTargetHceCompletion"], claim=paths["preTargetHceClaim"], prelabel_seal=paths["prelabelSeal"],
            target_free_routing=paths["targetFreeRouting"], engine=Path(plan["bindings"]["staticHceExecutable"]["path"]),
            runner=Path(plan["dependencies"]["evaluatorRunner"]["actual"]["path"]), options=paths["staticHceOptions"],
            transcript=paths["staticHceTranscript"],
        )
        if not os.path.lexists(paths["teacherOptions"]):
            teacher.publish_teacher_options(paths["teacherOptions"])
        if os.path.lexists(paths["teacherClaim"]):
            return teacher._verify_claim(paths["teacherClaim"]).document
        return teacher.publish_teacher_claim(
            paths["teacherClaim"], routing=paths["targetFreeRouting"], components=paths["componentMap"],
            prelabel=paths["prelabelSeal"], hce_completion=paths["preTargetHceCompletion"],
            engine=Path(plan["bindings"]["teacherEngine"]["path"]), options=paths["teacherOptions"],
            projection_producer=Path(plan["dependencies"]["teacher"]["actual"]["path"]),
            projected_corpus=paths["projectedCorpus"], projection_manifest=paths["labelManifest"], created_utc=created_utc,
        )
    raise ValueError(f"unknown claim stage: {stage}")


def materialize_hce(plan_path: Path) -> dict[str, Any]:
    plan = verify_plan(plan_path)
    _require_dependencies(plan, ("trainerAuthority", "evaluatorRunner"))
    paths = _paths(plan)
    trainer, _, _, _ = _import_authorities(("trainerAuthority",))
    assert trainer is not None
    trainer._verify_upstream_hce_claim(
        paths["preTargetHceClaim"], prelabel_seal=paths["prelabelSeal"], target_free_routing=paths["targetFreeRouting"],
        engine=Path(plan["bindings"]["staticHceExecutable"]["path"]),
        runner=Path(plan["dependencies"]["evaluatorRunner"]["actual"]["path"]), options=paths["staticHceOptions"],
        planned_transcript=paths["staticHceTranscript"],
    )
    routes = trainer._parse_target_free_routing(paths["targetFreeRouting"])
    ordered = trainer._target_free_hce_order(routes)
    replay = trainer._run_exact_evaluator(
        engine=Path(plan["bindings"]["staticHceExecutable"]["path"]),
        runner=Path(plan["dependencies"]["evaluatorRunner"]["actual"]["path"]), mode="--evaluate-handcrafted-stream",
        ofens=[row["normalizedChildOfen"] for row in ordered],
    )
    rows = [
        {"schemaVersion": 1, "kind": HCE_ROW_KIND, "profileId": PROFILE_ID,
         "childId": row["childId"], "handcraftedCpChildStm": replay[str(index)]}
        for index, row in enumerate(ordered)
    ]
    payload = b"".join(_canonical_json(row) for row in rows)
    identity = _publish_or_verify_bytes(
        paths["staticHceTranscript"], payload, "static-HCE transcript"
    )
    return {"rows": len(rows), "transcript": identity, "resultInformationRead": False}


def materialize_projection(plan_path: Path, *, created_utc: str) -> dict[str, Any]:
    """Publish only the exact projection defined by the pinned teacher authority."""

    plan = verify_plan(plan_path)
    _require_dependencies(plan, ("teacher",))
    paths = _paths(plan)
    _, _, teacher, _ = _import_authorities(("teacher",))
    assert teacher is not None

    # The exact teacher authority first proves complete shallow/deep coverage
    # and deterministically publishes its ledger receipt, labels, and manifest.
    _, teacher_rows, _ = teacher.publish_teacher_outputs(
        claim=paths["teacherClaim"], ledger=paths["teacherAttemptLedger"],
        ledger_completion=paths["teacherAttemptLedgerCompletion"],
        labels=paths["teacherLabels"], teacher_manifest=paths["teacherManifest"],
    )
    context = teacher._verify_claim(paths["teacherClaim"])
    projected_rows = teacher._projected_rows(teacher_rows, context.components)
    if len(projected_rows) != PRODUCTION_INVENTORY_CONTRACT["totalChildren"]:
        raise ValueError("exact teacher projection row inventory changed")
    corpus_payload = b"".join(_canonical_json(row) for row in projected_rows)
    corpus_identity = _publish_or_verify_bytes(
        paths["projectedCorpus"], corpus_payload, "projected corpus"
    )

    manifest_document = teacher.projected_manifest_document(
        context=context, corpus=paths["projectedCorpus"],
        teacher_labels=paths["teacherLabels"],
        teacher_manifest=paths["teacherManifest"], created_utc=created_utc,
    )
    _require_capsule_inventories(manifest_document)
    manifest_identity = _publish_or_verify(
        paths["labelManifest"], manifest_document, "projection label manifest"
    )
    verified_corpus, verified_manifest, verified_document = teacher._verify_projection(
        context=context, teacher_labels=paths["teacherLabels"],
        teacher_manifest=paths["teacherManifest"],
    )
    if (
        not _type_exact_equal(verified_corpus, corpus_identity)
        or not _type_exact_equal(verified_manifest, manifest_identity)
        or not _type_exact_equal(verified_document, manifest_document)
    ):
        raise ValueError("exact teacher projection replay changed after publication")
    return {
        "rows": len(projected_rows), "projectedCorpus": corpus_identity,
        "labelManifest": manifest_identity, "projectionSemanticsVerified": True,
        "resultInformationRead": False,
    }


def _label_manifest(path: Path) -> dict[str, Any]:
    document, _ = _load_json(path, "label manifest")
    return document


def _capsule_document(plan: Mapping[str, Any], *, created_utc: str) -> dict[str, Any]:
    _, _, teacher, _ = _import_authorities(("teacher",))
    assert teacher is not None
    paths = _paths(plan)
    initializer, _ = _load_json(paths["initializerManifest"], "initializer manifest")
    registry, _ = _load_json(paths["priorForbiddenRegistry"], "prior-forbidden registry")
    teacher_claim, _ = _load_json(paths["teacherClaim"], "teacher claim")
    labels = _label_manifest(paths["labelManifest"])
    _require_capsule_inventories(labels)
    verifier_runner = plan["dependencies"]["verifierRunner"]["actual"]
    return {
        "schemaVersion": 1, "kind": CAPSULE_KIND, "profileId": PROFILE_ID,
        "status": "closed-pretarget-to-final-projection-lineage", "createdUtc": created_utc,
        "upstreamVerifierExecutable": dict(plan["bindings"]["staticHceExecutable"]),
        "upstreamVerifierRunner": dict(verifier_runner), "upstreamVerifierOptions": _identity(paths["verifierOptions"]),
        "targetFreeRouting": _identity(paths["targetFreeRouting"]), "componentMap": _identity(paths["componentMap"]),
        "prelabelSeal": _identity(paths["prelabelSeal"]), "terminalClassifierLineage": _identity(paths["terminalLineage"]),
        "initializerSelection": _identity(paths["initializerSelection"]), "initializerClosure": _identity(paths["initializerClosure"]),
        "initializerModel": _identity(paths["initializerModel"]), "initializerManifest": _identity(paths["initializerManifest"]),
        "plannedProjectionProducer": dict(teacher_claim["plannedProjectionProducer"]),
        "plannedProjectedCorpusPath": teacher_claim["plannedProjectedCorpusPath"],
        "plannedProjectionManifestPath": teacher_claim["plannedProjectionManifestPath"],
        "priorForbiddenRegistry": _identity(paths["priorForbiddenRegistry"]),
        "priorForbiddenCatalogs": [dict(entry["manifest"]) for entry in registry["catalogs"]],
        "teacherClaim": _identity(paths["teacherClaim"]), "teacherEngine": dict(plan["bindings"]["teacherEngine"]),
        "teacherRunner": dict(plan["dependencies"]["teacher"]["actual"]), "teacherOptions": _identity(paths["teacherOptions"]),
        "teacherBudgets": dict(teacher.BUDGETS), "teacherInputOrderSha256": teacher_claim["inputOrderSha256"],
        "teacherAttemptLedger": _identity(paths["teacherAttemptLedger"]),
        "teacherAttemptLedgerCompletion": _identity(paths["teacherAttemptLedgerCompletion"]),
        "teacherCompletion": _identity(paths["teacherCompletion"]),
        "projectionProducer": dict(teacher_claim["plannedProjectionProducer"]),
        "teacherLabels": _identity(paths["teacherLabels"]), "teacherManifest": _identity(paths["teacherManifest"]),
        "projectedCorpus": _identity(paths["projectedCorpus"]), "labelManifest": _identity(paths["labelManifest"]),
        "preTargetHceClaim": _identity(paths["preTargetHceClaim"]), "preTargetHceCompletion": _identity(paths["preTargetHceCompletion"]),
        "staticHceEngine": dict(plan["bindings"]["staticHceExecutable"]),
        "staticHceRunner": dict(plan["dependencies"]["evaluatorRunner"]["actual"]),
        "staticHceOptions": _identity(paths["staticHceOptions"]), "staticHceTranscript": _identity(paths["staticHceTranscript"]),
        "staticHceManifest": _identity(paths["staticHceManifest"]),
        "rootInventories": labels["rootInventories"], "phaseSideInventories": labels["phaseSideInventories"],
        "closureDeclaration": {
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
        },
        "resultInformationRead": False, "finalStageSeal": True,
    }


def _run_bounded(command: Sequence[str], *, timeout: int) -> tuple[int, bytes, bytes]:
    """Run with concurrent hard-capped stdout/stderr capture."""

    process = subprocess.Popen(
        list(command), cwd=str(_tool_dir()), env={}, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert process.stdout is not None and process.stderr is not None
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    oversized = threading.Event()
    failures: list[BaseException] = []

    def drain(name: str, stream: Any) -> None:
        try:
            while True:
                block = stream.read(64 * 1024)
                if not block:
                    break
                buffer = buffers[name]
                remaining = MAX_PROCESS_STDOUT_BYTES + 1 - len(buffer)
                if remaining > 0:
                    buffer.extend(block[:remaining])
                if len(buffer) > MAX_PROCESS_STDOUT_BYTES:
                    oversized.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
        except BaseException as error:  # pragma: no cover - OS pipe failure
            failures.append(error)
            try:
                process.kill()
            except OSError:
                pass
        finally:
            stream.close()

    threads = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        returncode = process.wait()
    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("fresh verifier output pipes did not close after process exit")
    stdout = bytes(buffers["stdout"])
    stderr = bytes(buffers["stderr"])
    if failures:
        raise RuntimeError("fresh verifier output capture failed") from failures[0]
    if timed_out:
        raise subprocess.TimeoutExpired(list(command), timeout, output=stdout, stderr=stderr)
    if oversized.is_set():
        raise ValueError("fresh verifier emitted oversized output")
    return returncode, stdout, stderr


def _open_launch_bindings(
    named_paths: Mapping[str, Path],
) -> dict[str, dict[str, Any]]:
    """Hold authenticated descriptors open for every path crossing a launch."""

    bindings: dict[str, dict[str, Any]] = {}
    try:
        for name, path in named_paths.items():
            absolute = _safe_path(path, must_exist=True)
            before = os.lstat(absolute)
            descriptor = os.open(
                absolute,
                os.O_RDONLY | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                opened = os.fstat(descriptor)
                _validate_private_descriptor(opened, absolute, f"launch {name}")
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError(f"launch binding changed before open: {name}")
                digest = hashlib.sha256()
                length = 0
                while True:
                    block = os.read(descriptor, 1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    length += len(block)
                completed = os.fstat(descriptor)
                if _stat_state(completed) != _stat_state(opened):
                    raise ValueError(f"launch binding changed during snapshot: {name}")
                named = os.lstat(absolute)
                if _stat_state(named) != _stat_state(before):
                    raise ValueError(f"launch pathname changed during snapshot: {name}")
                bindings[name] = {
                    "descriptor": descriptor,
                    "path": absolute,
                    "state": _stat_state(completed),
                    "identity": {
                        "path": str(absolute), "bytes": length,
                        "sha256": digest.hexdigest(),
                    },
                }
            except BaseException:
                os.close(descriptor)
                raise
    except BaseException:
        _close_launch_bindings(bindings)
        raise
    return bindings


def _close_launch_bindings(bindings: Mapping[str, Mapping[str, Any]]) -> None:
    for binding in bindings.values():
        try:
            os.close(binding["descriptor"])
        except OSError:
            pass


def _recheck_launch_bindings(bindings: Mapping[str, Mapping[str, Any]]) -> None:
    for name, binding in bindings.items():
        descriptor = binding["descriptor"]
        path = Path(binding["path"])
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        length = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            length += len(block)
        current = os.fstat(descriptor)
        _validate_private_descriptor(current, path, f"launch recheck {name}")
        descriptor_identity = {
            "path": str(path), "bytes": length, "sha256": digest.hexdigest()
        }
        if (
            _stat_state(current) != binding["state"]
            or descriptor_identity != binding["identity"]
            or _identity(path) != binding["identity"]
        ):
            raise ValueError(f"launch binding changed during subprocess: {name}")


def _strict_stdout_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not one strict JSON object") from error
    if type(value) is not dict or _canonical_json(value) != payload:
        raise ValueError(f"{label} is not exact canonical JSON")
    return value


def _require_exact_identity(actual: Any, expected: Any, label: str) -> None:
    if not _type_exact_equal(actual, expected):
        raise ValueError(f"fresh verifier {label} cross-link changed")


def _validate_fresh_verifier_receipt(
    value: Mapping[str, Any], plan: Mapping[str, Any], paths: Mapping[str, Path],
    launch: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate the complete canonical verifier schema and every capsule link."""

    _exact_keys(value, VERIFICATION_FIELDS, "fresh verifier receipt")
    if (
        type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
        or value["kind"] != VERIFICATION_KIND or value["profileId"] != PROFILE_ID
        or value["status"] != "passed-fresh-semantic-replay"
        or value["resultInformationRead"] is not False
    ):
        raise ValueError("fresh verifier receipt header changed")
    capsule, capsule_identity = _load_json(paths["capsule"], "decision-v3 capsule")
    initializer, initializer_identity = _load_json(
        paths["initializerManifest"], "initializer manifest"
    )
    routing, routing_identity = _load_json(paths["routingCompletion"], "routing completion")
    hce_completion, hce_completion_identity = _load_json(
        paths["preTargetHceCompletion"], "pre-target HCE completion"
    )
    expected_launch = {
        "verifierExecutable": plan["bindings"]["staticHceExecutable"],
        "verifierRunner": plan["dependencies"]["verifierRunner"]["actual"],
        "verifierOptions": capsule["upstreamVerifierOptions"],
        "capsule": capsule_identity,
        "initializerManifest": initializer_identity,
        "preTargetHceCompletion": capsule["preTargetHceCompletion"],
    }
    for name, expected in expected_launch.items():
        _require_exact_identity(launch[name]["identity"], expected, name)
    for name in ("capsule", "verifierExecutable", "verifierRunner", "verifierOptions"):
        _require_exact_identity(value[name], launch[name]["identity"], name)
    _require_exact_identity(
        launch["routingCompletion"]["identity"], routing_identity,
        "routing completion",
    )
    _require_exact_identity(
        launch["preTargetHceCompletion"]["identity"], hce_completion_identity,
        "pre-target HCE completion",
    )
    _require_exact_identity(
        capsule.get("upstreamVerifierExecutable"), launch["verifierExecutable"]["identity"],
        "capsule verifier executable",
    )
    _require_exact_identity(
        capsule.get("upstreamVerifierRunner"), launch["verifierRunner"]["identity"],
        "capsule verifier runner",
    )

    routed_children = PRODUCTION_INVENTORY_CONTRACT["totalChildren"]
    root_count = PRODUCTION_INVENTORY_CONTRACT["totalRoots"]
    selected_components = routing.get("coverage", {}).get("selectedComponents")
    if type(selected_components) is not int or selected_components <= 0:
        raise ValueError("routing completion component count changed")

    initial = value["initializerAuthority"]
    _exact_keys(initial, INITIALIZER_AUTHORITY_FIELDS, "fresh initializer authority")
    selection_mode = initializer.get("selectionMode")
    selected_index = initializer.get("selectedCatalogIndex")
    promoted = selection_mode == "promoted-prior"
    if (
        selection_mode not in {"promoted-prior", "deterministic-fallback"}
        or (promoted and (type(selected_index) is not int or selected_index not in (0, 1)))
        or (not promoted and selected_index is not None)
        or not _type_exact_equal(initial["manifest"], capsule["initializerManifest"])
        or initial["selectionMode"] != selection_mode
        or not _type_exact_equal(
            initial["selectedCatalogIndex"], selected_index
        )
        or not _type_exact_equal(initial["selectedModel"], capsule["initializerModel"])
        or not _type_exact_equal(initial["catalogSourceIds"], ["G5", "G2-K2"])
        or initial["firstEligibleSelected"] is not True
        or initial["selectionSemanticsVerified"] is not True
        or initial["sourceClosureSemanticsVerified"] is not True
        or type(initial["g6TargetRowsDecoded"]) is not int
        or initial["g6TargetRowsDecoded"] != 0
        or initial["resultInformationRead"] is not False
        or (promoted and (
            initial["selectedPromotionHealthPassed"] is not True
            or initial["fallbackProtocolReplayed"] is not False
        ))
        or (not promoted and (
            initial["selectedPromotionHealthPassed"] is not None
            or initial["fallbackProtocolReplayed"] is not True
        ))
    ):
        raise ValueError("fresh initializer authority changed")

    terminal = value["terminalAuthority"]
    _exact_keys(terminal, TERMINAL_AUTHORITY_FIELDS, "fresh terminal authority")
    if (
        not _type_exact_equal(terminal["lineage"], capsule["terminalClassifierLineage"])
        or type(terminal["routedChildren"]) is not int
        or terminal["routedChildren"] != routed_children
        or type(terminal["terminalChildrenExcludedBeforeRouting"]) is not int
        or terminal["terminalChildrenExcludedBeforeRouting"] < 0
        or type(terminal["unclassifiedChildren"]) is not int
        or terminal["unclassifiedChildren"] != 0
        or terminal["errorTextAcceptedAsTerminal"] is not False
        or terminal["rulesSemanticsReplayed"] is not True
        or terminal["completionSemanticsVerified"] is not True
    ):
        raise ValueError("fresh terminal authority changed")

    forbidden = value["priorForbiddenAuthority"]
    _exact_keys(forbidden, FORBIDDEN_AUTHORITY_FIELDS, "fresh forbidden authority")
    if (
        not _type_exact_equal(forbidden["catalogs"], capsule["priorForbiddenCatalogs"])
        or not _type_exact_equal(forbidden["registry"], capsule["priorForbiddenRegistry"])
        or not _type_exact_equal(forbidden["requiredSourceIds"], ["G3", "G4", "G5"])
        or type(forbidden["catalogPositions"]) is not int
        or forbidden["catalogPositions"] <= 0
        or forbidden["manifestsSemanticallyReplayed"] is not True
        or any(
            type(forbidden[name]) is not int or forbidden[name] != 0
            for name in (
                "exactPositionOverlaps", "conservativeSignatureOverlaps",
                "sourceArtifactOverlaps",
            )
        )
    ):
        raise ValueError("fresh prior-forbidden authority changed")

    component = value["componentAuthority"]
    _exact_keys(component, COMPONENT_AUTHORITY_FIELDS, "fresh component authority")
    if (
        not _type_exact_equal(component["componentMap"], capsule["componentMap"])
        or type(component["roots"]) is not int or component["roots"] != root_count
        or type(component["components"]) is not int
        or component["components"] != selected_components
        or component["wholeComponentSplits"] is not True
        or component["semanticsReplayed"] is not True
    ):
        raise ValueError("fresh component authority changed")

    static_hce = value["staticHceAuthority"]
    _exact_keys(static_hce, STATIC_HCE_AUTHORITY_FIELDS, "fresh static-HCE authority")
    static_links = {
        "claim": "preTargetHceClaim", "completion": "preTargetHceCompletion",
        "teacherClaim": "teacherClaim", "prelabelSeal": "prelabelSeal",
        "targetFreeRouting": "targetFreeRouting", "engine": "staticHceEngine",
        "runner": "staticHceRunner", "options": "staticHceOptions",
        "transcript": "staticHceTranscript",
    }
    if any(
        not _type_exact_equal(static_hce[name], capsule[capsule_name])
        for name, capsule_name in static_links.items()
    ) or (
        static_hce["inputOrderSha256"] != hce_completion.get("inputOrderSha256")
        or type(static_hce["rows"]) is not int or static_hce["rows"] != routed_children
        or static_hce["perspective"] != STATIC_HCE_PERSPECTIVE
        or static_hce["freshReplayMatches"] is not True
        or static_hce["completedBeforeTeacherClaim"] is not True
        or static_hce["semanticsReplayed"] is not True
    ):
        raise ValueError("fresh static-HCE authority changed")

    teacher = value["teacherLedgerAuthority"]
    _exact_keys(teacher, TEACHER_LEDGER_AUTHORITY_FIELDS, "fresh teacher authority")
    teacher_links = {
        "claim": "teacherClaim", "attemptLedger": "teacherAttemptLedger",
        "attemptLedgerCompletion": "teacherAttemptLedgerCompletion",
        "completion": "teacherCompletion",
    }
    maximum_attempts = capsule["teacherBudgets"].get("maximumAttempts")
    if any(
        not _type_exact_equal(teacher[name], capsule[capsule_name])
        for name, capsule_name in teacher_links.items()
    ) or (
        not _type_exact_equal(teacher["budgets"], capsule["teacherBudgets"])
        or type(maximum_attempts) is not int or maximum_attempts <= 0
        or type(teacher["routedChildren"]) is not int
        or teacher["routedChildren"] != routed_children
        or type(teacher["attemptRecords"]) is not int
        or not routed_children <= teacher["attemptRecords"] <= routed_children * maximum_attempts
        or type(teacher["successfulChildren"]) is not int
        or teacher["successfulChildren"] != routed_children
        or type(teacher["rejectedChildren"]) is not int
        or teacher["rejectedChildren"] != 0
        or type(teacher["unresolvedChildren"]) is not int
        or teacher["unresolvedChildren"] != 0
        or teacher["semanticsReplayed"] is not True
    ):
        raise ValueError("fresh teacher authority changed")

    projection = value["projectionAuthority"]
    _exact_keys(projection, PROJECTION_AUTHORITY_FIELDS, "fresh projection authority")
    if (
        not _type_exact_equal(projection["plannedProducer"], capsule["plannedProjectionProducer"])
        or projection["plannedCorpusPath"] != capsule["plannedProjectedCorpusPath"]
        or projection["plannedManifestPath"] != capsule["plannedProjectionManifestPath"]
        or not _type_exact_equal(projection["actualProducer"], capsule["projectionProducer"])
        or not _type_exact_equal(projection["actualCorpus"], capsule["projectedCorpus"])
        or not _type_exact_equal(projection["actualManifest"], capsule["labelManifest"])
        or projection["producerMatches"] is not True
        or projection["pathsMatch"] is not True
        or projection["semanticsReplayed"] is not True
    ):
        raise ValueError("fresh projection authority changed")
    return dict(value)


def _fresh_verifier(plan: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    command = [
        plan["bindings"]["staticHceExecutable"]["path"], "-I", "-B",
        plan["dependencies"]["verifierRunner"]["actual"]["path"],
        "--verify-omega-decision-v3-capsule", str(paths["capsule"]), str(paths["verifierOptions"]),
        str(paths["initializerManifest"]),
    ]
    launch = _open_launch_bindings(
        {
            "verifierExecutable": Path(command[0]),
            "verifierRunner": Path(command[3]),
            "verifierOptions": paths["verifierOptions"],
            "capsule": paths["capsule"],
            "initializerManifest": paths["initializerManifest"],
            "routingCompletion": paths["routingCompletion"],
            "preTargetHceCompletion": paths["preTargetHceCompletion"],
        }
    )
    try:
        try:
            returncode, stdout, stderr = _run_bounded(command, timeout=3600)
        finally:
            _recheck_launch_bindings(launch)
    finally:
        _close_launch_bindings(launch)
    if returncode != 0 or stderr:
        detail = stderr[-4000:].decode("utf-8", errors="replace")
        raise ValueError(f"fresh verifier failed: {detail or 'nonzero exit'}")
    value = _strict_stdout_object(stdout, "fresh verifier stdout")
    return _validate_fresh_verifier_receipt(value, plan, paths, launch)


def _require_fresh_verifier_ready(plan: Mapping[str, Any]) -> dict[str, Any]:
    command = [
        plan["bindings"]["staticHceExecutable"]["path"], "-I", "-B",
        plan["dependencies"]["verifierRunner"]["actual"]["path"], "--self-test",
    ]
    launch = _open_launch_bindings(
        {"verifierExecutable": Path(command[0]), "verifierRunner": Path(command[3])}
    )
    try:
        try:
            returncode, stdout, stderr = _run_bounded(command, timeout=60)
        finally:
            _recheck_launch_bindings(launch)
    finally:
        _close_launch_bindings(launch)
    if returncode != 0 or stderr:
        raise RuntimeError("canonical verifier self-test did not complete cleanly")
    try:
        value = _strict_stdout_object(stdout, "canonical verifier self-test stdout")
    except ValueError as error:
        raise RuntimeError("canonical verifier self-test did not emit JSON") from error
    _exact_keys(value, SELF_TEST_FIELDS, "canonical verifier self-test")
    dependencies = value["dependencies"]
    if type(dependencies) is not dict or not dependencies:
        raise RuntimeError("canonical verifier self-test dependency inventory changed")
    for name, record in dependencies.items():
        if type(name) is not str or type(record) is not dict:
            raise RuntimeError("canonical verifier self-test dependency changed")
        _exact_keys(record, DEPENDENCY_RECORD_FIELDS, f"verifier self-test dependency {name}")
        if record["pinFinalized"] is not True or record["matches"] is not True:
            raise RuntimeError("canonical verifier reports awaiting-stable-pins")
        _verify_identity(record["actual"], f"verifier self-test dependency {name}")
    if (
        type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
        or value["kind"] != "omega-decision-v3-verifier-self-test"
        or value["profileId"] != PROFILE_ID or value["status"] != "ready"
        or value["resultInformationRead"] is not False
    ):
        raise RuntimeError("canonical verifier reports awaiting-stable-pins")
    return value


def finalize_stage(
    plan_path: Path, stage: str, *, created_utc: str | None = None,
    started_utc: str | None = None, completed_utc: str | None = None,
) -> dict[str, Any]:
    plan = verify_plan(plan_path)
    paths = _paths(plan)
    if stage == "terminal":
        _require_dependencies(plan, ("terminal",))
        _, _, _, terminal = _import_authorities(("terminal",))
        assert terminal is not None
        if not all(type(item) is str for item in (created_utc, started_utc, completed_utc)):
            raise ValueError("terminal finalize requires start, completion, and lineage UTC")
        if os.path.lexists(paths["terminalLineage"]):
            return terminal.verify_terminal_lineage(paths["terminalLineage"])
        return terminal.publish_terminal_lineage(
            paths["terminalLineage"], claim=paths["terminalClaim"], native_manifest=paths["terminalNativeManifest"],
            transcript=paths["terminalTranscript"], eligible_roots=paths["terminalEligibleRoots"],
            eligible_children=paths["terminalEligibleChildren"], classifier_stdout=paths["terminalStdout"],
            classifier_stderr=paths["terminalStderr"], started_utc=started_utc, completed_utc=completed_utc,
            exit_code=0, timed_out=False, created_utc=created_utc,
        )
    if stage == "routing":
        _require_dependencies(plan, ("routing", "terminal"))
        _, routing, _, _ = _import_authorities(("routing",))
        assert routing is not None
        document = routing.verify_completion(paths["routingCompletion"])
        _require_routing_inventory(routing, paths)
        return document
    if stage == "pretarget":
        _require_dependencies(plan, ("trainerAuthority", "evaluatorRunner"))
        trainer, _, _, _ = _import_authorities(("trainerAuthority",))
        assert trainer is not None
        if type(created_utc) is not str:
            raise ValueError("pretarget finalize requires createdUtc")
        kwargs = dict(
            claim=paths["preTargetHceClaim"], prelabel_seal=paths["prelabelSeal"], target_free_routing=paths["targetFreeRouting"],
            engine=Path(plan["bindings"]["staticHceExecutable"]["path"]),
            runner=Path(plan["dependencies"]["evaluatorRunner"]["actual"]["path"]), options=paths["staticHceOptions"],
            transcript=paths["staticHceTranscript"], created_utc=created_utc,
        )
        expected = trainer.expected_upstream_hce_completion(**kwargs)
        _publish_or_verify(paths["preTargetHceCompletion"], expected, "pre-target HCE completion")
        return trainer._verify_upstream_hce_completion(paths["preTargetHceCompletion"], **{k: v for k, v in kwargs.items() if k != "created_utc"})
    if stage == "teacher":
        _require_dependencies(plan, ("teacher",))
        _, _, teacher, _ = _import_authorities(("teacher",))
        assert teacher is not None
        return teacher.finalize_teacher(
            claim=paths["teacherClaim"], ledger=paths["teacherAttemptLedger"],
            ledger_completion=paths["teacherAttemptLedgerCompletion"], labels=paths["teacherLabels"],
            teacher_manifest=paths["teacherManifest"], completion=paths["teacherCompletion"],
        )
    if stage == "capsule":
        _require_dependencies(
            plan, ("terminal", "routing", "teacher", "evaluatorRunner", "trainerAuthority",
                   "initializerGenerator", "verifierImplementation", "verifierRunner"),
        )
        trainer, routing, teacher, terminal = _import_authorities()
        assert all(item is not None for item in (trainer, routing, teacher, terminal))
        _require_fresh_verifier_ready(plan)
        if type(created_utc) is not str:
            raise ValueError("capsule finalize requires createdUtc")
        # Re-run all closures that can be invoked without decoding targets.
        routing.verify_completion(paths["routingCompletion"])
        _require_routing_inventory(trainer, paths)
        terminal.verify_terminal_lineage(paths["terminalLineage"])
        trainer._verify_initializer_manifest(paths["initializerManifest"], paths["initializerModel"])
        _verify_initializer_source_authority(paths)
        teacher._verify_claim(paths["teacherClaim"])
        teacher.finalize_teacher(
            claim=paths["teacherClaim"], ledger=paths["teacherAttemptLedger"],
            ledger_completion=paths["teacherAttemptLedgerCompletion"],
            labels=paths["teacherLabels"], teacher_manifest=paths["teacherManifest"],
            completion=paths["teacherCompletion"],
        )
        if not os.path.lexists(paths["verifierOptions"]):
            command = [
                plan["bindings"]["staticHceExecutable"]["path"], "-I", "-B",
                plan["dependencies"]["verifierRunner"]["actual"]["path"],
                "--publish-verifier-options", str(paths["verifierOptions"]),
            ]
            launch = _open_launch_bindings(
                {"verifierExecutable": Path(command[0]), "verifierRunner": Path(command[3])}
            )
            try:
                try:
                    returncode, stdout, stderr = _run_bounded(command, timeout=60)
                finally:
                    _recheck_launch_bindings(launch)
            finally:
                _close_launch_bindings(launch)
            if returncode != 0 or stdout or stderr:
                raise ValueError("canonical verifier options publication failed")
        verifier = _load_exact_reviewed("verifierImplementation")
        verifier_options, _ = _load_json(paths["verifierOptions"], "verifier options")
        if not _type_exact_equal(verifier_options, verifier.VERIFIER_OPTIONS):
            raise ValueError("canonical verifier options differ from exact implementation")
        # The legacy training authority manifest is a post-projection binding;
        # publish it only after the exact teacher projection exists.
        label_document = _label_manifest(paths["labelManifest"])
        hce_time = _timestamp_after(label_document["createdUtc"], 1)
        hce_manifest = trainer.expected_static_hce_manifest(
            corpus=paths["projectedCorpus"], label_manifest=paths["labelManifest"], component_map=paths["componentMap"],
            projection=paths["staticHceTranscript"], engine=Path(plan["bindings"]["staticHceExecutable"]["path"]),
            runner=Path(plan["dependencies"]["evaluatorRunner"]["actual"]["path"]), options=paths["staticHceOptions"], created_utc=hce_time,
        )
        _publish_or_verify(paths["staticHceManifest"], hce_manifest, "static-HCE manifest")
        _require_after(
            created_utc,
            (
                (paths["teacherCompletion"], "teacher completion"),
                (paths["labelManifest"], "projection label manifest"),
                (paths["staticHceManifest"], "static-HCE manifest"),
            ),
        )
        document = _capsule_document(plan, created_utc=created_utc)
        _publish_or_verify(paths["capsule"], document, "decision-v3 capsule")
        # A failed verifier leaves the immutable capsule as diagnostic evidence.
        # The exact same closure can be retried; no rollback pathname race exists.
        receipt = _fresh_verifier(plan, paths)
        _publish_or_verify(paths["verificationReceipt"], receipt, "fresh verification receipt")
        return receipt
    raise ValueError(f"unknown finalize stage: {stage}")


def status_document(plan_path: Path) -> dict[str, Any]:
    plan = verify_plan(plan_path)
    paths = _paths(plan)
    realized = {name: os.path.isfile(path) for name, path in paths.items() if name != "plan"}
    if realized.get("verificationReceipt"):
        next_stage = "complete"
    elif realized.get("capsule"):
        next_stage = "capsule-finalize-or-fresh-verify"
    elif realized.get("teacherCompletion"):
        next_stage = "capsule-finalize"
    elif realized.get("labelManifest") or realized.get("projectedCorpus"):
        next_stage = "teacher-finalize"
    elif any(
        realized.get(name)
        for name in (
            "teacherAttemptLedgerCompletion", "teacherLabels", "teacherManifest"
        )
    ):
        next_stage = "materialize-projection"
    elif realized.get("teacherAttemptLedger"):
        next_stage = "teacher-resume-or-materialize-projection"
    elif realized.get("teacherClaim"):
        next_stage = "teacher-run"
    elif realized.get("preTargetHceCompletion"):
        next_stage = "teacher-claim"
    elif realized.get("staticHceTranscript"):
        next_stage = "pretarget-finalize"
    elif realized.get("preTargetHceClaim"):
        next_stage = "pretarget-hce"
    elif realized.get("routingCompletion") and all(
        realized.get(name)
        for name in (
            "initializerSelection", "initializerClosure", "initializerModel",
            "initializerManifest",
        )
    ):
        next_stage = "pretarget-claim"
    elif realized.get("routingCompletion"):
        next_stage = "initializer"
    elif realized.get("terminalLineage"):
        next_stage = "routing"
    elif any(
        realized.get(name)
        for name in ("terminalNativeManifest", "terminalTranscript", "terminalEligibleRoots", "terminalEligibleChildren")
    ):
        next_stage = "terminal-finalize"
    elif realized.get("terminalClaim"):
        next_stage = "terminal-classifier"
    elif realized.get("historyRoots") or realized.get("historyRootsManifest"):
        next_stage = "terminal-claim"
    else:
        next_stage = "history-then-terminal-claim"
    return {
        "schemaVersion": 1, "kind": "omega-decision-v3-pipeline-status", "profileId": PROFILE_ID,
        "nextStage": next_stage, "realized": realized,
        "artifactsSemanticallyVerifiedByStatus": False,
        "resultInformationRead": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--inputs", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    plan.add_argument("--created-utc", required=True)
    claim = sub.add_parser("claim")
    claim.add_argument("--plan", required=True, type=Path)
    claim.add_argument("--stage", required=True, choices=("terminal", "pretarget", "teacher"))
    claim.add_argument("--created-utc", required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--plan", required=True, type=Path)
    finalize.add_argument("--stage", required=True, choices=("terminal", "routing", "pretarget", "teacher", "capsule"))
    finalize.add_argument("--created-utc")
    finalize.add_argument("--started-utc")
    finalize.add_argument("--completed-utc")
    hce = sub.add_parser("materialize-hce")
    hce.add_argument("--plan", required=True, type=Path)
    projection = sub.add_parser("materialize-projection")
    projection.add_argument("--plan", required=True, type=Path)
    projection.add_argument("--created-utc", required=True)
    status = sub.add_parser("status")
    status.add_argument("--plan", required=True, type=Path)
    verify = sub.add_parser("verify-plan")
    verify.add_argument("--plan", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        result = publish_plan(args.inputs, args.output, created_utc=args.created_utc)
    elif args.command == "claim":
        result = claim_stage(args.plan, args.stage, created_utc=args.created_utc)
    elif args.command == "finalize":
        result = finalize_stage(args.plan, args.stage, created_utc=args.created_utc,
                                started_utc=args.started_utc, completed_utc=args.completed_utc)
    elif args.command == "materialize-hce":
        result = materialize_hce(args.plan)
    elif args.command == "materialize-projection":
        result = materialize_projection(args.plan, created_utc=args.created_utc)
    elif args.command == "status":
        result = status_document(args.plan)
    elif args.command == "verify-plan":
        result = verify_plan(args.plan)
    else:  # pragma: no cover
        raise AssertionError(args.command)
    sys.stdout.buffer.write(_canonical_json(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


__all__ = [
    "CANONICAL_RELATIVE_PATHS", "REVIEWED_PINS", "claim_stage", "expected_plan",
    "finalize_stage", "main", "materialize_hce", "materialize_projection",
    "publish_plan", "status_document", "verify_plan",
]
