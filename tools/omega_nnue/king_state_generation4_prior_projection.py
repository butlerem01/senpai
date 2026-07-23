#!/usr/bin/env python3
"""Project the frozen G3 target-opaque inventory into unique Omega OFENs.

The production command deliberately reuses the unchanged generation-3
lexical scanner and opening/transcript replay implementation.  It never
decodes a score, result, outcome, label, or target.  The only captured value
is each normalized six-field OFEN passed to ``deep._leakage_keys``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
from typing import Any, Callable, Iterable, Mapping, Sequence

import deep_hce_v2 as deep
import king_state_v3 as generation3
import omega_nnue as imported_omega_nnue
import select_screen as screen


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v4-g3-prior-projection-v1"
MANIFEST_KIND = "omega-nnue-king-state-v4-prior-projection-manifest"
ROW_KIND = "omega-nnue-king-state-v4-prior-projected-ofen"
STATUS = "complete-target-free-legacy-projection"
EXPECTED_ARTIFACT_COUNT = 753
EXPECTED_CATALOG_BYTES = 268_150
EXPECTED_CATALOG_SHA256 = (
    "d7a124593d6d11a675481a848222db9733a972c50a585b4e14eee1ccf58562ab"
)
EXPECTED_CLOSURE_BYTES = 24_919
EXPECTED_CLOSURE_SHA256 = (
    "a9876f77975fe60c1b7de779e09ba27e93be9980254c6f41b05cbb7786429e81"
)
EXPECTED_SCANNER_BYTES = 362_242
EXPECTED_SCANNER_SHA256 = (
    "ffe8625903269567eee083b96dbf38b18f1be6c1a0d94b8fabcb98bdba12e2db"
)
EXPECTED_DEEP_BYTES = 148_725
EXPECTED_DEEP_SHA256 = (
    "9e9911b0a56191e5209d6276c004cf36a94fe1530eb915baf1ad5a72d32f7702"
)
EXPECTED_PHASE_BYTES = 31_219
EXPECTED_PHASE_SHA256 = (
    "d58f1cb9150c79910c460dea7e95aefd2153e9ce3c8866cd2da3f5a953d591b9"
)
EXPECTED_SCREEN_BYTES = 39_442
EXPECTED_SCREEN_SHA256 = (
    "304172e583b4c963718191017b4f8d2426aea325dd42337c197496ee738677ee"
)
CATALOG_ERA_OMEGA_NNUE_IDENTITY = {
    "path": "tools/omega_nnue/omega_nnue.py",
    "bytes": 44_266,
    "sha256": "bbab323356cb1f7194852af13c0ea632d7262f90d14d94032a1bcf88da2c0eb2",
}
CATALOG_ERA_COMMIT = "39a2519adda56a2f0bb0a8df77d9a4276f180c70"
CATALOG_ERA_OMEGA_NNUE_BLOB_SHA1 = "bce8258d3f75782b29eea625bb99373c38f6c5ca"
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
GAME_ID_FIELDS = frozenset({"gameid", "sourcegameid"})
RUN_ID_FIELDS = frozenset({"runid", "sourcerunid"})
RESERVED_SOURCE_RUN_ID = "omega-decision-v1-source-2026072201"
RESERVED_PROVENANCE_TAG_PREFIX = "rules-only-pair:random-pair-"
RESERVED_PROVENANCE_TAG_COUNT = 8192
CREATED_UTC_FLOOR = datetime(2026, 7, 20, 0, 33, 42, tzinfo=timezone.utc)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = (
    REPO
    / "build-msvc"
    / "data-generation"
    / "deep-hce-v4"
    / "target-opaque-exclusion-catalog.json"
)
DEFAULT_CLOSURE = (
    REPO / "validation" / "omega-nnue-king-state-v3-closure.json"
)
DEFAULT_OUTPUT = (
    REPO
    / "build-msvc"
    / "data-generation"
    / "g3-prior-projection-v1"
    / "positions.jsonl"
)
DEFAULT_MANIFEST = DEFAULT_OUTPUT.with_suffix(".manifest.json")

G3_CATALOG_FIELDS = {
    "allDeclaredRootsExistedAsDirectories",
    "allOtherValuesSkippedLexically",
    "amendment003RequiredRoots",
    "artifactBytes",
    "artifactCount",
    "artifacts",
    "declaredRootArtifactCounts",
    "declaredRoots",
    "decodedValueFields",
    "destinationExcluded",
    "finalTargetFreeInventory",
    "forbiddenInputSignatureCount",
    "forbiddenPositionOccurrenceCount",
    "freshGenerationDestinationSubtreesExcluded",
    "freshRulesOnlySourceExcludedAndPinnedSeparately",
    "generatedOpeningReplayRows",
    "generatedTranscriptParityRows",
    "includesGeneration1Corpus",
    "includesGeneration2Corpus",
    "includesHistorySnapshot",
    "kind",
    "newlyImplicitOpeningRowsAddedToExclusionCount",
    "opaquePositionLikeMetadataFields",
    "openingAndTranscriptReplay",
    "ordinaryStringRawOfenShapeGuard",
    "phaseAndSymmetryImplementation",
    "positionLikeKeyCounts",
    "postPublicationVerificationUsesSameDestinationExclusions",
    "postScanArtifactPathSetMatched",
    "profileId",
    "rawOfenFamilyAndContainerValuesDecoded",
    "scanner",
    "schemaVersion",
    "selectedStringLeafContainers",
    "stableArtifactCount",
    "targetFieldsDecoded",
    "targetFieldsEmitted",
    "unclassifiedPositionLikeKeys",
    "workspaceComplementSentinel",
}

MANIFEST_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "status",
    "createdUtc",
    "targetOpaque",
    "projection",
    "projectionSchema",
    "projectedPositionCount",
    "sourceCatalog",
    "generation3Closure",
    "sourceInventory",
    "sourceInventorySha256",
    "implementationIdentities",
    "openingReplayRuntime",
    "evidence",
    "informationBoundary",
    "historicalIdentifiers",
    "finalStageSeal",
}


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: str | Path) -> dict[str, Any]:
    path = _resolve(path)
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "sha256": _sha256(path)}


def _strict_identity(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{description} is not a strict path/bytes/SHA-256 identity")
    if (
        not isinstance(value["path"], str)
        or not value["path"]
        or type(value["bytes"]) is not int
        or value["bytes"] < 0
        or not isinstance(value["sha256"], str)
        or not HEX_SHA256.fullmatch(value["sha256"])
    ):
        raise ValueError(f"{description} has invalid identity field types")
    canonical_path = str(_resolve(value["path"]))
    if value["path"] != canonical_path:
        raise ValueError(f"{description} path is not canonical and absolute")
    expected = dict(value)
    if not HEX_SHA256.fullmatch(expected["sha256"]):
        raise ValueError(f"{description} has an invalid SHA-256")
    actual = _identity(expected["path"])
    if actual != expected:
        raise ValueError(f"{description} identity changed")
    return actual


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _strict_keys(value: Any, expected: set[str], description: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ValueError(
            f"{description} field inventory differs: expected {sorted(expected)}, "
            f"found {actual}"
        )
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    folded: set[str] = set()
    for key, value in pairs:
        normalized = key.casefold()
        if normalized in folded:
            raise ValueError(f"duplicate/case-colliding JSON key: {key!r}")
        folded.add(normalized)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _strict_json_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite JSON number is forbidden: {value}")
    return result


def _strict_json_loads(text: str, description: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_strict_json_float,
        )
    except (TypeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"invalid strict JSON in {description}") from error


def _require_exact_json(value: Any, expected: Any, description: str) -> None:
    """Require recursively identical JSON values without bool/int/float aliasing."""

    if type(value) is not type(expected):
        raise ValueError(
            f"{description} has JSON type {type(value).__name__}; "
            f"expected {type(expected).__name__}"
        )
    if isinstance(expected, dict):
        if set(value) != set(expected):
            raise ValueError(f"{description} object field inventory changed")
        for key in expected:
            _require_exact_json(value[key], expected[key], f"{description}.{key}")
    elif isinstance(expected, list):
        if len(value) != len(expected):
            raise ValueError(f"{description} array length changed")
        for index, item in enumerate(expected):
            _require_exact_json(value[index], item, f"{description}[{index}]")
    elif value != expected:
        raise ValueError(f"{description} value changed")


def _validate_created_utc(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError("createdUtc must be canonical UTC to whole seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ValueError("createdUtc is not a real UTC timestamp") from error
    now = datetime.now(timezone.utc)
    if parsed < CREATED_UTC_FLOOR or parsed > now.replace(microsecond=0) + timedelta(
        minutes=5
    ):
        raise ValueError("createdUtc is outside the authorized publication window")
    return value


def _load_json_snapshot(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _resolve(path)
    before = _identity(path)
    value = _strict_json_loads(
        path.read_text(encoding="utf-8-sig"), str(path)
    )
    after = _identity(path)
    if before != after:
        raise ValueError(f"input changed while being read: {path}")
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value, before


def _git_executable() -> Path:
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("Git is required to load the frozen G3 source blob")
    return _resolve(executable)


def _git_output(*arguments: str) -> bytes:
    """Run a built-in Git command with ambient config/redirects disabled."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    command = [
        str(_git_executable()),
        "-c",
        f"safe.directory={REPO}",
        "-C",
        str(REPO),
        *arguments,
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"authenticated frozen-source Git command failed: {detail}")
    return completed.stdout


