#!/usr/bin/env python3
"""Freeze and verify generation-2 preselection match-history readiness.

This target-free step happens before validation selects a winner and before
the one-time held-out test is opened.  It builds an isolated Omega-aware
ChessLib replay helper, rejects PGNs containing recursive annotation
variations, snapshots every accepted PGN mainline and CCSF position, validates
every emitted OFEN with the frozen leakage-orbit code, and exclusively seals
the complete source/runtime/history inventory.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Iterator, Mapping, Sequence

import king_state_matches as core
import king_state_v2 as prelabel


SCHEMA_VERSION = 1
SEAL_KIND = "omega-nnue-king-state-v2-match-readiness-seal"
SNAPSHOT_KIND = "omega-history-replay-position-v1"
MANIFEST_KIND = "omega-history-replay-manifest-v1"
PGN_RAV_POLICY = "rejected-before-ChessLib.PGN.LoadFile"
HISTORY_PARSER = (
    "RAV-rejecting ChessLib.PGN.LoadFile/Game.LoadFromPgn and Game.Load "
    "plus coordinate replay"
)
MANIFEST_FIELDS = {
    "schemaVersion",
    "kind",
    "parser",
    "pgnRecursiveAnnotationVariations",
    "variant",
    "roots",
    "ignoredSubtrees",
    "sourceSet",
    "sourceCount",
    "games",
    "positions",
    "parseOrReplayErrors",
    "snapshot",
}
POSITION_FIELDS = {
    "schemaVersion",
    "kind",
    "sourcePath",
    "sourceBytes",
    "sourceSha256",
    "sourceFormat",
    "gameIndex",
    "gameId",
    "positionRole",
    "ply",
    "move",
    "ofen",
}

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO.parent
DOTNET_HOST = WORKSPACE / ".dotnet" / "dotnet.exe"
OUTPUT_DIR = REPO / "build-king-state-v2" / "readiness"
RUNTIME_DIR = OUTPUT_DIR / "runtime"
SNAPSHOT_PATH = OUTPUT_DIR / "history.positions.jsonl"
MANIFEST_PATH = OUTPUT_DIR / "history.manifest.json"
SEAL_PATH = OUTPUT_DIR / "king-state-v2-match-readiness.seal.json"
FUTURE_RUN_ROOT = WORKSPACE / "match-runs" / "output" / "king-state-v2"
GENERATION2_OUTPUT_DIR = REPO / "build-msvc" / "king-state-v2"
GENERATION2_ARTIFACT_PATHS = {
    "trainingPlan": GENERATION2_OUTPUT_DIR / "training-plan.json",
    "validationSelectionSeal": (
        GENERATION2_OUTPUT_DIR / "validation-selection.seal.json"
    ),
    "offlineAccessClaim": (
        GENERATION2_OUTPUT_DIR / "offline-test.json.access.json"
    ),
    "offlineReport": GENERATION2_OUTPUT_DIR / "offline-test.json",
    "offlineSufficientAttestation": (
        GENERATION2_OUTPUT_DIR / "offline-test.sufficient.json"
    ),
}

ADAPTER = Path(__file__).with_name("king_state_matches_generation2.py")
OFFLINE_WRAPPER = Path(__file__).with_name(
    "king_state_offline_generation2.py"
)
ADAPTER_CONTRACT = (
    REPO / "validation" / "omega-nnue-king-state-v2-match-adapter.json"
)
CONVERTER_PROJECT = (
    Path(__file__).parent
    / "OmegaHistorySnapshot"
    / "OmegaHistorySnapshot.csproj"
)
CONVERTER_SOURCE = CONVERTER_PROJECT.with_name("Program.cs")
COMPATIBILITY_PROTOCOL = (
    REPO / "validation" / "omega-nnue-king-state-v1-protocol.json"
)
V2_PREREGISTRATION = (
    REPO / "validation" / "omega-nnue-king-state-v2-preregistration.json"
)
V2_AMENDMENT = (
    REPO / "validation" / "omega-nnue-king-state-v2-amendment.json"
)
ACTIVE_PRELABEL_SEAL = (
    REPO
    / "build-msvc"
    / "data-generation"
    / "deep-hce-v3"
    / "king-state-v1-prelabel.seal.json"
)
DOTNET_HOST_FREEZE = (
    REPO
    / "build-msvc"
    / "data-generation"
    / "deep-hce-v3"
    / "deep-hce-v2.freeze.json"
)
HISTORY_ROOTS = (
    REPO / "build-msvc",
    REPO / "validation",
    WORKSPACE / "match-runs" / "configs",
    WORKSPACE / "match-runs" / "output",
    WORKSPACE / "omega-lab" / "regressions",
)

IDENTITY_FIELDS = {"path", "bytes", "sha256"}
SEAL_FIELDS = {
    "schemaVersion",
    "kind",
    "profileId",
    "effectiveBeforeWinnerSelection",
    "effectiveBeforeHeldOutAccess",
    "futureRunRootAbsentAtSeal",
    "futureRunRootMustRemainAbsentThroughOuterSeal",
    "generation2ArtifactAbsence",
    "historyRoots",
    "ignoredFutureRunRoot",
    "pgnRecursiveAnnotationVariationPolicy",
    "snapshot",
    "manifest",
    "adapter",
    "adapterContract",
    "compatibilityCore",
    "compatibilityProtocol",
    "generation2Preregistration",
    "generation2Amendment",
    "activePrelabelSeal",
    "converterProject",
    "converterSource",
    "offlineWrapper",
    "dotnetHost",
    "dotnetHostFreeze",
    "runtimeAssembly",
    "audit",
    "historySourceSet",
    "pinnedFiles",
}


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    path = _resolve(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object: {path}")
    return value


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        return (
            _resolve(Path(str(left["path"])))
            == _resolve(Path(str(right["path"])))
            and int(left["bytes"]) == int(right["bytes"])
            and str(left["sha256"]).lower() == str(right["sha256"]).lower()
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _canonical_dotnet_host(path: Path) -> Path:
    resolved = _resolve(path)
    expected = _resolve(DOTNET_HOST)
    if resolved != expected:
        raise ValueError(f".NET host must be the frozen workspace host: {expected}")
    current = core._identity(expected)
    freeze = _load_object(DOTNET_HOST_FREEZE, "deep-HCE-v3 data freeze")
    frozen_section = freeze.get("freeze")
    frozen = (
        frozen_section.get("dotnetHost")
        if isinstance(frozen_section, dict)
        else None
    )
    if not isinstance(frozen, dict) or frozen != current:
        raise ValueError(
            "workspace .NET host differs from the deep-HCE-v3 frozen host"
        )
    return expected


def _identity_at(actual_path: Path, published_path: Path) -> dict[str, Any]:
    identity = core._identity(_resolve(actual_path))
    identity["path"] = str(_resolve(published_path))
    return identity


def _exact_identity_sequence(
    actual: Any,
    expected: Sequence[Mapping[str, Any]],
    label: str,
    *,
    verify_files: bool = True,
) -> list[dict[str, Any]]:
    if not isinstance(actual, list):
        raise ValueError(f"{label} is not a list")
    if len(actual) != len(expected):
        raise ValueError(
            f"{label} cardinality changed: "
            f"expected {len(expected)}, got {len(actual)}"
        )
    result: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, (record, expected_record) in enumerate(
        zip(actual, expected, strict=True), 1
    ):
        if not isinstance(record, dict):
            raise ValueError(f"{label} entry {index} is not an identity object")
        if set(record) != IDENTITY_FIELDS:
            raise ValueError(f"{label} entry {index} has extra or missing fields")
        if (
            not isinstance(record.get("path"), str)
            or type(record.get("bytes")) is not int
            or not isinstance(record.get("sha256"), str)
        ):
            raise ValueError(f"{label} entry {index} has invalid identity types")
        path_key = str(record.get("path", "")).lower()
        if path_key in seen_paths:
            raise ValueError(f"{label} repeats path at entry {index}")
        seen_paths.add(path_key)
        if record != dict(expected_record):
            raise ValueError(
                f"{label} entry {index} is not the exact canonical identity"
            )
        if verify_files:
            core._verify_identity(record, f"{label} entry {index}")
        result.append(record)
    return result


def _generation2_absence_claim() -> dict[str, Any]:
    return {
        "checkedBeforeFreshReplay": True,
        "recheckedImmediatelyBeforeAtomicPublication": True,
        "artifacts": {
            name: {"path": str(_resolve(path)), "absent": True}
            for name, path in GENERATION2_ARTIFACT_PATHS.items()
        },
    }


def _verify_generation2_absence_claim(value: Any) -> None:
    if value != _generation2_absence_claim():
        raise ValueError(
            "generation-2 preselection/preaccess absence claim changed"
        )


def _require_absent_artifacts(
    paths: Mapping[str, Path] = GENERATION2_ARTIFACT_PATHS,
) -> None:
    present = [
        (name, _resolve(path))
        for name, path in paths.items()
        if _resolve(path).exists()
    ]
    if present:
        name, path = present[0]
        raise FileExistsError(
            f"generation-2 {name} already exists before readiness: {path}"
        )


def _require_absent_or_empty_output(path: Path) -> None:
    path = _resolve(path)
    if not path.exists():
        return
    if not path.is_dir():
        raise FileExistsError(f"readiness output is not a directory: {path}")
    first = next(path.iterdir(), None)
    if first is not None:
        raise FileExistsError(
            "readiness output must be wholly absent or empty; "
            f"preexisting artifact found: {first}"
        )


def _publish_staged_directory(stage: Path, output: Path) -> None:
    stage = _resolve(stage)
    output = _resolve(output)
    if not stage.is_dir():
        raise FileNotFoundError(f"readiness staging directory is absent: {stage}")
    _require_absent_or_empty_output(output)
    if output.exists():
        output.rmdir()
    os.rename(stage, output)


def _is_within(path: Path, root: Path) -> bool:
    try:
        _resolve(path).relative_to(_resolve(root))
        return True
    except ValueError:
        return False


def _history_sources(roots: Sequence[Path] = HISTORY_ROOTS) -> list[Path]:
    result: set[Path] = set()
    for raw in roots:
        root = _resolve(raw)
        if not root.is_dir():
            raise FileNotFoundError(f"history root is absent: {root}")
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in {".pgn", ".ccsf"}
                and not _is_within(path, FUTURE_RUN_ROOT)
            ):
                result.add(_resolve(path))
    return sorted(result, key=lambda path: str(path).lower())


def _jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with _resolve(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: record is not an object")
            yield line_number, value


def _source_key(identity: Mapping[str, Any]) -> tuple[str, int, str]:
    path = identity.get("path")
    byte_count = identity.get("bytes")
    digest = identity.get("sha256")
    if (
        type(path) is not str
        or type(byte_count) is not int
        or byte_count < 0
        or type(digest) is not str
    ):
        raise ValueError("history source identity has invalid exact JSON types")
    return (
        str(_resolve(Path(path))).lower(),
        byte_count,
        digest.lower(),
    )


def _validate_snapshot(
    snapshot_path: Path,
    manifest_path: Path,
    expected_sources: Sequence[Path],
    *,
    published_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    snapshot_path = _resolve(snapshot_path)
    manifest_path = _resolve(manifest_path)
    published_snapshot_path = _resolve(
        published_snapshot_path or snapshot_path
    )
    manifest = _load_object(manifest_path, "history snapshot manifest")
    if set(manifest) != MANIFEST_FIELDS:
        raise ValueError("history manifest field inventory changed")
    if (
        type(manifest.get("schemaVersion")) is not int
        or manifest["schemaVersion"] != SCHEMA_VERSION
    ):
        raise ValueError("history manifest schema changed")
    if (
        type(manifest.get("kind")) is not str
        or manifest["kind"] != MANIFEST_KIND
    ):
        raise ValueError("history manifest kind changed")
    if (
        type(manifest.get("parser")) is not str
        or manifest["parser"] != HISTORY_PARSER
    ):
        raise ValueError("history manifest parser changed")
    if (
        type(manifest.get("variant")) is not str
        or manifest["variant"] != "Omega"
    ):
        raise ValueError("history manifest is not Omega-only")
    if (
        type(manifest.get("pgnRecursiveAnnotationVariations")) is not str
        or manifest["pgnRecursiveAnnotationVariations"] != PGN_RAV_POLICY
    ):
        raise ValueError("history manifest PGN RAV policy changed")
    roots = manifest.get("roots")
    if (
        type(roots) is not list
        or any(type(item) is not str for item in roots)
        or roots != [str(_resolve(path)) for path in HISTORY_ROOTS]
    ):
        raise ValueError("history manifest roots changed")
    ignored = manifest.get("ignoredSubtrees")
    if (
        type(ignored) is not list
        or any(type(item) is not str for item in ignored)
        or ignored != [str(_resolve(FUTURE_RUN_ROOT))]
    ):
        raise ValueError("history manifest ignored-subtree contract changed")
    if (
        type(manifest.get("parseOrReplayErrors")) is not int
        or manifest["parseOrReplayErrors"] != 0
    ):
        raise ValueError("history converter reported a parse/replay error")
    for field in ("sourceCount", "games", "positions"):
        value = manifest.get(field)
        if type(value) is not int or value < 0:
            raise ValueError(
                f"history manifest {field} must be a nonnegative JSON integer"
            )
    expected_identities = [core._identity(path) for path in expected_sources]
    source_set = manifest.get("sourceSet")
    expected_keys = {_source_key(item) for item in expected_identities}
    _exact_identity_sequence(
        source_set,
        expected_identities,
        "history manifest sourceSet",
    )
    if manifest["sourceCount"] != len(expected_sources):
        raise ValueError("history manifest source count is not exact")

    snapshot = manifest.get("snapshot")
    if type(snapshot) is not dict:
        raise ValueError("history manifest lacks snapshot identity")
    expected_snapshot = _identity_at(snapshot_path, published_snapshot_path)
    _exact_identity_sequence(
        [snapshot],
        [expected_snapshot],
        "history manifest snapshot identity",
        verify_files=False,
    )

    seen_sources: set[tuple[str, int, str]] = set()
    seen_games: set[tuple[str, int, str]] = set()
    leakage_cache: dict[str, str] = {}
    unique_inputs: set[str] = set()
    records = 0
    games = 0
    current_key: tuple[str, int, str] | None = None
    previous_ofen = ""
    completed_ply = 0
    pending_pre: tuple[int, str | None] | None = None
    saw_final = False

    def finish_game() -> None:
        nonlocal games
        if current_key is None:
            return
        if not saw_final or pending_pre is not None:
            raise ValueError(f"snapshot game {current_key} is incomplete")
        games += 1

    for line, record in _jsonl(snapshot_path):
        if set(record) != POSITION_FIELDS:
            raise ValueError(
                f"{snapshot_path}:{line}: snapshot field inventory changed"
            )
        if (
            type(record.get("schemaVersion")) is not int
            or record["schemaVersion"] != SCHEMA_VERSION
        ):
            raise ValueError(f"{snapshot_path}:{line}: snapshot schema changed")
        if (
            type(record.get("kind")) is not str
            or record["kind"] != SNAPSHOT_KIND
        ):
            raise ValueError(f"{snapshot_path}:{line}: snapshot kind changed")
        source_path = record.get("sourcePath")
        source_bytes = record.get("sourceBytes")
        source_sha256 = record.get("sourceSha256")
        if (
            type(source_path) is not str
            or type(source_bytes) is not int
            or source_bytes < 0
            or type(source_sha256) is not str
        ):
            raise ValueError(
                f"{snapshot_path}:{line}: invalid source identity types"
            )
        identity = {
            "path": source_path,
            "bytes": source_bytes,
            "sha256": source_sha256,
        }
        source = _source_key(identity)
        if source not in expected_keys:
            raise ValueError(f"{snapshot_path}:{line}: unpinned source identity")
        source_format = record.get("sourceFormat")
        if (
            type(source_format) is not str
            or source_format not in {"pgn", "ccsf"}
        ):
            raise ValueError(f"{snapshot_path}:{line}: source format changed")
        game_index = record.get("gameIndex")
        game_id = record.get("gameId")
        if (
            type(game_index) is not int
            or game_index <= 0
            or type(game_id) is not str
            or len(game_id) != 64
            or any(character not in "0123456789abcdef" for character in game_id)
        ):
            raise ValueError(f"{snapshot_path}:{line}: malformed game identity")
        ofen = record.get("ofen")
        if type(ofen) is not str or not ofen:
            raise ValueError(f"{snapshot_path}:{line}: missing OFEN")
        try:
            identity_key = leakage_cache.get(ofen)
            if identity_key is None:
                identity_key = core._leakage_keys(ofen)[0]
                leakage_cache[ofen] = identity_key
            unique_inputs.add(identity_key)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"{snapshot_path}:{line}: invalid Omega OFEN: {error}"
            ) from error
        key = (source[0], game_index, game_id)
        role = record.get("positionRole")
        ply = record.get("ply")
        move = record.get("move")
        if type(role) is not str:
            raise ValueError(f"{snapshot_path}:{line}: position role is not a string")
        if type(ply) is not int or ply < 0:
            raise ValueError(f"{snapshot_path}:{line}: ply is not a nonnegative integer")
        if move is not None and type(move) is not str:
            raise ValueError(f"{snapshot_path}:{line}: move must be a string or null")
        if role in {"initial", "final"}:
            if move is not None:
                raise ValueError(
                    f"{snapshot_path}:{line}: {role} move must be null"
                )
        elif role in {"pre", "post"}:
            if type(move) is not str or not move:
                raise ValueError(
                    f"{snapshot_path}:{line}: {role} move must be a nonempty string"
                )
        if key != current_key:
            finish_game()
            if key in seen_games:
                raise ValueError(f"{snapshot_path}:{line}: non-contiguous game")
            if role != "initial" or ply != 0:
                raise ValueError(f"{snapshot_path}:{line}: game lacks initial OFEN")
            seen_games.add(key)
            current_key = key
            previous_ofen = ofen
            completed_ply = 0
            pending_pre = None
            saw_final = False
        elif saw_final:
            raise ValueError(f"{snapshot_path}:{line}: record follows final OFEN")
        elif role == "pre":
            if pending_pre is not None or ply != completed_ply + 1:
                raise ValueError(f"{snapshot_path}:{line}: malformed pre-ply record")
            if ofen != previous_ofen:
                raise ValueError(f"{snapshot_path}:{line}: pre-OFEN chain mismatch")
            pending_pre = (ply, move)
        elif role == "post":
            if (
                pending_pre is None
                or ply != pending_pre[0]
                or move != pending_pre[1]
            ):
                raise ValueError(f"{snapshot_path}:{line}: malformed post-ply record")
            previous_ofen = ofen
            completed_ply = pending_pre[0]
            pending_pre = None
        elif role == "final":
            if pending_pre is not None or ply != completed_ply or ofen != previous_ofen:
                raise ValueError(f"{snapshot_path}:{line}: inconsistent final OFEN")
            saw_final = True
        elif role != "initial":
            raise ValueError(f"{snapshot_path}:{line}: unknown position role")
        else:
            raise ValueError(f"{snapshot_path}:{line}: duplicate initial OFEN")
        seen_sources.add(source)
        records += 1
    finish_game()
    if seen_sources != expected_keys:
        raise ValueError("snapshot did not emit at least one game for every source")
    if manifest["games"] != games:
        raise ValueError("history manifest game count changed")
    if manifest["positions"] != records:
        raise ValueError("history manifest position count changed")
    return {
        "sources": len(expected_sources),
        "games": games,
        "positions": records,
        "uniqueLeakageInputs": len(unique_inputs),
    }


def _bundle_files(root: Path, suffixes: set[str] | None = None) -> list[Path]:
    root = _resolve(root)
    result = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part.lower() in {"bin", "obj"} for part in path.relative_to(root).parts):
            continue
        if suffixes is None or path.suffix.lower() in suffixes:
            result.append(_resolve(path))
    return sorted(result, key=lambda path: str(path).lower())


def _source_inventory() -> list[Path]:
    chesslib = WORKSPACE / "corechess-arena" / "ChessLib"
    files = [
        Path(__file__),
        ADAPTER,
        ADAPTER_CONTRACT,
        Path(core.__file__),
        Path(prelabel.__file__),
        Path(core.__file__).with_name("select_screen.py"),
        CONVERTER_PROJECT,
        CONVERTER_SOURCE,
        OFFLINE_WRAPPER,
        COMPATIBILITY_PROTOCOL,
        V2_PREREGISTRATION,
        V2_AMENDMENT,
        ACTIVE_PRELABEL_SEAL,
        DOTNET_HOST,
        DOTNET_HOST_FREEZE,
        WORKSPACE / "corechess-arena" / "SolutionInfo.proj",
        *_bundle_files(
            chesslib, {".cs", ".csproj", ".props", ".targets"}
        ),
    ]
    unique = {_resolve(path) for path in files}
    absent = [path for path in unique if not path.is_file()]
    if absent:
        raise FileNotFoundError(absent[0])
    return sorted(unique, key=lambda path: str(path).lower())


def _pinned_files(
    runtime_dir: Path,
    sources: Sequence[Path],
    snapshot: Path,
    manifest: Path,
    history: Sequence[Path],
) -> list[dict[str, Any]]:
    paths = {
        *(_resolve(path) for path in sources),
        *(_resolve(path) for path in _bundle_files(runtime_dir)),
        *(_resolve(path) for path in history),
        _resolve(snapshot),
        _resolve(manifest),
    }
    return [
        core._identity(path)
        for path in sorted(paths, key=lambda item: str(item).lower())
    ]


def _staged_pinned_files(
    staged_runtime: Path,
    sources: Sequence[Path],
    staged_snapshot: Path,
    staged_manifest: Path,
    history: Sequence[Path],
) -> list[dict[str, Any]]:
    staged_runtime = _resolve(staged_runtime)
    items: dict[Path, Path] = {
        _resolve(path): _resolve(path) for path in (*sources, *history)
    }
    items[_resolve(staged_snapshot)] = _resolve(SNAPSHOT_PATH)
    items[_resolve(staged_manifest)] = _resolve(MANIFEST_PATH)
    for path in _bundle_files(staged_runtime):
        relative = _resolve(path).relative_to(staged_runtime)
        items[_resolve(path)] = _resolve(RUNTIME_DIR / relative)
    published = list(items.values())
    if len({_resolve(path) for path in published}) != len(published):
        raise ValueError("staged readiness pins collide at publication paths")
    return [
        _identity_at(actual, public)
        for actual, public in sorted(
            items.items(), key=lambda item: str(item[1]).lower()
        )
    ]


def _rewrite_manifest_snapshot_identity(
    manifest_path: Path,
    snapshot_path: Path,
    published_snapshot_path: Path,
) -> None:
    manifest_path = _resolve(manifest_path)
    value = _load_object(manifest_path, "staged history manifest")
    value["snapshot"] = _identity_at(snapshot_path, published_snapshot_path)
    replacement = manifest_path.with_suffix(manifest_path.suffix + ".published.tmp")
    if replacement.exists():
        raise FileExistsError(replacement)
    replacement.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(replacement, manifest_path)


def _prepare(dotnet: Path, output_dir: Path) -> Path:
    dotnet = _canonical_dotnet_host(dotnet)
    dotnet_identity = core._identity(dotnet)
    output_dir = _resolve(output_dir)
    if output_dir != _resolve(OUTPUT_DIR):
        raise ValueError(f"readiness output must be {_resolve(OUTPUT_DIR)}")
    _require_absent_or_empty_output(output_dir)
    _require_absent_artifacts()
    if _resolve(FUTURE_RUN_ROOT).exists():
        raise FileExistsError(
            "future generation-2 match output already exists before readiness seal"
        )
    prelabel._verify_seal(ACTIVE_PRELABEL_SEAL)
    identities = _source_inventory()
    identity_records = [core._identity(path) for path in identities]
    history = _history_sources()
    history_records = [core._identity(path) for path in history]
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".king-state-v2-readiness-stage-",
        dir=output_dir.parent,
    ) as raw_stage:
        staging_root = _resolve(Path(raw_stage))
        stage = staging_root / "publication"
        stage.mkdir()
        runtime = stage / "runtime"
        snapshot = stage / SNAPSHOT_PATH.name
        manifest = stage / MANIFEST_PATH.name
        staged_seal = stage / SEAL_PATH.name
        assembly = runtime / "OmegaHistorySnapshot.dll"
        build = [
            str(_resolve(dotnet)),
            "build",
            str(_resolve(CONVERTER_PROJECT)),
            "-c",
            "Release",
            "--output",
            str(runtime),
            "--artifacts-path",
            str(staging_root / "dotnet-artifacts"),
        ]
        subprocess.run(build, cwd=REPO, check=True)
        if not assembly.is_file():
            raise FileNotFoundError(assembly)
        command = [
            str(_resolve(dotnet)),
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
        ]
        subprocess.run(command, cwd=REPO, check=True)

        # Validate the converter's untouched output first, then rewrite only
        # its snapshot path to the canonical post-rename publication path.
        _validate_snapshot(snapshot, manifest, history)
        _rewrite_manifest_snapshot_identity(
            manifest,
            snapshot,
            SNAPSHOT_PATH,
        )
        audit = _validate_snapshot(
            snapshot,
            manifest,
            history,
            published_snapshot_path=SNAPSHOT_PATH,
        )

        # Nothing from selection or the one-time held-out gate may appear
        # during the potentially long replay.  Rehash every source immediately
        # before constructing the seal.
        _require_absent_or_empty_output(output_dir)
        _require_absent_artifacts()
        if _resolve(FUTURE_RUN_ROOT).exists():
            raise FileExistsError(
                "future generation-2 match output appeared during readiness"
            )
        current_dotnet = _canonical_dotnet_host(dotnet)
        if core._identity(current_dotnet) != dotnet_identity:
            raise ValueError(".NET host changed during readiness preparation")
        prelabel._verify_seal(ACTIVE_PRELABEL_SEAL)
        current_identities = _source_inventory()
        _exact_identity_sequence(
            [core._identity(path) for path in current_identities],
            identity_records,
            "readiness source inventory",
        )
        current_history = _history_sources()
        _exact_identity_sequence(
            [core._identity(path) for path in current_history],
            history_records,
            "readiness history inventory",
        )
        repeated_audit = _validate_snapshot(
            snapshot,
            manifest,
            current_history,
            published_snapshot_path=SNAPSHOT_PATH,
        )
        if repeated_audit != audit:
            raise ValueError("staged readiness audit changed before publication")

        pins = _staged_pinned_files(
            runtime,
            current_identities,
            snapshot,
            manifest,
            current_history,
        )
        seal = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": SEAL_KIND,
            "profileId": "king-state-v2-deep-hce-v3",
            "effectiveBeforeWinnerSelection": True,
            "effectiveBeforeHeldOutAccess": True,
            "futureRunRootAbsentAtSeal": True,
            "futureRunRootMustRemainAbsentThroughOuterSeal": True,
            "generation2ArtifactAbsence": _generation2_absence_claim(),
            "historyRoots": [str(_resolve(path)) for path in HISTORY_ROOTS],
            "ignoredFutureRunRoot": str(_resolve(FUTURE_RUN_ROOT)),
            "pgnRecursiveAnnotationVariationPolicy": PGN_RAV_POLICY,
            "snapshot": _identity_at(snapshot, SNAPSHOT_PATH),
            "manifest": _identity_at(manifest, MANIFEST_PATH),
            "adapter": core._identity(ADAPTER),
            "adapterContract": core._identity(ADAPTER_CONTRACT),
            "compatibilityCore": core._identity(Path(core.__file__)),
            "compatibilityProtocol": core._identity(COMPATIBILITY_PROTOCOL),
            "generation2Preregistration": core._identity(V2_PREREGISTRATION),
            "generation2Amendment": core._identity(V2_AMENDMENT),
            "activePrelabelSeal": core._identity(ACTIVE_PRELABEL_SEAL),
            "converterProject": core._identity(CONVERTER_PROJECT),
            "converterSource": core._identity(CONVERTER_SOURCE),
            "offlineWrapper": core._identity(OFFLINE_WRAPPER),
            "dotnetHost": dict(dotnet_identity),
            "dotnetHostFreeze": core._identity(DOTNET_HOST_FREEZE),
            "runtimeAssembly": _identity_at(
                assembly,
                RUNTIME_DIR / assembly.name,
            ),
            "audit": audit,
            "historySourceSet": history_records,
            "pinnedFiles": pins,
        }
        if set(seal) != SEAL_FIELDS:
            raise AssertionError("internal readiness seal field inventory changed")
        _exact_identity_sequence(
            seal["historySourceSet"],
            history_records,
            "staged readiness historySourceSet",
        )
        _exact_identity_sequence(
            seal["pinnedFiles"],
            pins,
            "staged readiness pinnedFiles",
            verify_files=False,
        )
        core._exclusive_json(staged_seal, seal)

        # The directory rename is the only publication operation.  It exposes
        # runtime, replay, manifest, and seal together or none of them.
        _require_absent_or_empty_output(output_dir)
        _require_absent_artifacts()
        if _resolve(FUTURE_RUN_ROOT).exists():
            raise FileExistsError(
                "future generation-2 match output appeared before publication"
            )
        _publish_staged_directory(stage, output_dir)

    _require_absent_artifacts()
    _verify(
        SEAL_PATH,
        allow_future_run=False,
        require_generation2_absence=True,
    )
    print(f"Published match-readiness seal: {_resolve(SEAL_PATH)}", flush=True)
    print(f"Seal SHA-256: {core._sha256(SEAL_PATH)}", flush=True)
    return _resolve(SEAL_PATH)


def _verify(
    path: Path,
    *,
    allow_future_run: bool = True,
    require_generation2_absence: bool = False,
) -> dict[str, Any]:
    path = _resolve(path)
    if path != _resolve(SEAL_PATH):
        raise ValueError(f"readiness seal path must be {_resolve(SEAL_PATH)}")
    seal = _load_object(path, "match-readiness seal")
    if set(seal) != SEAL_FIELDS:
        raise ValueError("match-readiness seal field inventory changed")
    if seal.get("schemaVersion") != SCHEMA_VERSION or seal.get("kind") != SEAL_KIND:
        raise ValueError("wrong match-readiness seal schema/kind")
    if seal.get("profileId") != "king-state-v2-deep-hce-v3":
        raise ValueError("wrong match-readiness profile")
    if (
        seal.get("effectiveBeforeWinnerSelection") is not True
        or seal.get("effectiveBeforeHeldOutAccess") is not True
        or seal.get("futureRunRootAbsentAtSeal") is not True
        or seal.get("futureRunRootMustRemainAbsentThroughOuterSeal") is not True
    ):
        raise ValueError("readiness temporal boundary changed")
    if (
        seal.get("historyRoots") != [str(_resolve(path)) for path in HISTORY_ROOTS]
        or seal.get("ignoredFutureRunRoot") != str(_resolve(FUTURE_RUN_ROOT))
        or seal.get("pgnRecursiveAnnotationVariationPolicy") != PGN_RAV_POLICY
    ):
        raise ValueError("readiness history-root contract changed")
    if not allow_future_run and _resolve(FUTURE_RUN_ROOT).exists():
        raise ValueError("future run root existed during readiness publication")
    _verify_generation2_absence_claim(
        seal.get("generation2ArtifactAbsence")
    )
    if require_generation2_absence:
        _require_absent_artifacts()
    prelabel._verify_seal(ACTIVE_PRELABEL_SEAL)

    expected_named = {
        "snapshot": core._identity(SNAPSHOT_PATH),
        "manifest": core._identity(MANIFEST_PATH),
        "adapter": core._identity(ADAPTER),
        "adapterContract": core._identity(ADAPTER_CONTRACT),
        "compatibilityCore": core._identity(Path(core.__file__)),
        "compatibilityProtocol": core._identity(COMPATIBILITY_PROTOCOL),
        "generation2Preregistration": core._identity(V2_PREREGISTRATION),
        "generation2Amendment": core._identity(V2_AMENDMENT),
        "activePrelabelSeal": core._identity(ACTIVE_PRELABEL_SEAL),
        "converterProject": core._identity(CONVERTER_PROJECT),
        "converterSource": core._identity(CONVERTER_SOURCE),
        "offlineWrapper": core._identity(OFFLINE_WRAPPER),
        "dotnetHost": core._identity(
            _canonical_dotnet_host(DOTNET_HOST)
        ),
        "dotnetHostFreeze": core._identity(DOTNET_HOST_FREEZE),
        "runtimeAssembly": core._identity(
            RUNTIME_DIR / "OmegaHistorySnapshot.dll"
        ),
    }
    for name, expected in expected_named.items():
        actual = seal.get(name)
        if not isinstance(actual, dict) or actual != expected:
            raise ValueError(f"readiness named identity drift: {name}")

    history = _history_sources()
    expected_history = [core._identity(path) for path in history]
    sealed_history = seal.get("historySourceSet")
    _exact_identity_sequence(
        sealed_history,
        expected_history,
        "readiness historySourceSet",
    )
    expected_pins = _pinned_files(
        RUNTIME_DIR,
        _source_inventory(),
        SNAPSHOT_PATH,
        MANIFEST_PATH,
        history,
    )
    _exact_identity_sequence(
        seal.get("pinnedFiles"),
        expected_pins,
        "readiness pinnedFiles",
    )
    audit = _validate_snapshot(SNAPSHOT_PATH, MANIFEST_PATH, history)
    if seal.get("audit") != audit:
        raise ValueError("readiness snapshot audit changed")
    return seal


def _audit_unsealed() -> dict[str, Any]:
    if _resolve(SEAL_PATH).exists():
        raise FileExistsError("readiness seal already exists; use verify")
    _require_absent_artifacts()
    if _resolve(FUTURE_RUN_ROOT).exists():
        raise FileExistsError(
            "future generation-2 run root exists before readiness publication"
        )
    prelabel._verify_seal(ACTIVE_PRELABEL_SEAL)
    for path in _source_inventory():
        core._identity(path)
    for path in _bundle_files(RUNTIME_DIR):
        core._identity(path)
    history = _history_sources()
    audit = _validate_snapshot(SNAPSHOT_PATH, MANIFEST_PATH, history)
    print(
        "Unsealed readiness snapshot audited: "
        f"{audit['sources']} sources, {audit['games']} games, "
        f"{audit['positions']} positions, "
        f"{audit['uniqueLeakageInputs']} leakage inputs.",
        flush=True,
    )
    return audit


def _self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="omega-history-readiness-") as raw:
        root = Path(raw)

        def must_reject(action: Any, label: str) -> None:
            try:
                action()
            except (FileExistsError, ValueError):
                return
            raise AssertionError(f"{label} was accepted")

        mixed = root / "mixed"
        mixed.mkdir()
        must_reject(
            lambda: _canonical_dotnet_host(root / "dotnet.exe"),
            "noncanonical .NET host",
        )
        (mixed / "invalid.json").write_text("{not-json", encoding="utf-8")
        pgn = mixed / "game.pgn"
        pgn.write_text("[Event \"synthetic\"]\n", encoding="utf-8")
        sources = _history_sources((mixed,))
        if sources != [_resolve(pgn)]:
            raise AssertionError("mixed invalid JSON hid a PGN source")

        pin_target = root / "source.pgn"
        pin_target.write_text("[Event \"one\"]\n", encoding="utf-8")
        pin = core._identity(pin_target)
        pin_target.write_text("[Event \"drift\"]\n", encoding="utf-8")
        try:
            core._verify_identity(pin, "synthetic readiness drift")
        except ValueError:
            pass
        else:
            raise AssertionError("readiness source drift was accepted")

        first_pin = root / "first.pin"
        second_pin = root / "second.pin"
        first_pin.write_text("first\n", encoding="utf-8")
        second_pin.write_text("second\n", encoding="utf-8")
        exact_pins = [core._identity(first_pin), core._identity(second_pin)]
        _exact_identity_sequence(exact_pins, exact_pins, "synthetic exact pins")
        must_reject(
            lambda: _exact_identity_sequence(
                exact_pins[:1], exact_pins, "omitted pinnedFiles entry"
            ),
            "omitted pinnedFiles entry",
        )
        must_reject(
            lambda: _exact_identity_sequence(
                [*exact_pins, exact_pins[0]],
                exact_pins,
                "extra pinnedFiles entry",
            ),
            "extra pinnedFiles entry",
        )
        must_reject(
            lambda: _exact_identity_sequence(
                [exact_pins[0], exact_pins[0]],
                exact_pins,
                "duplicate pinnedFiles entry",
            ),
            "duplicate pinnedFiles entry",
        )
        must_reject(
            lambda: _exact_identity_sequence(
                [exact_pins[0], "not-an-identity"],
                exact_pins,
                "non-object pinnedFiles entry",
            ),
            "non-object pinnedFiles entry",
        )
        must_reject(
            lambda: _exact_identity_sequence(
                list(reversed(exact_pins)),
                exact_pins,
                "reordered historySourceSet",
            ),
            "reordered historySourceSet",
        )
        identity_with_extra = dict(exact_pins[0])
        identity_with_extra["forged"] = True
        must_reject(
            lambda: _exact_identity_sequence(
                [identity_with_extra, exact_pins[1]],
                exact_pins,
                "identity with extra fields",
            ),
            "identity with extra fields",
        )

        absence_paths = {
            name: root / f"{name}.json"
            for name in GENERATION2_ARTIFACT_PATHS
        }
        _require_absent_artifacts(absence_paths)
        absence_paths["validationSelectionSeal"].write_text(
            "{}\n", encoding="utf-8"
        )
        must_reject(
            lambda: _require_absent_artifacts(absence_paths),
            "preexisting validation selection",
        )
        forged_claim = _generation2_absence_claim()
        forged_claim["artifacts"]["offlineReport"]["absent"] = False
        must_reject(
            lambda: _verify_generation2_absence_claim(forged_claim),
            "forged preaccess absence claim",
        )

        absent_output = root / "absent-readiness"
        _require_absent_or_empty_output(absent_output)
        occupied_output = root / "occupied-readiness"
        occupied_output.mkdir()
        (occupied_output / "history.positions.jsonl").write_text(
            "preexisting\n", encoding="utf-8"
        )
        must_reject(
            lambda: _require_absent_or_empty_output(occupied_output),
            "preexisting unsealed replay resume",
        )
        stage = root / "atomic-stage"
        stage.mkdir()
        (stage / "complete.marker").write_text("complete\n", encoding="utf-8")
        atomic_output = root / "atomic-output"
        atomic_output.mkdir()
        _publish_staged_directory(stage, atomic_output)
        if stage.exists() or not (atomic_output / "complete.marker").is_file():
            raise AssertionError("staged readiness directory was not atomically moved")

        snapshot = root / "snapshot.jsonl"
        manifest = root / "manifest.json"
        source = core._identity(pin_target)
        invalid_record = {
            "schemaVersion": 1,
            "kind": SNAPSHOT_KIND,
            "sourcePath": source["path"],
            "sourceBytes": source["bytes"],
            "sourceSha256": source["sha256"],
            "sourceFormat": "pgn",
            "gameIndex": 1,
            "gameId": "0" * 64,
            "positionRole": "initial",
            "ply": 0,
            "move": None,
            "ofen": "not-an-ofen",
        }
        snapshot.write_text(json.dumps(invalid_record) + "\n", encoding="utf-8")
        manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": MANIFEST_KIND,
                    "parser": HISTORY_PARSER,
                    "variant": "Omega",
                    "pgnRecursiveAnnotationVariations": PGN_RAV_POLICY,
                    "roots": [
                        str(_resolve(path)) for path in HISTORY_ROOTS
                    ],
                    "ignoredSubtrees": [
                        str(_resolve(FUTURE_RUN_ROOT))
                    ],
                    "parseOrReplayErrors": 0,
                    "sourceSet": [source],
                    "sourceCount": 1,
                    "games": 1,
                    "positions": 1,
                    "snapshot": core._identity(snapshot),
                }
            ),
            encoding="utf-8",
        )
        # The temporary manifest names the correct temporary snapshot.
        value = _load_object(manifest, "synthetic manifest")
        value["snapshot"]["path"] = str(_resolve(snapshot))
        manifest.write_text(json.dumps(value), encoding="utf-8")
        try:
            _validate_snapshot(snapshot, manifest, [pin_target])
        except ValueError:
            pass
        else:
            raise AssertionError("invalid replay OFEN was accepted")

        valid_ofen = (
            "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
            "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
        )

        def write_synthetic_snapshot(
            *,
            manifest_substitution: tuple[str, Any] | None = None,
            record_substitution: tuple[str, Any] | None = None,
        ) -> None:
            base = {
                "schemaVersion": 1,
                "kind": SNAPSHOT_KIND,
                "sourcePath": source["path"],
                "sourceBytes": source["bytes"],
                "sourceSha256": source["sha256"],
                "sourceFormat": "pgn",
                "gameIndex": 1,
                "gameId": "1" * 64,
                "positionRole": "initial",
                "ply": 0,
                "move": None,
                "ofen": valid_ofen,
            }
            records = [
                dict(base),
                {
                    **base,
                    "positionRole": "final",
                },
            ]
            if record_substitution is not None:
                field, replacement = record_substitution
                records[0][field] = replacement
            snapshot.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            manifest_value = {
                "schemaVersion": 1,
                "kind": MANIFEST_KIND,
                "parser": HISTORY_PARSER,
                "variant": "Omega",
                "pgnRecursiveAnnotationVariations": PGN_RAV_POLICY,
                "roots": [
                    str(_resolve(path)) for path in HISTORY_ROOTS
                ],
                "ignoredSubtrees": [
                    str(_resolve(FUTURE_RUN_ROOT))
                ],
                "parseOrReplayErrors": 0,
                "sourceSet": [source],
                "sourceCount": 1,
                "games": 1,
                "positions": 2,
                "snapshot": core._identity(snapshot),
            }
            if manifest_substitution is not None:
                field, replacement = manifest_substitution
                manifest_value[field] = replacement
            manifest.write_text(
                json.dumps(manifest_value),
                encoding="utf-8",
            )

        write_synthetic_snapshot()
        valid_audit = _validate_snapshot(
            snapshot,
            manifest,
            [pin_target],
        )
        if valid_audit != {
            "sources": 1,
            "games": 1,
            "positions": 2,
            "uniqueLeakageInputs": 1,
        }:
            raise AssertionError("valid synthetic replay audit changed")

        for field, replacement in (
            ("schemaVersion", True),
            ("parseOrReplayErrors", False),
            ("sourceCount", True),
            ("games", True),
            ("positions", False),
        ):
            write_synthetic_snapshot(
                manifest_substitution=(field, replacement)
            )
            must_reject(
                lambda: _validate_snapshot(
                    snapshot,
                    manifest,
                    [pin_target],
                ),
                f"manifest boolean substitution for {field}",
            )

        for field, replacement in (
            ("schemaVersion", True),
            ("sourceBytes", False),
            ("gameIndex", True),
            ("ply", False),
            ("move", False),
            ("positionRole", False),
        ):
            write_synthetic_snapshot(
                record_substitution=(field, replacement)
            )
            must_reject(
                lambda: _validate_snapshot(
                    snapshot,
                    manifest,
                    [pin_target],
                ),
                f"position boolean substitution for {field}",
            )

        exclusive = root / "readiness.seal.json"
        core._exclusive_json(exclusive, {"first": True})
        try:
            core._exclusive_json(exclusive, {"second": True})
        except FileExistsError:
            pass
        else:
            raise AssertionError("readiness seal no-clobber was bypassed")
    print("king_state_match_readiness self-test passed", flush=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="build, snapshot, and seal history")
    prepare.add_argument("--dotnet", required=True, type=Path)
    prepare.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    verify = commands.add_parser("verify", help="rehash and replay-audit readiness")
    verify.add_argument("--seal", type=Path, default=SEAL_PATH)
    commands.add_parser(
        "audit-unsealed",
        help="validate the snapshot without publishing the exclusive seal",
    )
    commands.add_parser("self-test", help="run target-free adversarial checks")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "prepare":
        _prepare(args.dotnet, args.output_dir)
    elif args.command == "verify":
        report = _verify(args.seal)
        print(
            "Verified match readiness: "
            f"{report['audit']['sources']} sources, "
            f"{report['audit']['games']} games, "
            f"{report['audit']['positions']} positions.",
            flush=True,
        )
    elif args.command == "audit-unsealed":
        _audit_unsealed()
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
        raise SystemExit(2)
