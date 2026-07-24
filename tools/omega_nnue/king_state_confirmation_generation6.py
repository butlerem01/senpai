#!/usr/bin/env python3
"""Fail-closed Generation-6 promotion and confirmation successor.

This module is deliberately separate from the Generation-6 trainer and from
open-confirmation v2.  It freezes the missing bridge between them:

* a result-blind preregistration, published before held-out access;
* an aggregate-only held-out nomination rule;
* an exact Senpai executable + selected-network deployment bundle;
* a paired, candidate-blind G6-vs-G5 practical gate; and
* an alpha/attempt-preserving handoff to the three frozen v2 formal gates.

It does not launch matches and it does not create any production artifact on
import.  Every publication uses a final-name O_EXCL write.  The only random
draw occurs after the attempt reservation has durably consumed its global
index; the public claim and suite contain commitments, never the entropy or
derived seeds.
"""

from __future__ import annotations

import argparse
from collections import Counter
import contextlib
from datetime import datetime, timezone
import functools
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
PROTOCOL_ID = "omega-nnue-generation6-promotion-confirmation-v1"
PROTOCOL_KIND = f"{PROTOCOL_ID}-protocol"
PREREGISTRATION_KIND = f"{PROTOCOL_ID}-preregistration"
STARTUP_ATTESTATION_KIND = f"{PROTOCOL_ID}-startup-attestation"
DEPLOYMENT_BUNDLE_KIND = f"{PROTOCOL_ID}-deployment-bundle"
HELDOUT_DECISION_KIND = f"{PROTOCOL_ID}-heldout-decision"
RESERVATION_KIND = f"{PROTOCOL_ID}-attempt-reservation"
CLAIM_KIND = f"{PROTOCOL_ID}-candidate-claim"
SUITE_KIND = f"{PROTOCOL_ID}-practical-suite"
EVENT_KIND = f"{PROTOCOL_ID}-practical-pair-event"
PRACTICAL_DECISION_KIND = f"{PROTOCOL_ID}-practical-decision"
HCE_HANDOFF_KIND = f"{PROTOCOL_ID}-hce-handoff"

G6_PROFILE_ID = "omega-nnue-king-state-v6"
G6_PREREGISTRATION_KIND = "omega-nnue-king-state-v6-preregistration"
G6_CAPSULE_KIND = "omega-decision-v3-capsule-closure"
G6_RECEIPT_KIND = "omega-decision-v3-fresh-verification"
G6_SELECTION_KIND = "omega-nnue-king-state-v6-validation-selection-seal"
G6_ROBUSTNESS_KIND = "omega-nnue-king-state-v6-robustness-seal"
G6_HELDOUT_ACCESS_KIND = "omega-nnue-king-state-v6-heldout-access-seal"
G6_HELDOUT_CLAIM_KIND = "omega-nnue-king-state-v6-heldout-consumption-claim"
G6_HELDOUT_REPORT_KIND = "omega-nnue-king-state-v6-heldout-aggregate-report"
G6_HELDOUT_CLOSURE_KIND = "omega-nnue-king-state-v6-heldout-closure"

V2_PROTOCOL_ID = "omega-nnue-open-confirmation-v2"
V2_CLOSURE_KIND = "omega-nnue-open-confirmation-v2-attempt-closure"
FORMAL_HCE_GATES = ("equal-node", "equal-time", "normal-start-clock")
FORMAL_HCE_COMMON_OPTIONS = {
    "Threads": "1",
    "Hash": "128",
    "Ponder": "false",
    "OwnBook": "false",
    "UCI_Chess960": "false",
    "UCI_Variant": "omega",
}
PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
CELLS = tuple(f"{phase}:{side}" for phase in PHASES for side in SIDES)
METRICS = (
    "topSetAccuracy",
    "meanChosenMoveRegretCp",
    "listwiseCrossEntropy",
    "pointwiseHuber",
)

FAMILYWISE_ALPHA = 0.01
MAX_ADMISSIBLE_ATTEMPT_INDEX = 377
FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX = 378
PRACTICAL_NULL_ELO = 0.0
FORMAL_HCE_NULL_ELO = 15.0
MINIMUM_PRACTICAL_PAIRS = 128
MAXIMUM_PRACTICAL_PAIRS = 512
PAIRS_PER_CELL = MAXIMUM_PRACTICAL_PAIRS // len(CELLS)
CHECKPOINT_BLOCK = len(CELLS)
FUTILITY_E_VALUE = 20.0
BET_FRACTIONS = (
    1.0 / 128,
    1.0 / 64,
    1.0 / 32,
    1.0 / 16,
    1.0 / 8,
    1.0 / 4,
    1.0 / 2,
    3.0 / 4,
)
ENTROPY_BYTES = 32
ENTROPY_COMMITMENT_DOMAIN = b"omega-nnue-g6-confirmation-entropy-v1\x00"
SEED_HMAC_DOMAIN = b"omega-nnue-g6-confirmation-stage-seed-v1\x00"
OPENING_RANK_DOMAIN = b"omega-nnue-g6-confirmation-opening-rank-v1\x00"

HELDOUT_RULE = {
    "macroMaximumRegression": {
        "topSetAccuracy": 0.0,
        "meanChosenMoveRegretCp": 0.0,
        "listwiseCrossEntropy": 0.0,
        "pointwiseHuber": 0.0,
    },
    "cellMaximumRegression": {
        "topSetAccuracy": 0.01,
        "meanChosenMoveRegretCp": 5.0,
    },
    "minimumStrictMacroImprovements": 2,
    "requireStrictDecisionMetricImprovement": True,
    "decisionMetrics": ["topSetAccuracy", "meanChosenMoveRegretCp"],
    "comparisonTolerance": 1e-12,
    "nominationOnly": True,
}

CAPSULE_CLOSURE_DECLARATION = {
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

SAFETY_COUNTERS = (
    "illegalMoves",
    "engineCrashes",
    "searchTimeouts",
    "timeForfeits",
    "malformedResults",
    "assetMismatches",
    "diagnosticMismatches",
    "orphanProcesses",
)

ENGINE_OPTIONS = {
    "UCI_Variant": "omega",
    "UCI_Chess960": "false",
    "UseOmegaNNUE": "true",
    "Hash": "64",
    "Threads": "1",
    "Ponder": "false",
}
OMEGA_START = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
    "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
)

REPO = Path(__file__).resolve().parents[2]
TOOL_PATH = Path(__file__).resolve()
PROTOCOL_PATH = REPO / "validation/omega-nnue-generation6-promotion-protocol.json"
TRAINING_PROTOCOL_PATH = REPO / "validation/omega-nnue-king-state-v6-training-protocol.json"
DEFAULT_G6_NAMESPACE = REPO / "build-msvc/king-state-v6"
DEFAULT_ARTIFACT_ROOT = REPO / "build-msvc/king-state-v6-confirmation"
DEFAULT_V2_ATTEMPT_ROOT = REPO / "build-king-state-confirmation-v2/attempts"
DEFAULT_G5_AUTHORIZATION = (
    REPO / "build-king-state-v5/matches-color-compat-v2/sealed/match-authorization.json"
).resolve()
DEFAULT_G5_CLOSURE = (
    REPO / "build-king-state-v5/matches-color-compat-v2/closure.json"
).resolve()
_LOCK_TOKEN = hashlib.sha256(str(REPO).casefold().encode("utf-8")).hexdigest()[:16]
GLOBAL_OPERATION_LOCK_PATH = (
    Path(tempfile.gettempdir()) / f"omega-confirmation-v1-{_LOCK_TOKEN}.lock"
).resolve()
_LOCK_LOCAL = threading.local()

PINNED_AUTHORITIES: Mapping[str, tuple[str, int, str]] = {
    "g6TrainingProtocol": (
        "validation/omega-nnue-king-state-v6-training-protocol.json",
        34_982,
        "c3e034fb51483d72950fb6bb46977f658b8b55b8c24568e05172bb37601b66a1",
    ),
    "v2ProtocolJson": (
        "validation/omega-nnue-open-confirmation-v2-protocol.json",
        47_861,
        "9b2a558893806af89b60f9c3a96f36934cc7023b192758e436bfcb715e4cdc49",
    ),
    "v2ProtocolTool": (
        "tools/omega_nnue/king_state_confirmation_protocol_v2.py",
        126_760,
        "c1e1549cb6d7c8de208753411d1c143480f567d32cb25781e371b9df4a131671",
    ),
    "v2Readiness": (
        "tools/omega_nnue/king_state_confirmation_readiness_v2.py",
        359_123,
        "8ed4092e340fb7f34ce0435d6cfa1913580cc94c49d151972affbc1088793a36",
    ),
    "v2Practical": (
        "tools/omega_nnue/king_state_confirmation_practical_v2.py",
        82_703,
        "7b96851522bd73d5ffbe21fa10f0a7d39864b1be1c18877a9f3e0a0a4116264d",
    ),
    "v2Matches": (
        "tools/omega_nnue/king_state_confirmation_matches_v2.py",
        251_272,
        "e6593acdedaeff38244279f72b7f8dd866aef86001b2ee7e4b6d5a22edcb54cc",
    ),
    "hceLaunchAdapter": (
        "tools/omega_nnue/king_state_confirmation_generation6_hce_adapter.py",
        91_619,
        "9ad7ad3dd3ec758958ae87e19058c353e498fcc1f7b7fd02cde41487234fadc2",
    ),
    "practicalMatchAdapter": (
        "tools/omega_nnue/king_state_confirmation_generation6_practical_adapter.py",
        123_071,
        "f19d3b4f8c868566ba57915ce1cc278f69454673dfd3f8fe9305d5dc3b59f1c8",
    ),
}

_HCE_ADAPTER_MODULE: types.ModuleType | None = None
_HCE_ADAPTER_VERIFY: Callable[..., dict[str, Any]] | None = None
_HCE_ADAPTER_HISTORICAL_VERIFY: Callable[..., dict[str, Any]] | None = None
_PRACTICAL_ADAPTER_MODULE: types.ModuleType | None = None
_PRACTICAL_ADAPTER_BINDINGS: dict[str, Callable[..., Any]] = {}
_G6_TRAINER_MODULE: types.ModuleType | None = None
_G6_TRAINER_IDENTITY: dict[str, Any] | None = None
_G6_TRAINER_BINDINGS: dict[str, Callable[..., Any]] = {}

PRACTICAL_ADAPTER_AUTHORITY: Mapping[str, Any] = {
    "schemaVersion": 1,
    "adapterId": "omega-nnue-generation6-practical-match-adapter-v1",
    "implementation": {
        "path": str(
            (
                REPO
                / "tools/omega_nnue/king_state_confirmation_generation6_practical_adapter.py"
            ).resolve()
        ),
        "bytes": 123_071,
        "sha256": "f19d3b4f8c868566ba57915ce1cc278f69454673dfd3f8fe9305d5dc3b59f1c8",
    },
    "executionPolicy": {
        "mode": "nodes",
        "nodesPerMove": 30_000,
        "searchTimeoutMs": 60_000,
        "maxPlies": 300,
        "absoluteMaxPlies": 400,
        "stopGraceMs": 2_000,
        "freshProcessPerGame": True,
        "oneGameAtATime": True,
        "maximumConcurrentGames": 1,
        "repeats": 1,
        "pairsPerLaunchCheckpoint": 8,
        "maximumLaunchAttemptsPerPair": 3,
        "threadsPerEngine": 1,
        "harnessPairTimeoutMs": 48_120_000,
        "openingExecutionSeed": 0,
        "openingExecutionSeedIsStatisticallyInertForSingletonSuite": True,
        "hiddenSelectorSeedReadOrPublished": False,
    },
    "eventKind": EVENT_KIND,
    "executionSealKind": (
        "omega-nnue-generation6-practical-match-adapter-v1-execution-seal"
    ),
    "executionTranscriptKind": (
        "omega-nnue-generation6-practical-match-adapter-v1-execution-transcript"
    ),
    "incidentKind": "omega-nnue-generation6-practical-match-adapter-v1-incident",
    "incidentTerminalHandoffKind": (
        "omega-nnue-generation6-practical-match-adapter-v1-incident-terminal-handoff"
    ),
    "decisionPublisherApi": (
        "publish_practical_decision_from_execution_transcript"
    ),
    "decisionVerifierApi": "verify_practical_decision_from_execution_transcript",
    "incidentPublisherApi": "publish_practical_incident_failure",
    "incidentVerifierApi": "verify_practical_incident_failure",
    "successorPinnedAuthoritiesKey": "practicalMatchAdapter",
    "historicalAttemptScannerReplaysAdapterEvidence": True,
    "resultInformationRead": False,
    "finalStageSeal": True,
}

HEX256 = re.compile(r"^[0-9a-f]{64}$")
UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
MOVE = re.compile(r"^[a-jw][0-9][a-jw][0-9][qrbncw]?$", re.IGNORECASE)


def _set_region_lock(stream: Any, *, acquire: bool) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK
        msvcrt.locking(stream.fileno(), mode, 1)
    else:  # pragma: no cover - exercised on non-Windows CI
        import fcntl

        mode = fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN
        fcntl.flock(stream.fileno(), mode)


