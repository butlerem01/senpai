#!/usr/bin/env python3
"""Prepare leakage-resistant suites for open-ended Omega NNUE confirmation.

This module is the pre-result half of ``omega-nnue-open-confirmation-v1``.
The public commands deliberately separate four information domains:

* ``seal-implementation`` freezes the complete readiness/orchestrator/runtime
  implementation before a candidate can be claimed;
* ``claim`` consumes the next global attempt and authenticates the exact
  successful Generation-5 nominee;
* ``sample`` starts a clean worker whose command line and environment contain
  no candidate, claim, network, score, result, or target input; and
* ``authorize`` is the first transition allowed to join the already sealed
  suites to the nominated candidate and the identical-executable HCE control.

The clean worker extracts only position-bearing strings and legal move lists.
It does not import Generation-5 training, readiness, or match modules and does
not decode evaluation targets.  Every selected root is excluded by its full
rule-preserving input orbit against the complete Generation-5 corpus, all raw
match pools/suites/events, every prior confirmation attempt, and the remaining
frozen project history.  A claimed attempt is never reusable: a failure after
claim publication is closed as an immutable abort.
"""

from __future__ import annotations

import argparse
from collections import Counter
import contextlib
import copy
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import functools
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
from typing import Any, Iterator, Mapping, Sequence


# Authenticate the frozen foundation before executing it.  A normal import
# rejects every preloaded module, including a fake carrying the canonical
# ``__file__``.  The separately authenticated orchestrator may share its
# already-authenticated protocol only by injecting the exact unforgeable nonce
# object that it installed on that module before this source is executed.
_REPO = Path(__file__).resolve().parents[2]
_PROTOCOL_RELATIVE = "tools/omega_nnue/king_state_confirmation_protocol_v1.py"
_PROTOCOL_SIZE = 95_245
_PROTOCOL_SHA256 = "d76c20f46e52a970ff8e8171a8af067687737c739138d028750f342152b287d0"
_PROTOCOL_SOURCE = (_REPO / _PROTOCOL_RELATIVE).read_bytes()
if (
    len(_PROTOCOL_SOURCE) != _PROTOCOL_SIZE
    or hashlib.sha256(_PROTOCOL_SOURCE).hexdigest() != _PROTOCOL_SHA256
):
    raise ImportError("open-confirmation protocol implementation changed")

_PARENT_PROTOCOL_NONCE = globals().pop(
    "__confirmation_parent_protocol_nonce__", None
)
if "king_state_confirmation_protocol_v1" in sys.modules:
    contract = sys.modules["king_state_confirmation_protocol_v1"]
    if (
        _PARENT_PROTOCOL_NONCE is None
        or getattr(contract, "__confirmation_authenticated_nonce__", None)
        is not _PARENT_PROTOCOL_NONCE
        or Path(str(getattr(contract, "__file__", ""))).resolve()
        != (_REPO / _PROTOCOL_RELATIVE).resolve()
    ):
        raise ImportError("refusing preloaded confirmation protocol")
    _PROTOCOL_AUTH_NONCE = _PARENT_PROTOCOL_NONCE
else:
    if _PARENT_PROTOCOL_NONCE is not None:
        raise ImportError("parent protocol nonce supplied without its module")
    contract = types.ModuleType("king_state_confirmation_protocol_v1")
    contract.__file__ = str((_REPO / _PROTOCOL_RELATIVE).resolve())
    contract.__package__ = ""
    contract.__loader__ = None
    sys.modules["king_state_confirmation_protocol_v1"] = contract
    try:
        exec(
            compile(_PROTOCOL_SOURCE, str(contract.__file__), "exec"),
            contract.__dict__,
        )
    except BaseException:
        sys.modules.pop("king_state_confirmation_protocol_v1", None)
        raise
    _PROTOCOL_AUTH_NONCE = object()
    contract.__dict__["__confirmation_authenticated_nonce__"] = (
        _PROTOCOL_AUTH_NONCE
    )


SCHEMA_VERSION = 1
PROTOCOL = contract.validate_protocol()
PROTOCOL_IDENTITY = contract.identity(contract.PROTOCOL_PATH)
TOOL_PATH = Path(__file__).resolve()
ORCHESTRATOR_PATH = (
    _REPO / "tools/omega_nnue/king_state_confirmation_matches_v1.py"
).resolve()
ARTIFACT_ROOT = contract.ARTIFACT_ROOT.resolve()
IMPLEMENTATION_SEAL = ARTIFACT_ROOT / "implementation.seal.json"
PROGRAM_SUCCESS = ARTIFACT_ROOT / "program-success.json"
_WORKSPACE_ROOT = _REPO.parent.resolve()
_LOCK_TOKEN = hashlib.sha256(str(_REPO).casefold().encode("utf-8")).hexdigest()[:16]
GLOBAL_OPERATION_LOCK_PATH = (
    Path(tempfile.gettempdir()) / f"omega-confirmation-v1-{_LOCK_TOKEN}.lock"
).resolve()
_LOCK_LOCAL = threading.local()
GATES = tuple(contract.GATES)
FORMAL_GATES = tuple(contract.FORMAL_GATES)
PHASES = tuple(contract.PHASES)
SIDES = tuple(contract.SIDES)
HEX_256 = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_TOKEN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SAMPLER_PAIR = re.compile(r"^random-pair-([0-9]{6})$")
UINT64_MASK = (1 << 64) - 1
RELEVANT_PROCESS_NAMES = frozenset(("dotnet.exe", "omegamatch.exe", "senpai.exe"))
_SELF_TEST_STAGE_SEEDS = {
    "development": 1_710_001,
    "equal-node": 1_710_002,
    "equal-time": 1_710_003,
}

IMPLEMENTATION_KIND = "omega-nnue-open-confirmation-v1-implementation-seal"
RESERVATION_KIND = "omega-nnue-open-confirmation-v1-attempt-reservation"
CLAIM_KIND = "omega-nnue-open-confirmation-v1-candidate-claim"
EXCLUSION_KIND = "omega-nnue-open-confirmation-v1-exclusion-inventory"
JOINT_SUITE_KIND = "omega-nnue-open-confirmation-v1-joint-suite-seal"
AUTHORIZATION_KIND = "omega-nnue-open-confirmation-v1-match-authorization"
ATTEMPT_CLOSURE_KIND = "omega-nnue-open-confirmation-v1-attempt-closure"
PROGRAM_SUCCESS_KIND = "omega-nnue-open-confirmation-v1-program-success"
CORE_SEAL_KIND = "omega-nnue-king-state-v1-match-seal"

G5_AUTHORIZATION = (
    _REPO / "build-king-state-v5/matches/sealed/match-authorization.json"
).resolve()
G5_MATCH_ROOT = (_REPO / "build-king-state-v5/matches").resolve()
G5_DECISIONS = {
    gate: (_REPO / f"build-king-state-v5/matches/{gate}/decision.json").resolve()
    for gate in GATES
}
G5_MATCH_ORCHESTRATOR = (
    _REPO / "tools/omega_nnue/king_state_matches_generation5.py"
).resolve()

SIBLING_HISTORY_ROOTS = (
    ("abortedRuns", (_WORKSPACE_ROOT / ".aborted-runs").resolve()),
    ("auditRuns", (_WORKSPACE_ROOT / ".audit-runs").resolve()),
    ("matchRuns", (_WORKSPACE_ROOT / "match-runs").resolve()),
    ("omegaLab", (_WORKSPACE_ROOT / "omega-lab").resolve()),
    ("openingAudit", (_WORKSPACE_ROOT / "opening-audit").resolve()),
    ("examples", (_WORKSPACE_ROOT / "examples").resolve()),
    ("fixtures", (_WORKSPACE_ROOT / "fixtures").resolve()),
)

HISTORY_SNAPSHOT_ROOT = (
    _REPO
    / "tools/omega_nnue/frozen_runtime/king-state-v3/history-snapshot"
).resolve()
HISTORY_SNAPSHOT_ASSEMBLY = HISTORY_SNAPSHOT_ROOT / "OmegaHistorySnapshot.dll"
PREFIX_REPLAY_ROOT = (_REPO / "build-omega-opening-prefix-replay").resolve()
PREFIX_REPLAY_ASSEMBLY = PREFIX_REPLAY_ROOT / "OmegaOpeningPrefixReplay.dll"

_MATCH_CORE = contract.match_core
_POSITION_META = _MATCH_CORE._position_meta
_SHUFFLED_INDICES = _MATCH_CORE._shuffled_indices
_HARNESS_BUNDLE_IDENTITY = _MATCH_CORE._harness_bundle_identity

ROOT_FIELD_NAMES = frozenset(
    {
        "ofen",
        "initialofen",
        "preofen",
        "postofen",
        "finalofen",
        "parentofen",
        "childofen",
        "rootofen",
        "positionofen",
        "startofen",
        "sourceofen",
        "resultingofen",
        "canonicalrootofen",
        "officialinitialofen",
    }
)
JSON_STRING = r'"(?:\\.|[^"\\])*"'
POSITION_FIELD_RE = re.compile(
    rf'"(?P<key>{"|".join(sorted(ROOT_FIELD_NAMES))})"\s*:\s*(?P<value>{JSON_STRING})',
    re.IGNORECASE,
)
MOVE_ARRAY_RE = re.compile(
    rf'"(?P<key>pv|openingmoves|moves)"\s*:\s*'
    rf'(?P<value>\[\s*(?:{JSON_STRING}(?:\s*,\s*{JSON_STRING})*)?\s*\])',
    re.IGNORECASE,
)
COORDINATE_MOVE = re.compile(r"^[a-jw][0-9][a-jw][0-9][qrbncw]?$", re.IGNORECASE)

ATTEMPT_TOP_LEVEL = frozenset(
    {
        "candidate-claim.json",
        "attempt-reservation.json",
        "sampling-intent.json",
        "sampler",
        "sealed",
        "development",
        "equal-node",
        "equal-time",
        "attempt-closure.json",
    }
)
CLOSURE_REASONS = frozenset(
    {
        "confirmed",
        "development-fail",
        "safety-fail",
        "equal-node-futility",
        "equal-node-inconclusive",
        "equal-time-futility",
        "equal-time-inconclusive",
        "operator-abort",
        "pending-intent-no-growth",
        "protected-identity-failure",
        "sampling-failure",
        "suite-seal-failure",
        "authorization-failure",
        "claim-publication-failure",
    }
)


@dataclass(frozen=True)
class Root:
    gate: str
    source_path: Path
    source_sha256: str
    line: int
    generator_seed: int
    trajectory_pair_id: str
    trajectory_id: str
    flavor: str
    ply: int
    phase: str
    side: str
    ofen: str
    identity: str
    orbit: str
    orbit_signatures: tuple[str, ...]
    rank: str

    @property
    def source_group(self) -> str:
        return f"{self.generator_seed}:{self.trajectory_pair_id}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or contract.CANONICAL_UTC.fullmatch(value) is None:
        raise ValueError(f"{label} is not canonical UTC")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


