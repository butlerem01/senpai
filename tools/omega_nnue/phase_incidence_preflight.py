#!/usr/bin/env python3
"""Target-opaque phase-incidence preflight for Omega NNUE corpora.

Production scanning decodes exactly two JSONL values: ``groupId`` and
``ofen``.  Every other value, including ``phase`` and all target fields, is
validated and skipped lexically.  Phase is derived independently from the
piece count in the Omega FEN.

The resulting audit is suitable for publication before labels are used.  It
checks the *post-component* global group IDs in every deterministic split,
partitions them by their exact phase-incidence bitmask, and rejects an
observed stratum with fewer than the preregistered number of global groups.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping, Sequence

from omega_nnue import deterministic_split, parse_ofen


SCHEMA_VERSION = 1
AUDIT_KIND = "omega-nnue-phase-incidence-preflight"
PHASES = ("opening", "middlegame", "late", "endgame")
SPLIT_NAMES = ("train", "validation", "heldout")
DEFAULT_SPLIT_SEED = 4989
DEFAULT_TRAIN_PERCENT = 80.0
DEFAULT_VALIDATION_PERCENT = 10.0
DEFAULT_MINIMUM_GROUPS = 2
FINAL_GROUP_ID_PATTERN = re.compile(
    r"^deep-hce-v[0-9]+-component:[0-9a-f]{64}$"
)
NUMBER_PATTERN = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
)
CANONICAL_GEN3_AUDIT_RELATIVE_PATH = (
    "build-msvc/data-generation/deep-hce-v4/"
    "phase-incidence-preflight.seal.json"
)
GROUP_LIST_DIGEST_ENCODING = (
    "sorted unique UTF-8 groupIds, each followed by one LF"
)
SPLIT_ALGORITHM = (
    "SHA-256(UTF-8(decimal seed + NUL + groupId)); first 64 digest bits "
    "as an unsigned big-endian fraction of 2^64"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _skip_json_string(text: str, start: int, *, location: str) -> int:
    if start >= len(text) or text[start] != '"':
        raise ValueError(f"{location}: expected a JSON string")
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == '"':
            return index + 1
        if ord(character) < 0x20:
            raise ValueError(f"{location}: unescaped JSON control character")
        if character != "\\":
            index += 1
            continue
        index += 1
        if index >= len(text):
            raise ValueError(f"{location}: unterminated JSON escape")
        escape = text[index]
        if escape in '"\\/bfnrt':
            index += 1
            continue
        if escape != "u":
            raise ValueError(f"{location}: invalid JSON escape")
        digits = text[index + 1 : index + 5]
        if len(digits) != 4 or any(
            character not in "0123456789abcdefABCDEF" for character in digits
        ):
            raise ValueError(f"{location}: invalid JSON Unicode escape")
        index += 5
    raise ValueError(f"{location}: unterminated JSON string")


def _skip_json_value(text: str, start: int, *, location: str) -> int:
    """Validate and skip one JSON value without materializing it."""

    index = _skip_space(text, start)
    if index >= len(text):
        raise ValueError(f"{location}: missing JSON value")
    character = text[index]
    if character == '"':
        return _skip_json_string(text, index, location=location)
    if character == "{":
        index = _skip_space(text, index + 1)
        if index < len(text) and text[index] == "}":
            return index + 1
        while True:
            key_end = _skip_json_string(text, index, location=location)
            index = _skip_space(text, key_end)
            if index >= len(text) or text[index] != ":":
                raise ValueError(f"{location}: missing nested object colon")
            index = _skip_json_value(text, index + 1, location=location)
            index = _skip_space(text, index)
            if index < len(text) and text[index] == ",":
                index = _skip_space(text, index + 1)
                continue
            if index < len(text) and text[index] == "}":
                return index + 1
            raise ValueError(f"{location}: malformed nested JSON object")
    if character == "[":
        index = _skip_space(text, index + 1)
        if index < len(text) and text[index] == "]":
            return index + 1
        while True:
            index = _skip_json_value(text, index, location=location)
            index = _skip_space(text, index)
            if index < len(text) and text[index] == ",":
                index = _skip_space(text, index + 1)
                continue
            if index < len(text) and text[index] == "]":
                return index + 1
            raise ValueError(f"{location}: malformed JSON array")
    for literal in ("true", "false", "null"):
        if text.startswith(literal, index):
            return index + len(literal)
    match = NUMBER_PATTERN.match(text, index)
    if match is not None:
        return match.end()
    raise ValueError(f"{location}: invalid JSON value")


def _decode_json_string(
    text: str, start: int, end: int, *, location: str
) -> str:
    try:
        value = json.JSONDecoder().decode(text[start:end])
    except json.JSONDecodeError as error:
        raise ValueError(f"{location}: invalid selected JSON string") from error
    if not isinstance(value, str):
        raise ValueError(f"{location}: selected JSON value is not a string")
    return value


def _selected_strings(
    line: str,
    *,
    location: str,
    fields: Sequence[str] = ("groupId", "ofen"),
) -> dict[str, str]:
    """Decode selected top-level strings while skipping all other values."""

    requested = frozenset(fields)
    if len(requested) != len(fields):
        raise ValueError("selected JSON fields must be unique")
    text = line.lstrip("\ufeff")
    index = _skip_space(text, 0)
    if index >= len(text) or text[index] != "{":
        raise ValueError(f"{location}: JSONL row is not an object")
    index = _skip_space(text, index + 1)
    selected: dict[str, str] = {}
    seen: set[str] = set()
    if index < len(text) and text[index] == "}":
        index += 1
    else:
        while True:
            key_start = index
            key_end = _skip_json_string(text, key_start, location=location)
            key = _decode_json_string(
                text, key_start, key_end, location=location
            )
            if key in seen:
                raise ValueError(
                    f"{location}: duplicate top-level JSON key {key!r}"
                )
            seen.add(key)
            index = _skip_space(text, key_end)
            if index >= len(text) or text[index] != ":":
                raise ValueError(f"{location}: missing top-level object colon")
            value_start = _skip_space(text, index + 1)
            if key in requested:
                value_end = _skip_json_string(
                    text, value_start, location=location
                )
                value = _decode_json_string(
                    text, value_start, value_end, location=location
                )
                if not value.strip():
                    raise ValueError(
                        f"{location}: selected field {key!r} is empty"
                    )
                selected[key] = value
            else:
                value_end = _skip_json_value(
                    text, value_start, location=location
                )
            index = _skip_space(text, value_end)
            if index < len(text) and text[index] == ",":
                index = _skip_space(text, index + 1)
                continue
            if index < len(text) and text[index] == "}":
                index += 1
                break
            raise ValueError(f"{location}: malformed top-level JSON object")
    index = _skip_space(text, index)
    if index != len(text):
        raise ValueError(f"{location}: trailing content after JSON object")
    missing = requested - set(selected)
    if missing:
        raise ValueError(
            f"{location}: missing selected field(s): {sorted(missing)}"
        )
    return selected


def _phase_from_ofen(ofen: str, *, location: str) -> tuple[str, str]:
    try:
        pieces, side_to_move, _castling = parse_ofen(ofen)
    except ValueError as error:
        raise ValueError(f"{location}: invalid Omega OFEN: {error}") from error
    piece_count = len(pieces)
    if piece_count >= 37:
        phase = "opening"
    elif piece_count >= 25:
        phase = "middlegame"
    elif piece_count >= 13:
        phase = "late"
    elif piece_count >= 5:
        phase = "endgame"
    else:
        raise ValueError(
            f"{location}: {piece_count} pieces do not belong to a frozen phase"
        )
    return phase, side_to_move


def _group_list_digest(groups: Sequence[str]) -> str:
    ordered = sorted(set(groups))
    if len(ordered) != len(groups):
        raise ValueError("group-list digest input contains duplicates")
    payload = "".join(f"{group}\n" for group in ordered).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_parameters(
    *,
    split_seed: int,
    train_percent: float,
    validation_percent: float,
    minimum_groups: int,
) -> None:
    if isinstance(split_seed, bool) or not isinstance(split_seed, int):
        raise ValueError("split_seed must be an integer")
    if (
        isinstance(train_percent, bool)
        or isinstance(validation_percent, bool)
        or not isinstance(train_percent, (int, float))
        or not isinstance(validation_percent, (int, float))
    ):
        raise ValueError("split percentages must be finite numbers")
    if (
        not math.isfinite(train_percent)
        or not math.isfinite(validation_percent)
        or train_percent <= 0.0
        or validation_percent <= 0.0
        or train_percent + validation_percent >= 100.0
    ):
        raise ValueError("split percentages must define three positive splits")
    if (
        isinstance(minimum_groups, bool)
        or not isinstance(minimum_groups, int)
        or minimum_groups < 2
    ):
        raise ValueError("minimum_groups must be an integer of at least two")


def audit_corpus(
    path: Path,
    *,
    split_seed: int = DEFAULT_SPLIT_SEED,
    train_percent: float = DEFAULT_TRAIN_PERCENT,
    validation_percent: float = DEFAULT_VALIDATION_PERCENT,
    minimum_groups: int = DEFAULT_MINIMUM_GROUPS,
) -> dict[str, Any]:
    """Return a deterministic target-opaque phase-incidence audit."""

    _validate_parameters(
        split_seed=split_seed,
        train_percent=train_percent,
        validation_percent=validation_percent,
        minimum_groups=minimum_groups,
    )
    corpus = Path(path).resolve(strict=True)
    group_phases: dict[str, set[str]] = {}
    group_rows: dict[str, int] = {}
    group_phase_rows: dict[tuple[str, str], int] = {}
    group_splits: dict[str, int] = {}
    split_rows = [0, 0, 0]
    phase_rows = [{phase: 0 for phase in PHASES} for _ in SPLIT_NAMES]
    side_rows = [
        {"white": 0, "black": 0} for _ in SPLIT_NAMES
    ]
    rows = 0

    with corpus.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            location = f"{corpus}:{line_number}"
            selected = _selected_strings(line, location=location)
            group = selected["groupId"]
            if FINAL_GROUP_ID_PATTERN.fullmatch(group) is None:
                raise ValueError(
                    f"{location}: groupId is not a final global component id"
                )
            ofen = " ".join(selected["ofen"].split())
            phase, side_to_move = _phase_from_ofen(ofen, location=location)
            split = deterministic_split(
                group,
                split_seed,
                train_percent,
                validation_percent,
            )
            previous_split = group_splits.setdefault(group, split)
            if previous_split != split:
                raise AssertionError(
                    f"{location}: one global group reached multiple splits"
                )
            group_phases.setdefault(group, set()).add(phase)
            group_rows[group] = group_rows.get(group, 0) + 1
            key = (group, phase)
            group_phase_rows[key] = group_phase_rows.get(key, 0) + 1
            split_rows[split] += 1
            phase_rows[split][phase] += 1
            side_rows[split][
                "white" if side_to_move == "w" else "black"
            ] += 1
            rows += 1
    if rows == 0:
        raise ValueError("corpus contains no rows")

    failures: list[dict[str, Any]] = []
    split_audits: list[dict[str, Any]] = []
    for split, split_name in enumerate(SPLIT_NAMES):
        groups = sorted(
            group for group, routed in group_splits.items() if routed == split
        )
        strata: dict[int, list[str]] = {}
        for group in groups:
            mask = sum(
                1 << phase_index
                for phase_index, phase in enumerate(PHASES)
                if phase in group_phases[group]
            )
            if mask <= 0:
                raise AssertionError("global group has no phase incidence")
            strata.setdefault(mask, []).append(group)

        if not groups:
            failures.append(
                {
                    "type": "empty-split",
                    "splitId": split,
                    "split": split_name,
                }
            )
        missing_phases = [
            phase for phase in PHASES if phase_rows[split][phase] == 0
        ]
        if missing_phases:
            failures.append(
                {
                    "type": "missing-phase",
                    "splitId": split,
                    "split": split_name,
                    "phases": missing_phases,
                }
            )

        stratum_records: list[dict[str, Any]] = []
        for mask in sorted(strata):
            stratum_groups = sorted(strata[mask])
            phases = [
                phase
                for phase_index, phase in enumerate(PHASES)
                if mask & (1 << phase_index)
            ]
            row_count = sum(group_rows[group] for group in stratum_groups)
            phase_cell_count = sum(
                len(group_phases[group]) for group in stratum_groups
            )
            record = {
                "mask": mask,
                "phases": phases,
                "groupCount": len(stratum_groups),
                "rowCount": row_count,
                "phaseCellCount": phase_cell_count,
                "groupIdsSha256": _group_list_digest(stratum_groups),
            }
            stratum_records.append(record)
            if len(stratum_groups) < minimum_groups:
                failures.append(
                    {
                        "type": "minimum-groups-per-observed-stratum",
                        "splitId": split,
                        "split": split_name,
                        "mask": mask,
                        "phases": phases,
                        "groupCount": len(stratum_groups),
                        "rowCount": row_count,
                        "phaseCellCount": phase_cell_count,
                        "groupIds": stratum_groups,
                    }
                )

        phase_group_cells = {
            phase: sum(
                1 for group in groups if phase in group_phases[group]
            )
            for phase in PHASES
        }
        minimum_observed = (
            min(len(value) for value in strata.values()) if strata else 0
        )
        split_failed = any(
            failure["splitId"] == split for failure in failures
        )
        split_audits.append(
            {
                "splitId": split,
                "split": split_name,
                "rows": split_rows[split],
                "groups": len(groups),
                "phaseRows": phase_rows[split],
                "sideToMoveRows": side_rows[split],
                "phaseGroupCells": phase_group_cells,
                "observedStrata": stratum_records,
                "minimumObservedGroupCount": minimum_observed,
                "passed": not split_failed,
            }
        )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": AUDIT_KIND,
        "tool": _identity(Path(__file__)),
        "canonicalGeneration3AuditPath": (
            CANONICAL_GEN3_AUDIT_RELATIVE_PATH
        ),
        "corpus": _identity(corpus),
        "informationBoundary": {
            "decodedValueFields": ["groupId", "ofen"],
            "phaseFieldDecoded": False,
            "targetFieldsDecoded": 0,
            "targetFieldsEmitted": 0,
            "phaseDerivedFromOfen": True,
        },
        "globalGroupContract": {
            "field": "groupId",
            "requiredForm": FINAL_GROUP_ID_PATTERN.pattern,
            "postComponentGlobalIdRequired": True,
            "appendPhaseForbidden": True,
        },
        "phaseDerivation": {
            "phaseOrder": list(PHASES),
            "pieceCountRanges": {
                "opening": [37, None],
                "middlegame": [25, 36],
                "late": [13, 24],
                "endgame": [5, 12],
            },
            "source": "piece count from strict Omega OFEN parsing",
        },
        "splitRouting": {
            "algorithm": SPLIT_ALGORITHM,
            "groupField": "groupId",
            "splitSeed": split_seed,
            "trainPercent": train_percent,
            "validationPercent": validation_percent,
            "heldoutPercent": 100.0 - train_percent - validation_percent,
            "routeWholeGlobalGroup": True,
        },
        "groupListDigestEncoding": GROUP_LIST_DIGEST_ENCODING,
        "minimumGroupsPerObservedStratum": minimum_groups,
        "rows": rows,
        "groups": len(group_phases),
        "splits": split_audits,
        "failures": failures,
        "passed": not failures,
    }


def _atomic_json_no_clobber(output: Path, value: Mapping[str, Any]) -> None:
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"audit already exists: {destination}")
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    linked = False
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
        linked = True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        if not linked and destination.exists():
            # A racing publisher owns the destination; never remove it.
            pass


def write_audit(
    path: Path,
    output: Path,
    *,
    split_seed: int = DEFAULT_SPLIT_SEED,
    train_percent: float = DEFAULT_TRAIN_PERCENT,
    validation_percent: float = DEFAULT_VALIDATION_PERCENT,
    minimum_groups: int = DEFAULT_MINIMUM_GROUPS,
) -> dict[str, Any]:
    """Audit ``path`` and publish one no-clobber canonical JSON file."""

    audit = audit_corpus(
        path,
        split_seed=split_seed,
        train_percent=train_percent,
        validation_percent=validation_percent,
        minimum_groups=minimum_groups,
    )
    _atomic_json_no_clobber(Path(output), audit)
    return audit


def _load_audit(path: Path) -> dict[str, Any]:
    resolved = Path(path).resolve(strict=True)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid phase-incidence audit: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("phase-incidence audit is not an object")
    return value


def verify_audit(
    corpus: Path,
    audit_path: Path,
    *,
    split_seed: int = DEFAULT_SPLIT_SEED,
    train_percent: float = DEFAULT_TRAIN_PERCENT,
    validation_percent: float = DEFAULT_VALIDATION_PERCENT,
    minimum_groups: int = DEFAULT_MINIMUM_GROUPS,
) -> dict[str, Any]:
    """Recompute a target-opaque audit and require exact equality."""

    reported = _load_audit(audit_path)
    expected = audit_corpus(
        corpus,
        split_seed=split_seed,
        train_percent=train_percent,
        validation_percent=validation_percent,
        minimum_groups=minimum_groups,
    )
    if reported != expected:
        raise ValueError("phase-incidence audit differs from exact recomputation")
    return reported


def _synthetic_ofen(phase: str, side: str, index: int) -> str:
    piece_counts = {
        "opening": 38,
        "middlegame": 30,
        "late": 18,
        "endgame": 8,
    }
    count = piece_counts[phase]
    board = [["." for _ in range(10)] for _ in range(10)]
    board[0][4] = "K"
    board[9][4] = "k"
    board[1][0] = "P"
    board[8][9] = "p"
    symbols = "PNBRCQpnbrcq"
    placed = 4
    cursor = index * 13
    while placed < count:
        square = cursor % 98 + 1
        cursor += 17
        rank, file = divmod(square, 10)
        if rank >= 9 or board[rank][file] != ".":
            continue
        board[rank][file] = symbols[(placed + index) % len(symbols)]
        placed += 1
    ranks: list[str] = []
    for rank in range(9, -1, -1):
        tokens: list[str] = []
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
    return f"{'/'.join(ranks)}[-/-/-/-] {side} - - 0 1"


def _synthetic_group(split: int, ordinal: int) -> str:
    attempt = 0
    while True:
        digest = hashlib.sha256(
            f"phase-incidence-self-test\0{split}\0{ordinal}\0{attempt}".encode(
                "utf-8"
            )
        ).hexdigest()
        group = f"deep-hce-v9-component:{digest}"
        if (
            deterministic_split(
                group,
                DEFAULT_SPLIT_SEED,
                DEFAULT_TRAIN_PERCENT,
                DEFAULT_VALIDATION_PERCENT,
            )
            == split
        ):
            return group
        attempt += 1


def _self_test() -> None:
    poison = (
        '{"groupId":"deep-hce-v9-component:'
        + "0" * 64
        + '","targetCpStm":"POISON-TARGET-VALUE",'
        '"phase":"POISON-PHASE-VALUE","ofen":'
        + json.dumps(_synthetic_ofen("endgame", "w", 1))
        + "}"
    )
    original_decoder = globals()["_decode_json_string"]
    decoded_fragments: list[str] = []

    def guarded_decoder(
        text: str, start: int, end: int, *, location: str
    ) -> str:
        fragment = text[start:end]
        decoded_fragments.append(fragment)
        if "POISON-TARGET-VALUE" in fragment or "POISON-PHASE-VALUE" in fragment:
            raise AssertionError("scanner decoded a forbidden value")
        return original_decoder(text, start, end, location=location)

    globals()["_decode_json_string"] = guarded_decoder
    try:
        selected = _selected_strings(poison, location="poison-self-test")
    finally:
        globals()["_decode_json_string"] = original_decoder
    if set(selected) != {"groupId", "ofen"}:
        raise AssertionError("selective scanner changed its decoded fields")
    if not decoded_fragments:
        raise AssertionError("selective scanner self-test decoded nothing")

    with tempfile.TemporaryDirectory(
        prefix="omega-phase-incidence-self-test-"
    ) as temporary_text:
        temporary = Path(temporary_text)
        corpus = temporary / "synthetic.jsonl"
        rows: list[dict[str, Any]] = []
        index = 0
        for split in range(3):
            for ordinal in range(2):
                group = _synthetic_group(split, ordinal)
                for phase in PHASES:
                    index += 1
                    rows.append(
                        {
                            "groupId": group,
                            "targetCpStm": {
                                "POISON-TARGET-VALUE": [index, phase]
                            },
                            "phase": "POISON-PHASE-VALUE",
                            "ofen": _synthetic_ofen(
                                phase,
                                "w" if index % 2 else "b",
                                index,
                            ),
                        }
                    )
        singleton = _synthetic_group(2, 99)
        for phase in ("late", "endgame"):
            index += 1
            rows.append(
                {
                    "groupId": singleton,
                    "targetCpStm": "POISON-TARGET-VALUE",
                    "phase": "POISON-PHASE-VALUE",
                    "ofen": _synthetic_ofen(
                        phase,
                        "w" if index % 2 else "b",
                        index,
                    ),
                }
            )
        with corpus.open("x", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(
                    json.dumps(row, sort_keys=False, separators=(",", ":"))
                    + "\n"
                )
        audit = audit_corpus(corpus)
        singleton_failures = [
            failure
            for failure in audit["failures"]
            if failure["type"]
            == "minimum-groups-per-observed-stratum"
        ]
        if (
            audit["passed"]
            or len(singleton_failures) != 1
            or singleton_failures[0]["splitId"] != 2
            or singleton_failures[0]["mask"] != 12
            or singleton_failures[0]["groupIds"] != [singleton]
        ):
            raise AssertionError("target-opaque singleton preflight failed")
        output = temporary / "audit.json"
        written = write_audit(corpus, output)
        verified = verify_audit(corpus, output)
        if written != audit or verified != audit:
            raise AssertionError("audit write/verify round trip changed")
        try:
            write_audit(corpus, output)
        except FileExistsError:
            pass
        else:
            raise AssertionError("audit writer clobbered an existing seal")
        forged = json.loads(output.read_text(encoding="utf-8"))
        forged["splits"][2]["minimumObservedGroupCount"] = 2
        forged_path = temporary / "forged.json"
        forged_path.write_text(
            json.dumps(forged), encoding="utf-8", newline="\n"
        )
        try:
            verify_audit(corpus, forged_path)
        except ValueError:
            pass
        else:
            raise AssertionError("audit verifier accepted a forged count")


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--corpus", required=True, type=Path)
        command.add_argument(
            "--split-seed", type=int, default=DEFAULT_SPLIT_SEED
        )
        command.add_argument(
            "--train-percent", type=float, default=DEFAULT_TRAIN_PERCENT
        )
        command.add_argument(
            "--validation-percent",
            type=float,
            default=DEFAULT_VALIDATION_PERCENT,
        )
        command.add_argument(
            "--minimum-groups", type=int, default=DEFAULT_MINIMUM_GROUPS
        )

    audit_parser = subparsers.add_parser(
        "audit", help="write a no-clobber audit seal"
    )
    common(audit_parser)
    audit_parser.add_argument("--output", required=True, type=Path)

    check_parser = subparsers.add_parser(
        "check", help="check a corpus without writing an artifact"
    )
    common(check_parser)

    verify_parser = subparsers.add_parser(
        "verify", help="recompute and verify an audit seal"
    )
    common(verify_parser)
    verify_parser.add_argument("--audit", required=True, type=Path)

    subparsers.add_parser("self-test", help="run target-opacity self-tests")
    return parser.parse_args(argv)


def _summary(audit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "corpus": audit["corpus"],
        "rows": audit["rows"],
        "groups": audit["groups"],
        "minimumGroupsPerObservedStratum": (
            audit["minimumGroupsPerObservedStratum"]
        ),
        "splits": [
            {
                "splitId": split["splitId"],
                "split": split["split"],
                "rows": split["rows"],
                "groups": split["groups"],
                "minimumObservedGroupCount": (
                    split["minimumObservedGroupCount"]
                ),
                "passed": split["passed"],
            }
            for split in audit["splits"]
        ],
        "failures": audit["failures"],
        "passed": audit["passed"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if args.command == "self-test":
        _self_test()
        print("phase-incidence preflight self-test passed")
        return 0
    keywords = {
        "split_seed": args.split_seed,
        "train_percent": args.train_percent,
        "validation_percent": args.validation_percent,
        "minimum_groups": args.minimum_groups,
    }
    if args.command == "audit":
        audit = write_audit(args.corpus, args.output, **keywords)
        print(f"audit: {args.output.resolve()}")
    elif args.command == "check":
        audit = audit_corpus(args.corpus, **keywords)
    elif args.command == "verify":
        audit = verify_audit(args.corpus, args.audit, **keywords)
        print(f"verified: {args.audit.resolve()}")
    else:
        raise AssertionError(f"unknown command: {args.command}")
    print(json.dumps(_summary(audit), indent=2, sort_keys=True))
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, FileExistsError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
