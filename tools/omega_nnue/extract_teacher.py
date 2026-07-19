#!/usr/bin/env python3
"""Extract deterministic NNUE teacher labels from OmegaMatch telemetry.

OmegaMatch keeps the complete UCI ``info`` history for every searched ply.
The last exact (non-bound, non-mate) score is a much better bootstrap target
than a game result alone.  This tool:

* selects the newest complete attempt for every game;
* rejects failed searches and bound/mate-only scores;
* records the teacher binary/options/search budget and PV;
* snapshots append-only logs, so an active arena cannot silently change an
  already-read prefix; and
* optionally collapses positions that are identical to the current NNUE
  feature set, retaining the deepest node-budget bucket and a median score.

The output is accepted directly by ``train.py``.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from omega_nnue import active_features, nnue_input_signature, parse_ofen


NODE_COMMAND = re.compile(r"(?:^|\s)nodes\s+(\d+)(?:\s|$)", re.IGNORECASE)


@dataclass(frozen=True)
class TeacherCandidate:
    ofen: str
    side_to_move: str
    score_cp: int
    raw_score_cp: int
    outcome_stm: float | None
    game_id: str
    attempt: int
    ply: int
    pair_id: str
    opening_id: str
    group_id: str
    engine_id: str
    engine_sha256: str | None
    engine_options: dict[str, str]
    requested_nodes: int
    completed_nodes: int | None
    depth: int
    seldepth: int | None
    pv: tuple[str, ...]
    run_id: str
    source_path: str
    source_line: int
    source_snapshot_sha256: str


@dataclass
class Attempt:
    start: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    plies: list[tuple[int, dict[str, Any]]] = field(default_factory=list)


@dataclass
class ExtractionStats:
    physical_records: int = 0
    run_records: int = 0
    game_starts: int = 0
    game_results: int = 0
    ply_records: int = 0
    selected_games: int = 0
    selected_plies: int = 0
    accepted_scores: int = 0
    ignored_truncated_final_lines: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    def add(self, other: "ExtractionStats") -> None:
        for name in (
            "physical_records",
            "run_records",
            "game_starts",
            "game_results",
            "ply_records",
            "selected_games",
            "selected_plies",
            "accepted_scores",
            "ignored_truncated_final_lines",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.skipped.update(other.skipped)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_write_bytes(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )


def _snapshot_jsonl(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, Any]]:
    """Read precisely the byte prefix visible at open time and hash that prefix."""

    path = path.resolve()
    snapshot_size = path.stat().st_size
    digest = hashlib.sha256()
    records: list[tuple[int, dict[str, Any]]] = []
    consumed = 0
    ignored_truncated = False
    with path.open("rb") as stream:
        line_number = 0
        while consumed < snapshot_size:
            line = stream.readline(snapshot_size - consumed)
            if not line:
                break
            consumed += len(line)
            digest.update(line)
            line_number += 1
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                if consumed == snapshot_size and not line.endswith((b"\n", b"\r")):
                    ignored_truncated = True
                    continue
                raise ValueError(f"{path}:{line_number}: malformed telemetry JSON")
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: telemetry record is not an object")
            records.append((line_number, value))
    return records, {
        "path": str(path),
        "snapshotBytes": snapshot_size,
        "snapshotSha256": digest.hexdigest(),
        "ignoredTruncatedFinalLine": ignored_truncated,
    }


def _normalized_ofen(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing PreOfen")
    fields = value.split()
    if len(fields) != 6:
        raise ValueError(f"expected six-field OFEN, got {len(fields)} fields")
    if fields[1].lower() not in ("w", "b"):
        raise ValueError(f"invalid OFEN side to move {fields[1]!r}")
    return " ".join(fields)


def _feature_key(ofen: str) -> str:
    """Hash the exact ordered feature sets consumed by PS104-shared-v0."""

    _, side_to_move, _ = parse_ofen(ofen)
    return nnue_input_signature(
        active_features(ofen, 0),
        active_features(ofen, 1),
        side_to_move == "w",
    )


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _requested_nodes(search: dict[str, Any], run: dict[str, Any]) -> int | None:
    command = search.get("Command")
    if isinstance(command, str):
        match = NODE_COMMAND.search(command)
        if match:
            return int(match.group(1))
    match_metadata = run.get("Match")
    if isinstance(match_metadata, dict):
        return _integer(match_metadata.get("Nodes"))
    return None


def _latest_scored_exact(
    search: dict[str, Any], *, minimum_depth: int
) -> dict[str, Any] | None:
    """Return the latest *scored* update only when that update is exact.

    Unscored current-move/node-limit updates may follow the last completed
    iteration and are ignored.  A later bound or mate score is not silently
    replaced by an older exact value: that position is excluded from the
    high-confidence teacher corpus.
    """

    history = search.get("Info")
    if not isinstance(history, list):
        return None
    for info in reversed(history):
        if not isinstance(info, dict):
            continue
        score = _integer(info.get("ScoreCp"))
        has_mate = info.get("ScoreMate") is not None
        if score is None and not has_mate:
            continue
        depth = _integer(info.get("Depth"))
        multipv = _integer(info.get("MultiPv"))
        if score is None or has_mate or depth is None or depth < minimum_depth:
            return None
        if info.get("LowerBound") is True or info.get("UpperBound") is True:
            return None
        if multipv not in (None, 1):
            return None
        return info
    return None


def _result_outcome(result: dict[str, Any], side_to_move: str) -> float | None:
    value = result.get("Result")
    white = {"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}.get(value)
    if white is None:
        return None
    return white if side_to_move == "w" else 1.0 - white


def _engine_metadata(run: dict[str, Any], engine_id: str) -> dict[str, Any]:
    engines = run.get("Engines")
    if not isinstance(engines, list):
        return {}
    for engine in engines:
        if (
            isinstance(engine, dict)
            and str(engine.get("Id", "")).lower() == engine_id.lower()
        ):
            return engine
    return {}


def _string_options(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in sorted(value.items())}


def _candidate_from_ply(
    *,
    line_number: int,
    ply: dict[str, Any],
    start: dict[str, Any],
    result: dict[str, Any],
    run: dict[str, Any],
    source_pin: dict[str, Any],
    opening_root_key: str,
    minimum_requested_nodes: int,
    minimum_depth: int,
    clip_cp: int,
) -> tuple[TeacherCandidate | None, str | None]:
    if ply.get("PostOfen") is None or ply.get("BestMove") is None:
        return None, "no-played-move"
    if ply.get("Error") not in (None, ""):
        return None, "ply-error"
    search = ply.get("Search")
    if not isinstance(search, dict):
        return None, "no-search"
    if search.get("DeadlineExceeded") is True:
        return None, "deadline"
    if search.get("ProcessExited") is True:
        return None, "process-exited"
    requested_nodes = _requested_nodes(search, run)
    if requested_nodes is None:
        return None, "not-fixed-node"
    if requested_nodes < minimum_requested_nodes:
        return None, "below-node-floor"
    info = _latest_scored_exact(search, minimum_depth=minimum_depth)
    if info is None:
        return None, "no-exact-cp"

    try:
        ofen = _normalized_ofen(ply.get("PreOfen"))
    except ValueError:
        return None, "invalid-ofen"
    side = ofen.split()[1].lower()
    color = str(ply.get("Color", "")).lower()
    if color and color not in (side, "white" if side == "w" else "black"):
        return None, "side-mismatch"

    raw_score = _integer(info.get("ScoreCp"))
    depth = _integer(info.get("Depth"))
    assert raw_score is not None and depth is not None
    score = max(-clip_cp, min(clip_cp, raw_score))
    engine_id = str(ply.get("EngineId", ""))
    engine = _engine_metadata(run, engine_id)
    opening_id = str(start.get("OpeningId", ""))
    # The root position is global across suite files. Including the suite hash
    # here would let the same root leak into multiple deterministic splits.
    group_id = "root:" + hashlib.sha256(
        opening_root_key.encode("utf-8")
    ).hexdigest()
    pv_value = info.get("Pv")
    pv = (
        tuple(str(move) for move in pv_value)
        if isinstance(pv_value, list)
        else tuple()
    )
    return (
        TeacherCandidate(
            ofen=ofen,
            side_to_move=side,
            score_cp=score,
            raw_score_cp=raw_score,
            outcome_stm=_result_outcome(result, side),
            game_id=str(start.get("GameId", ply.get("GameId", ""))),
            attempt=int(start.get("Attempt", ply.get("Attempt", 0))),
            ply=int(ply.get("Ply", 0)),
            pair_id=str(start.get("PairId", "")),
            opening_id=opening_id,
            group_id=group_id,
            engine_id=engine_id,
            engine_sha256=(
                str(engine["Sha256"]) if engine.get("Sha256") is not None else None
            ),
            engine_options=_string_options(engine.get("Options")),
            requested_nodes=requested_nodes,
            completed_nodes=_integer(info.get("Nodes")),
            depth=depth,
            seldepth=_integer(info.get("SelDepth")),
            pv=pv,
            run_id=str(run.get("RunId", "")),
            source_path=str(source_pin["path"]),
            source_line=line_number,
            source_snapshot_sha256=str(source_pin["snapshotSha256"]),
        ),
        None,
    )


def extract_file(
    path: Path,
    *,
    minimum_requested_nodes: int,
    minimum_depth: int,
    clip_cp: int,
) -> tuple[list[TeacherCandidate], dict[str, Any], ExtractionStats]:
    records, source_pin = _snapshot_jsonl(path)
    stats = ExtractionStats()
    stats.physical_records = len(records)
    if source_pin["ignoredTruncatedFinalLine"]:
        stats.ignored_truncated_final_lines = 1

    run: dict[str, Any] | None = None
    attempts: dict[tuple[str, int], Attempt] = defaultdict(Attempt)
    for line_number, record in records:
        record_type = record.get("RecordType")
        if record_type == "run":
            if run is not None:
                raise ValueError(f"{path}:{line_number}: more than one run record")
            run = record
            stats.run_records += 1
        elif record_type == "gameStart":
            stats.game_starts += 1
            game_id = str(record.get("GameId", ""))
            attempt_number = int(record.get("Attempt", 0))
            attempts[(game_id, attempt_number)].start = record
        elif record_type == "ply":
            stats.ply_records += 1
            game_id = str(record.get("GameId", ""))
            attempt_number = int(record.get("Attempt", 0))
            attempts[(game_id, attempt_number)].plies.append((line_number, record))
        elif record_type == "gameResult":
            stats.game_results += 1
            game_id = str(record.get("GameId", ""))
            attempt_number = int(record.get("Attempt", 0))
            attempts[(game_id, attempt_number)].result = record

    if run is None:
        raise ValueError(f"{path}: no run record")

    complete_by_game: dict[str, list[tuple[int, Attempt]]] = defaultdict(list)
    for (game_id, attempt_number), attempt in attempts.items():
        if attempt.start is not None and attempt.result is not None:
            complete_by_game[game_id].append((attempt_number, attempt))

    candidates: list[TeacherCandidate] = []
    for game_id in sorted(complete_by_game):
        attempt_number, attempt = max(
            complete_by_game[game_id], key=lambda item: item[0]
        )
        del attempt_number
        assert attempt.start is not None and attempt.result is not None
        stats.selected_games += 1
        if not attempt.plies:
            stats.skipped["complete-game-without-search"] += 1
            continue
        try:
            opening_root_key = _feature_key(
                _normalized_ofen(attempt.plies[0][1].get("PreOfen"))
            )
        except ValueError:
            stats.skipped["invalid-opening-root"] += len(attempt.plies)
            continue
        for line_number, ply in attempt.plies:
            stats.selected_plies += 1
            candidate, reason = _candidate_from_ply(
                line_number=line_number,
                ply=ply,
                start=attempt.start,
                result=attempt.result,
                run=run,
                source_pin=source_pin,
                opening_root_key=opening_root_key,
                minimum_requested_nodes=minimum_requested_nodes,
                minimum_depth=minimum_depth,
                clip_cp=clip_cp,
            )
            if candidate is None:
                assert reason is not None
                stats.skipped[reason] += 1
                continue
            candidates.append(candidate)
            stats.accepted_scores += 1

    source_pin["runId"] = str(run.get("RunId", ""))
    source_pin["configSha256"] = run.get("ConfigSha256")
    source_pin["openingSuiteSha256"] = run.get("OpeningSuiteSha256")
    source_pin["selectedGames"] = stats.selected_games
    source_pin["acceptedScores"] = stats.accepted_scores
    return candidates, source_pin, stats


def _candidate_quality(candidate: TeacherCandidate) -> tuple[int, int, int]:
    return (
        candidate.requested_nodes,
        candidate.depth,
        candidate.completed_nodes if candidate.completed_nodes is not None else -1,
    )


def _median_int(values: Iterable[int]) -> int:
    return int(round(float(statistics.median(values))))


def _record(
    representative: TeacherCandidate,
    *,
    group_id: str,
    target_cp: int,
    outcome: float | None,
    teacher_count: int,
    score_min: int,
    score_max: int,
    score_mad: float,
) -> dict[str, Any]:
    sample_key = (
        f"{_feature_key(representative.ofen)}\0{target_cp}\0"
        f"{representative.requested_nodes}"
    )
    record: dict[str, Any] = {
        "schemaVersion": 1,
        "sampleId": hashlib.sha256(sample_key.encode("utf-8")).hexdigest(),
        "groupId": group_id,
        "ofen": representative.ofen,
        "sideToMove": representative.side_to_move,
        "targetCpStm": target_cp,
        "teacherSearchCount": teacher_count,
        "teacherScoreCpMin": score_min,
        "teacherScoreCpMax": score_max,
        "teacherScoreCpMad": score_mad,
        "provenance": {
            "kind": "omega-match-last-exact-score",
            "runId": representative.run_id,
            "gameId": representative.game_id,
            "pairId": representative.pair_id,
            "openingId": representative.opening_id,
            "attempt": representative.attempt,
            "ply": representative.ply,
            "engineId": representative.engine_id,
            "engineSha256": representative.engine_sha256,
            "engineOptions": representative.engine_options,
            "requestedNodes": representative.requested_nodes,
            "completedNodes": representative.completed_nodes,
            "depth": representative.depth,
            "seldepth": representative.seldepth,
            "rawScoreCp": representative.raw_score_cp,
            "pv": list(representative.pv),
            "sourcePath": representative.source_path,
            "sourceLine": representative.source_line,
            "sourceSnapshotSha256": representative.source_snapshot_sha256,
        },
    }
    if outcome is not None:
        record["outcomeStm"] = outcome
    return record


def collapse_candidates(
    candidates: Iterable[TeacherCandidate],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_feature: dict[str, list[TeacherCandidate]] = defaultdict(list)
    input_count = 0
    for candidate in candidates:
        input_count += 1
        by_feature[_feature_key(candidate.ofen)].append(candidate)

    # An exact transposition connects the full opening-root groups, not merely
    # the duplicate rows. Union them before collapsing so the representative
    # row cannot hide a train/validation leak from train.py.
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for bucket in by_feature.values():
        group_ids = sorted({item.group_id for item in bucket})
        if not group_ids:
            continue
        find(group_ids[0])
        for group_id in group_ids[1:]:
            union(group_ids[0], group_id)

    components: dict[str, list[str]] = defaultdict(list)
    for group_id in sorted(parent):
        components[find(group_id)].append(group_id)
    component_id: dict[str, str] = {}
    for members in components.values():
        digest = hashlib.sha256("\n".join(members).encode("utf-8")).hexdigest()
        for member in members:
            component_id[member] = f"root-component:{digest}"

    output: list[dict[str, Any]] = []
    disagreement = []
    dropped_lower_budget = 0
    for feature_key in sorted(by_feature):
        bucket = by_feature[feature_key]
        best_requested = max(item.requested_nodes for item in bucket)
        best_budget = [
            item for item in bucket if item.requested_nodes == best_requested
        ]
        dropped_lower_budget += len(bucket) - len(best_budget)
        scores = [item.score_cp for item in best_budget]
        target_cp = _median_int(scores)
        outcomes = [
            item.outcome_stm for item in best_budget if item.outcome_stm is not None
        ]
        outcome = float(statistics.median(outcomes)) if outcomes else None
        deviations = [abs(score - target_cp) for score in scores]
        mad = float(statistics.median(deviations))
        representative = max(
            best_budget,
            key=lambda item: (
                -abs(item.score_cp - target_cp),
                *_candidate_quality(item),
                item.run_id,
                item.game_id,
                -item.ply,
            ),
        )
        output.append(
            _record(
                representative,
                group_id=component_id[representative.group_id],
                target_cp=target_cp,
                outcome=outcome,
                teacher_count=len(best_budget),
                score_min=min(scores),
                score_max=max(scores),
                score_mad=mad,
            )
        )
        disagreement.append(max(scores) - min(scores))

    return output, {
        "inputCandidates": input_count,
        "uniqueFeatureInputs": len(output),
        "duplicatesCollapsed": input_count - len(output),
        "lowerBudgetCandidatesDropped": dropped_lower_budget,
        "openingRootGroups": len(parent),
        "transpositionComponents": len(components),
        "largestTranspositionComponent": max(
            (len(members) for members in components.values()), default=0
        ),
        "scoreRangeMedianCp": (
            float(statistics.median(disagreement)) if disagreement else None
        ),
        "scoreRangeP95Cp": (
            float(sorted(disagreement)[int(0.95 * (len(disagreement) - 1))])
            if disagreement
            else None
        ),
    }


def _raw_records(candidates: Iterable[TeacherCandidate]) -> list[dict[str, Any]]:
    records = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.source_path,
            item.source_line,
            item.game_id,
            item.ply,
        ),
    ):
        records.append(
            _record(
                candidate,
                group_id=candidate.group_id,
                target_cp=candidate.score_cp,
                outcome=candidate.outcome_stm,
                teacher_count=1,
                score_min=candidate.score_cp,
                score_max=candidate.score_cp,
                score_mad=0.0,
            )
        )
    return records


def _discover_inputs(
    files: list[Path], roots: list[Path], exclude_patterns: list[str]
) -> list[Path]:
    discovered = {path.resolve() for path in files}
    for root in roots:
        root = root.resolve()
        if root.is_file():
            discovered.add(root)
        else:
            discovered.update(root.rglob("events.jsonl"))
    result = sorted(
        (
            path
            for path in discovered
            if not any(
                fnmatch.fnmatch(str(path).replace("\\", "/"), pattern.replace("\\", "/"))
                for pattern in exclude_patterns
            )
        ),
        key=lambda path: str(path).lower(),
    )
    if not result:
        raise ValueError("no events.jsonl inputs were found")
    return result


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, default=[])
    parser.add_argument("--input-root", action="append", type=Path, default=[])
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="glob matched against normalized absolute input paths; may be repeated",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--min-requested-nodes", type=int, default=20_000)
    parser.add_argument("--min-depth", type=int, default=6)
    parser.add_argument("--clip-cp", type=int, default=2_000)
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="emit every selected ply instead of one row per current NNUE input",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    if args.min_requested_nodes <= 0:
        raise ValueError("--min-requested-nodes must be positive")
    if args.min_depth <= 0:
        raise ValueError("--min-depth must be positive")
    if args.clip_cp <= 0:
        raise ValueError("--clip-cp must be positive")

    paths = _discover_inputs(args.input, args.input_root, args.exclude)
    all_candidates: list[TeacherCandidate] = []
    pins: list[dict[str, Any]] = []
    totals = ExtractionStats()
    for index, path in enumerate(paths, start=1):
        candidates, pin, stats = extract_file(
            path,
            minimum_requested_nodes=args.min_requested_nodes,
            minimum_depth=args.min_depth,
            clip_cp=args.clip_cp,
        )
        all_candidates.extend(candidates)
        pins.append(pin)
        totals.add(stats)
        print(
            f"[{index}/{len(paths)}] {path}: "
            f"{stats.selected_games} games, {len(candidates)} labels",
            flush=True,
        )

    if not all_candidates:
        raise ValueError("no usable exact fixed-node teacher scores were found")
    if args.keep_duplicates:
        records = _raw_records(all_candidates)
        collapse = {
            "inputCandidates": len(all_candidates),
            "uniqueFeatureInputs": None,
            "duplicatesCollapsed": 0,
            "lowerBudgetCandidatesDropped": 0,
        }
    else:
        records, collapse = collapse_candidates(all_candidates)

    payload = b"".join(
        (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        for record in records
    )
    _atomic_write_bytes(args.output, payload)
    output_pin = {
        "path": str(args.output.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "records": len(records),
    }
    manifest = {
        "schemaVersion": 1,
        "selection": {
            "attemptPolicy": "newest-complete",
            "scorePolicy": "latest-scored-must-be-exact-nonbound-nonmate",
            "minimumRequestedNodes": args.min_requested_nodes,
            "minimumDepth": args.min_depth,
            "clipCp": args.clip_cp,
            "deduplicatedByCurrentNnueInput": not args.keep_duplicates,
            "excludedInputPatterns": args.exclude,
            "deduplicationPolicy": (
                "highest-requested-node-bucket-median-score"
                if not args.keep_duplicates
                else "none"
            ),
        },
        "totals": {
            "physicalRecords": totals.physical_records,
            "runRecords": totals.run_records,
            "gameStarts": totals.game_starts,
            "gameResults": totals.game_results,
            "plyRecords": totals.ply_records,
            "selectedGames": totals.selected_games,
            "selectedPlies": totals.selected_plies,
            "acceptedScores": totals.accepted_scores,
            "ignoredTruncatedFinalLines": totals.ignored_truncated_final_lines,
            "skipped": dict(sorted(totals.skipped.items())),
        },
        "collapse": collapse,
        "sources": pins,
        "output": output_pin,
    }
    manifest_path = args.manifest or args.output.with_suffix(
        args.output.suffix + ".manifest.json"
    )
    _atomic_json(manifest_path, manifest)
    print(
        f"wrote {len(records)} teacher rows to {args.output.resolve()} "
        f"(sha256 {output_pin['sha256']})",
        flush=True,
    )
    print(f"wrote manifest: {manifest_path.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
