#!/usr/bin/env python3
"""Ordered, resumable Generation-5 Omega NNUE match orchestrator.

The orchestrator is the only authorized OmegaMatch entry point.  It enforces
development -> equal-node -> equal-time, fixed initial/resume pair budgets,
an append-only intent/completion/assessment chain, exact event checkpoints,
fresh-process checks, and a post-equal-node idle attestation for equal time.
Every assessment is recomputed from its exact event prefix before publication;
later history verification authenticates the immutable chain and replays its tip.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
import time
from typing import Any, Mapping, Sequence

import king_state_match_protocol_generation5 as contract
import king_state_match_readiness_generation5 as readiness
import king_state_matches as core


_IMPORTED_CONTRACT = contract
_IMPORTED_READINESS = readiness
_IMPORTED_CORE = core
_CONTRACT_BINDINGS = {
    name: getattr(contract, name)
    for name in (
        "atomic_json",
        "identity",
        "install_core_profile",
        "strict_load",
        "validate_protocol",
        "verify_identity",
        "verify_module_binding",
    )
}
_READINESS_BINDINGS = {
    name: getattr(readiness, name)
    for name in (
        "_dotnet_runtime_bundle",
        "_sanitized_environment",
        "_verify_authorization",
        "_verify_runtime_authorization",
    )
}
_CORE_BINDINGS = {
    name: getattr(core, name)
    for name in (
        "_assess",
        "_harness_bundle_identity",
        "_score_for_candidate",
        "_verify_seal",
    )
}


SCHEMA_VERSION = 1
PROFILE_ID = contract.PROFILE_ID
GATES = contract.GATES
INTENT_KIND = "omega-nnue-king-state-v5-launch-intent"
COMPLETION_KIND = "omega-nnue-king-state-v5-launch-completion"
ASSESSMENT_KIND = "omega-nnue-king-state-v5-gate-assessment"
DECISION_KIND = "omega-nnue-king-state-v5-gate-decision"
IDLE_KIND = "omega-equal-time-idle-attestation-v1"

PREDECESSOR = {
    "development": None,
    "equal-node": "development",
    "equal-time": "equal-node",
}
SUCCESS = {
    "development": "pass",
    "equal-node": "promote",
    "equal-time": "promote",
}
TERMINAL = {
    "development": {"pass", "fail", "safety-fail"},
    "equal-node": {"promote", "futility", "inconclusive", "safety-fail"},
    "equal-time": {"promote", "futility", "inconclusive", "safety-fail"},
}
RELEVANT_PROCESS_NAMES = ("dotnet.exe", "omegamatch.exe", "senpai.exe")
CANONICAL_Z_TIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,7})?Z$"
)
MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
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
EVENT_REQUIRED_FIELDS = {
    kind: fields - EVENT_OPTIONAL_FIELDS[kind]
    for kind, fields in EVENT_FIELDS.items()
}


@dataclass(frozen=True)
class Context:
    protocol: dict[str, Any]
    authorization_path: Path
    authorization: dict[str, Any]
    authorization_identity: dict[str, Any]
    core_seal_path: Path
    core_seal: dict[str, Any]
    core_seal_identity: dict[str, Any]


def _verify_import_bindings(protocol: Mapping[str, Any]) -> None:
    verify_binding = _CONTRACT_BINDINGS["verify_module_binding"]
    runtime = _IMPORTED_CONTRACT.mapping(protocol.get("runtime"), "match runtime")
    identities: Mapping[str, Any] | None = None
    if _IMPORTED_CONTRACT.FINAL_PROFILE.is_file():
        profile = _IMPORTED_CONTRACT.strict_load(
            _IMPORTED_CONTRACT.FINAL_PROFILE,
            "Generation-5 final preregistration",
        )
        identities = _IMPORTED_CONTRACT.mapping(
            profile.get("finalFreezeIdentities"), "final-freeze identities"
        )
    verify_binding(
        contract,
        _IMPORTED_CONTRACT,
        _IMPORTED_CONTRACT.REPO
        / "tools/omega_nnue/king_state_match_protocol_generation5.py",
        "orchestrator match-protocol module",
        identity_record=None if identities is None else identities.get("matchProtocolTool"),
        callable_bindings=_CONTRACT_BINDINGS,
    )
    verify_binding(
        readiness,
        _IMPORTED_READINESS,
        _IMPORTED_CONTRACT.REPO
        / "tools/omega_nnue/king_state_match_readiness_generation5.py",
        "orchestrator match-readiness module",
        identity_record=(
            None if identities is None else identities.get("matchReadinessTool")
        ),
        callable_bindings=_READINESS_BINDINGS,
    )
    verify_binding(
        core,
        _IMPORTED_CORE,
        _IMPORTED_CONTRACT.REPO / "tools/omega_nnue/king_state_matches.py",
        "orchestrator shared match core",
        identity_record=runtime.get("matchCoreSource"),
        callable_bindings=_CORE_BINDINGS,
    )
    own_path = Path(__file__).resolve()
    expected_own = (
        _IMPORTED_CONTRACT.REPO
        / "tools/omega_nnue/king_state_matches_generation5.py"
    ).resolve()
    if own_path != expected_own:
        raise ValueError("Generation-5 orchestrator source path is noncanonical")
    if identities is not None and not _IMPORTED_CONTRACT.same_identity(
        identities.get("matchOrchestrator"), _IMPORTED_CONTRACT.identity(own_path)
    ):
        raise ValueError("Generation-5 orchestrator differs from its frozen pin")


def _install_core_profile(protocol: Mapping[str, Any]) -> None:
    _verify_import_bindings(protocol)
    _IMPORTED_CONTRACT.install_core_profile(protocol)


def _context(authorization: Path | None = None) -> Context:
    protocol = contract.validate_protocol()
    _install_core_profile(protocol)
    path = (
        contract.namespace(protocol, "authorization")
        if authorization is None
        else contract.resolve(authorization)
    )
    value = readiness._verify_runtime_authorization(path, protocol=protocol)
    core_path = contract.verify_identity(value.get("coreSeal"), "match core seal")
    core_value = contract.strict_load(core_path, "runtime match core seal")
    return Context(
        protocol=protocol,
        authorization_path=path,
        authorization=value,
        authorization_identity=contract.identity(path),
        core_seal_path=core_path,
        core_seal=core_value,
        core_seal_identity=contract.identity(core_path),
    )


def _gate_root(context: Context, gate: str) -> Path:
    key = {"development": "development", "equal-node": "equalNode", "equal-time": "equalTime"}[gate]
    return contract.namespace(context.protocol, key)


def _events(context: Context, gate: str) -> Path:
    return _gate_root(context, gate) / "events.jsonl"


def _launch_dir(context: Context, gate: str) -> Path:
    return _gate_root(context, gate) / "launches"


def _assessment_dir(context: Context, gate: str) -> Path:
    return _gate_root(context, gate) / "assessments"


def _decision_path(context: Context, gate: str) -> Path:
    return _gate_root(context, gate) / "decision.json"


def _idle_path(context: Context) -> Path:
    return _gate_root(context, "equal-time") / "idle-machine.attestation.json"


def _intent_path(context: Context, gate: str, sequence: int) -> Path:
    return _launch_dir(context, gate) / f"{sequence:06d}.intent.json"


def _completion_path(context: Context, gate: str, sequence: int) -> Path:
    return _launch_dir(context, gate) / f"{sequence:06d}.completion.json"


def _assessment_path(context: Context, gate: str, sequence: int) -> Path:
    return _assessment_dir(context, gate) / f"{sequence:06d}.json"


def _same_or_none(actual: Any, expected: Any, label: str) -> None:
    if expected is None:
        if actual is not None:
            raise ValueError(f"{label} must be null")
        return
    if not contract.same_identity(actual, expected):
        raise ValueError(f"{label} identity changed")
    contract.verify_identity(actual, label)


def _require_exact_int_fields(
    value: Mapping[str, Any], expected: Mapping[str, int], label: str
) -> None:
    for key, wanted in expected.items():
        actual = value.get(key)
        if type(actual) is not int or actual != wanted:
            raise ValueError(
                f"{label} {key} must be exact integer {wanted}, got {actual!r}"
            )


def _event_identity_shape(
    path: Path, record: Mapping[str, Any], label: str
) -> dict[str, Any]:
    identity = contract.mapping(record, f"{label} identity")
    if set(identity) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity fields changed")
    if contract.resolve(Path(str(identity.get("path", "")))) != contract.resolve(path):
        raise ValueError(f"{label} path changed")
    count = identity.get("bytes")
    digest = identity.get("sha256")
    if (
        type(count) is not int
        or count <= 0
        or type(digest) is not str
        or contract.HEX_256.fullmatch(digest) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(identity)


def _event_prefix(path: Path, record: Mapping[str, Any], *, exact: bool, label: str) -> bytes:
    identity = _event_identity_shape(path, record, label)
    count = identity["bytes"]
    current = contract.resolve(path).read_bytes()
    if len(current) < count or (exact and len(current) != count):
        raise ValueError(f"{label} event log length changed")
    prefix = current[:count]
    if hashlib.sha256(prefix).hexdigest() != identity.get("sha256"):
        raise ValueError(f"{label} event prefix changed")
    return prefix


def _verify_event_checkpoints(
    path: Path,
    checkpoints: Sequence[Mapping[str, Any]],
    *,
    exact_latest: bool,
    label: str,
) -> tuple[bytes, dict[str, int]]:
    """Verify every prefix hash with one file read and one streaming hash pass."""

    if not checkpoints:
        if exact_latest and contract.resolve(path).exists():
            raise ValueError(f"{label} has an event file without a sealed checkpoint")
        return b"", {"checkpoints": 0, "bytesHashed": 0, "fileReads": 0}
    normalized = [
        _event_identity_shape(path, item, f"{label} checkpoint {index}")
        for index, item in enumerate(checkpoints, 1)
    ]
    by_count: dict[int, str] = {}
    for item in normalized:
        count = item["bytes"]
        prior = by_count.setdefault(count, item["sha256"])
        if prior != item["sha256"]:
            raise ValueError(f"{label} has conflicting hashes at byte {count}")
    ordered = sorted(by_count.items())
    if any(left[0] >= right[0] for left, right in zip(ordered, ordered[1:])):
        raise ValueError(f"{label} checkpoints are not strictly increasing")
    current = contract.resolve(path).read_bytes()
    latest = ordered[-1][0]
    if len(current) < latest or (exact_latest and len(current) != latest):
        raise ValueError(f"{label} current event length differs from its checkpoint")
    prefix = current[:latest]
    digest = hashlib.sha256()
    cursor = 0
    for count, expected in ordered:
        digest.update(prefix[cursor:count])
        cursor = count
        if digest.hexdigest() != expected:
            raise ValueError(f"{label} checkpoint hash changed at byte {count}")
    return prefix, {
        "checkpoints": len(ordered),
        "bytesHashed": latest,
        "fileReads": 1,
    }


def _canonical_utc(
    value: Any,
    label: str,
    *,
    now: datetime | None = None,
) -> datetime:
    if type(value) is not str or CANONICAL_Z_TIME.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical UTC timestamp ending in Z")
    parsed = contract.parse_utc(value, label)
    current = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    if parsed > current + MAX_FUTURE_CLOCK_SKEW:
        raise ValueError(f"{label} is unreasonably far in the future")
    return parsed


def _event_exact_int(
    record: Mapping[str, Any], key: str, label: str, *, minimum: int = 0
) -> int:
    return contract.exact_int(record.get(key), f"{label} {key}", minimum=minimum)


def _event_nonempty_strings(
    record: Mapping[str, Any], keys: Sequence[str], label: str
) -> None:
    for key in keys:
        value = record.get(key)
        if type(value) is not str or not value:
            raise ValueError(f"{label} {key} must be a nonempty string")


def _event_nested_fields(
    value: Any, allowed: set[str], label: str
) -> dict[str, Any]:
    record = contract.mapping(value, label)
    extra = set(record) - allowed
    if extra:
        raise ValueError(f"{label} has unknown fields: {sorted(extra)!r}")
    return record


def _event_present_exact_types(
    record: Mapping[str, Any],
    keys: Sequence[str],
    expected_type: type,
    label: str,
) -> None:
    for key in keys:
        if key in record and type(record[key]) is not expected_type:
            raise ValueError(
                f"{label} {key} must be {expected_type.__name__}, "
                f"got {type(record[key]).__name__}"
            )


def _event_string_list(value: Any, label: str) -> None:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ValueError(f"{label} must be an array of strings")


def _validate_event_search_info(value: Any, label: str) -> None:
    info = _event_nested_fields(
        value,
        {
            "Raw",
            "Depth",
            "SelDepth",
            "MultiPv",
            "ScoreCp",
            "ScoreMate",
            "LowerBound",
            "UpperBound",
            "Nodes",
            "Nps",
            "TimeMs",
            "CurrentMove",
            "Pv",
        },
        label,
    )
    _event_present_exact_types(
        info,
        (
            "Depth",
            "SelDepth",
            "MultiPv",
            "ScoreCp",
            "ScoreMate",
            "Nodes",
            "Nps",
            "TimeMs",
        ),
        int,
        label,
    )
    _event_present_exact_types(
        info, ("LowerBound", "UpperBound"), bool, label
    )
    _event_present_exact_types(info, ("Raw", "CurrentMove"), str, label)
    if "Pv" in info:
        _event_string_list(info["Pv"], f"{label} Pv")


def _validate_event_run_payload(record: Mapping[str, Any], label: str) -> None:
    match = _event_nested_fields(
        record.get("Match"),
        {
            "EngineA",
            "EngineB",
            "OpeningsFile",
            "Repeats",
            "MaxPlies",
            "AbsoluteMaxPlies",
            "Mode",
            "Depth",
            "Nodes",
            "MoveTimeMs",
            "InitialTimeMs",
            "IncrementMs",
            "SearchTimeoutMs",
            "StopGraceMs",
            "BootstrapIterations",
            "SequentialGate",
            "FreshProcessPerGame",
        },
        f"{label} Match",
    )
    _event_present_exact_types(
        match,
        (
            "Repeats",
            "MaxPlies",
            "AbsoluteMaxPlies",
            "Depth",
            "Nodes",
            "MoveTimeMs",
            "InitialTimeMs",
            "IncrementMs",
            "SearchTimeoutMs",
            "StopGraceMs",
            "BootstrapIterations",
        ),
        int,
        f"{label} Match",
    )
    _event_present_exact_types(
        match,
        ("EngineA", "EngineB", "OpeningsFile", "Mode"),
        str,
        f"{label} Match",
    )
    _event_present_exact_types(
        match, ("FreshProcessPerGame",), bool, f"{label} Match"
    )
    if "SequentialGate" in match:
        sequential = _event_nested_fields(
            match["SequentialGate"],
            {
                "CandidateEngine",
                "MinimumPairs",
                "NullElo",
                "PromotionAlpha",
                "FutilityBeta",
            },
            f"{label} Match.SequentialGate",
        )
        _event_present_exact_types(
            sequential,
            ("CandidateEngine",),
            str,
            f"{label} Match.SequentialGate",
        )
        _event_present_exact_types(
            sequential,
            ("MinimumPairs",),
            int,
            f"{label} Match.SequentialGate",
        )
        for key in ("NullElo", "PromotionAlpha", "FutilityBeta"):
            if key in sequential and (
                type(sequential[key]) not in {int, float}
                or not math.isfinite(float(sequential[key]))
            ):
                raise ValueError(f"{label} Match.SequentialGate.{key} is malformed")

    engines = record.get("Engines")
    if type(engines) is not list:
        raise ValueError(f"{label} Engines must be an array")
    for index, item in enumerate(engines, 1):
        engine_label = f"{label} Engines[{index}]"
        engine = _event_nested_fields(
            item,
            {
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
            },
            engine_label,
        )
        _event_present_exact_types(engine, ("FileSize",), int, engine_label)
        _event_present_exact_types(
            engine, ("OmegaNnueActiveVerified",), bool, engine_label
        )
        _event_present_exact_types(
            engine,
            (
                "Id",
                "Executable",
                "Arguments",
                "WorkingDirectory",
                "Sha256",
                "LastWriteUtc",
                "UciName",
                "UciAuthor",
            ),
            str,
            engine_label,
        )
        options = engine.get("Options", {})
        if type(options) is not dict or any(
            type(key) is not str or type(item_value) is not str
            for key, item_value in options.items()
        ):
            raise ValueError(f"{engine_label} Options changed type")
        if "StartupDiagnostics" in engine:
            _event_string_list(
                engine["StartupDiagnostics"],
                f"{engine_label} StartupDiagnostics",
            )
        assets = engine.get("ExternalAssets", [])
        if type(assets) is not list:
            raise ValueError(f"{engine_label} ExternalAssets must be an array")
        for asset_index, item in enumerate(assets, 1):
            asset_label = f"{engine_label} ExternalAssets[{asset_index}]"
            asset = _event_nested_fields(
                item,
                {"OptionName", "Path", "Sha256", "FileSize", "LastWriteUtc"},
                asset_label,
            )
            _event_present_exact_types(asset, ("FileSize",), int, asset_label)
            _event_present_exact_types(
                asset,
                ("OptionName", "Path", "Sha256", "LastWriteUtc"),
                str,
                asset_label,
            )


def _validate_event_ply_payload(record: Mapping[str, Any], label: str) -> None:
    search = _event_nested_fields(
        record.get("Search"),
        {
            "Command",
            "BestMove",
            "Ponder",
            "WallTimeMs",
            "DeadlineExceeded",
            "ProcessExited",
            "ExitCode",
            "Info",
            "RawOutput",
            "StandardError",
        },
        f"{label} Search",
    )
    _event_present_exact_types(search, ("ExitCode",), int, f"{label} Search")
    _event_present_exact_types(
        search,
        ("DeadlineExceeded", "ProcessExited"),
        bool,
        f"{label} Search",
    )
    _event_present_exact_types(
        search,
        ("Command", "BestMove", "Ponder"),
        str,
        f"{label} Search",
    )
    if "WallTimeMs" in search and (
        type(search["WallTimeMs"]) not in {int, float}
        or not math.isfinite(float(search["WallTimeMs"]))
    ):
        raise ValueError(f"{label} Search.WallTimeMs is malformed")
    for key in ("RawOutput", "StandardError"):
        if key in search:
            _event_string_list(search[key], f"{label} Search.{key}")
    if "Info" in search:
        if type(search["Info"]) is not list:
            raise ValueError(f"{label} Search.Info must be an array")
        for index, item in enumerate(search["Info"], 1):
            _validate_event_search_info(item, f"{label} Search.Info[{index}]")
    if "FinalInfo" in record:
        _validate_event_search_info(record["FinalInfo"], f"{label} FinalInfo")
    pv = _event_nested_fields(
        record.get("Pv"),
        {"LegalPlies", "TotalPlies", "IllegalMove", "Error", "San", "IsLegal"},
        f"{label} Pv",
    )
    _event_present_exact_types(
        pv, ("LegalPlies", "TotalPlies"), int, f"{label} Pv"
    )
    _event_present_exact_types(pv, ("IsLegal",), bool, f"{label} Pv")
    _event_present_exact_types(
        pv, ("IllegalMove", "Error"), str, f"{label} Pv"
    )
    if "San" in pv:
        _event_string_list(pv["San"], f"{label} Pv.San")


def _validate_event_development_style(value: Any, label: str) -> None:
    style = _event_nested_fields(value, {"OpeningPlies", "White", "Black"}, label)
    _event_present_exact_types(style, ("OpeningPlies",), int, label)
    for side_name in ("White", "Black"):
        if side_name not in style:
            continue
        side_label = f"{label}.{side_name}"
        side = _event_nested_fields(
            style[side_name], {"EngineId", "Color", "Checkpoints"}, side_label
        )
        _event_present_exact_types(side, ("EngineId", "Color"), str, side_label)
        checkpoints = side.get("Checkpoints", [])
        if type(checkpoints) is not list:
            raise ValueError(f"{side_label}.Checkpoints must be an array")
        for index, item in enumerate(checkpoints, 1):
            checkpoint_label = f"{side_label}.Checkpoints[{index}]"
            checkpoint = _event_nested_fields(
                item,
                {
                    "Ply",
                    "Reached",
                    "EligiblePieces",
                    "DevelopedEligiblePieces",
                    "RepeatedNonPawnMovesBeforeBroadDevelopment",
                    "QueenMoves",
                    "Castled",
                    "NonPawnMoves",
                    "DistinctNonPawnPiecesMoved",
                    "MostMovedPieceMoves",
                    "TopPieceMoveShare",
                },
                checkpoint_label,
            )
            _event_present_exact_types(
                checkpoint,
                (
                    "Ply",
                    "EligiblePieces",
                    "DevelopedEligiblePieces",
                    "RepeatedNonPawnMovesBeforeBroadDevelopment",
                    "QueenMoves",
                    "NonPawnMoves",
                    "DistinctNonPawnPiecesMoved",
                    "MostMovedPieceMoves",
                ),
                int,
                checkpoint_label,
            )
            _event_present_exact_types(
                checkpoint, ("Reached", "Castled"), bool, checkpoint_label
            )
            if "TopPieceMoveShare" in checkpoint and (
                type(checkpoint["TopPieceMoveShare"]) not in {int, float}
                or not math.isfinite(float(checkpoint["TopPieceMoveShare"]))
            ):
                raise ValueError(
                    f"{checkpoint_label}.TopPieceMoveShare is malformed"
                )


def _strict_event_record(raw: bytes, label: str) -> dict[str, Any]:
    try:
        record = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=contract._unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token {token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from error
    if type(record) is not dict:
        raise ValueError(f"{label} is not a JSON object")
    record_type = record.get("RecordType")
    if type(record_type) is not str or record_type not in EVENT_FIELDS:
        raise ValueError(f"{label} has an unsupported RecordType")
    def reject_nonfinite(item: Any, path: str) -> None:
        if type(item) is float and not math.isfinite(item):
            raise ValueError(f"{path} is non-finite")
        if type(item) is dict:
            for key, child in item.items():
                reject_nonfinite(child, f"{path}.{key}")
        elif type(item) is list:
            for index, child in enumerate(item):
                reject_nonfinite(child, f"{path}[{index}]")
    reject_nonfinite(record, label)
    fields = set(record)
    if (
        not EVENT_REQUIRED_FIELDS[record_type].issubset(fields)
        or not fields.issubset(EVENT_FIELDS[record_type])
    ):
        raise ValueError(f"{label} field inventory changed")
    if record_type == "run":
        _canonical_utc(record.get("CreatedUtc"), f"{label} CreatedUtc")
        _event_exact_int(record, "ProcessorCount", label)
        _event_exact_int(record, "Seed", label)
        if type(record.get("Match")) is not dict or type(record.get("Engines")) is not list:
            raise ValueError(f"{label} run payload types changed")
        _validate_event_run_payload(record, label)
        _event_nonempty_strings(
            record,
            (
                "RunId",
                "ProfileId",
                "FreshnessMarker",
                "ConfigSha256",
                "OpeningSuiteSha256",
                "HarnessVersion",
                "HarnessSha256",
                "HarnessBundleSha256",
                "OperatingSystem",
                "Runtime",
            ),
            label,
        )
        if record.get("ProfileId") != PROFILE_ID:
            raise ValueError(f"{label} ProfileId is not the Generation-5 profile")
    elif record_type == "gameStart":
        _canonical_utc(record.get("StartedUtc"), f"{label} StartedUtc")
        _event_exact_int(record, "Attempt", label)
        if type(record.get("OpeningMoves")) is not list:
            raise ValueError(f"{label} OpeningMoves must be an array")
        if any(type(item) is not str for item in record["OpeningMoves"]):
            raise ValueError(f"{label} OpeningMoves contains a non-string")
        _event_nonempty_strings(
            record,
            (
                "GameId",
                "PairId",
                "OpeningId",
                "WhiteEngineId",
                "BlackEngineId",
                "InitialOfen",
            ),
            label,
        )
    elif record_type == "ply":
        for key in (
            "Attempt",
            "Ply",
            "WhiteClockBeforeMs",
            "BlackClockBeforeMs",
            "WhiteClockAfterMs",
            "BlackClockAfterMs",
        ):
            _event_exact_int(record, key, label)
        for key in ("WhiteScoreCp", "WhiteScoreMate"):
            if key in record and record[key] is not None:
                _event_exact_int(record, key, label, minimum=-(1 << 63))
        if type(record.get("Search")) is not dict or type(record.get("Pv")) is not dict:
            raise ValueError(f"{label} search/PV payload types changed")
        _validate_event_ply_payload(record, label)
        _event_nonempty_strings(
            record, ("GameId", "EngineId", "Color", "PreOfen"), label
        )
    else:
        _canonical_utc(record.get("FinishedUtc"), f"{label} FinishedUtc")
        for key in (
            "Attempt",
            "Plies",
            "WhiteClockMs",
            "BlackClockMs",
            "IllegalMoves",
            "IllegalPvs",
            "ProtocolFailures",
            "TimeForfeits",
        ):
            _event_exact_int(record, key, label)
        score = record.get("ScoreA")
        if (
            type(score) not in {int, float}
            or not 0.0 <= float(score) <= 1.0
        ):
            raise ValueError(f"{label} ScoreA is malformed")
        if "DevelopmentStyle" in record:
            _validate_event_development_style(
                record["DevelopmentStyle"], f"{label} DevelopmentStyle"
            )
        _event_nonempty_strings(
            record,
            (
                "GameId",
                "PairId",
                "OpeningId",
                "WhiteEngineId",
                "BlackEngineId",
                "Result",
                "Termination",
                "FinalOfen",
            ),
            label,
        )
    return record


def _strict_event_log(
    prefix: bytes,
    label: str,
    *,
    launch_intervals: Sequence[Mapping[str, Any]] = (),
    expected_run: Mapping[str, Any] | None = None,
    allow_incomplete_tail: bool = False,
) -> dict[str, int]:
    """Validate one complete append-only event prefix in a single linear pass."""

    if not prefix or not prefix.endswith(b"\n"):
        raise ValueError(f"{label} does not end at a complete JSONL record")
    lines = prefix.splitlines(keepends=True)
    if any(not line.endswith(b"\n") for line in lines):
        raise ValueError(f"{label} contains an incomplete JSONL record")

    run_binding: dict[str, Any] | None = None
    if expected_run is not None:
        run_binding = contract.mapping(expected_run, f"{label} expected run binding")
        if (
            set(run_binding) != {"RunId", "ProfileId", "FreshnessMarker"}
            or any(type(run_binding.get(key)) is not str or not run_binding[key] for key in run_binding)
            or run_binding.get("ProfileId") != PROFILE_ID
        ):
            raise ValueError(f"{label} expected run binding is malformed")
        run_binding = dict(run_binding)

    intervals = [dict(item) for item in launch_intervals]
    expected_before = 0
    for index, interval in enumerate(intervals, 1):
        before = interval.get("beforeBytes")
        after = interval.get("afterBytes")
        if (
            type(before) is not int
            or type(after) is not int
            or before != expected_before
            or after <= before
            or after > len(prefix)
        ):
            raise ValueError(f"{label} launch interval {index} is malformed")
        expected_before = after
    if intervals and expected_before != len(prefix):
        raise ValueError(f"{label} launch intervals do not cover the exact prefix")

    active: dict[str, Any] | None = None
    seen_attempts: set[tuple[str, int]] = set()
    last_finished: datetime | None = None
    run_time: datetime | None = None
    offset = 0
    interval_index = 0
    interval_first = True
    games = 0
    plies = 0
    for line_number, line in enumerate(lines, 1):
        raw = line[:-1].removesuffix(b"\r")
        record = _strict_event_record(raw, f"{label} line {line_number}")
        start_offset = offset
        offset += len(line)
        interval: Mapping[str, Any] | None = None
        if intervals:
            while interval_index < len(intervals) and start_offset >= intervals[
                interval_index
            ]["afterBytes"]:
                interval_index += 1
                interval_first = True
            if interval_index >= len(intervals):
                raise ValueError(f"{label} event lies outside launch intervals")
            interval = intervals[interval_index]
            if not (
                interval["beforeBytes"] <= start_offset
                and offset <= interval["afterBytes"]
            ):
                raise ValueError(f"{label} launch checkpoint splits an event record")
            if interval_first:
                expected_first = "run" if interval.get("action") == "run" else "gameStart"
                if record["RecordType"] != expected_first:
                    raise ValueError(
                        f"{label} launch {interval_index + 1} must begin with {expected_first}"
                    )
                interval_first = False

        record_type = record["RecordType"]
        event_time: datetime | None = None
        if record_type == "run":
            event_time = _canonical_utc(
                record["CreatedUtc"], f"{label} run CreatedUtc"
            )
        elif record_type == "gameStart":
            event_time = _canonical_utc(
                record["StartedUtc"], f"{label} game StartedUtc"
            )
        elif record_type == "gameResult":
            event_time = _canonical_utc(
                record["FinishedUtc"], f"{label} game FinishedUtc"
            )
        if interval is not None and event_time is not None:
            intent_time = _canonical_utc(
                interval.get("intentUtc"), f"{label} launch intentUtc"
            )
            completion_time = _canonical_utc(
                interval.get("completionUtc"), f"{label} launch completionUtc"
            )
            if event_time < intent_time or event_time > completion_time:
                raise ValueError(f"{label} event timestamp is outside its launch interval")

        if record_type == "run":
            if line_number != 1 or run_time is not None or active is not None:
                raise ValueError(f"{label} contains a noninitial run record")
            if run_binding is not None:
                contract.require_exact_json(
                    {key: record.get(key) for key in run_binding},
                    run_binding,
                    f"{label} run/config binding",
                )
            if record.get("Runtime") != f".NET {readiness.dotnet_runtime.RUNTIME_VERSION}":
                raise ValueError(f"{label} run record used an unpinned .NET runtime")
            run_time = event_time
            continue
        if run_time is None:
            raise ValueError(f"{label} game evidence precedes the run record")
        if record_type == "gameStart":
            if active is not None:
                raise ValueError(f"{label} contains overlapping game intervals")
            started = event_time
            if started is None or started < run_time:
                raise ValueError(f"{label} game starts before its run")
            if last_finished is not None and started < last_finished:
                raise ValueError(f"{label} game intervals overlap or go backward")
            game_id = record.get("GameId")
            pair_id = record.get("PairId")
            attempt = record.get("Attempt")
            if (
                type(game_id) is not str
                or not game_id
                or type(pair_id) is not str
                or not pair_id
                or (game_id, attempt) in seen_attempts
            ):
                raise ValueError(f"{label} game-start identity is malformed or repeated")
            seen_attempts.add((game_id, attempt))
            active = {
                "GameId": game_id,
                "PairId": pair_id,
                "Attempt": attempt,
                "OpeningId": record.get("OpeningId"),
                "WhiteEngineId": record.get("WhiteEngineId"),
                "BlackEngineId": record.get("BlackEngineId"),
                "StartedUtc": started,
                "lastPly": None,
            }
            continue
        if record_type == "ply":
            if active is None:
                raise ValueError(f"{label} ply appears outside a started game")
            if (
                record.get("GameId") != active["GameId"]
                or record.get("Attempt") != active["Attempt"]
            ):
                raise ValueError(f"{label} ply is bound to the wrong game attempt")
            ply = record["Ply"]
            prior_ply = active["lastPly"]
            if ply <= 0 or (prior_ply is not None and ply != prior_ply + 1):
                raise ValueError(f"{label} ply sequence is nonmonotonic")
            active["lastPly"] = ply
            plies += 1
            continue
        if active is None:
            raise ValueError(f"{label} gameResult appears before gameStart")
        for key in (
            "GameId",
            "PairId",
            "Attempt",
            "OpeningId",
            "WhiteEngineId",
            "BlackEngineId",
        ):
            if record.get(key) != active[key]:
                raise ValueError(f"{label} gameResult {key} binding changed")
        finished = event_time
        if finished is None or finished < active["StartedUtc"]:
            raise ValueError(f"{label} game finishes before it starts")
        last_finished = finished
        active = None
        games += 1

        if intervals and offset == intervals[interval_index]["afterBytes"]:
            successful = intervals[interval_index].get("successful") is True
            if successful and active is not None:
                raise ValueError(f"{label} successful launch ends mid-game")
    if active is not None and not allow_incomplete_tail:
        raise ValueError(f"{label} ends with an unfinished game interval")
    return {
        "records": len(lines),
        "games": games,
        "plies": plies,
        "bytes": len(prefix),
    }


def _strict_event_growth(
    prefix: bytes,
    before_bytes: int,
    label: str,
    *,
    expected_run: Mapping[str, Any] | None = None,
    allow_incomplete_tail: bool = False,
) -> None:
    if not prefix.endswith(b"\n"):
        raise ValueError(f"{label} does not end at a complete JSONL record")
    if before_bytes and not prefix[:before_bytes].endswith(b"\n"):
        raise ValueError(f"{label} prior checkpoint is not line-complete")
    suffix = prefix[before_bytes:]
    lines = suffix.splitlines(keepends=True)
    if not lines or any(not line.endswith(b"\n") for line in lines):
        raise ValueError(f"{label} appended JSONL is incomplete")
    _strict_event_log(
        prefix,
        label,
        expected_run=expected_run,
        allow_incomplete_tail=allow_incomplete_tail,
    )


def _validate_event_growth_boundary(
    prefix: bytes,
    intent: Mapping[str, Any],
    completion: Mapping[str, Any],
    *,
    label: str,
) -> None:
    before = intent.get("eventsBefore")
    before_bytes = 0 if before is None else int(before["bytes"])
    if before_bytes and not prefix[:before_bytes].endswith(b"\n"):
        raise ValueError(f"{label} prior event checkpoint is not line-complete")
    suffix = prefix[before_bytes:]
    newline = suffix.find(b"\n")
    if newline <= 0:
        raise ValueError(f"{label} first appended event is not a complete JSON line")
    record = _strict_event_record(
        suffix[:newline].removesuffix(b"\r"), f"{label} first appended event"
    )
    if intent.get("action") == "run":
        expected_type, timestamp_key = "run", "CreatedUtc"
    else:
        expected_type, timestamp_key = "gameStart", "StartedUtc"
    if record.get("RecordType") != expected_type:
        raise ValueError(
            f"{label} first appended event must be {expected_type!r}"
        )
    if expected_type == "run" and record.get("Runtime") != (
        f".NET {readiness.dotnet_runtime.RUNTIME_VERSION}"
    ):
        raise ValueError(f"{label} run record used an unpinned .NET runtime")
    if expected_type == "run":
        expected_run = _run_binding_from_protected(
            intent.get("protected"), f"{label} protected launch identities"
        )
        contract.require_exact_json(
            {key: record.get(key) for key in expected_run},
            expected_run,
            f"{label} first run/config binding",
        )
    event_time = _canonical_utc(
        record.get(timestamp_key), f"{label} first event {timestamp_key}"
    )
    intent_time = _canonical_utc(intent.get("createdUtc"), f"{label} intent")
    completion_time = _canonical_utc(
        completion.get("createdUtc"), f"{label} completion"
    )
    if event_time < intent_time or event_time > completion_time:
        raise ValueError(f"{label} first appended event is outside its launch interval")


def _parse_windows_processes(text: str) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        name = row[0].strip().casefold()
        if name in RELEVANT_PROCESS_NAMES:
            try:
                pid = int(row[1].replace(",", ""))
            except ValueError:
                pid = -1
            relevant.append({"name": name, "pid": pid})
    return sorted(relevant, key=lambda item: (item["name"], item["pid"]))


def _parse_posix_processes(text: str) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for line in text.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        name = Path(fields[1]).name.casefold()
        if name in RELEVANT_PROCESS_NAMES:
            relevant.append({"name": name, "pid": int(fields[0])})
    return sorted(relevant, key=lambda item: (item["name"], item["pid"]))


def _process_snapshot() -> dict[str, Any]:
    system = platform.system()
    if system == "Windows":
        completed = subprocess.run(
            ["tasklist.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
        )
        relevant = _parse_windows_processes(completed.stdout)
        method = "tasklist.exe /FO CSV /NH"
    else:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,comm="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=True,
        )
        relevant = _parse_posix_processes(completed.stdout)
        method = "ps -eo pid=,comm="
    return {
        "method": method,
        "relevantNames": list(RELEVANT_PROCESS_NAMES),
        "relevantProcesses": relevant,
    }


def _require_idle_processes(label: str) -> dict[str, Any]:
    snapshot = _process_snapshot()
    if snapshot["relevantProcesses"]:
        raise RuntimeError(
            f"{label} requires no running .NET/OmegaMatch/Senpai process: "
            f"{snapshot['relevantProcesses']}"
        )
    return snapshot


def _validate_process_snapshot(value: Any, label: str) -> dict[str, Any]:
    snapshot = contract.mapping(value, label)
    expected_method = (
        "tasklist.exe /FO CSV /NH"
        if platform.system() == "Windows"
        else "ps -eo pid=,comm="
    )
    if (
        set(snapshot) != {"method", "relevantNames", "relevantProcesses"}
        or snapshot.get("method") != expected_method
        or snapshot.get("relevantNames") != list(RELEVANT_PROCESS_NAMES)
        or type(snapshot.get("relevantProcesses")) is not list
        or any(
            type(item) is not dict
            or set(item) != {"name", "pid"}
            or item.get("name") not in RELEVANT_PROCESS_NAMES
            or type(item.get("pid")) is not int
            for item in snapshot["relevantProcesses"]
        )
        or snapshot["relevantProcesses"]
        != sorted(
            snapshot["relevantProcesses"],
            key=lambda item: (item["name"], item["pid"]),
        )
    ):
        raise ValueError(f"{label} process inventory changed")
    return snapshot


def _predecessor_evidence(
    context: Context, gate: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    predecessor = PREDECESSOR[gate]
    if predecessor is None:
        return None, None
    value = _verify_decision(context, predecessor, verify_history=False)
    if value.get("passed") is not True or value.get("decision") != SUCCESS[predecessor]:
        raise ValueError(f"{gate} is not authorized by {predecessor}")
    return value, contract.identity(_decision_path(context, predecessor))


def _predecessor_identity(context: Context, gate: str) -> dict[str, Any] | None:
    return _predecessor_evidence(context, gate)[1]


def _verify_idle(
    context: Context, *, predecessor_value: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    path = _idle_path(context)
    value = contract.strict_load(path, "equal-time idle attestation")
    expected = {
        "schemaVersion",
        "kind",
        "profileId",
        "runId",
        "createdUtc",
        "authorization",
        "equalNodeDecision",
        "idleMachine",
        "oneGameAtATime",
        "concurrentMatchProcesses",
        "processSnapshot",
        "logicalProcessors",
        "machine",
        "operator",
    }
    if set(value) != expected:
        raise ValueError("idle-attestation field inventory changed")
    run_id = context.core_seal["gates"]["equal-time"]["runId"]
    _require_exact_int_fields(
        value,
        {
            "schemaVersion": SCHEMA_VERSION,
            "concurrentMatchProcesses": 1,
            "logicalProcessors": os.cpu_count() or 1,
        },
        "idle attestation",
    )
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != IDLE_KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("runId") != run_id
        or value.get("idleMachine") is not True
        or value.get("oneGameAtATime") is not True
        or type(value.get("concurrentMatchProcesses")) is not int
        or value.get("concurrentMatchProcesses") != 1
        or not contract.exact_json_equal(
            value.get("authorization"), context.authorization_identity
        )
        or not contract.exact_json_equal(
            value.get("equalNodeDecision"),
            contract.identity(_decision_path(context, "equal-node")),
        )
        or type(value.get("logicalProcessors")) is not int
        or value.get("logicalProcessors") != (os.cpu_count() or 1)
        or value.get("machine") != (platform.node() or "unknown")
        or type(value.get("operator")) is not str
        or not value["operator"]
    ):
        raise ValueError("idle-attestation binding changed")
    created = _canonical_utc(value.get("createdUtc"), "idle createdUtc")
    predecessor = (
        _verify_decision(context, "equal-node")
        if predecessor_value is None
        else dict(predecessor_value)
    )
    if (
        predecessor.get("passed") is not True
        or predecessor.get("decision") != SUCCESS["equal-node"]
    ):
        raise ValueError("idle attestation lacks an equal-node promotion")
    if created < _canonical_utc(
        predecessor.get("createdUtc"), "equal-node decision createdUtc"
    ):
        raise ValueError("idle attestation predates equal-node promotion")
    snapshot = _validate_process_snapshot(
        value.get("processSnapshot"), "idle process snapshot"
    )
    if snapshot.get("relevantProcesses") != []:
        raise ValueError("idle attestation records relevant processes")
    return value


def _attest_idle(args: argparse.Namespace) -> None:
    context = _context(args.authorization)
    _predecessor_identity(context, "equal-time")
    path = _idle_path(context)
    if path.exists():
        _verify_idle(context)
        raise FileExistsError(f"idle attestation already exists: {path}")
    if _events(context, "equal-time").exists() or _launch_dir(
        context, "equal-time"
    ).exists():
        raise FileExistsError("equal-time evidence predates idle attestation")
    operator = str(args.operator).strip()
    if not operator:
        raise ValueError("--operator must be nonempty")
    snapshot = _require_idle_processes("equal-time attestation")
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": IDLE_KIND,
        "profileId": PROFILE_ID,
        "runId": context.core_seal["gates"]["equal-time"]["runId"],
        "createdUtc": contract.utc_now(),
        "authorization": context.authorization_identity,
        "equalNodeDecision": contract.identity(_decision_path(context, "equal-node")),
        "idleMachine": True,
        "oneGameAtATime": True,
        "concurrentMatchProcesses": 1,
        "processSnapshot": snapshot,
        "logicalProcessors": os.cpu_count() or 1,
        "machine": platform.node() or "unknown",
        "operator": operator,
    }
    contract.atomic_json(path, value, exclusive=True)
    _verify_idle(context)
    print(f"Generation-5 equal-time idle attestation: {path}")


def _config_path(context: Context, gate: str) -> Path:
    entry = contract.mapping(context.core_seal["gates"][gate], f"{gate} core gate")
    path = contract.verify_identity(entry.get("config"), f"{gate} config")
    expected = contract.namespace(context.protocol, "sealed") / f"king-state-v5-{gate}-match.json"
    if path != expected:
        raise ValueError(f"{gate} config path changed")
    return path


def _protected_identities(context: Context, gate: str) -> dict[str, Any]:
    config = contract.strict_load(_config_path(context, gate), f"{gate} config")
    suite = contract.mapping(context.core_seal["gates"][gate], f"{gate} gate")["suite"]
    if (
        type(config.get("runId")) is not str
        or not config["runId"]
        or config.get("profileId") != PROFILE_ID
        or type(config.get("freshnessMarker")) is not str
        or not config["freshnessMarker"]
    ):
        raise ValueError(f"{gate} config run identity is malformed")
    return {
        "authorization": context.authorization_identity,
        "coreSeal": context.core_seal_identity,
        "config": contract.identity(_config_path(context, gate)),
        "suite": dict(suite),
        "engine": dict(context.authorization["engine"]),
        "network": dict(context.authorization["selectedNetwork"]),
        "dotnetHost": dict(context.authorization["dotnetHost"]),
        "dotnetRuntimeManifest": dict(
            context.authorization["dotnetRuntimeManifest"]
        ),
        "matchCoreSource": dict(context.authorization["matchCoreSource"]),
        "omegaMatchAssembly": dict(context.authorization["omegaMatchAssembly"]),
        "appHost": dict(context.authorization["omegaMatchAppHost"]),
        "dotnetRuntimeBundleSha256": context.authorization[
            "dotnetRuntimeBundle"
        ]["bundleSha256"],
        "omegaMatchBundleSha256": context.authorization["omegaMatchBundle"]["sha256"],
        "runId": config["runId"],
        "profileId": config["profileId"],
        "freshnessMarker": config["freshnessMarker"],
    }


def _run_binding_from_protected(value: Any, label: str) -> dict[str, str]:
    protected = contract.mapping(value, label)
    binding = {
        "RunId": protected.get("runId"),
        "ProfileId": protected.get("profileId"),
        "FreshnessMarker": protected.get("freshnessMarker"),
    }
    if (
        any(type(item) is not str or not item for item in binding.values())
        or binding["ProfileId"] != PROFILE_ID
    ):
        raise ValueError(f"{label} run identity is malformed")
    return binding


def _verify_protected(
    context: Context,
    gate: str,
    value: Any,
    *,
    expected: Mapping[str, Any] | None = None,
) -> None:
    expected = (
        _protected_identities(context, gate)
        if expected is None
        else dict(expected)
    )
    contract.require_exact_json(
        value, expected, f"{gate} protected launch identities"
    )
    for key in (
        "authorization",
        "coreSeal",
        "config",
        "suite",
        "engine",
        "network",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "matchCoreSource",
        "omegaMatchAssembly",
        "appHost",
    ):
        contract.verify_identity(value[key], f"{gate} protected {key}")
    if not contract.exact_json_equal(
        readiness._dotnet_runtime_bundle(context.protocol),
        context.authorization["dotnetRuntimeBundle"],
    ):
        raise ValueError(".NET runtime bundle changed")
    current_bundle = core._harness_bundle_identity(
        Path(context.authorization["omegaMatchBundle"]["root"]) / "OmegaMatch.dll"
    )
    if not contract.exact_json_equal(
        current_bundle, context.authorization["omegaMatchBundle"]
    ):
        raise ValueError("OmegaMatch bundle changed")


def _intent_value(
    context: Context,
    gate: str,
    sequence: int,
    prior_assessment: dict[str, Any] | None,
    prior_completion: dict[str, Any] | None,
) -> dict[str, Any]:
    action = "run" if sequence == 1 else "resume"
    stage = context.protocol["stages"][gate]
    events_before = None if sequence == 1 else contract.identity(_events(context, gate))
    predecessor = _predecessor_identity(context, gate)
    idle = None
    if gate == "equal-time":
        _verify_idle(context)
        idle = contract.identity(_idle_path(context))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": INTENT_KIND,
        "profileId": PROFILE_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": contract.utc_now(),
        "authorization": context.authorization_identity,
        "predecessorDecision": predecessor,
        "idleAttestation": idle,
        "priorAssessment": prior_assessment,
        "priorCompletion": prior_completion,
        "eventsBefore": events_before,
        "action": action,
        "pairBudget": (
            stage["initialPairBudget"] if sequence == 1 else stage["resumePairBudget"]
        ),
        "protected": _protected_identities(context, gate),
        "preLaunchProcesses": _require_idle_processes(f"{gate} launch"),
    }


def _validate_intent(
    context: Context,
    gate: str,
    sequence: int,
    value: Mapping[str, Any],
    prior_assessment: dict[str, Any] | None,
    prior_completion: dict[str, Any] | None,
    expected_predecessor: dict[str, Any] | None,
    expected_idle: dict[str, Any] | None,
    *,
    verify_event_prefix: bool = True,
    expected_protected: Mapping[str, Any] | None = None,
    verify_protected_files: bool = True,
) -> None:
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "gate",
        "sequence",
        "createdUtc",
        "authorization",
        "predecessorDecision",
        "idleAttestation",
        "priorAssessment",
        "priorCompletion",
        "eventsBefore",
        "action",
        "pairBudget",
        "protected",
        "preLaunchProcesses",
    }
    if set(value) != expected_fields:
        raise ValueError(f"{gate} launch intent fields changed")
    action = "run" if sequence == 1 else "resume"
    stage = context.protocol["stages"][gate]
    budget = stage["initialPairBudget"] if sequence == 1 else stage["resumePairBudget"]
    _require_exact_int_fields(
        value,
        {
            "schemaVersion": SCHEMA_VERSION,
            "sequence": sequence,
            "pairBudget": budget,
        },
        f"{gate} launch intent",
    )
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != INTENT_KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("gate") != gate
        or type(value.get("sequence")) is not int
        or value.get("sequence") != sequence
        or value.get("action") != action
        or type(value.get("pairBudget")) is not int
        or value.get("pairBudget") != budget
        or not contract.exact_json_equal(
            value.get("authorization"), context.authorization_identity
        )
    ):
        raise ValueError(f"{gate} launch intent envelope changed")
    created = _canonical_utc(value.get("createdUtc"), f"{gate} intent createdUtc")
    if created < _canonical_utc(
        context.authorization.get("createdUtc"), "match authorization createdUtc"
    ):
        raise ValueError(f"{gate} launch intent predates authorization")
    _same_or_none(
        value.get("predecessorDecision"),
        expected_predecessor,
        f"{gate} predecessor",
    )
    _same_or_none(value.get("idleAttestation"), expected_idle, f"{gate} idle attestation")
    _same_or_none(value.get("priorAssessment"), prior_assessment, f"{gate} prior assessment")
    _same_or_none(value.get("priorCompletion"), prior_completion, f"{gate} prior completion")
    for record, label in (
        (value.get("predecessorDecision"), "predecessor decision"),
        (value.get("idleAttestation"), "idle attestation"),
        (value.get("priorAssessment"), "prior assessment"),
    ):
        if record is not None:
            prior_value = contract.strict_load(
                Path(str(record["path"])), f"{gate} {label}"
            )
            if created < _canonical_utc(
                prior_value.get("createdUtc"), f"{gate} {label} createdUtc"
            ):
                raise ValueError(f"{gate} launch intent predates {label}")
    processes = _validate_process_snapshot(
        value.get("preLaunchProcesses"), f"{gate} prelaunch processes"
    )
    if processes.get("relevantProcesses") != []:
        raise ValueError(f"{gate} launch was not process-idle")
    if expected_protected is None:
        _verify_protected(context, gate, value.get("protected"))
    else:
        contract.require_exact_json(
            value.get("protected"),
            expected_protected,
            f"{gate} protected intent bindings",
        )
        if verify_protected_files:
            _verify_protected(
                context,
                gate,
                value.get("protected"),
                expected=expected_protected,
            )
    if sequence == 1:
        if value.get("eventsBefore") is not None:
            raise ValueError(f"{gate} initial launch has prior events")
    else:
        prior_value = contract.strict_load(
            Path(str(prior_assessment["path"])),
            f"{gate} prior assessment value",
        )
        if not contract.exact_json_equal(
            value.get("eventsBefore"), prior_value.get("events")
        ):
            raise ValueError(f"{gate} resume is not based on latest assessment")
        _event_identity_shape(
            _events(context, gate),
            value["eventsBefore"],
            f"{gate} resume events before",
        )
        if verify_event_prefix:
            _event_prefix(
                _events(context, gate),
                value["eventsBefore"],
                exact=False,
                label=f"{gate} resume events before",
            )


def _validate_completion(
    context: Context,
    gate: str,
    sequence: int,
    value: Mapping[str, Any],
    intent: Mapping[str, Any],
    *,
    verify_event_log: bool = True,
) -> None:
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "gate",
        "sequence",
        "createdUtc",
        "intent",
        "eventsAfter",
        "returnCode",
        "recoveredFromEventGrowth",
        "postLaunchProcesses",
    }
    if set(value) != expected_fields:
        raise ValueError(f"{gate} launch completion fields changed")
    _require_exact_int_fields(
        value,
        {"schemaVersion": SCHEMA_VERSION, "sequence": sequence},
        f"{gate} launch completion",
    )
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != COMPLETION_KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("gate") != gate
        or type(value.get("sequence")) is not int
        or value.get("sequence") != sequence
        or not contract.exact_json_equal(
            value.get("intent"),
            contract.identity(_intent_path(context, gate, sequence)),
        )
        or type(value.get("returnCode")) not in {int, type(None)}
        or type(value.get("recoveredFromEventGrowth")) is not bool
    ):
        raise ValueError(f"{gate} launch completion envelope changed")
    if (
        (value["recoveredFromEventGrowth"] and value["returnCode"] is not None)
        or (
            not value["recoveredFromEventGrowth"]
            and type(value["returnCode"]) is not int
        )
    ):
        raise ValueError(f"{gate} completion recovery/return-code binding changed")
    if _canonical_utc(value.get("createdUtc"), f"{gate} completion createdUtc") < _canonical_utc(
        intent.get("createdUtc"), f"{gate} intent createdUtc"
    ):
        raise ValueError(f"{gate} completion predates intent")
    after_identity = _event_identity_shape(
        _events(context, gate),
        value.get("eventsAfter"),
        f"{gate} completion events",
    )
    before = intent.get("eventsBefore")
    before_bytes = 0
    if before is not None:
        before_identity = _event_identity_shape(
            _events(context, gate), before, f"{gate} intent prior events"
        )
        before_bytes = before_identity["bytes"]
    if after_identity["bytes"] <= before_bytes:
        raise ValueError(f"{gate} launch completion recorded no event growth")
    if verify_event_log:
        prefix = _event_prefix(
            _events(context, gate),
            after_identity,
            exact=False,
            label=f"{gate} completion events",
        )
        _strict_event_growth(
            prefix,
            before_bytes,
            f"{gate} completion events",
            expected_run=_run_binding_from_protected(
                intent.get("protected"), f"{gate} completion protected identities"
            ),
            allow_incomplete_tail=(
                value["recoveredFromEventGrowth"] is True
                or value["returnCode"] != 0
            ),
        )
        _validate_event_growth_boundary(
            prefix, intent, value, label=f"{gate} completion"
        )
    _validate_process_snapshot(
        value.get("postLaunchProcesses"), f"{gate} postlaunch processes"
    )


def _directory_entries(path: Path, suffixes: set[str]) -> dict[tuple[int, str], Path]:
    if not path.exists():
        return {}
    if not path.is_dir():
        raise ValueError(f"evidence path is not a directory: {path}")
    result: dict[tuple[int, str], Path] = {}
    for item in path.iterdir():
        parts = item.name.split(".")
        if (
            not item.is_file()
            or len(parts) != 3
            or len(parts[0]) != 6
            or not parts[0].isdigit()
            or parts[1] not in suffixes
            or parts[2] != "json"
        ):
            raise ValueError(f"unexpected evidence artifact: {item}")
        key = (int(parts[0]), parts[1])
        if key in result:
            raise ValueError(f"duplicate evidence sequence: {item}")
        result[key] = item
    return result


def _normalize_report(report: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(report))
    value.pop("createdUtc", None)
    events = contract.mapping(value.get("events"), "core report events")
    value["events"] = {"bytes": events.get("bytes"), "sha256": events.get("sha256")}
    return value


def _recompute_report(
    context: Context, gate: str, event_identity: Mapping[str, Any]
) -> dict[str, Any]:
    prefix = _event_prefix(
        _events(context, gate), event_identity, exact=False, label=f"{gate} assessment replay"
    )
    with tempfile.TemporaryDirectory(prefix="omega-g5-assess-") as directory:
        root = Path(directory)
        events = root / "events.jsonl"
        events.write_bytes(prefix)
        output = root / "report.json"
        args = argparse.Namespace(
            seal=context.core_seal_path,
            gate=gate,
            events=events,
            output=output,
            idle_attestation=(
                _idle_path(context) if gate == "equal-time" else None
            ),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            report = core._assess(args)
    return report


def _validate_assessment(
    context: Context,
    gate: str,
    sequence: int,
    value: Mapping[str, Any],
    prior_identity: dict[str, Any] | None,
    *,
    replay_core: bool = True,
    verify_event_prefix: bool = True,
) -> None:
    expected_fields = {
        "schemaVersion",
        "kind",
        "profileId",
        "gate",
        "sequence",
        "createdUtc",
        "authorization",
        "completion",
        "priorAssessment",
        "events",
        "coreReport",
        "progress",
        "decision",
        "terminal",
        "passed",
        "authorizedSuccessor",
        "processFreshness",
    }
    if set(value) != expected_fields:
        raise ValueError(f"{gate} assessment fields changed")
    _require_exact_int_fields(
        value,
        {"schemaVersion": SCHEMA_VERSION, "sequence": sequence},
        f"{gate} assessment",
    )
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != ASSESSMENT_KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("gate") != gate
        or type(value.get("sequence")) is not int
        or value.get("sequence") != sequence
        or not contract.exact_json_equal(
            value.get("authorization"), context.authorization_identity
        )
        or not contract.exact_json_equal(
            value.get("completion"),
            contract.identity(_completion_path(context, gate, sequence)),
        )
    ):
        raise ValueError(f"{gate} assessment envelope changed")
    assessment_created = _canonical_utc(
        value.get("createdUtc"), f"{gate} assessment createdUtc"
    )
    _same_or_none(value.get("priorAssessment"), prior_identity, f"{gate} assessment prior")
    event_identity = _event_identity_shape(
        _events(context, gate), value.get("events"), f"{gate} assessment events"
    )
    if verify_event_prefix:
        prefix = _event_prefix(
            _events(context, gate),
            event_identity,
            exact=False,
            label=f"{gate} assessment events",
        )
        if len(prefix) != event_identity["bytes"]:
            raise AssertionError("event prefix length changed")
    report = contract.mapping(value.get("coreReport"), f"{gate} core report")
    if not contract.exact_json_equal(report.get("events"), value.get("events")):
        raise ValueError(f"{gate} core/wrapper event identities differ")
    completion = contract.strict_load(
        _completion_path(context, gate, sequence),
        f"{gate} assessment completion",
    )
    if assessment_created < _canonical_utc(
        completion.get("createdUtc"), f"{gate} completion createdUtc"
    ):
        raise ValueError(f"{gate} assessment predates launch completion")
    if not contract.exact_json_equal(
        value.get("events"), completion.get("eventsAfter")
    ):
        raise ValueError(f"{gate} assessment is not at its launch completion")
    if replay_core:
        recomputed = _recompute_report(context, gate, event_identity)
        if not contract.exact_json_equal(
            _normalize_report(report), _normalize_report(recomputed)
        ):
            raise ValueError(f"{gate} core assessment differs from exact-prefix replay")
    if not contract.exact_json_equal(value.get("progress"), report.get("progress")):
        raise ValueError(f"{gate} assessment progress changed")
    section = report["developmentScreen"] if gate == "development" else report["sequentialGate"]
    decision = section["decision"]
    if (
        completion["postLaunchProcesses"]["relevantProcesses"]
        or completion["returnCode"] != 0
        or completion["recoveredFromEventGrowth"] is True
    ):
        decision = "safety-fail"
    terminal = decision in TERMINAL[gate]
    passed = terminal and decision == SUCCESS[gate]
    successor = GATES[GATES.index(gate) + 1] if passed and gate != "equal-time" else None
    if (
        value.get("decision") != decision
        or value.get("terminal") is not terminal
        or value.get("passed") is not passed
        or value.get("authorizedSuccessor") != successor
    ):
        raise ValueError(f"{gate} assessment decision binding changed")
    freshness = contract.mapping(value.get("processFreshness"), f"{gate} process freshness")
    intent = contract.strict_load(
        _intent_path(context, gate, sequence),
        f"{gate} assessment intent",
    )
    if (
        set(freshness)
        != {
            "freshProcessPerGameConfigured",
            "preLaunchRelevantProcesses",
            "postLaunchRelevantProcesses",
            "launcherReturnCode",
            "recoveredFromEventGrowth",
        }
        or freshness.get("freshProcessPerGameConfigured") is not True
        or not contract.exact_json_equal(
            freshness.get("preLaunchRelevantProcesses"),
            intent["preLaunchProcesses"]["relevantProcesses"],
        )
        or not contract.exact_json_equal(
            freshness.get("postLaunchRelevantProcesses"),
            completion["postLaunchProcesses"]["relevantProcesses"],
        )
        or not contract.exact_json_equal(
            freshness.get("preLaunchRelevantProcesses"), []
        )
        or not contract.exact_json_equal(
            freshness.get("launcherReturnCode"), completion["returnCode"]
        )
        or freshness.get("recoveredFromEventGrowth")
        is not completion["recoveredFromEventGrowth"]
    ):
        raise ValueError(f"{gate} process-freshness evidence changed")


def _assessment_replay_sequences(assessment_count: int) -> tuple[int, ...]:
    """Bound semantic core replay to the current immutable chain tip."""

    if type(assessment_count) is not int or assessment_count < 0:
        raise ValueError("assessment count must be a nonnegative integer")
    return () if assessment_count == 0 else (assessment_count,)


def _bounded_assessment_replays(assessment_count: int, replay: Any) -> int:
    """Invoke the semantic replay callback only for the immutable chain tip."""

    calls = 0
    for sequence in _assessment_replay_sequences(assessment_count):
        replay(sequence)
        calls += 1
    return calls


def _verify_metadata_identity_anchors(
    anchors: Sequence[tuple[Path, Any, str]],
) -> int:
    """Verify every persisted metadata link once, without reading event logs."""

    verified = 0
    for expected_path, identity, label in anchors:
        actual_path = contract.verify_identity(identity, label)
        if actual_path != contract.resolve(expected_path):
            raise ValueError(f"{label} path changed")
        verified += 1
    return verified


def _history(
    context: Context,
    gate: str,
    *,
    verify_event_chain: bool = True,
    replay_assessment_tip: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    if (
        type(verify_event_chain) is not bool
        or type(replay_assessment_tip) is not bool
    ):
        raise ValueError("history verification controls must be booleans")
    predecessor_value, expected_predecessor = _predecessor_evidence(context, gate)
    expected_idle: dict[str, Any] | None = None
    if gate == "equal-time":
        _verify_idle(context, predecessor_value=predecessor_value)
        expected_idle = contract.identity(_idle_path(context))
    launch_entries = _directory_entries(_launch_dir(context, gate), {"intent", "completion"})
    intents = sorted(sequence for sequence, kind in launch_entries if kind == "intent")
    completions = sorted(sequence for sequence, kind in launch_entries if kind == "completion")
    if intents != list(range(1, len(intents) + 1)):
        raise ValueError(f"{gate} launch intent sequence has a gap")
    if completions != list(range(1, len(completions) + 1)):
        raise ValueError(f"{gate} launch completion sequence has a gap")
    if len(intents) not in {len(completions), len(completions) + 1}:
        raise ValueError(f"{gate} launch chain is malformed")

    assessment_root = _assessment_dir(context, gate)
    assessment_paths: list[Path] = []
    if assessment_root.exists():
        if not assessment_root.is_dir():
            raise ValueError(f"{gate} assessments path is not a directory")
        assessment_paths = sorted(assessment_root.iterdir(), key=lambda item: item.name)
        expected_names = [f"{index:06d}.json" for index in range(1, len(assessment_paths) + 1)]
        if [item.name for item in assessment_paths] != expected_names or any(
            not item.is_file() for item in assessment_paths
        ):
            raise ValueError(f"{gate} assessment sequence has a gap or extra artifact")
    if len(assessment_paths) > len(completions) or len(completions) > len(assessment_paths) + 1:
        raise ValueError(f"{gate} launch/assessment chain is imbalanced")

    completed: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    launch_intervals: list[dict[str, Any]] = []
    metadata_anchors: list[tuple[Path, Any, str]] = []
    pending: dict[str, Any] | None = None
    prior_assessment_identity: dict[str, Any] | None = None
    prior_completion_identity: dict[str, Any] | None = None
    expected_protected = _protected_identities(context, gate)
    _verify_protected(
        context,
        gate,
        expected_protected,
        expected=expected_protected,
    )
    for sequence in intents:
        intent_path = launch_entries[(sequence, "intent")]
        intent = contract.strict_load(intent_path, f"{gate} launch intent {sequence}")
        _validate_intent(
            context,
            gate,
            sequence,
            intent,
            prior_assessment_identity,
            prior_completion_identity,
            expected_predecessor,
            expected_idle,
            verify_event_prefix=False,
            expected_protected=expected_protected,
            verify_protected_files=False,
        )
        if sequence > 1:
            metadata_anchors.extend(
                (
                    (
                        _assessment_path(context, gate, sequence - 1),
                        intent.get("priorAssessment"),
                        f"{gate} launch intent {sequence} prior assessment",
                    ),
                    (
                        _completion_path(context, gate, sequence - 1),
                        intent.get("priorCompletion"),
                        f"{gate} launch intent {sequence} prior completion",
                    ),
                )
            )
        completion_path = launch_entries.get((sequence, "completion"))
        if completion_path is None:
            pending = {"path": intent_path, "value": intent}
            break
        completion = contract.strict_load(completion_path, f"{gate} completion {sequence}")
        _validate_completion(
            context,
            gate,
            sequence,
            completion,
            intent,
            verify_event_log=False,
        )
        metadata_anchors.append(
            (
                intent_path,
                completion.get("intent"),
                f"{gate} completion {sequence} intent",
            )
        )
        completed.append({"intent": intent, "value": completion})
        after = contract.mapping(
            completion.get("eventsAfter"), f"{gate} completion {sequence} events"
        )
        before = intent.get("eventsBefore")
        before_bytes = 0 if before is None else contract.exact_int(
            contract.mapping(before, f"{gate} intent {sequence} eventsBefore").get(
                "bytes"
            ),
            f"{gate} intent {sequence} eventsBefore bytes",
            minimum=1,
        )
        after_bytes = contract.exact_int(
            after.get("bytes"),
            f"{gate} completion {sequence} eventsAfter bytes",
            minimum=1,
        )
        checkpoints.append(dict(after))
        launch_intervals.append(
            {
                "beforeBytes": before_bytes,
                "afterBytes": after_bytes,
                "action": intent["action"],
                "intentUtc": intent["createdUtc"],
                "completionUtc": completion["createdUtc"],
                "successful": (
                    completion["returnCode"] == 0
                    and completion["recoveredFromEventGrowth"] is False
                ),
            }
        )
        prior_completion_identity = contract.identity(completion_path)
        if sequence <= len(assessment_paths):
            assessment_path = assessment_paths[sequence - 1]
            assessment = contract.strict_load(assessment_path, f"{gate} assessment {sequence}")
            _validate_assessment(
                context,
                gate,
                sequence,
                assessment,
                prior_assessment_identity,
                replay_core=False,
                verify_event_prefix=False,
            )
            metadata_anchors.append(
                (
                    completion_path,
                    assessment.get("completion"),
                    f"{gate} assessment {sequence} completion",
                )
            )
            if sequence > 1:
                metadata_anchors.append(
                    (
                        _assessment_path(context, gate, sequence - 1),
                        assessment.get("priorAssessment"),
                        f"{gate} assessment {sequence} prior assessment",
                    )
                )
            assessments.append(assessment)
            checkpoints.append(dict(assessment["events"]))
            prior_assessment_identity = contract.identity(assessment_path)
    _verify_metadata_identity_anchors(metadata_anchors)
    if verify_event_chain and checkpoints:
        prefix, _ = _verify_event_checkpoints(
            _events(context, gate),
            checkpoints,
            exact_latest=pending is None,
            label=f"{gate} append-only history",
        )
        last_completion = completed[-1]["value"]
        _strict_event_log(
            prefix,
            f"{gate} append-only history",
            launch_intervals=launch_intervals,
            expected_run=_run_binding_from_protected(
                expected_protected, f"{gate} protected history identities"
            ),
            allow_incomplete_tail=(
                last_completion["returnCode"] != 0
                or last_completion["recoveredFromEventGrowth"] is True
            ),
        )
    elif verify_event_chain and pending is None and _events(context, gate).exists():
        raise ValueError(f"{gate} has events without a sealed completion")

    def replay_tip(replay_sequence: int) -> None:
        # Only the latest immutable assessment needs an exact core replay.  All
        # predecessors are protected by the identity chain and the single-pass
        # event checkpoint verification above.
        _validate_assessment(
            context,
            gate,
            replay_sequence,
            assessments[replay_sequence - 1],
            None
            if replay_sequence == 1
            else contract.identity(
                _assessment_path(context, gate, replay_sequence - 1)
            ),
            replay_core=True,
            verify_event_prefix=False,
        )

    if replay_assessment_tip:
        _bounded_assessment_replays(len(assessments), replay_tip)
    return completed, assessments, pending


def _decision_value(context: Context, gate: str, assessment: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": DECISION_KIND,
        "profileId": PROFILE_ID,
        "gate": gate,
        "createdUtc": contract.utc_now(),
        "authorization": context.authorization_identity,
        "assessment": contract.identity(
            _assessment_path(context, gate, int(assessment["sequence"]))
        ),
        "events": dict(assessment["events"]),
        "decision": assessment["decision"],
        "passed": assessment["passed"],
        "authorizedSuccessor": assessment["authorizedSuccessor"],
        "retryAllowed": False,
    }


def _verify_decision(
    context: Context, gate: str, *, verify_history: bool = True
) -> dict[str, Any]:
    path = _decision_path(context, gate)
    value = contract.strict_load(path, f"{gate} terminal decision")
    expected = {
        "schemaVersion",
        "kind",
        "profileId",
        "gate",
        "createdUtc",
        "authorization",
        "assessment",
        "events",
        "decision",
        "passed",
        "authorizedSuccessor",
        "retryAllowed",
    }
    if set(value) != expected:
        raise ValueError(f"{gate} decision fields changed")
    _require_exact_int_fields(
        value, {"schemaVersion": SCHEMA_VERSION}, f"{gate} decision"
    )
    if (
        type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != DECISION_KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("gate") != gate
        or not contract.exact_json_equal(
            value.get("authorization"), context.authorization_identity
        )
        or value.get("retryAllowed") is not False
    ):
        raise ValueError(f"{gate} decision envelope changed")
    decision_created = _canonical_utc(
        value.get("createdUtc"), f"{gate} decision createdUtc"
    )
    assessment_path = contract.verify_identity(value.get("assessment"), f"{gate} decision assessment")
    if assessment_path.parent != _assessment_dir(context, gate):
        raise ValueError(f"{gate} decision assessment path changed")
    sequence = int(assessment_path.stem)
    if verify_history:
        completed, assessments, pending = _history(context, gate)
        if (
            pending is not None
            or len(completed) != len(assessments)
            or not assessments
            or sequence != len(assessments)
            or assessment_path != _assessment_path(context, gate, len(assessments))
        ):
            raise ValueError(f"{gate} decision is not bound to the complete launch history")
        assessment = assessments[-1]
    else:
        # Successor authorization still walks and validates every immutable
        # predecessor intent/completion/assessment.  It deliberately skips
        # the event semantic scan and expensive core replay; the terminal
        # event checkpoint remains verified exactly below.
        completed, assessments, pending = _history(
            context,
            gate,
            verify_event_chain=False,
            replay_assessment_tip=False,
        )
        if (
            pending is not None
            or len(completed) != len(assessments)
            or not assessments
            or sequence != len(assessments)
            or assessment_path
            != _assessment_path(context, gate, len(assessments))
        ):
            raise ValueError(
                f"{gate} decision is not bound to the complete metadata history"
            )
        assessment = assessments[-1]
    if decision_created < _canonical_utc(
        assessment.get("createdUtc"), f"{gate} assessment createdUtc"
    ):
        raise ValueError(f"{gate} decision predates terminal assessment")
    if assessment.get("terminal") is not True or not contract.exact_json_equal(
        value.get("events"), assessment.get("events")
    ):
        raise ValueError(f"{gate} decision is not bound to a terminal assessment")
    _event_prefix(
        _events(context, gate),
        assessment["events"],
        exact=True,
        label=f"{gate} terminal decision events",
    )
    expected_value = _decision_value(context, gate, assessment)
    expected_value["createdUtc"] = value["createdUtc"]
    if not contract.exact_json_equal(value, expected_value):
        raise ValueError(f"{gate} terminal decision changed")
    return value


def _events_grew(context: Context, gate: str, intent: Mapping[str, Any]) -> bool:
    path = _events(context, gate)
    if not path.is_file():
        return False
    before = intent.get("eventsBefore")
    if before is None:
        return path.stat().st_size > 0
    _event_prefix(path, before, exact=False, label=f"{gate} pending launch")
    return path.stat().st_size > int(before["bytes"])


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
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": COMPLETION_KIND,
        "profileId": PROFILE_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": contract.utc_now(),
        "intent": contract.identity(_intent_path(context, gate, sequence)),
        "eventsAfter": contract.identity(_events(context, gate)),
        "returnCode": return_code,
        "recoveredFromEventGrowth": recovered,
        "postLaunchProcesses": _process_snapshot(),
    }
    path = _completion_path(context, gate, sequence)
    contract.atomic_json(path, value, exclusive=True)
    _validate_completion(context, gate, sequence, value, intent)
    return value


def _launch(args: argparse.Namespace) -> None:
    gate = str(args.gate)
    context = _context(args.authorization)
    if _decision_path(context, gate).exists():
        _verify_decision(context, gate)
        raise FileExistsError(f"{gate} already has a terminal decision")
    completed, assessments, pending = _history(context, gate)
    if assessments and assessments[-1]["terminal"] is True:
        raise FileExistsError(f"{gate} already has a terminal assessment")
    if len(completed) > len(assessments):
        raise FileExistsError(f"{gate} has an unassessed launch completion")
    if len(completed) != len(assessments):
        raise ValueError(f"{gate} launch/assessment evidence is inconsistent")
    action = "run" if not assessments else "resume"
    if args.action is not None and args.action != action:
        raise ValueError(f"{gate} next action is {action}, not {args.action}")
    sequence = len(assessments) + 1
    if action == "run" and _events(context, gate).exists():
        raise FileExistsError(f"{gate} initial launch requires absent events")
    if action == "resume":
        _event_prefix(
            _events(context, gate),
            assessments[-1]["events"],
            exact=True,
            label=f"{gate} exact resume checkpoint",
        )
    if pending is not None:
        intent = pending["value"]
        if _events_grew(context, gate, intent):
            _require_idle_processes(f"{gate} pending launch recovery")
            _publish_completion(
                context,
                gate,
                sequence,
                intent,
                return_code=None,
                recovered=True,
            )
            print(f"Recovered {gate} launch completion {sequence}; assess next")
            return
        raise RuntimeError(
            f"{gate} has a pending intent with no event growth; automatic "
            "re-execution cannot distinguish an unstarted launch from an "
            "unrecorded pre-write failure and is refused"
        )
    else:
        prior_assessment = (
            None if not assessments else contract.identity(_assessment_path(context, gate, sequence - 1))
        )
        prior_completion = (
            None if not completed else contract.identity(_completion_path(context, gate, sequence - 1))
        )
        intent = _intent_value(
            context, gate, sequence, prior_assessment, prior_completion
        )

    config_path = _config_path(context, gate)
    dotnet = Path(context.authorization["dotnetHost"]["path"])
    assembly = Path(context.authorization["omegaMatchAssembly"]["path"])
    validate_command = [
        str(dotnet),
        str(assembly),
        "validate",
        "--config",
        str(config_path),
    ]
    subprocess.run(
        validate_command,
        cwd=assembly.parent,
        env=readiness._sanitized_environment(),
        check=True,
    )
    current_idle = _require_idle_processes(f"{gate} immediate prelaunch")
    if pending is None:
        intent["preLaunchProcesses"] = current_idle
        path = _intent_path(context, gate, sequence)
        contract.atomic_json(path, intent, exclusive=True)
        _validate_intent(
            context,
            gate,
            sequence,
            intent,
            None if not assessments else contract.identity(_assessment_path(context, gate, sequence - 1)),
            None if not completed else contract.identity(_completion_path(context, gate, sequence - 1)),
            intent["predecessorDecision"],
            intent["idleAttestation"],
        )
    _verify_protected(context, gate, intent["protected"])
    command = [
        str(dotnet),
        str(assembly),
        action,
        "--config",
        str(config_path),
        "--pair-budget",
        str(intent["pairBudget"]),
    ]
    result = subprocess.run(
        command,
        cwd=assembly.parent,
        env=readiness._sanitized_environment(),
        check=False,
    )
    _verify_protected(context, gate, intent["protected"])
    if not _events_grew(context, gate, intent):
        raise RuntimeError(
            f"{gate} OmegaMatch exited {result.returncode} without event growth; "
            "the immutable intent remains pending for exact recovery"
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
        raise RuntimeError(
            f"{gate} left relevant processes running; completion preserved for safety assessment"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"{gate} OmegaMatch exited {result.returncode}; completion preserved for assessment"
        )
    print(
        f"Generation-5 {gate} {action} completed with fixed pair budget "
        f"{intent['pairBudget']}; assess next"
    )


def _budget_check(
    context: Context,
    gate: str,
    report: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    sequence: int,
) -> None:
    progress = contract.mapping(report.get("progress"), f"{gate} progress")
    stage = context.protocol["stages"][gate]
    budget = stage["initialPairBudget"] if sequence == 1 else stage["resumePairBudget"]
    prior_progress = (
        {"finishedGames": 0, "completePairs": 0, "balancedPrefixPairs": 0}
        if prior is None
        else prior["progress"]
    )
    for field, maximum in {
        "finishedGames": budget * 2,
        "completePairs": budget,
        "balancedPrefixPairs": budget,
    }.items():
        current = progress.get(field)
        before = prior_progress.get(field)
        if type(current) is not int or type(before) is not int:
            raise ValueError(f"{gate} {field} is not an integer")
        if current < before or current - before > maximum:
            raise ValueError(f"{gate} {field} exceeds launch budget bound")
    if progress["completePairs"] > stage["maximumPairs"]:
        raise ValueError(f"{gate} exceeded frozen maximum pairs")


def _assess(args: argparse.Namespace) -> dict[str, Any]:
    gate = str(args.gate)
    context = _context(args.authorization)
    if _decision_path(context, gate).exists():
        value = _verify_decision(context, gate)
        raise FileExistsError(
            f"{gate} already has terminal decision {value['decision']}"
        )
    completed, assessments, pending = _history(context, gate)
    if pending is not None:
        raise FileExistsError(f"{gate} has a pending launch intent")
    if (
        assessments
        and assessments[-1]["terminal"] is True
        and len(completed) == len(assessments)
    ):
        decision_value = _decision_value(context, gate, assessments[-1])
        contract.atomic_json(
            _decision_path(context, gate), decision_value, exclusive=True
        )
        _verify_decision(context, gate)
        print(
            f"Generation-5 {gate} terminal decision recovered: "
            f"{assessments[-1]['decision']}"
        )
        return assessments[-1]
    if len(completed) != len(assessments) + 1:
        raise ValueError(f"{gate} assessment requires exactly one new completion")
    sequence = len(assessments) + 1
    completion = completed[-1]["value"]
    current_events = contract.identity(_events(context, gate))
    if not contract.exact_json_equal(completion["eventsAfter"], current_events):
        raise ValueError(f"{gate} events changed after launch completion")
    report = _recompute_report(context, gate, current_events)
    report["events"] = current_events
    prior = assessments[-1] if assessments else None
    _budget_check(context, gate, report, prior, sequence)
    section = report["developmentScreen"] if gate == "development" else report["sequentialGate"]
    decision = str(section["decision"])
    if (
        completion["postLaunchProcesses"]["relevantProcesses"]
        or completion["returnCode"] != 0
        or completion["recoveredFromEventGrowth"] is True
    ):
        decision = "safety-fail"
    terminal = decision in TERMINAL[gate]
    passed = terminal and decision == SUCCESS[gate]
    successor = GATES[GATES.index(gate) + 1] if passed and gate != "equal-time" else None
    config = contract.strict_load(_config_path(context, gate), f"{gate} config")
    freshness = {
        "freshProcessPerGameConfigured": config["match"]["freshProcessPerGame"] is True,
        "preLaunchRelevantProcesses": completed[-1]["intent"]["preLaunchProcesses"]["relevantProcesses"],
        "postLaunchRelevantProcesses": completion["postLaunchProcesses"]["relevantProcesses"],
        "launcherReturnCode": completion["returnCode"],
        "recoveredFromEventGrowth": completion["recoveredFromEventGrowth"],
    }
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ASSESSMENT_KIND,
        "profileId": PROFILE_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": contract.utc_now(),
        "authorization": context.authorization_identity,
        "completion": contract.identity(_completion_path(context, gate, sequence)),
        "priorAssessment": (
            None if not assessments else contract.identity(_assessment_path(context, gate, sequence - 1))
        ),
        "events": current_events,
        "coreReport": report,
        "progress": copy.deepcopy(report["progress"]),
        "decision": decision,
        "terminal": terminal,
        "passed": passed,
        "authorizedSuccessor": successor,
        "processFreshness": freshness,
    }
    path = _assessment_path(context, gate, sequence)
    contract.atomic_json(path, value, exclusive=True)
    _validate_assessment(
        context,
        gate,
        sequence,
        value,
        None if sequence == 1 else contract.identity(_assessment_path(context, gate, sequence - 1)),
    )
    if terminal:
        decision_value = _decision_value(context, gate, value)
        contract.atomic_json(_decision_path(context, gate), decision_value, exclusive=True)
        _verify_decision(context, gate)
    print(f"Generation-5 {gate} assessment {sequence}: {decision}")
    return value


def _verify_state(args: argparse.Namespace) -> None:
    context = _context(args.authorization)
    for gate in GATES:
        predecessor = PREDECESSOR[gate]
        root = _gate_root(context, gate)
        if not root.exists():
            if any(_gate_root(context, later).exists() for later in GATES[GATES.index(gate) + 1 :]):
                raise ValueError(f"later match evidence exists before {gate}")
            continue
        completed, assessments, pending = _history(context, gate)
        if len(completed) < len(assessments) or len(completed) > len(assessments) + 1:
            raise ValueError(f"{gate} state chain is imbalanced")
        if pending is None:
            if completed:
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
            elif _events(context, gate).exists():
                raise FileExistsError(f"{gate} has events without a launch intent")
        decision_path = _decision_path(context, gate)
        if decision_path.exists():
            decision = _verify_decision(context, gate)
            if not assessments or assessments[-1]["terminal"] is not True:
                raise ValueError(f"{gate} decision lacks terminal assessment")
            print(f"{gate}: terminal {decision['decision']}")
        elif assessments and assessments[-1]["terminal"] is True:
            raise ValueError(f"{gate} terminal assessment lacks decision")
        elif pending is not None:
            print(f"{gate}: pending launch intent")
        elif len(completed) > len(assessments):
            print(f"{gate}: completion awaiting assessment")
        elif assessments:
            print(f"{gate}: {assessments[-1]['decision']} at {assessments[-1]['progress']['completePairs']} pairs")
        else:
            print(f"{gate}: not launched")


def _self_test() -> None:
    protocol = contract.validate_protocol()
    _install_core_profile(protocol)
    if set(TERMINAL["equal-time"]) != {"promote", "futility", "inconclusive", "safety-fail"}:
        raise AssertionError("equal-time terminal inventory changed")
    if protocol["stages"]["equal-node"]["initialPairBudget"] != 128:
        raise AssertionError("initial pair budget changed")
    if protocol["stages"]["equal-node"]["resumePairBudget"] != 4:
        raise AssertionError("resume pair budget changed")
    envelope_type_cases = {
        "intent": ({"schemaVersion": 1, "sequence": 1, "pairBudget": 64}, {"schemaVersion": 1, "sequence": 1, "pairBudget": 64}),
        "completion": ({"schemaVersion": 1, "sequence": 1}, {"schemaVersion": 1, "sequence": 1}),
        "assessment": ({"schemaVersion": 1, "sequence": 1}, {"schemaVersion": 1, "sequence": 1}),
        "decision": ({"schemaVersion": 1}, {"schemaVersion": 1}),
    }
    for envelope, (value, expected) in envelope_type_cases.items():
        _require_exact_int_fields(value, expected, f"synthetic {envelope}")
        for field in expected:
            for replacement in (float(expected[field]), True):
                changed = dict(value)
                changed[field] = replacement
                try:
                    _require_exact_int_fields(
                        changed,
                        expected,
                        f"synthetic {envelope} {field} substitution",
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError(
                        f"{envelope}.{field} accepted integer substitution "
                        f"{replacement!r}"
                    )
    with tempfile.TemporaryDirectory(prefix="omega-g5-match-state-") as directory:
        root = Path(directory)
        events = root / "events.jsonl"
        events.write_bytes(b'{"RecordType":"run"}\n')
        first = contract.identity(events)
        with events.open("ab") as stream:
            stream.write(b'{"RecordType":"gameStart"}\n')
        _event_prefix(events, first, exact=False, label="synthetic append-only")
        try:
            _event_prefix(events, first, exact=True, label="synthetic exact")
        except ValueError:
            pass
        else:
            raise AssertionError("resume accepted event growth past checkpoint")
        payload = bytearray(events.read_bytes())
        payload[0] ^= 1
        events.write_bytes(bytes(payload))
        try:
            _event_prefix(events, first, exact=False, label="synthetic mutation")
        except ValueError:
            pass
        else:
            raise AssertionError("event-prefix mutation was accepted")
    with tempfile.TemporaryDirectory(prefix="omega-g5-metadata-chain-") as directory:
        root = Path(directory)
        metadata_paths = [root / f"{index:06d}.json" for index in range(1, 4)]
        for index, path in enumerate(metadata_paths, 1):
            contract.atomic_json(
                path,
                {"schemaVersion": 1, "sequence": index},
                exclusive=True,
            )
        metadata_anchors = [
            (path, contract.identity(path), f"synthetic metadata {index}")
            for index, path in enumerate(metadata_paths, 1)
        ]
        if _verify_metadata_identity_anchors(metadata_anchors) != 3:
            raise AssertionError("linear metadata-chain traversal count changed")
        # Mutate sequence 1 while sequence 3 is the chain tip.  A verifier
        # that checks only the immediate predecessor (sequence 2) misses it.
        metadata_paths[0].write_bytes(b'{"schemaVersion":1,"sequence":101}\n')
        try:
            _verify_metadata_identity_anchors(metadata_anchors)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "older-than-immediate metadata mutation was accepted"
            )
    def zulu(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def event_line(value: Mapping[str, Any]) -> bytes:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")

    def synthetic_run(created: datetime) -> dict[str, Any]:
        return {
            "RecordType": "run",
            "RunId": "synthetic-run",
            "ProfileId": PROFILE_ID,
            "FreshnessMarker": "synthetic-fresh",
            "CreatedUtc": zulu(created),
            "ConfigSha256": "1" * 64,
            "OpeningSuiteSha256": "2" * 64,
            "HarnessVersion": "synthetic",
            "HarnessSha256": "3" * 64,
            "HarnessBundleSha256": "4" * 64,
            "OperatingSystem": "synthetic",
            "Runtime": f".NET {readiness.dotnet_runtime.RUNTIME_VERSION}",
            "ProcessorCount": 1,
            "Seed": 2026072308,
            "Match": {},
            "Engines": [],
        }

    def synthetic_start(index: int, started: datetime) -> dict[str, Any]:
        return {
            "RecordType": "gameStart",
            "GameId": f"game-{index:04d}",
            "PairId": f"pair-{(index + 1) // 2:04d}",
            "Attempt": 1,
            "StartedUtc": zulu(started),
            "OpeningId": f"opening-{(index + 1) // 2:04d}",
            "WhiteEngineId": "nnue-candidate",
            "BlackEngineId": "hce-control",
            "InitialOfen": "synthetic-ofen",
            "OpeningMoves": [],
        }

    def synthetic_result(index: int, finished: datetime) -> dict[str, Any]:
        start = synthetic_start(index, finished)
        return {
            "RecordType": "gameResult",
            "GameId": start["GameId"],
            "PairId": start["PairId"],
            "Attempt": 1,
            "FinishedUtc": zulu(finished),
            "OpeningId": start["OpeningId"],
            "WhiteEngineId": "nnue-candidate",
            "BlackEngineId": "hce-control",
            "Result": "1/2-1/2",
            "Termination": "synthetic",
            "ScoreA": 0.5,
            "Plies": 0,
            "FinalOfen": "synthetic-ofen",
            "WhiteClockMs": 1,
            "BlackClockMs": 1,
            "IllegalMoves": 0,
            "IllegalPvs": 0,
            "ProtocolFailures": 0,
            "TimeForfeits": 0,
        }

    base_time = datetime(2026, 7, 22, 1, 0, 1, tzinfo=timezone.utc)
    synthetic_run_binding = {
        "RunId": "synthetic-run",
        "ProfileId": PROFILE_ID,
        "FreshnessMarker": "synthetic-fresh",
    }
    one_start = synthetic_start(1, base_time + timedelta(seconds=1))
    one_result = synthetic_result(1, base_time + timedelta(seconds=2))
    strict_payload = b"".join(
        (
            event_line(synthetic_run(base_time)),
            event_line(one_start),
            event_line(one_result),
        )
    )
    _strict_event_growth(
        strict_payload,
        0,
        "synthetic strict events",
        expected_run=synthetic_run_binding,
    )
    _validate_event_growth_boundary(
        strict_payload,
        {
            "eventsBefore": None,
            "action": "run",
            "createdUtc": "2026-07-22T01:00:00Z",
            "protected": {
                "runId": synthetic_run_binding["RunId"],
                "profileId": synthetic_run_binding["ProfileId"],
                "freshnessMarker": synthetic_run_binding["FreshnessMarker"],
            },
        },
        {"createdUtc": "2026-07-22T01:00:04Z"},
        label="synthetic boundary",
    )
    for field in ("RunId", "ProfileId", "FreshnessMarker"):
        changed_run = synthetic_run(base_time)
        changed_run[field] = f"wrong-nonempty-{field.casefold()}"
        try:
            _strict_event_log(
                event_line(changed_run),
                f"synthetic wrong {field}",
                expected_run=synthetic_run_binding,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"wrong nonempty run {field} was accepted")
    future_run = synthetic_run(base_time)
    future_run["CreatedUtc"] = "2999-01-01T00:00:00Z"
    offset_run = synthetic_run(base_time)
    offset_run["CreatedUtc"] = "2026-07-22T01:00:01+00:00"
    second_start = synthetic_start(2, base_time + timedelta(seconds=1, milliseconds=1))
    malformed_events = (
        strict_payload[:-1],
        strict_payload.replace(b'"RecordType":"run"', b'"RecordType":"unknown"'),
        strict_payload.replace(b'"Runtime":', b'"Extra":0,"Runtime":'),
        b'{"RecordType":"run","RecordType":"run"}\n',
        b'{"RecordType":"gameResult","ScoreA":NaN}\n',
        event_line(synthetic_run(base_time)) + event_line(one_result),
        event_line(future_run),
        event_line(offset_run),
        event_line(synthetic_run(base_time))
        + event_line(one_start)
        + event_line(second_start),
    )
    for malformed in malformed_events:
        try:
            _strict_event_growth(malformed, 0, "synthetic malformed events")
        except ValueError:
            pass
        else:
            raise AssertionError("malformed/ambiguous event JSONL was accepted")

    integer_event_mutations = (
        (synthetic_run(base_time), "ProcessorCount"),
        (one_start, "Attempt"),
        (one_result, "Plies"),
    )
    for original, field in integer_event_mutations:
        for replacement in (float(original[field]), True):
            changed = dict(original)
            changed[field] = replacement
            try:
                _strict_event_record(
                    event_line(changed).rstrip(b"\n"),
                    f"synthetic event {field} substitution",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"event {field} accepted integer substitution "
                    f"{replacement!r}"
                )

    nested_integer_events: list[tuple[dict[str, Any], str]] = []
    nested_run_match = synthetic_run(base_time)
    nested_run_match["Match"] = {"MaxPlies": 400}
    nested_integer_events.append((nested_run_match, "run Match.MaxPlies"))
    nested_run_engine = synthetic_run(base_time)
    nested_run_engine["Engines"] = [{"FileSize": 1}]
    nested_integer_events.append((nested_run_engine, "run Engines.FileSize"))
    nested_ply = {
        "RecordType": "ply",
        "GameId": "game-0001",
        "Attempt": 1,
        "Ply": 1,
        "EngineId": "nnue-candidate",
        "Color": "w",
        "PreOfen": "synthetic-ofen",
        "WhiteClockBeforeMs": 1,
        "BlackClockBeforeMs": 1,
        "WhiteClockAfterMs": 1,
        "BlackClockAfterMs": 1,
        "Search": {},
        "FinalInfo": {"Nodes": 1},
        "Pv": {},
    }
    nested_integer_events.append((nested_ply, "ply FinalInfo.Nodes"))
    nested_result = synthetic_result(1, base_time + timedelta(seconds=2))
    nested_result["DevelopmentStyle"] = {"OpeningPlies": 1}
    nested_integer_events.append(
        (nested_result, "gameResult DevelopmentStyle.OpeningPlies")
    )
    for original, nested_label in nested_integer_events:
        path = {
            "run Match.MaxPlies": ("Match", "MaxPlies"),
            "run Engines.FileSize": ("Engines", 0, "FileSize"),
            "ply FinalInfo.Nodes": ("FinalInfo", "Nodes"),
            "gameResult DevelopmentStyle.OpeningPlies": (
                "DevelopmentStyle",
                "OpeningPlies",
            ),
        }[nested_label]
        original_cursor: Any = original
        for part in path:
            original_cursor = original_cursor[part]
        for replacement in (float(original_cursor), True):
            changed = copy.deepcopy(original)
            cursor: Any = changed
            for part in path[:-1]:
                cursor = cursor[part]
            cursor[path[-1]] = replacement
            try:
                _strict_event_record(
                    event_line(changed).rstrip(b"\n"),
                    f"synthetic nested event {nested_label}",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"{nested_label} accepted integer substitution "
                    f"{replacement!r}"
                )

    # Maximum equal-node/equal-time scale: 512 pairs = 1,024 serialized
    # games and 97 launch checkpoints (128 initial pairs plus 96 x 4-pair
    # resumes).  One file read and one streaming hash pass must cover all
    # checkpoints, while only the chain tip is scheduled for core replay.
    with tempfile.TemporaryDirectory(prefix="omega-g5-max-history-") as directory:
        max_events = Path(directory) / "events.jsonl"
        payload = bytearray(event_line(synthetic_run(base_time)))
        checkpoints: list[dict[str, Any]] = []
        intervals: list[dict[str, Any]] = []
        game_index = 1
        distributions = [256, *([8] * 96)]
        for launch_index, game_count in enumerate(distributions):
            before = len(payload)
            first_started = base_time + timedelta(
                seconds=1, milliseconds=(game_index - 1) * 10
            )
            for _ in range(game_count):
                started = base_time + timedelta(
                    seconds=1, milliseconds=(game_index - 1) * 10
                )
                finished = started + timedelta(milliseconds=4)
                payload.extend(event_line(synthetic_start(game_index, started)))
                payload.extend(event_line(synthetic_result(game_index, finished)))
                game_index += 1
            after = len(payload)
            checkpoints.append(
                {
                    "path": str(max_events.resolve()),
                    "bytes": after,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            intervals.append(
                {
                    "beforeBytes": 0 if launch_index == 0 else before,
                    "afterBytes": after,
                    "action": "run" if launch_index == 0 else "resume",
                    "intentUtc": zulu(
                        base_time - timedelta(milliseconds=1)
                        if launch_index == 0
                        else first_started - timedelta(milliseconds=1)
                    ),
                    "completionUtc": zulu(
                        base_time
                        + timedelta(
                            seconds=1,
                            milliseconds=(game_index - 2) * 10 + 5,
                        )
                    ),
                    "successful": True,
                }
            )
        max_events.write_bytes(payload)
        started_perf = time.perf_counter()
        verified_prefix, work = _verify_event_checkpoints(
            max_events,
            checkpoints,
            exact_latest=True,
            label="synthetic 512-pair history",
        )
        state = _strict_event_log(
            verified_prefix,
            "synthetic 512-pair history",
            launch_intervals=intervals,
            expected_run=synthetic_run_binding,
        )
        replayed_sequences: list[int] = []
        replay_calls = _bounded_assessment_replays(
            97, replayed_sequences.append
        )
        elapsed = time.perf_counter() - started_perf
        if (
            state["games"] != 1024
            or work
            != {
                "checkpoints": 97,
                "bytesHashed": len(payload),
                "fileReads": 1,
            }
            or replay_calls != 1
            or replayed_sequences != [97]
            or elapsed > 8.0
        ):
            raise AssertionError(
                "maximum-scale history validation is not linear/bounded: "
                f"state={state}, work={work}, elapsed={elapsed:.3f}s"
            )
        print(
            "Generation-5 512-pair synthetic history: "
            f"{elapsed:.3f}s, {work['fileReads']} event-file read, "
            f"{work['checkpoints']} checkpoints, 1 core-replay slot"
        )
    synthetic = {
        "finishedGames": 8,
        "completePairs": 4,
        "balancedPrefixPairs": 4,
    }
    prior = {
        "progress": {
            "finishedGames": 0,
            "completePairs": 0,
            "balancedPrefixPairs": 0,
        }
    }
    fake = type("Fake", (), {"protocol": protocol})()
    _budget_check(fake, "equal-node", {"progress": synthetic}, prior, 2)
    oversized = copy.deepcopy(synthetic)
    oversized["completePairs"] = 5
    try:
        _budget_check(fake, "equal-node", {"progress": oversized}, prior, 2)
    except ValueError:
        pass
    else:
        raise AssertionError("oversized resume budget was accepted")
    if _normalize_report(
        {"createdUtc": "now", "events": {"path": "a", "bytes": 1, "sha256": "x"}}
    ) != {"events": {"bytes": 1, "sha256": "x"}}:
        raise AssertionError("assessment replay normalization changed")
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
        raise AssertionError("Windows process-freshness parser changed")
    posix = _parse_posix_processes("123 /tmp/senpai.exe\n456 other\n")
    if posix != [{"name": "senpai.exe", "pid": 123}]:
        raise AssertionError("POSIX process-freshness parser changed")
    if core._score_for_candidate(
        {
            "Result": "1-0",
            "WhiteEngineId": "nnue-candidate",
            "BlackEngineId": "hce-control",
        }
    ) != 1.0 or core._score_for_candidate(
        {
            "Result": "1-0",
            "WhiteEngineId": "hce-control",
            "BlackEngineId": "nnue-candidate",
        }
    ) != 0.0:
        raise AssertionError("paired-color candidate scoring changed")
    for global_name in ("contract", "readiness", "core"):
        original = globals()[global_name]
        try:
            globals()[global_name] = object()
            try:
                _verify_import_bindings(protocol)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"orchestrator accepted in-memory {global_name} substitution"
                )
        finally:
            globals()[global_name] = original
    original_readiness_callable = readiness._verify_runtime_authorization
    try:
        readiness._verify_runtime_authorization = lambda path, protocol=None: {}
        try:
            _verify_import_bindings(protocol)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "orchestrator accepted in-memory readiness callable substitution"
            )
    finally:
        readiness._verify_runtime_authorization = original_readiness_callable
    original_binding_verifier = contract.verify_module_binding
    try:
        contract.verify_module_binding = lambda *args, **kwargs: None
        try:
            _verify_import_bindings(protocol)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "orchestrator accepted a substituted module-binding verifier"
            )
    finally:
        contract.verify_module_binding = original_binding_verifier


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("launch")
    launch.add_argument("--gate", required=True, choices=GATES)
    launch.add_argument("--action", choices=("run", "resume"))
    launch.add_argument("--authorization", type=Path)
    assess = commands.add_parser("assess")
    assess.add_argument("--gate", required=True, choices=GATES)
    assess.add_argument("--authorization", type=Path)
    attest = commands.add_parser("attest-idle")
    attest.add_argument("--operator", required=True)
    attest.add_argument("--authorization", type=Path)
    verify = commands.add_parser("verify-state")
    verify.add_argument("--authorization", type=Path)
    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "launch":
        _launch(args)
    elif args.command == "assess":
        _assess(args)
    elif args.command == "attest-idle":
        _attest_idle(args)
    elif args.command == "verify-state":
        _verify_state(args)
    else:
        contract.self_test()
        readiness._self_test()
        _self_test()
        print("Generation-5 match orchestrator self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
