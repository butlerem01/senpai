#!/usr/bin/env python3
"""Executable, fail-closed G6-vs-G5 practical-match adapter.

The Generation-6 promotion successor owns preregistration, the hidden-seed
claim, the candidate-blind suite, and the statistical decision.  This module
owns only match execution.  It descriptor-loads the exact successor named by
the preregistration, verifies the claim and suite, launches one two-game
colour-swapped pair at a time through the frozen Generation-5 OmegaMatch
runtime, replays every successful move with the byte-identical ChessLib
helper, and appends the successor's exact pair-event schema.

No random value is drawn here.  OmegaMatch receives the constant seed zero
for each one-opening pair file; with one opening this cannot reorder or reveal
the hidden candidate-blind suite.  Pair order is always the already sealed
``pairIndex`` order.

Production publication is append-only:

* the execution seal, pair intent, pair suite/config, launch intents and
  completions, raw completion, pair event, append receipt, and incidents are
  final-name ``O_EXCL`` files;
* the event ledger is canonical JSONL, locked, flushed, and fsynced after one
  whole pair event; and
* resume first replays the complete ledger/sidecar chain.  It may continue an
  interrupted OmegaMatch pair, but it can neither skip nor duplicate a pair.

This file creates no artifact on import and the self-test uses only temporary
directories and fake engines.
"""

from __future__ import annotations

import argparse
from collections import Counter
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import types
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
ADAPTER_ID = "omega-nnue-generation6-practical-match-adapter-v1"
EXECUTION_SEAL_KIND = f"{ADAPTER_ID}-execution-seal"
PAIR_INTENT_KIND = f"{ADAPTER_ID}-pair-intent"
LAUNCH_INTENT_KIND = f"{ADAPTER_ID}-launch-intent"
LAUNCH_COMPLETION_KIND = f"{ADAPTER_ID}-launch-completion"
RAW_COMPLETION_KIND = f"{ADAPTER_ID}-raw-pair-completion"
APPEND_RECEIPT_KIND = f"{ADAPTER_ID}-append-receipt"
INCIDENT_KIND = f"{ADAPTER_ID}-incident"
TRANSCRIPT_KIND = f"{ADAPTER_ID}-execution-transcript"
INCIDENT_HANDOFF_KIND = f"{ADAPTER_ID}-incident-terminal-handoff"
PAIR_SUITE_KIND = f"{ADAPTER_ID}-one-pair-suite"
PROFILE_ID = "omega-nnue-generation6-g6-vs-g5-practical-v1"

REPO = Path(__file__).resolve().parents[2]
TOOL_PATH = Path(__file__).resolve()
TOOL_RELATIVE = TOOL_PATH.relative_to(REPO).as_posix()
SUCCESSOR_PATH = REPO / "tools/omega_nnue/king_state_confirmation_generation6.py"
SUCCESSOR_PIN_NAME = "practicalMatchAdapter"
V2_PROTOCOL_PATH = REPO / "validation/omega-nnue-open-confirmation-v2-protocol.json"

# This byte-pinned authority supplies the already audited OmegaMatch, .NET,
# rules, and replay-helper identities.  The successor must in turn pin this
# adapter before any production preregistration is published.
V2_PROTOCOL_PIN = {
    "path": str(V2_PROTOCOL_PATH.resolve()),
    "bytes": 47_861,
    "sha256": "9b2a558893806af89b60f9c3a96f36934cc7023b192758e436bfcb715e4cdc49",
}

# Deterministic practical budget.  30k is the frozen v2 development budget;
# the later formal equal-node gate remains a distinct 60k-node test.
EXECUTION_POLICY: Mapping[str, Any] = {
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
    "harnessPairTimeoutMs": 2 * 400 * 60_000 + 120_000,
    "openingExecutionSeed": 0,
    "openingExecutionSeedIsStatisticallyInertForSingletonSuite": True,
    "hiddenSelectorSeedReadOrPublished": False,
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
CELLS = (
    "opening:w",
    "opening:b",
    "middlegame:w",
    "middlegame:b",
    "late:w",
    "late:b",
    "endgame:w",
    "endgame:b",
)
MOVE = re.compile(r"^[a-jw][0-9][a-jw][0-9][qrbncw]?$", re.IGNORECASE)
HEX256 = re.compile(r"^[0-9a-f]{64}$")
UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
RELEVANT_PROCESS_NAMES = {
    "dotnet",
    "dotnet.exe",
    "omegamatch",
    "omegamatch.exe",
    "senpai",
    "senpai.exe",
}


class AdapterError(RuntimeError):
    """Base fail-closed execution error."""


class SafetyEvidenceError(AdapterError):
    """Raw evidence proves an unsafe or unauthenticated launch."""

    def __init__(self, category: str, message: str) -> None:
        if category not in SAFETY_COUNTERS:
            raise ValueError(f"unknown safety category {category!r}")
        super().__init__(message)
        self.category = category


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _canonical(value: Any) -> bytes:
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or UTC.fullmatch(value) is None:
        raise ValueError(f"{label} is not canonical whole-microsecond UTC")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} is not UTC")
    return parsed


def _safe_file(path: Path | str) -> Path:
    value = Path(path).expanduser().resolve()
    if not value.is_file() or value.is_symlink():
        raise ValueError(f"unsafe or absent regular file: {value}")
    return value


def _sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with _safe_file(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path | str) -> dict[str, Any]:
    value = _safe_file(path)
    before = value.stat()
    digest = _sha256(value)
    after = value.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"file changed while hashed: {value}")
    return {"path": str(value), "bytes": after.st_size, "sha256": digest}


