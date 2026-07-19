#!/usr/bin/env python3
"""Build and assess the preregistered king-state Omega NNUE match gates.

This tool deliberately keeps root construction separate from engine play:

* ``sample`` runs the existing rules-only CoreChess sampler with the three
  preregistered seeds.  No evaluator, result, score, PV, or candidate identity
  participates.
* ``seal`` selects all three mutually orbit-disjoint suites at once, excludes
  every supplied training/historical orbit, writes pinned OmegaMatch configs,
  and exclusively publishes a one-time provenance seal.
* ``verify`` re-hashes the complete seal before a match is started or resumed.
* ``assess`` independently checks complete colour-swapped pairs, four-phase
  balanced sequential checkpoints, safety counters, and equal-time telemetry.

The script never starts a real match.  OmegaMatch remains the legality referee
and match runner.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Iterator, Sequence

from omega_nnue import parse_ofen
from select_screen import input_keys, observable_ofen, phase_of


SCHEMA_VERSION = 1
PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
SAFE_ID = re.compile(r"[^a-z0-9]+")
HEX_256 = re.compile(r"^[0-9a-f]{64}$")

SAMPLER_TRAJECTORY_PAIRS = 2_048
SAMPLER_MAX_PLIES = 220
SAMPLER_POSITIONS_PER_PHASE_SIDE = 2
SAMPLER_CAPTURE_PERCENT = 72

GATE_SPECS: dict[str, dict[str, Any]] = {
    "development": {
        "seed": 2026071901,
        "roots": 32,
        "rootsPerPhase": 8,
        "rootsPerPhaseSide": 4,
        "mode": "nodes",
        "nodes": 20_000,
        "searchTimeoutMs": 60_000,
        "minimumCandidateScore": 0.40,
    },
    "equal-node": {
        "seed": 2026071902,
        "roots": 128,
        "rootsPerPhase": 32,
        "rootsPerPhaseSide": 16,
        "mode": "nodes",
        "nodes": 50_000,
        "searchTimeoutMs": 60_000,
    },
    "equal-time": {
        "seed": 2026071903,
        "roots": 128,
        "rootsPerPhase": 32,
        "rootsPerPhaseSide": 16,
        "mode": "moveTime",
        "moveTimeMs": 1_000,
        "searchTimeoutMs": 5_000,
    },
}

MAX_PLIES = 300
ABSOLUTE_MAX_PLIES = 400
STOP_GRACE_MS = 2_000
HASH_MIB = 128
BOOTSTRAP_ITERATIONS = 20_000
MINIMUM_GATE_PAIRS = 64
MAXIMUM_GATE_PAIRS = 128
NULL_ELO = 10.0
PROMOTION_ALPHA = 0.05
FUTILITY_BETA = 0.10
PROMOTION_E_VALUE = 20.0
FUTILITY_E_VALUE = 10.0
BET_FRACTIONS = (
    1.0 / 128,
    1.0 / 64,
    1.0 / 32,
    1.0 / 16,
    1.0 / 8,
    1.0 / 4,
    1.0 / 2,
    3.0 / 4,
)

ENGINE_OPTIONS = {
    "Threads": "1",
    "Hash": str(HASH_MIB),
    "Ponder": "false",
    "OwnBook": "false",
    "UCI_Chess960": "false",
    "UCI_Variant": "omega",
}

SOURCE_NAMES = {
    "development": "king-state-development-rules-only.jsonl",
    "equal-node": "king-state-equal-node-rules-only.jsonl",
    "equal-time": "king-state-equal-time-rules-only.jsonl",
}


@dataclass(frozen=True)
class Root:
    gate: str
    source_path: Path
    source_sha256: str
    line: int
    generator_seed: int
    trajectory_pair_id: str
    trajectory_id: str
    flavor: str
    ply: int
    phase: str
    side: str
    ofen: str
    identity: str
    orbit: str
    orbit_signatures: tuple[str, ...]
    rank: str

    @property
    def source_group(self) -> str:
        return f"{self.generator_seed}:{self.trajectory_pair_id}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        _resolve(path).relative_to(_resolve(root))
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    path = _resolve(path)
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "sha256": _sha256(path)}


def _verify_identity(identity: dict[str, Any], label: str) -> Path:
    path = _resolve(Path(str(identity.get("path", ""))))
    actual = _identity(path)
    if (
        int(identity.get("bytes", -1)) != actual["bytes"]
        or str(identity.get("sha256", "")).lower() != actual["sha256"]
    ):
        raise ValueError(
            f"{label} changed after seal: {path}; expected "
            f"{identity.get('sha256')}, got {actual['sha256']}"
        )
    return path


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _exclusive_json(path: Path, value: Any) -> None:
    _exclusive_bytes(path, _canonical_json(value))


def _atomic_json(path: Path, value: Any) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(_resolve(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _field(value: dict[str, Any], name: str, default: Any = None) -> Any:
    for key, item in value.items():
        if key.lower() == name.lower():
            return item
    return default


def _has_field(value: dict[str, Any], name: str) -> bool:
    return any(key.lower() == name.lower() for key in value)


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _finite_nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite nonnegative number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return numeric


def _safe_id(text: str) -> str:
    safe = SAFE_ID.sub("-", text.lower()).strip("-")
    return safe[:88] or "root"


def _leakage_keys(ofen: str) -> tuple[str, str, tuple[str, ...]]:
    """Use a conservative rule-preserving NNUE-input orbit.

    En-passant and clocks are intentionally ignored for leakage detection.
    Horizontal reflection is allowed only after castling rights disappear;
    with rights present Omega's f-file castling geometry is not reflected.
    """

    observable = observable_ofen(ofen)
    identity, orbit, signatures = input_keys(observable)
    if observable.split()[2] != "-":
        return identity, identity, (identity,)
    return identity, orbit, signatures


def _position_meta(ofen: str) -> tuple[str, str, str, str, tuple[str, ...]]:
    pieces, side, _ = parse_ofen(ofen)
    phase = phase_of(len(pieces))
    if phase not in PHASES:
        raise ValueError("match root must contain at least seven pieces")
    identity, orbit, signatures = _leakage_keys(ofen)
    return phase, side, identity, orbit, signatures


def _jsonl(path: Path, *, allow_truncated_final: bool = False) -> Iterator[tuple[int, dict[str, Any]]]:
    lines = _resolve(path).read_text(encoding="utf-8").splitlines()
    nonempty = [index for index, line in enumerate(lines, 1) if line.strip()]
    last = nonempty[-1] if nonempty else -1
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            if allow_truncated_final and line_number == last:
                return
            raise ValueError(f"{path}:{line_number}: {error}") from error
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: record is not an object")
        yield line_number, value


def _walk_ofens(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {
                "ofen",
                "initialofen",
                "preofen",
                "postofen",
                "finalofen",
            } and isinstance(item, str):
                yield item
            yield from _walk_ofens(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_ofens(item)


def _file_ofens(path: Path, *, strict: bool) -> Iterator[str]:
    path = _resolve(path)
    try:
        if path.suffix.lower() == ".jsonl":
            for _, record in _jsonl(path, allow_truncated_final=not strict):
                yield from _walk_ofens(record)
        elif path.suffix.lower() == ".json":
            yield from _walk_ofens(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        if strict:
            raise


def _harness_bundle_identity(assembly: Path) -> dict[str, Any]:
    assembly = _resolve(assembly)
    root = assembly.parent

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
        item = _identity(path)
        relative = path.relative_to(root).as_posix()
        record = {
            "relativePath": relative,
            "bytes": item["bytes"],
            "sha256": item["sha256"],
        }
        files.append(record)
        canonical.extend(
            f"{relative}\t{item['bytes']}\t{item['sha256']}\n".encode("utf-8")
        )
    relative_assembly = assembly.relative_to(root).as_posix()
    if not any(item["relativePath"] == relative_assembly for item in files):
        raise ValueError("OmegaMatch.dll is absent from its runtime bundle")
    return {
        "root": str(root),
        "assemblyRelativePath": relative_assembly,
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def _verify_harness_bundle(bundle: dict[str, Any]) -> None:
    root = _resolve(Path(str(bundle["root"])))
    assembly = root / str(bundle["assemblyRelativePath"])
    current = _harness_bundle_identity(assembly)
    if current != bundle:
        raise ValueError(
            "OmegaMatch runtime bundle changed after seal: "
            f"expected {bundle.get('sha256')}, got {current.get('sha256')}"
        )


def _protocol_path() -> Path:
    return _resolve(
        Path(__file__).parents[2]
        / "validation"
        / "omega-nnue-king-state-v1-protocol.json"
    )


def _sampler_project_path() -> Path:
    return _resolve(Path(__file__).parent / "OmegaRootSampler" / "OmegaRootSampler.csproj")


def _validate_protocol(path: Path) -> dict[str, Any]:
    protocol = _load_object(path)
    expected = {
        ("kind",): "omega-nnue-king-state-v1-preregistration",
        ("freshSuiteGeneration", "developmentScreenSeed"): 2026071901,
        ("freshSuiteGeneration", "equalNodeConfirmationSeed"): 2026071902,
        ("freshSuiteGeneration", "equalTimeConfirmationSeed"): 2026071903,
        ("freshSuiteGeneration", "configSeedEqualsSuiteSeed"): True,
        (
            "freshSuiteGeneration",
            "schedule",
            "inverseArrangeForSeededDotNetRandomShuffle",
        ): True,
        ("freshSuiteGeneration", "schedule", "balancedPairBlockSize"): 4,
        ("freshSuiteGeneration", "schedule", "onePairFromEachPhasePerBlock"): True,
        (
            "freshSuiteGeneration",
            "schedule",
            "rootSideToMoveConstantWithinBlock",
        ): True,
        (
            "freshSuiteGeneration",
            "schedule",
            "rootSideToMoveAlternatesBetweenBlocks",
        ): True,
        ("matchExecution", "repeats"): 1,
        ("matchExecution", "freshProcessPerGame"): True,
        ("matchExecution", "maxPlies"): MAX_PLIES,
        ("matchExecution", "absoluteMaxPlies"): ABSOLUTE_MAX_PLIES,
        ("matchExecution", "stopGraceMs"): STOP_GRACE_MS,
        ("matchExecution", "bootstrapIterations"): BOOTSTRAP_ITERATIONS,
        ("matchExecution", "searchTimeoutMs", "developmentScreen"): 60_000,
        ("matchExecution", "searchTimeoutMs", "equalNode"): 60_000,
        ("matchExecution", "searchTimeoutMs", "equalTime"): 5_000,
        ("developmentScreen", "roots"): 32,
        ("developmentScreen", "rootsPerPhase"): 8,
        ("developmentScreen", "rootsPerPhaseAndSideToMove"): 4,
        ("developmentScreen", "gamesPerRoot"): 2,
        ("developmentScreen", "nodesPerMove"): 20_000,
        ("developmentScreen", "minimumCandidateScore"): 0.4,
        ("developmentScreen", "maximumSafetyFailures"): 0,
        ("equalNodeGate", "maximumPairs"): 128,
        ("equalNodeGate", "pairsPerPhase"): 32,
        ("equalNodeGate", "pairsPerPhaseAndSideToMove"): 16,
        ("equalNodeGate", "gamesPerPair"): 2,
        ("equalNodeGate", "nodesPerMove"): 50_000,
        ("equalNodeGate", "minimumPairsBeforeDecision"): 64,
        ("equalNodeGate", "nullElo"): 10,
        ("equalNodeGate", "alpha"): 0.05,
        ("equalNodeGate", "beta"): 0.1,
        ("equalNodeGate", "promotionEValue"): 20,
        ("equalNodeGate", "futilityEValue"): 10,
        ("equalNodeGate", "balancedBlockChecksOnly"): True,
        ("equalNodeGate", "maximumSafetyFailures"): 0,
        ("equalTimeGate", "maximumPairs"): 128,
        ("equalTimeGate", "pairsPerPhase"): 32,
        ("equalTimeGate", "pairsPerPhaseAndSideToMove"): 16,
        ("equalTimeGate", "gamesPerPair"): 2,
        ("equalTimeGate", "moveTimeMs"): 1_000,
        ("equalTimeGate", "oneGameAtATime"): True,
        ("equalTimeGate", "idleMachineRequired"): True,
        ("equalTimeGate", "minimumPairsBeforeDecision"): 64,
        ("equalTimeGate", "nullElo"): 10,
        ("equalTimeGate", "alpha"): 0.05,
        ("equalTimeGate", "beta"): 0.1,
        ("equalTimeGate", "promotionEValue"): 20,
        ("equalTimeGate", "futilityEValue"): 10,
        ("equalTimeGate", "balancedBlockChecksOnly"): True,
        ("equalTimeGate", "maximumSafetyFailures"): 0,
    }
    for path_parts, wanted in expected.items():
        current: Any = protocol
        for part in path_parts:
            if not isinstance(current, dict) or part not in current:
                raise ValueError(f"protocol is missing {'.'.join(path_parts)}")
            current = current[part]
        if current != wanted:
            raise ValueError(
                f"protocol {'.'.join(path_parts)} must be {wanted!r}, got {current!r}"
            )
    sampler = protocol["freshSuiteGeneration"]["rulesOnlySamplerPolicy"]
    if sampler != {
        "deterministicPrng": "SplitMix64",
        "trajectoryPairs": SAMPLER_TRAJECTORY_PAIRS,
        "independentTrajectoriesPerPair": 2,
        "maxPlies": SAMPLER_MAX_PLIES,
        "positionsPerPhaseAndSide": SAMPLER_POSITIONS_PER_PHASE_SIDE,
        "captureSelectionPercent": SAMPLER_CAPTURE_PERCENT,
    }:
        raise ValueError("protocol rules-only sampler policy changed")
    return protocol


def _all_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def _top_level_input_hashes(value: dict[str, Any], label: str) -> set[str]:
    inputs = _field(value, "inputs")
    if inputs is None:
        return set()
    if not isinstance(inputs, list):
        raise ValueError(f"{label}.inputs must be a list of file identities")
    hashes: set[str] = set()
    for index, item in enumerate(inputs, 1):
        if not isinstance(item, dict):
            raise ValueError(f"{label}.inputs[{index}] is not a file identity")
        sha = str(_field(item, "sha256", "")).lower()
        if not HEX_256.fullmatch(sha):
            raise ValueError(f"{label}.inputs[{index}] has an invalid SHA-256")
        hashes.add(sha)
    if len(hashes) != len(inputs):
        raise ValueError(f"{label}.inputs contains duplicate corpus identities")
    return hashes


def _nested_file_identities(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        path = _field(value, "path")
        size = _field(value, "bytes")
        sha = str(_field(value, "sha256", "")).lower()
        if (
            isinstance(path, str)
            and isinstance(size, int)
            and size >= 0
            and HEX_256.fullmatch(sha)
        ):
            yield {"path": path, "bytes": size, "sha256": sha}
        for item in value.values():
            yield from _nested_file_identities(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nested_file_identities(item)


def _same_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    try:
        return (
            _resolve(Path(str(_field(left, "path"))))
            == _resolve(Path(str(_field(right, "path"))))
            and int(_field(left, "bytes", -1)) == int(_field(right, "bytes", -2))
            and str(_field(left, "sha256", "")).lower()
            == str(_field(right, "sha256", "")).lower()
        )
    except (OSError, TypeError, ValueError):
        return False


def _sample(args: argparse.Namespace) -> None:
    sampler_dir = _resolve(args.output_dir)
    sampler_dir.mkdir(parents=True, exist_ok=True)
    project = _resolve(args.sampler_project)
    dotnet = _resolve(args.dotnet)
    _validate_protocol(args.protocol)
    if not project.is_file():
        raise FileNotFoundError(project)
    if not dotnet.is_file():
        raise FileNotFoundError(dotnet)
    runtime_dir = sampler_dir / "runtime"
    sampler_assembly = runtime_dir / "OmegaRootSampler.dll"
    if runtime_dir.exists():
        raise FileExistsError(
            f"refusing to replace isolated sampler runtime: {runtime_dir}"
        )
    build_command = [
        str(dotnet),
        "build",
        str(project),
        "-c",
        "Release",
        "--output",
        str(runtime_dir),
    ]
    print(
        " ".join(
            f'"{item}"' if " " in item else item for item in build_command
        )
    )
    subprocess.run(build_command, check=True)
    if not sampler_assembly.is_file():
        raise FileNotFoundError(
            f"isolated sampler build did not produce {sampler_assembly}"
        )
    for gate, spec in GATE_SPECS.items():
        output = sampler_dir / SOURCE_NAMES[gate]
        manifest = Path(str(output) + ".manifest.json")
        if output.exists() or manifest.exists():
            raise FileExistsError(f"refusing to replace rules-only source: {output}")
        command = [
            str(dotnet),
            str(sampler_assembly),
            "--output",
            str(output),
            "--seed",
            str(spec["seed"]),
            "--trajectory-pairs",
            str(SAMPLER_TRAJECTORY_PAIRS),
            "--max-plies",
            str(SAMPLER_MAX_PLIES),
            "--positions-per-phase-side",
            str(SAMPLER_POSITIONS_PER_PHASE_SIDE),
            "--capture-percent",
            str(SAMPLER_CAPTURE_PERCENT),
        ]
        print(" ".join(f'"{item}"' if " " in item else item for item in command))
        subprocess.run(command, check=True)


def _verify_sampler_source(path: Path, gate: str) -> tuple[list[Root], dict[str, Any]]:
    path = _resolve(path)
    manifest_path = Path(str(path) + ".manifest.json")
    manifest = _load_object(manifest_path)
    spec = GATE_SPECS[gate]
    if manifest.get("kind") != "omega-rules-only-random-root-manifest":
        raise ValueError(f"{manifest_path}: wrong sampler manifest kind")
    policy = manifest.get("policy")
    if not isinstance(policy, dict):
        raise ValueError(f"{manifest_path}: missing sampler policy")
    exact_policy = {
        "deterministicPrng": "SplitMix64",
        "seed": str(spec["seed"]),
        "trajectoryPairs": SAMPLER_TRAJECTORY_PAIRS,
        "independentTrajectoriesPerPair": 2,
        "maxPlies": SAMPLER_MAX_PLIES,
        "positionsPerPhaseAndSide": SAMPLER_POSITIONS_PER_PHASE_SIDE,
        "captureSelectionPercent": SAMPLER_CAPTURE_PERCENT,
        "terminalRootsEmitted": 0,
        "maximumHalfmoveClock": 89,
        "minimumPieces": 7,
        "minimumPiecesPerSide": 2,
        "phasePlyWindows": {
            "opening": [6, 48],
            "middlegame": [20, 140],
            "late": [40, 260],
            "endgame": [60, 400],
        },
    }
    if policy != exact_policy:
        raise ValueError(f"{manifest_path}: sampler policy differs from frozen policy")
    output = manifest.get("output")
    if not isinstance(output, dict):
        raise ValueError(f"{manifest_path}: missing output identity")
    actual = _identity(path)
    if int(output.get("bytes", -1)) != actual["bytes"] or str(
        output.get("sha256", "")
    ).lower() != actual["sha256"]:
        raise ValueError(f"{manifest_path}: source output identity mismatch")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError(f"{manifest_path}: missing sampler runtime identity")
    runtime_identities: list[dict[str, Any]] = []
    for key in ("samplerAssembly", "chessLibAssembly"):
        identity_record = runtime.get(key)
        if not isinstance(identity_record, dict):
            raise ValueError(f"{manifest_path}: missing runtime.{key}")
        _verify_identity(identity_record, f"{gate} sampler runtime {key}")
        runtime_identities.append(identity_record)

    roots: list[Root] = []
    seen_ranks: set[str] = set()
    counts: Counter[tuple[str, str]] = Counter()
    for line, record in _jsonl(path):
        if record.get("kind") != "omega-rules-only-random-root":
            raise ValueError(f"{path}:{line}: wrong rules-only record kind")
        if int(record.get("generatorSeed", -1)) != spec["seed"]:
            raise ValueError(f"{path}:{line}: generator seed mismatch")
        phase, side, identity, orbit, signatures = _position_meta(str(record["ofen"]))
        if phase != record.get("phase") or side != record.get("sideToMove"):
            raise ValueError(f"{path}:{line}: OFEN metadata mismatch")
        pieces, _, _ = parse_ofen(str(record["ofen"]))
        if int(record.get("pieceCount", -1)) != len(pieces):
            raise ValueError(f"{path}:{line}: piece-count metadata mismatch")
        pair = str(record.get("trajectoryPairId", ""))
        trajectory = str(record.get("trajectoryId", ""))
        flavor = str(record.get("flavor", ""))
        if not re.fullmatch(r"random-pair-\d{6}", pair):
            raise ValueError(f"{path}:{line}: malformed trajectoryPairId")
        if flavor not in {"ab", "ba"} or trajectory != f"{pair}-{flavor}":
            raise ValueError(f"{path}:{line}: malformed trajectory identity")
        source_rank = str(record.get("selectionRank", ""))
        if not HEX_256.fullmatch(source_rank) or source_rank in seen_ranks:
            raise ValueError(f"{path}:{line}: invalid or duplicate selection rank")
        seen_ranks.add(source_rank)
        rank_payload = (
            f"king-state-match-root-v1\0{spec['seed']}\0{pair}\0"
            f"{trajectory}\0{record['ply']}\0{orbit}"
        )
        rank = hashlib.sha256(rank_payload.encode("utf-8")).hexdigest()
        roots.append(
            Root(
                gate=gate,
                source_path=path,
                source_sha256=actual["sha256"],
                line=line,
                generator_seed=spec["seed"],
                trajectory_pair_id=pair,
                trajectory_id=trajectory,
                flavor=flavor,
                ply=int(record["ply"]),
                phase=phase,
                side=side,
                ofen=str(record["ofen"]),
                identity=identity,
                orbit=orbit,
                orbit_signatures=signatures,
                rank=rank,
            )
        )
        counts[(phase, side)] += 1
    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError(f"{manifest_path}: missing coverage audit")
    expected_phase_counts = {
        phase: sum(counts[(phase, side)] for side in SIDES) for phase in PHASES
    }
    expected_side_counts = {
        side: sum(counts[(phase, side)] for phase in PHASES) for side in SIDES
    }
    if (
        int(coverage.get("records", -1)) != len(roots)
        or coverage.get("phaseCounts") != expected_phase_counts
        or coverage.get("sideToMoveCounts") != expected_side_counts
    ):
        raise ValueError(f"{manifest_path}: coverage counts differ from source")
    for phase in PHASES:
        for side in SIDES:
            if counts[(phase, side)] < int(spec["rootsPerPhaseSide"]):
                raise ValueError(
                    f"{path}: insufficient {phase}/{side} candidates "
                    f"({counts[(phase, side)]})"
                )
    return roots, {
        "source": actual,
        "manifest": _identity(manifest_path),
        "runtime": runtime_identities,
        "records": len(roots),
        "phaseSideCounts": {
            f"{phase}:{side}": counts[(phase, side)]
            for phase in PHASES
            for side in SIDES
        },
    }


def _default_exclusion_roots(repo: Path) -> list[Path]:
    workspace = repo.parent
    return [
        repo / "validation",
        repo / "build-msvc" / "confirmation",
        workspace / "match-runs" / "output",
        workspace / "omega-lab" / "regressions",
    ]


def _expand_json_files(paths: Iterable[Path], excluded_tree: Path) -> list[Path]:
    files: set[Path] = set()
    excluded_tree = _resolve(excluded_tree)
    for item in paths:
        item = _resolve(item)
        if not item.exists():
            continue
        candidates = [item] if item.is_file() else list(item.rglob("*"))
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.lower() not in {".json", ".jsonl"}:
                continue
            try:
                candidate.relative_to(excluded_tree)
                continue
            except ValueError:
                pass
            files.add(candidate)
    return sorted(files, key=lambda item: str(item).lower())


def _read_forbidden(
    training_corpora: Sequence[Path],
    exclusion_files: Sequence[Path],
) -> tuple[set[str], list[dict[str, Any]], dict[str, Any]]:
    signatures: set[str] = set()
    identities: list[dict[str, Any]] = []
    stats = Counter()
    for strict, files in ((True, training_corpora), (False, exclusion_files)):
        for path in files:
            path = _resolve(path)
            identities.append(_identity(path))
            for ofen in _file_ofens(path, strict=strict):
                try:
                    _, _, orbit_signatures = _leakage_keys(ofen)
                except (KeyError, TypeError, ValueError):
                    if strict:
                        raise ValueError(f"{path}: invalid OFEN in frozen training input")
                    stats["ignoredMalformedHistoricalOfens"] += 1
                    continue
                signatures.update(orbit_signatures)
                stats["trainingOfens" if strict else "historicalOfens"] += 1
    return signatures, identities, dict(stats)


class _DotNetRandom:
    """Compatibility algorithm used by ``new Random(int seed)`` in .NET."""

    _MBIG = 2_147_483_647
    _MSEED = 161_803_398

    def __init__(self, seed: int):
        subtraction = self._MBIG if seed == -2_147_483_648 else abs(seed)
        mj = self._MSEED - subtraction
        if mj < 0:
            mj += self._MBIG
        self._seed = [0] * 56
        self._seed[55] = mj
        mk = 1
        for index in range(1, 55):
            slot = (21 * index) % 55
            self._seed[slot] = mk
            mk = mj - mk
            if mk < 0:
                mk += self._MBIG
            mj = self._seed[slot]
        for _ in range(4):
            for index in range(1, 56):
                self._seed[index] -= self._seed[1 + (index + 30) % 55]
                if self._seed[index] < 0:
                    self._seed[index] += self._MBIG
        self._inext = 0
        self._inextp = 21

    def next(self, maximum: int) -> int:
        self._inext += 1
        if self._inext >= 56:
            self._inext = 1
        self._inextp += 1
        if self._inextp >= 56:
            self._inextp = 1
        value = self._seed[self._inext] - self._seed[self._inextp]
        if value == self._MBIG:
            value -= 1
        if value < 0:
            value += self._MBIG
        self._seed[self._inext] = value
        return int(value * (1.0 / self._MBIG) * maximum)


def _shuffled_indices(count: int, seed: int) -> list[int]:
    order = list(range(count))
    random = _DotNetRandom(seed)
    for index in range(count - 1, 0, -1):
        other = random.next(index + 1)
        order[index], order[other] = order[other], order[index]
    return order


def _select_roots(
    roots: Sequence[Root],
    spec: dict[str, Any],
    forbidden: set[str],
    used_fresh: set[str],
) -> tuple[list[Root], dict[str, int]]:
    chosen: list[Root] = []
    used_groups: set[str] = set()
    rejected = Counter()
    per_bucket = int(spec["rootsPerPhaseSide"])
    for phase in PHASES:
        for side in SIDES:
            bucket = sorted(
                (root for root in roots if root.phase == phase and root.side == side),
                key=lambda root: root.rank,
            )
            accepted = 0
            for root in bucket:
                reasons = []
                if root.source_group in used_groups:
                    reasons.append("trajectory-pair-reuse")
                if forbidden.intersection(root.orbit_signatures):
                    reasons.append("training-or-historical-orbit")
                if used_fresh.intersection(root.orbit_signatures):
                    reasons.append("fresh-suite-orbit")
                if reasons:
                    rejected.update(reasons)
                    continue
                chosen.append(root)
                used_groups.add(root.source_group)
                used_fresh.update(root.orbit_signatures)
                accepted += 1
                if accepted == per_bucket:
                    break
            if accepted != per_bucket:
                raise ValueError(
                    f"could select only {accepted}/{per_bucket} {phase}/{side} roots"
                )
    return chosen, dict(rejected)


def _suite(gate: str, roots: Sequence[Root]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = GATE_SPECS[gate]
    by_bucket: dict[tuple[str, str], list[Root]] = {}
    for phase in PHASES:
        for side in SIDES:
            values = sorted(
                (root for root in roots if root.phase == phase and root.side == side),
                key=lambda root: root.rank,
            )
            if len(values) != spec["rootsPerPhaseSide"]:
                raise AssertionError("selected phase/side quota changed")
            by_bucket[(phase, side)] = values

    schedule: list[dict[str, Any]] = []
    blocks = int(spec["rootsPerPhase"])
    for block_index in range(blocks):
        side = SIDES[block_index % 2]
        bucket_index = block_index // 2
        for phase in PHASES:
            root = by_bucket[(phase, side)][bucket_index]
            opening_id = _safe_id(
                f"ksv1-{gate}-b{block_index + 1:03d}-{phase}-{side}-"
                f"{root.orbit[:12]}"
            )
            schedule.append(
                {
                    "id": opening_id,
                    "source": "preregistered rules-only OmegaRootSampler",
                    "initialOfen": root.ofen,
                    "moves": [],
                    "kingStateMatch": {
                        "gate": gate,
                        "phase": phase,
                        "rootSideToMove": side,
                        "balancedBlock": block_index + 1,
                        "generatorSeed": root.generator_seed,
                        "trajectoryPairId": root.trajectory_pair_id,
                        "trajectoryId": root.trajectory_id,
                        "sourceLine": root.line,
                        "sourceSha256": root.source_sha256,
                        "selectionRank": root.rank,
                        "identityKey": root.identity,
                        "symmetryOrbitKey": root.orbit,
                        "orbitSignatures": list(root.orbit_signatures),
                    },
                }
            )

    # ArenaRunner performs a seeded Fisher-Yates shuffle.  Invert that
    # permutation so pair-budget increments of four follow complete,
    # predeclared phase-balanced blocks.
    permutation = _shuffled_indices(len(schedule), int(spec["seed"]))
    source_order: list[dict[str, Any] | None] = [None] * len(schedule)
    for scheduled_index, original_index in enumerate(permutation):
        source_order[original_index] = schedule[scheduled_index]
    if any(item is None for item in source_order):
        raise AssertionError("invalid inverse schedule permutation")
    suite = {
        "schemaVersion": 1,
        "name": f"Omega NNUE king-state v1 {gate}",
        "kingStateMatchSuite": {
            "schemaVersion": SCHEMA_VERSION,
            "gate": gate,
            "seed": spec["seed"],
            "roots": spec["roots"],
            "rootsPerPhase": spec["rootsPerPhase"],
            "rootsPerPhaseSide": spec["rootsPerPhaseSide"],
            "balancedBlockSizePairs": 4,
            "schedulePermutation": "System.Random(int) compatibility Fisher-Yates",
        },
        "openings": source_order,
    }
    return suite, schedule


def _match_config(
    gate: str,
    suite_path: Path,
    suite_sha: str,
    engine: dict[str, Any],
    network: dict[str, Any],
    harness: dict[str, Any],
    bundle: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    spec = GATE_SPECS[gate]
    candidate_options = dict(ENGINE_OPTIONS)
    candidate_options.update(
        {"OmegaNNUEFile": str(_resolve(Path(network["path"]))), "UseOmegaNNUE": "true"}
    )
    hce_options = dict(ENGINE_OPTIONS)
    hce_options["UseOmegaNNUE"] = "false"
    match: dict[str, Any] = {
        "engineA": "nnue-candidate",
        "engineB": "hce-control",
        "openingsFile": str(_resolve(suite_path)),
        "repeats": 1,
        "maxPlies": MAX_PLIES,
        "absoluteMaxPlies": ABSOLUTE_MAX_PLIES,
        "mode": spec["mode"],
        "searchTimeoutMs": spec["searchTimeoutMs"],
        "stopGraceMs": STOP_GRACE_MS,
        "bootstrapIterations": BOOTSTRAP_ITERATIONS,
        "sequentialGate": {
            "candidateEngine": "nnue-candidate",
            "minimumPairs": (
                1 if gate == "development" else MINIMUM_GATE_PAIRS
            ),
            "nullElo": NULL_ELO,
            "promotionAlpha": PROMOTION_ALPHA,
            "futilityBeta": FUTILITY_BETA,
        },
        "freshProcessPerGame": True,
    }
    if spec["mode"] == "nodes":
        match["nodes"] = spec["nodes"]
    else:
        match["moveTimeMs"] = spec["moveTimeMs"]
    return {
        "schemaVersion": 1,
        "expectedHarnessSha256": harness["sha256"],
        "expectedHarnessBundleSha256": bundle["sha256"],
        "expectedOpeningSuiteSha256": suite_sha,
        "runId": f"king-state-v1-{gate}-{str(network['sha256'])[:12]}",
        "outputDirectory": str(_resolve(output_root / gate)),
        "seed": spec["seed"],
        "kingStateMatchExecution": {
            "oneGameAtATime": True,
            "maximumConcurrentGames": 1,
            "pairBudgetMustBeMultipleOf": 4,
            "idleMachineRequired": gate == "equal-time",
        },
        "engines": [
            {
                "id": "nnue-candidate",
                "executable": engine["path"],
                "expectedSha256": engine["sha256"],
                "expectedAssetSha256": {"OmegaNNUEFile": network["sha256"]},
                "options": candidate_options,
            },
            {
                "id": "hce-control",
                "executable": engine["path"],
                "expectedSha256": engine["sha256"],
                "options": hce_options,
            },
        ],
        "match": match,
    }


def _seal(args: argparse.Namespace, *, default_exclusions: bool = True) -> Path:
    repo = _resolve(Path(__file__).parents[2])
    output_dir = _resolve(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    preexisting_json = sorted(
        (
            path
            for path in output_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
        ),
        key=lambda path: str(path).lower(),
    )
    if preexisting_json:
        raise FileExistsError(
            "match-seal output directory must not contain prior JSON artifacts: "
            f"{preexisting_json[0]}"
        )
    protocol = _validate_protocol(args.protocol)
    protocol_identity = _identity(args.protocol)
    tool_identity = _identity(Path(__file__))
    orbit_tool_identity = _identity(Path(__file__).parent / "select_screen.py")
    feature_tool_identity = _identity(Path(__file__).parent / "omega_nnue.py")
    project = _resolve(args.sampler_project)
    project_identity = _identity(project)
    program_identity = _identity(project.parent / "Program.cs")
    engine = _identity(args.engine_executable)
    network = _identity(args.candidate_network)
    candidate_manifest = _identity(args.candidate_manifest)
    candidate_manifest_value = _load_object(args.candidate_manifest)
    round_trip = _field(candidate_manifest_value, "roundTrip")
    if (
        not isinstance(round_trip, dict)
        or str(_field(round_trip, "sha256", "")).lower() != network["sha256"]
    ):
        raise ValueError(
            "candidate manifest roundTrip.sha256 does not identify the candidate"
        )
    if network["sha256"] not in {
        value.lower() for value in _all_strings(candidate_manifest_value)
    }:
        raise ValueError("candidate manifest does not identify the candidate network hash")
    training_selection_paths = [_resolve(path) for path in args.training_selection]
    training_selection_values = [
        _load_object(path) for path in training_selection_paths
    ]
    if not any(
        network["sha256"] in {value.lower() for value in _all_strings(value)}
        for value in training_selection_values
    ):
        raise ValueError("no training-selection artifact identifies the selected network hash")
    training_selection = [_identity(path) for path in training_selection_paths]
    by_kind: dict[str, list[tuple[Path, dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for path, value, identity_record in zip(
        training_selection_paths, training_selection_values, training_selection
    ):
        by_kind[str(_field(value, "kind", ""))].append(
            (path, value, identity_record)
        )
    required_selection_kinds = (
        "omega-nnue-king-state-v1-training-plan",
        "omega-nnue-king-state-v1-validation-selection",
        "omega-nnue-king-state-v1-offline-test",
    )
    for kind in required_selection_kinds:
        if len(by_kind[kind]) != 1:
            raise ValueError(
                f"--training-selection must contain exactly one {kind}, "
                f"found {len(by_kind[kind])}"
            )
    _, plan_value, plan_identity = by_kind[required_selection_kinds[0]][0]
    _, selection_value, selection_identity = by_kind[required_selection_kinds[1]][0]
    _, offline_value, _ = by_kind[required_selection_kinds[2]][0]
    if not _same_identity(_field(selection_value, "plan", {}), plan_identity):
        raise ValueError("validation selection does not pin the supplied training plan")
    if not _same_identity(
        _field(selection_value, "selectedNetwork", {}), network
    ):
        raise ValueError("validation selection selectedNetwork differs from candidate")
    if not _same_identity(
        _field(selection_value, "selectedManifest", {}), candidate_manifest
    ):
        raise ValueError(
            "validation selection selectedManifest differs from candidate manifest"
        )
    selected_id = str(_field(selection_value, "selectedCandidateId", ""))
    if selected_id not in {"K1", "K2"}:
        raise ValueError("validation selection must choose eligible K1 or K2")
    selection_decision = _field(selection_value, "decision")
    if not isinstance(selection_decision, dict):
        raise ValueError("validation selection is missing its decision")
    if str(_field(selection_decision, "winner", "")) != selected_id:
        raise ValueError("validation selection winner differs from selectedCandidateId")
    eligibility = _field(selection_decision, "eligibility")
    if (
        not isinstance(eligibility, dict)
        or not isinstance(_field(eligibility, selected_id), dict)
        or _field(_field(eligibility, selected_id), "eligible") is not True
    ):
        raise ValueError("selected validation candidate is not eligible")
    candidates = _field(selection_value, "candidates")
    selected_candidate = (
        _field(candidates, selected_id) if isinstance(candidates, dict) else None
    )
    selected_health = (
        _field(selected_candidate, "health")
        if isinstance(selected_candidate, dict)
        else None
    )
    if not isinstance(selected_health, dict) or _field(
        selected_health, "passed"
    ) is not True:
        raise ValueError("selected validation candidate failed health checks")
    if not _same_identity(
        _field(offline_value, "selection", {}), selection_identity
    ):
        raise ValueError("offline test does not pin the supplied validation selection")
    if not _same_identity(_field(offline_value, "selectedNetwork", {}), network):
        raise ValueError("offline test selectedNetwork differs from candidate")
    comparisons = _field(offline_value, "comparisons")
    if (
        _field(offline_value, "passed") is not True
        or not isinstance(comparisons, dict)
        or not isinstance(_field(comparisons, "K0"), dict)
        or _field(_field(comparisons, "K0"), "passed") is not True
        or not isinstance(_field(comparisons, "zeroResidual"), dict)
        or _field(_field(comparisons, "zeroResidual"), "passed") is not True
        or _field(
            offline_value, "robustnessDirectionalImprovementOverK0"
        )
        is not True
        or _field(
            offline_value, "robustnessDirectionalImprovementOverZeroResidual"
        )
        is not True
    ):
        raise ValueError("offline test did not pass every preregistered comparison")
    training_corpora = [_resolve(path) for path in args.training_corpus]
    training_corpus_identities = [_identity(path) for path in training_corpora]
    corpus_hashes = {item["sha256"] for item in training_corpus_identities}
    if len(corpus_hashes) != len(training_corpus_identities):
        raise ValueError("duplicate training corpus content was supplied")
    direct_inputs = _top_level_input_hashes(
        candidate_manifest_value, "candidate manifest"
    )
    if not direct_inputs:
        raise ValueError("candidate manifest does not identify any training input")
    if direct_inputs != corpus_hashes:
        raise ValueError(
            "candidate manifest inputs and --training-corpus must be the same "
            f"closed set; manifest={sorted(direct_inputs)}, supplied={sorted(corpus_hashes)}"
        )
    referenced_corpora = set(direct_inputs)
    for path, value in zip(training_selection_paths, training_selection_values):
        referenced_corpora.update(
            _top_level_input_hashes(value, f"training-selection {path}")
        )
    unreferenced = corpus_hashes.difference(referenced_corpora)
    if unreferenced:
        raise ValueError(
            "supplied training corpora are absent from every frozen trainer/"
            "selection manifest: " + ", ".join(sorted(unreferenced))
        )
    plan_identity_hashes = {
        item["sha256"]
        for item in _nested_file_identities(_field(plan_value, "identities", {}))
    }
    if not corpus_hashes.issubset(plan_identity_hashes):
        raise ValueError("training plan identities omit a supplied training corpus")
    referenced_training_identities: list[dict[str, Any]] = []
    for label, value in [
        ("candidate manifest", candidate_manifest_value),
        *(
            (f"training-selection {path}", selection_value_item)
            for path, selection_value_item in zip(
                training_selection_paths, training_selection_values
            )
        ),
    ]:
        for identity_record in _nested_file_identities(value):
            _verify_identity(identity_record, f"{label} referenced artifact")
            referenced_training_identities.append(identity_record)

    harness = _identity(args.omega_match_assembly)
    if Path(str(harness["path"])).name.lower() != "omegamatch.dll":
        raise ValueError("--omega-match-assembly must identify OmegaMatch.dll")
    bundle = _harness_bundle_identity(Path(str(harness["path"])))

    sources: dict[str, list[Root]] = {}
    source_audits: dict[str, Any] = {}
    sampler_dir = _resolve(args.sampler_dir)
    if _is_within(sampler_dir, output_dir) or _is_within(output_dir, sampler_dir):
        raise ValueError("sampler and match-seal output directories must be disjoint")
    for gate in GATE_SPECS:
        sources[gate], source_audits[gate] = _verify_sampler_source(
            sampler_dir / SOURCE_NAMES[gate], gate
        )

    exclusion_roots = list(args.exclude)
    if default_exclusions:
        exclusion_roots.extend(_default_exclusion_roots(repo))
    exclusion_files = _expand_json_files(exclusion_roots, output_dir)
    forbidden, exclusion_identities, exclusion_stats = _read_forbidden(
        training_corpora, exclusion_files
    )

    used_fresh: set[str] = set()
    selected: dict[str, list[Root]] = {}
    rejected: dict[str, dict[str, int]] = {}
    for gate in GATE_SPECS:
        selected[gate], rejected[gate] = _select_roots(
            sources[gate], GATE_SPECS[gate], forbidden, used_fresh
        )

    output_root = _resolve(args.match_output_root or (output_dir / "runs"))
    for gate in GATE_SPECS:
        run_directory = output_root / gate
        if run_directory.exists():
            raise FileExistsError(
                f"sealed match run directory must be fresh: {run_directory}"
            )
    suite_paths = {
        gate: output_dir / f"king-state-v1-{gate}-roots.json"
        for gate in GATE_SPECS
    }
    config_paths = {
        gate: output_dir / f"king-state-v1-{gate}-match.json"
        for gate in GATE_SPECS
    }
    audit_path = output_dir / "king-state-v1-matches.audit.json"
    seal_path = output_dir / "king-state-v1-matches.seal.json"
    all_outputs = [*suite_paths.values(), *config_paths.values(), audit_path, seal_path]
    if len({_resolve(path) for path in all_outputs}) != len(all_outputs):
        raise ValueError("suite/config/audit/seal outputs must be distinct")
    existing = [path for path in all_outputs if path.exists()]
    if existing:
        raise FileExistsError(f"refusing to replace sealed artifact: {existing[0]}")

    suites: dict[str, dict[str, Any]] = {}
    schedules: dict[str, list[dict[str, Any]]] = {}
    for gate in GATE_SPECS:
        suites[gate], schedules[gate] = _suite(gate, selected[gate])
        _exclusive_json(suite_paths[gate], suites[gate])

    configs: dict[str, dict[str, Any]] = {}
    for gate in GATE_SPECS:
        configs[gate] = _match_config(
            gate,
            suite_paths[gate],
            _sha256(suite_paths[gate]),
            engine,
            network,
            harness,
            bundle,
            output_root,
        )
        _exclusive_json(config_paths[gate], configs[gate])

    inputs = [
        protocol_identity,
        tool_identity,
        orbit_tool_identity,
        feature_tool_identity,
        project_identity,
        program_identity,
        engine,
        network,
        candidate_manifest,
        *training_selection,
        *referenced_training_identities,
        *training_corpus_identities,
        *exclusion_identities,
    ]
    for gate in GATE_SPECS:
        inputs.extend(
            [
                source_audits[gate]["source"],
                source_audits[gate]["manifest"],
                *source_audits[gate]["runtime"],
            ]
        )
    # Deduplicate repeated validation/protocol identities without weakening
    # the frozen inventory.
    pinned_by_path = {str(item["path"]).lower(): item for item in inputs}

    audit = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v1-match-audit",
        "createdUtc": _utc_now(),
        "candidateBlindConstruction": True,
        "protocol": protocol_identity,
        "samplerPolicy": {
            "implementation": "OmegaRootSampler",
            "deterministicPrng": "SplitMix64",
            "trajectoryPairs": SAMPLER_TRAJECTORY_PAIRS,
            "independentTrajectoriesPerPair": 2,
            "maxPlies": SAMPLER_MAX_PLIES,
            "positionsPerPhaseAndSide": SAMPLER_POSITIONS_PER_PHASE_SIDE,
            "captureSelectionPercent": SAMPLER_CAPTURE_PERCENT,
            "seeds": {
                gate: GATE_SPECS[gate]["seed"] for gate in GATE_SPECS
            },
        },
        "inputs": {
            "engine": engine,
            "candidateNetwork": network,
            "candidateManifest": candidate_manifest,
            "trainingSelection": training_selection,
            "trainingCorpora": training_corpus_identities,
            "omegaMatchAssembly": harness,
            "omegaMatchBundle": bundle,
            "samplerProject": project_identity,
            "samplerProgram": program_identity,
            "samplerSources": source_audits,
        },
        "exclusions": {
            "files": exclusion_identities,
            "uniqueForbiddenSignatures": len(forbidden),
            "scan": exclusion_stats,
        },
        "selection": {
            gate: {
                "seed": GATE_SPECS[gate]["seed"],
                "roots": len(selected[gate]),
                "phaseCounts": dict(Counter(root.phase for root in selected[gate])),
                "sideToMoveCounts": dict(Counter(root.side for root in selected[gate])),
                "uniqueTrajectoryPairs": len(
                    {root.source_group for root in selected[gate]}
                ),
                "uniqueOrbits": len({root.orbit for root in selected[gate]}),
                "rejections": rejected[gate],
                "scheduledOpeningIds": [item["id"] for item in schedules[gate]],
                "suite": _identity(suite_paths[gate]),
                "config": _identity(config_paths[gate]),
            }
            for gate in GATE_SPECS
        },
        "crossSuite": {
            "roots": sum(len(values) for values in selected.values()),
            "uniqueOrbitSignatures": len(used_fresh),
            "mutuallyOrbitDisjoint": True,
        },
        "frozenMatchDegrees": {
            "maxPlies": MAX_PLIES,
            "absoluteMaxPlies": ABSOLUTE_MAX_PLIES,
            "stopGraceMs": STOP_GRACE_MS,
            "threads": 1,
            "hashMiB": HASH_MIB,
            "ownBook": False,
            "ponder": False,
            "developmentNodes": 20_000,
            "equalNodeNodes": 50_000,
            "equalTimeMoveTimeMs": 1_000,
            "equalTimeOneGameAtATime": True,
            "minimumPairs": MINIMUM_GATE_PAIRS,
            "maximumPairs": MAXIMUM_GATE_PAIRS,
            "nullElo": NULL_ELO,
            "promotionEValue": PROMOTION_E_VALUE,
            "futilityEValue": FUTILITY_E_VALUE,
            "balancedBlockSizePairs": 4,
        },
    }
    _exclusive_json(audit_path, audit)

    artifacts = [
        *(_identity(path) for path in suite_paths.values()),
        *(_identity(path) for path in config_paths.values()),
        _identity(audit_path),
    ]
    pinned_files = sorted(
        [*pinned_by_path.values(), *artifacts],
        key=lambda item: str(item["path"]).lower(),
    )
    seal = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v1-match-seal",
        "sealedUtc": _utc_now(),
        "generationId": protocol["generationId"],
        "protocolSha256": protocol_identity["sha256"],
        "candidateNetworkSha256": network["sha256"],
        "engineExecutableSha256": engine["sha256"],
        "omegaMatchAssemblySha256": harness["sha256"],
        "pinnedFiles": pinned_files,
        "omegaMatchBundle": bundle,
        "gates": {
            gate: {
                "suite": _identity(suite_paths[gate]),
                "config": _identity(config_paths[gate]),
                "runId": configs[gate]["runId"],
                "outputDirectory": configs[gate]["outputDirectory"],
            }
            for gate in GATE_SPECS
        },
        "audit": _identity(audit_path),
    }
    _exclusive_json(seal_path, seal)
    _verify_seal(seal_path)
    print(f"Published match seal: {seal_path}")
    print(f"Seal SHA-256: {_sha256(seal_path)}")
    for gate in GATE_SPECS:
        print(f"{gate}: {config_paths[gate]}")
    return seal_path


def _verify_suite(gate: str, suite_path: Path) -> dict[str, dict[str, Any]]:
    suite = _load_object(suite_path)
    metadata = suite.get("kingStateMatchSuite")
    openings = suite.get("openings")
    spec = GATE_SPECS[gate]
    if not isinstance(metadata, dict) or not isinstance(openings, list):
        raise ValueError(f"{suite_path}: malformed king-state suite")
    if (
        metadata.get("gate") != gate
        or metadata.get("seed") != spec["seed"]
        or metadata.get("roots") != spec["roots"]
        or len(openings) != spec["roots"]
    ):
        raise ValueError(f"{suite_path}: suite contract mismatch")
    ids: set[str] = set()
    orbits: set[str] = set()
    schedule: dict[int, dict[str, Any]] = {}
    for opening in openings:
        if not isinstance(opening, dict):
            raise ValueError(f"{suite_path}: malformed opening")
        opening_id = str(opening.get("id", ""))
        meta = opening.get("kingStateMatch")
        if not opening_id or opening_id in ids or not isinstance(meta, dict):
            raise ValueError(f"{suite_path}: duplicate/malformed opening")
        ids.add(opening_id)
        phase, side, identity, orbit, signatures = _position_meta(
            str(opening.get("initialOfen", ""))
        )
        if (
            meta.get("gate") != gate
            or meta.get("phase") != phase
            or meta.get("rootSideToMove") != side
            or meta.get("identityKey") != identity
            or meta.get("symmetryOrbitKey") != orbit
            or meta.get("orbitSignatures") != list(signatures)
        ):
            raise ValueError(f"{suite_path}: opening metadata mismatch for {opening_id}")
        if orbit in orbits:
            raise ValueError(f"{suite_path}: duplicate root orbit")
        orbits.add(orbit)
        block = int(meta.get("balancedBlock", 0))
        schedule.setdefault(block, {})[phase] = opening
    expected_blocks = int(spec["rootsPerPhase"])
    if set(schedule) != set(range(1, expected_blocks + 1)):
        raise ValueError(f"{suite_path}: balanced block IDs are incomplete")
    for block, phases in schedule.items():
        if set(phases) != set(PHASES):
            raise ValueError(f"{suite_path}: block {block} is not phase balanced")
        sides = {
            phases[phase]["kingStateMatch"]["rootSideToMove"] for phase in PHASES
        }
        if sides != {SIDES[(block - 1) % 2]}:
            raise ValueError(f"{suite_path}: block {block} side balance changed")
    shuffled = [
        openings[index]
        for index in _shuffled_indices(len(openings), int(spec["seed"]))
    ]
    for block_index in range(expected_blocks):
        block_items = shuffled[
            block_index * len(PHASES) : (block_index + 1) * len(PHASES)
        ]
        metadata_items = [item["kingStateMatch"] for item in block_items]
        if [item["phase"] for item in metadata_items] != list(PHASES):
            raise ValueError(
                f"{suite_path}: seeded schedule block {block_index + 1} "
                "is not in the frozen phase order"
            )
        if any(
            int(item["balancedBlock"]) != block_index + 1
            or item["rootSideToMove"] != SIDES[block_index % 2]
            for item in metadata_items
        ):
            raise ValueError(
                f"{suite_path}: seeded schedule block {block_index + 1} "
                "metadata differs from the frozen balance"
            )
    return {opening_id: opening for opening_id, opening in ((item["id"], item) for item in openings)}


def _verify_config(
    gate: str,
    config_path: Path,
    suite_identity: dict[str, Any],
    candidate_sha: str,
    engine_sha: str,
    harness_sha: str,
    harness_bundle_sha: str,
) -> dict[str, Any]:
    config = _load_object(config_path)
    spec = GATE_SPECS[gate]
    match = config.get("match")
    engines = config.get("engines")
    if not isinstance(match, dict) or not isinstance(engines, list) or len(engines) != 2:
        raise ValueError(f"{config_path}: malformed match config")
    expected_match = {
        "engineA": "nnue-candidate",
        "engineB": "hce-control",
        "repeats": 1,
        "maxPlies": MAX_PLIES,
        "absoluteMaxPlies": ABSOLUTE_MAX_PLIES,
        "mode": spec["mode"],
        "searchTimeoutMs": spec["searchTimeoutMs"],
        "stopGraceMs": STOP_GRACE_MS,
        "freshProcessPerGame": True,
    }
    for key, wanted in expected_match.items():
        if match.get(key) != wanted:
            raise ValueError(f"{config_path}: match.{key} changed")
    if spec["mode"] == "nodes" and match.get("nodes") != spec["nodes"]:
        raise ValueError(f"{config_path}: fixed-node budget changed")
    if spec["mode"] == "moveTime" and match.get("moveTimeMs") != spec["moveTimeMs"]:
        raise ValueError(f"{config_path}: fixed-time budget changed")
    if config.get("expectedOpeningSuiteSha256") != suite_identity["sha256"]:
        raise ValueError(f"{config_path}: suite pin mismatch")
    if config.get("expectedHarnessSha256") != harness_sha:
        raise ValueError(f"{config_path}: OmegaMatch assembly pin mismatch")
    if config.get("expectedHarnessBundleSha256") != harness_bundle_sha:
        raise ValueError(f"{config_path}: OmegaMatch bundle pin mismatch")
    if _resolve(Path(str(match["openingsFile"]))) != _resolve(
        Path(str(suite_identity["path"]))
    ):
        raise ValueError(f"{config_path}: suite path mismatch")
    candidate, hce = engines
    if candidate.get("id") != "nnue-candidate" or hce.get("id") != "hce-control":
        raise ValueError(f"{config_path}: engine roles changed")
    if candidate.get("executable") != hce.get("executable"):
        raise ValueError(f"{config_path}: evaluators do not use the same executable")
    if candidate.get("expectedSha256") != hce.get("expectedSha256"):
        raise ValueError(f"{config_path}: engine binary pins differ")
    if candidate.get("expectedSha256") != engine_sha:
        raise ValueError(f"{config_path}: engine binary pin mismatch")
    candidate_options = candidate.get("options")
    hce_options = hce.get("options")
    if not isinstance(candidate_options, dict) or not isinstance(hce_options, dict):
        raise ValueError(f"{config_path}: malformed engine options")
    for key, wanted in ENGINE_OPTIONS.items():
        if candidate_options.get(key) != wanted or hce_options.get(key) != wanted:
            raise ValueError(f"{config_path}: engine option {key} changed")
    if candidate_options.get("UseOmegaNNUE") != "true":
        raise ValueError(f"{config_path}: candidate NNUE is not active")
    if hce_options.get("UseOmegaNNUE") != "false":
        raise ValueError(f"{config_path}: HCE control is not isolated")
    if candidate.get("expectedAssetSha256", {}).get("OmegaNNUEFile") != candidate_sha:
        raise ValueError(f"{config_path}: candidate network pin mismatch")
    gate_spec = match.get("sequentialGate")
    if not isinstance(gate_spec, dict):
        raise ValueError(f"{config_path}: missing sequential gate")
    expected_gate = {
        "candidateEngine": "nnue-candidate",
        "minimumPairs": 1 if gate == "development" else MINIMUM_GATE_PAIRS,
        "nullElo": NULL_ELO,
        "promotionAlpha": PROMOTION_ALPHA,
        "futilityBeta": FUTILITY_BETA,
    }
    if gate_spec != expected_gate:
        raise ValueError(f"{config_path}: sequential gate changed")
    execution = config.get("kingStateMatchExecution")
    if not isinstance(execution, dict) or execution.get("oneGameAtATime") is not True:
        raise ValueError(f"{config_path}: serialized execution contract missing")
    return config


def _verify_seal(path: Path) -> dict[str, Any]:
    path = _resolve(path)
    seal = _load_object(path)
    if seal.get("kind") != "omega-nnue-king-state-v1-match-seal":
        raise ValueError("wrong king-state match seal kind")
    pinned = seal.get("pinnedFiles")
    if not isinstance(pinned, list) or not pinned:
        raise ValueError("match seal has no pinned files")
    pinned_paths: set[str] = set()
    for index, identity in enumerate(pinned):
        if not isinstance(identity, dict):
            raise ValueError("malformed pinned file identity")
        verified = _verify_identity(identity, f"pinned file {index + 1}")
        key = str(verified).lower()
        if key in pinned_paths:
            raise ValueError(f"duplicate pinned file in match seal: {verified}")
        pinned_paths.add(key)
    _verify_harness_bundle(seal["omegaMatchBundle"])
    gates = seal.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(GATE_SPECS):
        raise ValueError("match seal gate inventory changed")
    fresh_signatures: set[str] = set()
    for gate in GATE_SPECS:
        entry = gates[gate]
        suite_path = _verify_identity(entry["suite"], f"{gate} suite")
        config_path = _verify_identity(entry["config"], f"{gate} config")
        openings = _verify_suite(gate, suite_path)
        _verify_config(
            gate,
            config_path,
            entry["suite"],
            str(seal["candidateNetworkSha256"]),
            str(seal["engineExecutableSha256"]),
            str(seal["omegaMatchAssemblySha256"]),
            str(seal["omegaMatchBundle"]["sha256"]),
        )
        for opening in openings.values():
            signatures = set(opening["kingStateMatch"]["orbitSignatures"])
            if fresh_signatures.intersection(signatures):
                raise ValueError("fresh match suites are not mutually orbit disjoint")
            fresh_signatures.update(signatures)
    _verify_identity(seal["audit"], "match audit")
    return seal


def _parse_utc(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_records(path: Path) -> list[dict[str, Any]]:
    return [record for _, record in _jsonl(path)]


def _write_event_records(path: Path, records: Sequence[dict[str, Any]]) -> None:
    _exclusive_bytes(
        path,
        b"".join(
            (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
            for record in records
        ),
    )


def _score_for_candidate(result: dict[str, Any]) -> float:
    text = str(_field(result, "Result", ""))
    white = str(_field(result, "WhiteEngineId", ""))
    black = str(_field(result, "BlackEngineId", ""))
    if {white.lower(), black.lower()} != {"nnue-candidate", "hce-control"}:
        raise ValueError("game result contains unexpected engine identities")
    if text == "1/2-1/2":
        return 0.5
    if text == "1-0":
        return 1.0 if white.lower() == "nnue-candidate" else 0.0
    if text == "0-1":
        return 1.0 if black.lower() == "nnue-candidate" else 0.0
    raise ValueError(f"invalid game result {text!r}")


def _log_mean_exp(values: Sequence[float]) -> float:
    maximum = max(values)
    return maximum + math.log(sum(math.exp(value - maximum) for value in values) / len(values))


def _sequential_gate(pair_observations: Sequence[tuple[str, float]]) -> dict[str, Any]:
    null_score = 1.0 / (1.0 + 10.0 ** (-NULL_ELO / 400.0))
    promotion_logs = [0.0] * len(BET_FRACTIONS)
    futility_logs = [0.0] * len(BET_FRACTIONS)
    max_promotion_log = 0.0
    max_futility_log = 0.0
    cumulative = 0.0
    signal: str | None = None
    signal_pair: int | None = None
    checkpoints: list[dict[str, Any]] = []
    for index, (pair_id, score) in enumerate(pair_observations, 1):
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"{pair_id}: invalid candidate pair score")
        cumulative += score
        for bet, fraction in enumerate(BET_FRACTIONS):
            promotion_logs[bet] += math.log(
                1.0 + (fraction / null_score) * (score - null_score)
            )
            futility_logs[bet] += math.log(
                1.0
                + (fraction / (1.0 - null_score)) * (null_score - score)
            )
        promotion_log = _log_mean_exp(promotion_logs)
        futility_log = _log_mean_exp(futility_logs)
        max_promotion_log = max(max_promotion_log, promotion_log)
        max_futility_log = max(max_futility_log, futility_log)
        # Decisions are inspected only at the end of a four-phase block.
        if index % len(PHASES) == 0:
            promotion_e = math.exp(min(promotion_log, math.log(sys.float_info.max)))
            futility_e = math.exp(min(futility_log, math.log(sys.float_info.max)))
            # The preregistration says "minimumPairsBeforeDecision", not
            # merely "minimumPairsBeforePromotionReady".  Therefore an
            # e-value crossing before pair 64 is deliberately forgotten.  A
            # decision may first latch only at a complete four-phase
            # checkpoint at or beyond the minimum.
            if signal is None and index >= MINIMUM_GATE_PAIRS:
                promote = promotion_e >= PROMOTION_E_VALUE
                futile = futility_e >= FUTILITY_E_VALUE
                if promote or futile:
                    if promote and futile:
                        signal = (
                            "promote"
                            if promotion_e / PROMOTION_E_VALUE
                            >= futility_e / FUTILITY_E_VALUE
                            else "futility"
                        )
                    else:
                        signal = "promote" if promote else "futility"
                    signal_pair = index
            checkpoints.append(
                {
                    "pairs": index,
                    "balancedBlocks": index // len(PHASES),
                    "candidateScore": cumulative / index,
                    "promotionEValue": promotion_e,
                    "maxPromotionEValue": math.exp(
                        min(max_promotion_log, math.log(sys.float_info.max))
                    ),
                    "promotionAnytimePValue": min(
                        1.0, math.exp(-max(0.0, max_promotion_log))
                    ),
                    "futilityEValue": futility_e,
                    "maxFutilityEValue": math.exp(
                        min(max_futility_log, math.log(sys.float_info.max))
                    ),
                    "futilityAnytimePValue": min(
                        1.0, math.exp(-max(0.0, max_futility_log))
                    ),
                    "signal": signal or "continue",
                    "signalPair": signal_pair,
                }
            )
    pair_count = len(pair_observations)
    eligible_signal = signal if pair_count >= MINIMUM_GATE_PAIRS else None
    if eligible_signal is not None:
        decision = eligible_signal
    elif pair_count >= MAXIMUM_GATE_PAIRS:
        decision = "inconclusive"
    else:
        decision = "continue"
    return {
        "pairCount": pair_count,
        "minimumPairs": MINIMUM_GATE_PAIRS,
        "maximumPairs": MAXIMUM_GATE_PAIRS,
        "nullElo": NULL_ELO,
        "nullScore": null_score,
        "promotionThreshold": PROMOTION_E_VALUE,
        "futilityThreshold": FUTILITY_E_VALUE,
        "candidateScore": (
            sum(score for _, score in pair_observations) / pair_count
            if pair_count
            else None
        ),
        "decision": decision,
        "signal": signal or "continue",
        "signalPair": signal_pair,
        "checkpoints": checkpoints,
    }


def _validate_idle_attestation(path: Path, run_id: str) -> dict[str, Any]:
    value = _load_object(path)
    if value.get("kind") != "omega-equal-time-idle-attestation-v1":
        raise ValueError("wrong equal-time idle-attestation kind")
    required = {
        "runId": run_id,
        "idleMachine": True,
        "oneGameAtATime": True,
        "concurrentMatchProcesses": 1,
    }
    for key, wanted in required.items():
        if value.get(key) != wanted:
            raise ValueError(f"idle attestation {key} must be {wanted!r}")
    if "createdUtc" not in value:
        raise ValueError("idle attestation createdUtc is required")
    _parse_utc(str(value["createdUtc"]))
    if "operator" in value and not str(value["operator"]).strip():
        raise ValueError("idle attestation operator must be nonempty when present")
    return {"identity": _identity(path), "attestation": value}


def _attest(args: argparse.Namespace) -> None:
    seal = _verify_seal(args.seal)
    run_id = str(seal["gates"]["equal-time"]["runId"])
    value: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-equal-time-idle-attestation-v1",
        "runId": run_id,
        "createdUtc": _utc_now(),
        "idleMachine": True,
        "oneGameAtATime": True,
        "concurrentMatchProcesses": 1,
    }
    if args.operator is not None:
        operator = str(args.operator).strip()
        if not operator:
            raise ValueError("--operator must be nonempty")
        value["operator"] = operator
    _exclusive_json(args.output, value)
    _validate_idle_attestation(args.output, run_id)
    print(f"Idle-machine attestation: {_resolve(args.output)}")


def _assess(args: argparse.Namespace) -> dict[str, Any]:
    seal = _verify_seal(args.seal)
    gate = args.gate
    gate_entry = seal["gates"][gate]
    suite_path = Path(str(gate_entry["suite"]["path"]))
    config_path = Path(str(gate_entry["config"]["path"]))
    config = _load_object(config_path)
    openings = _verify_suite(gate, suite_path)
    events_path = _resolve(args.events)
    events = _event_records(events_path)
    run_records = [
        record for record in events if str(_field(record, "RecordType", "")) == "run"
    ]
    if len(run_records) != 1:
        raise ValueError("events must contain exactly one run record")
    run = run_records[0]
    expected_run = {
        "RunId": config["runId"],
        "ConfigSha256": _sha256(config_path),
        "OpeningSuiteSha256": _sha256(suite_path),
        "HarnessSha256": config["expectedHarnessSha256"],
        "HarnessBundleSha256": config["expectedHarnessBundleSha256"],
        "Seed": config["seed"],
    }
    for key, wanted in expected_run.items():
        if _field(run, key) != wanted:
            raise ValueError(f"run record {key} does not match the sealed config")
    _parse_utc(str(_field(run, "CreatedUtc")))
    run_match = _field(run, "Match")
    if not isinstance(run_match, dict):
        raise ValueError("run record is missing effective match settings")
    sealed_match = config["match"]
    for key in (
        "EngineA",
        "EngineB",
        "Repeats",
        "MaxPlies",
        "AbsoluteMaxPlies",
        "SearchTimeoutMs",
        "StopGraceMs",
        "FreshProcessPerGame",
    ):
        if _field(run_match, key) != _field(sealed_match, key):
            raise ValueError(f"run record match.{key} differs from sealed config")
    if str(_field(run_match, "Mode", "")).lower() != str(
        sealed_match["mode"]
    ).lower():
        raise ValueError("run record search mode differs from sealed config")
    budget_key = "Nodes" if gate != "equal-time" else "MoveTimeMs"
    if _field(run_match, budget_key) != _field(sealed_match, budget_key):
        raise ValueError(f"run record match.{budget_key} differs from sealed config")

    run_engines = _field(run, "Engines")
    if not isinstance(run_engines, list) or len(run_engines) != 2:
        raise ValueError("run record must contain exactly two engine identities")
    engines_by_id = {
        str(_field(item, "Id", "")).lower(): item
        for item in run_engines
        if isinstance(item, dict)
    }
    if set(engines_by_id) != {"nnue-candidate", "hce-control"}:
        raise ValueError("run record engine identities changed")
    for engine_id, sealed_engine in zip(
        ("nnue-candidate", "hce-control"), config["engines"]
    ):
        runtime_engine = engines_by_id[engine_id]
        if str(_field(runtime_engine, "Sha256", "")).lower() != str(
            sealed_engine["expectedSha256"]
        ).lower():
            raise ValueError(f"run record {engine_id} binary hash mismatch")
        runtime_options = _field(runtime_engine, "Options")
        if not isinstance(runtime_options, dict):
            raise ValueError(f"run record {engine_id} options are missing")
        for option, wanted in sealed_engine["options"].items():
            actual = _field(runtime_options, option)
            if option == "OmegaNNUEFile":
                if _resolve(Path(str(actual))) != _resolve(Path(str(wanted))):
                    raise ValueError(
                        f"run record {engine_id} option {option} changed"
                    )
            elif str(actual).lower() != str(wanted).lower():
                raise ValueError(f"run record {engine_id} option {option} changed")
    candidate_runtime = engines_by_id["nnue-candidate"]
    if _field(candidate_runtime, "OmegaNnueActiveVerified") is not True:
        raise ValueError("run record did not verify active Omega NNUE evaluation")
    assets = _field(candidate_runtime, "ExternalAssets")
    if not isinstance(assets, list):
        raise ValueError("run record is missing the candidate network identity")
    network_assets = [
        item
        for item in assets
        if isinstance(item, dict)
        and str(_field(item, "OptionName", "")).lower() == "omegannuefile"
    ]
    if len(network_assets) != 1 or str(
        _field(network_assets[0], "Sha256", "")
    ).lower() != str(seal["candidateNetworkSha256"]).lower():
        raise ValueError("run record candidate network hash mismatch")

    results_all = [
        record
        for record in events
        if str(_field(record, "RecordType", "")) == "gameResult"
    ]
    result_game_ids = [str(_field(record, "GameId", "")) for record in results_all]
    if any(not game_id for game_id in result_game_ids):
        raise ValueError("gameResult has an empty GameId")
    duplicate_result_games = [
        game_id
        for game_id, count in Counter(result_game_ids).items()
        if count != 1
    ]
    if duplicate_result_games:
        raise ValueError(
            "events contain duplicate completed GameIds: "
            + ", ".join(sorted(duplicate_result_games))
        )
    results = results_all
    result_safety: dict[tuple[str, int], dict[str, int]] = {}
    for result in results:
        game_id = str(_field(result, "GameId", ""))
        attempt = _nonnegative_int(
            _field(result, "Attempt"), f"{game_id}.Attempt"
        )
        key = (game_id, attempt)
        result_safety[key] = {
            field: _nonnegative_int(
                _field(result, field), f"{game_id}.{field}"
            )
            for field in (
                "IllegalMoves",
                "IllegalPvs",
                "ProtocolFailures",
                "TimeForfeits",
            )
        }

    all_ply_records = [
        record
        for record in events
        if str(_field(record, "RecordType", "")) == "ply"
    ]
    plies_by_attempt: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for record in all_ply_records:
        game_id = str(_field(record, "GameId", ""))
        attempt = _nonnegative_int(
            _field(record, "Attempt"), f"{game_id}.ply.Attempt"
        )
        plies_by_attempt[(game_id, attempt)].append(record)
    for result in results:
        game_id = str(_field(result, "GameId", ""))
        attempt = _nonnegative_int(
            _field(result, "Attempt"), f"{game_id}.Attempt"
        )
        key = (game_id, attempt)
        expected_plies = _nonnegative_int(
            _field(result, "Plies"), f"{game_id}.Plies"
        )
        records = plies_by_attempt.get(key, [])
        actual_plies = sorted(
            _nonnegative_int(_field(record, "Ply"), f"{game_id}.ply.Ply")
            for record in records
        )
        normal = list(range(1, expected_plies + 1))
        decisive_safety = sum(
            result_safety[key][field]
            for field in (
                "IllegalMoves",
                "ProtocolFailures",
                "TimeForfeits",
            )
        )
        failed_terminal = list(range(1, expected_plies + 2))
        if actual_plies == failed_terminal and decisive_safety > 0:
            extra = next(
                record
                for record in records
                if _nonnegative_int(
                    _field(record, "Ply"), f"{game_id}.ply.Ply"
                )
                == expected_plies + 1
            )
            if (
                not str(_field(extra, "Error", "")).strip()
                or _field(extra, "PostOfen") not in (None, "")
            ):
                raise ValueError(
                    f"{game_id}: terminal failed ply must have Error and no PostOfen"
                )
        elif actual_plies != normal:
            raise ValueError(
                f"{game_id}: ply events do not match gameResult.Plies "
                f"({actual_plies} vs {expected_plies})"
            )
    starts_all = [
        record
        for record in events
        if str(_field(record, "RecordType", "")) == "gameStart"
    ]
    start_key_list = [
        (
            str(_field(record, "GameId", "")),
            _nonnegative_int(
                _field(record, "Attempt"),
                f"{_field(record, 'GameId', '')}.gameStart.Attempt",
            ),
        )
        for record in starts_all
    ]
    duplicate_start_keys = [
        key for key, count in Counter(start_key_list).items() if count != 1
    ]
    if duplicate_start_keys:
        raise ValueError(
            "events contain duplicate gameStart attempts: "
            + ", ".join(f"{game}/{attempt}" for game, attempt in duplicate_start_keys)
        )
    start_keys = set(start_key_list)
    result_keys = set(result_safety)
    if result_keys.difference(start_keys):
        raise ValueError("events contain a gameResult without its gameStart")
    unknown_ply_attempts = set(plies_by_attempt).difference(start_keys)
    if unknown_ply_attempts:
        raise ValueError("events contain ply records without their gameStart")
    maximum_start_attempt: dict[str, int] = defaultdict(int)
    for game_id, attempt in start_keys:
        maximum_start_attempt[game_id] = max(maximum_start_attempt[game_id], attempt)
    dangling = start_keys.difference(result_keys)
    abandoned = [
        key
        for key in dangling
        if key[1] < maximum_start_attempt[key[0]]
        or any(result[0] == key[0] and result[1] > key[1] for result in result_keys)
    ]
    in_progress = [key for key in dangling if key not in abandoned]
    for game_id, attempt in dangling:
        ply_numbers = sorted(
            _nonnegative_int(
                _field(record, "Ply"), f"{game_id}.ply.Ply"
            )
            for record in plies_by_attempt.get((game_id, attempt), [])
        )
        if ply_numbers != list(range(1, len(ply_numbers) + 1)):
            raise ValueError(
                f"{game_id}/{attempt}: unfinished ply sequence is malformed"
            )
    safety = {
        "illegalMoves": sum(
            item["IllegalMoves"] for item in result_safety.values()
        ),
        "illegalPvs": sum(item["IllegalPvs"] for item in result_safety.values()),
        "protocolFailures": sum(
            item["ProtocolFailures"] for item in result_safety.values()
        ),
        "timeForfeits": sum(
            item["TimeForfeits"] for item in result_safety.values()
        ),
        "abandonedAttempts": len(abandoned),
    }

    by_opening: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        opening_id = str(_field(result, "OpeningId", ""))
        if opening_id not in openings:
            raise ValueError(f"events contain an unsealed opening: {opening_id}")
        by_opening[opening_id].append(result)
    pair_scores: dict[str, float] = {}
    incomplete: list[str] = []
    for opening_id in openings:
        games = by_opening.get(opening_id, [])
        roles = {
            (
                str(_field(game, "WhiteEngineId", "")).lower(),
                str(_field(game, "BlackEngineId", "")).lower(),
            )
            for game in games
        }
        if (
            len(games) != 2
            or roles
            != {
                ("nnue-candidate", "hce-control"),
                ("hce-control", "nnue-candidate"),
            }
            or len({str(_field(game, "GameId", "")) for game in games}) != 2
            or len({str(_field(game, "PairId", "")) for game in games}) != 1
        ):
            incomplete.append(opening_id)
            continue
        expected_pair = f"{opening_id}-r001"
        if str(_field(games[0], "PairId", "")).lower() != expected_pair.lower():
            raise ValueError(f"{opening_id}: pair identity differs from OmegaMatch schedule")
        expected_game_ids = {f"{expected_pair}-ab", f"{expected_pair}-ba"}
        actual_game_ids = {str(_field(game, "GameId", "")) for game in games}
        if {item.lower() for item in actual_game_ids} != {
            item.lower() for item in expected_game_ids
        }:
            raise ValueError(f"{opening_id}: game identities differ from sealed schedule")
        for game in games:
            candidate_score = _score_for_candidate(game)
            if abs(float(_field(game, "ScoreA", math.nan)) - candidate_score) > 1e-12:
                raise ValueError(f"{opening_id}: recorded ScoreA contradicts game result")
        pair_scores[opening_id] = sum(_score_for_candidate(game) for game in games) / 2

    blocks: dict[int, dict[str, str]] = defaultdict(dict)
    for opening_id, opening in openings.items():
        metadata = opening["kingStateMatch"]
        blocks[int(metadata["balancedBlock"])][str(metadata["phase"])] = opening_id
    ordered_pairs: list[tuple[str, float]] = []
    complete_blocks = 0
    for block in range(1, max(blocks) + 1):
        block_ids = blocks[block]
        if any(block_ids[phase] not in pair_scores for phase in PHASES):
            break
        for phase in PHASES:
            opening_id = block_ids[phase]
            ordered_pairs.append((opening_id, pair_scores[opening_id]))
        complete_blocks += 1

    safety_failures = sum(safety.values())
    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v1-match-assessment",
        "createdUtc": _utc_now(),
        "gate": gate,
        "seal": _identity(args.seal),
        "events": _identity(events_path),
        "runId": config["runId"],
        "progress": {
            "finishedGames": len(results),
            "completePairs": len(pair_scores),
            "incompleteOpenings": incomplete,
            "inProgressAttempts": [
                {"gameId": game_id, "attempt": attempt}
                for game_id, attempt in sorted(in_progress)
            ],
            "completeBalancedPrefixBlocks": complete_blocks,
            "balancedPrefixPairs": len(ordered_pairs),
            "completePairsOutsideBalancedPrefix": len(pair_scores)
            - len(ordered_pairs),
        },
        "safety": {**safety, "failures": safety_failures, "passes": safety_failures == 0},
    }

    if gate == "development":
        score = (
            sum(pair_scores.values()) / len(pair_scores) if pair_scores else None
        )
        complete = len(pair_scores) == GATE_SPECS[gate]["roots"]
        passes = (
            complete
            and safety_failures == 0
            and not in_progress
            and score is not None
            and score >= GATE_SPECS[gate]["minimumCandidateScore"]
        )
        report["developmentScreen"] = {
            "requiredPairs": GATE_SPECS[gate]["roots"],
            "candidateScore": score,
            "minimumCandidateScore": GATE_SPECS[gate]["minimumCandidateScore"],
            "complete": complete,
            "decision": (
                "safety-fail"
                if safety_failures
                else (
                    "pass"
                    if passes
                    else (
                        "in-progress"
                        if in_progress
                        else ("fail" if complete else "continue")
                    )
                )
            ),
        }
    else:
        gate_report = _sequential_gate(ordered_pairs)
        if safety_failures:
            gate_report["decision"] = "safety-fail"
        elif in_progress:
            gate_report["decision"] = "in-progress"
        report["sequentialGate"] = gate_report

    if gate == "equal-time":
        if args.idle_attestation is None:
            raise ValueError("--idle-attestation is required for equal-time assessment")
        idle_record = _validate_idle_attestation(
            args.idle_attestation, config["runId"]
        )
        if _parse_utc(
            str(idle_record["attestation"]["createdUtc"])
        ) > _parse_utc(str(_field(run, "CreatedUtc"))):
            raise ValueError(
                "idle-machine attestation must precede creation of the match run"
            )
        report["idleMachine"] = idle_record
        starts = {
            (
                str(_field(record, "GameId", "")),
                _nonnegative_int(
                    _field(record, "Attempt"),
                    f"{_field(record, 'GameId', '')}.gameStart.Attempt",
                ),
            ): record
            for record in starts_all
        }
        results_by_key = {
            (
                str(_field(result, "GameId", "")),
                _nonnegative_int(
                    _field(result, "Attempt"),
                    f"{_field(result, 'GameId', '')}.Attempt",
                ),
            ): result
            for result in results
        }
        intervals: list[
            tuple[datetime, datetime | None, tuple[str, int]]
        ] = []
        for key, start in starts.items():
            started = _parse_utc(str(_field(start, "StartedUtc")))
            result = results_by_key.get(key)
            finished = (
                None
                if result is None
                else _parse_utc(str(_field(result, "FinishedUtc")))
            )
            if finished is not None and finished < started:
                raise ValueError(
                    f"{key[0]}/{key[1]} finishes before it starts"
                )
            intervals.append((started, finished, key))
        intervals.sort(key=lambda item: (item[0], item[2]))
        overlaps: list[tuple[str, str]] = []
        for left_index, left in enumerate(intervals):
            for right in intervals[left_index + 1 :]:
                if left[1] is None or right[0] < left[1]:
                    overlaps.append(
                        (
                            f"{left[2][0]}/{left[2][1]}",
                            f"{right[2][0]}/{right[2][1]}",
                        )
                    )
        telemetry: dict[str, list[dict[str, float]]] = defaultdict(list)
        missing = 0
        invalid = 0
        deadline_failures = 0
        for ply in all_ply_records:
            game_id = str(_field(ply, "GameId", ""))
            attempt = _nonnegative_int(
                _field(ply, "Attempt"), f"{game_id}.ply.Attempt"
            )
            ply_number = _nonnegative_int(
                _field(ply, "Ply"), f"{game_id}.ply.Ply"
            )
            key = (game_id, attempt)
            start = starts.get(key)
            if start is None:
                raise ValueError(f"{game_id}/{attempt}: ply has no gameStart")
            engine_id = str(_field(ply, "EngineId", "")).lower()
            color = str(_field(ply, "Color", "")).lower()
            opening_id = str(_field(start, "OpeningId", ""))
            opening = openings.get(opening_id)
            if opening is None:
                raise ValueError(
                    f"{game_id}/{attempt}: gameStart uses an unsealed opening"
                )
            root_side = str(
                opening["kingStateMatch"]["rootSideToMove"]
            ).lower()
            expected_color = (
                root_side
                if ply_number % 2 == 1
                else ("b" if root_side == "w" else "w")
            )
            expected_engine = str(
                _field(
                    start,
                    "WhiteEngineId"
                    if expected_color == "w"
                    else "BlackEngineId",
                    "",
                )
            ).lower()
            if (
                engine_id not in {"nnue-candidate", "hce-control"}
                or color not in {"w", "b"}
                or color != expected_color
                or engine_id != expected_engine
            ):
                invalid += 1
                continue
            search = _field(ply, "Search", {})
            final = _field(ply, "FinalInfo", {})
            if not isinstance(search, dict) or not isinstance(final, dict):
                missing += 1
                continue
            command = str(_field(search, "Command", ""))
            if (
                not _has_field(search, "WallTimeMs")
                or not _has_field(search, "DeadlineExceeded")
                or not _has_field(final, "Nodes")
                or not _has_field(final, "Depth")
                or not _has_field(final, "Nps")
            ):
                missing += 1
                continue
            try:
                wall = _finite_nonnegative(
                    _field(search, "WallTimeMs"),
                    f"{game_id}/{attempt}/{ply_number}.WallTimeMs",
                )
                nodes = _finite_nonnegative(
                    _field(final, "Nodes"),
                    f"{game_id}/{attempt}/{ply_number}.Nodes",
                )
                depth = _finite_nonnegative(
                    _field(final, "Depth"),
                    f"{game_id}/{attempt}/{ply_number}.Depth",
                )
                nps = _finite_nonnegative(
                    _field(final, "Nps"),
                    f"{game_id}/{attempt}/{ply_number}.Nps",
                )
            except ValueError:
                invalid += 1
                continue
            deadline_value = _field(search, "DeadlineExceeded")
            if not isinstance(deadline_value, bool):
                invalid += 1
                continue
            deadline = deadline_value
            compliant = (
                command == "go movetime 1000"
                and not deadline
                and wall <= GATE_SPECS[gate]["moveTimeMs"] + STOP_GRACE_MS
            )
            if not compliant:
                deadline_failures += 1
            telemetry[engine_id].append(
                {
                    "nodes": nodes,
                    "depth": depth,
                    "nps": nps,
                    "wallTimeMs": wall,
                }
            )

        def aggregate(rows: Sequence[dict[str, float]]) -> dict[str, Any]:
            return {
                "searches": len(rows),
                "meanNodes": (
                    sum(item["nodes"] for item in rows) / len(rows) if rows else None
                ),
                "meanDepth": (
                    sum(item["depth"] for item in rows) / len(rows) if rows else None
                ),
                "meanNps": (
                    sum(item["nps"] for item in rows) / len(rows) if rows else None
                ),
                "meanWallTimeMs": (
                    sum(item["wallTimeMs"] for item in rows) / len(rows)
                    if rows
                    else None
                ),
                "maximumWallTimeMs": (
                    max(item["wallTimeMs"] for item in rows) if rows else None
                ),
            }

        telemetry_integrity_passes = (
            not overlaps
            and missing == 0
            and invalid == 0
            and deadline_failures == 0
        )
        serialization_complete = not dangling
        report["equalTimeAudit"] = {
            "serialized": not overlaps and serialization_complete,
            "serializationComplete": serialization_complete,
            "overlappingGames": overlaps,
            "unfinishedAttempts": [
                {"gameId": game_id, "attempt": attempt}
                for game_id, attempt in sorted(dangling)
            ],
            "missingTelemetrySearches": missing,
            "invalidTelemetrySearches": invalid,
            "deadlineFailures": deadline_failures,
            "deadlineLimitMs": GATE_SPECS[gate]["moveTimeMs"] + STOP_GRACE_MS,
            "byEngine": {
                engine: aggregate(telemetry.get(engine, []))
                for engine in ("nnue-candidate", "hce-control")
            },
            "integrityPasses": telemetry_integrity_passes,
            "passes": telemetry_integrity_passes and serialization_complete,
        }
        if not telemetry_integrity_passes:
            report["sequentialGate"]["decision"] = "safety-fail"

    _atomic_json(args.output, report)
    print(f"Assessment: {args.output}")
    if gate == "development":
        print(f"Decision: {report['developmentScreen']['decision']}")
    else:
        print(f"Decision: {report['sequentialGate']['decision']}")
    return report


def _test_ofen(index: int, phase: str, side: str) -> str:
    counts = {"opening": 40, "middlegame": 30, "late": 18, "endgame": 8}
    count = counts[phase]
    squares = list(range(100))
    # Deterministic affine permutation; the phase/index-dependent occupied
    # subsets create far more orbit families than the smoke test needs.
    offset = (index * 17 + PHASES.index(phase) * 23) % 100
    step = (index * 2 + 21) % 100
    while math.gcd(step, 100) != 1:
        step += 2
    order = [squares[(offset + step * item) % 100] for item in range(100)]
    board: dict[int, str] = {order[0]: "K", order[1]: "k"}
    symbols = "PpNnBbRrQqCcWw"
    for position, square in enumerate(order[2:count], 2):
        board[square] = symbols[(index + position) % len(symbols)]
    ranks: list[str] = []
    for rank in range(9, -1, -1):
        tokens: list[str] = []
        empty = 0
        for file in range(10):
            symbol = board.get(file * 10 + rank)
            if symbol is None:
                empty += 1
            else:
                if empty:
                    tokens.append(str(empty))
                    empty = 0
                tokens.append(symbol)
        if empty:
            tokens.append(str(empty))
        ranks.append("".join(tokens))
    return f"{'/'.join(ranks)}[-/-/-/-] {side} - - 0 1"


def _write_synthetic_sampler(path: Path, gate: str, offset: int) -> None:
    records = []
    rank_counter = 0
    pair_counter = 0
    for phase in PHASES:
        for side in SIDES:
            for local in range(48):
                pair_counter += 1
                index = offset + PHASES.index(phase) * 10_000 + SIDES.index(side) * 1_000 + local
                ofen = _test_ofen(index, phase, side)
                rank_counter += 1
                pair = f"random-pair-{pair_counter:06d}"
                records.append(
                    {
                        "schemaVersion": 1,
                        "kind": "omega-rules-only-random-root",
                        "generatorSeed": str(GATE_SPECS[gate]["seed"]),
                        "trajectorySeed": str(index),
                        "trajectoryPairId": pair,
                        "trajectoryId": f"{pair}-ab",
                        "flavor": "ab",
                        "ply": 80,
                        "phase": phase,
                        "sideToMove": side,
                        "ofen": ofen,
                        "pieceCount": {
                            "opening": 40,
                            "middlegame": 30,
                            "late": 18,
                            "endgame": 8,
                        }[phase],
                        "whitePieces": 4,
                        "blackPieces": 4,
                        "champions": 0,
                        "wizards": 0,
                        "halfmoveClock": 0,
                        "selectionRank": hashlib.sha256(
                            f"synthetic-{gate}-{rank_counter}".encode()
                        ).hexdigest(),
                    }
                )
    _exclusive_bytes(
        path,
        b"".join(
            (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
            for record in records
        ),
    )
    sampler_runtime = path.parent / f"{gate}-OmegaRootSampler.dll"
    chesslib_runtime = path.parent / f"{gate}-ChessLib.dll"
    sampler_runtime.write_bytes(f"synthetic-sampler-{gate}".encode())
    chesslib_runtime.write_bytes(f"synthetic-chesslib-{gate}".encode())
    manifest = {
        "schemaVersion": 1,
        "kind": "omega-rules-only-random-root-manifest",
        "policy": {
            "deterministicPrng": "SplitMix64",
            "seed": str(GATE_SPECS[gate]["seed"]),
            "trajectoryPairs": SAMPLER_TRAJECTORY_PAIRS,
            "independentTrajectoriesPerPair": 2,
            "maxPlies": SAMPLER_MAX_PLIES,
            "positionsPerPhaseAndSide": SAMPLER_POSITIONS_PER_PHASE_SIDE,
            "captureSelectionPercent": SAMPLER_CAPTURE_PERCENT,
            "terminalRootsEmitted": 0,
            "maximumHalfmoveClock": 89,
            "minimumPieces": 7,
            "minimumPiecesPerSide": 2,
            "phasePlyWindows": {
                "opening": [6, 48],
                "middlegame": [20, 140],
                "late": [40, 260],
                "endgame": [60, 400],
            },
        },
        "coverage": {
            "records": len(records),
            "phaseCounts": {
                phase: sum(
                    1 for record in records if record["phase"] == phase
                )
                for phase in PHASES
            },
            "sideToMoveCounts": {
                side: sum(
                    1 for record in records if record["sideToMove"] == side
                )
                for side in SIDES
            },
        },
        "runtime": {
            "framework": "synthetic",
            "samplerAssembly": _identity(sampler_runtime),
            "chessLibAssembly": _identity(chesslib_runtime),
        },
        "output": _identity(path),
    }
    _exclusive_json(Path(str(path) + ".manifest.json"), manifest)


def _write_synthetic_events(
    seal: dict[str, Any],
    gate: str,
    path: Path,
    *,
    candidate_wins: bool,
) -> None:
    config_path = Path(str(seal["gates"][gate]["config"]["path"]))
    suite_path = Path(str(seal["gates"][gate]["suite"]["path"]))
    config = _load_object(config_path)
    suite = _load_object(suite_path)
    candidate_spec, hce_spec = config["engines"]
    records: list[dict[str, Any]] = [
        {
            "RecordType": "run",
            "RunId": config["runId"],
            "CreatedUtc": "2026-07-19T05:00:00Z",
            "ConfigSha256": _sha256(config_path),
            "OpeningSuiteSha256": _sha256(suite_path),
            "HarnessSha256": config["expectedHarnessSha256"],
            "HarnessBundleSha256": config["expectedHarnessBundleSha256"],
            "Seed": config["seed"],
            "Match": config["match"],
            "Engines": [
                {
                    "Id": "nnue-candidate",
                    "Sha256": candidate_spec["expectedSha256"],
                    "Options": candidate_spec["options"],
                    "ExternalAssets": [
                        {
                            "OptionName": "OmegaNNUEFile",
                            "Path": candidate_spec["options"]["OmegaNNUEFile"],
                            "Sha256": seal["candidateNetworkSha256"],
                        }
                    ],
                    "OmegaNnueActiveVerified": True,
                },
                {
                    "Id": "hce-control",
                    "Sha256": hce_spec["expectedSha256"],
                    "Options": hce_spec["options"],
                    "ExternalAssets": [],
                    "OmegaNnueActiveVerified": False,
                },
            ],
        }
    ]
    second = 0
    for opening in suite["openings"]:
        opening_id = opening["id"]
        pair_id = f"{opening_id}-r001"
        for suffix, white, black in (
            ("ab", "nnue-candidate", "hce-control"),
            ("ba", "hce-control", "nnue-candidate"),
        ):
            game_id = f"{pair_id}-{suffix}"
            # The synthetic fixture has at most 256 games.  Advance minutes
            # without relying on timedelta imports.
            minute, within = divmod(second, 60)
            started = datetime(
                2026, 7, 19, 5 + minute // 60, minute % 60, within,
                tzinfo=timezone.utc,
            )
            finished_minute, finished_second = divmod(second + 1, 60)
            finished = datetime(
                2026,
                7,
                19,
                5 + finished_minute // 60,
                finished_minute % 60,
                finished_second,
                tzinfo=timezone.utc,
            )
            records.append(
                {
                    "RecordType": "gameStart",
                    "GameId": game_id,
                    "PairId": pair_id,
                    "Attempt": 1,
                    "StartedUtc": started.isoformat().replace("+00:00", "Z"),
                    "OpeningId": opening_id,
                    "WhiteEngineId": white,
                    "BlackEngineId": black,
                    "InitialOfen": opening["initialOfen"],
                    "OpeningMoves": [],
                }
            )
            if gate == "equal-time":
                moving_color = opening["kingStateMatch"]["rootSideToMove"]
                moving_engine = white if moving_color == "w" else black
                records.append(
                    {
                        "RecordType": "ply",
                        "GameId": game_id,
                        "Attempt": 1,
                        "Ply": 1,
                        "EngineId": moving_engine,
                        "Color": moving_color,
                        "Search": {
                            "Command": "go movetime 1000",
                            "WallTimeMs": 1000.0,
                            "DeadlineExceeded": False,
                        },
                        "FinalInfo": {
                            "Nodes": 100_000,
                            "Depth": 12,
                            "Nps": 100_000,
                        },
                    }
                )
            else:
                records.append(
                    {
                        "RecordType": "ply",
                        "GameId": game_id,
                        "Attempt": 1,
                        "Ply": 1,
                        "EngineId": white,
                    }
                )
            if candidate_wins:
                result = "1-0" if white == "nnue-candidate" else "0-1"
                winner = "nnue-candidate"
                score_a = 1.0
            else:
                result = "1/2-1/2"
                winner = None
                score_a = 0.5
            records.append(
                {
                    "RecordType": "gameResult",
                    "GameId": game_id,
                    "PairId": pair_id,
                    "Attempt": 1,
                    "FinishedUtc": finished.isoformat().replace("+00:00", "Z"),
                    "OpeningId": opening_id,
                    "WhiteEngineId": white,
                    "BlackEngineId": black,
                    "Result": result,
                    "Termination": "checkmate" if candidate_wins else "draw",
                    "WinnerEngineId": winner,
                    "ScoreA": score_a,
                    "Plies": 1,
                    "IllegalMoves": 0,
                    "IllegalPvs": 0,
                    "ProtocolFailures": 0,
                    "TimeForfeits": 0,
                }
            )
            second += 1
    _exclusive_bytes(
        path,
        b"".join(
            (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
            for record in records
        ),
    )


def _self_test() -> None:
    # Seeded Random compatibility has been stable since .NET Framework.  Pin a
    # known sequence so a porting error cannot silently unbalance pair budgets.
    random = _DotNetRandom(0)
    if [random.next(2_147_483_647) for _ in range(3)] != [
        1_559_595_546,
        1_755_192_844,
        1_649_316_166,
    ]:
        raise AssertionError(".NET seeded Random compatibility mismatch")

    with tempfile.TemporaryDirectory(prefix="king-state-matches-") as temporary:
        root = Path(temporary)
        sampler = root / "sampler"
        sampler.mkdir()
        for index, gate in enumerate(GATE_SPECS):
            _write_synthetic_sampler(
                sampler / SOURCE_NAMES[gate], gate, 100_000 * (index + 1)
            )
        engine = root / "senpai.exe"
        engine.write_bytes(b"synthetic-engine")
        network = root / "candidate.nnue"
        network.write_bytes(b"synthetic-network")
        network_sha = _sha256(network)
        training = root / "training.jsonl"
        training.write_text("", encoding="utf-8")
        candidate_manifest = root / "candidate.manifest.json"
        _exclusive_json(
            candidate_manifest,
            {
                "inputs": [_identity(training)],
                "roundTrip": {"sha256": network_sha},
            },
        )
        plan = root / "training-plan.json"
        _exclusive_json(
            plan,
            {
                "kind": "omega-nnue-king-state-v1-training-plan",
                "identities": {"corpus": _identity(training)},
            },
        )
        selection = root / "selection.json"
        _exclusive_json(
            selection,
            {
                "kind": "omega-nnue-king-state-v1-validation-selection",
                "plan": _identity(plan),
                "selectedCandidateId": "K1",
                "selectedNetwork": _identity(network),
                "selectedManifest": _identity(candidate_manifest),
                "decision": {
                    "winner": "K1",
                    "eligibility": {"K1": {"eligible": True}},
                },
                "candidates": {"K1": {"health": {"passed": True}}},
            },
        )
        offline = root / "offline-test.json"
        _exclusive_json(
            offline,
            {
                "kind": "omega-nnue-king-state-v1-offline-test",
                "selection": _identity(selection),
                "selectedNetwork": _identity(network),
                "passed": True,
                "comparisons": {
                    "K0": {"passed": True},
                    "zeroResidual": {"passed": True},
                },
                "robustnessDirectionalImprovementOverK0": True,
                "robustnessDirectionalImprovementOverZeroResidual": True,
            },
        )
        harness_dir = root / "harness"
        harness_dir.mkdir()
        harness = harness_dir / "OmegaMatch.dll"
        harness.write_bytes(b"synthetic-harness")
        (harness_dir / "ChessLib.dll").write_bytes(b"synthetic-referee")
        output = root / "sealed"

        args = argparse.Namespace(
            output_dir=output,
            match_output_root=root / "runs",
            protocol=_protocol_path(),
            sampler_project=_sampler_project_path(),
            sampler_dir=sampler,
            engine_executable=engine,
            candidate_network=network,
            candidate_manifest=candidate_manifest,
            training_selection=[plan, selection, offline],
            training_corpus=[training],
            omega_match_assembly=harness,
            exclude=[],
        )
        seal_path = _seal(args, default_exclusions=False)
        seal = _verify_seal(seal_path)
        suites = [
            Path(str(seal["gates"][gate]["suite"]["path"])) for gate in GATE_SPECS
        ]
        signatures: set[str] = set()
        for gate, suite_path in zip(GATE_SPECS, suites):
            openings = _verify_suite(gate, suite_path)
            for opening in openings.values():
                current = set(opening["kingStateMatch"]["orbitSignatures"])
                if signatures.intersection(current):
                    raise AssertionError("self-test fresh suites leaked")
                signatures.update(current)

        development_events = root / "development-events.jsonl"
        _write_synthetic_events(
            seal, "development", development_events, candidate_wins=False
        )
        development_report = _assess(
            argparse.Namespace(
                seal=seal_path,
                gate="development",
                events=development_events,
                output=root / "development-assessment.json",
                idle_attestation=None,
            )
        )
        if development_report["developmentScreen"]["decision"] != "pass":
            raise AssertionError("synthetic development assessment did not pass")

        time_events = root / "equal-time-events.jsonl"
        _write_synthetic_events(
            seal, "equal-time", time_events, candidate_wins=True
        )
        time_config = _load_object(
            Path(str(seal["gates"]["equal-time"]["config"]["path"]))
        )
        attestation = root / "idle-attestation.json"
        _exclusive_json(
            attestation,
            {
                "schemaVersion": 1,
                "kind": "omega-equal-time-idle-attestation-v1",
                "runId": time_config["runId"],
                "createdUtc": "2026-07-19T04:59:00Z",
                "idleMachine": True,
                "oneGameAtATime": True,
                "concurrentMatchProcesses": 1,
                "operator": "synthetic self-test",
            },
        )
        time_report = _assess(
            argparse.Namespace(
                seal=seal_path,
                gate="equal-time",
                events=time_events,
                output=root / "equal-time-assessment.json",
                idle_attestation=attestation,
            )
        )
        if (
            time_report["sequentialGate"]["decision"] != "promote"
            or not time_report["equalTimeAudit"]["passes"]
        ):
            raise AssertionError("synthetic equal-time assessment did not promote")

        # ArenaRunner records one final failed search ply without applying it,
        # so gameResult.Plies stays at the successful-move count.  That shape
        # must reach a safety-fail report instead of dying in event parsing.
        terminal_failure_records = copy.deepcopy(
            _event_records(development_events)
        )
        failed_result_index = next(
            index
            for index, record in enumerate(terminal_failure_records)
            if _field(record, "RecordType") == "gameResult"
        )
        failed_result = terminal_failure_records[failed_result_index]
        failed_result["ProtocolFailures"] = 1
        terminal_failure_records.insert(
            failed_result_index,
            {
                "RecordType": "ply",
                "GameId": failed_result["GameId"],
                "Attempt": failed_result["Attempt"],
                "Ply": failed_result["Plies"] + 1,
                "EngineId": failed_result["WhiteEngineId"],
                "Error": "synthetic engine crash",
                "PostOfen": None,
            },
        )
        terminal_failure_events = root / "terminal-failure-events.jsonl"
        _write_event_records(terminal_failure_events, terminal_failure_records)
        terminal_failure_report = _assess(
            argparse.Namespace(
                seal=seal_path,
                gate="development",
                events=terminal_failure_events,
                output=root / "terminal-failure-assessment.json",
                idle_attestation=None,
            )
        )
        if (
            terminal_failure_report["developmentScreen"]["decision"]
            != "safety-fail"
            or terminal_failure_report["safety"]["protocolFailures"] != 1
        ):
            raise AssertionError(
                "terminal failed-ply fixture did not produce safety-fail"
            )

        # Negative counters must be rejected, never summed so they can cancel
        # a genuine safety failure.
        negative_counter_records = copy.deepcopy(
            _event_records(development_events)
        )
        next(
            record
            for record in negative_counter_records
            if _field(record, "RecordType") == "gameResult"
        )["IllegalMoves"] = -1
        negative_counter_events = root / "negative-counter-events.jsonl"
        _write_event_records(negative_counter_events, negative_counter_records)
        try:
            _assess(
                argparse.Namespace(
                    seal=seal_path,
                    gate="development",
                    events=negative_counter_events,
                    output=root / "negative-counter-assessment.json",
                    idle_attestation=None,
                )
            )
        except ValueError:
            pass
        else:
            raise AssertionError("negative safety counter was accepted")

        # A currently running attempt with a fully completed/promoting prefix
        # must withhold the formal decision and make serialization incomplete.
        in_progress_records = copy.deepcopy(_event_records(time_events))
        original_start = next(
            record
            for record in in_progress_records
            if _field(record, "RecordType") == "gameStart"
        )
        running_start = copy.deepcopy(original_start)
        running_start["Attempt"] = 2
        running_start["StartedUtc"] = "2026-07-19T06:00:00Z"
        in_progress_records.append(running_start)
        in_progress_events = root / "in-progress-events.jsonl"
        _write_event_records(in_progress_events, in_progress_records)
        in_progress_report = _assess(
            argparse.Namespace(
                seal=seal_path,
                gate="equal-time",
                events=in_progress_events,
                output=root / "in-progress-assessment.json",
                idle_attestation=attestation,
            )
        )
        if (
            in_progress_report["sequentialGate"]["decision"] != "in-progress"
            or in_progress_report["equalTimeAudit"]["serialized"]
            or in_progress_report["equalTimeAudit"]["serializationComplete"]
        ):
            raise AssertionError(
                "dangling attempt did not withhold the formal time decision"
            )

        # Unknown engine telemetry is a fail-closed integrity failure.
        unknown_engine_records = copy.deepcopy(_event_records(time_events))
        next(
            record
            for record in unknown_engine_records
            if _field(record, "RecordType") == "ply"
        )["EngineId"] = "intruder"
        unknown_engine_events = root / "unknown-engine-events.jsonl"
        _write_event_records(unknown_engine_events, unknown_engine_records)
        unknown_engine_report = _assess(
            argparse.Namespace(
                seal=seal_path,
                gate="equal-time",
                events=unknown_engine_events,
                output=root / "unknown-engine-assessment.json",
                idle_attestation=attestation,
            )
        )
        if (
            unknown_engine_report["sequentialGate"]["decision"]
            != "safety-fail"
            or unknown_engine_report["equalTimeAudit"][
                "invalidTelemetrySearches"
            ]
            != 1
        ):
            raise AssertionError("unknown telemetry engine was not rejected")

        # Every numeric telemetry field and the explicit deadline flag is
        # independently fail-closed.
        telemetry_cases = (
            ("negative-wall", "Search", "WallTimeMs", -1),
            ("negative-nodes", "FinalInfo", "Nodes", -1),
            ("nonfinite-depth", "FinalInfo", "Depth", float("nan")),
            ("nonfinite-nps", "FinalInfo", "Nps", float("inf")),
        )
        for case, container, field, poison in telemetry_cases:
            poisoned_records = copy.deepcopy(_event_records(time_events))
            poisoned_ply = next(
                record
                for record in poisoned_records
                if _field(record, "RecordType") == "ply"
            )
            poisoned_ply[container][field] = poison
            poisoned_events = root / f"{case}-events.jsonl"
            _write_event_records(poisoned_events, poisoned_records)
            poisoned_report = _assess(
                argparse.Namespace(
                    seal=seal_path,
                    gate="equal-time",
                    events=poisoned_events,
                    output=root / f"{case}-assessment.json",
                    idle_attestation=attestation,
                )
            )
            if (
                poisoned_report["sequentialGate"]["decision"] != "safety-fail"
                or poisoned_report["equalTimeAudit"][
                    "invalidTelemetrySearches"
                ]
                != 1
            ):
                raise AssertionError(f"{case} telemetry was not rejected")

        missing_deadline_records = copy.deepcopy(_event_records(time_events))
        missing_deadline_ply = next(
            record
            for record in missing_deadline_records
            if _field(record, "RecordType") == "ply"
        )
        del missing_deadline_ply["Search"]["DeadlineExceeded"]
        missing_deadline_events = root / "missing-deadline-events.jsonl"
        _write_event_records(missing_deadline_events, missing_deadline_records)
        missing_deadline_report = _assess(
            argparse.Namespace(
                seal=seal_path,
                gate="equal-time",
                events=missing_deadline_events,
                output=root / "missing-deadline-assessment.json",
                idle_attestation=attestation,
            )
        )
        if (
            missing_deadline_report["sequentialGate"]["decision"]
            != "safety-fail"
            or missing_deadline_report["equalTimeAudit"][
                "missingTelemetrySearches"
            ]
            != 1
        ):
            raise AssertionError("missing deadline telemetry was not rejected")

        # Tampering with a suite must invalidate the seal.
        tampered = suites[0]
        original = tampered.read_bytes()
        tampered.write_bytes(original + b" ")
        try:
            _verify_seal(seal_path)
        except ValueError:
            pass
        else:
            raise AssertionError("tampered suite passed seal verification")
        tampered.write_bytes(original)
        _verify_seal(seal_path)

        # A training-orbit collision must be rejected by selection.
        sample_opening = next(iter(_verify_suite("development", suites[0]).values()))
        _, _, colliding = _leakage_keys(sample_opening["initialOfen"])
        roots, _ = _verify_sampler_source(
            sampler / SOURCE_NAMES["development"], "development"
        )
        selected, rejected = _select_roots(
            roots,
            GATE_SPECS["development"],
            set(colliding),
            set(),
        )
        if not rejected.get("training-or-historical-orbit"):
            raise AssertionError("training-orbit collision was not rejected")
        if any(set(root.orbit_signatures).intersection(colliding) for root in selected):
            raise AssertionError("selected suite retained a forbidden orbit")

        # Balanced gate fixtures pin promote, futility, and max-pair
        # inconclusive behavior.
        promote = _sequential_gate(
            [(f"p{index}", 1.0) for index in range(MAXIMUM_GATE_PAIRS)]
        )
        futile = _sequential_gate(
            [(f"p{index}", 0.0) for index in range(MAXIMUM_GATE_PAIRS)]
        )
        draws = _sequential_gate(
            [(f"p{index}", 0.5) for index in range(MAXIMUM_GATE_PAIRS)]
        )
        early_crossing_reversal = _sequential_gate(
            [
                *[(f"early-win-{index}", 1.0) for index in range(12)],
                *[(f"later-loss-{index}", 0.0) for index in range(52)],
            ]
        )
        if promote["decision"] != "promote":
            raise AssertionError("promotion e-process fixture did not promote")
        if futile["decision"] != "futility":
            raise AssertionError("futility e-process fixture did not stop")
        if draws["decision"] != "inconclusive":
            raise AssertionError("max-pair continuation was not inconclusive")
        early_checkpoint = next(
            item
            for item in early_crossing_reversal["checkpoints"]
            if item["pairs"] == 12
        )
        if (
            early_checkpoint["promotionEValue"] < PROMOTION_E_VALUE
            or early_checkpoint["signal"] != "continue"
            or early_crossing_reversal["decision"] != "futility"
            or early_crossing_reversal["signal"] != "futility"
            or early_crossing_reversal["signalPair"] != MINIMUM_GATE_PAIRS
        ):
            raise AssertionError(
                "pre-minimum promotion crossing leaked through the 12W/52L "
                "minimum-pair reversal fixture"
            )

    print("king_state_matches self-test passed")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    sample = subparsers.add_parser("sample", help="run the three rules-only samplers")
    sample.add_argument("--output-dir", required=True, type=Path)
    sample.add_argument("--dotnet", required=True, type=Path)
    sample.add_argument(
        "--sampler-project", type=Path, default=_sampler_project_path()
    )
    sample.add_argument("--protocol", type=Path, default=_protocol_path())

    seal = subparsers.add_parser("seal", help="select, configure, and seal all gates")
    seal.add_argument("--output-dir", required=True, type=Path)
    seal.add_argument("--match-output-root", type=Path)
    seal.add_argument("--sampler-dir", required=True, type=Path)
    seal.add_argument(
        "--sampler-project", type=Path, default=_sampler_project_path()
    )
    seal.add_argument("--protocol", type=Path, default=_protocol_path())
    seal.add_argument("--engine-executable", required=True, type=Path)
    seal.add_argument("--candidate-network", required=True, type=Path)
    seal.add_argument("--candidate-manifest", required=True, type=Path)
    seal.add_argument(
        "--training-selection", action="append", required=True, type=Path
    )
    seal.add_argument("--training-corpus", action="append", required=True, type=Path)
    seal.add_argument("--omega-match-assembly", required=True, type=Path)
    seal.add_argument(
        "--exclude",
        action="append",
        default=[],
        type=Path,
        help="Additional JSON/JSONL file or directory whose OFEN orbits are excluded.",
    )

    verify = subparsers.add_parser("verify", help="verify a published match seal")
    verify.add_argument("--seal", required=True, type=Path)

    attest = subparsers.add_parser(
        "attest",
        help="exclusively record the required equal-time idle-machine attestation",
    )
    attest.add_argument("--seal", required=True, type=Path)
    attest.add_argument("--output", required=True, type=Path)
    attest.add_argument("--operator")

    assess = subparsers.add_parser("assess", help="assess one sealed match run")
    assess.add_argument("--seal", required=True, type=Path)
    assess.add_argument("--gate", required=True, choices=tuple(GATE_SPECS))
    assess.add_argument("--events", required=True, type=Path)
    assess.add_argument("--output", required=True, type=Path)
    assess.add_argument("--idle-attestation", type=Path)

    subparsers.add_parser("self-test", help="run synthetic adversarial checks")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.command == "sample":
        _sample(args)
    elif args.command == "seal":
        _seal(args)
    elif args.command == "verify":
        seal = _verify_seal(args.seal)
        print(f"Verified: {args.seal}")
        print(f"Seal SHA-256: {_sha256(args.seal)}")
        print(f"Candidate: {seal['candidateNetworkSha256']}")
    elif args.command == "attest":
        _attest(args)
    elif args.command == "assess":
        _assess(args)
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)


if __name__ == "__main__":
    main()
