#!/usr/bin/env python3
"""Build the frozen, target-opaque Generation 5 source choice probes.

The opening builder consumes only rules-generated Omega positions.  It never
decodes a score, result, PV, target, or evaluation.  A capacity-one assignment
selects exactly one position from each random trajectory *pair*, so positions
whose random histories share a seed family cannot later cross data splits.

The config builder pins the exact HCE engine, opening suite, OmegaMatch
assembly, and complete OmegaMatch runtime bundle.  OmegaMatch then searches
one move from every opening in both A/B color assignments at the frozen node
budget.  Teacher labeling is a later, separately sealed stage.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat as stat_module
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Iterator, Mapping, Sequence

import king_state_dotnet_runtime_generation5 as dotnet_runtime_contract
import king_state_generation5_runtime as runtime_contract
import omega_nnue as network_format
from omega_nnue import parse_ofen


REPO = Path(__file__).resolve().parents[2]
NETWORK_FORMAT_SOURCE = REPO / "tools/omega_nnue/omega_nnue.py"
RUNTIME_MANIFEST = runtime_contract.DEFAULT_OUTPUT
PROFILE_ID = "omega-decision-v2"
SOURCE_SEED = 2026072301
ROOT_SELECTION_SEED = 2026072302
TRAJECTORY_PAIRS = 10240
TRAJECTORIES_PER_PAIR = 2
ROOT_SAMPLER_WORKERS = 4
MAX_TRAJECTORY_PLIES = 220
POSITIONS_PER_PHASE_SIDE = 2
CAPTURE_PERCENT = 72
OPENINGS_PER_PHASE_SIDE = 896
SOURCE_NODES = 2000
PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")
STRATA = tuple((phase, side) for phase in PHASES for side in SIDES)
PHASE_WINDOWS = {
    "opening": (6, 48),
    "middlegame": (20, 140),
    "late": (40, 260),
    "endgame": (60, 400),
}
FRESHNESS_MARKER = "g5-source-2026072301"
RUN_ID = "omega-decision-v2-source-2026072301"
SOURCE_DIRECTORY = (
    REPO / "build-msvc/data-generation/omega-decision-v2/source"
)
SOURCE_CONFIG = SOURCE_DIRECTORY / "source-match.json"
FROZEN_DOTNET_MANIFEST = dotnet_runtime_contract.MANIFEST_PATH
FROZEN_DOTNET_HOST = dotnet_runtime_contract.RUNTIME_ROOT / "dotnet.exe"
FROZEN_DOTNET_VERIFIER = (
    REPO / "tools/omega_nnue/king_state_dotnet_runtime_generation5.py"
)
FROZEN_OMEGAMATCH = (
    REPO
    / "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/OmegaMatch.dll"
)
FROZEN_ENGINE = (
    REPO / "tools/omega_nnue/frozen_runtime/king-state-v5/engine/senpai.exe"
)
FROZEN_ROOT_SAMPLER = (
    REPO
    / "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/"
    "OmegaRootSampler.dll"
)
FROZEN_ROOT_SAMPLER_CHESSLIB = (
    REPO
    / "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/ChessLib.dll"
)
SOURCE_POOL = SOURCE_DIRECTORY / "rules-only-pool.jsonl"
SOURCE_POOL_MANIFEST = SOURCE_DIRECTORY / "rules-only-pool.jsonl.manifest.json"
SOURCE_POOL_SEAL = SOURCE_DIRECTORY / "rules-only-pool.jsonl.complete.seal.json"
SOURCE_MATCH_INTENT = SOURCE_DIRECTORY / "source-match.launch.intent.json"
SOURCE_MATCH_LOG = SOURCE_DIRECTORY / "source-match.console.log"
SOURCE_MATCH_COMPLETION_SEAL = (
    SOURCE_DIRECTORY / "source-match.complete.seal.json"
)
EXPECTED_SOURCE_OPENINGS = OPENINGS_PER_PHASE_SIDE * len(STRATA)
EXPECTED_SOURCE_GAMES = EXPECTED_SOURCE_OPENINGS * 2
MATCH_ESSENTIAL_OUTPUT_NAMES = (
    "config.resolved.json",
    "manifest.json",
    "events.jsonl",
    "summary.json",
    "summary.csv",
    "sequential-gate.json",
    "sequential-gate.csv",
    "development-style-games.json",
    "development-style-games.csv",
    "development-style-summary.csv",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PAIR_ID = re.compile(r"^random-pair-[0-9]{6}$")
TRAJECTORY_ID = re.compile(r"^random-pair-[0-9]{6}-(ab|ba)$")
RECORD_FIELDS = {
    "schemaVersion",
    "kind",
    "generatorSeed",
    "trajectorySeed",
    "trajectoryPairId",
    "trajectoryId",
    "flavor",
    "ply",
    "phase",
    "sideToMove",
    "ofen",
    "pieceCount",
    "whitePieces",
    "blackPieces",
    "champions",
    "wizards",
    "halfmoveClock",
    "selectionRank",
}
FORBIDDEN_KEYS = (
    "score",
    "target",
    "result",
    "eval",
    "value",
    "label",
    "bestmove",
    "bestMove",
    "pv",
    "mate",
    "bound",
    "win",
    "loss",
)
TARGET_LIKE_KEY = re.compile(
    r"(?:^|[_-])(score|target|label|evaluation|eval|outcome|result|winner|mate)"
    r"(?:$|[_-])",
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


def _key_tokens(value: Any) -> tuple[str, ...]:
    """Tokenize snake/kebab/camel/acronym keys for target quarantine.

    This intentionally recognizes plural forms too.  A spelling such as
    ``rootScoresCp`` must not bypass the pre-label boundary merely because the
    older singular regular expression did not see an underscore after score.
    """

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number {value!r}")


def _decode_json(text: str, label: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label}: {error}") from error


def _load_json(path: Path) -> dict[str, Any]:
    value = _decode_json(path.read_text(encoding="utf-8"), str(path))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object")
    return value


def _jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = _decode_json(line, f"{path}:{line_number}")
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            yield line_number, value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "sha256": _sha256(path)}


def _identity_matches(record: Any, actual: Mapping[str, Any]) -> bool:
    return (
        isinstance(record, Mapping)
        and record.get("bytes") == actual.get("bytes")
        and record.get("sha256") == actual.get("sha256")
    )


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _atomic_create(path: Path, payload: bytes) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to replace {path}")
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # The prior existence check is repeated immediately before the
        # non-replacing publication.  os.link gives Windows and POSIX the same
        # fail-if-present commit behavior; the temporary inode is then removed.
        if path.exists():
            raise FileExistsError(f"refusing to replace {path}")
        os.link(temporary_name, path)
        os.unlink(temporary_name)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _phase_for_piece_count(count: int) -> str | None:
    if count >= 37:
        return "opening"
    if count >= 25:
        return "middlegame"
    if count >= 13:
        return "late"
    if count >= 7:
        return "endgame"
    return None


def _normalize_ofen(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("OFEN must be a string")
    fields = value.split()
    if len(fields) != 6 or fields[1].lower() not in SIDES:
        raise ValueError("expected a six-field OFEN with side w or b")
    if "[" not in fields[0] or "]" not in fields[0]:
        raise ValueError("Omega OFEN lacks corner-square fields")
    normalized = " ".join(fields)
    parse_ofen(normalized)
    return normalized


def _has_target_like_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _is_target_like_key(key):
                return True
            if _has_target_like_key(item):
                return True
    elif isinstance(value, list):
        return any(_has_target_like_key(item) for item in value)
    return False


def _mix64(value: int) -> int:
    mask = (1 << 64) - 1
    value = (value + 0x9E3779B97F4A7C15) & mask
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
    return (value ^ (value >> 31)) & mask


def _trajectory_seed(pair_index: int, flavor: str) -> int:
    flavor_mask = (
        0xA0761D6478BD642F if flavor == "ab" else 0xE7037ED1A0B428DB
    )
    return _mix64(
        SOURCE_SEED
        ^ (((pair_index + 1) * 0x9E3779B97F4A7C15) & ((1 << 64) - 1))
        ^ flavor_mask
    )


@dataclass(frozen=True)
class Candidate:
    pair_id: str
    trajectory_id: str
    phase: str
    side: str
    ofen: str
    selection_rank: str
    ply: int

    def rank(self) -> str:
        payload = (
            f"omega-g5-source-opening-v1\0{SOURCE_SEED}\0{self.pair_id}\0"
            f"{self.trajectory_id}\0{self.phase}\0{self.side}\0{self.ply}\0"
            f"{self.ofen}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class _Edge:
    to: int
    reverse: int
    capacity: int
    original_capacity: int
    assignment: tuple[str, tuple[str, str]] | None = None


class _Dinic:
    def __init__(self, nodes: int) -> None:
        self.graph: list[list[_Edge]] = [[] for _ in range(nodes)]

    def add(
        self,
        left: int,
        right: int,
        capacity: int,
        assignment: tuple[str, tuple[str, str]] | None = None,
    ) -> _Edge:
        forward = _Edge(right, len(self.graph[right]), capacity, capacity, assignment)
        backward = _Edge(left, len(self.graph[left]), 0, 0)
        self.graph[left].append(forward)
        self.graph[right].append(backward)
        return forward

    def maximum_flow(self, source: int, sink: int) -> int:
        flow = 0
        while True:
            level = [-1] * len(self.graph)
            level[source] = 0
            queue: deque[int] = deque([source])
            while queue:
                node = queue.popleft()
                for edge in self.graph[node]:
                    if edge.capacity and level[edge.to] < 0:
                        level[edge.to] = level[node] + 1
                        queue.append(edge.to)
            if level[sink] < 0:
                return flow
            cursor = [0] * len(self.graph)

            def send(node: int, amount: int) -> int:
                if node == sink:
                    return amount
                while cursor[node] < len(self.graph[node]):
                    edge = self.graph[node][cursor[node]]
                    if edge.capacity and level[edge.to] == level[node] + 1:
                        pushed = send(edge.to, min(amount, edge.capacity))
                        if pushed:
                            edge.capacity -= pushed
                            self.graph[edge.to][edge.reverse].capacity += pushed
                            return pushed
                    cursor[node] += 1
                return 0

            while True:
                pushed = send(source, 1 << 30)
                if not pushed:
                    break
                flow += pushed


def _assign_pairs(
    by_pair_stratum: Mapping[tuple[str, tuple[str, str]], Sequence[Candidate]],
    *,
    quota: int,
) -> dict[str, tuple[str, str]]:
    pairs = sorted(
        {key[0] for key in by_pair_stratum},
        key=lambda value: hashlib.sha256(
            f"omega-g5-source-pair-rank-v1\0{SOURCE_SEED}\0{value}".encode()
        ).hexdigest(),
    )
    source = 0
    pair_offset = 1
    stratum_offset = pair_offset + len(pairs)
    sink = stratum_offset + len(STRATA)
    flow = _Dinic(sink + 1)
    assignment_edges: list[_Edge] = []
    for pair_index, pair_id in enumerate(pairs):
        node = pair_offset + pair_index
        flow.add(source, node, 1)
        for stratum_index, stratum in enumerate(STRATA):
            if (pair_id, stratum) in by_pair_stratum:
                assignment_edges.append(
                    flow.add(
                        node,
                        stratum_offset + stratum_index,
                        1,
                        (pair_id, stratum),
                    )
                )
    for index, _ in enumerate(STRATA):
        flow.add(stratum_offset + index, sink, quota)
    required = quota * len(STRATA)
    achieved = flow.maximum_flow(source, sink)
    if achieved != required:
        offered = {
            f"{phase}/{side}": len(
                {pair for pair, item in by_pair_stratum if item == (phase, side)}
            )
            for phase, side in STRATA
        }
        raise ValueError(
            f"cannot assign {required} one-per-pair openings; flow={achieved}; "
            f"offered={offered}"
        )
    selected: dict[str, tuple[str, str]] = {}
    for edge in assignment_edges:
        if edge.original_capacity == 1 and edge.capacity == 0:
            assert edge.assignment is not None
            pair_id, stratum = edge.assignment
            if pair_id in selected:
                raise AssertionError("flow selected one pair twice")
            selected[pair_id] = stratum
    if len(selected) != required:
        raise AssertionError("flow extraction disagrees with maximum flow")
    return selected


def _validate_pool_manifest(
    manifest: Mapping[str, Any],
    *,
    pool_identity: Mapping[str, Any],
    sampler_identity: Mapping[str, Any],
    chesslib_identity: Mapping[str, Any],
) -> None:
    if set(manifest) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "policy",
        "coverage",
        "runtime",
        "output",
        "finalStageSeal",
    }:
        raise ValueError("rules-only pool manifest field inventory changed")
    if _has_target_like_key(manifest):
        raise ValueError("rules-only pool manifest contains a target-like field")
    if manifest.get("schemaVersion") != 1 or manifest.get("kind") != (
        "omega-rules-only-random-root-manifest"
    ) or manifest.get("finalStageSeal") is not False:
        raise ValueError("unexpected rules-only pool manifest kind/version")
    policy = manifest.get("policy")
    if not isinstance(policy, Mapping):
        raise ValueError("rules-only pool manifest lacks policy")
    expected = {
        "deterministicPrng": "SplitMix64",
        "seed": str(SOURCE_SEED),
        "trajectoryPairs": TRAJECTORY_PAIRS,
        "independentTrajectoriesPerPair": TRAJECTORIES_PER_PAIR,
        "workers": ROOT_SAMPLER_WORKERS,
        "maxPlies": MAX_TRAJECTORY_PLIES,
        "positionsPerPhaseAndSide": POSITIONS_PER_PHASE_SIDE,
        "captureSelectionPercent": CAPTURE_PERCENT,
        "terminalRootsEmitted": 0,
        "maximumHalfmoveClock": 89,
        "minimumPieces": 7,
        "minimumPiecesPerSide": 2,
    }
    for key, value in expected.items():
        if policy.get(key) != value:
            raise ValueError(
                f"rules-only pool policy {key} changed: {policy.get(key)!r}"
            )
    if policy.get("phasePlyWindows") != {
        phase: list(window) for phase, window in PHASE_WINDOWS.items()
    }:
        raise ValueError("rules-only phase/ply windows changed")
    if not _identity_matches(manifest.get("output"), pool_identity):
        raise ValueError("rules-only manifest does not pin the pool")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("rules-only manifest lacks runtime identities")
    if not _identity_matches(runtime.get("samplerAssembly"), sampler_identity):
        raise ValueError("rules-only pool used a different sampler assembly")
    if not _identity_matches(runtime.get("chessLibAssembly"), chesslib_identity):
        raise ValueError("rules-only pool used a different ChessLib assembly")


def _validate_pool_seal(
    seal: Mapping[str, Any],
    *,
    pool_identity: Mapping[str, Any],
    manifest_identity: Mapping[str, Any],
    sampler_identity: Mapping[str, Any],
    chesslib_identity: Mapping[str, Any],
) -> None:
    if set(seal) != {
        "schemaVersion",
        "kind",
        "createdUtc",
        "output",
        "manifest",
        "producer",
        "finalStageSeal",
    }:
        raise ValueError("rules-only completion seal field inventory changed")
    if _has_target_like_key(seal):
        raise ValueError("rules-only completion seal contains a target-like field")
    if (
        seal.get("schemaVersion") != 1
        or seal.get("kind") != "omega-rules-only-random-root-completion-seal"
        or seal.get("finalStageSeal") is not True
    ):
        raise ValueError("rules-only pool lacks its final completion seal")
    if not _identity_matches(seal.get("output"), pool_identity):
        raise ValueError("rules-only completion seal does not pin the pool")
    if not _identity_matches(seal.get("manifest"), manifest_identity):
        raise ValueError("rules-only completion seal does not pin the manifest")
    producer = seal.get("producer")
    if not isinstance(producer, Mapping):
        raise ValueError("rules-only completion seal lacks producer identities")
    if not _identity_matches(producer.get("samplerAssembly"), sampler_identity):
        raise ValueError("rules-only completion seal used a different sampler")
    if not _identity_matches(producer.get("chessLibAssembly"), chesslib_identity):
        raise ValueError("rules-only completion seal used a different ChessLib")


def _quarantine_cross_pair_duplicates(
    grouped: dict[tuple[str, tuple[str, str]], list[Candidate]],
    collided_ofens: set[str],
) -> dict[str, Any]:
    excluded_rows = 0
    affected_pairs: set[str] = set()
    if collided_ofens:
        for key, candidates in tuple(grouped.items()):
            excluded = [item for item in candidates if item.ofen in collided_ofens]
            retained = [item for item in candidates if item.ofen not in collided_ofens]
            excluded_rows += len(excluded)
            affected_pairs.update(item.pair_id for item in excluded)
            if retained:
                grouped[key] = retained
            else:
                del grouped[key]
    collision_digest = hashlib.sha256()
    for ofen in sorted(collided_ofens):
        collision_digest.update(ofen.encode("utf-8"))
        collision_digest.update(b"\n")
    result = {
        "policy": "exclude-all-copies-before-pair-assignment",
        "distinctOfens": len(collided_ofens),
        "excludedRows": excluded_rows,
        "affectedTrajectoryPairs": len(affected_pairs),
        "ofenSetSha256": collision_digest.hexdigest(),
    }
    _validate_cross_pair_duplicate_exclusion(result)
    return result


def _validate_cross_pair_duplicate_exclusion(
    value: Any,
    *,
    maximum_rows: int | None = None,
) -> None:
    fields = {
        "policy",
        "distinctOfens",
        "excludedRows",
        "affectedTrajectoryPairs",
        "ofenSetSha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("source cross-pair duplicate exclusion is malformed")
    if value.get("policy") != "exclude-all-copies-before-pair-assignment":
        raise ValueError("source cross-pair duplicate exclusion policy changed")
    integer_fields = (
        "distinctOfens",
        "excludedRows",
        "affectedTrajectoryPairs",
    )
    if any(type(value.get(field)) is not int for field in integer_fields):
        raise ValueError("source cross-pair duplicate exclusion counts are malformed")
    distinct = value["distinctOfens"]
    excluded = value["excludedRows"]
    affected = value["affectedTrajectoryPairs"]
    digest = str(value.get("ofenSetSha256", ""))
    if HEX64.fullmatch(digest) is None:
        raise ValueError("source cross-pair duplicate exclusion digest is malformed")
    if distinct < 0 or excluded < 0 or affected < 0:
        raise ValueError("source cross-pair duplicate exclusion counts are negative")
    if maximum_rows is not None and (
        type(maximum_rows) is not int
        or maximum_rows < 0
        or excluded > maximum_rows
    ):
        raise ValueError("source cross-pair duplicate exclusion exceeds the raw pool")
    if affected > TRAJECTORY_PAIRS or affected > excluded:
        raise ValueError("source cross-pair duplicate exclusion pair count is impossible")
    if distinct == 0:
        empty_digest = hashlib.sha256(b"").hexdigest()
        if excluded != 0 or affected != 0 or digest != empty_digest:
            raise ValueError("empty cross-pair duplicate exclusion is inconsistent")
    elif excluded < 2 * distinct or affected < 2:
        # Each quarantined OFEN was observed in at least two different
        # trajectory pairs.  Multiple OFENs may involve the same two pairs, so
        # affected-pair count is intentionally not compared with distinct OFENs.
        raise ValueError("nonempty cross-pair duplicate exclusion is inconsistent")


def _read_pool(
    path: Path,
) -> tuple[
    dict[tuple[str, tuple[str, str]], list[Candidate]],
    dict[str, Any],
]:
    before = _identity(path)
    grouped: dict[tuple[str, tuple[str, str]], list[Candidate]] = defaultdict(list)
    pair_trajectories: dict[str, set[str]] = defaultdict(set)
    counts: Counter[str] = Counter()
    side_counts: Counter[str] = Counter()
    promotion_counts = {piece: 0 for piece in "qrbncw"}
    maximum_ply = 0
    exact_owner: dict[str, str] = {}
    collided_ofens: set[str] = set()
    for line_number, record in _jsonl(path):
        if set(record) != RECORD_FIELDS:
            missing = sorted(RECORD_FIELDS - set(record))
            extra = sorted(set(record) - RECORD_FIELDS)
            raise ValueError(
                f"{path}:{line_number}: root field inventory changed; "
                f"missing={missing}, extra={extra}"
            )
        lowered = {str(key).lower() for key in record}
        if any(name.lower() in lowered for name in FORBIDDEN_KEYS):
            raise ValueError(f"{path}:{line_number}: target-like field present")
        if (
            record.get("schemaVersion") != 1
            or record.get("kind") != "omega-rules-only-random-root"
            or str(record.get("generatorSeed")) != str(SOURCE_SEED)
        ):
            raise ValueError(f"{path}:{line_number}: wrong pool record identity")
        pair_id = str(record.get("trajectoryPairId", ""))
        trajectory_id = str(record.get("trajectoryId", ""))
        flavor = str(record.get("flavor", ""))
        if not PAIR_ID.fullmatch(pair_id) or not TRAJECTORY_ID.fullmatch(trajectory_id):
            raise ValueError(f"{path}:{line_number}: malformed trajectory identity")
        if trajectory_id != f"{pair_id}-{flavor}" or flavor not in {"ab", "ba"}:
            raise ValueError(f"{path}:{line_number}: inconsistent trajectory flavor")
        pair_index = int(pair_id.rsplit("-", 1)[1]) - 1
        if not 0 <= pair_index < TRAJECTORY_PAIRS:
            raise ValueError(f"{path}:{line_number}: trajectory pair is out of range")
        expected_seed = _trajectory_seed(pair_index, flavor)
        if str(record.get("trajectorySeed")) != str(expected_seed):
            raise ValueError(f"{path}:{line_number}: trajectory seed mismatch")
        phase = str(record.get("phase", ""))
        side = str(record.get("sideToMove", "")).lower()
        if phase not in PHASES or side not in SIDES:
            raise ValueError(f"{path}:{line_number}: invalid phase/side")
        ofen = _normalize_ofen(record.get("ofen"))
        pieces, parsed_side, _ = parse_ofen(ofen)
        if parsed_side != side or ofen.split()[1].lower() != side:
            raise ValueError(f"{path}:{line_number}: side-to-move mismatch")
        piece_count = int(record.get("pieceCount", -1))
        if piece_count != len(pieces) or _phase_for_piece_count(piece_count) != phase:
            raise ValueError(f"{path}:{line_number}: phase/piece-count mismatch")
        white = sum(piece_side == 0 for _, piece_side, _ in pieces)
        black = len(pieces) - white
        if record.get("whitePieces") != white or record.get("blackPieces") != black:
            raise ValueError(f"{path}:{line_number}: color piece-count mismatch")
        champions = sum(piece == 6 for piece, _, _ in pieces)
        wizards = sum(piece == 7 for piece, _, _ in pieces)
        if record.get("champions") != champions or record.get("wizards") != wizards:
            raise ValueError(f"{path}:{line_number}: Champion/Wizard count mismatch")
        halfmove = int(ofen.split()[4])
        if record.get("halfmoveClock") != halfmove or not 0 <= halfmove < 90:
            raise ValueError(f"{path}:{line_number}: invalid halfmove clock")
        rank = str(record.get("selectionRank", "")).lower()
        if HEX64.fullmatch(rank) is None:
            raise ValueError(f"{path}:{line_number}: invalid selection rank")
        ply = int(record.get("ply", -1))
        minimum_ply, maximum_phase_ply = PHASE_WINDOWS[phase]
        if not minimum_ply <= ply <= min(maximum_phase_ply, MAX_TRAJECTORY_PLIES):
            raise ValueError(f"{path}:{line_number}: ply lies outside its phase window")
        rank_payload = (
            f"omega-root-sampler-v1\0{expected_seed}\0{pair_index}\0{flavor}\0"
            f"{ply}\0{ofen}"
        )
        expected_rank = hashlib.sha256(rank_payload.encode("utf-8")).hexdigest()
        if rank != expected_rank:
            raise ValueError(f"{path}:{line_number}: sampler selection rank mismatch")
        candidate = Candidate(pair_id, trajectory_id, phase, side, ofen, rank, ply)
        prior = exact_owner.setdefault(ofen, pair_id)
        if prior != pair_id:
            # Independently seeded legal trajectories can converge on the
            # same position.  Such a position cannot safely belong to either
            # trajectory-pair component, so quarantine every copy before the
            # capacity-one assignment instead of making the raw pool unusable.
            collided_ofens.add(ofen)
        grouped[(pair_id, (phase, side))].append(candidate)
        pair_trajectories[pair_id].add(trajectory_id)
        counts[f"{phase}/{side}"] += 1
        side_counts[side] += 1
        maximum_ply = max(maximum_ply, ply)
    after = _identity(path)
    if before != after:
        raise ValueError("rules-only pool changed while it was being read")
    if not grouped:
        raise ValueError("rules-only pool is empty")
    expected_pairs = {f"random-pair-{index:06d}" for index in range(1, TRAJECTORY_PAIRS + 1)}
    if set(pair_trajectories) != expected_pairs:
        raise ValueError(
            f"expected {TRAJECTORY_PAIRS} trajectory pairs, got {len(pair_trajectories)}"
        )
    # The sampler executes both flavors for every pair, but it emits only
    # retained positions.  A trajectory that terminates before reaching any
    # eligible phase window therefore has no row by design.  Pair presence is
    # still mandatory, and every observed flavor/seed was authenticated above.
    expected_trajectories = {
        f"{pair}-{flavor}"
        for pair in expected_pairs
        for flavor in ("ab", "ba")
    }
    observed_trajectories = {
        trajectory
        for trajectories in pair_trajectories.values()
        for trajectory in trajectories
    }
    zero_record_trajectory_ids = sorted(expected_trajectories - observed_trajectories)
    zero_record_trajectories = len(zero_record_trajectory_ids)
    duplicate_exclusion = _quarantine_cross_pair_duplicates(
        grouped, collided_ofens
    )
    phase_counts = {
        phase: sum(counts[f"{phase}/{side}"] for side in SIDES) for phase in PHASES
    }
    return grouped, {
        "records": sum(counts.values()),
        "phaseCounts": phase_counts,
        "sideToMoveCounts": dict(side_counts),
        "maxPlyReachedAtLeast": maximum_ply,
        "promotionSelections": promotion_counts,
        "offered": dict(sorted(counts.items())),
        "rowBearingTrajectories": len(observed_trajectories),
        "zeroRecordTrajectories": zero_record_trajectories,
        "zeroRecordTrajectoryIds": zero_record_trajectory_ids,
        "crossPairDuplicateExclusion": duplicate_exclusion,
    }


def _trajectory_coverage(
    pool_coverage: Mapping[str, Any], authenticated_coverage: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind legal zero-row trajectories to the authenticated sampler run."""

    executed = TRAJECTORY_PAIRS * TRAJECTORIES_PER_PAIR
    row_bearing = int(pool_coverage.get("rowBearingTrajectories", -1))
    zero_record = int(pool_coverage.get("zeroRecordTrajectories", -1))
    zero_ids = pool_coverage.get("zeroRecordTrajectoryIds")
    terminal = int(authenticated_coverage.get("terminalTrajectories", -1))
    if (
        not isinstance(zero_ids, list)
        or any(TRAJECTORY_ID.fullmatch(str(value)) is None for value in zero_ids)
        or zero_ids != sorted(set(zero_ids))
        or zero_record != len(zero_ids)
        or row_bearing + zero_record != executed
        or not 0 <= zero_record <= terminal
    ):
        raise ValueError("zero-record trajectory coverage is inconsistent")
    return {
        "trajectoryPairs": TRAJECTORY_PAIRS,
        "independentTrajectoriesPerPair": TRAJECTORIES_PER_PAIR,
        "samplerExecutedTrajectories": executed,
        "rowBearingTrajectories": row_bearing,
        "zeroRecordTrajectories": zero_record,
        "zeroRecordTrajectoryIds": zero_ids,
        "authenticatedTerminalTrajectories": terminal,
        "zeroRecordBound": (
            "zero-record trajectory count must not exceed authenticated "
            "terminalTrajectories"
        ),
    }