def _identity_shape(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity shape changed")
    if (
        type(value.get("path")) is not str
        or not value["path"]
        or type(value.get("bytes")) is not int
        or isinstance(value["bytes"], bool)
        or value["bytes"] < 0
        or type(value.get("sha256")) is not str
        or HEX256.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(value)


def verify_identity(value: Any, label: str) -> Path:
    expected = _identity_shape(value, label)
    path = _safe_file(expected["path"])
    if identity(path) != expected:
        raise ValueError(f"{label} identity changed")
    return path


def _prefix_identity(payload: bytes) -> dict[str, Any]:
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _strict_object(path: Path | str, label: str) -> dict[str, Any]:
    source = _safe_file(path)
    try:
        value = json.loads(
            source.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token {token}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not strict UTF-8 JSON: {source}") from error
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _exclusive_bytes(path: Path | str, payload: bytes, mode: int = 0o644) -> dict[str, Any]:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        mode,
    )
    owned = True
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("exclusive write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        owned = False
    finally:
        os.close(descriptor)
        if owned:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
    return identity(target)


def _exclusive_json(path: Path | str, value: Mapping[str, Any]) -> dict[str, Any]:
    return _exclusive_bytes(path, _canonical(dict(value)))


def _same_json(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(
            _same_json(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right)
        )
    return left == right


def _exact_keys(value: Any, fields: Iterable[str], label: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError(f"{label} field inventory changed")
    return value


def _load_v2_runtime() -> dict[str, Any]:
    if identity(V2_PROTOCOL_PATH) != V2_PROTOCOL_PIN:
        raise ImportError("frozen v2 protocol identity changed")
    protocol = _strict_object(V2_PROTOCOL_PATH, "frozen v2 protocol")
    shared = protocol.get("sharedRuntime")
    if type(shared) is not dict:
        raise ValueError("frozen v2 sharedRuntime is absent")
    pins = shared.get("identities")
    if type(pins) is not dict:
        raise ValueError("frozen v2 runtime identities are absent")

    def pinned(name: str) -> dict[str, Any]:
        item = _identity_shape(pins.get(name), f"v2 {name}")
        raw = Path(item["path"])
        path = raw.resolve() if raw.is_absolute() else (REPO / raw).resolve()
        actual = identity(path)
        if actual["bytes"] != item["bytes"] or actual["sha256"] != item["sha256"]:
            raise ValueError(f"v2 {name} bytes changed")
        return actual

    values = {
        "protocol": identity(V2_PROTOCOL_PATH),
        "dotnetHost": pinned("dotnetHost"),
        "omegaMatchAssembly": pinned("omegaMatchAssembly"),
        "omegaMatchRulesAssembly": pinned("omegaMatchRulesAssembly"),
        "prefixReplayAssembly": pinned("prefixReplayAssembly"),
        "prefixReplayRulesAssembly": pinned("prefixReplayRulesAssembly"),
        "omegaMatchBundleSha256": shared.get("omegaMatchBundleSha256"),
        "prefixReplayBundleSha256": shared.get("prefixReplayBundleSha256"),
    }
    if (
        type(values["omegaMatchBundleSha256"]) is not str
        or HEX256.fullmatch(values["omegaMatchBundleSha256"]) is None
        or type(values["prefixReplayBundleSha256"]) is not str
        or HEX256.fullmatch(values["prefixReplayBundleSha256"]) is None
        or values["omegaMatchRulesAssembly"]["bytes"]
        != values["prefixReplayRulesAssembly"]["bytes"]
        or values["omegaMatchRulesAssembly"]["sha256"]
        != values["prefixReplayRulesAssembly"]["sha256"]
    ):
        raise ValueError("frozen v2 runtime/rules binding changed")
    return values


def _require_reciprocal_successor_pin(successor: Any) -> None:
    pins = getattr(successor, "PINNED_AUTHORITIES", None)
    adapter_pin = pins.get(SUCCESSOR_PIN_NAME) if isinstance(pins, Mapping) else None
    own = identity(TOOL_PATH)
    if (
        type(adapter_pin) not in (tuple, list)
        or len(adapter_pin) != 3
        or adapter_pin[0] != TOOL_RELATIVE
        or adapter_pin[1] != own["bytes"]
        or adapter_pin[2] != own["sha256"]
    ):
        raise ImportError(
            "preregistered successor authority does not reciprocally pin this adapter"
        )


def _descriptor_load_successor(preregistration: Path) -> tuple[types.ModuleType, dict[str, Any]]:
    """Load only the exact successor implementation cited by preregistration.

    The successor must pin this adapter in its protocol before production.
    Conversely, this adapter uses the preregistration's immutable implementation
    identity so that a post-preregistration successor edit cannot change how the
    hidden claim or suite is interpreted.
    """

    shallow = _strict_object(preregistration, "successor preregistration envelope")
    implementation = _identity_shape(
        shallow.get("implementation"), "successor implementation"
    )
    path = verify_identity(implementation, "successor implementation")
    if path != SUCCESSOR_PATH.resolve():
        raise ImportError("preregistration cites a noncanonical successor implementation")
    protocol_identity = _identity_shape(shallow.get("protocol"), "successor protocol")
    verify_identity(protocol_identity, "successor protocol")
    payload = path.read_bytes()
    name = f"_omega_g6_successor_{implementation['sha256'][:16]}"
    if name in sys.modules:
        raise ImportError("successor descriptor module name is already occupied")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    module.__loader__ = None
    sys.modules[name] = module
    try:
        exec(compile(payload, str(path), "exec"), module.__dict__)
        required = (
            "verify_preregistration",
            "verify_candidate_claim",
            "verify_practical_suite",
            "verify_deployment_bundle",
            "publish_practical_decision",
            "verify_practical_decision",
            "publish_hce_handoff",
            "_sequential_practical",
        )
        if (
            getattr(module, "PROTOCOL_ID", None)
            != "omega-nnue-generation6-promotion-confirmation-v1"
            or any(not callable(getattr(module, name, None)) for name in required)
            or getattr(module, "EVENT_KIND", None)
            != "omega-nnue-generation6-promotion-confirmation-v1-practical-pair-event"
            or tuple(getattr(module, "SAFETY_COUNTERS", ())) != SAFETY_COUNTERS
        ):
            raise ImportError("successor integration API changed")
        _require_reciprocal_successor_pin(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    # Keep the returned module object, but remove the descriptor name so a
    # later independent replay in the same verifier process must execute the
    # authenticated bytes again instead of trusting mutable import-cache state.
    sys.modules.pop(name, None)
    return module, shallow


@dataclass(frozen=True)
class RuntimeContext:
    successor: types.ModuleType | Any
    preregistration_path: Path
    claim_path: Path
    suite_path: Path
    preregistration: Mapping[str, Any]
    claim: Mapping[str, Any]
    suite: Mapping[str, Any]
    deployment: Mapping[str, Any]
    candidate_engine: Mapping[str, Any]
    candidate_network: Mapping[str, Any]
    incumbent_engine: Mapping[str, Any]
    incumbent_network: Mapping[str, Any]
    candidate_options: Mapping[str, str]
    incumbent_options: Mapping[str, str]
    runtime: Mapping[str, Any]
    attempt_index: int
    execution_root: Path
    ledger_path: Path


def expected_successor_adapter_authority() -> dict[str, Any]:
    """Exact preregistration/protocol object required before match launch."""

    return {
        "schemaVersion": SCHEMA_VERSION,
        "adapterId": ADAPTER_ID,
        "implementation": identity(TOOL_PATH),
        "executionPolicy": dict(EXECUTION_POLICY),
        "eventKind": (
            "omega-nnue-generation6-promotion-confirmation-v1-practical-pair-event"
        ),
        "executionSealKind": EXECUTION_SEAL_KIND,
        "executionTranscriptKind": TRANSCRIPT_KIND,
        "incidentKind": INCIDENT_KIND,
        "incidentTerminalHandoffKind": INCIDENT_HANDOFF_KIND,
        "decisionPublisherApi": "publish_practical_decision_from_execution_transcript",
        "decisionVerifierApi": "verify_practical_decision_from_execution_transcript",
        "incidentPublisherApi": "publish_practical_incident_failure",
        "incidentVerifierApi": "verify_practical_incident_failure",
        "successorPinnedAuthoritiesKey": SUCCESSOR_PIN_NAME,
        "historicalAttemptScannerReplaysAdapterEvidence": True,
        "resultInformationRead": False,
        "finalStageSeal": True,
    }


def _require_successor_adapter_authority(
    successor: Any, preregistration: Mapping[str, Any]
) -> dict[str, Any]:
    expected = expected_successor_adapter_authority()
    cited = preregistration.get("practicalExecutionAuthority")
    module_value = getattr(successor, "PRACTICAL_ADAPTER_AUTHORITY", None)
    protocol_identity = _identity_shape(
        preregistration.get("protocol"), "successor protocol"
    )
    protocol_path = verify_identity(protocol_identity, "successor protocol")
    protocol = _strict_object(protocol_path, "successor protocol")
    protocol_value = protocol.get("practicalExecutionAuthority")
    if (
        not _same_json(cited, expected)
        or not _same_json(module_value, expected)
        or not _same_json(protocol_value, expected)
    ):
        raise ImportError(
            "successor protocol/module/preregistration does not pin the exact "
            "practical adapter, execution policy, transcript, and incident bridge"
        )
    for name in (
        expected["decisionPublisherApi"],
        expected["decisionVerifierApi"],
        expected["incidentPublisherApi"],
        expected["incidentVerifierApi"],
    ):
        if not callable(getattr(successor, name, None)):
            raise ImportError(f"successor practical integration API is absent: {name}")
    return expected


def _network_options(
    base: Mapping[str, Any], network: Mapping[str, Any], label: str
) -> dict[str, str]:
    expected_fields = {
        "UCI_Variant",
        "UCI_Chess960",
        "UseOmegaNNUE",
        "Hash",
        "Threads",
        "Ponder",
        "OmegaNNUEFile",
    }
    if type(base) is not dict or set(base) != expected_fields:
        raise ValueError(f"{label} options differ from exact G6 deployment profile")
    result = {str(key): str(value) for key, value in base.items()}
    expected = {
        "UCI_Variant": "omega",
        "UCI_Chess960": "false",
        "UseOmegaNNUE": "true",
        "Hash": "64",
        "Threads": "1",
        "Ponder": "false",
        "OmegaNNUEFile": str(Path(network["path"]).resolve()),
    }
    if result != expected:
        raise ValueError(f"{label} options changed")
    return result


def load_context(
    *, preregistration: Path, claim: Path, suite: Path
) -> RuntimeContext:
    preregistration = preregistration.resolve()
    claim = claim.resolve()
    suite = suite.resolve()
    successor, shallow = _descriptor_load_successor(preregistration)
    prereg = successor.verify_preregistration(preregistration)
    if not _same_json(prereg, shallow):
        raise ValueError("descriptor and successor preregistration parses differ")
    # This is deliberately before the candidate claim and candidate-blind suite
    # are opened, and therefore before any raw match/result ledger can be read.
    _require_successor_adapter_authority(successor, prereg)
    claim_doc = successor.verify_candidate_claim(
        claim, preregistration=preregistration
    )
    suite_doc = successor.verify_practical_suite(
        suite, preregistration=preregistration, claim=claim
    )
    deployment = successor.verify_deployment_bundle(
        Path(prereg["deploymentBundle"]["path"])
    )
    candidate_engine = _identity_shape(deployment["engine"], "G6 engine")
    candidate_network = _identity_shape(deployment["network"], "G6 network")
    incumbent = prereg.get("g5Incumbent")
    if type(incumbent) is not dict:
        raise ValueError("G5 incumbent binding is absent")
    incumbent_engine = _identity_shape(incumbent.get("engine"), "G5 engine")
    incumbent_network = _identity_shape(incumbent.get("network"), "G5 network")
    for label, item in (
        ("G6 engine", candidate_engine),
        ("G6 network", candidate_network),
        ("G5 engine", incumbent_engine),
        ("G5 network", incumbent_network),
    ):
        verify_identity(item, label)
    candidate_options = _network_options(
        deployment.get("options"), candidate_network, "G6 candidate"
    )
    incumbent_options = dict(candidate_options)
    incumbent_options["OmegaNNUEFile"] = str(Path(incumbent_network["path"]).resolve())
    runtime = _load_v2_runtime()

    # A real G5 authorization contains the already frozen host and harness.
    # Require byte parity when those bindings are present; the successor's
    # synthetic unit fixtures intentionally omit them and never call this
    # production loader.
    authorization = _strict_object(
        Path(incumbent["authorization"]["path"]), "G5 incumbent authorization"
    )
    for field in ("dotnetHost", "omegaMatchAssembly"):
        if field not in authorization:
            raise ValueError(f"G5 authorization lacks frozen {field}")
        cited = _identity_shape(authorization[field], f"G5 {field}")
        verify_identity(cited, f"G5 {field}")
        frozen = runtime[field]
        if cited["bytes"] != frozen["bytes"] or cited["sha256"] != frozen["sha256"]:
            raise ValueError(f"G5 {field} differs from frozen v2 runtime")
        runtime[field] = cited
    attempt = claim_doc.get("attemptIndex")
    if type(attempt) is not int or isinstance(attempt, bool) or attempt < 1:
        raise ValueError("claim attempt index changed")
    if suite_doc.get("attemptIndex") != attempt:
        raise ValueError("claim/suite attempt differs")
    root = (
        Path(prereg["artifactRoot"]).resolve()
        / "attempts"
        / f"attempt-{attempt:06d}"
        / "07-practical-execution-v1"
    )
    return RuntimeContext(
        successor=successor,
        preregistration_path=preregistration,
        claim_path=claim,
        suite_path=suite,
        preregistration=prereg,
        claim=claim_doc,
        suite=suite_doc,
        deployment=deployment,
        candidate_engine=candidate_engine,
        candidate_network=candidate_network,
        incumbent_engine=incumbent_engine,
        incumbent_network=incumbent_network,
        candidate_options=candidate_options,
        incumbent_options=incumbent_options,
        runtime=runtime,
        attempt_index=attempt,
        execution_root=root,
        ledger_path=root / "practical-events.jsonl",
    )


def _execution_seal_value(context: RuntimeContext) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": EXECUTION_SEAL_KIND,
        "status": "sealed-before-first-practical-match",
        "createdUtc": None,
        "adapter": identity(TOOL_PATH),
        "successorImplementation": context.preregistration["implementation"],
        "successorProtocol": context.preregistration["protocol"],
        "preregistration": identity(context.preregistration_path),
        "claim": identity(context.claim_path),
        "suite": identity(context.suite_path),
        "attemptIndex": context.attempt_index,
        "candidate": {
            "engine": dict(context.candidate_engine),
            "network": dict(context.candidate_network),
            "options": dict(context.candidate_options),
        },
        "incumbent": {
            "engine": dict(context.incumbent_engine),
            "network": dict(context.incumbent_network),
            "options": dict(context.incumbent_options),
        },
        "runtime": dict(context.runtime),
        "executionPolicy": dict(EXECUTION_POLICY),
        "pairOrder": "exact sealed successor entries order; no shuffle",
        "hiddenSeedFieldsReadOrPublished": 0,
        "resultInformationReadBeforeSeal": False,
        "finalStageSeal": True,
    }


def prepare_execution(context: RuntimeContext) -> dict[str, Any]:
    context.execution_root.mkdir(parents=True, exist_ok=True)
    seal_path = context.execution_root / "00-execution-seal.json"
    expected = _execution_seal_value(context)
    if seal_path.exists():
        value = _strict_object(seal_path, "practical execution seal")
        replay = dict(expected)
        replay["createdUtc"] = value.get("createdUtc")
        _timestamp(value.get("createdUtc"), "execution seal createdUtc")
        if not _same_json(value, replay):
            raise ValueError("practical execution seal changed")
    else:
        expected["createdUtc"] = _utc_now()
        _exclusive_json(seal_path, expected)
    if not context.ledger_path.exists():
        _exclusive_bytes(context.ledger_path, b"")
    elif context.ledger_path.is_symlink() or not context.ledger_path.is_file():
        raise ValueError("practical ledger is unsafe")
    return identity(seal_path)


def _verify_execution_seal(context: RuntimeContext) -> dict[str, Any]:
    path = context.execution_root / "00-execution-seal.json"
    value = _strict_object(path, "practical execution seal")
    expected = _execution_seal_value(context)
    expected["createdUtc"] = value.get("createdUtc")
    _timestamp(value.get("createdUtc"), "execution seal createdUtc")
    if not _same_json(value, expected):
        raise ValueError("practical execution seal changed")
    return value


def _pair_root(context: RuntimeContext, index: int) -> Path:
    return context.execution_root / "pairs" / f"pair-{index:06d}"


def _pair_paths(context: RuntimeContext, index: int) -> dict[str, Path]:
    root = _pair_root(context, index)
    return {
        "root": root,
        "intent": root / "00-intent.json",
        "suite": root / "01-opening-suite.json",
        "config": root / "02-match-config.json",
        "rawRoot": root / "raw",
        "rawEvents": root / "raw/events.jsonl",
        "launches": root / "launches",
        "rawCompletion": root / "03-raw-completion.json",
        "event": root / "04-pair-event.json",
        "appendReceipt": root / "05-append-receipt.json",
        "incident": root / "99-incident.json",
    }


def _entry(context: RuntimeContext, index: int) -> dict[str, Any]:
    entries = context.suite.get("entries")
    if type(entries) is not list or not 1 <= index <= len(entries):
        raise ValueError("pair index is outside the sealed practical suite")
    value = entries[index - 1]
    expected = {
        "pairIndex",
        "openingId",
        "phase",
        "sideToMove",
        "ofen",
        "moves",
        "games",
    }
    _exact_keys(value, expected, f"suite pair {index}")
    if (
        value.get("pairIndex") != index
        or value.get("games") != ["candidate-white", "candidate-black"]
        or f"{value.get('phase')}:{value.get('sideToMove')}" not in CELLS
        or type(value.get("openingId")) is not str
        or type(value.get("ofen")) is not str
        or not value["ofen"].strip()
        or type(value.get("moves")) is not list
        or any(type(move) is not str or MOVE.fullmatch(move) is None for move in value["moves"])
    ):
        raise ValueError(f"suite pair {index} changed")
    return dict(value)


def _opening_suite_value(context: RuntimeContext, entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": PAIR_SUITE_KIND,
        "name": f"G6-vs-G5 practical pair {entry['pairIndex']}",
        "generation6Practical": {
            "attemptIndex": context.attempt_index,
            "pairIndex": entry["pairIndex"],
            "openingId": entry["openingId"],
            "phase": entry["phase"],
            "sideToMove": entry["sideToMove"],
            "sourceSuite": identity(context.suite_path),
            "hiddenSeedRead": False,
        },
        "openings": [
            {
                "id": entry["openingId"],
                "source": "authenticated Generation-6 candidate-blind suite",
                "initialOfen": " ".join(str(entry["ofen"]).split()),
                "moves": list(entry["moves"]),
            }
        ],
    }


def _engine_spec(
    engine_id: str,
    engine: Mapping[str, Any],
    network: Mapping[str, Any],
    options: Mapping[str, str],
) -> dict[str, Any]:
    executable = Path(engine["path"]).resolve()
    return {
        "id": engine_id,
        "executable": str(executable),
        "arguments": "",
        "workingDirectory": str(executable.parent),
        "expectedSha256": engine["sha256"],
        "expectedAssetSha256": {"OmegaNNUEFile": network["sha256"]},
        "options": dict(options),
    }


def _config_value(
    context: RuntimeContext,
    entry: Mapping[str, Any],
    suite_path: Path,
    suite_identity: Mapping[str, Any],
    raw_root: Path,
) -> dict[str, Any]:
    run_id = f"g6-vs-g5-k{context.attempt_index:06d}-p{entry['pairIndex']:06d}"
    freshness = hashlib.sha256(
        (
            context.claim["entropyCommitment"]
            + "\x00"
            + context.suite["stageSeedCommitment"]
            + "\x00"
            + str(entry["pairIndex"])
        ).encode("ascii")
    ).hexdigest()
    return {
        "schemaVersion": 1,
        "expectedHarnessSha256": context.runtime["omegaMatchAssembly"]["sha256"],
        "expectedHarnessBundleSha256": context.runtime["omegaMatchBundleSha256"],
        "expectedOpeningSuiteSha256": suite_identity["sha256"],
        "runId": run_id,
        "profileId": PROFILE_ID,
        "freshnessMarker": freshness,
        "outputDirectory": str(raw_root.resolve()),
        "seed": EXECUTION_POLICY["openingExecutionSeed"],
        "engines": [
            _engine_spec(
                "g6-candidate",
                context.candidate_engine,
                context.candidate_network,
                context.candidate_options,
            ),
            _engine_spec(
                "g5-incumbent",
                context.incumbent_engine,
                context.incumbent_network,
                context.incumbent_options,
            ),
        ],
        "match": {
            "engineA": "g6-candidate",
            "engineB": "g5-incumbent",
            "openingsFile": str(suite_path.resolve()),
            "repeats": 1,
            "maxPlies": EXECUTION_POLICY["maxPlies"],
            "absoluteMaxPlies": EXECUTION_POLICY["absoluteMaxPlies"],
            "mode": "nodes",
            "depth": 6,
            "nodes": EXECUTION_POLICY["nodesPerMove"],
            "moveTimeMs": 1_000,
            "initialTimeMs": 60_000,
            "incrementMs": 500,
            "searchTimeoutMs": EXECUTION_POLICY["searchTimeoutMs"],
            "stopGraceMs": EXECUTION_POLICY["stopGraceMs"],
            "bootstrapIterations": 10_000,
            "sequentialGate": {
                "candidateEngine": "g6-candidate",
                "minimumPairs": 128,
                "nullElo": 0.0,
                "promotionAlpha": 0.01,
                "futilityBeta": 0.05,
            },
            "freshProcessPerGame": True,
        },
    }


def _preview_identity(path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _prepare_pair(
    context: RuntimeContext, index: int, ledger_payload: bytes
) -> dict[str, Path]:
    paths = _pair_paths(context, index)
    paths["root"].mkdir(parents=True, exist_ok=True)
    entry = _entry(context, index)
    suite_value = _opening_suite_value(context, entry)
    suite_payload = _canonical(suite_value)
    suite_identity = _preview_identity(paths["suite"], suite_payload)
    config_value = _config_value(
        context, entry, paths["suite"], suite_identity, paths["rawRoot"]
    )
    config_payload = _canonical(config_value)
    config_identity = _preview_identity(paths["config"], config_payload)
    intent = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PAIR_INTENT_KIND,
        "status": "committed-before-pair-launch",
        "createdUtc": None,
        "executionSeal": identity(context.execution_root / "00-execution-seal.json"),
        "preregistration": identity(context.preregistration_path),
        "claim": identity(context.claim_path),
        "suite": identity(context.suite_path),
        "attemptIndex": context.attempt_index,
        "pairIndex": index,
        "entry": entry,
        "pairOpeningSuite": suite_identity,
        "matchConfig": config_identity,
        "ledgerPrefix": _prefix_identity(ledger_payload),
        "executionPolicy": dict(EXECUTION_POLICY),
        "hiddenSeedReadOrPublished": False,
        "terminal": False,
    }
    if paths["intent"].exists():
        actual = _strict_object(paths["intent"], f"pair {index} intent")
        replay = dict(intent)
        replay["createdUtc"] = actual.get("createdUtc")
        _timestamp(actual.get("createdUtc"), f"pair {index} intent createdUtc")
        if not _same_json(actual, replay):
            raise ValueError(f"pair {index} intent changed")
    else:
        intent["createdUtc"] = _utc_now()
        _exclusive_json(paths["intent"], intent)
    for path, payload, label in (
        (paths["suite"], suite_payload, "opening suite"),
        (paths["config"], config_payload, "match config"),
    ):
        if path.exists():
            if path.read_bytes() != payload:
                raise ValueError(f"pair {index} {label} changed")
        else:
            _exclusive_bytes(path, payload)
    return paths


@contextlib.contextmanager
def _ledger_lock(context: RuntimeContext) -> Iterator[None]:
    lock_path = context.execution_root / ".practical-events.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            if stream.tell() == stream.seek(0, io.SEEK_END):
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _verify_event(context: RuntimeContext, value: Any, index: int) -> dict[str, Any]:
    fields = {
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
    game_fields = {
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
    _exact_keys(value, fields, f"pair event {index}")
    entry = _entry(context, index)
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != context.successor.EVENT_KIND
        or value.get("attemptIndex") != context.attempt_index
        or value.get("pairIndex") != index
        or value.get("openingId") != entry["openingId"]
        or value.get("phase") != entry["phase"]
        or value.get("sideToMove") != entry["sideToMove"]
        or value.get("terminal") is not True
        or type(value.get("games")) is not list
        or len(value["games"]) != 2
    ):
        raise ValueError(f"pair event {index} differs from sealed suite")
    for game_index, (game, assignment) in enumerate(
        zip(value["games"], ("candidate-white", "candidate-black")), 1
    ):
        _exact_keys(game, game_fields, f"pair event {index} game {game_index}")
        expected = {
            "gameIndex": game_index,
            "assignment": assignment,
            "candidateEngineSha256": context.candidate_engine["sha256"],
            "candidateNetworkSha256": context.candidate_network["sha256"],
            "incumbentEngineSha256": context.incumbent_engine["sha256"],
            "incumbentNetworkSha256": context.incumbent_network["sha256"],
            "candidateLoadedNetworkSha256": context.candidate_network["sha256"],
            "incumbentLoadedNetworkSha256": context.incumbent_network["sha256"],
            "candidateNnueActive": True,
            "incumbentNnueActive": True,
            "terminal": True,
        }
        for key, wanted in expected.items():
            if game.get(key) != wanted or type(game.get(key)) is not type(wanted):
                raise ValueError(f"pair event {index} game {game_index} {key} changed")
        if type(game.get("candidateHalfPoints")) is not int or game["candidateHalfPoints"] not in {0, 1, 2}:
            raise ValueError(f"pair event {index} game score changed")
    safety = value.get("safety")
    _exact_keys(safety, SAFETY_COUNTERS, f"pair event {index} safety")
    if any(type(safety[name]) is not int or safety[name] < 0 for name in SAFETY_COUNTERS):
        raise ValueError(f"pair event {index} safety counter changed")
    return dict(value)


def _ledger_records(context: RuntimeContext) -> tuple[bytes, list[dict[str, Any]]]:
    payload = context.ledger_path.read_bytes()
    if payload and not payload.endswith(b"\n"):
        raise ValueError("practical ledger has a partial final record")
    records: list[dict[str, Any]] = []
    running = b""
    for index, line in enumerate(payload.splitlines(keepends=True), 1):
        if not line.strip():
            raise ValueError(f"practical ledger line {index} is blank")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_duplicates,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON token {token}")
                ),
            )
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"practical ledger line {index} is invalid") from error
        _verify_event(context, value, index)
        if line != _canonical(value):
            raise ValueError(f"practical ledger line {index} is noncanonical")
        paths = _pair_paths(context, index)
        if not paths["event"].is_file() or paths["event"].read_bytes() != line:
            raise ValueError(f"pair {index} event sidecar differs from ledger")
        before = _prefix_identity(running)
        running += line
        after = _prefix_identity(running)
        if paths["appendReceipt"].exists():
            receipt = _strict_object(paths["appendReceipt"], f"pair {index} append receipt")
            expected = {
                "schemaVersion": SCHEMA_VERSION,
                "kind": APPEND_RECEIPT_KIND,
                "status": "pair-event-durably-appended",
                "createdUtc": receipt.get("createdUtc"),
                "attemptIndex": context.attempt_index,
                "pairIndex": index,
                "event": identity(paths["event"]),
                "ledger": str(context.ledger_path.resolve()),
                "before": before,
                "after": after,
                "terminal": True,
            }
            _timestamp(receipt.get("createdUtc"), f"pair {index} receipt createdUtc")
            if not _same_json(receipt, expected):
                raise ValueError(f"pair {index} append receipt changed")
        records.append(value)
    if running != payload:
        raise AssertionError("ledger prefix accounting changed")
    return payload, records


def _publish_append_receipt(
    context: RuntimeContext, index: int, before: bytes, after: bytes
) -> None:
    paths = _pair_paths(context, index)
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": APPEND_RECEIPT_KIND,
        "status": "pair-event-durably-appended",
        "createdUtc": _utc_now(),
        "attemptIndex": context.attempt_index,
        "pairIndex": index,
        "event": identity(paths["event"]),
        "ledger": str(context.ledger_path.resolve()),
        "before": _prefix_identity(before),
        "after": _prefix_identity(after),
        "terminal": True,
    }
    _exclusive_json(paths["appendReceipt"], value)


def _reconcile_pending_append(context: RuntimeContext) -> tuple[bytes, list[dict[str, Any]]]:
    payload, records = _ledger_records(context)
    # A crash may occur after the pair-event sidecar or ledger append but before
    # its receipt.  Only the immediate next sidecar may be reconciled.
    if records:
        last_paths = _pair_paths(context, len(records))
        if not last_paths["appendReceipt"].exists():
            line = _canonical(records[-1])
            _publish_append_receipt(
                context, len(records), payload[: -len(line)], payload
            )
    next_index = len(records) + 1
    next_paths = _pair_paths(context, next_index)
    if next_paths["event"].exists():
        value = _strict_object(next_paths["event"], f"pair {next_index} event")
        _verify_event(context, value, next_index)
        line = _canonical(value)
        if next_paths["event"].read_bytes() != line:
            raise ValueError(f"pair {next_index} event sidecar is noncanonical")
        descriptor = os.open(
            context.ledger_path,
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0),
        )
        try:
            if os.write(descriptor, line) != len(line):
                raise OSError("ledger append was short")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        after = payload + line
        _publish_append_receipt(context, next_index, payload, after)
        payload, records = _ledger_records(context)
    pairs_root = context.execution_root / "pairs"
    if pairs_root.exists():
        for child in pairs_root.iterdir():
            match = re.fullmatch(r"pair-([0-9]{6})", child.name)
            if not child.is_dir() or child.is_symlink() or match is None:
                raise ValueError("unexpected practical pair artifact")
            index = int(match.group(1))
            if index > len(records) + 1:
                raise ValueError("practical pair directory skips sealed suite order")
    return payload, records


def validate_ledger(context: RuntimeContext) -> list[dict[str, Any]]:
    if not (context.execution_root / "00-execution-seal.json").is_file():
        raise FileNotFoundError("practical execution has not been prepared")
    if not context.ledger_path.is_file():
        raise ValueError("prepared practical execution lacks its ledger")
    with _ledger_lock(context):
        _, records = _reconcile_pending_append(context)
    if records and len(records) % len(CELLS) == 0:
        for start in range(0, len(records), len(CELLS)):
            cells = {
                f"{record['phase']}:{record['sideToMove']}"
                for record in records[start : start + len(CELLS)]
            }
            if cells != set(CELLS):
                raise ValueError("practical ledger broke balanced suite checkpoints")
    return records


def _parse_processes(payload: str, windows: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if windows:
        import csv

        rows = csv.reader(io.StringIO(payload))
        for row in rows:
            if len(row) < 2:
                continue
            name = Path(row[0]).name.casefold()
            try:
                pid = int(row[1])
            except ValueError:
                continue
            if name in RELEVANT_PROCESS_NAMES:
                result.append({"name": name, "pid": pid})
    else:
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
                result.append({"name": name, "pid": pid})
    return sorted(result, key=lambda item: (item["name"], item["pid"]))


def _sanitized_environment() -> dict[str, str]:
    allowed = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "PATH",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
    )
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(
        {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "BLIS_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_NOLOGO": "1",
        }
    )
    return env


def _process_snapshot() -> dict[str, Any]:
    env = _sanitized_environment()
    if os.name == "nt":
        system_root = Path(env.get("SystemRoot", r"C:\Windows"))
        completed = subprocess.run(
            [str(system_root / "System32/tasklist.exe"), "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
            env=env,
        )
        rows = _parse_processes(completed.stdout, True)
        source = "tasklist-csv"
    else:
        completed = subprocess.run(
            ["/bin/ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
            env=env,
        )
        rows = _parse_processes(completed.stdout, False)
        source = "ps-pid-args"
    return {"capturedUtc": _utc_now(), "source": source, "relevantProcesses": rows}


def _kill_process_tree(pid: int) -> None:
    if type(pid) is not int or pid <= 0:
        return
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        subprocess.run(
            [str(system_root / "System32/taskkill.exe"), "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=_sanitized_environment(),
        )
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


@dataclass(frozen=True)
class ProcessOutcome:
    return_code: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes


def _run_subprocess(
    command: Sequence[str], *, cwd: Path, timeout_seconds: float
) -> ProcessOutcome:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=_sanitized_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
        start_new_session=(os.name != "nt"),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return ProcessOutcome(process.returncode, False, stdout, stderr)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process.pid)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        return ProcessOutcome(process.returncode, True, stdout, stderr)


Executor = Callable[[Sequence[str], Path, float], ProcessOutcome]
Snapshotter = Callable[[], Mapping[str, Any]]


def _default_executor(command: Sequence[str], cwd: Path, timeout: float) -> ProcessOutcome:
    return _run_subprocess(command, cwd=cwd, timeout_seconds=timeout)


def _pid_set(snapshot: Mapping[str, Any]) -> set[int]:
    rows = snapshot.get("relevantProcesses")
    if type(rows) is not list:
        raise ValueError("process snapshot is malformed")
    result: set[int] = set()
    for row in rows:
        if type(row) is not dict or type(row.get("pid")) is not int:
            raise ValueError("process snapshot entry is malformed")
        result.add(row["pid"])
    return result


def _launch_records(paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    root = paths["launches"]
    if not root.exists():
        return []
    if not root.is_dir() or root.is_symlink():
        raise ValueError("launch record directory is unsafe")
    result: list[dict[str, Any]] = []
    names = sorted(path.name for path in root.iterdir())
    recognized = re.compile(r"([0-9]{6})-(intent|completion|stdout|stderr)\.(json|log)")
    grouped: dict[int, set[str]] = {}
    for name in names:
        match = recognized.fullmatch(name)
        if match is None:
            raise ValueError("unexpected pair launch artifact")
        sequence = int(match.group(1))
        grouped.setdefault(sequence, set()).add(match.group(2))
    if set(grouped) != set(range(1, len(grouped) + 1)):
        raise ValueError("pair launch sequence has a gap")
    for sequence in range(1, len(grouped) + 1):
        fields = grouped[sequence]
        if "intent" not in fields:
            raise ValueError("pair launch lacks intent")
        intent_path = root / f"{sequence:06d}-intent.json"
        completion_path = root / f"{sequence:06d}-completion.json"
        intent = _strict_object(intent_path, f"launch {sequence} intent")
        completion = (
            _strict_object(completion_path, f"launch {sequence} completion")
            if completion_path.exists()
            else None
        )
        if completion is None and sequence != len(grouped):
            raise ValueError("nonterminal launch intent precedes a later launch")
        if completion is not None and fields != {"intent", "completion", "stdout", "stderr"}:
            raise ValueError("completed launch sidecars changed")
        if completion is None and fields not in (
            {"intent"},
            {"intent", "stdout"},
            {"intent", "stdout", "stderr"},
        ):
            raise ValueError("pending launch has impossible output sidecars")
        result.append({"sequence": sequence, "intent": intent, "completion": completion})
    return result


def _raw_identity_or_empty(path: Path) -> dict[str, Any]:
    return identity(path) if path.is_file() else {"path": str(path.resolve()), "bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}


def _launch_once(
    context: RuntimeContext,
    paths: Mapping[str, Path],
    *,
    executor: Executor,
    snapshotter: Snapshotter,
) -> dict[str, Any]:
    launches = _launch_records(paths)
    if launches and launches[-1]["completion"] is None:
        raise ValueError("pending launch must be recovered before a new launch")
    sequence = len(launches) + 1
    if sequence > EXECUTION_POLICY["maximumLaunchAttemptsPerPair"]:
        raise SafetyEvidenceError("malformedResults", "pair exhausted its fixed launch-attempt cap")
    action = "resume" if paths["rawEvents"].exists() else "run"
    pre = dict(snapshotter())
    if _pid_set(pre):
        raise AdapterError("practical launch requires no existing OmegaMatch/Senpai/dotnet process")
    dotnet = verify_identity(context.runtime["dotnetHost"], "frozen dotnet host")
    assembly = verify_identity(context.runtime["omegaMatchAssembly"], "frozen OmegaMatch")
    validate_command = [str(dotnet), str(assembly), "validate", "--config", str(paths["config"])]
    command = [
        str(dotnet),
        str(assembly),
        action,
        "--config",
        str(paths["config"]),
        "--pair-budget",
        "1",
    ]
    raw_before = _raw_identity_or_empty(paths["rawEvents"])
    intent = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LAUNCH_INTENT_KIND,
        "status": "durable-before-process-launch",
        "createdUtc": _utc_now(),
        "attemptIndex": context.attempt_index,
        "pairIndex": _strict_object(paths["intent"], "pair intent")["pairIndex"],
        "sequence": sequence,
        "action": action,
        "validationCommand": validate_command,
        "command": command,
        "config": identity(paths["config"]),
        "rawBefore": raw_before,
        "preLaunchProcesses": pre,
        "hiddenSeedReadOrPublished": False,
        "terminal": False,
    }
    paths["launches"].mkdir(parents=True, exist_ok=True)
    intent_path = paths["launches"] / f"{sequence:06d}-intent.json"
    _exclusive_json(intent_path, intent)
    validate = executor(validate_command, assembly.parent, 120.0)
    if validate.return_code == 0 and not validate.timed_out:
        outcome = executor(
            command,
            assembly.parent,
            EXECUTION_POLICY["harnessPairTimeoutMs"] / 1000.0,
        )
        stdout = validate.stdout + outcome.stdout
        stderr = validate.stderr + outcome.stderr
    else:
        outcome = validate
        stdout, stderr = validate.stdout, validate.stderr
    time.sleep(EXECUTION_POLICY["stopGraceMs"] / 1000.0)
    immediate = dict(snapshotter())
    before_pids = _pid_set(pre)
    orphan_rows = [
        row for row in immediate.get("relevantProcesses", []) if row["pid"] not in before_pids
    ]
    for row in orphan_rows:
        _kill_process_tree(row["pid"])
    if orphan_rows:
        time.sleep(0.25)
    post = dict(snapshotter())
    surviving = [row for row in post.get("relevantProcesses", []) if row["pid"] not in before_pids]
    stdout_path = paths["launches"] / f"{sequence:06d}-stdout.log"
    stderr_path = paths["launches"] / f"{sequence:06d}-stderr.log"
    _exclusive_bytes(stdout_path, stdout)
    _exclusive_bytes(stderr_path, stderr)
    raw_after = _raw_identity_or_empty(paths["rawEvents"])
    completion = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LAUNCH_COMPLETION_KIND,
        "status": "process-returned-and-cleanup-audited",
        "createdUtc": _utc_now(),
        "attemptIndex": context.attempt_index,
        "pairIndex": intent["pairIndex"],
        "sequence": sequence,
        "intent": identity(intent_path),
        "returnCode": outcome.return_code,
        "timedOut": outcome.timed_out,
        "stdout": identity(stdout_path),
        "stderr": identity(stderr_path),
        "rawBefore": raw_before,
        "rawAfter": raw_after,
        "postLaunchProcesses": immediate,
        "orphanProcessesObserved": orphan_rows,
        "orphanProcessesSurvivingCleanup": surviving,
        "cleanupAttempted": bool(orphan_rows),
        "terminal": True,
    }
    completion_path = paths["launches"] / f"{sequence:06d}-completion.json"
    _exclusive_json(completion_path, completion)
    if surviving:
        raise SafetyEvidenceError("orphanProcesses", "launched processes survived cleanup")
    return completion


def _strict_raw_records(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    payload = _safe_file(path).read_bytes()
    if not payload or not payload.endswith(b"\n"):
        raise ValueError("raw OmegaMatch events are empty or partial")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(payload.splitlines(keepends=True), 1):
        if not line.strip():
            raise ValueError(f"raw event line {line_number} is blank")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_duplicates,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON token {token}")
                ),
            )
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"raw event line {line_number} is invalid") from error
        if type(value) is not dict:
            raise ValueError(f"raw event line {line_number} is not an object")
        records.append(value)
    return payload, records


def _fold(value: Any) -> Any:
    if type(value) is dict:
        result: dict[str, Any] = {}
        for key, item in value.items():
            folded = str(key).casefold()
            if folded in result:
                raise ValueError("case-folded runtime field is duplicated")
            result[folded] = _fold(item)
        return result
    if type(value) is list:
        return [_fold(item) for item in value]
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _expected_runtime_match(config: Mapping[str, Any]) -> dict[str, Any]:
    return _fold(config["match"])


def _runtime_engine(
    runtime: Mapping[str, Any],
    spec: Mapping[str, Any],
    expected_network: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    required = {
        "Id",
        "Executable",
        "Arguments",
        "WorkingDirectory",
        "Sha256",
        "FileSize",
        "LastWriteUtc",
        "UciName",
        "UciAuthor",
        "Options",
        "ExternalAssets",
        "StartupDiagnostics",
        "OmegaNnueActiveVerified",
    }
    _exact_keys(runtime, required, f"{label} runtime engine")
    executable = Path(spec["executable"]).resolve()
    if (
        runtime.get("Id") != spec["id"]
        or Path(str(runtime.get("Executable"))).resolve() != executable
        or runtime.get("Arguments") != spec["arguments"]
        or Path(str(runtime.get("WorkingDirectory"))).resolve() != Path(spec["workingDirectory"]).resolve()
        or runtime.get("Sha256") != spec["expectedSha256"]
        or runtime.get("FileSize") != executable.stat().st_size
    ):
        raise SafetyEvidenceError("assetMismatches", f"{label} executable identity changed")
    if runtime.get("Options") != spec["options"]:
        raise SafetyEvidenceError("diagnosticMismatches", f"{label} effective options changed")
    assets = runtime.get("ExternalAssets")
    if type(assets) is not list or len(assets) != 1 or type(assets[0]) is not dict:
        raise SafetyEvidenceError("assetMismatches", f"{label} network asset inventory changed")
    asset = assets[0]
    if (
        asset.get("OptionName") != "OmegaNNUEFile"
        or Path(str(asset.get("Path"))).resolve() != Path(expected_network["path"]).resolve()
        or asset.get("Sha256") != expected_network["sha256"]
        or asset.get("FileSize") != expected_network["bytes"]
    ):
        raise SafetyEvidenceError("assetMismatches", f"{label} loaded network identity changed")
    diagnostics = runtime.get("StartupDiagnostics")
    if type(diagnostics) is not list or any(type(line) is not str for line in diagnostics):
        raise SafetyEvidenceError("diagnosticMismatches", f"{label} diagnostics are malformed")
    loaded = [line for line in diagnostics if line.startswith("info string Omega NNUE loaded:")]
    if (
        runtime.get("OmegaNnueActiveVerified") is not True
        or len(loaded) != 1
        or " from " not in loaded[0]
        or Path(loaded[0].rsplit(" from ", 1)[1]).resolve()
        != Path(expected_network["path"]).resolve()
        or not diagnostics
        or diagnostics[-1] != "info string Omega NNUE evaluation active"
    ):
        raise SafetyEvidenceError("diagnosticMismatches", f"{label} NNUE activation changed")
    return dict(runtime)


def _result_half_points(result: str, candidate_white: bool) -> int:
    if result == "1/2-1/2":
        return 1
    if result == "1-0":
        return 2 if candidate_white else 0
    if result == "0-1":
        return 0 if candidate_white else 2
    raise SafetyEvidenceError("malformedResults", "game result token changed")


def _analyze_raw(
    context: RuntimeContext, paths: Mapping[str, Path]
) -> dict[str, Any]:
    payload, records = _strict_raw_records(paths["rawEvents"])
    config = _strict_object(paths["config"], "pair match config")
    opening_suite = _strict_object(paths["suite"], "pair opening suite")
    if not records or records[0].get("RecordType") != "run":
        raise SafetyEvidenceError("malformedResults", "raw events lack the leading run")
    if sum(record.get("RecordType") == "run" for record in records) != 1:
        raise SafetyEvidenceError("malformedResults", "raw events repeat the run record")
    run = records[0]
    expected_run = {
        "RunId": config["runId"],
        "ProfileId": config["profileId"],
        "FreshnessMarker": config["freshnessMarker"],
        "ConfigSha256": identity(paths["config"])["sha256"],
        "OpeningSuiteSha256": identity(paths["suite"])["sha256"],
        "HarnessSha256": context.runtime["omegaMatchAssembly"]["sha256"],
        "HarnessBundleSha256": context.runtime["omegaMatchBundleSha256"],
        "Seed": EXECUTION_POLICY["openingExecutionSeed"],
    }
    for name, wanted in expected_run.items():
        if run.get(name) != wanted or type(run.get(name)) is not type(wanted):
            raise SafetyEvidenceError("assetMismatches", f"raw run {name} changed")
    if _fold(run.get("Match")) != _expected_runtime_match(config):
        raise SafetyEvidenceError("diagnosticMismatches", "effective match policy changed")
    runtime_engines = run.get("Engines")
    specs = config.get("engines")
    if (
        type(runtime_engines) is not list
        or type(specs) is not list
        or len(runtime_engines) != 2
        or len(specs) != 2
    ):
        raise SafetyEvidenceError("diagnosticMismatches", "runtime engine inventory changed")
    by_id = {item.get("Id"): item for item in runtime_engines if type(item) is dict}
    spec_by_id = {item.get("id"): item for item in specs if type(item) is dict}
    if set(by_id) != {"g6-candidate", "g5-incumbent"} or set(spec_by_id) != set(by_id):
        raise SafetyEvidenceError("diagnosticMismatches", "runtime engine IDs changed")
    candidate_runtime = _runtime_engine(
        by_id["g6-candidate"],
        spec_by_id["g6-candidate"],
        context.candidate_network,
        "G6 candidate",
    )
    incumbent_runtime = _runtime_engine(
        by_id["g5-incumbent"],
        spec_by_id["g5-incumbent"],
        context.incumbent_network,
        "G5 incumbent",
    )
    opening = opening_suite.get("openings")
    if type(opening) is not list or len(opening) != 1 or type(opening[0]) is not dict:
        raise SafetyEvidenceError("malformedResults", "pair opening suite changed")
    source = opening[0]
    opening_id = source.get("id")
    pair_id = f"{opening_id}-r001"
    expected_games = (
        {
            "GameId": f"{pair_id}-ab",
            "PairId": pair_id,
            "OpeningId": opening_id,
            "WhiteEngineId": "g6-candidate",
            "BlackEngineId": "g5-incumbent",
        },
        {
            "GameId": f"{pair_id}-ba",
            "PairId": pair_id,
            "OpeningId": opening_id,
            "WhiteEngineId": "g5-incumbent",
            "BlackEngineId": "g6-candidate",
        },
    )
    schedule_index = 0
    attempts: dict[str, int] = {}
    starts: dict[tuple[str, int], dict[str, Any]] = {}
    plies: dict[tuple[str, int], list[dict[str, Any]]] = {}
    results: list[dict[str, Any]] = []
    active: tuple[str, int] | None = None
    for record in records[1:]:
        kind = record.get("RecordType")
        if schedule_index >= 2:
            raise SafetyEvidenceError("malformedResults", "raw events exceed one pair")
        expected = expected_games[schedule_index]
        if kind == "gameStart":
            game_id = record.get("GameId")
            attempt = record.get("Attempt")
            if (
                game_id != expected["GameId"]
                or type(attempt) is not int
                or attempt != attempts.get(str(game_id), 0) + 1
            ):
                raise SafetyEvidenceError("malformedResults", "gameStart is off schedule")
            for name, wanted in expected.items():
                if record.get(name) != wanted or type(record.get(name)) is not type(wanted):
                    raise SafetyEvidenceError("malformedResults", f"gameStart {name} changed")
            if (
                record.get("InitialOfen") != source.get("initialOfen")
                or record.get("OpeningMoves") != source.get("moves")
            ):
                raise SafetyEvidenceError("malformedResults", "gameStart opening changed")
            # Resume is allowed to abandon an incomplete attempt, but the
            # abandoned attempt is retained and later counted unsafe.
            attempts[str(game_id)] = attempt
            active = (str(game_id), attempt)
            starts[active] = dict(record)
            plies[active] = []
        elif kind == "ply":
            key = (str(record.get("GameId")), record.get("Attempt"))
            if key != active or key not in starts:
                raise SafetyEvidenceError("malformedResults", "ply is not linked to active start")
            sequence = plies[key]
            if record.get("Ply") != len(starts[key]["OpeningMoves"]) + len(sequence) + 1:
                raise SafetyEvidenceError("malformedResults", "ply numbering changed")
            search = record.get("Search")
            if type(search) is not dict:
                raise SafetyEvidenceError("malformedResults", "ply search telemetry is absent")
            expected_command = f"go nodes {EXECUTION_POLICY['nodesPerMove']}"
            if search.get("Command") != expected_command:
                raise SafetyEvidenceError("diagnosticMismatches", "search command escaped node policy")
            wall = search.get("WallTimeMs")
            if (
                type(wall) not in (int, float)
                or isinstance(wall, bool)
                or not math.isfinite(float(wall))
                or float(wall) < 0.0
                or type(search.get("DeadlineExceeded")) is not bool
                or type(search.get("ProcessExited")) is not bool
            ):
                raise SafetyEvidenceError("malformedResults", "search telemetry shape changed")
            if sequence and record.get("PreOfen") != sequence[-1].get("PostOfen"):
                raise SafetyEvidenceError("malformedResults", "ply OFEN chain is discontinuous")
            initial_side = str(starts[key]["InitialOfen"]).split()[1]
            if initial_side not in {"w", "b"}:
                raise SafetyEvidenceError("malformedResults", "initial OFEN side changed")
            if len(starts[key]["OpeningMoves"]) % 2:
                initial_side = "b" if initial_side == "w" else "w"
            side = initial_side if len(sequence) % 2 == 0 else ("b" if initial_side == "w" else "w")
            expected_color = "white" if side == "w" else "black"
            expected_engine = (
                starts[key]["WhiteEngineId"]
                if expected_color == "white"
                else starts[key]["BlackEngineId"]
            )
            if record.get("Color") != expected_color or record.get("EngineId") != expected_engine:
                raise SafetyEvidenceError("malformedResults", "ply engine/color relation changed")
            successful = record.get("PostOfen") not in (None, "") and record.get("Error") in (None, "")
            if successful:
                best = record.get("BestMove")
                if (
                    type(best) is not str
                    or MOVE.fullmatch(best) is None
                    or search.get("BestMove") != best
                    or search.get("DeadlineExceeded") is not False
                    or search.get("ProcessExited") is not False
                    or type(record.get("San")) is not str
                    or not record["San"].strip()
                ):
                    raise SafetyEvidenceError("malformedResults", "successful ply telemetry changed")
            elif (
                record.get("PostOfen") not in (None, "")
                or (
                    record.get("Error") in (None, "")
                    and search.get("DeadlineExceeded") is not True
                    and search.get("ProcessExited") is not True
                )
            ):
                raise SafetyEvidenceError("malformedResults", "failed-search ply telemetry changed")
            sequence.append(dict(record))
        elif kind == "gameResult":
            key = (str(record.get("GameId")), record.get("Attempt"))
            if key != active or key not in starts:
                raise SafetyEvidenceError("malformedResults", "gameResult is unlinked")
            start = starts[key]
            for name in (
                "GameId",
                "PairId",
                "Attempt",
                "OpeningId",
                "WhiteEngineId",
                "BlackEngineId",
            ):
                if record.get(name) != start.get(name) or type(record.get(name)) is not type(start.get(name)):
                    raise SafetyEvidenceError("malformedResults", f"gameResult {name} changed")
            if record.get("Result") not in {"1-0", "0-1", "1/2-1/2"}:
                raise SafetyEvidenceError("malformedResults", "gameResult result changed")
            for name in ("IllegalMoves", "IllegalPvs", "ProtocolFailures", "TimeForfeits"):
                if type(record.get(name)) is not int or record[name] < 0:
                    raise SafetyEvidenceError("malformedResults", f"gameResult {name} changed")
            sequence = plies[key]
            successful = [
                ply
                for ply in sequence
                if ply.get("PostOfen") not in (None, "") and ply.get("Error") in (None, "")
            ]
            expected_plies = len(start["OpeningMoves"]) + len(successful)
            if (
                record.get("Plies") != expected_plies
                or type(record.get("FinalOfen")) is not str
                or not record["FinalOfen"].strip()
            ):
                raise SafetyEvidenceError("malformedResults", "gameResult ply count changed")
            if len(sequence) not in {len(successful), len(successful) + 1}:
                raise SafetyEvidenceError("malformedResults", "gameResult failed-ply coverage changed")
            if len(sequence) == len(successful) + 1:
                terminal = sequence[-1]
                if terminal.get("PostOfen") not in (None, ""):
                    raise SafetyEvidenceError("malformedResults", "terminal failed ply has a position")
            if successful and record["FinalOfen"] != successful[-1]["PostOfen"]:
                raise SafetyEvidenceError("malformedResults", "gameResult final OFEN changed")
            observed_illegal_pvs = sum(
                type(ply.get("Pv")) is dict and ply["Pv"].get("IsLegal") is False
                for ply in sequence
            )
            if record["IllegalPvs"] != observed_illegal_pvs:
                raise SafetyEvidenceError("malformedResults", "gameResult illegal-PV count changed")
            candidate_white = start["WhiteEngineId"] == "g6-candidate"
            score = _result_half_points(str(record["Result"]), candidate_white) / 2.0
            score_a = record.get("ScoreA")
            if (
                type(score_a) not in (int, float)
                or isinstance(score_a, bool)
                or not math.isfinite(float(score_a))
                or abs(float(score_a) - score) > 1e-12
            ):
                raise SafetyEvidenceError("malformedResults", "gameResult ScoreA changed")
            if "WinnerEngineId" in record:
                winner = {
                    "1-0": start["WhiteEngineId"],
                    "0-1": start["BlackEngineId"],
                    "1/2-1/2": None,
                }[record["Result"]]
                if record["WinnerEngineId"] != winner:
                    raise SafetyEvidenceError("malformedResults", "gameResult winner changed")
            results.append(dict(record))
            schedule_index += 1
            active = None
        else:
            raise SafetyEvidenceError("malformedResults", "unknown raw event record type")
    return {
        "payload": payload,
        "records": records,
        "candidateRuntime": candidate_runtime,
        "incumbentRuntime": incumbent_runtime,
        "starts": starts,
        "plies": plies,
        "results": results,
        "completeGames": schedule_index,
        "active": active,
        "complete": schedule_index == 2 and active is None,
    }


def _managed_rules_replay(
    context: RuntimeContext,
    paths: Mapping[str, Path],
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    raw_before = identity(paths["rawEvents"])
    requests: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    line_by_start = {
        (str(record.get("GameId")), record.get("Attempt")): index
        for index, record in enumerate(analysis["records"], 1)
        if record.get("RecordType") == "gameStart"
    }
    line_by_ply = {
        (str(record.get("GameId")), record.get("Attempt"), record.get("Ply")): index
        for index, record in enumerate(analysis["records"], 1)
        if record.get("RecordType") == "ply"
    }
    result_by_key = {
        (str(result["GameId"]), int(result["Attempt"])): result
        for result in analysis["results"]
    }
    endpoint_request_ids: dict[tuple[str, int], int] = {}
    transcript_request_ids: dict[tuple[str, int], int] = {}
    expected_endpoints: dict[tuple[str, int], str] = {}
    for key, start in analysis["starts"].items():
        sequence = analysis["plies"][key]
        successful = [
            ply
            for ply in sequence
            if ply.get("PostOfen") not in (None, "") and ply.get("Error") in (None, "")
        ]
        if successful:
            endpoint = str(successful[0]["PreOfen"])
        elif key in result_by_key:
            endpoint = str(result_by_key[key].get("FinalOfen", start["InitialOfen"]))
        else:
            # An abandoned attempt cannot supply a claimed endpoint; its
            # opening prefix is still replayed for legality, but no endpoint
            # parity assertion is made.
            endpoint = ""
        request_id = len(requests)
        endpoint_request_ids[key] = request_id
        expected_endpoints[key] = endpoint
        opening_request = {
            "schemaVersion": 1,
            "kind": "omega-opening-prefix-replay-request-v1",
            "requestId": request_id,
            "sourcePath": str(paths["rawEvents"].resolve()),
            "sourceBytes": raw_before["bytes"],
            "sourceSha256": raw_before["sha256"],
            "sourceRecord": line_by_start[key],
            "sourceObjectOrdinal": request_id + 1,
            "sourceObjectIdentity": f"/game/{key[0]}/attempt/{key[1]}/opening-prefix",
            "schema": "event-opening-moves",
            "initialSource": "explicit",
            "containerProof": "event-game-start",
            "initialOfen": str(start["InitialOfen"]),
            "moves": list(start["OpeningMoves"]),
            "expectedPositions": None,
        }
        requests.append(opening_request)
        normalized.append(
            {
                "gameId": key[0],
                "attempt": key[1],
                "kind": "opening-prefix",
                "initialOfen": opening_request["initialOfen"],
                "moves": opening_request["moves"],
                "expectedEndpoint": endpoint or None,
            }
        )
        if not successful:
            continue
        request_id = len(requests)
        transcript_request_ids[key] = request_id
        moves = [str(ply["BestMove"]) for ply in successful]
        positions = [str(ply["PostOfen"]) for ply in successful]
        request = {
            "schemaVersion": 1,
            "kind": "omega-opening-prefix-replay-request-v1",
            "requestId": request_id,
            "sourcePath": str(paths["rawEvents"].resolve()),
            "sourceBytes": raw_before["bytes"],
            "sourceSha256": raw_before["sha256"],
            "sourceRecord": line_by_ply[
                (
                    str(successful[0].get("GameId")),
                    successful[0].get("Attempt"),
                    successful[0].get("Ply"),
                )
            ],
            "sourceObjectOrdinal": request_id + 1,
            "sourceObjectIdentity": f"/game/{key[0]}/attempt/{key[1]}/successful-transcript",
            "schema": "transcript",
            "initialSource": "explicit",
            "containerProof": "transcript-game",
            "initialOfen": str(successful[0]["PreOfen"]),
            "moves": moves,
            "expectedPositions": positions,
        }
        requests.append(request)
        normalized.append(
            {
                "gameId": key[0],
                "attempt": key[1],
                "kind": "searched-transcript",
                "initialOfen": request["initialOfen"],
                "moves": moves,
                "expectedPositions": positions,
            }
        )
    if not requests:
        return {
            "contract": "frozen ChessLib legal transcript replay",
            "rawEvent": raw_before,
            "requests": 0,
            "successfulPlies": 0,
            "parityMatches": 0,
            "passes": True,
        }
    with tempfile.TemporaryDirectory(prefix="omega-g6-practical-replay-") as directory:
        root = Path(directory)
        projection = root / "requests.jsonl"
        output = root / "positions.jsonl"
        manifest = root / "manifest.json"
        projection.write_bytes(b"".join(_canonical(request) for request in requests))
        dotnet = verify_identity(context.runtime["dotnetHost"], "replay dotnet")
        helper = verify_identity(context.runtime["prefixReplayAssembly"], "replay helper")
        completed = subprocess.run(
            [
                str(dotnet),
                str(helper),
                "--input",
                str(projection),
                "--output",
                str(output),
                "--manifest",
                str(manifest),
            ],
            cwd=helper.parent,
            env=_sanitized_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=6 * 60 * 60,
            check=False,
        )
        if completed.returncode != 0 or completed.stderr:
            raise SafetyEvidenceError("malformedResults", "managed rules replay failed")
        emitted: dict[int, list[dict[str, Any]]] = {}
        for line in output.read_bytes().splitlines():
            value = json.loads(line, object_pairs_hook=_reject_duplicates)
            emitted.setdefault(value["requestId"], []).append(value)
        parity = 0
        opening_parity = 0
        for request in requests:
            rows = sorted(emitted.get(request["requestId"], []), key=lambda row: row.get("ply", -1))
            if len(rows) != len(request["moves"]) + 1:
                raise SafetyEvidenceError("illegalMoves", "managed replay position count changed")
            for ply, row in enumerate(rows):
                expected_move = None if ply == 0 else request["moves"][ply - 1]
                if (
                    row.get("kind") != "omega-opening-prefix-replay-position-v1"
                    or row.get("ply") != ply
                    or row.get("move") != expected_move
                ):
                    raise SafetyEvidenceError("illegalMoves", "managed rules replay diverged")
                if request["schema"] == "transcript":
                    expected_ofen = (
                        request["initialOfen"]
                        if ply == 0
                        else request["expectedPositions"][ply - 1]
                    )
                    if row.get("ofen") != expected_ofen:
                        raise SafetyEvidenceError("illegalMoves", "managed transcript position diverged")
                    parity += int(ply > 0)
        for key, request_id in endpoint_request_ids.items():
            rows = sorted(
                emitted.get(request_id, []), key=lambda row: row.get("ply", -1)
            )
            if not rows:
                raise SafetyEvidenceError("illegalMoves", "opening-prefix replay emitted no endpoint")
            endpoint = expected_endpoints[key]
            if endpoint:
                actual = " ".join(str(rows[-1].get("ofen", "")).split())
                if actual != " ".join(endpoint.split()):
                    raise SafetyEvidenceError("illegalMoves", "opening-prefix endpoint diverged")
                opening_parity += 1
        manifest_value = _strict_object(manifest, "managed replay manifest")
        runtime = manifest_value.get("runtime") or manifest_value.get("Runtime")
        if type(runtime) is not dict:
            raise SafetyEvidenceError("assetMismatches", "managed replay runtime is absent")
        helper_value = runtime.get("helper") or runtime.get("Helper")
        rules_value = runtime.get("rules") or runtime.get("Rules")
        if type(helper_value) is not dict or type(rules_value) is not dict:
            raise SafetyEvidenceError("assetMismatches", "managed replay identities are absent")
        if (
            helper_value.get("Sha256", helper_value.get("sha256")) != context.runtime["prefixReplayAssembly"]["sha256"]
            or rules_value.get("Sha256", rules_value.get("sha256")) != context.runtime["prefixReplayRulesAssembly"]["sha256"]
        ):
            raise SafetyEvidenceError("assetMismatches", "managed replay runtime changed")
    if identity(paths["rawEvents"]) != raw_before:
        raise ValueError("raw events changed during managed rules replay")
    searched_plies = sum(
        len(item["moves"])
        for item in normalized
        if item.get("kind") == "searched-transcript"
    )
    opening_plies = sum(
        len(item["moves"])
        for item in normalized
        if item.get("kind") == "opening-prefix"
    )
    completed_or_searched_endpoints = sum(
        bool(value) for value in expected_endpoints.values()
    )
    return {
        "contract": "frozen ChessLib legal transcript replay",
        "rawEvent": raw_before,
        "requests": len(requests),
        "openingPlies": opening_plies,
        "successfulPlies": searched_plies,
        "parityMatches": parity,
        "openingEndpointParityMatches": opening_parity,
        "normalizedTranscriptSha256": hashlib.sha256(
            b"".join(_canonical(item) for item in normalized)
        ).hexdigest(),
        "passes": (
            parity == searched_plies
            and opening_parity == completed_or_searched_endpoints
        ),
    }


RulesReplay = Callable[[RuntimeContext, Mapping[str, Path], Mapping[str, Any]], Mapping[str, Any]]


def _pair_safety(
    analysis: Mapping[str, Any], launch_records: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    safety = {name: 0 for name in SAFETY_COUNTERS}
    completed_keys = {
        (str(result["GameId"]), int(result["Attempt"])) for result in analysis["results"]
    }
    abandoned = set(analysis["starts"]).difference(completed_keys)
    safety["malformedResults"] += len(abandoned)
    for launch in launch_records:
        completion = launch["completion"]
        if completion is None:
            safety["malformedResults"] += 1
            continue
        if (
            completion.get("status")
            == "recovered-after-interrupted-adapter-process"
            or completion.get("returnCode") not in (0, None)
            or completion.get("timedOut") is True
        ):
            safety["malformedResults"] += 1
        safety["orphanProcesses"] += len(completion.get("orphanProcessesObserved", []))
    for result in analysis["results"]:
        safety["illegalMoves"] += int(result["IllegalMoves"])
        safety["engineCrashes"] += int(result["ProtocolFailures"])
        safety["timeForfeits"] += int(result["TimeForfeits"])
        safety["malformedResults"] += int(result["IllegalPvs"])
        if "timeout" in str(result.get("Termination", "")).casefold():
            safety["searchTimeouts"] += 1
    for sequence in analysis["plies"].values():
        for ply in sequence:
            search = ply.get("Search")
            if type(search) is not dict:
                continue
            safety["searchTimeouts"] += int(search.get("DeadlineExceeded") is True)
            safety["engineCrashes"] += int(search.get("ProcessExited") is True)
    return safety


def _candidate_game(context: RuntimeContext, result: Mapping[str, Any], index: int) -> dict[str, Any]:
    candidate_white = result["WhiteEngineId"] == "g6-candidate"
    assignment = "candidate-white" if candidate_white else "candidate-black"
    return {
        "gameIndex": index,
        "assignment": assignment,
        "candidateHalfPoints": _result_half_points(str(result["Result"]), candidate_white),
        "candidateEngineSha256": context.candidate_engine["sha256"],
        "candidateNetworkSha256": context.candidate_network["sha256"],
        "incumbentEngineSha256": context.incumbent_engine["sha256"],
        "incumbentNetworkSha256": context.incumbent_network["sha256"],
        "candidateLoadedNetworkSha256": context.candidate_network["sha256"],
        "incumbentLoadedNetworkSha256": context.incumbent_network["sha256"],
        "candidateNnueActive": True,
        "incumbentNnueActive": True,
        "terminal": True,
    }


def _finalize_pair(
    context: RuntimeContext,
    paths: Mapping[str, Path],
    analysis: Mapping[str, Any],
    *,
    rules_replay: RulesReplay,
) -> dict[str, Any]:
    if analysis.get("complete") is not True or len(analysis["results"]) != 2:
        raise ValueError("cannot finalize an incomplete pair")
    replay = dict(rules_replay(context, paths, analysis))
    if replay.get("passes") is not True:
        raise SafetyEvidenceError("illegalMoves", "managed rules replay did not pass")
    launches = _launch_records(paths)
    safety = _pair_safety(analysis, launches)
    entry = _strict_object(paths["intent"], "pair intent")["entry"]
    raw_completion = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RAW_COMPLETION_KIND,
        "status": "authenticated-complete-two-game-color-swapped-pair",
        "createdUtc": _utc_now(),
        "attemptIndex": context.attempt_index,
        "pairIndex": entry["pairIndex"],
        "intent": identity(paths["intent"]),
        "openingSuite": identity(paths["suite"]),
        "matchConfig": identity(paths["config"]),
        "rawEvents": identity(paths["rawEvents"]),
        "launchCompletions": [
            identity(paths["launches"] / f"{item['sequence']:06d}-completion.json")
            for item in launches
        ],
        "runtimeEngines": {
            "candidate": analysis["candidateRuntime"],
            "incumbent": analysis["incumbentRuntime"],
        },
        "rulesReplay": replay,
        "safety": safety,
        "hiddenSeedReadOrPublished": False,
        "terminal": True,
    }
    if paths["rawCompletion"].exists():
        existing = _strict_object(paths["rawCompletion"], "raw completion")
        replay_value = dict(raw_completion)
        replay_value["createdUtc"] = existing.get("createdUtc")
        if not _same_json(existing, replay_value):
            raise ValueError("raw pair completion changed")
    else:
        _exclusive_json(paths["rawCompletion"], raw_completion)
    event = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": context.successor.EVENT_KIND,
        "attemptIndex": context.attempt_index,
        "pairIndex": entry["pairIndex"],
        "openingId": entry["openingId"],
        "phase": entry["phase"],
        "sideToMove": entry["sideToMove"],
        "games": [
            _candidate_game(context, analysis["results"][0], 1),
            _candidate_game(context, analysis["results"][1], 2),
        ],
        "safety": safety,
        "terminal": True,
    }
    _verify_event(context, event, entry["pairIndex"])
    if paths["event"].exists():
        if paths["event"].read_bytes() != _canonical(event):
            raise ValueError("pair event sidecar changed")
    else:
        _exclusive_json(paths["event"], event)
    return event


def _publish_incident(
    context: RuntimeContext,
    paths: Mapping[str, Path],
    error: BaseException,
) -> dict[str, Any]:
    category = error.category if isinstance(error, SafetyEvidenceError) else "malformedResults"
    counters = {name: int(name == category) for name in SAFETY_COUNTERS}
    pair_index = _strict_object(paths["intent"], "pair intent")["pairIndex"]
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": INCIDENT_KIND,
        "status": "terminal-fail-closed-execution-incident",
        "createdUtc": _utc_now(),
        "attemptIndex": context.attempt_index,
        "pairIndex": pair_index,
        "intent": identity(paths["intent"]),
        "category": category,
        "safety": counters,
        "message": str(error),
        "rawEvents": identity(paths["rawEvents"]) if paths["rawEvents"].is_file() else None,
        "launchCompletions": [
            identity(paths["launches"] / f"{item['sequence']:06d}-completion.json")
            for item in _launch_records(paths)
            if item["completion"] is not None
        ],
        "successorEventPublished": False,
        "reasonSuccessorEventNotPublished": (
            "successor game schema requires expected loaded hashes and active NNUE; "
            "unsafe actual diagnostics may not be rewritten as successful telemetry"
        ),
        "terminal": True,
    }
    if paths["incident"].exists():
        existing = _strict_object(paths["incident"], "execution incident")
        return existing
    _exclusive_json(paths["incident"], value)
    return value


def _verify_incident_context(
    context: RuntimeContext, incident_path: Path
) -> dict[str, Any]:
    records = validate_ledger(context)
    expected_index = len(records) + 1
    paths = _pair_paths(context, expected_index)
    if incident_path.resolve() != paths["incident"].resolve():
        raise ValueError("execution incident is not at the exact next suite pair")
    value = _strict_object(incident_path, "execution incident")
    fields = {
        "schemaVersion",
        "kind",
        "status",
        "createdUtc",
        "attemptIndex",
        "pairIndex",
        "intent",
        "category",
        "safety",
        "message",
        "rawEvents",
        "launchCompletions",
        "successorEventPublished",
        "reasonSuccessorEventNotPublished",
        "terminal",
    }
    _exact_keys(value, fields, "execution incident")
    _timestamp(value.get("createdUtc"), "execution incident createdUtc")
    category = value.get("category")
    safety = value.get("safety")
    _exact_keys(safety, SAFETY_COUNTERS, "execution incident safety")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != INCIDENT_KIND
        or value.get("status") != "terminal-fail-closed-execution-incident"
        or value.get("attemptIndex") != context.attempt_index
        or value.get("pairIndex") != expected_index
        or category not in SAFETY_COUNTERS
        or safety != {name: int(name == category) for name in SAFETY_COUNTERS}
        or value.get("successorEventPublished") is not False
        or value.get("terminal") is not True
        or not _same_json(value.get("intent"), identity(paths["intent"]))
        or type(value.get("message")) is not str
        or not value["message"]
    ):
        raise ValueError("execution incident changed")
    raw = value.get("rawEvents")
    if raw is not None:
        verify_identity(raw, "incident raw events")
    launches = _launch_records(paths)
    expected_completions = [
        identity(paths["launches"] / f"{item['sequence']:06d}-completion.json")
        for item in launches
        if item["completion"] is not None
    ]
    if value.get("launchCompletions") != expected_completions:
        raise ValueError("execution incident launch chain changed")
    if paths["event"].exists() or paths["appendReceipt"].exists():
        raise ValueError("execution incident coexists with a forged successor event")
    return value