def _frozen_omega_module() -> types.ModuleType:
    """Load the exact catalog-era omega_nnue.py blob without checking it out."""

    toplevel = _resolve(
        _git_output("rev-parse", "--show-toplevel").decode("utf-8").strip()
    )
    if toplevel != _resolve(REPO):
        raise ValueError("Git resolved a different repository root")
    commit = _git_output(
        "rev-parse", "--verify", f"{CATALOG_ERA_COMMIT}^{{commit}}"
    ).decode("ascii").strip()
    if commit != CATALOG_ERA_COMMIT:
        raise ValueError("catalog-era commit does not resolve exactly")
    source_spec = f"{CATALOG_ERA_COMMIT}:tools/omega_nnue/omega_nnue.py"
    blob = _git_output("rev-parse", "--verify", source_spec).decode("ascii").strip()
    if blob != CATALOG_ERA_OMEGA_NNUE_BLOB_SHA1:
        raise ValueError("catalog-era commit:path resolves to a different Git blob")
    if _git_output("cat-file", "-t", blob).decode("ascii").strip() != "blob":
        raise ValueError("catalog-era frozen source object is not a Git blob")
    payload = _git_output("cat-file", "blob", blob)
    if (
        len(payload) != int(CATALOG_ERA_OMEGA_NNUE_IDENTITY["bytes"])
        or hashlib.sha256(payload).hexdigest()
        != CATALOG_ERA_OMEGA_NNUE_IDENTITY["sha256"]
    ):
        raise ValueError("frozen G3 omega_nnue Git blob identity changed")
    module_name = "_omega_nnue_g3_frozen_blob"
    module = types.ModuleType(module_name)
    module.__file__ = (
        f"git:{CATALOG_ERA_COMMIT}:tools/omega_nnue/omega_nnue.py"
    )
    previous = sys.modules.pop(module_name, None)
    sys.modules[module_name] = module
    try:
        exec(compile(payload, module.__file__, "exec"), module.__dict__)
        if sys.modules.get(module_name) is not module:
            raise RuntimeError("frozen G3 module replaced its reserved module slot")
    finally:
        displaced = sys.modules.pop(module_name, None)
        if previous is not None:
            sys.modules[module_name] = previous
        if displaced is not module:
            raise RuntimeError("frozen G3 module-slot restoration was not exact")
    return module


def _clear_screen_caches() -> None:
    for function_name in (
        "transformed_observable",
        "canonical_observable",
        "nnue_observable_key",
        "observable_symmetries",
        "identity_key",
        "input_keys",
    ):
        function = getattr(screen, function_name, None)
        clear = getattr(function, "cache_clear", None)
        if clear is not None:
            clear()


@contextmanager
def _frozen_g3_semantics() -> Iterable[types.ModuleType]:
    """Bind the unchanged scanner to its exact catalog-era parser/features."""

    frozen = _frozen_omega_module()
    if (
        deep.input_keys is not screen.input_keys
        or deep.observable_ofen is not screen.observable_ofen
        or deep.PIECE_INDEX != frozen.PIECE_INDEX
    ):
        raise ValueError("G3 deep/symmetry import graph changed")
    bindings = (
        (generation3, "parse_ofen", frozen.parse_ofen),
        (deep, "parse_ofen", frozen.parse_ofen),
        (screen, "parse_ofen", frozen.parse_ofen),
        (
            screen,
            "_active_features_from_parsed",
            frozen._active_features_from_parsed,
        ),
    )
    originals = [(module, name, getattr(module, name)) for module, name, _ in bindings]
    _clear_screen_caches()
    for module, name, replacement in bindings:
        setattr(module, name, replacement)
    try:
        yield frozen
    finally:
        for module, name, original in reversed(originals):
            setattr(module, name, original)
        _clear_screen_caches()
        if any(
            getattr(module, name) is not original
            for module, name, original in originals
        ):
            raise RuntimeError("frozen G3 semantic binding restoration failed")


def _scan_string(text: str, start: int) -> int:
    if start >= len(text) or text[start] != '"':
        raise ValueError("expected JSON string")
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == '"':
            return index + 1
        if character == "\\":
            index += 2
        else:
            if ord(character) < 0x20:
                raise ValueError("control character in JSON string")
            index += 1
    raise ValueError("unterminated JSON string")


def _scan_value(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        raise ValueError("missing JSON value")
    if text[index] == '"':
        return _scan_string(text, index)
    if text[index] in "[{":
        stack = ["]" if text[index] == "[" else "}"]
        index += 1
        while index < len(text) and stack:
            character = text[index]
            if character == '"':
                index = _scan_string(text, index)
                continue
            if character in "[{":
                stack.append("]" if character == "[" else "}")
            elif character in "]}":
                if character != stack.pop():
                    raise ValueError("mismatched JSON container")
            index += 1
        if stack:
            raise ValueError("unterminated JSON container")
        return index
    while index < len(text) and text[index] not in ",]}" and not text[index].isspace():
        index += 1
    return index


def _object_spans(text: str) -> dict[str, tuple[str, str]]:
    index = 0
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] != "{":
        raise ValueError("expected JSON object")
    index += 1
    result: dict[str, tuple[str, str]] = {}
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == "}":
            index += 1
            break
        end = _scan_string(text, index)
        key = _strict_json_loads(text[index:end], "historical JSON field name")
        if not isinstance(key, str):
            raise ValueError("historical JSON field name is not a string")
        folded = str(key).casefold()
        if folded in result:
            raise ValueError("case-colliding historical JSON field")
        index = end
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index] != ":":
            raise ValueError("missing JSON object colon")
        index += 1
        while index < len(text) and text[index].isspace():
            index += 1
        start = index
        index = _scan_value(text, index)
        result[folded] = (str(key), text[start:index])
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            index += 1
            break
        raise ValueError("missing JSON object comma/end")
    if text[index:].strip():
        raise ValueError("trailing JSON object content")
    return result