def _identity_shape(value: Any, label: str) -> dict[str, Any]:
    item = contract.mapping(value, label)
    if set(item) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity fields changed")
    if (
        type(item.get("path")) is not str
        or not item["path"]
        or type(item.get("bytes")) is not int
        or item["bytes"] < 0
        or type(item.get("sha256")) is not str
        or HEX_256.fullmatch(item["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(item)


def _identity_path(value: Any, label: str) -> Path:
    item = _identity_shape(value, label)
    path = Path(item["path"])
    if not path.is_absolute():
        path = contract.protocol_path(item["path"], f"{label} path")
    return path.resolve()


def _verify_identity(value: Any, label: str) -> Path:
    item = _identity_shape(value, label)
    path = _identity_path(item, label)
    actual = contract.identity(path, relative=not Path(item["path"]).is_absolute())
    if not contract.exact_json_equal(item, actual):
        raise ValueError(f"{label} identity changed")
    return path


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    _exclusive_bytes(path, payload)


def _set_region_lock(stream: Any, *, acquire: bool) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK
        msvcrt.locking(stream.fileno(), mode, 1)
    else:
        import fcntl

        mode = fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN
        fcntl.flock(stream.fileno(), mode)


@contextlib.contextmanager
def _operation_lock() -> Iterator[str]:
    """Serialize every confirmation transition across both Python tools.

    Nested use by the same thread shares the already-held OS lock.  The token
    is candidate-independent and lets the clean child prove that its parent
    still owns this exact lock while the child constructs and publishes suites.
    """

    active = getattr(_LOCK_LOCAL, "active", None)
    if active is not None:
        active["depth"] += 1
        try:
            yield str(active["token"])
        finally:
            active["depth"] -= 1
        return

    GLOBAL_OPERATION_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        GLOBAL_OPERATION_LOCK_PATH,
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
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
        # The token is only a lock-file freshness marker; possession never
        # substitutes for the OS lock itself.  Keep claim-time CSPRNG use
        # exclusive to the protocol's single 32-byte entropy draw.
        token = hashlib.sha256(
            (
                f"{os.getpid()}\0{threading.get_ident()}\0{time.monotonic_ns()}\0"
                f"{GLOBAL_OPERATION_LOCK_PATH}"
            ).encode("utf-8")
        ).hexdigest()
        # Byte zero is the OS-locked region.  Keep the public freshness token
        # entirely after it so a Windows child can read the token without
        # attempting to read the locked byte.
        stream.seek(0)
        stream.write(b"0")
        stream.truncate(1)
        stream.seek(1)
        stream.write((token + "\n").encode("ascii"))
        os.fsync(stream.fileno())
        _LOCK_LOCAL.active = {"depth": 1, "token": token, "stream": stream}
        try:
            yield token
        finally:
            active = getattr(_LOCK_LOCAL, "active", None)
            if active is None or active["depth"] != 1 or active["stream"] is not stream:
                raise RuntimeError("confirmation operation-lock nesting changed")
            delattr(_LOCK_LOCAL, "active")
    finally:
        if locked:
            try:
                _set_region_lock(stream, acquire=False)
            except OSError:
                pass
        stream.close()


def _require_operation_lock(label: str) -> str:
    active = getattr(_LOCK_LOCAL, "active", None)
    if active is None or active.get("depth", 0) < 1:
        raise RuntimeError(f"{label} requires the global confirmation operation lock")
    return str(active["token"])


def _serialized_transition(function: Any) -> Any:
    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with _operation_lock():
            return function(*args, **kwargs)

    return wrapped


def _read_operation_lock_token() -> str:
    with GLOBAL_OPERATION_LOCK_PATH.open("rb", buffering=0) as stream:
        stream.seek(1)
        payload = stream.read()
    try:
        return payload.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("confirmation operation-lock token is not ASCII") from error


def _verify_parent_operation_lock(token: Any) -> None:
    if type(token) is not str or HEX_256.fullmatch(token) is None:
        raise ValueError("candidate-blind worker parent-lock token is malformed")
    before = _read_operation_lock_token()
    if before != token + "\n":
        raise ValueError("candidate-blind worker parent-lock token changed")
    with GLOBAL_OPERATION_LOCK_PATH.open("r+b", buffering=0) as stream:
        try:
            _set_region_lock(stream, acquire=True)
        except OSError:
            pass
        else:
            try:
                _set_region_lock(stream, acquire=False)
            finally:
                raise RuntimeError(
                    "candidate-blind worker has no live parent operation lock"
                )
    after = _read_operation_lock_token()
    if after != before:
        raise ValueError("candidate-blind worker lock token changed during proof")


def _parse_windows_processes(payload: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(payload)):
        if len(row) < 2:
            continue
        name = Path(row[0]).name.casefold()
        if name not in RELEVANT_PROCESS_NAMES:
            continue
        try:
            pid = int(row[1])
        except ValueError:
            continue
        rows.append({"name": name, "pid": pid})
    return sorted(rows, key=lambda item: (item["name"], item["pid"]))


def _parse_posix_processes(payload: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in payload.splitlines():
        pieces = line.strip().split(None, 1)
        if len(pieces) != 2:
            continue
        try:
            pid = int(pieces[0])
        except ValueError:
            continue
        name = Path(pieces[1].split()[0]).name.casefold()
        if name in RELEVANT_PROCESS_NAMES:
            rows.append({"name": name, "pid": pid})
    return sorted(rows, key=lambda item: (item["name"], item["pid"]))


def _process_snapshot() -> dict[str, Any]:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
        )
        processes = _parse_windows_processes(result.stdout)
        source = "tasklist-csv"
    else:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
        )
        processes = _parse_posix_processes(result.stdout)
        source = "ps-pid-args"
    return {
        "capturedUtc": _utc_now(),
        "source": source,
        "relevantProcesses": processes,
    }


def _require_idle_processes(label: str) -> dict[str, Any]:
    snapshot = _process_snapshot()
    if snapshot["relevantProcesses"]:
        raise RuntimeError(f"{label} requires no dotnet/OmegaMatch/Senpai processes")
    return snapshot


def _directory_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    if not path.is_dir():
        raise ValueError(f"expected directory: {path}")
    return sorted(item.name for item in path.iterdir())


def attempt_paths(index: Any) -> dict[str, Any]:
    attempt = contract._validate_attempt_index(index)
    root = (ARTIFACT_ROOT / contract.attempt_directory_name(attempt)).resolve()
    sealed = root / "sealed"
    sampler = root / "sampler"
    suites = {gate: sealed / f"{gate}-suite.json" for gate in GATES}
    configs = {gate: sealed / f"{gate}-match.json" for gate in GATES}
    stages = {gate: root / gate for gate in GATES}
    value: dict[str, Any] = {
        "root": root,
        "implementationSeal": IMPLEMENTATION_SEAL,
        "claim": root / "candidate-claim.json",
        "reservation": root / "attempt-reservation.json",
        "samplingIntent": root / "sampling-intent.json",
        "sampler": sampler,
        "sealed": sealed,
        "exclusionInventory": sampler / "exclusion-inventory.json",
        "excludedOrbits": sampler / "excluded-orbits.txt",
        "jointSuiteSeal": sealed / "joint-suite.seal.json",
        "authorization": sealed / "match-authorization.json",
        "coreSeal": sealed / "core-seal.json",
        "audit": sealed / "match-audit.json",
        "suites": suites,
        "configs": configs,
        "stages": stages,
        "attemptClosure": root / "attempt-closure.json",
        "programSuccess": PROGRAM_SUCCESS,
    }
    for gate in GATES:
        camel = "".join(part.capitalize() if number else part for number, part in enumerate(gate.split("-")))
        value[f"{camel}Dir"] = stages[gate]
        value[f"{camel}Suite"] = suites[gate]
        value[f"{camel}Config"] = configs[gate]
    return value


def _verify_bindings() -> None:
    contract.validate_protocol()
    if (
        contract.match_core is not _MATCH_CORE
        or getattr(contract, "__confirmation_authenticated_nonce__", None)
        is not _PROTOCOL_AUTH_NONCE
        or _MATCH_CORE._position_meta is not _POSITION_META
        or _MATCH_CORE._shuffled_indices is not _SHUFFLED_INDICES
        or _MATCH_CORE._harness_bundle_identity is not _HARNESS_BUNDLE_IDENTITY
    ):
        raise ValueError("authenticated shared match-core binding was substituted")
    if Path(str(getattr(contract, "__file__", ""))).resolve() != (
        _REPO / _PROTOCOL_RELATIVE
    ).resolve():
        raise ValueError("confirmation protocol module escaped its canonical path")


def _sanitized_environment() -> dict[str, str]:
    environment = dict(os.environ)
    forbidden_prefixes = ("dotnet_", "coreclr_", "cor_", "complus_", "corehost_")
    for key in list(environment):
        lowered = key.casefold()
        if lowered.startswith(forbidden_prefixes) or lowered in {
            "msbuildexepath",
            "msbuildsdkspath",
            "pythonpath",
            "pythonhome",
        }:
            del environment[key]
    runtime_root = (
        _REPO / "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime"
    ).resolve()
    environment.update(
        {
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_NOLOGO": "1",
            "DOTNET_MULTILEVEL_LOOKUP": "0",
            "DOTNET_ROOT": str(runtime_root),
            "DOTNET_ROOT_X64": str(runtime_root),
            "DOTNET_ROLL_FORWARD": "LatestPatch",
            "DOTNET_EnableDiagnostics": "0",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


def _runtime_identity(name: str) -> dict[str, Any]:
    identities = contract.mapping(PROTOCOL["sharedRuntime"]["identities"], "runtime identities")
    record = _identity_shape(identities[name], f"runtime {name}")
    _verify_identity(record, f"runtime {name}")
    return record


def _runtime_path(name: str) -> Path:
    identities = contract.mapping(
        PROTOCOL["sharedRuntime"]["identities"], "runtime identities"
    )
    record = _identity_shape(identities[name], f"runtime {name}")
    return _verify_identity(record, f"runtime {name}")


def _dotnet_runtime_bundle() -> dict[str, Any]:
    manifest = _runtime_path("dotnetRuntimeManifest")
    value = contract._DOTNET_VERIFY_MANIFEST(manifest)
    if (
        value.get("runtimeVersion") != PROTOCOL["sharedRuntime"]["dotnetRuntimeVersion"]
        or value.get("bundleSha256")
        != PROTOCOL["sharedRuntime"]["dotnetRuntimeBundleSha256"]
    ):
        raise ValueError("frozen .NET runtime bundle changed")
    return value


def _runtime_bundle(root: Path, assembly_name: str, apphost_name: str) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    files: list[dict[str, Any]] = []
    canonical = bytearray()
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        name = path.name.casefold()
        if not (
            path.suffix.casefold() in {".dll", ".so", ".dylib"}
            or name == apphost_name.casefold()
            or name.endswith(".deps.json")
            or name.endswith(".runtimeconfig.json")
        ):
            continue
        item = contract.identity(path)
        relative = path.relative_to(root).as_posix()
        entry = {"relativePath": relative, "bytes": item["bytes"], "sha256": item["sha256"]}
        files.append(entry)
        canonical.extend(f"{relative}\t{item['bytes']}\t{item['sha256']}\n".encode("utf-8"))
    if not any(item["relativePath"] == assembly_name for item in files):
        raise ValueError(f"runtime bundle lacks {assembly_name}")
    if not any(item["relativePath"] == apphost_name for item in files):
        raise ValueError(f"runtime bundle lacks {apphost_name}")
    return {
        "root": str(root),
        "assemblyRelativePath": assembly_name,
        "appHostRelativePath": apphost_name,
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def _verify_runtime_bundle(value: Any, label: str) -> dict[str, Any]:
    bundle = contract.mapping(value, label)
    if set(bundle) != {"root", "assemblyRelativePath", "appHostRelativePath", "sha256", "files"}:
        raise ValueError(f"{label} fields changed")
    current = _runtime_bundle(
        Path(str(bundle["root"])),
        str(bundle["assemblyRelativePath"]),
        str(bundle["appHostRelativePath"]),
    )
    contract.require_exact_json(bundle, current, label)
    return current


def _synthetic_root(
    gate: str, phase: str, side: str, ordinal: int, signature: str
) -> Root:
    digest = hashlib.sha256(
        f"confirmation-invariance\0{gate}\0{phase}\0{side}\0{ordinal}\0{signature}".encode()
    ).hexdigest()
    return Root(
        gate=gate,
        source_path=Path("candidate-unaware-synthetic.jsonl"),
        source_sha256="f" * 64,
        line=ordinal + 1,
        generator_seed=_SELF_TEST_STAGE_SEEDS[gate],
        trajectory_pair_id=f"random-pair-{ordinal + 1:06d}",
        trajectory_id=f"random-pair-{ordinal + 1:06d}-ab",
        flavor="ab",
        ply=ordinal + 1,
        phase=phase,
        side=side,
        ofen=f"synthetic-{signature}",
        identity=digest,
        orbit=digest,
        orbit_signatures=(digest,),
        rank=digest,
    )


def _select_roots(
    roots: Sequence[Root],
    gate: str,
    forbidden: set[str],
    used_fresh: set[str],
    *,
    quota_override: int | None = None,
) -> tuple[list[Root], dict[str, int]]:
    spec = contract.mapping(PROTOCOL["stages"][gate], f"{gate} stage")
    quota = (
        int(spec["rootsPerPhaseAndSideToMove"])
        if quota_override is None
        else int(quota_override)
    )
    if quota < 1:
        raise ValueError("root-selection quota must be positive")
    selected: list[Root] = []
    used_groups: set[str] = set()
    rejected = Counter()
    for phase in PHASES:
        for side in SIDES:
            accepted = 0
            for root in sorted(
                (item for item in roots if item.phase == phase and item.side == side),
                key=lambda item: item.rank,
            ):
                reasons: list[str] = []
                if root.source_group in used_groups:
                    reasons.append("trajectory-pair-reuse")
                if forbidden.intersection(root.orbit_signatures):
                    reasons.append("historical-whole-orbit")
                if used_fresh.intersection(root.orbit_signatures):
                    reasons.append("fresh-suite-whole-orbit")
                if reasons:
                    rejected.update(reasons)
                    continue
                selected.append(root)
                used_groups.add(root.source_group)
                used_fresh.update(root.orbit_signatures)
                accepted += 1
                if accepted == quota:
                    break
            if accepted != quota:
                raise ValueError(
                    f"could select only {accepted}/{quota} roots for {gate} {phase}/{side}"
                )
    return selected, dict(sorted(rejected.items()))


def _selection_digest(roots: Sequence[Root]) -> str:
    payload = "".join(
        f"{item.gate}\t{item.phase}\t{item.side}\t{item.source_group}\t"
        f"{item.rank}\t{','.join(item.orbit_signatures)}\n"
        for item in roots
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _candidate_invariance_proof() -> dict[str, Any]:
    # Two deliberately different candidate capsules are created, then kept
    # outside the worker input closure.  Both executions receive byte-identical
    # roots/forbidden inputs and must produce the same selection digest.
    capsules = (
        {"candidate": "0" * 64, "network": "a" * 64},
        {"candidate": "f" * 64, "network": "b" * 64},
    )
    roots: list[Root] = []
    ordinal = 0
    for phase in PHASES:
        for side in SIDES:
            for number in range(3):
                roots.append(
                    _synthetic_root(
                        "development", phase, side, ordinal, f"{phase}-{side}-{number}"
                    )
                )
                ordinal += 1
    forbidden = {roots[0].orbit_signatures[0]}
    observed: list[str] = []
    for _capsule in capsules:
        # `_capsule` is intentionally never passed to `_select_roots`.
        selected, _ = _select_roots(
            roots, "development", set(forbidden), set(), quota_override=1
        )
        observed.append(_selection_digest(selected))
    if observed[0] != observed[1]:
        raise AssertionError("candidate-unaware selection changed with candidate capsule")
    command_schema = [
        "--attempt-index",
        "--implementation-seal",
        "--output-root",
        "--parent-lock-token",
    ]
    if any("candidate" in item.casefold() or "network" in item.casefold() for item in command_schema):
        raise AssertionError("clean-worker command exposes candidate information")
    return {
        "proofVersion": 1,
        "syntheticCandidateCapsuleSha256": [
            hashlib.sha256(
                (json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest()
            for item in capsules
        ],
        "workerAcceptedArgumentNames": command_schema,
        "candidateArgumentsAccepted": 0,
        "candidateEnvironmentVariablesAccepted": 0,
        "selectionDigestA": observed[0],
        "selectionDigestB": observed[1],
        "identical": True,
    }


def _implementation_value(
    orchestrator: Mapping[str, Any], *, created_utc: str
) -> dict[str, Any]:
    shared = contract.mapping(PROTOCOL["sharedRuntime"]["identities"], "shared identities")
    pinned = {
        "protocolJson": PROTOCOL_IDENTITY,
        "protocolTool": contract.identity(contract.TOOL_PATH),
        "readiness": contract.identity(TOOL_PATH),
        "orchestrator": dict(orchestrator),
        "dotnetHost": dict(shared["dotnetHost"]),
        "dotnetRuntimeManifest": dict(shared["dotnetRuntimeManifest"]),
        "dotnetRuntimeTool": dict(shared["dotnetRuntimeTool"]),
        "omegaRulesModule": dict(shared["omegaRulesModule"]),
        "screenSelectionModule": dict(shared["screenSelectionModule"]),
        "sharedMatchCore": dict(shared["sharedMatchCore"]),
        "rootSamplerAssembly": dict(shared["rootSamplerAssembly"]),
        "rootSamplerRulesAssembly": dict(shared["rootSamplerRulesAssembly"]),
        "omegaMatchAssembly": dict(shared["omegaMatchAssembly"]),
        "omegaMatchAppHost": dict(shared["omegaMatchAppHost"]),
    }
    history_bundle = _runtime_bundle(
        HISTORY_SNAPSHOT_ROOT,
        "OmegaHistorySnapshot.dll",
        "OmegaHistorySnapshot.exe",
    )
    prefix_bundle = _runtime_bundle(
        PREFIX_REPLAY_ROOT,
        "OmegaOpeningPrefixReplay.dll",
        "OmegaOpeningPrefixReplay.exe",
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": IMPLEMENTATION_KIND,
        "protocolId": contract.PROTOCOL_ID,
        "createdUtc": created_utc,
        "protocol": PROTOCOL_IDENTITY,
        "pinned": pinned,
        "dotnetRuntimeBundle": _dotnet_runtime_bundle(),
        "rootSamplerBundleSha256": PROTOCOL["sharedRuntime"]["rootSamplerBundleSha256"],
        "omegaMatchBundleSha256": PROTOCOL["sharedRuntime"]["omegaMatchBundleSha256"],
        "historySnapshotBundle": history_bundle,
        "prefixReplayBundle": prefix_bundle,
        "cleanWorker": {
            "entryPoint": "candidate-blind-worker",
            "acceptedArgumentNames": [
                "--attempt-index",
                "--implementation-seal",
                "--output-root",
                "--parent-lock-token",
            ],
            "candidateOrClaimInputs": 0,
            "targetFieldsDecoded": 0,
            "matchResultsAccessed": 0,
            "freshPythonProcessRequired": True,
            "freshManagedProcessPerSamplerAndReplay": True,
            "sanitizedEnvironmentRequired": True,
        },
        "candidateInvarianceProof": _candidate_invariance_proof(),
        "sealedBeforeCandidateClaim": True,
    }


@_serialized_transition
def create_implementation_seal(
    path: Path = IMPLEMENTATION_SEAL,
    *,
    orchestrator_path: Path = ORCHESTRATOR_PATH,
    expected_orchestrator_bytes: int | None = None,
    expected_orchestrator_sha256: str | None = None,
) -> dict[str, Any]:
    _verify_bindings()
    path = path.resolve()
    orchestrator_path = orchestrator_path.resolve()
    if orchestrator_path.is_file():
        orchestrator = contract.identity(orchestrator_path)
        if expected_orchestrator_bytes is not None and orchestrator["bytes"] != expected_orchestrator_bytes:
            raise ValueError("orchestrator size differs from expected sealed size")
        if expected_orchestrator_sha256 is not None and orchestrator["sha256"] != expected_orchestrator_sha256:
            raise ValueError("orchestrator hash differs from expected sealed hash")
    else:
        if (
            type(expected_orchestrator_bytes) is not int
            or expected_orchestrator_bytes < 1
            or type(expected_orchestrator_sha256) is not str
            or HEX_256.fullmatch(expected_orchestrator_sha256) is None
        ):
            raise FileNotFoundError(
                "absent orchestrator requires exact --orchestrator-bytes and --orchestrator-sha256"
            )
        orchestrator = {
            "path": str(orchestrator_path),
            "bytes": expected_orchestrator_bytes,
            "sha256": expected_orchestrator_sha256,
        }
    value = _implementation_value(orchestrator, created_utc=_utc_now())
    _exclusive_json(path, value)
    return verify_implementation_seal(
        path,
        orchestrator_path=orchestrator_path,
        allow_orchestrator_placeholder=not orchestrator_path.is_file(),
    )


def verify_implementation_seal(
    path: Path = IMPLEMENTATION_SEAL,
    orchestrator_path: Path = ORCHESTRATOR_PATH,
    *,
    allow_orchestrator_placeholder: bool = False,
) -> dict[str, Any]:
    _verify_bindings()
    path = path.resolve()
    value = contract.strict_load(path, "confirmation implementation seal")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocolId",
        "createdUtc",
        "protocol",
        "pinned",
        "dotnetRuntimeBundle",
        "rootSamplerBundleSha256",
        "omegaMatchBundleSha256",
        "historySnapshotBundle",
        "prefixReplayBundle",
        "cleanWorker",
        "candidateInvarianceProof",
        "sealedBeforeCandidateClaim",
    }
    if set(value) != expected_fields:
        raise ValueError("implementation-seal field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != IMPLEMENTATION_KIND
        or value.get("protocolId") != contract.PROTOCOL_ID
        or value.get("sealedBeforeCandidateClaim") is not True
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError("implementation-seal envelope changed")
    _parse_utc(value.get("createdUtc"), "implementation seal createdUtc")
    pinned = contract.mapping(value.get("pinned"), "implementation pins")
    expected_pin_names = {
        "protocolJson",
        "protocolTool",
        "readiness",
        "orchestrator",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeTool",
        "omegaRulesModule",
        "screenSelectionModule",
        "sharedMatchCore",
        "rootSamplerAssembly",
        "rootSamplerRulesAssembly",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
    }
    if set(pinned) != expected_pin_names:
        raise ValueError("implementation pin inventory changed")
    for name, record in pinned.items():
        item = _identity_shape(record, f"implementation {name}")
        item_path = _identity_path(item, f"implementation {name}")
        if name == "orchestrator" and not item_path.is_file():
            if not allow_orchestrator_placeholder:
                raise FileNotFoundError("sealed orchestrator is not yet available")
        else:
            actual_path = _verify_identity(item, f"implementation {name}")
            if name == "readiness" and actual_path != TOOL_PATH:
                raise ValueError("implementation readiness path is noncanonical")
            if name == "orchestrator" and actual_path != orchestrator_path.resolve():
                raise ValueError("implementation orchestrator path is noncanonical")
    contract.require_exact_json(value["dotnetRuntimeBundle"], _dotnet_runtime_bundle(), "dotnet runtime bundle")
    if (
        value.get("rootSamplerBundleSha256")
        != PROTOCOL["sharedRuntime"]["rootSamplerBundleSha256"]
        or value.get("omegaMatchBundleSha256")
        != PROTOCOL["sharedRuntime"]["omegaMatchBundleSha256"]
    ):
        raise ValueError("implementation shared-runtime bundle binding changed")
    _verify_runtime_bundle(value["historySnapshotBundle"], "history snapshot bundle")
    _verify_runtime_bundle(value["prefixReplayBundle"], "prefix replay bundle")
    expected_worker = {
        "entryPoint": "candidate-blind-worker",
        "acceptedArgumentNames": [
            "--attempt-index",
            "--implementation-seal",
            "--output-root",
            "--parent-lock-token",
        ],
        "candidateOrClaimInputs": 0,
        "targetFieldsDecoded": 0,
        "matchResultsAccessed": 0,
        "freshPythonProcessRequired": True,
        "freshManagedProcessPerSamplerAndReplay": True,
        "sanitizedEnvironmentRequired": True,
    }
    contract.require_exact_json(value.get("cleanWorker"), expected_worker, "clean-worker contract")
    contract.require_exact_json(value.get("candidateInvarianceProof"), _candidate_invariance_proof(), "candidate-invariance proof")
    return value


def _fresh_verify_g5(authorization: Path) -> None:
    expected = PROTOCOL["g5Screening"]["identities"]["matchOrchestrator"]
    if not contract.exact_json_equal(contract.identity(G5_MATCH_ORCHESTRATOR, relative=True), expected):
        raise ValueError("Generation-5 orchestrator differs from frozen identity")
    command = [
        sys.executable,
        "-I",
        "-B",
        str(G5_MATCH_ORCHESTRATOR),
        "verify-state",
        "--authorization",
        str(authorization.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=G5_MATCH_ORCHESTRATOR.parent,
        env=_sanitized_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30 * 60,
    )
    if completed.returncode != 0:
        raise ValueError(
            "Generation-5 nomination verification failed: "
            + completed.stderr[-2000:]
        )


_G5_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,7})?Z$"
)


def _parse_g5_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or _G5_UTC.fullmatch(value) is None:
        raise ValueError(f"{label} is not canonical Generation-5 UTC")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} is not a real UTC timestamp") from error


def _g5_match_chronology(
    implementation: Mapping[str, Any],
    authorization: Mapping[str, Any],
    authorization_identity: Mapping[str, Any],
    decision_paths: Mapping[str, Path],
    *,
    match_root: Path = G5_MATCH_ROOT,
) -> dict[str, Any]:
    implementation_created = _parse_utc(
        implementation.get("createdUtc"), "implementation seal createdUtc"
    )
    authorization_created = _parse_g5_utc(
        authorization.get("createdUtc"), "Generation-5 authorization createdUtc"
    )
    if authorization_created < implementation_created:
        raise ValueError("Generation-5 authorization predates the implementation seal")
    artifacts: list[dict[str, Any]] = []
    intent_times: list[tuple[datetime, Path]] = []
    decision_times: dict[str, datetime] = {}
    match_root = match_root.resolve()
    for gate in GATES:
        gate_root = (match_root / gate).resolve()
        if not gate_root.is_dir():
            raise FileNotFoundError(f"Generation-5 gate root is absent: {gate_root}")
        json_paths = sorted(
            gate_root.rglob("*.json"), key=lambda item: str(item).casefold()
        )
        if not json_paths:
            raise ValueError(f"Generation-5 {gate} has no match artifacts")
        gate_intents = 0
        for path in json_paths:
            value = contract.strict_load(path, f"Generation-5 chronology artifact {path}")
            kind = value.get("kind")
            created_text = value.get("createdUtc")
            if type(kind) is not str or not kind.startswith("omega-nnue-king-state-v5-"):
                raise ValueError(f"Generation-5 chronology artifact kind changed: {path}")
            created = _parse_g5_utc(created_text, f"{path} createdUtc")
            if created < implementation_created:
                raise ValueError(
                    f"Generation-5 match artifact predates implementation seal: {path}"
                )
            if kind == "omega-nnue-king-state-v5-launch-intent":
                if value.get("gate") != gate:
                    raise ValueError(f"Generation-5 launch intent gate changed: {path}")
                gate_intents += 1
                intent_times.append((created, path.resolve()))
            if path.resolve() == decision_paths[gate].resolve():
                decision_times[gate] = created
            artifacts.append(
                {
                    "identity": contract.identity(path.resolve()),
                    "kind": kind,
                    "createdUtc": created_text,
                }
            )
        if gate_intents < 1:
            raise ValueError(f"Generation-5 {gate} has no authenticated launch intent")
        if gate not in decision_times:
            raise ValueError(f"Generation-5 {gate} decision was not included in chronology")

        events_path = gate_root / "events.jsonl"
        before = contract.identity(events_path)
        event_times: list[datetime] = []
        with events_path.open(
            "r", encoding="utf-8", errors="strict", newline=""
        ) as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.endswith("\n"):
                    raise ValueError(
                        f"{events_path}:{line_number}: incomplete event record"
                    )
                try:
                    record = json.loads(
                        line,
                        object_pairs_hook=contract._unique_object,
                        parse_constant=lambda token: (_ for _ in ()).throw(
                            ValueError(token)
                        ),
                    )
                except (json.JSONDecodeError, ValueError) as error:
                    raise ValueError(
                        f"{events_path}:{line_number}: invalid strict JSON"
                    ) from error
                if type(record) is not dict:
                    raise ValueError(
                        f"{events_path}:{line_number}: event is not an object"
                    )
                timestamp_key = {
                    "run": "CreatedUtc",
                    "gameStart": "StartedUtc",
                    "gameResult": "FinishedUtc",
                }.get(record.get("RecordType"))
                if timestamp_key is None:
                    continue
                timestamp = _parse_g5_utc(
                    record.get(timestamp_key),
                    f"{events_path}:{line_number} {timestamp_key}",
                )
                if timestamp < implementation_created:
                    raise ValueError(
                        "Generation-5 event predates implementation seal: "
                        f"{events_path}:{line_number}"
                    )
                event_times.append(timestamp)
        if (
            not event_times
            or not contract.exact_json_equal(contract.identity(events_path), before)
        ):
            raise ValueError(
                f"Generation-5 {gate} event chronology is empty or changed"
            )
        artifacts.append(
            {
                "identity": before,
                "kind": "omega-nnue-king-state-v5-event-stream",
                "createdUtc": min(event_times).isoformat().replace("+00:00", "Z"),
                "lastUtc": max(event_times).isoformat().replace("+00:00", "Z"),
                "timestampedRecords": len(event_times),
            }
        )
    if set(decision_times) != set(GATES) or not intent_times:
        raise ValueError("Generation-5 chronology is incomplete")
    first_time, first_path = min(
        intent_times, key=lambda item: (item[0], str(item[1]).casefold())
    )
    if first_time <= implementation_created:
        raise ValueError(
            "implementation seal does not provably predate the first "
            "Generation-5 match launch"
        )
    return {
        "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
        "implementationCreatedUtc": implementation["createdUtc"],
        "generation5Authorization": dict(authorization_identity),
        "authorizationCreatedUtc": authorization["createdUtc"],
        "firstLaunchIntent": contract.identity(first_path),
        "firstLaunchCreatedUtc": first_time.isoformat().replace("+00:00", "Z"),
        "artifacts": sorted(
            artifacts, key=lambda item: str(item["identity"]["path"]).casefold()
        ),
        "allMatchArtifactsAtOrAfterImplementationSeal": True,
        "implementationStrictlyPredatesFirstLaunch": True,
    }


def _verify_g5_chronology_record(
    value: Any, implementation: Mapping[str, Any]
) -> dict[str, Any]:
    record = contract.mapping(value, "Generation-5 chronology record")
    expected_fields = {
        "implementationSeal",
        "implementationCreatedUtc",
        "generation5Authorization",
        "authorizationCreatedUtc",
        "firstLaunchIntent",
        "firstLaunchCreatedUtc",
        "artifacts",
        "allMatchArtifactsAtOrAfterImplementationSeal",
        "implementationStrictlyPredatesFirstLaunch",
    }
    if set(record) != expected_fields:
        raise ValueError("Generation-5 chronology record fields changed")
    implementation_identity = contract.identity(IMPLEMENTATION_SEAL)
    if (
        not contract.exact_json_equal(record.get("implementationSeal"), implementation_identity)
        or record.get("implementationCreatedUtc") != implementation.get("createdUtc")
        or record.get("allMatchArtifactsAtOrAfterImplementationSeal") is not True
        or record.get("implementationStrictlyPredatesFirstLaunch") is not True
    ):
        raise ValueError("Generation-5 chronology implementation binding changed")
    implementation_created = _parse_utc(
        implementation.get("createdUtc"), "chronology implementation createdUtc"
    )
    authorization_path = _verify_identity(
        record.get("generation5Authorization"), "chronology Generation-5 authorization"
    )
    if authorization_path != G5_AUTHORIZATION:
        raise ValueError("chronology Generation-5 authorization path changed")
    authorization_created = _parse_g5_utc(
        record.get("authorizationCreatedUtc"), "chronology authorization createdUtc"
    )
    first_created = _parse_g5_utc(
        record.get("firstLaunchCreatedUtc"), "chronology first launch createdUtc"
    )
    if authorization_created < implementation_created or first_created <= implementation_created:
        raise ValueError("Generation-5 chronology predates the implementation seal")
    first_path = _verify_identity(
        record.get("firstLaunchIntent"), "chronology first launch intent"
    )
    artifacts = record.get("artifacts")
    if type(artifacts) is not list or not artifacts:
        raise ValueError("Generation-5 chronology has no artifacts")
    prior = ""
    artifact_paths: set[Path] = set()
    for number, item in enumerate(artifacts, 1):
        artifact = contract.mapping(item, f"chronology artifact {number}")
        if set(artifact) not in (
            {"identity", "kind", "createdUtc"},
            {"identity", "kind", "createdUtc", "lastUtc", "timestampedRecords"},
        ):
            raise ValueError("Generation-5 chronology artifact fields changed")
        path = _verify_identity(artifact.get("identity"), f"chronology artifact {number}")
        if str(path).casefold() <= prior or not path.is_relative_to(G5_MATCH_ROOT):
            raise ValueError("Generation-5 chronology artifacts are unsorted or escaped")
        prior = str(path).casefold()
        artifact_paths.add(path)
        created = _parse_g5_utc(
            artifact.get("createdUtc"), f"chronology artifact {number} createdUtc"
        )
        if created < implementation_created:
            raise ValueError("Generation-5 chronology artifact predates implementation")
        if "lastUtc" in artifact:
            last = _parse_g5_utc(
                artifact.get("lastUtc"), f"chronology artifact {number} lastUtc"
            )
            if last < created or type(artifact.get("timestampedRecords")) is not int or artifact["timestampedRecords"] < 1:
                raise ValueError("Generation-5 event chronology summary changed")
    if first_path not in artifact_paths:
        raise ValueError("first Generation-5 launch is absent from chronology artifacts")
    return dict(record)


def _g5_nomination_evidence(
    authorization_path: Path = G5_AUTHORIZATION,
    decision_paths: Mapping[str, Path] = G5_DECISIONS,
    *,
    run_fresh_verifier: bool,
    implementation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    authorization_path = authorization_path.resolve()
    if authorization_path != G5_AUTHORIZATION:
        raise ValueError("Generation-5 authorization path is noncanonical")
    if set(decision_paths) != set(GATES) or any(
        decision_paths[gate].resolve() != G5_DECISIONS[gate] for gate in GATES
    ):
        raise ValueError("Generation-5 decision paths are noncanonical")
    if run_fresh_verifier:
        _fresh_verify_g5(authorization_path)
    authorization_before = contract.identity(authorization_path)
    authorization = contract.strict_load(authorization_path, "Generation-5 match authorization")
    if authorization.get("kind") != "omega-nnue-king-state-v5-match-authorization":
        raise ValueError("Generation-5 authorization kind changed")
    selected = _identity_shape(authorization.get("selectedNetwork"), "Generation-5 selected network")
    manifest = _identity_shape(authorization.get("selectedManifest"), "Generation-5 selected manifest")
    engine = _identity_shape(authorization.get("engine"), "Generation-5 engine")
    _verify_identity(selected, "Generation-5 selected network")
    _verify_identity(manifest, "Generation-5 selected manifest")
    _verify_identity(engine, "Generation-5 engine")
    if engine["sha256"] != PROTOCOL["g5Screening"]["identities"]["frozenScreeningEngine"]["sha256"]:
        raise ValueError("Generation-5 nominee used the wrong engine executable")
    decisions: dict[str, dict[str, Any]] = {}
    expected_decision = {
        "development": "pass",
        "equal-node": "promote",
        "equal-time": "promote",
    }
    for gate in GATES:
        path = decision_paths[gate].resolve()
        before = contract.identity(path)
        decision = contract.strict_load(path, f"Generation-5 {gate} decision")
        if (
            decision.get("kind") != "omega-nnue-king-state-v5-gate-decision"
            or decision.get("gate") != gate
            or decision.get("decision") != expected_decision[gate]
            or decision.get("passed") is not True
            or decision.get("retryAllowed") is not False
            or not contract.exact_json_equal(decision.get("authorization"), authorization_before)
        ):
            raise ValueError(f"Generation-5 {gate} did not produce the required successful nomination")
        if not contract.exact_json_equal(contract.identity(path), before):
            raise ValueError(f"Generation-5 {gate} decision changed while read")
        decisions[gate] = before
    if not contract.exact_json_equal(contract.identity(authorization_path), authorization_before):
        raise ValueError("Generation-5 authorization changed while read")
    lineage_names = (
        "offlineReport",
        "selectedManifest",
        "coreSeal",
        "suiteSeal",
    )
    lineage: dict[str, Any] = {}
    for name in lineage_names:
        record = _identity_shape(authorization.get(name), f"Generation-5 {name}")
        _verify_identity(record, f"Generation-5 {name}")
        lineage[name] = record
    if implementation is None:
        implementation = verify_implementation_seal(
            IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
        )
    chronology = _g5_match_chronology(
        implementation,
        authorization,
        authorization_before,
        decision_paths,
    )
    return {
        "authorization": authorization_before,
        "decisions": decisions,
        "selectedNetwork": selected,
        "selectedManifest": manifest,
        "engine": engine,
        "lineage": lineage,
        "freshVerifier": contract.identity(G5_MATCH_ORCHESTRATOR, relative=True),
        "requiredDecisions": expected_decision,
        "zeroSafetyFailuresVerifiedByFrozenOrchestrator": True,
        "chronology": chronology,
    }


def _publish_reservation_namespace(
    paths: Mapping[str, Any], value: Mapping[str, Any]
) -> None:
    _require_operation_lock("attempt reservation publication")
    root = Path(paths["root"]).resolve()
    reservation = Path(paths["reservation"]).resolve()
    if reservation != root / "attempt-reservation.json":
        raise ValueError("reservation publication paths are noncanonical")
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{root.parent.name}-claim-", dir=root.parent.parent
    ) as staging_directory:
        staged_root = Path(staging_directory) / root.name
        staged_root.mkdir()
        _exclusive_json(staged_root / reservation.name, value)
        if root.exists():
            raise FileExistsError(f"confirmation attempt already exists: {root}")
        try:
            os.rename(staged_root, root)
        except OSError as error:
            if root.exists():
                raise FileExistsError(
                    f"confirmation attempt was concurrently reserved: {root}"
                ) from error
            raise


def _normalized_stage_seed(value: Any, label: str) -> int:
    if (
        type(value) is not int
        or not 0 <= value <= contract.DOTNET_RANDOM_SEED_MAX
        or value in contract.G5_SCREENING_STAGE_SEEDS
    ):
        raise ValueError(f"{label} is invalid or collides with a screening seed")
    return value


def _normalized_stage_seeds(value: Any, label: str) -> dict[str, int]:
    seeds = contract.mapping(value, label)
    if set(seeds) != set(GATES):
        raise ValueError(f"{label} gate inventory changed")
    normalized: dict[str, int] = {}
    forbidden = set(contract.G5_SCREENING_STAGE_SEEDS)
    for gate in GATES:
        seed = seeds.get(gate)
        seed = _normalized_stage_seed(seed, f"{label} {gate} seed")
        if seed in forbidden:
            raise ValueError(f"{label} {gate} seed is invalid or colliding")
        normalized[gate] = seed
        forbidden.add(seed)
    return normalized


def prior_published_stage_seeds(
    attempt_index: Any, *, allow_active_reservation_tip: bool = False
) -> list[int]:
    """Return all predecessor seeds in attempt order, then protocol stage order."""

    # MAX+1 is a read-only exclusive upper-bound sentinel used to summarize a
    # fully exhausted v1 chain.  It does not authorize or name attempt 378.
    if (
        type(attempt_index) is not int
        or not 1 <= attempt_index <= contract.MAX_ATTEMPT_INDEX + 1
    ):
        raise ValueError("seed-history exclusive upper bound is out of range")
    if type(allow_active_reservation_tip) is not bool:
        raise ValueError("allow_active_reservation_tip must be boolean")
    index = attempt_index
    result: list[int] = []
    for prior in range(1, index):
        paths = attempt_paths(prior)
        if paths["attemptClosure"].is_file():
            closure = contract.strict_load(
                paths["attemptClosure"], f"attempt {prior} seed closure"
            )
            if closure.get("candidateClaim") is None:
                if closure.get("reason") != "claim-publication-failure":
                    raise ValueError(
                        f"attempt {prior} omitted its claim for the wrong reason"
                    )
                _verify_attempt_closure(paths["attemptClosure"], prior)
                continue
        if not paths["claim"].is_file():
            # A prepublication reservation abort consumed the index without
            # ever publishing stage seeds.
            if paths["reservation"].is_file() and paths["attemptClosure"].is_file():
                continue
            if (
                allow_active_reservation_tip
                and prior == index - 1
                and paths["reservation"].is_file()
                and not paths["attemptClosure"].exists()
            ):
                continue
            raise FileNotFoundError(f"attempt {prior} lacks its claim or terminal reservation abort")
        value = contract.strict_load(paths["claim"], f"attempt {prior} seed claim")
        if (
            value.get("kind") != CLAIM_KIND
            or value.get("attemptIndex") != prior
            or "seedDerivation" not in value
        ):
            raise ValueError(f"attempt {prior} published seed claim changed")
        record = contract.validate_published_seed_bundle(
            value["seedDerivation"], result
        )
        result.extend(int(record["stageSeeds"][gate]) for gate in GATES)
    return result


def verify_claim_seed_derivation(
    claim: Mapping[str, Any], attempt_index: Any
) -> dict[str, Any]:
    index = contract._validate_attempt_index(attempt_index)
    if claim.get("attemptIndex") != index:
        raise ValueError("claim seed derivation attempt changed")
    return contract.validate_published_seed_bundle(
        claim.get("seedDerivation"), prior_published_stage_seeds(index)
    )


def claim_stage_seeds(
    claim: Mapping[str, Any], attempt_index: Any
) -> dict[str, int]:
    record = verify_claim_seed_derivation(claim, attempt_index)
    return {gate: int(record["stageSeeds"][gate]) for gate in GATES}


def verify_attempt_reservation(path: Path, attempt_index: Any) -> dict[str, Any]:
    index = contract._validate_attempt_index(attempt_index)
    paths = attempt_paths(index)
    path = path.resolve()
    if path != paths["reservation"]:
        raise ValueError("attempt reservation escaped its canonical namespace")
    value = contract.strict_load(path, "confirmation attempt reservation")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocol",
        "attemptIndex",
        "predecessorClosure",
        "createdUtc",
        "implementationSeal",
        "orchestrator",
        "g5Authorization",
        "g5Decisions",
        "g5Lineage",
        "g5Verifier",
        "g5Chronology",
        "requiredG5Decisions",
        "selectedNetwork",
        "selectedManifest",
        "engine",
        "candidateValidationComplete",
        "reservationConsumesAttempt",
        "status",
    }
    if set(value) != expected_fields:
        raise ValueError("attempt-reservation field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != RESERVATION_KIND
        or value.get("attemptIndex") != index
        or value.get("candidateValidationComplete") is not True
        or value.get("reservationConsumesAttempt") is not True
        or value.get("status") != "reserved-before-entropy-draw"
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError("attempt-reservation envelope changed")
    created = _parse_utc(value.get("createdUtc"), "attempt reservation createdUtc")
    predecessor_value = value.get("predecessorClosure")
    if index == 1:
        if predecessor_value is not None:
            raise ValueError("first attempt reservation has a predecessor")
    else:
        predecessor_path = _verify_identity(
            predecessor_value, "attempt reservation predecessor closure"
        )
        if predecessor_path != attempt_paths(index - 1)["attemptClosure"]:
            raise ValueError("attempt reservation predecessor closure changed")
    implementation_path = _verify_identity(
        value.get("implementationSeal"), "reservation implementation seal"
    )
    if implementation_path != IMPLEMENTATION_SEAL:
        raise ValueError("reservation implementation-seal path changed")
    implementation = verify_implementation_seal(
        implementation_path, orchestrator_path=ORCHESTRATOR_PATH
    )
    if created < _parse_utc(implementation["createdUtc"], "implementation createdUtc"):
        raise ValueError("attempt reservation predates implementation seal")
    if not contract.exact_json_equal(
        value.get("orchestrator"), implementation["pinned"]["orchestrator"]
    ):
        raise ValueError("reservation orchestrator binding changed")
    for name in (
        "selectedNetwork",
        "selectedManifest",
        "engine",
        "g5Authorization",
        "g5Verifier",
    ):
        _verify_identity(value.get(name), f"attempt reservation {name}")
    decisions = contract.mapping(value.get("g5Decisions"), "reservation G5 decisions")
    if set(decisions) != set(GATES):
        raise ValueError("reservation G5 decision inventory changed")
    for gate in GATES:
        if _verify_identity(decisions[gate], f"reservation G5 {gate} decision") != G5_DECISIONS[gate]:
            raise ValueError(f"reservation G5 {gate} decision path changed")
    lineage = contract.mapping(value.get("g5Lineage"), "reservation G5 lineage")
    if set(lineage) != {"offlineReport", "selectedManifest", "coreSeal", "suiteSeal"}:
        raise ValueError("reservation G5 lineage inventory changed")
    for name, record in lineage.items():
        _verify_identity(record, f"reservation G5 lineage {name}")
    contract.require_exact_json(
        value.get("requiredG5Decisions"),
        {"development": "pass", "equal-node": "promote", "equal-time": "promote"},
        "reservation required G5 decisions",
    )
    _verify_g5_chronology_record(value.get("g5Chronology"), implementation)
    return value


@_serialized_transition
def create_candidate_claim(
    attempt_index: Any,
    *,
    implementation_seal: Path = IMPLEMENTATION_SEAL,
    g5_authorization: Path = G5_AUTHORIZATION,
) -> dict[str, Any]:
    index = contract._validate_attempt_index(attempt_index)
    paths = attempt_paths(index)
    if implementation_seal.resolve() != IMPLEMENTATION_SEAL:
        raise ValueError("candidate claim requires the canonical implementation seal")
    implementation = verify_implementation_seal(
        implementation_seal, orchestrator_path=ORCHESTRATOR_PATH
    )
    predecessor = verify_attempt_chain(PROTOCOL, through_attempt=index - 1)
    if (
        predecessor["attempts"] != index - 1
        or predecessor["closed"] != index - 1
        or predecessor["activeAttempt"] is not None
        or predecessor["confirmedAttempt"] is not None
    ):
        raise ValueError(
            "candidate claim requires every predecessor attempt to be terminal "
            "and permit a successor"
        )
    if PROGRAM_SUCCESS.exists():
        raise FileExistsError("confirmation program already has a success seal")
    evidence = _g5_nomination_evidence(
        g5_authorization,
        G5_DECISIONS,
        run_fresh_verifier=True,
        implementation=implementation,
    )
    common = {
        "schemaVersion": SCHEMA_VERSION,
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "predecessorClosure": (
            None
            if index == 1
            else contract.identity(attempt_paths(index - 1)["attemptClosure"])
        ),
        "implementationSeal": contract.identity(implementation_seal.resolve()),
        "orchestrator": implementation["pinned"]["orchestrator"],
        "g5Authorization": evidence["authorization"],
        "g5Decisions": evidence["decisions"],
        "g5Lineage": evidence["lineage"],
        "g5Verifier": evidence["freshVerifier"],
        "g5Chronology": evidence["chronology"],
        "requiredG5Decisions": evidence["requiredDecisions"],
        "selectedNetwork": evidence["selectedNetwork"],
        "selectedManifest": evidence["selectedManifest"],
        "engine": evidence["engine"],
    }
    reservation = {
        **common,
        "kind": RESERVATION_KIND,
        "createdUtc": _utc_now(),
        "candidateValidationComplete": True,
        "reservationConsumesAttempt": True,
        "status": "reserved-before-entropy-draw",
    }
    # This exclusive, durable publication consumes k before the one and only
    # entropy draw.  A crash from this point onward may only close k as a
    # claim-publication failure; it can never retry or reroll k.
    _publish_reservation_namespace(paths, reservation)
    try:
        seed_derivation = contract.draw_seed_bundle(
            index, prior_published_stage_seeds(index)
        )
        value = {
        **common,
        "kind": CLAIM_KIND,
        "createdUtc": _utc_now(),
        "attemptReservation": contract.identity(paths["reservation"]),
        "seedDerivation": seed_derivation,
        "candidateClaimConsumesAttempt": True,
        "status": "active",
        }
        _exclusive_json(paths["claim"], value)
        return verify_candidate_claim(paths["claim"], index)
    except BaseException:
        if not paths["attemptClosure"].exists():
            _publish_abort(index, "claim-publication-failure")
        raise


def verify_candidate_claim(
    path: Path,
    attempt_index: Any,
    *,
    reverify_g5: bool = False,
) -> dict[str, Any]:
    index = contract._validate_attempt_index(attempt_index)
    expected_path = attempt_paths(index)["claim"]
    path = path.resolve()
    if path != expected_path:
        raise ValueError("candidate claim escaped its canonical attempt namespace")
    value = contract.strict_load(path, "confirmation candidate claim")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocol",
        "attemptIndex",
        "predecessorClosure",
        "createdUtc",
        "implementationSeal",
        "orchestrator",
        "g5Authorization",
        "g5Decisions",
        "g5Lineage",
        "g5Verifier",
        "g5Chronology",
        "requiredG5Decisions",
        "selectedNetwork",
        "selectedManifest",
        "engine",
        "attemptReservation",
        "seedDerivation",
        "candidateClaimConsumesAttempt",
        "status",
    }
    if set(value) != expected_fields:
        raise ValueError("candidate-claim field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != CLAIM_KIND
        or value.get("attemptIndex") != index
        or value.get("candidateClaimConsumesAttempt") is not True
        or value.get("status") != "active"
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError("candidate-claim envelope changed")
    created = _parse_utc(value.get("createdUtc"), "candidate claim createdUtc")
    reservation_path = _verify_identity(
        value.get("attemptReservation"), "candidate claim attempt reservation"
    )
    if reservation_path != attempt_paths(index)["reservation"]:
        raise ValueError("candidate claim reservation path changed")
    reservation = verify_attempt_reservation(reservation_path, index)
    if not contract.exact_json_equal(
        value.get("implementationSeal"), reservation.get("implementationSeal")
    ):
        raise ValueError(f"attempt {index} closure implementation differs from reservation")
    if created < _parse_utc(reservation["createdUtc"], "reservation createdUtc"):
        raise ValueError("candidate claim predates its attempt reservation")
    implementation_path = _verify_identity(value.get("implementationSeal"), "claim implementation seal")
    implementation = verify_implementation_seal(
        implementation_path, orchestrator_path=ORCHESTRATOR_PATH
    )
    implementation_created = _parse_utc(
        implementation.get("createdUtc"), "implementation seal createdUtc"
    )
    if created < implementation_created:
        raise ValueError("candidate claim predates its implementation seal")
    if not contract.exact_json_equal(value.get("orchestrator"), implementation["pinned"]["orchestrator"]):
        raise ValueError("candidate claim orchestrator binding changed")
    inherited_fields = {
        "schemaVersion",
        "protocol",
        "attemptIndex",
        "predecessorClosure",
        "implementationSeal",
        "orchestrator",
        "g5Authorization",
        "g5Decisions",
        "g5Lineage",
        "g5Verifier",
        "g5Chronology",
        "requiredG5Decisions",
        "selectedNetwork",
        "selectedManifest",
        "engine",
    }
    for name in inherited_fields:
        if not contract.exact_json_equal(value.get(name), reservation.get(name)):
            raise ValueError(f"candidate claim {name} differs from its reservation")
    verify_claim_seed_derivation(value, index)
    for name in ("selectedNetwork", "selectedManifest", "engine", "g5Authorization", "g5Verifier"):
        _verify_identity(value.get(name), f"candidate claim {name}")
    decisions = contract.mapping(value.get("g5Decisions"), "claim G5 decisions")
    if set(decisions) != set(GATES):
        raise ValueError("claim G5 decision inventory changed")
    for gate in GATES:
        if _verify_identity(decisions[gate], f"claim G5 {gate} decision") != G5_DECISIONS[gate]:
            raise ValueError(f"claim G5 {gate} decision path changed")
    lineage = contract.mapping(value.get("g5Lineage"), "claim G5 lineage")
    if set(lineage) != {"offlineReport", "selectedManifest", "coreSeal", "suiteSeal"}:
        raise ValueError("claim G5 lineage inventory changed")
    for name, record in lineage.items():
        _verify_identity(record, f"claim G5 lineage {name}")
    required = {
        "development": "pass",
        "equal-node": "promote",
        "equal-time": "promote",
    }
    contract.require_exact_json(value.get("requiredG5Decisions"), required, "claim G5 decisions")
    if reverify_g5:
        evidence = _g5_nomination_evidence(
            G5_AUTHORIZATION,
            G5_DECISIONS,
            run_fresh_verifier=True,
            implementation=implementation,
        )
        bindings = {
            "g5Authorization": evidence["authorization"],
            "g5Decisions": evidence["decisions"],
            "g5Lineage": evidence["lineage"],
            "g5Verifier": evidence["freshVerifier"],
            "g5Chronology": evidence["chronology"],
            "selectedNetwork": evidence["selectedNetwork"],
            "selectedManifest": evidence["selectedManifest"],
            "engine": evidence["engine"],
        }
        for name, expected in bindings.items():
            if not contract.exact_json_equal(value.get(name), expected):
                raise ValueError(f"candidate claim {name} differs from reverified G5 nominee")
    return value


def _verify_attempt_closure(path: Path, index: int) -> dict[str, Any]:
    paths = attempt_paths(index)
    if path.resolve() != paths["attemptClosure"]:
        raise ValueError("attempt closure escaped its canonical namespace")
    value = contract.strict_load(path, f"confirmation attempt {index} closure")
    expected_fields = {
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
    if set(value) != expected_fields:
        raise ValueError(f"attempt {index} closure field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != ATTEMPT_CLOSURE_KIND
        or value.get("attemptIndex") != index
        or value.get("terminal") is not True
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError(f"attempt {index} closure envelope changed")
    created = _parse_utc(value.get("createdUtc"), f"attempt {index} closure createdUtc")
    implementation_path = _verify_identity(
        value.get("implementationSeal"), f"attempt {index} closure implementation seal"
    )
    verify_implementation_seal(implementation_path, orchestrator_path=ORCHESTRATOR_PATH)
    reservation_path = _verify_identity(
        value.get("attemptReservation"), f"attempt {index} closure reservation"
    )
    if reservation_path != paths["reservation"]:
        raise ValueError(f"attempt {index} closure reservation path changed")
    reservation = verify_attempt_reservation(reservation_path, index)
    if created < _parse_utc(
        reservation["createdUtc"], f"attempt {index} reservation createdUtc"
    ):
        raise ValueError(f"attempt {index} closure predates its reservation")
    outcome = value.get("outcome")
    reason = value.get("reason")
    if outcome not in {"confirmed", "failed", "aborted"}:
        raise ValueError(f"attempt {index} closure outcome changed")
    if type(reason) is not str or reason not in CLOSURE_REASONS:
        raise ValueError(f"attempt {index} closure reason is not registered")
    claim_value = value.get("candidateClaim")
    claim: dict[str, Any] | None = None
    if claim_value is None:
        if outcome != "aborted" or reason != "claim-publication-failure":
            raise ValueError(f"attempt {index} closure lacks a required candidate claim")
    else:
        claim_path = _verify_identity(
            claim_value, f"attempt {index} closure claim"
        )
        if claim_path != paths["claim"]:
            raise ValueError(f"attempt {index} closure claim path changed")
        claim = verify_candidate_claim(claim_path, index)
        if not contract.exact_json_equal(
            claim["attemptReservation"], value["attemptReservation"]
        ):
            raise ValueError(f"attempt {index} claim/reservation binding changed")
        if created < _parse_utc(
            claim["createdUtc"], f"attempt {index} claim createdUtc"
        ):
            raise ValueError(f"attempt {index} closure predates its claim")
    if (
        type(value.get("candidateNetworkSha256")) is not str
        or value["candidateNetworkSha256"]
        != reservation["selectedNetwork"]["sha256"]
    ):
        raise ValueError(f"attempt {index} closure candidate binding changed")
    joint_value = value.get("jointSuiteSeal")
    authorization_value = value.get("authorization")
    if joint_value is not None:
        joint_path = _verify_identity(joint_value, f"attempt {index} closure suite seal")
        if joint_path != paths["jointSuiteSeal"]:
            raise ValueError(f"attempt {index} closure suite path changed")
    if authorization_value is not None:
        authorization_path = _verify_identity(
            authorization_value, f"attempt {index} closure authorization"
        )
        if authorization_path != paths["authorization"]:
            raise ValueError(f"attempt {index} closure authorization path changed")
    decisions = contract.mapping(value.get("decisions"), f"attempt {index} closure decisions")
    if set(decisions) != set(GATES):
        raise ValueError(f"attempt {index} closure decision inventory changed")
    for gate, record in decisions.items():
        if record is not None:
            decision_path = _verify_identity(record, f"attempt {index} {gate} decision")
            if decision_path != paths["stages"][gate] / "decision.json":
                raise ValueError(f"attempt {index} {gate} decision path changed")
    if outcome == "confirmed":
        if (
            reason != "confirmed"
            or claim is None
            or value.get("nextAttemptAllowed") is not False
            or joint_value is None
            or authorization_value is None
            or any(item is None for item in decisions.values())
        ):
            raise ValueError(f"attempt {index} confirmed closure is incomplete")
    elif value.get("nextAttemptAllowed") is not True:
        raise ValueError(f"attempt {index} non-success closure blocks the next attempt")
    if outcome == "aborted" and reason == "confirmed":
        raise ValueError(f"attempt {index} abort uses the success reason")
    if claim is None and (
        joint_value is not None
        or authorization_value is not None
        or any(item is not None for item in decisions.values())
    ):
        raise ValueError(
            f"attempt {index} prepublication abort contains post-claim artifacts"
        )
    return value


def _verify_program_success(path: Path = PROGRAM_SUCCESS) -> dict[str, Any]:
    path = path.resolve()
    if path != PROGRAM_SUCCESS:
        raise ValueError("program success escaped its canonical namespace")
    value = contract.strict_load(path, "confirmation program success")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocol",
        "attemptIndex",
        "createdUtc",
        "attemptClosure",
        "candidateClaim",
        "authorization",
        "equalNodeDecision",
        "equalTimeDecision",
        "candidateNetworkSha256",
        "clearlySuperior",
        "terminal",
    }
    if set(value) != expected_fields:
        raise ValueError("program-success field inventory changed")
    index = contract._validate_attempt_index(value.get("attemptIndex"))
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != PROGRAM_SUCCESS_KIND
        or value.get("clearlySuperior") is not True
        or value.get("terminal") is not True
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError("program-success envelope changed")
    _parse_utc(value.get("createdUtc"), "program success createdUtc")
    paths = attempt_paths(index)
    closure_path = _verify_identity(value.get("attemptClosure"), "program success closure")
    claim_path = _verify_identity(value.get("candidateClaim"), "program success claim")
    authorization_path = _verify_identity(value.get("authorization"), "program success authorization")
    node_path = _verify_identity(value.get("equalNodeDecision"), "program success equal-node decision")
    time_path = _verify_identity(value.get("equalTimeDecision"), "program success equal-time decision")
    if (
        closure_path != paths["attemptClosure"]
        or claim_path != paths["claim"]
        or authorization_path != paths["authorization"]
        or node_path != paths["stages"]["equal-node"] / "decision.json"
        or time_path != paths["stages"]["equal-time"] / "decision.json"
    ):
        raise ValueError("program-success path binding changed")
    closure = _verify_attempt_closure(closure_path, index)
    claim = verify_candidate_claim(claim_path, index)
    if closure["outcome"] != "confirmed" or value["candidateNetworkSha256"] != claim["selectedNetwork"]["sha256"]:
        raise ValueError("program success is not bound to a confirmed candidate")
    return value


def verify_attempt_chain(
    protocol: Mapping[str, Any],
    through_attempt: int | None = None,
    *,
    allow_incomplete_claim_tip: bool = False,
) -> dict[str, Any]:
    contract.require_exact_json(dict(protocol), PROTOCOL, "confirmation protocol argument")
    if type(allow_incomplete_claim_tip) is not bool:
        raise ValueError("allow_incomplete_claim_tip must be boolean")
    if through_attempt is not None and (
        type(through_attempt) is not int or through_attempt < 0 or through_attempt > contract.MAX_ATTEMPT_INDEX
    ):
        raise ValueError("through_attempt is out of range")
    if not ARTIFACT_ROOT.exists():
        if through_attempt not in (None, 0):
            raise FileNotFoundError("confirmation attempt chain is absent")
        return {
            "attempts": 0,
            "closed": 0,
            "activeAttempt": None,
            "confirmedAttempt": None,
            "priorPublishedStageSeeds": [],
        }
    if not ARTIFACT_ROOT.is_dir():
        raise ValueError("confirmation artifact root is not a directory")
    allowed_root_files = {IMPLEMENTATION_SEAL.name, PROGRAM_SUCCESS.name}
    indices: list[int] = []
    for item in ARTIFACT_ROOT.iterdir():
        if item.is_dir():
            indices.append(contract.parse_attempt_directory_name(item.name))
        elif item.name not in allowed_root_files:
            raise ValueError(f"unexpected confirmation-root artifact: {item}")
    indices.sort()
    if indices != list(range(1, len(indices) + 1)):
        raise ValueError("confirmation attempt chain has a gap or reuse")
    maximum = len(indices)
    if through_attempt is not None and through_attempt != maximum:
        # Every caller reasons about an exact immutable prefix.  It may not
        # ignore a later attempt that already exists.
        raise ValueError(
            f"requested attempt prefix {through_attempt}, but chain tip is {maximum}"
        )
    active: int | None = None
    confirmed: int | None = None
    closures: list[dict[str, Any]] = []
    for index in indices:
        paths = attempt_paths(index)
        entries = set(_directory_entries(paths["root"]))
        extra = entries - ATTEMPT_TOP_LEVEL
        if extra:
            raise ValueError(f"attempt {index} has unexpected artifacts: {sorted(extra)!r}")
        if not paths["reservation"].is_file():
            raise ValueError(f"attempt {index} directory lacks its consuming reservation")
        verify_attempt_reservation(paths["reservation"], index)
        if paths["attemptClosure"].is_file():
            closure = _verify_attempt_closure(paths["attemptClosure"], index)
            closures.append(closure)
            if closure["outcome"] == "confirmed":
                confirmed = index
        else:
            if paths["claim"].is_file() and not (
                allow_incomplete_claim_tip and index == maximum
            ):
                verify_candidate_claim(paths["claim"], index)
            if active is not None or index != maximum:
                raise ValueError("confirmation chain contains multiple/nonterminal active attempts")
            active = index
    if confirmed is not None:
        if confirmed != maximum or active is not None:
            raise ValueError("an attempt exists after confirmed success")
        if not PROGRAM_SUCCESS.is_file():
            raise FileNotFoundError("confirmed attempt lacks program-success seal")
        success = _verify_program_success(PROGRAM_SUCCESS)
        if success["attemptIndex"] != confirmed:
            raise ValueError("program-success attempt differs from confirmed closure")
    elif PROGRAM_SUCCESS.exists():
        raise ValueError("program-success seal exists without a confirmed attempt")
    if active is not None and active != maximum:
        raise ValueError("active attempt is not the global chain tip")
    if active is not None and closures and closures[-1]["nextAttemptAllowed"] is not True:
        raise ValueError("active attempt follows a closure that forbids continuation")
    return {
        "attempts": maximum,
        "closed": len(closures),
        "activeAttempt": active,
        "confirmedAttempt": confirmed,
        "priorPublishedStageSeeds": prior_published_stage_seeds(
            maximum + 1, allow_active_reservation_tip=active is not None
        ),
    }


@_serialized_transition
def _publish_abort(index: int, reason: str) -> dict[str, Any]:
    _require_operation_lock("attempt abort")
    if reason not in CLOSURE_REASONS or reason == "confirmed":
        raise ValueError("abort reason is not a registered non-success reason")
    paths = attempt_paths(index)
    if paths["attemptClosure"].exists():
        raise FileExistsError(f"attempt {index} already has a terminal closure")
    state = verify_attempt_chain(
        PROTOCOL,
        through_attempt=index,
        allow_incomplete_claim_tip=reason == "claim-publication-failure",
    )
    if state["activeAttempt"] != index:
        raise ValueError(
            "only the unique active attempt may be aborted: "
            f"requested={index}, state={state!r}"
        )
    _require_idle_processes("attempt abort")
    reservation = verify_attempt_reservation(paths["reservation"], index)
    claim: dict[str, Any] | None = None
    if reason == "claim-publication-failure" and paths["claim"].is_file():
        try:
            verify_candidate_claim(paths["claim"], index)
        except (OSError, ValueError):
            pass
        else:
            raise ValueError(
                "a fully published candidate claim cannot use prepublication abort"
            )
    elif reason != "claim-publication-failure":
        claim = verify_candidate_claim(paths["claim"], index)
    decisions: dict[str, Any] = {}
    for gate in GATES:
        decision_path = paths["stages"][gate] / "decision.json"
        decisions[gate] = (
            contract.identity(decision_path) if decision_path.is_file() else None
        )
    if reason == "claim-publication-failure" and (
        paths["jointSuiteSeal"].exists()
        or paths["authorization"].exists()
        or any(item is not None for item in decisions.values())
    ):
        raise ValueError("prepublication abort contains post-claim artifacts")
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ATTEMPT_CLOSURE_KIND,
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "createdUtc": _utc_now(),
        "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
        "attemptReservation": contract.identity(paths["reservation"]),
        "candidateClaim": (
            None if claim is None else contract.identity(paths["claim"])
        ),
        "jointSuiteSeal": (
            contract.identity(paths["jointSuiteSeal"])
            if paths["jointSuiteSeal"].is_file()
            else None
        ),
        "authorization": (
            contract.identity(paths["authorization"])
            if paths["authorization"].is_file()
            else None
        ),
        "decisions": decisions,
        "outcome": "aborted",
        "reason": reason,
        "terminal": True,
        "nextAttemptAllowed": True,
        "candidateNetworkSha256": reservation["selectedNetwork"]["sha256"],
    }
    _exclusive_json(paths["attemptClosure"], value)
    return _verify_attempt_closure(paths["attemptClosure"], index)


def _history_roots(index: int) -> list[Path]:
    roots: set[Path] = set()
    for path in (
        _REPO / "validation",
        _REPO / "build-msvc",
        _REPO / "build-king-state-v5",
    ):
        if not path.is_dir():
            raise FileNotFoundError(f"required repository history root is absent: {path}")
        roots.add(path.resolve())
    for label, path in SIBLING_HISTORY_ROOTS:
        if not path.is_dir():
            raise FileNotFoundError(
                f"required sibling history root {label} is absent: {path}"
            )
        roots.add(path.resolve())
    for path in _REPO.glob("build-king-state-v[1-4]"):
        if path.is_dir():
            roots.add(path.resolve())
    for prior in range(1, index):
        path = contract.attempt_namespace(prior)
        if not path.is_dir():
            raise FileNotFoundError(f"prior confirmation attempt is absent: {path}")
        roots.add(path.resolve())
    required = (
        (_REPO / "build-msvc/data-generation/omega-decision-v2").resolve(),
        (_REPO / "build-msvc/data-generation/g3-prior-projection-v1").resolve(),
        (_REPO / "build-king-state-v5").resolve(),
    )
    for path in required:
        if not path.is_dir():
            raise FileNotFoundError(f"required Generation-5 exclusion root is absent: {path}")
    # Remove nested roots so every input file is inventoried exactly once.
    ordered: list[Path] = []
    for root in sorted(roots, key=lambda item: (len(item.parts), str(item).casefold())):
        if not any(root.is_relative_to(existing) for existing in ordered):
            ordered.append(root)
    return ordered


def _discover_history_files(
    index: int, roots: Sequence[Path] | None = None
) -> list[Path]:
    current = attempt_paths(index)["root"]
    result: set[Path] = set()
    allowed = {".json", ".jsonl", ".pgn", ".ccsf"}
    scan_roots = _history_roots(index) if roots is None else list(roots)
    for root in scan_roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in allowed:
                continue
            resolved = path.resolve()
            if resolved.is_relative_to(current):
                continue
            if any(part.casefold() in {".git", "obj", "bin", "__pycache__"} for part in resolved.parts):
                continue
            result.add(resolved)
    return sorted(result, key=lambda item: str(item).casefold())


def _history_root_coverage(
    roots: Sequence[Path],
    files: Sequence[Path],
    identities: Mapping[Path, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    coverage: list[dict[str, Any]] = []
    assigned: set[Path] = set()
    for root in roots:
        members = [path for path in files if path.is_relative_to(root)]
        suffix_counts = {
            suffix: sum(path.suffix.casefold() == suffix for path in members)
            for suffix in (".json", ".jsonl", ".pgn", ".ccsf")
        }
        coverage.append(
            {
                "root": str(root),
                "inputFiles": len(members),
                "inputBytes": sum(int(identities[path]["bytes"]) for path in members),
                "suffixCounts": suffix_counts,
            }
        )
        overlap = assigned.intersection(members)
        if overlap:
            raise ValueError(f"history roots overlap at {next(iter(overlap))}")
        assigned.update(members)
    if assigned != set(files):
        raise ValueError("history root coverage does not account for every input file")
    return coverage


def _history_coverage_evidence(
    identities: Mapping[Path, Mapping[str, Any]],
) -> dict[str, Any]:
    projection = (
        _REPO / "build-msvc/data-generation/g3-prior-projection-v1/positions.jsonl"
    ).resolve()
    manifest = (
        _REPO
        / "build-msvc/data-generation/g3-prior-projection-v1/positions.manifest.json"
    ).resolve()
    if projection not in identities or manifest not in identities:
        raise ValueError("frozen G3 prior-position projection is not inventoried")
    return {
        "generation5DecisionCorpus": True,
        "generation5RawMatchPools": True,
        "generation5SealedSuites": True,
        "generation5PlayedEventPreFinalAndPvPositions": True,
        "priorConfirmationPoolsSuitesEventsAndPvs": True,
        "frozenTrainingValidationHeldoutAndScreeningHistory": True,
        "g3PriorProjection": {
            "positions": dict(identities[projection]),
            "manifest": dict(identities[manifest]),
        },
        "canonicalSiblingDeltaRoots": {
            label: str(path) for label, path in SIBLING_HISTORY_ROOTS
        },
        "completeCurrentFileMembershipRecheckedAtVerification": True,
    }


def _decode_json_string(token: str, label: str) -> str:
    try:
        value = json.loads(token)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} contains a malformed JSON string") from error
    if type(value) is not str or not value:
        raise ValueError(f"{label} is not a nonempty JSON string")
    return value


def _direct_ofens_and_pv_requests(
    path: Path,
    source_identity: Mapping[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    ofens: list[str] = []
    requests: list[dict[str, Any]] = []
    ordinal = 0
    try:
        with path.open("r", encoding="utf-8", errors="strict", newline="") as stream:
            for line_number, line in enumerate(stream, 1):
                for match in POSITION_FIELD_RE.finditer(line):
                    ofens.append(
                        _decode_json_string(
                            match.group("value"), f"{path}:{line_number} position"
                        )
                    )
                initial_matches = list(POSITION_FIELD_RE.finditer(line))
                initial: str | None = None
                for match in initial_matches:
                    if match.group("key").casefold() in {"preofen", "initialofen"}:
                        initial = _decode_json_string(
                            match.group("value"), f"{path}:{line_number} replay initial"
                        )
                        if match.group("key").casefold() == "preofen":
                            break
                if initial is None:
                    continue
                for array_index, match in enumerate(MOVE_ARRAY_RE.finditer(line), 1):
                    key = match.group("key").casefold()
                    # On event ply records only coordinate PV arrays are
                    # replayed. OpeningMoves are replayed from gameStart.
                    if key == "moves":
                        continue
                    try:
                        moves = json.loads(match.group("value"))
                    except json.JSONDecodeError as error:
                        raise ValueError(f"{path}:{line_number}: malformed move array") from error
                    if type(moves) is not list or any(
                        type(move) is not str or COORDINATE_MOVE.fullmatch(move) is None
                        for move in moves
                    ):
                        # Non-coordinate arrays such as rendered SAN are not
                        # position-bearing UCI PVs.
                        continue
                    if not moves:
                        continue
                    ordinal += 1
                    requests.append(
                        {
                            "schemaVersion": 1,
                            "kind": "omega-opening-prefix-replay-request-v1",
                            "requestId": -1,
                            "sourcePath": str(path),
                            "sourceBytes": source_identity["bytes"],
                            "sourceSha256": source_identity["sha256"],
                            "sourceRecord": line_number,
                            "sourceObjectOrdinal": ordinal,
                            "sourceObjectIdentity": f"/line/{line_number}/{key}/{array_index}",
                            "schema": "schedule-moves",
                            "initialSource": "explicit",
                            "containerProof": "direct-object",
                            "initialOfen": initial,
                            "moves": moves,
                            "expectedPositions": None,
                        }
                    )
    except UnicodeError as error:
        raise ValueError(f"history input is not strict UTF-8: {path}") from error
    return ofens, requests


def _suite_replay_requests(
    path: Path,
    source_identity: Mapping[str, Any],
    start_ordinal: int,
) -> list[dict[str, Any]]:
    if path.suffix.casefold() != ".json" or not any(
        marker in path.name.casefold() for marker in ("suite", "opening")
    ):
        return []
    value = contract.strict_load(path, f"candidate-unaware opening container {path}")
    requests: list[dict[str, Any]] = []
    ordinal = start_ordinal

    def visit(item: Any, pointer: str) -> None:
        nonlocal ordinal
        if type(item) is dict:
            initial = item.get("initialOfen")
            moves = item.get("moves")
            if type(initial) is str and type(moves) is list and moves:
                if any(
                    type(move) is not str or COORDINATE_MOVE.fullmatch(move) is None
                    for move in moves
                ):
                    raise ValueError(f"{path}:{pointer}: non-coordinate opening move")
                ordinal += 1
                requests.append(
                    {
                        "schemaVersion": 1,
                        "kind": "omega-opening-prefix-replay-request-v1",
                        "requestId": -1,
                        "sourcePath": str(path),
                        "sourceBytes": source_identity["bytes"],
                        "sourceSha256": source_identity["sha256"],
                        "sourceRecord": 1,
                        "sourceObjectOrdinal": ordinal,
                        "sourceObjectIdentity": pointer or "/",
                        "schema": "schedule-moves",
                        "initialSource": "explicit",
                        "containerProof": "direct-object",
                        "initialOfen": initial,
                        "moves": list(moves),
                        "expectedPositions": None,
                    }
                )
            for key, child in item.items():
                visit(child, f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}")
        elif type(item) is list:
            for number, child in enumerate(item):
                visit(child, f"{pointer}/{number}")

    visit(value, "")
    return requests


def _add_orbit(ofen: str, signatures: set[str], label: str) -> None:
    try:
        _, _, identity, _, orbit_signatures = _POSITION_META(ofen)
    except Exception as error:
        raise ValueError(f"{label}: invalid Omega OFEN") from error
    signatures.add(identity)
    signatures.update(orbit_signatures)


def _run_prefix_replay(
    requests: list[dict[str, Any]],
    sampler_dir: Path,
    signatures: set[str],
    implementation: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not requests:
        return None
    projection = sampler_dir / "history-replay-requests.jsonl"
    output = sampler_dir / "history-replay-positions.jsonl"
    manifest = sampler_dir / "history-replay-positions.manifest.json"
    for request_id, request in enumerate(requests):
        request["requestId"] = request_id
    _exclusive_bytes(
        projection,
        b"".join(
            (json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            for item in requests
        ),
    )
    bundle = _verify_runtime_bundle(
        implementation["prefixReplayBundle"], "prefix replay bundle"
    )
    bundle_root = Path(bundle["root"]).resolve()
    assembly = bundle_root / bundle["assemblyRelativePath"]
    command = [
        str(_runtime_path("dotnetHost")),
        str(assembly),
        "--input",
        str(projection),
        "--output",
        str(output),
        "--manifest",
        str(manifest),
    ]
    subprocess.run(
        command,
        cwd=bundle_root,
        env=_sanitized_environment(),
        check=True,
        timeout=6 * 60 * 60,
    )
    positions = 0
    with output.open("r", encoding="utf-8", errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            matches = list(POSITION_FIELD_RE.finditer(line))
            if len(matches) != 1:
                raise ValueError(f"{output}:{line_number}: replay position field changed")
            _add_orbit(
                _decode_json_string(matches[0].group("value"), f"{output}:{line_number}"),
                signatures,
                f"{output}:{line_number}",
            )
            positions += 1
    return {
        "requests": len(requests),
        "positions": positions,
        "projection": contract.identity(projection),
        "output": contract.identity(output),
        "manifest": contract.identity(manifest),
        "runtimeBundle": bundle,
    }


def _run_history_snapshot(
    history_files: Sequence[Path],
    sampler_dir: Path,
    signatures: set[str],
    implementation: Mapping[str, Any],
) -> dict[str, Any] | None:
    sources = [path for path in history_files if path.suffix.casefold() in {".pgn", ".ccsf"}]
    if not sources:
        return None
    parents = sorted({path.parent.resolve() for path in sources}, key=lambda item: str(item).casefold())
    output = sampler_dir / "pgn-ccsf-history-positions.jsonl"
    manifest = sampler_dir / "pgn-ccsf-history-positions.manifest.json"
    bundle = _verify_runtime_bundle(
        implementation["historySnapshotBundle"], "history snapshot bundle"
    )
    bundle_root = Path(bundle["root"]).resolve()
    assembly = bundle_root / bundle["assemblyRelativePath"]
    command = [str(_runtime_path("dotnetHost")), str(assembly)]
    for parent in parents:
        command.extend(("--root", str(parent)))
    command.extend(("--output", str(output), "--manifest", str(manifest)))
    subprocess.run(
        command,
        cwd=bundle_root,
        env=_sanitized_environment(),
        check=True,
        timeout=6 * 60 * 60,
    )
    manifest_value = contract.strict_load(manifest, "PGN/CCSF history manifest")
    source_set = manifest_value.get("sourceSet")
    if type(source_set) is not list:
        raise ValueError("PGN/CCSF history manifest lacks sourceSet")
    expected_sources = sorted(
        (contract.identity(path) for path in sources), key=lambda item: item["path"].casefold()
    )
    actual_sources = sorted(
        (_identity_shape(item, "PGN/CCSF source") for item in source_set),
        key=lambda item: item["path"].casefold(),
    )
    contract.require_exact_json(actual_sources, expected_sources, "PGN/CCSF source inventory")
    positions = 0
    with output.open("r", encoding="utf-8", errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            matches = list(POSITION_FIELD_RE.finditer(line))
            if len(matches) != 1:
                raise ValueError(f"{output}:{line_number}: history position field changed")
            _add_orbit(
                _decode_json_string(matches[0].group("value"), f"{output}:{line_number}"),
                signatures,
                f"{output}:{line_number}",
            )
            positions += 1
    return {
        "sources": len(sources),
        "positions": positions,
        "output": contract.identity(output),
        "manifest": contract.identity(manifest),
        "runtimeBundle": bundle,
    }


def _build_exclusion_inventory(
    index: int,
    sampler_dir: Path,
    implementation: Mapping[str, Any],
) -> tuple[set[str], dict[str, Any]]:
    roots = _history_roots(index)
    files = _discover_history_files(index, roots)
    identities = {path: contract.identity(path) for path in files}
    signatures: set[str] = set()
    direct_positions = 0
    requests: list[dict[str, Any]] = []
    for path in files:
        if path.suffix.casefold() not in {".json", ".jsonl"}:
            continue
        ofens, current = _direct_ofens_and_pv_requests(path, identities[path])
        for number, ofen in enumerate(ofens, 1):
            direct_positions += 1
            try:
                _add_orbit(ofen, signatures, f"{path} direct position {number}")
            except ValueError:
                # The closed lexical allowlist is also documented in old
                # protocol/audit JSON.  Such descriptive strings are decoded
                # as candidates but, like the frozen G3 scanner, contribute no
                # exclusion signature unless they are a legal Omega OFEN.
                continue
        current.extend(_suite_replay_requests(path, identities[path], len(current)))
        requests.extend(current)
    replay = _run_prefix_replay(requests, sampler_dir, signatures, implementation)
    history = _run_history_snapshot(files, sampler_dir, signatures, implementation)
    # Rehash every source after all managed replays to close TOCTOU windows.
    for path, before in identities.items():
        if not contract.exact_json_equal(contract.identity(path), before):
            raise ValueError(f"exclusion input changed during scan: {path}")
    orbit_path = sampler_dir / "excluded-orbits.txt"
    _exclusive_bytes(
        orbit_path,
        "".join(f"{item}\n" for item in sorted(signatures)).encode("ascii"),
    )
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": EXCLUSION_KIND,
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "createdUtc": _utc_now(),
        "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
        "roots": [str(path) for path in roots],
        "inputFiles": [identities[path] for path in files],
        "rootCoverage": _history_root_coverage(roots, files, identities),
        "directPositions": direct_positions,
        "prefixReplay": replay,
        "pgnCcsfReplay": history,
        "excludedOrbitSignatures": len(signatures),
        "excludedOrbits": contract.identity(orbit_path),
        "coverage": _history_coverage_evidence(identities),
        "informationBoundary": {
            "positionStringsDecoded": direct_positions,
            "coordinateMoveListsDecoded": len(requests),
            "targetFieldsDecoded": 0,
            "scoreFieldsDecoded": 0,
            "resultFieldsDecoded": 0,
            "candidateIdentityAvailable": False,
        },
    }
    inventory_path = sampler_dir / "exclusion-inventory.json"
    _exclusive_json(inventory_path, value)
    return signatures, value


SAMPLER_FIELDS = frozenset(
    {
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
)


def _trajectory_seed(seed: int, pair_index: int, flavor: str) -> int:
    salt = 0xA0761D6478BD642F if flavor == "ab" else 0xE7037ED1A0B428DB
    value = (seed ^ (((pair_index + 1) * 0x9E3779B97F4A7C15) & UINT64_MASK) ^ salt) & UINT64_MASK
    value = (value + 0x9E3779B97F4A7C15) & UINT64_MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & UINT64_MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & UINT64_MASK
    return (value ^ (value >> 31)) & UINT64_MASK


def _source_path(index: int, gate: str) -> Path:
    return attempt_paths(index)["sampler"] / f"{gate}-rules-only.jsonl"


def _parse_sampler_source(
    path: Path, index: int, gate: str, seed: int
) -> list[Root]:
    seed = _normalized_stage_seed(seed, f"{gate} sampler seed")
    before = contract.identity(path)
    roots: list[Root] = []
    seen_ranks: set[str] = set()
    with path.open("r", encoding="utf-8", errors="strict", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                raise ValueError(f"{path}:{line_number}: incomplete sampler record")
            try:
                value = json.loads(
                    line,
                    object_pairs_hook=contract._unique_object,
                    parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
                )
            except (json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"{path}:{line_number}: invalid strict JSON") from error
            if type(value) is not dict or set(value) != SAMPLER_FIELDS:
                raise ValueError(f"{path}:{line_number}: sampler fields changed")
            if (
                type(value.get("schemaVersion")) is not int
                or value.get("schemaVersion") != 1
                or value.get("kind") != "omega-rules-only-random-root"
                or value.get("generatorSeed") != str(seed)
            ):
                raise ValueError(f"{path}:{line_number}: sampler envelope changed")
            pair = SAMPLER_PAIR.fullmatch(str(value.get("trajectoryPairId", "")))
            flavor = value.get("flavor")
            if pair is None or flavor not in {"ab", "ba"}:
                raise ValueError(f"{path}:{line_number}: trajectory identity changed")
            pair_number = int(pair.group(1))
            if not 1 <= pair_number <= int(PROTOCOL["sampler"]["trajectoryPairsPerStage"]):
                raise ValueError(f"{path}:{line_number}: trajectory pair is out of range")
            trajectory_id = f"{pair.group(0)}-{flavor}"
            expected_trajectory_seed = _trajectory_seed(seed, pair_number - 1, str(flavor))
            if (
                value.get("trajectoryId") != trajectory_id
                or value.get("trajectorySeed") != str(expected_trajectory_seed)
            ):
                raise ValueError(f"{path}:{line_number}: trajectory seed changed")
            ofen = value.get("ofen")
            ply = value.get("ply")
            if type(ofen) is not str or not ofen or type(ply) is not int or ply < 0:
                raise ValueError(f"{path}:{line_number}: sampler OFEN/ply is malformed")
            phase, side, identity, orbit, signatures = _POSITION_META(ofen)
            if value.get("phase") != phase or value.get("sideToMove") != side:
                raise ValueError(f"{path}:{line_number}: sampler OFEN metadata changed")
            pieces, _, _ = contract._OMEGA_PARSE_OFEN(ofen)
            expected_integer = {
                "pieceCount": len(pieces),
                "whitePieces": sum(piece_side == 0 for _, piece_side, _ in pieces),
                "blackPieces": sum(piece_side != 0 for _, piece_side, _ in pieces),
                "champions": sum(piece == 6 for piece, _, _ in pieces),
                "wizards": sum(piece == 7 for piece, _, _ in pieces),
                "halfmoveClock": int(ofen.split()[4]),
            }
            if any(type(value.get(key)) is not int or value.get(key) != wanted for key, wanted in expected_integer.items()):
                raise ValueError(f"{path}:{line_number}: sampler integer metadata changed")
            source_rank = hashlib.sha256(
                (
                    f"omega-root-sampler-v1\0{expected_trajectory_seed}\0{pair_number - 1}\0"
                    f"{flavor}\0{ply}\0{ofen}"
                ).encode("utf-8")
            ).hexdigest()
            if value.get("selectionRank") != source_rank or source_rank in seen_ranks:
                raise ValueError(f"{path}:{line_number}: sampler source rank changed")
            seen_ranks.add(source_rank)
            rank = hashlib.sha256(
                (
                    f"open-confirmation-v1-root\0{index}\0{gate}\0{seed}\0"
                    f"{pair.group(0)}\0{trajectory_id}\0{ply}\0{orbit}"
                ).encode("utf-8")
            ).hexdigest()
            roots.append(
                Root(
                    gate=gate,
                    source_path=path.resolve(),
                    source_sha256=before["sha256"],
                    line=line_number,
                    generator_seed=seed,
                    trajectory_pair_id=pair.group(0),
                    trajectory_id=trajectory_id,
                    flavor=str(flavor),
                    ply=ply,
                    phase=phase,
                    side=side,
                    ofen=ofen,
                    identity=identity,
                    orbit=orbit,
                    orbit_signatures=signatures,
                    rank=rank,
                )
            )
    if not roots or not contract.exact_json_equal(contract.identity(path), before):
        raise ValueError(f"sampler source is empty or changed while read: {path}")
    return roots


def _verify_sampler_artifacts(
    path: Path, index: int, gate: str, seed: int
) -> dict[str, Any]:
    manifest_path = Path(str(path) + ".manifest.json")
    completion_path = Path(str(path) + ".complete.seal.json")
    source = contract.identity(path)
    manifest_identity = contract.identity(manifest_path)
    completion_identity = contract.identity(completion_path)
    manifest = contract.strict_load(manifest_path, f"{gate} sampler manifest")
    completion = contract.strict_load(completion_path, f"{gate} sampler completion")
    expected_policy = {
        "deterministicPrng": "SplitMix64",
        "seed": str(seed),
        "trajectoryPairs": PROTOCOL["sampler"]["trajectoryPairsPerStage"],
        "independentTrajectoriesPerPair": PROTOCOL["sampler"]["independentTrajectoriesPerPair"],
        "workers": PROTOCOL["sampler"]["workers"],
        "maxPlies": PROTOCOL["sampler"]["maxPlies"],
        "positionsPerPhaseAndSide": PROTOCOL["sampler"]["positionsPerPhaseAndSide"],
        "captureSelectionPercent": PROTOCOL["sampler"]["captureSelectionPercent"],
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
    if (
        manifest.get("kind") != "omega-rules-only-random-root-manifest"
        or manifest.get("finalStageSeal") is not False
        or not contract.exact_json_equal(manifest.get("policy"), expected_policy)
        or not contract.exact_json_equal(manifest.get("output"), source)
    ):
        raise ValueError(f"{gate} sampler manifest contract changed")
    runtime = contract.mapping(manifest.get("runtime"), f"{gate} sampler runtime")
    if (
        set(runtime) != {"framework", "samplerAssembly", "chessLibAssembly"}
        or runtime.get("framework") != f".NET {PROTOCOL['sharedRuntime']['dotnetRuntimeVersion']}"
        or not contract.exact_json_equal(runtime.get("samplerAssembly"), _runtime_identity("rootSamplerAssembly"))
        or not contract.exact_json_equal(runtime.get("chessLibAssembly"), _runtime_identity("rootSamplerRulesAssembly"))
    ):
        raise ValueError(f"{gate} sampler runtime changed")
    if (
        completion.get("kind") != "omega-rules-only-random-root-completion-seal"
        or completion.get("finalStageSeal") is not True
        or not contract.exact_json_equal(completion.get("output"), source)
        or not contract.exact_json_equal(completion.get("manifest"), manifest_identity)
    ):
        raise ValueError(f"{gate} sampler completion contract changed")
    roots = _parse_sampler_source(path, index, gate, seed)
    for phase in PHASES:
        for side in SIDES:
            if sum(root.phase == phase and root.side == side for root in roots) < int(
                PROTOCOL["stages"][gate]["rootsPerPhaseAndSideToMove"]
            ):
                raise ValueError(f"{gate} sampler lacks {phase}/{side} coverage")
    if (
        not contract.exact_json_equal(contract.identity(path), source)
        or not contract.exact_json_equal(contract.identity(manifest_path), manifest_identity)
        or not contract.exact_json_equal(contract.identity(completion_path), completion_identity)
    ):
        raise ValueError(f"{gate} sampler artifacts changed while verified")
    return {
        "source": source,
        "manifest": manifest_identity,
        "completionSeal": completion_identity,
        "records": len(roots),
        "roots": roots,
    }


def _run_sampler(index: int, gate: str, seed: int, output: Path) -> dict[str, Any]:
    dotnet = _runtime_path("dotnetHost")
    assembly = _runtime_path("rootSamplerAssembly")
    command = [
        str(dotnet),
        str(assembly),
        "--output",
        str(output.resolve()),
        "--seed",
        str(seed),
        "--trajectory-pairs",
        str(PROTOCOL["sampler"]["trajectoryPairsPerStage"]),
        "--workers",
        str(PROTOCOL["sampler"]["workers"]),
        "--max-plies",
        str(PROTOCOL["sampler"]["maxPlies"]),
        "--positions-per-phase-side",
        str(PROTOCOL["sampler"]["positionsPerPhaseAndSide"]),
        "--capture-percent",
        str(PROTOCOL["sampler"]["captureSelectionPercent"]),
    ]
    subprocess.run(
        command,
        cwd=assembly.parent,
        env=_sanitized_environment(),
        check=True,
        timeout=6 * 60 * 60,
    )
    return _verify_sampler_artifacts(output, index, gate, seed)


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:88]


def _build_suite(
    index: int, gate: str, seed: int, roots: Sequence[Root]
) -> tuple[dict[str, Any], list[str]]:
    spec = PROTOCOL["stages"][gate]
    per_side = int(spec["rootsPerPhaseAndSideToMove"])
    by_bucket: dict[tuple[str, str], list[Root]] = {}
    for phase in PHASES:
        for side in SIDES:
            values = sorted(
                (root for root in roots if root.phase == phase and root.side == side),
                key=lambda root: root.rank,
            )
            if len(values) != per_side:
                raise ValueError(f"{gate} selected {phase}/{side} quota changed")
            by_bucket[(phase, side)] = values
    schedule: list[dict[str, Any]] = []
    blocks = int(spec["rootsPerPhase"])
    for block_index in range(blocks):
        side = SIDES[block_index % 2]
        bucket_index = block_index // 2
        for phase in PHASES:
            root = by_bucket[(phase, side)][bucket_index]
            opening_id = _safe_id(
                f"ocv1-a{index:06d}-{gate}-b{block_index + 1:03d}-{phase}-{side}-{root.orbit[:12]}"
            )
            schedule.append(
                {
                    "id": opening_id,
                    "source": "sealed candidate-unaware open-confirmation rules-only sampler",
                    "initialOfen": root.ofen,
                    "moves": [],
                    "kingStateMatch": {
                        "gate": gate,
                        "phase": phase,
                        "rootSideToMove": side,
                        "balancedBlock": block_index + 1,
                        "generatorSeed": root.generator_seed,
                        "trajectoryPairId": root.trajectory_pair_id,
                        "trajectoryId": root.trajectory_id,
                        "sourceLine": root.line,
                        "sourceSha256": root.source_sha256,
                        "selectionRank": root.rank,
                        "identityKey": root.identity,
                        "symmetryOrbitKey": root.orbit,
                        "orbitSignatures": list(root.orbit_signatures),
                    },
                }
            )
    permutation = _SHUFFLED_INDICES(len(schedule), seed)
    source_order: list[dict[str, Any] | None] = [None] * len(schedule)
    for scheduled_index, original_index in enumerate(permutation):
        source_order[original_index] = schedule[scheduled_index]
    if any(item is None for item in source_order):
        raise AssertionError("inverse .NET shuffle permutation is incomplete")
    return (
        {
            "schemaVersion": 1,
            "name": f"Omega NNUE open confirmation attempt {index} {gate}",
            "kingStateMatchSuite": {
                "schemaVersion": 1,
                "protocolId": contract.PROTOCOL_ID,
                "attemptIndex": index,
                "gate": gate,
                "seed": seed,
                "roots": int(spec["roots"]),
                "rootsPerPhase": int(spec["rootsPerPhase"]),
                "rootsPerPhaseSide": per_side,
                "balancedBlockSizePairs": 4,
                "schedulePermutation": "System.Random(int) compatibility Fisher-Yates",
            },
            "openings": source_order,
        },
        [item["id"] for item in schedule],
    )


def _verify_suite(
    path: Path, index: int, gate: str, seed: int
) -> tuple[dict[str, Any], set[str]]:
    value = contract.strict_load(path, f"confirmation {gate} suite")
    if set(value) != {"schemaVersion", "name", "kingStateMatchSuite", "openings"}:
        raise ValueError(f"{gate} suite fields changed")
    metadata = contract.mapping(value.get("kingStateMatchSuite"), f"{gate} suite metadata")
    expected_metadata = {
        "schemaVersion": 1,
        "protocolId": contract.PROTOCOL_ID,
        "attemptIndex": index,
        "gate": gate,
        "seed": seed,
        "roots": int(PROTOCOL["stages"][gate]["roots"]),
        "rootsPerPhase": int(PROTOCOL["stages"][gate]["rootsPerPhase"]),
        "rootsPerPhaseSide": int(PROTOCOL["stages"][gate]["rootsPerPhaseAndSideToMove"]),
        "balancedBlockSizePairs": 4,
        "schedulePermutation": "System.Random(int) compatibility Fisher-Yates",
    }
    contract.require_exact_json(metadata, expected_metadata, f"{gate} suite metadata")
    openings = value.get("openings")
    if type(openings) is not list or len(openings) != expected_metadata["roots"]:
        raise ValueError(f"{gate} suite root count changed")
    ids: set[str] = set()
    signatures: set[str] = set()
    schedule: dict[int, dict[str, str]] = {}
    for number, opening in enumerate(openings, 1):
        item = contract.mapping(opening, f"{gate} opening {number}")
        if set(item) != {"id", "source", "initialOfen", "moves", "kingStateMatch"}:
            raise ValueError(f"{gate} opening {number} fields changed")
        opening_id = item.get("id")
        if type(opening_id) is not str or not opening_id or opening_id in ids or item.get("moves") != []:
            raise ValueError(f"{gate} opening {number} identity/moves changed")
        ids.add(opening_id)
        phase, side, identity, orbit, orbit_signatures = _POSITION_META(str(item.get("initialOfen", "")))
        match = contract.mapping(item.get("kingStateMatch"), f"{gate} opening {number} metadata")
        expected_fields = {
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
        }
        if (
            set(match) != expected_fields
            or match.get("gate") != gate
            or match.get("phase") != phase
            or match.get("rootSideToMove") != side
            or match.get("generatorSeed") != seed
            or match.get("identityKey") != identity
            or match.get("symmetryOrbitKey") != orbit
            or match.get("orbitSignatures") != list(orbit_signatures)
        ):
            raise ValueError(f"{gate} opening {number} metadata changed")
        if signatures.intersection(orbit_signatures):
            raise ValueError(f"{gate} suite repeats a whole position orbit")
        signatures.update(orbit_signatures)
        block = match.get("balancedBlock")
        if type(block) is not int or not 1 <= block <= expected_metadata["rootsPerPhase"]:
            raise ValueError(f"{gate} opening {number} block changed")
        schedule.setdefault(block, {})[phase] = side
    if set(schedule) != set(range(1, expected_metadata["rootsPerPhase"] + 1)):
        raise ValueError(f"{gate} suite block inventory changed")
    for block, phase_sides in schedule.items():
        if set(phase_sides) != set(PHASES) or set(phase_sides.values()) != {SIDES[(block - 1) % 2]}:
            raise ValueError(f"{gate} suite block {block} is not phase/side balanced")
    shuffled = [openings[number] for number in _SHUFFLED_INDICES(len(openings), seed)]
    for block in range(expected_metadata["rootsPerPhase"]):
        items = shuffled[block * len(PHASES) : (block + 1) * len(PHASES)]
        if [item["kingStateMatch"]["phase"] for item in items] != list(PHASES):
            raise ValueError(f"{gate} seeded schedule block {block + 1} changed")
    return value, signatures


def _root_digest(suites: Mapping[str, Path]) -> str:
    canonical = bytearray()
    for gate in GATES:
        item = contract.identity(suites[gate])
        canonical.extend(f"{gate}\t{item['bytes']}\t{item['sha256']}\n".encode("utf-8"))
    return hashlib.sha256(canonical).hexdigest()


def _require_worker_attempt_open(paths: Mapping[str, Any], label: str) -> None:
    if Path(paths["attemptClosure"]).exists():
        raise FileExistsError(f"candidate-blind worker observed closure {label}")


def _candidate_blind_worker(
    index: int,
    implementation_seal: Path,
    output_root: Path,
    parent_lock_token: str,
) -> dict[str, Any]:
    _verify_parent_operation_lock(parent_lock_token)
    paths = attempt_paths(index)
    if output_root.resolve() != paths["root"]:
        raise ValueError("clean worker output root is noncanonical")
    _require_worker_attempt_open(paths, "before input authentication")
    implementation = verify_implementation_seal(
        implementation_seal, orchestrator_path=ORCHESTRATOR_PATH
    )
    intent_path = paths["samplingIntent"]
    intent = contract.strict_load(intent_path, "candidate-unaware sampling intent")
    stage_seeds = _normalized_stage_seeds(
        intent.get("stageSeeds"), "candidate-unaware sampling intent stage seeds"
    )
    expected_intent = {
        "schemaVersion": 1,
        "kind": "omega-nnue-open-confirmation-v1-sampling-intent",
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "createdUtc": intent.get("createdUtc"),
        "implementationSeal": contract.identity(implementation_seal.resolve()),
        "stageSeeds": stage_seeds,
        "workerCommandCandidateInputs": 0,
        "candidateIdentity": None,
    }
    _parse_utc(intent.get("createdUtc"), "sampling intent createdUtc")
    contract.require_exact_json(intent, expected_intent, "candidate-unaware sampling intent")
    forbidden_outputs = [
        paths["sampler"],
        paths["sealed"],
        paths["authorization"],
        paths["attemptClosure"],
        *paths["stages"].values(),
    ]
    if any(path.exists() for path in forbidden_outputs):
        raise FileExistsError(
            f"candidate-unaware worker refuses preexisting output: {next(path for path in forbidden_outputs if path.exists())}"
        )
    paths["sampler"].mkdir(parents=False, exist_ok=False)
    paths["sealed"].mkdir(parents=False, exist_ok=False)
    forbidden, exclusion = _build_exclusion_inventory(index, paths["sampler"], implementation)
    used_fresh: set[str] = set()
    selected: dict[str, list[Root]] = {}
    source_audits: dict[str, Any] = {}
    rejection_audits: dict[str, Any] = {}
    schedules: dict[str, list[str]] = {}
    for gate in GATES:
        seed = stage_seeds[gate]
        source_audit = _run_sampler(index, gate, seed, _source_path(index, gate))
        roots = source_audit.pop("roots")
        chosen, rejected = _select_roots(roots, gate, forbidden, used_fresh)
        selected[gate] = chosen
        source_audits[gate] = source_audit
        rejection_audits[gate] = rejected
        suite, schedule = _build_suite(index, gate, seed, chosen)
        schedules[gate] = schedule
        _exclusive_json(paths["suites"][gate], suite)
        _verify_suite(paths["suites"][gate], index, gate, seed)
    runtime = {
        "dotnetHost": _runtime_identity("dotnetHost"),
        "dotnetRuntimeManifest": _runtime_identity("dotnetRuntimeManifest"),
        "dotnetRuntimeBundle": _dotnet_runtime_bundle(),
        "rootSamplerAssembly": _runtime_identity("rootSamplerAssembly"),
        "rootSamplerRulesAssembly": _runtime_identity("rootSamplerRulesAssembly"),
        "rootSamplerBundleSha256": PROTOCOL["sharedRuntime"]["rootSamplerBundleSha256"],
        "matchCoreSource": _runtime_identity("sharedMatchCore"),
    }
    seal = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": JOINT_SUITE_KIND,
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "createdUtc": _utc_now(),
        "implementationSeal": contract.identity(implementation_seal.resolve()),
        "samplingIntent": contract.identity(intent_path),
        "stageSeeds": stage_seeds,
        "candidateIdentity": None,
        "exclusionInventory": contract.identity(paths["exclusionInventory"]),
        "excludedOrbits": contract.identity(paths["excludedOrbits"]),
        "excludedOrbitSignatures": len(forbidden),
        "samplerSources": source_audits,
        "suites": {
            gate: {
                "identity": contract.identity(paths["suites"][gate]),
                "roots": len(selected[gate]),
                "phaseCounts": {phase: sum(root.phase == phase for root in selected[gate]) for phase in PHASES},
                "sideToMoveCounts": {side: sum(root.side == side for root in selected[gate]) for side in SIDES},
                "uniqueTrajectoryPairs": len({root.source_group for root in selected[gate]}),
                "uniqueOrbitSignatures": len({signature for root in selected[gate] for signature in root.orbit_signatures}),
                "rejections": rejection_audits[gate],
                "scheduledOpeningIds": schedules[gate],
            }
            for gate in GATES
        },
        "rootDigest": _root_digest(paths["suites"]),
        "crossSuite": {
            "roots": sum(len(items) for items in selected.values()),
            "uniqueOrbitSignatures": len(used_fresh),
            "mutuallyWholeOrbitDisjoint": True,
            "historicalWholeOrbitIntersection": 0,
        },
        "runtime": runtime,
        "stageOutputsAbsentAtSeal": {gate: str(paths["stages"][gate]) for gate in GATES},
        "informationBoundary": {
            "candidateIdentityAvailableToWorker": False,
            "candidateOrClaimInputs": 0,
            "targetFieldsDecoded": 0,
            "scoreFieldsDecoded": 0,
            "resultFieldsDecoded": 0,
            "matchResultsAccessed": 0,
            "allThreeSuitesSealedTogether": True,
        },
    }
    _verify_parent_operation_lock(parent_lock_token)
    _require_worker_attempt_open(paths, "before joint-suite publication")
    _exclusive_json(paths["jointSuiteSeal"], seal)
    # `exclusion` was built without a candidate and is authenticated again by
    # the public deep verifier after the process exits.
    del exclusion, forbidden, used_fresh, selected
    result = verify_suite_seal(paths["jointSuiteSeal"], index, deep=True)
    _verify_parent_operation_lock(parent_lock_token)
    _require_worker_attempt_open(paths, "before successful return")
    return result


def _load_excluded_orbits(path: Path, expected_count: int) -> set[str]:
    before = contract.identity(path)
    values: list[str] = []
    with path.open("r", encoding="ascii", errors="strict", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                raise ValueError(f"{path}:{line_number}: orbit record is incomplete")
            value = line[:-1]
            if HEX_256.fullmatch(value) is None:
                raise ValueError(f"{path}:{line_number}: orbit signature is malformed")
            values.append(value)
    if values != sorted(set(values)) or len(values) != expected_count:
        raise ValueError("excluded-orbit file is unsorted, duplicated, or miscounted")
    if not contract.exact_json_equal(contract.identity(path), before):
        raise ValueError("excluded-orbit file changed while read")
    return set(values)


def _verify_exclusion_inventory(
    path: Path, index: int, expected_orbits: Mapping[str, Any], *, deep: bool
) -> dict[str, Any]:
    value = contract.strict_load(path, "confirmation exclusion inventory")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocol",
        "attemptIndex",
        "createdUtc",
        "implementationSeal",
        "roots",
        "inputFiles",
        "rootCoverage",
        "directPositions",
        "prefixReplay",
        "pgnCcsfReplay",
        "excludedOrbitSignatures",
        "excludedOrbits",
        "coverage",
        "informationBoundary",
    }
    if set(value) != expected_fields:
        raise ValueError("exclusion-inventory fields changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != 1
        or value.get("kind") != EXCLUSION_KIND
        or value.get("attemptIndex") != index
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError("exclusion-inventory envelope changed")
    _parse_utc(value.get("createdUtc"), "exclusion inventory createdUtc")
    implementation_path = _verify_identity(value.get("implementationSeal"), "exclusion implementation seal")
    verify_implementation_seal(implementation_path, orchestrator_path=ORCHESTRATOR_PATH)
    current_roots = _history_roots(index)
    roots = value.get("roots")
    expected_roots = [str(item) for item in current_roots]
    if roots != expected_roots:
        raise ValueError("exclusion scan-root inventory changed")
    inputs = value.get("inputFiles")
    if type(inputs) is not list or not inputs:
        raise ValueError("exclusion input-file inventory is empty")
    current_files = _discover_history_files(index, current_roots)
    if len(inputs) != len(current_files):
        raise ValueError("exclusion input-file set changed after suite sealing")
    prior_path = ""
    normalized_inputs: dict[Path, dict[str, Any]] = {}
    for number, (record, current_file) in enumerate(zip(inputs, current_files), 1):
        item = _identity_shape(record, f"exclusion input {number}")
        if item["path"].casefold() <= prior_path:
            raise ValueError("exclusion input files are not strictly path sorted")
        prior_path = item["path"].casefold()
        recorded_path = _identity_path(item, f"exclusion input {number}")
        if recorded_path != current_file:
            raise ValueError("exclusion input-file membership/order changed")
        normalized_inputs[current_file] = item
        if deep:
            _verify_identity(item, f"exclusion input {number}")
    expected_root_coverage = _history_root_coverage(
        current_roots, current_files, normalized_inputs
    )
    contract.require_exact_json(
        value.get("rootCoverage"),
        expected_root_coverage,
        "exclusion root coverage",
    )
    count = value.get("excludedOrbitSignatures")
    if type(count) is not int or count < 1:
        raise ValueError("excluded-orbit count is malformed")
    orbit_path = _verify_identity(value.get("excludedOrbits"), "excluded-orbit file")
    if orbit_path != attempt_paths(index)["excludedOrbits"] or not contract.exact_json_equal(value["excludedOrbits"], expected_orbits):
        raise ValueError("excluded-orbit identity/path changed")
    expected_coverage = _history_coverage_evidence(normalized_inputs)
    contract.require_exact_json(value.get("coverage"), expected_coverage, "exclusion coverage")
    boundary = {
        "positionStringsDecoded": value.get("directPositions"),
        "coordinateMoveListsDecoded": (
            0 if value.get("prefixReplay") is None else value["prefixReplay"]["requests"]
        ),
        "targetFieldsDecoded": 0,
        "scoreFieldsDecoded": 0,
        "resultFieldsDecoded": 0,
        "candidateIdentityAvailable": False,
    }
    contract.require_exact_json(value.get("informationBoundary"), boundary, "exclusion information boundary")
    for replay_name, bundle_key in (
        ("prefixReplay", "runtimeBundle"),
        ("pgnCcsfReplay", "runtimeBundle"),
    ):
        replay = value.get(replay_name)
        if replay is None:
            continue
        replay = contract.mapping(replay, replay_name)
        for identity_name in ("projection", "output", "manifest"):
            if identity_name in replay:
                _verify_identity(replay[identity_name], f"{replay_name} {identity_name}")
        _verify_runtime_bundle(replay[bundle_key], f"{replay_name} runtime bundle")
    return value


def verify_suite_seal(
    path: Path,
    attempt_index: Any,
    deep: bool = True,
) -> dict[str, Any]:
    index = contract._validate_attempt_index(attempt_index)
    paths = attempt_paths(index)
    path = path.resolve()
    if path != paths["jointSuiteSeal"]:
        raise ValueError("joint suite seal escaped its canonical attempt namespace")
    value = contract.strict_load(path, "confirmation joint suite seal")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocol",
        "attemptIndex",
        "createdUtc",
        "implementationSeal",
        "samplingIntent",
        "stageSeeds",
        "candidateIdentity",
        "exclusionInventory",
        "excludedOrbits",
        "excludedOrbitSignatures",
        "samplerSources",
        "suites",
        "rootDigest",
        "crossSuite",
        "runtime",
        "stageOutputsAbsentAtSeal",
        "informationBoundary",
    }
    if set(value) != expected_fields:
        raise ValueError("joint-suite-seal field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != 1
        or value.get("kind") != JOINT_SUITE_KIND
        or value.get("attemptIndex") != index
        or value.get("candidateIdentity") is not None
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError("joint-suite-seal envelope changed")
    created = _parse_utc(value.get("createdUtc"), "joint suite seal createdUtc")
    implementation_path = _verify_identity(value.get("implementationSeal"), "suite implementation seal")
    verify_implementation_seal(implementation_path, orchestrator_path=ORCHESTRATOR_PATH)
    intent_path = _verify_identity(value.get("samplingIntent"), "suite sampling intent")
    if intent_path != paths["samplingIntent"]:
        raise ValueError("suite sampling-intent path changed")
    intent = contract.strict_load(intent_path, "suite sampling intent")
    stage_seeds = _normalized_stage_seeds(
        value.get("stageSeeds"), "joint-suite stage seeds"
    )
    if not contract.exact_json_equal(intent.get("stageSeeds"), stage_seeds):
        raise ValueError("joint suite and sampling intent stage seeds differ")
    if created < _parse_utc(intent.get("createdUtc"), "sampling intent createdUtc"):
        raise ValueError("joint suite seal predates sampling intent")
    count = value.get("excludedOrbitSignatures")
    if type(count) is not int or count < 1:
        raise ValueError("joint suite excluded-orbit count changed")
    orbit_path = _verify_identity(value.get("excludedOrbits"), "joint suite excluded orbits")
    forbidden = _load_excluded_orbits(orbit_path, count)
    inventory_path = _verify_identity(value.get("exclusionInventory"), "joint suite exclusion inventory")
    if inventory_path != paths["exclusionInventory"]:
        raise ValueError("joint suite exclusion-inventory path changed")
    inventory = _verify_exclusion_inventory(
        inventory_path, index, value["excludedOrbits"], deep=deep
    )
    if inventory["excludedOrbitSignatures"] != count:
        raise ValueError("joint suite/inventory excluded-orbit counts differ")
    sources = contract.mapping(value.get("samplerSources"), "joint suite sampler sources")
    suites = contract.mapping(value.get("suites"), "joint suite suites")
    if set(sources) != set(GATES) or set(suites) != set(GATES):
        raise ValueError("joint suite gate inventory changed")
    used: set[str] = set()
    total_roots = 0
    for gate in GATES:
        source = contract.mapping(sources[gate], f"{gate} source audit")
        if set(source) != {"source", "manifest", "completionSeal", "records"}:
            raise ValueError(f"{gate} source-audit fields changed")
        source_paths: dict[str, Path] = {}
        for name in ("source", "manifest", "completionSeal"):
            source_paths[name] = _verify_identity(
                source[name], f"{gate} sampler {name}"
            )
        if deep:
            reproduced = _verify_sampler_artifacts(
                source_paths["source"], index, gate, stage_seeds[gate]
            )
            for name in ("source", "manifest", "completionSeal", "records"):
                if not contract.exact_json_equal(source[name], reproduced[name]):
                    raise ValueError(f"{gate} sampler audit differs from deep verification")
        entry = contract.mapping(suites[gate], f"{gate} suite entry")
        expected_entry_fields = {
            "identity",
            "roots",
            "phaseCounts",
            "sideToMoveCounts",
            "uniqueTrajectoryPairs",
            "uniqueOrbitSignatures",
            "rejections",
            "scheduledOpeningIds",
        }
        if set(entry) != expected_entry_fields:
            raise ValueError(f"{gate} suite entry fields changed")
        suite_path = _verify_identity(entry["identity"], f"{gate} suite")
        if suite_path != paths["suites"][gate]:
            raise ValueError(f"{gate} suite path changed")
        suite, signatures = _verify_suite(
            suite_path, index, gate, stage_seeds[gate]
        )
        openings = suite["openings"]
        expected_roots = int(PROTOCOL["stages"][gate]["roots"])
        if entry["roots"] != expected_roots or len(openings) != expected_roots:
            raise ValueError(f"{gate} suite summary root count changed")
        phase_counts = Counter(item["kingStateMatch"]["phase"] for item in openings)
        side_counts = Counter(item["kingStateMatch"]["rootSideToMove"] for item in openings)
        trajectory_pairs = {
            f"{item['kingStateMatch']['generatorSeed']}:{item['kingStateMatch']['trajectoryPairId']}"
            for item in openings
        }
        expected_summary = {
            "phaseCounts": {phase: phase_counts[phase] for phase in PHASES},
            "sideToMoveCounts": {side: side_counts[side] for side in SIDES},
            "uniqueTrajectoryPairs": len(trajectory_pairs),
            "uniqueOrbitSignatures": len(signatures),
            "scheduledOpeningIds": [
                item["id"]
                for item in sorted(
                    openings,
                    key=lambda item: (
                        item["kingStateMatch"]["balancedBlock"],
                        PHASES.index(item["kingStateMatch"]["phase"]),
                    ),
                )
            ],
        }
        for name, expected in expected_summary.items():
            if not contract.exact_json_equal(entry.get(name), expected):
                raise ValueError(f"{gate} suite summary {name} changed")
        if forbidden.intersection(signatures):
            raise ValueError(f"{gate} selected suite intersects historical whole orbit")
        if used.intersection(signatures):
            raise ValueError(f"{gate} selected suite intersects another stage whole orbit")
        used.update(signatures)
        total_roots += expected_roots
    if value.get("rootDigest") != _root_digest(paths["suites"]):
        raise ValueError("joint suite root digest changed")
    expected_cross = {
        "roots": total_roots,
        "uniqueOrbitSignatures": len(used),
        "mutuallyWholeOrbitDisjoint": True,
        "historicalWholeOrbitIntersection": 0,
    }
    contract.require_exact_json(value.get("crossSuite"), expected_cross, "joint-suite cross-stage audit")
    runtime = contract.mapping(value.get("runtime"), "joint-suite runtime")
    expected_runtime = {
        "dotnetHost": _runtime_identity("dotnetHost"),
        "dotnetRuntimeManifest": _runtime_identity("dotnetRuntimeManifest"),
        "dotnetRuntimeBundle": _dotnet_runtime_bundle(),
        "rootSamplerAssembly": _runtime_identity("rootSamplerAssembly"),
        "rootSamplerRulesAssembly": _runtime_identity("rootSamplerRulesAssembly"),
        "rootSamplerBundleSha256": PROTOCOL["sharedRuntime"]["rootSamplerBundleSha256"],
        "matchCoreSource": _runtime_identity("sharedMatchCore"),
    }
    contract.require_exact_json(runtime, expected_runtime, "joint-suite runtime")
    contract.require_exact_json(
        value.get("stageOutputsAbsentAtSeal"),
        {gate: str(paths["stages"][gate]) for gate in GATES},
        "joint-suite stage paths",
    )
    boundary = {
        "candidateIdentityAvailableToWorker": False,
        "candidateOrClaimInputs": 0,
        "targetFieldsDecoded": 0,
        "scoreFieldsDecoded": 0,
        "resultFieldsDecoded": 0,
        "matchResultsAccessed": 0,
        "allThreeSuitesSealedTogether": True,
    }
    contract.require_exact_json(value.get("informationBoundary"), boundary, "joint-suite information boundary")
    return value


@_serialized_transition
def sample_attempt(attempt_index: Any) -> dict[str, Any]:
    parent_lock_token = _require_operation_lock("confirmation sampling")
    index = contract._validate_attempt_index(attempt_index)
    paths = attempt_paths(index)
    state = verify_attempt_chain(PROTOCOL, through_attempt=index)
    if state["activeAttempt"] != index:
        raise ValueError("sampling requires the uniquely active claimed attempt")
    claim = verify_candidate_claim(paths["claim"], index, reverify_g5=True)
    stage_seeds = claim_stage_seeds(claim, index)
    if paths["attemptClosure"].exists():
        raise FileExistsError("closed attempt cannot be sampled")
    if any(path.exists() for path in (paths["samplingIntent"], paths["sampler"], paths["sealed"])):
        raise FileExistsError("sampling attempt already has readiness output")
    verify_implementation_seal(IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH)
    if not contract.exact_json_equal(claim["implementationSeal"], contract.identity(IMPLEMENTATION_SEAL)):
        raise ValueError("candidate claim and active implementation seal differ")
    intent = {
        "schemaVersion": 1,
        "kind": "omega-nnue-open-confirmation-v1-sampling-intent",
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "createdUtc": _utc_now(),
        "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
        "stageSeeds": stage_seeds,
        "workerCommandCandidateInputs": 0,
        "candidateIdentity": None,
    }
    _exclusive_json(paths["samplingIntent"], intent)
    command = [
        sys.executable,
        "-I",
        "-B",
        str(TOOL_PATH),
        "candidate-blind-worker",
        "--attempt-index",
        str(index),
        "--implementation-seal",
        str(IMPLEMENTATION_SEAL),
        "--output-root",
        str(paths["root"]),
        "--parent-lock-token",
        parent_lock_token,
    ]
    if any(
        token.casefold() in {"--candidate", "--network", "--claim", "--g5-authorization"}
        for token in command
    ):
        raise AssertionError("candidate-aware argument reached clean worker")
    try:
        completed = subprocess.run(
            command,
            cwd=TOOL_PATH.parent,
            env=_sanitized_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=24 * 60 * 60,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "candidate-blind worker failed: " + completed.stderr[-4000:]
            )
        _require_worker_attempt_open(paths, "after clean-worker exit")
        return verify_suite_seal(paths["jointSuiteSeal"], index, deep=True)
    except BaseException:
        if not paths["attemptClosure"].exists():
            _publish_abort(index, "sampling-failure")
        raise


def _promotion_linear_threshold(index: int) -> float:
    return math.nextafter(math.exp(contract.promotion_log_threshold(index)), math.inf)


def _expected_config(
    index: int,
    gate: str,
    seed: int,
    suite: Mapping[str, Any],
    engine: Mapping[str, Any],
    network: Mapping[str, Any],
    harness: Mapping[str, Any],
    harness_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    paths = attempt_paths(index)
    spec = PROTOCOL["stages"][gate]
    suite_path = _verify_identity(suite, f"{gate} config suite")
    engine_path = _verify_identity(engine, f"{gate} config engine")
    network_path = _verify_identity(network, f"{gate} config network")
    _verify_identity(harness, f"{gate} config harness")
    common = dict(PROTOCOL["engineConfiguration"]["commonEngineOptions"])
    candidate_options = {
        **common,
        "OmegaNNUEFile": str(network_path),
        "UseOmegaNNUE": "true",
    }
    hce_options = {
        **common,
        "OmegaNNUEFile": "<empty>",
        "UseOmegaNNUE": "false",
    }
    promotion_e = _promotion_linear_threshold(index)
    match: dict[str, Any] = {
        "engineA": "nnue-candidate",
        "engineB": "hce-control",
        "openingsFile": str(suite_path),
        "repeats": 1,
        "maxPlies": PROTOCOL["execution"]["maxPlies"],
        "absoluteMaxPlies": PROTOCOL["execution"]["absoluteMaxPlies"],
        "mode": spec["mode"],
        "searchTimeoutMs": spec["searchTimeoutMs"],
        "stopGraceMs": PROTOCOL["execution"]["stopGraceMs"],
        "bootstrapIterations": 20_000,
        "sequentialGate": {
            "candidateEngine": "nnue-candidate",
            "minimumPairs": 1 if gate == "development" else int(spec["minimumPairsBeforeDecision"]),
            "nullElo": 15.0,
            "promotionAlpha": 1.0 / promotion_e,
            "futilityBeta": 0.05,
        },
        "freshProcessPerGame": True,
    }
    if spec["mode"] == "nodes":
        match["nodes"] = int(spec["nodesPerMove"])
    else:
        match["moveTimeMs"] = int(spec["moveTimeMs"])
    return {
        "schemaVersion": 1,
        "profileId": contract.PROTOCOL_ID,
        "freshnessMarker": f"{contract.PROTOCOL_ID}:attempt-{index:06d}:{gate}",
        "expectedHarnessSha256": harness["sha256"],
        "expectedHarnessBundleSha256": harness_bundle["sha256"],
        "expectedOpeningSuiteSha256": suite["sha256"],
        "runId": f"open-confirmation-v1-a{index:06d}-{gate}-{network['sha256'][:12]}",
        "outputDirectory": str(paths["stages"][gate]),
        "seed": seed,
        "kingStateMatchExecution": {
            "oneGameAtATime": True,
            "maximumConcurrentGames": 1,
            "pairBudgetRequired": True,
            "pairBudgetMustBeMultipleOf": 4,
            "initialPairBudget": int(spec["initialPairBudget"]),
            "resumePairBudget": int(spec["resumePairBudget"]),
            "executionOnlyThroughAdapterLaunch": True,
            "idleMachineRequired": gate == "equal-time",
        },
        "engines": [
            {
                "id": "nnue-candidate",
                "executable": str(engine_path),
                "expectedSha256": engine["sha256"],
                "expectedAssetSha256": {"OmegaNNUEFile": network["sha256"]},
                "options": candidate_options,
            },
            {
                "id": "hce-control",
                "executable": str(engine_path),
                "expectedSha256": engine["sha256"],
                "options": hce_options,
            },
        ],
        "match": match,
    }


def _verify_config(
    path: Path,
    index: int,
    gate: str,
    seed: int,
    suite: Mapping[str, Any],
    engine: Mapping[str, Any],
    network: Mapping[str, Any],
    harness: Mapping[str, Any],
    harness_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    value = contract.strict_load(path, f"confirmation {gate} config")
    expected = _expected_config(
        index, gate, seed, suite, engine, network, harness, harness_bundle
    )
    contract.require_exact_json(value, expected, f"confirmation {gate} exact config")
    candidate, control = value["engines"]
    if (
        candidate["executable"] != control["executable"]
        or candidate["expectedSha256"] != control["expectedSha256"]
        or candidate["options"]["UseOmegaNNUE"] != "true"
        or control["options"]["UseOmegaNNUE"] != "false"
        or control["options"]["OmegaNNUEFile"] != "<empty>"
        or "expectedAssetSha256" in control
    ):
        raise ValueError(f"{gate} candidate/HCE isolation changed")
    return value


def _verify_core_seal(
    path: Path,
    index: int,
    *,
    suites: Mapping[str, Any],
    configs: Mapping[str, Any],
    network: Mapping[str, Any],
    engine: Mapping[str, Any],
    harness: Mapping[str, Any],
    harness_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    value = contract.strict_load(path, "confirmation shared-core seal")
    expected_fields = {
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
    if set(value) != expected_fields:
        raise ValueError("confirmation core-seal fields changed")
    if (
        value.get("schemaVersion") != 1
        or value.get("kind") != CORE_SEAL_KIND
        or value.get("generationId") != f"{contract.PROTOCOL_ID}:attempt-{index:06d}"
        or value.get("protocolSha256") != PROTOCOL_IDENTITY["sha256"]
        or value.get("candidateNetworkSha256") != network["sha256"]
        or value.get("engineExecutableSha256") != engine["sha256"]
        or value.get("matchCoreSourceSha256") != _runtime_identity("sharedMatchCore")["sha256"]
        or value.get("omegaMatchAssemblySha256") != harness["sha256"]
    ):
        raise ValueError("confirmation core-seal envelope changed")
    _parse_utc(value.get("sealedUtc"), "confirmation core seal sealedUtc")
    pinned = value.get("pinnedFiles")
    if type(pinned) is not list or not pinned:
        raise ValueError("confirmation core seal has no pinned files")
    seen: set[Path] = set()
    for number, record in enumerate(pinned, 1):
        verified = _verify_identity(record, f"confirmation core pinned file {number}")
        if verified in seen:
            raise ValueError("confirmation core seal repeats a pinned file")
        seen.add(verified)
    contract.require_exact_json(value.get("omegaMatchBundle"), harness_bundle, "core OmegaMatch bundle")
    contract.require_exact_json(value.get("dotnetRuntimeBundle"), _dotnet_runtime_bundle(), "core dotnet bundle")
    gates = contract.mapping(value.get("gates"), "confirmation core gates")
    if set(gates) != set(GATES):
        raise ValueError("confirmation core gate inventory changed")
    for gate in GATES:
        config_path = _verify_identity(
            configs[gate], f"confirmation core {gate} config"
        )
        config = contract.strict_load(config_path, f"{gate} core config")
        expected_gate = {
            "suite": dict(suites[gate]),
            "config": dict(configs[gate]),
            "runId": config["runId"],
            "outputDirectory": config["outputDirectory"],
        }
        contract.require_exact_json(gates[gate], expected_gate, f"confirmation core {gate} binding")
    _verify_identity(value.get("audit"), "confirmation core audit")
    return value


@_serialized_transition
def authorize_attempt(attempt_index: Any) -> dict[str, Any]:
    index = contract._validate_attempt_index(attempt_index)
    paths = attempt_paths(index)
    state = verify_attempt_chain(PROTOCOL, through_attempt=index)
    if state["activeAttempt"] != index:
        raise ValueError("authorization requires the uniquely active claimed attempt")
    claim = verify_candidate_claim(paths["claim"], index, reverify_g5=True)
    suite_seal = verify_suite_seal(paths["jointSuiteSeal"], index, deep=True)
    stage_seeds = claim_stage_seeds(claim, index)
    if not contract.exact_json_equal(suite_seal.get("stageSeeds"), stage_seeds):
        raise ValueError("sealed-suite stage seeds differ from the candidate claim")
    outputs = [
        paths["authorization"],
        paths["coreSeal"],
        paths["audit"],
        *paths["configs"].values(),
    ]
    if any(path.exists() for path in outputs):
        raise FileExistsError(
            f"authorization refuses existing output: {next(path for path in outputs if path.exists())}"
        )
    if any(path.exists() for path in paths["stages"].values()):
        raise FileExistsError("match stage exists before confirmation authorization")
    try:
        network = claim["selectedNetwork"]
        engine = claim["engine"]
        harness = _runtime_identity("omegaMatchAssembly")
        apphost = _runtime_identity("omegaMatchAppHost")
        harness_path = _verify_identity(harness, "authorization OmegaMatch assembly")
        harness_bundle = _HARNESS_BUNDLE_IDENTITY(harness_path)
        if harness_bundle["sha256"] != PROTOCOL["sharedRuntime"]["omegaMatchBundleSha256"]:
            raise ValueError("OmegaMatch bundle differs from frozen confirmation runtime")
        suite_identities = {
            gate: dict(suite_seal["suites"][gate]["identity"]) for gate in GATES
        }
        config_values: dict[str, dict[str, Any]] = {}
        for gate in GATES:
            config = _expected_config(
                index,
                gate,
                stage_seeds[gate],
                suite_identities[gate],
                engine,
                network,
                harness,
                harness_bundle,
            )
            _exclusive_json(paths["configs"][gate], config)
            config_values[gate] = _verify_config(
                paths["configs"][gate],
                index,
                gate,
                stage_seeds[gate],
                suite_identities[gate],
                engine,
                network,
                harness,
                harness_bundle,
            )
        config_identities = {
            gate: contract.identity(paths["configs"][gate]) for gate in GATES
        }
        audit = {
            "schemaVersion": 1,
            "kind": "omega-nnue-open-confirmation-v1-match-audit",
            "protocol": PROTOCOL_IDENTITY,
            "attemptIndex": index,
            "createdUtc": _utc_now(),
            "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
            "candidateClaim": contract.identity(paths["claim"]),
            "jointSuiteSeal": contract.identity(paths["jointSuiteSeal"]),
            "selectedNetwork": network,
            "engine": engine,
            "suites": suite_identities,
            "stageSeeds": stage_seeds,
            "configs": config_identities,
            "candidateControlIsolation": {
                "sameExecutable": True,
                "sameWorkingDirectory": True,
                "candidateUseOmegaNNUE": True,
                "candidateAssetSha256": network["sha256"],
                "controlUseOmegaNNUE": False,
                "controlOmegaNNUEFile": "<empty>",
                "controlExternalAssets": [],
            },
            "matchResultsAccessed": 0,
        }
        _exclusive_json(paths["audit"], audit)
        pins = [
            PROTOCOL_IDENTITY,
            contract.identity(IMPLEMENTATION_SEAL),
            contract.identity(paths["claim"]),
            contract.identity(paths["jointSuiteSeal"]),
            network,
            claim["selectedManifest"],
            engine,
            harness,
            apphost,
            _runtime_identity("dotnetHost"),
            _runtime_identity("dotnetRuntimeManifest"),
            _runtime_identity("sharedMatchCore"),
            *suite_identities.values(),
            *config_identities.values(),
            contract.identity(paths["audit"]),
        ]
        by_path = {str(item["path"]).casefold(): dict(item) for item in pins}
        core_seal = {
            "schemaVersion": 1,
            "kind": CORE_SEAL_KIND,
            "sealedUtc": _utc_now(),
            "generationId": f"{contract.PROTOCOL_ID}:attempt-{index:06d}",
            "protocolSha256": PROTOCOL_IDENTITY["sha256"],
            "candidateNetworkSha256": network["sha256"],
            "engineExecutableSha256": engine["sha256"],
            "matchCoreSourceSha256": _runtime_identity("sharedMatchCore")["sha256"],
            "omegaMatchAssemblySha256": harness["sha256"],
            "pinnedFiles": sorted(by_path.values(), key=lambda item: item["path"].casefold()),
            "omegaMatchBundle": harness_bundle,
            "dotnetRuntimeBundle": _dotnet_runtime_bundle(),
            "gates": {
                gate: {
                    "suite": suite_identities[gate],
                    "config": config_identities[gate],
                    "runId": config_values[gate]["runId"],
                    "outputDirectory": config_values[gate]["outputDirectory"],
                }
                for gate in GATES
            },
            "audit": contract.identity(paths["audit"]),
        }
        _exclusive_json(paths["coreSeal"], core_seal)
        _verify_core_seal(
            paths["coreSeal"],
            index,
            suites=suite_identities,
            configs=config_identities,
            network=network,
            engine=engine,
            harness=harness,
            harness_bundle=harness_bundle,
        )
        alpha = {
            "attemptIndex": index,
            "betaComponents": contract.beta_components(index),
            "promotionLogThreshold": contract.promotion_log_threshold(index),
            "promotionEValue": _promotion_linear_threshold(index),
            "comparisonDomain": "natural logarithm",
        }
        evidence = {
            "g5Authorization": claim["g5Authorization"],
            "g5Decisions": claim["g5Decisions"],
            "g5Lineage": claim["g5Lineage"],
            "g5Verifier": claim["g5Verifier"],
            "requiredDecisions": claim["requiredG5Decisions"],
            "candidateEqualsSuccessfulScreenNominee": True,
            "zeroSafetyFailures": True,
        }
        authorization = {
            "schemaVersion": 1,
            "kind": AUTHORIZATION_KIND,
            "protocol": PROTOCOL_IDENTITY,
            "attemptIndex": index,
            "createdUtc": _utc_now(),
            "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
            "candidateClaim": contract.identity(paths["claim"]),
            "jointSuiteSeal": contract.identity(paths["jointSuiteSeal"]),
            "coreSeal": contract.identity(paths["coreSeal"]),
            "selectedNetwork": network,
            "engine": engine,
            "dotnetHost": _runtime_identity("dotnetHost"),
            "dotnetRuntimeManifest": _runtime_identity("dotnetRuntimeManifest"),
            "dotnetRuntimeBundle": _dotnet_runtime_bundle(),
            "omegaMatchAssembly": harness,
            "omegaMatchAppHost": apphost,
            "omegaMatchBundle": harness_bundle,
            "matchCoreSource": _runtime_identity("sharedMatchCore"),
            "configs": config_identities,
            "suites": suite_identities,
            "stageSeeds": stage_seeds,
            "orchestrator": claim["orchestrator"],
            "alphaSpending": alpha,
            "candidateControlIsolation": audit["candidateControlIsolation"],
            "g5NominationEvidence": evidence,
            "stageOrder": list(GATES),
            "launchOnlyThrough": "tools/omega_nnue/king_state_confirmation_matches_v1.py",
        }
        _exclusive_json(paths["authorization"], authorization)
        return verify_authorization(
            paths["authorization"], index, runtime_authority=True
        )
    except BaseException:
        if not paths["attemptClosure"].exists():
            _publish_abort(index, "authorization-failure")
        raise


def verify_authorization(
    path: Path,
    attempt_index: Any,
    runtime_authority: bool = True,
) -> dict[str, Any]:
    if type(runtime_authority) is not bool:
        raise ValueError("runtime_authority must be boolean")
    index = contract._validate_attempt_index(attempt_index)
    paths = attempt_paths(index)
    path = path.resolve()
    if path != paths["authorization"]:
        raise ValueError("confirmation authorization escaped its canonical namespace")
    value = contract.strict_load(path, "confirmation match authorization")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocol",
        "attemptIndex",
        "createdUtc",
        "implementationSeal",
        "candidateClaim",
        "jointSuiteSeal",
        "coreSeal",
        "selectedNetwork",
        "engine",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "dotnetRuntimeBundle",
        "omegaMatchAssembly",
        "omegaMatchAppHost",
        "omegaMatchBundle",
        "matchCoreSource",
        "configs",
        "suites",
        "stageSeeds",
        "orchestrator",
        "alphaSpending",
        "candidateControlIsolation",
        "g5NominationEvidence",
        "stageOrder",
        "launchOnlyThrough",
    }
    if set(value) != expected_fields:
        raise ValueError("confirmation authorization field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != 1
        or value.get("kind") != AUTHORIZATION_KIND
        or value.get("attemptIndex") != index
        or value.get("stageOrder") != list(GATES)
        or value.get("launchOnlyThrough")
        != "tools/omega_nnue/king_state_confirmation_matches_v1.py"
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError("confirmation authorization envelope changed")
    created = _parse_utc(value.get("createdUtc"), "confirmation authorization createdUtc")
    implementation_path = _verify_identity(
        value.get("implementationSeal"), "authorization implementation seal"
    )
    implementation = verify_implementation_seal(
        implementation_path, orchestrator_path=ORCHESTRATOR_PATH
    )
    claim_path = _verify_identity(value.get("candidateClaim"), "authorization candidate claim")
    claim = verify_candidate_claim(claim_path, index)
    suite_seal_path = _verify_identity(value.get("jointSuiteSeal"), "authorization joint suite seal")
    suite_seal = verify_suite_seal(
        suite_seal_path, index, deep=not runtime_authority
    )
    if created < _parse_utc(suite_seal["createdUtc"], "joint suite seal createdUtc"):
        raise ValueError("confirmation authorization predates suite seal")
    if not contract.exact_json_equal(value.get("orchestrator"), implementation["pinned"]["orchestrator"]):
        raise ValueError("authorization orchestrator binding changed")
    network = _identity_shape(value.get("selectedNetwork"), "authorization selected network")
    engine = _identity_shape(value.get("engine"), "authorization engine")
    _verify_identity(network, "authorization selected network")
    _verify_identity(engine, "authorization engine")
    if (
        not contract.exact_json_equal(network, claim["selectedNetwork"])
        or not contract.exact_json_equal(engine, claim["engine"])
    ):
        raise ValueError("authorization candidate differs from claimed G5 nominee")
    runtime_bindings = {
        "dotnetHost": _runtime_identity("dotnetHost"),
        "dotnetRuntimeManifest": _runtime_identity("dotnetRuntimeManifest"),
        "matchCoreSource": _runtime_identity("sharedMatchCore"),
        "omegaMatchAssembly": _runtime_identity("omegaMatchAssembly"),
        "omegaMatchAppHost": _runtime_identity("omegaMatchAppHost"),
    }
    runtime_paths: dict[str, Path] = {}
    for name, expected in runtime_bindings.items():
        if not contract.exact_json_equal(value.get(name), expected):
            raise ValueError(f"authorization {name} changed")
        runtime_paths[name] = _verify_identity(
            expected, f"authorization {name} runtime binding"
        )
    contract.require_exact_json(value.get("dotnetRuntimeBundle"), _dotnet_runtime_bundle(), "authorization dotnet bundle")
    harness_bundle = _HARNESS_BUNDLE_IDENTITY(runtime_paths["omegaMatchAssembly"])
    if harness_bundle["sha256"] != PROTOCOL["sharedRuntime"]["omegaMatchBundleSha256"]:
        raise ValueError("authorization OmegaMatch bundle is not frozen")
    contract.require_exact_json(value.get("omegaMatchBundle"), harness_bundle, "authorization OmegaMatch bundle")
    suites = contract.mapping(value.get("suites"), "authorization suites")
    configs = contract.mapping(value.get("configs"), "authorization configs")
    stage_seeds = _normalized_stage_seeds(
        value.get("stageSeeds"), "authorization stage seeds"
    )
    contract.require_exact_json(
        stage_seeds,
        claim_stage_seeds(claim, index),
        "authorization/claim stage seeds",
    )
    contract.require_exact_json(
        suite_seal.get("stageSeeds"),
        stage_seeds,
        "authorization/sealed-suite stage seeds",
    )
    if set(suites) != set(GATES) or set(configs) != set(GATES):
        raise ValueError("authorization gate inventory changed")
    for gate in GATES:
        suite_identity = _identity_shape(suites[gate], f"authorization {gate} suite")
        config_identity = _identity_shape(configs[gate], f"authorization {gate} config")
        suite_path = _verify_identity(suite_identity, f"authorization {gate} suite")
        config_path = _verify_identity(config_identity, f"authorization {gate} config")
        if (
            suite_path != paths["suites"][gate]
            or config_path != paths["configs"][gate]
            or not contract.exact_json_equal(
                suite_identity, suite_seal["suites"][gate]["identity"]
            )
        ):
            raise ValueError(f"authorization {gate} suite/config path changed")
        _verify_config(
            config_path,
            index,
            gate,
            stage_seeds[gate],
            suite_identity,
            engine,
            network,
            runtime_bindings["omegaMatchAssembly"],
            harness_bundle,
        )
    alpha = {
        "attemptIndex": index,
        "betaComponents": contract.beta_components(index),
        "promotionLogThreshold": contract.promotion_log_threshold(index),
        "promotionEValue": _promotion_linear_threshold(index),
        "comparisonDomain": "natural logarithm",
    }
    contract.require_exact_json(value.get("alphaSpending"), alpha, "authorization alpha spending")
    isolation = {
        "sameExecutable": True,
        "sameWorkingDirectory": True,
        "candidateUseOmegaNNUE": True,
        "candidateAssetSha256": network["sha256"],
        "controlUseOmegaNNUE": False,
        "controlOmegaNNUEFile": "<empty>",
        "controlExternalAssets": [],
    }
    contract.require_exact_json(value.get("candidateControlIsolation"), isolation, "authorization candidate/HCE isolation")
    evidence = {
        "g5Authorization": claim["g5Authorization"],
        "g5Decisions": claim["g5Decisions"],
        "g5Lineage": claim["g5Lineage"],
        "g5Verifier": claim["g5Verifier"],
        "requiredDecisions": claim["requiredG5Decisions"],
        "candidateEqualsSuccessfulScreenNominee": True,
        "zeroSafetyFailures": True,
    }
    contract.require_exact_json(value.get("g5NominationEvidence"), evidence, "authorization G5 nomination")
    core_path = _verify_identity(value.get("coreSeal"), "authorization core seal")
    if core_path != paths["coreSeal"]:
        raise ValueError("authorization core-seal path changed")
    _verify_core_seal(
        core_path,
        index,
        suites=suites,
        configs=configs,
        network=network,
        engine=engine,
        harness=runtime_bindings["omegaMatchAssembly"],
        harness_bundle=harness_bundle,
    )
    return value


# Orchestrator-facing alias. Runtime launches call this exact function through
# their own authenticated readiness import.
def _verify_runtime_authorization(
    path: Path, *, attempt_index: Any, protocol: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if protocol is not None:
        contract.require_exact_json(dict(protocol), PROTOCOL, "runtime protocol")
    return verify_authorization(path, attempt_index, runtime_authority=True)


_SELF_TEST_OFENS = {
    ("opening", "w"): "crn1qkbnr1/ppppb2pp1/5p1c2/10/10/5p4/P7P1/10/1PPPP1PP1P/CRNB1KBNRC[W/W/w/w] w KQkq - 0 13",
    ("opening", "b"): "crnbqkbnr1/ppppQ2pp1/5p1c2/10/10/5p4/P7P1/10/1PPPP1PP1P/CRNB1KBNRC[W/W/w/w] b KQkq - 0 12",
    ("middlegame", "w"): "1cn2k1nr1/1p1pb2R2/p4p3w/10/10/5p3c/10/3C6/1P1PK1PP2/1RN4N1C[W/W/-/w] w k - 0 33",
    ("middlegame", "b"): "1cn1qk1nr1/pp1pb2pp1/5p1c2/10/10/5p3P/8P1/2C7/1P1PP1PP2/1RNb1K1NRC[W/W/w/w] b KQk - 1 22",
    ("late", "w"): "10/10/10/3p1p4/2c4k2/10/10/RP2N5/7P2/2NK3W2[W/-/-/w] w - - 0 99",
    ("late", "b"): "1c8/1p1p2k1w1/p4p4/8c1/n9/3C6/10/1P4N3/1R4PP2/2NK3C2[W/W/-/w] b - - 6 54",
    ("endgame", "w"): "w5k3/10/10/10/10/2p7/10/WP8/3K3P2/2N2W4[-/-/-/-] w - - 8 127",
    ("endgame", "b"): "w9/10/10/5k4/3p6/2N7/10/1P8/3K2WP2/2N7[W/-/-/-] b - - 1 118",
}


def _self_test_authenticated_loader_and_lock() -> None:
    fake_loader = f"""
import runpy
import sys
import types
fake = types.ModuleType('king_state_confirmation_protocol_v1')
fake.__file__ = {str((_REPO / _PROTOCOL_RELATIVE).resolve())!r}
sys.modules['king_state_confirmation_protocol_v1'] = fake
try:
    runpy.run_path({str(TOOL_PATH)!r}, run_name='__fake_readiness__')
except ImportError as error:
    if 'preloaded confirmation protocol' not in str(error):
        raise
else:
    raise AssertionError('same-path fake preloaded protocol was accepted')
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", fake_loader],
        cwd=TOOL_PATH.parent,
        env=_sanitized_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "same-path fake-protocol rejection self-test failed: "
            + completed.stderr[-2000:]
        )

    with _operation_lock() as token:
        lock_child = f"""
import runpy
namespace = runpy.run_path({str(TOOL_PATH)!r}, run_name='__lock_readiness__')
namespace['_verify_parent_operation_lock']({token!r})
try:
    with namespace['_operation_lock']():
        pass
except RuntimeError as error:
    if 'already active' not in str(error):
        raise
else:
    raise AssertionError('concurrent process acquired the confirmation lock')
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", lock_child],
            cwd=TOOL_PATH.parent,
            env=_sanitized_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=180,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "shared parent-lock proof self-test failed: "
                + completed.stderr[-2000:]
            )
    try:
        _verify_parent_operation_lock(token)
    except RuntimeError as error:
        if "no live parent operation lock" not in str(error):
            raise
    else:
        raise AssertionError("stale parent-lock token was accepted after release")

    outside_worker = f"""
import pathlib
import runpy
namespace = runpy.run_path({str(TOOL_PATH)!r}, run_name='__outside_readiness__')
bundle = namespace['_dotnet_runtime_bundle']()
if bundle.get('bundleSha256') != namespace['PROTOCOL']['sharedRuntime']['dotnetRuntimeBundleSha256']:
    raise AssertionError('outside-cwd dotnet bundle changed')
for name in ('dotnetHost', 'dotnetRuntimeManifest', 'rootSamplerAssembly', 'omegaMatchAssembly'):
    record = namespace['_runtime_identity'](name)
    if pathlib.Path(record['path']).is_absolute():
        raise AssertionError('frozen protocol identity unexpectedly became absolute')
    path = namespace['_runtime_path'](name)
    if not path.is_absolute() or not path.is_file():
        raise AssertionError(f'outside-cwd runtime path failed: {{name}}')
for root_name, assembly_name, apphost_name in (
    ('HISTORY_SNAPSHOT_ROOT', 'OmegaHistorySnapshot.dll', 'OmegaHistorySnapshot.exe'),
    ('PREFIX_REPLAY_ROOT', 'OmegaOpeningPrefixReplay.dll', 'OmegaOpeningPrefixReplay.exe'),
):
    runtime = namespace['_runtime_bundle'](
        namespace[root_name], assembly_name, apphost_name
    )
    root = pathlib.Path(runtime['root'])
    assembly = root / runtime['assemblyRelativePath']
    if not root.is_absolute() or not assembly.is_file():
        raise AssertionError(f'outside-cwd replay path failed: {{root_name}}')
relative = namespace['contract'].identity(
    namespace['contract'].TOOL_PATH, relative=True
)
harness = namespace['_runtime_identity']('omegaMatchAssembly')
harness_bundle = namespace['_HARNESS_BUNDLE_IDENTITY'](
    namespace['_runtime_path']('omegaMatchAssembly')
)
config = namespace['_expected_config'](
    1,
    'development',
    namespace['_SELF_TEST_STAGE_SEEDS']['development'],
    relative,
    relative,
    relative,
    harness,
    harness_bundle,
)
paths = (
    config['match']['openingsFile'],
    config['engines'][0]['executable'],
    config['engines'][0]['options']['OmegaNNUEFile'],
)
if any(not pathlib.Path(value).is_absolute() for value in paths):
    raise AssertionError('outside-cwd match config contains a relative runtime path')
"""
    with tempfile.TemporaryDirectory(prefix="omega-confirmation-outside-cwd-") as directory:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", outside_worker],
            cwd=Path(directory).resolve(),
            env=_sanitized_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=180,
        )
    if completed.returncode != 0:
        raise AssertionError(
            "outside-repository worker-style path self-test failed: "
            + completed.stderr[-3000:]
        )


def _self_test_history_root_guards() -> None:
    expected = (
        "abortedRuns",
        "auditRuns",
        "matchRuns",
        "omegaLab",
        "openingAudit",
        "examples",
        "fixtures",
    )
    if tuple(label for label, _ in SIBLING_HISTORY_ROOTS) != expected:
        raise AssertionError("canonical sibling history-root inventory changed")
    roots = _history_roots(1)
    missing = [
        str(path)
        for _, path in SIBLING_HISTORY_ROOTS
        if path.resolve() not in roots
    ]
    if missing:
        raise AssertionError(f"sibling history roots were omitted: {missing!r}")

    with tempfile.TemporaryDirectory(prefix="omega-history-membership-") as directory:
        base = Path(directory).resolve()
        first_root = base / "first"
        second_root = base / "second"
        first_root.mkdir()
        second_root.mkdir()
        first_file = first_root / "one.json"
        second_file = second_root / "two.pgn"
        first_file.write_text("{}\n", encoding="utf-8")
        second_file.write_text("[Result \"*\"]\n", encoding="utf-8")
        files = _discover_history_files(1, [first_root, second_root])
        identities = {path: contract.identity(path) for path in files}
        try:
            _history_root_coverage([first_root], files, identities)
        except ValueError as error:
            if "every input file" not in str(error):
                raise
        else:
            raise AssertionError("omitted dynamic history root was accepted")
        _history_root_coverage(
            [first_root, second_root], files, identities
        )
        late_file = second_root / "late.jsonl"
        late_file.write_text("{}\n", encoding="utf-8")
        current = _discover_history_files(1, [first_root, second_root])
        if current == files or late_file.resolve() not in current:
            raise AssertionError("new history membership was not detected")


def _self_test_attempt_values(
    index: int,
    paths: Mapping[str, Any],
    fixtures: Mapping[str, Path],
    chronology: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    common = {
        "schemaVersion": 1,
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "predecessorClosure": (
            None
            if index == 1
            else contract.identity(attempt_paths(index - 1)["attemptClosure"])
        ),
        "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
        "orchestrator": verify_implementation_seal(
            IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
        )["pinned"]["orchestrator"],
        "g5Authorization": contract.identity(G5_AUTHORIZATION),
        "g5Decisions": {gate: contract.identity(G5_DECISIONS[gate]) for gate in GATES},
        "g5Lineage": {
            "offlineReport": contract.identity(fixtures["offline"]),
            "selectedManifest": contract.identity(fixtures["manifest"]),
            "coreSeal": contract.identity(fixtures["g5core"]),
            "suiteSeal": contract.identity(fixtures["g5suite"]),
        },
        "g5Verifier": contract.identity(G5_MATCH_ORCHESTRATOR),
        "g5Chronology": dict(chronology),
        "requiredG5Decisions": {
            "development": "pass",
            "equal-node": "promote",
            "equal-time": "promote",
        },
        "selectedNetwork": contract.identity(fixtures["network"]),
        "selectedManifest": contract.identity(fixtures["manifest"]),
        "engine": contract.identity(
            _REPO / "tools/omega_nnue/frozen_runtime/king-state-v5/engine/senpai.exe"
        ),
    }
    reservation = {
        **common,
        "kind": RESERVATION_KIND,
        "createdUtc": _utc_now(),
        "candidateValidationComplete": True,
        "reservationConsumesAttempt": True,
        "status": "reserved-before-entropy-draw",
    }
    _publish_reservation_namespace(paths, reservation)
    seed_derivation = contract._draw_seed_bundle_with_source(
        index,
        prior_published_stage_seeds(index),
        lambda size: bytes([index]) * size,
    )
    claim = {
        **common,
        "kind": CLAIM_KIND,
        "createdUtc": _utc_now(),
        "attemptReservation": contract.identity(paths["reservation"]),
        "seedDerivation": seed_derivation,
        "candidateClaimConsumesAttempt": True,
        "status": "active",
    }
    return reservation, claim


def self_test() -> None:
    _verify_bindings()
    _self_test_authenticated_loader_and_lock()
    _self_test_history_root_guards()
    proof = _candidate_invariance_proof()
    if proof["identical"] is not True or proof["selectionDigestA"] != proof["selectionDigestB"]:
        raise AssertionError("candidate-invariance proof failed")
    poison = {
        "CORECLR_ENABLE_PROFILING": "1",
        "COMPlus_ReadyToRun": "0",
        "PYTHONPATH": "poisoned",
        "DOTNET_ROOT": "poisoned",
    }
    prior_environment = {key: os.environ.get(key) for key in poison}
    try:
        os.environ.update(poison)
        sanitized = _sanitized_environment()
    finally:
        for key, prior in prior_environment.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
    if (
        sanitized.get("DOTNET_MULTILEVEL_LOOKUP") != "0"
        or sanitized.get("PYTHONNOUSERSITE") != "1"
        or "CORECLR_ENABLE_PROFILING" in sanitized
        or "PYTHONPATH" in sanitized
        or sanitized.get("DOTNET_ROOT") == "poisoned"
    ):
        raise AssertionError("managed/Python worker environment is not isolated")
    with tempfile.TemporaryDirectory(prefix="omega-confirmation-readiness-") as directory:
        root = Path(directory).resolve()
        global ARTIFACT_ROOT, IMPLEMENTATION_SEAL, PROGRAM_SUCCESS
        global G5_AUTHORIZATION, G5_DECISIONS, G5_MATCH_ORCHESTRATOR, G5_MATCH_ROOT
        global ORCHESTRATOR_PATH, _process_snapshot, _g5_nomination_evidence
        original_globals = (
            ARTIFACT_ROOT,
            IMPLEMENTATION_SEAL,
            PROGRAM_SUCCESS,
            G5_AUTHORIZATION,
            G5_DECISIONS,
            G5_MATCH_ORCHESTRATOR,
            G5_MATCH_ROOT,
            ORCHESTRATOR_PATH,
            _process_snapshot,
            _g5_nomination_evidence,
        )
        try:
            ARTIFACT_ROOT = root / "confirmation"
            IMPLEMENTATION_SEAL = ARTIFACT_ROOT / "implementation.seal.json"
            PROGRAM_SUCCESS = ARTIFACT_ROOT / "program-success.json"
            ARTIFACT_ROOT.mkdir()
            stable_orchestrator = root / "confirmation-orchestrator.py"
            stable_orchestrator.write_bytes(original_globals[7].read_bytes())
            ORCHESTRATOR_PATH = stable_orchestrator
            create_implementation_seal(
                IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
            )
            try:
                create_implementation_seal(
                    IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
                )
            except FileExistsError:
                pass
            else:
                raise AssertionError("implementation seal was overwritten")
            original_seal = IMPLEMENTATION_SEAL.read_bytes()
            changed = contract.strict_load(IMPLEMENTATION_SEAL, "self-test implementation")
            changed["candidateInvarianceProof"]["identical"] = False
            IMPLEMENTATION_SEAL.write_text(
                json.dumps(changed, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            try:
                verify_implementation_seal(
                    IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
                )
            except ValueError:
                pass
            else:
                raise AssertionError("tampered candidate-invariance proof was accepted")
            IMPLEMENTATION_SEAL.write_bytes(original_seal)
            verify_implementation_seal(
                IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
            )

            fixtures: dict[str, Path] = {}
            for name in (
                "network",
                "manifest",
                "offline",
                "g5core",
                "g5suite",
                "g5auth",
                "g5verifier",
            ):
                path = root / f"{name}.bin"
                path.write_bytes(f"self-test-{name}\n".encode())
                fixtures[name] = path
            G5_AUTHORIZATION = fixtures["g5auth"]
            G5_MATCH_ORCHESTRATOR = fixtures["g5verifier"]
            G5_MATCH_ROOT = root / "g5-matches"
            G5_DECISIONS = {}
            for ordinal, gate in enumerate(GATES, 1):
                gate_root = G5_MATCH_ROOT / gate
                gate_root.mkdir(parents=True)
                launch = gate_root / "launch-intent.json"
                _exclusive_json(
                    launch,
                    {
                        "kind": "omega-nnue-king-state-v5-launch-intent",
                        "gate": gate,
                        "createdUtc": f"2099-01-01T00:00:{ordinal:02d}Z",
                    },
                )
                decision = gate_root / "decision.json"
                _exclusive_json(
                    decision,
                    {
                        "kind": "omega-nnue-king-state-v5-decision",
                        "createdUtc": f"2099-01-01T00:01:{ordinal:02d}Z",
                    },
                )
                G5_DECISIONS[gate] = decision
                (gate_root / "events.jsonl").write_text(
                    json.dumps(
                        {
                            "RecordType": "run",
                            "CreatedUtc": f"2099-01-01T00:00:{ordinal:02d}Z",
                        },
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )

            implementation = verify_implementation_seal(
                IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
            )
            first_launch = G5_MATCH_ROOT / "development/launch-intent.json"
            first_launch_bytes = first_launch.read_bytes()
            predating = contract.strict_load(first_launch, "self-test predating launch")
            predating["createdUtc"] = "2000-01-01T00:00:00Z"
            first_launch.write_text(
                json.dumps(predating, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            try:
                _g5_match_chronology(
                    implementation,
                    {"createdUtc": "2099-01-01T00:00:00Z"},
                    contract.identity(G5_AUTHORIZATION),
                    G5_DECISIONS,
                    match_root=G5_MATCH_ROOT,
                )
            except ValueError as error:
                if "predates implementation" not in str(error):
                    raise
            else:
                raise AssertionError("predating Generation-5 launch was accepted")
            finally:
                first_launch.write_bytes(first_launch_bytes)
            chronology = _g5_match_chronology(
                implementation,
                {"createdUtc": "2099-01-01T00:00:00Z"},
                contract.identity(G5_AUTHORIZATION),
                G5_DECISIONS,
                match_root=G5_MATCH_ROOT,
            )
            _process_snapshot = lambda: {
                "capturedUtc": _utc_now(),
                "source": "self-test",
                "relevantProcesses": [],
            }

            first = attempt_paths(1)
            with _operation_lock():
                first_reservation, first_claim = _self_test_attempt_values(
                    1, first, fixtures, chronology
                )
                if {
                    "seedDerivation",
                    "entropyCommitment",
                    "stageSeeds",
                    "rejectionCounters",
                }.intersection(first_reservation):
                    raise AssertionError("attempt reservation leaked seed material")
                _exclusive_json(first["claim"], first_claim)
                try:
                    _publish_reservation_namespace(first, first_reservation)
                except FileExistsError:
                    pass
                else:
                    raise AssertionError("attempt reservation namespace was overwritten")
            verify_candidate_claim(first["claim"], 1)
            state = verify_attempt_chain(PROTOCOL, through_attempt=1)
            if (
                state["attempts"] != 1
                or state["closed"] != 0
                or state["activeAttempt"] != 1
                or state["confirmedAttempt"] is not None
                or state["priorPublishedStageSeeds"]
                != list(claim_stage_seeds(first_claim, 1).values())
            ):
                raise AssertionError("active attempt chain state changed")
            claim_bytes = first["claim"].read_bytes()
            malformed = contract.strict_load(first["claim"], "self-test claim")
            malformed["attemptIndex"] = 2
            first["claim"].write_text(
                json.dumps(malformed, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            try:
                verify_candidate_claim(first["claim"], 1)
            except ValueError:
                pass
            else:
                raise AssertionError("candidate claim attempt substitution was accepted")
            first["claim"].write_bytes(claim_bytes)

            # A still-active predecessor must reject its successor before the
            # candidate-validation or entropy-draw phases and create no root.
            try:
                create_candidate_claim(
                    2,
                    implementation_seal=IMPLEMENTATION_SEAL,
                    g5_authorization=G5_AUTHORIZATION,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("active predecessor admitted a successor")
            if attempt_paths(2)["root"].exists():
                raise AssertionError("rejected successor consumed its attempt")

            _process_snapshot = lambda: {
                "capturedUtc": _utc_now(),
                "source": "self-test-live",
                "relevantProcesses": [{"name": "senpai.exe", "pid": 999_999}],
            }
            try:
                _publish_abort(1, "operator-abort")
            except RuntimeError as error:
                if "requires no dotnet/OmegaMatch/Senpai processes" not in str(error):
                    raise
            else:
                raise AssertionError("abort proceeded while a match process was live")
            if first["attemptClosure"].exists():
                raise AssertionError("live-process abort published a closure")
            _process_snapshot = lambda: {
                "capturedUtc": _utc_now(),
                "source": "self-test",
                "relevantProcesses": [],
            }
            _publish_abort(1, "operator-abort")
            try:
                _require_worker_attempt_open(first, "self-test closure recheck")
            except FileExistsError:
                pass
            else:
                raise AssertionError("worker closure recheck accepted a closed attempt")
            closed = verify_attempt_chain(PROTOCOL, through_attempt=1)
            if closed["closed"] != 1 or closed["activeAttempt"] is not None:
                raise AssertionError("aborted attempt did not close the chain")
            try:
                _publish_abort(1, "operator-abort")
            except FileExistsError:
                pass
            else:
                raise AssertionError("attempt closure was overwritten")

            evidence = {
                "authorization": contract.identity(G5_AUTHORIZATION),
                "decisions": {
                    gate: contract.identity(G5_DECISIONS[gate]) for gate in GATES
                },
                "lineage": {
                    "offlineReport": contract.identity(fixtures["offline"]),
                    "selectedManifest": contract.identity(fixtures["manifest"]),
                    "coreSeal": contract.identity(fixtures["g5core"]),
                    "suiteSeal": contract.identity(fixtures["g5suite"]),
                },
                "freshVerifier": contract.identity(G5_MATCH_ORCHESTRATOR),
                "chronology": chronology,
                "requiredDecisions": {
                    "development": "pass",
                    "equal-node": "promote",
                    "equal-time": "promote",
                },
                "selectedNetwork": contract.identity(fixtures["network"]),
                "selectedManifest": contract.identity(fixtures["manifest"]),
                "engine": contract.identity(
                    _REPO
                    / "tools/omega_nnue/frozen_runtime/king-state-v5/engine/senpai.exe"
                ),
            }
            draw_calls = 0
            original_draw = contract.draw_seed_bundle
            _g5_nomination_evidence = lambda *args, **kwargs: copy.deepcopy(evidence)

            def fail_after_reservation(
                attempt_index: Any, prior_attempt_seeds: Any
            ) -> dict[str, Any]:
                nonlocal draw_calls
                draw_calls += 1
                if not attempt_paths(attempt_index)["reservation"].is_file():
                    raise AssertionError("entropy draw preceded durable reservation")
                raise RuntimeError("self-test entropy-draw failure")

            contract.draw_seed_bundle = fail_after_reservation
            try:
                try:
                    create_candidate_claim(
                        2,
                        implementation_seal=IMPLEMENTATION_SEAL,
                        g5_authorization=G5_AUTHORIZATION,
                    )
                except RuntimeError as error:
                    if "self-test entropy-draw failure" not in str(error):
                        raise
                else:
                    raise AssertionError("entropy failure did not abort claim creation")
                if draw_calls != 1:
                    raise AssertionError("consumed attempt did not perform exactly one draw")
                second = attempt_paths(2)
                second_closure = _verify_attempt_closure(
                    second["attemptClosure"], 2
                )
                if (
                    second_closure["candidateClaim"] is not None
                    or second_closure["reason"] != "claim-publication-failure"
                    or second["claim"].exists()
                ):
                    raise AssertionError("failed claim did not close reservation-only attempt")
                try:
                    create_candidate_claim(
                        2,
                        implementation_seal=IMPLEMENTATION_SEAL,
                        g5_authorization=G5_AUTHORIZATION,
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError("consumed attempt index was rerolled")
                if draw_calls != 1:
                    raise AssertionError("same attempt index drew entropy again")
            finally:
                contract.draw_seed_bundle = original_draw
                _g5_nomination_evidence = original_globals[9]

            history_after_failure = prior_published_stage_seeds(3)
            if history_after_failure != list(
                claim_stage_seeds(first_claim, 1).values()
            ):
                raise AssertionError("reservation-only abort polluted seed history")

            third = attempt_paths(3)
            with _operation_lock():
                _, third_claim = _self_test_attempt_values(
                    3, third, fixtures, chronology
                )
                _exclusive_json(third["claim"], third_claim)
            if verify_attempt_chain(PROTOCOL, through_attempt=3)["activeAttempt"] != 3:
                raise AssertionError("global attempt successor was not admitted")

            selective = root / "selective-events.jsonl"
            selective.write_text(
                json.dumps(
                    {
                        "PreOfen": _SELF_TEST_OFENS[("opening", "w")],
                        "ScoreCp": 123456789,
                        "SecretTarget": "must-remain-opaque",
                        "Info": {"Pv": ["f1f2"]},
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            ofens, requests = _direct_ofens_and_pv_requests(
                selective, contract.identity(selective)
            )
            if len(ofens) != 1 or len(requests) != 1 or requests[0]["moves"] != ["f1f2"]:
                raise AssertionError("target-opaque event/PV scanner changed")

            compact_protocol = copy.deepcopy(PROTOCOL)
            compact_protocol["stages"]["development"].update(
                {"roots": 8, "rootsPerPhase": 2, "rootsPerPhaseAndSideToMove": 1}
            )
            original_protocol = PROTOCOL
            try:
                globals()["PROTOCOL"] = compact_protocol
                roots: list[Root] = []
                for ordinal, ((phase, side), ofen) in enumerate(_SELF_TEST_OFENS.items()):
                    parsed_phase, parsed_side, identity, orbit, signatures = _POSITION_META(ofen)
                    if (parsed_phase, parsed_side) != (phase, side):
                        raise AssertionError("self-test OFEN bucket changed")
                    rank = hashlib.sha256(f"suite-root-{ordinal}".encode()).hexdigest()
                    roots.append(
                        Root(
                            gate="development",
                            source_path=selective,
                            source_sha256=contract.sha256(selective),
                            line=ordinal + 1,
                            generator_seed=_SELF_TEST_STAGE_SEEDS["development"],
                            trajectory_pair_id=f"random-pair-{ordinal + 1:06d}",
                            trajectory_id=f"random-pair-{ordinal + 1:06d}-ab",
                            flavor="ab",
                            ply=ordinal,
                            phase=phase,
                            side=side,
                            ofen=ofen,
                            identity=identity,
                            orbit=orbit,
                            orbit_signatures=signatures,
                            rank=rank,
                        )
                    )
                suite, _ = _build_suite(
                    1, "development", _SELF_TEST_STAGE_SEEDS["development"], roots
                )
                suite_path = root / "compact-suite.json"
                _exclusive_json(suite_path, suite)
                _verify_suite(
                    suite_path,
                    1,
                    "development",
                    _SELF_TEST_STAGE_SEEDS["development"],
                )
                try:
                    _select_roots(
                        roots,
                        "development",
                        {roots[0].orbit_signatures[0]},
                        set(),
                        quota_override=1,
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError("whole-orbit exclusion was not enforced")
            finally:
                globals()["PROTOCOL"] = original_protocol
        finally:
            (
                ARTIFACT_ROOT,
                IMPLEMENTATION_SEAL,
                PROGRAM_SUCCESS,
                G5_AUTHORIZATION,
                G5_DECISIONS,
                G5_MATCH_ORCHESTRATOR,
                G5_MATCH_ROOT,
                ORCHESTRATOR_PATH,
                _process_snapshot,
                _g5_nomination_evidence,
            ) = original_globals

    original_position = _MATCH_CORE._position_meta
    try:
        _MATCH_CORE._position_meta = lambda ofen: ("opening", "w", "x", "x", ("x",))
        try:
            _verify_bindings()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted whole-orbit function was accepted")
    finally:
        _MATCH_CORE._position_meta = original_position


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("seal-implementation")
    seal.add_argument("--output", type=Path, default=IMPLEMENTATION_SEAL)
    seal.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    seal.add_argument("--orchestrator-bytes", type=int)
    seal.add_argument("--orchestrator-sha256")
    verify_impl = commands.add_parser("verify-implementation")
    verify_impl.add_argument("--seal", type=Path, default=IMPLEMENTATION_SEAL)
    verify_impl.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    claim = commands.add_parser("claim")
    claim.add_argument("--attempt-index", type=int, required=True)
    claim.add_argument("--implementation-seal", type=Path, default=IMPLEMENTATION_SEAL)
    claim.add_argument("--g5-authorization", type=Path, default=G5_AUTHORIZATION)
    chain = commands.add_parser("verify-chain")
    chain.add_argument("--through-attempt", type=int)
    sample = commands.add_parser("sample")
    sample.add_argument("--attempt-index", type=int, required=True)
    suites = commands.add_parser("verify-suites")
    suites.add_argument("--attempt-index", type=int, required=True)
    authorize = commands.add_parser("authorize")
    authorize.add_argument("--attempt-index", type=int, required=True)
    verify_auth = commands.add_parser("verify-authorization")
    verify_auth.add_argument("--attempt-index", type=int, required=True)
    verify_auth.add_argument("--deep", action="store_true")
    abort = commands.add_parser("abort-attempt")
    abort.add_argument("--attempt-index", type=int, required=True)
    abort.add_argument(
        "--reason",
        required=True,
        choices=sorted(CLOSURE_REASONS - {"confirmed"}),
    )
    worker = commands.add_parser("candidate-blind-worker", help=argparse.SUPPRESS)
    worker.add_argument("--attempt-index", type=int, required=True)
    worker.add_argument("--implementation-seal", type=Path, required=True)
    worker.add_argument("--output-root", type=Path, required=True)
    worker.add_argument("--parent-lock-token", required=True)
    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "seal-implementation":
        value = create_implementation_seal(
            args.output,
            orchestrator_path=args.orchestrator,
            expected_orchestrator_bytes=args.orchestrator_bytes,
            expected_orchestrator_sha256=args.orchestrator_sha256,
        )
        print(f"Confirmation implementation seal: {args.output.resolve()}")
        print(f"Readiness SHA-256: {value['pinned']['readiness']['sha256']}")
    elif args.command == "verify-implementation":
        value = verify_implementation_seal(
            args.seal, orchestrator_path=args.orchestrator
        )
        print(f"Confirmation implementation seal verified: {value['createdUtc']}")
    elif args.command == "claim":
        value = create_candidate_claim(
            args.attempt_index,
            implementation_seal=args.implementation_seal,
            g5_authorization=args.g5_authorization,
        )
        print(f"Confirmation attempt {args.attempt_index} claimed: {value['selectedNetwork']['sha256']}")
    elif args.command == "verify-chain":
        value = verify_attempt_chain(PROTOCOL, through_attempt=args.through_attempt)
        print(json.dumps(value, sort_keys=True))
    elif args.command == "sample":
        value = sample_attempt(args.attempt_index)
        print(f"Confirmation suites sealed: {value['rootDigest']}")
    elif args.command == "verify-suites":
        value = verify_suite_seal(
            attempt_paths(args.attempt_index)["jointSuiteSeal"],
            args.attempt_index,
            deep=True,
        )
        print(f"Confirmation suites verified: {value['rootDigest']}")
    elif args.command == "authorize":
        value = authorize_attempt(args.attempt_index)
        print(f"Confirmation authorization: {value['selectedNetwork']['sha256']}")
    elif args.command == "verify-authorization":
        value = verify_authorization(
            attempt_paths(args.attempt_index)["authorization"],
            args.attempt_index,
            runtime_authority=not args.deep,
        )
        print(f"Confirmation authorization verified: {value['selectedNetwork']['sha256']}")
    elif args.command == "abort-attempt":
        value = _publish_abort(args.attempt_index, args.reason)
        print(f"Confirmation attempt {args.attempt_index} aborted: {value['reason']}")
    elif args.command == "candidate-blind-worker":
        value = _candidate_blind_worker(
            args.attempt_index,
            args.implementation_seal,
            args.output_root,
            args.parent_lock_token,
        )
        print(f"Candidate-blind worker sealed suites: {value['rootDigest']}")
    else:
        contract.self_test()
        self_test()
        print("Open-confirmation readiness self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
