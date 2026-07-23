#!/usr/bin/env python3
"""Launch and assess immutable Generation-5 matches through additive evidence.

OmegaMatch's raw JSONL is never edited.  Each assessment derives a separate,
strict view in which only ``ply.Color`` changes from ``white``/``black`` to
``w``/``b``; both identities and an exact semantic-difference audit are bound
into the append-only assessment and terminal decision chain.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
from datetime import datetime, timezone
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
import types
from typing import Any, Mapping, Sequence

_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))
import king_state_match_readiness_generation5_compat_v2 as readiness


SCHEMA_VERSION = 1
COMPAT_ID = readiness.COMPAT_ID
GATES = readiness.GATES
ASSESSMENT_KIND = "omega-nnue-king-state-v5-color-compat-assessment"
DECISION_KIND = "omega-nnue-king-state-v5-color-compat-decision"
ADAPTER_KIND = "omega-nnue-king-state-v5-color-adapter-evidence"
INTENT_KIND = "omega-nnue-king-state-v5-color-compat-launch-intent"
COMPLETION_KIND = "omega-nnue-king-state-v5-color-compat-launch-completion"
ABORT_COMPLETION_KIND = (
    "omega-nnue-king-state-v5-color-compat-launch-abort-completion"
)
ABORT_DECISION_KIND = "omega-nnue-king-state-v5-color-compat-abort-decision"
CLOSURE_KIND = "omega-nnue-king-state-v5-color-compat-closure"
IDLE_KIND = "omega-equal-time-idle-attestation-v1"
SUCCESS = {"development": "pass", "equal-node": "promote", "equal-time": "promote"}
TERMINAL = {
    "development": {"pass", "fail", "safety-fail"},
    "equal-node": {"promote", "futility", "inconclusive", "safety-fail"},
    "equal-time": {"promote", "futility", "inconclusive", "safety-fail"},
}
PREDECESSOR = {"development": None, "equal-node": "development", "equal-time": "equal-node"}
DERIVED_PROCESS_EXIT_ERROR = (
    "Authenticated OmegaMatch process exit (derived for frozen assessor compatibility)."
)
COORDINATE_MOVE = re.compile(r"^[a-jw][0-9][a-jw][0-9][qrbncw]?$", re.IGNORECASE)
_AUTH_CACHE: tuple[
    dict[str, Any], dict[str, Any], Path, dict[str, Any], dict[str, Any]
] | None = None
_FROZEN_ORCHESTRATOR: Any | None = None


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} must carry UTC timezone information")
    return parsed.astimezone(timezone.utc)


def _validate_process_snapshot(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "capturedUtc",
        "method",
        "relevantNames",
        "relevantProducerPatterns",
        "relevantProcesses",
        "relevantProducerProcesses",
    }:
        raise ValueError(f"{label} fields changed")
    _parse_utc(value.get("capturedUtc"), f"{label} capturedUtc")
    if (
        type(value.get("method")) is not str
        or not value["method"].strip()
        or value["method"] != value["method"].strip()
    ):
        raise ValueError(f"{label} capture method changed")
    if value.get("relevantNames") != list(readiness.RELEVANT_PROCESS_NAMES):
        raise ValueError(f"{label} relevant-name inventory changed")
    if value.get("relevantProducerPatterns") != list(
        readiness.RELEVANT_PRODUCER_PATTERNS
    ):
        raise ValueError(f"{label} producer-pattern inventory changed")
    processes = value.get("relevantProcesses")
    if type(processes) is not list:
        raise ValueError(f"{label} process inventory is not a list")
    normalized: list[dict[str, Any]] = []
    for item in processes:
        if (
            type(item) is not dict
            or set(item) != {"name", "pid"}
            or item.get("name") not in readiness.RELEVANT_PROCESS_NAMES
            or type(item.get("pid")) is not int
            or item["pid"] <= 0
        ):
            raise ValueError(f"{label} process entry changed")
        normalized.append(dict(item))
    if normalized != sorted(normalized, key=lambda item: (item["name"], item["pid"])):
        raise ValueError(f"{label} process entries are unsorted")
    if len({(item["name"], item["pid"]) for item in normalized}) != len(normalized):
        raise ValueError(f"{label} process entries repeat")
    producers = value.get("relevantProducerProcesses")
    if type(producers) is not list:
        raise ValueError(f"{label} producer process inventory is not a list")
    normalized_producers: list[dict[str, Any]] = []
    for item in producers:
        if (
            type(item) is not dict
            or set(item) != {"name", "pid", "pattern"}
            or type(item.get("name")) is not str
            or not item["name"]
            or type(item.get("pid")) is not int
            or item["pid"] <= 0
            or item.get("pattern") not in readiness.RELEVANT_PRODUCER_PATTERNS
        ):
            raise ValueError(f"{label} producer process entry changed")
        normalized_producers.append(dict(item))
    if normalized_producers != sorted(
        normalized_producers,
        key=lambda item: (item["name"], item["pid"], item["pattern"]),
    ) or len(
        {(item["name"], item["pid"], item["pattern"]) for item in normalized_producers}
    ) != len(normalized_producers):
        raise ValueError(f"{label} producer entries are unsorted or repeated")
    return dict(value)


def _protocol() -> dict[str, Any]:
    protocol = readiness.validate_protocol()
    # Import authentication is intentionally repeated at every entry point.
    if not readiness._same_identity(
        protocol["tools"]["matches"], readiness.identity(Path(__file__))
    ):
        raise ValueError("running compatibility match tool differs from protocol pin")
    return protocol


def _authorization(
    path: Path | None, *, fresh: bool = False
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    global _AUTH_CACHE
    protocol = _protocol()
    auth_path = (
        readiness._namespace(protocol, "authorization")
        if path is None
        else path.resolve()
    )
    if not fresh and _AUTH_CACHE is not None:
        cached_protocol, cached_value, cached_path, protocol_identity, auth_identity = (
            _AUTH_CACHE
        )
        if (
            cached_path == auth_path
            and readiness._same_identity(
                protocol_identity, readiness.identity(readiness.PROTOCOL_PATH)
            )
            and readiness._same_identity(auth_identity, readiness.identity(auth_path))
        ):
            return cached_protocol, cached_value, cached_path
    # Full replay is deliberate: every launch and assessment rebinds the exact
    # offline winner and matchAuthorization capsule, not merely file hashes.
    value = readiness.verify_authorization(
        auth_path, protocol_path=readiness.PROTOCOL_PATH, runtime=True
    )
    _AUTH_CACHE = (
        protocol,
        value,
        auth_path,
        readiness.identity(readiness.PROTOCOL_PATH),
        readiness.identity(auth_path),
    )
    return protocol, value, auth_path


def _stage(protocol: Mapping[str, Any], gate: str) -> Path:
    return readiness._stage_paths(protocol)[gate]


def _events(protocol: Mapping[str, Any], gate: str) -> Path:
    return _stage(protocol, gate) / "events.jsonl"


def _launch_dir(protocol: Mapping[str, Any], gate: str) -> Path:
    return _stage(protocol, gate) / "launches"


def _assessment_dir(protocol: Mapping[str, Any], gate: str) -> Path:
    return _stage(protocol, gate) / "assessments"


def _evidence_dir(protocol: Mapping[str, Any], gate: str) -> Path:
    return readiness._namespace(protocol, "evidence") / gate


def _intent_path(protocol: Mapping[str, Any], gate: str, sequence: int) -> Path:
    return _launch_dir(protocol, gate) / f"{sequence:06d}.intent.json"


def _completion_path(protocol: Mapping[str, Any], gate: str, sequence: int) -> Path:
    return _launch_dir(protocol, gate) / f"{sequence:06d}.completion.json"


def _assessment_path(protocol: Mapping[str, Any], gate: str, sequence: int) -> Path:
    return _assessment_dir(protocol, gate) / f"{sequence:06d}.json"


def _raw_prefix_path(protocol: Mapping[str, Any], gate: str, sequence: int) -> Path:
    return _evidence_dir(protocol, gate) / f"{sequence:06d}.raw-prefix.jsonl"


def _color_path(protocol: Mapping[str, Any], gate: str, sequence: int) -> Path:
    return _evidence_dir(protocol, gate) / f"{sequence:06d}.color-events.jsonl"


def _prepared_path(protocol: Mapping[str, Any], gate: str, sequence: int) -> Path:
    return _evidence_dir(protocol, gate) / f"{sequence:06d}.prepared-events.jsonl"


def _replay_request_path(
    protocol: Mapping[str, Any], gate: str, sequence: int
) -> Path:
    return _evidence_dir(protocol, gate) / f"{sequence:06d}.replay-requests.jsonl"


def _replay_output_path(
    protocol: Mapping[str, Any], gate: str, sequence: int
) -> Path:
    return _evidence_dir(protocol, gate) / f"{sequence:06d}.replay-output.jsonl"


def _replay_manifest_path(
    protocol: Mapping[str, Any], gate: str, sequence: int
) -> Path:
    return _evidence_dir(protocol, gate) / f"{sequence:06d}.replay-manifest.json"


def _adapter_path(protocol: Mapping[str, Any], gate: str, sequence: int) -> Path:
    return _evidence_dir(protocol, gate) / f"{sequence:06d}.adapter.json"


def _decision_path(protocol: Mapping[str, Any], gate: str) -> Path:
    return readiness._namespace(protocol, "decisions") / f"{gate}.json"


def _idle_path(protocol: Mapping[str, Any]) -> Path:
    return _stage(protocol, "equal-time") / "idle-machine.attestation.json"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_line(raw: bytes, label: str) -> dict[str, Any]:
    if not raw or raw.endswith(b"\r"):
        raw = raw.removesuffix(b"\r")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=readiness._unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token {token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from error
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _event_records(payload: bytes, label: str) -> list[dict[str, Any]]:
    if not payload or not payload.endswith(b"\n"):
        raise ValueError(f"{label} must be nonempty, line-complete JSONL")
    lines = payload.splitlines()
    return [_strict_line(raw, f"{label} line {index}") for index, raw in enumerate(lines, 1)]


def _derive_records(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    derived: list[dict[str, Any]] = []
    token_counts = {"white": 0, "black": 0}
    ply_records = 0
    for index, raw in enumerate(records, 1):
        record = copy.deepcopy(dict(raw))
        record_type = record.get("RecordType")
        if type(record_type) is not str:
            raise ValueError(f"event {index} has no string RecordType")
        if record_type == "ply":
            ply_records += 1
            color = record.get("Color")
            if type(color) is not str or color not in {"white", "black"}:
                raise ValueError(
                    f"event {index} ply.Color must be exactly 'white' or 'black'"
                )
            token_counts[color] += 1
            record["Color"] = {"white": "w", "black": "b"}[color]
        derived.append(record)
    return derived, {
        "records": len(records),
        "plyRecords": ply_records,
        "rawTokenCounts": token_counts,
        "derivedTokenCounts": {"w": token_counts["white"], "b": token_counts["black"]},
        "colorFieldsChanged": ply_records,
        "otherFieldsChanged": 0,
    }


def _serialize_records(records: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        for record in records
    )


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    """Publish once, or accept an existing byte-identical retry artifact."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"existing evidence path is invalid: {path}")
        if path.read_bytes() != payload:
            raise ValueError(f"existing evidence bytes changed: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise ValueError(f"concurrent evidence publication differs: {path}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _publish_json(path: Path, value: Mapping[str, Any]) -> None:
    _exclusive_bytes(
        path,
        (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8"),
    )


def _derive_terminal_records(
    records: Sequence[Mapping[str, Any]], *, expected_transforms: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if type(expected_transforms) is not int or expected_transforms < 0:
        raise ValueError("terminal adapter transform count is invalid")
    derived = copy.deepcopy([dict(record) for record in records])
    transformed = 0
    for index, (raw, record) in enumerate(zip(records, derived), 1):
        search = raw.get("Search")
        if (
            raw.get("RecordType") == "ply"
            and raw.get("Error") is None
            and type(search) is dict
            and search.get("ProcessExited") is True
        ):
            if raw.get("PostOfen") not in (None, ""):
                raise ValueError(
                    f"event {index} process-exit terminal has a PostOfen"
                )
            record["Error"] = DERIVED_PROCESS_EXIT_ERROR
            transformed += 1
    if transformed != expected_transforms:
        raise ValueError(
            "terminal adapter count differs from authenticated raw schedule"
        )
    return derived, {
        "transformedTerminalPlies": transformed,
        "errorFieldsChanged": transformed,
        "nonErrorFieldsChanged": 0,
    }


def _semantic_terminal_audit(
    color_records: Sequence[Mapping[str, Any]],
    prepared_records: Sequence[Mapping[str, Any]],
    *,
    expected_transforms: int,
) -> dict[str, Any]:
    if len(color_records) != len(prepared_records):
        raise ValueError("terminal adapter changed the event-record count")
    transformed = 0
    for index, (left_value, right_value) in enumerate(
        zip(color_records, prepared_records), 1
    ):
        left = copy.deepcopy(dict(left_value))
        right = copy.deepcopy(dict(right_value))
        search = left.get("Search")
        if (
            left.get("RecordType") == "ply"
            and left.get("Error") is None
            and type(search) is dict
            and search.get("ProcessExited") is True
        ):
            if left.get("PostOfen") not in (None, ""):
                raise ValueError(
                    f"event {index} process-exit terminal has a PostOfen"
                )
            if right.get("Error") != DERIVED_PROCESS_EXIT_ERROR:
                raise ValueError(
                    f"event {index} has the wrong derived process-exit Error"
                )
            left["Error"] = DERIVED_PROCESS_EXIT_ERROR
            transformed += 1
        if left != right:
            raise ValueError(
                f"terminal adapter changed a field other than ply.Error at event {index}"
            )
    if transformed != expected_transforms:
        raise ValueError(
            "terminal semantic audit differs from raw schedule authentication"
        )
    return {
        "recordsCompared": len(color_records),
        "transformedTerminalPlies": transformed,
        "errorFieldsChanged": transformed,
        "nonErrorFieldsChanged": 0,
    }


def _explicit_terminal_failure(record: Mapping[str, Any]) -> bool:
    error = record.get("Error")
    if type(error) is str:
        return bool(error.strip())
    if error is not None:
        return False
    search = record.get("Search")
    return type(search) is dict and search.get("ProcessExited") is True


def _candidate_game_score(record: Mapping[str, Any]) -> float:
    result = record.get("Result")
    white = record.get("WhiteEngineId")
    black = record.get("BlackEngineId")
    if result == "1/2-1/2":
        return 0.5
    if result == "1-0":
        return 1.0 if white == "nnue-candidate" else 0.0
    if result == "0-1":
        return 1.0 if black == "nnue-candidate" else 0.0
    raise ValueError("gameResult.Result is outside the sealed result domain")


def _authenticate_ply_emitter_shape(
    record: Mapping[str, Any], match: Mapping[str, Any], gate: str
) -> None:
    search = record.get("Search")
    if type(search) is not dict:
        raise ValueError(f"{gate} ply Search is not an object")
    required_search = {
        "Command",
        "WallTimeMs",
        "DeadlineExceeded",
        "ProcessExited",
        "Info",
        "RawOutput",
        "StandardError",
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
        or any(type(item) is not str for item in san)
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
    core: Any,
) -> dict[str, Any]:
    """Bind every start/ply/result to the exact sealed schedule prefix."""

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
    match = config.get("match")
    if type(match) is not dict or type(run.get("Match")) is not dict:
        raise ValueError(f"{gate} raw/config match settings are malformed")

    openings_value = suite.get("openings")
    if type(openings_value) is not list or not openings_value:
        raise ValueError(f"{gate} suite has no openings")
    openings: dict[str, Mapping[str, Any]] = {}
    for opening in openings_value:
        if (
            type(opening) is not dict
            or type(opening.get("id")) is not str
            or type(opening.get("initialOfen")) is not str
            or opening.get("moves") != []
        ):
            raise ValueError(f"{gate} sealed opening is malformed")
        if opening["id"] in openings:
            raise ValueError(f"{gate} sealed opening identity is duplicated")
        openings[opening["id"]] = opening
    permutation = core._shuffled_indices(len(openings_value), int(config["seed"]))
    expected_games: list[dict[str, Any]] = []
    for index in permutation:
        opening = openings_value[index]
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
                    "InitialOfen": opening["initialOfen"],
                    "OpeningMoves": [],
                }
            )

    schedule_index = 0
    attempt_by_game: dict[str, int] = {}
    starts: dict[tuple[str, int], Mapping[str, Any]] = {}
    plies: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    active_key: tuple[str, int] | None = None
    completed_games = 0
    raw_colors = {"white": 0, "black": 0}
    terminal_failed_plies = 0
    process_exit_terminal_plies = 0
    successful_plies = 0
    for record in records[1:]:
        record_type = record.get("RecordType")
        if schedule_index >= len(expected_games):
            raise ValueError(f"{gate} raw events exceed the sealed schedule")
        expected = expected_games[schedule_index]
        if record_type == "gameStart":
            if active_key is not None:
                raise ValueError(f"{gate} gameStart overlaps an active attempt")
            game_id = record.get("GameId")
            attempt = record.get("Attempt")
            if game_id != expected["GameId"]:
                raise ValueError(
                    f"{gate} gameStart is outside the exact schedule prefix"
                )
            if (
                type(attempt) is not int
                or attempt != attempt_by_game.get(str(game_id), 0) + 1
            ):
                raise ValueError(
                    f"{gate} gameStart attempts are not unique and monotone"
                )
            for field, wanted in expected.items():
                if record.get(field) != wanted or type(record.get(field)) is not type(
                    wanted
                ):
                    raise ValueError(
                        f"{gate} gameStart {field} differs from sealed schedule"
                    )
            attempt_by_game[str(game_id)] = attempt
            active_key = (str(game_id), attempt)
            starts[active_key] = record
            plies[active_key] = []
        elif record_type == "ply":
            key = (str(record.get("GameId")), record.get("Attempt"))
            if active_key is None or key != active_key or key not in starts:
                raise ValueError(f"{gate} ply is not linked to the active gameStart")
            sequence = plies[key]
            if record.get("Ply") != len(sequence) + 1:
                raise ValueError(f"{gate} ply numbering is not unique and monotone")
            start = starts[key]
            initial_side = str(start["InitialOfen"]).split()[1]
            if initial_side not in {"w", "b"}:
                raise ValueError(f"{gate} gameStart side to move is malformed")
            side = (
                initial_side
                if len(sequence) % 2 == 0
                else ("b" if initial_side == "w" else "w")
            )
            raw_color = "white" if side == "w" else "black"
            if record.get("Color") != raw_color:
                raise ValueError(
                    f"{gate} ply uses a noncanonical or wrong raw Color token"
                )
            expected_engine = (
                start["WhiteEngineId"] if side == "w" else start["BlackEngineId"]
            )
            if record.get("EngineId") != expected_engine:
                raise ValueError(f"{gate} ply EngineId contradicts its color roles")
            _authenticate_ply_emitter_shape(record, match, gate)
            if sequence and record.get("PreOfen") != sequence[-1].get("PostOfen"):
                raise ValueError(f"{gate} ply OFEN chain is discontinuous")
            if not sequence and record.get("PreOfen") != start["InitialOfen"]:
                raise ValueError(
                    f"{gate} first ply is not rooted at the sealed position"
                )
            raw_colors[raw_color] += 1
            sequence.append(record)
        elif record_type == "gameResult":
            key = (str(record.get("GameId")), record.get("Attempt"))
            if active_key is None or key != active_key or key not in starts:
                raise ValueError(
                    f"{gate} gameResult is not linked to the active gameStart"
                )
            start = starts[key]
            for field in (
                "GameId",
                "PairId",
                "Attempt",
                "OpeningId",
                "WhiteEngineId",
                "BlackEngineId",
            ):
                if record.get(field) != start.get(field) or type(
                    record.get(field)
                ) is not type(start.get(field)):
                    raise ValueError(
                        f"{gate} gameResult {field} contradicts gameStart"
                    )
            expected_plies = record.get("Plies")
            game_plies = plies[key]
            if type(expected_plies) is not int or expected_plies < 0:
                raise ValueError(f"{gate} gameResult.Plies is malformed")
            successful_searches = expected_plies
            safety_counters: dict[str, int] = {}
            for field in ("IllegalMoves", "ProtocolFailures", "TimeForfeits"):
                counter = record.get(field)
                if type(counter) is not int or counter < 0:
                    raise ValueError(
                        f"{gate} gameResult.{field} must be an exact nonnegative int"
                    )
                safety_counters[field] = counter
            safety_total = sum(safety_counters.values())
            if successful_searches == 0 and safety_total == 0:
                raise ValueError(
                    f"{gate} zero-safety completed game has no rules-replayed ply"
                )
            for successful in game_plies[:successful_searches]:
                search = successful.get("Search")
                best_move = successful.get("BestMove")
                san = successful.get("San")
                pv = successful.get("Pv")
                if (
                    successful.get("PostOfen") in (None, "")
                    or successful.get("Error") not in (None, "")
                    or type(best_move) is not str
                    or COORDINATE_MOVE.fullmatch(best_move) is None
                    or type(san) is not str
                    or not san.strip()
                    or type(search) is not dict
                    or search.get("ProcessExited") is not False
                    or search.get("DeadlineExceeded") is not False
                    or search.get("BestMove") != best_move
                    or type(pv) is not dict
                    or type(pv.get("IsLegal")) is not bool
                ):
                    raise ValueError(f"{gate} successful search ply is malformed")
            successful_plies += successful_searches
            observed_illegal_pvs = sum(
                type(ply.get("Pv")) is dict and ply["Pv"].get("IsLegal") is False
                for ply in game_plies
            )
            if (
                type(record.get("IllegalPvs")) is not int
                or record["IllegalPvs"] != observed_illegal_pvs
            ):
                raise ValueError(
                    f"{gate} gameResult.IllegalPvs contradicts ply validation"
                )
            if len(game_plies) == successful_searches:
                pass
            elif len(game_plies) == successful_searches + 1 and safety_total > 0:
                terminal = game_plies[-1]
                if (
                    terminal.get("Ply") != expected_plies + 1
                    or not _explicit_terminal_failure(terminal)
                    or terminal.get("PostOfen") not in (None, "")
                    or type(record.get("Termination")) is not str
                    or not record["Termination"].strip()
                ):
                    raise ValueError(
                        f"{gate} terminal failed-search ply is malformed"
                    )
                terminal_failed_plies += 1
                if (
                    terminal.get("Error") is None
                    and type(terminal.get("Search")) is dict
                    and terminal["Search"].get("ProcessExited") is True
                ):
                    process_exit_terminal_plies += 1
            else:
                raise ValueError(
                    f"{gate} gameResult.Plies lacks exact emitted coverage"
                )
            expected_final = (
                game_plies[successful_searches - 1].get("PostOfen")
                if successful_searches
                else start["InitialOfen"]
            )
            if record.get("FinalOfen") != expected_final:
                raise ValueError(f"{gate} gameResult.FinalOfen breaks the ply chain")
            score = _candidate_game_score(record)
            score_a = record.get("ScoreA")
            if (
                type(score_a) not in (int, float)
                or type(score_a) is bool
                or not math.isfinite(float(score_a))
                or abs(float(score_a) - score) > 1e-12
            ):
                raise ValueError(
                    f"{gate} gameResult.ScoreA contradicts the result"
                )
            winner = {
                "1-0": start["WhiteEngineId"],
                "0-1": start["BlackEngineId"],
                "1/2-1/2": None,
            }[record["Result"]]
            if record.get("WinnerEngineId") != winner:
                raise ValueError(
                    f"{gate} gameResult winner contradicts the result"
                )
            completed_games += 1
            schedule_index += 1
            active_key = None
        else:
            raise ValueError(
                f"{gate} raw event record type escaped strict parsing"
            )
    return {
        "schemaVersion": 1,
        "gate": gate,
        "config": dict(config_identity),
        "suite": dict(suite_identity),
        "authorizedScheduleGames": len(expected_games),
        "authorizedStarts": len(starts),
        "completedGames": completed_games,
        "completePairs": schedule_index // 2,
        "activeAttempt": (
            None
            if active_key is None
            else {"gameId": active_key[0], "attempt": active_key[1]}
        ),
        "rawColorCounts": raw_colors,
        "successfulPlies": successful_plies,
        "terminalFailedPlies": terminal_failed_plies,
        "processExitTerminalPlies": process_exit_terminal_plies,
        "allStartsResultsAndPliesAuthenticated": True,
    }


def _replay_requests(
    raw_path: Path, records: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Derive one transcript request per authenticated game attempt."""

    raw_identity = readiness.identity(raw_path)
    starts: dict[tuple[str, int], tuple[int, Mapping[str, Any]]] = {}
    plies: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for line_number, record in enumerate(records, 1):
        if record.get("RecordType") == "gameStart":
            key = (str(record["GameId"]), int(record["Attempt"]))
            if key in starts:
                raise ValueError("rules replay repeats a game-attempt start")
            starts[key] = (line_number, record)
            plies[key] = []
        elif record.get("RecordType") == "ply":
            key = (str(record["GameId"]), int(record["Attempt"]))
            if key not in starts:
                raise ValueError("rules replay ply lacks its gameStart")
            plies[key].append(record)
    requests: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    successful_count = 0
    for key, (line_number, start) in starts.items():
        if start.get("OpeningMoves") != []:
            raise ValueError("G5 rules replay encountered a nonempty opening prefix")
        successful = [
            ply
            for ply in plies[key]
            if ply.get("PostOfen") not in (None, "")
            and ply.get("Error") in (None, "")
        ]
        moves = [str(ply["BestMove"]) for ply in successful]
        positions = [str(ply["PostOfen"]) for ply in successful]
        initial = str(start["InitialOfen"])
        request_id = len(requests)
        requests.append(
            {
                "schemaVersion": 1,
                "kind": "omega-opening-prefix-replay-request-v1",
                "requestId": request_id,
                "sourcePath": str(raw_path.resolve()),
                "sourceBytes": raw_identity["bytes"],
                "sourceSha256": raw_identity["sha256"],
                "sourceRecord": line_number,
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
        )
        normalized.append(
            {
                "gameId": key[0],
                "attempt": key[1],
                "initialOfen": initial,
                "moves": moves,
                "expectedPositions": positions,
            }
        )
        successful_count += len(moves)
    if not requests:
        raise ValueError("rules replay requires at least one authenticated gameStart")
    return requests, normalized, successful_count


def _rules_replay(
    *,
    protocol: Mapping[str, Any],
    authorization: Mapping[str, Any],
    gate: str,
    sequence: int,
    raw_path: Path,
    records: Sequence[Mapping[str, Any]],
    publish: bool,
) -> dict[str, Any]:
    bundle = readiness.verify_rules_replay(protocol)
    rules_parity = readiness.require_rules_match_harness(
        protocol, authorization["omegaMatchBundle"]
    )
    if authorization.get("rulesParity") != rules_parity:
        raise ValueError("authorization replay/OmegaMatch rules parity changed")
    bundle_root = Path(str(bundle["root"])).resolve()
    assembly = (bundle_root / str(bundle["assemblyRelativePath"])).resolve()
    rules = (bundle_root / "ChessLib.dll").resolve()
    dotnet = readiness.verify_identity(
        authorization["dotnetHost"], "rules-replay frozen dotnet host"
    )
    requests, normalized, successful_count = _replay_requests(raw_path, records)
    request_payload = _serialize_records(requests)
    request_path = _replay_request_path(protocol, gate, sequence)
    output_path = _replay_output_path(protocol, gate, sequence)
    manifest_path = _replay_manifest_path(protocol, gate, sequence)
    if publish:
        _exclusive_bytes(request_path, request_payload)
    else:
        if request_path.read_bytes() != request_payload:
            raise ValueError(f"{gate} replay request derivation changed")

    _contract, _core, frozen_readiness = readiness._import_frozen_modules(
        readiness._template(protocol)[1]
    )

    def run(output: Path, manifest: Path) -> None:
        subprocess.run(
            [
                str(dotnet),
                str(assembly),
                "--input",
                str(request_path),
                "--output",
                str(output),
                "--manifest",
                str(manifest),
            ],
            cwd=bundle_root,
            env=frozen_readiness._sanitized_environment(),
            capture_output=True,
            check=True,
            timeout=6 * 60 * 60,
        )

    raw_identity = readiness.identity(raw_path)
    if publish:
        # Run into a disposable directory on every retry.  The managed output
        # is path-independent; the canonical manifest is reconstructed with
        # the final evidence paths so a crash after any subset of the three
        # files can be resumed byte-for-byte.
        with tempfile.TemporaryDirectory(
            prefix=f"omega-{gate}-compat-rules-publish-"
        ) as directory:
            temporary_output = Path(directory) / "positions.jsonl"
            temporary_manifest = Path(directory) / "manifest.json"
            run(temporary_output, temporary_manifest)
            output_payload = temporary_output.read_bytes()
            generated = readiness.strict_load(
                temporary_manifest, f"{gate} generated replay manifest"
            )
            if (
                generated.get("requests") != len(requests)
                or generated.get("sourceCount") != 1
                or not readiness._same_identity(
                    generated.get("input"), readiness.identity(request_path)
                )
                or type(generated.get("sourceSet")) is not list
                or len(generated["sourceSet"]) != 1
                or not readiness._same_identity(
                    generated["sourceSet"][0], raw_identity
                )
            ):
                raise ValueError(f"{gate} generated replay provenance changed")
        _exclusive_bytes(output_path, output_payload)
        output_identity = readiness.identity(output_path)
        prefixes = len(output_payload.splitlines())
        canonical_manifest = {
            "schemaVersion": 1,
            "kind": "omega-opening-prefix-replay-manifest-v1",
            "variant": "Omega",
            "rules": "ChessLib legal coordinate replay",
            "input": readiness.identity(request_path),
            "sourceSet": [raw_identity],
            "sourceCount": 1,
            "requests": len(requests),
            "prefixes": prefixes,
            "parseOrReplayErrors": 0,
            "output": output_identity,
            "runtime": {
                "helper": readiness.identity(assembly),
                "rules": readiness.identity(rules),
            },
        }
        _exclusive_bytes(
            manifest_path,
            (
                json.dumps(
                    canonical_manifest, sort_keys=True, separators=(",", ":")
                )
                + "\n"
            ).encode("utf-8"),
        )
    elif not output_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(f"{gate} persisted rules-replay evidence is absent")

    # The helper's lower-camel replay records intentionally do not use the
    # OmegaMatch ``RecordType`` envelope, so decode each object directly.
    parsed_output = [
        _strict_line(line, f"{gate} managed replay output line {number}")
        for number, line in enumerate(output_path.read_bytes().splitlines(), 1)
    ]
    by_request: dict[int, list[dict[str, Any]]] = {}
    output_fields = {
        "schemaVersion",
        "kind",
        "requestId",
        "sourcePath",
        "sourceBytes",
        "sourceSha256",
        "sourceRecord",
        "sourceObjectOrdinal",
        "sourceObjectIdentity",
        "schema",
        "initialSource",
        "containerProof",
        "ply",
        "move",
        "ofen",
    }
    for record in parsed_output:
        if set(record) != output_fields or type(record.get("requestId")) is not int:
            raise ValueError(f"{gate} managed replay output shape changed")
        by_request.setdefault(record["requestId"], []).append(record)
    semantic: list[dict[str, Any]] = []
    parity = 0
    for request in requests:
        request_id = int(request["requestId"])
        emitted = sorted(
            by_request.pop(request_id, []), key=lambda item: item.get("ply", -1)
        )
        expected_positions = [request["initialOfen"], *request["expectedPositions"]]
        if len(emitted) != len(expected_positions):
            raise ValueError(f"{gate} managed replay prefix count changed")
        for ply, (record, expected_ofen) in enumerate(
            zip(emitted, expected_positions)
        ):
            expected_move = None if ply == 0 else request["moves"][ply - 1]
            expected_source = {
                key: request[key]
                for key in (
                    "requestId",
                    "sourcePath",
                    "sourceBytes",
                    "sourceSha256",
                    "sourceRecord",
                    "sourceObjectOrdinal",
                    "sourceObjectIdentity",
                    "schema",
                    "initialSource",
                    "containerProof",
                )
            }
            if (
                record.get("schemaVersion") != 1
                or record.get("kind")
                != "omega-opening-prefix-replay-position-v1"
                or any(record.get(key) != value for key, value in expected_source.items())
                or record.get("ply") != ply
                or record.get("move") != expected_move
                or record.get("ofen") != expected_ofen
            ):
                raise ValueError(
                    f"{gate} managed rules replay diverged at request "
                    f"{request_id} ply {ply}"
                )
            semantic.append(
                {
                    "requestId": request_id,
                    "ply": ply,
                    "move": expected_move,
                    "ofen": expected_ofen,
                }
            )
            if ply:
                parity += 1
    if by_request:
        raise ValueError(f"{gate} managed replay emitted unknown request ids")
    manifest = readiness.strict_load(manifest_path, f"{gate} replay manifest")
    if set(manifest) != {
        "schemaVersion",
        "kind",
        "variant",
        "rules",
        "input",
        "sourceSet",
        "sourceCount",
        "requests",
        "prefixes",
        "parseOrReplayErrors",
        "output",
        "runtime",
    }:
        raise ValueError(f"{gate} replay manifest field inventory changed")
    runtime = manifest.get("runtime")
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("kind") != "omega-opening-prefix-replay-manifest-v1"
        or manifest.get("variant") != "Omega"
        or manifest.get("rules") != "ChessLib legal coordinate replay"
        or manifest.get("sourceCount") != 1
        or manifest.get("requests") != len(requests)
        or manifest.get("prefixes") != len(parsed_output)
        or manifest.get("parseOrReplayErrors") != 0
        or type(manifest.get("sourceSet")) is not list
        or len(manifest["sourceSet"]) != 1
        or not readiness._same_identity(manifest["sourceSet"][0], raw_identity)
        or type(runtime) is not dict
        or set(runtime) != {"helper", "rules"}
        or not readiness._same_identity(runtime["helper"], readiness.identity(assembly))
        or not readiness._same_identity(runtime["rules"], readiness.identity(rules))
        or not readiness._same_identity(manifest.get("input"), readiness.identity(request_path))
        or not readiness._same_identity(manifest.get("output"), readiness.identity(output_path))
    ):
        raise ValueError(f"{gate} replay manifest binding changed")

    if not publish:
        with tempfile.TemporaryDirectory(
            prefix=f"omega-{gate}-compat-rules-replay-"
        ) as directory:
            rerun_output = Path(directory) / "positions.jsonl"
            rerun_manifest = Path(directory) / "manifest.json"
            run(rerun_output, rerun_manifest)
            if rerun_output.read_bytes() != output_path.read_bytes():
                raise ValueError(f"{gate} persisted rules replay differs from rerun")

    normalized_payload = _serialize_records(normalized)
    semantic_payload = _serialize_records(semantic)
    return {
        "schemaVersion": 1,
        "contract": "exact G5 ChessLib legal transcript replay",
        "rawPrefix": raw_identity,
        "requests": readiness.identity(request_path),
        "output": readiness.identity(output_path),
        "manifest": readiness.identity(manifest_path),
        "gameAttemptRequests": len(requests),
        "zeroMoveInitialStateRequests": sum(not item["moves"] for item in requests),
        "successfulPlies": successful_count,
        "prefixes": len(parsed_output),
        "parityMatches": parity,
        "illegalOrMalformedMoves": 0,
        "normalizedTranscriptSha256": _sha256_bytes(normalized_payload),
        "managedOutputSemanticSha256": _sha256_bytes(semantic_payload),
        "dotnetHost": readiness.identity(dotnet),
        "runtimeBundle": bundle,
        "helper": readiness.identity(assembly),
        "rules": readiness.identity(rules),
        "rulesParity": rules_parity,
        "passes": parity == successful_count,
    }


def _semantic_adapter_audit(
    raw: Sequence[Mapping[str, Any]], derived: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if len(raw) != len(derived):
        raise ValueError("adapter changed the event-record count")
    changes = 0
    raw_counts = {"white": 0, "black": 0}
    for index, (left_value, right_value) in enumerate(zip(raw, derived), 1):
        left = copy.deepcopy(dict(left_value))
        right = copy.deepcopy(dict(right_value))
        if left.get("RecordType") == "ply":
            token = left.get("Color")
            if type(token) is not str or token not in {"white", "black"}:
                raise ValueError(f"raw event {index} contains an unaccepted Color")
            expected = {"white": "w", "black": "b"}[token]
            if right.get("Color") != expected:
                raise ValueError(f"derived event {index} has wrong Color mapping")
            raw_counts[token] += 1
            changes += 1
            left["Color"] = expected
        if left != right:
            raise ValueError(f"adapter changed a field other than ply.Color at event {index}")
    return {
        "recordsCompared": len(raw),
        "colorFieldsChanged": changes,
        "otherFieldsChanged": 0,
        "rawTokenCounts": raw_counts,
        "derivedTokenCounts": {"w": raw_counts["white"], "b": raw_counts["black"]},
    }


def derive_events(
    raw_path: Path,
    evidence_path: Path,
    *,
    gate: str,
    sequence: int,
    authorization: Mapping[str, Any],
    authorization_path: Path,
) -> dict[str, Any]:
    protocol = _protocol()
    if evidence_path.exists():
        return verify_adapter_evidence(
            evidence_path, authorization_path=authorization_path
        )
    raw_before = readiness.identity(raw_path)
    payload = raw_path.read_bytes()
    if len(payload) != raw_before["bytes"] or _sha256_bytes(payload) != raw_before["sha256"]:
        raise ValueError("raw events changed while being read")
    raw_snapshot = _raw_prefix_path(protocol, gate, sequence)
    _exclusive_bytes(raw_snapshot, payload)
    raw_snapshot_identity = readiness.identity(raw_snapshot)
    if (
        raw_snapshot_identity["bytes"] != raw_before["bytes"]
        or raw_snapshot_identity["sha256"] != raw_before["sha256"]
    ):
        raise ValueError("immutable raw snapshot differs from OmegaMatch prefix")
    raw_records = _event_records(payload, "raw OmegaMatch events")
    contract, core, frozen_readiness = readiness._import_frozen_modules(
        readiness._template(protocol)[1]
    )
    original_protocol = contract.validate_protocol()
    frozen_readiness._install_core_profile(original_protocol)
    suite_identity = authorization["gates"][gate]["suite"]
    config_identity = authorization["gates"][gate]["config"]
    suite = readiness.strict_load(
        readiness.verify_identity(suite_identity, f"{gate} adapter suite"),
        f"{gate} adapter suite",
    )
    config = readiness.strict_load(
        readiness.verify_identity(config_identity, f"{gate} adapter config"),
        f"{gate} adapter config",
    )
    schedule = _authenticate_raw_event_schedule(
        records=raw_records,
        suite=suite,
        config=config,
        suite_identity=suite_identity,
        config_identity=config_identity,
        gate=gate,
        core=core,
    )
    rules_replay = _rules_replay(
        protocol=protocol,
        authorization=authorization,
        gate=gate,
        sequence=sequence,
        raw_path=raw_snapshot,
        records=raw_records,
        publish=True,
    )
    color_records, color_summary = _derive_records(raw_records)
    color_payload = _serialize_records(color_records)
    color_path = _color_path(protocol, gate, sequence)
    _exclusive_bytes(color_path, color_payload)
    prepared_records, terminal_summary = _derive_terminal_records(
        color_records,
        expected_transforms=int(schedule["processExitTerminalPlies"]),
    )
    prepared_path = _prepared_path(protocol, gate, sequence)
    _exclusive_bytes(prepared_path, _serialize_records(prepared_records))
    raw_after = readiness.identity(raw_path)
    if raw_after != raw_before:
        raise ValueError("raw events changed during derivation")
    color_semantic = _semantic_adapter_audit(
        raw_records, _event_records(color_path.read_bytes(), "color-derived events")
    )
    if color_semantic != {
        "recordsCompared": color_summary["records"],
        "colorFieldsChanged": color_summary["colorFieldsChanged"],
        "otherFieldsChanged": 0,
        "rawTokenCounts": color_summary["rawTokenCounts"],
        "derivedTokenCounts": color_summary["derivedTokenCounts"],
    }:
        raise ValueError("color adapter summary and semantic replay differ")
    terminal_semantic = _semantic_terminal_audit(
        color_records,
        _event_records(prepared_path.read_bytes(), "prepared compatibility events"),
        expected_transforms=int(schedule["processExitTerminalPlies"]),
    )
    if terminal_semantic != {
        "recordsCompared": color_summary["records"],
        **terminal_summary,
    }:
        raise ValueError("terminal adapter summary and semantic replay differ")
    prepared_identity = readiness.identity(prepared_path)
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ADAPTER_KIND,
        "compatibilityId": COMPAT_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": readiness._utc_now(),
        "protocol": readiness.identity(readiness.PROTOCOL_PATH),
        "authorization": readiness.identity(authorization_path),
        "selectedNetwork": copy.deepcopy(authorization["selectedNetwork"]),
        "rawEvents": raw_before,
        "rawPrefix": raw_snapshot_identity,
        "colorDerivedEvents": readiness.identity(color_path),
        "preparedEvents": prepared_identity,
        "assessmentInput": prepared_identity,
        "rawAuthentication": schedule,
        "rulesReplay": rules_replay,
        "adapter": copy.deepcopy(protocol["adapter"]),
        "audit": {
            "color": color_semantic,
            "terminal": terminal_semantic,
            "preparedCompatibilityView": {
                "raw": raw_snapshot_identity,
                "derived": prepared_identity,
                "changedFields": {
                    "Color": color_semantic["colorFieldsChanged"],
                    "Error": terminal_semantic["errorFieldsChanged"],
                },
                "totalFieldsChanged": (
                    color_semantic["colorFieldsChanged"]
                    + terminal_semantic["errorFieldsChanged"]
                ),
                "unauthorizedFieldsChanged": 0,
                "rawEvidenceRetained": True,
            },
        },
        "rawEventsUnchangedDuringDerivation": True,
        "finalStageSeal": True,
    }
    _publish_json(evidence_path, value)
    return verify_adapter_evidence(evidence_path, authorization_path=authorization_path)


def verify_adapter_evidence(
    path: Path, *, authorization_path: Path | None = None
) -> dict[str, Any]:
    protocol, authorization, canonical_authorization = _authorization(authorization_path)
    value = readiness.strict_load(path, "color-adapter evidence")
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "gate",
        "sequence",
        "createdUtc",
        "protocol",
        "authorization",
        "selectedNetwork",
        "rawEvents",
        "rawPrefix",
        "colorDerivedEvents",
        "preparedEvents",
        "assessmentInput",
        "rawAuthentication",
        "rulesReplay",
        "adapter",
        "audit",
        "rawEventsUnchangedDuringDerivation",
        "finalStageSeal",
    }
    if set(value) != expected:
        raise ValueError("color-adapter evidence field inventory changed")
    gate = value.get("gate")
    sequence = value.get("sequence")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != ADAPTER_KIND
        or value.get("compatibilityId") != COMPAT_ID
        or gate not in GATES
        or type(sequence) is not int
        or sequence <= 0
        or value.get("adapter") != protocol["adapter"]
        or value.get("rawEventsUnchangedDuringDerivation") is not True
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError("color-adapter evidence envelope changed")
    if path.resolve() != _adapter_path(protocol, gate, sequence):
        raise ValueError("color-adapter evidence path changed")
    if not readiness._same_identity(
        value.get("authorization"), readiness.identity(canonical_authorization)
    ):
        raise ValueError("color-adapter authorization changed")
    if value.get("selectedNetwork") != authorization["selectedNetwork"]:
        raise ValueError("color-adapter selected network changed")
    raw_record = readiness._identity_shape(
        value.get("rawEvents"), "adapter raw events"
    )
    raw_path = Path(raw_record["path"]).resolve()
    raw_snapshot_path = readiness.verify_identity(
        value.get("rawPrefix"), "adapter immutable raw prefix"
    )
    color_path = readiness.verify_identity(
        value.get("colorDerivedEvents"), "adapter color-derived events"
    )
    prepared_path = readiness.verify_identity(
        value.get("preparedEvents"), "adapter prepared events"
    )
    if (
        raw_path != _events(protocol, gate)
        or raw_snapshot_path != _raw_prefix_path(protocol, gate, sequence)
        or color_path != _color_path(protocol, gate, sequence)
        or prepared_path != _prepared_path(protocol, gate, sequence)
    ):
        raise ValueError("color-adapter event path changed")
    appendable_prefix = _prefix(raw_path, raw_record, exact=False)
    immutable_prefix = raw_snapshot_path.read_bytes()
    if (
        appendable_prefix != immutable_prefix
        or raw_record["bytes"] != value["rawPrefix"]["bytes"]
        or raw_record["sha256"] != value["rawPrefix"]["sha256"]
    ):
        raise ValueError("immutable raw prefix differs from append-only checkpoint")
    raw_records = _event_records(immutable_prefix, "adapter raw replay")
    color_records = _event_records(
        color_path.read_bytes(), "adapter color-derived replay"
    )
    prepared_records = _event_records(
        prepared_path.read_bytes(), "adapter prepared replay"
    )
    contract, core, frozen_readiness = readiness._import_frozen_modules(
        readiness._template(protocol)[1]
    )
    original_protocol = contract.validate_protocol()
    frozen_readiness._install_core_profile(original_protocol)
    suite_identity = authorization["gates"][gate]["suite"]
    config_identity = authorization["gates"][gate]["config"]
    suite = readiness.strict_load(
        readiness.verify_identity(suite_identity, f"{gate} replay suite"),
        f"{gate} replay suite",
    )
    config = readiness.strict_load(
        readiness.verify_identity(config_identity, f"{gate} replay config"),
        f"{gate} replay config",
    )
    schedule = _authenticate_raw_event_schedule(
        records=raw_records,
        suite=suite,
        config=config,
        suite_identity=suite_identity,
        config_identity=config_identity,
        gate=gate,
        core=core,
    )
    if value.get("rawAuthentication") != schedule:
        raise ValueError("adapter raw schedule authentication changed")
    replay = _rules_replay(
        protocol=protocol,
        authorization=authorization,
        gate=gate,
        sequence=sequence,
        raw_path=raw_snapshot_path,
        records=raw_records,
        publish=False,
    )
    if value.get("rulesReplay") != replay:
        raise ValueError("adapter rules-replay evidence changed")
    color_audit = _semantic_adapter_audit(raw_records, color_records)
    terminal_audit = _semantic_terminal_audit(
        color_records,
        prepared_records,
        expected_transforms=int(schedule["processExitTerminalPlies"]),
    )
    expected_audit = {
        "color": color_audit,
        "terminal": terminal_audit,
        "preparedCompatibilityView": {
            "raw": value["rawPrefix"],
            "derived": value["preparedEvents"],
            "changedFields": {
                "Color": color_audit["colorFieldsChanged"],
                "Error": terminal_audit["errorFieldsChanged"],
            },
            "totalFieldsChanged": (
                color_audit["colorFieldsChanged"]
                + terminal_audit["errorFieldsChanged"]
            ),
            "unauthorizedFieldsChanged": 0,
            "rawEvidenceRetained": True,
        },
    }
    if value.get("audit") != expected_audit:
        raise ValueError("color-adapter semantic audit changed")
    if value.get("assessmentInput") != value.get("preparedEvents"):
        raise ValueError("adapter assessment input is not the prepared view")
    return value


def _prefix(path: Path, record: Mapping[str, Any], *, exact: bool) -> bytes:
    shaped = readiness._identity_shape(record, "event-prefix identity")
    if Path(shaped["path"]).resolve() != path.resolve():
        raise ValueError("event-prefix path changed")
    current = path.read_bytes()
    count = shaped["bytes"]
    if len(current) < count or (exact and len(current) != count):
        raise ValueError("event-prefix length changed")
    prefix = current[:count]
    if _sha256_bytes(prefix) != shaped["sha256"]:
        raise ValueError("event-prefix bytes changed")
    return prefix


def _verify_event_checkpoints(
    protocol: Mapping[str, Any],
    gate: str,
    checkpoints: Sequence[Mapping[str, Any]],
    *,
    exact_latest: bool,
) -> None:
    path = _events(protocol, gate)
    if not checkpoints:
        if exact_latest and path.exists():
            raise ValueError(f"{gate} has events without a sealed checkpoint")
        return
    by_size: dict[int, str] = {}
    for number, item in enumerate(checkpoints, 1):
        record = readiness._identity_shape(item, f"{gate} checkpoint {number}")
        if Path(record["path"]).resolve() != path.resolve():
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
        raise ValueError(f"{gate} current event length differs from chain tip")
    digest = hashlib.sha256()
    cursor = 0
    for count, expected in ordered:
        digest.update(payload[cursor:count])
        cursor = count
        if digest.hexdigest() != expected:
            raise ValueError(f"{gate} event checkpoint changed at byte {count}")


def _entries(directory: Path, suffixes: set[str]) -> dict[tuple[int, str], Path]:
    if not directory.exists():
        return {}
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"evidence directory is invalid: {directory}")
    result: dict[tuple[int, str], Path] = {}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"unexpected evidence entry: {path}")
        parts = path.name.split(".")
        if (
            len(parts) != 3
            or len(parts[0]) != 6
            or not parts[0].isdigit()
            or parts[1] not in suffixes
            or parts[2] != "json"
        ):
            raise ValueError(f"unexpected evidence filename: {path}")
        key = (int(parts[0]), parts[1])
        if key in result:
            raise ValueError(f"duplicate evidence sequence: {path}")
        result[key] = path
    return result


def _history(
    protocol: Mapping[str, Any], gate: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    launch_entries = _entries(_launch_dir(protocol, gate), {"intent", "completion"})
    assessment_entries: dict[tuple[int, str], Path] = {}
    # Assessments use ``000001.json`` rather than a suffix; parse separately.
    if _assessment_dir(protocol, gate).exists():
        for path in _assessment_dir(protocol, gate).iterdir():
            if path.is_symlink() or not path.is_file() or not re_full_sequence(path.name):
                raise ValueError(f"unexpected assessment artifact: {path}")
            assessment_entries[(int(path.stem), "assessment")] = path
    sequences = sorted({key[0] for key in launch_entries})
    if sequences and sequences != list(range(1, max(sequences) + 1)):
        raise ValueError(f"{gate} launch sequences are not contiguous")
    completed: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    checkpoints: list[dict[str, Any]] = []
    for sequence in sequences:
        intent_path = launch_entries.get((sequence, "intent"))
        completion_path = launch_entries.get((sequence, "completion"))
        if intent_path is None:
            raise ValueError(f"{gate} completion lacks intent {sequence}")
        intent = _verify_intent(intent_path, protocol, gate, sequence)
        if intent.get("eventsBefore") is not None:
            checkpoints.append(dict(intent["eventsBefore"]))
        if completion_path is None:
            if sequence != max(sequences):
                raise ValueError(f"{gate} has a non-final pending intent")
            pending = {"path": intent_path, "value": intent}
        else:
            completion = _verify_completion(
                completion_path, protocol, gate, sequence, intent
            )
            if completion.get("aborted") is True and sequence != max(sequences):
                raise ValueError(f"{gate} has a launch after an abort completion")
            completed.append(
                {"intent": intent, "value": completion, "path": completion_path}
            )
            if completion.get("aborted") is not True:
                checkpoints.append(dict(completion["eventsAfter"]))
    assessments: list[dict[str, Any]] = []
    assessment_sequences = sorted(key[0] for key in assessment_entries)
    if assessment_sequences != list(range(1, len(assessment_sequences) + 1)):
        raise ValueError(f"{gate} assessment sequences are not contiguous")
    for sequence in assessment_sequences:
        assessments.append(
            _verify_assessment(
                assessment_entries[(sequence, "assessment")],
                protocol,
                gate,
                sequence,
                replay_core=True,
            )
        )
        checkpoints.append(dict(assessments[-1]["rawEvents"]))
    if len(completed) < len(assessments) or len(completed) > len(assessments) + 1:
        raise ValueError(f"{gate} launch/assessment chain is imbalanced")
    aborted = bool(completed and completed[-1]["value"].get("aborted") is True)
    if aborted and len(completed) != len(assessments) + 1:
        raise ValueError(f"{gate} abort completion is not the chain tip")
    _verify_event_checkpoints(
        protocol,
        gate,
        checkpoints,
        exact_latest=pending is None and not aborted,
    )
    evidence = _evidence_dir(protocol, gate)
    expected_evidence = {
        path.resolve()
        for sequence in assessment_sequences
        for path in (
            _raw_prefix_path(protocol, gate, sequence),
            _color_path(protocol, gate, sequence),
            _prepared_path(protocol, gate, sequence),
            _replay_request_path(protocol, gate, sequence),
            _replay_output_path(protocol, gate, sequence),
            _replay_manifest_path(protocol, gate, sequence),
            _adapter_path(protocol, gate, sequence),
        )
    }
    actual_evidence: set[Path] = set()
    if evidence.exists():
        if evidence.is_symlink() or not evidence.is_dir():
            raise ValueError(f"{gate} adapter evidence directory is invalid")
        for path in evidence.iterdir():
            match = re.fullmatch(
                r"([0-9]{6})\.(adapter\.json|raw-prefix\.jsonl|"
                r"color-events\.jsonl|prepared-events\.jsonl|"
                r"replay-requests\.jsonl|replay-output\.jsonl|"
                r"replay-manifest\.json)",
                path.name,
            )
            if path.is_symlink() or not path.is_file() or match is None:
                raise ValueError(
                    f"unexpected {gate} adapter evidence artifact: {path}"
                )
            actual_evidence.add(path.resolve())
    staged_evidence: set[Path] = set()
    if (
        pending is None
        and len(completed) == len(assessments) + 1
        and completed[-1]["value"].get("aborted") is not True
    ):
        staged_sequence = len(assessments) + 1
        staged_evidence = {
            path.resolve()
            for path in (
                _raw_prefix_path(protocol, gate, staged_sequence),
                _color_path(protocol, gate, staged_sequence),
                _prepared_path(protocol, gate, staged_sequence),
                _replay_request_path(protocol, gate, staged_sequence),
                _replay_output_path(protocol, gate, staged_sequence),
                _replay_manifest_path(protocol, gate, staged_sequence),
                _adapter_path(protocol, gate, staged_sequence),
            )
        }
    if not expected_evidence.issubset(actual_evidence) or not actual_evidence.issubset(
        expected_evidence | staged_evidence
    ):
        raise ValueError(f"{gate} adapter-evidence inventory changed")
    return completed, assessments, pending


def re_full_sequence(name: str) -> bool:
    return len(name) == 11 and name[:6].isdigit() and name[6:] == ".json"


def _run_binding(auth: Mapping[str, Any], gate: str) -> dict[str, Any]:
    config = readiness.strict_load(
        Path(auth["gates"][gate]["config"]["path"]), f"{gate} config"
    )
    return {
        "RunId": config["runId"],
        "ProfileId": config["profileId"],
        "FreshnessMarker": config["freshnessMarker"],
    }


def _protected(
    protocol: Mapping[str, Any],
    auth: Mapping[str, Any],
    auth_path: Path,
    gate: str,
) -> dict[str, Any]:
    binding = _run_binding(auth, gate)
    value = {
        "authorization": readiness.identity(auth_path),
        "coreSeal": copy.deepcopy(auth["coreSeal"]),
        "config": copy.deepcopy(auth["gates"][gate]["config"]),
        "suite": copy.deepcopy(auth["gates"][gate]["suite"]),
        "engine": copy.deepcopy(auth["engine"]),
        "network": copy.deepcopy(auth["selectedNetwork"]),
        "dotnetHost": copy.deepcopy(auth["dotnetHost"]),
        "dotnetRuntimeManifest": copy.deepcopy(auth["dotnetRuntimeManifest"]),
        "omegaMatchAssembly": copy.deepcopy(auth["omegaMatchAssembly"]),
        "appHost": copy.deepcopy(auth["omegaMatchAppHost"]),
        "compatReadiness": copy.deepcopy(protocol["tools"]["readiness"]),
        "compatMatches": copy.deepcopy(protocol["tools"]["matches"]),
        "rulesReplayBundleSha256": protocol["rulesReplay"]["runtimeBundle"][
            "sha256"
        ],
        "omegaMatchBundleSha256": auth["omegaMatchBundle"]["sha256"],
        "runId": binding["RunId"],
        "profileId": binding["ProfileId"],
        "freshnessMarker": binding["FreshnessMarker"],
    }
    for label in (
        "authorization",
        "coreSeal",
        "config",
        "suite",
        "engine",
        "network",
        "dotnetHost",
        "dotnetRuntimeManifest",
        "omegaMatchAssembly",
        "appHost",
        "compatReadiness",
        "compatMatches",
    ):
        readiness.verify_identity(value[label], f"{gate} protected {label}")
    readiness.verify_rules_replay(protocol)
    return value


def _canonical_artifact_identity(path: Path, expected: Any, label: str) -> None:
    if not readiness._same_identity(expected, readiness.identity(path)):
        raise ValueError(f"{label} identity changed")


def _identity_created(value: Any, label: str) -> datetime:
    path = readiness.verify_identity(value, f"{label} identity")
    record = readiness.strict_load(path, label)
    return _parse_utc(record.get("createdUtc"), f"{label} createdUtc")


def _frozen_orchestrator(protocol: Mapping[str, Any]) -> Any:
    global _FROZEN_ORCHESTRATOR
    expected = readiness.verify_identity(
        readiness._template(protocol)[1]["immutableGeneration5Authority"][
            "matchOrchestratorSource"
        ],
        "frozen G5 orchestrator",
    )
    if _FROZEN_ORCHESTRATOR is not None:
        if Path(str(_FROZEN_ORCHESTRATOR.__file__)).resolve() != expected:
            raise ValueError("cached frozen G5 orchestrator path changed")
        return _FROZEN_ORCHESTRATOR
    name = "_omega_g5_frozen_orchestrator_color_compat_v2"
    if name in sys.modules:
        raise ImportError("refusing preloaded private frozen G5 orchestrator")
    payload = expected.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(expected)
    module.__package__ = ""
    sys.modules[name] = module
    try:
        exec(compile(payload, str(expected), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if readiness.identity(expected)["sha256"] != hashlib.sha256(payload).hexdigest():
        raise ValueError("frozen G5 orchestrator changed during authenticated load")
    _FROZEN_ORCHESTRATOR = module
    return module


def _verify_intent(
    path: Path,
    protocol: Mapping[str, Any],
    gate: str,
    sequence: int,
) -> dict[str, Any]:
    _protocol_value, auth, auth_path = _authorization(None)
    value = readiness.strict_load(path, f"{gate} launch intent")
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "gate",
        "sequence",
        "createdUtc",
        "authorization",
        "action",
        "pairBudget",
        "eventsBefore",
        "priorAssessment",
        "priorCompletion",
        "predecessorDecision",
        "idleAttestation",
        "preLaunchIdleSnapshot",
        "protected",
        "finalStageSeal",
    }
    if set(value) != expected:
        raise ValueError(f"{gate} launch-intent fields changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != INTENT_KIND
        or value.get("compatibilityId") != COMPAT_ID
        or value.get("gate") != gate
        or value.get("sequence") != sequence
        or value.get("action") != ("run" if sequence == 1 else "resume")
        or value.get("pairBudget")
        != protocol["execution"][
            "initialPairBudget" if sequence == 1 else "resumePairBudget"
        ][gate]
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError(f"{gate} launch-intent envelope changed")
    if path.resolve() != _intent_path(protocol, gate, sequence):
        raise ValueError(f"{gate} launch-intent path changed")
    created = _parse_utc(value.get("createdUtc"), f"{gate} intent createdUtc")
    if created < _parse_utc(auth.get("createdUtc"), "authorization createdUtc"):
        raise ValueError(f"{gate} intent predates authorization")
    _canonical_artifact_identity(
        auth_path, value.get("authorization"), f"{gate} intent authorization"
    )
    prior_assessment = (
        None
        if sequence == 1
        else readiness.identity(_assessment_path(protocol, gate, sequence - 1))
    )
    prior_completion = (
        None
        if sequence == 1
        else readiness.identity(_completion_path(protocol, gate, sequence - 1))
    )
    if value.get("priorAssessment") != prior_assessment:
        raise ValueError(f"{gate} intent prior assessment changed")
    if value.get("priorCompletion") != prior_completion:
        raise ValueError(f"{gate} intent prior completion changed")
    before = value.get("eventsBefore")
    if sequence == 1:
        if before is not None:
            raise ValueError(f"{gate} initial intent has prior events")
    else:
        prior_value = readiness.strict_load(
            _assessment_path(protocol, gate, sequence - 1),
            f"{gate} prior assessment",
        )
        if before != prior_value.get("rawEvents"):
            raise ValueError(f"{gate} resume checkpoint changed")
        _prefix(_events(protocol, gate), before, exact=False)
    expected_predecessor = _predecessor_identity(protocol, gate)
    if value.get("predecessorDecision") != expected_predecessor:
        raise ValueError(f"{gate} intent predecessor changed")
    expected_idle = (
        readiness.identity(_idle_path(protocol)) if gate == "equal-time" else None
    )
    if value.get("idleAttestation") != expected_idle:
        raise ValueError(f"{gate} intent idle-attestation binding changed")
    for record, label in (
        (prior_assessment, "prior assessment"),
        (prior_completion, "prior completion"),
        (expected_predecessor, "predecessor decision"),
        (expected_idle, "idle attestation"),
    ):
        if record is not None and created < _identity_created(
            record, f"{gate} {label}"
        ):
            raise ValueError(f"{gate} intent predates {label}")
    snapshot = _validate_process_snapshot(
        value.get("preLaunchIdleSnapshot"), f"{gate} intent prelaunch snapshot"
    )
    if (
        snapshot["relevantProcesses"] != []
        or snapshot["relevantProducerProcesses"] != []
    ):
        raise ValueError(f"{gate} launch was not process-idle")
    if _parse_utc(
        snapshot["capturedUtc"], f"{gate} prelaunch capturedUtc"
    ) > created:
        raise ValueError(f"{gate} prelaunch snapshot postdates intent")
    if value.get("protected") != _protected(protocol, auth, auth_path, gate):
        raise ValueError(f"{gate} protected launch identities changed")
    return value


def _normal_completion_growth(
    protocol: Mapping[str, Any],
    auth: Mapping[str, Any],
    gate: str,
    intent: Mapping[str, Any],
    completion: Mapping[str, Any],
) -> bytes:
    event_record = readiness._identity_shape(
        completion.get("eventsAfter"), f"{gate} completion events"
    )
    event_path = Path(event_record["path"]).resolve()
    if event_path != _events(protocol, gate):
        raise ValueError(f"{gate} completion event path changed")
    prefix = _prefix(event_path, event_record, exact=False)
    before = intent.get("eventsBefore")
    before_bytes = 0 if before is None else int(before["bytes"])
    if event_record["bytes"] <= before_bytes:
        raise ValueError(f"{gate} completion records no event growth")
    if before is not None:
        _prefix(event_path, before, exact=False)
    orchestrator = _frozen_orchestrator(protocol)
    orchestrator._strict_event_growth(
        prefix,
        before_bytes,
        f"{gate} compatibility completion",
        expected_run=_run_binding(auth, gate),
        allow_incomplete_tail=(
            completion.get("recoveredFromEventGrowth") is True
            or completion.get("returnCode") != 0
        ),
    )
    orchestrator._validate_event_growth_boundary(
        prefix,
        intent,
        completion,
        label=f"{gate} compatibility completion",
    )
    return prefix


def _abort_prefix_evidence(payload: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    newline = payload.rfind(b"\n")
    complete = payload[: newline + 1] if newline >= 0 else b""
    tail = payload[len(complete) :]
    return (
        {"bytes": len(complete), "sha256": _sha256_bytes(complete)},
        {"bytes": len(tail), "sha256": _sha256_bytes(tail)},
    )


def _verify_abort_completion(
    path: Path,
    protocol: Mapping[str, Any],
    gate: str,
    sequence: int,
    intent: Mapping[str, Any],
    value: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _protocol_value, auth, auth_path = _authorization(None)
    document = (
        readiness.strict_load(path, f"{gate} launch abort completion")
        if value is None
        else dict(value)
    )
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "gate",
        "sequence",
        "createdUtc",
        "intent",
        "observedRawEvents",
        "completePrefix",
        "ignoredTail",
        "abortReason",
        "cause",
        "returnCode",
        "postLaunchProcessSnapshot",
        "postLaunchProtected",
        "finalStageSeal",
    }
    if set(document) != expected:
        raise ValueError(f"{gate} abort-completion fields changed")
    if (
        document.get("schemaVersion") != SCHEMA_VERSION
        or document.get("kind") != ABORT_COMPLETION_KIND
        or document.get("compatibilityId") != COMPAT_ID
        or document.get("gate") != gate
        or document.get("sequence") != sequence
        or document.get("abortReason")
        not in {"no-event-growth", "partial-json-tail", "invalid-complete-growth"}
        or (
            document.get("returnCode") is not None
            and type(document.get("returnCode")) is not int
        )
        or document.get("finalStageSeal") is not True
    ):
        raise ValueError(f"{gate} abort-completion envelope changed")
    if path.resolve() != _completion_path(protocol, gate, sequence):
        raise ValueError(f"{gate} abort-completion path changed")
    created = _parse_utc(
        document.get("createdUtc"), f"{gate} abort completion createdUtc"
    )
    if created < _parse_utc(intent.get("createdUtc"), f"{gate} intent createdUtc"):
        raise ValueError(f"{gate} abort completion predates intent")
    if not readiness._same_identity(
        document.get("intent"),
        readiness.identity(_intent_path(protocol, gate, sequence)),
    ):
        raise ValueError(f"{gate} abort-completion intent changed")
    cause = document.get("cause")
    if (
        type(cause) is not dict
        or set(cause) != {"errorType", "message"}
        or type(cause.get("errorType")) is not str
        or not cause["errorType"]
        or type(cause.get("message")) is not str
        or not cause["message"]
    ):
        raise ValueError(f"{gate} abort-completion cause changed")
    event_path = _events(protocol, gate)
    observed = document.get("observedRawEvents")
    if observed is None:
        if event_path.exists():
            raise ValueError(f"{gate} abort omitted existing raw events")
        payload = b""
    else:
        observed_path = readiness.verify_identity(
            observed, f"{gate} abort observed raw events"
        )
        if observed_path != event_path:
            raise ValueError(f"{gate} abort raw-event path changed")
        payload = _prefix(event_path, observed, exact=True)
    complete, tail = _abort_prefix_evidence(payload)
    if document.get("completePrefix") != complete or document.get(
        "ignoredTail"
    ) != tail:
        raise ValueError(f"{gate} abort prefix/tail evidence changed")
    before = intent.get("eventsBefore")
    before_bytes = 0 if before is None else int(before["bytes"])
    if before is not None:
        _prefix(event_path, before, exact=False)
    growth = len(payload) - before_bytes
    reason = document["abortReason"]
    if reason == "no-event-growth":
        if growth > 0:
            raise ValueError(f"{gate} no-growth abort contains new events")
    elif reason == "partial-json-tail":
        if growth <= 0 or tail["bytes"] <= 0:
            raise ValueError(f"{gate} partial-tail abort has no partial growth")
    else:
        if growth <= 0 or tail["bytes"] != 0:
            raise ValueError(f"{gate} invalid-growth abort shape changed")
        probe = {
            "eventsAfter": dict(observed),
            "recoveredFromEventGrowth": document.get("returnCode") is None,
            "returnCode": document.get("returnCode"),
            "createdUtc": document["createdUtc"],
        }
        try:
            _normal_completion_growth(
                protocol, auth, gate, intent, probe
            )
        except (RuntimeError, ValueError):
            pass
        else:
            raise ValueError(f"{gate} aborted an otherwise valid event growth")
    snapshot = _validate_process_snapshot(
        document.get("postLaunchProcessSnapshot"),
        f"{gate} abort postlaunch snapshot",
    )
    if _parse_utc(
        snapshot["capturedUtc"], f"{gate} abort snapshot capturedUtc"
    ) < created:
        raise ValueError(f"{gate} abort snapshot predates completion")
    if document.get("postLaunchProtected") != _protected(
        protocol, auth, auth_path, gate
    ):
        raise ValueError(f"{gate} abort protected identities changed")
    return {**document, "aborted": True}


def _verify_completion(
    path: Path,
    protocol: Mapping[str, Any],
    gate: str,
    sequence: int,
    intent: Mapping[str, Any],
) -> dict[str, Any]:
    _protocol_value, auth, auth_path = _authorization(None)
    value = readiness.strict_load(path, f"{gate} launch completion")
    if value.get("kind") == ABORT_COMPLETION_KIND:
        return _verify_abort_completion(
            path, protocol, gate, sequence, intent, value
        )
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "gate",
        "sequence",
        "createdUtc",
        "intent",
        "eventsAfter",
        "returnCode",
        "recoveredFromEventGrowth",
        "postLaunchProcessSnapshot",
        "postLaunchProtected",
        "finalStageSeal",
    }
    if set(value) != expected:
        raise ValueError(f"{gate} completion fields changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != COMPLETION_KIND
        or value.get("compatibilityId") != COMPAT_ID
        or value.get("gate") != gate
        or value.get("sequence") != sequence
        or type(value.get("recoveredFromEventGrowth")) is not bool
        or (
            value.get("returnCode") is not None
            and type(value.get("returnCode")) is not int
        )
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError(f"{gate} completion envelope changed")
    if path.resolve() != _completion_path(protocol, gate, sequence):
        raise ValueError(f"{gate} completion path changed")
    recovered = value["recoveredFromEventGrowth"]
    if (value.get("returnCode") is None) is not recovered:
        raise ValueError(f"{gate} completion recovery/return-code relation changed")
    created = _parse_utc(value.get("createdUtc"), f"{gate} completion createdUtc")
    if created < _parse_utc(intent.get("createdUtc"), f"{gate} intent createdUtc"):
        raise ValueError(f"{gate} completion predates intent")
    if not readiness._same_identity(value.get("intent"), readiness.identity(_intent_path(protocol, gate, sequence))):
        raise ValueError(f"{gate} completion intent changed")
    _normal_completion_growth(protocol, auth, gate, intent, value)
    snapshot = _validate_process_snapshot(
        value.get("postLaunchProcessSnapshot"),
        f"{gate} completion postlaunch snapshot",
    )
    if _parse_utc(
        snapshot["capturedUtc"], f"{gate} postlaunch capturedUtc"
    ) < created:
        raise ValueError(f"{gate} postlaunch snapshot predates completion")
    if value.get("postLaunchProtected") != _protected(
        protocol, auth, auth_path, gate
    ):
        raise ValueError(f"{gate} postlaunch protected identities changed")
    return value


def _gate_inventory(protocol: Mapping[str, Any], gate: str) -> None:
    root = _stage(protocol, gate)
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{gate} stage root is invalid")
    allowed = {"events.jsonl", "launches", "assessments"}
    if gate == "equal-time":
        allowed.add("idle-machine.attestation.json")
    for path in root.iterdir():
        if path.name not in allowed or path.is_symlink():
            raise ValueError(f"unexpected {gate} stage artifact: {path}")
    _entries(_launch_dir(protocol, gate), {"intent", "completion"})
    if _assessment_dir(protocol, gate).exists():
        for path in _assessment_dir(protocol, gate).iterdir():
            if path.is_symlink() or not path.is_file() or not re_full_sequence(path.name):
                raise ValueError(f"unexpected {gate} assessment artifact: {path}")
    evidence = _evidence_dir(protocol, gate)
    if evidence.exists():
        if evidence.is_symlink() or not evidence.is_dir():
            raise ValueError(f"{gate} adapter evidence directory is invalid")
        for path in evidence.iterdir():
            match = re.fullmatch(
                r"([0-9]{6})\.(adapter\.json|raw-prefix\.jsonl|"
                r"color-events\.jsonl|prepared-events\.jsonl|"
                r"replay-requests\.jsonl|replay-output\.jsonl|"
                r"replay-manifest\.json)",
                path.name,
            )
            if (
                path.is_symlink()
                or not path.is_file()
                or match is None
            ):
                raise ValueError(f"unexpected {gate} adapter evidence artifact: {path}")


def _predecessor_identity(
    protocol: Mapping[str, Any], gate: str
) -> dict[str, Any] | None:
    predecessor = PREDECESSOR[gate]
    if predecessor is None:
        return None
    path = _decision_path(protocol, predecessor)
    decision = _verify_decision(path, protocol, predecessor)
    if decision["passed"] is not True:
        raise ValueError(f"{gate} predecessor did not pass")
    return readiness.identity(path)


def _publish_completion(
    protocol: Mapping[str, Any],
    gate: str,
    sequence: int,
    intent: Mapping[str, Any],
    *,
    return_code: int | None,
    recovered: bool,
) -> dict[str, Any]:
    _protocol_value, auth, auth_path = _authorization(None)
    event_path = _events(protocol, gate)
    if not event_path.is_file():
        raise FileNotFoundError(f"{gate} launch produced no events")
    after = readiness.identity(event_path)
    before = intent.get("eventsBefore")
    before_bytes = 0 if before is None else before["bytes"]
    if after["bytes"] <= before_bytes:
        raise ValueError(f"{gate} launch produced no event growth")
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": COMPLETION_KIND,
        "compatibilityId": COMPAT_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": readiness._utc_now(),
        "intent": readiness.identity(_intent_path(protocol, gate, sequence)),
        "eventsAfter": after,
        "returnCode": return_code,
        "recoveredFromEventGrowth": recovered,
        "postLaunchProcessSnapshot": readiness._process_snapshot(),
        "postLaunchProtected": _protected(protocol, auth, auth_path, gate),
        "finalStageSeal": True,
    }
    # Validate the complete event boundary before the exclusive publication;
    # malformed growth is consumed by the separate immutable abort path.
    _normal_completion_growth(protocol, auth, gate, intent, value)
    _publish_json(_completion_path(protocol, gate, sequence), value)
    return _verify_completion(
        _completion_path(protocol, gate, sequence), protocol, gate, sequence, intent
    )


def _publish_abort_completion(
    protocol: Mapping[str, Any],
    gate: str,
    sequence: int,
    intent: Mapping[str, Any],
    *,
    return_code: int | None,
    cause: BaseException,
) -> dict[str, Any]:
    _protocol_value, auth, auth_path = _authorization(None)
    event_path = _events(protocol, gate)
    observed = readiness.identity(event_path) if event_path.is_file() else None
    payload = b"" if observed is None else event_path.read_bytes()
    if observed is not None and (
        len(payload) != observed["bytes"]
        or _sha256_bytes(payload) != observed["sha256"]
    ):
        raise RuntimeError(f"{gate} raw events changed during abort capture")
    before = intent.get("eventsBefore")
    before_bytes = 0 if before is None else int(before["bytes"])
    complete, tail = _abort_prefix_evidence(payload)
    growth = len(payload) - before_bytes
    if growth <= 0:
        reason = "no-event-growth"
    elif tail["bytes"]:
        reason = "partial-json-tail"
    else:
        reason = "invalid-complete-growth"
    message = str(cause).strip() or repr(cause)
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ABORT_COMPLETION_KIND,
        "compatibilityId": COMPAT_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": readiness._utc_now(),
        "intent": readiness.identity(_intent_path(protocol, gate, sequence)),
        "observedRawEvents": observed,
        "completePrefix": complete,
        "ignoredTail": tail,
        "abortReason": reason,
        "cause": {"errorType": type(cause).__name__, "message": message},
        "returnCode": return_code,
        "postLaunchProcessSnapshot": readiness._process_snapshot(),
        "postLaunchProtected": _protected(protocol, auth, auth_path, gate),
        "finalStageSeal": True,
    }
    path = _completion_path(protocol, gate, sequence)
    _publish_json(path, value)
    return _verify_abort_completion(
        path, protocol, gate, sequence, intent
    )


def _launch(args: argparse.Namespace) -> None:
    gate = args.gate
    protocol, auth, auth_path = _authorization(args.authorization, fresh=True)
    _verify_global_inventory(protocol, auth, auth_path)
    _gate_inventory(protocol, gate)
    predecessor = _predecessor_identity(protocol, gate)
    if _decision_path(protocol, gate).exists():
        raise FileExistsError(f"{gate} already has a terminal decision")
    completed, assessments, pending = _history(protocol, gate)
    if assessments and assessments[-1]["terminal"] is True:
        if pending is None and len(completed) == len(assessments):
            _publish_normal_decision(
                protocol,
                gate,
                auth_path,
                _assessment_path(protocol, gate, len(assessments)),
                assessments[-1],
            )
            print(f"Recovered {gate} terminal decision")
            return
        raise FileExistsError(f"{gate} already has a terminal assessment")
    if (
        len(completed) == len(assessments) + 1
        and completed[-1]["value"].get("aborted") is True
    ):
        _publish_abort_decision(
            protocol, gate, completed[-1]["value"]["sequence"]
        )
        print(f"Recovered {gate} terminal abort decision")
        return
    if len(completed) > len(assessments):
        raise FileExistsError(f"{gate} has a completion awaiting assessment")
    action = "run" if not assessments else "resume"
    if args.action is not None and args.action != action:
        raise ValueError(f"{gate} next action is {action}, not {args.action}")
    sequence = len(assessments) + 1
    event_path = _events(protocol, gate)
    if action == "run" and event_path.exists() and pending is None:
        raise FileExistsError(f"{gate} initial launch requires absent raw events")
    prior_events = None
    prior_assessment = None
    prior_completion = None
    if assessments:
        prior_assessment = readiness.identity(
            _assessment_path(protocol, gate, sequence - 1)
        )
        prior_events = assessments[-1]["rawEvents"]
        prior_completion = readiness.identity(
            _completion_path(protocol, gate, sequence - 1)
        )
        _prefix(event_path, prior_events, exact=pending is None)
    if pending is not None:
        intent = pending["value"]
        before = intent.get("eventsBefore")
        before_bytes = 0 if before is None else before["bytes"]
        readiness.require_idle_processes(f"{gate} pending-launch recovery")
        try:
            if not event_path.is_file() or event_path.stat().st_size <= before_bytes:
                raise RuntimeError("pending launch produced no event growth")
            _publish_completion(
                protocol,
                gate,
                sequence,
                intent,
                return_code=None,
                recovered=True,
            )
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            if _completion_path(protocol, gate, sequence).exists():
                raise
            _publish_abort_completion(
                protocol,
                gate,
                sequence,
                intent,
                return_code=None,
                cause=error,
            )
            _publish_abort_decision(protocol, gate, sequence)
            print(f"Consumed {gate} pending launch as terminal safety-fail")
            return
        print(f"Recovered {gate} launch completion {sequence}; assess next")
        return

    config_path = Path(auth["gates"][gate]["config"]["path"])
    dotnet = Path(auth["dotnetHost"]["path"])
    assembly = Path(auth["omegaMatchAssembly"]["path"])
    _contract, _core, frozen_readiness = readiness._import_frozen_modules(
        readiness._template(protocol)[1]
    )
    subprocess.run(
        [str(dotnet), str(assembly), "validate", "--config", str(config_path)],
        cwd=assembly.parent,
        env=frozen_readiness._sanitized_environment(),
        check=True,
    )
    idle = readiness.require_idle_processes(f"{gate} immediate prelaunch")
    if gate == "equal-time":
        _verify_idle(_idle_path(protocol), protocol, auth)
    budget = protocol["execution"][
        "initialPairBudget" if action == "run" else "resumePairBudget"
    ][gate]
    protected = _protected(protocol, auth, auth_path, gate)
    intent = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": INTENT_KIND,
        "compatibilityId": COMPAT_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": readiness._utc_now(),
        "authorization": readiness.identity(auth_path),
        "action": action,
        "pairBudget": budget,
        "eventsBefore": prior_events,
        "priorAssessment": prior_assessment,
        "priorCompletion": prior_completion,
        "predecessorDecision": predecessor,
        "idleAttestation": (
            readiness.identity(_idle_path(protocol))
            if gate == "equal-time"
            else None
        ),
        "preLaunchIdleSnapshot": idle,
        "protected": protected,
        "finalStageSeal": True,
    }
    _publish_json(_intent_path(protocol, gate, sequence), intent)
    _verify_intent(_intent_path(protocol, gate, sequence), protocol, gate, sequence)
    readiness.require_idle_processes(f"{gate} final launch boundary")
    if protected != _protected(protocol, auth, auth_path, gate):
        raise RuntimeError(f"{gate} protected identities changed before launch")
    try:
        result = subprocess.run(
            [
                str(dotnet),
                str(assembly),
                action,
                "--config",
                str(config_path),
                "--pair-budget",
                str(budget),
            ],
            cwd=assembly.parent,
            env=frozen_readiness._sanitized_environment(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        readiness.require_idle_processes(f"{gate} failed-launch abort")
        _publish_abort_completion(
            protocol,
            gate,
            sequence,
            intent,
            return_code=None,
            cause=error,
        )
        _publish_abort_decision(protocol, gate, sequence)
        print(f"Compatibility {gate} launch aborted as terminal safety-fail")
        return
    try:
        completion = _publish_completion(
            protocol,
            gate,
            sequence,
            intent,
            return_code=result.returncode,
            recovered=False,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        if _completion_path(protocol, gate, sequence).exists():
            raise
        readiness.require_idle_processes(f"{gate} invalid-growth abort")
        _publish_abort_completion(
            protocol,
            gate,
            sequence,
            intent,
            return_code=result.returncode,
            cause=error,
        )
        _publish_abort_decision(protocol, gate, sequence)
        print(f"Compatibility {gate} invalid growth is terminal safety-fail")
        return
    if (
        completion["postLaunchProcessSnapshot"]["relevantProcesses"]
        or completion["postLaunchProcessSnapshot"]["relevantProducerProcesses"]
    ):
        raise RuntimeError(f"{gate} left a relevant process running; assess as safety-fail")
    if result.returncode != 0:
        raise RuntimeError(f"{gate} OmegaMatch exited {result.returncode}; assess next")
    print(f"Compatibility {gate} {action} completed; assess next")


def _verify_idle(
    path: Path, protocol: Mapping[str, Any], auth: Mapping[str, Any]
) -> dict[str, Any]:
    value = readiness.strict_load(path, "equal-time idle attestation")
    expected = {
        "schemaVersion",
        "kind",
        "runId",
        "createdUtc",
        "idleMachine",
        "oneGameAtATime",
        "concurrentMatchProcesses",
        "operator",
        "authorization",
        "predecessorDecision",
        "processSnapshot",
        "finalStageSeal",
    }
    if set(value) != expected:
        raise ValueError("equal-time idle attestation fields changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != IDLE_KIND
        or value.get("runId") != auth["gates"]["equal-time"]["runId"]
        or value.get("idleMachine") is not True
        or value.get("oneGameAtATime") is not True
        or value.get("concurrentMatchProcesses") != 1
        or type(value.get("operator")) is not str
        or not value["operator"].strip()
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError("equal-time idle attestation changed")
    if path.resolve() != _idle_path(protocol):
        raise ValueError("equal-time idle attestation path changed")
    created = _parse_utc(value.get("createdUtc"), "equal-time idle createdUtc")
    _protocol_value, current_auth, auth_path = _authorization(None)
    if current_auth != auth:
        raise ValueError("equal-time idle authorization context changed")
    _canonical_artifact_identity(
        auth_path, value.get("authorization"), "equal-time idle authorization"
    )
    predecessor = _predecessor_identity(protocol, "equal-time")
    if value.get("predecessorDecision") != predecessor:
        raise ValueError("equal-time idle predecessor decision changed")
    if created < _parse_utc(auth.get("createdUtc"), "authorization createdUtc"):
        raise ValueError("equal-time idle attestation predates authorization")
    if predecessor is not None and created < _identity_created(
        predecessor, "equal-time predecessor decision"
    ):
        raise ValueError("equal-time idle attestation predates predecessor success")
    snapshot = _validate_process_snapshot(
        value.get("processSnapshot"), "equal-time idle process snapshot"
    )
    if (
        snapshot["relevantProcesses"] != []
        or snapshot["relevantProducerProcesses"] != []
    ):
        raise ValueError("equal-time idle attestation records relevant processes")
    if _parse_utc(snapshot["capturedUtc"], "idle snapshot capturedUtc") > created:
        raise ValueError("equal-time idle process snapshot postdates attestation")
    return value


def _attest_idle(args: argparse.Namespace) -> None:
    protocol, auth, auth_path = _authorization(args.authorization, fresh=True)
    _verify_global_inventory(protocol, auth, auth_path)
    path = _idle_path(protocol)
    if path.exists():
        raise FileExistsError("equal-time idle attestation already exists")
    operator = args.operator.strip()
    if not operator:
        raise ValueError("--operator must be nonempty")
    snapshot = readiness.require_idle_processes("equal-time idle attestation")
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": IDLE_KIND,
        "runId": auth["gates"]["equal-time"]["runId"],
        "createdUtc": readiness._utc_now(),
        "idleMachine": True,
        "oneGameAtATime": True,
        "concurrentMatchProcesses": 1,
        "operator": operator,
        "authorization": readiness.identity(auth_path),
        "predecessorDecision": _predecessor_identity(protocol, "equal-time"),
        "processSnapshot": snapshot,
        "finalStageSeal": True,
    }
    _publish_json(path, value)
    _verify_idle(path, protocol, auth)
    _verify_global_inventory(protocol, auth, auth_path)
    print(f"Compatibility equal-time idle attestation: {path}")


def _decision_from_report(
    gate: str, report: Mapping[str, Any], completion: Mapping[str, Any]
) -> tuple[str, bool, bool]:
    section = report["developmentScreen"] if gate == "development" else report["sequentialGate"]
    decision = str(section["decision"])
    snapshot = completion["postLaunchProcessSnapshot"]
    if (
        completion["returnCode"] != 0
        or completion["recoveredFromEventGrowth"] is True
        or snapshot["relevantProcesses"]
        or snapshot["relevantProducerProcesses"]
    ):
        decision = "safety-fail"
    terminal = decision in TERMINAL[gate]
    passed = terminal and decision == SUCCESS[gate]
    return decision, terminal, passed


def _publish_normal_decision(
    protocol: Mapping[str, Any],
    gate: str,
    auth_path: Path,
    assessment_path: Path,
    assessment: Mapping[str, Any],
) -> dict[str, Any]:
    if assessment.get("terminal") is not True:
        raise ValueError(f"{gate} cannot publish a nonterminal decision")
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": DECISION_KIND,
        "compatibilityId": COMPAT_ID,
        "gate": gate,
        "createdUtc": readiness._utc_now(),
        "authorization": readiness.identity(auth_path),
        "assessment": readiness.identity(assessment_path),
        "rawEvents": copy.deepcopy(assessment["rawEvents"]),
        "rawPrefix": copy.deepcopy(assessment["rawPrefix"]),
        "preparedEvents": copy.deepcopy(assessment["preparedEvents"]),
        "adapterEvidence": copy.deepcopy(assessment["adapterEvidence"]),
        "decision": assessment["decision"],
        "passed": assessment["passed"],
        "authorizedSuccessor": assessment["authorizedSuccessor"],
        "finalStageSeal": True,
    }
    path = _decision_path(protocol, gate)
    _publish_json(path, value)
    return _verify_decision(path, protocol, gate)


def _assess(args: argparse.Namespace) -> dict[str, Any]:
    gate = args.gate
    protocol, auth, auth_path = _authorization(args.authorization, fresh=True)
    _verify_global_inventory(protocol, auth, auth_path)
    _gate_inventory(protocol, gate)
    if _decision_path(protocol, gate).exists():
        raise FileExistsError(f"{gate} already has a terminal decision")
    completed, assessments, pending = _history(protocol, gate)
    if (
        pending is None
        and assessments
        and assessments[-1]["terminal"] is True
        and len(completed) == len(assessments)
    ):
        assessment_path = _assessment_path(
            protocol, gate, len(assessments)
        )
        _publish_normal_decision(
            protocol,
            gate,
            auth_path,
            assessment_path,
            assessments[-1],
        )
        print(f"Recovered {gate} terminal decision")
        return assessments[-1]
    if pending is not None or len(completed) != len(assessments) + 1:
        raise ValueError(f"{gate} assessment requires exactly one new completion")
    sequence = len(assessments) + 1
    completion = completed[-1]["value"]
    event_path = _events(protocol, gate)
    current = readiness.identity(event_path)
    if current != completion["eventsAfter"]:
        raise ValueError(f"{gate} raw events changed after launch completion")
    contract, core, frozen_readiness = readiness._import_frozen_modules(
        readiness._template(protocol)[1]
    )
    original_protocol = contract.validate_protocol()
    frozen_readiness._install_core_profile(original_protocol)
    original_orchestrator = _frozen_orchestrator(protocol)
    before_bytes = 0
    if sequence > 1:
        before_bytes = assessments[-1]["rawEvents"]["bytes"]
    original_orchestrator._strict_event_growth(
        event_path.read_bytes(),
        before_bytes,
        f"{gate} raw compatibility events",
        expected_run=_run_binding(auth, gate),
        allow_incomplete_tail=(
            completion["recoveredFromEventGrowth"] is True
            or completion["returnCode"] != 0
        ),
    )
    adapter = derive_events(
        event_path,
        _adapter_path(protocol, gate, sequence),
        gate=gate,
        sequence=sequence,
        authorization=auth,
        authorization_path=auth_path,
    )
    with tempfile.TemporaryDirectory(prefix="omega-g5-color-compat-assess-") as directory:
        output = Path(directory) / "core-report.json"
        namespace = argparse.Namespace(
            seal=Path(auth["coreSeal"]["path"]),
            gate=gate,
            events=Path(adapter["assessmentInput"]["path"]),
            output=output,
            idle_attestation=_idle_path(protocol) if gate == "equal-time" else None,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            report = core._assess(namespace)
    decision, terminal, passed = _decision_from_report(gate, report, completion)
    prior = (
        None
        if not assessments
        else readiness.identity(_assessment_path(protocol, gate, sequence - 1))
    )
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ASSESSMENT_KIND,
        "compatibilityId": COMPAT_ID,
        "gate": gate,
        "sequence": sequence,
        "createdUtc": readiness._utc_now(),
        "authorization": readiness.identity(auth_path),
        "completion": readiness.identity(_completion_path(protocol, gate, sequence)),
        "priorAssessment": prior,
        "rawEvents": copy.deepcopy(adapter["rawEvents"]),
        "rawPrefix": copy.deepcopy(adapter["rawPrefix"]),
        "preparedEvents": copy.deepcopy(adapter["preparedEvents"]),
        "adapterEvidence": readiness.identity(_adapter_path(protocol, gate, sequence)),
        "coreReport": report,
        "progress": copy.deepcopy(report["progress"]),
        "safety": copy.deepcopy(report["safety"]),
        "decision": decision,
        "terminal": terminal,
        "passed": passed,
        "authorizedSuccessor": (
            GATES[GATES.index(gate) + 1]
            if passed and gate != "equal-time"
            else None
        ),
        "processFreshness": {
            "preLaunch": copy.deepcopy(completed[-1]["intent"]["preLaunchIdleSnapshot"]),
            "postLaunch": copy.deepcopy(completion["postLaunchProcessSnapshot"]),
            "launcherReturnCode": completion["returnCode"],
            "recoveredFromEventGrowth": completion["recoveredFromEventGrowth"],
        },
        "finalStageSeal": True,
    }
    _publish_json(_assessment_path(protocol, gate, sequence), value)
    verified = _verify_assessment(
        _assessment_path(protocol, gate, sequence),
        protocol,
        gate,
        sequence,
        replay_core=True,
    )
    if terminal:
        _publish_normal_decision(
            protocol,
            gate,
            auth_path,
            _assessment_path(protocol, gate, sequence),
            verified,
        )
    print(f"Compatibility {gate} assessment {sequence}: {decision}")
    return verified


def _normalize_core_report(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("createdUtc", None)
    if type(result.get("events")) is dict:
        result["events"] = {
            "bytes": result["events"].get("bytes"),
            "sha256": result["events"].get("sha256"),
        }
    return result


def _verify_assessment(
    path: Path,
    protocol: Mapping[str, Any],
    gate: str,
    sequence: int,
    *,
    replay_core: bool,
) -> dict[str, Any]:
    _protocol_value, auth, auth_path = _authorization(None)
    value = readiness.strict_load(path, f"{gate} compatibility assessment")
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "gate",
        "sequence",
        "createdUtc",
        "authorization",
        "completion",
        "priorAssessment",
        "rawEvents",
        "rawPrefix",
        "preparedEvents",
        "adapterEvidence",
        "coreReport",
        "progress",
        "safety",
        "decision",
        "terminal",
        "passed",
        "authorizedSuccessor",
        "processFreshness",
        "finalStageSeal",
    }
    if set(value) != expected:
        raise ValueError(f"{gate} compatibility assessment fields changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != ASSESSMENT_KIND
        or value.get("compatibilityId") != COMPAT_ID
        or value.get("gate") != gate
        or value.get("sequence") != sequence
        or type(value.get("terminal")) is not bool
        or type(value.get("passed")) is not bool
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError(f"{gate} compatibility assessment envelope changed")
    if path.resolve() != _assessment_path(protocol, gate, sequence):
        raise ValueError(f"{gate} compatibility assessment path changed")
    created = _parse_utc(value.get("createdUtc"), f"{gate} assessment createdUtc")
    _canonical_artifact_identity(
        auth_path, value.get("authorization"), f"{gate} assessment authorization"
    )
    intent_path = _intent_path(protocol, gate, sequence)
    intent = _verify_intent(intent_path, protocol, gate, sequence)
    completion_path = _completion_path(protocol, gate, sequence)
    completion = _verify_completion(
        completion_path,
        protocol,
        gate,
        sequence,
        intent,
    )
    _canonical_artifact_identity(
        completion_path,
        value.get("completion"),
        f"{gate} assessment completion",
    )
    if created < _parse_utc(
        completion.get("createdUtc"), f"{gate} completion createdUtc"
    ):
        raise ValueError(f"{gate} assessment predates completion")
    expected_prior = (
        None
        if sequence == 1
        else readiness.identity(_assessment_path(protocol, gate, sequence - 1))
    )
    if value.get("priorAssessment") != expected_prior:
        raise ValueError(f"{gate} assessment prior identity changed")
    if expected_prior is not None and created < _identity_created(
        expected_prior, f"{gate} prior assessment"
    ):
        raise ValueError(f"{gate} assessment predates its prior assessment")
    adapter_path = readiness.verify_identity(
        value.get("adapterEvidence"), f"{gate} adapter evidence"
    )
    if adapter_path != _adapter_path(protocol, gate, sequence):
        raise ValueError(f"{gate} assessment adapter path changed")
    adapter = verify_adapter_evidence(
        adapter_path, authorization_path=auth_path
    )
    if (
        value.get("rawEvents") != adapter["rawEvents"]
        or value.get("rawPrefix") != adapter["rawPrefix"]
        or value.get("preparedEvents") != adapter["preparedEvents"]
    ):
        raise ValueError(f"{gate} assessment event bindings changed")
    if value["rawEvents"] != completion["eventsAfter"]:
        raise ValueError(f"{gate} assessment differs from completion checkpoint")
    if value.get("progress") != value["coreReport"].get("progress") or value.get(
        "safety"
    ) != value["coreReport"].get("safety"):
        raise ValueError(f"{gate} assessment summary differs from core report")
    # ``replay_core`` is retained for call-site compatibility but deliberately
    # cannot weaken verification.  Every persisted assessment is replayed.
    contract, core, frozen_readiness = readiness._import_frozen_modules(
        readiness._template(protocol)[1]
    )
    original_protocol = contract.validate_protocol()
    frozen_readiness._install_core_profile(original_protocol)
    with tempfile.TemporaryDirectory(prefix="omega-g5-color-compat-replay-") as directory:
        output = Path(directory) / "report.json"
        namespace = argparse.Namespace(
            seal=Path(auth["coreSeal"]["path"]),
            gate=gate,
            events=Path(adapter["assessmentInput"]["path"]),
            output=output,
            idle_attestation=_idle_path(protocol) if gate == "equal-time" else None,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            replay = core._assess(namespace)
    if _normalize_core_report(replay) != _normalize_core_report(value["coreReport"]):
        raise ValueError(f"{gate} core assessment replay changed")
    expected_decision, expected_terminal, expected_passed = _decision_from_report(
        gate, replay, completion
    )
    expected_successor = (
        GATES[GATES.index(gate) + 1]
        if expected_passed and gate != "equal-time"
        else None
    )
    if (
        value.get("decision") != expected_decision
        or value.get("terminal") is not expected_terminal
        or value.get("passed") is not expected_passed
        or value.get("authorizedSuccessor") != expected_successor
    ):
        raise ValueError(f"{gate} assessment decision replay changed")
    expected_freshness = {
        "preLaunch": copy.deepcopy(intent["preLaunchIdleSnapshot"]),
        "postLaunch": copy.deepcopy(completion["postLaunchProcessSnapshot"]),
        "launcherReturnCode": completion["returnCode"],
        "recoveredFromEventGrowth": completion["recoveredFromEventGrowth"],
    }
    if value.get("processFreshness") != expected_freshness:
        raise ValueError(f"{gate} assessment process-freshness binding changed")
    return value


def _verify_abort_decision(
    path: Path,
    protocol: Mapping[str, Any],
    gate: str,
    value: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _protocol_value, auth, auth_path = _authorization(None)
    document = (
        readiness.strict_load(path, f"{gate} abort decision")
        if value is None
        else dict(value)
    )
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "gate",
        "createdUtc",
        "authorization",
        "completion",
        "decision",
        "passed",
        "authorizedSuccessor",
        "finalStageSeal",
    }
    if set(document) != expected:
        raise ValueError(f"{gate} abort-decision fields changed")
    if (
        document.get("schemaVersion") != SCHEMA_VERSION
        or document.get("kind") != ABORT_DECISION_KIND
        or document.get("compatibilityId") != COMPAT_ID
        or document.get("gate") != gate
        or document.get("decision") != "safety-fail"
        or document.get("passed") is not False
        or document.get("authorizedSuccessor") is not None
        or document.get("finalStageSeal") is not True
    ):
        raise ValueError(f"{gate} abort-decision envelope changed")
    if path.resolve() != _decision_path(protocol, gate):
        raise ValueError(f"{gate} abort-decision path changed")
    created = _parse_utc(
        document.get("createdUtc"), f"{gate} abort decision createdUtc"
    )
    _canonical_artifact_identity(
        auth_path, document.get("authorization"), f"{gate} abort authorization"
    )
    completed, assessments, pending = _history(protocol, gate)
    if (
        pending is not None
        or not completed
        or completed[-1]["value"].get("aborted") is not True
        or len(completed) != len(assessments) + 1
    ):
        raise ValueError(f"{gate} abort decision is not the terminal chain tip")
    completion_path = completed[-1]["path"]
    _canonical_artifact_identity(
        completion_path,
        document.get("completion"),
        f"{gate} abort completion",
    )
    if created < _parse_utc(
        completed[-1]["value"].get("createdUtc"),
        f"{gate} abort completion createdUtc",
    ):
        raise ValueError(f"{gate} abort decision predates completion")
    return document


def _publish_abort_decision(
    protocol: Mapping[str, Any],
    gate: str,
    sequence: int,
) -> dict[str, Any]:
    _protocol_value, _auth, auth_path = _authorization(None)
    completion_path = _completion_path(protocol, gate, sequence)
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": ABORT_DECISION_KIND,
        "compatibilityId": COMPAT_ID,
        "gate": gate,
        "createdUtc": readiness._utc_now(),
        "authorization": readiness.identity(auth_path),
        "completion": readiness.identity(completion_path),
        "decision": "safety-fail",
        "passed": False,
        "authorizedSuccessor": None,
        "finalStageSeal": True,
    }
    path = _decision_path(protocol, gate)
    _publish_json(path, value)
    return _verify_abort_decision(path, protocol, gate)


def _verify_decision(
    path: Path, protocol: Mapping[str, Any], gate: str
) -> dict[str, Any]:
    _protocol_value, auth, auth_path = _authorization(None)
    value = readiness.strict_load(path, f"{gate} compatibility decision")
    if value.get("kind") == ABORT_DECISION_KIND:
        return _verify_abort_decision(path, protocol, gate, value)
    expected = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "gate",
        "createdUtc",
        "authorization",
        "assessment",
        "rawEvents",
        "rawPrefix",
        "preparedEvents",
        "adapterEvidence",
        "decision",
        "passed",
        "authorizedSuccessor",
        "finalStageSeal",
    }
    if set(value) != expected:
        raise ValueError(f"{gate} compatibility decision fields changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != DECISION_KIND
        or value.get("compatibilityId") != COMPAT_ID
        or value.get("gate") != gate
        or value.get("decision") not in TERMINAL[gate]
        or type(value.get("passed")) is not bool
        or value.get("passed") != (value["decision"] == SUCCESS[gate])
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError(f"{gate} compatibility decision envelope changed")
    if path.resolve() != _decision_path(protocol, gate):
        raise ValueError(f"{gate} compatibility decision path changed")
    created = _parse_utc(value.get("createdUtc"), f"{gate} decision createdUtc")
    _canonical_artifact_identity(
        auth_path, value.get("authorization"), f"{gate} decision authorization"
    )
    assessment_path = readiness.verify_identity(
        value.get("assessment"), f"{gate} terminal assessment"
    )
    if assessment_path.parent != _assessment_dir(protocol, gate):
        raise ValueError(f"{gate} decision assessment escaped its namespace")
    try:
        sequence = int(assessment_path.stem)
    except ValueError as error:
        raise ValueError(f"{gate} decision assessment name changed") from error
    completed, assessments, pending = _history(protocol, gate)
    if (
        pending is not None
        or len(completed) != len(assessments)
        or not assessments
        or sequence != len(assessments)
        or assessment_path != _assessment_path(protocol, gate, sequence)
    ):
        raise ValueError(f"{gate} decision is not bound to complete history")
    assessment = assessments[-1]
    if created < _parse_utc(
        assessment.get("createdUtc"), f"{gate} assessment createdUtc"
    ):
        raise ValueError(f"{gate} decision predates terminal assessment")
    _prefix(_events(protocol, gate), value.get("rawEvents"), exact=True)
    if (
        assessment["terminal"] is not True
        or assessment["decision"] != value["decision"]
        or assessment["passed"] != value["passed"]
        or assessment["rawEvents"] != value["rawEvents"]
        or assessment["rawPrefix"] != value["rawPrefix"]
        or assessment["preparedEvents"] != value["preparedEvents"]
        or assessment["adapterEvidence"] != value["adapterEvidence"]
        or value.get("authorizedSuccessor")
        != assessment.get("authorizedSuccessor")
    ):
        raise ValueError(f"{gate} decision differs from terminal assessment")
    return value


def _verify_state(args: argparse.Namespace) -> None:
    protocol, auth, auth_path = _authorization(args.authorization, fresh=True)
    _verify_global_inventory(protocol, auth, auth_path)
    for gate in GATES:
        _gate_inventory(protocol, gate)
        if not _stage(protocol, gate).exists():
            print(f"{gate}: not launched")
            continue
        completed, assessments, pending = _history(protocol, gate)
        if pending is not None:
            print(f"{gate}: pending launch intent")
        elif _decision_path(protocol, gate).exists():
            decision = _verify_decision(_decision_path(protocol, gate), protocol, gate)
            print(f"{gate}: terminal {decision['decision']}")
        elif len(completed) > len(assessments):
            print(f"{gate}: completion awaiting assessment")
        elif assessments:
            print(f"{gate}: {assessments[-1]['decision']} at {assessments[-1]['progress']['completePairs']} pairs")
        else:
            print(f"{gate}: no completed launch")
    closure = readiness._namespace(protocol, "closure")
    if closure.exists():
        value = _verify_closure(closure, protocol, auth, auth_path)
        print(f"closure: clearlySuperior={value['clearlySuperior']}")


def _linklike(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def _verify_global_inventory(
    protocol: Mapping[str, Any],
    auth: Mapping[str, Any],
    auth_path: Path,
) -> None:
    """Reject every unbound post-authorization namespace artifact."""

    root = readiness._namespace(protocol, "root")
    preregistration = readiness._namespace(protocol, "preregistration")
    sealed = readiness._namespace(protocol, "sealed")
    evidence_root = readiness._namespace(protocol, "evidence")
    decisions_root = readiness._namespace(protocol, "decisions")
    projection_path = readiness._namespace(protocol, "historyProjection")
    manifest_path = readiness._namespace(protocol, "historyManifest")
    closure_path = readiness._namespace(protocol, "closure")
    stages = readiness._stage_paths(protocol)
    suites = readiness._suite_paths(protocol)
    configs = readiness._config_paths(protocol)

    direct_directories = {
        sealed,
        evidence_root,
        decisions_root,
        *stages.values(),
    }
    direct_files = {
        preregistration,
        projection_path,
        manifest_path,
        closure_path,
    }
    if (
        any(path.parent != root for path in direct_directories | direct_files)
        or any(path.parent != sealed for path in suites.values())
        or any(path.parent != sealed for path in configs.values())
        or readiness._namespace(protocol, "suiteSeal").parent != sealed
        or readiness._namespace(protocol, "preauthorizationState").parent
        != sealed
        or readiness._namespace(protocol, "authorization").parent != sealed
        or readiness._namespace(protocol, "coreSeal").parent != sealed
    ):
        raise ValueError("compatibility global namespace layout changed")
    if _linklike(root) or not root.is_dir():
        raise ValueError("compatibility global root is invalid")

    actual_top: set[Path] = set()
    for path in root.iterdir():
        if _linklike(path):
            raise ValueError(f"compatibility global root contains a link: {path}")
        if not path.is_file() and not path.is_dir():
            raise ValueError(
                f"compatibility global root contains a special entry: {path}"
            )
        actual_top.add(path)
    allowed_top = direct_directories | direct_files
    if not actual_top.issubset(allowed_top):
        raise ValueError("compatibility global root inventory changed")
    if not {preregistration, sealed}.issubset(actual_top):
        raise ValueError("compatibility authorized root lacks its foundation")
    if _linklike(preregistration) or not preregistration.is_file():
        raise ValueError("compatibility preregistration is not a regular file")
    if _linklike(sealed) or not sealed.is_dir():
        raise ValueError("compatibility sealed namespace is invalid")

    expected_sealed = {
        readiness._namespace(protocol, "suiteSeal"),
        readiness._namespace(protocol, "preauthorizationState"),
        readiness._namespace(protocol, "authorization"),
        readiness._namespace(protocol, "coreSeal"),
        *suites.values(),
        *configs.values(),
    }
    actual_sealed: set[Path] = set()
    for path in sealed.iterdir():
        if _linklike(path) or not path.is_file():
            raise ValueError(
                f"compatibility sealed namespace contains a non-file: {path}"
            )
        actual_sealed.add(path)
    if actual_sealed != expected_sealed:
        raise ValueError("compatibility sealed inventory changed")
    if auth_path.resolve() != readiness._namespace(protocol, "authorization"):
        raise ValueError("compatibility global authorization path changed")

    existing_stages = [gate for gate in GATES if stages[gate].exists()]
    if existing_stages != list(GATES[: len(existing_stages)]):
        raise ValueError("compatibility stage roots are not an exact prefix")
    for gate in existing_stages:
        stage = stages[gate]
        if _linklike(stage) or not stage.is_dir():
            raise ValueError(f"{gate} compatibility stage root is invalid")
        events = _events(protocol, gate)
        launches = _launch_dir(protocol, gate)
        assessments = _assessment_dir(protocol, gate)
        idle = _idle_path(protocol)
        for path in stage.iterdir():
            expected = {events, launches, assessments}
            if gate == "equal-time":
                expected.add(idle)
            if path not in expected or _linklike(path):
                raise ValueError(f"unexpected {gate} global stage artifact: {path}")
            if path in {launches, assessments} and not path.is_dir():
                raise ValueError(f"{gate} transaction namespace is not a directory")
            if path in {events, idle} and not path.is_file():
                raise ValueError(f"{gate} stage artifact is not a regular file")
        _gate_inventory(protocol, gate)
        _history(protocol, gate)

    if evidence_root.exists():
        if _linklike(evidence_root) or not evidence_root.is_dir():
            raise ValueError("compatibility evidence root is invalid")
        expected_evidence_roots = {
            gate: _evidence_dir(protocol, gate) for gate in GATES
        }
        reverse_evidence = {
            path: gate for gate, path in expected_evidence_roots.items()
        }
        for path in evidence_root.iterdir():
            gate = reverse_evidence.get(path)
            if gate is None or _linklike(path) or not path.is_dir():
                raise ValueError(
                    f"unexpected compatibility evidence-root artifact: {path}"
                )
            if gate not in existing_stages:
                raise ValueError(f"{gate} evidence exists without its stage")
            # ``_history`` above independently checks every exact filename,
            # file type, transaction sequence, and admissible staged subset.

    decision_gates: list[str] = []
    decision_values: dict[str, dict[str, Any]] = {}
    if decisions_root.exists():
        if _linklike(decisions_root) or not decisions_root.is_dir():
            raise ValueError("compatibility decisions root is invalid")
        expected_decisions = {
            _decision_path(protocol, gate): gate for gate in GATES
        }
        for path in decisions_root.iterdir():
            gate = expected_decisions.get(path)
            if gate is None or _linklike(path) or not path.is_file():
                raise ValueError(
                    f"unexpected compatibility decision artifact: {path}"
                )
        decision_gates = [
            gate for gate in GATES if _decision_path(protocol, gate).exists()
        ]
        if decision_gates != list(GATES[: len(decision_gates)]):
            raise ValueError("compatibility decisions are not an exact prefix")
        for gate in decision_gates:
            if gate not in existing_stages:
                raise ValueError(f"{gate} decision exists without its stage")
            decision_values[gate] = _verify_decision(
                _decision_path(protocol, gate), protocol, gate
            )

    projection_exists = projection_path.exists()
    manifest_exists = manifest_path.exists()
    closure_exists = closure_path.exists()
    admissible_stage_states = [decision_gates]
    if (
        not closure_exists
        and len(decision_gates) < len(GATES)
        and (
            not decision_gates
            or decision_values[decision_gates[-1]]["passed"] is True
        )
    ):
        admissible_stage_states.append(
            [*decision_gates, GATES[len(decision_gates)]]
        )
    if existing_stages not in admissible_stage_states:
        raise ValueError(
            "compatibility stage roots do not match a known transaction state"
        )
    if manifest_exists and not projection_exists:
        raise ValueError("position-history manifest lacks its projection")
    if closure_exists and not (projection_exists and manifest_exists):
        raise ValueError("compatibility closure lacks complete position history")
    if projection_exists:
        if not decision_gates:
            raise ValueError("position history exists without a terminal decision")
        rows, _sources, _decisions = _position_history_material(
            protocol, decision_gates
        )
        if projection_path.read_bytes() != _serialize_records(rows):
            raise ValueError("compatibility position-history projection changed")
    if manifest_exists:
        _verify_position_history(
            protocol, decision_gates, auth_path, auth["selectedNetwork"]
        )
    if closure_exists:
        _verify_closure(closure_path, protocol, auth, auth_path)


def _position_history_material(
    protocol: Mapping[str, Any], decision_gates: Sequence[str]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Project every authenticated reached position from each terminal raw prefix."""

    contract, core, frozen_readiness = readiness._import_frozen_modules(
        readiness._template(protocol)[1]
    )
    original_protocol = contract.validate_protocol()
    frozen_readiness._install_core_profile(original_protocol)
    rows: list[dict[str, Any]] = []
    sources: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}
    occurrence = 0
    ordered_gates = list(decision_gates)
    selected_gates = set(ordered_gates)
    if (
        len(selected_gates) != len(ordered_gates)
        or ordered_gates != list(GATES[: len(ordered_gates)])
    ):
        raise ValueError("position-history gate inventory changed")
    for gate in GATES:
        if gate not in selected_gates:
            continue
        decision_path = _decision_path(protocol, gate)
        _verify_decision(decision_path, protocol, gate)
        decisions[gate] = readiness.identity(decision_path)
        _completed, assessments, pending = _history(protocol, gate)
        if pending is not None:
            raise ValueError(f"{gate} position history has a pending launch")
        if not assessments:
            # A first-launch abort has no authenticated game transcript.
            continue
        source = copy.deepcopy(assessments[-1]["rawPrefix"])
        source_path = readiness.verify_identity(
            source, f"{gate} position-history raw prefix"
        )
        sources[gate] = source
        records = _event_records(
            source_path.read_bytes(), f"{gate} position-history raw prefix"
        )
        gate_occurrences_before = occurrence
        for line_number, record in enumerate(records, 1):
            record_type = record.get("RecordType")
            positions: list[tuple[str, Any, Any]] = []
            if record_type == "gameStart":
                positions.append(("initial", record.get("InitialOfen"), 0))
            elif record_type == "ply" and record.get("PostOfen") not in (None, ""):
                positions.append(("post-move", record.get("PostOfen"), record.get("Ply")))
            for role, raw_ofen, ply in positions:
                if type(raw_ofen) is not str or not raw_ofen.strip():
                    raise ValueError(f"{gate} position-history OFEN is malformed")
                phase, side, position_identity, orbit, signatures = core._position_meta(
                    raw_ofen
                )
                if (
                    type(phase) is not str
                    or type(side) is not str
                    or type(position_identity) is not str
                    or type(orbit) is not str
                    or not isinstance(signatures, tuple)
                    or any(type(item) is not str for item in signatures)
                ):
                    raise ValueError(f"{gate} frozen position metadata changed")
                occurrence += 1
                rows.append(
                    {
                        "schemaVersion": 1,
                        "kind": "omega-nnue-king-state-v5-compat-position-history-row",
                        "occurrence": occurrence,
                        "gate": gate,
                        "sourceRawPrefixSha256": source["sha256"],
                        "sourceLine": line_number,
                        "recordType": record_type,
                        "gameId": record.get("GameId"),
                        "attempt": record.get("Attempt"),
                        "ply": ply,
                        "positionRole": role,
                        "ofen": raw_ofen,
                        "phase": phase,
                        "sideToMove": side,
                        "positionIdentity": position_identity,
                        "symmetryOrbitKey": orbit,
                        "orbitSignatures": list(signatures),
                    }
                )
        if occurrence == gate_occurrences_before:
            raise ValueError(
                f"{gate} authenticated position history contains no positions"
            )
    return rows, sources, decisions


def _verify_position_history(
    protocol: Mapping[str, Any],
    decision_gates: Sequence[str],
    auth_path: Path,
    selected_network: Mapping[str, Any],
) -> dict[str, Any]:
    projection_path = readiness._namespace(protocol, "historyProjection")
    manifest_path = readiness._namespace(protocol, "historyManifest")
    for path, label in (
        (projection_path, "compatibility position-history projection"),
        (manifest_path, "compatibility position-history manifest"),
    ):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{label} is not an exact regular file")
    rows, sources, decisions = _position_history_material(
        protocol, decision_gates
    )
    expected_payload = _serialize_records(rows)
    if projection_path.read_bytes() != expected_payload:
        raise ValueError("compatibility position-history projection changed")
    manifest = readiness.strict_load(
        manifest_path, "compatibility position-history manifest"
    )
    expected_fields = {
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
    if set(manifest) != expected_fields:
        raise ValueError("compatibility position-history manifest fields changed")
    created = _parse_utc(
        manifest.get("createdUtc"), "position-history manifest createdUtc"
    )
    expected = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v5-compat-position-history-manifest",
        "compatibilityId": COMPAT_ID,
        "createdUtc": manifest["createdUtc"],
        "protocol": readiness.identity(readiness.PROTOCOL_PATH),
        "authorization": readiness.identity(auth_path),
        "selectedNetwork": copy.deepcopy(dict(selected_network)),
        "decisions": decisions,
        "sources": sources,
        "projection": readiness.identity(projection_path),
        "rows": len(rows),
        "uniquePositionIdentities": len(
            {row["positionIdentity"] for row in rows}
        ),
        "uniqueOrbitSignatures": len(
            {
                signature
                for row in rows
                for signature in row["orbitSignatures"]
            }
        ),
        "recordSchema": (
            "gameStart.InitialOfen plus every successful ply.PostOfen from "
            "the final authenticated raw prefix of each terminal gate"
        ),
        "producer": {
            "readiness": copy.deepcopy(protocol["tools"]["readiness"]),
            "matches": copy.deepcopy(protocol["tools"]["matches"]),
        },
        "originalArtifactsRewritten": 0,
        "finalStageSeal": True,
    }
    if manifest != expected:
        raise ValueError("compatibility position-history manifest changed")
    if created < _parse_utc(
        readiness.strict_load(auth_path, "history authorization").get("createdUtc"),
        "history authorization createdUtc",
    ):
        raise ValueError("position-history manifest predates authorization")
    for gate in decisions:
        decision = readiness.strict_load(
            _decision_path(protocol, gate), f"{gate} history decision"
        )
        if created < _parse_utc(
            decision.get("createdUtc"), f"{gate} history decision createdUtc"
        ):
            raise ValueError("position-history manifest predates a bound decision")
    return manifest


def _publish_position_history(
    protocol: Mapping[str, Any],
    decision_gates: Sequence[str],
    auth_path: Path,
    selected_network: Mapping[str, Any],
) -> dict[str, Any]:
    projection_path = readiness._namespace(protocol, "historyProjection")
    manifest_path = readiness._namespace(protocol, "historyManifest")
    rows, sources, decisions = _position_history_material(
        protocol, decision_gates
    )
    _exclusive_bytes(projection_path, _serialize_records(rows))
    if not manifest_path.exists():
        value = {
            "schemaVersion": 1,
            "kind": "omega-nnue-king-state-v5-compat-position-history-manifest",
            "compatibilityId": COMPAT_ID,
            "createdUtc": readiness._utc_now(),
            "protocol": readiness.identity(readiness.PROTOCOL_PATH),
            "authorization": readiness.identity(auth_path),
            "selectedNetwork": copy.deepcopy(dict(selected_network)),
            "decisions": decisions,
            "sources": sources,
            "projection": readiness.identity(projection_path),
            "rows": len(rows),
            "uniquePositionIdentities": len(
                {row["positionIdentity"] for row in rows}
            ),
            "uniqueOrbitSignatures": len(
                {
                    signature
                    for row in rows
                    for signature in row["orbitSignatures"]
                }
            ),
            "recordSchema": (
                "gameStart.InitialOfen plus every successful ply.PostOfen from "
                "the final authenticated raw prefix of each terminal gate"
            ),
            "producer": {
                "readiness": copy.deepcopy(protocol["tools"]["readiness"]),
                "matches": copy.deepcopy(protocol["tools"]["matches"]),
            },
            "originalArtifactsRewritten": 0,
            "finalStageSeal": True,
        }
        _publish_json(manifest_path, value)
    return _verify_position_history(
        protocol, decision_gates, auth_path, selected_network
    )


def _verify_closure(
    path: Path,
    protocol: Mapping[str, Any],
    auth: Mapping[str, Any],
    auth_path: Path,
) -> dict[str, Any]:
    value = readiness.strict_load(path, "compatibility closure")
    expected_fields = {
        "schemaVersion",
        "kind",
        "compatibilityId",
        "createdUtc",
        "protocol",
        "authorization",
        "selectedNetwork",
        "decisions",
        "positionHistoryProjection",
        "positionHistoryManifest",
        "clearlySuperior",
        "rule",
        "originalArtifactsRewritten",
        "finalStageSeal",
    }
    if set(value) != expected_fields:
        raise ValueError("compatibility closure field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != CLOSURE_KIND
        or value.get("compatibilityId") != COMPAT_ID
        or type(value.get("clearlySuperior")) is not bool
        or value.get("rule")
        != (
            "development pass plus equal-node promote plus equal-time promote, "
            "all with zero safety failures"
        )
        or value.get("originalArtifactsRewritten") != 0
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError("compatibility closure envelope changed")
    if path.resolve() != readiness._namespace(protocol, "closure"):
        raise ValueError("compatibility closure path changed")
    created = _parse_utc(value.get("createdUtc"), "compatibility closure createdUtc")
    if not readiness._same_identity(
        value.get("protocol"), readiness.identity(readiness.PROTOCOL_PATH)
    ):
        raise ValueError("compatibility closure protocol changed")
    _canonical_artifact_identity(
        auth_path, value.get("authorization"), "closure authorization"
    )
    if value.get("selectedNetwork") != auth["selectedNetwork"]:
        raise ValueError("compatibility closure selected network changed")
    decisions = value.get("decisions")
    if type(decisions) is not dict or not set(decisions).issubset(set(GATES)):
        raise ValueError("compatibility closure decision inventory changed")
    expected_decisions: dict[str, dict[str, Any]] = {}
    expected_values: dict[str, dict[str, Any]] = {}
    stopped = False
    for gate in GATES:
        decision_path = _decision_path(protocol, gate)
        if stopped:
            if decision_path.exists() or gate in decisions:
                raise ValueError("closure contains a later gate after failure")
            continue
        if not decision_path.is_file():
            raise ValueError(f"closure lacks terminal {gate} decision")
        decision = _verify_decision(decision_path, protocol, gate)
        expected_values[gate] = decision
        expected_decisions[gate] = readiness.identity(decision_path)
        if created < _parse_utc(
            decision.get("createdUtc"), f"{gate} decision createdUtc"
        ):
            raise ValueError("compatibility closure predates a decision")
        if decision["passed"] is not True:
            stopped = True
    if decisions != expected_decisions:
        raise ValueError("compatibility closure decision identities changed")
    history = _verify_position_history(
        protocol, list(expected_decisions), auth_path, auth["selectedNetwork"]
    )
    projection_path = readiness.verify_identity(
        value.get("positionHistoryProjection"),
        "closure position-history projection",
    )
    manifest_path = readiness.verify_identity(
        value.get("positionHistoryManifest"),
        "closure position-history manifest",
    )
    if (
        projection_path != readiness._namespace(protocol, "historyProjection")
        or manifest_path != readiness._namespace(protocol, "historyManifest")
        or value.get("positionHistoryProjection") != history["projection"]
        or value.get("positionHistoryManifest")
        != readiness.identity(manifest_path)
        or created
        < _parse_utc(
            history["createdUtc"], "position-history manifest createdUtc"
        )
    ):
        raise ValueError("compatibility closure position history changed")
    clearly_superior = set(expected_values) == set(GATES) and all(
        expected_values[gate]["passed"] is True for gate in GATES
    )
    if value["clearlySuperior"] is not clearly_superior:
        raise ValueError("compatibility closure superiority conclusion changed")
    return value


def _close(args: argparse.Namespace) -> dict[str, Any]:
    protocol, auth, auth_path = _authorization(args.authorization, fresh=True)
    _verify_global_inventory(protocol, auth, auth_path)
    path = readiness._namespace(protocol, "closure")
    if path.exists():
        raise FileExistsError("compatibility closure already exists")
    decisions: dict[str, dict[str, Any]] = {}
    stopped = False
    for gate in GATES:
        decision_path = _decision_path(protocol, gate)
        if stopped:
            if decision_path.exists():
                raise ValueError("later decision exists after a failed predecessor")
            continue
        if not decision_path.exists():
            raise ValueError(f"cannot close while {gate} lacks a terminal decision")
        decision = _verify_decision(decision_path, protocol, gate)
        decisions[gate] = readiness.identity(decision_path)
        if decision["passed"] is not True:
            stopped = True
    clearly_superior = set(decisions) == set(GATES) and all(
        _verify_decision(_decision_path(protocol, gate), protocol, gate)["passed"]
        for gate in GATES
    )
    history = _publish_position_history(
        protocol, list(decisions), auth_path, auth["selectedNetwork"]
    )
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": CLOSURE_KIND,
        "compatibilityId": COMPAT_ID,
        "createdUtc": readiness._utc_now(),
        "protocol": readiness.identity(readiness.PROTOCOL_PATH),
        "authorization": readiness.identity(auth_path),
        "selectedNetwork": copy.deepcopy(auth["selectedNetwork"]),
        "decisions": decisions,
        "positionHistoryProjection": copy.deepcopy(history["projection"]),
        "positionHistoryManifest": readiness.identity(
            readiness._namespace(protocol, "historyManifest")
        ),
        "clearlySuperior": clearly_superior,
        "rule": "development pass plus equal-node promote plus equal-time promote, all with zero safety failures",
        "originalArtifactsRewritten": 0,
        "finalStageSeal": True,
    }
    _publish_json(path, value)
    verified = _verify_closure(path, protocol, auth, auth_path)
    _verify_global_inventory(protocol, auth, auth_path)
    print(f"Compatibility closure: {path}")
    return verified


def _expect_self_test_rejection(action: Any, label: str) -> None:
    try:
        action()
    except (FileNotFoundError, RuntimeError, ValueError):
        return
    raise AssertionError(f"{label} was accepted")


def _self_test_rules_helper(protocol: Mapping[str, Any]) -> None:
    """Run the pinned helper's adversarial legality/parity suite."""

    bundle = readiness.verify_rules_replay(protocol)
    _template_path, template = readiness._template(protocol)
    original_protocol_path = readiness.verify_identity(
        template["immutableGeneration5Authority"]["matchProtocol"],
        "self-test frozen G5 match protocol",
    )
    original_protocol = readiness.strict_load(
        original_protocol_path, "self-test frozen G5 match protocol"
    )
    dotnet = readiness.verify_identity(
        original_protocol["runtime"]["dotnetHost"],
        "self-test frozen G5 dotnet host",
    )
    assembly = Path(bundle["root"]) / str(bundle["assemblyRelativePath"])
    completed = subprocess.run(
        [str(dotnet), str(assembly), "--self-test"],
        cwd=assembly.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    # The pinned helper suite includes illegal moves, terminal continuation,
    # and a same-length forged expected-position transcript.
    if (
        completed.returncode != 0
        or "OmegaOpeningPrefixReplay self-test passed." not in completed.stdout
        or completed.stderr.strip()
    ):
        raise AssertionError(
            "pinned G5 rules-replay adversarial self-test failed: "
            f"rc={completed.returncode}, stderr={completed.stderr!r}"
        )


def _self_test_zero_ply_completed_game() -> None:
    suite = {
        "openings": [
            {
                "id": "synthetic-opening",
                "initialOfen": "synthetic-ofen w - - 0 1",
                "moves": [],
            }
        ]
    }
    config = {
        "runId": "synthetic-run",
        "profileId": "synthetic-profile",
        "freshnessMarker": "synthetic-freshness",
        "seed": 7,
        "match": {"mode": "nodes", "nodes": 1},
    }
    suite_identity = {
        "path": "synthetic-suite.json",
        "bytes": 1,
        "sha256": "1" * 64,
    }
    config_identity = {
        "path": "synthetic-config.json",
        "bytes": 1,
        "sha256": "2" * 64,
    }
    start = {
        "RecordType": "gameStart",
        "GameId": "synthetic-opening-r001-ab",
        "PairId": "synthetic-opening-r001",
        "Attempt": 1,
        "OpeningId": "synthetic-opening",
        "WhiteEngineId": "nnue-candidate",
        "BlackEngineId": "hce-control",
        "InitialOfen": "synthetic-ofen w - - 0 1",
        "OpeningMoves": [],
    }
    records = [
        {
            "RecordType": "run",
            "RunId": config["runId"],
            "ProfileId": config["profileId"],
            "FreshnessMarker": config["freshnessMarker"],
            "ConfigSha256": config_identity["sha256"],
            "OpeningSuiteSha256": suite_identity["sha256"],
            "Seed": config["seed"],
            "Match": {},
        },
        start,
        {
            "RecordType": "gameResult",
            **{
                key: start[key]
                for key in (
                    "GameId",
                    "PairId",
                    "Attempt",
                    "OpeningId",
                    "WhiteEngineId",
                    "BlackEngineId",
                )
            },
            "Plies": 0,
            "IllegalMoves": 0,
            "ProtocolFailures": 0,
            "TimeForfeits": 0,
        },
    ]
    core = types.SimpleNamespace(_shuffled_indices=lambda count, seed: [0])
    _expect_self_test_rejection(
        lambda: _authenticate_raw_event_schedule(
            records=records,
            suite=suite,
            config=config,
            suite_identity=suite_identity,
            config_identity=config_identity,
            gate="development",
            core=core,
        ),
        "zero-ply zero-safety completed game",
    )
    malformed_counter_records = copy.deepcopy(records)
    malformed_counter_records[-1]["IllegalMoves"] = True
    _expect_self_test_rejection(
        lambda: _authenticate_raw_event_schedule(
            records=malformed_counter_records,
            suite=suite,
            config=config,
            suite_identity=suite_identity,
            config_identity=config_identity,
            gate="development",
            core=core,
        ),
        "boolean safety counter",
    )


def _self_test_persisted_state_chain() -> None:
    """Exercise every persisted transition verifier plus hostile mutations."""

    def write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            (
                json.dumps(value, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
        )

    with tempfile.TemporaryDirectory(
        prefix="omega-g5-compat-state-selftest-", dir=readiness.REPO
    ) as directory:
        root = Path(directory).resolve()

        def repo_value(path: Path) -> str:
            return path.resolve().relative_to(readiness.REPO).as_posix()

        sealed = root / "sealed"
        decisions = root / "decisions"
        evidence = root / "evidence"
        synthetic_protocol = {
            "tools": {
                "readiness": readiness.identity(Path(readiness.__file__)),
                "matches": readiness.identity(Path(__file__)),
            },
            "namespaces": {
                "root": repo_value(root),
                "preregistration": repo_value(root / "preregistration.json"),
                "sealed": repo_value(sealed),
                "suiteSeal": repo_value(sealed / "suite-seal.json"),
                "preauthorizationState": repo_value(
                    sealed / "preauthorization-state.json"
                ),
                "authorization": repo_value(sealed / "authorization.json"),
                "coreSeal": repo_value(sealed / "core-seal.json"),
                "development": repo_value(root / "development"),
                "equalNode": repo_value(root / "equal-node"),
                "equalTime": repo_value(root / "equal-time"),
                "evidence": repo_value(evidence),
                "decisions": repo_value(decisions),
                "historyProjection": repo_value(root / "position-history.jsonl"),
                "historyManifest": repo_value(
                    root / "position-history.manifest.json"
                ),
                "closure": repo_value(root / "closure.json"),
            },
            "execution": {
                "initialPairBudget": {
                    "development": 64,
                    "equal-node": 128,
                    "equal-time": 128,
                },
                "resumePairBudget": {
                    "development": 4,
                    "equal-node": 4,
                    "equal-time": 4,
                },
            },
        }
        t0 = "2026-01-01T00:00:00Z"
        t1 = "2026-01-01T00:00:01Z"
        t2 = "2026-01-01T00:00:02Z"
        t3 = "2026-01-01T00:00:03Z"
        t4 = "2026-01-01T00:00:04Z"
        t5 = "2026-01-01T00:00:05Z"
        for path in (
            readiness._namespace(synthetic_protocol, "preregistration"),
            readiness._namespace(synthetic_protocol, "suiteSeal"),
            readiness._namespace(synthetic_protocol, "preauthorizationState"),
            *readiness._suite_paths(synthetic_protocol).values(),
        ):
            write_json(path, {"synthetic": path.name})
        config_paths = readiness._config_paths(synthetic_protocol)
        for config_gate, path in config_paths.items():
            write_json(
                path,
                {
                    "runId": "synthetic-run",
                    "profileId": "synthetic-profile",
                    "freshnessMarker": "synthetic-freshness",
                    "gate": config_gate,
                },
            )
        config_path = config_paths["development"]
        core_path = readiness._namespace(synthetic_protocol, "coreSeal")
        write_json(core_path, {"synthetic": True})
        auth_path = readiness._namespace(synthetic_protocol, "authorization")
        write_json(auth_path, {"createdUtc": t0, "synthetic": True})
        auth = {
            "createdUtc": t0,
            "selectedNetwork": {"syntheticNetwork": True},
            "coreSeal": readiness.identity(core_path),
            "gates": {
                gate: {"config": readiness.identity(config_paths[gate])}
                for gate in GATES
            },
        }
        protected = {"syntheticProtectedBoundary": True}

        def snapshot(when: str) -> dict[str, Any]:
            return {
                "capturedUtc": when,
                "method": "synthetic persisted-chain fixture",
                "relevantNames": list(readiness.RELEVANT_PROCESS_NAMES),
                "relevantProducerPatterns": list(
                    readiness.RELEVANT_PRODUCER_PATTERNS
                ),
                "relevantProcesses": [],
                "relevantProducerProcesses": [],
            }

        gate = "development"
        sequence = 1
        stage = _stage(synthetic_protocol, gate)
        launch_dir = _launch_dir(synthetic_protocol, gate)
        assessment_dir = _assessment_dir(synthetic_protocol, gate)
        evidence_dir = _evidence_dir(synthetic_protocol, gate)
        for path in (stage, launch_dir, assessment_dir, evidence_dir, decisions):
            path.mkdir(parents=True, exist_ok=True)
        intent_path = _intent_path(synthetic_protocol, gate, sequence)
        intent = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": INTENT_KIND,
            "compatibilityId": COMPAT_ID,
            "gate": gate,
            "sequence": sequence,
            "createdUtc": t1,
            "authorization": readiness.identity(auth_path),
            "action": "run",
            "pairBudget": 64,
            "eventsBefore": None,
            "priorAssessment": None,
            "priorCompletion": None,
            "predecessorDecision": None,
            "idleAttestation": None,
            "preLaunchIdleSnapshot": snapshot(t0),
            "protected": protected,
            "finalStageSeal": True,
        }
        write_json(intent_path, intent)
        event_path = _events(synthetic_protocol, gate)
        event_path.write_bytes(
            _serialize_records(
                [
                    {"RecordType": "run"},
                    {
                        "RecordType": "gameStart",
                        "GameId": "synthetic-game",
                        "Attempt": 1,
                        "InitialOfen": "synthetic-ofen w - - 0 1",
                    },
                ]
            )
        )
        completion_path = _completion_path(synthetic_protocol, gate, sequence)
        completion = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": COMPLETION_KIND,
            "compatibilityId": COMPAT_ID,
            "gate": gate,
            "sequence": sequence,
            "createdUtc": t2,
            "intent": readiness.identity(intent_path),
            "eventsAfter": readiness.identity(event_path),
            "returnCode": 0,
            "recoveredFromEventGrowth": False,
            "postLaunchProcessSnapshot": snapshot(t2),
            "postLaunchProtected": protected,
            "finalStageSeal": True,
        }
        write_json(completion_path, completion)

        raw_prefix_path = _raw_prefix_path(synthetic_protocol, gate, sequence)
        raw_prefix_path.write_bytes(event_path.read_bytes())
        color_path = _color_path(synthetic_protocol, gate, sequence)
        color_terminal = [
            {"RecordType": "run"},
            {
                "RecordType": "ply",
                "Color": "w",
                "Ply": 1,
                "PostOfen": None,
                "Search": {"ProcessExited": True},
            },
        ]
        color_path.write_bytes(_serialize_records(color_terminal))
        prepared_terminal, _terminal_summary = _derive_terminal_records(
            color_terminal, expected_transforms=1
        )
        prepared_path = _prepared_path(synthetic_protocol, gate, sequence)
        prepared_path.write_bytes(_serialize_records(prepared_terminal))
        _replay_request_path(synthetic_protocol, gate, sequence).write_bytes(b'{}\n')
        _replay_output_path(synthetic_protocol, gate, sequence).write_bytes(b'{}\n')
        write_json(_replay_manifest_path(synthetic_protocol, gate, sequence), {})
        adapter_path = _adapter_path(synthetic_protocol, gate, sequence)
        write_json(adapter_path, {"synthetic": True})
        adapter = {
            "rawEvents": readiness.identity(event_path),
            "rawPrefix": readiness.identity(raw_prefix_path),
            "preparedEvents": readiness.identity(prepared_path),
            "assessmentInput": readiness.identity(prepared_path),
        }
        core_report = {
            "createdUtc": t3,
            "events": readiness.identity(prepared_path),
            "progress": {"completePairs": 64},
            "safety": {"terminalProcessExits": 1},
            "developmentScreen": {"decision": "safety-fail"},
            "sequentialGate": {"decision": "inconclusive"},
        }
        assessment_path = _assessment_path(synthetic_protocol, gate, sequence)
        assessment = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": ASSESSMENT_KIND,
            "compatibilityId": COMPAT_ID,
            "gate": gate,
            "sequence": sequence,
            "createdUtc": t3,
            "authorization": readiness.identity(auth_path),
            "completion": readiness.identity(completion_path),
            "priorAssessment": None,
            "rawEvents": adapter["rawEvents"],
            "rawPrefix": adapter["rawPrefix"],
            "preparedEvents": adapter["preparedEvents"],
            "adapterEvidence": readiness.identity(adapter_path),
            "coreReport": core_report,
            "progress": core_report["progress"],
            "safety": core_report["safety"],
            "decision": "safety-fail",
            "terminal": True,
            "passed": False,
            "authorizedSuccessor": None,
            "processFreshness": {
                "preLaunch": intent["preLaunchIdleSnapshot"],
                "postLaunch": completion["postLaunchProcessSnapshot"],
                "launcherReturnCode": 0,
                "recoveredFromEventGrowth": False,
            },
            "finalStageSeal": True,
        }
        write_json(assessment_path, assessment)
        decision_path = _decision_path(synthetic_protocol, gate)
        decision = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": DECISION_KIND,
            "compatibilityId": COMPAT_ID,
            "gate": gate,
            "createdUtc": t4,
            "authorization": readiness.identity(auth_path),
            "assessment": readiness.identity(assessment_path),
            "rawEvents": adapter["rawEvents"],
            "rawPrefix": adapter["rawPrefix"],
            "preparedEvents": adapter["preparedEvents"],
            "adapterEvidence": readiness.identity(adapter_path),
            "decision": "safety-fail",
            "passed": False,
            "authorizedSuccessor": None,
            "finalStageSeal": True,
        }
        write_json(decision_path, decision)
        history_projection_path = readiness._namespace(
            synthetic_protocol, "historyProjection"
        )
        history_rows = [
            {
                "schemaVersion": 1,
                "kind": "omega-nnue-king-state-v5-compat-position-history-row",
                "occurrence": 1,
                "gate": gate,
                "sourceRawPrefixSha256": adapter["rawPrefix"]["sha256"],
                "sourceLine": 2,
                "recordType": "gameStart",
                "gameId": "synthetic-game",
                "attempt": 1,
                "ply": 0,
                "positionRole": "initial",
                "ofen": "synthetic-ofen w - - 0 1",
                "phase": "opening",
                "sideToMove": "w",
                "positionIdentity": "synthetic-position-identity",
                "symmetryOrbitKey": "synthetic-orbit",
                "orbitSignatures": ["synthetic-orbit-signature"],
            }
        ]
        history_projection_path.write_bytes(_serialize_records(history_rows))
        history_manifest_path = readiness._namespace(
            synthetic_protocol, "historyManifest"
        )
        history_manifest = {
            "schemaVersion": 1,
            "kind": "omega-nnue-king-state-v5-compat-position-history-manifest",
            "compatibilityId": COMPAT_ID,
            "createdUtc": t4,
            "protocol": readiness.identity(readiness.PROTOCOL_PATH),
            "authorization": readiness.identity(auth_path),
            "selectedNetwork": auth["selectedNetwork"],
            "decisions": {gate: readiness.identity(decision_path)},
            "sources": {gate: adapter["rawPrefix"]},
            "projection": readiness.identity(history_projection_path),
            "rows": 1,
            "uniquePositionIdentities": 1,
            "uniqueOrbitSignatures": 1,
            "recordSchema": (
                "gameStart.InitialOfen plus every successful ply.PostOfen from "
                "the final authenticated raw prefix of each terminal gate"
            ),
            "producer": copy.deepcopy(synthetic_protocol["tools"]),
            "originalArtifactsRewritten": 0,
            "finalStageSeal": True,
        }
        write_json(history_manifest_path, history_manifest)
        closure_path = readiness._namespace(synthetic_protocol, "closure")
        closure = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": CLOSURE_KIND,
            "compatibilityId": COMPAT_ID,
            "createdUtc": t5,
            "protocol": readiness.identity(readiness.PROTOCOL_PATH),
            "authorization": readiness.identity(auth_path),
            "selectedNetwork": auth["selectedNetwork"],
            "decisions": {gate: readiness.identity(decision_path)},
            "positionHistoryProjection": readiness.identity(
                history_projection_path
            ),
            "positionHistoryManifest": readiness.identity(
                history_manifest_path
            ),
            "clearlySuperior": False,
            "rule": (
                "development pass plus equal-node promote plus equal-time "
                "promote, all with zero safety failures"
            ),
            "originalArtifactsRewritten": 0,
            "finalStageSeal": True,
        }
        write_json(closure_path, closure)

        def fake_assess(namespace: argparse.Namespace) -> dict[str, Any]:
            records = _event_records(
                Path(namespace.events).read_bytes(), "synthetic core input"
            )
            if not any(
                record.get("Error") == DERIVED_PROCESS_EXIT_ERROR
                for record in records
                if record.get("RecordType") == "ply"
            ):
                raise AssertionError(
                    "terminal ProcessExited derivation did not reach the core"
                )
            return copy.deepcopy(core_report)

        fake_core = types.SimpleNamespace(
            _assess=fake_assess,
            _position_meta=lambda ofen: (
                "opening",
                "w",
                "synthetic-position-identity",
                "synthetic-orbit",
                ("synthetic-orbit-signature",),
            ),
        )
        fake_contract = types.SimpleNamespace(validate_protocol=lambda: {})
        fake_readiness = types.SimpleNamespace(
            _install_core_profile=lambda original_protocol: None
        )

        def fake_strict_growth(
            prefix: bytes,
            before_bytes: int,
            label: str,
            **kwargs: Any,
        ) -> None:
            if len(prefix) <= before_bytes:
                raise ValueError(f"{label} has no growth")
            _event_records(prefix, label)

        fake_orchestrator = types.SimpleNamespace(
            _strict_event_growth=fake_strict_growth,
            _validate_event_growth_boundary=lambda *args, **kwargs: None,
        )
        original_globals = {
            name: globals()[name]
            for name in (
                "_authorization",
                "_protocol",
                "_protected",
                "_frozen_orchestrator",
                "verify_adapter_evidence",
            )
        }
        original_readiness = {
            "_template": readiness._template,
            "_import_frozen_modules": readiness._import_frozen_modules,
            "_process_snapshot": readiness._process_snapshot,
            "require_idle_processes": readiness.require_idle_processes,
        }
        globals()["_authorization"] = lambda path=None, fresh=False: (
            synthetic_protocol,
            auth,
            auth_path,
        )
        globals()["_protocol"] = lambda: synthetic_protocol
        globals()["_protected"] = lambda *args, **kwargs: copy.deepcopy(
            protected
        )
        globals()["_frozen_orchestrator"] = lambda protocol: fake_orchestrator

        def fake_adapter(
            path: Path, *, authorization_path: Path | None = None
        ) -> dict[str, Any]:
            if path.resolve() != adapter_path or authorization_path != auth_path:
                raise ValueError("synthetic adapter binding changed")
            return copy.deepcopy(adapter)

        globals()["verify_adapter_evidence"] = fake_adapter
        readiness._template = lambda protocol: (readiness.PROTOCOL_PATH, {})
        readiness._import_frozen_modules = lambda template: (
            fake_contract,
            fake_core,
            fake_readiness,
        )
        readiness._process_snapshot = lambda: snapshot(readiness._utc_now())
        readiness.require_idle_processes = lambda label: snapshot(
            readiness._utc_now()
        )
        try:
            _gate_inventory(synthetic_protocol, gate)
            verified_intent = _verify_intent(
                intent_path, synthetic_protocol, gate, sequence
            )
            verified_completion = _verify_completion(
                completion_path,
                synthetic_protocol,
                gate,
                sequence,
                verified_intent,
            )
            _verify_assessment(
                assessment_path,
                synthetic_protocol,
                gate,
                sequence,
                replay_core=True,
            )
            _verify_decision(decision_path, synthetic_protocol, gate)
            _verify_closure(
                closure_path, synthetic_protocol, auth, auth_path
            )

            # Recover the exact close transaction after projection+manifest
            # publication but before closure publication.
            prerecovery_closure = closure_path.read_bytes()
            closure_path.unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                _close(argparse.Namespace(authorization=None))
            _verify_global_inventory(
                synthetic_protocol, auth, auth_path
            )
            closure_path.unlink()
            closure_path.write_bytes(prerecovery_closure)

            # History is a standalone verifier and must reject hostile staged
            # evidence even when callers do not run the broader gate scan.
            non_file_evidence = evidence_dir / "000002.raw-prefix.jsonl"
            non_file_evidence.mkdir()
            _expect_self_test_rejection(
                lambda: _history(synthetic_protocol, gate),
                "non-file staged evidence entry",
            )
            non_file_evidence.rmdir()
            symlink_evidence = evidence_dir / "000002.raw-prefix.jsonl"
            try:
                symlink_evidence.symlink_to(raw_prefix_path)
            except OSError:
                # Windows installations without Developer Mode may forbid
                # unprivileged symlink creation; the directory fixture above
                # still exercises the same mandatory non-regular-file guard.
                pass
            else:
                try:
                    _expect_self_test_rejection(
                        lambda: _history(synthetic_protocol, gate),
                        "symlink staged evidence entry",
                    )
                finally:
                    symlink_evidence.unlink()

            # All seven deterministic evidence files may predate the
            # assessment after a crash.  History admits only that next exact
            # sequence, and assess reuses/reverifies it to finish the
            # transaction.  A partial subset is likewise retryable.
            transaction_assessment = assessment_path.read_bytes()
            transaction_decision = decision_path.read_bytes()
            transaction_closure = closure_path.read_bytes()
            transaction_history_projection = history_projection_path.read_bytes()
            transaction_history_manifest = history_manifest_path.read_bytes()
            assessment_path.unlink()
            decision_path.unlink()
            closure_path.unlink()
            history_projection_path.unlink()
            history_manifest_path.unlink()
            staged_completed, staged_assessments, staged_pending = _history(
                synthetic_protocol, gate
            )
            if (
                staged_pending is not None
                or len(staged_completed) != 1
                or staged_assessments
            ):
                raise AssertionError("staged evidence blocked assessment retry")
            with contextlib.redirect_stdout(io.StringIO()):
                _assess(
                    argparse.Namespace(gate=gate, authorization=None)
                )
            _verify_decision(decision_path, synthetic_protocol, gate)
            assessment_path.unlink()
            decision_path.unlink()
            assessment_path.write_bytes(transaction_assessment)
            decision_path.write_bytes(transaction_decision)
            history_projection_path.write_bytes(transaction_history_projection)
            history_manifest_path.write_bytes(transaction_history_manifest)
            closure_path.write_bytes(transaction_closure)

            staged_payloads = {
                path: path.read_bytes() for path in evidence_dir.iterdir()
            }
            for path in list(staged_payloads)[1:]:
                path.unlink()
            assessment_path.unlink()
            decision_path.unlink()
            closure_path.unlink()
            _history(synthetic_protocol, gate)
            for path, payload in staged_payloads.items():
                if not path.exists():
                    path.write_bytes(payload)
            assessment_path.write_bytes(transaction_assessment)
            decision_path.write_bytes(transaction_decision)
            closure_path.write_bytes(transaction_closure)
            _exclusive_bytes(raw_prefix_path, raw_prefix_path.read_bytes())
            _expect_self_test_rejection(
                lambda: _exclusive_bytes(raw_prefix_path, b"changed"),
                "mismatched idempotent evidence retry",
            )

            original_intent_bytes = intent_path.read_bytes()
            malformed_intent = copy.deepcopy(intent)
            malformed_intent["pairBudget"] = 65
            write_json(intent_path, malformed_intent)
            _expect_self_test_rejection(
                lambda: _verify_intent(
                    intent_path, synthetic_protocol, gate, sequence
                ),
                "tampered persisted launch intent",
            )
            intent_path.write_bytes(original_intent_bytes)

            original_completion_bytes = completion_path.read_bytes()
            chronological = copy.deepcopy(completion)
            chronological["createdUtc"] = t0
            write_json(completion_path, chronological)
            _expect_self_test_rejection(
                lambda: _verify_completion(
                    completion_path,
                    synthetic_protocol,
                    gate,
                    sequence,
                    intent,
                ),
                "completion chronology inversion",
            )
            completion_path.write_bytes(original_completion_bytes)

            original_event_bytes = event_path.read_bytes()
            event_path.write_bytes(
                original_event_bytes + b'{"RecordType":"gameStart"'
            )
            crash_completion = copy.deepcopy(completion)
            crash_completion["eventsAfter"] = readiness.identity(event_path)
            write_json(completion_path, crash_completion)
            _expect_self_test_rejection(
                lambda: _verify_completion(
                    completion_path,
                    synthetic_protocol,
                    gate,
                    sequence,
                    intent,
                ),
                "crash-tail completion",
            )

            # Consume that exact partial tail through the immutable abort
            # completion, then exercise crash recovery by publishing the
            # missing abort decision in a second step.
            normal_assessment_bytes = assessment_path.read_bytes()
            normal_decision_bytes = decision_path.read_bytes()
            normal_closure_bytes = closure_path.read_bytes()
            normal_history_projection = history_projection_path.read_bytes()
            normal_history_manifest = history_manifest_path.read_bytes()
            evidence_payloads = {
                path: path.read_bytes() for path in evidence_dir.iterdir()
            }
            assessment_path.unlink()
            decision_path.unlink()
            closure_path.unlink()
            history_projection_path.unlink()
            history_manifest_path.unlink()
            for path in evidence_payloads:
                path.unlink()
            complete_prefix, ignored_tail = _abort_prefix_evidence(
                event_path.read_bytes()
            )
            abort_completion = {
                "schemaVersion": SCHEMA_VERSION,
                "kind": ABORT_COMPLETION_KIND,
                "compatibilityId": COMPAT_ID,
                "gate": gate,
                "sequence": sequence,
                "createdUtc": t2,
                "intent": readiness.identity(intent_path),
                "observedRawEvents": readiness.identity(event_path),
                "completePrefix": complete_prefix,
                "ignoredTail": ignored_tail,
                "abortReason": "partial-json-tail",
                "cause": {
                    "errorType": "SyntheticCrash",
                    "message": "synthetic partial JSONL tail",
                },
                "returnCode": 9,
                "postLaunchProcessSnapshot": snapshot(t2),
                "postLaunchProtected": protected,
                "finalStageSeal": True,
            }
            write_json(completion_path, abort_completion)
            _verify_abort_completion(
                completion_path,
                synthetic_protocol,
                gate,
                sequence,
                intent,
            )
            aborted_history, aborted_assessments, aborted_pending = _history(
                synthetic_protocol, gate
            )
            if (
                aborted_pending is not None
                or aborted_assessments
                or len(aborted_history) != 1
                or aborted_history[0]["value"].get("aborted") is not True
            ):
                raise AssertionError("partial-tail abort did not become a chain tip")
            _publish_abort_decision(
                synthetic_protocol, gate, sequence
            )
            abort_decision = _verify_decision(
                decision_path, synthetic_protocol, gate
            )
            abort_history = _publish_position_history(
                synthetic_protocol,
                [gate],
                auth_path,
                auth["selectedNetwork"],
            )
            abort_closure = copy.deepcopy(closure)
            abort_closure["createdUtc"] = readiness._utc_now()
            abort_closure["decisions"] = {
                gate: readiness.identity(decision_path)
            }
            abort_closure["positionHistoryProjection"] = copy.deepcopy(
                abort_history["projection"]
            )
            abort_closure["positionHistoryManifest"] = readiness.identity(
                history_manifest_path
            )
            write_json(closure_path, abort_closure)
            _verify_closure(
                closure_path, synthetic_protocol, auth, auth_path
            )
            if abort_decision["passed"] is not False:
                raise AssertionError("partial-tail abort was not terminal failure")

            decision_path.unlink()
            closure_path.unlink()
            history_projection_path.unlink()
            history_manifest_path.unlink()
            event_path.write_bytes(original_event_bytes)
            completion_path.write_bytes(original_completion_bytes)
            assessment_path.write_bytes(normal_assessment_bytes)
            for path, payload in evidence_payloads.items():
                path.write_bytes(payload)
            decision_path.write_bytes(normal_decision_bytes)
            closure_path.write_bytes(normal_closure_bytes)
            history_projection_path.write_bytes(normal_history_projection)
            history_manifest_path.write_bytes(normal_history_manifest)

            # A crash immediately after intent publication and before any
            # event byte must also be consumable.  Exercise the public launch
            # recovery branch, which atomically adds the abort completion and
            # then its terminal decision instead of wedging the namespace.
            pending_assessment_bytes = assessment_path.read_bytes()
            pending_decision_bytes = decision_path.read_bytes()
            pending_closure_bytes = closure_path.read_bytes()
            pending_history_projection = history_projection_path.read_bytes()
            pending_history_manifest = history_manifest_path.read_bytes()
            pending_evidence = {
                path: path.read_bytes() for path in evidence_dir.iterdir()
            }
            completion_path.unlink()
            assessment_path.unlink()
            decision_path.unlink()
            closure_path.unlink()
            history_projection_path.unlink()
            history_manifest_path.unlink()
            event_path.unlink()
            for path in pending_evidence:
                path.unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                _launch(
                    argparse.Namespace(
                        gate=gate, authorization=None, action=None
                    )
                )
            no_growth_decision = _verify_decision(
                decision_path, synthetic_protocol, gate
            )
            no_growth_completion = readiness.strict_load(
                completion_path, "synthetic no-growth abort"
            )
            if (
                no_growth_decision.get("kind") != ABORT_DECISION_KIND
                or no_growth_completion.get("abortReason")
                != "no-event-growth"
            ):
                raise AssertionError("pending no-growth intent was not consumed")
            decision_path.unlink()
            completion_path.unlink()
            event_path.write_bytes(original_event_bytes)
            completion_path.write_bytes(original_completion_bytes)
            assessment_path.write_bytes(pending_assessment_bytes)
            for path, payload in pending_evidence.items():
                path.write_bytes(payload)
            decision_path.write_bytes(pending_decision_bytes)
            history_projection_path.write_bytes(pending_history_projection)
            history_manifest_path.write_bytes(pending_history_manifest)
            closure_path.write_bytes(pending_closure_bytes)

            # A crash after a terminal assessment but before its decision is
            # recoverable by re-entering assess; no match or core replay is
            # needed beyond the already authenticated assessment history.
            terminal_decision_bytes = decision_path.read_bytes()
            terminal_closure_bytes = closure_path.read_bytes()
            terminal_history_projection = history_projection_path.read_bytes()
            terminal_history_manifest = history_manifest_path.read_bytes()
            decision_path.unlink()
            closure_path.unlink()
            history_projection_path.unlink()
            history_manifest_path.unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                _assess(
                    argparse.Namespace(gate=gate, authorization=None)
                )
            recovered_decision = _verify_decision(
                decision_path, synthetic_protocol, gate
            )
            if recovered_decision.get("decision") != "safety-fail":
                raise AssertionError("terminal assessment recovery changed decision")
            decision_path.unlink()
            decision_path.write_bytes(terminal_decision_bytes)
            history_projection_path.write_bytes(terminal_history_projection)
            history_manifest_path.write_bytes(terminal_history_manifest)
            closure_path.write_bytes(terminal_closure_bytes)

            pass_report = copy.deepcopy(core_report)
            pass_report["developmentScreen"]["decision"] = "pass"
            nonzero_completion = copy.deepcopy(completion)
            nonzero_completion["returnCode"] = 9
            if _decision_from_report(
                gate, pass_report, nonzero_completion
            ) != ("safety-fail", True, False):
                raise AssertionError("nonzero launcher exit did not fail closed")

            original_assessment_bytes = assessment_path.read_bytes()
            forged_report = copy.deepcopy(assessment)
            forged_report["coreReport"]["safety"] = {
                "terminalProcessExits": 0
            }
            forged_report["safety"] = forged_report["coreReport"]["safety"]
            write_json(assessment_path, forged_report)
            _expect_self_test_rejection(
                lambda: _verify_assessment(
                    assessment_path,
                    synthetic_protocol,
                    gate,
                    sequence,
                    replay_core=False,
                ),
                "forged persisted core report",
            )
            assessment_path.write_bytes(original_assessment_bytes)

            original_decision_bytes = decision_path.read_bytes()
            forged_decision = copy.deepcopy(decision)
            forged_decision["authorizedSuccessor"] = "equal-node"
            write_json(decision_path, forged_decision)
            _expect_self_test_rejection(
                lambda: _verify_decision(
                    decision_path, synthetic_protocol, gate
                ),
                "forged decision successor",
            )
            decision_path.write_bytes(original_decision_bytes)

            original_closure_bytes = closure_path.read_bytes()
            forged_closure = copy.deepcopy(closure)
            forged_closure["clearlySuperior"] = True
            write_json(closure_path, forged_closure)
            _expect_self_test_rejection(
                lambda: _verify_closure(
                    closure_path, synthetic_protocol, auth, auth_path
                ),
                "forged superiority closure",
            )
            closure_path.write_bytes(original_closure_bytes)

            backdated_closure = copy.deepcopy(closure)
            backdated_closure["createdUtc"] = t3
            write_json(closure_path, backdated_closure)
            _expect_self_test_rejection(
                lambda: _verify_closure(
                    closure_path, synthetic_protocol, auth, auth_path
                ),
                "closure predating its terminal decision",
            )
            closure_path.write_bytes(original_closure_bytes)

            original_projection_bytes = history_projection_path.read_bytes()
            history_projection_path.write_bytes(
                original_projection_bytes + b'{"forged":true}\n'
            )
            _expect_self_test_rejection(
                lambda: _verify_closure(
                    closure_path, synthetic_protocol, auth, auth_path
                ),
                "forged closure-bound position history",
            )
            history_projection_path.write_bytes(original_projection_bytes)

            original_manifest_bytes = history_manifest_path.read_bytes()
            backdated_manifest = copy.deepcopy(history_manifest)
            backdated_manifest["createdUtc"] = t3
            write_json(history_manifest_path, backdated_manifest)
            backdated_closure = copy.deepcopy(closure)
            backdated_closure["positionHistoryManifest"] = readiness.identity(
                history_manifest_path
            )
            write_json(closure_path, backdated_closure)
            _expect_self_test_rejection(
                lambda: _verify_closure(
                    closure_path, synthetic_protocol, auth, auth_path
                ),
                "position-history manifest predating its decision",
            )
            history_manifest_path.write_bytes(original_manifest_bytes)
            closure_path.write_bytes(original_closure_bytes)

            _verify_global_inventory(
                synthetic_protocol, auth, auth_path
            )
            top_level_junk = root / "junk.json"
            top_level_junk.write_bytes(b"{}\n")
            _expect_self_test_rejection(
                lambda: _verify_global_inventory(
                    synthetic_protocol, auth, auth_path
                ),
                "top-level compatibility namespace junk",
            )
            top_level_junk.unlink()

            decision_junk = decisions / "junk.json"
            decision_junk.write_bytes(b"{}\n")
            _expect_self_test_rejection(
                lambda: _verify_global_inventory(
                    synthetic_protocol, auth, auth_path
                ),
                "compatibility decisions-root junk",
            )
            decision_junk.unlink()

            evidence_junk = evidence / "junk"
            evidence_junk.mkdir()
            _expect_self_test_rejection(
                lambda: _verify_global_inventory(
                    synthetic_protocol, auth, auth_path
                ),
                "compatibility evidence-root junk",
            )
            evidence_junk.rmdir()

            known_later_stage = readiness._stage_paths(synthetic_protocol)[
                "equal-node"
            ]
            known_later_evidence = _evidence_dir(
                synthetic_protocol, "equal-node"
            )
            known_later_stage.mkdir()
            known_later_evidence.mkdir()
            _expect_self_test_rejection(
                lambda: _verify_global_inventory(
                    synthetic_protocol, auth, auth_path
                ),
                "known-path post-closure stage/evidence additions",
            )
            known_later_evidence.rmdir()
            known_later_stage.rmdir()

            extra = stage / "unbound-extra.bin"
            extra.write_bytes(b"forbidden")
            _expect_self_test_rejection(
                lambda: _gate_inventory(synthetic_protocol, gate),
                "extra stage namespace artifact",
            )
            extra.unlink()

            # Recheck the full valid chain after every mutation was restored.
            _verify_global_inventory(
                synthetic_protocol, auth, auth_path
            )
            _verify_closure(
                closure_path, synthetic_protocol, auth, auth_path
            )
            if verified_completion["returnCode"] != 0:
                raise AssertionError("valid persisted completion changed")
        finally:
            for name, value in original_globals.items():
                globals()[name] = value
            readiness._template = original_readiness["_template"]
            readiness._import_frozen_modules = original_readiness[
                "_import_frozen_modules"
            ]
            readiness._process_snapshot = original_readiness[
                "_process_snapshot"
            ]
            readiness.require_idle_processes = original_readiness[
                "require_idle_processes"
            ]


