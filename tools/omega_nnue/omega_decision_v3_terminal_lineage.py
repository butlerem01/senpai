#!/usr/bin/env python3
"""Strict pre-teacher authority for the Omega-decision-v3 terminal gate.

This module owns the two no-clobber authority documents around the terminal
classifier launch and exposes the exact, sealed invocation for the caller:

* a claim which freezes all inputs, executable/runtime identities, producer,
  and planned output paths before the classifier runs; and
* a lineage receipt which strictly replays the native artifact schemas,
  recomputes their classifier-only root/child partition, and binds the result before
  any teacher target may be decoded.

The later capsule verifier remains responsible for launching the pinned
classifier afresh and byte-comparing its outputs.  Keeping process execution
out of this layer makes the authority graph testable with synthetic artifacts
without weakening any identity or chronology edge.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v6-move-decision-v1"
CLAIM_KIND = "omega-decision-v3-terminal-classifier-claim"
LINEAGE_KIND = "omega-decision-v3-terminal-classifier-lineage"
HISTORY_MANIFEST_KIND = "omega-g6-history-root-manifest"
BUNDLE_MANIFEST_KIND = "omega-g6-terminal-classifier-bundle"
RUNTIME_MANIFEST_KIND = "omega-g6-frozen-dotnet-runtime-manifest"
HISTORY_BUNDLE_MANIFEST_KIND = "omega-g6-history-root-sampler-bundle"
NATIVE_MANIFEST_KIND = "omega-g6-terminal-preclassification-manifest"
HISTORY_ROOT_KIND = "omega-g6-history-root"
TRANSCRIPT_KIND = "omega-g6-terminal-preclassification"
ELIGIBLE_ROOT_KIND = "omega-g6-terminal-safe-root"
ELIGIBLE_CHILD_KIND = "omega-g6-terminal-safe-child"
EXPECTED_CHESSLIB_SHA256 = (
    "16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c"
)
EXPECTED_CHESSLIB_BYTES = 248_832
EXPECTED_NEWTONSOFT_SHA256 = (
    "a28c251dfe36d881e9e2462e171441b8b0ec156fe3f452602c9149b1b9efe05b"
)
EXPECTED_NEWTONSOFT_BYTES = 723_368
EXPECTED_SYSTEM_IO_PORTS_SHA256 = (
    "2767e21f384cca9004b1266ec4b71d3b8a76898594382c377c726c780aa34508"
)
EXPECTED_SYSTEM_IO_PORTS_BYTES = 37_648
EXPECTED_SAMPLER_SHA256 = (
    "ae182dfcd3b33a0996481f4ed887c06dec4508a6584d15cb0cefe47012029499"
)
EXPECTED_SAMPLER_BYTES = 110_592
EXPECTED_CLASSIFIER_SHA256 = (
    "ecb149c6db6d298437b6de69bef40fbeb1c228686e7bc111de94e8fdb63becc2"
)
EXPECTED_CLASSIFIER_BYTES = 131_072
EXPECTED_DOTNET_SHA256 = (
    "a5ccdc3a41d5e5c6014ff64509aed176db39f4f14caffff3dd1997f8907e94d7"
)
EXPECTED_DOTNET_BYTES = 167_248
EXPECTED_RUNTIME_VERSION = "10.0.9"
EXPECTED_RUNTIME_BUNDLE_SHA256 = (
    "0ce194480dfb9a58a59c79bf94f19cb2eb571635a00547094a9c8ff28bb5f8f8"
)
EXPECTED_RUNTIME_MANIFEST_SHA256 = (
    "c8543f22f4b353ee461f2e417c3d06ea2f05de2622fab49b789923f9944e00ee"
)
EXPECTED_RUNTIME_MANIFEST_BYTES = 33_633
EXPECTED_RUNTIME_FILE_COUNT = 191
EXPECTED_CORELIB_SHA256 = (
    "dc1945de746f94987ec705a1f27d512abf72a41a1da37e414d1951e4e823037a"
)
EXPECTED_CORELIB_BYTES = 16_017_232
EXPECTED_SAMPLER_DEPS_SHA256 = (
    "7d8ef239efc3d76ed83ad79fe38d02028ed58584e869ff8796ea04561cad930b"
)
EXPECTED_SAMPLER_DEPS_BYTES = 1_527
EXPECTED_CLASSIFIER_DEPS_SHA256 = (
    "d256893d4cab42ef23dd6cd49ece2b8363316724b387ada2723c921266ae79b4"
)
EXPECTED_CLASSIFIER_DEPS_BYTES = 1_536
EXPECTED_RUNTIME_CONFIG_SHA256 = (
    "c230a317a54dd960bcbeb5f347f52e18dc665a26f7efda2159fced9a5ac7e097"
)
EXPECTED_RUNTIME_CONFIG_BYTES = 342
FORBIDDEN_DOTNET_ENVIRONMENT = (
    "DOTNET_STARTUP_HOOKS",
    "DOTNET_ADDITIONAL_DEPS",
    "DOTNET_SHARED_STORE",
    "DOTNET_HOST_PATH",
    "DOTNET_ROOT",
    "DOTNET_ROOT_X64",
    "DOTNET_ROOT_X86",
    "DOTNET_ROOT(x86)",
    "DOTNET_MULTILEVEL_LOOKUP",
    "DOTNET_ROLL_FORWARD",
    "DOTNET_ROLL_FORWARD_ON_NO_CANDIDATE_FX",
    "DOTNET_ROLL_FORWARD_TO_PRERELEASE",
    "DOTNET_BUNDLE_EXTRACT_BASE_DIR",
)
CLASSIFIER_TIMEOUT_SECONDS = 86_400
MAX_JSON_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_JSONL_ROW_BYTES = 4 * 1024 * 1024
MAX_EXECUTION_LOG_BYTES = 64 * 1024
MAX_HISTORY_ROOT_RECORDS = 1_000_000
CHILD_ID_DOMAIN = "omega-g6-terminal-safe-child-v1"
TRANSCRIPT_HASH_DOMAIN = "omega-g6-terminal-preclassification-transcript-v1"
OFFICIAL_INITIAL_OFEN = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/"
    "CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
)

PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
TERMINAL_CLASSES = (
    "checkmate",
    "stalemate",
    "draw-repetition",
    "draw-halfmove",
    "draw-insufficient",
)
INVALID_CLASSES = ("illegal", "replay-failure")
CLASSIFICATIONS = (
    "root-nonterminal",
    "child-nonterminal",
    *TERMINAL_CLASSES,
    *INVALID_CLASSES,
)

_TIMESTAMP = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"\.(?P<fraction>\d{6})Z$"
)
_DOTNET_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400

IDENTITY_FIELDS = frozenset({"path", "bytes", "sha256"})
NATIVE_IDENTITY_FIELDS = frozenset({"bytes", "sha256"})
RELATIVE_IDENTITY_FIELDS = frozenset({"relativePath", "bytes", "sha256"})

CLAIM_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "historyRoots",
        "historyRootsManifest",
        "classifierBundleManifest",
        "classifierExecutable",
        "classifierRunner",
        "classifierRuntimeManifest",
        "chessLibAssembly",
        "producer",
        "plannedNativeManifestPath",
        "plannedTranscriptPath",
        "plannedEligibleRootsPath",
        "plannedEligibleChildrenPath",
        "plannedStdoutPath",
        "plannedStderrPath",
        "invocation",
        "targetRowsDecodedAtClaim",
        "targetFieldsDecodedAtClaim",
        "resultInformationRead",
    }
)
CLAIM_IDENTITY_FIELDS = (
    "historyRoots",
    "historyRootsManifest",
    "classifierBundleManifest",
    "classifierExecutable",
    "classifierRunner",
    "classifierRuntimeManifest",
    "chessLibAssembly",
    "producer",
)
CLAIM_PLANNED_FIELDS = (
    "plannedNativeManifestPath",
    "plannedTranscriptPath",
    "plannedEligibleRootsPath",
    "plannedEligibleChildrenPath",
    "plannedStdoutPath",
    "plannedStderrPath",
)

LINEAGE_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "claim",
        "nativeManifest",
        "transcript",
        "eligibleRoots",
        "eligibleChildren",
        "classifierStdout",
        "classifierStderr",
        "execution",
        "coverage",
        "classificationCounts",
        "transcriptOrderSha256",
        "eligibleRootOrderSha256",
        "eligibleChildOrderSha256",
        "terminalChildrenExcludedBeforeRouting",
        "unclassifiedChildren",
        "errorTextAcceptedAsTerminal",
        "targetRowsDecodedAtCompletion",
        "targetFieldsDecodedAtCompletion",
        "resultInformationRead",
        "finalStageSeal",
    }
)

COVERAGE_FIELDS = frozenset(
    {
        "sourceRecords",
        "transcriptRecords",
        "acceptedRoots",
        "rejectedRoots",
        "rootTerminalRejections",
        "historyInvalidRejections",
        "childGateRejectedRoots",
        "classifiedChildren",
        "terminalChildren",
        "invalidChildren",
        "childNonterminalChildren",
        "eligibleRoots",
        "eligibleChildren",
        "safeSiblingChildrenExcludedByAtomicRootPolicy",
        "childrenExcludedByAtomicRootPolicy",
    }
)

NATIVE_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "policy",
        "coverage",
        "input",
        "transcript",
        "eligibleRoots",
        "eligibleChildren",
        "runtime",
    }
)
NATIVE_POLICY_FIELDS = frozenset(
    {
        "variant",
        "replay",
        "orderedSemantics",
        "drawForRepetition",
        "senpaiOfenCompatibility",
        "eligibility",
    }
)
NATIVE_COMPATIBILITY_FIELDS = frozenset(
    {
        "exactKingsPerSide",
        "forbiddenPawnLocations",
        "minimumHalfmoveClock",
        "minimumFullmoveNumber",
        "validationPoints",
    }
)
NATIVE_COVERAGE_FIELDS = frozenset(
    {
        "sourceRecords",
        "acceptedRoots",
        "rejectedRoots",
        "eligibleChildren",
        "classificationCounts",
    }
)
NATIVE_RUNTIME_FIELDS = frozenset(
    {
        "framework",
        "expectedRuntimeVersion",
        "expectedRuntimeBundleSha256",
        "expectedRuntimeManifestSha256",
        "expectedDotnetHostSha256",
        "expectedChessLibSha256",
        "expectedNewtonsoftJsonSha256",
        "expectedSystemIoPortsSha256",
        "executionClosure",
    }
)

COMMON_EXECUTION_CLOSURE_FIELDS = frozenset(
    {
        "runtimeVersion",
        "runtimeBundleSha256",
        "runtimeFilesVerified",
        "forbiddenEnvironmentVariablesChecked",
        "forbiddenEnvironmentVariablesPresent",
        "dotnetHost",
        "runtimeManifest",
        "coreLibraryAssembly",
        "chessLibAssembly",
        "newtonsoftJsonAssembly",
        "systemIoPortsAssembly",
    }
)
SAMPLER_EXECUTION_CLOSURE_FIELDS = frozenset(
    {*COMMON_EXECUTION_CLOSURE_FIELDS, "samplerAssembly"}
)
CLASSIFIER_EXECUTION_CLOSURE_FIELDS = frozenset(
    {*COMMON_EXECUTION_CLOSURE_FIELDS, "classifierAssembly"}
)

HISTORY_ROOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "groupId",
        "initialOfen",
        "moves",
        "plyOfenSha256",
    }
)

TRANSCRIPT_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "groupId",
        "teacherEligible",
        "rejection",
        "history",
        "root",
        "children",
        "transcriptSha256",
    }
)
HISTORY_FIELDS = frozenset(
    {
        "initialOfen",
        "initialOfenSha256",
        "moves",
        "expectedPlyOfenSha256",
        "observedPlyOfenSha256",
        "verifiedPlies",
        "failurePly",
        "failureMove",
        "failureCode",
    }
)
ROOT_POSITION_FIELDS = frozenset(
    {
        "ofen",
        "ofenSha256",
        "classification",
        "sideToMove",
        "halfmoveClock",
        "repetitionCount",
        "legalMoveCount",
    }
)
CHILD_TRANSCRIPT_FIELDS = frozenset(
    {
        "ordinal",
        "move",
        "ofen",
        "ofenSha256",
        "classification",
        "failureCode",
        "sideToMove",
        "halfmoveClock",
        "repetitionCount",
        "legalMoveCount",
    }
)
ELIGIBLE_ROOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "groupId",
        "initialOfen",
        "moves",
        "plyOfenSha256",
        "rootOfen",
        "rootOfenSha256",
        "preclassificationTranscriptSha256",
        "legalChildCount",
    }
)
ELIGIBLE_CHILD_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "groupId",
        "initialOfen",
        "moves",
        "plyOfenSha256",
        "parentOfen",
        "parentOfenSha256",
        "childId",
        "move",
        "moveOrdinal",
        "childOfen",
        "childOfenSha256",
        "classification",
        "preclassificationTranscriptSha256",
    }
)
INVOCATION_FIELDS = frozenset(
    {
        "argv",
        "cwd",
        "environment",
        "timeoutSeconds",
        "stdinPolicy",
        "stdoutPolicy",
        "stderrPolicy",
        "expectedExitCode",
    }
)
EXECUTION_FIELDS = frozenset(
    {
        "startedUtc",
        "completedUtc",
        "invocation",
        "exitCode",
        "timedOut",
    }
)

HISTORY_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "createdUtc",
        "policy",
        "coverage",
        "runtime",
        "output",
        "finalStageSeal",
    }
)
HISTORY_POLICY_FIELDS = frozenset(
    {
        "variant",
        "officialInitialOfen",
        "sourceKind",
        "rowFieldInventory",
        "sourceRowsAreHistoryOnly",
        "phaseRoutingAuthority",
        "deterministicPrng",
        "seed",
        "trajectoryPairs",
        "independentTrajectoriesPerPair",
        "trajectoryFlavors",
        "groupProvenance",
        "workers",
        "deterministicOrdering",
        "maxPlies",
        "positionsPerPhaseAndSide",
        "captureSelectionPercent",
        "completeCoordinateHistory",
        "promotionSuffixes",
        "plyHash",
        "replay",
        "terminalRootsEmitted",
        "maximumHalfmoveClock",
        "minimumPieces",
        "minimumPiecesPerSide",
        "phasePlyWindows",
        "rulesOnly",
        "externalInputs",
        "publicationCommitPoint",
    }
)
HISTORY_COVERAGE_FIELDS = frozenset(
    {
        "records",
        "phaseCounts",
        "sideToMoveCounts",
        "trajectoryGroups",
        "independentTrajectories",
        "terminalTrajectories",
        "maxPlyReached",
        "promotionSelections",
    }
)
HISTORY_RUNTIME_FIELDS = frozenset(
    {
        "framework",
        "expectedRuntimeVersion",
        "expectedRuntimeBundleSha256",
        "expectedRuntimeManifestSha256",
        "expectedDotnetHostSha256",
        "expectedChessLibSha256",
        "expectedNewtonsoftJsonSha256",
        "expectedSystemIoPortsSha256",
        "executionClosure",
    }
)
RUNTIME_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "runtimeVersion",
        "runtimeBundleSha256",
        "runtimeFileCount",
        "dotnetExecutable",
        "dotnetRuntimeManifest",
        "coreLibraryAssembly",
        "forbiddenEnvironmentVariables",
        "environment",
        "workingDirectoryPolicy",
        "finalStageSeal",
    }
)
BUNDLE_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "buildInformationalVersion",
        "classifierAssembly",
        "classifierDepsJson",
        "classifierRuntimeConfig",
        "classifierRunner",
        "classifierRuntimeManifest",
        "chessLibAssembly",
        "newtonsoftJsonAssembly",
        "systemIoPortsAssembly",
        "producer",
        "applicationFileCount",
        "commandProtocol",
        "finalStageSeal",
    }
)
HISTORY_BUNDLE_MANIFEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "buildInformationalVersion",
        "samplerAssembly",
        "samplerDepsJson",
        "samplerRuntimeConfig",
        "chessLibAssembly",
        "newtonsoftJsonAssembly",
        "systemIoPortsAssembly",
        "samplerRunner",
        "samplerRuntimeManifest",
        "applicationFileCount",
        "finalStageSeal",
    }
)


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _safe_path(path: Path, *, regular_file: bool) -> Path:
    absolute = _lexical_absolute(path)
    chain = list(reversed(absolute.parents)) + [absolute]
    leaf_seen = False
    for item in chain:
        try:
            info = os.lstat(item)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"symlink/junction/reparse path is forbidden: {item}")
        if item == absolute:
            leaf_seen = True
            if regular_file and not stat.S_ISREG(info.st_mode):
                raise ValueError(f"artifact is not a regular file: {absolute}")
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"artifact parent is not a directory: {item}")
    if regular_file and not leaf_seen:
        raise FileNotFoundError(absolute)
    return absolute


def _safe_existing_file(path: Path) -> Path:
    return _safe_path(path, regular_file=True)


def _safe_new_file(path: Path) -> Path:
    absolute = _safe_path(path, regular_file=False)
    parent = _safe_path(absolute.parent, regular_file=False)
    if not parent.is_dir():
        raise FileNotFoundError(f"publisher parent does not exist: {parent}")
    if absolute.exists() or os.path.lexists(absolute):
        raise FileExistsError(absolute)
    return absolute


def _safe_planned_file(path: Path, *, require_absent: bool) -> Path:
    absolute = _safe_path(path, regular_file=False)
    parent = _safe_path(absolute.parent, regular_file=False)
    if not parent.is_dir():
        raise FileNotFoundError(f"planned artifact parent does not exist: {parent}")
    if absolute.exists() or os.path.lexists(absolute):
        if require_absent:
            raise FileExistsError(absolute)
        return _safe_existing_file(absolute)
    return absolute


def _same_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and getattr(left, "st_mtime_ns", None)
        == getattr(right, "st_mtime_ns", None)
    )


def _snapshot_file(
    path: Path, *, max_bytes: int = MAX_JSON_DOCUMENT_BYTES
) -> tuple[dict[str, Any], bytes]:
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("snapshot byte ceiling must be a nonnegative integer")
    safe = _safe_existing_file(path)
    before = os.lstat(safe)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    descriptor = os.open(safe, flags)
    payload = bytearray()
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError(
                f"opened artifact is not one regular non-reparse file: {safe}"
            )
        if not _same_snapshot(before, opened):
            raise ValueError(f"artifact changed before descriptor open: {safe}")
        if opened.st_size > max_bytes:
            raise ValueError(
                f"artifact exceeds frozen {max_bytes}-byte snapshot ceiling: {safe}"
            )
        while True:
            block = os.read(descriptor, min(1024 * 1024, max_bytes + 1))
            if not block:
                break
            payload.extend(block)
            if len(payload) > max_bytes:
                raise ValueError(
                    f"artifact exceeds frozen {max_bytes}-byte snapshot ceiling: {safe}"
                )
            digest.update(block)
        after_open = os.fstat(descriptor)
        if not _same_snapshot(opened, after_open):
            raise ValueError(f"artifact changed while read: {safe}")
    finally:
        os.close(descriptor)
    _safe_existing_file(safe)
    after_path = os.lstat(safe)
    if not _same_snapshot(before, after_path):
        raise ValueError(f"artifact path changed during snapshot: {safe}")
    identity = {
        "path": str(safe),
        "bytes": len(payload),
        "sha256": digest.hexdigest(),
    }
    return identity, bytes(payload)


def _identity(path: Path) -> dict[str, Any]:
    """Hash one stable descriptor without retaining the artifact in memory."""

    safe = _safe_existing_file(path)
    before = os.lstat(safe)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    descriptor = os.open(safe, flags)
    digest = hashlib.sha256()
    byte_count = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError(
                f"opened artifact is not one regular non-reparse file: {safe}"
            )
        if not _same_snapshot(before, opened):
            raise ValueError(f"artifact changed before descriptor open: {safe}")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            byte_count += len(block)
            digest.update(block)
        after_open = os.fstat(descriptor)
        if not _same_snapshot(opened, after_open) or byte_count != opened.st_size:
            raise ValueError(f"artifact changed while hashed: {safe}")
    finally:
        os.close(descriptor)
    _safe_existing_file(safe)
    after_path = os.lstat(safe)
    if not _same_snapshot(before, after_path):
        raise ValueError(f"artifact path changed during hashing: {safe}")
    return {
        "path": str(safe),
        "bytes": byte_count,
        "sha256": digest.hexdigest(),
    }


def _identity_with_inode(path: Path) -> tuple[dict[str, Any], tuple[int, int]]:
    safe = _safe_existing_file(path)
    before = os.lstat(safe)
    identity = _identity(safe)
    after = os.lstat(safe)
    if not _same_snapshot(before, after):
        raise ValueError(f"artifact changed while inode was read: {safe}")
    return identity, (after.st_dev, after.st_ino)


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _exclusive_bytes(path: Path, payload: bytes) -> dict[str, Any]:
    safe = _safe_new_file(path)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(safe, flags, 0o644)
    opened: os.stat_result | None = None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError("exclusive publisher did not create one regular file")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        completed = os.fstat(descriptor)
        if (
            completed.st_dev != opened.st_dev
            or completed.st_ino != opened.st_ino
            or completed.st_size != len(payload)
            or getattr(completed, "st_nlink", 1) != 1
        ):
            raise ValueError("published descriptor identity changed")
        os.close(descriptor)
        descriptor = -1
        named = os.lstat(_safe_existing_file(safe))
        if not _same_snapshot(completed, named):
            raise ValueError("published path no longer names its descriptor")
        identity, actual = _snapshot_file(safe)
        if actual != payload:
            raise ValueError("published bytes changed after close")
        return identity
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            named = os.lstat(safe)
            if (
                opened is not None
                and named.st_dev == opened.st_dev
                and named.st_ino == opened.st_ino
                and stat.S_ISREG(named.st_mode)
            ):
                safe.unlink()
        except OSError:
            pass
        raise


def _exclusive_json(path: Path, value: Any) -> dict[str, Any]:
    return _exclusive_bytes(path, _canonical_json(value))


def _delete_if_identity(path: Path, expected: Mapping[str, Any]) -> None:
    """Retain every failed publication for inspection.

    A path can be replaced between an identity check and an unlink.  There is
    no portable descriptor-relative unlink in the supported Python/Windows
    runtime, so rollback deliberately never removes a named artifact.  This is
    stricter than a best-effort cleanup: no concurrent replacement can cause
    evidence owned by another process to be deleted.
    """

    del path, expected


def _strict_json_loads(text: str, *, location: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{location}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError(f"{location}: non-finite JSON number {value!r}")

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f"{location}: invalid JSON: {error}") from error


def _load_json(path: Path, description: str) -> dict[str, Any]:
    safe, payload = _snapshot_file(path, max_bytes=MAX_JSON_DOCUMENT_BYTES)
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError(f"{description} is not strict UTF-8") from error
    value = _strict_json_loads(text, location=str(safe["path"]))
    if type(value) is not dict:
        raise ValueError(f"{description} is not a JSON object")
    return value


@contextmanager
def _iter_jsonl(
    path: Path,
    description: str,
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> Iterator[Iterator[dict[str, Any]]]:
    """Yield strict rows from one stable descriptor with bounded row memory.

    The descriptor and its named path are checked before and after iteration.
    Individual rows have a frozen ceiling because a JSON object must necessarily
    be resident while its schema is checked; total artifact size is unbounded.
    """

    safe = _safe_existing_file(path)
    before = os.lstat(safe)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    descriptor = os.open(safe, flags)
    stream = None
    opened: os.stat_result | None = None
    exhausted = False
    digest = hashlib.sha256()
    byte_count = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
        ):
            raise ValueError(
                f"opened artifact is not one regular non-reparse file: {safe}"
            )
        if not _same_snapshot(before, opened):
            raise ValueError(f"artifact changed before descriptor open: {safe}")
        stream = os.fdopen(descriptor, "rb", closefd=False)

        def rows() -> Iterator[dict[str, Any]]:
            nonlocal byte_count, exhausted
            number = 0
            while True:
                payload = stream.readline(MAX_JSONL_ROW_BYTES + 1)
                if not payload:
                    exhausted = True
                    break
                number += 1
                if len(payload) > MAX_JSONL_ROW_BYTES:
                    raise ValueError(
                        f"{safe}:{number}: row exceeds frozen "
                        f"{MAX_JSONL_ROW_BYTES}-byte ceiling"
                    )
                if not payload.endswith(b"\n"):
                    raise ValueError(
                        f"{description} must be newline-terminated"
                    )
                byte_count += len(payload)
                digest.update(payload)
                try:
                    line = payload.decode("utf-8", errors="strict")
                except UnicodeError as error:
                    raise ValueError(
                        f"{safe}:{number}: row is not strict UTF-8"
                    ) from error
                if not line.strip():
                    raise ValueError(
                        f"{safe}:{number}: blank row is forbidden"
                    )
                value = _strict_json_loads(
                    line, location=f"{safe}:{number}"
                )
                if type(value) is not dict:
                    raise ValueError(f"{safe}:{number}: expected object")
                yield value
            if number == 0:
                raise ValueError(
                    f"{description} must be nonempty and newline-terminated"
                )

        iterator = rows()
        yield iterator
        if not exhausted:
            # All production callers consume to EOF.  Reject accidental partial
            # validation instead of silently blessing an unread suffix.
            raise ValueError(f"{description} validation did not reach EOF")
        after_open = os.fstat(descriptor)
        if not _same_snapshot(opened, after_open):
            raise ValueError(f"artifact changed while streamed: {safe}")
        if expected_identity is not None:
            actual_identity = {
                "path": str(safe),
                "bytes": byte_count,
                "sha256": digest.hexdigest(),
            }
            if dict(expected_identity) != actual_identity:
                raise ValueError(
                    f"{description} streamed identity differs from its authority"
                )
    finally:
        if stream is not None:
            stream.close()
        os.close(descriptor)
        _safe_existing_file(safe)
        after_path = os.lstat(safe)
        if opened is not None and not _same_snapshot(before, after_path):
            raise ValueError(f"artifact path changed during stream: {safe}")


def _load_jsonl(path: Path, description: str) -> list[dict[str, Any]]:
    with _iter_jsonl(path, description) as rows:
        return list(rows)


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], where: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{where} fields changed; missing={missing}, extra={extra}")


def _parse_timestamp(value: Any, where: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{where} must be a timestamp string")
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise ValueError(
            f"{where} must be RFC3339 UTC with exactly six fractional digits"
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as error:
        raise ValueError(f"{where} is not a valid UTC timestamp") from error
    return parsed.replace(tzinfo=timezone.utc)


def _parse_dotnet_timestamp(value: Any, where: str) -> str:
    if type(value) is not str or _DOTNET_TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{where} is not a strict .NET UTC timestamp")
    base, _, fraction = value[:-1].partition(".")
    normalized = base + "." + (fraction + "000000")[:6] + "Z"
    try:
        datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as error:
        raise ValueError(f"{where} is not a valid UTC timestamp") from error
    return value


def _require_sha(value: Any, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{where} must be lowercase SHA-256")
    return value


def _require_nonempty_string(value: Any, where: str) -> str:
    if type(value) is not str or not value or "\0" in value:
        raise ValueError(f"{where} must be a nonempty NUL-free string")
    return value


def _require_ascii(value: Any, where: str) -> str:
    result = _require_nonempty_string(value, where)
    if not result.isascii():
        raise ValueError(f"{where} must be ASCII")
    return result


def _require_int(value: Any, where: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{where} must be an integer >= {minimum}")
    return value


def _verify_identity_record(value: Any, where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{where} identity is not an object")
    _exact_keys(value, IDENTITY_FIELDS, f"{where} identity")
    if type(value["path"]) is not str or not Path(value["path"]).is_absolute():
        raise ValueError(f"{where} identity path is not absolute")
    _require_int(value["bytes"], f"{where} identity bytes")
    _require_sha(value["sha256"], f"{where} identity sha256")
    actual = _identity(Path(value["path"]))
    if value != actual:
        raise ValueError(f"{where} identity differs from current bytes")
    return actual


def _require_pinned_identity(
    value: Any,
    where: str,
    *,
    expected_bytes: int,
    expected_sha256: str,
) -> dict[str, Any]:
    identity = _verify_identity_record(value, where)
    if (
        identity["bytes"] != expected_bytes
        or identity["sha256"] != expected_sha256
    ):
        raise ValueError(f"{where} pinned identity changed")
    return identity


def _verify_relative_identity(
    value: Any,
    manifest_path: Path,
    where: str,
    *,
    expected_relative_path: str,
    expected_bytes: int,
    expected_sha256: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{where} relative identity is not an object")
    _exact_keys(value, RELATIVE_IDENTITY_FIELDS, f"{where} relative identity")
    relative = value["relativePath"]
    if (
        type(relative) is not str
        or relative != expected_relative_path
        or not relative
        or "\0" in relative
        or "\\" in relative
        or Path(relative).is_absolute()
    ):
        raise ValueError(f"{where} relative path changed")
    _require_int(value["bytes"], f"{where} relative identity bytes")
    _require_sha(value["sha256"], f"{where} relative identity sha256")
    resolved = _lexical_absolute(manifest_path.parent / relative)
    actual = _identity(resolved)
    if (
        value["bytes"] != expected_bytes
        or value["sha256"] != expected_sha256
        or actual["bytes"] != expected_bytes
        or actual["sha256"] != expected_sha256
    ):
        raise ValueError(f"{where} pinned relative identity changed")
    return actual


def _native_identity(value: Any, where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{where} native identity is not an object")
    _exact_keys(value, NATIVE_IDENTITY_FIELDS, f"{where} native identity")
    _require_int(value["bytes"], f"{where} native bytes")
    _require_sha(value["sha256"], f"{where} native sha256")
    return dict(value)


def _identity_without_path(value: Mapping[str, Any]) -> dict[str, Any]:
    return {"bytes": value["bytes"], "sha256": value["sha256"]}


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(_lexical_absolute(path))))


def _assert_distinct_existing_roles(
    roles: Mapping[str, Path], description: str
) -> dict[str, dict[str, Any]]:
    path_keys: dict[str, str] = {}
    inode_keys: dict[tuple[int, int], str] = {}
    identities: dict[str, dict[str, Any]] = {}
    for role, path in roles.items():
        identity, inode = _identity_with_inode(path)
        key = _path_key(Path(identity["path"]))
        if key in path_keys:
            raise ValueError(
                f"{description} roles {path_keys[key]!r} and {role!r} share a path"
            )
        if inode in inode_keys:
            raise ValueError(
                f"{description} roles {inode_keys[inode]!r} and {role!r} share an inode"
            )
        path_keys[key] = role
        inode_keys[inode] = role
        identities[role] = identity
    return identities


def _assert_distinct_lexical_roles(
    existing: Mapping[str, Path], planned: Mapping[str, Path], description: str
) -> None:
    seen: dict[str, str] = {}
    for role, path in (*existing.items(), *planned.items()):
        key = _path_key(path)
        if key in seen:
            raise ValueError(
                f"{description} roles {seen[key]!r} and {role!r} share a path"
            )
        seen[key] = role


def classifier_command_protocol() -> dict[str, Any]:
    return {
        "argv": [
            "dotnetExecutable",
            "classifierAssembly",
            "--input",
            "historyRoots",
            "--transcript",
            "transcript",
            "--eligible-roots",
            "eligibleRoots",
            "--eligible-children",
            "eligibleChildren",
            "--manifest",
            "nativeManifest",
        ],
        "cwd": "classifier-assembly-parent",
        "environment": {},
        "timeoutSeconds": CLASSIFIER_TIMEOUT_SECONDS,
        "stdinPolicy": "closed",
        "stdoutPolicy": "captured-strict-utf8-to-preclaimed-file",
        "stderrPolicy": "captured-to-preclaimed-file-and-must-be-empty",
        "expectedExitCode": 0,
    }


def _invocation_document(
    *,
    runner: Path,
    classifier: Path,
    history_roots: Path,
    transcript: Path,
    eligible_roots: Path,
    eligible_children: Path,
    native_manifest: Path,
) -> dict[str, Any]:
    return {
        "argv": [
            str(_lexical_absolute(runner)),
            str(_lexical_absolute(classifier)),
            "--input",
            str(_lexical_absolute(history_roots)),
            "--transcript",
            str(_lexical_absolute(transcript)),
            "--eligible-roots",
            str(_lexical_absolute(eligible_roots)),
            "--eligible-children",
            str(_lexical_absolute(eligible_children)),
            "--manifest",
            str(_lexical_absolute(native_manifest)),
        ],
        "cwd": str(_lexical_absolute(classifier).parent),
        "environment": {},
        "timeoutSeconds": CLASSIFIER_TIMEOUT_SECONDS,
        "stdinPolicy": "closed",
        "stdoutPolicy": "captured-strict-utf8-to-preclaimed-file",
        "stderrPolicy": "captured-to-preclaimed-file-and-must-be-empty",
        "expectedExitCode": 0,
    }


def _verify_invocation(value: Any, where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{where} is not an object")
    _exact_keys(value, INVOCATION_FIELDS, where)
    argv = value["argv"]
    if type(argv) is not list or any(type(item) is not str for item in argv):
        raise ValueError(f"{where}.argv must be a string array")
    if type(value["cwd"]) is not str or not Path(value["cwd"]).is_absolute():
        raise ValueError(f"{where}.cwd must be absolute")
    if (
        type(value["environment"]) is not dict
        or value["environment"] != {}
        or type(value["timeoutSeconds"]) is not int
        or value["timeoutSeconds"] != CLASSIFIER_TIMEOUT_SECONDS
        or value["stdinPolicy"] != "closed"
        or value["stdoutPolicy"]
        != "captured-strict-utf8-to-preclaimed-file"
        or value["stderrPolicy"]
        != "captured-to-preclaimed-file-and-must-be-empty"
        or type(value["expectedExitCode"]) is not int
        or value["expectedExitCode"] != 0
    ):
        raise ValueError(f"{where} policy changed")
    return dict(value)


def _verify_dotnet_bundle_manifest(
    path: Path,
    *,
    dotnet: Mapping[str, Any],
    core_library: Mapping[str, Any],
) -> None:
    document = _load_json(path, "frozen .NET runtime inventory")
    _exact_keys(
        document,
        frozenset(
            {
                "schemaVersion",
                "kind",
                "root",
                "runtimeVersion",
                "includedTrees",
                "excludedTrees",
                "dotnetHostRelativePath",
                "files",
                "bundleSha256",
            }
        ),
        "frozen .NET runtime inventory",
    )
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != 1
        or document["kind"]
        != "omega-nnue-king-state-v5-dotnet-runtime-bundle"
        or document["root"]
        != "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime"
        or document["runtimeVersion"] != EXPECTED_RUNTIME_VERSION
        or document["includedTrees"]
        != ["host/fxr/10.0.9", "shared/Microsoft.NETCore.App/10.0.9"]
        or document["excludedTrees"]
        != ["sdk", "sdk-manifests", "packs", "templates"]
        or document["dotnetHostRelativePath"] != "dotnet.exe"
        or document["bundleSha256"] != EXPECTED_RUNTIME_BUNDLE_SHA256
        or type(document["files"]) is not list
        or len(document["files"]) != EXPECTED_RUNTIME_FILE_COUNT
    ):
        raise ValueError("frozen .NET runtime inventory header changed")
    entries: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(document["files"]):
        where = f"frozen .NET runtime inventory files[{index}]"
        if type(entry) is not dict:
            raise ValueError(f"{where} is not an object")
        _exact_keys(
            entry,
            frozenset({"relativePath", "bytes", "sha256"}),
            where,
        )
        relative = _require_ascii(entry["relativePath"], f"{where}.relativePath")
        if (
            relative in entries
            or "\\" in relative
            or Path(relative).is_absolute()
            or any(part in ("", ".", "..") for part in relative.split("/"))
        ):
            raise ValueError(f"{where} path is not canonical and unique")
        _require_int(entry["bytes"], f"{where}.bytes")
        _require_sha(entry["sha256"], f"{where}.sha256")
        entries[relative] = entry
    expected_critical = {
        "dotnet.exe": _identity_without_path(dotnet),
        "shared/Microsoft.NETCore.App/10.0.9/System.Private.CoreLib.dll": (
            _identity_without_path(core_library)
        ),
    }
    for relative, expected in expected_critical.items():
        entry = entries.get(relative)
        if entry is None or {
            "bytes": entry["bytes"],
            "sha256": entry["sha256"],
        } != expected:
            raise ValueError(
                f"frozen .NET runtime inventory critical file changed: {relative}"
            )


def parse_classifier_runtime_manifest(path: Path) -> dict[str, Any]:
    manifest_path = _safe_existing_file(path)
    document = _load_json(manifest_path, "terminal classifier runtime manifest")
    _exact_keys(
        document,
        RUNTIME_MANIFEST_FIELDS,
        "terminal classifier runtime manifest",
    )
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != RUNTIME_MANIFEST_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "frozen-dotnet-runtime-closure"
        or document["runtimeVersion"] != EXPECTED_RUNTIME_VERSION
        or document["runtimeBundleSha256"]
        != EXPECTED_RUNTIME_BUNDLE_SHA256
        or type(document["runtimeFileCount"]) is not int
        or document["runtimeFileCount"] != EXPECTED_RUNTIME_FILE_COUNT
        or document["forbiddenEnvironmentVariables"]
        != list(FORBIDDEN_DOTNET_ENVIRONMENT)
        or type(document["environment"]) is not dict
        or document["environment"] != {}
        or document["workingDirectoryPolicy"]
        != "application-assembly-parent"
        or document["finalStageSeal"] is not True
    ):
        raise ValueError("terminal classifier runtime manifest changed")
    dotnet = _verify_relative_identity(
        document["dotnetExecutable"],
        manifest_path,
        "terminal runtime dotnet executable",
        expected_relative_path="../../king-state-v5/dotnet-runtime/dotnet.exe",
        expected_bytes=EXPECTED_DOTNET_BYTES,
        expected_sha256=EXPECTED_DOTNET_SHA256,
    )
    inventory = _verify_relative_identity(
        document["dotnetRuntimeManifest"],
        manifest_path,
        "terminal runtime inventory manifest",
        expected_relative_path="../../king-state-v5/dotnet-runtime.manifest.json",
        expected_bytes=EXPECTED_RUNTIME_MANIFEST_BYTES,
        expected_sha256=EXPECTED_RUNTIME_MANIFEST_SHA256,
    )
    core_library = _verify_relative_identity(
        document["coreLibraryAssembly"],
        manifest_path,
        "terminal runtime core library",
        expected_relative_path=(
            "../../king-state-v5/dotnet-runtime/shared/"
            "Microsoft.NETCore.App/10.0.9/System.Private.CoreLib.dll"
        ),
        expected_bytes=EXPECTED_CORELIB_BYTES,
        expected_sha256=EXPECTED_CORELIB_SHA256,
    )
    _verify_dotnet_bundle_manifest(
        Path(inventory["path"]), dotnet=dotnet, core_library=core_library
    )
    _assert_distinct_existing_roles(
        {
            "dotnetExecutable": Path(dotnet["path"]),
            "dotnetRuntimeManifest": Path(inventory["path"]),
            "coreLibraryAssembly": Path(core_library["path"]),
        },
        "terminal classifier runtime manifest",
    )
    return {
        **document,
        "dotnetExecutable": dotnet,
        "dotnetRuntimeManifest": inventory,
        "coreLibraryAssembly": core_library,
    }


def parse_classifier_bundle_manifest(path: Path) -> dict[str, Any]:
    manifest_path = _safe_existing_file(path)
    document = _load_json(manifest_path, "terminal classifier bundle manifest")
    _exact_keys(
        document,
        BUNDLE_MANIFEST_FIELDS,
        "terminal classifier bundle manifest",
    )
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != BUNDLE_MANIFEST_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "frozen-reviewed-terminal-classifier"
        or document["buildInformationalVersion"]
        != "1.0.0+omega-g6-terminal-preclassifier-v1"
        or type(document["applicationFileCount"]) is not int
        or document["applicationFileCount"] != 6
        or document["commandProtocol"] != classifier_command_protocol()
        or document["finalStageSeal"] is not True
    ):
        raise ValueError("terminal classifier bundle manifest changed")
    classifier = _verify_relative_identity(
        document["classifierAssembly"],
        manifest_path,
        "terminal bundle classifier assembly",
        expected_relative_path="OmegaTerminalPreclassifierG6.dll",
        expected_bytes=EXPECTED_CLASSIFIER_BYTES,
        expected_sha256=EXPECTED_CLASSIFIER_SHA256,
    )
    classifier_deps = _verify_relative_identity(
        document["classifierDepsJson"],
        manifest_path,
        "terminal bundle classifier deps.json",
        expected_relative_path="OmegaTerminalPreclassifierG6.deps.json",
        expected_bytes=EXPECTED_CLASSIFIER_DEPS_BYTES,
        expected_sha256=EXPECTED_CLASSIFIER_DEPS_SHA256,
    )
    classifier_runtime_config = _verify_relative_identity(
        document["classifierRuntimeConfig"],
        manifest_path,
        "terminal bundle classifier runtimeconfig",
        expected_relative_path="OmegaTerminalPreclassifierG6.runtimeconfig.json",
        expected_bytes=EXPECTED_RUNTIME_CONFIG_BYTES,
        expected_sha256=EXPECTED_RUNTIME_CONFIG_SHA256,
    )
    runner = _verify_relative_identity(
        document["classifierRunner"],
        manifest_path,
        "terminal bundle classifier runner",
        expected_relative_path="../../king-state-v5/dotnet-runtime/dotnet.exe",
        expected_bytes=EXPECTED_DOTNET_BYTES,
        expected_sha256=EXPECTED_DOTNET_SHA256,
    )
    chesslib = _verify_relative_identity(
        document["chessLibAssembly"],
        manifest_path,
        "terminal bundle ChessLib assembly",
        expected_relative_path="ChessLib.dll",
        expected_bytes=EXPECTED_CHESSLIB_BYTES,
        expected_sha256=EXPECTED_CHESSLIB_SHA256,
    )
    newtonsoft = _verify_relative_identity(
        document["newtonsoftJsonAssembly"],
        manifest_path,
        "terminal bundle Newtonsoft.Json assembly",
        expected_relative_path="Newtonsoft.Json.dll",
        expected_bytes=EXPECTED_NEWTONSOFT_BYTES,
        expected_sha256=EXPECTED_NEWTONSOFT_SHA256,
    )
    ports = _verify_relative_identity(
        document["systemIoPortsAssembly"],
        manifest_path,
        "terminal bundle System.IO.Ports assembly",
        expected_relative_path="System.IO.Ports.dll",
        expected_bytes=EXPECTED_SYSTEM_IO_PORTS_BYTES,
        expected_sha256=EXPECTED_SYSTEM_IO_PORTS_SHA256,
    )
    runtime_identity = _verify_relative_identity(
        document["classifierRuntimeManifest"],
        manifest_path,
        "terminal bundle runtime manifest",
        expected_relative_path="runtime.manifest.json",
        expected_bytes=1_516,
        expected_sha256=(
            "9315148a3c860c77ca46ef639bb957e9ea2b0d2fc2f3fcb3734e37961628b77a"
        ),
    )
    runtime = parse_classifier_runtime_manifest(
        Path(runtime_identity["path"])
    )
    producer_identity = _identity(Path(__file__))
    producer = _verify_relative_identity(
        document["producer"],
        manifest_path,
        "terminal bundle producer",
        expected_relative_path="../../../omega_decision_v3_terminal_lineage.py",
        expected_bytes=producer_identity["bytes"],
        expected_sha256=producer_identity["sha256"],
    )
    if producer != producer_identity:
        raise ValueError("terminal bundle producer is not this reviewed module")
    if (
        runtime["dotnetExecutable"] != runner
    ):
        raise ValueError("terminal bundle/runtime identities differ")
    _assert_distinct_existing_roles(
        {
            "classifierAssembly": Path(classifier["path"]),
            "classifierDepsJson": Path(classifier_deps["path"]),
            "classifierRuntimeConfig": Path(
                classifier_runtime_config["path"]
            ),
            "classifierRunner": Path(runner["path"]),
            "classifierRuntimeManifest": Path(runtime_identity["path"]),
            "chessLibAssembly": Path(chesslib["path"]),
            "newtonsoftJsonAssembly": Path(newtonsoft["path"]),
            "systemIoPortsAssembly": Path(ports["path"]),
            "producer": Path(producer["path"]),
        },
        "terminal classifier bundle manifest",
    )
    return {
        **document,
        "classifierAssembly": classifier,
        "classifierDepsJson": classifier_deps,
        "classifierRuntimeConfig": classifier_runtime_config,
        "classifierRunner": runner,
        "classifierRuntimeManifest": runtime_identity,
        "chessLibAssembly": chesslib,
        "newtonsoftJsonAssembly": newtonsoft,
        "systemIoPortsAssembly": ports,
        "producer": producer,
    }


def parse_history_sampler_bundle_manifest(path: Path) -> dict[str, Any]:
    manifest_path = _safe_existing_file(path)
    document = _load_json(manifest_path, "history sampler bundle manifest")
    _exact_keys(
        document,
        HISTORY_BUNDLE_MANIFEST_FIELDS,
        "history sampler bundle manifest",
    )
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != HISTORY_BUNDLE_MANIFEST_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "frozen-reviewed-history-root-sampler"
        or document["buildInformationalVersion"]
        != "1.0.0+omega-g6-history-root-sampler-v1"
        or type(document["applicationFileCount"]) is not int
        or document["applicationFileCount"] != 6
        or document["finalStageSeal"] is not True
    ):
        raise ValueError("history sampler bundle manifest changed")
    specs = {
        "samplerAssembly": (
            "OmegaHistoryRootSamplerG6.dll",
            EXPECTED_SAMPLER_BYTES,
            EXPECTED_SAMPLER_SHA256,
        ),
        "samplerDepsJson": (
            "OmegaHistoryRootSamplerG6.deps.json",
            EXPECTED_SAMPLER_DEPS_BYTES,
            EXPECTED_SAMPLER_DEPS_SHA256,
        ),
        "samplerRuntimeConfig": (
            "OmegaHistoryRootSamplerG6.runtimeconfig.json",
            EXPECTED_RUNTIME_CONFIG_BYTES,
            EXPECTED_RUNTIME_CONFIG_SHA256,
        ),
        "chessLibAssembly": (
            "ChessLib.dll",
            EXPECTED_CHESSLIB_BYTES,
            EXPECTED_CHESSLIB_SHA256,
        ),
        "newtonsoftJsonAssembly": (
            "Newtonsoft.Json.dll",
            EXPECTED_NEWTONSOFT_BYTES,
            EXPECTED_NEWTONSOFT_SHA256,
        ),
        "systemIoPortsAssembly": (
            "System.IO.Ports.dll",
            EXPECTED_SYSTEM_IO_PORTS_BYTES,
            EXPECTED_SYSTEM_IO_PORTS_SHA256,
        ),
        "samplerRunner": (
            "../../king-state-v5/dotnet-runtime/dotnet.exe",
            EXPECTED_DOTNET_BYTES,
            EXPECTED_DOTNET_SHA256,
        ),
        "samplerRuntimeManifest": (
            "runtime.manifest.json",
            1_516,
            "9315148a3c860c77ca46ef639bb957e9ea2b0d2fc2f3fcb3734e37961628b77a",
        ),
    }
    identities = {
        field: _verify_relative_identity(
            document[field],
            manifest_path,
            f"history bundle {field}",
            expected_relative_path=relative,
            expected_bytes=size,
            expected_sha256=digest,
        )
        for field, (relative, size, digest) in specs.items()
    }
    runtime = parse_classifier_runtime_manifest(
        Path(identities["samplerRuntimeManifest"]["path"])
    )
    if runtime["dotnetExecutable"] != identities["samplerRunner"]:
        raise ValueError("history sampler bundle/runtime identities differ")
    _assert_distinct_existing_roles(
        {field: Path(value["path"]) for field, value in identities.items()},
        "history sampler bundle manifest",
    )
    return {**document, **identities}


def _parse_execution_closure(
    value: Any,
    where: str,
    *,
    assembly_field: str,
    expected_assembly_bytes: int,
    expected_assembly_sha256: str,
    bundle: Mapping[str, Any],
    bundle_assembly_field: str,
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    expected_fields = (
        SAMPLER_EXECUTION_CLOSURE_FIELDS
        if assembly_field == "samplerAssembly"
        else CLASSIFIER_EXECUTION_CLOSURE_FIELDS
    )
    if type(value) is not dict:
        raise ValueError(f"{where} is not an object")
    _exact_keys(value, expected_fields, where)
    if (
        value["runtimeVersion"] != EXPECTED_RUNTIME_VERSION
        or value["runtimeBundleSha256"]
        != EXPECTED_RUNTIME_BUNDLE_SHA256
        or type(value["runtimeFilesVerified"]) is not int
        or value["runtimeFilesVerified"] != EXPECTED_RUNTIME_FILE_COUNT
        or value["forbiddenEnvironmentVariablesChecked"]
        != list(FORBIDDEN_DOTNET_ENVIRONMENT)
        or type(value["forbiddenEnvironmentVariablesPresent"]) is not int
        or value["forbiddenEnvironmentVariablesPresent"] != 0
    ):
        raise ValueError(f"{where} policy changed")
    specs = {
        "dotnetHost": (EXPECTED_DOTNET_BYTES, EXPECTED_DOTNET_SHA256),
        "runtimeManifest": (
            EXPECTED_RUNTIME_MANIFEST_BYTES,
            EXPECTED_RUNTIME_MANIFEST_SHA256,
        ),
        "coreLibraryAssembly": (
            EXPECTED_CORELIB_BYTES,
            EXPECTED_CORELIB_SHA256,
        ),
        assembly_field: (expected_assembly_bytes, expected_assembly_sha256),
        "chessLibAssembly": (
            EXPECTED_CHESSLIB_BYTES,
            EXPECTED_CHESSLIB_SHA256,
        ),
        "newtonsoftJsonAssembly": (
            EXPECTED_NEWTONSOFT_BYTES,
            EXPECTED_NEWTONSOFT_SHA256,
        ),
        "systemIoPortsAssembly": (
            EXPECTED_SYSTEM_IO_PORTS_BYTES,
            EXPECTED_SYSTEM_IO_PORTS_SHA256,
        ),
    }
    identities = {
        field: _require_pinned_identity(
            value[field],
            f"{where}.{field}",
            expected_bytes=size,
            expected_sha256=digest,
        )
        for field, (size, digest) in specs.items()
    }
    expected_links = {
        "dotnetHost": runtime["dotnetExecutable"],
        "runtimeManifest": runtime["dotnetRuntimeManifest"],
        "coreLibraryAssembly": runtime["coreLibraryAssembly"],
        assembly_field: bundle[bundle_assembly_field],
        "chessLibAssembly": bundle["chessLibAssembly"],
        "newtonsoftJsonAssembly": bundle["newtonsoftJsonAssembly"],
        "systemIoPortsAssembly": bundle["systemIoPortsAssembly"],
    }
    for field, expected in expected_links.items():
        if identities[field] != expected:
            raise ValueError(f"{where}.{field} differs from frozen bundle")
    _assert_distinct_existing_roles(
        {field: Path(identity["path"]) for field, identity in identities.items()},
        where,
    )
    return {**value, **identities}


def _history_root_metadata(row: Mapping[str, Any], where: str) -> dict[str, Any]:
    pattern = re.compile(
        r"^(?P<group>random-pair-[0-9]{6})-(?P<flavor>ab|ba)-"
        r"(?P<phase>opening|middlegame|late|endgame)-(?P<side>w|b)-"
        r"ply-(?P<ply>[0-9]{4})-(?P<rank>[0-9a-f]{64})$"
    )
    match = pattern.fullmatch(row["rootId"])
    if match is None or match.group("group") != row["groupId"]:
        raise ValueError(f"{where} sampler root/group provenance changed")
    ply = int(match.group("ply"))
    if ply != len(row["moves"]):
        raise ValueError(f"{where} sampler root ply differs from history")
    return {
        "phase": match.group("phase"),
        "side": match.group("side"),
        "ply": ply,
        "flavor": match.group("flavor"),
    }


def _history_root_summary(
    path: Path,
    phase_windows: Mapping[str, Any],
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate sampler roots at O(root IDs) memory and O(one row) payload."""

    seen: set[str] = set()
    groups: set[str] = set()
    phase_counts: Counter[str] = Counter()
    side_counts: Counter[str] = Counter()
    records = 0
    max_ply = 0
    with _iter_jsonl(
        path,
        "terminal history roots",
        expected_identity=expected_identity,
    ) as rows:
        for number, raw in enumerate(rows, 1):
            records = number
            if records > MAX_HISTORY_ROOT_RECORDS:
                raise ValueError(
                    "terminal history roots exceed frozen record ceiling"
                )
            row = _parse_history_root_row(raw, number, seen)
            if row["initialOfen"] != OFFICIAL_INITIAL_OFEN:
                raise ValueError(
                    "history roots do not use the official initial OFEN"
                )
            metadata = _history_root_metadata(row, f"history-root row {number}")
            window = phase_windows[metadata["phase"]]
            if not window[0] <= metadata["ply"] <= window[1]:
                raise ValueError(
                    f"history-root row {number} lies outside its phase ply window"
                )
            groups.add(row["groupId"])
            phase_counts[metadata["phase"]] += 1
            side_counts[metadata["side"]] += 1
            max_ply = max(max_ply, metadata["ply"])
    return {
        "records": records,
        "groups": len(groups),
        "phaseCounts": {phase: phase_counts[phase] for phase in PHASES},
        "sideCounts": {side: side_counts[side] for side in SIDES},
        "maxPly": max_ply,
    }