def _replay_pair_evidence(
    context: RuntimeContext,
    index: int,
    *,
    rules_replay: RulesReplay,
) -> dict[str, Any]:
    paths = _pair_paths(context, index)
    if paths["incident"].exists():
        raise ValueError(f"completed pair {index} coexists with an execution incident")
    analysis = _analyze_raw(context, paths)
    if analysis.get("complete") is not True:
        raise ValueError(f"completed pair {index} raw evidence is incomplete")
    replay = dict(rules_replay(context, paths, analysis))
    if replay.get("passes") is not True:
        raise ValueError(f"completed pair {index} managed replay failed")
    launches = _launch_records(paths)
    if not launches or any(item["completion"] is None for item in launches):
        raise ValueError(f"completed pair {index} launch chain is incomplete")
    safety = _pair_safety(analysis, launches)
    raw_completion = _strict_object(
        paths["rawCompletion"], f"pair {index} raw completion"
    )
    expected_raw = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RAW_COMPLETION_KIND,
        "status": "authenticated-complete-two-game-color-swapped-pair",
        "createdUtc": raw_completion.get("createdUtc"),
        "attemptIndex": context.attempt_index,
        "pairIndex": index,
        "intent": identity(paths["intent"]),
        "openingSuite": identity(paths["suite"]),
        "matchConfig": identity(paths["config"]),
        "rawEvents": identity(paths["rawEvents"]),
        "launchCompletions": [
            identity(paths["launches"] / f"{item['sequence']:06d}-completion.json")
            for item in launches
        ],
        "runtimeEngines": {
            "candidate": analysis["candidateRuntime"],
            "incumbent": analysis["incumbentRuntime"],
        },
        "rulesReplay": replay,
        "safety": safety,
        "hiddenSeedReadOrPublished": False,
        "terminal": True,
    }
    _timestamp(raw_completion.get("createdUtc"), f"pair {index} raw completion createdUtc")
    if not _same_json(raw_completion, expected_raw):
        raise ValueError(f"pair {index} raw completion changed")
    entry = _entry(context, index)
    expected_event = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": context.successor.EVENT_KIND,
        "attemptIndex": context.attempt_index,
        "pairIndex": index,
        "openingId": entry["openingId"],
        "phase": entry["phase"],
        "sideToMove": entry["sideToMove"],
        "games": [
            _candidate_game(context, analysis["results"][0], 1),
            _candidate_game(context, analysis["results"][1], 2),
        ],
        "safety": safety,
        "terminal": True,
    }
    actual_event = _strict_object(paths["event"], f"pair {index} event")
    if not _same_json(actual_event, expected_event):
        raise ValueError(f"pair {index} successor event differs from raw replay")
    receipt = _strict_object(paths["appendReceipt"], f"pair {index} append receipt")
    return {
        "pairIndex": index,
        "intent": identity(paths["intent"]),
        "openingSuite": identity(paths["suite"]),
        "matchConfig": identity(paths["config"]),
        "rawEvents": identity(paths["rawEvents"]),
        "rawCompletion": identity(paths["rawCompletion"]),
        "event": identity(paths["event"]),
        "appendReceipt": identity(paths["appendReceipt"]),
        "launchCompletions": expected_raw["launchCompletions"],
        "rulesReplay": replay,
        "safety": safety,
        "receiptAfter": receipt["after"],
        "terminal": True,
    }