def _build_openings(args: argparse.Namespace) -> None:
    pool = args.pool.expanduser().resolve()
    manifest_path = args.pool_manifest.expanduser().resolve()
    seal_path = args.pool_seal.expanduser().resolve()
    output = args.output.expanduser().resolve()
    sampler = args.sampler_assembly.expanduser().resolve()
    chesslib = args.chesslib_assembly.expanduser().resolve()
    imported_network_format = Path(network_format.__file__).resolve()
    if imported_network_format != NETWORK_FORMAT_SOURCE.resolve():
        raise ValueError(
            "imported omega_nnue module is outside the canonical repository path"
        )
    runtime_contract.verify_manifest(RUNTIME_MANIFEST)
    runtime_pin = _identity(RUNTIME_MANIFEST)
    if output.exists():
        raise FileExistsError(f"refusing to replace {output}")
    pool_pin = _identity(pool)
    sampler_pin = _identity(sampler)
    chesslib_pin = _identity(chesslib)
    manifest_before = _identity(manifest_path)
    seal_before = _identity(seal_path)
    manifest = _load_json(manifest_path)
    _validate_pool_manifest(
        manifest,
        pool_identity=pool_pin,
        sampler_identity=sampler_pin,
        chesslib_identity=chesslib_pin,
    )
    seal = _load_json(seal_path)
    _validate_pool_seal(
        seal,
        pool_identity=pool_pin,
        manifest_identity=manifest_before,
        sampler_identity=sampler_pin,
        chesslib_identity=chesslib_pin,
    )
    if _identity(manifest_path) != manifest_before:
        raise ValueError("rules-only pool manifest changed while read")
    if _identity(seal_path) != seal_before:
        raise ValueError("rules-only pool completion seal changed while read")
    grouped, pool_coverage = _read_pool(pool)
    coverage = manifest.get("coverage")
    if not isinstance(coverage, Mapping) or set(coverage) != {
        "records",
        "phaseCounts",
        "sideToMoveCounts",
        "terminalTrajectories",
        "maxPlyReached",
        "promotionSelections",
        "enPassantClassification",
    }:
        raise ValueError("rules-only coverage field inventory changed")
    for name in ("records", "phaseCounts", "sideToMoveCounts"):
        if coverage.get(name) != pool_coverage[name]:
            raise ValueError(f"rules-only coverage {name} does not match pool rows")
    trajectory_coverage = _trajectory_coverage(pool_coverage, coverage)
    if int(coverage.get("maxPlyReached", -1)) < pool_coverage["maxPlyReachedAtLeast"]:
        raise ValueError("rules-only coverage max ply is below an emitted root")
    promotions = coverage.get("promotionSelections")
    if not isinstance(promotions, Mapping) or set(promotions) != set("qrbncw"):
        raise ValueError("rules-only promotion coverage changed")
    if coverage.get("enPassantClassification") != (
        "An en-passant move lands on an empty target and remains in the ordinary "
        "move pool; legality still comes from ChessLib."
    ):
        raise ValueError("rules-only en-passant classification changed")
    assignments = _assign_pairs(grouped, quota=OPENINGS_PER_PHASE_SIDE)
    selected: list[Candidate] = []
    for pair_id, stratum in assignments.items():
        candidates = sorted(grouped[(pair_id, stratum)], key=Candidate.rank)
        selected.append(candidates[0])
    selected.sort(
        key=lambda item: (
            PHASES.index(item.phase),
            SIDES.index(item.side),
            item.rank(),
        )
    )
    counts = Counter((item.phase, item.side) for item in selected)
    if any(counts[stratum] != OPENINGS_PER_PHASE_SIDE for stratum in STRATA):
        raise AssertionError("balanced source opening quota was not met")
    if len({item.pair_id for item in selected}) != len(selected):
        raise AssertionError("more than one opening was selected from a trajectory pair")
    if len({item.ofen for item in selected}) != len(selected):
        raise AssertionError("selected source openings contain an exact duplicate")
    ordinal: Counter[tuple[str, str]] = Counter()
    openings: list[dict[str, Any]] = []
    for item in selected:
        key = (item.phase, item.side)
        ordinal[key] += 1
        opening_id = (
            f"g5-{item.phase}-{item.side}-{ordinal[key]:04d}-"
            f"{item.rank()[:12]}"
        )
        openings.append(
            {
                "id": opening_id,
                "source": f"rules-only-pair:{item.pair_id}",
                "initialOfen": item.ofen,
                "moves": [],
            }
        )
    suite = {
        "schemaVersion": 1,
        "name": "Omega NNUE Generation 5 target-opaque source openings",
        "rootSamplerSha256": sampler_pin["sha256"],
        "rootSamplerChessLibSha256": chesslib_pin["sha256"],
        "rootPoolSha256": pool_pin["sha256"],
        "rootPoolManifestSha256": manifest_before["sha256"],
        "rootPoolSealSha256": seal_before["sha256"],
        "sourceBuilderSha256": _sha256(Path(__file__).resolve()),
        "networkFormatSha256": _sha256(imported_network_format),
        "pythonRuntimeManifestSha256": runtime_pin["sha256"],
        "trajectoryCoverage": trajectory_coverage,
        "crossPairDuplicateExclusion": pool_coverage[
            "crossPairDuplicateExclusion"
        ],
        "openings": openings,
    }
    # Recheck immutable inputs immediately before publication.
    if (
        _identity(pool) != pool_pin
        or _identity(manifest_path) != manifest_before
        or _identity(seal_path) != seal_before
    ):
        raise ValueError("source-pool inputs changed before publication")
    _atomic_create(output, _canonical_json(suite))
    print(
        f"Wrote {len(openings)} target-opaque openings "
        f"({OPENINGS_PER_PHASE_SIDE} per phase/side): {output}"
    )
    print(f"Pool offered: {pool_coverage['offered']}")
    print(
        "Cross-pair duplicate quarantine: "
        f"{pool_coverage['crossPairDuplicateExclusion']}"
    )
    print(f"Opening suite SHA-256: {_sha256(output)}")