def parse_history_sampler_manifest(
    path: Path, history_roots: Path
) -> dict[str, Any]:
    document = _load_json(path, "history-root sampler manifest")
    _exact_keys(document, HISTORY_MANIFEST_FIELDS, "history-root sampler manifest")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != HISTORY_MANIFEST_KIND
        or document["finalStageSeal"] is not True
    ):
        raise ValueError("history-root sampler manifest header changed")
    _parse_dotnet_timestamp(document["createdUtc"], "history sampler createdUtc")
    policy = document["policy"]
    if type(policy) is not dict:
        raise ValueError("history-root sampler policy is not an object")
    _exact_keys(policy, HISTORY_POLICY_FIELDS, "history-root sampler policy")
    fixed_policy = {
        "variant": "omega",
        "officialInitialOfen": OFFICIAL_INITIAL_OFEN,
        "sourceKind": HISTORY_ROOT_KIND,
        "rowFieldInventory": [
            "schemaVersion",
            "kind",
            "rootId",
            "groupId",
            "initialOfen",
            "moves",
            "plyOfenSha256",
        ],
        "sourceRowsAreHistoryOnly": True,
        "phaseRoutingAuthority": (
            "recompute phase from the replayed root OFEN before target-free routing"
        ),
        "deterministicPrng": "SplitMix64",
        "independentTrajectoriesPerPair": 2,
        "trajectoryFlavors": ["ab", "ba"],
        "groupProvenance": (
            "groupId is the trajectory-pair id; rootId also binds flavor, "
            "phase, side, ply, and selection rank"
        ),
        "deterministicOrdering": (
            "pair index, flavor ab/ba, phase ordinal, side ordinal, "
            "selection-rank ordinal; independent of workers"
        ),
        "completeCoordinateHistory": True,
        "promotionSuffixes": "qrbncw",
        "plyHash": (
            "lowercase SHA-256 of normalized six-field ChessLib OFEN after every ply"
        ),
        "replay": "ChessLib.Game.DoMove(move, checkEndGame: false)",
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
        "rulesOnly": True,
        "externalInputs": 0,
        "publicationCommitPoint": "manifest",
    }
    for field, expected in fixed_policy.items():
        if policy[field] != expected or type(policy[field]) is not type(expected):
            raise ValueError(f"history-root sampler policy.{field} changed")
    if (
        type(policy["seed"]) is not str
        or re.fullmatch(r"(?:0|[1-9][0-9]*)", policy["seed"]) is None
        or int(policy["seed"]) > (1 << 64) - 1
    ):
        raise ValueError("history-root sampler seed changed")
    for field, minimum, maximum in (
        ("trajectoryPairs", 1, None),
        ("workers", 1, 1024),
        ("maxPlies", 6, 4096),
        ("positionsPerPhaseAndSide", 1, None),
    ):
        value = _require_int(
            policy[field], f"history policy.{field}", minimum=minimum
        )
        if maximum is not None and value > maximum:
            raise ValueError(f"history policy.{field} exceeds its bound")
    capture = _require_int(
        policy["captureSelectionPercent"],
        "history policy.captureSelectionPercent",
    )
    if capture > 100:
        raise ValueError("history capture selection percent exceeds 100")
    output = _verify_identity_record(document["output"], "history sampler output")
    summary = _history_root_summary(
        history_roots,
        policy["phasePlyWindows"],
        expected_identity=output,
    )
    coverage = document["coverage"]
    if type(coverage) is not dict:
        raise ValueError("history-root sampler coverage is not an object")
    _exact_keys(coverage, HISTORY_COVERAGE_FIELDS, "history-root sampler coverage")
    expected_phase_counts = summary["phaseCounts"]
    expected_side_counts = summary["sideCounts"]
    if (
        type(coverage["phaseCounts"]) is not dict
        or set(coverage["phaseCounts"]) != set(PHASES)
        or any(type(coverage["phaseCounts"][phase]) is not int for phase in PHASES)
        or type(coverage["sideToMoveCounts"]) is not dict
        or set(coverage["sideToMoveCounts"]) != set(SIDES)
        or any(type(coverage["sideToMoveCounts"][side]) is not int for side in SIDES)
    ):
        raise ValueError("history-root phase/side coverage types changed")
    for field in (
        "records",
        "trajectoryGroups",
        "independentTrajectories",
        "terminalTrajectories",
        "maxPlyReached",
    ):
        _require_int(coverage[field], f"history coverage.{field}")
    if (
        coverage["records"] != summary["records"]
        or coverage["phaseCounts"] != expected_phase_counts
        or coverage["sideToMoveCounts"] != expected_side_counts
        or coverage["trajectoryGroups"] != policy["trajectoryPairs"]
        or summary["groups"] > coverage["trajectoryGroups"]
        or coverage["independentTrajectories"]
        != policy["trajectoryPairs"] * 2
        or coverage["terminalTrajectories"]
        > coverage["independentTrajectories"]
        or coverage["maxPlyReached"] < summary["maxPly"]
        or coverage["maxPlyReached"] > policy["maxPlies"]
    ):
        raise ValueError("history-root sampler coverage differs from rows/policy")
    promotions = coverage["promotionSelections"]
    if type(promotions) is not dict or set(promotions) != set("qrbncw"):
        raise ValueError("history-root promotion inventory changed")
    for suffix in "qrbncw":
        _require_int(promotions[suffix], f"history promotionSelections.{suffix}")
    runtime = document["runtime"]
    if type(runtime) is not dict:
        raise ValueError("history-root sampler runtime is not an object")
    _exact_keys(runtime, HISTORY_RUNTIME_FIELDS, "history-root sampler runtime")
    if (
        runtime["framework"] != ".NET 10.0.9"
        or runtime["expectedRuntimeVersion"] != EXPECTED_RUNTIME_VERSION
        or runtime["expectedRuntimeBundleSha256"]
        != EXPECTED_RUNTIME_BUNDLE_SHA256
        or runtime["expectedRuntimeManifestSha256"]
        != EXPECTED_RUNTIME_MANIFEST_SHA256
        or runtime["expectedDotnetHostSha256"] != EXPECTED_DOTNET_SHA256
        or runtime["expectedChessLibSha256"] != EXPECTED_CHESSLIB_SHA256
        or runtime["expectedNewtonsoftJsonSha256"]
        != EXPECTED_NEWTONSOFT_SHA256
        or runtime["expectedSystemIoPortsSha256"]
        != EXPECTED_SYSTEM_IO_PORTS_SHA256
    ):
        raise ValueError("history sampler runtime expectation changed")
    raw_closure = runtime["executionClosure"]
    if type(raw_closure) is not dict:
        raise ValueError("history sampler execution closure is not an object")
    raw_sampler = _require_pinned_identity(
        raw_closure.get("samplerAssembly"),
        "history sampler execution closure sampler",
        expected_bytes=EXPECTED_SAMPLER_BYTES,
        expected_sha256=EXPECTED_SAMPLER_SHA256,
    )
    bundle_path = Path(raw_sampler["path"]).parent / "bundle.manifest.json"
    bundle = parse_history_sampler_bundle_manifest(bundle_path)
    frozen_runtime = parse_classifier_runtime_manifest(
        Path(raw_sampler["path"]).parent / "runtime.manifest.json"
    )
    closure = _parse_execution_closure(
        raw_closure,
        "history sampler execution closure",
        assembly_field="samplerAssembly",
        expected_assembly_bytes=EXPECTED_SAMPLER_BYTES,
        expected_assembly_sha256=EXPECTED_SAMPLER_SHA256,
        bundle=bundle,
        bundle_assembly_field="samplerAssembly",
        runtime=frozen_runtime,
    )
    _assert_distinct_existing_roles(
        {
            "historyRoots": history_roots,
            "historyManifest": path,
            "samplerAssembly": Path(closure["samplerAssembly"]["path"]),
            "chessLibAssembly": Path(closure["chessLibAssembly"]["path"]),
            "newtonsoftJsonAssembly": Path(
                closure["newtonsoftJsonAssembly"]["path"]
            ),
            "systemIoPortsAssembly": Path(
                closure["systemIoPortsAssembly"]["path"]
            ),
            "historySamplerBundle": bundle_path,
        },
        "history-root sampler authority",
    )
    return {
        **document,
        "runtime": {**runtime, "executionClosure": closure},
    }