def _execution_transcript_value(
    context: RuntimeContext,
    *,
    rules_replay: RulesReplay,
) -> dict[str, Any]:
    _verify_execution_seal(context)
    records = validate_ledger(context)
    if not records or len(records) % len(CELLS):
        raise ValueError("execution transcript requires a complete balanced checkpoint")
    pair_evidence = [
        _replay_pair_evidence(context, index, rules_replay=rules_replay)
        for index in range(1, len(records) + 1)
    ]
    safety = {
        name: sum(item["safety"][name] for item in pair_evidence)
        for name in SAFETY_COUNTERS
    }
    observations = [
        (
            record["openingId"],
            sum(game["candidateHalfPoints"] for game in record["games"]) / 4.0,
        )
        for record in records
    ]
    assessment = context.successor._sequential_practical(
        observations, attempt_index=context.attempt_index
    )
    if not any(safety.values()) and assessment.get("decision") == "continue":
        raise ValueError("nonterminal zero-safety transcript may not be sealed")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TRANSCRIPT_KIND,
        "status": "fully-replayed-terminal-practical-execution",
        "createdUtc": None,
        "adapter": identity(TOOL_PATH),
        "executionSeal": identity(context.execution_root / "00-execution-seal.json"),
        "preregistration": identity(context.preregistration_path),
        "claim": identity(context.claim_path),
        "suite": identity(context.suite_path),
        "events": identity(context.ledger_path),
        "attemptIndex": context.attempt_index,
        "pairs": len(records),
        "balancedCheckpoints": len(records) // len(CELLS),
        "pairEvidence": pair_evidence,
        "safety": safety,
        "zeroSafetyFailures": not any(safety.values()),
        "sequentialAssessment": assessment,
        "incident": None,
        "exactSuitePrefix": True,
        "allRawTranscriptsRulesReplayed": True,
        "hiddenSeedReadOrPublished": False,
        "terminal": True,
    }


