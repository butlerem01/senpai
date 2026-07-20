#!/usr/bin/env python3
"""Freeze and verify generation-3 preselection match-history readiness.

This is a target-free adapter over the sealed generation-2 replay machinery.
It gives generation 3 its own canonical paths and provenance envelope while
retaining the exercised Omega-aware ChessLib converter and snapshot checks.
PGN recursive annotation variations are rejected by this adapter *before*
the converter is invoked and by the converter itself.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Sequence

# Import the generation-3 trainer before the compatibility replay core: the
# latter imports omega_nnue/NumPy, while the trainer's frozen runtime contract
# requires owning NumPy initialization.
import king_state_train_generation3 as training

import king_state_match_readiness as replay
import king_state_matches as core
import king_state_v3 as prelabel
import phase_incidence_preflight as incidence


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v3-deep-hce-v4"
SEAL_KIND = "omega-nnue-king-state-v3-match-readiness-seal"
PGN_RAV_POLICY = "rejected-before-ChessLib.PGN.LoadFile"

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO.parent
DOTNET_HOST = WORKSPACE / ".dotnet" / "dotnet.exe"
OUTPUT_DIR = REPO / "build-king-state-v3" / "readiness"
RUNTIME_DIR = OUTPUT_DIR / "runtime"
SNAPSHOT_PATH = OUTPUT_DIR / "history.positions.jsonl"
MANIFEST_PATH = OUTPUT_DIR / "history.manifest.json"
OPENING_PROJECTION_PATH = OUTPUT_DIR / "opening-sequences.jsonl"
OPENING_SNAPSHOT_PATH = OUTPUT_DIR / "opening-prefixes.jsonl"
OPENING_MANIFEST_PATH = OUTPUT_DIR / "opening-prefixes.manifest.json"
SEAL_PATH = OUTPUT_DIR / "king-state-v3-match-readiness.seal.json"
FUTURE_RUN_ROOT = WORKSPACE / "match-runs" / "output" / "king-state-v3"
GENERATION3_OUTPUT_DIR = REPO / "build-msvc" / "king-state-v3"
GENERATION3_ARTIFACT_PATHS = {
    "trainingPlan": GENERATION3_OUTPUT_DIR / "training-plan.json",
    "validationSelectionSeal": (
        GENERATION3_OUTPUT_DIR / "validation-selection.seal.json"
    ),
    "robustnessSeal": GENERATION3_OUTPUT_DIR / "robustness.seal.json",
    "offlineAccessClaim": (
        GENERATION3_OUTPUT_DIR / "offline-test.json.access.json"
    ),
    "offlineReport": GENERATION3_OUTPUT_DIR / "offline-test.json",
    "offlineSufficientAttestation": (
        GENERATION3_OUTPUT_DIR / "offline-test.sufficient.json"
    ),
    "offlineFailureSeal": (
        GENERATION3_OUTPUT_DIR / "offline-test.failure.json"
    ),
}

ADAPTER = Path(__file__).with_name("king_state_matches_generation3.py")
OFFLINE_WRAPPER = Path(__file__).with_name(
    "king_state_offline_generation3.py"
)
ADAPTER_CONTRACT = (
    REPO / "validation" / "omega-nnue-king-state-v3-match-adapter.json"
)
COMPATIBILITY_PROTOCOL = (
    REPO / "validation" / "omega-nnue-king-state-v3-match-protocol.json"
)
V3_PREREGISTRATION = (
    REPO / "validation" / "omega-nnue-king-state-v3-preregistration.json"
)
V3_AMENDMENT_001 = training.AMENDMENT
V3_AMENDMENT_002 = training.AMENDMENT_002
V3_AMENDMENT_003 = training.AMENDMENT_003
V3_AMENDMENT_CHAIN = (
    V3_AMENDMENT_001,
    V3_AMENDMENT_002,
    V3_AMENDMENT_003,
)
V3_AMENDMENT_CHAIN_PINS = (
    {
        "bytes": 8729,
        "sha256": (
            "846f43487558246edec871359f9f0a5dbd0377e69a3b95baee6ab6f47f32f78b"
        ),
    },
    {
        "bytes": 7192,
        "sha256": (
            "833886a638ebda8d062dfd195d22cfc482152730284d7da5f73a5066fb69c266"
        ),
    },
    {
        "bytes": 68249,
        "sha256": (
            "30d4eeb080d7a8d07d20b11abe8f3b2df19dc8c5e3c4ab354c523fcdce642469"
        ),
    },
)
ACTIVE_PRELABEL_SEAL = prelabel.PRELABEL_SEAL
DOTNET_HOST_FREEZE = prelabel.DEEP_LOCK
PHASE_IMPLEMENTATION = Path(incidence.__file__).resolve()
TRAINING_ORCHESTRATOR = Path(training.__file__).resolve()
PRELABEL_ORCHESTRATOR = Path(prelabel.__file__).resolve()
OPENING_REPLAY_PROJECT = (
    REPO / "tools" / "omega_nnue" / "OmegaOpeningPrefixReplay"
    / "OmegaOpeningPrefixReplay.csproj"
)
OPENING_REPLAY_SOURCE = OPENING_REPLAY_PROJECT.with_name("Program.cs")
OPENING_RUNTIME_DIR = OUTPUT_DIR / "opening-runtime"
FROZEN_HISTORY_RUNTIME = prelabel.HISTORY_SNAPSHOT_RUNTIME
FROZEN_OPENING_RUNTIME = prelabel.OPENING_REPLAY_RUNTIME
OMEGA_START = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
    "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
)
OPENING_PROJECTION_KIND = "omega-opening-prefix-replay-request-v1"
OPENING_PREFIX_KIND = "omega-opening-prefix-replay-position-v1"
OPENING_MANIFEST_KIND = "omega-opening-prefix-replay-manifest-v1"
OPENING_REPLAY_POLICY = {
    "recognizedSchemas": [
        "object with initialOfen plus exactly one of moves/openingMoves",
        "direct entry of an openings array with moves and optional initialOfen",
        (
            "exact lower-camel transcript game row with initialOfen, moves, "
            "sanMoves, and post-move positions"
        ),
    ],
    "fieldNameComparison": "Unicode str.casefold",
    "defaultInitialOfenOnlyForOpeningsEntry": OMEGA_START,
    "emitsEveryPrefixIncludingInitial": True,
    "targetsDecoded": 0,
    "pvOrSanDecoded": False,
    "transcriptSanMovesDecoded": False,
    "transcriptExpectedPositionsDecoded": True,
    "illegalOrMalformedSequence": "abort-before-readiness-publication",
    "helperTimeoutSeconds": prelabel.OPENING_REPLAY_TIMEOUT_SECONDS,
    "ignoredFreshSubtrees": [
        str(path.resolve())
        for path in (
            prelabel.DATA_DIR,
            GENERATION3_OUTPUT_DIR,
            OUTPUT_DIR,
            FUTURE_RUN_ROOT,
        )
    ],
}
OPENING_PROJECTION_AUDIT_FIELDS = (
    "requestCount",
    "requestSchemaCounts",
    "initialSourceCounts",
    "prefixRecordCount",
    "openingRequestCount",
    "openingPrefixRecordCount",
    "openingPostMovePrefixRecordCount",
    "openingExplicitInitialOccurrenceCount",
    "openingDefaultInitialOccurrenceCount",
    "newlyImplicitOpeningOccurrenceCount",
    "transcriptRequestCount",
    "transcriptMoveCount",
    "transcriptPrefixRecordCount",
    "uniqueOpeningSequenceCount",
    "uniqueOpeningSequenceMoveLengthCounts",
    "completeSequenceDeduplicatedRootAndPrefixOccurrenceCount",
    "completeSequenceDeduplicatedPostMovePrefixOccurrenceCount",
    "uniqueOpeningPrefixIdentityCount",
    "uniqueOpeningPostMovePrefixIdentityCount",
)
HISTORY_ROOTS = (
    REPO / "build-msvc",
    REPO / "build-king-state-v2",
    REPO / "validation",
    WORKSPACE / "match-runs",
    WORKSPACE / "match-runs" / "configs",
    WORKSPACE / "match-runs" / "output",
    WORKSPACE / "opening-audit",
    WORKSPACE / "fixtures",
    WORKSPACE / "omega-lab",
    WORKSPACE / "omega-lab" / "regressions",
    (
        WORKSPACE
        / "corechess-arena"
        / "Tools"
        / "OmegaMatch"
        / "Openings"
    ),
)
OPENING_HISTORY_ROOTS = (
    *prelabel.BASE_PRIOR_ROOTS,
    *(
        path
        for _root_id, path
        in prelabel.AMENDMENT_003_ADDITIONAL_ROOTS
    ),
)

IDENTITY_FIELDS = {"path", "bytes", "sha256"}
OPENING_REQUEST_FIELDS = {
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
    "initialOfen",
    "moves",
    "expectedPositions",
}
OPENING_PREFIX_FIELDS = (
    OPENING_REQUEST_FIELDS
    - {"initialOfen", "moves", "expectedPositions"}
) | {"ply", "move", "ofen"}
OPENING_MANIFEST_FIELDS = {
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
}
SEAL_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "effectiveBeforeWinnerSelection",
    "effectiveBeforeHeldOutAccess",
    "futureRunRootAbsentAtSeal",
    "futureRunRootMustRemainAbsentThroughOuterSeal",
    "generation3ArtifactAbsence",
    "historyRoots",
    "openingHistoryRoots",
    "ignoredFutureRunRoot",
    "pgnRecursiveAnnotationVariationPolicy",
    "snapshot",
    "manifest",
    "openingProjection",
    "openingSnapshot",
    "openingManifest",
    "openingReplayPolicy",
    "adapter",
    "adapterContract",
    "compatibilityCore",
    "compatibilityReplayCore",
    "compatibilityProtocol",
    "generation3Preregistration",
    "generation3AmendmentChain",
    "activePrelabelSeal",
    "prelabelOrchestrator",
    "trainingOrchestrator",
    "phaseIncidenceImplementation",
    "converterProject",
    "converterSource",
    "openingReplayProject",
    "openingReplaySource",
    "offlineWrapper",
    "dotnetHost",
    "dotnetHostFreeze",
    "runtimeAssembly",
    "openingRuntimeAssembly",
    "openingRulesAssembly",
    "audit",
    "openingAudit",
    "historySourceSet",
    "openingSourceSet",
    "pinnedFiles",
}


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _strict_object(path: Path, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"{label} contains non-finite JSON number {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} repeats JSON key {key!r}")
            result[key] = value
        return result

    value = json.loads(
        _resolve(path).read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=unique_object,
    )
    if type(value) is not dict:
        raise ValueError(f"{label} is not a JSON object")
    return value


def _strict_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return (
            set(actual) == set(expected)
            and all(
                _strict_equal(actual[key], expected[key]) for key in expected
            )
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _strict_equal(left, right)
            for left, right in zip(actual, expected)
        )
    return bool(actual == expected)


def _configure_replay() -> None:
    """Install generation-3 paths in the sealed replay helper."""

    replay.OUTPUT_DIR = OUTPUT_DIR
    replay.RUNTIME_DIR = RUNTIME_DIR
    replay.SNAPSHOT_PATH = SNAPSHOT_PATH
    replay.MANIFEST_PATH = MANIFEST_PATH
    replay.SEAL_PATH = SEAL_PATH
    replay.FUTURE_RUN_ROOT = FUTURE_RUN_ROOT
    replay.GENERATION2_OUTPUT_DIR = GENERATION3_OUTPUT_DIR
    replay.GENERATION2_ARTIFACT_PATHS = GENERATION3_ARTIFACT_PATHS
    replay.ADAPTER = ADAPTER
    replay.OFFLINE_WRAPPER = OFFLINE_WRAPPER
    replay.ADAPTER_CONTRACT = ADAPTER_CONTRACT
    replay.COMPATIBILITY_PROTOCOL = COMPATIBILITY_PROTOCOL
    replay.V2_PREREGISTRATION = V3_PREREGISTRATION
    # The compatibility core has a singular legacy slot.  Amendment-001 is
    # installed there; this adapter independently binds and verifies the
    # complete ordered generation-3 amendment chain.
    replay.V2_AMENDMENT = V3_AMENDMENT_001
    replay.ACTIVE_PRELABEL_SEAL = ACTIVE_PRELABEL_SEAL
    replay.DOTNET_HOST_FREEZE = DOTNET_HOST_FREEZE
    replay.HISTORY_ROOTS = HISTORY_ROOTS
    replay.prelabel = prelabel


def _amendment_chain_identities() -> list[dict[str, Any]]:
    """Verify and return the immutable ordered [001, 002, 003] chain."""

    if (
        _resolve(V3_AMENDMENT_001) != _resolve(training.AMENDMENT)
        or _resolve(V3_AMENDMENT_002) != _resolve(training.AMENDMENT_002)
        or _resolve(V3_AMENDMENT_003) != _resolve(training.AMENDMENT_003)
        or tuple(map(_resolve, V3_AMENDMENT_CHAIN))
        != (
            _resolve(training.AMENDMENT),
            _resolve(training.AMENDMENT_002),
            _resolve(training.AMENDMENT_003),
        )
    ):
        raise ValueError("generation-3 amendment chain path/order changed")
    trainer_pins = (
        (training.AMENDMENT_BYTES, training.AMENDMENT_SHA256),
        (training.AMENDMENT_002_BYTES, training.AMENDMENT_002_SHA256),
        (training.AMENDMENT_003_BYTES, training.AMENDMENT_003_SHA256),
    )
    initial: list[dict[str, Any]] = []
    for ordinal, (path, frozen, trainer_pin) in enumerate(
        zip(V3_AMENDMENT_CHAIN, V3_AMENDMENT_CHAIN_PINS, trainer_pins),
        start=1,
    ):
        if (
            type(trainer_pin[0]) is not int
            or trainer_pin[0] != frozen["bytes"]
            or type(trainer_pin[1]) is not str
            or trainer_pin[1].lower() != frozen["sha256"]
        ):
            raise ValueError(
                f"trainer amendment-{ordinal:03d} hard pin changed"
            )
        actual = core._identity(path)
        if (
            actual["bytes"] != frozen["bytes"]
            or actual["sha256"].lower() != frozen["sha256"]
        ):
            raise ValueError(
                f"generation-3 amendment-{ordinal:03d} exact bytes changed"
            )
        initial.append(actual)
    training._validate_amendment(
        V3_AMENDMENT_001,
        preregistration=V3_PREREGISTRATION,
    )
    training._validate_amendment_002(
        V3_AMENDMENT_002,
        prior_amendment=V3_AMENDMENT_001,
    )
    training._validate_amendment_003(
        V3_AMENDMENT_003,
        prior_amendment=V3_AMENDMENT_002,
    )
    repeated = [core._identity(path) for path in V3_AMENDMENT_CHAIN]
    if not _strict_equal(repeated, initial):
        raise ValueError("generation-3 amendment chain changed during verification")
    return repeated


def _generation3_absence_claim() -> dict[str, Any]:
    return {
        "checkedBeforeFreshReplay": True,
        "recheckedImmediatelyBeforeAtomicPublication": True,
        "artifacts": {
            name: {"path": str(_resolve(path)), "absent": True}
            for name, path in GENERATION3_ARTIFACT_PATHS.items()
        },
    }


def _verify_absence_claim(value: Any) -> None:
    if not _strict_equal(value, _generation3_absence_claim()):
        raise ValueError("generation-3 preselection absence claim changed")


def _require_absent_artifacts() -> None:
    present = [
        (name, _resolve(path))
        for name, path in GENERATION3_ARTIFACT_PATHS.items()
        if _resolve(path).exists()
    ]
    if present:
        name, path = present[0]
        raise FileExistsError(
            f"generation-3 {name} already exists before readiness: {path}"
        )


def _pgn_has_rav(text: str) -> bool:
    """Return true for a movetext parenthesis outside tags/comments."""

    in_tag = False
    in_tag_string = False
    escaped = False
    in_brace = False
    in_semicolon = False
    line_start = True
    index = 0
    while index < len(text):
        character = text[index]
        if in_semicolon:
            if character in "\r\n":
                in_semicolon = False
                line_start = True
            index += 1
            continue
        if in_brace:
            if character == "}":
                in_brace = False
            line_start = character in "\r\n"
            index += 1
            continue
        if in_tag:
            if in_tag_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_tag_string = False
            elif character == '"':
                in_tag_string = True
            elif character == "]":
                in_tag = False
            line_start = character in "\r\n"
            index += 1
            continue
        if line_start and character == "%":
            in_semicolon = True
        elif character == "[":
            in_tag = True
        elif character == "{":
            in_brace = True
        elif character == ";":
            in_semicolon = True
        elif character in "()":
            return True
        if character not in " \t":
            line_start = character in "\r\n"
        index += 1
    if in_tag or in_tag_string or in_brace:
        raise ValueError("PGN has an unterminated tag or brace comment")
    return False


def _reject_rav_sources(sources: Sequence[Path]) -> None:
    for path in sources:
        if path.suffix.lower() != ".pgn":
            continue
        try:
            text = _resolve(path).read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError(f"PGN is not valid UTF-8: {_resolve(path)}") from error
        if _pgn_has_rav(text):
            raise ValueError(
                "PGN recursive annotation variation is forbidden before "
                f"ChessLib replay: {_resolve(path)}"
            )


def _is_within(path: Path, root: Path) -> bool:
    try:
        _resolve(path).relative_to(_resolve(root))
        return True
    except ValueError:
        return False


def _opening_sources(
    roots: Sequence[Path] = OPENING_HISTORY_ROOTS,
) -> list[Path]:
    """Return the stable target-opaque JSON/JSONL history inventory."""

    ignored = tuple(
        map(
            _resolve,
            (
                prelabel.DATA_DIR,
                GENERATION3_OUTPUT_DIR,
                OUTPUT_DIR,
                FUTURE_RUN_ROOT,
            ),
        )
    )
    result: set[Path] = set()
    for supplied in roots:
        root = _resolve(supplied)
        if not root.is_dir():
            raise FileNotFoundError(f"opening-history root is absent: {root}")
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in {".json", ".jsonl"}
                and not any(_is_within(path, item) for item in ignored)
            ):
                result.add(_resolve(path))
    return sorted(result, key=lambda path: str(path).lower())


def _frozen_opening_projection_audit() -> dict[str, Any]:
    try:
        return {
            key: prelabel.EXPECTED_REPLAY_AUDIT[key]
            for key in OPENING_PROJECTION_AUDIT_FIELDS
        }
    except KeyError as error:
        raise ValueError(
            "frozen replay audit is missing a projection-owned field: "
            f"{error.args[0]}"
        ) from error


def _opening_projection_audit(
    requests: Sequence[dict[str, Any]],
    source_identities: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Derive the frozen completeness audit solely from the projection."""

    schema_counts: dict[str, int] = {}
    initial_counts: dict[str, int] = {}
    opening_sequences: set[tuple[str, tuple[str, ...]]] = set()
    transcript_moves = 0
    opening_prefixes = 0
    transcript_prefixes = 0
    for request in requests:
        schema = str(request["schema"])
        initial_source = str(request["initialSource"])
        schema_counts[schema] = schema_counts.get(schema, 0) + 1
        initial_counts[initial_source] = (
            initial_counts.get(initial_source, 0) + 1
        )
        if schema == "transcript":
            transcript_moves += len(request["moves"])
            transcript_prefixes += len(request["moves"]) + 1
        else:
            opening_prefixes += len(request["moves"]) + 1
            opening_sequences.add(
                (
                    prelabel._normalize_full_ofen(
                        request["initialOfen"],
                        label="readiness opening projection initial OFEN",
                    ),
                    tuple(move.casefold() for move in request["moves"]),
                )
            )
    unique_lengths: dict[str, int] = {}
    for _initial, moves in opening_sequences:
        key = str(len(moves))
        unique_lengths[key] = unique_lengths.get(key, 0) + 1
    unique_prefixes = sum(
        len(moves) + 1 for _initial, moves in opening_sequences
    )
    opening_prefix_identities = {
        (initial, moves[:ply])
        for initial, moves in opening_sequences
        for ply in range(len(moves) + 1)
    }
    opening_postmove_prefix_identities = {
        identity
        for identity in opening_prefix_identities
        if identity[1]
    }
    opening_request_count = (
        schema_counts.get("schedule-moves", 0)
        + schema_counts.get("event-opening-moves", 0)
    )
    opening_default_initials = sum(
        1
        for request in requests
        if request["schema"] != "transcript"
        and request["initialSource"] == "official-default"
    )
    opening_post_move_prefixes = (
        opening_prefixes - opening_request_count
    )
    frozen_summary = {
        "requestCount": len(requests),
        "requestSchemaCounts": {
            key: schema_counts[key] for key in sorted(schema_counts)
        },
        "initialSourceCounts": {
            key: initial_counts[key] for key in sorted(initial_counts)
        },
        "prefixRecordCount": opening_prefixes + transcript_prefixes,
        "openingRequestCount": opening_request_count,
        "openingPrefixRecordCount": opening_prefixes,
        "openingPostMovePrefixRecordCount": opening_post_move_prefixes,
        "openingExplicitInitialOccurrenceCount": (
            opening_request_count - opening_default_initials
        ),
        "openingDefaultInitialOccurrenceCount": (
            opening_default_initials
        ),
        "newlyImplicitOpeningOccurrenceCount": (
            opening_default_initials + opening_post_move_prefixes
        ),
        "transcriptRequestCount": schema_counts.get("transcript", 0),
        "transcriptMoveCount": transcript_moves,
        "transcriptPrefixRecordCount": transcript_prefixes,
        "uniqueOpeningSequenceCount": len(opening_sequences),
        "uniqueOpeningSequenceMoveLengthCounts": {
            key: unique_lengths[key]
            for key in sorted(unique_lengths, key=int)
        },
        "completeSequenceDeduplicatedRootAndPrefixOccurrenceCount": (
            unique_prefixes
        ),
        "completeSequenceDeduplicatedPostMovePrefixOccurrenceCount": (
            unique_prefixes - len(opening_sequences)
        ),
        "uniqueOpeningPrefixIdentityCount": len(
            opening_prefix_identities
        ),
        "uniqueOpeningPostMovePrefixIdentityCount": len(
            opening_postmove_prefix_identities
        ),
    }
    if not _strict_equal(
        frozen_summary, _frozen_opening_projection_audit()
    ):
        raise ValueError(
            "opening/transcript projection disagrees with the frozen "
            "target-free completeness audit"
        )
    return {
        "sources": len(source_identities),
        **frozen_summary,
        "moves": sum(len(request["moves"]) for request in requests),
        "sourceSetSha256": hashlib.sha256(
            "".join(
                f"{item['path']}\0{item['bytes']}\0{item['sha256']}\n"
                for item in source_identities
            ).encode("utf-8")
        ).hexdigest(),
    }