def _validate_claim_header(document: Mapping[str, Any]) -> None:
    _exact_keys(document, CLAIM_FIELDS, "terminal claim")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != CLAIM_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"]
        != "claimed-before-terminal-run-and-before-any-teacher-target-decode"
        or type(document["targetRowsDecodedAtClaim"]) is not int
        or document["targetRowsDecodedAtClaim"] != 0
        or type(document["targetFieldsDecodedAtClaim"]) is not int
        or document["targetFieldsDecodedAtClaim"] != 0
        or document["resultInformationRead"] is not False
    ):
        raise ValueError("terminal claim header changed")
    _parse_timestamp(document["createdUtc"], "terminal claim createdUtc")
    _verify_invocation(document["invocation"], "terminal claim invocation")


def terminal_claim_document(
    *,
    history_roots: Path,
    history_roots_manifest: Path,
    classifier_bundle_manifest: Path,
    classifier_executable: Path,
    classifier_runner: Path,
    classifier_runtime_manifest: Path,
    chesslib_assembly: Path,
    producer: Path,
    planned_native_manifest: Path,
    planned_transcript: Path,
    planned_eligible_roots: Path,
    planned_eligible_children: Path,
    planned_stdout: Path,
    planned_stderr: Path,
    created_utc: str,
) -> dict[str, Any]:
    _parse_timestamp(created_utc, "terminal claim createdUtc")
    existing = {
        "historyRoots": history_roots,
        "historyRootsManifest": history_roots_manifest,
        "classifierBundleManifest": classifier_bundle_manifest,
        "classifierExecutable": classifier_executable,
        "classifierRunner": classifier_runner,
        "classifierRuntimeManifest": classifier_runtime_manifest,
        "chessLibAssembly": chesslib_assembly,
        "producer": producer,
    }
    identities = _assert_distinct_existing_roles(existing, "terminal claim")
    history_path = Path(identities["historyRoots"]["path"])
    parse_history_sampler_manifest(
        Path(identities["historyRootsManifest"]["path"]), history_path
    )
    runtime = parse_classifier_runtime_manifest(
        Path(identities["classifierRuntimeManifest"]["path"])
    )
    bundle = parse_classifier_bundle_manifest(
        Path(identities["classifierBundleManifest"]["path"])
    )
    expected_links = {
        "classifierAssembly": identities["classifierExecutable"],
        "classifierRunner": identities["classifierRunner"],
        "classifierRuntimeManifest": identities["classifierRuntimeManifest"],
        "chessLibAssembly": identities["chessLibAssembly"],
        "producer": identities["producer"],
    }
    for field, expected in expected_links.items():
        if bundle[field] != expected:
            raise ValueError(f"terminal classifier bundle {field} differs from claim")
    if runtime["dotnetExecutable"] != identities["classifierRunner"]:
        raise ValueError("terminal classifier runtime differs from claim")
    _require_pinned_identity(
        identities["classifierExecutable"],
        "claimed terminal classifier",
        expected_bytes=EXPECTED_CLASSIFIER_BYTES,
        expected_sha256=EXPECTED_CLASSIFIER_SHA256,
    )
    _require_pinned_identity(
        identities["classifierRunner"],
        "claimed terminal dotnet runner",
        expected_bytes=EXPECTED_DOTNET_BYTES,
        expected_sha256=EXPECTED_DOTNET_SHA256,
    )
    _require_pinned_identity(
        identities["chessLibAssembly"],
        "claimed terminal ChessLib",
        expected_bytes=EXPECTED_CHESSLIB_BYTES,
        expected_sha256=EXPECTED_CHESSLIB_SHA256,
    )
    if identities["producer"] != _identity(Path(__file__)):
        raise ValueError("claimed terminal producer is not this reviewed module")
    planned = {
        "plannedNativeManifestPath": _safe_planned_file(
            planned_native_manifest, require_absent=True
        ),
        "plannedTranscriptPath": _safe_planned_file(
            planned_transcript, require_absent=True
        ),
        "plannedEligibleRootsPath": _safe_planned_file(
            planned_eligible_roots, require_absent=True
        ),
        "plannedEligibleChildrenPath": _safe_planned_file(
            planned_eligible_children, require_absent=True
        ),
        "plannedStdoutPath": _safe_planned_file(
            planned_stdout, require_absent=True
        ),
        "plannedStderrPath": _safe_planned_file(
            planned_stderr, require_absent=True
        ),
    }
    _assert_distinct_lexical_roles(existing, planned, "terminal claim")
    invocation = _invocation_document(
        runner=Path(identities["classifierRunner"]["path"]),
        classifier=Path(identities["classifierExecutable"]["path"]),
        history_roots=history_path,
        transcript=planned["plannedTranscriptPath"],
        eligible_roots=planned["plannedEligibleRootsPath"],
        eligible_children=planned["plannedEligibleChildrenPath"],
        native_manifest=planned["plannedNativeManifestPath"],
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": CLAIM_KIND,
        "profileId": PROFILE_ID,
        "status": (
            "claimed-before-terminal-run-and-before-any-teacher-target-decode"
        ),
        "createdUtc": created_utc,
        **identities,
        **{field: str(path) for field, path in planned.items()},
        "invocation": invocation,
        "targetRowsDecodedAtClaim": 0,
        "targetFieldsDecodedAtClaim": 0,
        "resultInformationRead": False,
    }