def _publish_execution_transcript_with_rules_replay(
    context: RuntimeContext,
    *,
    rules_replay: RulesReplay,
) -> dict[str, Any]:
    path = context.execution_root / "06-execution-transcript.json"
    if path.exists():
        raise FileExistsError("execution transcript slot is already consumed")
    value = _execution_transcript_value(context, rules_replay=rules_replay)
    value["createdUtc"] = _utc_now()
    result = _exclusive_json(path, value)
    _verify_execution_transcript_context_with_rules_replay(
        context, path, rules_replay=rules_replay
    )
    return result


def publish_execution_transcript(context: RuntimeContext) -> dict[str, Any]:
    """Seal a production transcript using only the frozen managed replay."""

    return _publish_execution_transcript_with_rules_replay(
        context, rules_replay=_managed_rules_replay
    )


def _verify_execution_transcript_context_with_rules_replay(
    context: RuntimeContext,
    path: Path,
    *,
    rules_replay: RulesReplay,
) -> dict[str, Any]:
    expected_path = context.execution_root / "06-execution-transcript.json"
    if path.resolve() != expected_path.resolve():
        raise ValueError("execution transcript path changed")
    value = _strict_object(path, "execution transcript")
    expected = _execution_transcript_value(context, rules_replay=rules_replay)
    expected["createdUtc"] = value.get("createdUtc")
    _timestamp(value.get("createdUtc"), "execution transcript createdUtc")
    if not _same_json(value, expected):
        raise ValueError("execution transcript changed")
    return value


