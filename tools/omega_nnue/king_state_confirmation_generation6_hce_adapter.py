#!/usr/bin/env python3
"""Authenticated Generation-6 bridge into the frozen v2 HCE gates.

This is a namespace adapter, not a new match implementation.  It authenticates
the Generation-6 handoff, seals candidate-blind v2 suites, constructs the exact
v2 engine configurations for the selected G6 executable/network, and invokes
the byte-pinned v2 launch/assessment functions for equal-node, equal-time, and
normal-start-clock.  Only context revalidation and the practical-to-equal-node
predecessor edge are adapted; the frozen gate, event-authentication, rules-
replay, sequential-e-process, timing, clock, and safety implementations remain
unchanged.

Importing this module writes nothing and launches nothing.  Every publication
uses final-name O_EXCL semantics.  The one G6 entropy draw remains private; the
adapter derives deterministic signed-Int32 sampler seeds from its committed
HMAC stage keys only after a durable adapter reservation.  Raw entropy and HMAC
keys are never copied into adapter artifacts.
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import copy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import types
from typing import Any, Callable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
ADAPTER_ID = "omega-nnue-generation6-frozen-v2-hce-adapter-v1"
RESERVATION_KIND = f"{ADAPTER_ID}-reservation"
IMPLEMENTATION_KIND = f"{ADAPTER_ID}-implementation-seal"
AUDIT_KIND = f"{ADAPTER_ID}-match-audit"
AUTHORIZATION_KIND = f"{ADAPTER_ID}-authorization"
SELECTOR_INTENT_KIND = "omega-nnue-open-confirmation-v2-sampling-intent"
V2_CLOSURE_KIND = "omega-nnue-open-confirmation-v2-attempt-closure"

FORMAL_GATES = ("equal-node", "equal-time", "normal-start-clock")
ALL_V2_GATES = ("development", *FORMAL_GATES)
TIMED_GATES = frozenset(("equal-time", "normal-start-clock"))
SUCCESS_DECISION = {gate: "promote" for gate in FORMAL_GATES}
SEED_KEY_STAGE = {
    "development": "practical-opening-selection",
    "equal-node": "equal-node",
    "equal-time": "equal-time",
    "normal-start-clock": "normal-start-clock",
}
SEED_DERIVATION_DOMAIN = b"omega-nnue-g6-v2-dotnet-seed-v1\x00"
UINT31_MAX = (1 << 31) - 1
HEX256 = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?Z$"
)
SAFE_ABORT = re.compile(r"^[a-z][a-z0-9-]{0,79}$")

REPO = Path(__file__).resolve().parents[2]
TOOL_PATH = Path(__file__).resolve()
G6_RELATIVE = "tools/omega_nnue/king_state_confirmation_generation6.py"
G6_PROTOCOL_RELATIVE = "validation/omega-nnue-generation6-promotion-protocol.json"
G6_AUTHORITY_MODULE = f"{__name__}.__g6_authority"
MATCHES_AUTHORITY_MODULE = f"{__name__}.__v2_matches_authority"
SUCCESSOR_CLOSURE_KIND = (
    "omega-nnue-generation6-promotion-confirmation-v1-attempt-closure"
)
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
V2_AUTHORITIES: Mapping[str, tuple[str, int, str]] = {
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
}

_G6_MODULE: types.ModuleType | None = None
_G6_IDENTITY: dict[str, Any] | None = None
_G6_BINDINGS: dict[str, Callable[..., Any]] = {}
_G6_PIN_TABLE: dict[str, tuple[str, int, str]] | None = None
_MATCHES_MODULE: types.ModuleType | None = None
_MATCHES_ORIGINALS: dict[str, Callable[..., Any]] = {}
_MATCHES_ENTRYPOINTS: dict[str, Callable[..., Any]] = {}
_TERMINAL_REPLAY_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "omega_g6_hce_terminal_replay_depth", default=0
)
_HISTORICAL_REPLAY_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "omega_g6_hce_historical_replay_depth", default=0
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _utc_now_g6() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or CANONICAL_UTC.fullmatch(value) is None:
        raise ValueError(f"{label} is not canonical UTC")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def _unique_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"strict JSON repeats key {key!r}")
        result[key] = value
    return result


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _plain_path(path: Path | str) -> Path:
    """Return an absolute path without following a link or junction."""

    value = Path(path).expanduser()
    if not value.is_absolute():
        value = Path.cwd() / value
    return Path(os.path.abspath(os.fspath(value)))


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    probe = getattr(path, "is_junction", None)
    return bool(callable(probe) and probe())


def _verify_plain_directory(path: Path | str) -> Path:
    """Reject reparse traversal and create no directory implicitly."""

    value = _plain_path(path)
    cursor = value
    while True:
        info = os.lstat(cursor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_link_or_junction(cursor)
        ):
            raise ValueError(f"unsafe directory ancestor: {cursor}")
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    return value


def _mkdir_plain(path: Path | str) -> Path:
    value = _plain_path(path)
    missing: list[Path] = []
    cursor = value
    while not os.path.lexists(cursor):
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise ValueError(f"cannot establish a safe directory root: {value}")
        cursor = parent
    _verify_plain_directory(cursor)
    for item in reversed(missing):
        os.mkdir(item, 0o755)
        _verify_plain_directory(item)
    return _verify_plain_directory(value)


def _stat_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    # Windows currently reports incompatible ``st_ctime_ns`` values for the
    # same file through lstat() and fstat().  Its explicit birth time is stable
    # across both APIs; POSIX falls back to metadata-change time.
    identity_time = int(getattr(info, "st_birthtime_ns", info.st_ctime_ns))
    return (
        stat.S_IFMT(info.st_mode),
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_nlink),
        int(info.st_size),
        int(info.st_mtime_ns),
        identity_time,
    )


def _measure_plain_file(
    path: Path | str, *, capture: bool
) -> tuple[Path, dict[str, Any], bytes | None]:
    """Hash one opened inode and prove the pathname still names that inode."""

    value = _plain_path(path)
    _verify_plain_directory(value.parent)
    before = os.lstat(value)
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or _is_link_or_junction(value)
        or before.st_nlink != 1
    ):
        raise ValueError(f"identity target is not one plain unlinked file: {value}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(value, flags)
    chunks: list[bytes] | None = [] if capture else None
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _stat_signature(opened) != _stat_signature(before)
        ):
            raise ValueError(f"identity target changed while opened: {value}")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            if chunks is not None:
                chunks.append(block)
        after = os.fstat(descriptor)
        if _stat_signature(after) != _stat_signature(opened):
            raise ValueError(f"identity target changed while hashed: {value}")
    finally:
        os.close(descriptor)
    final = os.lstat(value)
    if (
        not stat.S_ISREG(final.st_mode)
        or stat.S_ISLNK(final.st_mode)
        or _is_link_or_junction(value)
        or final.st_nlink != 1
        or _stat_signature(final) != _stat_signature(after)
    ):
        raise ValueError(f"identity pathname was substituted while hashed: {value}")
    _verify_plain_directory(value.parent)
    record = {
        "path": str(value),
        "bytes": int(after.st_size),
        "sha256": digest.hexdigest(),
    }
    return value, record, b"".join(chunks) if chunks is not None else None


def _read_plain_bytes(path: Path | str) -> tuple[Path, bytes, dict[str, Any]]:
    value, record, payload = _measure_plain_file(path, capture=True)
    assert payload is not None
    return value, payload, record


def _load_json(path: Path | str, label: str) -> dict[str, Any]:
    path, payload, _ = _read_plain_bytes(path)
    if not payload.endswith(b"\n") or b"\r" in payload:
        raise ValueError(f"{label} is not canonical LF-terminated JSON")
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"{label} contains non-finite JSON {token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not strict JSON") from error
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def _exact_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} field inventory changed")
    return value


def _same(left: Any, right: Any, label: str) -> None:
    if type(left) is not type(right) or left != right:
        raise ValueError(f"{label} changed")


def _sha256(path: Path | str) -> str:
    return _measure_plain_file(path, capture=False)[1]["sha256"]


def identity(path: Path | str) -> dict[str, Any]:
    return _measure_plain_file(path, capture=False)[1]


def _identity_shape(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity fields changed")
    if (
        type(value.get("path")) is not str
        or not value["path"]
        or type(value.get("bytes")) is not int
        or value["bytes"] < 0
        or type(value.get("sha256")) is not str
        or HEX256.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(value)


def verify_identity(value: Any, label: str) -> Path:
    record = _identity_shape(value, label)
    path = _plain_path(record["path"])
    _same(record, identity(path), label)
    return path


def _exclusive_bytes(path: Path | str, payload: bytes, *, mode: int = 0o600) -> dict[str, Any]:
    path = _plain_path(path)
    _mkdir_plain(path.parent)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return identity(path)


def _exclusive_json(path: Path | str, value: Mapping[str, Any]) -> dict[str, Any]:
    return _exclusive_bytes(path, _canonical_json(value))


def _authority_identities() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, (relative, size, digest) in V2_AUTHORITIES.items():
        path = REPO / relative
        actual = identity(path)
        if actual["bytes"] != size or actual["sha256"] != digest:
            raise ValueError(f"frozen v2 authority changed: {name}")
        result[name] = actual
    return result


def _load_exact_module(
    name: str, relative: str, size: int, digest: str
) -> types.ModuleType:
    path, payload, source = _read_plain_bytes(REPO / relative)
    if source["bytes"] != size or source["sha256"] != digest:
        raise ImportError(f"authenticated module changed: {path}")
    if name in sys.modules:
        raise ImportError(f"refusing preloaded adapter authority: {name}")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    module.__loader__ = None
    sys.modules[name] = module
    try:
        exec(compile(payload, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _bind_g6_identity(record: Mapping[str, Any]) -> types.ModuleType:
    """Bind G6 through its pre-result preregistration implementation identity.

    The dependency direction is deliberately acyclic: G6 pins this adapter,
    while this adapter accepts no compiled-in G6 digest.  At runtime it binds
    the exact G6 bytes that the authenticated preregistration froze before any
    held-out or match result access, then proves those G6 bytes pin this exact
    adapter back.
    """

    global _G6_MODULE, _G6_IDENTITY, _G6_PIN_TABLE
    source = _identity_shape(record, "preregistered G6 implementation")
    path = verify_identity(source, "preregistered G6 implementation")
    canonical = (REPO / G6_RELATIVE).resolve()
    if path != canonical:
        raise ImportError("preregistered Generation-6 implementation is noncanonical")
    if _G6_IDENTITY is not None and _G6_IDENTITY != source:
        raise ImportError("Generation-6 implementation binding changed in process")
    if _G6_MODULE is None:
        _G6_MODULE = _load_exact_module(
            G6_AUTHORITY_MODULE,
            G6_RELATIVE,
            source["bytes"],
            source["sha256"],
        )
        _G6_IDENTITY = dict(source)
        for name in (
            "verify_hce_handoff",
            "verify_preregistration",
            "verify_candidate_claim",
            "verify_practical_decision",
            "_attempt_paths",
            "_global_attempt_state",
            "bridge_hce_attempt_closure",
        ):
            function = getattr(_G6_MODULE, name, None)
            if not callable(function) or function.__module__ != _G6_MODULE.__name__:
                raise ImportError(f"Generation-6 callable is not authentic: {name}")
            _G6_BINDINGS[name] = function
        _G6_PIN_TABLE = dict(_G6_MODULE.PINNED_AUTHORITIES)
    if Path(str(_G6_MODULE.__file__)).resolve() != canonical:
        raise ImportError("Generation-6 successor module escaped its path")
    current = identity(canonical)
    if current != source or current != _G6_IDENTITY:
        raise ImportError("preregistered Generation-6 source bytes changed")
    if (
        _G6_PIN_TABLE is None
        or dict(_G6_MODULE.PINNED_AUTHORITIES) != _G6_PIN_TABLE
        or any(getattr(_G6_MODULE, name, None) is not function for name, function in _G6_BINDINGS.items())
    ):
        raise ImportError("Generation-6 authority binding changed in memory")
    adapter_pin = _G6_MODULE.PINNED_AUTHORITIES.get("hceLaunchAdapter")
    own = identity(TOOL_PATH)
    if (
        type(adapter_pin) not in (tuple, list)
        or len(adapter_pin) != 3
        or adapter_pin[0]
        != G6_RELATIVE.replace(
            "king_state_confirmation_generation6.py",
            "king_state_confirmation_generation6_hce_adapter.py",
        )
        or adapter_pin[1] != own["bytes"]
        or adapter_pin[2] != own["sha256"]
    ):
        raise ImportError("preregistered Generation-6 authority does not pin this adapter")
    return _G6_MODULE


def _bind_g6_from_preregistration(path: Path | str) -> types.ModuleType:
    preregistration = _load_json(path, "G6 preregistration implementation envelope")
    return _bind_g6_identity(
        _identity_shape(
            preregistration.get("implementation"),
            "G6 preregistration implementation",
        )
    )


def _g6() -> types.ModuleType:
    if _G6_MODULE is None or _G6_IDENTITY is None:
        raise RuntimeError("Generation-6 authority is not bound to a preregistration")
    return _bind_g6_identity(_G6_IDENTITY)


def _matches() -> types.ModuleType:
    global _MATCHES_MODULE
    if _MATCHES_MODULE is None:
        relative, size, digest = V2_AUTHORITIES["v2Matches"]
        _MATCHES_MODULE = _load_exact_module(
            MATCHES_AUTHORITY_MODULE,
            relative,
            size,
            digest,
        )
        if tuple(_MATCHES_MODULE.GATES) != ALL_V2_GATES:
            raise ImportError("frozen v2 gate inventory changed")
        for name in (
            "_context",
            "_rehash_context",
            "_verify_active_launch_state",
            "_predecessor_decision",
        ):
            _MATCHES_ORIGINALS[name] = getattr(_MATCHES_MODULE, name)
        for name in ("_launch", "_assess_command", "_attest_idle"):
            _MATCHES_ENTRYPOINTS[name] = getattr(_MATCHES_MODULE, name)
        _MATCHES_MODULE._context = _frozen_context
        _MATCHES_MODULE._rehash_context = _frozen_rehash_context
        _MATCHES_MODULE._verify_active_launch_state = _frozen_active_launch_state
        _MATCHES_MODULE._predecessor_decision = _frozen_predecessor_decision
    _MATCHES_MODULE._assert_exact_bindings()
    if any(
        getattr(_MATCHES_MODULE, name, None) is not function
        for name, function in _MATCHES_ENTRYPOINTS.items()
    ):
        raise ImportError("frozen v2 launch entrypoint changed in memory")
    return _MATCHES_MODULE


HANDOFF_INPUT_FIELDS = {
    "preregistration",
    "claim",
    "suite",
    "practicalDecision",
    "handoff",
}


def _authenticate_handoff(path: Path | str) -> dict[str, Any]:
    handoff_path = _plain_path(path)
    raw = _load_json(handoff_path, "G6 HCE handoff envelope")
    preregistration = verify_identity(raw.get("preregistration"), "handoff preregistration")
    g6 = _bind_g6_from_preregistration(preregistration)
    claim = verify_identity(raw.get("claim"), "handoff claim")
    practical_decision = verify_identity(
        raw.get("practicalDecision"), "handoff practical decision"
    )
    practical = _load_json(practical_decision, "handoff practical decision")
    suite = verify_identity(practical.get("suite"), "handoff practical suite")
    verified = g6.verify_hce_handoff(
        handoff_path,
        preregistration=preregistration,
        claim=claim,
        suite=suite,
        practical_decision=practical_decision,
    )
    practical_verified = g6.verify_practical_decision(
        practical_decision,
        preregistration=preregistration,
        claim=claim,
        suite=suite,
    )
    transcript = practical_verified.get("executionTranscript")
    if transcript is None or practical_verified.get("incidentHandoff") is not None:
        raise ValueError("HCE handoff lacks the normal practical execution transcript")
    verify_identity(transcript, "HCE practical execution transcript")
    if [item["gate"] for item in verified["formalGates"]] != list(FORMAL_GATES):
        raise ValueError("HCE handoff formal-gate order changed")
    expected_options = {
        "common": dict(g6.FORMAL_HCE_COMMON_OPTIONS),
        "candidate": {
            "UseOmegaNNUE": "true",
            "OmegaNNUEFile": verified["candidateNetwork"]["path"],
        },
        "hceControl": {
            "UseOmegaNNUE": "false",
            "OmegaNNUEFile": "<empty>",
        },
    }
    _same(verified.get("engineOptions"), expected_options, "handoff engine options")
    return {
        "preregistration": preregistration,
        "claim": claim,
        "suite": suite,
        "practicalDecision": practical_decision,
        "handoff": handoff_path,
        "handoffDocument": verified,
        "preregistrationDocument": g6.verify_preregistration(preregistration),
        "claimDocument": g6.verify_candidate_claim(
            claim, preregistration=preregistration
        ),
        "practicalDecisionDocument": practical_verified,
    }


def _adapter_roots(preregistration: Mapping[str, Any]) -> dict[str, Path]:
    artifact_root = _plain_path(str(preregistration["artifactRoot"]))
    return {
        "adapterRoot": artifact_root / "hce-frozen-v2-adapter",
        "shadowArtifactRoot": artifact_root / "hce-frozen-v2-adapter/attempts",
        "selectorWorkspaceRoot": artifact_root
        / "hce-frozen-v2-adapter/selector-workspaces",
    }


def attempt_paths(preregistration: Mapping[str, Any], index: int) -> dict[str, Any]:
    if type(index) is not int or isinstance(index, bool) or not 1 <= index <= 377:
        raise ValueError("adapter attempt index must be an admissible exact integer")
    roots = _adapter_roots(preregistration)
    name = f"attempt-{index:06d}"
    root = roots["shadowArtifactRoot"] / name
    selector = roots["selectorWorkspaceRoot"] / name
    sealed = root / "sealed"
    selector_sealed = selector / "sealed"
    stages = {gate: root / gate for gate in ALL_V2_GATES}
    configs = {gate: sealed / f"{gate}-match.json" for gate in ALL_V2_GATES}
    suites = {gate: selector_sealed / f"{gate}-suite.json" for gate in ALL_V2_GATES}
    return {
        **roots,
        "root": root,
        "selectorRoot": selector,
        "sampler": selector / "sampler",
        "selectorSealed": selector_sealed,
        "samplingIntent": selector / "sampling-intent.json",
        "jointSuiteSeal": selector_sealed / "joint-suite.seal.json",
        "sealed": sealed,
        "reservation": root / "adapter-reservation.json",
        "implementationSeal": sealed / "implementation-seal.json",
        "claim": None,
        "authorization": sealed / "match-authorization.json",
        "coreSeal": sealed / "core-seal.json",
        "audit": sealed / "match-audit.json",
        "configs": configs,
        "suites": suites,
        "stages": stages,
        "attemptClosure": root / "attempt-closure.json",
        "programSuccess": root / "program-success.json",
    }


def _stage_seeds(inputs: Mapping[str, Any]) -> dict[str, int]:
    g6 = _g6()
    claim = inputs["claimDocument"]
    index = claim["attemptIndex"]
    prereg = inputs["preregistrationDocument"]
    private = g6._attempt_paths(Path(prereg["artifactRoot"]), index)["entropy"]
    entropy = g6._safe_file(private).read_bytes()
    if g6._entropy_commitment(entropy) != claim["entropyCommitment"]:
        raise ValueError("private attempt entropy no longer matches its commitment")
    return _derive_seeds_from_entropy(
        entropy,
        index,
        claim["stageSeedCommitments"],
        g6_module=g6,
    )


def _derive_seeds_from_entropy(
    entropy: bytes,
    index: int,
    commitments: Mapping[str, Any],
    *,
    g6_module: types.ModuleType | None = None,
) -> dict[str, int]:
    g6 = _g6() if g6_module is None else g6_module
    if type(entropy) is not bytes or len(entropy) != g6.ENTROPY_BYTES:
        raise ValueError("adapter entropy length changed")
    if type(index) is not int or isinstance(index, bool) or not 1 <= index <= 377:
        raise ValueError("adapter seed attempt index changed")
    result: dict[str, int] = {}
    used: set[int] = set()
    for gate in ALL_V2_GATES:
        stage = SEED_KEY_STAGE[gate]
        key = g6._stage_key(entropy, index, stage)
        expected = hashlib.sha256(
            b"public-stage-commitment-v1\x00" + key
        ).hexdigest()
        if commitments.get(stage) != expected:
            raise ValueError(f"{gate} HMAC stage commitment changed")
        for counter in range(1 << 20):
            digest = hmac.new(
                key,
                SEED_DERIVATION_DOMAIN
                + gate.encode("ascii")
                + b"\x00"
                + counter.to_bytes(4, "big"),
                hashlib.sha256,
            ).digest()
            seed = int.from_bytes(digest[:4], "big") & UINT31_MAX
            if seed not in used:
                used.add(seed)
                result[gate] = seed
                break
        else:  # pragma: no cover - cryptographically unreachable
            raise RuntimeError("could not derive a distinct signed-Int32 stage seed")
    return result


def _seed_commitments(inputs: Mapping[str, Any]) -> dict[str, str]:
    claim = inputs["claimDocument"]
    return {
        gate: claim["stageSeedCommitments"][SEED_KEY_STAGE[gate]]
        for gate in ALL_V2_GATES
    }


@contextlib.contextmanager
def _patched_readiness_paths(
    readiness: types.ModuleType, paths: Mapping[str, Any]
) -> Iterator[None]:
    original_artifact = readiness.ARTIFACT_ROOT
    original_selector = readiness.SELECTOR_WORKSPACE_ROOT
    readiness.ARTIFACT_ROOT = Path(paths["shadowArtifactRoot"]).resolve()
    readiness.SELECTOR_WORKSPACE_ROOT = Path(paths["selectorWorkspaceRoot"]).resolve()
    try:
        yield
    finally:
        readiness.ARTIFACT_ROOT = original_artifact
        readiness.SELECTOR_WORKSPACE_ROOT = original_selector


RESERVATION_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "adapter",
    "v2Authorities",
    "hceHandoff",
    "preregistration",
    "candidateClaim",
    "practicalDecision",
    "attemptIndex",
    "attemptBeta",
    "promotionLogThreshold",
    "candidateEngine",
    "candidateNetwork",
    "engineOptions",
    "formalGates",
    "stageSeedCommitments",
    "reservationConsumesHceStage",
    "stageSeedsDisclosed",
    "rawEntropyPublished",
    "hmacKeysPublished",
    "matchResultsRead",
}


def _reservation_value(inputs: Mapping[str, Any]) -> dict[str, Any]:
    handoff = inputs["handoffDocument"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RESERVATION_KIND,
        "status": "reserved-before-v2-selector-seed-disclosure",
        "createdUtc": _utc_now(),
        "adapter": identity(TOOL_PATH),
        "v2Authorities": _authority_identities(),
        "hceHandoff": identity(inputs["handoff"]),
        "preregistration": identity(inputs["preregistration"]),
        "candidateClaim": identity(inputs["claim"]),
        "practicalDecision": identity(inputs["practicalDecision"]),
        "attemptIndex": handoff["attemptIndex"],
        "attemptBeta": copy.deepcopy(handoff["attemptBeta"]),
        "promotionLogThreshold": handoff["promotionLogThreshold"],
        "candidateEngine": copy.deepcopy(handoff["candidateEngine"]),
        "candidateNetwork": copy.deepcopy(handoff["candidateNetwork"]),
        "engineOptions": copy.deepcopy(handoff["engineOptions"]),
        "formalGates": copy.deepcopy(handoff["formalGates"]),
        "stageSeedCommitments": _seed_commitments(inputs),
        "reservationConsumesHceStage": True,
        "stageSeedsDisclosed": False,
        "rawEntropyPublished": False,
        "hmacKeysPublished": False,
        "matchResultsRead": 0,
    }


def verify_reservation(path: Path | str, *, handoff: Path | str | None = None) -> dict[str, Any]:
    value = _load_json(path, "G6 HCE adapter reservation")
    _exact_fields(value, RESERVATION_FIELDS, "adapter reservation")
    inputs = _authenticate_handoff(
        verify_identity(value.get("hceHandoff"), "reservation HCE handoff")
        if handoff is None
        else _plain_path(handoff)
    )
    expected = _reservation_value(inputs)
    expected["createdUtc"] = value.get("createdUtc")
    _parse_utc(value.get("createdUtc"), "adapter reservation createdUtc")
    _same(value, expected, "adapter reservation")
    paths = attempt_paths(
        inputs["preregistrationDocument"], value["attemptIndex"]
    )
    if _plain_path(path) != paths["reservation"]:
        raise ValueError("adapter reservation escaped its canonical namespace")
    return value


def _selector_intent_value(
    inputs: Mapping[str, Any], paths: Mapping[str, Any], seeds: Mapping[str, int]
) -> dict[str, Any]:
    matches = _matches()
    readiness = matches.readiness
    index = inputs["handoffDocument"]["attemptIndex"]
    return {
        "schemaVersion": 1,
        "kind": SELECTOR_INTENT_KIND,
        "protocol": identity(REPO / V2_AUTHORITIES["v2ProtocolJson"][0]),
        "attemptIndex": index,
        "createdUtc": _utc_now(),
        "selectorCapsule": _selector_capsule_identity(),
        "stageSeeds": dict(seeds),
        "workerCommandCandidateInputs": 0,
        "workerCommandHistoryProjectionInputs": 0,
    }


def _selector_capsule_identity() -> dict[str, Any]:
    matches = _matches()
    return matches.protocol.identity(matches.readiness.BASE_SELECTOR_CAPSULE)


def _preflight_selector(matches: types.ModuleType) -> dict[str, Any]:
    readiness = matches.readiness
    _authority_identities()
    readiness._verify_bindings(require_retirement=False)
    capsule = readiness._verify_selector_capsule(
        readiness.BASE_SELECTOR_CAPSULE, verify_private_freeze=True
    )
    readiness._runtime_identity("dotnetHost")
    readiness._runtime_identity("rootSamplerAssembly")
    readiness._runtime_identity("rootSamplerRulesAssembly")
    readiness._runtime_identity("omegaMatchAssembly")
    readiness._runtime_identity("omegaMatchAppHost")
    readiness._exact_g5_prefix_replay_bundle()
    return capsule


def freeze_selector_base() -> dict[str, Any]:
    """Explicitly create the result-blind frozen-v2 selector base.

    This is a static, zero-alpha setup transition and never consumes a G6
    attempt.  It intentionally delegates the write and every history/runtime
    check to the byte-authenticated frozen-v2 readiness authority.  That
    authority requires its own explicit zero-spend v1 retirement seal first.
    """

    matches = _matches()
    readiness = matches.readiness
    readiness.freeze_base_exclusion_projection()
    _preflight_selector(matches)
    return {
        "baseProjection": identity(readiness.BASE_PROJECTION),
        "selectorCapsule": _selector_capsule_identity(),
        "zeroAlphaSpent": True,
        "g6AttemptConsumed": False,
    }


def _run_selector_child(
    *,
    paths: Mapping[str, Any],
    attempt_index: int,
    parent_lock_token: str,
) -> None:
    command = [
        sys.executable,
        "-I",
        "-B",
        str(TOOL_PATH),
        "selector-worker",
        "--attempt",
        str(attempt_index),
        "--shadow-artifact-root",
        str(Path(paths["shadowArtifactRoot"]).resolve()),
        "--selector-workspace-root",
        str(Path(paths["selectorWorkspaceRoot"]).resolve()),
        "--parent-lock-token",
        parent_lock_token,
    ]
    if any(
        token.casefold() in {"--candidate", "--network", "--claim", "--handoff"}
        for token in command
    ):
        raise AssertionError("candidate-aware input reached the v2 selector worker")
    completed = subprocess.run(
        command,
        cwd=TOOL_PATH.parent,
        env={},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=24 * 60 * 60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "candidate-blind v2 selector failed: " + completed.stderr[-4000:]
        )


def _selector_worker(
    *,
    attempt_index: int,
    shadow_artifact_root: Path,
    selector_workspace_root: Path,
    parent_lock_token: str,
) -> None:
    matches = _matches()
    readiness = matches.readiness
    synthetic_paths = {
        "shadowArtifactRoot": shadow_artifact_root.resolve(),
        "selectorWorkspaceRoot": selector_workspace_root.resolve(),
    }
    with _patched_readiness_paths(readiness, synthetic_paths):
        expected = readiness.attempt_paths(attempt_index)
        output = expected["selectorRoot"]
        # The exact v2 worker installs its access probe before reading either
        # selector input.  Its command receives no candidate or result path.
        readiness._candidate_blind_worker(
            readiness.BASE_SELECTOR_CAPSULE,
            expected["samplingIntent"],
            output,
            parent_lock_token,
        )


def _verify_selector_outputs(
    inputs: Mapping[str, Any], paths: Mapping[str, Any], seeds: Mapping[str, int]
) -> dict[str, Any]:
    matches = _matches()
    readiness = matches.readiness
    index = inputs["handoffDocument"]["attemptIndex"]
    with _patched_readiness_paths(readiness, paths):
        readiness._verify_selector_workspace_inventory(index, sealed=True)
        joint = readiness.contract.strict_load(
            paths["jointSuiteSeal"], "G6 adapter v2 joint suite seal"
        )
        if (
            joint.get("kind") != readiness.JOINT_SUITE_KIND
            or joint.get("attemptIndex") != index
            or joint.get("stageSeeds") != dict(seeds)
            or joint.get("informationBoundary", {}).get(
                "candidateIdentityAvailableToWorker"
            )
            is not False
            or joint.get("informationBoundary", {}).get("candidateOrClaimInputs")
            != 0
            or joint.get("informationBoundary", {}).get("matchResultsAccessed")
            != 0
            or joint.get("osAccessProbe", {}).get("passes") is not True
        ):
            raise ValueError("candidate-blind v2 selector seal changed")
        used: set[str] = set()
        for gate in ALL_V2_GATES:
            suite_path = paths["suites"][gate]
            if gate == "normal-start-clock":
                suite = readiness.contract.strict_load(
                    suite_path, "normal-start-clock suite"
                )
                openings = readiness._PRACTICAL_VERIFY_SUITE(
                    suite, seed=seeds[gate]
                )
                signatures = {
                    signature
                    for opening in suite["openings"]
                    for signature in opening["openConfirmationV2"][
                        "orbitSignatures"
                    ]
                }
                if len(openings) != int(readiness.PROTOCOL["stages"][gate]["roots"]):
                    raise ValueError("normal-start-clock suite root count changed")
            else:
                _, signatures = readiness._verify_suite(
                    suite_path, index, gate, seeds[gate]
                )
            if used.intersection(signatures):
                raise ValueError("v2 adapter suites overlap a whole symmetry orbit")
            used.update(signatures)
        return joint


def _implementation_value(inputs: Mapping[str, Any]) -> dict[str, Any]:
    matches = _matches()
    readiness = matches.readiness
    pinned = {
        "adapter": identity(TOOL_PATH),
        "g6Successor": identity(REPO / G6_RELATIVE),
        **_authority_identities(),
        "prefixReplayAssembly": readiness._runtime_identity(
            "prefixReplayAssembly"
        ),
        "omegaMatchRulesAssembly": readiness._runtime_identity(
            "omegaMatchRulesAssembly"
        ),
        "sharedMatchCore": readiness._runtime_identity("sharedMatchCore"),
        "omegaMatchAssembly": readiness._runtime_identity("omegaMatchAssembly"),
        "omegaMatchAppHost": readiness._runtime_identity("omegaMatchAppHost"),
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": IMPLEMENTATION_KIND,
        "status": "sealed-frozen-v2-formal-gate-adapter",
        "createdUtc": _utc_now(),
        "hceHandoff": identity(inputs["handoff"]),
        "pinned": pinned,
        "prefixReplayBundle": readiness._exact_g5_prefix_replay_bundle(),
        "dotnetRuntimeBundle": readiness._dotnet_runtime_bundle(),
        "omegaMatchBundle": readiness._HARNESS_BUNDLE_IDENTITY(
            readiness._runtime_path("omegaMatchAssembly")
        ),
        "formalGateImplementationsModified": False,
        "matchResultsRead": 0,
        "finalStageSeal": True,
    }


def _verify_implementation(path: Path | str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    value = _load_json(path, "G6 HCE adapter implementation seal")
    expected = _implementation_value(inputs)
    expected["createdUtc"] = value.get("createdUtc")
    _parse_utc(value.get("createdUtc"), "implementation seal createdUtc")
    _same(value, expected, "adapter implementation seal")
    return value


def _write_configs(
    inputs: Mapping[str, Any], paths: Mapping[str, Any], seeds: Mapping[str, int]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    matches = _matches()
    readiness = matches.readiness
    handoff = inputs["handoffDocument"]
    index = handoff["attemptIndex"]
    engine = handoff["candidateEngine"]
    network = handoff["candidateNetwork"]
    harness = readiness._runtime_identity("omegaMatchAssembly")
    bundle = readiness._HARNESS_BUNDLE_IDENTITY(
        readiness._runtime_path("omegaMatchAssembly")
    )
    configs: dict[str, dict[str, Any]] = {}
    with _patched_readiness_paths(readiness, paths):
        for gate in ALL_V2_GATES:
            suite_identity = matches.protocol.identity(paths["suites"][gate])
            value = readiness._expected_config(
                index,
                gate,
                seeds[gate],
                suite_identity,
                engine,
                network,
                harness,
                bundle,
            )
            _exclusive_json(paths["configs"][gate], value)
            configs[gate] = readiness._verify_config(
                paths["configs"][gate],
                index,
                gate,
                seeds[gate],
                suite_identity,
                engine,
                network,
                harness,
                bundle,
            )
    return configs, {"harness": harness, "bundle": bundle}


def _audit_value(
    inputs: Mapping[str, Any],
    paths: Mapping[str, Any],
    seeds: Mapping[str, int],
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    handoff = inputs["handoffDocument"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUDIT_KIND,
        "status": "sealed-before-first-hce-match-result",
        "createdUtc": _utc_now(),
        "hceHandoff": identity(inputs["handoff"]),
        "adapterReservation": identity(paths["reservation"]),
        "implementationSeal": identity(paths["implementationSeal"]),
        "jointSuiteSeal": identity(paths["jointSuiteSeal"]),
        "attemptIndex": handoff["attemptIndex"],
        "promotionLogThreshold": handoff["promotionLogThreshold"],
        "stageSeeds": dict(seeds),
        "stageSeedCommitments": _seed_commitments(inputs),
        "seedDisclosure": {
            "reservationPredatesDisclosure": True,
            "commitmentsPredateDisclosure": True,
            "rawEntropyPublished": False,
            "hmacKeysPublished": False,
            "derivedSignedInt32SeedsDisclosedForFrozenV2Runtime": True,
        },
        "candidateEngine": copy.deepcopy(handoff["candidateEngine"]),
        "candidateNetwork": copy.deepcopy(handoff["candidateNetwork"]),
        "candidateControlIsolation": {
            "sameExecutable": True,
            "sameWorkingDirectory": True,
            "candidateUseOmegaNNUE": True,
            "candidateAssetSha256": handoff["candidateNetwork"]["sha256"],
            "controlUseOmegaNNUE": False,
            "controlOmegaNNUEFile": "<empty>",
            "controlExternalAssets": [],
        },
        "configs": {
            gate: identity(paths["configs"][gate]) for gate in ALL_V2_GATES
        },
        "suites": {
            gate: identity(paths["suites"][gate]) for gate in ALL_V2_GATES
        },
        "dotnetHost": _matches().readiness._runtime_identity("dotnetHost"),
        "omegaMatchAssembly": copy.deepcopy(runtime["harness"]),
        "omegaMatchBundle": copy.deepcopy(runtime["bundle"]),
        "candidateIdentityAvailableToSelectorWorker": False,
        "matchResultsRead": 0,
        "finalStageSeal": True,
    }


def _core_seal_value(
    inputs: Mapping[str, Any],
    paths: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    matches = _matches()
    readiness = matches.readiness
    handoff = inputs["handoffDocument"]
    suites = {
        gate: matches.protocol.identity(paths["suites"][gate])
        for gate in ALL_V2_GATES
    }
    configs = {
        gate: matches.protocol.identity(paths["configs"][gate])
        for gate in ALL_V2_GATES
    }
    pins = [
        identity(TOOL_PATH),
        identity(REPO / G6_RELATIVE),
        *_authority_identities().values(),
        identity(inputs["handoff"]),
        identity(inputs["claim"]),
        identity(paths["reservation"]),
        identity(paths["implementationSeal"]),
        identity(paths["jointSuiteSeal"]),
        handoff["candidateEngine"],
        handoff["candidateNetwork"],
        readiness._runtime_identity("dotnetHost"),
        readiness._runtime_identity("dotnetRuntimeManifest"),
        readiness._runtime_identity("sharedMatchCore"),
        runtime["harness"],
        readiness._runtime_identity("omegaMatchAppHost"),
        *suites.values(),
        *configs.values(),
        identity(paths["audit"]),
    ]
    by_path = {str(item["path"]).casefold(): dict(item) for item in pins}
    return {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v1-match-seal",
        "sealedUtc": _utc_now(),
        "generationId": (
            f"{matches.protocol.PROTOCOL_ID}:g6-adapter-attempt-"
            f"{handoff['attemptIndex']:06d}"
        ),
        "protocolSha256": identity(
            REPO / V2_AUTHORITIES["v2ProtocolJson"][0]
        )["sha256"],
        "candidateNetworkSha256": handoff["candidateNetwork"]["sha256"],
        "engineExecutableSha256": handoff["candidateEngine"]["sha256"],
        "matchCoreSourceSha256": readiness._runtime_identity(
            "sharedMatchCore"
        )["sha256"],
        "omegaMatchAssemblySha256": runtime["harness"]["sha256"],
        "pinnedFiles": sorted(
            by_path.values(), key=lambda item: str(item["path"]).casefold()
        ),
        "omegaMatchBundle": copy.deepcopy(runtime["bundle"]),
        "dotnetRuntimeBundle": readiness._dotnet_runtime_bundle(),
        "gates": {
            gate: {
                "suite": suites[gate],
                "config": configs[gate],
                "runId": _load_json(
                    paths["configs"][gate], f"{gate} adapter config"
                )["runId"],
                "outputDirectory": _load_json(
                    paths["configs"][gate], f"{gate} adapter config"
                )["outputDirectory"],
            }
            for gate in ALL_V2_GATES
        },
        "audit": identity(paths["audit"]),
    }


def _verify_core_seal(
    path: Path | str,
    inputs: Mapping[str, Any],
    paths: Mapping[str, Any],
    seeds: Mapping[str, int],
) -> dict[str, Any]:
    matches = _matches()
    handoff = inputs["handoffDocument"]
    matches._install_core_profile(
        matches.protocol.validate_protocol(), handoff["attemptIndex"], seeds
    )
    value = _load_json(path, "G6 adapter v2 core seal")
    expected = _core_seal_value(
        inputs,
        paths,
        {
            "harness": matches.readiness._runtime_identity("omegaMatchAssembly"),
            "bundle": matches.readiness._HARNESS_BUNDLE_IDENTITY(
                matches.readiness._runtime_path("omegaMatchAssembly")
            ),
        },
    )
    expected["sealedUtc"] = value.get("sealedUtc")
    _parse_utc(value.get("sealedUtc"), "core seal sealedUtc")
    _same(value, expected, "adapter core seal")
    matches.core._verify_seal(Path(path).resolve())
    return value


AUTHORIZATION_FIELDS = {
    "schemaVersion",
    "kind",
    "status",
    "createdUtc",
    "protocol",
    "v2Protocol",
    "adapter",
    "hceHandoff",
    "preregistration",
    "candidateClaim",
    "practicalDecision",
    "attemptIndex",
    "attemptBeta",
    "promotionLogThreshold",
    "implementationSeal",
    "adapterReservation",
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
    "stageSeedCommitments",
    "engineOptions",
    "candidateControlIsolation",
    "formalGates",
    "stageOrder",
    "alphaSpending",
    "frozenV2Authorities",
    "selectorInformationBoundary",
    "seedDisclosure",
    "orchestrator",
    "launchOnlyThrough",
    "thresholdsChangedAfterResults",
    "matchResultsRead",
    "finalStageSeal",
}


def _authorization_value(
    inputs: Mapping[str, Any],
    paths: Mapping[str, Any],
    seeds: Mapping[str, int],
    *,
    created_utc: str,
) -> dict[str, Any]:
    matches = _matches()
    readiness = matches.readiness
    handoff = inputs["handoffDocument"]
    index = handoff["attemptIndex"]
    threshold = matches.protocol.promotion_log_threshold(index)
    if threshold != handoff["promotionLogThreshold"]:
        raise ValueError("G6 handoff and frozen v2 alpha share differ")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUTHORIZATION_KIND,
        "status": "authorized-for-three-frozen-v2-formal-gates",
        "createdUtc": created_utc,
        "protocol": identity(_g6().PROTOCOL_PATH),
        "v2Protocol": identity(REPO / V2_AUTHORITIES["v2ProtocolJson"][0]),
        "adapter": identity(TOOL_PATH),
        "hceHandoff": identity(inputs["handoff"]),
        "preregistration": identity(inputs["preregistration"]),
        "candidateClaim": identity(inputs["claim"]),
        "practicalDecision": identity(inputs["practicalDecision"]),
        "attemptIndex": index,
        "attemptBeta": copy.deepcopy(handoff["attemptBeta"]),
        "promotionLogThreshold": threshold,
        "implementationSeal": identity(paths["implementationSeal"]),
        "adapterReservation": identity(paths["reservation"]),
        "jointSuiteSeal": identity(paths["jointSuiteSeal"]),
        "coreSeal": identity(paths["coreSeal"]),
        "selectedNetwork": copy.deepcopy(handoff["candidateNetwork"]),
        "engine": copy.deepcopy(handoff["candidateEngine"]),
        "dotnetHost": readiness._runtime_identity("dotnetHost"),
        "dotnetRuntimeManifest": readiness._runtime_identity(
            "dotnetRuntimeManifest"
        ),
        "dotnetRuntimeBundle": readiness._dotnet_runtime_bundle(),
        "omegaMatchAssembly": readiness._runtime_identity("omegaMatchAssembly"),
        "omegaMatchAppHost": readiness._runtime_identity("omegaMatchAppHost"),
        "omegaMatchBundle": readiness._HARNESS_BUNDLE_IDENTITY(
            readiness._runtime_path("omegaMatchAssembly")
        ),
        "matchCoreSource": readiness._runtime_identity("sharedMatchCore"),
        "configs": {
            gate: identity(paths["configs"][gate]) for gate in ALL_V2_GATES
        },
        "suites": {
            gate: identity(paths["suites"][gate]) for gate in ALL_V2_GATES
        },
        "stageSeeds": dict(seeds),
        "stageSeedCommitments": _seed_commitments(inputs),
        "engineOptions": copy.deepcopy(handoff["engineOptions"]),
        "candidateControlIsolation": {
            "sameExecutable": True,
            "sameWorkingDirectory": True,
            "candidateUseOmegaNNUE": True,
            "candidateAssetSha256": handoff["candidateNetwork"]["sha256"],
            "controlUseOmegaNNUE": False,
            "controlOmegaNNUEFile": "<empty>",
            "controlExternalAssets": [],
        },
        "formalGates": copy.deepcopy(handoff["formalGates"]),
        "stageOrder": list(FORMAL_GATES),
        "alphaSpending": {
            "attemptIndex": index,
            "attemptBeta": copy.deepcopy(handoff["attemptBeta"]),
            "promotionLogThreshold": threshold,
            "promotionEValue": math.nextafter(math.exp(threshold), math.inf),
            "nullEloSeparatelyForEveryGate": 15.0,
            "globalAttemptOrAlphaReset": False,
        },
        "frozenV2Authorities": _authority_identities(),
        "selectorInformationBoundary": {
            "candidateIdentityAvailableToWorker": False,
            "candidateOrClaimInputs": 0,
            "matchResultsAccessed": 0,
            "selectorCapsule": _selector_capsule_identity(),
            "jointSuiteSeal": identity(paths["jointSuiteSeal"]),
        },
        "seedDisclosure": {
            "reservationPredatesDisclosure": True,
            "commitmentsPredateDisclosure": True,
            "rawEntropyPublished": False,
            "hmacKeysPublished": False,
            "derivedSignedInt32SeedsDisclosedForFrozenV2Runtime": True,
        },
        "orchestrator": identity(TOOL_PATH),
        "launchOnlyThrough": G6_RELATIVE.replace(
            "king_state_confirmation_generation6.py",
            "king_state_confirmation_generation6_hce_adapter.py",
        ),
        "thresholdsChangedAfterResults": False,
        "matchResultsRead": 0,
        "finalStageSeal": True,
    }


def _require_active_global_attempt(
    inputs: Mapping[str, Any], attempt_index: int
) -> None:
    """Keep launch authority active-only, except inside authenticated replay."""

    if _HISTORICAL_REPLAY_DEPTH.get() > 0:
        return
    state = _g6()._global_attempt_state(inputs["preregistrationDocument"])
    if state.get("activeAttempt") != attempt_index:
        raise ValueError("adapter authorization no longer names the active global attempt")


def verify_authorization(path: Path | str) -> dict[str, Any]:
    authorization_path = _plain_path(path)
    value = _load_json(authorization_path, "G6 frozen-v2 HCE authorization")
    _exact_fields(value, AUTHORIZATION_FIELDS, "adapter authorization")
    handoff_path = verify_identity(
        value.get("hceHandoff"), "authorization HCE handoff"
    )
    inputs = _authenticate_handoff(handoff_path)
    handoff = inputs["handoffDocument"]
    paths = attempt_paths(
        inputs["preregistrationDocument"], handoff["attemptIndex"]
    )
    if authorization_path != paths["authorization"]:
        raise ValueError("adapter authorization escaped its canonical namespace")
    if paths["attemptClosure"].exists():
        # Terminal verification is allowed, but no mutation path may mistake a
        # closed authorization for an active launch authority.
        _load_json(paths["attemptClosure"], "terminal adapter closure")
    seeds = _stage_seeds(inputs)
    verify_reservation(paths["reservation"], handoff=handoff_path)
    _verify_implementation(paths["implementationSeal"], inputs)
    _verify_selector_outputs(inputs, paths, seeds)
    matches = _matches()
    readiness = matches.readiness
    runtime = {
        "harness": readiness._runtime_identity("omegaMatchAssembly"),
        "bundle": readiness._HARNESS_BUNDLE_IDENTITY(
            readiness._runtime_path("omegaMatchAssembly")
        ),
    }
    with _patched_readiness_paths(readiness, paths):
        for gate in ALL_V2_GATES:
            readiness._verify_config(
                paths["configs"][gate],
                handoff["attemptIndex"],
                gate,
                seeds[gate],
                matches.protocol.identity(paths["suites"][gate]),
                handoff["candidateEngine"],
                handoff["candidateNetwork"],
                runtime["harness"],
                runtime["bundle"],
            )
    audit = _load_json(paths["audit"], "adapter match audit")
    expected_audit = _audit_value(inputs, paths, seeds, runtime)
    expected_audit["createdUtc"] = audit.get("createdUtc")
    _parse_utc(audit.get("createdUtc"), "adapter audit createdUtc")
    _same(audit, expected_audit, "adapter match audit")
    _verify_core_seal(paths["coreSeal"], inputs, paths, seeds)
    expected = _authorization_value(
        inputs, paths, seeds, created_utc=str(value.get("createdUtc"))
    )
    _parse_utc(value.get("createdUtc"), "adapter authorization createdUtc")
    _same(value, expected, "adapter authorization")
    if _parse_utc(value["createdUtc"], "authorization createdUtc") < max(
        _parse_utc(
            _load_json(paths[name], f"authorization predecessor {name}")[
                "createdUtc" if name != "coreSeal" else "sealedUtc"
            ],
            f"authorization predecessor {name} UTC",
        )
        for name in ("reservation", "implementationSeal", "audit", "coreSeal")
    ):
        raise ValueError("adapter authorization predates a sealed predecessor")
    _require_active_global_attempt(inputs, handoff["attemptIndex"])
    expected_files = {
        "implementation-seal.json",
        "match-audit.json",
        "core-seal.json",
        "match-authorization.json",
        *(f"{gate}-match.json" for gate in ALL_V2_GATES),
    }
    if (
        not paths["sealed"].is_dir()
        or paths["sealed"].is_symlink()
        or {item.name for item in paths["sealed"].iterdir()} != expected_files
        or any(item.is_symlink() or not item.is_file() for item in paths["sealed"].iterdir())
    ):
        raise ValueError("adapter sealed-directory inventory changed")
    return value


def _serialized(function: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with _matches().readiness._operation_lock():
            return function(*args, **kwargs)

    wrapped.__name__ = function.__name__
    wrapped.__doc__ = function.__doc__
    return wrapped


@_serialized
def prepare(
    handoff: Path | str,
    *,
    _selector: Callable[..., None] = _run_selector_child,
) -> dict[str, Any]:
    """Seal the one candidate-blind v2 runtime authority for a G6 handoff."""

    inputs = _authenticate_handoff(handoff)
    matches = _matches()
    readiness = matches.readiness
    # Static/runtime preflight is result-blind and occurs before consuming the
    # adapter stage, so a missing frozen runtime does not waste the global k.
    _preflight_selector(matches)
    handoff_document = inputs["handoffDocument"]
    index = handoff_document["attemptIndex"]
    paths = attempt_paths(inputs["preregistrationDocument"], index)
    if paths["root"].exists() or paths["selectorRoot"].exists():
        raise FileExistsError("G6 HCE adapter namespace already exists")
    seeds = _stage_seeds(inputs)
    token = readiness._require_operation_lock("G6 HCE adapter preparation")
    try:
        _exclusive_json(paths["reservation"], _reservation_value(inputs))
        paths["selectorRoot"].mkdir(parents=True, exist_ok=False)
        intent = _selector_intent_value(inputs, paths, seeds)
        _exclusive_json(paths["samplingIntent"], intent)
        _selector(
            paths=paths,
            attempt_index=index,
            parent_lock_token=token,
        )
        _verify_selector_outputs(inputs, paths, seeds)
        _exclusive_json(paths["implementationSeal"], _implementation_value(inputs))
        _, runtime = _write_configs(inputs, paths, seeds)
        _exclusive_json(paths["audit"], _audit_value(inputs, paths, seeds, runtime))
        core_value = _core_seal_value(inputs, paths, runtime)
        _exclusive_json(paths["coreSeal"], core_value)
        _verify_core_seal(paths["coreSeal"], inputs, paths, seeds)
        authorization = _authorization_value(
            inputs, paths, seeds, created_utc=_utc_now()
        )
        result = _exclusive_json(paths["authorization"], authorization)
        verify_authorization(paths["authorization"])
        return result
    except BaseException:
        if paths["reservation"].is_file() and not paths["attemptClosure"].exists():
            try:
                _publish_abort_closure_from_inputs(
                    inputs, paths, reason="adapter-preparation-failure"
                )
            except BaseException:
                pass
        raise


def _frozen_context(
    attempt_index: int,
    authorization_path: Path | None = None,
    *,
    verify_chain: bool = True,
) -> Any:
    del verify_chain
    if authorization_path is None:
        raise ValueError("G6 adapter requires an explicit authorization path")
    authorization_path = _plain_path(authorization_path)
    authorization = verify_authorization(authorization_path)
    if authorization["attemptIndex"] != attempt_index:
        raise ValueError("launch attempt differs from G6 adapter authorization")
    inputs = _authenticate_handoff(Path(authorization["hceHandoff"]["path"]))
    paths = attempt_paths(inputs["preregistrationDocument"], attempt_index)
    paths = dict(paths)
    paths["claim"] = inputs["claim"]
    matches = _matches()
    seeds = _stage_seeds(inputs)
    threshold, linear = matches._install_core_profile(
        matches.protocol.validate_protocol(), attempt_index, seeds
    )
    implementation = _load_json(
        paths["implementationSeal"], "adapter implementation context"
    )
    suite_seal = matches.protocol.strict_load(
        paths["jointSuiteSeal"], "adapter joint-suite context"
    )
    core_seal = _load_json(paths["coreSeal"], "adapter core-seal context")
    claim = _load_json(inputs["claim"], "adapter G6 claim context")
    return matches.Context(
        protocol_value=matches.protocol.validate_protocol(),
        attempt_index=attempt_index,
        paths=paths,
        implementation_seal=implementation,
        implementation_seal_identity=matches.protocol.identity(
            paths["implementationSeal"]
        ),
        claim=claim,
        claim_identity=matches.protocol.identity(inputs["claim"]),
        stage_seeds=seeds,
        suite_seal=suite_seal,
        suite_seal_identity=matches.protocol.identity(paths["jointSuiteSeal"]),
        authorization=authorization,
        authorization_identity=matches.protocol.identity(authorization_path),
        core_seal=core_seal,
        core_seal_identity=matches.protocol.identity(paths["coreSeal"]),
        promotion_log_threshold=threshold,
        promotion_e_value=linear,
    )


def _frozen_rehash_context(context: Any, gate: str) -> dict[str, Any]:
    matches = _MATCHES_MODULE
    if matches is None:
        raise RuntimeError("frozen v2 module is not loaded")
    if gate not in FORMAL_GATES:
        raise ValueError("G6 adapter cannot launch the v2 development gate")
    matches._assert_exact_bindings()
    authorization_path = verify_identity(
        context.authorization_identity, "adapter authorization rehash"
    )
    current = verify_authorization(authorization_path)
    matches.protocol.require_exact_json(
        current, context.authorization, "adapter authorization rehash"
    )
    inputs = _authenticate_handoff(Path(current["hceHandoff"]["path"]))
    paths = attempt_paths(
        inputs["preregistrationDocument"], context.attempt_index
    )
    if paths["attemptClosure"].exists() and _TERMINAL_REPLAY_DEPTH.get() < 1:
        raise RuntimeError("G6 HCE adapter attempt is terminal")
    _verify_core_seal(
        paths["coreSeal"], inputs, paths, _stage_seeds(inputs)
    )
    return matches._protected(context, gate)


def _frozen_predecessor_decision(context: Any, gate: str) -> dict[str, Any] | None:
    matches = _MATCHES_MODULE
    if matches is None:
        raise RuntimeError("frozen v2 module is not loaded")
    if gate != "equal-node":
        return _MATCHES_ORIGINALS["_predecessor_decision"](context, gate)
    authorization = verify_authorization(
        verify_identity(context.authorization_identity, "equal-node authorization")
    )
    decision_path = verify_identity(
        authorization["practicalDecision"], "equal-node practical predecessor"
    )
    decision = _load_json(decision_path, "equal-node practical predecessor")
    if decision.get("decision") != "promote" or decision.get(
        "zeroSafetyFailures"
    ) is not True:
        raise ValueError("equal-node requires the safe promoted G6 practical gate")
    return matches.protocol.identity(decision_path, relative=False)


def _frozen_active_launch_state(
    context: Any,
    gate: str,
    sequence: int,
    intent: Mapping[str, Any],
) -> None:
    matches = _MATCHES_MODULE
    if matches is None:
        raise RuntimeError("frozen v2 module is not loaded")
    protected = _frozen_rehash_context(context, gate)
    paths = context.paths
    if (
        Path(paths["attemptClosure"]).exists()
        or matches._decision_path(context, gate).exists()
        or matches._completion_path(context, gate, sequence).exists()
    ):
        raise RuntimeError(f"{gate} launch became terminal before completion")
    matches._same_identity(
        intent.get("authorization"),
        context.authorization_identity,
        f"{gate} live G6 adapter authorization",
    )
    matches.protocol.require_exact_json(
        intent.get("protected"), protected, f"{gate} live protected identities"
    )
    inputs = _authenticate_handoff(
        Path(context.authorization["hceHandoff"]["path"])
    )
    state = _g6()._global_attempt_state(inputs["preregistrationDocument"])
    if state.get("activeAttempt") != context.attempt_index:
        raise RuntimeError("G6 global attempt stopped being uniquely active")


def _require_open_authorization(path: Path | str) -> dict[str, Any]:
    value = verify_authorization(path)
    inputs = _authenticate_handoff(Path(value["hceHandoff"]["path"]))
    paths = attempt_paths(inputs["preregistrationDocument"], value["attemptIndex"])
    if paths["attemptClosure"].exists():
        raise FileExistsError("G6 HCE adapter attempt is already terminal")
    return value


@_serialized
def launch(
    authorization: Path | str,
    *,
    gate: str,
    action: str | None = None,
) -> None:
    if gate not in FORMAL_GATES:
        raise ValueError("G6 adapter launches only the three formal HCE gates")
    auth_path = _plain_path(authorization)
    value = _require_open_authorization(auth_path)
    if action not in {None, "run", "resume"}:
        raise ValueError("launch action must be run, resume, or omitted")
    _matches()._launch(
        argparse.Namespace(
            attempt=value["attemptIndex"],
            gate=gate,
            action=action,
            authorization=auth_path,
        )
    )


@_serialized
def assess(authorization: Path | str, *, gate: str) -> None:
    if gate not in FORMAL_GATES:
        raise ValueError("G6 adapter assesses only the three formal HCE gates")
    auth_path = _plain_path(authorization)
    value = _require_open_authorization(auth_path)
    _matches()._assess_command(
        argparse.Namespace(
            attempt=value["attemptIndex"], gate=gate, authorization=auth_path
        )
    )


@_serialized
def attest_idle(
    authorization: Path | str, *, gate: str, operator: str
) -> None:
    if gate not in TIMED_GATES:
        raise ValueError("only equal-time and normal-start-clock require idle attestation")
    auth_path = _plain_path(authorization)
    value = _require_open_authorization(auth_path)
    _matches()._attest_idle(
        argparse.Namespace(
            attempt=value["attemptIndex"],
            gate=gate,
            operator=operator,
            authorization=auth_path,
        )
    )


CLOSURE_FIELDS = {
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


def _existing_identity(path: Any) -> dict[str, Any] | None:
    if not isinstance(path, Path) or not path.is_file():
        return None
    return identity(path)


def _same_identity_or_none(value: Any, expected: Any, label: str) -> None:
    if expected is None:
        _same(value, None, label)
        return
    _same(_identity_shape(value, label), expected, label)


def _closure_base(
    inputs: Mapping[str, Any], paths: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": V2_CLOSURE_KIND,
        "protocol": identity(REPO / V2_AUTHORITIES["v2ProtocolJson"][0]),
        "attemptIndex": inputs["handoffDocument"]["attemptIndex"],
        "createdUtc": _utc_now_g6(),
        "implementationSeal": _existing_identity(paths["implementationSeal"]),
        "attemptReservation": _existing_identity(paths["reservation"]),
        "candidateClaim": identity(inputs["claim"]),
        "jointSuiteSeal": _existing_identity(paths["jointSuiteSeal"]),
        "authorization": _existing_identity(paths["authorization"]),
        "decisions": {
            "development": identity(inputs["practicalDecision"]),
            **{
                gate: _existing_identity(paths["stages"][gate] / "decision.json")
                for gate in FORMAL_GATES
            },
        },
        "outcome": "aborted",
        "reason": "adapter-abort",
        "terminal": True,
        "nextAttemptAllowed": True,
        "candidateNetworkSha256": inputs["handoffDocument"]["candidateNetwork"][
            "sha256"
        ],
    }


def _publish_abort_closure_from_inputs(
    inputs: Mapping[str, Any], paths: Mapping[str, Any], *, reason: str
) -> dict[str, Any]:
    if type(reason) is not str or SAFE_ABORT.fullmatch(reason) is None:
        raise ValueError("adapter abort reason is not canonical")
    value = _closure_base(inputs, paths)
    value.update(
        {
            "outcome": "aborted",
            "reason": reason,
            "nextAttemptAllowed": True,
        }
    )
    result = _exclusive_json(paths["attemptClosure"], value)
    verify_attempt_closure(paths["attemptClosure"], handoff=inputs["handoff"])
    return result


def _terminal_decisions(
    authorization: Path,
) -> tuple[dict[str, dict[str, Any] | None], str | None]:
    auth = verify_authorization(authorization)
    context = _frozen_context(auth["attemptIndex"], authorization)
    matches = _matches()
    decisions: dict[str, dict[str, Any] | None] = {}
    failure: str | None = None
    missing = False
    for gate in FORMAL_GATES:
        path = matches._decision_path(context, gate)
        if not path.is_file():
            decisions[gate] = None
            missing = True
            continue
        if missing:
            raise ValueError("formal HCE decision order has a gap")
        decision = matches._verify_decision(context, gate)
        decisions[gate] = decision
        if (
            decision.get("decision") != SUCCESS_DECISION[gate]
            or decision.get("zeroSafetyFailures") is not True
        ):
            failure = f"{gate}-{decision.get('decision')}"
            missing = True
    return decisions, failure


@_serialized
def close_attempt(
    authorization: Path | str,
    *,
    abort_reason: str | None = None,
) -> dict[str, Any]:
    auth_path = _plain_path(authorization)
    auth = verify_authorization(auth_path)
    inputs = _authenticate_handoff(Path(auth["hceHandoff"]["path"]))
    paths = attempt_paths(inputs["preregistrationDocument"], auth["attemptIndex"])
    if paths["attemptClosure"].exists():
        verify_attempt_closure(paths["attemptClosure"], handoff=inputs["handoff"])
        raise FileExistsError("adapter attempt closure already exists")
    _matches()._require_idle_processes("G6 HCE adapter closure")
    if abort_reason is not None:
        return _publish_abort_closure_from_inputs(
            inputs, paths, reason=abort_reason
        )
    decisions, failure = _terminal_decisions(auth_path)
    if failure is None and any(value is None for value in decisions.values()):
        raise ValueError("formal HCE evidence is nonterminal")
    value = _closure_base(inputs, paths)
    if failure is None:
        value.update(
            {
                "outcome": "confirmed",
                "reason": "all-three-formal-hce-gates-promoted",
                "nextAttemptAllowed": False,
            }
        )
    else:
        value.update(
            {
                "outcome": "failed",
                "reason": failure,
                "nextAttemptAllowed": True,
            }
        )
    result = _exclusive_json(paths["attemptClosure"], value)
    verify_attempt_closure(paths["attemptClosure"], handoff=inputs["handoff"])
    return result


@contextlib.contextmanager
def _terminal_replay(*, historical: bool = False) -> Iterator[None]:
    terminal_token = _TERMINAL_REPLAY_DEPTH.set(_TERMINAL_REPLAY_DEPTH.get() + 1)
    historical_token = None
    if historical:
        historical_token = _HISTORICAL_REPLAY_DEPTH.set(
            _HISTORICAL_REPLAY_DEPTH.get() + 1
        )
    try:
        yield
    finally:
        if historical_token is not None:
            _HISTORICAL_REPLAY_DEPTH.reset(historical_token)
        _TERMINAL_REPLAY_DEPTH.reset(terminal_token)


def verify_attempt_closure(
    path: Path | str, *, handoff: Path | str
) -> dict[str, Any]:
    closure_path = _plain_path(path)
    inputs = _authenticate_handoff(handoff)
    index = inputs["handoffDocument"]["attemptIndex"]
    paths = attempt_paths(inputs["preregistrationDocument"], index)
    if closure_path != paths["attemptClosure"]:
        raise ValueError("adapter closure escaped its canonical namespace")
    value = _load_json(closure_path, "G6 HCE adapter attempt closure")
    _exact_fields(value, CLOSURE_FIELDS, "adapter attempt closure")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != V2_CLOSURE_KIND
        or value.get("attemptIndex") != index
        or type(value.get("attemptIndex")) is not int
        or value.get("terminal") is not True
        or value.get("outcome") not in {"confirmed", "failed", "aborted"}
        or type(value.get("nextAttemptAllowed")) is not bool
        or (value["outcome"] == "confirmed") is value["nextAttemptAllowed"]
        or value.get("candidateNetworkSha256")
        != inputs["handoffDocument"]["candidateNetwork"]["sha256"]
        or value.get("protocol")
        != identity(REPO / V2_AUTHORITIES["v2ProtocolJson"][0])
    ):
        raise ValueError("adapter attempt closure envelope changed")
    _same(
        _identity_shape(value.get("protocol"), "adapter closure protocol"),
        identity(REPO / V2_AUTHORITIES["v2ProtocolJson"][0]),
        "adapter closure protocol",
    )
    _parse_utc(value.get("createdUtc"), "adapter v2 closure createdUtc")
    _same_identity_or_none(
        value.get("candidateClaim"),
        identity(inputs["claim"]),
        "adapter closure candidate claim",
    )
    _same_identity_or_none(
        value.get("attemptReservation"),
        _existing_identity(paths["reservation"]),
        "adapter closure reservation",
    )
    _same_identity_or_none(
        value.get("implementationSeal"),
        _existing_identity(paths["implementationSeal"]),
        "adapter closure implementation seal",
    )
    _same_identity_or_none(
        value.get("jointSuiteSeal"),
        _existing_identity(paths["jointSuiteSeal"]),
        "adapter closure suite seal",
    )
    decisions = value.get("decisions")
    if type(decisions) is not dict or set(decisions) != {
        "development",
        *FORMAL_GATES,
    }:
        raise ValueError("adapter closure decision inventory changed")
    _same_identity_or_none(
        decisions.get("development"),
        identity(inputs["practicalDecision"]),
        "adapter closure practical bridge",
    )
    if value["outcome"] == "aborted":
        if (
            type(value.get("reason")) is not str
            or SAFE_ABORT.fullmatch(value["reason"]) is None
            or value["nextAttemptAllowed"] is not True
        ):
            raise ValueError("adapter abort closure changed")
        for gate in FORMAL_GATES:
            current = _existing_identity(paths["stages"][gate] / "decision.json")
            _same_identity_or_none(
                decisions.get(gate), current, f"aborted {gate} decision"
            )
        if value.get("authorization") is not None:
            auth_path = verify_identity(
                value["authorization"], "aborted adapter authorization"
            )
            if auth_path != paths["authorization"]:
                raise ValueError("aborted authorization path changed")
        return value
    authorization_path = verify_identity(
        value.get("authorization"), "adapter closure authorization"
    )
    if authorization_path != paths["authorization"]:
        raise ValueError("adapter closure authorization path changed")
    with _terminal_replay():
        observed, failure = _terminal_decisions(authorization_path)
    for gate in FORMAL_GATES:
        expected_identity = _existing_identity(
            paths["stages"][gate] / "decision.json"
        )
        _same_identity_or_none(
            decisions.get(gate), expected_identity, f"closure {gate} decision"
        )
    expected_outcome = "confirmed" if failure is None else "failed"
    expected_reason = (
        "all-three-formal-hce-gates-promoted" if failure is None else failure
    )
    if (
        value["outcome"] != expected_outcome
        or value["reason"] != expected_reason
        or value["nextAttemptAllowed"] is (failure is None)
        or (failure is None and any(item is None for item in observed.values()))
    ):
        raise ValueError("adapter closure differs from frozen decision replay")
    return value


def _document_identity(
    path: Path, value: Mapping[str, Any], label: str
) -> dict[str, Any]:
    """Tie one parsed canonical document to its descriptor-safe file identity."""

    payload = _canonical_json(value)
    expected = {
        "path": str(_plain_path(path)),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    actual = identity(path)
    _same(actual, expected, label)
    return actual


def _verify_historical_successor_bridge(
    inputs: Mapping[str, Any],
    closure_path: Path,
    closure: Mapping[str, Any],
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Authenticate the immutable global closure that consumed this evidence."""

    index = inputs["handoffDocument"]["attemptIndex"]
    artifact_root = _verify_plain_directory(
        _plain_path(str(inputs["preregistrationDocument"]["artifactRoot"]))
    )
    successor_path = (
        artifact_root
        / "attempts"
        / f"attempt-{index:06d}"
        / "06-attempt-closure.json"
    )
    g6 = _g6()
    bound_path = _plain_path(g6._attempt_paths(artifact_root, index)["closure"])
    if bound_path != successor_path:
        raise ImportError("authenticated successor closure namespace changed")
    protocol_path = _plain_path(REPO / G6_PROTOCOL_RELATIVE)
    if _plain_path(g6.PROTOCOL_PATH) != protocol_path:
        raise ImportError("authenticated successor protocol path changed")

    value = _load_json(successor_path, "historical successor attempt closure")
    _exact_fields(value, SUCCESSOR_CLOSURE_FIELDS, "historical successor closure")
    created = _parse_utc(value.get("createdUtc"), "historical successor createdUtc")
    adapter_created = _parse_utc(
        closure.get("createdUtc"), "historical adapter closure createdUtc"
    )
    expected = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SUCCESSOR_CLOSURE_KIND,
        "status": "terminal-global-attempt-closure",
        "createdUtc": value.get("createdUtc"),
        "protocol": identity(protocol_path),
        "preregistration": identity(inputs["preregistration"]),
        "attemptIndex": index,
        "terminalStage": "hce",
        "reservation": copy.deepcopy(inputs["claimDocument"]["reservation"]),
        "claim": identity(inputs["claim"]),
        "suite": identity(inputs["suite"]),
        "practicalDecision": identity(inputs["practicalDecision"]),
        "practicalExecutionTranscript": copy.deepcopy(
            inputs["practicalDecisionDocument"]["executionTranscript"]
        ),
        "practicalIncidentHandoff": None,
        "hceHandoff": identity(inputs["handoff"]),
        "hceAttemptClosure": identity(closure_path),
        "outcome": closure.get("outcome"),
        "reason": f"hce-{closure.get('reason')}",
        "terminal": True,
        "nextAttemptAllowed": closure.get("nextAttemptAllowed"),
        "candidateNetworkSha256": inputs["handoffDocument"]["candidateNetwork"][
            "sha256"
        ],
    }
    if _canonical_json(value) != _canonical_json(expected):
        raise ValueError("historical successor bridge changed")
    if created < adapter_created:
        raise ValueError("historical successor closure predates adapter evidence")
    return (
        successor_path,
        value,
        _document_identity(
            successor_path, value, "historical successor document identity"
        ),
    )