@contextlib.contextmanager
def _operation_lock() -> Iterator[None]:
    """Share v2's exact cross-process transition lock."""

    active = getattr(_LOCK_LOCAL, "active", None)
    if active is not None:
        active["depth"] += 1
        try:
            yield
        finally:
            active["depth"] -= 1
        return
    GLOBAL_OPERATION_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _verify_directory_chain(GLOBAL_OPERATION_LOCK_PATH.parent)
    descriptor = os.open(
        GLOBAL_OPERATION_LOCK_PATH,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        lock_fd = os.fstat(descriptor)
        lock_path = os.lstat(GLOBAL_OPERATION_LOCK_PATH)
        if (
            not stat.S_ISREG(lock_fd.st_mode)
            or lock_fd.st_nlink != 1
            or not _same_file_object(lock_fd, lock_path)
            or _unsafe_linklike(GLOBAL_OPERATION_LOCK_PATH, lock_path)
        ):
            raise RuntimeError("confirmation operation lock is unsafe")
    except BaseException:
        os.close(descriptor)
        raise
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    locked = False
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            os.fsync(stream.fileno())
        try:
            _set_region_lock(stream, acquire=True)
            locked = True
        except OSError as error:
            raise RuntimeError(
                "another open-confirmation command is already active"
            ) from error
        token = hashlib.sha256(
            (
                f"{os.getpid()}\0{threading.get_ident()}\0{time.monotonic_ns()}\0"
                f"{GLOBAL_OPERATION_LOCK_PATH}"
            ).encode("utf-8")
        ).hexdigest()
        stream.seek(0)
        stream.write(b"0")
        stream.truncate(1)
        stream.seek(1)
        stream.write((token + "\n").encode("ascii"))
        os.fsync(stream.fileno())
        _LOCK_LOCAL.active = {"depth": 1, "stream": stream}
        try:
            yield
        finally:
            current = getattr(_LOCK_LOCAL, "active", None)
            if current is None or current["depth"] != 1 or current["stream"] is not stream:
                raise RuntimeError("confirmation operation-lock nesting changed")
            delattr(_LOCK_LOCAL, "active")
    finally:
        if locked:
            try:
                _set_region_lock(stream, acquire=False)
            except OSError:
                pass
        stream.close()


def _serialized_transition(function: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with _operation_lock():
            return function(*args, **kwargs)

    return wrapped


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
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


def _type_exact_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _type_exact_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _type_exact_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _exact_keys(value: Any, fields: Iterable[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{label} field inventory changed")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or UTC.fullmatch(value) is None:
        raise ValueError(f"{label} is not canonical RFC3339 UTC")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    return parsed


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _lexical_absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _is_reparse(info: os.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & attribute)


def _unsafe_linklike(path: Path, info: os.stat_result | None = None) -> bool:
    current = os.lstat(path) if info is None else info
    return (
        stat.S_ISLNK(current.st_mode)
        or _is_reparse(current)
        or path.is_symlink()
        or (hasattr(path, "is_junction") and path.is_junction())
    )


def _same_open_file(left: os.stat_result, right: os.stat_result) -> bool:
    stable = (
        stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )
    if left.st_ino and right.st_ino:
        stable = stable and left.st_ino == right.st_ino and left.st_dev == right.st_dev
    return stable


def _same_file_object(left: os.stat_result, right: os.stat_result) -> bool:
    stable = (
        stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
        and left.st_nlink == right.st_nlink
    )
    if left.st_ino and right.st_ino:
        stable = stable and left.st_ino == right.st_ino and left.st_dev == right.st_dev
    return stable


def _verify_directory_chain(path: Path) -> None:
    cursor = path
    while True:
        info = os.lstat(cursor)
        if not stat.S_ISDIR(info.st_mode) or _unsafe_linklike(cursor, info):
            raise ValueError(f"unsafe directory ancestor: {cursor}")
        if cursor.parent == cursor:
            return
        cursor = cursor.parent


def _snapshot(path: Path | str) -> tuple[Path, bytes, os.stat_result]:
    """Read one immutable descriptor snapshot and reject path/descriptor swaps."""

    target = _lexical_absolute(path)
    _verify_directory_chain(target.parent)
    before = os.lstat(target)
    if (
        not stat.S_ISREG(before.st_mode)
        or _unsafe_linklike(target, before)
        or before.st_nlink != 1
    ):
        raise ValueError(f"not a unique regular non-link file: {target}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags)
    try:
        opened = os.fstat(descriptor)
        after_open = os.lstat(target)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not _same_open_file(before, opened)
            or not _same_open_file(opened, after_open)
            or _unsafe_linklike(target, after_open)
        ):
            raise ValueError(f"file changed between path validation and open: {target}")
        chunks: list[bytes] = []
        while block := os.read(descriptor, 1024 * 1024):
            chunks.append(block)
        final_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final_path = os.lstat(target)
    if (
        not _same_open_file(opened, final_fd)
        or not _same_open_file(final_fd, final_path)
        or _unsafe_linklike(target, final_path)
        or final_path.st_nlink != 1
    ):
        raise ValueError(f"file changed during descriptor snapshot: {target}")
    payload = b"".join(chunks)
    if len(payload) != final_fd.st_size:
        raise ValueError(f"descriptor snapshot length changed: {target}")
    return target, payload, final_fd


def _snapshot_bytes(path: Path | str) -> bytes:
    return _snapshot(path)[1]


def _safe_file(path: Path | str) -> Path:
    return _snapshot(path)[0]


def _sha256(path: Path | str) -> str:
    return hashlib.sha256(_snapshot_bytes(path)).hexdigest()


def identity(path: Path | str) -> dict[str, Any]:
    target, payload, info = _snapshot(path)
    return {
        "path": str(target),
        "bytes": info.st_size,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _identity_shape(value: Any, label: str) -> dict[str, Any]:
    _exact_keys(value, {"path", "bytes", "sha256"}, label)
    if (
        type(value["path"]) is not str
        or type(value["bytes"]) is not int
        or value["bytes"] < 0
        or type(value["sha256"]) is not str
        or HEX256.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity shape changed")
    return dict(value)


def verify_identity(value: Any, label: str) -> Path:
    expected = _identity_shape(value, label)
    path = _lexical_absolute(expected["path"])
    if not _type_exact_equal(identity(path), expected):
        raise ValueError(f"{label} identity mismatch")
    return path


def _load_json(path: Path | str, label: str) -> dict[str, Any]:
    path, payload, _ = _snapshot(path)
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"nonfinite JSON token {token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {label}: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object")
    return value


def _mkdir(path: Path) -> None:
    path = _lexical_absolute(path)
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    _verify_directory_chain(cursor)
    for item in reversed(missing):
        os.mkdir(item, 0o755)
        info = os.lstat(item)
        if not stat.S_ISDIR(info.st_mode) or _unsafe_linklike(item, info):
            raise ValueError(f"unsafe created directory: {item}")


def _exclusive_bytes(path: Path | str, payload: bytes, *, mode: int = 0o644) -> dict[str, Any]:
    path = _lexical_absolute(path)
    _mkdir(path.parent)
    _verify_directory_chain(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, mode)
    descriptor_open = True
    try:
        opened = os.fstat(descriptor)
        published = os.lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not _same_file_object(opened, published)
            or _unsafe_linklike(path, published)
        ):
            raise ValueError("exclusive publication target changed during creation")
        stream = os.fdopen(descriptor, "wb", closefd=True)
        descriptor_open = False
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            final_fd = os.fstat(stream.fileno())
    except BaseException:
        # Never erase a final-name partial: its existence consumes the slot.
        if descriptor_open:
            os.close(descriptor)
        raise
    _verify_directory_chain(path.parent)
    final_path = os.lstat(path)
    if (
        not _same_file_object(opened, final_fd)
        or not _same_file_object(final_fd, final_path)
        or final_fd.st_nlink != 1
        or final_fd.st_size != len(payload)
        or _unsafe_linklike(path, final_path)
    ):
        raise ValueError("exclusive publication target changed during write")
    expected = {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    actual = identity(path)
    if not _type_exact_equal(actual, expected):
        raise ValueError("exclusive publication bytes changed after fsync")
    return actual


def _exclusive_json(path: Path | str, value: Mapping[str, Any]) -> dict[str, Any]:
    return _exclusive_bytes(path, _canonical_json(dict(value)))


def _relative_identity(path: Path) -> dict[str, Any]:
    value = identity(path)
    try:
        value["relativePath"] = path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        value["relativePath"] = None
    return value


def _verify_pinned_authorities() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, (relative, size, digest) in PINNED_AUTHORITIES.items():
        path = _lexical_absolute(REPO / relative)
        actual = identity(path)
        if actual["bytes"] != size or actual["sha256"] != digest:
            raise ValueError(f"pinned authority changed: {name}")
        result[name] = actual
    return result


def _hce_launch_adapter() -> types.ModuleType:
    """Descriptor-load the exact adapter pinned by this successor."""

    global _HCE_ADAPTER_MODULE, _HCE_ADAPTER_VERIFY, _HCE_ADAPTER_HISTORICAL_VERIFY
    relative, size, digest = PINNED_AUTHORITIES["hceLaunchAdapter"]
    path = _lexical_absolute(REPO / relative)
    payload = _snapshot_bytes(path)
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise ImportError("pinned G6 HCE launch adapter changed")
    if _HCE_ADAPTER_MODULE is None:
        module_name = f"{__name__}.__hce_launch_adapter"
        if module_name in sys.modules:
            raise ImportError("refusing a preloaded G6 HCE launch adapter")
        module = types.ModuleType(module_name)
        module.__file__ = str(path)
        module.__package__ = ""
        module.__loader__ = None
        sys.modules[module_name] = module
        try:
            exec(compile(payload, str(path), "exec"), module.__dict__)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        _HCE_ADAPTER_MODULE = module
        _HCE_ADAPTER_VERIFY = module.verify_attempt_closure
        _HCE_ADAPTER_HISTORICAL_VERIFY = module.verify_historical_attempt_closure
    module = _HCE_ADAPTER_MODULE
    if (
        _lexical_absolute(str(module.__file__)) != path
        or identity(path)["bytes"] != size
        or identity(path)["sha256"] != digest
        or getattr(module, "TOOL_PATH", None) != path
        or getattr(module, "ADAPTER_ID", None)
        != "omega-nnue-generation6-frozen-v2-hce-adapter-v1"
        or not callable(getattr(module, "verify_attempt_closure", None))
        or module.verify_attempt_closure is not _HCE_ADAPTER_VERIFY
        or module.verify_attempt_closure.__module__ != module.__name__
        or not callable(getattr(module, "verify_historical_attempt_closure", None))
        or module.verify_historical_attempt_closure
        is not _HCE_ADAPTER_HISTORICAL_VERIFY
        or module.verify_historical_attempt_closure.__module__ != module.__name__
    ):
        raise ImportError("pinned G6 HCE adapter binding changed")
    return module


def _practical_launch_adapter() -> types.ModuleType:
    """Descriptor-load and bind the exact practical execution authority."""

    global _PRACTICAL_ADAPTER_MODULE
    relative, size, digest = PINNED_AUTHORITIES["practicalMatchAdapter"]
    path = _lexical_absolute(REPO / relative)
    payload = _snapshot_bytes(path)
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise ImportError("pinned G6 practical launch adapter changed")
    if _PRACTICAL_ADAPTER_MODULE is None:
        module_name = f"{__name__}.__practical_launch_adapter"
        if module_name in sys.modules:
            raise ImportError("refusing a preloaded G6 practical launch adapter")
        module = types.ModuleType(module_name)
        module.__file__ = str(path)
        module.__package__ = ""
        module.__loader__ = None
        sys.modules[module_name] = module
        try:
            exec(compile(payload, str(path), "exec"), module.__dict__)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        for name in (
            "expected_successor_adapter_authority",
            "verify_execution_transcript",
            "verify_incident_terminal_handoff",
        ):
            function = getattr(module, name, None)
            if not callable(function) or function.__module__ != module_name:
                sys.modules.pop(module_name, None)
                raise ImportError(f"practical adapter callable changed: {name}")
            _PRACTICAL_ADAPTER_BINDINGS[name] = function
        _PRACTICAL_ADAPTER_MODULE = module
    module = _PRACTICAL_ADAPTER_MODULE
    if (
        _lexical_absolute(str(module.__file__)) != path
        or identity(path)["bytes"] != size
        or identity(path)["sha256"] != digest
        or any(
            getattr(module, name, None) is not function
            for name, function in _PRACTICAL_ADAPTER_BINDINGS.items()
        )
        or not _type_exact_equal(
            module.expected_successor_adapter_authority(),
            PRACTICAL_ADAPTER_AUTHORITY,
        )
    ):
        raise ImportError("pinned G6 practical adapter binding changed")
    return module


def _g6_trainer_authority(prereg: Mapping[str, Any]) -> types.ModuleType:
    """Descriptor-load the exact trainer contract cited by the G6 genesis."""

    global _G6_TRAINER_MODULE, _G6_TRAINER_IDENTITY
    g6_prereg_path = verify_identity(
        prereg["g6"]["preregistration"], "G6 trainer preregistration"
    )
    g6_prereg = _load_json(g6_prereg_path, "G6 trainer preregistration")
    contract = _identity_shape(
        g6_prereg.get("contractSource"), "G6 trainer contract source"
    )
    source_path = verify_identity(contract, "G6 trainer contract source")
    canonical = _lexical_absolute(
        REPO / "tools/omega_nnue/king_state_train_generation6.py"
    )
    if source_path != canonical:
        raise ValueError("G6 preregistration cites a noncanonical trainer contract")
    source = _snapshot_bytes(source_path)
    if (
        len(source) != contract["bytes"]
        or hashlib.sha256(source).hexdigest() != contract["sha256"]
    ):
        raise ValueError("G6 trainer changed after its preregistration")
    if _G6_TRAINER_MODULE is None:
        module_name = f"{__name__}.__g6_trainer_authority"
        if module_name in sys.modules:
            raise ImportError("refusing a preloaded G6 trainer authority")
        module = types.ModuleType(module_name)
        module.__file__ = str(source_path)
        module.__package__ = ""
        module.__loader__ = None
        sys.modules[module_name] = module
        try:
            exec(compile(source, str(source_path), "exec"), module.__dict__)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        for name in ("verify_canonical_namespace", "_verify_heldout_closure"):
            function = getattr(module, name, None)
            if not callable(function) or function.__module__ != module_name:
                sys.modules.pop(module_name, None)
                raise ImportError(f"G6 trainer callable changed: {name}")
            _G6_TRAINER_BINDINGS[name] = function
        _G6_TRAINER_MODULE = module
        _G6_TRAINER_IDENTITY = dict(contract)
    module = _G6_TRAINER_MODULE
    if (
        not _type_exact_equal(_G6_TRAINER_IDENTITY, contract)
        or _lexical_absolute(str(module.__file__)) != source_path
        or not _type_exact_equal(identity(source_path), contract)
        or any(
            getattr(module, name, None) is not function
            for name, function in _G6_TRAINER_BINDINGS.items()
        )
    ):
        raise ImportError("G6 trainer authority binding changed")
    return module


def _verify_trainer_heldout_authority(
    prereg: Mapping[str, Any], lineage: Mapping[str, Any]
) -> None:
    """Replay trainer-owned held-out namespace and compare successor evidence."""

    trainer = _g6_trainer_authority(prereg)
    registry = trainer.verify_canonical_namespace()
    if not _type_exact_equal(
        identity(registry.preregistration), prereg["g6"]["preregistration"]
    ):
        raise ValueError("trainer canonical namespace differs from successor G6 genesis")
    trainer_closure = trainer._verify_heldout_closure(registry)
    slots = {
        "access": trainer._canonical_slot(registry, "heldoutAccess"),
        "claim": trainer._canonical_slot(registry, "heldoutClaim"),
        "report": trainer._canonical_slot(registry, "heldoutReport"),
        "closure": trainer._canonical_slot(registry, "heldoutClosure"),
    }
    if any(
        not _type_exact_equal(identity(slots[name]), identity(lineage["paths"][name]))
        for name in slots
    ):
        raise ValueError("trainer held-out identities differ from successor lineage")
    trainer_report = trainer._load_json(slots["report"], "trainer held-out report")
    if (
        not _type_exact_equal(trainer_closure, lineage["closure"])
        or not _type_exact_equal(
            trainer_report.get("modelMetrics"), lineage["report"].get("modelMetrics")
        )
    ):
        raise ValueError("trainer held-out closure or aggregate metrics diverged")


def _protocol_document() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PROTOCOL_KIND,
        "protocolId": PROTOCOL_ID,
        "status": "frozen-before-heldout-access",
        "purpose": (
            "nominate one exact G6 deployment, prove practical superiority to the "
            "G5 incumbent, then preserve the same global attempt and alpha share "
            "through all three >+15 Elo HCE gates"
        ),
        "prerequisiteOrder": [
            "G6 capsule and fresh-verification receipt",
            "G6 validation selection",
            "G6 robustness seal",
            "merged Senpai executable/network deployment bundle and startup probe",
            "successor preregistration",
            "G6 heldout access, claim, aggregate report, and closure",
            "fixed heldout decision",
            "paired G6-vs-G5 practical attempt",
            "three frozen HCE formal gates",
        ],
        "heldoutRule": HELDOUT_RULE,
        "practicalExecutionAuthority": PRACTICAL_ADAPTER_AUTHORITY,
        "practicalGate": {
            "candidate": "selected G6 primary deployment",
            "control": "exact previously promoted G5 incumbent deployment",
            "nullElo": PRACTICAL_NULL_ELO,
            "minimumPairs": MINIMUM_PRACTICAL_PAIRS,
            "maximumPairs": MAXIMUM_PRACTICAL_PAIRS,
            "checkpointEveryPairs": CHECKPOINT_BLOCK,
            "cells": list(CELLS),
            "pairsPerCell": PAIRS_PER_CELL,
            "colors": "each selected opening is one AB color-swapped pair",
            "source": "candidate-blind phase-by-side balanced opening pool",
            "safetyCounters": list(SAFETY_COUNTERS),
            "zeroSafetyFailuresRequired": True,
            "futilityEValue": FUTILITY_E_VALUE,
            "betFractions": list(BET_FRACTIONS),
        },
        "formalHceHandoff": {
            "gates": list(FORMAL_HCE_GATES),
            "nullEloSeparatelyForEveryGate": FORMAL_HCE_NULL_ELO,
            "sameGlobalAttemptIndex": True,
            "sameAttemptAlphaShare": True,
            "allThreePromotionsRequired": True,
            "engineOptions": {
                "common": FORMAL_HCE_COMMON_OPTIONS,
                "candidate": {
                    "UseOmegaNNUE": "true",
                    "OmegaNNUEFile": "exact selected G6 network path",
                },
                "hceControl": {
                    "UseOmegaNNUE": "false",
                    "OmegaNNUEFile": "<empty>",
                },
            },
            "launchAdapter": {
                "consumesAuthenticatedHandoff": True,
                "implementation": {
                    "relativePath": PINNED_AUTHORITIES["hceLaunchAdapter"][0],
                    "bytes": PINNED_AUTHORITIES["hceLaunchAdapter"][1],
                    "sha256": PINNED_AUTHORITIES["hceLaunchAdapter"][2],
                },
                "frozenV2AuthoritiesRemainByteExact": True,
                "formalGateImplementationsRemainUnmodified": True,
                "candidateBlindV2SelectorWorker": True,
                "developmentGateSatisfiedOnlyByAuthenticatedPracticalPromotion": True,
                "formalGateOrderStrict": True,
                "rawEntropyOrHmacKeysPublished": False,
                "stageSeedsDerivedFromPrecommittedHmacKeys": True,
                "stageSeedsDisclosedOnlyAfterDurableReservation": True,
                "staticSelectorBaseInitializedExplicitlyBeforeAdapterReservation": True,
                "bridgeRequiresTerminalDecisionAndSafetyReplay": True,
                "terminalClosureBridgesBackToThisGlobalAttempt": True,
            },
            "implementations": {
                name: {"relativePath": relative, "bytes": size, "sha256": digest}
                for name, (relative, size, digest) in PINNED_AUTHORITIES.items()
                if name.startswith("v2")
            },
        },
        "alphaSpending": {
            "familywiseAlpha": FAMILYWISE_ALPHA,
            "attemptShare": "beta_k = 0.01 / 2**k for global attempt k",
            "promotionLogThreshold": "log(100) + k*log(2)",
            "conjunctionUsesNoAlphaResetOrBonferroni": True,
            "maximumAdmissibleGlobalAttemptIndex": MAX_ADMISSIBLE_ATTEMPT_INDEX,
            "firstStatisticallyExhaustedGlobalAttemptIndex": FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX,
            "exhaustedIndexIsRejectedNotAdvertisedAsASuccessor": True,
        },
        "entropy": {
            "bytes": ENTROPY_BYTES,
            "source": "secrets.token_bytes / OS CSPRNG",
            "drawsPerAttempt": 1,
            "reservationPrecedesDraw": True,
            "claimSuiteAndHandoffContainOnlyCommitments": True,
            "launchAdapterMayDiscloseOnlyDerivedInt32SeedsAfterReservation": True,
            "rawEntropyAndHmacKeysRemainPrivate": True,
            "derivation": "HMAC-SHA256 with protocol/domain/attempt/stage separation",
            "rerollPermitted": False,
        },
        "publication": {
            "mode": "final-name O_EXCL; no overwrite, repair, or alternate output",
            "consumedClaimOrSuitePublicationFailureUsesTerminalClosure": True,
            "heldoutResultReadBeforePreregistration": False,
            "formalHceMatchLaunchProvided": True,
            "historicalSuccessorClosuresReplayEveryCitedAuthority": True,
            "practicalMatchLaunchProvided": True,
            "thresholdMutationAfterResults": False,
            "unknownArtifactsAccepted": False,
        },
    }


def validate_protocol(path: Path = PROTOCOL_PATH, *, allow_noncanonical: bool = False) -> dict[str, Any]:
    if not allow_noncanonical and path.resolve() != PROTOCOL_PATH.resolve():
        raise ValueError("noncanonical successor protocol path refused")
    actual = _load_json(path, "G6 promotion protocol")
    expected = _protocol_document()
    if not _type_exact_equal(actual, expected):
        raise ValueError("G6 promotion protocol differs from frozen implementation")
    _verify_pinned_authorities()
    return actual


def _artifact_paths(root: Path) -> dict[str, Path]:
    root = root.resolve()
    return {
        "root": root,
        "startupAttestation": root / "00-deployment/g6.startup-attestation.json",
        "deploymentBundle": root / "00-deployment/g6.bundle.json",
        "preregistration": root / "01-preregistration.json",
        "heldoutDecision": root / "02-heldout-decision.json",
        "attempts": root / "attempts",
    }


def _expected_heldout_paths(g6_namespace: Path) -> dict[str, str]:
    root = g6_namespace.resolve() / "05-heldout"
    return {
        "access": str((root / "access.json").resolve()),
        "claim": str((root / "claim.json").resolve()),
        "report": str((root / "aggregate-report.json").resolve()),
        "closure": str((root / "closure.json").resolve()),
    }


def _verify_header(
    document: Mapping[str, Any], *, kind: str, status: str, label: str
) -> None:
    if (
        type(document.get("schemaVersion")) is not int
        or document.get("schemaVersion") != SCHEMA_VERSION
        or document.get("kind") != kind
        or document.get("status") != status
    ):
        raise ValueError(f"{label} schema/kind/status changed")


def _verify_g6_prerequisite_chain(
    *,
    g6_preregistration: Path,
    capsule: Path,
    receipt: Path,
    selection: Path,
    robustness: Path,
    selected_network: Path,
    require_heldout_absent: bool,
) -> dict[str, Any]:
    prereg = _load_json(g6_preregistration, "G6 preregistration")
    if (
        prereg.get("kind") != G6_PREREGISTRATION_KIND
        or prereg.get("profileId") != G6_PROFILE_ID
        or prereg.get("status")
        != "frozen-single-lineage-before-generation6-training"
        or prereg.get("resultInformationRead") is not False
        or prereg.get("heldOutTargetRowsDecodedAtFreeze") != 0
        or prereg.get("heldOutTargetFieldsDecodedAtFreeze") != 0
    ):
        raise ValueError("G6 preregistration is not the frozen pre-result authority")
    if not _type_exact_equal(
        _identity_shape(prereg.get("protocol"), "G6 training protocol"),
        identity(TRAINING_PROTOCOL_PATH),
    ):
        raise ValueError("G6 preregistration does not bind the pinned training protocol")
    if not _type_exact_equal(
        _identity_shape(
            prereg.get("upstreamVerifierExecutable"),
            "G6 preregistered Python executable",
        ),
        identity(Path(sys.executable)),
    ):
        raise ValueError(
            "successor must run under the exact Python executable frozen by G6"
        )

    capsule_doc = _load_json(capsule, "G6 capsule")
    if (
        capsule_doc.get("kind") != G6_CAPSULE_KIND
        or capsule_doc.get("status") != "closed-pretarget-to-final-projection-lineage"
        or capsule_doc.get("resultInformationRead") is not False
        or capsule_doc.get("finalStageSeal") is not True
        or not _type_exact_equal(
            capsule_doc.get("closureDeclaration"), CAPSULE_CLOSURE_DECLARATION
        )
    ):
        raise ValueError("G6 capsule closure changed")
    if not _type_exact_equal(prereg.get("upstreamCapsule"), identity(capsule)):
        raise ValueError("G6 preregistration/capsule binding changed")

    receipt_doc = _load_json(receipt, "G6 fresh verifier receipt")
    receipt_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "capsule",
        "verifierExecutable",
        "verifierRunner",
        "verifierOptions",
        "initializerAuthority",
        "terminalAuthority",
        "priorForbiddenAuthority",
        "componentAuthority",
        "staticHceAuthority",
        "teacherLedgerAuthority",
        "projectionAuthority",
        "resultInformationRead",
    }
    _exact_keys(receipt_doc, receipt_fields, "G6 fresh verifier receipt")
    if (
        receipt_doc.get("kind") != G6_RECEIPT_KIND
        or receipt_doc.get("profileId") != G6_PROFILE_ID
        or receipt_doc.get("status") != "passed-fresh-semantic-replay"
        or receipt_doc.get("resultInformationRead") is not False
        or not _type_exact_equal(receipt_doc.get("capsule"), identity(capsule))
        or not _type_exact_equal(
            receipt_doc.get("verifierExecutable"),
            prereg.get("upstreamVerifierExecutable"),
        )
        or not _type_exact_equal(
            receipt_doc.get("verifierRunner"), capsule_doc.get("upstreamVerifierRunner")
        )
        or not _type_exact_equal(
            receipt_doc.get("verifierOptions"), capsule_doc.get("upstreamVerifierOptions")
        )
    ):
        raise ValueError("G6 fresh verifier receipt changed")
    initializer = receipt_doc.get("initializerAuthority")
    terminal = receipt_doc.get("terminalAuthority")
    forbidden = receipt_doc.get("priorForbiddenAuthority")
    component = receipt_doc.get("componentAuthority")
    static_hce = receipt_doc.get("staticHceAuthority")
    teacher = receipt_doc.get("teacherLedgerAuthority")
    projection = receipt_doc.get("projectionAuthority")
    _exact_keys(
        initializer,
        {
            "manifest",
            "selectionMode",
            "selectedCatalogIndex",
            "selectedModel",
            "catalogSourceIds",
            "firstEligibleSelected",
            "selectionSemanticsVerified",
            "selectedPromotionHealthPassed",
            "sourceClosureSemanticsVerified",
            "fallbackProtocolReplayed",
            "g6TargetRowsDecoded",
            "resultInformationRead",
        },
        "receipt initializer authority",
    )
    if (
        initializer["firstEligibleSelected"] is not True
        or initializer["selectionSemanticsVerified"] is not True
        or initializer["sourceClosureSemanticsVerified"] is not True
        or initializer["g6TargetRowsDecoded"] != 0
        or initializer["resultInformationRead"] is not False
    ):
        raise ValueError("receipt initializer semantics changed")
    _exact_keys(
        terminal,
        {
            "lineage",
            "routedChildren",
            "terminalChildrenExcludedBeforeRouting",
            "unclassifiedChildren",
            "errorTextAcceptedAsTerminal",
            "rulesSemanticsReplayed",
            "completionSemanticsVerified",
        },
        "receipt terminal authority",
    )
    if (
        terminal["terminalChildrenExcludedBeforeRouting"] is not True
        or terminal["unclassifiedChildren"] != 0
        or terminal["errorTextAcceptedAsTerminal"] is not False
        or terminal["rulesSemanticsReplayed"] is not True
        or terminal["completionSemanticsVerified"] is not True
    ):
        raise ValueError("receipt terminal semantics changed")
    _exact_keys(
        forbidden,
        {
            "catalogs",
            "registry",
            "requiredSourceIds",
            "catalogPositions",
            "manifestsSemanticallyReplayed",
            "exactPositionOverlaps",
            "conservativeSignatureOverlaps",
            "sourceArtifactOverlaps",
        },
        "receipt forbidden authority",
    )
    if (
        forbidden["manifestsSemanticallyReplayed"] is not True
        or forbidden["exactPositionOverlaps"] != 0
        or forbidden["conservativeSignatureOverlaps"] != 0
        or forbidden["sourceArtifactOverlaps"] != 0
    ):
        raise ValueError("receipt prior-forbidden semantics changed")
    _exact_keys(
        component,
        {"componentMap", "roots", "components", "wholeComponentSplits", "semanticsReplayed"},
        "receipt component authority",
    )
    if component["wholeComponentSplits"] is not True or component["semanticsReplayed"] is not True:
        raise ValueError("receipt component semantics changed")
    _exact_keys(
        static_hce,
        {
            "claim",
            "completion",
            "teacherClaim",
            "prelabelSeal",
            "targetFreeRouting",
            "engine",
            "runner",
            "options",
            "transcript",
            "inputOrderSha256",
            "rows",
            "perspective",
            "freshReplayMatches",
            "completedBeforeTeacherClaim",
            "semanticsReplayed",
        },
        "receipt static-HCE authority",
    )
    if any(static_hce[field] is not True for field in ("freshReplayMatches", "completedBeforeTeacherClaim", "semanticsReplayed")):
        raise ValueError("receipt static-HCE semantics changed")
    _exact_keys(
        teacher,
        {
            "claim",
            "attemptLedger",
            "attemptLedgerCompletion",
            "completion",
            "budgets",
            "routedChildren",
            "attemptRecords",
            "successfulChildren",
            "rejectedChildren",
            "unresolvedChildren",
            "semanticsReplayed",
        },
        "receipt teacher authority",
    )
    if teacher["unresolvedChildren"] != 0 or teacher["semanticsReplayed"] is not True:
        raise ValueError("receipt teacher-ledger semantics changed")
    _exact_keys(
        projection,
        {
            "plannedProducer",
            "plannedCorpusPath",
            "plannedManifestPath",
            "actualProducer",
            "actualCorpus",
            "actualManifest",
            "producerMatches",
            "pathsMatch",
            "semanticsReplayed",
        },
        "receipt projection authority",
    )
    if any(projection[field] is not True for field in ("producerMatches", "pathsMatch", "semanticsReplayed")):
        raise ValueError("receipt projection semantics changed")

    selection_doc = _load_json(selection, "G6 selection")
    if (
        selection_doc.get("kind") != G6_SELECTION_KIND
        or selection_doc.get("profileId") != G6_PROFILE_ID
        or selection_doc.get("status") != "selected-from-fresh-canonical-replay"
        or selection_doc.get("resultInformationRead") is not False
        or selection_doc.get("heldOutTargetRowsDecodedAtSelection") != 0
        or selection_doc.get("heldOutTargetFieldsDecodedAtSelection") != 0
        or type(selection_doc.get("selectedCandidateId")) is not str
        or SAFE_ID.fullmatch(selection_doc["selectedCandidateId"]) is None
        or not _type_exact_equal(selection_doc.get("selectedModel"), identity(selected_network))
    ):
        raise ValueError("G6 primary selection changed")
    authority_manifest = verify_identity(
        selection_doc.get("authorityManifest"), "G6 selection authority manifest"
    )
    authority = _load_json(authority_manifest, "G6 authority manifest")
    if (
        not _type_exact_equal(authority.get("upstreamCapsule"), identity(capsule))
        or not _type_exact_equal(authority.get("upstreamVerification"), identity(receipt))
    ):
        raise ValueError("G6 materialized authority does not bind capsule/receipt")

    robustness_doc = _load_json(robustness, "G6 robustness seal")
    if (
        robustness_doc.get("kind") != G6_ROBUSTNESS_KIND
        or robustness_doc.get("profileId") != G6_PROFILE_ID
        or robustness_doc.get("status") != "passed-replayed-second-seed-confirmation"
        or robustness_doc.get("resultInformationRead") is not False
        or robustness_doc.get("passedDeploymentHealth") is not True
        or robustness_doc.get("heldOutTargetRowsDecodedAtSeal") != 0
        or robustness_doc.get("heldOutTargetFieldsDecodedAtSeal") != 0
        or robustness_doc.get("selectedCandidateId")
        != selection_doc["selectedCandidateId"]
        or not _type_exact_equal(robustness_doc.get("primaryModel"), identity(selected_network))
        or not _type_exact_equal(robustness_doc.get("validationSelection"), identity(selection))
    ):
        raise ValueError("G6 robustness seal changed")

    namespace = Path(prereg.get("namespace", "")).expanduser().resolve()
    expected_heldout = _expected_heldout_paths(namespace)
    if require_heldout_absent:
        for label, raw in expected_heldout.items():
            if os.path.lexists(raw):
                raise FileExistsError(
                    f"successor preregistration must precede G6 heldout {label}"
                )
    return {
        "preregistration": prereg,
        "capsule": capsule_doc,
        "receipt": receipt_doc,
        "selection": selection_doc,
        "robustness": robustness_doc,
        "selectedNetwork": identity(selected_network),
        "heldoutPaths": expected_heldout,
        "namespace": str(namespace),
    }


def _probe_engine(engine: Path, network: Path) -> dict[str, Any]:
    engine_before = identity(engine)
    network_before = identity(network)
    commands = [
        "uci",
        "setoption name UCI_Variant value omega",
        f"setoption name OmegaNNUEFile value {network}",
        "setoption name UseOmegaNNUE value true",
        "isready",
        f"position fen {OMEGA_START}",
        "go nodes 1",
        "quit",
    ]
    completed = subprocess.run(
        [str(engine)],
        input="\n".join(commands) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        cwd=engine.parent,
        env={},
        timeout=60,
        check=False,
    )
    if identity(engine) != engine_before or identity(network) != network_before:
        raise ValueError("deployment engine/network changed during startup probe")
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError(
            "deployment startup probe failed: "
            + (completed.stderr.strip() or f"exit {completed.returncode}")
        )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    loaded = [line for line in lines if line.startswith("info string Omega NNUE loaded:")]
    active = [line for line in lines if line == "info string Omega NNUE evaluation active"]
    bestmoves = [line for line in lines if line.startswith("bestmove ")]
    options = {
        line[len("option name ") :].split(" type ", 1)[0]
        for line in lines
        if line.startswith("option name ") and " type " in line
    }
    required_options = {"UCI_Variant", "OmegaNNUEFile", "UseOmegaNNUE"}
    if (
        "uciok" not in lines
        or "readyok" not in lines
        or len(loaded) != 1
        or str(network) not in loaded[0]
        or not active
        or len(bestmoves) != 1
        or len(bestmoves[0].split()) < 2
        or MOVE.fullmatch(bestmoves[0].split()[1]) is None
        or not required_options.issubset(options)
    ):
        raise ValueError("deployment startup diagnostics do not prove active Omega NNUE")
    return {
        "uciOk": True,
        "readyOk": True,
        "requiredOptions": sorted(required_options),
        "networkLoadedDiagnostic": loaded[0],
        "activeDiagnostic": "info string Omega NNUE evaluation active",
        "bestmove": bestmoves[0].split()[1].lower(),
        "searchNodes": 1,
    }


def _publish_deployment_bundle_with_probe(
    *,
    engine: Path,
    network: Path,
    g6_preregistration: Path,
    capsule: Path,
    receipt: Path,
    selection: Path,
    robustness: Path,
    engine_source: Path,
    build_manifest: Path,
    runtime_manifest: Path,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    probe_function: Callable[[Path, Path], Mapping[str, Any]],
) -> dict[str, Any]:
    """Private publication core; tests may supply a hermetic probe here only."""

    validate_protocol()
    paths = _artifact_paths(artifact_root)
    chain = _verify_g6_prerequisite_chain(
        g6_preregistration=g6_preregistration,
        capsule=capsule,
        receipt=receipt,
        selection=selection,
        robustness=robustness,
        selected_network=network,
        require_heldout_absent=True,
    )
    for occupied in (paths["startupAttestation"], paths["deploymentBundle"]):
        if os.path.lexists(occupied):
            raise FileExistsError(f"deployment slot already consumed: {occupied}")
    probe = dict(probe_function(engine.resolve(), network.resolve()))
    expected_probe_fields = {
        "uciOk",
        "readyOk",
        "requiredOptions",
        "networkLoadedDiagnostic",
        "activeDiagnostic",
        "bestmove",
        "searchNodes",
    }
    _exact_keys(probe, expected_probe_fields, "startup probe")
    if (
        probe["uciOk"] is not True
        or probe["readyOk"] is not True
        or probe["activeDiagnostic"] != "info string Omega NNUE evaluation active"
        or type(probe["networkLoadedDiagnostic"]) is not str
        or not probe["networkLoadedDiagnostic"].startswith("info string Omega NNUE loaded:")
        or str(network.resolve()) not in probe["networkLoadedDiagnostic"]
        or probe["searchNodes"] != 1
        or type(probe["bestmove"]) is not str
        or MOVE.fullmatch(probe["bestmove"]) is None
    ):
        raise ValueError("startup probe result changed")
    robust_created = _timestamp(chain["robustness"]["createdUtc"], "robustness createdUtc")
    created = _utc_now()
    if _timestamp(created, "startup createdUtc") <= robust_created:
        raise ValueError("startup attestation must follow robustness")
    attestation = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": STARTUP_ATTESTATION_KIND,
        "status": "passed-fresh-omega-nnue-startup-and-search",
        "createdUtc": created,
        "protocol": identity(PROTOCOL_PATH),
        "engine": identity(engine),
        "network": identity(network),
        "selection": identity(selection),
        "robustness": identity(robustness),
        "engineSource": identity(engine_source),
        "buildManifest": identity(build_manifest),
        "runtimeManifest": identity(runtime_manifest),
        "options": {**ENGINE_OPTIONS, "OmegaNNUEFile": str(network.resolve())},
        "probe": probe,
        "resultInformationRead": False,
    }
    attestation_identity = _exclusive_json(paths["startupAttestation"], attestation)
    bundle_created = _utc_now()
    if _timestamp(bundle_created, "bundle createdUtc") < _timestamp(
        created, "startup createdUtc"
    ):
        raise ValueError("deployment bundle chronology changed")
    bundle = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": DEPLOYMENT_BUNDLE_KIND,
        "status": "sealed-exact-g6-engine-network-deployment",
        "createdUtc": bundle_created,
        "protocol": identity(PROTOCOL_PATH),
        "g6Preregistration": identity(g6_preregistration),
        "capsule": identity(capsule),
        "freshVerificationReceipt": identity(receipt),
        "selection": identity(selection),
        "robustness": identity(robustness),
        "selectedCandidateId": chain["selection"]["selectedCandidateId"],
        "engine": identity(engine),
        "network": identity(network),
        "engineSource": identity(engine_source),
        "buildManifest": identity(build_manifest),
        "runtimeManifest": identity(runtime_manifest),
        "startupAttestation": attestation_identity,
        "options": {**ENGINE_OPTIONS, "OmegaNNUEFile": str(network.resolve())},
        "heldoutLineageExpectedPaths": chain["heldoutPaths"],
        "resultInformationRead": False,
    }
    return _exclusive_json(paths["deploymentBundle"], bundle)


def _bind_production_deployment_publisher(
    production_probe: Callable[[Path, Path], Mapping[str, Any]],
) -> Callable[..., dict[str, Any]]:
    """Close over the reviewed probe; do not expose it as caller input/state."""

    @_serialized_transition
    def publisher(
        *,
        engine: Path,
        network: Path,
        g6_preregistration: Path,
        capsule: Path,
        receipt: Path,
        selection: Path,
        robustness: Path,
        engine_source: Path,
        build_manifest: Path,
        runtime_manifest: Path,
        artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    ) -> dict[str, Any]:
        """Probe and bind the exact merged Senpai executable/network deployment."""

        return _publish_deployment_bundle_with_probe(
            engine=engine,
            network=network,
            g6_preregistration=g6_preregistration,
            capsule=capsule,
            receipt=receipt,
            selection=selection,
            robustness=robustness,
            engine_source=engine_source,
            build_manifest=build_manifest,
            runtime_manifest=runtime_manifest,
            artifact_root=artifact_root,
            probe_function=production_probe,
        )

    publisher.__name__ = "publish_deployment_bundle"
    publisher.__qualname__ = "publish_deployment_bundle"
    return publisher


publish_deployment_bundle = _bind_production_deployment_publisher(_probe_engine)


DEPLOYMENT_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "g6Preregistration",
    "capsule",
    "freshVerificationReceipt",
    "selection",
    "robustness",
    "selectedCandidateId",
    "engine",
    "network",
    "engineSource",
    "buildManifest",
    "runtimeManifest",
    "startupAttestation",
    "options",
    "heldoutLineageExpectedPaths",
    "resultInformationRead",
}