def publish_terminal_claim(path: Path, **kwargs: Any) -> dict[str, Any]:
    document = terminal_claim_document(**kwargs)
    publication = _safe_new_file(path)
    all_paths = {
        field: Path(document[field]["path"]) for field in CLAIM_IDENTITY_FIELDS
    }
    all_paths.update(
        {field: Path(document[field]) for field in CLAIM_PLANNED_FIELDS}
    )
    _assert_distinct_lexical_roles(
        all_paths, {"claimPublication": publication}, "terminal claim publication"
    )
    published = _exclusive_json(publication, document)
    try:
        for field in CLAIM_IDENTITY_FIELDS:
            _verify_identity_record(document[field], f"published claim {field}")
        for field in CLAIM_PLANNED_FIELDS:
            _safe_planned_file(Path(document[field]), require_absent=True)
        return published
    except BaseException:
        _delete_if_identity(publication, published)
        raise


def verify_terminal_claim(path: Path) -> dict[str, Any]:
    document = _load_json(path, "terminal claim")
    _validate_claim_header(document)
    existing: dict[str, Path] = {}
    for field in CLAIM_IDENTITY_FIELDS:
        verified = _verify_identity_record(document[field], f"terminal claim {field}")
        existing[field] = Path(verified["path"])
    _assert_distinct_existing_roles(existing, "terminal claim identities")
    parse_history_sampler_manifest(
        existing["historyRootsManifest"], existing["historyRoots"]
    )
    runtime = parse_classifier_runtime_manifest(
        existing["classifierRuntimeManifest"]
    )
    bundle = parse_classifier_bundle_manifest(existing["classifierBundleManifest"])
    for field, claim_field in (
        ("classifierAssembly", "classifierExecutable"),
        ("classifierRunner", "classifierRunner"),
        ("classifierRuntimeManifest", "classifierRuntimeManifest"),
        ("chessLibAssembly", "chessLibAssembly"),
        ("producer", "producer"),
    ):
        if bundle[field] != document[claim_field]:
            raise ValueError(f"terminal classifier bundle {field} differs from claim")
    if runtime["dotnetExecutable"] != document["classifierRunner"]:
        raise ValueError("terminal classifier runtime differs from claim")
    planned = {
        field: _safe_planned_file(Path(document[field]), require_absent=False)
        for field in CLAIM_PLANNED_FIELDS
        if type(document[field]) is str and Path(document[field]).is_absolute()
    }
    if len(planned) != len(CLAIM_PLANNED_FIELDS):
        raise ValueError("terminal claim planned path is not absolute")
    _assert_distinct_lexical_roles(existing, planned, "terminal claim")
    existing_planned = {
        role: path for role, path in planned.items() if path.is_file()
    }
    if existing_planned:
        _assert_distinct_existing_roles(
            {**existing, **existing_planned}, "terminal claim realized roles"
        )
    claim_path = _safe_existing_file(path)
    _assert_distinct_lexical_roles(
        {**existing, **planned},
        {"claim": claim_path},
        "terminal claim document",
    )
    expected_invocation = _invocation_document(
        runner=existing["classifierRunner"],
        classifier=existing["classifierExecutable"],
        history_roots=existing["historyRoots"],
        transcript=planned["plannedTranscriptPath"],
        eligible_roots=planned["plannedEligibleRootsPath"],
        eligible_children=planned["plannedEligibleChildrenPath"],
        native_manifest=planned["plannedNativeManifestPath"],
    )
    if document["invocation"] != expected_invocation:
        raise ValueError("terminal claim invocation differs from exact command")
    return document


