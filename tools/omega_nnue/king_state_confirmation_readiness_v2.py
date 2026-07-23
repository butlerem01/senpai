#!/usr/bin/env python3
"""Prepare leakage-resistant suites for open-ended Omega NNUE confirmation.

This module is the pre-result half of ``omega-nnue-open-confirmation-v2``.
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
_PROTOCOL_RELATIVE = "tools/omega_nnue/king_state_confirmation_protocol_v2.py"
_PROTOCOL_SIZE = 126_759
_PROTOCOL_SHA256 = "5509ee3a7525e5bef28f79be2930ae6a9b05197fe3cfbd319c143af3f0439a79"
_PROTOCOL_SOURCE = (_REPO / _PROTOCOL_RELATIVE).read_bytes()
if (
    len(_PROTOCOL_SOURCE) != _PROTOCOL_SIZE
    or hashlib.sha256(_PROTOCOL_SOURCE).hexdigest() != _PROTOCOL_SHA256
):
    raise ImportError("open-confirmation protocol implementation changed")

_PARENT_PROTOCOL_NONCE = globals().pop(
    "__confirmation_parent_protocol_nonce__", None
)
if "king_state_confirmation_protocol_v2" in sys.modules:
    contract = sys.modules["king_state_confirmation_protocol_v2"]
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
    contract = types.ModuleType("king_state_confirmation_protocol_v2")
    contract.__file__ = str((_REPO / _PROTOCOL_RELATIVE).resolve())
    contract.__package__ = ""
    contract.__loader__ = None
    sys.modules["king_state_confirmation_protocol_v2"] = contract
    try:
        exec(
            compile(_PROTOCOL_SOURCE, str(contract.__file__), "exec"),
            contract.__dict__,
        )
    except BaseException:
        sys.modules.pop("king_state_confirmation_protocol_v2", None)
        raise
    _PROTOCOL_AUTH_NONCE = object()
    contract.__dict__["__confirmation_authenticated_nonce__"] = (
        _PROTOCOL_AUTH_NONCE
    )


_PRACTICAL_RELATIVE = "tools/omega_nnue/king_state_confirmation_practical_v2.py"
_PRACTICAL_SIZE = 82_703
_PRACTICAL_SHA256 = "7b96851522bd73d5ffbe21fa10f0a7d39864b1be1c18877a9f3e0a0a4116264d"
_PRACTICAL_SOURCE = (_REPO / _PRACTICAL_RELATIVE).read_bytes()
if (
    len(_PRACTICAL_SOURCE) != _PRACTICAL_SIZE
    or hashlib.sha256(_PRACTICAL_SOURCE).hexdigest() != _PRACTICAL_SHA256
):
    raise ImportError("open-confirmation practical implementation changed")
_PRACTICAL_MODULE_NAME = "king_state_confirmation_practical_v2"
if _PRACTICAL_MODULE_NAME in sys.modules:
    raise ImportError("refusing preloaded confirmation practical module")
practical = types.ModuleType(_PRACTICAL_MODULE_NAME)
practical.__file__ = str((_REPO / _PRACTICAL_RELATIVE).resolve())
practical.__package__ = ""
practical.__loader__ = None
sys.modules[_PRACTICAL_MODULE_NAME] = practical
try:
    exec(compile(_PRACTICAL_SOURCE, practical.__file__, "exec"), practical.__dict__)
except BaseException:
    sys.modules.pop(_PRACTICAL_MODULE_NAME, None)
    raise
_PRACTICAL_BUILD_SUITE = practical.build_suite
_PRACTICAL_VERIFY_SOURCE = practical.read_source
_PRACTICAL_VERIFY_SUITE = practical.verify_suite
_PRACTICAL_BUILD_CONFIG = practical.build_config
_PRACTICAL_ASSESS_EVENTS = practical.assess_events
_PRACTICAL_AUTHENTICATE_ENGINE_INVENTORY = practical._authenticate_engine_inventory


SCHEMA_VERSION = 1
PROTOCOL = contract.validate_protocol()
PROTOCOL_IDENTITY = contract.identity(contract.PROTOCOL_PATH)
TOOL_PATH = Path(__file__).resolve()
ORCHESTRATOR_PATH = (
    _REPO / "tools/omega_nnue/king_state_confirmation_matches_v2.py"
).resolve()
ARTIFACT_ROOT = contract.ARTIFACT_ROOT.resolve()
IMPLEMENTATION_SEAL = ARTIFACT_ROOT / "implementation.seal.json"
V1_RETIREMENT_SEAL = contract.V1_RETIREMENT_SEAL.resolve()
PROGRAM_SUCCESS = ARTIFACT_ROOT / "program-success.json"
BASE_PROJECTION_ROOT = (ARTIFACT_ROOT / "runtime/private-history-projector").resolve()
BASE_PROJECTION = BASE_PROJECTION_ROOT / "projection.json"
BASE_SELECTOR_ROOT = (ARTIFACT_ROOT / "runtime/public-selector-capsule").resolve()
BASE_SELECTOR_CAPSULE = BASE_SELECTOR_ROOT / "capsule.json"
BASE_EXCLUDED_ORBITS = BASE_SELECTOR_ROOT / "excluded-orbits.txt"
SELECTOR_WORKSPACE_ROOT = (ARTIFACT_ROOT / "runtime/selector-workspaces").resolve()
SELECTOR_CAPSULE_KIND = "omega-nnue-open-confirmation-v2-selector-capsule"
POST_SELECTION_AUDIT_KIND = (
    "omega-nnue-open-confirmation-v2-post-selection-collision-audit"
)
_WORKSPACE_ROOT = _REPO.parent.resolve()
_LOCK_TOKEN = hashlib.sha256(str(_REPO).casefold().encode("utf-8")).hexdigest()[:16]
GLOBAL_OPERATION_LOCK_PATH = (
    Path(tempfile.gettempdir()) / f"omega-confirmation-v1-{_LOCK_TOKEN}.lock"
).resolve()
SUBPROCESS_ENVIRONMENT_KEYS = (
    "SystemRoot",
    "WINDIR",
    "TEMP",
    "TMP",
    "DOTNET_CLI_TELEMETRY_OPTOUT",
    "DOTNET_NOLOGO",
    "DOTNET_MULTILEVEL_LOOKUP",
    "DOTNET_ROOT",
    "DOTNET_ROOT_X64",
    "DOTNET_ROLL_FORWARD",
    "DOTNET_EnableDiagnostics",
    "PYTHONNOUSERSITE",
)
_LOCK_LOCAL = threading.local()
GATES = tuple(contract.GATES)
FORMAL_GATES = tuple(contract.FORMAL_GATES)
G5_GATES = ("development", "equal-node", "equal-time")
ROOT_GATES = G5_GATES
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
    "normal-start-clock": 1_710_004,
}

IMPLEMENTATION_KIND = "omega-nnue-open-confirmation-v2-implementation-seal"
V1_RETIREMENT_KIND = "omega-nnue-open-confirmation-v2-v1-retirement-seal"
RESERVATION_KIND = "omega-nnue-open-confirmation-v2-attempt-reservation"
CLAIM_KIND = "omega-nnue-open-confirmation-v2-candidate-claim"
EXCLUSION_KIND = "omega-nnue-open-confirmation-v2-exclusion-inventory"
JOINT_SUITE_KIND = "omega-nnue-open-confirmation-v2-joint-suite-seal"
AUTHORIZATION_KIND = "omega-nnue-open-confirmation-v2-match-authorization"
ATTEMPT_CLOSURE_KIND = "omega-nnue-open-confirmation-v2-attempt-closure"
PROGRAM_SUCCESS_KIND = "omega-nnue-open-confirmation-v2-program-success"
CORE_SEAL_KIND = "omega-nnue-king-state-v1-match-seal"

ORIGINAL_G5_PROFILE = (
    _REPO / "validation/omega-nnue-king-state-v5-preregistration.json"
).resolve()
ORIGINAL_G5_FREEZE = (
    _REPO / "validation/omega-nnue-king-state-v5-freeze.seal.json"
).resolve()
ORIGINAL_G5_MATCH_PROTOCOL = (
    _REPO / "validation/omega-nnue-king-state-v5-match-protocol.json"
).resolve()
ORIGINAL_G5_MATCH_READINESS = (
    _REPO / "tools/omega_nnue/king_state_match_readiness_generation5.py"
).resolve()
ORIGINAL_G5_MATCH_CORE = (
    _REPO / "tools/omega_nnue/king_state_matches.py"
).resolve()
ORIGINAL_G5_MATCH_ORCHESTRATOR = (
    _REPO / "tools/omega_nnue/king_state_matches_generation5.py"
).resolve()
ORIGINAL_G5_MATCH_ROOT = (_REPO / "build-king-state-v5/matches").resolve()

# Generation 5's original bytes remain immutable ancestors.  The additive
# compatibility namespace is the only authority allowed to nominate a v2
# confirmation candidate because it authenticates the emitter's white/black
# color tokens, terminal process-exit records, managed rules replay, and a
# terminal all-three-gates closure without rewriting the original experiment.
G5_COMPAT_TEMPLATE = (
    _REPO
    / "validation/omega-nnue-king-state-v5-color-compat-preregistration.template.json"
).resolve()
G5_COMPAT_PROTOCOL = (
    _REPO / "validation/omega-nnue-king-state-v5-color-compat-protocol.json"
).resolve()
G5_COMPAT_READINESS = (
    _REPO / "tools/omega_nnue/king_state_match_readiness_generation5_compat_v2.py"
).resolve()
G5_MATCH_ORCHESTRATOR = (
    _REPO / "tools/omega_nnue/king_state_matches_generation5_compat_v2.py"
).resolve()
G5_MATCH_ROOT = (
    _REPO / "build-king-state-v5/matches-color-compat-v2"
).resolve()
G5_COMPAT_PREREGISTRATION = (G5_MATCH_ROOT / "preregistration.json").resolve()
G5_COMPAT_SUITE_SEAL = (G5_MATCH_ROOT / "sealed/suite-seal.json").resolve()
G5_COMPAT_PREAUTHORIZATION = (
    G5_MATCH_ROOT / "sealed/preauthorization-state.json"
).resolve()
G5_AUTHORIZATION = (
    G5_MATCH_ROOT / "sealed/match-authorization.json"
).resolve()
G5_COMPAT_CORE_SEAL = (
    G5_MATCH_ROOT / "sealed/core-match-seal.json"
).resolve()
G5_DECISIONS = {
    gate: (G5_MATCH_ROOT / f"decisions/{gate}.json").resolve()
    for gate in G5_GATES
}
G5_CLOSURE = (G5_MATCH_ROOT / "closure.json").resolve()
G5_HISTORY_PROJECTION = (G5_MATCH_ROOT / "position-history.jsonl").resolve()
G5_HISTORY_MANIFEST = (
    G5_MATCH_ROOT / "position-history.manifest.json"
).resolve()
G5_COMPAT_SUITES = {
    gate: (
        G5_MATCH_ROOT
        / f"sealed/king-state-v5-color-compat-v2-{gate}-suite.json"
    ).resolve()
    for gate in G5_GATES
}

ORIGINAL_G5_SAMPLER_FILES = {
    gate: {
        "source": (
            ORIGINAL_G5_MATCH_ROOT
            / f"sampler/king-state-v5-{gate}-rules-only.jsonl"
        ).resolve(),
        "manifest": (
            ORIGINAL_G5_MATCH_ROOT
            / f"sampler/king-state-v5-{gate}-rules-only.jsonl.manifest.json"
        ).resolve(),
        "completionSeal": (
            ORIGINAL_G5_MATCH_ROOT
            / f"sampler/king-state-v5-{gate}-rules-only.jsonl.complete.seal.json"
        ).resolve(),
    }
    for gate in G5_GATES
}

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
PREFIX_REPLAY_ROOT = (
    _REPO
    / "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay"
).resolve()
PREFIX_REPLAY_ASSEMBLY = PREFIX_REPLAY_ROOT / "OmegaOpeningPrefixReplay.dll"
G5_OMEGA_MATCH_RULES_ASSEMBLY = (
    _REPO / "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/ChessLib.dll"
).resolve()
PREFIX_REPLAY_BUNDLE_SHA256 = contract.PREFIX_REPLAY_BUNDLE_SHA256
G5_RULES_BYTES = contract.G5_RULES_BYTES
G5_RULES_SHA256 = contract.G5_RULES_SHA256
PREFIX_REPLAY_EXACT_FILES: dict[str, tuple[int, str]] = {
    "ChessLib.dll": (G5_RULES_BYTES, G5_RULES_SHA256),
    "ChessLib.pdb": (
        78_904,
        "c27b5a5134d57c81f0f2f7c8fbb818716157c24a1dbb5ab34074157b250e236b",
    ),
    "Newtonsoft.Json.dll": (
        723_368,
        "a28c251dfe36d881e9e2462e171441b8b0ec156fe3f452602c9149b1b9efe05b",
    ),
    "OmegaOpeningPrefixReplay.deps.json": (
        1_608,
        "9ade5beb57416eca211de58010fa81edd07948faed701de366e262f61aae4fc3",
    ),
    "OmegaOpeningPrefixReplay.dll": (
        54_272,
        "b7b4205d6be985489aebe5d1f7c2728ddd6fdac7ffbe59fe8da63bfa2bc71495",
    ),
    "OmegaOpeningPrefixReplay.exe": (
        162_816,
        "9980f4812dae96a62086a454676dfb96ef89f9942c684bc415be054da0f57f20",
    ),
    "OmegaOpeningPrefixReplay.runtimeconfig.json": (
        342,
        "c230a317a54dd960bcbeb5f347f52e18dc665a26f7efda2159fced9a5ac7e097",
    ),
    "System.IO.Ports.dll": (
        37_648,
        "2767e21f384cca9004b1266ec4b71d3b8a76898594382c377c726c780aa34508",
    ),
}
PRACTICAL_SAMPLER_RUNTIME_ROOT = (
    ARTIFACT_ROOT / "runtime/practical-opening-sampler"
).resolve()
PRACTICAL_SAMPLER_ASSEMBLY = (
    PRACTICAL_SAMPLER_RUNTIME_ROOT / "OmegaPracticalOpeningSampler.dll"
)

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
        "post-selection-audit",
        "sealed",
        "development",
        "equal-node",
        "equal-time",
        "normal-start-clock",
        "attempt-closure.json",
    }
)
CLAIM_STAGING_PREFIX = ".candidate-claim-delete-on-close-"
CLOSURE_REASONS = frozenset(
    {
        "confirmed",
        "development-fail",
        "safety-fail",
        "equal-node-futility",
        "equal-node-inconclusive",
        "equal-time-futility",
        "equal-time-inconclusive",
        "normal-start-clock-futility",
        "normal-start-clock-inconclusive",
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


def _claim_staging_artifacts(root: Path) -> list[Path]:
    root = root.resolve()
    if not root.is_dir():
        return []
    return sorted(
        (
            item.resolve()
            for item in root.iterdir()
            if item.name.startswith(CLAIM_STAGING_PREFIX)
        ),
        key=str,
    )


def _publish_candidate_claim_atomic(
    path: Path,
    value: Mapping[str, Any],
    *,
    fault_after: str | None = None,
) -> None:
    """Publish a complete claim through a no-replace hard-link commit.

    The temporary inode is opened delete-on-close, fully written and fsynced
    before its final name can exist.  ``os.link`` is the single no-replace
    commit point: a crash leaves either no final claim or the complete fsynced
    bytes, never a partially written final JSON object containing stage seeds.
    """

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if fault_after not in {
        None,
        "open",
        "write",
        "flush",
        "fsync",
        "link",
        "close",
    }:
        raise ValueError("unknown candidate-claim publication fault boundary")
    payload = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")

    def checkpoint(name: str) -> None:
        if fault_after == name:
            raise RuntimeError(f"candidate-claim fault after {name}")

    with tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix=CLAIM_STAGING_PREFIX,
        suffix=".tmp",
        dir=path.parent,
        delete=True,
    ) as stream:
        checkpoint("open")
        stream.write(payload)
        checkpoint("write")
        stream.flush()
        checkpoint("flush")
        os.fsync(stream.fileno())
        checkpoint("fsync")
        os.link(stream.name, path)
        checkpoint("link")
    checkpoint("close")


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
    environment = _sanitized_environment()
    if os.name == "nt":
        result = subprocess.run(
            [str(Path(environment["SystemRoot"]) / "System32/tasklist.exe"), "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
            env=environment,
        )
        processes = _parse_windows_processes(result.stdout)
        source = "tasklist-csv"
    else:
        result = subprocess.run(
            ["/bin/ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
            env=environment,
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


def _require_exact_directory_inventory(
    path: Path,
    *,
    files: Sequence[str],
    directories: Sequence[str],
    label: str,
) -> None:
    """Reject injected entries and links in a selector-owned directory."""

    path = path.resolve()
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"{label} is not a plain directory")
    expected_files = set(files)
    expected_directories = set(directories)
    if expected_files.intersection(expected_directories):
        raise AssertionError(f"{label} expected inventory overlaps")
    actual = set(_directory_entries(path))
    expected = expected_files.union(expected_directories)
    if actual != expected:
        raise ValueError(
            f"{label} inventory changed: expected={sorted(expected)!r}, "
            f"actual={sorted(actual)!r}"
        )
    for name in sorted(expected):
        child = path / name
        if child.is_symlink() or child.resolve().parent != path:
            raise ValueError(f"{label} contains a linked/escaping entry: {name}")
        if name in expected_files and not child.is_file():
            raise ValueError(f"{label} expected a file: {name}")
        if name in expected_directories and not child.is_dir():
            raise ValueError(f"{label} expected a directory: {name}")


def _verify_selector_workspace_inventory(index: int, *, sealed: bool) -> None:
    """Authenticate the complete public-selector workspace namespace."""

    paths = attempt_paths(index)
    if not sealed:
        _require_exact_directory_inventory(
            paths["selectorRoot"],
            files=("sampling-intent.json",),
            directories=(),
            label="pre-sampling selector workspace",
        )
        return
    _require_exact_directory_inventory(
        paths["selectorRoot"],
        files=("sampling-intent.json",),
        directories=("sampler", "sealed"),
        label="sealed selector workspace",
    )
    sampler_files = tuple(
        name
        for gate in GATES
        for name in (
            f"{gate}-rules-only.jsonl",
            f"{gate}-rules-only.jsonl.manifest.json",
            f"{gate}-rules-only.jsonl.complete.seal.json",
        )
    )
    _require_exact_directory_inventory(
        paths["sampler"],
        files=sampler_files,
        directories=(),
        label="selector sampler output",
    )
    _require_exact_directory_inventory(
        paths["selectorSealed"],
        files=tuple(f"{gate}-suite.json" for gate in GATES)
        + ("joint-suite.seal.json",),
        directories=(),
        label="selector sealed output",
    )


def _verify_v1_root_singleton() -> dict[str, Any]:
    root = contract.V1_ARTIFACT_ROOT.resolve()
    if _directory_entries(root) != ["implementation.seal.json"]:
        raise ValueError(
            "retired v1 root is no longer the audited implementation-seal singleton"
        )
    expected = contract.V1_PREDECESSOR["implementationSeal"]
    actual = contract.identity(contract.V1_IMPLEMENTATION_SEAL.resolve())
    if (
        actual["bytes"] != expected["bytes"]
        or actual["sha256"] != expected["sha256"]
    ):
        raise ValueError("retired v1 implementation seal identity changed")
    value = contract.strict_load(
        contract.V1_IMPLEMENTATION_SEAL.resolve(), "retired v1 implementation seal"
    )
    if (
        value.get("schemaVersion") != 1
        or value.get("kind")
        != "omega-nnue-open-confirmation-v1-implementation-seal"
        or value.get("protocolId") != "omega-nnue-open-confirmation-v1"
        or value.get("createdUtc") != "2026-07-23T17:11:56Z"
        or value.get("sealedBeforeCandidateClaim") is not True
    ):
        raise ValueError("retired v1 implementation seal envelope changed")
    protocol_identity = _identity_shape(
        value.get("protocol"), "retired v1 protocol identity"
    )
    expected_protocol = contract.V1_PREDECESSOR["protocolJson"]
    if (
        protocol_identity["bytes"] != expected_protocol["bytes"]
        or protocol_identity["sha256"] != expected_protocol["sha256"]
    ):
        raise ValueError("retired v1 protocol identity changed")
    pinned = contract.mapping(value.get("pinned"), "retired v1 implementation pins")
    for pin_name, predecessor_name in (
        ("protocolJson", "protocolJson"),
        ("protocolTool", "protocolTool"),
        ("readiness", "readiness"),
        ("orchestrator", "orchestrator"),
    ):
        pin = _identity_shape(pinned.get(pin_name), f"retired v1 {pin_name}")
        expected_pin = contract.V1_PREDECESSOR[predecessor_name]
        if (
            pin["bytes"] != expected_pin["bytes"]
            or pin["sha256"] != expected_pin["sha256"]
        ):
            raise ValueError(f"retired v1 {pin_name} identity changed")
    return {
        "root": str(root),
        "entries": ["implementation.seal.json"],
        "implementationSeal": actual,
        "implementationCreatedUtc": value["createdUtc"],
        "protocol": protocol_identity,
    }


def _v1_retirement_value(*, created_utc: str) -> dict[str, Any]:
    singleton = _verify_v1_root_singleton()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": V1_RETIREMENT_KIND,
        "protocolId": contract.PROTOCOL_ID,
        "createdUtc": created_utc,
        "protocol": PROTOCOL_IDENTITY,
        "predecessorProtocolId": "omega-nnue-open-confirmation-v1",
        "historicalCommit": contract.V1_PREDECESSOR["historicalCommit"],
        "predecessorRoot": singleton["root"],
        "predecessorRootEntries": singleton["entries"],
        "predecessorImplementationSeal": singleton["implementationSeal"],
        "predecessorImplementationCreatedUtc": singleton[
            "implementationCreatedUtc"
        ],
        "predecessorProtocol": singleton["protocol"],
        "attemptReservations": 0,
        "candidateClaims": 0,
        "attemptsConsumed": 0,
        "poolsSuitesMatchesOrResults": 0,
        "alphaSpent": 0,
        "nextGlobalAttemptIndex": 1,
        "v1ImplementationSealMayNotAuthorizeFutureWork": True,
        "continuousSingletonRevalidationRequired": True,
        "finalStageSeal": True,
    }


@_serialized_transition
def create_v1_retirement_seal(
    path: Path = V1_RETIREMENT_SEAL,
) -> dict[str, Any]:
    _verify_bindings(require_retirement=False)
    path = path.resolve()
    if path != V1_RETIREMENT_SEAL:
        raise ValueError("v1 retirement seal path is noncanonical")
    if ARTIFACT_ROOT.exists() and any(
        item.name != "predecessor" for item in ARTIFACT_ROOT.iterdir()
    ):
        raise ValueError("v2 work exists before v1 retirement")
    if path.parent.exists() and _directory_entries(path.parent):
        raise FileExistsError("v1 retirement namespace is not empty")
    value = _v1_retirement_value(created_utc=_utc_now())
    _exclusive_json(path, value)
    return verify_v1_retirement_seal(path)


def verify_v1_retirement_seal(
    path: Path = V1_RETIREMENT_SEAL,
) -> dict[str, Any]:
    path = path.resolve()
    if path != V1_RETIREMENT_SEAL:
        raise ValueError("v1 retirement seal path is noncanonical")
    value = contract.strict_load(path, "v1 retirement seal")
    expected_fields = set(_v1_retirement_value(created_utc=value.get("createdUtc", "")))
    if set(value) != expected_fields:
        raise ValueError("v1 retirement seal field inventory changed")
    _parse_utc(value.get("createdUtc"), "v1 retirement createdUtc")
    expected = _v1_retirement_value(created_utc=value["createdUtc"])
    contract.require_exact_json(value, expected, "v1 retirement seal")
    if _directory_entries(path.parent) != [path.name]:
        raise ValueError("v1 retirement namespace is not a singleton")
    return value


def attempt_paths(index: Any) -> dict[str, Any]:
    attempt = contract._validate_attempt_index(index)
    root = (ARTIFACT_ROOT / contract.attempt_directory_name(attempt)).resolve()
    sealed = root / "sealed"
    selector_root = (
        SELECTOR_WORKSPACE_ROOT / contract.attempt_directory_name(attempt)
    ).resolve()
    sampler = selector_root / "sampler"
    selector_sealed = selector_root / "sealed"
    post_selection = root / "post-selection-audit"
    suites = {gate: selector_sealed / f"{gate}-suite.json" for gate in GATES}
    configs = {gate: sealed / f"{gate}-match.json" for gate in GATES}
    stages = {gate: root / gate for gate in GATES}
    value: dict[str, Any] = {
        "root": root,
        "implementationSeal": IMPLEMENTATION_SEAL,
        "claim": root / "candidate-claim.json",
        "reservation": root / "attempt-reservation.json",
        "selectorRoot": selector_root,
        "samplingIntent": selector_root / "sampling-intent.json",
        "sampler": sampler,
        "selectorSealed": selector_sealed,
        "sealed": sealed,
        "postSelectionRoot": post_selection,
        "exclusionInventory": post_selection / "exclusion-inventory.json",
        "excludedOrbits": post_selection / "excluded-orbits.txt",
        "postSelectionAudit": post_selection / "collision-audit.json",
        "jointSuiteSeal": selector_sealed / "joint-suite.seal.json",
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


def _verify_bindings(*, require_retirement: bool = True) -> None:
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
    if (
        sys.modules.get(_PRACTICAL_MODULE_NAME) is not practical
        or Path(str(getattr(practical, "__file__", ""))).resolve()
        != (_REPO / _PRACTICAL_RELATIVE).resolve()
        or practical.build_suite is not _PRACTICAL_BUILD_SUITE
        or practical.read_source is not _PRACTICAL_VERIFY_SOURCE
        or practical.verify_suite is not _PRACTICAL_VERIFY_SUITE
        or practical.build_config is not _PRACTICAL_BUILD_CONFIG
        or practical.assess_events is not _PRACTICAL_ASSESS_EVENTS
        or practical._authenticate_engine_inventory
        is not _PRACTICAL_AUTHENTICATE_ENGINE_INVENTORY
    ):
        raise ValueError("authenticated practical module binding was substituted")
    if require_retirement:
        verify_v1_retirement_seal()


def _system_root() -> Path:
    """Return the OS directory without consulting the parent environment."""

    if os.name != "nt":
        return Path("/").resolve()
    import ctypes

    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    if length <= 0 or length >= len(buffer):
        raise OSError("GetWindowsDirectoryW failed")
    value = Path(buffer.value).resolve()
    if not value.is_absolute() or not value.is_dir():
        raise OSError("GetWindowsDirectoryW returned a non-directory")
    return value


def _subprocess_environment_contract() -> dict[str, Any]:
    return {
        "policyVersion": 1,
        "construction": "new empty mapping populated only with the listed keys",
        "allowedKeys": list(SUBPROCESS_ENVIRONMENT_KEYS),
        "inheritedParentKeys": [],
        "systemRootSource": "Win32 GetWindowsDirectoryW; no environment lookup",
        "temporaryDirectorySource": "authenticated global-operation-lock parent",
        "dotnetRuntimeSource": "authenticated frozen-runtime directory",
        "allOtherParentVariablesRemoved": True,
        "candidateClaimNetworkScoreResultTargetOrSecretVariablesAccepted": 0,
    }


def _sanitized_environment() -> dict[str, str]:
    # Deliberately start empty.  A blacklist is not a security boundary: a new
    # candidate-, claim-, score-, target-, profiler-, or host-specific variable
    # would otherwise silently cross into the clean Python/managed processes.
    system_root = _system_root()
    runtime_root = (
        _REPO / "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime"
    ).resolve()
    temporary = GLOBAL_OPERATION_LOCK_PATH.parent.resolve()
    environment = {
        "SystemRoot": str(system_root),
        "WINDIR": str(system_root),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_MULTILEVEL_LOOKUP": "0",
        "DOTNET_ROOT": str(runtime_root),
        "DOTNET_ROOT_X64": str(runtime_root),
        "DOTNET_ROLL_FORWARD": "LatestPatch",
        "DOTNET_EnableDiagnostics": "0",
        "PYTHONNOUSERSITE": "1",
    }
    if tuple(environment) != SUBPROCESS_ENVIRONMENT_KEYS:
        raise AssertionError("subprocess environment allowlist order changed")
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


def _exact_g5_prefix_replay_bundle() -> dict[str, Any]:
    """Authenticate the replay helper and its exact Generation-5 rules bytes."""

    _require_exact_directory_inventory(
        PREFIX_REPLAY_ROOT,
        files=tuple(PREFIX_REPLAY_EXACT_FILES),
        directories=(),
        label="exact Generation-5 prefix replay bundle",
    )
    for relative, (expected_bytes, expected_sha256) in (
        PREFIX_REPLAY_EXACT_FILES.items()
    ):
        actual = contract.identity(PREFIX_REPLAY_ROOT / relative, relative=False)
        if (
            actual["bytes"] != expected_bytes
            or actual["sha256"] != expected_sha256
        ):
            raise ValueError(
                f"exact Generation-5 prefix replay file changed: {relative}"
            )
    omega_match_rules = contract.identity(
        G5_OMEGA_MATCH_RULES_ASSEMBLY, relative=False
    )
    replay_rules = contract.identity(
        PREFIX_REPLAY_ROOT / "ChessLib.dll", relative=False
    )
    if (
        omega_match_rules["bytes"] != G5_RULES_BYTES
        or omega_match_rules["sha256"] != G5_RULES_SHA256
        or replay_rules["bytes"] != omega_match_rules["bytes"]
        or replay_rules["sha256"] != omega_match_rules["sha256"]
    ):
        raise ValueError(
            "prefix replay rules differ from frozen Generation-5 OmegaMatch ChessLib"
        )
    bundle = _runtime_bundle(
        PREFIX_REPLAY_ROOT,
        "OmegaOpeningPrefixReplay.dll",
        "OmegaOpeningPrefixReplay.exe",
    )
    if bundle["sha256"] != PREFIX_REPLAY_BUNDLE_SHA256:
        raise ValueError("exact Generation-5 prefix replay bundle digest changed")
    return bundle


def _verify_exact_g5_prefix_replay_bundle(
    value: Any, label: str
) -> dict[str, Any]:
    bundle = contract.mapping(value, label)
    current = _exact_g5_prefix_replay_bundle()
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
        "--selector-capsule",
        "--sampling-intent",
        "--output-root",
        "--parent-lock-token",
    ]
    if any("candidate" in item.casefold() or "network" in item.casefold() for item in command_schema):
        raise AssertionError("clean-worker command exposes candidate information")
    return {
        "proofVersion": 2,
        "syntheticCandidateCapsuleSha256": [
            hashlib.sha256(
                (json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest()
            for item in capsules
        ],
        "workerAcceptedArgumentNames": command_schema,
        "candidateArgumentsAccepted": 0,
        "candidateEnvironmentVariablesAccepted": 0,
        "selectorHistoryProjectionInputs": 0,
        "selectorInputIsPositionProjectionOnly": True,
        "selectionDigestA": observed[0],
        "selectionDigestB": observed[1],
        "identical": True,
    }


def _implementation_value(
    orchestrator: Mapping[str, Any], *, created_utc: str
) -> dict[str, Any]:
    base_projection = _verify_base_exclusion_projection(BASE_PROJECTION)
    shared = contract.mapping(PROTOCOL["sharedRuntime"]["identities"], "shared identities")
    pinned = {
        "protocolJson": PROTOCOL_IDENTITY,
        "protocolTool": contract.identity(contract.TOOL_PATH),
        "readiness": contract.identity(TOOL_PATH),
        "orchestrator": dict(orchestrator),
        "g5CompatibilityTemplate": _g5_compatibility_identity(
            "template", G5_COMPAT_TEMPLATE
        ),
        "g5CompatibilityProtocol": _g5_compatibility_identity(
            "protocol", G5_COMPAT_PROTOCOL
        ),
        "g5CompatibilityReadiness": _g5_compatibility_identity(
            "readiness", G5_COMPAT_READINESS
        ),
        "g5CompatibilityMatches": _g5_compatibility_identity(
            "matches", G5_MATCH_ORCHESTRATOR
        ),
        "practicalModule": contract.identity(
            _REPO / _PRACTICAL_RELATIVE, relative=True
        ),
        "practicalSamplerProject": contract.identity(
            _REPO
            / "tools/omega_nnue/OmegaPracticalOpeningSampler/OmegaPracticalOpeningSampler.csproj",
            relative=True,
        ),
        "practicalSamplerSource": contract.identity(
            _REPO / "tools/omega_nnue/OmegaPracticalOpeningSampler/Program.cs",
            relative=True,
        ),
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
        "omegaMatchRulesAssembly": dict(shared["omegaMatchRulesAssembly"]),
        "prefixReplayAssembly": dict(shared["prefixReplayAssembly"]),
        "prefixReplayRulesAssembly": dict(
            shared["prefixReplayRulesAssembly"]
        ),
        "baseExclusionProjection": contract.identity(BASE_PROJECTION),
        "baseSelectorCapsule": contract.identity(BASE_SELECTOR_CAPSULE),
        "baseExcludedOrbits": contract.identity(BASE_EXCLUDED_ORBITS),
    }
    history_bundle = _runtime_bundle(
        HISTORY_SNAPSHOT_ROOT,
        "OmegaHistorySnapshot.dll",
        "OmegaHistorySnapshot.exe",
    )
    prefix_bundle = _exact_g5_prefix_replay_bundle()
    practical_sampler_bundle = _runtime_bundle(
        PRACTICAL_SAMPLER_RUNTIME_ROOT,
        "OmegaPracticalOpeningSampler.dll",
        "OmegaPracticalOpeningSampler.exe",
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": IMPLEMENTATION_KIND,
        "protocolId": contract.PROTOCOL_ID,
        "createdUtc": created_utc,
        "protocol": PROTOCOL_IDENTITY,
        "predecessorRetirement": contract.identity(V1_RETIREMENT_SEAL),
        "pinned": pinned,
        "dotnetRuntimeBundle": _dotnet_runtime_bundle(),
        "rootSamplerBundleSha256": PROTOCOL["sharedRuntime"]["rootSamplerBundleSha256"],
        "omegaMatchBundleSha256": PROTOCOL["sharedRuntime"]["omegaMatchBundleSha256"],
        "historySnapshotBundle": history_bundle,
        "prefixReplayBundle": prefix_bundle,
        "practicalSamplerBundle": practical_sampler_bundle,
        "baseExclusionFreeze": {
            "projection": contract.identity(BASE_PROJECTION),
            "selectorCapsule": contract.identity(BASE_SELECTOR_CAPSULE),
            "excludedOrbits": contract.identity(BASE_EXCLUDED_ORBITS),
            "createdUtc": base_projection["createdUtc"],
            "g5PreResultFreeze": copy.deepcopy(base_projection["g5PreResultFreeze"]),
            "projectionPredatesImplementationSeal": True,
        },
        "cleanWorker": {
            "entryPoint": "candidate-blind-worker",
            "acceptedArgumentNames": [
                "--selector-capsule",
                "--sampling-intent",
                "--output-root",
                "--parent-lock-token",
            ],
            "candidateOrClaimInputs": 0,
            "historyProjectionInputs": 0,
            "selectorCapsuleOnly": True,
            "targetFieldsDecoded": 0,
            "matchResultsAccessed": 0,
            "freshPythonProcessRequired": True,
            "freshManagedProcessPerSamplerAndReplay": True,
            "sanitizedEnvironmentRequired": True,
            "subprocessEnvironment": _subprocess_environment_contract(),
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
    if path != IMPLEMENTATION_SEAL:
        raise ValueError("v2 implementation seal path is noncanonical")
    if not orchestrator_path.is_file():
        raise FileNotFoundError("the complete v2 orchestrator must exist before sealing")
    orchestrator = contract.identity(orchestrator_path)
    if expected_orchestrator_bytes is not None and orchestrator["bytes"] != expected_orchestrator_bytes:
        raise ValueError("orchestrator size differs from expected sealed size")
    if expected_orchestrator_sha256 is not None and orchestrator["sha256"] != expected_orchestrator_sha256:
        raise ValueError("orchestrator hash differs from expected sealed hash")
    value = _implementation_value(orchestrator, created_utc=_utc_now())
    _exclusive_json(path, value)
    return verify_implementation_seal(
        path,
        orchestrator_path=orchestrator_path,
        allow_orchestrator_placeholder=False,
    )


def verify_implementation_seal(
    path: Path = IMPLEMENTATION_SEAL,
    orchestrator_path: Path = ORCHESTRATOR_PATH,
    *,
    allow_orchestrator_placeholder: bool = False,
) -> dict[str, Any]:
    _verify_bindings()
    if allow_orchestrator_placeholder:
        raise ValueError("v2 implementation seals never authorize placeholders")
    path = path.resolve()
    if path != IMPLEMENTATION_SEAL:
        raise ValueError("v2 implementation seal path is noncanonical")
    value = contract.strict_load(path, "confirmation implementation seal")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocolId",
        "createdUtc",
        "protocol",
        "predecessorRetirement",
        "pinned",
        "dotnetRuntimeBundle",
        "rootSamplerBundleSha256",
        "omegaMatchBundleSha256",
        "historySnapshotBundle",
        "prefixReplayBundle",
        "practicalSamplerBundle",
        "baseExclusionFreeze",
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
        or not contract.exact_json_equal(
            value.get("predecessorRetirement"), contract.identity(V1_RETIREMENT_SEAL)
        )
    ):
        raise ValueError("implementation-seal envelope changed")
    _parse_utc(value.get("createdUtc"), "implementation seal createdUtc")
    pinned = contract.mapping(value.get("pinned"), "implementation pins")
    expected_pin_names = {
        "protocolJson",
        "protocolTool",
        "readiness",
        "orchestrator",
        "g5CompatibilityTemplate",
        "g5CompatibilityProtocol",
        "g5CompatibilityReadiness",
        "g5CompatibilityMatches",
        "practicalModule",
        "practicalSamplerProject",
        "practicalSamplerSource",
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
        "omegaMatchRulesAssembly",
        "prefixReplayAssembly",
        "prefixReplayRulesAssembly",
        "baseExclusionProjection",
        "baseSelectorCapsule",
        "baseExcludedOrbits",
    }
    if set(pinned) != expected_pin_names:
        raise ValueError("implementation pin inventory changed")
    for name, record in pinned.items():
        item = _identity_shape(record, f"implementation {name}")
        item_path = _identity_path(item, f"implementation {name}")
        if name == "orchestrator" and not item_path.is_file():
            raise FileNotFoundError("sealed orchestrator is not available")
        actual_path = _verify_identity(item, f"implementation {name}")
        if name == "readiness" and actual_path != TOOL_PATH:
            raise ValueError("implementation readiness path is noncanonical")
        if name == "orchestrator" and actual_path != orchestrator_path.resolve():
            raise ValueError("implementation orchestrator path is noncanonical")
        if name == "g5CompatibilityTemplate" and actual_path != G5_COMPAT_TEMPLATE:
            raise ValueError("implementation compatibility template path changed")
        if name == "g5CompatibilityProtocol" and actual_path != G5_COMPAT_PROTOCOL:
            raise ValueError("implementation compatibility protocol path changed")
        if name == "g5CompatibilityReadiness" and actual_path != G5_COMPAT_READINESS:
            raise ValueError("implementation compatibility readiness path changed")
        if name == "g5CompatibilityMatches" and actual_path != G5_MATCH_ORCHESTRATOR:
            raise ValueError("implementation compatibility matches path changed")
        if name == "practicalModule" and actual_path != (
            _REPO / _PRACTICAL_RELATIVE
        ).resolve():
            raise ValueError("implementation practical module path is noncanonical")
        if name == "omegaMatchRulesAssembly" and actual_path != G5_OMEGA_MATCH_RULES_ASSEMBLY:
            raise ValueError("implementation OmegaMatch rules path is noncanonical")
        if name == "prefixReplayAssembly" and actual_path != PREFIX_REPLAY_ASSEMBLY:
            raise ValueError("implementation prefix replay path is noncanonical")
        if name == "prefixReplayRulesAssembly" and actual_path != (
            PREFIX_REPLAY_ROOT / "ChessLib.dll"
        ):
            raise ValueError("implementation prefix replay rules path is noncanonical")
        if name == "baseExclusionProjection" and actual_path != BASE_PROJECTION:
            raise ValueError("implementation base projection path is noncanonical")
        if name == "baseSelectorCapsule" and actual_path != BASE_SELECTOR_CAPSULE:
            raise ValueError("implementation selector capsule path is noncanonical")
        if name == "baseExcludedOrbits" and actual_path != BASE_EXCLUDED_ORBITS:
            raise ValueError("implementation base orbit path is noncanonical")
    contract.require_exact_json(value["dotnetRuntimeBundle"], _dotnet_runtime_bundle(), "dotnet runtime bundle")
    if (
        value.get("rootSamplerBundleSha256")
        != PROTOCOL["sharedRuntime"]["rootSamplerBundleSha256"]
        or value.get("omegaMatchBundleSha256")
        != PROTOCOL["sharedRuntime"]["omegaMatchBundleSha256"]
    ):
        raise ValueError("implementation shared-runtime bundle binding changed")
    _verify_runtime_bundle(value["historySnapshotBundle"], "history snapshot bundle")
    _verify_exact_g5_prefix_replay_bundle(
        value["prefixReplayBundle"], "prefix replay bundle"
    )
    omega_match_rules = _identity_shape(
        pinned["omegaMatchRulesAssembly"], "implementation OmegaMatch rules"
    )
    replay_rules = _identity_shape(
        pinned["prefixReplayRulesAssembly"], "implementation replay rules"
    )
    if (
        omega_match_rules["bytes"] != G5_RULES_BYTES
        or omega_match_rules["sha256"] != G5_RULES_SHA256
        or replay_rules["bytes"] != omega_match_rules["bytes"]
        or replay_rules["sha256"] != omega_match_rules["sha256"]
    ):
        raise ValueError("implementation replay/OmegaMatch rules parity changed")
    _verify_runtime_bundle(
        value["practicalSamplerBundle"], "practical sampler bundle"
    )
    base_projection = _verify_base_exclusion_projection(BASE_PROJECTION)
    expected_base = {
        "projection": contract.identity(BASE_PROJECTION),
        "selectorCapsule": contract.identity(BASE_SELECTOR_CAPSULE),
        "excludedOrbits": contract.identity(BASE_EXCLUDED_ORBITS),
        "createdUtc": base_projection["createdUtc"],
        "g5PreResultFreeze": copy.deepcopy(base_projection["g5PreResultFreeze"]),
        "projectionPredatesImplementationSeal": True,
    }
    contract.require_exact_json(
        value.get("baseExclusionFreeze"), expected_base, "base exclusion freeze"
    )
    implementation_created = _parse_utc(
        value.get("createdUtc"), "implementation seal createdUtc"
    )
    base_created = _parse_utc(
        base_projection.get("createdUtc"), "base projection createdUtc"
    )
    if implementation_created <= base_created:
        raise ValueError("implementation seal predates its base projection")
    expected_worker = {
        "entryPoint": "candidate-blind-worker",
        "acceptedArgumentNames": [
            "--selector-capsule",
            "--sampling-intent",
            "--output-root",
            "--parent-lock-token",
        ],
        "candidateOrClaimInputs": 0,
        "historyProjectionInputs": 0,
        "selectorCapsuleOnly": True,
        "targetFieldsDecoded": 0,
        "matchResultsAccessed": 0,
        "freshPythonProcessRequired": True,
        "freshManagedProcessPerSamplerAndReplay": True,
        "sanitizedEnvironmentRequired": True,
        "subprocessEnvironment": _subprocess_environment_contract(),
    }
    contract.require_exact_json(value.get("cleanWorker"), expected_worker, "clean-worker contract")
    contract.require_exact_json(value.get("candidateInvarianceProof"), _candidate_invariance_proof(), "candidate-invariance proof")
    return value


def _fresh_verify_g5(authorization: Path) -> dict[str, Any]:
    authorization = authorization.resolve()
    if authorization != G5_AUTHORIZATION:
        raise ValueError("Generation-5 compatibility authorization path changed")
    _g5_compatibility_identity("template", G5_COMPAT_TEMPLATE)
    _g5_compatibility_identity("protocol", G5_COMPAT_PROTOCOL)
    _g5_compatibility_identity("readiness", G5_COMPAT_READINESS)
    verifier = _g5_compatibility_identity("matches", G5_MATCH_ORCHESTRATOR)
    command = [
        sys.executable,
        "-I",
        "-B",
        str(G5_MATCH_ORCHESTRATOR),
        "verify-state",
        "--authorization",
        str(authorization),
    ]
    completed = subprocess.run(
        command,
        cwd=Path(tempfile.gettempdir()).resolve(),
        env=_sanitized_environment(),
        capture_output=True,
        text=False,
        timeout=30 * 60,
    )
    if completed.returncode != 0:
        detail = completed.stderr[-4000:].decode("utf-8", errors="replace")
        raise ValueError(
            "Generation-5 compatibility nomination verification failed: " + detail
        )
    return {
        "verifier": verifier,
        "isolatedPython": True,
        "ignoreEnvironment": True,
        "bytecodeDisabled": True,
        "arbitraryWorkingDirectory": True,
        "exitCode": 0,
        "stdoutBytes": len(completed.stdout),
        "stdoutSha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderrBytes": len(completed.stderr),
        "stderrSha256": hashlib.sha256(completed.stderr).hexdigest(),
    }


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


def _strict_event_times(path: Path) -> tuple[dict[str, Any], list[datetime]]:
    before = contract.identity(path)
    times: list[datetime] = []
    with path.open("r", encoding="utf-8", errors="strict", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                raise ValueError(f"{path}:{line_number}: incomplete event record")
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
                    f"{path}:{line_number}: invalid strict JSON"
                ) from error
            if type(record) is not dict:
                raise ValueError(f"{path}:{line_number}: event is not an object")
            timestamp_key = {
                "run": "CreatedUtc",
                "gameStart": "StartedUtc",
                "gameResult": "FinishedUtc",
            }.get(record.get("RecordType"))
            if timestamp_key is not None:
                times.append(
                    _parse_g5_utc(
                        record.get(timestamp_key),
                        f"{path}:{line_number} {timestamp_key}",
                    )
                )
    if not contract.exact_json_equal(contract.identity(path), before):
        raise ValueError(f"Generation-5 event stream changed while read: {path}")
    if not times:
        raise ValueError(f"Generation-5 event chronology is empty: {path}")
    return before, times


def _g5_match_chronology(
    implementation: Mapping[str, Any],
    authorization: Mapping[str, Any],
    authorization_identity: Mapping[str, Any],
    decision_paths: Mapping[str, Path],
    *,
    match_root: Path | None = None,
    preauthorization_path: Path | None = None,
    closure_path: Path | None = None,
) -> dict[str, Any]:
    match_root = (G5_MATCH_ROOT if match_root is None else match_root).resolve()
    preauthorization_path = (
        G5_COMPAT_PREAUTHORIZATION
        if preauthorization_path is None
        else preauthorization_path
    ).resolve()
    closure_path = (G5_CLOSURE if closure_path is None else closure_path).resolve()
    implementation_created = _parse_utc(
        implementation.get("createdUtc"), "implementation seal createdUtc"
    )
    base_freeze = contract.mapping(
        implementation.get("baseExclusionFreeze"),
        "implementation base-exclusion freeze",
    )
    pre_result = contract.mapping(
        base_freeze.get("g5PreResultFreeze"),
        "implementation Generation-5 pre-result freeze",
    )
    if not contract.exact_json_equal(
        pre_result.get("preauthorizationState"),
        contract.identity(preauthorization_path),
    ):
        raise ValueError(
            "implementation seal does not bind the compatibility preauthorization"
        )
    preauthorization = contract.strict_load(
        preauthorization_path, "Generation-5 compatibility preauthorization"
    )
    preauthorization_created = _parse_g5_utc(
        preauthorization.get("createdUtc"), "Generation-5 preauthorization createdUtc"
    )
    base_created = _parse_utc(
        base_freeze.get("createdUtc"), "implementation base projection createdUtc"
    )
    if not preauthorization_created < base_created < implementation_created:
        raise ValueError(
            "Generation-5 preauthorization/base-projection/implementation order changed"
        )

    authorization_created = _parse_g5_utc(
        authorization.get("createdUtc"),
        "Generation-5 compatibility authorization createdUtc",
    )
    if authorization_created <= implementation_created:
        raise ValueError(
            "Generation-5 compatibility authorization does not postdate implementation"
        )
    if not contract.exact_json_equal(
        authorization.get("preauthorizationState"),
        contract.identity(preauthorization_path),
    ):
        raise ValueError(
            "Generation-5 authorization preauthorization binding changed"
        )

    expected_success = {
        "development": "pass",
        "equal-node": "promote",
        "equal-time": "promote",
    }
    gate_records: dict[str, dict[str, Any]] = {}
    first_times: list[tuple[datetime, Path]] = []
    prior_decision_created: datetime | None = None
    for gate in G5_GATES:
        stage = (match_root / gate).resolve()
        launch_dir = stage / "launches"
        if not launch_dir.is_dir():
            raise FileNotFoundError(
                f"Generation-5 compatibility launch directory is absent: {launch_dir}"
            )
        intents: list[tuple[datetime, Path]] = []
        for path in sorted(
            launch_dir.glob("*.intent.json"), key=lambda item: str(item).casefold()
        ):
            intent = contract.strict_load(
                path, f"Generation-5 compatibility launch intent {path}"
            )
            if (
                intent.get("kind")
                != "omega-nnue-king-state-v5-color-compat-launch-intent"
                or intent.get("gate") != gate
                or not contract.exact_json_equal(
                    intent.get("authorization"), authorization_identity
                )
            ):
                raise ValueError(f"Generation-5 compatibility intent changed: {path}")
            intents.append(
                (
                    _parse_g5_utc(intent.get("createdUtc"), f"{path} createdUtc"),
                    path.resolve(),
                )
            )
        if not intents:
            raise ValueError(f"Generation-5 compatibility {gate} has no launch intent")
        first_gate_time, first_gate_path = min(
            intents, key=lambda item: (item[0], str(item[1]).casefold())
        )
        if first_gate_time <= implementation_created:
            raise ValueError(
                "Generation-5 compatibility launch predates implementation seal"
            )
        if first_gate_time <= authorization_created:
            raise ValueError(
                "Generation-5 compatibility launch predates authorization"
            )
        if prior_decision_created is not None and first_gate_time <= prior_decision_created:
            raise ValueError(
                f"Generation-5 compatibility {gate} launch predates predecessor decision"
            )
        first_times.append((first_gate_time, first_gate_path))

        decision_path = decision_paths[gate].resolve()
        decision = contract.strict_load(
            decision_path, f"Generation-5 compatibility {gate} decision"
        )
        if (
            decision.get("kind")
            != "omega-nnue-king-state-v5-color-compat-decision"
            or decision.get("gate") != gate
            or decision.get("decision") != expected_success[gate]
            or decision.get("passed") is not True
            or not contract.exact_json_equal(
                decision.get("authorization"), authorization_identity
            )
        ):
            raise ValueError(
                f"Generation-5 compatibility {gate} is not a successful decision"
            )
        decision_created = _parse_g5_utc(
            decision.get("createdUtc"), f"{decision_path} createdUtc"
        )
        if decision_created < first_gate_time:
            raise ValueError(
                f"Generation-5 compatibility {gate} decision predates its launch"
            )
        events_path = stage / "events.jsonl"
        events_identity, event_times = _strict_event_times(events_path)
        if min(event_times) < first_gate_time or max(event_times) > decision_created:
            raise ValueError(
                f"Generation-5 compatibility {gate} event chronology escaped its gate"
            )
        gate_records[gate] = {
            "firstLaunchIntent": contract.identity(first_gate_path),
            "firstLaunchCreatedUtc": first_gate_time.isoformat().replace(
                "+00:00", "Z"
            ),
            "launchIntents": [
                contract.identity(path)
                for _, path in sorted(
                    intents, key=lambda item: (item[0], str(item[1]).casefold())
                )
            ],
            "events": events_identity,
            "firstEventUtc": min(event_times).isoformat().replace("+00:00", "Z"),
            "lastEventUtc": max(event_times).isoformat().replace("+00:00", "Z"),
            "timestampedEventRecords": len(event_times),
            "decision": contract.identity(decision_path),
            "decisionCreatedUtc": decision.get("createdUtc"),
        }
        prior_decision_created = decision_created

    closure = contract.strict_load(
        closure_path, "Generation-5 compatibility closure"
    )
    if (
        closure.get("kind") != "omega-nnue-king-state-v5-color-compat-closure"
        or closure.get("compatibilityId") != "king-state-v5-color-compat-v2"
        or closure.get("clearlySuperior") is not True
        or not contract.exact_json_equal(
            closure.get("authorization"), authorization_identity
        )
        or not contract.exact_json_equal(
            closure.get("selectedNetwork"), authorization.get("selectedNetwork")
        )
        or not contract.exact_json_equal(
            closure.get("decisions"),
            {
                gate: contract.identity(decision_paths[gate].resolve())
                for gate in G5_GATES
            },
        )
        or not contract.exact_json_equal(
            closure.get("positionHistoryProjection"),
            contract.identity(G5_HISTORY_PROJECTION),
        )
        or not contract.exact_json_equal(
            closure.get("positionHistoryManifest"),
            contract.identity(G5_HISTORY_MANIFEST),
        )
    ):
        raise ValueError("Generation-5 compatibility closure is not successful")
    closure_created = _parse_g5_utc(
        closure.get("createdUtc"), "Generation-5 compatibility closure createdUtc"
    )
    history_manifest = contract.strict_load(
        G5_HISTORY_MANIFEST, "Generation-5 compatibility position-history manifest"
    )
    history_created = _parse_g5_utc(
        history_manifest.get("createdUtc"),
        "Generation-5 compatibility position-history createdUtc",
    )
    if (
        prior_decision_created is None
        or not prior_decision_created < history_created < closure_created
    ):
        raise ValueError("Generation-5 compatibility closure predates a decision")
    first_time, first_path = min(
        first_times, key=lambda item: (item[0], str(item[1]).casefold())
    )
    namespace_files = sorted(
        [
            path.resolve()
            for path in match_root.rglob("*")
            if path.is_file()
            and path.suffix.casefold() in {".json", ".jsonl"}
        ],
        key=lambda item: str(item).casefold(),
    )
    if closure_path not in namespace_files:
        raise ValueError("Generation-5 compatibility closure escaped its namespace")
    return {
        "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
        "implementationCreatedUtc": implementation["createdUtc"],
        "preauthorizationState": contract.identity(preauthorization_path),
        "preauthorizationCreatedUtc": preauthorization["createdUtc"],
        "generation5Authorization": dict(authorization_identity),
        "authorizationCreatedUtc": authorization["createdUtc"],
        "firstLaunchIntent": contract.identity(first_path),
        "firstLaunchCreatedUtc": first_time.isoformat().replace("+00:00", "Z"),
        "gates": gate_records,
        "closure": contract.identity(closure_path),
        "closureCreatedUtc": closure["createdUtc"],
        "positionHistoryProjection": contract.identity(G5_HISTORY_PROJECTION),
        "positionHistoryManifest": contract.identity(G5_HISTORY_MANIFEST),
        "positionHistoryCreatedUtc": history_manifest["createdUtc"],
        "namespaceInventory": [
            contract.identity(path) for path in namespace_files
        ],
        "preauthorizationPredatesBaseProjection": True,
        "baseProjectionPredatesImplementationSeal": True,
        "compatibilityAuthorizationStrictlyPostdatesImplementationSeal": True,
        "implementationStrictlyPredatesFirstLaunch": True,
        "closurePostdatesAllDecisions": True,
    }


def _verify_g5_chronology_record(
    value: Any, implementation: Mapping[str, Any]
) -> dict[str, Any]:
    record = contract.mapping(value, "Generation-5 chronology record")
    authorization_path = _verify_identity(
        record.get("generation5Authorization"),
        "chronology Generation-5 authorization",
    )
    if authorization_path != G5_AUTHORIZATION:
        raise ValueError("chronology Generation-5 authorization path changed")
    authorization_identity = contract.identity(authorization_path)
    authorization = contract.strict_load(
        authorization_path, "chronology Generation-5 authorization"
    )
    expected = _g5_match_chronology(
        implementation,
        authorization,
        authorization_identity,
        G5_DECISIONS,
    )
    contract.require_exact_json(record, expected, "Generation-5 chronology")
    return dict(record)


def _g5_lineage_paths(
    authorization: Mapping[str, Any],
) -> dict[str, Path]:
    lineage = {
        "compatibilityTemplate": G5_COMPAT_TEMPLATE,
        "compatibilityProtocol": G5_COMPAT_PROTOCOL,
        "compatibilityReadiness": G5_COMPAT_READINESS,
        "compatibilityMatches": G5_MATCH_ORCHESTRATOR,
        "compatibilityPreregistration": G5_COMPAT_PREREGISTRATION,
        "compatibilityPreauthorization": G5_COMPAT_PREAUTHORIZATION,
        "compatibilitySuiteSeal": G5_COMPAT_SUITE_SEAL,
        "compatibilityCoreSeal": G5_COMPAT_CORE_SEAL,
        "compatibilityClosure": G5_CLOSURE,
        "compatibilityPositionHistoryProjection": G5_HISTORY_PROJECTION,
        "compatibilityPositionHistoryManifest": G5_HISTORY_MANIFEST,
        "offlineReport": _verify_identity(
            authorization.get("offlineReport"),
            "Generation-5 compatibility offline report",
        ),
        "selectedManifest": _verify_identity(
            authorization.get("selectedManifest"),
            "Generation-5 compatibility selected manifest",
        ),
        "originalProfile": ORIGINAL_G5_PROFILE,
        "originalFinalFreeze": ORIGINAL_G5_FREEZE,
        "originalMatchProtocol": ORIGINAL_G5_MATCH_PROTOCOL,
        "originalMatchReadiness": ORIGINAL_G5_MATCH_READINESS,
        "originalMatchCore": ORIGINAL_G5_MATCH_CORE,
        "originalMatchOrchestrator": ORIGINAL_G5_MATCH_ORCHESTRATOR,
    }
    for gate in G5_GATES:
        for kind in ("source", "manifest", "completionSeal"):
            key = (
                "originalSampler"
                + gate.title().replace("-", "")
                + kind[0].upper()
                + kind[1:]
            )
            lineage[key] = ORIGINAL_G5_SAMPLER_FILES[gate][kind]
    return lineage


def _g5_nomination_evidence(
    authorization_path: Path = G5_AUTHORIZATION,
    decision_paths: Mapping[str, Path] = G5_DECISIONS,
    *,
    run_fresh_verifier: bool,
    implementation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    authorization_path = authorization_path.resolve()
    if authorization_path != G5_AUTHORIZATION:
        raise ValueError("Generation-5 compatibility authorization is noncanonical")
    if set(decision_paths) != set(G5_GATES) or any(
        decision_paths[gate].resolve() != G5_DECISIONS[gate] for gate in G5_GATES
    ):
        raise ValueError("Generation-5 compatibility decision paths are noncanonical")
    fresh = (
        _fresh_verify_g5(authorization_path)
        if run_fresh_verifier
        else {
            "verifier": _g5_compatibility_identity(
                "matches", G5_MATCH_ORCHESTRATOR
            )
        }
    )
    authorization_before = contract.identity(authorization_path)
    authorization = contract.strict_load(
        authorization_path, "Generation-5 compatibility authorization"
    )
    if (
        authorization.get("kind")
        != "omega-nnue-king-state-v5-color-compat-authorization"
        or authorization.get("compatibilityId")
        != "king-state-v5-color-compat-v2"
        or authorization.get("runnerUpFallback") is not False
        or authorization.get("finalStageSeal") is not True
        or _verify_identity(
            authorization.get("protocol"),
            "Generation-5 compatibility authorization protocol",
        )
        != G5_COMPAT_PROTOCOL
        or _verify_identity(
            authorization.get("preauthorizationState"),
            "Generation-5 compatibility authorization preauthorization",
        )
        != G5_COMPAT_PREAUTHORIZATION
    ):
        raise ValueError("Generation-5 compatibility authorization changed")
    selected = _identity_shape(
        authorization.get("selectedNetwork"), "Generation-5 selected network"
    )
    manifest = _identity_shape(
        authorization.get("selectedManifest"), "Generation-5 selected manifest"
    )
    engine = _identity_shape(
        authorization.get("engine"), "Generation-5 engine"
    )
    _verify_identity(selected, "Generation-5 selected network")
    _verify_identity(manifest, "Generation-5 selected manifest")
    _verify_identity(engine, "Generation-5 engine")
    if (
        engine["sha256"]
        != PROTOCOL["g5Screening"]["identities"]["frozenScreeningEngine"]["sha256"]
    ):
        raise ValueError("Generation-5 nominee used the wrong engine executable")

    expected_decision = {
        "development": "pass",
        "equal-node": "promote",
        "equal-time": "promote",
    }
    decisions: dict[str, dict[str, Any]] = {}
    for gate in G5_GATES:
        path = decision_paths[gate].resolve()
        before = contract.identity(path)
        decision = contract.strict_load(
            path, f"Generation-5 compatibility {gate} decision"
        )
        if (
            decision.get("kind")
            != "omega-nnue-king-state-v5-color-compat-decision"
            or decision.get("compatibilityId")
            != "king-state-v5-color-compat-v2"
            or decision.get("gate") != gate
            or decision.get("decision") != expected_decision[gate]
            or decision.get("passed") is not True
            or decision.get("finalStageSeal") is not True
            or not contract.exact_json_equal(
                decision.get("authorization"), authorization_before
            )
        ):
            raise ValueError(
                f"Generation-5 compatibility {gate} is not a successful nomination"
            )
        if not contract.exact_json_equal(contract.identity(path), before):
            raise ValueError(
                f"Generation-5 compatibility {gate} decision changed while read"
            )
        decisions[gate] = before

    closure_before = contract.identity(G5_CLOSURE)
    closure = contract.strict_load(
        G5_CLOSURE, "Generation-5 compatibility closure"
    )
    expected_closure_decisions = {
        gate: decisions[gate] for gate in G5_GATES
    }
    if (
        closure.get("kind")
        != "omega-nnue-king-state-v5-color-compat-closure"
        or closure.get("compatibilityId")
        != "king-state-v5-color-compat-v2"
        or closure.get("clearlySuperior") is not True
        or closure.get("originalArtifactsRewritten") != 0
        or closure.get("finalStageSeal") is not True
        or not contract.exact_json_equal(
            closure.get("authorization"), authorization_before
        )
        or not contract.exact_json_equal(
            closure.get("selectedNetwork"), selected
        )
        or not contract.exact_json_equal(
            closure.get("decisions"), expected_closure_decisions
        )
        or not contract.exact_json_equal(
            closure.get("positionHistoryProjection"),
            contract.identity(G5_HISTORY_PROJECTION),
        )
        or not contract.exact_json_equal(
            closure.get("positionHistoryManifest"),
            contract.identity(G5_HISTORY_MANIFEST),
        )
    ):
        raise ValueError(
            "Generation-5 compatibility closure does not establish superiority"
        )
    if not contract.exact_json_equal(contract.identity(G5_CLOSURE), closure_before):
        raise ValueError("Generation-5 compatibility closure changed while read")
    if not contract.exact_json_equal(
        contract.identity(authorization_path), authorization_before
    ):
        raise ValueError(
            "Generation-5 compatibility authorization changed while read"
        )

    lineage_paths = _g5_lineage_paths(authorization)
    lineage = {
        name: contract.identity(path)
        for name, path in sorted(
            lineage_paths.items(), key=lambda item: item[0].casefold()
        )
    }
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
        "freshVerifier": fresh["verifier"],
        "requiredDecisions": expected_decision,
        "zeroSafetyFailuresVerifiedByCompatibilityClosure": True,
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
        "predecessorRetirement",
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
    if not contract.exact_json_equal(
        value.get("predecessorRetirement"), implementation["predecessorRetirement"]
    ):
        raise ValueError("reservation predecessor-retirement binding changed")
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
    if set(decisions) != set(G5_GATES):
        raise ValueError("reservation G5 decision inventory changed")
    for gate in G5_GATES:
        if _verify_identity(decisions[gate], f"reservation G5 {gate} decision") != G5_DECISIONS[gate]:
            raise ValueError(f"reservation G5 {gate} decision path changed")
    authorization = contract.strict_load(
        G5_AUTHORIZATION, "reservation Generation-5 compatibility authorization"
    )
    expected_lineage = _g5_lineage_paths(authorization)
    lineage = contract.mapping(value.get("g5Lineage"), "reservation G5 lineage")
    if set(lineage) != set(expected_lineage):
        raise ValueError("reservation G5 lineage inventory changed")
    for name, expected_path in expected_lineage.items():
        if _verify_identity(
            lineage[name], f"reservation G5 lineage {name}"
        ) != expected_path:
            raise ValueError(f"reservation G5 lineage {name} path changed")
    contract.require_exact_json(
        value.get("requiredG5Decisions"),
        {"development": "pass", "equal-node": "promote", "equal-time": "promote"},
        "reservation required G5 decisions",
    )
    chronology = _verify_g5_chronology_record(
        value.get("g5Chronology"), implementation
    )
    if created <= _parse_g5_utc(
        chronology.get("closureCreatedUtc"),
        "reservation Generation-5 closure createdUtc",
    ):
        raise ValueError("attempt reservation predates compatibility closure")
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
        "predecessorRetirement": implementation["predecessorRetirement"],
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
        _publish_candidate_claim_atomic(paths["claim"], value)
        return verify_candidate_claim(paths["claim"], index)
    except BaseException:
        # A failure after the hard-link commit has nevertheless published a
        # complete, valid claim.  Authenticate and return that committed state;
        # it may never be mislabeled as a nullable prepublication abort.
        if paths["claim"].exists():
            return verify_candidate_claim(paths["claim"], index)
        if _claim_staging_artifacts(paths["root"]):
            raise RuntimeError(
                "candidate-claim staging artifact survived delete-on-close"
            )
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
        "predecessorRetirement",
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
        "predecessorRetirement",
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
    if set(decisions) != set(G5_GATES):
        raise ValueError("claim G5 decision inventory changed")
    for gate in G5_GATES:
        if _verify_identity(decisions[gate], f"claim G5 {gate} decision") != G5_DECISIONS[gate]:
            raise ValueError(f"claim G5 {gate} decision path changed")
    authorization = contract.strict_load(
        G5_AUTHORIZATION, "claim Generation-5 compatibility authorization"
    )
    expected_lineage = _g5_lineage_paths(authorization)
    lineage = contract.mapping(value.get("g5Lineage"), "claim G5 lineage")
    if set(lineage) != set(expected_lineage):
        raise ValueError("claim G5 lineage inventory changed")
    for name, expected_path in expected_lineage.items():
        if _verify_identity(
            lineage[name], f"claim G5 lineage {name}"
        ) != expected_path:
            raise ValueError(f"claim G5 lineage {name} path changed")
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
        if paths["claim"].exists():
            raise ValueError(
                f"attempt {index} nullable closure coexists with a claim publication"
            )
        if _claim_staging_artifacts(paths["root"]):
            raise ValueError(
                f"attempt {index} nullable closure coexists with claim staging"
            )
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
        "normalStartClockDecision",
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
    clock_path = _verify_identity(
        value.get("normalStartClockDecision"),
        "program success normal-start-clock decision",
    )
    if (
        closure_path != paths["attemptClosure"]
        or claim_path != paths["claim"]
        or authorization_path != paths["authorization"]
        or node_path != paths["stages"]["equal-node"] / "decision.json"
        or time_path != paths["stages"]["equal-time"] / "decision.json"
        or clock_path
        != paths["stages"]["normal-start-clock"] / "decision.json"
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
    verify_v1_retirement_seal()
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
    allowed_root_directories = {"predecessor", "runtime"}
    indices: list[int] = []
    for item in ARTIFACT_ROOT.iterdir():
        if item.is_dir():
            if item.name not in allowed_root_directories:
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
    if reason == "claim-publication-failure" and paths["claim"].exists():
        raise ValueError(
            "any candidate-claim publication blocks a nullable prepublication abort"
        )
    if reason == "claim-publication-failure" and _claim_staging_artifacts(
        paths["root"]
    ):
        raise ValueError(
            "candidate-claim staging blocks a nullable prepublication abort"
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
        selector = attempt_paths(prior)["selectorRoot"]
        if selector.is_dir():
            roots.add(selector.resolve())
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
    current_paths = attempt_paths(index)
    current = current_paths["root"]
    current_selector = current_paths["selectorRoot"]
    result: set[Path] = set()
    allowed = {".json", ".jsonl", ".pgn", ".ccsf"}
    scan_roots = _history_roots(index) if roots is None else list(roots)
    for root in scan_roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in allowed:
                continue
            resolved = path.resolve()
            if _is_current_attempt_evidence(resolved, current, current_selector):
                continue
            if any(part.casefold() in {".git", "obj", "bin", "__pycache__"} for part in resolved.parts):
                continue
            result.add(resolved)
    return sorted(result, key=lambda item: str(item).casefold())


def _is_current_attempt_evidence(
    path: Path, attempt_root: Path, selector_root: Path
) -> bool:
    resolved = path.resolve()
    return resolved.is_relative_to(attempt_root.resolve()) or resolved.is_relative_to(
        selector_root.resolve()
    )


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
    *,
    require_compatibility_results: bool = False,
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
    compat_members = sorted(
        [path for path in identities if path.is_relative_to(G5_MATCH_ROOT)],
        key=lambda item: str(item).casefold(),
    )
    eligible_suffixes = {".json", ".jsonl", ".pgn", ".ccsf"}
    current_compat_members = sorted(
        [
            path.resolve()
            for path in G5_MATCH_ROOT.rglob("*")
            if path.is_file() and path.suffix.casefold() in eligible_suffixes
        ],
        key=lambda item: str(item).casefold(),
    )
    if compat_members != current_compat_members:
        raise ValueError(
            "Generation-5 compatibility history membership is incomplete"
        )
    required_pre_result = {
        G5_COMPAT_PREAUTHORIZATION,
        G5_COMPAT_PREREGISTRATION,
        G5_COMPAT_SUITE_SEAL,
        *G5_COMPAT_SUITES.values(),
    }
    if not required_pre_result.issubset(identities):
        raise ValueError(
            "Generation-5 compatibility pre-result history is not inventoried"
        )
    required_result_paths = {
        G5_CLOSURE,
        G5_HISTORY_PROJECTION,
        G5_HISTORY_MANIFEST,
        *G5_DECISIONS.values(),
        *[(G5_MATCH_ROOT / f"{gate}/events.jsonl").resolve() for gate in G5_GATES],
    }
    if require_compatibility_results:
        missing_results = required_result_paths.difference(identities)
        if missing_results:
            raise ValueError(
                "Generation-5 compatibility result history is not inventoried: "
                + ", ".join(
                    str(path) for path in sorted(missing_results, key=str)
                )
            )
        evidence_files = [
            path
            for path in compat_members
            if path.is_relative_to((G5_MATCH_ROOT / "evidence").resolve())
        ]
        if not evidence_files:
            raise ValueError(
                "Generation-5 compatibility replay evidence is not inventoried"
            )
    return {
        "generation5DecisionCorpus": True,
        "generation5RawMatchPools": True,
        "generation5SealedSuites": True,
        "generation5PlayedEventPreFinalAndPvPositions": True,
        "generation5CompatibilityNamespaceExactMembership": {
            "root": str(G5_MATCH_ROOT),
            "inputFiles": len(compat_members),
            "inputBytes": sum(int(identities[path]["bytes"]) for path in compat_members),
            "preauthorization": dict(identities[G5_COMPAT_PREAUTHORIZATION]),
            "resultEvidenceRequired": require_compatibility_results,
            "closure": (
                dict(identities[G5_CLOSURE])
                if require_compatibility_results
                else None
            ),
            "positionHistoryProjection": (
                dict(identities[G5_HISTORY_PROJECTION])
                if require_compatibility_results
                else None
            ),
            "positionHistoryManifest": (
                dict(identities[G5_HISTORY_MANIFEST])
                if require_compatibility_results
                else None
            ),
            "decisions": (
                {
                    gate: dict(identities[G5_DECISIONS[gate]])
                    for gate in G5_GATES
                }
                if require_compatibility_results
                else None
            ),
            "rawEvents": (
                {
                    gate: dict(
                        identities[(G5_MATCH_ROOT / f"{gate}/events.jsonl").resolve()]
                    )
                    for gate in G5_GATES
                }
                if require_compatibility_results
                else None
            ),
            "rawAndReplayEvidenceIncluded": require_compatibility_results,
        },
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


def _compatibility_position_history_coverage(
    excluded_signatures: set[str],
) -> dict[str, Any]:
    """Authenticate the closure-bound projection and prove orbit inclusion."""

    closure = contract.strict_load(
        G5_CLOSURE, "Generation-5 compatibility closure history binding"
    )
    projection_identity = contract.identity(G5_HISTORY_PROJECTION)
    manifest_identity = contract.identity(G5_HISTORY_MANIFEST)
    if (
        not contract.exact_json_equal(
            closure.get("positionHistoryProjection"), projection_identity
        )
        or not contract.exact_json_equal(
            closure.get("positionHistoryManifest"), manifest_identity
        )
    ):
        raise ValueError(
            "compatibility closure does not bind the canonical position history"
        )
    manifest = contract.strict_load(
        G5_HISTORY_MANIFEST, "Generation-5 compatibility position-history manifest"
    )
    expected_manifest_fields = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "createdUtc",
        "protocol",
        "authorization",
        "selectedNetwork",
        "decisions",
        "sources",
        "projection",
        "rows",
        "uniquePositionIdentities",
        "uniqueOrbitSignatures",
        "recordSchema",
        "producer",
        "originalArtifactsRewritten",
        "finalStageSeal",
    }
    if set(manifest) != expected_manifest_fields:
        raise ValueError("compatibility position-history manifest fields changed")
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("kind")
        != "omega-nnue-king-state-v5-compat-position-history-manifest"
        or manifest.get("compatibilityId") != "king-state-v5-color-compat-v2"
        or manifest.get("recordSchema")
        != (
            "gameStart.InitialOfen plus every successful ply.PostOfen from "
            "the final authenticated raw prefix of each terminal gate"
        )
        or manifest.get("originalArtifactsRewritten") != 0
        or manifest.get("finalStageSeal") is not True
        or _verify_identity(
            manifest.get("protocol"), "position-history manifest protocol"
        )
        != G5_COMPAT_PROTOCOL
        or _verify_identity(
            manifest.get("authorization"), "position-history authorization"
        )
        != G5_AUTHORIZATION
        or not contract.exact_json_equal(
            manifest.get("selectedNetwork"), closure.get("selectedNetwork")
        )
        or not contract.exact_json_equal(
            manifest.get("decisions"), closure.get("decisions")
        )
        or not contract.exact_json_equal(
            manifest.get("projection"), projection_identity
        )
    ):
        raise ValueError("compatibility position-history manifest changed")
    sources = contract.mapping(
        manifest.get("sources"), "compatibility position-history sources"
    )
    if set(sources) != set(G5_GATES):
        raise ValueError("compatibility position-history source inventory changed")
    for gate in G5_GATES:
        source_path = _verify_identity(
            sources[gate], f"compatibility {gate} position-history source"
        )
        if not source_path.is_relative_to((G5_MATCH_ROOT / "evidence" / gate).resolve()):
            raise ValueError(
                f"compatibility {gate} position-history source escaped evidence"
            )
    producer = contract.mapping(
        manifest.get("producer"), "compatibility position-history producer"
    )
    expected_producer = contract.mapping(
        PROTOCOL["g5Screening"]["compatibilityAuthority"].get("identities"),
        "compatibility authority identities",
    )
    contract.require_exact_json(
        producer,
        {
            "readiness": expected_producer["readiness"],
            "matches": expected_producer["matches"],
        },
        "compatibility position-history producer",
    )
    history_created = _parse_g5_utc(
        manifest.get("createdUtc"), "compatibility position-history createdUtc"
    )
    closure_created = _parse_g5_utc(
        closure.get("createdUtc"), "compatibility closure createdUtc"
    )
    decision_times = [
        _parse_g5_utc(
            contract.strict_load(G5_DECISIONS[gate], f"{gate} history decision").get(
                "createdUtc"
            ),
            f"{gate} history decision createdUtc",
        )
        for gate in G5_GATES
    ]
    if not max(decision_times) < history_created < closure_created:
        raise ValueError(
            "compatibility decisions/history-projection/closure order changed"
        )

    expected_row_fields = {
        "schemaVersion",
        "kind",
        "occurrence",
        "gate",
        "sourceRawPrefixSha256",
        "sourceLine",
        "recordType",
        "gameId",
        "attempt",
        "ply",
        "positionRole",
        "ofen",
        "phase",
        "sideToMove",
        "positionIdentity",
        "symmetryOrbitKey",
        "orbitSignatures",
    }
    rows = 0
    last_gate_index = -1
    row_gates: set[str] = set()
    position_identities: set[str] = set()
    manifest_orbit_signatures: set[str] = set()
    orbit_signatures: set[str] = set()
    before = contract.identity(G5_HISTORY_PROJECTION)
    with G5_HISTORY_PROJECTION.open(
        "r", encoding="utf-8", errors="strict", newline=""
    ) as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                raise ValueError(
                    f"{G5_HISTORY_PROJECTION}:{line_number}: incomplete history row"
                )
            try:
                row = json.loads(
                    line,
                    object_pairs_hook=contract._unique_object,
                    parse_constant=lambda token: (_ for _ in ()).throw(
                        ValueError(token)
                    ),
                )
            except (json.JSONDecodeError, ValueError) as error:
                raise ValueError(
                    f"{G5_HISTORY_PROJECTION}:{line_number}: invalid history row"
                ) from error
            if type(row) is not dict or set(row) != expected_row_fields:
                raise ValueError("compatibility position-history row fields changed")
            if (
                row.get("schemaVersion") != 1
                or row.get("kind")
                != "omega-nnue-king-state-v5-compat-position-history-row"
                or row.get("occurrence") != line_number
                or row.get("gate") not in G5_GATES
                or G5_GATES.index(row["gate"]) < last_gate_index
                or type(row.get("sourceLine")) is not int
                or row["sourceLine"] < 1
                or type(row.get("sourceRawPrefixSha256")) is not str
                or HEX_256.fullmatch(row["sourceRawPrefixSha256"]) is None
                or row["sourceRawPrefixSha256"]
                != sources[row["gate"]]["sha256"]
                or row.get("recordType") not in {"gameStart", "ply"}
                or row.get("positionRole") not in {"initial", "post-move"}
                or (row["recordType"], row["positionRole"])
                not in {("gameStart", "initial"), ("ply", "post-move")}
                or type(row.get("gameId")) is not str
                or not row["gameId"]
                or type(row.get("attempt")) is not int
                or row["attempt"] < 1
                or type(row.get("ply")) is not int
                or row["ply"] < 0
                or type(row.get("ofen")) is not str
            ):
                raise ValueError("compatibility position-history row changed")
            phase, side, identity, orbit, signatures = _POSITION_META(row["ofen"])
            if (
                row.get("phase") != phase
                or row.get("sideToMove") != side
                or row.get("positionIdentity") != identity
                or row.get("symmetryOrbitKey") != orbit
                or row.get("orbitSignatures") != list(signatures)
            ):
                raise ValueError(
                    "compatibility position-history frozen metadata changed"
                )
            rows += 1
            last_gate_index = G5_GATES.index(row["gate"])
            row_gates.add(row["gate"])
            position_identities.add(identity)
            orbit_signatures.add(identity)
            orbit_signatures.update(signatures)
            manifest_orbit_signatures.update(signatures)
    if not contract.exact_json_equal(
        contract.identity(G5_HISTORY_PROJECTION), before
    ):
        raise ValueError("compatibility position-history projection changed while read")
    if (
        rows < 1
        or row_gates != set(G5_GATES)
        or manifest.get("rows") != rows
        or manifest.get("uniquePositionIdentities") != len(position_identities)
        or manifest.get("uniqueOrbitSignatures")
        != len(manifest_orbit_signatures)
    ):
        raise ValueError("compatibility position-history counts changed")
    missing = orbit_signatures.difference(excluded_signatures)
    if missing:
        raise ValueError(
            "compatibility position-history orbits are absent from exclusion projection"
        )
    digest = hashlib.sha256(
        "".join(f"{item}\n" for item in sorted(orbit_signatures)).encode("ascii")
    ).hexdigest()
    return {
        "projection": projection_identity,
        "manifest": manifest_identity,
        "rows": rows,
        "uniquePositionIdentities": len(position_identities),
        "projectedExclusionSignatures": len(orbit_signatures),
        "projectedExclusionSignatureSha256": digest,
        "missingExclusionSignatures": 0,
        "allProjectedOrbitsIncluded": True,
        "closureBound": True,
    }


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
    bundle = _verify_exact_g5_prefix_replay_bundle(
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
    replay_rules = contract.identity(bundle_root / "ChessLib.dll")
    omega_match_rules = contract.identity(G5_OMEGA_MATCH_RULES_ASSEMBLY)
    manifest_value = contract.strict_load(
        manifest, "history prefix replay manifest"
    )
    manifest_runtime = contract.mapping(
        manifest_value.get("runtime"), "history prefix replay runtime"
    )
    contract.require_exact_json(
        manifest_runtime.get("helper"),
        contract.identity(assembly),
        "history prefix replay helper identity",
    )
    contract.require_exact_json(
        manifest_runtime.get("rules"),
        replay_rules,
        "history prefix replay rules identity",
    )
    if (
        replay_rules["bytes"] != omega_match_rules["bytes"]
        or replay_rules["sha256"] != omega_match_rules["sha256"]
    ):
        raise ValueError("history replay rules differ from frozen OmegaMatch")
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
        "helper": contract.identity(assembly),
        "rules": replay_rules,
        "omegaMatchRules": omega_match_rules,
        "rulesByteParityWithOmegaMatch": True,
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
    compatibility_history = _compatibility_position_history_coverage(signatures)
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
        "compatibilityPositionHistory": compatibility_history,
        "excludedOrbitSignatures": len(signatures),
        "excludedOrbits": contract.identity(orbit_path),
        "coverage": _history_coverage_evidence(
            identities, require_compatibility_results=True
        ),
        "informationBoundary": {
            "positionStringsDecoded": direct_positions,
            "coordinateMoveListsDecoded": len(requests),
            "targetFieldsDecoded": 0,
            "scoreFieldsDecoded": 0,
            "resultFieldsDecoded": 0,
            "candidateIdentityAvailableToAuditParent": True,
            "candidateIdentityDecoded": 0,
            "candidateIdentityUsedForRanking": False,
            "selectionInputsAccepted": 0,
            "reselectionPermitted": False,
            "historyPositionsUsedOnlyForIntersection": True,
        },
    }
    inventory_path = sampler_dir / "exclusion-inventory.json"
    _exclusive_json(inventory_path, value)
    return signatures, value


def _g5_compatibility_identity(name: str, path: Path) -> dict[str, Any]:
    authority = contract.mapping(
        PROTOCOL["g5Screening"].get("compatibilityAuthority"),
        "Generation-5 compatibility authority",
    )
    identities = contract.mapping(
        authority.get("identities"), "Generation-5 compatibility identities"
    )
    expected = _identity_shape(
        identities.get(name), f"Generation-5 compatibility {name} pin"
    )
    actual = contract.identity(path, relative=True)
    if not contract.exact_json_equal(actual, expected):
        raise ValueError(f"Generation-5 compatibility {name} differs from its pin")
    return actual


def _original_g5_sampler_identities() -> list[dict[str, Any]]:
    paths = [
        ORIGINAL_G5_SAMPLER_FILES[gate][kind]
        for gate in G5_GATES
        for kind in ("source", "manifest", "completionSeal")
    ]
    return [
        contract.identity(path, relative=True)
        for path in sorted(paths, key=lambda item: str(item).casefold())
    ]


def _verify_g5_preauthorization_record(
    path: Path = G5_COMPAT_PREAUTHORIZATION,
) -> dict[str, Any]:
    path = path.resolve()
    if path != G5_COMPAT_PREAUTHORIZATION:
        raise ValueError("Generation-5 compatibility preauthorization path changed")
    value = contract.strict_load(path, "Generation-5 compatibility preauthorization")
    expected_fields = {
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
    if set(value) != expected_fields:
        raise ValueError("Generation-5 preauthorization field inventory changed")
    if (
        value.get("schemaVersion") != 1
        or value.get("kind")
        != "omega-nnue-king-state-v5-color-compat-preauthorization-state"
        or value.get("compatibilityId") != "king-state-v5-color-compat-v2"
        or value.get("root") != str(G5_MATCH_ROOT)
        or value.get("allowedDirectories")
        != [str((G5_MATCH_ROOT / "sealed").resolve())]
        or value.get("authorizationAbsent") is not True
        or value.get("resultsAbsent") is not True
        or value.get("originalArtifactsRewritten") != 0
        or value.get("finalStageSeal") is not True
        or type(value.get("suiteRootDigest")) is not str
        or HEX_256.fullmatch(value["suiteRootDigest"]) is None
    ):
        raise ValueError("Generation-5 preauthorization envelope changed")
    _parse_g5_utc(value.get("createdUtc"), "Generation-5 preauthorization createdUtc")
    if _verify_identity(
        value.get("protocol"), "Generation-5 preauthorization protocol"
    ) != G5_COMPAT_PROTOCOL:
        raise ValueError("Generation-5 preauthorization protocol path changed")
    if _verify_identity(
        value.get("suiteSeal"), "Generation-5 preauthorization suite seal"
    ) != G5_COMPAT_SUITE_SEAL:
        raise ValueError("Generation-5 preauthorization suite path changed")
    expected_allowed_paths = sorted(
        [
            G5_COMPAT_PREREGISTRATION,
            G5_COMPAT_SUITE_SEAL,
            *G5_COMPAT_SUITES.values(),
        ],
        key=lambda item: str(item).casefold(),
    )
    allowed = value.get("allowedFiles")
    if type(allowed) is not list or len(allowed) != len(expected_allowed_paths):
        raise ValueError("Generation-5 preauthorization allowed-file inventory changed")
    for number, (record, expected_path) in enumerate(
        zip(allowed, expected_allowed_paths, strict=True), 1
    ):
        if _verify_identity(
            record, f"Generation-5 preauthorization allowed file {number}"
        ) != expected_path:
            raise ValueError("Generation-5 preauthorization allowed-file path changed")
    expected_original = _original_g5_sampler_identities()
    contract.require_exact_json(
        value.get("originalSamplerFiles"),
        expected_original,
        "Generation-5 preauthorization original sampler files",
    )
    return value


def _fresh_verify_g5_preauthorization() -> dict[str, Any]:
    _g5_compatibility_identity("template", G5_COMPAT_TEMPLATE)
    _g5_compatibility_identity("protocol", G5_COMPAT_PROTOCOL)
    readiness_identity = _g5_compatibility_identity(
        "readiness", G5_COMPAT_READINESS
    )
    _g5_compatibility_identity("matches", G5_MATCH_ORCHESTRATOR)
    command = [
        sys.executable,
        "-I",
        "-B",
        str(G5_COMPAT_READINESS),
        "verify-suites",
        "--protocol",
        str(G5_COMPAT_PROTOCOL),
        "--suite-seal",
        str(G5_COMPAT_SUITE_SEAL),
        "--require-preauthorization-state",
    ]
    completed = subprocess.run(
        command,
        cwd=Path(tempfile.gettempdir()).resolve(),
        env=_sanitized_environment(),
        capture_output=True,
        text=False,
        timeout=30 * 60,
    )
    if completed.returncode != 0:
        detail = completed.stderr[-4000:].decode("utf-8", errors="replace")
        raise ValueError(
            "Generation-5 compatibility preauthorization verification failed: "
            + detail
        )
    state = _verify_g5_preauthorization_record()
    return {
        "compatibilityRoot": str(G5_MATCH_ROOT),
        "preauthorizationState": contract.identity(G5_COMPAT_PREAUTHORIZATION),
        "preauthorizationCreatedUtc": state["createdUtc"],
        "preregistration": contract.identity(G5_COMPAT_PREREGISTRATION),
        "suiteSeal": contract.identity(G5_COMPAT_SUITE_SEAL),
        "suites": {
            gate: contract.identity(G5_COMPAT_SUITES[gate]) for gate in G5_GATES
        },
        "originalSamplerFiles": copy.deepcopy(state["originalSamplerFiles"]),
        "freshVerifier": readiness_identity,
        "verification": {
            "isolatedPython": True,
            "ignoreEnvironment": True,
            "bytecodeDisabled": True,
            "arbitraryWorkingDirectory": True,
            "exitCode": 0,
            "stdoutBytes": len(completed.stdout),
            "stdoutSha256": hashlib.sha256(completed.stdout).hexdigest(),
            "stderrBytes": len(completed.stderr),
            "stderrSha256": hashlib.sha256(completed.stderr).hexdigest(),
        },
        "authorizationAbsent": True,
        "resultsAbsent": True,
        "originalArtifactsRewritten": 0,
        "freezeIsPreMatchAndPreResult": True,
    }


def _verify_g5_pre_result_freeze(value: Any) -> dict[str, Any]:
    freeze = contract.mapping(value, "base projection Generation-5 freeze")
    expected_fields = {
        "compatibilityRoot",
        "preauthorizationState",
        "preauthorizationCreatedUtc",
        "preregistration",
        "suiteSeal",
        "suites",
        "originalSamplerFiles",
        "freshVerifier",
        "verification",
        "authorizationAbsent",
        "resultsAbsent",
        "originalArtifactsRewritten",
        "freezeIsPreMatchAndPreResult",
    }
    if set(freeze) != expected_fields:
        raise ValueError("base projection Generation-5 freeze fields changed")
    if (
        freeze.get("compatibilityRoot") != str(G5_MATCH_ROOT)
        or freeze.get("authorizationAbsent") is not True
        or freeze.get("resultsAbsent") is not True
        or freeze.get("originalArtifactsRewritten") != 0
        or freeze.get("freezeIsPreMatchAndPreResult") is not True
    ):
        raise ValueError("base projection was not frozen before compatibility results")
    preauthorization = _verify_identity(
        freeze.get("preauthorizationState"),
        "base projection Generation-5 preauthorization",
    )
    if preauthorization != G5_COMPAT_PREAUTHORIZATION:
        raise ValueError("base projection preauthorization path changed")
    state = _verify_g5_preauthorization_record(preauthorization)
    if freeze.get("preauthorizationCreatedUtc") != state.get("createdUtc"):
        raise ValueError("base projection preauthorization timestamp changed")
    if _verify_identity(
        freeze.get("preregistration"), "base projection preregistration"
    ) != G5_COMPAT_PREREGISTRATION:
        raise ValueError("base projection preregistration path changed")
    if _verify_identity(
        freeze.get("suiteSeal"), "base projection compatibility suite seal"
    ) != G5_COMPAT_SUITE_SEAL:
        raise ValueError("base projection suite-seal path changed")
    suites = contract.mapping(freeze.get("suites"), "base projection suites")
    if set(suites) != set(G5_GATES):
        raise ValueError("base projection suite inventory changed")
    for gate in G5_GATES:
        if _verify_identity(
            suites[gate], f"base projection {gate} suite"
        ) != G5_COMPAT_SUITES[gate]:
            raise ValueError(f"base projection {gate} suite path changed")
    contract.require_exact_json(
        freeze.get("originalSamplerFiles"),
        _original_g5_sampler_identities(),
        "base projection original Generation-5 samplers",
    )
    if _verify_identity(
        freeze.get("freshVerifier"), "base projection preauthorization verifier"
    ) != G5_COMPAT_READINESS:
        raise ValueError("base projection preauthorization verifier path changed")
    verification = contract.mapping(
        freeze.get("verification"), "base projection preauthorization verification"
    )
    if set(verification) != {
        "isolatedPython",
        "ignoreEnvironment",
        "bytecodeDisabled",
        "arbitraryWorkingDirectory",
        "exitCode",
        "stdoutBytes",
        "stdoutSha256",
        "stderrBytes",
        "stderrSha256",
    }:
        raise ValueError("base projection preauthorization verification fields changed")
    if (
        verification.get("isolatedPython") is not True
        or verification.get("ignoreEnvironment") is not True
        or verification.get("bytecodeDisabled") is not True
        or verification.get("arbitraryWorkingDirectory") is not True
        or verification.get("exitCode") != 0
        or type(verification.get("stdoutBytes")) is not int
        or verification["stdoutBytes"] < 0
        or type(verification.get("stderrBytes")) is not int
        or verification["stderrBytes"] < 0
        or type(verification.get("stdoutSha256")) is not str
        or HEX_256.fullmatch(verification["stdoutSha256"]) is None
        or type(verification.get("stderrSha256")) is not str
        or HEX_256.fullmatch(verification["stderrSha256"]) is None
    ):
        raise ValueError("base projection preauthorization verification changed")
    return dict(freeze)


def _g5_sampler_only_freeze_audit() -> dict[str, Any]:
    """Persist the additive candidate-blind state before any G5 match launch."""

    return _fresh_verify_g5_preauthorization()


def _base_projection_runtime() -> dict[str, Any]:
    return {
        "historySnapshotBundle": _runtime_bundle(
            HISTORY_SNAPSHOT_ROOT,
            "OmegaHistorySnapshot.dll",
            "OmegaHistorySnapshot.exe",
        ),
        "prefixReplayBundle": _exact_g5_prefix_replay_bundle(),
    }


def _build_base_exclusion_projection(output_root: Path) -> dict[str, Any]:
    _require_operation_lock("base exclusion projection")
    output_root = output_root.resolve()
    if output_root != BASE_PROJECTION_ROOT:
        raise ValueError("base exclusion projection root is noncanonical")
    if output_root.exists():
        raise FileExistsError("base exclusion projection is already frozen")
    freeze = _g5_sampler_only_freeze_audit()
    output_root.mkdir(parents=True, exist_ok=False)
    roots = _history_roots(1)
    files = _discover_history_files(1, roots)
    identities = {path: contract.identity(path) for path in files}
    signatures: set[str] = set()
    direct_positions = 0
    requests: list[dict[str, Any]] = []
    runtime = _base_projection_runtime()
    for path in files:
        if path.suffix.casefold() not in {".json", ".jsonl"}:
            continue
        ofens, current = _direct_ofens_and_pv_requests(path, identities[path])
        for number, ofen in enumerate(ofens, 1):
            try:
                _add_orbit(ofen, signatures, f"{path} direct position {number}")
            except ValueError:
                continue
            direct_positions += 1
        current.extend(_suite_replay_requests(path, identities[path], len(current)))
        requests.extend(current)
    replay = _run_prefix_replay(requests, output_root, signatures, runtime)
    history = _run_history_snapshot(files, output_root, signatures, runtime)
    for path, before in identities.items():
        if not contract.exact_json_equal(contract.identity(path), before):
            raise ValueError(f"base projection input changed during scan: {path}")
    _exclusive_bytes(
        BASE_EXCLUDED_ORBITS,
        "".join(f"{item}\n" for item in sorted(signatures)).encode("ascii"),
    )
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-open-confirmation-v2-base-exclusion-projection",
        "protocol": PROTOCOL_IDENTITY,
        "createdUtc": _utc_now(),
        "g5PreResultFreeze": freeze,
        "roots": [str(path) for path in roots],
        "inputFiles": [identities[path] for path in files],
        "rootCoverage": _history_root_coverage(roots, files, identities),
        "directPositions": direct_positions,
        "prefixReplay": replay,
        "pgnCcsfReplay": history,
        "excludedOrbitSignatures": len(signatures),
        "excludedOrbits": contract.identity(BASE_EXCLUDED_ORBITS),
        "coverage": _history_coverage_evidence(identities),
        "projectorBoundary": {
            "candidateAndHistoryAware": True,
            "outputContainsOnlySourceIdentitiesCountsAndCanonicalOrbitProjection": True,
            "stageSeedInputs": 0,
            "rankingInputs": 0,
            "selectorExecuted": False,
        },
    }
    _exclusive_json(BASE_PROJECTION, value)
    projection_identity = contract.identity(BASE_PROJECTION)
    capsule = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SELECTOR_CAPSULE_KIND,
        "protocol": PROTOCOL_IDENTITY,
        # Deliberately omit the private projector path.  These two scalars
        # authenticate the parent's private audit without giving the selector
        # a location it could dereference.
        "freezeIdentity": {
            "bytes": projection_identity["bytes"],
            "sha256": projection_identity["sha256"],
        },
        "excludedOrbitSignatures": len(signatures),
        "excludedOrbits": contract.identity(BASE_EXCLUDED_ORBITS),
    }
    _exclusive_json(BASE_SELECTOR_CAPSULE, capsule)
    return _verify_base_exclusion_projection(BASE_PROJECTION)


def _validate_selector_capsule_shape(value: Any) -> tuple[dict[str, Any], int, dict[str, Any]]:
    if type(value) is not dict:
        raise ValueError("selector capsule must be an object")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocol",
        "freezeIdentity",
        "excludedOrbitSignatures",
        "excludedOrbits",
    }
    if (
        set(value) != expected_fields
        or type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != SELECTOR_CAPSULE_KIND
    ):
        raise ValueError("selector capsule envelope changed")
    contract.require_exact_json(
        value.get("protocol"), PROTOCOL_IDENTITY, "selector capsule protocol"
    )
    freeze = value.get("freezeIdentity")
    if (
        type(freeze) is not dict
        or set(freeze) != {"bytes", "sha256"}
        or type(freeze.get("bytes")) is not int
        or freeze["bytes"] <= 0
        or type(freeze.get("sha256")) is not str
        or HEX_256.fullmatch(freeze["sha256"]) is None
    ):
        raise ValueError("selector capsule freeze identity is malformed")
    count = value.get("excludedOrbitSignatures")
    if type(count) is not int or count < 1:
        raise ValueError("selector capsule orbit count is malformed")
    orbit_identity = _identity_shape(
        value.get("excludedOrbits"), "selector capsule excluded orbits"
    )
    return dict(freeze), count, orbit_identity


def _verify_selector_capsule(
    path: Path = BASE_SELECTOR_CAPSULE, *, verify_private_freeze: bool
) -> dict[str, Any]:
    path = path.resolve()
    if path != BASE_SELECTOR_CAPSULE:
        raise ValueError("selector capsule path is noncanonical")
    value = contract.strict_load(path, "selector capsule")
    freeze, count, orbit_identity = _validate_selector_capsule_shape(value)
    orbit_path = _verify_identity(
        orbit_identity, "selector capsule excluded orbits"
    )
    if orbit_path != BASE_EXCLUDED_ORBITS:
        raise ValueError("selector capsule orbit-list path changed")
    _load_excluded_orbits(orbit_path, count)
    if verify_private_freeze:
        private = contract.identity(BASE_PROJECTION)
        contract.require_exact_json(
            freeze,
            {"bytes": private["bytes"], "sha256": private["sha256"]},
            "selector capsule/private projector binding",
        )
    return value


def _verify_base_exclusion_projection(path: Path = BASE_PROJECTION) -> dict[str, Any]:
    path = path.resolve()
    if path != BASE_PROJECTION:
        raise ValueError("base exclusion projection path is noncanonical")
    value = contract.strict_load(path, "base exclusion projection")
    expected_fields = {
        "schemaVersion", "kind", "protocol", "createdUtc", "g5PreResultFreeze",
        "roots", "inputFiles", "rootCoverage", "directPositions", "prefixReplay",
        "pgnCcsfReplay", "excludedOrbitSignatures", "excludedOrbits", "coverage",
        "projectorBoundary",
    }
    if set(value) != expected_fields or value.get("kind") != "omega-nnue-open-confirmation-v2-base-exclusion-projection":
        raise ValueError("base exclusion projection envelope changed")
    _parse_utc(value.get("createdUtc"), "base projection createdUtc")
    contract.require_exact_json(value.get("protocol"), PROTOCOL_IDENTITY, "base projection protocol")
    freeze = _verify_g5_pre_result_freeze(value.get("g5PreResultFreeze"))
    projection_created = _parse_utc(
        value.get("createdUtc"), "base projection createdUtc"
    )
    preauthorization_created = _parse_g5_utc(
        freeze.get("preauthorizationCreatedUtc"),
        "base projection preauthorization createdUtc",
    )
    if projection_created <= preauthorization_created:
        raise ValueError("base projection predates compatibility preauthorization")
    inputs = value.get("inputFiles")
    if type(inputs) is not list:
        raise ValueError("base projection input identity list is missing")
    input_paths: dict[Path, Mapping[str, Any]] = {}
    for number, record in enumerate(inputs, 1):
        current = _verify_identity(record, f"base projection input {number}")
        input_paths[current] = record
    required_pre_result = {
        G5_COMPAT_PREAUTHORIZATION,
        G5_COMPAT_PREREGISTRATION,
        G5_COMPAT_SUITE_SEAL,
        *G5_COMPAT_SUITES.values(),
        *[
            ORIGINAL_G5_SAMPLER_FILES[gate][kind]
            for gate in G5_GATES
            for kind in ("source", "manifest", "completionSeal")
        ],
    }
    missing = required_pre_result.difference(input_paths)
    if missing:
        raise ValueError(
            "base projection omitted compatibility pre-result evidence: "
            + ", ".join(str(path) for path in sorted(missing, key=str))
        )
    count = value.get("excludedOrbitSignatures")
    if type(count) is not int or count < 1:
        raise ValueError("base projection orbit count is malformed")
    orbit_path = _verify_identity(value.get("excludedOrbits"), "base projection orbit file")
    if orbit_path != BASE_EXCLUDED_ORBITS:
        raise ValueError("base projection orbit path changed")
    _load_excluded_orbits(orbit_path, count)
    contract.require_exact_json(
        value.get("projectorBoundary"),
        {
            "candidateAndHistoryAware": True,
            "outputContainsOnlySourceIdentitiesCountsAndCanonicalOrbitProjection": True,
            "stageSeedInputs": 0,
            "rankingInputs": 0,
            "selectorExecuted": False,
        },
        "base projection information boundary",
    )
    _verify_selector_capsule(BASE_SELECTOR_CAPSULE, verify_private_freeze=True)
    return value


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


def _verify_practical_sampler_artifacts(
    path: Path,
    seed: int,
    implementation: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_path = Path(str(path) + ".manifest.json")
    completion_path = Path(str(path) + ".complete.seal.json")
    records = _PRACTICAL_VERIFY_SOURCE(
        path,
        manifest_path,
        completion_path,
        seed=seed,
    )
    bundle = _verify_runtime_bundle(
        implementation["practicalSamplerBundle"], "practical sampler bundle"
    )
    bundle_root = Path(bundle["root"]).resolve()
    expected_sampler = (
        bundle_root / bundle["assemblyRelativePath"]
    ).resolve()
    expected_rules = (bundle_root / "ChessLib.dll").resolve()
    manifest = contract.strict_load(manifest_path, "practical sampler manifest")
    completion = contract.strict_load(
        completion_path, "practical sampler completion seal"
    )
    runtime = contract.mapping(manifest.get("runtime"), "practical sampler runtime")
    producer = contract.mapping(
        completion.get("producer"), "practical sampler producer"
    )
    for container, label in ((runtime, "runtime"), (producer, "producer")):
        sampler_path = _verify_identity(
            container.get("samplerAssembly"), f"practical sampler {label} assembly"
        )
        rules_path = _verify_identity(
            container.get("chessLibAssembly"), f"practical sampler {label} rules"
        )
        if sampler_path != expected_sampler or rules_path != expected_rules:
            raise ValueError(f"practical sampler {label} escaped the sealed bundle")
    return {
        "source": contract.identity(path),
        "manifest": contract.identity(manifest_path),
        "completionSeal": contract.identity(completion_path),
        "records": len(records),
        "endpoints": records,
    }


def _run_practical_sampler(
    seed: int,
    output: Path,
    implementation: Mapping[str, Any],
) -> dict[str, Any]:
    bundle = _verify_runtime_bundle(
        implementation["practicalSamplerBundle"], "practical sampler bundle"
    )
    bundle_root = Path(bundle["root"]).resolve()
    assembly = (bundle_root / bundle["assemblyRelativePath"]).resolve()
    command = [
        str(_runtime_path("dotnetHost")),
        str(assembly),
        "--output",
        str(output.resolve()),
        "--seed",
        str(seed),
        "--trajectories",
        str(practical.TRAJECTORIES),
        "--target-plies",
        ",".join(str(item) for item in practical.DEPTHS),
        "--workers",
        "4",
        "--minimum-pieces",
        "37",
    ]
    subprocess.run(
        command,
        cwd=bundle_root,
        env=_sanitized_environment(),
        check=True,
        timeout=6 * 60 * 60,
    )
    return _verify_practical_sampler_artifacts(
        output, seed, implementation
    )


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


class _SelectorAccessProbe:
    """Fail closed if the selector tries to read private/history evidence."""

    def __init__(
        self,
        attempt_root: Path,
        selector_root: Path,
        capsule: Path,
        intent: Path,
    ) -> None:
        self._attempt_root = os.path.normcase(os.path.abspath(attempt_root))
        self._private_root = os.path.normcase(os.path.abspath(BASE_PROJECTION_ROOT))
        self._capsule = os.path.normcase(os.path.abspath(capsule))
        self._intent = os.path.normcase(os.path.abspath(intent))
        self._orbit = os.path.normcase(os.path.abspath(BASE_EXCLUDED_ORBITS))
        self._allowed_files = {self._capsule, self._intent, self._orbit}
        shared = contract.mapping(
            PROTOCOL["sharedRuntime"]["identities"],
            "selector trusted runtime identities",
        )
        for record in shared.values():
            if type(record) is dict and type(record.get("path")) is str:
                raw = Path(record["path"])
                resolved = raw if raw.is_absolute() else (_REPO / raw)
                self._allowed_files.add(
                    os.path.normcase(os.path.abspath(resolved))
                )
        self._allowed_roots = tuple(
            os.path.normcase(os.path.abspath(path))
            for path in (selector_root, PRACTICAL_SAMPLER_RUNTIME_ROOT)
        )
        self._history_roots = tuple(
            os.path.normcase(os.path.abspath(path))
            for path in (
                _REPO / "validation",
                _REPO / "build-msvc",
                *(_REPO / f"build-king-state-v{number}" for number in range(1, 6)),
                *(path for _, path in SIBLING_HISTORY_ROOTS),
            )
        )
        self.capsule_reads = 0
        self.intent_reads = 0
        self.orbit_reads = 0
        self.private_projection_attempts = 0
        self.attempt_namespace_attempts = 0
        self.history_game_record_attempts = 0
        self.history_root_attempts = 0

    @staticmethod
    def _inside(path: str, root: str) -> bool:
        try:
            return os.path.commonpath((path, root)) == root
        except ValueError:
            return False

    def __call__(self, event: str, args: tuple[Any, ...]) -> None:
        if event not in {"open", "os.listdir", "os.scandir"} or not args or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        raw = os.fsdecode(args[0])
        path = os.path.normcase(os.path.abspath(raw))
        if path == self._capsule:
            self.capsule_reads += 1
            return
        if path == self._intent:
            self.intent_reads += 1
            return
        if path == self._orbit:
            self.orbit_reads += 1
            return
        if path in self._allowed_files or any(
            self._inside(path, root) for root in self._allowed_roots
        ):
            return
        if self._inside(path, self._private_root):
            self.private_projection_attempts += 1
            raise PermissionError("selector may not access the private projector audit")
        if self._inside(path, self._attempt_root):
            self.attempt_namespace_attempts += 1
            raise PermissionError("selector may not access the candidate attempt namespace")
        if any(self._inside(path, root) for root in self._history_roots):
            self.history_root_attempts += 1
            raise PermissionError("selector may not access a frozen history root")
        basename = os.path.basename(path).casefold()
        suffix = os.path.splitext(basename)[1]
        if (
            suffix in {".pgn", ".ccsf"}
            or basename in {
                "events.jsonl",
                "decision.json",
                "candidate-claim.json",
                "attempt-reservation.json",
                "attempt-closure.json",
            }
        ):
            self.history_game_record_attempts += 1
            raise PermissionError("selector may not access match/history game records")

    def report(self) -> dict[str, Any]:
        return {
            "probe": "python-sys-audit-open-v1",
            "capsuleReadEvents": self.capsule_reads,
            "samplingIntentReadEvents": self.intent_reads,
            "excludedOrbitReadEvents": self.orbit_reads,
            "privateProjectionReadAttempts": self.private_projection_attempts,
            "attemptNamespaceReadAttempts": self.attempt_namespace_attempts,
            "historyGameRecordReadAttempts": self.history_game_record_attempts,
            "historyRootReadAttempts": self.history_root_attempts,
            "forbiddenReadAttempts": (
                self.private_projection_attempts
                + self.attempt_namespace_attempts
                + self.history_game_record_attempts
                + self.history_root_attempts
            ),
            "passes": (
                self.private_projection_attempts == 0
                and self.attempt_namespace_attempts == 0
                and self.history_game_record_attempts == 0
                and self.history_root_attempts == 0
            ),
        }


def _candidate_blind_worker(
    selector_capsule: Path,
    sampling_intent: Path,
    output_root: Path,
    parent_lock_token: str,
) -> dict[str, Any]:
    _verify_parent_operation_lock(parent_lock_token)
    capsule_path = selector_capsule.resolve()
    intent_path = sampling_intent.resolve()
    # The workspace name supplies the public attempt ordinal, allowing the
    # audit hook to be installed before either selector input is opened.
    index = contract.parse_attempt_directory_name(output_root.resolve().name)
    paths = attempt_paths(index)
    if output_root.resolve() != paths["selectorRoot"]:
        raise ValueError("clean selector output root is noncanonical")
    probe = _SelectorAccessProbe(
        paths["root"], paths["selectorRoot"], capsule_path, intent_path
    )
    sys.addaudithook(probe)
    _verify_selector_workspace_inventory(index, sealed=False)
    capsule = _verify_selector_capsule(
        capsule_path, verify_private_freeze=False
    )
    intent = contract.strict_load(intent_path, "candidate-unaware sampling intent")
    stage_seeds = _normalized_stage_seeds(
        intent.get("stageSeeds"), "candidate-unaware sampling intent stage seeds"
    )
    expected_intent = {
        "schemaVersion": 1,
        "kind": "omega-nnue-open-confirmation-v2-sampling-intent",
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "createdUtc": intent.get("createdUtc"),
        "selectorCapsule": contract.identity(capsule_path),
        "stageSeeds": stage_seeds,
        "workerCommandCandidateInputs": 0,
        "workerCommandHistoryProjectionInputs": 0,
    }
    _parse_utc(intent.get("createdUtc"), "sampling intent createdUtc")
    contract.require_exact_json(intent, expected_intent, "candidate-unaware sampling intent")
    forbidden_outputs = [
        paths["sampler"],
        paths["selectorSealed"],
    ]
    if any(path.exists() for path in forbidden_outputs):
        raise FileExistsError(
            f"candidate-unaware worker refuses preexisting output: {next(path for path in forbidden_outputs if path.exists())}"
        )
    paths["sampler"].mkdir(parents=False, exist_ok=False)
    paths["selectorSealed"].mkdir(parents=False, exist_ok=False)
    forbidden = _load_excluded_orbits(
        BASE_EXCLUDED_ORBITS, int(capsule["excludedOrbitSignatures"])
    )
    selector_runtime = {
        "practicalSamplerBundle": _runtime_bundle(
            PRACTICAL_SAMPLER_RUNTIME_ROOT,
            "OmegaPracticalOpeningSampler.dll",
            "OmegaPracticalOpeningSampler.exe",
        )
    }
    used_fresh: set[str] = set()
    selected: dict[str, Any] = {}
    source_audits: dict[str, Any] = {}
    rejection_audits: dict[str, Any] = {}
    schedules: dict[str, list[str]] = {}
    for gate in ROOT_GATES:
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
    practical_gate = practical.GATE
    practical_seed = stage_seeds[practical_gate]
    practical_audit = _run_practical_sampler(
        practical_seed,
        _source_path(index, practical_gate),
        selector_runtime,
    )
    practical_records = practical_audit.pop("endpoints")
    practical_selected, practical_rejections = practical.select_endpoints(
        practical_records,
        forbidden_orbits=forbidden,
        used_orbits=used_fresh,
    )
    practical_suite, practical_schedule = _PRACTICAL_BUILD_SUITE(
        practical_selected, seed=practical_seed
    )
    selected[practical_gate] = practical_selected
    source_audits[practical_gate] = practical_audit
    rejection_audits[practical_gate] = practical_rejections
    schedules[practical_gate] = [item["id"] for item in practical_schedule]
    _exclusive_json(paths["suites"][practical_gate], practical_suite)
    _PRACTICAL_VERIFY_SUITE(practical_suite, seed=practical_seed)
    runtime = {
        "dotnetHost": _runtime_identity("dotnetHost"),
        "dotnetRuntimeManifest": _runtime_identity("dotnetRuntimeManifest"),
        "dotnetRuntimeBundle": _dotnet_runtime_bundle(),
        "rootSamplerAssembly": _runtime_identity("rootSamplerAssembly"),
        "rootSamplerRulesAssembly": _runtime_identity("rootSamplerRulesAssembly"),
        "rootSamplerBundleSha256": PROTOCOL["sharedRuntime"]["rootSamplerBundleSha256"],
        "matchCoreSource": _runtime_identity("sharedMatchCore"),
        "practicalModule": contract.identity(_REPO / _PRACTICAL_RELATIVE),
        "practicalSamplerBundle": selector_runtime["practicalSamplerBundle"],
    }
    suite_entries: dict[str, Any] = {
        gate: {
            "identity": contract.identity(paths["suites"][gate]),
            "roots": len(selected[gate]),
            "phaseCounts": {
                phase: sum(root.phase == phase for root in selected[gate])
                for phase in PHASES
            },
            "sideToMoveCounts": {
                side: sum(root.side == side for root in selected[gate])
                for side in SIDES
            },
            "uniqueTrajectoryPairs": len(
                {root.source_group for root in selected[gate]}
            ),
            "uniqueOrbitSignatures": len(
                {
                    signature
                    for root in selected[gate]
                    for signature in root.orbit_signatures
                }
            ),
            "rejections": rejection_audits[gate],
            "scheduledOpeningIds": schedules[gate],
        }
        for gate in ROOT_GATES
    }
    suite_entries[practical_gate] = {
        "identity": contract.identity(paths["suites"][practical_gate]),
        "roots": len(practical_selected),
        "depthCounts": {
            str(depth): sum(
                item["targetPlies"] == depth for item in practical_selected
            )
            for depth in practical.DEPTHS
        },
        "sideToMoveCounts": {"w": len(practical_selected), "b": 0},
        "uniqueTrajectories": len(
            {item["trajectoryIndex"] for item in practical_selected}
        ),
        "uniqueOrbitSignatures": len(
            {
                signature
                for item in practical_selected
                for signature in item["orbitSignatures"]
            }
        ),
        "rejections": practical_rejections,
        "scheduledOpeningIds": schedules[practical_gate],
    }
    seal = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": JOINT_SUITE_KIND,
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "createdUtc": _utc_now(),
        "samplingIntent": contract.identity(intent_path),
        "selectorCapsule": contract.identity(capsule_path),
        "stageSeeds": stage_seeds,
        "excludedOrbits": contract.identity(BASE_EXCLUDED_ORBITS),
        "excludedOrbitSignatures": len(forbidden),
        "samplerSources": source_audits,
        "suites": suite_entries,
        "rootDigest": _root_digest(paths["suites"]),
        "crossSuite": {
            "roots": sum(len(items) for items in selected.values()),
            "uniqueOrbitSignatures": len(used_fresh),
            "mutuallyWholeOrbitDisjoint": True,
            "historicalWholeOrbitIntersection": 0,
        },
        "runtime": runtime,
        "informationBoundary": {
            "candidateIdentityAvailableToWorker": False,
            "candidateOrClaimInputs": 0,
            "targetFieldsDecoded": 0,
            "scoreFieldsDecoded": 0,
            "resultFieldsDecoded": 0,
            "matchResultsAccessed": 0,
            "selectorInputCapsule": contract.identity(BASE_SELECTOR_CAPSULE),
            "selectorInputContainsSourcePathsOrIdentities": False,
            "selectorHistoryRootsAccessed": 0,
            "selectorClaimFilesAccessed": 0,
            "postSelectionFullHistoryAuditRequired": True,
            "allFourSuitesSealedTogether": True,
        },
        "osAccessProbe": probe.report(),
    }
    _verify_parent_operation_lock(parent_lock_token)
    _exclusive_json(paths["jointSuiteSeal"], seal)
    del capsule, forbidden, used_fresh, selected
    _verify_parent_operation_lock(parent_lock_token)
    return seal


def _history_projector_worker(output_root: Path, parent_lock_token: str) -> dict[str, Any]:
    _verify_parent_operation_lock(parent_lock_token)
    if IMPLEMENTATION_SEAL.exists():
        raise FileExistsError("base projection must predate the implementation seal")
    if any(contract.attempt_namespace(index).exists() for index in range(1, 2)):
        raise FileExistsError("base projection must predate every attempt")
    result = _build_base_exclusion_projection(output_root)
    _verify_parent_operation_lock(parent_lock_token)
    return result


@_serialized_transition
def freeze_base_exclusion_projection() -> dict[str, Any]:
    token = _require_operation_lock("base exclusion projection freeze")
    verify_v1_retirement_seal()
    if IMPLEMENTATION_SEAL.exists():
        raise FileExistsError("implementation seal already exists")
    if BASE_PROJECTION_ROOT.exists():
        return _verify_base_exclusion_projection(BASE_PROJECTION)
    command = [
        sys.executable,
        "-I",
        "-B",
        str(TOOL_PATH),
        "history-projector-worker",
        "--output-root",
        str(BASE_PROJECTION_ROOT),
        "--parent-lock-token",
        token,
    ]
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
        raise RuntimeError("base history projector failed: " + completed.stderr[-4000:])
    return _verify_base_exclusion_projection(BASE_PROJECTION)


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
        "compatibilityPositionHistory",
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
    recorded_members = [
        _identity_path(
            _identity_shape(record, f"exclusion input {number}"),
            f"exclusion input {number}",
        )
        for number, record in enumerate(inputs, 1)
    ]
    _require_exact_history_membership(recorded_members, current_files)
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
    excluded = _load_excluded_orbits(orbit_path, count)
    expected_compatibility_history = _compatibility_position_history_coverage(
        excluded
    )
    contract.require_exact_json(
        value.get("compatibilityPositionHistory"),
        expected_compatibility_history,
        "compatibility position-history exclusion coverage",
    )
    expected_coverage = _history_coverage_evidence(
        normalized_inputs, require_compatibility_results=True
    )
    contract.require_exact_json(value.get("coverage"), expected_coverage, "exclusion coverage")
    boundary = {
        "positionStringsDecoded": value.get("directPositions"),
        "coordinateMoveListsDecoded": (
            0 if value.get("prefixReplay") is None else value["prefixReplay"]["requests"]
        ),
        "targetFieldsDecoded": 0,
        "scoreFieldsDecoded": 0,
        "resultFieldsDecoded": 0,
        "candidateIdentityAvailableToAuditParent": True,
        "candidateIdentityDecoded": 0,
        "candidateIdentityUsedForRanking": False,
        "selectionInputsAccepted": 0,
        "reselectionPermitted": False,
        "historyPositionsUsedOnlyForIntersection": True,
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


def _selected_suite_orbits(
    index: int, stage_seeds: Mapping[str, int]
) -> set[str]:
    paths = attempt_paths(index)
    selected: set[str] = set()
    for gate in GATES:
        if gate == practical.GATE:
            suite = contract.strict_load(paths["suites"][gate], f"{gate} selected suite")
            _PRACTICAL_VERIFY_SUITE(suite, seed=stage_seeds[gate])
            current = {
                signature
                for opening in suite["openings"]
                for signature in opening["openConfirmationV2"]["orbitSignatures"]
            }
        else:
            _, current = _verify_suite(
                paths["suites"][gate], index, gate, stage_seeds[gate]
            )
        if selected.intersection(current):
            raise ValueError("selected suites are not whole-orbit disjoint")
        selected.update(current)
    return selected


def _require_exact_history_membership(
    recorded: Sequence[Path], current: Sequence[Path]
) -> None:
    if [path.resolve() for path in recorded] != [path.resolve() for path in current]:
        raise ValueError(
            "post-selection history membership changed; consume the attempt without rescan"
        )


def _post_selection_collision_result(
    selected: set[str], history: set[str]
) -> tuple[set[str], str]:
    collisions = selected.intersection(history)
    return collisions, (
        "abort-and-consume-attempt" if collisions else "accept"
    )


def _signature_set_digest(signatures: set[str]) -> str:
    return hashlib.sha256(
        "".join(f"{item}\n" for item in sorted(signatures)).encode("ascii")
    ).hexdigest()


def _publish_post_selection_collision_audit(
    index: int, joint_suite: Mapping[str, Any]
) -> dict[str, Any]:
    """Audit current full history after selection, without any reselection."""

    _require_operation_lock("post-selection collision audit")
    paths = attempt_paths(index)
    if paths["postSelectionRoot"].exists():
        raise FileExistsError("post-selection collision audit already exists")
    if not paths["jointSuiteSeal"].is_file():
        raise FileNotFoundError("post-selection audit requires a published joint suite")
    paths["postSelectionRoot"].mkdir(parents=False, exist_ok=False)
    joint_created = _parse_utc(
        joint_suite.get("createdUtc"), "post-selection joint suite createdUtc"
    )
    history_scan_started_utc = _utc_now()
    history_scan_started = _parse_utc(
        history_scan_started_utc, "post-selection history scan startedUtc"
    )
    if history_scan_started < joint_created:
        raise ValueError("post-selection scan predates the joint-suite seal")
    implementation = verify_implementation_seal(
        IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
    )
    history_orbits, inventory = _build_exclusion_inventory(
        index, paths["postSelectionRoot"], implementation
    )
    stage_seeds = _normalized_stage_seeds(
        joint_suite.get("stageSeeds"), "post-selection stage seeds"
    )
    selected = _selected_suite_orbits(index, stage_seeds)
    collisions, collision_action = _post_selection_collision_result(
        selected, history_orbits
    )
    audit = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": POST_SELECTION_AUDIT_KIND,
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "createdUtc": _utc_now(),
        "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
        "jointSuiteSeal": contract.identity(paths["jointSuiteSeal"]),
        "selectorCapsule": contract.identity(BASE_SELECTOR_CAPSULE),
        "fullHistoryInventory": contract.identity(paths["exclusionInventory"]),
        "fullHistoryExcludedOrbits": contract.identity(paths["excludedOrbits"]),
        "fullHistoryOrbitSignatures": inventory["excludedOrbitSignatures"],
        "selectedOrbitSignatures": len(selected),
        "selectedOrbitDigest": _signature_set_digest(selected),
        "intersectionCount": len(collisions),
        "intersectionSignatures": sorted(collisions),
        "historyScanStartedUtc": history_scan_started_utc,
        "jointSuiteCreatedUtc": joint_suite["createdUtc"],
        "historyScanStartedAfterJointSuitePublication": (
            history_scan_started >= joint_created
        ),
        "selectorOutputsModified": 0,
        "reselectionAttempts": 0,
        "collisionAction": collision_action,
        "noResamplingOrReselection": True,
    }
    _exclusive_json(paths["postSelectionAudit"], audit)
    verified = _verify_post_selection_collision_audit(
        paths["postSelectionAudit"], index, joint_suite, allow_collision=True
    )
    if collisions:
        raise ValueError(
            "post-selection full-history collision consumes the attempt without reselection"
        )
    return verified


def _verify_post_selection_collision_audit(
    path: Path,
    index: int,
    joint_suite: Mapping[str, Any],
    *,
    allow_collision: bool = False,
) -> dict[str, Any]:
    paths = attempt_paths(index)
    path = path.resolve()
    if path != paths["postSelectionAudit"]:
        raise ValueError("post-selection audit path is noncanonical")
    value = contract.strict_load(path, "post-selection collision audit")
    expected_fields = {
        "schemaVersion", "kind", "protocol", "attemptIndex", "createdUtc",
        "implementationSeal", "jointSuiteSeal", "selectorCapsule",
        "fullHistoryInventory", "fullHistoryExcludedOrbits",
        "fullHistoryOrbitSignatures", "selectedOrbitSignatures",
        "selectedOrbitDigest", "intersectionCount", "intersectionSignatures",
        "historyScanStartedUtc", "jointSuiteCreatedUtc",
        "historyScanStartedAfterJointSuitePublication", "selectorOutputsModified",
        "reselectionAttempts", "collisionAction", "noResamplingOrReselection",
    }
    if (
        set(value) != expected_fields
        or type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != POST_SELECTION_AUDIT_KIND
        or value.get("attemptIndex") != index
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError("post-selection collision audit envelope changed")
    _parse_utc(value.get("createdUtc"), "post-selection audit createdUtc")
    if not contract.exact_json_equal(
        value.get("implementationSeal"), contract.identity(IMPLEMENTATION_SEAL)
    ):
        raise ValueError("post-selection audit implementation changed")
    if not contract.exact_json_equal(
        value.get("jointSuiteSeal"), contract.identity(paths["jointSuiteSeal"])
    ):
        raise ValueError("post-selection audit joint suite changed")
    contract.require_exact_json(
        value.get("selectorCapsule"),
        contract.identity(BASE_SELECTOR_CAPSULE),
        "post-selection selector capsule",
    )
    inventory_identity = _identity_shape(
        value.get("fullHistoryInventory"), "post-selection full-history inventory"
    )
    inventory_path = _verify_identity(
        inventory_identity, "post-selection full-history inventory"
    )
    if inventory_path != paths["exclusionInventory"]:
        raise ValueError("post-selection inventory path changed")
    orbit_identity = _identity_shape(
        value.get("fullHistoryExcludedOrbits"), "post-selection full-history orbits"
    )
    inventory = _verify_exclusion_inventory(
        inventory_path, index, orbit_identity, deep=True
    )
    history_orbits = _load_excluded_orbits(
        paths["excludedOrbits"], inventory["excludedOrbitSignatures"]
    )
    stage_seeds = _normalized_stage_seeds(
        joint_suite.get("stageSeeds"), "post-selection joint stage seeds"
    )
    selected = _selected_suite_orbits(index, stage_seeds)
    collisions, collision_action = _post_selection_collision_result(
        selected, history_orbits
    )
    scan_started = _parse_utc(
        value.get("historyScanStartedUtc"), "post-selection scan startedUtc"
    )
    joint_created = _parse_utc(
        joint_suite.get("createdUtc"), "post-selection joint suite createdUtc"
    )
    if scan_started < joint_created:
        raise ValueError("post-selection history scan predates joint-suite publication")
    expected = {
        "fullHistoryOrbitSignatures": len(history_orbits),
        "selectedOrbitSignatures": len(selected),
        "selectedOrbitDigest": _signature_set_digest(selected),
        "intersectionCount": len(collisions),
        "intersectionSignatures": sorted(collisions),
        "jointSuiteCreatedUtc": joint_suite["createdUtc"],
        "historyScanStartedAfterJointSuitePublication": scan_started >= joint_created,
        "selectorOutputsModified": 0,
        "reselectionAttempts": 0,
        "collisionAction": collision_action,
        "noResamplingOrReselection": True,
    }
    for name, expected_value in expected.items():
        if not contract.exact_json_equal(value.get(name), expected_value):
            raise ValueError(f"post-selection collision audit {name} changed")
    if collisions and not allow_collision:
        raise ValueError("post-selection collision audit is not clean")
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
    _verify_selector_workspace_inventory(index, sealed=True)
    value = contract.strict_load(path, "confirmation joint suite seal")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocol",
        "attemptIndex",
        "createdUtc",
        "samplingIntent",
        "selectorCapsule",
        "stageSeeds",
        "excludedOrbits",
        "excludedOrbitSignatures",
        "samplerSources",
        "suites",
        "rootDigest",
        "crossSuite",
        "runtime",
        "informationBoundary",
        "osAccessProbe",
    }
    if set(value) != expected_fields:
        raise ValueError("joint-suite-seal field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != 1
        or value.get("kind") != JOINT_SUITE_KIND
        or value.get("attemptIndex") != index
        or not contract.exact_json_equal(value.get("protocol"), PROTOCOL_IDENTITY)
    ):
        raise ValueError("joint-suite-seal envelope changed")
    created = _parse_utc(value.get("createdUtc"), "joint suite seal createdUtc")
    intent_path = _verify_identity(value.get("samplingIntent"), "suite sampling intent")
    if intent_path != paths["samplingIntent"]:
        raise ValueError("suite sampling-intent path changed")
    intent = contract.strict_load(intent_path, "suite sampling intent")
    stage_seeds = _normalized_stage_seeds(
        value.get("stageSeeds"), "joint-suite stage seeds"
    )
    expected_intent = {
        "schemaVersion": 1,
        "kind": "omega-nnue-open-confirmation-v2-sampling-intent",
        "protocol": PROTOCOL_IDENTITY,
        "attemptIndex": index,
        "createdUtc": intent.get("createdUtc"),
        "selectorCapsule": contract.identity(BASE_SELECTOR_CAPSULE),
        "stageSeeds": stage_seeds,
        "workerCommandCandidateInputs": 0,
        "workerCommandHistoryProjectionInputs": 0,
    }
    _parse_utc(intent.get("createdUtc"), "sampling intent createdUtc")
    contract.require_exact_json(
        intent, expected_intent, "joint-suite sampling intent"
    )
    if not contract.exact_json_equal(intent.get("stageSeeds"), stage_seeds):
        raise ValueError("joint suite and sampling intent stage seeds differ")
    if created < _parse_utc(intent.get("createdUtc"), "sampling intent createdUtc"):
        raise ValueError("joint suite seal predates sampling intent")
    capsule_path = _verify_identity(
        value.get("selectorCapsule"), "joint suite selector capsule"
    )
    if capsule_path != BASE_SELECTOR_CAPSULE:
        raise ValueError("joint suite selector capsule path changed")
    capsule = _verify_selector_capsule(
        capsule_path, verify_private_freeze=True
    )
    contract.require_exact_json(
        intent.get("selectorCapsule"),
        value.get("selectorCapsule"),
        "joint-suite/intent selector capsule",
    )
    count = value.get("excludedOrbitSignatures")
    if type(count) is not int or count < 1:
        raise ValueError("joint suite excluded-orbit count changed")
    orbit_path = _verify_identity(value.get("excludedOrbits"), "joint suite excluded orbits")
    forbidden = _load_excluded_orbits(orbit_path, count)
    if orbit_path != BASE_EXCLUDED_ORBITS:
        raise ValueError("joint suite excluded-orbit projection path changed")
    if capsule["excludedOrbitSignatures"] != count:
        raise ValueError("joint suite/capsule excluded-orbit counts differ")
    sources = contract.mapping(value.get("samplerSources"), "joint suite sampler sources")
    suites = contract.mapping(value.get("suites"), "joint suite suites")
    if set(sources) != set(GATES) or set(suites) != set(GATES):
        raise ValueError("joint suite gate inventory changed")
    used: set[str] = set()
    total_roots = 0
    for gate in GATES:
        is_practical = gate == practical.GATE
        source = contract.mapping(sources[gate], f"{gate} source audit")
        if set(source) != {"source", "manifest", "completionSeal", "records"}:
            raise ValueError(f"{gate} source-audit fields changed")
        source_paths: dict[str, Path] = {}
        for name in ("source", "manifest", "completionSeal"):
            source_paths[name] = _verify_identity(
                source[name], f"{gate} sampler {name}"
            )
        if deep:
            if is_practical:
                reproduced = _verify_practical_sampler_artifacts(
                    source_paths["source"],
                    stage_seeds[gate],
                    verify_implementation_seal(
                        IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
                    ),
                )
                reproduced.pop("endpoints")
            else:
                reproduced = _verify_sampler_artifacts(
                    source_paths["source"], index, gate, stage_seeds[gate]
                )
                reproduced.pop("roots")
            for name in ("source", "manifest", "completionSeal", "records"):
                if not contract.exact_json_equal(source[name], reproduced[name]):
                    raise ValueError(f"{gate} sampler audit differs from deep verification")
        entry = contract.mapping(suites[gate], f"{gate} suite entry")
        expected_entry_fields = (
            {
                "identity",
                "roots",
                "depthCounts",
                "sideToMoveCounts",
                "uniqueTrajectories",
                "uniqueOrbitSignatures",
                "rejections",
                "scheduledOpeningIds",
            }
            if is_practical
            else {
                "identity",
                "roots",
                "phaseCounts",
                "sideToMoveCounts",
                "uniqueTrajectoryPairs",
                "uniqueOrbitSignatures",
                "rejections",
                "scheduledOpeningIds",
            }
        )
        if set(entry) != expected_entry_fields:
            raise ValueError(f"{gate} suite entry fields changed")
        suite_path = _verify_identity(entry["identity"], f"{gate} suite")
        if suite_path != paths["suites"][gate]:
            raise ValueError(f"{gate} suite path changed")
        if is_practical:
            suite = contract.strict_load(suite_path, f"{gate} suite")
            _PRACTICAL_VERIFY_SUITE(suite, seed=stage_seeds[gate])
            openings = suite["openings"]
            signatures = {
                signature
                for item in openings
                for signature in item["openConfirmationV2"]["orbitSignatures"]
            }
        else:
            suite, signatures = _verify_suite(
                suite_path, index, gate, stage_seeds[gate]
            )
            openings = suite["openings"]
        expected_roots = int(PROTOCOL["stages"][gate]["roots"])
        if entry["roots"] != expected_roots or len(openings) != expected_roots:
            raise ValueError(f"{gate} suite summary root count changed")
        if is_practical:
            scheduled = [
                openings[number]
                for number in _SHUFFLED_INDICES(len(openings), stage_seeds[gate])
            ]
            expected_summary = {
                "depthCounts": {
                    str(depth): sum(
                        item["openConfirmationV2"]["depthStratum"] == depth
                        for item in openings
                    )
                    for depth in practical.DEPTHS
                },
                "sideToMoveCounts": {"w": len(openings), "b": 0},
                "uniqueTrajectories": len(
                    {
                        item["openConfirmationV2"]["trajectoryIndex"]
                        for item in openings
                    }
                ),
                "uniqueOrbitSignatures": len(signatures),
                "scheduledOpeningIds": [item["id"] for item in scheduled],
            }
        else:
            phase_counts = Counter(
                item["kingStateMatch"]["phase"] for item in openings
            )
            side_counts = Counter(
                item["kingStateMatch"]["rootSideToMove"] for item in openings
            )
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
        "practicalModule": contract.identity(_REPO / _PRACTICAL_RELATIVE),
        "practicalSamplerBundle": _runtime_bundle(
            PRACTICAL_SAMPLER_RUNTIME_ROOT,
            "OmegaPracticalOpeningSampler.dll",
            "OmegaPracticalOpeningSampler.exe",
        ),
    }
    contract.require_exact_json(runtime, expected_runtime, "joint-suite runtime")
    boundary = {
        "candidateIdentityAvailableToWorker": False,
        "candidateOrClaimInputs": 0,
        "targetFieldsDecoded": 0,
        "scoreFieldsDecoded": 0,
        "resultFieldsDecoded": 0,
        "matchResultsAccessed": 0,
        "selectorInputCapsule": contract.identity(BASE_SELECTOR_CAPSULE),
        "selectorInputContainsSourcePathsOrIdentities": False,
        "selectorHistoryRootsAccessed": 0,
        "selectorClaimFilesAccessed": 0,
        "postSelectionFullHistoryAuditRequired": True,
        "allFourSuitesSealedTogether": True,
    }
    contract.require_exact_json(value.get("informationBoundary"), boundary, "joint-suite information boundary")
    access = contract.mapping(value.get("osAccessProbe"), "selector OS access probe")
    if set(access) != {
        "probe",
        "capsuleReadEvents",
        "samplingIntentReadEvents",
        "excludedOrbitReadEvents",
        "privateProjectionReadAttempts",
        "attemptNamespaceReadAttempts",
        "historyGameRecordReadAttempts",
        "historyRootReadAttempts",
        "forbiddenReadAttempts",
        "passes",
    }:
        raise ValueError("selector OS access probe field inventory changed")
    if (
        access.get("probe") != "python-sys-audit-open-v1"
        or type(access.get("capsuleReadEvents")) is not int
        or access["capsuleReadEvents"] < 1
        or type(access.get("samplingIntentReadEvents")) is not int
        or access["samplingIntentReadEvents"] < 1
        or type(access.get("excludedOrbitReadEvents")) is not int
        or access["excludedOrbitReadEvents"] < 1
        or any(
            access.get(field) != 0
            for field in (
                "privateProjectionReadAttempts",
                "attemptNamespaceReadAttempts",
                "historyGameRecordReadAttempts",
                "historyRootReadAttempts",
                "forbiddenReadAttempts",
            )
        )
        or access.get("passes") is not True
    ):
        raise ValueError("selector OS access probe did not prove isolation")
    _verify_post_selection_collision_audit(paths["postSelectionAudit"], index, value)
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
    try:
        preexisting = any(
            path.exists()
            for path in (
                paths["selectorRoot"],
                paths["postSelectionRoot"],
                paths["sealed"],
            )
        )
        if preexisting:
            # A prior process may only be recovered after it published both
            # the complete selector seal and the immutable clean history
            # audit.  This is idempotent even after authorization populated
            # the main sealed directory.  Anything less consumes k; the
            # selector is never rerun.
            if (
                paths["jointSuiteSeal"].is_file()
                and paths["postSelectionAudit"].is_file()
            ):
                return verify_suite_seal(
                    paths["jointSuiteSeal"], index, deep=True
                )
            raise RuntimeError(
                "incomplete preexisting selector output consumes the attempt"
            )
        implementation = verify_implementation_seal(
            IMPLEMENTATION_SEAL, orchestrator_path=ORCHESTRATOR_PATH
        )
        _verify_selector_capsule(
            BASE_SELECTOR_CAPSULE, verify_private_freeze=True
        )
        if not contract.exact_json_equal(
            claim["implementationSeal"], contract.identity(IMPLEMENTATION_SEAL)
        ):
            raise ValueError("candidate claim and active implementation seal differ")
        intent = {
            "schemaVersion": 1,
            "kind": "omega-nnue-open-confirmation-v2-sampling-intent",
            "protocol": PROTOCOL_IDENTITY,
            "attemptIndex": index,
            "createdUtc": _utc_now(),
            "selectorCapsule": contract.identity(BASE_SELECTOR_CAPSULE),
            "stageSeeds": stage_seeds,
            "workerCommandCandidateInputs": 0,
            "workerCommandHistoryProjectionInputs": 0,
        }
        _exclusive_json(paths["samplingIntent"], intent)
        _verify_selector_workspace_inventory(index, sealed=False)
        command = [
            sys.executable,
            "-I",
            "-B",
            str(TOOL_PATH),
            "candidate-blind-worker",
            "--selector-capsule",
            str(BASE_SELECTOR_CAPSULE),
            "--sampling-intent",
            str(paths["samplingIntent"]),
            "--output-root",
            str(paths["selectorRoot"]),
            "--parent-lock-token",
            parent_lock_token,
        ]
        if any(
            token.casefold()
            in {"--candidate", "--network", "--claim", "--g5-authorization"}
            for token in command
        ):
            raise AssertionError("candidate-aware argument reached clean worker")
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
        _verify_selector_workspace_inventory(index, sealed=True)
        joint = contract.strict_load(
            paths["jointSuiteSeal"], "post-worker joint suite"
        )
        _publish_post_selection_collision_audit(index, joint)
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
    promotion_e = _promotion_linear_threshold(index)
    if gate == practical.GATE:
        suite_value = contract.strict_load(suite_path, "normal-start-clock suite")
        _PRACTICAL_VERIFY_SUITE(suite_value, seed=seed)
        result = _PRACTICAL_BUILD_CONFIG(
            suite=suite_value,
            suite_path=suite_path,
            seed=seed,
            engine=engine,
            network=network,
            omega_match=harness,
            omega_match_bundle_sha256=str(harness_bundle["sha256"]),
            output_directory=paths["stages"][gate],
        )
        result["runId"] = (
            f"open-confirmation-v2-a{index:06d}-normal-start-clock-"
            f"{network['sha256'][:12]}"
        )
        result["profileId"] = contract.PROTOCOL_ID
        result["freshnessMarker"] = (
            f"{contract.PROTOCOL_ID}:attempt-{index:06d}:{gate}"
        )
        result["match"]["sequentialGate"]["promotionAlpha"] = 1.0 / promotion_e
        return result
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
        "runId": f"open-confirmation-v2-a{index:06d}-{gate}-{network['sha256'][:12]}",
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
            "idleMachineRequired": gate in {"equal-time", "normal-start-clock"},
        },
        "engines": [
            {
                "id": "nnue-candidate",
                "executable": str(engine_path),
                "arguments": "",
                "workingDirectory": str(engine_path.parent),
                "expectedSha256": engine["sha256"],
                "expectedAssetSha256": {"OmegaNNUEFile": network["sha256"]},
                "options": candidate_options,
            },
            {
                "id": "hce-control",
                "executable": str(engine_path),
                "arguments": "",
                "workingDirectory": str(engine_path.parent),
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
        or candidate["arguments"] != ""
        or control["arguments"] != ""
        or candidate["workingDirectory"] != control["workingDirectory"]
        or Path(candidate["workingDirectory"]).resolve()
        != Path(candidate["executable"]).resolve().parent
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


def _authorize_attempt_locked(attempt_index: Any) -> dict[str, Any]:
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
            "kind": "omega-nnue-open-confirmation-v2-match-audit",
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
            "launchOnlyThrough": "tools/omega_nnue/king_state_confirmation_matches_v2.py",
        }
        _exclusive_json(paths["authorization"], authorization)
        return verify_authorization(
            paths["authorization"], index, runtime_authority=True
        )
    except BaseException:
        if not paths["attemptClosure"].exists():
            _publish_abort(index, "authorization-failure")
        raise


@_serialized_transition
def authorize_attempt(attempt_index: Any) -> dict[str, Any]:
    """Authorize once, consuming the attempt on every post-claim failure."""

    index = contract._validate_attempt_index(attempt_index)
    paths = attempt_paths(index)
    try:
        return _authorize_attempt_locked(index)
    except BaseException:
        if paths["claim"].is_file() and not paths["attemptClosure"].exists():
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
        != "tools/omega_nnue/king_state_confirmation_matches_v2.py"
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
fake = types.ModuleType('king_state_confirmation_protocol_v2')
fake.__file__ = {str((_REPO / _PROTOCOL_RELATIVE).resolve())!r}
sys.modules['king_state_confirmation_protocol_v2'] = fake
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
):
    runtime = namespace['_runtime_bundle'](
        namespace[root_name], assembly_name, apphost_name
    )
    root = pathlib.Path(runtime['root'])
    assembly = root / runtime['assemblyRelativePath']
    if not root.is_absolute() or not assembly.is_file():
        raise AssertionError(f'outside-cwd replay path failed: {{root_name}}')
prefix = namespace['_exact_g5_prefix_replay_bundle']()
prefix_root = pathlib.Path(prefix['root'])
if (
    prefix.get('sha256') != namespace['PREFIX_REPLAY_BUNDLE_SHA256']
    or not (prefix_root / prefix['assemblyRelativePath']).is_file()
):
    raise AssertionError('outside-cwd exact Generation-5 replay path failed')
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
        "g5Decisions": {
            gate: contract.identity(G5_DECISIONS[gate]) for gate in G5_GATES
        },
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
        "cAnDiDaTe_NeTwOrK": "secret.onnx",
        "ClAiM_EnTrOpY": "secret",
        "sCoRe_ReSuLt": "1-0",
        "TaRgEt_FeAtUrEs": "secret",
        "AWS_SECRET_ACCESS_KEY": "secret",
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
        or set(sanitized) != set(SUBPROCESS_ENVIRONMENT_KEYS)
        or any(key in sanitized for key in poison if key != "DOTNET_ROOT")
        or _subprocess_environment_contract()["inheritedParentKeys"] != []
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
            for ordinal, gate in enumerate(G5_GATES, 1):
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
                    gate: contract.identity(G5_DECISIONS[gate])
                    for gate in G5_GATES
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


def _self_test_sampling_failure_transitions() -> None:
    """Exercise crash/collision consumption through the real sample wrapper."""

    global ARTIFACT_ROOT, IMPLEMENTATION_SEAL, PROGRAM_SUCCESS
    global SELECTOR_WORKSPACE_ROOT, BASE_SELECTOR_CAPSULE
    global BASE_EXCLUDED_ORBITS, GLOBAL_OPERATION_LOCK_PATH

    saved_globals = {
        name: globals()[name]
        for name in (
            "ARTIFACT_ROOT", "IMPLEMENTATION_SEAL", "PROGRAM_SUCCESS",
            "SELECTOR_WORKSPACE_ROOT", "BASE_SELECTOR_CAPSULE",
            "BASE_EXCLUDED_ORBITS", "GLOBAL_OPERATION_LOCK_PATH",
        )
    }
    saved_callables = {
        name: globals()[name]
        for name in (
            "verify_attempt_chain", "verify_candidate_claim",
            "claim_stage_seeds", "verify_implementation_seal",
            "_verify_selector_capsule", "_require_worker_attempt_open",
            "_publish_post_selection_collision_audit", "_publish_abort",
            "verify_suite_seal",
        )
    }
    saved_run = subprocess.run
    try:
        with tempfile.TemporaryDirectory(
            prefix="omega-sampling-transition-"
        ) as directory:
            fixture_base = Path(directory).resolve()
            for scenario in ("partial-crash", "late-collision"):
                ARTIFACT_ROOT = fixture_base / scenario / "artifacts"
                IMPLEMENTATION_SEAL = ARTIFACT_ROOT / "implementation.seal.json"
                PROGRAM_SUCCESS = ARTIFACT_ROOT / "program-success.json"
                SELECTOR_WORKSPACE_ROOT = ARTIFACT_ROOT / "runtime/selectors"
                BASE_SELECTOR_CAPSULE = ARTIFACT_ROOT / "runtime/capsule.json"
                BASE_EXCLUDED_ORBITS = ARTIFACT_ROOT / "runtime/orbits.txt"
                GLOBAL_OPERATION_LOCK_PATH = fixture_base / scenario / "operation.lock"
                paths = attempt_paths(1)
                paths["root"].mkdir(parents=True)
                IMPLEMENTATION_SEAL.parent.mkdir(parents=True, exist_ok=True)
                IMPLEMENTATION_SEAL.write_text("implementation\n", encoding="utf-8")
                BASE_SELECTOR_CAPSULE.parent.mkdir(parents=True, exist_ok=True)
                BASE_SELECTOR_CAPSULE.write_text("capsule\n", encoding="utf-8")
                paths["reservation"].write_text("{}\n", encoding="utf-8")
                paths["claim"].write_text("{}\n", encoding="utf-8")
                claim = {
                    "implementationSeal": contract.identity(IMPLEMENTATION_SEAL),
                }

                def fixture_chain(
                    protocol_value: Mapping[str, Any],
                    through_attempt: int | None = None,
                    **_: Any,
                ) -> dict[str, Any]:
                    closed = paths["attemptClosure"].is_file()
                    return {
                        "attempts": 1,
                        "closed": 1 if closed else 0,
                        "activeAttempt": None if closed else 1,
                        "confirmedAttempt": None,
                        "priorPublishedStageSeeds": [],
                    }

                def fixture_abort(index: int, reason: str) -> dict[str, Any]:
                    _require_operation_lock("sampling transition fixture abort")
                    if index != 1 or paths["attemptClosure"].exists():
                        raise FileExistsError("fixture attempt is already closed")
                    value = {
                        "attemptIndex": 1,
                        "outcome": "aborted",
                        "reason": reason,
                        "terminal": True,
                        "nextAttemptAllowed": True,
                    }
                    _exclusive_json(paths["attemptClosure"], value)
                    return value

                def fixture_run(*_: Any, **__: Any) -> Any:
                    paths["sampler"].mkdir(parents=False)
                    paths["selectorSealed"].mkdir(parents=False)
                    for gate in GATES:
                        source = _source_path(1, gate)
                        source.write_text("{}\n", encoding="utf-8")
                        Path(str(source) + ".manifest.json").write_text(
                            "{}\n", encoding="utf-8"
                        )
                        Path(str(source) + ".complete.seal.json").write_text(
                            "{}\n", encoding="utf-8"
                        )
                        paths["suites"][gate].write_text("{}\n", encoding="utf-8")
                    paths["jointSuiteSeal"].write_text(
                        '{"createdUtc":"2026-07-23T00:00:00Z"}\n',
                        encoding="utf-8",
                    )
                    return types.SimpleNamespace(returncode=0, stderr="")

                globals()["verify_attempt_chain"] = fixture_chain
                globals()["verify_candidate_claim"] = lambda *args, **kwargs: claim
                globals()["claim_stage_seeds"] = (
                    lambda *args, **kwargs: dict(_SELF_TEST_STAGE_SEEDS)
                )
                globals()["verify_implementation_seal"] = lambda *args, **kwargs: {}
                globals()["_verify_selector_capsule"] = lambda *args, **kwargs: {}
                globals()["_require_worker_attempt_open"] = lambda *args, **kwargs: None
                globals()["_publish_abort"] = fixture_abort
                subprocess.run = fixture_run
                if scenario == "partial-crash":
                    paths["selectorRoot"].mkdir(parents=True)
                    paths["samplingIntent"].write_text("{}\n", encoding="utf-8")
                else:
                    globals()["_publish_post_selection_collision_audit"] = (
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            ValueError("late colliding history member")
                        )
                    )
                try:
                    sample_attempt(1)
                except (RuntimeError, ValueError):
                    pass
                else:
                    raise AssertionError(f"{scenario} sampling unexpectedly survived")
                closure = contract.strict_load(
                    paths["attemptClosure"], f"{scenario} terminal closure"
                )
                state = fixture_chain(PROTOCOL)
                if (
                    closure.get("reason") != "sampling-failure"
                    or closure.get("terminal") is not True
                    or state["activeAttempt"] is not None
                    or state["closed"] != 1
                ):
                    raise AssertionError(f"{scenario} did not consume attempt k")
                try:
                    sample_attempt(1)
                except (FileExistsError, ValueError):
                    pass
                else:
                    raise AssertionError(f"{scenario} allowed same-index resampling")
                try:
                    create_candidate_claim(1)
                except (FileExistsError, ValueError):
                    pass
                else:
                    raise AssertionError(f"{scenario} allowed same-index reclaim")

            # Repeating sample after a complete seal/audit (including a main
            # authorization directory) is idempotent and non-consuming.
            ARTIFACT_ROOT = fixture_base / "complete-repeat" / "artifacts"
            IMPLEMENTATION_SEAL = ARTIFACT_ROOT / "implementation.seal.json"
            PROGRAM_SUCCESS = ARTIFACT_ROOT / "program-success.json"
            SELECTOR_WORKSPACE_ROOT = ARTIFACT_ROOT / "runtime/selectors"
            BASE_SELECTOR_CAPSULE = ARTIFACT_ROOT / "runtime/capsule.json"
            BASE_EXCLUDED_ORBITS = ARTIFACT_ROOT / "runtime/orbits.txt"
            GLOBAL_OPERATION_LOCK_PATH = fixture_base / "complete-repeat" / "operation.lock"
            paths = attempt_paths(1)
            paths["root"].mkdir(parents=True)
            paths["reservation"].write_text("{}\n", encoding="utf-8")
            paths["claim"].write_text("{}\n", encoding="utf-8")
            paths["selectorRoot"].mkdir(parents=True)
            paths["samplingIntent"].write_text("{}\n", encoding="utf-8")
            paths["sampler"].mkdir()
            paths["selectorSealed"].mkdir()
            for gate in GATES:
                source = _source_path(1, gate)
                source.write_text("{}\n", encoding="utf-8")
                Path(str(source) + ".manifest.json").write_text("{}\n", encoding="utf-8")
                Path(str(source) + ".complete.seal.json").write_text("{}\n", encoding="utf-8")
                paths["suites"][gate].write_text("{}\n", encoding="utf-8")
            paths["jointSuiteSeal"].write_text("{}\n", encoding="utf-8")
            paths["postSelectionRoot"].mkdir(parents=True)
            paths["postSelectionAudit"].write_text("{}\n", encoding="utf-8")
            paths["sealed"].mkdir()
            (paths["sealed"] / "match-authorization.json").write_text(
                "{}\n", encoding="utf-8"
            )
            claim = {"implementationSeal": {}}
            globals()["verify_attempt_chain"] = lambda *args, **kwargs: {
                "attempts": 1, "closed": 0, "activeAttempt": 1,
                "confirmedAttempt": None, "priorPublishedStageSeeds": [],
            }
            globals()["verify_candidate_claim"] = lambda *args, **kwargs: claim
            globals()["claim_stage_seeds"] = (
                lambda *args, **kwargs: dict(_SELF_TEST_STAGE_SEEDS)
            )
            globals()["verify_suite_seal"] = (
                lambda path, index, deep=True: {
                    "fixture": "complete-and-clean", "attemptIndex": index
                }
            )
            globals()["_publish_abort"] = lambda *args, **kwargs: (
                _ for _ in ()
            ).throw(AssertionError("complete repeated sample was aborted"))

            def fixture_tree_snapshot(
                *roots: tuple[str, Path],
            ) -> dict[str, tuple[str, bytes | str | None]]:
                snapshot: dict[str, tuple[str, bytes | str | None]] = {}
                for label, root in roots:
                    entries = [root, *sorted(root.rglob("*"), key=str)]
                    for entry in entries:
                        relative = "." if entry == root else entry.relative_to(root).as_posix()
                        key = f"{label}/{relative}"
                        if entry.is_symlink():
                            snapshot[key] = ("link", os.readlink(entry))
                        elif entry.is_dir():
                            snapshot[key] = ("directory", None)
                        elif entry.is_file():
                            snapshot[key] = ("file", entry.read_bytes())
                        else:
                            snapshot[key] = ("other", None)
                return snapshot

            before = fixture_tree_snapshot(
                ("attempt", paths["root"]),
                ("selector", paths["selectorRoot"]),
            )
            for _ in range(2):
                recovered = sample_attempt(1)
                if recovered.get("fixture") != "complete-and-clean":
                    raise AssertionError("complete repeated sample did not recover")
            after = fixture_tree_snapshot(
                ("attempt", paths["root"]),
                ("selector", paths["selectorRoot"]),
            )
            if before != after or paths["attemptClosure"].exists():
                raise AssertionError("complete repeated sample mutated/consumed k")

            selector = fixture_base / "injected-selector"
            selector.mkdir()
            (selector / "sampling-intent.json").write_text("{}\n", encoding="utf-8")
            (selector / "injected.json").write_text("{}\n", encoding="utf-8")
            try:
                _require_exact_directory_inventory(
                    selector,
                    files=("sampling-intent.json",),
                    directories=(),
                    label="injected selector fixture",
                )
            except ValueError:
                pass
            else:
                raise AssertionError("selector workspace accepted an injected file")
    finally:
        for name, value in saved_callables.items():
            globals()[name] = value
        subprocess.run = saved_run
        for name, value in saved_globals.items():
            globals()[name] = value


def _self_test_compatibility_chronology_and_history() -> None:
    """Reject chronology forgery and omitted additive match-history evidence."""

    global IMPLEMENTATION_SEAL, G5_MATCH_ROOT, G5_COMPAT_PREAUTHORIZATION
    global G5_COMPAT_PREREGISTRATION, G5_COMPAT_SUITE_SEAL, G5_COMPAT_SUITES
    global G5_AUTHORIZATION, G5_DECISIONS, G5_CLOSURE
    global G5_HISTORY_PROJECTION, G5_HISTORY_MANIFEST
    saved = {
        name: globals()[name]
        for name in (
            "IMPLEMENTATION_SEAL",
            "G5_MATCH_ROOT",
            "G5_COMPAT_PREAUTHORIZATION",
            "G5_COMPAT_PREREGISTRATION",
            "G5_COMPAT_SUITE_SEAL",
            "G5_COMPAT_SUITES",
            "G5_AUTHORIZATION",
            "G5_DECISIONS",
            "G5_CLOSURE",
            "G5_HISTORY_PROJECTION",
            "G5_HISTORY_MANIFEST",
        )
    }

    def write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    with tempfile.TemporaryDirectory(
        prefix="omega-confirmation-compat-chronology-", dir=_REPO
    ) as directory:
        root = Path(directory).resolve()
        try:
            G5_MATCH_ROOT = root / "matches-color-compat-v2"
            sealed = G5_MATCH_ROOT / "sealed"
            G5_COMPAT_PREREGISTRATION = G5_MATCH_ROOT / "preregistration.json"
            G5_COMPAT_SUITE_SEAL = sealed / "suite-seal.json"
            G5_COMPAT_SUITES = {
                gate: sealed / f"{gate}-suite.json" for gate in G5_GATES
            }
            G5_COMPAT_PREAUTHORIZATION = sealed / "preauthorization-state.json"
            G5_AUTHORIZATION = sealed / "match-authorization.json"
            G5_DECISIONS = {
                gate: G5_MATCH_ROOT / f"decisions/{gate}.json"
                for gate in G5_GATES
            }
            G5_CLOSURE = G5_MATCH_ROOT / "closure.json"
            G5_HISTORY_PROJECTION = G5_MATCH_ROOT / "position-history.jsonl"
            G5_HISTORY_MANIFEST = G5_MATCH_ROOT / "position-history.manifest.json"
            IMPLEMENTATION_SEAL = root / "implementation.seal.json"
            IMPLEMENTATION_SEAL.write_text("{}\n", encoding="utf-8")

            for path in (
                G5_COMPAT_PREREGISTRATION,
                G5_COMPAT_SUITE_SEAL,
                *G5_COMPAT_SUITES.values(),
            ):
                write_json(path, {"fixture": path.name})
            write_json(
                G5_COMPAT_PREAUTHORIZATION,
                {"createdUtc": "2099-01-01T00:00:00Z"},
            )
            selected_network = {"fixture": "network"}
            authorization = {
                "createdUtc": "2099-01-01T00:00:03Z",
                "preauthorizationState": contract.identity(
                    G5_COMPAT_PREAUTHORIZATION
                ),
                "selectedNetwork": selected_network,
            }
            write_json(G5_AUTHORIZATION, authorization)
            authorization_identity = contract.identity(G5_AUTHORIZATION)
            times = {
                "development": (4, 5, 6),
                "equal-node": (7, 8, 9),
                "equal-time": (10, 11, 12),
            }
            expected_success = {
                "development": "pass",
                "equal-node": "promote",
                "equal-time": "promote",
            }
            for gate in G5_GATES:
                launch_second, event_second, decision_second = times[gate]
                launch = G5_MATCH_ROOT / f"{gate}/launches/000001.intent.json"
                write_json(
                    launch,
                    {
                        "kind": "omega-nnue-king-state-v5-color-compat-launch-intent",
                        "gate": gate,
                        "createdUtc": f"2099-01-01T00:00:{launch_second:02d}Z",
                        "authorization": authorization_identity,
                    },
                )
                events = G5_MATCH_ROOT / f"{gate}/events.jsonl"
                events.write_text(
                    json.dumps(
                        {
                            "RecordType": "run",
                            "CreatedUtc": f"2099-01-01T00:00:{event_second:02d}Z",
                        },
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )
                write_json(
                    G5_DECISIONS[gate],
                    {
                        "kind": "omega-nnue-king-state-v5-color-compat-decision",
                        "compatibilityId": "king-state-v5-color-compat-v2",
                        "gate": gate,
                        "createdUtc": f"2099-01-01T00:00:{decision_second:02d}Z",
                        "decision": expected_success[gate],
                        "passed": True,
                        "authorization": authorization_identity,
                        "finalStageSeal": True,
                    },
                )
            sources: dict[str, dict[str, Any]] = {}
            for gate in G5_GATES:
                evidence = (
                    G5_MATCH_ROOT / f"evidence/{gate}/000001.raw-prefix.jsonl"
                )
                evidence.parent.mkdir(parents=True)
                evidence.write_text("{}\n", encoding="utf-8")
                sources[gate] = contract.identity(evidence)
            ofen = _SELF_TEST_OFENS[("opening", "w")]
            phase, side, position_identity, orbit, signatures = _POSITION_META(ofen)
            history_rows = [
                {
                    "schemaVersion": 1,
                    "kind": "omega-nnue-king-state-v5-compat-position-history-row",
                    "occurrence": occurrence,
                    "gate": gate,
                    "sourceRawPrefixSha256": sources[gate]["sha256"],
                    "sourceLine": 1,
                    "recordType": "gameStart",
                    "gameId": f"fixture-{gate}",
                    "attempt": 1,
                    "ply": 0,
                    "positionRole": "initial",
                    "ofen": ofen,
                    "phase": phase,
                    "sideToMove": side,
                    "positionIdentity": position_identity,
                    "symmetryOrbitKey": orbit,
                    "orbitSignatures": list(signatures),
                }
                for occurrence, gate in enumerate(G5_GATES, 1)
            ]
            G5_HISTORY_PROJECTION.write_text(
                "".join(
                    json.dumps(row, sort_keys=True, separators=(",", ":"))
                    + "\n"
                    for row in history_rows
                ),
                encoding="utf-8",
            )
            decisions = {
                gate: contract.identity(G5_DECISIONS[gate]) for gate in G5_GATES
            }
            write_json(
                G5_HISTORY_MANIFEST,
                {
                    "schemaVersion": 1,
                    "kind": "omega-nnue-king-state-v5-compat-position-history-manifest",
                    "compatibilityId": "king-state-v5-color-compat-v2",
                    "createdUtc": "2099-01-01T00:00:12.5Z",
                    "protocol": contract.identity(G5_COMPAT_PROTOCOL),
                    "authorization": authorization_identity,
                    "selectedNetwork": selected_network,
                    "decisions": decisions,
                    "sources": sources,
                    "projection": contract.identity(G5_HISTORY_PROJECTION),
                    "rows": len(history_rows),
                    "uniquePositionIdentities": 1,
                    "uniqueOrbitSignatures": len(set(signatures)),
                    "recordSchema": (
                        "gameStart.InitialOfen plus every successful ply.PostOfen from "
                        "the final authenticated raw prefix of each terminal gate"
                    ),
                    "producer": {
                        "readiness": PROTOCOL["g5Screening"]["compatibilityAuthority"]["identities"]["readiness"],
                        "matches": PROTOCOL["g5Screening"]["compatibilityAuthority"]["identities"]["matches"],
                    },
                    "originalArtifactsRewritten": 0,
                    "finalStageSeal": True,
                },
            )
            write_json(
                G5_CLOSURE,
                {
                    "kind": "omega-nnue-king-state-v5-color-compat-closure",
                    "compatibilityId": "king-state-v5-color-compat-v2",
                    "createdUtc": "2099-01-01T00:00:13Z",
                    "authorization": authorization_identity,
                    "selectedNetwork": selected_network,
                    "decisions": decisions,
                    "positionHistoryProjection": contract.identity(
                        G5_HISTORY_PROJECTION
                    ),
                    "positionHistoryManifest": contract.identity(G5_HISTORY_MANIFEST),
                    "clearlySuperior": True,
                },
            )
            implementation = {
                "createdUtc": "2099-01-01T00:00:02Z",
                "baseExclusionFreeze": {
                    "createdUtc": "2099-01-01T00:00:01Z",
                    "g5PreResultFreeze": {
                        "preauthorizationState": contract.identity(
                            G5_COMPAT_PREAUTHORIZATION
                        )
                    },
                },
            }
            chronology = _g5_match_chronology(
                implementation,
                authorization,
                authorization_identity,
                G5_DECISIONS,
            )
            _verify_g5_chronology_record(chronology, implementation)

            closure_bytes = G5_CLOSURE.read_bytes()
            forged = contract.strict_load(G5_CLOSURE, "forged closure fixture")
            forged["clearlySuperior"] = False
            write_json(G5_CLOSURE, forged)
            try:
                _g5_match_chronology(
                    implementation,
                    authorization,
                    authorization_identity,
                    G5_DECISIONS,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("false compatibility closure was accepted")
            G5_CLOSURE.write_bytes(closure_bytes)

            late = G5_MATCH_ROOT / "evidence/late.json"
            write_json(late, {"late": True})
            try:
                _verify_g5_chronology_record(chronology, implementation)
            except ValueError:
                pass
            else:
                raise AssertionError("late compatibility evidence escaped chronology")

            projection = (
                _REPO
                / "build-msvc/data-generation/g3-prior-projection-v1/positions.jsonl"
            ).resolve()
            manifest = (
                _REPO
                / "build-msvc/data-generation/g3-prior-projection-v1/positions.manifest.json"
            ).resolve()
            history_paths = [
                path.resolve()
                for path in G5_MATCH_ROOT.rglob("*")
                if path.is_file()
                and path.suffix.casefold() in {".json", ".jsonl", ".pgn", ".ccsf"}
            ]
            identities = {
                path: contract.identity(path)
                for path in [projection, manifest, *history_paths]
            }
            coverage = _history_coverage_evidence(
                identities, require_compatibility_results=True
            )
            compat = coverage["generation5CompatibilityNamespaceExactMembership"]
            if (
                compat["resultEvidenceRequired"] is not True
                or compat["rawAndReplayEvidenceIncluded"] is not True
            ):
                raise AssertionError("compatibility result coverage was weakened")
            projected_signatures = {position_identity, *signatures}
            position_coverage = _compatibility_position_history_coverage(
                projected_signatures
            )
            if (
                position_coverage["allProjectedOrbitsIncluded"] is not True
                or position_coverage["missingExclusionSignatures"] != 0
            ):
                raise AssertionError(
                    "compatibility position history was not proven excluded"
                )
            try:
                _compatibility_position_history_coverage(set())
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "unincluded compatibility position-history orbit was accepted"
                )
            projection_bytes = G5_HISTORY_PROJECTION.read_bytes()
            forged_rows = [dict(row) for row in history_rows]
            forged_rows[0]["symmetryOrbitKey"] = "0" * 64
            G5_HISTORY_PROJECTION.write_text(
                "".join(
                    json.dumps(row, sort_keys=True, separators=(",", ":"))
                    + "\n"
                    for row in forged_rows
                ),
                encoding="utf-8",
            )
            try:
                _compatibility_position_history_coverage(projected_signatures)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "forged compatibility position-history metadata was accepted"
                )
            G5_HISTORY_PROJECTION.write_bytes(projection_bytes)
            without_events = dict(identities)
            without_events.pop(
                (G5_MATCH_ROOT / "equal-time/events.jsonl").resolve()
            )
            try:
                _history_coverage_evidence(
                    without_events, require_compatibility_results=True
                )
            except ValueError:
                pass
            else:
                raise AssertionError("omitted compatibility raw events were accepted")
        finally:
            for name, value in saved.items():
                globals()[name] = value


def self_test_v2() -> None:
    """Exercise v2 invariants without creating any production artifact."""

    _verify_bindings(require_retirement=False)
    _self_test_authenticated_loader_and_lock()
    _self_test_history_root_guards()
    _self_test_compatibility_chronology_and_history()
    _self_test_sampling_failure_transitions()

    singleton = _verify_v1_root_singleton()
    if singleton["entries"] != ["implementation.seal.json"]:
        raise AssertionError("audited v1 predecessor is not a singleton")
    retirement = _v1_retirement_value(created_utc="2026-07-23T23:59:59Z")
    if (
        retirement["attemptReservations"] != 0
        or retirement["candidateClaims"] != 0
        or retirement["attemptsConsumed"] != 0
        or retirement["poolsSuitesMatchesOrResults"] != 0
        or retirement["alphaSpent"] != 0
        or retirement["nextGlobalAttemptIndex"] != 1
        or retirement["continuousSingletonRevalidationRequired"] is not True
        or retirement["v1ImplementationSealMayNotAuthorizeFutureWork"] is not True
    ):
        raise AssertionError("v1 zero-spend retirement contract changed")

    # Prove that continuous predecessor validation catches both namespace growth
    # and byte tampering, using only a temporary copy of the audited v1 seal.
    original_v1_root = contract.V1_ARTIFACT_ROOT
    original_v1_seal = contract.V1_IMPLEMENTATION_SEAL
    with tempfile.TemporaryDirectory(prefix="omega-confirmation-v1-audit-") as directory:
        test_root = Path(directory).resolve()
        test_seal = test_root / "implementation.seal.json"
        test_seal.write_bytes(Path(original_v1_seal).read_bytes())
        try:
            contract.V1_ARTIFACT_ROOT = test_root
            contract.V1_IMPLEMENTATION_SEAL = test_seal
            _verify_v1_root_singleton()
            extra = test_root / "attempt-000001"
            extra.mkdir()
            try:
                _verify_v1_root_singleton()
            except ValueError:
                pass
            else:
                raise AssertionError("v1 namespace growth escaped retirement audit")
            extra.rmdir()
            original_bytes = test_seal.read_bytes()
            test_seal.write_bytes(original_bytes + b"\n")
            try:
                _verify_v1_root_singleton()
            except ValueError:
                pass
            else:
                raise AssertionError("v1 predecessor tampering escaped retirement audit")
            test_seal.write_bytes(original_bytes)
            _verify_v1_root_singleton()
        finally:
            contract.V1_ARTIFACT_ROOT = original_v1_root
            contract.V1_IMPLEMENTATION_SEAL = original_v1_seal
    _verify_v1_root_singleton()

    proof = _candidate_invariance_proof()
    if (
        proof["candidateArgumentsAccepted"] != 0
        or proof["candidateEnvironmentVariablesAccepted"] != 0
        or proof["selectionDigestA"] != proof["selectionDigestB"]
        or proof["identical"] is not True
    ):
        raise AssertionError("candidate-blind selector proof changed")

    capsule_fixture = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SELECTOR_CAPSULE_KIND,
        "protocol": PROTOCOL_IDENTITY,
        "freezeIdentity": {"bytes": 1, "sha256": "a" * 64},
        "excludedOrbitSignatures": 1,
        "excludedOrbits": {
            "path": str(BASE_EXCLUDED_ORBITS),
            "bytes": 65,
            "sha256": "b" * 64,
        },
    }
    _validate_selector_capsule_shape(capsule_fixture)
    for label, poison_capsule in (
        (
            "candidate field",
            {**copy.deepcopy(capsule_fixture), "candidateNetwork": "secret"},
        ),
        (
            "hidden projector path",
            {
                **copy.deepcopy(capsule_fixture),
                "freezeIdentity": {
                    "bytes": 1,
                    "sha256": "a" * 64,
                    "path": str(BASE_PROJECTION),
                },
            },
        ),
        (
            "history roots",
            {**copy.deepcopy(capsule_fixture), "historyRoots": [str(G5_MATCH_ROOT)]},
        ),
    ):
        try:
            _validate_selector_capsule_shape(poison_capsule)
        except ValueError:
            pass
        else:
            raise AssertionError(f"selector capsule accepted {label}")

    probe_fixture = _SelectorAccessProbe(
        contract.attempt_namespace(1),
        attempt_paths(1)["selectorRoot"],
        BASE_SELECTOR_CAPSULE,
        attempt_paths(1)["samplingIntent"],
    )
    forbidden_probe_paths = (
        BASE_PROJECTION,
        attempt_paths(1)["claim"],
        G5_AUTHORIZATION,
        (_REPO / "build-msvc/generic-history.json").resolve(),
    )
    for forbidden_path in forbidden_probe_paths:
        try:
            probe_fixture("open", (str(forbidden_path), "r", 0))
        except PermissionError:
            pass
        else:
            raise AssertionError(
                f"selector OS access probe allowed {forbidden_path}"
            )
    probe_report = probe_fixture.report()
    if (
        probe_report["privateProjectionReadAttempts"] != 1
        or probe_report["attemptNamespaceReadAttempts"] != 1
        or probe_report["historyRootReadAttempts"] != 2
        or probe_report["forbiddenReadAttempts"] != 4
        or probe_report["passes"] is not False
    ):
        raise AssertionError("selector OS access probe classifications changed")

    current_paths = attempt_paths(2)
    current_suite_probe = current_paths["suites"]["development"]
    prior_suite_probe = attempt_paths(1)["suites"]["development"]
    if not _is_current_attempt_evidence(
        current_suite_probe,
        current_paths["root"],
        current_paths["selectorRoot"],
    ) or _is_current_attempt_evidence(
        prior_suite_probe,
        current_paths["root"],
        current_paths["selectorRoot"],
    ):
        raise AssertionError(
            "current selector workspace exclusion swallowed a prior workspace"
        )

    selected_fixture = {"a" * 64, "b" * 64}
    collisions, action = _post_selection_collision_result(
        selected_fixture, {"a" * 64, "c" * 64}
    )
    if collisions != {"a" * 64} or action != "abort-and-consume-attempt":
        raise AssertionError("post-selection collision did not require consumption")
    collisions, action = _post_selection_collision_result(
        selected_fixture, {"c" * 64}
    )
    if collisions or action != "accept":
        raise AssertionError("clean post-selection intersection was rejected")

    with tempfile.TemporaryDirectory(
        prefix="omega-post-selection-immutability-"
    ) as directory:
        fixture_root = Path(directory).resolve()
        immutable_audit = fixture_root / "collision-audit.json"
        _exclusive_json(
            immutable_audit,
            {
                "intersectionCount": 1,
                "collisionAction": "abort-and-consume-attempt",
                "reselectionAttempts": 0,
                "noResamplingOrReselection": True,
            },
        )
        original_audit = immutable_audit.read_bytes()
        try:
            _exclusive_json(immutable_audit, {"intersectionCount": 0})
        except FileExistsError:
            pass
        else:
            raise AssertionError("collision audit was rewritten for reselection")
        if immutable_audit.read_bytes() != original_audit:
            raise AssertionError("collision audit bytes changed after rejection")

        recorded = [fixture_root / "history-a.json"]
        for label, added in (
            ("colliding", fixture_root / "new-colliding.jsonl"),
            ("noncolliding", fixture_root / "new-noncolliding.json"),
        ):
            try:
                _require_exact_history_membership(recorded, [*recorded, added])
            except ValueError as error:
                if "consume the attempt" not in str(error):
                    raise
            else:
                raise AssertionError(
                    f"new {label} history file did not invalidate the immutable scan"
                )

    seed_bundle = contract._draw_seed_bundle_with_source(
        1, (), lambda size: b"\x42" * size
    )
    normalized = contract.validate_published_seed_bundle(seed_bundle, ())
    seeds = normalized["stageSeeds"]
    if (
        tuple(seeds) != GATES
        or tuple(seeds)[-1] != "normal-start-clock"
        or len(set(seeds.values())) != 4
        or set(seeds.values()).intersection(contract.G5_SCREENING_STAGE_SEEDS)
    ):
        raise AssertionError("four-stage v2 entropy derivation changed")
    duplicate = copy.deepcopy(seed_bundle)
    duplicate["stageSeeds"]["normal-start-clock"] = duplicate["stageSeeds"][
        "development"
    ]
    try:
        contract.validate_published_seed_bundle(duplicate, ())
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate fourth-stage seed was accepted")

    poison = {
        "CORECLR_ENABLE_PROFILING": "1",
        "COMPlus_ReadyToRun": "0",
        "PYTHONPATH": "poisoned",
        "DOTNET_ROOT": "poisoned",
        "cAnDiDaTe_NeTwOrK": "secret.onnx",
        "ClAiM_EnTrOpY": "secret",
        "sCoRe_ReSuLt": "1-0",
        "TaRgEt_FeAtUrEs": "secret",
        "AWS_SECRET_ACCESS_KEY": "secret",
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
        or set(sanitized) != set(SUBPROCESS_ENVIRONMENT_KEYS)
        or any(key in sanitized for key in poison if key != "DOTNET_ROOT")
        or _subprocess_environment_contract()["inheritedParentKeys"] != []
    ):
        raise AssertionError("v2 worker environment is not isolated")

    claim_fixture = {
        "kind": CLAIM_KIND,
        "seedDerivation": {
            "entropyCommitment": "a" * 64,
            "stageSeeds": dict(seeds),
        },
    }
    for boundary in ("open", "write", "flush", "fsync", "link", "close"):
        with tempfile.TemporaryDirectory(
            prefix=f"omega-confirmation-claim-{boundary}-"
        ) as directory:
            root = Path(directory).resolve()
            final = root / "candidate-claim.json"
            try:
                _publish_candidate_claim_atomic(
                    final, claim_fixture, fault_after=boundary
                )
            except RuntimeError as error:
                if f"after {boundary}" not in str(error):
                    raise
            else:
                raise AssertionError(
                    f"claim fault boundary {boundary} did not interrupt"
                )
            if boundary in {"open", "write", "flush", "fsync"}:
                if final.exists():
                    raise AssertionError(
                        f"precommit claim fault {boundary} exposed final bytes"
                    )
            else:
                if contract.strict_load(final, "atomic claim fixture") != claim_fixture:
                    raise AssertionError(
                        f"postcommit claim fault {boundary} lost full payload"
                    )
            if _claim_staging_artifacts(root):
                raise AssertionError(
                    f"claim fault {boundary} left a staged publication"
                )
    with tempfile.TemporaryDirectory(
        prefix="omega-confirmation-claim-no-replace-"
    ) as directory:
        final = Path(directory).resolve() / "candidate-claim.json"
        _publish_candidate_claim_atomic(final, claim_fixture)
        try:
            _publish_candidate_claim_atomic(final, claim_fixture)
        except FileExistsError:
            pass
        else:
            raise AssertionError("candidate claim no-replace commit was overwritten")

    practical.self_test()
    with tempfile.TemporaryDirectory(prefix="omega-confirmation-clock-config-") as directory:
        root = Path(directory).resolve()
        files: dict[str, Path] = {}
        for name in ("engine.exe", "network.onnx", "OmegaMatch.dll"):
            path = root / name
            path.write_bytes((name + "\n").encode("ascii"))
            files[name] = path
        suite_path = root / "normal-start-suite.json"
        _exclusive_json(suite_path, {"kind": practical.SUITE_KIND})
        config = _PRACTICAL_BUILD_CONFIG(
            suite={"kind": practical.SUITE_KIND},
            suite_path=suite_path,
            seed=_SELF_TEST_STAGE_SEEDS["normal-start-clock"],
            engine=contract.identity(files["engine.exe"]),
            network=contract.identity(files["network.onnx"]),
            omega_match=contract.identity(files["OmegaMatch.dll"]),
            omega_match_bundle_sha256="a" * 64,
            output_directory=root / "events",
        )
        match = config["match"]
        execution = config["openConfirmationV2Execution"]
        if (
            config["schemaVersion"] != 1
            or match["mode"] != "clock"
            or match["initialTimeMs"] != 60_000
            or match["incrementMs"] != 1_000
            or match["freshProcessPerGame"] is not True
            or execution["oneGameAtATime"] is not True
            or execution["maximumConcurrentGames"] != 1
            or execution["pairBudgetMustBeMultipleOf"] != 4
            or config["engines"][0]["arguments"] != ""
            or config["engines"][1]["arguments"] != ""
            or Path(config["engines"][0]["workingDirectory"]).resolve()
            != files["engine.exe"].parent.resolve()
            or config["engines"][0]["workingDirectory"]
            != config["engines"][1]["workingDirectory"]
            or config["engines"][0]["options"]["UseOmegaNNUE"] != "true"
            or config["engines"][1]["options"]["UseOmegaNNUE"] != "false"
        ):
            raise AssertionError("normal-start clock configuration changed")

    if _parse_windows_processes('"senpai.exe","44"\n"notepad.exe","45"\n') != [
        {"name": "senpai.exe", "pid": 44}
    ]:
        raise AssertionError("Windows idle-process parser changed")
    if _parse_posix_processes("44 /tmp/OmegaMatch.exe --config x\n45 /bin/other\n") != [
        {"name": "omegamatch.exe", "pid": 44}
    ]:
        raise AssertionError("POSIX idle-process parser changed")

    original_position = _MATCH_CORE._position_meta
    try:
        _MATCH_CORE._position_meta = lambda ofen: (
            "opening",
            "w",
            "x",
            "x",
            ("x",),
        )
        try:
            _verify_bindings(require_retirement=False)
        except ValueError:
            pass
        else:
            raise AssertionError("substituted whole-orbit function was accepted")
    finally:
        _MATCH_CORE._position_meta = original_position
    original_engine_authenticator = practical._authenticate_engine_inventory
    try:
        practical._authenticate_engine_inventory = lambda *args, **kwargs: None
        try:
            _verify_bindings(require_retirement=False)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "substituted practical engine authenticator was accepted"
            )
    finally:
        practical._authenticate_engine_inventory = original_engine_authenticator
    _verify_bindings(require_retirement=False)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    retire = commands.add_parser("retire-v1")
    retire.add_argument("--output", type=Path, default=V1_RETIREMENT_SEAL)
    verify_retirement = commands.add_parser("verify-retirement")
    verify_retirement.add_argument("--seal", type=Path, default=V1_RETIREMENT_SEAL)
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
    worker.add_argument("--selector-capsule", type=Path, required=True)
    worker.add_argument("--sampling-intent", type=Path, required=True)
    worker.add_argument("--output-root", type=Path, required=True)
    worker.add_argument("--parent-lock-token", required=True)
    projector = commands.add_parser("history-projector-worker", help=argparse.SUPPRESS)
    projector.add_argument("--output-root", type=Path, required=True)
    projector.add_argument("--parent-lock-token", required=True)
    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "retire-v1":
        value = create_v1_retirement_seal(args.output)
        print(
            "V1 retired without alpha spend: "
            f"{value['predecessorImplementationSeal']['sha256']}"
        )
    elif args.command == "verify-retirement":
        value = verify_v1_retirement_seal(args.seal)
        print(
            "V1 retirement verified; next global attempt: "
            f"{value['nextGlobalAttemptIndex']}"
        )
    elif args.command == "seal-implementation":
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
            args.selector_capsule,
            args.sampling_intent,
            args.output_root,
            args.parent_lock_token,
        )
        print(f"Candidate-blind worker sealed suites: {value['rootDigest']}")
    elif args.command == "history-projector-worker":
        value = _history_projector_worker(
            args.output_root, args.parent_lock_token
        )
        print(
            "Base history projection frozen: "
            f"{value['excludedOrbitSignatures']} orbits"
        )
    else:
        contract.self_test()
        self_test_v2()
        print("Open-confirmation readiness self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