def verify_deployment_bundle(path: Path) -> dict[str, Any]:
    document = _load_json(path, "G6 deployment bundle")
    _exact_keys(document, DEPLOYMENT_FIELDS, "G6 deployment bundle")
    _verify_header(
        document,
        kind=DEPLOYMENT_BUNDLE_KIND,
        status="sealed-exact-g6-engine-network-deployment",
        label="G6 deployment bundle",
    )
    _timestamp(document["createdUtc"], "deployment bundle createdUtc")
    if (
        document["resultInformationRead"] is not False
        or not _type_exact_equal(document["protocol"], identity(PROTOCOL_PATH))
        or type(document["selectedCandidateId"]) is not str
        or SAFE_ID.fullmatch(document["selectedCandidateId"]) is None
        or not isinstance(document["options"], dict)
        or document["options"].get("UCI_Variant") != "omega"
        or document["options"].get("UseOmegaNNUE") != "true"
    ):
        raise ValueError("G6 deployment bundle policy changed")
    for field in (
        "g6Preregistration",
        "capsule",
        "freshVerificationReceipt",
        "selection",
        "robustness",
        "engine",
        "network",
        "engineSource",
        "buildManifest",
        "runtimeManifest",
        "startupAttestation",
    ):
        verify_identity(document[field], f"deployment {field}")
    selection = _load_json(document["selection"]["path"], "deployment selection")
    robustness = _load_json(document["robustness"]["path"], "deployment robustness")
    if (
        selection.get("selectedCandidateId") != document["selectedCandidateId"]
        or not _type_exact_equal(selection.get("selectedModel"), document["network"])
        or robustness.get("selectedCandidateId") != document["selectedCandidateId"]
        or not _type_exact_equal(robustness.get("primaryModel"), document["network"])
    ):
        raise ValueError("deployment selection/robustness/network binding changed")
    attestation = _load_json(
        document["startupAttestation"]["path"], "deployment startup attestation"
    )
    if (
        attestation.get("kind") != STARTUP_ATTESTATION_KIND
        or attestation.get("status") != "passed-fresh-omega-nnue-startup-and-search"
        or attestation.get("resultInformationRead") is not False
        or not _type_exact_equal(attestation.get("engine"), document["engine"])
        or not _type_exact_equal(attestation.get("network"), document["network"])
        or attestation.get("probe", {}).get("activeDiagnostic")
        != "info string Omega NNUE evaluation active"
    ):
        raise ValueError("deployment startup attestation changed")
    if _timestamp(document["createdUtc"], "deployment createdUtc") < _timestamp(
        attestation["createdUtc"], "startup createdUtc"
    ):
        raise ValueError("deployment predates startup attestation")
    return document


OPENING_ROW_FIELDS = {
    "schemaVersion",
    "kind",
    "openingId",
    "phase",
    "sideToMove",
    "ofen",
    "moves",
}
OPENING_MANIFEST_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "entries",
    "rows",
    "cellCounts",
    "candidateInformationRead",
    "gameResultsRead",
    "finalStageSeal",
}


def _read_opening_pool(manifest_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _load_json(manifest_path, "candidate-blind opening manifest")
    _exact_keys(manifest, OPENING_MANIFEST_FIELDS, "opening manifest")
    if (
        manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("kind") != "omega-nnue-candidate-blind-opening-pool-v1"
        or manifest.get("status") != "sealed-before-candidate-selection"
        or manifest.get("candidateInformationRead") is not False
        or manifest.get("gameResultsRead") is not False
        or manifest.get("finalStageSeal") is not True
    ):
        raise ValueError("opening manifest candidate-blind contract changed")
    _timestamp(manifest.get("createdUtc"), "opening manifest createdUtc")
    entries_path = verify_identity(manifest.get("entries"), "opening entries")
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    counts = Counter()
    try:
        entry_lines = _snapshot_bytes(entries_path).decode(
            "utf-8", errors="strict"
        ).splitlines(keepends=True)
    except UnicodeError as error:
        raise ValueError("opening entries are not strict UTF-8") from error
    for line_number, line in enumerate(entry_lines, 1):
        if not line.endswith(("\n", "\r")):
            raise ValueError(f"opening entries line {line_number} is incomplete")
        if not line.strip():
            raise ValueError(f"opening entries line {line_number} is blank")
        value = json.loads(line, object_pairs_hook=_reject_duplicate_pairs)
        _exact_keys(value, OPENING_ROW_FIELDS, f"opening row {line_number}")
        opening_id = value.get("openingId")
        phase = value.get("phase")
        side = value.get("sideToMove")
        moves = value.get("moves")
        if (
            value.get("schemaVersion") != SCHEMA_VERSION
            or value.get("kind") != "omega-nnue-candidate-blind-opening-v1"
            or type(opening_id) is not str
            or SAFE_ID.fullmatch(opening_id) is None
            or opening_id in ids
            or phase not in PHASES
            or side not in SIDES
            or type(value.get("ofen")) is not str
            or not value["ofen"].strip()
            or type(moves) is not list
            or any(
                type(move) is not str or MOVE.fullmatch(move) is None
                for move in moves
            )
        ):
            raise ValueError(f"opening row {line_number} changed")
        ids.add(opening_id)
        counts[f"{phase}:{side}"] += 1
        rows.append(value)
    expected_counts = {cell: counts[cell] for cell in CELLS}
    if (
        type(manifest.get("rows")) is not int
        or manifest["rows"] != len(rows)
        or not _type_exact_equal(manifest.get("cellCounts"), expected_counts)
        or any(counts[cell] < PAIRS_PER_CELL for cell in CELLS)
    ):
        raise ValueError("opening pool lacks the frozen balanced inventory")
    return manifest, rows


def _g5_replay_worker(authorization_path: Path, closure_path: Path) -> dict[str, Any]:
    """Descriptor-load frozen readiness and replay the complete G5 nomination."""

    relative, size, digest = PINNED_AUTHORITIES["v2Readiness"]
    source_path = _lexical_absolute(REPO / relative)
    source = _snapshot_bytes(source_path)
    if len(source) != size or hashlib.sha256(source).hexdigest() != digest:
        raise ValueError("pinned v2 readiness verifier changed")
    module_name = "__omega_g6_fresh_frozen_v2_readiness"
    if module_name in sys.modules:
        raise ImportError("fresh G5 replay worker has a preloaded readiness module")
    module = types.ModuleType(module_name)
    module.__file__ = str(source_path)
    module.__package__ = ""
    module.__loader__ = None
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(source_path), "exec"), module.__dict__)
        if (
            authorization_path.resolve() != module.G5_AUTHORIZATION.resolve()
            or closure_path.resolve() != module.G5_CLOSURE.resolve()
        ):
            raise ValueError("G5 authorization or closure is noncanonical")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            evidence = module._g5_nomination_evidence(
                module.G5_AUTHORIZATION,
                module.G5_DECISIONS,
                run_fresh_verifier=True,
            )
        return {
            "authorization": identity(module.G5_AUTHORIZATION),
            "closure": identity(module.G5_CLOSURE),
            "engine": evidence["engine"],
            "network": evidence["selectedNetwork"],
        }
    finally:
        sys.modules.pop(module_name, None)


def _fresh_g5_incumbent_replay(
    authorization_path: Path, closure_path: Path
) -> dict[str, Any]:
    """Run the descriptor replay in a clean isolated Python process."""

    if (
        authorization_path.resolve() != DEFAULT_G5_AUTHORIZATION
        or closure_path.resolve() != DEFAULT_G5_CLOSURE
    ):
        raise ValueError("G5 authorization or closure is noncanonical")
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(TOOL_PATH),
            "g5-replay-worker",
            "--authorization",
            str(authorization_path.resolve()),
            "--closure",
            str(closure_path.resolve()),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        cwd=TOOL_PATH.parent,
        env={},
        timeout=3600,
        check=False,
    )
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError(
            "fresh pinned G5 nomination replay failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    try:
        value = json.loads(
            completed.stdout,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"nonfinite JSON token {token}")
            ),
        )
    except json.JSONDecodeError as error:
        raise ValueError("fresh G5 replay emitted invalid JSON") from error
    _exact_keys(value, {"authorization", "closure", "engine", "network"}, "fresh G5 replay")
    for field in ("authorization", "closure", "engine", "network"):
        _identity_shape(value[field], f"fresh G5 replay {field}")
    return value


def _verify_g5_incumbent(
    authorization_path: Path, closure_path: Path
) -> dict[str, Any]:
    replay = _fresh_g5_incumbent_replay(authorization_path, closure_path)
    authorization = _load_json(authorization_path, "G5 incumbent authorization")
    if (
        authorization.get("kind")
        != "omega-nnue-king-state-v5-color-compat-authorization"
        or authorization.get("finalStageSeal") is not True
        or authorization.get("runnerUpFallback") is not False
    ):
        raise ValueError("G5 incumbent authorization changed")
    network = _identity_shape(
        authorization.get("selectedNetwork"), "G5 incumbent network"
    )
    engine = _identity_shape(authorization.get("engine"), "G5 incumbent engine")
    verify_identity(network, "G5 incumbent network")
    verify_identity(engine, "G5 incumbent engine")
    closure = _load_json(closure_path, "G5 incumbent closure")
    if (
        closure.get("kind") != "omega-nnue-king-state-v5-color-compat-closure"
        or closure.get("clearlySuperior") is not True
        or closure.get("finalStageSeal") is not True
        or closure.get("originalArtifactsRewritten") != 0
        or not _type_exact_equal(closure.get("authorization"), identity(authorization_path))
        or not _type_exact_equal(closure.get("selectedNetwork"), network)
        or type(closure.get("decisions")) is not dict
        or set(closure["decisions"]) != {"development", "equal-node", "equal-time"}
    ):
        raise ValueError("G5 incumbent is not the exact terminal promoted deployment")
    for gate, value in closure["decisions"].items():
        verify_identity(value, f"G5 {gate} decision")
    if (
        not _type_exact_equal(replay["authorization"], identity(authorization_path))
        or not _type_exact_equal(replay["closure"], identity(closure_path))
        or not _type_exact_equal(replay["network"], network)
        or not _type_exact_equal(replay["engine"], engine)
    ):
        raise ValueError("fresh G5 nomination replay differs from incumbent binding")
    return {
        "authorization": authorization,
        "closure": closure,
        "network": network,
        "engine": engine,
    }