def verify_historical_attempt_closure(
    path: Path | str, *, handoff: Path | str
) -> dict[str, Any]:
    """Replay a non-abort HCE closure after its exact global bridge is closed.

    This verifier is read-only.  Its context-local active-state exception is
    entered only after authenticating the canonical successor closure and is
    removed before returning, so ordinary authorization and launch checks stay
    active-only.
    """

    closure_path = _plain_path(path)
    inputs = _authenticate_handoff(handoff)
    index = inputs["handoffDocument"]["attemptIndex"]
    paths = attempt_paths(inputs["preregistrationDocument"], index)
    if closure_path != paths["attemptClosure"]:
        raise ValueError("historical adapter closure escaped its canonical namespace")
    preview = _load_json(closure_path, "historical G6 HCE adapter closure")
    _exact_fields(preview, CLOSURE_FIELDS, "historical adapter attempt closure")
    if preview.get("outcome") not in {"confirmed", "failed"}:
        raise ValueError("historical replay requires terminal non-abort HCE evidence")
    closure_identity = _document_identity(
        closure_path, preview, "historical adapter document identity"
    )
    successor_path, successor, successor_identity = _verify_historical_successor_bridge(
        inputs, closure_path, preview
    )
    with _terminal_replay(historical=True):
        verified = verify_attempt_closure(closure_path, handoff=inputs["handoff"])
    _same(
        _document_identity(
            closure_path, verified, "historical replay adapter document identity"
        ),
        closure_identity,
        "historical replay closure identity",
    )
    final_path, final_successor, final_successor_identity = (
        _verify_historical_successor_bridge(inputs, closure_path, verified)
    )
    if final_path != successor_path:
        raise ValueError("historical successor closure path changed during replay")
    _same(final_successor, successor, "historical successor closure replay")
    _same(
        final_successor_identity,
        successor_identity,
        "historical successor closure identity",
    )
    return verified


