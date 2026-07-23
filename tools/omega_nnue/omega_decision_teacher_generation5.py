#!/usr/bin/env python3
"""Resumable, fail-closed Generation 5 decision-teacher pipeline for Omega Chess.

This module intentionally stops before training.  It can:

* build a target-opaque forbidden-position catalog from explicit prior files;
* select 896 raw roots per phase/side from completed HCE-only OmegaMatch
  trajectories without reading their scores;
* invoke the separate ``OmegaDecisionSampler`` to enumerate every legal child;
* authenticate the complete raw leakage graph, then retain exactly 768 roots
  per phase/side with at least five distinct children and exactly one source PV;
* shallow-search every sibling, then select exactly four children per root;
* deep-search those children with append-only, fsynced, resumable ledgers; and
* finalize only complete leakage groups into component-stable splits.

All expensive policies and destinations are CLI inputs so a later experiment
can freeze them in a preregistration.  Outputs and manifests are no-clobber.
No command in this file trains or loads an NNUE.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Iterable, Iterator, Sequence

import king_state_generation5_runtime as runtime_contract
import king_state_generation4_abort as generation4_abort
import king_state_generation4_prior_projection as prior_projection
import deep_hce_v2 as deep
import omega_nnue
from omega_nnue import parse_ofen
import select_screen
from select_screen import phase_of
import validate_king_state_v5_preregistration as v5_prereg


SCHEMA_VERSION = 1
PHASES = ("opening", "middlegame", "late", "endgame")
SPLITS = ("train", "validation", "heldOut")
PROFILE_ID = "king-state-v5-omega-decision-v2"
DATA_PROFILE_ID = "omega-decision-v2"
SOURCE_SEED = 2026072301
ROOT_SELECTION_SEED = 2026072302
SIBLING_EXPLORATION_SEED = 2026072303
COMPONENT_SPLIT_SEED = 2026072304
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
TARGET_LIKE_KEY = re.compile(
    r"(?:^|[_-])(score|target|label|evaluation|eval|outcome|result|winner|mate)(?:$|[_-])",
    re.IGNORECASE,
)
TARGET_LIKE_STEMS = frozenset(
    {
        "score",
        "target",
        "label",
        "evaluation",
        "eval",
        "outcome",
        "result",
        "winner",
        "mate",
        "value",
        "bound",
        "win",
        "loss",
        "probability",
        "bestmove",
    }
)
TARGET_OPAQUE_DECLARATION_KEYS = frozenset(
    {
        "targetOpaque",
        "targetInformationRead",
        "targetOrScoreFieldsDecoded",
        "targetOrScoreFieldsEmitted",
    }
)


def _key_tokens(value: Any) -> tuple[str, ...]:
    """Split snake/kebab/camel/acronym keys without trusting punctuation."""

    text = str(value)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return tuple(token for token in re.split(r"[^A-Za-z0-9]+", text.lower()) if token)


def _is_target_like_key(value: Any) -> bool:
    tokens = _key_tokens(value)
    token_forms: list[set[str]] = []
    for token in tokens:
        forms = {token}
        if token.endswith("ies"):
            forms.add(token[:-3] + "y")
        if token.endswith("es"):
            forms.add(token[:-2])
        if token.endswith("s"):
            forms.add(token[:-1])
        token_forms.append(forms)
    if any(
        "best" in token_forms[index] and "move" in token_forms[index + 1]
        for index in range(max(0, len(token_forms) - 1))
    ):
        return True
    for forms in token_forms:
        if forms & TARGET_LIKE_STEMS:
            return True
    return False
FORBIDDEN_CATALOG_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "positionId",
        "ofen",
        "exactPositionKey",
        "conservativeOrbitKey",
        "conservativeOrbitSignatures",
        "sourceGameId",
        "sourceRunId",
        "sourceArtifactSha256",
    }
)
FORBIDDEN_OFEN_FIELD_NAMES = frozenset(
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
    }
)
FORBIDDEN_GAME_ID_FIELD_NAMES = frozenset({"gameid", "sourcegameid"})
FORBIDDEN_RUN_ID_FIELD_NAMES = frozenset({"runid", "sourcerunid"})
FORBIDDEN_SOURCE_SUFFIXES = (".json", ".jsonl", ".ndjson")
FORBIDDEN_G5_PATH_MARKERS = (
    "/build-msvc/data-generation/omega-decision-v2/",
    "/build-msvc/king-state-v5/",
    "/build-msvc/king-state-v5-development/",
    "/build-king-state-v5/",
    "/tools/omega_nnue/frozen_runtime/king-state-v5/",
    "/validation/omega-nnue-king-state-v5-",
)
DEFAULT_HCE_OPTIONS = {
    "Threads": "1",
    "Hash": "128",
    "Ponder": "false",
    "OwnBook": "false",
    "UCI_Chess960": "false",
    "UCI_Variant": "omega",
    "OmegaNNUEFile": "<empty>",
    "UseOmegaNNUE": "false",
}

FINAL_FREEZE_KIND = "omega-nnue-king-state-v5-final-freeze-seal"
FINAL_FREEZE_STATUS = "target-blind-generation-5-design-frozen-before-teacher-labels"
PRELABEL_STATUS = "target-opaque-frozen-before-teacher-search"
PRIOR_REUSE_POLICY = (
    "abort exact, conservative orbit, source game/run, or symmetric source-data "
    "input reuse across the complete raw graph before retention; authenticate "
    "but exclude reusable source infrastructure"
)
SEARCH_WORKERS = 4
SOURCE_NODES = 2000
SOURCE_MAX_PLIES = 1
SOURCE_OPENINGS = 7168
SOURCE_OPENINGS_PER_PHASE_SIDE = 896
SOURCE_TRAJECTORY_PAIRS = 10240
RAW_ROOTS_PER_PHASE_SIDE = 896
FEASIBLE_ROOTS_PER_PHASE_SIDE = 768
DEEP_ROOTS_PER_PHASE_SIDE = 640
FINAL_ROOTS_PER_PHASE_SIDE = 512
REPO = Path(__file__).resolve().parents[2]
SOURCE_OPENING_BUILDER = REPO / "tools/omega_nnue/king_state_generation5_source.py"
SOURCE_PAIR_TAG = re.compile(r"^rules-only-pair:(random-pair-[0-9]{6})$")
SOURCE_DATA_INPUT_IDENTITY_FIELDS = (
    "source",
    "openingSuite",
    "sourceMatchConfig",
    "sourceRootPool",
    "sourceRootPoolManifest",
    "sourceRootPoolSeal",
)
REUSABLE_SOURCE_INFRASTRUCTURE_IDENTITY_FIELDS = (
    "sourceMatchHarnessAssembly",
    "rootSamplerAssembly",
    "rootSamplerChessLibAssembly",
    "sourceOpeningBuilderSource",
)
SOURCE_AUDIT_LAUNCH_PROVENANCE_IDENTITY_FIELDS = ("sourceMatchCompletionSeal",)
GENERATION4_SOURCE_AUDIT_IDENTITY_FIELDS = (
    *SOURCE_DATA_INPUT_IDENTITY_FIELDS,
    *REUSABLE_SOURCE_INFRASTRUCTURE_IDENTITY_FIELDS,
)
SOURCE_AUDIT_INPUT_IDENTITY_FIELDS = (
    *GENERATION4_SOURCE_AUDIT_IDENTITY_FIELDS,
    *SOURCE_AUDIT_LAUNCH_PROVENANCE_IDENTITY_FIELDS,
)
GENERATION4_CLOSURE_PATH = (
    REPO / "validation/omega-nnue-king-state-v4-structural-abort.seal.json"
)
GENERATION4_DATA_CLOSURE_NAMES = {
    "source": "sourceEvents",
    "openingSuite": "sourceOpeningSuite",
    "sourceMatchConfig": "sourceMatchConfig",
    "sourceRootPool": "sourceRootPool",
    "sourceRootPoolManifest": "sourceRootPoolManifest",
    "sourceRootPoolSeal": "sourceRootPoolSeal",
}
GENERATION4_POSITION_CLOSURE_NAMES = (
    "sourceRootPool",
    "sourceOpeningSuite",
    "sourceEvents",
    "roots",
    "children",
)


def _strict_keys(value: Any, expected: set[str], description: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ValueError(
            f"{description} has the wrong field inventory: expected "
            f"{sorted(expected)}, found {actual}"
        )
    return value


def _validate_source_trajectory_coverage(value: Any) -> dict[str, Any]:
    coverage = _strict_keys(
        value,
        {
            "trajectoryPairs",
            "independentTrajectoriesPerPair",
            "samplerExecutedTrajectories",
            "rowBearingTrajectories",
            "zeroRecordTrajectories",
            "zeroRecordTrajectoryIds",
            "authenticatedTerminalTrajectories",
            "zeroRecordBound",
        },
        "source trajectory coverage",
    )
    zero_ids = coverage["zeroRecordTrajectoryIds"]
    if (
        coverage["trajectoryPairs"] != SOURCE_TRAJECTORY_PAIRS
        or coverage["independentTrajectoriesPerPair"] != 2
        or coverage["samplerExecutedTrajectories"] != SOURCE_TRAJECTORY_PAIRS * 2
        or type(coverage["rowBearingTrajectories"]) is not int
        or type(coverage["zeroRecordTrajectories"]) is not int
        or type(coverage["authenticatedTerminalTrajectories"]) is not int
        or not isinstance(zero_ids, list)
        or any(
            re.fullmatch(r"random-pair-[0-9]{6}-(?:ab|ba)", str(item)) is None
            for item in zero_ids
        )
        or zero_ids != sorted(set(zero_ids))
        or coverage["zeroRecordTrajectories"] != len(zero_ids)
        or coverage["rowBearingTrajectories"]
        + coverage["zeroRecordTrajectories"]
        != coverage["samplerExecutedTrajectories"]
        or not 0
        <= coverage["zeroRecordTrajectories"]
        <= coverage["authenticatedTerminalTrajectories"]
        or coverage["zeroRecordBound"]
        != (
            "zero-record trajectory count must not exceed authenticated "
            "terminalTrajectories"
        )
    ):
        raise ValueError("source trajectory coverage is inconsistent")
    return dict(coverage)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: str | Path) -> dict[str, Any]:
    path = _resolve(path)
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "sha256": _sha256(path)}


def _verify_identity(identity: Any, description: str) -> dict[str, Any]:
    if not isinstance(identity, dict) or set(identity) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{description} is not a strict path/bytes/SHA-256 identity")
    expected = {
        "path": str(_resolve(str(identity["path"]))),
        "bytes": int(identity["bytes"]),
        "sha256": str(identity["sha256"]).lower(),
    }
    if not HEX_SHA256.fullmatch(expected["sha256"]):
        raise ValueError(f"{description} has an invalid SHA-256")
    actual = _identity(expected["path"])
    if actual != expected:
        raise ValueError(f"{description} identity changed: expected {expected}, found {actual}")
    return actual


def _producer_identity() -> dict[str, Any]:
    """Pin every Python implementation that affects a published artifact."""

    return {
        "omegaDecisionTeacher": _identity(Path(__file__)),
        "deepHceV2": _identity(Path(deep.__file__)),
        "omegaNnue": _identity(Path(omega_nnue.__file__)),
        "selectScreen": _identity(Path(select_screen.__file__)),
        "generation5PreregistrationValidator": _identity(Path(v5_prereg.__file__)),
        "generation4AbortVerifier": _identity(Path(generation4_abort.__file__)),
        "priorProjection": _identity(Path(prior_projection.__file__)),
        "python": {
            **_identity(Path(sys.executable)),
            "version": sys.version,
        },
    }


def _verify_producer_identity(value: Any, description: str) -> dict[str, Any]:
    """Verify the complete, deliberately closed producer schema.

    Producer records are executable provenance, not an extensible metadata bag.
    In particular, accepting an arbitrary nested object here would let a prior
    catalog smuggle score or outcome fields across the target-opaque boundary.
    """

    producer = _strict_keys(
        value,
        {
            "omegaDecisionTeacher",
            "deepHceV2",
            "omegaNnue",
            "selectScreen",
            "generation5PreregistrationValidator",
            "generation4AbortVerifier",
            "priorProjection",
            "python",
        },
        description,
    )
    for name in (
        "omegaDecisionTeacher",
        "deepHceV2",
        "omegaNnue",
        "selectScreen",
        "generation5PreregistrationValidator",
        "generation4AbortVerifier",
        "priorProjection",
    ):
        _verify_identity(producer[name], f"{description}.{name}")
    python = _strict_keys(
        producer["python"], {"path", "bytes", "sha256", "version"},
        f"{description}.python",
    )
    _verify_identity(
        {key: python[key] for key in ("path", "bytes", "sha256")},
        f"{description}.python",
    )
    if not isinstance(python["version"], str) or not python["version"]:
        raise ValueError(f"{description}.python.version is invalid")
    return producer


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _verify_final_freeze(
    preregistration_path: Path, final_freeze_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate and bind the final preregistration and its adjacent seal.

    The seal deliberately repeats the final identity inventory.  That makes a
    stage invocation prove both directions of the binding: the seal pins the
    exact preregistration bytes and the preregistration pins every executable
    and data artifact.  Merely trusting a path supplied on the command line is
    not sufficient.
    """

    preregistration_path = _resolve(preregistration_path)
    final_freeze_path = _resolve(final_freeze_path)
    profile, profile_identity = _load_json_snapshot(preregistration_path)
    v5_prereg.validate_profile(profile, mode="frozen", verify_external=True)
    if profile.get("profileId") != PROFILE_ID:
        raise ValueError("final preregistration has the wrong profile ID")
    namespaces = _strict_keys(
        profile.get("namespaces"),
        set(v5_prereg.EXPECTED_NAMESPACES),
        "final preregistration namespaces",
    )
    expected_seal_path = _resolve(namespaces["finalFreezeSeal"])
    if final_freeze_path != expected_seal_path:
        raise ValueError("--final-freeze-seal differs from the preregistered path")

    seal, seal_identity = _load_json_snapshot(final_freeze_path)
    _strict_keys(
        seal,
        {
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
        },
        "final-freeze seal",
    )
    if (
        seal["schemaVersion"] != 1
        or seal["kind"] != FINAL_FREEZE_KIND
        or seal["profileId"] != PROFILE_ID
        or seal["status"] != FINAL_FREEZE_STATUS
        or seal["finalStageSeal"] is not True
    ):
        raise ValueError("wrong or unsupported final-freeze seal")
    if seal["preregistration"] != profile_identity:
        raise ValueError("final-freeze seal does not pin this preregistration")
    validator_identity = _identity(Path(v5_prereg.__file__))
    if seal["validator"] != validator_identity:
        raise ValueError("final-freeze seal does not pin the executing validator")
    final_identities = profile.get("finalFreezeIdentities")
    if seal["finalFreezeIdentities"] != final_identities:
        raise ValueError("final-freeze identity inventory differs from preregistration")
    if seal["finalFreezeIdentitiesSha256"] != _canonical_digest(final_identities):
        raise ValueError("final-freeze identity inventory digest is invalid")
    declaration = _strict_keys(
        seal["declaration"],
        {
            "teacherSearchesPresentAtFreeze",
            "generation5TeacherTargetsDecoded",
            "generation5ValidationTargetsDecoded",
            "generation5HeldOutTargetsDecoded",
        },
        "final-freeze declaration",
    )
    if declaration != {
        "teacherSearchesPresentAtFreeze": False,
        "generation5TeacherTargetsDecoded": 0,
        "generation5ValidationTargetsDecoded": 0,
        "generation5HeldOutTargetsDecoded": 0,
    }:
        raise ValueError("final-freeze information-boundary declaration is invalid")
    if not isinstance(seal["createdUtc"], str):
        raise ValueError("final-freeze createdUtc is invalid")

    validator_from_profile = _verify_identity(
        final_identities.get("preregistrationValidatorSource"),
        "final preregistration validator",
    )
    if validator_from_profile != validator_identity:
        raise ValueError("preregistration and seal pin different validators")
    verified = 0
    for label, identity in _walk_identity_objects(
        final_identities, "finalFreezeIdentities"
    ):
        _verify_identity(identity, label)
        verified += 1
    if verified == 0:
        raise ValueError("final preregistration pins no identities")
    producer = _producer_identity()
    for producer_name, frozen_name in (
        ("omegaDecisionTeacher", "decisionTeacherSource"),
        ("deepHceV2", "deepHceV2Source"),
        ("omegaNnue", "networkFormatPythonSource"),
        ("selectScreen", "selectScreenSource"),
        (
            "generation5PreregistrationValidator",
            "preregistrationValidatorSource",
        ),
        ("generation4AbortVerifier", "generation4AbortVerifierSource"),
        ("priorProjection", "priorProjectionSource"),
    ):
        if producer[producer_name] != _verify_identity(
            final_identities.get(frozen_name), f"final producer identity {frozen_name}"
        ):
            raise ValueError(
                f"executing producer {producer_name} differs from final identity {frozen_name}"
            )
    runtime_contract.verify_manifest(runtime_contract.DEFAULT_OUTPUT)
    if _identity(Path(runtime_contract.__file__)) != _verify_identity(
        final_identities.get("pythonRuntimeToolSource"),
        "final producer identity pythonRuntimeToolSource",
    ):
        raise ValueError("executing runtime verifier differs from final freeze")
    runtime_manifest_identity = _verify_identity(
        final_identities.get("pythonRuntimeManifest"),
        "final producer identity pythonRuntimeManifest",
    )
    if runtime_manifest_identity != _identity(runtime_contract.DEFAULT_OUTPUT):
        raise ValueError("executing Python/NumPy runtime manifest differs from final freeze")
    frozen_runtime = runtime_contract.verify_manifest(runtime_contract.DEFAULT_OUTPUT)[
        "runtime"
    ]
    if producer["python"] != {
        **frozen_runtime["python"]["executable"],
        "version": frozen_runtime["python"]["versionDetail"],
    }:
        raise ValueError("executing Python differs from the frozen runtime manifest")
    return profile, profile_identity, seal_identity


def _frozen_identity(profile: dict[str, Any], name: str) -> dict[str, Any]:
    inventory = profile.get("finalFreezeIdentities")
    if not isinstance(inventory, dict) or name not in inventory:
        raise ValueError(f"final preregistration lacks identity {name}")
    return _verify_identity(inventory[name], f"final identity {name}")


def _frozen_identity_list(profile: dict[str, Any], name: str) -> list[dict[str, Any]]:
    inventory = profile.get("finalFreezeIdentities")
    value = inventory.get(name) if isinstance(inventory, dict) else None
    if not isinstance(value, list) or not value:
        raise ValueError(f"final preregistration lacks identity list {name}")
    return [
        _verify_identity(item, f"final identity {name}[{index}]")
        for index, item in enumerate(value)
    ]


def _publish_temporary_no_clobber(temporary: str | Path, target: str | Path) -> None:
    """Atomically publish a same-directory temporary without replacement.

    ``os.replace`` has a check/use race and can overwrite another publisher.
    A same-volume hard-link is atomic and fails if the target already exists.
    """

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


def _atomic_bytes(path: str | Path, payload: bytes, *, no_clobber: bool = True) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if no_clobber and path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if no_clobber:
            _publish_temporary_no_clobber(temporary, path)
        else:
            os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_json(path: str | Path, value: Any, *, no_clobber: bool = True) -> None:
    _atomic_bytes(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        no_clobber=no_clobber,
    )


def _atomic_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            for row in rows:
                stream.write(
                    (
                        json.dumps(row, sort_keys=True, separators=(",", ":"))
                        + "\n"
                    ).encode("utf-8")
                )
            stream.flush()
            os.fsync(stream.fileno())
        _publish_temporary_no_clobber(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _jsonl(path: str | Path) -> Iterator[tuple[int, dict[str, Any]]]:
    path = _resolve(path)
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            yield line_number, value


def _snapshot_jsonl(path: str | Path) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, Any]]:
    path = _resolve(path)
    before = _identity(path)
    records = list(_jsonl(path))
    after = _identity(path)
    if before != after:
        raise ValueError(f"input changed while being read: {path}")
    return records, before


def _scan_json_string(text: str, start: int) -> int:
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


def _scan_json_value(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        raise ValueError("missing JSON value")
    if text[index] == '"':
        return _scan_json_string(text, index)
    if text[index] in "[{":
        stack = ["]" if text[index] == "[" else "}"]
        index += 1
        while index < len(text) and stack:
            character = text[index]
            if character == '"':
                index = _scan_json_string(text, index)
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
    end = index
    while end < len(text) and text[end] not in ",]}" and not text[end].isspace():
        end += 1
    if end == index:
        raise ValueError("empty JSON primitive")
    return end


def _top_level_json_spans(text: str) -> dict[str, tuple[str, str]]:
    """Return top-level field raw slices without decoding their values."""

    index = 0
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] != "{":
        raise ValueError("source telemetry record must be an object")
    index += 1
    fields: dict[str, tuple[str, str]] = {}
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == "}":
            index += 1
            break
        key_end = _scan_json_string(text, index)
        key = json.loads(text[index:key_end])
        if not isinstance(key, str):
            raise ValueError("JSON object key is not a string")
        lowered = key.lower()
        if lowered in fields:
            raise ValueError("case-colliding/duplicate JSON field")
        index = key_end
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index] != ":":
            raise ValueError("missing colon after JSON field")
        index += 1
        while index < len(text) and text[index].isspace():
            index += 1
        value_start = index
        index = _scan_json_value(text, index)
        fields[lowered] = (key, text[value_start:index])
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            index += 1
            break
        raise ValueError("missing comma/end after JSON field")
    if text[index:].strip():
        raise ValueError("trailing text after source telemetry object")
    return fields


def _decode_allowlisted_fields(
    text: str, names: Sequence[str], description: str
) -> dict[str, Any]:
    spans = _top_level_json_spans(text)
    result: dict[str, Any] = {}
    for name in names:
        entry = spans.get(name.lower())
        if entry is None:
            continue
        try:
            result[name] = json.loads(entry[1])
        except json.JSONDecodeError as error:
            raise ValueError(f"{description}: invalid allowlisted field {name}") from error
    return result


def _decode_structural_match(raw: str, description: str) -> dict[str, Any]:
    return _decode_allowlisted_fields(
        raw,
        ("Mode", "Nodes", "Repeats", "MaxPlies", "AbsoluteMaxPlies"),
        description,
    )


def _json_array_value_slices(text: str) -> list[str]:
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
        index = _scan_json_value(text, index)
        result.append(text[start:index])
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "]":
            index += 1
            break
        raise ValueError("missing comma/end in JSON array")
    if text[index:].strip():
        raise ValueError("trailing text after JSON array")
    return result


def _decode_structural_engines(raw: str, description: str) -> list[dict[str, Any]]:
    return [
        _decode_allowlisted_fields(
            item,
            (
                "Id", "Sha256", "FileSize", "Options",
                "OmegaNnueActiveVerified", "ExternalAssets",
            ),
            f"{description}[{index}]",
        )
        for index, item in enumerate(_json_array_value_slices(raw))
    ]


def _snapshot_source_events(
    path: Path,
) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, Any]]:
    """Lexically skip all nonallowlisted target-bearing telemetry values."""

    path = _resolve(path)
    before = _identity(path)
    records: list[tuple[int, dict[str, Any]]] = []
    field_sets = {
        "run": (
            "RecordType", "RunId", "SourceSeed", "Seed", "ProfileId",
            "FreshnessMarker", "ConfigSha256", "OpeningSuiteSha256",
            "HarnessSha256", "HarnessBundleSha256",
        ),
        "gameStart": (
            "RecordType", "GameId", "PairId", "Attempt", "OpeningId",
            "WhiteEngineId", "BlackEngineId", "InitialOfen", "OpeningMoves",
        ),
        "ply": (
            "RecordType", "GameId", "Attempt", "Ply", "EngineId", "Color",
            "PreOfen", "PostOfen", "BestMove", "Error",
        ),
        "gameResult": (
            "RecordType", "GameId", "PairId", "Attempt", "OpeningId",
            "WhiteEngineId", "BlackEngineId", "Plies", "FinalOfen",
            "IllegalMoves", "IllegalPvs", "ProtocolFailures", "TimeForfeits",
        ),
    }
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            spans = _top_level_json_spans(line)
            record_type_raw = spans.get("recordtype")
            if record_type_raw is None:
                raise ValueError(f"{path}:{line_number}: missing RecordType")
            kind = json.loads(record_type_raw[1])
            if kind not in field_sets:
                raise ValueError(f"{path}:{line_number}: unsupported RecordType {kind!r}")
            selected = _decode_allowlisted_fields(
                line, field_sets[kind], f"{path}:{line_number}:{kind}"
            )
            if kind == "run":
                match_entry = spans.get("match")
                if match_entry is not None:
                    selected["Match"] = _decode_structural_match(
                        match_entry[1], f"{path}:{line_number}:Match"
                    )
                engines_entry = spans.get("engines")
                if engines_entry is not None:
                    selected["Engines"] = _decode_structural_engines(
                        engines_entry[1], f"{path}:{line_number}:Engines"
                    )
            elif kind == "ply":
                search_entry = spans.get("search")
                selected["SearchCommand"] = None
                if search_entry is not None:
                    structural_search = _decode_allowlisted_fields(
                        search_entry[1], ("Command",),
                        f"{path}:{line_number}:Search",
                    )
                    selected["SearchCommand"] = structural_search.get("Command")
                selected.pop("Search", None)
            records.append((line_number, selected))
    after = _identity(path)
    if before != after:
        raise ValueError(f"source telemetry changed while being read: {path}")
    return records, before


def _normalized_ofen(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("OFEN is not a string")
    fields = value.split()
    if len(fields) != 6 or fields[1].lower() not in ("w", "b"):
        raise ValueError("expected six-field OFEN with side w or b")
    if "[" not in fields[0] or "]" not in fields[0]:
        raise ValueError("Omega OFEN lacks corner-square fields")
    return " ".join(fields)


def _canonical_source_ofen(value: Any) -> str:
    """Return an exact canonical source OFEN, rejecting silent normalization."""

    normalized = _normalized_ofen(value)
    if value != normalized or normalized.split()[1] not in ("w", "b"):
        raise ValueError("source opening OFEN is not exact canonical text")
    parse_ofen(normalized)
    return normalized


def _validate_cross_pair_duplicate_exclusion(value: Any) -> dict[str, Any]:
    exclusion = _strict_keys(
        value,
        {
            "policy",
            "distinctOfens",
            "excludedRows",
            "affectedTrajectoryPairs",
            "ofenSetSha256",
        },
        "source cross-pair duplicate exclusion",
    )
    if exclusion["policy"] != "exclude-all-copies-before-pair-assignment":
        raise ValueError("source cross-pair duplicate exclusion policy changed")
    for name in ("distinctOfens", "excludedRows", "affectedTrajectoryPairs"):
        if type(exclusion[name]) is not int:
            raise ValueError("source cross-pair duplicate exclusion counts are malformed")
    distinct = exclusion["distinctOfens"]
    excluded = exclusion["excludedRows"]
    affected = exclusion["affectedTrajectoryPairs"]
    digest = exclusion["ofenSetSha256"]
    if not isinstance(digest, str) or not HEX_SHA256.fullmatch(digest):
        raise ValueError("source cross-pair duplicate exclusion digest is malformed")
    if distinct < 0 or excluded < 0 or affected < 0:
        raise ValueError("source cross-pair duplicate exclusion counts are negative")
    if affected > SOURCE_TRAJECTORY_PAIRS or affected > excluded:
        raise ValueError("source cross-pair duplicate exclusion pair count is impossible")
    if distinct == 0:
        if (
            excluded != 0
            or affected != 0
            or digest != hashlib.sha256(b"").hexdigest()
        ):
            raise ValueError("empty source cross-pair duplicate exclusion is inconsistent")
    elif excluded < 2 * distinct or affected < 2:
        raise ValueError("nonempty source cross-pair duplicate exclusion is inconsistent")
    return dict(exclusion)


def _parse_source_openings(
    openings_value: Any,
    *,
    openings_per_phase_side: int = SOURCE_OPENINGS_PER_PHASE_SIDE,
) -> tuple[dict[str, tuple[str, tuple[str, ...], str]], dict[str, Any]]:
    expected_rows = openings_per_phase_side * len(PHASES) * 2
    if not isinstance(openings_value, list) or len(openings_value) != expected_rows:
        raise ValueError(
            f"pinned source opening suite must contain exactly {expected_rows:,} openings"
        )
    openings: dict[str, tuple[str, tuple[str, ...], str]] = {}
    source_tags: set[str] = set()
    opening_ofens: set[str] = set()
    phase_side_counts: Counter[tuple[str, str]] = Counter()
    for index, opening_value in enumerate(openings_value):
        if not isinstance(opening_value, dict):
            raise ValueError(f"source opening {index} is not an object")
        _strict_keys(
            opening_value,
            {"id", "initialOfen", "moves", "source"},
            f"source opening {index}",
        )
        opening_id = opening_value["id"]
        source_tag = opening_value["source"]
        if (
            not isinstance(opening_id, str)
            or not opening_id
            or opening_id != opening_id.strip()
            or opening_id in openings
            or not isinstance(source_tag, str)
            or source_tag in source_tags
        ):
            raise ValueError(f"source opening {index} is malformed or duplicated")
        pair_match = SOURCE_PAIR_TAG.fullmatch(source_tag)
        if pair_match is None:
            raise ValueError("source opening provenance tag is malformed")
        pair_number = int(pair_match.group(1).rsplit("-", 1)[1])
        if not 1 <= pair_number <= SOURCE_TRAJECTORY_PAIRS:
            raise ValueError("source opening provenance pair is out of range")
        if opening_value["moves"] != []:
            raise ValueError("source choice probes require an exact empty moves array")
        initial_ofen = _canonical_source_ofen(opening_value["initialOfen"])
        if initial_ofen in opening_ofens:
            raise ValueError("source openings contain an exact duplicate OFEN")
        phase = _phase(initial_ofen)
        side = initial_ofen.split()[1]
        phase_side_counts[(phase, side)] += 1
        openings[opening_id] = (initial_ofen, tuple(), source_tag)
        source_tags.add(source_tag)
        opening_ofens.add(initial_ofen)
    expected_counts = {
        f"{phase}/{side}": openings_per_phase_side
        for phase in PHASES
        for side in ("w", "b")
    }
    actual_counts = {
        f"{phase}/{side}": phase_side_counts[(phase, side)]
        for phase in PHASES
        for side in ("w", "b")
    }
    if actual_counts != expected_counts:
        raise ValueError("source opening-suite phase/side quotas changed")
    proof = {
        "openingRows": expected_rows,
        "uniqueOpeningIds": len(openings),
        "uniqueOfens": len(opening_ofens),
        "uniqueTrajectoryPairTags": len(source_tags),
        "phaseSideCounts": actual_counts,
        "canonicalOfens": True,
        "exactEmptyMoveArrays": True,
        "rawPoolQuarantineRecomputed": True,
        "selectedOpeningsSurvivedClaimedPair": True,
    }
    return openings, proof


def _phase(ofen: str) -> str:
    pieces, _, _ = parse_ofen(ofen)
    phase = phase_of(len(pieces))
    if phase not in PHASES:
        raise ValueError("position is outside the four-phase corpus")
    return phase


def _target_opaque_source_identity(
    *,
    run_id: str,
    opening_suite_sha256: str,
    source_match_config_sha256: str,
    source_tag: str,
    game_id: str,
    attempt_number: int,
    ply_number: int,
    ofen: str,
    phase: str,
    side: str,
) -> tuple[str, str, str]:
    """Derive source group/root/rank using target-opaque provenance only.

    In particular, the source-events artifact identity must never enter this
    domain: that artifact also contains scores and game outcomes whose raw
    bytes are intentionally skipped by the structural telemetry parser.
    """

    if (
        not run_id
        or not source_tag
        or not game_id
        or type(attempt_number) is not int
        or attempt_number <= 0
        or type(ply_number) is not int
        or ply_number <= 0
        or not HEX_SHA256.fullmatch(opening_suite_sha256)
        or not HEX_SHA256.fullmatch(source_match_config_sha256)
    ):
        raise ValueError("target-opaque source identity inputs are malformed")
    canonical_ofen = _normalized_ofen(ofen)
    group_payload = (
        "omega-decision-source-group-v3\0"
        f"{opening_suite_sha256}\0{source_match_config_sha256}\0"
        f"{run_id}\0{source_tag}"
    )
    group_id = "trajectory:" + hashlib.sha256(
        group_payload.encode("utf-8")
    ).hexdigest()
    root_payload = (
        "omega-decision-root-v3\0"
        f"{opening_suite_sha256}\0{source_match_config_sha256}\0"
        f"{run_id}\0{game_id}\0{attempt_number}\0{ply_number}\0"
        f"{canonical_ofen}"
    )
    root_id = hashlib.sha256(root_payload.encode("utf-8")).hexdigest()
    return group_id, root_id, _expected_root_rank(root_id, phase, side, group_id)


def _string_options(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key).lower(): str(item).strip().lower() for key, item in value.items()}


