#!/usr/bin/env python3
"""Leakage-resistant generation-5 Omega NNUE trainer.

Generation 5 is deliberately decision aligned: every training example belongs
to a four-child root decision.  The pointwise target is the deep HCE score
minus Senpai's static HCE, while a second loss teaches the ordering of the four
siblings.  Before the one-time offline gate, split-2 (held-out) rows are routed
using their string-valued split field and discarded before JSON target decoding.

The public workflow is ``plan``, ``run``, ``recover-primary-claim``, ``select``,
``run-robustness``, ``offline-evaluate`` and ``self-test``.
``train-worker`` is an internal, identity-pinned command emitted by ``plan``.
Only ``offline-evaluate`` may read held-out target fields, and it must publish
the frozen single-access claim first.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


# Deterministic reductions are part of the frozen worker contract.  These must
# be installed before NumPy is imported.
DETERMINISTIC_WORKER_ENVIRONMENT = {
    "BLIS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "PYTHONHASHSEED": "0",
}
for _name, _value in DETERMINISTIC_WORKER_ENVIRONMENT.items():
    os.environ[_name] = _value

import king_state_generation5_runtime as runtime_contract
import numpy as np

if runtime_contract.DETERMINISTIC_WORKER_ENVIRONMENT != DETERMINISTIC_WORKER_ENVIRONMENT:
    raise RuntimeError("trainer and frozen runtime environment contracts differ")

import train as base
import validate_king_state_v5_preregistration as prereg_validator
import omega_nnue as network_format
from omega_nnue import (
    ACCUMULATOR_SIZE,
    ACTIVATION_MAX,
    ARCHITECTURE_KING_STATE_RESIDUAL,
    ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
    HEADER_BYTES,
    HIDDEN_DIVISOR,
    HIDDEN_SIZE,
    KING_BUCKET_COUNT,
    KING_STATE_FEATURE_COUNT,
    KING_STATE_OCCUPANCY_FEATURES,
    OMEGA_INTERACTION_FEATURE_COUNT,
    OMEGA_INTERACTION_FEATURES,
    OMEGA_INTERACTION_RESIDUAL_LIMIT_CP,
    OUTPUT_DIVISOR,
    QuantizedNetwork,
    active_features,
    nnue_input_signature,
    payload_bytes_for_architecture,
)


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v5-omega-decision-v2"
PLAN_KIND = "omega-nnue-king-state-v5-training-plan"
CANDIDATE_KIND = "omega-nnue-king-state-v5-candidate"
FAILURE_KIND = "omega-nnue-king-state-v5-candidate-failure"
SELECTION_KIND = "omega-nnue-king-state-v5-validation-selection"
ROBUSTNESS_KIND = "omega-nnue-king-state-v5-robustness"
ROBUSTNESS_FAILURE_KIND = "omega-nnue-king-state-v5-robustness-failure"
OFFLINE_CLAIM_KIND = "omega-nnue-king-state-v5-offline-access-claim"
OFFLINE_REPORT_KIND = "omega-nnue-king-state-v5-offline-report"
PROFILE_KIND = "omega-nnue-king-state-v5-preregistration"
PROFILE_STATUS = "target-blind-generation-5-design-frozen-before-teacher-labels"
FINAL_FREEZE_KIND = "omega-nnue-king-state-v5-final-freeze-seal"
PHASES = ("opening", "middlegame", "late", "endgame")
SPLITS = {"train": 0, "validation": 1, "heldOut": 2}
CANDIDATES = ("G5A", "G5B", "G5C")
TIE_PRIORITY = ("G5B", "G5C", "G5A")
CANDIDATE_RECIPES = {
    "G5A": {"rank": 4, "rankingWeight": 0.25},
    "G5B": {"rank": 4, "rankingWeight": 0.50},
    "G5C": {"rank": 8, "rankingWeight": 0.50},
}

EPOCHS = 48
QAT_EPOCHS = 12
FIRST_QAT_EPOCH = 37
BATCH_SIZE = 256
ROOTS_PER_BATCH = BATCH_SIZE // 4
LEARNING_RATE = 0.003
QAT_LR_SCALE = 0.1
CP_CLIP = 2000.0
CP_NORMALIZER = 100.0
HUBER_DELTA = 2.0
RANKING_IGNORE_BELOW_CP = 20
RANKING_GAP_CAP_CP = 600
TARGET_FIELDS_DECODED_PER_ROW = (
    "deepScoreCpChildStm",
    "deepScoreCpRoot",
    "deepRank",
    "deepRegretCp",
    "rankingEligibleAgainstBest",
    "rankingGapCpCapped",
)
ACTIVATION_PENALTY = 32.0
ACTIVATION_TARGET = 0.62
ACTIVATION_TEMPERATURE = 16.0
BASE_PIECE_FEATURES = KING_STATE_OCCUPANCY_FEATURES // KING_BUCKET_COUNT
EXPECTED_NETWORK_BYTES = HEADER_BYTES + payload_bytes_for_architecture(
    ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
)
CPP_NETWORK_MODE = "--evaluate-network-stream"
CPP_HCE_MODE = "--evaluate-handcrafted-stream"

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "build-msvc" / "data-generation" / "omega-decision-v2"
OUTPUT_DIR = REPO / "build-msvc" / "king-state-v5"
DEFAULT_CORPUS = DATA_DIR / "decision-labels.jsonl"
DEFAULT_CORPUS_MANIFEST = DATA_DIR / "decision-labels.jsonl.manifest.json"
DEFAULT_PROFILE = (
    REPO / "validation" / "omega-nnue-king-state-v5-preregistration.json"
)
DEFAULT_PLAN = OUTPUT_DIR / "training-plan.json"
DEFAULT_SELECTION = OUTPUT_DIR / "validation-selection.seal.json"
DEFAULT_ROBUSTNESS = OUTPUT_DIR / "robustness.seal.json"
DEFAULT_OFFLINE_CLAIM = OUTPUT_DIR / "offline" / "access-claim.json"
DEFAULT_OFFLINE_REPORT = OUTPUT_DIR / "offline" / "report.json"
DEFAULT_CPP_EVALUATOR = (
    REPO
    / "tools"
    / "omega_nnue"
    / "frozen_runtime"
    / "king-state-v5"
    / "evaluator"
    / "omega_nnue.exe"
)

OFFLINE_BOOTSTRAP_REPLICATES = 10_000
OFFLINE_BOOTSTRAP_SEED = 2026072307
PRIMARY_CLAIM_LOCK_PROTOCOL = "exclusive-os-file-lock-held-for-worker-lifetime-v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    resolved = _resolve(path)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "bytes": stat.st_size,
        "sha256": _sha256(resolved),
    }


def _payload_identity(path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "path": str(_resolve(path)),
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        resolved.unlink(missing_ok=True)
        raise


def _exclusive_json(path: Path, value: Any) -> None:
    _exclusive_bytes(path, _canonical_json(value))


def _atomic_exclusive_bytes(
    path: Path,
    payload: bytes,
    *,
    interruption_hook: Callable[[str], None] | None = None,
) -> None:
    """Commit complete bytes atomically at a new final path, never replacing it."""

    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(
        prefix=f".{resolved.name}.",
        suffix=".staging",
        dir=resolved.parent,
    )
    staging = Path(staging_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if interruption_hook is not None:
            interruption_hook("after-stage-before-commit")
        # A same-directory hard link is an atomic create-if-absent operation on
        # both NTFS and POSIX filesystems.  Unlike os.rename on POSIX, it cannot
        # silently replace an existing final artifact.
        os.link(staging, resolved)
    finally:
        staging.unlink(missing_ok=True)


def _atomic_exclusive_json(
    path: Path,
    value: Any,
    *,
    interruption_hook: Callable[[str], None] | None = None,
) -> None:
    _atomic_exclusive_bytes(
        path,
        _canonical_json(value),
        interruption_hook=interruption_hook,
    )


def _publish_or_verify_deterministic_bytes(path: Path, payload: bytes) -> dict[str, Any]:
    """Publish deterministic bytes once, or authenticate an interrupted prior write.

    This is intentionally narrower than a general resume primitive: an existing
    file is accepted only when its complete bytes are exactly those that this
    invocation independently recomputed.  Nothing is ever replaced.
    """

    resolved = _resolve(path)
    expected = _payload_identity(resolved, payload)
    if not resolved.exists():
        try:
            _exclusive_bytes(resolved, payload)
        except FileExistsError:
            # A concurrent publisher won O_EXCL.  It is acceptable only after
            # the same complete-byte authentication as an interrupted write.
            pass
    if not resolved.is_file():
        raise ValueError(f"deterministic artifact is not a regular file: {resolved}")
    actual = _identity(resolved)
    if actual != expected or resolved.read_bytes() != payload:
        raise ValueError(
            f"existing deterministic artifact differs from recomputation: {resolved}"
        )
    return actual


def _try_claim_file_lock(stream: Any) -> bool:
    """Try to take the claim file's byte-zero lock without waiting."""

    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True
    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise
    return True


def _unlock_claim_file(stream: Any) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass
class _HeldClaimLock:
    stream: Any
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        try:
            _unlock_claim_file(self.stream)
        finally:
            self.stream.close()
            self.released = True