def verify_execution_transcript_context(
    context: RuntimeContext, path: Path
) -> dict[str, Any]:
    """Verify a production transcript using only the frozen managed replay."""

    return _verify_execution_transcript_context_with_rules_replay(
        context, path, rules_replay=_managed_rules_replay
    )


def verify_execution_transcript(
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    events: Path,
    transcript: Path,
) -> dict[str, Any]:
    """Public successor integration API; performs a full independent replay."""

    context = load_context(
        preregistration=preregistration, claim=claim, suite=suite
    )
    if events.resolve() != context.ledger_path.resolve():
        raise ValueError("successor events path is not the adapter ledger")
    return verify_execution_transcript_context(context, transcript)


def publish_incident_terminal_handoff(context: RuntimeContext) -> dict[str, Any]:
    """Seal an unrepresentable incident for successor safety-fail closure.

    The current successor cannot truthfully encode a wrong loaded-network or
    inactive-NNUE game in its success-shaped game fields.  This handoff is the
    immutable bridge the successor must authenticate when publishing a
    ``safety-fail`` practical decision and terminal global-attempt closure.
    """

    records = validate_ledger(context)
    incident_path = _pair_paths(context, len(records) + 1)["incident"]
    incident = _verify_incident_context(context, incident_path)
    path = context.execution_root / "98-incident-terminal-handoff.json"
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": INCIDENT_HANDOFF_KIND,
        "status": "authenticated-for-successor-safety-fail-closure",
        "createdUtc": _utc_now(),
        "adapter": identity(TOOL_PATH),
        "executionSeal": identity(context.execution_root / "00-execution-seal.json"),
        "preregistration": identity(context.preregistration_path),
        "claim": identity(context.claim_path),
        "suite": identity(context.suite_path),
        "eventsPrefix": identity(context.ledger_path),
        "attemptIndex": context.attempt_index,
        "completedPairs": len(records),
        "incident": identity(incident_path),
        "incidentPairIndex": incident["pairIndex"],
        "safety": incident["safety"],
        "requestedSuccessorDecision": "safety-fail",
        "requestedGlobalOutcome": "failed",
        "nextAttemptAllowed": True,
        "successorEventFabricated": False,
        "hiddenSeedReadOrPublished": False,
        "terminal": True,
    }
    _exclusive_json(path, value)
    verify_incident_terminal_handoff_context(context, path)
    return identity(path)


