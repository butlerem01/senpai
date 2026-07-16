#!/usr/bin/env python3
"""Convert verified OMTB3WDL/OMTB4WDL artifacts to OMTBPROD.

This is intentionally a strict, offline bridge.  It accepts only the frozen
full-table source layouts and explicitly remaps their four-valued file codes
to the production WDL5 encoding.  Search and evaluation do not call it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from production_format import (
    DRAW,
    INVALID,
    LOSS,
    WIN,
    encode_theoretical_wdl,
    material_spec,
    read_table,
    write_table,
)


MAX_HEADER_BYTES = 64 * 1024
SOURCE_CODES = {"invalid": 0, "loss": 2, "draw": 3, "win": 4}
INDEX_NAME = "D4-first-piece-v1"
SQUARE_COUNT = 104

THREE_MAN_ORDER = ["strong_king", "role_piece", "weak_king", "turn"]
FOUR_MAN_ORDERS = {
    "KRKC": ["rook_king", "rook", "champion_king", "champion", "turn"],
    "KRKN": ["rook_king", "rook", "knight_king", "knight", "turn"],
    "KWKN": ["wizard_king", "wizard", "knight_king", "knight", "turn"],
}
# Retain the public name used by existing tests and scripts.
FOUR_MAN_ORDER = FOUR_MAN_ORDERS["KRKC"]

THREE_MAN_RULES = (
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "krk-theoretical-wdl;kck-insufficient-material-v1"
)
FOUR_MAN_RULES = (
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "krkc-theoretical-wdl;krk-OMTB3WDL-v1;kck-insufficient-material-v1"
)
KRKN_RULES = (
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "krkn-theoretical-wdl;krk-OMTB3WDL-v1;knk-insufficient-material-v1"
)
KWKN_RULES = (
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "kwkn-theoretical-wdl;wizard-v1;knight-v1;"
    "kwk-insufficient-material-v1;knk-insufficient-material-v1"
)
KWKN_CAPTURE_POLICY = "kwk-knk-insufficient-material-v1"
KWKN_CAPTURE_POLICY_SHA256 = hashlib.sha256(
    KWKN_CAPTURE_POLICY.encode("ascii")
).hexdigest()


@dataclass(frozen=True)
class SourceArtifact:
    path: Path
    header: Mapping[str, object]
    material: str
    payload: bytes
    payload_sha256: str
    counts: Mapping[str, int]


def _read_json_line_artifact(path: Path) -> Tuple[dict, bytes]:
    path = Path(path)
    with path.open("rb") as stream:
        header_line = stream.readline(MAX_HEADER_BYTES + 1)
        if not header_line.endswith(b"\n") or len(header_line) > MAX_HEADER_BYTES:
            raise ValueError("missing or oversized source table header")
        try:
            header = json.loads(header_line[:-1].decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("source table header is not valid ASCII JSON") from exc
        payload = stream.read()
    if not isinstance(header, dict):
        raise ValueError("source table header must be a JSON object")
    return header, payload


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_int(header: Mapping[str, object], key: str, expected: int) -> None:
    value = header.get(key)
    if type(value) is not int or value != expected:
        raise ValueError(f"source {key} metadata mismatch")


def _source_counts(payload: bytes) -> Dict[str, int]:
    allowed = (SOURCE_CODES["invalid"], SOURCE_CODES["loss"],
               SOURCE_CODES["draw"], SOURCE_CODES["win"])
    if any(code not in allowed for code in payload):
        raise ValueError("source payload contains UNKNOWN or an unsupported WDL code")
    return {name: payload.count(code) for name, code in SOURCE_CODES.items()}


def read_source_artifact(path: Path) -> SourceArtifact:
    """Read and fully validate one frozen source artifact."""

    path = Path(path)
    header, payload = _read_json_line_artifact(path)
    magic = header.get("magic")
    material = header.get("material")
    if magic not in ("OMTB3WDL", "OMTB4WDL") or header.get("version") != 1:
        raise ValueError("unsupported source table magic/version")
    if not isinstance(material, str):
        raise ValueError("source material signature is missing")
    material = material.upper()
    spec = material_spec(material)

    if magic == "OMTB3WDL":
        if material not in ("KRK", "KCK"):
            raise ValueError("OMTB3WDL must contain KRK or KCK")
        expected_order = THREE_MAN_ORDER
        expected_rules = THREE_MAN_RULES
    else:
        if material not in FOUR_MAN_ORDERS:
            raise ValueError("OMTB4WDL must contain KRKC, KRKN, or KWKN")
        expected_order = FOUR_MAN_ORDERS[material]
        expected_rules = {
            "KRKC": FOUR_MAN_RULES,
            "KRKN": KRKN_RULES,
            "KWKN": KWKN_RULES,
        }[material]
        if header.get("complete") is not True or header.get("boundary") != "full":
            raise ValueError("production conversion requires a complete full-boundary OMTB4WDL table")
        _require_int(header, "dense_state_count", spec.state_count)

    if header.get("labelled_order") != expected_order:
        raise ValueError("source labelled-piece order mismatch")
    if header.get("index") != INDEX_NAME:
        raise ValueError("source index metadata mismatch")
    _require_int(header, "square_count", SQUARE_COUNT)
    _require_int(header, "state_count", spec.state_count)
    _require_int(header, "legal_count", spec.legal_count)
    if len(payload) != spec.state_count:
        raise ValueError("source payload size mismatch")
    if header.get("codes") != SOURCE_CODES:
        raise ValueError("source WDL code map mismatch")

    rules_sha256 = hashlib.sha256(expected_rules.encode("ascii")).hexdigest()
    if header.get("rules") != expected_rules or header.get("rules_sha256") != rules_sha256:
        raise ValueError("source rules fingerprint mismatch")
    payload_sha256 = _sha256_hex(payload)
    if header.get("payload_sha256") != payload_sha256:
        raise ValueError("source payload checksum mismatch")

    counts = _source_counts(payload)
    if header.get("counts") != counts:
        raise ValueError("source payload outcome counts mismatch")
    if counts["invalid"] != spec.state_count - spec.legal_count:
        raise ValueError("source invalid-state count mismatch")
    if sum(counts[name] for name in ("loss", "draw", "win")) != spec.legal_count:
        raise ValueError("source legal-state count mismatch")
    if material == "KCK" and (counts["loss"] or counts["win"]):
        raise ValueError("KCK source violates the insufficient-material draw policy")

    if magic == "OMTB4WDL":
        for name in SOURCE_CODES:
            value = header.get(f"{name}_count")
            if type(value) is not int or value != counts[name]:
                raise ValueError(f"source {name} count field mismatch")
        if material == "KWKN":
            if header.get("capture_policy_sha256") != KWKN_CAPTURE_POLICY_SHA256:
                raise ValueError("source KWKN capture-policy checksum mismatch")
        else:
            dependency_hash = header.get("krk_payload_sha256")
            if not isinstance(dependency_hash, str) or len(dependency_hash) != 64:
                raise ValueError("source KRK dependency checksum is missing")

    return SourceArtifact(path, header, material, payload, payload_sha256, counts)


def convert_artifact(input_path: Path, output_path: Path,
                     krk_dependency: Optional[Path] = None):
    """Validate, convert, write, and re-read one production table."""

    source = read_source_artifact(input_path)
    if source.material in ("KRKC", "KRKN"):
        if krk_dependency is None:
            raise ValueError(f"{source.material} conversion requires the source KRK dependency")
        dependency = read_source_artifact(krk_dependency)
        if dependency.material != "KRK":
            raise ValueError(f"{source.material} dependency must be an OMTB3WDL KRK table")
        if source.header.get("krk_payload_sha256") != dependency.payload_sha256:
            raise ValueError(f"{source.material} source was built from a different KRK dependency")
    elif krk_dependency is not None:
        raise ValueError("--krk-dependency is only valid for KRKC/KRKN conversion")

    production_wdl = encode_theoretical_wdl(source.payload)
    write_table(Path(output_path), source.material, production_wdl)
    converted = read_table(Path(output_path), expected_material=source.material)
    expected_counts = (
        source.counts["invalid"], source.counts["loss"], 0,
        source.counts["draw"], 0, source.counts["win"],
    )
    if converted.header.outcome_counts != expected_counts:
        raise AssertionError("production re-read changed the WDL outcome counts")
    if converted.wdl != production_wdl:
        raise AssertionError("production re-read changed the WDL payload")
    return converted.header


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path,
                        help="verified .omtb3 or complete .omtb4 input")
    parser.add_argument("--output", required=True, type=Path,
                        help="OMTBPROD output path")
    parser.add_argument("--krk-dependency", type=Path,
                        help="OMTB3WDL KRK file used to build a KRKC/KRKN input")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    header = convert_artifact(arguments.input, arguments.output, arguments.krk_dependency)
    counts = ", ".join(
        f"{name}={count}"
        for name, count in zip(
            ("invalid", "loss", "blessed-loss", "draw", "cursed-win", "win"),
            header.outcome_counts,
        )
    )
    print(
        f"OMTBPROD verified: material={header.material} states={header.state_count} "
        f"legal={header.legal_count} {counts} "
        f"payload_sha256={header.payload_sha256.hex()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