def _project_opening_sequences(
    sources: Sequence[Path],
    output: Path,
) -> dict[str, Any]:
    """Publish the deterministic target-free projection with CreateNew."""

    output = _resolve(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    source_identities = [core._identity(path) for path in sources]
    occurrences: list[dict[str, Any]] = []

    def project_record(
        payload: str,
        *,
        path: Path,
        identity: dict[str, Any],
        record: int,
    ) -> None:
        selected: list[dict[str, Any]] = []
        prelabel._lexical_scan_validated(
            payload,
            location=f"{path}:{record}",
            replay_requests=selected,
            source_record=record,
        )
        for request in sorted(
            selected,
            key=lambda item: int(item["sourceObjectOrdinal"]),
        ):
            occurrences.append(
                {
                    "sourcePath": identity["path"],
                    "sourceBytes": identity["bytes"],
                    "sourceSha256": identity["sha256"],
                    "sourceRecord": request["sourceRecord"],
                    "sourceObjectOrdinal": request[
                        "sourceObjectOrdinal"
                    ],
                    "sourceObjectIdentity": request[
                        "sourceObjectIdentity"
                    ],
                    "schema": request["schema"],
                    "initialSource": request["initialSource"],
                    "containerProof": request["containerProof"],
                    "initialOfen": request["initialOfen"],
                    "moves": request["moves"],
                    "expectedPositions": request["expectedPositions"],
                }
            )

    for path, identity in zip(sources, source_identities):
        path = _resolve(path)
        if path.suffix.lower() == ".jsonl":
            try:
                with path.open(
                    "r",
                    encoding="utf-8-sig",
                    newline=None,
                ) as stream:
                    for record, payload in enumerate(stream, 1):
                        if payload.strip():
                            project_record(
                                payload,
                                path=path,
                                identity=identity,
                                record=record,
                            )
            except UnicodeDecodeError as error:
                raise ValueError(
                    f"opening-history JSONL is not valid UTF-8: {path}"
                ) from error
        else:
            try:
                payload = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as error:
                raise ValueError(
                    f"opening-history JSON is not valid UTF-8: {path}"
                ) from error
            project_record(
                payload,
                path=path,
                identity=identity,
                record=1,
            )
        if not _strict_equal(core._identity(path), identity):
            raise ValueError(
                f"opening-history source changed during projection: {path}"
            )
    requests = occurrences
    projection_audit = _opening_projection_audit(
        requests, source_identities
    )
    try:
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            for request_id, request in enumerate(requests):
                value = {
                    "schemaVersion": 1,
                    "kind": OPENING_PROJECTION_KIND,
                    "requestId": request_id,
                    **request,
                }
                stream.write(
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
    except Exception:
        output.unlink(missing_ok=True)
        raise
    repeated = [core._identity(path) for path in sources]
    if not _strict_equal(repeated, source_identities):
        output.unlink(missing_ok=True)
        raise ValueError("opening-history source set changed during projection")
    return projection_audit


def _strict_json_line(text: str, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"{label} contains non-finite number {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} repeats JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{label}: {error}") from error
    if type(value) is not dict:
        raise ValueError(f"{label} is not an object")
    return value


def _iter_jsonl_objects(
    path: Path,
    label: str,
) -> Iterator[dict[str, Any]]:
    with _resolve(path).open(
        "r",
        encoding="utf-8",
        newline=None,
    ) as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                raise ValueError(f"{label}:{line_number}: blank record")
            yield _strict_json_line(line, f"{label}:{line_number}")


def _jsonl_objects(path: Path, label: str) -> list[dict[str, Any]]:
    return list(_iter_jsonl_objects(path, label))


def _identity_from_request(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": value.get("sourcePath"),
        "bytes": value.get("sourceBytes"),
        "sha256": value.get("sourceSha256"),
    }


def _load_opening_projection(
    path: Path,
    source_identities: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    known = {identity["path"]: identity for identity in source_identities}
    requests = _jsonl_objects(path, "opening projection")
    if not requests:
        raise ValueError("opening projection contains no requests")
    seen_objects: set[tuple[str, int, int, str]] = set()
    for expected_id, request in enumerate(requests):
        if set(request) != OPENING_REQUEST_FIELDS:
            raise ValueError(
                f"opening request {expected_id} field inventory changed"
            )
        if (
            type(request.get("schemaVersion")) is not int
            or request["schemaVersion"] != 1
            or request.get("kind") != OPENING_PROJECTION_KIND
            or type(request.get("requestId")) is not int
            or request["requestId"] != expected_id
        ):
            raise ValueError(
                f"opening request {expected_id} envelope changed"
            )
        identity = _identity_from_request(request)
        if (
            set(identity) != IDENTITY_FIELDS
            or type(identity["path"]) is not str
            or identity["path"] not in known
            or not _strict_equal(identity, known[identity["path"]])
        ):
            raise ValueError(
                f"opening request {expected_id} source is unpinned"
            )
        if (
            type(request.get("sourceRecord")) is not int
            or request["sourceRecord"] < 1
            or type(request.get("sourceObjectOrdinal")) is not int
            or request["sourceObjectOrdinal"] < 1
            or type(request.get("sourceObjectIdentity")) is not str
        ):
            raise ValueError(
                f"opening request {expected_id} source locator changed"
            )
        locator = (
            request["sourcePath"],
            request["sourceRecord"],
            request["sourceObjectOrdinal"],
            request["sourceObjectIdentity"],
        )
        if locator in seen_objects:
            raise ValueError("opening projection repeats a source object")
        seen_objects.add(locator)
        schema = request.get("schema")
        initial_source = request.get("initialSource")
        proof = request.get("containerProof")
        if schema not in {
            "schedule-moves",
            "event-opening-moves",
            "transcript",
        }:
            raise ValueError(f"opening request {expected_id} schema changed")
        if initial_source not in {"explicit", "official-default"}:
            raise ValueError(
                f"opening request {expected_id} initial source changed"
            )
        if proof not in {
            "openings-container",
            "direct-object",
            "event-game-start",
            "transcript-game",
        }:
            raise ValueError(
                f"opening request {expected_id} container proof changed"
            )
        if schema == "event-opening-moves" and (
            initial_source != "explicit" or proof != "event-game-start"
        ):
            raise ValueError(
                f"opening request {expected_id} has invalid event proof"
            )
        if schema == "schedule-moves" and proof not in {
            "openings-container",
            "direct-object",
        }:
            raise ValueError(
                f"opening request {expected_id} has invalid schedule proof"
            )
        if schema == "transcript" and (
            initial_source != "explicit" or proof != "transcript-game"
        ):
            raise ValueError(
                f"opening request {expected_id} has invalid transcript proof"
            )
        if initial_source == "official-default" and (
            schema != "schedule-moves"
            or proof != "openings-container"
            or request.get("initialOfen") != OMEGA_START
        ):
            raise ValueError(
                f"opening request {expected_id} has unproved default"
            )
        moves = request.get("moves")
        expected_positions = request.get("expectedPositions")
        if (
            type(request.get("initialOfen")) is not str
            or not request["initialOfen"]
            or request["initialOfen"] != request["initialOfen"].strip()
            or type(moves) is not list
            or len(moves) > 512
            or any(
                type(move) is not str
                or not move
                or move != move.strip()
                for move in moves
            )
        ):
            raise ValueError(
                f"opening request {expected_id} payload changed"
            )
        if schema == "transcript":
            if (
                type(expected_positions) is not list
                or len(expected_positions) != len(moves)
                or any(
                    type(position) is not str or not position.strip()
                    for position in expected_positions
                )
            ):
                raise ValueError(
                    f"opening request {expected_id} transcript parity changed"
                )
        elif expected_positions is not None:
            raise ValueError(
                f"opening request {expected_id} exposes unexpected positions"
            )
    return requests


def _rewrite_opening_manifest(
    manifest: Path,
    projection: Path,
    prefixes: Path,
    helper: Path,
    rules: Path,
) -> None:
    manifest = _resolve(manifest)
    value = _strict_object(manifest, "staged opening replay manifest")
    value["input"] = replay._identity_at(
        projection, OPENING_PROJECTION_PATH
    )
    value["output"] = replay._identity_at(
        prefixes, OPENING_SNAPSHOT_PATH
    )
    runtime = value.get("runtime")
    if type(runtime) is not dict or set(runtime) != {"helper", "rules"}:
        raise ValueError("opening replay runtime manifest changed")
    runtime["helper"] = replay._identity_at(
        helper, OPENING_RUNTIME_DIR / helper.name
    )
    runtime["rules"] = replay._identity_at(
        rules, OPENING_RUNTIME_DIR / rules.name
    )
    replacement = manifest.with_suffix(manifest.suffix + ".published.tmp")
    if replacement.exists():
        raise FileExistsError(replacement)
    replacement.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
    )
    replacement.replace(manifest)


def _validate_opening_replay(
    projection: Path,
    prefixes: Path,
    manifest: Path,
    sources: Sequence[Path],
    *,
    published: bool,
    helper: Path,
    rules: Path,
) -> dict[str, Any]:
    source_identities = [core._identity(path) for path in sources]
    requests = _load_opening_projection(projection, source_identities)
    projection_audit = _opening_projection_audit(
        requests, source_identities
    )
    summary = {
        key: projection_audit[key]
        for key in OPENING_PROJECTION_AUDIT_FIELDS
    }
    expected_prefixes = sum(
        len(request["moves"]) + 1 for request in requests
    )
    records = _iter_jsonl_objects(prefixes, "opening prefix replay")
    unique_inputs: set[str] = set()
    record_index = 0
    linkage = (
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
    for request in requests:
        for ply in range(len(request["moves"]) + 1):
            try:
                record = next(records)
            except StopIteration as error:
                raise ValueError(
                    "opening prefix replay cardinality changed: "
                    f"expected {expected_prefixes}, got {record_index}"
                ) from error
            record_index += 1
            if set(record) != OPENING_PREFIX_FIELDS:
                raise ValueError(
                    f"opening prefix record {record_index} fields changed"
                )
            if (
                type(record.get("schemaVersion")) is not int
                or record["schemaVersion"] != 1
                or record.get("kind") != OPENING_PREFIX_KIND
                or type(record.get("ply")) is not int
                or record["ply"] != ply
                or any(
                    not _strict_equal(record.get(field), request[field])
                    for field in linkage
                )
            ):
                raise ValueError(
                    f"opening prefix record {record_index} linkage changed"
                )
            expected_move = None if ply == 0 else request["moves"][ply - 1]
            if not _strict_equal(record.get("move"), expected_move):
                raise ValueError(
                    f"opening prefix record {record_index} move changed"
                )
            ofen = record.get("ofen")
            if type(ofen) is not str or not ofen:
                raise ValueError(
                    f"opening prefix record {record_index} lacks OFEN"
                )
            try:
                core.parse_ofen(ofen)
                unique_inputs.update(core._leakage_keys(ofen)[2])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    f"opening prefix record {record_index} has invalid OFEN"
                ) from error
            if request["schema"] == "transcript" and ply > 0:
                actual_position = prelabel._normalize_full_ofen(
                    ofen,
                    label=f"opening transcript replay row {record_index}",
                )
                expected_position = prelabel._normalize_full_ofen(
                    request["expectedPositions"][ply - 1],
                    label=(
                        f"opening transcript request "
                        f"{request['requestId']} expected position {ply}"
                    ),
                )
                if actual_position != expected_position:
                    raise ValueError(
                        "opening transcript full-position parity changed "
                        f"at request {request['requestId']} ply {ply}"
                    )
    try:
        next(records)
    except StopIteration:
        pass
    else:
        raise ValueError(
            "opening prefix replay cardinality changed: "
            f"expected {expected_prefixes}, got more"
        )

    value = _strict_object(manifest, "opening replay manifest")
    if set(value) != OPENING_MANIFEST_FIELDS:
        raise ValueError("opening replay manifest fields changed")
    if (
        type(value.get("schemaVersion")) is not int
        or value["schemaVersion"] != 1
        or value.get("kind") != OPENING_MANIFEST_KIND
        or value.get("variant") != "Omega"
        or value.get("rules") != "ChessLib legal coordinate replay"
        or type(value.get("sourceCount")) is not int
        or type(value.get("requests")) is not int
        or type(value.get("prefixes")) is not int
        or type(value.get("parseOrReplayErrors")) is not int
        or value["parseOrReplayErrors"] != 0
        or value["requests"] != len(requests)
        or value["prefixes"] != record_index
    ):
        raise ValueError("opening replay manifest summary changed")
    expected_input = (
        replay._identity_at(projection, OPENING_PROJECTION_PATH)
        if published
        else core._identity(projection)
    )
    expected_output = (
        replay._identity_at(prefixes, OPENING_SNAPSHOT_PATH)
        if published
        else core._identity(prefixes)
    )
    if (
        not _strict_equal(value.get("input"), expected_input)
        or not _strict_equal(value.get("output"), expected_output)
    ):
        raise ValueError("opening replay manifest I/O identity changed")
    referenced_paths = sorted(
        {request["sourcePath"] for request in requests},
        key=str.lower,
    )
    expected_referenced = [
        next(
            identity
            for identity in source_identities
            if identity["path"] == path
        )
        for path in referenced_paths
    ]
    if (
        value["sourceCount"] != len(expected_referenced)
        or not _strict_equal(value.get("sourceSet"), expected_referenced)
    ):
        raise ValueError("opening replay manifest source set changed")
    runtime = value.get("runtime")
    if type(runtime) is not dict or set(runtime) != {"helper", "rules"}:
        raise ValueError("opening replay manifest runtime changed")
    expected_helper = (
        replay._identity_at(
            helper, OPENING_RUNTIME_DIR / helper.name
        )
        if published
        else core._identity(helper)
    )
    expected_rules = (
        replay._identity_at(rules, OPENING_RUNTIME_DIR / rules.name)
        if published
        else core._identity(rules)
    )
    if (
        not _strict_equal(runtime.get("helper"), expected_helper)
        or not _strict_equal(runtime.get("rules"), expected_rules)
    ):
        raise ValueError("opening replay runtime/rules identity changed")
    return {
        "sourcesScanned": len(source_identities),
        "sourcesReplayed": len(expected_referenced),
        **summary,
        "moves": projection_audit["moves"],
        "uniqueLeakageInputs": len(unique_inputs),
        "targetFieldsDecoded": 0,
        "pvOrSanDecoded": False,
        "transcriptFullSixFieldParityPassed": True,
        "projection": projection_audit,
    }


def _source_inventory() -> list[Path]:
    _configure_replay()
    paths = {
        *replay._source_inventory(),
        _resolve(Path(__file__)),
        _resolve(TRAINING_ORCHESTRATOR),
        _resolve(PRELABEL_ORCHESTRATOR),
        _resolve(PHASE_IMPLEMENTATION),
        _resolve(OPENING_REPLAY_PROJECT),
        _resolve(OPENING_REPLAY_SOURCE),
        *map(_resolve, V3_AMENDMENT_CHAIN),
    }
    absent = [path for path in paths if not path.is_file()]
    if absent:
        raise FileNotFoundError(absent[0])
    return sorted(paths, key=lambda path: str(path).lower())


def _named_identities(
    dotnet: Path, runtime_assembly: Path
) -> dict[str, dict[str, Any]]:
    return {
        "snapshot": core._identity(SNAPSHOT_PATH),
        "manifest": core._identity(MANIFEST_PATH),
        "openingProjection": core._identity(OPENING_PROJECTION_PATH),
        "openingSnapshot": core._identity(OPENING_SNAPSHOT_PATH),
        "openingManifest": core._identity(OPENING_MANIFEST_PATH),
        "adapter": core._identity(ADAPTER),
        "adapterContract": core._identity(ADAPTER_CONTRACT),
        "compatibilityCore": core._identity(Path(core.__file__)),
        "compatibilityReplayCore": core._identity(Path(replay.__file__)),
        "compatibilityProtocol": core._identity(COMPATIBILITY_PROTOCOL),
        "generation3Preregistration": core._identity(V3_PREREGISTRATION),
        "activePrelabelSeal": core._identity(ACTIVE_PRELABEL_SEAL),
        "prelabelOrchestrator": core._identity(PRELABEL_ORCHESTRATOR),
        "trainingOrchestrator": core._identity(TRAINING_ORCHESTRATOR),
        "phaseIncidenceImplementation": core._identity(
            PHASE_IMPLEMENTATION
        ),
        "converterProject": core._identity(replay.CONVERTER_PROJECT),
        "converterSource": core._identity(replay.CONVERTER_SOURCE),
        "openingReplayProject": core._identity(OPENING_REPLAY_PROJECT),
        "openingReplaySource": core._identity(OPENING_REPLAY_SOURCE),
        "offlineWrapper": core._identity(OFFLINE_WRAPPER),
        "dotnetHost": core._identity(dotnet),
        "dotnetHostFreeze": core._identity(DOTNET_HOST_FREEZE),
        "runtimeAssembly": core._identity(runtime_assembly),
        "openingRuntimeAssembly": core._identity(
            OPENING_RUNTIME_DIR / "OmegaOpeningPrefixReplay.dll"
        ),
        "openingRulesAssembly": core._identity(
            OPENING_RUNTIME_DIR / "ChessLib.dll"
        ),
    }


def _merge_pins(
    identities: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for identity in identities:
        if set(identity) != IDENTITY_FIELDS:
            raise ValueError("readiness pin has invalid identity fields")
        path = identity.get("path")
        if type(path) is not str:
            raise ValueError("readiness pin path is not a string")
        previous = merged.setdefault(path, identity)
        if not _strict_equal(previous, identity):
            raise ValueError(f"conflicting readiness pin for {path}")
    return [
        merged[path]
        for path in sorted(merged, key=str.lower)
    ]


def _staged_pins(
    runtime: Path,
    sources: Sequence[Path],
    snapshot: Path,
    manifest: Path,
    history: Sequence[Path],
    opening_runtime: Path,
    opening_sources: Sequence[Path],
    projection: Path,
    opening_snapshot: Path,
    opening_manifest: Path,
) -> list[dict[str, Any]]:
    identities = replay._staged_pinned_files(
        runtime, sources, snapshot, manifest, history
    )
    identities.extend(core._identity(path) for path in opening_sources)
    identities.extend(
        (
            replay._identity_at(projection, OPENING_PROJECTION_PATH),
            replay._identity_at(
                opening_snapshot, OPENING_SNAPSHOT_PATH
            ),
            replay._identity_at(
                opening_manifest, OPENING_MANIFEST_PATH
            ),
        )
    )
    for path in replay._bundle_files(opening_runtime):
        relative = _resolve(path).relative_to(_resolve(opening_runtime))
        identities.append(
            replay._identity_at(path, OPENING_RUNTIME_DIR / relative)
        )
    return _merge_pins(identities)


def _published_pins(
    runtime: Path,
    sources: Sequence[Path],
    snapshot: Path,
    manifest: Path,
    history: Sequence[Path],
    opening_runtime: Path,
    opening_sources: Sequence[Path],
    projection: Path,
    opening_snapshot: Path,
    opening_manifest: Path,
) -> list[dict[str, Any]]:
    identities = replay._pinned_files(
        runtime, sources, snapshot, manifest, history
    )
    paths = {
        *map(_resolve, opening_sources),
        _resolve(projection),
        _resolve(opening_snapshot),
        _resolve(opening_manifest),
        *map(_resolve, replay._bundle_files(opening_runtime)),
    }
    identities.extend(
        core._identity(path)
        for path in sorted(paths, key=lambda item: str(item).lower())
    )
    return _merge_pins(identities)


def _prepare(dotnet: Path, output_dir: Path = OUTPUT_DIR) -> Path:
    _configure_replay()
    dotnet = replay._canonical_dotnet_host(dotnet)
    dotnet_identity = core._identity(dotnet)
    prelabel._verify_prebuilt_tooling()
    output_dir = _resolve(output_dir)
    if output_dir != _resolve(OUTPUT_DIR):
        raise ValueError(f"readiness output must be {_resolve(OUTPUT_DIR)}")
    replay._require_absent_or_empty_output(output_dir)
    _require_absent_artifacts()
    amendment_chain = _amendment_chain_identities()
    if _resolve(FUTURE_RUN_ROOT).exists():
        raise FileExistsError(
            f"future generation-3 match root already exists: {FUTURE_RUN_ROOT}"
        )
    prelabel._verify_seal()
    sources = _source_inventory()
    source_pins = [core._identity(path) for path in sources]
    history = replay._history_sources(HISTORY_ROOTS)
    _reject_rav_sources(history)
    history_pins = [core._identity(path) for path in history]
    opening_sources = _opening_sources(OPENING_HISTORY_ROOTS)
    opening_source_pins = [
        core._identity(path) for path in opening_sources
    ]
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".king-state-v3-readiness-stage-",
        dir=output_dir.parent,
    ) as raw_stage:
        staging_root = _resolve(Path(raw_stage))
        stage = staging_root / "publication"
        stage.mkdir()
        runtime = stage / "runtime"
        opening_runtime = stage / "opening-runtime"
        snapshot = stage / SNAPSHOT_PATH.name
        manifest = stage / MANIFEST_PATH.name
        opening_projection = stage / OPENING_PROJECTION_PATH.name
        opening_snapshot = stage / OPENING_SNAPSHOT_PATH.name
        opening_manifest = stage / OPENING_MANIFEST_PATH.name
        staged_seal = stage / SEAL_PATH.name
        assembly = runtime / "OmegaHistorySnapshot.dll"
        opening_assembly = (
            opening_runtime / "OmegaOpeningPrefixReplay.dll"
        )
        opening_rules = opening_runtime / "ChessLib.dll"
        shutil.copytree(
            _resolve(FROZEN_HISTORY_RUNTIME),
            runtime,
            copy_function=shutil.copy2,
        )
        if not assembly.is_file():
            raise FileNotFoundError(assembly)
        shutil.copytree(
            _resolve(FROZEN_OPENING_RUNTIME),
            opening_runtime,
            copy_function=shutil.copy2,
        )
        if not opening_assembly.is_file() or not opening_rules.is_file():
            raise FileNotFoundError(
                "opening replay helper or ChessLib rules assembly is absent"
            )
        staged_runtime_pins: dict[str, dict[str, Any]] = {}
        for label, staged_root, source_pin in (
            (
                "history snapshot",
                runtime,
                prelabel.HISTORY_SNAPSHOT_PINS[
                    "appLocalRuntimeBundle"
                ],
            ),
            (
                "opening replay",
                opening_runtime,
                prelabel.OPENING_REPLAY_RUNTIME_BUNDLE_PIN,
            ),
        ):
            expected_bundle = dict(source_pin)
            expected_bundle["path"] = str(_resolve(staged_root))
            staged_runtime_pins[label] = expected_bundle
            actual_bundle = (
                prelabel._application_runtime_bundle_identity(
                    expected_bundle
                )
            )
            if actual_bundle != expected_bundle:
                raise ValueError(
                    f"staged {label} runtime differs from frozen archive"
                )
        try:
            prelabel._run_pinned_dotnet(
                [str(dotnet), str(opening_assembly), "--self-test"],
                runtime_bundle_pin=staged_runtime_pins[
                    "opening replay"
                ],
                cwd=REPO,
                check=True,
                timeout=prelabel.OPENING_REPLAY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "opening replay helper self-test exceeded the frozen timeout"
            ) from error
        prelabel._run_pinned_dotnet(
            [
                str(dotnet),
                str(assembly),
                *(
                    item
                    for root in HISTORY_ROOTS
                    for item in ("--root", str(_resolve(root)))
                ),
                "--ignore-subtree",
                str(_resolve(FUTURE_RUN_ROOT)),
                "--output",
                str(snapshot),
                "--manifest",
                str(manifest),
            ],
            runtime_bundle_pin=staged_runtime_pins[
                "history snapshot"
            ],
            cwd=REPO,
            check=True,
        )
        replay._validate_snapshot(snapshot, manifest, history)
        replay._rewrite_manifest_snapshot_identity(
            manifest, snapshot, SNAPSHOT_PATH
        )
        audit = replay._validate_snapshot(
            snapshot,
            manifest,
            history,
            published_snapshot_path=SNAPSHOT_PATH,
        )
        projection_audit = _project_opening_sequences(
            opening_sources, opening_projection
        )
        try:
            prelabel._run_pinned_dotnet(
                [
                    str(dotnet),
                    str(opening_assembly),
                    "--input",
                    str(opening_projection),
                    "--output",
                    str(opening_snapshot),
                    "--manifest",
                    str(opening_manifest),
                ],
                runtime_bundle_pin=staged_runtime_pins[
                    "opening replay"
                ],
                cwd=REPO,
                check=True,
                timeout=prelabel.OPENING_REPLAY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "opening replay exceeded the frozen timeout"
            ) from error
        staged_opening_audit = _validate_opening_replay(
            opening_projection,
            opening_snapshot,
            opening_manifest,
            opening_sources,
            published=False,
            helper=opening_assembly,
            rules=opening_rules,
        )
        if not _strict_equal(
            staged_opening_audit.get("projection"),
            projection_audit,
        ):
            raise ValueError("staged opening projection audit changed")
        _rewrite_opening_manifest(
            opening_manifest,
            opening_projection,
            opening_snapshot,
            opening_assembly,
            opening_rules,
        )
        opening_audit = _validate_opening_replay(
            opening_projection,
            opening_snapshot,
            opening_manifest,
            opening_sources,
            published=True,
            helper=opening_assembly,
            rules=opening_rules,
        )
        if not _strict_equal(
            opening_audit.get("projection"),
            projection_audit,
        ):
            raise ValueError("published opening projection audit changed")

        replay._require_absent_or_empty_output(output_dir)
        _require_absent_artifacts()
        if _resolve(FUTURE_RUN_ROOT).exists():
            raise FileExistsError(
                "future generation-3 match root appeared during readiness"
            )
        if core._identity(replay._canonical_dotnet_host(dotnet)) != dotnet_identity:
            raise ValueError(".NET host changed during readiness")
        prelabel._verify_seal()
        current_sources = _source_inventory()
        replay._exact_identity_sequence(
            [core._identity(path) for path in current_sources],
            source_pins,
            "generation-3 readiness sources",
        )
        current_history = replay._history_sources(HISTORY_ROOTS)
        _reject_rav_sources(current_history)
        replay._exact_identity_sequence(
            [core._identity(path) for path in current_history],
            history_pins,
            "generation-3 readiness history",
        )
        current_opening_sources = _opening_sources(
            OPENING_HISTORY_ROOTS
        )
        replay._exact_identity_sequence(
            [core._identity(path) for path in current_opening_sources],
            opening_source_pins,
            "generation-3 opening history",
        )
        repeated = replay._validate_snapshot(
            snapshot,
            manifest,
            current_history,
            published_snapshot_path=SNAPSHOT_PATH,
        )
        if repeated != audit:
            raise ValueError("readiness replay audit changed before publication")
        repeated_opening = _validate_opening_replay(
            opening_projection,
            opening_snapshot,
            opening_manifest,
            current_opening_sources,
            published=True,
            helper=opening_assembly,
            rules=opening_rules,
        )
        if not _strict_equal(repeated_opening, opening_audit):
            raise ValueError(
                "opening replay audit changed before publication"
            )
        pins = _staged_pins(
            runtime,
            current_sources,
            snapshot,
            manifest,
            current_history,
            opening_runtime,
            current_opening_sources,
            opening_projection,
            opening_snapshot,
            opening_manifest,
        )
        named = {
            "snapshot": replay._identity_at(snapshot, SNAPSHOT_PATH),
            "manifest": replay._identity_at(manifest, MANIFEST_PATH),
            "openingProjection": replay._identity_at(
                opening_projection, OPENING_PROJECTION_PATH
            ),
            "openingSnapshot": replay._identity_at(
                opening_snapshot, OPENING_SNAPSHOT_PATH
            ),
            "openingManifest": replay._identity_at(
                opening_manifest, OPENING_MANIFEST_PATH
            ),
            "adapter": core._identity(ADAPTER),
            "adapterContract": core._identity(ADAPTER_CONTRACT),
            "compatibilityCore": core._identity(Path(core.__file__)),
            "compatibilityReplayCore": core._identity(Path(replay.__file__)),
            "compatibilityProtocol": core._identity(COMPATIBILITY_PROTOCOL),
            "generation3Preregistration": core._identity(V3_PREREGISTRATION),
            "generation3AmendmentChain": amendment_chain,
            "activePrelabelSeal": core._identity(ACTIVE_PRELABEL_SEAL),
            "prelabelOrchestrator": core._identity(PRELABEL_ORCHESTRATOR),
            "trainingOrchestrator": core._identity(TRAINING_ORCHESTRATOR),
            "phaseIncidenceImplementation": core._identity(
                PHASE_IMPLEMENTATION
            ),
            "converterProject": core._identity(replay.CONVERTER_PROJECT),
            "converterSource": core._identity(replay.CONVERTER_SOURCE),
            "openingReplayProject": core._identity(
                OPENING_REPLAY_PROJECT
            ),
            "openingReplaySource": core._identity(
                OPENING_REPLAY_SOURCE
            ),
            "offlineWrapper": core._identity(OFFLINE_WRAPPER),
            "dotnetHost": dict(dotnet_identity),
            "dotnetHostFreeze": core._identity(DOTNET_HOST_FREEZE),
            "runtimeAssembly": replay._identity_at(
                assembly, RUNTIME_DIR / assembly.name
            ),
            "openingRuntimeAssembly": replay._identity_at(
                opening_assembly,
                OPENING_RUNTIME_DIR / opening_assembly.name,
            ),
            "openingRulesAssembly": replay._identity_at(
                opening_rules,
                OPENING_RUNTIME_DIR / opening_rules.name,
            ),
        }
        seal = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": SEAL_KIND,
            "profileId": PROFILE_ID,
            "effectiveBeforeWinnerSelection": True,
            "effectiveBeforeHeldOutAccess": True,
            "futureRunRootAbsentAtSeal": True,
            "futureRunRootMustRemainAbsentThroughOuterSeal": True,
            "generation3ArtifactAbsence": _generation3_absence_claim(),
            "historyRoots": [str(_resolve(path)) for path in HISTORY_ROOTS],
            "openingHistoryRoots": [
                str(_resolve(path)) for path in OPENING_HISTORY_ROOTS
            ],
            "ignoredFutureRunRoot": str(_resolve(FUTURE_RUN_ROOT)),
            "pgnRecursiveAnnotationVariationPolicy": PGN_RAV_POLICY,
            "openingReplayPolicy": OPENING_REPLAY_POLICY,
            **named,
            "audit": audit,
            "openingAudit": opening_audit,
            "historySourceSet": history_pins,
            "openingSourceSet": opening_source_pins,
            "pinnedFiles": pins,
        }
        if set(seal) != SEAL_FIELDS:
            raise AssertionError("internal generation-3 readiness fields changed")
        core._exclusive_json(staged_seal, seal)
        replay._require_absent_or_empty_output(output_dir)
        _require_absent_artifacts()
        if _resolve(FUTURE_RUN_ROOT).exists():
            raise FileExistsError(
                "future generation-3 match root appeared before publication"
            )
        replay._publish_staged_directory(stage, output_dir)

    _verify(
        SEAL_PATH,
        allow_future_run=False,
        require_generation3_absence=True,
    )
    return _resolve(SEAL_PATH)


def _verify(
    path: Path = SEAL_PATH,
    *,
    allow_future_run: bool = True,
    require_generation3_absence: bool = False,
) -> dict[str, Any]:
    _configure_replay()
    path = _resolve(path)
    if path != _resolve(SEAL_PATH):
        raise ValueError(f"readiness seal path must be {_resolve(SEAL_PATH)}")
    seal = _strict_object(path, "generation-3 match-readiness seal")
    if set(seal) != SEAL_FIELDS:
        raise ValueError("generation-3 readiness field inventory changed")
    if type(seal.get("schemaVersion")) is not int or seal["schemaVersion"] != 1:
        raise ValueError("generation-3 readiness schema changed")
    if type(seal.get("kind")) is not str or seal["kind"] != SEAL_KIND:
        raise ValueError("generation-3 readiness kind changed")
    if type(seal.get("profileId")) is not str or seal["profileId"] != PROFILE_ID:
        raise ValueError("generation-3 readiness profile changed")
    for field in (
        "effectiveBeforeWinnerSelection",
        "effectiveBeforeHeldOutAccess",
        "futureRunRootAbsentAtSeal",
        "futureRunRootMustRemainAbsentThroughOuterSeal",
    ):
        if seal.get(field) is not True:
            raise ValueError(f"generation-3 readiness {field} changed")
    if seal.get("historyRoots") != [
        str(_resolve(path)) for path in HISTORY_ROOTS
    ]:
        raise ValueError("generation-3 readiness history roots changed")
    if seal.get("openingHistoryRoots") != [
        str(_resolve(path)) for path in OPENING_HISTORY_ROOTS
    ]:
        raise ValueError(
            "generation-3 readiness opening-history roots changed"
        )
    if seal.get("ignoredFutureRunRoot") != str(_resolve(FUTURE_RUN_ROOT)):
        raise ValueError("generation-3 future match root binding changed")
    if seal.get("pgnRecursiveAnnotationVariationPolicy") != PGN_RAV_POLICY:
        raise ValueError("generation-3 PGN RAV policy changed")
    if not _strict_equal(
        seal.get("openingReplayPolicy"), OPENING_REPLAY_POLICY
    ):
        raise ValueError("generation-3 opening replay policy changed")
    if not allow_future_run and _resolve(FUTURE_RUN_ROOT).exists():
        raise ValueError("future generation-3 match root must remain absent")
    _verify_absence_claim(seal.get("generation3ArtifactAbsence"))
    if require_generation3_absence:
        _require_absent_artifacts()
    prelabel._verify_seal()
    amendment_chain = _amendment_chain_identities()

    expected_named = _named_identities(
        replay._canonical_dotnet_host(DOTNET_HOST),
        RUNTIME_DIR / "OmegaHistorySnapshot.dll",
    )
    for name, expected in expected_named.items():
        actual = seal.get(name)
        if type(actual) is not dict or actual != expected:
            raise ValueError(f"generation-3 readiness identity drift: {name}")
        core._verify_identity(actual, f"generation-3 readiness {name}")
    if not _strict_equal(
        seal.get("generation3AmendmentChain"), amendment_chain
    ):
        raise ValueError("generation-3 readiness amendment chain drift")
    for ordinal, identity in enumerate(amendment_chain, start=1):
        core._verify_identity(
            identity,
            f"generation-3 readiness amendment-{ordinal:03d}",
        )
    history = replay._history_sources(HISTORY_ROOTS)
    _reject_rav_sources(history)
    expected_history = [core._identity(item) for item in history]
    replay._exact_identity_sequence(
        seal.get("historySourceSet"),
        expected_history,
        "generation-3 readiness historySourceSet",
    )
    opening_sources = _opening_sources(OPENING_HISTORY_ROOTS)
    expected_opening_sources = [
        core._identity(item) for item in opening_sources
    ]
    replay._exact_identity_sequence(
        seal.get("openingSourceSet"),
        expected_opening_sources,
        "generation-3 readiness openingSourceSet",
    )
    expected_pins = _published_pins(
        RUNTIME_DIR,
        _source_inventory(),
        SNAPSHOT_PATH,
        MANIFEST_PATH,
        history,
        OPENING_RUNTIME_DIR,
        opening_sources,
        OPENING_PROJECTION_PATH,
        OPENING_SNAPSHOT_PATH,
        OPENING_MANIFEST_PATH,
    )
    replay._exact_identity_sequence(
        seal.get("pinnedFiles"),
        expected_pins,
        "generation-3 readiness pinnedFiles",
    )
    audit = replay._validate_snapshot(SNAPSHOT_PATH, MANIFEST_PATH, history)
    if seal.get("audit") != audit:
        raise ValueError("generation-3 readiness replay audit changed")
    opening_audit = _validate_opening_replay(
        OPENING_PROJECTION_PATH,
        OPENING_SNAPSHOT_PATH,
        OPENING_MANIFEST_PATH,
        opening_sources,
        published=True,
        helper=OPENING_RUNTIME_DIR / "OmegaOpeningPrefixReplay.dll",
        rules=OPENING_RUNTIME_DIR / "ChessLib.dll",
    )
    sealed_opening_audit = seal.get("openingAudit")
    if not _strict_equal(sealed_opening_audit, opening_audit):
        raise ValueError("generation-3 opening replay audit changed")
    return seal


def _self_test() -> None:
    if training.NUMPY_WAS_PRELOADED:
        raise AssertionError("trainer observed NumPy before its thread contract")
    if (
        OPENING_REPLAY_POLICY.get("helperTimeoutSeconds")
        != prelabel.OPENING_REPLAY_TIMEOUT_SECONDS
    ):
        raise AssertionError("readiness replay timeout left the frozen contract")
    expected_history_roots = (
        REPO / "build-msvc",
        REPO / "build-king-state-v2",
        REPO / "validation",
        WORKSPACE / "match-runs",
        WORKSPACE / "match-runs" / "configs",
        WORKSPACE / "match-runs" / "output",
        WORKSPACE / "opening-audit",
        WORKSPACE / "fixtures",
        WORKSPACE / "omega-lab",
        WORKSPACE / "omega-lab" / "regressions",
        (
            WORKSPACE
            / "corechess-arena"
            / "Tools"
            / "OmegaMatch"
            / "Openings"
        ),
    )
    if tuple(map(_resolve, HISTORY_ROOTS)) != tuple(
        map(_resolve, expected_history_roots)
    ):
        raise AssertionError(
            "PGN/CCSF history roots do not cover the declared union"
        )
    chain = _amendment_chain_identities()
    if len(chain) != 3:
        raise AssertionError(
            "ordered amendment chain does not contain 001/002/003"
        )
    with tempfile.TemporaryDirectory(
        prefix="omega-king-state-v3-readiness-self-test-"
    ) as raw:
        root = Path(raw)
        clean = (
            '[Event "synthetic (not RAV)"]\n\n'
            "1. f1f2 {comment (allowed)} f8f7 ; comment (allowed)\n"
            "2. a0c2 *\n"
        )
        if _pgn_has_rav(clean):
            raise AssertionError("tag/comment parentheses were treated as RAV")
        for text in (
            "1. f1f2 (1. a0c2) f8f7 *\n",
            "1. f1f2 ) f8f7 *\n",
        ):
            if not _pgn_has_rav(text):
                raise AssertionError("PGN RAV parenthesis was accepted")
        rav = root / "rav.pgn"
        rav.write_text("1. f1f2 (1. a0c2) *\n", encoding="utf-8")
        try:
            _reject_rav_sources([rav])
        except ValueError:
            pass
        else:
            raise AssertionError("RAV-bearing PGN passed the source guard")

        duplicate = root / "duplicate.json"
        duplicate.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
        try:
            _strict_object(duplicate, "synthetic duplicate")
        except ValueError:
            pass
        else:
            raise AssertionError("duplicate JSON key was accepted")
        nonfinite = root / "nonfinite.json"
        nonfinite.write_text('{"value":NaN}', encoding="utf-8")
        try:
            _strict_object(nonfinite, "synthetic nonfinite")
        except ValueError:
            pass
        else:
            raise AssertionError("non-finite JSON number was accepted")

        opening_rows = (
            {"openings": [{"moves": []}]},
            {
                "recordType": "gameStart",
                "initialOfen": OMEGA_START,
                "openingMoves": [],
            },
            {
                "schemaVersion": 1,
                "id": "synthetic",
                "sources": [],
                "event": "synthetic",
                "date": "synthetic",
                "white": "white",
                "black": "black",
                "result": "*",
                "termination": "normal",
                "initialOfen": OMEGA_START,
                "moves": [],
                "sanMoves": [],
                "positions": [],
            },
        )
        opening_text = (
            json.dumps(opening_rows[0])
            + "\n\n"
            + json.dumps(opening_rows[1])
            + "\n"
            + json.dumps(opening_rows[2])
            + "\n"
        )
        synthetic_audit = {
            "requestCount": 3,
            "requestSchemaCounts": {
                "event-opening-moves": 1,
                "schedule-moves": 1,
                "transcript": 1,
            },
            "initialSourceCounts": {
                "explicit": 2,
                "official-default": 1,
            },
            "prefixRecordCount": 3,
            "openingRequestCount": 2,
            "openingPrefixRecordCount": 2,
            "openingPostMovePrefixRecordCount": 0,
            "openingExplicitInitialOccurrenceCount": 1,
            "openingDefaultInitialOccurrenceCount": 1,
            "newlyImplicitOpeningOccurrenceCount": 1,
            "transcriptRequestCount": 1,
            "transcriptMoveCount": 0,
            "transcriptPrefixRecordCount": 1,
            "uniqueOpeningSequenceCount": 1,
            "uniqueOpeningSequenceMoveLengthCounts": {"0": 1},
            "completeSequenceDeduplicatedRootAndPrefixOccurrenceCount": 1,
            "completeSequenceDeduplicatedPostMovePrefixOccurrenceCount": 0,
            "uniqueOpeningPrefixIdentityCount": 1,
            "uniqueOpeningPostMovePrefixIdentityCount": 0,
        }
        synthetic_full_replay_audit = {
            **synthetic_audit,
            "nullScalarValueCount": 1,
            "nullScalarValueCounts": {"postofen": 1},
            "partialOfenCanonicalizationCount": 0,
            "partialOfenCanonicalizationCounts": {},
            "legacyFourFieldBoardKeyArityCountEntries": [],
            "legacyFourFieldBoardKeySourceEntries": [],
            "legacyFourFieldBoardKeyProjection": {
                "bytes": 0,
                "sha256": "0" * 64,
            },
            "legacyFourFieldBoardKeyReconciliation": {
                "passed": True,
            },
            "structuralScalarAliasCount": 0,
            "structuralScalarAliasCounts": {},
        }
        if (
            len(synthetic_full_replay_audit)
            != len(OPENING_PROJECTION_AUDIT_FIELDS) + 10
        ):
            raise AssertionError(
                "synthetic expanded replay audit field count drifted"
            )
        opening_source = root / "opening-history.jsonl"
        opening_source.write_text(opening_text, encoding="utf-8")
        projection = root / "opening-projection.jsonl"
        frozen_audit = prelabel.EXPECTED_REPLAY_AUDIT
        try:
            prelabel.EXPECTED_REPLAY_AUDIT = (
                synthetic_full_replay_audit
            )
            audit = _project_opening_sequences(
                [opening_source], projection
            )
            projected = _jsonl_objects(
                projection, "synthetic opening projection"
            )
            if (
                [item["sourceRecord"] for item in projected] != [1, 3, 4]
                or [item["schema"] for item in projected]
                != [
                    "schedule-moves",
                    "event-opening-moves",
                    "transcript",
                ]
                or any(
                    item["sourcePath"]
                    != str(opening_source.resolve())
                    for item in projected
                )
                or not _strict_equal(
                    {
                        key: audit[key]
                        for key in OPENING_PROJECTION_AUDIT_FIELDS
                    },
                    synthetic_audit,
                )
            ):
                raise AssertionError(
                    "streamed opening-history projection changed"
                )

            mutable_source = root / "mutable-opening-history.jsonl"
            mutable_source.write_text(opening_text, encoding="utf-8")
            original_identity = core._identity
            calls = [0]

            def mutate_before_second_identity(
                path: Path,
            ) -> dict[str, Any]:
                if _resolve(path) == _resolve(mutable_source):
                    calls[0] += 1
                    if calls[0] == 2:
                        mutable_source.write_text(
                            opening_text + "\n",
                            encoding="utf-8",
                        )
                return original_identity(path)

            core._identity = mutate_before_second_identity
            try:
                _project_opening_sequences(
                    [mutable_source],
                    root / "mutable-projection.jsonl",
                )
            except ValueError as error:
                if "changed during projection" not in str(error):
                    raise
            else:
                raise AssertionError(
                    "opening-history mutation passed projection"
                )
            finally:
                core._identity = original_identity
        finally:
            prelabel.EXPECTED_REPLAY_AUDIT = frozen_audit

        paths = dict(GENERATION3_ARTIFACT_PATHS)
        try:
            globals()["GENERATION3_ARTIFACT_PATHS"] = {
                name: root / path.name for name, path in paths.items()
            }
            _require_absent_artifacts()
            forged = _generation3_absence_claim()
            forged["checkedBeforeFreshReplay"] = 1
            try:
                _verify_absence_claim(forged)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "boolean/integer substitution passed absence verification"
                )
            first = next(iter(GENERATION3_ARTIFACT_PATHS.values()))
            first.write_text("{}\n", encoding="utf-8")
            try:
                _require_absent_artifacts()
            except FileExistsError:
                pass
            else:
                raise AssertionError("preexisting generation-3 artifact passed")
        finally:
            globals()["GENERATION3_ARTIFACT_PATHS"] = paths

        exclusive = root / "readiness.json"
        core._exclusive_json(exclusive, {"first": True})
        try:
            core._exclusive_json(exclusive, {"second": True})
        except FileExistsError:
            pass
        else:
            raise AssertionError("readiness no-clobber publication was bypassed")
    print("king_state_match_readiness_generation3 self-test passed", flush=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--dotnet", required=True, type=Path)
    prepare.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    verify = commands.add_parser("verify")
    verify.add_argument("--seal", type=Path, default=SEAL_PATH)
    commands.add_parser("self-test")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "prepare":
        path = _prepare(args.dotnet, args.output_dir)
        print(f"Published generation-3 match readiness: {path}", flush=True)
    elif args.command == "verify":
        report = _verify(args.seal)
        print(
            "Verified generation-3 match readiness: "
            f"{report['audit']['sources']} sources, "
            f"{report['audit']['positions']} positions.",
            flush=True,
        )
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=__import__("sys").stderr, flush=True)
        raise SystemExit(1)