def _exclusive_locked_json(path: Path, value: Any) -> _HeldClaimLock:
    """Create a no-clobber JSON claim while holding its lifetime OS lock."""

    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json(value)
    descriptor = os.open(resolved, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    held: _HeldClaimLock | None = None
    try:
        if not _try_claim_file_lock(stream):
            raise RuntimeError("new exclusive claim file could not be locked")
        held = _HeldClaimLock(stream)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        return held
    except BaseException:
        if held is not None:
            held.release()
        else:
            stream.close()
        resolved.unlink(missing_ok=True)
        raise


def _acquire_existing_claim_lock(path: Path) -> _HeldClaimLock | None:
    stream = _resolve(path).open("r+b", buffering=0)
    try:
        if not _try_claim_file_lock(stream):
            stream.close()
            return None
        return _HeldClaimLock(stream)
    except BaseException:
        stream.close()
        raise


def _read_held_claim(
    path: Path, held: _HeldClaimLock, label: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read identity and strict JSON through the handle owning the byte lock."""

    held.stream.seek(0)
    payload = held.stream.read()
    if os.fstat(held.stream.fileno()).st_size != len(payload):
        raise ValueError("locked primary claim size changed while reading")
    identity = _payload_identity(path, payload)
    try:
        value = _strict_json_loads(payload.decode("utf-8"), location=str(_resolve(path)))
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object")
    return identity, value


def _machine_name() -> str:
    return platform.node() or "unknown"


def _claim_owner_record() -> dict[str, Any]:
    return {
        "machine": _machine_name(),
        "pid": os.getpid(),
        "pythonExecutable": _identity(Path(sys.executable)),
        "lockProtocol": PRIMARY_CLAIM_LOCK_PROTOCOL,
    }


def _pid_exists(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return True
    if os.name == "nt":
        # os.kill(pid, 0) is not a portable existence probe on Windows: signals
        # other than CTRL_C/CTRL_BREAK are implemented with TerminateProcess on
        # some Python builds.  Query the process handle without mutating it.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        process_query_limited_information = 0x1000
        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error in {87, 1168}:  # invalid parameter / not found
                return False
            # Access denied and all unknown failures remain ambiguous.
            return True
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            close_handle(handle)
    try:
        os.kill(pid, 0)
    except (OverflowError, ValueError):
        # An unqueryable PID is ambiguous, never evidence of a dead owner.
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        if error.errno == errno.ESRCH or getattr(error, "winerror", None) in {
            87,
            1168,
        }:
            return False
        # Unknown process-query failures are ambiguous and therefore live.
        return True
    return True


def _load_json(path: Path, label: str) -> dict[str, Any]:
    resolved = _resolve(path)
    value = _strict_json_loads(
        resolved.read_text(encoding="utf-8"), location=str(resolved)
    )
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object")
    return value


def _strict_json_loads(text: str, *, location: str) -> Any:
    """Decode JSON while rejecting duplicate keys and non-finite constants."""

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
    except json.JSONDecodeError as error:
        raise ValueError(f"{location}: invalid JSON") from error


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} is not finite")
    return result


def _verify_identity(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    if set(("path", "bytes", "sha256")) - set(record):
        raise ValueError(f"{label} is not a complete identity")
    path = _resolve(Path(str(record["path"])))
    if _identity(path) != {
        "path": str(path),
        "bytes": int(record["bytes"]),
        "sha256": str(record["sha256"]),
    }:
        raise ValueError(f"{label} identity changed")
    return path


def _repo_profile_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is not a nonempty repo-relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\\" in value:
        raise ValueError(f"{label} is not a safe repo-relative POSIX path")
    return _resolve(REPO / Path(*pure.parts))


def _frozen_file_identity(
    profile: Mapping[str, Any], key: str, label: str
) -> dict[str, Any]:
    identities = _mapping(
        profile.get("finalFreezeIdentities"), "final freeze identities"
    )
    record = _mapping(identities.get(key), label)
    if set(record) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} has the wrong identity fields")
    path = _repo_profile_path(record.get("path"), f"{label} path")
    actual = _identity(path)
    if (
        int(record.get("bytes", -1)) != actual["bytes"]
        or str(record.get("sha256", "")) != actual["sha256"]
    ):
        raise ValueError(f"{label} differs from the final freeze")
    return actual


def _namespace_path(
    profile: Mapping[str, Any], key: str, label: str
) -> Path:
    namespaces = _mapping(profile.get("namespaces"), "profile namespaces")
    return _repo_profile_path(namespaces.get(key), label)


def _candidate_namespace(
    profile: Mapping[str, Any], candidate: str, *, robustness: bool = False
) -> Path:
    namespaces = _mapping(profile.get("namespaces"), "profile namespaces")
    if robustness:
        return _repo_profile_path(
            namespaces.get("robustnessBundle"), "robustness bundle namespace"
        )
    bundles = _mapping(namespaces.get("candidateBundles"), "candidate bundles")
    return _repo_profile_path(
        bundles.get(candidate), f"{candidate} bundle namespace"
    )


def _verify_final_freeze_binding(
    profile: Mapping[str, Any], profile_path: Path
) -> dict[str, Any]:
    seal_path = _namespace_path(
        profile, "finalFreezeSeal", "final-freeze seal namespace"
    )
    seal = _load_json(seal_path, "generation-5 final-freeze seal")
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
    if (
        set(seal) != expected_fields
        or seal.get("schemaVersion") != SCHEMA_VERSION
        or seal.get("kind") != FINAL_FREEZE_KIND
        or seal.get("profileId") != PROFILE_ID
        or seal.get("status") != PROFILE_STATUS
        or seal.get("finalStageSeal") is not True
        or seal.get("preregistration") != _identity(profile_path)
        or seal.get("validator")
        != _identity(Path(prereg_validator.__file__).resolve())
        or seal.get("finalFreezeIdentities")
        != profile.get("finalFreezeIdentities")
    ):
        raise ValueError("final-freeze seal does not bind this preregistration")
    digest = hashlib.sha256(
        json.dumps(
            profile.get("finalFreezeIdentities"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if seal.get("finalFreezeIdentitiesSha256") != digest:
        raise ValueError("final-freeze identity digest changed")
    if seal.get("declaration") != {
        "teacherSearchesPresentAtFreeze": False,
        "generation5TeacherTargetsDecoded": 0,
        "generation5ValidationTargetsDecoded": 0,
        "generation5HeldOutTargetsDecoded": 0,
    }:
        raise ValueError("final-freeze information boundary changed")
    return _identity(seal_path)


def _basis_path(candidate: str, output_dir: Path) -> Path:
    return _resolve(output_dir) / "factor-bases" / f"{candidate}.geometry.f32le"


def _basis_record(
    candidate: str,
    path: Path,
    payload: bytes,
    *,
    rank: int,
    seed: int,
) -> dict[str, Any]:
    return {
        **_payload_identity(path, payload),
        "candidateId": candidate,
        "dtype": "<f4",
        "shape": [KING_BUCKET_COUNT, rank],
        "rank": rank,
        "seed": seed,
        "targetFieldsDecoded": 0,
    }


def _load_factor_basis(
    value: Any,
    *,
    candidate: str,
    rank: int,
    seed: int,
) -> np.ndarray:
    record = _mapping(value, f"{candidate} factor basis")
    expected_fields = {
        "path",
        "bytes",
        "sha256",
        "candidateId",
        "dtype",
        "shape",
        "rank",
        "seed",
        "targetFieldsDecoded",
    }
    if set(record) != expected_fields:
        raise ValueError(f"{candidate} factor-basis fields changed")
    if (
        record.get("candidateId") != candidate
        or record.get("dtype") != "<f4"
        or record.get("shape") != [KING_BUCKET_COUNT, rank]
        or record.get("rank") != rank
        or record.get("seed") != seed
        or record.get("targetFieldsDecoded") != 0
    ):
        raise ValueError(f"{candidate} factor-basis metadata changed")
    path = _verify_identity(record, f"{candidate} factor-basis artifact")
    payload = path.read_bytes()
    expected_bytes = KING_BUCKET_COUNT * rank * np.dtype("<f4").itemsize
    if len(payload) != expected_bytes:
        raise ValueError(f"{candidate} factor-basis byte count changed")
    basis = np.frombuffer(payload, dtype=np.dtype("<f4")).reshape(
        KING_BUCKET_COUNT, rank
    )
    if not np.all(np.isfinite(basis)):
        raise ValueError(f"{candidate} factor basis is nonfinite")
    # Copy preserves the exact stored values without any QR/LAPACK call.
    return basis.astype(np.float32, copy=True)


def _walk_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _walk_mappings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _walk_mappings(item)


def _contains_identity(value: Any, wanted: Mapping[str, Any]) -> bool:
    return any(dict(item) == dict(wanted) for item in _walk_mappings(value))


def _contains_digest_identity(value: Any, wanted: Mapping[str, Any]) -> bool:
    for item in _walk_mappings(value):
        if (
            item.get("bytes") == wanted.get("bytes")
            and item.get("sha256") == wanted.get("sha256")
        ):
            return True
    return False


_JSON_NUMBER = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
)


class _OpaqueJsonRouter:
    """Validate one JSON value while decoding only requested routing strings.

    In particular, numeric/string teacher targets are skipped lexically.  The
    entire row and every object key are nevertheless checked before a selected
    row is handed to the ordinary decoder.  That makes a duplicate ``split``
    (or any other duplicate key) an opacity-preserving hard failure.
    """

    def __init__(
        self, text: str, wanted: Sequence[str], location: str
    ) -> None:
        self.text = text
        self.length = len(text)
        self.wanted = frozenset(wanted)
        self.location = location
        self.found: dict[str, str] = {}

    def error(self, message: str) -> ValueError:
        return ValueError(f"{self.location}: {message}")

    def whitespace(self, index: int) -> int:
        while index < self.length and self.text[index] in " \t\r\n":
            index += 1
        return index

    def string_end(self, index: int) -> int:
        if index >= self.length or self.text[index] != '"':
            raise self.error("expected JSON string")
        index += 1
        while index < self.length:
            character = self.text[index]
            if character == '"':
                return index + 1
            if ord(character) < 0x20:
                raise self.error("unescaped control character in JSON string")
            if character != "\\":
                index += 1
                continue
            index += 1
            if index >= self.length:
                raise self.error("truncated JSON escape")
            escape = self.text[index]
            if escape == "u":
                digits = self.text[index + 1 : index + 5]
                if len(digits) != 4 or any(
                    item not in "0123456789abcdefABCDEF" for item in digits
                ):
                    raise self.error("invalid JSON unicode escape")
                index += 5
            elif escape in '"\\/bfnrt':
                index += 1
            else:
                raise self.error("invalid JSON escape")
        raise self.error("unterminated JSON string")

    def decoded_string(self, index: int) -> tuple[str, int]:
        end = self.string_end(index)
        try:
            value = json.loads(self.text[index:end])
        except json.JSONDecodeError as error:
            raise self.error("invalid JSON string") from error
        if not isinstance(value, str):
            raise AssertionError("JSON string decoder returned a non-string")
        return value, end

    def value(self, index: int, *, top_level: bool = False) -> int:
        index = self.whitespace(index)
        if index >= self.length:
            raise self.error("missing JSON value")
        character = self.text[index]
        if character == "{":
            return self.object(index, top_level=top_level)
        if character == "[":
            return self.array(index)
        if character == '"':
            return self.string_end(index)
        for literal in ("true", "false", "null"):
            if self.text.startswith(literal, index):
                return index + len(literal)
        match = _JSON_NUMBER.match(self.text, index)
        if match is not None:
            return match.end()
        raise self.error("invalid or non-finite JSON value")

    def array(self, index: int) -> int:
        index = self.whitespace(index + 1)
        if index < self.length and self.text[index] == "]":
            return index + 1
        while True:
            index = self.whitespace(self.value(index))
            if index >= self.length:
                raise self.error("unterminated JSON array")
            if self.text[index] == "]":
                return index + 1
            if self.text[index] != ",":
                raise self.error("expected comma in JSON array")
            index = self.whitespace(index + 1)

    def object(self, index: int, *, top_level: bool) -> int:
        seen: set[str] = set()
        index = self.whitespace(index + 1)
        if index < self.length and self.text[index] == "}":
            return index + 1
        while True:
            key, index = self.decoded_string(index)
            if key in seen:
                raise self.error(f"duplicate JSON key {key!r}")
            seen.add(key)
            index = self.whitespace(index)
            if index >= self.length or self.text[index] != ":":
                raise self.error("expected colon after JSON key")
            index = self.whitespace(index + 1)
            if top_level and key in self.wanted:
                if index >= self.length or self.text[index] != '"':
                    raise self.error(f"routing field {key!r} is not a string")
                value, index = self.decoded_string(index)
                self.found[key] = value
            else:
                index = self.value(index)
            index = self.whitespace(index)
            if index >= self.length:
                raise self.error("unterminated JSON object")
            if self.text[index] == "}":
                return index + 1
            if self.text[index] != ",":
                raise self.error("expected comma in JSON object")
            index = self.whitespace(index + 1)

    def route(self) -> dict[str, str]:
        start = self.whitespace(0)
        if start >= self.length or self.text[start] != "{":
            raise self.error("row is not a JSON object")
        end = self.whitespace(self.object(start, top_level=True))
        if end != self.length:
            raise self.error("trailing data after JSON object")
        missing = self.wanted - self.found.keys()
        if missing:
            raise self.error(f"missing routing fields {sorted(missing)!r}")
        return self.found


def _routing_strings(
    line: str, fields: Sequence[str], location: str
) -> dict[str, str]:
    return _OpaqueJsonRouter(line, fields, location).route()


def _selective_string(line: str, field: str, location: str) -> str:
    return _routing_strings(line, (field,), location)[field]


def _feature_matrix(
    ofens: Sequence[str], architecture: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[str, ...]]:
    white_rows: list[tuple[int, ...]] = []
    black_rows: list[tuple[int, ...]] = []
    side_to_move: list[bool] = []
    signatures: list[str] = []
    feature_count = (
        OMEGA_INTERACTION_FEATURE_COUNT
        if architecture == ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
        else KING_STATE_FEATURE_COUNT
    )
    for ofen in ofens:
        white = active_features(ofen, 0, architecture)
        black = active_features(ofen, 1, architecture)
        fields = ofen.split()
        if len(fields) != 6 or fields[1] not in ("w", "b"):
            raise ValueError("decision corpus contains an invalid six-field OFEN")
        stm_white = fields[1] == "w"
        white_rows.append(white)
        black_rows.append(black)
        side_to_move.append(stm_white)
        signatures.append(
            nnue_input_signature(white, black, stm_white, architecture)
        )
    width = max(
        max((len(row) for row in white_rows), default=0),
        max((len(row) for row in black_rows), default=0),
    )
    if not ofens or width == 0:
        raise ValueError("decision corpus has no feature rows")
    white_matrix = np.full(
        (len(ofens), width), feature_count, dtype=np.uint16
    )
    black_matrix = np.full_like(white_matrix, feature_count)
    for index, (white, black) in enumerate(zip(white_rows, black_rows)):
        white_matrix[index, : len(white)] = white
        black_matrix[index, : len(black)] = black
    return (
        white_matrix,
        black_matrix,
        np.asarray(side_to_move, dtype=np.bool_),
        tuple(signatures),
    )


@dataclass(frozen=True)
class FeatureCorpus:
    ofens: tuple[str, ...]
    root_ids: tuple[str, ...]
    groups: tuple[str, ...]
    phases: tuple[str, ...]
    splits: np.ndarray
    white_features: np.ndarray
    black_features: np.ndarray
    side_to_move_white: np.ndarray
    input_signatures: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.ofens)

    @property
    def pad_feature(self) -> int:
        return OMEGA_INTERACTION_FEATURE_COUNT

    def indices(self, split: int) -> np.ndarray:
        return np.flatnonzero(self.splits == split)

    def perspective(
        self, indices: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        white = self.white_features[indices]
        black = self.black_features[indices]
        stm_white = self.side_to_move_white[indices, None]
        return (
            np.where(stm_white, white, black),
            np.where(stm_white, black, white),
        )


@dataclass(frozen=True)
class DecisionCorpus:
    features: FeatureCorpus
    target_cp: np.ndarray
    search_cp: np.ndarray
    handcrafted_cp: np.ndarray
    deep_rank: np.ndarray
    deep_gap_cp: np.ndarray
    ranking_eligible: np.ndarray
    source_rows: int
    heldout_rows: int
    target_rows_decoded: int
    target_fields_decoded: int
    handcrafted_scores_computed: int
    residual_targets_computed: int
    decoded_splits: tuple[str, ...]
    routed_rows: Mapping[str, int]
    target_fields_decoded_by_split: Mapping[str, int]

    def indices(self, split: int) -> np.ndarray:
        return self.features.indices(split)

    def perspective_features(
        self, indices: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        return self.features.perspective(indices)

    @property
    def pad_feature(self) -> int:
        return self.features.pad_feature


def _audit_four_siblings(features: FeatureCorpus) -> dict[str, Any]:
    by_root: dict[str, list[int]] = defaultdict(list)
    for index, root in enumerate(features.root_ids):
        by_root[root].append(index)
    for root, indices in by_root.items():
        if len(indices) != 4:
            raise ValueError(f"decision root {root!r} has {len(indices)}/4 siblings")
        if len({features.groups[index] for index in indices}) != 1:
            raise ValueError(f"decision root {root!r} crosses leakage components")
        if len({int(features.splits[index]) for index in indices}) != 1:
            raise ValueError(f"decision root {root!r} crosses splits")
        if len({features.phases[index] for index in indices}) != 1:
            raise ValueError(f"decision root {root!r} crosses phase labels")

    group_splits: dict[str, set[int]] = defaultdict(set)
    signature_splits: dict[str, set[int]] = defaultdict(set)
    for group, signature, split in zip(
        features.groups, features.input_signatures, features.splits
    ):
        group_splits[group].add(int(split))
        signature_splits[signature].add(int(split))
    leaking_groups = [key for key, values in group_splits.items() if len(values) > 1]
    leaking_inputs = [
        key for key, values in signature_splits.items() if len(values) > 1
    ]
    if leaking_groups:
        raise ValueError("leakage component occurs in multiple splits")
    if leaking_inputs:
        raise ValueError("exact NNUE input signature occurs in multiple splits")
    phase_roots: dict[str, dict[str, int]] = {}
    for name, split in SPLITS.items():
        phase_roots[name] = {
            phase: len(
                {
                    root
                    for root, row_phase, row_split in zip(
                        features.root_ids, features.phases, features.splits
                    )
                    if row_phase == phase and int(row_split) == split
                }
            )
            for phase in PHASES
        }
        if any(value == 0 for value in phase_roots[name].values()):
            raise ValueError(f"split {name} is missing a phase")
    return {
        "rows": features.count,
        "roots": len(by_root),
        "components": len(group_splits),
        "rowsPerRoot": 4,
        "componentSplitOverlaps": 0,
        "inputSignatureSplitOverlaps": 0,
        "phaseRoots": phase_roots,
        "targetFieldsDecoded": 0,
    }


def _split_index(name: str, *, allow_test_alias: bool) -> int:
    if name in SPLITS:
        return SPLITS[name]
    if allow_test_alias and name == "test":
        return SPLITS["heldOut"]
    raise ValueError(f"unknown split {name!r}")


def _load_feature_corpus(
    path: Path, *, allow_test_alias: bool = False
) -> FeatureCorpus:
    """Read only string-valued routing/feature fields from every row."""

    ofens: list[str] = []
    roots: list[str] = []
    groups: list[str] = []
    phases: list[str] = []
    splits: list[int] = []
    resolved = _resolve(path)
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            location = f"{resolved}:{line_number}"
            routing = _routing_strings(
                line,
                ("split", "phase", "childOfen", "rootId", "leakageComponentId"),
                location,
            )
            split_name = routing["split"]
            split = _split_index(
                split_name, allow_test_alias=allow_test_alias
            )
            phase = routing["phase"]
            if phase not in PHASES:
                raise ValueError(f"{location}: unknown phase {phase!r}")
            ofen = " ".join(routing["childOfen"].split())
            ofens.append(ofen)
            roots.append(routing["rootId"])
            groups.append(routing["leakageComponentId"])
            phases.append(phase)
            splits.append(split)
    white, black, stm, signatures = _feature_matrix(
        ofens, ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
    )
    result = FeatureCorpus(
        ofens=tuple(ofens),
        root_ids=tuple(roots),
        groups=tuple(groups),
        phases=tuple(phases),
        splits=np.asarray(splits, dtype=np.int8),
        white_features=white,
        black_features=black,
        side_to_move_white=stm,
        input_signatures=signatures,
    )
    _audit_four_siblings(result)
    return result


def _stream_integers(
    helper: Path,
    mode: str,
    ofens: Sequence[str],
    *,
    network: Path | None = None,
) -> np.ndarray:
    command = [str(_resolve(helper)), mode]
    if network is not None:
        command.append(str(_resolve(network)))
    completed = subprocess.run(
        command,
        input="".join(ofen + "\n" for ofen in ofens),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=max(600, len(ofens) // 8),
    )
    if completed.returncode != 0:
        raise ValueError(
            f"C++ evaluator {mode} failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    lines = completed.stdout.splitlines()
    if len(lines) != len(ofens):
        raise ValueError(
            f"C++ evaluator returned {len(lines)}/{len(ofens)} rows"
        )
    values: list[int] = []
    for ordinal, line in enumerate(lines, 1):
        text = line.strip()
        if not text or not text.lstrip("+-").isdigit():
            raise ValueError(f"C++ evaluator row {ordinal} is not an integer")
        values.append(int(text))
    return np.asarray(values, dtype=np.int32)


def _load_decision_splits(
    path: Path,
    *,
    hce_evaluator: Path | None,
    allow_embedded_hce: bool = False,
    allow_test_alias: bool = False,
    decode_splits: Sequence[str] = ("train", "validation"),
    heldout_access_claim: Mapping[str, Any] | None = None,
) -> DecisionCorpus:
    """Decode targets only for explicitly authorized, pre-routed splits.

    The production worker first calls this with only ``train``.  It does not
    call the validation-only loader until a QAT checkpoint has passed all
    target-opaque deployment-health gates.
    """

    wanted = tuple(dict.fromkeys(decode_splits))
    if not wanted or any(name not in SPLITS for name in wanted):
        raise ValueError("unknown target-decoding split")
    if "heldOut" in wanted:
        if wanted != ("heldOut",) or heldout_access_claim is None:
            raise ValueError(
                "held-out decoding requires its exclusive access-claim identity"
            )
        _verify_identity(heldout_access_claim, "held-out access claim")
    elif heldout_access_claim is not None:
        raise ValueError("held-out claim supplied to a non-held-out load")

    records: list[dict[str, Any]] = []
    source_rows = 0
    heldout_rows = 0
    routed_rows: Counter[str] = Counter()
    resolved = _resolve(path)
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            source_rows += 1
            location = f"{resolved}:{line_number}"
            # This complete, duplicate-aware structural pass occurs before any
            # target field is decoded, including for authorized splits.
            split_name = _routing_strings(line, ("split",), location)["split"]
            split = _split_index(
                split_name, allow_test_alias=allow_test_alias
            )
            canonical_split = next(
                name for name, value in SPLITS.items() if value == split
            )
            routed_rows[canonical_split] += 1
            if split == SPLITS["heldOut"]:
                heldout_rows += 1
                if "heldOut" not in wanted:
                    continue
            if canonical_split not in wanted:
                # In particular, a train-only load routes validation rows
                # without JSON decoding any deep score or derived target.
                continue
            record = _strict_json_loads(line, location=location)
            if not isinstance(record, dict):
                raise ValueError(f"{location}: row is not an object")
            record["_location"] = location
            records.append(record)
    if not records or heldout_rows == 0:
        raise ValueError("authorized split is empty or corpus lacks held-out rows")

    ofens: list[str] = []
    roots: list[str] = []
    groups: list[str] = []
    phases: list[str] = []
    splits: list[int] = []
    searches: list[float] = []
    ranks: list[int] = []
    gaps: list[float] = []
    eligible: list[bool] = []
    embedded_hce: list[float] = []
    for record in records:
        location = str(record["_location"])
        if record.get("kind") != "omega-decision-deep-label":
            raise ValueError(f"{location}: wrong decision-label kind")
        phase = record.get("phase")
        split_name = record.get("split")
        canonical_split = (
            "heldOut" if allow_test_alias and split_name == "test" else split_name
        )
        if phase not in PHASES or canonical_split not in wanted:
            raise ValueError(f"{location}: invalid phase or split")
        for field in ("rootId", "leakageComponentId", "childOfen"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise ValueError(f"{location}: missing {field}")
        child_score = _number(
            record.get("deepScoreCpChildStm"), f"{location} child score"
        )
        root_score = _number(
            record.get("deepScoreCpRoot"), f"{location} root score"
        )
        if root_score != -child_score:
            raise ValueError(f"{location}: child/root teacher scores disagree")
        rank = int(_number(record.get("deepRank"), f"{location} rank"))
        gap = _number(record.get("deepRegretCp"), f"{location} regret")
        if rank not in (1, 2, 3, 4) or gap < 0:
            raise ValueError(f"{location}: invalid deep rank/regret")
        expected_eligible = gap >= RANKING_IGNORE_BELOW_CP
        if record.get("rankingEligibleAgainstBest") is not expected_eligible:
            raise ValueError(f"{location}: ranking eligibility changed")
        if _number(
            record.get("rankingGapCpCapped"), f"{location} capped gap"
        ) != min(gap, RANKING_GAP_CAP_CP):
            raise ValueError(f"{location}: ranking cap changed")
        ofens.append(" ".join(str(record["childOfen"]).split()))
        roots.append(str(record["rootId"]))
        groups.append(str(record["leakageComponentId"]))
        phases.append(str(phase))
        splits.append(
            _split_index(str(split_name), allow_test_alias=allow_test_alias)
        )
        searches.append(child_score)
        ranks.append(rank)
        gaps.append(gap)
        eligible.append(expected_eligible)
        if allow_embedded_hce:
            embedded_hce.append(
                _number(
                    record.get("handcraftedCpChildStm"),
                    f"{location} embedded handcrafted score",
                )
            )

    white, black, stm, signatures = _feature_matrix(
        ofens, ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
    )
    features = FeatureCorpus(
        ofens=tuple(ofens),
        root_ids=tuple(roots),
        groups=tuple(groups),
        phases=tuple(phases),
        splits=np.asarray(splits, dtype=np.int8),
        white_features=white,
        black_features=black,
        side_to_move_white=stm,
        input_signatures=signatures,
    )
    # Every surviving split still contains complete sibling groups.
    _audit_four_siblings_subset(features)
    for name in wanted:
        if features.indices(SPLITS[name]).size == 0:
            raise ValueError(f"authorized split {name} is empty")

    if allow_embedded_hce:
        handcrafted = np.asarray(embedded_hce, dtype=np.float32)
    else:
        if hce_evaluator is None:
            raise ValueError("a pinned static-HCE evaluator is required")
        handcrafted = _stream_integers(
            hce_evaluator, CPP_HCE_MODE, features.ofens
        ).astype(np.float32)
    search_array = np.asarray(searches, dtype=np.float32)
    targets = np.clip(search_array - handcrafted, -CP_CLIP, CP_CLIP)
    dataset = DecisionCorpus(
        features=features,
        target_cp=targets.astype(np.float32),
        search_cp=search_array,
        handcrafted_cp=handcrafted,
        deep_rank=np.asarray(ranks, dtype=np.int8),
        deep_gap_cp=np.asarray(gaps, dtype=np.float32),
        ranking_eligible=np.asarray(eligible, dtype=np.bool_),
        source_rows=source_rows,
        heldout_rows=heldout_rows,
        target_rows_decoded=len(records),
        target_fields_decoded=(
            len(records) * len(TARGET_FIELDS_DECODED_PER_ROW)
        ),
        handcrafted_scores_computed=len(records),
        residual_targets_computed=len(records),
        decoded_splits=wanted,
        routed_rows={name: int(routed_rows[name]) for name in SPLITS},
        target_fields_decoded_by_split={
            name: (
                int(routed_rows[name]) * len(TARGET_FIELDS_DECODED_PER_ROW)
                if name in wanted
                else 0
            )
            for name in SPLITS
        },
    )
    _audit_ranked_roots(dataset)
    return dataset


def _audit_four_siblings_subset(features: FeatureCorpus) -> None:
    by_root: dict[str, list[int]] = defaultdict(list)
    for index, root in enumerate(features.root_ids):
        by_root[root].append(index)
    for root, indices in by_root.items():
        if len(indices) != 4:
            raise ValueError(f"selected root {root!r} has {len(indices)}/4 rows")
        if len({features.groups[index] for index in indices}) != 1:
            raise ValueError(f"selected root {root!r} crosses components")
        if len({int(features.splits[index]) for index in indices}) != 1:
            raise ValueError(f"selected root {root!r} crosses splits")
    by_group: dict[str, set[int]] = defaultdict(set)
    by_signature: dict[str, set[int]] = defaultdict(set)
    for group, signature, split in zip(
        features.groups, features.input_signatures, features.splits
    ):
        by_group[group].add(int(split))
        by_signature[signature].add(int(split))
    if any(len(values) > 1 for values in by_group.values()):
        raise ValueError("selected leakage component crosses splits")
    if any(len(values) > 1 for values in by_signature.values()):
        raise ValueError("selected exact input crosses splits")


def _audit_ranked_roots(dataset: DecisionCorpus) -> None:
    by_root: dict[str, list[int]] = defaultdict(list)
    for index, root in enumerate(dataset.features.root_ids):
        by_root[root].append(index)
    for root, indices in by_root.items():
        if sorted(int(dataset.deep_rank[index]) for index in indices) != [1, 2, 3, 4]:
            raise ValueError(f"root {root!r} does not contain ranks 1..4")
        best = next(index for index in indices if dataset.deep_rank[index] == 1)
        if dataset.deep_gap_cp[best] != 0 or dataset.ranking_eligible[best]:
            raise ValueError(f"root {root!r} has an invalid best-child label")
        root_scores = [-float(dataset.search_cp[index]) for index in indices]
        best_score = root_scores[indices.index(best)]
        for index, root_score in zip(indices, root_scores):
            expected = best_score - root_score
            if not math.isclose(
                float(dataset.deep_gap_cp[index]), expected, abs_tol=1e-6
            ):
                raise ValueError(f"root {root!r} has inconsistent regret")


def _domain_seed(base_seed: int, purpose: str, candidate: str, rank: int) -> int:
    digest = hashlib.sha256(
        f"{base_seed}|{purpose}|{candidate}|{rank}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "little", signed=False)


def _bucket_geometry() -> np.ndarray:
    rows: list[list[float]] = []
    for bucket in range(KING_BUCKET_COUNT):
        if bucket < 25:
            file_band, rank_band = divmod(bucket, 5)
            x = (file_band - 2.0) / 2.0
            y = (rank_band - 2.0) / 2.0
            corner = [0.0, 0.0, 0.0, 0.0]
        else:
            coordinates = ((-1.2, -1.2), (1.2, -1.2), (1.2, 1.2), (-1.2, 1.2))
            x, y = coordinates[bucket - 25]
            corner = [float(index == bucket - 25) for index in range(4)]
        rows.append(
            [
                1.0,
                x,
                y,
                x * y,
                x * x,
                y * y,
                x * (x * x + y * y),
                y * (x * x + y * y),
                *corner,
            ]
        )
    return np.asarray(rows, dtype=np.float64)


def _factor_basis(seed: int, rank: int) -> np.ndarray:
    if rank not in (4, 8):
        raise ValueError("generation-5 rank must be 4 or 8")
    geometry = _bucket_geometry()
    rng = np.random.default_rng(seed)
    projection = rng.normal(0.0, 1.0, size=(geometry.shape[1], rank))
    projected = geometry @ projection
    # A QR basis fixes scale and makes each D learning rate comparable.  Make
    # the otherwise arbitrary QR sign deterministic.
    basis, _ = np.linalg.qr(projected, mode="reduced")
    for column in range(rank):
        pivot = int(np.argmax(np.abs(basis[:, column])))
        if basis[pivot, column] < 0:
            basis[:, column] *= -1.0
    result = basis.astype(np.float32)
    if result.shape != (KING_BUCKET_COUNT, rank):
        raise AssertionError("factor basis shape changed")
    return result


def _migrate_initializer(initializer: QuantizedNetwork) -> QuantizedNetwork:
    if initializer.architecture != ARCHITECTURE_KING_STATE_RESIDUAL:
        raise ValueError("K2 initializer is not architecture 3")
    if initializer.ft_weights.shape[0] != KING_STATE_FEATURE_COUNT:
        raise ValueError("K2 initializer feature inventory changed")
    extra = np.zeros(
        (OMEGA_INTERACTION_FEATURES, ACCUMULATOR_SIZE), dtype=np.int16
    )
    result = QuantizedNetwork(
        ft_bias=initializer.ft_bias.copy(),
        ft_weights=np.concatenate((initializer.ft_weights, extra), axis=0),
        dense_bias=initializer.dense_bias.copy(),
        dense_weights=initializer.dense_weights.copy(),
        output_bias=initializer.output_bias,
        output_weights=initializer.output_weights.copy(),
        architecture=ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
    )
    result.validate()
    return result


def _predict_network(
    network: QuantizedNetwork,
    features: FeatureCorpus,
    indices: np.ndarray | None = None,
) -> np.ndarray:
    wanted = (
        np.arange(features.count, dtype=np.int64)
        if indices is None
        else np.asarray(indices, dtype=np.int64)
    )
    result = np.empty(wanted.size, dtype=np.int32)
    for start in range(0, wanted.size, BATCH_SIZE):
        selected = wanted[start : start + BATCH_SIZE]
        stm, opponent = features.perspective(selected)
        result[start : start + selected.size] = network.predict_features(
            stm, opponent
        )
    return result


def _migration_parity(
    initializer: QuantizedNetwork,
    migrated: QuantizedNetwork,
    features: FeatureCorpus,
) -> dict[str, Any]:
    white, black, stm, _ = _feature_matrix(
        features.ofens, ARCHITECTURE_KING_STATE_RESIDUAL
    )
    legacy_predictions = np.empty(features.count, dtype=np.int32)
    for start in range(0, features.count, BATCH_SIZE):
        stop = min(start + BATCH_SIZE, features.count)
        use_white = stm[start:stop, None]
        first = np.where(use_white, white[start:stop], black[start:stop])
        second = np.where(use_white, black[start:stop], white[start:stop])
        legacy_predictions[start:stop] = initializer.predict_features(first, second)
    migrated_predictions = _predict_network(migrated, features)
    mismatches = int(np.count_nonzero(legacy_predictions != migrated_predictions))
    return {
        "positions": features.count,
        "predictionMismatches": mismatches,
        "newRowsAllZero": bool(
            np.all(migrated.ft_weights[KING_STATE_FEATURE_COUNT:] == 0)
        ),
        "architecture3RowsByteIdentical": bool(
            np.array_equal(
                initializer.ft_weights,
                migrated.ft_weights[:KING_STATE_FEATURE_COUNT],
            )
        ),
        "passed": mismatches == 0,
        "targetFieldsDecoded": 0,
    }


def _quantized_effective(
    value: np.ndarray, low: int, high: int, divisor: float = 1.0
) -> np.ndarray:
    return (
        np.clip(np.rint(value * divisor), low, high).astype(np.float32)
        / divisor
    )


def _round_engine(value: np.ndarray) -> np.ndarray:
    return np.where(
        value >= 0.0,
        np.floor(value + 0.5),
        -np.floor(-value + 0.5),
    )


@dataclass
class FactorizedNetwork:
    base_conditioned: np.ndarray
    basis: np.ndarray
    delta: np.ndarray
    ft_bias: np.ndarray
    state_weights: np.ndarray
    dense_bias: np.ndarray
    dense_weights: np.ndarray
    output_bias: np.ndarray
    output_weights: np.ndarray

    @classmethod
    def from_initializer(
        cls, initializer: QuantizedNetwork, basis: np.ndarray
    ) -> "FactorizedNetwork":
        if initializer.architecture != ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL:
            raise ValueError("factor model requires mapped architecture-4 I0")
        rank = int(basis.shape[1])
        conditioned = initializer.ft_weights[:KING_STATE_OCCUPANCY_FEATURES]
        result = cls(
            base_conditioned=conditioned.astype(np.float32).reshape(
                KING_BUCKET_COUNT, BASE_PIECE_FEATURES, ACCUMULATOR_SIZE
            ),
            basis=np.asarray(basis, dtype=np.float32).copy(),
            delta=np.zeros(
                (rank, BASE_PIECE_FEATURES, ACCUMULATOR_SIZE),
                dtype=np.float32,
            ),
            ft_bias=initializer.ft_bias.astype(np.float32),
            state_weights=initializer.ft_weights[
                KING_STATE_OCCUPANCY_FEATURES:
            ].astype(np.float32),
            dense_bias=(
                initializer.dense_bias.astype(np.float32) / HIDDEN_DIVISOR
            ),
            dense_weights=(
                initializer.dense_weights.astype(np.float32) / HIDDEN_DIVISOR
            ),
            output_bias=np.asarray(
                [initializer.output_bias / OUTPUT_DIVISOR], dtype=np.float32
            ),
            output_weights=(
                initializer.output_weights.astype(np.float32) / OUTPUT_DIVISOR
            ),
        )
        if result.quantize().to_bytes() != initializer.to_bytes():
            raise AssertionError("rank-factor epoch-zero materialization changed K2")
        return result

    def clone(self) -> "FactorizedNetwork":
        return FactorizedNetwork(
            base_conditioned=self.base_conditioned.copy(),
            basis=self.basis.copy(),
            delta=self.delta.copy(),
            ft_bias=self.ft_bias.copy(),
            state_weights=self.state_weights.copy(),
            dense_bias=self.dense_bias.copy(),
            dense_weights=self.dense_weights.copy(),
            output_bias=self.output_bias.copy(),
            output_weights=self.output_weights.copy(),
        )

    def materialize(self) -> base.FloatNetwork:
        update = np.einsum(
            "kr,rfa->kfa", self.basis, self.delta, optimize=True
        )
        conditioned = (self.base_conditioned + update).reshape(
            KING_STATE_OCCUPANCY_FEATURES, ACCUMULATOR_SIZE
        )
        return base.FloatNetwork(
            ft_bias=self.ft_bias.copy(),
            ft_weights=np.concatenate(
                (conditioned, self.state_weights), axis=0
            ).astype(np.float32, copy=False),
            dense_bias=self.dense_bias.copy(),
            dense_weights=self.dense_weights.copy(),
            output_bias=self.output_bias.copy(),
            output_weights=self.output_weights.copy(),
        )

    def quantize(self) -> QuantizedNetwork:
        return self.materialize().quantize(
            ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
        )

    def parameters(self) -> dict[str, np.ndarray]:
        return {
            "delta": self.delta,
            "ft_bias": self.ft_bias,
            "state_weights": self.state_weights,
            "dense_bias": self.dense_bias,
            "dense_weights": self.dense_weights,
            "output_bias": self.output_bias,
            "output_weights": self.output_weights,
        }

    def _accumulator(
        self, encoded_rows: np.ndarray, *, quantization_aware: bool
    ) -> np.ndarray:
        bias = (
            _quantized_effective(self.ft_bias, -(1 << 15), (1 << 15) - 1)
            if quantization_aware
            else self.ft_bias
        )
        result = np.repeat(bias[None, :], encoded_rows.shape[0], axis=0)
        for row, encoded in enumerate(encoded_rows):
            real = encoded[encoded != OMEGA_INTERACTION_FEATURE_COUNT].astype(
                np.int64
            )
            conditioned = real[real < KING_STATE_OCCUPANCY_FEATURES]
            state = real[real >= KING_STATE_OCCUPANCY_FEATURES]
            if conditioned.size:
                buckets = conditioned // BASE_PIECE_FEATURES
                pieces = conditioned % BASE_PIECE_FEATURES
                update = np.einsum(
                    "nr,rna->na",
                    self.basis[buckets],
                    self.delta[:, pieces, :],
                    optimize=True,
                )
                values = self.base_conditioned[buckets, pieces] + update
                if quantization_aware:
                    values = _quantized_effective(
                        values, -(1 << 15), (1 << 15) - 1
                    )
                result[row] += values.sum(axis=0)
            if state.size:
                values = self.state_weights[
                    state - KING_STATE_OCCUPANCY_FEATURES
                ]
                if quantization_aware:
                    values = _quantized_effective(
                        values, -(1 << 15), (1 << 15) - 1
                    )
                result[row] += values.sum(axis=0)
        return result

    def forward(
        self,
        stm_features: np.ndarray,
        opponent_features: np.ndarray,
        *,
        quantization_aware: bool,
        need_cache: bool,
    ) -> tuple[np.ndarray, dict[str, np.ndarray] | None]:
        stm_z = self._accumulator(
            stm_features, quantization_aware=quantization_aware
        )
        opponent_z = self._accumulator(
            opponent_features, quantization_aware=quantization_aware
        )
        stm_a = np.clip(stm_z, 0.0, ACTIVATION_MAX)
        opponent_a = np.clip(opponent_z, 0.0, ACTIVATION_MAX)
        joined = np.concatenate((stm_a, opponent_a), axis=1)
        if quantization_aware:
            dense_bias = _quantized_effective(
                self.dense_bias, -(1 << 31), (1 << 31) - 1, HIDDEN_DIVISOR
            )
            dense_weights = _quantized_effective(
                self.dense_weights, -128, 127, HIDDEN_DIVISOR
            )
            output_bias = float(
                _quantized_effective(
                    self.output_bias,
                    -(1 << 31),
                    (1 << 31) - 1,
                    OUTPUT_DIVISOR,
                )[0]
            )
            output_weights = _quantized_effective(
                self.output_weights, -128, 127, OUTPUT_DIVISOR
            )
        else:
            dense_bias = self.dense_bias
            dense_weights = self.dense_weights
            output_bias = float(self.output_bias[0])
            output_weights = self.output_weights
        dense_z = dense_bias + joined @ dense_weights.T
        if quantization_aware:
            dense_z = _round_engine(dense_z)
        dense_a = np.clip(dense_z, 0.0, ACTIVATION_MAX)
        raw_prediction = output_bias + dense_a @ output_weights
        if quantization_aware:
            raw_prediction = _round_engine(raw_prediction)
        prediction = np.clip(
            raw_prediction,
            -OMEGA_INTERACTION_RESIDUAL_LIMIT_CP,
            OMEGA_INTERACTION_RESIDUAL_LIMIT_CP,
        ).astype(np.float32)
        if not need_cache:
            return prediction, None
        return prediction, {
            "stm_z": stm_z,
            "opponent_z": opponent_z,
            "joined": joined,
            "dense_z": dense_z,
            "dense_a": dense_a,
            "dense_weights": dense_weights,
            "output_weights": output_weights,
            "raw_prediction": np.asarray(raw_prediction, dtype=np.float32),
        }


class Adam:
    def __init__(self, parameters: Mapping[str, np.ndarray]) -> None:
        self.parameters = dict(parameters)
        self.first = {
            key: np.zeros_like(value, dtype=np.float32)
            for key, value in self.parameters.items()
        }
        self.second = {
            key: np.zeros_like(value, dtype=np.float32)
            for key, value in self.parameters.items()
        }
        self.scales = {
            "delta": 1.0,
            "ft_bias": 1.0,
            "state_weights": 1.0,
            "dense_bias": 0.1,
            "dense_weights": 0.01,
            "output_bias": 0.25,
            "output_weights": 0.25,
        }
        if set(self.parameters) != set(self.scales):
            raise AssertionError("optimizer parameter inventory changed")
        self.steps = 0

    def step(self, gradients: Mapping[str, np.ndarray], scale: float) -> None:
        if set(gradients) != set(self.parameters):
            raise ValueError("gradient inventory differs from parameters")
        self.steps += 1
        beta1, beta2, epsilon = 0.9, 0.999, 1e-8
        correction1 = 1.0 - beta1**self.steps
        correction2 = 1.0 - beta2**self.steps
        for name, parameter in self.parameters.items():
            gradient = np.asarray(gradients[name], dtype=np.float32)
            if not np.all(np.isfinite(gradient)):
                raise FloatingPointError(f"nonfinite gradient in {name}")
            first = self.first[name]
            second = self.second[name]
            first *= beta1
            first += (1.0 - beta1) * gradient
            second *= beta2
            second += (1.0 - beta2) * gradient * gradient
            parameter -= (
                LEARNING_RATE
                * scale
                * self.scales[name]
                * (first / correction1)
                / (np.sqrt(second / correction2) + epsilon)
            )
        np.clip(
            self.parameters["state_weights"],
            -(1 << 15),
            (1 << 15) - 1,
            out=self.parameters["state_weights"],
        )
        np.clip(
            self.parameters["ft_bias"],
            -(1 << 15),
            (1 << 15) - 1,
            out=self.parameters["ft_bias"],
        )
        np.clip(
            self.parameters["dense_weights"],
            -128.0 / HIDDEN_DIVISOR,
            127.0 / HIDDEN_DIVISOR,
            out=self.parameters["dense_weights"],
        )
        np.clip(
            self.parameters["output_weights"],
            -128.0 / OUTPUT_DIVISOR,
            127.0 / OUTPUT_DIVISOR,
            out=self.parameters["output_weights"],
        )


def _huber_loss_and_derivative(error_cp: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    normalized = error_cp.astype(np.float64) / CP_NORMALIZER
    absolute = np.abs(normalized)
    quadratic = absolute <= HUBER_DELTA
    loss = np.where(
        quadratic,
        0.5 * normalized * normalized,
        HUBER_DELTA * (absolute - 0.5 * HUBER_DELTA),
    )
    derivative = np.where(
        quadratic, normalized, HUBER_DELTA * np.sign(normalized)
    ) / CP_NORMALIZER
    return loss, derivative


def _phase_group_weights(dataset: DecisionCorpus) -> np.ndarray:
    train = dataset.indices(0)
    cells: Counter[tuple[str, str]] = Counter()
    groups_by_phase: dict[str, set[str]] = {phase: set() for phase in PHASES}
    for index in train:
        row = int(index)
        key = (dataset.features.phases[row], dataset.features.groups[row])
        cells[key] += 1
        groups_by_phase[key[0]].add(key[1])
    if any(not values for values in groups_by_phase.values()):
        raise ValueError("training split is missing a phase")
    weights = np.ones(dataset.features.count, dtype=np.float32)
    for index in train:
        row = int(index)
        phase = dataset.features.phases[row]
        group = dataset.features.groups[row]
        weights[row] = len(train) / (
            len(PHASES) * len(groups_by_phase[phase]) * cells[(phase, group)]
        )
    if not math.isclose(float(weights[train].sum()), float(len(train)), abs_tol=1e-3):
        raise AssertionError("training weights no longer sum to N")
    return weights


def _objective(
    dataset: DecisionCorpus,
    batch: np.ndarray,
    prediction: np.ndarray,
    row_weights: np.ndarray,
    ranking_weight: float,
) -> tuple[float, np.ndarray, dict[str, Any]]:
    weights = row_weights[batch].astype(np.float64)
    weights /= float(weights.sum())
    point_loss, point_derivative = _huber_loss_and_derivative(
        prediction.astype(np.float64) - dataset.target_cp[batch]
    )
    gradient = weights * point_derivative
    total = float(np.sum(weights * point_loss))

    local_by_root: dict[str, list[int]] = defaultdict(list)
    for local, corpus_index in enumerate(batch):
        local_by_root[dataset.features.root_ids[int(corpus_index)]].append(local)
    pairs_by_root: list[list[tuple[int, int, float]]] = []
    for root, locals_ in local_by_root.items():
        if len(locals_) != 4:
            raise AssertionError(f"batch split sibling root {root!r}")
        best = next(
            local
            for local in locals_
            if int(dataset.deep_rank[int(batch[local])]) == 1
        )
        root_pairs: list[tuple[int, int, float]] = []
        for other in locals_:
            row = int(batch[other])
            if dataset.ranking_eligible[row]:
                root_pairs.append(
                    (
                        best,
                        other,
                        min(float(dataset.deep_gap_cp[row]), RANKING_GAP_CAP_CP),
                    )
                )
        if root_pairs:
            pairs_by_root.append(root_pairs)
    rank_loss = 0.0
    if pairs_by_root:
        # Normalize in two explicit stages: eligible pairs are averaged
        # within their root, then roots having at least one eligible pair are
        # averaged within this complete-root batch.  Thus a tactically noisy
        # root with three eligible siblings cannot receive triple the weight
        # of a root with one.  The candidate's preregistered .25/.50 weight is
        # applied only after both averages.
        root_scale = ranking_weight / len(pairs_by_root)
        for root_pairs in pairs_by_root:
            pair_scale = root_scale / len(root_pairs)
            for best, other, teacher_gap in root_pairs:
                # Child scores are from child STM, the opposite of the root
                # STM. Therefore root(best)-root(other) equals
                # child(other)-child(best).
                predicted_gap = (
                    float(dataset.handcrafted_cp[int(batch[other])])
                    + float(prediction[other])
                    - float(dataset.handcrafted_cp[int(batch[best])])
                    - float(prediction[best])
                )
                losses, derivatives = _huber_loss_and_derivative(
                    np.asarray(
                        [predicted_gap - teacher_gap], dtype=np.float64
                    )
                )
                rank_loss += pair_scale * float(losses[0])
                value = pair_scale * float(derivatives[0])
                gradient[other] += value
                gradient[best] -= value
        total += rank_loss
    if not math.isfinite(total) or not np.all(np.isfinite(gradient)):
        raise FloatingPointError("decision objective became nonfinite")
    return total, gradient.astype(np.float32), {
        "pointwiseHuber": float(np.sum(weights * point_loss)),
        "rankingHuber": rank_loss,
        "rankingPairs": sum(map(len, pairs_by_root)),
        "rankingRoots": len(pairs_by_root),
        "rankingNormalization": (
            "mean eligible best-vs-child pair per root; mean eligible root "
            "per complete-root batch; multiply candidate weight"
        ),
        "rankingIgnoreBelowCp": RANKING_IGNORE_BELOW_CP,
        "rankingGapCapCp": RANKING_GAP_CAP_CP,
    }


def _dense_backprop(
    cache: Mapping[str, np.ndarray], output_gradient: np.ndarray
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, float]:
    output_weights = cache["output_weights"]
    dense_weights = cache["dense_weights"]
    dense_a = cache["dense_a"]
    joined = cache["joined"]
    dense_z = cache["dense_z"]
    raw = cache["raw_prediction"]
    # The deployed architecture clamps corrections.  The straight-through
    # gradient is active only while the unclamped output remains in range.
    unclamped = (raw >= -OMEGA_INTERACTION_RESIDUAL_LIMIT_CP) & (
        raw <= OMEGA_INTERACTION_RESIDUAL_LIMIT_CP
    )
    output_gradient = output_gradient * unclamped
    output_bias_gradient = np.asarray([output_gradient.sum()], dtype=np.float32)
    output_weights_gradient = dense_a.T @ output_gradient
    dense_activation_gradient = output_gradient[:, None] * output_weights[None, :]

    scaled = np.clip(
        dense_z.astype(np.float64) / ACTIVATION_TEMPERATURE, -40.0, 40.0
    )
    soft = 1.0 / (1.0 + np.exp(-scaled))
    rates = soft.mean(axis=0)
    excess = np.maximum(rates - ACTIVATION_TARGET, 0.0)
    regularizer_loss = float(
        ACTIVATION_PENALTY * np.mean(excess * excess)
    )
    regularizer_gradient = (
        (2.0 * ACTIVATION_PENALTY / HIDDEN_SIZE)
        * excess[None, :]
        * soft
        * (1.0 - soft)
        / (ACTIVATION_TEMPERATURE * dense_z.shape[0])
    )
    dense_activation_gradient += regularizer_gradient.astype(np.float32)

    dense_mask = (dense_z > 0.0) & (dense_z < ACTIVATION_MAX)
    dense_z_gradient = dense_activation_gradient * dense_mask
    dense_bias_gradient = dense_z_gradient.sum(axis=0)
    dense_weights_gradient = dense_z_gradient.T @ joined
    joined_gradient = dense_z_gradient @ dense_weights
    stm_gradient = joined_gradient[:, :ACCUMULATOR_SIZE]
    opponent_gradient = joined_gradient[:, ACCUMULATOR_SIZE:]
    stm_gradient *= (cache["stm_z"] > 0.0) & (
        cache["stm_z"] < ACTIVATION_MAX
    )
    opponent_gradient *= (cache["opponent_z"] > 0.0) & (
        cache["opponent_z"] < ACTIVATION_MAX
    )
    return {
        "dense_bias": dense_bias_gradient.astype(np.float32),
        "dense_weights": dense_weights_gradient.astype(np.float32),
        "output_bias": output_bias_gradient,
        "output_weights": output_weights_gradient.astype(np.float32),
    }, stm_gradient, opponent_gradient, regularizer_loss


def _scatter_factor_gradients(
    model: FactorizedNetwork,
    encoded_rows: np.ndarray,
    activation_gradient: np.ndarray,
    delta_gradient: np.ndarray,
    state_gradient: np.ndarray,
) -> None:
    for encoded, gradient in zip(encoded_rows, activation_gradient):
        real = encoded[encoded != OMEGA_INTERACTION_FEATURE_COUNT].astype(np.int64)
        conditioned = real[real < KING_STATE_OCCUPANCY_FEATURES]
        state = real[real >= KING_STATE_OCCUPANCY_FEATURES]
        if conditioned.size:
            buckets = conditioned // BASE_PIECE_FEATURES
            pieces = conditioned % BASE_PIECE_FEATURES
            for bucket, piece in zip(buckets, pieces):
                delta_gradient[:, piece, :] += (
                    model.basis[bucket, :, None] * gradient[None, :]
                )
        if state.size:
            np.add.at(
                state_gradient,
                state - KING_STATE_OCCUPANCY_FEATURES,
                gradient,
            )


def _gradients(
    dataset: DecisionCorpus,
    batch: np.ndarray,
    model: FactorizedNetwork,
    *,
    row_weights: np.ndarray,
    ranking_weight: float,
    quantization_aware: bool,
) -> tuple[float, dict[str, np.ndarray], dict[str, Any]]:
    stm, opponent = dataset.perspective_features(batch)
    prediction, cache = model.forward(
        stm,
        opponent,
        quantization_aware=quantization_aware,
        need_cache=True,
    )
    assert cache is not None
    objective, output_gradient, audit = _objective(
        dataset, batch, prediction, row_weights, ranking_weight
    )
    dense, stm_gradient, opponent_gradient, activation_loss = _dense_backprop(
        cache, output_gradient
    )
    delta = np.zeros_like(model.delta, dtype=np.float32)
    state = np.zeros_like(model.state_weights, dtype=np.float32)
    _scatter_factor_gradients(model, stm, stm_gradient, delta, state)
    _scatter_factor_gradients(model, opponent, opponent_gradient, delta, state)
    audit["activationPenalty"] = activation_loss
    audit["totalLoss"] = objective + activation_loss
    return objective + activation_loss, {
        "delta": delta,
        "ft_bias": (stm_gradient + opponent_gradient).sum(axis=0).astype(np.float32),
        "state_weights": state,
        **dense,
    }, audit


def _root_batches(
    dataset: DecisionCorpus, rng: np.random.Generator
) -> list[np.ndarray]:
    by_phase: dict[str, list[str]] = {phase: [] for phase in PHASES}
    rows: dict[str, list[int]] = defaultdict(list)
    for index in dataset.indices(0):
        row = int(index)
        root = dataset.features.root_ids[row]
        rows[root].append(row)
    for root, indices in rows.items():
        if len(indices) != 4:
            raise ValueError(f"training root {root!r} is incomplete")
        by_phase[dataset.features.phases[indices[0]]].append(root)
    for phase in PHASES:
        if not by_phase[phase]:
            raise ValueError(f"training roots omit {phase}")
        rng.shuffle(by_phase[phase])
    order: list[str] = []
    while any(by_phase.values()):
        for phase in PHASES:
            if by_phase[phase]:
                order.append(by_phase[phase].pop())
    batches: list[np.ndarray] = []
    for start in range(0, len(order), ROOTS_PER_BATCH):
        roots = order[start : start + ROOTS_PER_BATCH]
        batch = np.asarray(
            [row for root in roots for row in sorted(rows[root])], dtype=np.int64
        )
        if batch.size % 4:
            raise AssertionError("root batch split siblings")
        batches.append(batch)
    return batches


def _huber(error_cp: np.ndarray) -> np.ndarray:
    return _huber_loss_and_derivative(error_cp)[0]


def _common_metrics(
    dataset: DecisionCorpus, split: int, predictions: np.ndarray
) -> dict[str, Any]:
    indices = dataset.indices(split)
    if predictions.shape == (dataset.features.count,):
        predictions = predictions[indices]
    if predictions.shape != (indices.size,):
        raise ValueError("metric prediction shape changed")
    error = predictions.astype(np.float64) - dataset.target_cp[indices]
    losses = _huber(error)
    absolute = np.abs(error)
    phase_metrics: dict[str, Any] = {}
    for phase in PHASES:
        cells_loss: dict[str, list[float]] = defaultdict(list)
        cells_mae: dict[str, list[float]] = defaultdict(list)
        for local, corpus_index in enumerate(indices):
            row = int(corpus_index)
            if dataset.features.phases[row] != phase:
                continue
            group = dataset.features.groups[row]
            cells_loss[group].append(float(losses[local]))
            cells_mae[group].append(float(absolute[local]))
        if not cells_loss:
            raise ValueError(f"metric split {split} lacks {phase}")
        group_loss = [float(np.mean(value)) for value in cells_loss.values()]
        group_mae = [float(np.mean(value)) for value in cells_mae.values()]
        phase_metrics[phase] = {
            "rows": sum(map(len, cells_loss.values())),
            "groups": len(cells_loss),
            "huberLoss": float(np.mean(group_loss)),
            "cpMae": float(np.mean(group_mae)),
        }
    return {
        "huberLoss": float(
            np.mean([phase_metrics[phase]["huberLoss"] for phase in PHASES])
        ),
        "cpMae": float(
            np.mean([phase_metrics[phase]["cpMae"] for phase in PHASES])
        ),
        "phase": phase_metrics,
    }


def _forward_materialized(
    model: base.FloatNetwork,
    stm: np.ndarray,
    opponent: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    extended = np.vstack(
        (model.ft_weights, np.zeros((1, ACCUMULATOR_SIZE), dtype=np.float32))
    )
    stm_z = model.ft_bias + extended[stm].sum(axis=1)
    opponent_z = model.ft_bias + extended[opponent].sum(axis=1)
    joined = np.concatenate(
        (
            np.clip(stm_z, 0.0, ACTIVATION_MAX),
            np.clip(opponent_z, 0.0, ACTIVATION_MAX),
        ),
        axis=1,
    )
    dense_z = model.dense_bias + joined @ model.dense_weights.T
    dense_a = np.clip(dense_z, 0.0, ACTIVATION_MAX)
    raw = float(model.output_bias[0]) + dense_a @ model.output_weights
    return (
        np.clip(
            raw,
            -OMEGA_INTERACTION_RESIDUAL_LIMIT_CP,
            OMEGA_INTERACTION_RESIDUAL_LIMIT_CP,
        ).astype(np.float32),
        dense_z,
    )


def _float_checkpoint_bytes(model: base.FloatNetwork) -> bytes:
    arrays = tuple(model.parameters().values())
    if not all(np.all(np.isfinite(value)) for value in arrays):
        raise ValueError("float checkpoint contains nonfinite values")
    payload = b"".join(
        np.asarray(value, dtype=np.dtype("<f4")).tobytes(order="C")
        for value in arrays
    )
    header = base.FLOAT_CHECKPOINT_HEADER.pack(
        base.FLOAT_CHECKPOINT_MAGIC,
        base.FORMAT_VERSION,
        int(model.ft_weights.shape[0]),
        ACCUMULATOR_SIZE,
        HIDDEN_SIZE,
        len(payload),
        hashlib.sha256(payload).digest(),
    )
    return header + payload


def _read_arch4_float(payload: bytes) -> base.FloatNetwork:
    header_size = base.FLOAT_CHECKPOINT_HEADER.size
    if len(payload) < header_size:
        raise ValueError("float checkpoint is truncated")
    magic, version, features, accumulator, hidden, size, expected_hash = (
        base.FLOAT_CHECKPOINT_HEADER.unpack_from(payload)
    )
    if (
        magic != base.FLOAT_CHECKPOINT_MAGIC
        or version != base.FORMAT_VERSION
        or features != OMEGA_INTERACTION_FEATURE_COUNT
        or accumulator != ACCUMULATOR_SIZE
        or hidden != HIDDEN_SIZE
    ):
        raise ValueError("wrong architecture-4 float checkpoint header")
    body = payload[header_size:]
    if len(body) != size or hashlib.sha256(body).digest() != expected_hash:
        raise ValueError("float checkpoint payload identity failed")
    offset = 0

    def take(shape: tuple[int, ...]) -> np.ndarray:
        nonlocal offset
        count = math.prod(shape)
        bytes_ = count * 4
        result = np.frombuffer(
            body, dtype=np.dtype("<f4"), count=count, offset=offset
        ).reshape(shape).astype(np.float32, copy=True)
        offset += bytes_
        return result

    result = base.FloatNetwork(
        ft_bias=take((ACCUMULATOR_SIZE,)),
        ft_weights=take((OMEGA_INTERACTION_FEATURE_COUNT, ACCUMULATOR_SIZE)),
        dense_bias=take((HIDDEN_SIZE,)),
        dense_weights=take((HIDDEN_SIZE, ACCUMULATOR_SIZE * 2)),
        output_bias=take((1,)),
        output_weights=take((HIDDEN_SIZE,)),
    )
    if offset != len(body):
        raise ValueError("float checkpoint has trailing bytes")
    return result


def _runtime_health(
    network: QuantizedNetwork,
    features: FeatureCorpus,
    *,
    mapped_initializer: QuantizedNetwork,
    migration: Mapping[str, Any],
    cpp_evaluator: Path | None,
    thresholds: Mapping[str, Any],
    require_cpp: bool,
) -> tuple[dict[str, Any], np.ndarray]:
    network_bytes = network.to_bytes()
    if len(network_bytes) != EXPECTED_NETWORK_BYTES:
        raise ValueError("architecture-4 network byte count changed")
    round_trip = QuantizedNetwork.from_bytes(network_bytes)
    canonical = base.FloatNetwork.from_quantized(network)
    canonical_bytes = _float_checkpoint_bytes(canonical)
    reread = _read_arch4_float(canonical_bytes)
    parameter_round_trip = all(
        np.array_equal(canonical.parameters()[key], reread.parameters()[key])
        for key in canonical.parameters()
    )
    requant_exact = (
        canonical.quantize(ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL).to_bytes()
        == network_bytes
    )

    predictions = np.empty(features.count, dtype=np.int32)
    penalties: list[np.ndarray] = []
    ever_positive = np.zeros(HIDDEN_SIZE, dtype=np.bool_)
    ever_below = np.zeros(HIDDEN_SIZE, dtype=np.bool_)
    active_cells = 0
    total_cells = 0
    finite = True
    for start in range(0, features.count, BATCH_SIZE):
        indices = np.arange(
            start, min(start + BATCH_SIZE, features.count), dtype=np.int64
        )
        stm, opponent = features.perspective(indices)
        integer = network.predict_features(stm, opponent)
        floating, dense = _forward_materialized(canonical, stm, opponent)
        predictions[start : start + indices.size] = integer
        penalties.append(np.abs(floating.astype(np.float64) - integer))
        active = (dense > 0.0) & (dense < ACTIVATION_MAX)
        active_cells += int(active.sum())
        total_cells += int(active.size)
        ever_positive |= np.any(dense > 0.0, axis=0)
        ever_below |= np.any(dense < ACTIVATION_MAX, axis=0)
        finite = finite and bool(
            np.all(np.isfinite(floating)) and np.all(np.isfinite(dense))
        )
    errors = np.concatenate(penalties)
    dead = int((~ever_positive).sum())
    saturated = int((~ever_below).sum())
    active_fraction = active_cells / total_cells

    i0_model = base.FloatNetwork.from_quantized(mapped_initializer)
    i0_positive = np.zeros(HIDDEN_SIZE, dtype=np.bool_)
    for start in range(0, features.count, BATCH_SIZE):
        indices = np.arange(
            start, min(start + BATCH_SIZE, features.count), dtype=np.int64
        )
        stm, opponent = features.perspective(indices)
        _prediction, dense = _forward_materialized(i0_model, stm, opponent)
        i0_positive |= np.any(dense > 0.0, axis=0)
    i0_dead = int((~i0_positive).sum())

    cpp_mismatches: int | None = None
    if cpp_evaluator is not None:
        with tempfile.TemporaryDirectory(prefix="omega-g5-health-") as directory:
            path = Path(directory) / "candidate.nnue"
            path.write_bytes(network_bytes)
            cpp = _stream_integers(
                cpp_evaluator, CPP_NETWORK_MODE, features.ofens, network=path
            )
        cpp_mismatches = int(np.count_nonzero(cpp != predictions))
    elif require_cpp:
        raise ValueError("whole-corpus C++ parity helper is required")

    active_range = thresholds["denseActiveFractionRangeInclusive"]
    checks = {
        "meanFloatQuantizationPenalty": float(errors.mean())
        <= float(thresholds["maximumMeanFloatQuantizationPenaltyCp"]),
        "singleFloatQuantizationPenalty": float(errors.max())
        <= float(thresholds["maximumSingleFloatQuantizationPenaltyCp"]),
        "saturatedDenseUnits": saturated
        <= int(thresholds["maximumSaturatedDenseUnits"]),
        "deadDenseUnits": dead <= int(thresholds["maximumDeadDenseUnits"]),
        "deadDenseUnitsVsI0": dead <= i0_dead,
        "denseActiveFraction": float(active_range[0])
        <= active_fraction
        <= float(active_range[1]),
        "maximumAbsoluteCorrection": int(np.max(np.abs(predictions)))
        <= int(thresholds["maximumAbsoluteCorrectionCp"]),
        "finitePredictions": finite,
        "expectedFileSize": len(network_bytes) == EXPECTED_NETWORK_BYTES,
        "roundTripHash": round_trip.to_bytes() == network_bytes,
        "deploymentFloatParameterRoundTripExact": parameter_round_trip,
        "deploymentFloatRequantizationByteIdentical": requant_exact,
        "wholeCorpusPythonCppIntegerAgreement": (
            cpp_mismatches == 0 if cpp_mismatches is not None else not require_cpp
        ),
        "wholeCorpusMigrationParityForI0": bool(migration.get("passed")),
    }
    return {
        "positions": features.count,
        "meanFloatQuantizationPenaltyCp": float(errors.mean()),
        "maximumFloatQuantizationPenaltyCp": float(errors.max()),
        "deadDenseUnits": dead,
        "i0DeadDenseUnits": i0_dead,
        "saturatedDenseUnits": saturated,
        "denseActiveFraction": active_fraction,
        "maximumAbsoluteCorrectionCp": int(np.max(np.abs(predictions))),
        "pythonCppMismatches": cpp_mismatches,
        "checks": checks,
        "passed": all(checks.values()),
        "targetFieldsDecoded": 0,
    }, predictions


@dataclass(frozen=True)
class TrainingResult:
    eligible: bool
    network: QuantizedNetwork | None
    canonical_float: base.FloatNetwork | None
    optimizer_shadow: base.FloatNetwork | None
    selected_epoch: int | None
    common_validation: dict[str, Any] | None
    health: dict[str, Any] | None
    history: tuple[dict[str, Any], ...]
    basis_identity: dict[str, Any]


def _train_candidate(
    *,
    candidate_id: str,
    dataset: DecisionCorpus,
    validation_loader: Callable[[], DecisionCorpus],
    whole_features: FeatureCorpus,
    initializer: QuantizedNetwork,
    seed: int,
    factor_basis: np.ndarray,
    factor_basis_identity: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    cpp_evaluator: Path | None,
    migration: Mapping[str, Any],
    epochs: int = EPOCHS,
    qat_epochs: int = QAT_EPOCHS,
    batch_size: int = BATCH_SIZE,
    require_cpp: bool = True,
    synthetic_schedule: bool = False,
    quiet: bool = False,
) -> TrainingResult:
    if candidate_id not in CANDIDATES:
        raise ValueError(f"unknown generation-5 candidate {candidate_id}")
    if dataset.decoded_splits != ("train",):
        raise ValueError("primary training dataset must decode train targets only")
    if dataset.indices(1).size != 0:
        raise ValueError("validation rows reached the primary training dataset")
    if batch_size != BATCH_SIZE and not synthetic_schedule:
        raise ValueError("generation-5 batch size is frozen at 256")
    if not synthetic_schedule and (epochs, qat_epochs) != (EPOCHS, QAT_EPOCHS):
        raise ValueError("generation-5 epoch schedule is frozen at 48/12")
    first_qat = epochs - qat_epochs + 1
    if not synthetic_schedule and first_qat != FIRST_QAT_EPOCH:
        raise AssertionError("QAT epoch range changed")

    recipe = CANDIDATE_RECIPES[candidate_id]
    rank = int(recipe["rank"])
    basis = np.asarray(factor_basis, dtype=np.float32)
    if basis.shape != (KING_BUCKET_COUNT, rank) or not np.all(np.isfinite(basis)):
        raise ValueError(f"{candidate_id} received an invalid frozen factor basis")
    model = FactorizedNetwork.from_initializer(initializer, basis)
    basis_payload = np.asarray(basis, dtype=np.dtype("<f4")).tobytes(order="C")
    basis_identity = dict(factor_basis_identity)
    if (
        basis_identity.get("rank") != rank
        or basis_identity.get("candidateId") != candidate_id
        or basis_identity.get("bytes") != len(basis_payload)
        or basis_identity.get("sha256") != _sha256_bytes(basis_payload)
        or basis_identity.get("dtype") != "<f4"
        or basis_identity.get("shape") != [KING_BUCKET_COUNT, rank]
        or basis_identity.get("targetFieldsDecoded") != 0
    ):
        raise ValueError(f"{candidate_id} frozen factor-basis identity changed")
    optimizer = Adam(model.parameters())
    row_weights = _phase_group_weights(dataset)
    rng = np.random.default_rng(seed)
    history: list[dict[str, Any]] = []
    best_loss = math.inf
    best_epoch: int | None = None
    best_model: FactorizedNetwork | None = None
    best_network: QuantizedNetwork | None = None
    best_metrics: dict[str, Any] | None = None
    best_health: dict[str, Any] | None = None
    validation_dataset: DecisionCorpus | None = None
    for epoch in range(1, epochs + 1):
        qat = epoch >= first_qat
        losses: list[float] = []
        ranking_pairs = 0
        for batch in _root_batches(dataset, rng):
            loss, gradients, audit = _gradients(
                dataset,
                batch,
                model,
                row_weights=row_weights,
                ranking_weight=float(recipe["rankingWeight"]),
                quantization_aware=qat,
            )
            optimizer.step(gradients, QAT_LR_SCALE if qat else 1.0)
            if not math.isfinite(loss) or not all(
                np.all(np.isfinite(value)) for value in model.parameters().values()
            ):
                raise FloatingPointError(
                    f"{candidate_id} became nonfinite in epoch {epoch}"
                )
            losses.append(loss)
            ranking_pairs += int(audit["rankingPairs"])
        row: dict[str, Any] = {
            "epoch": epoch,
            "quantizationAware": qat,
            "optimizerSteps": optimizer.steps,
            "meanBatchLoss": float(np.mean(losses)),
            "rankingPairs": ranking_pairs,
            "activationSettings": {
                "penaltyWeight": ACTIVATION_PENALTY,
                "targetActiveFraction": ACTIVATION_TARGET,
                "temperature": ACTIVATION_TEMPERATURE,
            },
            "healthCheckedBeforeValidation": qat,
            "validationTargetsAccessed": False,
            "targetAccessBeforeHealth": {
                "targetFieldNames": list(TARGET_FIELDS_DECODED_PER_ROW),
                "trainTargetRowsDecoded": dataset.target_rows_decoded,
                "trainTargetFieldsDecoded": dataset.target_fields_decoded,
                "trainHandcraftedScoresComputed": (
                    dataset.handcrafted_scores_computed
                ),
                "trainResidualTargetsComputed": dataset.residual_targets_computed,
                "validationTargetFieldsDecoded": 0,
                "validationHandcraftedScoresComputed": 0,
                "validationResidualTargetsComputed": 0,
                "heldOutTargetFieldsDecoded": 0,
            },
        }
        if qat:
            network = model.quantize()
            health, predictions = _runtime_health(
                network,
                whole_features,
                mapped_initializer=initializer,
                migration=migration,
                cpp_evaluator=cpp_evaluator,
                thresholds=thresholds,
                require_cpp=require_cpp,
            )
            row["deploymentHealth"] = health
            if health["passed"]:
                if validation_dataset is None:
                    validation_dataset = validation_loader()
                    if validation_dataset.decoded_splits != ("validation",):
                        raise ValueError(
                            "lazy loader decoded a non-validation target split"
                        )
                    if validation_dataset.indices(0).size != 0:
                        raise ValueError("lazy validation corpus contains train rows")
                    if (
                        validation_dataset.target_fields_decoded_by_split.get(
                            "heldOut", -1
                        )
                        != 0
                    ):
                        raise ValueError("lazy validation load decoded held-out targets")
                validation_indices = validation_dataset.indices(1)
                validation_predictions = _predict_network(
                    network, validation_dataset.features, validation_indices
                )
                metrics = _common_metrics(
                    validation_dataset, 1, validation_predictions
                )
                row["validationTargetsAccessed"] = True
                row["validationTargetRowsDecodedAfterHealth"] = (
                    validation_dataset.target_rows_decoded
                )
                row["validationTargetFieldsDecodedAfterHealth"] = (
                    validation_dataset.target_fields_decoded
                )
                row["validationHandcraftedScoresComputedAfterHealth"] = (
                    validation_dataset.handcrafted_scores_computed
                )
                row["validationResidualTargetsComputedAfterHealth"] = (
                    validation_dataset.residual_targets_computed
                )
                row["commonValidation"] = metrics
                loss = float(metrics["huberLoss"])
                if loss < best_loss:
                    best_loss = loss
                    best_epoch = epoch
                    best_model = model.clone()
                    best_network = network
                    best_metrics = metrics
                    best_health = health
            else:
                row["commonValidation"] = None
        history.append(row)
        if not quiet:
            health_text = (
                "n/a"
                if not qat
                else "pass" if row["deploymentHealth"]["passed"] else "fail"
            )
            validation_text = (
                "withheld"
                if row.get("commonValidation") is None
                else f"{row['commonValidation']['huberLoss']:.8f}"
            )
            print(
                f"{candidate_id} epoch {epoch:2d}/{epochs}: "
                f"health={health_text} validation={validation_text}",
                flush=True,
            )
    if best_epoch is None:
        return TrainingResult(
            eligible=False,
            network=None,
            canonical_float=None,
            optimizer_shadow=None,
            selected_epoch=None,
            common_validation=None,
            health=None,
            history=tuple(history),
            basis_identity=basis_identity,
        )
    assert best_model is not None and best_network is not None
    assert best_metrics is not None and best_health is not None
    canonical = base.FloatNetwork.from_quantized(best_network)
    if (
        canonical.quantize(ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL).to_bytes()
        != best_network.to_bytes()
    ):
        raise AssertionError("selected deployment float does not requantize")
    return TrainingResult(
        eligible=True,
        network=best_network,
        canonical_float=canonical,
        optimizer_shadow=best_model.materialize(),
        selected_epoch=best_epoch,
        common_validation=best_metrics,
        health=best_health,
        history=tuple(history),
        basis_identity=basis_identity,
    )


def _candidate_bundle(
    candidate: str,
    output_dir: Path = OUTPUT_DIR,
    *,
    robustness: bool = False,
) -> Path:
    return _resolve(output_dir) / (
        "robustness.bundle" if robustness else f"{candidate}.bundle"
    )


def _candidate_paths(
    candidate: str,
    output_dir: Path = OUTPUT_DIR,
    *,
    robustness: bool = False,
) -> dict[str, Path]:
    bundle = _candidate_bundle(candidate, output_dir, robustness=robustness)
    stem = "robustness" if robustness else candidate
    return {
        "network": bundle / f"{stem}.nnue",
        "canonical": bundle / f"{stem}.float",
        "shadow": bundle / f"{stem}.optimizer-shadow.float",
        "manifest": bundle / f"{stem}.manifest.json",
        "failure": _resolve(output_dir)
        / ("robustness.failure.json" if robustness else f"{candidate}.failure.json"),
    }


def _candidate_claim_path(
    candidate: str,
    output_dir: Path = OUTPUT_DIR,
    *,
    robustness: bool = False,
) -> Path:
    return _resolve(output_dir) / (
        ".robustness.training.claim.json"
        if robustness
        else f".{candidate}.training.claim.json"
    )


def _publish_bundle(
    paths: Mapping[str, Path],
    payloads: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    *,
    interruption_hook: Callable[[str], None] | None = None,
) -> None:
    required = {"network", "canonical", "shadow", "manifest"}
    if set(payloads) != required:
        raise ValueError("candidate payload inventory changed")
    bundle = _resolve(paths["network"].parent)
    if bundle.exists() or _resolve(paths["failure"]).exists():
        raise FileExistsError(bundle if bundle.exists() else paths["failure"])
    bundle.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{bundle.name}-staging-", dir=bundle.parent
    ) as directory:
        staging = Path(directory)
        for key in ("network", "canonical", "shadow", "manifest"):
            _exclusive_bytes(staging / paths[key].name, payloads[key])
            if interruption_hook:
                interruption_hook(f"after-{key}")
        expected = {paths[key].name for key in required}
        if {item.name for item in staging.iterdir()} != expected:
            raise ValueError("staged candidate inventory changed")
        parsed = json.loads((staging / paths["manifest"].name).read_text("utf-8"))
        if parsed != dict(manifest):
            raise ValueError("staged candidate manifest changed")
        for key, manifest_key in (
            ("network", "network"),
            ("canonical", "canonicalDeploymentFloat"),
            ("shadow", "optimizerShadow"),
        ):
            if manifest.get(manifest_key) != _payload_identity(
                paths[key], payloads[key]
            ):
                raise ValueError(f"manifest has wrong {manifest_key} identity")
        if interruption_hook:
            interruption_hook("before-commit")
        os.rename(staging, bundle)


def _candidate_seed(profile: Mapping[str, Any], candidate: str, purpose: str) -> int:
    recipe = CANDIDATE_RECIPES[candidate]
    seeds = _mapping(profile.get("seeds"), "profile seeds")
    return _domain_seed(
        int(seeds["training"]), purpose, candidate, int(recipe["rank"])
    )


def _worker_argv(
    *,
    plan: Path,
    candidate: str,
    profile: Mapping[str, Any],
    output_dir: Path,
    robustness: bool,
) -> list[str]:
    purpose = "robustness-training" if robustness else "candidate-training"
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "train-worker",
        "--plan",
        str(_resolve(plan)),
        "--candidate",
        candidate,
        "--seed",
        str(_candidate_seed(profile, candidate, purpose)),
        "--factor-seed",
        str(_candidate_seed(profile, candidate, "factor-basis")),
        "--output-dir",
        str(_resolve(output_dir)),
        *( ["--robustness"] if robustness else [] ),
    ]


def _worker_command(
    *,
    plan: Path,
    candidate: str,
    profile: Mapping[str, Any],
    output_dir: Path,
    robustness: bool,
) -> dict[str, Any]:
    paths = _candidate_paths(
        candidate, output_dir, robustness=robustness
    )
    return {
        "candidateId": candidate,
        "purpose": "robustness-training" if robustness else "primary-training",
        "seed": _candidate_seed(
            profile,
            candidate,
            "robustness-training" if robustness else "candidate-training",
        ),
        "factorSeed": _candidate_seed(profile, candidate, "factor-basis"),
        "argv": _worker_argv(
            plan=plan,
            candidate=candidate,
            profile=profile,
            output_dir=output_dir,
            robustness=robustness,
        ),
        "environment": dict(DETERMINISTIC_WORKER_ENVIRONMENT),
        "bundle": str(
            _candidate_bundle(candidate, output_dir, robustness=robustness)
        ),
        **{key: str(value) for key, value in paths.items()},
        "claim": str(
            _candidate_claim_path(
                candidate, output_dir, robustness=robustness
            )
        ),
    }


def _validate_profile(path: Path) -> dict[str, Any]:
    profile = _load_json(path, "generation-5 preregistration")
    prereg_validator.validate_profile(profile, mode="frozen", verify_external=True)
    if (
        profile.get("kind") != PROFILE_KIND
        or profile.get("profileId") != PROFILE_ID
        or profile.get("status") != PROFILE_STATUS
    ):
        raise ValueError("wrong generation-5 preregistration")
    if _resolve(path) != _resolve(DEFAULT_PROFILE):
        raise ValueError("frozen preregistration is outside its canonical path")
    trainer_identity = _frozen_file_identity(
        profile, "trainerSource", "frozen trainer source"
    )
    if trainer_identity != _identity(Path(__file__)):
        raise ValueError("final preregistration does not pin this trainer exactly")
    base_trainer_identity = _frozen_file_identity(
        profile, "baseTrainerSource", "frozen base trainer source"
    )
    if base_trainer_identity != _identity(Path(base.__file__)):
        raise ValueError("final preregistration does not pin the imported base trainer")
    network_format_identity = _frozen_file_identity(
        profile, "networkFormatPythonSource", "frozen network format source"
    )
    if network_format_identity != _identity(Path(network_format.__file__)):
        raise ValueError("final preregistration does not pin the imported network format")
    runtime_tool_identity = _frozen_file_identity(
        profile, "pythonRuntimeToolSource", "frozen runtime verifier source"
    )
    if runtime_tool_identity != _identity(Path(runtime_contract.__file__)):
        raise ValueError("final preregistration does not pin the runtime verifier")
    runtime_manifest_identity = _frozen_file_identity(
        profile, "pythonRuntimeManifest", "frozen Python/NumPy runtime manifest"
    )
    if runtime_manifest_identity != _identity(runtime_contract.DEFAULT_OUTPUT):
        raise ValueError("final preregistration does not pin the runtime manifest")
    runtime_contract.verify_manifest(runtime_contract.DEFAULT_OUTPUT)
    _verify_final_freeze_binding(profile, path)
    return profile


def _validate_manifest(manifest: Mapping[str, Any], corpus: Path) -> None:
    if manifest.get("kind") != "omega-decision-label-manifest":
        raise ValueError("wrong decision corpus manifest kind")
    policy = _mapping(manifest.get("policy"), "decision manifest policy")
    expected = {
        "childrenPerRoot": 4,
        "rankingGapIgnoreBelowCp": RANKING_IGNORE_BELOW_CP,
        "rankingGapCapCp": RANKING_GAP_CAP_CP,
        "wholeSourceGroup": True,
        "symmetryAndTranspositionComponents": True,
    }
    for key, value in expected.items():
        if policy.get(key) != value:
            raise ValueError(f"decision manifest policy changed: {key}")
    output = _mapping(manifest.get("output"), "decision manifest output")
    actual = _identity(corpus)
    if (
        int(output.get("bytes", -1)) != actual["bytes"]
        or str(output.get("sha256", "")).lower() != actual["sha256"]
    ):
        raise ValueError("decision manifest does not pin the corpus")


def _prepare_plan(args: argparse.Namespace) -> dict[str, Any]:
    profile = _validate_profile(args.profile)
    corpus = _resolve(args.corpus)
    manifest_path = _resolve(args.corpus_manifest)
    initializer_path = _resolve(args.initializer)
    cpp_evaluator = _resolve(args.cpp_evaluator)
    output_dir = _resolve(args.output_dir)
    plan_path = _resolve(args.plan)
    selection_path = _resolve(args.selection)

    data_root = _namespace_path(profile, "dataRoot", "data root namespace")
    training_root = _namespace_path(
        profile, "trainingRoot", "training root namespace"
    )
    expected_paths = {
        "corpus": data_root / "decision-labels.jsonl",
        "corpusManifest": data_root / "decision-labels.jsonl.manifest.json",
        "outputDirectory": training_root,
        "plan": training_root / "training-plan.json",
        "selection": _namespace_path(
            profile, "selectionSeal", "selection namespace"
        ),
        "robustness": _namespace_path(
            profile, "robustnessSeal", "robustness namespace"
        ),
        "offlineAccessClaim": _namespace_path(
            profile, "offlineAccessClaim", "offline access-claim namespace"
        ),
        "offlineReport": _namespace_path(
            profile, "offlineReport", "offline-report namespace"
        ),
    }
    actual_paths = {
        "corpus": corpus,
        "corpusManifest": manifest_path,
        "outputDirectory": output_dir,
        "plan": plan_path,
        "selection": selection_path,
        "robustness": DEFAULT_ROBUSTNESS,
        "offlineAccessClaim": DEFAULT_OFFLINE_CLAIM,
        "offlineReport": DEFAULT_OFFLINE_REPORT,
    }
    for key, expected in expected_paths.items():
        if actual_paths[key] != _resolve(expected):
            raise ValueError(f"{key} path differs from the frozen namespace")

    # A hard kill after the atomic final-name commit may prevent the original
    # invocation from reporting success.  Re-running is read-only and
    # idempotent only for a fully valid, current-source-bound plan; malformed,
    # partial, or stale bytes are never replaced.
    if plan_path.exists():
        existing_plan, _existing_profile = _verify_plan(plan_path)
        return existing_plan

    frozen_initializer = _frozen_file_identity(
        profile, "mappedK2Initializer", "mapped K2 initializer"
    )
    frozen_initializer_manifest = _frozen_file_identity(
        profile,
        "mappedK2InitializerManifest",
        "mapped K2 initializer manifest",
    )
    frozen_cpp = _frozen_file_identity(
        profile, "cppStaticAndNnueEvaluator", "frozen C++ evaluator"
    )
    if initializer_path != Path(str(frozen_initializer["path"])):
        raise ValueError("initializer path differs from the final freeze")
    if cpp_evaluator != Path(str(frozen_cpp["path"])):
        raise ValueError("C++ evaluator path differs from the final freeze")

    for path in (corpus, manifest_path, initializer_path, cpp_evaluator):
        if not path.is_file():
            raise FileNotFoundError(path)
    initializer_manifest = _load_json(
        Path(str(frozen_initializer_manifest["path"])),
        "mapped K2 initializer manifest",
    )
    if not _contains_digest_identity(initializer_manifest, frozen_initializer):
        raise ValueError("mapped K2 manifest does not pin the frozen initializer")
    manifest = _load_json(manifest_path, "decision corpus manifest")
    _validate_manifest(manifest, corpus)
    features = _load_feature_corpus(corpus)
    audit = _audit_four_siblings(features)
    if audit["rows"] != 16384 or audit["roots"] != 4096:
        raise ValueError("generation-5 corpus is not exactly 4,096 x 4 rows")
    phase_root_totals = {
        phase: len(
            {
                root
                for root, row_phase in zip(features.root_ids, features.phases)
                if row_phase == phase
            }
        )
        for phase in PHASES
    }
    if any(value != 1024 for value in phase_root_totals.values()):
        raise ValueError("generation-5 phase quotas are not exactly 1,024 roots")
    initializer = QuantizedNetwork.read(initializer_path)
    migrated = _migrate_initializer(initializer)
    migration = _migration_parity(initializer, migrated, features)
    if not migration["passed"]:
        raise ValueError("K2 architecture-4 migration prediction parity failed")
    if selection_path.exists():
        raise FileExistsError(selection_path)
    for candidate in CANDIDATES:
        paths = _candidate_paths(candidate, output_dir)
        expected_bundle = _candidate_namespace(profile, candidate)
        if _candidate_bundle(candidate, output_dir) != expected_bundle:
            raise ValueError(f"{candidate} bundle differs from frozen namespace")
        for key in ("network", "canonical", "shadow", "manifest", "failure"):
            if paths[key].exists():
                raise FileExistsError(paths[key])
        for path in (
            expected_bundle,
            _candidate_claim_path(candidate, output_dir),
        ):
            if path.exists():
                raise FileExistsError(path)
    robustness_paths = _candidate_paths(
        CANDIDATES[0], output_dir, robustness=True
    )
    if _candidate_bundle(
        CANDIDATES[0], output_dir, robustness=True
    ) != _candidate_namespace(profile, CANDIDATES[0], robustness=True):
        raise ValueError("robustness bundle differs from frozen namespace")
    postselection_paths = {
        *robustness_paths.values(),
        _candidate_claim_path(CANDIDATES[0], output_dir, robustness=True),
        expected_paths["robustness"],
        expected_paths["offlineAccessClaim"],
        expected_paths["offlineReport"],
    }
    for path in postselection_paths:
        if path.exists():
            raise FileExistsError(path)
    identities = {
        "profile": _identity(args.profile),
        "finalFreezeSeal": _verify_final_freeze_binding(profile, args.profile),
        "corpus": _identity(corpus),
        "corpusManifest": _identity(manifest_path),
        "initializer": frozen_initializer,
        "initializerManifest": frozen_initializer_manifest,
        "cppEvaluator": frozen_cpp,
        "trainer": _identity(Path(__file__)),
        "baseTrainer": _frozen_file_identity(
            profile, "baseTrainerSource", "frozen base trainer"
        ),
        "pythonRuntimeManifest": _frozen_file_identity(
            profile, "pythonRuntimeManifest", "frozen Python/NumPy runtime manifest"
        ),
        "pythonRuntimeTool": _frozen_file_identity(
            profile, "pythonRuntimeToolSource", "frozen runtime verifier"
        ),
        "networkFormat": _frozen_file_identity(
            profile, "networkFormatPythonSource", "frozen network format"
        ),
        "profileValidator": _frozen_file_identity(
            profile,
            "preregistrationValidatorSource",
            "frozen preregistration validator",
        ),
    }
    factor_bases: dict[str, dict[str, Any]] = {}
    for candidate in CANDIDATES:
        recipe = CANDIDATE_RECIPES[candidate]
        factor_seed = _candidate_seed(profile, candidate, "factor-basis")
        basis = _factor_basis(factor_seed, int(recipe["rank"]))
        payload = np.asarray(basis, dtype=np.dtype("<f4")).tobytes(order="C")
        basis_path = _basis_path(candidate, output_dir)
        # Basis bytes are target-blind and exactly reproducible.  A prior
        # interruption may therefore leave them before the plan commit; accept
        # only an exact recomputation and never replace an existing file.
        _publish_or_verify_deterministic_bytes(basis_path, payload)
        factor_bases[candidate] = _basis_record(
            candidate,
            basis_path,
            payload,
            rank=int(recipe["rank"]),
            seed=factor_seed,
        )
    commands = {
        candidate: _worker_command(
            plan=plan_path,
            candidate=candidate,
            profile=profile,
            output_dir=output_dir,
            robustness=False,
        )
        for candidate in CANDIDATES
    }
    robustness_commands = {
        candidate: _worker_command(
            plan=plan_path,
            candidate=candidate,
            profile=profile,
            output_dir=output_dir,
            robustness=True,
        )
        for candidate in CANDIDATES
    }
    plan = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "profileId": PROFILE_ID,
        "createdUtc": _utc_now(),
        "identities": identities,
        "targetOpaqueCorpusAudit": {
            **audit,
            "phaseRootTotals": phase_root_totals,
            "heldOutTargetFieldsDecoded": 0,
        },
        "initializerMigration": migration,
        "candidateRecipes": CANDIDATE_RECIPES,
        "factorBases": factor_bases,
        "activationSettings": {
            "penaltyWeight": ACTIVATION_PENALTY,
            "targetActiveFraction": ACTIVATION_TARGET,
            "temperature": ACTIVATION_TEMPERATURE,
        },
        "commands": commands,
        "robustnessCommands": robustness_commands,
        "outputDirectory": str(output_dir),
        "selectionPath": str(selection_path),
        "outputs": {
            "selection": str(expected_paths["selection"]),
            "robustnessBundle": str(
                _candidate_namespace(profile, CANDIDATES[0], robustness=True)
            ),
            "robustnessClaim": str(
                _candidate_claim_path(CANDIDATES[0], output_dir, robustness=True)
            ),
            "robustnessFailure": str(robustness_paths["failure"]),
            "robustnessSeal": str(expected_paths["robustness"]),
            "offlineAccessClaim": str(expected_paths["offlineAccessClaim"]),
            "offlineReport": str(expected_paths["offlineReport"]),
        },
        "candidateBundles": {
            candidate: str(_candidate_namespace(profile, candidate))
            for candidate in CANDIDATES
        },
    }
    # Commit the plan last.  Its complete bytes are staged and fsynced before
    # the final path appears atomically, so a hard kill can expose only no plan
    # or the complete plan.
    _atomic_exclusive_json(plan_path, plan)
    return plan


def _verify_plan(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_path = _resolve(path)
    plan = _load_json(plan_path, "generation-5 training plan")
    expected_plan_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "identities",
        "targetOpaqueCorpusAudit",
        "initializerMigration",
        "candidateRecipes",
        "factorBases",
        "activationSettings",
        "commands",
        "robustnessCommands",
        "outputDirectory",
        "selectionPath",
        "outputs",
        "candidateBundles",
    }
    if set(plan) != expected_plan_fields or plan.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("generation-5 training-plan field inventory changed")
    if plan.get("kind") != PLAN_KIND or plan.get("profileId") != PROFILE_ID:
        raise ValueError("wrong generation-5 training plan")
    identities = _mapping(plan.get("identities"), "plan identities")
    expected_identity_keys = {
        "profile",
        "finalFreezeSeal",
        "corpus",
        "corpusManifest",
        "initializer",
        "initializerManifest",
        "cppEvaluator",
        "trainer",
        "baseTrainer",
        "pythonRuntimeManifest",
        "pythonRuntimeTool",
        "networkFormat",
        "profileValidator",
    }
    if set(identities) != expected_identity_keys:
        raise ValueError("plan identity inventory changed")
    for key in expected_identity_keys:
        _verify_identity(identities.get(key), f"plan {key}")
    if _identity(Path(__file__)) != identities["trainer"]:
        raise ValueError("running trainer differs from planned trainer")
    profile_path = Path(str(identities["profile"]["path"]))
    profile = _validate_profile(profile_path)
    if identities.get("finalFreezeSeal") != _verify_final_freeze_binding(
        profile, profile_path
    ):
        raise ValueError("plan final-freeze seal identity changed")

    expected_plan = _namespace_path(
        profile, "trainingRoot", "training namespace"
    ) / "training-plan.json"
    expected_output = _namespace_path(
        profile, "trainingRoot", "training namespace"
    )
    expected_selection = _namespace_path(
        profile, "selectionSeal", "selection namespace"
    )
    data_root = _namespace_path(profile, "dataRoot", "data namespace")
    if plan_path != _resolve(expected_plan):
        raise ValueError("plan is outside the frozen training namespace")
    if _resolve(Path(str(plan.get("outputDirectory")))) != expected_output:
        raise ValueError("planned output directory differs from frozen namespace")
    if _resolve(Path(str(plan.get("selectionPath")))) != expected_selection:
        raise ValueError("planned selection path differs from frozen namespace")
    if Path(str(identities["corpus"]["path"])) != data_root / "decision-labels.jsonl":
        raise ValueError("planned corpus differs from frozen data namespace")
    if (
        Path(str(identities["corpusManifest"]["path"]))
        != data_root / "decision-labels.jsonl.manifest.json"
    ):
        raise ValueError("planned corpus manifest differs from frozen namespace")

    frozen_identity_bindings = {
        "initializer": "mappedK2Initializer",
        "initializerManifest": "mappedK2InitializerManifest",
        "cppEvaluator": "cppStaticAndNnueEvaluator",
        "trainer": "trainerSource",
        "baseTrainer": "baseTrainerSource",
        "pythonRuntimeManifest": "pythonRuntimeManifest",
        "pythonRuntimeTool": "pythonRuntimeToolSource",
        "networkFormat": "networkFormatPythonSource",
        "profileValidator": "preregistrationValidatorSource",
    }
    for plan_key, profile_key in frozen_identity_bindings.items():
        frozen = _frozen_file_identity(
            profile, profile_key, f"frozen {profile_key}"
        )
        if identities.get(plan_key) != frozen:
            raise ValueError(f"plan {plan_key} differs from final freeze")

    if plan.get("candidateRecipes") != CANDIDATE_RECIPES:
        raise ValueError("planned candidate recipes changed")
    if plan.get("activationSettings") != {
        "penaltyWeight": ACTIVATION_PENALTY,
        "targetActiveFraction": ACTIVATION_TARGET,
        "temperature": ACTIVATION_TEMPERATURE,
    }:
        raise ValueError("planned activation settings changed")
    expected_bundles = {
        candidate: str(_candidate_namespace(profile, candidate))
        for candidate in CANDIDATES
    }
    if plan.get("candidateBundles") != expected_bundles:
        raise ValueError("planned candidate bundle namespaces changed")

    expected_outputs = {
        "selection": str(expected_selection),
        "robustnessBundle": str(
            _candidate_namespace(profile, CANDIDATES[0], robustness=True)
        ),
        "robustnessClaim": str(
            _candidate_claim_path(CANDIDATES[0], expected_output, robustness=True)
        ),
        "robustnessFailure": str(
            _candidate_paths(
                CANDIDATES[0], expected_output, robustness=True
            )["failure"]
        ),
        "robustnessSeal": str(
            _namespace_path(profile, "robustnessSeal", "robustness namespace")
        ),
        "offlineAccessClaim": str(
            _namespace_path(
                profile, "offlineAccessClaim", "offline claim namespace"
            )
        ),
        "offlineReport": str(
            _namespace_path(profile, "offlineReport", "offline report namespace")
        ),
    }
    if plan.get("outputs") != expected_outputs:
        raise ValueError("planned post-selection outputs changed")

    factor_bases = _mapping(plan.get("factorBases"), "planned factor bases")
    if set(factor_bases) != set(CANDIDATES):
        raise ValueError("planned factor-basis inventory changed")
    commands = _mapping(plan.get("commands"), "planned commands")
    robustness_commands = _mapping(
        plan.get("robustnessCommands"), "planned robustness commands"
    )
    if set(commands) != set(CANDIDATES):
        raise ValueError("planned command inventory changed")
    if set(robustness_commands) != set(CANDIDATES):
        raise ValueError("planned robustness-command inventory changed")
    for candidate in CANDIDATES:
        recipe = CANDIDATE_RECIPES[candidate]
        seed = _candidate_seed(profile, candidate, "candidate-training")
        factor_seed = _candidate_seed(profile, candidate, "factor-basis")
        basis_record = _mapping(
            factor_bases[candidate], f"{candidate} planned factor basis"
        )
        if Path(str(basis_record.get("path"))) != _basis_path(
            candidate, expected_output
        ):
            raise ValueError(f"{candidate} factor basis is outside its namespace")
        _load_factor_basis(
            basis_record,
            candidate=candidate,
            rank=int(recipe["rank"]),
            seed=factor_seed,
        )
        command = _mapping(commands[candidate], f"{candidate} command")
        expected_command = _worker_command(
            plan=plan_path,
            candidate=candidate,
            profile=profile,
            output_dir=expected_output,
            robustness=False,
        )
        if command != expected_command:
            raise ValueError(f"{candidate} planned command changed")
        robustness_command = _mapping(
            robustness_commands[candidate],
            f"{candidate} robustness command",
        )
        expected_robustness_command = _worker_command(
            plan=plan_path,
            candidate=candidate,
            profile=profile,
            output_dir=expected_output,
            robustness=True,
        )
        if robustness_command != expected_robustness_command:
            raise ValueError(f"{candidate} planned robustness command changed")
    return plan, profile


def _verify_primary_training_claim(
    path: Path,
    *,
    candidate: str,
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
    plan_path: Path,
) -> dict[str, Any]:
    claim_path = _resolve(path)
    value = _load_json(claim_path, f"{candidate} primary training claim")
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "candidateId",
        "plan",
        "outputDirectory",
        "bundle",
        "trainingSeed",
        "factorSeed",
        "factorBasis",
        "trainingPurpose",
        "createdUtc",
        "targetFieldsDecoded",
        "owner",
    }
    if set(value) != expected_fields:
        raise ValueError("primary training claim field inventory changed")
    output_dir = _resolve(Path(str(plan["outputDirectory"])))
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "omega-nnue-king-state-v5-training-claim"
        or value.get("profileId") != PROFILE_ID
        or value.get("candidateId") != candidate
        or value.get("plan") != _identity(plan_path)
        or _resolve(Path(str(value.get("outputDirectory")))) != output_dir
        or _resolve(Path(str(value.get("bundle"))))
        != _candidate_bundle(candidate, output_dir)
        or value.get("trainingSeed")
        != _candidate_seed(profile, candidate, "candidate-training")
        or value.get("factorSeed")
        != _candidate_seed(profile, candidate, "factor-basis")
        or value.get("factorBasis")
        != _mapping(plan["factorBases"], "factor bases")[candidate]
        or value.get("trainingPurpose") != "primary-training"
        or value.get("targetFieldsDecoded") != 0
    ):
        raise ValueError("primary training claim differs from its frozen plan")
    owner = _mapping(value.get("owner"), "primary claim owner")
    if set(owner) != {
        "machine",
        "pid",
        "pythonExecutable",
        "lockProtocol",
    }:
        raise ValueError("primary training claim owner fields changed")
    if (
        type(owner.get("machine")) is not str
        or not owner["machine"]
        or type(owner.get("pid")) is not int
        or owner["pid"] <= 0
        or owner.get("lockProtocol") != PRIMARY_CLAIM_LOCK_PROTOCOL
        or owner.get("pythonExecutable") != _identity(Path(sys.executable))
    ):
        raise ValueError("primary training claim owner is invalid")
    try:
        datetime.fromisoformat(str(value.get("createdUtc", "")).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("primary training claim createdUtc is invalid") from error
    return value


def _recover_stale_claim_file(
    claim_path: Path,
    authenticated_claim: Mapping[str, Any],
    *,
    blocked_outputs: Sequence[Path],
    committed_bundle: Path | None = None,
    committed_failure: Path | None = None,
    committed_output_identities: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Remove one authenticated, unlocked, same-host dead-owner claim.

    The caller must first authenticate the full claim against its frozen plan.
    This helper then proves either that no output exists or that one fully
    verified committed bundle remains byte-identical, that all forbidden output
    is absent, the lifetime OS lock is unowned, the file did not change while
    locked, and the recorded PID is not live (PID reuse therefore fails closed).
    """

    path = _resolve(claim_path)
    owner = _mapping(authenticated_claim.get("owner"), "stale claim owner")
    if owner.get("machine") != _machine_name():
        raise ValueError("primary claim belongs to another or ambiguous host")
    committed = dict(committed_output_identities or {})
    terminal_states = int(committed_bundle is not None) + int(
        committed_failure is not None
    )
    if terminal_states > 1 or (terminal_states == 0) != (not committed):
        raise ValueError("completed-output recovery state is incomplete")
    if committed_bundle is not None and set(committed) != {
        "network",
        "canonical",
        "shadow",
        "manifest",
    }:
        raise ValueError("completed-output recovery inventory changed")
    if committed_failure is not None and set(committed) != {"failure"}:
        raise ValueError("completed-failure recovery inventory changed")

    def require_output_state() -> None:
        present = sorted(
            str(_resolve(candidate))
            for candidate in blocked_outputs
            if _resolve(candidate).exists()
        )
        if present:
            raise FileExistsError(
                "primary claim recovery refused because committed output exists: "
                + ", ".join(present)
            )
        if terminal_states == 0:
            return
        if committed_failure is not None:
            failure = _resolve(committed_failure)
            identity = _mapping(
                committed["failure"], "committed failure identity"
            )
            if (
                _resolve(Path(str(identity.get("path")))) != failure
                or not failure.is_file()
                or _identity(failure) != identity
            ):
                raise ValueError(
                    "completed candidate failure changed during recovery"
                )
            return
        assert committed_bundle is not None
        bundle = _resolve(committed_bundle)
        expected_names: set[str] = set()
        for key, raw_identity in committed.items():
            identity = _mapping(raw_identity, f"committed {key} identity")
            artifact = _resolve(Path(str(identity.get("path"))))
            if artifact.parent != bundle:
                raise ValueError("completed-output identity escaped its bundle")
            expected_names.add(artifact.name)
            if not artifact.is_file() or _identity(artifact) != identity:
                raise ValueError(
                    f"completed candidate output changed during recovery: {artifact}"
                )
        if not bundle.is_dir() or {
            item.name for item in bundle.iterdir()
        } != expected_names:
            raise ValueError("completed candidate bundle inventory changed")

    require_output_state()
    owner_pid = int(owner["pid"])
    if _pid_exists(owner_pid):
        raise RuntimeError(
            "primary claim PID is live or has been reused; recovery is ambiguous"
        )
    try:
        before = _identity(path)
    except OSError as error:
        raise RuntimeError(
            "primary claim is locked or inaccessible by a live or ambiguous worker"
        ) from error
    held = _acquire_existing_claim_lock(path)
    if held is None:
        raise RuntimeError("primary claim is locked by a live or ambiguous worker")
    try:
        locked_identity, current = _read_held_claim(
            path, held, "locked primary training claim"
        )
        if locked_identity != before:
            raise ValueError("primary claim changed before stale-lock acquisition")
        if current != dict(authenticated_claim):
            raise ValueError("primary claim changed during stale recovery")
        if _pid_exists(owner_pid):
            raise RuntimeError(
                "primary claim PID is live or has been reused; recovery is ambiguous"
            )
        require_output_state()
    finally:
        held.release()
    # Cooperating workers cannot enter while the claim exists.  A concurrent
    # explicit recoverer already failed the lock attempt above; after releasing
    # the Windows byte-range lock, rehash once more and remove exactly this file.
    if _identity(path) != before:
        raise ValueError("primary claim changed before recovery commit")
    require_output_state()
    path.unlink()
    return {
        "claim": before,
        "owner": dict(owner),
        "lockWasUnowned": True,
        "recordedPidWasAbsent": True,
        "recoveryMode": (
            "completed-candidate-bundle"
            if committed_bundle is not None
            else (
                "completed-candidate-failure"
                if committed_failure is not None
                else "uncommitted-stale-claim"
            )
        ),
        "committedOutputsAbsent": terminal_states == 0,
        "completedCandidateBundleVerified": committed_bundle is not None,
        "completedCandidateFailureVerified": committed_failure is not None,
        "completedCandidateOutputs": committed,
    }


def _recover_primary_claim(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = _resolve(args.plan)
    plan, profile = _verify_plan(plan_path)
    candidate = str(args.candidate)
    output_dir = _resolve(Path(str(plan["outputDirectory"])))
    claim_path = _candidate_claim_path(candidate, output_dir)
    if not claim_path.is_file():
        raise FileNotFoundError(claim_path)
    try:
        claim = _verify_primary_training_claim(
            claim_path,
            candidate=candidate,
            plan=plan,
            profile=profile,
            plan_path=plan_path,
        )
    except OSError as error:
        raise RuntimeError(
            "primary claim is locked or inaccessible by a live or ambiguous worker"
        ) from error
    candidate_paths = _candidate_paths(candidate, output_dir)
    bundle = _candidate_bundle(candidate, output_dir)
    outputs = _mapping(plan.get("outputs"), "plan outputs")
    downstream = {
        _resolve(Path(str(plan["selectionPath"]))),
        *(_resolve(Path(str(value))) for value in outputs.values()),
    }
    owner = _mapping(claim.get("owner"), "primary claim owner")
    if owner.get("machine") != _machine_name():
        raise ValueError("primary claim belongs to another or ambiguous host")
    if _pid_exists(int(owner["pid"])):
        raise RuntimeError(
            "primary claim PID is live or has been reused; recovery is ambiguous"
        )
    committed_bundle: Path | None = None
    committed_failure: Path | None = None
    if bundle.exists():
        committed_identities = _verify_completed_primary_bundle(
            candidate=candidate,
            plan=plan,
            profile=profile,
            plan_path=plan_path,
            output_dir=output_dir,
        )
        blocked = {candidate_paths["failure"], *downstream}
        committed_bundle = bundle
    elif candidate_paths["failure"].exists():
        committed_identities = _verify_completed_primary_failure(
            candidate=candidate,
            plan=plan,
            profile=profile,
            plan_path=plan_path,
            output_dir=output_dir,
        )
        blocked = {
            bundle,
            *(candidate_paths[key] for key in ("network", "canonical", "shadow", "manifest")),
            *downstream,
        }
        committed_failure = candidate_paths["failure"]
    else:
        committed_identities = None
        blocked = {bundle, *candidate_paths.values(), *downstream}
    recovery = _recover_stale_claim_file(
        claim_path,
        claim,
        blocked_outputs=sorted(blocked, key=str),
        committed_bundle=committed_bundle,
        committed_failure=committed_failure,
        committed_output_identities=committed_identities,
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v5-primary-claim-recovery",
        "profileId": PROFILE_ID,
        "candidateId": candidate,
        "plan": _identity(plan_path),
        **recovery,
    }


def _run_worker(args: argparse.Namespace) -> dict[str, Any]:
    plan, profile = _verify_plan(args.plan)
    if args.candidate not in CANDIDATES:
        raise ValueError("unknown generation-5 candidate")
    output_dir = _resolve(args.output_dir)
    if output_dir != _resolve(Path(str(plan.get("outputDirectory")))):
        raise ValueError("worker output directory differs from the frozen plan")
    robustness = bool(args.robustness)
    bundle = _candidate_bundle(
        args.candidate, output_dir, robustness=robustness
    )
    if bundle != _candidate_namespace(
        profile, args.candidate, robustness=robustness
    ):
        raise ValueError("worker bundle differs from the frozen namespace")
    selection = _resolve(Path(str(plan.get("selectionPath"))))
    paths = _candidate_paths(
        args.candidate, output_dir, robustness=robustness
    )
    if robustness:
        selection_record, _selection_plan, _selection_profile = (
            _verify_selection(selection)
        )
        if selection_record.get("winner") != args.candidate:
            raise ValueError("robustness worker is not the validation winner")
    # This check deliberately happens before either train or validation target
    # decoding.  The O_EXCL claim closes the race between two clean workers.
    preflight = [
        bundle,
        paths["network"],
        paths["canonical"],
        paths["shadow"],
        paths["manifest"],
        paths["failure"],
    ]
    if robustness:
        preflight.extend(
            (
                Path(str(_mapping(plan["outputs"], "outputs")["robustnessSeal"])),
                Path(str(_mapping(plan["outputs"], "outputs")["offlineAccessClaim"])),
                Path(str(_mapping(plan["outputs"], "outputs")["offlineReport"])),
            )
        )
    else:
        preflight.append(selection)
    for path in preflight:
        if path.exists():
            raise FileExistsError(path)
    claim_path = _candidate_claim_path(
        args.candidate, output_dir, robustness=robustness
    )
    claim = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": (
            "omega-nnue-king-state-v5-robustness-training-claim"
            if robustness
            else "omega-nnue-king-state-v5-training-claim"
        ),
        "profileId": PROFILE_ID,
        "candidateId": args.candidate,
        "plan": _identity(args.plan),
        "outputDirectory": str(output_dir),
        "bundle": str(bundle),
        "trainingSeed": args.seed,
        "factorSeed": args.factor_seed,
        "factorBasis": _mapping(plan["factorBases"], "factor bases")[
            args.candidate
        ],
        "trainingPurpose": (
            "robustness-training" if robustness else "primary-training"
        ),
        "createdUtc": _utc_now(),
        "targetFieldsDecoded": 0,
    }
    if not robustness:
        # A primary claim is intentionally non-resumable.  Its owner record and
        # lifetime OS lock let the separate recovery command distinguish an
        # interrupted worker from a live, reused-PID, or otherwise ambiguous
        # owner without ever interpreting claim presence as permission to run.
        claim["owner"] = _claim_owner_record()
    created_claim = False
    claim_lock: _HeldClaimLock | None = None
    try:
        if robustness:
            _exclusive_json(claim_path, claim)
        else:
            claim_lock = _exclusive_locked_json(claim_path, claim)
        created_claim = True
    except FileExistsError:
        if not robustness:
            raise
        _verify_robustness_training_claim(
            claim_path,
            candidate=args.candidate,
            selection=selection_record,
            plan=plan,
            profile=profile,
        )
    try:
        # Recheck after winning the claim in case an unclaimed legacy writer
        # raced the initial preflight.
        for path in preflight:
            if path.exists():
                raise FileExistsError(path)
        return _run_worker_claimed(args, plan, profile)
    finally:
        # A resumed robustness worker did not create the immutable claim and
        # leaves it for run-robustness to retire only after verifying the
        # atomic bundle commit.  This makes a pre-commit process crash safely
        # retryable without inventing a second claim.
        if claim_lock is not None:
            claim_lock.release()
        if created_claim:
            claim_path.unlink(missing_ok=True)


def _run_worker_claimed(
    args: argparse.Namespace,
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    if args.candidate not in CANDIDATES:
        raise ValueError("unknown generation-5 candidate")
    robustness = bool(args.robustness)
    command_key = "robustnessCommands" if robustness else "commands"
    command = _mapping(
        _mapping(plan[command_key], command_key).get(args.candidate),
        "planned command",
    )
    actual_argv = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    if command.get("argv") != actual_argv:
        raise ValueError("worker argv differs from the frozen plan")
    expected_purpose = (
        "robustness-training" if robustness else "candidate-training"
    )
    if args.seed != _candidate_seed(profile, args.candidate, expected_purpose):
        raise ValueError("candidate training seed differs from preregistration")
    if args.factor_seed != _candidate_seed(profile, args.candidate, "factor-basis"):
        raise ValueError("factor basis seed differs from preregistration")
    identities = plan["identities"]
    corpus_path = Path(str(identities["corpus"]["path"]))
    cpp_evaluator = Path(str(identities["cppEvaluator"]["path"]))
    whole = _load_feature_corpus(corpus_path)
    dataset = _load_decision_splits(
        corpus_path,
        hce_evaluator=cpp_evaluator,
        decode_splits=("train",),
    )
    validation_cache: DecisionCorpus | None = None

    def load_validation() -> DecisionCorpus:
        nonlocal validation_cache
        if validation_cache is None:
            validation_cache = _load_decision_splits(
                corpus_path,
                hce_evaluator=cpp_evaluator,
                decode_splits=("validation",),
            )
        return validation_cache
    initializer3 = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    initializer4 = _migrate_initializer(initializer3)
    migration = _migration_parity(initializer3, initializer4, whole)
    if migration != plan.get("initializerMigration"):
        raise ValueError("initializer migration audit changed after planning")
    thresholds = _mapping(
        profile.get("deploymentHealthGate"), "deployment health thresholds"
    )
    factor_basis_record = _mapping(
        _mapping(plan.get("factorBases"), "planned factor bases").get(
            args.candidate
        ),
        f"{args.candidate} factor basis",
    )
    factor_basis = _load_factor_basis(
        factor_basis_record,
        candidate=args.candidate,
        rank=int(CANDIDATE_RECIPES[args.candidate]["rank"]),
        seed=args.factor_seed,
    )
    result = _train_candidate(
        candidate_id=args.candidate,
        dataset=dataset,
        validation_loader=load_validation,
        whole_features=whole,
        initializer=initializer4,
        seed=args.seed,
        factor_basis=factor_basis,
        factor_basis_identity=factor_basis_record,
        thresholds=thresholds,
        cpp_evaluator=cpp_evaluator,
        migration=migration,
        quiet=args.quiet,
    )
    paths = _candidate_paths(
        args.candidate, args.output_dir, robustness=robustness
    )
    if not result.eligible:
        failure = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": ROBUSTNESS_FAILURE_KIND if robustness else FAILURE_KIND,
            "profileId": PROFILE_ID,
            "candidateId": args.candidate,
            "trainingPurpose": (
                "robustness-training" if robustness else "primary-training"
            ),
            "createdUtc": _utc_now(),
            "reason": "no QAT checkpoint passed every deployment-health gate",
            "recipe": CANDIDATE_RECIPES[args.candidate],
            "trainingSeed": args.seed,
            "factorSeed": args.factor_seed,
            "history": list(result.history),
            "basis": result.basis_identity,
            "initializerMigration": migration,
            "inputs": {
                "plan": _identity(args.plan),
                **plan["identities"],
            },
            "informationBoundary": {
                "targetFieldNames": list(TARGET_FIELDS_DECODED_PER_ROW),
                "trainTargetRowsDecoded": dataset.target_rows_decoded,
                "trainTargetsDecoded": dataset.target_fields_decoded,
                "trainHandcraftedScoresComputed": dataset.handcrafted_scores_computed,
                "trainResidualTargetsComputed": dataset.residual_targets_computed,
                "validationTargetsDecoded": 0,
                "validationHandcraftedScoresComputed": 0,
                "validationResidualTargetsComputed": 0,
                "validationTargetsDecodedBeforeHealthPass": 0,
                "heldOutRowsWithheld": dataset.heldout_rows,
                "heldOutTargetFieldsDecoded": 0,
                "heldOutMetricsComputed": False,
            },
        }
        _exclusive_json(paths["failure"], failure)
        return failure
    assert result.network is not None
    assert result.canonical_float is not None
    assert result.optimizer_shadow is not None
    if validation_cache is None:
        raise AssertionError("eligible checkpoint did not trigger lazy validation")
    validation_dataset = validation_cache
    validation_indices = validation_dataset.indices(1)
    i0_predictions = _predict_network(
        initializer4, validation_dataset.features, validation_indices
    )
    zero_predictions = np.zeros(validation_indices.size, dtype=np.int32)
    i0_metrics = _common_metrics(validation_dataset, 1, i0_predictions)
    zero_metrics = _common_metrics(validation_dataset, 1, zero_predictions)
    network_payload = result.network.to_bytes()
    canonical_payload = _float_checkpoint_bytes(result.canonical_float)
    shadow_payload = _float_checkpoint_bytes(result.optimizer_shadow)
    manifest_path = paths["manifest"]
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": CANDIDATE_KIND,
        "profileId": PROFILE_ID,
        "candidateId": args.candidate,
        "trainingPurpose": (
            "robustness-training" if robustness else "primary-training"
        ),
        "createdUtc": _utc_now(),
        "recipe": CANDIDATE_RECIPES[args.candidate],
        "trainingSeed": args.seed,
        "factorSeed": args.factor_seed,
        "factorBasis": result.basis_identity,
        "selectedEpoch": result.selected_epoch,
        "commonValidation": result.common_validation,
        "i0Validation": i0_metrics,
        "zeroResidualValidation": zero_metrics,
        "health": result.health,
        "history": list(result.history),
        "initializerMigration": migration,
        "network": _payload_identity(paths["network"], network_payload),
        "canonicalDeploymentFloat": _payload_identity(
            paths["canonical"], canonical_payload
        ),
        "optimizerShadow": _payload_identity(paths["shadow"], shadow_payload),
        "inputs": {
            "plan": _identity(args.plan),
            **plan["identities"],
        },
        "informationBoundary": {
            "targetFieldNames": list(TARGET_FIELDS_DECODED_PER_ROW),
            "trainTargetRowsDecoded": dataset.target_rows_decoded,
            "trainTargetsDecoded": dataset.target_fields_decoded,
            "validationTargetRowsDecoded": validation_dataset.target_rows_decoded,
            "validationTargetsDecoded": validation_dataset.target_fields_decoded,
            "trainHandcraftedScoresComputed": dataset.handcrafted_scores_computed,
            "trainResidualTargetsComputed": dataset.residual_targets_computed,
            "validationHandcraftedScoresComputed": (
                validation_dataset.handcrafted_scores_computed
            ),
            "validationResidualTargetsComputed": (
                validation_dataset.residual_targets_computed
            ),
            "validationTargetsDecodedBeforeFirstHealthPass": 0,
            "targetFieldsDecodedBySplit": {
                "train": dataset.target_fields_decoded,
                "validation": validation_dataset.target_fields_decoded,
                "heldOut": 0,
            },
            "heldOutRowsWithheld": dataset.heldout_rows,
            "heldOutTargetFieldsDecoded": 0,
            "heldOutMetricsComputed": False,
            "matchResultsAccessed": False,
        },
    }
    manifest_payload = _canonical_json(manifest)
    _publish_bundle(
        paths,
        {
            "network": network_payload,
            "canonical": canonical_payload,
            "shadow": shadow_payload,
            "manifest": manifest_payload,
        },
        manifest,
    )
    return manifest