def _field_ci(value: dict[str, Any], name: str, default: Any = None) -> Any:
    for key, item in value.items():
        if str(key).lower() == name.lower():
            return item
    return default


def _selected_fields_ci(
    value: dict[str, Any], names: Sequence[str], description: str
) -> dict[str, Any]:
    """Copy only explicitly approved fields from target-bearing telemetry."""

    result: dict[str, Any] = {}
    lowered = {str(key).lower(): key for key in value}
    if len(lowered) != len(value):
        raise ValueError(f"{description} has case-colliding field names")
    for name in names:
        key = lowered.get(name.lower())
        if key is not None:
            result[name] = value[key]
    return result


def _source_builder_verify_command(
    *,
    config: Path,
    completion_seal: Path,
    harness: Path,
    root_sampler: Path,
    root_sampler_chesslib: Path,
    root_pool: Path,
    root_pool_manifest: Path,
    root_pool_seal: Path,
) -> list[str]:
    return [
        str(_resolve(sys.executable)),
        "-B",
        str(SOURCE_OPENING_BUILDER.resolve()),
        "verify",
        "--config",
        str(config),
        "--completion-seal",
        str(completion_seal),
        "--harness",
        str(harness),
        "--root-sampler",
        str(root_sampler),
        "--root-sampler-chesslib",
        str(root_sampler_chesslib),
        "--pool",
        str(root_pool),
        "--pool-manifest",
        str(root_pool_manifest),
        "--pool-seal",
        str(root_pool_seal),
    ]


def _load_source_contract(
    opening_suite_path: Path,
    source_match_config_path: Path,
    source_match_completion_seal_path: Path,
    source_match_harness_path: Path,
    root_sampler_path: Path,
    root_sampler_chesslib_path: Path,
    source_root_pool_path: Path,
    source_root_pool_manifest_path: Path,
    source_root_pool_seal_path: Path,
    required_engine_sha256: str,
    data_profile_id: str,
    freshness_marker: str,
) -> dict[str, Any]:
    opening_suite_path = _resolve(opening_suite_path)
    source_match_config_path = _resolve(source_match_config_path)
    source_match_completion_seal_path = _resolve(source_match_completion_seal_path)
    source_match_harness_path = _resolve(source_match_harness_path)
    root_sampler_path = _resolve(root_sampler_path)
    root_sampler_chesslib_path = _resolve(root_sampler_chesslib_path)
    source_root_pool_path = _resolve(source_root_pool_path)
    source_root_pool_manifest_path = _resolve(source_root_pool_manifest_path)
    source_root_pool_seal_path = _resolve(source_root_pool_seal_path)
    source_paths = {
        "openingSuite": opening_suite_path,
        "sourceMatchConfig": source_match_config_path,
        "sourceMatchCompletionSeal": source_match_completion_seal_path,
        "sourceMatchHarnessAssembly": source_match_harness_path,
        "rootSamplerAssembly": root_sampler_path,
        "rootSamplerChessLibAssembly": root_sampler_chesslib_path,
        "sourceRootPool": source_root_pool_path,
        "sourceRootPoolManifest": source_root_pool_manifest_path,
        "sourceRootPoolSeal": source_root_pool_seal_path,
        "sourceOpeningBuilderSource": SOURCE_OPENING_BUILDER.resolve(),
    }
    before = {name: _identity(path) for name, path in source_paths.items()}
    subprocess.run(
        _source_builder_verify_command(
            config=source_match_config_path,
            completion_seal=source_match_completion_seal_path,
            harness=source_match_harness_path,
            root_sampler=root_sampler_path,
            root_sampler_chesslib=root_sampler_chesslib_path,
            root_pool=source_root_pool_path,
            root_pool_manifest=source_root_pool_manifest_path,
            root_pool_seal=source_root_pool_seal_path,
        ),
        check=True,
        cwd=str(REPO),
    )
    if {name: _identity(path) for name, path in source_paths.items()} != before:
        raise ValueError("source contract inputs changed during authenticated verification")
    suite, suite_identity = _load_json_snapshot(opening_suite_path)
    config, config_identity = _load_json_snapshot(source_match_config_path)
    harness_identity = before["sourceMatchHarnessAssembly"]
    root_sampler_identity = before["rootSamplerAssembly"]
    _strict_keys(
        suite,
        {
            "schemaVersion",
            "name",
            "rootSamplerSha256",
            "rootSamplerChessLibSha256",
            "rootPoolSha256",
            "rootPoolManifestSha256",
            "rootPoolSealSha256",
            "sourceBuilderSha256",
            "networkFormatSha256",
            "pythonRuntimeManifestSha256",
            "trajectoryCoverage",
            "crossPairDuplicateExclusion",
            "openings",
        },
        "source opening suite",
    )
    if suite["schemaVersion"] != 1 or not isinstance(suite["name"], str):
        raise ValueError("source opening suite header is malformed")
    for hash_name in (
        "rootSamplerSha256",
        "rootSamplerChessLibSha256",
        "rootPoolSha256",
        "rootPoolManifestSha256",
        "rootPoolSealSha256",
        "sourceBuilderSha256",
        "networkFormatSha256",
        "pythonRuntimeManifestSha256",
    ):
        if not HEX_SHA256.fullmatch(str(suite[hash_name]).lower()):
            raise ValueError(f"source opening suite {hash_name} is malformed")
    if str(suite["rootSamplerSha256"]).lower() != root_sampler_identity[
        "sha256"
    ]:
        raise ValueError("opening suite does not pin the root sampler assembly")
    for hash_name, identity_name in (
        ("rootSamplerChessLibSha256", "rootSamplerChessLibAssembly"),
        ("rootPoolSha256", "sourceRootPool"),
        ("rootPoolManifestSha256", "sourceRootPoolManifest"),
        ("rootPoolSealSha256", "sourceRootPoolSeal"),
        ("sourceBuilderSha256", "sourceOpeningBuilderSource"),
    ):
        if str(suite[hash_name]).lower() != before[identity_name]["sha256"]:
            raise ValueError(f"opening suite does not pin {identity_name}")
    if str(suite["networkFormatSha256"]).lower() != _identity(
        Path(omega_nnue.__file__)
    )["sha256"]:
        raise ValueError("opening suite does not pin the executing network format")
    runtime_contract.verify_manifest(runtime_contract.DEFAULT_OUTPUT)
    if str(suite["pythonRuntimeManifestSha256"]).lower() != _identity(
        runtime_contract.DEFAULT_OUTPUT
    )["sha256"]:
        raise ValueError("opening suite does not pin the Python/NumPy runtime")

    trajectory_coverage = _validate_source_trajectory_coverage(
        suite["trajectoryCoverage"]
    )
    exclusion = _validate_cross_pair_duplicate_exclusion(
        suite["crossPairDuplicateExclusion"]
    )
    openings, opening_proof = _parse_source_openings(suite["openings"])

    if int(_field_ci(config, "schemaVersion", -1)) != 1:
        raise ValueError("source match config has the wrong schema version")
    if (
        str(_field_ci(config, "profileId", "")) != data_profile_id
        or str(_field_ci(config, "freshnessMarker", "")) != freshness_marker
    ):
        raise ValueError("source match config data profile/freshness marker differs")
    expected_suite_sha = str(
        _field_ci(config, "expectedOpeningSuiteSha256", "")
    ).lower()
    expected_harness_sha = str(
        _field_ci(config, "expectedHarnessSha256", "")
    ).lower()
    expected_harness_bundle_sha = str(
        _field_ci(config, "expectedHarnessBundleSha256", "")
    ).lower()
    if expected_suite_sha != suite_identity["sha256"]:
        raise ValueError("source match config does not pin the opening suite")
    if expected_harness_sha != harness_identity["sha256"]:
        raise ValueError("source match config does not pin the source harness")
    if not HEX_SHA256.fullmatch(expected_harness_bundle_sha):
        raise ValueError("source match config does not pin a harness bundle")
    match = _field_ci(config, "match")
    if not isinstance(match, dict):
        raise ValueError("source match config has no match contract")
    mode = str(_field_ci(match, "mode", "")).lower()
    nodes = int(_field_ci(match, "nodes", 0))
    repeats = int(_field_ci(match, "repeats", 0))
    max_plies = int(_field_ci(match, "maxPlies", 0))
    absolute_max_plies = int(_field_ci(match, "absoluteMaxPlies", 0))
    fresh = _field_ci(match, "freshProcessPerGame", None)
    if (
        mode != "nodes"
        or nodes != SOURCE_NODES
        or repeats != 1
        or max_plies != SOURCE_MAX_PLIES
        or absolute_max_plies != SOURCE_MAX_PLIES
        or fresh is not True
    ):
        raise ValueError(
            "source config must freeze 2,000-node mode, one-ply max/absolute "
            "limits, one AB/BA repeat, and a fresh process per game"
        )
    engine_a = str(_field_ci(match, "engineA", ""))
    engine_b = str(_field_ci(match, "engineB", ""))
    if not engine_a or not engine_b or engine_a == engine_b:
        raise ValueError("source config must name two distinct HCE engines")
    config_engines = _field_ci(config, "engines")
    if not isinstance(config_engines, list) or len(config_engines) != 2:
        raise ValueError("source config must contain exactly two HCE engines")
    required_options = {
        name.lower(): value.lower() for name, value in DEFAULT_HCE_OPTIONS.items()
    }
    config_engine_ids: set[str] = set()
    for index, engine in enumerate(config_engines):
        _strict_keys(
            engine,
            {"id", "executable", "expectedSha256", "options"},
            f"source config engine {index}",
        )
        engine_id = str(engine["id"])
        if (
            not engine_id
            or engine_id in config_engine_ids
            or str(engine["expectedSha256"]).lower()
            != required_engine_sha256.lower()
            or _string_options(engine["options"]) != required_options
        ):
            raise ValueError(f"source config engine {index} violates HCE contract")
        config_engine_ids.add(engine_id)
    if config_engine_ids != {engine_a, engine_b}:
        raise ValueError("source match engine IDs differ from its engine inventory")
    run_id = str(_field_ci(config, "runId", ""))
    config_seed = int(_field_ci(config, "seed", -1))
    after = {name: _identity(path) for name, path in source_paths.items()}
    if after != before:
        raise ValueError("source contract inputs changed while the contract was loaded")
    return {
        "openingSuite": suite_identity,
        "sourceMatchConfig": config_identity,
        "sourceMatchCompletionSeal": before["sourceMatchCompletionSeal"],
        "sourceMatchHarnessAssembly": harness_identity,
        "rootSamplerAssembly": root_sampler_identity,
        "rootSamplerChessLibAssembly": before["rootSamplerChessLibAssembly"],
        "sourceRootPool": before["sourceRootPool"],
        "sourceRootPoolManifest": before["sourceRootPoolManifest"],
        "sourceRootPoolSeal": before["sourceRootPoolSeal"],
        "sourceOpeningBuilderSource": before["sourceOpeningBuilderSource"],
        "trajectoryCoverage": trajectory_coverage,
        "crossPairDuplicateExclusion": exclusion,
        "sourceOpeningProof": opening_proof,
        "openingSuiteProvenance": {
            name: str(suite[name]).lower()
            for name in (
                "rootSamplerSha256",
                "rootSamplerChessLibSha256",
                "rootPoolSha256",
                "rootPoolManifestSha256",
                "rootPoolSealSha256",
                "sourceBuilderSha256",
                "networkFormatSha256",
                "pythonRuntimeManifestSha256",
            )
        },
        "harnessBundleSha256": expected_harness_bundle_sha,
        "openings": openings,
        "runId": run_id,
        "seed": config_seed,
        "mode": mode,
        "nodes": nodes,
        "repeats": repeats,
        "maxPlies": max_plies,
        "absoluteMaxPlies": absolute_max_plies,
        "engineA": engine_a,
        "engineB": engine_b,
    }


def _verify_source_audit_evidence(
    profile: dict[str, Any],
    source_audit: dict[str, Any],
    *,
    reverified_contract: dict[str, Any] | None = None,
) -> None:
    for audit_name, final_name in {
        "sourceRootPool": "sourceRootPool",
        "sourceRootPoolManifest": "sourceRootPoolManifest",
        "sourceRootPoolSeal": "sourceRootPoolSeal",
        "sourceOpeningBuilderSource": "sourceOpeningBuilderSource",
        "sourceMatchCompletionSeal": "sourceMatchCompletionSeal",
    }.items():
        if source_audit.get(audit_name) != _frozen_identity(profile, final_name):
            raise ValueError(f"frozen root source {audit_name} is invalid")

    chesslib_identity = _verify_identity(
        source_audit.get("rootSamplerChessLibAssembly"),
        "frozen root-sampler ChessLib",
    )
    frozen_chesslib = _frozen_identity(profile, "chessLibAssembly")
    if any(
        chesslib_identity[name] != frozen_chesslib[name]
        for name in ("bytes", "sha256")
    ):
        raise ValueError("root-sampler ChessLib differs from frozen ChessLib content")

    pool_manifest, pool_manifest_identity = _load_json_snapshot(
        Path(source_audit["sourceRootPoolManifest"]["path"])
    )
    pool_seal, pool_seal_identity = _load_json_snapshot(
        Path(source_audit["sourceRootPoolSeal"]["path"])
    )
    if (
        pool_manifest_identity != source_audit["sourceRootPoolManifest"]
        or pool_seal_identity != source_audit["sourceRootPoolSeal"]
    ):
        raise ValueError("root source pool manifest/seal identity changed")
    manifest_runtime = pool_manifest.get("runtime")
    seal_producer = pool_seal.get("producer")
    if not isinstance(manifest_runtime, dict) or not isinstance(seal_producer, dict):
        raise ValueError("root source pool manifest/seal lacks runtime provenance")
    manifest_chesslib = _verify_identity(
        manifest_runtime.get("chessLibAssembly"),
        "root source pool manifest ChessLib",
    )
    seal_chesslib = _verify_identity(
        seal_producer.get("chessLibAssembly"),
        "root source pool seal ChessLib",
    )
    if manifest_chesslib != chesslib_identity or seal_chesslib != chesslib_identity:
        raise ValueError("root-sampler ChessLib is not bound by pool manifest and seal")

    suite, suite_identity = _load_json_snapshot(
        Path(_frozen_identity(profile, "sourceOpeningSuite")["path"])
    )
    if suite_identity != _frozen_identity(profile, "sourceOpeningSuite"):
        raise ValueError("frozen source opening suite identity changed")
    exclusion = _validate_cross_pair_duplicate_exclusion(
        suite.get("crossPairDuplicateExclusion")
    )
    trajectory_coverage = _validate_source_trajectory_coverage(
        suite.get("trajectoryCoverage")
    )
    _, opening_proof = _parse_source_openings(suite.get("openings"))
    if source_audit.get("trajectoryCoverage") != trajectory_coverage:
        raise ValueError("root source trajectory coverage differs from frozen suite")
    if source_audit.get("crossPairDuplicateExclusion") != exclusion:
        raise ValueError("root source duplicate exclusion differs from frozen suite")
    if source_audit.get("sourceOpeningProof") != opening_proof:
        raise ValueError("root source opening proof differs from frozen suite")

    if reverified_contract is not None:
        for name in (
            "openingSuite",
            "sourceMatchConfig",
            "sourceMatchCompletionSeal",
            "sourceMatchHarnessAssembly",
            "rootSamplerAssembly",
            "rootSamplerChessLibAssembly",
            "sourceRootPool",
            "sourceRootPoolManifest",
            "sourceRootPoolSeal",
            "sourceOpeningBuilderSource",
            "trajectoryCoverage",
            "crossPairDuplicateExclusion",
            "sourceOpeningProof",
            "openingSuiteProvenance",
            "harnessBundleSha256",
        ):
            if source_audit.get(name) != reverified_contract.get(name):
                raise ValueError(
                    f"root source audit {name} differs from authenticated re-verification"
                )


def _require_hce_run(
    run: dict[str, Any], *, required_engine_sha256: str | None
) -> dict[str, dict[str, Any]]:
    engines = run.get("Engines")
    if not isinstance(engines, list) or not engines:
        raise ValueError("trajectory run has no engine inventory")
    required = {key.lower(): value.lower() for key, value in DEFAULT_HCE_OPTIONS.items()}
    inventory: dict[str, dict[str, Any]] = {}
    hashes: set[str] = set()
    for engine in engines:
        if not isinstance(engine, dict):
            raise ValueError("malformed engine inventory")
        _strict_keys(
            engine,
            {
                "Id",
                "Sha256",
                "FileSize",
                "Options",
                "OmegaNnueActiveVerified",
                "ExternalAssets",
            },
            "source run engine",
        )
        engine_id = str(engine.get("Id", ""))
        sha = str(engine.get("Sha256", "")).lower()
        if not engine_id or len(sha) != 64:
            raise ValueError("engine inventory lacks a stable ID or SHA-256")
        if required_engine_sha256 and sha != required_engine_sha256.lower():
            raise ValueError(f"unexpected HCE engine SHA-256 for {engine_id}")
        options = _string_options(engine.get("Options"))
        if options != required:
            raise ValueError(f"{engine_id}: source run HCE option inventory differs")
        if engine.get("OmegaNnueActiveVerified") is not False:
            raise ValueError(f"{engine_id}: source did not verify NNUE was inactive")
        if engine.get("ExternalAssets") not in ([], None):
            raise ValueError(f"{engine_id}: source HCE engine has external assets")
        for name, expected in required.items():
            actual = options.get(name)
            # OmegaNNUEFile is serialized as either an empty string or the
            # explicit sentinel by different harness versions.
            if name == "omegannuefile" and actual in ("", "<empty>"):
                continue
            if actual != expected:
                raise ValueError(
                    f"{engine_id}: HCE-only option {name} is {actual!r}, "
                    f"expected {expected!r}"
                )
        hashes.add(sha)
        inventory[engine_id] = {
            "id": engine_id,
            "sha256": sha,
            "bytes": engine.get("FileSize"),
            "options": options,
            "omegaNnueActiveVerified": False,
            "externalAssets": [],
        }
    if len(hashes) != 1:
        raise ValueError("on-policy source must use one identical HCE binary")
    return inventory


@dataclass
class _Attempt:
    start: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    plies: list[tuple[int, dict[str, Any]]] = field(default_factory=list)


