#!/usr/bin/env python3
"""Candidate-blind normal-start practical gate for open confirmation v2.

This module contains only deterministic source verification, endpoint
selection, suite/config construction, and result assessment.  It never reads
a candidate claim or network.  The v2 readiness layer supplies the one sealed
stage seed and the already computed forbidden endpoint-orbit set; the v2
orchestrator supplies the sealed candidate/control identities only after the
joint suite seal exists.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import types
from typing import Any, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 2
SOURCE_KIND = "omega-rules-only-balanced-opening-prefix-v2"
MANIFEST_KIND = "omega-rules-only-balanced-opening-prefix-manifest-v2"
COMPLETION_KIND = (
    "omega-rules-only-balanced-opening-prefix-completion-seal-v2"
)
SUITE_KIND = "omega-nnue-open-confirmation-v2-normal-start-suite"
ASSESSMENT_KIND = (
    "omega-nnue-open-confirmation-v2-normal-start-clock-assessment"
)
GATE = "normal-start-clock"
OMEGA_START = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
    "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
)
DEPTHS = (4, 8, 12, 16)
TRAJECTORIES = 8_192
ROOTS_PER_DEPTH = 128
ROOTS = ROOTS_PER_DEPTH * len(DEPTHS)
MINIMUM_PAIRS = 128
MAXIMUM_PAIRS = ROOTS
RESUME_PAIRS = 4
NULL_ELO = 15.0
FUTILITY_E_VALUE = 20.0
INITIAL_TIME_MS = 60_000
INCREMENT_MS = 1_000
SEARCH_TIMEOUT_MS = 65_000
STOP_GRACE_MS = 2_000
MAX_PLIES = 300
ABSOLUTE_MAX_PLIES = 400
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
HEX_256 = re.compile(r"^[0-9a-f]{64}$")
MOVE = re.compile(r"^[a-jw][0-9][a-jw][0-9][qrbncw]?$", re.IGNORECASE)
CANONICAL_CLOCK_COMMAND = re.compile(
    r"^go wtime ([0-9]+) btime ([0-9]+) winc ([0-9]+) binc ([0-9]+)$"
)
UINT64_MASK = (1 << 64) - 1


_REPO = Path(__file__).resolve().parents[2]
_AUTHENTICATED = {
    "tools/omega_nnue/king_state_matches.py": (
        138_593,
        "c8e9d45452716f468023b7af1607e735a269e131c95bce510dd25849f76d9601",
    ),
    "tools/omega_nnue/omega_nnue.py": (
        53_900,
        "efc55715895f32e948db35428372393c711f701f2e84c69256bdf689065422aa",
    ),
    "tools/omega_nnue/select_screen.py": (
        39_442,
        "304172e583b4c963718191017b4f8d2426aea325dd42337c197496ee738677ee",
    ),
}


def _load_authenticated(name: str, relative: str) -> types.ModuleType:
    if name in sys.modules:
        raise ImportError(f"refusing preloaded practical dependency: {name}")
    path = (_REPO / relative).resolve()
    payload = path.read_bytes()
    size, digest = _AUTHENTICATED[relative]
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise ImportError(f"unauthenticated practical dependency: {path}")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    module.__loader__ = None
    sys.modules[name] = module
    try:
        exec(compile(payload, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


omega = _load_authenticated(
    "omega_nnue_practical_v2", "tools/omega_nnue/omega_nnue.py"
)
_prior_omega_alias = sys.modules.get("omega_nnue")
_prior_screen_alias = sys.modules.get("select_screen")
sys.modules["omega_nnue"] = omega
try:
    screen = _load_authenticated(
        "select_screen_practical_v2", "tools/omega_nnue/select_screen.py"
    )
    # The shared core imports by canonical module names.  Give it authenticated
    # aliases only for the duration of its source execution, then remove them so a
    # caller cannot substitute a later module through the import cache.
    sys.modules["select_screen"] = screen
    core = _load_authenticated(
        "king_state_matches_practical_v2",
        "tools/omega_nnue/king_state_matches.py",
    )
finally:
    if _prior_omega_alias is None:
        sys.modules.pop("omega_nnue", None)
    else:
        sys.modules["omega_nnue"] = _prior_omega_alias
    if _prior_screen_alias is None:
        sys.modules.pop("select_screen", None)
    else:
        sys.modules["select_screen"] = _prior_screen_alias

_POSITION_META = core._position_meta
_SHUFFLED_INDICES = core._shuffled_indices
_CORE_SEQUENTIAL_GATE = core._sequential_gate
_CORE_BETS = tuple(core.BET_FRACTIONS)
if _CORE_BETS != BET_FRACTIONS:
    raise ImportError("shared e-process bet fractions changed")


def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path | str) -> dict[str, Any]:
    path = _resolve(path)
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "sha256": sha256(path)}


def verify_identity(value: Mapping[str, Any], label: str) -> Path:
    if set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity fields changed")
    path = _resolve(str(value["path"]))
    actual = identity(path)
    if (
        type(value["bytes"]) is not int
        or value["bytes"] != actual["bytes"]
        or str(value["sha256"]).lower() != actual["sha256"]
    ):
        raise ValueError(f"{label} identity mismatch")
    return path


def _object(path: Path | str, label: str) -> dict[str, Any]:
    payload = _resolve(path).read_bytes()
    if not payload.endswith(b"\n") or b"\r" in payload:
        raise ValueError(f"{label} is not canonical LF-terminated JSON")
    value = json.loads(payload)
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def _jsonl(path: Path | str) -> Iterator[tuple[int, dict[str, Any]]]:
    payload = _resolve(path).read_bytes()
    if not payload or not payload.endswith(b"\n") or b"\r" in payload:
        raise ValueError(f"{path} is not canonical JSONL")
    for line, raw in enumerate(payload.splitlines(), 1):
        value = json.loads(raw)
        if type(value) is not dict:
            raise ValueError(f"{path}:{line}: record must be an object")
        yield line, value


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        return (
            _resolve(str(left["path"])) == _resolve(str(right["path"]))
            and type(left["bytes"]) is int
            and left["bytes"] == right["bytes"]
            and str(left["sha256"]).lower() == str(right["sha256"]).lower()
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _canonical_json(path: Path, value: Mapping[str, Any]) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    payload = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{hashlib.sha256(payload).hexdigest()}.tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    try:
        with temporary.open("xb", buffering=0) as stream:
            stream.write(payload)
            stream.flush()
        if path.exists():
            raise FileExistsError(path)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _rank(seed: int, depth: int, trajectory: int, final_ofen: str) -> str:
    payload = (
        f"omega-practical-v2-select\0{seed}\0{depth}\0{trajectory}\0{final_ofen}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mix64(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & UINT64_MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & UINT64_MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & UINT64_MASK
    return (value ^ (value >> 31)) & UINT64_MASK


def _trajectory_seed(seed: int, trajectory: int) -> int:
    return _mix64(
        (seed ^ ((trajectory * 0x9E3779B97F4A7C15) & UINT64_MASK))
        & UINT64_MASK
    )


def _producer_selection_rank(
    seed: int,
    trajectory_seed: int,
    trajectory: int,
    depth: int,
    moves: Sequence[str],
    final_ofen: str,
) -> str:
    payload = (
        f"omega-practical-opening-v2\0{seed}\0{trajectory_seed}\0"
        f"{trajectory - 1}\0{depth}\0{' '.join(moves)}\0{final_ofen}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_manifest(
    source: Path, manifest_path: Path, seal_path: Path, seed: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _object(manifest_path, "practical source manifest")
    seal = _object(seal_path, "practical source completion seal")
    if set(manifest) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "policy",
        "coverage",
        "runtime",
        "output",
        "finalStageSeal",
    } or set(seal) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "output",
        "manifest",
        "producer",
        "finalStageSeal",
    }:
        raise ValueError("practical source publication fields changed")
    if manifest.get("schemaVersion") != 2 or manifest.get("kind") != MANIFEST_KIND:
        raise ValueError("practical source manifest kind changed")
    if seal.get("schemaVersion") != 2 or seal.get("kind") != COMPLETION_KIND:
        raise ValueError("practical source completion kind changed")
    policy = manifest.get("policy")
    if type(policy) is not dict:
        raise ValueError("practical source policy missing")
    required = {
        "officialInitialOfen": OMEGA_START,
        "deterministicPrng": "SplitMix64",
        "seed": str(seed),
        "trajectories": TRAJECTORIES,
        "workers": 4,
        "targetPlies": list(DEPTHS),
        "assignment": "trajectory index modulo frozen target-ply order",
        "movePolicy": "uniform random permutation; first legal vertically colour-reflected full-move pair",
        "candidateInputs": 0,
        "engineEvaluationInputs": 0,
        "terminalPrefixesEmitted": 0,
        "minimumPieces": 37,
    }
    if set(policy) != set(required) | {"verticalColourReflection"}:
        raise ValueError("practical source policy inventory changed")
    for key, wanted in required.items():
        if policy.get(key) != wanted:
            raise ValueError(f"practical source policy {key} changed")
    if policy.get("verticalColourReflection") != {
        "ordinarySquares": "file unchanged; rank r maps to 9-r",
        "corners": {"w1": "w4", "w4": "w1", "w2": "w3", "w3": "w2"},
        "promotionSuffixUnchanged": True,
    }:
        raise ValueError("practical source reflection policy changed")
    manifest_created = _utc(
        manifest.get("createdUtc"), "practical source manifest createdUtc"
    )
    seal_created = _utc(seal.get("createdUtc"), "practical source seal createdUtc")
    if seal_created < manifest_created:
        raise ValueError("practical source seal predates its manifest")
    runtime = manifest.get("runtime")
    producer = seal.get("producer")
    producer_fields = {"framework", "samplerAssembly", "chessLibAssembly"}
    if (
        type(runtime) is not dict
        or type(producer) is not dict
        or set(runtime) != producer_fields
        or set(producer) != producer_fields
        or type(runtime.get("framework")) is not str
        or not runtime["framework"]
        or producer.get("framework") != runtime["framework"]
    ):
        raise ValueError("practical source producer identity changed")
    if manifest.get("finalStageSeal") is not False or seal.get("finalStageSeal") is not True:
        raise ValueError("practical source publication seal changed")
    if not _same_identity(manifest.get("output", {}), identity(source)):
        raise ValueError("practical source manifest output mismatch")
    if not _same_identity(seal.get("output", {}), identity(source)):
        raise ValueError("practical source seal output mismatch")
    if not _same_identity(seal.get("manifest", {}), identity(manifest_path)):
        raise ValueError("practical source seal manifest mismatch")
    return manifest, seal


def read_source(
    source: Path | str,
    manifest: Path | str,
    completion_seal: Path | str,
    *,
    seed: int,
) -> list[dict[str, Any]]:
    if type(seed) is not int or seed < 0 or seed > (1 << 31) - 1:
        raise ValueError("practical stage seed must be a nonnegative Int32")
    source = _resolve(source)
    manifest = _resolve(manifest)
    completion_seal = _resolve(completion_seal)
    manifest_value, _ = _validate_manifest(
        source, manifest, completion_seal, seed
    )
    records: list[dict[str, Any]] = []
    seen_trajectory: set[int] = set()
    counts: Counter[int] = Counter()
    for line, record in _jsonl(source):
        required = {
            "schemaVersion",
            "kind",
            "generatorSeed",
            "trajectoryIndex",
            "trajectorySeed",
            "targetPlies",
            "initialOfen",
            "moves",
            "finalOfen",
            "pieceCount",
            "sideToMove",
            "selectionRank",
        }
        if set(record) != required:
            raise ValueError(f"{source}:{line}: source fields changed")
        trajectory = record["trajectoryIndex"]
        depth = record["targetPlies"]
        moves = record["moves"]
        trajectory_seed_text = record["trajectorySeed"]
        try:
            trajectory_seed = int(trajectory_seed_text)
        except (TypeError, ValueError):
            trajectory_seed = -1
        if (
            record["schemaVersion"] != 2
            or record["kind"] != SOURCE_KIND
            or record["generatorSeed"] != str(seed)
            or type(trajectory) is not int
            or trajectory < 1
            or trajectory > TRAJECTORIES
            or trajectory in seen_trajectory
            or depth not in DEPTHS
            or (trajectory - 1) % len(DEPTHS) != DEPTHS.index(depth)
            or record["initialOfen"] != OMEGA_START
            or type(moves) is not list
            or len(moves) != depth
            or any(
                type(move) is not str
                or move != move.lower()
                or not MOVE.fullmatch(move)
                for move in moves
            )
            or type(record["finalOfen"]) is not str
            or type(trajectory_seed_text) is not str
            or not 0 <= trajectory_seed <= UINT64_MASK
            or trajectory_seed_text != str(trajectory_seed)
            or trajectory_seed != _trajectory_seed(seed, trajectory)
            or record["sideToMove"] != "w"
            or type(record["pieceCount"]) is not int
            or record["pieceCount"] < 37
            or not HEX_256.fullmatch(str(record["selectionRank"]))
        ):
            raise ValueError(f"{source}:{line}: invalid practical source record")
        if record["selectionRank"] != _producer_selection_rank(
            seed,
            trajectory_seed,
            trajectory,
            depth,
            moves,
            record["finalOfen"],
        ):
            raise ValueError(f"{source}:{line}: producer selection rank changed")
        phase, side, position_id, orbit, signatures = _POSITION_META(
            str(record["finalOfen"])
        )
        if phase != "opening" or side != "w":
            raise ValueError(f"{source}:{line}: endpoint is not White-to-move opening")
        seen_trajectory.add(trajectory)
        counts[depth] += 1
        records.append(
            {
                **record,
                "sourceLine": line,
                "source": identity(source),
                "positionId": position_id,
                "orbit": orbit,
                "orbitSignatures": list(signatures),
                "selectionRankV2": _rank(
                    seed, depth, trajectory, str(record["finalOfen"])
                ),
            }
        )
    if len(records) != TRAJECTORIES or counts != Counter(
        {depth: TRAJECTORIES // len(DEPTHS) for depth in DEPTHS}
    ):
        raise ValueError("practical source coverage changed")
    expected_coverage = {
        "records": len(records),
        "depthCounts": {str(depth): counts[depth] for depth in DEPTHS},
        "uniqueMovePrefixes": len({tuple(item["moves"]) for item in records}),
        "uniqueFinalOfens": len({item["finalOfen"] for item in records}),
    }
    if manifest_value.get("coverage") != expected_coverage:
        raise ValueError("practical source manifest coverage changed")
    return records


def select_endpoints(
    records: Sequence[Mapping[str, Any]],
    *,
    forbidden_orbits: set[str],
    used_orbits: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    used_trajectories: set[int] = set()
    for depth in DEPTHS:
        accepted = 0
        bucket = sorted(
            (record for record in records if record["targetPlies"] == depth),
            key=lambda record: str(record["selectionRankV2"]),
        )
        for record in bucket:
            signatures = set(record["orbitSignatures"])
            reasons: list[str] = []
            if record["trajectoryIndex"] in used_trajectories:
                reasons.append("trajectory-reuse")
            if forbidden_orbits.intersection(signatures):
                reasons.append("historical-endpoint-orbit")
            if used_orbits.intersection(signatures):
                reasons.append("cross-suite-endpoint-orbit")
            if reasons:
                rejected.update(reasons)
                continue
            selected.append(dict(record))
            used_trajectories.add(int(record["trajectoryIndex"]))
            used_orbits.update(signatures)
            accepted += 1
            if accepted == ROOTS_PER_DEPTH:
                break
        if accepted != ROOTS_PER_DEPTH:
            raise ValueError(
                f"could select only {accepted}/{ROOTS_PER_DEPTH} depth-{depth} endpoints"
            )
    return selected, dict(rejected)


def build_suite(
    selected: Sequence[Mapping[str, Any]], *, seed: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    buckets = {
        depth: sorted(
            (item for item in selected if item["targetPlies"] == depth),
            key=lambda item: str(item["selectionRankV2"]),
        )
        for depth in DEPTHS
    }
    if any(len(bucket) != ROOTS_PER_DEPTH for bucket in buckets.values()):
        raise ValueError("practical endpoint quotas changed")
    schedule: list[dict[str, Any]] = []
    for block in range(ROOTS_PER_DEPTH):
        for depth in DEPTHS:
            item = buckets[depth][block]
            opening_id = (
                f"ocv2-normal-b{block + 1:03d}-p{depth:02d}-"
                f"{str(item['orbit'])[:12]}"
            )
            schedule.append(
                {
                    "id": opening_id,
                    "source": "candidate-blind rules-only balanced prefix v2",
                    "initialOfen": OMEGA_START,
                    "moves": list(item["moves"]),
                    "openConfirmationV2": {
                        "gate": GATE,
                        "balancedBlock": block + 1,
                        "depthStratum": depth,
                        "trajectoryIndex": item["trajectoryIndex"],
                        "trajectorySeed": item["trajectorySeed"],
                        "sourceLine": item["sourceLine"],
                        "sourceSha256": item["source"]["sha256"],
                        "selectionRank": item["selectionRankV2"],
                        "finalOfen": item["finalOfen"],
                        "identityKey": item["positionId"],
                        "symmetryOrbitKey": item["orbit"],
                        "orbitSignatures": list(item["orbitSignatures"]),
                    },
                }
            )
    permutation = _SHUFFLED_INDICES(len(schedule), seed)
    source_order: list[dict[str, Any] | None] = [None] * len(schedule)
    for scheduled_index, original_index in enumerate(permutation):
        source_order[original_index] = schedule[scheduled_index]
    if any(item is None for item in source_order):
        raise AssertionError("practical inverse schedule failed")
    suite = {
        # OmegaMatch's frozen OpeningSuite envelope is schema 1.  The v2
        # provenance lives in the strict kind/normalStartClockSuite fields,
        # which the harness ignores while this verifier authenticates them.
        "schemaVersion": 1,
        "kind": SUITE_KIND,
        "name": "Omega NNUE open confirmation v2 normal-start clock",
        "normalStartClockSuite": {
            "gate": GATE,
            "seed": seed,
            "roots": ROOTS,
            "rootsPerDepth": ROOTS_PER_DEPTH,
            "depthOrder": list(DEPTHS),
            "balancedBlockSizePairs": len(DEPTHS),
            "schedulePermutation": "System.Random(int) compatibility Fisher-Yates",
            "candidateInputs": 0,
        },
        "openings": source_order,
    }
    return suite, schedule


def build_config(
    *,
    suite: Mapping[str, Any],
    suite_path: Path | str,
    seed: int,
    engine: Mapping[str, Any],
    network: Mapping[str, Any],
    omega_match: Mapping[str, Any],
    omega_match_bundle_sha256: str,
    output_directory: Path | str,
) -> dict[str, Any]:
    verify_identity(engine, "engine")
    verify_identity(network, "network")
    verify_identity(omega_match, "OmegaMatch")
    if suite.get("kind") != SUITE_KIND:
        raise ValueError("cannot configure an unverified practical suite")
    common = {
        "Threads": "1",
        "Hash": "128",
        "Ponder": "false",
        "OwnBook": "false",
        "UCI_Chess960": "false",
        "UCI_Variant": "omega",
    }
    candidate = {
        **common,
        "OmegaNNUEFile": str(_resolve(str(network["path"]))),
        "UseOmegaNNUE": "true",
    }
    control = {
        **common,
        "OmegaNNUEFile": "<empty>",
        "UseOmegaNNUE": "false",
    }
    suite_id = identity(suite_path)
    engine_path = _resolve(str(engine["path"]))
    return {
        "schemaVersion": 1,
        "expectedHarnessSha256": omega_match["sha256"],
        "expectedHarnessBundleSha256": omega_match_bundle_sha256,
        "expectedOpeningSuiteSha256": suite_id["sha256"],
        "runId": f"open-confirmation-v2-normal-clock-{str(network['sha256'])[:12]}",
        "outputDirectory": str(_resolve(output_directory)),
        "seed": seed,
        "openConfirmationV2Execution": {
            "oneGameAtATime": True,
            "maximumConcurrentGames": 1,
            "pairBudgetRequired": True,
            "pairBudgetMustBeMultipleOf": 4,
            "initialPairBudget": MINIMUM_PAIRS,
            "resumePairBudget": RESUME_PAIRS,
            "idleMachineRequired": True,
        },
        "engines": [
            {
                "id": "nnue-candidate",
                "executable": str(engine_path),
                "arguments": "",
                "workingDirectory": str(engine_path.parent),
                "expectedSha256": engine["sha256"],
                "expectedAssetSha256": {"OmegaNNUEFile": network["sha256"]},
                "options": candidate,
            },
            {
                "id": "hce-control",
                "executable": str(engine_path),
                "arguments": "",
                "workingDirectory": str(engine_path.parent),
                "expectedSha256": engine["sha256"],
                "options": control,
            },
        ],
        "match": {
            "engineA": "nnue-candidate",
            "engineB": "hce-control",
            "openingsFile": str(_resolve(suite_path)),
            "repeats": 1,
            "maxPlies": MAX_PLIES,
            "absoluteMaxPlies": ABSOLUTE_MAX_PLIES,
            "mode": "clock",
            "initialTimeMs": INITIAL_TIME_MS,
            "incrementMs": INCREMENT_MS,
            "searchTimeoutMs": SEARCH_TIMEOUT_MS,
            "stopGraceMs": STOP_GRACE_MS,
            "freshProcessPerGame": True,
            "sequentialGate": {
                "candidateEngine": "nnue-candidate",
                "minimumPairs": MINIMUM_PAIRS,
                "nullElo": NULL_ELO,
                "promotionAlpha": 0.005,
                "futilityBeta": 0.05,
            },
        },
    }


def verify_suite(value: Mapping[str, Any], *, seed: int) -> dict[str, dict[str, Any]]:
    if set(value) != {"schemaVersion", "kind", "name", "normalStartClockSuite", "openings"}:
        raise ValueError("practical suite fields changed")
    if value["schemaVersion"] != 1 or value["kind"] != SUITE_KIND:
        raise ValueError("practical suite envelope changed")
    metadata = value["normalStartClockSuite"]
    if type(metadata) is not dict or metadata != {
        "gate": GATE,
        "seed": seed,
        "roots": ROOTS,
        "rootsPerDepth": ROOTS_PER_DEPTH,
        "depthOrder": list(DEPTHS),
        "balancedBlockSizePairs": 4,
        "schedulePermutation": "System.Random(int) compatibility Fisher-Yates",
        "candidateInputs": 0,
    }:
        raise ValueError("practical suite metadata changed")
    openings = value["openings"]
    if type(openings) is not list or len(openings) != ROOTS:
        raise ValueError("practical suite opening count changed")
    by_id: dict[str, dict[str, Any]] = {}
    scheduled: list[dict[str, Any]] = []
    used_signatures: set[str] = set()
    used_trajectories: set[int] = set()
    for opening in openings:
        if type(opening) is not dict or set(opening) != {
            "id", "source", "initialOfen", "moves", "openConfirmationV2"
        }:
            raise ValueError("practical opening shape changed")
        opening_id = opening["id"]
        detail = opening["openConfirmationV2"]
        detail_fields = {
            "gate",
            "balancedBlock",
            "depthStratum",
            "trajectoryIndex",
            "trajectorySeed",
            "sourceLine",
            "sourceSha256",
            "selectionRank",
            "finalOfen",
            "identityKey",
            "symmetryOrbitKey",
            "orbitSignatures",
        }
        if (
            type(opening_id) is not str
            or opening_id in by_id
            or opening["initialOfen"] != OMEGA_START
            or type(opening["moves"]) is not list
            or type(detail) is not dict
            or set(detail) != detail_fields
            or detail.get("gate") != GATE
            or detail.get("depthStratum") not in DEPTHS
            or len(opening["moves"]) != detail["depthStratum"]
            or any(
                type(move) is not str or MOVE.fullmatch(move) is None
                for move in opening["moves"]
            )
            or type(detail.get("balancedBlock")) is not int
            or not 1 <= detail["balancedBlock"] <= ROOTS_PER_DEPTH
            or type(detail.get("trajectoryIndex")) is not int
            or not 1 <= detail["trajectoryIndex"] <= TRAJECTORIES
            or detail["trajectoryIndex"] in used_trajectories
            or type(detail.get("trajectorySeed")) is not str
            or type(detail.get("sourceLine")) is not int
            or detail["sourceLine"] < 1
            or HEX_256.fullmatch(str(detail.get("sourceSha256"))) is None
            or HEX_256.fullmatch(str(detail.get("selectionRank"))) is None
        ):
            raise ValueError("practical opening identity changed")
        phase, side, identity_key, orbit, signatures = _POSITION_META(
            str(detail["finalOfen"])
        )
        if (
            phase != "opening"
            or side != "w"
            or detail.get("identityKey") != identity_key
            or detail.get("symmetryOrbitKey") != orbit
            or detail.get("orbitSignatures") != list(signatures)
            or used_signatures.intersection(signatures)
        ):
            raise ValueError("practical opening endpoint metadata changed")
        used_signatures.update(signatures)
        used_trajectories.add(detail["trajectoryIndex"])
        by_id[opening_id] = dict(opening)
        scheduled.append(dict(opening))
    permutation = _SHUFFLED_INDICES(ROOTS, seed)
    ordered = [scheduled[index] for index in permutation]
    for block in range(ROOTS_PER_DEPTH):
        items = ordered[block * 4 : (block + 1) * 4]
        if [item["openConfirmationV2"]["depthStratum"] for item in items] != list(DEPTHS):
            raise ValueError("practical schedule lost a depth-balanced block")
        if any(item["openConfirmationV2"]["balancedBlock"] != block + 1 for item in items):
            raise ValueError("practical schedule block identity changed")
    return by_id


def sequential_gate(
    observations: Sequence[tuple[str, float]], *, promotion_log_threshold: float
) -> dict[str, Any]:
    if not math.isfinite(promotion_log_threshold) or promotion_log_threshold <= 0:
        raise ValueError("promotion log threshold must be finite positive")
    if core._sequential_gate is not _CORE_SEQUENTIAL_GATE:
        raise ValueError("authenticated shared e-process callable changed")
    for pair_id, score in observations:
        if type(pair_id) is not str or type(score) not in (int, float):
            raise ValueError("invalid practical pair observation")
    names = (
        "MINIMUM_GATE_PAIRS",
        "MAXIMUM_GATE_PAIRS",
        "NULL_ELO",
        "PROMOTION_E_VALUE",
        "FUTILITY_E_VALUE",
        "BET_FRACTIONS",
    )
    prior = {name: getattr(core, name) for name in names}
    try:
        core.MINIMUM_GATE_PAIRS = MINIMUM_PAIRS
        core.MAXIMUM_GATE_PAIRS = MAXIMUM_PAIRS
        core.NULL_ELO = NULL_ELO
        core.PROMOTION_E_VALUE = math.nextafter(
            math.exp(promotion_log_threshold), math.inf
        )
        core.FUTILITY_E_VALUE = FUTILITY_E_VALUE
        core.BET_FRACTIONS = BET_FRACTIONS
        return _CORE_SEQUENTIAL_GATE(observations)
    finally:
        for name, value in prior.items():
            setattr(core, name, value)


def _candidate_score(result: Mapping[str, Any]) -> float:
    text = result.get("Result")
    white = result.get("WhiteEngineId")
    black = result.get("BlackEngineId")
    if text == "1/2-1/2":
        return 0.5
    if text == "1-0":
        return 1.0 if white == "nnue-candidate" else 0.0
    if text == "0-1":
        return 1.0 if black == "nnue-candidate" else 0.0
    raise ValueError("invalid practical game result")


def _explicit_terminal_failure(record: Mapping[str, Any]) -> bool:
    """Recognize the two terminal-failure shapes emitted by OmegaMatch."""

    error = record.get("Error")
    if type(error) is str:
        return bool(error.strip())
    if error is not None:
        return False
    search = record.get("Search")
    return type(search) is dict and search.get("ProcessExited") is True


def _clock_audit(plies: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    invalid = 0
    deadline = 0
    maximum_error = 0.0
    for ply in plies:
        search = ply.get("Search")
        if type(search) is not dict:
            invalid += 1
            continue
        command = CANONICAL_CLOCK_COMMAND.fullmatch(str(search.get("Command", "")))
        color = ply.get("Color")
        wall_value = search.get("WallTimeMs")
        try:
            wb = int(ply["WhiteClockBeforeMs"])
            bb = int(ply["BlackClockBeforeMs"])
            wa = int(ply["WhiteClockAfterMs"])
            ba = int(ply["BlackClockAfterMs"])
            wall = float(wall_value)
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        if (
            command is None
            or [int(command.group(i)) for i in range(1, 5)]
            != [wb, bb, INCREMENT_MS, INCREMENT_MS]
            or color not in {"white", "black"}
            or type(wall_value) not in (int, float)
            or not math.isfinite(wall)
            or wall < 0
        ):
            invalid += 1
            continue
        if search.get("DeadlineExceeded") is not False:
            deadline += 1
        if color == "white":
            error = abs(wa - (wb - wall + INCREMENT_MS))
            unchanged = ba == bb
        else:
            error = abs(ba - (bb - wall + INCREMENT_MS))
            unchanged = wa == wb
        maximum_error = max(maximum_error, error)
        if error > 2.0 or not unchanged:
            invalid += 1
    return {
        "searches": len(plies),
        "invalidClockSearches": invalid,
        "deadlineFailures": deadline,
        "maximumClockRoundingErrorMs": maximum_error,
        "passes": invalid == 0 and deadline == 0,
    }


def _utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"{label} is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} is not a real UTC timestamp") from error
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{label} is not UTC")
    return parsed


def _serialization_audit(
    starts: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    start_by_key: dict[tuple[str, int], datetime] = {}
    result_by_key: dict[tuple[str, int], datetime] = {}
    for label, records, timestamp, destination in (
        ("start", starts, "StartedUtc", start_by_key),
        ("result", results, "FinishedUtc", result_by_key),
    ):
        for record in records:
            game = record.get("GameId")
            attempt = record.get("Attempt")
            if type(game) is not str or not game or type(attempt) is not int or attempt < 1:
                raise ValueError(f"practical {label} identity is malformed")
            key = (game, attempt)
            if key in destination:
                raise ValueError(f"duplicate practical {label} identity")
            destination[key] = _utc(
                record.get(timestamp), f"practical {label} {game}/{attempt}"
            )
    if not set(result_by_key).issubset(start_by_key):
        raise ValueError("practical result has no matching game start")
    intervals: list[tuple[datetime, datetime | None, tuple[str, int]]] = []
    unfinished: list[dict[str, Any]] = []
    for key, started in start_by_key.items():
        finished = result_by_key.get(key)
        if finished is not None and finished < started:
            raise ValueError("practical game finishes before it starts")
        if finished is None:
            unfinished.append({"gameId": key[0], "attempt": key[1]})
        intervals.append((started, finished, key))
    intervals.sort(key=lambda item: (item[0], item[2]))
    overlaps: list[list[str]] = []
    for index, left in enumerate(intervals):
        for right in intervals[index + 1 :]:
            if left[1] is None or right[0] < left[1]:
                overlaps.append(
                    [
                        f"{left[2][0]}/{left[2][1]}",
                        f"{right[2][0]}/{right[2][1]}",
                    ]
                )
    complete = not unfinished
    return {
        "serialized": not overlaps and complete,
        "serializationComplete": complete,
        "overlappingGames": overlaps,
        "unfinishedAttempts": sorted(
            unfinished, key=lambda item: (item["gameId"], item["attempt"])
        ),
        "passes": not overlaps and complete,
    }


def _identity_record(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity shape changed")
    if (
        type(value.get("path")) is not str
        or not value["path"]
        or type(value.get("bytes")) is not int
        or value["bytes"] < 0
        or type(value.get("sha256")) is not str
        or HEX_256.fullmatch(value["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    return dict(value)


def _casefold_object(value: Any, label: str) -> Any:
    if type(value) is dict:
        result: dict[str, Any] = {}
        for key, item in value.items():
            folded = str(key).casefold()
            if folded in result:
                raise ValueError(f"{label} repeats a case-folded key")
            result[folded] = _casefold_object(item, f"{label}.{key}")
        return result
    if type(value) is list:
        return [_casefold_object(item, label) for item in value]
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _resolved_match_spec(match: Mapping[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        "engineA": "baseline",
        "engineB": "candidate",
        "openingsFile": "Openings/omega-public-24.json",
        "repeats": 1,
        "maxPlies": 400,
        "absoluteMaxPlies": None,
        "mode": "depth",
        "depth": 6,
        "nodes": 100_000,
        "moveTimeMs": 1_000,
        "initialTimeMs": 60_000,
        "incrementMs": 500,
        "searchTimeoutMs": 120_000,
        "stopGraceMs": 2_000,
        "bootstrapIterations": 10_000,
        "sequentialGate": {
            "candidateEngine": "",
            "minimumPairs": 64,
            "nullElo": 10.0,
            "promotionAlpha": 0.05,
            "futilityBeta": 0.10,
        },
        "freshProcessPerGame": True,
    }
    for key, value in match.items():
        if key == "sequentialGate":
            if type(value) is not dict:
                raise ValueError("practical sequentialGate is malformed")
            resolved["sequentialGate"].update(value)
        else:
            if key not in resolved:
                raise ValueError(f"practical match has unknown field {key}")
            resolved[key] = value
    return resolved


def _authenticate_engine_inventory(
    engine_specs: Any, emitted_engines: Any, label: str
) -> None:
    if (
        type(engine_specs) is not list
        or type(emitted_engines) is not list
        or len(engine_specs) != 2
        or len(emitted_engines) != 2
        or any(type(item) is not dict for item in engine_specs)
        or any(type(item) is not dict for item in emitted_engines)
    ):
        raise ValueError(f"{label} engine inventory must contain exactly two objects")
    spec_ids = [item.get("id") for item in engine_specs]
    runtime_ids = [item.get("Id") for item in emitted_engines]
    expected_ids = {"nnue-candidate", "hce-control"}
    if len(set(spec_ids)) != 2 or len(set(runtime_ids)) != 2 or set(spec_ids) != expected_ids or set(runtime_ids) != expected_ids:
        raise ValueError(f"{label} engine IDs are duplicate or off profile")
    specs = {item["id"]: item for item in engine_specs}
    runtime = {item["Id"]: item for item in emitted_engines}
    required_runtime_fields = {
        "Id", "Executable", "Arguments", "WorkingDirectory", "Sha256",
        "FileSize", "LastWriteUtc", "UciName", "UciAuthor", "Options",
        "ExternalAssets", "StartupDiagnostics", "OmegaNnueActiveVerified",
    }
    common_runtime_fields = (
        "Executable", "Arguments", "WorkingDirectory", "Sha256", "FileSize",
        "LastWriteUtc", "UciName", "UciAuthor",
    )
    for engine_id in ("nnue-candidate", "hce-control"):
        spec = specs[engine_id]
        item = runtime[engine_id]
        if set(item) != required_runtime_fields:
            raise ValueError(f"{label} {engine_id} runtime field inventory changed")
        executable = spec.get("executable")
        if type(executable) is not str or not executable:
            raise ValueError(f"{label} {engine_id} sealed executable is missing")
        expected_executable = Path(executable).resolve()
        expected_arguments = spec.get("arguments")
        expected_working_directory = spec.get("workingDirectory")
        if type(expected_arguments) is not str or type(expected_working_directory) is not str:
            raise ValueError(f"{label} {engine_id} sealed launch fields are missing")
        if type(item.get("Executable")) is not str or Path(item["Executable"]).resolve() != expected_executable:
            raise ValueError(f"{label} {engine_id} executable path changed")
        if (
            Path(expected_working_directory).resolve() != expected_executable.parent
            or type(item.get("WorkingDirectory")) is not str
            or Path(item["WorkingDirectory"]).resolve() != Path(expected_working_directory).resolve()
        ):
            raise ValueError(f"{label} {engine_id} working directory changed")
        if item.get("Arguments") != expected_arguments or item.get("Sha256") != spec.get("expectedSha256"):
            raise ValueError(f"{label} {engine_id} executable identity changed")
        if type(item.get("FileSize")) is not int or item["FileSize"] < 1:
            raise ValueError(f"{label} {engine_id} executable size is malformed")
        if expected_executable.is_file() and item["FileSize"] != expected_executable.stat().st_size:
            raise ValueError(f"{label} {engine_id} executable size changed")
        for name in ("LastWriteUtc", "UciName", "UciAuthor"):
            if type(item.get(name)) is not str or not item[name]:
                raise ValueError(f"{label} {engine_id} {name} is missing")
        options = item.get("Options")
        expected_options = spec.get("options")
        if (
            type(options) is not dict
            or type(expected_options) is not dict
            or set(options) != set(expected_options)
            or any(type(key) is not str or type(value) is not str for key, value in options.items())
        ):
            raise ValueError(f"{label} {engine_id} effective options changed")
        for name, expected in expected_options.items():
            actual = options[name]
            if name == "OmegaNNUEFile" and str(expected).casefold() != "<empty>":
                if Path(actual).resolve() != Path(str(expected)).resolve():
                    raise ValueError(f"{label} {engine_id} OmegaNNUEFile changed")
            elif actual != expected:
                raise ValueError(f"{label} {engine_id} option {name} changed")
    candidate = runtime["nnue-candidate"]
    control = runtime["hce-control"]
    for name in common_runtime_fields:
        if candidate[name] != control[name]:
            raise ValueError(f"{label} candidate/control {name} differ")
    expected_assets = specs["nnue-candidate"].get("expectedAssetSha256")
    assets = candidate.get("ExternalAssets")
    if type(expected_assets) is not dict or set(expected_assets) != {"OmegaNNUEFile"} or type(assets) is not list or len(assets) != 1 or type(assets[0]) is not dict:
        raise ValueError(f"{label} candidate asset must be an exact singleton")
    asset = assets[0]
    if set(asset) != {"OptionName", "Path", "Sha256", "FileSize", "LastWriteUtc"}:
        raise ValueError(f"{label} candidate asset fields changed")
    network_path = Path(candidate["Options"]["OmegaNNUEFile"]).resolve()
    if (
        asset.get("OptionName") != "OmegaNNUEFile"
        or asset.get("Sha256") != expected_assets["OmegaNNUEFile"]
        or type(asset.get("Path")) is not str
        or Path(asset["Path"]).resolve() != network_path
        or type(asset.get("FileSize")) is not int
        or asset["FileSize"] < 1
        or type(asset.get("LastWriteUtc")) is not str
        or not asset["LastWriteUtc"]
    ):
        raise ValueError(f"{label} candidate asset identity changed")
    if network_path.is_file() and asset["FileSize"] != network_path.stat().st_size:
        raise ValueError(f"{label} candidate asset size changed")
    if candidate.get("OmegaNnueActiveVerified") is not True:
        raise ValueError(f"{label} candidate activation is not attested")
    diagnostics = candidate.get("StartupDiagnostics")
    if type(diagnostics) is not list or any(type(item) is not str for item in diagnostics):
        raise ValueError(f"{label} candidate diagnostics are malformed")
    nnue = [item for item in diagnostics if item.startswith("info string Omega NNUE")]
    loaded = [item for item in nnue if item.startswith("info string Omega NNUE loaded:")]
    if len(loaded) != 1 or " from " not in loaded[0] or Path(loaded[0].rsplit(" from ", 1)[1]).resolve() != network_path or not nnue or nnue[-1] != "info string Omega NNUE evaluation active":
        raise ValueError(f"{label} candidate diagnostics do not bind the active network")
    if control.get("ExternalAssets") != [] or control.get("OmegaNnueActiveVerified") is not False:
        raise ValueError(f"{label} control activation identity changed")
    control_diagnostics = control.get("StartupDiagnostics")
    if type(control_diagnostics) is not list or any(type(item) is not str for item in control_diagnostics):
        raise ValueError(f"{label} control diagnostics are malformed")
    control_nnue = [item for item in control_diagnostics if item.startswith("info string Omega NNUE")]
    if (
        not control_nnue
        or control_nnue[-1] != "info string Omega NNUE disabled; handcrafted evaluation active"
        or any(item.startswith("info string Omega NNUE loaded:") or item == "info string Omega NNUE evaluation active" for item in control_nnue)
    ):
        raise ValueError(f"{label} control diagnostics do not prove HCE-only evaluation")


def _authenticate_practical_events(
    *,
    suite: Mapping[str, Any],
    openings: Mapping[str, Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    seed: int,
    config: Mapping[str, Any],
    suite_identity: Mapping[str, Any],
    config_identity: Mapping[str, Any],
) -> dict[str, Any]:
    suite_id = _identity_record(suite_identity, "practical suite")
    config_id = _identity_record(config_identity, "practical config")
    if type(events) is not list and type(events) is not tuple:
        raise ValueError("practical events must be an ordered sequence")
    runs = [item for item in events if item.get("RecordType") == "run"]
    if len(runs) != 1 or not events or events[0] is not runs[0]:
        raise ValueError("practical events lack exactly one leading run")
    run = runs[0]
    expected_run = {
        "RunId": config.get("runId"),
        "ProfileId": config.get("profileId"),
        "FreshnessMarker": config.get("freshnessMarker"),
        "ConfigSha256": config_id["sha256"],
        "OpeningSuiteSha256": suite_id["sha256"],
        "Seed": seed,
    }
    for field, expected in expected_run.items():
        if run.get(field) != expected or type(run.get(field)) is not type(expected):
            raise ValueError(f"practical run {field} is not sealed")
    match = config.get("match")
    emitted_match = run.get("Match")
    if type(match) is not dict or type(emitted_match) is not dict:
        raise ValueError("practical effective match settings are missing")
    expected_match = _casefold_object(
        _resolved_match_spec(match), "practical sealed full MatchSpec"
    )
    actual_match = _casefold_object(
        emitted_match, "practical raw full MatchSpec"
    )
    if type(actual_match.get("mode")) is str:
        actual_match["mode"] = actual_match["mode"].casefold()
    expected_match["mode"] = str(expected_match["mode"]).casefold()
    if actual_match != expected_match:
        raise ValueError("practical effective full MatchSpec differs from config")
    _authenticate_engine_inventory(
        config.get("engines"), run.get("Engines"), "practical run"
    )

    source = suite.get("openings")
    if type(source) is not list or len(source) != ROOTS:
        raise ValueError("practical schedule source changed")
    ordered = [source[index] for index in _SHUFFLED_INDICES(ROOTS, seed)]
    expected_games: list[dict[str, Any]] = []
    for opening in ordered:
        opening_id = opening["id"]
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
                    "OpeningMoves": opening["moves"],
                }
            )

    schedule_index = 0
    active: tuple[str, int] | None = None
    next_attempt: dict[str, int] = {}
    starts: dict[tuple[str, int], Mapping[str, Any]] = {}
    plies: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    results: list[Mapping[str, Any]] = []
    for record in events[1:]:
        if schedule_index >= len(expected_games):
            raise ValueError("practical events exceed the sealed schedule")
        expected = expected_games[schedule_index]
        record_type = record.get("RecordType")
        if record_type == "gameStart":
            game_id = record.get("GameId")
            attempt = record.get("Attempt")
            if game_id != expected["GameId"] or type(attempt) is not int or attempt != next_attempt.get(str(game_id), 1):
                raise ValueError("practical gameStart identity/attempt is off schedule")
            for field, wanted in expected.items():
                if record.get(field) != wanted or type(record.get(field)) is not type(wanted):
                    raise ValueError(f"practical gameStart {field} changed")
            active = (str(game_id), attempt)
            if active in starts:
                raise ValueError("duplicate practical gameStart attempt")
            starts[active] = record
            plies[active] = []
            next_attempt[str(game_id)] = attempt + 1
        elif record_type == "ply":
            key = (str(record.get("GameId")), record.get("Attempt"))
            if key != active or key not in starts:
                raise ValueError("practical ply is unlinked")
            game_plies = plies[key]
            opening_depth = len(starts[key]["OpeningMoves"])
            if record.get("Ply") != opening_depth + len(game_plies) + 1:
                raise ValueError("practical ply numbering is not unique/monotone")
            start = starts[key]
            initial_side = str(start["InitialOfen"]).split()[1]
            if initial_side not in {"w", "b"}:
                raise ValueError("practical gameStart side to move is malformed")
            if len(start["OpeningMoves"]) % 2:
                initial_side = "b" if initial_side == "w" else "w"
            side = initial_side if len(game_plies) % 2 == 0 else ("b" if initial_side == "w" else "w")
            color = "white" if side == "w" else "black"
            if record.get("Color") != color:
                raise ValueError("practical ply raw Color is noncanonical or wrong")
            engine = start["WhiteEngineId"] if color == "white" else start["BlackEngineId"]
            if record.get("EngineId") != engine:
                raise ValueError("practical ply engine/color relation changed")
            endpoint = openings[str(start["OpeningId"])]["openConfirmationV2"]["finalOfen"]
            if not game_plies and record.get("PreOfen") != endpoint:
                raise ValueError("practical first ply is not rooted at the sealed endpoint")
            if game_plies and record.get("PreOfen") != game_plies[-1].get("PostOfen"):
                raise ValueError("practical ply OFEN chain is discontinuous")
            game_plies.append(record)
        elif record_type == "gameResult":
            key = (str(record.get("GameId")), record.get("Attempt"))
            if key != active or key not in starts:
                raise ValueError("practical result is unlinked")
            start = starts[key]
            for field in ("GameId", "PairId", "Attempt", "OpeningId", "WhiteEngineId", "BlackEngineId"):
                if record.get(field) != start.get(field) or type(record.get(field)) is not type(start.get(field)):
                    raise ValueError(f"practical result {field} contradicts its start")
            count = record.get("Plies")
            opening_depth = len(start["OpeningMoves"])
            game_plies = plies[key]
            if type(count) is not int or count < opening_depth:
                raise ValueError("practical result precedes its opening prefix")
            successful_searches = count - opening_depth
            decisive_safety = sum(
                int(record.get(field, -1))
                for field in ("IllegalMoves", "ProtocolFailures", "TimeForfeits")
            )
            for successful in game_plies[:successful_searches]:
                if successful.get("PostOfen") in (None, "") or successful.get("Error") not in (None, ""):
                    raise ValueError("practical successful search ply is malformed")
            if len(game_plies) == successful_searches:
                pass
            elif len(game_plies) == successful_searches + 1 and decisive_safety > 0:
                terminal = game_plies[-1]
                if (
                    terminal.get("Ply") != count + 1
                    or not _explicit_terminal_failure(terminal)
                    or terminal.get("PostOfen") not in (None, "")
                    or type(record.get("Termination")) is not str
                    or not record["Termination"].strip()
                ):
                    raise ValueError("practical terminal failed-search ply is malformed")
            else:
                raise ValueError("practical result lacks exact emitted ply coverage")
            endpoint = openings[str(start["OpeningId"])]["openConfirmationV2"]["finalOfen"]
            expected_final = (
                game_plies[successful_searches - 1].get("PostOfen")
                if successful_searches
                else endpoint
            )
            if record.get("FinalOfen") != expected_final:
                raise ValueError("practical result FinalOfen breaks the ply chain")
            score = _candidate_score(record)
            score_a = record.get("ScoreA")
            if type(score_a) not in (int, float) or type(score_a) is bool or not math.isfinite(float(score_a)) or abs(float(score_a) - score) > 1e-12:
                raise ValueError("practical result ScoreA contradicts Result")
            winner = {
                "1-0": start["WhiteEngineId"],
                "0-1": start["BlackEngineId"],
                "1/2-1/2": None,
            }[record["Result"]]
            if record.get("WinnerEngineId") != winner:
                raise ValueError("practical result winner contradicts Result")
            results.append(record)
            schedule_index += 1
            active = None
        else:
            raise ValueError("practical events contain an unknown record type")
    return {
        "starts": list(starts.values()),
        "plies": [ply for game in plies.values() for ply in game],
        "results": results,
        "active": active,
        "completedGames": schedule_index,
        "completePairs": schedule_index // 2,
        "authenticated": True,
    }


def assess_events(
    *,
    suite: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    seed: int,
    promotion_log_threshold: float,
    config: Mapping[str, Any],
    suite_identity: Mapping[str, Any],
    config_identity: Mapping[str, Any],
) -> dict[str, Any]:
    openings = verify_suite(suite, seed=seed)
    authentication = _authenticate_practical_events(
        suite=suite,
        openings=openings,
        events=events,
        seed=seed,
        config=config,
        suite_identity=suite_identity,
        config_identity=config_identity,
    )
    starts = authentication["starts"]
    plies = authentication["plies"]
    results = authentication["results"]
    by_opening: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    safety = Counter(
        {
            "illegalMoves": 0,
            "illegalPvs": 0,
            "protocolFailures": 0,
            "timeForfeits": 0,
            "abandonedAttempts": 0,
        }
    )
    for result in results:
        opening_id = str(result.get("OpeningId", ""))
        if opening_id not in openings:
            raise ValueError("practical events use an unsealed opening")
        by_opening[opening_id].append(result)
        for key in ("IllegalMoves", "IllegalPvs", "ProtocolFailures", "TimeForfeits"):
            value = result.get(key)
            if type(value) is not int or value < 0:
                raise ValueError("practical safety counter malformed")
            safety[key[0].lower() + key[1:]] += value
    start_keys = {(str(item["GameId"]), int(item["Attempt"])) for item in starts}
    result_keys = {
        (str(item.get("GameId", "")), int(item.get("Attempt", 0))) for item in results
    }
    safety["abandonedAttempts"] = len(start_keys.difference(result_keys))
    pair_scores: dict[str, float] = {}
    for opening_id, opening in openings.items():
        games = by_opening.get(opening_id, [])
        roles = {
            (
                str(game.get("WhiteEngineId", "")).lower(),
                str(game.get("BlackEngineId", "")).lower(),
            )
            for game in games
        }
        if len(games) != 2 or roles != {
            ("nnue-candidate", "hce-control"),
            ("hce-control", "nnue-candidate"),
        }:
            continue
        pair_scores[opening_id] = sum(_candidate_score(game) for game in games) / 2
    scheduled = [openings[item["id"]] for item in suite["openings"]]
    permutation = _SHUFFLED_INDICES(ROOTS, seed)
    ordered_openings = [scheduled[index] for index in permutation]
    observations: list[tuple[str, float]] = []
    for block in range(ROOTS_PER_DEPTH):
        block_openings = ordered_openings[block * 4 : (block + 1) * 4]
        if any(item["id"] not in pair_scores for item in block_openings):
            break
        observations.extend((item["id"], pair_scores[item["id"]]) for item in block_openings)
    clock = _clock_audit(plies)
    serialization = _serialization_audit(starts, results)
    clock.update(
        {
            "serialized": serialization["serialized"],
            "serializationComplete": serialization["serializationComplete"],
            "overlappingGames": serialization["overlappingGames"],
            "unfinishedAttempts": serialization["unfinishedAttempts"],
            "passes": clock["passes"] and serialization["passes"],
        }
    )
    gate = sequential_gate(
        observations, promotion_log_threshold=promotion_log_threshold
    )
    zero_safety = sum(safety.values()) == 0 and clock["passes"]
    if not zero_safety:
        gate["decision"] = "safety-fail"
    return {
        "schemaVersion": 2,
        "kind": ASSESSMENT_KIND,
        "gate": GATE,
        "progress": {
            "finishedGames": len(results),
            "completePairs": len(pair_scores),
            "balancedPrefixPairs": len(observations),
        },
        "safety": dict(safety),
        "zeroSafetyFailures": zero_safety,
        "clockAudit": clock,
        "eventAuthentication": {
            "completedGames": authentication["completedGames"],
            "completePairs": authentication["completePairs"],
            "activeAttempt": (
                None
                if authentication["active"] is None
                else {
                    "gameId": authentication["active"][0],
                    "attempt": authentication["active"][1],
                }
            ),
            "allStartsResultsAndPliesAuthenticated": authentication["authenticated"],
            "config": dict(config_identity),
            "suite": dict(suite_identity),
        },
        "sequentialGate": gate,
    }


def self_test() -> None:
    global _POSITION_META

    if (
        _trajectory_seed(0x123456789ABCDEF0, 1) != 16_483_130_067_377_077_212
        or _producer_selection_rank(
            5,
            7,
            1,
            4,
            ["a0a1", "a9a8", "b0b1", "b9b8"],
            "ofen",
        )
        != "4cc7ddd47734d0665e40c3ec3b6ef4638ea21b4c2b8acd6c3aa92452983add40"
    ):
        raise AssertionError("practical producer derivation vector changed")
    real_meta = _POSITION_META(OMEGA_START)
    if real_meta[0:2] != ("opening", "w") or len(real_meta[4]) == 0:
        raise AssertionError("authenticated Omega OFEN parser self-test changed")
    synthetic: list[dict[str, Any]] = []
    for trajectory in range(1, TRAJECTORIES + 1):
        depth = DEPTHS[(trajectory - 1) % 4]
        # Distinct synthetic OFENs are unnecessary here: selection behavior is
        # tested with already normalized orbit records, independent of parsing.
        synthetic.append(
            {
                "targetPlies": depth,
                "trajectoryIndex": trajectory,
                "trajectorySeed": str(trajectory * 17),
                "moves": ["a1a2", "a8a7"] * (depth // 2),
                "finalOfen": f"synthetic-{trajectory}",
                "sourceLine": trajectory,
                "source": {"sha256": "1" * 64},
                "positionId": hashlib.sha256(f"id-{trajectory}".encode()).hexdigest(),
                "orbit": hashlib.sha256(f"orbit-{trajectory}".encode()).hexdigest(),
                "orbitSignatures": [
                    hashlib.sha256(f"sig-{trajectory}".encode()).hexdigest()
                ],
                "selectionRankV2": hashlib.sha256(
                    f"rank-{trajectory}".encode()
                ).hexdigest(),
            }
        )
    used: set[str] = set()
    selected, rejected = select_endpoints(
        synthetic, forbidden_orbits=set(), used_orbits=used
    )
    if len(selected) != ROOTS or rejected or len(used) != ROOTS:
        raise AssertionError("practical selector self-test changed")
    suite, schedule = build_suite(selected, seed=1_710_004)

    def synthetic_meta(ofen: str) -> tuple[str, str, str, str, tuple[str, ...]]:
        match = re.fullmatch(r"synthetic-([1-9][0-9]*)", ofen)
        if match is None:
            raise ValueError("unexpected synthetic OFEN")
        trajectory = int(match.group(1))
        return (
            "opening",
            "w",
            hashlib.sha256(f"id-{trajectory}".encode()).hexdigest(),
            hashlib.sha256(f"orbit-{trajectory}".encode()).hexdigest(),
            (hashlib.sha256(f"sig-{trajectory}".encode()).hexdigest(),),
        )

    saved_position_meta = _POSITION_META
    try:
        _POSITION_META = synthetic_meta
        if (
            len(schedule) != ROOTS
            or len(verify_suite(suite, seed=1_710_004)) != ROOTS
        ):
            raise AssertionError("practical suite self-test changed")
        tampered = json.loads(json.dumps(suite))
        tampered["normalStartClockSuite"]["candidateInputs"] = 1
        try:
            verify_suite(tampered, seed=1_710_004)
        except ValueError:
            pass
        else:
            raise AssertionError("candidate-aware practical suite was accepted")
    finally:
        _POSITION_META = saved_position_meta

    # A complete 512-pair forged-stream regression: only the exact pinned
    # schedule, start/result/ply relations, opening-ply offsets, ScoreA, raw
    # color tokens, and full effective MatchSpec may reach the e-process.
    common_options = {
        "Threads": "1",
        "Hash": "128",
        "Ponder": "false",
        "OwnBook": "false",
        "UCI_Chess960": "false",
        "UCI_Variant": "omega",
    }
    candidate_options = {
        **common_options,
        "OmegaNNUEFile": "C:/synthetic/network.onnx",
        "UseOmegaNNUE": "true",
    }
    control_options = {
        **common_options,
        "OmegaNNUEFile": "<empty>",
        "UseOmegaNNUE": "false",
    }
    synthetic_config = {
        "runId": "open-confirmation-v2-a000001-normal-start-clock-synthetic",
        "profileId": "omega-nnue-open-confirmation-v2",
        "freshnessMarker": "omega-nnue-open-confirmation-v2:attempt-000001:normal-start-clock",
        "seed": 1_710_004,
        "engines": [
            {
                "id": "nnue-candidate",
                "executable": "C:/synthetic/senpai.exe",
                "arguments": "",
                "workingDirectory": "C:/synthetic",
                "expectedSha256": "4" * 64,
                "expectedAssetSha256": {"OmegaNNUEFile": "5" * 64},
                "options": candidate_options,
            },
            {
                "id": "hce-control",
                "executable": "C:/synthetic/senpai.exe",
                "arguments": "",
                "workingDirectory": "C:/synthetic",
                "expectedSha256": "4" * 64,
                "options": control_options,
            },
        ],
        "match": {
            "engineA": "nnue-candidate",
            "engineB": "hce-control",
            "openingsFile": "C:/synthetic/suite.json",
            "repeats": 1,
            "maxPlies": MAX_PLIES,
            "absoluteMaxPlies": ABSOLUTE_MAX_PLIES,
            "mode": "clock",
            "initialTimeMs": INITIAL_TIME_MS,
            "incrementMs": INCREMENT_MS,
            "searchTimeoutMs": SEARCH_TIMEOUT_MS,
            "stopGraceMs": STOP_GRACE_MS,
            "freshProcessPerGame": True,
            "sequentialGate": {
                "candidateEngine": "nnue-candidate",
                "minimumPairs": MINIMUM_PAIRS,
                "nullElo": NULL_ELO,
                "promotionAlpha": 0.005,
                "futilityBeta": 0.05,
            },
        },
    }
    synthetic_suite_identity = {
        "path": "C:/synthetic/suite.json",
        "bytes": 1,
        "sha256": "2" * 64,
    }
    synthetic_config_identity = {
        "path": "C:/synthetic/config.json",
        "bytes": 1,
        "sha256": "3" * 64,
    }

    def copy_json(value: Any) -> Any:
        return json.loads(json.dumps(value))

    run_record = {
        "RecordType": "run",
        "RunId": synthetic_config["runId"],
        "ProfileId": synthetic_config["profileId"],
        "FreshnessMarker": synthetic_config["freshnessMarker"],
        "ConfigSha256": synthetic_config_identity["sha256"],
        "OpeningSuiteSha256": synthetic_suite_identity["sha256"],
        "Seed": synthetic_config["seed"],
        "Match": _resolved_match_spec(synthetic_config["match"]),
        "Engines": [
            {
                "Id": "nnue-candidate",
                "Executable": "C:/synthetic/senpai.exe",
                "Arguments": "",
                "WorkingDirectory": "C:/synthetic",
                "Sha256": "4" * 64,
                "FileSize": 1,
                "LastWriteUtc": "2026-07-23T00:00:00Z",
                "UciName": "Senpai synthetic",
                "UciAuthor": "Synthetic",
                "Options": candidate_options,
                "ExternalAssets": [
                    {
                        "OptionName": "OmegaNNUEFile",
                        "Path": candidate_options["OmegaNNUEFile"],
                        "Sha256": "5" * 64,
                        "FileSize": 1,
                        "LastWriteUtc": "2026-07-23T00:00:00Z",
                    }
                ],
                "StartupDiagnostics": [
                    "info string Omega NNUE loaded: synthetic from C:/synthetic/network.onnx",
                    "info string Omega NNUE evaluation active",
                ],
                "OmegaNnueActiveVerified": True,
            },
            {
                "Id": "hce-control",
                "Executable": "C:/synthetic/senpai.exe",
                "Arguments": "",
                "WorkingDirectory": "C:/synthetic",
                "Sha256": "4" * 64,
                "FileSize": 1,
                "LastWriteUtc": "2026-07-23T00:00:00Z",
                "UciName": "Senpai synthetic",
                "UciAuthor": "Synthetic",
                "Options": control_options,
                "ExternalAssets": [],
                "StartupDiagnostics": [
                    "info string Omega NNUE disabled; handcrafted evaluation active"
                ],
                "OmegaNnueActiveVerified": False,
            },
        ],
    }
    honest_events: list[dict[str, Any]] = [run_record]
    ordered_openings = [
        suite["openings"][index]
        for index in _SHUFFLED_INDICES(ROOTS, synthetic_config["seed"])
    ]
    first_search_plies: set[int] = set()
    base_time = datetime(2026, 7, 23, tzinfo=timezone.utc)
    game_number = 0
    for opening in ordered_openings:
        opening_id = opening["id"]
        pair_id = f"{opening_id}-r001"
        depth = len(opening["moves"])
        first_search_plies.add(depth + 1)
        endpoint = opening["openConfirmationV2"]["finalOfen"]
        for suffix, white, black in (
            ("ab", "nnue-candidate", "hce-control"),
            ("ba", "hce-control", "nnue-candidate"),
        ):
            game_id = f"{pair_id}-{suffix}"
            started = base_time + timedelta(seconds=2 * game_number)
            finished = started + timedelta(seconds=1)
            start = {
                "RecordType": "gameStart",
                "GameId": game_id,
                "PairId": pair_id,
                "Attempt": 1,
                "StartedUtc": started.isoformat().replace("+00:00", "Z"),
                "OpeningId": opening_id,
                "WhiteEngineId": white,
                "BlackEngineId": black,
                "InitialOfen": opening["initialOfen"],
                "OpeningMoves": opening["moves"],
            }
            post = f"post-{game_number}"
            ply = {
                "RecordType": "ply",
                "GameId": game_id,
                "Attempt": 1,
                "Ply": depth + 1,
                "EngineId": white,
                "Color": "white",
                "PreOfen": endpoint,
                "PostOfen": post,
                "WhiteClockBeforeMs": 60_000,
                "BlackClockBeforeMs": 60_000,
                "WhiteClockAfterMs": 60_500,
                "BlackClockAfterMs": 60_000,
                "Search": {
                    "Command": "go wtime 60000 btime 60000 winc 1000 binc 1000",
                    "WallTimeMs": 500.0,
                    "DeadlineExceeded": False,
                },
            }
            result_text = "1-0" if white == "nnue-candidate" else "0-1"
            result = {
                "RecordType": "gameResult",
                "GameId": game_id,
                "PairId": pair_id,
                "Attempt": 1,
                "FinishedUtc": finished.isoformat().replace("+00:00", "Z"),
                "OpeningId": opening_id,
                "WhiteEngineId": white,
                "BlackEngineId": black,
                "Result": result_text,
                "Termination": "checkmate",
                "WinnerEngineId": "nnue-candidate",
                "ScoreA": 1.0,
                "Plies": depth + 1,
                "FinalOfen": post,
                "IllegalMoves": 0,
                "IllegalPvs": 0,
                "ProtocolFailures": 0,
                "TimeForfeits": 0,
            }
            honest_events.extend((start, ply, result))
            game_number += 1
    if first_search_plies != {5, 9, 13, 17}:
        raise AssertionError("honest practical opening-ply offsets changed")

    _POSITION_META = synthetic_meta
    try:
        honest_report = assess_events(
            suite=suite,
            events=honest_events,
            seed=synthetic_config["seed"],
            promotion_log_threshold=math.log(200.0),
            config=synthetic_config,
            suite_identity=synthetic_suite_identity,
            config_identity=synthetic_config_identity,
        )
        if (
            honest_report["progress"]["completePairs"] != ROOTS
            or honest_report["sequentialGate"]["decision"] != "promote"
            or honest_report["zeroSafetyFailures"] is not True
        ):
            raise AssertionError("authenticated 512-pair practical fixture did not promote")

        def expect_event_rejection(records: list[dict[str, Any]], label: str) -> None:
            try:
                assess_events(
                    suite=suite,
                    events=records,
                    seed=synthetic_config["seed"],
                    promotion_log_threshold=math.log(200.0),
                    config=synthetic_config,
                    suite_identity=synthetic_suite_identity,
                    config_identity=synthetic_config_identity,
                )
            except (KeyError, TypeError, ValueError):
                return
            raise AssertionError(f"forged practical events were accepted: {label}")

        mutations: list[tuple[str, list[dict[str, Any]]]] = []
        for label, record_index, field, value in (
            ("wrong GameId", 1, "GameId", "forged-game"),
            ("wrong PairId", 1, "PairId", "forged-pair"),
            ("wrong start OFEN", 1, "InitialOfen", "forged-ofen"),
            ("wrong raw Color", 2, "Color", "White"),
            ("wrong ScoreA", 3, "ScoreA", 0.0),
            ("wrong FinalOfen", 3, "FinalOfen", "forged-final"),
            ("unlinked ply", 2, "GameId", "forged-game"),
        ):
            changed = copy_json(honest_events)
            changed[record_index][field] = value
            mutations.append((label, changed))
        missing = copy_json(honest_events)
        del missing[2]
        mutations.append(("missing result-covered ply", missing))
        duplicate = copy_json(honest_events)
        duplicate.insert(3, copy_json([duplicate[2]])[0])
        mutations.append(("duplicate ply", duplicate))
        match_extra = copy_json(honest_events)
        match_extra[0]["Match"]["CandidateHint"] = "secret"
        mutations.append(("unknown MatchSpec field", match_extra))
        match_type = copy_json(honest_events)
        match_type[0]["Match"]["sequentialGate"]["nullElo"] = "15"
        mutations.append(("wrong MatchSpec numeric type", match_type))
        duplicate_engine = copy_json(honest_events)
        duplicate_engine[0]["Engines"][1] = copy_json(
            [duplicate_engine[0]["Engines"][0]]
        )[0]
        mutations.append(("duplicate engine ID", duplicate_engine))
        non_object_engine = copy_json(honest_events)
        non_object_engine[0]["Engines"][1] = "hce-control"
        mutations.append(("non-object engine entry", non_object_engine))
        extra_option = copy_json(honest_events)
        extra_option[0]["Engines"][0]["Options"]["CandidateHint"] = "secret"
        mutations.append(("extra effective engine option", extra_option))
        duplicate_asset = copy_json(honest_events)
        duplicate_asset[0]["Engines"][0]["ExternalAssets"].append(
            copy_json([duplicate_asset[0]["Engines"][0]["ExternalAssets"][0]])[0]
        )
        mutations.append(("duplicate candidate asset", duplicate_asset))
        divergent_asset_path = copy_json(honest_events)
        divergent_asset_path[0]["Engines"][0]["ExternalAssets"][0]["Path"] = (
            "C:/synthetic/other.onnx"
        )
        mutations.append(("divergent candidate asset path", divergent_asset_path))
        forged_candidate_diagnostic = copy_json(honest_events)
        forged_candidate_diagnostic[0]["Engines"][0]["StartupDiagnostics"][0] = (
            "info string Omega NNUE loaded: synthetic from C:/synthetic/other.onnx"
        )
        mutations.append(("candidate diagnostic wrong network", forged_candidate_diagnostic))
        forged_control_diagnostic = copy_json(honest_events)
        forged_control_diagnostic[0]["Engines"][1]["StartupDiagnostics"].insert(
            0, "info string Omega NNUE evaluation active"
        )
        mutations.append(("control diagnostic claims active NNUE", forged_control_diagnostic))
        wrong_executable = copy_json(honest_events)
        wrong_executable[0]["Engines"][1]["Executable"] = "C:/synthetic/other.exe"
        mutations.append(("control executable path differs", wrong_executable))
        for label, changed in mutations:
            expect_event_rejection(changed, label)

        failed = copy_json(honest_events[:4])
        successful = failed[2]
        result = failed[3]
        terminal = copy_json([successful])[0]
        terminal.update(
            {
                "Ply": successful["Ply"] + 1,
                "EngineId": failed[1]["BlackEngineId"],
                "Color": "black",
                "PreOfen": successful["PostOfen"],
                "PostOfen": None,
                "Error": "synthetic protocol failure",
                "WhiteClockBeforeMs": 60_500,
                "BlackClockBeforeMs": 60_000,
                "WhiteClockAfterMs": 60_500,
                "BlackClockAfterMs": 60_000,
                "Search": {
                    "Command": "go wtime 60500 btime 60000 winc 1000 binc 1000",
                    "WallTimeMs": 0.0,
                    "DeadlineExceeded": False,
                },
            }
        )
        result["ProtocolFailures"] = 1
        failed.insert(3, terminal)
        failed_report = assess_events(
            suite=suite,
            events=failed,
            seed=synthetic_config["seed"],
            promotion_log_threshold=math.log(200.0),
            config=synthetic_config,
            suite_identity=synthetic_suite_identity,
            config_identity=synthetic_config_identity,
        )
        if failed_report["sequentialGate"]["decision"] != "safety-fail":
            raise AssertionError("honest failed-terminal ply did not safety-fail")
        process_exit_terminal = copy_json(failed)
        process_exit_terminal[3]["Error"] = None
        process_exit_terminal[3]["Search"]["ProcessExited"] = True
        process_exit_report = assess_events(
            suite=suite,
            events=process_exit_terminal,
            seed=synthetic_config["seed"],
            promotion_log_threshold=math.log(200.0),
            config=synthetic_config,
            suite_identity=synthetic_suite_identity,
            config_identity=synthetic_config_identity,
        )
        if process_exit_report["sequentialGate"]["decision"] != "safety-fail":
            raise AssertionError("honest process-exit terminal ply did not safety-fail")
        malformed_terminal = copy_json(process_exit_terminal)
        malformed_terminal[3]["Search"]["ProcessExited"] = False
        expect_event_rejection(
            malformed_terminal,
            "failed terminal with forged null Error and no process exit",
        )
    finally:
        _POSITION_META = saved_position_meta

    clock_plies = [
        {
            "Color": "white",
            "WhiteClockBeforeMs": 60_000,
            "BlackClockBeforeMs": 60_000,
            "WhiteClockAfterMs": 60_500,
            "BlackClockAfterMs": 60_000,
            "Search": {
                "Command": "go wtime 60000 btime 60000 winc 1000 binc 1000",
                "WallTimeMs": 500.0,
                "DeadlineExceeded": False,
            },
        }
    ]
    if not _clock_audit(clock_plies)["passes"]:
        raise AssertionError("valid normal clock search failed its audit")
    wrong_case = json.loads(json.dumps(clock_plies))
    wrong_case[0]["Color"] = "White"
    if _clock_audit(wrong_case)["passes"]:
        raise AssertionError("noncanonical raw clock Color passed its audit")
    bad_clock = json.loads(json.dumps(clock_plies))
    bad_clock[0]["Search"]["Command"] = "go movetime 1000"
    if _clock_audit(bad_clock)["passes"]:
        raise AssertionError("non-clock practical search passed its audit")
    bad_wall = json.loads(json.dumps(clock_plies))
    bad_wall[0]["Search"]["WallTimeMs"] = True
    if _clock_audit(bad_wall)["passes"]:
        raise AssertionError("non-numeric clock telemetry passed its audit")

    starts = [
        {
            "GameId": "g1",
            "Attempt": 1,
            "StartedUtc": "2026-07-23T00:00:00Z",
        },
        {
            "GameId": "g2",
            "Attempt": 1,
            "StartedUtc": "2026-07-23T00:00:02Z",
        },
    ]
    results = [
        {
            "GameId": "g1",
            "Attempt": 1,
            "FinishedUtc": "2026-07-23T00:00:01Z",
        },
        {
            "GameId": "g2",
            "Attempt": 1,
            "FinishedUtc": "2026-07-23T00:00:03Z",
        },
    ]
    if not _serialization_audit(starts, results)["passes"]:
        raise AssertionError("serialized practical games failed their audit")
    overlapping = json.loads(json.dumps(starts))
    overlapping[1]["StartedUtc"] = "2026-07-23T00:00:00.500000Z"
    if _serialization_audit(overlapping, results)["passes"]:
        raise AssertionError("overlapping practical games passed their audit")
    attempt_one = math.log(200.0)
    winning = sequential_gate(
        [(f"pair-{index}", 1.0) for index in range(1, ROOTS + 1)],
        promotion_log_threshold=attempt_one,
    )
    if winning["decision"] != "promote":
        raise AssertionError("all-win practical sequence did not promote")
    losing = sequential_gate(
        [(f"pair-{index}", 0.0) for index in range(1, MINIMUM_PAIRS + 1)],
        promotion_log_threshold=attempt_one,
    )
    if losing["decision"] != "futility":
        raise AssertionError("all-loss practical sequence did not stop")
    if set(inspect_signature(select_endpoints)) != {
        "records", "forbidden_orbits", "used_orbits"
    }:
        raise AssertionError("candidate-blind selector interface changed")


def inspect_signature(callable_value: Any) -> tuple[str, ...]:
    import inspect

    return tuple(inspect.signature(callable_value).parameters)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != ["self-test"]:
        raise SystemExit(
            "usage: king_state_confirmation_practical_v2.py self-test"
        )
    self_test()
    print("Open-confirmation v2 practical module self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