def verify_incident_terminal_handoff_context(
    context: RuntimeContext, path: Path
) -> dict[str, Any]:
    expected_path = context.execution_root / "98-incident-terminal-handoff.json"
    if path.resolve() != expected_path.resolve():
        raise ValueError("incident handoff path changed")
    value = _strict_object(path, "incident terminal handoff")
    records = validate_ledger(context)
    incident_path = _pair_paths(context, len(records) + 1)["incident"]
    incident = _verify_incident_context(context, incident_path)
    expected = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": INCIDENT_HANDOFF_KIND,
        "status": "authenticated-for-successor-safety-fail-closure",
        "createdUtc": value.get("createdUtc"),
        "adapter": identity(TOOL_PATH),
        "executionSeal": identity(context.execution_root / "00-execution-seal.json"),
        "preregistration": identity(context.preregistration_path),
        "claim": identity(context.claim_path),
        "suite": identity(context.suite_path),
        "eventsPrefix": identity(context.ledger_path),
        "attemptIndex": context.attempt_index,
        "completedPairs": len(records),
        "incident": identity(incident_path),
        "incidentPairIndex": incident["pairIndex"],
        "safety": incident["safety"],
        "requestedSuccessorDecision": "safety-fail",
        "requestedGlobalOutcome": "failed",
        "nextAttemptAllowed": True,
        "successorEventFabricated": False,
        "hiddenSeedReadOrPublished": False,
        "terminal": True,
    }
    _timestamp(value.get("createdUtc"), "incident handoff createdUtc")
    if not _same_json(value, expected):
        raise ValueError("incident terminal handoff changed")
    return value


def verify_incident_terminal_handoff(
    *,
    preregistration: Path,
    claim: Path,
    suite: Path,
    handoff: Path,
) -> dict[str, Any]:
    """Public successor/scanner API for terminal safety-failure replay."""

    context = load_context(
        preregistration=preregistration, claim=claim, suite=suite
    )
    return verify_incident_terminal_handoff_context(context, handoff)


def _recover_pending_launch(
    context: RuntimeContext,
    paths: Mapping[str, Path],
    snapshotter: Snapshotter,
) -> None:
    launches = _launch_records(paths)
    if not launches or launches[-1]["completion"] is not None:
        return
    pending = launches[-1]
    if _pid_set(snapshotter()):
        raise AdapterError("pending launch still has relevant live processes")
    sequence = pending["sequence"]
    intent = pending["intent"]
    raw_after = _raw_identity_or_empty(paths["rawEvents"])
    stdout_path = paths["launches"] / f"{sequence:06d}-stdout.log"
    stderr_path = paths["launches"] / f"{sequence:06d}-stderr.log"
    if not stdout_path.exists():
        _exclusive_bytes(stdout_path, b"")
    if not stderr_path.exists():
        _exclusive_bytes(stderr_path, b"")
    completion = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LAUNCH_COMPLETION_KIND,
        "status": "recovered-after-interrupted-adapter-process",
        "createdUtc": _utc_now(),
        "attemptIndex": context.attempt_index,
        "pairIndex": intent["pairIndex"],
        "sequence": sequence,
        "intent": identity(paths["launches"] / f"{sequence:06d}-intent.json"),
        "returnCode": None,
        "timedOut": False,
        "stdout": identity(stdout_path),
        "stderr": identity(stderr_path),
        "rawBefore": intent["rawBefore"],
        "rawAfter": raw_after,
        "postLaunchProcesses": dict(snapshotter()),
        "orphanProcessesObserved": [],
        "orphanProcessesSurvivingCleanup": [],
        "cleanupAttempted": False,
        "terminal": True,
    }
    _exclusive_json(paths["launches"] / f"{sequence:06d}-completion.json", completion)


