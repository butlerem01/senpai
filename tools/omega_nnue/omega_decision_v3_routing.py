#!/usr/bin/env python3
"""Frozen, target-free routing and leakage-component authority for G6.

This producer runs after the rules-only terminal classifier and before either
the handcrafted evaluator or the search teacher.  It reads only structural
Omega positions and target-opaque prior-position catalogs.  The complete
eligible root/child graph is componentized before any filtering, every
component receives one deterministic split, and exact phase-by-side quotas are
then selected without consulting a score, target, result, or game outcome.
"""

from __future__ import annotations

import argparse
import builtins
from collections import Counter, defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import types
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v6-move-decision-v1"
ROUTING_KIND = "omega-decision-v3-target-free-routing"
COMPONENT_KIND = "omega-nnue-king-state-v6-component-authority"
ROOT_MANIFEST_KIND = "omega-decision-v3-source-root-manifest"
CHILD_MANIFEST_KIND = "omega-decision-v3-source-children-manifest"
COMPLETION_KIND = "omega-decision-v3-target-free-routing-completion"
ELIGIBLE_ROOT_KIND = "omega-g6-terminal-safe-root"
ELIGIBLE_CHILD_KIND = "omega-g6-terminal-safe-child"
FORBIDDEN_MANIFEST_KIND = (
    "omega-target-opaque-forbidden-position-catalog-manifest"
)
FORBIDDEN_ROW_KIND = "omega-target-opaque-forbidden-position"

PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
SPLITS = ("train", "validation", "heldOut")
CELLS = tuple(f"{phase}:{side}" for phase in PHASES for side in SIDES)
QUOTAS: Mapping[str, int] = {"train": 512, "validation": 128, "heldOut": 128}
CHILDREN_PER_ROOT = 4
# The reviewed 8,192-pair sampler profile can emit at most 262,144 roots
# (two trajectory flavours, eight phase/side cells, two positions per cell).
# Capping that complete inventory makes the only resident source state bounded;
# every eligible child and leakage edge is disk-backed.
MAX_ELIGIBLE_ROOTS = 262_144
MAX_JSONL_ROW_BYTES = 4 * 1024 * 1024
MAX_JSON_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_FORBIDDEN_BATCH_ROWS = 4_096
MAX_FORBIDDEN_BATCH_KEY_ENTRIES = 32_768
MAX_LEGAL_CHILDREN_PER_ROOT = 2_048
MAX_SOURCE_IDENTIFIER_CHARS = 1_024
MAX_OFEN_CHARS = 512
MAX_HISTORY_PLIES = 220
MAX_REFERENCE_CHILDREN = 100_000
MAX_ROUTING_ROOTS = sum(QUOTAS.values()) * len(CELLS)
SPLIT_BUCKETS: Mapping[str, tuple[int, ...]] = {
    "train": (0, 1, 2, 3),
    "validation": (4,),
    "heldOut": (5,),
}

COMPONENT_DOMAIN = "omega-decision-v3-complete-leakage-component-v1"
SPLIT_DOMAIN = "omega-decision-v3-component-split-v1"
ROOT_RANK_DOMAIN = "omega-decision-v3-root-quota-rank-v1"
CHILD_ROTATION_DOMAIN = "omega-decision-v3-four-child-rotation-v1"
CHILD_ID_DOMAIN = "omega-g6-terminal-safe-child-v1"

# These are execution pins, not merely manifest annotations.  Dependency
# modules are compiled and executed directly from descriptor-stable snapshots
# after their exact size and SHA-256 match these reviewed values.
EXPECTED_OMEGA_NNUE_BYTES = 53_900
EXPECTED_OMEGA_NNUE_SHA256 = (
    "efc55715895f32e948db35428372393c711f701f2e84c69256bdf689065422aa"
)
EXPECTED_SELECT_SCREEN_BYTES = 39_442
EXPECTED_SELECT_SCREEN_SHA256 = (
    "304172e583b4c963718191017b4f8d2426aea325dd42337c197496ee738677ee"
)
EXPECTED_TERMINAL_LINEAGE_MODULE_BYTES = 141_573
EXPECTED_TERMINAL_LINEAGE_MODULE_SHA256 = (
    "c227d5011c55bd6f7f52e61b02e67a358ff4594ff3ec4244b54002a5b1277e38"
)

COMPONENT_ALGORITHM = {
    "id": "omega-decision-v3-complete-root-child-external-edge-dsu-v2",
    "graph": "every eligible root and every eligible child before filtering",
    "storage": (
        "descriptor-streamed rows; at most 262144 compact roots resident; all "
        "children and exact/signature owner edges in bounded-cache SQLite; "
        "primary-key ordered external edge scan feeds root-only DSU"
    ),
    "edges": [
        "all roots with the same source group",
        "all roots owning positions with the same exact NNUE-v1 input",
        "all roots owning positions sharing a conservative NNUE-v1 orbit signature",
    ],
    "componentId": (
        "'component-' plus lowercase SHA-256 of UTF-8 domain, NUL, and canonical "
        "JSON of the ascending sourceRootId array"
    ),
}
SPLIT_ALGORITHM = {
    "id": "omega-decision-v3-component-hash-six-buckets-v1",
    "digest": (
        "SHA-256 of UTF-8 domain, NUL, and canonical JSON [seed,leakageComponentId]"
    ),
    "bucket": "unsigned big-endian digest modulo 6",
    "assignment": {"train": [0, 1, 2, 3], "validation": [4], "heldOut": [5]},
    "wholeComponents": True,
    "assignedBeforeQuotaSelection": True,
}
CHILD_SELECTION_ALGORITHM = {
    "id": "omega-decision-v3-rotated-even-four-v1",
    "sourceOrder": "ascending contiguous moveOrdinal",
    "rotation": (
        "unsigned big-endian SHA-256 of UTF-8 domain, NUL, and canonical JSON "
        "[seed,sourceRootId,sourceGroupId,normalized-parent-OFEN], modulo "
        "legalChildCount"
    ),
    "spreadIndices": "floor(k * legalChildCount / 4), k=0,1,2,3",
    "publicationOrder": "ascending moveOrdinal",
}
ROOT_SELECTION_ALGORITHM = {
    "id": "omega-decision-v3-target-free-cell-quota-v1",
    "rank": (
        "ascending SHA-256 of UTF-8 domain, NUL, and canonical JSON "
        "[seed,split,phase,side,leakageComponentId,sourceRootId], then sourceRootId"
    ),
    "crossCellBorrowing": False,
    "rootIdPolicy": "rootId equals sourceRootId",
}
PHASE_POLICY = {
    "opening": ">=37 pieces",
    "middlegame": ">=25 pieces",
    "late": ">=13 pieces",
    "endgame": ">=7 pieces",
    "fewerThanSeven": "abort",
}

IDENTITY_FIELDS = frozenset({"path", "bytes", "sha256"})
ROOT_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "rootId", "groupId", "initialOfen", "moves",
        "plyOfenSha256", "rootOfen", "rootOfenSha256",
        "preclassificationTranscriptSha256", "legalChildCount",
    }
)
CHILD_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "rootId", "groupId", "initialOfen", "moves",
        "plyOfenSha256", "parentOfen", "parentOfenSha256", "childId", "move",
        "moveOrdinal", "childOfen", "childOfenSha256", "classification",
        "preclassificationTranscriptSha256",
    }
)
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
FORBIDDEN_ROW_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "positionId", "ofen", "exactPositionKey",
        "conservativeOrbitKey", "conservativeOrbitSignatures", "sourceGameId",
        "sourceRunId", "sourceArtifactSha256",
    }
)
FORBIDDEN_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "targetOpaque", "createdUtc", "catalog",
        "catalogSchema", "sourceInventory", "sourceInventorySha256",
        "sourceProjectionManifests", "sourceProjectionManifestsSha256",
        "priorSourceAudits", "priorSourceAuditsSha256", "extractionPolicy",
        "positionCount", "targetOrScoreFieldsDecoded", "targetOrScoreFieldsEmitted",
        "producer",
    }
)
ROOT_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "terminalLineage", "eligibleRoots", "eligibleChildren", "producer",
        "dependencyIdentities", "records", "sourceGroups", "phaseSideCounts",
        "sourceOrderSha256", "semanticRowsSha256", "phasePolicy",
        "targetFieldsDecoded", "resultInformationRead", "finalStageSeal",
    }
)
CHILD_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "terminalLineage", "eligibleRoots", "eligibleChildren",
        "sourceRootManifest", "producer", "dependencyIdentities", "records",
        "roots", "sourceGroups", "sourceOrderSha256", "semanticRowsSha256",
        "classification", "completeLegalChildCoverage", "targetFieldsDecoded",
        "resultInformationRead", "finalStageSeal",
    }
)
COMPLETION_FIELDS = frozenset(
    {
        "schemaVersion", "kind", "profileId", "status", "createdUtc", "seed",
        "terminalLineage", "eligibleRoots", "eligibleChildren",
        "priorForbiddenManifests", "sourceRootManifest", "sourceChildrenManifest",
        "targetFreeRouting", "componentMap", "producer", "dependencyIdentities",
        "phasePolicy", "componentAlgorithm", "splitAlgorithm",
        "childSelectionAlgorithm", "rootSelectionAlgorithm", "quotas",
        "coverage", "phaseSideInventories", "digests", "forbiddenAuthority",
        "completeGraphBuiltBeforeFiltering", "componentsAssignedBeforeSelection",
        "targetRowsDecoded", "targetFieldsDecoded", "resultInformationRead",
        "finalStageSeal",
    }
)

_SHA = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_MOVE = re.compile(r"^(?:[a-j][0-9]|w[1-4])(?:[a-j][0-9]|w[1-4])[qrbncw]?$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_TARGET_DECLARATION_KEYS = frozenset(
    {
        "targetOpaque", "targetInformationRead", "targetOrScoreFieldsDecoded",
        "targetOrScoreFieldsEmitted",
    }
)
_TARGET_STEMS = frozenset(
    {
        "score", "target", "label", "evaluation", "eval", "outcome", "result",
        "winner", "mate", "value", "bound", "win", "loss", "probability",
        "bestmove", "regret", "rank", "principalvariation", "centipawn", "pv",
        "cp", "hce", "teacher", "prediction", "residual", "utility", "logit",
    }
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


class _CanonicalArrayHasher:
    """Hash a canonical JSON array incrementally without retaining its rows."""

    def __init__(self, prefix: bytes = b"") -> None:
        self.digest = hashlib.sha256()
        self.digest.update(prefix)
        self.digest.update(b"[")
        self.count = 0
        self.finished = False

    def add(self, value: Any) -> None:
        if self.finished:
            raise RuntimeError("canonical array digest is already finalized")
        if self.count:
            self.digest.update(b",")
        self.digest.update(_canonical_json(value))
        self.count += 1

    def hexdigest(self) -> str:
        if not self.finished:
            self.digest.update(b"]")
            self.finished = True
        return self.digest.hexdigest()


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != set(expected):
        raise ValueError(f"{label} field inventory changed")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _strict_json(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"{label} contains non-finite JSON {token}")
            ),
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON: {error}") from error


class _OpaqueKeyScanner:
    """Inspect object keys while leaving every scalar/string value undecoded."""

    def __init__(
        self,
        text: str,
        label: str,
        allowed_target_metadata_keys: Collection[str] = (),
    ) -> None:
        self.text = text
        self.label = label
        self.length = len(text)
        self.allowed_target_metadata_keys = frozenset(allowed_target_metadata_keys)

    def fail(self, message: str) -> ValueError:
        return ValueError(f"{self.label}: {message}")

    def ws(self, index: int) -> int:
        while index < self.length and self.text[index] in " \t\r\n":
            index += 1
        return index

    def string_end(self, index: int) -> int:
        if index >= self.length or self.text[index] != '"':
            raise self.fail("expected JSON string")
        index += 1
        while index < self.length:
            character = self.text[index]
            if character == '"':
                return index + 1
            if ord(character) < 0x20:
                raise self.fail("control character in JSON string")
            if character == "\\":
                index += 1
                if index >= self.length or self.text[index] not in '"\\/bfnrtu':
                    raise self.fail("invalid JSON string escape")
                if self.text[index] == "u":
                    digits = self.text[index + 1 : index + 5]
                    if len(digits) != 4 or re.fullmatch(r"[0-9a-fA-F]{4}", digits) is None:
                        raise self.fail("invalid JSON Unicode escape")
                    index += 4
            index += 1
        raise self.fail("unterminated JSON string")

    def value(self, index: int) -> int:
        index = self.ws(index)
        if index >= self.length:
            raise self.fail("missing JSON value")
        character = self.text[index]
        if character == "{":
            return self.object(index)
        if character == "[":
            return self.array(index)
        if character == '"':
            return self.string_end(index)
        for literal in ("true", "false", "null"):
            if self.text.startswith(literal, index):
                return index + len(literal)
        number = re.match(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", self.text[index:])
        if number is not None:
            return index + number.end()
        raise self.fail("invalid JSON value")

    def array(self, index: int) -> int:
        index = self.ws(index + 1)
        if index < self.length and self.text[index] == "]":
            return index + 1
        while True:
            index = self.ws(self.value(index))
            if index >= self.length:
                raise self.fail("unterminated JSON array")
            if self.text[index] == "]":
                return index + 1
            if self.text[index] != ",":
                raise self.fail("expected comma in JSON array")
            index = self.ws(index + 1)

    def object(self, index: int) -> int:
        index = self.ws(index + 1)
        if index < self.length and self.text[index] == "}":
            return index + 1
        while True:
            start = index
            end = self.string_end(start)
            try:
                key = json.loads(self.text[start:end])
            except json.JSONDecodeError as error:
                raise self.fail("invalid JSON object key") from error
            if type(key) is not str:
                raise self.fail("non-string JSON object key")
            index = self.ws(end)
            if index >= self.length or self.text[index] != ":":
                raise self.fail("expected colon after JSON key")
            index = self.ws(index + 1)
            if (
                key not in _TARGET_DECLARATION_KEYS
                and key not in self.allowed_target_metadata_keys
                and _target_key(key)
            ):
                # Crucially, return before scanning or decoding the sensitive value.
                raise ValueError(
                    f"{self.label} contains forbidden target/score-like key {key!r}"
                )
            index = self.ws(self.value(index))
            if index >= self.length:
                raise self.fail("unterminated JSON object")
            if self.text[index] == "}":
                return index + 1
            if self.text[index] != ",":
                raise self.fail("expected comma in JSON object")
            index = self.ws(index + 1)

    def scan(self) -> None:
        end = self.ws(self.value(self.ws(0)))
        if end != self.length:
            raise self.fail("trailing data after JSON value")


def _preflight_target_opaque(
    payload: bytes,
    label: str,
    allowed_target_metadata_keys: Collection[str] = (),
) -> None:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    _OpaqueKeyScanner(text, label, allowed_target_metadata_keys).scan()


def _validate_timestamp(value: Any, label: str) -> str:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{label} must be RFC3339 UTC with six fractional digits")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ValueError(f"{label} is not a real UTC timestamp") from error
    return value


def _validate_prior_timestamp(value: Any, label: str) -> str:
    if type(value) is not str or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value
    ) is None:
        raise ValueError(f"{label} is not a canonical UTC timestamp")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} is not a real UTC timestamp") from error
    return value