def _normalized_ofen(value: Any, where: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{where} must be a nonempty OFEN string")
    fields = value.split()
    if len(fields) != 6 or fields[1] not in SIDES:
        raise ValueError(f"{where} must be a six-field OFEN")
    normalized = " ".join(fields)
    if normalized != value:
        raise ValueError(f"{where} is not whitespace-normalized")
    if "[" not in fields[0] or not fields[0].endswith("]"):
        raise ValueError(f"{where} lacks four-corner notation")
    if not value.isascii():
        raise ValueError(f"{where} must be ASCII")
    return value


def _phase_for_ofen(ofen: str, where: str) -> str:
    board = ofen.split()[0]
    main, separator, corner_tail = board.partition("[")
    if separator != "[" or not corner_tail.endswith("]"):
        raise ValueError(f"{where} has invalid board/corner syntax")
    ranks = main.split("/")
    if len(ranks) != 10:
        raise ValueError(f"{where} does not have ten ranks")
    pieces = 0
    for rank_index, rank_text in enumerate(ranks):
        width = 0
        index = 0
        while index < len(rank_text):
            if rank_text[index].isdigit():
                end = index + 1
                while end < len(rank_text) and rank_text[end].isdigit():
                    end += 1
                empty = int(rank_text[index:end])
                if empty <= 0:
                    raise ValueError(f"{where} rank {rank_index} has zero empties")
                width += empty
                index = end
            elif rank_text[index].lower() in "kqrbncwp":
                pieces += 1
                width += 1
                index += 1
            else:
                raise ValueError(f"{where} has an unknown board piece")
        if width != 10:
            raise ValueError(f"{where} rank {rank_index} width is not ten")
    corners = corner_tail[:-1].split("/")
    if len(corners) != 4:
        raise ValueError(f"{where} does not have four corner fields")
    for corner in corners:
        if corner == "-":
            continue
        if len(corner) != 1 or corner.lower() not in "kqrbncwp":
            raise ValueError(f"{where} has an invalid corner piece")
        pieces += 1
    if pieces >= 37:
        return "opening"
    if pieces >= 25:
        return "middlegame"
    if pieces >= 13:
        return "late"
    if pieces >= 7:
        return "endgame"
    raise ValueError(f"{where} has fewer than seven production pieces")


def _square(value: str) -> bool:
    return (
        len(value) == 2
        and (
            ("a" <= value[0] <= "j" and "0" <= value[1] <= "9")
            or (value[0] == "w" and "1" <= value[1] <= "4")
        )
    )


def _coordinate(value: Any, where: str) -> str:
    if (
        type(value) is not str
        or len(value) not in (4, 5)
        or value != value.lower()
        or not _square(value[:2])
        or not _square(value[2:4])
        or (len(value) == 5 and value[4] not in "qrbncw")
    ):
        raise ValueError(f"{where} is not canonical Omega coordinate notation")
    return value


def _string_array(value: Any, where: str) -> list[str]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ValueError(f"{where} must be a string array")
    return list(value)


def _parse_history(value: Any, where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{where} is not an object")
    _exact_keys(value, HISTORY_FIELDS, where)
    initial = _normalized_ofen(value["initialOfen"], f"{where}.initialOfen")
    if _require_sha(value["initialOfenSha256"], f"{where}.initialOfenSha256") != (
        hashlib.sha256(initial.encode("utf-8")).hexdigest()
    ):
        raise ValueError(f"{where} initial OFEN hash changed")
    moves = _string_array(value["moves"], f"{where}.moves")
    if len(moves) > 4096:
        raise ValueError(f"{where} exceeds 4096 plies")
    for index, move in enumerate(moves):
        _coordinate(move, f"{where}.moves[{index}]")
    expected = _string_array(
        value["expectedPlyOfenSha256"], f"{where}.expectedPlyOfenSha256"
    )
    observed = _string_array(
        value["observedPlyOfenSha256"], f"{where}.observedPlyOfenSha256"
    )
    if len(expected) != len(moves):
        raise ValueError(f"{where} move/expected-hash count differs")
    for index, item in enumerate(expected):
        _require_sha(item, f"{where}.expectedPlyOfenSha256[{index}]")
    for index, item in enumerate(observed):
        _require_sha(item, f"{where}.observedPlyOfenSha256[{index}]")
    verified = _require_int(value["verifiedPlies"], f"{where}.verifiedPlies")
    if verified > len(moves) or len(observed) > len(moves) or verified > len(observed):
        raise ValueError(f"{where} verified/observed ply counts are impossible")
    failure_ply = value["failurePly"]
    failure_move = value["failureMove"]
    failure_code = value["failureCode"]
    if failure_ply is None:
        if (
            failure_move is not None
            or failure_code is not None
            or verified != len(moves)
            or observed != expected
        ):
            raise ValueError(f"{where} successful replay evidence is incomplete")
    else:
        failure_ply = _require_int(failure_ply, f"{where}.failurePly")
        failure_code = _require_ascii(failure_code, f"{where}.failureCode")
        if re.fullmatch(r"[a-z0-9-]+", failure_code) is None:
            raise ValueError(f"{where}.failureCode is not a canonical token")
        if failure_ply > len(moves):
            raise ValueError(f"{where} failure ply exceeds history")
        if failure_ply == 0:
            if failure_move is not None or verified != 0 or observed:
                raise ValueError(f"{where} initial failure evidence changed")
        else:
            if (
                _coordinate(failure_move, f"{where}.failureMove")
                != moves[failure_ply - 1]
                or verified != failure_ply - 1
                or len(observed) not in (verified, verified + 1)
                or observed[:verified] != expected[:verified]
                or (
                    len(observed) == verified + 1
                    and observed[verified] == expected[verified]
                )
            ):
                raise ValueError(f"{where} failed replay prefix changed")
    return dict(value)


def _parse_root_position(value: Any, where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{where} is not an object")
    _exact_keys(value, ROOT_POSITION_FIELDS, where)
    ofen = _normalized_ofen(value["ofen"], f"{where}.ofen")
    if _require_sha(value["ofenSha256"], f"{where}.ofenSha256") != hashlib.sha256(
        ofen.encode("utf-8")
    ).hexdigest():
        raise ValueError(f"{where} OFEN hash changed")
    classification = value["classification"]
    if classification not in ("root-nonterminal", *TERMINAL_CLASSES):
        raise ValueError(f"{where} root classification changed")
    if value["sideToMove"] not in SIDES or value["sideToMove"] != ofen.split()[1]:
        raise ValueError(f"{where} side-to-move changed")
    for field in ("halfmoveClock", "repetitionCount", "legalMoveCount"):
        _require_int(value[field], f"{where}.{field}")
    return dict(value)


def _parse_child_transcript(value: Any, where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{where} is not an object")
    _exact_keys(value, CHILD_TRANSCRIPT_FIELDS, where)
    _require_int(value["ordinal"], f"{where}.ordinal")
    _coordinate(value["move"], f"{where}.move")
    classification = value["classification"]
    if classification not in ("child-nonterminal", *TERMINAL_CLASSES, *INVALID_CLASSES):
        raise ValueError(f"{where} classification changed")
    _require_int(value["legalMoveCount"], f"{where}.legalMoveCount")
    if value["ofen"] is not None:
        ofen = _normalized_ofen(value["ofen"], f"{where}.ofen")
        if _require_sha(value["ofenSha256"], f"{where}.ofenSha256") != (
            hashlib.sha256(ofen.encode("utf-8")).hexdigest()
        ):
            raise ValueError(f"{where} OFEN hash changed")
        if value["sideToMove"] not in SIDES or value["sideToMove"] != ofen.split()[1]:
            raise ValueError(f"{where} side-to-move changed")
        _require_int(value["halfmoveClock"], f"{where}.halfmoveClock")
        if value["repetitionCount"] is not None:
            _require_int(value["repetitionCount"], f"{where}.repetitionCount")
    elif any(
        value[field] is not None
        for field in ("ofenSha256", "sideToMove", "halfmoveClock", "repetitionCount")
    ):
        raise ValueError(f"{where} null OFEN has nonnull position evidence")
    if classification in ("child-nonterminal", *TERMINAL_CLASSES):
        if value["ofen"] is None or value["failureCode"] is not None:
            raise ValueError(f"{where} classified position lacks complete evidence")
        if value["repetitionCount"] is None:
            raise ValueError(f"{where} classified position lacks repetition evidence")
    else:
        code = _require_ascii(value["failureCode"], f"{where}.failureCode")
        if re.fullmatch(r"[a-z0-9-]+", code) is None:
            raise ValueError(f"{where}.failureCode is not a canonical token")
    return dict(value)


def _transcript_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    history = row["history"]
    payload_history = {
        "initialOfen": history["initialOfen"],
        "initialOfenSha256": history["initialOfenSha256"],
        "moves": history["moves"],
        "expectedPlyOfenSha256": history["expectedPlyOfenSha256"],
        "observedPlyOfenSha256": history["observedPlyOfenSha256"],
        "verifiedPlies": history["verifiedPlies"],
        "failurePly": history["failurePly"],
        "failureMove": history["failureMove"],
        "failureCode": history["failureCode"],
    }
    root = row["root"]
    payload_root = None
    if root is not None:
        payload_root = {
            "ofen": root["ofen"],
            "ofenSha256": root["ofenSha256"],
            "classification": root["classification"],
            "sideToMove": root["sideToMove"],
            "halfmoveClock": root["halfmoveClock"],
            "repetitionCount": root["repetitionCount"],
            "legalMoveCount": root["legalMoveCount"],
        }
    payload_children = [
        {
            "ordinal": child["ordinal"],
            "move": child["move"],
            "ofen": child["ofen"],
            "ofenSha256": child["ofenSha256"],
            "classification": child["classification"],
            "failureCode": child["failureCode"],
            "sideToMove": child["sideToMove"],
            "halfmoveClock": child["halfmoveClock"],
            "repetitionCount": child["repetitionCount"],
            "legalMoveCount": child["legalMoveCount"],
        }
        for child in row["children"]
    ]
    return {
        "schemaVersion": row["schemaVersion"],
        "kind": row["kind"],
        "rootId": row["rootId"],
        "groupId": row["groupId"],
        "teacherEligible": row["teacherEligible"],
        "rejection": row["rejection"],
        "history": payload_history,
        "root": payload_root,
        "children": payload_children,
    }


def transcript_seal(row: Mapping[str, Any]) -> str:
    payload = _transcript_payload(row)
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(
        f"{TRANSCRIPT_HASH_DOMAIN}\0{encoded}".encode("utf-8")
    ).hexdigest()


def _parse_history_root_row(
    row: Mapping[str, Any], number: int, seen: set[str] | None = None
) -> dict[str, Any]:
    where = f"history-root row {number}"
    _exact_keys(row, HISTORY_ROOT_FIELDS, where)
    if (
        type(row["schemaVersion"]) is not int
        or row["schemaVersion"] != SCHEMA_VERSION
        or row["kind"] != HISTORY_ROOT_KIND
    ):
        raise ValueError(f"{where} header changed")
    root_id = _require_nonempty_string(row["rootId"], f"{where}.rootId")
    _require_ascii(root_id, f"{where}.rootId")
    group_id = _require_ascii(row["groupId"], f"{where}.groupId")
    if len(root_id) > 256 or len(group_id) > 256:
        raise ValueError(f"{where} source ID exceeds 256 characters")
    if seen is not None:
        if root_id in seen:
            raise ValueError(f"{where} duplicates rootId {root_id!r}")
        seen.add(root_id)
    initial = _normalized_ofen(row["initialOfen"], f"{where}.initialOfen")
    moves = _string_array(row["moves"], f"{where}.moves")
    hashes = _string_array(row["plyOfenSha256"], f"{where}.plyOfenSha256")
    if len(moves) > 4096 or len(moves) != len(hashes):
        raise ValueError(f"{where} history length/hash coverage changed")
    for index, move in enumerate(moves):
        _coordinate(move, f"{where}.moves[{index}]")
    for index, item in enumerate(hashes):
        _require_sha(item, f"{where}.plyOfenSha256[{index}]")
    return {**row, "initialOfen": initial}


def parse_history_roots(path: Path) -> list[dict[str, Any]]:
    seen: set[str] = set()
    with _iter_jsonl(path, "terminal history roots") as rows:
        parsed = [
            _parse_history_root_row(row, number, seen)
            for number, row in enumerate(rows, 1)
        ]
    if len(parsed) > MAX_HISTORY_ROOT_RECORDS:
        raise ValueError("terminal history roots exceed frozen record ceiling")
    return parsed


def _parse_transcript_row(
    row: Mapping[str, Any], number: int, seen: set[str] | None = None
) -> dict[str, Any]:
    where = f"terminal transcript row {number}"
    _exact_keys(row, TRANSCRIPT_FIELDS, where)
    if (
        type(row["schemaVersion"]) is not int
        or row["schemaVersion"] != SCHEMA_VERSION
        or row["kind"] != TRANSCRIPT_KIND
        or type(row["teacherEligible"]) is not bool
        or type(row["children"]) is not list
    ):
        raise ValueError(f"{where} header changed")
    root_id = _require_nonempty_string(row["rootId"], f"{where}.rootId")
    _require_ascii(root_id, f"{where}.rootId")
    _require_ascii(row["groupId"], f"{where}.groupId")
    if seen is not None:
        if root_id in seen:
            raise ValueError(f"{where} duplicates rootId {root_id!r}")
        seen.add(root_id)
    history = _parse_history(row["history"], f"{where}.history")
    root = (
        None
        if row["root"] is None
        else _parse_root_position(row["root"], f"{where}.root")
    )
    children = [
        _parse_child_transcript(child, f"{where}.children[{index}]")
        for index, child in enumerate(row["children"])
    ]
    if [child["ordinal"] for child in children] != list(range(len(children))):
        raise ValueError(f"{where} child ordinals are not contiguous")
    seal = _require_sha(row["transcriptSha256"], f"{where}.transcriptSha256")
    parsed_row = {**row, "history": history, "root": root, "children": children}
    if seal != transcript_seal(parsed_row):
        raise ValueError(f"{where} transcript seal changed")
    if root is not None and (
        history["verifiedPlies"] != len(history["moves"])
        or len(history["observedPlyOfenSha256"]) != len(history["moves"])
        or history["observedPlyOfenSha256"]
        != history["expectedPlyOfenSha256"]
        or any(
            history[field] is not None
            for field in ("failurePly", "failureMove", "failureCode")
        )
    ):
        raise ValueError(f"{where} replayed root has incomplete history")
    if row["teacherEligible"]:
        if (
            row["rejection"] is not None
            or root is None
            or root["classification"] != "root-nonterminal"
            or not children
            or any(
                child["classification"] != "child-nonterminal"
                for child in children
            )
        ):
            raise ValueError(f"{where} eligible semantics changed")
    else:
        if type(row["rejection"]) is not str or row["rejection"] not in CLASSIFICATIONS:
            raise ValueError(f"{where} rejection changed")
        if root is None:
            if (
                row["rejection"] not in INVALID_CLASSES
                or children
                or history["failurePly"] is None
                or history["failureCode"] is None
            ):
                raise ValueError(f"{where} failed-history semantics changed")
        elif root["classification"] in TERMINAL_CLASSES:
            if row["rejection"] != root["classification"] or children:
                raise ValueError(f"{where} terminal-root semantics changed")
        elif root["classification"] == "root-nonterminal":
            rejected = next(
                (
                    child["classification"]
                    for child in children
                    if child["classification"] != "child-nonterminal"
                ),
                None,
            )
            if rejected is None or row["rejection"] != rejected:
                raise ValueError(f"{where} atomic child-gate semantics changed")
        else:
            raise ValueError(f"{where} root/rejection semantics changed")
    if (
        root is not None
        and root["classification"] == "root-nonterminal"
        and len(children) != root["legalMoveCount"]
    ):
        raise ValueError(f"{where} legal-child coverage is incomplete")
    if root is not None:
        expected_child_side = "b" if root["sideToMove"] == "w" else "w"
        if any(
            child["sideToMove"] is not None
            and child["sideToMove"] != expected_child_side
            for child in children
        ):
            raise ValueError(f"{where} child side did not alternate")
    return parsed_row


def parse_transcript(path: Path) -> list[dict[str, Any]]:
    seen: set[str] = set()
    with _iter_jsonl(path, "terminal transcript") as rows:
        return [
            _parse_transcript_row(row, number, seen)
            for number, row in enumerate(rows, 1)
        ]


def _parse_eligible_root_row(
    row: Mapping[str, Any], number: int, seen: set[str] | None = None
) -> dict[str, Any]:
    where = f"eligible-root row {number}"
    _exact_keys(row, ELIGIBLE_ROOT_FIELDS, where)
    if (
        type(row["schemaVersion"]) is not int
        or row["schemaVersion"] != SCHEMA_VERSION
        or row["kind"] != ELIGIBLE_ROOT_KIND
    ):
        raise ValueError(f"{where} header changed")
    root_id = _require_nonempty_string(row["rootId"], f"{where}.rootId")
    _require_nonempty_string(row["groupId"], f"{where}.groupId")
    if seen is not None:
        if root_id in seen:
            raise ValueError(f"{where} duplicates rootId {root_id!r}")
        seen.add(root_id)
    initial = _normalized_ofen(row["initialOfen"], f"{where}.initialOfen")
    moves = _string_array(row["moves"], f"{where}.moves")
    hashes = _string_array(row["plyOfenSha256"], f"{where}.plyOfenSha256")
    if len(moves) != len(hashes):
        raise ValueError(f"{where} history arrays differ")
    for index, move in enumerate(moves):
        _coordinate(move, f"{where}.moves[{index}]")
    for index, item in enumerate(hashes):
        _require_sha(item, f"{where}.plyOfenSha256[{index}]")
    root_ofen = _normalized_ofen(row["rootOfen"], f"{where}.rootOfen")
    if _require_sha(row["rootOfenSha256"], f"{where}.rootOfenSha256") != (
        hashlib.sha256(root_ofen.encode("utf-8")).hexdigest()
    ):
        raise ValueError(f"{where} root OFEN hash changed")
    _require_sha(
        row["preclassificationTranscriptSha256"],
        f"{where}.preclassificationTranscriptSha256",
    )
    _require_int(row["legalChildCount"], f"{where}.legalChildCount", minimum=1)
    return {**row, "initialOfen": initial}


def parse_eligible_roots(path: Path) -> list[dict[str, Any]]:
    seen: set[str] = set()
    with _iter_jsonl(path, "terminal eligible roots") as rows:
        return [
            _parse_eligible_root_row(row, number, seen)
            for number, row in enumerate(rows, 1)
        ]


def _parse_eligible_child_row(
    row: Mapping[str, Any], number: int, seen: set[str] | None = None
) -> dict[str, Any]:
    where = f"eligible-child row {number}"
    _exact_keys(row, ELIGIBLE_CHILD_FIELDS, where)
    if (
        type(row["schemaVersion"]) is not int
        or row["schemaVersion"] != SCHEMA_VERSION
        or row["kind"] != ELIGIBLE_CHILD_KIND
        or row["classification"] != "child-nonterminal"
    ):
        raise ValueError(f"{where} header/classification changed")
    root_id = _require_nonempty_string(row["rootId"], f"{where}.rootId")
    _require_nonempty_string(row["groupId"], f"{where}.groupId")
    child_id = _require_nonempty_string(row["childId"], f"{where}.childId")
    if seen is not None:
        if child_id in seen:
            raise ValueError(f"{where} duplicates childId {child_id!r}")
        seen.add(child_id)
    initial = _normalized_ofen(row["initialOfen"], f"{where}.initialOfen")
    moves = _string_array(row["moves"], f"{where}.moves")
    hashes = _string_array(row["plyOfenSha256"], f"{where}.plyOfenSha256")
    if len(moves) != len(hashes):
        raise ValueError(f"{where} history arrays differ")
    for index, move in enumerate(moves):
        _coordinate(move, f"{where}.moves[{index}]")
    for index, item in enumerate(hashes):
        _require_sha(item, f"{where}.plyOfenSha256[{index}]")
    parent = _normalized_ofen(row["parentOfen"], f"{where}.parentOfen")
    child = _normalized_ofen(row["childOfen"], f"{where}.childOfen")
    if parent.split()[1] == child.split()[1]:
        raise ValueError(f"{where} child side did not alternate")
    if _require_sha(row["parentOfenSha256"], f"{where}.parentOfenSha256") != (
        hashlib.sha256(parent.encode("utf-8")).hexdigest()
    ):
        raise ValueError(f"{where} parent OFEN hash changed")
    if _require_sha(row["childOfenSha256"], f"{where}.childOfenSha256") != (
        hashlib.sha256(child.encode("utf-8")).hexdigest()
    ):
        raise ValueError(f"{where} child OFEN hash changed")
    move = _coordinate(row["move"], f"{where}.move")
    ordinal = _require_int(row["moveOrdinal"], f"{where}.moveOrdinal")
    transcript_sha = _require_sha(
        row["preclassificationTranscriptSha256"],
        f"{where}.preclassificationTranscriptSha256",
    )
    expected_id = hashlib.sha256(
        (
            f"{CHILD_ID_DOMAIN}\0{root_id}\0{row['groupId']}\0"
            f"{move}\0{child}"
        ).encode("utf-8")
    ).hexdigest()
    if child_id != expected_id:
        raise ValueError(f"{where} stable child ID changed")
    return {
        **row,
        "initialOfen": initial,
        "parentOfen": parent,
        "childOfen": child,
        "moveOrdinal": ordinal,
        "preclassificationTranscriptSha256": transcript_sha,
    }


def parse_eligible_children(path: Path) -> list[dict[str, Any]]:
    seen: set[str] = set()
    with _iter_jsonl(path, "terminal eligible children") as rows:
        return [
            _parse_eligible_child_row(row, number, seen)
            for number, row in enumerate(rows, 1)
        ]


def parse_native_manifest(path: Path) -> dict[str, Any]:
    document = _load_json(path, "native terminal manifest")
    _exact_keys(document, NATIVE_MANIFEST_FIELDS, "native terminal manifest")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != NATIVE_MANIFEST_KIND
    ):
        raise ValueError("native terminal manifest header changed")
    policy = document["policy"]
    if type(policy) is not dict:
        raise ValueError("native terminal policy is not an object")
    _exact_keys(policy, NATIVE_POLICY_FIELDS, "native terminal policy")
    compatibility = policy["senpaiOfenCompatibility"]
    if type(compatibility) is not dict:
        raise ValueError("native compatibility policy is not an object")
    _exact_keys(
        compatibility,
        NATIVE_COMPATIBILITY_FIELDS,
        "native compatibility policy",
    )
    expected_policy = {
        "variant": "omega",
        "replay": "ChessLib.Game.DoMove(move, checkEndGame: false)",
        "orderedSemantics": [
            "checkmate",
            "stalemate",
            "draw-repetition",
            "draw-halfmove",
            "draw-insufficient",
            "nonterminal",
        ],
        "drawForRepetition": 3,
        "senpaiOfenCompatibility": {
            "exactKingsPerSide": 1,
            "forbiddenPawnLocations": [
                "W1-W4",
                "all board squares on ranks 0 and 9",
            ],
            "minimumHalfmoveClock": 0,
            "minimumFullmoveNumber": 1,
            "validationPoints": [
                "initial-position",
                "after-each-history-ply",
                "after-each-legal-child",
            ],
        },
        "eligibility": (
            "reject the root unless root and every legal child are nonterminal"
        ),
    }
    if policy != expected_policy:
        raise ValueError("native terminal policy changed")
    coverage = document["coverage"]
    if type(coverage) is not dict:
        raise ValueError("native terminal coverage is not an object")
    _exact_keys(coverage, NATIVE_COVERAGE_FIELDS, "native terminal coverage")
    for field in ("sourceRecords", "acceptedRoots", "rejectedRoots", "eligibleChildren"):
        _require_int(coverage[field], f"native terminal coverage.{field}")
    counts = coverage["classificationCounts"]
    if type(counts) is not dict or set(counts) != set(CLASSIFICATIONS):
        raise ValueError("native terminal classification inventory changed")
    for classification in CLASSIFICATIONS:
        _require_int(
            counts[classification],
            f"native classificationCounts.{classification}",
        )
    for field in ("input", "transcript", "eligibleRoots", "eligibleChildren"):
        _native_identity(document[field], f"native terminal {field}")
    runtime = document["runtime"]
    if type(runtime) is not dict:
        raise ValueError("native terminal runtime is not an object")
    _exact_keys(runtime, NATIVE_RUNTIME_FIELDS, "native terminal runtime")
    if (
        runtime["framework"] != ".NET 10.0.9"
        or runtime["expectedRuntimeVersion"] != EXPECTED_RUNTIME_VERSION
        or runtime["expectedRuntimeBundleSha256"]
        != EXPECTED_RUNTIME_BUNDLE_SHA256
        or runtime["expectedRuntimeManifestSha256"]
        != EXPECTED_RUNTIME_MANIFEST_SHA256
        or runtime["expectedDotnetHostSha256"] != EXPECTED_DOTNET_SHA256
        or runtime["expectedChessLibSha256"] != EXPECTED_CHESSLIB_SHA256
        or runtime["expectedNewtonsoftJsonSha256"]
        != EXPECTED_NEWTONSOFT_SHA256
        or runtime["expectedSystemIoPortsSha256"]
        != EXPECTED_SYSTEM_IO_PORTS_SHA256
    ):
        raise ValueError("native terminal runtime expectation changed")
    raw_closure = runtime["executionClosure"]
    if type(raw_closure) is not dict:
        raise ValueError("native terminal execution closure is not an object")
    raw_classifier = _require_pinned_identity(
        raw_closure.get("classifierAssembly"),
        "native terminal execution closure classifier",
        expected_bytes=EXPECTED_CLASSIFIER_BYTES,
        expected_sha256=EXPECTED_CLASSIFIER_SHA256,
    )
    application_directory = Path(raw_classifier["path"]).parent
    bundle = parse_classifier_bundle_manifest(
        application_directory / "bundle.manifest.json"
    )
    frozen_runtime = parse_classifier_runtime_manifest(
        application_directory / "runtime.manifest.json"
    )
    closure = _parse_execution_closure(
        raw_closure,
        "native terminal execution closure",
        assembly_field="classifierAssembly",
        expected_assembly_bytes=EXPECTED_CLASSIFIER_BYTES,
        expected_assembly_sha256=EXPECTED_CLASSIFIER_SHA256,
        bundle=bundle,
        bundle_assembly_field="classifierAssembly",
        runtime=frozen_runtime,
    )
    return {
        **document,
        "runtime": {**runtime, "executionClosure": closure},
    }


def _cross_check_artifacts(
    *,
    history_roots: Sequence[dict[str, Any]],
    transcript: Sequence[dict[str, Any]],
    eligible_roots: Sequence[dict[str, Any]],
    eligible_children: Sequence[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    if [row["rootId"] for row in history_roots] != [
        row["rootId"] for row in transcript
    ]:
        raise ValueError("terminal transcript is not in exact history-root order")
    for source, replayed in zip(history_roots, transcript, strict=True):
        history = replayed["history"]
        if (
            source["groupId"] != replayed["groupId"]
            or source["initialOfen"] != history["initialOfen"]
            or source["moves"] != history["moves"]
            or source["plyOfenSha256"]
            != history["expectedPlyOfenSha256"]
        ):
            raise ValueError(
                f"terminal transcript {replayed['rootId']!r} differs from history input"
            )
        metadata = _history_root_metadata(source, f"history root {source['rootId']!r}")
        if replayed["root"] is not None and (
            replayed["root"]["sideToMove"] != metadata["side"]
            or _phase_for_ofen(
                replayed["root"]["ofen"],
                f"terminal root {source['rootId']!r}",
            )
            != metadata["phase"]
        ):
            raise ValueError(
                f"terminal transcript {source['rootId']!r} phase/side differs from sampler"
            )
    transcript_by_root = {row["rootId"]: row for row in transcript}
    accepted = [row for row in transcript if row["teacherEligible"]]
    expected_root_order = [row["rootId"] for row in accepted]
    if [row["rootId"] for row in eligible_roots] != expected_root_order:
        raise ValueError("eligible-root output is not the exact accepted transcript order")
    roots_by_id = {row["rootId"]: row for row in eligible_roots}
    expected_child_order: list[tuple[str, int]] = []
    for root in eligible_roots:
        source = transcript_by_root[root["rootId"]]
        history = source["history"]
        position = source["root"]
        assert position is not None
        if (
            root["groupId"] != source["groupId"]
            or root["initialOfen"] != history["initialOfen"]
            or root["moves"] != history["moves"]
            or root["plyOfenSha256"] != history["expectedPlyOfenSha256"]
            or root["rootOfen"] != position["ofen"]
            or root["rootOfenSha256"] != position["ofenSha256"]
            or root["preclassificationTranscriptSha256"]
            != source["transcriptSha256"]
            or root["legalChildCount"] != len(source["children"])
        ):
            raise ValueError(f"eligible root {root['rootId']!r} differs from transcript")
        expected_child_order.extend(
            (root["rootId"], ordinal) for ordinal in range(len(source["children"]))
        )
    if [
        (row["rootId"], row["moveOrdinal"]) for row in eligible_children
    ] != expected_child_order:
        raise ValueError("eligible-child output is not exact accepted root/ordinal order")
    children_by_id: dict[str, dict[str, Any]] = {}
    children_by_root: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for child in eligible_children:
        root = roots_by_id.get(child["rootId"])
        if root is None:
            raise ValueError(f"eligible child {child['childId']!r} has no eligible root")
        source = transcript_by_root[child["rootId"]]
        transcript_child = source["children"][child["moveOrdinal"]]
        if (
            child["groupId"] != root["groupId"]
            or child["initialOfen"] != root["initialOfen"]
            or child["moves"] != root["moves"]
            or child["plyOfenSha256"] != root["plyOfenSha256"]
            or child["parentOfen"] != root["rootOfen"]
            or child["parentOfenSha256"] != root["rootOfenSha256"]
            or child["move"] != transcript_child["move"]
            or child["childOfen"] != transcript_child["ofen"]
            or child["childOfenSha256"] != transcript_child["ofenSha256"]
            or child["preclassificationTranscriptSha256"]
            != root["preclassificationTranscriptSha256"]
        ):
            raise ValueError(f"eligible child {child['childId']!r} differs from transcript")
        children_by_id[child["childId"]] = child
        children_by_root[child["rootId"]].append(child)
    if any(
        len(children_by_root[root["rootId"]]) != root["legalChildCount"]
        for root in eligible_roots
    ):
        raise ValueError("eligible root/child coverage is incomplete")

    classifications: Counter[str] = Counter()
    root_terminal = 0
    history_invalid = 0
    child_gate = 0
    classified_children = 0
    terminal_children = 0
    invalid_children = 0
    child_nonterminal = 0
    for row in transcript:
        classifications[
            row["root"]["classification"] if row["root"] is not None else row["rejection"]
        ] += 1
        if not row["teacherEligible"]:
            if row["root"] is None:
                history_invalid += 1
            elif row["root"]["classification"] in TERMINAL_CLASSES:
                root_terminal += 1
            else:
                child_gate += 1
        for child in row["children"]:
            classification = child["classification"]
            classifications[classification] += 1
            classified_children += 1
            if classification in TERMINAL_CLASSES:
                terminal_children += 1
            elif classification in INVALID_CLASSES:
                invalid_children += 1
            elif classification == "child-nonterminal":
                child_nonterminal += 1
            else:
                raise ValueError("unclassified terminal child escaped strict parser")
    safe_siblings = child_nonterminal - len(eligible_children)
    if safe_siblings < 0:
        raise ValueError("eligible-child count exceeds classified nonterminal children")
    coverage = {
        "sourceRecords": len(history_roots),
        "transcriptRecords": len(transcript),
        "acceptedRoots": len(accepted),
        "rejectedRoots": len(transcript) - len(accepted),
        "rootTerminalRejections": root_terminal,
        "historyInvalidRejections": history_invalid,
        "childGateRejectedRoots": child_gate,
        "classifiedChildren": classified_children,
        "terminalChildren": terminal_children,
        "invalidChildren": invalid_children,
        "childNonterminalChildren": child_nonterminal,
        "eligibleRoots": len(eligible_roots),
        "eligibleChildren": len(eligible_children),
        "safeSiblingChildrenExcludedByAtomicRootPolicy": safe_siblings,
        "childrenExcludedByAtomicRootPolicy": (
            terminal_children + invalid_children + safe_siblings
        ),
    }
    if (
        coverage["acceptedRoots"] + coverage["rejectedRoots"]
        != coverage["sourceRecords"]
        or root_terminal + history_invalid + child_gate != coverage["rejectedRoots"]
        or terminal_children + invalid_children + child_nonterminal
        != classified_children
    ):
        raise AssertionError("internal terminal partition arithmetic changed")
    counts = {classification: classifications[classification] for classification in CLASSIFICATIONS}
    return coverage, counts


class _CanonicalArrayDigest:
    """Incremental digest exactly matching ``_order_digest(list(items))``."""

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._digest.update(b"[")
        self._items = 0
        self._finished = False

    def add(self, value: Any) -> None:
        if self._finished:
            raise AssertionError("canonical array digest was already finalized")
        if self._items:
            self._digest.update(b",")
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._digest.update(encoded)
        self._items += 1

    def hexdigest(self) -> str:
        if self._finished:
            raise AssertionError("canonical array digest was already finalized")
        self._digest.update(b"]\n")
        self._finished = True
        return self._digest.hexdigest()


def _stream_cross_check_artifacts(
    *,
    history_roots: Path,
    transcript: Path,
    eligible_roots: Path,
    eligible_children: Path,
    expected_identities: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, int], dict[str, int], str, str, str]:
    """Validate the four ordered artifacts without materializing any of them."""

    history_seen: set[str] = set()
    classifications: Counter[str] = Counter()
    transcript_digest = _CanonicalArrayDigest()
    root_digest = _CanonicalArrayDigest()
    child_digest = _CanonicalArrayDigest()
    source_records = 0
    accepted_roots = 0
    root_terminal = 0
    history_invalid = 0
    child_gate = 0
    classified_children = 0
    terminal_children = 0
    invalid_children = 0
    child_nonterminal = 0
    eligible_root_count = 0
    eligible_child_count = 0

    expected = expected_identities or {}
    with _iter_jsonl(
        history_roots,
        "terminal history roots",
        expected_identity=expected.get("historyRoots"),
    ) as source_rows:
        with _iter_jsonl(
            transcript,
            "terminal transcript",
            expected_identity=expected.get("transcript"),
        ) as transcript_rows:
            with _iter_jsonl(
                eligible_roots,
                "terminal eligible roots",
                expected_identity=expected.get("eligibleRoots"),
            ) as root_rows:
                with _iter_jsonl(
                    eligible_children,
                    "terminal eligible children",
                    expected_identity=expected.get("eligibleChildren"),
                ) as child_rows:
                    while True:
                        try:
                            source_raw = next(source_rows)
                        except StopIteration:
                            source_raw = None
                        try:
                            replayed_raw = next(transcript_rows)
                        except StopIteration:
                            replayed_raw = None
                        if source_raw is None and replayed_raw is None:
                            break
                        if source_raw is None or replayed_raw is None:
                            raise ValueError(
                                "terminal transcript is not in exact history-root order"
                            )
                        source_records += 1
                        if source_records > MAX_HISTORY_ROOT_RECORDS:
                            raise ValueError(
                                "terminal history roots exceed frozen record ceiling"
                            )
                        source = _parse_history_root_row(
                            source_raw, source_records, history_seen
                        )
                        replayed = _parse_transcript_row(
                            replayed_raw, source_records
                        )
                        if source["rootId"] != replayed["rootId"]:
                            raise ValueError(
                                "terminal transcript is not in exact history-root order"
                            )
                        history = replayed["history"]
                        if (
                            source["groupId"] != replayed["groupId"]
                            or source["initialOfen"] != history["initialOfen"]
                            or source["moves"] != history["moves"]
                            or source["plyOfenSha256"]
                            != history["expectedPlyOfenSha256"]
                        ):
                            raise ValueError(
                                f"terminal transcript {replayed['rootId']!r} "
                                "differs from history input"
                            )
                        metadata = _history_root_metadata(
                            source, f"history root {source['rootId']!r}"
                        )
                        if replayed["root"] is not None and (
                            replayed["root"]["sideToMove"] != metadata["side"]
                            or _phase_for_ofen(
                                replayed["root"]["ofen"],
                                f"terminal root {source['rootId']!r}",
                            )
                            != metadata["phase"]
                        ):
                            raise ValueError(
                                f"terminal transcript {source['rootId']!r} "
                                "phase/side differs from sampler"
                            )
                        transcript_digest.add(
                            {
                                "rootId": replayed["rootId"],
                                "groupId": replayed["groupId"],
                                "transcriptSha256": replayed[
                                    "transcriptSha256"
                                ],
                            }
                        )

                        root_classification = (
                            replayed["root"]["classification"]
                            if replayed["root"] is not None
                            else replayed["rejection"]
                        )
                        classifications[root_classification] += 1
                        if not replayed["teacherEligible"]:
                            if replayed["root"] is None:
                                history_invalid += 1
                            elif replayed["root"]["classification"] in TERMINAL_CLASSES:
                                root_terminal += 1
                            else:
                                child_gate += 1
                        for transcript_child in replayed["children"]:
                            classification = transcript_child["classification"]
                            classifications[classification] += 1
                            classified_children += 1
                            if classification in TERMINAL_CLASSES:
                                terminal_children += 1
                            elif classification in INVALID_CLASSES:
                                invalid_children += 1
                            elif classification == "child-nonterminal":
                                child_nonterminal += 1
                            else:
                                raise ValueError(
                                    "unclassified terminal child escaped strict parser"
                                )

                        if not replayed["teacherEligible"]:
                            continue
                        accepted_roots += 1
                        try:
                            root_raw = next(root_rows)
                        except StopIteration as error:
                            raise ValueError(
                                "eligible-root output is not the exact accepted "
                                "transcript order"
                            ) from error
                        eligible_root_count += 1
                        root = _parse_eligible_root_row(
                            root_raw, eligible_root_count
                        )
                        if root["rootId"] != replayed["rootId"]:
                            raise ValueError(
                                "eligible-root output is not the exact accepted "
                                "transcript order"
                            )
                        position = replayed["root"]
                        assert position is not None
                        if (
                            root["groupId"] != replayed["groupId"]
                            or root["initialOfen"] != history["initialOfen"]
                            or root["moves"] != history["moves"]
                            or root["plyOfenSha256"]
                            != history["expectedPlyOfenSha256"]
                            or root["rootOfen"] != position["ofen"]
                            or root["rootOfenSha256"] != position["ofenSha256"]
                            or root["preclassificationTranscriptSha256"]
                            != replayed["transcriptSha256"]
                            or root["legalChildCount"]
                            != len(replayed["children"])
                        ):
                            raise ValueError(
                                f"eligible root {root['rootId']!r} differs from transcript"
                            )
                        root_digest.add(root["rootId"])

                        for expected_ordinal, transcript_child in enumerate(
                            replayed["children"]
                        ):
                            try:
                                child_raw = next(child_rows)
                            except StopIteration as error:
                                raise ValueError(
                                    "eligible-child output is not exact accepted "
                                    "root/ordinal order"
                                ) from error
                            eligible_child_count += 1
                            child = _parse_eligible_child_row(
                                child_raw, eligible_child_count
                            )
                            if (
                                child["rootId"] != root["rootId"]
                                or child["moveOrdinal"] != expected_ordinal
                            ):
                                raise ValueError(
                                    "eligible-child output is not exact accepted "
                                    "root/ordinal order"
                                )
                            if (
                                child["groupId"] != root["groupId"]
                                or child["initialOfen"] != root["initialOfen"]
                                or child["moves"] != root["moves"]
                                or child["plyOfenSha256"]
                                != root["plyOfenSha256"]
                                or child["parentOfen"] != root["rootOfen"]
                                or child["parentOfenSha256"]
                                != root["rootOfenSha256"]
                                or child["move"] != transcript_child["move"]
                                or child["childOfen"] != transcript_child["ofen"]
                                or child["childOfenSha256"]
                                != transcript_child["ofenSha256"]
                                or child[
                                    "preclassificationTranscriptSha256"
                                ]
                                != root[
                                    "preclassificationTranscriptSha256"
                                ]
                            ):
                                raise ValueError(
                                    f"eligible child {child['childId']!r} "
                                    "differs from transcript"
                                )
                            child_digest.add(child["childId"])

                    try:
                        next(root_rows)
                    except StopIteration:
                        pass
                    else:
                        raise ValueError(
                            "eligible-root output is not the exact accepted "
                            "transcript order"
                        )
                    try:
                        next(child_rows)
                    except StopIteration:
                        pass
                    else:
                        raise ValueError(
                            "eligible-child output is not exact accepted "
                            "root/ordinal order"
                        )

    safe_siblings = child_nonterminal - eligible_child_count
    if safe_siblings < 0:
        raise ValueError("eligible-child count exceeds classified nonterminal children")
    rejected_roots = source_records - accepted_roots
    coverage = {
        "sourceRecords": source_records,
        "transcriptRecords": source_records,
        "acceptedRoots": accepted_roots,
        "rejectedRoots": rejected_roots,
        "rootTerminalRejections": root_terminal,
        "historyInvalidRejections": history_invalid,
        "childGateRejectedRoots": child_gate,
        "classifiedChildren": classified_children,
        "terminalChildren": terminal_children,
        "invalidChildren": invalid_children,
        "childNonterminalChildren": child_nonterminal,
        "eligibleRoots": eligible_root_count,
        "eligibleChildren": eligible_child_count,
        "safeSiblingChildrenExcludedByAtomicRootPolicy": safe_siblings,
        "childrenExcludedByAtomicRootPolicy": (
            terminal_children + invalid_children + safe_siblings
        ),
    }
    if (
        accepted_roots + rejected_roots != source_records
        or root_terminal + history_invalid + child_gate != rejected_roots
        or terminal_children + invalid_children + child_nonterminal
        != classified_children
        or eligible_root_count != accepted_roots
    ):
        raise AssertionError("internal terminal partition arithmetic changed")
    counts = {
        classification: classifications[classification]
        for classification in CLASSIFICATIONS
    }
    return (
        coverage,
        counts,
        transcript_digest.hexdigest(),
        root_digest.hexdigest(),
        child_digest.hexdigest(),
    )


def _order_digest(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _expected_classifier_stdout(
    *,
    coverage: Mapping[str, int],
    transcript: Path,
    eligible_roots: Path,
    eligible_children: Path,
    native_manifest: Path,
) -> bytes:
    lines = (
        f"Classified {coverage['sourceRecords']} roots: "
        f"{coverage['acceptedRoots']} eligible, "
        f"{coverage['rejectedRoots']} rejected, "
        f"{coverage['eligibleChildren']} eligible children.",
        f"Transcript: {_lexical_absolute(transcript)}",
        f"Eligible roots: {_lexical_absolute(eligible_roots)}",
        f"Eligible children: {_lexical_absolute(eligible_children)}",
        f"Manifest: {_lexical_absolute(native_manifest)}",
    )
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def terminal_lineage_document(
    *,
    claim: Path,
    native_manifest: Path,
    transcript: Path,
    eligible_roots: Path,
    eligible_children: Path,
    classifier_stdout: Path,
    classifier_stderr: Path,
    started_utc: str,
    completed_utc: str,
    exit_code: int,
    timed_out: bool,
    created_utc: str,
) -> dict[str, Any]:
    claim_document = verify_terminal_claim(claim)
    claim_created = _parse_timestamp(
        claim_document["createdUtc"], "terminal claim createdUtc"
    )
    started = _parse_timestamp(started_utc, "terminal execution startedUtc")
    completed = _parse_timestamp(completed_utc, "terminal execution completedUtc")
    created = _parse_timestamp(created_utc, "terminal lineage createdUtc")
    if not claim_created < started < completed < created:
        raise ValueError(
            "terminal chronology must be claim < start < completion < lineage"
        )
    if type(exit_code) is not int or exit_code != 0 or timed_out is not False:
        raise ValueError("terminal execution did not satisfy exit/timeout policy")
    actual_paths = {
        "plannedNativeManifestPath": _safe_existing_file(native_manifest),
        "plannedTranscriptPath": _safe_existing_file(transcript),
        "plannedEligibleRootsPath": _safe_existing_file(eligible_roots),
        "plannedEligibleChildrenPath": _safe_existing_file(eligible_children),
        "plannedStdoutPath": _safe_existing_file(classifier_stdout),
        "plannedStderrPath": _safe_existing_file(classifier_stderr),
    }
    for field, actual in actual_paths.items():
        if claim_document[field] != str(actual):
            raise ValueError(f"terminal claim planned path differs for {field}")
    claim_roles = {
        field: Path(claim_document[field]["path"])
        for field in CLAIM_IDENTITY_FIELDS
    }
    all_roles = {
        "claim": claim,
        **claim_roles,
        "nativeManifest": native_manifest,
        "transcript": transcript,
        "eligibleRoots": eligible_roots,
        "eligibleChildren": eligible_children,
        "classifierStdout": classifier_stdout,
        "classifierStderr": classifier_stderr,
    }
    identities = _assert_distinct_existing_roles(all_roles, "terminal lineage")
    native = parse_native_manifest(native_manifest)
    (
        coverage,
        classification_counts,
        transcript_order_sha256,
        eligible_root_order_sha256,
        eligible_child_order_sha256,
    ) = _stream_cross_check_artifacts(
        history_roots=Path(claim_document["historyRoots"]["path"]),
        transcript=transcript,
        eligible_roots=eligible_roots,
        eligible_children=eligible_children,
        expected_identities={
            "historyRoots": identities["historyRoots"],
            "transcript": identities["transcript"],
            "eligibleRoots": identities["eligibleRoots"],
            "eligibleChildren": identities["eligibleChildren"],
        },
    )
    expected_native_identities = {
        "input": _identity_without_path(identities["historyRoots"]),
        "transcript": _identity_without_path(identities["transcript"]),
        "eligibleRoots": _identity_without_path(identities["eligibleRoots"]),
        "eligibleChildren": _identity_without_path(identities["eligibleChildren"]),
    }
    for field, expected in expected_native_identities.items():
        if native[field] != expected:
            raise ValueError(f"native terminal manifest {field} identity changed")
    native_coverage = native["coverage"]
    if (
        native_coverage["sourceRecords"] != coverage["sourceRecords"]
        or native_coverage["acceptedRoots"] != coverage["acceptedRoots"]
        or native_coverage["rejectedRoots"] != coverage["rejectedRoots"]
        or native_coverage["eligibleChildren"] != coverage["eligibleChildren"]
        or native_coverage["classificationCounts"] != classification_counts
    ):
        raise ValueError("native terminal manifest coverage differs from transcript")
    if (
        native["runtime"]["executionClosure"]["classifierAssembly"]
        != identities["classifierExecutable"]
    ):
        raise ValueError("native terminal classifier differs from pre-run claim")
    stdout_identity, stdout_payload = _snapshot_file(
        classifier_stdout, max_bytes=MAX_EXECUTION_LOG_BYTES
    )
    stderr_identity, stderr_payload = _snapshot_file(
        classifier_stderr, max_bytes=MAX_EXECUTION_LOG_BYTES
    )
    if (
        stdout_identity != identities["classifierStdout"]
        or stderr_identity != identities["classifierStderr"]
    ):
        raise ValueError("terminal execution log identity changed")
    expected_stdout = _expected_classifier_stdout(
        coverage=coverage,
        transcript=transcript,
        eligible_roots=eligible_roots,
        eligible_children=eligible_children,
        native_manifest=native_manifest,
    )
    if stdout_payload != expected_stdout or stderr_payload != b"":
        raise ValueError("terminal execution stdout/stderr policy changed")
    execution = {
        "startedUtc": started_utc,
        "completedUtc": completed_utc,
        "invocation": claim_document["invocation"],
        "exitCode": exit_code,
        "timedOut": timed_out,
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LINEAGE_KIND,
        "profileId": PROFILE_ID,
        "status": "complete-rules-semantic-classification",
        "createdUtc": created_utc,
        "claim": identities["claim"],
        "nativeManifest": identities["nativeManifest"],
        "transcript": identities["transcript"],
        "eligibleRoots": identities["eligibleRoots"],
        "eligibleChildren": identities["eligibleChildren"],
        "classifierStdout": identities["classifierStdout"],
        "classifierStderr": identities["classifierStderr"],
        "execution": execution,
        "coverage": coverage,
        "classificationCounts": classification_counts,
        "transcriptOrderSha256": transcript_order_sha256,
        "eligibleRootOrderSha256": eligible_root_order_sha256,
        "eligibleChildOrderSha256": eligible_child_order_sha256,
        "terminalChildrenExcludedBeforeRouting": coverage["terminalChildren"],
        "unclassifiedChildren": 0,
        "errorTextAcceptedAsTerminal": False,
        "targetRowsDecodedAtCompletion": 0,
        "targetFieldsDecodedAtCompletion": 0,
        "resultInformationRead": False,
        "finalStageSeal": True,
    }


def publish_terminal_lineage(path: Path, **kwargs: Any) -> dict[str, Any]:
    document = terminal_lineage_document(**kwargs)
    publication = _safe_new_file(path)
    role_paths = {
        field: Path(document[field]["path"])
        for field in (
            "claim",
            "nativeManifest",
            "transcript",
            "eligibleRoots",
            "eligibleChildren",
            "classifierStdout",
            "classifierStderr",
        )
    }
    _assert_distinct_lexical_roles(
        role_paths,
        {"lineagePublication": publication},
        "terminal lineage publication",
    )
    published = _exclusive_json(publication, document)
    try:
        verify_terminal_lineage(publication)
        return published
    except BaseException:
        _delete_if_identity(publication, published)
        raise


def verify_terminal_lineage(path: Path) -> dict[str, Any]:
    document = _load_json(path, "terminal classifier lineage")
    _exact_keys(document, LINEAGE_FIELDS, "terminal classifier lineage")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != LINEAGE_KIND
        or document["profileId"] != PROFILE_ID
        or document["status"] != "complete-rules-semantic-classification"
        or type(document["terminalChildrenExcludedBeforeRouting"]) is not int
        or document["terminalChildrenExcludedBeforeRouting"] < 0
        or type(document["unclassifiedChildren"]) is not int
        or document["unclassifiedChildren"] != 0
        or document["errorTextAcceptedAsTerminal"] is not False
        or type(document["targetRowsDecodedAtCompletion"]) is not int
        or document["targetRowsDecodedAtCompletion"] != 0
        or type(document["targetFieldsDecodedAtCompletion"]) is not int
        or document["targetFieldsDecodedAtCompletion"] != 0
        or document["resultInformationRead"] is not False
        or document["finalStageSeal"] is not True
    ):
        raise ValueError("terminal classifier lineage header changed")
    _parse_timestamp(document["createdUtc"], "terminal lineage createdUtc")
    if type(document["coverage"]) is not dict:
        raise ValueError("terminal lineage coverage is not an object")
    _exact_keys(document["coverage"], COVERAGE_FIELDS, "terminal lineage coverage")
    for field in COVERAGE_FIELDS:
        _require_int(document["coverage"][field], f"terminal coverage.{field}")
    if (
        type(document["classificationCounts"]) is not dict
        or set(document["classificationCounts"]) != set(CLASSIFICATIONS)
    ):
        raise ValueError("terminal lineage classification inventory changed")
    for classification in CLASSIFICATIONS:
        _require_int(
            document["classificationCounts"][classification],
            f"terminal lineage classificationCounts.{classification}",
        )
    for field in (
        "transcriptOrderSha256",
        "eligibleRootOrderSha256",
        "eligibleChildOrderSha256",
    ):
        _require_sha(document[field], f"terminal lineage {field}")
    identity_fields = (
        "claim",
        "nativeManifest",
        "transcript",
        "eligibleRoots",
        "eligibleChildren",
        "classifierStdout",
        "classifierStderr",
    )
    identities = {
        field: _verify_identity_record(document[field], f"terminal lineage {field}")
        for field in identity_fields
    }
    _assert_distinct_existing_roles(
        {field: Path(identity["path"]) for field, identity in identities.items()},
        "terminal lineage document",
    )
    execution = document["execution"]
    if type(execution) is not dict:
        raise ValueError("terminal execution receipt is not an object")
    _exact_keys(execution, EXECUTION_FIELDS, "terminal execution receipt")
    _parse_timestamp(execution["startedUtc"], "terminal execution startedUtc")
    _parse_timestamp(execution["completedUtc"], "terminal execution completedUtc")
    _verify_invocation(execution["invocation"], "terminal execution invocation")
    if (
        type(execution["exitCode"]) is not int
        or execution["exitCode"] != 0
        or execution["timedOut"] is not False
    ):
        raise ValueError("terminal execution receipt changed")
    expected = terminal_lineage_document(
        claim=Path(identities["claim"]["path"]),
        native_manifest=Path(identities["nativeManifest"]["path"]),
        transcript=Path(identities["transcript"]["path"]),
        eligible_roots=Path(identities["eligibleRoots"]["path"]),
        eligible_children=Path(identities["eligibleChildren"]["path"]),
        classifier_stdout=Path(identities["classifierStdout"]["path"]),
        classifier_stderr=Path(identities["classifierStderr"]["path"]),
        started_utc=execution["startedUtc"],
        completed_utc=execution["completedUtc"],
        exit_code=execution["exitCode"],
        timed_out=execution["timedOut"],
        created_utc=document["createdUtc"],
    )
    if document != expected:
        raise ValueError("terminal classifier lineage differs from recomputation")
    lineage_path = _safe_existing_file(path)
    _assert_distinct_lexical_roles(
        {field: Path(identity["path"]) for field, identity in identities.items()},
        {"lineage": lineage_path},
        "terminal lineage document",
    )
    return document


__all__ = [
    "CLAIM_KIND",
    "CLASSIFICATIONS",
    "ELIGIBLE_CHILD_KIND",
    "ELIGIBLE_ROOT_KIND",
    "BUNDLE_MANIFEST_KIND",
    "HISTORY_MANIFEST_KIND",
    "RUNTIME_MANIFEST_KIND",
    "EXPECTED_CHESSLIB_SHA256",
    "HISTORY_ROOT_KIND",
    "LINEAGE_KIND",
    "NATIVE_MANIFEST_KIND",
    "PROFILE_ID",
    "TRANSCRIPT_KIND",
    "classifier_command_protocol",
    "parse_eligible_children",
    "parse_eligible_roots",
    "parse_history_roots",
    "parse_history_sampler_manifest",
    "parse_classifier_bundle_manifest",
    "parse_classifier_runtime_manifest",
    "parse_native_manifest",
    "parse_transcript",
    "publish_terminal_claim",
    "publish_terminal_lineage",
    "terminal_claim_document",
    "terminal_lineage_document",
    "transcript_seal",
    "verify_terminal_claim",
    "verify_terminal_lineage",
]