def _bundle_identity(assembly: Path) -> dict[str, Any]:
    assembly = assembly.expanduser().resolve()
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
        (path for path in root.rglob("*") if path.is_file() and included(path)),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if assembly not in files:
        raise ValueError("OmegaMatch.dll is absent from its runtime bundle")
    digest = hashlib.sha256()
    members = []
    for path in files:
        identity = _identity(path)
        relative = path.relative_to(root).as_posix()
        digest.update(
            f"{relative}\t{identity['bytes']}\t{identity['sha256']}\n".encode(
                "utf-8"
            )
        )
        members.append(
            {
                "relativePath": relative,
                "bytes": identity["bytes"],
                "sha256": identity["sha256"],
            }
        )
    return {"sha256": digest.hexdigest(), "members": members}


def _canonical_path(value: Path, expected: Path, label: str) -> Path:
    actual = value.expanduser().resolve()
    expected = expected.expanduser().resolve()
    if actual != expected:
        raise ValueError(f"{label} must be canonical: {expected}")
    return actual


def _require_canonical_source_match_args(args: argparse.Namespace) -> None:
    for attribute, expected, label in (
        ("config", SOURCE_CONFIG, "source config"),
        ("harness", FROZEN_OMEGAMATCH, "OmegaMatch assembly"),
        ("root_sampler", FROZEN_ROOT_SAMPLER, "root sampler"),
        (
            "root_sampler_chesslib",
            FROZEN_ROOT_SAMPLER_CHESSLIB,
            "root-sampler ChessLib",
        ),
        ("pool", SOURCE_POOL, "rules-only pool"),
        ("pool_manifest", SOURCE_POOL_MANIFEST, "rules-only pool manifest"),
        ("pool_seal", SOURCE_POOL_SEAL, "rules-only pool completion seal"),
    ):
        _canonical_path(getattr(args, attribute), expected, label)