def _safe_parent(path: Path) -> Path:
    # Keep the leaf lexical: ``Path.resolve`` follows a leaf symlink before
    # lstat can reject it.  Absolute/normpath canonicalization does not dereference.
    result = Path(os.path.abspath(os.path.normpath(os.fspath(path.expanduser()))))
    parent = result.parent
    if not parent.is_dir():
        raise FileNotFoundError(parent)
    current = parent
    while True:
        metadata = os.lstat(current)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"symlink/junction/reparse parent is forbidden: {current}")
        if current == current.parent:
            break
        current = current.parent
    return result


def _descriptor_snapshot(
    path: Path, label: str, *, maximum_bytes: int | None = None
) -> tuple[dict[str, Any], bytes, tuple[int, int]]:
    lexical = _safe_parent(path)
    before = os.lstat(lexical)
    attributes = getattr(before, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(before.st_mode)
        or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise ValueError(f"{label} must be one non-linked regular file: {lexical}")
    if maximum_bytes is not None and before.st_size > maximum_bytes:
        raise ValueError(
            f"{label} exceeds the bounded {maximum_bytes}-byte snapshot limit"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lexical, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"{label} changed before descriptor open")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = os.lstat(lexical)
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        or (after_fd.st_dev, after_fd.st_ino, after_fd.st_size)
        != (before.st_dev, before.st_ino, before.st_size)
        or len(payload) != before.st_size
    ):
        raise ValueError(f"{label} changed while being read")
    identity = {
        "path": str(lexical),
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }
    return identity, payload, (before.st_dev, before.st_ino)


def _descriptor_identity(
    path: Path, label: str
) -> tuple[dict[str, Any], tuple[int, int]]:
    """Hash one stable descriptor without retaining the file's bytes."""

    lexical = _safe_parent(path)
    before = os.lstat(lexical)
    attributes = getattr(before, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(before.st_mode)
        or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise ValueError(f"{label} must be one non-linked regular file: {lexical}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lexical, flags)
    digest = hashlib.sha256()
    total = 0
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"{label} changed before descriptor open")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = os.lstat(lexical)
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        or (after_fd.st_dev, after_fd.st_ino, after_fd.st_size)
        != (before.st_dev, before.st_ino, before.st_size)
        or total != before.st_size
    ):
        raise ValueError(f"{label} changed while being hashed")
    return (
        {"path": str(lexical), "bytes": total, "sha256": digest.hexdigest()},
        (before.st_dev, before.st_ino),
    )