def bridge(path: Path | str, *, handoff: Path | str) -> dict[str, Any]:
    """Publish the successor global closure from the exact adapter closure."""

    closure_path = _plain_path(path)
    verify_attempt_closure(closure_path, handoff=handoff)
    inputs = _authenticate_handoff(handoff)
    g6 = _g6()
    return g6.bridge_hce_attempt_closure(
        preregistration=inputs["preregistration"],
        claim=inputs["claim"],
        suite=inputs["suite"],
        practical_decision=inputs["practicalDecision"],
        hce_handoff=inputs["handoff"],
        hce_attempt_closure=closure_path,
    )


def readiness(handoff: Path | str) -> dict[str, Any]:
    inputs = _authenticate_handoff(handoff)
    index = inputs["handoffDocument"]["attemptIndex"]
    paths = attempt_paths(inputs["preregistrationDocument"], index)
    if not paths["root"].exists() and not paths["selectorRoot"].exists():
        matches = _matches()
        try:
            _preflight_selector(matches)
        except (OSError, ValueError) as error:
            return {
                "adapterId": ADAPTER_ID,
                "attemptIndex": index,
                "state": "static-prerequisite-missing",
                "detail": str(error),
                "nextAction": (
                    "if absent, publish the frozen-v2 zero-spend v1 retirement; "
                    "then run freeze-selector-base before prepare; the global "
                    "attempt is not yet additionally consumed"
                ),
            }
        return {
            "adapterId": ADAPTER_ID,
            "attemptIndex": index,
            "state": "ready-to-prepare",
            "nextAction": "seal candidate-blind suites and the G6 frozen-v2 authorization",
        }
    if paths["attemptClosure"].is_file():
        closure = verify_attempt_closure(
            paths["attemptClosure"], handoff=inputs["handoff"]
        )
        successor = _g6()._attempt_paths(
            Path(inputs["preregistrationDocument"]["artifactRoot"]), index
        )["closure"]
        return {
            "adapterId": ADAPTER_ID,
            "attemptIndex": index,
            "state": f"terminal-{closure['outcome']}",
            "reason": closure["reason"],
            "nextAction": None if successor.exists() else "bridge the terminal closure",
        }
    if not paths["authorization"].is_file():
        return {
            "adapterId": ADAPTER_ID,
            "attemptIndex": index,
            "state": "consumed-incomplete-preparation",
            "nextAction": "close as adapter-preparation-failure; do not rerun the selector",
        }
    authorization = verify_authorization(paths["authorization"])
    context = _frozen_context(index, paths["authorization"])
    matches = _matches()
    stages: dict[str, str] = {}
    for gate in FORMAL_GATES:
        decision = matches._decision_path(context, gate)
        if decision.is_file():
            stages[gate] = str(_load_json(decision, f"{gate} status decision").get("decision"))
        elif matches._launch_dir(context, gate).exists():
            stages[gate] = "in-progress"
        else:
            stages[gate] = "not-started"
    next_action = "close the terminal formal-gate result"
    for gate in FORMAL_GATES:
        if stages[gate] == "not-started":
            next_action = (
                f"attest idle then launch {gate}"
                if gate in TIMED_GATES
                else f"launch {gate}"
            )
            break
        if stages[gate] == "in-progress":
            next_action = f"assess or resume {gate}"
            break
        if stages[gate] != "promote":
            break
    return {
        "adapterId": ADAPTER_ID,
        "attemptIndex": authorization["attemptIndex"],
        "state": "active",
        "stages": stages,
        "nextAction": next_action,
    }


