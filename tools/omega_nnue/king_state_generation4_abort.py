#!/usr/bin/env python3
"""Close Generation 4 before labels after its rules-only slack failure.

The closure is deliberately target-opaque.  It authenticates the frozen G4
profile and every source identity, then derives its conclusion only from the
already-frozen root and legal-child structure.  ``create`` is the sole command
that writes anything and publishes the canonical seal with no replacement.
``verify`` repeats the complete derivation rather than trusting seal counts.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterable, Mapping, Sequence

import king_state_generation4_freeze as generation4_freeze
from omega_nnue import parse_ofen
from select_screen import phase_of


REPO = Path(__file__).resolve().parents[2]
OUTPUT = REPO / "validation/omega-nnue-king-state-v4-structural-abort.seal.json"
FINAL_PROFILE = REPO / "validation/omega-nnue-king-state-v4-preregistration.json"
FINAL_FREEZE = REPO / "validation/omega-nnue-king-state-v4-freeze.seal.json"
PROFILE_ID = "king-state-v4-omega-decision-v1"
KIND = "omega-nnue-king-state-v4-target-opaque-structural-abort"
STATUS = "closed-before-prelabel-or-teacher-search"
ABORT_REASON = "insufficient-rules-feasible-root-slack"
MINIMUM_LEGAL_CHILDREN = 5
DEEP_QUOTA_PER_PHASE_SIDE = 640
PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")

IDENTITY_PATHS = {
    "finalPreregistration": "validation/omega-nnue-king-state-v4-preregistration.json",
    "finalFreezeSeal": "validation/omega-nnue-king-state-v4-freeze.seal.json",
    "forbiddenPositionCatalog": (
        "build-msvc/data-generation/omega-decision-v1/forbidden-positions.jsonl"
    ),
    "forbiddenPositionCatalogManifest": (
        "build-msvc/data-generation/omega-decision-v1/"
        "forbidden-positions.jsonl.manifest.json"
    ),
    "sourceRootPool": (
        "build-msvc/data-generation/omega-decision-v1/source/rules-only-pool.jsonl"
    ),
    "sourceRootPoolManifest": (
        "build-msvc/data-generation/omega-decision-v1/source/"
        "rules-only-pool.jsonl.manifest.json"
    ),
    "sourceRootPoolSeal": (
        "build-msvc/data-generation/omega-decision-v1/source/"
        "rules-only-pool.jsonl.complete.seal.json"
    ),
    "sourceEvents": (
        "build-msvc/data-generation/omega-decision-v1/source/events.jsonl"
    ),
    "sourceOpeningSuite": (
        "build-msvc/data-generation/omega-decision-v1/source/openings.json"
    ),
    "sourceMatchConfig": (
        "build-msvc/data-generation/omega-decision-v1/source/source-match.json"
    ),
    "roots": "build-msvc/data-generation/omega-decision-v1/roots.jsonl",
    "rootsManifest": (
        "build-msvc/data-generation/omega-decision-v1/roots.jsonl.manifest.json"
    ),
    "children": "build-msvc/data-generation/omega-decision-v1/children.jsonl",
    "childrenManifest": (
        "build-msvc/data-generation/omega-decision-v1/children.jsonl.manifest.json"
    ),
    "samplerCompletionSeal": (
        "build-msvc/data-generation/omega-decision-v1/"
        "children.jsonl.complete.seal.json"
    ),
}

ROOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "groupId",
        "sourceOpeningId",
        "sourcePairId",
        "sourceProvenanceTag",
        "sourceGameId",
        "sourceRunId",
        "sourceAttempt",
        "sourcePly",
        "sourceLine",
        "sourceEngineId",
        "sourceEngineSha256",
        "phase",
        "sideToMove",
        "ofen",
        "rootPvMove",
        "selectionRank",
        "candidateRole",
    }
)
CHILD_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "groupId",
        "sourceGameId",
        "phase",
        "rootPvMove",
        "candidateRole",
        "selectionRank",
        "parentOfen",
        "parentSideToMove",
        "childId",
        "move",
        "moveOrdinal",
        "childOfen",
        "childSideToMove",
        "isPromotion",
    }
)

HEX_256 = re.compile(r"^[0-9a-f]{64}$")
GROUP_ID = re.compile(r"^trajectory:[0-9a-f]{64}$")
MOVE = re.compile(
    r"^(?:(?:[a-j][0-9])|(?:w[1-4]))"
    r"(?:(?:[a-j][0-9])|(?:w[1-4]))[qrbncw]?$"
)

# Every possible post-structure G4 output is prohibited.  Directories are
# listed where their existence alone implies that the corresponding phase was
# entered.  Append-only ledgers include all fixed companion names.
_DATA_ROOT = "build-msvc/data-generation/omega-decision-v1"
_TRAINING_ROOT = "build-msvc/king-state-v4"
PROHIBITED_ARTIFACT_PATHS = tuple(
    [
        f"{_DATA_ROOT}/component-splits.jsonl",
        f"{_DATA_ROOT}/component-splits.jsonl.manifest.json",
        f"{_DATA_ROOT}/prelabel-freeze.seal.json",
    ]
    + [
        f"{_DATA_ROOT}/{base}{suffix}"
        for base in (
            "shallow-results.jsonl",
            "selected-children.jsonl",
            "deep-results.jsonl",
            "decision-labels.jsonl",
        )
        for suffix in (
            "",
            ".lock.json",
            ".active.claim.json",
            ".manifest.json",
            ".complete.manifest.json",
        )
    ]
    + [
        f"{_TRAINING_ROOT}/training-plan.json",
        f"{_TRAINING_ROOT}/validation-selection.seal.json",
        f"{_TRAINING_ROOT}/robustness.bundle",
        f"{_TRAINING_ROOT}/.robustness.training.claim.json",
        f"{_TRAINING_ROOT}/robustness.failure.json",
        f"{_TRAINING_ROOT}/robustness.seal.json",
        f"{_TRAINING_ROOT}/offline",
    ]
    + [
        item
        for candidate in ("G4A", "G4B", "G4C")
        for item in (
            f"{_TRAINING_ROOT}/{candidate}.bundle",
            f"{_TRAINING_ROOT}/.{candidate}.training.claim.json",
            f"{_TRAINING_ROOT}/{candidate}.failure.json",
        )
    ]
    + [
        "build-king-state-v4/matches/sealed",
        "build-king-state-v4/matches/development",
        "build-king-state-v4/matches/equal-node",
        "build-king-state-v4/matches/equal-time",
    ]
)
MATCH_SAMPLER_DIRECTORY = REPO / "build-king-state-v4/matches/sampler"
DATA_SAMPLER_DIRECTORY = REPO / f"{_DATA_ROOT}/sampler"

EXPECTED_HISTOGRAM = {
    "1": 8,
    "2": 20,
    "3": 38,
    "4": 60,
    "5": 63,
    "6": 75,
    "7": 62,
    "8": 77,
    "9": 52,
    "10": 37,
    "11": 37,
    "12": 45,
    "13": 39,
    "14": 47,
    "15": 54,
    "16": 69,
    "17": 65,
    "18": 67,
    "19": 61,
    "20": 66,
    "21": 64,
    "22": 57,
    "23": 58,
    "24": 68,
    "25": 61,
    "26": 59,
    "27": 69,
    "28": 89,
    "29": 87,
    "30": 69,
    "31": 76,
    "32": 77,
    "33": 80,
    "34": 105,
    "35": 109,
    "36": 101,
    "37": 126,
    "38": 137,
    "39": 154,
    "40": 132,
    "41": 172,
    "42": 144,
    "43": 152,
    "44": 150,
    "45": 140,
    "46": 136,
    "47": 143,
    "48": 125,
    "49": 143,
    "50": 122,
    "51": 96,
    "52": 93,
    "53": 92,
    "54": 64,
    "55": 75,
    "56": 63,
    "57": 53,
    "58": 58,
    "59": 32,
    "60": 34,
    "61": 28,
    "62": 27,
    "63": 34,
    "64": 17,
    "65": 11,
    "66": 15,
    "67": 14,
    "68": 3,
    "69": 11,
    "70": 11,
    "71": 8,
    "72": 3,
    "73": 5,
    "74": 7,
    "75": 4,
    "76": 1,
    "77": 2,
    "78": 2,
    "79": 3,
    "80": 1,
    "81": 3,
    "83": 1,
    "84": 1,
    "86": 1,
}

EXPECTED_PHASE_SIDE = {
    "opening/w": (640, 30150, 1, 629, 0),
    "opening/b": (640, 30019, 1, 625, 0),
    "middlegame/w": (640, 27827, 1, 624, 0),
    "middlegame/b": (640, 27921, 1, 623, 0),
    "late/w": (640, 20928, 2, 635, 0),
    "late/b": (640, 20549, 1, 630, 0),
    "endgame/w": (640, 13370, 1, 617, 0),
    "endgame/b": (640, 13117, 1, 611, 0),
}


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON token {token!r}")


def _strict_json(text: str, label: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeError, ValueError) as error:
        raise ValueError(f"{label} is not strict JSON") from error


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = _strict_json(path.read_text(encoding="utf-8", errors="strict"), label)
    except UnicodeError as error:
        raise ValueError(f"{label} is not strict UTF-8") from error
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _jsonl(path: Path, label: str) -> Iterable[tuple[int, dict[str, Any]]]:
    try:
        with path.open("r", encoding="utf-8", errors="strict", newline="") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise ValueError(f"{label} line {line_number} is blank")
                value = _strict_json(line, f"{label} line {line_number}")
                if type(value) is not dict:
                    raise ValueError(f"{label} line {line_number} is not an object")
                yield line_number, value
    except UnicodeError as error:
        raise ValueError(f"{label} is not strict UTF-8") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_path(relative: str) -> Path:
    if type(relative) is not str or not relative or relative != relative.strip():
        raise ValueError("repository path must be a nonempty canonical string")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        raise ValueError(f"repository path escapes: {relative!r}")
    result = (REPO / Path(*pure.parts)).resolve()
    try:
        result.relative_to(REPO.resolve())
    except ValueError as error:
        raise ValueError(f"repository path escapes: {relative!r}") from error
    return result


def _identity(path: Path, *, reported_path: str | None = None) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    stat = path.stat()
    return {
        "path": str(path) if reported_path is None else reported_path,
        "bytes": stat.st_size,
        "sha256": _sha256(path),
    }


def _identity_shape(record: Any, label: str) -> dict[str, Any]:
    if (
        type(record) is not dict
        or set(record) != {"path", "bytes", "sha256"}
        or type(record.get("path")) is not str
        or not record["path"]
        or record["path"] != record["path"].strip()
        or type(record.get("bytes")) is not int
        or record["bytes"] < 0
        or type(record.get("sha256")) is not str
        or HEX_256.fullmatch(record["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(record)


def _identity_path(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = REPO / path
    path = path.resolve()
    try:
        path.relative_to(REPO.resolve())
    except ValueError as error:
        raise ValueError(f"{label} identity escapes the repository") from error
    return path


def _require_identity(record: Any, expected: Path, label: str) -> dict[str, Any]:
    shaped = _identity_shape(record, label)
    expected = expected.resolve()
    if _identity_path(shaped["path"], label) != expected:
        raise ValueError(f"{label} path changed")
    actual = _identity(expected)
    if shaped["bytes"] != actual["bytes"] or shaped["sha256"] != actual["sha256"]:
        raise ValueError(f"{label} content changed")
    return actual


def _same_identity(left: Any, right: Any) -> bool:
    return (
        type(left) is dict
        and type(right) is dict
        and type(left.get("bytes")) is int
        and type(right.get("bytes")) is int
        and left["bytes"] == right["bytes"]
        and type(left.get("sha256")) is str
        and type(right.get("sha256")) is str
        and left["sha256"] == right["sha256"]
    )


def _require_manifest_identity(
    record: Any, expected: Path, actual: Mapping[str, Any], label: str
) -> None:
    value = _require_identity(record, expected, label)
    if not _same_identity(value, actual):
        raise ValueError(f"{label} disagrees with the authenticated input")


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _exact_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(
            _exact_equal(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _exact_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _canonical_utc(value: Any, label: str) -> str:
    if type(value) is not str or not value.endswith("Z") or value != value.strip():
        raise ValueError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} must be UTC")
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{label} is not canonical")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _profile_pin(
    final_identities: Mapping[str, Any], name: str, path: Path, label: str
) -> None:
    if name not in final_identities:
        raise ValueError(f"frozen profile is missing {name}")
    _require_identity(final_identities[name], path, label)


def _authenticate_manifests(
    pins: Mapping[str, Mapping[str, Any]], profile: Mapping[str, Any]
) -> None:
    final = profile.get("finalFreezeIdentities")
    if type(final) is not dict:
        raise ValueError("frozen profile lacks finalFreezeIdentities")

    direct = {
        "sourceRootPool": "sourceRootPool",
        "sourceRootPoolManifest": "sourceRootPoolManifest",
        "sourceRootPoolSeal": "sourceRootPoolSeal",
        "sourceEvents": "sourceEvents",
        "sourceOpeningSuite": "sourceOpeningSuite",
        "sourceMatchConfig": "sourceMatchConfig",
        "roots": "roots",
        "rootsManifest": "rootsManifest",
        "children": "children",
        "childrenManifest": "childrenManifest",
    }
    for closure_name, profile_name in direct.items():
        _profile_pin(
            final,
            profile_name,
            _repo_path(IDENTITY_PATHS[closure_name]),
            f"profile {profile_name}",
        )
        if not _same_identity(final[profile_name], pins[closure_name]):
            raise ValueError(f"profile {profile_name} pin changed")

    forbidden = final.get("forbiddenPositionCatalogManifests")
    if type(forbidden) is not list or len(forbidden) != 1:
        raise ValueError("G4 must have exactly one frozen forbidden catalog manifest")
    _require_identity(
        forbidden[0],
        _repo_path(IDENTITY_PATHS["forbiddenPositionCatalogManifest"]),
        "profile forbidden-position manifest",
    )
    if not _same_identity(forbidden[0], pins["forbiddenPositionCatalogManifest"]):
        raise ValueError("profile forbidden-position manifest pin changed")

    forbidden_manifest = _load_json(
        _repo_path(IDENTITY_PATHS["forbiddenPositionCatalogManifest"]),
        "forbidden-position manifest",
    )
    if (
        forbidden_manifest.get("schemaVersion") != 1
        or forbidden_manifest.get("kind")
        != "omega-target-opaque-forbidden-position-catalog-manifest"
        or forbidden_manifest.get("targetOpaque") is not True
        or forbidden_manifest.get("targetOrScoreFieldsDecoded") != 0
        or forbidden_manifest.get("targetOrScoreFieldsEmitted") != 0
    ):
        raise ValueError("forbidden-position manifest is not target-opaque")
    _require_manifest_identity(
        forbidden_manifest.get("catalog"),
        _repo_path(IDENTITY_PATHS["forbiddenPositionCatalog"]),
        pins["forbiddenPositionCatalog"],
        "forbidden-position catalog",
    )

    pool_manifest = _load_json(
        _repo_path(IDENTITY_PATHS["sourceRootPoolManifest"]),
        "source-root-pool manifest",
    )
    if (
        pool_manifest.get("schemaVersion") != 1
        or pool_manifest.get("kind") != "omega-rules-only-random-root-manifest"
        or pool_manifest.get("finalStageSeal") is not False
    ):
        raise ValueError("source-root-pool manifest envelope changed")
    _require_manifest_identity(
        pool_manifest.get("output"),
        _repo_path(IDENTITY_PATHS["sourceRootPool"]),
        pins["sourceRootPool"],
        "source-root-pool manifest output",
    )
    pool_seal = _load_json(
        _repo_path(IDENTITY_PATHS["sourceRootPoolSeal"]),
        "source-root-pool completion seal",
    )
    if (
        pool_seal.get("schemaVersion") != 1
        or pool_seal.get("kind")
        != "omega-rules-only-random-root-completion-seal"
        or pool_seal.get("finalStageSeal") is not True
    ):
        raise ValueError("source-root-pool completion seal envelope changed")
    _require_manifest_identity(
        pool_seal.get("output"),
        _repo_path(IDENTITY_PATHS["sourceRootPool"]),
        pins["sourceRootPool"],
        "source-root-pool seal output",
    )
    _require_manifest_identity(
        pool_seal.get("manifest"),
        _repo_path(IDENTITY_PATHS["sourceRootPoolManifest"]),
        pins["sourceRootPoolManifest"],
        "source-root-pool seal manifest",
    )

    roots_manifest = _load_json(
        _repo_path(IDENTITY_PATHS["rootsManifest"]), "root manifest"
    )
    if (
        roots_manifest.get("schemaVersion") != 1
        or roots_manifest.get("kind") != "omega-decision-root-manifest"
        or roots_manifest.get("profileId") != PROFILE_ID
        or roots_manifest.get("finalStageSeal") is not True
    ):
        raise ValueError("root manifest envelope changed")
    _require_manifest_identity(
        roots_manifest.get("output"),
        _repo_path(IDENTITY_PATHS["roots"]),
        pins["roots"],
        "root manifest output",
    )

    children_manifest = _load_json(
        _repo_path(IDENTITY_PATHS["childrenManifest"]), "child manifest"
    )
    if (
        children_manifest.get("schemaVersion") != 1
        or children_manifest.get("kind") != "omega-decision-sampler-manifest"
        or children_manifest.get("finalStageSeal") is not False
    ):
        raise ValueError("child manifest envelope changed")
    _require_manifest_identity(
        children_manifest.get("input"),
        _repo_path(IDENTITY_PATHS["roots"]),
        pins["roots"],
        "child manifest input",
    )
    _require_manifest_identity(
        children_manifest.get("output"),
        _repo_path(IDENTITY_PATHS["children"]),
        pins["children"],
        "child manifest output",
    )

    completion = _load_json(
        _repo_path(IDENTITY_PATHS["samplerCompletionSeal"]),
        "child completion seal",
    )
    if (
        completion.get("schemaVersion") != 1
        or completion.get("kind") != "omega-decision-sampler-completion-seal"
        or completion.get("finalStageSeal") is not True
    ):
        raise ValueError("child completion seal envelope changed")
    for field, name in (
        ("input", "roots"),
        ("output", "children"),
        ("manifest", "childrenManifest"),
    ):
        _require_manifest_identity(
            completion.get(field),
            _repo_path(IDENTITY_PATHS[name]),
            pins[name],
            f"child completion {field}",
        )
    producer = completion.get("producer")
    runtime = children_manifest.get("runtime")
    if type(producer) is not dict or type(runtime) is not dict:
        raise ValueError("child sampler runtime identities are missing")
    for field, profile_name in (
        ("samplerAssembly", "decisionSamplerAssembly"),
        ("chessLibAssembly", "chessLibAssembly"),
    ):
        expected = _identity_path(final[profile_name]["path"], profile_name)
        _require_identity(producer.get(field), expected, f"completion {field}")
        _require_identity(runtime.get(field), expected, f"manifest {field}")


def _authenticate_inputs() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    # This validates the completed profile, its adjacent seal, and every
    # external identity frozen inside the profile before any structural row is
    # opened by this tool.
    generation4_freeze._verify_paths(FINAL_PROFILE, FINAL_FREEZE)
    profile = _load_json(FINAL_PROFILE, "Generation-4 final preregistration")
    if profile.get("profileId") != PROFILE_ID:
        raise ValueError("Generation-4 profile id changed")

    pins = {
        name: _identity(_repo_path(relative), reported_path=relative)
        for name, relative in IDENTITY_PATHS.items()
    }
    _authenticate_manifests(pins, profile)
    return pins, profile


def _recheck_inputs(pins: Mapping[str, Mapping[str, Any]]) -> None:
    if set(pins) != set(IDENTITY_PATHS):
        raise ValueError("authenticated G4 identity inventory changed")
    for name, relative in IDENTITY_PATHS.items():
        actual = _identity(_repo_path(relative), reported_path=relative)
        if not _exact_equal(actual, pins[name]):
            raise ValueError(f"authenticated G4 input changed during audit: {name}")


def _string(value: Any, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty canonical string")
    return value


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _root_record(row: dict[str, Any], line: int) -> dict[str, Any]:
    label = f"root line {line}"
    if set(row) != ROOT_FIELDS:
        raise ValueError(f"{label} field inventory changed")
    if row.get("schemaVersion") != 1 or row.get("kind") != "omega-hce-on-policy-root":
        raise ValueError(f"{label} envelope changed")
    root_id = _string(row.get("rootId"), f"{label} rootId")
    group_id = _string(row.get("groupId"), f"{label} groupId")
    rank = _string(row.get("selectionRank"), f"{label} selectionRank")
    if HEX_256.fullmatch(root_id) is None or GROUP_ID.fullmatch(group_id) is None:
        raise ValueError(f"{label} has a malformed stable id")
    if HEX_256.fullmatch(rank) is None:
        raise ValueError(f"{label} has a malformed selection rank")
    phase = row.get("phase")
    side = row.get("sideToMove")
    if phase not in PHASES or side not in SIDES:
        raise ValueError(f"{label} phase/side changed")
    ofen = _string(row.get("ofen"), f"{label} ofen")
    pieces, parsed_side, _ = parse_ofen(ofen)
    if parsed_side != side or phase_of(len(pieces)) != phase:
        raise ValueError(f"{label} OFEN phase/side disagrees")
    move = _string(row.get("rootPvMove"), f"{label} rootPvMove")
    if MOVE.fullmatch(move) is None:
        raise ValueError(f"{label} root PV move is malformed")
    if row.get("candidateRole") not in {"primary", "reserve"}:
        raise ValueError(f"{label} candidate role changed")
    for field in (
        "sourceOpeningId",
        "sourcePairId",
        "sourceProvenanceTag",
        "sourceGameId",
        "sourceRunId",
        "sourceEngineId",
    ):
        _string(row.get(field), f"{label} {field}")
    for field in ("sourceAttempt", "sourcePly", "sourceLine"):
        _positive_int(row.get(field), f"{label} {field}")
    engine_sha = _string(row.get("sourceEngineSha256"), f"{label} engine sha")
    if HEX_256.fullmatch(engine_sha) is None:
        raise ValueError(f"{label} engine hash is malformed")
    return {
        "rootId": root_id,
        "groupId": group_id,
        "sourceGameId": row["sourceGameId"],
        "phase": phase,
        "side": side,
        "ofen": ofen,
        "rootPvMove": move,
        "candidateRole": row["candidateRole"],
        "selectionRank": rank,
    }


def _scan_roots(path: Path) -> dict[str, dict[str, Any]]:
    roots: dict[str, dict[str, Any]] = {}
    groups: set[str] = set()
    ranks: set[str] = set()
    for line, row in _jsonl(path, "G4 roots"):
        root = _root_record(row, line)
        root_id = root["rootId"]
        if root_id in roots or root["groupId"] in groups or root["selectionRank"] in ranks:
            raise ValueError(f"root line {line} duplicates a frozen identity")
        roots[root_id] = root
        groups.add(root["groupId"])
        ranks.add(root["selectionRank"])
    return roots


def _scan_children(
    path: Path, roots: Mapping[str, Mapping[str, Any]]
) -> tuple[int, dict[str, set[str]], Counter[str], dict[str, set[int]]]:
    moves: dict[str, set[str]] = defaultdict(set)
    pv_occurrences: Counter[str] = Counter()
    ordinals: dict[str, set[int]] = defaultdict(set)
    child_ids: set[str] = set()
    child_count = 0
    for line, row in _jsonl(path, "G4 children"):
        label = f"child line {line}"
        if set(row) != CHILD_FIELDS:
            raise ValueError(f"{label} field inventory changed")
        if row.get("schemaVersion") != 1 or row.get("kind") != "omega-legal-child":
            raise ValueError(f"{label} envelope changed")
        root_id = _string(row.get("rootId"), f"{label} rootId")
        if HEX_256.fullmatch(root_id) is None or root_id not in roots:
            raise ValueError(f"{label} has an unknown root")
        root = roots[root_id]
        inherited = {
            "groupId": "groupId",
            "sourceGameId": "sourceGameId",
            "phase": "phase",
            "rootPvMove": "rootPvMove",
            "candidateRole": "candidateRole",
            "selectionRank": "selectionRank",
            "parentOfen": "ofen",
            "parentSideToMove": "side",
        }
        for child_field, root_field in inherited.items():
            if row.get(child_field) != root[root_field]:
                raise ValueError(f"{label} {child_field} disagrees with its root")
        child_id = _string(row.get("childId"), f"{label} childId")
        if HEX_256.fullmatch(child_id) is None or child_id in child_ids:
            raise ValueError(f"{label} has a malformed or duplicate childId")
        child_ids.add(child_id)
        move = _string(row.get("move"), f"{label} move")
        if MOVE.fullmatch(move) is None:
            raise ValueError(f"{label} move is malformed")
        ordinal = row.get("moveOrdinal")
        if type(ordinal) is not int or ordinal < 0 or ordinal in ordinals[root_id]:
            raise ValueError(f"{label} move ordinal is malformed or duplicated")
        ordinals[root_id].add(ordinal)
        if type(row.get("isPromotion")) is not bool or row["isPromotion"] != (len(move) == 5):
            raise ValueError(f"{label} promotion declaration changed")
        child_ofen = _string(row.get("childOfen"), f"{label} childOfen")
        _, child_side, _ = parse_ofen(child_ofen)
        if child_side != row.get("childSideToMove") or child_side == root["side"]:
            raise ValueError(f"{label} child side-to-move is inconsistent")
        if row.get("childSideToMove") not in SIDES:
            raise ValueError(f"{label} child side-to-move changed")
        moves[root_id].add(move)
        if move == root["rootPvMove"]:
            pv_occurrences[root_id] += 1
        child_count += 1

    if set(moves) != set(roots) or set(ordinals) != set(roots):
        raise ValueError("one or more G4 roots has no legal child")
    for root_id, values in ordinals.items():
        if values != set(range(len(values))):
            raise ValueError(f"root {root_id} child ordinals are not contiguous")
    return child_count, moves, pv_occurrences, ordinals


def _recompute_structural_evidence() -> dict[str, Any]:
    roots = _scan_roots(_repo_path(IDENTITY_PATHS["roots"]))
    child_count, moves, pv_occurrences, ordinals = _scan_children(
        _repo_path(IDENTITY_PATHS["children"]), roots
    )
    duplicate_moves = sum(len(ordinals[root]) - len(moves[root]) for root in roots)
    source_pv_missing = sum(pv_occurrences[root] == 0 for root in roots)
    histogram = Counter(len(moves[root]) for root in roots)
    eligible = {
        root
        for root in roots
        if len(moves[root]) >= MINIMUM_LEGAL_CHILDREN
        and pv_occurrences[root] == 1
    }

    phase_side: dict[str, Any] = {}
    for phase in PHASES:
        for side in SIDES:
            key = f"{phase}/{side}"
            bucket = [
                root_id
                for root_id, root in roots.items()
                if root["phase"] == phase and root["side"] == side
            ]
            eligible_count = sum(root in eligible for root in bucket)
            bucket_children = sum(len(moves[root]) for root in bucket)
            bucket_missing = sum(pv_occurrences[root] == 0 for root in bucket)
            bucket_duplicates = sum(
                len(ordinals[root]) - len(moves[root]) for root in bucket
            )
            phase_side[key] = {
                "rootCount": len(bucket),
                "childCount": bucket_children,
                "minimumChildCount": min(len(moves[root]) for root in bucket),
                "guaranteedEligibleRoots": eligible_count,
                "shortfallToDeepQuota": DEEP_QUOTA_PER_PHASE_SIDE - eligible_count,
                "sourcePvMissing": bucket_missing,
                "duplicateChildMoves": bucket_duplicates,
            }

    return {
        "rootCount": len(roots),
        "childCount": child_count,
        "minimumLegalChildrenForGuaranteedSelection": MINIMUM_LEGAL_CHILDREN,
        "guaranteedEligibleRoots": len(eligible),
        "shortfallToDeepQuota": len(roots) - len(eligible),
        "childCountHistogram": {
            str(count): histogram[count] for count in sorted(histogram)
        },
        "phaseSideEvidence": phase_side,
        "sourcePvMissing": source_pv_missing,
        "duplicateChildMoves": duplicate_moves,
        "targetOrScoreFieldsDecoded": 0,
        "proof": {
            "eligibilityRule": (
                "at least five distinct legal child moves and exactly one child "
                "matching rootPvMove"
            ),
            "quotaRule": (
                "640 deep-candidate roots are required independently in each "
                "phase/side bucket; cross-bucket borrowing is forbidden"
            ),
            "conclusion": (
                "all 5,120 roots would be required, but only 4,994 are "
                "guaranteed eligible; abort before prelabel or teacher search"
            ),
        },
    }


def _assert_expected_structure(value: Mapping[str, Any]) -> None:
    expected_counts = {
        "rootCount": 5120,
        "childCount": 183881,
        "minimumLegalChildrenForGuaranteedSelection": 5,
        "guaranteedEligibleRoots": 4994,
        "shortfallToDeepQuota": 126,
        "sourcePvMissing": 0,
        "duplicateChildMoves": 0,
        "targetOrScoreFieldsDecoded": 0,
    }
    for key, expected in expected_counts.items():
        if type(value.get(key)) is not int or value[key] != expected:
            raise ValueError(f"G4 structural result {key} changed")
    if not _exact_equal(value.get("childCountHistogram"), EXPECTED_HISTOGRAM):
        raise ValueError("G4 child-count histogram changed")
    phase_side = value.get("phaseSideEvidence")
    if type(phase_side) is not dict or set(phase_side) != set(EXPECTED_PHASE_SIDE):
        raise ValueError("G4 phase/side inventory changed")
    for key, expected in EXPECTED_PHASE_SIDE.items():
        roots, children, minimum, eligible, missing = expected
        wanted = {
            "rootCount": roots,
            "childCount": children,
            "minimumChildCount": minimum,
            "guaranteedEligibleRoots": eligible,
            "shortfallToDeepQuota": DEEP_QUOTA_PER_PHASE_SIDE - eligible,
            "sourcePvMissing": missing,
            "duplicateChildMoves": 0,
        }
        if not _exact_equal(phase_side[key], wanted):
            raise ValueError(f"G4 phase/side evidence changed: {key}")


def _dynamic_absence_paths() -> list[Path]:
    paths: list[Path] = []
    patterns = (
        (REPO / _DATA_ROOT, (".*.tmp", "*.staging-*")),
        (REPO / _TRAINING_ROOT, (".*.tmp", "*.bundle-staging-*")),
        (REPO / "build-king-state-v4/matches", (".*.tmp", "*.staging-*")),
    )
    for directory, globs in patterns:
        if not directory.exists():
            continue
        for pattern in globs:
            paths.extend(item.resolve() for item in directory.rglob(pattern))
    return sorted(set(paths), key=str)


def _absence_evidence() -> dict[str, Any]:
    prohibited = [_repo_path(item) for item in PROHIBITED_ARTIFACT_PATHS]
    existing = [str(path) for path in prohibited if path.exists()]
    existing.extend(str(path) for path in _dynamic_absence_paths())
    if not MATCH_SAMPLER_DIRECTORY.is_dir():
        raise ValueError("canonical G4 match sampler directory is missing")
    match_sampler_entries = list(MATCH_SAMPLER_DIRECTORY.iterdir())
    if match_sampler_entries:
        existing.extend(str(item.resolve()) for item in match_sampler_entries)
    # The data sampler is a transient worker namespace, not a required frozen
    # directory.  If an interrupted run left it behind, it must still be empty.
    if DATA_SAMPLER_DIRECTORY.exists():
        if not DATA_SAMPLER_DIRECTORY.is_dir():
            existing.append(str(DATA_SAMPLER_DIRECTORY.resolve()))
        else:
            existing.extend(
                str(item.resolve()) for item in DATA_SAMPLER_DIRECTORY.iterdir()
            )
    if existing:
        raise ValueError(
            "post-structure Generation-4 artifacts exist: " + ", ".join(existing)
        )
    return {
        "prohibitedArtifactPaths": list(PROHIBITED_ARTIFACT_PATHS),
        "existingArtifactCount": 0,
        "matchSamplerDirectoryEmpty": True,
    }


def _build_closure(created_utc: str) -> dict[str, Any]:
    _canonical_utc(created_utc, "closure createdUtc")
    pins, _ = _authenticate_inputs()
    structural = _recompute_structural_evidence()
    _assert_expected_structure(structural)
    absence = _absence_evidence()
    _recheck_inputs(pins)
    return {
        "schemaVersion": 1,
        "kind": KIND,
        "createdUtc": created_utc,
        "status": STATUS,
        "profileId": PROFILE_ID,
        "abortReason": ABORT_REASON,
        "structuralEvidence": structural,
        "absenceEvidence": absence,
        "identities": pins,
        "producer": _identity(
            Path(__file__).resolve(),
            reported_path="tools/omega_nnue/king_state_generation4_abort.py",
        ),
        "finalStageSeal": True,
    }


def _write_temporary(path: Path, payload: bytes) -> Path:
    handle, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return temporary


def _publish_no_clobber(path: Path, payload: bytes) -> dict[str, Any]:
    path = path.resolve()
    if path.exists():
        raise FileExistsError(f"refusing to replace {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _write_temporary(path, payload)
    published = False
    try:
        if path.exists():
            raise FileExistsError(f"closure appeared during publication: {path}")
        os.link(temporary, path)
        published = True
        result = _identity(path)
        temporary.unlink()
        return result
    except BaseException:
        try:
            temporary_identity = _identity(temporary)
        except (FileNotFoundError, OSError):
            temporary_identity = None
        if published and temporary_identity is not None:
            try:
                current = _identity(path)
            except (FileNotFoundError, OSError):
                current = None
            if current is not None and _same_identity(current, temporary_identity):
                try:
                    path.unlink()
                except OSError:
                    pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _verify_path(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if path != OUTPUT.resolve():
        raise ValueError("closure verification must use the canonical path")
    before = _identity(path)
    closure = _load_json(path, "Generation-4 structural-abort closure")
    if set(closure) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "status",
        "profileId",
        "abortReason",
        "structuralEvidence",
        "absenceEvidence",
        "identities",
        "producer",
        "finalStageSeal",
    }:
        raise ValueError("closure field inventory changed")
    expected = _build_closure(
        _canonical_utc(closure.get("createdUtc"), "closure createdUtc")
    )
    if not _exact_equal(closure, expected):
        raise ValueError("closure differs from full structural recomputation")
    after = _identity(path)
    if not _same_identity(before, after):
        raise ValueError("closure changed during verification")
    return after


def _create(args: argparse.Namespace) -> None:
    output = args.output.expanduser().resolve()
    if output != OUTPUT.resolve():
        raise ValueError("closure must use its canonical preregistered path")
    if output.exists():
        raise FileExistsError(f"refusing to replace {output}")
    closure = _build_closure(_utc_now())
    published = _publish_no_clobber(output, _canonical_json(closure))
    try:
        _verify_path(output)
    except BaseException:
        try:
            current = _identity(output)
        except (FileNotFoundError, OSError):
            current = None
        if current is not None and _same_identity(current, published):
            try:
                output.unlink()
            except OSError:
                pass
        raise
    print(f"Published target-opaque Generation-4 structural abort: {output}")


def _verify(args: argparse.Namespace) -> None:
    identity = _verify_path(args.output)
    print(
        "Verified target-opaque Generation-4 structural abort: "
        f"{identity['sha256']}"
    )


def _self_test() -> None:
    try:
        _strict_json('{"x":1,"x":2}', "duplicate-key test")
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate JSON keys were accepted")
    try:
        _repo_path("../escape")
    except ValueError:
        pass
    else:
        raise AssertionError("repository escape was accepted")
    _canonical_utc("2026-07-23T12:34:56.123456Z", "self-test timestamp")

    with tempfile.TemporaryDirectory(prefix="omega-g4-abort-self-test-") as directory:
        target = Path(directory) / "closure.json"
        payload = _canonical_json({"synthetic": True})
        first = _publish_no_clobber(target, payload)
        if target.read_bytes() != payload or first["sha256"] != _sha256(target):
            raise AssertionError("no-clobber publication changed the payload")
        try:
            _publish_no_clobber(target, b"replacement")
        except FileExistsError:
            pass
        else:
            raise AssertionError("no-clobber publication replaced an output")
        if target.read_bytes() != payload:
            raise AssertionError("failed publication damaged the existing output")

    # Build the exact in-memory production document from the actual frozen
    # corpus without publishing it.
    closure = _build_closure("2026-07-23T12:34:56.123456Z")
    _assert_expected_structure(closure["structuralEvidence"])
    if closure["absenceEvidence"]["existingArtifactCount"] != 0:
        raise AssertionError("absence proof is inconsistent")
    if (
        set(closure["identities"]) != set(IDENTITY_PATHS)
        or closure["producer"]["path"]
        != "tools/omega_nnue/king_state_generation4_abort.py"
    ):
        raise AssertionError("closure identity inventory changed")
    print(
        "Generation 4 structural-abort self-tests passed "
        "(5,120 roots; 183,881 children; 4,994 guaranteed eligible)."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--output", type=Path, default=OUTPUT)
    verify = commands.add_parser("verify")
    verify.add_argument("--output", type=Path, default=OUTPUT)
    commands.add_parser("self-test")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        _create(args)
    elif args.command == "verify":
        _verify(args)
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