def _v2_closure_shape(path: Path, expected_index: int) -> dict[str, Any]:
    value = _load_json(path, f"v2 attempt {expected_index} closure")
    fields = {
        "schemaVersion",
        "kind",
        "protocol",
        "attemptIndex",
        "createdUtc",
        "implementationSeal",
        "attemptReservation",
        "candidateClaim",
        "jointSuiteSeal",
        "authorization",
        "decisions",
        "outcome",
        "reason",
        "terminal",
        "nextAttemptAllowed",
        "candidateNetworkSha256",
    }
    _exact_keys(value, fields, "v2 attempt closure")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != V2_CLOSURE_KIND
        or value.get("attemptIndex") != expected_index
        or type(value.get("attemptIndex")) is not int
        or value.get("terminal") is not True
        or value.get("outcome") not in {"confirmed", "failed", "aborted"}
        or type(value.get("nextAttemptAllowed")) is not bool
        or (value["outcome"] == "confirmed") is value["nextAttemptAllowed"]
        or type(value.get("candidateNetworkSha256")) is not str
        or HEX256.fullmatch(value["candidateNetworkSha256"]) is None
        or not _type_exact_equal(value.get("protocol"), identity(REPO / PINNED_AUTHORITIES["v2ProtocolJson"][0]))
    ):
        raise ValueError("v2 attempt closure changed")
    _timestamp(value.get("createdUtc"), "v2 closure createdUtc")
    return value


def _run_v2_chain_verifier(through_attempt: int) -> None:
    """Replay the exact frozen-v2 readiness authority in a fresh process."""

    readiness_path = REPO / PINNED_AUTHORITIES["v2Readiness"][0]
    expected = PINNED_AUTHORITIES["v2Readiness"]
    current = identity(readiness_path)
    if current["bytes"] != expected[1] or current["sha256"] != expected[2]:
        raise ValueError("pinned v2 readiness verifier changed")
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(readiness_path),
            "verify-chain",
            "--through-attempt",
            str(through_attempt),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        cwd=readiness_path.parent,
        env={},
        timeout=3600,
        check=False,
    )
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError(
            "pinned v2 verifier rejected the canonical global attempt chain: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )


def _scan_v2_attempts(root: Path) -> list[dict[str, Any]]:
    root = _lexical_absolute(root)
    if root.resolve() != DEFAULT_V2_ATTEMPT_ROOT.resolve():
        raise ValueError("v2 attempt root is not the canonical frozen-v2 namespace")
    root = root.resolve()
    if not root.exists():
        return []
    _verify_directory_chain(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("v2 attempt root is unsafe")
    directories: dict[int, Path] = {}
    for child in root.iterdir():
        if not child.is_dir() or child.is_symlink():
            raise ValueError("unexpected v2 attempt-root artifact")
        match = re.fullmatch(r"attempt-([0-9]{6})", child.name)
        if match is None:
            raise ValueError("unexpected v2 attempt directory")
        index = int(match.group(1))
        if index < 1 or index in directories:
            raise ValueError("invalid or duplicate v2 attempt index")
        directories[index] = child
    if set(directories) != set(range(1, len(directories) + 1)):
        raise ValueError("v2 attempt chain has a gap")
    result: list[dict[str, Any]] = []
    for index in range(1, len(directories) + 1):
        closure = directories[index] / "attempt-closure.json"
        if not closure.is_file():
            raise ValueError(f"v2 attempt {index} is active or malformed")
        document = _v2_closure_shape(closure, index)
        if index < len(directories) and document["nextAttemptAllowed"] is not True:
            raise ValueError("v2 successor exists after a confirmed attempt")
        result.append({"identity": identity(closure), "document": document})
    if result:
        _run_v2_chain_verifier(len(result))
    return result


PREREGISTRATION_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "implementation",
    "pinnedAuthorities",
    "artifactRoot",
    "g6",
    "deploymentBundle",
    "g5Incumbent",
    "openingPool",
    "globalAttemptSnapshot",
    "heldoutRule",
    "practicalRule",
    "practicalExecutionAuthority",
    "hceHandoffRule",
    "resultInformationRead",
    "heldoutTargetRowsDecoded",
    "gameResultsRead",
    "finalStageSeal",
}


@_serialized_transition
def publish_preregistration(
    *,
    g6_preregistration: Path,
    capsule: Path,
    receipt: Path,
    selection: Path,
    robustness: Path,
    deployment_bundle: Path,
    g5_authorization: Path,
    g5_closure: Path,
    opening_pool_manifest: Path,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    v2_attempt_root: Path = DEFAULT_V2_ATTEMPT_ROOT,
) -> dict[str, Any]:
    """Freeze every threshold and lineage before any G6 held-out access."""

    protocol = validate_protocol()
    pinned = _verify_pinned_authorities()
    bundle = verify_deployment_bundle(deployment_bundle)
    network = Path(bundle["network"]["path"])
    chain = _verify_g6_prerequisite_chain(
        g6_preregistration=g6_preregistration,
        capsule=capsule,
        receipt=receipt,
        selection=selection,
        robustness=robustness,
        selected_network=network,
        require_heldout_absent=True,
    )
    if (
        not _type_exact_equal(bundle["g6Preregistration"], identity(g6_preregistration))
        or not _type_exact_equal(bundle["capsule"], identity(capsule))
        or not _type_exact_equal(bundle["freshVerificationReceipt"], identity(receipt))
        or not _type_exact_equal(bundle["selection"], identity(selection))
        or not _type_exact_equal(bundle["robustness"], identity(robustness))
    ):
        raise ValueError("deployment bundle differs from preregistered G6 lineage")
    incumbent = _verify_g5_incumbent(g5_authorization, g5_closure)
    opening_manifest, _ = _read_opening_pool(opening_pool_manifest)
    if _timestamp(opening_manifest["createdUtc"], "opening pool createdUtc") >= _timestamp(
        chain["selection"]["createdUtc"], "selection createdUtc"
    ):
        raise ValueError("opening pool was not frozen before G6 candidate selection")
    prior = _scan_v2_attempts(v2_attempt_root)
    if prior and prior[-1]["document"]["outcome"] == "confirmed":
        raise ValueError("v2 confirmation already reached terminal success")
    paths = _artifact_paths(artifact_root)
    if os.path.lexists(paths["preregistration"]):
        raise FileExistsError("G6 successor preregistration already exists")
    created = _utc_now()
    if _timestamp(created, "successor preregistration createdUtc") < _timestamp(
        bundle["createdUtc"], "deployment bundle createdUtc"
    ):
        raise ValueError("successor preregistration predates deployment bundle")
    practical_rule = _protocol_document()["practicalGate"]
    handoff_rule = _protocol_document()["formalHceHandoff"]
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PREREGISTRATION_KIND,
        "status": "sealed-before-g6-heldout-access",
        "createdUtc": created,
        "protocol": identity(PROTOCOL_PATH),
        "implementation": identity(TOOL_PATH),
        "pinnedAuthorities": pinned,
        "artifactRoot": str(paths["root"]),
        "g6": {
            "preregistration": identity(g6_preregistration),
            "capsule": identity(capsule),
            "freshVerificationReceipt": identity(receipt),
            "selection": identity(selection),
            "robustness": identity(robustness),
            "selectedCandidateId": chain["selection"]["selectedCandidateId"],
            "selectedNetwork": identity(network),
            "heldoutExpectedPaths": chain["heldoutPaths"],
        },
        "deploymentBundle": identity(deployment_bundle),
        "g5Incumbent": {
            "authorization": identity(g5_authorization),
            "closure": identity(g5_closure),
            "engine": incumbent["engine"],
            "network": incumbent["network"],
        },
        "openingPool": {
            "manifest": identity(opening_pool_manifest),
            "entries": opening_manifest["entries"],
            "cellCounts": opening_manifest["cellCounts"],
        },
        "globalAttemptSnapshot": {
            "v2AttemptRoot": str(v2_attempt_root.resolve()),
            "closedAttempts": [item["identity"] for item in prior],
            "nextGlobalAttemptIndexObserved": len(prior) + 1,
            "snapshotMayOnlyGrowByValidTerminalFailures": True,
        },
        "heldoutRule": HELDOUT_RULE,
        "practicalRule": practical_rule,
        "practicalExecutionAuthority": PRACTICAL_ADAPTER_AUTHORITY,
        "hceHandoffRule": handoff_rule,
        "resultInformationRead": False,
        "heldoutTargetRowsDecoded": 0,
        "gameResultsRead": False,
        "finalStageSeal": True,
    }
    return _exclusive_json(paths["preregistration"], document)


def verify_preregistration(path: Path) -> dict[str, Any]:
    document = _load_json(path, "G6 successor preregistration")
    _exact_keys(document, PREREGISTRATION_FIELDS, "G6 successor preregistration")
    _verify_header(
        document,
        kind=PREREGISTRATION_KIND,
        status="sealed-before-g6-heldout-access",
        label="G6 successor preregistration",
    )
    _timestamp(document["createdUtc"], "successor preregistration createdUtc")
    if (
        document["resultInformationRead"] is not False
        or document["heldoutTargetRowsDecoded"] != 0
        or document["gameResultsRead"] is not False
        or document["finalStageSeal"] is not True
        or not _type_exact_equal(document["protocol"], identity(PROTOCOL_PATH))
        or not _type_exact_equal(document["implementation"], identity(TOOL_PATH))
        or not _type_exact_equal(document["heldoutRule"], HELDOUT_RULE)
        or not _type_exact_equal(document["practicalRule"], _protocol_document()["practicalGate"])
        or not _type_exact_equal(
            document["practicalExecutionAuthority"], PRACTICAL_ADAPTER_AUTHORITY
        )
        or not _type_exact_equal(document["hceHandoffRule"], _protocol_document()["formalHceHandoff"])
    ):
        raise ValueError("successor preregistration policy changed")
    pinned = _verify_pinned_authorities()
    if not _type_exact_equal(document["pinnedAuthorities"], pinned):
        raise ValueError("successor preregistration pinned authorities changed")
    g6 = document.get("g6")
    _exact_keys(
        g6,
        {
            "preregistration",
            "capsule",
            "freshVerificationReceipt",
            "selection",
            "robustness",
            "selectedCandidateId",
            "selectedNetwork",
            "heldoutExpectedPaths",
        },
        "successor G6 lineage",
    )
    for field in (
        "preregistration",
        "capsule",
        "freshVerificationReceipt",
        "selection",
        "robustness",
        "selectedNetwork",
    ):
        verify_identity(g6[field], f"successor G6 {field}")
    bundle_path = verify_identity(document["deploymentBundle"], "successor deployment bundle")
    bundle = verify_deployment_bundle(bundle_path)
    if (
        bundle["selectedCandidateId"] != g6["selectedCandidateId"]
        or not _type_exact_equal(bundle["network"], g6["selectedNetwork"])
        or not _type_exact_equal(bundle["heldoutLineageExpectedPaths"], g6["heldoutExpectedPaths"])
    ):
        raise ValueError("successor deployment/G6 binding changed")
    incumbent = document.get("g5Incumbent")
    _exact_keys(incumbent, {"authorization", "closure", "engine", "network"}, "G5 incumbent")
    auth_path = verify_identity(incumbent["authorization"], "G5 authorization")
    closure_path = verify_identity(incumbent["closure"], "G5 closure")
    current_incumbent = _verify_g5_incumbent(auth_path, closure_path)
    if (
        not _type_exact_equal(current_incumbent["engine"], incumbent["engine"])
        or not _type_exact_equal(current_incumbent["network"], incumbent["network"])
    ):
        raise ValueError("G5 incumbent identities changed")
    pool = document.get("openingPool")
    _exact_keys(pool, {"manifest", "entries", "cellCounts"}, "opening pool binding")
    pool_path = verify_identity(pool["manifest"], "opening pool manifest")
    pool_doc, _ = _read_opening_pool(pool_path)
    if (
        not _type_exact_equal(pool_doc["entries"], pool["entries"])
        or not _type_exact_equal(pool_doc["cellCounts"], pool["cellCounts"])
    ):
        raise ValueError("opening pool binding changed")
    snapshot = document.get("globalAttemptSnapshot")
    _exact_keys(
        snapshot,
        {
            "v2AttemptRoot",
            "closedAttempts",
            "nextGlobalAttemptIndexObserved",
            "snapshotMayOnlyGrowByValidTerminalFailures",
        },
        "global attempt snapshot",
    )
    prior = _scan_v2_attempts(Path(snapshot["v2AttemptRoot"]))
    closed = [item["identity"] for item in prior]
    frozen = snapshot["closedAttempts"]
    if (
        type(frozen) is not list
        or len(closed) < len(frozen)
        or closed[: len(frozen)] != frozen
        or snapshot["nextGlobalAttemptIndexObserved"] != len(frozen) + 1
        or snapshot["snapshotMayOnlyGrowByValidTerminalFailures"] is not True
    ):
        raise ValueError("global attempt history rolled back or diverged")
    return document


def _finite(value: Any, label: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ValueError(f"{label} is not numeric")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{label} is nonfinite")
    return converted


def _verify_heldout_aggregate(value: Any, label: str) -> dict[str, Any]:
    _exact_keys(value, {"decisionRoots", "cells", "macro"}, label)
    if type(value["decisionRoots"]) is not int or value["decisionRoots"] < 1:
        raise ValueError(f"{label} root count changed")
    if not isinstance(value["cells"], dict) or set(value["cells"]) != set(CELLS):
        raise ValueError(f"{label} cell inventory changed")
    if not isinstance(value["macro"], dict) or set(value["macro"]) != set(METRICS):
        raise ValueError(f"{label} macro inventory changed")
    for metric in METRICS:
        _finite(value["macro"][metric], f"{label} macro {metric}")
    for cell in CELLS:
        row = value["cells"][cell]
        _exact_keys(row, {"roots", *METRICS}, f"{label} {cell}")
        if type(row["roots"]) is not int or row["roots"] < 1:
            raise ValueError(f"{label} {cell} root count changed")
        for metric in METRICS:
            _finite(row[metric], f"{label} {cell} {metric}")
    return dict(value)


def _verify_g6_heldout_lineage(prereg: Mapping[str, Any]) -> dict[str, Any]:
    expected = prereg["g6"]["heldoutExpectedPaths"]
    if not isinstance(expected, dict) or set(expected) != {"access", "claim", "report", "closure"}:
        raise ValueError("heldout expected-path inventory changed")
    paths = {name: _safe_file(raw) for name, raw in expected.items()}
    access = _load_json(paths["access"], "G6 heldout access")
    claim = _load_json(paths["claim"], "G6 heldout claim")
    report = _load_json(paths["report"], "G6 heldout report")
    closure = _load_json(paths["closure"], "G6 heldout closure")
    candidate = prereg["g6"]["selectedCandidateId"]
    network = prereg["g6"]["selectedNetwork"]
    if (
        access.get("kind") != G6_HELDOUT_ACCESS_KIND
        or access.get("status") != "armed-before-first-heldout-target-decode"
        or access.get("resultInformationRead") is not False
        or access.get("heldOutTargetRowsDecodedAtArm") != 0
        or access.get("heldOutTargetFieldsDecodedAtArm") != 0
        or access.get("selectedCandidateId") != candidate
        or not _type_exact_equal(access.get("validationSelection"), prereg["g6"]["selection"])
        or not _type_exact_equal(access.get("robustnessSeal"), prereg["g6"]["robustness"])
        or not _type_exact_equal(access.get("selectedPrimaryModel"), network)
    ):
        raise ValueError("G6 heldout access does not continue preregistered lineage")
    if (
        claim.get("kind") != G6_HELDOUT_CLAIM_KIND
        or claim.get("status") != "permanently-claimed-before-first-target-decode"
        or claim.get("resultInformationRead") is not False
        or claim.get("heldOutTargetRowsDecodedAtClaim") != 0
        or claim.get("heldOutTargetFieldsDecodedAtClaim") != 0
        or claim.get("selectedCandidateId") != candidate
        or not _type_exact_equal(claim.get("heldoutAccess"), identity(paths["access"]))
        or not _type_exact_equal(claim.get("selectedPrimaryModel"), network)
    ):
        raise ValueError("G6 heldout claim does not continue preregistered lineage")
    report_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "claim",
        "heldoutAccess",
        "validationSelection",
        "robustnessSeal",
        "selectedCandidateId",
        "models",
        "evaluatorExecutable",
        "evaluatorRunner",
        "evaluatorOptions",
        "runtimeManifest",
        "aggregation",
        "modelMetrics",
        "resultInformationRead",
        "decodedHeldOutTargetRows",
        "rawRootsEmitted",
        "rawChildrenEmitted",
        "rawOfensEmitted",
        "predictionsEmitted",
        "perRootMetricsEmitted",
    }
    _exact_keys(report, report_fields, "G6 heldout report")
    if (
        report.get("kind") != G6_HELDOUT_REPORT_KIND
        or report.get("status") != "consumed-once-aggregate-only"
        or report.get("selectedCandidateId") != candidate
        or report.get("resultInformationRead") is not False
        or type(report.get("decodedHeldOutTargetRows")) is not int
        or report["decodedHeldOutTargetRows"] < 1
        or any(
            report.get(field) is not False
            for field in (
                "rawRootsEmitted",
                "rawChildrenEmitted",
                "rawOfensEmitted",
                "predictionsEmitted",
                "perRootMetricsEmitted",
            )
        )
        or not _type_exact_equal(report.get("claim"), identity(paths["claim"]))
        or not _type_exact_equal(report.get("heldoutAccess"), identity(paths["access"]))
        or not _type_exact_equal(report.get("validationSelection"), prereg["g6"]["selection"])
        or not _type_exact_equal(report.get("robustnessSeal"), prereg["g6"]["robustness"])
        or not isinstance(report.get("models"), dict)
        or set(report["models"]) != {"I0", candidate}
        or not _type_exact_equal(report["models"][candidate], network)
        or not isinstance(report.get("modelMetrics"), dict)
        or set(report["modelMetrics"]) != {"I0", candidate}
    ):
        raise ValueError("G6 heldout report header or opacity contract changed")
    baseline = _verify_heldout_aggregate(report["modelMetrics"]["I0"], "heldout I0")
    selected = _verify_heldout_aggregate(
        report["modelMetrics"][candidate], f"heldout {candidate}"
    )
    if baseline["decisionRoots"] != selected["decisionRoots"]:
        raise ValueError("heldout model root counts differ")
    closure_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "claim",
        "heldoutAccess",
        "report",
        "selectedCandidateId",
        "heldOutRootInventory",
        "spentForFutureGenerations",
        "retryPermitted",
        "resultInformationRead",
    }
    _exact_keys(closure, closure_fields, "G6 heldout closure")
    if (
        closure.get("kind") != G6_HELDOUT_CLOSURE_KIND
        or closure.get("status") != "heldout-consumed-and-permanently-closed"
        or closure.get("selectedCandidateId") != candidate
        or closure.get("spentForFutureGenerations") is not True
        or closure.get("retryPermitted") is not False
        or closure.get("resultInformationRead") is not False
        or not _type_exact_equal(closure.get("preregistration"), prereg["g6"]["preregistration"])
        or not _type_exact_equal(closure.get("claim"), identity(paths["claim"]))
        or not _type_exact_equal(closure.get("heldoutAccess"), identity(paths["access"]))
        or not _type_exact_equal(closure.get("report"), identity(paths["report"]))
    ):
        raise ValueError("G6 heldout closure changed")
    chronology = [
        _timestamp(access.get("createdUtc"), "heldout access createdUtc"),
        _timestamp(claim.get("createdUtc"), "heldout claim createdUtc"),
        _timestamp(report.get("createdUtc"), "heldout report createdUtc"),
        _timestamp(closure.get("createdUtc"), "heldout closure createdUtc"),
    ]
    prereg_created = _timestamp(
        prereg.get("createdUtc"), "successor preregistration createdUtc"
    )
    if (
        chronology != sorted(chronology)
        or len(set(chronology)) != len(chronology)
        or chronology[0] <= prereg_created
    ):
        raise ValueError("G6 heldout chronology changed")
    lineage = {
        "paths": paths,
        "access": access,
        "claim": claim,
        "report": report,
        "closure": closure,
        "baseline": baseline,
        "selected": selected,
    }
    _verify_trainer_heldout_authority(prereg, lineage)
    return lineage


