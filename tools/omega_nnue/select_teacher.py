#!/usr/bin/env python3
"""Select a deterministic, phase/score-balanced NNUE fine-tuning corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from omega_nnue import active_features


def _pin(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    digest = hashlib.sha256()
    size = 0
    with resolved.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return {"path": str(resolved), "bytes": size, "sha256": digest.hexdigest()}


def _atomic_write(path: Path, payload: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _sample_id(record: dict[str, Any], line_number: int) -> str:
    value = record.get("sampleId")
    if value is not None and str(value).strip():
        return str(value)
    ofen = record.get("ofen")
    return hashlib.sha256(f"{line_number}\0{ofen}".encode("utf-8")).hexdigest()


def _phase(record: dict[str, Any]) -> int:
    ofen = record.get("ofen")
    if not isinstance(ofen, str):
        raise ValueError("record has no OFEN")
    count = len(active_features(ofen, 0))
    if count <= 12:
        return 0
    if count < 29:
        return 1
    return 2


def _score_band(record: dict[str, Any]) -> int:
    value = record.get("targetCpStm")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("record has no numeric targetCpStm")
    if value < -100:
        return 0
    if value > 100:
        return 2
    return 1


def _rank(seed: int, sample_id: str) -> bytes:
    return hashlib.sha256(f"{seed}\0{sample_id}".encode("utf-8")).digest()


def select(
    records: list[dict[str, Any]],
    *,
    maximum_records: int,
    maximum_per_group: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_group: dict[str, list[tuple[int, int, str, dict[str, Any]]]] = defaultdict(
        list
    )
    for line_number, record in enumerate(records, start=1):
        group = record.get("groupId")
        if group is None or not str(group).strip():
            raise ValueError(f"line {line_number}: missing groupId")
        sample = _sample_id(record, line_number)
        by_group[str(group)].append((_phase(record), _score_band(record), sample, record))

    selected_by_group: dict[str, list[dict[str, Any]]] = {}
    for group, rows in by_group.items():
        strata: dict[tuple[int, int], list[tuple[str, dict[str, Any]]]] = defaultdict(
            list
        )
        for phase, score, sample, record in rows:
            strata[(phase, score)].append((sample, record))
        for values in strata.values():
            values.sort(key=lambda item: (_rank(seed, item[0]), item[0]))

        positions = {key: 0 for key in strata}
        chosen: list[dict[str, Any]] = []
        keys = sorted(strata)
        while len(chosen) < maximum_per_group:
            progressed = False
            for key in keys:
                position = positions[key]
                values = strata[key]
                if position >= len(values):
                    continue
                chosen.append(values[position][1])
                positions[key] += 1
                progressed = True
                if len(chosen) >= maximum_per_group:
                    break
            if not progressed:
                break
        selected_by_group[group] = chosen

    # Interleave groups so a global limit cannot privilege lexically early or
    # very large source families.
    group_order = sorted(
        selected_by_group,
        key=lambda group: (
            hashlib.sha256(f"{seed}\0group\0{group}".encode("utf-8")).digest(),
            group,
        ),
    )
    positions = {group: 0 for group in group_order}
    selected: list[dict[str, Any]] = []
    while len(selected) < maximum_records:
        progressed = False
        for group in group_order:
            position = positions[group]
            values = selected_by_group[group]
            if position >= len(values):
                continue
            selected.append(values[position])
            positions[group] += 1
            progressed = True
            if len(selected) >= maximum_records:
                break
        if not progressed:
            break

    selected.sort(key=lambda record: _sample_id(record, 0))
    phase_counts: Counter[int] = Counter()
    score_counts: Counter[int] = Counter()
    group_counts: Counter[str] = Counter()
    for record in selected:
        phase_counts[_phase(record)] += 1
        score_counts[_score_band(record)] += 1
        group_counts[str(record["groupId"])] += 1
    return selected, {
        "inputRecords": len(records),
        "inputGroups": len(by_group),
        "selectedRecords": len(selected),
        "selectedGroups": len(group_counts),
        "largestSelectedGroup": max(group_counts.values(), default=0),
        "phaseCounts": {
            "ending": phase_counts[0],
            "middlegame": phase_counts[1],
            "opening": phase_counts[2],
        },
        "scoreCounts": {
            "negative": score_counts[0],
            "quiet": score_counts[1],
            "positive": score_counts[2],
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--max-records", type=int, default=12_000)
    parser.add_argument("--max-per-group", type=int, default=96)
    parser.add_argument("--seed", type=int, default=20260718)
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    if args.max_records <= 0 or args.max_per_group <= 0:
        raise ValueError("record limits must be positive")
    input_pin = _pin(args.input)
    records = []
    with args.input.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number}: record is not an object")
            records.append(value)

    selected, audit = select(
        records,
        maximum_records=args.max_records,
        maximum_per_group=args.max_per_group,
        seed=args.seed,
    )
    payload = b"".join(
        (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        for record in selected
    )
    if _pin(args.input) != input_pin:
        raise ValueError("input changed while selection was running")
    _atomic_write(args.output, payload)
    output_pin = {
        "path": str(args.output.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    manifest = {
        "schemaVersion": 1,
        "selection": {
            "seed": args.seed,
            "maximumRecords": args.max_records,
            "maximumPerGroup": args.max_per_group,
            "phaseBandsByActiveFeatures": ["<=12", "13..28", ">=29"],
            "scoreBandsCp": ["<-100", "-100..100", ">100"],
            "withinGroupPolicy": "round-robin-phase-score-stable-hash",
            "globalPolicy": "round-robin-groups-stable-hash",
        },
        "audit": audit,
        "input": input_pin,
        "output": output_pin,
    }
    manifest_path = args.manifest or args.output.with_suffix(
        args.output.suffix + ".manifest.json"
    )
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(
        f"selected {len(selected)} rows from {len(records)}: {args.output.resolve()}"
    )
    print(f"output sha256: {output_pin['sha256']}")
    print(f"wrote manifest: {manifest_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