def _run(args: argparse.Namespace) -> None:
    plan, _profile = _verify_plan(args.plan)
    candidates = [args.candidate] if args.candidate else list(CANDIDATES)
    for candidate in candidates:
        command = plan["commands"][candidate]
        environment = os.environ.copy()
        environment.update(command["environment"])
        completed = subprocess.run(command["argv"], env=environment, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f"generation-5 worker {candidate} exited {completed.returncode}"
            )


def _relative_improvement(candidate: float, baseline: float) -> float:
    if baseline <= 0.0:
        return math.inf if candidate < baseline else 0.0
    return (baseline - candidate) / baseline


def _planned_split_rows(plan: Mapping[str, Any]) -> dict[str, int]:
    audit = _mapping(plan.get("targetOpaqueCorpusAudit"), "plan corpus audit")
    phase_roots = _mapping(audit.get("phaseRoots"), "plan split phase roots")
    result: dict[str, int] = {}
    for split in SPLITS:
        phases = _mapping(phase_roots.get(split), f"{split} phase roots")
        if set(phases) != set(PHASES):
            raise ValueError(f"{split} phase-root inventory changed")
        roots = 0
        for phase in PHASES:
            value = phases[phase]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{split}/{phase} root count is invalid")
            roots += value
        result[split] = roots * 4
    if sum(result.values()) != int(audit.get("rows", -1)):
        raise ValueError("planned split rows do not cover the corpus")
    return result