def _heldout_nomination_math(
    baseline: Mapping[str, Any], selected: Mapping[str, Any]
) -> dict[str, Any]:
    tolerance = HELDOUT_RULE["comparisonTolerance"]
    directions = {
        "topSetAccuracy": 1,
        "meanChosenMoveRegretCp": -1,
        "listwiseCrossEntropy": -1,
        "pointwiseHuber": -1,
    }
    macro_non_regression: dict[str, bool] = {}
    strict: dict[str, bool] = {}
    for metric, direction in directions.items():
        old = _finite(baseline["macro"][metric], f"baseline macro {metric}")
        new = _finite(selected["macro"][metric], f"selected macro {metric}")
        delta = direction * (new - old)
        macro_non_regression[metric] = delta >= -tolerance
        strict[metric] = delta > tolerance
    cell_non_regression: dict[str, dict[str, bool]] = {}
    for cell in CELLS:
        top_old = _finite(baseline["cells"][cell]["topSetAccuracy"], "baseline cell top")
        top_new = _finite(selected["cells"][cell]["topSetAccuracy"], "selected cell top")
        regret_old = _finite(
            baseline["cells"][cell]["meanChosenMoveRegretCp"], "baseline cell regret"
        )
        regret_new = _finite(
            selected["cells"][cell]["meanChosenMoveRegretCp"], "selected cell regret"
        )
        cell_non_regression[cell] = {
            "topSetAccuracy": top_new
            >= top_old - HELDOUT_RULE["cellMaximumRegression"]["topSetAccuracy"] - tolerance,
            "meanChosenMoveRegretCp": regret_new
            <= regret_old
            + HELDOUT_RULE["cellMaximumRegression"]["meanChosenMoveRegretCp"]
            + tolerance,
        }
    strict_count = sum(strict.values())
    strict_decision = any(strict[metric] for metric in HELDOUT_RULE["decisionMetrics"])
    gates = {
        "allMacroMetricsNonRegressing": all(macro_non_regression.values()),
        "everyCellDecisionMetricWithinFrozenTolerance": all(
            all(values.values()) for values in cell_non_regression.values()
        ),
        "minimumStrictMacroImprovements": strict_count
        >= HELDOUT_RULE["minimumStrictMacroImprovements"],
        "strictDecisionMetricImprovement": strict_decision,
    }
    return {
        "fixedRule": HELDOUT_RULE,
        "macroNonRegression": macro_non_regression,
        "strictMacroImprovement": strict,
        "strictMacroImprovementCount": strict_count,
        "cellNonRegression": cell_non_regression,
        "gates": gates,
        "nominate": all(gates.values()),
    }


HELDOUT_DECISION_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "preregistration",
    "deploymentBundle",
    "heldoutAccess",
    "heldoutClaim",
    "heldoutReport",
    "heldoutClosure",
    "selectedCandidateId",
    "selectedNetwork",
    "fixedRule",
    "assessment",
    "decision",
    "terminal",
    "heldoutAggregateRead",
    "rawHeldoutTargetsRead",
    "gameResultsRead",
}


@_serialized_transition
def publish_heldout_decision(preregistration: Path) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    lineage = _verify_g6_heldout_lineage(prereg)
    assessment = _heldout_nomination_math(lineage["baseline"], lineage["selected"])
    root = Path(prereg["artifactRoot"])
    output = _artifact_paths(root)["heldoutDecision"]
    if os.path.lexists(output):
        raise FileExistsError("heldout decision slot already consumed")
    decision = "nominate-for-practical-gate" if assessment["nominate"] else "reject"
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HELDOUT_DECISION_KIND,
        "status": "terminal-fixed-heldout-nomination",
        "createdUtc": _utc_now(),
        "protocol": identity(PROTOCOL_PATH),
        "preregistration": identity(preregistration),
        "deploymentBundle": prereg["deploymentBundle"],
        "heldoutAccess": identity(lineage["paths"]["access"]),
        "heldoutClaim": identity(lineage["paths"]["claim"]),
        "heldoutReport": identity(lineage["paths"]["report"]),
        "heldoutClosure": identity(lineage["paths"]["closure"]),
        "selectedCandidateId": prereg["g6"]["selectedCandidateId"],
        "selectedNetwork": prereg["g6"]["selectedNetwork"],
        "fixedRule": HELDOUT_RULE,
        "assessment": assessment,
        "decision": decision,
        "terminal": True,
        "heldoutAggregateRead": True,
        "rawHeldoutTargetsRead": False,
        "gameResultsRead": False,
    }
    result = _exclusive_json(output, document)
    verify_heldout_decision(output, preregistration=preregistration)
    return result


def verify_heldout_decision(path: Path, *, preregistration: Path) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    document = _load_json(path, "successor heldout decision")
    _exact_keys(document, HELDOUT_DECISION_FIELDS, "successor heldout decision")
    _verify_header(
        document,
        kind=HELDOUT_DECISION_KIND,
        status="terminal-fixed-heldout-nomination",
        label="successor heldout decision",
    )
    lineage = _verify_g6_heldout_lineage(prereg)
    expected_assessment = _heldout_nomination_math(lineage["baseline"], lineage["selected"])
    expected_decision = (
        "nominate-for-practical-gate" if expected_assessment["nominate"] else "reject"
    )
    if (
        document.get("terminal") is not True
        or document.get("heldoutAggregateRead") is not True
        or document.get("rawHeldoutTargetsRead") is not False
        or document.get("gameResultsRead") is not False
        or not _type_exact_equal(document.get("protocol"), identity(PROTOCOL_PATH))
        or not _type_exact_equal(document.get("preregistration"), identity(preregistration))
        or not _type_exact_equal(document.get("deploymentBundle"), prereg["deploymentBundle"])
        or not _type_exact_equal(document.get("heldoutAccess"), identity(lineage["paths"]["access"]))
        or not _type_exact_equal(document.get("heldoutClaim"), identity(lineage["paths"]["claim"]))
        or not _type_exact_equal(document.get("heldoutReport"), identity(lineage["paths"]["report"]))
        or not _type_exact_equal(document.get("heldoutClosure"), identity(lineage["paths"]["closure"]))
        or document.get("selectedCandidateId") != prereg["g6"]["selectedCandidateId"]
        or not _type_exact_equal(document.get("selectedNetwork"), prereg["g6"]["selectedNetwork"])
        or not _type_exact_equal(document.get("fixedRule"), HELDOUT_RULE)
        or not _type_exact_equal(document.get("assessment"), expected_assessment)
        or document.get("decision") != expected_decision
    ):
        raise ValueError("heldout decision differs from frozen rule replay")
    _timestamp(document.get("createdUtc"), "heldout decision createdUtc")
    if _timestamp(document["createdUtc"], "heldout decision createdUtc") < _timestamp(
        lineage["closure"]["createdUtc"], "heldout closure createdUtc"
    ):
        raise ValueError("heldout decision predates heldout closure")
    return document


def _attempt_paths(root: Path, index: int) -> dict[str, Path]:
    if type(index) is not int or isinstance(index, bool) or index < 1:
        raise ValueError("attempt index must be a positive exact integer")
    base = root.resolve() / "attempts" / f"attempt-{index:06d}"
    return {
        "root": base,
        "reservation": base / "00-reservation.json",
        "entropy": base / "01-private-entropy.bin",
        "claim": base / "02-claim.json",
        "suite": base / "03-practical-suite.json",
        "practicalDecision": base / "04-practical-decision.json",
        "hceHandoff": base / "05-hce-handoff.json",
        "closure": base / "06-attempt-closure.json",
        "practicalExecution": base / "07-practical-execution-v1",
    }


SUCCESSOR_CLOSURE_KIND = f"{PROTOCOL_ID}-attempt-closure"
SUCCESSOR_CLOSURE_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "preregistration",
    "attemptIndex",
    "terminalStage",
    "reservation",
    "claim",
    "suite",
    "practicalDecision",
    "practicalExecutionTranscript",
    "practicalIncidentHandoff",
    "hceHandoff",
    "hceAttemptClosure",
    "outcome",
    "reason",
    "terminal",
    "nextAttemptAllowed",
    "candidateNetworkSha256",
}


def _verify_successor_closure(path: Path, expected_index: int) -> dict[str, Any]:
    value = _load_json(path, f"successor attempt {expected_index} closure")
    _exact_keys(value, SUCCESSOR_CLOSURE_FIELDS, "successor attempt closure")
    stage = value.get("terminalStage")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != SUCCESSOR_CLOSURE_KIND
        or value.get("status") != "terminal-global-attempt-closure"
        or value.get("attemptIndex") != expected_index
        or type(value.get("attemptIndex")) is not int
        or value.get("preregistration") is None
        or value.get("reservation") is None
        or value.get("terminal") is not True
        or value.get("outcome") not in {"confirmed", "failed", "aborted"}
        or type(value.get("nextAttemptAllowed")) is not bool
        or (value["outcome"] == "confirmed") is value["nextAttemptAllowed"]
        or type(value.get("candidateNetworkSha256")) is not str
        or HEX256.fullmatch(value["candidateNetworkSha256"]) is None
        or type(value.get("reason")) is not str
        or not value["reason"]
        or not _type_exact_equal(value.get("protocol"), identity(PROTOCOL_PATH))
        or stage
        not in {"claim-publication", "suite-publication", "practical", "hce"}
    ):
        raise ValueError("successor attempt closure changed")
    _timestamp(value.get("createdUtc"), "successor closure createdUtc")
    for field in (
        "preregistration",
        "reservation",
        "claim",
        "suite",
        "practicalDecision",
        "practicalExecutionTranscript",
        "practicalIncidentHandoff",
        "hceHandoff",
        "hceAttemptClosure",
    ):
        if value.get(field) is not None:
            verify_identity(value[field], f"successor closure {field}")
    nullability = {
        "claim-publication": {
            "claim": None,
            "suite": None,
            "practicalDecision": None,
            "practicalExecutionTranscript": None,
            "practicalIncidentHandoff": None,
            "hceHandoff": None,
            "hceAttemptClosure": None,
        },
        "suite-publication": {
            "suite": None,
            "practicalDecision": None,
            "practicalExecutionTranscript": None,
            "practicalIncidentHandoff": None,
            "hceHandoff": None,
            "hceAttemptClosure": None,
        },
        "practical": {
            "hceHandoff": None,
            "hceAttemptClosure": None,
        },
        "hce": {},
    }[stage]
    if any(value.get(field) is not expected for field, expected in nullability.items()):
        raise ValueError("successor closure stage/evidence union changed")
    if stage in {"suite-publication", "practical", "hce"} and value.get("claim") is None:
        raise ValueError("successor closure stage lacks a claim")
    if stage in {"practical", "hce"} and (
        value.get("suite") is None or value.get("practicalDecision") is None
    ):
        raise ValueError("successor closure stage lacks practical evidence")
    if stage == "practical" and (
        (value.get("practicalExecutionTranscript") is None)
        == (value.get("practicalIncidentHandoff") is None)
    ):
        raise ValueError("practical closure must cite exactly one adapter terminal bridge")
    if stage == "hce" and (
        value.get("practicalExecutionTranscript") is None
        or value.get("practicalIncidentHandoff") is not None
        or value.get("hceHandoff") is None
        or value.get("hceAttemptClosure") is None
    ):
        raise ValueError("HCE closure evidence union changed")
    if stage == "claim-publication" and (
        value["outcome"] != "aborted"
        or value["reason"] != "claim-publication-failure"
        or value["nextAttemptAllowed"] is not True
    ):
        raise ValueError("claim-publication closure semantics changed")
    if stage == "suite-publication" and (
        value["outcome"] != "aborted"
        or value["reason"] != "suite-publication-failure"
        or value["nextAttemptAllowed"] is not True
    ):
        raise ValueError("suite-publication closure semantics changed")
    if stage == "practical" and (
        value["outcome"] != "failed" or value["nextAttemptAllowed"] is not True
    ):
        raise ValueError("practical closure semantics changed")
    return value


def _replay_successor_attempt(
    *,
    preregistration: Path,
    prereg: Mapping[str, Any],
    paths: Mapping[str, Path],
    closure: Mapping[str, Any],
    expected_predecessor: Any,
) -> None:
    """Replay every authority cited by one historical successor closure."""

    index = closure["attemptIndex"]
    stage = closure["terminalStage"]
    if (
        not _type_exact_equal(closure["preregistration"], identity(preregistration))
        or not _type_exact_equal(closure["reservation"], identity(paths["reservation"]))
    ):
        raise ValueError("successor closure preregistration/reservation binding changed")
    reservation = verify_attempt_reservation(
        paths["reservation"],
        preregistration=preregistration,
        expected_predecessor=expected_predecessor,
    )
    if stage == "claim-publication":
        actual = _verify_attempt_direct_inventory(
            paths,
            required={"reservation", "closure"},
            optional={"entropy"},
        )
        if paths["entropy"].name in actual and len(_snapshot_bytes(paths["entropy"])) != ENTROPY_BYTES:
            raise ValueError("historical failed claim entropy is malformed")
        if closure["candidateNetworkSha256"] != reservation["candidateNetwork"]["sha256"]:
            raise ValueError("claim-publication closure candidate changed")
        terminal_created = reservation["createdUtc"]
    else:
        claim_doc = verify_candidate_claim(
            paths["claim"], preregistration=preregistration
        )
        if (
            not _type_exact_equal(closure["claim"], identity(paths["claim"]))
            or closure["candidateNetworkSha256"]
            != claim_doc["candidateNetwork"]["sha256"]
        ):
            raise ValueError("successor closure claim/candidate binding changed")
        terminal_created = claim_doc["createdUtc"]
        if stage == "suite-publication":
            _verify_attempt_direct_inventory(
                paths,
                required={"reservation", "entropy", "claim", "closure"},
            )
        else:
            suite_doc = verify_practical_suite(
                paths["suite"],
                preregistration=preregistration,
                claim=paths["claim"],
            )
            if not _type_exact_equal(closure["suite"], identity(paths["suite"])):
                raise ValueError("successor closure practical suite changed")
            decision = verify_practical_decision(
                paths["practicalDecision"],
                preregistration=preregistration,
                claim=paths["claim"],
                suite=paths["suite"],
            )
            if not _type_exact_equal(
                closure["practicalDecision"], identity(paths["practicalDecision"])
            ):
                raise ValueError("successor closure practical decision changed")
            terminal_created = decision["createdUtc"]
            if stage == "practical":
                _verify_attempt_direct_inventory(
                    paths,
                    required={
                        "reservation",
                        "entropy",
                        "claim",
                        "suite",
                        "practicalDecision",
                        "closure",
                        "practicalExecution",
                    },
                )
                if decision["decision"] == "promote":
                    raise ValueError("practical-stage closure truncated a promotion")
                if decision["executionEvidenceMode"] == "authenticated-execution-transcript":
                    expected_reason = f"practical-{decision['decision']}"
                    if (
                        not _type_exact_equal(
                            closure["practicalExecutionTranscript"],
                            decision["executionTranscript"],
                        )
                        or closure["practicalIncidentHandoff"] is not None
                    ):
                        raise ValueError("normal practical closure transcript changed")
                else:
                    expected_reason = "practical-safety-fail-incident"
                    if (
                        closure["practicalExecutionTranscript"] is not None
                        or not _type_exact_equal(
                            closure["practicalIncidentHandoff"],
                            decision["incidentHandoff"],
                        )
                    ):
                        raise ValueError("incident practical closure handoff changed")
                if closure["reason"] != expected_reason:
                    raise ValueError("practical closure reason changed")
            else:
                _verify_attempt_direct_inventory(
                    paths,
                    required={
                        "reservation",
                        "entropy",
                        "claim",
                        "suite",
                        "practicalDecision",
                        "hceHandoff",
                        "closure",
                        "practicalExecution",
                    },
                )
                if (
                    decision["decision"] != "promote"
                    or decision["zeroSafetyFailures"] is not True
                    or not _type_exact_equal(
                        closure["practicalExecutionTranscript"],
                        decision["executionTranscript"],
                    )
                ):
                    raise ValueError("HCE closure lacks a safe practical promotion")
                verify_hce_handoff(
                    paths["hceHandoff"],
                    preregistration=preregistration,
                    claim=paths["claim"],
                    suite=paths["suite"],
                    practical_decision=paths["practicalDecision"],
                )
                if not _type_exact_equal(
                    closure["hceHandoff"], identity(paths["hceHandoff"])
                ):
                    raise ValueError("historical HCE handoff changed")
                v2_path = verify_identity(
                    closure["hceAttemptClosure"], "historical HCE attempt closure"
                )
                adapter = _hce_launch_adapter()
                preview = _load_json(v2_path, "historical HCE closure preview")
                if preview.get("outcome") == "aborted":
                    v2 = adapter.verify_attempt_closure(
                        v2_path.resolve(), handoff=paths["hceHandoff"].resolve()
                    )
                else:
                    v2 = adapter.verify_historical_attempt_closure(
                        v2_path.resolve(), handoff=paths["hceHandoff"].resolve()
                    )
                if (
                    v2.get("attemptIndex") != index
                    or v2.get("candidateNetworkSha256")
                    != claim_doc["candidateNetwork"]["sha256"]
                    or closure["outcome"] != v2["outcome"]
                    or closure["nextAttemptAllowed"] != v2["nextAttemptAllowed"]
                    or closure["reason"] != f"hce-{v2['reason']}"
                ):
                    raise ValueError("historical HCE closure differs from adapter replay")
                terminal_created = v2["createdUtc"]
    if _timestamp(closure["createdUtc"], "successor closure createdUtc") < _timestamp(
        terminal_created, "successor terminal evidence createdUtc"
    ):
        raise ValueError("successor closure predates its terminal evidence")


def _scan_successor_attempts(
    root: Path,
    *,
    preregistration: Path,
    prereg: Mapping[str, Any],
    v2: Mapping[int, Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    attempts_root = root.resolve() / "attempts"
    if not attempts_root.exists():
        return {}
    _verify_directory_chain(attempts_root)
    if attempts_root.is_symlink() or not attempts_root.is_dir():
        raise ValueError("successor attempt root is unsafe")
    directories: dict[int, Path] = {}
    for child in attempts_root.iterdir():
        if not child.is_dir() or child.is_symlink():
            raise ValueError("unexpected successor attempt artifact")
        match = re.fullmatch(r"attempt-([0-9]{6})", child.name)
        if match is None:
            raise ValueError("unexpected successor attempt directory")
        index = int(match.group(1))
        if index < 1 or index in directories:
            raise ValueError("invalid duplicate successor attempt")
        directories[index] = child
    result: dict[int, dict[str, Any]] = {}
    for index in sorted(directories):
        child = directories[index]
        paths = _attempt_paths(root, index)
        if paths["closure"].exists():
            closure = _verify_successor_closure(paths["closure"], index)
            if index == 1:
                predecessor = None
            elif index - 1 in v2:
                predecessor = v2[index - 1]["identity"]
            elif index - 1 in result and result[index - 1]["identity"] is not None:
                predecessor = result[index - 1]["identity"]
            else:
                raise ValueError("successor attempt lacks its global predecessor")
            _replay_successor_attempt(
                preregistration=preregistration,
                prereg=prereg,
                paths=paths,
                closure=closure,
                expected_predecessor=predecessor,
            )
            result[index] = {
                "identity": identity(paths["closure"]),
                "document": closure,
                "active": False,
            }
        else:
            # Any published final-name attempt artifact consumes and blocks k.
            allowed = {path.name for name, path in paths.items() if name != "root"}
            actual = {entry.name for entry in child.iterdir()}
            if not actual.issubset(allowed):
                raise ValueError("active successor attempt has an unexpected artifact")
            for entry in child.iterdir():
                info = os.lstat(entry)
                if _unsafe_linklike(entry, info):
                    raise ValueError("active successor attempt contains a link-like artifact")
                if entry.name == paths["practicalExecution"].name:
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError("active practical execution is not a directory")
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("active successor artifact is not a unique regular file")
            if actual:
                result[index] = {"identity": None, "document": None, "active": True}
            else:
                raise ValueError("empty successor attempt directory is not canonical")
    return result


def _global_attempt_state(prereg: Mapping[str, Any]) -> dict[str, Any]:
    v2_rows = _scan_v2_attempts(Path(prereg["globalAttemptSnapshot"]["v2AttemptRoot"]))
    v2 = {row["document"]["attemptIndex"]: row for row in v2_rows}
    preregistration = _artifact_paths(Path(prereg["artifactRoot"]))["preregistration"]
    successor = _scan_successor_attempts(
        Path(prereg["artifactRoot"]),
        preregistration=preregistration,
        prereg=prereg,
        v2=v2,
    )
    active = [index for index, row in successor.items() if row["active"]]
    if len(active) > 1:
        raise ValueError("multiple active successor attempts")
    combined: dict[int, dict[str, Any]] = {}
    for index, row in v2.items():
        combined[index] = {
            "identity": row["identity"],
            "outcome": row["document"]["outcome"],
            "nextAttemptAllowed": row["document"]["nextAttemptAllowed"],
        }
    for index, row in successor.items():
        if row["active"]:
            continue
        document = row["document"]
        assert document is not None
        if index in combined:
            if (
                document.get("hceAttemptClosure") is None
                or not _type_exact_equal(document["hceAttemptClosure"], v2[index]["identity"])
                or document["outcome"] != v2[index]["document"]["outcome"]
                or document["nextAttemptAllowed"]
                != v2[index]["document"]["nextAttemptAllowed"]
            ):
                raise ValueError("overlapping v2/successor attempt is not an exact bridge")
        else:
            combined[index] = {
                "identity": row["identity"],
                "outcome": document["outcome"],
                "nextAttemptAllowed": document["nextAttemptAllowed"],
            }
    if set(combined) != set(range(1, len(combined) + 1)):
        raise ValueError("global attempt chain has a gap")
    for index in range(1, len(combined)):
        if combined[index]["nextAttemptAllowed"] is not True:
            raise ValueError("global successor exists after terminal confirmation")
    if active:
        expected_active = len(combined) + 1
        if (
            active[0] != expected_active
            or (
                combined
                and combined[len(combined)]["outcome"] == "confirmed"
            )
        ):
            raise ValueError("active successor attempt is not the next global index")
        return {
            "activeAttempt": active[0],
            "nextAttemptIndex": None,
            "closed": combined,
        }
    if combined and combined[len(combined)]["outcome"] == "confirmed":
        return {"activeAttempt": None, "nextAttemptIndex": None, "closed": combined}
    return {
        "activeAttempt": None,
        "nextAttemptIndex": len(combined) + 1,
        "closed": combined,
    }


def _attempt_log_threshold(index: int) -> float:
    if type(index) is not int or isinstance(index, bool) or index < 1:
        raise ValueError("attempt index changed")
    if index > MAX_ADMISSIBLE_ATTEMPT_INDEX:
        raise ValueError(
            "statistical-cap-exhausted: global attempt 378 is the first rejected "
            "index, not a diagnostic successor"
        )
    return math.log(100.0) + index * math.log(2.0)


def _entropy_commitment(entropy: bytes) -> str:
    if type(entropy) is not bytes or len(entropy) != ENTROPY_BYTES:
        raise ValueError("attempt entropy length changed")
    return hashlib.sha256(ENTROPY_COMMITMENT_DOMAIN + entropy).hexdigest()


def _stage_key(entropy: bytes, index: int, stage: str) -> bytes:
    if type(stage) is not str or SAFE_ID.fullmatch(stage) is None:
        raise ValueError("invalid stage domain")
    message = (
        SEED_HMAC_DOMAIN
        + PROTOCOL_ID.encode("ascii")
        + b"\x00"
        + index.to_bytes(8, "big", signed=False)
        + b"\x00"
        + stage.encode("ascii")
    )
    return hmac.new(entropy, message, hashlib.sha256).digest()


def _stage_commitments(entropy: bytes, index: int) -> dict[str, str]:
    stages = ("practical-opening-selection", *FORMAL_HCE_GATES)
    return {
        stage: hashlib.sha256(b"public-stage-commitment-v1\x00" + _stage_key(entropy, index, stage)).hexdigest()
        for stage in stages
    }


RESERVATION_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "preregistration",
    "heldoutDecision",
    "attemptIndex",
    "predecessorClosure",
    "attemptBeta",
    "promotionLogThreshold",
    "candidateDeploymentBundle",
    "candidateNetwork",
    "incumbentAuthorization",
    "incumbentNetwork",
    "reservationConsumesAttempt",
    "entropyDrawn",
    "resultInformationRead",
}
CLAIM_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "preregistration",
    "reservation",
    "heldoutDecision",
    "attemptIndex",
    "predecessorClosure",
    "attemptBeta",
    "promotionLogThreshold",
    "candidateDeploymentBundle",
    "candidateNetwork",
    "incumbentAuthorization",
    "incumbentNetwork",
    "entropyCommitment",
    "stageSeedCommitments",
    "entropyBytesPublished",
    "stageSeedsPublished",
    "resultInformationRead",
}

