#!/usr/bin/env python3
"""Build exactly aligned search-minus-HCE targets for residual Omega NNUE.

Both source corpora must contain the same unique ``sampleId`` values and the
same normalized OFEN/NNUE input for every sample.  The output keeps the search
teacher's metadata while replacing its absolute target with a correction:

    targetCpStm = searchTargetCpStm - handcraftedCpStm

The explicit target tag is required by ``train.py --network-semantics
residual``.  Game-outcome labels are moved out of the trainer's recognized
fields because an outcome is a valid target for an absolute evaluator, not for
the correction term alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
import argparse
import hashlib
import json
import math
import os
import sys
import tempfile

from omega_nnue import active_features, nnue_input_signature


TARGET_SEMANTICS = "search-minus-handcrafted"
NETWORK_SEMANTICS = "residual"


@dataclass(frozen=True)
class SourceRow:
    record: dict[str, Any]
    normalized_ofen: str
    input_signature: str
    target_cp_stm: float
    line_number: int


@dataclass(frozen=True)
class LoadedSource:
    rows: dict[str, SourceRow]
    order: tuple[str, ...]


def _file_pin(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    digest = hashlib.sha256()
    byte_count = 0
    with resolved.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            byte_count += len(block)
    return {
        "path": str(resolved),
        "bytes": byte_count,
        "sha256": digest.hexdigest(),
    }


def _pin_bytes(path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _same_pin(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left["path"] == right["path"]
        and left["bytes"] == right["bytes"]
        and left["sha256"] == right["sha256"]
    )


def _normalized_ofen(record: dict[str, Any], location: str) -> tuple[str, str]:
    value = record.get("ofen")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}: missing nonempty 'ofen'")
    fields = value.split()
    if len(fields) != 6:
        raise ValueError(
            f"{location}: expected six-field Omega OFEN, got {len(fields)} fields"
        )
    side = fields[1].lower()
    if side not in ("w", "b"):
        raise ValueError(f"{location}: invalid OFEN side to move {fields[1]!r}")
    supplied_side = record.get("sideToMove")
    if supplied_side is not None:
        normalized_side = str(supplied_side).strip().lower()
        normalized_side = {"white": "w", "black": "b"}.get(
            normalized_side, normalized_side
        )
        if normalized_side != side:
            raise ValueError(
                f"{location}: sideToMove {supplied_side!r} disagrees with OFEN"
            )
    return " ".join(fields), side


def _finite_target(record: dict[str, Any], location: str) -> float:
    value = record.get("targetCpStm")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{location}: targetCpStm must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{location}: targetCpStm must be a finite number")
    return result


def _load_source(path: Path, label: str) -> LoadedSource:
    rows: dict[str, SourceRow] = {}
    order: list[str] = []
    resolved = path.resolve(strict=True)
    with resolved.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            location = f"{resolved}:{line_number}"
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{location}: invalid JSON: {error}") from error
            if not isinstance(record, dict):
                raise ValueError(f"{location}: JSONL row is not an object")
            sample_id = record.get("sampleId")
            if not isinstance(sample_id, str) or not sample_id.strip():
                raise ValueError(f"{location}: missing nonempty string sampleId")
            if sample_id in rows:
                previous = rows[sample_id].line_number
                raise ValueError(
                    f"{location}: duplicate {label} sampleId {sample_id!r}; "
                    f"first seen at line {previous}"
                )
            ofen, side = _normalized_ofen(record, location)
            try:
                white = active_features(ofen, 0)
                black = active_features(ofen, 1)
            except ValueError as error:
                raise ValueError(f"{location}: invalid Omega OFEN: {error}") from error
            signature = nnue_input_signature(white, black, side == "w")
            rows[sample_id] = SourceRow(
                record=record,
                normalized_ofen=ofen,
                input_signature=signature,
                target_cp_stm=_finite_target(record, location),
                line_number=line_number,
            )
            order.append(sample_id)
    if not rows:
        raise ValueError(f"{resolved}: {label} corpus has no rows")
    return LoadedSource(rows=rows, order=tuple(order))


def _compact_number(value: float) -> int | float:
    if value.is_integer():
        return int(value)
    return value


def _aligned_payload(
    search: LoadedSource,
    handcrafted: LoadedSource,
    *,
    search_path: Path,
    handcrafted_path: Path,
) -> tuple[bytes, dict[str, float | int]]:
    search_ids = set(search.rows)
    handcrafted_ids = set(handcrafted.rows)
    if search_ids != handcrafted_ids:
        missing_hce = sorted(search_ids - handcrafted_ids)
        extra_hce = sorted(handcrafted_ids - search_ids)
        detail: list[str] = []
        if missing_hce:
            detail.append(
                "missing HCE sampleIds=" + ", ".join(repr(v) for v in missing_hce[:5])
            )
        if extra_hce:
            detail.append(
                "extra HCE sampleIds=" + ", ".join(repr(v) for v in extra_hce[:5])
            )
        raise ValueError(
            "search/HCE sampleId sets differ"
            + (": " + "; ".join(detail) if detail else "")
        )

    encoded_rows: list[bytes] = []
    residuals: list[float] = []
    for sample_id in search.order:
        search_row = search.rows[sample_id]
        hce_row = handcrafted.rows[sample_id]
        if search_row.normalized_ofen != hce_row.normalized_ofen:
            raise ValueError(
                f"sampleId {sample_id!r}: normalized OFEN mismatch between "
                f"{search_path}:{search_row.line_number} and "
                f"{handcrafted_path}:{hce_row.line_number}"
            )
        if search_row.input_signature != hce_row.input_signature:
            raise ValueError(
                f"sampleId {sample_id!r}: exact NNUE input signature mismatch"
            )

        residual = search_row.target_cp_stm - hce_row.target_cp_stm
        residuals.append(residual)
        output = dict(search_row.record)
        if "outcomeStm" in output:
            output["searchOutcomeStm"] = output.pop("outcomeStm")
        if "sideToMoveScore" in output:
            output["searchSideToMoveScore"] = output.pop("sideToMoveScore")
        output.update(
            {
                "ofen": search_row.normalized_ofen,
                "targetCpStm": _compact_number(residual),
                "searchTargetCpStm": _compact_number(search_row.target_cp_stm),
                "handcraftedCpStm": _compact_number(hce_row.target_cp_stm),
                "targetSemantics": TARGET_SEMANTICS,
                "networkSemantics": NETWORK_SEMANTICS,
                "nnueInputSignature": search_row.input_signature,
            }
        )
        encoded_rows.append(
            json.dumps(
                output,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )

    count = len(residuals)
    return b"".join(encoded_rows), {
        "count": count,
        "minimumCp": min(residuals),
        "maximumCp": max(residuals),
        "meanCp": sum(residuals) / count,
        "meanAbsoluteCp": sum(abs(value) for value in residuals) / count,
    }


def _stage_bytes(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return name
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _cleanup(name: str | None) -> None:
    if name is None:
        return
    try:
        os.unlink(name)
    except OSError:
        pass


def build_residual_targets(
    *,
    search_path: Path,
    handcrafted_path: Path,
    output_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    search_resolved = search_path.resolve(strict=True)
    handcrafted_resolved = handcrafted_path.resolve(strict=True)
    output_resolved = output_path.resolve()
    manifest_resolved = manifest_path.resolve()
    if search_resolved == handcrafted_resolved:
        raise ValueError("search teacher and HCE labels must be different files")
    named_paths = {
        "search teacher": search_resolved,
        "HCE labels": handcrafted_resolved,
        "output": output_resolved,
        "manifest": manifest_resolved,
    }
    if len(set(named_paths.values())) != len(named_paths):
        collisions: list[str] = []
        items = list(named_paths.items())
        for index, (left_name, left_path) in enumerate(items):
            for right_name, right_path in items[index + 1 :]:
                if left_path == right_path:
                    collisions.append(f"{left_name} and {right_name}")
        raise ValueError("paths must differ: " + ", ".join(collisions))
    search_pin = _file_pin(search_path)
    handcrafted_pin = _file_pin(handcrafted_path)
    search = _load_source(search_path, "search")
    handcrafted = _load_source(handcrafted_path, "HCE")
    payload, statistics = _aligned_payload(
        search,
        handcrafted,
        search_path=search_path.resolve(),
        handcrafted_path=handcrafted_path.resolve(),
    )
    output_pin = _pin_bytes(output_path, payload)
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "targetSemantics": TARGET_SEMANTICS,
        "networkSemantics": NETWORK_SEMANTICS,
        "alignment": {
            "key": "sampleId",
            "normalizedOfenExact": True,
            "nnueInputSignatureExact": True,
            "coverage": "one-to-one",
            "order": "search-teacher",
        },
        "searchTeacher": search_pin,
        "handcraftedLabels": handcrafted_pin,
        "tool": _file_pin(Path(__file__)),
        "output": output_pin,
        "residual": statistics,
    }
    manifest_payload = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )

    output_temporary: str | None = None
    manifest_temporary: str | None = None
    try:
        output_temporary = _stage_bytes(output_path, payload)
        manifest_temporary = _stage_bytes(manifest_path, manifest_payload)
        if not _same_pin(_file_pin(search_path), search_pin):
            raise ValueError("search teacher changed while residual targets were built")
        if not _same_pin(_file_pin(handcrafted_path), handcrafted_pin):
            raise ValueError("HCE labels changed while residual targets were built")
        os.replace(output_temporary, output_path)
        output_temporary = None
        os.replace(manifest_temporary, manifest_path)
        manifest_temporary = None
    finally:
        _cleanup(output_temporary)
        _cleanup(manifest_temporary)
    return manifest


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    payload = b"".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for record in records
    )
    path.write_bytes(payload)


def self_test() -> None:
    initial = (
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
        "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
    )
    sparse = "5k4/10/10/10/10/10/10/10/10/4K5[-/-/-/-] b - - 0 1"
    search_rows = [
        {
            "sampleId": "b",
            "ofen": sparse,
            "targetCpStm": -31,
            "outcomeStm": 0.0,
        },
        {
            "sampleId": "a",
            "ofen": initial,
            "targetCpStm": 18.5,
            "sideToMoveScore": 0.75,
        },
    ]
    hce_rows = [
        {"sampleId": "a", "ofen": initial, "targetCpStm": -1.5},
        {"sampleId": "b", "ofen": sparse, "targetCpStm": -11},
    ]
    with tempfile.TemporaryDirectory(prefix="omega-nnue-residual-") as directory:
        root = Path(directory)
        search_path = root / "search.jsonl"
        hce_path = root / "hce.jsonl"
        output_path = root / "residual.jsonl"
        manifest_path = root / "residual.manifest.json"
        _write_jsonl(search_path, search_rows)
        _write_jsonl(hce_path, hce_rows)
        first = build_residual_targets(
            search_path=search_path,
            handcrafted_path=hce_path,
            output_path=output_path,
            manifest_path=manifest_path,
        )
        first_payload = output_path.read_bytes()
        first_manifest = manifest_path.read_bytes()
        second = build_residual_targets(
            search_path=search_path,
            handcrafted_path=hce_path,
            output_path=output_path,
            manifest_path=manifest_path,
        )
        if first != second:
            raise AssertionError("residual manifest is not deterministic")
        if output_path.read_bytes() != first_payload:
            raise AssertionError("residual JSONL is not deterministic")
        if manifest_path.read_bytes() != first_manifest:
            raise AssertionError("residual manifest bytes are not deterministic")
        rows = [
            json.loads(line)
            for line in first_payload.decode("utf-8").splitlines()
        ]
        if [row["sampleId"] for row in rows] != ["b", "a"]:
            raise AssertionError("search-teacher ordering was not preserved")
        if rows[0]["targetCpStm"] != -20 or rows[1]["targetCpStm"] != 20:
            raise AssertionError("residual target subtraction is incorrect")
        if "outcomeStm" in rows[0] or "sideToMoveScore" in rows[1]:
            raise AssertionError("absolute outcome labels leaked into residual targets")
        if (
            rows[0].get("searchOutcomeStm") != 0.0
            or rows[1].get("searchSideToMoveScore") != 0.75
        ):
            raise AssertionError("search outcome labels were not preserved")
        if any(row["targetSemantics"] != TARGET_SEMANTICS for row in rows):
            raise AssertionError("residual semantic tag is missing")

        bad_hce = list(hce_rows)
        bad_hce[0] = dict(bad_hce[0], ofen=sparse)
        _write_jsonl(hce_path, bad_hce)
        try:
            build_residual_targets(
                search_path=search_path,
                handcrafted_path=hce_path,
                output_path=output_path,
                manifest_path=manifest_path,
            )
        except ValueError as error:
            if "OFEN mismatch" not in str(error):
                raise
        else:
            raise AssertionError("misaligned OFEN was accepted")
    print("residual-target self-test passed")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Align search-teacher and HCE-labeled JSONL by exact sampleId and "
            "NNUE input, then emit search-minus-HCE correction targets."
        )
    )
    parser.add_argument("--search-teacher", type=Path)
    parser.add_argument("--handcrafted", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    if args.self_test:
        self_test()
        if (
            args.search_teacher is None
            and args.handcrafted is None
            and args.output is None
            and args.manifest is None
        ):
            return 0
    required = {
        "--search-teacher": args.search_teacher,
        "--handcrafted": args.handcrafted,
        "--output": args.output,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError("missing required arguments: " + ", ".join(missing))
    assert args.search_teacher is not None
    assert args.handcrafted is not None
    assert args.output is not None
    manifest_path = (
        args.manifest
        if args.manifest is not None
        else args.output.with_suffix(args.output.suffix + ".manifest.json")
    )
    manifest = build_residual_targets(
        search_path=args.search_teacher,
        handcrafted_path=args.handcrafted,
        output_path=args.output,
        manifest_path=manifest_path,
    )
    print(
        f"wrote {manifest['residual']['count']} residual rows: "
        f"{args.output.resolve()}"
    )
    print(f"wrote manifest: {manifest_path.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, AssertionError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