def _array_slices(text: str) -> list[str]:
    index = 0
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] != "[":
        raise ValueError("expected JSON array")
    index += 1
    result: list[str] = []
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == "]":
            index += 1
            break
        start = index
        index = _scan_value(text, index)
        result.append(text[start:index])
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "]":
            index += 1
            break
        raise ValueError("missing JSON array comma/end")
    if text[index:].strip():
        raise ValueError("trailing JSON array content")
    return result


def _decode_id(raw: str, description: str) -> str:
    raw = raw.strip()
    if not raw.startswith('"') or _scan_string(raw, 0) != len(raw):
        raise ValueError(f"{description} must be a JSON string")
    value = _strict_json_loads(raw, description)
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError(f"{description} must be a bounded nonempty string")
    return value


def _collect_ids_from_value(
    raw: str, game_ids: set[str], run_ids: set[str], description: str
) -> None:
    raw = raw.strip()
    if not raw:
        raise ValueError(f"{description}: empty JSON value")
    if raw.startswith("["):
        for index, child in enumerate(_array_slices(raw)):
            _collect_ids_from_value(
                child, game_ids, run_ids, f"{description}[{index}]"
            )
        return
    if not raw.startswith("{"):
        return
    for folded, (key, value_raw) in _object_spans(raw).items():
        normalized = re.sub(r"[^a-z0-9]", "", folded)
        if normalized in GAME_ID_FIELDS:
            game_ids.add(_decode_id(value_raw, f"{description}.{key}"))
        elif normalized in RUN_ID_FIELDS:
            run_ids.add(_decode_id(value_raw, f"{description}.{key}"))
        elif value_raw.lstrip().startswith(("{", "[")):
            _collect_ids_from_value(
                value_raw, game_ids, run_ids, f"{description}.{key}"
            )