_UNSPECIFIED_PREDECESSOR = object()


def verify_attempt_reservation(
    path: Path,
    *,
    preregistration: Path,
    expected_predecessor: Any = _UNSPECIFIED_PREDECESSOR,
) -> dict[str, Any]:
    """Replay the result-blind reservation independently of claim publication."""

    prereg = verify_preregistration(preregistration)
    document = _load_json(path, "attempt reservation")
    _exact_keys(document, RESERVATION_FIELDS, "attempt reservation")
    _verify_header(
        document,
        kind=RESERVATION_KIND,
        status="reserved-before-single-entropy-draw",
        label="attempt reservation",
    )
    index = document.get("attemptIndex")
    threshold = _attempt_log_threshold(index)
    paths = _attempt_paths(Path(prereg["artifactRoot"]), index)
    heldout_path = verify_identity(
        document.get("heldoutDecision"), "reservation heldout decision"
    )
    heldout = verify_heldout_decision(
        heldout_path, preregistration=preregistration
    )
    predecessor = document.get("predecessorClosure")
    if predecessor is not None:
        verify_identity(predecessor, "reservation predecessor closure")
    if (
        Path(path).resolve() != paths["reservation"]
        or document.get("reservationConsumesAttempt") is not True
        or document.get("entropyDrawn") is not False
        or document.get("resultInformationRead") is not False
        or document.get("attemptBeta")
        != {"numerator": 1, "denominator": 100 * (2**index)}
        or document.get("promotionLogThreshold") != threshold
        or heldout.get("decision") != "nominate-for-practical-gate"
        or not _type_exact_equal(document.get("protocol"), identity(PROTOCOL_PATH))
        or not _type_exact_equal(
            document.get("preregistration"), identity(preregistration)
        )
        or not _type_exact_equal(
            document.get("candidateDeploymentBundle"), prereg["deploymentBundle"]
        )
        or not _type_exact_equal(
            document.get("candidateNetwork"), prereg["g6"]["selectedNetwork"]
        )
        or not _type_exact_equal(
            document.get("incumbentAuthorization"),
            prereg["g5Incumbent"]["authorization"],
        )
        or not _type_exact_equal(
            document.get("incumbentNetwork"), prereg["g5Incumbent"]["network"]
        )
        or (
            expected_predecessor is not _UNSPECIFIED_PREDECESSOR
            and not _type_exact_equal(predecessor, expected_predecessor)
        )
    ):
        raise ValueError("attempt reservation differs from frozen global claim policy")
    created = _timestamp(document.get("createdUtc"), "reservation createdUtc")
    if created < _timestamp(heldout["createdUtc"], "heldout decision createdUtc"):
        raise ValueError("attempt reservation predates heldout nomination")
    return document


@_serialized_transition
def claim_practical_attempt(
    *, preregistration: Path, heldout_decision: Path
) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    decision = verify_heldout_decision(
        heldout_decision, preregistration=preregistration
    )
    if decision["decision"] != "nominate-for-practical-gate":
        raise ValueError("heldout rejection cannot consume a practical attempt")
    state = _global_attempt_state(prereg)
    if state["activeAttempt"] is not None:
        raise ValueError(f"global attempt {state['activeAttempt']} is already active")
    index = state["nextAttemptIndex"]
    if index is None:
        raise ValueError("confirmation program is already terminally successful")
    threshold = _attempt_log_threshold(index)
    paths = _attempt_paths(Path(prereg["artifactRoot"]), index)
    if paths["root"].exists():
        raise FileExistsError("next attempt directory already exists")
    predecessor = None
    if index > 1:
        assert state["closed"] is not None
        predecessor = state["closed"][index - 1]["identity"]
    reservation = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RESERVATION_KIND,
        "status": "reserved-before-single-entropy-draw",
        "createdUtc": _utc_now(),
        "protocol": identity(PROTOCOL_PATH),
        "preregistration": identity(preregistration),
        "heldoutDecision": identity(heldout_decision),
        "attemptIndex": index,
        "predecessorClosure": predecessor,
        "attemptBeta": {"numerator": 1, "denominator": 100 * (2**index)},
        "promotionLogThreshold": threshold,
        "candidateDeploymentBundle": prereg["deploymentBundle"],
        "candidateNetwork": prereg["g6"]["selectedNetwork"],
        "incumbentAuthorization": prereg["g5Incumbent"]["authorization"],
        "incumbentNetwork": prereg["g5Incumbent"]["network"],
        "reservationConsumesAttempt": True,
        "entropyDrawn": False,
        "resultInformationRead": False,
    }
    reservation_identity = _exclusive_json(paths["reservation"], reservation)
    # Exactly one draw follows durable reservation publication.  Any exception
    # from here onward leaves a consumed active attempt; reroll is forbidden.
    entropy = secrets.token_bytes(ENTROPY_BYTES)
    _exclusive_bytes(paths["entropy"], entropy, mode=0o600)
    claim = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": CLAIM_KIND,
        "status": "claimed-with-hidden-csprng-hmac-seeds",
        "createdUtc": _utc_now(),
        "protocol": identity(PROTOCOL_PATH),
        "preregistration": identity(preregistration),
        "reservation": reservation_identity,
        "heldoutDecision": identity(heldout_decision),
        "attemptIndex": index,
        "predecessorClosure": predecessor,
        "attemptBeta": reservation["attemptBeta"],
        "promotionLogThreshold": threshold,
        "candidateDeploymentBundle": prereg["deploymentBundle"],
        "candidateNetwork": prereg["g6"]["selectedNetwork"],
        "incumbentAuthorization": prereg["g5Incumbent"]["authorization"],
        "incumbentNetwork": prereg["g5Incumbent"]["network"],
        "entropyCommitment": _entropy_commitment(entropy),
        "stageSeedCommitments": _stage_commitments(entropy, index),
        "entropyBytesPublished": 0,
        "stageSeedsPublished": False,
        "resultInformationRead": False,
    }
    result = _exclusive_json(paths["claim"], claim)
    verify_candidate_claim(paths["claim"], preregistration=preregistration)
    return result


def verify_candidate_claim(path: Path, *, preregistration: Path) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    document = _load_json(path, "successor candidate claim")
    _exact_keys(document, CLAIM_FIELDS, "successor candidate claim")
    _verify_header(
        document,
        kind=CLAIM_KIND,
        status="claimed-with-hidden-csprng-hmac-seeds",
        label="successor candidate claim",
    )
    index = document.get("attemptIndex")
    threshold = _attempt_log_threshold(index)
    paths = _attempt_paths(Path(prereg["artifactRoot"]), index)
    if path.resolve() != paths["claim"]:
        raise ValueError("candidate claim path changed")
    reservation = verify_attempt_reservation(
        paths["reservation"], preregistration=preregistration
    )
    entropy = _snapshot_bytes(paths["entropy"])
    if (
        document.get("entropyBytesPublished") != 0
        or document.get("stageSeedsPublished") is not False
        or document.get("resultInformationRead") is not False
        or document.get("promotionLogThreshold") != threshold
        or not _type_exact_equal(document.get("protocol"), identity(PROTOCOL_PATH))
        or not _type_exact_equal(document.get("preregistration"), identity(preregistration))
        or not _type_exact_equal(document.get("reservation"), identity(paths["reservation"]))
        or not _type_exact_equal(document.get("heldoutDecision"), reservation.get("heldoutDecision"))
        or document.get("predecessorClosure") != reservation.get("predecessorClosure")
        or document.get("attemptBeta") != reservation.get("attemptBeta")
        or not _type_exact_equal(document.get("candidateDeploymentBundle"), prereg["deploymentBundle"])
        or not _type_exact_equal(document.get("candidateNetwork"), prereg["g6"]["selectedNetwork"])
        or not _type_exact_equal(document.get("incumbentAuthorization"), prereg["g5Incumbent"]["authorization"])
        or not _type_exact_equal(document.get("incumbentNetwork"), prereg["g5Incumbent"]["network"])
        or document.get("entropyCommitment") != _entropy_commitment(entropy)
        or document.get("stageSeedCommitments") != _stage_commitments(entropy, index)
    ):
        raise ValueError("candidate claim differs from frozen hidden-seed claim")
    if reservation.get("status") != "reserved-before-single-entropy-draw" or reservation.get("entropyDrawn") is not False:
        raise ValueError("attempt reservation contract changed")
    if _timestamp(document.get("createdUtc"), "claim createdUtc") < _timestamp(
        reservation["createdUtc"], "reservation createdUtc"
    ):
        raise ValueError("candidate claim predates reservation")
    return document


def _verify_attempt_direct_inventory(
    paths: Mapping[str, Path],
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> set[str]:
    root = paths["root"]
    _verify_directory_chain(root)
    required_names = {paths[name].name for name in required}
    optional_names = {paths[name].name for name in optional}
    actual = {child.name for child in root.iterdir()}
    if not required_names.issubset(actual) or not actual.issubset(
        required_names | optional_names
    ):
        raise ValueError("successor attempt direct artifact inventory changed")
    for name in actual:
        child = root / name
        info = os.lstat(child)
        if _unsafe_linklike(child, info):
            raise ValueError("successor attempt contains a link-like artifact")
        if name == paths["practicalExecution"].name:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("practical execution artifact is not a directory")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("successor attempt artifact is not a unique regular file")
    return actual


@_serialized_transition
def terminalize_consumed_publication_failure(
    *,
    preregistration: Path,
    attempt_index: int,
    failure_stage: str,
) -> dict[str, Any]:
    """Advance a consumed k after claim or suite publication irrecoverably fails."""

    if failure_stage not in {"claim-publication", "suite-publication"}:
        raise ValueError("failure stage is not a generic consumed-publication stage")
    prereg = verify_preregistration(preregistration)
    paths = _attempt_paths(Path(prereg["artifactRoot"]), attempt_index)
    if not paths["root"].exists() or paths["closure"].exists():
        raise ValueError("consumed attempt is absent or already terminal")
    state = _global_attempt_state(prereg)
    if state.get("activeAttempt") != attempt_index:
        raise ValueError("publication failure does not name the unique active global attempt")
    reservation = verify_attempt_reservation(
        paths["reservation"], preregistration=preregistration
    )
    if failure_stage == "claim-publication":
        actual = _verify_attempt_direct_inventory(
            paths, required={"reservation"}, optional={"entropy"}
        )
        if paths["entropy"].name in actual and len(_snapshot_bytes(paths["entropy"])) != ENTROPY_BYTES:
            raise ValueError("failed claim left a noncanonical entropy artifact")
        claim_identity = None
        candidate_hash = reservation["candidateNetwork"]["sha256"]
    else:
        _verify_attempt_direct_inventory(
            paths, required={"reservation", "entropy", "claim"}
        )
        claim_doc = verify_candidate_claim(
            paths["claim"], preregistration=preregistration
        )
        claim_identity = identity(paths["claim"])
        candidate_hash = claim_doc["candidateNetwork"]["sha256"]
    created = _utc_now()
    if _timestamp(created, "publication failure closure createdUtc") < _timestamp(
        reservation["createdUtc"], "reservation createdUtc"
    ):
        raise ValueError("publication failure closure predates reservation")
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SUCCESSOR_CLOSURE_KIND,
        "status": "terminal-global-attempt-closure",
        "createdUtc": created,
        "protocol": identity(PROTOCOL_PATH),
        "preregistration": identity(preregistration),
        "attemptIndex": attempt_index,
        "terminalStage": failure_stage,
        "reservation": identity(paths["reservation"]),
        "claim": claim_identity,
        "suite": None,
        "practicalDecision": None,
        "practicalExecutionTranscript": None,
        "practicalIncidentHandoff": None,
        "hceHandoff": None,
        "hceAttemptClosure": None,
        "outcome": "aborted",
        "reason": f"{failure_stage}-failure",
        "terminal": True,
        "nextAttemptAllowed": True,
        "candidateNetworkSha256": candidate_hash,
    }
    result = _exclusive_json(paths["closure"], document)
    _verify_successor_closure(paths["closure"], attempt_index)
    return result


SUITE_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "preregistration",
    "claim",
    "attemptIndex",
    "openingPoolManifest",
    "openingPoolEntries",
    "selectionAlgorithm",
    "stageSeedCommitment",
    "pairs",
    "cellCounts",
    "entries",
    "candidateIdentityReadBySelector",
    "gameResultsRead",
    "seedPublished",
    "finalStageSeal",
}
SUITE_ENTRY_FIELDS = {
    "pairIndex",
    "openingId",
    "phase",
    "sideToMove",
    "ofen",
    "moves",
    "games",
}


def _rank_opening(key: bytes, opening_id: str) -> bytes:
    return hmac.new(
        key,
        OPENING_RANK_DOMAIN + opening_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _select_openings(rows: Sequence[Mapping[str, Any]], key: bytes) -> list[dict[str, Any]]:
    """Pure candidate-blind selector: it accepts only pool rows and a stage key."""

    buckets: dict[str, list[Mapping[str, Any]]] = {cell: [] for cell in CELLS}
    for row in rows:
        cell = f"{row['phase']}:{row['sideToMove']}"
        if cell not in buckets:
            raise ValueError("opening row has an unknown cell")
        buckets[cell].append(row)
    selected: dict[str, list[Mapping[str, Any]]] = {}
    for cell in CELLS:
        ranked = sorted(
            buckets[cell],
            key=lambda row: (_rank_opening(key, str(row["openingId"])), row["openingId"]),
        )
        if len(ranked) < PAIRS_PER_CELL:
            raise ValueError(f"opening pool cell {cell} is undersized")
        selected[cell] = ranked[:PAIRS_PER_CELL]
    output: list[dict[str, Any]] = []
    pair_index = 0
    for block in range(PAIRS_PER_CELL):
        cell_order = sorted(
            CELLS,
            key=lambda cell: hmac.new(
                key,
                b"cell-order-v1\x00" + block.to_bytes(4, "big") + cell.encode("ascii"),
                hashlib.sha256,
            ).digest(),
        )
        for cell in cell_order:
            pair_index += 1
            row = selected[cell][block]
            output.append(
                {
                    "pairIndex": pair_index,
                    "openingId": row["openingId"],
                    "phase": row["phase"],
                    "sideToMove": row["sideToMove"],
                    "ofen": " ".join(str(row["ofen"]).split()),
                    "moves": list(row["moves"]),
                    "games": ["candidate-white", "candidate-black"],
                }
            )
    return output


@_serialized_transition
def publish_practical_suite(*, preregistration: Path, claim: Path) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    claim_doc = verify_candidate_claim(claim, preregistration=preregistration)
    index = claim_doc["attemptIndex"]
    paths = _attempt_paths(Path(prereg["artifactRoot"]), index)
    if os.path.lexists(paths["suite"]):
        raise FileExistsError("practical suite slot already consumed")
    pool_manifest_path = Path(prereg["openingPool"]["manifest"]["path"])
    pool_manifest, rows = _read_opening_pool(pool_manifest_path)
    entropy = _snapshot_bytes(paths["entropy"])
    key = _stage_key(entropy, index, "practical-opening-selection")
    entries = _select_openings(rows, key)
    counts = Counter(f"{row['phase']}:{row['sideToMove']}" for row in entries)
    if (
        len(entries) != MAXIMUM_PRACTICAL_PAIRS
        or {cell: counts[cell] for cell in CELLS}
        != {cell: PAIRS_PER_CELL for cell in CELLS}
    ):
        raise AssertionError("selected practical suite is not exactly balanced")
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SUITE_KIND,
        "status": "sealed-candidate-blind-balanced-opening-pairs",
        "createdUtc": _utc_now(),
        "protocol": identity(PROTOCOL_PATH),
        "preregistration": identity(preregistration),
        "claim": identity(claim),
        "attemptIndex": index,
        "openingPoolManifest": prereg["openingPool"]["manifest"],
        "openingPoolEntries": pool_manifest["entries"],
        "selectionAlgorithm": (
            "HMAC-SHA256 rank within each phase:side cell; select 64; "
            "HMAC block cell order; exact eight-cell balanced prefixes"
        ),
        "stageSeedCommitment": claim_doc["stageSeedCommitments"]["practical-opening-selection"],
        "pairs": len(entries),
        "cellCounts": {cell: counts[cell] for cell in CELLS},
        "entries": entries,
        "candidateIdentityReadBySelector": False,
        "gameResultsRead": False,
        "seedPublished": False,
        "finalStageSeal": True,
    }
    result = _exclusive_json(paths["suite"], document)
    verify_practical_suite(paths["suite"], preregistration=preregistration, claim=claim)
    return result


def verify_practical_suite(
    path: Path, *, preregistration: Path, claim: Path
) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    claim_doc = verify_candidate_claim(claim, preregistration=preregistration)
    document = _load_json(path, "practical suite")
    _exact_keys(document, SUITE_FIELDS, "practical suite")
    _verify_header(
        document,
        kind=SUITE_KIND,
        status="sealed-candidate-blind-balanced-opening-pairs",
        label="practical suite",
    )
    index = claim_doc["attemptIndex"]
    paths = _attempt_paths(Path(prereg["artifactRoot"]), index)
    if path.resolve() != paths["suite"]:
        raise ValueError("practical suite path changed")
    pool_manifest, rows = _read_opening_pool(
        Path(prereg["openingPool"]["manifest"]["path"])
    )
    entropy = _snapshot_bytes(paths["entropy"])
    expected_entries = _select_openings(
        rows, _stage_key(entropy, index, "practical-opening-selection")
    )
    for entry in document.get("entries", []):
        _exact_keys(entry, SUITE_ENTRY_FIELDS, "practical suite entry")
    if (
        document.get("candidateIdentityReadBySelector") is not False
        or document.get("gameResultsRead") is not False
        or document.get("seedPublished") is not False
        or document.get("finalStageSeal") is not True
        or document.get("attemptIndex") != index
        or document.get("pairs") != MAXIMUM_PRACTICAL_PAIRS
        or document.get("cellCounts") != {cell: PAIRS_PER_CELL for cell in CELLS}
        or not _type_exact_equal(document.get("protocol"), identity(PROTOCOL_PATH))
        or not _type_exact_equal(document.get("preregistration"), identity(preregistration))
        or not _type_exact_equal(document.get("claim"), identity(claim))
        or not _type_exact_equal(document.get("openingPoolManifest"), prereg["openingPool"]["manifest"])
        or not _type_exact_equal(document.get("openingPoolEntries"), pool_manifest["entries"])
        or document.get("stageSeedCommitment")
        != claim_doc["stageSeedCommitments"]["practical-opening-selection"]
        or not _type_exact_equal(document.get("entries"), expected_entries)
    ):
        raise ValueError("practical suite differs from hidden-seed replay")
    _timestamp(document.get("createdUtc"), "practical suite createdUtc")
    return document


EVENT_FIELDS = {
    "schemaVersion",
    "kind",
    "attemptIndex",
    "pairIndex",
    "openingId",
    "phase",
    "sideToMove",
    "games",
    "safety",
    "terminal",
}
GAME_FIELDS = {
    "gameIndex",
    "assignment",
    "candidateHalfPoints",
    "candidateEngineSha256",
    "candidateNetworkSha256",
    "incumbentEngineSha256",
    "incumbentNetworkSha256",
    "candidateLoadedNetworkSha256",
    "incumbentLoadedNetworkSha256",
    "candidateNnueActive",
    "incumbentNnueActive",
    "terminal",
}


def _log_mean_exp(values: Sequence[float]) -> float:
    maximum = max(values)
    return maximum + math.log(
        sum(math.exp(value - maximum) for value in values) / len(values)
    )


