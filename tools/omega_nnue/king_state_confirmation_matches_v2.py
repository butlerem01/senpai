#!/usr/bin/env python3
"""Strict, append-only orchestrator for open Omega-NNUE confirmation.

The Generation-5 match program nominates candidates; it is not evidence for a
project-level superiority claim.  This program exercises a nominated candidate
on a separately sealed, globally alpha-spent confirmation attempt.  Every
attempt is consumed, every launch is represented by an immutable
intent/completion/assessment chain, and only a development pass followed by
formal equal-node, equal-time, *and* normal-start-clock promotions can publish
program success.

The frozen protocol authenticates and loads the shared match core in a fresh
module.  This orchestrator deliberately imports no Generation-5 adapter and
changes only the small set of core constants needed by the confirmation
profile.  In particular, the shared ``_sequential_gate`` implementation is
exercised unchanged with the attempt-specific conservative threshold.
"""

from __future__ import annotations

import argparse
import copy
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import types
from typing import Any, Mapping, Sequence


# Execute both confirmation dependencies only from authenticated source bytes.
# Rejecting even a same-path preloaded module closes the normal import-cache
# substitution route before any dependency code or callable is trusted.
_PREAUTH_REPO = Path(__file__).resolve().parents[2]
_PROTOCOL_RELATIVE = "tools/omega_nnue/king_state_confirmation_protocol_v2.py"
_PROTOCOL_SIZE = 126_759
_PROTOCOL_SHA256 = "5509ee3a7525e5bef28f79be2930ae6a9b05197fe3cfbd319c143af3f0439a79"
_READINESS_RELATIVE = "tools/omega_nnue/king_state_confirmation_readiness_v2.py"
_READINESS_SIZE = 359_123
_READINESS_SHA256 = "b8b3716910665f626b61e2f247df95e12f382408a5b7d23daa66f18ab22bf81e"

for _preloaded_name in (
    "king_state_confirmation_protocol_v2",
    "king_state_confirmation_readiness_v2",
):
    if _preloaded_name in sys.modules:
        raise ImportError(
            f"refusing preloaded confirmation module: {_preloaded_name}"
        )


def _load_authenticated_module(
    name: str,
    relative: str,
    expected_size: int,
    expected_sha256: str,
    *,
    initial_globals: Mapping[str, Any] | None = None,
) -> tuple[types.ModuleType, bytes, object]:
    path = (_PREAUTH_REPO / relative).resolve()
    payload = path.read_bytes()
    if (
        len(payload) != expected_size
        or hashlib.sha256(payload).hexdigest() != expected_sha256
    ):
        raise ImportError(f"authenticated confirmation dependency changed: {path}")
    if name in sys.modules:
        raise ImportError(f"refusing cached confirmation dependency: {name}")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    module.__loader__ = None
    if initial_globals is not None:
        module.__dict__.update(dict(initial_globals))
    sys.modules[name] = module
    try:
        exec(compile(payload, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    nonce = object()
    module.__dict__["__confirmation_authenticated_nonce__"] = nonce
    return module, payload, nonce


protocol, _PROTOCOL_SOURCE_BYTES, _PROTOCOL_AUTH_NONCE = _load_authenticated_module(
    "king_state_confirmation_protocol_v2",
    _PROTOCOL_RELATIVE,
    _PROTOCOL_SIZE,
    _PROTOCOL_SHA256,
)
readiness, _READINESS_SOURCE_BYTES, _READINESS_AUTH_NONCE = _load_authenticated_module(
    "king_state_confirmation_readiness_v2",
    _READINESS_RELATIVE,
    _READINESS_SIZE,
    _READINESS_SHA256,
    initial_globals={
        "__confirmation_parent_protocol_nonce__": _PROTOCOL_AUTH_NONCE,
    },
)


SCHEMA_VERSION = 1
PROTOCOL_ID = protocol.PROTOCOL_ID
GATES = protocol.GATES
FORMAL_GATES = protocol.FORMAL_GATES
PHASES = protocol.PHASES
TIMED_GATES = frozenset(("equal-time", "normal-start-clock"))

INTENT_KIND = "omega-nnue-open-confirmation-v2-launch-intent"
COMPLETION_KIND = "omega-nnue-open-confirmation-v2-launch-completion"
ASSESSMENT_KIND = "omega-nnue-open-confirmation-v2-gate-assessment"
DECISION_KIND = "omega-nnue-open-confirmation-v2-gate-decision"
# Shared-core compatibility kind; the confirmation-specific protocol/attempt
# bindings below make this instance stricter than the legacy minimum schema.
IDLE_KIND = "omega-equal-time-idle-attestation-v1"
CLOSURE_KIND = "omega-nnue-open-confirmation-v2-attempt-closure"
SUCCESS_KIND = "omega-nnue-open-confirmation-v2-program-success"
AUTHORIZATION_KIND = "omega-nnue-open-confirmation-v2-match-authorization"

PREDECESSOR = {
    "development": None,
    "equal-node": "development",
    "equal-time": "equal-node",
    "normal-start-clock": "equal-time",
}
SUCCESS_DECISION = {
    "development": "pass",
    "equal-node": "promote",
    "equal-time": "promote",
    "normal-start-clock": "promote",
}
TERMINAL_DECISIONS = {
    "development": frozenset(("pass", "fail", "safety-fail")),
    "equal-node": frozenset(
        ("promote", "futility", "inconclusive", "safety-fail")
    ),
    "equal-time": frozenset(
        ("promote", "futility", "inconclusive", "safety-fail")
    ),
    "normal-start-clock": frozenset(
        ("promote", "futility", "inconclusive", "safety-fail")
    ),
}
FAILURE_REASONS = {
    ("development", "fail"): "development-fail",
    ("development", "safety-fail"): "safety-fail",
    ("equal-node", "futility"): "equal-node-futility",
    ("equal-node", "inconclusive"): "equal-node-inconclusive",
    ("equal-node", "safety-fail"): "safety-fail",
    ("equal-time", "futility"): "equal-time-futility",
    ("equal-time", "inconclusive"): "equal-time-inconclusive",
    ("equal-time", "safety-fail"): "safety-fail",
    ("normal-start-clock", "futility"): "normal-start-clock-futility",
    ("normal-start-clock", "inconclusive"): "normal-start-clock-inconclusive",
    ("normal-start-clock", "safety-fail"): "safety-fail",
}
ABORT_REASONS = frozenset(
    (
        "operator-abort",
        "pending-intent-no-growth",
        "protected-identity-failure",
        "sampling-failure",
        "suite-seal-failure",
        "authorization-failure",
        "claim-publication-failure",
    )
)
RELEVANT_PROCESS_NAMES = frozenset(
    ("dotnet.exe", "omegamatch.exe", "senpai.exe")
)
EVENT_FIELDS = {
    "run": {
        "RecordType",
        "RunId",
        "ProfileId",
        "FreshnessMarker",
        "CreatedUtc",
        "ConfigSha256",
        "OpeningSuiteSha256",
        "HarnessVersion",
        "HarnessSha256",
        "HarnessBundleSha256",
        "OperatingSystem",
        "Runtime",
        "ProcessorCount",
        "Seed",
        "Match",
        "Engines",
    },
    "gameStart": {
        "RecordType",
        "GameId",
        "PairId",
        "Attempt",
        "StartedUtc",
        "OpeningId",
        "WhiteEngineId",
        "BlackEngineId",
        "InitialOfen",
        "OpeningMoves",
    },
    "ply": {
        "RecordType",
        "GameId",
        "Attempt",
        "Ply",
        "EngineId",
        "Color",
        "PreOfen",
        "PostOfen",
        "BestMove",
        "San",
        "WhiteClockBeforeMs",
        "BlackClockBeforeMs",
        "WhiteClockAfterMs",
        "BlackClockAfterMs",
        "Search",
        "FinalInfo",
        "WhiteScoreCp",
        "WhiteScoreMate",
        "Pv",
        "Error",
    },
    "gameResult": {
        "RecordType",
        "GameId",
        "PairId",
        "Attempt",
        "FinishedUtc",
        "OpeningId",
        "WhiteEngineId",
        "BlackEngineId",
        "Result",
        "Termination",
        "WinnerEngineId",
        "ScoreA",
        "Plies",
        "FinalOfen",
        "WhiteClockMs",
        "BlackClockMs",
        "IllegalMoves",
        "IllegalPvs",
        "ProtocolFailures",
        "TimeForfeits",
        "DevelopmentStyle",
    },
}
EVENT_OPTIONAL_FIELDS = {
    "run": set(),
    "gameStart": set(),
    "ply": {
        "PostOfen",
        "BestMove",
        "San",
        "FinalInfo",
        "WhiteScoreCp",
        "WhiteScoreMate",
        "Error",
    },
    "gameResult": {"WinnerEngineId", "DevelopmentStyle"},
}
EVENT_INTEGER_FIELDS = {
    "run": {"ProcessorCount", "Seed"},
    "gameStart": {"Attempt"},
    "ply": {
        "Attempt",
        "Ply",
        "WhiteClockBeforeMs",
        "BlackClockBeforeMs",
        "WhiteClockAfterMs",
        "BlackClockAfterMs",
    },
    "gameResult": {
        "Attempt",
        "Plies",
        "WhiteClockMs",
        "BlackClockMs",
        "IllegalMoves",
        "IllegalPvs",
        "ProtocolFailures",
        "TimeForfeits",
    },
}
CANONICAL_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
EVENT_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,7})?Z$"
)
COORDINATE_MOVE = re.compile(
    r"^[a-jw][0-9][a-jw][0-9](?:[qrbncw])?$", re.IGNORECASE
)
# SAN is emitted as one canonical token per move.  The rules replay proves
# coordinate-move legality; this syntax guard prevents blank, padded, or
# whitespace/control-bearing display strings from entering the authenticated
# evidence stream; emitted Omega SAN is printable ASCII.
SAN_TOKEN = re.compile(r"^[\x21-\x7e]+$")
SAFE_REASON = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
_UNSET = object()


# The protocol created this module from authenticated source bytes.  Capture
# both the module and the exact callables before any profile constants change.
core = protocol.match_core
_IMPORTED_PROTOCOL = protocol
_IMPORTED_READINESS = readiness
_IMPORTED_CORE = core
_IMPORTED_READINESS_CONTRACT = readiness.contract
_IMPORTED_READINESS_PROTOCOL_NONCE = readiness._PROTOCOL_AUTH_NONCE
_PROTOCOL_BINDINGS = {
    name: getattr(protocol, name)
    for name in (
        "attempt_namespace",
        "identity",
        "promotion_log_threshold",
        "strict_load",
        "validate_published_seed_bundle",
        "validate_protocol",
    )
}
_IMPORTED_SEED_ALGORITHM_ID = protocol.SEED_ALGORITHM_ID
_IMPORTED_DOTNET_RANDOM_SEED_MAX = protocol.DOTNET_RANDOM_SEED_MAX
_IMPORTED_UINT32_MAX = protocol.UINT32_MAX
_READINESS_BINDINGS = {
    name: getattr(readiness, name)
    for name in (
        "_operation_lock",
        "_require_operation_lock",
        "_sanitized_environment",
        "_verify_core_seal",
        "_verify_exact_g5_prefix_replay_bundle",
        "_verify_runtime_bundle",
        "_verify_runtime_authorization",
        "_PRACTICAL_ASSESS_EVENTS",
        "_PRACTICAL_AUTHENTICATE_ENGINE_INVENTORY",
        "_publish_abort",
        "attempt_paths",
        "claim_stage_seeds",
        "prior_published_stage_seeds",
        "verify_attempt_chain",
        "verify_attempt_reservation",
        "verify_authorization",
        "verify_candidate_claim",
        "verify_claim_seed_derivation",
        "verify_implementation_seal",
        "verify_suite_seal",
    )
}
_IMPORTED_GLOBAL_OPERATION_LOCK_PATH = readiness.GLOBAL_OPERATION_LOCK_PATH
_IMPORTED_PREFIX_REPLAY_ROOT = readiness.PREFIX_REPLAY_ROOT
_IMPORTED_G5_OMEGA_MATCH_RULES_ASSEMBLY = (
    readiness.G5_OMEGA_MATCH_RULES_ASSEMBLY
)
_IMPORTED_G5_RULES_BYTES = readiness.G5_RULES_BYTES
_IMPORTED_G5_RULES_SHA256 = readiness.G5_RULES_SHA256
_CORE_BINDINGS = {
    name: getattr(core, name)
    for name in (
        "_assess",
        "_harness_bundle_identity",
        "_score_for_candidate",
        "_sequential_gate",
        "_shuffled_indices",
        "_verify_seal",
    )
}
_SHUFFLED_INDICES = core._shuffled_indices
COLOR_ADAPTER_VERSION = "omega-match-color-white-black-to-w-b-v1"
TERMINAL_ADAPTER_VERSION = "omega-match-process-exit-terminal-error-v1"
DERIVED_PROCESS_EXIT_ERROR = (
    "Authenticated OmegaMatch process exit (derived for frozen assessor compatibility)."
)

# No other shared-core globals may be installed by this layer.
CORE_PROFILE_GLOBALS = frozenset(
    (
        "GATE_SPECS",
        "SAMPLER_TRAJECTORY_PAIRS",
        "SAMPLER_MAX_PLIES",
        "SAMPLER_POSITIONS_PER_PHASE_SIDE",
        "SAMPLER_CAPTURE_PERCENT",
        "MAX_PLIES",
        "ABSOLUTE_MAX_PLIES",
        "STOP_GRACE_MS",
        "MINIMUM_GATE_PAIRS",
        "MAXIMUM_GATE_PAIRS",
        "NULL_ELO",
        "PROMOTION_ALPHA",
        "FUTILITY_BETA",
        "PROMOTION_E_VALUE",
        "FUTILITY_E_VALUE",
        "BET_FRACTIONS",
        "ENGINE_OPTIONS",
        "SOURCE_NAMES",
    )
)