def _historical_identifier_inventory(
    identities: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    game_ids: set[str] = set()
    run_ids: set[str] = set()
    for identity in identities:
        expected = _strict_identity(dict(identity), "historical identifier source")
        path = Path(expected["path"])
        before = _identity(path)
        if before != expected:
            raise ValueError("historical identifier source changed before decoding")
        if path.suffix.lower() == ".jsonl":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                for line_number, line in enumerate(stream, 1):
                    if line.strip():
                        _collect_ids_from_value(
                            line, game_ids, run_ids, f"{path}:{line_number}"
                        )
        else:
            _collect_ids_from_value(
                path.read_text(encoding="utf-8-sig"),
                game_ids,
                run_ids,
                str(path),
            )
        if _identity(path) != before:
            raise ValueError("historical identifier source changed while decoding")
    games = sorted(game_ids)
    runs = sorted(run_ids)
    all_ids = game_ids | run_ids
    reserved_tags = {
        f"{RESERVED_PROVENANCE_TAG_PREFIX}{index:06d}"
        for index in range(1, RESERVED_PROVENANCE_TAG_COUNT + 1)
    }
    collisions = sorted(
        ({RESERVED_SOURCE_RUN_ID} | reserved_tags) & all_ids
    )
    if collisions:
        raise ValueError(
            "new G4 source identifiers collide with historical allowlisted IDs: "
            + repr(collisions[:20])
        )
    return {
        "decodedFieldNames": sorted(GAME_ID_FIELDS | RUN_ID_FIELDS),
        "gameIds": games,
        "runIds": runs,
        "gameIdCount": len(games),
        "runIdCount": len(runs),
        "gameIdsSha256": _canonical_digest(games),
        "runIdsSha256": _canonical_digest(runs),
        "reservedNewSourceRunId": RESERVED_SOURCE_RUN_ID,
        "reservedNewProvenanceTagPrefix": RESERVED_PROVENANCE_TAG_PREFIX,
        "reservedNewProvenanceTagCount": RESERVED_PROVENANCE_TAG_COUNT,
        "completeDecodedReservedCollisionCount": 0,
    }


def _verify_identifier_list(value: Any, description: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(
            not isinstance(item, str) or not item or len(item) > 4096
            for item in value
        )
        or value != sorted(set(value))
    ):
        raise ValueError(f"{description} is not a sorted unique string inventory")
    return list(value)


def _verify_historical_identifiers(value: Any) -> dict[str, Any]:
    fields = {
        "decodedFieldNames",
        "gameIds",
        "runIds",
        "gameIdCount",
        "runIdCount",
        "gameIdsSha256",
        "runIdsSha256",
        "reservedNewSourceRunId",
        "reservedNewProvenanceTagPrefix",
        "reservedNewProvenanceTagCount",
        "completeDecodedReservedCollisionCount",
    }
    inventory = _strict_keys(value, fields, "historical identifier inventory")
    if inventory.get("decodedFieldNames") != sorted(GAME_ID_FIELDS | RUN_ID_FIELDS):
        raise ValueError("historical decoded identifier field names changed")
    games = _verify_identifier_list(inventory.get("gameIds"), "historical game IDs")
    runs = _verify_identifier_list(inventory.get("runIds"), "historical run IDs")
    if (
        type(inventory.get("gameIdCount")) is not int
        or inventory["gameIdCount"] != len(games)
        or type(inventory.get("runIdCount")) is not int
        or inventory["runIdCount"] != len(runs)
        or inventory.get("gameIdsSha256") != _canonical_digest(games)
        or inventory.get("runIdsSha256") != _canonical_digest(runs)
        or inventory.get("reservedNewSourceRunId") != RESERVED_SOURCE_RUN_ID
        or inventory.get("reservedNewProvenanceTagPrefix")
        != RESERVED_PROVENANCE_TAG_PREFIX
        or inventory.get("reservedNewProvenanceTagCount")
        != RESERVED_PROVENANCE_TAG_COUNT
        or inventory.get("completeDecodedReservedCollisionCount") != 0
    ):
        raise ValueError("historical identifier evidence changed")
    reserved_tags = {
        f"{RESERVED_PROVENANCE_TAG_PREFIX}{index:06d}"
        for index in range(1, RESERVED_PROVENANCE_TAG_COUNT + 1)
    }
    collisions = ({RESERVED_SOURCE_RUN_ID} | reserved_tags) & (set(games) | set(runs))
    if collisions:
        raise ValueError("new G4 identifiers collide with historical identifiers")
    return inventory


def _publish_temporary_no_clobber(temporary: str | Path, target: str | Path) -> None:
    temporary = _resolve(temporary)
    target = _resolve(target)
    try:
        os.link(temporary, target)
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite {target}") from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_bytes(path: str | Path, payload: bytes) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _publish_temporary_no_clobber(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _projection_payload(ofens: Sequence[str]) -> bytes:
    return b"".join(
        (
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": ROW_KIND,
                    "positionId": "g3-prior-"
                    + hashlib.sha256(ofen.encode("utf-8")).hexdigest(),
                    "ofen": ofen,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for ofen in ofens
    )


def _runtime_bundle_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = _strict_keys(
        dict(value),
        {"path", "assemblyRelativePath", "fileCount", "bytes", "sha256"},
        "opening replay app-local runtime bundle",
    )
    if (
        not isinstance(expected["path"], str)
        or expected["path"] != str(_resolve(expected["path"]))
        or not isinstance(expected["assemblyRelativePath"], str)
        or not expected["assemblyRelativePath"]
        or type(expected["fileCount"]) is not int
        or expected["fileCount"] < 0
        or type(expected["bytes"]) is not int
        or expected["bytes"] < 0
        or not isinstance(expected["sha256"], str)
        or not HEX_SHA256.fullmatch(expected["sha256"])
    ):
        raise ValueError("opening replay runtime bundle fields are malformed")
    root = _resolve(expected["path"])
    if not root.is_dir():
        raise ValueError("opening replay runtime bundle is not a directory")
    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    canonical = bytearray()
    total_bytes = 0
    for path in files:
        if path.is_symlink():
            raise ValueError("opening replay runtime contains a symbolic link")
        identity = _identity(path)
        relative = path.relative_to(root).as_posix()
        total_bytes += int(identity["bytes"])
        canonical.extend(
            f"{relative}\t{identity['bytes']}\t{identity['sha256']}\n".encode(
                "utf-8"
            )
        )
    actual = {
        "path": str(root),
        "assemblyRelativePath": str(expected["assemblyRelativePath"]),
        "fileCount": len(files),
        "bytes": total_bytes,
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }
    if actual != expected:
        raise ValueError("opening replay app-local runtime bundle identity changed")
    assembly = root / actual["assemblyRelativePath"]
    if not assembly.is_file():
        raise ValueError("opening replay runtime lacks its declared assembly")
    return actual


def _verify_opening_runtime(value: Any) -> dict[str, Any]:
    expected_keys = set(generation3.OPENING_REPLAY_PINS) | {"appLocalRuntimeBundle"}
    runtime = _strict_keys(value, expected_keys, "opening replay runtime")
    verified: dict[str, Any] = {}
    for key in sorted(expected_keys):
        if key == "appLocalRuntimeBundle":
            verified[key] = _runtime_bundle_identity(runtime[key])
        else:
            verified[key] = _strict_identity(runtime[key], f"opening runtime {key}")
    if verified != runtime:
        raise ValueError("opening replay runtime is not canonical")
    if verified != generation3._opening_replay_identity_bundle():
        raise ValueError("opening replay runtime differs from the frozen G3 bundle")
    return verified


def _expected_catalog_identity(path: Path) -> dict[str, Any]:
    expected = {
        "path": str(_resolve(DEFAULT_CATALOG)),
        "bytes": EXPECTED_CATALOG_BYTES,
        "sha256": EXPECTED_CATALOG_SHA256,
    }
    if _resolve(path) != _resolve(DEFAULT_CATALOG):
        raise ValueError("only the frozen G3 exclusion catalog path is accepted")
    return _strict_identity(expected, "frozen G3 exclusion catalog")


def _expected_local_identity(
    path: Path, expected_bytes: int, expected_sha256: str, description: str
) -> dict[str, Any]:
    return _strict_identity(
        {
            "path": str(_resolve(path)),
            "bytes": expected_bytes,
            "sha256": expected_sha256,
        },
        description,
    )


def _expected_closure_identity(path: Path) -> dict[str, Any]:
    expected = {
        "path": str(_resolve(DEFAULT_CLOSURE)),
        "bytes": EXPECTED_CLOSURE_BYTES,
        "sha256": EXPECTED_CLOSURE_SHA256,
    }
    if _resolve(path) != _resolve(DEFAULT_CLOSURE):
        raise ValueError("only the published G3 closure path is accepted")
    # The closure is label-derived.  Its published content identity is an
    # authorization/provenance pin only; never parse any of its values here.
    return _strict_identity(expected, "published G3 closure")


def _verify_catalog_and_sources(
    catalog_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    catalog_identity = _expected_catalog_identity(catalog_path)
    catalog, snapshot = _load_json_snapshot(catalog_path)
    if snapshot != catalog_identity:
        raise ValueError("G3 exclusion catalog changed during validation")
    _strict_keys(catalog, G3_CATALOG_FIELDS, "G3 exclusion catalog")
    if (
        catalog.get("schemaVersion") != 1
        or catalog.get("kind")
        != "omega-nnue-king-state-v3-target-opaque-exclusion-catalog"
        or catalog.get("profileId") != "king-state-v3-deep-hce-v4"
        or catalog.get("targetFieldsDecoded") != 0
        or catalog.get("targetFieldsEmitted") != 0
        or catalog.get("allOtherValuesSkippedLexically") is not True
        or catalog.get("postScanArtifactPathSetMatched") is not True
        or catalog.get("unclassifiedPositionLikeKeys") != []
    ):
        raise ValueError("G3 exclusion catalog is not the frozen target-opaque contract")
    artifacts = catalog.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != EXPECTED_ARTIFACT_COUNT:
        raise ValueError("G3 exclusion catalog does not contain exactly 753 artifacts")
    verified: list[dict[str, Any]] = []
    paths: set[str] = set()
    for index, pin in enumerate(artifacts):
        identity = _strict_identity(pin, f"G3 artifact[{index}]")
        artifact_path = Path(identity["path"])
        if (
            not artifact_path.is_file()
            or artifact_path.suffix.lower() not in {".json", ".jsonl"}
        ):
            raise ValueError(f"G3 artifact[{index}] is not a JSON/JSONL regular file")
        if identity["path"] in paths:
            raise ValueError("G3 artifact inventory contains a duplicate path")
        paths.add(identity["path"])
        verified.append(identity)
    if verified != sorted(verified, key=lambda item: item["path"].lower()):
        raise ValueError("G3 artifact inventory order changed")
    if (
        catalog.get("artifactCount") != len(verified)
        or catalog.get("stableArtifactCount") != len(verified)
        or catalog.get("artifactBytes")
        != sum(int(identity["bytes"]) for identity in verified)
    ):
        raise ValueError("G3 artifact aggregate evidence changed")
    scanner = _strict_identity(catalog.get("scanner"), "G3 lexical scanner")
    expected_scanner = _expected_local_identity(
        Path(generation3.__file__),
        EXPECTED_SCANNER_BYTES,
        EXPECTED_SCANNER_SHA256,
        "frozen generation-3 scanner",
    )
    if scanner != expected_scanner:
        raise ValueError("imported king_state_v3.py differs from the frozen scanner")
    expected_deep = _expected_local_identity(
        Path(deep.__file__),
        EXPECTED_DEEP_BYTES,
        EXPECTED_DEEP_SHA256,
        "frozen deep leakage implementation",
    )
    expected_phase = _expected_local_identity(
        Path(generation3.incidence.__file__),
        EXPECTED_PHASE_BYTES,
        EXPECTED_PHASE_SHA256,
        "frozen phase/symmetry incidence implementation",
    )
    expected_screen = _expected_local_identity(
        Path(screen.__file__),
        EXPECTED_SCREEN_BYTES,
        EXPECTED_SCREEN_SHA256,
        "frozen selection/symmetry implementation",
    )
    if catalog.get("phaseAndSymmetryImplementation") != expected_phase:
        raise ValueError("G3 phase/symmetry implementation pin changed")
    # Force the exact dependencies to be present before the 3.47 GB scan.
    if expected_deep != _identity(Path(deep.__file__)):
        raise ValueError("deep implementation changed during validation")
    if expected_screen != _identity(Path(screen.__file__)):
        raise ValueError("selection/symmetry implementation changed during validation")
    _frozen_omega_module()
    runtime = _verify_opening_runtime(
        catalog.get("openingAndTranscriptReplay", {}).get("runtime")
    )
    if runtime != catalog.get("openingAndTranscriptReplay", {}).get(
        "runtimeAfterReplay"
    ):
        raise ValueError("G3 catalog opening runtime before/after identities differ")
    return catalog, catalog_identity, verified


def _capture_inventory(
    paths: Sequence[Path],
    *,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> tuple[
    set[str],
    list[dict[str, Any]],
    int,
    Counter[str],
    int,
    dict[str, Any],
    set[str],
    int,
]:
    resolved_paths = [_resolve(path) for path in paths]
    before_identities = [_identity(path) for path in resolved_paths]
    original_leakage = generation3.deep._leakage_keys
    if original_leakage is not deep._leakage_keys:
        raise ValueError("generation-3 scanner and projection imported different deep modules")
    captured: set[str] = set()
    calls = 0

    def capture(value: str) -> Any:
        nonlocal calls
        normalized = generation3._normalize_full_ofen(
            value, label="G4 prior-projection capture"
        )
        result = original_leakage(value)
        captured.add(normalized)
        calls += 1
        return result

    try:
        with _frozen_g3_semantics():
            generation3.deep._leakage_keys = capture
            try:
                inventory = generation3._lexical_inventory(
                    resolved_paths, progress=progress
                )
            finally:
                generation3.deep._leakage_keys = original_leakage
    finally:
        generation3.deep._leakage_keys = original_leakage
        if (
            generation3.deep._leakage_keys is not original_leakage
            or deep._leakage_keys is not original_leakage
        ):
            raise RuntimeError("deep._leakage_keys wrapper restoration failed")
    after_identities = [_identity(path) for path in resolved_paths]
    if after_identities != before_identities:
        raise ValueError("G3 source inventory changed during lexical capture")
    return (*inventory, captured, calls)


def _projection_evidence(
    catalog: Mapping[str, Any],
    replay: Mapping[str, Any],
    *,
    calls: int,
    projected_count: int,
) -> dict[str, Any]:
    return {
        "artifactCount": int(catalog["artifactCount"]),
        "artifactBytes": int(catalog["artifactBytes"]),
        "stableArtifactCount": int(catalog["stableArtifactCount"]),
        "rawDecodedPositionOccurrenceCount": int(
            replay["rawDecodedPositionOccurrenceCount"]
        ),
        "replayPrefixRecordCount": int(replay["prefixRecordCount"]),
        "leakageKeyCallCount": calls,
        "forbiddenPositionOccurrenceCount": int(
            catalog["forbiddenPositionOccurrenceCount"]
        ),
        "forbiddenInputSignatureCount": int(
            catalog["forbiddenInputSignatureCount"]
        ),
        "projectedUniqueOfenCount": projected_count,
        "positionLikeKeyCountsSha256": _canonical_digest(
            catalog["positionLikeKeyCounts"]
        ),
        "replayAuditSha256": _canonical_digest(replay),
        "finalTargetFreeInventorySha256": _canonical_digest(
            catalog["finalTargetFreeInventory"]
        ),
    }


def _verify_recomputation(
    catalog: Mapping[str, Any],
    pins: Sequence[dict[str, Any]],
    signatures: set[str],
    position_count: int,
    key_counts: Mapping[str, int],
    stable_count: int,
    replay: Mapping[str, Any],
    captured: set[str],
    calls: int,
) -> None:
    if list(pins) != catalog["artifacts"]:
        raise ValueError("recomputed G3 source pins differ from the frozen inventory")
    if not (
        len(pins)
        == stable_count
        == catalog["artifactCount"]
        == catalog["stableArtifactCount"]
        == EXPECTED_ARTIFACT_COUNT
    ):
        raise ValueError("recomputed stable G3 artifact count differs")
    if sum(identity["bytes"] for identity in pins) != catalog["artifactBytes"]:
        raise ValueError("recomputed G3 artifact byte total differs")
    if not (
        position_count
        == catalog["forbiddenPositionOccurrenceCount"]
        == replay["aggregateExclusionPositionOccurrenceCount"]
    ):
        raise ValueError("recomputed forbidden position occurrence count differs")
    if not (
        len(signatures)
        == catalog["forbiddenInputSignatureCount"]
        == replay["aggregateConservativeSignatureCardinality"]
    ):
        raise ValueError("recomputed forbidden signature count differs")
    if {key: key_counts[key] for key in sorted(key_counts)} != catalog[
        "positionLikeKeyCounts"
    ]:
        raise ValueError("recomputed position-like key evidence differs")
    if dict(replay) != catalog["openingAndTranscriptReplay"]:
        raise ValueError("recomputed replay aggregate evidence differs")
    alias_checks = {
        "rawOfenFamilyAndContainerValuesDecoded": (
            "rawDecodedPositionOccurrenceCount"
        ),
        "generatedOpeningReplayRows": "openingPrefixRecordCount",
        "generatedTranscriptParityRows": "transcriptPrefixRecordCount",
        "newlyImplicitOpeningRowsAddedToExclusionCount": (
            "newlyImplicitOpeningOccurrenceCount"
        ),
    }
    for catalog_key, replay_key in alias_checks.items():
        if catalog[catalog_key] != replay[replay_key]:
            raise ValueError(f"G3 catalog/replay alias drifted: {catalog_key}")
    # Every direct scalar/container candidate reaches the leakage function
    # once during strict lexical validation and once when its exclusion
    # signatures are accumulated.  Generated replay rows reach it once.
    expected_calls = 2 * int(replay["rawDecodedPositionOccurrenceCount"]) + int(
        replay["prefixRecordCount"]
    )
    if calls != expected_calls:
        raise ValueError("captured leakage-key call count does not reconcile")
    if len(captured) != int(replay["aggregateNormalizedOfenCardinality"]):
        raise ValueError("captured unique OFEN cardinality differs from G3 evidence")
    final_inventory = generation3._final_target_free_inventory_summary(
        signatures=signatures,
        pins=pins,
        positions=position_count,
        position_like_key_counts=key_counts,
        stable_artifact_count=stable_count,
        replay_audit=replay,
        complement=catalog["workspaceComplementSentinel"],
    )
    if (
        final_inventory != catalog["finalTargetFreeInventory"]
        or final_inventory != generation3.EXPECTED_FINAL_TARGET_FREE_INVENTORY
    ):
        raise ValueError("recomputed final target-free G3 inventory seal differs")


def _implementation_identities() -> dict[str, Any]:
    _frozen_omega_module()
    return {
        "projectionBuilderSource": _identity(Path(__file__)),
        "generation3ScannerSource": _expected_local_identity(
            Path(generation3.__file__),
            EXPECTED_SCANNER_BYTES,
            EXPECTED_SCANNER_SHA256,
            "frozen generation-3 scanner",
        ),
        "deepLeakageSource": _expected_local_identity(
            Path(deep.__file__),
            EXPECTED_DEEP_BYTES,
            EXPECTED_DEEP_SHA256,
            "frozen deep leakage implementation",
        ),
        "phaseIncidenceSource": _expected_local_identity(
            Path(generation3.incidence.__file__),
            EXPECTED_PHASE_BYTES,
            EXPECTED_PHASE_SHA256,
            "frozen phase incidence implementation",
        ),
        "selectionSymmetrySource": _expected_local_identity(
            Path(screen.__file__),
            EXPECTED_SCREEN_BYTES,
            EXPECTED_SCREEN_SHA256,
            "frozen selection/symmetry implementation",
        ),
        "importedOmegaNnueSource": _identity(Path(imported_omega_nnue.__file__)),
        "catalogEraOmegaNnueGitBlob": {
            **CATALOG_ERA_OMEGA_NNUE_IDENTITY,
            "commit": CATALOG_ERA_COMMIT,
            "blobSha1": CATALOG_ERA_OMEGA_NNUE_BLOB_SHA1,
            "executionPolicy": (
                "compile exact Git blob in memory and bind parser/features in finally scope"
            ),
        },
        "gitExecutable": _identity(_git_executable()),
        "pythonRuntime": {
            **_identity(Path(sys.executable)),
            "version": sys.version,
        },
    }


def _create(args: argparse.Namespace) -> None:
    output = _resolve(args.output)
    manifest_path = _resolve(args.manifest)
    if output == manifest_path:
        raise ValueError("projection and manifest paths must differ")
    if output.exists() or manifest_path.exists():
        existing = output if output.exists() else manifest_path
        raise FileExistsError(f"refusing to overwrite {existing}")
    created_utc = _validate_created_utc(args.created_utc)

    catalog, catalog_identity, source_inventory = _verify_catalog_and_sources(
        _resolve(args.catalog)
    )
    closure_identity = _expected_closure_identity(_resolve(args.closure))
    historical_identifiers = _historical_identifier_inventory(source_inventory)

    def progress(event: str, fields: Mapping[str, Any]) -> None:
        detail = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
        print(f"[g3-prior-projection] {event} {detail}".rstrip(), file=sys.stderr)

    (
        signatures,
        pins,
        position_count,
        key_counts,
        stable_count,
        replay,
        captured,
        calls,
    ) = _capture_inventory(
        [Path(identity["path"]) for identity in source_inventory],
        progress=progress,
    )
    _verify_recomputation(
        catalog,
        pins,
        signatures,
        position_count,
        key_counts,
        stable_count,
        replay,
        captured,
        calls,
    )
    runtime = _verify_opening_runtime(replay["runtimeAfterReplay"])
    ofens = sorted(captured)
    payload = _projection_payload(ofens)
    output_identity: dict[str, Any] | None = None
    try:
        _atomic_bytes(output, payload)
        output_identity = _identity(output)
        manifest = {
            "schemaVersion": 1,
            "kind": MANIFEST_KIND,
            "profileId": PROFILE_ID,
            "status": STATUS,
            "createdUtc": created_utc,
            "targetOpaque": True,
            "projection": output_identity,
            "projectionSchema": {
                "recognizedFields": ["kind", "ofen", "positionId", "schemaVersion"],
                "unknownFieldPolicy": "abort",
                "ordering": "normalized-ofen-lexicographic",
                "uniqueness": "normalized-six-field-ofen",
            },
            "projectedPositionCount": len(ofens),
            "sourceCatalog": catalog_identity,
            "generation3Closure": closure_identity,
            "sourceInventory": source_inventory,
            "sourceInventorySha256": _canonical_digest(source_inventory),
            "implementationIdentities": _implementation_identities(),
            "openingReplayRuntime": runtime,
            "evidence": _projection_evidence(
                catalog, replay, calls=calls, projected_count=len(ofens)
            ),
            "informationBoundary": {
                "decodedValueFields": [
                    "gameid",
                    "normalized-six-field-ofen",
                    "runid",
                    "sourcegameid",
                    "sourcerunid",
                ],
                "sourceTargetOrScoreFieldsDecoded": 0,
                "targetOrScoreFieldsEmitted": 0,
                "captureHook": "deep._leakage_keys input only",
                "captureHookRestoredInFinally": True,
            },
            "historicalIdentifiers": historical_identifiers,
            "finalStageSeal": True,
        }
        _verify_projection_rows(output, output_identity, ofens)
        if (
            _identity(output) != output_identity
            or _identity(Path(catalog_identity["path"])) != catalog_identity
            or _identity(Path(closure_identity["path"])) != closure_identity
            or [_identity(Path(item["path"])) for item in source_inventory]
            != source_inventory
        ):
            raise ValueError("projection inputs/output changed before manifest publication")
        _atomic_bytes(
            manifest_path,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
    except BaseException:
        if (
            output_identity is not None
            and not manifest_path.exists()
            and output.exists()
            and _identity(output) == output_identity
        ):
            output.unlink()
        raise
    print(f"Published G3 target-free OFEN projection: {output}")
    print(f"Published strict projection manifest: {manifest_path}")


def _verify_projection_rows(
    path: Path,
    expected_identity: Mapping[str, Any],
    expected_ofens: Sequence[str],
) -> None:
    with _frozen_g3_semantics():
        _verify_projection_rows_with_frozen_semantics(
            path, expected_identity, expected_ofens
        )


def _verify_projection_rows_with_frozen_semantics(
    path: Path,
    expected_identity: Mapping[str, Any],
    expected_ofens: Sequence[str],
) -> None:
    identity = _strict_identity(dict(expected_identity), "G3 OFEN projection")
    if identity != dict(expected_identity):
        raise ValueError("projection identity is not canonical")
    normalized_expected = [
        generation3._normalize_full_ofen(ofen, label="expected G3 projection OFEN")
        for ofen in expected_ofens
    ]
    if normalized_expected != list(expected_ofens) or normalized_expected != sorted(
        set(normalized_expected)
    ):
        raise ValueError("expected projection OFEN sequence is not canonical/unique")
    count = 0
    with _resolve(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                raise ValueError(f"projection contains a blank row at line {line_number}")
            row = _strict_json_loads(line, f"projection row {line_number}")
            _strict_keys(
                row,
                {"schemaVersion", "kind", "positionId", "ofen"},
                f"projection row {line_number}",
            )
            if count >= len(normalized_expected):
                raise ValueError("projection contains more rows than recomputed OFENs")
            raw_ofen = row.get("ofen")
            if not isinstance(raw_ofen, str):
                raise ValueError(f"projection row {line_number} OFEN is not a string")
            ofen = generation3._normalize_full_ofen(
                raw_ofen, label=f"projection row {line_number}"
            )
            expected_id = "g3-prior-" + hashlib.sha256(ofen.encode("utf-8")).hexdigest()
            expected_row = {
                "schemaVersion": 1,
                "kind": ROW_KIND,
                "positionId": expected_id,
                "ofen": ofen,
            }
            _require_exact_json(row, expected_row, f"projection row {line_number}")
            if raw_ofen != ofen or ofen != normalized_expected[count]:
                raise ValueError(
                    f"projection row {line_number} differs from recomputed OFEN order"
                )
            generation3.deep._leakage_keys(ofen)
            count += 1
    if count != len(normalized_expected):
        raise ValueError("projection row count differs from its manifest")
    if _identity(path) != identity:
        raise ValueError("projection changed while its rows were verified")


def verify_projection_manifest(
    path: str | Path,
) -> dict[str, Any]:
    """Strictly verify a published projection and all transitive identities."""

    manifest, manifest_identity = _load_json_snapshot(path)
    _strict_keys(manifest, MANIFEST_FIELDS, "G3 prior-projection manifest")
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("kind") != MANIFEST_KIND
        or manifest.get("profileId") != PROFILE_ID
        or manifest.get("status") != STATUS
        or manifest.get("targetOpaque") is not True
        or manifest.get("finalStageSeal") is not True
    ):
        raise ValueError("G3 prior-projection manifest header is invalid")
    for key, expected in (
        ("schemaVersion", 1),
        ("kind", MANIFEST_KIND),
        ("profileId", PROFILE_ID),
        ("status", STATUS),
        ("targetOpaque", True),
        ("finalStageSeal", True),
    ):
        _require_exact_json(manifest.get(key), expected, f"manifest.{key}")
    created_utc = _validate_created_utc(manifest.get("createdUtc"))
    expected_schema = {
        "recognizedFields": ["kind", "ofen", "positionId", "schemaVersion"],
        "unknownFieldPolicy": "abort",
        "ordering": "normalized-ofen-lexicographic",
        "uniqueness": "normalized-six-field-ofen",
    }
    _require_exact_json(
        manifest.get("projectionSchema"), expected_schema, "manifest.projectionSchema"
    )

    projection_identity = _strict_identity(
        manifest.get("projection"), "G3 OFEN projection"
    )
    source_catalog = _strict_identity(
        manifest.get("sourceCatalog"), "G3 exclusion catalog"
    )
    if source_catalog != _expected_catalog_identity(Path(source_catalog["path"])):
        raise ValueError("projection does not pin the exact frozen G3 catalog")
    closure = _strict_identity(manifest.get("generation3Closure"), "G3 closure")
    if closure != _expected_closure_identity(Path(closure["path"])):
        raise ValueError("projection does not pin the exact published G3 closure")

    (
        frozen_catalog,
        frozen_catalog_snapshot,
        authenticated_inventory,
    ) = _verify_catalog_and_sources(
        Path(source_catalog["path"])
    )
    if frozen_catalog_snapshot != source_catalog:
        raise ValueError("frozen G3 catalog changed while projection was verified")

    inventory = manifest.get("sourceInventory")
    if not isinstance(inventory, list) or len(inventory) != EXPECTED_ARTIFACT_COUNT:
        raise ValueError("projection manifest lacks the 753-source inventory")
    if (
        inventory != frozen_catalog.get("artifacts")
        or inventory != authenticated_inventory
    ):
        raise ValueError("projection source inventory differs from the frozen G3 catalog")
    verified_inventory: list[dict[str, Any]] = []
    for index, identity in enumerate(inventory):
        verified_inventory.append(
            _strict_identity(identity, f"projection sourceInventory[{index}]")
        )
    if verified_inventory != inventory or manifest.get(
        "sourceInventorySha256"
    ) != _canonical_digest(inventory):
        raise ValueError("projection source inventory or digest changed")

    implementations = _strict_keys(
        manifest.get("implementationIdentities"),
        {
            "projectionBuilderSource",
            "generation3ScannerSource",
            "deepLeakageSource",
            "phaseIncidenceSource",
            "selectionSymmetrySource",
            "importedOmegaNnueSource",
            "catalogEraOmegaNnueGitBlob",
            "gitExecutable",
            "pythonRuntime",
        },
        "projection implementation identities",
    )
    expected_implementations = _implementation_identities()
    for key in (
        "projectionBuilderSource",
        "generation3ScannerSource",
        "deepLeakageSource",
        "phaseIncidenceSource",
        "selectionSymmetrySource",
        "importedOmegaNnueSource",
        "gitExecutable",
    ):
        _strict_identity(implementations[key], f"projection implementation {key}")
    frozen_blob = _strict_keys(
        implementations["catalogEraOmegaNnueGitBlob"],
        {
            "path",
            "bytes",
            "sha256",
            "commit",
            "blobSha1",
            "executionPolicy",
        },
        "catalog-era omega_nnue Git blob",
    )
    if frozen_blob != expected_implementations["catalogEraOmegaNnueGitBlob"]:
        raise ValueError("catalog-era omega_nnue Git blob declaration changed")
    python_runtime = _strict_keys(
        implementations["pythonRuntime"],
        {"path", "bytes", "sha256", "version"},
        "projection Python runtime",
    )
    if _strict_identity(
        {key: python_runtime[key] for key in ("path", "bytes", "sha256")},
        "projection Python executable",
    ) != _identity(Path(sys.executable)) or python_runtime["version"] != sys.version:
        raise ValueError("projection Python runtime differs from the manifest")
    if implementations != expected_implementations:
        raise ValueError("projection implementation identities differ from the runtime")
    opening_runtime = _verify_opening_runtime(manifest.get("openingReplayRuntime"))

    boundary = _strict_keys(
        manifest.get("informationBoundary"),
        {
            "decodedValueFields",
            "sourceTargetOrScoreFieldsDecoded",
            "targetOrScoreFieldsEmitted",
            "captureHook",
            "captureHookRestoredInFinally",
        },
        "projection information boundary",
    )
    expected_boundary = {
        "decodedValueFields": [
            "gameid",
            "normalized-six-field-ofen",
            "runid",
            "sourcegameid",
            "sourcerunid",
        ],
        "sourceTargetOrScoreFieldsDecoded": 0,
        "targetOrScoreFieldsEmitted": 0,
        "captureHook": "deep._leakage_keys input only",
        "captureHookRestoredInFinally": True,
    }
    if boundary != expected_boundary:
        raise ValueError("projection information boundary changed")

    (
        signatures,
        recomputed_pins,
        position_count,
        key_counts,
        stable_count,
        replay,
        captured,
        calls,
    ) = _capture_inventory([Path(item["path"]) for item in verified_inventory])
    _verify_recomputation(
        frozen_catalog,
        recomputed_pins,
        signatures,
        position_count,
        key_counts,
        stable_count,
        replay,
        captured,
        calls,
    )
    expected_ofens = sorted(captured)
    if opening_runtime != _verify_opening_runtime(replay["runtimeAfterReplay"]):
        raise ValueError("projection opening runtime differs from recomputed replay")
    historical_identifiers = _verify_historical_identifiers(
        manifest.get("historicalIdentifiers")
    )
    recomputed_identifiers = _historical_identifier_inventory(verified_inventory)
    if historical_identifiers != recomputed_identifiers:
        raise ValueError("historical identifier inventory differs from exact sources")
    evidence = manifest.get("evidence")
    expected_evidence_keys = {
        "artifactCount",
        "artifactBytes",
        "stableArtifactCount",
        "rawDecodedPositionOccurrenceCount",
        "replayPrefixRecordCount",
        "leakageKeyCallCount",
        "forbiddenPositionOccurrenceCount",
        "forbiddenInputSignatureCount",
        "projectedUniqueOfenCount",
        "positionLikeKeyCountsSha256",
        "replayAuditSha256",
        "finalTargetFreeInventorySha256",
    }
    _strict_keys(evidence, expected_evidence_keys, "projection aggregate evidence")
    catalog = frozen_catalog
    expected_evidence = _projection_evidence(
        catalog,
        replay,
        calls=calls,
        projected_count=len(expected_ofens),
    )
    if (
        evidence != expected_evidence
        or type(manifest.get("projectedPositionCount")) is not int
        or manifest["projectedPositionCount"] != len(expected_ofens)
    ):
        raise ValueError("projection aggregate evidence differs from the frozen catalog")
    expected_manifest = {
        "schemaVersion": 1,
        "kind": MANIFEST_KIND,
        "profileId": PROFILE_ID,
        "status": STATUS,
        "createdUtc": created_utc,
        "targetOpaque": True,
        "projection": projection_identity,
        "projectionSchema": expected_schema,
        "projectedPositionCount": len(expected_ofens),
        "sourceCatalog": source_catalog,
        "generation3Closure": closure,
        "sourceInventory": authenticated_inventory,
        "sourceInventorySha256": _canonical_digest(authenticated_inventory),
        "implementationIdentities": expected_implementations,
        "openingReplayRuntime": opening_runtime,
        "evidence": expected_evidence,
        "informationBoundary": expected_boundary,
        "historicalIdentifiers": recomputed_identifiers,
        "finalStageSeal": True,
    }
    _require_exact_json(manifest, expected_manifest, "G3 prior-projection manifest")
    _verify_projection_rows(
        Path(projection_identity["path"]),
        projection_identity,
        expected_ofens,
    )
    if _identity(path) != manifest_identity:
        raise ValueError("projection manifest changed during verification")
    return {
        "manifest": manifest_identity,
        "projection": projection_identity,
        "sourceArtifactCount": len(inventory),
        "sourceArtifactBytes": sum(int(item["bytes"]) for item in inventory),
        "projectedPositionCount": int(manifest["projectedPositionCount"]),
        "projectionBuilderSource": implementations["projectionBuilderSource"],
        "sourceSha256": sorted(item["sha256"] for item in inventory),
        "historicalGameIds": list(historical_identifiers["gameIds"]),
        "historicalRunIds": list(historical_identifiers["runIds"]),
    }


def _self_test() -> None:
    valid = generation3.OMEGA_INITIAL_OFEN
    poison = "TARGET-VALUE-MUST-NOT-BE-DECODED"
    original_leakage = generation3.deep._leakage_keys
    original_decoder = generation3._decode_json_string
    original_bindings = (
        generation3.parse_ofen,
        deep.parse_ofen,
        screen.parse_ofen,
        screen._active_features_from_parsed,
    )
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "target-opaque.json"
        source.write_text(
            json.dumps(
                {
                    "InitialOfen": valid,
                    "Moves": ["e1e3", "e8e6"],
                    "teacherTarget": {
                        "scoreCp": poison,
                        "outcome": poison,
                        "nested": {"runId": "historical-run-inside-target"},
                    },
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        identifiers = Path(directory) / "identifiers.jsonl"
        identifiers.write_text(
            json.dumps(
                {
                    "gameId": "historical-game",
                    "metadata": {"sourceRunId": "historical-source-run"},
                    "target": poison,
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        # If leaf inputs were widened to their parent directory, this sibling
        # would abort the scan.  It is intentionally not passed below.
        (Path(directory) / "unlisted-invalid.json").write_text(
            '{"initialOfen":"not-an-ofen"}', encoding="utf-8"
        )
        decoded: list[str] = []

        def recording_decoder(
            text: str, start: int, end: int, *, location: str
        ) -> str:
            value = original_decoder(text, start, end, location=location)
            decoded.append(value)
            return value

        generation3._decode_json_string = recording_decoder
        try:
            (
                _signatures,
                pins,
                positions,
                _key_counts,
                stable,
                replay,
                captured,
                calls,
            ) = _capture_inventory([source, identifiers])
        finally:
            generation3._decode_json_string = original_decoder
        if (
            len(pins) != 2
            or stable != 2
            or positions != 3
            or replay["requestSchemaCounts"] != {"schedule-moves": 1}
            or replay["prefixRecordCount"] != 3
            or calls != 5
            or len(captured) != 3
            or poison in decoded
            or generation3._decode_json_string is not original_decoder
            or generation3.deep._leakage_keys is not original_leakage
            or deep._leakage_keys is not original_leakage
            or original_bindings
            != (
                generation3.parse_ofen,
                deep.parse_ofen,
                screen.parse_ofen,
                screen._active_features_from_parsed,
            )
        ):
            raise AssertionError("synthetic direct/replay projection self-test drifted")
        original_strict_loads = globals()["_strict_json_loads"]
        locally_decoded: list[Any] = []

        def recording_strict_loads(text: str, description: str) -> Any:
            value = original_strict_loads(text, description)
            locally_decoded.append(value)
            return value

        globals()["_strict_json_loads"] = recording_strict_loads
        try:
            historical = _historical_identifier_inventory(
                [_identity(source), _identity(identifiers)]
            )
        finally:
            globals()["_strict_json_loads"] = original_strict_loads
        if (
            historical["gameIds"] != ["historical-game"]
            or historical["runIds"]
            != ["historical-run-inside-target", "historical-source-run"]
            or _verify_historical_identifiers(historical) != historical
            or "historical-game" not in locally_decoded
            or "historical-source-run" not in locally_decoded
            or poison in locally_decoded
            or globals()["_strict_json_loads"] is not original_strict_loads
        ):
            raise AssertionError("historical identifier lexical projection drifted")
        payload = _projection_payload(sorted(captured))
        rows = [
            _strict_json_loads(line, "synthetic projection row")
            for line in payload.decode("utf-8").splitlines()
        ]
        if len(rows) != 3 or any(set(row) != {
            "schemaVersion", "kind", "positionId", "ofen"
        } for row in rows):
            raise AssertionError("target-free projection row schema drifted")
        projection = Path(directory) / "synthetic-projection.jsonl"
        projection.write_bytes(payload)
        expected_ofens = sorted(captured)
        _verify_projection_rows(projection, _identity(projection), expected_ofens)
        typed_row = dict(rows[0])
        typed_row["schemaVersion"] = 1.0
        typed_projection = Path(directory) / "typed-projection.jsonl"
        typed_projection.write_text(
            json.dumps(typed_row, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        try:
            _verify_projection_rows(
                typed_projection, _identity(typed_projection), [typed_row["ofen"]]
            )
        except ValueError:
            pass
        else:
            raise AssertionError("projection accepted a float schema version")
        for invalid in (
            '{"Key":1,"key":2}',
            '{"value":NaN}',
            '{"value":1e9999}',
        ):
            try:
                _strict_json_loads(invalid, "synthetic invalid strict JSON")
            except ValueError:
                pass
            else:
                raise AssertionError("strict JSON parser accepted ambiguous input")
        for value, expected in ((True, 1), (1.0, 1), ("1", 1)):
            try:
                _require_exact_json(value, expected, "synthetic exact-type check")
            except ValueError:
                pass
            else:
                raise AssertionError("recursive exact-type check accepted an alias")
        collision = Path(directory) / "collision.jsonl"
        collision.write_text(
            json.dumps({"runId": RESERVED_SOURCE_RUN_ID}) + "\n",
            encoding="utf-8",
        )
        try:
            _historical_identifier_inventory([_identity(collision)])
        except ValueError as error:
            if "collide" not in str(error):
                raise
        else:
            raise AssertionError("reserved run-ID collision was accepted")
        identifier_toc = Path(directory) / "identifier-toc.json"
        identifier_toc.write_text(
            json.dumps({"gameId": "historical-toc"}), encoding="utf-8"
        )
        original_collector = globals()["_collect_ids_from_value"]
        mutated = False

        def mutating_collector(*collector_args: Any, **collector_kwargs: Any) -> None:
            nonlocal mutated
            if not mutated:
                mutated = True
                identifier_toc.write_text(
                    identifier_toc.read_text(encoding="utf-8") + " ",
                    encoding="utf-8",
                )
            original_collector(*collector_args, **collector_kwargs)

        globals()["_collect_ids_from_value"] = mutating_collector
        try:
            try:
                _historical_identifier_inventory([_identity(identifier_toc)])
            except ValueError as error:
                if "changed" not in str(error):
                    raise
            else:
                raise AssertionError("historical identifier TOCTOU was accepted")
        finally:
            globals()["_collect_ids_from_value"] = original_collector

        original_inventory = generation3._lexical_inventory

        def failing_inventory(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("synthetic inventory failure")

        generation3._lexical_inventory = failing_inventory
        try:
            try:
                _capture_inventory([source])
            except RuntimeError as error:
                if "synthetic inventory failure" not in str(error):
                    raise
            else:
                raise AssertionError("synthetic inventory failure did not propagate")
        finally:
            generation3._lexical_inventory = original_inventory
        if generation3.deep._leakage_keys is not original_leakage:
            raise AssertionError("capture hook was not restored after failure")

        module_name = "_omega_nnue_g3_frozen_blob"
        sentinel = types.ModuleType(module_name)
        previous_module = sys.modules.pop(module_name, None)
        sys.modules[module_name] = sentinel
        try:
            _frozen_omega_module()
            if sys.modules.get(module_name) is not sentinel:
                raise AssertionError("frozen module did not restore reserved slot")
        finally:
            sys.modules.pop(module_name, None)
            if previous_module is not None:
                sys.modules[module_name] = previous_module

        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        if _validate_created_utc(now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")) == "":
            raise AssertionError("current createdUtc did not validate")
        for invalid_time in (
            (CREATED_UTC_FLOOR - timedelta(seconds=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            (now_utc + timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "2026-07-22T25:00:00Z",
        ):
            try:
                _validate_created_utc(invalid_time)
            except ValueError:
                pass
            else:
                raise AssertionError("createdUtc authorization accepted invalid time")
    print("king_state_generation4_prior_projection self-test passed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    create.add_argument("--closure", default=str(DEFAULT_CLOSURE))
    create.add_argument("--output", default=str(DEFAULT_OUTPUT))
    create.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    create.add_argument("--created-utc", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    subparsers.add_parser("self-test")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        _create(args)
    elif args.command == "verify":
        result = verify_projection_manifest(args.manifest)
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "self-test":
        _self_test()
    else:  # pragma: no cover
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
