#!/usr/bin/env python3
"""Prepare and run a leakage-resistant deep HCE teacher corpus.

The expensive stage deliberately searches static roots rather than playing
HCE-vs-HCE games.  Two identical deterministic engines replay the same AB and
BA game, so a conventional self-play pair spends twice the CPU on the same
NNUE input.  Here each training pair contains genuinely different A and B
roots from either independent rules-only trajectories or a historical AB/BA
mate.  The roots have opposite side to move, share one split group, and may
not share a rule-preserving symmetry orbit.

``prepare`` is candidate blind.  Its primary roots come from deterministic
rules-only CoreChess A/B trajectories that never call an evaluator.  It may
also read completed non-NNUE historical AB/BA games, but never reads their
scores, results, best moves, or PVs when ranking roots.  It freezes the exact
current Senpai executable and emits a phase-balanced static-search suite plus
a harmless one-node OmegaMatch legality-validation configuration.

``run`` searches the frozen primary roots with that frozen executable, one
thread, OwnBook=false, UseOmegaNNUE=false, and one uniform fixed-node budget.
If a root pair cannot yield two valid exact-cp labels, it deterministically
advances into a small frozen reserve pool for that phase.  It is resumable and
appends one fsynced JSON record per attempt.

``finalize`` requires the exact target number of successful paired labels,
rechecks every frozen identity, and emits a trainer-ready JSONL whose whole
A/B source pair and every observed symmetry collision stay in one split group.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import itertools
import json
import os
from pathlib import Path
import queue
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterable, Iterator, Sequence

from omega_nnue import PIECE_INDEX, parse_ofen
from select_screen import input_keys, observable_ofen, phase_of


SCHEMA_VERSION = 2
PHASES = ("opening", "middlegame", "late", "endgame")
PHASE_PLY_WINDOWS = {
    "opening": (6, 48),
    "middlegame": (20, 140),
    "late": (40, 260),
    "endgame": (60, 400),
}
DEFAULT_SEED = 2026071802
# 4,096 paired units produce 8,192 genuinely distinct deep labels.  This is
# still a bootstrap-sized corpus for a multi-million-parameter transformer,
# but is large enough to test whether deep targets improve the residual model.
# The CLI scales higher without changing the grouping contract.
DEFAULT_ROOT_PAIRS = 4_096
DEFAULT_RESERVE_PAIRS_PER_PHASE = 64
DEFAULT_PREFLIGHT_EXTRA_PAIRS_PER_PHASE = 64
DEFAULT_MAX_PREFLIGHT_REJECTED_PAIRS = 32
DEFAULT_NODES = 100_000
SENPAI_ACCEPTANCE_COMMAND = "go depth 1"
EXPECTED_STATIC_HCE_SHA256 = (
    "b6bdd2310901319d85464a1cb570557dfceaee0ebfefba0"
    "563c9564728c27c04"
)
SAFE_ID = re.compile(r"[^a-z0-9]+")
OFEN_FIELDS = {
    "ofen",
    "preofen",
    "postofen",
    "initialofen",
    "finalofen",
}
HCE_OPTIONS = {
    "Threads": "1",
    "Hash": "128",
    "Ponder": "false",
    "OwnBook": "false",
    "UCI_Chess960": "false",
    "UCI_Variant": "omega",
    "UseOmegaNNUE": "false",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    path = _resolve(path)
    stat = path.stat()
    return {
        "path": str(path),
        "bytes": stat.st_size,
        "sha256": _sha256(path),
    }


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _atomic_json(path: Path, value: Any) -> None:
    payload = (
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    _atomic_bytes(path, payload)


def _distribution(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "p10": percentile(0.10),
        "p25": percentile(0.25),
        "median": percentile(0.50),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "maximum": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


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
                raise ValueError(f"{path}:{line_number}: not a JSON object")
            yield line_number, value


def _field(value: dict[str, Any], name: str, default: Any = None) -> Any:
    for key, item in value.items():
        if key.lower() == name.lower():
            return item
    return default


def _safe_id(text: str) -> str:
    safe = SAFE_ID.sub("-", text.lower()).strip("-")
    return safe[:72] or "root"


def _leakage_keys(ofen: str) -> tuple[str, str, tuple[str, ...]]:
    """Return conservative exact and rule-preserving symmetry keys.

    File reflection is not treated as legal while castling rights remain:
    Omega castling is tied to the original f-file king geometry.  Rank
    reflection plus colour/side-to-move swap is already identical under the
    side-relative NNUE input.  Ignoring en-passant and clocks here is
    deliberately conservative: it may keep extra positions together, never
    split a related family.
    """

    observable = observable_ofen(ofen)
    identity, orbit, signatures = input_keys(observable)
    if observable.split()[2] != "-":
        return identity, identity, (identity,)
    return identity, orbit, signatures


def _position_meta(ofen: str) -> tuple[str, str, str, tuple[str, ...]]:
    pieces, stm, _ = parse_ofen(ofen)
    phase = phase_of(len(pieces))
    if phase is None:
        raise ValueError("position has fewer than five pieces")
    identity, orbit, signatures = _leakage_keys(ofen)
    return phase, stm, orbit, signatures


def _walk_ofens(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in OFEN_FIELDS and isinstance(item, str):
                yield item
            yield from _walk_ofens(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_ofens(item)


def _exclusion_files(roots: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        root = _resolve(root)
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() in {".json", ".jsonl"}:
                files.add(root)
            continue
        files.update(root.rglob("*.json"))
        files.update(root.rglob("*.jsonl"))
    return sorted(files, key=lambda item: str(item).lower())


def _read_forbidden(roots: Iterable[Path]) -> tuple[set[str], list[dict[str, Any]], int]:
    signatures: set[str] = set()
    pins: list[dict[str, Any]] = []
    position_count = 0
    for path in _exclusion_files(roots):
        before = _identity(path)
        values: Iterable[Any]
        if path.suffix.lower() == ".jsonl":
            values = (record for _, record in _jsonl(path))
        else:
            values = [json.loads(path.read_text(encoding="utf-8"))]
        for value in values:
            for ofen in _walk_ofens(value):
                try:
                    _, _, orbit_signatures = _leakage_keys(ofen)
                except (TypeError, ValueError):
                    continue
                signatures.update(orbit_signatures)
                position_count += 1
        after = _identity(path)
        if before != after:
            raise ValueError(f"exclusion artifact changed while read: {path}")
        pins.append(after)
    return signatures, pins, position_count


def _discover_events(
    explicit: Iterable[Path],
    roots: Iterable[Path],
    patterns: Sequence[str],
    output_dir: Path,
) -> list[Path]:
    found = {_resolve(path) for path in explicit}
    for root in roots:
        root = _resolve(root)
        if root.is_file() and root.name.lower() == "events.jsonl":
            found.add(root)
        elif root.exists():
            found.update(root.rglob("events.jsonl"))
    output_dir = _resolve(output_dir)
    result: list[Path] = []
    for path in sorted(found, key=lambda item: str(item).lower()):
        normalized = path.as_posix().lower()
        if path == output_dir or output_dir in path.parents:
            continue
        if any(fnmatch.fnmatch(normalized, pattern.lower()) for pattern in patterns):
            continue
        result.append(path)
    if not result:
        raise ValueError("no candidate-blind historical events.jsonl files found")
    return result


def _engine_is_candidate_blind(run: dict[str, Any]) -> bool:
    engines = _field(run, "engines", [])
    if not isinstance(engines, list) or not engines:
        return False
    for engine in engines:
        if not isinstance(engine, dict):
            return False
        options = _field(engine, "options", {})
        if not isinstance(options, dict):
            return False
        lowered = {str(key).lower(): str(value).strip().lower()
                   for key, value in options.items()}
        if lowered.get("useomegannue", "false") == "true":
            return False
        configured = lowered.get("omegannuefile", "")
        if configured not in {"", "<empty>", "none"}:
            return False
    return True


def _safety_clean(record: dict[str, Any]) -> bool:
    for name in (
        "illegalMoves",
        "illegalPvs",
        "protocolFailures",
        "timeForfeits",
    ):
        value = _field(record, name, 0)
        try:
            if int(value or 0) != 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


@dataclass(frozen=True)
class SourcePosition:
    source_index: int
    source_sha256: str
    run_id: str
    pair_id: str
    game_id: str
    opening_id: str
    flavor: str
    ply: int
    ofen: str
    phase: str
    stm: str
    orbit: str
    orbit_signatures: tuple[str, ...]


@dataclass(frozen=True)
class RootPair:
    source_unit: str
    opening_family: str
    phase: str
    ab: SourcePosition
    ba: SourcePosition
    rank: str

    @property
    def orbits(self) -> frozenset[str]:
        return frozenset((self.ab.orbit, self.ba.orbit))


def _parse_source(
    path: Path,
    source_index: int,
    forbidden: set[str],
    seed: int,
    candidate_pairs_per_trajectory_phase: int,
) -> tuple[list[RootPair], dict[str, Any]]:
    pin_before = _identity(path)
    run: dict[str, Any] | None = None
    starts: dict[tuple[str, int], dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    plies: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    physical = 0
    ignored_evaluative = Counter()
    rejected = Counter()
    for _, record in _jsonl(path):
        physical += 1
        record_type = str(_field(record, "recordType", "")).lower()
        if record_type == "run":
            if run is not None:
                raise ValueError(f"{path}: multiple run records")
            run = record
        elif record_type == "gamestart":
            game_id = str(_field(record, "gameId", ""))
            attempt = int(_field(record, "attempt", 1))
            starts[(game_id, attempt)] = record
        elif record_type == "ply":
            game_id = str(_field(record, "gameId", ""))
            attempt = int(_field(record, "attempt", 1))
            plies[(game_id, attempt)].append(record)
            for key in ("Search", "BestMove", "San", "Pv", "FinalInfo"):
                if _field(record, key) is not None:
                    ignored_evaluative[key] += 1
        elif record_type == "gameresult":
            game_id = str(_field(record, "gameId", ""))
            attempt = int(_field(record, "attempt", 1))
            previous = results.get(game_id)
            if previous is None or attempt >= int(_field(previous, "attempt", 1)):
                results[game_id] = record
    pin_after = _identity(path)
    if pin_before != pin_after:
        raise ValueError(f"source log changed while read: {path}")
    if run is None or not _engine_is_candidate_blind(run):
        return [], {
            "events": pin_after,
            "eligible": False,
            "reason": "missing-run-or-NNUE-enabled",
            "records": physical,
        }
    run_id = str(_field(run, "runId", path.parent.name))

    complete: dict[str, tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = {}
    for game_id, result in results.items():
        attempt = int(_field(result, "attempt", 1))
        start = starts.get((game_id, attempt))
        game_plies = plies.get((game_id, attempt), [])
        if start is None or not game_plies or not _safety_clean(result):
            continue
        if any(_field(ply, "error") not in {None, ""} for ply in game_plies):
            continue
        complete[game_id] = (start, result, game_plies)

    games_by_pair: dict[str, dict[str, tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]]] = defaultdict(dict)
    for game_id, game in complete.items():
        start = game[0]
        pair_id = str(_field(start, "pairId", "")).strip()
        lowered = game_id.lower()
        flavor = "ab" if lowered.endswith("-ab") else "ba" if lowered.endswith("-ba") else ""
        if pair_id and flavor:
            games_by_pair[pair_id][flavor] = game

    candidates: list[RootPair] = []
    for pair_id, flavors in games_by_pair.items():
        if set(flavors) != {"ab", "ba"}:
            continue
        ab_start, _, ab_plies = flavors["ab"]
        ba_start, _, ba_plies = flavors["ba"]
        ab_white = str(_field(ab_start, "whiteEngineId", ""))
        ab_black = str(_field(ab_start, "blackEngineId", ""))
        ba_white = str(_field(ba_start, "whiteEngineId", ""))
        ba_black = str(_field(ba_start, "blackEngineId", ""))
        if not (
            ab_white
            and ab_black
            and ab_white.lower() == ba_black.lower()
            and ab_black.lower() == ba_white.lower()
        ):
            continue
        opening_id = str(_field(ab_start, "openingId", ""))
        launch_orbits: list[str] = []
        for game_plies in (ab_plies, ba_plies):
            if not game_plies:
                continue
            launch = _field(game_plies[0], "preOfen")
            if isinstance(launch, str):
                try:
                    launch_orbits.append(_leakage_keys(launch)[1])
                except (TypeError, ValueError):
                    pass
        launch_payload = "\n".join(sorted(set(launch_orbits))) or opening_id
        opening_family = "launch-orbit:" + hashlib.sha256(
            launch_payload.encode("utf-8")
        ).hexdigest()
        by_flavor_phase: dict[tuple[str, str], list[SourcePosition]] = defaultdict(list)
        for flavor, game_plies in (("ab", ab_plies), ("ba", ba_plies)):
            game_id = str(_field(flavors[flavor][0], "gameId", ""))
            for ply in game_plies:
                ofen = _field(ply, "preOfen")
                if not isinstance(ofen, str):
                    continue
                try:
                    phase, stm, orbit, orbit_signatures = _position_meta(ofen)
                    pieces, parsed_stm, _ = parse_ofen(ofen)
                    halfmove = int(ofen.split()[4])
                    ply_number = int(_field(ply, "ply", 0))
                except (TypeError, ValueError, IndexError):
                    rejected["invalid-ofen-or-ply"] += 1
                    continue
                white_pieces = sum(side == 0 for _, side, _ in pieces)
                black_pieces = len(pieces) - white_pieces
                window = PHASE_PLY_WINDOWS[phase]
                if stm != parsed_stm:
                    rejected["side-to-move-mismatch"] += 1
                    continue
                if (
                    len(pieces) < 7
                    or white_pieces < 2
                    or black_pieces < 2
                    or halfmove >= 90
                    or not window[0] <= ply_number <= window[1]
                ):
                    rejected["unsafe-material-clock-or-phase-window"] += 1
                    continue
                if forbidden.intersection(orbit_signatures):
                    rejected["forbidden-orbit"] += 1
                    continue
                by_flavor_phase[(flavor, phase)].append(
                    SourcePosition(
                        source_index=source_index,
                        source_sha256=pin_after["sha256"],
                        run_id=run_id,
                        pair_id=pair_id,
                        game_id=game_id,
                        opening_id=opening_id,
                        flavor=flavor,
                        ply=ply_number,
                        ofen=ofen,
                        phase=phase,
                        stm=stm,
                        orbit=orbit,
                        orbit_signatures=orbit_signatures,
                    )
                )
        source_unit = f"{pin_after['sha256']}:{run_id}:{pair_id}"
        for phase in PHASES:
            possible: list[tuple[str, SourcePosition, SourcePosition]] = []
            for ab in by_flavor_phase.get(("ab", phase), []):
                for ba in by_flavor_phase.get(("ba", phase), []):
                    if ab.stm == ba.stm or ab.orbit == ba.orbit:
                        continue
                    rank_payload = (
                        f"omega-deep-hce-v2\0{seed}\0{source_unit}\0{phase}\0"
                        f"{ab.game_id}\0{ab.ply}\0{ab.orbit}\0"
                        f"{ba.game_id}\0{ba.ply}\0{ba.orbit}"
                    )
                    rank = hashlib.sha256(rank_payload.encode("utf-8")).hexdigest()
                    possible.append((rank, ab, ba))
            used_ab_orbits: set[str] = set()
            used_ba_orbits: set[str] = set()
            accepted = 0
            for rank, ab, ba in sorted(possible, key=lambda item: item[0]):
                if (
                    ab.orbit in used_ab_orbits
                    or ba.orbit in used_ba_orbits
                ):
                    continue
                candidates.append(
                    RootPair(
                        source_unit=source_unit,
                        opening_family=opening_family,
                        phase=phase,
                        ab=ab,
                        ba=ba,
                        rank=rank,
                    )
                )
                used_ab_orbits.add(ab.orbit)
                used_ba_orbits.add(ba.orbit)
                accepted += 1
                if accepted >= candidate_pairs_per_trajectory_phase:
                    break
    return candidates, {
        "events": pin_after,
        "eligible": True,
        "runId": run_id,
        "records": physical,
        "completeGames": len(complete),
        "completePairs": len(games_by_pair),
        "candidateRootPairs": len(candidates),
        "rejectedRoots": dict(sorted(rejected.items())),
        "evaluativeFieldsPresentButIgnored": dict(ignored_evaluative),
    }


def _parse_rules_only_roots(
    path: Path,
    source_index: int,
    forbidden: set[str],
    seed: int,
) -> tuple[list[RootPair], dict[str, Any]]:
    """Load deterministic legal A/B trajectories emitted by OmegaRootSampler."""

    pin_before = _identity(path)
    by_pair_flavor_phase: dict[
        tuple[str, str, str], list[SourcePosition]
    ] = defaultdict(list)
    trajectory_ids: set[str] = set()
    rejected = Counter()
    generator_seed: str | None = None
    records = 0
    for line_number, record in _jsonl(path):
        records += 1
        if record.get("kind") != "omega-rules-only-random-root":
            rejected["wrong-kind"] += 1
            continue
        current_seed = str(record.get("generatorSeed", ""))
        if generator_seed is None:
            generator_seed = current_seed
        elif current_seed != generator_seed:
            raise ValueError(f"{path}:{line_number}: mixed generator seeds")
        pair_id = str(record.get("trajectoryPairId", "")).strip()
        trajectory_id = str(record.get("trajectoryId", "")).strip()
        flavor = str(record.get("flavor", "")).lower()
        ofen = record.get("ofen")
        if not pair_id or not trajectory_id or flavor not in {"ab", "ba"}:
            rejected["bad-provenance"] += 1
            continue
        if not isinstance(ofen, str):
            rejected["missing-ofen"] += 1
            continue
        try:
            phase, stm, orbit, orbit_signatures = _position_meta(ofen)
            pieces, parsed_stm, _ = parse_ofen(ofen)
            halfmove = int(ofen.split()[4])
        except (TypeError, ValueError, IndexError):
            rejected["invalid-ofen"] += 1
            continue
        if (
            phase != str(record.get("phase", ""))
            or stm != parsed_stm
            or stm != str(record.get("sideToMove", ""))
        ):
            rejected["metadata-mismatch"] += 1
            continue
        white_pieces = sum(side == 0 for _, side, _ in pieces)
        black_pieces = len(pieces) - white_pieces
        ply_number = int(record.get("ply", 0))
        window = PHASE_PLY_WINDOWS[phase]
        if (
            len(pieces) < 7
            or white_pieces < 2
            or black_pieces < 2
            or halfmove >= 90
            or not window[0] <= ply_number <= window[1]
        ):
            rejected["unsafe-material-or-clock"] += 1
            continue
        if forbidden.intersection(orbit_signatures):
            rejected["forbidden-orbit"] += 1
            continue
        trajectory_ids.add(trajectory_id)
        by_pair_flavor_phase[(pair_id, flavor, phase)].append(
            SourcePosition(
                source_index=source_index,
                source_sha256=pin_before["sha256"],
                run_id="rules-only-random-v1",
                pair_id=pair_id,
                game_id=trajectory_id,
                opening_id=pair_id,
                flavor=flavor,
                ply=ply_number,
                ofen=ofen,
                phase=phase,
                stm=stm,
                orbit=orbit,
                orbit_signatures=orbit_signatures,
            )
        )
    pin_after = _identity(path)
    if pin_before != pin_after:
        raise ValueError(f"rules-only root source changed while read: {path}")

    pair_ids = sorted({key[0] for key in by_pair_flavor_phase})
    candidates: list[RootPair] = []
    for pair_id in pair_ids:
        source_unit = f"rules-only:{pin_after['sha256']}:{pair_id}"
        opening_family = "rules-only-pair:" + hashlib.sha256(
            pair_id.encode("utf-8")
        ).hexdigest()
        for phase in PHASES:
            possible: list[tuple[str, SourcePosition, SourcePosition]] = []
            for ab in by_pair_flavor_phase.get((pair_id, "ab", phase), []):
                for ba in by_pair_flavor_phase.get((pair_id, "ba", phase), []):
                    if ab.stm == ba.stm or ab.orbit == ba.orbit:
                        continue
                    payload = (
                        f"omega-deep-hce-v2-rules\0{seed}\0{source_unit}\0"
                        f"{phase}\0{ab.ply}\0{ab.orbit}\0{ba.ply}\0{ba.orbit}"
                    )
                    rank = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                    possible.append((rank, ab, ba))
            if possible:
                rank, ab, ba = min(possible, key=lambda item: item[0])
                candidates.append(
                    RootPair(
                        source_unit=source_unit,
                        opening_family=opening_family,
                        phase=phase,
                        ab=ab,
                        ba=ba,
                        rank=rank,
                    )
                )
    return candidates, {
        "roots": pin_after,
        "eligible": True,
        "kind": "rules-only-independent-a-b-trajectories",
        "generatorSeed": generator_seed,
        "records": records,
        "trajectoryPairs": len(pair_ids),
        "trajectories": len(trajectory_ids),
        "candidateRootPairs": len(candidates),
        "rejected": dict(sorted(rejected.items())),
        "evaluativeFieldsRead": 0,
    }


def _select_pairs(
    candidates: Sequence[RootPair],
    count: int,
    *,
    max_pairs_per_trajectory: int,
    max_pairs_per_split_group: int,
) -> list[RootPair]:
    if count <= 0 or count % len(PHASES) != 0:
        raise ValueError(f"--root-pairs must be positive and divisible by {len(PHASES)}")
    quota = count // len(PHASES)
    if max_pairs_per_trajectory <= 0 or max_pairs_per_split_group <= 0:
        raise ValueError("selection caps must be positive")
    by_phase: dict[str, list[RootPair]] = {
        phase: sorted(
            (item for item in candidates if item.phase == phase),
            key=lambda item: item.rank,
        )
        for phase in PHASES
    }
    scarcity = tuple(sorted(
        PHASES,
        key=lambda phase: (len(by_phase[phase]), PHASES.index(phase)),
    ))

    # The trajectory and launch-family caps cross phase boundaries.  A single
    # greedy phase order can consume capacity needed by the last phase even
    # when a valid balanced assignment exists.  Try all 24 precommitted phase
    # orders; this is still candidate blind because ordering and ranking depend
    # only on provenance, phase, and the fixed seed.
    preferred_orders = [
        tuple(PHASES),
        tuple(reversed(PHASES)),
        scarcity,
    ]
    phase_orders: list[tuple[str, ...]] = []
    for order in preferred_orders + list(itertools.permutations(PHASES)):
        if order not in phase_orders:
            phase_orders.append(order)
    failures: list[dict[str, Any]] = []
    for order in phase_orders:
        selected: list[RootPair] = []
        used_orbits: set[str] = set()
        trajectory_counts = Counter()
        split_group_counts = Counter()
        completed = Counter()
        failed = False
        for phase in order:
            while completed[phase] < quota:
                usable = [
                    candidate
                    for candidate in by_phase[phase]
                    if candidate.orbits.isdisjoint(used_orbits)
                    and trajectory_counts[candidate.source_unit]
                    < max_pairs_per_trajectory
                    and split_group_counts[candidate.opening_family]
                    < max_pairs_per_split_group
                ]
                if not usable:
                    failures.append(
                        {
                            "order": order,
                            "failedPhase": phase,
                            "completed": dict(completed),
                        }
                    )
                    failed = True
                    break
                chosen = min(
                    usable,
                    key=lambda item: (
                        trajectory_counts[item.source_unit],
                        split_group_counts[item.opening_family],
                        item.rank,
                    ),
                )
                selected.append(chosen)
                used_orbits.update(chosen.orbits)
                trajectory_counts[chosen.source_unit] += 1
                split_group_counts[chosen.opening_family] += 1
                completed[phase] += 1
            if failed:
                break
        if not failed:
            return sorted(
                selected,
                key=lambda item: (PHASES.index(item.phase), item.rank),
            )
    offered = {phase: len(by_phase[phase]) for phase in PHASES}
    best = max(
        failures,
        key=lambda item: sum(item["completed"].values()),
        default={},
    )
    raise ValueError(
        f"cannot fill balanced quota {quota} per phase under declared caps; "
        f"offered={offered}; trajectory cap={max_pairs_per_trajectory}; "
        f"split-group cap={max_pairs_per_split_group}; best attempt={best}"
    )


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

    files = sorted(
        (
            path for path in root.rglob("*")
            if path.is_file() and included(path)
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    members = []
    canonical = bytearray()
    for path in files:
        identity = _identity(path)
        relative = path.relative_to(root).as_posix()
        members.append(
            {
                "relativePath": relative,
                "bytes": identity["bytes"],
                "sha256": identity["sha256"],
            }
        )
        canonical.extend(
            f"{relative}\t{identity['bytes']}\t{identity['sha256']}\n".encode(
                "utf-8"
            )
        )
    if not any(
        item["relativePath"] == assembly.relative_to(root).as_posix()
        for item in members
    ):
        raise ValueError("OmegaMatch.dll is absent from runtime bundle")
    return {
        "root": str(root),
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": members,
    }


def _default_paths() -> dict[str, Path]:
    repo = Path(__file__).resolve().parents[2]
    workspace = repo.parent
    return {
        "repo": repo,
        "workspace": workspace,
        "engine": repo / "build-msvc" / "senpai-omega-nnue.exe",
        "harness": (
            workspace / "corechess-arena" / "Tools" / "OmegaMatch"
            / "bin" / "Debug" / "net10.0" / "OmegaMatch.dll"
        ),
        "source_root": workspace / "match-runs" / "output",
        "output_dir": repo / "build-msvc" / "data-generation" / "deep-hce-v2",
        "experimental": repo / "build-msvc" / "experimental-networks",
        "confirmation": repo / "build-msvc" / "confirmation",
        "screen_roots": repo / "validation" / "omega-nnue-screen-roots-v1.json",
        "dotnet": workspace / ".dotnet" / "dotnet.exe",
        "sampler_project": (
            repo / "tools" / "omega_nnue" / "OmegaRootSampler"
            / "OmegaRootSampler.csproj"
        ),
        "sampled_roots": (
            repo / "build-msvc" / "data-generation"
            / "deep-hce-v2-random-roots.jsonl"
        ),
    }


def _default_forbidden_roots(defaults: dict[str, Path]) -> list[Path]:
    """Discover every prior NNUE training/screen/confirmation input.

    The resolved file inventory is pinned in the freeze, and `_verify_lock`
    rediscovers the inventory before either labeling or finalization.  Sorted
    discovery keeps the command-line default deterministic.
    """

    roots = [
        defaults["experimental"],
        defaults["confirmation"],
        defaults["screen_roots"],
    ]
    match_output = defaults["source_root"]
    if match_output.is_dir():
        roots.extend(
            path
            for path in sorted(
                match_output.iterdir(), key=lambda item: item.name.lower()
            )
            if path.is_dir()
            and (
                path.name.lower().startswith("screen-omega-nnue-")
                or path.name.lower().startswith("confirm-omega-nnue-")
            )
        )
    validation = defaults["repo"] / "validation"
    if validation.is_dir():
        roots.extend(sorted(validation.glob("omega-nnue-screen*.json")))
        roots.extend(sorted(validation.glob("omega-nnue-confirmation*.json")))
    return list(dict.fromkeys(_resolve(path) for path in roots))


def _sample(args: argparse.Namespace) -> None:
    dotnet = _resolve(args.dotnet)
    project = _resolve(args.project)
    output = _resolve(args.output)
    if output.exists() and not args.force:
        raise ValueError(f"refusing to overwrite existing sampled roots: {output}")
    if not dotnet.is_file():
        raise FileNotFoundError(dotnet)
    if not project.is_file():
        raise FileNotFoundError(project)
    subprocess.run(
        [
            str(dotnet),
            "build",
            str(project),
            "-c",
            "Release",
            "--nologo",
        ],
        check=True,
    )
    sampler = (
        project.parent / "bin" / "Release" / "net10.0"
        / "OmegaRootSampler.dll"
    )
    subprocess.run(
        [
            str(dotnet),
            str(sampler),
            "--output",
            str(output),
            "--seed",
            str(args.seed),
            "--trajectory-pairs",
            str(args.trajectory_pairs),
            "--max-plies",
            str(args.max_plies),
            "--positions-per-phase-side",
            str(args.positions_per_phase_side),
            "--capture-percent",
            str(args.capture_percent),
        ],
        check=True,
    )
    manifest_path = Path(str(output) + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        str(manifest.get("policy", {}).get("seed")) != str(args.seed)
        or int(manifest.get("policy", {}).get("trajectoryPairs", 0))
        != args.trajectory_pairs
        or int(manifest.get("policy", {}).get("terminalRootsEmitted", -1)) != 0
    ):
        raise ValueError("root sampler manifest does not match requested policy")
    freeze_path = Path(str(output) + ".freeze.json")
    _atomic_json(
        freeze_path,
        {
            "schemaVersion": 1,
            "kind": "omega-rules-only-random-root-freeze",
            "createdUtc": _utc_now(),
            "seed": args.seed,
            "policy": {
                "trajectoryPairs": args.trajectory_pairs,
                "maxPlies": args.max_plies,
                "positionsPerPhaseSide": args.positions_per_phase_side,
                "capturePercent": args.capture_percent,
            },
            "output": _identity(output),
            "manifest": _identity(manifest_path),
            "samplerAssembly": _identity(sampler),
            "samplerProgram": _identity(project.parent / "Program.cs"),
            "samplerProject": _identity(project),
            "dotnetHost": _identity(dotnet),
            "runtime": manifest.get("runtime"),
        },
    )
    print(f"Rules-only root freeze: {freeze_path}")


def _prepare(args: argparse.Namespace) -> Path:
    output_dir = _resolve(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty freeze directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    engine_source = _resolve(args.engine)
    harness = _resolve(args.harness)
    if not engine_source.is_file():
        raise FileNotFoundError(engine_source)
    if not harness.is_file():
        raise FileNotFoundError(harness)
    if args.nodes < 100_000:
        raise ValueError("--nodes must be at least 100000 for the v2 deep corpus")
    if args.reserve_pairs_per_phase < 1:
        raise ValueError("--reserve-pairs-per-phase must be positive")
    if args.preflight_extra_pairs_per_phase < 1:
        raise ValueError("--preflight-extra-pairs-per-phase must be positive")
    if args.max_preflight_rejected_pairs < 0:
        raise ValueError("--max-preflight-rejected-pairs cannot be negative")

    forbidden, exclusion_pins, forbidden_positions = _read_forbidden(
        args.forbidden_root
    )
    source_paths = _discover_events(
        args.source, args.source_root, args.exclude_source_pattern, output_dir
    )
    all_candidates: list[RootPair] = []
    source_audit: list[dict[str, Any]] = []
    for index, path in enumerate(source_paths):
        candidates, audit = _parse_source(
            path,
            index,
            forbidden,
            args.seed,
            args.candidate_pairs_per_trajectory_phase,
        )
        all_candidates.extend(candidates)
        source_audit.append(audit)
    rules_source_audit: list[dict[str, Any]] = []
    next_source_index = len(source_paths)
    for offset, path in enumerate(args.rules_only_root):
        path = _resolve(path)
        if not path.is_file():
            raise FileNotFoundError(
                f"rules-only root file is missing; run the sample command: {path}"
            )
        candidates, audit = _parse_rules_only_roots(
            path,
            next_source_index + offset,
            forbidden,
            args.seed,
        )
        if str(audit.get("generatorSeed")) != str(args.seed):
            raise ValueError(
                f"rules-only roots use seed {audit.get('generatorSeed')}, "
                f"selection requires {args.seed}: {path}"
            )
        companions = []
        for suffix in (".manifest.json", ".freeze.json"):
            companion = Path(str(path) + suffix)
            if not companion.is_file():
                raise FileNotFoundError(
                    f"rules-only provenance companion is missing: {companion}"
                )
            companions.append(_identity(companion))
        audit["provenanceCompanions"] = companions
        all_candidates.extend(candidates)
        rules_source_audit.append(audit)
    candidate_pair_count = (
        args.root_pairs + args.reserve_pairs_per_phase * len(PHASES)
    )
    preflight_pair_count = candidate_pair_count + (
        args.preflight_extra_pairs_per_phase * len(PHASES)
    )
    preflight_pairs = _select_pairs(
        all_candidates,
        preflight_pair_count,
        max_pairs_per_trajectory=args.max_pairs_per_trajectory,
        max_pairs_per_split_group=args.max_pairs_per_split_group,
    )

    artifacts = output_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    frozen_engine = artifacts / "senpai-hce-v2.exe"
    shutil.copy2(engine_source, frozen_engine)
    source_engine_identity = _identity(engine_source)
    frozen_engine_identity = _identity(frozen_engine)
    if source_engine_identity["sha256"] != frozen_engine_identity["sha256"]:
        raise AssertionError("frozen HCE executable differs from its source")

    preflight_roots: list[dict[str, Any]] = []
    preflight_pair_roots: list[tuple[RootPair, list[dict[str, Any]]]] = []
    for pair in preflight_pairs:
        roots = []
        for source in (pair.ab, pair.ba):
            root_id = "preflight-" + hashlib.sha256(
                f"{pair.rank}\0{source.flavor}\0{source.orbit}".encode("utf-8")
            ).hexdigest()
            root = {
                "id": root_id,
                "initialOfen": source.ofen,
                "sourcePosition": source,
            }
            roots.append(root)
            preflight_roots.append(root)
        preflight_pair_roots.append((pair, roots))
    accepted_preflight_ids, senpai_preflight = _validate_senpai_acceptance(
        frozen_engine,
        preflight_roots,
        timeout_seconds=args.acceptance_timeout_seconds,
    )
    rejected_reasons = {
        item["rootId"]: item["reason"]
        for item in senpai_preflight["rejectedRoots"]
    }
    rejected_pairs: list[dict[str, Any]] = []
    accepted_pairs_by_phase: dict[str, list[RootPair]] = {
        phase: [] for phase in PHASES
    }
    for pair, roots in preflight_pair_roots:
        rejected = [
            root for root in roots if root["id"] not in accepted_preflight_ids
        ]
        if not rejected:
            accepted_pairs_by_phase[pair.phase].append(pair)
            continue
        rejected_entries = []
        directly_rejected_ids = {root["id"] for root in rejected}
        for root in roots:
            source = root["sourcePosition"]
            direct_rejection = root["id"] in directly_rejected_ids
            reason = (
                rejected_reasons[root["id"]]
                if direct_rejection
                else "whole-pair companion excluded after paired-root rejection"
            )
            rejected_entries.append(
                {
                    "rootId": root["id"],
                    "directProtocolRejection": direct_rejection,
                    "ofenSha256": hashlib.sha256(
                        source.ofen.encode("utf-8")
                    ).hexdigest(),
                    "reason": reason,
                    "reasonSha256": hashlib.sha256(
                        reason.encode("utf-8")
                    ).hexdigest(),
                    "sourceKind": (
                        "rules-only"
                        if source.run_id == "rules-only-random-v1"
                        else "historical"
                    ),
                    "runId": source.run_id,
                    "pairId": source.pair_id,
                    "gameId": source.game_id,
                    "flavor": source.flavor,
                    "ply": source.ply,
                }
            )
        rejected_pairs.append(
            {
                "phase": pair.phase,
                "pairRank": pair.rank,
                "roots": rejected_entries,
            }
        )
    if len(rejected_pairs) > args.max_preflight_rejected_pairs:
        raise ValueError(
            "widespread Senpai/CoreChess protocol disagreement: "
            f"{len(rejected_pairs)} pairs rejected, sanity cap is "
            f"{args.max_preflight_rejected_pairs}"
        )
    selected: list[RootPair] = []
    required_pairs_per_phase = candidate_pair_count // len(PHASES)
    for phase in PHASES:
        accepted_phase = accepted_pairs_by_phase[phase]
        if len(accepted_phase) < required_pairs_per_phase:
            raise ValueError(
                f"Senpai preflight leaves only {len(accepted_phase)}/"
                f"{required_pairs_per_phase} required candidate pairs in {phase}"
            )
        selected.extend(accepted_phase[:required_pairs_per_phase])
    senpai_preflight.update(
        {
            "pairsProbed": len(preflight_pairs),
            "pairsRejected": len(rejected_pairs),
            "maximumRejectedPairs": args.max_preflight_rejected_pairs,
            "extraPairsPerPhase": args.preflight_extra_pairs_per_phase,
            "finalPairsAccepted": len(selected),
            "finalPositionsAccepted": len(selected) * 2,
            "finalPoolContainsRejectedRoot": False,
            "rejectedPairs": rejected_pairs,
        }
    )

    positions: list[dict[str, Any]] = []
    openings: list[dict[str, Any]] = []
    pair_entries: list[dict[str, Any]] = []
    phase_counts = Counter()
    stm_counts = Counter()
    primary_phase_counts = Counter()
    primary_stm_counts = Counter()
    phase_pair_ordinals = Counter()
    target_pairs_per_phase = args.root_pairs // len(PHASES)
    for pair_number, pair in enumerate(selected, 1):
        # Repeated positions from the same launch family, even across
        # experimental runs, must never land in different train/validation
        # splits.  The group is therefore intentionally broader than one
        # selected root pair.
        group_payload = f"omega-deep-hce-v2-group\0{pair.opening_family}"
        group_digest = hashlib.sha256(group_payload.encode("utf-8")).hexdigest()
        group_id = f"deep-hce-v2-pair:{group_digest}"
        phase_pair_ordinals[pair.phase] += 1
        phase_pair_ordinal = phase_pair_ordinals[pair.phase]
        candidate_role = (
            "primary"
            if phase_pair_ordinal <= target_pairs_per_phase
            else "reserve"
        )
        pair_id = f"dh2-{pair.phase[:2]}-{pair_number:04d}-{group_digest[:10]}"
        root_ids: list[str] = []
        for source in (pair.ab, pair.ba):
            root_id = (
                f"{pair_id}-{source.flavor}-{source.orbit[:10]}"
            )
            phase_counts[source.phase] += 1
            stm_counts[source.stm] += 1
            if candidate_role == "primary":
                primary_phase_counts[source.phase] += 1
                primary_stm_counts[source.stm] += 1
            entry = {
                "id": root_id,
                "pairId": pair_id,
                "splitGroup": group_id,
                "phase": source.phase,
                "sideToMove": source.stm,
                "initialOfen": source.ofen,
                "symmetryOrbitKey": source.orbit,
                "candidateRole": candidate_role,
                "phasePairOrdinal": phase_pair_ordinal,
                "source": {
                    "eventsIndex": source.source_index,
                    "eventsSha256": source.source_sha256,
                    "runId": source.run_id,
                    "pairId": source.pair_id,
                    "gameId": source.game_id,
                    "openingId": source.opening_id,
                    "flavor": source.flavor,
                    "ply": source.ply,
                },
            }
            positions.append(entry)
            root_ids.append(root_id)
            openings.append(
                {
                    "id": root_id,
                    "initialOfen": source.ofen,
                    "moves": [],
                    "phaseBucket": source.phase,
                    "source": (
                        "candidate-blind rules-only A/B trajectory root"
                        if source.run_id == "rules-only-random-v1"
                        else "candidate-blind historical AB/BA root"
                    ),
                }
            )
        pair_entries.append(
            {
                "id": pair_id,
                "phase": pair.phase,
                "phasePairOrdinal": phase_pair_ordinal,
                "candidateRole": candidate_role,
                "splitGroup": group_id,
                "rootIds": root_ids,
            }
        )
    if len({item["symmetryOrbitKey"] for item in positions}) != len(positions):
        raise AssertionError("selected suite contains a duplicate symmetry orbit")
    group_root_counts = Counter(item["splitGroup"] for item in positions)
    group_pair_counts = {
        group: roots // 2 for group, roots in group_root_counts.items()
    }
    effective_group_count = (
        (sum(group_root_counts.values()) ** 2)
        / sum(value * value for value in group_root_counts.values())
    )
    piece_counts: list[int] = []
    source_plies: list[int] = []
    halfmove_clocks: list[int] = []
    material_histogram = Counter()
    champion_present = 0
    wizard_present = 0
    both_leapers_present = 0
    exact_inputs: set[str] = set()
    for item in positions:
        pieces, _, _ = parse_ofen(item["initialOfen"])
        piece_count = len(pieces)
        champions = sum(piece == PIECE_INDEX["c"] for piece, _, _ in pieces)
        wizards = sum(piece == PIECE_INDEX["w"] for piece, _, _ in pieces)
        piece_counts.append(piece_count)
        source_plies.append(int(item["source"]["ply"]))
        halfmove_clocks.append(int(item["initialOfen"].split()[4]))
        material_histogram[piece_count] += 1
        champion_present += champions > 0
        wizard_present += wizards > 0
        both_leapers_present += champions > 0 and wizards > 0
        exact_inputs.add(_leakage_keys(item["initialOfen"])[0])
    if len(exact_inputs) != len(positions):
        raise AssertionError("selected suite contains a duplicate exact NNUE input")
    diversity_audit = {
        "roots": len(positions),
        "uniqueExactInputs": len(exact_inputs),
        "uniqueConservativeSymmetryOrbits": len(
            {item["symmetryOrbitKey"] for item in positions}
        ),
        "sourceTrajectories": len({item.source_unit for item in selected}),
        "launchFamilySplitGroups": len(group_root_counts),
        "pieceCount": _distribution(piece_counts),
        "sourcePly": _distribution(source_plies),
        "halfmoveClock": _distribution(halfmove_clocks),
        "materialPieceCountHistogram": {
            str(key): value for key, value in sorted(material_histogram.items())
        },
        "championPresentRoots": champion_present,
        "wizardPresentRoots": wizard_present,
        "championAndWizardPresentRoots": both_leapers_present,
        "championPresenceRate": champion_present / len(positions),
        "wizardPresenceRate": wizard_present / len(positions),
        "bothLeapersPresenceRate": both_leapers_present / len(positions),
        "minimumPiecesPerRoot": min(piece_counts),
        "maximumHalfmoveClock": max(halfmove_clocks),
    }
    target_searches = args.root_pairs * 2
    maximum_searches = len(positions)
    target_search_nodes = target_searches * args.nodes
    maximum_search_nodes = maximum_searches * args.nodes
    cpu_hours_slow = target_search_nodes / 15_000 / 3_600
    cpu_hours_fast = target_search_nodes / 40_000 / 3_600
    resource_estimate = {
        "targetSuccessfulSearches": target_searches,
        "maximumSearchesIfEveryReserveIsNeeded": maximum_searches,
        "nodesPerSearch": args.nodes,
        "targetSearchNodes": target_search_nodes,
        "maximumNodesIfEveryReserveIsNeeded": maximum_search_nodes,
        "observedPlanningNpsRange": [15_000, 40_000],
        "oneWorkerWallHoursRange": [
            round(cpu_hours_fast, 2),
            round(cpu_hours_slow, 2),
        ],
        "fourWorkerIdealWallHoursRange": [
            round(cpu_hours_fast / 4, 2),
            round(cpu_hours_slow / 4, 2),
        ],
        "fourWorkerCaveat": (
            "Ideal division only; memory bandwidth and CPU contention can "
            "reduce per-process NPS."
        ),
        "estimatedRawResultsMiBRange": [80, 300],
    }

    suite_path = output_dir / "deep-hce-v2-suite.json"
    suite = {
        # OmegaMatch opening suites currently use schema version 1.  The
        # surrounding freeze/audit remains deep-HCE schema version 2.
        "schemaVersion": 1,
        "kind": "omega-deep-hce-static-search-suite",
        "name": (
            f"Omega deep HCE v2 ({args.root_pairs} target A/B root pairs; "
            f"{args.reserve_pairs_per_phase} reserves per phase)"
        ),
        "seed": args.seed,
        "fixedNodes": args.nodes,
        "targetRootPairs": args.root_pairs,
        "targetPairsPerPhase": target_pairs_per_phase,
        "reservePairsPerPhase": args.reserve_pairs_per_phase,
        "candidateRootPairs": len(selected),
        "pairs": pair_entries,
        "positions": positions,
        "openings": openings,
        "effectiveSampleAudit": {
            "launchFamilySplitGroups": len(group_root_counts),
            "kishEffectiveGroupCountFromRootWeights": effective_group_count,
            "minimumRootsPerGroup": min(group_root_counts.values()),
            "medianRootsPerGroup": statistics.median(group_root_counts.values()),
            "maximumRootsPerGroup": max(group_root_counts.values()),
            "groupPairCounts": dict(sorted(group_pair_counts.items())),
            "note": (
                "The Kish count describes clustering by launch family; it is "
                "not a claim that positions within a group are independent."
            ),
        },
        "rootDiversityAudit": diversity_audit,
        "resourceEstimate": resource_estimate,
    }
    _atomic_json(suite_path, suite)

    validation_config_path = output_dir / "legality-validation-only.json"
    validation_output = output_dir / "DO-NOT-RUN-legality-only"
    validation_config = {
        "schemaVersion": 1,
        "runId": "DO-NOT-RUN-deep-hce-v2-legality-only",
        "outputDirectory": str(validation_output),
        "seed": args.seed,
        "engines": [
            {
                "id": "hce-validator-a",
                "executable": str(frozen_engine),
                "expectedSha256": frozen_engine_identity["sha256"],
                "options": HCE_OPTIONS,
            },
            {
                "id": "hce-validator-b",
                "executable": str(frozen_engine),
                "expectedSha256": frozen_engine_identity["sha256"],
                "options": HCE_OPTIONS,
            },
        ],
        "match": {
            "engineA": "hce-validator-a",
            "engineB": "hce-validator-b",
            "openingsFile": str(suite_path),
            "repeats": 1,
            "maxPlies": 1,
            "absoluteMaxPlies": 2,
            "mode": "nodes",
            "nodes": 1,
            "searchTimeoutMs": 5_000,
            "stopGraceMs": 500,
            "bootstrapIterations": 100,
            "freshProcessPerGame": True,
        },
        "expectedHarnessSha256": _sha256(harness),
        "expectedHarnessBundleSha256": _harness_bundle_identity(harness)["sha256"],
        "expectedOpeningSuiteSha256": _sha256(suite_path),
    }
    _atomic_json(validation_config_path, validation_config)
    dotnet = _resolve(args.dotnet)
    if not dotnet.is_file():
        raise FileNotFoundError(dotnet)
    validation_probe = subprocess.run(
        [
            str(dotnet),
            str(harness),
            "validate",
            "--config",
            str(validation_config_path),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if validation_probe.returncode != 0:
        raise ValueError(
            "CoreChess/OmegaMatch legality validation failed:\n"
            + validation_probe.stdout
            + validation_probe.stderr
        )
    lock_path = output_dir / "deep-hce-v2.freeze.json"
    lock = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-deep-hce-v2-freeze",
        "createdUtc": _utc_now(),
        "selectionContract": {
            "candidateBlind": True,
            "acceptedSourceKinds": [
                "deterministic rules-only independent A/B trajectories",
                "completed non-NNUE historical AB/BA trajectories",
            ],
            "rulesOnlySourcesUseEvaluator": False,
            "historicalNnueEnabledRunsAccepted": False,
            "scoresReadForRanking": False,
            "gameResultsReadForRanking": False,
            "bestMovesReadForRanking": False,
            "pvsReadForRanking": False,
            "resultRecordsUsedForCompletenessAndSafetyOnly": True,
            "wholeHistoricalTrajectoryKeptInOneSplitGroup": True,
            "maximumSelectedPairsPerHistoricalTrajectory": (
                args.max_pairs_per_trajectory
            ),
            "maximumSelectedPairsPerLaunchFamily": args.max_pairs_per_split_group,
            "candidatePairsPerTrajectoryPhase": (
                args.candidate_pairs_per_trajectory_phase
            ),
            "sourceFlavorLabelsPerUnit": ["ab", "ba"],
            "oppositeSideToMoveWithinPair": True,
            "distinctSymmetryOrbitsWithinPair": True,
            "distinctSymmetryOrbitsAcrossSuite": True,
            "phaseBalanced": True,
            "phaseOrder": list(PHASES),
            "phaseRootQuota": args.root_pairs * 2 // len(PHASES),
            "targetPairsPerPhase": target_pairs_per_phase,
            "reservePairsPerPhase": args.reserve_pairs_per_phase,
            "preflightExtraPairsPerPhase": args.preflight_extra_pairs_per_phase,
            "preflightWholePairExclusion": True,
            "preflightRejectedPairSanityCap": args.max_preflight_rejected_pairs,
            "replacementPolicy": (
                "Within each phase, consume frozen pair order. A pair is usable "
                "only when both roots produce valid fixed-node exact-cp labels; "
                "otherwise advance to the next frozen reserve pair. Never rank "
                "or choose replacements by score value, PV, or game result."
            ),
            "determinismPolicy": (
                "Search two genuinely distinct A/B roots once each. Rules-only "
                "A/B roots come from independent pinned PRNG trajectories; "
                "historical roots retain their original AB/BA provenance. "
                "Never run byte-identical HCE-vs-HCE color swaps."
            ),
        },
        "searchContract": {
            "mode": "fixed nodes",
            "nodes": args.nodes,
            "freshProcessPerPosition": True,
            "options": HCE_OPTIONS,
            "scorePolicy": (
                "require an unscored max-node line for attempted depth A; "
                "discard every score at A; require scored depth d=A-1 and use "
                "its last score, which must be exact cp, non-bound, non-mate"
            ),
            "minimumCompletedNodes": args.nodes,
            "targetIsLastCompletedExactIteration": True,
        },
        "freeze": {
            "sourceEngine": source_engine_identity,
            "engineExecutable": frozen_engine_identity,
            "harnessAssembly": _identity(harness),
            "harnessBundle": _harness_bundle_identity(harness),
            "dotnetHost": _identity(dotnet),
            "selector": _identity(Path(__file__)),
            "omegaNnueModule": _identity(Path(__file__).with_name("omega_nnue.py")),
            "symmetryModule": _identity(Path(__file__).with_name("select_screen.py")),
            "sourceEvents": source_audit,
            "rulesOnlyRootSources": rules_source_audit,
            "forbiddenArtifactRoots": [
                str(_resolve(path)) for path in args.forbidden_root
            ],
            "forbiddenArtifacts": exclusion_pins,
            "forbiddenPositionsRead": forbidden_positions,
            "forbiddenInputSignatures": sorted(forbidden),
        },
        "selection": {
            "candidateUnits": len(all_candidates),
            "selectedPairs": args.root_pairs,
            "selectedRoots": args.root_pairs * 2,
            "frozenCandidatePairs": len(selected),
            "frozenCandidateRoots": len(positions),
            "preflightCandidatePairs": len(preflight_pairs),
            "preflightRejectedPairs": len(rejected_pairs),
            "preflightRejectedRoots": senpai_preflight["positionsRejected"],
            "preflightRejectedPairSanityCap": args.max_preflight_rejected_pairs,
            "phaseCounts": dict(sorted(primary_phase_counts.items())),
            "sideToMoveCounts": dict(sorted(primary_stm_counts.items())),
            "candidatePhaseCounts": dict(sorted(phase_counts.items())),
            "candidateSideToMoveCounts": dict(sorted(stm_counts.items())),
            "sourceTrajectoryGroups": len(
                {item.source_unit for item in selected}
            ),
            "candidateRulesOnlyPairs": sum(
                item.ab.run_id == "rules-only-random-v1" for item in selected
            ),
            "candidateHistoricalPairs": sum(
                item.ab.run_id != "rules-only-random-v1" for item in selected
            ),
            "launchFamilySplitGroups": len(group_root_counts),
            "groupRootCountMinimum": min(group_root_counts.values()),
            "groupRootCountMedian": statistics.median(group_root_counts.values()),
            "groupRootCountMaximum": max(group_root_counts.values()),
            "kishEffectiveGroupCountFromRootWeights": effective_group_count,
            "resourceEstimate": resource_estimate,
            "rootDiversityAudit": diversity_audit,
            "validation": {
                "coreChessOmegaMatch": {
                    "passed": True,
                    "openings": len(positions),
                    "stdout": validation_probe.stdout.strip(),
                    "stderr": validation_probe.stderr.strip(),
                },
                "senpaiOfenAcceptance": {
                    "passed": True,
                    "searchMode": "depth",
                    "searchDepthPerPosition": 1,
                    "uciCommand": SENPAI_ACCEPTANCE_COMMAND,
                    "nodesOneFalseTerminalRegressionAvoided": True,
                    "syntacticScoreBearingInfoRequired": True,
                    "strictTeacherCompletedDepthPolicyApplied": False,
                    "candidateScoresUsedForSelection": 0,
                    **senpai_preflight,
                },
            },
            "suite": _identity(suite_path),
            "legalityValidationConfig": _identity(validation_config_path),
        },
        "outputs": {
            "directory": str(output_dir),
            "results": str(output_dir / "deep-hce-v2-results.jsonl"),
            "corpus": str(output_dir / "deep-hce-v2-search.jsonl"),
            "corpusManifest": str(output_dir / "deep-hce-v2-search.manifest.json"),
        },
        "commands": {
            "validate": (
                f'& ".\\.dotnet\\dotnet.exe" "{harness}" validate '
                f'--config "{validation_config_path}"'
            ),
            "run": (
                f'python "{Path(__file__).resolve()}" run --lock "{lock_path}" '
                f'--seal "{output_dir / "king-state-v1-prelabel.seal.json"}"'
            ),
            "finalize": (
                f'python "{Path(__file__).resolve()}" finalize --lock "{lock_path}" '
                f'--seal "{output_dir / "king-state-v1-prelabel.seal.json"}"'
            ),
        },
        "warnings": [
            (
                "legality-validation-only.json is intentionally a one-node "
                "validation fixture. Do not run it as the teacher match."
            ),
            (
                "Rules-only sources contain no engine evaluation. Historical "
                "sources are accepted only when no NNUE is enabled and their "
                "evaluative fields are ignored during selection."
            ),
            (
                "Every discovered prior NNUE training, screen, and formal "
                "confirmation artifact supplied through --forbidden-root was "
                "excluded under conservative symmetry orbits."
            ),
        ],
    }
    _atomic_json(lock_path, lock)
    print(
        f"Prepared {args.root_pairs} target A/B pairs plus "
        f"{args.reserve_pairs_per_phase} reserves per phase "
        f"({len(positions)} frozen candidate roots); target phases="
        f"{dict(primary_phase_counts)}, target stm={dict(primary_stm_counts)}."
    )
    print(
        f"Split groups={len(group_root_counts)}, weighted Kish effective groups="
        f"{effective_group_count:.1f}, group roots min/median/max="
        f"{min(group_root_counts.values())}/"
        f"{statistics.median(group_root_counts.values()):g}/"
        f"{max(group_root_counts.values())}."
    )
    print(
        f"Planned target nodes={target_search_nodes:,}; estimated wall time "
        f"{cpu_hours_fast:.1f}-{cpu_hours_slow:.1f}h at 1 worker or "
        f"{cpu_hours_fast / 4:.1f}-{cpu_hours_slow / 4:.1f}h ideal at 4."
    )
    print(f"Freeze: {lock_path}")
    print(f"Legality check: {lock['commands']['validate']}")
    print("No teacher-label searches were started.")
    return lock_path


def _load_lock(path: Path) -> dict[str, Any]:
    path = _resolve(path)
    lock = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(lock, dict) or lock.get("kind") != "omega-deep-hce-v2-freeze":
        raise ValueError(f"not a deep-HCE-v2 freeze: {path}")
    return lock


def _verify_identity(identity: dict[str, Any], label: str) -> Path:
    path = _resolve(Path(str(identity["path"])))
    current = _identity(path)
    if (
        current["bytes"] != int(identity["bytes"])
        or current["sha256"].lower() != str(identity["sha256"]).lower()
    ):
        raise ValueError(
            f"{label} changed after freeze: expected {identity['sha256']}, "
            f"got {current['sha256']}"
        )
    return path


def _verify_lock(lock_path: Path, lock: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    freeze = lock["freeze"]
    _verify_identity(freeze["sourceEngine"], "source HCE executable")
    engine = _verify_identity(freeze["engineExecutable"], "HCE executable")
    _verify_identity(freeze["selector"], "deep HCE selector/runner")
    _verify_identity(freeze["omegaNnueModule"], "omega_nnue.py")
    _verify_identity(freeze["symmetryModule"], "select_screen.py")
    harness = _verify_identity(freeze["harnessAssembly"], "OmegaMatch assembly")
    current_bundle = _harness_bundle_identity(harness)
    frozen_bundle = freeze["harnessBundle"]
    if (
        current_bundle["sha256"].lower()
        != str(frozen_bundle["sha256"]).lower()
        or current_bundle["files"] != frozen_bundle["files"]
    ):
        raise ValueError("OmegaMatch runtime bundle changed after freeze")
    _verify_identity(freeze["dotnetHost"], "dotnet host")
    for index, source in enumerate(freeze.get("sourceEvents", [])):
        if "events" in source:
            _verify_identity(source["events"], f"historical source events[{index}]")
    for index, source in enumerate(freeze.get("rulesOnlyRootSources", [])):
        _verify_identity(source["roots"], f"rules-only roots[{index}]")
        for companion_index, identity in enumerate(
            source.get("provenanceCompanions", [])
        ):
            _verify_identity(
                identity,
                f"rules-only provenance[{index}][{companion_index}]",
            )
    pinned_forbidden = freeze.get("forbiddenArtifacts", [])
    current_forbidden = _exclusion_files(
        Path(path) for path in freeze.get("forbiddenArtifactRoots", [])
    )
    pinned_paths = [
        str(_resolve(Path(str(identity["path"])))) for identity in pinned_forbidden
    ]
    current_paths = [str(path) for path in current_forbidden]
    if current_paths != pinned_paths:
        raise ValueError(
            "forbidden-artifact inventory changed after freeze: "
            f"expected {len(pinned_paths)} files, found {len(current_paths)}"
        )
    for index, identity in enumerate(pinned_forbidden):
        _verify_identity(identity, f"forbidden artifact[{index}]")
    suite_path = _verify_identity(lock["selection"]["suite"], "root suite")
    _verify_identity(
        lock["selection"]["legalityValidationConfig"],
        "legality-validation config",
    )
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    return engine, suite_path, suite


def _identities_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        str(_resolve(Path(str(left["path"]))))
        == str(_resolve(Path(str(right["path"]))))
        and int(left["bytes"]) == int(right["bytes"])
        and str(left["sha256"]).lower() == str(right["sha256"]).lower()
    )


def coordination_guard_path(results_path: Path) -> Path:
    """Return the shared sealer/runner/finalizer guard path."""

    return Path(str(_resolve(results_path)) + ".coord.lock")


def _validate_suite_structure(
    lock_path: Path,
    lock: dict[str, Any],
    suite_path: Path,
    suite: dict[str, Any],
) -> Path:
    if int(lock.get("schemaVersion", -1)) != SCHEMA_VERSION:
        raise ValueError("deep freeze schemaVersion is not supported")
    if suite.get("schemaVersion") != 1:
        raise ValueError("teacher suite schemaVersion must be 1")
    if suite.get("kind") != "omega-deep-hce-static-search-suite":
        raise ValueError("teacher suite kind is invalid")
    search = lock.get("searchContract")
    if not isinstance(search, dict):
        raise ValueError("deep freeze has no search contract")
    nodes = int(search.get("nodes", 0))
    if nodes < 100_000 or int(suite.get("fixedNodes", 0)) != nodes:
        raise ValueError("teacher suite fixed-node contract is inconsistent")
    if search.get("options") != HCE_OPTIONS:
        raise ValueError("deep freeze HCE options changed")

    target_pairs = int(suite.get("targetRootPairs", 0))
    target_per_phase = int(suite.get("targetPairsPerPhase", 0))
    reserve_per_phase = int(suite.get("reservePairsPerPhase", -1))
    candidate_pairs = int(suite.get("candidateRootPairs", 0))
    if (
        target_pairs <= 0
        or target_pairs != target_per_phase * len(PHASES)
        or reserve_per_phase < 1
        or candidate_pairs
        != (target_per_phase + reserve_per_phase) * len(PHASES)
    ):
        raise ValueError("teacher suite target/reserve quotas are inconsistent")
    pairs = suite.get("pairs")
    positions = suite.get("positions")
    openings = suite.get("openings")
    if not isinstance(pairs, list) or len(pairs) != candidate_pairs:
        raise ValueError("teacher suite pair inventory length is inconsistent")
    if not isinstance(positions, list) or len(positions) != candidate_pairs * 2:
        raise ValueError("teacher suite root inventory length is inconsistent")
    if not isinstance(openings, list) or len(openings) != len(positions):
        raise ValueError("teacher suite opening inventory length is inconsistent")

    forbidden = set(lock["freeze"].get("forbiddenInputSignatures", []))
    roots: dict[str, dict[str, Any]] = {}
    exact_inputs: set[str] = set()
    orbits: set[str] = set()
    candidate_phase_counts = Counter()
    candidate_stm_counts = Counter()
    for root in positions:
        if not isinstance(root, dict):
            raise ValueError("teacher suite contains a non-object root")
        root_id = str(root.get("id", ""))
        if not root_id or root_id in roots:
            raise ValueError(f"duplicate/empty teacher root id: {root_id}")
        ofen = root.get("initialOfen")
        if not isinstance(ofen, str):
            raise ValueError(f"teacher root has no OFEN: {root_id}")
        phase, stm, orbit, signatures = _position_meta(ofen)
        if (
            phase != root.get("phase")
            or stm != root.get("sideToMove")
            or orbit != root.get("symmetryOrbitKey")
        ):
            raise ValueError(f"teacher root metadata mismatch: {root_id}")
        if forbidden.intersection(signatures):
            raise ValueError(f"teacher root reaches forbidden orbit: {root_id}")
        exact = _leakage_keys(ofen)[0]
        if exact in exact_inputs or orbit in orbits:
            raise ValueError(f"duplicate exact/symmetry teacher root: {root_id}")
        exact_inputs.add(exact)
        orbits.add(orbit)
        pieces, parsed_stm, _ = parse_ofen(ofen)
        source = root.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"teacher root has no source provenance: {root_id}")
        ply = int(source.get("ply", -1))
        white_pieces = sum(side == 0 for _, side, _ in pieces)
        black_pieces = len(pieces) - white_pieces
        if (
            parsed_stm != stm
            or len(pieces) < 7
            or white_pieces < 2
            or black_pieces < 2
            or int(ofen.split()[4]) >= 90
            or not PHASE_PLY_WINDOWS[phase][0]
            <= ply
            <= PHASE_PLY_WINDOWS[phase][1]
        ):
            raise ValueError(f"unsafe teacher root survived freeze: {root_id}")
        roots[root_id] = root
        candidate_phase_counts[phase] += 1
        candidate_stm_counts[stm] += 1

    pair_ids: set[str] = set()
    paired_root_ids: set[str] = set()
    phase_ordinals: dict[str, set[int]] = {
        phase: set() for phase in PHASES
    }
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError("teacher suite contains a non-object pair")
        pair_id = str(pair.get("id", ""))
        phase = str(pair.get("phase", ""))
        ordinal = int(pair.get("phasePairOrdinal", 0))
        role = str(pair.get("candidateRole", ""))
        root_ids = pair.get("rootIds")
        if (
            not pair_id
            or pair_id in pair_ids
            or phase not in PHASES
            or ordinal <= 0
            or ordinal in phase_ordinals[phase]
            or not isinstance(root_ids, list)
            or len(root_ids) != 2
            or len(set(root_ids)) != 2
        ):
            raise ValueError(f"invalid teacher pair structure: {pair_id}")
        expected_role = "primary" if ordinal <= target_per_phase else "reserve"
        if role != expected_role:
            raise ValueError(f"teacher pair role/ordinal mismatch: {pair_id}")
        pair_roots = []
        for root_id_value in root_ids:
            root_id = str(root_id_value)
            if root_id not in roots or root_id in paired_root_ids:
                raise ValueError(f"teacher pair root inventory mismatch: {pair_id}")
            root = roots[root_id]
            if (
                root.get("pairId") != pair_id
                or root.get("splitGroup") != pair.get("splitGroup")
                or root.get("phase") != phase
                or int(root.get("phasePairOrdinal", 0)) != ordinal
                or root.get("candidateRole") != role
            ):
                raise ValueError(f"teacher pair/root metadata mismatch: {pair_id}")
            paired_root_ids.add(root_id)
            pair_roots.append(root)
        if (
            {root["sideToMove"] for root in pair_roots} != {"w", "b"}
            or pair_roots[0]["symmetryOrbitKey"]
            == pair_roots[1]["symmetryOrbitKey"]
        ):
            raise ValueError(f"teacher A/B distinction is invalid: {pair_id}")
        pair_ids.add(pair_id)
        phase_ordinals[phase].add(ordinal)
    if paired_root_ids != set(roots):
        raise ValueError("not every teacher root belongs to exactly one pair")
    expected_ordinals = set(range(1, target_per_phase + reserve_per_phase + 1))
    for phase in PHASES:
        if phase_ordinals[phase] != expected_ordinals:
            raise ValueError(f"{phase} pair ordinals are not contiguous")
        expected_roots = (target_per_phase + reserve_per_phase) * 2
        if candidate_phase_counts[phase] != expected_roots:
            raise ValueError(f"{phase} candidate-root quota is inconsistent")
    if candidate_stm_counts != Counter(
        {"w": candidate_pairs, "b": candidate_pairs}
    ):
        raise ValueError("candidate side-to-move balance is inconsistent")

    opening_by_id: dict[str, dict[str, Any]] = {}
    for opening in openings:
        if not isinstance(opening, dict):
            raise ValueError("teacher suite contains a non-object opening")
        opening_id = str(opening.get("id", ""))
        if opening_id in opening_by_id:
            raise ValueError(f"duplicate opening id: {opening_id}")
        opening_by_id[opening_id] = opening
    if set(opening_by_id) != set(roots):
        raise ValueError("opening/root inventories differ")
    for root_id, root in roots.items():
        opening = opening_by_id[root_id]
        if (
            opening.get("initialOfen") != root["initialOfen"]
            or opening.get("moves") != []
            or opening.get("phaseBucket") != root["phase"]
        ):
            raise ValueError(f"opening/root payload mismatch: {root_id}")

    selection = lock.get("selection")
    if not isinstance(selection, dict):
        raise ValueError("deep freeze has no selection audit")
    expected_target_phase = {
        phase: target_per_phase * 2 for phase in PHASES
    }
    expected_candidate_phase = {
        phase: (target_per_phase + reserve_per_phase) * 2
        for phase in PHASES
    }
    if (
        int(selection.get("selectedPairs", 0)) != target_pairs
        or int(selection.get("selectedRoots", 0)) != target_pairs * 2
        or int(selection.get("frozenCandidatePairs", 0)) != candidate_pairs
        or int(selection.get("frozenCandidateRoots", 0)) != candidate_pairs * 2
        or selection.get("phaseCounts") != expected_target_phase
        or selection.get("candidatePhaseCounts") != expected_candidate_phase
    ):
        raise ValueError("deep freeze selection audit disagrees with suite")

    output_dir = _resolve(lock_path).parent
    outputs = lock.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("deep freeze has no output contract")
    expected_outputs = {
        "results": output_dir / "deep-hce-v2-results.jsonl",
        "corpus": output_dir / "deep-hce-v2-search.jsonl",
        "corpusManifest": output_dir / "deep-hce-v2-search.manifest.json",
    }
    for key, expected in expected_outputs.items():
        if _resolve(Path(str(outputs.get(key, "")))) != expected:
            raise ValueError(f"deep freeze output path changed: {key}")
    return expected_outputs["results"]


def verify_freeze(lock_path: Path) -> dict[str, Any]:
    """Strictly rehash and structurally validate a deep-HCE-v2 freeze."""

    lock_path = _resolve(lock_path)
    lock = _load_lock(lock_path)
    engine, suite_path, suite = _verify_lock(lock_path, lock)
    results_path = _validate_suite_structure(lock_path, lock, suite_path, suite)
    lock_identity = _identity(lock_path)
    suite_identity = _identity(suite_path)
    engine_identity = _identity(engine)
    return {
        "lockPath": lock_path,
        "lockIdentity": lock_identity,
        "lockSha256": lock_identity["sha256"],
        "lock": lock,
        "suitePath": suite_path,
        "suiteIdentity": suite_identity,
        "suite": suite,
        "enginePath": engine,
        "engineIdentity": engine_identity,
        "resultsPath": results_path,
        "coordinationGuardPath": coordination_guard_path(results_path),
    }


def _walk_identity_dicts(
    value: Any, label: str = "seal.identities"
) -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if {"path", "bytes", "sha256"}.issubset(value):
            yield label, value
            return
        for key in sorted(value):
            yield from _walk_identity_dicts(value[key], f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_identity_dicts(item, f"{label}[{index}]")


def _verify_prelabel_seal(
    seal_path: Path,
    freeze_context: dict[str, Any],
    *,
    expected_static_hce_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    seal_path = _resolve(seal_path)
    expected_path = freeze_context["lockPath"].with_name(
        "king-state-v1-prelabel.seal.json"
    )
    if seal_path != expected_path:
        raise ValueError(
            f"pre-label seal must be adjacent to the freeze: {expected_path}"
        )
    before = _identity(seal_path)
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    after = _identity(seal_path)
    if before != after:
        raise ValueError("pre-label seal changed while it was verified")
    if (
        not isinstance(seal, dict)
        or seal.get("schemaVersion") != 1
        or seal.get("kind") != "omega-nnue-king-state-v1-prelabel-seal"
        or seal.get("generationId") != "king-state-v1-deep-hce-v2"
    ):
        raise ValueError("wrong or unsupported pre-label seal")
    declaration = seal.get("declaration")
    contracts = seal.get("contracts")
    identities = seal.get("identities")
    if (
        not isinstance(declaration, dict)
        or declaration.get("effectiveBeforeTeacherLabels") is not True
        or declaration.get("teacherArtifactsAbsentAtDeclaration") is not True
        or not isinstance(contracts, dict)
        or contracts.get("labelsPermittedOnlyAfterSeal") is not True
        or not isinstance(identities, dict)
    ):
        raise ValueError("pre-label seal declaration/contract is invalid")
    suite = freeze_context["suite"]
    if (
        int(contracts.get("deepHceTargetPairs", 0))
        != int(suite["targetRootPairs"])
        or int(contracts.get("deepHceTargetRoots", 0))
        != int(suite["targetRootPairs"]) * 2
        or int(contracts.get("teacherNodesPerRoot", 0))
        != int(suite["fixedNodes"])
    ):
        raise ValueError("pre-label seal teacher contract differs from freeze")
    identity_count = 0
    for label, identity in _walk_identity_dicts(identities):
        identity_count += 1
        _verify_identity(identity, label)
    if identity_count == 0:
        raise ValueError("pre-label seal pins no identities")
    required = {
        "deepHceFreeze": freeze_context["lockIdentity"],
        "teacherSuite": freeze_context["suiteIdentity"],
        "frozenTeacherEngine": freeze_context["engineIdentity"],
        "teacherLegalityConfig": freeze_context["lock"]["selection"][
            "legalityValidationConfig"
        ],
        "sourceTeacherEngine": freeze_context["lock"]["freeze"]["sourceEngine"],
    }
    for name, expected in required.items():
        actual = identities.get(name)
        if not isinstance(actual, dict) or not _identities_equal(actual, expected):
            raise ValueError(f"pre-label seal does not pin exact {name}")
    tooling = identities.get("tooling")
    if not isinstance(tooling, dict):
        raise ValueError("pre-label seal has no tooling identities")
    selector = tooling.get("deepHceV2")
    if (
        not isinstance(selector, dict)
        or not _identities_equal(selector, _identity(Path(__file__)))
        or not _identities_equal(
            selector, freeze_context["lock"]["freeze"]["selector"]
        )
    ):
        raise ValueError("pre-label seal does not pin this deep-HCE runner")
    static_hce = identities.get("staticHceEvaluator")
    if expected_static_hce_identity is None:
        expected_static_path = (
            Path(__file__).resolve().parents[2]
            / ".build-msvc-tests"
            / "Release"
            / "tests"
            / "omega_nnue.exe"
        ).resolve()
        expected_static_sha256 = EXPECTED_STATIC_HCE_SHA256
    else:
        expected_static_path = _resolve(
            Path(str(expected_static_hce_identity["path"]))
        )
        expected_static_sha256 = str(
            expected_static_hce_identity["sha256"]
        ).lower()
    if (
        not isinstance(static_hce, dict)
        or _resolve(Path(str(static_hce.get("path", "")))) != expected_static_path
        or str(static_hce.get("sha256", "")).lower()
        != expected_static_sha256
    ):
        raise ValueError("pre-label seal does not pin canonical static HCE evaluator")
    return {
        "sealPath": seal_path,
        "sealIdentity": after,
        "sealSha256": after["sha256"],
        "seal": seal,
        "identityCount": identity_count,
        "staticHceEvaluator": static_hce,
    }


def _verify_cached_seal(seal_context: dict[str, Any]) -> None:
    current = _identity(seal_context["sealPath"])
    if current != seal_context["sealIdentity"]:
        raise ValueError("pre-label seal changed during coordinated operation")


class _Uci:
    def __init__(self, executable: Path):
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        self.lines: queue.Queue[tuple[str, str]] = queue.Queue()
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        for name, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr)):
            threading.Thread(
                target=self._reader, args=(name, stream), daemon=True
            ).start()

    def _reader(self, name: str, stream: Any) -> None:
        for line in stream:
            self.lines.put((name, line.rstrip("\r\n")))

    def send(self, command: str) -> None:
        if self.process.poll() is not None:
            raise RuntimeError(f"engine exited with {self.process.returncode}")
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def until(self, predicate: Any, timeout: float) -> tuple[list[str], list[str]]:
        stdout: list[str] = []
        stderr: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("UCI response timed out")
            try:
                channel, line = self.lines.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f"engine exited with {self.process.returncode}"
                    )
                continue
            if channel == "stdout":
                stdout.append(line)
                if predicate(line):
                    return stdout, stderr
            else:
                stderr.append(line)

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.send("quit")
                self.process.wait(timeout=2)
            except (BrokenPipeError, RuntimeError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=2)


def _parse_info(lines: Sequence[str], requested_nodes: int) -> dict[str, Any]:
    infos: list[dict[str, Any]] = []
    maximum_nodes = 0
    for line_index, line in enumerate(lines):
        tokens = line.split()
        if not tokens or tokens[0] != "info":
            continue
        parsed: dict[str, Any] = {
            "raw": line,
            "lineIndex": line_index,
            "scoreTokenPresent": "score" in tokens,
        }
        for key in ("depth", "seldepth", "nodes"):
            if key in tokens:
                index = tokens.index(key)
                if index + 1 < len(tokens):
                    try:
                        parsed[key] = int(tokens[index + 1])
                    except ValueError:
                        pass
        maximum_nodes = max(maximum_nodes, int(parsed.get("nodes", 0)))
        if "score" in tokens:
            index = tokens.index("score")
            if index + 2 < len(tokens):
                parsed["scoreType"] = tokens[index + 1]
                try:
                    parsed["score"] = int(tokens[index + 2])
                except ValueError:
                    pass
                else:
                    parsed["lowerBound"] = "lowerbound" in tokens
                    parsed["upperBound"] = "upperbound" in tokens
                    if "pv" in tokens:
                        parsed["pv"] = tokens[tokens.index("pv") + 1 :]
                    else:
                        parsed["pv"] = []
        infos.append(parsed)
    if maximum_nodes < requested_nodes:
        raise ValueError(
            f"search completed only {maximum_nodes} nodes; required {requested_nodes}"
        )
    max_node_infos = [
        info for info in infos
        if int(info.get("nodes", -1)) == maximum_nodes
    ]
    if not max_node_infos:
        raise ValueError("search emitted no max-node progress line")
    attempted = max_node_infos[-1]
    if attempted["scoreTokenPresent"] or "depth" not in attempted:
        raise ValueError(
            "search lacks final unscored attempted-depth evidence at node cap"
        )
    attempted_depth = int(attempted["depth"])
    scored = [
        info for info in infos
        if (
            "score" in info
            and "depth" in info
            and int(info["depth"]) < attempted_depth
        )
    ]
    if not scored:
        raise ValueError(
            "search has no scored iteration below interrupted depth "
            f"{attempted_depth}"
        )
    completed_depth = max(int(info["depth"]) for info in scored)
    if completed_depth != attempted_depth - 1:
        raise ValueError(
            f"missing score evidence for immediately prior depth "
            f"{attempted_depth - 1}; greatest scored depth is {completed_depth}"
        )
    completed_scores = [
        info for info in scored if int(info["depth"]) == completed_depth
    ]
    latest_score = completed_scores[-1]
    if latest_score["scoreType"] != "cp":
        raise ValueError("greatest completed iteration has a mate score")
    if latest_score["lowerBound"] or latest_score["upperBound"]:
        raise ValueError(
            "greatest completed iteration ends in a bound, not an exact score"
        )
    return {
        "scoreCp": latest_score["score"],
        "depth": latest_score.get("depth"),
        "seldepth": latest_score.get("seldepth"),
        "pv": latest_score["pv"],
        "maximumNodes": maximum_nodes,
        "scoreRaw": latest_score["raw"],
        "interruptedDepth": attempted_depth,
    }


def _search_one(
    engine: Path,
    root: dict[str, Any],
    nodes: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    uci = _Uci(engine)
    transcript: list[str] = []
    stderr: list[str] = []
    started = time.monotonic()
    try:
        uci.send("uci")
        out, err = uci.until(lambda line: line == "uciok", 10)
        transcript.extend(out)
        stderr.extend(err)
        for name, value in HCE_OPTIONS.items():
            uci.send(f"setoption name {name} value {value}")
        uci.send("isready")
        out, err = uci.until(lambda line: line == "readyok", 10)
        transcript.extend(out)
        stderr.extend(err)
        uci.send("ucinewgame")
        uci.send("setoption name Clear Hash")
        uci.send(f"position fen {root['initialOfen']}")
        uci.send(f"go nodes {nodes}")
        out, err = uci.until(lambda line: line.startswith("bestmove "), timeout_seconds)
        transcript.extend(out)
        stderr.extend(err)
        bestmove_line = next(
            line for line in reversed(out) if line.startswith("bestmove ")
        )
        bestmove = bestmove_line.split()[1]
        if bestmove in {"(none)", "0000"}:
            raise ValueError(f"engine returned terminal bestmove {bestmove}")
        parsed = _parse_info(transcript, nodes)
        parsed.update(
            {
                "bestMove": bestmove,
                "wallTimeMs": round((time.monotonic() - started) * 1000, 3),
                "rawOutput": transcript,
                "standardError": stderr,
            }
        )
        return parsed
    finally:
        uci.close()


def _validate_senpai_acceptance(
    engine: Path,
    roots: Sequence[dict[str, Any]],
    *,
    timeout_seconds: float,
) -> tuple[set[str], dict[str, Any]]:
    """Require the frozen HCE to parse and search every root.

    Scores and PVs are intentionally discarded.  A completed `go depth 1`
    avoids the false `bestmove 0000` that Senpai can emit when `go nodes 1`
    stops before its first root move.  A syntactically scored info line then
    distinguishes a real search from a scoreless one-legal-move fast path.
    The strict 100k policy is not applied and this cannot influence ranking.
    """

    def start_uci() -> _Uci:
        instance = _Uci(engine)
        try:
            instance.send("uci")
            instance.until(lambda line: line == "uciok", 10)
            for name, value in HCE_OPTIONS.items():
                instance.send(f"setoption name {name} value {value}")
            instance.send("isready")
            instance.until(lambda line: line == "readyok", 10)
            return instance
        except BaseException:
            instance.close()
            raise

    accepted: set[str] = set()
    rejected: list[dict[str, str]] = []
    retried_roots: list[str] = []
    retry_events: list[dict[str, Any]] = []
    restart_count = 0
    uci = start_uci()
    try:
        for root in roots:
            root_id = str(root["id"])
            stdout: list[str] = []
            stderr: list[str] = []
            bestmove = ""
            protocol_failure: BaseException | None = None
            for probe_attempt in range(2):
                attempt_started = time.monotonic()
                try:
                    uci.send("ucinewgame")
                    uci.send("setoption name Clear Hash")
                    uci.send(f"position fen {root['initialOfen']}")
                    uci.send(SENPAI_ACCEPTANCE_COMMAND)
                    stdout, stderr = uci.until(
                        lambda line: line.startswith("bestmove "),
                        timeout_seconds,
                    )
                    bestmove_line = next(
                        line for line in reversed(stdout)
                        if line.startswith("bestmove ")
                    )
                    bestmove_tokens = bestmove_line.split()
                    if len(bestmove_tokens) < 2:
                        raise ValueError("malformed bestmove line")
                    bestmove = bestmove_tokens[1]
                    protocol_failure = None
                    break
                except (
                    BrokenPipeError,
                    IndexError,
                    OSError,
                    RuntimeError,
                    StopIteration,
                    TimeoutError,
                    ValueError,
                ) as error:
                    protocol_failure = error
                    source = root.get("sourcePosition")
                    source_audit = {}
                    if isinstance(source, SourcePosition):
                        source_audit = {
                            "sourceKind": (
                                "rules-only"
                                if source.run_id == "rules-only-random-v1"
                                else "historical"
                            ),
                            "runId": source.run_id,
                            "pairId": source.pair_id,
                            "gameId": source.game_id,
                            "flavor": source.flavor,
                            "ply": source.ply,
                        }
                    retry_events.append(
                        {
                            "rootId": root_id,
                            "probeAttempt": probe_attempt + 1,
                            "elapsedMs": round(
                                (time.monotonic() - attempt_started) * 1000, 3
                            ),
                            "errorType": type(error).__name__,
                            "error": str(error),
                            "ofenSha256": hashlib.sha256(
                                str(root["initialOfen"]).encode("utf-8")
                            ).hexdigest(),
                            **source_audit,
                        }
                    )
                    uci.close()
                    restart_count += 1
                    uci = start_uci()
                    if probe_attempt == 0:
                        retried_roots.append(root_id)
            if protocol_failure is not None:
                rejected.append(
                    {
                        "rootId": root_id,
                        "reason": (
                            "protocol failure after one fresh-process retry: "
                            f"{type(protocol_failure).__name__}: "
                            f"{protocol_failure}"
                        ),
                    }
                )
                continue
            if bestmove in {"0000", "(none)", "none"}:
                rejected.append(
                    {
                        "rootId": root_id,
                        "reason": f"terminal/unsearchable bestmove {bestmove}",
                    }
                )
                continue
            score_bearing = False
            for line in stdout:
                tokens = line.split()
                if not tokens or tokens[0] != "info" or "score" not in tokens:
                    continue
                score_index = tokens.index("score")
                if score_index + 2 >= len(tokens):
                    continue
                if tokens[score_index + 1] not in {"cp", "mate"}:
                    continue
                try:
                    int(tokens[score_index + 2])
                except ValueError:
                    continue
                score_bearing = True
                break
            if not score_bearing:
                rejected.append(
                    {
                        "rootId": root_id,
                        "reason": "scoreless/non-search fast path",
                    }
                )
                continue
            if stderr:
                rejected.append(
                    {
                        "rootId": root_id,
                        "reason": "stderr: " + "\n".join(stderr),
                    }
                )
                continue
            accepted.add(root_id)
        return accepted, {
            "positionsProbed": len(roots),
            "positionsAccepted": len(accepted),
            "positionsRejected": len(rejected),
            "freshProcessRetryLimit": 1,
            "engineRestartCount": restart_count,
            "retriedRoots": retried_roots,
            "retryEvents": retry_events,
            "rejectedRoots": rejected,
        }
    finally:
        uci.close()


def _append_result(path: Path, record: dict[str, Any]) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def _validate_result_contract(
    record: dict[str, Any],
    root: dict[str, Any],
    *,
    lock_sha256: str,
    seal_path: Path,
    seal_sha256: str,
    suite_sha256: str,
    engine_sha256: str,
    requested_nodes: int,
) -> None:
    expected = {
        "kind": "omega-deep-hce-v2-search-result",
        "rootId": root["id"],
        "pairId": root["pairId"],
        "splitGroup": root["splitGroup"],
        "phase": root["phase"],
        "sideToMove": root["sideToMove"],
        "ofen": root["initialOfen"],
        "symmetryOrbitKey": root["symmetryOrbitKey"],
        "lockSha256": lock_sha256,
        "sealPath": str(_resolve(seal_path)),
        "sealSha256": seal_sha256,
        "suiteSha256": suite_sha256,
        "engineSha256": engine_sha256,
        "requestedNodes": requested_nodes,
        "engineOptions": HCE_OPTIONS,
    }
    for key, value in expected.items():
        if record.get(key) != value:
            raise ValueError(f"result {key} does not match frozen root/contract")
    phase, stm, orbit, _ = _position_meta(str(record["ofen"]))
    if (
        phase != root["phase"]
        or stm != root["sideToMove"]
        or orbit != root["symmetryOrbitKey"]
    ):
        raise ValueError("stored OFEN metadata does not match frozen root")


def _validated_success(
    record: dict[str, Any],
    root: dict[str, Any],
    *,
    lock_sha256: str,
    seal_path: Path,
    seal_sha256: str,
    suite_sha256: str,
    engine_sha256: str,
    requested_nodes: int,
) -> dict[str, Any]:
    """Return a re-parsed valid search payload or raise ValueError."""

    _validate_result_contract(
        record,
        root,
        lock_sha256=lock_sha256,
        seal_path=seal_path,
        seal_sha256=seal_sha256,
        suite_sha256=suite_sha256,
        engine_sha256=engine_sha256,
        requested_nodes=requested_nodes,
    )
    if record.get("status") != "ok":
        raise ValueError("result is not successful")
    search = record.get("search")
    if not isinstance(search, dict):
        raise ValueError("result has no search payload")
    raw_output = search.get("rawOutput")
    if not isinstance(raw_output, list) or not all(
        isinstance(line, str) for line in raw_output
    ):
        raise ValueError("result has no re-playable raw UCI output")
    reparsed = _parse_info(raw_output, requested_nodes)
    for key in (
        "scoreCp",
        "depth",
        "seldepth",
        "pv",
        "maximumNodes",
        "scoreRaw",
        "interruptedDepth",
    ):
        if search.get(key) != reparsed.get(key):
            raise ValueError(f"derived search field {key} differs from raw UCI")
    bestmove_lines = [
        line for line in raw_output if line.startswith("bestmove ")
    ]
    if not bestmove_lines:
        raise ValueError("raw UCI output has no bestmove")
    bestmove_tokens = bestmove_lines[-1].split()
    if len(bestmove_tokens) < 2:
        raise ValueError("raw UCI bestmove line is malformed")
    bestmove = bestmove_tokens[1]
    if bestmove in {"0000", "(none)", "none"} or search.get("bestMove") != bestmove:
        raise ValueError("derived bestmove differs from raw UCI")
    standard_error = search.get("standardError")
    if standard_error not in ([], None):
        raise ValueError("successful result contains engine stderr")
    return search


@contextmanager
def coordination_guard(
    results_path: Path,
    owner: str,
    lock_sha256: str | None = None,
) -> Iterator[None]:
    """Exclusively coordinate sealing, teacher running, and finalization."""

    guard = coordination_guard_path(results_path)
    guard.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            guard,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError as error:
        raise ValueError(
            f"another sealer/runner/finalizer may be active: {guard}; remove "
            "the guard only after verifying no coordinated process is active"
        ) from error
    try:
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "createdUtc": _utc_now(),
                "owner": owner,
                "lockSha256": lock_sha256,
            },
            sort_keys=True,
        ).encode("utf-8")
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        guard.unlink(missing_ok=True)


def _run(args: argparse.Namespace) -> None:
    lock_path = _resolve(args.lock)
    lock = _load_lock(lock_path)
    results_path = _resolve(Path(lock["outputs"]["results"]))
    with coordination_guard(results_path, "deep-hce-v2-run"):
        freeze_context = verify_freeze(lock_path)
        if freeze_context["resultsPath"] != results_path:
            raise ValueError("guarded results path differs from verified freeze")
        seal_context = _verify_prelabel_seal(args.seal, freeze_context)
        _run_guarded(args, freeze_context, seal_context)
        _verify_cached_seal(seal_context)


def _run_guarded(
    args: argparse.Namespace,
    freeze_context: dict[str, Any],
    seal_context: dict[str, Any],
) -> None:
    lock = freeze_context["lock"]
    engine = freeze_context["enginePath"]
    suite_path = freeze_context["suitePath"]
    suite = freeze_context["suite"]
    lock_sha256 = freeze_context["lockSha256"]
    results_path = freeze_context["resultsPath"]
    seal_path = seal_context["sealPath"]
    seal_sha256 = seal_context["sealSha256"]
    nodes = int(lock["searchContract"]["nodes"])
    suite_sha256 = _sha256(suite_path)
    engine_sha256 = _sha256(engine)
    roots = {str(root["id"]): root for root in suite["positions"]}
    pairs_by_phase: dict[str, list[dict[str, Any]]] = {
        phase: sorted(
            (
                pair for pair in suite["pairs"]
                if pair["phase"] == phase
            ),
            key=lambda pair: int(pair["phasePairOrdinal"]),
        )
        for phase in PHASES
    }
    target_pairs_per_phase = int(suite["targetPairsPerPhase"])
    valid: dict[str, tuple[int, dict[str, Any]]] = {}
    latest: dict[str, dict[str, Any]] = {}
    attempts = Counter()
    if results_path.exists():
        for _, record in _jsonl(results_path):
            root_id = str(record.get("rootId", ""))
            if record.get("lockSha256") != lock_sha256:
                raise ValueError(
                    f"result from a different freeze/lock is mixed in: {root_id}"
                )
            if (
                record.get("sealPath") != str(seal_path)
                or record.get("sealSha256") != seal_sha256
            ):
                raise ValueError(
                    f"result from a different pre-label seal is mixed in: {root_id}"
                )
            attempts[root_id] = max(attempts[root_id], int(record.get("attempt", 0)))
            root = roots.get(root_id)
            if root is None:
                raise ValueError(f"results contain unknown root: {root_id}")
            try:
                _validate_result_contract(
                    record,
                    root,
                    lock_sha256=lock_sha256,
                    seal_path=seal_path,
                    seal_sha256=seal_sha256,
                    suite_sha256=suite_sha256,
                    engine_sha256=engine_sha256,
                    requested_nodes=nodes,
                )
            except (KeyError, TypeError, ValueError):
                continue
            previous = latest.get(root_id)
            if previous is None or int(record.get("attempt", 0)) >= int(
                previous.get("attempt", 0)
            ):
                latest[root_id] = record
            try:
                _validated_success(
                    record,
                    root,
                    lock_sha256=lock_sha256,
                    seal_path=seal_path,
                    seal_sha256=seal_sha256,
                    suite_sha256=suite_sha256,
                    engine_sha256=engine_sha256,
                    requested_nodes=nodes,
                )
            except (KeyError, TypeError, ValueError):
                continue
            attempt = int(record.get("attempt", 0))
            if root_id not in valid or attempt >= valid[root_id][0]:
                valid[root_id] = (attempt, record)
    if args.jobs <= 0:
        raise ValueError("--jobs must be positive")
    attempted_this_run: set[str] = set()
    remaining_limit = args.limit

    def execute(root: dict[str, Any]) -> tuple[dict[str, Any], str]:
        root_id = root["id"]
        attempt = attempts[root_id] + 1
        common = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "omega-deep-hce-v2-search-result",
            "createdUtc": _utc_now(),
            "rootId": root_id,
            "pairId": root["pairId"],
            "splitGroup": root["splitGroup"],
            "phase": root["phase"],
            "sideToMove": root["sideToMove"],
            "ofen": root["initialOfen"],
            "symmetryOrbitKey": root["symmetryOrbitKey"],
            "attempt": attempt,
            "lockSha256": lock_sha256,
            "sealPath": str(seal_path),
            "sealSha256": seal_sha256,
            "suiteSha256": suite_sha256,
            "engineSha256": engine_sha256,
            "requestedNodes": nodes,
            "engineOptions": HCE_OPTIONS,
        }
        try:
            search = _search_one(engine, root, nodes, args.timeout_seconds)
            record = {**common, "status": "ok", "search": search}
            status = f"cp {search['scoreCp']:+d}, d{search.get('depth')}"
        except ValueError as error:
            record = {
                **common,
                "status": "invalid-label",
                "errorType": type(error).__name__,
                "error": str(error),
            }
            status = f"INVALID LABEL: {error}"
        except BaseException as error:
            record = {
                **common,
                "status": "error",
                "errorType": type(error).__name__,
                "error": str(error),
            }
            status = f"ERROR {type(error).__name__}: {error}"
        return record, status

    def schedule() -> tuple[list[dict[str, Any]], Counter]:
        pending: list[dict[str, Any]] = []
        successful = Counter()
        for phase in PHASES:
            occupied = 0
            for pair in pairs_by_phase[phase]:
                root_ids = [str(value) for value in pair["rootIds"]]
                if all(root_id in valid for root_id in root_ids):
                    successful[phase] += 1
                    occupied += 1
                elif any(
                    root_id not in valid
                    and latest.get(root_id, {}).get("status") == "invalid-label"
                    for root_id in root_ids
                ):
                    continue
                else:
                    occupied += 1
                    for root_id in root_ids:
                        if (
                            root_id not in valid
                            and root_id not in attempted_this_run
                        ):
                            pending.append(roots[root_id])
                if occupied >= target_pairs_per_phase:
                    break
            if occupied < target_pairs_per_phase:
                raise ValueError(
                    f"frozen reserve pool exhausted for {phase}: "
                    f"only {occupied} successful-or-retryable pairs remain"
                )
        return pending, successful

    while True:
        pending, successful = schedule()
        if not pending:
            print(
                "Successful pairs now available by phase: "
                + ", ".join(
                    f"{phase}={successful[phase]}/{target_pairs_per_phase}"
                    for phase in PHASES
                ),
                flush=True,
            )
            break
        if remaining_limit is not None:
            if remaining_limit <= 0:
                break
            pending = pending[:remaining_limit]
        print(
            f"Searching {len(pending)} required root(s) with {args.jobs} "
            f"worker(s); {len(valid)} valid roots already recorded.",
            flush=True,
        )
        # Only the coordinator appends, so even a multi-worker search batch
        # keeps the resume log line-atomic and fsynced.
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            outcomes = executor.map(execute, pending)
            for index, (record, status) in enumerate(outcomes, 1):
                root_id = str(record["rootId"])
                _append_result(results_path, record)
                attempts[root_id] = max(
                    attempts[root_id], int(record.get("attempt", 0))
                )
                latest[root_id] = record
                attempted_this_run.add(root_id)
                if record.get("status") == "ok":
                    try:
                        _validated_success(
                            record,
                            roots[root_id],
                            lock_sha256=lock_sha256,
                            seal_path=seal_path,
                            seal_sha256=seal_sha256,
                            suite_sha256=suite_sha256,
                            engine_sha256=engine_sha256,
                            requested_nodes=nodes,
                        )
                    except (KeyError, TypeError, ValueError) as error:
                        raise AssertionError(
                            f"newly written success failed integrity check: {root_id}"
                        ) from error
                    valid[root_id] = (int(record["attempt"]), record)
                print(
                    f"[{index}/{len(pending)}] {root_id}: {status}",
                    flush=True,
                )
        if remaining_limit is not None:
            remaining_limit -= len(pending)
        # Invalid labels advance immediately into the frozen reserve order.
        # Retryable process/time-out errors wait for a fresh invocation.
    print(f"Results: {results_path}")


def _component_groups(records: list[dict[str, Any]]) -> dict[str, str]:
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    by_orbit: dict[str, set[str]] = defaultdict(set)
    for record in records:
        group = str(record["splitGroup"])
        find(group)
        _, orbit, _ = _leakage_keys(str(record["ofen"]))
        by_orbit[orbit].add(group)
    for groups in by_orbit.values():
        values = sorted(groups)
        for value in values[1:]:
            union(values[0], value)
    components: dict[str, list[str]] = defaultdict(list)
    for value in sorted(parent):
        components[find(value)].append(value)
    result: dict[str, str] = {}
    for members in components.values():
        digest = hashlib.sha256("\n".join(members).encode("utf-8")).hexdigest()
        for member in members:
            result[member] = f"deep-hce-v2-component:{digest}"
    return result


def _finalize(args: argparse.Namespace) -> None:
    lock_path = _resolve(args.lock)
    lock = _load_lock(lock_path)
    results_path = _resolve(Path(lock["outputs"]["results"]))
    with coordination_guard(results_path, "deep-hce-v2-finalize"):
        freeze_context = verify_freeze(lock_path)
        if freeze_context["resultsPath"] != results_path:
            raise ValueError("guarded results path differs from verified freeze")
        seal_context = _verify_prelabel_seal(args.seal, freeze_context)
        _finalize_guarded(freeze_context, seal_context)
        _verify_cached_seal(seal_context)


def _finalize_guarded(
    freeze_context: dict[str, Any],
    seal_context: dict[str, Any],
) -> None:
    lock_path = freeze_context["lockPath"]
    expected_lock = freeze_context["lockSha256"]
    lock = freeze_context["lock"]
    engine = freeze_context["enginePath"]
    suite_path = freeze_context["suitePath"]
    suite = freeze_context["suite"]
    results_path = freeze_context["resultsPath"]
    seal_path = seal_context["sealPath"]
    expected_seal = seal_context["sealSha256"]
    if not results_path.is_file():
        raise FileNotFoundError(results_path)
    expected_suite = freeze_context["suiteIdentity"]["sha256"]
    expected_engine = freeze_context["engineIdentity"]["sha256"]
    expected_nodes = int(lock["searchContract"]["nodes"])
    roots = {str(root["id"]): root for root in suite["positions"]}
    valid: dict[str, tuple[int, dict[str, Any]]] = {}
    attempts = 0
    for _, record in _jsonl(results_path):
        attempts += 1
        root_id = str(record.get("rootId", ""))
        if root_id not in roots:
            raise ValueError(f"results contain unknown root: {root_id}")
        if record.get("lockSha256") != expected_lock:
            raise ValueError(
                f"result from a different freeze/lock is mixed in: {root_id}"
            )
        if (
            record.get("sealPath") != str(seal_path)
            or record.get("sealSha256") != expected_seal
        ):
            raise ValueError(
                f"result from a different pre-label seal is mixed in: {root_id}"
            )
        try:
            _validated_success(
                record,
                roots[root_id],
                lock_sha256=expected_lock,
                seal_path=seal_path,
                seal_sha256=expected_seal,
                suite_sha256=expected_suite,
                engine_sha256=expected_engine,
                requested_nodes=expected_nodes,
            )
        except (KeyError, TypeError, ValueError):
            continue
        attempt = int(record.get("attempt", 0))
        if root_id not in valid or attempt >= valid[root_id][0]:
            valid[root_id] = (attempt, record)

    target_pairs_per_phase = int(suite["targetPairsPerPhase"])
    selected: list[dict[str, Any]] = []
    selected_pair_ids: list[str] = []
    skipped_pairs = Counter()
    reserve_pairs_used = Counter()
    for phase in PHASES:
        phase_selected = 0
        phase_pairs = sorted(
            (pair for pair in suite["pairs"] if pair["phase"] == phase),
            key=lambda pair: int(pair["phasePairOrdinal"]),
        )
        for pair in phase_pairs:
            root_ids = [str(value) for value in pair["rootIds"]]
            if not all(root_id in valid for root_id in root_ids):
                skipped_pairs[phase] += 1
                continue
            selected.extend(valid[root_id][1] for root_id in root_ids)
            selected_pair_ids.append(str(pair["id"]))
            phase_selected += 1
            if pair.get("candidateRole") == "reserve":
                reserve_pairs_used[phase] += 1
            if phase_selected >= target_pairs_per_phase:
                break
        if phase_selected != target_pairs_per_phase:
            raise ValueError(
                f"{phase} has only {phase_selected}/"
                f"{target_pairs_per_phase} fully valid pairs; rerun or inspect "
                "reserve exhaustion"
            )

    forbidden = set(lock["freeze"]["forbiddenInputSignatures"])
    for record in selected:
        root_id = str(record["rootId"])
        _, orbit, signatures = _leakage_keys(str(record["ofen"]))
        if forbidden.intersection(signatures):
            raise ValueError(f"forbidden confirmation/training orbit reached: {root_id}")
    expected_records = int(suite["targetRootPairs"]) * 2
    if len(selected) != expected_records:
        raise AssertionError(
            f"replacement selection produced {len(selected)} roots, "
            f"expected {expected_records}"
        )
    components = _component_groups(selected)
    results_sha256 = _sha256(results_path)

    rows: list[dict[str, Any]] = []
    phase_counts = Counter()
    stm_counts = Counter()
    for record in selected:
        search = record["search"]
        root = roots[record["rootId"]]
        original_group = str(record["splitGroup"])
        sample_payload = (
            f"omega-deep-hce-v2\0{record['rootId']}\0{expected_engine}\0"
            f"{expected_nodes}\0{expected_lock}\0{expected_seal}\0"
            f"{record['ofen']}"
        )
        row = {
            "schemaVersion": SCHEMA_VERSION,
            "sampleId": hashlib.sha256(sample_payload.encode("utf-8")).hexdigest(),
            "groupId": components[original_group],
            "sourcePairGroup": original_group,
            "pairId": record["pairId"],
            "ofen": record["ofen"],
            "sideToMove": record["sideToMove"],
            "phase": record["phase"],
            "targetCpStm": int(search["scoreCp"]),
            "provenance": {
                "kind": "fresh-static-deep-hce-search",
                "rootId": record["rootId"],
                "lockSha256": expected_lock,
                "sealPath": str(seal_path),
                "sealSha256": expected_seal,
                "staticHceEvaluatorSha256": seal_context[
                    "staticHceEvaluator"
                ]["sha256"],
                "source": root["source"],
                "engineSha256": expected_engine,
                "engineOptions": HCE_OPTIONS,
                "requestedNodes": expected_nodes,
                "completedNodes": int(search["maximumNodes"]),
                "depth": search.get("depth"),
                "seldepth": search.get("seldepth"),
                "bestMove": search.get("bestMove"),
                "pv": search.get("pv", []),
                "resultAttempt": record["attempt"],
                "resultFileSha256": results_sha256,
            },
        }
        rows.append(row)
        phase_counts[row["phase"]] += 1
        stm_counts[row["sideToMove"]] += 1
    rows.sort(key=lambda row: row["sampleId"])
    payload = b"".join(
        (
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        for row in rows
    )
    corpus_path = _resolve(Path(lock["outputs"]["corpus"]))
    _verify_cached_seal(seal_context)
    _atomic_bytes(corpus_path, payload)
    manifest_path = _resolve(Path(lock["outputs"]["corpusManifest"]))
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-deep-hce-v2-search-corpus",
        "createdUtc": _utc_now(),
        "freeze": _identity(lock_path),
        "prelabelSeal": _identity(seal_path),
        "staticHceEvaluator": seal_context["staticHceEvaluator"],
        "suite": _identity(suite_path),
        "results": {**_identity(results_path), "attemptRecords": attempts},
        "engineExecutable": _identity(engine),
        "selection": lock["selectionContract"],
        "search": lock["searchContract"],
        "replacement": {
            "policy": lock["selectionContract"]["replacementPolicy"],
            "selectedPairIds": selected_pair_ids,
            "skippedIncompleteOrInvalidPairsByPhase": dict(
                sorted(skipped_pairs.items())
            ),
            "reservePairsUsedByPhase": {
                phase: reserve_pairs_used[phase] for phase in PHASES
            },
        },
        "grouping": {
            "wholeSourceAbBaOrRulesOnlyPair": True,
            "symmetryOrbitComponents": True,
            "componentCount": len(set(components.values())),
            "crossSplitOrbitPolicy": "union whole source-pair groups",
        },
        "coverage": {
            "records": len(rows),
            "rootPairs": len(rows) // 2,
            "phaseCounts": dict(sorted(phase_counts.items())),
            "sideToMoveCounts": dict(sorted(stm_counts.items())),
            "uniqueSampleIds": len({row["sampleId"] for row in rows}),
            "uniqueSymmetryOrbits": len(
                {_leakage_keys(str(row["ofen"]))[1] for row in rows}
            ),
            "minimumCompletedNodes": min(
                row["provenance"]["completedNodes"] for row in rows
            ),
        },
        "output": {
            "path": str(corpus_path),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "nextCommands": {
            "labelStaticHce": (
                f'python "{Path(__file__).with_name("label_hce.py").resolve()}" '
                f'--input "{corpus_path}" --output "<HCE_OUTPUT.jsonl>" '
                f'--cpp-evaluator "'
                f'{seal_context["staticHceEvaluator"]["path"]}"'
            ),
            "buildResidual": (
                f'python "{Path(__file__).with_name("build_residual_targets.py").resolve()}" '
                f'--search-teacher "{corpus_path}" --handcrafted "<HCE_OUTPUT.jsonl>" '
                f'--output "<RESIDUAL_OUTPUT.jsonl>"'
            ),
        },
    }
    _atomic_json(manifest_path, manifest)
    print(
        f"Finalized {len(rows)} deep labels in {len(set(components.values()))} "
        f"leakage-safe split components; phases={dict(phase_counts)}, "
        f"stm={dict(stm_counts)}."
    )
    print(f"Corpus: {corpus_path}")
    print(f"Manifest: {manifest_path}")


def _synthetic_ofen(phase: str, flavor: str, stm: str, index: int) -> str:
    piece_counts = {"opening": 38, "middlegame": 30, "late": 18, "endgame": 8}
    count = piece_counts[phase]
    # parse_ofen only needs a legal encoding, while the self-test deliberately
    # varies pawn files to create distinct conservative orbits.
    board = [["." for _ in range(10)] for _ in range(10)]
    board[0][4] = "K"
    board[9][4] = "k"
    board[1][0] = "P"
    board[8][9] = "p"
    symbols = "PNBRCQpnbrcq"
    placed = 4
    cursor = index * 13 + (0 if flavor == "ab" else 7)
    while placed < count:
        square = cursor % 98 + 1
        cursor += 17
        rank, file = divmod(square, 10)
        if rank >= 9 or board[rank][file] != ".":
            continue
        board[rank][file] = symbols[(placed + index) % len(symbols)]
        placed += 1
    ranks = []
    for rank in range(9, -1, -1):
        tokens = []
        empty = 0
        for symbol in board[rank]:
            if symbol == ".":
                empty += 1
            else:
                if empty:
                    tokens.append(str(empty))
                    empty = 0
                tokens.append(symbol)
        if empty:
            tokens.append(str(empty))
        ranks.append("".join(tokens))
    return f"{'/'.join(ranks)}[-/-/-/-] {stm} - - 0 1"


def _self_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="omega-deep-hce-v2-selftest-"))
    try:
        default_prepare = _parse_args(["prepare"])
        expected_rules_root = _default_paths()["sampled_roots"]
        if default_prepare.rules_only_root != [expected_rules_root]:
            raise AssertionError("prepare lost its default rules-only root")
        explicit_rules_root = Path("fresh-rules-only-roots.jsonl")
        explicit_prepare = _parse_args(
            [
                "prepare",
                "--rules-only-root",
                str(explicit_rules_root),
            ]
        )
        if explicit_prepare.rules_only_root != [explicit_rules_root]:
            raise AssertionError(
                "explicit rules-only root did not replace the default"
            )
        if SENPAI_ACCEPTANCE_COMMAND != "go depth 1":
            raise AssertionError(
                "acceptance probe regressed to a node-capped false-terminal path"
            )
        events = root / "events.jsonl"
        records: list[dict[str, Any]] = [
            {
                "RecordType": "run",
                "RunId": "synthetic-candidate-blind",
                "Engines": [
                    {"Id": "a", "Options": {"UseOmegaNNUE": "false"}},
                    {"Id": "b", "Options": {"UseOmegaNNUE": "false"}},
                ],
            }
        ]
        expected = []
        for index, phase in enumerate(PHASES, 1):
            pair_id = f"pair-{phase}"
            for flavor, white, black, stm in (
                ("ab", "a", "b", "w"),
                ("ba", "b", "a", "b"),
            ):
                game_id = f"{pair_id}-{flavor}"
                ofen = _synthetic_ofen(phase, flavor, stm, index)
                expected.append(ofen)
                common = {
                    "GameId": game_id,
                    "PairId": pair_id,
                    "Attempt": 1,
                    "OpeningId": pair_id,
                    "WhiteEngineId": white,
                    "BlackEngineId": black,
                }
                records.append({"RecordType": "gameStart", **common})
                records.append(
                    {
                        "RecordType": "ply",
                        "GameId": game_id,
                        "Attempt": 1,
                        "Ply": PHASE_PLY_WINDOWS[phase][0] + index,
                        "PreOfen": ofen,
                        "Error": None,
                        "BestMove": "ignored",
                        "Search": {"ScoreCp": 999, "Pv": ["ignored"]},
                    }
                )
                records.append(
                    {
                        "RecordType": "gameResult",
                        **common,
                        "Result": "1/2-1/2",
                        "IllegalMoves": 0,
                        "IllegalPvs": 0,
                        "ProtocolFailures": 0,
                        "TimeForfeits": 0,
                    }
                )
        _atomic_bytes(
            events,
            b"".join(
                (
                    json.dumps(record, separators=(",", ":")) + "\n"
                ).encode("utf-8")
                for record in records
            ),
        )
        candidates, audit = _parse_source(
            events, 0, set(), DEFAULT_SEED, 4
        )
        if _engine_is_candidate_blind(
            {
                "Engines": [
                    {"Options": {"UseOmegaNNUE": "true"}},
                ]
            }
        ):
            raise AssertionError("UseOmegaNNUE=true historical run was accepted")
        selected = _select_pairs(
            candidates,
            4,
            max_pairs_per_trajectory=4,
            max_pairs_per_split_group=4,
        )
        if len(selected) != 4 or {item.phase for item in selected} != set(PHASES):
            raise AssertionError("phase-balanced pair selection failed")
        for pair in selected:
            if pair.ab.stm == pair.ba.stm or len(pair.orbits) != 2:
                raise AssertionError("AB/BA root distinction failed")
        if audit["evaluativeFieldsPresentButIgnored"].get("Search") != 8:
            raise AssertionError("evaluative-field audit failed")
        rules_path = root / "rules.jsonl"
        rules_records = []
        for index, phase in enumerate(PHASES, 1):
            for flavor, stm in (("ab", "w"), ("ba", "b")):
                ofen = _synthetic_ofen(phase, flavor, stm, index)
                rules_records.append(
                    {
                        "schemaVersion": 1,
                        "kind": "omega-rules-only-random-root",
                        "generatorSeed": str(DEFAULT_SEED),
                        "trajectoryPairId": f"random-{phase}",
                        "trajectoryId": f"random-{phase}-{flavor}",
                        "flavor": flavor,
                        "ply": PHASE_PLY_WINDOWS[phase][0] + index,
                        "phase": phase,
                        "sideToMove": stm,
                        "ofen": ofen,
                    }
                )
        _atomic_bytes(
            rules_path,
            b"".join(
                (
                    json.dumps(record, separators=(",", ":")) + "\n"
                ).encode("utf-8")
                for record in rules_records
            ),
        )
        rules_candidates, rules_audit = _parse_rules_only_roots(
            rules_path, 1, set(), DEFAULT_SEED
        )
        if len(rules_candidates) != 4 or rules_audit["trajectories"] != 8:
            raise AssertionError("rules-only A/B trajectory parsing failed")
        forbidden = set(selected[0].ab.orbit_signatures)
        filtered, _ = _parse_source(
            events, 0, forbidden, DEFAULT_SEED, 4
        )
        if any(item.ab.orbit == selected[0].ab.orbit for item in filtered):
            raise AssertionError("forbidden symmetry orbit was not excluded")
        exact = _parse_info(
            [
                "info depth 8 score cp 17 nodes 80000 pv a1a2",
                "info depth 8 score cp 21 nodes 90000 pv a1a2 b9b8",
                "info depth 9 score cp 99 nodes 99000 pv b1b2",
                "info depth 9 seldepth 13 nodes 100000 nps 25000",
                "bestmove a1a2",
            ],
            100000,
        )
        if (
            exact["scoreCp"] != 21
            or exact["depth"] != 8
            or exact["interruptedDepth"] != 9
            or exact["maximumNodes"] != 100000
        ):
            raise AssertionError("fixed-node score parsing failed")
        for invalid in (
            [
                "info depth 7 score cp 18 nodes 40000",
                "info depth 8 score cp 21 upperbound nodes 80000",
                "info depth 9 nodes 100000",
            ],
            [
                "info depth 7 score cp 18 nodes 40000",
                "info depth 8 score mate 3 nodes 80000",
                "info depth 9 nodes 100000",
            ],
            ["info depth 9 score cp 21 nodes 99999"],
            [
                "info depth 8 score cp 21 nodes 100000",
            ],
            [
                "info depth 9 score cp 21 nodes 90000",
                "info depth 9 nodes 100000",
            ],
            [
                "info depth 7 score cp 21 nodes 80000",
                "info depth 9 nodes 100000",
            ],
        ):
            try:
                _parse_info(invalid, 100000)
            except ValueError:
                pass
            else:
                raise AssertionError("unsafe teacher score was accepted")
        completed_lines = [
            "info depth 8 score cp 18 nodes 58277 pv a1a2",
            "info depth 8 score cp 20 nodes 80283 pv a1a2 b9b8",
            "info depth 9 score cp 42 lowerbound nodes 93000 pv c1c2",
            "info depth 9 score cp 41 nodes 97000 pv d1d2",
            "info depth 9 seldepth 13 nodes 100000 nps 25000",
            "bestmove a1a2",
        ]
        completed_iteration = _parse_info(completed_lines, 100000)
        if (
            completed_iteration["scoreCp"] != 20
            or completed_iteration["depth"] != 8
            or completed_iteration["interruptedDepth"] != 9
            or completed_iteration["maximumNodes"] != 100000
        ):
            raise AssertionError("Senpai completed-iteration score was rejected")

        seal_dir = root / "seal-adversarial"
        seal_dir.mkdir()
        lock_file = seal_dir / "deep-hce-v2.freeze.json"
        suite_file = seal_dir / "deep-hce-v2-suite.json"
        engine_file = seal_dir / "teacher.exe"
        source_engine_file = seal_dir / "source-teacher.exe"
        legality_file = seal_dir / "legality.json"
        static_file = seal_dir / "static-hce.exe"
        for path, payload in (
            (lock_file, b"lock"),
            (suite_file, b"suite"),
            (engine_file, b"engine"),
            (source_engine_file, b"source-engine"),
            (legality_file, b"legality"),
            (static_file, b"static"),
        ):
            _atomic_bytes(path, payload)
        synthetic_freeze_context = {
            "lockPath": lock_file,
            "lockIdentity": _identity(lock_file),
            "suiteIdentity": _identity(suite_file),
            "engineIdentity": _identity(engine_file),
            "suite": {
                "targetRootPairs": 4096,
                "fixedNodes": 100000,
            },
            "lock": {
                "selection": {
                    "legalityValidationConfig": _identity(legality_file),
                },
                "freeze": {
                    "sourceEngine": _identity(source_engine_file),
                    "selector": _identity(Path(__file__)),
                },
            },
        }
        seal_file = seal_dir / "king-state-v1-prelabel.seal.json"
        static_identity = _identity(static_file)
        try:
            _verify_prelabel_seal(
                seal_file,
                synthetic_freeze_context,
                expected_static_hce_identity=static_identity,
            )
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("missing pre-label seal was accepted")
        valid_seal = {
            "schemaVersion": 1,
            "kind": "omega-nnue-king-state-v1-prelabel-seal",
            "generationId": "king-state-v1-deep-hce-v2",
            "declaration": {
                "effectiveBeforeTeacherLabels": True,
                "teacherArtifactsAbsentAtDeclaration": True,
            },
            "contracts": {
                "labelsPermittedOnlyAfterSeal": True,
                "deepHceTargetPairs": 4096,
                "deepHceTargetRoots": 8192,
                "teacherNodesPerRoot": 100000,
            },
            "identities": {
                "deepHceFreeze": _identity(lock_file),
                "teacherSuite": _identity(suite_file),
                "frozenTeacherEngine": _identity(engine_file),
                "sourceTeacherEngine": _identity(source_engine_file),
                "teacherLegalityConfig": _identity(legality_file),
                "staticHceEvaluator": static_identity,
                "tooling": {
                    "deepHceV2": _identity(Path(__file__)),
                },
            },
        }
        wrong_seal = {
            **valid_seal,
            "identities": {
                **valid_seal["identities"],
                "deepHceFreeze": _identity(suite_file),
            },
        }
        _atomic_json(seal_file, wrong_seal)
        try:
            _verify_prelabel_seal(
                seal_file,
                synthetic_freeze_context,
                expected_static_hce_identity=static_identity,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("seal for a different freeze was accepted")
        _atomic_json(seal_file, valid_seal)
        cached_seal = _verify_prelabel_seal(
            seal_file,
            synthetic_freeze_context,
            expected_static_hce_identity=static_identity,
        )
        _atomic_json(seal_file, {**valid_seal, "tampered": True})
        try:
            _verify_cached_seal(cached_seal)
        except ValueError:
            pass
        else:
            raise AssertionError("changed pre-label seal remained accepted")

        source = selected[0].ab
        integrity_root = {
            "id": "integrity-root",
            "pairId": "integrity-pair",
            "splitGroup": "integrity-group",
            "phase": source.phase,
            "sideToMove": source.stm,
            "initialOfen": source.ofen,
            "symmetryOrbitKey": source.orbit,
        }
        integrity_search = {
            **completed_iteration,
            "bestMove": "a1a2",
            "rawOutput": completed_lines,
            "standardError": [],
        }
        integrity_record = {
            "kind": "omega-deep-hce-v2-search-result",
            "status": "ok",
            "rootId": integrity_root["id"],
            "pairId": integrity_root["pairId"],
            "splitGroup": integrity_root["splitGroup"],
            "phase": integrity_root["phase"],
            "sideToMove": integrity_root["sideToMove"],
            "ofen": integrity_root["initialOfen"],
            "symmetryOrbitKey": integrity_root["symmetryOrbitKey"],
            "lockSha256": "lock",
            "sealPath": str(_resolve(root / "seal.json")),
            "sealSha256": "seal",
            "suiteSha256": "suite",
            "engineSha256": "engine",
            "requestedNodes": 100000,
            "engineOptions": HCE_OPTIONS,
            "search": integrity_search,
        }
        _validated_success(
            integrity_record,
            integrity_root,
            lock_sha256="lock",
            seal_path=root / "seal.json",
            seal_sha256="seal",
            suite_sha256="suite",
            engine_sha256="engine",
            requested_nodes=100000,
        )
        corrupted = {**integrity_record, "sideToMove": "b" if source.stm == "w" else "w"}
        try:
            _validated_success(
                corrupted,
                integrity_root,
                lock_sha256="lock",
                seal_path=root / "seal.json",
                seal_sha256="seal",
                suite_sha256="suite",
                engine_sha256="engine",
                requested_nodes=100000,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("corrupt immutable result metadata was accepted")
        wrong_seal_result = {**integrity_record, "sealSha256": "wrong"}
        try:
            _validated_success(
                wrong_seal_result,
                integrity_root,
                lock_sha256="lock",
                seal_path=root / "seal.json",
                seal_sha256="seal",
                suite_sha256="suite",
                engine_sha256="engine",
                requested_nodes=100000,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("result from a different seal was accepted")
        print("deep_hce_v2 self-test passed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    defaults = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sample = subparsers.add_parser(
        "sample",
        help="generate deterministic rules-only legal A/B trajectories",
    )
    sample.add_argument("--dotnet", type=Path, default=defaults["dotnet"])
    sample.add_argument("--project", type=Path, default=defaults["sampler_project"])
    sample.add_argument("--output", type=Path, default=defaults["sampled_roots"])
    sample.add_argument("--seed", type=int, default=DEFAULT_SEED)
    sample.add_argument("--trajectory-pairs", type=int, default=2048)
    sample.add_argument("--max-plies", type=int, default=220)
    sample.add_argument("--positions-per-phase-side", type=int, default=2)
    sample.add_argument("--capture-percent", type=int, default=72)
    sample.add_argument("--force", action="store_true")

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--engine", type=Path, default=defaults["engine"])
    prepare.add_argument("--harness", type=Path, default=defaults["harness"])
    prepare.add_argument("--dotnet", type=Path, default=defaults["dotnet"])
    prepare.add_argument(
        "--source-root", action="append", type=Path,
        default=[defaults["source_root"]]
    )
    prepare.add_argument("--source", action="append", type=Path, default=[])
    prepare.add_argument(
        "--rules-only-root",
        action="append",
        type=Path,
        default=None,
    )
    prepare.add_argument(
        "--exclude-source-pattern",
        action="append",
        default=["*nnue*", "*residual-v3*"],
    )
    prepare.add_argument(
        "--forbidden-root",
        action="append",
        type=Path,
        default=_default_forbidden_roots(defaults),
    )
    prepare.add_argument("--output-dir", type=Path, default=defaults["output_dir"])
    prepare.add_argument("--seed", type=int, default=DEFAULT_SEED)
    prepare.add_argument("--root-pairs", type=int, default=DEFAULT_ROOT_PAIRS)
    prepare.add_argument(
        "--reserve-pairs-per-phase",
        type=int,
        default=DEFAULT_RESERVE_PAIRS_PER_PHASE,
        help=(
            "frozen candidate-blind replacement pairs per phase; searched only "
            "when an earlier pair cannot yield two valid exact-cp labels"
        ),
    )
    prepare.add_argument(
        "--preflight-extra-pairs-per-phase",
        type=int,
        default=DEFAULT_PREFLIGHT_EXTRA_PAIRS_PER_PHASE,
    )
    prepare.add_argument(
        "--max-preflight-rejected-pairs",
        type=int,
        default=DEFAULT_MAX_PREFLIGHT_REJECTED_PAIRS,
        help="abort on broader Senpai/CoreChess protocol disagreement",
    )
    prepare.add_argument(
        "--candidate-pairs-per-trajectory-phase", type=int, default=64
    )
    prepare.add_argument("--max-pairs-per-trajectory", type=int, default=32)
    prepare.add_argument("--max-pairs-per-split-group", type=int, default=64)
    prepare.add_argument("--nodes", type=int, default=DEFAULT_NODES)
    prepare.add_argument("--acceptance-timeout-seconds", type=float, default=5.0)

    run = subparsers.add_parser("run")
    run.add_argument("--lock", required=True, type=Path)
    run.add_argument("--seal", required=True, type=Path)
    run.add_argument("--limit", type=int)
    run.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Concurrent fresh engine processes; 1 is most reproducible.",
    )
    run.add_argument("--timeout-seconds", type=float, default=180.0)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--lock", required=True, type=Path)
    finalize.add_argument("--seal", required=True, type=Path)

    verify = subparsers.add_parser(
        "verify-freeze",
        help="strictly rehash and structurally validate a prepared freeze",
    )
    verify.add_argument("--lock", required=True, type=Path)

    subparsers.add_parser("self-test")
    args = parser.parse_args(argv)
    if args.command == "prepare" and args.rules_only_root is None:
        args.rules_only_root = [defaults["sampled_roots"]]
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "sample":
        _sample(args)
    elif args.command == "prepare":
        _prepare(args)
    elif args.command == "run":
        _run(args)
    elif args.command == "finalize":
        _finalize(args)
    elif args.command == "verify-freeze":
        context = verify_freeze(args.lock)
        print(
            f"Verified freeze {context['lockSha256']}; "
            f"pairs={len(context['suite']['pairs'])}, "
            f"roots={len(context['suite']['positions'])}."
        )
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