def _sequential_practical(
    observations: Sequence[tuple[str, float]], *, attempt_index: int
) -> dict[str, Any]:
    null_score = 1.0 / (1.0 + 10.0 ** (-PRACTICAL_NULL_ELO / 400.0))
    threshold_log = _attempt_log_threshold(attempt_index)
    promotion_logs = [0.0] * len(BET_FRACTIONS)
    futility_logs = [0.0] * len(BET_FRACTIONS)
    max_promotion = 0.0
    max_futility = 0.0
    cumulative = 0.0
    signal: str | None = None
    signal_pair: int | None = None
    checkpoints: list[dict[str, Any]] = []
    for index, (pair_id, score) in enumerate(observations, 1):
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"{pair_id}: invalid candidate pair score")
        cumulative += score
        for bet_index, fraction in enumerate(BET_FRACTIONS):
            promotion_logs[bet_index] += math.log(
                1.0 + (fraction / null_score) * (score - null_score)
            )
            futility_logs[bet_index] += math.log(
                1.0
                + (fraction / (1.0 - null_score)) * (null_score - score)
            )
        promotion_log = _log_mean_exp(promotion_logs)
        futility_log = _log_mean_exp(futility_logs)
        max_promotion = max(max_promotion, promotion_log)
        max_futility = max(max_futility, futility_log)
        if index % CHECKPOINT_BLOCK == 0:
            if signal is None and index >= MINIMUM_PRACTICAL_PAIRS:
                promote = promotion_log >= threshold_log
                futile = futility_log >= math.log(FUTILITY_E_VALUE)
                if promote or futile:
                    if promote and futile:
                        signal = (
                            "promote"
                            if promotion_log - threshold_log
                            >= futility_log - math.log(FUTILITY_E_VALUE)
                            else "futility"
                        )
                    else:
                        signal = "promote" if promote else "futility"
                    signal_pair = index
            checkpoints.append(
                {
                    "pairs": index,
                    "balancedCellBlocks": index // CHECKPOINT_BLOCK,
                    "candidateScore": cumulative / index,
                    "promotionLogEValue": promotion_log,
                    "maximumPromotionLogEValue": max_promotion,
                    "promotionLogThreshold": threshold_log,
                    "futilityLogEValue": futility_log,
                    "maximumFutilityLogEValue": max_futility,
                    "futilityLogThreshold": math.log(FUTILITY_E_VALUE),
                    "signal": signal or "continue",
                    "signalPair": signal_pair,
                }
            )
    count = len(observations)
    if signal is not None:
        decision = signal
    elif count >= MAXIMUM_PRACTICAL_PAIRS:
        decision = "inconclusive"
    else:
        decision = "continue"
    return {
        "pairCount": count,
        "minimumPairs": MINIMUM_PRACTICAL_PAIRS,
        "maximumPairs": MAXIMUM_PRACTICAL_PAIRS,
        "checkpointEveryPairs": CHECKPOINT_BLOCK,
        "nullElo": PRACTICAL_NULL_ELO,
        "nullScore": null_score,
        "attemptIndex": attempt_index,
        "promotionLogThreshold": threshold_log,
        "futilityEValue": FUTILITY_E_VALUE,
        "candidateScore": cumulative / count if count else None,
        "decision": decision,
        "signal": signal or "continue",
        "signalPair": signal_pair,
        "checkpoints": checkpoints,
    }


def _read_practical_events(
    events_path: Path,
    *,
    prereg: Mapping[str, Any],
    claim: Mapping[str, Any],
    suite: Mapping[str, Any],
    allow_incomplete_prefix: bool = False,
) -> tuple[list[dict[str, Any]], list[tuple[str, float]], dict[str, int]]:
    events_path = _lexical_absolute(events_path)
    events: list[dict[str, Any]] = []
    observations: list[tuple[str, float]] = []
    totals = {name: 0 for name in SAFETY_COUNTERS}
    deployment = verify_deployment_bundle(
        Path(prereg["deploymentBundle"]["path"])
    )
    candidate_engine = deployment["engine"]["sha256"]
    candidate_network = deployment["network"]["sha256"]
    incumbent_engine = prereg["g5Incumbent"]["engine"]["sha256"]
    incumbent_network = prereg["g5Incumbent"]["network"]["sha256"]
    payload = _snapshot_bytes(events_path)
    try:
        lines = payload.decode("utf-8", errors="strict").splitlines(keepends=True)
    except UnicodeError as error:
        raise ValueError("practical events are not strict UTF-8") from error
    for line_number, line in enumerate(lines, 1):
        if not line.endswith("\n") or "\r" in line:
            raise ValueError(f"practical event line {line_number} is incomplete")
        if not line.strip():
            raise ValueError(f"practical event line {line_number} is blank")
        event = json.loads(line, object_pairs_hook=_reject_duplicate_pairs)
        _exact_keys(event, EVENT_FIELDS, f"practical event {line_number}")
        pair_index = len(events) + 1
        suite_entry = (
            suite["entries"][pair_index - 1]
            if pair_index <= len(suite["entries"])
            else None
        )
        if (
                event.get("schemaVersion") != SCHEMA_VERSION
                or event.get("kind") != EVENT_KIND
                or event.get("attemptIndex") != claim["attemptIndex"]
                or event.get("pairIndex") != pair_index
                or suite_entry is None
                or event.get("openingId") != suite_entry["openingId"]
                or event.get("phase") != suite_entry["phase"]
                or event.get("sideToMove") != suite_entry["sideToMove"]
                or event.get("terminal") is not True
                or type(event.get("games")) is not list
                or len(event["games"]) != 2
        ):
            raise ValueError(f"practical event {line_number} differs from suite prefix")
        expected_assignments = ("candidate-white", "candidate-black")
        half_points = 0
        for game_index, (game, assignment) in enumerate(
            zip(event["games"], expected_assignments), 1
        ):
            _exact_keys(game, GAME_FIELDS, f"event {line_number} game {game_index}")
            if (
                    game.get("gameIndex") != game_index
                    or game.get("assignment") != assignment
                    or type(game.get("candidateHalfPoints")) is not int
                    or game["candidateHalfPoints"] not in {0, 1, 2}
                    or game.get("candidateEngineSha256") != candidate_engine
                    or game.get("candidateNetworkSha256") != candidate_network
                    or game.get("incumbentEngineSha256") != incumbent_engine
                    or game.get("incumbentNetworkSha256") != incumbent_network
                    or game.get("candidateLoadedNetworkSha256") != candidate_network
                    or game.get("incumbentLoadedNetworkSha256") != incumbent_network
                    or game.get("candidateNnueActive") is not True
                    or game.get("incumbentNnueActive") is not True
                    or game.get("terminal") is not True
            ):
                raise ValueError(
                    f"event {line_number} game {game_index} asset/diagnostic binding changed"
                )
            half_points += game["candidateHalfPoints"]
        safety = event.get("safety")
        _exact_keys(safety, SAFETY_COUNTERS, f"event {line_number} safety")
        for name in SAFETY_COUNTERS:
            value = safety[name]
            if type(value) is not int or value < 0:
                raise ValueError(f"event {line_number} safety {name} changed")
            totals[name] += value
        observations.append((event["openingId"], half_points / 4.0))
        events.append(event)
    if (
        len(events) > MAXIMUM_PRACTICAL_PAIRS
        or (
            not allow_incomplete_prefix
            and (not events or len(events) % CHECKPOINT_BLOCK != 0)
        )
    ):
        raise ValueError("practical events are not a complete balanced checkpoint")
    # Each complete eight-pair block must contain every phase:side cell once.
    complete = len(events) - len(events) % CHECKPOINT_BLOCK
    for start in range(0, complete, CHECKPOINT_BLOCK):
        cells = {
            f"{event['phase']}:{event['sideToMove']}"
            for event in events[start : start + CHECKPOINT_BLOCK]
        }
        if cells != set(CELLS):
            raise ValueError("practical events broke balanced-cell checkpoint order")
    return events, observations, totals


PRACTICAL_DECISION_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "preregistration",
    "claim",
    "suite",
    "events",
    "executionEvidenceMode",
    "executionTranscript",
    "incidentHandoff",
    "attemptIndex",
    "candidateDeploymentBundle",
    "candidateNetwork",
    "incumbentAuthorization",
    "incumbentNetwork",
    "fixedRule",
    "sequentialAssessment",
    "safetyCounters",
    "zeroSafetyFailures",
    "decision",
    "terminal",
    "thresholdsChangedAfterResults",
}


@_serialized_transition
def publish_practical_decision(
    *, preregistration: Path, claim: Path, suite: Path, events: Path
) -> dict[str, Any]:
    """Reject the legacy unauthenticated JSONL publication surface."""

    del preregistration, claim, suite, events
    raise ValueError(
        "arbitrary practical JSONL is not an authority; use the pinned practical "
        "adapter execution-transcript or incident-handoff API"
    )


def _normal_practical_evidence(
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    events: Path,
    execution_transcript: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[tuple[str, float]],
    dict[str, int],
    dict[str, Any],
]:
    prereg = verify_preregistration(preregistration)
    claim_doc = verify_candidate_claim(claim, preregistration=preregistration)
    suite_doc = verify_practical_suite(
        suite, preregistration=preregistration, claim=claim
    )
    transcript = _practical_launch_adapter().verify_execution_transcript(
        preregistration=Path(preregistration).resolve(),
        claim=Path(claim).resolve(),
        suite=Path(suite).resolve(),
        events=Path(events).resolve(),
        transcript=Path(execution_transcript).resolve(),
    )
    event_rows, observations, safety = _read_practical_events(
        events, prereg=prereg, claim=claim_doc, suite=suite_doc
    )
    assessment = _sequential_practical(
        observations, attempt_index=claim_doc["attemptIndex"]
    )
    zero_safety = all(value == 0 for value in safety.values())
    if (
        not _type_exact_equal(transcript.get("preregistration"), identity(preregistration))
        or not _type_exact_equal(transcript.get("claim"), identity(claim))
        or not _type_exact_equal(transcript.get("suite"), identity(suite))
        or not _type_exact_equal(transcript.get("events"), identity(events))
        or transcript.get("attemptIndex") != claim_doc["attemptIndex"]
        or transcript.get("pairs") != len(event_rows)
        or not _type_exact_equal(transcript.get("safety"), safety)
        or transcript.get("zeroSafetyFailures") is not zero_safety
        or not _type_exact_equal(transcript.get("sequentialAssessment"), assessment)
        or transcript.get("incident") is not None
        or transcript.get("exactSuitePrefix") is not True
        or transcript.get("allRawTranscriptsRulesReplayed") is not True
        or transcript.get("hiddenSeedReadOrPublished") is not False
        or transcript.get("terminal") is not True
    ):
        raise ValueError("practical transcript differs from independent successor replay")
    return (
        prereg,
        claim_doc,
        suite_doc,
        transcript,
        observations,
        safety,
        assessment,
    )


def _practical_decision_value(
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    events: Path,
    evidence_mode: str,
    execution_transcript: Path | None,
    incident_handoff: Path | None,
    prereg: Mapping[str, Any],
    claim_doc: Mapping[str, Any],
    assessment: Mapping[str, Any],
    safety: Mapping[str, int],
    created_utc: str,
) -> dict[str, Any]:
    zero_safety = all(value == 0 for value in safety.values())
    decision = "safety-fail" if not zero_safety else assessment["decision"]
    if decision == "continue":
        raise ValueError("practical evidence is nonterminal; no decision may be published")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PRACTICAL_DECISION_KIND,
        "status": "terminal-paired-g6-vs-g5-decision",
        "createdUtc": created_utc,
        "protocol": identity(PROTOCOL_PATH),
        "preregistration": identity(preregistration),
        "claim": identity(claim),
        "suite": identity(suite),
        "events": identity(events),
        "executionEvidenceMode": evidence_mode,
        "executionTranscript": (
            identity(execution_transcript) if execution_transcript is not None else None
        ),
        "incidentHandoff": (
            identity(incident_handoff) if incident_handoff is not None else None
        ),
        "attemptIndex": claim_doc["attemptIndex"],
        "candidateDeploymentBundle": claim_doc["candidateDeploymentBundle"],
        "candidateNetwork": claim_doc["candidateNetwork"],
        "incumbentAuthorization": claim_doc["incumbentAuthorization"],
        "incumbentNetwork": claim_doc["incumbentNetwork"],
        "fixedRule": prereg["practicalRule"],
        "sequentialAssessment": dict(assessment),
        "safetyCounters": dict(safety),
        "zeroSafetyFailures": zero_safety,
        "decision": decision,
        "terminal": True,
        "thresholdsChangedAfterResults": False,
    }


@_serialized_transition
def publish_practical_decision_from_execution_transcript(
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    events: Path,
    execution_transcript: Path,
) -> dict[str, Any]:
    """Publish only after the pinned adapter fully replays raw execution."""

    (
        prereg,
        claim_doc,
        _suite_doc,
        transcript,
        _observations,
        safety,
        assessment,
    ) = _normal_practical_evidence(
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        events=events,
        execution_transcript=execution_transcript,
    )
    paths = _attempt_paths(Path(prereg["artifactRoot"]), claim_doc["attemptIndex"])
    if _global_attempt_state(prereg).get("activeAttempt") != claim_doc["attemptIndex"]:
        raise ValueError("practical transcript does not name the active global attempt")
    if os.path.lexists(paths["practicalDecision"]):
        raise FileExistsError("practical decision slot already consumed")
    created = _utc_now()
    if _timestamp(created, "practical decision createdUtc") < _timestamp(
        transcript["createdUtc"], "practical transcript createdUtc"
    ):
        raise ValueError("practical decision predates execution transcript")
    paths = _attempt_paths(Path(prereg["artifactRoot"]), claim_doc["attemptIndex"])
    document = _practical_decision_value(
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        events=events,
        evidence_mode="authenticated-execution-transcript",
        execution_transcript=execution_transcript,
        incident_handoff=None,
        prereg=prereg,
        claim_doc=claim_doc,
        assessment=assessment,
        safety=safety,
        created_utc=created,
    )
    result = _exclusive_json(paths["practicalDecision"], document)
    verify_practical_decision_from_execution_transcript(
        paths["practicalDecision"],
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        execution_transcript=execution_transcript,
    )
    return result


def verify_practical_decision_from_execution_transcript(
    path: Path,
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    execution_transcript: Path,
) -> dict[str, Any]:
    document = _load_json(path, "practical decision")
    events_path = verify_identity(document.get("events"), "practical events")
    (
        prereg,
        claim_doc,
        _suite_doc,
        transcript,
        _observations,
        safety,
        assessment,
    ) = _normal_practical_evidence(
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        events=events_path,
        execution_transcript=execution_transcript,
    )
    expected = _practical_decision_value(
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        events=events_path,
        evidence_mode="authenticated-execution-transcript",
        execution_transcript=execution_transcript,
        incident_handoff=None,
        prereg=prereg,
        claim_doc=claim_doc,
        assessment=assessment,
        safety=safety,
        created_utc=document.get("createdUtc"),
    )
    _exact_keys(document, PRACTICAL_DECISION_FIELDS, "practical decision")
    _verify_header(
        document,
        kind=PRACTICAL_DECISION_KIND,
        status="terminal-paired-g6-vs-g5-decision",
        label="practical decision",
    )
    paths = _attempt_paths(Path(prereg["artifactRoot"]), claim_doc["attemptIndex"])
    if (
        Path(path).resolve() != paths["practicalDecision"]
        or not _type_exact_equal(document, expected)
        or not _type_exact_equal(document.get("executionTranscript"), identity(execution_transcript))
        or document.get("incidentHandoff") is not None
    ):
        raise ValueError("practical decision differs from authenticated transcript replay")
    created = _timestamp(document.get("createdUtc"), "practical decision createdUtc")
    if created < _timestamp(transcript["createdUtc"], "practical transcript createdUtc"):
        raise ValueError("practical decision predates execution transcript")
    return document


def _incident_practical_evidence(
    *, preregistration: Path, claim: Path, suite: Path, incident_handoff: Path
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Path,
    dict[str, int],
    dict[str, Any],
]:
    prereg = verify_preregistration(preregistration)
    claim_doc = verify_candidate_claim(claim, preregistration=preregistration)
    suite_doc = verify_practical_suite(
        suite, preregistration=preregistration, claim=claim
    )
    handoff = _practical_launch_adapter().verify_incident_terminal_handoff(
        preregistration=Path(preregistration).resolve(),
        claim=Path(claim).resolve(),
        suite=Path(suite).resolve(),
        handoff=Path(incident_handoff).resolve(),
    )
    events_path = verify_identity(handoff.get("eventsPrefix"), "incident events prefix")
    events, observations, prior_safety = _read_practical_events(
        events_path,
        prereg=prereg,
        claim=claim_doc,
        suite=suite_doc,
        allow_incomplete_prefix=True,
    )
    incident_safety = handoff.get("safety")
    _exact_keys(incident_safety, SAFETY_COUNTERS, "incident handoff safety")
    if any(type(incident_safety[name]) is not int or incident_safety[name] < 0 for name in SAFETY_COUNTERS):
        raise ValueError("incident safety counters changed")
    safety = {
        name: prior_safety[name] + incident_safety[name] for name in SAFETY_COUNTERS
    }
    assessment = _sequential_practical(
        observations, attempt_index=claim_doc["attemptIndex"]
    )
    if (
        handoff.get("attemptIndex") != claim_doc["attemptIndex"]
        or handoff.get("completedPairs") != len(events)
        or handoff.get("requestedSuccessorDecision") != "safety-fail"
        or handoff.get("requestedGlobalOutcome") != "failed"
        or handoff.get("nextAttemptAllowed") is not True
        or handoff.get("successorEventFabricated") is not False
        or handoff.get("hiddenSeedReadOrPublished") is not False
        or handoff.get("terminal") is not True
        or not any(safety.values())
    ):
        raise ValueError("incident handoff cannot authorize a successor safety failure")
    return prereg, claim_doc, suite_doc, handoff, events_path, safety, assessment


def _verify_practical_incident_decision(
    path: Path,
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    incident_handoff: Path,
) -> dict[str, Any]:
    (
        prereg,
        claim_doc,
        _suite_doc,
        handoff,
        events_path,
        safety,
        assessment,
    ) = _incident_practical_evidence(
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        incident_handoff=incident_handoff,
    )
    document = _load_json(path, "practical decision")
    _exact_keys(document, PRACTICAL_DECISION_FIELDS, "practical decision")
    _verify_header(
        document,
        kind=PRACTICAL_DECISION_KIND,
        status="terminal-paired-g6-vs-g5-decision",
        label="practical decision",
    )
    expected = _practical_decision_value(
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        events=events_path,
        evidence_mode="authenticated-incident-terminal-handoff",
        execution_transcript=None,
        incident_handoff=incident_handoff,
        prereg=prereg,
        claim_doc=claim_doc,
        assessment=assessment,
        safety=safety,
        created_utc=document.get("createdUtc"),
    )
    paths = _attempt_paths(Path(prereg["artifactRoot"]), claim_doc["attemptIndex"])
    if (
        Path(path).resolve() != paths["practicalDecision"]
        or not _type_exact_equal(document, expected)
        or document.get("decision") != "safety-fail"
        or document.get("zeroSafetyFailures") is not False
    ):
        raise ValueError("practical incident decision differs from authenticated handoff")
    if _timestamp(document.get("createdUtc"), "practical decision createdUtc") < _timestamp(
        handoff["createdUtc"], "incident handoff createdUtc"
    ):
        raise ValueError("practical incident decision predates handoff")
    return document


def verify_practical_decision(
    path: Path, *, preregistration: Path, claim: Path, suite: Path
) -> dict[str, Any]:
    """Dispatch only to one of the two authenticated practical evidence modes."""

    document = _load_json(path, "practical decision")
    mode = document.get("executionEvidenceMode")
    if mode == "authenticated-execution-transcript":
        transcript = verify_identity(
            document.get("executionTranscript"), "practical execution transcript"
        )
        if document.get("incidentHandoff") is not None:
            raise ValueError("normal practical decision also cites an incident")
        return verify_practical_decision_from_execution_transcript(
            path,
            preregistration=preregistration,
            claim=claim,
            suite=suite,
            execution_transcript=transcript,
        )
    if mode == "authenticated-incident-terminal-handoff":
        handoff = verify_identity(
            document.get("incidentHandoff"), "practical incident handoff"
        )
        if document.get("executionTranscript") is not None:
            raise ValueError("incident practical decision also cites a normal transcript")
        return _verify_practical_incident_decision(
            path,
            preregistration=preregistration,
            claim=claim,
            suite=suite,
            incident_handoff=handoff,
        )
    raise ValueError("practical decision lacks pinned-adapter execution authority")


