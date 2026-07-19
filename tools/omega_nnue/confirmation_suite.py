#!/usr/bin/env python3
"""Freeze, generate, and select an independent Omega NNUE confirmation suite.

The workflow has two deliberately separated stages:

``prepare``
    Freeze the candidate/network, evaluator executable, training inputs,
    exclusions, launch suites, selector code, and selection policy.  Emit
    OmegaMatch configurations that contain *only* the handcrafted evaluator.

``select``
    After every source match is complete, re-check every frozen hash, audit the
    source logs, and choose one root at most from each AB/BA source pair.  Root
    choice uses only material phase and a precommitted deterministic hash.  It
    never uses a score, game outcome, best move, PV, candidate evaluation, or candidate
    hash.  The selected roots are excluded from every training input at both the
    exact frozen-NNUE-input and rule-preserving symmetry-orbit levels.

The script does not run an engine or a match.  OmegaMatch remains the independent
legality referee and must validate the generated source and confirmation
configurations before play.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Iterator

from select_screen import input_keys, observable_ofen, phase_of
from omega_nnue import parse_ofen


SCHEMA_VERSION = 1
PHASE_ORDER = ("opening", "middlegame", "late", "endgame")
SAFE_ID = re.compile(r"[^a-z0-9]+")
OMEGA_START = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
    "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
)
REQUIRED_HCE_OPTIONS = {
    "Threads": "1",
    "Hash": "128",
    "Ponder": "false",
    "OwnBook": "false",
    "UCI_Chess960": "false",
    "UCI_Variant": "omega",
    "UseOmegaNNUE": "false",
}


@dataclass(frozen=True)
class CandidateRoot:
    source_unit: str
    run_id: str
    pair_id: str
    game_id: str
    opening_id: str
    ply: int
    ofen: str
    phase: str
    piece_count: int
    pawn_count: int
    signature: str
    orbit: str
    orbit_signatures: tuple[str, ...]
    rank: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="freeze inputs and write HCE-only source configurations"
    )
    prepare.add_argument("--candidate-network", required=True, type=Path)
    prepare.add_argument(
        "--candidate-manifest",
        required=True,
        type=Path,
        help="Final trainer manifest that identifies the candidate network and inputs.",
    )
    prepare.add_argument(
        "--engine-executable",
        required=True,
        type=Path,
        help="One NNUE-capable executable used on both sides; source uses HCE off-switch.",
    )
    prepare.add_argument(
        "--omega-match-assembly",
        required=True,
        type=Path,
        help="Exact OmegaMatch.dll invoked with dotnet for source and confirmation.",
    )
    prepare.add_argument(
        "--launch-suite",
        action="append",
        required=True,
        type=Path,
        help="Legal OmegaMatch launch suite; repeat to create multiple source runs.",
    )
    prepare.add_argument(
        "--training-corpus", action="append", required=True, type=Path
    )
    prepare.add_argument(
        "--training-manifest", action="append", required=True, type=Path
    )
    prepare.add_argument(
        "--exclude-position-file",
        action="append",
        default=[],
        type=Path,
        help="Additional JSON/JSONL position-bearing artifact to keep out of confirmation.",
    )
    prepare.add_argument(
        "--exclude-directory",
        action="append",
        default=[],
        type=Path,
        help="Recursively pin/exclude every .json and .jsonl file in this directory.",
    )
    prepare.add_argument("--output-dir", required=True, type=Path)
    prepare.add_argument("--roots", type=int, default=64)
    prepare.add_argument("--seed", type=int, default=20260718)
    prepare.add_argument("--source-nodes", type=int, default=5000)
    prepare.add_argument("--confirmation-nodes", type=int, default=50000)
    prepare.add_argument("--source-max-plies", type=int, default=400)
    prepare.add_argument("--source-absolute-max-plies", type=int, default=500)
    prepare.add_argument("--confirmation-max-plies", type=int, default=300)
    prepare.add_argument("--confirmation-absolute-max-plies", type=int, default=400)
    prepare.add_argument("--search-timeout-ms", type=int, default=60000)
    prepare.add_argument("--stop-grace-ms", type=int, default=2000)
    prepare.add_argument("--minimum-source-ply", type=int, default=12)
    prepare.add_argument("--minimum-remaining-plies", type=int, default=6)

    select = subparsers.add_parser(
        "select", help="audit completed HCE source logs and freeze confirmation roots"
    )
    select.add_argument("--lock", required=True, type=Path)
    select.add_argument("--output-suite", type=Path)
    select.add_argument("--output-audit", type=Path)
    select.add_argument("--output-match-config", type=Path)

    subparsers.add_parser("self-test", help="run a lightweight synthetic smoke test")
    return parser.parse_args()


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    path = _resolve(path)
    stat = path.stat()
    return {
        "path": str(path),
        "bytes": stat.st_size,
        "sha256": _sha256(path),
    }


def _verify_identity(identity: dict[str, Any], label: str) -> Path:
    path = _resolve(Path(str(identity["path"])))
    current = _identity(path)
    if (
        current["bytes"] != int(identity["bytes"])
        or current["sha256"].lower() != str(identity["sha256"]).lower()
    ):
        raise ValueError(
            f"{label} changed after freeze: {path}; "
            f"expected {identity['sha256']}, got {current['sha256']}"
        )
    return path


def _harness_bundle_identity(assembly_path: Path) -> dict[str, Any]:
    """Hash the local runtime bundle with the same recipe as OmegaMatch."""

    assembly_path = _resolve(assembly_path)
    root = assembly_path.parent

    def included(path: Path) -> bool:
        name = path.name.lower()
        return (
            path.suffix.lower() in {".dll", ".so", ".dylib"}
            or name == "omegamatch.exe"
            or name.endswith(".deps.json")
            or name.endswith(".runtimeconfig.json")
        )

    files: list[dict[str, Any]] = []
    canonical = bytearray()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file() and included(item)),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        identity = _identity(path)
        relative = path.relative_to(root).as_posix()
        files.append(
            {
                "relativePath": relative,
                "bytes": identity["bytes"],
                "sha256": identity["sha256"],
            }
        )
        canonical.extend(
            (
                f"{relative}\t{identity['bytes']}\t"
                f"{identity['sha256']}\n"
            ).encode("utf-8")
        )
    assembly_relative = assembly_path.relative_to(root).as_posix()
    if not any(item["relativePath"] == assembly_relative for item in files):
        raise ValueError("OmegaMatch.dll is absent from its runtime-bundle identity")
    return {
        "root": str(root),
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def _verify_harness_bundle(identity: dict[str, Any]) -> dict[str, Any]:
    root = _resolve(Path(str(identity["root"])))
    current = _harness_bundle_identity(root / "OmegaMatch.dll")
    if current != identity:
        raise ValueError(
            "OmegaMatch runtime bundle changed after freeze: "
            f"{root}; expected {identity['sha256']}, got {current['sha256']}"
        )
    return current


def _atomic_json(path: Path, value: Any) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _exclusive_json(path: Path, value: Any) -> None:
    """Create a one-time JSON seal without replacing an existing file."""

    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as stream:
            descriptor = -1
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(_resolve(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _jsonl(path: Path) -> Iterator[dict[str, Any]]:
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
            yield value


def _leakage_keys(ofen: str) -> tuple[str, str, tuple[str, ...]]:
    """Return exact and genuinely rule-preserving NNUE-input keys.

    File reflection is a legal Omega symmetry only after castling rights are
    gone.  With rights present it moves the king from its required f-file
    castling origin to the e-file, so treating that reflection as equivalent
    would overstate the symmetry audit.  Rank reflection plus colour/STM swap
    already serializes to the identity key in the frozen side-relative input.
    """

    observable = observable_ofen(ofen)
    identity, orbit, signatures = input_keys(observable)
    castling = observable.split()[2]
    if castling != "-":
        return identity, identity, (identity,)
    return identity, orbit, signatures


def _field(value: dict[str, Any], name: str, default: Any = None) -> Any:
    for key, nested in value.items():
        if key.lower() == name.lower():
            return nested
    return default


def _iso_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _expand_exclusions(
    files: Iterable[Path], directories: Iterable[Path]
) -> list[Path]:
    paths = {_resolve(path) for path in files}
    for directory in directories:
        directory = _resolve(directory)
        paths.update(directory.rglob("*.json"))
        paths.update(directory.rglob("*.jsonl"))
    return sorted(paths, key=lambda path: str(path).lower())


def _suite_openings(path: Path) -> list[dict[str, Any]]:
    suite = _load_object(path)
    openings = _field(suite, "openings")
    if not isinstance(openings, list) or not openings:
        raise ValueError(f"launch suite has no openings: {path}")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_safe: set[str] = set()
    for index, opening in enumerate(openings):
        if not isinstance(opening, dict):
            raise ValueError(f"{path}: opening {index} is not an object")
        opening_id = str(_field(opening, "id", "")).strip()
        if not opening_id or opening_id.lower() in seen:
            raise ValueError(f"{path}: missing or duplicate opening id {opening_id!r}")
        seen.add(opening_id.lower())
        safe_id = _omega_safe_id(opening_id).lower()
        if safe_id in seen_safe:
            raise ValueError(
                f"{path}: opening ids collide after OmegaMatch sanitization: "
                f"{opening_id!r}"
            )
        seen_safe.add(safe_id)
        result.append(opening)
    return result


def _omega_safe_id(opening_id: str) -> str:
    return "".join(
        character
        if character.isalnum() or character in "-_"
        else "_"
        for character in opening_id
    )


def _source_config(
    run_id: str,
    output_directory: Path,
    engine: dict[str, Any],
    launch_suite: dict[str, Any],
    policy: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    executable = str(engine["path"])
    engine_hash = str(engine["sha256"])
    engines = []
    for suffix in ("a", "b"):
        engines.append(
            {
                "id": f"hce-source-{suffix}",
                "executable": executable,
                "expectedSha256": engine_hash,
                "options": dict(REQUIRED_HCE_OPTIONS),
            }
        )
    return {
        "schemaVersion": 1,
        "runId": run_id,
        "outputDirectory": str(_resolve(output_directory)),
        "seed": seed,
        "engines": engines,
        "match": {
            "engineA": "hce-source-a",
            "engineB": "hce-source-b",
            "openingsFile": str(launch_suite["path"]),
            "repeats": 1,
            "maxPlies": policy["sourceMaxPlies"],
            "absoluteMaxPlies": policy["sourceAbsoluteMaxPlies"],
            "mode": "nodes",
            "nodes": policy["sourceNodes"],
            "searchTimeoutMs": policy["searchTimeoutMs"],
            "stopGraceMs": policy["stopGraceMs"],
            "bootstrapIterations": 1000,
            "freshProcessPerGame": True,
        },
    }


def _prepare(args: argparse.Namespace) -> Path:
    if args.roots <= 0 or args.roots % len(PHASE_ORDER) != 0:
        raise ValueError(
            f"--roots must be positive and divisible by {len(PHASE_ORDER)}"
        )
    if args.roots < 64 and not bool(getattr(args, "_self_test", False)):
        raise ValueError(
            "an independent promotion confirmation requires at least 64 roots "
            "(64 complete AB/BA pairs)"
        )
    for name in (
        "source_nodes",
        "confirmation_nodes",
        "source_max_plies",
        "source_absolute_max_plies",
        "confirmation_max_plies",
        "confirmation_absolute_max_plies",
        "search_timeout_ms",
        "stop_grace_ms",
    ):
        if int(getattr(args, name)) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be positive")
    if args.minimum_source_ply < 1 or args.minimum_remaining_plies < 0:
        raise ValueError("invalid source-ply eligibility policy")

    output_dir = _resolve(args.output_dir)
    lock_path = output_dir / "confirmation-freeze.lock.json"
    if lock_path.exists():
        raise ValueError(
            f"freeze lock already exists; use a new output directory: {lock_path}"
        )

    candidate_network = _identity(args.candidate_network)
    candidate_manifest = _identity(args.candidate_manifest)
    candidate_manifest_value = _load_object(
        Path(str(candidate_manifest["path"]))
    )
    round_trip = _field(candidate_manifest_value, "roundTrip", {})
    round_trip_sha = (
        str(_field(round_trip, "sha256", "")).lower()
        if isinstance(round_trip, dict)
        else ""
    )
    if candidate_network["sha256"].lower() != round_trip_sha:
        raise ValueError(
            "candidate manifest roundTrip.sha256 does not identify the frozen "
            "candidate network"
        )
    manifest_inputs = _field(candidate_manifest_value, "inputs", [])
    if not isinstance(manifest_inputs, list) or not manifest_inputs:
        raise ValueError("candidate manifest does not identify any training input")
    direct_input_hashes = {
        str(_field(item, "sha256", "")).lower()
        for item in manifest_inputs
        if isinstance(item, dict)
    }
    if (
        len(direct_input_hashes) != len(manifest_inputs)
        or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in direct_input_hashes)
    ):
        raise ValueError("candidate manifest contains an invalid direct input identity")
    engine = _identity(args.engine_executable)
    omega_match_assembly = _identity(args.omega_match_assembly)
    if Path(str(omega_match_assembly["path"])).name.lower() != "omegamatch.dll":
        raise ValueError("--omega-match-assembly must identify OmegaMatch.dll")
    omega_match_bundle = _harness_bundle_identity(
        Path(str(omega_match_assembly["path"]))
    )
    training_corpora = [_identity(path) for path in args.training_corpus]
    corpus_hashes = {
        str(item["sha256"]).lower() for item in training_corpora
    }
    training_manifests = [_identity(path) for path in args.training_manifest]
    training_manifest_values = [
        (Path(str(item["path"])), _load_object(Path(str(item["path"]))))
        for item in training_manifests
    ]
    training_provenance = _validate_training_provenance(
        candidate_manifest_value,
        training_manifest_values,
        corpus_hashes,
    )
    exclusions = [
        _identity(path)
        for path in _expand_exclusions(
            args.exclude_position_file, args.exclude_directory
        )
    ]
    launch_suites = [_identity(path) for path in args.launch_suite]
    total_launch_units = sum(
        len(_suite_openings(Path(item["path"]))) for item in launch_suites
    )
    if total_launch_units < args.roots:
        raise ValueError(
            f"{total_launch_units} launch openings cannot supply {args.roots} "
            "independent source-pair units"
        )

    policy = {
        "roots": args.roots,
        "phaseOrder": list(PHASE_ORDER),
        "phaseQuota": args.roots // len(PHASE_ORDER),
        "phaseBucketsByPieceCount": {
            "opening": "37+",
            "middlegame": "25-36",
            "late": "13-24",
            "endgame": "5-12",
        },
        "seed": args.seed,
        "sourceNodes": args.source_nodes,
        "confirmationNodes": args.confirmation_nodes,
        "sourceMaxPlies": args.source_max_plies,
        "sourceAbsoluteMaxPlies": args.source_absolute_max_plies,
        "confirmationMaxPlies": args.confirmation_max_plies,
        "confirmationAbsoluteMaxPlies": args.confirmation_absolute_max_plies,
        "searchTimeoutMs": args.search_timeout_ms,
        "stopGraceMs": args.stop_grace_ms,
        "minimumSourcePly": args.minimum_source_ply,
        "minimumRemainingPlies": args.minimum_remaining_plies,
        "maximumRootsPerSourceGame": 1,
        "maximumRootsPerSourcePair": 1,
        "sourceRepeats": 1,
        "sourceSearchMode": "nodes",
        "sourceOwnBook": False,
        "sourceUseOmegaNNUE": False,
    }
    freeze_time = datetime.now(timezone.utc)
    source_configs: list[dict[str, Any]] = []
    for index, launch_suite in enumerate(launch_suites, 1):
        run_id = (
            f"nnue-confirm-source-v1-{args.seed}-{index:02d}-"
            f"{launch_suite['sha256'][:8]}"
        )
        config_path = output_dir / "source-configs" / f"source-{index:02d}.json"
        run_directory = output_dir / "source-runs" / f"source-{index:02d}"
        config = _source_config(
            run_id,
            run_directory,
            engine,
            launch_suite,
            policy,
            args.seed + index - 1,
        )
        config["expectedHarnessSha256"] = omega_match_assembly["sha256"]
        config["expectedHarnessBundleSha256"] = omega_match_bundle["sha256"]
        config["expectedOpeningSuiteSha256"] = launch_suite["sha256"]
        _atomic_json(config_path, config)
        source_configs.append(
            {
                "config": _identity(config_path),
                "runId": run_id,
                "eventsPath": str(_resolve(run_directory / "events.jsonl")),
                "launchSuite": launch_suite,
                "expectedPairs": len(
                    _suite_openings(Path(str(launch_suite["path"])))
                ),
                "expectedGames": 2
                * len(_suite_openings(Path(str(launch_suite["path"])))),
            }
        )

    selector_dependencies = [
        _identity(Path(__file__)),
        _identity(Path(__file__).with_name("select_screen.py")),
        _identity(Path(__file__).with_name("omega_nnue.py")),
    ]
    lock = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-confirmation-freeze",
        "freeze": {
            "createdUtc": freeze_time.isoformat().replace("+00:00", "Z"),
            "candidateNetwork": candidate_network,
            "candidateManifest": candidate_manifest,
            "engineExecutable": engine,
            "omegaMatchAssembly": omega_match_assembly,
            "omegaMatchBundle": omega_match_bundle,
            "trainingCorpora": training_corpora,
            "trainingManifests": training_manifests,
            "trainingProvenance": training_provenance,
            "excludedPositionArtifacts": exclusions,
            "launchSuites": launch_suites,
            "selectorDependencies": selector_dependencies,
        },
        "policy": policy,
        "sourceConfigs": source_configs,
        "plannedOutputs": {
            "suite": str(_resolve(output_dir / "confirmation-roots.json")),
            "audit": str(_resolve(output_dir / "confirmation-audit.json")),
            "matchConfig": str(
                _resolve(output_dir / "confirmation-match.json")
            ),
            "matchRun": str(_resolve(output_dir / "confirmation-run")),
        },
        "candidateBlindContract": {
            "candidateBytesUsedByPrepare": "SHA-256 identity only",
            "candidateHashAffectsSelectionSeed": False,
            "candidateMayNotRunBeforeSuiteAndAuditAreFrozen": True,
            "sourceEngines": "same pinned executable, UseOmegaNNUE=false",
            "sourceBook": False,
            "rootRankInputs": [
                "precommitted seed",
                "source run id",
                "source pair id",
                "source game id",
                "source ply",
                "position symmetry-orbit key",
            ],
            "forbiddenSelectionInputs": [
                "candidate evaluation",
                "HCE score",
                "game outcome",
                "best move",
                "PV",
                "candidate hash",
            ],
        },
    }
    _atomic_json(lock_path, lock)
    print(f"Freeze lock: {lock_path}")
    for item in source_configs:
        print(f"HCE-only source config: {item['config']['path']}")
    print(
        "Run and complete every source config with OmegaMatch, then call "
        f"`select --lock \"{lock_path}\"` before any candidate game."
    )
    return lock_path


def _nested_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for nested in value.values():
            yield from _nested_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _nested_strings(nested)
    elif isinstance(value, str):
        yield value


def _pin_sha(pin: Any, label: str) -> str:
    if not isinstance(pin, dict):
        raise ValueError(f"{label} is not a content-identity object")
    sha = str(_field(pin, "sha256", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError(f"{label} has no valid SHA-256 identity")
    return sha


def _trainer_inputs(manifest: dict[str, Any], label: str) -> set[str]:
    if _field(manifest, "schemaVersion") != 2:
        raise ValueError(f"{label} is not a schemaVersion=2 trainer manifest")
    inputs = _field(manifest, "inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError(f"{label} does not identify any training input")
    hashes = {
        _pin_sha(item, f"{label} input {index}")
        for index, item in enumerate(inputs, 1)
    }
    if len(hashes) != len(inputs):
        raise ValueError(f"{label} contains duplicate training-input hashes")
    return hashes


def _validate_training_provenance(
    candidate_manifest: dict[str, Any],
    supplied_manifests: list[tuple[Path, dict[str, Any]]],
    corpus_hashes: set[str],
) -> dict[str, Any]:
    """Prove that every trainer stage in the candidate chain is screened.

    A warm-start hash must identify exactly one corresponding trainer output:
    ``initialNetwork`` links to ``roundTrip`` and
    ``initialFloatCheckpoint`` links to ``floatCheckpoint``.  Merely finding
    the hash in arbitrary manifest metadata is deliberately insufficient.
    Every input of every supplied schema-v2 trainer manifest is conservatively
    required to be among the frozen corpora, even if that manifest is not
    ultimately reached by the candidate's warm-start chain.
    """

    candidate_inputs = _trainer_inputs(candidate_manifest, "candidate manifest")
    missing_candidate_inputs = sorted(candidate_inputs - corpus_hashes)
    if missing_candidate_inputs:
        raise ValueError(
            "candidate manifest direct inputs were not supplied as frozen "
            "training corpora: " + ", ".join(missing_candidate_inputs)
        )

    all_manifest_hashes: set[str] = set()
    output_index: dict[tuple[str, str], list[tuple[Path, dict[str, Any]]]] = (
        defaultdict(list)
    )
    trainer_manifest_count = 0
    for path, manifest in supplied_manifests:
        all_manifest_hashes.update(
            text.lower()
            for text in _nested_strings(manifest)
            if re.fullmatch(r"[0-9a-fA-F]{64}", text)
        )
        if _field(manifest, "schemaVersion") != 2:
            continue
        trainer_manifest_count += 1
        inputs = _trainer_inputs(manifest, f"trainer manifest {path}")
        missing = sorted(inputs - corpus_hashes)
        if missing:
            raise ValueError(
                f"trainer manifest {path} uses corpora that were not frozen "
                "for exclusion screening: " + ", ".join(missing)
            )
        for output_field in ("roundTrip", "floatCheckpoint"):
            pin = _field(manifest, output_field)
            if pin is None:
                continue
            sha = _pin_sha(pin, f"trainer manifest {path} {output_field}")
            output_index[(output_field.lower(), sha)].append((path, manifest))

    unmanifested = sorted(corpus_hashes - all_manifest_hashes)
    if unmanifested:
        raise ValueError(
            "every training corpus must be identified by at least one supplied "
            "manifest; missing hashes: " + ", ".join(unmanifested)
        )

    chain: list[dict[str, str]] = []
    visited: set[tuple[str, str]] = set()

    def follow(manifest: dict[str, Any], label: str) -> None:
        for warm_field, output_field in (
            ("initialFloatCheckpoint", "floatCheckpoint"),
            ("initialNetwork", "roundTrip"),
        ):
            warm_pin = _field(manifest, warm_field)
            if warm_pin is None:
                continue
            warm_sha = _pin_sha(warm_pin, f"{label} {warm_field}")
            matches = output_index.get((output_field.lower(), warm_sha), [])
            if not matches:
                raise ValueError(
                    f"{label} {warm_field} {warm_sha} has no supplied "
                    f"schemaVersion=2 trainer manifest whose {output_field} "
                    "identifies that exact artifact"
                )
            if len(matches) != 1:
                paths = ", ".join(str(path) for path, _ in matches)
                raise ValueError(
                    f"{label} {warm_field} {warm_sha} ambiguously matches "
                    f"multiple trainer manifests: {paths}"
                )
            path, parent = matches[0]
            edge = (warm_field.lower(), warm_sha)
            if edge in visited:
                raise ValueError(
                    f"trainer warm-start provenance contains a cycle at {warm_sha}"
                )
            visited.add(edge)
            chain.append(
                {
                    "consumer": label,
                    "inputField": warm_field,
                    "sha256": warm_sha,
                    "producerField": output_field,
                    "producerManifest": str(_resolve(path)),
                }
            )
            follow(parent, f"trainer manifest {path}")

    follow(candidate_manifest, "candidate manifest")
    return {
        "candidateDirectInputHashes": sorted(candidate_inputs),
        "frozenCorpusHashes": sorted(corpus_hashes),
        "suppliedTrainerManifestsChecked": trainer_manifest_count,
        "warmStartChain": chain,
    }


def _training_inputs(paths: Iterable[Path]) -> tuple[set[str], int]:
    signatures: set[str] = set()
    records = 0
    for path in paths:
        for record in _jsonl(path):
            records += 1
            ofen = _field(record, "ofen")
            if not isinstance(ofen, str):
                raise ValueError(f"training record has no OFEN: {path}")
            identity, _, _ = _leakage_keys(ofen)
            signatures.add(identity)
    return signatures, records


def _excluded_inputs(paths: Iterable[Path]) -> tuple[set[str], int]:
    signatures: set[str] = set()
    positions = 0
    for path in paths:
        path = _resolve(path)
        values: Iterable[Any]
        if path.suffix.lower() == ".jsonl":
            values = _jsonl(path)
        else:
            values = (_load_object(path),)
        for value in values:
            for text in _nested_strings(value):
                if "[" not in text:
                    continue
                try:
                    _, _, orbit_signatures = _leakage_keys(text)
                except ValueError:
                    continue
                signatures.update(orbit_signatures)
                positions += 1
    return signatures, positions


def _manifest_source_hashes(paths: Iterable[Path]) -> set[str]:
    hashes: set[str] = set()
    for path in paths:
        value = _load_object(path)
        for nested in _nested_dicts(value):
            for key in ("snapshotSha256", "sha256"):
                item = _field(nested, key)
                if isinstance(item, str) and re.fullmatch(
                    r"[0-9a-fA-F]{64}", item
                ):
                    hashes.add(item.lower())
    return hashes


def _nested_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _nested_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _nested_dicts(nested)


def _critical_options(options: dict[str, Any]) -> dict[str, str]:
    return {
        key.lower(): str(value).lower()
        for key, value in options.items()
        if key.lower()
        in {
            "threads",
            "hash",
            "ponder",
            "ownbook",
            "uci_chess960",
            "uci_variant",
            "useomegannue",
            "omegannuefile",
        }
    }


def _read_source_events(
    source: dict[str, Any],
    freeze_time: datetime,
    engine_hash: str,
    harness_hash: str,
    harness_bundle_hash: str,
    policy: dict[str, Any],
    training_artifact_hashes: set[str],
) -> tuple[list[CandidateRoot], dict[str, Any]]:
    config_path = _verify_identity(source["config"], "source config")
    config = _load_object(config_path)
    events_path = _resolve(Path(str(source["eventsPath"])))
    events_identity = _identity(events_path)
    if events_identity["sha256"].lower() in training_artifact_hashes:
        raise ValueError(f"source events were already used by training: {events_path}")

    run_record: dict[str, Any] | None = None
    starts: dict[tuple[str, int], dict[str, Any]] = {}
    plies: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    results: dict[tuple[str, int], dict[str, Any]] = {}
    attempts_seen: dict[str, set[int]] = defaultdict(set)
    record_count = 0
    ignored_fields = Counter()
    for record in _jsonl(events_path):
        record_count += 1
        record_type = str(_field(record, "recordType", "")).lower()
        if record_type == "run":
            if run_record is not None:
                raise ValueError(f"multiple run records in {events_path}")
            run_record = {
                "runId": _field(record, "runId"),
                "createdUtc": _field(record, "createdUtc"),
                "configSha256": _field(record, "configSha256"),
                "openingSuiteSha256": _field(record, "openingSuiteSha256"),
                "harnessSha256": _field(record, "harnessSha256"),
                "harnessBundleSha256": _field(
                    record, "harnessBundleSha256"
                ),
                "match": _field(record, "match", {}),
                "engines": _field(record, "engines", []),
            }
        elif record_type == "gamestart":
            game_id = str(_field(record, "gameId", ""))
            attempt = int(_field(record, "attempt", 0))
            if not game_id or attempt < 1:
                raise ValueError(f"malformed gameStart identity in {events_path}")
            attempts_seen[game_id].add(attempt)
            starts[(game_id, attempt)] = {
                "gameId": game_id,
                "pairId": str(_field(record, "pairId", "")),
                "attempt": attempt,
                "openingId": str(_field(record, "openingId", "")),
                "whiteEngineId": str(_field(record, "whiteEngineId", "")),
                "blackEngineId": str(_field(record, "blackEngineId", "")),
                "initialOfen": str(_field(record, "initialOfen", "")),
                "openingMoves": list(_field(record, "openingMoves", []) or []),
            }
        elif record_type == "ply":
            game_id = str(_field(record, "gameId", ""))
            attempt = int(_field(record, "attempt", 0))
            if not game_id or attempt < 1:
                raise ValueError(f"malformed ply identity in {events_path}")
            attempts_seen[game_id].add(attempt)
            search = _field(record, "search", {})
            plies[(game_id, attempt)].append(
                {
                    "ply": int(_field(record, "ply", 0)),
                    "preOfen": _field(record, "preOfen"),
                    "postOfen": _field(record, "postOfen"),
                    "engineId": str(_field(record, "engineId", "")),
                    "color": str(_field(record, "color", "")).lower(),
                    "error": _field(record, "error"),
                    "command": (
                        _field(search, "command") if isinstance(search, dict) else None
                    ),
                }
            )
            # Make the non-use of evaluative fields explicit in the audit.
            for name in ("FinalInfo", "WhiteScoreCp", "WhiteScoreMate", "BestMove", "Pv"):
                if _field(record, name) is not None:
                    ignored_fields[name] += 1
        elif record_type == "gameresult":
            game_id = str(_field(record, "gameId", ""))
            attempt = int(_field(record, "attempt", 0))
            if not game_id or attempt < 1:
                raise ValueError(f"malformed gameResult identity in {events_path}")
            attempts_seen[game_id].add(attempt)
            results[(game_id, attempt)] = {
                "gameId": game_id,
                "pairId": str(_field(record, "pairId", "")),
                "attempt": attempt,
                "openingId": str(_field(record, "openingId", "")),
                "whiteEngineId": str(_field(record, "whiteEngineId", "")),
                "blackEngineId": str(_field(record, "blackEngineId", "")),
                "plies": int(_field(record, "plies", 0)),
                "finalOfen": str(_field(record, "finalOfen", "")),
                "illegalMoves": int(_field(record, "illegalMoves", 0)),
                "illegalPvs": int(_field(record, "illegalPvs", 0)),
                "protocolFailures": int(_field(record, "protocolFailures", 0)),
                "timeForfeits": int(_field(record, "timeForfeits", 0)),
            }
            if _field(record, "result") is not None:
                ignored_fields["GameResult.Result"] += 1

    final_events_identity = _identity(events_path)
    if final_events_identity != events_identity:
        raise ValueError(
            f"source events changed while being audited: {events_path}; "
            "finish the source run before selection"
        )
    if run_record is None:
        raise ValueError(f"source events have no run record: {events_path}")
    if str(run_record["runId"]) != str(source["runId"]):
        raise ValueError(f"source run id mismatch in {events_path}")
    if str(run_record["configSha256"]).lower() != source["config"]["sha256"].lower():
        raise ValueError(f"source config hash mismatch in {events_path}")
    if (
        str(run_record["openingSuiteSha256"]).lower()
        != source["launchSuite"]["sha256"].lower()
    ):
        raise ValueError(f"source launch-suite hash mismatch in {events_path}")
    if str(run_record["harnessSha256"]).lower() != harness_hash.lower():
        raise ValueError(
            f"source run did not use the frozen OmegaMatch assembly: {events_path}"
        )
    if (
        str(run_record["harnessBundleSha256"]).lower()
        != harness_bundle_hash.lower()
    ):
        raise ValueError(
            "source run did not use the frozen OmegaMatch runtime bundle: "
            f"{events_path}"
        )
    created = _iso_utc(str(run_record["createdUtc"]))
    if created < freeze_time:
        raise ValueError(f"source run predates candidate freeze: {events_path}")

    match = run_record["match"]
    if not isinstance(match, dict):
        raise ValueError(f"source run match metadata is malformed: {events_path}")
    if str(_field(match, "mode", "")).lower() != "nodes":
        raise ValueError(f"source run was not fixed-node: {events_path}")
    if int(_field(match, "nodes", -1)) != int(policy["sourceNodes"]):
        raise ValueError(f"source run used the wrong node budget: {events_path}")
    if int(_field(match, "repeats", -1)) != 1:
        raise ValueError(f"source repeats changed after freeze: {events_path}")

    engine_records = run_record["engines"]
    if not isinstance(engine_records, list) or len(engine_records) != 2:
        raise ValueError(f"source run must identify exactly two HCE processes")
    expected_options = _critical_options(REQUIRED_HCE_OPTIONS)
    source_engine_ids: set[str] = set()
    for engine in engine_records:
        if not isinstance(engine, dict):
            raise ValueError(f"source engine record is malformed: {events_path}")
        source_engine_ids.add(str(_field(engine, "id", "")))
        if str(_field(engine, "sha256", "")).lower() != engine_hash.lower():
            raise ValueError(f"source engine executable differs from freeze")
        options = _field(engine, "options", {})
        if not isinstance(options, dict):
            raise ValueError(f"source engine options are malformed")
        critical = _critical_options(options)
        if critical != expected_options:
            raise ValueError(
                f"source engine was not the locked HCE-only control: {critical}"
            )
        if bool(_field(engine, "omegaNnueActiveVerified", False)):
            raise ValueError("source engine reported NNUE active")
        assets = _field(engine, "externalAssets", [])
        if assets:
            raise ValueError("HCE source generation unexpectedly loaded an external asset")
    if source_engine_ids != {"hce-source-a", "hce-source-b"}:
        raise ValueError(f"unexpected source engine ids: {source_engine_ids}")

    newest_attempt = {
        game_id: max(attempts)
        for game_id, attempts in attempts_seen.items()
        if game_id
    }
    if len(newest_attempt) != int(source["expectedGames"]):
        raise ValueError(
            f"incomplete source run {events_path}: expected "
            f"{source['expectedGames']} completed games, got {len(newest_attempt)}"
        )

    launch_openings = {
        str(_field(opening, "id", "")).lower(): opening
        for opening in _suite_openings(Path(source["launchSuite"]["path"]))
    }
    complete: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
    pair_games: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for game_id, attempt in sorted(newest_attempt.items()):
        key = (game_id, attempt)
        start = starts.get(key)
        result = results.get(key)
        if result is None:
            raise ValueError(
                f"latest source attempt is incomplete: {game_id} attempt {attempt}"
            )
        game_plies = sorted(plies.get(key, []), key=lambda value: value["ply"])
        if start is None:
            raise ValueError(f"completed source game lacks gameStart: {game_id}")
        if start["pairId"] != result["pairId"] or start["openingId"] != result["openingId"]:
            raise ValueError(f"source game metadata changed within attempt: {game_id}")
        opening = launch_openings.get(start["openingId"].lower())
        if opening is None:
            raise ValueError(f"source game used an unplanned opening: {game_id}")
        expected_initial = str(_field(opening, "initialOfen", OMEGA_START))
        expected_moves = [str(item) for item in _field(opening, "moves", [])]
        if (
            start["initialOfen"] != expected_initial
            or start["openingMoves"] != expected_moves
        ):
            raise ValueError(f"source game launch position changed: {game_id}")
        safe_id = _omega_safe_id(str(_field(opening, "id", "")))
        expected_pair_id = f"{safe_id}-r001"
        expected_suffix = (
            "ab"
            if (
                start["whiteEngineId"],
                start["blackEngineId"],
            )
            == ("hce-source-a", "hce-source-b")
            else "ba"
        )
        if (
            start["pairId"] != expected_pair_id
            or start["gameId"] != f"{expected_pair_id}-{expected_suffix}"
        ):
            raise ValueError(
                f"source game identity differs from the frozen OmegaMatch "
                f"schedule: {game_id}"
            )
        safety = (
            result["illegalMoves"]
            + result["illegalPvs"]
            + result["protocolFailures"]
            + result["timeForfeits"]
        )
        if safety:
            raise ValueError(f"unsafe source game cannot seed confirmation: {game_id}")
        engine_plies = result["plies"] - len(start["openingMoves"])
        if engine_plies < 0 or len(game_plies) != engine_plies:
            raise ValueError(
                f"source game ply count mismatch: {game_id}; "
                f"{len(game_plies)} engine records versus "
                f"{result['plies']} total plies and "
                f"{len(start['openingMoves'])} forced opening plies"
            )
        expected_ply_numbers = list(
            range(len(start["openingMoves"]) + 1, result["plies"] + 1)
        )
        actual_ply_numbers = [item["ply"] for item in game_plies]
        if actual_ply_numbers != expected_ply_numbers:
            raise ValueError(
                f"source game plies are duplicated or non-contiguous: {game_id}; "
                f"expected {expected_ply_numbers}, got {actual_ply_numbers}"
            )
        expected_command = f"go nodes {policy['sourceNodes']}"
        for index, ply in enumerate(game_plies):
            if (
                ply["engineId"] not in source_engine_ids
                or ply["error"] is not None
                or not isinstance(ply["preOfen"], str)
                or not isinstance(ply["postOfen"], str)
                or ply["command"] != expected_command
            ):
                raise ValueError(f"invalid fixed-node source ply in {game_id}")
            try:
                _, side_to_move, _ = parse_ofen(str(ply["preOfen"]))
            except ValueError as error:
                raise ValueError(
                    f"invalid source PreOfen in {game_id}: {error}"
                ) from error
            expected_color = "white" if side_to_move == "w" else "black"
            expected_engine = (
                start["whiteEngineId"]
                if expected_color == "white"
                else start["blackEngineId"]
            )
            if ply["color"] != expected_color or ply["engineId"] != expected_engine:
                raise ValueError(
                    f"source side/engine metadata disagrees with OFEN in {game_id}"
                )
            if index > 0 and game_plies[index - 1]["postOfen"] != ply["preOfen"]:
                raise ValueError(f"source OFEN chain is broken in {game_id}")
        if game_plies and game_plies[-1]["postOfen"] != result["finalOfen"]:
            raise ValueError(f"source final OFEN does not match the last ply: {game_id}")
        pair_games[start["pairId"]].append(start)
        complete.append((start, result, game_plies))

    if len(pair_games) != int(source["expectedPairs"]):
        raise ValueError(
            f"source pair count mismatch: expected {source['expectedPairs']}, "
            f"got {len(pair_games)}"
        )
    scheduled_openings: list[str] = []
    for pair_id, games in pair_games.items():
        if len(games) != 2:
            raise ValueError(f"source pair {pair_id} is not complete AB/BA")
        pair_opening_ids = {game["openingId"].lower() for game in games}
        if len(pair_opening_ids) != 1:
            raise ValueError(f"source pair {pair_id} mixes launch openings")
        scheduled_openings.append(next(iter(pair_opening_ids)))
        color_assignments = {
            (game["whiteEngineId"], game["blackEngineId"]) for game in games
        }
        if color_assignments != {
            ("hce-source-a", "hce-source-b"),
            ("hce-source-b", "hce-source-a"),
        }:
            raise ValueError(f"source pair {pair_id} is not color-swapped AB/BA")
    frozen_opening_counts = Counter(launch_openings.keys())
    if Counter(scheduled_openings) != frozen_opening_counts:
        raise ValueError(
            "source schedule did not cover each frozen launch opening exactly "
            f"once: scheduled={dict(Counter(scheduled_openings))}, "
            f"frozen={dict(frozen_opening_counts)}"
        )

    candidates: list[CandidateRoot] = []
    invalid_ofens = 0
    for start, _, game_plies in complete:
        maximum_ply = max((item["ply"] for item in game_plies), default=0)
        source_unit = f"{source['runId']}:{start['pairId']}"
        for ply in game_plies:
            ply_number = int(ply["ply"])
            if ply_number < int(policy["minimumSourcePly"]):
                continue
            if maximum_ply - ply_number < int(policy["minimumRemainingPlies"]):
                continue
            ofen = str(ply["preOfen"])
            try:
                pieces, _, _ = parse_ofen(ofen)
                if sum(piece == 5 and side == 0 for piece, side, _ in pieces) != 1:
                    raise ValueError("white king count")
                if sum(piece == 5 and side == 1 for piece, side, _ in pieces) != 1:
                    raise ValueError("black king count")
                signature, orbit, orbit_signatures = _leakage_keys(ofen)
            except ValueError:
                invalid_ofens += 1
                continue
            phase = phase_of(len(pieces))
            if phase is None:
                continue
            rank = hashlib.sha256(
                (
                    f"omega-nnue-confirm-v1\0{policy['seed']}\0"
                    f"{source['runId']}\0{start['pairId']}\0"
                    f"{start['gameId']}\0{ply_number}\0{orbit}"
                ).encode("utf-8")
            ).hexdigest()
            candidates.append(
                CandidateRoot(
                    source_unit=source_unit,
                    run_id=str(source["runId"]),
                    pair_id=start["pairId"],
                    game_id=start["gameId"],
                    opening_id=start["openingId"],
                    ply=ply_number,
                    ofen=ofen,
                    phase=phase,
                    piece_count=len(pieces),
                    pawn_count=sum(piece == 0 for piece, _, _ in pieces),
                    signature=signature,
                    orbit=orbit,
                    orbit_signatures=orbit_signatures,
                    rank=rank,
                )
            )

    evidence = {
        "events": events_identity,
        "runId": source["runId"],
        "createdUtc": created.isoformat().replace("+00:00", "Z"),
        "records": record_count,
        "completeGames": len(complete),
        "completePairs": len(pair_games),
        "candidatePositionsBeforeExclusion": len(candidates),
        "invalidOfensIgnored": invalid_ofens,
        "evaluativeFieldsPresentButIgnored": dict(sorted(ignored_fields.items())),
        "candidateEvaluationsPresent": False,
        "sourceUseOmegaNNUE": False,
        "sourceOwnBook": False,
        "fixedNodes": policy["sourceNodes"],
        "harnessSha256": harness_hash,
        "harnessBundleSha256": harness_bundle_hash,
    }
    return candidates, evidence


def _select_phase_balanced(
    candidates: list[CandidateRoot], roots: int
) -> list[CandidateRoot]:
    quota = roots // len(PHASE_ORDER)
    by_unit_phase: dict[tuple[str, str], list[CandidateRoot]] = defaultdict(list)
    phases_by_unit: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        by_unit_phase[(candidate.source_unit, candidate.phase)].append(candidate)
        phases_by_unit[candidate.source_unit].add(candidate.phase)
    for values in by_unit_phase.values():
        values.sort(key=lambda item: item.rank)

    phase_units = {
        phase: {
            unit
            for unit, available_phases in phases_by_unit.items()
            if phase in available_phases
        }
        for phase in PHASE_ORDER
    }
    scarcity = sorted(
        PHASE_ORDER,
        key=lambda phase: (
            len(phase_units[phase]),
            PHASE_ORDER.index(phase),
        ),
    )
    selected: list[CandidateRoot] = []
    used_units: set[str] = set()
    used_orbits: set[str] = set()
    for phase in scarcity:
        while sum(item.phase == phase for item in selected) < quota:
            compatible: list[CandidateRoot] = []
            for unit in sorted(phase_units[phase]):
                if unit in used_units:
                    continue
                usable = next(
                    (
                        item
                        for item in by_unit_phase[(unit, phase)]
                        if item.orbit not in used_orbits
                    ),
                    None,
                )
                if usable is not None:
                    compatible.append(usable)
            if not compatible:
                raise ValueError(
                    f"cannot fill phase {phase!r} quota {quota}; "
                    f"available source pairs={len(phase_units[phase])}, "
                    f"already used={len(used_units)}"
                )
            chosen = min(
                compatible,
                key=lambda item: (
                    len(phases_by_unit[item.source_unit]),
                    item.rank,
                ),
            )
            selected.append(chosen)
            used_units.add(chosen.source_unit)
            used_orbits.add(chosen.orbit)
    return sorted(
        selected,
        key=lambda item: (
            PHASE_ORDER.index(item.phase),
            item.rank,
        ),
    )


def _confirmation_config(
    lock: dict[str, Any], suite_path: Path, match_output: Path
) -> dict[str, Any]:
    freeze = lock["freeze"]
    policy = lock["policy"]
    executable = freeze["engineExecutable"]
    network = freeze["candidateNetwork"]
    candidate_options = dict(REQUIRED_HCE_OPTIONS)
    candidate_options["OmegaNNUEFile"] = str(network["path"])
    candidate_options["UseOmegaNNUE"] = "true"
    return {
        "schemaVersion": 1,
        "expectedHarnessSha256": freeze["omegaMatchAssembly"]["sha256"],
        "expectedHarnessBundleSha256": freeze["omegaMatchBundle"]["sha256"],
        "expectedOpeningSuiteSha256": _sha256(suite_path),
        "runId": (
            f"confirm-omega-nnue-{network['sha256'][:12]}-"
            f"{policy['roots'] * 2}"
        ),
        "outputDirectory": str(_resolve(match_output)),
        "seed": policy["seed"],
        "engines": [
            {
                "id": "nnue-candidate",
                "executable": str(executable["path"]),
                "expectedSha256": executable["sha256"],
                "expectedAssetSha256": {
                    "OmegaNNUEFile": network["sha256"]
                },
                "options": candidate_options,
            },
            {
                "id": "hce-control",
                "executable": str(executable["path"]),
                "expectedSha256": executable["sha256"],
                "options": dict(REQUIRED_HCE_OPTIONS),
            },
        ],
        "match": {
            "engineA": "nnue-candidate",
            "engineB": "hce-control",
            "openingsFile": str(_resolve(suite_path)),
            "repeats": 1,
            "maxPlies": policy["confirmationMaxPlies"],
            "absoluteMaxPlies": policy["confirmationAbsoluteMaxPlies"],
            "mode": "nodes",
            "nodes": policy["confirmationNodes"],
            "searchTimeoutMs": policy["searchTimeoutMs"],
            "stopGraceMs": policy["stopGraceMs"],
            "bootstrapIterations": 20000,
            "sequentialGate": {
                "candidateEngine": "nnue-candidate",
                "minimumPairs": policy["roots"],
                "nullElo": 10.0,
                "promotionAlpha": 0.05,
                "futilityBeta": 0.10,
            },
            "freshProcessPerGame": True,
        },
    }


def _select(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    lock_path = _resolve(args.lock)
    lock = _load_object(lock_path)
    if _field(lock, "kind") != "omega-nnue-confirmation-freeze":
        raise ValueError(f"not an Omega NNUE confirmation freeze lock: {lock_path}")
    freeze = lock["freeze"]
    policy = lock["policy"]
    freeze_time = _iso_utc(str(freeze["createdUtc"]))

    candidate_path = _verify_identity(
        freeze["candidateNetwork"], "candidate network"
    )
    candidate_manifest_path = _verify_identity(
        freeze["candidateManifest"], "candidate manifest"
    )
    engine_path = _verify_identity(
        freeze["engineExecutable"], "engine executable"
    )
    omega_match_path = _verify_identity(
        freeze["omegaMatchAssembly"], "OmegaMatch assembly"
    )
    omega_match_bundle = _verify_harness_bundle(freeze["omegaMatchBundle"])
    training_paths = [
        _verify_identity(item, "training corpus")
        for item in freeze["trainingCorpora"]
    ]
    manifest_paths = [
        _verify_identity(item, "training manifest")
        for item in freeze["trainingManifests"]
    ]
    exclusion_paths = [
        _verify_identity(item, "excluded position artifact")
        for item in freeze["excludedPositionArtifacts"]
    ]
    for item in freeze["launchSuites"]:
        _verify_identity(item, "launch suite")
    for item in freeze["selectorDependencies"]:
        _verify_identity(item, "selector dependency")

    candidate_manifest_value = _load_object(candidate_manifest_path)
    round_trip = _field(candidate_manifest_value, "roundTrip", {})
    if (
        not isinstance(round_trip, dict)
        or str(_field(round_trip, "sha256", "")).lower()
        != _sha256(candidate_path)
    ):
        raise ValueError(
            "candidate manifest no longer identifies the frozen network"
        )
    actual_corpus_hashes = {_sha256(path) for path in training_paths}
    training_provenance = _validate_training_provenance(
        candidate_manifest_value,
        [(path, _load_object(path)) for path in manifest_paths],
        actual_corpus_hashes,
    )
    if training_provenance != freeze.get("trainingProvenance"):
        raise ValueError("training provenance no longer matches the frozen audit")

    planned = lock["plannedOutputs"]
    suite_path = _resolve(
        args.output_suite or Path(str(planned["suite"]))
    )
    audit_path = _resolve(
        args.output_audit or Path(str(planned["audit"]))
    )
    match_config_path = _resolve(
        args.output_match_config or Path(str(planned["matchConfig"]))
    )
    output_paths = (suite_path, audit_path, match_config_path)
    if len(set(output_paths)) != len(output_paths):
        raise ValueError("suite, audit, and match-config outputs must be distinct")
    selection_seal_path = lock_path.with_name(
        "confirmation-selection.seal.json"
    )
    if selection_seal_path.exists():
        raise ValueError(
            f"this freeze lock has already been selected and sealed: "
            f"{selection_seal_path}"
        )
    existing_outputs = [path for path in output_paths if path.exists()]
    if existing_outputs:
        raise ValueError(
            "refusing to overwrite confirmation artifacts: "
            + ", ".join(str(path) for path in existing_outputs)
        )

    training_signatures, training_records = _training_inputs(training_paths)
    excluded_signatures, excluded_positions = _excluded_inputs(exclusion_paths)
    training_artifact_hashes = _manifest_source_hashes(manifest_paths)
    training_artifact_hashes.update(
        str(item["sha256"]).lower()
        for item in freeze["trainingCorpora"] + freeze["trainingManifests"]
    )

    all_candidates: list[CandidateRoot] = []
    source_evidence: list[dict[str, Any]] = []
    for source in lock["sourceConfigs"]:
        candidates, evidence = _read_source_events(
            source,
            freeze_time,
            str(freeze["engineExecutable"]["sha256"]),
            str(freeze["omegaMatchAssembly"]["sha256"]),
            str(freeze["omegaMatchBundle"]["sha256"]),
            policy,
            training_artifact_hashes,
        )
        all_candidates.extend(candidates)
        source_evidence.append(evidence)

    exclusions = Counter()
    eligible: list[CandidateRoot] = []
    for candidate in all_candidates:
        reasons: set[str] = set()
        if candidate.signature in training_signatures:
            reasons.add("training-exact-input")
        if training_signatures.intersection(candidate.orbit_signatures):
            reasons.add("training-symmetry-orbit")
        if excluded_signatures.intersection(candidate.orbit_signatures):
            reasons.add("frozen-exclusion-orbit")
        if reasons:
            for reason in reasons:
                exclusions[reason] += 1
        else:
            eligible.append(candidate)

    selected = _select_phase_balanced(eligible, int(policy["roots"]))
    source_unit_counts = Counter(item.source_unit for item in selected)
    game_counts = Counter(
        (item.run_id, item.game_id) for item in selected
    )
    phase_counts = Counter(item.phase for item in selected)
    if max(source_unit_counts.values(), default=0) != 1:
        raise AssertionError("selector violated one-root-per-source-pair policy")
    if max(game_counts.values(), default=0) != 1:
        raise AssertionError("selector violated one-root-per-source-game policy")
    if any(
        phase_counts[phase] != int(policy["phaseQuota"])
        for phase in PHASE_ORDER
    ):
        raise AssertionError("selector violated phase quota")

    openings: list[dict[str, Any]] = []
    root_details: list[dict[str, Any]] = []
    for candidate in selected:
        run_tag = hashlib.sha256(candidate.run_id.encode("utf-8")).hexdigest()[:8]
        pair_slug = SAFE_ID.sub("-", candidate.pair_id.lower()).strip("-")
        root_id = (
            f"nnue-confirm-v1-{candidate.phase}-{run_tag}-{pair_slug}-"
            f"p{candidate.ply:03d}-{candidate.orbit[:8]}"
        )
        openings.append(
            {
                "id": root_id,
                "source": (
                    "Candidate-blind HCE-only fixed-node self-play; "
                    f"run {candidate.run_id}, source pair {candidate.pair_id}, "
                    f"game {candidate.game_id}, pre-ply {candidate.ply}."
                ),
                "initialOfen": candidate.ofen,
                "moves": [],
                "phaseBucket": candidate.phase,
                "sourceRunId": candidate.run_id,
                "sourcePairId": candidate.pair_id,
                "sourceGameId": candidate.game_id,
                "sourcePly": candidate.ply,
            }
        )
        root_details.append(
            {
                "id": root_id,
                "sourceUnit": candidate.source_unit,
                "runId": candidate.run_id,
                "pairId": candidate.pair_id,
                "gameId": candidate.game_id,
                "openingId": candidate.opening_id,
                "ply": candidate.ply,
                "phase": candidate.phase,
                "pieceCount": candidate.piece_count,
                "pawnCount": candidate.pawn_count,
                "nnueInputSignature": candidate.signature,
                "symmetryOrbitKey": candidate.orbit,
                "deterministicRank": candidate.rank,
            }
        )

    suite = {
        "schemaVersion": 1,
        "name": (
            f"Omega NNUE independent confirmation v1 "
            f"({len(selected)} roots / {len(selected) * 2} AB-BA games)"
        ),
        "confirmationEligible": True,
        "candidateNetworkSha256": freeze["candidateNetwork"]["sha256"],
        "openings": openings,
    }
    _atomic_json(suite_path, suite)

    match_config = _confirmation_config(
        lock, suite_path, Path(str(planned["matchRun"]))
    )
    _atomic_json(match_config_path, match_config)
    audit = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-independent-confirmation-selection",
        "promotionEligible": True,
        "freezeLock": _identity(lock_path),
        "candidateFreeze": {
            "createdUtc": freeze["createdUtc"],
            "candidateNetwork": _identity(candidate_path),
            "candidateManifest": _identity(candidate_manifest_path),
            "engineExecutable": _identity(engine_path),
            "omegaMatchAssembly": _identity(omega_match_path),
            "omegaMatchBundle": omega_match_bundle,
            "candidateHashReverifiedBeforeSelection": True,
            "candidateManifestReverifiedBeforeSelection": True,
        },
        "independence": {
            "candidateBlind": True,
            "candidateEvaluationsRead": 0,
            "candidateNeverAppearsInSourceConfigs": True,
            "sourceUseOmegaNNUE": False,
            "sourceOwnBook": False,
            "sourceSearchMode": "fixed nodes",
            "sourceLogsCreatedAfterCandidateFreeze": True,
            "maximumRootsPerSourceGame": 1,
            "maximumRootsPerSourcePair": 1,
            "selectionUsesScores": False,
            "selectionUsesGameOutcome": False,
            "gameResultUsedForCompletenessAndSafetyOnly": True,
            "selectionUsesBestMoves": False,
            "selectionUsesPvs": False,
            "selectionUsesCandidateHash": False,
            "rootRankInputs": lock["candidateBlindContract"]["rootRankInputs"],
            "exactTrainingInputExclusion": True,
            "rulePreservingSymmetryOrbitExclusion": True,
            "rulePreservingSymmetries": [
                "identity",
                "rank-reflection-with-colour-swap",
                "horizontal-file-reflection (only without castling rights)",
                (
                    "combined-180-degree-with-colour-swap "
                    "(only without castling rights)"
                ),
            ],
        },
        "policy": policy,
        "inputs": {
            "trainingCorpora": [
                _identity(path) for path in training_paths
            ],
            "trainingManifests": [
                _identity(path) for path in manifest_paths
            ],
            "excludedPositionArtifacts": [
                _identity(path) for path in exclusion_paths
            ],
            "sourceRuns": source_evidence,
        },
        "audit": {
            "trainingRecordsRead": training_records,
            "uniqueTrainingInputs": len(training_signatures),
            "additionalExcludedPositionsRead": excluded_positions,
            "sourceCandidatePositions": len(all_candidates),
            "eligibleCandidatePositions": len(eligible),
            "exclusionsByReasonNonExclusive": dict(sorted(exclusions.items())),
        },
        "selection": {
            "roots": len(selected),
            "pairedConfirmationGames": len(selected) * 2,
            "phaseCounts": {
                phase: phase_counts[phase] for phase in PHASE_ORDER
            },
            "uniqueSourcePairs": len(source_unit_counts),
            "uniqueSourceGames": len(game_counts),
            "maximumRootsFromAnySourcePair": max(
                source_unit_counts.values(), default=0
            ),
            "maximumRootsFromAnySourceGame": max(
                game_counts.values(), default=0
            ),
            "rootsDetail": root_details,
            "suite": _identity(suite_path),
            "matchConfig": _identity(match_config_path),
        },
        "requiredNextStep": (
            "Run OmegaMatch validate on the frozen confirmation match config. "
            "Only then may candidate evaluation begin."
        ),
        "selectionSealPath": str(selection_seal_path),
    }
    _atomic_json(audit_path, audit)
    _exclusive_json(
        selection_seal_path,
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "omega-nnue-confirmation-selection-seal",
            "createdUtc": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "freezeLock": _identity(lock_path),
            "candidateNetwork": _identity(candidate_path),
            "candidateManifest": _identity(candidate_manifest_path),
            "suite": _identity(suite_path),
            "audit": _identity(audit_path),
            "matchConfig": _identity(match_config_path),
            "reselectionAllowed": False,
        },
    )
    print(
        f"Selected {len(selected)} independent roots; "
        f"phases={dict(phase_counts)}."
    )
    print(f"Suite: {suite_path}")
    print(f"Audit: {audit_path}")
    print(f"Confirmation config: {match_config_path}")
    print(f"One-time selection seal: {selection_seal_path}")
    return suite_path, audit_path, match_config_path


def _self_test_ofens() -> dict[str, str]:
    empty = ["10"] * 10

    def position(rank9: str, rank8: str, rank1: str, rank0: str) -> str:
        ranks = list(empty)
        ranks[0] = rank9
        ranks[1] = rank8
        ranks[8] = rank1
        ranks[9] = rank0
        return "/".join(ranks) + "[W/W/w/w] w - - 0 1"

    return {
        "opening": (
            "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
            "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
        ),
        "middlegame": position(
            "crnbqkbnrc", "ppp7", "PPP7", "CRNBQKBNRC"
        ),
        "late": position("crnbqkbnrc", "10", "10", "CRNBQKBNRC"),
        "endgame": position("4k5", "10", "10", "4K5"),
        "training": position("4k5", "p9", "10", "4K5"),
    }


def _self_test() -> None:
    import shutil

    root = Path(tempfile.mkdtemp(prefix="omega-confirmation-selftest-"))
    try:
        candidate = root / "candidate.nnue"
        candidate.write_bytes(b"candidate-network-v1")
        candidate_manifest = root / "candidate.manifest.json"
        engine = root / "senpai.exe"
        engine.write_bytes(b"synthetic-engine")
        omega_match = root / "OmegaMatch.dll"
        omega_match.write_bytes(b"synthetic-omega-match")
        ofens = _self_test_ofens()
        if len(_leakage_keys(ofens["opening"])[2]) != 1:
            raise AssertionError("castling position used invalid file reflection")
        if len(_leakage_keys(ofens["training"])[2]) < 2:
            raise AssertionError("castling-free position lost valid file reflection")
        training = root / "training.jsonl"
        training.write_text(
            json.dumps({"ofen": ofens["training"]}) + "\n", encoding="utf-8"
        )
        warm_training = root / "warm-training.jsonl"
        warm_training.write_text(
            json.dumps(
                {"ofen": ofens["training"].replace("p9", "1p8", 1)}
            )
            + "\n",
            encoding="utf-8",
        )
        warm_checkpoint = root / "warm.float"
        warm_checkpoint.write_bytes(b"synthetic-warm-checkpoint")
        manifest = root / "training.manifest.json"
        _atomic_json(
            manifest,
            {
                "schemaVersion": 1,
                "output": _identity(training),
                "sources": [],
            },
        )
        warm_stage_manifest = root / "warm-stage.manifest.json"
        _atomic_json(
            warm_stage_manifest,
            {
                "schemaVersion": 2,
                "inputs": [_identity(warm_training)],
                "floatCheckpoint": _identity(warm_checkpoint),
            },
        )
        _atomic_json(
            candidate_manifest,
            {
                "schemaVersion": 2,
                "inputs": [_identity(training)],
                "initialFloatCheckpoint": _identity(warm_checkpoint),
                "roundTrip": {"sha256": _sha256(candidate)},
            },
        )
        launch = root / "launch.json"
        _atomic_json(
            launch,
            {
                "schemaVersion": 1,
                "name": "synthetic four",
                "openings": [
                    {
                        "id": f"phase-{phase}",
                        "moves": ["a1a2"] if phase == "opening" else [],
                    }
                    for phase in PHASE_ORDER
                ],
            },
        )
        output = root / "workflow"
        prepare_args = argparse.Namespace(
            candidate_network=candidate,
            candidate_manifest=candidate_manifest,
            engine_executable=engine,
            omega_match_assembly=omega_match,
            launch_suite=[launch],
            training_corpus=[training, warm_training],
            training_manifest=[manifest, warm_stage_manifest],
            exclude_position_file=[],
            exclude_directory=[],
            output_dir=output,
            roots=4,
            seed=12345,
            source_nodes=17,
            confirmation_nodes=31,
            source_max_plies=20,
            source_absolute_max_plies=30,
            confirmation_max_plies=40,
            confirmation_absolute_max_plies=50,
            search_timeout_ms=1000,
            stop_grace_ms=100,
            minimum_source_ply=1,
            minimum_remaining_plies=0,
            _self_test=True,
        )
        bad_provenance_args = argparse.Namespace(**vars(prepare_args))
        bad_provenance_args.output_dir = root / "bad-provenance"
        bad_provenance_args.training_corpus = [training]
        try:
            _prepare(bad_provenance_args)
        except ValueError as error:
            if "were not frozen for exclusion screening" not in str(error):
                raise
        else:
            raise AssertionError(
                "an omitted warm-start-stage corpus was not rejected"
            )
        lock_path = _prepare(prepare_args)
        lock = _load_object(lock_path)
        if len(lock["freeze"]["trainingProvenance"]["warmStartChain"]) != 1:
            raise AssertionError("warm-start provenance chain was not frozen")
        rogue_dependency = root / "ChessLib.dll"
        rogue_dependency.write_bytes(b"synthetic-mutated-referee")
        try:
            _verify_harness_bundle(lock["freeze"]["omegaMatchBundle"])
        except ValueError as error:
            if "runtime bundle changed after freeze" not in str(error):
                raise
        else:
            raise AssertionError("runtime dependency mutation was not rejected")
        rogue_dependency.unlink()
        source = lock["sourceConfigs"][0]
        config = _load_object(Path(source["config"]["path"]))
        if (
            config["expectedOpeningSuiteSha256"]
            != source["launchSuite"]["sha256"]
        ):
            raise AssertionError("source opening suite was not hash-pinned")
        if (
            config["expectedHarnessBundleSha256"]
            != lock["freeze"]["omegaMatchBundle"]["sha256"]
        ):
            raise AssertionError("source runtime bundle was not hash-pinned")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        events: list[dict[str, Any]] = [
            {
                "RecordType": "run",
                "RunId": source["runId"],
                "CreatedUtc": now,
                "ConfigSha256": source["config"]["sha256"],
                "OpeningSuiteSha256": source["launchSuite"]["sha256"],
                "HarnessSha256": lock["freeze"]["omegaMatchAssembly"]["sha256"],
                "HarnessBundleSha256": (
                    lock["freeze"]["omegaMatchBundle"]["sha256"]
                ),
                "Match": {
                    "Mode": "nodes",
                    "Nodes": 17,
                    "Repeats": 1,
                },
                "Engines": [
                    {
                        "Id": item["id"],
                        "Sha256": lock["freeze"]["engineExecutable"]["sha256"],
                        "Options": item["options"],
                        "OmegaNnueActiveVerified": False,
                        "ExternalAssets": [],
                    }
                    for item in config["engines"]
                ],
            }
        ]
        for phase in PHASE_ORDER:
            pair_id = f"phase-{phase}-r001"
            forced_moves = ["a1a2"] if phase == "opening" else []
            source_ply = len(forced_moves) + 1
            candidate_ply = source_ply + (1 if phase == "opening" else 0)
            phase_white = ofens[phase]
            phase_black = phase_white.replace(" w ", " b ", 1)
            for flavor, white, black in (
                ("ab", "hce-source-a", "hce-source-b"),
                ("ba", "hce-source-b", "hce-source-a"),
            ):
                game_id = f"{pair_id}-{flavor}"
                common = {
                    "GameId": game_id,
                    "PairId": pair_id,
                    "Attempt": 1,
                    "OpeningId": f"phase-{phase}",
                    "WhiteEngineId": white,
                    "BlackEngineId": black,
                }
                events.append(
                    {
                        "RecordType": "gameStart",
                        **common,
                        "InitialOfen": OMEGA_START,
                        "OpeningMoves": forced_moves,
                    }
                )
                events.append(
                    {
                        "RecordType": "ply",
                        "GameId": game_id,
                        "Attempt": 1,
                        "Ply": candidate_ply,
                        "EngineId": black if phase == "opening" else white,
                        "Color": "black" if phase == "opening" else "white",
                        "PreOfen": (
                            phase_black if phase == "opening" else phase_white
                        ),
                        "PostOfen": (
                            phase_white if phase == "opening" else phase_black
                        ),
                        "Error": None,
                        "Search": {"Command": "go nodes 17"},
                    }
                )
                if phase == "opening":
                    events[-1:-1] = [
                        {
                            "RecordType": "ply",
                            "GameId": game_id,
                            "Attempt": 1,
                            "Ply": source_ply,
                            "EngineId": white,
                            "Color": "white",
                            "PreOfen": ofens["training"],
                            "PostOfen": phase_black,
                            "Error": None,
                            "Search": {"Command": "go nodes 17"},
                        }
                    ]
                events.append(
                    {
                        "RecordType": "gameResult",
                        **common,
                        "Result": "1/2-1/2",
                        "Plies": candidate_ply,
                        "FinalOfen": (
                            phase_white if phase == "opening" else phase_black
                        ),
                        "IllegalMoves": 0,
                        "IllegalPvs": 0,
                        "ProtocolFailures": 0,
                        "TimeForfeits": 0,
                    }
                )
        events_path = Path(source["eventsPath"])
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("w", encoding="utf-8", newline="\n") as stream:
            for event in events:
                stream.write(json.dumps(event, separators=(",", ":")) + "\n")
        frozen_events = events_path.read_bytes()

        events[0]["Engines"][0]["Options"]["UseOmegaNNUE"] = "true"
        with events_path.open("w", encoding="utf-8", newline="\n") as stream:
            for event in events:
                stream.write(json.dumps(event, separators=(",", ":")) + "\n")
        try:
            _select(
                argparse.Namespace(
                    lock=lock_path,
                    output_suite=None,
                    output_audit=None,
                    output_match_config=None,
                )
            )
        except ValueError as error:
            if "locked HCE-only control" not in str(error):
                raise
        else:
            raise AssertionError("NNUE-enabled source generation was not rejected")
        events_path.write_bytes(frozen_events)

        suite_path, audit_path, match_path = _select(
            argparse.Namespace(
                lock=lock_path,
                output_suite=None,
                output_audit=None,
                output_match_config=None,
            )
        )
        suite = _load_object(suite_path)
        audit = _load_object(audit_path)
        match = _load_object(match_path)
        if len(suite["openings"]) != 4:
            raise AssertionError("self-test did not select four roots")
        if audit["selection"]["uniqueSourcePairs"] != 4:
            raise AssertionError("self-test reused a source pair")
        if set(audit["selection"]["phaseCounts"].values()) != {1}:
            raise AssertionError("self-test phase balance failed")
        if audit["audit"]["exclusionsByReasonNonExclusive"].get(
            "training-exact-input", 0
        ) < 1:
            raise AssertionError("self-test did not exercise training exclusion")
        if match["engines"][0]["options"]["UseOmegaNNUE"] != "true":
            raise AssertionError("candidate was not configured for NNUE")
        if match["engines"][1]["options"]["UseOmegaNNUE"] != "false":
            raise AssertionError("control was not configured for HCE")
        if match["expectedOpeningSuiteSha256"] != _sha256(suite_path):
            raise AssertionError("confirmation opening suite was not hash-pinned")
        if (
            match["expectedHarnessBundleSha256"]
            != lock["freeze"]["omegaMatchBundle"]["sha256"]
        ):
            raise AssertionError("confirmation runtime bundle was not hash-pinned")

        candidate.write_bytes(b"candidate-network-v2")
        try:
            _select(
                argparse.Namespace(
                    lock=lock_path,
                    output_suite=None,
                    output_audit=None,
                    output_match_config=None,
                )
            )
        except ValueError as error:
            if "candidate network changed after freeze" not in str(error):
                raise
        else:
            raise AssertionError("candidate mutation was not rejected")
        candidate.write_bytes(b"candidate-network-v1")
        try:
            _select(
                argparse.Namespace(
                    lock=lock_path,
                    output_suite=None,
                    output_audit=None,
                    output_match_config=None,
                )
            )
        except ValueError as error:
            if "already been selected and sealed" not in str(error):
                raise
        else:
            raise AssertionError("sealed confirmation lock was reselected")
        print("confirmation_suite self-test passed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    args = _parse_args()
    if args.command == "prepare":
        _prepare(args)
    elif args.command == "select":
        _select(args)
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
