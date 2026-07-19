#!/usr/bin/env python3
"""Build a leakage-audited Omega NNUE screening root suite.

This selector treats every supplied training corpus as in-sample.  It rejects
candidate roots that overlap training at the source-game, opening-family,
exact frozen-v1 NNUE input, or rule-preserving symmetry-orbit levels.  Frozen
regressions and positions already observed in active matches are excluded too.

The script deliberately does not run engines or matches.  OmegaMatch performs
the final legality/terminal-position validation when an instantiated match
configuration is passed to its ``validate`` command.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Any, Iterable, Iterator

from omega_nnue import (
    _active_features_from_parsed,
    parse_ofen,
)


SCHEMA_VERSION = 1
PHASE_ORDER = ("opening", "middlegame", "late", "endgame")
CORNER_COORDINATES = ((0, 0), (9, 0), (9, 9), (0, 9))
CORNER_BY_COORDINATE = {
    coordinate: index for index, coordinate in enumerate(CORNER_COORDINATES)
}
PIECE_SYMBOLS = "pnbrqkcw"
REPEAT_SUFFIX = re.compile(r"-r\d+-(?:ab|ba)$", re.IGNORECASE)
SAFE_ID = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class FileIdentity:
    path: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class Candidate:
    game_id: str
    ply: int
    ofen: str
    phase: str
    piece_count: int
    pawn_count: int
    signature: str
    orbit: str
    source_family: str
    source_path: Path
    source_sha256: str
    catalog_path: Path
    rank: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--training-corpus",
        action="append",
        required=True,
        type=Path,
        help="JSONL corpus used by any training stage; repeat for every corpus.",
    )
    parser.add_argument(
        "--training-manifest",
        action="append",
        default=[],
        type=Path,
        help="Manifest for a training corpus; repeat when available.",
    )
    parser.add_argument(
        "--catalog",
        action="append",
        required=True,
        type=Path,
        help="OmegaLab catalog directory to search; repeat for supplemental catalogs.",
    )
    parser.add_argument("--regressions-dir", required=True, type=Path)
    parser.add_argument(
        "--active-events",
        action="append",
        default=[],
        type=Path,
        help="Active OmegaMatch events.jsonl to exclude; repeat as needed.",
    )
    parser.add_argument("--output-suite", required=True, type=Path)
    parser.add_argument("--output-audit", required=True, type=Path)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--roots", type=int, default=16)
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument("--family-plies", type=int, default=8)
    parser.add_argument("--minimum-ply-gap", type=int, default=5)
    parser.add_argument(
        "--maximum-roots-per-game",
        type=int,
        default=6,
        help="Cluster cap. This does not make multiple roots from one game independent.",
    )
    parser.add_argument(
        "--minimum-source-families-for-promotion",
        type=int,
        default=12,
    )
    return parser.parse_args()


def normalize_path(path: Path) -> Path:
    return path.expanduser().resolve()


def portable_path(path: Path, workspace_root: Path) -> str:
    path = normalize_path(path)
    workspace_root = normalize_path(workspace_root)
    try:
        return path.relative_to(workspace_root).as_posix()
    except ValueError:
        pass
    home = Path.home().resolve()
    try:
        return "%USERPROFILE%/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def identify(path: Path, workspace_root: Path) -> FileIdentity:
    path = normalize_path(path)
    stat = path.stat()
    return FileIdentity(
        path=portable_path(path, workspace_root),
        bytes=stat.st_size,
        sha256=sha256_file(path),
    )


def atomic_json(path: Path, value: Any) -> None:
    path = normalize_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=False)
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


def jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with normalize_path(path).open("r", encoding="utf-8") as stream:
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


def _transformed_signature_from_parsed(
    pieces: list[tuple[int, int, int]],
    side_to_move: str,
    castling: str,
    horizontal: bool,
    colour_rank: bool,
) -> str:
    transformed: list[tuple[int, int, int]] = []
    for piece, side, square in pieces:
        if square < 100:
            file, rank = divmod(square, 10)
            if horizontal:
                file = 9 - file
            if colour_rank:
                rank = 9 - rank
            transformed_square = file * 10 + rank
        else:
            file, rank = CORNER_COORDINATES[square - 100]
            if horizontal:
                file = 9 - file
            if colour_rank:
                rank = 9 - rank
            transformed_square = 100 + CORNER_BY_COORDINATE[(file, rank)]
        transformed.append(
            (piece, 1 - side if colour_rank else side, transformed_square)
        )

    rights = set() if castling == "-" else set(castling)
    if horizontal:
        rights = {
            {"K": "Q", "Q": "K", "k": "q", "q": "k"}[right]
            for right in rights
        }
    if colour_rank:
        rights = {right.swapcase() for right in rights}
        side_to_move = "b" if side_to_move == "w" else "w"
    transformed_castling = "".join(
        right for right in "KQkq" if right in rights
    ) or "-"
    white = _active_features_from_parsed(transformed, transformed_castling, 0)
    black = _active_features_from_parsed(transformed, transformed_castling, 1)
    # Inline the tiny serialization used by omega_nnue.nnue_input_signature.
    # Avoiding hundreds of thousands of short NumPy allocations makes a full
    # corpus orbit audit practical while retaining byte-for-byte equivalence.
    stm, opponent = (white, black) if side_to_move == "w" else (black, white)
    digest = hashlib.sha256()
    digest.update(b"OMNNUE1-input\0")
    for perspective in (stm, opponent):
        digest.update(struct.pack("<H", len(perspective)))
        if perspective:
            digest.update(
                struct.pack(f"<{len(perspective)}H", *perspective)
            )
    return digest.hexdigest()


def observable_ofen(ofen: str) -> str:
    fields = ofen.split()
    if len(fields) != 6:
        raise ValueError("Omega OFEN must contain exactly six fields")
    return " ".join(fields[:3])


def _serialize_observable(
    pieces: Iterable[tuple[int, int, int]],
    side_to_move: str,
    castling: str,
) -> str:
    board: dict[int, str] = {}
    for piece, side, square in pieces:
        symbol = PIECE_SYMBOLS[piece]
        if side == 0:
            symbol = symbol.upper()
        if square in board:
            raise ValueError(f"duplicate piece on square {square}")
        board[square] = symbol

    ranks: list[str] = []
    for rank in range(9, -1, -1):
        tokens: list[str] = []
        empty = 0
        for file in range(10):
            symbol = board.get(file * 10 + rank)
            if symbol is None:
                empty += 1
                continue
            if empty:
                tokens.append(str(empty))
                empty = 0
            tokens.append(symbol)
        if empty:
            tokens.append(str(empty))
        ranks.append("".join(tokens))
    corners = [board.get(100 + corner, "-") for corner in range(4)]
    placement = "/".join(ranks) + "[" + "/".join(corners) + "]"
    canonical_castling = "".join(
        right for right in "KQkq" if right in set(castling)
    ) or "-"
    return f"{placement} {side_to_move} {canonical_castling}"


@lru_cache(maxsize=None)
def transformed_observable(
    observable: str,
    horizontal: bool,
    colour_rank: bool,
) -> str:
    pieces, side_to_move, castling = parse_ofen(observable + " - 0 1")
    transformed: list[tuple[int, int, int]] = []
    for piece, side, square in pieces:
        if square < 100:
            file, rank = divmod(square, 10)
            if horizontal:
                file = 9 - file
            if colour_rank:
                rank = 9 - rank
            transformed_square = file * 10 + rank
        else:
            file, rank = CORNER_COORDINATES[square - 100]
            if horizontal:
                file = 9 - file
            if colour_rank:
                rank = 9 - rank
            transformed_square = 100 + CORNER_BY_COORDINATE[(file, rank)]
        transformed.append(
            (piece, 1 - side if colour_rank else side, transformed_square)
        )

    rights = set() if castling == "-" else set(castling)
    if horizontal:
        rights = {
            {"K": "Q", "Q": "K", "k": "q", "q": "k"}[right]
            for right in rights
        }
    if colour_rank:
        rights = {right.swapcase() for right in rights}
        side_to_move = "b" if side_to_move == "w" else "w"
    transformed_castling = "".join(
        right for right in "KQkq" if right in rights
    ) or "-"
    return _serialize_observable(
        transformed, side_to_move, transformed_castling
    )


@lru_cache(maxsize=None)
def canonical_observable(observable: str) -> str:
    return transformed_observable(observable, False, False)


@lru_cache(maxsize=None)
def nnue_observable_key(observable: str) -> str:
    """Canonical key for exactly the state observable by frozen NNUE v1.

    Raw castling characters are metadata, not features.  Runtime inference
    activates a castling feature only when that right still names a real
    same-side rook, and it records the rook's flank relative to the king.
    Encode those effective flanks so stale or equivalent metadata cannot evade
    the membership audit.
    """
    pieces, side_to_move, castling = parse_ofen(observable + " - 0 1")
    placement = _serialize_observable(pieces, side_to_move, "-").split()[0]
    by_side: list[dict[int, int]] = [{}, {}]
    kings: list[list[int]] = [[], []]
    for piece, side, square in pieces:
        by_side[side][square] = piece
        if piece == 5:
            kings[side].append(square)
    castling_rooks = (
        (0, "K", 80),
        (0, "Q", 10),
        (1, "k", 89),
        (1, "q", 19),
    )
    flanks: list[set[bool]] = [set(), set()]
    for side in (0, 1):
        if not kings[side]:
            continue
        king_file = kings[side][0] // 10
        for rook_side, right, square in castling_rooks:
            if (
                rook_side == side
                and right in castling
                and by_side[side].get(square) == 3
            ):
                rook_file = square // 10
                if rook_file != king_file:
                    flanks[side].add(rook_file > king_file)
    effective = "".join(
        (
            "L" if False in flanks[0] else "",
            "R" if True in flanks[0] else "",
            "l" if False in flanks[1] else "",
            "r" if True in flanks[1] else "",
        )
    ) or "-"
    return f"{placement} {side_to_move} {effective}"


@lru_cache(maxsize=None)
def observable_symmetries(observable: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                transformed_observable(observable, False, False),
                transformed_observable(observable, True, False),
                transformed_observable(observable, False, True),
                transformed_observable(observable, True, True),
            }
        )
    )


@lru_cache(maxsize=None)
def identity_key(observable: str) -> str:
    pieces, side_to_move, castling = parse_ofen(observable + " - 0 1")
    return _transformed_signature_from_parsed(
        pieces, side_to_move, castling, False, False
    )


@lru_cache(maxsize=None)
def input_keys(observable: str) -> tuple[str, str, tuple[str, ...]]:
    pieces, side_to_move, castling = parse_ofen(observable + " - 0 1")
    identity = identity_key(observable)
    horizontal = _transformed_signature_from_parsed(
        pieces, side_to_move, castling, True, False
    )
    # Frozen architecture v1 already orients every feature to the evaluating
    # side.  Rank reflection plus colour/STM swap therefore serializes to the
    # identity input; applying it after a file reflection likewise serializes
    # to ``horizontal``.  The four legal symmetries form two observable inputs,
    # so computing the duplicate pair again would only waste audit time.
    signatures = tuple(
        sorted({identity, horizontal})
    )
    return identity, signatures[0], signatures


def input_orbit(ofen: str) -> tuple[str, tuple[str, ...]]:
    _, orbit, signatures = input_keys(observable_ofen(ofen))
    return orbit, signatures


def phase_of(piece_count: int) -> str | None:
    if piece_count >= 37:
        return "opening"
    if piece_count >= 25:
        return "middlegame"
    if piece_count >= 13:
        return "late"
    if piece_count >= 5:
        return "endgame"
    return None


def source_family_text(value: str) -> str:
    stem = Path(value).stem.lower()
    return REPEAT_SUFFIX.sub("", stem)


def opening_family(game: dict[str, Any], family_plies: int) -> str:
    states = [str(game["initialOfen"])]
    states.extend(str(value) for value in game.get("positions", [])[:family_plies])
    family_keys: list[str] = []
    for horizontal, colour_rank in (
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ):
        digest = hashlib.sha256()
        digest.update(f"omega-opening-family-v1:{family_plies}\0".encode("ascii"))
        for state in states:
            observable = observable_ofen(state)
            transformed = nnue_observable_key(
                transformed_observable(
                    observable, horizontal, colour_rank
                )
            ).encode("ascii")
            digest.update(struct.pack("<H", len(transformed)))
            digest.update(transformed)
        family_keys.append(digest.hexdigest())
    return min(family_keys)


def nested_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for nested in value.values():
            yield from nested_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from nested_strings(nested)
    elif isinstance(value, str):
        yield value


def regression_orbits(
    directory: Path,
) -> tuple[set[str], list[Path], int]:
    result: set[str] = set()
    files = sorted(normalize_path(directory).glob("*.json"))
    positions = 0
    for path in files:
        value = json.loads(path.read_text(encoding="utf-8"))
        for text in nested_strings(value):
            if "[" not in text:
                continue
            try:
                orbit, _ = input_orbit(text)
            except ValueError:
                continue
            positions += 1
            result.add(orbit)
    return result, files, positions


def active_snapshot(
    path: Path,
) -> tuple[set[str], set[str], set[str], FileIdentity, int]:
    path = normalize_path(path)
    size = path.stat().st_size
    with path.open("rb") as stream:
        data = stream.read(size)
    digest = hashlib.sha256(data).hexdigest()
    inputs: set[str] = set()
    game_ids: set[str] = set()
    opening_ids: set[str] = set()
    records = 0
    for physical_line in data.splitlines():
        try:
            record = json.loads(physical_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        records += 1
        for key in ("GameId", "gameId", "PairId", "pairId"):
            value = record.get(key)
            if value:
                game_ids.add(str(value).lower())
        for key in ("OpeningId", "openingId"):
            value = record.get(key)
            if value:
                opening_ids.add(str(value).lower())
        for key in (
            "InitialOfen",
            "initialOfen",
            "PreOfen",
            "preOfen",
            "PostOfen",
            "postOfen",
            "FinalOfen",
            "finalOfen",
        ):
            value = record.get(key)
            if not value:
                continue
            try:
                input_key = nnue_observable_key(observable_ofen(str(value)))
            except ValueError:
                continue
            inputs.add(input_key)
    identity = FileIdentity(str(path), size, digest)
    return inputs, game_ids, opening_ids, identity, records


def load_catalogs(
    directories: Iterable[Path],
) -> tuple[dict[str, dict[str, Any]], list[tuple[dict[str, Any], Path]], list[Path]]:
    games: dict[str, dict[str, Any]] = {}
    rows: list[tuple[dict[str, Any], Path]] = []
    files: list[Path] = []
    for directory in directories:
        directory = normalize_path(directory)
        games_path = directory / "games.jsonl"
        positions_path = directory / "training-positions.jsonl"
        files.extend((games_path, positions_path))
        for game in jsonl(games_path):
            game_id = str(game["id"])
            previous = games.get(game_id)
            if previous is None:
                game = dict(game)
                game["_catalog"] = str(directory)
                games[game_id] = game
            else:
                merged = sorted(
                    set(previous.get("sources", [])) | set(game.get("sources", []))
                )
                previous["sources"] = merged
        rows.extend((record, directory) for record in jsonl(positions_path))
    return games, rows, files


def is_below(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def select_roots(
    eligible: list[Candidate],
    root_count: int,
    maximum_per_game: int,
    minimum_ply_gap: int,
) -> list[Candidate]:
    if root_count <= 0 or root_count % len(PHASE_ORDER) != 0:
        raise ValueError(
            f"--roots must be positive and divisible by {len(PHASE_ORDER)}"
        )
    target = root_count // len(PHASE_ORDER)
    by_phase: dict[str, list[Candidate]] = {
        phase: sorted(
            (item for item in eligible if item.phase == phase),
            key=lambda item: item.rank,
        )
        for phase in PHASE_ORDER
    }
    scarcity_order = sorted(
        PHASE_ORDER,
        key=lambda phase: (
            len({item.game_id for item in by_phase[phase]}),
            len(by_phase[phase]),
            PHASE_ORDER.index(phase),
        ),
    )
    selected: list[Candidate] = []
    selected_by_game: dict[str, list[int]] = defaultdict(list)
    selected_orbits: set[str] = set()
    for phase in scarcity_order:
        while sum(item.phase == phase for item in selected) < target:
            compatible = [
                item
                for item in by_phase[phase]
                if item.orbit not in selected_orbits
                and len(selected_by_game[item.game_id]) < maximum_per_game
                and all(
                    abs(item.ply - previous) >= minimum_ply_gap
                    for previous in selected_by_game[item.game_id]
                )
            ]
            if not compatible:
                counts = Counter(item.game_id for item in by_phase[phase])
                raise ValueError(
                    f"cannot fill {phase!r} target {target}; candidates by game: "
                    f"{dict(counts)}"
                )
            chosen = min(
                compatible,
                key=lambda item: (
                    len(selected_by_game[item.game_id]),
                    item.rank,
                ),
            )
            selected.append(chosen)
            selected_by_game[chosen.game_id].append(chosen.ply)
            selected_orbits.add(chosen.orbit)
    return sorted(
        selected,
        key=lambda item: (
            PHASE_ORDER.index(item.phase),
            item.source_family,
            item.ply,
        ),
    )


def main() -> int:
    args = parse_args()
    workspace_root = normalize_path(args.workspace_root)
    training_corpora = [normalize_path(path) for path in args.training_corpus]
    manifests = [normalize_path(path) for path in args.training_manifest]
    catalogs = [normalize_path(path) for path in args.catalog]

    training_inputs: set[str] = set()
    training_game_ids: set[str] = set()
    training_opening_ids: set[str] = set()
    training_source_names: set[str] = set()
    training_records = 0
    for corpus in training_corpora:
        for record in jsonl(corpus):
            training_records += 1
            ofen = str(record["ofen"])
            input_key = nnue_observable_key(observable_ofen(ofen))
            training_inputs.add(input_key)
            for key in ("gameId", "pairId", "openingId", "groupId"):
                value = record.get(key)
                if value:
                    lowered = str(value).lower()
                    if key == "openingId":
                        training_opening_ids.add(lowered)
                    else:
                        training_game_ids.add(lowered)
            provenance = record.get("provenance")
            if isinstance(provenance, dict):
                for key in ("gameId", "pairId"):
                    value = provenance.get(key)
                    if value:
                        training_game_ids.add(str(value).lower())
                        training_source_names.add(source_family_text(str(value)))
                value = provenance.get("openingId")
                if value:
                    training_opening_ids.add(str(value).lower())
                    training_source_names.add(source_family_text(str(value)))

    training_run_directories: set[Path] = set()
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for source in manifest.get("sources", []):
            source_path = source.get("path") if isinstance(source, dict) else None
            if source_path:
                training_run_directories.add(
                    normalize_path(Path(str(source_path))).parent
                )

    frozen_orbits, regression_files, regression_positions = regression_orbits(
        args.regressions_dir
    )
    active_inputs: set[str] = set()
    active_game_ids: set[str] = set()
    active_opening_ids: set[str] = set()
    active_identities: list[FileIdentity] = []
    active_records = 0
    active_directories: set[Path] = set()
    for events_path in args.active_events:
        (
            event_inputs,
            event_game_ids,
            event_opening_ids,
            identity,
            record_count,
        ) = active_snapshot(events_path)
        active_inputs.update(event_inputs)
        active_game_ids.update(event_game_ids)
        active_opening_ids.update(event_opening_ids)
        active_identities.append(identity)
        active_records += record_count
        active_directories.add(normalize_path(events_path).parent)

    games, rows, catalog_files = load_catalogs(catalogs)
    family_by_game: dict[str, str] = {}
    for game_id, game in games.items():
        family_by_game[game_id] = opening_family(game, args.family_plies)
    training_families = {
        family
        for game_id, family in family_by_game.items()
        if game_id.lower() in training_game_ids
    }

    sources_by_game: dict[str, tuple[Path, ...]] = {}
    source_gate_by_game: dict[str, tuple[str, ...]] = {}
    for game_id, game in games.items():
        sources = tuple(
            normalize_path(Path(str(value))) for value in game.get("sources", [])
        )
        sources_by_game[game_id] = sources
        source_names = {
            source_family_text(source.name) for source in sources
        }
        reasons: set[str] = set()
        family = family_by_game[game_id]
        if game_id.lower() in training_game_ids:
            reasons.add("training-source-game")
        if family in training_families:
            reasons.add("training-opening-family")
        for source, family_text in zip(
            sources,
            (source_family_text(source.name) for source in sources),
        ):
            if (
                family_text in training_source_names
                or family_text in training_opening_ids
                or family_text in training_game_ids
            ):
                reasons.add("training-source-name")
            if any(
                is_below(source, directory)
                for directory in training_run_directories
            ):
                reasons.add("training-source-run")
            if any(
                is_below(source, directory) for directory in active_directories
            ):
                reasons.add("active-source-run")
        if source_names & active_game_ids or source_names & active_opening_ids:
            reasons.add("active-source-name")
        source_gate_by_game[game_id] = tuple(sorted(reasons))

    source_hash_cache: dict[Path, str] = {}
    eligible: list[Candidate] = []
    exclusion_counts: Counter[str] = Counter()
    considered = 0
    for record, catalog_path in rows:
        considered += 1
        game_id = str(record["gameId"])
        game = games.get(game_id)
        if game is None:
            exclusion_counts["missing-game-record"] += 1
            continue
        sources = sources_by_game[game_id]
        if not sources:
            exclusion_counts["missing-source"] += 1
            continue
        reasons = set(source_gate_by_game[game_id])

        ofen = str(record["ofen"])
        if reasons:
            for reason in reasons:
                exclusion_counts[reason] += 1
            continue

        try:
            pieces, _, _ = parse_ofen(ofen)
            white_kings = sum(piece == 5 and side == 0 for piece, side, _ in pieces)
            black_kings = sum(piece == 5 and side == 1 for piece, side, _ in pieces)
            observable = canonical_observable(observable_ofen(ofen))
            input_key = nnue_observable_key(observable)
            input_orbit_keys = {
                nnue_observable_key(transformed)
                for transformed in observable_symmetries(observable)
            }
            signature, orbit, _ = input_keys(observable)
        except ValueError:
            reasons.add("invalid-ofen")
            pieces = []
            white_kings = black_kings = 0
            observable = ""
            input_key = ""
            input_orbit_keys = set()
            signature = orbit = ""
        if white_kings != 1 or black_kings != 1:
            reasons.add("king-count")
        piece_count = len(pieces)
        phase = phase_of(piece_count)
        if phase is None:
            reasons.add("too-few-pieces")
        if input_key in training_inputs:
            reasons.add("training-exact-input")
        if training_inputs.intersection(input_orbit_keys):
            reasons.add("training-symmetry-orbit")
        if orbit in frozen_orbits:
            reasons.add("frozen-regression")
        if active_inputs.intersection(input_orbit_keys):
            reasons.add("active-match-position")
        if reasons:
            for reason in reasons:
                exclusion_counts[reason] += 1
            continue

        source_path = min(sources, key=lambda value: str(value).lower())
        if source_path not in source_hash_cache:
            source_hash_cache[source_path] = sha256_file(source_path)
        pawn_count = sum(piece == 0 for piece, _, _ in pieces)
        rank = hashlib.sha256(
            f"{args.seed}\0{game_id}\0{record['ply']}\0{orbit}".encode("utf-8")
        ).hexdigest()
        eligible.append(
            Candidate(
                game_id=game_id,
                ply=int(record["ply"]),
                ofen=ofen,
                phase=str(phase),
                piece_count=piece_count,
                pawn_count=pawn_count,
                signature=signature,
                orbit=orbit,
                source_family=source_family_text(source_path.name),
                source_path=source_path,
                source_sha256=source_hash_cache[source_path],
                catalog_path=catalog_path,
                rank=rank,
            )
        )

    selected = select_roots(
        eligible,
        args.roots,
        args.maximum_roots_per_game,
        args.minimum_ply_gap,
    )
    selected_game_counts = Counter(item.game_id for item in selected)
    selected_source_counts = Counter(item.source_family for item in selected)
    selected_phase_counts = Counter(item.phase for item in selected)
    source_family_count = len(selected_source_counts)
    promotion_eligible = (
        source_family_count >= args.minimum_source_families_for_promotion
        and max(selected_source_counts.values(), default=0) <= 1
    )

    openings: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    for item in selected:
        slug = SAFE_ID.sub("-", item.source_family).strip("-")
        root_id = (
            f"nnue-screen-v1-{item.phase}-{slug}-p{item.ply:03d}-"
            f"{item.orbit[:8]}"
        )
        source_label = (
            f"Held-out CoreChess save {item.source_path.name} "
            f"(sha256 {item.source_sha256[:16]}...), position before source ply "
            f"{item.ply + 1}; selected without candidate evaluation."
        )
        openings.append(
            {
                "id": root_id,
                "source": source_label,
                "initialOfen": item.ofen,
                "moves": [],
                "phaseBucket": item.phase,
                "pieceCount": item.piece_count,
                "pawnCount": item.pawn_count,
                "sourceFamily": item.source_family,
            }
        )
        selected_rows.append(
            {
                "id": root_id,
                "gameId": item.game_id,
                "sourceFamily": item.source_family,
                "sourcePath": portable_path(item.source_path, workspace_root),
                "sourceSha256": item.source_sha256,
                "catalog": portable_path(item.catalog_path, workspace_root),
                "ply": item.ply,
                "phase": item.phase,
                "pieceCount": item.piece_count,
                "pawnCount": item.pawn_count,
                "nnueInputSignature": item.signature,
                "symmetryOrbitKey": item.orbit,
                "deterministicRank": item.rank,
            }
        )

    suite = {
        "schemaVersion": SCHEMA_VERSION,
        "name": (
            f"Omega NNUE leakage-audited screen v1 ({len(selected)} roots; "
            "screen-only)"
        ),
        "screenOnly": True,
        "openings": openings,
    }
    atomic_json(args.output_suite, suite)
    suite_identity = identify(args.output_suite, workspace_root)

    source_identities = [
        identify(path, workspace_root)
        for path in sorted(
            {item.source_path for item in selected}, key=lambda value: str(value)
        )
    ]
    audit = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-screen-root-selection",
        "policy": {
            "candidateBlind": True,
            "candidateEvaluationsRead": 0,
            "rulePreservingSymmetries": [
                "identity",
                "horizontal-file-reflection",
                "rank-reflection-with-colour-swap",
                "combined-180-degree-with-colour-swap",
            ],
            "openingFamily": (
                f"Minimum SHA-256 over four consistently transformed sequences "
                f"of the initial position plus first {args.family_plies} "
                "post-move positions"
            ),
            "inputMembershipAudit": (
                "Canonical legal Omega OFEN placement, side-to-move, and "
                "effective castling rights exactly determine frozen-v1 NNUE "
                "features. Membership is tested on those three canonical fields "
                "under all four rule-preserving symmetries; selected roots also "
                "record the independent frozen-v1 feature signature."
            ),
            "phaseBucketsByPieceCount": {
                "opening": "37+",
                "middlegame": "25-36",
                "late": "13-24",
                "endgame": "5-12",
            },
            "minimumPlyGapWithinSourceGame": args.minimum_ply_gap,
            "maximumRootsPerSourceGame": args.maximum_roots_per_game,
            "requestedRoots": args.roots,
            "minimumSourceFamiliesForPromotion": (
                args.minimum_source_families_for_promotion
            ),
            "seed": args.seed,
        },
        "inputs": {
            "selectorFiles": [
                identify(path, workspace_root).__dict__
                for path in (
                    Path(__file__),
                    Path(__file__).with_name("omega_nnue.py"),
                )
            ],
            "trainingCorpora": [
                identify(path, workspace_root).__dict__ for path in training_corpora
            ],
            "trainingManifests": [
                identify(path, workspace_root).__dict__ for path in manifests
            ],
            "catalogFiles": [
                identify(path, workspace_root).__dict__ for path in catalog_files
            ],
            "regressionFiles": [
                identify(path, workspace_root).__dict__ for path in regression_files
            ],
            "activeEventSnapshots": [
                {
                    "path": portable_path(Path(item.path), workspace_root),
                    "snapshotBytes": item.bytes,
                    "snapshotSha256": item.sha256,
                }
                for item in active_identities
            ],
            "selectedSourceFiles": [
                identity.__dict__ for identity in source_identities
            ],
        },
        "audit": {
            "trainingRecordsRead": training_records,
            "trainingExactInputs": len(training_inputs),
            "trainingObservableInputs": len(training_inputs),
            "trainingSourceGameIds": len(training_game_ids),
            "trainingOpeningFamilies": len(training_families),
            "frozenRegressionPositionsRead": regression_positions,
            "frozenRegressionSymmetryOrbits": len(frozen_orbits),
            "activeRecordsRead": active_records,
            "activeObservableInputs": len(active_inputs),
            "candidateRowsConsidered": considered,
            "candidateRowsEligible": len(eligible),
            "exclusionsByReasonNonExclusive": dict(sorted(exclusion_counts.items())),
        },
        "selection": {
            "suite": suite_identity.__dict__,
            "roots": len(selected),
            "phaseCounts": {
                phase: selected_phase_counts[phase] for phase in PHASE_ORDER
            },
            "sourceFamilyCounts": dict(sorted(selected_source_counts.items())),
            "sourceGameCounts": dict(sorted(selected_game_counts.items())),
            "uniqueSourceFamilies": source_family_count,
            "promotionEligible": promotion_eligible,
            "minimumSourceFamiliesForPromotion": (
                args.minimum_source_families_for_promotion
            ),
            "rootsDetail": selected_rows,
        },
        "limitations": [
            (
                "SCREEN-ONLY: multiple roots come from each held-out game, so "
                "paired outcomes are clustered and the arena's pair bootstrap "
                "would overstate independent evidence."
            ),
            (
                f"Only {source_family_count} source-game families survived all "
                "training, symmetry, regression, and active-match exclusions."
            ),
            (
                "The active events file was audited as a byte-pinned partial "
                "snapshot; positions produced after that byte boundary were not "
                "available to the selector."
            ),
            (
                "OmegaMatch validate must still reject any root that is illegal "
                "or already terminal before a screening run starts."
            ),
        ],
        "confirmationRemedy": {
            "sequence": [
                "Freeze and hash the candidate network before generating confirmation data.",
                (
                    "Generate a new candidate-blind source corpus using only the "
                    "unchanged HCE control, book off, fixed shallow node budget, "
                    "and independent source games."
                ),
                (
                    "Before any candidate evaluation, sample at most one "
                    "phase-stratified root from each source game."
                ),
                (
                    "Freeze source logs, root suite, executable, and selector "
                    "hashes; then run paired AB/BA confirmation."
                ),
            ],
            "candidateMayNotInfluence": [
                "source-game generation",
                "root selection",
                "phase assignment",
                "exclusion decisions",
            ],
        },
    }
    atomic_json(args.output_audit, audit)

    print(
        f"Selected {len(selected)} roots from {source_family_count} source "
        f"families; phases={dict(selected_phase_counts)}."
    )
    print(f"Suite: {normalize_path(args.output_suite)}")
    print(f"Audit: {normalize_path(args.output_audit)}")
    if not promotion_eligible:
        print(
            "SCREEN-ONLY: source-family independence is insufficient for a "
            "superiority/promotion claim."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