@_serialized_transition
def publish_practical_incident_failure(
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    incident_handoff: Path,
) -> dict[str, Any]:
    """Fail closed from the practical adapter's authenticated incident bridge."""

    (
        prereg,
        claim_doc,
        _suite_doc,
        handoff,
        events_path,
        safety,
        assessment,
    ) = _incident_practical_evidence(
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        incident_handoff=incident_handoff,
    )
    paths = _attempt_paths(Path(prereg["artifactRoot"]), claim_doc["attemptIndex"])
    if not paths["closure"].exists() and (
        _global_attempt_state(prereg).get("activeAttempt")
        != claim_doc["attemptIndex"]
    ):
        raise ValueError("practical incident does not name the active global attempt")
    if paths["practicalDecision"].exists():
        decision = _verify_practical_incident_decision(
            paths["practicalDecision"],
            preregistration=preregistration,
            claim=claim,
            suite=suite,
            incident_handoff=incident_handoff,
        )
    else:
        created = _utc_now()
        if _timestamp(created, "incident decision createdUtc") < _timestamp(
            handoff["createdUtc"], "incident handoff createdUtc"
        ):
            raise ValueError("incident decision predates authenticated handoff")
        decision_value = _practical_decision_value(
            preregistration=preregistration,
            claim=claim,
            suite=suite,
            events=events_path,
            evidence_mode="authenticated-incident-terminal-handoff",
            execution_transcript=None,
            incident_handoff=incident_handoff,
            prereg=prereg,
            claim_doc=claim_doc,
            assessment=assessment,
            safety=safety,
            created_utc=created,
        )
        _exclusive_json(paths["practicalDecision"], decision_value)
        decision = _verify_practical_incident_decision(
            paths["practicalDecision"],
            preregistration=preregistration,
            claim=claim,
            suite=suite,
            incident_handoff=incident_handoff,
        )
    if decision["decision"] != "safety-fail":
        raise ValueError("incident bridge did not produce a safety failure")
    if not paths["closure"].exists():
        closure_created = _utc_now()
        if _timestamp(closure_created, "incident closure createdUtc") < _timestamp(
            decision["createdUtc"], "incident decision createdUtc"
        ):
            raise ValueError("incident closure predates practical decision")
        closure_value = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": SUCCESSOR_CLOSURE_KIND,
            "status": "terminal-global-attempt-closure",
            "createdUtc": closure_created,
            "protocol": identity(PROTOCOL_PATH),
            "preregistration": identity(preregistration),
            "attemptIndex": claim_doc["attemptIndex"],
            "terminalStage": "practical",
            "reservation": claim_doc["reservation"],
            "claim": identity(claim),
            "suite": identity(suite),
            "practicalDecision": identity(paths["practicalDecision"]),
            "practicalExecutionTranscript": None,
            "practicalIncidentHandoff": identity(incident_handoff),
            "hceHandoff": None,
            "hceAttemptClosure": None,
            "outcome": "failed",
            "reason": "practical-safety-fail-incident",
            "terminal": True,
            "nextAttemptAllowed": True,
            "candidateNetworkSha256": claim_doc["candidateNetwork"]["sha256"],
        }
        _exclusive_json(paths["closure"], closure_value)
    result = {
        "practicalDecision": identity(paths["practicalDecision"]),
        "attemptClosure": identity(paths["closure"]),
    }
    verify_practical_incident_failure(
        practical_decision=paths["practicalDecision"],
        attempt_closure=paths["closure"],
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        incident_handoff=incident_handoff,
    )
    return result


def verify_practical_incident_failure(
    *,
    practical_decision: Path,
    attempt_closure: Path,
    preregistration: Path,
    claim: Path,
    suite: Path,
    incident_handoff: Path,
) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    claim_doc = verify_candidate_claim(claim, preregistration=preregistration)
    decision = _verify_practical_incident_decision(
        practical_decision,
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        incident_handoff=incident_handoff,
    )
    closure = _verify_successor_closure(
        attempt_closure, claim_doc["attemptIndex"]
    )
    paths = _attempt_paths(Path(prereg["artifactRoot"]), claim_doc["attemptIndex"])
    if (
        Path(attempt_closure).resolve() != paths["closure"]
        or decision["decision"] != "safety-fail"
        or closure.get("terminalStage") != "practical"
        or closure.get("outcome") != "failed"
        or closure.get("reason") != "practical-safety-fail-incident"
        or closure.get("nextAttemptAllowed") is not True
        or not _type_exact_equal(closure.get("reservation"), claim_doc["reservation"])
        or not _type_exact_equal(closure.get("claim"), identity(claim))
        or not _type_exact_equal(closure.get("suite"), identity(suite))
        or not _type_exact_equal(
            closure.get("practicalDecision"), identity(practical_decision)
        )
        or closure.get("practicalExecutionTranscript") is not None
        or not _type_exact_equal(
            closure.get("practicalIncidentHandoff"), identity(incident_handoff)
        )
        or closure.get("hceHandoff") is not None
        or closure.get("hceAttemptClosure") is not None
        or _timestamp(closure["createdUtc"], "incident closure createdUtc")
        < _timestamp(decision["createdUtc"], "incident decision createdUtc")
    ):
        raise ValueError("incident closure differs from authenticated safety failure")
    return {"practicalDecision": decision, "attemptClosure": closure}


HCE_HANDOFF_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "preregistration",
    "claim",
    "practicalDecision",
    "attemptIndex",
    "attemptBeta",
    "promotionLogThreshold",
    "candidateDeploymentBundle",
    "candidateEngine",
    "candidateNetwork",
    "controlEngine",
    "controlNetwork",
    "engineOptions",
    "formalGates",
    "frozenV2Authorities",
    "stageSeedCommitments",
    "entropyBytesPublished",
    "stageSeedsPublished",
    "globalAttemptOrAlphaReset",
    "terminal",
}


@_serialized_transition
def publish_hce_handoff(
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    practical_decision: Path,
) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    claim_doc = verify_candidate_claim(claim, preregistration=preregistration)
    decision = verify_practical_decision(
        practical_decision,
        preregistration=preregistration,
        claim=claim,
        suite=suite,
    )
    if decision["decision"] != "promote" or decision["zeroSafetyFailures"] is not True:
        raise ValueError("HCE handoff requires a safe practical promotion")
    deployment = verify_deployment_bundle(
        Path(prereg["deploymentBundle"]["path"])
    )
    paths = _attempt_paths(Path(prereg["artifactRoot"]), claim_doc["attemptIndex"])
    if _global_attempt_state(prereg).get("activeAttempt") != claim_doc["attemptIndex"]:
        raise ValueError("HCE handoff does not name the active global attempt")
    pinned = _verify_pinned_authorities()
    formal = [
        {
            "gate": gate,
            "nullElo": FORMAL_HCE_NULL_ELO,
            "maximumPairs": 512,
            "promotionLogThreshold": claim_doc["promotionLogThreshold"],
            "sameAttemptIndex": claim_doc["attemptIndex"],
            "zeroSafetyFailuresRequired": True,
        }
        for gate in FORMAL_HCE_GATES
    ]
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HCE_HANDOFF_KIND,
        "status": "authorized-for-three-frozen-hce-gates",
        "createdUtc": _utc_now(),
        "protocol": identity(PROTOCOL_PATH),
        "preregistration": identity(preregistration),
        "claim": identity(claim),
        "practicalDecision": identity(practical_decision),
        "attemptIndex": claim_doc["attemptIndex"],
        "attemptBeta": claim_doc["attemptBeta"],
        "promotionLogThreshold": claim_doc["promotionLogThreshold"],
        "candidateDeploymentBundle": prereg["deploymentBundle"],
        "candidateEngine": deployment["engine"],
        "candidateNetwork": deployment["network"],
        "controlEngine": deployment["engine"],
        "controlNetwork": None,
        "engineOptions": {
            "common": FORMAL_HCE_COMMON_OPTIONS,
            "candidate": {
                "UseOmegaNNUE": "true",
                "OmegaNNUEFile": deployment["network"]["path"],
            },
            "hceControl": {
                "UseOmegaNNUE": "false",
                "OmegaNNUEFile": "<empty>",
            },
        },
        "formalGates": formal,
        "frozenV2Authorities": {
            name: pinned[name]
            for name in (
                "v2ProtocolJson",
                "v2ProtocolTool",
                "v2Readiness",
                "v2Practical",
                "v2Matches",
            )
        },
        "stageSeedCommitments": {
            gate: claim_doc["stageSeedCommitments"][gate]
            for gate in FORMAL_HCE_GATES
        },
        "entropyBytesPublished": 0,
        "stageSeedsPublished": False,
        "globalAttemptOrAlphaReset": False,
        "terminal": False,
    }
    result = _exclusive_json(paths["hceHandoff"], document)
    verify_hce_handoff(
        paths["hceHandoff"],
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        practical_decision=practical_decision,
    )
    return result


def verify_hce_handoff(
    path: Path,
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    practical_decision: Path,
) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    claim_doc = verify_candidate_claim(claim, preregistration=preregistration)
    decision = verify_practical_decision(
        practical_decision,
        preregistration=preregistration,
        claim=claim,
        suite=suite,
    )
    if decision["decision"] != "promote" or decision["zeroSafetyFailures"] is not True:
        raise ValueError("unsafe/nonpromoted practical decision cannot authorize HCE")
    document = _load_json(path, "HCE handoff")
    _exact_keys(document, HCE_HANDOFF_FIELDS, "HCE handoff")
    _verify_header(
        document,
        kind=HCE_HANDOFF_KIND,
        status="authorized-for-three-frozen-hce-gates",
        label="HCE handoff",
    )
    deployment = verify_deployment_bundle(Path(prereg["deploymentBundle"]["path"]))
    pinned = _verify_pinned_authorities()
    expected_formal = [
        {
            "gate": gate,
            "nullElo": FORMAL_HCE_NULL_ELO,
            "maximumPairs": 512,
            "promotionLogThreshold": claim_doc["promotionLogThreshold"],
            "sameAttemptIndex": claim_doc["attemptIndex"],
            "zeroSafetyFailuresRequired": True,
        }
        for gate in FORMAL_HCE_GATES
    ]
    expected_v2 = {
        name: pinned[name]
        for name in (
            "v2ProtocolJson",
            "v2ProtocolTool",
            "v2Readiness",
            "v2Practical",
            "v2Matches",
        )
    }
    if (
        document.get("attemptIndex") != claim_doc["attemptIndex"]
        or document.get("attemptBeta") != claim_doc["attemptBeta"]
        or document.get("promotionLogThreshold") != claim_doc["promotionLogThreshold"]
        or document.get("entropyBytesPublished") != 0
        or document.get("stageSeedsPublished") is not False
        or document.get("globalAttemptOrAlphaReset") is not False
        or document.get("terminal") is not False
        or not _type_exact_equal(document.get("protocol"), identity(PROTOCOL_PATH))
        or not _type_exact_equal(document.get("preregistration"), identity(preregistration))
        or not _type_exact_equal(document.get("claim"), identity(claim))
        or not _type_exact_equal(document.get("practicalDecision"), identity(practical_decision))
        or not _type_exact_equal(document.get("candidateDeploymentBundle"), prereg["deploymentBundle"])
        or not _type_exact_equal(document.get("candidateEngine"), deployment["engine"])
        or not _type_exact_equal(document.get("candidateNetwork"), deployment["network"])
        or not _type_exact_equal(document.get("controlEngine"), deployment["engine"])
        or document.get("controlNetwork") is not None
        or document.get("formalGates") != expected_formal
        or document.get("frozenV2Authorities") != expected_v2
        or document.get("stageSeedCommitments")
        != {gate: claim_doc["stageSeedCommitments"][gate] for gate in FORMAL_HCE_GATES}
        or document.get("engineOptions", {}).get("hceControl")
        != {"UseOmegaNNUE": "false", "OmegaNNUEFile": "<empty>"}
        or document.get("engineOptions", {}).get("common")
        != FORMAL_HCE_COMMON_OPTIONS
        or document.get("engineOptions", {}).get("candidate")
        != {
            "UseOmegaNNUE": "true",
            "OmegaNNUEFile": deployment["network"]["path"],
        }
    ):
        raise ValueError("HCE handoff changed attempt, alpha, engine, or gate authority")
    _timestamp(document.get("createdUtc"), "HCE handoff createdUtc")
    return document


@_serialized_transition
def close_practical_failure(
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    practical_decision: Path,
) -> dict[str, Any]:
    prereg = verify_preregistration(preregistration)
    claim_doc = verify_candidate_claim(claim, preregistration=preregistration)
    decision = verify_practical_decision(
        practical_decision,
        preregistration=preregistration,
        claim=claim,
        suite=suite,
    )
    if decision["decision"] == "promote":
        raise ValueError("a practical promotion must continue to the HCE handoff")
    paths = _attempt_paths(Path(prereg["artifactRoot"]), claim_doc["attemptIndex"])
    if _global_attempt_state(prereg).get("activeAttempt") != claim_doc["attemptIndex"]:
        raise ValueError("practical failure does not name the active global attempt")
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SUCCESSOR_CLOSURE_KIND,
        "status": "terminal-global-attempt-closure",
        "createdUtc": _utc_now(),
        "protocol": identity(PROTOCOL_PATH),
        "preregistration": identity(preregistration),
        "attemptIndex": claim_doc["attemptIndex"],
        "terminalStage": "practical",
        "reservation": claim_doc["reservation"],
        "claim": identity(claim),
        "suite": identity(suite),
        "practicalDecision": identity(practical_decision),
        "practicalExecutionTranscript": decision["executionTranscript"],
        "practicalIncidentHandoff": decision["incidentHandoff"],
        "hceHandoff": None,
        "hceAttemptClosure": None,
        "outcome": "failed",
        "reason": f"practical-{decision['decision']}",
        "terminal": True,
        "nextAttemptAllowed": True,
        "candidateNetworkSha256": claim_doc["candidateNetwork"]["sha256"],
    }
    result = _exclusive_json(paths["closure"], document)
    _verify_successor_closure(paths["closure"], claim_doc["attemptIndex"])
    return result


@_serialized_transition
def bridge_hce_attempt_closure(
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    practical_decision: Path,
    hce_handoff: Path,
    hce_attempt_closure: Path,
) -> dict[str, Any]:
    """Close the shared global k after the frozen v2 gates finish.

    The byte-pinned launch adapter remains the match-evidence authority.  This
    successor independently descriptor-loads that adapter and requires its
    full canonical-path, authorization, gate-order, decision, clock, and
    safety replay before publishing the shared global closure.
    """

    prereg = verify_preregistration(preregistration)
    claim_doc = verify_candidate_claim(claim, preregistration=preregistration)
    verify_hce_handoff(
        hce_handoff,
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        practical_decision=practical_decision,
    )
    # `_v2_closure_shape` is intentionally not an authorization boundary.  The
    # exact pinned adapter below replays its canonical namespace and frozen-v2
    # evidence, so direct callers cannot bypass adapter.bridge with a merely
    # v2-shaped forged closure.
    adapter = _hce_launch_adapter()
    v2 = adapter.verify_attempt_closure(
        Path(hce_attempt_closure).resolve(), handoff=Path(hce_handoff).resolve()
    )
    _v2_closure_shape(hce_attempt_closure, claim_doc["attemptIndex"])
    if v2["candidateNetworkSha256"] != claim_doc["candidateNetwork"]["sha256"]:
        raise ValueError("v2 HCE closure used a different candidate network")
    paths = _attempt_paths(Path(prereg["artifactRoot"]), claim_doc["attemptIndex"])
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SUCCESSOR_CLOSURE_KIND,
        "status": "terminal-global-attempt-closure",
        "createdUtc": _utc_now(),
        "protocol": identity(PROTOCOL_PATH),
        "preregistration": identity(preregistration),
        "attemptIndex": claim_doc["attemptIndex"],
        "terminalStage": "hce",
        "reservation": claim_doc["reservation"],
        "claim": identity(claim),
        "suite": identity(suite),
        "practicalDecision": identity(practical_decision),
        "practicalExecutionTranscript": verify_practical_decision(
            practical_decision,
            preregistration=preregistration,
            claim=claim,
            suite=suite,
        )["executionTranscript"],
        "practicalIncidentHandoff": None,
        "hceHandoff": identity(hce_handoff),
        "hceAttemptClosure": identity(hce_attempt_closure),
        "outcome": v2["outcome"],
        "reason": f"hce-{v2['reason']}",
        "terminal": True,
        "nextAttemptAllowed": v2["nextAttemptAllowed"],
        "candidateNetworkSha256": claim_doc["candidateNetwork"]["sha256"],
    }
    result = _exclusive_json(paths["closure"], document)
    _verify_successor_closure(paths["closure"], claim_doc["attemptIndex"])
    return result


def readiness(preregistration: Path | None = None) -> dict[str, Any]:
    path = (
        Path(preregistration).resolve()
        if preregistration is not None
        else _artifact_paths(DEFAULT_ARTIFACT_ROOT)["preregistration"]
    )
    if not path.exists():
        return {
            "protocolId": PROTOCOL_ID,
            "state": "not-preregistered",
            "nextAction": "publish deployment bundle and preregistration before heldout access",
        }
    prereg = verify_preregistration(path)
    decision_path = _artifact_paths(Path(prereg["artifactRoot"]))["heldoutDecision"]
    if not decision_path.exists():
        expected = prereg["g6"]["heldoutExpectedPaths"]
        complete = all(Path(raw).is_file() for raw in expected.values())
        return {
            "protocolId": PROTOCOL_ID,
            "state": "awaiting-heldout-decision" if complete else "preregistered-awaiting-heldout",
            "nextAction": "publish heldout decision" if complete else "run the one-shot G6 heldout stage",
        }
    decision = verify_heldout_decision(decision_path, preregistration=path)
    if decision["decision"] == "reject":
        return {
            "protocolId": PROTOCOL_ID,
            "state": "heldout-rejected",
            "nextAction": "do not consume a practical confirmation attempt",
        }
    state = _global_attempt_state(prereg)
    if state["activeAttempt"] is None:
        if state["nextAttemptIndex"] is None:
            return {"protocolId": PROTOCOL_ID, "state": "confirmed", "nextAction": None}
        return {
            "protocolId": PROTOCOL_ID,
            "state": "ready-to-claim",
            "nextGlobalAttemptIndex": state["nextAttemptIndex"],
            "nextAction": "claim the paired G6-vs-G5 practical gate",
        }
    index = state["activeAttempt"]
    paths = _attempt_paths(Path(prereg["artifactRoot"]), index)
    if not paths["claim"].exists():
        action = "attempt consumed; close as claim-publication failure"
    elif not paths["suite"].exists():
        action = "seal candidate-blind practical suite"
    elif not paths["practicalDecision"].exists():
        action = "run externally and publish a terminal practical decision"
    else:
        practical = _load_json(paths["practicalDecision"], "active practical decision")
        if practical.get("decision") == "promote" and not paths["hceHandoff"].exists():
            action = "publish HCE handoff"
        elif practical.get("decision") == "promote":
            action = "run the three frozen HCE gates at the same global attempt index"
        else:
            action = "close failed practical attempt"
    return {
        "protocolId": PROTOCOL_ID,
        "state": "active-attempt",
        "activeAttempt": index,
        "nextAction": action,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("print-protocol")
    sub.add_parser("self-test")
    status = sub.add_parser("status")
    status.add_argument("--preregistration", type=Path)
    verify_p = sub.add_parser("verify-preregistration")
    verify_p.add_argument("path", type=Path)
    verify_d = sub.add_parser("verify-heldout-decision")
    verify_d.add_argument("path", type=Path)
    verify_d.add_argument("--preregistration", type=Path, required=True)
    g5_worker = sub.add_parser("g5-replay-worker", help=argparse.SUPPRESS)
    g5_worker.add_argument("--authorization", type=Path, required=True)
    g5_worker.add_argument("--closure", type=Path, required=True)
    return parser


def self_test() -> None:
    validate_protocol()
    if not math.isclose(
        _attempt_log_threshold(1), math.log(200.0), rel_tol=0.0, abs_tol=1e-15
    ):
        raise AssertionError("attempt-1 log threshold changed")
    try:
        _attempt_log_threshold(FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX)
    except ValueError as error:
        if "not a diagnostic successor" not in str(error):
            raise AssertionError("exhaustion diagnostic regressed") from error
    else:
        raise AssertionError("statistically exhausted attempt was admitted")
    baseline = {
        "decisionRoots": 8,
        "cells": {
            cell: {
                "roots": 1,
                "topSetAccuracy": 0.80,
                "meanChosenMoveRegretCp": 100.0,
                "listwiseCrossEntropy": 1.0,
                "pointwiseHuber": 1.0,
            }
            for cell in CELLS
        },
        "macro": {
            "topSetAccuracy": 0.80,
            "meanChosenMoveRegretCp": 100.0,
            "listwiseCrossEntropy": 1.0,
            "pointwiseHuber": 1.0,
        },
    }
    better = json.loads(json.dumps(baseline))
    better["macro"]["topSetAccuracy"] = 0.81
    better["macro"]["meanChosenMoveRegretCp"] = 99.0
    for cell in CELLS:
        better["cells"][cell]["topSetAccuracy"] = 0.81
        better["cells"][cell]["meanChosenMoveRegretCp"] = 99.0
    if _heldout_nomination_math(baseline, better)["nominate"] is not True:
        raise AssertionError("fixed heldout rule rejected a strict Pareto improvement")
    observations = [(f"p{index}", 1.0) for index in range(1, 129)]
    assessment = _sequential_practical(observations, attempt_index=1)
    if assessment["decision"] not in {"promote", "continue"}:
        raise AssertionError("winning practical stream produced a negative signal")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "print-protocol":
        sys.stdout.write(_canonical_json(_protocol_document()).decode("utf-8"))
    elif args.command == "self-test":
        self_test()
        print("Generation-6 promotion successor self-test: PASS")
    elif args.command == "status":
        print(json.dumps(readiness(args.preregistration), indent=2, sort_keys=True))
    elif args.command == "verify-preregistration":
        value = verify_preregistration(args.path)
        print(f"Verified successor preregistration: {value['status']}")
    elif args.command == "verify-heldout-decision":
        value = verify_heldout_decision(
            args.path, preregistration=args.preregistration
        )
        print(f"Verified heldout decision: {value['decision']}")
    elif args.command == "g5-replay-worker":
        sys.stdout.buffer.write(
            _canonical_json(_g5_replay_worker(args.authorization, args.closure))
        )
    else:  # pragma: no cover
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