def self_test() -> None:
    # Self-test has no preregistration argument, so start from the current G6
    # source identity and require its pinned-authority table to authenticate
    # this exact adapter before exercising any bridge logic.
    g6 = _bind_g6_identity(identity(REPO / G6_RELATIVE))
    authorities = _authority_identities()
    matches = _matches()
    if set(authorities) != set(V2_AUTHORITIES):
        raise AssertionError("v2 authority inventory changed")
    if matches._launch.__module__ != MATCHES_AUTHORITY_MODULE:
        raise AssertionError("frozen v2 launch implementation was replaced")
    if matches._assess_command.__module__ != matches._launch.__module__:
        raise AssertionError("frozen v2 assessment implementation was replaced")
    if matches._attest_idle.__module__ != matches._launch.__module__:
        raise AssertionError("frozen v2 idle implementation was replaced")
    if matches._context is not _frozen_context:
        raise AssertionError("adapter context bridge was not installed")
    if matches.readiness.PROTOCOL["engineConfiguration"][
        "commonEngineOptions"
    ] != g6.FORMAL_HCE_COMMON_OPTIONS:
        raise AssertionError("G6 handoff options differ from frozen v2")
    entropy = bytes(range(g6.ENTROPY_BYTES))
    commitments = g6._stage_commitments(entropy, 7)
    first = _derive_seeds_from_entropy(
        entropy, 7, commitments, g6_module=g6
    )
    second = _derive_seeds_from_entropy(
        entropy, 7, commitments, g6_module=g6
    )
    if (
        first != second
        or set(first) != set(ALL_V2_GATES)
        or len(set(first.values())) != len(ALL_V2_GATES)
        or any(type(seed) is not int or not 0 <= seed <= UINT31_MAX for seed in first.values())
    ):
        raise AssertionError("committed adapter seed derivation changed")
    forged = dict(commitments)
    forged["equal-time"] = "0" * 64
    try:
        _derive_seeds_from_entropy(entropy, 7, forged, g6_module=g6)
    except ValueError:
        pass
    else:
        raise AssertionError("forged HMAC stage commitment was accepted")
    threshold = matches.protocol.promotion_log_threshold(7)
    if threshold != math.log(100.0) + 7 * math.log(2.0):
        raise AssertionError("frozen v2/global attempt alpha threshold changed")
    print("Generation-6 frozen-v2 HCE adapter self-test passed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    ready = commands.add_parser("readiness")
    ready.add_argument("--handoff", required=True, type=Path)
    commands.add_parser("freeze-selector-base")
    prepare_cmd = commands.add_parser("prepare")
    prepare_cmd.add_argument("--handoff", required=True, type=Path)
    verify_auth = commands.add_parser("verify-authorization")
    verify_auth.add_argument("--authorization", required=True, type=Path)

    launch_cmd = commands.add_parser("launch")
    launch_cmd.add_argument("--authorization", required=True, type=Path)
    launch_cmd.add_argument("--gate", required=True, choices=FORMAL_GATES)
    launch_cmd.add_argument("--action", choices=("run", "resume"))
    assess_cmd = commands.add_parser("assess")
    assess_cmd.add_argument("--authorization", required=True, type=Path)
    assess_cmd.add_argument("--gate", required=True, choices=FORMAL_GATES)
    idle_cmd = commands.add_parser("attest-idle")
    idle_cmd.add_argument("--authorization", required=True, type=Path)
    idle_cmd.add_argument("--gate", required=True, choices=sorted(TIMED_GATES))
    idle_cmd.add_argument("--operator", required=True)

    close_cmd = commands.add_parser("close")
    close_cmd.add_argument("--authorization", required=True, type=Path)
    close_cmd.add_argument("--abort-reason")
    verify_close = commands.add_parser("verify-closure")
    verify_close.add_argument("--closure", required=True, type=Path)
    verify_close.add_argument("--handoff", required=True, type=Path)
    verify_historical = commands.add_parser("verify-historical-closure")
    verify_historical.add_argument("--closure", required=True, type=Path)
    verify_historical.add_argument("--handoff", required=True, type=Path)
    bridge_cmd = commands.add_parser("bridge")
    bridge_cmd.add_argument("--closure", required=True, type=Path)
    bridge_cmd.add_argument("--handoff", required=True, type=Path)

    worker = commands.add_parser("selector-worker", help=argparse.SUPPRESS)
    worker.add_argument("--attempt", required=True, type=int)
    worker.add_argument("--shadow-artifact-root", required=True, type=Path)
    worker.add_argument("--selector-workspace-root", required=True, type=Path)
    worker.add_argument("--parent-lock-token", required=True)
    commands.add_parser("self-test")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "self-test":
        self_test()
    elif args.command == "selector-worker":
        _selector_worker(
            attempt_index=args.attempt,
            shadow_artifact_root=args.shadow_artifact_root,
            selector_workspace_root=args.selector_workspace_root,
            parent_lock_token=args.parent_lock_token,
        )
    elif args.command == "readiness":
        print(json.dumps(readiness(args.handoff), indent=2, sort_keys=True))
    elif args.command == "freeze-selector-base":
        print(json.dumps(freeze_selector_base(), indent=2, sort_keys=True))
    elif args.command == "prepare":
        print(json.dumps(prepare(args.handoff), indent=2, sort_keys=True))
    elif args.command == "verify-authorization":
        value = verify_authorization(args.authorization)
        print(json.dumps({"attemptIndex": value["attemptIndex"], "valid": True}, indent=2))
    elif args.command == "launch":
        launch(args.authorization, gate=args.gate, action=args.action)
    elif args.command == "assess":
        assess(args.authorization, gate=args.gate)
    elif args.command == "attest-idle":
        attest_idle(args.authorization, gate=args.gate, operator=args.operator)
    elif args.command == "close":
        print(
            json.dumps(
                close_attempt(args.authorization, abort_reason=args.abort_reason),
                indent=2,
                sort_keys=True,
            )
        )
    elif args.command == "verify-closure":
        value = verify_attempt_closure(args.closure, handoff=args.handoff)
        print(json.dumps({"outcome": value["outcome"], "valid": True}, indent=2))
    elif args.command == "verify-historical-closure":
        value = verify_historical_attempt_closure(args.closure, handoff=args.handoff)
        print(json.dumps({"outcome": value["outcome"], "valid": True}, indent=2))
    else:
        print(json.dumps(bridge(args.closure, handoff=args.handoff), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