_MANAGED_ENVIRONMENT_CLEARED_PREFIXES = (
    "DOTNET_",
    "COMPLUS_",
    "COREHOST_",
    "CORECLR_",
    "COR_",
    "DYLD_",
)
_MANAGED_ENVIRONMENT_CLEARED_NAMES = ("LD_PRELOAD", "LD_AUDIT")


def _managed_environment() -> tuple[dict[str, str], dict[str, Any]]:
    environment = dict(os.environ)
    for name in tuple(environment):
        upper = name.upper()
        if upper.startswith(_MANAGED_ENVIRONMENT_CLEARED_PREFIXES) or upper in (
            _MANAGED_ENVIRONMENT_CLEARED_NAMES
        ):
            del environment[name]
    overrides = {
        "DOTNET_ROOT": str(dotnet_runtime_contract.RUNTIME_ROOT.resolve()),
        "DOTNET_MULTILEVEL_LOOKUP": "0",
        "DOTNET_NOLOGO": "1",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
    }
    environment.update(overrides)
    policy = {
        "clearedPrefixes": list(_MANAGED_ENVIRONMENT_CLEARED_PREFIXES),
        "clearedNames": list(_MANAGED_ENVIRONMENT_CLEARED_NAMES),
        "set": overrides,
    }
    return environment, policy


def _source_match_commands(config: Path) -> dict[str, list[str]]:
    prefix = [
        str(FROZEN_DOTNET_HOST.resolve()),
        str(FROZEN_OMEGAMATCH.resolve()),
    ]
    return {
        "validate": [*prefix, "validate", "--config", str(config.resolve())],
        "run": [*prefix, "run", "--config", str(config.resolve())],
    }


def _source_match_input_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    _require_canonical_source_match_args(args)
    for directory, label in (
        (SOURCE_DIRECTORY, "canonical source directory"),
        (dotnet_runtime_contract.RUNTIME_ROOT, "frozen dotnet runtime"),
        (FROZEN_OMEGAMATCH.parent, "frozen OmegaMatch bundle"),
        (FROZEN_ENGINE.parent, "frozen engine bundle"),
        (FROZEN_ROOT_SAMPLER.parent, "frozen root-sampler bundle"),
    ):
        _require_plain_directory(directory, label)
    _verify(args)
    dotnet_manifest = dotnet_runtime_contract.verify_manifest(
        FROZEN_DOTNET_MANIFEST
    )
    if Path(dotnet_runtime_contract.__file__).resolve() != FROZEN_DOTNET_VERIFIER.resolve():
        raise ValueError("loaded a noncanonical Generation 5 dotnet verifier")
    dotnet_host = _canonical_path(
        FROZEN_DOTNET_HOST,
        dotnet_runtime_contract.RUNTIME_ROOT / "dotnet.exe",
        "frozen dotnet host",
    )
    config_path = SOURCE_CONFIG.resolve()
    config = _load_json(config_path)
    match = config["match"]
    openings = (config_path.parent / str(match["openingsFile"])).resolve()
    engines = config["engines"]
    engine_paths = {
        (config_path.parent / str(record["executable"])).resolve()
        for record in engines
    }
    if engine_paths != {FROZEN_ENGINE.resolve()}:
        raise ValueError("source config does not use the canonical frozen HCE engine")
    for path, label in (
        (Path(__file__), "source builder"),
        (RUNTIME_MANIFEST, "Python runtime manifest"),
        (config_path, "source config"),
        (openings, "source openings"),
        (FROZEN_ENGINE, "frozen HCE engine"),
        (FROZEN_ROOT_SAMPLER, "frozen root sampler"),
        (FROZEN_ROOT_SAMPLER_CHESSLIB, "frozen root-sampler ChessLib"),
        (SOURCE_POOL, "rules-only pool"),
        (SOURCE_POOL_MANIFEST, "rules-only pool manifest"),
        (SOURCE_POOL_SEAL, "rules-only pool completion seal"),
        (dotnet_host, "frozen dotnet host"),
        (FROZEN_DOTNET_MANIFEST, "frozen dotnet manifest"),
        (FROZEN_DOTNET_VERIFIER, "frozen dotnet verifier"),
        (FROZEN_OMEGAMATCH, "frozen OmegaMatch assembly"),
    ):
        _require_plain_file(path.resolve(), label)
    harness_bundle = _bundle_identity(FROZEN_OMEGAMATCH)
    snapshot: dict[str, Any] = {
        "sourceBuilder": _identity(Path(__file__)),
        "pythonRuntimeManifest": _identity(RUNTIME_MANIFEST),
        "config": _identity(config_path),
        "openings": _identity(openings),
        "engine": _identity(FROZEN_ENGINE),
        "rootSampler": _identity(FROZEN_ROOT_SAMPLER),
        "rootSamplerChessLib": _identity(FROZEN_ROOT_SAMPLER_CHESSLIB),
        "rootPool": _identity(SOURCE_POOL),
        "rootPoolManifest": _identity(SOURCE_POOL_MANIFEST),
        "rootPoolCompletionSeal": _identity(SOURCE_POOL_SEAL),
        "dotnetHost": _identity(dotnet_host),
        "dotnetRuntimeManifest": _identity(FROZEN_DOTNET_MANIFEST),
        "dotnetRuntimeVerifier": _identity(FROZEN_DOTNET_VERIFIER),
        "dotnetRuntimeVersion": dotnet_manifest["runtimeVersion"],
        "dotnetRuntimeBundleSha256": dotnet_manifest["bundleSha256"],
        "omegaMatchAssembly": _identity(FROZEN_OMEGAMATCH),
        "omegaMatchBundle": harness_bundle,
    }
    pool_freeze = SOURCE_DIRECTORY / "rules-only-pool.jsonl.freeze.json"
    if pool_freeze.is_file():
        _require_plain_file(pool_freeze, "rules-only pool freeze")
        snapshot["rootPoolFreeze"] = _identity(pool_freeze)
    return snapshot


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def _require_plain_directory(path: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path.expanduser()))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if not current.exists():
            raise FileNotFoundError(current)
        if current.is_symlink() or _is_reparse_point(current):
            raise ValueError(f"{label} has a symlink/reparse ancestor: {current}")
    if not absolute.is_dir():
        raise ValueError(f"{label} is not a directory: {absolute}")


