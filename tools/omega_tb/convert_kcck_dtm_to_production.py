#!/usr/bin/env python3
"""Convert the frozen OMTB4DTM KCCK solve to the checked uint16 companion."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from convert_to_production import (
    FOUR_MAN_ORDERS,
    INDEX_NAME,
    KCCK_CAPTURE_POLICY_SHA256,
    KCCK_RULES,
    MAX_HEADER_BYTES,
    SOURCE_CODES,
    SQUARE_COUNT,
    encode_theoretical_wdl,
    read_source_artifact,
)
from production_format import (
    DTM_NO_DISTANCE,
    KCCK_DTM_DECISIVE_COUNT,
    KCCK_DTM_MAXIMUM,
    KCCK_SOURCE_DTM_CONTAINER_SHA256,
    KCCK_SOURCE_DTM_PAYLOAD_SHA256,
    read_dtm_table,
    read_table,
    write_dtm_table,
)


SOURCE_NO_DISTANCE = 0xFFFFFFFF
SOURCE_DTM_MAGIC = "OMTB4DTM"
SOURCE_DTM_ENCODING = "u32le"


def _file_sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.digest()


def _read_source_dtm(path: Path) -> Tuple[Mapping[str, object], bytes]:
    path = Path(path)
    with path.open("rb") as stream:
        header_line = stream.readline(MAX_HEADER_BYTES + 1)
        if not header_line.endswith(b"\n") or len(header_line) > MAX_HEADER_BYTES:
            raise ValueError("missing or oversized source DTM header")
        try:
            header = json.loads(header_line[:-1].decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("source DTM header is not valid ASCII JSON") from exc
        payload = stream.read()
    if not isinstance(header, dict):
        raise ValueError("source DTM header must be a JSON object")
    return header, payload


def _require_int(header: Mapping[str, object], key: str, expected: int) -> None:
    value = header.get(key)
    if type(value) is not int or value != expected:
        raise ValueError(f"source DTM {key} metadata mismatch")


def convert_kcck_dtm(
    dtm_input: Path,
    source_wdl_input: Path,
    production_wdl_input: Path,
    output: Path,
):
    """Validate every source record, narrow it, write it, and re-read it."""

    dtm_input = Path(dtm_input)
    if _file_sha256(dtm_input) != KCCK_SOURCE_DTM_CONTAINER_SHA256:
        raise ValueError("source DTM container checksum mismatch")
    header, source_dtm = _read_source_dtm(dtm_input)
    source_wdl = read_source_artifact(source_wdl_input)
    if source_wdl.material != "KCCK":
        raise ValueError("source WDL must be the frozen KCCK OMTB4WDL table")
    production_wdl = read_table(production_wdl_input, "KCCK")
    expected_production_wdl = encode_theoretical_wdl(source_wdl.payload)
    if production_wdl.wdl != expected_production_wdl:
        raise ValueError("production KCCK WDL differs from the frozen source WDL")

    spec_states = production_wdl.header.state_count
    if header.get("magic") != SOURCE_DTM_MAGIC or header.get("version") != 1:
        raise ValueError("unsupported source DTM magic/version")
    if header.get("material") != "KCCK":
        raise ValueError("source DTM material signature mismatch")
    if header.get("labelled_order") != FOUR_MAN_ORDERS["KCCK"]:
        raise ValueError("source DTM labelled-piece order mismatch")
    if header.get("index") != INDEX_NAME:
        raise ValueError("source DTM index metadata mismatch")
    _require_int(header, "square_count", SQUARE_COUNT)
    _require_int(header, "dense_state_count", spec_states)
    _require_int(header, "state_count", spec_states)
    if header.get("complete") is not True or header.get("boundary") != "full":
        raise ValueError("source DTM must be complete and full-boundary")
    if (
        header.get("distance_unit") != "ply-to-checkmate"
        or header.get("terminal_mate") != 0
        or header.get("win_recurrence") != "1+min(loss-child)"
        or header.get("loss_recurrence") != "1+max(win-child)"
        or header.get("payload_encoding") != SOURCE_DTM_ENCODING
    ):
        raise ValueError("source DTM distance semantics mismatch")
    _require_int(header, "no_distance", SOURCE_NO_DISTANCE)
    _require_int(header, "decisive_count", KCCK_DTM_DECISIVE_COUNT)
    _require_int(header, "max_dtm", KCCK_DTM_MAXIMUM)
    if header.get("source_wdl_payload_sha256") != source_wdl.payload_sha256:
        raise ValueError("source DTM is bound to a different WDL payload")
    expected_rules = hashlib.sha256(KCCK_RULES.encode("ascii")).hexdigest()
    if (
        header.get("source_rules_sha256") != expected_rules
        or header.get("source_capture_policy_sha256")
        != KCCK_CAPTURE_POLICY_SHA256
    ):
        raise ValueError("source DTM rules/capture binding mismatch")
    if bytes.fromhex(source_wdl.payload_sha256) != production_wdl.header.payload_sha256 \
            and production_wdl.wdl == source_wdl.payload:
        # Defensive sanity check: source and production encodings are expected
        # to differ, even though their outcomes are identical.
        raise ValueError("source/production WDL encoding identity changed")
    if len(source_dtm) != spec_states * 4:
        raise ValueError("source DTM payload size mismatch")
    if hashlib.sha256(source_dtm).digest() != KCCK_SOURCE_DTM_PAYLOAD_SHA256:
        raise ValueError("source DTM payload checksum mismatch")
    if header.get("payload_sha256") != KCCK_SOURCE_DTM_PAYLOAD_SHA256.hex():
        raise ValueError("source DTM header payload checksum mismatch")

    packed = bytearray(spec_states * 2)
    decisive_count = 0
    maximum = 0
    within = {20: 0, 40: 0, 60: 0, 80: 0, 100: 0}
    unpack = struct.Struct("<I").unpack_from
    for index, outcome in enumerate(source_wdl.payload):
        value = unpack(source_dtm, index * 4)[0]
        decisive = outcome in (SOURCE_CODES["loss"], SOURCE_CODES["win"])
        if decisive != (value != SOURCE_NO_DISTANCE):
            raise ValueError(f"source DTM/WDL sentinel mismatch at index {index}")
        if not decisive:
            struct.pack_into("<H", packed, index * 2, DTM_NO_DISTANCE)
            continue
        if value > KCCK_DTM_MAXIMUM:
            raise ValueError(f"source DTM exceeds uint16/frozen maximum at index {index}")
        if (
            outcome == SOURCE_CODES["win"] and value % 2 == 0
        ) or (
            outcome == SOURCE_CODES["loss"] and value % 2 != 0
        ):
            raise ValueError(f"source DTM parity mismatch at index {index}")
        if value == 0 and outcome != SOURCE_CODES["loss"]:
            raise ValueError(f"source DTM zero is not a loss at index {index}")
        struct.pack_into("<H", packed, index * 2, value)
        decisive_count += 1
        maximum = max(maximum, value)
        for boundary in within:
            if value <= boundary:
                within[boundary] += 1

    if decisive_count != KCCK_DTM_DECISIVE_COUNT or maximum != KCCK_DTM_MAXIMUM:
        raise ValueError("source DTM decisive count or maximum changed")
    for boundary, count in within.items():
        _require_int(header, f"within_{boundary}", count)
    _require_int(header, "beyond_100", decisive_count - within[100])

    written = write_dtm_table(output, production_wdl, packed)
    checked = read_dtm_table(output, production_wdl)
    if checked.dtm != packed or checked.header != written:
        raise AssertionError("production DTM re-read changed the companion")
    return checked.header


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dtm-input", required=True, type=Path)
    parser.add_argument("--source-wdl", required=True, type=Path)
    parser.add_argument("--production-wdl", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    header = convert_kcck_dtm(
        arguments.dtm_input,
        arguments.source_wdl,
        arguments.production_wdl,
        arguments.output,
    )
    print(
        "OMTBDTM1 verified: material=KCCK "
        f"states={header.state_count} decisive={header.decisive_count} "
        f"max_dtm={header.maximum_dtm} "
        f"payload_sha256={header.payload_sha256.hex()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