def _run_next_pair_with_dependencies(
    context: RuntimeContext,
    *,
    executor: Executor,
    snapshotter: Snapshotter,
    rules_replay: RulesReplay,
) -> dict[str, Any]:
    prepare_execution(context)
    with _ledger_lock(context):
        ledger_payload, records = _reconcile_pending_append(context)
        transcript_path = context.execution_root / "06-execution-transcript.json"
        incident_handoff = context.execution_root / "98-incident-terminal-handoff.json"
        if transcript_path.exists() or incident_handoff.exists():
            return {
                "state": "execution-already-terminally-sealed",
                "transcript": identity(transcript_path) if transcript_path.exists() else None,
                "incidentHandoff": identity(incident_handoff) if incident_handoff.exists() else None,
                "pairs": len(records),
            }
        assessment = context.successor._sequential_practical(
            [
                (record["openingId"], sum(game["candidateHalfPoints"] for game in record["games"]) / 4.0)
                for record in records
            ],
            attempt_index=context.attempt_index,
        )
        safety = {
            name: sum(record["safety"][name] for record in records)
            for name in SAFETY_COUNTERS
        }
        if (
            assessment["decision"] != "continue"
            or (
                records
                and len(records) % len(CELLS) == 0
                and any(safety.values())
            )
        ):
            return {"state": "terminal-evidence-ready", "assessment": assessment, "pairs": len(records)}
        index = len(records) + 1
        if index > len(context.suite["entries"]):
            return {"state": "maximum-pairs-reached", "assessment": assessment, "pairs": len(records)}
        paths = _prepare_pair(context, index, ledger_payload)
        if paths["incident"].exists():
            return {"state": "fail-closed-incident", "incident": _strict_object(paths["incident"], "incident")}
        _recover_pending_launch(context, paths, snapshotter)
        try:
            analysis = _analyze_raw(context, paths) if paths["rawEvents"].exists() else None
            if analysis is None or not analysis["complete"]:
                _launch_once(
                    context,
                    paths,
                    executor=executor,
                    snapshotter=snapshotter,
                )
                if not paths["rawEvents"].exists():
                    return {"state": "pair-no-event-growth", "pairIndex": index}
                analysis = _analyze_raw(context, paths)
            if not analysis["complete"]:
                return {
                    "state": "pair-partial-resume-required",
                    "pairIndex": index,
                    "completeGames": analysis["completeGames"],
                    "launches": len(_launch_records(paths)),
                }
            event = _finalize_pair(
                context, paths, analysis, rules_replay=rules_replay
            )
            # The sidecar exists now; reconcile performs the one durable append.
            after_payload, after_records = _reconcile_pending_append(context)
            return {
                "state": "pair-appended",
                "pairIndex": index,
                "pairs": len(after_records),
                "ledger": _prefix_identity(after_payload),
                "event": event,
            }
        except (SafetyEvidenceError, ValueError) as error:
            incident = _publish_incident(context, paths, error)
            return {"state": "fail-closed-incident", "incident": incident}


def run_next_pair(context: RuntimeContext) -> dict[str, Any]:
    """Run one production pair with the exact authenticated dependencies."""

    return _run_next_pair_with_dependencies(
        context,
        executor=_default_executor,
        snapshotter=_process_snapshot,
        rules_replay=_managed_rules_replay,
    )


def status(context: RuntimeContext) -> dict[str, Any]:
    if not (context.execution_root / "00-execution-seal.json").is_file():
        return {
            "adapterId": ADAPTER_ID,
            "attemptIndex": context.attempt_index,
            "state": "not-started",
            "pairs": 0,
            "completeBalancedCheckpoints": 0,
            "assessment": context.successor._sequential_practical(
                [], attempt_index=context.attempt_index
            ),
            "safety": {name: 0 for name in SAFETY_COUNTERS},
            "incident": None,
            "nextAction": "run next exact eight-pair checkpoint",
            "executionPolicy": dict(EXECUTION_POLICY),
            "ledger": None,
            "hiddenSeedReadOrPublished": False,
        }
    records = validate_ledger(context)
    next_index = len(records) + 1
    incident = None
    if next_index <= len(context.suite["entries"]):
        incident_path = _pair_paths(context, next_index)["incident"]
        if incident_path.exists():
            incident = _strict_object(incident_path, "execution incident")
    observations = [
        (
            record["openingId"],
            sum(game["candidateHalfPoints"] for game in record["games"]) / 4.0,
        )
        for record in records
    ]
    assessment = context.successor._sequential_practical(
        observations, attempt_index=context.attempt_index
    )
    safety = {name: sum(record["safety"][name] for record in records) for name in SAFETY_COUNTERS}
    if incident is not None:
        for name in SAFETY_COUNTERS:
            safety[name] += incident["safety"][name]
    if incident is not None:
        handoff_path = context.execution_root / "98-incident-terminal-handoff.json"
        next_action = (
            "successor must authenticate incident handoff and close safety-fail"
            if handoff_path.exists()
            else "seal immutable incident terminal handoff"
        )
    elif any(safety.values()):
        next_action = "publish authenticated execution transcript and safety-fail decision"
    elif assessment["decision"] == "continue":
        next_action = "run next exact eight-pair checkpoint"
    else:
        next_action = "publish practical decision"
    return {
        "adapterId": ADAPTER_ID,
        "attemptIndex": context.attempt_index,
        "pairs": len(records),
        "completeBalancedCheckpoints": len(records) // len(CELLS),
        "assessment": assessment,
        "safety": safety,
        "incident": incident,
        "nextAction": next_action,
        "executionPolicy": dict(EXECUTION_POLICY),
        "ledger": identity(context.ledger_path),
        "hiddenSeedReadOrPublished": False,
    }


def _run_checkpoint_with_dependencies(
    context: RuntimeContext,
    *,
    executor: Executor,
    snapshotter: Snapshotter,
    rules_replay: RulesReplay,
) -> dict[str, Any]:
    prepare_execution(context)
    before = len(validate_ledger(context))
    target = min(
        before + (len(CELLS) - before % len(CELLS)),
        len(context.suite["entries"]),
    )
    latest: dict[str, Any] = {}
    while len(validate_ledger(context)) < target:
        latest = _run_next_pair_with_dependencies(
            context,
            executor=executor,
            snapshotter=snapshotter,
            rules_replay=rules_replay,
        )
        if latest["state"] != "pair-appended":
            return latest
    return {"state": "balanced-checkpoint-complete", "status": status(context)}


def run_checkpoint(context: RuntimeContext) -> dict[str, Any]:
    """Run one production checkpoint with no injectable launch dependencies."""

    return _run_checkpoint_with_dependencies(
        context,
        executor=_default_executor,
        snapshotter=_process_snapshot,
        rules_replay=_managed_rules_replay,
    )


def assess(context: RuntimeContext) -> dict[str, Any]:
    report = status(context)
    if report["incident"] is not None:
        raise AdapterError("cannot publish a successor decision over an unrepresentable incident")
    if report["pairs"] == 0 or report["pairs"] % len(CELLS):
        raise AdapterError("successor assessment requires a complete balanced checkpoint")
    if report["assessment"]["decision"] == "continue" and not any(
        report["safety"].values()
    ):
        raise AdapterError("practical evidence remains nonterminal")
    transcript_path = context.execution_root / "06-execution-transcript.json"
    if transcript_path.exists():
        verify_execution_transcript_context(context, transcript_path)
    else:
        publish_execution_transcript(context)
    publisher = getattr(
        context.successor,
        "publish_practical_decision_from_execution_transcript",
        None,
    )
    if not callable(publisher):
        raise AdapterError(
            "execution transcript is sealed, but successor authenticated-decision API is absent"
        )
    return publisher(
        preregistration=context.preregistration_path,
        claim=context.claim_path,
        suite=context.suite_path,
        events=context.ledger_path,
        execution_transcript=transcript_path,
    )


def handoff(context: RuntimeContext) -> dict[str, Any]:
    decision_path = (
        Path(context.preregistration["artifactRoot"])
        / "attempts"
        / f"attempt-{context.attempt_index:06d}"
        / "04-practical-decision.json"
    ).resolve()
    transcript_path = context.execution_root / "06-execution-transcript.json"
    verify_execution_transcript_context(context, transcript_path)
    verifier = getattr(
        context.successor,
        "verify_practical_decision_from_execution_transcript",
        None,
    )
    if not callable(verifier):
        raise AdapterError("successor authenticated practical-decision verifier is absent")
    decision = verifier(
        decision_path,
        preregistration=context.preregistration_path,
        claim=context.claim_path,
        suite=context.suite_path,
        execution_transcript=transcript_path,
    )
    if decision["decision"] != "promote" or decision["zeroSafetyFailures"] is not True:
        raise AdapterError("HCE handoff requires a zero-safety practical promotion")
    return context.successor.publish_hce_handoff(
        preregistration=context.preregistration_path,
        claim=context.claim_path,
        suite=context.suite_path,
        practical_decision=decision_path,
    )


def close_incident(context: RuntimeContext) -> dict[str, Any]:
    handoff_path = context.execution_root / "98-incident-terminal-handoff.json"
    if handoff_path.exists():
        verify_incident_terminal_handoff_context(context, handoff_path)
    else:
        publish_incident_terminal_handoff(context)
    publisher = getattr(context.successor, "publish_practical_incident_failure", None)
    if not callable(publisher):
        raise AdapterError(
            "incident handoff is sealed, but successor terminal safety-failure API is absent"
        )
    result = publisher(
        preregistration=context.preregistration_path,
        claim=context.claim_path,
        suite=context.suite_path,
        incident_handoff=handoff_path,
    )
    if type(result) is not dict or set(result) != {
        "practicalDecision",
        "attemptClosure",
    }:
        raise ValueError("successor incident publisher result changed")
    verifier = getattr(context.successor, "verify_practical_incident_failure", None)
    if not callable(verifier):
        raise AdapterError("successor incident failure verifier is absent")
    verifier(
        practical_decision=Path(result["practicalDecision"]["path"]),
        attempt_closure=Path(result["attemptClosure"]["path"]),
        preregistration=context.preregistration_path,
        claim=context.claim_path,
        suite=context.suite_path,
        incident_handoff=handoff_path,
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test")
    sub.add_parser("print-policy")
    sub.add_parser("print-integration-contract")
    for name in (
        "status",
        "run-checkpoint",
        "assess",
        "handoff",
        "seal-incident-handoff",
        "close-incident",
    ):
        item = sub.add_parser(name)
        item.add_argument("--preregistration", type=Path, required=True)
        item.add_argument("--claim", type=Path, required=True)
        item.add_argument("--suite", type=Path, required=True)
    return parser


def self_test() -> None:
    if EXECUTION_POLICY["mode"] != "nodes" or EXECUTION_POLICY["nodesPerMove"] != 30_000:
        raise AssertionError("practical node policy changed")
    if EXECUTION_POLICY["pairsPerLaunchCheckpoint"] != len(CELLS):
        raise AssertionError("practical checkpoint balance changed")
    if identity(V2_PROTOCOL_PATH) != V2_PROTOCOL_PIN:
        raise AssertionError("v2 protocol pin changed")
    runtime = _load_v2_runtime()
    if runtime["omegaMatchRulesAssembly"]["sha256"] != runtime["prefixReplayRulesAssembly"]["sha256"]:
        raise AssertionError("rules parity changed")
    authority = expected_successor_adapter_authority()
    if (
        authority["executionPolicy"] != dict(EXECUTION_POLICY)
        or authority["historicalAttemptScannerReplaysAdapterEvidence"] is not True
        or authority["resultInformationRead"] is not False
    ):
        raise AssertionError("successor adapter integration contract changed")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "self-test":
        self_test()
        print("Generation-6 practical match adapter self-test: PASS")
        return 0
    if args.command == "print-policy":
        print(json.dumps(EXECUTION_POLICY, indent=2, sort_keys=True))
        return 0
    if args.command == "print-integration-contract":
        value = {
            "practicalExecutionAuthority": expected_successor_adapter_authority(),
            "successorRequirements": {
                "protocolAndPreregistrationContainAuthorityExactly": True,
                "decisionMustBindExecutionTranscriptIdentity": True,
                "decisionVerifierReplaysVerifyExecutionTranscript": True,
                "incidentMustPublishSafetyFailDecisionAndFailedGlobalClosure": True,
                "incidentClosureNextAttemptAllowed": True,
                "historicalScannerReplaysDecisionOrIncidentAdapterEvidence": True,
                "arbitraryJsonlWithoutAdapterTranscriptRejected": True,
                "unknownAdapterArtifactRejected": True,
            },
        }
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    context = load_context(
        preregistration=args.preregistration,
        claim=args.claim,
        suite=args.suite,
    )
    if args.command == "status":
        value = status(context)
    elif args.command == "run-checkpoint":
        value = run_checkpoint(context)
    elif args.command == "assess":
        value = assess(context)
    elif args.command == "handoff":
        value = handoff(context)
    elif args.command == "seal-incident-handoff":
        value = publish_incident_terminal_handoff(context)
    elif args.command == "close-incident":
        value = close_incident(context)
    else:  # pragma: no cover
        raise AssertionError(args.command)
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