def _descriptor_inode(path: Path, label: str) -> tuple[str, tuple[int, int]]:
    """Bind a role to one stable inode without redundantly rehashing content."""

    lexical = _safe_parent(path)
    before = os.lstat(lexical)
    attributes = getattr(before, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(before.st_mode)
        or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise ValueError(f"{label} must be one non-linked regular file: {lexical}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lexical, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"{label} changed before descriptor open")
    finally:
        os.close(descriptor)
    after = os.lstat(lexical)
    if (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise ValueError(f"{label} changed during inode binding")
    return str(lexical), (before.st_dev, before.st_ino)


def _identity(path: Path, label: str = "file") -> dict[str, Any]:
    return _descriptor_identity(path, label)[0]


def _validate_identity_record(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} is not a content identity")
    _exact_keys(value, IDENTITY_FIELDS, label)
    if (
        type(value["path"]) is not str
        or type(value["bytes"]) is not int
        or value["bytes"] < 0
        or type(value["sha256"]) is not str
        or _SHA.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} is malformed")
    return dict(value)


def _verify_identity_record(value: Any, label: str) -> dict[str, Any]:
    value = _validate_identity_record(value, label)
    actual = _identity(Path(value["path"]), label)
    if actual != value:
        raise ValueError(f"{label} differs from its content")
    return actual


def _assert_distinct_roles(roles: Mapping[str, Path]) -> None:
    lexical: dict[str, str] = {}
    inodes: dict[tuple[int, int], str] = {}
    for role, path in roles.items():
        identity_path, inode = _descriptor_inode(path, role)
        key = os.path.normcase(os.path.normpath(identity_path))
        if key in lexical:
            raise ValueError(f"roles {lexical[key]!r} and {role!r} share one path")
        if inode in inodes:
            raise ValueError(f"roles {inodes[inode]!r} and {role!r} share one inode")
        lexical[key] = role
        inodes[inode] = role


@dataclass(frozen=True, slots=True)
class _PinnedDependency:
    identity: dict[str, Any]
    module: types.ModuleType


_DEPENDENCY_CACHE: dict[str, _PinnedDependency] = {}
_DEPENDENCY_EXECUTION_SERIAL = 0


def _dependency_spec(name: str) -> tuple[str, int | None, str | None]:
    if name == "omegaNnue":
        return (
            "omega_nnue.py",
            EXPECTED_OMEGA_NNUE_BYTES,
            EXPECTED_OMEGA_NNUE_SHA256,
        )
    if name == "selectScreen":
        return (
            "select_screen.py",
            EXPECTED_SELECT_SCREEN_BYTES,
            EXPECTED_SELECT_SCREEN_SHA256,
        )
    if name == "terminalLineageModule":
        return (
            "omega_decision_v3_terminal_lineage.py",
            EXPECTED_TERMINAL_LINEAGE_MODULE_BYTES,
            EXPECTED_TERMINAL_LINEAGE_MODULE_SHA256,
        )
    raise KeyError(name)


def _dependency_path(name: str, filename: str) -> Path:
    del name
    return Path(__file__).resolve().parent / filename


def _execute_pinned_module(
    *,
    logical_name: str,
    path: Path,
    expected_bytes: int | None,
    expected_sha256: str | None,
    pinned_imports: Mapping[str, types.ModuleType] | None = None,
) -> _PinnedDependency:
    """Execute exactly the bytes whose stable identity is returned."""

    if (
        type(expected_bytes) is not int
        or expected_bytes < 1
        or type(expected_sha256) is not str
        or _SHA.fullmatch(expected_sha256) is None
    ):
        raise RuntimeError(
            f"{logical_name} execution pin is not finalized; set its exact "
            "reviewed byte count and SHA-256 before production"
        )
    identity, payload, _ = _descriptor_snapshot(
        path,
        f"{logical_name} dependency",
        maximum_bytes=expected_bytes,
    )
    expected = {
        "path": identity["path"],
        "bytes": expected_bytes,
        "sha256": expected_sha256,
    }
    if identity != expected:
        raise RuntimeError(
            f"{logical_name} dependency differs from its execution pin: "
            f"expected {expected_bytes} B/{expected_sha256}, got "
            f"{identity['bytes']} B/{identity['sha256']}"
        )

    global _DEPENDENCY_EXECUTION_SERIAL
    _DEPENDENCY_EXECUTION_SERIAL += 1
    module = types.ModuleType(
        f"_omega_decision_v3_routing_pinned_{logical_name}_"
        f"{_DEPENDENCY_EXECUTION_SERIAL}"
    )
    module.__file__ = identity["path"]
    module.__package__ = ""
    module.__loader__ = None
    module.__spec__ = None
    original_import = builtins.__import__
    imports = dict(pinned_imports or {})

    def exact_import(
        name: str,
        globals: Mapping[str, Any] | None = None,
        locals: Mapping[str, Any] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if level == 0 and name in imports:
            return imports[name]
        return original_import(name, globals, locals, fromlist, level)

    execution_builtins = dict(vars(builtins))
    execution_builtins["__import__"] = exact_import
    module.__dict__["__builtins__"] = execution_builtins
    sys.modules[module.__name__] = module
    try:
        code = compile(payload, identity["path"], "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except BaseException:
        if sys.modules.get(module.__name__) is module:
            del sys.modules[module.__name__]
        raise
    return _PinnedDependency(identity, module)


def _pinned_dependency(name: str) -> _PinnedDependency:
    filename, expected_bytes, expected_sha256 = _dependency_spec(name)
    path = _dependency_path(name, filename)
    cached = _DEPENDENCY_CACHE.get(name)
    if cached is not None:
        if (
            cached.identity["bytes"] != expected_bytes
            or cached.identity["sha256"] != expected_sha256
        ):
            raise RuntimeError(f"{name} execution pin changed after dependency load")
        if cached.identity["path"] == str(
            Path(os.path.abspath(os.path.normpath(os.fspath(path.expanduser()))))
        ):
            return cached
    imports: dict[str, types.ModuleType] = {}
    if name == "selectScreen":
        imports["omega_nnue"] = _pinned_dependency("omegaNnue").module
    dependency = _execute_pinned_module(
        logical_name=name,
        path=path,
        expected_bytes=expected_bytes,
        expected_sha256=expected_sha256,
        pinned_imports=imports,
    )
    _DEPENDENCY_CACHE[name] = dependency
    return dependency


def parse_ofen(value: str) -> Any:
    return _pinned_dependency("omegaNnue").module.parse_ofen(value)


def observable_ofen(value: str) -> str:
    return _pinned_dependency("selectScreen").module.observable_ofen(value)


def input_keys(value: str) -> Any:
    module = _pinned_dependency("selectScreen").module
    try:
        return module.input_keys(value)
    finally:
        # ``select_screen`` was designed for smaller in-memory selectors and
        # deliberately uses unbounded lru_cache instances.  The routing
        # authority can see tens of millions of one-shot child observables, so
        # retaining those cache keys would defeat descriptor streaming.
        for name in (
            "transformed_observable",
            "canonical_observable",
            "nnue_observable_key",
            "observable_symmetries",
            "identity_key",
            "input_keys",
        ):
            function = getattr(module, name, None)
            clear = getattr(function, "cache_clear", None)
            if clear is not None:
                clear()


def transformed_observable(value: str, horizontal: bool, vertical: bool) -> str:
    function = _pinned_dependency("selectScreen").module.transformed_observable
    try:
        return function(value, horizontal, vertical)
    finally:
        function.cache_clear()


def _verify_terminal_lineage(path: Path) -> dict[str, Any]:
    return _pinned_dependency(
        "terminalLineageModule"
    ).module.verify_terminal_lineage(path)


def _load_json(
    path: Path,
    label: str,
    *,
    target_opaque: bool = False,
    allowed_target_metadata_keys: Collection[str] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity, payload, _ = _descriptor_snapshot(
        path, label, maximum_bytes=MAX_JSON_DOCUMENT_BYTES
    )
    if target_opaque:
        _preflight_target_opaque(payload, label, allowed_target_metadata_keys)
    value = _strict_json(payload, label)
    if type(value) is not dict:
        raise ValueError(f"{label} is not a JSON object")
    return value, identity


class _StableJsonlReader:
    """Single-pass JSONL reader bound to one verified regular-file descriptor."""

    def __init__(
        self,
        path: Path,
        label: str,
        *,
        target_opaque: bool = False,
        expected_identity: Mapping[str, Any] | None = None,
    ) -> None:
        self.path = _safe_parent(path)
        self.label = label
        self.target_opaque = target_opaque
        self.expected_identity = (
            _validate_identity_record(expected_identity, f"{label} expected identity")
            if expected_identity is not None
            else None
        )
        self._stream: Any = None
        self._before: Any = None
        self._digest = hashlib.sha256()
        self._total = 0
        self._rows = 0
        self.identity: dict[str, Any] | None = None

    def __enter__(self) -> _StableJsonlReader:
        before = os.lstat(self.path)
        attributes = getattr(before, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(before.st_mode)
            or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
        ):
            raise ValueError(
                f"{self.label} must be one non-linked regular file: {self.path}"
            )
        if self.expected_identity is not None:
            if (
                self.expected_identity["path"] != str(self.path)
                or self.expected_identity["bytes"] != before.st_size
            ):
                raise ValueError(f"{self.label} differs from its declared path/size")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError(f"{self.label} changed before descriptor open")
            self._stream = os.fdopen(descriptor, "rb", buffering=1024 * 1024)
        except BaseException:
            os.close(descriptor)
            raise
        self._before = before
        return self

    def __iter__(self) -> Iterable[tuple[int, dict[str, Any]]]:
        if self._stream is None or self.identity is not None or self._rows:
            raise RuntimeError(f"{self.label} stream is not available for one full pass")
        number = 0
        while True:
            raw = self._stream.readline(MAX_JSONL_ROW_BYTES + 1)
            if not raw:
                break
            number += 1
            if len(raw) > MAX_JSONL_ROW_BYTES:
                raise ValueError(
                    f"{self.label}:{number}: row exceeds the bounded "
                    f"{MAX_JSONL_ROW_BYTES}-byte limit"
                )
            self._digest.update(raw)
            self._total += len(raw)
            if not raw.strip():
                raise ValueError(f"{self.label}:{number}: blank rows are forbidden")
            if self.target_opaque:
                _preflight_target_opaque(raw, f"{self.label}:{number}")
            row = _strict_json(raw, f"{self.label}:{number}")
            if type(row) is not dict:
                raise ValueError(f"{self.label}:{number}: row is not an object")
            self._rows = number
            yield number, row
        if not self._rows:
            raise ValueError(f"{self.label} is empty")
        after_fd = os.fstat(self._stream.fileno())
        after = os.lstat(self.path)
        before = self._before
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            or (after_fd.st_dev, after_fd.st_ino, after_fd.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
            or self._total != before.st_size
        ):
            raise ValueError(f"{self.label} changed while being streamed")
        actual = {
            "path": str(self.path),
            "bytes": self._total,
            "sha256": self._digest.hexdigest(),
        }
        if self.expected_identity is not None and actual != self.expected_identity:
            raise ValueError(f"{self.label} differs from its declared content identity")
        self.identity = actual

    @property
    def rows(self) -> int:
        return self._rows

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


def _load_bounded_jsonl_rows(
    path: Path, label: str, *, maximum_rows: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with _StableJsonlReader(path, label) as reader:
        for _, row in reader:
            rows.append(row)
            if len(rows) > maximum_rows:
                raise ValueError(
                    f"{label} exceeds its bounded {maximum_rows}-row authority"
                )
        identity = reader.identity
    if identity is None:
        raise AssertionError(f"{label} stream identity was not finalized")
    return rows, identity


def _normalized_ofen(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or len(value) > MAX_OFEN_CHARS
    ):
        raise ValueError(f"{label} is not a nonempty ASCII OFEN")
    normalized = " ".join(value.split())
    if normalized != value or len(value.split()) != 6:
        raise ValueError(f"{label} is not a normalized six-field OFEN")
    parse_ofen(value)
    return value


def _sha(value: Any, label: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise ValueError(f"{label} is not lowercase SHA-256")
    return value


def _strings(value: Any, label: str) -> list[str]:
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        raise ValueError(f"{label} is not a nonempty-string array")
    return list(value)


def _phase_side(ofen: str, label: str) -> tuple[str, str, int]:
    pieces, side, _ = parse_ofen(ofen)
    count = len(pieces)
    if count >= 37:
        phase = "opening"
    elif count >= 25:
        phase = "middlegame"
    elif count >= 13:
        phase = "late"
    elif count >= 7:
        phase = "endgame"
    else:
        raise ValueError(f"{label} has fewer than seven production pieces")
    return phase, side, count


def _leakage_keys(ofen: str) -> tuple[str, str, tuple[str, ...]]:
    observable = observable_ofen(ofen)
    exact, orbit, signatures = input_keys(observable)
    if observable.split()[2] != "-":
        return exact, exact, (exact,)
    return exact, orbit, tuple(sorted(set(signatures)))


@dataclass(frozen=True, slots=True)
class Root:
    root_id: str
    group_id: str
    initial_ofen: str
    moves: tuple[str, ...]
    ply_hashes: tuple[str, ...]
    ofen: str
    ofen_sha256: str
    transcript_sha256: str
    legal_child_count: int
    phase: str
    side: str
    piece_count: int
    exact: str
    orbit: str
    signatures: tuple[str, ...]
    history_sha256: str = ""


@dataclass(frozen=True, slots=True)
class Child:
    root_id: str
    group_id: str
    child_id: str
    move: str
    ordinal: int
    parent_ofen: str
    ofen: str
    ofen_sha256: str
    exact: str
    orbit: str
    signatures: tuple[str, ...]


class _ForbiddenKeyStore:
    """Bounded-memory exact/signature authority backed by temporary SQLite."""

    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="omega-decision-v3-forbidden-"
        )
        self.path = Path(self.temporary.name) / "keys.sqlite3"
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA cache_size=-32768")
        self.connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        self.connection.execute(
            "CREATE TABLE exact_keys (key TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        self.connection.execute(
            "CREATE TABLE signature_keys (key TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        self.connection.execute(
            "CREATE TABLE position_ids (key TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        self.connection.execute(
            "CREATE TEMP TABLE lookup_keys (kind INTEGER, key TEXT, "
            "PRIMARY KEY(kind, key)) WITHOUT ROWID"
        )
        self.connection.commit()
        self.closed = False

    def begin_catalog(self) -> None:
        self.connection.execute("DELETE FROM position_ids")
        self.connection.commit()

    def add_batch(
        self,
        position_ids: Sequence[str],
        exact_keys: Sequence[str],
        signature_keys: Sequence[str],
        *,
        label: str,
    ) -> None:
        if len(position_ids) != len(set(position_ids)):
            raise ValueError(f"{label} contains a duplicate positionId in one batch")
        try:
            self.connection.executemany(
                "INSERT INTO position_ids(key) VALUES (?)",
                ((value,) for value in position_ids),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"{label} contains a duplicate positionId") from error
        self.connection.executemany(
            "INSERT OR IGNORE INTO exact_keys(key) VALUES (?)",
            ((value,) for value in exact_keys),
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO signature_keys(key) VALUES (?)",
            ((value,) for value in signature_keys),
        )
        self.connection.commit()

    def key_count(self, table: str) -> int:
        if table not in ("exact_keys", "signature_keys"):
            raise ValueError(table)
        return int(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

    def contains(self, table: str, value: object) -> bool:
        if type(value) is not str:
            return False
        if table not in ("exact_keys", "signature_keys"):
            raise ValueError(table)
        return (
            self.connection.execute(
                f"SELECT 1 FROM {table} WHERE key=?", (value,)
            ).fetchone()
            is not None
        )

    def matching_keys(
        self, exact_keys: Iterable[str], signature_keys: Iterable[str]
    ) -> tuple[set[str], set[str]]:
        """Return forbidden members for one caller-bounded lookup batch."""

        rows = [(0, value) for value in set(exact_keys)]
        rows.extend((1, value) for value in set(signature_keys))
        self.connection.execute("DELETE FROM lookup_keys")
        self.connection.executemany(
            "INSERT INTO lookup_keys(kind,key) VALUES (?,?)", rows
        )
        exact = {
            row[0]
            for row in self.connection.execute(
                "SELECT q.key FROM lookup_keys q JOIN exact_keys e ON e.key=q.key "
                "WHERE q.kind=0"
            )
        }
        signatures = {
            row[0]
            for row in self.connection.execute(
                "SELECT q.key FROM lookup_keys q JOIN signature_keys s ON s.key=q.key "
                "WHERE q.kind=1"
            )
        }
        return exact, signatures

    @property
    def bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.connection.close()
        self.temporary.cleanup()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class _SqliteKeySet:
    def __init__(self, store: _ForbiddenKeyStore, table: str, count: int) -> None:
        self.store = store
        self.table = table
        self.count = count

    def __contains__(self, value: object) -> bool:
        return self.store.contains(self.table, value)

    def __len__(self) -> int:
        return self.count


@dataclass(frozen=True, slots=True)
class Forbidden:
    exact: Collection[str]
    signatures: Collection[str]
    manifests: tuple[dict[str, Any], ...]
    catalogs: tuple[dict[str, Any], ...]
    positions: int
    source_artifact_sha256: frozenset[str]
    store: _ForbiddenKeyStore | None = None

    @property
    def disk_bytes(self) -> int:
        return 0 if self.store is None else self.store.bytes

    def close(self) -> None:
        if self.store is not None:
            self.store.close()


@dataclass(frozen=True, slots=True)
class Computed:
    routing_rows: tuple[dict[str, Any], ...]
    component_rows: tuple[dict[str, Any], ...]
    all_roots: tuple[Root, ...]
    all_children: tuple[Child, ...]
    root_components: Mapping[str, str]
    component_splits: Mapping[str, str]
    component_forbidden: Mapping[str, bool]
    coverage: Mapping[str, Any]
    inventories: Mapping[str, Any]
    digests: Mapping[str, str]
    forbidden_authority: Mapping[str, Any]


class DSU:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        while parent != self.parent[parent]:
            parent = self.parent[parent]
        while value != parent:
            next_value = self.parent[value]
            self.parent[value] = parent
            value = next_value
        return parent

    def union(self, left: str, right: str) -> None:
        a = self.find(left)
        b = self.find(right)
        if a == b:
            return
        low, high = sorted((a, b))
        self.parent[high] = low


def _root_from_row(row: Mapping[str, Any], label: str) -> Root:
    _exact_keys(row, ROOT_FIELDS, label)
    if (
        type(row["schemaVersion"]) is not int
        or row["schemaVersion"] != SCHEMA_VERSION
        or row["kind"] != ELIGIBLE_ROOT_KIND
    ):
        raise ValueError(f"{label} header changed")
    root_id = row["rootId"]
    group_id = row["groupId"]
    if (
        type(root_id) is not str
        or not root_id
        or not root_id.isascii()
        or len(root_id) > MAX_SOURCE_IDENTIFIER_CHARS
        or type(group_id) is not str
        or not group_id
        or not group_id.isascii()
        or len(group_id) > MAX_SOURCE_IDENTIFIER_CHARS
    ):
        raise ValueError(f"{label} has invalid root identity")
    initial = _normalized_ofen(row["initialOfen"], f"{label}.initialOfen")
    moves = _strings(row["moves"], f"{label}.moves")
    hashes = _strings(row["plyOfenSha256"], f"{label}.plyOfenSha256")
    if len(moves) != len(hashes) or len(moves) > MAX_HISTORY_PLIES:
        raise ValueError(f"{label} history arrays differ")
    for index, move in enumerate(moves):
        if _MOVE.fullmatch(move) is None:
            raise ValueError(f"{label}.moves[{index}] is not a coordinate move")
    for index, item in enumerate(hashes):
        _sha(item, f"{label}.plyOfenSha256[{index}]")
    ofen = _normalized_ofen(row["rootOfen"], f"{label}.rootOfen")
    ofen_sha = _sha(row["rootOfenSha256"], f"{label}.rootOfenSha256")
    if ofen_sha != _sha256_bytes(ofen.encode("utf-8")):
        raise ValueError(f"{label} root OFEN hash differs")
    transcript_sha = _sha(
        row["preclassificationTranscriptSha256"],
        f"{label}.preclassificationTranscriptSha256",
    )
    legal_count = row["legalChildCount"]
    if (
        type(legal_count) is not int
        or legal_count < 1
        or legal_count > MAX_LEGAL_CHILDREN_PER_ROOT
    ):
        raise ValueError(
            f"{label}.legalChildCount is outside the bounded range "
            f"1..{MAX_LEGAL_CHILDREN_PER_ROOT}"
        )
    phase, side, piece_count = _phase_side(ofen, f"{label}.rootOfen")
    exact, orbit, signatures = _leakage_keys(ofen)
    history_sha256 = _digest(
        {"initialOfen": initial, "moves": moves, "plyOfenSha256": hashes}
    )
    return Root(
        root_id, group_id, initial, (), (), ofen,
        ofen_sha, transcript_sha, legal_count, phase, side, piece_count,
        exact, orbit, signatures, history_sha256,
    )


def _child_from_row(
    row: Mapping[str, Any], label: str, roots_by_id: Mapping[str, Root]
) -> Child:
    _exact_keys(row, CHILD_FIELDS, label)
    if (
        type(row["schemaVersion"]) is not int
        or row["schemaVersion"] != SCHEMA_VERSION
        or row["kind"] != ELIGIBLE_CHILD_KIND
        or row["classification"] != "child-nonterminal"
    ):
        raise ValueError(f"{label} header/classification changed")
    root_id = row["rootId"]
    root = roots_by_id.get(root_id)
    if root is None:
        raise ValueError(f"{label} has no eligible root")
    group = row["groupId"]
    child_id = row["childId"]
    if (
        type(group) is not str
        or group != root.group_id
        or type(child_id) is not str
        or not child_id
    ):
        raise ValueError(f"{label} child/group identity changed")
    initial = _normalized_ofen(row["initialOfen"], f"{label}.initialOfen")
    moves = _strings(row["moves"], f"{label}.moves")
    hashes = _strings(row["plyOfenSha256"], f"{label}.plyOfenSha256")
    if len(moves) != len(hashes) or len(moves) > MAX_HISTORY_PLIES:
        raise ValueError(f"{label} history arrays differ")
    actual_history_sha256 = _digest(
        {"initialOfen": initial, "moves": moves, "plyOfenSha256": hashes}
    )
    expected_history_sha256 = root.history_sha256 or _digest(
        {
            "initialOfen": root.initial_ofen,
            "moves": list(root.moves),
            "plyOfenSha256": list(root.ply_hashes),
        }
    )
    if actual_history_sha256 != expected_history_sha256:
        raise ValueError(f"{label} history differs from root")
    parent = _normalized_ofen(row["parentOfen"], f"{label}.parentOfen")
    parent_sha = _sha(row["parentOfenSha256"], f"{label}.parentOfenSha256")
    if parent != root.ofen or parent_sha != root.ofen_sha256:
        raise ValueError(f"{label} parent differs from root")
    move = row["move"]
    if type(move) is not str or _MOVE.fullmatch(move) is None:
        raise ValueError(f"{label}.move is invalid")
    ordinal = row["moveOrdinal"]
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError(f"{label}.moveOrdinal is invalid")
    child_ofen = _normalized_ofen(row["childOfen"], f"{label}.childOfen")
    child_sha = _sha(row["childOfenSha256"], f"{label}.childOfenSha256")
    if child_sha != _sha256_bytes(child_ofen.encode("utf-8")):
        raise ValueError(f"{label} child OFEN hash differs")
    if child_ofen.split()[1] == root.side:
        raise ValueError(f"{label} side-to-move did not alternate")
    if row["preclassificationTranscriptSha256"] != root.transcript_sha256:
        raise ValueError(f"{label} transcript seal differs from root")
    expected_id = _sha256_bytes(
        f"{CHILD_ID_DOMAIN}\0{root_id}\0{group}\0{move}\0{child_ofen}".encode(
            "utf-8"
        )
    )
    if child_id != expected_id:
        raise ValueError(f"{label} stable child ID differs")
    exact, orbit, signatures = _leakage_keys(child_ofen)
    return Child(
        root.root_id, root.group_id, child_id, move, ordinal, root.ofen, child_ofen,
        child_sha, exact, orbit, signatures,
    )


def _parse_roots(path: Path) -> tuple[list[Root], dict[str, Any]]:
    result: list[Root] = []
    seen: set[str] = set()
    with _StableJsonlReader(path, "terminal eligible roots") as reader:
        for number, row in reader:
            label = f"terminal eligible roots:{number}"
            root = _root_from_row(row, label)
            if root.root_id in seen:
                raise ValueError(f"{label} has invalid/duplicate root identity")
            seen.add(root.root_id)
            result.append(root)
            if len(result) > MAX_ELIGIBLE_ROOTS:
                raise ValueError(
                    "eligible-root inventory exceeds the reviewed bounded-memory "
                    f"ceiling of {MAX_ELIGIBLE_ROOTS}"
                )
        identity = reader.identity
    if identity is None:
        raise AssertionError("eligible-root stream identity was not finalized")
    return result, identity


def _parse_children(
    path: Path, roots: Sequence[Root]
) -> tuple[list[Child], dict[str, Any]]:
    """Small-fixture reference parser; production uses `_load_bounded_children`."""

    roots_by_id = {root.root_id: root for root in roots}
    result: list[Child] = []
    seen: set[str] = set()
    by_root: defaultdict[str, list[Child]] = defaultdict(list)
    previous_order: tuple[int, int] | None = None
    root_ordinals = {root.root_id: index for index, root in enumerate(roots)}
    with _StableJsonlReader(path, "terminal eligible children") as reader:
        for number, row in reader:
            label = f"terminal eligible children:{number}"
            child = _child_from_row(row, label, roots_by_id)
            if child.child_id in seen:
                raise ValueError(f"{label} child/group identity changed")
            seen.add(child.child_id)
            order = (root_ordinals[child.root_id], child.ordinal)
            if previous_order is not None and order <= previous_order:
                raise ValueError("eligible-child rows are not exact root/ordinal order")
            previous_order = order
            result.append(child)
            if len(result) > MAX_REFERENCE_CHILDREN:
                raise ValueError(
                    "reference child parser exceeds its bounded test-only ceiling"
                )
            by_root[child.root_id].append(child)
        identity = reader.identity
    if identity is None:
        raise AssertionError("eligible-child stream identity was not finalized")
    for root in roots:
        children = by_root[root.root_id]
        if len(children) != root.legal_child_count:
            raise ValueError(f"root {root.root_id!r} lacks complete legal-child coverage")
        if [child.ordinal for child in children] != list(range(root.legal_child_count)):
            raise ValueError(f"root {root.root_id!r} child ordinals are not contiguous")
    return result, identity


@dataclass(frozen=True, slots=True)
class _ChildInventory:
    records: int
    source_order_sha256: str
    semantic_rows_sha256: str


class _BoundedGraphStore:
    """Disk-backed complete child graph; resident state is O(eligible roots)."""

    def __init__(self, roots: Sequence[Root], forbidden: Forbidden) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="omega-decision-v3-source-graph-"
        )
        self.path = Path(self.temporary.name) / "graph.sqlite3"
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA cache_size=-65536")
        self.connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        self.connection.execute(
            "CREATE TABLE children ("
            "root_index INTEGER NOT NULL, ordinal INTEGER NOT NULL, "
            "child_id TEXT NOT NULL UNIQUE, ofen TEXT NOT NULL, "
            "PRIMARY KEY(root_index, ordinal)) WITHOUT ROWID"
        )
        self.connection.execute(
            "CREATE TABLE edges ("
            "kind INTEGER NOT NULL, key TEXT NOT NULL, root_index INTEGER NOT NULL, "
            "PRIMARY KEY(kind, key, root_index)) WITHOUT ROWID"
        )
        self.connection.commit()
        self.roots = roots
        self.root_index_by_id = {
            root.root_id: index for index, root in enumerate(roots)
        }
        self.forbidden = forbidden
        self.exact_overlaps = [0] * len(roots)
        self.signature_overlaps = [0] * len(roots)
        self.child_count = 0
        self.exact_key_count = 0
        self.signature_key_count = 0
        self.identity: dict[str, Any] | None = None
        self.inventory: _ChildInventory | None = None
        self.closed = False
        self._add_root_edges_and_overlaps()

    def _matching_keys(
        self, exacts: Iterable[str], signatures: Iterable[str]
    ) -> tuple[set[str], set[str]]:
        if self.forbidden.store is not None:
            return self.forbidden.store.matching_keys(exacts, signatures)
        return (
            {value for value in exacts if value in self.forbidden.exact},
            {value for value in signatures if value in self.forbidden.signatures},
        )

    def _add_root_edges_and_overlaps(self) -> None:
        batch_size = 4096
        for start in range(0, len(self.roots), batch_size):
            batch = self.roots[start : start + batch_size]
            exact_matches, signature_matches = self._matching_keys(
                (root.exact for root in batch),
                (signature for root in batch for signature in root.signatures),
            )
            edges: list[tuple[int, str, int]] = []
            for offset, root in enumerate(batch):
                index = start + offset
                edges.append((0, root.exact, index))
                edges.extend((1, signature, index) for signature in root.signatures)
                if root.exact in exact_matches:
                    self.exact_overlaps[index] += 1
                if any(signature in signature_matches for signature in root.signatures):
                    self.signature_overlaps[index] += 1
            self.connection.executemany(
                "INSERT OR IGNORE INTO edges(kind,key,root_index) VALUES (?,?,?)",
                edges,
            )
            self.connection.commit()

    def add_children(self, children: Sequence[Child]) -> None:
        exact_matches, signature_matches = self._matching_keys(
            (child.exact for child in children),
            (signature for child in children for signature in child.signatures),
        )
        child_rows: list[tuple[int, int, str, str]] = []
        edges: list[tuple[int, str, int]] = []
        for child in children:
            index = self.root_index_by_id[child.root_id]
            child_rows.append((index, child.ordinal, child.child_id, child.ofen))
            edges.append((0, child.exact, index))
            edges.extend((1, signature, index) for signature in child.signatures)
            if child.exact in exact_matches:
                self.exact_overlaps[index] += 1
            if any(signature in signature_matches for signature in child.signatures):
                self.signature_overlaps[index] += 1
        try:
            self.connection.executemany(
                "INSERT INTO children(root_index,ordinal,child_id,ofen) VALUES (?,?,?,?)",
                child_rows,
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("eligible children repeat a child ID or root ordinal") from error
        self.connection.executemany(
            "INSERT OR IGNORE INTO edges(kind,key,root_index) VALUES (?,?,?)",
            edges,
        )
        self.connection.commit()
        self.child_count += len(children)

    def finalize_components(
        self,
    ) -> tuple[
        dict[str, str],
        dict[str, tuple[str, ...]],
        dict[str, dict[str, int]],
    ]:
        count = len(self.roots)
        parent = list(range(count))

        def find(value: int) -> int:
            root = value
            while root != parent[root]:
                root = parent[root]
            while value != root:
                next_value = parent[value]
                parent[value] = root
                value = next_value
            return root

        def union(left: int, right: int) -> None:
            a = find(left)
            b = find(right)
            if a != b:
                low, high = sorted((a, b))
                parent[high] = low

        group_owner: dict[str, int] = {}
        for index, root in enumerate(self.roots):
            prior = group_owner.setdefault(root.group_id, index)
            union(index, prior)

        prior_key: tuple[int, str] | None = None
        owner = -1
        exact_keys = 0
        signature_keys = 0
        for kind, key, root_index in self.connection.execute(
            "SELECT kind,key,root_index FROM edges ORDER BY kind,key,root_index"
        ):
            current = (int(kind), str(key))
            if current != prior_key:
                prior_key = current
                owner = int(root_index)
                if kind == 0:
                    exact_keys += 1
                else:
                    signature_keys += 1
            else:
                union(owner, int(root_index))
        self.exact_key_count = exact_keys
        self.signature_key_count = signature_keys

        members_by_parent: defaultdict[int, list[str]] = defaultdict(list)
        for index, root in enumerate(self.roots):
            members_by_parent[find(index)].append(root.root_id)
        root_components: dict[str, str] = {}
        component_members: dict[str, tuple[str, ...]] = {}
        component_overlaps: dict[str, dict[str, int]] = {}
        for values in members_by_parent.values():
            ordered = tuple(sorted(values))
            component = _component_id(ordered)
            component_members[component] = ordered
            component_overlaps[component] = {
                "exact": sum(
                    self.exact_overlaps[self.root_index_by_id[value]]
                    for value in ordered
                ),
                "conservative": sum(
                    self.signature_overlaps[self.root_index_by_id[value]]
                    for value in ordered
                ),
            }
            for root_id in ordered:
                root_components[root_id] = component
        return root_components, component_members, component_overlaps

    def selected_children(self, root: Root, seed: int) -> tuple[dict[str, str], ...]:
        index = self.root_index_by_id[root.root_id]
        rows = list(
            self.connection.execute(
                "SELECT ordinal,child_id,ofen FROM children "
                "WHERE root_index=? ORDER BY ordinal",
                (index,),
            )
        )
        count = len(rows)
        if count != root.legal_child_count:
            raise ValueError(f"root {root.root_id!r} lacks complete legal-child coverage")
        if count < CHILDREN_PER_ROOT:
            raise ValueError(f"root {root.root_id!r} has fewer than four legal children")
        rotation = _hash_integer(
            CHILD_ROTATION_DOMAIN, seed, root.root_id, root.group_id, root.ofen
        ) % count
        spread = tuple((position * count) // CHILDREN_PER_ROOT for position in range(4))
        chosen = {rows[(rotation + position) % count] for position in spread}
        if len(chosen) != CHILDREN_PER_ROOT:
            raise AssertionError("four-child spread selected duplicates")
        return tuple(
            {"childId": str(row[1]), "normalizedChildOfen": str(row[2])}
            for row in sorted(chosen, key=lambda item: item[0])
        )

    @property
    def disk_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.connection.close()
        self.temporary.cleanup()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _load_bounded_children(
    path: Path, roots: Sequence[Root], forbidden: Forbidden
) -> tuple[_BoundedGraphStore, dict[str, Any]]:
    roots_by_id = {root.root_id: root for root in roots}
    root_ordinals = {root.root_id: index for index, root in enumerate(roots)}
    store = _BoundedGraphStore(roots, forbidden)
    previous_order: tuple[int, int] | None = None
    counts = [0] * len(roots)
    source_order = _CanonicalArrayHasher()
    semantic_rows = _CanonicalArrayHasher()
    batch: list[Child] = []
    try:
        with _StableJsonlReader(path, "terminal eligible children") as reader:
            for number, row in reader:
                label = f"terminal eligible children:{number}"
                child = _child_from_row(row, label, roots_by_id)
                order = (root_ordinals[child.root_id], child.ordinal)
                if previous_order is not None and order <= previous_order:
                    raise ValueError(
                        "eligible-child rows are not exact root/ordinal order"
                    )
                previous_order = order
                if child.ordinal != counts[order[0]]:
                    raise ValueError(
                        f"root {child.root_id!r} child ordinals are not contiguous"
                    )
                counts[order[0]] += 1
                source_order.add(
                    {"rootId": child.root_id, "moveOrdinal": child.ordinal}
                )
                semantic_rows.add(_semantic_child_row(child))
                batch.append(child)
                if len(batch) >= 2048:
                    store.add_children(batch)
                    batch.clear()
            if batch:
                store.add_children(batch)
                batch.clear()
            identity = reader.identity
        if identity is None:
            raise AssertionError("eligible-child stream identity was not finalized")
        for index, root in enumerate(roots):
            if counts[index] != root.legal_child_count:
                raise ValueError(
                    f"root {root.root_id!r} lacks complete legal-child coverage"
                )
        store.identity = identity
        store.inventory = _ChildInventory(
            store.child_count,
            source_order.hexdigest(),
            semantic_rows.hexdigest(),
        )
        return store, identity
    except BaseException:
        store.close()
        raise


def _target_key(value: str) -> bool:
    if value in _TARGET_DECLARATION_KEYS:
        return False
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", str(value))
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    tokens = tuple(
        token for token in re.split(r"[^A-Za-z0-9]+", text.lower()) if token
    )
    forms: list[set[str]] = []
    for token in tokens:
        variants = {token}
        if token.endswith("ies"):
            variants.add(token[:-3] + "y")
        if token.endswith("es"):
            variants.add(token[:-2])
        if token.endswith("s"):
            variants.add(token[:-1])
        forms.append(variants)
    if any(
        "best" in forms[index] and "move" in forms[index + 1]
        for index in range(max(0, len(forms) - 1))
    ):
        return True
    return any(variants & _TARGET_STEMS for variants in forms)


def _reject_target_keys(value: Any, label: str) -> None:
    if type(value) is dict:
        for key, item in value.items():
            if key not in _TARGET_DECLARATION_KEYS and _target_key(str(key)):
                raise ValueError(f"{label} contains forbidden target/score-like key {key!r}")
            _reject_target_keys(item, label)
    elif type(value) is list:
        for item in value:
            _reject_target_keys(item, label)


def _catalog_position_id(row: Mapping[str, Any]) -> str:
    basis = {
        "ofen": str(row.get("ofen", "")),
        "sourceGameId": str(row.get("sourceGameId", "")),
        "sourceRunId": str(row.get("sourceRunId", "")),
        "sourceArtifactSha256": str(row.get("sourceArtifactSha256", "")).lower(),
    }
    return "prior-" + _digest(basis)


def _identity_list(value: Any, label: str, *, nonempty: bool) -> list[dict[str, Any]]:
    if type(value) is not list or (nonempty and not value):
        raise ValueError(f"{label} is not a valid identity list")
    result: list[dict[str, Any]] = []
    paths: set[str] = set()
    inodes: set[tuple[int, int]] = set()
    for index, item in enumerate(value):
        identity = _verify_identity_record(item, f"{label}[{index}]")
        _, inode = _descriptor_inode(Path(identity["path"]), f"{label}[{index}]")
        path_key = os.path.normcase(os.path.normpath(identity["path"]))
        if path_key in paths or inode in inodes:
            raise ValueError(f"{label} repeats a path/inode")
        paths.add(path_key)
        inodes.add(inode)
        result.append(identity)
    if result != sorted(result, key=lambda item: item["path"].casefold()):
        raise ValueError(f"{label} is not canonically ordered")
    return result


def _verify_embedded_identities(value: Any, label: str) -> int:
    """Rehash every strict identity nested in opaque provenance evidence."""

    if type(value) is dict:
        if set(value) == set(IDENTITY_FIELDS):
            _verify_identity_record(value, label)
            return 1
        return sum(
            _verify_embedded_identities(item, f"{label}.{key}")
            for key, item in value.items()
        )
    if type(value) is list:
        return sum(
            _verify_embedded_identities(item, f"{label}[{index}]")
            for index, item in enumerate(value)
        )
    return 0


def _load_forbidden(manifests: Sequence[Path]) -> Forbidden:
    if not manifests:
        raise ValueError("at least one prior-forbidden manifest is required")
    store = _ForbiddenKeyStore()
    source_artifacts: set[str] = set()
    manifest_ids: list[dict[str, Any]] = []
    catalog_ids: list[dict[str, Any]] = []
    positions = 0
    seen_manifest_paths: set[str] = set()
    seen_catalog_paths: set[str] = set()
    try:
        for manifest_index, path in enumerate(manifests):
            label = f"prior-forbidden manifest {manifest_index}"
            # This exact-schema document contains provenance and policy metadata.
            # Its only target-like producer keys are explicit artifact names;
            # catalog rows allow no such exception before JSON value decode.
            manifest, manifest_id = _load_json(
                path,
                label,
                target_opaque=True,
                allowed_target_metadata_keys={"deepHceV2", "omegaDecisionTeacher"},
            )
            _exact_keys(manifest, FORBIDDEN_MANIFEST_FIELDS, label)
            if (
                type(manifest["schemaVersion"]) is not int
                or manifest["schemaVersion"] != SCHEMA_VERSION
                or manifest["kind"] != FORBIDDEN_MANIFEST_KIND
                or manifest["targetOpaque"] is not True
                or type(manifest["targetOrScoreFieldsDecoded"]) is not int
                or manifest["targetOrScoreFieldsDecoded"] != 0
                or type(manifest["targetOrScoreFieldsEmitted"]) is not int
                or manifest["targetOrScoreFieldsEmitted"] != 0
            ):
                raise ValueError(f"{label} is not target-opaque")
            _validate_prior_timestamp(manifest["createdUtc"], f"{label}.createdUtc")
            manifest_key = os.path.normcase(os.path.normpath(manifest_id["path"]))
            if manifest_key in seen_manifest_paths:
                raise ValueError("prior-forbidden manifest repeated")
            seen_manifest_paths.add(manifest_key)
            schema = manifest["catalogSchema"]
            if type(schema) is not dict or schema != {
                "recognizedFields": sorted(FORBIDDEN_ROW_FIELDS),
                "unknownFieldPolicy": "abort",
                "minimumPositionIdentity": (
                    "at least one exact or conservative orbit signature per row"
                ),
            }:
                raise ValueError(f"{label} declares an unsupported catalog schema")
            source_inventory = _identity_list(
                manifest["sourceInventory"], f"{label}.sourceInventory", nonempty=True
            )
            if manifest["sourceInventorySha256"] != _digest(source_inventory):
                raise ValueError(f"{label} source inventory digest differs")
            source_hashes = {item["sha256"] for item in source_inventory}
            source_artifacts.update(source_hashes)
            for name, digest_name in (
                ("sourceProjectionManifests", "sourceProjectionManifestsSha256"),
                ("priorSourceAudits", "priorSourceAuditsSha256"),
            ):
                values = manifest[name]
                if type(values) is not list or manifest[digest_name] != _digest(values):
                    raise ValueError(f"{label} {name} digest differs")
                _verify_embedded_identities(values, f"{label}.{name}")
            if type(manifest["extractionPolicy"]) is not dict:
                raise ValueError(f"{label}.extractionPolicy is not an object")
            if _verify_embedded_identities(manifest["producer"], f"{label}.producer") < 1:
                raise ValueError(f"{label}.producer contains no pinned identity")
            catalog_identity = _validate_identity_record(
                manifest["catalog"], f"{label}.catalog"
            )
            catalog_key = os.path.normcase(os.path.normpath(catalog_identity["path"]))
            if catalog_key in seen_catalog_paths:
                raise ValueError("prior-forbidden catalog repeated")
            seen_catalog_paths.add(catalog_key)
            store.begin_catalog()
            position_batch: list[str] = []
            exact_batch: list[str] = []
            signature_batch: list[str] = []

            def flush() -> None:
                if not position_batch:
                    return
                store.add_batch(
                    position_batch,
                    exact_batch,
                    signature_batch,
                    label=f"{label} catalog",
                )
                position_batch.clear()
                exact_batch.clear()
                signature_batch.clear()

            with _StableJsonlReader(
                Path(catalog_identity["path"]),
                f"{label} catalog",
                target_opaque=True,
                expected_identity=catalog_identity,
            ) as reader:
                for row_number, row in reader:
                    row_label = f"{label} catalog row {row_number}"
                    unknown = set(row) - FORBIDDEN_ROW_FIELDS
                    if unknown:
                        raise ValueError(
                            f"{row_label} undeclared fields: {sorted(unknown)}"
                        )
                    if (
                        row.get("schemaVersion") != SCHEMA_VERSION
                        or row.get("kind") != FORBIDDEN_ROW_KIND
                    ):
                        raise ValueError(f"{row_label} header changed")
                    position_id = row.get("positionId")
                    if type(position_id) is not str or not position_id:
                        raise ValueError(f"{row_label} has an empty positionId")
                    source_sha = row.get("sourceArtifactSha256")
                    _sha(source_sha, f"{row_label}.sourceArtifactSha256")
                    if source_sha not in source_hashes:
                        raise ValueError(
                            f"{row_label} source artifact absent from inventory"
                        )
                    exact_key = row.get("exactPositionKey", "")
                    orbit_key = row.get("conservativeOrbitKey", "")
                    signature_values = row.get("conservativeOrbitSignatures", [])
                    if type(signature_values) is not list or any(
                        type(item) is not str or _SHA.fullmatch(item) is None
                        for item in signature_values
                    ):
                        raise ValueError(f"{row_label} orbit signatures are malformed")
                    if signature_values != sorted(set(signature_values)):
                        raise ValueError(
                            f"{row_label} orbit signatures are not canonical"
                        )
                    if exact_key:
                        _sha(exact_key, f"{row_label}.exactPositionKey")
                    if orbit_key:
                        _sha(orbit_key, f"{row_label}.conservativeOrbitKey")
                    row_signatures = set(signature_values)
                    if orbit_key:
                        row_signatures.add(orbit_key)
                    if "ofen" in row:
                        ofen = _normalized_ofen(row["ofen"], f"{row_label}.ofen")
                        derived_exact, derived_orbit, derived_signatures = _leakage_keys(ofen)
                        if (
                            exact_key != derived_exact
                            or orbit_key != derived_orbit
                            or set(signature_values) != set(derived_signatures)
                        ):
                            raise ValueError(
                                f"{row_label} position signatures differ from OFEN"
                            )
                        exact_key = derived_exact
                        row_signatures.update(derived_signatures)
                    if not exact_key and not row_signatures:
                        raise ValueError(f"{row_label} has no position identity")
                    if position_id != _catalog_position_id(row):
                        raise ValueError(
                            f"{row_label} content-derived positionId differs"
                        )
                    position_batch.append(position_id)
                    if exact_key:
                        exact_batch.append(exact_key)
                    signature_batch.extend(sorted(row_signatures))
                    # A single bounded row may legitimately carry many orbit
                    # signatures.  Bound accumulated key entries as well as
                    # row count so adversarial high-cardinality rows cannot
                    # multiply the resident batch by 4,096.
                    if (
                        len(position_batch) >= MAX_FORBIDDEN_BATCH_ROWS
                        or len(exact_batch) + len(signature_batch)
                        >= MAX_FORBIDDEN_BATCH_KEY_ENTRIES
                    ):
                        flush()
                flush()
                row_count = reader.rows
                actual_catalog_id = reader.identity
            if actual_catalog_id != catalog_identity:
                raise ValueError(f"{label} catalog identity changed")
            if (
                type(manifest["positionCount"]) is not int
                or manifest["positionCount"] != row_count
            ):
                raise ValueError(f"{label} row count differs")
            positions += row_count
            manifest_ids.append(manifest_id)
            catalog_ids.append(catalog_identity)
        exact_count = store.key_count("exact_keys")
        signature_count = store.key_count("signature_keys")
        return Forbidden(
            _SqliteKeySet(store, "exact_keys", exact_count),
            _SqliteKeySet(store, "signature_keys", signature_count),
            tuple(manifest_ids),
            tuple(catalog_ids),
            positions,
            frozenset(source_artifacts),
            store,
        )
    except BaseException:
        store.close()
        raise


def _assert_source_artifact_disjoint(
    roots_identity: Mapping[str, Any],
    children_identity: Mapping[str, Any],
    forbidden: Forbidden,
) -> None:
    overlaps = sorted(
        {roots_identity["sha256"], children_identity["sha256"]}
        & set(forbidden.source_artifact_sha256)
    )
    if overlaps:
        raise ValueError(
            "current terminal source artifacts overlap prior-forbidden source data: "
            + ", ".join(overlaps)
        )


def _terminal_binding(
    lineage_path: Path,
    roots_identity: Mapping[str, Any],
    children_identity: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    document = _verify_terminal_lineage(lineage_path)
    lineage_identity = _identity(lineage_path, "terminal classifier lineage")
    if (
        document.get("eligibleRoots") != roots_identity
        or document.get("eligibleChildren") != children_identity
        or document.get("targetFieldsDecodedAtCompletion") != 0
        or document.get("resultInformationRead") is not False
        or document.get("finalStageSeal") is not True
    ):
        raise ValueError("terminal lineage does not bind the exact target-free inputs")
    return document, lineage_identity


def _component_id(root_ids: Sequence[str]) -> str:
    ordered = sorted(root_ids)
    stream = _CanonicalArrayHasher(COMPONENT_DOMAIN.encode("utf-8") + b"\0")
    for root_id in ordered:
        stream.add(root_id)
    return "component-" + stream.hexdigest()


def _component_membership_digest(
    component_members: Mapping[str, Sequence[str]],
) -> str:
    """Hash canonical membership rows without materializing nested arrays."""

    digest = hashlib.sha256()
    digest.update(b"[")
    first_component = True
    for component in sorted(component_members):
        if not first_component:
            digest.update(b",")
        first_component = False
        digest.update(b'{"leakageComponentId":')
        digest.update(_canonical_json(component))
        digest.update(b',"sourceRootIds":[')
        first_root = True
        for root_id in component_members[component]:
            if not first_root:
                digest.update(b",")
            first_root = False
            digest.update(_canonical_json(root_id))
        digest.update(b"]}")
    digest.update(b"]")
    return digest.hexdigest()


def _component_assignment_digest(
    component_members: Mapping[str, Sequence[str]],
    component_splits: Mapping[str, str],
    component_forbidden: Mapping[str, bool],
) -> str:
    stream = _CanonicalArrayHasher()
    for component in sorted(component_members):
        stream.add(
            {
                "leakageComponentId": component,
                "split": component_splits[component],
                "forbidden": component_forbidden[component],
            }
        )
    return stream.hexdigest()


def _hash_integer(domain: str, *parts: object) -> int:
    payload = domain.encode("utf-8") + b"\0" + _canonical_json(list(parts))
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


def _split_for(component_id: str, seed: int) -> str:
    bucket = _hash_integer(SPLIT_DOMAIN, seed, component_id) % 6
    for split, buckets in SPLIT_BUCKETS.items():
        if bucket in buckets:
            return split
    raise AssertionError("unreachable split bucket")


def _selected_children(root: Root, children: Sequence[Child], seed: int) -> tuple[Child, ...]:
    ordered = sorted(children, key=lambda child: child.ordinal)
    count = len(ordered)
    if count < CHILDREN_PER_ROOT:
        raise ValueError(f"root {root.root_id!r} has fewer than four legal children")
    rotation = _hash_integer(
        CHILD_ROTATION_DOMAIN, seed, root.root_id, root.group_id, root.ofen
    ) % count
    spread = tuple((index * count) // CHILDREN_PER_ROOT for index in range(4))
    selected = {ordered[(rotation + index) % count] for index in spread}
    if len(selected) != CHILDREN_PER_ROOT:
        raise AssertionError("four-child spread selected duplicates")
    return tuple(sorted(selected, key=lambda child: child.ordinal))


def _build_components(
    roots: Sequence[Root], children: Sequence[Child]
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """In-memory semantic reference for bounded-fixture equivalence tests."""

    dsu = DSU(root.root_id for root in roots)
    groups: dict[str, str] = {}
    exact_owner: dict[str, str] = {}
    signature_owner: dict[str, str] = {}

    def connect(root_id: str, exact: str, signatures: Sequence[str]) -> None:
        prior = exact_owner.setdefault(exact, root_id)
        dsu.union(root_id, prior)
        for signature in signatures:
            prior_signature = signature_owner.setdefault(signature, root_id)
            dsu.union(root_id, prior_signature)

    for root in roots:
        prior_group = groups.setdefault(root.group_id, root.root_id)
        dsu.union(root.root_id, prior_group)
        connect(root.root_id, root.exact, root.signatures)
    for child in children:
        connect(child.root_id, child.exact, child.signatures)

    members: defaultdict[str, list[str]] = defaultdict(list)
    for root in roots:
        members[dsu.find(root.root_id)].append(root.root_id)
    component_members: dict[str, tuple[str, ...]] = {}
    root_components: dict[str, str] = {}
    for values in members.values():
        ordered = tuple(sorted(values))
        component = _component_id(ordered)
        component_members[component] = ordered
        for root_id in ordered:
            root_components[root_id] = component
    return root_components, component_members


def _semantic_root_row(root: Root) -> dict[str, Any]:
    return {
        "sourceRootId": root.root_id,
        "sourceGroupId": root.group_id,
        "normalizedRootOfen": root.ofen,
        "rootOfenSha256": root.ofen_sha256,
        "preclassificationTranscriptSha256": root.transcript_sha256,
        "legalChildCount": root.legal_child_count,
        "phase": root.phase,
        "parentSideToMove": root.side,
        "pieceCount": root.piece_count,
        "exactNnueV1Input": root.exact,
        "conservativeOrbitKey": root.orbit,
        "conservativeOrbitSignatures": list(root.signatures),
    }


def _semantic_child_row(child: Child) -> dict[str, Any]:
    return {
        "sourceRootId": child.root_id,
        "sourceGroupId": child.group_id,
        "childId": child.child_id,
        "move": child.move,
        "moveOrdinal": child.ordinal,
        "normalizedParentOfen": child.parent_ofen,
        "normalizedChildOfen": child.ofen,
        "childOfenSha256": child.ofen_sha256,
        "classification": "child-nonterminal",
        "exactNnueV1Input": child.exact,
        "conservativeOrbitKey": child.orbit,
        "conservativeOrbitSignatures": list(child.signatures),
    }


def _cell_inventory(root_ids: Sequence[str]) -> dict[str, Any]:
    ordered = sorted(root_ids)
    return {"roots": len(ordered), "sha256": _digest(ordered)}


def _compute(
    roots: Sequence[Root],
    children: Sequence[Child],
    forbidden: Forbidden,
    *,
    seed: int,
    quotas: Mapping[str, int],
) -> Computed:
    """In-memory semantic reference; production calls `_compute_bounded`."""

    if len(children) > MAX_REFERENCE_CHILDREN:
        raise ValueError("in-memory reference computation exceeds its safe ceiling")
    if type(seed) is not int or seed < 0 or seed > (1 << 63) - 1:
        raise ValueError("seed must be an exact unsigned 63-bit integer")
    if set(quotas) != set(SPLITS) or any(
        type(quotas[split]) is not int or quotas[split] <= 0 for split in SPLITS
    ):
        raise ValueError("quotas must be positive exact counts for all three splits")
    roots_by_id = {root.root_id: root for root in roots}
    if len(roots_by_id) != len(roots):
        raise ValueError("duplicate source root")
    children_by_root: defaultdict[str, list[Child]] = defaultdict(list)
    for child in children:
        children_by_root[child.root_id].append(child)

    root_components, component_members = _build_components(roots, children)
    component_splits = {
        component: _split_for(component, seed) for component in component_members
    }
    component_overlaps: dict[str, dict[str, int]] = {
        component: {"exact": 0, "conservative": 0}
        for component in component_members
    }
    for root in roots:
        component = root_components[root.root_id]
        if root.exact in forbidden.exact:
            component_overlaps[component]["exact"] += 1
        if any(signature in forbidden.signatures for signature in root.signatures):
            component_overlaps[component]["conservative"] += 1
    for child in children:
        component = root_components[child.root_id]
        if child.exact in forbidden.exact:
            component_overlaps[component]["exact"] += 1
        if any(signature in forbidden.signatures for signature in child.signatures):
            component_overlaps[component]["conservative"] += 1
    component_forbidden = {
        component: bool(counts["exact"] or counts["conservative"])
        for component, counts in component_overlaps.items()
    }

    candidates: defaultdict[tuple[str, str, str], list[Root]] = defaultdict(list)
    fewer_than_four = 0
    for root in roots:
        component = root_components[root.root_id]
        if component_forbidden[component]:
            continue
        if len(children_by_root[root.root_id]) < CHILDREN_PER_ROOT:
            fewer_than_four += 1
            continue
        split = component_splits[component]
        candidates[(split, root.phase, root.side)].append(root)

    selected_roots: list[Root] = []
    insufficiencies: list[str] = []
    candidate_counts: dict[str, dict[str, int]] = {
        split: {} for split in SPLITS
    }
    for split in SPLITS:
        for phase in PHASES:
            for side in SIDES:
                key = (split, phase, side)
                values = candidates[key]
                values.sort(
                    key=lambda root: (
                        _hash_integer(
                            ROOT_RANK_DOMAIN, seed, split, phase, side,
                            root_components[root.root_id], root.root_id,
                        ),
                        root.root_id,
                    )
                )
                cell = f"{phase}:{side}"
                candidate_counts[split][cell] = len(values)
                if len(values) < quotas[split]:
                    insufficiencies.append(
                        f"{split}/{cell}={len(values)}<{quotas[split]}"
                    )
                else:
                    selected_roots.extend(values[: quotas[split]])
    if insufficiencies:
        raise ValueError("target-free quota insufficiency: " + ", ".join(insufficiencies))

    routing_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    selected_by_split_cell: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    selected_components: set[str] = set()
    selected_exact_overlaps = 0
    selected_signature_overlaps = 0
    for root in sorted(selected_roots, key=lambda item: item.root_id):
        component = root_components[root.root_id]
        split = component_splits[component]
        selected_components.add(component)
        chosen = _selected_children(root, children_by_root[root.root_id], seed)
        selected_positions: tuple[Root | Child, ...] = (root, *chosen)
        selected_exact_overlaps += sum(
            position.exact in forbidden.exact for position in selected_positions
        )
        selected_signature_overlaps += sum(
            any(signature in forbidden.signatures for signature in position.signatures)
            for position in selected_positions
        )
        routing_rows.append(
            {
                "schemaVersion": SCHEMA_VERSION,
                "kind": ROUTING_KIND,
                "profileId": PROFILE_ID,
                "rootId": root.root_id,
                "sourceRootId": root.root_id,
                "sourceGroupId": root.group_id,
                "phase": root.phase,
                "parentSideToMove": root.side,
                "children": [
                    {"childId": child.child_id, "normalizedChildOfen": child.ofen}
                    for child in chosen
                ],
            }
        )
        component_rows.append(
            {
                "schemaVersion": SCHEMA_VERSION,
                "kind": COMPONENT_KIND,
                "profileId": PROFILE_ID,
                "rootId": root.root_id,
                "leakageComponentId": component,
                "split": split,
                "sourceRootId": root.root_id,
                "sourceGroupId": root.group_id,
            }
        )
        selected_by_split_cell[(split, f"{root.phase}:{root.side}")].append(root.root_id)

    if selected_exact_overlaps or selected_signature_overlaps:
        raise AssertionError("a prior-forbidden position escaped component exclusion")

    phase_side_inventories = {
        split: {
            cell: _cell_inventory(selected_by_split_cell[(split, cell)])
            for cell in CELLS
        }
        for split in SPLITS
    }
    all_position_exact = {root.exact for root in roots} | {child.exact for child in children}
    all_position_signatures = {
        signature for root in roots for signature in root.signatures
    } | {signature for child in children for signature in child.signatures}
    selected_ids = {row["rootId"] for row in routing_rows}
    coverage = {
        "eligibleRoots": len(roots),
        "eligibleChildren": len(children),
        "sourceGroups": len({root.group_id for root in roots}),
        "completeGraphPositions": len(roots) + len(children),
        "completeGraphComponents": len(component_members),
        "forbiddenComponents": sum(component_forbidden.values()),
        "forbiddenRoots": sum(
            len(component_members[component])
            for component, excluded in component_forbidden.items() if excluded
        ),
        "rootsWithFewerThanFourChildren": fewer_than_four,
        "candidateRootsBySplitAndCell": candidate_counts,
        "selectedRoots": len(routing_rows),
        "selectedChildren": len(routing_rows) * CHILDREN_PER_ROOT,
        "selectedComponents": len(selected_components),
        "unselectedEligibleRoots": len(roots) - len(selected_ids),
    }
    forbidden_authority = {
        "catalogManifests": list(forbidden.manifests),
        "catalogs": list(forbidden.catalogs),
        "catalogPositions": forbidden.positions,
        "catalogExactKeys": len(forbidden.exact),
        "catalogConservativeSignatures": len(forbidden.signatures),
        "completeGraphExactKeys": len(all_position_exact),
        "completeGraphConservativeSignatures": len(all_position_signatures),
        "completeGraphExactOverlapOccurrences": sum(
            counts["exact"] for counts in component_overlaps.values()
        ),
        "completeGraphConservativeSignatureOverlapOccurrences": sum(
            counts["conservative"] for counts in component_overlaps.values()
        ),
        "selectedExactPositionOverlaps": selected_exact_overlaps,
        "selectedConservativeSignatureOverlaps": selected_signature_overlaps,
        "sourceArtifactOverlaps": 0,
        "entireOverlappingComponentsExcluded": True,
        "targetFieldsDecoded": 0,
        "resultInformationRead": False,
    }
    digests = {
        "completeComponentMembershipSha256": _component_membership_digest(
            component_members
        ),
        "componentAssignmentSha256": _component_assignment_digest(
            component_members, component_splits, component_forbidden
        ),
        "routingRowsSha256": _digest(routing_rows),
        "componentRowsSha256": _digest(component_rows),
        "selectedRootOrderSha256": _digest([row["rootId"] for row in routing_rows]),
        "selectedChildOrderSha256": _digest(
            [child["childId"] for row in routing_rows for child in row["children"]]
        ),
    }
    return Computed(
        tuple(routing_rows), tuple(component_rows), tuple(roots), tuple(children),
        root_components, component_splits, component_forbidden, coverage,
        phase_side_inventories, digests, forbidden_authority,
    )


def _compute_bounded(
    roots: Sequence[Root],
    graph: _BoundedGraphStore,
    forbidden: Forbidden,
    *,
    seed: int,
    quotas: Mapping[str, int],
) -> Computed:
    """Compute the identical authority without retaining eligible children."""

    if type(seed) is not int or seed < 0 or seed > (1 << 63) - 1:
        raise ValueError("seed must be an exact unsigned 63-bit integer")
    if set(quotas) != set(SPLITS) or any(
        type(quotas[split]) is not int or quotas[split] <= 0 for split in SPLITS
    ):
        raise ValueError("quotas must be positive exact counts for all three splits")
    roots_by_id = {root.root_id: root for root in roots}
    if len(roots_by_id) != len(roots):
        raise ValueError("duplicate source root")

    root_components, component_members, component_overlaps = (
        graph.finalize_components()
    )
    component_splits = {
        component: _split_for(component, seed) for component in component_members
    }
    component_forbidden = {
        component: bool(counts["exact"] or counts["conservative"])
        for component, counts in component_overlaps.items()
    }

    candidates: defaultdict[tuple[str, str, str], list[Root]] = defaultdict(list)
    fewer_than_four = 0
    for root in roots:
        component = root_components[root.root_id]
        if component_forbidden[component]:
            continue
        if root.legal_child_count < CHILDREN_PER_ROOT:
            fewer_than_four += 1
            continue
        split = component_splits[component]
        candidates[(split, root.phase, root.side)].append(root)

    selected_roots: list[Root] = []
    insufficiencies: list[str] = []
    candidate_counts: dict[str, dict[str, int]] = {split: {} for split in SPLITS}
    for split in SPLITS:
        for phase in PHASES:
            for side in SIDES:
                values = candidates[(split, phase, side)]
                values.sort(
                    key=lambda root: (
                        _hash_integer(
                            ROOT_RANK_DOMAIN,
                            seed,
                            split,
                            phase,
                            side,
                            root_components[root.root_id],
                            root.root_id,
                        ),
                        root.root_id,
                    )
                )
                cell = f"{phase}:{side}"
                candidate_counts[split][cell] = len(values)
                if len(values) < quotas[split]:
                    insufficiencies.append(
                        f"{split}/{cell}={len(values)}<{quotas[split]}"
                    )
                else:
                    selected_roots.extend(values[: quotas[split]])
    if insufficiencies:
        raise ValueError("target-free quota insufficiency: " + ", ".join(insufficiencies))

    routing_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    selected_by_split_cell: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    selected_components: set[str] = set()
    for root in sorted(selected_roots, key=lambda item: item.root_id):
        component = root_components[root.root_id]
        if component_forbidden[component]:
            raise AssertionError("a forbidden component reached bounded selection")
        split = component_splits[component]
        selected_components.add(component)
        routing_rows.append(
            {
                "schemaVersion": SCHEMA_VERSION,
                "kind": ROUTING_KIND,
                "profileId": PROFILE_ID,
                "rootId": root.root_id,
                "sourceRootId": root.root_id,
                "sourceGroupId": root.group_id,
                "phase": root.phase,
                "parentSideToMove": root.side,
                "children": list(graph.selected_children(root, seed)),
            }
        )
        component_rows.append(
            {
                "schemaVersion": SCHEMA_VERSION,
                "kind": COMPONENT_KIND,
                "profileId": PROFILE_ID,
                "rootId": root.root_id,
                "leakageComponentId": component,
                "split": split,
                "sourceRootId": root.root_id,
                "sourceGroupId": root.group_id,
            }
        )
        selected_by_split_cell[(split, f"{root.phase}:{root.side}")].append(
            root.root_id
        )

    phase_side_inventories = {
        split: {
            cell: _cell_inventory(selected_by_split_cell[(split, cell)])
            for cell in CELLS
        }
        for split in SPLITS
    }
    selected_ids = {row["rootId"] for row in routing_rows}
    coverage = {
        "eligibleRoots": len(roots),
        "eligibleChildren": graph.child_count,
        "sourceGroups": len({root.group_id for root in roots}),
        "completeGraphPositions": len(roots) + graph.child_count,
        "completeGraphComponents": len(component_members),
        "forbiddenComponents": sum(component_forbidden.values()),
        "forbiddenRoots": sum(
            len(component_members[component])
            for component, excluded in component_forbidden.items()
            if excluded
        ),
        "rootsWithFewerThanFourChildren": fewer_than_four,
        "candidateRootsBySplitAndCell": candidate_counts,
        "selectedRoots": len(routing_rows),
        "selectedChildren": len(routing_rows) * CHILDREN_PER_ROOT,
        "selectedComponents": len(selected_components),
        "unselectedEligibleRoots": len(roots) - len(selected_ids),
    }
    forbidden_authority = {
        "catalogManifests": list(forbidden.manifests),
        "catalogs": list(forbidden.catalogs),
        "catalogPositions": forbidden.positions,
        "catalogExactKeys": len(forbidden.exact),
        "catalogConservativeSignatures": len(forbidden.signatures),
        "completeGraphExactKeys": graph.exact_key_count,
        "completeGraphConservativeSignatures": graph.signature_key_count,
        "completeGraphExactOverlapOccurrences": sum(
            counts["exact"] for counts in component_overlaps.values()
        ),
        "completeGraphConservativeSignatureOverlapOccurrences": sum(
            counts["conservative"] for counts in component_overlaps.values()
        ),
        "selectedExactPositionOverlaps": 0,
        "selectedConservativeSignatureOverlaps": 0,
        "sourceArtifactOverlaps": 0,
        "entireOverlappingComponentsExcluded": True,
        "targetFieldsDecoded": 0,
        "resultInformationRead": False,
    }
    digests = {
        "completeComponentMembershipSha256": _component_membership_digest(
            component_members
        ),
        "componentAssignmentSha256": _component_assignment_digest(
            component_members, component_splits, component_forbidden
        ),
        "routingRowsSha256": _digest(routing_rows),
        "componentRowsSha256": _digest(component_rows),
        "selectedRootOrderSha256": _digest([row["rootId"] for row in routing_rows]),
        "selectedChildOrderSha256": _digest(
            [child["childId"] for row in routing_rows for child in row["children"]]
        ),
    }
    return Computed(
        tuple(routing_rows),
        tuple(component_rows),
        tuple(roots),
        (),
        root_components,
        component_splits,
        component_forbidden,
        coverage,
        phase_side_inventories,
        digests,
        forbidden_authority,
    )


def _dependency_identities() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in ("omegaNnue", "selectScreen", "terminalLineageModule"):
        dependency = _pinned_dependency(name)
        current = _verify_identity_record(
            dependency.identity, f"{name} pinned dependency"
        )
        if current != dependency.identity:
            raise ValueError(f"{name} dependency changed after exact-byte execution")
        result[name] = dict(dependency.identity)
    return result


def _root_manifest_document(
    *,
    created_utc: str,
    terminal_lineage: Mapping[str, Any],
    eligible_roots: Mapping[str, Any],
    eligible_children: Mapping[str, Any],
    producer: Mapping[str, Any],
    dependencies: Mapping[str, Any],
    roots: Sequence[Root],
) -> dict[str, Any]:
    semantic = _CanonicalArrayHasher()
    for root in roots:
        semantic.add(_semantic_root_row(root))
    phase_side = Counter(f"{root.phase}:{root.side}" for root in roots)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ROOT_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "complete-target-free-source-root-inventory",
        "createdUtc": created_utc,
        "terminalLineage": dict(terminal_lineage),
        "eligibleRoots": dict(eligible_roots),
        "eligibleChildren": dict(eligible_children),
        "producer": dict(producer),
        "dependencyIdentities": dict(dependencies),
        "records": len(roots),
        "sourceGroups": len({root.group_id for root in roots}),
        "phaseSideCounts": {cell: phase_side[cell] for cell in CELLS},
        "sourceOrderSha256": _digest([root.root_id for root in roots]),
        "semanticRowsSha256": semantic.hexdigest(),
        "phasePolicy": dict(PHASE_POLICY),
        "targetFieldsDecoded": 0,
        "resultInformationRead": False,
        "finalStageSeal": True,
    }


def _child_manifest_document(
    *,
    created_utc: str,
    terminal_lineage: Mapping[str, Any],
    eligible_roots: Mapping[str, Any],
    eligible_children: Mapping[str, Any],
    source_root_manifest: Mapping[str, Any],
    producer: Mapping[str, Any],
    dependencies: Mapping[str, Any],
    roots: Sequence[Root],
    children: Sequence[Child] | None,
    child_inventory: _ChildInventory | None = None,
) -> dict[str, Any]:
    if child_inventory is None:
        if children is None:
            raise ValueError("child sequence or bounded child inventory is required")
        source_order = _CanonicalArrayHasher()
        semantic = _CanonicalArrayHasher()
        for child in children:
            source_order.add(
                {"rootId": child.root_id, "moveOrdinal": child.ordinal}
            )
            semantic.add(_semantic_child_row(child))
        child_inventory = _ChildInventory(
            len(children), source_order.hexdigest(), semantic.hexdigest()
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": CHILD_MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": "complete-target-free-source-child-inventory",
        "createdUtc": created_utc,
        "terminalLineage": dict(terminal_lineage),
        "eligibleRoots": dict(eligible_roots),
        "eligibleChildren": dict(eligible_children),
        "sourceRootManifest": dict(source_root_manifest),
        "producer": dict(producer),
        "dependencyIdentities": dict(dependencies),
        "records": child_inventory.records,
        "roots": len(roots),
        "sourceGroups": len({root.group_id for root in roots}),
        "sourceOrderSha256": child_inventory.source_order_sha256,
        "semanticRowsSha256": child_inventory.semantic_rows_sha256,
        "classification": "child-nonterminal",
        "completeLegalChildCoverage": True,
        "targetFieldsDecoded": 0,
        "resultInformationRead": False,
        "finalStageSeal": True,
    }


def _completion_document(
    *,
    created_utc: str,
    seed: int,
    quotas: Mapping[str, int],
    terminal_lineage: Mapping[str, Any],
    eligible_roots: Mapping[str, Any],
    eligible_children: Mapping[str, Any],
    prior_manifests: Sequence[Mapping[str, Any]],
    source_root_manifest: Mapping[str, Any],
    source_children_manifest: Mapping[str, Any],
    routing: Mapping[str, Any],
    component_map: Mapping[str, Any],
    producer: Mapping[str, Any],
    dependencies: Mapping[str, Any],
    computed: Computed,
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": COMPLETION_KIND,
        "profileId": PROFILE_ID,
        "status": "complete-target-free-routing-before-any-teacher-target",
        "createdUtc": created_utc,
        "seed": seed,
        "terminalLineage": dict(terminal_lineage),
        "eligibleRoots": dict(eligible_roots),
        "eligibleChildren": dict(eligible_children),
        "priorForbiddenManifests": [dict(item) for item in prior_manifests],
        "sourceRootManifest": dict(source_root_manifest),
        "sourceChildrenManifest": dict(source_children_manifest),
        "targetFreeRouting": dict(routing),
        "componentMap": dict(component_map),
        "producer": dict(producer),
        "dependencyIdentities": dict(dependencies),
        "phasePolicy": dict(PHASE_POLICY),
        "componentAlgorithm": dict(COMPONENT_ALGORITHM),
        "splitAlgorithm": dict(SPLIT_ALGORITHM),
        "childSelectionAlgorithm": dict(CHILD_SELECTION_ALGORITHM),
        "rootSelectionAlgorithm": dict(ROOT_SELECTION_ALGORITHM),
        "quotas": {split: quotas[split] for split in SPLITS},
        "coverage": dict(computed.coverage),
        "phaseSideInventories": dict(computed.inventories),
        "digests": dict(computed.digests),
        "forbiddenAuthority": dict(computed.forbidden_authority),
        "completeGraphBuiltBeforeFiltering": True,
        "componentsAssignedBeforeSelection": True,
        "targetRowsDecoded": 0,
        "targetFieldsDecoded": 0,
        "resultInformationRead": False,
        "finalStageSeal": True,
    }


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return _canonical_json(value) + b"\n"


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical_json(row) + b"\n" for row in rows)


def _exclusive_write(path: Path, payload: bytes) -> dict[str, Any]:
    output = _safe_parent(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(output, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        try:
            output.unlink()
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(descriptor)
    identity = _identity(output, "new publication")
    if identity["bytes"] != len(payload) or identity["sha256"] != _sha256_bytes(payload):
        raise ValueError(f"published bytes changed for {output}")
    return identity


def _delete_if_identity(path: Path, identity: Mapping[str, Any]) -> None:
    try:
        if _identity(path, "rollback publication") == identity:
            path.unlink()
    except (FileNotFoundError, ValueError):
        pass


def _preflight_outputs(
    outputs: Mapping[str, Path], existing: Mapping[str, Path]
) -> dict[str, Path]:
    resolved = {role: _safe_parent(path) for role, path in outputs.items()}
    keys: dict[str, str] = {}
    for role, path in {**existing, **resolved}.items():
        key = os.path.normcase(os.path.normpath(str(path.expanduser().resolve(strict=False))))
        if key in keys:
            raise ValueError(f"roles {keys[key]!r} and {role!r} share one path")
        keys[key] = role
    for role, path in resolved.items():
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite {role}: {path}")
    return resolved


def _materialize(
    *,
    eligible_roots: Path,
    eligible_children: Path,
    terminal_lineage: Path,
    prior_forbidden_manifests: Sequence[Path],
    routing_output: Path,
    component_output: Path,
    source_root_manifest_output: Path,
    source_children_manifest_output: Path,
    completion_output: Path,
    seed: int,
    created_utc: str,
    quotas: Mapping[str, int],
) -> dict[str, Any]:
    _validate_timestamp(created_utc, "createdUtc")
    roots, roots_identity = _parse_roots(eligible_roots)
    forbidden = _load_forbidden(prior_forbidden_manifests)
    graph, children_identity = _load_bounded_children(
        eligible_children, roots, forbidden
    )
    terminal_document, lineage_identity = _terminal_binding(
        terminal_lineage, roots_identity, children_identity
    )
    if _validate_timestamp(terminal_document["createdUtc"], "terminal createdUtc") >= created_utc:
        raise ValueError("routing completion must follow terminal-lineage completion")
    _assert_source_artifact_disjoint(roots_identity, children_identity, forbidden)
    producer = _identity(Path(__file__), "routing producer")
    dependencies = _dependency_identities()
    existing_roles: dict[str, Path] = {
        "eligibleRoots": Path(roots_identity["path"]),
        "eligibleChildren": Path(children_identity["path"]),
        "terminalLineage": Path(lineage_identity["path"]),
        "producer": Path(producer["path"]),
        **{f"dependency:{name}": Path(identity["path"]) for name, identity in dependencies.items()},
        **{
            f"priorManifest:{index}": Path(identity["path"])
            for index, identity in enumerate(forbidden.manifests)
        },
        **{
            f"priorCatalog:{index}": Path(identity["path"])
            for index, identity in enumerate(forbidden.catalogs)
        },
    }
    _assert_distinct_roles(existing_roles)
    outputs = _preflight_outputs(
        {
            "targetFreeRouting": routing_output,
            "componentMap": component_output,
            "sourceRootManifest": source_root_manifest_output,
            "sourceChildrenManifest": source_children_manifest_output,
            "completion": completion_output,
        },
        existing_roles,
    )
    computed = _compute_bounded(
        roots, graph, forbidden, seed=seed, quotas=quotas
    )
    root_document = _root_manifest_document(
        created_utc=created_utc,
        terminal_lineage=lineage_identity,
        eligible_roots=roots_identity,
        eligible_children=children_identity,
        producer=producer,
        dependencies=dependencies,
        roots=roots,
    )
    published: list[tuple[Path, dict[str, Any]]] = []
    try:
        routing_id = _exclusive_write(
            outputs["targetFreeRouting"], _jsonl_bytes(computed.routing_rows)
        )
        published.append((outputs["targetFreeRouting"], routing_id))
        component_id = _exclusive_write(
            outputs["componentMap"], _jsonl_bytes(computed.component_rows)
        )
        published.append((outputs["componentMap"], component_id))
        root_manifest_id = _exclusive_write(
            outputs["sourceRootManifest"], _json_bytes(root_document)
        )
        published.append((outputs["sourceRootManifest"], root_manifest_id))
        child_document = _child_manifest_document(
            created_utc=created_utc,
            terminal_lineage=lineage_identity,
            eligible_roots=roots_identity,
            eligible_children=children_identity,
            source_root_manifest=root_manifest_id,
            producer=producer,
            dependencies=dependencies,
            roots=roots,
            children=None,
            child_inventory=graph.inventory,
        )
        child_manifest_id = _exclusive_write(
            outputs["sourceChildrenManifest"], _json_bytes(child_document)
        )
        published.append((outputs["sourceChildrenManifest"], child_manifest_id))
        completion_document = _completion_document(
            created_utc=created_utc,
            seed=seed,
            quotas=quotas,
            terminal_lineage=lineage_identity,
            eligible_roots=roots_identity,
            eligible_children=children_identity,
            prior_manifests=forbidden.manifests,
            source_root_manifest=root_manifest_id,
            source_children_manifest=child_manifest_id,
            routing=routing_id,
            component_map=component_id,
            producer=producer,
            dependencies=dependencies,
            computed=computed,
        )
        completion_id = _exclusive_write(
            outputs["completion"], _json_bytes(completion_document)
        )
        published.append((outputs["completion"], completion_id))
        _verify_completion(outputs["completion"], expected_quotas=quotas)
        return completion_document
    except BaseException:
        for path, identity in reversed(published):
            _delete_if_identity(path, identity)
        raise


def build_authority(
    *,
    eligible_roots: Path,
    eligible_children: Path,
    terminal_lineage: Path,
    prior_forbidden_manifests: Sequence[Path],
    routing_output: Path,
    component_output: Path,
    source_root_manifest_output: Path,
    source_children_manifest_output: Path,
    completion_output: Path,
    seed: int,
    created_utc: str,
) -> dict[str, Any]:
    """Publish the fixed production authority; quotas are deliberately not caller-settable."""

    return _materialize(
        eligible_roots=eligible_roots,
        eligible_children=eligible_children,
        terminal_lineage=terminal_lineage,
        prior_forbidden_manifests=prior_forbidden_manifests,
        routing_output=routing_output,
        component_output=component_output,
        source_root_manifest_output=source_root_manifest_output,
        source_children_manifest_output=source_children_manifest_output,
        completion_output=completion_output,
        seed=seed,
        created_utc=created_utc,
        quotas=QUOTAS,
    )


def _parse_routing_output(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, identity = _load_bounded_jsonl_rows(
        path, "target-free routing", maximum_rows=MAX_ROUTING_ROOTS
    )
    seen_roots: set[str] = set()
    seen_children: set[str] = set()
    previous = ""
    for number, row in enumerate(rows, 1):
        label = f"target-free routing:{number}"
        _exact_keys(row, ROUTING_FIELDS, label)
        if (
            type(row["schemaVersion"]) is not int
            or row["schemaVersion"] != SCHEMA_VERSION
            or row["kind"] != ROUTING_KIND
            or row["profileId"] != PROFILE_ID
            or row["phase"] not in PHASES
            or row["parentSideToMove"] not in SIDES
            or type(row["children"]) is not list
            or len(row["children"]) != CHILDREN_PER_ROOT
        ):
            raise ValueError(f"{label} schema changed")
        for field in ("rootId", "sourceRootId", "sourceGroupId"):
            if type(row[field]) is not str or not row[field]:
                raise ValueError(f"{label}.{field} is invalid")
        if row["rootId"] != row["sourceRootId"]:
            raise ValueError(f"{label} rootId/sourceRootId policy changed")
        if row["rootId"] in seen_roots or row["rootId"] <= previous:
            raise ValueError(f"{label} duplicate/noncanonical root order")
        seen_roots.add(row["rootId"])
        previous = row["rootId"]
        for child in row["children"]:
            if type(child) is not dict:
                raise ValueError(f"{label} child is not an object")
            _exact_keys(child, ROUTING_CHILD_FIELDS, f"{label} child")
            child_id = child["childId"]
            if type(child_id) is not str or not child_id or child_id in seen_children:
                raise ValueError(f"{label} child ID is invalid/duplicate")
            seen_children.add(child_id)
            _normalized_ofen(child["normalizedChildOfen"], f"{label} child OFEN")
    return rows, identity


def _parse_component_output(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, identity = _load_bounded_jsonl_rows(
        path, "component map", maximum_rows=MAX_ROUTING_ROOTS
    )
    roots: set[str] = set()
    group_assignments: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    component_splits: defaultdict[str, set[str]] = defaultdict(set)
    previous = ""
    for number, row in enumerate(rows, 1):
        label = f"component map:{number}"
        _exact_keys(row, COMPONENT_FIELDS, label)
        if (
            type(row["schemaVersion"]) is not int
            or row["schemaVersion"] != SCHEMA_VERSION
            or row["kind"] != COMPONENT_KIND
            or row["profileId"] != PROFILE_ID
            or row["split"] not in SPLITS
        ):
            raise ValueError(f"{label} schema changed")
        for field in ("rootId", "leakageComponentId", "sourceRootId", "sourceGroupId"):
            if type(row[field]) is not str or not row[field]:
                raise ValueError(f"{label}.{field} is invalid")
        if row["rootId"] != row["sourceRootId"]:
            raise ValueError(f"{label} root ID policy changed")
        if row["rootId"] in roots or row["rootId"] <= previous:
            raise ValueError(f"{label} duplicate/noncanonical root order")
        roots.add(row["rootId"])
        previous = row["rootId"]
        group_assignments[row["sourceGroupId"]].add(
            (row["leakageComponentId"], row["split"])
        )
        component_splits[row["leakageComponentId"]].add(row["split"])
    if any(len(values) != 1 for values in group_assignments.values()):
        raise ValueError("one source group crosses components or splits")
    if any(len(values) != 1 for values in component_splits.values()):
        raise ValueError("one leakage component crosses splits")
    return rows, identity


def _verify_manifest_header(
    document: Mapping[str, Any], fields: frozenset[str], kind: str, status: str, label: str
) -> None:
    _exact_keys(document, fields, label)
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != kind
        or document["profileId"] != PROFILE_ID
        or document["status"] != status
        or document["targetFieldsDecoded"] != 0
        or document["resultInformationRead"] is not False
        or document["finalStageSeal"] is not True
    ):
        raise ValueError(f"{label} header changed")
    _validate_timestamp(document["createdUtc"], f"{label}.createdUtc")


def _verify_completion(
    path: Path, *, expected_quotas: Mapping[str, int]
) -> dict[str, Any]:
    completion, _ = _load_json(path, "routing completion")
    _exact_keys(completion, COMPLETION_FIELDS, "routing completion")
    if (
        type(completion["schemaVersion"]) is not int
        or completion["schemaVersion"] != SCHEMA_VERSION
        or completion["kind"] != COMPLETION_KIND
        or completion["profileId"] != PROFILE_ID
        or completion["status"]
        != "complete-target-free-routing-before-any-teacher-target"
        or completion["quotas"] != {split: expected_quotas[split] for split in SPLITS}
        or completion["phasePolicy"] != PHASE_POLICY
        or completion["componentAlgorithm"] != COMPONENT_ALGORITHM
        or completion["splitAlgorithm"] != SPLIT_ALGORITHM
        or completion["childSelectionAlgorithm"] != CHILD_SELECTION_ALGORITHM
        or completion["rootSelectionAlgorithm"] != ROOT_SELECTION_ALGORITHM
        or completion["completeGraphBuiltBeforeFiltering"] is not True
        or completion["componentsAssignedBeforeSelection"] is not True
        or type(completion["targetRowsDecoded"]) is not int
        or completion["targetRowsDecoded"] != 0
        or type(completion["targetFieldsDecoded"]) is not int
        or completion["targetFieldsDecoded"] != 0
        or completion["resultInformationRead"] is not False
        or completion["finalStageSeal"] is not True
    ):
        raise ValueError("routing completion header/policy changed")
    created = _validate_timestamp(completion["createdUtc"], "routing completion.createdUtc")
    seed = completion["seed"]
    if type(seed) is not int or seed < 0 or seed > (1 << 63) - 1:
        raise ValueError("routing completion seed changed")

    identity_names = (
        "terminalLineage", "eligibleRoots", "eligibleChildren", "sourceRootManifest",
        "sourceChildrenManifest", "targetFreeRouting", "componentMap", "producer",
    )
    identities = {
        name: (
            _verify_identity_record(completion[name], f"routing completion.{name}")
            if name == "producer"
            else _validate_identity_record(
                completion[name], f"routing completion.{name}"
            )
        )
        for name in identity_names
    }
    dependency_value = completion["dependencyIdentities"]
    if type(dependency_value) is not dict or set(dependency_value) != {
        "omegaNnue", "selectScreen", "terminalLineageModule"
    }:
        raise ValueError("routing completion dependency inventory changed")
    dependencies = {
        name: _verify_identity_record(value, f"routing dependency {name}")
        for name, value in dependency_value.items()
    }
    if identities["producer"] != _identity(Path(__file__), "routing producer"):
        raise ValueError("routing completion producer changed")
    if dependencies != _dependency_identities():
        raise ValueError("routing completion pinned dependency changed")
    prior_values = completion["priorForbiddenManifests"]
    if type(prior_values) is not list or not prior_values:
        raise ValueError("routing completion has no prior-forbidden manifests")
    prior_ids = [
        _verify_identity_record(value, f"routing prior manifest {index}")
        for index, value in enumerate(prior_values)
    ]
    forbidden = _load_forbidden([Path(value["path"]) for value in prior_ids])
    if list(forbidden.manifests) != prior_ids:
        raise ValueError("routing prior-forbidden identities changed")

    all_existing = {
        **{name: Path(value["path"]) for name, value in identities.items()},
        **{f"dependency:{name}": Path(value["path"]) for name, value in dependencies.items()},
        **{f"prior:{index}": Path(value["path"]) for index, value in enumerate(prior_ids)},
        **{
            f"priorCatalog:{index}": Path(value["path"])
            for index, value in enumerate(forbidden.catalogs)
        },
        "completion": path,
    }
    _assert_distinct_roles(all_existing)
    roots, roots_identity = _parse_roots(Path(identities["eligibleRoots"]["path"]))
    graph, children_identity = _load_bounded_children(
        Path(identities["eligibleChildren"]["path"]), roots, forbidden
    )
    if roots_identity != identities["eligibleRoots"] or children_identity != identities["eligibleChildren"]:
        raise ValueError("routing source identities changed")
    _assert_source_artifact_disjoint(roots_identity, children_identity, forbidden)
    terminal_document, lineage_identity = _terminal_binding(
        Path(identities["terminalLineage"]["path"]), roots_identity, children_identity
    )
    if lineage_identity != identities["terminalLineage"]:
        raise ValueError("routing terminal-lineage identity changed")
    if _validate_timestamp(terminal_document["createdUtc"], "terminal createdUtc") >= created:
        raise ValueError("routing completion chronology changed")

    routing_rows, routing_identity = _parse_routing_output(
        Path(identities["targetFreeRouting"]["path"])
    )
    component_rows, component_identity = _parse_component_output(
        Path(identities["componentMap"]["path"])
    )
    if routing_identity != identities["targetFreeRouting"] or component_identity != identities["componentMap"]:
        raise ValueError("routing/component identity changed")
    computed = _compute_bounded(
        roots, graph, forbidden, seed=seed, quotas=expected_quotas
    )
    if routing_rows != list(computed.routing_rows):
        raise ValueError("target-free routing differs from exact recomputation")
    if component_rows != list(computed.component_rows):
        raise ValueError("component map differs from complete-graph recomputation")

    root_manifest, root_manifest_identity = _load_json(
        Path(identities["sourceRootManifest"]["path"]), "source-root manifest"
    )
    child_manifest, child_manifest_identity = _load_json(
        Path(identities["sourceChildrenManifest"]["path"]), "source-children manifest"
    )
    if root_manifest_identity != identities["sourceRootManifest"] or child_manifest_identity != identities["sourceChildrenManifest"]:
        raise ValueError("source manifest identity changed")
    _verify_manifest_header(
        root_manifest, ROOT_MANIFEST_FIELDS, ROOT_MANIFEST_KIND,
        "complete-target-free-source-root-inventory", "source-root manifest",
    )
    _verify_manifest_header(
        child_manifest, CHILD_MANIFEST_FIELDS, CHILD_MANIFEST_KIND,
        "complete-target-free-source-child-inventory", "source-children manifest",
    )
    expected_root_manifest = _root_manifest_document(
        created_utc=created,
        terminal_lineage=lineage_identity,
        eligible_roots=roots_identity,
        eligible_children=children_identity,
        producer=identities["producer"],
        dependencies=dependencies,
        roots=roots,
    )
    if root_manifest != expected_root_manifest:
        raise ValueError("source-root manifest differs from semantic recomputation")
    expected_child_manifest = _child_manifest_document(
        created_utc=created,
        terminal_lineage=lineage_identity,
        eligible_roots=roots_identity,
        eligible_children=children_identity,
        source_root_manifest=root_manifest_identity,
        producer=identities["producer"],
        dependencies=dependencies,
        roots=roots,
        children=None,
        child_inventory=graph.inventory,
    )
    if child_manifest != expected_child_manifest:
        raise ValueError("source-children manifest differs from semantic recomputation")
    expected_completion = _completion_document(
        created_utc=created,
        seed=seed,
        quotas=expected_quotas,
        terminal_lineage=lineage_identity,
        eligible_roots=roots_identity,
        eligible_children=children_identity,
        prior_manifests=forbidden.manifests,
        source_root_manifest=root_manifest_identity,
        source_children_manifest=child_manifest_identity,
        routing=routing_identity,
        component_map=component_identity,
        producer=identities["producer"],
        dependencies=dependencies,
        computed=computed,
    )
    if completion != expected_completion:
        raise ValueError("routing completion differs from fresh semantic replay")
    return completion


def verify_completion(path: Path) -> dict[str, Any]:
    """Freshly verify one formal production completion with the fixed quotas."""

    return _verify_completion(path, expected_quotas=QUOTAS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="publish the target-free authority")
    build.add_argument("--eligible-roots", required=True, type=Path)
    build.add_argument("--eligible-children", required=True, type=Path)
    build.add_argument("--terminal-lineage", required=True, type=Path)
    build.add_argument(
        "--prior-forbidden-manifest", action="append", required=True, type=Path
    )
    build.add_argument("--routing-output", required=True, type=Path)
    build.add_argument("--component-output", required=True, type=Path)
    build.add_argument("--source-root-manifest-output", required=True, type=Path)
    build.add_argument("--source-children-manifest-output", required=True, type=Path)
    build.add_argument("--completion-output", required=True, type=Path)
    build.add_argument("--seed", required=True, type=int)
    build.add_argument("--created-utc", required=True)
    verify = subparsers.add_parser("verify", help="freshly verify a completion")
    verify.add_argument("--completion", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "build":
        document = build_authority(
            eligible_roots=arguments.eligible_roots,
            eligible_children=arguments.eligible_children,
            terminal_lineage=arguments.terminal_lineage,
            prior_forbidden_manifests=arguments.prior_forbidden_manifest,
            routing_output=arguments.routing_output,
            component_output=arguments.component_output,
            source_root_manifest_output=arguments.source_root_manifest_output,
            source_children_manifest_output=arguments.source_children_manifest_output,
            completion_output=arguments.completion_output,
            seed=arguments.seed,
            created_utc=arguments.created_utc,
        )
        print(
            "Published target-free routing: "
            f"roots={document['coverage']['selectedRoots']} "
            f"children={document['coverage']['selectedChildren']}"
        )
        print(f"Completion: {arguments.completion_output.expanduser().resolve()}")
        return 0
    verify_completion(arguments.completion)
    print(f"Verified target-free routing completion: {arguments.completion.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