@dataclass(frozen=True)
class Context:
    protocol_value: dict[str, Any]
    attempt_index: int
    paths: dict[str, Any]
    implementation_seal: dict[str, Any]
    implementation_seal_identity: dict[str, Any]
    claim: dict[str, Any]
    claim_identity: dict[str, Any]
    stage_seeds: dict[str, int]
    suite_seal: dict[str, Any]
    suite_seal_identity: dict[str, Any]
    authorization: dict[str, Any]
    authorization_identity: dict[str, Any]
    core_seal: dict[str, Any]
    core_seal_identity: dict[str, Any]
    promotion_log_threshold: float
    promotion_e_value: float


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or CANONICAL_UTC.fullmatch(value) is None:
        raise ValueError(f"{label} must be canonical UTC with whole seconds")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    return parsed


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(_canonical_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _strict_object_bytes(payload: bytes, label: str) -> dict[str, Any]:
    def unique(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} repeats JSON key {key!r}")
            result[key] = value
        return result

    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=unique,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"{label} contains non-finite JSON {token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from error
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _strict_event_log(payload: bytes, label: str) -> list[dict[str, Any]]:
    if not payload or not payload.endswith(b"\n"):
        raise ValueError(f"{label} must be nonempty newline-terminated JSONL")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(payload.splitlines(), 1):
        if not line:
            raise ValueError(f"{label} contains a blank line")
        record = _strict_object_bytes(line, f"{label} line {line_number}")
        record_type = record.get("RecordType")
        if type(record_type) is not str or record_type not in {
            "run",
            "gameStart",
            "ply",
            "gameResult",
        }:
            raise ValueError(f"{label} line {line_number} has unknown RecordType")
        allowed = EVENT_FIELDS[record_type]
        required = allowed - EVENT_OPTIONAL_FIELDS[record_type]
        if not required.issubset(record) or not set(record).issubset(allowed):
            raise ValueError(
                f"{label} line {line_number} {record_type} field inventory changed"
            )
        for field in EVENT_INTEGER_FIELDS[record_type]:
            if type(record.get(field)) is not int:
                raise ValueError(
                    f"{label} line {line_number} {record_type}.{field} "
                    "is not an exact integer"
                )
        timestamp_field = {
            "run": "CreatedUtc",
            "gameStart": "StartedUtc",
            "gameResult": "FinishedUtc",
        }.get(record_type)
        if timestamp_field is not None:
            timestamp = record.get(timestamp_field)
            if type(timestamp) is not str or EVENT_UTC.fullmatch(timestamp) is None:
                raise ValueError(
                    f"{label} line {line_number} {record_type}."
                    f"{timestamp_field} is not canonical UTC"
                )
        records.append(record)
    if records[0].get("RecordType") != "run":
        raise ValueError(f"{label} must begin with its run record")
    if sum(record.get("RecordType") == "run" for record in records) != 1:
        raise ValueError(f"{label} must contain exactly one run record")
    return records


def _identity_shape(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity fields changed")
    path = value.get("path")
    size = value.get("bytes")
    digest = value.get("sha256")
    if (
        type(path) is not str
        or not path
        or type(size) is not int
        or size < 0
        or type(digest) is not str
        or protocol.HEX_256.fullmatch(digest) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(value)


def _verify_identity(value: Any, label: str) -> Path:
    record = _identity_shape(value, label)
    path = _resolved_identity_path(record, label)
    actual = protocol.identity(path, relative=not Path(record["path"]).is_absolute())
    protocol.require_exact_json(record, actual, label)
    return path.resolve()


def _resolved_identity_path(record: Mapping[str, Any], label: str) -> Path:
    """Resolve an authenticated identity path independently of process cwd."""

    raw = Path(str(record["path"]))
    if raw.is_absolute():
        return raw.resolve()
    return protocol.protocol_path(str(record["path"]), f"{label} path").resolve()


def _same_identity(actual: Any, expected: Any, label: str) -> None:
    protocol.require_exact_json(
        _identity_shape(actual, label), _identity_shape(expected, label), label
    )
    _verify_identity(actual, label)


def _event_prefix(
    path: Path,
    identity_record: Any,
    *,
    exact: bool,
    label: str,
) -> bytes:
    record = _identity_shape(identity_record, label)
    if _resolved_identity_path(record, label) != path.resolve():
        raise ValueError(f"{label} path changed")
    payload = path.resolve().read_bytes()
    count = record["bytes"]
    if len(payload) < count or (exact and len(payload) != count):
        raise ValueError(f"{label} event length changed")
    prefix = payload[:count]
    if hashlib.sha256(prefix).hexdigest() != record["sha256"]:
        raise ValueError(f"{label} event prefix changed")
    _strict_event_log(prefix, label)
    return prefix


def _verify_event_checkpoints(
    context: Context,
    gate: str,
    checkpoints: Sequence[Mapping[str, Any]],
    *,
    exact_latest: bool,
) -> None:
    path = _events(context, gate)
    if not checkpoints:
        if exact_latest and path.exists():
            raise ValueError(f"{gate} has events without a sealed checkpoint")
        return
    by_size: dict[int, str] = {}
    for number, item in enumerate(checkpoints, 1):
        record = _identity_shape(item, f"{gate} event checkpoint {number}")
        if _resolved_identity_path(record, f"{gate} event checkpoint {number}") != path.resolve():
            raise ValueError(f"{gate} checkpoint path changed")
        prior = by_size.setdefault(record["bytes"], record["sha256"])
        if prior != record["sha256"]:
            raise ValueError(f"{gate} has conflicting hashes at one event length")
    ordered = sorted(by_size.items())
    if not ordered or ordered[0][0] <= 0:
        raise ValueError(f"{gate} has an empty event checkpoint")
    payload = path.read_bytes()
    latest = ordered[-1][0]
    if len(payload) < latest or (exact_latest and len(payload) != latest):
        raise ValueError(f"{gate} current event length differs from its chain tip")
    digest = hashlib.sha256()
    cursor = 0
    for count, expected in ordered:
        digest.update(payload[cursor:count])
        cursor = count
        if digest.hexdigest() != expected:
            raise ValueError(f"{gate} event checkpoint changed at byte {count}")
    prefix = payload[:latest]
    records = _strict_event_log(prefix, f"{gate} checkpointed events")
    _verify_event_run_binding(context, gate, records)


def _assert_exact_bindings() -> None:
    if (
        sys.modules.get("king_state_confirmation_protocol_v2") is not _IMPORTED_PROTOCOL
        or sys.modules.get("king_state_confirmation_readiness_v2")
        is not _IMPORTED_READINESS
        or getattr(
            _IMPORTED_PROTOCOL, "__confirmation_authenticated_nonce__", None
        )
        is not _PROTOCOL_AUTH_NONCE
        or getattr(
            _IMPORTED_READINESS, "__confirmation_authenticated_nonce__", None
        )
        is not _READINESS_AUTH_NONCE
    ):
        raise ValueError("authenticated confirmation module/cache nonce changed")
    current_protocol = (_PREAUTH_REPO / _PROTOCOL_RELATIVE).read_bytes()
    current_readiness = (_PREAUTH_REPO / _READINESS_RELATIVE).read_bytes()
    if (
        current_protocol != _PROTOCOL_SOURCE_BYTES
        or current_readiness != _READINESS_SOURCE_BYTES
        or len(current_protocol) != _PROTOCOL_SIZE
        or hashlib.sha256(current_protocol).hexdigest() != _PROTOCOL_SHA256
        or len(current_readiness) != _READINESS_SIZE
        or hashlib.sha256(current_readiness).hexdigest() != _READINESS_SHA256
    ):
        raise ValueError("authenticated confirmation source bytes changed")
    if protocol is not _IMPORTED_PROTOCOL or readiness is not _IMPORTED_READINESS:
        raise ValueError("confirmation module binding changed in memory")
    if (
        readiness.contract is not _IMPORTED_READINESS_CONTRACT
        or _IMPORTED_READINESS_CONTRACT is not protocol
        or readiness._PROTOCOL_AUTH_NONCE
        is not _IMPORTED_READINESS_PROTOCOL_NONCE
        or _IMPORTED_READINESS_PROTOCOL_NONCE is not _PROTOCOL_AUTH_NONCE
    ):
        raise ValueError("readiness-to-protocol authenticated binding changed")
    if (
        getattr(protocol, "SEED_ALGORITHM_ID", None)
        != _IMPORTED_SEED_ALGORITHM_ID
        or getattr(protocol, "DOTNET_RANDOM_SEED_MAX", None)
        != _IMPORTED_DOTNET_RANDOM_SEED_MAX
        or getattr(protocol, "UINT32_MAX", None) != _IMPORTED_UINT32_MAX
    ):
        raise ValueError("confirmation protocol seed constants changed")
    if protocol.match_core is not _IMPORTED_CORE or core is not _IMPORTED_CORE:
        raise ValueError("authenticated shared match core binding changed")
    for name, value in _PROTOCOL_BINDINGS.items():
        if getattr(protocol, name, None) is not value:
            raise ValueError(f"confirmation protocol callable changed: {name}")
    for name, value in _READINESS_BINDINGS.items():
        if getattr(readiness, name, None) is not value:
            raise ValueError(f"confirmation readiness callable changed: {name}")
    if (
        getattr(readiness, "GLOBAL_OPERATION_LOCK_PATH", None)
        is not _IMPORTED_GLOBAL_OPERATION_LOCK_PATH
        or not isinstance(_IMPORTED_GLOBAL_OPERATION_LOCK_PATH, Path)
        or not _IMPORTED_GLOBAL_OPERATION_LOCK_PATH.is_absolute()
    ):
        raise ValueError("confirmation global operation-lock path changed")
    if (
        getattr(readiness, "PREFIX_REPLAY_ROOT", None)
        != _IMPORTED_PREFIX_REPLAY_ROOT
        or getattr(readiness, "G5_OMEGA_MATCH_RULES_ASSEMBLY", None)
        != _IMPORTED_G5_OMEGA_MATCH_RULES_ASSEMBLY
        or getattr(readiness, "G5_RULES_BYTES", None) != _IMPORTED_G5_RULES_BYTES
        or getattr(readiness, "G5_RULES_SHA256", None)
        != _IMPORTED_G5_RULES_SHA256
    ):
        raise ValueError("exact Generation-5 rules-replay constants changed")
    for name, value in _CORE_BINDINGS.items():
        if getattr(core, name, None) is not value:
            raise ValueError(f"shared match-core callable changed: {name}")


def _claim_stage_seeds(
    claim: Mapping[str, Any], attempt_index: int
) -> dict[str, int]:
    derivation = protocol.mapping(
        claim.get("seedDerivation"), "candidate claim seed derivation"
    )
    if set(derivation) != {
        "algorithmId",
        "entropyCommitment",
        "stageSeeds",
        "rejectionCounters",
    }:
        raise ValueError("candidate claim seed-derivation fields changed")
    if derivation.get("algorithmId") != _IMPORTED_SEED_ALGORITHM_ID:
        raise ValueError("candidate claim seed-derivation algorithm changed")
    commitment = derivation.get("entropyCommitment")
    if type(commitment) is not str or protocol.HEX_256.fullmatch(commitment) is None:
        raise ValueError("candidate claim entropy commitment is malformed")
    seeds = protocol.mapping(
        derivation.get("stageSeeds"), "candidate claim stage seeds"
    )
    counters = protocol.mapping(
        derivation.get("rejectionCounters"), "candidate claim rejection counters"
    )
    if set(seeds) != set(GATES) or set(counters) != set(GATES):
        raise ValueError("candidate claim seed stage inventory changed")
    normalized = readiness.verify_claim_seed_derivation(claim, attempt_index)
    protocol.require_exact_json(
        dict(derivation), normalized, "candidate claim seed derivation"
    )
    result: dict[str, int] = {}
    observed: set[int] = set()
    for gate in GATES:
        seed = seeds.get(gate)
        counter = counters.get(gate)
        if (
            type(seed) is not int
            or seed < 0
            or seed > _IMPORTED_DOTNET_RANDOM_SEED_MAX
        ):
            raise ValueError(f"candidate claim {gate} seed is not signed Int32")
        if seed in observed:
            raise ValueError("candidate claim reuses a seed across stages")
        if (
            type(counter) is not int
            or counter < 0
            or counter > _IMPORTED_UINT32_MAX
        ):
            raise ValueError(f"candidate claim {gate} rejection counter is not uint32")
        observed.add(seed)
        result[gate] = seed
    protocol.require_exact_json(
        result,
        readiness.claim_stage_seeds(claim, attempt_index),
        "candidate claim normalized stage seeds",
    )
    return result


def _stage_spec(
    protocol_value: Mapping[str, Any], stage_seeds: Mapping[str, int]
) -> dict[str, dict[str, Any]]:
    if set(stage_seeds) != set(GATES):
        raise ValueError("claim-bound stage-seed inventory changed")
    if any(
        type(stage_seeds[gate]) is not int
        or stage_seeds[gate] < 0
        or stage_seeds[gate] > _IMPORTED_DOTNET_RANDOM_SEED_MAX
        for gate in GATES
    ) or len({stage_seeds[gate] for gate in GATES}) != len(GATES):
        raise ValueError("claim-bound stage seeds are not distinct signed Int32")
    stages = protocol.mapping(protocol_value.get("stages"), "confirmation stages")
    result: dict[str, dict[str, Any]] = {}
    for gate in GATES:
        stage = protocol.mapping(stages.get(gate), f"{gate} stage")
        item: dict[str, Any] = {
            "seed": stage_seeds[gate],
            "roots": stage["roots"],
            "mode": stage["mode"],
            "searchTimeoutMs": stage["searchTimeoutMs"],
        }
        if gate == "normal-start-clock":
            item.update(
                {
                    "rootsPerPhase": stage["rootsPerTargetPly"],
                    "rootsPerPhaseSide": stage["rootsPerTargetPly"],
                    "initialTimeMs": stage["initialTimeMs"],
                    "incrementMs": stage["incrementMs"],
                }
            )
        else:
            item.update(
                {
                    "rootsPerPhase": stage["rootsPerPhase"],
                    "rootsPerPhaseSide": stage["rootsPerPhaseAndSideToMove"],
                }
            )
        if gate == "equal-time":
            item["moveTimeMs"] = stage["moveTimeMs"]
        elif gate != "normal-start-clock":
            item["nodes"] = stage["nodesPerMove"]
        if gate == "development":
            item["minimumCandidateScore"] = stage["minimumCandidateScore"]
        result[gate] = item
    return result


def _linear_threshold(log_threshold: float) -> float:
    value = math.exp(log_threshold)
    # The protocol comparison is authoritative in log space.  Rounding the
    # linear compatibility constant upward prevents the shared core from
    # accepting a value below the conservative log threshold.
    value = math.nextafter(value, math.inf)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("attempt promotion threshold is not finite positive")
    return value


def _install_core_profile(
    protocol_value: Mapping[str, Any],
    attempt_index: int,
    stage_seeds: Mapping[str, int],
) -> tuple[float, float]:
    _assert_exact_bindings()
    threshold = protocol.promotion_log_threshold(attempt_index)
    linear = _linear_threshold(threshold)
    sampler = protocol.mapping(protocol_value.get("sampler"), "confirmation sampler")
    execution = protocol.mapping(
        protocol_value.get("execution"), "confirmation execution"
    )
    engine = protocol.mapping(
        protocol_value.get("engineConfiguration"), "engine configuration"
    )
    values = {
        "GATE_SPECS": _stage_spec(protocol_value, stage_seeds),
        "SAMPLER_TRAJECTORY_PAIRS": sampler["trajectoryPairsPerStage"],
        "SAMPLER_MAX_PLIES": sampler["maxPlies"],
        "SAMPLER_POSITIONS_PER_PHASE_SIDE": sampler[
            "positionsPerPhaseAndSide"
        ],
        "SAMPLER_CAPTURE_PERCENT": sampler["captureSelectionPercent"],
        "MAX_PLIES": execution["maxPlies"],
        "ABSOLUTE_MAX_PLIES": execution["absoluteMaxPlies"],
        "STOP_GRACE_MS": execution["stopGraceMs"],
        "MINIMUM_GATE_PAIRS": 128,
        "MAXIMUM_GATE_PAIRS": 512,
        "NULL_ELO": 15.0,
        "PROMOTION_ALPHA": 1.0 / linear,
        "FUTILITY_BETA": 1.0 / 20.0,
        "PROMOTION_E_VALUE": linear,
        "FUTILITY_E_VALUE": 20.0,
        "BET_FRACTIONS": tuple(protocol.E_PROCESS_BET_FRACTIONS),
        "ENGINE_OPTIONS": dict(engine["commonEngineOptions"]),
        "SOURCE_NAMES": {
            gate: f"open-confirmation-v2-attempt-{attempt_index:06d}-{gate}.jsonl"
            for gate in GATES
        },
    }
    if set(values) != CORE_PROFILE_GLOBALS:
        raise AssertionError("confirmation shared-core profile inventory changed")
    for name, value in values.items():
        setattr(core, name, value)
    _assert_exact_bindings()
    return threshold, linear


def _path_entry(paths: Mapping[str, Any], key: str) -> Path:
    value = paths.get(key)
    if not isinstance(value, Path):
        raise ValueError(f"readiness attempt_paths[{key!r}] must be a Path")
    return value.resolve()


def _authorization_fields() -> set[str]:
    return {
        "schemaVersion",
        "kind",
        "protocol",
        "attemptIndex",
        "createdUtc",
        "implementationSeal",
        "candidateClaim",
        "stageSeeds",
        "jointSuiteSeal",
        "coreSeal",
        "g5NominationEvidence",
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
        "candidateControlIsolation",
        "orchestrator",
        "alphaSpending",
        "stageOrder",
        "launchOnlyThrough",
    }


def _context_checked(
    attempt_index: int,
    authorization_path: Path | None = None,
    *,
    verify_chain: bool = True,
) -> Context:
    protocol_value = protocol.validate_protocol()
    paths = readiness.attempt_paths(attempt_index)
    if type(paths) is not dict:
        raise ValueError("readiness attempt_paths result changed")
    readiness.verify_implementation_seal(
        _path_entry(paths, "implementationSeal"), orchestrator_path=Path(__file__)
    )
    # The active attempt is allowed to lack its closure, but every predecessor
    # must be closed and no successor may already exist.
    if verify_chain:
        readiness.verify_attempt_chain(protocol_value, through_attempt=attempt_index)
    claim_path = _path_entry(paths, "claim")
    suite_path = _path_entry(paths, "jointSuiteSeal")
    authorization = (
        _path_entry(paths, "authorization")
        if authorization_path is None
        else authorization_path.resolve()
    )
    claim = readiness.verify_candidate_claim(claim_path, attempt_index)
    reservation_path = _path_entry(paths, "reservation")
    readiness.verify_attempt_reservation(reservation_path, attempt_index)
    _same_identity(
        claim.get("attemptReservation"),
        protocol.identity(reservation_path),
        "claim attempt reservation",
    )
    stage_seeds = _claim_stage_seeds(claim, attempt_index)
    threshold, linear = _install_core_profile(
        protocol_value, attempt_index, stage_seeds
    )
    # Authorization creation performed the expensive full reproduction.  A
    # runtime command rehashes every sealed artifact and suite byte, but does
    # not regenerate the rules-only pools on every four-pair resume.
    suite = readiness.verify_suite_seal(suite_path, attempt_index, deep=False)
    protocol.require_exact_json(
        protocol.mapping(suite.get("stageSeeds"), "joint-suite stage seeds"),
        stage_seeds,
        "joint-suite claim-bound stage seeds",
    )
    auth = readiness._verify_runtime_authorization(
        authorization,
        attempt_index=attempt_index,
        protocol=protocol_value,
    )
    if type(auth) is not dict or set(auth) != _authorization_fields():
        raise ValueError("match authorization field inventory changed")
    if (
        type(auth.get("schemaVersion")) is not int
        or auth.get("schemaVersion") != SCHEMA_VERSION
        or auth.get("kind") != AUTHORIZATION_KIND
        or auth.get("attemptIndex") != attempt_index
        or auth.get("stageOrder") != list(GATES)
        or auth.get("launchOnlyThrough")
        != "tools/omega_nnue/king_state_confirmation_matches_v2.py"
    ):
        raise ValueError("match authorization envelope changed")
    _canonical_utc(auth.get("createdUtc"), "authorization createdUtc")
    _same_identity(
        auth.get("implementationSeal"),
        protocol.identity(_path_entry(paths, "implementationSeal")),
        "authorization implementation seal",
    )
    _same_identity(auth.get("candidateClaim"), protocol.identity(claim_path), "claim")
    protocol.require_exact_json(
        protocol.mapping(auth.get("stageSeeds"), "authorization stage seeds"),
        stage_seeds,
        "authorization claim-bound stage seeds",
    )
    _same_identity(auth.get("jointSuiteSeal"), protocol.identity(suite_path), "suite seal")
    core_seal_path = _verify_identity(auth.get("coreSeal"), "authorization core seal")
    core_seal = readiness._verify_core_seal(
        core_seal_path,
        attempt_index,
        suites=protocol.mapping(auth.get("suites"), "authorization suites"),
        configs=protocol.mapping(auth.get("configs"), "authorization configs"),
        network=protocol.mapping(
            auth.get("selectedNetwork"), "authorization selected network"
        ),
        engine=protocol.mapping(auth.get("engine"), "authorization engine"),
        harness=protocol.mapping(
            auth.get("omegaMatchAssembly"), "authorization OmegaMatch assembly"
        ),
        harness_bundle=protocol.mapping(
            auth.get("omegaMatchBundle"), "authorization OmegaMatch bundle"
        ),
    )
    alpha = protocol.mapping(auth.get("alphaSpending"), "authorization alpha spending")
    if (
        alpha.get("attemptIndex") != attempt_index
        or type(alpha.get("promotionLogThreshold")) is not float
        or alpha["promotionLogThreshold"] != threshold
    ):
        raise ValueError("authorization attempt threshold changed")
    context = Context(
        protocol_value=dict(protocol_value),
        attempt_index=attempt_index,
        paths=dict(paths),
        implementation_seal=readiness.verify_implementation_seal(
            _path_entry(paths, "implementationSeal"),
            orchestrator_path=Path(__file__),
        ),
        implementation_seal_identity=protocol.identity(
            _path_entry(paths, "implementationSeal")
        ),
        claim=dict(claim),
        claim_identity=protocol.identity(claim_path),
        stage_seeds=stage_seeds,
        suite_seal=dict(suite),
        suite_seal_identity=protocol.identity(suite_path),
        authorization=dict(auth),
        authorization_identity=protocol.identity(authorization),
        core_seal=dict(core_seal),
        core_seal_identity=protocol.identity(core_seal_path),
        promotion_log_threshold=threshold,
        promotion_e_value=linear,
    )
    _verify_authorized_gate_identities(context)
    return context


def _context(
    attempt_index: int,
    authorization_path: Path | None = None,
    *,
    verify_chain: bool = True,
) -> Context:
    """Load a context and consume post-selection identity failures."""

    try:
        return _context_checked(
            attempt_index,
            authorization_path,
            verify_chain=verify_chain,
        )
    except (OSError, ValueError):
        paths = readiness.attempt_paths(attempt_index)
        claim = _path_entry(paths, "claim")
        closure = _path_entry(paths, "attemptClosure")
        joint = _path_entry(paths, "jointSuiteSeal")
        if claim.is_file() and joint.exists() and not closure.exists():
            readiness._publish_abort(attempt_index, "protected-identity-failure")
        raise


def _gate_root(context: Context, gate: str) -> Path:
    stages = context.paths.get("stages")
    if type(stages) is not dict or set(stages) != set(GATES):
        raise ValueError("readiness attempt_paths stage inventory changed")
    value = stages.get(gate)
    if not isinstance(value, Path):
        raise ValueError(f"readiness stage path for {gate} is not a Path")
    return value.resolve()


def _events(context: Context, gate: str) -> Path:
    return _gate_root(context, gate) / "events.jsonl"


def _launch_dir(context: Context, gate: str) -> Path:
    return _gate_root(context, gate) / "launches"


def _assessment_dir(context: Context, gate: str) -> Path:
    return _gate_root(context, gate) / "assessments"


def _intent_path(context: Context, gate: str, sequence: int) -> Path:
    return _launch_dir(context, gate) / f"{sequence:06d}.intent.json"


def _completion_path(context: Context, gate: str, sequence: int) -> Path:
    return _launch_dir(context, gate) / f"{sequence:06d}.completion.json"


def _assessment_path(context: Context, gate: str, sequence: int) -> Path:
    return _assessment_dir(context, gate) / f"{sequence:06d}.json"


def _decision_path(context: Context, gate: str) -> Path:
    return _gate_root(context, gate) / "decision.json"


def _idle_path(context: Context, gate: str) -> Path:
    if gate not in TIMED_GATES:
        raise ValueError(f"{gate} does not use an idle-machine attestation")
    return _gate_root(context, gate) / "idle-machine.attestation.json"


def _config_identity(context: Context, gate: str) -> dict[str, Any]:
    configs = protocol.mapping(context.authorization.get("configs"), "authorization configs")
    if set(configs) != set(GATES):
        raise ValueError("authorization config inventory changed")
    return _identity_shape(configs.get(gate), f"{gate} config")


def _suite_identity(context: Context, gate: str) -> dict[str, Any]:
    suites = protocol.mapping(context.authorization.get("suites"), "authorization suites")
    if set(suites) != set(GATES):
        raise ValueError("authorization suite inventory changed")
    return _identity_shape(suites.get(gate), f"{gate} suite")


def _config_path(context: Context, gate: str) -> Path:
    return _verify_identity(_config_identity(context, gate), f"{gate} config")


def _verify_event_run_binding(
    context: Context,
    gate: str,
    records: Sequence[Mapping[str, Any]],
) -> None:
    if not records or records[0].get("RecordType") != "run":
        raise ValueError(f"{gate} event prefix lacks its run record")
    config_path = _config_path(context, gate)
    config = protocol.strict_load(config_path, f"{gate} config run binding")
    run = records[0]
    expected = {
        "RunId": config["runId"],
        "ProfileId": config["profileId"],
        "FreshnessMarker": config["freshnessMarker"],
        "ConfigSha256": protocol.sha256(config_path),
        "OpeningSuiteSha256": _suite_identity(context, gate)["sha256"],
        "HarnessSha256": context.authorization["omegaMatchAssembly"]["sha256"],
        "HarnessBundleSha256": context.authorization["omegaMatchBundle"]["sha256"],
        "Seed": context.stage_seeds[gate],
    }
    for field, wanted in expected.items():
        if type(wanted) is int:
            if type(run.get(field)) is not int or run.get(field) != wanted:
                raise ValueError(f"{gate} run record {field} changed")
        elif run.get(field) != wanted:
            raise ValueError(f"{gate} run record {field} changed")


def _nested_field(value: Mapping[str, Any], name: str) -> Any:
    matches = [item for key, item in value.items() if str(key).casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValueError(f"effective runtime object lacks unique field {name}")
    return matches[0]


def _casefold_object(value: Any, label: str) -> Any:
    if type(value) is dict:
        result: dict[str, Any] = {}
        for key, item in value.items():
            folded = str(key).casefold()
            if folded in result:
                raise ValueError(f"{label} repeats a case-folded key")
            result[folded] = _casefold_object(item, f"{label}.{key}")
        return result
    if type(value) is list:
        return [_casefold_object(item, label) for item in value]
    return value


def _resolved_match_spec(match: Mapping[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        "engineA": "baseline",
        "engineB": "candidate",
        "openingsFile": "Openings/omega-public-24.json",
        "repeats": 1,
        "maxPlies": 400,
        "absoluteMaxPlies": None,
        "mode": "depth",
        "depth": 6,
        "nodes": 100_000,
        "moveTimeMs": 1_000,
        "initialTimeMs": 60_000,
        "incrementMs": 500,
        "searchTimeoutMs": 120_000,
        "stopGraceMs": 2_000,
        "bootstrapIterations": 10_000,
        "sequentialGate": {
            "candidateEngine": "",
            "minimumPairs": 64,
            "nullElo": 10.0,
            "promotionAlpha": 0.05,
            "futilityBeta": 0.10,
        },
        "freshProcessPerGame": True,
    }
    for key, value in match.items():
        if key == "sequentialGate":
            if type(value) is not dict:
                raise ValueError("sealed match sequentialGate is malformed")
            resolved["sequentialGate"].update(value)
        else:
            if key not in resolved:
                raise ValueError(f"sealed match has unknown field {key}")
            resolved[key] = value
    return resolved


def _system_text_json_number_shape(value: Any) -> Any:
    if type(value) is dict:
        return {key: _system_text_json_number_shape(item) for key, item in value.items()}
    if type(value) is list:
        return [_system_text_json_number_shape(item) for item in value]
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _candidate_game_score(result: Mapping[str, Any]) -> float:
    text = result.get("Result")
    white = result.get("WhiteEngineId")
    black = result.get("BlackEngineId")
    if text == "1/2-1/2":
        return 0.5
    if text == "1-0":
        return 1.0 if white == "nnue-candidate" else 0.0
    if text == "0-1":
        return 1.0 if black == "nnue-candidate" else 0.0
    raise ValueError("gameResult.Result is outside the sealed result domain")


def _explicit_terminal_failure(record: Mapping[str, Any]) -> bool:
    """Recognize an error message or the pinned process-exit telemetry."""

    error = record.get("Error")
    if type(error) is str:
        return bool(error.strip())
    if error is not None:
        return False
    search = record.get("Search")
    return type(search) is dict and search.get("ProcessExited") is True


def _authenticate_ply_emitter_shape(
    record: Mapping[str, Any], match: Mapping[str, Any], gate: str
) -> None:
    """Authenticate the pinned OmegaMatch Search/Pv JSON shapes and command."""

    search = record.get("Search")
    if type(search) is not dict:
        raise ValueError(f"{gate} ply Search is not an object")
    required_search = {
        "Command", "WallTimeMs", "DeadlineExceeded", "ProcessExited",
        "Info", "RawOutput", "StandardError",
    }
    optional_search = {"BestMove", "Ponder", "ExitCode"}
    if not required_search.issubset(search) or set(search).difference(
        required_search | optional_search
    ):
        raise ValueError(f"{gate} ply Search field inventory changed")
    if (
        type(search.get("Command")) is not str
        or type(search.get("WallTimeMs")) not in (int, float)
        or type(search.get("WallTimeMs")) is bool
        or not math.isfinite(float(search["WallTimeMs"]))
        or float(search["WallTimeMs"]) < 0
        or type(search.get("DeadlineExceeded")) is not bool
        or type(search.get("ProcessExited")) is not bool
        or type(search.get("Info")) is not list
        or type(search.get("RawOutput")) is not list
        or type(search.get("StandardError")) is not list
        or any(type(item) is not dict for item in search["Info"])
        or any(type(item) is not str for item in search["RawOutput"])
        or any(type(item) is not str for item in search["StandardError"])
    ):
        raise ValueError(f"{gate} ply Search value shape changed")
    mode = str(match.get("mode", "")).casefold()
    if mode == "nodes":
        expected_command = f"go nodes {match.get('nodes')}"
    elif mode == "movetime":
        expected_command = f"go movetime {match.get('moveTimeMs')}"
    elif mode == "clock":
        white = record.get("WhiteClockBeforeMs")
        black = record.get("BlackClockBeforeMs")
        if type(white) is not int or type(black) is not int:
            raise ValueError(f"{gate} clock ply lacks exact before clocks")
        expected_command = (
            f"go wtime {white} btime {black} "
            f"winc {match.get('incrementMs')} binc {match.get('incrementMs')}"
        )
    else:
        raise ValueError(f"{gate} raw Search uses an unsupported mode")
    if search["Command"] != expected_command:
        raise ValueError(f"{gate} raw Search.Command differs from the sealed mode")

    pv = record.get("Pv")
    required_pv = {"LegalPlies", "TotalPlies", "San", "IsLegal"}
    optional_pv = {"IllegalMove", "Error"}
    if (
        type(pv) is not dict
        or not required_pv.issubset(pv)
        or set(pv).difference(required_pv | optional_pv)
    ):
        raise ValueError(f"{gate} ply PvValidation field inventory changed")
    legal = pv.get("LegalPlies")
    total = pv.get("TotalPlies")
    san = pv.get("San")
    illegal = pv.get("IllegalMove")
    if (
        type(legal) is not int
        or type(total) is not int
        or legal < 0
        or total < legal
        or type(san) is not list
        or len(san) != legal
        or any(
            type(item) is not str or SAN_TOKEN.fullmatch(item) is None
            for item in san
        )
        or pv.get("IsLegal") is not (illegal is None)
        or (illegal is not None and type(illegal) is not str)
        or (pv.get("Error") is not None and type(pv.get("Error")) is not str)
    ):
        raise ValueError(f"{gate} ply PvValidation value shape changed")


def _authenticate_raw_event_schedule(
    *,
    records: Sequence[Mapping[str, Any]],
    suite: Mapping[str, Any],
    config: Mapping[str, Any],
    suite_identity: Mapping[str, Any],
    config_identity: Mapping[str, Any],
    gate: str,
) -> dict[str, Any]:
    """Authenticate every raw OmegaMatch relation before any assessment."""

    if not records or records[0].get("RecordType") != "run":
        raise ValueError(f"{gate} raw events lack their leading run")
    run = records[0]
    expected_run = {
        "RunId": config.get("runId"),
        "ProfileId": config.get("profileId"),
        "FreshnessMarker": config.get("freshnessMarker"),
        "ConfigSha256": config_identity.get("sha256"),
        "OpeningSuiteSha256": suite_identity.get("sha256"),
        "Seed": config.get("seed"),
    }
    for field, expected in expected_run.items():
        if run.get(field) != expected or type(run.get(field)) is not type(expected):
            raise ValueError(f"{gate} raw run {field} is not config-bound")
    run_match = run.get("Match")
    if type(run_match) is not dict:
        raise ValueError(f"{gate} raw run lacks effective match settings")
    match = config.get("match")
    if type(match) is not dict:
        raise ValueError(f"{gate} config lacks match settings")
    expected_match = _casefold_object(
        _system_text_json_number_shape(_resolved_match_spec(match)),
        f"{gate} sealed match",
    )
    actual_match = _casefold_object(run_match, f"{gate} raw run match")
    if actual_match.get("mode") is not None and type(actual_match["mode"]) is str:
        actual_match["mode"] = actual_match["mode"].casefold()
    expected_match["mode"] = str(expected_match["mode"]).casefold()
    if not protocol.exact_json_equal(actual_match, expected_match):
        raise ValueError(f"{gate} raw run full MatchSpec changed")

    readiness._PRACTICAL_AUTHENTICATE_ENGINE_INVENTORY(
        config.get("engines"), run.get("Engines"), f"{gate} raw run"
    )

    openings_value = suite.get("openings")
    if type(openings_value) is not list or not openings_value:
        raise ValueError(f"{gate} suite has no openings")
    openings: dict[str, Mapping[str, Any]] = {}
    for opening in openings_value:
        if type(opening) is not dict or type(opening.get("id")) is not str:
            raise ValueError(f"{gate} sealed opening is malformed")
        if opening["id"] in openings:
            raise ValueError(f"{gate} sealed opening identity is duplicated")
        openings[opening["id"]] = opening
    permutation = _SHUFFLED_INDICES(len(openings_value), int(config["seed"]))
    scheduled_openings = [openings_value[index] for index in permutation]
    expected_games: list[dict[str, Any]] = []
    for opening in scheduled_openings:
        opening_id = str(opening["id"])
        pair_id = f"{opening_id}-r001"
        for suffix, white, black in (
            ("ab", "nnue-candidate", "hce-control"),
            ("ba", "hce-control", "nnue-candidate"),
        ):
            expected_games.append(
                {
                    "GameId": f"{pair_id}-{suffix}",
                    "PairId": pair_id,
                    "OpeningId": opening_id,
                    "WhiteEngineId": white,
                    "BlackEngineId": black,
                    "InitialOfen": opening.get("initialOfen"),
                    "OpeningMoves": opening.get("moves"),
                }
            )

    schedule_index = 0
    attempt_by_game: dict[str, int] = {}
    starts: dict[tuple[str, int], Mapping[str, Any]] = {}
    plies: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    results: dict[str, Mapping[str, Any]] = {}
    active_key: tuple[str, int] | None = None
    completed_games = 0
    raw_colors = {"white": 0, "black": 0}
    terminal_failed_plies = 0
    process_exit_terminal_plies = 0
    successful_plies_authenticated = 0
    for record in records[1:]:
        record_type = record.get("RecordType")
        if schedule_index >= len(expected_games):
            raise ValueError(f"{gate} raw events exceed the sealed schedule")
        expected = expected_games[schedule_index]
        if record_type == "gameStart":
            game_id = record.get("GameId")
            attempt = record.get("Attempt")
            if game_id != expected["GameId"]:
                raise ValueError(f"{gate} gameStart is outside the exact schedule prefix")
            if type(attempt) is not int or attempt != attempt_by_game.get(game_id, 0) + 1:
                raise ValueError(f"{gate} gameStart attempts are not unique and monotone")
            for field, wanted in expected.items():
                if record.get(field) != wanted or type(record.get(field)) is not type(wanted):
                    raise ValueError(f"{gate} gameStart {field} differs from sealed schedule")
            attempt_by_game[str(game_id)] = attempt
            active_key = (str(game_id), attempt)
            starts[active_key] = record
            plies[active_key] = []
        elif record_type == "ply":
            key = (str(record.get("GameId")), record.get("Attempt"))
            if active_key is None or key != active_key or key not in starts:
                raise ValueError(f"{gate} ply is not linked to the active gameStart")
            sequence = plies[key]
            opening_depth = len(starts[key]["OpeningMoves"])
            if record.get("Ply") != opening_depth + len(sequence) + 1:
                raise ValueError(f"{gate} ply numbering is not unique and monotone")
            start = starts[key]
            initial_side = str(start["InitialOfen"]).split()[1]
            if initial_side not in {"w", "b"}:
                raise ValueError(f"{gate} gameStart side to move is malformed")
            if len(start["OpeningMoves"]) % 2:
                initial_side = "b" if initial_side == "w" else "w"
            side = initial_side if len(sequence) % 2 == 0 else ("b" if initial_side == "w" else "w")
            raw_color = "white" if side == "w" else "black"
            if record.get("Color") != raw_color:
                raise ValueError(f"{gate} ply uses a noncanonical or wrong raw Color token")
            expected_engine = start["WhiteEngineId"] if side == "w" else start["BlackEngineId"]
            if record.get("EngineId") != expected_engine:
                raise ValueError(f"{gate} ply EngineId contradicts its color roles")
            _authenticate_ply_emitter_shape(record, match, gate)
            if sequence and record.get("PreOfen") != sequence[-1].get("PostOfen"):
                raise ValueError(f"{gate} ply OFEN chain is discontinuous")
            if not sequence:
                opening = openings[str(start["OpeningId"])]
                metadata = opening.get("openConfirmationV2")
                endpoint = (
                    metadata.get("finalOfen")
                    if type(metadata) is dict
                    else opening.get("initialOfen")
                )
                if record.get("PreOfen") != endpoint:
                    raise ValueError(f"{gate} first ply is not rooted at the sealed position")
            raw_colors[raw_color] += 1
            sequence.append(record)
        elif record_type == "gameResult":
            key = (str(record.get("GameId")), record.get("Attempt"))
            if active_key is None or key != active_key or key not in starts:
                raise ValueError(f"{gate} gameResult is not linked to the active gameStart")
            if key[0] in results:
                raise ValueError(f"{gate} gameResult GameId is duplicated")
            start = starts[key]
            for field in (
                "GameId", "PairId", "Attempt", "OpeningId", "WhiteEngineId", "BlackEngineId"
            ):
                if record.get(field) != start.get(field) or type(record.get(field)) is not type(start.get(field)):
                    raise ValueError(f"{gate} gameResult {field} contradicts gameStart")
            expected_plies = record.get("Plies")
            opening_depth = len(start["OpeningMoves"])
            game_plies = plies[key]
            if type(expected_plies) is not int or expected_plies < opening_depth:
                raise ValueError(f"{gate} gameResult.Plies precedes its opening prefix")
            successful_searches = expected_plies - opening_depth
            successful_plies_authenticated += successful_searches
            decisive_safety = sum(
                int(record.get(field, -1))
                for field in ("IllegalMoves", "ProtocolFailures", "TimeForfeits")
            )
            if successful_searches == 0 and decisive_safety == 0:
                raise ValueError(
                    f"{gate} zero-safety completed game has no rules-replayed ply"
                )
            for successful in game_plies[:successful_searches]:
                successful_search = successful.get("Search")
                best_move = successful.get("BestMove")
                san = successful.get("San")
                pv = successful.get("Pv")
                if (
                    successful.get("PostOfen") in (None, "")
                    or successful.get("Error") not in (None, "")
                    or type(best_move) is not str
                    or COORDINATE_MOVE.fullmatch(best_move) is None
                    or type(san) is not str
                    or SAN_TOKEN.fullmatch(san) is None
                    or type(successful_search) is not dict
                    or successful_search.get("ProcessExited") is not False
                    or successful_search.get("DeadlineExceeded") is not False
                    or successful_search.get("BestMove") != best_move
                    or type(pv) is not dict
                    or type(pv.get("IsLegal")) is not bool
                ):
                    raise ValueError(f"{gate} successful search ply is malformed")
            observed_illegal_pvs = sum(
                type(ply.get("Pv")) is dict
                and ply["Pv"].get("IsLegal") is False
                for ply in game_plies
            )
            if (
                type(record.get("IllegalPvs")) is not int
                or record["IllegalPvs"] != observed_illegal_pvs
            ):
                raise ValueError(
                    f"{gate} gameResult.IllegalPvs contradicts ply PvValidation"
                )
            if len(game_plies) == successful_searches:
                pass
            elif len(game_plies) == successful_searches + 1 and decisive_safety > 0:
                terminal = game_plies[-1]
                if (
                    terminal.get("Ply") != expected_plies + 1
                    or not _explicit_terminal_failure(terminal)
                    or terminal.get("PostOfen") not in (None, "")
                    or type(record.get("Termination")) is not str
                    or not record["Termination"].strip()
                ):
                    raise ValueError(f"{gate} terminal failed-search ply is malformed")
                terminal_failed_plies += 1
                if (
                    terminal.get("Error") is None
                    and type(terminal.get("Search")) is dict
                    and terminal["Search"].get("ProcessExited") is True
                ):
                    process_exit_terminal_plies += 1
            else:
                raise ValueError(f"{gate} gameResult.Plies lacks exact emitted coverage")
            opening = openings[str(start["OpeningId"])]
            metadata = opening.get("openConfirmationV2")
            endpoint = (
                metadata.get("finalOfen")
                if type(metadata) is dict
                else opening.get("initialOfen")
            )
            expected_final = (
                game_plies[successful_searches - 1].get("PostOfen")
                if successful_searches
                else endpoint
            )
            if record.get("FinalOfen") != expected_final:
                raise ValueError(f"{gate} gameResult.FinalOfen breaks the ply chain")
            score = _candidate_game_score(record)
            score_a = record.get("ScoreA")
            if type(score_a) not in (int, float) or type(score_a) is bool or not math.isfinite(float(score_a)) or abs(float(score_a) - score) > 1e-12:
                raise ValueError(f"{gate} gameResult.ScoreA contradicts the result")
            winner = {
                "1-0": start["WhiteEngineId"],
                "0-1": start["BlackEngineId"],
                "1/2-1/2": None,
            }[record["Result"]]
            if record.get("WinnerEngineId") != winner:
                raise ValueError(f"{gate} gameResult winner contradicts the result")
            results[key[0]] = record
            completed_games += 1
            schedule_index += 1
            active_key = None
        else:
            raise ValueError(f"{gate} raw event record type escaped strict parsing")

    complete_pairs = schedule_index // 2
    return {
        "schemaVersion": 1,
        "gate": gate,
        "config": dict(config_identity),
        "suite": dict(suite_identity),
        "authorizedScheduleGames": len(expected_games),
        "authorizedStarts": len(starts),
        "completedGames": completed_games,
        "completePairs": complete_pairs,
        "activeAttempt": (
            None
            if active_key is None
            else {"gameId": active_key[0], "attempt": active_key[1]}
        ),
        "rawColorCounts": raw_colors,
        "successfulPlies": successful_plies_authenticated,
        "terminalFailedPlies": terminal_failed_plies,
        "processExitTerminalPlies": process_exit_terminal_plies,
        "allStartsResultsAndPliesAuthenticated": True,
    }


def _adapt_pinned_emitter_colors(
    raw: bytes, *, label: str
) -> tuple[bytes, dict[str, Any]]:
    records = _strict_event_log(raw, label)
    derived = copy.deepcopy(records)
    transformed = 0
    for raw_record, derived_record in zip(records, derived):
        if raw_record.get("RecordType") != "ply":
            continue
        token = raw_record.get("Color")
        if token not in {"white", "black"}:
            raise ValueError(f"{label} contains a noncanonical raw Color token")
        derived_record["Color"] = "w" if token == "white" else "b"
        transformed += 1
    for raw_record, derived_record in zip(records, derived):
        expected = copy.deepcopy(raw_record)
        if raw_record.get("RecordType") == "ply":
            expected["Color"] = "w" if raw_record["Color"] == "white" else "b"
        if derived_record != expected:
            raise AssertionError("color adapter changed a non-Color field")
    derived_bytes = b"".join(
        (
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        for record in derived
    )
    _strict_event_log(derived_bytes, f"{label} derived color view")
    return derived_bytes, {
        "transformVersion": COLOR_ADAPTER_VERSION,
        "acceptedRawTokens": ["white", "black"],
        "derivedTokens": ["w", "b"],
        "raw": {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
        "derived": {
            "bytes": len(derived_bytes),
            "sha256": hashlib.sha256(derived_bytes).hexdigest(),
        },
        "transformedPlies": transformed,
        "otherFieldsChanged": 0,
        "rawEvidenceRetained": True,
    }


def _adapt_process_exit_terminals_for_frozen_core(
    color_derived: bytes,
    *,
    expected_transforms: int,
    label: str,
) -> tuple[bytes, dict[str, Any]]:
    """Supply the frozen assessor's required Error without changing raw proof.

    The pinned emitter can serialize a crash as ``ProcessExited: true`` with a
    missing/null ply Error.  Raw schedule authentication establishes that this
    is the one extra terminal failed-search ply.  The older shared assessor
    requires a nonempty Error, so this deterministic derived view adds exactly
    that field and nothing else.
    """

    if type(expected_transforms) is not int or expected_transforms < 0:
        raise ValueError("terminal adapter expected transform count is invalid")
    records = _strict_event_log(color_derived, label)
    derived = copy.deepcopy(records)
    transformed = 0
    for raw_record, derived_record in zip(records, derived):
        search = raw_record.get("Search")
        if (
            raw_record.get("RecordType") == "ply"
            and raw_record.get("Error") is None
            and type(search) is dict
            and search.get("ProcessExited") is True
        ):
            if raw_record.get("PostOfen") not in (None, ""):
                raise ValueError(f"{label} process-exit ply has a PostOfen")
            derived_record["Error"] = DERIVED_PROCESS_EXIT_ERROR
            transformed += 1
    if transformed != expected_transforms:
        raise ValueError(
            f"{label} terminal-adapter count differs from raw authentication"
        )
    for raw_record, derived_record in zip(records, derived):
        expected = copy.deepcopy(raw_record)
        search = raw_record.get("Search")
        if (
            raw_record.get("RecordType") == "ply"
            and raw_record.get("Error") is None
            and type(search) is dict
            and search.get("ProcessExited") is True
        ):
            expected["Error"] = DERIVED_PROCESS_EXIT_ERROR
        if derived_record != expected:
            raise AssertionError("terminal adapter changed a non-Error field")
    derived_bytes = b"".join(
        (
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        for record in derived
    )
    _strict_event_log(derived_bytes, f"{label} derived terminal view")
    return derived_bytes, {
        "transformVersion": TERMINAL_ADAPTER_VERSION,
        "authenticatedCondition": (
            "terminal ply with absent/null Error and Search.ProcessExited true"
        ),
        "derivedError": DERIVED_PROCESS_EXIT_ERROR,
        "input": {
            "bytes": len(color_derived),
            "sha256": hashlib.sha256(color_derived).hexdigest(),
        },
        "derived": {
            "bytes": len(derived_bytes),
            "sha256": hashlib.sha256(derived_bytes).hexdigest(),
        },
        "transformedTerminalPlies": transformed,
        "errorFieldsChanged": transformed,
        "nonErrorFieldsChanged": 0,
        "rawEvidenceRetained": True,
    }


def _authenticate_successful_ply_rules_replay(
    context: Context,
    gate: str,
    raw_prefix: bytes,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Replay every successful transcript with the pinned ChessLib helper."""

    bundle = readiness._verify_exact_g5_prefix_replay_bundle(
        context.implementation_seal.get("prefixReplayBundle"),
        "formal raw-event prefix replay bundle",
    )
    bundle_root = Path(str(bundle["root"])).resolve()
    assembly = (bundle_root / str(bundle["assemblyRelativePath"])).resolve()
    rules = (bundle_root / "ChessLib.dll").resolve()
    omega_match_rules = _verify_identity(
        protocol.mapping(
            context.implementation_seal.get("pinned"),
            "formal implementation pins",
        ).get("omegaMatchRulesAssembly"),
        "formal frozen OmegaMatch rules assembly",
    )
    if (
        bundle_root != _IMPORTED_PREFIX_REPLAY_ROOT
        or omega_match_rules != _IMPORTED_G5_OMEGA_MATCH_RULES_ASSEMBLY
        or protocol.identity(rules, relative=False)["bytes"]
        != _IMPORTED_G5_RULES_BYTES
        or protocol.identity(rules, relative=False)["sha256"]
        != _IMPORTED_G5_RULES_SHA256
        or protocol.identity(omega_match_rules, relative=False)["bytes"]
        != _IMPORTED_G5_RULES_BYTES
        or protocol.identity(omega_match_rules, relative=False)["sha256"]
        != _IMPORTED_G5_RULES_SHA256
    ):
        raise ValueError(
            f"{gate} replay rules differ from frozen Generation-5 OmegaMatch"
        )
    replay_rules_identity = protocol.identity(rules, relative=False)
    omega_match_rules_identity = protocol.identity(
        omega_match_rules, relative=False
    )
    dotnet = _verify_identity(
        context.authorization.get("dotnetHost"),
        "formal raw-event prefix replay dotnet host",
    )
    starts: dict[tuple[str, int], Mapping[str, Any]] = {}
    plies: dict[tuple[str, int], list[tuple[int, Mapping[str, Any]]]] = {}
    for line_number, record in enumerate(records, 1):
        record_type = record.get("RecordType")
        if record_type == "gameStart":
            key = (str(record["GameId"]), int(record["Attempt"]))
            starts[key] = record
            plies[key] = []
        elif record_type == "ply":
            key = (str(record["GameId"]), int(record["Attempt"]))
            if key not in starts:
                raise ValueError(f"{gate} replay ply lacks its authenticated start")
            plies[key].append((line_number, record))

    normalized: list[dict[str, Any]] = []
    successful_plies = 0
    with tempfile.TemporaryDirectory(
        prefix=f"omega-{gate}-rules-replay-"
    ) as directory:
        root = Path(directory).resolve()
        source = root / "authenticated-events-prefix.jsonl"
        source.write_bytes(raw_prefix)
        source_identity = protocol.identity(source, relative=False)
        requests: list[dict[str, Any]] = []
        for request_id, key in enumerate(starts):
            successful = [
                (line_number, ply)
                for line_number, ply in plies[key]
                if ply.get("PostOfen") not in (None, "")
                and ply.get("Error") in (None, "")
            ]
            if not successful:
                continue
            moves = [str(ply["BestMove"]) for _, ply in successful]
            positions = [str(ply["PostOfen"]) for _, ply in successful]
            initial = str(successful[0][1]["PreOfen"])
            request_id = len(requests)
            request = {
                "schemaVersion": 1,
                "kind": "omega-opening-prefix-replay-request-v1",
                "requestId": request_id,
                "sourcePath": str(source),
                "sourceBytes": source_identity["bytes"],
                "sourceSha256": source_identity["sha256"],
                "sourceRecord": successful[0][0],
                "sourceObjectOrdinal": request_id + 1,
                "sourceObjectIdentity": (
                    f"/game/{key[0]}/attempt/{key[1]}/successful-transcript"
                ),
                "schema": "transcript",
                "initialSource": "explicit",
                "containerProof": "transcript-game",
                "initialOfen": initial,
                "moves": moves,
                "expectedPositions": positions,
            }
            requests.append(request)
            normalized.append(
                {
                    "gameId": key[0],
                    "attempt": key[1],
                    "initialOfen": initial,
                    "moves": moves,
                    "expectedPositions": positions,
                }
            )
            successful_plies += len(moves)
        if not requests and successful_plies != 0:
            raise AssertionError("rules replay request accounting changed")
        if not requests:
            return {
                "schemaVersion": 1,
                "gate": gate,
                "contract": "ChessLib legal transcript replay",
                "rawEvent": {
                    "bytes": len(raw_prefix),
                    "sha256": hashlib.sha256(raw_prefix).hexdigest(),
                },
                "transcriptRequests": 0,
                "successfulPlies": 0,
                "parityMatches": 0,
                "illegalOrMalformedMoves": 0,
                "normalizedTranscriptSha256": hashlib.sha256(b"").hexdigest(),
                "managedOutputSemanticSha256": hashlib.sha256(b"").hexdigest(),
                "dotnetHost": protocol.identity(dotnet, relative=False),
                "runtimeBundle": bundle,
                "helper": protocol.identity(assembly, relative=False),
                "rules": replay_rules_identity,
                "omegaMatchRules": omega_match_rules_identity,
                "rulesByteParityWithOmegaMatch": True,
                "passes": True,
            }
        projection = root / "requests.jsonl"
        output = root / "positions.jsonl"
        manifest = root / "manifest.json"
        projection.write_bytes(
            b"".join(
                (
                    json.dumps(item, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode("utf-8")
                for item in requests
            )
        )
        subprocess.run(
            [
                str(dotnet), str(assembly),
                "--input", str(projection),
                "--output", str(output),
                "--manifest", str(manifest),
            ],
            cwd=bundle_root,
            env=readiness._sanitized_environment(),
            check=True,
            capture_output=True,
            timeout=6 * 60 * 60,
        )
        output_records = [
            _strict_object_bytes(line, f"{gate} managed replay output")
            for line in output.read_bytes().splitlines()
            if line
        ]
        by_request: dict[int, list[dict[str, Any]]] = {}
        for record in output_records:
            request_id = record.get("requestId")
            if type(request_id) is not int:
                raise ValueError(f"{gate} managed replay request id changed")
            by_request.setdefault(request_id, []).append(record)
        semantic_output: list[dict[str, Any]] = []
        parity = 0
        for request in requests:
            request_id = int(request["requestId"])
            emitted = sorted(
                by_request.get(request_id, []), key=lambda item: item.get("ply", -1)
            )
            expected_positions = [request["initialOfen"], *request["expectedPositions"]]
            if len(emitted) != len(expected_positions):
                raise ValueError(f"{gate} managed replay prefix count changed")
            for ply, (record, expected_ofen) in enumerate(
                zip(emitted, expected_positions)
            ):
                expected_move = None if ply == 0 else request["moves"][ply - 1]
                if (
                    record.get("kind")
                    != "omega-opening-prefix-replay-position-v1"
                    or record.get("ply") != ply
                    or record.get("move") != expected_move
                    or record.get("ofen") != expected_ofen
                ):
                    raise ValueError(
                        f"{gate} managed rules replay diverged at request "
                        f"{request_id} ply {ply}"
                    )
                semantic_output.append(
                    {
                        "requestId": request_id,
                        "ply": ply,
                        "move": expected_move,
                        "ofen": expected_ofen,
                    }
                )
                if ply:
                    parity += 1
        manifest_value = protocol.strict_load(manifest, f"{gate} replay manifest")
        runtime = protocol.mapping(
            manifest_value.get("runtime"), f"{gate} replay manifest runtime"
        )
        protocol.require_exact_json(
            runtime.get("helper"),
            protocol.identity(assembly, relative=False),
            f"{gate} replay helper identity",
        )
        protocol.require_exact_json(
            runtime.get("rules"),
            protocol.identity(rules, relative=False),
            f"{gate} replay rules identity",
        )
    normalized_bytes = b"".join(
        (
            json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        for item in normalized
    )
    semantic_bytes = b"".join(
        (
            json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        for item in semantic_output
    )
    return {
        "schemaVersion": 1,
        "gate": gate,
        "contract": "ChessLib legal transcript replay",
        "rawEvent": {
            "bytes": len(raw_prefix),
            "sha256": hashlib.sha256(raw_prefix).hexdigest(),
        },
        "transcriptRequests": len(normalized),
        "successfulPlies": successful_plies,
        "parityMatches": parity,
        "illegalOrMalformedMoves": 0,
        "normalizedTranscriptSha256": hashlib.sha256(normalized_bytes).hexdigest(),
        "managedOutputSemanticSha256": hashlib.sha256(semantic_bytes).hexdigest(),
        "dotnetHost": protocol.identity(dotnet, relative=False),
        "runtimeBundle": bundle,
        "helper": protocol.identity(assembly, relative=False),
        "rules": replay_rules_identity,
        "omegaMatchRules": omega_match_rules_identity,
        "rulesByteParityWithOmegaMatch": True,
        "passes": parity == successful_plies,
    }


def _verify_authorized_gate_identities(context: Context) -> None:
    protocol.require_exact_json(
        protocol.mapping(
            context.authorization.get("stageSeeds"),
            "authorization stage seeds",
        ),
        context.stage_seeds,
        "authorization claim-bound stage seeds",
    )
    if set(context.core_seal.get("gates", {})) != set(GATES):
        raise ValueError("shared core seal gate inventory changed")
    for gate in GATES:
        config_identity = _config_identity(context, gate)
        suite_identity = _suite_identity(context, gate)
        config_path = _verify_identity(
            config_identity, f"{gate} authorization config"
        )
        _verify_identity(suite_identity, f"{gate} authorization suite")
        config = protocol.strict_load(config_path, f"{gate} authorized config")
        if (
            type(config.get("seed")) is not int
            or config.get("seed") != context.stage_seeds[gate]
            or core.GATE_SPECS[gate].get("seed") != context.stage_seeds[gate]
        ):
            raise ValueError(f"{gate} config escaped its claim-bound stage seed")
        entry = protocol.mapping(context.core_seal["gates"].get(gate), f"{gate} core seal")
        _same_identity(entry.get("config"), config_identity, f"{gate} core config")
        _same_identity(entry.get("suite"), suite_identity, f"{gate} core suite")


def _protected(context: Context, gate: str) -> dict[str, Any]:
    value = {
        "authorization": context.authorization_identity,
        "implementationSeal": context.implementation_seal_identity,
        "attemptReservation": protocol.identity(
            _path_entry(context.paths, "reservation")
        ),
        "candidateClaim": context.claim_identity,
        "jointSuiteSeal": context.suite_seal_identity,
        "coreSeal": context.core_seal_identity,
        "engine": copy.deepcopy(context.authorization["engine"]),
        "network": copy.deepcopy(context.authorization["selectedNetwork"]),
        "dotnetHost": copy.deepcopy(context.authorization["dotnetHost"]),
        "dotnetRuntimeManifest": copy.deepcopy(
            context.authorization["dotnetRuntimeManifest"]
        ),
        "matchCoreSource": copy.deepcopy(context.authorization["matchCoreSource"]),
        "omegaMatchAssembly": copy.deepcopy(
            context.authorization["omegaMatchAssembly"]
        ),
        "omegaMatchAppHost": copy.deepcopy(
            context.authorization["omegaMatchAppHost"]
        ),
        "config": _config_identity(context, gate),
        "suite": _suite_identity(context, gate),
        "orchestrator": copy.deepcopy(context.authorization["orchestrator"]),
        "dotnetRuntimeBundle": copy.deepcopy(
            context.authorization["dotnetRuntimeBundle"]
        ),
        "omegaMatchBundle": copy.deepcopy(context.authorization["omegaMatchBundle"]),
    }
    for label, identity_record in value.items():
        if label not in {"dotnetRuntimeBundle", "omegaMatchBundle"}:
            _verify_identity(identity_record, f"protected {label}")
    return value


def _rehash_context(context: Context, gate: str) -> dict[str, Any]:
    _assert_exact_bindings()
    readiness.verify_implementation_seal(
        _path_entry(context.paths, "implementationSeal"),
        orchestrator_path=Path(__file__),
    )
    readiness.verify_attempt_reservation(
        _path_entry(context.paths, "reservation"), context.attempt_index
    )
    current_claim = readiness.verify_candidate_claim(
        _path_entry(context.paths, "claim"), context.attempt_index
    )
    protocol.require_exact_json(current_claim, context.claim, "candidate claim rehash")
    _same_identity(
        current_claim.get("attemptReservation"),
        protocol.identity(_path_entry(context.paths, "reservation")),
        "candidate claim reservation rehash",
    )
    protocol.require_exact_json(
        _claim_stage_seeds(current_claim, context.attempt_index),
        context.stage_seeds,
        "candidate claim stage-seed rehash",
    )
    current_suite = readiness.verify_suite_seal(
        _path_entry(context.paths, "jointSuiteSeal"),
        context.attempt_index,
        deep=False,
    )
    protocol.require_exact_json(
        current_suite, context.suite_seal, "joint-suite seal rehash"
    )
    protocol.require_exact_json(
        protocol.mapping(
            current_suite.get("stageSeeds"), "joint-suite stage seeds"
        ),
        context.stage_seeds,
        "joint-suite stage-seed rehash",
    )
    current = readiness._verify_runtime_authorization(
        _verify_identity(
            context.authorization_identity, "authorization rehash path"
        ),
        attempt_index=context.attempt_index,
        protocol=context.protocol_value,
    )
    protocol.require_exact_json(current, context.authorization, "authorization rehash")
    readiness._verify_core_seal(
        _verify_identity(context.core_seal_identity, "core-seal rehash path"),
        context.attempt_index,
        suites=protocol.mapping(context.authorization.get("suites"), "authorization suites"),
        configs=protocol.mapping(context.authorization.get("configs"), "authorization configs"),
        network=protocol.mapping(
            context.authorization.get("selectedNetwork"), "authorization selected network"
        ),
        engine=protocol.mapping(context.authorization.get("engine"), "authorization engine"),
        harness=protocol.mapping(
            context.authorization.get("omegaMatchAssembly"),
            "authorization OmegaMatch assembly",
        ),
        harness_bundle=protocol.mapping(
            context.authorization.get("omegaMatchBundle"),
            "authorization OmegaMatch bundle",
        ),
    )
    _verify_authorized_gate_identities(context)
    current_protected = _protected(context, gate)
    return current_protected


def _verify_protected(context: Context, gate: str, expected: Any) -> None:
    actual = _rehash_context(context, gate)
    protocol.require_exact_json(actual, expected, f"{gate} protected identities")


def _verify_active_launch_state(
    context: Context,
    gate: str,
    sequence: int,
    intent: Mapping[str, Any],
) -> None:
    """Rehash all terminal state at the last boundary around OmegaMatch."""

    chain = readiness.verify_attempt_chain(
        context.protocol_value, through_attempt=context.attempt_index
    )
    if (
        chain.get("activeAttempt") != context.attempt_index
        or chain.get("confirmedAttempt") is not None
        or _path_entry(context.paths, "attemptClosure").exists()
        or _path_entry(context.paths, "programSuccess").exists()
        or _decision_path(context, gate).exists()
        or _completion_path(context, gate, sequence).exists()
    ):
        raise RuntimeError(f"{gate} launch became terminal before completion")
    _same_identity(
        intent.get("authorization"),
        context.authorization_identity,
        f"{gate} live authorization",
    )
    protocol.require_exact_json(
        protocol.strict_load(
            _intent_path(context, gate, sequence), f"{gate} live launch intent"
        ),
        dict(intent),
        f"{gate} live launch intent",
    )
    protocol.require_exact_json(
        intent.get("predecessorDecision"),
        _predecessor_decision(context, gate),
        f"{gate} live predecessor",
    )
    if gate in TIMED_GATES:
        _verify_idle(context, gate)
        protocol.require_exact_json(
            intent.get("idleAttestation"),
            protocol.identity(_idle_path(context, gate)),
            f"{gate} live idle attestation",
        )
    _verify_protected(context, gate, intent.get("protected"))


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
    environment = readiness._sanitized_environment()
    if os.name == "nt":
        result = subprocess.run(
            [
                str(Path(environment["SystemRoot"]) / "System32/tasklist.exe"),
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
        )
        relevant = _parse_windows_processes(result.stdout)
        source = "tasklist-csv"
    else:
        result = subprocess.run(
            ["/bin/ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
        )
        relevant = _parse_posix_processes(result.stdout)
        source = "ps-pid-args"
    return {
        "capturedUtc": _utc_now(),
        "source": source,
        "relevantProcesses": relevant,
    }


def _validate_process_snapshot(value: Any, label: str) -> dict[str, Any]:
    snapshot = protocol.mapping(value, label)
    if set(snapshot) != {"capturedUtc", "source", "relevantProcesses"}:
        raise ValueError(f"{label} field inventory changed")
    _canonical_utc(snapshot.get("capturedUtc"), f"{label} capturedUtc")
    if snapshot.get("source") not in {"tasklist-csv", "ps-pid-args"}:
        raise ValueError(f"{label} source changed")
    processes = snapshot.get("relevantProcesses")
    if type(processes) is not list:
        raise ValueError(f"{label} relevantProcesses must be a list")
    normalized: list[dict[str, Any]] = []
    for item in processes:
        if type(item) is not dict or set(item) != {"name", "pid"}:
            raise ValueError(f"{label} process entry changed")
        if (
            item.get("name") not in RELEVANT_PROCESS_NAMES
            or type(item.get("pid")) is not int
            or item["pid"] <= 0
        ):
            raise ValueError(f"{label} process entry is malformed")
        normalized.append(dict(item))
    if normalized != sorted(
        normalized, key=lambda item: (item["name"], item["pid"])
    ) or len({(item["name"], item["pid"]) for item in normalized}) != len(
        normalized
    ):
        raise ValueError(f"{label} process entries are unsorted or repeated")
    return snapshot


def _require_idle_processes(label: str) -> dict[str, Any]:
    snapshot = _process_snapshot()
    if snapshot["relevantProcesses"]:
        raise RuntimeError(f"{label} requires no dotnet/OmegaMatch/Senpai processes")
    return snapshot


def _directory_records(root: Path, suffix: str, label: str) -> list[Path]:
    if not root.exists():
        return []
    if not root.is_dir():
        raise ValueError(f"{label} path is not a directory")
    paths = sorted(root.iterdir(), key=lambda item: item.name)
    expected = [f"{index:06d}{suffix}" for index in range(1, len(paths) + 1)]
    if [path.name for path in paths] != expected or any(
        not path.is_file() for path in paths
    ):
        raise ValueError(f"{label} sequence has a gap or extra artifact")
    return paths


def _launch_records(context: Context, gate: str) -> dict[tuple[int, str], Path]:
    root = _launch_dir(context, gate)
    if not root.exists():
        return {}
    if not root.is_dir():
        raise ValueError(f"{gate} launches path is not a directory")
    result: dict[tuple[int, str], Path] = {}
    pattern = re.compile(r"^([0-9]{6})\.(intent|completion)\.json$")
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        match = pattern.fullmatch(path.name)
        if match is None or not path.is_file():
            raise ValueError(f"{gate} launches contain an extra artifact: {path.name}")
        sequence = int(match.group(1))
        if sequence < 1 or f"{sequence:06d}" != match.group(1):
            raise ValueError(f"{gate} launch sequence is noncanonical")
        result[(sequence, match.group(2))] = path
    intents = sorted(sequence for sequence, kind in result if kind == "intent")
    completions = sorted(
        sequence for sequence, kind in result if kind == "completion"
    )
    if intents != list(range(1, len(intents) + 1)):
        raise ValueError(f"{gate} intent sequence has a gap")
    if completions != list(range(1, len(completions) + 1)):
        raise ValueError(f"{gate} completion sequence has a gap")
    if len(intents) < len(completions) or len(intents) > len(completions) + 1:
        raise ValueError(f"{gate} launch chain is imbalanced")
    return result


def _prior_identity(path: Path | None) -> dict[str, Any] | None:
    return None if path is None else protocol.identity(path)


def _predecessor_decision(context: Context, gate: str) -> dict[str, Any] | None:
    predecessor = PREDECESSOR[gate]
    if predecessor is None:
        return None
    path = _decision_path(context, predecessor)
    if not path.is_file():
        raise FileNotFoundError(f"{gate} requires a {predecessor} decision")
    decision = _verify_decision(context, predecessor, replay=False)
    if decision["decision"] != SUCCESS_DECISION[predecessor]:
        raise ValueError(f"{gate} predecessor did not succeed")
    return protocol.identity(path)


def _intent_value(
    context: Context,
    gate: str,
    sequence: int,
    prior_assessment: dict[str, Any] | None,
    prior_completion: dict[str, Any] | None,
    prelaunch: dict[str, Any],
) -> dict[str, Any]:
    stage = context.protocol_value["stages"][gate]
    pair_budget = (
        stage["initialPairBudget"]
        if sequence == 1
        else stage["resumePairBudget"]
    )
    events_before = (
        None
        if not _events(context, gate).exists()
        else protocol.identity(_events(context, gate))
    )
    idle = None
    if gate in TIMED_GATES:
        _verify_idle(context, gate)
        idle = protocol.identity(_idle_path(context, gate))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": INTENT_KIND,
        "protocolId": PROTOCOL_ID,
        "attemptIndex": context.attempt_index,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": _utc_now(),
        "action": "run" if sequence == 1 else "resume",
        "pairBudget": pair_budget,
        "authorization": context.authorization_identity,
        "predecessorDecision": _predecessor_decision(context, gate),
        "idleAttestation": idle,
        "priorAssessment": prior_assessment,
        "priorCompletion": prior_completion,
        "eventsBefore": events_before,
        "protected": _protected(context, gate),
        "preLaunchProcesses": prelaunch,
    }


def _validate_intent(
    context: Context,
    gate: str,
    sequence: int,
    value: Mapping[str, Any],
    prior_assessment: dict[str, Any] | None,
    prior_completion: dict[str, Any] | None,
    *,
    expected_predecessor: Any = _UNSET,
    expected_idle: Any = _UNSET,
    expected_protected: Mapping[str, Any] | None = None,
    verify_events: bool = True,
) -> None:
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocolId",
        "attemptIndex",
        "gate",
        "sequence",
        "createdUtc",
        "action",
        "pairBudget",
        "authorization",
        "predecessorDecision",
        "idleAttestation",
        "priorAssessment",
        "priorCompletion",
        "eventsBefore",
        "protected",
        "preLaunchProcesses",
    }
    if set(value) != expected_fields:
        raise ValueError(f"{gate} intent field inventory changed")
    stage = context.protocol_value["stages"][gate]
    budget = stage["initialPairBudget"] if sequence == 1 else stage["resumePairBudget"]
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != INTENT_KIND
        or value.get("protocolId") != PROTOCOL_ID
        or value.get("attemptIndex") != context.attempt_index
        or value.get("gate") != gate
        or value.get("sequence") != sequence
        or value.get("action") != ("run" if sequence == 1 else "resume")
        or value.get("pairBudget") != budget
    ):
        raise ValueError(f"{gate} intent envelope changed")
    for field in ("attemptIndex", "sequence", "pairBudget"):
        if type(value.get(field)) is not int:
            raise ValueError(f"{gate} intent {field} is not an exact integer")
    created = _canonical_utc(value.get("createdUtc"), f"{gate} intent createdUtc")
    if created < _canonical_utc(
        context.authorization.get("createdUtc"), "authorization createdUtc"
    ):
        raise ValueError(f"{gate} intent predates authorization")
    _same_identity(value.get("authorization"), context.authorization_identity, "authorization")
    protocol.require_exact_json(
        value.get("priorAssessment"), prior_assessment, f"{gate} prior assessment"
    )
    protocol.require_exact_json(
        value.get("priorCompletion"), prior_completion, f"{gate} prior completion"
    )
    if expected_predecessor is _UNSET:
        expected_predecessor = _predecessor_decision(context, gate)
    protocol.require_exact_json(
        value.get("predecessorDecision"),
        expected_predecessor,
        f"{gate} predecessor decision",
    )
    if expected_idle is _UNSET:
        expected_idle = None
        if gate in TIMED_GATES:
            _verify_idle(context, gate)
            expected_idle = protocol.identity(_idle_path(context, gate))
    protocol.require_exact_json(
        value.get("idleAttestation"), expected_idle, f"{gate} idle attestation"
    )
    for record, prior_label in (
        (expected_predecessor, "predecessor decision"),
        (expected_idle, "idle attestation"),
        (prior_assessment, "prior assessment"),
        (prior_completion, "prior completion"),
    ):
        if record is None:
            continue
        prior_value = protocol.strict_load(
            _verify_identity(record, f"{gate} {prior_label} identity"),
            f"{gate} {prior_label}",
        )
        if created < _canonical_utc(
            prior_value.get("createdUtc"), f"{gate} {prior_label} createdUtc"
        ):
            raise ValueError(f"{gate} intent predates {prior_label}")
    before = value.get("eventsBefore")
    if sequence == 1:
        if before is not None:
            raise ValueError(f"{gate} initial intent has prior events")
    else:
        if before is None:
            raise ValueError(f"{gate} resume intent lacks prior events")
        _identity_shape(before, f"{gate} intent events-before")
        if verify_events:
            _event_prefix(
                _events(context, gate), before, exact=False, label=f"{gate} intent prefix"
            )
        if prior_assessment is None:
            raise ValueError(f"{gate} resume lacks a prior assessment")
        prior_value = protocol.strict_load(
            _verify_identity(
                prior_assessment, f"{gate} prior-assessment identity"
            ),
            f"{gate} prior assessment",
        )
        protocol.require_exact_json(
            before, prior_value.get("events"), f"{gate} resume checkpoint"
        )
    snapshot = _validate_process_snapshot(
        value.get("preLaunchProcesses"), f"{gate} prelaunch processes"
    )
    if _canonical_utc(
        snapshot.get("capturedUtc"), f"{gate} prelaunch capturedUtc"
    ) > created:
        raise ValueError(f"{gate} prelaunch snapshot postdates intent")
    if snapshot.get("relevantProcesses") != []:
        raise ValueError(f"{gate} launch was not process-idle")
    if expected_protected is None:
        _verify_protected(context, gate, value.get("protected"))
    else:
        protocol.require_exact_json(
            value.get("protected"),
            expected_protected,
            f"{gate} protected intent identities",
        )


def _completion_value(
    context: Context,
    gate: str,
    sequence: int,
    intent: Mapping[str, Any],
    *,
    return_code: int | None,
    recovered: bool,
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": COMPLETION_KIND,
        "protocolId": PROTOCOL_ID,
        "attemptIndex": context.attempt_index,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": _utc_now(),
        "intent": protocol.identity(_intent_path(context, gate, sequence)),
        "eventsAfter": protocol.identity(_events(context, gate)),
        "returnCode": return_code,
        "recoveredFromEventGrowth": recovered,
        "postLaunchProcesses": _process_snapshot(),
        "postLaunchProtected": _rehash_context(context, gate),
    }


def _validate_completion(
    context: Context,
    gate: str,
    sequence: int,
    value: Mapping[str, Any],
    intent: Mapping[str, Any],
    *,
    expected_protected: Mapping[str, Any] | None = None,
    verify_events: bool = True,
) -> None:
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocolId",
        "attemptIndex",
        "gate",
        "sequence",
        "createdUtc",
        "intent",
        "eventsAfter",
        "returnCode",
        "recoveredFromEventGrowth",
        "postLaunchProcesses",
        "postLaunchProtected",
    }
    if set(value) != expected_fields:
        raise ValueError(f"{gate} completion field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != COMPLETION_KIND
        or value.get("protocolId") != PROTOCOL_ID
        or value.get("attemptIndex") != context.attempt_index
        or value.get("gate") != gate
        or value.get("sequence") != sequence
        or type(value.get("recoveredFromEventGrowth")) is not bool
        or (
            value.get("returnCode") is not None
            and type(value.get("returnCode")) is not int
        )
    ):
        raise ValueError(f"{gate} completion envelope changed")
    for field in ("attemptIndex", "sequence"):
        if type(value.get(field)) is not int:
            raise ValueError(f"{gate} completion {field} is not exact integer")
    created = _canonical_utc(value.get("createdUtc"), f"{gate} completion createdUtc")
    if created < _canonical_utc(intent.get("createdUtc"), f"{gate} intent createdUtc"):
        raise ValueError(f"{gate} completion predates intent")
    _same_identity(
        value.get("intent"),
        protocol.identity(_intent_path(context, gate, sequence)),
        f"{gate} completion intent",
    )
    after = _identity_shape(value.get("eventsAfter"), f"{gate} completion events")
    prefix: bytes | None = None
    if verify_events:
        prefix = _event_prefix(
            _events(context, gate), after, exact=False, label=f"{gate} completion events"
        )
    before = intent.get("eventsBefore")
    before_count = 0 if before is None else before["bytes"]
    if after["bytes"] <= before_count:
        raise ValueError(f"{gate} completion records no event growth")
    if before is not None:
        _identity_shape(before, f"{gate} completion prior events")
        if verify_events:
            _event_prefix(
                _events(context, gate), before, exact=False, label=f"{gate} append-only prefix"
            )
    # Parsing the complete prefix prevents a completion checkpoint from
    # authenticating a partial or malformed JSONL append.
    if prefix is not None:
        records = _strict_event_log(prefix, f"{gate} completion events")
        _verify_event_run_binding(context, gate, records)
    snapshot = _validate_process_snapshot(
        value.get("postLaunchProcesses"), f"{gate} postlaunch processes"
    )
    if _canonical_utc(
        snapshot.get("capturedUtc"), f"{gate} postlaunch capturedUtc"
    ) < created:
        raise ValueError(f"{gate} postlaunch snapshot predates completion")
    if expected_protected is None:
        _verify_protected(context, gate, value.get("postLaunchProtected"))
    else:
        protocol.require_exact_json(
            value.get("postLaunchProtected"),
            expected_protected,
            f"{gate} postlaunch protected identities",
        )


def _events_grew(context: Context, gate: str, intent: Mapping[str, Any]) -> bool:
    path = _events(context, gate)
    if not path.is_file():
        return False
    before = intent.get("eventsBefore")
    if before is None:
        return path.stat().st_size > 0
    _event_prefix(path, before, exact=False, label=f"{gate} pending intent prefix")
    return path.stat().st_size > before["bytes"]


def _publish_completion(
    context: Context,
    gate: str,
    sequence: int,
    intent: Mapping[str, Any],
    *,
    return_code: int | None,
    recovered: bool,
) -> dict[str, Any]:
    if not _events_grew(context, gate, intent):
        raise FileNotFoundError(f"{gate} launch produced no event growth")
    value = _completion_value(
        context,
        gate,
        sequence,
        intent,
        return_code=return_code,
        recovered=recovered,
    )
    _exclusive_json(_completion_path(context, gate, sequence), value)
    _validate_completion(context, gate, sequence, value, intent)
    return value


def _normalize_report(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("createdUtc", None)
    events = result.get("events")
    if type(events) is dict:
        events.pop("path", None)
    return result


def _recompute_report(
    context: Context, gate: str, event_identity: Mapping[str, Any]
) -> dict[str, Any]:
    prefix = _event_prefix(
        _events(context, gate),
        event_identity,
        exact=False,
        label=f"{gate} assessment replay",
    )
    records = _strict_event_log(prefix, f"{gate} assessment replay")
    _verify_event_run_binding(context, gate, records)
    suite_identity = _suite_identity(context, gate)
    config_identity = _config_identity(context, gate)
    suite = protocol.strict_load(
        _verify_identity(suite_identity, f"{gate} suite"), f"{gate} suite"
    )
    config = protocol.strict_load(
        _verify_identity(config_identity, f"{gate} config"), f"{gate} config"
    )
    schedule_audit = _authenticate_raw_event_schedule(
        records=records,
        suite=suite,
        config=config,
        suite_identity=suite_identity,
        config_identity=config_identity,
        gate=gate,
    )
    rules_replay = _authenticate_successful_ply_rules_replay(
        context, gate, prefix, records
    )
    color_prefix, color_audit = _adapt_pinned_emitter_colors(
        prefix, label=f"{gate} assessment replay"
    )
    protocol.require_exact_json(
        color_audit["raw"],
        {
            "bytes": event_identity["bytes"],
            "sha256": event_identity["sha256"],
        },
        f"{gate} color adapter raw prefix",
    )
    color_audit["rawEventIdentity"] = dict(event_identity)
    derived_prefix, terminal_audit = _adapt_process_exit_terminals_for_frozen_core(
        color_prefix,
        expected_transforms=int(schedule_audit["processExitTerminalPlies"]),
        label=f"{gate} assessment replay after color adapter",
    )
    protocol.require_exact_json(
        terminal_audit["input"],
        color_audit["derived"],
        f"{gate} terminal adapter input",
    )
    raw_authentication = {
        "schemaVersion": 1,
        "schedule": schedule_audit,
        "rulesReplay": rules_replay,
        "colorAdapter": color_audit,
        "terminalAdapter": terminal_audit,
        "preparedCompatibilityView": {
            "raw": {
                "bytes": event_identity["bytes"],
                "sha256": event_identity["sha256"],
            },
            "derived": terminal_audit["derived"],
            "changedFields": {
                "Color": color_audit["transformedPlies"],
                "Error": terminal_audit["transformedTerminalPlies"],
            },
            "totalFieldsChanged": (
                color_audit["transformedPlies"]
                + terminal_audit["transformedTerminalPlies"]
            ),
            "unauthorizedFieldsChanged": 0,
            "rawEvidenceRetained": True,
        },
        "assessmentInput": (
            {
                "view": "authenticated-raw-events",
                "identity": {
                    "bytes": event_identity["bytes"],
                    "sha256": event_identity["sha256"],
                },
            }
            if gate == "normal-start-clock"
            else {
                "view": "frozen-core-compatibility-events",
                "identity": terminal_audit["derived"],
            }
        ),
    }
    if gate == "normal-start-clock":
        report = readiness._PRACTICAL_ASSESS_EVENTS(
            suite=suite,
            events=records,
            seed=context.stage_seeds[gate],
            promotion_log_threshold=context.promotion_log_threshold,
            config=config,
            suite_identity=suite_identity,
            config_identity=config_identity,
        )
        if type(report) is not dict:
            raise ValueError("practical match assessor returned a non-object")
        safety = protocol.mapping(report.get("safety"), "normal-start-clock safety")
        failure_fields = (
            "illegalMoves",
            "illegalPvs",
            "protocolFailures",
            "timeForfeits",
            "abandonedAttempts",
        )
        failures = sum(int(safety.get(field, -1)) for field in failure_fields)
        safety["failures"] = failures
        safety["passes"] = failures == 0 and report.get("zeroSafetyFailures") is True
        report["safety"] = safety
        report["rawEventAuthentication"] = raw_authentication
        return report
    with tempfile.TemporaryDirectory(prefix="omega-confirmation-assess-") as directory:
        root = Path(directory)
        events = root / "events.jsonl"
        output = root / "assessment.json"
        events.write_bytes(derived_prefix)
        arguments = argparse.Namespace(
            seal=_verify_identity(
                context.core_seal_identity, "assessment core seal"
            ),
            gate=gate,
            events=events,
            output=output,
            idle_attestation=(
                _idle_path(context, gate) if gate == "equal-time" else None
            ),
        )
        report = core._assess(arguments)
    if type(report) is not dict:
        raise ValueError("shared match core returned a non-object assessment")
    report["rawEventAuthentication"] = raw_authentication
    return report


def _validate_formal_report(context: Context, gate: str, report: Mapping[str, Any]) -> None:
    section = protocol.mapping(report.get("sequentialGate"), f"{gate} sequential gate")
    if (
        section.get("minimumPairs") != 128
        or section.get("maximumPairs") != 512
        or section.get("nullElo") != 15.0
        or section.get("promotionThreshold") != context.promotion_e_value
        or section.get("futilityThreshold") != 20.0
    ):
        raise ValueError(f"{gate} sequential parameters changed")
    count = section.get("pairCount")
    if type(count) is not int or count < 0 or count > 512:
        raise ValueError(f"{gate} pair count is malformed")
    checkpoints = section.get("checkpoints")
    if type(checkpoints) is not list:
        raise ValueError(f"{gate} checkpoints are missing")
    for checkpoint in checkpoints:
        if type(checkpoint) is not dict:
            raise ValueError(f"{gate} checkpoint is malformed")
        pairs = checkpoint.get("pairs")
        if type(pairs) is not int or pairs % 4 != 0 or pairs > count:
            raise ValueError(f"{gate} checkpoint is not a complete four-phase block")
    # The shared core receives an upward-rounded linear image of the frozen
    # log threshold.  Reassert the exact log-domain rule at every reported
    # promotion so a future float refactor cannot make the test liberal.
    if section.get("decision") == "promote":
        signal_pair = section.get("signalPair")
        if type(signal_pair) is not int or signal_pair < 128 or signal_pair % 4:
            raise ValueError(f"{gate} promotion did not latch at an eligible block")
        signal = next(
            (
                item
                for item in checkpoints
                if item.get("pairs") == signal_pair
            ),
            None,
        )
        if signal is None:
            raise ValueError(f"{gate} promotion signal checkpoint is missing")
        promotion_e = signal.get("promotionEValue")
        if (
            type(promotion_e) not in (int, float)
            or type(promotion_e) is bool
            or not math.isfinite(float(promotion_e))
            or float(promotion_e) <= 0
            or math.log(float(promotion_e)) < context.promotion_log_threshold
        ):
            raise ValueError(f"{gate} promotion fails conservative log threshold")


def _budget_check(
    context: Context,
    gate: str,
    report: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    sequence: int,
) -> dict[str, Any]:
    progress = protocol.mapping(report.get("progress"), f"{gate} progress")
    stage = context.protocol_value["stages"][gate]
    budget = stage["initialPairBudget"] if sequence == 1 else stage["resumePairBudget"]
    if budget % 4 or (sequence > 1 and budget != 4):
        raise ValueError(f"{gate} launch budget changed")
    before = (
        {"finishedGames": 0, "completePairs": 0, "balancedPrefixPairs": 0}
        if prior is None
        else prior["progress"]
    )
    limits = {
        "finishedGames": budget * 2,
        "completePairs": budget,
        "balancedPrefixPairs": budget,
    }
    deltas: dict[str, int] = {}
    for field, maximum in limits.items():
        current = progress.get(field)
        previous = before.get(field)
        if type(current) is not int or type(previous) is not int:
            raise ValueError(f"{gate} {field} is not an exact integer")
        if current < previous or current - previous > maximum:
            raise ValueError(f"{gate} {field} exceeds its launch budget")
        deltas[field] = current - previous
    if progress.get("completePairs", 0) > stage["maximumPairs"]:
        raise ValueError(f"{gate} exceeded its maximum pair cap")
    return {
        "pairBudget": budget,
        "deltas": deltas,
        "expectedDeltas": limits,
        "exact": deltas == limits,
    }


def _assessment_decision(
    context: Context,
    gate: str,
    report: Mapping[str, Any],
    completion: Mapping[str, Any],
) -> str:
    raw_authentication = protocol.mapping(
        report.get("rawEventAuthentication"), f"{gate} raw event authentication"
    )
    schedule_authentication = protocol.mapping(
        raw_authentication.get("schedule"), f"{gate} raw schedule authentication"
    )
    rules_replay = protocol.mapping(
        raw_authentication.get("rulesReplay"), f"{gate} raw rules replay"
    )
    color_adapter = protocol.mapping(
        raw_authentication.get("colorAdapter"), f"{gate} color adapter"
    )
    terminal_adapter = protocol.mapping(
        raw_authentication.get("terminalAdapter"), f"{gate} terminal adapter"
    )
    prepared_view = protocol.mapping(
        raw_authentication.get("preparedCompatibilityView"),
        f"{gate} prepared compatibility view",
    )
    assessment_input = protocol.mapping(
        raw_authentication.get("assessmentInput"),
        f"{gate} actual assessment input",
    )
    replay_rules_identity = _identity_shape(
        rules_replay.get("rules"), f"{gate} replay rules identity"
    )
    omega_match_rules_identity = _identity_shape(
        rules_replay.get("omegaMatchRules"),
        f"{gate} OmegaMatch rules identity",
    )
    replay_helper_identity = _identity_shape(
        rules_replay.get("helper"), f"{gate} replay helper identity"
    )
    replay_dotnet_identity = _identity_shape(
        rules_replay.get("dotnetHost"), f"{gate} replay dotnet identity"
    )
    authorized_dotnet_identity = _identity_shape(
        context.authorization.get("dotnetHost"),
        f"{gate} authorized dotnet identity",
    )
    implementation_pins = protocol.mapping(
        context.implementation_seal.get("pinned"),
        f"{gate} implementation pins",
    )
    color_changes = color_adapter.get("transformedPlies")
    terminal_changes = terminal_adapter.get("transformedTerminalPlies")
    total_changes = prepared_view.get("totalFieldsChanged")
    compatibility_counts_are_exact = all(
        type(value) is int and value >= 0
        for value in (color_changes, terminal_changes, total_changes)
    )
    raw_integrity = (
        compatibility_counts_are_exact
        and schedule_authentication.get("allStartsResultsAndPliesAuthenticated") is True
        and rules_replay.get("contract") == "ChessLib legal transcript replay"
        and rules_replay.get("illegalOrMalformedMoves") == 0
        and rules_replay.get("parityMatches")
        == rules_replay.get("successfulPlies")
        and rules_replay.get("successfulPlies")
        == schedule_authentication.get("successfulPlies")
        and rules_replay.get("rulesByteParityWithOmegaMatch") is True
        and replay_rules_identity["bytes"] == _IMPORTED_G5_RULES_BYTES
        and replay_rules_identity["sha256"] == _IMPORTED_G5_RULES_SHA256
        and omega_match_rules_identity["bytes"] == _IMPORTED_G5_RULES_BYTES
        and omega_match_rules_identity["sha256"] == _IMPORTED_G5_RULES_SHA256
        and _resolved_identity_path(
            replay_rules_identity, f"{gate} replay rules"
        )
        == (_IMPORTED_PREFIX_REPLAY_ROOT / "ChessLib.dll")
        and _resolved_identity_path(
            omega_match_rules_identity, f"{gate} OmegaMatch rules"
        )
        == _IMPORTED_G5_OMEGA_MATCH_RULES_ASSEMBLY
        and protocol.exact_json_equal(
            rules_replay.get("runtimeBundle"),
            context.implementation_seal.get("prefixReplayBundle"),
        )
        and _resolved_identity_path(
            replay_helper_identity, f"{gate} replay helper"
        )
        == _IMPORTED_PREFIX_REPLAY_ROOT / "OmegaOpeningPrefixReplay.dll"
        and replay_helper_identity["bytes"]
        == _identity_shape(
            implementation_pins.get("prefixReplayAssembly"),
            f"{gate} sealed replay helper",
        )["bytes"]
        and replay_helper_identity["sha256"]
        == _identity_shape(
            implementation_pins.get("prefixReplayAssembly"),
            f"{gate} sealed replay helper",
        )["sha256"]
        and replay_dotnet_identity["bytes"]
        == authorized_dotnet_identity["bytes"]
        and replay_dotnet_identity["sha256"]
        == authorized_dotnet_identity["sha256"]
        and _resolved_identity_path(
            replay_dotnet_identity, f"{gate} replay dotnet"
        )
        == _resolved_identity_path(
            authorized_dotnet_identity, f"{gate} authorized dotnet"
        )
        and rules_replay.get("passes") is True
        and protocol.exact_json_equal(
            rules_replay.get("rawEvent"), color_adapter.get("raw")
        )
        and color_adapter.get("transformVersion") == COLOR_ADAPTER_VERSION
        and color_adapter.get("acceptedRawTokens") == ["white", "black"]
        and color_adapter.get("derivedTokens") == ["w", "b"]
        and color_adapter.get("otherFieldsChanged") == 0
        and color_adapter.get("rawEvidenceRetained") is True
        and terminal_adapter.get("transformVersion") == TERMINAL_ADAPTER_VERSION
        and terminal_adapter.get("errorFieldsChanged")
        == terminal_adapter.get("transformedTerminalPlies")
        and terminal_adapter.get("nonErrorFieldsChanged") == 0
        and terminal_adapter.get("rawEvidenceRetained") is True
        and terminal_adapter.get("transformedTerminalPlies")
        == schedule_authentication.get("processExitTerminalPlies")
        and protocol.exact_json_equal(
            terminal_adapter.get("input"), color_adapter.get("derived")
        )
        and protocol.exact_json_equal(
            prepared_view.get("raw"), color_adapter.get("raw")
        )
        and protocol.exact_json_equal(
            prepared_view.get("derived"), terminal_adapter.get("derived")
        )
        and prepared_view.get("changedFields")
        == {
            "Color": color_changes,
            "Error": terminal_changes,
        }
        and prepared_view.get("totalFieldsChanged")
        == color_changes + terminal_changes
        and prepared_view.get("unauthorizedFieldsChanged") == 0
        and prepared_view.get("rawEvidenceRetained") is True
        and assessment_input.get("view")
        == (
            "authenticated-raw-events"
            if gate == "normal-start-clock"
            else "frozen-core-compatibility-events"
        )
        and protocol.exact_json_equal(
            assessment_input.get("identity"),
            (
                color_adapter.get("raw")
                if gate == "normal-start-clock"
                else terminal_adapter.get("derived")
            ),
        )
    )
    safety = protocol.mapping(report.get("safety"), f"{gate} safety")
    zero_safety = (
        safety.get("failures") == 0
        and safety.get("passes") is True
        and all(
            safety.get(field) == 0
            for field in (
                "illegalMoves",
                "illegalPvs",
                "protocolFailures",
                "timeForfeits",
                "abandonedAttempts",
            )
        )
    )
    processes = protocol.mapping(
        completion.get("postLaunchProcesses"), f"{gate} postlaunch processes"
    )
    launch_safe = (
        completion.get("returnCode") == 0
        and completion.get("recoveredFromEventGrowth") is False
        and processes.get("relevantProcesses") == []
    )
    budget_audit = protocol.mapping(
        report.get("confirmationBudgetAudit"), f"{gate} budget audit"
    )
    launch_safe = launch_safe and budget_audit.get("exact") is True
    if not zero_safety or not launch_safe or not raw_integrity:
        return "safety-fail"
    if gate == "development":
        section = protocol.mapping(report.get("developmentScreen"), "development screen")
        decision = section.get("decision")
        if decision not in {"continue", "pass", "fail", "in-progress"}:
            raise ValueError("development decision changed")
        return "continue" if decision == "in-progress" else str(decision)
    _validate_formal_report(context, gate, report)
    section = report["sequentialGate"]
    decision = section.get("decision")
    if decision == "in-progress":
        return "continue"
    if decision not in {"continue", "promote", "futility", "inconclusive"}:
        raise ValueError(f"{gate} decision changed")
    if gate == "equal-time":
        audit = protocol.mapping(report.get("equalTimeAudit"), "equal-time audit")
        if (
            audit.get("serialized") is not True
            or audit.get("serializationComplete") is not True
            or audit.get("overlappingGames") != []
            or audit.get("unfinishedAttempts") != []
            or audit.get("missingTelemetrySearches") != 0
            or audit.get("invalidTelemetrySearches") != 0
            or audit.get("deadlineFailures") != 0
            or audit.get("integrityPasses") is not True
            or audit.get("passes") is not True
        ):
            return "safety-fail"
    if gate == "normal-start-clock":
        audit = protocol.mapping(
            report.get("clockAudit"), "normal-start-clock audit"
        )
        if (
            audit.get("serialized") is not True
            or audit.get("serializationComplete") is not True
            or audit.get("overlappingGames") != []
            or audit.get("unfinishedAttempts") != []
            or audit.get("invalidClockSearches") != 0
            or audit.get("deadlineFailures") != 0
            or audit.get("passes") is not True
            or type(audit.get("searches")) is not int
            or audit["searches"] < 1
            or type(audit.get("maximumClockRoundingErrorMs")) not in (int, float)
            or audit["maximumClockRoundingErrorMs"] > 2.0
        ):
            return "safety-fail"
    return str(decision)


def _assessment_value(
    context: Context,
    gate: str,
    sequence: int,
    completion: Mapping[str, Any],
    prior_assessment: dict[str, Any] | None,
) -> dict[str, Any]:
    current_events = protocol.identity(_events(context, gate))
    protocol.require_exact_json(
        completion.get("eventsAfter"), current_events, f"{gate} completion checkpoint"
    )
    report = _recompute_report(context, gate, current_events)
    report["events"] = current_events
    prior_value = (
        None
        if prior_assessment is None
        else protocol.strict_load(
            _verify_identity(
                prior_assessment, f"{gate} prior-assessment identity"
            ),
            f"{gate} prior assessment",
        )
    )
    report["confirmationBudgetAudit"] = _budget_check(
        context, gate, report, prior_value, sequence
    )
    decision = _assessment_decision(context, gate, report, completion)
    terminal = decision in TERMINAL_DECISIONS[gate]
    passed = terminal and decision == SUCCESS_DECISION[gate]
    successor = (
        GATES[GATES.index(gate) + 1]
        if passed and gate != GATES[-1]
        else None
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ASSESSMENT_KIND,
        "protocolId": PROTOCOL_ID,
        "attemptIndex": context.attempt_index,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": _utc_now(),
        "authorization": context.authorization_identity,
        "completion": protocol.identity(_completion_path(context, gate, sequence)),
        "priorAssessment": prior_assessment,
        "events": current_events,
        "rawEventAuthentication": copy.deepcopy(
            report["rawEventAuthentication"]
        ),
        "promotionLogThreshold": (
            context.promotion_log_threshold if gate in FORMAL_GATES else None
        ),
        "coreReport": report,
        "progress": copy.deepcopy(report["progress"]),
        "decision": decision,
        "terminal": terminal,
        "passed": passed,
        "authorizedSuccessor": successor,
        "zeroSafetyFailures": decision != "safety-fail"
        and report["safety"]["failures"] == 0,
    }


def _validate_assessment(
    context: Context,
    gate: str,
    sequence: int,
    value: Mapping[str, Any],
    prior_assessment: dict[str, Any] | None,
    *,
    replay: bool,
    verify_events: bool = True,
) -> None:
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocolId",
        "attemptIndex",
        "gate",
        "sequence",
        "createdUtc",
        "authorization",
        "completion",
        "priorAssessment",
        "events",
        "rawEventAuthentication",
        "promotionLogThreshold",
        "coreReport",
        "progress",
        "decision",
        "terminal",
        "passed",
        "authorizedSuccessor",
        "zeroSafetyFailures",
    }
    if set(value) != expected_fields:
        raise ValueError(f"{gate} assessment field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != ASSESSMENT_KIND
        or value.get("protocolId") != PROTOCOL_ID
        or value.get("attemptIndex") != context.attempt_index
        or value.get("gate") != gate
        or value.get("sequence") != sequence
        or type(value.get("terminal")) is not bool
        or type(value.get("passed")) is not bool
        or type(value.get("zeroSafetyFailures")) is not bool
    ):
        raise ValueError(f"{gate} assessment envelope changed")
    for field in ("attemptIndex", "sequence"):
        if type(value.get(field)) is not int:
            raise ValueError(f"{gate} assessment {field} is not exact integer")
    created = _canonical_utc(
        value.get("createdUtc"), f"{gate} assessment createdUtc"
    )
    _same_identity(value.get("authorization"), context.authorization_identity, "authorization")
    _same_identity(
        value.get("completion"),
        protocol.identity(_completion_path(context, gate, sequence)),
        f"{gate} assessment completion",
    )
    completion_value = protocol.strict_load(
        _completion_path(context, gate, sequence), f"{gate} assessment completion"
    )
    if created < _canonical_utc(
        completion_value.get("createdUtc"), f"{gate} completion createdUtc"
    ):
        raise ValueError(f"{gate} assessment predates completion")
    protocol.require_exact_json(
        value.get("priorAssessment"), prior_assessment, f"{gate} assessment prior"
    )
    expected_threshold = (
        context.promotion_log_threshold if gate in FORMAL_GATES else None
    )
    protocol.require_exact_json(
        value.get("promotionLogThreshold"),
        expected_threshold,
        f"{gate} promotion log threshold",
    )
    event_identity = _identity_shape(value.get("events"), f"{gate} assessment events")
    if event_identity["bytes"] <= 0:
        raise ValueError(f"{gate} assessment has no events")
    if verify_events:
        _event_prefix(
            _events(context, gate),
            event_identity,
            exact=False,
            label=f"{gate} assessment events",
        )
    protocol.require_exact_json(
        value.get("rawEventAuthentication"),
        protocol.mapping(
            value.get("coreReport"), f"{gate} assessment core report"
        ).get("rawEventAuthentication"),
        f"{gate} assessment raw-event authentication binding",
    )
    if replay:
        completion = protocol.strict_load(
            _completion_path(context, gate, sequence), f"{gate} completion"
        )
        expected = _assessment_value(
            context, gate, sequence, completion, prior_assessment
        )
        expected["createdUtc"] = value["createdUtc"]
        expected_core = protocol.mapping(
            expected.get("coreReport"), f"{gate} expected core report"
        )
        actual_core = protocol.mapping(
            value.get("coreReport"), f"{gate} actual core report"
        )
        expected_core["createdUtc"] = actual_core.get("createdUtc")
        protocol.require_exact_json(
            _normalize_report(value),
            _normalize_report(expected),
            f"{gate} exact assessment replay",
        )


def _history(
    context: Context, gate: str, *, replay_tip: bool = True
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    launches = _launch_records(context, gate)
    intents = sorted(sequence for sequence, kind in launches if kind == "intent")
    completions = sorted(sequence for sequence, kind in launches if kind == "completion")
    assessment_paths = _directory_records(
        _assessment_dir(context, gate), ".json", f"{gate} assessments"
    )
    if len(assessment_paths) > len(completions) or len(completions) > len(assessment_paths) + 1:
        raise ValueError(f"{gate} completion/assessment chain is imbalanced")
    completed: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    prior_assessment: dict[str, Any] | None = None
    prior_completion: dict[str, Any] | None = None
    expected_protected = _rehash_context(context, gate) if launches else None
    expected_predecessor = _predecessor_decision(context, gate)
    expected_idle = None
    if gate in TIMED_GATES:
        _verify_idle(context, gate)
        expected_idle = protocol.identity(_idle_path(context, gate))
    checkpoints: list[dict[str, Any]] = []
    for sequence in completions:
        intent = protocol.strict_load(launches[(sequence, "intent")], f"{gate} intent {sequence}")
        _validate_intent(
            context,
            gate,
            sequence,
            intent,
            prior_assessment,
            prior_completion,
            expected_predecessor=expected_predecessor,
            expected_idle=expected_idle,
            expected_protected=expected_protected,
            verify_events=False,
        )
        if intent.get("eventsBefore") is not None:
            checkpoints.append(dict(intent["eventsBefore"]))
        completion = protocol.strict_load(
            launches[(sequence, "completion")], f"{gate} completion {sequence}"
        )
        _validate_completion(
            context,
            gate,
            sequence,
            completion,
            intent,
            expected_protected=expected_protected,
            verify_events=False,
        )
        checkpoints.append(dict(completion["eventsAfter"]))
        completed.append({"intent": intent, "value": completion})
        prior_completion = protocol.identity(launches[(sequence, "completion")])
        if sequence <= len(assessment_paths):
            assessment = protocol.strict_load(
                assessment_paths[sequence - 1], f"{gate} assessment {sequence}"
            )
            _validate_assessment(
                context,
                gate,
                sequence,
                assessment,
                prior_assessment,
                replay=replay_tip and sequence == len(assessment_paths),
                verify_events=False,
            )
            checkpoints.append(dict(assessment["events"]))
            assessments.append(assessment)
            prior_assessment = protocol.identity(assessment_paths[sequence - 1])
    pending = None
    if len(intents) == len(completions) + 1:
        sequence = intents[-1]
        intent = protocol.strict_load(launches[(sequence, "intent")], f"{gate} pending intent")
        _validate_intent(
            context,
            gate,
            sequence,
            intent,
            prior_assessment,
            prior_completion,
            expected_predecessor=expected_predecessor,
            expected_idle=expected_idle,
            expected_protected=expected_protected,
            verify_events=False,
        )
        if intent.get("eventsBefore") is not None:
            checkpoints.append(dict(intent["eventsBefore"]))
        pending = {"sequence": sequence, "value": intent}
    _verify_event_checkpoints(
        context,
        gate,
        checkpoints,
        exact_latest=pending is None,
    )
    return completed, assessments, pending


def _decision_value(
    context: Context, gate: str, assessment: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": DECISION_KIND,
        "protocolId": PROTOCOL_ID,
        "attemptIndex": context.attempt_index,
        "gate": gate,
        "createdUtc": _utc_now(),
        "authorization": context.authorization_identity,
        "assessment": protocol.identity(
            _assessment_path(context, gate, int(assessment["sequence"]))
        ),
        "events": copy.deepcopy(assessment["events"]),
        "rawEventAuthentication": copy.deepcopy(
            assessment["rawEventAuthentication"]
        ),
        "decision": assessment["decision"],
        "passed": assessment["passed"],
        "authorizedSuccessor": assessment["authorizedSuccessor"],
        "promotionLogThreshold": assessment["promotionLogThreshold"],
        "zeroSafetyFailures": assessment["zeroSafetyFailures"],
        "terminal": True,
    }


def _verify_decision(
    context: Context, gate: str, *, replay: bool = True
) -> dict[str, Any]:
    path = _decision_path(context, gate)
    value = protocol.strict_load(path, f"{gate} decision")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocolId",
        "attemptIndex",
        "gate",
        "createdUtc",
        "authorization",
        "assessment",
        "events",
        "rawEventAuthentication",
        "decision",
        "passed",
        "authorizedSuccessor",
        "promotionLogThreshold",
        "zeroSafetyFailures",
        "terminal",
    }
    if set(value) != expected_fields:
        raise ValueError(f"{gate} decision field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != DECISION_KIND
        or value.get("protocolId") != PROTOCOL_ID
        or value.get("attemptIndex") != context.attempt_index
        or value.get("gate") != gate
        or value.get("terminal") is not True
        or type(value.get("passed")) is not bool
        or type(value.get("zeroSafetyFailures")) is not bool
    ):
        raise ValueError(f"{gate} decision envelope changed")
    created = _canonical_utc(value.get("createdUtc"), f"{gate} decision createdUtc")
    _same_identity(value.get("authorization"), context.authorization_identity, "authorization")
    assessment_path = _verify_identity(value.get("assessment"), f"{gate} decision assessment")
    if assessment_path.parent != _assessment_dir(context, gate):
        raise ValueError(f"{gate} decision assessment escaped its namespace")
    sequence = int(assessment_path.stem)
    completed, assessments, pending = _history(
        context, gate, replay_tip=replay
    )
    if (
        pending is not None
        or len(completed) != len(assessments)
        or not assessments
        or sequence != len(assessments)
        or assessment_path != _assessment_path(context, gate, sequence)
    ):
        raise ValueError(f"{gate} decision is not bound to complete history")
    assessment = assessments[-1]
    if created < _canonical_utc(
        assessment.get("createdUtc"), f"{gate} assessment createdUtc"
    ):
        raise ValueError(f"{gate} decision predates terminal assessment")
    if assessment.get("terminal") is not True:
        raise ValueError(f"{gate} decision assessment is nonterminal")
    _event_prefix(
        _events(context, gate),
        value.get("events"),
        exact=True,
        label=f"{gate} terminal events",
    )
    expected = _decision_value(context, gate, assessment)
    expected["createdUtc"] = value["createdUtc"]
    protocol.require_exact_json(value, expected, f"{gate} terminal decision")
    return value


def _idle_value(
    context: Context,
    gate: str,
    operator: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    if gate not in TIMED_GATES:
        raise ValueError(f"{gate} does not use an idle-machine attestation")
    predecessor_gate = PREDECESSOR[gate]
    if predecessor_gate is None:
        raise AssertionError("timed gate lacks its predecessor")
    predecessor = _verify_decision(context, predecessor_gate)
    if predecessor["decision"] != SUCCESS_DECISION[predecessor_gate]:
        raise ValueError(f"{gate} idle attestation requires {predecessor_gate} success")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": IDLE_KIND,
        "protocolId": PROTOCOL_ID,
        "attemptIndex": context.attempt_index,
        "gate": gate,
        "createdUtc": _utc_now(),
        "authorization": context.authorization_identity,
        "predecessorDecision": protocol.identity(
            _decision_path(context, predecessor_gate)
        ),
        "runId": context.core_seal["gates"][gate]["runId"],
        "idleMachine": True,
        "oneGameAtATime": True,
        "concurrentMatchProcesses": 1,
        "processSnapshot": dict(snapshot),
        "logicalProcessors": os.cpu_count() or 1,
        "machine": platform.node() or "unknown",
        "operator": operator,
    }


def _verify_idle(context: Context, gate: str) -> dict[str, Any]:
    if gate not in TIMED_GATES:
        raise ValueError(f"{gate} does not use an idle-machine attestation")
    path = _idle_path(context, gate)
    value = protocol.strict_load(path, f"{gate} idle attestation")
    expected_fields = {
        "schemaVersion",
        "kind",
        "protocolId",
        "attemptIndex",
        "gate",
        "createdUtc",
        "authorization",
        "predecessorDecision",
        "runId",
        "idleMachine",
        "oneGameAtATime",
        "concurrentMatchProcesses",
        "processSnapshot",
        "logicalProcessors",
        "machine",
        "operator",
    }
    if set(value) != expected_fields:
        raise ValueError("idle-attestation field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != IDLE_KIND
        or value.get("protocolId") != PROTOCOL_ID
        or value.get("attemptIndex") != context.attempt_index
        or value.get("gate") != gate
        or value.get("runId")
        != context.core_seal["gates"][gate]["runId"]
        or value.get("idleMachine") is not True
        or value.get("oneGameAtATime") is not True
        or value.get("concurrentMatchProcesses") != 1
        or type(value.get("concurrentMatchProcesses")) is not int
        or type(value.get("logicalProcessors")) is not int
        or value["logicalProcessors"] < 1
        or type(value.get("operator")) is not str
        or not value["operator"].strip()
    ):
        raise ValueError("idle-attestation envelope changed")
    created = _canonical_utc(value.get("createdUtc"), "idle createdUtc")
    _same_identity(value.get("authorization"), context.authorization_identity, "authorization")
    predecessor_gate = PREDECESSOR[gate]
    if predecessor_gate is None:
        raise AssertionError("timed gate lacks its predecessor")
    decision = _verify_decision(context, predecessor_gate, replay=False)
    _same_identity(
        value.get("predecessorDecision"),
        protocol.identity(_decision_path(context, predecessor_gate)),
        f"idle {predecessor_gate} decision",
    )
    if created < _canonical_utc(
        decision.get("createdUtc"), f"{predecessor_gate} decision createdUtc"
    ):
        raise ValueError(f"idle attestation predates {predecessor_gate} success")
    snapshot = _validate_process_snapshot(
        value.get("processSnapshot"), "idle process snapshot"
    )
    if _canonical_utc(
        snapshot.get("capturedUtc"), "idle process snapshot capturedUtc"
    ) > created:
        raise ValueError("idle process snapshot postdates attestation")
    if snapshot.get("relevantProcesses") != []:
        raise ValueError("idle attestation records relevant processes")
    return value


def _attest_idle(args: argparse.Namespace) -> None:
    readiness._require_operation_lock("match idle attestation")
    gate = str(args.gate)
    if gate not in TIMED_GATES:
        raise ValueError(f"{gate} does not use an idle-machine attestation")
    context = _context(args.attempt, args.authorization)
    if _path_entry(context.paths, "attemptClosure").exists():
        raise FileExistsError("attempt is already closed")
    _predecessor_decision(context, gate)
    path = _idle_path(context, gate)
    if path.exists():
        _verify_idle(context, gate)
        raise FileExistsError(f"idle attestation already exists: {path}")
    if _events(context, gate).exists() or _launch_dir(
        context, gate
    ).exists():
        raise FileExistsError(f"{gate} evidence predates idle attestation")
    operator = str(args.operator).strip()
    if not operator:
        raise ValueError("--operator must be nonempty")
    snapshot = _require_idle_processes(f"{gate} attestation")
    value = _idle_value(context, gate, operator, snapshot)
    _exclusive_json(path, value)
    _verify_idle(context, gate)
    print(f"Open-confirmation attempt {context.attempt_index} idle attestation: {path}")


def _launch(args: argparse.Namespace) -> None:
    readiness._require_operation_lock("match launch")
    gate = str(args.gate)
    context = _context(args.attempt, args.authorization)
    if _path_entry(context.paths, "attemptClosure").exists():
        raise FileExistsError("attempt is already terminally consumed")
    _predecessor_decision(context, gate)
    if _decision_path(context, gate).exists():
        decision = _verify_decision(context, gate)
        raise FileExistsError(f"{gate} already decided {decision['decision']}")
    completed, assessments, pending = _history(context, gate)
    if assessments and assessments[-1]["terminal"] is True:
        raise FileExistsError(f"{gate} has an unpublished terminal assessment")
    if len(completed) > len(assessments):
        raise FileExistsError(f"{gate} has a completion awaiting assessment")
    action = "run" if not assessments else "resume"
    if args.action is not None and args.action != action:
        raise ValueError(f"{gate} next action is {action}, not {args.action}")
    sequence = len(assessments) + 1
    if pending is not None:
        intent = pending["value"]
        if _events_grew(context, gate, intent):
            _require_idle_processes(f"{gate} crash recovery")
            _verify_protected(context, gate, intent["protected"])
            _publish_completion(
                context,
                gate,
                sequence,
                intent,
                return_code=None,
                recovered=True,
            )
            print(f"Recovered {gate} completion {sequence}; assess next")
            return
        raise RuntimeError(
            f"{gate} has a consumed pending intent with no event growth; "
            "close this attempt as pending-intent-no-growth"
        )
    if action == "run" and _events(context, gate).exists():
        raise FileExistsError(f"{gate} run requires absent events")
    if action == "resume":
        _event_prefix(
            _events(context, gate),
            assessments[-1]["events"],
            exact=True,
            label=f"{gate} exact resume checkpoint",
        )
    config_path = _config_path(context, gate)
    dotnet = _verify_identity(context.authorization["dotnetHost"], "dotnet host")
    assembly = _verify_identity(
        context.authorization["omegaMatchAssembly"], "OmegaMatch assembly"
    )
    validate = [str(dotnet), str(assembly), "validate", "--config", str(config_path)]
    subprocess.run(
        validate,
        cwd=assembly.parent,
        env=readiness._sanitized_environment(),
        check=True,
    )
    protected = _rehash_context(context, gate)
    prelaunch = _require_idle_processes(f"{gate} immediate prelaunch")
    prior_assessment = (
        None
        if not assessments
        else protocol.identity(_assessment_path(context, gate, sequence - 1))
    )
    prior_completion = (
        None
        if not completed
        else protocol.identity(_completion_path(context, gate, sequence - 1))
    )
    intent = _intent_value(
        context,
        gate,
        sequence,
        prior_assessment,
        prior_completion,
        prelaunch,
    )
    protocol.require_exact_json(intent["protected"], protected, "immediate protected rehash")
    _exclusive_json(_intent_path(context, gate, sequence), intent)
    _validate_intent(
        context, gate, sequence, intent, prior_assessment, prior_completion
    )
    command = [
        str(dotnet),
        str(assembly),
        action,
        "--config",
        str(config_path),
        "--pair-budget",
        str(intent["pairBudget"]),
    ]
    _verify_active_launch_state(context, gate, sequence, intent)
    result = subprocess.run(
        command,
        cwd=assembly.parent,
        env=readiness._sanitized_environment(),
        check=False,
    )
    _verify_active_launch_state(context, gate, sequence, intent)
    if not _events_grew(context, gate, intent):
        raise RuntimeError(
            f"{gate} OmegaMatch exited {result.returncode} without event growth; "
            "the intent consumed this attempt and may not be replayed"
        )
    completion = _publish_completion(
        context,
        gate,
        sequence,
        intent,
        return_code=result.returncode,
        recovered=False,
    )
    if completion["postLaunchProcesses"]["relevantProcesses"]:
        raise RuntimeError(f"{gate} left match processes running; assess as safety failure")
    if result.returncode != 0:
        raise RuntimeError(f"{gate} OmegaMatch exited {result.returncode}; assess next")
    print(
        f"Open-confirmation attempt {context.attempt_index} {gate} {action} "
        f"completed ({intent['pairBudget']} pairs); assess next"
    )


def _assess_command(args: argparse.Namespace) -> None:
    readiness._require_operation_lock("match assessment")
    gate = str(args.gate)
    context = _context(args.attempt, args.authorization)
    if _path_entry(context.paths, "attemptClosure").exists():
        raise FileExistsError("attempt is already terminally consumed")
    _predecessor_decision(context, gate)
    if _decision_path(context, gate).exists():
        decision = _verify_decision(context, gate)
        raise FileExistsError(f"{gate} already decided {decision['decision']}")
    completed, assessments, pending = _history(context, gate)
    if pending is not None:
        raise FileExistsError(f"{gate} has a pending launch intent")
    if assessments and assessments[-1]["terminal"] is True:
        value = _decision_value(context, gate, assessments[-1])
        _exclusive_json(_decision_path(context, gate), value)
        _verify_decision(context, gate)
        print(f"Recovered {gate} terminal decision {assessments[-1]['decision']}")
        return
    if len(completed) != len(assessments) + 1:
        raise ValueError(f"{gate} assessment requires exactly one new completion")
    sequence = len(assessments) + 1
    prior = (
        None
        if not assessments
        else protocol.identity(_assessment_path(context, gate, sequence - 1))
    )
    value = _assessment_value(
        context, gate, sequence, completed[-1]["value"], prior
    )
    _exclusive_json(_assessment_path(context, gate, sequence), value)
    _validate_assessment(
        context, gate, sequence, value, prior, replay=True
    )
    if value["terminal"]:
        decision = _decision_value(context, gate, value)
        _exclusive_json(_decision_path(context, gate), decision)
        _verify_decision(context, gate)
    print(
        f"Open-confirmation attempt {context.attempt_index} {gate} "
        f"assessment {sequence}: {value['decision']}"
    )


def _candidate_network_identity(claim: Mapping[str, Any]) -> dict[str, Any]:
    return _identity_shape(claim.get("selectedNetwork"), "candidate claim selected network")


def _decision_inventory(context: Context) -> tuple[dict[str, Any], dict[str, Any]]:
    identities: dict[str, Any] = {}
    values: dict[str, Any] = {}
    missing_seen = False
    for gate in GATES:
        path = _decision_path(context, gate)
        if not path.exists():
            identities[gate] = None
            missing_seen = True
            continue
        if missing_seen:
            raise ValueError(f"{gate} decision exists after a missing predecessor")
        decision = _verify_decision(context, gate)
        identities[gate] = protocol.identity(path)
        values[gate] = decision
    return identities, values


def _assert_stage_order_state(context: Context) -> None:
    for gate in GATES[1:]:
        if _gate_root(context, gate).exists():
            _predecessor_decision(context, gate)


def _closure_fields() -> set[str]:
    return {
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


def _success_fields() -> set[str]:
    return {
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


def _validate_closure_shape(value: Mapping[str, Any], attempt_index: int) -> None:
    if set(value) != _closure_fields():
        raise ValueError("attempt closure field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != CLOSURE_KIND
        or value.get("attemptIndex") != attempt_index
        or type(value.get("attemptIndex")) is not int
        or value.get("terminal") is not True
        or type(value.get("nextAttemptAllowed")) is not bool
        or value.get("outcome") not in {"confirmed", "failed", "aborted"}
        or type(value.get("reason")) is not str
        or SAFE_REASON.fullmatch(value["reason"]) is None
        or type(value.get("candidateNetworkSha256")) is not str
        or protocol.HEX_256.fullmatch(value["candidateNetworkSha256"]) is None
    ):
        raise ValueError("attempt closure envelope changed")
    _canonical_utc(value.get("createdUtc"), "attempt closure createdUtc")
    _same_identity(
        value.get("protocol"),
        protocol.identity(protocol.PROTOCOL_PATH),
        "closure protocol",
    )
    _identity_shape(value.get("implementationSeal"), "closure implementation seal")
    _identity_shape(value.get("attemptReservation"), "closure attempt reservation")
    decisions = protocol.mapping(value.get("decisions"), "closure decisions")
    if set(decisions) != set(GATES):
        raise ValueError("closure decision inventory changed")
    reservation_abort = (
        value["outcome"] == "aborted"
        and value["reason"] == "claim-publication-failure"
    )
    if (value.get("candidateClaim") is None) is not reservation_abort:
        raise ValueError(
            "closure candidate claim is null outside reservation-only abort"
        )
    if value.get("candidateClaim") is not None:
        _identity_shape(value["candidateClaim"], "closure candidate claim")
    for label in ("jointSuiteSeal", "authorization"):
        if value.get(label) is not None:
            _identity_shape(value[label], f"closure {label}")
    for gate in GATES:
        if decisions[gate] is not None:
            _identity_shape(decisions[gate], f"closure {gate} decision")
    if reservation_abort and (
        value.get("jointSuiteSeal") is not None
        or value.get("authorization") is not None
        or any(decisions[gate] is not None for gate in GATES)
    ):
        raise ValueError("reservation-only abort contains post-claim evidence")
    if value["outcome"] == "confirmed":
        if value["reason"] != "confirmed" or value["nextAttemptAllowed"] is not False:
            raise ValueError("confirmed closure policy changed")
        if any(decisions[gate] is None for gate in GATES):
            raise ValueError("confirmed closure lacks a decision")
    else:
        if value["nextAttemptAllowed"] is not True:
            raise ValueError("unsuccessful closure must authorize the next index")


def _success_value(
    context: Context, closure_identity: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SUCCESS_KIND,
        "protocol": protocol.identity(protocol.PROTOCOL_PATH),
        "attemptIndex": context.attempt_index,
        "createdUtc": _utc_now(),
        "attemptClosure": dict(closure_identity),
        "candidateClaim": context.claim_identity,
        "authorization": context.authorization_identity,
        "equalNodeDecision": protocol.identity(
            _decision_path(context, "equal-node")
        ),
        "equalTimeDecision": protocol.identity(
            _decision_path(context, "equal-time")
        ),
        "normalStartClockDecision": protocol.identity(
            _decision_path(context, "normal-start-clock")
        ),
        "candidateNetworkSha256": _candidate_network_identity(context.claim)[
            "sha256"
        ],
        "clearlySuperior": True,
        "terminal": True,
    }


def _verify_program_success(context: Context) -> dict[str, Any]:
    path = _path_entry(context.paths, "programSuccess")
    value = protocol.strict_load(path, "open-confirmation program success")
    if set(value) != _success_fields():
        raise ValueError("program-success field inventory changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != SUCCESS_KIND
        or value.get("attemptIndex") != context.attempt_index
        or type(value.get("attemptIndex")) is not int
        or value.get("clearlySuperior") is not True
        or value.get("terminal") is not True
    ):
        raise ValueError("program-success envelope changed")
    created = _canonical_utc(value.get("createdUtc"), "program-success createdUtc")
    closure_value = protocol.strict_load(
        _path_entry(context.paths, "attemptClosure"), "program-success closure"
    )
    if created < _canonical_utc(
        closure_value.get("createdUtc"), "program-success closure createdUtc"
    ):
        raise ValueError("program success predates attempt closure")
    expected = _success_value(
        context, protocol.identity(_path_entry(context.paths, "attemptClosure"))
    )
    expected["createdUtc"] = value["createdUtc"]
    protocol.require_exact_json(value, expected, "program success")
    for gate in FORMAL_GATES:
        decision = _verify_decision(context, gate)
        if decision["decision"] != "promote" or decision["zeroSafetyFailures"] is not True:
            raise ValueError("program success lacks all safe formal promotions")
    return value


def _publish_program_success(context: Context) -> None:
    path = _path_entry(context.paths, "programSuccess")
    closure = _path_entry(context.paths, "attemptClosure")
    value = _success_value(context, protocol.identity(closure))
    _exclusive_json(path, value)
    _verify_program_success(context)


def _terminal_outcome(
    values: Mapping[str, Mapping[str, Any]]
) -> tuple[str, str] | None:
    for gate in GATES:
        decision = values.get(gate)
        if decision is None:
            return None
        name = str(decision.get("decision"))
        if name != SUCCESS_DECISION[gate]:
            reason = FAILURE_REASONS.get((gate, name))
            if reason is None:
                raise ValueError(f"{gate} has unknown terminal failure {name!r}")
            return "failed", reason
    return "confirmed", "confirmed"


def _attempt_closure(args: argparse.Namespace) -> None:
    readiness._require_operation_lock("match attempt closure")
    attempt_index = args.attempt
    requested_abort = args.abort_reason
    protocol_value = protocol.validate_protocol()
    paths = readiness.attempt_paths(attempt_index)
    closure_path = _path_entry(paths, "attemptClosure")
    success_path = _path_entry(paths, "programSuccess")
    if closure_path.exists():
        closure = protocol.strict_load(closure_path, "existing attempt closure")
        _validate_closure_shape(closure, attempt_index)
        if closure["outcome"] == "confirmed" and not success_path.exists():
            context = _context(
                attempt_index, args.authorization, verify_chain=False
            )
            decisions, values = _decision_inventory(context)
            if _terminal_outcome(values) != ("confirmed", "confirmed"):
                raise ValueError("confirmed closure cannot recover program success")
            protocol.require_exact_json(
                closure["decisions"], decisions, "confirmed closure decisions"
            )
            _publish_program_success(context)
            print("Recovered final open-confirmation program-success record")
            return
        readiness.verify_attempt_chain(protocol_value, through_attempt=attempt_index)
        raise FileExistsError(f"attempt {attempt_index} is already closed")

    # The reservation consumes an attempt before its single entropy draw.  A
    # crash before claim publication can therefore close only as the narrow
    # reservation-only failure; every normal match transition still requires
    # the immutable claim and authorization.
    readiness.verify_implementation_seal(
        _path_entry(paths, "implementationSeal"), orchestrator_path=Path(__file__)
    )
    readiness.verify_attempt_chain(
        protocol_value,
        through_attempt=attempt_index,
        allow_incomplete_claim_tip=(
            requested_abort == "claim-publication-failure"
        ),
    )
    reservation_path = _path_entry(paths, "reservation")
    reservation = readiness.verify_attempt_reservation(
        reservation_path, attempt_index
    )
    reservation_identity = protocol.identity(reservation_path)
    claim_path = _path_entry(paths, "claim")
    claim: dict[str, Any] | None = None
    if claim_path.exists():
        # A final-name claim is never compatible with the nullable abort.  A
        # valid final is a published claim; a malformed/partial final is an
        # integrity failure that deliberately blocks k+1 rather than exposing
        # seed-bearing bytes and permitting a reroll.
        claim = readiness.verify_candidate_claim(claim_path, attempt_index)
    if claim is None:
        network = _candidate_network_identity(reservation)
    else:
        _install_core_profile(
            protocol_value,
            attempt_index,
            _claim_stage_seeds(claim, attempt_index),
        )
        _same_identity(
            claim.get("attemptReservation"),
            reservation_identity,
            "claim attempt reservation",
        )
        network = _candidate_network_identity(claim)
    context: Context | None = None
    authorization_path = _path_entry(paths, "authorization")
    reservation_only_abort = claim is None
    if reservation_only_abort and requested_abort != "claim-publication-failure":
        raise ValueError(
            "a reservation without a claim may close only as "
            "claim-publication-failure"
        )
    if (
        not reservation_only_abort
        and requested_abort == "claim-publication-failure"
    ):
        raise ValueError("claim-publication-failure cannot close a published claim")
    if authorization_path.exists() and not reservation_only_abort:
        try:
            context = _context(attempt_index, args.authorization)
        except (OSError, ValueError):
            if requested_abort not in {
                "authorization-failure",
                "suite-seal-failure",
                "protected-identity-failure",
            }:
                raise
            context = None
    elif reservation_only_abort:
        if readiness._claim_staging_artifacts(_path_entry(paths, "root")):
            raise ValueError(
                "reservation-only abort contains candidate-claim staging"
            )
        forbidden = [
            _path_entry(paths, "jointSuiteSeal"),
            authorization_path,
        ]
        stages = paths.get("stages")
        if type(stages) is not dict or set(stages) != set(GATES):
            raise ValueError("readiness stage inventory changed during reservation abort")
        forbidden.extend(Path(stages[gate]).resolve() for gate in GATES)
        if any(path.exists() for path in forbidden):
            raise ValueError("reservation-only abort contains post-claim artifacts")
    identities: dict[str, Any] = {gate: None for gate in GATES}
    values: dict[str, Any] = {}
    automatic: tuple[str, str] | None = None
    if context is not None:
        _assert_stage_order_state(context)
        identities, values = _decision_inventory(context)
        automatic = _terminal_outcome(values)
    else:
        missing_seen = False
        stages = paths.get("stages")
        if type(stages) is not dict or set(stages) != set(GATES):
            raise ValueError("readiness stage inventory changed during abort")
        for gate in GATES:
            decision_path = Path(stages[gate]) / "decision.json"
            if decision_path.is_file():
                if missing_seen:
                    raise ValueError("abort evidence has a decision-order gap")
                identities[gate] = protocol.identity(decision_path)
            else:
                missing_seen = True
    if requested_abort is not None:
        if requested_abort not in ABORT_REASONS:
            raise ValueError(f"unsupported --abort-reason {requested_abort!r}")
        if automatic is not None:
            raise ValueError("a completed terminal outcome may not be rewritten as abort")
        outcome, reason = "aborted", requested_abort
    else:
        if automatic is None:
            raise ValueError("attempt is nonterminal; provide an explicit --abort-reason")
        outcome, reason = automatic
    _require_idle_processes("attempt closure")
    joint_identity = None
    joint_path = _path_entry(paths, "jointSuiteSeal")
    if joint_path.exists():
        if context is not None:
            joint_identity = context.suite_seal_identity
        else:
            # An abort before authorization still binds the bytes that exist;
            # readiness chain verification decides whether they form a valid seal.
            joint_identity = protocol.identity(joint_path)
    authorization_identity = (
        context.authorization_identity
        if context is not None
        else (
            protocol.identity(authorization_path)
            if authorization_path.is_file()
            else None
        )
    )
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": CLOSURE_KIND,
        "protocol": protocol.identity(protocol.PROTOCOL_PATH),
        "attemptIndex": attempt_index,
        "createdUtc": _utc_now(),
        "implementationSeal": protocol.identity(
            _path_entry(paths, "implementationSeal")
        ),
        "attemptReservation": reservation_identity,
        "candidateClaim": (
            protocol.identity(claim_path) if claim is not None else None
        ),
        "jointSuiteSeal": joint_identity,
        "authorization": authorization_identity,
        "decisions": identities,
        "outcome": outcome,
        "reason": reason,
        "terminal": True,
        "nextAttemptAllowed": outcome != "confirmed",
        "candidateNetworkSha256": network["sha256"],
    }
    _validate_closure_shape(value, attempt_index)
    if outcome == "confirmed":
        if context is None:
            raise AssertionError("confirmed attempt lacks authorization")
        for gate in GATES:
            if values[gate]["decision"] != SUCCESS_DECISION[gate]:
                raise ValueError("confirmed closure lacks all gate successes")
        if any(values[gate]["zeroSafetyFailures"] is not True for gate in GATES):
            raise ValueError("confirmed closure has a safety failure")
    _exclusive_json(closure_path, value)
    if outcome == "confirmed":
        assert context is not None
        _publish_program_success(context)
    readiness.verify_attempt_chain(protocol_value, through_attempt=attempt_index)
    print(f"Open-confirmation attempt {attempt_index} closed: {outcome} ({reason})")


def _verify_state(args: argparse.Namespace) -> None:
    attempt_index = args.attempt
    protocol_value = protocol.validate_protocol()
    paths = readiness.attempt_paths(attempt_index)
    closure_path = _path_entry(paths, "attemptClosure")
    if closure_path.exists():
        readiness.verify_attempt_chain(protocol_value, through_attempt=attempt_index)
        closure = protocol.strict_load(closure_path, "attempt closure")
        _validate_closure_shape(closure, attempt_index)
        print(
            f"attempt {attempt_index}: terminal {closure['outcome']} "
            f"({closure['reason']})"
        )
        if closure["outcome"] == "confirmed":
            context = _context(attempt_index, args.authorization)
            _verify_program_success(context)
            print("program: clearly superior (all three formal gates promoted)")
        return
    context = _context(attempt_index, args.authorization)
    _assert_stage_order_state(context)
    for gate in GATES:
        root = _gate_root(context, gate)
        if not root.exists():
            later = GATES[GATES.index(gate) + 1 :]
            if any(_gate_root(context, item).exists() for item in later):
                raise ValueError(f"later evidence exists before {gate}")
            print(f"{gate}: not launched")
            continue
        if PREDECESSOR[gate] is not None:
            _predecessor_decision(context, gate)
        completed, assessments, pending = _history(context, gate)
        if pending is None and completed:
            checkpoint = (
                assessments[-1]["events"]
                if len(completed) == len(assessments)
                else completed[-1]["value"]["eventsAfter"]
            )
            _event_prefix(
                _events(context, gate),
                checkpoint,
                exact=True,
                label=f"{gate} verify-state checkpoint",
            )
        elif pending is None and _events(context, gate).exists():
            raise ValueError(f"{gate} has unsealed events")
        decision_path = _decision_path(context, gate)
        if decision_path.exists():
            decision = _verify_decision(context, gate)
            print(f"{gate}: terminal {decision['decision']}")
        elif assessments and assessments[-1]["terminal"] is True:
            raise ValueError(f"{gate} terminal assessment lacks decision")
        elif pending is not None:
            state = "event growth recoverable" if _events_grew(context, gate, pending["value"]) else "no growth; close abort"
            print(f"{gate}: pending intent ({state})")
        elif len(completed) > len(assessments):
            print(f"{gate}: completion awaiting assessment")
        elif assessments:
            print(
                f"{gate}: {assessments[-1]['decision']} at "
                f"{assessments[-1]['progress']['completePairs']} pairs"
            )
        else:
            print(f"{gate}: not launched")


def _synthetic_stage_seeds(attempt_index: int) -> dict[str, int]:
    base = 1_000_000 + attempt_index * 10
    return {gate: base + offset for offset, gate in enumerate(GATES, 1)}


def _synthetic_context(root: Path, attempt_index: int = 1) -> Context:
    protocol_value = protocol.validate_protocol()
    stage_seeds = _synthetic_stage_seeds(attempt_index)
    threshold, linear = _install_core_profile(
        protocol_value, attempt_index, stage_seeds
    )
    paths: dict[str, Any] = {
        "stages": {
            "development": root / "development",
            "equal-node": root / "equal-node",
            "equal-time": root / "equal-time",
            "normal-start-clock": root / "normal-start-clock",
        },
    }
    return Context(
        protocol_value=protocol_value,
        attempt_index=attempt_index,
        paths=paths,
        implementation_seal={},
        implementation_seal_identity={},
        claim={},
        claim_identity={},
        stage_seeds=stage_seeds,
        suite_seal={},
        suite_seal_identity={},
        authorization={},
        authorization_identity={},
        core_seal={},
        core_seal_identity={},
        promotion_log_threshold=threshold,
        promotion_e_value=linear,
    )


def _self_test_terminal_adapter_and_rules_replay() -> None:
    """Run raw→compatibility→frozen-core safety fixtures end to end."""

    with tempfile.TemporaryDirectory(
        prefix="omega-terminal-adapter-core-"
    ) as directory:
        root = Path(directory).resolve()
        sampler = root / "sampler"
        sampler.mkdir()
        for ordinal, gate in enumerate(core.GATE_SPECS):
            core._write_synthetic_sampler(
                sampler / core.SOURCE_NAMES[gate], gate, 100_000 * (ordinal + 1)
            )
        engine = root / "senpai.exe"
        engine.write_bytes(b"synthetic-engine")
        network = root / "candidate.nnue"
        network.write_bytes(b"synthetic-network")
        network_sha = hashlib.sha256(network.read_bytes()).hexdigest()
        training = root / "training.jsonl"
        training.write_text("", encoding="utf-8")
        candidate_manifest = root / "candidate.manifest.json"
        _exclusive_json(
            candidate_manifest,
            {
                "inputs": [protocol.identity(training, relative=False)],
                "roundTrip": {"sha256": network_sha},
            },
        )
        plan = root / "training-plan.json"
        _exclusive_json(
            plan,
            {
                "kind": "omega-nnue-king-state-v1-training-plan",
                "identities": {
                    "corpus": protocol.identity(training, relative=False)
                },
            },
        )
        selection = root / "selection.json"
        _exclusive_json(
            selection,
            {
                "kind": "omega-nnue-king-state-v1-validation-selection",
                "plan": protocol.identity(plan, relative=False),
                "selectedCandidateId": "K1",
                "selectedNetwork": protocol.identity(network, relative=False),
                "selectedManifest": protocol.identity(
                    candidate_manifest, relative=False
                ),
                "decision": {
                    "winner": "K1",
                    "eligibility": {"K1": {"eligible": True}},
                },
                "candidates": {"K1": {"health": {"passed": True}}},
            },
        )
        offline = root / "offline-test.json"
        _exclusive_json(
            offline,
            {
                "kind": "omega-nnue-king-state-v1-offline-test",
                "selection": protocol.identity(selection, relative=False),
                "selectedNetwork": protocol.identity(network, relative=False),
                "passed": True,
                "comparisons": {
                    "K0": {"passed": True},
                    "zeroResidual": {"passed": True},
                },
                "robustnessDirectionalImprovementOverK0": True,
                "robustnessDirectionalImprovementOverZeroResidual": True,
            },
        )
        harness_root = root / "harness"
        harness_root.mkdir()
        harness = harness_root / "OmegaMatch.dll"
        harness.write_bytes(b"synthetic-harness")
        (harness_root / "ChessLib.dll").write_bytes(b"synthetic-referee")
        original_match_config = core._match_config

        def emitter_compatible_config(*args: Any, **kwargs: Any) -> dict[str, Any]:
            value = original_match_config(*args, **kwargs)
            value["profileId"] = PROTOCOL_ID
            value["freshnessMarker"] = f"{PROTOCOL_ID}:terminal-adapter-fixture"
            for spec in value["engines"]:
                executable = Path(str(spec["executable"])).resolve()
                spec["arguments"] = ""
                spec["workingDirectory"] = str(executable.parent)
            return value

        core._match_config = emitter_compatible_config
        try:
            seal_path = core._seal(
                argparse.Namespace(
                    output_dir=root / "sealed",
                    match_output_root=root / "runs",
                    protocol=core._protocol_path(),
                    sampler_project=core._sampler_project_path(),
                    sampler_dir=sampler,
                    engine_executable=engine,
                    candidate_network=network,
                    candidate_manifest=candidate_manifest,
                    training_selection=[plan, selection, offline],
                    training_corpus=[training],
                    omega_match_assembly=harness,
                    exclude=[],
                ),
                default_exclusions=False,
            )
        finally:
            core._match_config = original_match_config
        gate = "development"
        # Replace the first scheduled synthetic root with the official Omega
        # start so this fixture has a stable, known legal move.  Rebind every
        # affected temp identity before invoking the frozen verifier.
        seal = protocol.strict_load(seal_path, "terminal fixture temp seal")
        entry = seal["gates"][gate]
        config_path = Path(str(entry["config"]["path"])).resolve()
        suite_path = Path(str(entry["suite"]["path"])).resolve()
        config = protocol.strict_load(config_path, "terminal fixture config")
        suite = protocol.strict_load(suite_path, "terminal fixture suite")
        permutation = _SHUFFLED_INDICES(
            len(suite["openings"]), int(config["seed"])
        )
        opening = suite["openings"][permutation[0]]
        initial = (
            "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/"
            "CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
        )
        phase, side, identity, orbit, signatures = core._position_meta(initial)
        opening["initialOfen"] = initial
        opening["kingStateMatch"].update(
            {
                "phase": phase,
                "rootSideToMove": side,
                "identityKey": identity,
                "symmetryOrbitKey": orbit,
                "orbitSignatures": list(signatures),
            }
        )
        suite_path.write_bytes(
            (json.dumps(suite, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        suite_identity = protocol.identity(suite_path, relative=False)
        config["expectedOpeningSuiteSha256"] = suite_identity["sha256"]
        config_path.write_bytes(
            (json.dumps(config, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        config_identity = protocol.identity(config_path, relative=False)
        entry["suite"] = suite_identity
        entry["config"] = config_identity
        replacements = {
            suite_path: suite_identity,
            config_path: config_identity,
        }
        seal["pinnedFiles"] = [
            replacements.get(Path(str(item["path"])).resolve(), item)
            for item in seal["pinnedFiles"]
        ]
        seal_path.write_bytes(
            (json.dumps(seal, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        seal = core._verify_seal(seal_path)
        entry = seal["gates"][gate]
        config = protocol.strict_load(config_path, "terminal fixture config")
        suite = protocol.strict_load(suite_path, "terminal fixture suite")
        opening = suite["openings"][permutation[0]]
        best_move = "f1f2"

        replay_bundle = readiness._runtime_bundle(
            readiness.PREFIX_REPLAY_ROOT,
            "OmegaOpeningPrefixReplay.dll",
            "OmegaOpeningPrefixReplay.exe",
        )
        dotnet_identity = readiness._runtime_identity("dotnetHost")
        dotnet = _verify_identity(dotnet_identity, "terminal fixture dotnet")
        replay_root = Path(str(replay_bundle["root"])).resolve()
        replay_assembly = (
            replay_root / str(replay_bundle["assemblyRelativePath"])
        ).resolve()
        move_source = root / "single-move-source.json"
        move_source.write_text("{}\n", encoding="utf-8")
        move_source_identity = protocol.identity(move_source, relative=False)
        replay_request = {
            "schemaVersion": 1,
            "kind": "omega-opening-prefix-replay-request-v1",
            "requestId": 0,
            "sourcePath": str(move_source),
            "sourceBytes": move_source_identity["bytes"],
            "sourceSha256": move_source_identity["sha256"],
            "sourceRecord": 1,
            "sourceObjectOrdinal": 1,
            "sourceObjectIdentity": "/single-move",
            "schema": "schedule-moves",
            "initialSource": "explicit",
            "containerProof": "direct-object",
            "initialOfen": initial,
            "moves": [best_move],
            "expectedPositions": None,
        }
        replay_input = root / "single-move-request.jsonl"
        replay_input.write_text(
            json.dumps(replay_request, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        replay_output = root / "single-move-output.jsonl"
        replay_manifest = root / "single-move-manifest.json"
        subprocess.run(
            [
                str(dotnet), str(replay_assembly),
                "--input", str(replay_input),
                "--output", str(replay_output),
                "--manifest", str(replay_manifest),
            ],
            cwd=replay_root,
            env=readiness._sanitized_environment(),
            check=True,
            capture_output=True,
            timeout=60,
        )
        replay_positions = [
            _strict_object_bytes(line, "single move replay")
            for line in replay_output.read_bytes().splitlines()
            if line
        ]
        post = str(replay_positions[-1]["ofen"])

        common_runtime = {
            "Executable": str(engine),
            "Arguments": "",
            "WorkingDirectory": str(engine.parent),
            "Sha256": config["engines"][0]["expectedSha256"],
            "FileSize": engine.stat().st_size,
            "LastWriteUtc": "2026-07-23T00:00:00Z",
            "UciName": "Senpai synthetic",
            "UciAuthor": "Synthetic",
        }
        runtime_engines = [
            {
                "Id": "nnue-candidate",
                **common_runtime,
                "Options": config["engines"][0]["options"],
                "ExternalAssets": [
                    {
                        "OptionName": "OmegaNNUEFile",
                        "Path": str(network),
                        "Sha256": network_sha,
                        "FileSize": network.stat().st_size,
                        "LastWriteUtc": "2026-07-23T00:00:00Z",
                    }
                ],
                "StartupDiagnostics": [
                    f"info string Omega NNUE loaded: synthetic from {network}",
                    "info string Omega NNUE evaluation active",
                ],
                "OmegaNnueActiveVerified": True,
            },
            {
                "Id": "hce-control",
                **common_runtime,
                "Options": config["engines"][1]["options"],
                "ExternalAssets": [],
                "StartupDiagnostics": [
                    "info string Omega NNUE disabled; handcrafted evaluation active"
                ],
                "OmegaNnueActiveVerified": False,
            },
        ]
        run_record = {
            "RecordType": "run",
            "RunId": config["runId"],
            "ProfileId": config["profileId"],
            "FreshnessMarker": config["freshnessMarker"],
            "CreatedUtc": "2026-07-23T00:00:00Z",
            "ConfigSha256": config_identity["sha256"],
            "OpeningSuiteSha256": suite_identity["sha256"],
            "HarnessVersion": "fixture",
            "HarnessSha256": config["expectedHarnessSha256"],
            "HarnessBundleSha256": config["expectedHarnessBundleSha256"],
            "OperatingSystem": "fixture",
            "Runtime": "fixture",
            "ProcessorCount": 1,
            "Seed": config["seed"],
            "Match": _system_text_json_number_shape(
                _resolved_match_spec(config["match"])
            ),
            "Engines": runtime_engines,
        }
        search_command = f"go nodes {config['match']['nodes']}"
        initial_side = initial.split()[1]
        successful_raw_color = "white" if initial_side == "w" else "black"
        terminal_raw_color = "black" if initial_side == "w" else "white"

        def raw_fixture(*, process_exit: bool) -> list[dict[str, Any]]:
            records: list[dict[str, Any]] = [copy.deepcopy(run_record)]
            opening_id = str(opening["id"])
            pair_id = f"{opening_id}-r001"
            for game_number, (suffix, white, black) in enumerate(
                (
                    ("ab", "nnue-candidate", "hce-control"),
                    ("ba", "hce-control", "nnue-candidate"),
                )
            ):
                game_id = f"{pair_id}-{suffix}"
                successful_engine = white if initial_side == "w" else black
                terminal_engine = black if initial_side == "w" else white
                winner = successful_engine
                result = "1-0" if initial_side == "w" else "0-1"
                score_a = 1.0 if winner == "nnue-candidate" else 0.0
                start_second = 1 + game_number * 2
                records.extend(
                    [
                        {
                            "RecordType": "gameStart",
                            "GameId": game_id,
                            "PairId": pair_id,
                            "Attempt": 1,
                            "StartedUtc": f"2026-07-23T00:00:0{start_second}Z",
                            "OpeningId": opening_id,
                            "WhiteEngineId": white,
                            "BlackEngineId": black,
                            "InitialOfen": initial,
                            "OpeningMoves": [],
                        },
                        {
                            "RecordType": "ply",
                            "GameId": game_id,
                            "Attempt": 1,
                            "Ply": 1,
                            "EngineId": successful_engine,
                            "Color": successful_raw_color,
                            "PreOfen": initial,
                            "PostOfen": post,
                            "BestMove": best_move,
                            "San": "fixture-san",
                            "WhiteClockBeforeMs": 60_000,
                            "BlackClockBeforeMs": 60_000,
                            "WhiteClockAfterMs": 60_000,
                            "BlackClockAfterMs": 60_000,
                            "Search": {
                                "Command": search_command,
                                "BestMove": best_move,
                                "WallTimeMs": 1.0,
                                "DeadlineExceeded": False,
                                "ProcessExited": False,
                                "Info": [],
                                "RawOutput": [f"bestmove {best_move}"],
                                "StandardError": [],
                            },
                            "WhiteScoreCp": 0,
                            "Pv": {
                                "LegalPlies": 1,
                                "TotalPlies": 1,
                                "San": ["fixture-san"],
                                "IsLegal": True,
                            },
                        },
                    ]
                )
                terminal = {
                    "RecordType": "ply",
                    "GameId": game_id,
                    "Attempt": 1,
                    "Ply": 2,
                    "EngineId": terminal_engine,
                    "Color": terminal_raw_color,
                    "PreOfen": post,
                    "WhiteClockBeforeMs": 60_000,
                    "BlackClockBeforeMs": 60_000,
                    "WhiteClockAfterMs": 60_000,
                    "BlackClockAfterMs": 60_000,
                    "Search": {
                        "Command": search_command,
                        "WallTimeMs": 0.0,
                        "DeadlineExceeded": False,
                        "ProcessExited": process_exit,
                        "Info": [],
                        "RawOutput": [],
                        "StandardError": [],
                    },
                    "Pv": {
                        "LegalPlies": 0,
                        "TotalPlies": 0,
                        "San": [],
                        "IsLegal": True,
                    },
                }
                if not process_exit:
                    terminal["Error"] = "synthetic engine crash"
                records.append(terminal)
                records.append(
                    {
                        "RecordType": "gameResult",
                        "GameId": game_id,
                        "PairId": pair_id,
                        "Attempt": 1,
                        "FinishedUtc": f"2026-07-23T00:00:0{start_second + 1}Z",
                        "OpeningId": opening_id,
                        "WhiteEngineId": white,
                        "BlackEngineId": black,
                        "Result": result,
                        "Termination": "engine crash",
                        "WinnerEngineId": winner,
                        "ScoreA": score_a,
                        "Plies": 1,
                        "FinalOfen": post,
                        "WhiteClockMs": 60_000,
                        "BlackClockMs": 60_000,
                        "IllegalMoves": 0,
                        "IllegalPvs": 0,
                        "ProtocolFailures": 1,
                        "TimeForfeits": 0,
                    }
                )
            return records

        fake_context = types.SimpleNamespace(
            implementation_seal={
                "prefixReplayBundle": replay_bundle,
                "pinned": {
                    "omegaMatchRulesAssembly": protocol.identity(
                        _IMPORTED_G5_OMEGA_MATCH_RULES_ASSEMBLY,
                        relative=True,
                    )
                },
            },
            authorization={"dotnetHost": dotnet_identity},
        )
        for label, process_exit, expected_transforms in (
            ("explicit-error", False, 0),
            ("process-exit", True, 2),
        ):
            records = raw_fixture(process_exit=process_exit)
            raw = b"".join(
                (
                    json.dumps(item, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode("utf-8")
                for item in records
            )
            parsed = _strict_event_log(raw, f"{label} raw fixture")
            schedule = _authenticate_raw_event_schedule(
                records=parsed,
                suite=suite,
                config=config,
                suite_identity=suite_identity,
                config_identity=config_identity,
                gate=gate,
            )
            if schedule["processExitTerminalPlies"] != expected_transforms:
                raise AssertionError(f"{label} raw terminal count changed")
            replay = _authenticate_successful_ply_rules_replay(
                fake_context, gate, raw, parsed
            )
            if (
                replay["passes"] is not True
                or replay["successfulPlies"] != 2
                or replay["parityMatches"] != 2
            ):
                raise AssertionError(f"{label} legal replay did not pass")
            color_bytes, color_audit = _adapt_pinned_emitter_colors(
                raw, label=f"{label} color fixture"
            )
            derived, terminal_audit = (
                _adapt_process_exit_terminals_for_frozen_core(
                    color_bytes,
                    expected_transforms=expected_transforms,
                    label=f"{label} terminal fixture",
                )
            )
            raw_identity = {
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()
            }
            color_identity = {
                "bytes": len(color_bytes),
                "sha256": hashlib.sha256(color_bytes).hexdigest(),
            }
            derived_identity = {
                "bytes": len(derived),
                "sha256": hashlib.sha256(derived).hexdigest(),
            }
            if (
                color_audit["raw"] != raw_identity
                or terminal_audit["input"] != color_identity
                or terminal_audit["derived"] != derived_identity
                or terminal_audit["transformedTerminalPlies"]
                != expected_transforms
                or terminal_audit["errorFieldsChanged"] != expected_transforms
                or terminal_audit["nonErrorFieldsChanged"] != 0
            ):
                raise AssertionError(f"{label} adapter hash/count chain changed")
            events_path = root / f"{label}-derived-events.jsonl"
            events_path.write_bytes(derived)
            report = core._assess(
                argparse.Namespace(
                    seal=seal_path,
                    gate=gate,
                    events=events_path,
                    output=root / f"{label}-assessment.json",
                    idle_attestation=None,
                )
            )
            if (
                report["developmentScreen"]["decision"] != "safety-fail"
                or report["safety"]["protocolFailures"] != 2
            ):
                raise AssertionError(f"{label} did not reach frozen-core safety-fail")

        fabricated = raw_fixture(process_exit=False)
        fabricated_post = initial
        for record in fabricated:
            if record.get("RecordType") == "ply" and record.get("Ply") == 1:
                record["PostOfen"] = fabricated_post
            elif record.get("RecordType") == "ply" and record.get("Ply") == 2:
                record["PreOfen"] = fabricated_post
            elif record.get("RecordType") == "gameResult":
                record["FinalOfen"] = fabricated_post
        fabricated_raw = b"".join(
            (
                json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            for item in fabricated
        )
        fabricated_parsed = _strict_event_log(
            fabricated_raw, "fabricated PostOfen fixture"
        )
        _authenticate_raw_event_schedule(
            records=fabricated_parsed,
            suite=suite,
            config=config,
            suite_identity=suite_identity,
            config_identity=config_identity,
            gate=gate,
        )
        try:
            _authenticate_successful_ply_rules_replay(
                fake_context, gate, fabricated_raw, fabricated_parsed
            )
        except (subprocess.CalledProcessError, ValueError):
            pass
        else:
            raise AssertionError("fabricated PostOfen passed pinned rules replay")


def _self_test_late_history_consumes_attempt() -> None:
    """Prove a post-authorization noncollision growth consumes the index."""

    original_checked = globals()["_context_checked"]
    saved = {
        name: getattr(readiness, name)
        for name in (
            "attempt_paths", "_publish_abort", "verify_attempt_chain",
            "verify_implementation_seal",
        )
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="omega-context-late-noncollision-"
        ) as directory:
            root = Path(directory).resolve()
            paths = {
                "claim": root / "candidate-claim.json",
                "jointSuiteSeal": root / "joint-suite.seal.json",
                "attemptClosure": root / "attempt-closure.json",
            }
            paths["claim"].write_text("{}\n", encoding="utf-8")
            paths["jointSuiteSeal"].write_text("{}\n", encoding="utf-8")

            def fixture_paths(index: int) -> dict[str, Path]:
                if index != 1:
                    raise ValueError("fixture only owns attempt one")
                return dict(paths)

            def fixture_chain(*args: Any, **kwargs: Any) -> dict[str, Any]:
                closed = paths["attemptClosure"].is_file()
                return {
                    "attempts": 1,
                    "closed": 1 if closed else 0,
                    "activeAttempt": None if closed else 1,
                    "confirmedAttempt": None,
                    "priorPublishedStageSeeds": [],
                }

            def fixture_abort(index: int, reason: str) -> dict[str, Any]:
                if index != 1 or reason != "protected-identity-failure":
                    raise AssertionError("context fixture abort changed")
                value = {
                    "attemptIndex": 1,
                    "reason": reason,
                    "outcome": "aborted",
                    "terminal": True,
                    "nextAttemptAllowed": True,
                }
                if paths["attemptClosure"].exists():
                    raise FileExistsError("fixture attempt already closed")
                _exclusive_json(paths["attemptClosure"], value)
                return value

            globals()["_context_checked"] = lambda *args, **kwargs: (
                _ for _ in ()
            ).throw(
                ValueError(
                    "post-selection exact history membership gained a new "
                    "noncolliding member; consume the attempt"
                )
            )
            readiness.attempt_paths = fixture_paths
            readiness._publish_abort = fixture_abort
            readiness.verify_attempt_chain = fixture_chain
            readiness.verify_implementation_seal = lambda *args, **kwargs: {}
            try:
                _context(1)
            except ValueError:
                pass
            else:
                raise AssertionError("late noncollision did not fail context load")
            closure = protocol.strict_load(
                paths["attemptClosure"], "late noncollision closure"
            )
            state = fixture_chain()
            if (
                closure.get("reason") != "protected-identity-failure"
                or closure.get("terminal") is not True
                or state["activeAttempt"] is not None
                or state["closed"] != 1
            ):
                raise AssertionError("late noncollision did not consume attempt k")
            try:
                readiness.sample_attempt(1)
            except (FileExistsError, ValueError):
                pass
            else:
                raise AssertionError("late noncollision allowed same-index resampling")
            try:
                readiness.create_candidate_claim(1)
            except (FileExistsError, ValueError):
                pass
            else:
                raise AssertionError("late noncollision allowed same-index reclaim")
    finally:
        globals()["_context_checked"] = original_checked
        for name, value in saved.items():
            setattr(readiness, name, value)


def _self_test() -> None:
    protocol_value = protocol.validate_protocol()
    _assert_exact_bindings()
    _self_test_late_history_consumes_attempt()
    _assert_exact_bindings()
    _self_test_terminal_adapter_and_rules_replay()
    _assert_exact_bindings()
    relative_protocol_identity = protocol.identity(
        protocol.PROTOCOL_PATH, relative=True
    )
    if (
        _verify_identity(
            relative_protocol_identity, "relative protocol identity"
        )
        != protocol.PROTOCOL_PATH.resolve()
    ):
        raise AssertionError("relative identity resolution changed")

    valid_seed_claim = {
        "seedDerivation": {
            "algorithmId": _IMPORTED_SEED_ALGORITHM_ID,
            "entropyCommitment": "a" * 64,
            "stageSeeds": _synthetic_stage_seeds(1),
            "rejectionCounters": {gate: 0 for gate in GATES},
        }
    }
    valid_seed_claim["attemptIndex"] = 1
    if _claim_stage_seeds(valid_seed_claim, 1) != _synthetic_stage_seeds(1):
        raise AssertionError("valid claim-bound stage seeds changed")
    malformed_seed_claims: list[dict[str, Any]] = []
    for mutate in (
        lambda value: value["seedDerivation"].update(
            {"algorithmId": "predictable-seed-v0"}
        ),
        lambda value: value["seedDerivation"]["stageSeeds"].update(
            {"development": True}
        ),
        lambda value: value["seedDerivation"]["stageSeeds"].update(
            {
                "equal-node": value["seedDerivation"]["stageSeeds"][
                    "development"
                ]
            }
        ),
        lambda value: value["seedDerivation"]["rejectionCounters"].update(
            {"equal-time": 0.0}
        ),
        lambda value: value["seedDerivation"].update(
            {"entropyCommitment": "A" * 64}
        ),
        lambda value: value["seedDerivation"]["stageSeeds"].pop("equal-time"),
    ):
        malformed = copy.deepcopy(valid_seed_claim)
        mutate(malformed)
        malformed_seed_claims.append(malformed)
    for malformed in malformed_seed_claims:
        try:
            _claim_stage_seeds(malformed, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed claim-bound stage seeds were accepted")

    fake = types.ModuleType("king_state_confirmation_readiness_v2")
    fake.__file__ = str((_PREAUTH_REPO / _READINESS_RELATIVE).resolve())
    fake.verify_authorization = lambda *args, **kwargs: {}
    fake._verify_runtime_authorization = lambda *args, **kwargs: {}
    prior_cache = sys.modules["king_state_confirmation_readiness_v2"]
    try:
        sys.modules["king_state_confirmation_readiness_v2"] = fake
        try:
            _assert_exact_bindings()
        except ValueError:
            pass
        else:
            raise AssertionError("same-path fake readiness cache was accepted")
    finally:
        sys.modules["king_state_confirmation_readiness_v2"] = prior_cache

    original_authorization = readiness.verify_authorization
    try:
        readiness.verify_authorization = lambda *args, **kwargs: {}
        try:
            _assert_exact_bindings()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted readiness authorization was accepted")
    finally:
        readiness.verify_authorization = original_authorization
    _assert_exact_bindings()

    original_contract = readiness.contract
    try:
        readiness.contract = types.ModuleType("substituted_confirmation_protocol")
        try:
            _assert_exact_bindings()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted readiness protocol was accepted")
    finally:
        readiness.contract = original_contract
    _assert_exact_bindings()

    original_lock_path = readiness.GLOBAL_OPERATION_LOCK_PATH
    try:
        readiness.GLOBAL_OPERATION_LOCK_PATH = Path(str(original_lock_path))
        try:
            _assert_exact_bindings()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted operation-lock path was accepted")
    finally:
        readiness.GLOBAL_OPERATION_LOCK_PATH = original_lock_path
    _assert_exact_bindings()

    probe = f"""
import runpy, sys, types
fake = types.ModuleType('king_state_confirmation_readiness_v2')
fake.__file__ = {str((_PREAUTH_REPO / _READINESS_RELATIVE).resolve())!r}
fake.verify_authorization = lambda *args, **kwargs: {{}}
fake._verify_runtime_authorization = lambda *args, **kwargs: {{}}
sys.modules['king_state_confirmation_readiness_v2'] = fake
try:
    runpy.run_path({str(Path(__file__).resolve())!r}, run_name='confirmation_fake_probe')
except ImportError as error:
    if 'preloaded confirmation module' not in str(error):
        raise
else:
    raise SystemExit(93)
"""
    probed = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        env=readiness._sanitized_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    if probed.returncode != 0:
        raise AssertionError(
            "fresh-process same-path fake readiness preload was accepted or "
            f"misclassified: {probed.stderr[-1000:]}"
        )

    try:
        readiness._require_operation_lock("match self-test unlocked transition")
    except RuntimeError:
        pass
    else:
        raise AssertionError("unlocked match transition was accepted")

    with readiness._operation_lock() as outer_lock_token:
        if (
            readiness._require_operation_lock("match self-test transition")
            != outer_lock_token
        ):
            raise AssertionError("match transition observed a different lock token")
        with readiness._operation_lock() as inner_lock_token:
            if (
                type(outer_lock_token) is not str
                or protocol.HEX_256.fullmatch(outer_lock_token) is None
                or inner_lock_token != outer_lock_token
            ):
                raise AssertionError(
                    "global confirmation operation-lock reentrancy changed"
                )

    holder_code = f"""
import runpy, time
ns = runpy.run_path({str(Path(__file__).resolve())!r}, run_name='confirmation_lock_holder')
with ns['readiness']._operation_lock():
    ns['readiness']._require_operation_lock('simulated readiness abort')
    print('LOCKED', flush=True)
    time.sleep(30)
"""
    contender_code = f"""
import runpy
ns = runpy.run_path({str(Path(__file__).resolve())!r}, run_name='confirmation_lock_contender')
try:
    with ns['readiness']._operation_lock():
        ns['readiness']._require_operation_lock('simulated match launch or closure')
except RuntimeError:
    raise SystemExit(0)
raise SystemExit(94)
"""
    holder = subprocess.Popen(
        [sys.executable, "-I", "-c", holder_code],
        env=readiness._sanitized_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = holder.stdout.readline().strip() if holder.stdout is not None else ""
        if ready != "LOCKED":
            stderr = "" if holder.stderr is None else holder.stderr.read()
            raise AssertionError(f"cross-process lock holder failed: {stderr[-1000:]}")
        contender = subprocess.run(
            [sys.executable, "-I", "-c", contender_code],
            env=readiness._sanitized_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if contender.returncode != 0:
            raise AssertionError(
                "cross-process abort-vs-launch lock race was not refused: "
                f"{contender.stderr[-1000:]}"
            )
    finally:
        holder.terminate()
        try:
            holder.wait(timeout=10)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=10)

    # Attempt 1 uses E >= 200; attempt 377 remains feasible under the same
    # shared e-process, while attempt 378 is rejected before a float, seed, or
    # namespace can be materialized.
    nonprofile_before = {
        name: value
        for name, value in core.__dict__.items()
        if name not in CORE_PROFILE_GLOBALS
    }
    threshold1, linear1 = _install_core_profile(
        protocol_value, 1, _synthetic_stage_seeds(1)
    )
    if (
        threshold1 != protocol.promotion_log_threshold(1)
        or linear1 <= 200.0
        or core.PROMOTION_E_VALUE != linear1
        or core.FUTILITY_E_VALUE != 20.0
        or core.MINIMUM_GATE_PAIRS != 128
        or core.MAXIMUM_GATE_PAIRS != 512
    ):
        raise AssertionError("attempt-1 core profile changed")
    first_result = core._sequential_gate(
        [(f"a1-{index:04d}", 1.0) for index in range(1, 513)]
    )
    if first_result["decision"] != "promote" or first_result["signalPair"] < 128:
        raise AssertionError("attempt 1 all-win formal path did not promote")
    threshold377, linear377 = _install_core_profile(
        protocol_value, 377, _synthetic_stage_seeds(377)
    )
    final_result = core._sequential_gate(
        [(f"a377-{index:04d}", 1.0) for index in range(1, 513)]
    )
    if (
        threshold377 != protocol.promotion_log_threshold(377)
        or linear377 <= linear1
        or final_result["decision"] != "promote"
        or final_result["signalPair"] is None
    ):
        raise AssertionError("attempt 377 feasibility path changed")
    try:
        _install_core_profile(
            protocol_value, 378, _synthetic_stage_seeds(378)
        )
    except ValueError as error:
        if "statistical-cap-exhausted" not in str(error):
            raise
    else:
        raise AssertionError("attempt 378 escaped the statistical cap")
    for name, value in nonprofile_before.items():
        if core.__dict__.get(name) is not value:
            raise AssertionError(f"confirmation install changed nonprofile core global {name}")
    _install_core_profile(protocol_value, 1, _synthetic_stage_seeds(1))

    with tempfile.TemporaryDirectory(prefix="omega-confirmation-seed-binding-") as raw:
        root = Path(raw)
        configs: dict[str, dict[str, Any]] = {}
        suites: dict[str, dict[str, Any]] = {}
        core_gates: dict[str, dict[str, Any]] = {}
        for gate in GATES:
            config_path = root / f"{gate}.config.json"
            suite_path = root / f"{gate}.suite.json"
            config_path.write_bytes(
                _canonical_bytes({"seed": _synthetic_stage_seeds(1)[gate]})
            )
            suite_path.write_bytes(_canonical_bytes({"gate": gate}))
            configs[gate] = protocol.identity(config_path, relative=False)
            suites[gate] = protocol.identity(suite_path, relative=False)
            core_gates[gate] = {
                "config": configs[gate],
                "suite": suites[gate],
            }
        seed_context_base = _synthetic_context(root / "stages")
        seed_context = Context(
            **{
                **seed_context_base.__dict__,
                "authorization": {
                    "stageSeeds": _synthetic_stage_seeds(1),
                    "configs": configs,
                    "suites": suites,
                },
                "core_seal": {"gates": core_gates},
            }
        )
        _verify_authorized_gate_identities(seed_context)
        wrong_authorization = copy.deepcopy(seed_context.authorization)
        wrong_authorization["stageSeeds"]["development"] += 1
        wrong_context = Context(
            **{**seed_context.__dict__, "authorization": wrong_authorization}
        )
        try:
            _verify_authorized_gate_identities(wrong_context)
        except ValueError:
            pass
        else:
            raise AssertionError("authorization stage-seed mismatch was accepted")

        wrong_config = root / "development.wrong.config.json"
        wrong_config.write_bytes(
            _canonical_bytes(
                {"seed": _synthetic_stage_seeds(1)["development"] + 1}
            )
        )
        wrong_configs = copy.deepcopy(configs)
        wrong_configs["development"] = protocol.identity(
            wrong_config, relative=False
        )
        wrong_core_gates = copy.deepcopy(core_gates)
        wrong_core_gates["development"]["config"] = wrong_configs[
            "development"
        ]
        wrong_context = Context(
            **{
                **seed_context.__dict__,
                "authorization": {
                    **seed_context.authorization,
                    "configs": wrong_configs,
                },
                "core_seal": {"gates": wrong_core_gates},
            }
        )
        try:
            _verify_authorized_gate_identities(wrong_context)
        except ValueError:
            pass
        else:
            raise AssertionError("config stage-seed mismatch was accepted")

    def event_line(value: Mapping[str, Any]) -> bytes:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")

    synthetic_run = {
        "RecordType": "run",
        "RunId": "synthetic",
        "ProfileId": PROTOCOL_ID,
        "FreshnessMarker": "synthetic",
        "CreatedUtc": "2026-07-23T00:00:00Z",
        "ConfigSha256": "1" * 64,
        "OpeningSuiteSha256": "2" * 64,
        "HarnessVersion": "synthetic",
        "HarnessSha256": "3" * 64,
        "HarnessBundleSha256": "4" * 64,
        "OperatingSystem": "synthetic",
        "Runtime": "synthetic",
        "ProcessorCount": 1,
        "Seed": 1,
        "Match": {},
        "Engines": [],
    }
    synthetic_start = {
        "RecordType": "gameStart",
        "GameId": "g",
        "PairId": "p",
        "Attempt": 1,
        "StartedUtc": "2026-07-23T00:00:01Z",
        "OpeningId": "o",
        "WhiteEngineId": "nnue-candidate",
        "BlackEngineId": "hce-control",
        "InitialOfen": "synthetic",
        "OpeningMoves": [],
    }
    valid_events = event_line(synthetic_run) + event_line(synthetic_start)
    if len(_strict_event_log(valid_events, "synthetic")) != 2:
        raise AssertionError("strict event parser rejected a valid prefix")
    float_run = dict(synthetic_run)
    float_run["ProcessorCount"] = 1.0
    bool_start = dict(synthetic_start)
    bool_start["Attempt"] = True
    extra_run = dict(synthetic_run)
    extra_run["Unexpected"] = 1
    malformed = (
        valid_events[:-1],
        b'{"RecordType":"run","RecordType":"run"}\n',
        b'{"RecordType":"run","Score":NaN}\n',
        b'{"RecordType":"unknown"}\n',
        b'{"RecordType":"gameStart"}\n',
        b'{"RecordType":"run"}\n{"RecordType":"run"}\n',
        b'{"RecordType":"run"}\n\n',
        b'\xff\n',
        event_line(float_run),
        event_line(synthetic_run) + event_line(bool_start),
        event_line(extra_run),
    )
    for payload in malformed:
        try:
            _strict_event_log(payload, "synthetic malformed")
        except ValueError:
            pass
        else:
            raise AssertionError("malformed event JSONL was accepted")

    # Pinned-emitter schedule fixtures cover every gate mode, exact start
    # metadata, both honest terminal-failure encodings, and the strict engine
    # attestation shared with the practical gate.
    fixture_initial = (
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/"
        "CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
    )
    fixture_common_options = {
        "Threads": "1", "Hash": "128", "Ponder": "false",
        "OwnBook": "false", "UCI_Chess960": "false", "UCI_Variant": "omega",
    }
    fixture_candidate_options = {
        **fixture_common_options,
        "OmegaNNUEFile": "C:/synthetic/network.onnx",
        "UseOmegaNNUE": "true",
    }
    fixture_control_options = {
        **fixture_common_options,
        "OmegaNNUEFile": "<empty>",
        "UseOmegaNNUE": "false",
    }

    def pinned_fixture(gate: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        mode = {
            "development": "nodes",
            "equal-node": "nodes",
            "equal-time": "moveTime",
            "normal-start-clock": "clock",
        }[gate]
        match: dict[str, Any] = {
            "engineA": "nnue-candidate",
            "engineB": "hce-control",
            "openingsFile": f"C:/synthetic/{gate}-suite.json",
            "repeats": 1,
            "mode": mode,
            "freshProcessPerGame": True,
            "sequentialGate": {
                "candidateEngine": "nnue-candidate",
                "minimumPairs": 128,
                "nullElo": 15.0,
                "promotionAlpha": 0.005,
                "futilityBeta": 0.05,
            },
        }
        if mode == "nodes":
            match["nodes"] = 30_000 if gate == "development" else 60_000
        elif mode == "moveTime":
            match["moveTimeMs"] = 1_000
        else:
            match.update(
                {"initialTimeMs": 60_000, "incrementMs": 1_000}
            )
        config = {
            "runId": f"fixture-{gate}",
            "profileId": PROTOCOL_ID,
            "freshnessMarker": f"{PROTOCOL_ID}:fixture:{gate}",
            "seed": 17,
            "engines": [
                {
                    "id": "nnue-candidate",
                    "executable": "C:/synthetic/senpai.exe",
                    "arguments": "",
                    "workingDirectory": "C:/synthetic",
                    "expectedSha256": "4" * 64,
                    "expectedAssetSha256": {"OmegaNNUEFile": "5" * 64},
                    "options": fixture_candidate_options,
                },
                {
                    "id": "hce-control",
                    "executable": "C:/synthetic/senpai.exe",
                    "arguments": "",
                    "workingDirectory": "C:/synthetic",
                    "expectedSha256": "4" * 64,
                    "options": fixture_control_options,
                },
            ],
            "match": match,
        }
        suite = {
            "openings": [
                {
                    "id": f"fixture-{gate}-opening",
                    "initialOfen": fixture_initial,
                    "moves": [],
                    "openConfirmationV2": {"finalOfen": fixture_initial},
                }
            ]
        }
        suite_identity = {
            "path": f"C:/synthetic/{gate}-suite.json",
            "bytes": 1,
            "sha256": "2" * 64,
        }
        config_identity = {
            "path": f"C:/synthetic/{gate}-config.json",
            "bytes": 1,
            "sha256": "3" * 64,
        }
        common_runtime = {
            "Executable": "C:/synthetic/senpai.exe",
            "Arguments": "",
            "WorkingDirectory": "C:/synthetic",
            "Sha256": "4" * 64,
            "FileSize": 1,
            "LastWriteUtc": "2026-07-23T00:00:00Z",
            "UciName": "Senpai synthetic",
            "UciAuthor": "Synthetic",
        }
        runtime_engines = [
            {
                "Id": "nnue-candidate",
                **common_runtime,
                "Options": fixture_candidate_options,
                "ExternalAssets": [
                    {
                        "OptionName": "OmegaNNUEFile",
                        "Path": "C:/synthetic/network.onnx",
                        "Sha256": "5" * 64,
                        "FileSize": 1,
                        "LastWriteUtc": "2026-07-23T00:00:00Z",
                    }
                ],
                "StartupDiagnostics": [
                    "info string Omega NNUE loaded: synthetic from C:/synthetic/network.onnx",
                    "info string Omega NNUE evaluation active",
                ],
                "OmegaNnueActiveVerified": True,
            },
            {
                "Id": "hce-control",
                **common_runtime,
                "Options": fixture_control_options,
                "ExternalAssets": [],
                "StartupDiagnostics": [
                    "info string Omega NNUE disabled; handcrafted evaluation active"
                ],
                "OmegaNnueActiveVerified": False,
            },
        ]
        records: list[dict[str, Any]] = [
            {
                "RecordType": "run",
                "RunId": config["runId"],
                "ProfileId": config["profileId"],
                "FreshnessMarker": config["freshnessMarker"],
                "CreatedUtc": "2026-07-23T00:00:00Z",
                "ConfigSha256": config_identity["sha256"],
                "OpeningSuiteSha256": suite_identity["sha256"],
                "HarnessVersion": "fixture",
                "HarnessSha256": "6" * 64,
                "HarnessBundleSha256": "7" * 64,
                "OperatingSystem": "fixture",
                "Runtime": "fixture",
                "ProcessorCount": 1,
                "Seed": config["seed"],
                "Match": _system_text_json_number_shape(
                    _resolved_match_spec(match)
                ),
                "Engines": runtime_engines,
            }
        ]
        opening_id = suite["openings"][0]["id"]
        pair_id = f"{opening_id}-r001"
        search_command = (
            f"go nodes {match['nodes']}"
            if mode == "nodes"
            else (
                f"go movetime {match['moveTimeMs']}"
                if mode == "moveTime"
                else "go wtime 60000 btime 60000 winc 1000 binc 1000"
            )
        )
        final_info = {
            "Raw": "info depth 1 score cp 100 nodes 1 time 1 pv f1f2",
            "Depth": 1,
            "ScoreCp": 100,
            "LowerBound": False,
            "UpperBound": False,
            "Nodes": 1,
            "TimeMs": 1,
            "Pv": ["f1f2"],
        }
        for game_number, (suffix, white, black) in enumerate(
            (("ab", "nnue-candidate", "hce-control"), ("ba", "hce-control", "nnue-candidate"))
        ):
            game_id = f"{pair_id}-{suffix}"
            post = f"fixture-post-{gate}-{game_number}"
            result_text = "1-0" if white == "nnue-candidate" else "0-1"
            records.extend(
                [
                    {
                        "RecordType": "gameStart", "GameId": game_id,
                        "PairId": pair_id, "Attempt": 1,
                        "StartedUtc": f"2026-07-23T00:00:0{1 + game_number * 2}Z",
                        "OpeningId": opening_id, "WhiteEngineId": white,
                        "BlackEngineId": black, "InitialOfen": fixture_initial,
                        "OpeningMoves": [],
                    },
                    {
                        "RecordType": "ply", "GameId": game_id, "Attempt": 1,
                        "Ply": 1, "EngineId": white, "Color": "white",
                        "PreOfen": fixture_initial, "PostOfen": post,
                        "BestMove": "f1f2", "San": "f2",
                        "WhiteClockBeforeMs": 60_000, "BlackClockBeforeMs": 60_000,
                        "WhiteClockAfterMs": 60_999 if mode == "clock" else 60_000,
                        "BlackClockAfterMs": 60_000,
                        "Search": {
                            "Command": search_command,
                            "BestMove": "f1f2",
                            "WallTimeMs": 1.0,
                            "DeadlineExceeded": False,
                            "ProcessExited": False,
                            "Info": [final_info],
                            "RawOutput": [final_info["Raw"], "bestmove f1f2"],
                            "StandardError": [],
                        },
                        "FinalInfo": final_info, "WhiteScoreCp": 100,
                        "Pv": {
                            "LegalPlies": 1,
                            "TotalPlies": 1,
                            "San": ["f2"],
                            "IsLegal": True,
                        },
                    },
                    {
                        "RecordType": "gameResult", "GameId": game_id,
                        "PairId": pair_id, "Attempt": 1,
                        "FinishedUtc": f"2026-07-23T00:00:0{2 + game_number * 2}Z",
                        "OpeningId": opening_id, "WhiteEngineId": white,
                        "BlackEngineId": black, "Result": result_text,
                        "Termination": "checkmate", "WinnerEngineId": "nnue-candidate",
                        "ScoreA": 1.0, "Plies": 1, "FinalOfen": post,
                        "WhiteClockMs": 60_999 if mode == "clock" else 60_000,
                        "BlackClockMs": 60_000,
                        "IllegalMoves": 0, "IllegalPvs": 0,
                        "ProtocolFailures": 0, "TimeForfeits": 0,
                    },
                ]
            )
        return config, suite, config_identity, suite_identity, records

    for fixture_gate in GATES:
        fixture_config, fixture_suite, fixture_config_id, fixture_suite_id, fixture_records = pinned_fixture(fixture_gate)
        serialized = b"".join(event_line(item) for item in fixture_records)
        parsed_fixture = _strict_event_log(serialized, f"{fixture_gate} pinned fixture")
        audit = _authenticate_raw_event_schedule(
            records=parsed_fixture,
            suite=fixture_suite,
            config=fixture_config,
            suite_identity=fixture_suite_id,
            config_identity=fixture_config_id,
            gate=fixture_gate,
        )
        if audit["completedGames"] != 2 or audit["rawColorCounts"]["white"] != 2:
            raise AssertionError(f"{fixture_gate} pinned-emitter fixture did not authenticate")
        mismatched_start = copy.deepcopy(parsed_fixture)
        mismatched_start[1]["InitialOfen"] = "forged start"
        try:
            _authenticate_raw_event_schedule(
                records=mismatched_start,
                suite=fixture_suite,
                config=fixture_config,
                suite_identity=fixture_suite_id,
                config_identity=fixture_config_id,
                gate=fixture_gate,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"{fixture_gate} mismatched start metadata was accepted")
        missing_bestmove = copy.deepcopy(parsed_fixture)
        missing_bestmove[2].pop("BestMove", None)
        missing_bestmove[2]["Search"].pop("BestMove", None)
        try:
            _authenticate_raw_event_schedule(
                records=missing_bestmove,
                suite=fixture_suite,
                config=fixture_config,
                suite_identity=fixture_suite_id,
                config_identity=fixture_config_id,
                gate=fixture_gate,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"{fixture_gate} successful ply without BestMove was accepted"
            )
        for label, mutate_san in (
            (
                "record SAN",
                lambda value: value[2].__setitem__("San", " f2"),
            ),
            (
                "PV SAN",
                lambda value: value[2]["Pv"].__setitem__("San", ["f2\n"]),
            ),
        ):
            malformed_san = copy.deepcopy(parsed_fixture)
            mutate_san(malformed_san)
            try:
                _authenticate_raw_event_schedule(
                    records=malformed_san,
                    suite=fixture_suite,
                    config=fixture_config,
                    suite_identity=fixture_suite_id,
                    config_identity=fixture_config_id,
                    gate=fixture_gate,
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"{fixture_gate} non-canonical {label} token was accepted"
                )
        illegal_pv_mismatch = copy.deepcopy(parsed_fixture)
        illegal_pv_mismatch[2]["Pv"]["IsLegal"] = False
        illegal_pv_mismatch[2]["Pv"]["IllegalMove"] = "f1f2"
        try:
            _authenticate_raw_event_schedule(
                records=illegal_pv_mismatch,
                suite=fixture_suite,
                config=fixture_config,
                suite_identity=fixture_suite_id,
                config_identity=fixture_config_id,
                gate=fixture_gate,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"{fixture_gate} illegal-PV counter mismatch was accepted"
            )

    engine_config, engine_suite, engine_config_id, engine_suite_id, engine_records = pinned_fixture("development")

    def reject_engine_fixture(changed: list[dict[str, Any]], label: str) -> None:
        try:
            _authenticate_raw_event_schedule(
                records=changed,
                suite=engine_suite,
                config=engine_config,
                suite_identity=engine_suite_id,
                config_identity=engine_config_id,
                gate="development",
            )
        except ValueError:
            return
        raise AssertionError(f"formal engine fixture accepted {label}")

    for label, mutate in (
        (
            "duplicate engine ID",
            lambda value: value[0]["Engines"].__setitem__(
                1, copy.deepcopy(value[0]["Engines"][0])
            ),
        ),
        (
            "non-object engine",
            lambda value: value[0]["Engines"].__setitem__(1, "hce-control"),
        ),
        (
            "extra effective option",
            lambda value: value[0]["Engines"][0]["Options"].__setitem__(
                "CandidateHint", "secret"
            ),
        ),
        (
            "duplicate candidate asset",
            lambda value: value[0]["Engines"][0]["ExternalAssets"].append(
                copy.deepcopy(value[0]["Engines"][0]["ExternalAssets"][0])
            ),
        ),
        (
            "candidate diagnostic network mismatch",
            lambda value: value[0]["Engines"][0]["StartupDiagnostics"].__setitem__(
                0,
                "info string Omega NNUE loaded: synthetic from C:/synthetic/other.onnx",
            ),
        ),
        (
            "control active diagnostic",
            lambda value: value[0]["Engines"][1]["StartupDiagnostics"].insert(
                0, "info string Omega NNUE evaluation active"
            ),
        ),
    ):
        changed = copy.deepcopy(engine_records)
        mutate(changed)
        reject_engine_fixture(changed, label)

    failure_config, failure_suite, failure_config_id, failure_suite_id, full_failure = pinned_fixture("development")
    failed_records = copy.deepcopy(full_failure[:4])
    successful_ply = failed_records[2]
    failed_result = failed_records[3]
    terminal_ply = copy.deepcopy(successful_ply)
    terminal_ply.update(
        {
            "Ply": 2,
            "EngineId": failed_records[1]["BlackEngineId"],
            "Color": "black",
            "PreOfen": successful_ply["PostOfen"],
            "PostOfen": None,
            "Error": "synthetic protocol failure",
            "Search": {
                "Command": "go nodes 30000", "WallTimeMs": 0.0,
                "DeadlineExceeded": False, "ProcessExited": False,
                "Info": [], "RawOutput": [], "StandardError": [],
            },
            "Pv": {
                "LegalPlies": 0, "TotalPlies": 0, "San": [],
                "IsLegal": True,
            },
        }
    )
    for absent in ("BestMove", "San", "FinalInfo", "WhiteScoreCp"):
        terminal_ply.pop(absent, None)
    failed_result["ProtocolFailures"] = 1
    failed_result["Termination"] = "protocol failure"
    failed_records.insert(3, terminal_ply)
    _authenticate_raw_event_schedule(
        records=_strict_event_log(b"".join(event_line(item) for item in failed_records), "error terminal fixture"),
        suite=failure_suite, config=failure_config,
        suite_identity=failure_suite_id, config_identity=failure_config_id,
        gate="development",
    )
    exited_records = copy.deepcopy(failed_records)
    exited_records[3].pop("Error", None)
    exited_records[3]["Search"]["ProcessExited"] = True
    _authenticate_raw_event_schedule(
        records=_strict_event_log(b"".join(event_line(item) for item in exited_records), "process-exit terminal fixture"),
        suite=failure_suite, config=failure_config,
        suite_identity=failure_suite_id, config_identity=failure_config_id,
        gate="development",
    )
    forged_null_error = copy.deepcopy(exited_records)
    forged_null_error[3]["Search"]["ProcessExited"] = False
    try:
        _authenticate_raw_event_schedule(
            records=forged_null_error,
            suite=failure_suite, config=failure_config,
            suite_identity=failure_suite_id, config_identity=failure_config_id,
            gate="development",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("forged null-error terminal without process exit was accepted")

    with tempfile.TemporaryDirectory(prefix="omega-confirmation-match-test-") as directory:
        root = Path(directory)
        context = _synthetic_context(root)
        event_path = _events(context, "development")
        event_path.parent.mkdir(parents=True)
        event_path.write_bytes(event_line(synthetic_run))
        first = protocol.identity(event_path)
        if _events_grew(context, "development", {"eventsBefore": first}):
            raise AssertionError("unchanged pending intent was treated as recoverable")
        with event_path.open("ab") as stream:
            stream.write(event_line(synthetic_start))
        if not _events_grew(context, "development", {"eventsBefore": first}):
            raise AssertionError("append-only crash recovery was not recognized")
        _event_prefix(
            event_path, first, exact=False, label="synthetic append-only prefix"
        )
        payload = bytearray(event_path.read_bytes())
        payload[1] ^= 1
        event_path.write_bytes(payload)
        try:
            _event_prefix(event_path, first, exact=False, label="synthetic tamper")
        except ValueError:
            pass
        else:
            raise AssertionError("event-prefix tamper was accepted")

    synthetic_identity = {
        "path": str(protocol.PROTOCOL_PATH),
        "bytes": protocol.PROTOCOL_PATH.stat().st_size,
        "sha256": protocol.sha256(protocol.PROTOCOL_PATH),
    }
    base_closure = {
        "schemaVersion": 1,
        "kind": CLOSURE_KIND,
        "protocol": synthetic_identity,
        "attemptIndex": 1,
        "createdUtc": "2026-07-23T00:00:00Z",
        "implementationSeal": synthetic_identity,
        "attemptReservation": synthetic_identity,
        "candidateClaim": None,
        "jointSuiteSeal": None,
        "authorization": None,
        "decisions": {gate: None for gate in GATES},
        "outcome": "aborted",
        "reason": "claim-publication-failure",
        "terminal": True,
        "nextAttemptAllowed": True,
        "candidateNetworkSha256": "0" * 64,
    }
    _validate_closure_shape(base_closure, 1)
    claimed_abort = {
        **base_closure,
        "candidateClaim": synthetic_identity,
        "reason": "operator-abort",
    }
    _validate_closure_shape(claimed_abort, 1)
    # Shape-level exact integer/type checks occur before identity dereference.
    for replacement in (1.0, True):
        changed = copy.deepcopy(base_closure)
        changed["attemptIndex"] = replacement
        try:
            _validate_closure_shape(changed, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("closure accepted a non-integer attempt index")
    for mutation in (
        {**base_closure, "extra": 1},
        {**base_closure, "reason": "Not Canonical"},
        {**base_closure, "nextAttemptAllowed": False},
        {**base_closure, "reason": "operator-abort"},
        {**base_closure, "candidateClaim": synthetic_identity},
        {**base_closure, "jointSuiteSeal": synthetic_identity},
    ):
        try:
            _validate_closure_shape(mutation, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed terminal closure was accepted")

    fake = _synthetic_context(Path(tempfile.gettempdir()) / "omega-confirmation-fake")
    exact_replay_bundle = readiness._exact_g5_prefix_replay_bundle()
    fake = Context(
        **{
            **fake.__dict__,
            "implementation_seal": {
                "prefixReplayBundle": exact_replay_bundle,
                "pinned": {
                    "prefixReplayAssembly": protocol.identity(
                        _IMPORTED_PREFIX_REPLAY_ROOT
                        / "OmegaOpeningPrefixReplay.dll",
                        relative=True,
                    ),
                    "omegaMatchRulesAssembly": protocol.identity(
                        _IMPORTED_G5_OMEGA_MATCH_RULES_ASSEMBLY,
                        relative=True,
                    ),
                },
            },
            "authorization": {
                "dotnetHost": copy.deepcopy(
                    fake.protocol_value["sharedRuntime"]["identities"][
                        "dotnetHost"
                    ]
                )
            },
        }
    )
    formal_section = {
        "minimumPairs": 128,
        "maximumPairs": 512,
        "nullElo": 15.0,
        "promotionThreshold": fake.promotion_e_value,
        "futilityThreshold": 20.0,
        "pairCount": 128,
        "decision": "promote",
        "signalPair": 128,
        "checkpoints": [
            {
                "pairs": 128,
                "promotionEValue": fake.promotion_e_value,
            }
        ],
    }
    _validate_formal_report(
        fake, "equal-node", {"sequentialGate": formal_section}
    )
    for field, replacement in (
        ("promotionThreshold", 200.0),
        ("minimumPairs", 64),
        ("signalPair", 126),
    ):
        changed_section = copy.deepcopy(formal_section)
        changed_section[field] = replacement
        try:
            _validate_formal_report(
                fake, "equal-node", {"sequentialGate": changed_section}
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"formal report accepted tampered {field}")

    prior_progress = {
        "progress": {
            "finishedGames": 256,
            "completePairs": 128,
            "balancedPrefixPairs": 128,
        }
    }
    exact_resume = {
        "progress": {
            "finishedGames": 264,
            "completePairs": 132,
            "balancedPrefixPairs": 132,
        }
    }
    if not _budget_check(fake, "equal-node", exact_resume, prior_progress, 2)[
        "exact"
    ]:
        raise AssertionError("exact four-pair resume failed its budget audit")
    short_resume = copy.deepcopy(exact_resume)
    short_resume["progress"]["completePairs"] = 131
    short_resume["progress"]["balancedPrefixPairs"] = 131
    if _budget_check(fake, "equal-node", short_resume, prior_progress, 2)["exact"]:
        raise AssertionError("short formal resume passed its exact budget audit")

    fixture_raw_identity = {"bytes": 1, "sha256": "a" * 64}
    fixture_color_identity = {"bytes": 1, "sha256": "b" * 64}
    fixture_terminal_identity = {"bytes": 1, "sha256": "c" * 64}
    fixture_raw_authentication = {
        "schemaVersion": 1,
        "schedule": {
            "allStartsResultsAndPliesAuthenticated": True,
            "processExitTerminalPlies": 0,
            "successfulPlies": 1,
        },
        "rulesReplay": {
            "contract": "ChessLib legal transcript replay",
            "rawEvent": fixture_raw_identity,
            "successfulPlies": 1,
            "parityMatches": 1,
            "illegalOrMalformedMoves": 0,
            "runtimeBundle": exact_replay_bundle,
            "helper": protocol.identity(
                _IMPORTED_PREFIX_REPLAY_ROOT / "OmegaOpeningPrefixReplay.dll",
                relative=False,
            ),
            "rules": protocol.identity(
                _IMPORTED_PREFIX_REPLAY_ROOT / "ChessLib.dll",
                relative=False,
            ),
            "omegaMatchRules": protocol.identity(
                _IMPORTED_G5_OMEGA_MATCH_RULES_ASSEMBLY,
                relative=False,
            ),
            "dotnetHost": protocol.identity(
                protocol.protocol_path(
                    fake.authorization["dotnetHost"]["path"],
                    "fixture dotnet host",
                ),
                relative=False,
            ),
            "rulesByteParityWithOmegaMatch": True,
            "passes": True,
        },
        "colorAdapter": {
            "transformVersion": COLOR_ADAPTER_VERSION,
            "acceptedRawTokens": ["white", "black"],
            "derivedTokens": ["w", "b"],
            "raw": fixture_raw_identity,
            "derived": fixture_color_identity,
            "transformedPlies": 1,
            "otherFieldsChanged": 0,
            "rawEvidenceRetained": True,
        },
        "terminalAdapter": {
            "transformVersion": TERMINAL_ADAPTER_VERSION,
            "input": fixture_color_identity,
            "derived": fixture_terminal_identity,
            "transformedTerminalPlies": 0,
            "errorFieldsChanged": 0,
            "nonErrorFieldsChanged": 0,
            "rawEvidenceRetained": True,
        },
        "preparedCompatibilityView": {
            "raw": fixture_raw_identity,
            "derived": fixture_terminal_identity,
            "changedFields": {"Color": 1, "Error": 0},
            "totalFieldsChanged": 1,
            "unauthorizedFieldsChanged": 0,
            "rawEvidenceRetained": True,
        },
        "assessmentInput": {
            "view": "frozen-core-compatibility-events",
            "identity": fixture_terminal_identity,
        },
    }
    safe_report = {
        "rawEventAuthentication": fixture_raw_authentication,
        "safety": {
            "failures": 0,
            "passes": True,
            "illegalMoves": 0,
            "illegalPvs": 0,
            "protocolFailures": 0,
            "timeForfeits": 0,
            "abandonedAttempts": 0,
        },
        "developmentScreen": {"decision": "pass"},
        "confirmationBudgetAudit": {"exact": True},
    }
    recovered_completion = {
        "returnCode": None,
        "recoveredFromEventGrowth": True,
        "postLaunchProcesses": {"relevantProcesses": []},
    }
    if (
        _assessment_decision(
            fake, "development", safe_report, recovered_completion
        )
        != "safety-fail"
    ):
        raise AssertionError("crash-recovered launch was treated as clean evidence")
    nonzero_completion = {
        "returnCode": 7,
        "recoveredFromEventGrowth": False,
        "postLaunchProcesses": {"relevantProcesses": []},
    }
    if (
        _assessment_decision(fake, "development", safe_report, nonzero_completion)
        != "safety-fail"
    ):
        raise AssertionError("nonzero launcher return was not a safety failure")
    equal_time_report = {
        "rawEventAuthentication": copy.deepcopy(fixture_raw_authentication),
        "safety": copy.deepcopy(safe_report["safety"]),
        "confirmationBudgetAudit": {"exact": True},
        "sequentialGate": {
            **formal_section,
            "decision": "continue",
            "signalPair": None,
        },
        "equalTimeAudit": {
            "serialized": False,
            "serializationComplete": True,
            "overlappingGames": [["a", "b"]],
            "unfinishedAttempts": [],
            "missingTelemetrySearches": 0,
            "invalidTelemetrySearches": 0,
            "deadlineFailures": 0,
            "integrityPasses": False,
            "passes": False,
        },
    }
    clean_completion = {
        "returnCode": 0,
        "recoveredFromEventGrowth": False,
        "postLaunchProcesses": {"relevantProcesses": []},
    }
    wrong_rules_report = copy.deepcopy(safe_report)
    wrong_rules_report["rawEventAuthentication"]["rulesReplay"]["rules"][
        "sha256"
    ] = "0" * 64
    if (
        _assessment_decision(
            fake, "development", wrong_rules_report, clean_completion
        )
        != "safety-fail"
    ):
        raise AssertionError("non-Generation-5 replay rules were accepted")
    wrong_replay_count = copy.deepcopy(safe_report)
    wrong_replay_count["rawEventAuthentication"]["schedule"][
        "successfulPlies"
    ] = 2
    if (
        _assessment_decision(
            fake, "development", wrong_replay_count, clean_completion
        )
        != "safety-fail"
    ):
        raise AssertionError("schedule/replay ply-count mismatch was accepted")
    wrong_dotnet = copy.deepcopy(safe_report)
    wrong_dotnet["rawEventAuthentication"]["rulesReplay"]["dotnetHost"][
        "sha256"
    ] = "0" * 64
    if (
        _assessment_decision(fake, "development", wrong_dotnet, clean_completion)
        != "safety-fail"
    ):
        raise AssertionError("unauthorized rules-replay dotnet host was accepted")
    if (
        _assessment_decision(
            fake, "equal-time", equal_time_report, clean_completion
        )
        != "safety-fail"
    ):
        raise AssertionError("overlapping equal-time intervals were accepted")

    if tuple(FORMAL_GATES) != (
        "equal-node",
        "equal-time",
        "normal-start-clock",
    ):
        raise AssertionError("formal three-gate conjunction changed")
    stage_specs = _stage_spec(protocol_value, _synthetic_stage_seeds(1))
    normal_spec = stage_specs["normal-start-clock"]
    if (
        normal_spec.get("mode") != "clock"
        or normal_spec.get("roots") != 512
        or normal_spec.get("rootsPerPhase") != 128
        or normal_spec.get("rootsPerPhaseSide") != 128
        or normal_spec.get("initialTimeMs") != 60_000
        or normal_spec.get("incrementMs") != 1_000
        or normal_spec.get("searchTimeoutMs") != 65_000
        or "nodes" in normal_spec
        or "moveTimeMs" in normal_spec
    ):
        raise AssertionError("normal-start clock stage profile changed")

    normal_report = {
        "rawEventAuthentication": copy.deepcopy(fixture_raw_authentication),
        "safety": copy.deepcopy(safe_report["safety"]),
        "confirmationBudgetAudit": {"exact": True},
        "sequentialGate": {
            **formal_section,
            "decision": "continue",
            "signalPair": None,
        },
        "clockAudit": {
            "serialized": True,
            "serializationComplete": True,
            "overlappingGames": [],
            "unfinishedAttempts": [],
            "invalidClockSearches": 0,
            "deadlineFailures": 0,
            "searches": 256,
            "maximumClockRoundingErrorMs": 1.0,
            "passes": True,
        },
    }
    normal_report["rawEventAuthentication"]["assessmentInput"] = {
        "view": "authenticated-raw-events",
        "identity": fixture_raw_identity,
    }
    if (
        _assessment_decision(
            fake, "normal-start-clock", normal_report, clean_completion
        )
        != "continue"
    ):
        raise AssertionError("clean normal-start clock evidence was rejected")
    bad_normal_report = copy.deepcopy(normal_report)
    bad_normal_report["clockAudit"]["invalidClockSearches"] = 1
    bad_normal_report["clockAudit"]["passes"] = False
    if (
        _assessment_decision(
            fake, "normal-start-clock", bad_normal_report, clean_completion
        )
        != "safety-fail"
    ):
        raise AssertionError("invalid normal clock evidence was accepted")

    first_three = {
        "development": {"decision": "pass"},
        "equal-node": {"decision": "promote"},
        "equal-time": {"decision": "promote"},
    }
    if _terminal_outcome(first_three) is not None:
        raise AssertionError("three-stage evidence bypassed the normal clock gate")
    if _terminal_outcome(
        {**first_three, "normal-start-clock": {"decision": "promote"}}
    ) != ("confirmed", "confirmed"):
        raise AssertionError("four-gate success conjunction changed")
    if _terminal_outcome(
        {**first_three, "normal-start-clock": {"decision": "futility"}}
    ) != ("failed", "normal-start-clock-futility"):
        raise AssertionError("normal-start failure classification changed")

    windows = _parse_windows_processes(
        '"dotnet.exe","100","Console","1","1,024 K"\n'
        '"senpai.exe","123","Console","1","1,024 K"\n'
        '"other.exe","456","Console","1","1,024 K"\n'
        '"OmegaMatch.exe","789","Console","1","1,024 K"\n'
    )
    if windows != [
        {"name": "dotnet.exe", "pid": 100},
        {"name": "omegamatch.exe", "pid": 789},
        {"name": "senpai.exe", "pid": 123},
    ]:
        raise AssertionError("Windows process parser changed")
    if _parse_posix_processes("123 /tmp/senpai.exe\n456 other\n") != [
        {"name": "senpai.exe", "pid": 123}
    ]:
        raise AssertionError("POSIX process parser changed")
    if protocol_value["stages"]["equal-node"]["resumePairBudget"] != 4:
        raise AssertionError("formal resume budget changed")
    try:
        Path.cwd().resolve().relative_to(_PREAUTH_REPO)
        running_inside_repository = True
    except ValueError:
        running_inside_repository = False
    if running_inside_repository:
        with tempfile.TemporaryDirectory(
            prefix="omega-confirmation-outside-cwd-"
        ) as directory:
            outside = Path(directory).resolve()
            try:
                outside.relative_to(_PREAUTH_REPO)
            except ValueError:
                pass
            else:
                raise AssertionError("outside-cwd probe remained inside repository")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).resolve()),
                    "self-test",
                ],
                cwd=outside,
                env=readiness._sanitized_environment(),
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
            if (
                completed.returncode != 0
                or "Open-confirmation match orchestrator self-test passed"
                not in completed.stdout
            ):
                raise AssertionError(
                    "outside-repository cwd self-test failed: "
                    f"{completed.stderr[-2000:]}"
                )
    print("Open-confirmation match orchestrator self-test passed")


def _attempt_index(value: str) -> int:
    try:
        index = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("attempt must be an integer") from error
    try:
        protocol.attempt_namespace(index)
    except (ValueError, OverflowError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return index


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    launch = commands.add_parser("launch")
    launch.add_argument("--attempt", required=True, type=_attempt_index)
    launch.add_argument("--gate", required=True, choices=GATES)
    launch.add_argument("--action", choices=("run", "resume"))
    launch.add_argument("--authorization", type=Path)

    assess = commands.add_parser("assess")
    assess.add_argument("--attempt", required=True, type=_attempt_index)
    assess.add_argument("--gate", required=True, choices=GATES)
    assess.add_argument("--authorization", type=Path)

    idle = commands.add_parser("attest-idle")
    idle.add_argument("--attempt", required=True, type=_attempt_index)
    idle.add_argument("--gate", required=True, choices=sorted(TIMED_GATES))
    idle.add_argument("--operator", required=True)
    idle.add_argument("--authorization", type=Path)

    verify = commands.add_parser("verify-state")
    verify.add_argument("--attempt", required=True, type=_attempt_index)
    verify.add_argument("--authorization", type=Path)

    close = commands.add_parser("attempt-closure")
    close.add_argument("--attempt", required=True, type=_attempt_index)
    close.add_argument("--authorization", type=Path)
    close.add_argument("--abort-reason", choices=sorted(ABORT_REASONS))

    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "self-test":
        _self_test()
        return 0
    with readiness._operation_lock():
        if args.command == "launch":
            _launch(args)
        elif args.command == "assess":
            _assess_command(args)
        elif args.command == "attest-idle":
            _attest_idle(args)
        elif args.command == "verify-state":
            _verify_state(args)
        else:
            _attempt_closure(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