def _self_test() -> None:
    protocol = _protocol()
    _self_test_rules_helper(protocol)
    _self_test_zero_ply_completed_game()
    _self_test_persisted_state_chain()
    raw = [
        {"RecordType": "run", "RunId": "synthetic"},
        {"RecordType": "ply", "GameId": "g1", "Color": "white", "Ply": 1, "Nested": {"x": [1, True, None]}},
        {"RecordType": "ply", "GameId": "g1", "Color": "black", "Ply": 2, "Nested": {"x": [2, False, None]}},
    ]
    derived, summary = _derive_records(raw)
    if [derived[1]["Color"], derived[2]["Color"]] != ["w", "b"]:
        raise AssertionError("white/black color derivation failed")
    if summary["otherFieldsChanged"] != 0 or _semantic_adapter_audit(raw, derived)["otherFieldsChanged"] != 0:
        raise AssertionError("color adapter changed another field")
    malformed = ("w", "b", "WHITE", "Black", "", None, 1, True)
    for token in malformed:
        try:
            _derive_records([{"RecordType": "ply", "Color": token}])
        except ValueError:
            pass
        else:
            raise AssertionError(f"malformed raw Color {token!r} was accepted")
    tampered = copy.deepcopy(derived)
    tampered[1]["GameId"] = "changed"
    try:
        _semantic_adapter_audit(raw, tampered)
    except ValueError:
        pass
    else:
        raise AssertionError("non-Color event mutation was accepted")
    terminal_color = [
        {"RecordType": "run", "RunId": "synthetic"},
        {
            "RecordType": "ply",
            "GameId": "g1",
            "Color": "w",
            "Ply": 1,
            "PostOfen": None,
            "Search": {"ProcessExited": True},
        },
    ]
    prepared, terminal_summary = _derive_terminal_records(
        terminal_color, expected_transforms=1
    )
    if (
        prepared[1].get("Error") != DERIVED_PROCESS_EXIT_ERROR
        or terminal_summary["nonErrorFieldsChanged"] != 0
        or _semantic_terminal_audit(
            terminal_color, prepared, expected_transforms=1
        )["errorFieldsChanged"]
        != 1
    ):
        raise AssertionError("terminal process-exit compatibility derivation failed")
    terminal_tamper = copy.deepcopy(prepared)
    terminal_tamper[1]["GameId"] = "changed"
    try:
        _semantic_terminal_audit(
            terminal_color, terminal_tamper, expected_transforms=1
        )
    except ValueError:
        pass
    else:
        raise AssertionError("terminal adapter accepted a non-Error mutation")
    with tempfile.TemporaryDirectory(prefix="omega-g5-color-events-selftest-") as directory:
        root = Path(directory)
        raw_path = root / "raw.jsonl"
        payload = _serialize_records(raw)
        raw_path.write_bytes(payload)
        before = readiness.identity(raw_path)
        derived_path = root / "derived.jsonl"
        derived_path.write_bytes(_serialize_records(derived))
        if readiness.identity(raw_path) != before:
            raise AssertionError("synthetic raw events changed")
        _prefix(raw_path, before, exact=True)
        with raw_path.open("ab") as stream:
            stream.write(b'{"RecordType":"gameStart"}\n')
        _prefix(raw_path, before, exact=False)
        try:
            _prefix(raw_path, before, exact=True)
        except ValueError:
            pass
        else:
            raise AssertionError("exact prefix accepted appended raw events")
        payload_mutated = bytearray(raw_path.read_bytes())
        payload_mutated[0] ^= 1
        raw_path.write_bytes(payload_mutated)
        try:
            _prefix(raw_path, before, exact=False)
        except ValueError:
            pass
        else:
            raise AssertionError("mutated raw-event prefix was accepted")
        evidence_dir = root / "inventory"
        evidence_dir.mkdir()
        (evidence_dir / "000001.intent.json").write_text("{}", encoding="utf-8")
        _entries(evidence_dir, {"intent"})
        (evidence_dir / "evil.json").write_text("{}", encoding="utf-8")
        try:
            _entries(evidence_dir, {"intent"})
        except ValueError:
            pass
        else:
            raise AssertionError("unexpected evidence inventory was accepted")
    if protocol["adapter"]["otherFieldsChanged"] != 0:
        raise AssertionError("protocol adapter field-change policy changed")


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
    idle = commands.add_parser("attest-idle")
    idle.add_argument("--operator", required=True)
    idle.add_argument("--authorization", type=Path)
    verify = commands.add_parser("verify-state")
    verify.add_argument("--authorization", type=Path)
    close = commands.add_parser("close")
    close.add_argument("--authorization", type=Path)
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
    elif args.command == "close":
        _close(args)
    else:
        _self_test()
        print("Generation-5 color-compat match self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
