#!/usr/bin/env python3
"""Generation-6 Omega teacher-search authority.

This module is deliberately independent of the training program.  It accepts
only the frozen, target-free routing/component authority, the pre-label seal,
the already-completed static-HCE receipt, and a pre-claimed Senpai HCE binary.
The first target-bearing operation is therefore necessarily later than the
exclusive teacher claim.

The public workflow is ``claim`` -> ``run``/``resume`` -> ``finalize``.  Every
search attempt is an immediately fsynced JSONL record.  Search results are not
accepted unless all four routed children of every root obtain an exact deep
centipawn score under the fixed Generation-6 protocol.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v6-move-decision-v1"

ROUTING_KIND = "omega-decision-v3-target-free-routing"
COMPONENT_KIND = "omega-nnue-king-state-v6-component-authority"
PRELABEL_KIND = "omega-nnue-king-state-v6-upstream-prelabel-seal"
HCE_COMPLETION_KIND = "omega-decision-v3-pretarget-hce-completion"
TEACHER_OPTIONS_KIND = "omega-decision-v3-teacher-options"
TEACHER_CLAIM_KIND = "omega-decision-v3-teacher-claim"
ATTEMPT_KIND = "omega-decision-v3-teacher-attempt"
LEDGER_COMPLETION_KIND = "omega-decision-v3-teacher-attempt-ledger-completion"
TEACHER_LABEL_KIND = "omega-nnue-king-state-v6-upstream-teacher-label"
TEACHER_MANIFEST_KIND = "omega-nnue-king-state-v6-upstream-teacher-completion"
PROJECTED_LABEL_KIND = "omega-nnue-king-state-v6-decision-label"
PROJECTED_MANIFEST_KIND = "omega-nnue-king-state-v6-label-manifest"
TEACHER_COMPLETION_KIND = "omega-decision-v3-teacher-completion"

PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
SPLITS = ("train", "validation", "heldOut")
CELLS = tuple(f"{phase}:{side}" for phase in PHASES for side in SIDES)
STAGES = ("shallow", "deep")
STAGE_NODES: Mapping[str, int] = {"shallow": 2_000, "deep": 50_000}
BUDGETS = {
    "shallowNodes": 2_000,
    "deepNodes": 50_000,
    "timeoutSeconds": 180,
    "maximumAttempts": 3,
    "workers": 4,
    "childrenPerRoot": 4,
}
FIXED_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Threads", "1"),
    ("Hash", "128"),
    ("Ponder", "false"),
    ("OwnBook", "false"),
    ("UCI_Chess960", "false"),
    ("UCI_Variant", "omega"),
    ("OmegaNNUEFile", "<empty>"),
    ("UseOmegaNNUE", "false"),
)
STATIC_HCE_PERSPECTIVE = (
    "integer centipawns from child side-to-move; child side is opposite parent side"
)
SCORE_LIMIT_CP = 1_000_000
MAX_TRANSCRIPT_LINES = 100_000
MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024
MAX_TRANSCRIPT_LINE_CHARS = 65_536
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

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
HCE_COMPLETION_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc", "claim",
        "prelabelSeal", "targetFreeRouting", "engine", "runner", "options",
        "transcript", "inputOrderSha256", "rows", "perspective",
        "targetRowsDecodedAtCompletion", "targetFieldsDecodedAtCompletion",
        "resultInformationRead", "finalStageSeal",
    }
)
OPTIONS_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "engineProtocol", "fixedOptions",
        "searchStages", "scorePolicy", "processPolicy",
    }
)
CLAIM_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "prelabelSeal", "targetFreeRouting", "componentMap", "engine", "runner",
        "options", "budgets", "inputOrderSha256", "plannedProjectionProducer",
        "plannedProjectedCorpusPath", "plannedProjectionManifestPath",
        "preTargetHceCompletion", "targetRowsDecodedAtClaim",
        "targetFieldsDecodedAtClaim", "resultInformationRead",
    }
)
ATTEMPT_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "sequence", "claimSha256",
        "rootId", "childId", "normalizedChildOfen", "stage", "nodes", "attempt",
        "startedUtc", "completedUtc", "elapsedMilliseconds", "processCommand",
        "workingDirectory", "environment", "uciCommandsSha256", "exitCode",
        "timedOut", "transcriptComplete", "stdoutLines", "stderrLines",
        "stdoutSha256", "stderrSha256", "outcome",
        "scoreCpChildStm", "reportedNodes", "exactCp", "resultInformationRead",
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
COMPLETION_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc", "claim",
        "prelabelSeal", "engine", "runner", "options", "budgets",
        "inputOrderSha256", "attemptLedger", "attemptLedgerCompletion",
        "teacherLabels", "teacherManifest", "projectionProducer",
        "projectedCorpus", "labelManifest", "finalStageSeal",
        "resultInformationRead",
    }
)

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_FAILURE_OUTCOMES = frozenset(
    {
        "timeout", "crash", "stderr", "protocol-error", "malformed-score",
        "mate-score", "bound-score", "node-underrun",
    }
)


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _sha256(_canonical_json(value))


def _exact_keys(value: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    if set(value) != set(fields):
        missing = sorted(set(fields) - set(value))
        extra = sorted(set(value) - set(fields))
        raise ValueError(f"{label} field inventory changed: missing={missing} extra={extra}")


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
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not strict JSON: {error}") from error


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int | None]:
    return info.st_dev, info.st_ino, info.st_size, getattr(info, "st_mtime_ns", None)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _check_path_chain(path: Path) -> None:
    absolute = _absolute(path)
    for item in (*reversed(absolute.parents), absolute):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or getattr(
            info, "st_file_attributes", 0
        ) & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"reparse path is forbidden: {item}")


def _snapshot(path: Path) -> tuple[dict[str, Any], bytes]:
    absolute = _absolute(path)
    _check_path_chain(absolute)
    before = os.lstat(absolute)
    if (
        not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_nlink", 1) != 1
        or getattr(before, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError(f"unsafe authority artifact: {absolute}")
    descriptor = os.open(
        absolute,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_nlink", 1) != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError(f"authority artifact changed before open: {absolute}")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
            digest.update(block)
        if _stat_key(os.fstat(descriptor)) != _stat_key(opened):
            raise ValueError(f"authority artifact changed while read: {absolute}")
    finally:
        os.close(descriptor)
    if _stat_key(os.lstat(absolute)) != _stat_key(before):
        raise ValueError(f"authority artifact path changed while read: {absolute}")
    payload = b"".join(chunks)
    return {
        "path": str(absolute),
        "bytes": len(payload),
        "sha256": digest.hexdigest(),
    }, payload


def _identity(path: Path) -> dict[str, Any]:
    return _snapshot(path)[0]


def _identity_shape(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} identity is not an object")
    _exact_keys(value, IDENTITY_FIELDS, f"{label} identity")
    if (
        type(value["path"]) is not str
        or not value["path"]
        or type(value["bytes"]) is not int
        or value["bytes"] < 0
        or type(value["sha256"]) is not str
        or _SHA256.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(value)


def _verified_identity(value: Any, label: str) -> dict[str, Any]:
    shaped = _identity_shape(value, label)
    actual = _identity(Path(shaped["path"]))
    if actual != shaped:
        raise ValueError(f"{label} identity changed")
    return shaped


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    identity, payload = _snapshot(path)
    value = _strict_json_bytes(payload, label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value, identity


def _load_jsonl(path: Path, label: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    identity, payload = _snapshot(path)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not strict UTF-8") from error
    if text.startswith("\ufeff"):
        raise ValueError(f"{label} has a UTF-8 BOM")
    if not text or not text.endswith("\n"):
        raise ValueError(f"{label} must be nonempty newline-terminated JSONL")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line:
            raise ValueError(f"{label}:{number} is blank")
        value = _strict_json_bytes(line.encode("utf-8"), f"{label}:{number}")
        if not isinstance(value, dict):
            raise ValueError(f"{label}:{number} is not an object")
        rows.append(value)
    return rows, identity


def _parse_timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{label} is not exact microsecond RFC3339 UTC")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    return parsed


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _utc_after(*values: str) -> str:
    floor = max((_parse_timestamp(value, "chronology input") for value in values), default=None)
    now = datetime.now(timezone.utc)
    if floor is not None and now <= floor:
        now = floor + timedelta(microseconds=1)
    return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _safe_planned_path(path: Path, *, require_absent: bool) -> Path:
    absolute = _absolute(path)
    if not absolute.parent.exists():
        raise ValueError(f"planned output parent does not exist: {absolute.parent}")
    _check_path_chain(absolute.parent)
    parent = os.lstat(absolute.parent)
    if not stat.S_ISDIR(parent.st_mode):
        raise ValueError(f"planned output parent is not a directory: {absolute.parent}")
    if require_absent and os.path.lexists(absolute):
        raise FileExistsError(f"planned output already exists: {absolute}")
    if not require_absent and os.path.lexists(absolute):
        _check_path_chain(absolute)
    return absolute


def _exclusive_bytes(path: Path, payload: bytes) -> dict[str, Any]:
    absolute = _safe_planned_path(path, require_absent=True)
    descriptor = os.open(
        absolute,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return _identity(absolute)


def _exclusive_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    _exclusive_bytes(path, _canonical_json(value))
    document, _ = _load_json(path, "exclusive JSON publication")
    if document != dict(value):
        raise ValueError("exclusive JSON publication differs after write")
    return document


def _exclusive_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("refusing to publish empty JSONL")
    payload = b"".join(_canonical_json(row) for row in rows)
    return _exclusive_bytes(path, payload)


def _child_side(ofen: str, label: str) -> str:
    fields = ofen.split(" ")
    if len(fields) != 6 or " ".join(fields) != ofen or fields[1] not in SIDES:
        raise ValueError(f"{label} is not normalized six-field OFEN")
    try:
        ofen.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} is not ASCII OFEN") from error
    return fields[1]


@dataclass(frozen=True)
class Child:
    root_id: str
    child_id: str
    ofen: str
    phase: str
    parent_side: str


@dataclass(frozen=True)
class Root:
    root_id: str
    source_root_id: str
    source_group_id: str
    phase: str
    parent_side: str
    children: tuple[Child, ...]


def _parse_routes(path: Path) -> tuple[tuple[Root, ...], dict[str, Any], list[dict[str, Any]]]:
    raw, identity = _load_jsonl(path, "target-free routing")
    roots: list[Root] = []
    root_ids: set[str] = set()
    child_ids: set[str] = set()
    canonical_rows: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        label = f"target-free routing row {index}"
        _exact_keys(row, ROUTING_FIELDS, label)
        if (
            type(row["schemaVersion"]) is not int
            or row["schemaVersion"] != SCHEMA_VERSION
            or row["kind"] != ROUTING_KIND
            or row["profileId"] != PROFILE_ID
            or type(row["rootId"]) is not str
            or not row["rootId"]
            or row["rootId"] in root_ids
            or type(row["sourceRootId"]) is not str
            or not row["sourceRootId"]
            or type(row["sourceGroupId"]) is not str
            or not row["sourceGroupId"]
            or row["phase"] not in PHASES
            or row["parentSideToMove"] not in SIDES
            or type(row["children"]) is not list
            or len(row["children"]) != BUDGETS["childrenPerRoot"]
        ):
            raise ValueError(f"{label} schema changed")
        children: list[Child] = []
        normalized_children: list[dict[str, str]] = []
        for child_index, child in enumerate(row["children"]):
            child_label = f"{label} child {child_index}"
            if not isinstance(child, dict):
                raise ValueError(f"{child_label} is not an object")
            _exact_keys(child, ROUTING_CHILD_FIELDS, child_label)
            if (
                type(child["childId"]) is not str
                or not child["childId"]
                or child["childId"] in child_ids
                or type(child["normalizedChildOfen"]) is not str
                or not child["normalizedChildOfen"]
            ):
                raise ValueError(f"{child_label} identity changed")
            ofen = child["normalizedChildOfen"]
            if _child_side(ofen, child_label) == row["parentSideToMove"]:
                raise ValueError(f"{child_label} side is not opposite its parent")
            child_ids.add(child["childId"])
            normalized_children.append(dict(child))
            children.append(
                Child(
                    row["rootId"], child["childId"], ofen, row["phase"],
                    row["parentSideToMove"],
                )
            )
        root_ids.add(row["rootId"])
        children.sort(key=lambda item: item.child_id)
        normalized_children.sort(key=lambda item: item["childId"])
        roots.append(
            Root(
                row["rootId"], row["sourceRootId"], row["sourceGroupId"],
                row["phase"], row["parentSideToMove"], tuple(children),
            )
        )
        canonical_rows.append({**{key: row[key] for key in row if key != "children"}, "children": normalized_children})
    if not roots:
        raise ValueError("target-free routing is empty")
    roots.sort(key=lambda item: item.root_id)
    by_root = {row["rootId"]: row for row in canonical_rows}
    canonical_rows = [by_root[root.root_id] for root in roots]
    return tuple(roots), identity, canonical_rows


def _children(roots: Sequence[Root]) -> tuple[Child, ...]:
    return tuple(child for root in roots for child in root.children)


def _hce_order(roots: Sequence[Root]) -> list[dict[str, str]]:
    return sorted(
        (
            {"childId": child.child_id, "normalizedChildOfen": child.ofen}
            for child in _children(roots)
        ),
        key=lambda row: row["childId"],
    )


def _parse_components(path: Path, roots: Sequence[Root]) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    raw, identity = _load_jsonl(path, "component map")
    routes = {root.root_id: root for root in roots}
    result: dict[str, dict[str, str]] = {}
    component_splits: dict[str, set[str]] = defaultdict(set)
    for index, row in enumerate(raw):
        label = f"component map row {index}"
        _exact_keys(row, COMPONENT_FIELDS, label)
        if (
            type(row["schemaVersion"]) is not int
            or row["schemaVersion"] != SCHEMA_VERSION
            or row["kind"] != COMPONENT_KIND
            or row["profileId"] != PROFILE_ID
            or type(row["rootId"]) is not str
            or row["rootId"] in result
            or type(row["leakageComponentId"]) is not str
            or not row["leakageComponentId"]
            or row["split"] not in SPLITS
            or type(row["sourceRootId"]) is not str
            or type(row["sourceGroupId"]) is not str
        ):
            raise ValueError(f"{label} schema changed")
        route = routes.get(row["rootId"])
        if (
            route is None
            or row["sourceRootId"] != route.source_root_id
            or row["sourceGroupId"] != route.source_group_id
        ):
            raise ValueError(f"{label} differs from target-free routing")
        result[row["rootId"]] = dict(row)
        component_splits[row["leakageComponentId"]].add(row["split"])
    if set(result) != set(routes):
        raise ValueError("component/routing root inventories differ")
    if any(len(splits) != 1 for splits in component_splits.values()):
        raise ValueError("a leakage component crosses splits")
    return result, identity


def _parse_prelabel(
    path: Path,
    *,
    routing_identity: Mapping[str, Any],
    component_identity: Mapping[str, Any],
    component_count: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    document, identity = _load_json(path, "prelabel seal")
    _exact_keys(document, PRELABEL_FIELDS, "prelabel seal")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != PRELABEL_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "frozen-before-any-teacher-target-decode"
        or type(document["componentRows"]) is not int
        or document["componentRows"] != component_count
        or type(document["targetFieldsDecodedAtSeal"]) is not int
        or document["targetFieldsDecodedAtSeal"] != 0
        or type(document["targetFieldsEmittedAtSeal"]) is not int
        or document["targetFieldsEmittedAtSeal"] != 0
    ):
        raise ValueError("prelabel seal schema/status changed")
    _parse_timestamp(document["createdUtc"], "prelabel createdUtc")
    if _identity_shape(document["targetFreeRouting"], "prelabel routing") != dict(routing_identity):
        raise ValueError("prelabel does not bind this routing")
    if _identity_shape(document["componentMap"], "prelabel component map") != dict(component_identity):
        raise ValueError("prelabel does not bind this component map")
    for field in (
        "terminalClassifierLineage", "priorForbiddenRegistry", "initializerManifest",
        "sourceRootManifest", "sourceChildrenManifest", "producer",
    ):
        _identity_shape(document[field], f"prelabel {field}")
    catalogs = document["priorForbiddenCatalogs"]
    if type(catalogs) is not list or not catalogs:
        raise ValueError("prelabel prior-forbidden catalog is empty")
    for index, value in enumerate(catalogs):
        _identity_shape(value, f"prelabel prior-forbidden catalog {index}")
    return document, identity


def _parse_hce_completion(
    path: Path,
    *,
    prelabel_identity: Mapping[str, Any],
    routing_identity: Mapping[str, Any],
    roots: Sequence[Root],
) -> tuple[dict[str, Any], dict[str, Any]]:
    document, identity = _load_json(path, "pre-target HCE completion")
    _exact_keys(document, HCE_COMPLETION_FIELDS, "pre-target HCE completion")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != HCE_COMPLETION_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "completed-before-teacher-and-target-decode"
        or type(document["rows"]) is not int
        or document["rows"] != len(_children(roots))
        or document["perspective"] != STATIC_HCE_PERSPECTIVE
        or type(document["targetRowsDecodedAtCompletion"]) is not int
        or document["targetRowsDecodedAtCompletion"] != 0
        or type(document["targetFieldsDecodedAtCompletion"]) is not int
        or document["targetFieldsDecodedAtCompletion"] != 0
        or document["resultInformationRead"] is not False
        or document["finalStageSeal"] is not True
    ):
        raise ValueError("pre-target HCE completion schema/status changed")
    _parse_timestamp(document["createdUtc"], "pre-target HCE completion createdUtc")
    if _identity_shape(document["prelabelSeal"], "HCE prelabel") != dict(prelabel_identity):
        raise ValueError("pre-target HCE completion does not bind this prelabel")
    if _identity_shape(document["targetFreeRouting"], "HCE routing") != dict(routing_identity):
        raise ValueError("pre-target HCE completion does not bind this routing")
    for field in ("claim", "engine", "runner", "options", "transcript"):
        _identity_shape(document[field], f"HCE completion {field}")
    if document["inputOrderSha256"] != _digest(_hce_order(roots)):
        raise ValueError("pre-target HCE completion input order changed")
    return document, identity


def teacher_options_document() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TEACHER_OPTIONS_KIND,
        "profileId": PROFILE_ID,
        "engineProtocol": "UCI",
        "fixedOptions": [
            {"name": name, "value": value} for name, value in FIXED_OPTIONS
        ],
        "searchStages": [
            {"stage": stage, "nodes": STAGE_NODES[stage]} for stage in STAGES
        ],
        "scorePolicy": {
            "perspective": "integer centipawns from child side-to-move",
            "accepted": (
                "maximum reported nodes reach the budget; the last maximum-node info "
                "line is an unscored attempted depth D; the latest score at exactly "
                "completed depth D-1 is an exact non-bound cp"
            ),
            "mateScoresAccepted": False,
            "boundScoresAccepted": False,
            "principalVariationUsedForSelection": False,
            "gameResultsUsedForSelection": False,
        },
        "processPolicy": {
            "workingDirectory": "engine parent",
            "environment": {},
            "stdinEncoding": "strict UTF-8 UCI lines",
            "stdoutEncoding": "strict UTF-8 UCI lines",
            "stderr": "must be empty",
            "startupTimeoutSeconds": 10,
            "searchTimeoutSeconds": BUDGETS["timeoutSeconds"],
            "shutdownTimeoutSeconds": 2,
            "windowsCreationFlags": "CREATE_NO_WINDOW",
        },
    }


def publish_teacher_options(path: Path) -> dict[str, Any]:
    return _exclusive_json(path, teacher_options_document())


def _verify_options(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    document, identity = _load_json(path, "teacher options")
    _exact_keys(document, OPTIONS_FIELDS, "teacher options")
    if document != teacher_options_document():
        raise ValueError("teacher options differ from the fixed Generation-6 contract")
    return document, identity


def _routing_digest(canonical_rows: Sequence[Mapping[str, Any]]) -> str:
    return _digest(list(canonical_rows))


def _assert_distinct_paths(paths: Mapping[str, Path]) -> None:
    normalized = {
        role: os.path.normcase(os.path.normpath(str(_absolute(path))))
        for role, path in paths.items()
    }
    if len(set(normalized.values())) != len(normalized):
        raise ValueError("teacher authority roles share a path")


def _claim_document(
    *,
    routing: Path,
    components: Path,
    prelabel: Path,
    hce_completion: Path,
    engine: Path,
    options: Path,
    projection_producer: Path,
    projected_corpus: Path,
    projection_manifest: Path,
    created_utc: str,
    require_outputs_absent: bool,
) -> dict[str, Any]:
    roots, routing_identity, canonical_rows = _parse_routes(routing)
    component_map, component_identity = _parse_components(components, roots)
    prelabel_document, prelabel_identity = _parse_prelabel(
        prelabel,
        routing_identity=routing_identity,
        component_identity=component_identity,
        component_count=len(component_map),
    )
    hce_document, hce_identity = _parse_hce_completion(
        hce_completion,
        prelabel_identity=prelabel_identity,
        routing_identity=routing_identity,
        roots=roots,
    )
    engine_identity = _identity(engine)
    runner_identity = _identity(Path(__file__))
    _, options_identity = _verify_options(options)
    projection_producer_identity = _identity(projection_producer)
    corpus_path = _safe_planned_path(projected_corpus, require_absent=require_outputs_absent)
    manifest_path = _safe_planned_path(
        projection_manifest, require_absent=require_outputs_absent
    )
    _assert_distinct_paths(
        {
            "routing": Path(routing_identity["path"]),
            "components": Path(component_identity["path"]),
            "prelabel": Path(prelabel_identity["path"]),
            "hceCompletion": Path(hce_identity["path"]),
            "engine": Path(engine_identity["path"]),
            "runner": Path(runner_identity["path"]),
            "options": Path(options_identity["path"]),
            "projectionProducer": Path(projection_producer_identity["path"]),
            "projectedCorpus": corpus_path,
            "projectionManifest": manifest_path,
        }
    )
    created = _parse_timestamp(created_utc, "teacher claim createdUtc")
    prelabel_created = _parse_timestamp(
        prelabel_document["createdUtc"], "prelabel createdUtc"
    )
    hce_created = _parse_timestamp(
        hce_document["createdUtc"], "HCE completion createdUtc"
    )
    if hce_created <= prelabel_created:
        raise ValueError("static-HCE completion must follow the prelabel seal")
    if created <= prelabel_created:
        raise ValueError("teacher claim must follow the prelabel seal")
    if created <= hce_created:
        raise ValueError("teacher claim must follow static-HCE completion")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TEACHER_CLAIM_KIND,
        "profileId": PROFILE_ID,
        "status": "claimed-before-first-teacher-target-decode",
        "createdUtc": created_utc,
        "prelabelSeal": prelabel_identity,
        "targetFreeRouting": routing_identity,
        "componentMap": component_identity,
        "engine": engine_identity,
        "runner": runner_identity,
        "options": options_identity,
        "budgets": dict(BUDGETS),
        "inputOrderSha256": _routing_digest(canonical_rows),
        "plannedProjectionProducer": projection_producer_identity,
        "plannedProjectedCorpusPath": str(corpus_path),
        "plannedProjectionManifestPath": str(manifest_path),
        "preTargetHceCompletion": hce_identity,
        "targetRowsDecodedAtClaim": 0,
        "targetFieldsDecodedAtClaim": 0,
        "resultInformationRead": False,
    }


def publish_teacher_claim(
    path: Path,
    *,
    routing: Path,
    components: Path,
    prelabel: Path,
    hce_completion: Path,
    engine: Path,
    options: Path,
    projection_producer: Path,
    projected_corpus: Path,
    projection_manifest: Path,
    created_utc: str | None = None,
) -> dict[str, Any]:
    if os.path.lexists(_absolute(path)):
        raise FileExistsError(f"teacher claim already exists: {_absolute(path)}")
    created = created_utc or _utc_now()
    document = _claim_document(
        routing=routing,
        components=components,
        prelabel=prelabel,
        hce_completion=hce_completion,
        engine=engine,
        options=options,
        projection_producer=projection_producer,
        projected_corpus=projected_corpus,
        projection_manifest=projection_manifest,
        created_utc=created,
        require_outputs_absent=True,
    )
    return _exclusive_json(path, document)


@dataclass(frozen=True)
class ClaimContext:
    path: Path
    identity: Mapping[str, Any]
    document: Mapping[str, Any]
    roots: tuple[Root, ...]
    components: Mapping[str, Mapping[str, str]]


def _verify_claim(path: Path) -> ClaimContext:
    document, claim_identity = _load_json(path, "teacher claim")
    _exact_keys(document, CLAIM_FIELDS, "teacher claim")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != TEACHER_CLAIM_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "claimed-before-first-teacher-target-decode"
        or document["budgets"] != BUDGETS
        or type(document["targetRowsDecodedAtClaim"]) is not int
        or document["targetRowsDecodedAtClaim"] != 0
        or type(document["targetFieldsDecodedAtClaim"]) is not int
        or document["targetFieldsDecodedAtClaim"] != 0
        or document["resultInformationRead"] is not False
    ):
        raise ValueError("teacher claim header changed")
    links = {
        field: _verified_identity(document[field], f"teacher claim {field}")
        for field in (
            "prelabelSeal", "targetFreeRouting", "componentMap", "engine", "runner",
            "options", "plannedProjectionProducer", "preTargetHceCompletion",
        )
    }
    if links["runner"] != _identity(Path(__file__)):
        raise ValueError("teacher runner identity changed")
    roots, routing_identity, canonical_rows = _parse_routes(
        Path(links["targetFreeRouting"]["path"])
    )
    components, component_identity = _parse_components(
        Path(links["componentMap"]["path"]), roots
    )
    prelabel_document, prelabel_identity = _parse_prelabel(
        Path(links["prelabelSeal"]["path"]),
        routing_identity=routing_identity,
        component_identity=component_identity,
        component_count=len(components),
    )
    hce_document, hce_identity = _parse_hce_completion(
        Path(links["preTargetHceCompletion"]["path"]),
        prelabel_identity=prelabel_identity,
        routing_identity=routing_identity,
        roots=roots,
    )
    _verify_options(Path(links["options"]["path"]))
    if (
        links["targetFreeRouting"] != routing_identity
        or links["componentMap"] != component_identity
        or links["prelabelSeal"] != prelabel_identity
        or links["preTargetHceCompletion"] != hce_identity
        or document["inputOrderSha256"] != _routing_digest(canonical_rows)
        or document["plannedProjectedCorpusPath"]
        != str(_safe_planned_path(Path(document["plannedProjectedCorpusPath"]), require_absent=False))
        or document["plannedProjectionManifestPath"]
        != str(_safe_planned_path(Path(document["plannedProjectionManifestPath"]), require_absent=False))
    ):
        raise ValueError("teacher claim dependency or order changed")
    created = _parse_timestamp(document["createdUtc"], "teacher claim createdUtc")
    prelabel_created = _parse_timestamp(
        prelabel_document["createdUtc"], "prelabel createdUtc"
    )
    hce_created = _parse_timestamp(
        hce_document["createdUtc"], "HCE completion createdUtc"
    )
    if hce_created <= prelabel_created:
        raise ValueError("teacher claim/HCE precondition chronology changed")
    if created <= prelabel_created:
        raise ValueError("teacher claim chronology changed")
    if created <= hce_created:
        raise ValueError("teacher claim/HCE chronology changed")
    return ClaimContext(_absolute(path), claim_identity, document, roots, components)


class SearchFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Uci:
    def __init__(self, command: Sequence[str], cwd: Path):
        self.process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            cwd=cwd,
            env={},
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stdout: list[str] = []
        self.stderr: list[str] = []
        self.transcript_complete = True
        self._transcript_lines = 0
        self._transcript_bytes = 0
        self._transcript_lock = threading.Lock()
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self.threads: list[threading.Thread] = []
        for channel, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr)):
            thread = threading.Thread(
                target=self._reader,
                args=(channel, stream),
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)

    def _reader(self, channel: str, stream: Any) -> None:
        try:
            while True:
                line = stream.readline(MAX_TRANSCRIPT_LINE_CHARS + 1)
                if not line:
                    break
                if (
                    len(line) > MAX_TRANSCRIPT_LINE_CHARS
                    and not line.endswith(("\n", "\r"))
                ):
                    self._overflow(channel)
                    return
                text = line.rstrip("\r\n")
                encoded_bytes = len((text + "\n").encode("utf-8"))
                with self._transcript_lock:
                    if (
                        self._transcript_lines + 1 > MAX_TRANSCRIPT_LINES
                        or self._transcript_bytes + encoded_bytes
                        > MAX_TRANSCRIPT_BYTES
                    ):
                        overflow = True
                    else:
                        self._transcript_lines += 1
                        self._transcript_bytes += encoded_bytes
                        overflow = False
                if overflow:
                    self._overflow(channel)
                    return
                self.events.put((channel, text))
        except UnicodeError as error:
            self.events.put(("decode-error", f"{channel}: {error}"))
        finally:
            self.events.put((f"{channel}-eof", ""))

    def _overflow(self, channel: str) -> None:
        with self._transcript_lock:
            first = self.transcript_complete
            self.transcript_complete = False
        if first:
            self.events.put(("overflow", channel))
            if self.process.poll() is None:
                self.process.kill()

    def send(self, command: str) -> None:
        if self.process.poll() is not None:
            raise SearchFailure("crash", f"engine exited with {self.process.returncode}")
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise SearchFailure("crash", "engine input pipe closed") from error

    def until(self, predicate: Any, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        eof: set[str] = set()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SearchFailure("timeout", "UCI response timed out")
            try:
                channel, line = self.events.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                if self.process.poll() is not None and eof == {"stdout", "stderr"}:
                    raise SearchFailure(
                        "crash", f"engine exited with {self.process.returncode}"
                    )
                continue
            if channel == "stdout":
                self.stdout.append(line)
                if predicate(line):
                    return
            elif channel == "stderr":
                self.stderr.append(line)
            elif channel == "decode-error":
                raise SearchFailure("protocol-error", line)
            elif channel == "overflow":
                raise SearchFailure("protocol-error", f"{line} transcript exceeded bounds")
            elif channel.endswith("-eof"):
                eof.add(channel[:-4])
                if self.process.poll() is not None and eof == {"stdout", "stderr"}:
                    raise SearchFailure(
                        "crash", f"engine exited with {self.process.returncode}"
                    )

    def finish(self, *, timed_out: bool) -> tuple[int | None, bool]:
        forced = False
        if self.process.poll() is None:
            if timed_out:
                self.process.kill()
                forced = True
            else:
                try:
                    self.send("quit")
                except SearchFailure:
                    pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
                forced = True
        for thread in self.threads:
            thread.join(timeout=1)
        while True:
            try:
                channel, line = self.events.get_nowait()
            except queue.Empty:
                break
            if channel == "stdout":
                self.stdout.append(line)
            elif channel == "stderr":
                self.stderr.append(line)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        return self.process.returncode, forced


def _engine_command(engine: Path) -> list[str]:
    return [str(_absolute(engine))]


@contextmanager
def _engine_guard(engine: Path, expected_identity: Mapping[str, Any]):
    """Hold the claimed engine stable across Windows process creation/execution."""

    absolute = _absolute(engine)
    if os.name != "nt":
        descriptor = os.open(
            absolute,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if _identity(absolute) != dict(expected_identity):
                raise ValueError("teacher engine identity changed before execution")
            yield
            if _identity(absolute) != dict(expected_identity):
                raise ValueError("teacher engine identity changed during execution")
        finally:
            os.close(descriptor)
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    handle = create_file(
        str(absolute),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ: deny concurrent write/delete/rename
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        raise OSError(error, f"cannot lock teacher engine for execution: {absolute}")
    try:
        if _identity(absolute) != dict(expected_identity):
            raise ValueError("teacher engine identity changed before execution")
        yield
        if _identity(absolute) != dict(expected_identity):
            raise ValueError("teacher engine identity changed during execution")
    finally:
        close_handle(handle)


def _uci_commands(child: Child, nodes: int) -> list[str]:
    return [
        "uci",
        *(f"setoption name {name} value {value}" for name, value in FIXED_OPTIONS),
        "isready",
        "ucinewgame",
        "setoption name Clear Hash",
        "isready",
        f"position fen {child.ofen}",
        f"go nodes {nodes}",
    ]


def _parse_score(lines: Sequence[str], requested_nodes: int) -> tuple[int, int]:
    infos: list[dict[str, Any]] = []
    maximum_nodes = 0
    for line_index, line in enumerate(lines):
        tokens = line.split()
        if not tokens or tokens[0] != "info":
            continue
        parsed: dict[str, Any] = {
            "lineIndex": line_index,
            "scoreTokenPresent": "score" in tokens,
        }
        if "nodes" in tokens:
            index = tokens.index("nodes")
            if index + 1 >= len(tokens) or _INTEGER.fullmatch(tokens[index + 1]) is None:
                raise SearchFailure("malformed-score", "malformed nodes token")
            parsed["nodes"] = int(tokens[index + 1])
            maximum_nodes = max(maximum_nodes, parsed["nodes"])
        if "score" in tokens:
            index = tokens.index("score")
            if index + 2 >= len(tokens) or _INTEGER.fullmatch(tokens[index + 2]) is None:
                raise SearchFailure("malformed-score", "malformed score token")
            parsed["scoreType"] = tokens[index + 1]
            parsed["score"] = int(tokens[index + 2])
            parsed["bound"] = "lowerbound" in tokens or "upperbound" in tokens
        if "depth" in tokens:
            index = tokens.index("depth")
            if index + 1 < len(tokens) and _INTEGER.fullmatch(tokens[index + 1]):
                parsed["depth"] = int(tokens[index + 1])
        infos.append(parsed)
    if maximum_nodes < requested_nodes:
        raise SearchFailure(
            "node-underrun",
            f"search reported {maximum_nodes}/{requested_nodes} nodes",
        )
    max_node_infos = [
        info for info in infos if int(info.get("nodes", -1)) == maximum_nodes
    ]
    if not max_node_infos:
        raise SearchFailure("malformed-score", "search emitted no max-node progress line")
    attempted = max_node_infos[-1]
    if attempted["scoreTokenPresent"] or "depth" not in attempted:
        raise SearchFailure(
            "malformed-score",
            "search lacks final unscored attempted-depth evidence at node cap",
        )
    attempted_depth = int(attempted["depth"])
    scored = [
        info
        for info in infos
        if "scoreType" in info
        and "depth" in info
        and int(info["depth"]) < attempted_depth
    ]
    if not scored:
        raise SearchFailure(
            "malformed-score", "search has no scored iteration below attempted depth"
        )
    completed_depth = max(int(info["depth"]) for info in scored)
    if completed_depth != attempted_depth - 1:
        raise SearchFailure(
            "malformed-score",
            "search lacks an exact score at the immediately prior completed depth",
        )
    latest = [
        info for info in scored if int(info["depth"]) == completed_depth
    ][-1]
    if latest["scoreType"] == "mate":
        raise SearchFailure("mate-score", "mate score is not an exact cp target")
    if latest["scoreType"] != "cp":
        raise SearchFailure("malformed-score", "unknown score type")
    if latest["bound"]:
        raise SearchFailure("bound-score", "bound score is not exact")
    score = int(latest["score"])
    if score < -SCORE_LIMIT_CP or score > SCORE_LIMIT_CP:
        raise SearchFailure("malformed-score", "cp score is out of bounds")
    return score, maximum_nodes


def _perform_attempt(
    context: ClaimContext,
    child: Child,
    stage: str,
    attempt: int,
    sequence: int,
) -> dict[str, Any]:
    nodes = STAGE_NODES[stage]
    engine = Path(str(context.document["engine"]["path"]))
    command = _engine_command(engine)
    uci_commands = _uci_commands(child, nodes)
    started_utc = _utc_after(str(context.document["createdUtc"]))
    started_tick = time.monotonic()
    session: _Uci | None = None
    outcome = "protocol-error"
    score: int | None = None
    reported_nodes: int | None = None
    timed_out = False
    exit_code: int | None = None
    forced = False
    try:
        with _engine_guard(engine, context.document["engine"]):
            try:
                session = _Uci(command, engine.parent)
                session.send("uci")
                session.until(lambda line: line == "uciok", 10)
                names = [
                    line[8:].strip()
                    for line in session.stdout
                    if line.startswith("id name ")
                ]
                if not names or not names[-1].lower().startswith("senpai"):
                    raise SearchFailure(
                        "protocol-error", "UCI engine did not identify as Senpai"
                    )
                for name, value in FIXED_OPTIONS:
                    session.send(f"setoption name {name} value {value}")
                session.send("isready")
                session.until(lambda line: line == "readyok", 10)
                session.send("ucinewgame")
                session.send("setoption name Clear Hash")
                session.send("isready")
                session.until(lambda line: line == "readyok", 10)
                search_start = len(session.stdout)
                session.send(f"position fen {child.ofen}")
                session.send(f"go nodes {nodes}")
                session.until(
                    lambda line: line.startswith("bestmove "),
                    BUDGETS["timeoutSeconds"],
                )
                search_lines = session.stdout[search_start:]
                bestmoves = [
                    line for line in search_lines if line.startswith("bestmove ")
                ]
                if len(bestmoves) != 1:
                    raise SearchFailure(
                        "protocol-error", "search returned a non-unique bestmove"
                    )
                bestmove_tokens = bestmoves[0].split()
                if (
                    len(bestmove_tokens) < 2
                    or bestmove_tokens[1] in {"0000", "(none)"}
                ):
                    raise SearchFailure(
                        "protocol-error", "search returned no legal bestmove"
                    )
                score, reported_nodes = _parse_score(search_lines, nodes)
                outcome = "success"
            except SearchFailure as error:
                outcome = error.code
                timed_out = error.code == "timeout"
            except (OSError, subprocess.SubprocessError, UnicodeError):
                outcome = "crash"
            finally:
                if session is not None:
                    exit_code, forced = session.finish(timed_out=timed_out)
                    if not session.transcript_complete:
                        outcome = "protocol-error"
                        score = None
                        reported_nodes = None
                    if session.stderr and outcome == "success":
                        outcome = "stderr"
                        score = None
                        reported_nodes = None
                    if (exit_code != 0 or forced) and outcome == "success":
                        outcome = "crash"
                        score = None
                        reported_nodes = None
    except (OSError, ValueError):
        outcome = "protocol-error"
        score = None
        reported_nodes = None
    if outcome not in _FAILURE_OUTCOMES and outcome != "success":
        outcome = "protocol-error"
    completed_utc = _utc_after(started_utc)
    elapsed = max(0, int(round((time.monotonic() - started_tick) * 1000)))
    stdout_payload = (
        b"" if session is None else "".join(line + "\n" for line in session.stdout).encode("utf-8")
    )
    stderr_payload = (
        b"" if session is None else "".join(line + "\n" for line in session.stderr).encode("utf-8")
    )
    transcript_complete = session is None or session.transcript_complete
    if outcome != "success":
        score = None
        reported_nodes = None
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ATTEMPT_KIND,
        "profileId": PROFILE_ID,
        "sequence": sequence,
        "claimSha256": context.identity["sha256"],
        "rootId": child.root_id,
        "childId": child.child_id,
        "normalizedChildOfen": child.ofen,
        "stage": stage,
        "nodes": nodes,
        "attempt": attempt,
        "startedUtc": started_utc,
        "completedUtc": completed_utc,
        "elapsedMilliseconds": elapsed,
        "processCommand": command,
        "workingDirectory": str(_absolute(engine.parent)),
        "environment": {},
        "uciCommandsSha256": _digest(uci_commands),
        "exitCode": exit_code,
        "timedOut": timed_out,
        "transcriptComplete": transcript_complete,
        "stdoutLines": [] if session is None else list(session.stdout),
        "stderrLines": [] if session is None else list(session.stderr),
        "stdoutSha256": _sha256(stdout_payload) if transcript_complete else None,
        "stderrSha256": _sha256(stderr_payload) if transcript_complete else None,
        "outcome": outcome,
        "scoreCpChildStm": score,
        "reportedNodes": reported_nodes,
        "exactCp": outcome == "success",
        "resultInformationRead": False,
    }


def _transcript_payload(value: Any, label: str) -> bytes:
    if type(value) is not list:
        raise ValueError(f"{label} is not an exact line array")
    if len(value) > MAX_TRANSCRIPT_LINES:
        raise ValueError(f"{label} exceeds the line bound")
    lines: list[str] = []
    for index, line in enumerate(value):
        if (
            type(line) is not str
            or "\n" in line
            or "\r" in line
            or len(line) > MAX_TRANSCRIPT_LINE_CHARS
        ):
            raise ValueError(f"{label} line {index} is malformed")
        lines.append(line)
    payload = "".join(line + "\n" for line in lines).encode("utf-8")
    if len(payload) > MAX_TRANSCRIPT_BYTES:
        raise ValueError(f"{label} exceeds the byte bound")
    return payload


def _verify_bestmove_evidence(lines: Sequence[str], label: str) -> None:
    bestmoves = [line for line in lines if line.startswith("bestmove ")]
    if len(bestmoves) != 1:
        raise ValueError(f"{label} lacks a unique bestmove completion marker")
    tokens = bestmoves[0].split()
    if len(tokens) < 2 or tokens[1] in {"0000", "(none)"}:
        raise ValueError(f"{label} has a terminal/malformed bestmove")


def _validate_attempt(
    row: Mapping[str, Any],
    *,
    context: ClaimContext,
    child: Child,
    stage: str,
    attempt: int,
    sequence: int,
) -> None:
    label = f"attempt ledger row {sequence}"
    _exact_keys(row, ATTEMPT_FIELDS, label)
    nodes = STAGE_NODES[stage]
    engine = Path(str(context.document["engine"]["path"]))
    if (
        type(row["schemaVersion"]) is not int
        or row["schemaVersion"] != SCHEMA_VERSION
        or row["kind"] != ATTEMPT_KIND
        or row["profileId"] != PROFILE_ID
        or type(row["sequence"]) is not int
        or row["sequence"] != sequence
        or row["claimSha256"] != context.identity["sha256"]
        or row["rootId"] != child.root_id
        or row["childId"] != child.child_id
        or row["normalizedChildOfen"] != child.ofen
        or row["stage"] != stage
        or type(row["nodes"]) is not int
        or row["nodes"] != nodes
        or type(row["attempt"]) is not int
        or row["attempt"] != attempt
        or type(row["elapsedMilliseconds"]) is not int
        or row["elapsedMilliseconds"] < 0
        or row["processCommand"] != _engine_command(engine)
        or row["workingDirectory"] != str(_absolute(engine.parent))
        or row["environment"] != {}
        or row["uciCommandsSha256"] != _digest(_uci_commands(child, nodes))
        or row["resultInformationRead"] is not False
    ):
        raise ValueError(f"{label} command/order contract changed")
    started = _parse_timestamp(row["startedUtc"], f"{label} startedUtc")
    completed = _parse_timestamp(row["completedUtc"], f"{label} completedUtc")
    if (
        started <= _parse_timestamp(context.document["createdUtc"], "claim createdUtc")
        or completed <= started
    ):
        raise ValueError(f"{label} chronology changed")
    stdout_payload = _transcript_payload(row["stdoutLines"], f"{label} stdout")
    stderr_payload = _transcript_payload(row["stderrLines"], f"{label} stderr")
    if len(stdout_payload) + len(stderr_payload) > MAX_TRANSCRIPT_BYTES:
        raise ValueError(f"{label} combined transcript exceeds the byte bound")
    if type(row["transcriptComplete"]) is not bool:
        raise ValueError(f"{label} transcriptComplete is not exact boolean")
    if row["transcriptComplete"]:
        for field in ("stdoutSha256", "stderrSha256"):
            if type(row[field]) is not str or _SHA256.fullmatch(row[field]) is None:
                raise ValueError(f"{label} has malformed {field}")
        if (
            row["stdoutSha256"] != _sha256(stdout_payload)
            or row["stderrSha256"] != _sha256(stderr_payload)
        ):
            raise ValueError(f"{label} transcript hash differs from retained lines")
    elif (
        row["outcome"] != "protocol-error"
        or row["stdoutSha256"] is not None
        or row["stderrSha256"] is not None
    ):
        raise ValueError(f"{label} incomplete transcript claims unavailable bytes")
    if type(row["timedOut"]) is not bool:
        raise ValueError(f"{label} timedOut is not exact boolean")
    if row["exitCode"] is not None and type(row["exitCode"]) is not int:
        raise ValueError(f"{label} exitCode is not exact integer/null")
    search_completed_outcomes = {
        "success", "stderr", "malformed-score", "mate-score", "bound-score",
        "node-underrun",
    }
    if row["outcome"] in search_completed_outcomes:
        _verify_bestmove_evidence(row["stdoutLines"], label)
    if row["outcome"] == "success":
        stdout_lines = row["stdoutLines"]
        try:
            replay_score, replay_nodes = _parse_score(stdout_lines, nodes)
        except SearchFailure as error:
            raise ValueError(f"{label} retained score evidence is invalid") from error
        if (
            row["timedOut"] is not False
            or row["transcriptComplete"] is not True
            or row["exitCode"] != 0
            or row["stderrLines"] != []
            or row["stderrSha256"] != EMPTY_SHA256
            or type(row["scoreCpChildStm"]) is not int
            or not -SCORE_LIMIT_CP <= row["scoreCpChildStm"] <= SCORE_LIMIT_CP
            or type(row["reportedNodes"]) is not int
            or row["reportedNodes"] < nodes
            or row["scoreCpChildStm"] != replay_score
            or row["reportedNodes"] != replay_nodes
            or row["exactCp"] is not True
        ):
            raise ValueError(f"{label} successful process outcome is incoherent")
    else:
        if (
            row["outcome"] not in _FAILURE_OUTCOMES
            or row["scoreCpChildStm"] is not None
            or row["reportedNodes"] is not None
            or row["exactCp"] is not False
            or (row["outcome"] == "timeout" and row["timedOut"] is not True)
            or (row["outcome"] != "timeout" and row["timedOut"] is not False)
        ):
            raise ValueError(f"{label} failed process outcome is incoherent")
        if row["outcome"] == "stderr" and row["stderrLines"] == []:
            raise ValueError(f"{label} claims stderr without retained stderr")
        if row["outcome"] == "stderr":
            try:
                _parse_score(row["stdoutLines"], nodes)
            except SearchFailure as error:
                raise ValueError(
                    f"{label} stderr failure lacks otherwise-valid score evidence"
                ) from error
        if row["outcome"] == "crash" and (
            row["exitCode"] == 0
            or (
                row["exitCode"] is None
                and (row["stdoutLines"] != [] or row["stderrLines"] != [])
            )
        ):
            raise ValueError(f"{label} claims a crash without a failed process")
        if row["outcome"] in {
            "malformed-score", "mate-score", "bound-score", "node-underrun"
        }:
            try:
                _parse_score(row["stdoutLines"], nodes)
            except SearchFailure as error:
                if error.code != row["outcome"]:
                    raise ValueError(
                        f"{label} failure code differs from transcript replay"
                    ) from error
            else:
                raise ValueError(f"{label} claims score rejection for valid evidence")


@dataclass(frozen=True)
class LedgerState:
    rows: tuple[Mapping[str, Any], ...]
    identity: Mapping[str, Any] | None
    next_stage: str | None
    next_attempt: int | None
    next_children: tuple[Child, ...]
    complete: bool
    exhausted: bool
    deep_scores: Mapping[str, int]


def _ledger_state(path: Path, context: ClaimContext, *, allow_absent: bool) -> LedgerState:
    if not os.path.lexists(_absolute(path)):
        if not allow_absent:
            raise FileNotFoundError(f"attempt ledger does not exist: {_absolute(path)}")
        rows: list[dict[str, Any]] = []
        identity: Mapping[str, Any] | None = None
    else:
        rows, identity = _load_jsonl(path, "teacher attempt ledger")
    cursor = 0
    ordered = _children(context.roots)
    deep_scores: dict[str, int] = {}
    for stage in STAGES:
        succeeded: set[str] = set()
        for attempt in range(1, BUDGETS["maximumAttempts"] + 1):
            wave = tuple(child for child in ordered if child.child_id not in succeeded)
            if not wave:
                break
            for index, child in enumerate(wave):
                if cursor == len(rows):
                    return LedgerState(
                        tuple(rows), identity, stage, attempt, wave[index:], False, False,
                        deep_scores,
                    )
                row = rows[cursor]
                _validate_attempt(
                    row,
                    context=context,
                    child=child,
                    stage=stage,
                    attempt=attempt,
                    sequence=cursor,
                )
                if row["outcome"] == "success":
                    succeeded.add(child.child_id)
                    if stage == "deep":
                        deep_scores[child.child_id] = int(row["scoreCpChildStm"])
                cursor += 1
        if len(succeeded) != len(ordered):
            if cursor != len(rows):
                raise ValueError("attempt ledger continues after an exhausted stage")
            return LedgerState(
                tuple(rows), identity, None, None, (), False, True, deep_scores
            )
    if cursor != len(rows):
        raise ValueError("attempt ledger has records after complete deep coverage")
    return LedgerState(tuple(rows), identity, None, None, (), True, False, deep_scores)


def _append_attempt(
    path: Path,
    row: Mapping[str, Any],
    expected_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    absolute = _absolute(path)
    payload = _canonical_json(row)
    if expected_identity is None:
        descriptor = os.open(
            _safe_planned_path(absolute, require_absent=True),
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_APPEND
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    else:
        if _identity(absolute) != dict(expected_identity):
            raise ValueError("attempt ledger changed before append")
        descriptor = os.open(
            absolute,
            os.O_WRONLY
            | os.O_APPEND
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            opened.st_size != expected_identity["bytes"]
            or (opened.st_dev, opened.st_ino)
            != (os.lstat(absolute).st_dev, os.lstat(absolute).st_ino)
        ):
            os.close(descriptor)
            raise ValueError("attempt ledger changed while opened for append")
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return _identity(absolute)


def run_teacher(claim: Path, ledger: Path, *, resume: bool) -> LedgerState:
    context = _verify_claim(claim)
    if resume and not os.path.lexists(_absolute(ledger)):
        raise FileNotFoundError("resume requires an existing attempt ledger")
    if not resume and os.path.lexists(_absolute(ledger)):
        raise FileExistsError("run refuses to clobber an existing attempt ledger")
    while True:
        context = _verify_claim(claim)
        state = _ledger_state(ledger, context, allow_absent=True)
        if state.complete:
            return state
        if state.exhausted:
            raise RuntimeError("teacher search exhausted three attempts without full coverage")
        assert state.next_stage is not None and state.next_attempt is not None
        pending = list(state.next_children)
        for offset in range(0, len(pending), BUDGETS["workers"]):
            context = _verify_claim(claim)
            chunk = pending[offset : offset + BUDGETS["workers"]]
            sequence = len(state.rows)
            with ThreadPoolExecutor(max_workers=BUDGETS["workers"]) as executor:
                futures = [
                    executor.submit(
                        _perform_attempt,
                        context,
                        child,
                        state.next_stage,
                        state.next_attempt,
                        sequence + index,
                    )
                    for index, child in enumerate(chunk)
                ]
                completed = [future.result() for future in futures]
            for row in completed:
                state_identity = _append_attempt(ledger, row, state.identity)
                state = LedgerState(
                    (*state.rows, row), state_identity, state.next_stage,
                    state.next_attempt, state.next_children, False, False,
                    state.deep_scores,
                )
        # Re-parse the fsynced bytes before deriving the next retry wave/stage.


def _deep_score_rows(context: ClaimContext, state: LedgerState) -> list[dict[str, Any]]:
    if not state.complete or len(state.deep_scores) != len(_children(context.roots)):
        raise ValueError("teacher labels require exact deep scores for every routed child")
    rows: list[dict[str, Any]] = []
    for root in context.roots:
        siblings = [
            (child, int(state.deep_scores[child.child_id])) for child in root.children
        ]
        ordered = sorted(siblings, key=lambda item: (item[1], item[0].child_id))
        # Lower child-STM is better for the parent because root score is its negation.
        ranks = {child.child_id: index + 1 for index, (child, _) in enumerate(ordered)}
        root_scores = {child.child_id: -score for child, score in siblings}
        best = max(root_scores.values())
        for child, child_score in siblings:
            root_score = -child_score
            rows.append(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "kind": TEACHER_LABEL_KIND,
                    "profileId": PROFILE_ID,
                    "rootId": root.root_id,
                    "childId": child.child_id,
                    "childOfen": child.ofen,
                    "phase": root.phase,
                    "parentSideToMove": root.parent_side,
                    "deepRank": ranks[child.child_id],
                    "deepRegretCp": best - root_score,
                    "deepScoreCpRoot": root_score,
                    "deepScoreCpChildStm": child_score,
                }
            )
    return rows


def _parse_teacher_labels(path: Path, context: ClaimContext) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, identity = _load_jsonl(path, "teacher labels")
    expected_children = _children(context.roots)
    if len(rows) != len(expected_children):
        raise ValueError("teacher label row count changed")
    by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, (row, child) in enumerate(zip(rows, expected_children, strict=True)):
        label = f"teacher label row {index}"
        _exact_keys(row, TEACHER_LABEL_FIELDS, label)
        if (
            type(row["schemaVersion"]) is not int
            or row["schemaVersion"] != SCHEMA_VERSION
            or row["kind"] != TEACHER_LABEL_KIND
            or row["profileId"] != PROFILE_ID
            or row["rootId"] != child.root_id
            or row["childId"] != child.child_id
            or row["childOfen"] != child.ofen
            or row["phase"] != child.phase
            or row["parentSideToMove"] != child.parent_side
            or type(row["deepRank"]) is not int
            or not 1 <= row["deepRank"] <= 4
            or type(row["deepRegretCp"]) is not int
            or not 0 <= row["deepRegretCp"] <= 2 * SCORE_LIMIT_CP
            or type(row["deepScoreCpRoot"]) is not int
            or not -SCORE_LIMIT_CP <= row["deepScoreCpRoot"] <= SCORE_LIMIT_CP
            or type(row["deepScoreCpChildStm"]) is not int
            or not -SCORE_LIMIT_CP <= row["deepScoreCpChildStm"] <= SCORE_LIMIT_CP
            or row["deepScoreCpRoot"] != -row["deepScoreCpChildStm"]
        ):
            raise ValueError(f"{label} schema or score sign changed")
        by_root[row["rootId"]].append(row)
    for root_id, siblings in by_root.items():
        if len(siblings) != 4 or sorted(row["deepRank"] for row in siblings) != [1, 2, 3, 4]:
            raise ValueError(f"teacher root {root_id} rank inventory changed")
        best = max(row["deepScoreCpRoot"] for row in siblings)
        expected_order = sorted(
            siblings, key=lambda row: (-row["deepScoreCpRoot"], row["childId"])
        )
        for rank, row in enumerate(expected_order, 1):
            if row["deepRank"] != rank or row["deepRegretCp"] != best - row["deepScoreCpRoot"]:
                raise ValueError(f"teacher root {root_id} rank/regret changed")
    return rows, identity


def _ledger_completion_document(
    *, context: ClaimContext, ledger: Path, state: LedgerState, created_utc: str
) -> dict[str, Any]:
    if not state.complete:
        raise ValueError("attempt-ledger completion requires full deep coverage")
    created = _parse_timestamp(created_utc, "attempt-ledger completion createdUtc")
    latest = max(
        (_parse_timestamp(row["completedUtc"], "attempt completedUtc") for row in state.rows),
        default=_parse_timestamp(context.document["createdUtc"], "claim createdUtc"),
    )
    if created <= latest:
        raise ValueError("attempt-ledger completion must follow every attempt")
    deep_rows = [
        {"childId": child_id, "scoreCpChildStm": state.deep_scores[child_id]}
        for child_id in sorted(state.deep_scores)
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LEDGER_COMPLETION_KIND,
        "profileId": PROFILE_ID,
        "status": "complete-exact-child-coverage",
        "createdUtc": created_utc,
        "claim": dict(context.identity),
        "attemptLedger": _identity(ledger),
        "budgets": dict(BUDGETS),
        "inputOrderSha256": context.document["inputOrderSha256"],
        "routedChildren": len(_children(context.roots)),
        "attemptRecords": len(state.rows),
        "successfulChildren": len(state.deep_scores),
        "rejectedChildren": 0,
        "unresolvedChildren": 0,
        "deepScoresSha256": _digest(deep_rows),
        "resultInformationRead": False,
        "finalStageSeal": True,
    }


def _verify_ledger_completion(
    path: Path, *, context: ClaimContext, ledger: Path, state: LedgerState
) -> tuple[dict[str, Any], dict[str, Any]]:
    document, identity = _load_json(path, "attempt-ledger completion")
    _exact_keys(document, LEDGER_COMPLETION_FIELDS, "attempt-ledger completion")
    expected = _ledger_completion_document(
        context=context,
        ledger=ledger,
        state=state,
        created_utc=document["createdUtc"],
    )
    if document != expected:
        raise ValueError("attempt-ledger completion differs from ledger replay")
    return document, identity


def publish_teacher_outputs(
    *,
    claim: Path,
    ledger: Path,
    ledger_completion: Path,
    labels: Path,
    teacher_manifest: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    context = _verify_claim(claim)
    state = _ledger_state(ledger, context, allow_absent=False)
    if not state.complete:
        raise ValueError("cannot finalize an incomplete teacher ledger")
    receipt_created = _utc_after(
        str(context.document["createdUtc"]),
        *(str(row["completedUtc"]) for row in state.rows),
    )
    receipt_document = _ledger_completion_document(
        context=context,
        ledger=ledger,
        state=state,
        created_utc=receipt_created,
    )
    if os.path.lexists(_absolute(ledger_completion)):
        receipt_document, _ = _verify_ledger_completion(
            ledger_completion, context=context, ledger=ledger, state=state
        )
    else:
        _exclusive_json(ledger_completion, receipt_document)
    expected_labels = _deep_score_rows(context, state)
    if os.path.lexists(_absolute(labels)):
        actual_labels, _ = _parse_teacher_labels(labels, context)
        if actual_labels != expected_labels:
            raise ValueError("existing teacher labels differ from ledger scores")
    else:
        _exclusive_jsonl(labels, expected_labels)
    actual_labels, labels_identity = _parse_teacher_labels(labels, context)
    manifest_created = _utc_after(
        str(receipt_document["createdUtc"]), str(context.document["createdUtc"])
    )
    manifest_document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TEACHER_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "complete-after-frozen-prelabel-authority",
        "createdUtc": manifest_created,
        "prelabelSeal": dict(context.document["prelabelSeal"]),
        "componentMap": dict(context.document["componentMap"]),
        "labels": labels_identity,
        "producer": dict(context.document["runner"]),
        "rows": len(actual_labels),
        "childrenPerRoot": 4,
    }
    if os.path.lexists(_absolute(teacher_manifest)):
        existing, _ = _load_json(teacher_manifest, "teacher manifest")
        _exact_keys(existing, TEACHER_MANIFEST_FIELDS, "teacher manifest")
        # Preserve the original publication time during a resume/finalize pass.
        manifest_document["createdUtc"] = existing["createdUtc"]
        if existing != manifest_document:
            raise ValueError("existing teacher manifest differs from ledger authority")
    else:
        _exclusive_json(teacher_manifest, manifest_document)
    return receipt_document, actual_labels, manifest_document


def _projected_rows(
    teacher_rows: Sequence[Mapping[str, Any]],
    components: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for teacher in teacher_rows:
        component = components[teacher["rootId"]]
        rows.append(
            {
                "schemaVersion": SCHEMA_VERSION,
                "kind": PROJECTED_LABEL_KIND,
                "rootId": teacher["rootId"],
                "leakageComponentId": component["leakageComponentId"],
                "split": component["split"],
                "childId": teacher["childId"],
                "childOfen": teacher["childOfen"],
                "phase": teacher["phase"],
                "parentSideToMove": teacher["parentSideToMove"],
                "deepRank": teacher["deepRank"],
                "deepRegretCp": teacher["deepRegretCp"],
                "deepScoreCpRoot": teacher["deepScoreCpRoot"],
                "deepScoreCpChildStm": teacher["deepScoreCpChildStm"],
            }
        )
    return rows


def _root_record(root: Root, component: Mapping[str, str]) -> dict[str, Any]:
    return {
        "rootId": root.root_id,
        "leakageComponentId": component["leakageComponentId"],
        "split": component["split"],
        "phase": root.phase,
        "parentSideToMove": root.parent_side,
        "childIds": [child.child_id for child in root.children],
    }


def _inventory(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda row: str(row["rootId"]))
    return {"roots": len(ordered), "children": len(ordered) * 4, "sha256": _digest(ordered)}


def _cell_inventory(root_ids: Iterable[str]) -> dict[str, Any]:
    ordered = sorted(root_ids)
    return {"roots": len(ordered), "sha256": _digest(ordered)}


def projected_manifest_document(
    *,
    context: ClaimContext,
    corpus: Path,
    teacher_labels: Path,
    teacher_manifest: Path,
    created_utc: str,
) -> dict[str, Any]:
    teacher_rows, teacher_labels_identity = _parse_teacher_labels(teacher_labels, context)
    expected_rows = _projected_rows(teacher_rows, context.components)
    actual_rows, corpus_identity = _load_jsonl(corpus, "projected corpus")
    for index, row in enumerate(actual_rows):
        _exact_keys(row, PROJECTED_LABEL_FIELDS, f"projected corpus row {index}")
    if actual_rows != expected_rows:
        raise ValueError("projected corpus is not the exact teacher/component projection")
    manifest_identity = _identity(teacher_manifest)
    teacher_document, _ = _load_json(teacher_manifest, "teacher manifest")
    created = _parse_timestamp(created_utc, "projection manifest createdUtc")
    if created <= _parse_timestamp(teacher_document["createdUtc"], "teacher manifest createdUtc"):
        raise ValueError("projection manifest must follow teacher manifest")
    root_records = [
        _root_record(root, context.components[root.root_id]) for root in context.roots
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PROJECTED_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "sealed-exact-projected-label-authority",
        "createdUtc": created_utc,
        "corpus": corpus_identity,
        "componentMap": dict(context.document["componentMap"]),
        "rows": len(actual_rows),
        "childrenPerRoot": 4,
        "rootInventories": {
            split: _inventory(
                [
                    record for record in root_records
                    if record["split"] == split
                ]
            )
            for split in SPLITS
        },
        "phaseSideInventories": {
            split: {
                cell: _cell_inventory(
                    record["rootId"]
                    for record in root_records
                    if record["split"] == split
                    and f"{record['phase']}:{record['parentSideToMove']}" == cell
                )
                for cell in CELLS
            }
            for split in SPLITS
        },
        "upstreamPrelabelSeal": dict(context.document["prelabelSeal"]),
        "upstreamTeacherLabels": teacher_labels_identity,
        "upstreamTeacherManifest": manifest_identity,
        "projectionProducer": dict(context.document["plannedProjectionProducer"]),
    }


def _verify_projection(
    *, context: ClaimContext, teacher_labels: Path, teacher_manifest: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    corpus = Path(str(context.document["plannedProjectedCorpusPath"]))
    manifest = Path(str(context.document["plannedProjectionManifestPath"]))
    if not corpus.is_file() or not manifest.is_file():
        raise FileNotFoundError(
            "planned projection outputs are not yet present; run the frozen projection producer"
        )
    document, manifest_identity = _load_json(manifest, "projection manifest")
    _exact_keys(document, PROJECTED_MANIFEST_FIELDS, "projection manifest")
    expected = projected_manifest_document(
        context=context,
        corpus=corpus,
        teacher_labels=teacher_labels,
        teacher_manifest=teacher_manifest,
        created_utc=document["createdUtc"],
    )
    if document != expected:
        raise ValueError("projection manifest differs from exact recomputation")
    return _identity(corpus), manifest_identity, document


def finalize_teacher(
    *,
    claim: Path,
    ledger: Path,
    ledger_completion: Path,
    labels: Path,
    teacher_manifest: Path,
    completion: Path,
) -> dict[str, Any]:
    receipt, teacher_rows, teacher_document = publish_teacher_outputs(
        claim=claim,
        ledger=ledger,
        ledger_completion=ledger_completion,
        labels=labels,
        teacher_manifest=teacher_manifest,
    )
    context = _verify_claim(claim)
    state = _ledger_state(ledger, context, allow_absent=False)
    _, receipt_identity = _verify_ledger_completion(
        ledger_completion, context=context, ledger=ledger, state=state
    )
    corpus_identity, projection_manifest_identity, projection_document = _verify_projection(
        context=context,
        teacher_labels=labels,
        teacher_manifest=teacher_manifest,
    )
    completion_created = _utc_after(
        str(receipt["createdUtc"]),
        str(teacher_document["createdUtc"]),
        str(projection_document["createdUtc"]),
    )
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TEACHER_COMPLETION_KIND,
        "profileId": PROFILE_ID,
        "status": "completed-exact-claimed-teacher-and-projection",
        "createdUtc": completion_created,
        "claim": dict(context.identity),
        "prelabelSeal": dict(context.document["prelabelSeal"]),
        "engine": dict(context.document["engine"]),
        "runner": dict(context.document["runner"]),
        "options": dict(context.document["options"]),
        "budgets": dict(BUDGETS),
        "inputOrderSha256": context.document["inputOrderSha256"],
        "attemptLedger": _identity(ledger),
        "attemptLedgerCompletion": receipt_identity,
        "teacherLabels": _identity(labels),
        "teacherManifest": _identity(teacher_manifest),
        "projectionProducer": dict(context.document["plannedProjectionProducer"]),
        "projectedCorpus": corpus_identity,
        "labelManifest": projection_manifest_identity,
        "finalStageSeal": True,
        "resultInformationRead": False,
    }
    if os.path.lexists(_absolute(completion)):
        existing, _ = _load_json(completion, "teacher completion")
        _exact_keys(existing, COMPLETION_FIELDS, "teacher completion")
        document["createdUtc"] = existing["createdUtc"]
        if existing != document:
            raise ValueError("existing teacher completion differs from recomputation")
        return existing
    return _exclusive_json(completion, document)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    options = sub.add_parser("options", help="publish the fixed teacher options")
    options.add_argument("--output", type=Path, required=True)

    claim = sub.add_parser("claim", help="publish the pre-target teacher claim")
    for name in ("routing", "components", "prelabel", "hce-completion", "engine", "options"):
        claim.add_argument(f"--{name}", type=Path, required=True)
    claim.add_argument("--projection-producer", type=Path, required=True)
    claim.add_argument("--projected-corpus", type=Path, required=True)
    claim.add_argument("--projection-manifest", type=Path, required=True)
    claim.add_argument("--output", type=Path, required=True)
    claim.add_argument("--created-utc")

    for command in ("run", "resume"):
        operation = sub.add_parser(command, help=f"{command} the fixed teacher searches")
        operation.add_argument("--claim", type=Path, required=True)
        operation.add_argument("--ledger", type=Path, required=True)

    finalize = sub.add_parser("finalize", help="seal labels and completed projection")
    finalize.add_argument("--claim", type=Path, required=True)
    finalize.add_argument("--ledger", type=Path, required=True)
    finalize.add_argument("--ledger-completion", type=Path, required=True)
    finalize.add_argument("--labels", type=Path, required=True)
    finalize.add_argument("--teacher-manifest", type=Path, required=True)
    finalize.add_argument("--completion", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "options":
        publish_teacher_options(args.output)
    elif args.command == "claim":
        publish_teacher_claim(
            args.output,
            routing=args.routing,
            components=args.components,
            prelabel=args.prelabel,
            hce_completion=args.hce_completion,
            engine=args.engine,
            options=args.options,
            projection_producer=args.projection_producer,
            projected_corpus=args.projected_corpus,
            projection_manifest=args.projection_manifest,
            created_utc=args.created_utc,
        )
    elif args.command in {"run", "resume"}:
        run_teacher(args.claim, args.ledger, resume=args.command == "resume")
    elif args.command == "finalize":
        finalize_teacher(
            claim=args.claim,
            ledger=args.ledger,
            ledger_completion=args.ledger_completion,
            labels=args.labels,
            teacher_manifest=args.teacher_manifest,
            completion=args.completion,
        )
    else:  # pragma: no cover - argparse owns this branch.
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


__all__ = [
    "BUDGETS",
    "FIXED_OPTIONS",
    "finalize_teacher",
    "main",
    "projected_manifest_document",
    "publish_teacher_claim",
    "publish_teacher_options",
    "publish_teacher_outputs",
    "run_teacher",
    "teacher_options_document",
]