def _verify_finite_tree(value: Any, label: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{label} contains a nonfinite number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _verify_finite_tree(item, f"{label}.{key}")
        return
    if isinstance(value, Sequence):
        for index, item in enumerate(value):
            _verify_finite_tree(item, f"{label}[{index}]")
        return
    raise ValueError(f"{label} contains an unsupported value")


def _verify_metric_shape(value: Any, label: str) -> dict[str, Any]:
    metric = _mapping(value, label)
    if set(metric) != {"huberLoss", "cpMae", "phase"}:
        raise ValueError(f"{label} field inventory changed")
    for key in ("huberLoss", "cpMae"):
        if _number(metric[key], f"{label}.{key}") < 0.0:
            raise ValueError(f"{label}.{key} is negative")
    phases = _mapping(metric["phase"], f"{label}.phase")
    if set(phases) != set(PHASES):
        raise ValueError(f"{label} phase inventory changed")
    for phase in PHASES:
        cell = _mapping(phases[phase], f"{label}.{phase}")
        if set(cell) != {"rows", "groups", "huberLoss", "cpMae"}:
            raise ValueError(f"{label}.{phase} field inventory changed")
        for count in ("rows", "groups"):
            if (
                isinstance(cell[count], bool)
                or not isinstance(cell[count], int)
                or cell[count] <= 0
            ):
                raise ValueError(f"{label}.{phase}.{count} is invalid")
        for key in ("huberLoss", "cpMae"):
            if _number(cell[key], f"{label}.{phase}.{key}") < 0.0:
                raise ValueError(f"{label}.{phase}.{key} is negative")
    return dict(metric)


def _expected_prehealth_boundary(
    split_rows: Mapping[str, int]
) -> dict[str, Any]:
    train_rows = split_rows["train"]
    return {
        "targetFieldNames": list(TARGET_FIELDS_DECODED_PER_ROW),
        "trainTargetRowsDecoded": train_rows,
        "trainTargetFieldsDecoded": train_rows
        * len(TARGET_FIELDS_DECODED_PER_ROW),
        "trainHandcraftedScoresComputed": train_rows,
        "trainResidualTargetsComputed": train_rows,
        "validationTargetFieldsDecoded": 0,
        "validationHandcraftedScoresComputed": 0,
        "validationResidualTargetsComputed": 0,
        "heldOutTargetFieldsDecoded": 0,
    }


def _verify_history(
    value: Any,
    *,
    candidate: str,
    split_rows: Mapping[str, int],
    successful: bool,
    selected_epoch: int | None,
    selected_metrics: Any,
    selected_health: Any,
) -> None:
    if not isinstance(value, list) or len(value) != EPOCHS:
        raise ValueError(f"{candidate} history does not contain {EPOCHS} epochs")
    expected_before = _expected_prehealth_boundary(split_rows)
    previous_steps = -1
    passing: list[Mapping[str, Any]] = []
    for ordinal, raw in enumerate(value, 1):
        row = _mapping(raw, f"{candidate} history epoch {ordinal}")
        qat = ordinal >= FIRST_QAT_EPOCH
        if (
            row.get("epoch") != ordinal
            or row.get("quantizationAware") is not qat
            or row.get("healthCheckedBeforeValidation") is not qat
            or row.get("validationTargetsAccessed") not in (True, False)
        ):
            raise ValueError(f"{candidate} epoch {ordinal} schedule changed")
        steps = row.get("optimizerSteps")
        if isinstance(steps, bool) or not isinstance(steps, int) or steps <= previous_steps:
            raise ValueError(f"{candidate} optimizer history is inconsistent")
        previous_steps = steps
        if _number(row.get("meanBatchLoss"), "mean batch loss") < 0.0:
            raise ValueError(f"{candidate} epoch loss is negative")
        pairs = row.get("rankingPairs")
        if isinstance(pairs, bool) or not isinstance(pairs, int) or pairs < 0:
            raise ValueError(f"{candidate} ranking-pair count is invalid")
        if row.get("activationSettings") != {
            "penaltyWeight": ACTIVATION_PENALTY,
            "targetActiveFraction": ACTIVATION_TARGET,
            "temperature": ACTIVATION_TEMPERATURE,
        }:
            raise ValueError(f"{candidate} activation history changed")
        if row.get("targetAccessBeforeHealth") != expected_before:
            raise ValueError(f"{candidate} target-access chronology changed")
        if not qat:
            if (
                "deploymentHealth" in row
                or "commonValidation" in row
                or row["validationTargetsAccessed"]
            ):
                raise ValueError(f"{candidate} accessed validation before QAT")
            continue
        health = _mapping(
            row.get("deploymentHealth"), f"{candidate} epoch {ordinal} health"
        )
        if health.get("targetFieldsDecoded") != 0 or health.get("passed") not in (
            True,
            False,
        ):
            raise ValueError(f"{candidate} epoch {ordinal} health is malformed")
        if health["passed"]:
            if not row["validationTargetsAccessed"]:
                raise ValueError(f"{candidate} withheld health-passing validation")
            expected_after = {
                "validationTargetRowsDecodedAfterHealth": split_rows["validation"],
                "validationTargetFieldsDecodedAfterHealth": split_rows["validation"]
                * len(TARGET_FIELDS_DECODED_PER_ROW),
                "validationHandcraftedScoresComputedAfterHealth": split_rows[
                    "validation"
                ],
                "validationResidualTargetsComputedAfterHealth": split_rows[
                    "validation"
                ],
            }
            for key, expected in expected_after.items():
                if row.get(key) != expected:
                    raise ValueError(
                        f"{candidate} epoch {ordinal} target counter changed"
                    )
            _verify_metric_shape(
                row.get("commonValidation"),
                f"{candidate} epoch {ordinal} validation",
            )
            passing.append(row)
        elif row["validationTargetsAccessed"] or row.get("commonValidation") is not None:
            raise ValueError(f"{candidate} accessed validation after failed health")
    if successful:
        if not passing or selected_epoch is None:
            raise ValueError(f"{candidate} has no selectable checkpoint")
        best = min(
            passing,
            key=lambda row: (
                float(row["commonValidation"]["huberLoss"]),
                int(row["epoch"]),
            ),
        )
        if (
            best["epoch"] != selected_epoch
            or best["commonValidation"] != selected_metrics
            or best["deploymentHealth"] != selected_health
        ):
            raise ValueError(f"{candidate} selected checkpoint is inconsistent")
    elif passing:
        raise ValueError(f"{candidate} failure hides a health-passing checkpoint")


def _verify_inputs(
    value: Any,
    *,
    candidate: str,
    plan: Mapping[str, Any],
    plan_path: Path,
) -> None:
    inputs = _mapping(value, f"{candidate} inputs")
    expected = {"plan": _identity(plan_path), **plan["identities"]}
    if inputs != expected:
        raise ValueError(f"{candidate} input identities differ from the plan")


def _verify_failure_record(
    failure: Mapping[str, Any],
    *,
    candidate: str,
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
    plan_path: Path,
    split_rows: Mapping[str, int],
    robustness: bool = False,
) -> None:
    expected_fields = {
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
    if set(failure) != expected_fields:
        raise ValueError(f"{candidate} failure field inventory changed")
    if (
        failure.get("schemaVersion") != SCHEMA_VERSION
        or failure.get("kind")
        != (ROBUSTNESS_FAILURE_KIND if robustness else FAILURE_KIND)
        or failure.get("profileId") != PROFILE_ID
        or failure.get("candidateId") != candidate
        or failure.get("trainingPurpose")
        != ("robustness-training" if robustness else "primary-training")
        or failure.get("reason")
        != "no QAT checkpoint passed every deployment-health gate"
        or failure.get("recipe") != CANDIDATE_RECIPES[candidate]
        or failure.get("trainingSeed")
        != _candidate_seed(
            profile,
            candidate,
            "robustness-training" if robustness else "candidate-training",
        )
        or failure.get("factorSeed")
        != _candidate_seed(profile, candidate, "factor-basis")
        or failure.get("basis") != plan["factorBases"][candidate]
        or failure.get("initializerMigration") != plan["initializerMigration"]
    ):
        raise ValueError(f"{candidate} failure identity changed")
    _verify_inputs(
        failure.get("inputs"),
        candidate=candidate,
        plan=plan,
        plan_path=plan_path,
    )
    boundary = _mapping(failure.get("informationBoundary"), "failure boundary")
    expected_boundary = {
        "targetFieldNames": list(TARGET_FIELDS_DECODED_PER_ROW),
        "trainTargetRowsDecoded": split_rows["train"],
        "trainTargetsDecoded": split_rows["train"]
        * len(TARGET_FIELDS_DECODED_PER_ROW),
        "trainHandcraftedScoresComputed": split_rows["train"],
        "trainResidualTargetsComputed": split_rows["train"],
        "validationTargetsDecoded": 0,
        "validationHandcraftedScoresComputed": 0,
        "validationResidualTargetsComputed": 0,
        "validationTargetsDecodedBeforeHealthPass": 0,
        "heldOutRowsWithheld": split_rows["heldOut"],
        "heldOutTargetFieldsDecoded": 0,
        "heldOutMetricsComputed": False,
    }
    if boundary != expected_boundary:
        raise ValueError(f"{candidate} failure target counters changed")
    _verify_history(
        failure.get("history"),
        candidate=candidate,
        split_rows=split_rows,
        successful=False,
        selected_epoch=None,
        selected_metrics=None,
        selected_health=None,
    )


def _verify_manifest_record(
    manifest: Mapping[str, Any],
    *,
    candidate: str,
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
    plan_path: Path,
    output_dir: Path,
    split_rows: Mapping[str, int],
    robustness: bool = False,
) -> QuantizedNetwork:
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "candidateId",
        "trainingPurpose",
        "createdUtc",
        "recipe",
        "trainingSeed",
        "factorSeed",
        "factorBasis",
        "selectedEpoch",
        "commonValidation",
        "i0Validation",
        "zeroResidualValidation",
        "health",
        "history",
        "initializerMigration",
        "network",
        "canonicalDeploymentFloat",
        "optimizerShadow",
        "inputs",
        "informationBoundary",
    }
    if set(manifest) != expected_fields:
        raise ValueError(f"{candidate} manifest field inventory changed")
    if (
        manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("kind") != CANDIDATE_KIND
        or manifest.get("profileId") != PROFILE_ID
        or manifest.get("candidateId") != candidate
        or manifest.get("trainingPurpose")
        != ("robustness-training" if robustness else "primary-training")
        or manifest.get("recipe") != CANDIDATE_RECIPES[candidate]
        or manifest.get("trainingSeed")
        != _candidate_seed(
            profile,
            candidate,
            "robustness-training" if robustness else "candidate-training",
        )
        or manifest.get("factorSeed")
        != _candidate_seed(profile, candidate, "factor-basis")
        or manifest.get("factorBasis") != plan["factorBases"][candidate]
        or manifest.get("initializerMigration") != plan["initializerMigration"]
    ):
        raise ValueError(f"{candidate} manifest identity changed")
    _verify_inputs(
        manifest.get("inputs"),
        candidate=candidate,
        plan=plan,
        plan_path=plan_path,
    )
    paths = _candidate_paths(
        candidate, output_dir, robustness=robustness
    )
    bundle = _candidate_bundle(
        candidate, output_dir, robustness=robustness
    )
    expected_names = {
        paths["network"].name,
        paths["canonical"].name,
        paths["shadow"].name,
        paths["manifest"].name,
    }
    if not bundle.is_dir() or {item.name for item in bundle.iterdir()} != expected_names:
        raise ValueError(f"{candidate} bundle inventory changed")
    identity_bindings = (
        ("network", "network"),
        ("canonicalDeploymentFloat", "canonical"),
        ("optimizerShadow", "shadow"),
    )
    for manifest_key, path_key in identity_bindings:
        record = _mapping(manifest.get(manifest_key), f"{candidate} {manifest_key}")
        if Path(str(record.get("path"))) != paths[path_key]:
            raise ValueError(f"{candidate} {manifest_key} path changed")
        _verify_identity(record, f"{candidate} {manifest_key}")
    network = QuantizedNetwork.read(paths["network"])
    if (
        network.architecture != ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
        or network.to_bytes() != paths["network"].read_bytes()
    ):
        raise ValueError(f"{candidate} network artifact changed")
    canonical = _read_arch4_float(paths["canonical"].read_bytes())
    shadow = _read_arch4_float(paths["shadow"].read_bytes())
    if (
        canonical.quantize(ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL).to_bytes()
        != network.to_bytes()
        or shadow.quantize(ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL).to_bytes()
        != network.to_bytes()
    ):
        raise ValueError(f"{candidate} float artifacts do not bind the network")
    selected_epoch = manifest.get("selectedEpoch")
    if (
        isinstance(selected_epoch, bool)
        or not isinstance(selected_epoch, int)
        or selected_epoch < FIRST_QAT_EPOCH
        or selected_epoch > EPOCHS
    ):
        raise ValueError(f"{candidate} selected epoch is invalid")
    metrics = _verify_metric_shape(
        manifest.get("commonValidation"), f"{candidate} common validation"
    )
    _verify_metric_shape(manifest.get("i0Validation"), f"{candidate} I0")
    _verify_metric_shape(
        manifest.get("zeroResidualValidation"), f"{candidate} zero residual"
    )
    health = _mapping(manifest.get("health"), f"{candidate} health")
    if health.get("passed") is not True or health.get("targetFieldsDecoded") != 0:
        raise ValueError(f"{candidate} selected health did not pass")
    _verify_finite_tree(health, f"{candidate} health")
    _verify_history(
        manifest.get("history"),
        candidate=candidate,
        split_rows=split_rows,
        successful=True,
        selected_epoch=selected_epoch,
        selected_metrics=metrics,
        selected_health=health,
    )
    boundary = _mapping(
        manifest.get("informationBoundary"), f"{candidate} boundary"
    )
    expected_boundary = {
        "targetFieldNames": list(TARGET_FIELDS_DECODED_PER_ROW),
        "trainTargetRowsDecoded": split_rows["train"],
        "trainTargetsDecoded": split_rows["train"]
        * len(TARGET_FIELDS_DECODED_PER_ROW),
        "validationTargetRowsDecoded": split_rows["validation"],
        "validationTargetsDecoded": split_rows["validation"]
        * len(TARGET_FIELDS_DECODED_PER_ROW),
        "trainHandcraftedScoresComputed": split_rows["train"],
        "trainResidualTargetsComputed": split_rows["train"],
        "validationHandcraftedScoresComputed": split_rows["validation"],
        "validationResidualTargetsComputed": split_rows["validation"],
        "validationTargetsDecodedBeforeFirstHealthPass": 0,
        "targetFieldsDecodedBySplit": {
            "train": split_rows["train"] * len(TARGET_FIELDS_DECODED_PER_ROW),
            "validation": split_rows["validation"]
            * len(TARGET_FIELDS_DECODED_PER_ROW),
            "heldOut": 0,
        },
        "heldOutRowsWithheld": split_rows["heldOut"],
        "heldOutTargetFieldsDecoded": 0,
        "heldOutMetricsComputed": False,
        "matchResultsAccessed": False,
    }
    if boundary != expected_boundary:
        raise ValueError(f"{candidate} target counters changed")
    return network


def _selection_verification_context(
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
    split_rows: Mapping[str, int],
) -> dict[str, Any]:
    """Load the exact frozen data and baselines used to verify candidates."""

    identities = _mapping(plan["identities"], "plan identities")
    corpus_path = Path(str(identities["corpus"]["path"]))
    cpp_evaluator = Path(str(identities["cppEvaluator"]["path"]))
    whole = _load_feature_corpus(corpus_path)
    validation = _load_decision_splits(
        corpus_path,
        hce_evaluator=cpp_evaluator,
        decode_splits=("validation",),
    )
    if (
        validation.target_rows_decoded != split_rows["validation"]
        or validation.target_fields_decoded
        != split_rows["validation"] * len(TARGET_FIELDS_DECODED_PER_ROW)
        or validation.target_fields_decoded_by_split.get("heldOut") != 0
    ):
        raise ValueError("selection validation target counters changed")
    initializer3 = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    initializer4 = _migrate_initializer(initializer3)
    migration = _migration_parity(initializer3, initializer4, whole)
    if migration != plan.get("initializerMigration"):
        raise ValueError("selection initializer migration changed")
    validation_indices = validation.indices(SPLITS["validation"])
    return {
        "whole": whole,
        "validation": validation,
        "validationIndices": validation_indices,
        "initializer": initializer4,
        "migration": migration,
        "cppEvaluator": cpp_evaluator,
        "thresholds": _mapping(
            profile.get("deploymentHealthGate"),
            "deployment health thresholds",
        ),
        "i0Validation": _common_metrics(
            validation,
            SPLITS["validation"],
            _predict_network(initializer4, validation.features, validation_indices),
        ),
        "zeroResidualValidation": _common_metrics(
            validation,
            SPLITS["validation"],
            np.zeros(validation_indices.size, dtype=np.int32),
        ),
    }


def _verify_recomputed_candidate_selection(
    *,
    candidate: str,
    manifest: Mapping[str, Any],
    network: QuantizedNetwork,
    context: Mapping[str, Any],
) -> None:
    """Recompute the metrics and health that selection treats as authoritative."""

    validation = context["validation"]
    validation_indices = context["validationIndices"]
    recomputed = _common_metrics(
        validation,
        SPLITS["validation"],
        _predict_network(network, validation.features, validation_indices),
    )
    if (
        manifest.get("commonValidation") != recomputed
        or manifest.get("i0Validation") != context["i0Validation"]
        or manifest.get("zeroResidualValidation")
        != context["zeroResidualValidation"]
    ):
        raise ValueError(f"{candidate} validation metrics are stale")
    recomputed_health, _ = _runtime_health(
        network,
        context["whole"],
        mapped_initializer=context["initializer"],
        migration=context["migration"],
        cpp_evaluator=context["cppEvaluator"],
        thresholds=context["thresholds"],
        require_cpp=True,
    )
    if manifest.get("health") != recomputed_health:
        raise ValueError(f"{candidate} deployment health is stale")


def _verify_completed_primary_bundle(
    *,
    candidate: str,
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
    plan_path: Path,
    output_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Run the complete selection verifier and snapshot its immutable bundle."""

    paths = _candidate_paths(candidate, output_dir)
    bundle = _candidate_bundle(candidate, output_dir)
    if paths["failure"].exists():
        raise ValueError(f"{candidate} failure coexists with a candidate bundle")
    artifact_keys = ("network", "canonical", "shadow", "manifest")
    if any(not paths[key].is_file() for key in artifact_keys):
        raise ValueError(f"{candidate} committed bundle is partial or non-regular")
    before = {key: _identity(paths[key]) for key in artifact_keys}
    manifest = _load_json(paths["manifest"], f"{candidate} manifest")
    split_rows = _planned_split_rows(plan)
    network = _verify_manifest_record(
        manifest,
        candidate=candidate,
        plan=plan,
        profile=profile,
        plan_path=plan_path,
        output_dir=output_dir,
        split_rows=split_rows,
    )
    context = _selection_verification_context(plan, profile, split_rows)
    _verify_recomputed_candidate_selection(
        candidate=candidate,
        manifest=manifest,
        network=network,
        context=context,
    )
    after = {key: _identity(paths[key]) for key in artifact_keys}
    if after != before or not bundle.is_dir() or {
        item.name for item in bundle.iterdir()
    } != {paths[key].name for key in artifact_keys}:
        raise ValueError(
            f"{candidate} bundle changed during completed-output verification"
        )
    return after


def _verify_completed_primary_failure(
    *,
    candidate: str,
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
    plan_path: Path,
    output_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Run selection's terminal-failure verifier and snapshot the exact JSON."""

    paths = _candidate_paths(candidate, output_dir)
    success_keys = ("network", "canonical", "shadow", "manifest")
    bundle = _candidate_bundle(candidate, output_dir)
    if bundle.exists() or any(paths[key].exists() for key in success_keys):
        raise ValueError(f"{candidate} failure coexists with success output")
    if not paths["failure"].is_file():
        raise ValueError(f"{candidate} terminal failure is missing or non-regular")
    before = _identity(paths["failure"])
    failure = _load_json(paths["failure"], f"{candidate} failure")
    _verify_failure_record(
        failure,
        candidate=candidate,
        plan=plan,
        profile=profile,
        plan_path=plan_path,
        split_rows=_planned_split_rows(plan),
    )
    after = _identity(paths["failure"])
    if (
        after != before
        or bundle.exists()
        or any(paths[key].exists() for key in success_keys)
    ):
        raise ValueError(
            f"{candidate} failure state changed during completed-output verification"
        )
    return {"failure": after}


def _require_postselection_outputs_absent(plan: Mapping[str, Any]) -> None:
    outputs = _mapping(plan.get("outputs"), "plan outputs")
    for key in (
        "robustnessBundle",
        "robustnessClaim",
        "robustnessFailure",
        "robustnessSeal",
        "offlineAccessClaim",
        "offlineReport",
    ):
        path = _resolve(Path(str(outputs[key])))
        if path.exists():
            raise ValueError(f"unexpected preselection output exists: {key}={path}")


def _select(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = _resolve(args.plan)
    plan, profile = _verify_plan(plan_path)
    output_dir = _resolve(Path(str(plan["outputDirectory"])))
    selection_path = _resolve(Path(str(plan["selectionPath"])))
    if selection_path.exists():
        raise FileExistsError(selection_path)
    _require_postselection_outputs_absent(plan)
    split_rows = _planned_split_rows(plan)
    manifests: dict[str, dict[str, Any]] = {}
    failures: dict[str, dict[str, Any]] = {}
    networks: dict[str, QuantizedNetwork] = {}
    for candidate in CANDIDATES:
        paths = _candidate_paths(candidate, output_dir)
        manifest_exists = paths["manifest"].exists()
        failure_exists = paths["failure"].exists()
        if _candidate_claim_path(candidate, output_dir).exists():
            raise RuntimeError(f"candidate {candidate} still has an active claim")
        if manifest_exists and failure_exists:
            raise ValueError(f"candidate {candidate} has both manifest and failure")
        if manifest_exists:
            if not paths["manifest"].is_file():
                raise ValueError(f"{candidate} manifest path is not a file")
            manifest = _load_json(paths["manifest"], f"{candidate} manifest")
            networks[candidate] = _verify_manifest_record(
                manifest,
                candidate=candidate,
                plan=plan,
                profile=profile,
                plan_path=plan_path,
                output_dir=output_dir,
                split_rows=split_rows,
            )
            manifests[candidate] = manifest
        elif failure_exists:
            if not paths["failure"].is_file():
                raise ValueError(f"{candidate} failure path is not a file")
            failure = _load_json(paths["failure"], f"{candidate} failure")
            _verify_failure_record(
                failure,
                candidate=candidate,
                plan=plan,
                profile=profile,
                plan_path=plan_path,
                split_rows=split_rows,
            )
            if _candidate_bundle(candidate, output_dir).exists():
                raise ValueError(f"{candidate} failure coexists with a bundle")
            failures[candidate] = failure
        else:
            raise FileNotFoundError(f"candidate {candidate} is unfinished")

    # Recompute every selectable aggregate from the pinned artifacts and the
    # authorized validation split.  Held-out rows are routed away before JSON
    # decoding by _load_decision_splits.
    if manifests:
        verification_context = _selection_verification_context(
            plan, profile, split_rows
        )
        for candidate, manifest in manifests.items():
            _verify_recomputed_candidate_selection(
                candidate=candidate,
                manifest=manifest,
                network=networks[candidate],
                context=verification_context,
            )

        i0_values = [
            manifest["i0Validation"] for manifest in manifests.values()
        ]
        zero_values = [
            manifest["zeroResidualValidation"] for manifest in manifests.values()
        ]
        if any(value != i0_values[0] for value in i0_values[1:]) or any(
            value != zero_values[0] for value in zero_values[1:]
        ):
            raise ValueError("candidate common baselines differ")

    gate = _mapping(
        _mapping(profile.get("validationSelection"), "validation selection").get(
            "eligibility"
        ),
        "validation eligibility",
    )
    evaluations: dict[str, Any] = {}
    eligible: list[str] = []
    for candidate in CANDIDATES:
        if candidate not in manifests:
            evaluations[candidate] = {
                "eligible": False,
                "reason": failures[candidate].get("reason"),
            }
            continue
        manifest = manifests[candidate]
        metrics = manifest["commonValidation"]
        i0 = manifest["i0Validation"]
        zero = manifest["zeroResidualValidation"]
        checks = {
            "minimumRelativeHuberImprovementOverI0": _relative_improvement(
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
                for phase in PHASES
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
    if not eligible:
        closure = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": SELECTION_KIND,
            "profileId": PROFILE_ID,
            "createdUtc": _utc_now(),
            "plan": _identity(plan_path),
            "status": "closed-no-eligible-candidate",
            "winner": None,
            "evaluations": evaluations,
            "heldOutTargetFieldsDecoded": 0,
            "validationTargetFieldsDecodedForVerification": (
                split_rows["validation"] * len(TARGET_FIELDS_DECODED_PER_ROW)
                if manifests
                else 0
            ),
            "robustnessAuthorized": False,
            "outputs": plan["outputs"],
        }
        _exclusive_json(selection_path, closure)
        return closure
    minimum = min(
        float(manifests[candidate]["commonValidation"]["huberLoss"])
        for candidate in eligible
    )
    tie_relative = float(
        _mapping(profile["validationSelection"], "selection")["tieRelativeLoss"]
    )
    tied = [
        candidate
        for candidate in eligible
        if (
            float(manifests[candidate]["commonValidation"]["huberLoss"])
            - minimum
        )
        / max(minimum, 1e-12)
        <= tie_relative
    ]
    winner = next(candidate for candidate in TIE_PRIORITY if candidate in tied)
    seal = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SELECTION_KIND,
        "profileId": PROFILE_ID,
        "createdUtc": _utc_now(),
        "plan": _identity(plan_path),
        "status": "selected-validation-winner",
        "winner": winner,
        "winnerManifest": _identity(_candidate_paths(winner, output_dir)["manifest"]),
        "winnerNetwork": _identity(_candidate_paths(winner, output_dir)["network"]),
        "winnerCanonicalDeploymentFloat": _identity(
            _candidate_paths(winner, output_dir)["canonical"]
        ),
        "evaluations": evaluations,
        "tieCandidates": tied,
        "tiePriority": list(TIE_PRIORITY),
        "heldOutTargetFieldsDecoded": 0,
        "validationTargetFieldsDecodedForVerification": (
            split_rows["validation"] * len(TARGET_FIELDS_DECODED_PER_ROW)
        ),
        "robustnessAuthorized": True,
        "robustnessCommand": plan["robustnessCommands"][winner],
        "outputs": plan["outputs"],
    }
    _exclusive_json(selection_path, seal)
    return seal


def _verify_selection(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    selection_path = _resolve(path)
    selection = _load_json(selection_path, "generation-5 selection")
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "createdUtc",
        "plan",
        "status",
        "winner",
        "winnerManifest",
        "winnerNetwork",
        "winnerCanonicalDeploymentFloat",
        "evaluations",
        "tieCandidates",
        "tiePriority",
        "heldOutTargetFieldsDecoded",
        "validationTargetFieldsDecodedForVerification",
        "robustnessAuthorized",
        "robustnessCommand",
        "outputs",
    }
    if set(selection) != expected_fields:
        raise ValueError("generation-5 selected-seal field inventory changed")
    if (
        selection.get("schemaVersion") != SCHEMA_VERSION
        or selection.get("kind") != SELECTION_KIND
        or selection.get("profileId") != PROFILE_ID
        or selection.get("status") != "selected-validation-winner"
        or selection.get("robustnessAuthorized") is not True
        or selection.get("heldOutTargetFieldsDecoded") != 0
    ):
        raise ValueError("generation-5 selection did not authorize robustness")
    try:
        datetime.fromisoformat(
            str(selection.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("selection createdUtc is invalid") from error
    plan_path = _verify_identity(selection.get("plan"), "selection plan")
    plan, profile = _verify_plan(plan_path)
    if selection_path != _resolve(Path(str(plan["selectionPath"]))):
        raise ValueError("selection is outside its frozen namespace")
    if selection.get("outputs") != plan.get("outputs"):
        raise ValueError("selection post-selection outputs changed")

    split_rows = _planned_split_rows(plan)
    output_dir = _resolve(Path(str(plan["outputDirectory"])))
    manifests: dict[str, dict[str, Any]] = {}
    failures: dict[str, dict[str, Any]] = {}
    networks: dict[str, QuantizedNetwork] = {}
    for candidate in CANDIDATES:
        paths = _candidate_paths(candidate, output_dir)
        if _candidate_claim_path(candidate, output_dir).exists():
            raise RuntimeError(f"candidate {candidate} still has an active claim")
        if paths["manifest"].exists() == paths["failure"].exists():
            raise ValueError(f"candidate {candidate} has ambiguous completion state")
        if paths["manifest"].exists():
            manifest = _load_json(paths["manifest"], f"{candidate} manifest")
            networks[candidate] = _verify_manifest_record(
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
            failure = _load_json(paths["failure"], f"{candidate} failure")
            _verify_failure_record(
                failure,
                candidate=candidate,
                plan=plan,
                profile=profile,
                plan_path=plan_path,
                split_rows=split_rows,
            )
            failures[candidate] = failure

    identities = _mapping(plan["identities"], "plan identities")
    corpus = Path(str(identities["corpus"]["path"]))
    cpp_evaluator = Path(str(identities["cppEvaluator"]["path"]))
    whole = _load_feature_corpus(corpus)
    validation = _load_decision_splits(
        corpus,
        hce_evaluator=cpp_evaluator,
        decode_splits=("validation",),
    )
    initializer3 = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    initializer4 = _migrate_initializer(initializer3)
    migration = _migration_parity(initializer3, initializer4, whole)
    if migration != plan.get("initializerMigration"):
        raise ValueError("selection initializer migration changed")
    validation_indices = validation.indices(SPLITS["validation"])
    i0 = _common_metrics(
        validation,
        SPLITS["validation"],
        _predict_network(initializer4, validation.features, validation_indices),
    )
    zero = _common_metrics(
        validation,
        SPLITS["validation"],
        np.zeros(validation_indices.size, dtype=np.int32),
    )
    thresholds = _mapping(
        profile.get("deploymentHealthGate"), "deployment health thresholds"
    )
    gate = _mapping(
        _mapping(profile.get("validationSelection"), "validation selection").get(
            "eligibility"
        ),
        "validation eligibility",
    )
    evaluations: dict[str, Any] = {}
    eligible: list[str] = []
    for candidate in CANDIDATES:
        if candidate in failures:
            evaluations[candidate] = {
                "eligible": False,
                "reason": failures[candidate].get("reason"),
            }
            continue
        manifest = manifests[candidate]
        predictions = _predict_network(
            networks[candidate], validation.features, validation_indices
        )
        metrics = _common_metrics(
            validation, SPLITS["validation"], predictions
        )
        health, _whole_predictions = _runtime_health(
            networks[candidate],
            whole,
            mapped_initializer=initializer4,
            migration=migration,
            cpp_evaluator=cpp_evaluator,
            thresholds=thresholds,
            require_cpp=True,
        )
        if (
            manifest.get("commonValidation") != metrics
            or manifest.get("i0Validation") != i0
            or manifest.get("zeroResidualValidation") != zero
            or manifest.get("health") != health
        ):
            raise ValueError(f"{candidate} selection inputs are stale")
        checks = {
            "minimumRelativeHuberImprovementOverI0": _relative_improvement(
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
                for phase in PHASES
            ),
            "deploymentHealth": bool(health["passed"]),
        }
        evaluations[candidate] = {
            "eligible": all(checks.values()),
            "checks": checks,
            "commonValidation": metrics,
        }
        if all(checks.values()):
            eligible.append(candidate)
    if not eligible:
        raise ValueError("selection seal claims a winner but none is eligible")
    minimum = min(
        float(manifests[candidate]["commonValidation"]["huberLoss"])
        for candidate in eligible
    )
    tie_relative = float(
        _mapping(profile["validationSelection"], "selection")["tieRelativeLoss"]
    )
    tied = [
        candidate
        for candidate in eligible
        if (
            float(manifests[candidate]["commonValidation"]["huberLoss"])
            - minimum
        )
        / max(minimum, 1e-12)
        <= tie_relative
    ]
    winner = next(candidate for candidate in TIE_PRIORITY if candidate in tied)
    winner_paths = _candidate_paths(winner, output_dir)
    expected_values = {
        "winner": winner,
        "winnerManifest": _identity(winner_paths["manifest"]),
        "winnerNetwork": _identity(winner_paths["network"]),
        "winnerCanonicalDeploymentFloat": _identity(winner_paths["canonical"]),
        "evaluations": evaluations,
        "tieCandidates": tied,
        "tiePriority": list(TIE_PRIORITY),
        "validationTargetFieldsDecodedForVerification": (
            split_rows["validation"] * len(TARGET_FIELDS_DECODED_PER_ROW)
        ),
        "robustnessCommand": plan["robustnessCommands"][winner],
    }
    for key, expected in expected_values.items():
        if selection.get(key) != expected:
            raise ValueError(f"selection {key} differs from recomputation")
    return selection, plan, profile


def _robustness_snapshot(
    *,
    selection_path: Path,
    selection: Mapping[str, Any],
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = str(selection["winner"])
    plan_path = Path(str(_mapping(selection["plan"], "selection plan")["path"]))
    output_dir = _resolve(Path(str(plan["outputDirectory"])))
    paths = _candidate_paths(candidate, output_dir, robustness=True)
    manifest = _load_json(paths["manifest"], "robustness manifest")
    network = _verify_manifest_record(
        manifest,
        candidate=candidate,
        plan=plan,
        profile=profile,
        plan_path=plan_path,
        output_dir=output_dir,
        split_rows=_planned_split_rows(plan),
        robustness=True,
    )
    identities = _mapping(plan["identities"], "plan identities")
    corpus = Path(str(identities["corpus"]["path"]))
    cpp_evaluator = Path(str(identities["cppEvaluator"]["path"]))
    whole = _load_feature_corpus(corpus)
    validation = _load_decision_splits(
        corpus,
        hce_evaluator=cpp_evaluator,
        decode_splits=("validation",),
    )
    initializer3 = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    initializer4 = _migrate_initializer(initializer3)
    migration = _migration_parity(initializer3, initializer4, whole)
    if migration != plan.get("initializerMigration"):
        raise ValueError("robustness initializer migration changed")
    indices = validation.indices(SPLITS["validation"])
    metrics = _common_metrics(
        validation,
        SPLITS["validation"],
        _predict_network(network, validation.features, indices),
    )
    i0 = _common_metrics(
        validation,
        SPLITS["validation"],
        _predict_network(initializer4, validation.features, indices),
    )
    zero = _common_metrics(
        validation,
        SPLITS["validation"],
        np.zeros(indices.size, dtype=np.int32),
    )
    health, _whole_predictions = _runtime_health(
        network,
        whole,
        mapped_initializer=initializer4,
        migration=migration,
        cpp_evaluator=cpp_evaluator,
        thresholds=_mapping(
            profile.get("deploymentHealthGate"), "deployment health thresholds"
        ),
        require_cpp=True,
    )
    if (
        manifest.get("commonValidation") != metrics
        or manifest.get("i0Validation") != i0
        or manifest.get("zeroResidualValidation") != zero
        or manifest.get("health") != health
    ):
        raise ValueError("robustness artifact metrics or health are stale")
    gates = {
        "deploymentHealth": health.get("passed") is True,
        "strictlyLowerCommonValidationHuberThanI0": (
            float(metrics["huberLoss"]) < float(i0["huberLoss"])
        ),
        "strictlyLowerCommonValidationHuberThanZeroResidual": (
            float(metrics["huberLoss"]) < float(zero["huberLoss"])
        ),
    }
    return {
        "profileId": PROFILE_ID,
        "plan": _identity(plan_path),
        "selection": _identity(selection_path),
        "candidateId": candidate,
        "recipe": CANDIDATE_RECIPES[candidate],
        "trainingSeedPurpose": "robustness-training",
        "trainingSeed": _candidate_seed(
            profile, candidate, "robustness-training"
        ),
        "factorBasis": plan["factorBases"][candidate],
        "initializer": "mapped K2 architecture-4 migration I0",
        "selectedPrimaryNetwork": selection["winnerNetwork"],
        "robustnessNetwork": _identity(paths["network"]),
        "robustnessManifest": _identity(paths["manifest"]),
        "commonValidation": metrics,
        "baselines": {"I0": i0, "zeroResidual": zero},
        "deploymentHealth": health,
        "gates": gates,
        "passed": all(gates.values()),
        "mayReplacePrimary": False,
        "informationBoundary": {
            "validationTargetRowsDecoded": validation.target_rows_decoded,
            "validationTargetFieldsDecoded": validation.target_fields_decoded,
            "heldOutRowsRouted": validation.heldout_rows,
            "heldOutTargetFieldsDecoded": 0,
            "matchResultsAccessed": False,
        },
    }


def _verify_robustness_training_claim(
    path: Path,
    *,
    candidate: str,
    selection: Mapping[str, Any],
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    claim_path = _resolve(path)
    output_dir = _resolve(Path(str(plan["outputDirectory"])))
    expected_path = _candidate_claim_path(
        candidate, output_dir, robustness=True
    )
    if claim_path != expected_path:
        raise ValueError("robustness training claim is outside its namespace")
    value = _load_json(claim_path, "robustness training claim")
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "candidateId",
        "plan",
        "outputDirectory",
        "bundle",
        "trainingSeed",
        "factorSeed",
        "factorBasis",
        "trainingPurpose",
        "createdUtc",
        "targetFieldsDecoded",
    }
    if set(value) != expected_fields:
        raise ValueError("robustness training-claim fields changed")
    expected = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v5-robustness-training-claim",
        "profileId": PROFILE_ID,
        "candidateId": candidate,
        "plan": selection["plan"],
        "outputDirectory": str(output_dir),
        "bundle": str(
            _candidate_bundle(candidate, output_dir, robustness=True)
        ),
        "trainingSeed": _candidate_seed(
            profile, candidate, "robustness-training"
        ),
        "factorSeed": _candidate_seed(profile, candidate, "factor-basis"),
        "factorBasis": plan["factorBases"][candidate],
        "trainingPurpose": "robustness-training",
        "targetFieldsDecoded": 0,
    }
    for key, item in expected.items():
        if value.get(key) != item:
            raise ValueError(f"robustness training-claim {key} changed")
    try:
        datetime.fromisoformat(
            str(value.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("robustness claim createdUtc is invalid") from error
    return value


def _run_robustness(args: argparse.Namespace) -> dict[str, Any]:
    selection_path = _resolve(args.selection)
    selection, plan, profile = _verify_selection(selection_path)
    outputs = _mapping(plan.get("outputs"), "plan outputs")
    seal_path = _resolve(Path(str(outputs["robustnessSeal"])))
    candidate = str(selection["winner"])
    output_dir = _resolve(Path(str(plan["outputDirectory"])))
    paths = _candidate_paths(candidate, output_dir, robustness=True)
    claim_path = _candidate_claim_path(
        candidate, output_dir, robustness=True
    )
    if seal_path.exists():
        value, _selection, _plan, _profile = _verify_robustness(seal_path)
        return value
    if paths["failure"].exists():
        raise ValueError("robustness failure seal already exists")
    if claim_path.exists():
        _verify_robustness_training_claim(
            claim_path,
            candidate=candidate,
            selection=selection,
            plan=plan,
            profile=profile,
        )
        if paths["manifest"].exists():
            # The bundle directory is the worker's atomic commit point.  If a
            # process died after rename but before its finally block, a
            # complete verified bundle makes retirement of the claim safe.
            manifest = _load_json(paths["manifest"], "robustness manifest")
            _verify_manifest_record(
                manifest,
                candidate=candidate,
                plan=plan,
                profile=profile,
                plan_path=Path(
                    str(_mapping(selection["plan"], "plan")["path"])
                ),
                output_dir=output_dir,
                split_rows=_planned_split_rows(plan),
                robustness=True,
            )
            claim_path.unlink()
    if not paths["manifest"].exists():
        command = _mapping(
            _mapping(plan["robustnessCommands"], "robustness commands")[candidate],
            "selected robustness command",
        )
        if command != selection.get("robustnessCommand"):
            raise ValueError("selected robustness command changed")
        environment = os.environ.copy()
        environment.update(_mapping(command["environment"], "worker environment"))
        completed = subprocess.run(
            [str(value) for value in command["argv"]],
            cwd=REPO,
            env=environment,
            check=False,
        )
        if completed.returncode != 0:
            if paths["manifest"].exists():
                committed = _load_json(
                    paths["manifest"], "robustness manifest"
                )
                _verify_manifest_record(
                    committed,
                    candidate=candidate,
                    plan=plan,
                    profile=profile,
                    plan_path=Path(
                        str(_mapping(selection["plan"], "plan")["path"])
                    ),
                    output_dir=output_dir,
                    split_rows=_planned_split_rows(plan),
                    robustness=True,
                )
            elif not paths["failure"].exists():
                _exclusive_json(
                    paths["failure"],
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "kind": ROBUSTNESS_FAILURE_KIND,
                        "profileId": PROFILE_ID,
                        "createdUtc": _utc_now(),
                        "candidateId": candidate,
                        "trainingPurpose": "robustness-training",
                        "reason": "robustness worker exited before bundle publication",
                        "exitCode": completed.returncode,
                        "plan": _identity(
                            Path(str(_mapping(selection["plan"], "plan")["path"]))
                        ),
                        "selection": _identity(selection_path),
                        "heldOutTargetFieldsDecoded": 0,
                    },
                )
                raise RuntimeError(
                    f"robustness worker exited {completed.returncode}"
                )
    if claim_path.exists():
        _verify_robustness_training_claim(
            claim_path,
            candidate=candidate,
            selection=selection,
            plan=plan,
            profile=profile,
        )
        if not paths["manifest"].exists():
            raise RuntimeError(
                "robustness claim remains without a committed bundle; "
                "preserving it for another deterministic resume"
            )
        manifest = _load_json(paths["manifest"], "robustness manifest")
        _verify_manifest_record(
            manifest,
            candidate=candidate,
            plan=plan,
            profile=profile,
            plan_path=Path(str(_mapping(selection["plan"], "plan")["path"])),
            output_dir=output_dir,
            split_rows=_planned_split_rows(plan),
            robustness=True,
        )
        claim_path.unlink()

    if paths["failure"].exists():
        raise ValueError("selected recipe produced no health-passing robustness checkpoint")
    snapshot = _robustness_snapshot(
        selection_path=selection_path,
        selection=selection,
        plan=plan,
        profile=profile,
    )
    seal = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ROBUSTNESS_KIND,
        "createdUtc": _utc_now(),
        **snapshot,
    }
    _exclusive_json(seal_path, seal)
    if seal["passed"] is not True:
        raise ValueError(
            "selected recipe failed robustness deployment/directional gates"
        )
    return seal


def _verify_robustness(
    path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    robustness_path = _resolve(path)
    value = _load_json(robustness_path, "generation-5 robustness seal")
    expected_fields = {
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
    if set(value) != expected_fields:
        raise ValueError("robustness seal field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != ROBUSTNESS_KIND
    ):
        raise ValueError("robustness seal schema or kind changed")
    try:
        datetime.fromisoformat(
            str(value.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("robustness createdUtc is invalid") from error
    selection_path = _verify_identity(
        value.get("selection"), "robustness selection"
    )
    selection, plan, profile = _verify_selection(selection_path)
    expected_path = _resolve(Path(str(plan["outputs"]["robustnessSeal"])))
    if robustness_path != expected_path:
        raise ValueError("robustness seal is outside its frozen namespace")
    snapshot = _robustness_snapshot(
        selection_path=selection_path,
        selection=selection,
        plan=plan,
        profile=profile,
    )
    for key, expected in snapshot.items():
        if value.get(key) != expected:
            raise ValueError(f"robustness {key} differs from recomputation")
    if value.get("passed") is not True:
        raise ValueError("robustness gate did not pass")
    return value, selection, plan, profile


def _whole_root_bootstrap(
    dataset: DecisionCorpus,
    candidate_predictions: np.ndarray,
    i0_predictions: np.ndarray,
    *,
    replicates: int = OFFLINE_BOOTSTRAP_REPLICATES,
    seed: int = OFFLINE_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    indices = dataset.indices(SPLITS["heldOut"])
    if candidate_predictions.shape != (indices.size,) or i0_predictions.shape != (
        indices.size,
    ):
        raise ValueError("held-out bootstrap prediction shape changed")
    if type(replicates) is not int or replicates <= 0 or type(seed) is not int:
        raise ValueError("held-out bootstrap configuration is invalid")
    candidate_loss = _huber(
        candidate_predictions.astype(np.float64) - dataset.target_cp[indices]
    )
    i0_loss = _huber(
        i0_predictions.astype(np.float64) - dataset.target_cp[indices]
    )
    local_by_root: dict[str, list[int]] = defaultdict(list)
    for local, corpus_index in enumerate(indices):
        local_by_root[dataset.features.root_ids[int(corpus_index)]].append(local)
    strata: dict[str, dict[str, Any]] = {}
    for phase in PHASES:
        roots = sorted(
            root
            for root, locals_ in local_by_root.items()
            if dataset.features.phases[int(indices[locals_[0]])] == phase
        )
        if not roots:
            raise ValueError(f"held-out bootstrap lacks {phase} roots")
        candidate_roots: list[float] = []
        i0_roots: list[float] = []
        group_names: list[str] = []
        for root in roots:
            locals_ = local_by_root[root]
            if len(locals_) != 4:
                raise ValueError(f"bootstrap root {root!r} is not whole")
            rows = [int(indices[local]) for local in locals_]
            groups = {dataset.features.groups[row] for row in rows}
            phases = {dataset.features.phases[row] for row in rows}
            if len(groups) != 1 or phases != {phase}:
                raise ValueError(f"bootstrap root {root!r} changed group/phase")
            candidate_roots.append(float(np.mean(candidate_loss[locals_])))
            i0_roots.append(float(np.mean(i0_loss[locals_])))
            group_names.append(next(iter(groups)))
        ordered_groups = {name: index for index, name in enumerate(sorted(set(group_names)))}
        strata[phase] = {
            "candidate": np.asarray(candidate_roots, dtype=np.float64),
            "i0": np.asarray(i0_roots, dtype=np.float64),
            "groups": np.asarray(
                [ordered_groups[name] for name in group_names], dtype=np.int64
            ),
            "groupCount": len(ordered_groups),
            "rootCount": len(roots),
        }
    rng = np.random.Generator(np.random.PCG64(seed))
    statistics = np.empty(replicates, dtype=np.float64)
    for replicate in range(replicates):
        phase_candidate: list[float] = []
        phase_i0: list[float] = []
        for phase in PHASES:
            stratum = strata[phase]
            root_count = int(stratum["rootCount"])
            sample = rng.integers(0, root_count, size=root_count)
            sampled_groups = stratum["groups"][sample]
            counts = np.bincount(
                sampled_groups, minlength=int(stratum["groupCount"])
            )
            present = counts > 0
            candidate_sums = np.bincount(
                sampled_groups,
                weights=stratum["candidate"][sample],
                minlength=int(stratum["groupCount"]),
            )
            i0_sums = np.bincount(
                sampled_groups,
                weights=stratum["i0"][sample],
                minlength=int(stratum["groupCount"]),
            )
            phase_candidate.append(
                float(np.mean(candidate_sums[present] / counts[present]))
            )
            phase_i0.append(float(np.mean(i0_sums[present] / counts[present])))
        candidate_macro = float(np.mean(phase_candidate))
        i0_macro = float(np.mean(phase_i0))
        if i0_macro <= 0.0:
            raise ValueError("bootstrap I0 Huber denominator is not positive")
        statistics[replicate] = (i0_macro - candidate_macro) / i0_macro
    return {
        "paired": True,
        "resamplingUnit": "whole four-child root",
        "stratification": "phase in frozen opening,middlegame,late,endgame order",
        "withinReplicateMetric": "phase-macro group-balanced Huber loss",
        "statistic": "(I0 phase-macro group-balanced Huber - candidate) / I0",
        "replicates": replicates,
        "rng": "NumPy Generator PCG64",
        "seed": seed,
        "quantile": 0.05,
        "quantileMethod": "linear",
        "oneSidedConfidenceLevel": 0.95,
        "lowerBound": float(
            np.quantile(statistics, 0.05, method="linear")
        ),
        "rootsPerPhase": {
            phase: int(strata[phase]["rootCount"]) for phase in PHASES
        },
        "groupsPerPhase": {
            phase: int(strata[phase]["groupCount"]) for phase in PHASES
        },
    }


def _offline_claim_expected(
    *,
    selection: Mapping[str, Any],
    robustness: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    outputs = _mapping(plan.get("outputs"), "plan outputs")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": OFFLINE_CLAIM_KIND,
        "profileId": PROFILE_ID,
        "plan": selection["plan"],
        "selection": robustness["selection"],
        "robustness": _identity(Path(str(outputs["robustnessSeal"]))),
        "selectedCandidateId": selection["winner"],
        "selectedPrimaryNetwork": selection["winnerNetwork"],
        "corpus": plan["identities"]["corpus"],
        "output": str(_resolve(Path(str(outputs["offlineReport"])))),
        "access": {
            "status": "claimed-before-any-held-out-target-decode",
            "heldOutTargetRowsDecodedAtClaim": 0,
            "heldOutTargetFieldsDecodedAtClaim": 0,
            "selectedCandidateOnly": True,
            "runnerUpFallback": False,
            "resumePolicy": "reuse this immutable claim; never publish a second claim",
        },
    }


def _verify_offline_claim(
    path: Path,
    *,
    selection: Mapping[str, Any],
    robustness: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    claim_path = _resolve(path)
    expected_path = _resolve(Path(str(plan["outputs"]["offlineAccessClaim"])))
    if claim_path != expected_path:
        raise ValueError("offline access claim is outside its frozen namespace")
    value = _load_json(claim_path, "generation-5 offline access claim")
    if set(value) != {*_offline_claim_expected(
        selection=selection, robustness=robustness, plan=plan
    ), "createdUtc"}:
        raise ValueError("offline access-claim field inventory changed")
    expected = _offline_claim_expected(
        selection=selection, robustness=robustness, plan=plan
    )
    for key, item in expected.items():
        if value.get(key) != item:
            raise ValueError(f"offline access-claim {key} changed")
    try:
        datetime.fromisoformat(
            str(value.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("offline claim createdUtc is invalid") from error
    return value


def _offline_gate(
    *,
    metrics: Mapping[str, Mapping[str, Any]],
    bootstrap: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    selected = _mapping(metrics.get("selectedPrimary"), "selected metrics")
    i0 = _mapping(metrics.get("I0"), "I0 metrics")
    zero = _mapping(metrics.get("zeroResidual"), "zero metrics")
    gate = _mapping(
        _mapping(profile.get("postSelection"), "post selection").get(
            "oneTimeHeldOut"
        ),
        "held-out gate",
    )
    relative_i0 = _relative_improvement(
        float(selected["huberLoss"]), float(i0["huberLoss"])
    )
    relative_zero = _relative_improvement(
        float(selected["huberLoss"]), float(zero["huberLoss"])
    )
    cp_improvement = float(i0["cpMae"]) - float(selected["cpMae"])
    phase_regressions = {
        phase: float(selected["phase"][phase]["cpMae"])
        - float(i0["phase"][phase]["cpMae"])
        for phase in PHASES
    }
    checks = {
        "minimumRelativeHuberImprovementAgainstI0": relative_i0
        >= float(gate["minimumRelativeHuberImprovementAgainstI0"]),
        "minimumRelativeHuberImprovementAgainstZeroResidual": relative_zero
        >= float(gate["minimumRelativeHuberImprovementAgainstZeroResidual"]),
        "minimumPhaseMacroCpMaeImprovementVersusI0": cp_improvement
        >= float(gate["minimumPhaseMacroCpMaeImprovementVersusI0"]),
        "maximumAnyPhaseCpMaeRegressionVersusI0": max(
            phase_regressions.values()
        )
        <= float(gate["maximumAnyPhaseCpMaeRegressionVersusI0"]),
        "oneSided95PercentBootstrapLowerBoundPositive": float(
            bootstrap["lowerBound"]
        )
        > 0.0,
    }
    return {
        "relativeHuberImprovementAgainstI0": relative_i0,
        "relativeHuberImprovementAgainstZeroResidual": relative_zero,
        "phaseMacroCpMaeImprovementVersusI0": cp_improvement,
        "phaseCpMaeRegressionVersusI0": phase_regressions,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _verify_offline_report(path: Path) -> dict[str, Any]:
    report_path = _resolve(path)
    value = _load_json(report_path, "generation-5 offline report")
    expected_fields = {
        "schemaVersion",
        "kind",
        "createdUtc",
        "profileId",
        "plan",
        "selection",
        "robustness",
        "accessClaim",
        "selectedCandidateId",
        "evaluatedNetworks",
        "metrics",
        "bootstrap",
        "gate",
        "targetAccess",
        "matchAuthorization",
        "failureAction",
    }
    if set(value) != expected_fields:
        raise ValueError("offline report field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != OFFLINE_REPORT_KIND
        or value.get("profileId") != PROFILE_ID
    ):
        raise ValueError("offline report schema/kind/profile changed")
    robustness_path = _verify_identity(
        value.get("robustness"), "offline robustness seal"
    )
    robustness, selection, plan, profile = _verify_robustness(robustness_path)
    if report_path != _resolve(Path(str(plan["outputs"]["offlineReport"]))):
        raise ValueError("offline report is outside its frozen namespace")
    claim_path = _verify_identity(value.get("accessClaim"), "offline claim")
    _verify_offline_claim(
        claim_path,
        selection=selection,
        robustness=robustness,
        plan=plan,
    )
    if (
        value.get("plan") != selection["plan"]
        or value.get("selection") != robustness["selection"]
        or value.get("selectedCandidateId") != selection["winner"]
        or value.get("evaluatedNetworks")
        != {
            "selectedPrimary": selection["winnerNetwork"],
            "I0": plan["identities"]["initializer"],
            "zeroResidual": "constant zero residual; no network artifact",
            "robustnessNetworkHeldOutTargetEvaluations": 0,
            "runnerUpNetworkHeldOutTargetEvaluations": 0,
        }
    ):
        raise ValueError("offline report preaccess identities changed")
    metrics = _mapping(value.get("metrics"), "offline metrics")
    if set(metrics) != {"selectedPrimary", "I0", "zeroResidual"}:
        raise ValueError("offline metric model inventory changed")
    for key in metrics:
        _verify_metric_shape(metrics[key], f"offline {key}")
    bootstrap = _mapping(value.get("bootstrap"), "offline bootstrap")
    expected_bootstrap_static = {
        "paired": True,
        "resamplingUnit": "whole four-child root",
        "stratification": "phase in frozen opening,middlegame,late,endgame order",
        "withinReplicateMetric": "phase-macro group-balanced Huber loss",
        "statistic": "(I0 phase-macro group-balanced Huber - candidate) / I0",
        "replicates": OFFLINE_BOOTSTRAP_REPLICATES,
        "rng": "NumPy Generator PCG64",
        "seed": OFFLINE_BOOTSTRAP_SEED,
        "quantile": 0.05,
        "quantileMethod": "linear",
        "oneSidedConfidenceLevel": 0.95,
    }
    if set(bootstrap) != {
        *expected_bootstrap_static,
        "lowerBound",
        "rootsPerPhase",
        "groupsPerPhase",
    }:
        raise ValueError("offline bootstrap field inventory changed")
    for key, expected in expected_bootstrap_static.items():
        if bootstrap.get(key) != expected:
            raise ValueError(f"offline bootstrap {key} changed")
    _number(bootstrap.get("lowerBound"), "bootstrap lower bound")
    for key in ("rootsPerPhase", "groupsPerPhase"):
        counts = _mapping(bootstrap.get(key), f"bootstrap {key}")
        if set(counts) != set(PHASES) or any(
            type(counts[phase]) is not int or counts[phase] <= 0 for phase in PHASES
        ):
            raise ValueError(f"offline bootstrap {key} changed")
    planned_phase_roots = _mapping(
        _mapping(
            plan.get("targetOpaqueCorpusAudit"), "plan corpus audit"
        ).get("phaseRoots"),
        "plan phase roots",
    )
    expected_heldout_roots = _mapping(
        planned_phase_roots.get("heldOut"), "plan held-out phase roots"
    )
    if bootstrap["rootsPerPhase"] != expected_heldout_roots:
        raise ValueError("offline bootstrap root quotas changed")
    if any(
        int(bootstrap["groupsPerPhase"][phase])
        > int(bootstrap["rootsPerPhase"][phase])
        for phase in PHASES
    ):
        raise ValueError("offline bootstrap has more groups than roots")
    expected_gate = _offline_gate(
        metrics=metrics, bootstrap=bootstrap, profile=profile
    )
    if value.get("gate") != expected_gate:
        raise ValueError("offline gate differs from sealed metrics")
    passed = expected_gate["passed"]
    expected_authorization = {
        "authorized": passed,
        "selectedPrimaryNetwork": selection["winnerNetwork"],
        "runnerUpFallback": False,
    }
    if value.get("matchAuthorization") != expected_authorization:
        raise ValueError("offline match authorization changed")
    expected_action = (
        "authorize preregistered matches for the selected primary only"
        if passed
        else "close generation; do not test a runner-up or launch matches"
    )
    if value.get("failureAction") != expected_action:
        raise ValueError("offline failure action changed")
    split_rows = _planned_split_rows(plan)
    expected_access = {
        "accessClaimPublishedBeforeHeldOutDecode": True,
        "targetFieldNames": list(TARGET_FIELDS_DECODED_PER_ROW),
        "trainTargetRowsDecoded": 0,
        "validationTargetRowsDecoded": 0,
        "heldOutTargetRowsDecoded": split_rows["heldOut"],
        "heldOutTargetFieldsDecoded": split_rows["heldOut"]
        * len(TARGET_FIELDS_DECODED_PER_ROW),
        "heldOutHandcraftedScoresComputed": split_rows["heldOut"],
        "heldOutResidualTargetsComputed": split_rows["heldOut"],
        "selectedPrimaryNetworkPredictions": split_rows["heldOut"],
        "i0Predictions": split_rows["heldOut"],
        "zeroResidualPredictions": split_rows["heldOut"],
        "robustnessNetworkHeldOutTargetMetricPredictions": 0,
        "runnerUpNetworkHeldOutTargetMetricPredictions": 0,
        "matchResultsAccessed": False,
    }
    if value.get("targetAccess") != expected_access:
        raise ValueError("offline target counters changed")
    try:
        datetime.fromisoformat(
            str(value.get("createdUtc", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("offline report createdUtc is invalid") from error
    return value


def _offline_evaluate(args: argparse.Namespace) -> dict[str, Any]:
    robustness_path = _resolve(args.robustness)
    robustness, selection, plan, profile = _verify_robustness(robustness_path)
    outputs = _mapping(plan.get("outputs"), "plan outputs")
    claim_path = _resolve(Path(str(outputs["offlineAccessClaim"])))
    report_path = _resolve(Path(str(outputs["offlineReport"])))
    if report_path.exists():
        report = _verify_offline_report(report_path)
        if not report["gate"]["passed"]:
            raise ValueError("generation-5 held-out gate did not pass")
        return report

    expected_claim = _offline_claim_expected(
        selection=selection, robustness=robustness, plan=plan
    )
    if not claim_path.exists():
        _exclusive_json(
            claim_path,
            {**expected_claim, "createdUtc": _utc_now()},
        )
    _verify_offline_claim(
        claim_path,
        selection=selection,
        robustness=robustness,
        plan=plan,
    )
    claim_identity = _identity(claim_path)

    identities = _mapping(plan["identities"], "plan identities")
    corpus = Path(str(identities["corpus"]["path"]))
    cpp_evaluator = Path(str(identities["cppEvaluator"]["path"]))
    heldout = _load_decision_splits(
        corpus,
        hce_evaluator=cpp_evaluator,
        decode_splits=("heldOut",),
        heldout_access_claim=claim_identity,
    )
    indices = heldout.indices(SPLITS["heldOut"])
    selected_network = QuantizedNetwork.read(
        Path(str(_mapping(selection["winnerNetwork"], "winner network")["path"]))
    )
    initializer3 = QuantizedNetwork.read(
        Path(str(identities["initializer"]["path"]))
    )
    initializer4 = _migrate_initializer(initializer3)
    selected_predictions = _predict_network(
        selected_network, heldout.features, indices
    )
    i0_predictions = _predict_network(initializer4, heldout.features, indices)
    zero_predictions = np.zeros(indices.size, dtype=np.int32)
    metrics = {
        "selectedPrimary": _common_metrics(
            heldout, SPLITS["heldOut"], selected_predictions
        ),
        "I0": _common_metrics(heldout, SPLITS["heldOut"], i0_predictions),
        "zeroResidual": _common_metrics(
            heldout, SPLITS["heldOut"], zero_predictions
        ),
    }
    bootstrap = _whole_root_bootstrap(
        heldout,
        selected_predictions,
        i0_predictions,
        replicates=OFFLINE_BOOTSTRAP_REPLICATES,
        seed=OFFLINE_BOOTSTRAP_SEED,
    )
    gate = _offline_gate(metrics=metrics, bootstrap=bootstrap, profile=profile)
    split_rows = _planned_split_rows(plan)
    if (
        heldout.target_rows_decoded != split_rows["heldOut"]
        or heldout.target_fields_decoded
        != split_rows["heldOut"] * len(TARGET_FIELDS_DECODED_PER_ROW)
        or heldout.target_fields_decoded_by_split
        != {
            "train": 0,
            "validation": 0,
            "heldOut": split_rows["heldOut"]
            * len(TARGET_FIELDS_DECODED_PER_ROW),
        }
    ):
        raise ValueError("held-out target counters changed")
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": OFFLINE_REPORT_KIND,
        "createdUtc": _utc_now(),
        "profileId": PROFILE_ID,
        "plan": selection["plan"],
        "selection": robustness["selection"],
        "robustness": _identity(robustness_path),
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
            "targetFieldNames": list(TARGET_FIELDS_DECODED_PER_ROW),
            "trainTargetRowsDecoded": 0,
            "validationTargetRowsDecoded": 0,
            "heldOutTargetRowsDecoded": heldout.target_rows_decoded,
            "heldOutTargetFieldsDecoded": heldout.target_fields_decoded,
            "heldOutHandcraftedScoresComputed": (
                heldout.handcrafted_scores_computed
            ),
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
    _exclusive_json(report_path, report)
    verified = _verify_offline_report(report_path)
    if verified["gate"]["passed"] is not True:
        raise ValueError("generation-5 held-out gate did not pass")
    return verified


def _synthetic_initializer() -> QuantizedNetwork:
    return QuantizedNetwork(
        ft_bias=np.full(ACCUMULATOR_SIZE, 16, dtype=np.int16),
        ft_weights=np.zeros(
            (KING_STATE_FEATURE_COUNT, ACCUMULATOR_SIZE), dtype=np.int16
        ),
        dense_bias=np.full(HIDDEN_SIZE, 8 * HIDDEN_DIVISOR, dtype=np.int32),
        dense_weights=np.zeros(
            (HIDDEN_SIZE, ACCUMULATOR_SIZE * 2), dtype=np.int8
        ),
        output_bias=0,
        output_weights=np.zeros(HIDDEN_SIZE, dtype=np.int8),
        architecture=ARCHITECTURE_KING_STATE_RESIDUAL,
    )


def _synthetic_ofen(root: int, child: int) -> str:
    # Kings plus one rook.  Rook and halfmove bins make signatures distinct.
    file = (root * 4 + child) % 10
    ranks = ["10"] * 10
    ranks[9] = "9k"
    rook_rank = 2 + ((root + child) % 6)
    left = "" if file == 0 else str(file)
    right_count = 9 - file
    right = "" if right_count == 0 else str(right_count)
    ranks[9 - rook_rank] = f"{left}R{right}"
    ranks[0] = "K9"
    return "/".join(ranks) + f"[-/-/-/-] {'w' if root % 2 == 0 else 'b'} - - {child * 20} 1"


def _synthetic_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    # Four roots/phase/split permits the same structural audits as real data.
    root_number = 0
    for split in ("train", "validation", "test"):
        for phase in PHASES:
            root = root_number
            root_number += 1
            scores = [120.0, 80.0, 60.0, -700.0]
            for child, root_score in enumerate(scores):
                gap = scores[0] - root_score
                records.append(
                    {
                        "kind": "omega-decision-deep-label",
                        "childOfen": _synthetic_ofen(root, child),
                        "rootId": f"root-{root}",
                        "leakageComponentId": f"component-{root}",
                        "phase": phase,
                        "split": split,
                        "deepScoreCpRoot": root_score,
                        "deepScoreCpChildStm": -root_score,
                        "deepRank": child + 1,
                        "deepRegretCp": gap,
                        "rankingEligibleAgainstBest": gap >= 20,
                        "rankingGapCpCapped": min(gap, 600),
                        "handcraftedCpChildStm": -20.0 + child,
                    }
                )
    return records


def _self_test() -> dict[str, Any]:
    runtime = runtime_contract.current_runtime_record()
    if runtime["numpyPreloadedBeforeRuntimeContract"] is not False:
        raise AssertionError("trainer missed the pre-NumPy runtime contract")
    with tempfile.TemporaryDirectory(prefix="omega-g5-trainer-self-test-") as directory:
        root = Path(directory)
        corpus = root / "decision-labels.jsonl"
        records = _synthetic_records()
        with corpus.open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                # A held-out target is deliberately the wrong type.  The
                # feature-only loader accepts it, and the labeled loader must
                # route it away before JSON target interpretation.
                if record["split"] == "test":
                    record = {**record, "deepScoreCpChildStm": "HELD-OUT-POISON"}
                stream.write(json.dumps(record, sort_keys=True) + "\n")
        try:
            _load_feature_corpus(corpus)
        except ValueError:
            pass
        else:
            raise AssertionError("frozen loader accepted legacy test split alias")
        whole = _load_feature_corpus(corpus, allow_test_alias=True)
        audit = _audit_four_siblings(whole)
        combined = _load_decision_splits(
            corpus,
            hce_evaluator=None,
            allow_embedded_hce=True,
            allow_test_alias=True,
        )
        if (
            combined.heldout_rows != 16
            or combined.target_rows_decoded != 32
            or combined.target_fields_decoded != 192
        ):
            raise AssertionError("held-out target routing self-test failed")
        dataset = _load_decision_splits(
            corpus,
            hce_evaluator=None,
            allow_embedded_hce=True,
            allow_test_alias=True,
            decode_splits=("train",),
        )
        if (
            dataset.target_fields_decoded_by_split
            != {"train": 96, "validation": 0, "heldOut": 0}
        ):
            raise AssertionError("train-only target boundary failed")
        validation_loads = 0

        def load_validation() -> DecisionCorpus:
            nonlocal validation_loads
            validation_loads += 1
            return _load_decision_splits(
                corpus,
                hce_evaluator=None,
                allow_embedded_hce=True,
                allow_test_alias=True,
                decode_splits=("validation",),
            )

        initializer3 = _synthetic_initializer()
        initializer4 = _migrate_initializer(initializer3)
        migration = _migration_parity(initializer3, initializer4, whole)
        if not migration["passed"]:
            raise AssertionError("synthetic migration parity failed")
        basis = _factor_basis(12345, 4)
        basis_payload = np.asarray(basis, dtype=np.dtype("<f4")).tobytes(
            order="C"
        )
        basis_identity = _basis_record(
            "G5A",
            root / "G5A.geometry.f32le",
            basis_payload,
            rank=4,
            seed=12345,
        )
        _exclusive_bytes(Path(str(basis_identity["path"])), basis_payload)
        stored_basis = _load_factor_basis(
            basis_identity, candidate="G5A", rank=4, seed=12345
        )
        if stored_basis.tobytes(order="C") != basis_payload:
            raise AssertionError("stored geometry basis changed bytes")

        # Simulate a process dying after publishing one target-blind basis but
        # before the plan commit.  The next invocation must authenticate that
        # byte-identical partial artifact, finish the remaining publications,
        # and make the plan visible only after every referenced basis exists.
        interrupted_plan_dir = root / "interrupted-plan-publication"
        interrupted_plan_path = interrupted_plan_dir / "plan.json"
        interrupted_basis_payloads = {
            interrupted_plan_dir / "G5A.geometry.f32le": b"basis-A-v1",
            interrupted_plan_dir / "G5B.geometry.f32le": b"basis-B-v1",
            interrupted_plan_dir / "G5C.geometry.f32le": b"basis-C-v1",
        }
        first_interrupted_path = next(iter(interrupted_basis_payloads))
        _publish_or_verify_deterministic_bytes(
            first_interrupted_path,
            interrupted_basis_payloads[first_interrupted_path],
        )
        if interrupted_plan_path.exists():
            raise AssertionError("interrupted target-blind plan became visible")
        interrupted_identities = {
            str(path): _publish_or_verify_deterministic_bytes(path, payload)
            for path, payload in interrupted_basis_payloads.items()
        }
        if any(not path.is_file() for path in interrupted_basis_payloads):
            raise AssertionError("resumed plan publication omitted a basis")
        interrupted_plan = {
            "kind": "synthetic-target-blind-plan",
            "factorBases": interrupted_identities,
        }
        try:
            _atomic_exclusive_json(
                interrupted_plan_path,
                interrupted_plan,
                interruption_hook=lambda stage: (
                    (_ for _ in ()).throw(RuntimeError("synthetic plan interruption"))
                    if stage == "after-stage-before-commit"
                    else None
                ),
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("plan-publication interruption did not fire")
        if interrupted_plan_path.exists():
            raise AssertionError("interrupted plan final path became visible")
        _atomic_exclusive_json(interrupted_plan_path, interrupted_plan)
        if _load_json(interrupted_plan_path, "synthetic resumed plan").get(
            "factorBases"
        ) != interrupted_identities:
            raise AssertionError("resumed plan did not bind authenticated bases")
        try:
            _atomic_exclusive_json(interrupted_plan_path, interrupted_plan)
        except FileExistsError:
            pass
        else:
            raise AssertionError("atomic plan commit replaced an existing plan")

        tampered_basis_path = interrupted_plan_dir / "tampered.geometry.f32le"
        tampered_bytes = b"tampered-partial-basis"
        _exclusive_bytes(tampered_basis_path, tampered_bytes)
        try:
            _publish_or_verify_deterministic_bytes(
                tampered_basis_path, b"expected-deterministic-basis"
            )
        except ValueError:
            pass
        else:
            raise AssertionError("tampered partial basis was accepted")
        if tampered_basis_path.read_bytes() != tampered_bytes:
            raise AssertionError("tampered partial basis was silently replaced")

        model = FactorizedNetwork.from_initializer(initializer4, stored_basis)
        if model.quantize().to_bytes() != initializer4.to_bytes():
            raise AssertionError("factorized epoch-zero identity failed")

        train = dataset.indices(0)
        first_batch = np.asarray(
            [
                row
                for root_id in sorted(
                    {dataset.features.root_ids[int(index)] for index in train}
                )
                for row in [
                    int(index)
                    for index in train
                    if dataset.features.root_ids[int(index)] == root_id
                ]
            ],
            dtype=np.int64,
        )
        weights = _phase_group_weights(dataset)
        loss, gradients, objective = _gradients(
            dataset,
            first_batch,
            model,
            row_weights=weights,
            ranking_weight=0.5,
            quantization_aware=True,
        )
        if (
            not math.isfinite(loss)
            or objective["rankingPairs"] <= 0
            or not all(np.all(np.isfinite(value)) for value in gradients.values())
        ):
            raise AssertionError("decision/ranking gradient self-test failed")

        synthetic_thresholds = {
            "maximumMeanFloatQuantizationPenaltyCp": 2,
            "maximumSingleFloatQuantizationPenaltyCp": 10,
            "maximumSaturatedDenseUnits": 0,
            "maximumDeadDenseUnits": 32,
            "deadDenseUnitsMayExceedI0": False,
            "denseActiveFractionRangeInclusive": [0.0, 1.0],
            "maximumAbsoluteCorrectionCp": 2500,
        }
        health, predictions = _runtime_health(
            initializer4,
            whole,
            mapped_initializer=initializer4,
            migration=migration,
            cpp_evaluator=None,
            thresholds=synthetic_thresholds,
            require_cpp=False,
        )
        if not health["passed"] or np.max(np.abs(predictions)) > 600:
            raise AssertionError("clamp-aware synthetic health failed")

        trained = _train_candidate(
            candidate_id="G5A",
            dataset=dataset,
            validation_loader=load_validation,
            whole_features=whole,
            initializer=initializer4,
            seed=24680,
            factor_basis=stored_basis,
            factor_basis_identity=basis_identity,
            thresholds=synthetic_thresholds,
            cpp_evaluator=None,
            migration=migration,
            epochs=1,
            qat_epochs=1,
            require_cpp=False,
            synthetic_schedule=True,
            quiet=True,
        )
        if (
            not trained.eligible
            or trained.selected_epoch != 1
            or not trained.history[0]["deploymentHealth"]["passed"]
            or not trained.history[0]["validationTargetsAccessed"]
            or trained.history[0]["targetAccessBeforeHealth"]
            ["validationTargetFieldsDecoded"]
            != 0
            or validation_loads != 1
        ):
            raise AssertionError("health-first QAT checkpoint selection failed")

        paths = _candidate_paths("G5A", root / "bundles")
        network_payload = initializer4.to_bytes()
        float_model = base.FloatNetwork.from_quantized(initializer4)
        float_payload = _float_checkpoint_bytes(float_model)
        manifest = {
            "network": _payload_identity(paths["network"], network_payload),
            "canonicalDeploymentFloat": _payload_identity(
                paths["canonical"], float_payload
            ),
            "optimizerShadow": _payload_identity(paths["shadow"], float_payload),
        }
        manifest_payload = _canonical_json(manifest)
        _publish_bundle(
            paths,
            {
                "network": network_payload,
                "canonical": float_payload,
                "shadow": float_payload,
                "manifest": manifest_payload,
            },
            manifest,
        )
        try:
            _publish_bundle(
                paths,
                {
                    "network": network_payload,
                    "canonical": float_payload,
                    "shadow": float_payload,
                    "manifest": manifest_payload,
                },
                manifest,
            )
        except FileExistsError:
            pass
        else:
            raise AssertionError("candidate bundle no-clobber test failed")

        interrupted_paths = _candidate_paths("G5B", root / "bundles")
        try:
            _publish_bundle(
                interrupted_paths,
                {
                    "network": network_payload,
                    "canonical": float_payload,
                    "shadow": float_payload,
                    "manifest": _canonical_json(
                        {
                            "network": _payload_identity(
                                interrupted_paths["network"], network_payload
                            ),
                            "canonicalDeploymentFloat": _payload_identity(
                                interrupted_paths["canonical"], float_payload
                            ),
                            "optimizerShadow": _payload_identity(
                                interrupted_paths["shadow"], float_payload
                            ),
                        }
                    ),
                },
                {
                    "network": _payload_identity(
                        interrupted_paths["network"], network_payload
                    ),
                    "canonicalDeploymentFloat": _payload_identity(
                        interrupted_paths["canonical"], float_payload
                    ),
                    "optimizerShadow": _payload_identity(
                        interrupted_paths["shadow"], float_payload
                    ),
                },
                interruption_hook=lambda stage: (
                    (_ for _ in ()).throw(RuntimeError("synthetic interruption"))
                    if stage == "before-commit"
                    else None
                ),
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("bundle interruption did not fire")
        if interrupted_paths["network"].parent.exists():
            raise AssertionError("interrupted bundle became visible")

        claim_root = root / "claim-recovery"
        owner = _claim_owner_record()
        dead_pid = 999_999_999
        if _pid_exists(dead_pid):
            raise AssertionError("synthetic stale-claim PID is unexpectedly live")
        dead_owner = {**owner, "pid": dead_pid}

        # The worker-lifetime lock is authoritative while held.  Recovery must
        # neither ignore nor remove a claim whose owner is still executing.
        locked_claim_path = claim_root / "locked.claim.json"
        locked_claim = {"case": "locked-dead-pid", "owner": dead_owner}
        locked_handle = _exclusive_locked_json(locked_claim_path, locked_claim)
        try:
            try:
                _recover_stale_claim_file(
                    locked_claim_path,
                    locked_claim,
                    blocked_outputs=(),
                )
            except RuntimeError as error:
                if "locked" not in str(error):
                    raise AssertionError(
                        "live claim failed for the wrong reason"
                    ) from error
            else:
                raise AssertionError("locked live claim was recovered")
        finally:
            locked_handle.release()
        if not locked_claim_path.is_file():
            raise AssertionError("live claim rejection removed the claim")
        locked_claim_path.unlink()

        # Even an unlocked claim is ambiguous if its recorded PID is live or
        # has been reused.  A missing lock alone never authorizes deletion.
        live_pid_claim_path = claim_root / "live-pid.claim.json"
        live_pid_claim = {"case": "live-pid", "owner": owner}
        live_pid_handle = _exclusive_locked_json(
            live_pid_claim_path, live_pid_claim
        )
        live_pid_handle.release()
        try:
            _recover_stale_claim_file(
                live_pid_claim_path,
                live_pid_claim,
                blocked_outputs=(),
            )
        except RuntimeError as error:
            if "PID is live" not in str(error):
                raise AssertionError(
                    "unlocked live-PID claim failed for the wrong reason"
                ) from error
        else:
            raise AssertionError("unlocked live-PID claim was recovered")
        if not live_pid_claim_path.is_file():
            raise AssertionError("live-PID rejection removed the claim")
        live_pid_claim_path.unlink()

        stale_claim_path = claim_root / "stale.claim.json"
        stale_claim = {"case": "stale", "owner": dead_owner}
        stale_handle = _exclusive_locked_json(stale_claim_path, stale_claim)
        stale_handle.release()
        stale_recovery = _recover_stale_claim_file(
            stale_claim_path,
            stale_claim,
            blocked_outputs=(),
        )
        if (
            stale_claim_path.exists()
            or not stale_recovery["lockWasUnowned"]
            or not stale_recovery["recordedPidWasAbsent"]
        ):
            raise AssertionError("authenticated stale claim was not recovered")

        other_host_claim_path = claim_root / "other-host.claim.json"
        other_host_claim = {
            "case": "other-host",
            "owner": {**dead_owner, "machine": owner["machine"] + ".other"},
        }
        other_host_handle = _exclusive_locked_json(
            other_host_claim_path, other_host_claim
        )
        other_host_handle.release()
        try:
            _recover_stale_claim_file(
                other_host_claim_path,
                other_host_claim,
                blocked_outputs=(),
            )
        except ValueError as error:
            if "another or ambiguous host" not in str(error):
                raise AssertionError(
                    "other-host claim failed for the wrong reason"
                ) from error
        else:
            raise AssertionError("other-host claim was recovered")
        if not other_host_claim_path.is_file():
            raise AssertionError("other-host rejection removed the claim")
        other_host_claim_path.unlink()

        tampered_claim_path = claim_root / "tampered.claim.json"
        tampered_claim = {"case": "actual", "owner": dead_owner}
        tampered_claim_handle = _exclusive_locked_json(
            tampered_claim_path, tampered_claim
        )
        tampered_claim_handle.release()
        authenticated_snapshot = {**tampered_claim, "case": "authenticated"}
        try:
            _recover_stale_claim_file(
                tampered_claim_path,
                authenticated_snapshot,
                blocked_outputs=(),
            )
        except ValueError as error:
            if "changed during stale recovery" not in str(error):
                raise AssertionError(
                    "tampered claim failed for the wrong reason"
                ) from error
        else:
            raise AssertionError("tampered claim was recovered")
        if not tampered_claim_path.is_file():
            raise AssertionError("tampered-claim rejection removed the claim")
        tampered_claim_path.unlink()

        blocked_claim_path = claim_root / "blocked.claim.json"
        blocked_claim = {"case": "blocked-output", "owner": dead_owner}
        blocked_claim_handle = _exclusive_locked_json(
            blocked_claim_path, blocked_claim
        )
        blocked_claim_handle.release()
        committed_output = claim_root / "committed-output.nnue"
        _exclusive_bytes(committed_output, b"committed")
        try:
            _recover_stale_claim_file(
                blocked_claim_path,
                blocked_claim,
                blocked_outputs=(committed_output,),
            )
        except FileExistsError:
            pass
        else:
            raise AssertionError("claim with committed output was recovered")
        if not blocked_claim_path.is_file():
            raise AssertionError("committed-output rejection removed the claim")
        blocked_claim_path.unlink()

        # Authenticate the complete production-shaped primary claim against a
        # plan identity, deterministic seeds, namespace, factor-basis record,
        # interpreter identity, and owner protocol before stale recovery.
        binding_plan_path = claim_root / "binding-plan.json"
        _exclusive_json(binding_plan_path, {"case": "claim-binding"})
        binding_output = _resolve(claim_root / "binding-output")
        binding_factor = {"case": "frozen-factor-basis"}
        binding_profile = {"seeds": {"training": 2026072306}}
        binding_plan = {
            "outputDirectory": str(binding_output),
            "factorBases": {"G5A": binding_factor},
        }
        binding_claim_path = _candidate_claim_path("G5A", binding_output)
        binding_claim = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "omega-nnue-king-state-v5-training-claim",
            "profileId": PROFILE_ID,
            "candidateId": "G5A",
            "plan": _identity(binding_plan_path),
            "outputDirectory": str(binding_output),
            "bundle": str(_candidate_bundle("G5A", binding_output)),
            "trainingSeed": _candidate_seed(
                binding_profile, "G5A", "candidate-training"
            ),
            "factorSeed": _candidate_seed(
                binding_profile, "G5A", "factor-basis"
            ),
            "factorBasis": binding_factor,
            "trainingPurpose": "primary-training",
            "createdUtc": _utc_now(),
            "targetFieldsDecoded": 0,
            "owner": dead_owner,
        }
        binding_handle = _exclusive_locked_json(
            binding_claim_path, binding_claim
        )
        binding_handle.release()
        authenticated_binding = _verify_primary_training_claim(
            binding_claim_path,
            candidate="G5A",
            plan=binding_plan,
            profile=binding_profile,
            plan_path=binding_plan_path,
        )
        if authenticated_binding != binding_claim:
            raise AssertionError("primary claim plan binding changed")
        _recover_stale_claim_file(
            binding_claim_path,
            authenticated_binding,
            blocked_outputs=(),
        )
        if binding_claim_path.exists():
            raise AssertionError("plan-bound stale claim remained visible")

        # Simulate a hard kill in the narrow window after the atomic candidate
        # bundle rename but before the worker's finally block removes its
        # lifetime claim.  Recovery preserves the authenticated complete bundle
        # and retires only the dead owner's unchanged claim.
        postcommit_output = _resolve(claim_root / "postcommit-output")
        postcommit_paths = _candidate_paths("G5C", postcommit_output)
        postcommit_manifest = {
            "network": _payload_identity(
                postcommit_paths["network"], network_payload
            ),
            "canonicalDeploymentFloat": _payload_identity(
                postcommit_paths["canonical"], float_payload
            ),
            "optimizerShadow": _payload_identity(
                postcommit_paths["shadow"], float_payload
            ),
        }
        postcommit_payloads = {
            "network": network_payload,
            "canonical": float_payload,
            "shadow": float_payload,
            "manifest": _canonical_json(postcommit_manifest),
        }
        postcommit_claim_path = _candidate_claim_path(
            "G5C", postcommit_output
        )
        postcommit_claim = {"case": "postcommit-crash", "owner": dead_owner}
        postcommit_handle = _exclusive_locked_json(
            postcommit_claim_path, postcommit_claim
        )
        _publish_bundle(
            postcommit_paths,
            postcommit_payloads,
            postcommit_manifest,
        )
        postcommit_handle.release()
        postcommit_identities = {
            key: _identity(postcommit_paths[key])
            for key in ("network", "canonical", "shadow", "manifest")
        }
        postcommit_recovery = _recover_stale_claim_file(
            postcommit_claim_path,
            postcommit_claim,
            blocked_outputs=(postcommit_paths["failure"],),
            committed_bundle=_candidate_bundle("G5C", postcommit_output),
            committed_output_identities=postcommit_identities,
        )
        if (
            postcommit_claim_path.exists()
            or not _candidate_bundle("G5C", postcommit_output).is_dir()
            or postcommit_recovery["recoveryMode"]
            != "completed-candidate-bundle"
            or not postcommit_recovery["completedCandidateBundleVerified"]
        ):
            raise AssertionError("post-commit primary claim recovery failed")

        mutated_output = _resolve(claim_root / "mutated-output")
        mutated_paths = _candidate_paths("G5C", mutated_output)
        mutated_manifest = {
            "network": _payload_identity(mutated_paths["network"], network_payload),
            "canonicalDeploymentFloat": _payload_identity(
                mutated_paths["canonical"], float_payload
            ),
            "optimizerShadow": _payload_identity(
                mutated_paths["shadow"], float_payload
            ),
        }
        mutated_payloads = {
            "network": network_payload,
            "canonical": float_payload,
            "shadow": float_payload,
            "manifest": _canonical_json(mutated_manifest),
        }
        mutated_claim_path = _candidate_claim_path("G5C", mutated_output)
        mutated_claim = {"case": "mutated-postcommit", "owner": dead_owner}
        mutated_handle = _exclusive_locked_json(
            mutated_claim_path, mutated_claim
        )
        _publish_bundle(mutated_paths, mutated_payloads, mutated_manifest)
        mutated_handle.release()
        authenticated_outputs = {
            key: _identity(mutated_paths[key])
            for key in ("network", "canonical", "shadow", "manifest")
        }
        mutated_paths["network"].write_bytes(network_payload + b"mutation")
        try:
            _recover_stale_claim_file(
                mutated_claim_path,
                mutated_claim,
                blocked_outputs=(mutated_paths["failure"],),
                committed_bundle=_candidate_bundle("G5C", mutated_output),
                committed_output_identities=authenticated_outputs,
            )
        except ValueError as error:
            if "output changed" not in str(error):
                raise AssertionError(
                    "mutated committed bundle failed for the wrong reason"
                ) from error
        else:
            raise AssertionError("mutated committed bundle was recovered")
        if not mutated_claim_path.is_file():
            raise AssertionError("bundle-mutation rejection removed the claim")
        mutated_claim_path.unlink()

        inventory_output = _resolve(claim_root / "inventory-output")
        inventory_paths = _candidate_paths("G5C", inventory_output)
        inventory_manifest = {
            "network": _payload_identity(
                inventory_paths["network"], network_payload
            ),
            "canonicalDeploymentFloat": _payload_identity(
                inventory_paths["canonical"], float_payload
            ),
            "optimizerShadow": _payload_identity(
                inventory_paths["shadow"], float_payload
            ),
        }
        inventory_payloads = {
            "network": network_payload,
            "canonical": float_payload,
            "shadow": float_payload,
            "manifest": _canonical_json(inventory_manifest),
        }
        inventory_claim_path = _candidate_claim_path("G5C", inventory_output)
        inventory_claim = {"case": "inventory-mutation", "owner": dead_owner}
        inventory_handle = _exclusive_locked_json(
            inventory_claim_path, inventory_claim
        )
        _publish_bundle(inventory_paths, inventory_payloads, inventory_manifest)
        inventory_handle.release()
        inventory_identities = {
            key: _identity(inventory_paths[key])
            for key in ("network", "canonical", "shadow", "manifest")
        }
        _exclusive_bytes(
            _candidate_bundle("G5C", inventory_output) / "unexpected.bin",
            b"mutation",
        )
        try:
            _recover_stale_claim_file(
                inventory_claim_path,
                inventory_claim,
                blocked_outputs=(inventory_paths["failure"],),
                committed_bundle=_candidate_bundle("G5C", inventory_output),
                committed_output_identities=inventory_identities,
            )
        except ValueError as error:
            if "inventory changed" not in str(error):
                raise AssertionError(
                    "bundle inventory mutation failed for the wrong reason"
                ) from error
        else:
            raise AssertionError("bundle inventory mutation was recovered")
        if not inventory_claim_path.is_file():
            raise AssertionError("inventory-mutation rejection removed the claim")
        inventory_claim_path.unlink()

        # A merely atomic-looking directory is not enough: the production
        # completed-bundle gate must invoke the full manifest verifier before
        # it can produce the immutable identity snapshot used above.
        synthetic_split_audit = {
            "rows": 48,
            "phaseRoots": {
                split: {phase: 1 for phase in PHASES} for split in SPLITS
            },
        }
        try:
            _verify_completed_primary_bundle(
                candidate="G5C",
                plan={"targetOpaqueCorpusAudit": synthetic_split_audit},
                profile={},
                plan_path=binding_plan_path,
                output_dir=postcommit_output,
            )
        except ValueError as error:
            if "manifest field inventory changed" not in str(error):
                raise AssertionError(
                    "invalid completed bundle failed for the wrong reason"
                ) from error
        else:
            raise AssertionError("invalid completed bundle passed full verification")

        # Exercise the other terminal worker state with a production-shaped,
        # selection-verifiable failure record.  The failure is published while
        # the lifetime claim is held, then the handle is released without claim
        # cleanup to model a hard kill immediately after the terminal write.
        failure_plan_path = claim_root / "failure-plan.json"
        _exclusive_json(failure_plan_path, {"case": "terminal-failure-plan"})
        failure_factor = {"case": "synthetic-failure-factor-basis"}
        failure_split_audit = {
            "rows": 48,
            "phaseRoots": {
                split: {phase: 1 for phase in PHASES} for split in SPLITS
            },
        }
        failure_plan = {
            "identities": {},
            "factorBases": {"G5B": failure_factor},
            "initializerMigration": migration,
            "targetOpaqueCorpusAudit": failure_split_audit,
        }
        failure_profile = {"seeds": {"training": 2026072306}}
        failure_split_rows = _planned_split_rows(failure_plan)
        failure_history: list[dict[str, Any]] = []
        for epoch in range(1, EPOCHS + 1):
            qat = epoch >= FIRST_QAT_EPOCH
            row: dict[str, Any] = {
                "epoch": epoch,
                "quantizationAware": qat,
                "optimizerSteps": epoch,
                "meanBatchLoss": 1.0,
                "rankingPairs": 1,
                "activationSettings": {
                    "penaltyWeight": ACTIVATION_PENALTY,
                    "targetActiveFraction": ACTIVATION_TARGET,
                    "temperature": ACTIVATION_TEMPERATURE,
                },
                "healthCheckedBeforeValidation": qat,
                "validationTargetsAccessed": False,
                "targetAccessBeforeHealth": _expected_prehealth_boundary(
                    failure_split_rows
                ),
            }
            if qat:
                row["deploymentHealth"] = {
                    "passed": False,
                    "targetFieldsDecoded": 0,
                }
                row["commonValidation"] = None
            failure_history.append(row)
        failure_record = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": FAILURE_KIND,
            "profileId": PROFILE_ID,
            "candidateId": "G5B",
            "trainingPurpose": "primary-training",
            "createdUtc": _utc_now(),
            "reason": "no QAT checkpoint passed every deployment-health gate",
            "recipe": CANDIDATE_RECIPES["G5B"],
            "trainingSeed": _candidate_seed(
                failure_profile, "G5B", "candidate-training"
            ),
            "factorSeed": _candidate_seed(
                failure_profile, "G5B", "factor-basis"
            ),
            "history": failure_history,
            "basis": failure_factor,
            "initializerMigration": migration,
            "inputs": {"plan": _identity(failure_plan_path)},
            "informationBoundary": {
                "targetFieldNames": list(TARGET_FIELDS_DECODED_PER_ROW),
                "trainTargetRowsDecoded": failure_split_rows["train"],
                "trainTargetsDecoded": failure_split_rows["train"]
                * len(TARGET_FIELDS_DECODED_PER_ROW),
                "trainHandcraftedScoresComputed": failure_split_rows["train"],
                "trainResidualTargetsComputed": failure_split_rows["train"],
                "validationTargetsDecoded": 0,
                "validationHandcraftedScoresComputed": 0,
                "validationResidualTargetsComputed": 0,
                "validationTargetsDecodedBeforeHealthPass": 0,
                "heldOutRowsWithheld": failure_split_rows["heldOut"],
                "heldOutTargetFieldsDecoded": 0,
                "heldOutMetricsComputed": False,
            },
        }
        terminal_failure_output = _resolve(claim_root / "terminal-failure-output")
        terminal_failure_paths = _candidate_paths("G5B", terminal_failure_output)
        terminal_failure_claim_path = _candidate_claim_path(
            "G5B", terminal_failure_output
        )
        terminal_failure_claim = {
            "case": "post-failure-commit-crash",
            "owner": dead_owner,
        }
        terminal_failure_handle = _exclusive_locked_json(
            terminal_failure_claim_path, terminal_failure_claim
        )
        _exclusive_json(terminal_failure_paths["failure"], failure_record)
        terminal_failure_handle.release()
        terminal_failure_identity = _verify_completed_primary_failure(
            candidate="G5B",
            plan=failure_plan,
            profile=failure_profile,
            plan_path=failure_plan_path,
            output_dir=terminal_failure_output,
        )
        terminal_failure_recovery = _recover_stale_claim_file(
            terminal_failure_claim_path,
            terminal_failure_claim,
            blocked_outputs=(
                _candidate_bundle("G5B", terminal_failure_output),
                *(terminal_failure_paths[key] for key in ("network", "canonical", "shadow", "manifest")),
            ),
            committed_failure=terminal_failure_paths["failure"],
            committed_output_identities=terminal_failure_identity,
        )
        if (
            terminal_failure_claim_path.exists()
            or not terminal_failure_paths["failure"].is_file()
            or terminal_failure_recovery["recoveryMode"]
            != "completed-candidate-failure"
            or not terminal_failure_recovery["completedCandidateFailureVerified"]
        ):
            raise AssertionError("post-failure primary claim recovery failed")

        tampered_failure_output = _resolve(claim_root / "tampered-failure-output")
        tampered_failure_paths = _candidate_paths("G5B", tampered_failure_output)
        tampered_failure_claim_path = _candidate_claim_path(
            "G5B", tampered_failure_output
        )
        tampered_failure_claim = {
            "case": "tampered-terminal-failure",
            "owner": dead_owner,
        }
        tampered_failure_handle = _exclusive_locked_json(
            tampered_failure_claim_path, tampered_failure_claim
        )
        _exclusive_json(tampered_failure_paths["failure"], failure_record)
        tampered_failure_handle.release()
        authenticated_failure = _verify_completed_primary_failure(
            candidate="G5B",
            plan=failure_plan,
            profile=failure_profile,
            plan_path=failure_plan_path,
            output_dir=tampered_failure_output,
        )
        tampered_failure_paths["failure"].write_bytes(
            tampered_failure_paths["failure"].read_bytes() + b" "
        )
        try:
            _recover_stale_claim_file(
                tampered_failure_claim_path,
                tampered_failure_claim,
                blocked_outputs=(
                    _candidate_bundle("G5B", tampered_failure_output),
                ),
                committed_failure=tampered_failure_paths["failure"],
                committed_output_identities=authenticated_failure,
            )
        except ValueError as error:
            if "failure changed" not in str(error):
                raise AssertionError(
                    "tampered terminal failure failed for the wrong reason"
                ) from error
        else:
            raise AssertionError("tampered terminal failure was recovered")
        if not tampered_failure_claim_path.is_file():
            raise AssertionError("failure-tamper rejection removed the claim")
        tampered_failure_claim_path.unlink()

        mixed_failure_output = _resolve(claim_root / "mixed-failure-output")
        mixed_failure_paths = _candidate_paths("G5B", mixed_failure_output)
        mixed_failure_claim_path = _candidate_claim_path(
            "G5B", mixed_failure_output
        )
        mixed_failure_claim = {"case": "mixed-terminal-state", "owner": dead_owner}
        mixed_failure_handle = _exclusive_locked_json(
            mixed_failure_claim_path, mixed_failure_claim
        )
        _exclusive_json(mixed_failure_paths["failure"], failure_record)
        mixed_failure_handle.release()
        mixed_failure_identity = _verify_completed_primary_failure(
            candidate="G5B",
            plan=failure_plan,
            profile=failure_profile,
            plan_path=failure_plan_path,
            output_dir=mixed_failure_output,
        )
        mixed_bundle = _candidate_bundle("G5B", mixed_failure_output)
        mixed_bundle.mkdir(parents=True)
        try:
            _recover_stale_claim_file(
                mixed_failure_claim_path,
                mixed_failure_claim,
                blocked_outputs=(mixed_bundle,),
                committed_failure=mixed_failure_paths["failure"],
                committed_output_identities=mixed_failure_identity,
            )
        except FileExistsError:
            pass
        else:
            raise AssertionError("mixed terminal failure state was recovered")
        if not mixed_failure_claim_path.is_file():
            raise AssertionError("mixed-state rejection removed the claim")
        mixed_failure_claim_path.unlink()

        invalid_failure_output = _resolve(claim_root / "invalid-failure-output")
        invalid_failure_paths = _candidate_paths("G5B", invalid_failure_output)
        _exclusive_bytes(invalid_failure_paths["failure"], b"{truncated")
        try:
            _verify_completed_primary_failure(
                candidate="G5B",
                plan=failure_plan,
                profile=failure_profile,
                plan_path=failure_plan_path,
                output_dir=invalid_failure_output,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid terminal failure passed verification")

        robustness_plan_path = root / "robustness-plan.json"
        _exclusive_json(robustness_plan_path, {"synthetic": True})
        robustness_output = _resolve(root / "robustness-output")
        robustness_factor = {"syntheticFactorBasis": True}
        robustness_profile = {"seeds": {"training": 2026072306}}
        robustness_plan = {
            "outputDirectory": str(robustness_output),
            "factorBases": {"G5A": robustness_factor},
        }
        robustness_selection = {"plan": _identity(robustness_plan_path)}
        robustness_claim_path = _candidate_claim_path(
            "G5A", robustness_output, robustness=True
        )
        _exclusive_json(
            robustness_claim_path,
            {
                "schemaVersion": SCHEMA_VERSION,
                "kind": "omega-nnue-king-state-v5-robustness-training-claim",
                "profileId": PROFILE_ID,
                "candidateId": "G5A",
                "plan": robustness_selection["plan"],
                "outputDirectory": str(robustness_output),
                "bundle": str(
                    _candidate_bundle(
                        "G5A", robustness_output, robustness=True
                    )
                ),
                "trainingSeed": _candidate_seed(
                    robustness_profile, "G5A", "robustness-training"
                ),
                "factorSeed": _candidate_seed(
                    robustness_profile, "G5A", "factor-basis"
                ),
                "factorBasis": robustness_factor,
                "trainingPurpose": "robustness-training",
                "createdUtc": _utc_now(),
                "targetFieldsDecoded": 0,
            },
        )
        _verify_robustness_training_claim(
            robustness_claim_path,
            candidate="G5A",
            selection=robustness_selection,
            plan=robustness_plan,
            profile=robustness_profile,
        )

        # Cross-split component reuse must fail before any targets are read.
        leaked = [dict(record) for record in _synthetic_records()]
        leaked[-1]["leakageComponentId"] = leaked[0]["leakageComponentId"]
        leaked_path = root / "leaked.jsonl"
        with leaked_path.open("w", encoding="utf-8", newline="\n") as stream:
            for record in leaked:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
        try:
            _load_feature_corpus(leaked_path, allow_test_alias=True)
        except ValueError:
            pass
        else:
            raise AssertionError("cross-split leakage mutation survived")

        # An escaped spelling of a second split key must be recognized as a
        # duplicate during the target-opaque routing pass.  The poison target
        # is never handed to the selected-row decoder.
        duplicate_record = dict(_synthetic_records()[-1])
        duplicate_record["deepScoreCpChildStm"] = "DO-NOT-DECODE"
        duplicate_line = json.dumps(duplicate_record, sort_keys=True).replace(
            '"split": "test"',
            '"split": "heldOut", "spl\\u0069t": "train"',
            1,
        )
        try:
            _routing_strings(duplicate_line, ("split",), "duplicate-mutation")
        except ValueError as error:
            if "duplicate JSON key 'split'" not in str(error):
                raise AssertionError(
                    "duplicate split failed for the wrong reason"
                ) from error
        else:
            raise AssertionError("escaped duplicate split survived opaque routing")
        duplicate_path = root / "duplicate-split.jsonl"
        with duplicate_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(duplicate_line + "\n")
        selected_decode_calls = 0
        original_selected_decoder = globals()["_strict_json_loads"]

        def forbidden_selected_decode(text: str, *, location: str) -> Any:
            nonlocal selected_decode_calls
            selected_decode_calls += 1
            raise AssertionError("duplicate row reached target decoder")

        globals()["_strict_json_loads"] = forbidden_selected_decode
        try:
            try:
                _load_decision_splits(
                    duplicate_path,
                    hce_evaluator=None,
                    allow_embedded_hce=True,
                    allow_test_alias=True,
                )
            except ValueError as error:
                if "duplicate JSON key 'split'" not in str(error):
                    raise AssertionError(
                        "duplicate loader mutation failed for the wrong reason"
                    ) from error
            else:
                raise AssertionError("duplicate split reached selected decoder")
        finally:
            globals()["_strict_json_loads"] = original_selected_decoder
        if selected_decode_calls != 0:
            raise AssertionError("duplicate row decoded target-bearing JSON")

        # Held-out targets require a pre-existing claim identity.  The clean
        # synthetic corpus then exercises whole-root, phase-stratified PCG64
        # resampling without touching any real generation-5 label.
        heldout_path = root / "heldout-clean.jsonl"
        with heldout_path.open("w", encoding="utf-8", newline="\n") as stream:
            for record in _synthetic_records():
                stream.write(json.dumps(record, sort_keys=True) + "\n")
        try:
            _load_decision_splits(
                heldout_path,
                hce_evaluator=None,
                allow_embedded_hce=True,
                allow_test_alias=True,
                decode_splits=("heldOut",),
            )
        except ValueError as error:
            if "access-claim" not in str(error):
                raise AssertionError(
                    "unclaimed held-out load failed for the wrong reason"
                ) from error
        else:
            raise AssertionError("held-out targets decoded without a claim")
        heldout_claim = root / "offline-access-claim.json"
        _exclusive_json(
            heldout_claim,
            {
                "status": "claimed-before-any-held-out-target-decode",
                "heldOutTargetFieldsDecodedAtClaim": 0,
            },
        )
        heldout = _load_decision_splits(
            heldout_path,
            hce_evaluator=None,
            allow_embedded_hce=True,
            allow_test_alias=True,
            decode_splits=("heldOut",),
            heldout_access_claim=_identity(heldout_claim),
        )
        heldout_indices = heldout.indices(SPLITS["heldOut"])
        selected_predictions = np.rint(
            heldout.target_cp[heldout_indices]
        ).astype(np.int32)
        i0_predictions = np.zeros(heldout_indices.size, dtype=np.int32)
        first_bootstrap = _whole_root_bootstrap(
            heldout,
            selected_predictions,
            i0_predictions,
            replicates=256,
            seed=OFFLINE_BOOTSTRAP_SEED,
        )
        second_bootstrap = _whole_root_bootstrap(
            heldout,
            selected_predictions,
            i0_predictions,
            replicates=256,
            seed=OFFLINE_BOOTSTRAP_SEED,
        )
        if (
            first_bootstrap != second_bootstrap
            or first_bootstrap["resamplingUnit"] != "whole four-child root"
            or first_bootstrap["lowerBound"] <= 0.0
        ):
            raise AssertionError("whole-root held-out bootstrap changed")
        synthetic_metrics = {
            "selectedPrimary": _common_metrics(
                heldout,
                SPLITS["heldOut"],
                selected_predictions,
            ),
            "I0": _common_metrics(
                heldout, SPLITS["heldOut"], i0_predictions
            ),
            "zeroResidual": _common_metrics(
                heldout, SPLITS["heldOut"], i0_predictions
            ),
        }
        synthetic_profile = {
            "postSelection": {
                "oneTimeHeldOut": {
                    "minimumRelativeHuberImprovementAgainstI0": 0.01,
                    "minimumRelativeHuberImprovementAgainstZeroResidual": 0.01,
                    "minimumPhaseMacroCpMaeImprovementVersusI0": 2,
                    "maximumAnyPhaseCpMaeRegressionVersusI0": 5,
                }
            }
        }
        if not _offline_gate(
            metrics=synthetic_metrics,
            bootstrap=first_bootstrap,
            profile=synthetic_profile,
        )["passed"]:
            raise AssertionError("synthetic held-out gate did not pass")
        if _candidate_bundle(
            "G5A", root / "bundles", robustness=True
        ) == _candidate_bundle("G5A", root / "bundles"):
            raise AssertionError("robustness bundle overlaps primary bundle")
        if _candidate_claim_path(
            "G5A", root / "bundles", robustness=True
        ) == _candidate_claim_path("G5A", root / "bundles"):
            raise AssertionError("robustness claim overlaps primary claim")

        return {
            "pythonNumpyRuntimeChecked": True,
            "status": "passed",
            "featureRows": audit["rows"],
            "decisionRoots": audit["roots"],
            "heldOutTargetFieldsDecoded": 0,
            "syntheticHeldOutTargetFieldsDecoded": heldout.target_fields_decoded,
            "factorRanksChecked": [4, 8],
            "rankingLossChecked": True,
            "activationRegularizerChecked": True,
            "clampAwareHealthChecked": True,
            "healthFirstCheckpointSelectionChecked": True,
            "atomicNoClobberBundlesChecked": True,
            "atomicWorkerClaimChecked": True,
            "plannedBasisReloadChecked": True,
            "interruptedPlanPublicationRecoveryChecked": True,
            "tamperedPartialBasisRejected": True,
            "stalePrimaryClaimRecoveryChecked": True,
            "livePrimaryClaimRecoveryRejected": True,
            "ambiguousPrimaryClaimRecoveryRejected": True,
            "tamperedPrimaryClaimRecoveryRejected": True,
            "committedOutputClaimRecoveryRejected": True,
            "primaryClaimPlanBindingChecked": True,
            "postCommitPrimaryClaimRecoveryChecked": True,
            "mutatedCompletedBundleRecoveryRejected": True,
            "completedBundleInventoryMutationRejected": True,
            "invalidCompletedBundleRejectedBySelectionVerifier": True,
            "postFailurePrimaryClaimRecoveryChecked": True,
            "tamperedCompletedFailureRecoveryRejected": True,
            "mixedTerminalFailureRecoveryRejected": True,
            "invalidCompletedFailureRejectedBySelectionVerifier": True,
            "duplicateAwareOpaqueRoutingChecked": True,
            "robustnessNamespaceIsolationChecked": True,
            "robustnessCrashResumeClaimChecked": True,
            "heldOutClaimBeforeDecodeChecked": True,
            "wholeRootPhaseBootstrapChecked": True,
        }


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--profile", type=_path, default=DEFAULT_PROFILE)
    plan.add_argument("--corpus", type=_path, default=DEFAULT_CORPUS)
    plan.add_argument(
        "--corpus-manifest", type=_path, default=DEFAULT_CORPUS_MANIFEST
    )
    plan.add_argument("--initializer", type=_path, required=True)
    plan.add_argument("--cpp-evaluator", type=_path, default=DEFAULT_CPP_EVALUATOR)
    plan.add_argument("--output-dir", type=_path, default=OUTPUT_DIR)
    plan.add_argument("--plan", type=_path, default=DEFAULT_PLAN)
    plan.add_argument("--selection", type=_path, default=DEFAULT_SELECTION)

    worker = subparsers.add_parser("train-worker")
    worker.add_argument("--plan", type=_path, required=True)
    worker.add_argument("--candidate", choices=CANDIDATES, required=True)
    worker.add_argument("--seed", type=int, required=True)
    worker.add_argument("--factor-seed", type=int, required=True)
    worker.add_argument("--output-dir", type=_path, required=True)
    worker.add_argument("--robustness", action="store_true")
    worker.add_argument("--quiet", action="store_true")

    run = subparsers.add_parser("run")
    run.add_argument("--plan", type=_path, default=DEFAULT_PLAN)
    run.add_argument("--candidate", choices=CANDIDATES)

    recover = subparsers.add_parser("recover-primary-claim")
    recover.add_argument("--plan", type=_path, default=DEFAULT_PLAN)
    recover.add_argument("--candidate", choices=CANDIDATES, required=True)

    select = subparsers.add_parser("select")
    select.add_argument("--plan", type=_path, default=DEFAULT_PLAN)

    robustness = subparsers.add_parser("run-robustness")
    robustness.add_argument(
        "--selection", type=_path, default=DEFAULT_SELECTION
    )

    offline = subparsers.add_parser("offline-evaluate")
    offline.add_argument(
        "--robustness", type=_path, default=DEFAULT_ROBUSTNESS
    )

    subparsers.add_parser("self-test")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "self-test":
        runtime_contract.verify_manifest(runtime_contract.DEFAULT_OUTPUT)
    if args.command == "plan":
        result = _prepare_plan(args)
    elif args.command == "train-worker":
        result = _run_worker(args)
    elif args.command == "run":
        _run(args)
        return 0
    elif args.command == "recover-primary-claim":
        result = _recover_primary_claim(args)
    elif args.command == "select":
        result = _select(args)
    elif args.command == "run-robustness":
        result = _run_robustness(args)
    elif args.command == "offline-evaluate":
        result = _offline_evaluate(args)
    elif args.command == "self-test":
        result = _self_test()
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