def _require_plain_file(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink() or _is_reparse_point(path):
        raise ValueError(f"{label} is not a plain file: {path}")


_SUMMARY_COMPLETION_FIELDS = {
    "RunId": str,
    "EngineA": str,
    "EngineB": str,
    "Games": int,
    "PairedGames": int,
    "UnpairedGames": int,
    "Pairs": int,
    "IncompletePairs": int,
    "IllegalMoves": int,
    "IllegalPvs": int,
    "ProtocolFailures": int,
    "TimeForfeits": int,
}
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _json_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _json_string_end(text: str, index: int, label: str) -> int:
    if index >= len(text) or text[index] != '"':
        raise ValueError(f"{label}: expected a JSON string")
    index += 1
    while index < len(text):
        character = text[index]
        if character == '"':
            return index + 1
        if ord(character) < 0x20:
            raise ValueError(f"{label}: control character in JSON string")
        if character == "\\":
            index += 1
            if index >= len(text):
                break
            escape = text[index]
            if escape == "u":
                digits = text[index + 1 : index + 5]
                if len(digits) != 4 or any(
                    digit not in "0123456789abcdefABCDEF" for digit in digits
                ):
                    raise ValueError(f"{label}: malformed JSON unicode escape")
                index += 4
            elif escape not in '"\\/bfnrt':
                raise ValueError(f"{label}: malformed JSON escape")
        index += 1
    raise ValueError(f"{label}: unterminated JSON string")


def _json_skip_value(text: str, index: int, label: str) -> int:
    index = _json_space(text, index)
    if index >= len(text):
        raise ValueError(f"{label}: missing JSON value")
    character = text[index]
    if character == '"':
        return _json_string_end(text, index, label)
    if character == "{":
        index = _json_space(text, index + 1)
        keys: set[str] = set()
        if index < len(text) and text[index] == "}":
            return index + 1
        while True:
            key_start = index
            key_end = _json_string_end(text, key_start, label)
            key = _decode_json(text[key_start:key_end], label)
            if type(key) is not str or key in keys:
                raise ValueError(f"{label}: duplicate or malformed object key")
            keys.add(key)
            index = _json_space(text, key_end)
            if index >= len(text) or text[index] != ":":
                raise ValueError(f"{label}: expected ':'")
            index = _json_skip_value(text, index + 1, label)
            index = _json_space(text, index)
            if index < len(text) and text[index] == "}":
                return index + 1
            if index >= len(text) or text[index] != ",":
                raise ValueError(f"{label}: expected ',' or '}}'")
            index = _json_space(text, index + 1)
    if character == "[":
        index = _json_space(text, index + 1)
        if index < len(text) and text[index] == "]":
            return index + 1
        while True:
            index = _json_skip_value(text, index, label)
            index = _json_space(text, index)
            if index < len(text) and text[index] == "]":
                return index + 1
            if index >= len(text) or text[index] != ",":
                raise ValueError(f"{label}: expected ',' or ']'")
            index = _json_space(text, index + 1)
    for literal in ("true", "false", "null"):
        if text.startswith(literal, index):
            return index + len(literal)
    number = _JSON_NUMBER.match(text, index)
    if number is not None:
        return number.end()
    raise ValueError(f"{label}: malformed JSON value")


def _read_summary_completion_contract(path: Path) -> dict[str, Any]:
    """Read only allowlisted schedule/safety scalars from OmegaMatch summary.

    Outcome, score, gate, PV, and development values are deliberately never
    JSON-decoded by this pre-label source runner.
    """

    label = str(path)
    text = path.read_text(encoding="utf-8")
    index = _json_space(text, 0)
    if index >= len(text) or text[index] != "{":
        raise ValueError("summary is not a JSON object")
    index = _json_space(text, index + 1)
    values: dict[str, Any] = {}
    keys: set[str] = set()
    if index < len(text) and text[index] == "}":
        index += 1
    else:
        while True:
            key_start = index
            key_end = _json_string_end(text, key_start, label)
            name = _decode_json(text[key_start:key_end], label)
            if type(name) is not str or name in keys:
                raise ValueError("summary has a duplicate or malformed top-level key")
            keys.add(name)
            index = _json_space(text, key_end)
            if index >= len(text) or text[index] != ":":
                raise ValueError("summary lacks ':' after a top-level key")
            value_start = _json_space(text, index + 1)
            value_end = _json_skip_value(text, value_start, label)
            if name in _SUMMARY_COMPLETION_FIELDS:
                value = _decode_json(text[value_start:value_end], f"{path}:{name}")
                expected_type = _SUMMARY_COMPLETION_FIELDS[name]
                if type(value) is not expected_type:
                    raise ValueError(f"summary {name} has the wrong scalar type")
                values[name] = value
            index = _json_space(text, value_end)
            if index < len(text) and text[index] == "}":
                index += 1
                break
            if index >= len(text) or text[index] != ",":
                raise ValueError("summary lacks ',' or '}' after a top-level value")
            index = _json_space(text, index + 1)
    if _json_space(text, index) != len(text):
        raise ValueError("summary has trailing content")
    if set(values) != set(_SUMMARY_COMPLETION_FIELDS):
        missing = sorted(set(_SUMMARY_COMPLETION_FIELDS) - set(values))
        raise ValueError(f"summary lacks completion scalars: {missing}")
    return values


def _validate_summary_completion_contract(values: Mapping[str, Any]) -> None:
    expected = {
        "RunId": RUN_ID,
        "EngineA": "hce-a",
        "EngineB": "hce-b",
        "Games": EXPECTED_SOURCE_GAMES,
        "PairedGames": EXPECTED_SOURCE_GAMES,
        "UnpairedGames": 0,
        "Pairs": EXPECTED_SOURCE_OPENINGS,
        "IncompletePairs": 0,
        "IllegalMoves": 0,
        "IllegalPvs": 0,
        "ProtocolFailures": 0,
        "TimeForfeits": 0,
    }
    if dict(values) != expected:
        raise ValueError("source match is incomplete or has a nonzero safety count")


def _source_game_name(opening_id: str, color_order: str) -> str:
    """Return OmegaMatch's exact repeat-one PGN filename."""

    if not opening_id or color_order not in ("ab", "ba"):
        raise ValueError("source game filename inputs are malformed")
    return f"{opening_id}-r001-{color_order}.pgn"


def _expected_game_names(openings: Path) -> set[str]:
    suite = _load_json(openings)
    rows = suite.get("openings")
    if not isinstance(rows, list) or len(rows) != EXPECTED_SOURCE_OPENINGS:
        raise ValueError("source opening inventory changed before game audit")
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or type(row.get("id")) is not str:
            raise ValueError("source opening lacks an ID before game audit")
        opening_id = row["id"]
        for order in ("ab", "ba"):
            name = _source_game_name(opening_id, order)
            if name in names:
                raise ValueError("source opening IDs do not define unique game files")
            names.add(name)
    return names


def _games_tree_identity(
    games: Path, expected_names: set[str]
) -> dict[str, Any]:
    if not games.is_dir() or games.is_symlink() or _is_reparse_point(games):
        raise ValueError("source games tree is absent or is a reparse point")
    entries = sorted(games.iterdir(), key=lambda path: path.name)
    actual_names = {path.name for path in entries}
    if actual_names != expected_names:
        raise ValueError("source games tree does not exactly match the schedule")
    digest = hashlib.sha256()
    total_bytes = 0
    for path in entries:
        _require_plain_file(path, "source game")
        if path.suffix.lower() != ".pgn":
            raise ValueError(f"non-PGN source game output: {path}")
        identity = _identity(path)
        relative = f"games/{path.name}"
        digest.update(
            f"{relative}\t{identity['bytes']}\t{identity['sha256']}\n".encode(
                "utf-8"
            )
        )
        total_bytes += identity["bytes"]
    return {
        "path": str(games.resolve()),
        "files": len(entries),
        "bytes": total_bytes,
        "sha256": digest.hexdigest(),
    }


def _verify_omegamatch_manifest(
    path: Path, inputs: Mapping[str, Any]
) -> None:
    manifest = _load_json(path)
    if (
        manifest.get("RecordType") != "run"
        or manifest.get("RunId") != RUN_ID
        or manifest.get("ProfileId") != PROFILE_ID
        or manifest.get("FreshnessMarker") != FRESHNESS_MARKER
        or manifest.get("ConfigSha256") != inputs["config"]["sha256"]
        or manifest.get("OpeningSuiteSha256") != inputs["openings"]["sha256"]
        or manifest.get("HarnessSha256")
        != inputs["omegaMatchAssembly"]["sha256"]
        or manifest.get("HarnessBundleSha256")
        != inputs["omegaMatchBundle"]["sha256"]
        or manifest.get("Runtime")
        != f".NET {inputs['dotnetRuntimeVersion']}"
        or manifest.get("Seed") != SOURCE_SEED
    ):
        raise ValueError("OmegaMatch run manifest differs from the frozen source run")
    match = manifest.get("Match")
    if not isinstance(match, Mapping) or (
        match.get("EngineA") != "hce-a"
        or match.get("EngineB") != "hce-b"
        or Path(str(match.get("OpeningsFile", ""))).resolve()
        != Path(inputs["openings"]["path"]).resolve()
        or match.get("Repeats") != 1
        or match.get("MaxPlies") != 1
        or match.get("AbsoluteMaxPlies") != 1
        or match.get("Mode") != "nodes"
        or match.get("Nodes") != SOURCE_NODES
        or match.get("FreshProcessPerGame") is not True
    ):
        raise ValueError("OmegaMatch manifest schedule changed")
    engines = manifest.get("Engines")
    if not isinstance(engines, list) or len(engines) != 2:
        raise ValueError("OmegaMatch manifest engine inventory changed")
    for expected_id, engine in zip(("hce-a", "hce-b"), engines):
        if not isinstance(engine, Mapping) or (
            engine.get("Id") != expected_id
            or Path(str(engine.get("Executable", ""))).resolve()
            != Path(inputs["engine"]["path"]).resolve()
            or engine.get("Sha256") != inputs["engine"]["sha256"]
            or engine.get("OmegaNnueActiveVerified") is not False
        ):
            raise ValueError("OmegaMatch manifest did not verify frozen HCE-only engines")


def _source_match_output_snapshot(
    inputs: Mapping[str, Any], *, allow_completion_seal: bool
) -> dict[str, Any]:
    essentials: dict[str, Any] = {}
    for name in MATCH_ESSENTIAL_OUTPUT_NAMES:
        path = SOURCE_DIRECTORY / name
        _require_plain_file(path, f"essential source output {name}")
        essentials[name] = _identity(path)
    _verify_omegamatch_manifest(SOURCE_DIRECTORY / "manifest.json", inputs)
    summary_contract = _read_summary_completion_contract(
        SOURCE_DIRECTORY / "summary.json"
    )
    _validate_summary_completion_contract(summary_contract)
    games = _games_tree_identity(
        SOURCE_DIRECTORY / "games",
        _expected_game_names(Path(inputs["openings"]["path"])),
    )
    _require_plain_file(SOURCE_MATCH_LOG, "source match console log")
    allowed = {
        "rules-only-pool.jsonl",
        "rules-only-pool.jsonl.manifest.json",
        "rules-only-pool.jsonl.complete.seal.json",
        "rules-only-pool.jsonl.freeze.json",
        "openings.json",
        "source-match.json",
        "source-match.launch.intent.json",
        "source-match.console.log",
        "source-match.complete.seal.json",
        "games",
        *MATCH_ESSENTIAL_OUTPUT_NAMES,
    }
    names = {path.name for path in SOURCE_DIRECTORY.iterdir()}
    if names - allowed:
        raise ValueError(
            f"unexpected source-match outputs: {sorted(names - allowed)}"
        )
    if not allow_completion_seal and SOURCE_MATCH_COMPLETION_SEAL.exists():
        raise FileExistsError("source-match completion seal already exists")
    return {
        "essential": essentials,
        "gamesTree": games,
        "consoleLog": _identity(SOURCE_MATCH_LOG),
        "summaryContract": summary_contract,
        "targetDataDecoded": False,
    }


def _assert_source_match_unstarted() -> None:
    _require_plain_directory(SOURCE_DIRECTORY, "canonical source directory")
    required_inputs = {
        "rules-only-pool.jsonl",
        "rules-only-pool.jsonl.manifest.json",
        "rules-only-pool.jsonl.complete.seal.json",
        "openings.json",
        "source-match.json",
    }
    optional_inputs = {"rules-only-pool.jsonl.freeze.json"}
    entries = list(SOURCE_DIRECTORY.iterdir())
    names = [path.name for path in entries]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("source directory contains a case-colliding entry")
    actual = set(names)
    if not required_inputs <= actual or actual - required_inputs - optional_inputs:
        raise ValueError(
            "source directory is not the exact fresh input inventory: "
            f"{sorted(actual)}"
        )
    for path in entries:
        _require_plain_file(path, "fresh source input")
    reserved = {
        SOURCE_MATCH_INTENT,
        SOURCE_MATCH_LOG,
        SOURCE_MATCH_COMPLETION_SEAL,
        SOURCE_DIRECTORY / "games",
        *(SOURCE_DIRECTORY / name for name in MATCH_ESSENTIAL_OUTPUT_NAMES),
    }
    present = sorted(str(path) for path in reserved if path.exists())
    if present:
        raise FileExistsError(
            "source match is not a fresh no-clobber run; present: "
            + ", ".join(present)
        )


def _stream_managed_command(
    command: Sequence[str], *, environment: Mapping[str, str], log: Any
) -> int:
    header = ("[source-match-runner] " + subprocess.list2cmdline(command) + "\n").encode(
        "utf-8"
    )
    log.write(header)
    log.flush()
    process = subprocess.Popen(
        list(command),
        cwd=SOURCE_DIRECTORY,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    try:
        for line in iter(process.stdout.readline, b""):
            log.write(line)
            log.flush()
            sys.stdout.write(line.decode("utf-8", errors="replace"))
            sys.stdout.flush()
        return process.wait()
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        process.stdout.close()


def _run_source_match(args: argparse.Namespace) -> None:
    _require_canonical_source_match_args(args)
    _assert_source_match_unstarted()
    launch_inputs = _source_match_input_snapshot(args)
    environment, environment_policy = _managed_environment()
    commands = _source_match_commands(SOURCE_CONFIG)
    intent = {
        "schemaVersion": 1,
        "kind": "omega-nnue-generation5-source-match-launch-intent",
        "createdUtc": _utc_now(),
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "freshnessMarker": FRESHNESS_MARKER,
        "workingDirectory": str(SOURCE_DIRECTORY.resolve()),
        "commands": commands,
        "environment": environment_policy,
        "inputs": launch_inputs,
        "consoleLogPath": str(SOURCE_MATCH_LOG.resolve()),
        "resumeAllowed": False,
        "finalStageSeal": False,
    }
    _atomic_create(SOURCE_MATCH_INTENT, _canonical_json(intent))
    if _source_match_input_snapshot(args) != launch_inputs:
        raise ValueError("source-match inputs changed after launch intent")
    with SOURCE_MATCH_LOG.open("xb") as log:
        validate_exit = _stream_managed_command(
            commands["validate"], environment=environment, log=log
        )
        if validate_exit != 0:
            raise RuntimeError(f"OmegaMatch validate exited {validate_exit}")
        if _source_match_input_snapshot(args) != launch_inputs:
            raise ValueError("source-match inputs changed after validation")
        run_exit = _stream_managed_command(
            commands["run"], environment=environment, log=log
        )
        log.flush()
        os.fsync(log.fileno())
    if run_exit != 0:
        raise RuntimeError(f"OmegaMatch run exited {run_exit}")
    post_inputs = _source_match_input_snapshot(args)
    if post_inputs != launch_inputs:
        raise ValueError("source-match inputs changed during OmegaMatch")
    outputs = _source_match_output_snapshot(
        launch_inputs, allow_completion_seal=False
    )
    if _source_match_input_snapshot(args) != launch_inputs:
        raise ValueError("source-match inputs changed before completion sealing")
    if _source_match_output_snapshot(
        launch_inputs, allow_completion_seal=False
    ) != outputs:
        raise ValueError("source-match outputs changed before completion sealing")
    completion = {
        "schemaVersion": 1,
        "kind": "omega-nnue-generation5-source-match-completion-seal",
        "createdUtc": _utc_now(),
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "freshnessMarker": FRESHNESS_MARKER,
        "exitCode": 0,
        "launchIntent": _identity(SOURCE_MATCH_INTENT),
        "inputs": launch_inputs,
        "outputs": outputs,
        "resumeUsed": False,
        "finalStageSeal": True,
    }
    _atomic_create(SOURCE_MATCH_COMPLETION_SEAL, _canonical_json(completion))
    _verify_source_match(args)
    print(f"Sealed complete Generation 5 source match: {SOURCE_MATCH_COMPLETION_SEAL}")


def _verify_source_match(args: argparse.Namespace) -> None:
    _require_canonical_source_match_args(args)
    inputs = _source_match_input_snapshot(args)
    _require_plain_file(SOURCE_MATCH_INTENT, "source-match launch intent")
    _require_plain_file(SOURCE_MATCH_COMPLETION_SEAL, "source-match completion seal")
    intent = _load_json(SOURCE_MATCH_INTENT)
    _, environment_policy = _managed_environment()
    if (
        set(intent)
        != {
            "schemaVersion",
            "kind",
            "createdUtc",
            "runId",
            "profileId",
            "freshnessMarker",
            "workingDirectory",
            "commands",
            "environment",
            "inputs",
            "consoleLogPath",
            "resumeAllowed",
            "finalStageSeal",
        }
        or intent.get("schemaVersion") != 1
        or intent.get("kind")
        != "omega-nnue-generation5-source-match-launch-intent"
        or intent.get("runId") != RUN_ID
        or intent.get("profileId") != PROFILE_ID
        or intent.get("freshnessMarker") != FRESHNESS_MARKER
        or intent.get("workingDirectory") != str(SOURCE_DIRECTORY.resolve())
        or intent.get("commands") != _source_match_commands(SOURCE_CONFIG)
        or intent.get("environment") != environment_policy
        or intent.get("inputs") != inputs
        or intent.get("consoleLogPath") != str(SOURCE_MATCH_LOG.resolve())
        or intent.get("resumeAllowed") is not False
        or intent.get("finalStageSeal") is not False
    ):
        raise ValueError("source-match launch intent changed")
    outputs = _source_match_output_snapshot(inputs, allow_completion_seal=True)
    completion = _load_json(SOURCE_MATCH_COMPLETION_SEAL)
    if (
        set(completion)
        != {
            "schemaVersion",
            "kind",
            "createdUtc",
            "runId",
            "profileId",
            "freshnessMarker",
            "exitCode",
            "launchIntent",
            "inputs",
            "outputs",
            "resumeUsed",
            "finalStageSeal",
        }
        or completion.get("schemaVersion") != 1
        or completion.get("kind")
        != "omega-nnue-generation5-source-match-completion-seal"
        or completion.get("runId") != RUN_ID
        or completion.get("profileId") != PROFILE_ID
        or completion.get("freshnessMarker") != FRESHNESS_MARKER
        or completion.get("exitCode") != 0
        or completion.get("launchIntent") != _identity(SOURCE_MATCH_INTENT)
        or completion.get("inputs") != inputs
        or completion.get("outputs") != outputs
        or completion.get("resumeUsed") is not False
        or completion.get("finalStageSeal") is not True
    ):
        raise ValueError("source-match completion seal changed or is incomplete")
    print(f"Verified complete Generation 5 source match: {SOURCE_MATCH_COMPLETION_SEAL}")


def _relative(from_directory: Path, target: Path) -> str:
    return Path(os.path.relpath(target.resolve(), from_directory.resolve())).as_posix()


def _build_config(args: argparse.Namespace) -> None:
    output = args.output.expanduser().resolve()
    openings = args.openings.expanduser().resolve()
    engine = args.engine.expanduser().resolve()
    harness = args.harness.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to replace {output}")
    if harness.name.lower() != "omegamatch.dll":
        raise ValueError("--harness must name OmegaMatch.dll")
    suite = _load_json(openings)
    opening_rows = suite.get("openings")
    if not isinstance(opening_rows, list) or len(opening_rows) != (
        OPENINGS_PER_PHASE_SIDE * len(STRATA)
    ):
        raise ValueError("source opening suite has the wrong inventory")
    sources = [row.get("source") for row in opening_rows if isinstance(row, Mapping)]
    if len(sources) != len(opening_rows) or len(set(sources)) != len(sources):
        raise ValueError("source opening provenance tags must be unique")
    engine_pin = _identity(engine)
    harness_pin = _identity(harness)
    bundle = _bundle_identity(harness)
    openings_pin = _identity(openings)
    base = output.parent
    options = {
        "Threads": "1",
        "Hash": "128",
        "Ponder": "false",
        "OwnBook": "false",
        "UCI_Chess960": "false",
        "UCI_Variant": "omega",
        "OmegaNNUEFile": "<empty>",
        "UseOmegaNNUE": "false",
    }
    engine_record = {
        "executable": _relative(base, engine),
        "expectedSha256": engine_pin["sha256"],
        "options": options,
    }
    config = {
        "schemaVersion": 1,
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "freshnessMarker": FRESHNESS_MARKER,
        "outputDirectory": ".",
        "expectedHarnessSha256": harness_pin["sha256"],
        "expectedHarnessBundleSha256": bundle["sha256"],
        "expectedOpeningSuiteSha256": openings_pin["sha256"],
        "seed": SOURCE_SEED,
        "engines": [
            {"id": "hce-a", **engine_record},
            {"id": "hce-b", **engine_record},
        ],
        "match": {
            "engineA": "hce-a",
            "engineB": "hce-b",
            "openingsFile": _relative(base, openings),
            "repeats": 1,
            "maxPlies": 1,
            "absoluteMaxPlies": 1,
            "mode": "nodes",
            "nodes": SOURCE_NODES,
            "searchTimeoutMs": 120000,
            "stopGraceMs": 2000,
            "bootstrapIterations": 1000,
            "freshProcessPerGame": True,
        },
    }
    _atomic_create(output, _canonical_json(config))
    print(f"Wrote frozen source choice-probe config: {output}")
    print(f"Engine SHA-256: {engine_pin['sha256']}")
    print(f"OmegaMatch SHA-256: {harness_pin['sha256']}")
    print(f"OmegaMatch bundle SHA-256: {bundle['sha256']}")


def _verify(args: argparse.Namespace) -> None:
    config_path = args.config.expanduser().resolve()
    config = _load_json(config_path)
    expected_top = {
        "schemaVersion",
        "runId",
        "profileId",
        "freshnessMarker",
        "outputDirectory",
        "expectedHarnessSha256",
        "expectedHarnessBundleSha256",
        "expectedOpeningSuiteSha256",
        "seed",
        "engines",
        "match",
    }
    if set(config) != expected_top:
        raise ValueError("source config field inventory changed")
    if (
        config.get("schemaVersion") != 1
        or config.get("runId") != RUN_ID
        or config.get("profileId") != PROFILE_ID
        or config.get("freshnessMarker") != FRESHNESS_MARKER
        or config.get("seed") != SOURCE_SEED
        or config.get("outputDirectory") != "."
    ):
        raise ValueError("source config identity changed")
    base = config_path.parent
    match = config.get("match")
    expected_match = {
        "engineA": "hce-a",
        "engineB": "hce-b",
        "openingsFile": "openings.json",
        "repeats": 1,
        "maxPlies": 1,
        "absoluteMaxPlies": 1,
        "mode": "nodes",
        "nodes": SOURCE_NODES,
        "searchTimeoutMs": 120000,
        "stopGraceMs": 2000,
        "bootstrapIterations": 1000,
        "freshProcessPerGame": True,
    }
    if not isinstance(match, Mapping) or dict(match) != expected_match:
        raise ValueError("source choice-probe match contract changed")
    openings = (base / str(match.get("openingsFile"))).resolve()
    if _sha256(openings) != config.get("expectedOpeningSuiteSha256"):
        raise ValueError("source opening suite hash mismatch")
    suite = _load_json(openings)
    expected_suite_fields = {
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
    }
    if set(suite) != expected_suite_fields:
        raise ValueError("source opening-suite field inventory changed")
    if (
        suite.get("schemaVersion") != 1
        or suite.get("name")
        != "Omega NNUE Generation 5 target-opaque source openings"
        or suite.get("sourceBuilderSha256") != _sha256(Path(__file__).resolve())
        or Path(network_format.__file__).resolve() != NETWORK_FORMAT_SOURCE.resolve()
        or suite.get("networkFormatSha256") != _sha256(NETWORK_FORMAT_SOURCE)
        or suite.get("pythonRuntimeManifestSha256") != _sha256(RUNTIME_MANIFEST)
    ):
        raise ValueError("source opening-suite provenance changed")
    runtime_contract.verify_manifest(RUNTIME_MANIFEST)
    exclusion = suite.get("crossPairDuplicateExclusion")
    _validate_cross_pair_duplicate_exclusion(exclusion)
    provenance_files = {
        "rootSamplerSha256": args.root_sampler,
        "rootSamplerChessLibSha256": args.root_sampler_chesslib,
        "rootPoolSha256": args.pool,
        "rootPoolManifestSha256": args.pool_manifest,
        "rootPoolSealSha256": args.pool_seal,
    }
    for field, path in provenance_files.items():
        if suite.get(field) != _sha256(path.expanduser().resolve()):
            raise ValueError(f"source opening-suite {field} mismatch")

    # Re-derive the quarantine from the exact pinned rules-only pool.  Merely
    # validating the summary's shape would allow an edited suite to conceal a
    # cross-pair duplicate while retaining otherwise plausible counts.
    pool_path = args.pool.expanduser().resolve()
    manifest_path = args.pool_manifest.expanduser().resolve()
    seal_path = args.pool_seal.expanduser().resolve()
    sampler_path = args.root_sampler.expanduser().resolve()
    chesslib_path = args.root_sampler_chesslib.expanduser().resolve()
    pool_pin = _identity(pool_path)
    manifest_pin = _identity(manifest_path)
    seal_pin = _identity(seal_path)
    sampler_pin = _identity(sampler_path)
    chesslib_pin = _identity(chesslib_path)
    pool_manifest = _load_json(manifest_path)
    pool_seal = _load_json(seal_path)
    _validate_pool_manifest(
        pool_manifest,
        pool_identity=pool_pin,
        sampler_identity=sampler_pin,
        chesslib_identity=chesslib_pin,
    )
    _validate_pool_seal(
        pool_seal,
        pool_identity=pool_pin,
        manifest_identity=manifest_pin,
        sampler_identity=sampler_pin,
        chesslib_identity=chesslib_pin,
    )
    retained_pool, pool_coverage = _read_pool(pool_path)
    actual_trajectory_coverage = _trajectory_coverage(
        pool_coverage, pool_manifest["coverage"]
    )
    if suite.get("trajectoryCoverage") != actual_trajectory_coverage:
        raise ValueError(
            "source trajectory coverage does not match the pinned raw pool"
        )
    actual_exclusion = pool_coverage["crossPairDuplicateExclusion"]
    _validate_cross_pair_duplicate_exclusion(
        actual_exclusion,
        maximum_rows=pool_coverage["records"],
    )
    if dict(exclusion) != actual_exclusion:
        raise ValueError(
            "source cross-pair duplicate exclusion does not match the pinned raw pool"
        )
    if (
        _identity(pool_path) != pool_pin
        or _identity(manifest_path) != manifest_pin
        or _identity(seal_path) != seal_pin
        or _identity(sampler_path) != sampler_pin
        or _identity(chesslib_path) != chesslib_pin
    ):
        raise ValueError("source-pool inputs changed during verification")
    retained_pair_ofens = {
        (pair_id, item.ofen)
        for (pair_id, _), candidates in retained_pool.items()
        for item in candidates
    }
    opening_rows = suite.get("openings")
    if not isinstance(opening_rows, list) or len(opening_rows) != (
        OPENINGS_PER_PHASE_SIDE * len(STRATA)
    ):
        raise ValueError("source opening-suite row count changed")
    source_tags: set[str] = set()
    opening_ids: set[str] = set()
    opening_ofens: set[str] = set()
    counts: Counter[tuple[str, str]] = Counter()
    for index, row in enumerate(opening_rows):
        if not isinstance(row, Mapping) or set(row) != {
            "id",
            "source",
            "initialOfen",
            "moves",
        }:
            raise ValueError(f"source opening {index} field inventory changed")
        opening_id = str(row.get("id", ""))
        source = str(row.get("source", ""))
        if not opening_id or opening_id in opening_ids:
            raise ValueError("source opening IDs must be unique and nonempty")
        if not source.startswith("rules-only-pair:") or source in source_tags:
            raise ValueError("source opening provenance tags must be unique rules-only pairs")
        pair_id = source.removeprefix("rules-only-pair:")
        if PAIR_ID.fullmatch(pair_id) is None:
            raise ValueError("source opening provenance pair is malformed")
        if row.get("moves") != []:
            raise ValueError("source choice probes must launch directly from initialOfen")
        ofen = _normalize_ofen(row.get("initialOfen"))
        if ofen in opening_ofens:
            raise ValueError("source openings contain an exact duplicate")
        if (pair_id, ofen) not in retained_pair_ofens:
            raise ValueError(
                "source opening is absent from its pinned pair after duplicate quarantine"
            )
        pieces, side, _ = parse_ofen(ofen)
        phase = _phase_for_piece_count(len(pieces))
        if phase not in PHASES or side not in SIDES:
            raise ValueError("source opening is outside a frozen phase/side stratum")
        counts[(phase, side)] += 1
        opening_ids.add(opening_id)
        opening_ofens.add(ofen)
        source_tags.add(source)
    if any(counts[stratum] != OPENINGS_PER_PHASE_SIDE for stratum in STRATA):
        raise ValueError("source opening-suite phase/side quotas changed")
    harness = args.harness.expanduser().resolve()
    if _sha256(harness) != config.get("expectedHarnessSha256"):
        raise ValueError("source OmegaMatch assembly hash mismatch")
    if _bundle_identity(harness)["sha256"] != config.get(
        "expectedHarnessBundleSha256"
    ):
        raise ValueError("source OmegaMatch runtime-bundle hash mismatch")
    engines = config.get("engines")
    if not isinstance(engines, list) or len(engines) != 2:
        raise ValueError("source config must contain two HCE engine IDs")
    hashes = set()
    paths = set()
    expected_options = {
        "Threads": "1",
        "Hash": "128",
        "Ponder": "false",
        "OwnBook": "false",
        "UCI_Chess960": "false",
        "UCI_Variant": "omega",
        "OmegaNNUEFile": "<empty>",
        "UseOmegaNNUE": "false",
    }
    for expected_id, engine in zip(("hce-a", "hce-b"), engines):
        if not isinstance(engine, Mapping) or set(engine) != {
            "id",
            "executable",
            "expectedSha256",
            "options",
        }:
            raise ValueError("malformed source engine")
        if engine.get("id") != expected_id or engine.get("options") != expected_options:
            raise ValueError("source HCE engine ID/options changed")
        executable = (base / str(engine.get("executable"))).resolve()
        actual = _sha256(executable)
        if actual != engine.get("expectedSha256"):
            raise ValueError("source HCE executable hash mismatch")
        hashes.add(actual)
        paths.add(executable)
    if len(hashes) != 1 or len(paths) != 1:
        raise ValueError("source engine IDs do not use the same HCE binary")
    print(f"Verified frozen Generation 5 source config: {config_path}")


def _self_test() -> None:
    assert PROFILE_ID == "omega-decision-v2"
    assert SOURCE_SEED == 2026072301
    assert ROOT_SELECTION_SEED == 2026072302
    assert TRAJECTORY_PAIRS == 10240
    assert OPENINGS_PER_PHASE_SIDE == 896
    for key in (
        "score",
        "scores",
        "rootScoresCp",
        "TARGETScores",
        "evaluation",
        "bestMoves",
        "bestMoveScores",
        "losses",
        "winProbabilities",
    ):
        if not _has_target_like_key({key: 1}):
            raise AssertionError(f"target-like source key escaped quarantine: {key}")
    if _has_target_like_key({"selectionRank": "0" * 64, "rootPvMove": "a0a1"}):
        raise AssertionError("approved target-blind source keys were quarantined")
    runtime = runtime_contract.current_runtime_record()
    if runtime["numpyPreloadedBeforeRuntimeContract"] is not False:
        raise AssertionError("source builder missed the pre-NumPy runtime contract")
    if Path(network_format.__file__).resolve() != NETWORK_FORMAT_SOURCE.resolve():
        raise AssertionError("source builder imported a noncanonical network format")
    candidates: dict[tuple[str, tuple[str, str]], list[Candidate]] = {}
    index = 0
    for stratum in STRATA:
        for duplicate in range(3):
            index += 1
            pair = f"random-pair-{index:06d}"
            phase, side = stratum
            candidates[(pair, stratum)] = [
                Candidate(
                    pair,
                    f"{pair}-ab",
                    phase,
                    side,
                    "8/8/8/8/8/8/8/8/8/K6k[-/-/-/-] w - - 0 1",
                    "0" * 64,
                    10,
                )
            ]
    assignment = _assign_pairs(candidates, quota=2)
    if len(assignment) != len(STRATA) * 2:
        raise AssertionError("capacity-one assignment self-test failed")
    if Counter(assignment.values()) != Counter({item: 2 for item in STRATA}):
        raise AssertionError("stratum quota self-test failed")
    collision = "8/8/8/8/8/8/8/8/8/K6k[-/-/-/-] w - - 0 1"
    unique = "8/8/8/8/8/8/8/8/8/1K5k[-/-/-/-] w - - 0 1"
    pair_one = "random-pair-900001"
    pair_two = "random-pair-900002"
    collision_fixture = {
        (pair_one, ("opening", "w")): [
            Candidate(
                pair_one, f"{pair_one}-ab", "opening", "w", collision,
                "0" * 64, 10,
            ),
            Candidate(
                pair_one, f"{pair_one}-ab", "opening", "w", unique,
                "1" * 64, 11,
            ),
        ],
        (pair_two, ("opening", "w")): [
            Candidate(
                pair_two, f"{pair_two}-ba", "opening", "w", collision,
                "2" * 64, 12,
            )
        ],
    }
    exclusion = _quarantine_cross_pair_duplicates(
        collision_fixture, {collision}
    )
    if (
        exclusion["distinctOfens"] != 1
        or exclusion["excludedRows"] != 2
        or exclusion["affectedTrajectoryPairs"] != 2
        or any(
            item.ofen == collision
            for items in collision_fixture.values()
            for item in items
        )
        or (pair_two, ("opening", "w")) in collision_fixture
    ):
        raise AssertionError("cross-pair duplicate quarantine self-test failed")
    collision_two = "8/8/8/8/8/8/8/8/8/2K4k[-/-/-/-] w - - 0 1"
    repeated_pair_fixture = {
        (pair_one, ("opening", "w")): [
            Candidate(
                pair_one, f"{pair_one}-ab", "opening", "w", collision,
                "0" * 64, 10,
            ),
            Candidate(
                pair_one, f"{pair_one}-ab", "opening", "w", collision_two,
                "1" * 64, 11,
            ),
        ],
        (pair_two, ("opening", "w")): [
            Candidate(
                pair_two, f"{pair_two}-ba", "opening", "w", collision,
                "2" * 64, 12,
            ),
            Candidate(
                pair_two, f"{pair_two}-ba", "opening", "w", collision_two,
                "3" * 64, 13,
            ),
        ],
    }
    repeated_exclusion = _quarantine_cross_pair_duplicates(
        repeated_pair_fixture, {collision, collision_two}
    )
    _validate_cross_pair_duplicate_exclusion(repeated_exclusion, maximum_rows=4)
    if repeated_exclusion != {
        "policy": "exclude-all-copies-before-pair-assignment",
        "distinctOfens": 2,
        "excludedRows": 4,
        "affectedTrajectoryPairs": 2,
        "ofenSetSha256": hashlib.sha256(
            "".join(
                f"{value}\n" for value in sorted({collision, collision_two})
            ).encode("utf-8")
        ).hexdigest(),
    }:
        raise AssertionError("shared-pair duplicate quarantine self-test failed")
    malformed_exclusion = dict(repeated_exclusion)
    malformed_exclusion["affectedTrajectoryPairs"] = 1
    try:
        _validate_cross_pair_duplicate_exclusion(malformed_exclusion, maximum_rows=4)
    except ValueError:
        pass
    else:
        raise AssertionError("impossible duplicate quarantine counts were accepted")
    malformed_exclusion = dict(repeated_exclusion)
    malformed_exclusion["distinctOfens"] = True
    try:
        _validate_cross_pair_duplicate_exclusion(malformed_exclusion, maximum_rows=4)
    except ValueError:
        pass
    else:
        raise AssertionError("boolean duplicate quarantine count was accepted")
    try:
        _decode_json('{"a": 1, "a": 2}', "duplicate-test")
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate JSON key was accepted")
    completion_contract = {
        "RunId": RUN_ID,
        "EngineA": "hce-a",
        "EngineB": "hce-b",
        "Games": EXPECTED_SOURCE_GAMES,
        "PairedGames": EXPECTED_SOURCE_GAMES,
        "UnpairedGames": 0,
        "Pairs": EXPECTED_SOURCE_OPENINGS,
        "IncompletePairs": 0,
        "IllegalMoves": 0,
        "IllegalPvs": 0,
        "ProtocolFailures": 0,
        "TimeForfeits": 0,
    }
    with tempfile.TemporaryDirectory(prefix="omega-g5-source-self-test-") as raw:
        temporary = Path(raw)
        summary_path = temporary / "summary.json"
        summary_lines = ["{"]
        for offset, (name, value) in enumerate(completion_contract.items()):
            token = json.dumps(value)
            summary_lines.append(f'  "{name}": {token},')
        # These target-bearing values must remain opaque to the completion
        # reader; malformed contents here must not influence source ranking.
        summary_lines.extend(
            [
                '  "WinsA": {"deliberately": "not decoded"},',
                '  "ScoreA": ["also", "opaque"]',
                "}",
            ]
        )
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        parsed_completion = _read_summary_completion_contract(summary_path)
        if parsed_completion != completion_contract:
            raise AssertionError("target-opaque summary completion scan changed")
        _validate_summary_completion_contract(parsed_completion)
        duplicate_summary = temporary / "duplicate-summary.json"
        duplicate_summary.write_text(
            summary_path.read_text(encoding="utf-8").replace(
                '  "Games": 14336,',
                '  "Games": 14336,\n  "Games": 14336,',
            ),
            encoding="utf-8",
        )
        try:
            _read_summary_completion_contract(duplicate_summary)
        except ValueError:
            pass
        else:
            raise AssertionError("duplicate completion scalar was accepted")
        malformed_summary = temporary / "malformed-summary.json"
        malformed_summary.write_text(
            summary_path.read_text(encoding="utf-8")[:-3], encoding="utf-8"
        )
        try:
            _read_summary_completion_contract(malformed_summary)
        except ValueError:
            pass
        else:
            raise AssertionError("structurally invalid summary was accepted")
        unsafe = dict(parsed_completion)
        unsafe["IllegalMoves"] = 1
        try:
            _validate_summary_completion_contract(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError("nonzero source safety count was accepted")
        games = temporary / "games"
        games.mkdir()
        expected_game_names = {
            _source_game_name("one", "ab"),
            _source_game_name("one", "ba"),
        }
        if expected_game_names != {"one-r001-ab.pgn", "one-r001-ba.pgn"}:
            raise AssertionError("OmegaMatch repeat-one PGN naming changed")
        (games / "one-r001-ab.pgn").write_bytes(b"one\n")
        (games / "one-r001-ba.pgn").write_bytes(b"two\n")
        first_tree = _games_tree_identity(games, expected_game_names)
        if first_tree["files"] != 2:
            raise AssertionError("games-tree count changed")
        (games / "one-r001-ba.pgn").write_bytes(b"tampered\n")
        second_tree = _games_tree_identity(games, expected_game_names)
        if first_tree["sha256"] == second_tree["sha256"]:
            raise AssertionError("games-tree mutation escaped the digest")
        try:
            _games_tree_identity(games, {"one-r001-ab.pgn"})
        except ValueError:
            pass
        else:
            raise AssertionError("extra game file was accepted")
        intent_path = temporary / "intent.json"
        _atomic_create(intent_path, b"{}\n")
        try:
            _atomic_create(intent_path, b"{}\n")
        except FileExistsError:
            pass
        else:
            raise AssertionError("launch intent was clobbered")
    old_dotnet_hook = os.environ.get("DOTNET_STARTUP_HOOKS")
    old_complus = os.environ.get("COMPlus_OmegaSourceSelfTest")
    old_coreclr = os.environ.get("CORECLR_ENABLE_PROFILING")
    try:
        os.environ["DOTNET_STARTUP_HOOKS"] = "forbidden"
        os.environ["COMPlus_OmegaSourceSelfTest"] = "forbidden"
        os.environ["CORECLR_ENABLE_PROFILING"] = "1"
        managed, policy = _managed_environment()
        if (
            "DOTNET_STARTUP_HOOKS" in managed
            or "COMPlus_OmegaSourceSelfTest" in managed
            or "CORECLR_ENABLE_PROFILING" in managed
            or managed.get("DOTNET_MULTILEVEL_LOOKUP") != "0"
            or policy.get("set", {}).get("DOTNET_ROOT")
            != str(dotnet_runtime_contract.RUNTIME_ROOT.resolve())
            or "CORECLR_" not in policy.get("clearedPrefixes", [])
        ):
            raise AssertionError("managed-runtime environment was not sanitized")
    finally:
        if old_dotnet_hook is None:
            os.environ.pop("DOTNET_STARTUP_HOOKS", None)
        else:
            os.environ["DOTNET_STARTUP_HOOKS"] = old_dotnet_hook
        if old_complus is None:
            os.environ.pop("COMPlus_OmegaSourceSelfTest", None)
        else:
            os.environ["COMPlus_OmegaSourceSelfTest"] = old_complus
        if old_coreclr is None:
            os.environ.pop("CORECLR_ENABLE_PROFILING", None)
        else:
            os.environ["CORECLR_ENABLE_PROFILING"] = old_coreclr
    commands = _source_match_commands(SOURCE_CONFIG)
    if (
        commands["run"][0] != str(FROZEN_DOTNET_HOST.resolve())
        or commands["run"][1] != str(FROZEN_OMEGAMATCH.resolve())
        or "resume" in commands["run"]
        or "--pair-budget" in commands["run"]
    ):
        raise AssertionError("canonical one-shot source command changed")
    try:
        _canonical_path(Path(tempfile.gettempdir()), SOURCE_CONFIG, "test")
    except ValueError:
        pass
    else:
        raise AssertionError("noncanonical source path was accepted")
    print("Generation 5 source builder self-tests passed.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    openings = subparsers.add_parser("build-openings")
    openings.add_argument(
        "--pool",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/rules-only-pool.jsonl",
    )
    openings.add_argument(
        "--pool-manifest",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/"
        "rules-only-pool.jsonl.manifest.json",
    )
    openings.add_argument(
        "--pool-seal",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/"
        "rules-only-pool.jsonl.complete.seal.json",
    )
    openings.add_argument("--sampler-assembly", type=Path, required=True)
    openings.add_argument("--chesslib-assembly", type=Path, required=True)
    openings.add_argument(
        "--output",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/openings.json",
    )
    config = subparsers.add_parser("build-config")
    config.add_argument(
        "--openings",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/openings.json",
    )
    config.add_argument("--engine", type=Path, required=True)
    config.add_argument("--harness", type=Path, required=True)
    config.add_argument(
        "--output",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/source-match.json",
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument(
        "--config",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/source-match.json",
    )
    verify.add_argument(
        "--harness",
        type=Path,
        default=REPO
        / "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/OmegaMatch.dll",
    )
    verify.add_argument(
        "--root-sampler",
        type=Path,
        default=REPO
        / "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/"
        "OmegaRootSampler.dll",
    )
    verify.add_argument(
        "--root-sampler-chesslib",
        type=Path,
        default=REPO
        / "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/ChessLib.dll",
    )
    verify.add_argument(
        "--pool",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/rules-only-pool.jsonl",
    )
    verify.add_argument(
        "--pool-manifest",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/"
        "rules-only-pool.jsonl.manifest.json",
    )
    verify.add_argument(
        "--pool-seal",
        type=Path,
        default=REPO
        / "build-msvc/data-generation/omega-decision-v2/source/"
        "rules-only-pool.jsonl.complete.seal.json",
    )
    verify.add_argument(
        "--completion-seal",
        type=Path,
        help="also verify the no-resume source-match completion seal",
    )

    def add_source_match_paths(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config", type=Path, default=SOURCE_CONFIG)
        command.add_argument("--harness", type=Path, default=FROZEN_OMEGAMATCH)
        command.add_argument(
            "--root-sampler", type=Path, default=FROZEN_ROOT_SAMPLER
        )
        command.add_argument(
            "--root-sampler-chesslib",
            type=Path,
            default=FROZEN_ROOT_SAMPLER_CHESSLIB,
        )
        command.add_argument("--pool", type=Path, default=SOURCE_POOL)
        command.add_argument(
            "--pool-manifest", type=Path, default=SOURCE_POOL_MANIFEST
        )
        command.add_argument("--pool-seal", type=Path, default=SOURCE_POOL_SEAL)

    run_source = subparsers.add_parser(
        "run-source-match",
        help="launch the canonical source match once and seal clean completion",
    )
    add_source_match_paths(run_source)
    verify_source = subparsers.add_parser(
        "verify-source-match",
        help="rehash the canonical source-match completion seal",
    )
    add_source_match_paths(verify_source)
    verify_source.add_argument(
        "--completion-seal",
        type=Path,
        default=SOURCE_MATCH_COMPLETION_SEAL,
    )
    subparsers.add_parser("self-test")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "self-test":
        runtime_contract.verify_manifest(RUNTIME_MANIFEST)
    if args.command == "build-openings":
        _build_openings(args)
    elif args.command == "build-config":
        _build_config(args)
    elif args.command == "verify":
        _verify(args)
        if args.completion_seal is not None:
            _canonical_path(
                args.completion_seal,
                SOURCE_MATCH_COMPLETION_SEAL,
                "source-match completion seal",
            )
            _verify_source_match(args)
    elif args.command == "run-source-match":
        _run_source_match(args)
    elif args.command == "verify-source-match":
        _canonical_path(
            args.completion_seal,
            SOURCE_MATCH_COMPLETION_SEAL,
            "source-match completion seal",
        )
        _verify_source_match(args)
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