def _extract_source_candidates(
    path: Path,
    *,
    seed: int,
    source_seed: int,
    data_profile_id: str,
    freshness_marker: str,
    required_engine_sha256: str,
    source_contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, pin = _snapshot_source_events(path)
    run: dict[str, Any] | None = None
    attempts: dict[tuple[str, int], _Attempt] = defaultdict(_Attempt)
    for line_number, record in records:
        kind = _field_ci(record, "RecordType")
        if kind == "run":
            if run is not None:
                raise ValueError(f"{path}:{line_number}: multiple run records")
            run = record
        elif kind in ("gameStart", "ply", "gameResult"):
            common = _selected_fields_ci(
                record,
                ("RecordType", "GameId", "Attempt"),
                f"{path}:{line_number}:{kind}",
            )
            game_id = str(common.get("GameId", ""))
            attempt_number = int(common.get("Attempt", 0))
            if not game_id or attempt_number <= 0:
                raise ValueError(f"{path}:{line_number}: invalid game attempt")
            attempt = attempts[(game_id, attempt_number)]
            if kind == "gameStart":
                if attempt.start is not None:
                    raise ValueError(
                        f"{path}:{line_number}: duplicate gameStart for "
                        f"{game_id}/attempt {attempt_number}"
                    )
                attempt.start = record
            elif kind == "gameResult":
                if attempt.result is not None:
                    raise ValueError(
                        f"{path}:{line_number}: duplicate gameResult for "
                        f"{game_id}/attempt {attempt_number}"
                    )
                # The literal Result/outcome is intentionally not copied or
                # inspected.  Completion is structural: a terminal record,
                # its exact ply count, and zero frozen safety counters.
                attempt.result = record
            else:
                attempt.plies.append((line_number, record))
        else:
            raise ValueError(f"{path}:{line_number}: unsupported telemetry record {kind!r}")
    if run is None:
        raise ValueError(f"{path}: no run record")
    inventory = _require_hce_run(
        run, required_engine_sha256=required_engine_sha256
    )
    if set(inventory) != {source_contract["engineA"], source_contract["engineB"]}:
        raise ValueError("source run engine IDs differ from the pinned source config")
    run_id = str(run.get("RunId", ""))
    if not run_id:
        raise ValueError(f"{path}: run has no RunId")
    if int(_field_ci(run, "SourceSeed", _field_ci(run, "Seed", -1))) != source_seed:
        raise ValueError(f"{path}: source run seed is not frozen seed {source_seed}")
    if str(_field_ci(run, "ProfileId", "")) != data_profile_id:
        raise ValueError(
            f"{path}: source run does not declare data profile {data_profile_id!r}"
        )
    if str(_field_ci(run, "FreshnessMarker", "")) != freshness_marker:
        raise ValueError(f"{path}: source run freshness marker does not match CLI")
    if run_id != source_contract["runId"]:
        raise ValueError(f"{path}: run ID differs from the pinned source config")
    if source_contract["seed"] != source_seed:
        raise ValueError("pinned source config seed differs from the frozen source seed")
    if str(_field_ci(run, "ConfigSha256", "")).lower() != source_contract[
        "sourceMatchConfig"
    ]["sha256"]:
        raise ValueError(f"{path}: run does not pin the exact source config")
    if str(_field_ci(run, "OpeningSuiteSha256", "")).lower() != source_contract[
        "openingSuite"
    ]["sha256"]:
        raise ValueError(f"{path}: run/config/opening-suite identity mismatch")
    if str(_field_ci(run, "HarnessSha256", "")).lower() != source_contract[
        "sourceMatchHarnessAssembly"
    ]["sha256"]:
        raise ValueError(f"{path}: run/config/source-harness identity mismatch")
    if str(_field_ci(run, "HarnessBundleSha256", "")).lower() != source_contract[
        "harnessBundleSha256"
    ]:
        raise ValueError(f"{path}: run/config harness-bundle identity mismatch")
    run_match = _field_ci(run, "Match")
    if not isinstance(run_match, dict):
        raise ValueError(f"{path}: run has no structural match contract")
    if (
        str(_field_ci(run_match, "Mode", "")).lower() != source_contract["mode"]
        or int(_field_ci(run_match, "Nodes", 0)) != source_contract["nodes"]
        or int(_field_ci(run_match, "Repeats", 0)) != source_contract["repeats"]
        or int(_field_ci(run_match, "MaxPlies", 0))
        != source_contract["maxPlies"]
        or int(_field_ci(run_match, "AbsoluteMaxPlies", 0))
        != source_contract["absoluteMaxPlies"]
    ):
        raise ValueError(f"{path}: source node mode/budget/repeats differ from config")
    candidates: list[dict[str, Any]] = []
    complete_games: list[tuple[str, int, _Attempt]] = []
    opening_contracts: dict[str, tuple[str, tuple[str, ...]]] = {}
    for game_id in sorted({key[0] for key in attempts}):
        game_attempts = sorted(
            attempt_number
            for candidate_game, attempt_number in attempts
            if candidate_game == game_id
        )
        if game_attempts != [1]:
            raise ValueError(f"{path}: source game {game_id} has retried/abandoned attempts")
        latest_number = 1
        attempt = attempts[(game_id, 1)]
        if attempt.start is None or attempt.result is None:
            raise ValueError(f"{path}: latest attempt for {game_id} is incomplete")
        complete_games.append((game_id, latest_number, attempt))

    expected_openings = source_contract["openings"]
    by_pair: dict[str, list[tuple[str, int, _Attempt]]] = defaultdict(list)
    seen_openings: Counter[str] = Counter()
    for game_id, attempt_number, attempt in complete_games:
        assert attempt.start is not None
        assert attempt.result is not None
        opening_id = str(_field_ci(attempt.start, "OpeningId", "")).strip()
        pair_id = str(_field_ci(attempt.start, "PairId", "")).strip()
        if not opening_id or not pair_id:
            raise ValueError(f"{path}: game {game_id} lacks OpeningId/PairId")
        initial_ofen = _normalized_ofen(_field_ci(attempt.start, "InitialOfen"))
        opening_moves_value = _field_ci(attempt.start, "OpeningMoves", [])
        if not isinstance(opening_moves_value, list) or not all(
            isinstance(move, str) and move for move in opening_moves_value
        ):
            raise ValueError(f"{path}: game {game_id} has malformed OpeningMoves")
        opening_contract = (
            initial_ofen,
            tuple(str(move).lower() for move in opening_moves_value),
        )
        if opening_id not in expected_openings or opening_contract != expected_openings[
            opening_id
        ][:2]:
            raise ValueError(
                f"{path}: game {game_id} differs from pinned opening {opening_id!r}"
            )
        prior_opening = opening_contracts.setdefault(opening_id, opening_contract)
        if prior_opening != opening_contract:
            raise ValueError(
                f"{path}: OpeningId {opening_id!r} has conflicting OFEN/moves"
            )
        for field in (
            "GameId",
            "PairId",
            "Attempt",
            "OpeningId",
            "WhiteEngineId",
            "BlackEngineId",
        ):
            if _field_ci(attempt.start, field) != _field_ci(attempt.result, field):
                raise ValueError(
                    f"{path}: start/result structural field {field} differs for {game_id}"
                )
        for counter in (
            "IllegalMoves",
            "IllegalPvs",
            "ProtocolFailures",
            "TimeForfeits",
        ):
            if int(_field_ci(attempt.result, counter, -1)) != 0:
                raise ValueError(f"{path}: nonzero source safety counter {counter}")
        ordered_plies = sorted(
            attempt.plies, key=lambda item: int(item[1].get("Ply", 0))
        )
        result_plies = int(_field_ci(attempt.result, "Plies", -1))
        ply_numbers = [int(item[1].get("Ply", 0)) for item in ordered_plies]
        if result_plies != SOURCE_MAX_PLIES or ply_numbers != [1]:
            raise ValueError(
                f"{path}: accepted source game must contain exactly one ply"
            )
        expected_command = f"go nodes {source_contract['nodes']}"
        white_engine = str(_field_ci(attempt.start, "WhiteEngineId", ""))
        black_engine = str(_field_ci(attempt.start, "BlackEngineId", ""))
        if {white_engine, black_engine} != {
            source_contract["engineA"], source_contract["engineB"]
        }:
            raise ValueError(f"{path}: game {game_id} uses unexpected engines")
        previous_post: str | None = None
        for line_number, ply in ordered_plies:
            pre_ofen = _normalized_ofen(ply.get("PreOfen"))
            post_ofen = _normalized_ofen(ply.get("PostOfen"))
            if previous_post is not None and pre_ofen != previous_post:
                raise ValueError(f"{path}:{line_number}: source trajectory is discontinuous")
            previous_post = post_ofen
            if ply.get("Error") not in (None, ""):
                raise ValueError(f"{path}:{line_number}: source ply has an error")
            if str(ply.get("SearchCommand", "")).lower() != expected_command:
                raise ValueError(
                    f"{path}:{line_number}: source ply is not exact node-budget search"
                )
            side = pre_ofen.split()[1].lower()
            expected_engine = white_engine if side == "w" else black_engine
            if str(ply.get("EngineId", "")) != expected_engine:
                raise ValueError(f"{path}:{line_number}: engine/color/OFEN mismatch")
            color = str(ply.get("Color", "")).lower()
            if color not in ({"w", "white"} if side == "w" else {"b", "black"}):
                raise ValueError(f"{path}:{line_number}: color/OFEN mismatch")
            move = str(ply.get("BestMove", "")).lower()
            if not move or move in ("0000", "(none)"):
                raise ValueError(f"{path}:{line_number}: missing structural best move")
        if previous_post != _normalized_ofen(_field_ci(attempt.result, "FinalOfen")):
            raise ValueError(f"{path}: final OFEN differs from final source ply")
        by_pair[pair_id].append((game_id, attempt_number, attempt))
        seen_openings[opening_id] += 1

    if set(opening_contracts) != set(expected_openings):
        raise ValueError("source telemetry does not cover the exact pinned opening suite")
    if any(count != 2 for count in seen_openings.values()):
        raise ValueError("each pinned opening must have exactly one complete AB/BA pair")
    if len(by_pair) != len(expected_openings):
        raise ValueError("source pair count differs from the pinned opening suite")
    for pair_id, games in sorted(by_pair.items()):
        if len(games) != 2:
            raise ValueError(f"{path}: pair {pair_id!r} is not complete AB/BA")
        starts = [game[2].start for game in games]
        assert starts[0] is not None and starts[1] is not None
        openings = {str(_field_ci(start, "OpeningId", "")) for start in starts}
        contracts = {
            (
                _normalized_ofen(_field_ci(start, "InitialOfen")),
                tuple(str(move).lower() for move in _field_ci(start, "OpeningMoves", [])),
            )
            for start in starts
        }
        color_orders = {
            (
                str(_field_ci(start, "WhiteEngineId", "")),
                str(_field_ci(start, "BlackEngineId", "")),
            )
            for start in starts
        }
        expected_orders = {
            (source_contract["engineA"], source_contract["engineB"]),
            (source_contract["engineB"], source_contract["engineA"]),
        }
        if len(openings) != 1 or len(contracts) != 1 or color_orders != expected_orders:
            raise ValueError(
                f"{path}: pair {pair_id!r} is not same-opening complete color swap"
            )

    for game_id, attempt_number, attempt in complete_games:
        assert attempt.start is not None
        opening_id = str(_field_ci(attempt.start, "OpeningId", "")).strip()
        pair_id = str(_field_ci(attempt.start, "PairId", "")).strip()
        source_tag = str(expected_openings[opening_id][2])
        ordered_plies = sorted(
            attempt.plies, key=lambda item: int(item[1].get("Ply", 0))
        )
        for line_number, ply in ordered_plies:
            ofen = _normalized_ofen(ply.get("PreOfen"))
            engine_id = str(ply.get("EngineId", ""))
            if engine_id not in inventory:
                raise ValueError(f"{path}:{line_number}: unknown engine {engine_id}")
            move = str(ply.get("BestMove", "")).lower()
            if not move or move in ("0000", "(none)"):
                continue
            ply_number = int(ply.get("Ply", 0))
            phase = _phase(ofen)
            side = ofen.split()[1].lower()
            if seed != ROOT_SELECTION_SEED:
                raise ValueError("root extraction seed differs from the G5 rank domain")
            group_id, root_id, rank = _target_opaque_source_identity(
                run_id=run_id,
                opening_suite_sha256=source_contract["openingSuite"]["sha256"],
                source_match_config_sha256=source_contract["sourceMatchConfig"][
                    "sha256"
                ],
                source_tag=source_tag,
                game_id=game_id,
                attempt_number=attempt_number,
                ply_number=ply_number,
                ofen=ofen,
                phase=phase,
                side=side,
            )
            candidates.append(
                {
                    "schemaVersion": 1,
                    "kind": "omega-hce-on-policy-root",
                    "rootId": root_id,
                    "groupId": group_id,
                    "sourceOpeningId": opening_id,
                    "sourcePairId": pair_id,
                    "sourceProvenanceTag": source_tag,
                    "sourceGameId": f"{run_id}:{game_id}:a{attempt_number}",
                    "sourceRunId": run_id,
                    "sourceAttempt": attempt_number,
                    "sourcePly": ply_number,
                    "sourceLine": line_number,
                    "sourceEngineId": engine_id,
                    "sourceEngineSha256": inventory[engine_id]["sha256"],
                    "phase": phase,
                    "sideToMove": side,
                    "ofen": ofen,
                    "rootPvMove": move,
                    "selectionRank": rank,
                }
            )
    audit = {
        "source": pin,
        "runId": run_id,
        "sourceSeed": source_seed,
        "dataProfileId": data_profile_id,
        "freshnessMarker": freshness_marker,
        "completeGames": len(complete_games),
        "completePairs": len(by_pair),
        "openingGroups": len(opening_contracts),
        "sourceProvenanceGroups": len(
            {contract[2] for contract in expected_openings.values()}
        ),
        "candidateRoots": len(candidates),
        "engineInventory": inventory,
        "targetInformationRead": False,
        "sourceTelemetryParser": (
            "lexical top-level allowlist; nonallowlisted score/result/outcome "
            "value slices are skipped without json decoding"
        ),
        "searchScoresDecoded": 0,
        "gameOutcomesDecoded": 0,
        "literalGameResultDecoded": False,
        "completionRule": (
            "gameResult record presence + exact contiguous Result.Plies + final OFEN "
            "+ zero safety counters; literal Result is never decoded"
        ),
        "sourceNodeMode": source_contract["mode"],
        "sourceNodes": source_contract["nodes"],
        "sourceRepeats": source_contract["repeats"],
        "sourceMaxPlies": source_contract["maxPlies"],
        "sourceAbsoluteMaxPlies": source_contract["absoluteMaxPlies"],
        "openingSuite": source_contract["openingSuite"],
        "sourceMatchConfig": source_contract["sourceMatchConfig"],
        "sourceMatchCompletionSeal": source_contract["sourceMatchCompletionSeal"],
        "sourceMatchHarnessAssembly": source_contract[
            "sourceMatchHarnessAssembly"
        ],
        "rootSamplerAssembly": source_contract["rootSamplerAssembly"],
        "rootSamplerChessLibAssembly": source_contract[
            "rootSamplerChessLibAssembly"
        ],
        "sourceRootPool": source_contract["sourceRootPool"],
        "sourceRootPoolManifest": source_contract["sourceRootPoolManifest"],
        "sourceRootPoolSeal": source_contract["sourceRootPoolSeal"],
        "sourceOpeningBuilderSource": source_contract[
            "sourceOpeningBuilderSource"
        ],
        "trajectoryCoverage": source_contract["trajectoryCoverage"],
        "crossPairDuplicateExclusion": source_contract[
            "crossPairDuplicateExclusion"
        ],
        "sourceOpeningProof": source_contract["sourceOpeningProof"],
        "openingSuiteProvenance": source_contract["openingSuiteProvenance"],
        "harnessBundleSha256": source_contract["harnessBundleSha256"],
        "abBaPairsComplete": True,
        "abandonedAttempts": 0,
        "colorSwapsComplete": True,
        "sameOpeningWithinPair": True,
        "sourceSafetyCounters": {
            "illegalMoves": 0,
            "illegalPvs": 0,
            "protocolFailures": 0,
            "timeForfeits": 0,
        },
        "decodedPlyFields": [
            "RecordType",
            "GameId",
            "Attempt",
            "Ply",
            "EngineId",
            "Color",
            "PreOfen",
            "PostOfen",
            "BestMove",
            "Error",
            "Search.Command",
        ],
    }
    return candidates, audit


def _prepare_roots(args: argparse.Namespace) -> None:
    output = _resolve(args.output)
    manifest_path = _resolve(args.manifest or str(output) + ".manifest.json")
    if output.name != "raw-roots.jsonl":
        raise ValueError("G5 prepare-roots must publish canonical raw-roots.jsonl")
    if manifest_path != Path(str(output) + ".manifest.json"):
        raise ValueError("root manifest must be the adjacent no-clobber path")
    if output.exists() or manifest_path.exists():
        raise FileExistsError("root output or manifest already exists")
    all_candidates: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    if len(args.events) != 1:
        raise ValueError("G5 source provenance requires exactly one pinned events ledger")
    source_contract = _load_source_contract(
        _resolve(args.opening_suite),
        _resolve(args.source_match_config),
        _resolve(args.source_match_completion_seal),
        _resolve(args.source_match_harness),
        _resolve(args.root_sampler),
        _resolve(args.root_sampler_chesslib),
        _resolve(args.source_root_pool),
        _resolve(args.source_root_pool_manifest),
        _resolve(args.source_root_pool_seal),
        args.required_engine_sha256,
        args.data_profile_id,
        args.freshness_marker,
    )
    for source in args.events:
        candidates, audit = _extract_source_candidates(
            _resolve(source),
            seed=args.seed,
            source_seed=args.source_seed,
            data_profile_id=args.data_profile_id,
            freshness_marker=args.freshness_marker,
            required_engine_sha256=args.required_engine_sha256,
            source_contract=source_contract,
        )
        all_candidates.extend(candidates)
        audits.append(audit)
    root_ids = [str(candidate["rootId"]) for candidate in all_candidates]
    if len(root_ids) != len(set(root_ids)):
        raise ValueError("duplicate root IDs in source telemetry")
    by_exact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in all_candidates:
        exact, _, _ = deep._leakage_keys(str(candidate["ofen"]))
        by_exact[exact].append(candidate)
    unique: list[dict[str, Any]] = []
    collapsed_equivalent_records = 0
    for exact, equivalent in sorted(by_exact.items()):
        groups = {str(item["groupId"]) for item in equivalent}
        if len(groups) != 1:
            raise ValueError(
                "exact OFEN/input occurs in distinct source opening groups: "
                f"{exact}"
            )
        moves = {str(item["rootPvMove"]) for item in equivalent}
        if len(moves) != 1:
            raise ValueError(
                "equivalent AB/BA source records disagree on best move: "
                f"{exact}"
            )
        chosen = min(
            equivalent,
            key=lambda item: (str(item["selectionRank"]), str(item["rootId"])),
        )
        unique.append(chosen)
        collapsed_equivalent_records += len(equivalent) - 1
    signature_owner: dict[str, str] = {}
    for candidate in unique:
        group = str(candidate["groupId"])
        _, _, signatures = deep._leakage_keys(str(candidate["ofen"]))
        for signature in signatures:
            prior = signature_owner.setdefault(signature, group)
            if prior != group:
                raise ValueError(
                    "conservative orbit occurs in distinct source opening groups: "
                    f"{signature}"
                )
    if args.roots_per_phase % 2 or args.reserve_per_phase % 2:
        raise ValueError("per-phase primary and reserve quotas must be even")
    required_per_side = (args.roots_per_phase + args.reserve_per_phase) // 2
    primary_per_side = args.roots_per_phase // 2
    selected: list[dict[str, Any]] = []
    coverage: dict[str, int] = {}
    side_coverage: Counter[tuple[str, str]] = Counter()
    used_groups: set[str] = set()
    for phase in PHASES:
        phase_total = 0
        for side in ("w", "b"):
            candidates = sorted(
                (
                    item
                    for item in unique
                    if item["phase"] == phase and item["sideToMove"] == side
                ),
                key=lambda item: (item["selectionRank"], item["rootId"]),
            )
            retained: list[dict[str, Any]] = []
            for item in candidates:
                # One root per complete trajectory group makes the advertised
                # 4,096 roots also 4,096 leakage-independent source groups.
                if item["groupId"] in used_groups:
                    continue
                role = "primary" if len(retained) < primary_per_side else "reserve"
                retained.append({**item, "candidateRole": role})
                used_groups.add(item["groupId"])
                if len(retained) == required_per_side:
                    break
            if len(retained) != required_per_side:
                raise ValueError(
                    f"{phase}/{side}: only {len(retained)}/{required_per_side} "
                    "unique source groups satisfy the frozen quota"
                )
            selected.extend(retained)
            side_coverage[(phase, side)] = len(retained)
            phase_total += len(retained)
        coverage[phase] = phase_total
    selected.sort(key=lambda item: (PHASES.index(item["phase"]), item["selectionRank"]))
    _atomic_jsonl(output, selected)
    try:
        _atomic_json(
            manifest_path,
            {
                "schemaVersion": 1,
                "kind": "omega-decision-root-manifest",
                "createdUtc": _utc_now(),
                "profileId": args.profile_id,
                "freshnessMarker": args.freshness_marker,
                "policy": {
                    "sourceSeed": args.source_seed,
                    "seed": args.seed,
                    "rootsPerPhase": args.roots_per_phase,
                    "reservePerPhase": args.reserve_per_phase,
                    "maximumRootsPerSourceGroup": 1,
                    "primaryPerPhaseAndSide": primary_per_side,
                    "reservePerPhaseAndSide": args.reserve_per_phase // 2,
                    "selection": "target-blind SHA-256 rank",
                    "source": "latest complete HCE-only OmegaMatch attempt",
                    "sourceGrouping": (
                        "one root maximum per pinned opening Source provenance tag; "
                        "the complete AB/BA pair stays indivisible"
                    ),
                    "equivalentAbBaPolicy": "same-group exact inputs with the same move are deterministically collapsed; conflicts abort",
                    "requiredHceOptions": DEFAULT_HCE_OPTIONS,
                    "requiredEngineSha256": args.required_engine_sha256,
                },
                "coverage": {
                    "records": len(selected),
                    "phaseCounts": coverage,
                    "phaseSideCounts": {
                        f"{phase}/{side}": side_coverage[(phase, side)]
                        for phase in PHASES
                        for side in ("w", "b")
                    },
                    "primaryPhaseSideCounts": {
                        f"{phase}/{side}": sum(
                            item["phase"] == phase
                            and item["sideToMove"] == side
                            and item["candidateRole"] == "primary"
                            for item in selected
                        )
                        for phase in PHASES
                        for side in ("w", "b")
                    },
                    "primary": args.roots_per_phase * len(PHASES),
                    "reserve": args.reserve_per_phase * len(PHASES),
                    "uniqueRootIds": len({item["rootId"] for item in selected}),
                    "sourceGroups": len({item["groupId"] for item in selected}),
                    "oneRootPerSourceGroup": len(selected)
                    == len({item["groupId"] for item in selected}),
                    "collapsedEquivalentAbBaRecords": collapsed_equivalent_records,
                },
                "sources": audits,
                "producer": _producer_identity(),
                "finalStageSeal": True,
                "output": _identity(output),
            },
        )
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print(f"Prepared {len(selected)} target-blind on-policy roots: {output}")


def _expand(args: argparse.Namespace) -> None:
    sampler = _resolve(args.sampler)
    roots = _resolve(args.roots)
    output = _resolve(args.output)
    if roots.name != "raw-roots.jsonl" or output.name != "raw-children.jsonl":
        raise ValueError("G5 expansion must map raw-roots.jsonl to raw-children.jsonl")
    if not sampler.is_file() or not roots.is_file():
        raise FileNotFoundError("sampler or root input is missing")
    manifest_path = Path(str(output) + ".manifest.json")
    seal_path = Path(str(output) + ".complete.seal.json")
    if output.exists() or manifest_path.exists() or seal_path.exists():
        raise FileExistsError("child output, manifest, or seal already exists")
    command = [str(sampler)]
    if sampler.suffix.lower() == ".dll":
        command.insert(0, str(_resolve(args.dotnet)))
    command.extend(["--input", str(roots), "--output", str(output)])
    subprocess.run(command, check=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("input", {}).get("sha256") != _sha256(roots):
        raise ValueError("sampler manifest does not pin the requested roots")
    if manifest.get("coverage", {}).get("uniqueChildIds") != manifest.get(
        "coverage", {}
    ).get("children"):
        raise ValueError("sampler reported a child-ID collision")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if (
        seal.get("kind") != "omega-decision-sampler-completion-seal"
        or seal.get("input") != _identity(roots)
        or seal.get("output") != _identity(output)
        or seal.get("manifest") != _identity(manifest_path)
        or seal.get("finalStageSeal") is not True
    ):
        raise ValueError("sampler completion seal is invalid")
    print(f"Expanded legal siblings: {output}")


def _bucket_key(value: dict[str, Any], *, child: bool = False) -> tuple[str, str]:
    phase = str(value.get("phase") or "")
    side_field = "parentSideToMove" if child else "sideToMove"
    side = str(value.get(side_field) or "").lower()
    if phase not in PHASES or side not in ("w", "b"):
        raise ValueError(f"invalid phase/side bucket {phase!r}/{side!r}")
    return phase, side


def _expected_root_rank(
    root_id: str, phase: str, side: str, group_id: str
) -> str:
    if not root_id or phase not in PHASES or side not in ("w", "b") or not group_id:
        raise ValueError("root rank inputs are malformed")
    return hashlib.sha256(
        (
            f"omega-g5-feasible-root-v1\0{ROOT_SELECTION_SEED}\0{phase}\0"
            f"{side}\0{group_id}\0{root_id}"
        ).encode("utf-8")
    ).hexdigest()


def _raw_component_ids(
    roots: Sequence[dict[str, Any]], children: Sequence[dict[str, Any]]
) -> dict[str, str]:
    """Build leakage components over the complete raw graph, before filtering."""

    groups = sorted({str(root.get("groupId") or "") for root in roots})
    if not groups or any(not group for group in groups):
        raise ValueError("raw roots contain an empty source group")
    dsu = _DisjointSet(groups)
    signature_owner: dict[str, str] = {}
    source_owner: dict[str, str] = {}

    def observe(group: str, source_game: str, ofen: Any) -> None:
        if group not in dsu.parent:
            raise ValueError("raw graph contains a child from an unknown source group")
        if not source_game:
            raise ValueError("raw graph row lacks sourceGameId")
        dsu.union(group, source_owner.setdefault(source_game, group))
        _, _, signatures = deep._leakage_keys(_normalized_ofen(ofen))
        for signature in signatures:
            dsu.union(group, signature_owner.setdefault(signature, group))

    for root in roots:
        observe(
            str(root["groupId"]), str(root.get("sourceGameId") or ""), root.get("ofen")
        )
    for child in children:
        group = str(child.get("groupId") or "")
        source_game = str(child.get("sourceGameId") or "")
        observe(group, source_game, child.get("parentOfen"))
        observe(group, source_game, child.get("childOfen"))

    members: dict[str, list[str]] = defaultdict(list)
    for group in groups:
        members[dsu.find(group)].append(group)
    component_for_representative = {
        representative: "raw-decision-component:"
        + hashlib.sha256(
            (
                "omega-raw-decision-component-v2\0"
                + "\0".join(sorted(component_members))
            ).encode("utf-8")
        ).hexdigest()
        for representative, component_members in members.items()
    }
    return {
        group: component_for_representative[dsu.find(group)] for group in groups
    }


def _audit_raw_graph(
    roots: Sequence[dict[str, Any]],
    children: Sequence[dict[str, Any]],
    *,
    raw_per_phase_side: int = RAW_ROOTS_PER_PHASE_SIDE,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Authenticate the complete target-opaque raw graph and classify roots.

    Sparse roots are ordinary feasibility rejects.  Structural ambiguity
    (duplicates, foreign children, altered ranks, or target-bearing fields) is
    an abort because filtering must never conceal a malformed raw input.
    """

    if _has_target_like_key(list(roots)) or _has_target_like_key(list(children)):
        raise ValueError("target/score-like field exists in the raw graph")
    root_by_id: dict[str, dict[str, Any]] = {}
    group_owner: dict[str, str] = {}
    ranks: set[str] = set()
    bucket_counts: Counter[tuple[str, str]] = Counter()
    for root in roots:
        root_id = str(root.get("rootId") or "")
        group = str(root.get("groupId") or "")
        rank = str(root.get("selectionRank") or "")
        if not HEX_SHA256.fullmatch(root_id) or root_id in root_by_id:
            raise ValueError("raw roots contain an empty or duplicate root ID")
        if (
            re.fullmatch(r"trajectory:[0-9a-f]{64}", group) is None
            or group in group_owner
        ):
            raise ValueError("raw roots must contain exactly one root per source group")
        phase, side = _bucket_key(root)
        if (
            not HEX_SHA256.fullmatch(rank)
            or rank != _expected_root_rank(root_id, phase, side, group)
            or rank in ranks
        ):
            raise ValueError("raw root has an altered or duplicate target-blind rank")
        normalized_ofen = _normalized_ofen(root.get("ofen"))
        if (
            normalized_ofen.split()[1].lower() != side
            or _phase(normalized_ofen) != phase
        ):
            raise ValueError("raw root phase/side metadata disagrees with its OFEN")
        bucket_counts[(phase, side)] += 1
        root_by_id[root_id] = root
        group_owner[group] = root_id
        ranks.add(rank)
    expected_buckets = {(phase, side) for phase in PHASES for side in ("w", "b")}
    if set(bucket_counts) != expected_buckets or any(
        bucket_counts[bucket] != raw_per_phase_side for bucket in expected_buckets
    ):
        raise ValueError(
            "raw root inventory must contain exactly "
            f"{raw_per_phase_side} roots in every phase/side bucket"
        )

    by_root: dict[str, list[dict[str, Any]]] = {
        root_id: [] for root_id in root_by_id
    }
    child_ids: set[str] = set()
    moves_by_root: dict[str, set[str]] = defaultdict(set)
    for child in children:
        child_id = _item_id(child)
        if not HEX_SHA256.fullmatch(child_id) or child_id in child_ids:
            raise ValueError("raw children contain a duplicate child ID")
        child_ids.add(child_id)
        root_id = str(child.get("rootId") or "")
        root = root_by_id.get(root_id)
        if root is None:
            raise ValueError("raw child belongs to an unknown root")
        if any(
            child.get(field) != root.get(root_field)
            for field, root_field in (
                ("groupId", "groupId"),
                ("sourceGameId", "sourceGameId"),
                ("phase", "phase"),
                ("parentOfen", "ofen"),
                ("rootPvMove", "rootPvMove"),
                ("selectionRank", "selectionRank"),
            )
        ) or str(child.get("parentSideToMove") or "").lower() != str(
            root.get("sideToMove") or ""
        ).lower():
            raise ValueError("raw child does not preserve its authenticated root metadata")
        move = str(child.get("move") or "").lower()
        if not move or move in moves_by_root[root_id]:
            raise ValueError("raw expansion contains an empty or duplicate legal move")
        child_ofen = _normalized_ofen(child.get("childOfen"))
        child_side = str(child.get("childSideToMove") or "").lower()
        if child_side != child_ofen.split()[1].lower() or child_side == str(
            root.get("sideToMove") or ""
        ).lower():
            raise ValueError("raw child side-to-move metadata disagrees with its OFEN")
        moves_by_root[root_id].add(move)
        by_root[root_id].append(child)

    components = _raw_component_ids(roots, children)
    feasibility: list[dict[str, Any]] = []
    for root_id, root in sorted(root_by_id.items()):
        items = by_root[root_id]
        pv = str(root.get("rootPvMove") or "").lower()
        pv_occurrences = sum(str(item.get("move") or "").lower() == pv for item in items)
        reasons: list[str] = []
        if len(items) < 5:
            reasons.append("fewer-than-five-distinct-legal-children")
        if pv_occurrences != 1:
            reasons.append("source-pv-not-exactly-once")
        feasibility.append(
            {
                "schemaVersion": 1,
                "kind": "omega-decision-root-feasibility",
                "rootId": root_id,
                "groupId": str(root["groupId"]),
                "phase": str(root["phase"]),
                "sideToMove": str(root["sideToMove"]).lower(),
                "selectionRank": str(root["selectionRank"]).lower(),
                "distinctLegalChildren": len(items),
                "sourcePvOccurrences": pv_occurrences,
                "structurallyFeasible": not reasons,
                "retained": False,
                "feasibilityOrdinal": None,
                "rejectionReasons": reasons,
                "rawLeakageComponentId": components[str(root["groupId"])],
            }
        )
    return feasibility, by_root


def _computed_child_coverage(
    roots: Sequence[dict[str, Any]],
    children_by_root: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    root_ids = {str(root["rootId"]) for root in roots}
    if set(children_by_root) != root_ids:
        raise ValueError("child coverage does not enumerate every input root")
    counts = [len(children_by_root[root_id]) for root_id in sorted(root_ids)]
    phase_counts = Counter(str(root["phase"]) for root in roots)
    side_counts = Counter(str(root["sideToMove"]).lower() for root in roots)
    return {
        "roots": len(roots),
        "children": sum(counts),
        "zeroChildRoots": sum(count == 0 for count in counts),
        "minimumChildrenPerRoot": min(counts),
        "maximumChildrenPerRoot": max(counts),
        "phaseCounts": {phase: phase_counts[phase] for phase in PHASES},
        "sideToMoveCounts": {side: side_counts[side] for side in ("w", "b")},
        "uniqueRootIds": len(root_ids),
        "uniqueChildIds": sum(counts),
    }


def _retain_feasible_roots(
    roots: Sequence[dict[str, Any]],
    children_by_root: dict[str, list[dict[str, Any]]],
    feasibility: Sequence[dict[str, Any]],
    *,
    retain_per_phase_side: int = FEASIBLE_ROOTS_PER_PHASE_SIDE,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Retain each bucket independently using only its frozen root rank."""

    root_by_id = {str(root["rootId"]): root for root in roots}
    row_by_id = {str(row["rootId"]): dict(row) for row in feasibility}
    if set(root_by_id) != set(row_by_id):
        raise ValueError("feasibility inventory does not match raw roots")
    retained_ids: set[str] = set()
    ranked_buckets = _ranked_feasible_buckets(
        row_by_id.values(), required_per_bucket=retain_per_phase_side
    )
    for (phase, side), candidates in ranked_buckets.items():
        for ordinal, row in enumerate(candidates[:retain_per_phase_side], 1):
            row["retained"] = True
            row["feasibilityOrdinal"] = ordinal
            retained_ids.add(str(row["rootId"]))

    filtered_roots: list[dict[str, Any]] = []
    filtered_children: list[dict[str, Any]] = []
    for root_id in sorted(
        retained_ids,
        key=lambda value: (
            PHASES.index(str(root_by_id[value]["phase"])),
            str(root_by_id[value]["sideToMove"]),
            str(root_by_id[value]["selectionRank"]),
            value,
        ),
    ):
        root = root_by_id[root_id]
        component = str(row_by_id[root_id]["rawLeakageComponentId"])
        filtered_roots.append(
            {
                **root,
                "rawCandidateRole": root.get("candidateRole"),
                "candidateRole": "primary",
                "rawLeakageComponentId": component,
            }
        )
        filtered_children.extend(
            {
                **child,
                "rawCandidateRole": child.get("candidateRole"),
                "candidateRole": "primary",
                "rawLeakageComponentId": component,
            }
            for child in sorted(
                children_by_root[root_id], key=lambda item: str(item.get("move") or "")
            )
        )
    rows = sorted(
        row_by_id.values(),
        key=lambda row: (
            PHASES.index(str(row["phase"])),
            str(row["sideToMove"]),
            str(row["selectionRank"]),
            str(row["rootId"]),
        ),
    )
    return filtered_roots, filtered_children, rows


def _ranked_feasible_buckets(
    rows: Iterable[dict[str, Any]], *, required_per_bucket: int
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Validate independent bucket capacity and return frozen-rank ordering."""

    if required_per_bucket <= 0:
        raise ValueError("required bucket capacity must be positive")
    materialized = list(rows)
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for phase in PHASES:
        for side in ("w", "b"):
            candidates = sorted(
                (
                    row
                    for row in materialized
                    if row.get("phase") == phase
                    and row.get("sideToMove") == side
                    and row.get("structurallyFeasible") is True
                ),
                key=lambda row: (str(row.get("selectionRank") or ""), str(row.get("rootId") or "")),
            )
            if len(candidates) < required_per_bucket:
                raise ValueError(
                    f"{phase}/{side}: only {len(candidates)}/{required_per_bucket} "
                    "structurally feasible raw roots; cross-bucket borrowing is forbidden"
                )
            result[(phase, side)] = candidates
    return result


def _item_id(item: dict[str, Any]) -> str:
    value = item.get("childId")
    if not isinstance(value, str) or not value:
        raise ValueError("search item lacks childId")
    _normalized_ofen(item.get("childOfen"))
    return value


def _load_unique_items(path: Path) -> list[dict[str, Any]]:
    items = [record for _, record in _jsonl(path)]
    ids = [_item_id(item) for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate child IDs in {path}")
    return items


def _load_json_snapshot(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    before = _identity(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    after = _identity(path)
    if before != after:
        raise ValueError(f"input changed while being read: {path}")
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value, after


def _source_audit_identity_hashes(
    root_manifest: dict[str, Any],
) -> tuple[set[str], set[str], set[str]]:
    """Reauthenticate and split current source data from reusable infrastructure."""

    sources = root_manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("raw root manifest has no source audit inventory")
    data_hashes: set[str] = set()
    infrastructure_hashes: set[str] = set()
    launch_provenance_hashes: set[str] = set()
    expected_fields = set(SOURCE_AUDIT_INPUT_IDENTITY_FIELDS)
    for source_index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"raw root source audit {source_index} is malformed")
        unexpected_identity_fields = {
            name
            for name, value in source.items()
            if name not in expected_fields
            and isinstance(value, dict)
            and {"path", "bytes", "sha256"}.issubset(value)
        }
        if unexpected_identity_fields:
            raise ValueError(
                f"raw root source audit {source_index} has unregistered input "
                f"identities: {sorted(unexpected_identity_fields)}"
            )
        for name in SOURCE_AUDIT_INPUT_IDENTITY_FIELDS:
            identity = _verify_identity(
                source.get(name), f"raw root source audit {source_index}.{name}"
            )
            if name in SOURCE_DATA_INPUT_IDENTITY_FIELDS:
                bucket = data_hashes
            elif name in REUSABLE_SOURCE_INFRASTRUCTURE_IDENTITY_FIELDS:
                bucket = infrastructure_hashes
            else:
                bucket = launch_provenance_hashes
            bucket.add(identity["sha256"])
    return data_hashes, infrastructure_hashes, launch_provenance_hashes


def _assert_raw_prior_disjoint(
    roots: Sequence[dict[str, Any]],
    children: Sequence[dict[str, Any]],
    root_manifest: dict[str, Any],
    manifest_paths: Sequence[str | Path],
    *,
    enforce_canonical_source_inventory: bool = True,
    _self_test_prior_audit: dict[str, Any] | None = None,
) -> tuple[dict[str, set[str]], list[dict[str, Any]], dict[str, int]]:
    """Authenticate prior catalogs and abort on any raw-graph reuse."""

    forbidden, pins = _load_forbidden_catalogs(
        [_resolve(path) for path in manifest_paths],
        enforce_canonical_source_inventory=enforce_canonical_source_inventory,
        _self_test_prior_audit=_self_test_prior_audit,
    )
    source_data_input_hashes, _, _ = _source_audit_identity_hashes(root_manifest)
    counters = {
        "sourceDataInputCollisions": len(
            source_data_input_hashes & forbidden["sourceDataInputSha256"]
        ),
        "sourceGameCollisions": 0,
        "sourceRunCollisions": 0,
        "exactPositionCollisions": 0,
        "conservativeOrbitCollisions": 0,
    }
    for root in roots:
        counters["sourceGameCollisions"] += int(
            str(root.get("sourceGameId") or "") in forbidden["sourceGameIds"]
        )
        counters["sourceRunCollisions"] += int(
            str(root.get("sourceRunId") or "") in forbidden["sourceRunIds"]
        )
    for item in [*roots, *children]:
        ofen = item.get("ofen") if "ofen" in item else item.get("childOfen")
        exact, _, signatures = deep._leakage_keys(_normalized_ofen(ofen))
        counters["exactPositionCollisions"] += int(exact in forbidden["exact"])
        counters["conservativeOrbitCollisions"] += int(
            bool(set(signatures) & forbidden["signatures"])
        )
    if any(counters.values()):
        raise ValueError(f"raw graph collides with prior data: {counters}")
    return forbidden, pins, counters


def _feasibility_freeze(args: argparse.Namespace) -> None:
    """Publish the audited raw-to-feasible boundary without reading targets."""

    raw_roots_path = _resolve(args.raw_roots)
    raw_root_manifest_path = _resolve(args.raw_roots_manifest)
    raw_children_path = _resolve(args.raw_children)
    raw_child_manifest_path = _resolve(args.raw_children_manifest)
    raw_sampler_seal_path = _resolve(args.raw_sampler_seal)
    feasibility_path = _resolve(args.root_feasibility)
    feasibility_manifest_path = Path(str(feasibility_path) + ".manifest.json")
    feasibility_seal_path = _resolve(args.root_feasibility_seal)
    roots_path = _resolve(args.roots)
    root_manifest_path = Path(str(roots_path) + ".manifest.json")
    children_path = _resolve(args.children)
    child_manifest_path = Path(str(children_path) + ".manifest.json")
    sampler_seal_path = Path(str(children_path) + ".complete.seal.json")
    canonical_names = {
        raw_roots_path: "raw-roots.jsonl",
        raw_root_manifest_path: "raw-roots.jsonl.manifest.json",
        raw_children_path: "raw-children.jsonl",
        raw_child_manifest_path: "raw-children.jsonl.manifest.json",
        raw_sampler_seal_path: "raw-children.jsonl.complete.seal.json",
        feasibility_path: "root-feasibility.jsonl",
        feasibility_manifest_path: "root-feasibility.jsonl.manifest.json",
        feasibility_seal_path: "root-feasibility.seal.json",
        roots_path: "roots.jsonl",
        root_manifest_path: "roots.jsonl.manifest.json",
        children_path: "children.jsonl",
        child_manifest_path: "children.jsonl.manifest.json",
        sampler_seal_path: "children.jsonl.complete.seal.json",
    }
    if any(path.name != name for path, name in canonical_names.items()):
        raise ValueError("raw/feasible artifacts must use the frozen canonical filenames")
    outputs = (
        feasibility_path,
        feasibility_manifest_path,
        feasibility_seal_path,
        roots_path,
        root_manifest_path,
        children_path,
        child_manifest_path,
        sampler_seal_path,
    )
    if any(path.exists() for path in outputs):
        raise FileExistsError("a root-feasibility output already exists")

    roots = [record for _, record in _jsonl(raw_roots_path)]
    children = _load_unique_items(raw_children_path)
    root_manifest, root_manifest_identity = _load_json_snapshot(
        raw_root_manifest_path
    )
    child_manifest, child_manifest_identity = _load_json_snapshot(
        raw_child_manifest_path
    )
    sampler_seal, sampler_seal_identity = _load_json_snapshot(raw_sampler_seal_path)
    raw_identities = {
        "rawRoots": _identity(raw_roots_path),
        "rawRootsManifest": root_manifest_identity,
        "rawChildren": _identity(raw_children_path),
        "rawChildrenManifest": child_manifest_identity,
        "rawSamplerCompletionSeal": sampler_seal_identity,
    }
    if (
        root_manifest.get("kind") != "omega-decision-root-manifest"
        or root_manifest.get("profileId") != PROFILE_ID
        or root_manifest.get("output") != raw_identities["rawRoots"]
        or int(root_manifest.get("policy", {}).get("sourceSeed", -1)) != SOURCE_SEED
        or int(root_manifest.get("policy", {}).get("seed", -1))
        != ROOT_SELECTION_SEED
        or root_manifest.get("finalStageSeal") is not True
    ):
        raise ValueError("raw root manifest violates the G5 source contract")
    raw_coverage = root_manifest.get("coverage", {})
    if (
        int(raw_coverage.get("records", -1)) != len(roots)
        or len(roots) != RAW_ROOTS_PER_PHASE_SIDE * len(PHASES) * 2
    ):
        raise ValueError("raw root manifest inventory is incomplete")
    if (
        child_manifest.get("kind") != "omega-decision-sampler-manifest"
        or child_manifest.get("input") != raw_identities["rawRoots"]
        or child_manifest.get("output") != raw_identities["rawChildren"]
        or child_manifest.get("finalStageSeal") is not False
    ):
        raise ValueError("raw child manifest does not pin the raw root graph")
    child_coverage = child_manifest.get("coverage", {})
    if (
        int(child_coverage.get("roots", -1)) != len(roots)
        or int(child_coverage.get("uniqueRootIds", -1)) != len(roots)
        or int(child_coverage.get("children", -1)) != len(children)
        or int(child_coverage.get("uniqueChildIds", -1)) != len(children)
    ):
        raise ValueError("raw legal-child completion coverage is inconsistent")
    if (
        sampler_seal.get("kind") != "omega-decision-sampler-completion-seal"
        or sampler_seal.get("input") != raw_identities["rawRoots"]
        or sampler_seal.get("output") != raw_identities["rawChildren"]
        or sampler_seal.get("manifest") != raw_identities["rawChildrenManifest"]
        or sampler_seal.get("finalStageSeal") is not True
    ):
        raise ValueError("raw sampler completion seal is invalid")

    _, forbidden_pins, prior_reuse_counts = _assert_raw_prior_disjoint(
        roots,
        children,
        root_manifest,
        args.forbidden_position_manifest,
    )

    feasibility, children_by_root = _audit_raw_graph(roots, children)
    if child_coverage != _computed_child_coverage(roots, children_by_root):
        raise ValueError("raw child manifest coverage does not reproduce from rows")
    filtered_roots, filtered_children, feasibility = _retain_feasible_roots(
        roots, children_by_root, feasibility
    )
    produced: list[Path] = []
    try:
        _atomic_jsonl(feasibility_path, feasibility)
        produced.append(feasibility_path)
        _atomic_jsonl(roots_path, filtered_roots)
        produced.append(roots_path)
        _atomic_jsonl(children_path, filtered_children)
        produced.append(children_path)

        phase_counts = Counter(str(root["phase"]) for root in filtered_roots)
        phase_side_counts = Counter(_bucket_key(root) for root in filtered_roots)
        filtered_root_manifest = {
            **root_manifest,
            "createdUtc": _utc_now(),
            "policy": {
                **dict(root_manifest.get("policy", {})),
                "rootsPerPhase": FEASIBLE_ROOTS_PER_PHASE_SIDE * 2,
                "reservePerPhase": 0,
                "primaryPerPhaseAndSide": FEASIBLE_ROOTS_PER_PHASE_SIDE,
                "reservePerPhaseAndSide": 0,
                "selection": "target-blind frozen root rank after structural feasibility",
                "rawRootsPerPhaseAndSide": RAW_ROOTS_PER_PHASE_SIDE,
                "minimumDistinctLegalChildren": 5,
                "sourcePvOccurrencesRequired": 1,
            },
            "coverage": {
                "records": len(filtered_roots),
                "phaseCounts": {
                    phase: phase_counts[phase] for phase in PHASES
                },
                "phaseSideCounts": {
                    f"{phase}/{side}": phase_side_counts[(phase, side)]
                    for phase in PHASES
                    for side in ("w", "b")
                },
                "primaryPhaseSideCounts": {
                    f"{phase}/{side}": phase_side_counts[(phase, side)]
                    for phase in PHASES
                    for side in ("w", "b")
                },
                "primary": len(filtered_roots),
                "reserve": 0,
                "uniqueRootIds": len({root["rootId"] for root in filtered_roots}),
                "sourceGroups": len({root["groupId"] for root in filtered_roots}),
                "oneRootPerSourceGroup": True,
                "collapsedEquivalentAbBaRecords": int(
                    raw_coverage.get("collapsedEquivalentAbBaRecords", 0)
                ),
            },
            "producer": _producer_identity(),
            "finalStageSeal": True,
            "output": _identity(roots_path),
        }
        _atomic_json(root_manifest_path, filtered_root_manifest)
        produced.append(root_manifest_path)

        filtered_children_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for child in filtered_children:
            filtered_children_by_root[str(child["rootId"])].append(child)
        filtered_child_coverage = _computed_child_coverage(
            filtered_roots, filtered_children_by_root
        )
        filtered_child_manifest = {
            **child_manifest,
            "createdUtc": _utc_now(),
            "policy": {
                **dict(child_manifest.get("policy", {})),
                "derivation": "exact child subset selected by root-feasibility-v2",
            },
            "coverage": filtered_child_coverage,
            "input": _identity(roots_path),
            "output": _identity(children_path),
            "finalStageSeal": False,
        }
        _atomic_json(child_manifest_path, filtered_child_manifest)
        produced.append(child_manifest_path)
        filtered_sampler_seal = {
            **sampler_seal,
            "createdUtc": _utc_now(),
            "input": _identity(roots_path),
            "output": _identity(children_path),
            "manifest": _identity(child_manifest_path),
            "finalStageSeal": True,
        }
        _atomic_json(sampler_seal_path, filtered_sampler_seal)
        produced.append(sampler_seal_path)

        feasibility_counts = Counter(
            (str(row["phase"]), str(row["sideToMove"]), bool(row["structurallyFeasible"]))
            for row in feasibility
        )
        feasibility_manifest = {
            "schemaVersion": 1,
            "kind": "omega-decision-root-feasibility-manifest",
            "createdUtc": _utc_now(),
            "profileId": PROFILE_ID,
            "policy": {
                "sourceSeed": SOURCE_SEED,
                "rootSelectionSeed": ROOT_SELECTION_SEED,
                "rankDomain": "omega-g5-feasible-root-v1",
                "rawRootsPerPhaseAndSide": RAW_ROOTS_PER_PHASE_SIDE,
                "retainedRootsPerPhaseAndSide": FEASIBLE_ROOTS_PER_PHASE_SIDE,
                "minimumDistinctLegalChildren": 5,
                "sourcePvOccurrencesRequired": 1,
                "targetInformationRead": False,
                "crossBucketBorrowing": False,
                "rawGraphComponentsBuiltBeforeFiltering": True,
            },
            "coverage": {
                "rawRoots": len(roots),
                "rawChildren": len(children),
                "structurallyFeasibleRoots": sum(
                    row["structurallyFeasible"] is True for row in feasibility
                ),
                "retainedRoots": len(filtered_roots),
                "retainedChildren": len(filtered_children),
                "rawComponents": len(
                    {row["rawLeakageComponentId"] for row in feasibility}
                ),
                "phaseSide": {
                    f"{phase}/{side}": {
                        "raw": RAW_ROOTS_PER_PHASE_SIDE,
                        "feasible": feasibility_counts[(phase, side, True)],
                        "retained": FEASIBLE_ROOTS_PER_PHASE_SIDE,
                    }
                    for phase in PHASES
                    for side in ("w", "b")
                },
                "rejectionReasons": dict(
                    Counter(
                        reason
                        for row in feasibility
                        for reason in row["rejectionReasons"]
                    )
                ),
            },
            "inputs": raw_identities,
            "priorReuse": {
                "forbiddenCatalogs": forbidden_pins,
                "collisionCounts": prior_reuse_counts,
            },
            "producer": _producer_identity(),
            "finalStageSeal": False,
            "output": _identity(feasibility_path),
        }
        _atomic_json(feasibility_manifest_path, feasibility_manifest)
        produced.append(feasibility_manifest_path)
        feasibility_seal = {
            "schemaVersion": 1,
            "kind": "omega-decision-root-feasibility-seal",
            "profileId": PROFILE_ID,
            "status": "target-opaque-raw-graph-authenticated-and-feasibility-frozen",
            "createdUtc": _utc_now(),
            "declaration": {
                "teacherSearchesPresentAtFreeze": False,
                "targetOrScoreFieldsDecoded": 0,
                "targetOrScoreFieldsEmitted": 0,
                "rawGraphAuthenticatedBeforeFiltering": True,
                "rawComponentsInheritedByFilteredRows": True,
            },
            "forbiddenCatalogs": forbidden_pins,
            "priorReuseCollisionCounts": prior_reuse_counts,
            "identities": {
                **raw_identities,
                "rootFeasibility": _identity(feasibility_path),
                "rootFeasibilityManifest": _identity(feasibility_manifest_path),
                "roots": _identity(roots_path),
                "rootsManifest": _identity(root_manifest_path),
                "children": _identity(children_path),
                "childrenManifest": _identity(child_manifest_path),
                "samplerCompletionSeal": _identity(sampler_seal_path),
            },
            "producer": _producer_identity(),
            "finalStageSeal": True,
        }
        _atomic_json(feasibility_seal_path, feasibility_seal)
        produced.append(feasibility_seal_path)
    except BaseException:
        for path in reversed(produced):
            path.unlink(missing_ok=True)
        raise
    if any(_identity(path) != identity for path, identity in (
        (raw_roots_path, raw_identities["rawRoots"]),
        (raw_root_manifest_path, raw_identities["rawRootsManifest"]),
        (raw_children_path, raw_identities["rawChildren"]),
        (raw_child_manifest_path, raw_identities["rawChildrenManifest"]),
        (raw_sampler_seal_path, raw_identities["rawSamplerCompletionSeal"]),
    )):
        for path in reversed(produced):
            path.unlink(missing_ok=True)
        raise ValueError("raw graph changed during feasibility publication")
    print(
        f"Retained {len(filtered_roots)} structurally feasible roots and "
        f"{len(filtered_children)} children: {feasibility_seal_path}"
    )


def _verify_feasibility_bundle(
    prereg: dict[str, Any], args: argparse.Namespace
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, set[str]],
    list[dict[str, Any]],
]:
    """Reauthenticate the raw graph and exact filtered derivation at prelabel."""

    paths = {
        "rawRoots": _resolve(args.raw_roots),
        "rawRootsManifest": _resolve(args.raw_roots_manifest),
        "rawChildren": _resolve(args.raw_children),
        "rawChildrenManifest": _resolve(args.raw_children_manifest),
        "rawSamplerCompletionSeal": _resolve(args.raw_sampler_seal),
        "rootFeasibility": _resolve(args.root_feasibility),
        "rootFeasibilityManifest": _resolve(args.root_feasibility_manifest),
        "rootFeasibilitySeal": _resolve(args.root_feasibility_seal),
        "roots": _resolve(args.roots),
        "rootsManifest": _resolve(args.roots_manifest),
        "children": _resolve(args.children),
        "childrenManifest": _resolve(args.children_manifest),
        "samplerCompletionSeal": _resolve(args.sampler_seal),
    }
    for name, path in paths.items():
        if _identity(path) != _frozen_identity(prereg, name):
            raise ValueError(f"pre-label {name} differs from finalFreezeIdentities")

    raw_roots = [record for _, record in _jsonl(paths["rawRoots"])]
    raw_children = _load_unique_items(paths["rawChildren"])
    raw_root_manifest, _ = _load_json_snapshot(paths["rawRootsManifest"])
    raw_child_manifest, _ = _load_json_snapshot(paths["rawChildrenManifest"])
    raw_sampler_seal, _ = _load_json_snapshot(paths["rawSamplerCompletionSeal"])
    if (
        raw_root_manifest.get("output") != _identity(paths["rawRoots"])
        or raw_root_manifest.get("profileId") != PROFILE_ID
        or raw_root_manifest.get("finalStageSeal") is not True
        or raw_child_manifest.get("input") != _identity(paths["rawRoots"])
        or raw_child_manifest.get("output") != _identity(paths["rawChildren"])
        or raw_sampler_seal.get("input") != _identity(paths["rawRoots"])
        or raw_sampler_seal.get("output") != _identity(paths["rawChildren"])
        or raw_sampler_seal.get("manifest") != _identity(paths["rawChildrenManifest"])
        or raw_sampler_seal.get("finalStageSeal") is not True
    ):
        raise ValueError("pre-label raw graph envelopes are not a complete expansion")
    raw_coverage = raw_child_manifest.get("coverage", {})
    if (
        int(raw_coverage.get("roots", -1)) != len(raw_roots)
        or int(raw_coverage.get("uniqueRootIds", -1)) != len(raw_roots)
        or int(raw_coverage.get("children", -1)) != len(raw_children)
        or int(raw_coverage.get("uniqueChildIds", -1)) != len(raw_children)
    ):
        raise ValueError("pre-label raw expansion coverage is incomplete")
    forbidden, forbidden_pins, prior_reuse_counts = _assert_raw_prior_disjoint(
        raw_roots,
        raw_children,
        raw_root_manifest,
        args.forbidden_position_manifest,
    )
    if [pin["manifest"] for pin in forbidden_pins] != _frozen_identity_list(
        prereg, "forbiddenPositionCatalogManifests"
    ):
        raise ValueError("feasibility prior catalogs differ from finalFreezeIdentities")

    expected_feasibility, raw_children_by_root = _audit_raw_graph(
        raw_roots, raw_children
    )
    if raw_coverage != _computed_child_coverage(raw_roots, raw_children_by_root):
        raise ValueError("pre-label raw child coverage does not reproduce from rows")
    expected_roots, expected_children, expected_feasibility = _retain_feasible_roots(
        raw_roots, raw_children_by_root, expected_feasibility
    )
    actual_feasibility = [
        record for _, record in _jsonl(paths["rootFeasibility"])
    ]
    actual_roots = [record for _, record in _jsonl(paths["roots"])]
    actual_children = _load_unique_items(paths["children"])
    if actual_feasibility != expected_feasibility:
        raise ValueError("root-feasibility rows do not reproduce from the raw graph")
    if actual_roots != expected_roots or actual_children != expected_children:
        raise ValueError("filtered roots/children are not the exact frozen-rank derivation")

    feasibility_manifest, _ = _load_json_snapshot(paths["rootFeasibilityManifest"])
    feasibility_seal, _ = _load_json_snapshot(paths["rootFeasibilitySeal"])
    _strict_keys(
        feasibility_manifest,
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "profileId",
            "policy",
            "coverage",
            "inputs",
            "priorReuse",
            "producer",
            "finalStageSeal",
            "output",
        },
        "root-feasibility manifest",
    )
    _strict_keys(
        feasibility_seal,
        {
            "schemaVersion",
            "kind",
            "profileId",
            "status",
            "createdUtc",
            "declaration",
            "forbiddenCatalogs",
            "priorReuseCollisionCounts",
            "identities",
            "producer",
            "finalStageSeal",
        },
        "root-feasibility seal",
    )
    if _has_target_like_key(feasibility_manifest) or _has_target_like_key(
        feasibility_seal
    ):
        raise ValueError("root-feasibility envelopes contain a target/score-like key")
    expected_inputs = {
        name: _identity(paths[name])
        for name in (
            "rawRoots",
            "rawRootsManifest",
            "rawChildren",
            "rawChildrenManifest",
            "rawSamplerCompletionSeal",
        )
    }
    expected_outputs = {
        name: _identity(paths[name])
        for name in (
            "rootFeasibility",
            "rootFeasibilityManifest",
            "roots",
            "rootsManifest",
            "children",
            "childrenManifest",
            "samplerCompletionSeal",
        )
    }
    feasibility_policy = feasibility_manifest.get("policy", {})
    feasibility_coverage = feasibility_manifest.get("coverage", {})
    feasibility_declaration = feasibility_seal.get("declaration", {})
    if (
        feasibility_manifest.get("schemaVersion") != 1
        or feasibility_manifest.get("kind")
        != "omega-decision-root-feasibility-manifest"
        or feasibility_manifest.get("profileId") != PROFILE_ID
        or feasibility_manifest.get("inputs") != expected_inputs
        or feasibility_manifest.get("priorReuse")
        != {
            "forbiddenCatalogs": forbidden_pins,
            "collisionCounts": prior_reuse_counts,
        }
        or feasibility_manifest.get("output")
        != expected_outputs["rootFeasibility"]
        or feasibility_manifest.get("finalStageSeal") is not False
        or feasibility_policy.get("sourceSeed") != SOURCE_SEED
        or feasibility_policy.get("rootSelectionSeed") != ROOT_SELECTION_SEED
        or feasibility_policy.get("rankDomain") != "omega-g5-feasible-root-v1"
        or feasibility_policy.get("rawRootsPerPhaseAndSide")
        != RAW_ROOTS_PER_PHASE_SIDE
        or feasibility_policy.get("retainedRootsPerPhaseAndSide")
        != FEASIBLE_ROOTS_PER_PHASE_SIDE
        or feasibility_policy.get("minimumDistinctLegalChildren") != 5
        or feasibility_policy.get("sourcePvOccurrencesRequired") != 1
        or feasibility_policy.get("targetInformationRead") is not False
        or feasibility_policy.get("crossBucketBorrowing") is not False
        or feasibility_policy.get("rawGraphComponentsBuiltBeforeFiltering")
        is not True
        or int(feasibility_coverage.get("rawRoots", -1)) != len(raw_roots)
        or int(feasibility_coverage.get("rawChildren", -1)) != len(raw_children)
        or int(feasibility_coverage.get("retainedRoots", -1)) != len(actual_roots)
        or int(feasibility_coverage.get("retainedChildren", -1))
        != len(actual_children)
        or feasibility_seal.get("schemaVersion") != 1
        or feasibility_seal.get("kind") != "omega-decision-root-feasibility-seal"
        or feasibility_seal.get("profileId") != PROFILE_ID
        or feasibility_seal.get("status")
        != "target-opaque-raw-graph-authenticated-and-feasibility-frozen"
        or feasibility_seal.get("forbiddenCatalogs") != forbidden_pins
        or feasibility_seal.get("priorReuseCollisionCounts")
        != prior_reuse_counts
        or feasibility_declaration
        != {
            "teacherSearchesPresentAtFreeze": False,
            "targetOrScoreFieldsDecoded": 0,
            "targetOrScoreFieldsEmitted": 0,
            "rawGraphAuthenticatedBeforeFiltering": True,
            "rawComponentsInheritedByFilteredRows": True,
        }
        or feasibility_seal.get("identities")
        != {**expected_inputs, **expected_outputs}
        or feasibility_seal.get("finalStageSeal") is not True
    ):
        raise ValueError("root-feasibility manifest/seal binding is invalid")
    _verify_producer_identity(
        feasibility_manifest.get("producer"), "root-feasibility manifest producer"
    )
    _verify_producer_identity(
        feasibility_seal.get("producer"), "root-feasibility seal producer"
    )
    return raw_roots, raw_children, forbidden, forbidden_pins


def _has_target_like_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                str(key) not in TARGET_OPAQUE_DECLARATION_KEYS
                and _is_target_like_key(key)
            ) or _has_target_like_key(item):
                return True
    elif isinstance(value, list):
        return any(_has_target_like_key(item) for item in value)
    return False


def _forbidden_extraction_policy() -> dict[str, Any]:
    """Return the closed, byte-stable policy for prior-position extraction."""

    return {
        "policyId": "omega-target-opaque-lexical-ofen-v1",
        "sourceSelection": "explicit-regular-files-only",
        "supportedSuffixes": list(FORBIDDEN_SOURCE_SUFFIXES),
        "decodedStringFieldNames": sorted(
            FORBIDDEN_OFEN_FIELD_NAMES
            | FORBIDDEN_GAME_ID_FIELD_NAMES
            | FORBIDDEN_RUN_ID_FIELD_NAMES
        ),
        "sensitiveFieldPolicy": "skip-complete-value-lexeme-without-decoding",
        "unknownScalarPolicy": "skip-value-lexeme-without-decoding",
        "nestedContainerPolicy": "recurse-except-sensitive-fields",
        "generation5NamespacePolicy": "reject-source-path-before-open",
        "positionIdPolicy": "sha256-of-ofen-and-source-provenance",
        "sourceProjectionPolicy": (
            "recognized-G3-projection-requires-strict-transitive-manifest"
        ),
        "sourceDataDisjointnessPolicy": "omega-source-data-input-disjointness-v1",
        "sourceDataInputIdentityFields": list(SOURCE_DATA_INPUT_IDENTITY_FIELDS),
        "reusableInfrastructureIdentityFields": list(
            REUSABLE_SOURCE_INFRASTRUCTURE_IDENTITY_FIELDS
        ),
        "launchProvenanceIdentityFields": list(
            SOURCE_AUDIT_LAUNCH_PROVENANCE_IDENTITY_FIELDS
        ),
        "catalogSourceArtifactHashesExcludedFromDataCollisionSet": True,
        "legacyG3TransitiveArtifactHashesExcludedFromDataCollisionSet": True,
        "reusableInfrastructureExcludedFromDataCollisionSet": True,
    }


def _catalog_normalized_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _catalog_sensitive_field_name(value: str) -> bool:
    return _is_target_like_key(value)


def _assert_prior_catalog_source(path: str | Path) -> Path:
    """Reject all known G5 namespaces before opening an explicit prior source."""

    path = _resolve(path)
    normalized = "/" + path.as_posix().lower().lstrip("/")
    components = {_catalog_normalized_field_name(part) for part in path.parts}
    forbidden_components = {
        "omegadecisionv2",
        "kingstatev5",
        "kingstatev5development",
        "buildkingstatev5",
    }
    if (
        any(marker in normalized for marker in FORBIDDEN_G5_PATH_MARKERS)
        or components & forbidden_components
        or path.name.lower().startswith("omega-nnue-king-state-v5-")
    ):
        raise ValueError(f"generation-5 namespace is forbidden as prior input: {path}")
    if not path.is_file():
        raise FileNotFoundError(path)
    if not path.name.lower().endswith(FORBIDDEN_SOURCE_SUFFIXES):
        raise ValueError(f"unsupported prior-artifact source suffix: {path}")
    return path


def _decode_catalog_string(raw: str, description: str) -> str:
    raw = raw.strip()
    if not raw.startswith('"') or _scan_json_string(raw, 0) != len(raw):
        raise ValueError(f"{description} must be a JSON string")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{description} is not a valid JSON string") from error
    if not isinstance(value, str) or not value:
        raise ValueError(f"{description} must be a nonempty JSON string")
    return value


@dataclass
class _CatalogLexicalNode:
    observations: list[dict[str, str]] = field(default_factory=list)
    game_ids: set[str] = field(default_factory=set)
    run_ids: set[str] = field(default_factory=set)


def _extract_catalog_node(raw: str, description: str) -> _CatalogLexicalNode:
    """Select strings from JSON while never decoding an unallowlisted value.

    Object keys must be decoded to decide whether a value is allowlisted.  A
    target/score-like key causes its complete value lexeme to be ignored,
    including nested containers.  Other containers are visited structurally;
    unknown scalar and string values are never passed to ``json.loads``.
    """

    raw = raw.strip()
    if not raw:
        raise ValueError(f"{description}: empty JSON value")
    if raw[0] == "[":
        node = _CatalogLexicalNode()
        for index, item in enumerate(_json_array_value_slices(raw)):
            child = _extract_catalog_node(item, f"{description}[{index}]")
            node.observations.extend(child.observations)
            node.game_ids.update(child.game_ids)
            node.run_ids.update(child.run_ids)
        return node
    if raw[0] != "{":
        # The parent scanner already bounded this primitive.  It is deliberately
        # not decoded because it is not an allowlisted string field.
        return _CatalogLexicalNode()

    spans = _top_level_json_spans(raw)
    direct_ofens: list[str] = []
    direct_game_ids: set[str] = set()
    direct_run_ids: set[str] = set()
    children: list[_CatalogLexicalNode] = []
    for lowered, (original, value_raw) in spans.items():
        normalized = _catalog_normalized_field_name(lowered)
        field_description = f"{description}.{original}"
        if normalized in FORBIDDEN_OFEN_FIELD_NAMES:
            direct_ofens.append(
                _normalized_ofen(_decode_catalog_string(value_raw, field_description))
            )
            continue
        if normalized in FORBIDDEN_GAME_ID_FIELD_NAMES:
            direct_game_ids.add(_decode_catalog_string(value_raw, field_description))
            continue
        if normalized in FORBIDDEN_RUN_ID_FIELD_NAMES:
            direct_run_ids.add(_decode_catalog_string(value_raw, field_description))
            continue
        if _catalog_sensitive_field_name(original):
            continue
        stripped = value_raw.lstrip()
        if stripped.startswith(("{", "[")):
            children.append(_extract_catalog_node(value_raw, field_description))

    node = _CatalogLexicalNode(
        game_ids=set(direct_game_ids), run_ids=set(direct_run_ids)
    )
    for child in children:
        for observation in child.observations:
            enriched = dict(observation)
            if not enriched.get("sourceGameId") and len(direct_game_ids) == 1:
                enriched["sourceGameId"] = next(iter(direct_game_ids))
            if not enriched.get("sourceRunId") and len(direct_run_ids) == 1:
                enriched["sourceRunId"] = next(iter(direct_run_ids))
            node.observations.append(enriched)
        node.game_ids.update(child.game_ids)
        node.run_ids.update(child.run_ids)

    # A common prior format stores the position beside a nested provenance
    # object.  Use that nested context only when it is unambiguous.
    game_choices = direct_game_ids
    if not game_choices and len(node.game_ids) == 1:
        game_choices = set(node.game_ids)
    run_choices = direct_run_ids
    if not run_choices and len(node.run_ids) == 1:
        run_choices = set(node.run_ids)
    for ofen in direct_ofens:
        for game_id in sorted(game_choices or {""}):
            for run_id in sorted(run_choices or {""}):
                node.observations.append(
                    {
                        "ofen": ofen,
                        "sourceGameId": game_id,
                        "sourceRunId": run_id,
                    }
                )
    return node


def _extract_catalog_source(
    source: str | Path,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    source = _assert_prior_catalog_source(source)
    before = _identity(source)
    observations: list[dict[str, str]] = []
    if source.name.lower().endswith((".jsonl", ".ndjson")):
        with source.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                node = _extract_catalog_node(line, f"{source}:{line_number}")
                observations.extend(node.observations)
    else:
        text = source.read_text(encoding="utf-8")
        observations.extend(
            _extract_catalog_node(text, str(source)).observations
        )
    after = _identity(source)
    if before != after:
        raise ValueError(f"prior-artifact source changed while being scanned: {source}")
    if not observations:
        raise ValueError(f"prior-artifact source yielded no allowlisted Omega OFEN: {source}")
    for observation in observations:
        observation["sourceArtifactSha256"] = before["sha256"]
    return observations, before


def _catalog_position_id(row: dict[str, Any]) -> str:
    basis = {
        "ofen": str(row.get("ofen", "")),
        "sourceGameId": str(row.get("sourceGameId", "")),
        "sourceRunId": str(row.get("sourceRunId", "")),
        "sourceArtifactSha256": str(row.get("sourceArtifactSha256", "")).lower(),
    }
    return "prior-" + _canonical_digest(basis)


def _catalog_rows_from_observations(
    observations: Iterable[dict[str, str]],
) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for observation in observations:
        key = (
            _normalized_ofen(observation.get("ofen")),
            str(observation.get("sourceGameId", "")),
            str(observation.get("sourceRunId", "")),
            str(observation.get("sourceArtifactSha256", "")).lower(),
        )
        if not HEX_SHA256.fullmatch(key[3]):
            raise ValueError("catalog observation has no source content identity")
        unique[key] = {
            "ofen": key[0],
            "sourceGameId": key[1],
            "sourceRunId": key[2],
            "sourceArtifactSha256": key[3],
        }

    rows: list[dict[str, Any]] = []
    for _, observation in sorted(unique.items()):
        exact, orbit, signatures = deep._leakage_keys(observation["ofen"])
        row: dict[str, Any] = {
            "schemaVersion": 1,
            "kind": "omega-target-opaque-forbidden-position",
            "ofen": observation["ofen"],
            "exactPositionKey": exact,
            "conservativeOrbitKey": orbit,
            "conservativeOrbitSignatures": sorted(set(signatures)),
            "sourceArtifactSha256": observation["sourceArtifactSha256"],
        }
        if observation["sourceGameId"]:
            row["sourceGameId"] = observation["sourceGameId"]
        if observation["sourceRunId"]:
            row["sourceRunId"] = observation["sourceRunId"]
        row["positionId"] = _catalog_position_id(row)
        rows.append(row)
    rows.sort(key=lambda row: row["positionId"])
    return rows


def _verify_forbidden_source_inventory(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("forbidden manifest has no source inventory")
    verified: list[dict[str, Any]] = []
    paths: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
            raise ValueError(
                f"forbidden sourceInventory[{index}] is not a strict content identity"
            )
        # Apply the G5 namespace boundary to the declared path before hashing
        # or opening its content during identity verification.
        path = _assert_prior_catalog_source(str(item["path"]))
        identity = _verify_identity(item, f"forbidden sourceInventory[{index}]")
        canonical = str(path)
        if canonical in paths:
            raise ValueError("forbidden source inventory contains a duplicate path")
        paths.add(canonical)
        verified.append(identity)
    if verified != sorted(verified, key=lambda item: item["path"].casefold()):
        raise ValueError("forbidden source inventory is not canonically ordered")
    return verified


def _source_declares_g3_projection(path: Path) -> bool:
    """Read only the first row's structural kind string."""

    path = _resolve(path)
    if not path.name.lower().endswith((".jsonl", ".ndjson")):
        return False
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            spans = _top_level_json_spans(line)
            kind = spans.get("kind")
            if kind is None:
                return False
            return (
                _decode_catalog_string(
                    kind[1], f"{path}:{line_number}.kind"
                )
                == prior_projection.ROW_KIND
            )
    return False


def _verify_source_projection_manifests(
    values: Sequence[Path | dict[str, Any]],
    source_inventory: Sequence[dict[str, Any]],
) -> dict[str, list[Any]]:
    source_by_path = {
        str(_resolve(identity["path"])): identity for identity in source_inventory
    }
    pins: list[dict[str, Any]] = []
    covered_sources: set[str] = set()
    manifest_paths: set[str] = set()
    transitive_hashes: set[str] = set()
    historical_game_ids: set[str] = set()
    historical_run_ids: set[str] = set()
    for index, value in enumerate(values):
        if isinstance(value, dict):
            declared = _verify_identity(
                value, f"source projection manifest[{index}]"
            )
            path = Path(declared["path"])
        else:
            path = _resolve(value)
            declared = _identity(path)
        result = prior_projection.verify_projection_manifest(path)
        if result.get("manifest") != declared:
            raise ValueError("source projection manifest identity changed")
        projection = result.get("projection")
        if not isinstance(projection, dict):
            raise ValueError("source projection verifier returned no projection")
        source_hashes = result.get("sourceSha256")
        game_ids = result.get("historicalGameIds")
        run_ids = result.get("historicalRunIds")
        if (
            not isinstance(source_hashes, list)
            or any(
                not isinstance(item, str) or not HEX_SHA256.fullmatch(item)
                for item in source_hashes
            )
            or source_hashes != sorted(source_hashes)
            or result.get("sourceArtifactCount") != len(source_hashes)
        ):
            raise ValueError("source projection returned malformed transitive hashes")
        for label, identifiers in (
            ("game", game_ids),
            ("run", run_ids),
        ):
            if (
                not isinstance(identifiers, list)
                or identifiers != sorted(set(identifiers))
                or any(
                    not isinstance(item, str) or not item or len(item) > 4096
                    for item in identifiers
                )
            ):
                raise ValueError(
                    f"source projection returned malformed historical {label} IDs"
                )
        projection_path = str(_resolve(projection["path"]))
        if source_by_path.get(projection_path) != projection:
            raise ValueError(
                "source projection is absent from the forbidden source inventory"
            )
        manifest_key = str(_resolve(path))
        if manifest_key in manifest_paths or projection_path in covered_sources:
            raise ValueError("duplicate source projection manifest or projection")
        manifest_paths.add(manifest_key)
        covered_sources.add(projection_path)
        pins.append(declared)
        transitive_hashes.update(source_hashes)
        historical_game_ids.update(game_ids)
        historical_run_ids.update(run_ids)
    pins.sort(key=lambda item: item["path"].casefold())
    required = {
        path
        for path in source_by_path
        if _source_declares_g3_projection(Path(path))
    }
    if required != covered_sources:
        raise ValueError(
            "recognized G3 projection sources and strict manifests differ: "
            f"missing={sorted(required - covered_sources)}, "
            f"unexpected={sorted(covered_sources - required)}"
        )
    return {
        "manifestPins": pins,
        "projectionSources": sorted(covered_sources),
        "legacySourceArtifactSha256": sorted(transitive_hashes),
        "sourceGameIds": sorted(historical_game_ids),
        "sourceRunIds": sorted(historical_run_ids),
    }


def _verify_portable_identity(value: Any, description: str) -> dict[str, Any]:
    """Verify a repo-relative or absolute identity and return an absolute pin."""

    record = _strict_keys(value, {"path", "bytes", "sha256"}, description)
    raw_path = record.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"{description}.path is invalid")
    path = Path(raw_path)
    if not path.is_absolute():
        path = REPO / path
    actual = _identity(path)
    if (
        type(record.get("bytes")) is not int
        or record["bytes"] != actual["bytes"]
        or not isinstance(record.get("sha256"), str)
        or record["sha256"].lower() != actual["sha256"]
    ):
        raise ValueError(f"{description} content identity changed")
    return actual


def _prior_source_audit_from_closure(
    value: Path | dict[str, Any],
) -> tuple[dict[str, Any], list[Path]]:
    """Recompute the canonical G4 source audit and its position-source paths."""

    if isinstance(value, dict):
        declared = _verify_identity(value, "prior source-audit closure")
        closure_path = Path(declared["path"])
    else:
        closure_path = _resolve(value)
        declared = _identity(closure_path)
    if closure_path != GENERATION4_CLOSURE_PATH.resolve():
        raise ValueError("prior source audit must use the canonical G4 closure")
    verifier_path = Path(generation4_abort.__file__).resolve()
    verifier_before = _identity(verifier_path)
    subprocess.run(
        [
            str(_resolve(sys.executable)),
            "-B",
            str(verifier_path),
            "verify",
            "--output",
            str(closure_path),
        ],
        check=True,
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if _identity(verifier_path) != verifier_before:
        raise ValueError("G4 abort verifier changed during source-audit verification")
    verified = _identity(closure_path)
    if verified != declared:
        raise ValueError("G4 closure identity changed during source-audit verification")
    closure, stable_closure = _load_json_snapshot(closure_path)
    if stable_closure != declared:
        raise ValueError("G4 closure changed after full structural verification")
    identities = _strict_keys(
        closure.get("identities"),
        set(generation4_abort.IDENTITY_PATHS),
        "G4 closure identities",
    )

    roots_manifest_identity = _verify_portable_identity(
        identities.get("rootsManifest"), "G4 closure roots manifest"
    )
    root_manifest, stable_root_manifest = _load_json_snapshot(
        Path(roots_manifest_identity["path"])
    )
    if stable_root_manifest != roots_manifest_identity:
        raise ValueError("G4 roots manifest changed during source-audit verification")
    sources = root_manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict):
        raise ValueError("G4 roots manifest must contain exactly one source audit")
    source = sources[0]
    expected_fields = set(GENERATION4_SOURCE_AUDIT_IDENTITY_FIELDS)
    unexpected_identity_fields = {
        name
        for name, item in source.items()
        if name not in expected_fields
        and isinstance(item, dict)
        and {"path", "bytes", "sha256"}.issubset(item)
    }
    if unexpected_identity_fields:
        raise ValueError(
            "G4 source audit has unregistered identity fields: "
            f"{sorted(unexpected_identity_fields)}"
        )
    data_inputs = {
        name: _verify_identity(source.get(name), f"G4 source data input {name}")
        for name in SOURCE_DATA_INPUT_IDENTITY_FIELDS
    }
    infrastructure = {
        name: _verify_identity(source.get(name), f"G4 reusable infrastructure {name}")
        for name in REUSABLE_SOURCE_INFRASTRUCTURE_IDENTITY_FIELDS
    }
    for role, closure_name in GENERATION4_DATA_CLOSURE_NAMES.items():
        closure_identity = _verify_portable_identity(
            identities.get(closure_name), f"G4 closure {closure_name}"
        )
        if data_inputs[role] != closure_identity:
            raise ValueError(
                f"G4 source data input {role} differs from closure {closure_name}"
            )

    position_sources: list[Path] = []
    for closure_name in GENERATION4_POSITION_CLOSURE_NAMES:
        position_sources.append(
            Path(
                _verify_portable_identity(
                    identities.get(closure_name), f"G4 position source {closure_name}"
                )["path"]
            )
        )
    evidence = {
        "closure": declared,
        "rootsManifest": roots_manifest_identity,
        "dataInputs": data_inputs,
        "dataInputsSha256": _canonical_digest(data_inputs),
        "reusableInfrastructure": infrastructure,
        "reusableInfrastructureSha256": _canonical_digest(infrastructure),
    }
    return evidence, position_sources


def _verify_prior_source_audits(
    values: Any,
) -> tuple[list[dict[str, Any]], set[str], set[str], list[Path]]:
    if not isinstance(values, list) or len(values) != 1:
        raise ValueError("exactly one canonical prior source audit is required")
    evidence, position_sources = _prior_source_audit_from_closure(
        _strict_keys(
            values[0],
            {
                "closure",
                "rootsManifest",
                "dataInputs",
                "dataInputsSha256",
                "reusableInfrastructure",
                "reusableInfrastructureSha256",
            },
            "prior source audit[0]",
        ).get("closure")
    )
    if evidence != values[0]:
        raise ValueError("prior source audit differs from full G4 recomputation")
    return (
        [evidence],
        {item["sha256"] for item in evidence["dataInputs"].values()},
        {item["sha256"] for item in evidence["reusableInfrastructure"].values()},
        position_sources,
    )


def _publish_forbidden_catalog(
    catalog_path: Path,
    manifest_path: Path,
    rows: Sequence[dict[str, Any]],
    source_inventory: Sequence[dict[str, Any]],
    created_utc: str,
    source_projection_manifests: Sequence[dict[str, Any]] = (),
    prior_source_audits: Sequence[dict[str, Any]] = (),
) -> tuple[Path, Path]:
    catalog_path = _resolve(catalog_path)
    manifest_path = _resolve(manifest_path)
    if catalog_path == manifest_path:
        raise ValueError("catalog and manifest destinations must differ")
    if catalog_path.exists() or manifest_path.exists():
        existing = catalog_path if catalog_path.exists() else manifest_path
        raise FileExistsError(f"refusing to overwrite {existing}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created_utc):
        raise ValueError("--created-utc must be canonical UTC to whole seconds")

    catalog_identity: dict[str, Any] | None = None
    try:
        _atomic_jsonl(catalog_path, rows)
        catalog_identity = _identity(catalog_path)
        manifest = {
            "schemaVersion": 1,
            "kind": "omega-target-opaque-forbidden-position-catalog-manifest",
            "targetOpaque": True,
            "createdUtc": created_utc,
            "catalog": catalog_identity,
            "catalogSchema": {
                "recognizedFields": sorted(FORBIDDEN_CATALOG_FIELDS),
                "unknownFieldPolicy": "abort",
                "minimumPositionIdentity": (
                    "at least one exact or conservative orbit signature per row"
                ),
            },
            "sourceInventory": list(source_inventory),
            "sourceInventorySha256": _canonical_digest(list(source_inventory)),
            "sourceProjectionManifests": list(source_projection_manifests),
            "sourceProjectionManifestsSha256": _canonical_digest(
                list(source_projection_manifests)
            ),
            "priorSourceAudits": list(prior_source_audits),
            "priorSourceAuditsSha256": _canonical_digest(list(prior_source_audits)),
            "extractionPolicy": _forbidden_extraction_policy(),
            "positionCount": len(rows),
            "targetOrScoreFieldsDecoded": 0,
            "targetOrScoreFieldsEmitted": 0,
            "producer": _producer_identity(),
        }
        _atomic_json(manifest_path, manifest)
    except BaseException:
        # Roll back only the catalog published by this call and only when no
        # manifest won the destination race.  A completed pair is immutable.
        if (
            catalog_identity is not None
            and not manifest_path.exists()
            and catalog_path.exists()
            and _identity(catalog_path) == catalog_identity
        ):
            catalog_path.unlink()
        raise
    return catalog_path, manifest_path


def _build_forbidden_catalog_from_sources(
    sources: Sequence[Path],
    catalog_path: Path,
    manifest_path: Path,
    created_utc: str,
    source_projection_manifests: Sequence[Path] = (),
    prior_source_audit_closure: Path = GENERATION4_CLOSURE_PATH,
) -> tuple[Path, Path]:
    if len(sources) != 1:
        raise ValueError("exactly one explicit G3 projection --source is required")
    explicit_sources = [_assert_prior_catalog_source(source) for source in sources]
    if not _source_declares_g3_projection(explicit_sources[0]):
        raise ValueError("the explicit source must be the recognized G3 projection")
    prior_audit, audit_position_sources = _prior_source_audit_from_closure(
        prior_source_audit_closure
    )
    resolved_sources = [
        *explicit_sources,
        *[_assert_prior_catalog_source(path) for path in audit_position_sources],
    ]
    if len(set(resolved_sources)) != len(resolved_sources):
        raise ValueError("prior-artifact sources must be unique")
    destinations = {_resolve(catalog_path), _resolve(manifest_path)}
    if destinations & set(resolved_sources):
        raise ValueError("a prior-artifact source cannot also be an output")

    all_observations: list[dict[str, str]] = []
    inventory: list[dict[str, Any]] = []
    for source in sorted(resolved_sources, key=lambda path: str(path).casefold()):
        observations, identity = _extract_catalog_source(source)
        all_observations.extend(observations)
        inventory.append(identity)
    projection_evidence = _verify_source_projection_manifests(
        list(source_projection_manifests), inventory
    )
    rows = _catalog_rows_from_observations(all_observations)
    if not rows:
        raise ValueError("explicit prior sources yielded an empty forbidden catalog")
    return _publish_forbidden_catalog(
        catalog_path,
        manifest_path,
        rows,
        inventory,
        created_utc,
        projection_evidence["manifestPins"],
        [prior_audit],
    )


def _build_forbidden_catalog_command(args: argparse.Namespace) -> None:
    runtime_contract.verify_manifest(runtime_contract.DEFAULT_OUTPUT)
    catalog, manifest = _build_forbidden_catalog_from_sources(
        [_resolve(path) for path in args.source],
        _resolve(args.catalog),
        _resolve(args.manifest),
        args.created_utc,
        [_resolve(path) for path in args.source_projection_manifest],
        _resolve(args.prior_source_audit_closure),
    )
    print(f"Published target-opaque forbidden catalog: {catalog}")
    print(f"Published target-opaque forbidden manifest: {manifest}")


def _verify_forbidden_catalog_command(args: argparse.Namespace) -> None:
    forbidden, pins = _load_forbidden_catalogs([_resolve(args.manifest)])
    if len(pins) != 1:
        raise ValueError("forbidden catalog verification requires exactly one manifest")
    pin = pins[0]
    print(
        "Verified target-opaque forbidden catalog: "
        f"positions={pin['positionCount']} "
        f"dataInputs={len(forbidden['sourceDataInputSha256'])} "
        f"reusableInfrastructure={len(forbidden['reusableInfrastructureSha256'])} "
        f"historicalGames={len(forbidden['sourceGameIds'])} "
        f"historicalRuns={len(forbidden['sourceRunIds'])}"
    )


def _load_forbidden_catalogs(
    manifests: Sequence[Path],
    *,
    enforce_canonical_source_inventory: bool = True,
    _self_test_prior_audit: dict[str, Any] | None = None,
) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    if not manifests:
        raise ValueError("at least one prior target-opaque forbidden manifest is required")
    forbidden = {
        "exact": set(),
        "signatures": set(),
        "sourceGameIds": set(),
        "sourceRunIds": set(),
        "sourceDataInputSha256": set(),
        "reusableInfrastructureSha256": set(),
        "legacySourceArtifactSha256": set(),
    }
    pins: list[dict[str, Any]] = []
    for manifest_path in manifests:
        manifest_path = _resolve(manifest_path)
        manifest, manifest_identity = _load_json_snapshot(manifest_path)
        allowed_manifest_fields = {
            "schemaVersion",
            "kind",
            "targetOpaque",
            "createdUtc",
            "catalog",
            "catalogSchema",
            "sourceInventory",
            "sourceInventorySha256",
            "sourceProjectionManifests",
            "sourceProjectionManifestsSha256",
            "priorSourceAudits",
            "priorSourceAuditsSha256",
            "extractionPolicy",
            "positionCount",
            "targetOrScoreFieldsDecoded",
            "targetOrScoreFieldsEmitted",
            "producer",
        }
        if set(manifest) != allowed_manifest_fields:
            raise ValueError(
                f"forbidden manifest has undeclared or missing fields: {manifest_path}"
            )
        if _has_target_like_key(manifest):
            raise ValueError(f"forbidden manifest has a target/score-like nested key")
        if (
            manifest.get("schemaVersion") != 1
            or manifest.get("kind")
            != "omega-target-opaque-forbidden-position-catalog-manifest"
            or manifest.get("targetOpaque") is not True
            or int(manifest.get("targetOrScoreFieldsDecoded", -1)) != 0
            or int(manifest.get("targetOrScoreFieldsEmitted", -1)) != 0
        ):
            raise ValueError(f"manifest is not a target-opaque catalog declaration: {manifest_path}")
        if not isinstance(manifest.get("createdUtc"), str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
            manifest["createdUtc"],
        ):
            raise ValueError(f"forbidden manifest has no createdUtc: {manifest_path}")
        _verify_producer_identity(
            manifest.get("producer"), f"forbidden manifest producer {manifest_path}"
        )
        source_inventory = _verify_forbidden_source_inventory(
            manifest.get("sourceInventory")
        )
        if manifest.get("sourceInventorySha256") != _canonical_digest(
            source_inventory
        ):
            raise ValueError("forbidden source inventory digest is invalid")
        projection_values = manifest.get("sourceProjectionManifests")
        if not isinstance(projection_values, list):
            raise ValueError("forbidden source projection inventory is malformed")
        projection_evidence = _verify_source_projection_manifests(
            projection_values, source_inventory
        )
        projection_pins = projection_evidence["manifestPins"]
        if (
            projection_pins != projection_values
            or manifest.get("sourceProjectionManifestsSha256")
            != _canonical_digest(projection_pins)
        ):
            raise ValueError("forbidden source projection inventory digest is invalid")
        forbidden["legacySourceArtifactSha256"].update(
            projection_evidence["legacySourceArtifactSha256"]
        )
        forbidden["sourceGameIds"].update(projection_evidence["sourceGameIds"])
        forbidden["sourceRunIds"].update(projection_evidence["sourceRunIds"])
        audit_values = manifest.get("priorSourceAudits")
        if _self_test_prior_audit is None:
            audits, data_hashes, infrastructure_hashes, audit_position_sources = (
                _verify_prior_source_audits(audit_values)
            )
        else:
            if enforce_canonical_source_inventory:
                raise AssertionError("self-test prior audit cannot bypass production inventory")
            audits = [_self_test_prior_audit]
            if audit_values != audits:
                raise ValueError("synthetic prior source audit differs from its fixture")
            data_hashes = {
                item["sha256"] for item in _self_test_prior_audit["dataInputs"].values()
            }
            infrastructure_hashes = {
                item["sha256"]
                for item in _self_test_prior_audit["reusableInfrastructure"].values()
            }
            audit_position_sources = []
        if (
            audits != audit_values
            or manifest.get("priorSourceAuditsSha256") != _canonical_digest(audits)
        ):
            raise ValueError("forbidden prior source-audit inventory digest is invalid")
        forbidden["sourceDataInputSha256"].update(data_hashes)
        forbidden["reusableInfrastructureSha256"].update(infrastructure_hashes)
        if manifest.get("extractionPolicy") != _forbidden_extraction_policy():
            raise ValueError("forbidden manifest has an unsupported extraction policy")
        source_hashes = {identity["sha256"] for identity in source_inventory}
        source_paths = {str(_resolve(identity["path"])) for identity in source_inventory}
        expected_source_paths = {
            *projection_evidence["projectionSources"],
            *(str(_resolve(path)) for path in audit_position_sources),
        }
        if enforce_canonical_source_inventory and source_paths != expected_source_paths:
            raise ValueError(
                "forbidden source inventory differs from G3 projection plus "
                "closure-authenticated G4 position sources"
            )
        schema = manifest.get("catalogSchema")
        if not isinstance(schema, dict) or set(schema) != {
            "recognizedFields",
            "unknownFieldPolicy",
            "minimumPositionIdentity",
        }:
            raise ValueError(f"forbidden manifest has no strict catalog schema: {manifest_path}")
        if (
            schema.get("recognizedFields") != sorted(FORBIDDEN_CATALOG_FIELDS)
            or schema.get("unknownFieldPolicy") != "abort"
            or schema.get("minimumPositionIdentity")
            != "at least one exact or conservative orbit signature per row"
        ):
            raise ValueError(f"forbidden manifest declares an unsupported catalog schema")
        catalog_identity = _verify_identity(manifest.get("catalog"), "forbidden catalog")
        catalog_path = Path(catalog_identity["path"])
        records, stable_catalog_identity = _snapshot_jsonl(catalog_path)
        if stable_catalog_identity != catalog_identity:
            raise ValueError("forbidden catalog changed after manifest publication")
        position_ids: set[str] = set()
        for line_number, row in records:
            unknown = set(row) - FORBIDDEN_CATALOG_FIELDS
            if unknown:
                raise ValueError(
                    f"{catalog_path}:{line_number}: undeclared fields {sorted(unknown)}"
                )
            if _has_target_like_key(row):
                raise ValueError(f"{catalog_path}:{line_number}: target/score-like key")
            if (
                row.get("schemaVersion") != 1
                or row.get("kind") != "omega-target-opaque-forbidden-position"
            ):
                raise ValueError(f"{catalog_path}:{line_number}: wrong row schema/kind")
            for text_field in (
                "positionId",
                "ofen",
                "exactPositionKey",
                "conservativeOrbitKey",
                "sourceGameId",
                "sourceRunId",
                "sourceArtifactSha256",
            ):
                if text_field in row and (
                    not isinstance(row[text_field], str) or not row[text_field]
                ):
                    raise ValueError(
                        f"{catalog_path}:{line_number}: {text_field} is not a nonempty string"
                    )
            position_id = row.get("positionId", "")
            if not position_id or position_id in position_ids:
                raise ValueError(f"{catalog_path}:{line_number}: duplicate/empty positionId")
            position_ids.add(position_id)
            exact = row.get("exactPositionKey", "")
            orbit = row.get("conservativeOrbitKey", "")
            signatures_value = row.get("conservativeOrbitSignatures", [])
            if not isinstance(signatures_value, list) or not all(
                isinstance(value, str) and value for value in signatures_value
            ):
                raise ValueError(f"{catalog_path}:{line_number}: malformed orbit signatures")
            signatures = set(signatures_value)
            if signatures_value != sorted(signatures):
                raise ValueError(
                    f"{catalog_path}:{line_number}: orbit signatures are not canonical"
                )
            if orbit:
                signatures.add(orbit)
            if row.get("ofen") is not None:
                ofen = _normalized_ofen(row["ofen"])
                derived_exact, derived_orbit, derived_signatures = deep._leakage_keys(ofen)
                if exact and exact != derived_exact:
                    raise ValueError(f"{catalog_path}:{line_number}: exact key mismatch")
                if orbit and orbit != derived_orbit:
                    raise ValueError(f"{catalog_path}:{line_number}: orbit key mismatch")
                if signatures and not signatures.issubset(set(derived_signatures)):
                    raise ValueError(f"{catalog_path}:{line_number}: orbit signature mismatch")
                exact = derived_exact
                signatures.update(derived_signatures)
            if not exact and not signatures:
                raise ValueError(
                    f"{catalog_path}:{line_number}: no exact/orbit position identity"
                )
            if exact:
                forbidden["exact"].add(exact)
            forbidden["signatures"].update(signatures)
            for field, bucket in (
                ("sourceGameId", "sourceGameIds"),
                ("sourceRunId", "sourceRunIds"),
            ):
                value = row.get(field, "")
                if value:
                    forbidden[bucket].add(value)
            source_sha = str(row.get("sourceArtifactSha256", "")).lower()
            if not HEX_SHA256.fullmatch(source_sha):
                raise ValueError(
                    f"{catalog_path}:{line_number}: malformed source artifact SHA-256"
                )
            if source_sha not in source_hashes:
                raise ValueError(
                    f"{catalog_path}:{line_number}: source identity is absent from inventory"
                )
            if position_id != _catalog_position_id(row):
                raise ValueError(
                    f"{catalog_path}:{line_number}: content-derived positionId mismatch"
                )
        if int(manifest.get("positionCount", -1)) != len(records):
            raise ValueError(f"forbidden catalog row count differs from its manifest")
        pins.append(
            {
                "manifest": manifest_identity,
                "catalog": stable_catalog_identity,
                "positionCount": len(records),
            }
        )
    return forbidden, pins


def _build_synthetic_forbidden_catalog(
    directory: Path,
    rows: Sequence[dict[str, Any]],
    *,
    prior_audit: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Build the exact target-opaque catalog contract used by unit tests."""

    directory = _resolve(directory)
    catalog = directory / "forbidden.positions.jsonl"
    manifest = directory / "forbidden.positions.manifest.json"
    synthetic_source = directory / "synthetic-prior-source.jsonl"
    _atomic_jsonl(
        synthetic_source,
        [
            {
                "ofen": str(source.get("ofen", "")),
                "gameId": str(source.get("sourceGameId", "")),
                "runId": str(source.get("sourceRunId", "")),
            }
            for source in rows
        ],
    )
    source_identity = _identity(synthetic_source)
    observations: list[dict[str, str]] = []
    extras: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        observations.append(
            {
                "ofen": str(row.pop("ofen")),
                "sourceGameId": str(row.pop("sourceGameId", "")),
                "sourceRunId": str(row.pop("sourceRunId", "")),
                "sourceArtifactSha256": source_identity["sha256"],
            }
        )
        # Tests may intentionally add an undeclared field after the valid row
        # has been constructed to exercise the strict loader.
        row.pop("sourceArtifactSha256", None)
        extras.append(row)
    normalized_rows = []
    for observation, extra in zip(observations, extras):
        normalized = _catalog_rows_from_observations([observation])[0]
        normalized.update(extra)
        normalized_rows.append(normalized)
    normalized_rows.sort(key=lambda row: row["positionId"])
    if prior_audit is None:
        prior_audit, _ = _prior_source_audit_from_closure(GENERATION4_CLOSURE_PATH)
    return _publish_forbidden_catalog(
        catalog,
        manifest,
        normalized_rows,
        [source_identity],
        "2026-07-21T00:00:00Z",
        (),
        [prior_audit],
    )


def _walk_identity_objects(value: Any, label: str = "identity") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if {"path", "bytes", "sha256"}.issubset(value):
            yield label, {key: value[key] for key in ("path", "bytes", "sha256")}
            return
        for key, item in value.items():
            yield from _walk_identity_objects(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_identity_objects(item, f"{label}[{index}]")


def _verify_prelabel_seal(
    path: Path,
    preregistration_path: Path,
    final_freeze_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    profile, prereg_identity, final_freeze_identity = _verify_final_freeze(
        preregistration_path, final_freeze_path
    )
    seal, seal_identity = _load_json_snapshot(_resolve(path))
    _strict_keys(
        seal,
        {
            "schemaVersion",
            "kind",
            "profileId",
            "status",
            "createdUtc",
            "declaration",
            "policy",
            "coverage",
            "plannedArtifacts",
            "identities",
            "finalStageSeal",
        },
        "pre-label freeze seal",
    )
    if (
        seal.get("schemaVersion") != 1
        or seal.get("kind") != "omega-decision-prelabel-freeze-seal"
        or seal.get("profileId") != PROFILE_ID
        or seal.get("status") != PRELABEL_STATUS
        or seal.get("finalStageSeal") is not True
        or not isinstance(seal.get("createdUtc"), str)
    ):
        raise ValueError("wrong or unsupported pre-label freeze seal")
    declaration = seal.get("declaration")
    if not isinstance(declaration, dict) or declaration != {
        "teacherSearchesPresentAtFreeze": False,
        "targetOrScoreFieldsDecoded": 0,
        "targetOrScoreFieldsEmitted": 0,
        "componentAndSplitMapFrozenBeforeSearch": True,
        "rawGraphAuthenticatedBeforeFiltering": True,
        "rootFeasibilityReplayedAtFreeze": True,
    }:
        raise ValueError("pre-label freeze declaration is invalid")
    identities = seal.get("identities")
    if not isinstance(identities, dict):
        raise ValueError("pre-label freeze has no identities")
    _strict_keys(
        identities,
        {
            "preregistration",
            "finalFreezeSeal",
            "rawRoots",
            "rawRootsManifest",
            "rawChildren",
            "rawChildrenManifest",
            "rawSamplerCompletionSeal",
            "rootFeasibility",
            "rootFeasibilityManifest",
            "rootFeasibilitySeal",
            "roots",
            "rootManifest",
            "children",
            "childrenManifest",
            "samplerCompletionSeal",
            "componentMap",
            "teacherEngine",
            "forbiddenCatalogs",
            "producer",
        },
        "pre-label identities",
    )
    if identities["preregistration"] != prereg_identity:
        raise ValueError("pre-label seal belongs to another preregistration")
    if identities["finalFreezeSeal"] != final_freeze_identity:
        raise ValueError("pre-label seal belongs to another final freeze")
    final_name_for_prelabel_name = {
        "rawRoots": "rawRoots",
        "rawRootsManifest": "rawRootsManifest",
        "rawChildren": "rawChildren",
        "rawChildrenManifest": "rawChildrenManifest",
        "rawSamplerCompletionSeal": "rawSamplerCompletionSeal",
        "rootFeasibility": "rootFeasibility",
        "rootFeasibilityManifest": "rootFeasibilityManifest",
        "rootFeasibilitySeal": "rootFeasibilitySeal",
        "roots": "roots",
        "rootManifest": "rootsManifest",
        "children": "children",
        "childrenManifest": "childrenManifest",
        "samplerCompletionSeal": "samplerCompletionSeal",
        "teacherEngine": "teacherEngineExecutable",
    }
    for prelabel_name, final_name in final_name_for_prelabel_name.items():
        actual = _verify_identity(
            identities[prelabel_name], f"pre-label {prelabel_name}"
        )
        if actual != _frozen_identity(profile, final_name):
            raise ValueError(
                f"pre-label {prelabel_name} differs from final identity {final_name}"
            )
    for name in ("componentMap",):
        _verify_identity(identities[name], f"pre-label {name}")
    forbidden = identities["forbiddenCatalogs"]
    if not isinstance(forbidden, list) or not forbidden:
        raise ValueError("pre-label seal has no forbidden catalog pins")
    manifest_identities: list[dict[str, Any]] = []
    for index, pin in enumerate(forbidden):
        _strict_keys(
            pin,
            {"manifest", "catalog", "positionCount"},
            f"pre-label forbidden catalog {index}",
        )
        manifest_identities.append(
            _verify_identity(pin["manifest"], f"pre-label forbidden manifest {index}")
        )
        _verify_identity(pin["catalog"], f"pre-label forbidden catalog {index}")
        if int(pin["positionCount"]) < 0:
            raise ValueError("pre-label forbidden position count is invalid")
    if manifest_identities != _frozen_identity_list(
        profile, "forbiddenPositionCatalogManifests"
    ):
        raise ValueError("pre-label forbidden manifest inventory differs from final freeze")
    _verify_producer_identity(identities["producer"], "pre-label producer")
    if identities["producer"]["omegaDecisionTeacher"] != _frozen_identity(
        profile, "decisionTeacherSource"
    ):
        raise ValueError("pre-label producer differs from final decision teacher")

    root_manifest, root_manifest_identity = _load_json_snapshot(
        Path(identities["rootManifest"]["path"])
    )
    if root_manifest_identity != identities["rootManifest"]:
        raise ValueError("pre-label root manifest identity changed")
    _strict_keys(
        root_manifest,
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "profileId",
            "freshnessMarker",
            "policy",
            "coverage",
            "sources",
            "producer",
            "finalStageSeal",
            "output",
        },
        "frozen root manifest",
    )
    if (
        root_manifest["schemaVersion"] != 1
        or root_manifest["kind"] != "omega-decision-root-manifest"
        or root_manifest["profileId"] != PROFILE_ID
        or root_manifest["finalStageSeal"] is not True
        or root_manifest["output"] != identities["roots"]
    ):
        raise ValueError("frozen root manifest is invalid")
    _verify_producer_identity(root_manifest["producer"], "frozen root producer")
    sources = root_manifest["sources"]
    if not isinstance(sources, list) or len(sources) != 1:
        raise ValueError("frozen root manifest has the wrong source inventory")
    source = sources[0]
    if not isinstance(source, dict) or source.get("dataProfileId") != DATA_PROFILE_ID:
        raise ValueError("frozen root source has the wrong data profile")
    if (
        source.get("sourceNodeMode") != "nodes"
        or int(source.get("sourceNodes", 0)) != SOURCE_NODES
        or int(source.get("sourceRepeats", 0)) != 1
        or int(source.get("sourceMaxPlies", 0)) != SOURCE_MAX_PLIES
        or int(source.get("sourceAbsoluteMaxPlies", 0)) != SOURCE_MAX_PLIES
        or source.get("abBaPairsComplete") is not True
        or int(source.get("abandonedAttempts", -1)) != 0
        or source.get("colorSwapsComplete") is not True
        or source.get("sameOpeningWithinPair") is not True
        or source.get("sourceSafetyCounters")
        != {
            "illegalMoves": 0,
            "illegalPvs": 0,
            "protocolFailures": 0,
            "timeForfeits": 0,
        }
    ):
        raise ValueError("frozen root source search/safety contract is invalid")
    _verify_source_audit_evidence(profile, source)
    for audit_name, final_name in {
        "source": "sourceEvents",
        "openingSuite": "sourceOpeningSuite",
        "sourceMatchConfig": "sourceMatchConfig",
        "sourceMatchHarnessAssembly": "sourceMatchHarnessAssembly",
        "rootSamplerAssembly": "rootSamplerAssembly",
    }.items():
        if not isinstance(source, dict) or source.get(audit_name) != _frozen_identity(
            profile, final_name
        ):
            raise ValueError(f"frozen root source {audit_name} is invalid")
    suite_provenance = source.get("openingSuiteProvenance")
    if not isinstance(suite_provenance, dict) or (
        suite_provenance.get("rootSamplerSha256")
        != _frozen_identity(profile, "rootSamplerAssembly")["sha256"]
        or suite_provenance.get("rootSamplerChessLibSha256")
        != _frozen_identity(profile, "chessLibAssembly")["sha256"]
        or suite_provenance.get("rootPoolSha256")
        != _frozen_identity(profile, "sourceRootPool")["sha256"]
        or suite_provenance.get("rootPoolManifestSha256")
        != _frozen_identity(profile, "sourceRootPoolManifest")["sha256"]
        or suite_provenance.get("rootPoolSealSha256")
        != _frozen_identity(profile, "sourceRootPoolSeal")["sha256"]
        or suite_provenance.get("sourceBuilderSha256")
        != _frozen_identity(profile, "sourceOpeningBuilderSource")["sha256"]
        or suite_provenance.get("networkFormatSha256")
        != _frozen_identity(profile, "networkFormatPythonSource")["sha256"]
        or suite_provenance.get("pythonRuntimeManifestSha256")
        != _frozen_identity(profile, "pythonRuntimeManifest")["sha256"]
    ):
        raise ValueError("frozen opening-suite provenance is invalid")

    child_manifest, child_manifest_identity = _load_json_snapshot(
        Path(identities["childrenManifest"]["path"])
    )
    if child_manifest_identity != identities["childrenManifest"]:
        raise ValueError("pre-label child manifest identity changed")
    _strict_keys(
        child_manifest,
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "policy",
            "coverage",
            "input",
            "output",
            "runtime",
            "finalStageSeal",
        },
        "frozen child manifest",
    )
    if (
        child_manifest["schemaVersion"] != 1
        or child_manifest["kind"] != "omega-decision-sampler-manifest"
        or child_manifest["input"] != identities["roots"]
        or child_manifest["output"] != identities["children"]
        or child_manifest["finalStageSeal"] is not False
    ):
        raise ValueError("frozen child manifest is invalid")
    child_runtime = _strict_keys(
        child_manifest["runtime"],
        {"framework", "samplerAssembly", "chessLibAssembly"},
        "frozen child runtime",
    )
    if child_runtime["samplerAssembly"] != _frozen_identity(
        profile, "decisionSamplerAssembly"
    ) or child_runtime["chessLibAssembly"] != _frozen_identity(
        profile, "chessLibAssembly"
    ):
        raise ValueError("child manifest runtime differs from final freeze")

    sampler_seal, sampler_identity = _load_json_snapshot(
        Path(identities["samplerCompletionSeal"]["path"])
    )
    if sampler_identity != identities["samplerCompletionSeal"]:
        raise ValueError("sampler completion seal identity changed")
    _strict_keys(
        sampler_seal,
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "input",
            "output",
            "manifest",
            "producer",
            "finalStageSeal",
        },
        "sampler completion seal",
    )
    if (
        sampler_seal["schemaVersion"] != 1
        or sampler_seal["kind"] != "omega-decision-sampler-completion-seal"
        or sampler_seal["input"] != identities["roots"]
        or sampler_seal["output"] != identities["children"]
        or sampler_seal["manifest"] != identities["childrenManifest"]
        or sampler_seal["finalStageSeal"] is not True
    ):
        raise ValueError("sampler completion seal is invalid")
    sampler_producer = _strict_keys(
        sampler_seal["producer"],
        {"samplerAssembly", "chessLibAssembly", "framework"},
        "sampler completion producer",
    )
    if sampler_producer["samplerAssembly"] != _frozen_identity(
        profile, "decisionSamplerAssembly"
    ) or sampler_producer["chessLibAssembly"] != _frozen_identity(
        profile, "chessLibAssembly"
    ):
        raise ValueError("sampler completion producer differs from final freeze")
    if not isinstance(sampler_producer["framework"], str):
        raise ValueError("sampler completion framework is malformed")

    map_records, map_identity = _snapshot_jsonl(Path(identities["componentMap"]["path"]))
    if map_identity != identities["componentMap"] or not map_records:
        raise ValueError("component map identity/contents are invalid")
    if _has_target_like_key([record for _, record in map_records]):
        raise ValueError("component map contains a target-like field")

    policy = _strict_keys(
        seal.get("policy"),
        {
            "sourceSeed",
            "rootSelectionSeed",
            "siblingExplorationSeed",
            "componentSplitSeed",
            "trainPercent",
            "validationPercent",
            "heldOutPercent",
            "splitNames",
            "priorReusePolicy",
        },
        "pre-label policy",
    )
    if (
        policy["sourceSeed"] != SOURCE_SEED
        or policy["rootSelectionSeed"] != ROOT_SELECTION_SEED
        or policy["siblingExplorationSeed"] != SIBLING_EXPLORATION_SEED
        or policy["componentSplitSeed"] != COMPONENT_SPLIT_SEED
        or policy["trainPercent"] != 80.0
        or policy["validationPercent"] != 10.0
        or policy["heldOutPercent"] != 10.0
        or policy["splitNames"] != list(SPLITS)
        or policy["priorReusePolicy"] != PRIOR_REUSE_POLICY
    ):
        raise ValueError("pre-label policy differs from the frozen G5 contract")
    coverage = _strict_keys(
        seal.get("coverage"),
        {
            "rawRoots",
            "rawChildren",
            "roots",
            "children",
            "sourceGroups",
            "components",
            "phaseSplitRootCounts",
            "forbiddenCatalogs",
            "forbiddenCatalogPositions",
            "priorReuseCollisions",
        },
        "pre-label coverage",
    )
    if (
        int(coverage["rawRoots"]) != RAW_ROOTS_PER_PHASE_SIDE * len(PHASES) * 2
        or int(coverage["rawChildren"]) <= 0
        or int(coverage["roots"])
        != FEASIBLE_ROOTS_PER_PHASE_SIDE * len(PHASES) * 2
        or int(coverage["children"]) <= 0
        or int(coverage["sourceGroups"])
        != FEASIBLE_ROOTS_PER_PHASE_SIDE * len(PHASES) * 2
        or int(coverage["components"]) <= 0
        or int(coverage["forbiddenCatalogs"]) != len(forbidden)
        or int(coverage["forbiddenCatalogPositions"]) < 0
        or int(coverage["priorReuseCollisions"]) != 0
        or not isinstance(coverage["phaseSplitRootCounts"], dict)
    ):
        raise ValueError("pre-label coverage is invalid")
    planned = _strict_keys(
        seal.get("plannedArtifacts"),
        {"shallowLedger", "deepLedger", "selectedChildren", "labels"},
        "pre-label planned artifacts",
    )
    data_root = _resolve(profile["namespaces"]["dataRoot"])
    expected_planned = {
        "shallowLedger": str(data_root / "shallow-results.jsonl"),
        "deepLedger": str(data_root / "deep-results.jsonl"),
        "selectedChildren": str(data_root / "selected-children.jsonl"),
        "labels": str(data_root / "decision-labels.jsonl"),
    }
    if planned != expected_planned:
        raise ValueError("pre-label planned artifacts differ from preregistration")
    if _resolve(path) != _resolve(profile["namespaces"]["preLabelSeal"]):
        raise ValueError("pre-label seal is outside its frozen namespace")
    return seal, seal_identity, profile


def _ledger_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [record for _, record in _jsonl(path)]


def _append_fsynced(path: Path, record: dict[str, Any], lock: threading.Lock) -> None:
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab", buffering=0) as stream:
            stream.write(payload)
            os.fsync(stream.fileno())


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # On Windows ``os.kill(pid, 0)`` terminates the target instead of
        # providing the POSIX existence probe expected below.  Query a
        # limited-information process handle without signaling it.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # Access denied still proves that a process owns the PID.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class _ExclusiveLedgerClaim:
    """Crash-recoverable cross-process single-writer claim for one ledger."""

    def __init__(
        self,
        path: Path,
        *,
        ledger: Path,
        stage: str,
        lock_sha256: str,
        recover_stale: bool,
    ) -> None:
        self.path = _resolve(path)
        self.document = {
            "schemaVersion": 1,
            "kind": "omega-decision-active-ledger-claim",
            "stage": stage,
            "ledger": str(_resolve(ledger)),
            "lockSha256": lock_sha256,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "token": uuid.uuid4().hex,
            "createdUtc": _utc_now(),
        }
        if self.path.exists():
            if not recover_stale:
                raise RuntimeError(
                    f"ledger already has an active/stale claim: {self.path}; "
                    "run again with --recover-stale-claim only after auditing it"
                )
            existing, existing_identity = _load_json_snapshot(self.path)
            _strict_keys(
                existing,
                set(self.document),
                "existing active-ledger claim",
            )
            if (
                existing.get("kind") != self.document["kind"]
                or existing.get("stage") != stage
                or existing.get("ledger") != self.document["ledger"]
                or existing.get("lockSha256") != lock_sha256
            ):
                raise ValueError("stale claim belongs to another ledger/search lock")
            if existing.get("host") != socket.gethostname():
                raise RuntimeError("cannot establish staleness of a claim from another host")
            if _pid_is_alive(int(existing.get("pid", -1))):
                raise RuntimeError("refusing to recover a claim whose owner PID is alive")
            if _identity(self.path) != existing_identity:
                raise RuntimeError("active claim changed during stale-recovery audit")
            self.path.unlink()
        _atomic_json(self.path, self.document)
        if json.loads(self.path.read_text(encoding="utf-8")) != self.document:
            raise RuntimeError("exclusive ledger claim publication was not stable")

    def release(self) -> None:
        if not self.path.exists():
            raise RuntimeError("exclusive ledger claim disappeared while owned")
        existing = json.loads(self.path.read_text(encoding="utf-8"))
        if existing != self.document:
            raise RuntimeError("exclusive ledger claim changed while owned")
        self.path.unlink()


_UCI_FACTORY = deep._Uci


class _PersistentHceSession:
    """One configured UCI process reused serially by one search worker."""

    def __init__(self, engine: Path, reverify_before_start: Any):
        self.engine = engine
        self.reverify_before_start = reverify_before_start
        self.uci: Any | None = None
        self.starts = 0
        self.restarts = 0

    def _start(self) -> Any:
        # This executes for the initial process and every restart after an
        # error.  No persistent teacher process may start on stale identities.
        self.reverify_before_start()
        uci = _UCI_FACTORY(self.engine)
        try:
            uci.send("uci")
            uci.until(lambda line: line == "uciok", 10.0)
            for name, value in DEFAULT_HCE_OPTIONS.items():
                uci.send(f"setoption name {name} value {value}")
            uci.send("isready")
            uci.until(lambda line: line == "readyok", 10.0)
        except BaseException:
            uci.close()
            raise
        self.starts += 1
        self.uci = uci
        return uci

    def _reset_after_error(self) -> None:
        if self.uci is not None:
            self.uci.close()
        self.uci = None
        self.restarts += 1

    def search(self, ofen: str, nodes: int, timeout_seconds: float) -> dict[str, Any]:
        uci = self.uci or self._start()
        started = time.monotonic()
        try:
            uci.send("ucinewgame")
            uci.send("setoption name Clear Hash")
            uci.send("isready")
            uci.until(lambda line: line == "readyok", 10.0)
            uci.send(f"position fen {ofen}")
            uci.send(f"go nodes {nodes}")
            stdout, stderr = uci.until(
                lambda line: line.startswith("bestmove "), timeout_seconds
            )
            bestmove_line = next(
                line for line in reversed(stdout) if line.startswith("bestmove ")
            )
            tokens = bestmove_line.split()
            if len(tokens) < 2 or tokens[1] in {"(none)", "0000"}:
                raise ValueError(f"engine returned terminal/malformed {bestmove_line!r}")
            parsed = deep._parse_info(stdout, nodes)
            parsed.update(
                {
                    "bestMove": tokens[1],
                    "wallTimeMs": round((time.monotonic() - started) * 1000, 3),
                    "rawOutput": stdout,
                    "standardError": stderr,
                }
            )
            return parsed
        except BaseException:
            self._reset_after_error()
            raise

    def close(self) -> None:
        if self.uci is not None:
            self.uci.close()
            self.uci = None


def _run_stage(args: argparse.Namespace, stage: str) -> None:
    expected_nodes = 2000 if stage == "shallow" else 50000
    if args.jobs != SEARCH_WORKERS or args.nodes != expected_nodes:
        raise ValueError("teacher stage worker count/node budget differs from final freeze")
    ledger = _resolve(args.ledger)
    prelabel, prelabel_identity, _ = _verify_prelabel_seal(
        _resolve(args.prelabel_seal),
        _resolve(args.preregistration),
        _resolve(args.final_freeze_seal),
    )
    claim_contract = {
        "stage": stage,
        "ledger": str(ledger),
        "input": str(_resolve(args.input)),
        "inputManifest": str(_resolve(args.input_manifest)),
        "engine": str(_resolve(args.engine)),
        "nodes": args.nodes,
        "jobs": args.jobs,
        "maximumAttempts": args.max_attempts,
        "prelabelFreeze": prelabel_identity,
        "finalFreezeSeal": prelabel["identities"]["finalFreezeSeal"],
    }
    claim = _ExclusiveLedgerClaim(
        Path(str(ledger) + ".active.claim.json"),
        ledger=ledger,
        stage=stage,
        lock_sha256=_canonical_digest(claim_contract),
        recover_stale=bool(args.recover_stale_claim),
    )
    try:
        _run_stage_claimed(args, stage)
    finally:
        claim.release()


def _run_stage_claimed(args: argparse.Namespace, stage: str) -> None:
    source = _resolve(args.input)
    source_manifest_path = _resolve(args.input_manifest)
    ledger = _resolve(args.ledger)
    engine = _resolve(args.engine)
    lock_path = Path(str(ledger) + ".lock.json")
    complete_path = Path(str(ledger) + ".complete.manifest.json")
    if not source.is_file() or not source_manifest_path.is_file() or not engine.is_file():
        raise FileNotFoundError("stage input/manifest or HCE engine is missing")
    items = _load_unique_items(source)
    prelabel, prelabel_identity, prereg = _verify_prelabel_seal(
        _resolve(args.prelabel_seal),
        _resolve(args.preregistration),
        _resolve(args.final_freeze_seal),
    )
    identities = prelabel["identities"]

    def reverify_before_engine_process() -> None:
        current_prelabel, current_identity, _ = _verify_prelabel_seal(
            _resolve(args.prelabel_seal),
            _resolve(args.preregistration),
            _resolve(args.final_freeze_seal),
        )
        if current_identity != prelabel_identity:
            raise ValueError("pre-label freeze changed before engine process start")
        if _identity(engine) != current_prelabel["identities"]["teacherEngine"]:
            raise ValueError("teacher engine changed before engine process start")
    planned_ledger_key = "shallowLedger" if stage == "shallow" else "deepLedger"
    if str(ledger) != prelabel.get("plannedArtifacts", {}).get(planned_ledger_key):
        raise ValueError("search ledger path differs from the pre-label freeze")
    if _identity(engine) != identities.get("teacherEngine"):
        raise ValueError("search engine differs from the pre-label freeze")
    if stage == "shallow":
        if _identity(source) != identities.get("children"):
            raise ValueError("shallow input differs from frozen legal children")
        if _identity(source_manifest_path) != identities.get("childrenManifest"):
            raise ValueError("shallow input manifest differs from frozen child manifest")
    else:
        if str(source) != prelabel.get("plannedArtifacts", {}).get("selectedChildren"):
            raise ValueError("deep input path differs from the pre-label freeze")
        frozen_children = {
            _item_id(item): item
            for item in _load_unique_items(Path(identities["children"]["path"]))
        }
        for item in items:
            frozen = frozen_children.get(_item_id(item))
            if frozen is None or any(
                item.get(field) != frozen.get(field)
                for field in (
                    "rootId",
                    "groupId",
                    "move",
                    "parentOfen",
                    "childOfen",
                    "phase",
                    "sourceGameId",
                )
            ):
                raise ValueError("deep input is not a faithful frozen-child subset")
        selection_manifest, selection_manifest_identity = _load_json_snapshot(
            source_manifest_path
        )
        _strict_keys(
            selection_manifest,
            {
                "schemaVersion",
                "kind",
                "createdUtc",
                "policy",
                "coverage",
                "inputs",
                "producer",
                "finalStageSeal",
                "output",
            },
            "deep-input selection manifest",
        )
        if (
            selection_manifest["schemaVersion"] != 1
            or selection_manifest["kind"] != "omega-decision-four-child-selection"
            or selection_manifest["output"] != _identity(source)
            or selection_manifest["finalStageSeal"] is not True
            or selection_manifest["inputs"].get("preregistration")
            != identities["preregistration"]
            or selection_manifest["inputs"].get("finalFreezeSeal")
            != identities["finalFreezeSeal"]
            or selection_manifest["inputs"].get("prelabelFreeze")
            != prelabel_identity
        ):
            raise ValueError("deep-input selection manifest is invalid")
        _verify_producer_identity(
            selection_manifest["producer"], "deep-input selection producer"
        )
    lock_payload = {
        "schemaVersion": 1,
        "kind": "omega-decision-search-lock",
        "stage": stage,
        "nodes": args.nodes,
        "timeoutSeconds": args.timeout_seconds,
        "maximumAttempts": args.max_attempts,
        "workerCount": args.jobs,
        "sessionPolicy": {
            "onePersistentUciSessionPerWorker": True,
            "configureOptionsOncePerSession": True,
            "perChildCommands": [
                "ucinewgame",
                "setoption name Clear Hash",
                "isready",
                "position fen <child-ofen>",
                f"go nodes {args.nodes}",
            ],
            "restartAfterAnyProtocolOrSearchError": True,
        },
        "input": _identity(source),
        "inputManifest": _identity(source_manifest_path),
        "engine": _identity(engine),
        "preregistration": identities["preregistration"],
        "finalFreezeSeal": identities["finalFreezeSeal"],
        "prelabelFreeze": prelabel_identity,
        "hceOptions": DEFAULT_HCE_OPTIONS,
        "producer": _producer_identity(),
    }
    lock_digest = hashlib.sha256(
        json.dumps(lock_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    lock_document = {**lock_payload, "lockSha256": lock_digest}
    if lock_path.exists():
        existing = json.loads(lock_path.read_text(encoding="utf-8"))
        if existing != lock_document:
            raise ValueError("existing stage lock differs from requested search")
    else:
        try:
            _atomic_json(lock_path, lock_document)
        except FileExistsError:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
            if existing != lock_document:
                raise ValueError("concurrently published stage lock differs")
    if complete_path.exists():
        complete = _require_completed_ledger(ledger, stage)
        if (
            complete.get("lockSha256") != lock_digest
            or complete.get("preregistration") != identities["preregistration"]
            or complete.get("finalFreezeSeal") != identities["finalFreezeSeal"]
            or complete.get("prelabelFreeze") != prelabel_identity
        ):
            raise ValueError("completion manifest belongs to another lock")
        print(f"{stage} ledger already complete: {ledger}")
        return

    existing = _ledger_records(ledger)
    successes: dict[str, dict[str, Any]] = {}
    attempts = Counter()
    item_ids = {_item_id(item) for item in items}
    for record in existing:
        if record.get("lockSha256") != lock_digest:
            raise ValueError("ledger contains a record from another search lock")
        child_id = str(record.get("childId", ""))
        if child_id not in item_ids:
            raise ValueError("ledger contains an unknown child ID")
        attempts[child_id] = max(attempts[child_id], int(record.get("attempt", 0)))
        if record.get("status") == "ok":
            successes[child_id] = record
    append_lock = threading.Lock()
    success_lock = threading.Lock()
    lifecycle_lock = threading.Lock()
    lifecycle = Counter()

    def record_for(item: dict[str, Any], attempt: int, session: _PersistentHceSession) -> dict[str, Any]:
        child_id = _item_id(item)
        base = {
            "schemaVersion": 1,
            "kind": "omega-decision-search-result",
            "stage": stage,
            "lockSha256": lock_digest,
            "childId": child_id,
            "rootId": item.get("rootId"),
            "groupId": item.get("groupId"),
            "move": item.get("move"),
            "attempt": attempt,
            "createdUtc": _utc_now(),
        }
        try:
            search = session.search(item["childOfen"], args.nodes, args.timeout_seconds)
            return {
                **base,
                "status": "ok",
                "scoreCpChildStm": int(search["scoreCp"]),
                "scoreCpRoot": -int(search["scoreCp"]),
                "search": search,
            }
        except Exception as error:  # permanent failures remain auditable
            return {
                **base,
                "status": "error",
                "errorType": type(error).__name__,
                "error": str(error),
            }
    pending = [
        item
        for item in items
        if _item_id(item) not in successes
        and attempts[_item_id(item)] < args.max_attempts
    ]
    assignments = [pending[index::args.jobs] for index in range(args.jobs)]

    def worker(assigned: list[dict[str, Any]]) -> None:
        session = _PersistentHceSession(engine, reverify_before_engine_process)
        try:
            for item in assigned:
                child_id = _item_id(item)
                while (
                    child_id not in successes
                    and attempts[child_id] < args.max_attempts
                ):
                    attempt = attempts[child_id] + 1
                    record = record_for(item, attempt, session)
                    _append_fsynced(ledger, record, append_lock)
                    attempts[child_id] = attempt
                    if record["status"] == "ok":
                        with success_lock:
                            successes[child_id] = record
                    else:
                        print(
                            f"{stage} rejected {child_id[:12]} attempt "
                            f"{record['attempt']}: {record['error']}",
                            flush=True,
                        )
        finally:
            session.close()
            with lifecycle_lock:
                lifecycle["sessionStarts"] += session.starts
                lifecycle["sessionRestartsAfterError"] += session.restarts

    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = [executor.submit(worker, assigned) for assigned in assignments]
        for future in futures:
            future.result()
    missing = sorted(item_ids - successes.keys())
    _atomic_json(
        complete_path,
        {
            "schemaVersion": 1,
            "kind": "omega-decision-search-completion",
            "createdUtc": _utc_now(),
            "stage": stage,
            "lockSha256": lock_digest,
            "recordsRequired": len(items),
            "successfulChildren": len(successes),
            "rejectedChildren": len(missing),
            "rejectedChildIds": missing,
            "maximumAttempts": args.max_attempts,
            "workerCount": args.jobs,
            "sessionLifecycle": dict(lifecycle),
            "ledger": _identity(ledger),
            "lock": _identity(lock_path),
            "preregistration": identities["preregistration"],
            "finalFreezeSeal": identities["finalFreezeSeal"],
            "prelabelFreeze": prelabel_identity,
            "producer": _producer_identity(),
            "finalStageSeal": True,
        },
    )
    print(
        f"Sealed {stage} ledger: {len(successes)} valid, {len(missing)} rejected: "
        f"{ledger}"
    )


def _successful_results(ledger: Path, expected_stage: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for _, record in _jsonl(ledger):
        if record.get("stage") != expected_stage:
            raise ValueError(f"unexpected stage in {ledger}")
        if record.get("status") == "ok":
            result[str(record["childId"])] = record
    return result


def _require_completed_ledger(ledger: Path, expected_stage: str) -> dict[str, Any]:
    manifest_path = Path(str(ledger) + ".complete.manifest.json")
    if not manifest_path.is_file():
        raise ValueError(f"unsealed {expected_stage} ledger: {ledger}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _strict_keys(
        manifest,
        {
            "schemaVersion",
            "kind",
            "createdUtc",
            "stage",
            "lockSha256",
            "recordsRequired",
            "successfulChildren",
            "rejectedChildren",
            "rejectedChildIds",
            "maximumAttempts",
            "workerCount",
            "sessionLifecycle",
            "ledger",
            "lock",
            "preregistration",
            "finalFreezeSeal",
            "prelabelFreeze",
            "producer",
            "finalStageSeal",
        },
        f"{expected_stage} completion manifest",
    )
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("kind") != "omega-decision-search-completion"
        or manifest.get("stage") != expected_stage
        or manifest.get("workerCount") != SEARCH_WORKERS
        or manifest.get("finalStageSeal") is not True
    ):
        raise ValueError(f"completion manifest has the wrong stage: {manifest_path}")
    if manifest.get("ledger") != _identity(ledger):
        raise ValueError(f"sealed {expected_stage} ledger changed: {ledger}")
    _verify_identity(manifest.get("lock"), f"{expected_stage} search lock")
    _verify_producer_identity(
        manifest.get("producer"), f"{expected_stage} completion producer"
    )
    rejected = manifest.get("rejectedChildIds")
    if (
        not isinstance(rejected, list)
        or len(rejected) != int(manifest.get("rejectedChildren", -1))
        or int(manifest.get("recordsRequired", -1))
        != int(manifest.get("successfulChildren", -1)) + len(rejected)
    ):
        raise ValueError(f"{expected_stage} completion counts are inconsistent")
    return manifest


def _choose_four(
    children: Sequence[dict[str, Any]],
    shallow: dict[str, dict[str, Any]],
    *,
    seed: int,
) -> list[dict[str, Any]]:
    if len(children) < 5:
        raise ValueError("root has fewer than five distinct legal children")
    ranked = sorted(
        children,
        key=lambda child: (
            -int(shallow[_item_id(child)]["scoreCpRoot"]),
            str(child["move"]),
        ),
    )
    root_id = str(ranked[0]["rootId"])
    pv = str(ranked[0].get("rootPvMove") or "").lower()
    selected: list[tuple[dict[str, Any], str]] = []
    pv_child = next((child for child in ranked if child["move"] == pv), None)
    if pv_child is None:
        raise ValueError("source root PV move is absent from the legal-child set")
    selected.append((pv_child, "source-pv"))
    for child in ranked:
        if len(selected) == 3:
            break
        if any(_item_id(chosen) == _item_id(child) for chosen, _ in selected):
            continue
        selected.append((child, "shallow-top"))
    already = {_item_id(child) for child, _ in selected}
    hard_pool = [
        child
        for rank, child in enumerate(ranked, 1)
        if 4 <= rank <= 12 and _item_id(child) not in already
    ]
    hard_pool.sort(
        key=lambda child: hashlib.sha256(
            f"omega-hard-negative-v2\0{seed}\0{root_id}\0{_item_id(child)}".encode(
                "utf-8"
            )
        ).hexdigest()
    )
    if not hard_pool:
        raise ValueError("root has no distinct hard negative in shallow ranks 4-12")
    selected.append((hard_pool[0], "hard-negative"))
    shallow_rank = {_item_id(child): rank for rank, child in enumerate(ranked, 1)}
    return [
        {
            **child,
            "selectionRole": role,
            "shallowRank": shallow_rank[_item_id(child)],
            "shallowScoreCpRoot": int(shallow[_item_id(child)]["scoreCpRoot"]),
        }
        for child, role in selected
    ]


def _select(args: argparse.Namespace) -> None:
    children_path = _resolve(args.children)
    ledger_path = _resolve(args.shallow_ledger)
    output = _resolve(args.output)
    manifest_path = _resolve(args.manifest or str(output) + ".manifest.json")
    if manifest_path != Path(str(output) + ".manifest.json"):
        raise ValueError("selection manifest must be the frozen adjacent path")
    if output.exists() or manifest_path.exists():
        raise FileExistsError("selection output or manifest already exists")
    prelabel, prelabel_identity, prereg = _verify_prelabel_seal(
        _resolve(args.prelabel_seal),
        _resolve(args.preregistration),
        _resolve(args.final_freeze_seal),
    )
    if str(output) != prelabel.get("plannedArtifacts", {}).get("selectedChildren"):
        raise ValueError("selection output path differs from the pre-label freeze")
    if str(ledger_path) != prelabel.get("plannedArtifacts", {}).get("shallowLedger"):
        raise ValueError("selection shallow ledger differs from the pre-label freeze")
    if _identity(children_path) != prelabel["identities"].get("children"):
        raise ValueError("selection children differ from the pre-label freeze")
    children = _load_unique_items(children_path)
    shallow_completion = _require_completed_ledger(ledger_path, "shallow")
    if shallow_completion.get("prelabelFreeze") != prelabel_identity:
        raise ValueError("shallow completion belongs to another pre-label freeze")
    if (
        shallow_completion.get("preregistration")
        != prelabel["identities"]["preregistration"]
        or shallow_completion.get("finalFreezeSeal")
        != prelabel["identities"]["finalFreezeSeal"]
    ):
        raise ValueError("shallow completion is not bound to the final freeze")
    shallow = _successful_results(ledger_path, "shallow")
    by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for child in children:
        by_root[str(child.get("rootId"))].append(child)
    incomplete_roots = {
        root_id
        for root_id, items in by_root.items()
        if len(items) < 5 or any(_item_id(item) not in shallow for item in items)
    }
    rejected_groups = {
        str(item["groupId"])
        for root_id in incomplete_roots
        for item in by_root[root_id]
    }
    eligible = {
        root_id: items
        for root_id, items in by_root.items()
        if str(items[0]["groupId"]) not in rejected_groups
    }
    if args.roots_per_phase % 2 or args.reserve_roots_per_phase % 2:
        raise ValueError("selection primary and reserve phase quotas must be even")
    required_per_side = (
        args.roots_per_phase + args.reserve_roots_per_phase
    ) // 2
    primary_per_side = args.roots_per_phase // 2
    chosen_root_roles: dict[str, str] = {}
    for phase in PHASES:
        for side in ("w", "b"):
            candidates = sorted(
                (
                    (root_id, items)
                    for root_id, items in eligible.items()
                    if items[0].get("phase") == phase
                    and items[0].get("parentSideToMove") == side
                ),
                key=lambda item: (
                    str(item[1][0].get("selectionRank") or ""), item[0]
                ),
            )
            if len(candidates) < required_per_side:
                raise ValueError(
                    f"{phase}/{side}: only {len(candidates)}/{required_per_side} "
                    "complete whole-group roots survive shallow screening"
                )
            for ordinal, (root_id, _) in enumerate(
                candidates[:required_per_side]
            ):
                chosen_root_roles[root_id] = (
                    "primary" if ordinal < primary_per_side else "deep-reserve"
                )
    selected: list[dict[str, Any]] = []
    for root_id in sorted(chosen_root_roles):
        selected.extend(
            {
                **item,
                "deepCandidateRole": chosen_root_roles[root_id],
            }
            for item in _choose_four(eligible[root_id], shallow, seed=args.seed)
        )
    _atomic_jsonl(output, selected)
    try:
        _atomic_json(
            manifest_path,
            {
                "schemaVersion": 1,
                "kind": "omega-decision-four-child-selection",
                "createdUtc": _utc_now(),
                "policy": {
                    "seed": args.seed,
                    "childrenPerRoot": 4,
                    "minimumLegalChildrenPerRoot": 5,
                    "seriousCandidates": 3,
                    "hardNegativeRanksInclusive": [4, 12],
                    "scorePerspective": "root side to move",
                    "rootsPerPhase": args.roots_per_phase,
                    "reserveRootsPerPhase": args.reserve_roots_per_phase,
                    "rejectWholeSourceGroupOnAnyInvalidSibling": True,
                    "rootOrdering": "frozen target-blind selectionRank only",
                    "shallowScoresUsedOnlyWithinRoot": True,
                    "crossBucketBorrowing": False,
                },
                "coverage": {
                    "candidateRoots": len(by_root),
                    "selectedRoots": len(chosen_root_roles),
                    "children": len(selected),
                    "rootPhaseSideCounts": {
                        f"{phase}/{side}": sum(
                            items[0].get("phase") == phase
                            and items[0].get("parentSideToMove") == side
                            and root_id in chosen_root_roles
                            for root_id, items in by_root.items()
                        )
                        for phase in PHASES
                        for side in ("w", "b")
                    },
                    "roles": dict(Counter(item["selectionRole"] for item in selected)),
                    "deepCandidateRoles": dict(
                        Counter(item["deepCandidateRole"] for item in selected)
                    ),
                    "deepCandidateRoleRoots": dict(
                        Counter(chosen_root_roles.values())
                    ),
                    "incompleteRoots": len(incomplete_roots),
                    "rejectedSourceGroups": len(rejected_groups),
                    "shallowRejectedChildren": shallow_completion.get(
                        "rejectedChildren"
                    ),
                },
                "inputs": {
                    "children": _identity(children_path),
                    "shallowLedger": _identity(ledger_path),
                    "preregistration": prelabel["identities"]["preregistration"],
                    "finalFreezeSeal": prelabel["identities"]["finalFreezeSeal"],
                    "prelabelFreeze": prelabel_identity,
                },
                "producer": _producer_identity(),
                "finalStageSeal": True,
                "output": _identity(output),
            },
        )
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print(f"Selected four children for {len(chosen_root_roles)} roots: {output}")


class _DisjointSet:
    def __init__(self, values: Iterable[str]):
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left = self.find(left)
        right = self.find(right)
        if left == right:
            return
        first, second = sorted((left, right))
        self.parent[second] = first


def _component_splits(
    selected: Sequence[dict[str, Any]],
    *,
    seed: int,
    train_percent: float,
    validation_percent: float,
) -> tuple[dict[str, str], dict[str, str]]:
    groups = sorted({str(item["groupId"]) for item in selected})
    if not groups:
        raise ValueError("cannot split an empty decision corpus")
    dsu = _DisjointSet(groups)
    owner: dict[str, str] = {}
    source_owner: dict[str, str] = {}
    for item in selected:
        group = str(item["groupId"])
        source_game = str(item.get("sourceGameId") or "")
        if not source_game:
            raise ValueError("decision row lacks sourceGameId")
        prior_source = source_owner.setdefault(source_game, group)
        dsu.union(group, prior_source)
        for ofen in (item.get("parentOfen"), item.get("childOfen")):
            normalized = _normalized_ofen(ofen)
            _, _, signatures = deep._leakage_keys(normalized)
            for signature in signatures:
                prior = owner.setdefault(signature, group)
                dsu.union(group, prior)
    members: dict[str, list[str]] = defaultdict(list)
    for group in groups:
        members[dsu.find(group)].append(group)
    stable_components = {
        representative: "decision-component:" + hashlib.sha256(
            (
                "omega-decision-component-v2\0"
                + "\0".join(sorted(component_members))
            ).encode("utf-8")
        ).hexdigest()
        for representative, component_members in members.items()
    }
    components = {
        group: stable_components[dsu.find(group)] for group in groups
    }
    splits: dict[str, str] = {}
    for component in sorted(set(components.values())):
        draw = int(
            hashlib.sha256(
                f"omega-decision-split-v2\0{seed}\0{component}".encode("utf-8")
            ).hexdigest()[:16],
            16,
        ) % 1_000_000 / 10_000.0
        split = (
            "train"
            if draw < train_percent
            else "validation"
            if draw < train_percent + validation_percent
            else "heldOut"
        )
        splits[component] = split
    return components, splits


def _inherited_component_splits(
    selected: Sequence[dict[str, Any]],
    *,
    seed: int,
    train_percent: float,
    validation_percent: float,
) -> tuple[dict[str, str], dict[str, str]]:
    """Split only the component IDs inherited from the authenticated raw graph."""

    components: dict[str, str] = {}
    for item in selected:
        group = str(item.get("groupId") or "")
        component = str(item.get("rawLeakageComponentId") or "")
        if not group or not component.startswith("raw-decision-component:"):
            raise ValueError("filtered row lacks its raw leakage component")
        prior = components.setdefault(group, component)
        if prior != component:
            raise ValueError("a source group claims multiple raw leakage components")
    if not components:
        raise ValueError("cannot split an empty inherited component inventory")
    split_for_component: dict[str, str] = {}
    for component in sorted(set(components.values())):
        draw = int(
            hashlib.sha256(
                f"omega-decision-split-v2\0{seed}\0{component}".encode("utf-8")
            ).hexdigest()[:16],
            16,
        ) % 1_000_000 / 10_000.0
        split_for_component[component] = (
            "train"
            if draw < train_percent
            else "validation"
            if draw < train_percent + validation_percent
            else "heldOut"
        )
    return components, split_for_component


def _assert_all_phases_per_split(
    rows: Sequence[dict[str, Any]], *, description: str
) -> dict[str, dict[str, int]]:
    coverage = {
        split: {
            phase: len(
                {
                    str(item["rootId"])
                    for item in rows
                    if item.get("split") == split and item.get("phase") == phase
                }
            )
            for phase in PHASES
        }
        for split in SPLITS
    }
    missing = [
        f"{split}/{phase}"
        for split in SPLITS
        for phase in PHASES
        if coverage[split][phase] == 0
    ]
    if missing:
        raise ValueError(
            f"{description} lacks required phase coverage: {', '.join(missing)}"
        )
    return coverage


def _prelabel_freeze(args: argparse.Namespace) -> None:
    output = _resolve(args.output)
    component_map_path = _resolve(args.component_map)
    if output.exists() or component_map_path.exists():
        raise FileExistsError("pre-label seal or component map already exists")

    preregistration = _resolve(args.preregistration)
    prereg, prereg_identity, final_freeze_identity = _verify_final_freeze(
        preregistration, _resolve(args.final_freeze_seal)
    )
    expected_prereg = {
        "path": str(preregistration),
        "bytes": args.preregistration_bytes,
        "sha256": args.preregistration_sha256.lower(),
    }
    if prereg_identity != expected_prereg:
        raise ValueError("frozen preregistration identity differs from CLI declaration")
    if prereg.get("profileId") != args.profile_id:
        raise ValueError("preregistration profile differs from the requested profile")

    # This replays the raw component graph and exact target-blind filtering
    # before any filtered artifact is trusted.  Discarded roots therefore
    # cannot conceal a transposition, orbit link, malformed child, or score key.
    raw_roots, raw_children, forbidden, forbidden_pins = (
        _verify_feasibility_bundle(prereg, args)
    )

    roots_path = _resolve(args.roots)
    root_manifest_path = _resolve(args.roots_manifest)
    children_path = _resolve(args.children)
    child_manifest_path = _resolve(args.children_manifest)
    sampler_seal_path = _resolve(args.sampler_seal)
    teacher_engine = _resolve(args.engine)
    roots = [record for _, record in _jsonl(roots_path)]
    root_manifest, root_manifest_identity = _load_json_snapshot(root_manifest_path)
    children = _load_unique_items(children_path)
    child_manifest, child_manifest_identity = _load_json_snapshot(child_manifest_path)
    sampler_seal, sampler_seal_identity = _load_json_snapshot(sampler_seal_path)

    frozen_cli = {
        "roots": (roots_path, "roots"),
        "roots manifest": (root_manifest_path, "rootsManifest"),
        "children": (children_path, "children"),
        "children manifest": (child_manifest_path, "childrenManifest"),
        "teacher engine": (teacher_engine, "teacherEngineExecutable"),
    }
    for description, (actual_path, identity_name) in frozen_cli.items():
        if _identity(actual_path) != _frozen_identity(prereg, identity_name):
            raise ValueError(
                f"CLI {description} differs from finalFreezeIdentities.{identity_name}"
            )
    if _producer_identity()["omegaDecisionTeacher"] != _frozen_identity(
        prereg, "decisionTeacherSource"
    ):
        raise ValueError("running decision teacher differs from final freeze")

    root_ids = [str(item.get("rootId") or "") for item in roots]
    if not root_ids or any(not value for value in root_ids) or len(root_ids) != len(set(root_ids)):
        raise ValueError("pre-label roots contain duplicate/empty root IDs")
    exact_owner: dict[str, str] = {}
    signature_owner: dict[str, str] = {}
    for root in roots:
        ofen = _normalized_ofen(root.get("ofen"))
        exact, _, signatures = deep._leakage_keys(ofen)
        group = str(root.get("groupId") or "")
        if not group:
            raise ValueError("pre-label root lacks a source group")
        component = str(root.get("rawLeakageComponentId") or "")
        if re.fullmatch(r"raw-decision-component:[0-9a-f]{64}", component) is None:
            raise ValueError("pre-label root lacks its authenticated raw component")
        prior_exact = exact_owner.setdefault(exact, component)
        if prior_exact != component:
            raise ValueError("exact root link was not inherited into one raw component")
        for signature in signatures:
            prior = signature_owner.setdefault(signature, component)
            if prior != component:
                raise ValueError(
                    "conservative root-orbit link was not inherited into one raw component"
                )
    if _has_target_like_key(roots) or _has_target_like_key(children):
        raise ValueError("target/score-like fields exist before the label boundary")

    if (
        root_manifest.get("profileId") != args.profile_id
        or root_manifest.get("freshnessMarker") != args.freshness_marker
        or int(root_manifest.get("policy", {}).get("sourceSeed", -1)) != SOURCE_SEED
        or int(root_manifest.get("policy", {}).get("seed", -1))
        != ROOT_SELECTION_SEED
        or root_manifest.get("output") != _identity(roots_path)
    ):
        raise ValueError("root manifest violates the frozen source/profile contract")
    sources = root_manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != 1:
        raise ValueError("root manifest must pin exactly one source telemetry ledger")
    source_audit = sources[0]
    if not isinstance(source_audit, dict):
        raise ValueError("root manifest source audit is malformed")
    source_identity_checks = {
        "source": "sourceEvents",
        "openingSuite": "sourceOpeningSuite",
        "sourceMatchConfig": "sourceMatchConfig",
        "sourceMatchHarnessAssembly": "sourceMatchHarnessAssembly",
        "rootSamplerAssembly": "rootSamplerAssembly",
    }
    for audit_key, frozen_name in source_identity_checks.items():
        if source_audit.get(audit_key) != _frozen_identity(prereg, frozen_name):
            raise ValueError(
                f"root manifest {audit_key} differs from finalFreezeIdentities.{frozen_name}"
            )
    suite_provenance = source_audit.get("openingSuiteProvenance")
    if not isinstance(suite_provenance, dict) or (
        suite_provenance.get("rootSamplerSha256")
        != _frozen_identity(prereg, "rootSamplerAssembly")["sha256"]
        or suite_provenance.get("rootSamplerChessLibSha256")
        != _frozen_identity(prereg, "chessLibAssembly")["sha256"]
        or suite_provenance.get("rootPoolSha256")
        != _frozen_identity(prereg, "sourceRootPool")["sha256"]
        or suite_provenance.get("rootPoolManifestSha256")
        != _frozen_identity(prereg, "sourceRootPoolManifest")["sha256"]
        or suite_provenance.get("rootPoolSealSha256")
        != _frozen_identity(prereg, "sourceRootPoolSeal")["sha256"]
        or suite_provenance.get("sourceBuilderSha256")
        != _frozen_identity(prereg, "sourceOpeningBuilderSource")["sha256"]
        or suite_provenance.get("networkFormatSha256")
        != _frozen_identity(prereg, "networkFormatPythonSource")["sha256"]
        or suite_provenance.get("pythonRuntimeManifestSha256")
        != _frozen_identity(prereg, "pythonRuntimeManifest")["sha256"]
    ):
        raise ValueError("opening-suite producer/pool provenance differs from final freeze")
    if (
        source_audit.get("dataProfileId") != DATA_PROFILE_ID
        or source_audit.get("targetInformationRead") is not False
        or int(source_audit.get("searchScoresDecoded", -1)) != 0
        or int(source_audit.get("gameOutcomesDecoded", -1)) != 0
        or source_audit.get("literalGameResultDecoded") is not False
        or source_audit.get("abBaPairsComplete") is not True
        or int(source_audit.get("abandonedAttempts", -1)) != 0
        or source_audit.get("colorSwapsComplete") is not True
        or source_audit.get("sameOpeningWithinPair") is not True
        or source_audit.get("sourceNodeMode") != "nodes"
        or int(source_audit.get("sourceNodes", 0)) != SOURCE_NODES
        or int(source_audit.get("sourceRepeats", 0)) != 1
        or int(source_audit.get("sourceMaxPlies", 0)) != SOURCE_MAX_PLIES
        or int(source_audit.get("sourceAbsoluteMaxPlies", 0))
        != SOURCE_MAX_PLIES
        or set(source_audit.get("sourceSafetyCounters", {}).values()) != {0}
    ):
        raise ValueError("root manifest source provenance/safety audit is not closed")
    required_engine_sha = str(
        root_manifest.get("policy", {}).get("requiredEngineSha256", "")
    ).lower()
    if (
        not HEX_SHA256.fullmatch(required_engine_sha)
        or _identity(teacher_engine)["sha256"] != required_engine_sha
    ):
        raise ValueError("teacher engine differs from frozen on-policy HCE engine")
    root_sampler_chesslib = _verify_identity(
        source_audit.get("rootSamplerChessLibAssembly"),
        "root source audit root-sampler ChessLib",
    )
    reverified_source_contract = _load_source_contract(
        Path(_frozen_identity(prereg, "sourceOpeningSuite")["path"]),
        Path(_frozen_identity(prereg, "sourceMatchConfig")["path"]),
        Path(_frozen_identity(prereg, "sourceMatchCompletionSeal")["path"]),
        Path(_frozen_identity(prereg, "sourceMatchHarnessAssembly")["path"]),
        Path(_frozen_identity(prereg, "rootSamplerAssembly")["path"]),
        Path(root_sampler_chesslib["path"]),
        Path(_frozen_identity(prereg, "sourceRootPool")["path"]),
        Path(_frozen_identity(prereg, "sourceRootPoolManifest")["path"]),
        Path(_frozen_identity(prereg, "sourceRootPoolSeal")["path"]),
        required_engine_sha,
        DATA_PROFILE_ID,
        args.freshness_marker,
    )
    _verify_source_audit_evidence(
        prereg,
        source_audit,
        reverified_contract=reverified_source_contract,
    )
    required_per_phase = args.roots_per_phase + args.reserve_roots_per_phase
    phase_counts = Counter(str(item.get("phase")) for item in roots)
    if any(phase_counts[phase] != required_per_phase for phase in PHASES):
        raise ValueError("root phase quota/reserve inventory differs from preregistration")
    if any(
        sum(
            item.get("phase") == phase
            and item.get("candidateRole") == role
            for item in roots
        )
        != expected
        for phase in PHASES
        for role, expected in (
            ("primary", args.roots_per_phase),
            ("reserve", args.reserve_roots_per_phase),
        )
    ):
        raise ValueError("root primary/reserve roles differ from frozen quotas")

    if child_manifest.get("input") != _identity(roots_path) or child_manifest.get(
        "output"
    ) != _identity(children_path):
        raise ValueError("legal-child manifest does not pin the frozen roots/children")
    coverage = child_manifest.get("coverage", {})
    if (
        int(coverage.get("roots", -1)) != len(roots)
        or int(coverage.get("uniqueRootIds", -1)) != len(roots)
        or int(coverage.get("children", -1)) != len(children)
        or int(coverage.get("uniqueChildIds", -1)) != len(children)
        or int(coverage.get("zeroChildRoots", -1)) != 0
    ):
        raise ValueError("legal-child expansion coverage is incomplete")
    children_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    root_by_id = {str(item["rootId"]): item for item in roots}
    for child in children:
        root_id = str(child.get("rootId") or "")
        root = root_by_id.get(root_id)
        if root is None:
            raise ValueError("legal-child output contains an unknown root")
        if any(
            child.get(field) != root.get(root_field)
            for field, root_field in (
                ("groupId", "groupId"),
                ("sourceGameId", "sourceGameId"),
                ("phase", "phase"),
                ("parentOfen", "ofen"),
            )
        ):
            raise ValueError("legal child does not preserve frozen root metadata")
        children_by_root[root_id].append(child)
    if set(children_by_root) != set(root_by_id) or any(
        not items for items in children_by_root.values()
    ):
        raise ValueError("legal expansion omitted a root")
    if (
        sampler_seal.get("kind") != "omega-decision-sampler-completion-seal"
        or sampler_seal.get("input") != _identity(roots_path)
        or sampler_seal.get("output") != _identity(children_path)
        or sampler_seal.get("manifest") != child_manifest_identity
        or sampler_seal.get("finalStageSeal") is not True
    ):
        raise ValueError("sampler completion seal is invalid")
    sampler_producer = _strict_keys(
        sampler_seal.get("producer"),
        {"samplerAssembly", "chessLibAssembly", "framework"},
        "sampler completion producer",
    )
    if sampler_producer["samplerAssembly"] != _frozen_identity(
        prereg, "decisionSamplerAssembly"
    ):
        raise ValueError("CLI sampler completion differs from the final freeze")
    if sampler_producer["chessLibAssembly"] != _frozen_identity(
        prereg, "chessLibAssembly"
    ):
        raise ValueError("CLI sampler ChessLib differs from the final freeze")
    if not isinstance(sampler_producer["framework"], str):
        raise ValueError("sampler framework provenance is malformed")

    planned_paths = {
        "shallowLedger": str(_resolve(args.shallow_ledger)),
        "deepLedger": str(_resolve(args.deep_ledger)),
        "selectedChildren": str(_resolve(args.selected_children)),
        "labels": str(_resolve(args.labels_output)),
    }
    data_root = _resolve(prereg["namespaces"]["dataRoot"])
    expected_paths = {
        "shallowLedger": str(data_root / "shallow-results.jsonl"),
        "deepLedger": str(data_root / "deep-results.jsonl"),
        "selectedChildren": str(data_root / "selected-children.jsonl"),
        "labels": str(data_root / "decision-labels.jsonl"),
    }
    if planned_paths != expected_paths:
        raise ValueError("planned teacher artifacts differ from the final preregistration")
    if output != _resolve(prereg["namespaces"]["preLabelSeal"]):
        raise ValueError("pre-label output differs from the final preregistration")
    if component_map_path != data_root / "component-splits.jsonl":
        raise ValueError("component-map path differs from the frozen data namespace")
    for description, path_string in planned_paths.items():
        path = Path(path_string)
        companions = [
            path,
            Path(str(path) + ".lock.json"),
            Path(str(path) + ".active.claim.json"),
            Path(str(path) + ".manifest.json"),
            Path(str(path) + ".complete.manifest.json"),
        ]
        if any(candidate.exists() for candidate in companions):
            raise ValueError(f"{description} or a companion already exists before freeze")

    components, split_for_component = _inherited_component_splits(
        children,
        seed=args.split_seed,
        train_percent=args.train_percent,
        validation_percent=args.validation_percent,
    )
    map_rows: list[dict[str, Any]] = []
    groups = sorted({str(item["groupId"]) for item in children})
    for group in groups:
        group_roots = {
            str(item["rootId"]) for item in children if str(item["groupId"]) == group
        }
        component = components[group]
        map_rows.append(
            {
                "schemaVersion": 1,
                "kind": "omega-decision-component-split",
                "groupId": group,
                "leakageComponentId": component,
                "split": split_for_component[component],
                "rootIds": sorted(group_roots),
                "sourceGameIds": sorted(
                    {
                        str(item["sourceGameId"])
                        for item in children
                        if str(item["groupId"]) == group
                    }
                ),
                "phases": sorted(
                    {
                        str(item["phase"])
                        for item in children
                        if str(item["groupId"]) == group
                    },
                    key=PHASES.index,
                ),
            }
        )
    coverage_rows = [
        {
            "rootId": root_id,
            "phase": root_by_id[root_id]["phase"],
            "split": split_for_component[components[str(root_by_id[root_id]["groupId"])]],
        }
        for root_id in sorted(root_by_id)
    ]
    phase_split_coverage = _assert_all_phases_per_split(
        coverage_rows, description="pre-label component split"
    )
    _atomic_jsonl(component_map_path, map_rows)
    try:
        _atomic_json(
            output,
            {
                "schemaVersion": 1,
                "kind": "omega-decision-prelabel-freeze-seal",
                "profileId": args.profile_id,
                "status": "target-opaque-frozen-before-teacher-search",
                "createdUtc": _utc_now(),
                "declaration": {
                    "teacherSearchesPresentAtFreeze": False,
                    "targetOrScoreFieldsDecoded": 0,
                    "targetOrScoreFieldsEmitted": 0,
                    "componentAndSplitMapFrozenBeforeSearch": True,
                    "rawGraphAuthenticatedBeforeFiltering": True,
                    "rootFeasibilityReplayedAtFreeze": True,
                },
                "policy": {
                    "sourceSeed": SOURCE_SEED,
                    "rootSelectionSeed": ROOT_SELECTION_SEED,
                    "siblingExplorationSeed": SIBLING_EXPLORATION_SEED,
                    "componentSplitSeed": COMPONENT_SPLIT_SEED,
                    "trainPercent": args.train_percent,
                    "validationPercent": args.validation_percent,
                    "heldOutPercent": args.heldout_percent,
                    "splitNames": list(SPLITS),
                    "priorReusePolicy": PRIOR_REUSE_POLICY,
                },
                "coverage": {
                    "rawRoots": len(raw_roots),
                    "rawChildren": len(raw_children),
                    "roots": len(roots),
                    "children": len(children),
                    "sourceGroups": len(groups),
                    "components": len(set(components.values())),
                    "phaseSplitRootCounts": phase_split_coverage,
                    "forbiddenCatalogs": len(forbidden_pins),
                    "forbiddenCatalogPositions": sum(
                        int(pin["positionCount"]) for pin in forbidden_pins
                    ),
                    "priorReuseCollisions": 0,
                },
                "plannedArtifacts": planned_paths,
                "identities": {
                    "preregistration": prereg_identity,
                    "finalFreezeSeal": final_freeze_identity,
                    "rawRoots": _identity(_resolve(args.raw_roots)),
                    "rawRootsManifest": _identity(_resolve(args.raw_roots_manifest)),
                    "rawChildren": _identity(_resolve(args.raw_children)),
                    "rawChildrenManifest": _identity(
                        _resolve(args.raw_children_manifest)
                    ),
                    "rawSamplerCompletionSeal": _identity(
                        _resolve(args.raw_sampler_seal)
                    ),
                    "rootFeasibility": _identity(
                        _resolve(args.root_feasibility)
                    ),
                    "rootFeasibilityManifest": _identity(
                        _resolve(args.root_feasibility_manifest)
                    ),
                    "rootFeasibilitySeal": _identity(
                        _resolve(args.root_feasibility_seal)
                    ),
                    "roots": _identity(roots_path),
                    "rootManifest": root_manifest_identity,
                    "children": _identity(children_path),
                    "childrenManifest": child_manifest_identity,
                    "samplerCompletionSeal": sampler_seal_identity,
                    "componentMap": _identity(component_map_path),
                    "teacherEngine": _identity(teacher_engine),
                    "forbiddenCatalogs": forbidden_pins,
                    "producer": _producer_identity(),
                },
                "finalStageSeal": True,
            },
        )
    except BaseException:
        if component_map_path.exists():
            component_map_path.unlink()
        raise
    print(f"Frozen target-opaque pre-label boundary: {output}")


def _finalize(args: argparse.Namespace) -> None:
    selected_path = _resolve(args.selected)
    selected_manifest_path = _resolve(args.selected_manifest)
    deep_ledger_path = _resolve(args.deep_ledger)
    output = _resolve(args.output)
    manifest_path = _resolve(args.manifest or str(output) + ".manifest.json")
    if manifest_path != Path(str(output) + ".manifest.json"):
        raise ValueError("label manifest must be the frozen adjacent path")
    if output.exists() or manifest_path.exists():
        raise FileExistsError("finalized output or manifest already exists")
    prelabel, prelabel_identity, prereg = _verify_prelabel_seal(
        _resolve(args.prelabel_seal),
        _resolve(args.preregistration),
        _resolve(args.final_freeze_seal),
    )
    if str(output) != prelabel.get("plannedArtifacts", {}).get("labels"):
        raise ValueError("label output path differs from the pre-label freeze")
    if str(deep_ledger_path) != prelabel.get("plannedArtifacts", {}).get("deepLedger"):
        raise ValueError("final deep ledger differs from the pre-label freeze")
    selected = _load_unique_items(selected_path)
    selected_manifest, _ = _load_json_snapshot(selected_manifest_path)
    if (
        selected_manifest.get("kind") != "omega-decision-four-child-selection"
        or selected_manifest.get("output") != _identity(selected_path)
        or selected_manifest.get("inputs", {}).get("preregistration")
        != prelabel["identities"]["preregistration"]
        or selected_manifest.get("inputs", {}).get("finalFreezeSeal")
        != prelabel["identities"]["finalFreezeSeal"]
        or selected_manifest.get("inputs", {}).get("prelabelFreeze")
        != prelabel_identity
        or selected_manifest.get("finalStageSeal") is not True
    ):
        raise ValueError("finalizer selected manifest is invalid")
    _verify_producer_identity(
        selected_manifest.get("producer"), "finalizer selected producer"
    )
    deep_completion = _require_completed_ledger(deep_ledger_path, "deep")
    if deep_completion.get("prelabelFreeze") != prelabel_identity:
        raise ValueError("deep completion belongs to another pre-label freeze")
    if (
        deep_completion.get("preregistration")
        != prelabel["identities"]["preregistration"]
        or deep_completion.get("finalFreezeSeal")
        != prelabel["identities"]["finalFreezeSeal"]
    ):
        raise ValueError("deep completion is not bound to the final freeze")
    deep_results = _successful_results(deep_ledger_path, "deep")
    component_map_path = Path(prelabel["identities"]["componentMap"]["path"])
    map_rows = [record for _, record in _jsonl(component_map_path)]
    map_by_group: dict[str, dict[str, Any]] = {}
    for row in map_rows:
        group = str(row.get("groupId") or "")
        if (
            not group
            or group in map_by_group
            or row.get("split") not in SPLITS
            or not str(row.get("leakageComponentId") or "")
        ):
            raise ValueError("frozen component map contains an invalid/duplicate group")
        map_by_group[group] = row
    by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in selected:
        by_root[str(item["rootId"])].append(item)
    incomplete_roots = {
        root_id
        for root_id, items in by_root.items()
        if len(items) != 4 or any(_item_id(item) not in deep_results for item in items)
    }
    groups_with_incomplete = {
        str(item["groupId"])
        for root_id in incomplete_roots
        for item in by_root[root_id]
    }
    eligible = {
        root_id: items
        for root_id, items in by_root.items()
        if str(items[0]["groupId"]) not in groups_with_incomplete
    }
    if args.roots_per_phase % 2:
        raise ValueError("final roots-per-phase must be even")
    roots_per_side = args.roots_per_phase // 2
    claimed_roots: dict[str, list[dict[str, Any]]] = {}
    for phase in PHASES:
        for side in ("w", "b"):
            candidates = sorted(
                (
                    (root_id, items)
                    for root_id, items in eligible.items()
                    if items[0].get("phase") == phase
                    and items[0].get("parentSideToMove") == side
                ),
                key=lambda item: (
                    str(item[1][0].get("selectionRank") or ""), item[0]
                ),
            )
            if len(candidates) < roots_per_side:
                raise ValueError(
                    f"{phase}/{side}: only {len(candidates)}/{roots_per_side} "
                    "complete whole-group roots survive deep search"
                )
            claimed_roots.update(candidates[:roots_per_side])
    claimed = [item for items in claimed_roots.values() for item in items]
    rows: list[dict[str, Any]] = []
    for root_id in sorted(claimed_roots):
        items = claimed_roots[root_id]
        ranked = sorted(
            items,
            key=lambda item: (
                -int(deep_results[_item_id(item)]["scoreCpRoot"]),
                str(item["move"]),
            ),
        )
        best = int(deep_results[_item_id(ranked[0])]["scoreCpRoot"])
        for rank, item in enumerate(ranked, 1):
            score = int(deep_results[_item_id(item)]["scoreCpRoot"])
            regret = best - score
            group = str(item["groupId"])
            mapping = map_by_group.get(group)
            if mapping is None or root_id not in mapping.get("rootIds", []):
                raise ValueError("selected root is absent from frozen component map")
            component = str(mapping["leakageComponentId"])
            rows.append(
                {
                    **item,
                    "kind": "omega-decision-deep-label",
                    "deepScoreCpRoot": score,
                    "deepScoreCpChildStm": -score,
                    "deepRank": rank,
                    "deepRegretCp": regret,
                    "teacherBest": rank == 1,
                    "rankingEligibleAgainstBest": regret >= 20,
                    "rankingGapCpCapped": min(regret, 600),
                    "leakageComponentId": component,
                    "split": mapping["split"],
                }
            )
    rows.sort(key=lambda item: (item["split"], item["rootId"], item["deepRank"]))
    split_groups: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        split_groups[row["split"]].add(row["leakageComponentId"])
    overlaps = {
        f"{left}-{right}": len(split_groups[left] & split_groups[right])
        for index, left in enumerate(SPLITS)
        for right in SPLITS[index + 1 :]
    }
    if any(overlaps.values()):
        raise AssertionError("leakage component split overlap")
    phase_split_coverage = _assert_all_phases_per_split(
        rows, description="final decision labels"
    )
    _atomic_jsonl(output, rows)
    try:
        _atomic_json(
            manifest_path,
            {
                "schemaVersion": 1,
                "kind": "omega-decision-label-manifest",
                "createdUtc": _utc_now(),
                "policy": {
                    "splitSeed": prelabel["policy"]["componentSplitSeed"],
                    "trainPercent": prelabel["policy"]["trainPercent"],
                    "validationPercent": prelabel["policy"]["validationPercent"],
                    "heldOutPercent": prelabel["policy"]["heldOutPercent"],
                    "childrenPerRoot": 4,
                    "rootsPerPhase": args.roots_per_phase,
                    "rankingGapIgnoreBelowCp": 20,
                    "rankingGapCapCp": 600,
                    "wholeSourceGroup": True,
                    "symmetryAndTranspositionComponents": True,
                },
                "coverage": {
                    "candidateRoots": len(by_root),
                    "roots": len(claimed_roots),
                    "labels": len(rows),
                    "sourceGroups": len({item["groupId"] for item in claimed}),
                    "components": len(
                        {item["leakageComponentId"] for item in rows}
                    ),
                    "phaseCounts": dict(Counter(item.get("phase") for item in rows)),
                    "rootPhaseSideCounts": {
                        f"{phase}/{side}": sum(
                            items[0].get("phase") == phase
                            and items[0].get("parentSideToMove") == side
                            for items in claimed_roots.values()
                        )
                        for phase in PHASES
                        for side in ("w", "b")
                    },
                    "splitRootCounts": {
                        split: len({item["rootId"] for item in rows if item["split"] == split})
                        for split in SPLITS
                    },
                    "phaseSplitRootCounts": phase_split_coverage,
                    "componentOverlapCounts": overlaps,
                    "incompleteRoots": len(incomplete_roots),
                    "rejectedIncompleteGroups": len(groups_with_incomplete),
                    "deepRejectedChildren": deep_completion.get("rejectedChildren"),
                },
                "inputs": {
                    "selected": _identity(selected_path),
                    "selectedManifest": _identity(selected_manifest_path),
                    "deepLedger": _identity(deep_ledger_path),
                    "preregistration": prelabel["identities"]["preregistration"],
                    "finalFreezeSeal": prelabel["identities"]["finalFreezeSeal"],
                    "prelabelFreeze": prelabel_identity,
                    "componentMap": _identity(component_map_path),
                },
                "producer": _producer_identity(),
                "finalStageSeal": True,
                "output": _identity(output),
            },
        )
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print(
        f"Finalized {len(rows)} labels from {len(claimed_roots)} complete roots: "
        f"{output}"
    )


def _self_test() -> None:
    assert PROFILE_ID == "king-state-v5-omega-decision-v2"
    assert DATA_PROFILE_ID == "omega-decision-v2"
    assert (SOURCE_SEED, ROOT_SELECTION_SEED, SIBLING_EXPLORATION_SEED) == (
        2026072301,
        2026072302,
        2026072303,
    )
    assert (
        RAW_ROOTS_PER_PHASE_SIDE,
        FEASIBLE_ROOTS_PER_PHASE_SIDE,
        DEEP_ROOTS_PER_PHASE_SIDE,
        FINAL_ROOTS_PER_PHASE_SIDE,
    ) == (896, 768, 640, 512)
    trajectory_fixture = {
        "trajectoryPairs": SOURCE_TRAJECTORY_PAIRS,
        "independentTrajectoriesPerPair": 2,
        "samplerExecutedTrajectories": SOURCE_TRAJECTORY_PAIRS * 2,
        "rowBearingTrajectories": SOURCE_TRAJECTORY_PAIRS * 2 - 1,
        "zeroRecordTrajectories": 1,
        "zeroRecordTrajectoryIds": ["random-pair-007708-ba"],
        "authenticatedTerminalTrajectories": 4304,
        "zeroRecordBound": (
            "zero-record trajectory count must not exceed authenticated "
            "terminalTrajectories"
        ),
    }
    assert _validate_source_trajectory_coverage(trajectory_fixture) == trajectory_fixture
    try:
        _validate_source_trajectory_coverage(
            {**trajectory_fixture, "zeroRecordTrajectoryIds": []}
        )
    except ValueError:
        pass
    else:
        raise AssertionError("inconsistent source trajectory coverage survived")
    rank_fixture = hashlib.sha256(
        (
            "omega-g5-feasible-root-v1\0"
            f"{ROOT_SELECTION_SEED}\0opening\0w\0group-a\0root-a"
        ).encode("utf-8")
    ).hexdigest()
    assert _expected_root_rank("root-a", "opening", "w", "group-a") == rank_fixture
    with tempfile.TemporaryDirectory(prefix="omega-g5-target-opaque-rank-") as raw:
        fixture_directory = Path(raw)
        fixture_ofen = (
            "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/"
            "CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
        )

        def write_target_variant(path: Path, score_cp: int, result: str) -> None:
            records = [
                {
                    "RecordType": "run",
                    "RunId": "target-opaque-run",
                    "ScoreSummary": {"cp": score_cp},
                },
                {
                    "RecordType": "gameStart",
                    "GameId": "opening-r001-ab",
                    "PairId": "opening-r001",
                    "Attempt": 1,
                    "OpeningId": "opening",
                    "WhiteEngineId": "hce-a",
                    "BlackEngineId": "hce-b",
                    "InitialOfen": fixture_ofen,
                    "OpeningMoves": [],
                },
                {
                    "RecordType": "ply",
                    "GameId": "opening-r001-ab",
                    "Attempt": 1,
                    "Ply": 1,
                    "EngineId": "hce-a",
                    "Color": "w",
                    "PreOfen": fixture_ofen,
                    "PostOfen": fixture_ofen.replace(" w ", " b "),
                    "BestMove": "j0j1",
                    "ScoreCp": score_cp,
                    "Search": {"Command": "go nodes 2000", "ScoreCp": score_cp},
                },
                {
                    "RecordType": "gameResult",
                    "GameId": "opening-r001-ab",
                    "PairId": "opening-r001",
                    "Attempt": 1,
                    "OpeningId": "opening",
                    "WhiteEngineId": "hce-a",
                    "BlackEngineId": "hce-b",
                    "Plies": 1,
                    "FinalOfen": fixture_ofen.replace(" w ", " b "),
                    "IllegalMoves": 0,
                    "IllegalPvs": 0,
                    "ProtocolFailures": 0,
                    "TimeForfeits": 0,
                    "Result": result,
                    "OutcomeDetails": {"scoreCp": score_cp},
                },
            ]
            path.write_text(
                "".join(
                    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                    for record in records
                ),
                encoding="utf-8",
            )

        first_events = fixture_directory / "target-a.jsonl"
        second_events = fixture_directory / "target-b.jsonl"
        write_target_variant(first_events, 75, "1-0")
        write_target_variant(second_events, -925, "0-1")
        first_structural, first_pin = _snapshot_source_events(first_events)
        second_structural, second_pin = _snapshot_source_events(second_events)
        if first_structural != second_structural:
            raise AssertionError("target mutation changed allowlisted source telemetry")
        if first_pin["sha256"] == second_pin["sha256"]:
            raise AssertionError("target mutation did not change raw events identity")

        def structural_identity(
            records: list[tuple[int, dict[str, Any]]],
        ) -> tuple[str, str, str]:
            run = next(record for _, record in records if record["RecordType"] == "run")
            ply = next(record for _, record in records if record["RecordType"] == "ply")
            ofen = _normalized_ofen(ply["PreOfen"])
            return _target_opaque_source_identity(
                run_id=run["RunId"],
                opening_suite_sha256="1" * 64,
                source_match_config_sha256="2" * 64,
                source_tag="rules-only-pair:random-pair-000001",
                game_id=ply["GameId"],
                attempt_number=ply["Attempt"],
                ply_number=ply["Ply"],
                ofen=ofen,
                phase=_phase(ofen),
                side=ofen.split()[1],
            )

        if structural_identity(first_structural) != structural_identity(second_structural):
            raise AssertionError(
                "target-bearing score/result mutation changed root selection identity"
            )
    for key in (
        "score",
        "scores",
        "rootScoresCp",
        "TARGETScores",
        "evaluationResults",
        "bestMoves",
        "bestMoveScores",
        "losses",
        "winProbabilities",
    ):
        if not _has_target_like_key({key: 1}):
            raise AssertionError(f"score-key quarantine missed {key!r}")
    if _has_target_like_key({"selectionRank": "0" * 64, "rootPvMove": "a0a1"}):
        raise AssertionError("target-blind rank/PV metadata was quarantined")

    children = []
    shallow: dict[str, dict[str, Any]] = {}
    for index in range(1, 13):
        child_id = f"child-{index:02d}"
        child = {
            "childId": child_id,
            "rootId": "root-a",
            "groupId": "group-a",
            "sourceGameId": "run-a:game-a:a1",
            "move": f"a0a{index:02d}",
            "rootPvMove": "a0a05",
            "parentOfen": "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1",
            "childOfen": "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] b - - 1 1",
        }
        children.append(child)
        shallow[child_id] = {"scoreCpRoot": 130 - index * 10}
    first = _choose_four(children, shallow, seed=SIBLING_EXPLORATION_SEED)
    second = _choose_four(children, shallow, seed=SIBLING_EXPLORATION_SEED)
    assert first == second and len(first) == 4
    assert len({_item_id(item) for item in first}) == 4
    assert Counter(item["selectionRole"] for item in first)["hard-negative"] == 1
    assert any(item["selectionRole"] == "source-pv" for item in first)

    # Exactly five children must succeed for every possible source-PV rank.
    five = children[:5]
    five_shallow = {item["childId"]: shallow[item["childId"]] for item in five}
    for pv_rank in range(1, 6):
        fixture = [dict(item, rootPvMove=five[pv_rank - 1]["move"]) for item in five]
        chosen = _choose_four(
            fixture, five_shallow, seed=SIBLING_EXPLORATION_SEED
        )
        assert len(chosen) == 4
        assert Counter(item["selectionRole"] for item in chosen) == {
            "source-pv": 1,
            "shallow-top": 2,
            "hard-negative": 1,
        }
    four = [dict(item, rootPvMove=five[3]["move"]) for item in five[:4]]
    try:
        _choose_four(four, five_shallow, seed=SIBLING_EXPLORATION_SEED)
    except ValueError as error:
        assert "fewer than five" in str(error)
    else:
        raise AssertionError("four children with source PV at shallow rank 4 survived")

    # Losing the 129th root from a 768-root bucket leaves 639, so that bucket
    # must fail independently even if another bucket has an extra root.
    assert FEASIBLE_ROOTS_PER_PHASE_SIDE - 129 == DEEP_ROOTS_PER_PHASE_SIDE - 1
    capacity_rows: list[dict[str, Any]] = []
    for phase in PHASES:
        for side in ("w", "b"):
            count = DEEP_ROOTS_PER_PHASE_SIDE
            if (phase, side) == ("opening", "w"):
                count -= 1
            elif (phase, side) == ("opening", "b"):
                count += 1
            capacity_rows.extend(
                {
                    "phase": phase,
                    "sideToMove": side,
                    "structurallyFeasible": True,
                    "selectionRank": f"{index:064x}",
                    "rootId": f"{phase}-{side}-{index}",
                }
                for index in range(count)
            )
    try:
        _ranked_feasible_buckets(
            capacity_rows, required_per_bucket=DEEP_ROOTS_PER_PHASE_SIDE
        )
    except ValueError as error:
        assert "opening/w" in str(error) and "cross-bucket borrowing" in str(error)
    else:
        raise AssertionError("the 129th per-bucket loss borrowed from another bucket")
    assert DEEP_ROOTS_PER_PHASE_SIDE - 129 == FINAL_ROOTS_PER_PHASE_SIDE - 1
    deep_capacity_rows: list[dict[str, Any]] = []
    for phase in PHASES:
        for side in ("w", "b"):
            count = FINAL_ROOTS_PER_PHASE_SIDE
            if (phase, side) == ("endgame", "b"):
                count -= 1
            elif (phase, side) == ("endgame", "w"):
                count += 1
            deep_capacity_rows.extend(
                {
                    "phase": phase,
                    "sideToMove": side,
                    "structurallyFeasible": True,
                    "selectionRank": f"{index:064x}",
                    "rootId": f"deep-{phase}-{side}-{index}",
                }
                for index in range(count)
            )
    try:
        _ranked_feasible_buckets(
            deep_capacity_rows, required_per_bucket=FINAL_ROOTS_PER_PHASE_SIDE
        )
    except ValueError as error:
        assert "endgame/b" in str(error)
    else:
        raise AssertionError("the 129th deep loss borrowed from another bucket")

    components, splits = _component_splits(
        first,
        seed=COMPONENT_SPLIT_SEED,
        train_percent=80.0,
        validation_percent=10.0,
    )
    assert len(set(components.values())) == 1
    assert len(splits) == 1
    inherited_fixture = [
        {**first[0], "groupId": "group-a", "rawLeakageComponentId": "raw-decision-component:x"},
        {**first[1], "groupId": "group-b", "rawLeakageComponentId": "raw-decision-component:x"},
    ]
    inherited, inherited_splits = _inherited_component_splits(
        inherited_fixture,
        seed=COMPONENT_SPLIT_SEED,
        train_percent=80.0,
        validation_percent=10.0,
    )
    assert inherited == {
        "group-a": "raw-decision-component:x",
        "group-b": "raw-decision-component:x",
    }
    assert len(inherited_splits) == 1
    with tempfile.TemporaryDirectory() as directory:
        fixture_directory = Path(directory)
        prior_audit, _ = _prior_source_audit_from_closure(GENERATION4_CLOSURE_PATH)
        _, prior_manifest = _build_synthetic_forbidden_catalog(
            fixture_directory,
            [
                {
                    "ofen": (
                        "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] "
                        "w - - 0 1"
                    ),
                    "sourceGameId": "prior-game",
                    "sourceRunId": "prior-run",
                }
            ],
            prior_audit=prior_audit,
        )

        def current_audit(
            tag: str, overrides: dict[str, dict[str, Any]] | None = None
        ) -> dict[str, dict[str, Any]]:
            overrides = overrides or {}
            result: dict[str, dict[str, Any]] = {}
            for index, name in enumerate(SOURCE_AUDIT_INPUT_IDENTITY_FIELDS):
                if name in overrides:
                    result[name] = overrides[name]
                    continue
                path = fixture_directory / f"{tag}-{index:02d}-{name}.bin"
                _atomic_bytes(path, f"{tag}:{name}\n".encode("utf-8"))
                result[name] = _identity(path)
            return result

        forbidden, _ = _load_forbidden_catalogs(
            [prior_manifest],
            enforce_canonical_source_inventory=False,
            _self_test_prior_audit=prior_audit,
        )
        expected_prior_data = {
            identity["sha256"] for identity in prior_audit["dataInputs"].values()
        }
        expected_prior_infrastructure = {
            identity["sha256"]
            for identity in prior_audit["reusableInfrastructure"].values()
        }
        if forbidden["sourceDataInputSha256"] != expected_prior_data:
            raise AssertionError("prior source audit did not expose exactly six data inputs")
        if forbidden["reusableInfrastructureSha256"] != expected_prior_infrastructure:
            raise AssertionError("prior source audit did not authenticate four infrastructure inputs")

        for role in SOURCE_DATA_INPUT_IDENTITY_FIELDS:
            audit = current_audit(
                f"data-collision-{role}",
                {role: prior_audit["dataInputs"][role]},
            )
            data, infrastructure, launch = _source_audit_identity_hashes(
                {"sources": [audit]}
            )
            if len(data & expected_prior_data) != 1 or infrastructure & expected_prior_data:
                raise AssertionError(f"data collision role was not isolated: {role}")
            if launch & expected_prior_data:
                raise AssertionError("launch provenance leaked into the data domain")

        audit = current_audit(
            "config-collision",
            {"sourceMatchConfig": prior_audit["dataInputs"]["sourceMatchConfig"]},
        )
        try:
            _assert_raw_prior_disjoint(
                [],
                [],
                {"sources": [audit]},
                [prior_manifest],
                enforce_canonical_source_inventory=False,
                _self_test_prior_audit=prior_audit,
            )
        except ValueError as error:
            if "'sourceDataInputCollisions': 1" not in str(error):
                raise
        else:
            raise AssertionError("provenance-only source config reuse survived prior audit")

        infrastructure_overrides = dict(prior_audit["reusableInfrastructure"])
        infrastructure_overrides["sourceMatchCompletionSeal"] = prior_audit["dataInputs"][
            "sourceMatchConfig"
        ]
        audit = current_audit("reused-infrastructure", infrastructure_overrides)
        _, _, counters = _assert_raw_prior_disjoint(
            [],
            [],
            {"sources": [audit]},
            [prior_manifest],
            enforce_canonical_source_inventory=False,
            _self_test_prior_audit=prior_audit,
        )
        if any(counters.values()):
            raise AssertionError("reusable infrastructure or launch provenance became data reuse")

        catalog_source_identity = _identity(
            fixture_directory / "synthetic-prior-source.jsonl"
        )
        audit = current_audit(
            "catalog-source-provenance",
            {"sourceRootPool": catalog_source_identity},
        )
        _, _, counters = _assert_raw_prior_disjoint(
            [],
            [],
            {"sources": [audit]},
            [prior_manifest],
            enforce_canonical_source_inventory=False,
            _self_test_prior_audit=prior_audit,
        )
        if any(counters.values()):
            raise AssertionError("catalog row provenance became source-data reuse")

        complete = current_audit("complete-audit")
        for role in SOURCE_AUDIT_INPUT_IDENTITY_FIELDS:
            missing = dict(complete)
            missing.pop(role)
            try:
                _source_audit_identity_hashes({"sources": [missing]})
            except ValueError as error:
                if role not in str(error):
                    raise
            else:
                raise AssertionError(f"missing source-audit identity survived: {role}")

        unexpected = dict(complete)
        unexpected["futureSourceInput"] = complete["source"]
        try:
            _source_audit_identity_hashes({"sources": [unexpected]})
        except ValueError as error:
            if "unregistered input identities" not in str(error):
                raise
        else:
            raise AssertionError("unregistered source-audit identity survived")

        suite_path = Path(complete["openingSuite"]["path"])
        _atomic_bytes(suite_path, b"tampered\n", no_clobber=False)
        try:
            _source_audit_identity_hashes({"sources": [complete]})
        except ValueError as error:
            if "openingSuite identity changed" not in str(error):
                raise
        else:
            raise AssertionError("tampered source-audit identity survived")

        bad_audit_digest_value = json.loads(
            prior_manifest.read_text(encoding="utf-8")
        )
        bad_audit_digest_value["priorSourceAuditsSha256"] = "0" * 64
        bad_audit_digest_manifest = (
            fixture_directory / "bad-prior-audit-digest.manifest.json"
        )
        _atomic_json(bad_audit_digest_manifest, bad_audit_digest_value)
        try:
            _load_forbidden_catalogs(
                [bad_audit_digest_manifest],
                enforce_canonical_source_inventory=False,
                _self_test_prior_audit=prior_audit,
            )
        except ValueError as error:
            if "prior source-audit inventory digest" not in str(error):
                raise
        else:
            raise AssertionError("tampered prior source-audit digest survived")

        swapped = json.loads(json.dumps(prior_audit))
        swapped["dataInputs"], swapped["reusableInfrastructure"] = (
            swapped["reusableInfrastructure"],
            swapped["dataInputs"],
        )
        swapped["dataInputsSha256"] = _canonical_digest(swapped["dataInputs"])
        swapped["reusableInfrastructureSha256"] = _canonical_digest(
            swapped["reusableInfrastructure"]
        )
        swapped_manifest_value = json.loads(prior_manifest.read_text(encoding="utf-8"))
        swapped_manifest_value["priorSourceAudits"] = [swapped]
        swapped_manifest_value["priorSourceAuditsSha256"] = _canonical_digest([swapped])
        swapped_manifest = fixture_directory / "swapped-prior-audit.manifest.json"
        _atomic_json(swapped_manifest, swapped_manifest_value)
        try:
            _load_forbidden_catalogs(
                [swapped_manifest],
                enforce_canonical_source_inventory=False,
                _self_test_prior_audit=prior_audit,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("prior data/infrastructure role swap survived")
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "no-clobber.json"
        _atomic_json(target, {"ok": True})
        try:
            _atomic_json(target, {"ok": False})
        except FileExistsError:
            pass
        else:
            raise AssertionError("no-clobber write unexpectedly succeeded")
    print("omega_decision_teacher_generation5 self-test passed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    forbidden = subparsers.add_parser("build-forbidden-catalog")
    forbidden.add_argument(
        "--source",
        action="append",
        required=True,
        help="explicit prior-generation JSON/JSONL artifact (repeatable; no globbing)",
    )
    forbidden.add_argument(
        "--source-projection-manifest",
        action="append",
        default=[],
        help=(
            "strict transitive manifest for a recognized G3 OFEN projection "
            "source (repeatable)"
        ),
    )
    forbidden.add_argument(
        "--prior-source-audit-closure",
        required=True,
        help="canonical, fully recomputed Generation-4 structural-abort closure",
    )
    forbidden.add_argument("--catalog", required=True)
    forbidden.add_argument("--manifest", required=True)
    forbidden.add_argument(
        "--created-utc",
        required=True,
        help="frozen manifest timestamp, formatted YYYY-MM-DDTHH:MM:SSZ",
    )

    verify_forbidden = subparsers.add_parser("verify-forbidden-catalog")
    verify_forbidden.add_argument("--manifest", required=True)

    roots = subparsers.add_parser("prepare-roots")
    roots.add_argument("--events", action="append", required=True)
    roots.add_argument("--opening-suite", required=True)
    roots.add_argument("--source-match-config", required=True)
    roots.add_argument("--source-match-completion-seal", required=True)
    roots.add_argument("--source-match-harness", required=True)
    roots.add_argument("--root-sampler", required=True)
    roots.add_argument("--root-sampler-chesslib", required=True)
    roots.add_argument("--source-root-pool", required=True)
    roots.add_argument("--source-root-pool-manifest", required=True)
    roots.add_argument("--source-root-pool-seal", required=True)
    roots.add_argument("--output", required=True)
    roots.add_argument("--manifest")
    roots.add_argument("--seed", type=int, required=True)
    roots.add_argument("--source-seed", type=int, required=True)
    roots.add_argument("--profile-id", required=True)
    roots.add_argument("--data-profile-id", required=True)
    roots.add_argument("--freshness-marker", required=True)
    roots.add_argument("--roots-per-phase", type=int, default=1536)
    roots.add_argument("--reserve-per-phase", type=int, default=256)
    roots.add_argument("--required-engine-sha256", required=True)

    expand = subparsers.add_parser("expand")
    expand.add_argument("--sampler", required=True)
    expand.add_argument("--dotnet", default="dotnet")
    expand.add_argument("--roots", required=True)
    expand.add_argument("--output", required=True)

    feasibility = subparsers.add_parser("feasibility-freeze")
    feasibility.add_argument("--raw-roots", required=True)
    feasibility.add_argument("--raw-roots-manifest", required=True)
    feasibility.add_argument("--raw-children", required=True)
    feasibility.add_argument("--raw-children-manifest", required=True)
    feasibility.add_argument("--raw-sampler-seal", required=True)
    feasibility.add_argument("--root-feasibility", required=True)
    feasibility.add_argument("--root-feasibility-seal", required=True)
    feasibility.add_argument(
        "--forbidden-position-manifest", action="append", required=True
    )
    feasibility.add_argument("--roots", required=True)
    feasibility.add_argument("--children", required=True)

    prelabel = subparsers.add_parser("prelabel-freeze")
    prelabel.add_argument("--raw-roots", required=True)
    prelabel.add_argument("--raw-roots-manifest", required=True)
    prelabel.add_argument("--raw-children", required=True)
    prelabel.add_argument("--raw-children-manifest", required=True)
    prelabel.add_argument("--raw-sampler-seal", required=True)
    prelabel.add_argument("--root-feasibility", required=True)
    prelabel.add_argument("--root-feasibility-manifest", required=True)
    prelabel.add_argument("--root-feasibility-seal", required=True)
    prelabel.add_argument("--roots", required=True)
    prelabel.add_argument("--roots-manifest", required=True)
    prelabel.add_argument("--children", required=True)
    prelabel.add_argument("--children-manifest", required=True)
    prelabel.add_argument("--sampler-seal", required=True)
    prelabel.add_argument("--engine", required=True)
    prelabel.add_argument("--preregistration", required=True)
    prelabel.add_argument("--final-freeze-seal", required=True)
    prelabel.add_argument("--preregistration-bytes", type=int, required=True)
    prelabel.add_argument("--preregistration-sha256", required=True)
    prelabel.add_argument("--profile-id", required=True)
    prelabel.add_argument("--freshness-marker", required=True)
    prelabel.add_argument(
        "--forbidden-position-manifest", action="append", required=True
    )
    prelabel.add_argument("--component-map", required=True)
    prelabel.add_argument("--output", required=True)
    prelabel.add_argument("--shallow-ledger", required=True)
    prelabel.add_argument("--selected-children", required=True)
    prelabel.add_argument("--deep-ledger", required=True)
    prelabel.add_argument("--labels-output", required=True)
    prelabel.add_argument("--split-seed", type=int, default=COMPONENT_SPLIT_SEED)
    prelabel.add_argument("--roots-per-phase", type=int, default=1536)
    prelabel.add_argument("--reserve-roots-per-phase", type=int, default=0)
    prelabel.add_argument("--train-percent", type=float, default=80.0)
    prelabel.add_argument("--validation-percent", type=float, default=10.0)
    prelabel.add_argument("--heldout-percent", type=float, default=10.0)

    for name, nodes in (("run-shallow", 2000), ("run-deep", 50000)):
        stage = subparsers.add_parser(name)
        stage.add_argument("--input", required=True)
        stage.add_argument("--input-manifest", required=True)
        stage.add_argument("--ledger", required=True)
        stage.add_argument("--engine", required=True)
        stage.add_argument("--prelabel-seal", required=True)
        stage.add_argument("--preregistration", required=True)
        stage.add_argument("--final-freeze-seal", required=True)
        stage.add_argument("--nodes", type=int, default=nodes)
        stage.add_argument("--timeout-seconds", type=float, default=180.0)
        stage.add_argument("--jobs", type=int, default=4)
        stage.add_argument("--max-attempts", type=int, default=3)
        stage.add_argument(
            "--recover-stale-claim",
            action="store_true",
            help="explicitly recover a same-host claim only when its owner PID is dead",
        )

    select = subparsers.add_parser("select")
    select.add_argument("--children", required=True)
    select.add_argument("--shallow-ledger", required=True)
    select.add_argument("--prelabel-seal", required=True)
    select.add_argument("--preregistration", required=True)
    select.add_argument("--final-freeze-seal", required=True)
    select.add_argument("--output", required=True)
    select.add_argument("--manifest")
    select.add_argument("--seed", type=int, required=True)
    select.add_argument(
        "--roots-per-phase", type=int, default=FINAL_ROOTS_PER_PHASE_SIDE * 2
    )
    select.add_argument(
        "--reserve-roots-per-phase",
        type=int,
        default=(DEEP_ROOTS_PER_PHASE_SIDE - FINAL_ROOTS_PER_PHASE_SIDE) * 2,
    )

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--selected", required=True)
    finalize.add_argument("--selected-manifest", required=True)
    finalize.add_argument("--deep-ledger", required=True)
    finalize.add_argument("--prelabel-seal", required=True)
    finalize.add_argument("--preregistration", required=True)
    finalize.add_argument("--final-freeze-seal", required=True)
    finalize.add_argument("--output", required=True)
    finalize.add_argument("--manifest")
    finalize.add_argument(
        "--roots-per-phase", type=int, default=FINAL_ROOTS_PER_PHASE_SIDE * 2
    )

    subparsers.add_parser("self-test")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "self-test":
        runtime_contract.verify_manifest(runtime_contract.DEFAULT_OUTPUT)
    if args.command == "build-forbidden-catalog":
        _build_forbidden_catalog_command(args)
    elif args.command == "verify-forbidden-catalog":
        _verify_forbidden_catalog_command(args)
    elif args.command == "prepare-roots":
        if (
            args.roots_per_phase != FEASIBLE_ROOTS_PER_PHASE_SIDE * 2
            or args.reserve_per_phase
            != (RAW_ROOTS_PER_PHASE_SIDE - FEASIBLE_ROOTS_PER_PHASE_SIDE) * 2
            or args.source_seed != SOURCE_SEED
            or args.seed != ROOT_SELECTION_SEED
            or args.profile_id != PROFILE_ID
            or args.data_profile_id != DATA_PROFILE_ID
            or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", args.freshness_marker)
            or not HEX_SHA256.fullmatch(args.required_engine_sha256.lower())
        ):
            raise ValueError("root quotas or frozen source/profile identities are invalid")
        _prepare_roots(args)
    elif args.command == "expand":
        _expand(args)
    elif args.command == "feasibility-freeze":
        _feasibility_freeze(args)
    elif args.command == "prelabel-freeze":
        if (
            args.profile_id != PROFILE_ID
            or args.split_seed != COMPONENT_SPLIT_SEED
            or args.roots_per_phase != FEASIBLE_ROOTS_PER_PHASE_SIDE * 2
            or args.reserve_roots_per_phase != 0
            or args.train_percent != 80.0
            or args.validation_percent != 10.0
            or args.heldout_percent != 10.0
            or args.train_percent + args.validation_percent + args.heldout_percent
            != 100.0
            or args.preregistration_bytes <= 0
            or not HEX_SHA256.fullmatch(args.preregistration_sha256.lower())
            or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", args.freshness_marker)
        ):
            raise ValueError("pre-label freeze differs from the frozen G5 contract")
        _prelabel_freeze(args)
    elif args.command == "run-shallow":
        if args.nodes != 2000 or args.jobs != SEARCH_WORKERS or args.max_attempts <= 0:
            raise ValueError("shallow nodes must be 2000; jobs must be exactly 4")
        _run_stage(args, "shallow")
    elif args.command == "select":
        if (
            args.roots_per_phase != FINAL_ROOTS_PER_PHASE_SIDE * 2
            or args.reserve_roots_per_phase
            != (DEEP_ROOTS_PER_PHASE_SIDE - FINAL_ROOTS_PER_PHASE_SIDE) * 2
            or args.seed != SIBLING_EXPLORATION_SEED
        ):
            raise ValueError("selection quotas are invalid")
        _select(args)
    elif args.command == "run-deep":
        if args.nodes != 50000 or args.jobs != SEARCH_WORKERS or args.max_attempts <= 0:
            raise ValueError("deep nodes must be 50000; jobs must be exactly 4")
        _run_stage(args, "deep")
    elif args.command == "finalize":
        if args.roots_per_phase != FINAL_ROOTS_PER_PHASE_SIDE * 2:
            raise ValueError("final root quota differs from the frozen G5 contract")
        _finalize(args)
    elif args.command == "self-test":
        _self_test()
    else:  # pragma: no cover
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
