#!/usr/bin/env python3
"""Production Omega tablebase container format.

This module is deliberately independent of the retrograde generators.  It
turns an already generated dense WDL/DTZ payload into a fixed-size,
checksummed little-endian file and validates the same file on read.  The
native reader in ``src/production_format.*`` implements this exact layout.

The format stores one WDL byte per dense index.  Codes are the signed
five-valued WDL order shifted by three::

    invalid=0, loss=1, blessed-loss=2, draw=3, cursed-win=4, win=5

DTZ is optional unsigned little-endian uint16, one value per dense index.
KRK, KCK, KRKC, KRKN, KWKN, and KCKW use the same checked container.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Tuple, Union


MAGIC = b"OMTBPROD"
VERSION = 1
HEADER_SIZE = 256
ENDIAN_TAG = 0x01020304
SQUARE_COUNT = 104
TURN_COUNT = 2
RULES_ID = 1
INDEX_ID = 1  # D4-first-piece-v1

FLAG_HAS_DTZ = 1
WDL_ENCODING_BYTE_FIVE = 1
DTZ_ENCODING_NONE = 0
DTZ_ENCODING_UINT16_LE = 1

INVALID = 0
LOSS = 1
BLESSED_LOSS = 2
DRAW = 3
CURSED_WIN = 4
WIN = 5
WDL_CODES = (INVALID, LOSS, BLESSED_LOSS, DRAW, CURSED_WIN, WIN)
WDL_NAMES = ("invalid", "loss", "blessed_loss", "draw", "cursed_win", "win")

# tools/omega_tb/three_man_wdl.py predates the production WDL5 encoding.
# Keep its remap explicit so its UNKNOWN=1 work code can never leak into a
# production file as a false loss.
FOUNDATION_INVALID = 0
FOUNDATION_UNKNOWN = 1
FOUNDATION_LOSS = 2
FOUNDATION_DRAW = 3
FOUNDATION_WIN = 4

PIECE_NONE = 0
PIECE_KING = 1
PIECE_ROOK = 2
PIECE_CHAMPION = 3
PIECE_KNIGHT = 4
PIECE_WIZARD = 5
ROLE_NONE = 0xFF
ROLE_ZERO = 0
ROLE_ONE = 1

MATERIAL_KRK = 1
MATERIAL_KCK = 2
MATERIAL_KRKC = 3
MATERIAL_KRKN = 4
MATERIAL_KWKN = 5
MATERIAL_KCKW = 6

RULES_DESCRIPTION = (
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "king-v1;rook-v1;champion-v1;knight-v1;100-ply-auto-draw-v1;"
    "insufficient-k-plus-one-nbcw-v1;wdl5-dtz16-v1"
)
RULES_FINGERPRINT = hashlib.sha256(RULES_DESCRIPTION.encode("ascii")).digest()
RULES_FINGERPRINT_HEX = RULES_FINGERPRINT.hex()

# Legacy materials retain their original fingerprint. KWKN and KCKW have
# material-specific identities that freeze their fairy-piece geometry and
# theoretical capture boundaries.
KWKN_RULES_DESCRIPTION = (
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "king-v1;wizard-v1;knight-v1;100-ply-auto-draw-v1;"
    "insufficient-k-plus-one-nbcw-v1;kwkn-theoretical-wdl-v1;"
    "wdl5-dtz16-v1"
)
KWKN_RULES_FINGERPRINT = hashlib.sha256(KWKN_RULES_DESCRIPTION.encode("ascii")).digest()
KWKN_RULES_FINGERPRINT_HEX = KWKN_RULES_FINGERPRINT.hex()

KCKW_RULES_DESCRIPTION = (
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "king-v1;champion-v1;wizard-v1;100-ply-auto-draw-v1;"
    "insufficient-k-plus-one-nbcw-v1;kckw-theoretical-wdl-v1;"
    "wdl5-dtz16-v1"
)
KCKW_RULES_FINGERPRINT = hashlib.sha256(KCKW_RULES_DESCRIPTION.encode("ascii")).digest()
KCKW_RULES_FINGERPRINT_HEX = KCKW_RULES_FINGERPRINT.hex()

RULES_OFFSET = 144
PAYLOAD_HASH_OFFSET = 176
HEADER_HASH_OFFSET = 208
RESERVED_OFFSET = 240


@dataclass(frozen=True)
class MaterialSpec:
    name: str
    code: int
    piece_count: int
    pieces: Tuple[int, int, int, int]
    roles: Tuple[int, int, int, int]
    state_count: int
    legal_count: int


MATERIALS: Mapping[str, MaterialSpec] = {
    "KRK": MaterialSpec(
        "KRK", MATERIAL_KRK, 3,
        (PIECE_KING, PIECE_ROOK, PIECE_KING, PIECE_NONE),
        (ROLE_ZERO, ROLE_ZERO, ROLE_ONE, ROLE_NONE),
        273_816, 235_033,
    ),
    "KCK": MaterialSpec(
        "KCK", MATERIAL_KCK, 3,
        (PIECE_KING, PIECE_CHAMPION, PIECE_KING, PIECE_NONE),
        (ROLE_ZERO, ROLE_ZERO, ROLE_ONE, ROLE_NONE),
        273_816, 244_779,
    ),
    "KRKC": MaterialSpec(
        "KRKC", MATERIAL_KRKC, 4,
        (PIECE_KING, PIECE_ROOK, PIECE_KING, PIECE_CHAMPION),
        (ROLE_ZERO, ROLE_ZERO, ROLE_ONE, ROLE_ONE),
        27_594_696, 22_607_206,
    ),
    "KRKN": MaterialSpec(
        "KRKN", MATERIAL_KRKN, 4,
        (PIECE_KING, PIECE_ROOK, PIECE_KING, PIECE_KNIGHT),
        (ROLE_ZERO, ROLE_ZERO, ROLE_ONE, ROLE_ONE),
        27_594_696, 23_034_346,
    ),
    "KWKN": MaterialSpec(
        "KWKN", MATERIAL_KWKN, 4,
        (PIECE_KING, PIECE_WIZARD, PIECE_KING, PIECE_KNIGHT),
        (ROLE_ZERO, ROLE_ZERO, ROLE_ONE, ROLE_ONE),
        27_594_696, 24_078_355,
    ),
    "KCKW": MaterialSpec(
        "KCKW", MATERIAL_KCKW, 4,
        (PIECE_KING, PIECE_CHAMPION, PIECE_KING, PIECE_WIZARD),
        (ROLE_ZERO, ROLE_ZERO, ROLE_ONE, ROLE_ONE),
        27_594_696, 23_651_215,
    ),
}
MATERIALS_BY_CODE = {spec.code: spec for spec in MATERIALS.values()}


@dataclass(frozen=True)
class Header:
    material: str
    state_count: int
    legal_count: int
    outcome_counts: Tuple[int, int, int, int, int, int]
    has_dtz: bool
    wdl_offset: int
    wdl_size: int
    dtz_offset: int
    dtz_size: int
    rules_fingerprint: bytes
    payload_sha256: bytes
    header_sha256: bytes


@dataclass(frozen=True)
class Table:
    header: Header
    wdl: bytes
    # Raw little-endian uint16 payload. Keeping it packed avoids turning a
    # future 27.6-million-state KRKC table into a very large Python tuple.
    dtz: Optional[bytes]

    def dtz_at(self, index: int) -> int:
        if self.dtz is None:
            raise ValueError("table has no DTZ payload")
        if not 0 <= index < self.header.state_count:
            raise IndexError(index)
        return struct.unpack_from("<H", self.dtz, index * 2)[0]


def material_spec(material: Union[str, int]) -> MaterialSpec:
    if isinstance(material, int):
        try:
            return MATERIALS_BY_CODE[material]
        except KeyError as exc:
            raise ValueError(f"unsupported material code: {material}") from exc
    try:
        return MATERIALS[str(material).upper()]
    except KeyError as exc:
        raise ValueError(f"unsupported material signature: {material}") from exc


def rules_fingerprint(material: Union[str, int]) -> bytes:
    name = material_spec(material).name
    if name == "KWKN":
        return KWKN_RULES_FINGERPRINT
    if name == "KCKW":
        return KCKW_RULES_FINGERPRINT
    return RULES_FINGERPRINT


def encode_theoretical_wdl(foundation_wdl: Union[bytes, bytearray, memoryview]) -> bytes:
    """Map the verified three-man solver's codes into production WDL5."""

    source = bytes(foundation_wdl)
    mapping = bytes.maketrans(
        bytes((FOUNDATION_INVALID, FOUNDATION_LOSS, FOUNDATION_DRAW, FOUNDATION_WIN)),
        bytes((INVALID, LOSS, DRAW, WIN)),
    )
    if any(code not in (FOUNDATION_INVALID, FOUNDATION_LOSS, FOUNDATION_DRAW, FOUNDATION_WIN)
           for code in source):
        raise ValueError("foundation WDL contains UNKNOWN or an unsupported code")
    return source.translate(mapping)


def _header_digest(header: bytes) -> bytes:
    if len(header) != HEADER_SIZE:
        raise ValueError("production header has the wrong size")
    canonical = bytearray(header)
    canonical[HEADER_HASH_OFFSET:HEADER_HASH_OFFSET + 32] = bytes(32)
    return hashlib.sha256(canonical).digest()


def _outcome_counts(wdl: bytes) -> Tuple[int, int, int, int, int, int]:
    if any(code not in WDL_CODES for code in wdl):
        raise ValueError("WDL payload contains an unsupported code")
    return tuple(wdl.count(code) for code in WDL_CODES)


def _validate_payload_semantics(spec: MaterialSpec, wdl: bytes,
                                dtz_bytes: bytes) -> Tuple[int, ...]:
    if len(wdl) != spec.state_count:
        raise ValueError(
            f"{spec.name} WDL payload has {len(wdl):,} states; "
            f"expected {spec.state_count:,}"
        )
    counts = _outcome_counts(wdl)
    if counts[INVALID] != spec.state_count - spec.legal_count:
        raise ValueError(f"{spec.name} invalid-state count does not match its index metadata")
    if sum(counts[1:]) != spec.legal_count:
        raise ValueError(f"{spec.name} legal-state count does not match its index metadata")
    if spec.name == "KCK" and (
        counts[LOSS] or counts[BLESSED_LOSS] or counts[CURSED_WIN] or counts[WIN]
    ):
        raise ValueError("KCK violates the current insufficient-material draw policy")
    if not dtz_bytes and (counts[BLESSED_LOSS] or counts[CURSED_WIN]):
        raise ValueError("blessed/cursed WDL codes require a DTZ payload")
    if dtz_bytes and len(dtz_bytes) != spec.state_count * 2:
        raise ValueError(f"{spec.name} DTZ payload size mismatch")
    return counts


def _encode_dtz(dtz: Optional[Union[bytes, bytearray, memoryview, Sequence[int]]],
                state_count: int) -> bytes:
    if dtz is None:
        return b""
    if isinstance(dtz, (bytes, bytearray, memoryview)):
        encoded = bytes(dtz)
    else:
        if len(dtz) != state_count:
            raise ValueError("DTZ value count does not match the WDL state count")
        encoded = bytearray(state_count * 2)
        for index, value in enumerate(dtz):
            if not 0 <= int(value) <= 0xFFFF:
                raise ValueError("DTZ value is outside uint16 range")
            struct.pack_into("<H", encoded, index * 2, int(value))
        encoded = bytes(encoded)
    if len(encoded) != state_count * 2:
        raise ValueError("DTZ payload must contain one uint16 per state")
    return encoded


def _build_header(spec: MaterialSpec, wdl: bytes, dtz: bytes) -> bytes:
    counts = _validate_payload_semantics(spec, wdl, dtz)
    has_dtz = bool(dtz)
    wdl_offset = HEADER_SIZE
    wdl_size = len(wdl)
    dtz_offset = HEADER_SIZE + wdl_size if has_dtz else 0
    dtz_size = len(dtz)
    payload_hasher = hashlib.sha256()
    payload_hasher.update(wdl)
    payload_hasher.update(dtz)
    payload_hash = payload_hasher.digest()

    header = bytearray(HEADER_SIZE)
    header[0:8] = MAGIC
    struct.pack_into("<H", header, 8, VERSION)
    struct.pack_into("<H", header, 10, HEADER_SIZE)
    struct.pack_into("<I", header, 12, ENDIAN_TAG)
    header[16] = spec.code
    header[17] = spec.piece_count
    header[18] = WDL_ENCODING_BYTE_FIVE
    header[19] = DTZ_ENCODING_UINT16_LE if has_dtz else DTZ_ENCODING_NONE
    struct.pack_into("<I", header, 20, FLAG_HAS_DTZ if has_dtz else 0)
    struct.pack_into("<H", header, 24, SQUARE_COUNT)
    header[26] = TURN_COUNT
    header[27] = RULES_ID
    struct.pack_into("<I", header, 28, INDEX_ID)
    struct.pack_into("<Q", header, 32, spec.state_count)
    struct.pack_into("<Q", header, 40, spec.legal_count)
    struct.pack_into("<Q", header, 48, wdl_offset)
    struct.pack_into("<Q", header, 56, wdl_size)
    struct.pack_into("<Q", header, 64, dtz_offset)
    struct.pack_into("<Q", header, 72, dtz_size)
    header[80:84] = bytes(spec.pieces)
    header[84:88] = bytes(spec.roles)
    header[88] = ROLE_ZERO
    header[89] = ROLE_ONE
    header[90:96] = bytes(WDL_CODES)
    for index, count in enumerate(counts):
        struct.pack_into("<Q", header, 96 + index * 8, count)
    header[RULES_OFFSET:RULES_OFFSET + 32] = rules_fingerprint(spec.name)
    header[PAYLOAD_HASH_OFFSET:PAYLOAD_HASH_OFFSET + 32] = payload_hash
    header[HEADER_HASH_OFFSET:HEADER_HASH_OFFSET + 32] = _header_digest(header)
    return bytes(header)


def write_table(path: Path, material: Union[str, int],
                wdl: Union[bytes, bytearray, memoryview],
                dtz: Optional[Union[bytes, bytearray, memoryview, Sequence[int]]] = None) -> Header:
    """Atomically write and re-read a production tablebase file."""

    path = Path(path)
    spec = material_spec(material)
    wdl_bytes = bytes(wdl)
    dtz_bytes = _encode_dtz(dtz, spec.state_count)
    header_bytes = _build_header(spec, wdl_bytes, dtz_bytes)

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as stream:
            stream.write(header_bytes)
            stream.write(wdl_bytes)
            stream.write(dtz_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        verified = read_table(temporary, expected_material=spec.name)
        os.replace(temporary, path)
        return verified.header
    finally:
        if temporary.exists():
            temporary.unlink()


def read_table(path: Path, expected_material: Optional[Union[str, int]] = None,
               expected_rules_fingerprint: Optional[bytes] = None,
               require_dtz: bool = False) -> Table:
    """Read and fully validate a production tablebase file."""

    path = Path(path)
    with path.open("rb") as stream:
        header_bytes = stream.read(HEADER_SIZE)
        if len(header_bytes) != HEADER_SIZE:
            raise ValueError("truncated production header")
        if header_bytes[0:8] != MAGIC:
            raise ValueError("unsupported production table magic")
        version = struct.unpack_from("<H", header_bytes, 8)[0]
        header_size = struct.unpack_from("<H", header_bytes, 10)[0]
        if version != VERSION or header_size != HEADER_SIZE:
            raise ValueError("unsupported production table version")
        stored_header_hash = header_bytes[HEADER_HASH_OFFSET:HEADER_HASH_OFFSET + 32]
        if _header_digest(header_bytes) != stored_header_hash:
            raise ValueError("production header checksum mismatch")
        if struct.unpack_from("<I", header_bytes, 12)[0] != ENDIAN_TAG:
            raise ValueError("production endianness marker mismatch")

        spec = material_spec(header_bytes[16])
        if expected_material is not None and spec != material_spec(expected_material):
            raise ValueError("production material signature mismatch")
        if header_bytes[17] != spec.piece_count:
            raise ValueError("production piece-count metadata mismatch")
        if header_bytes[18] != WDL_ENCODING_BYTE_FIVE:
            raise ValueError("unsupported production WDL encoding")
        dtz_encoding = header_bytes[19]
        flags = struct.unpack_from("<I", header_bytes, 20)[0]
        has_dtz = bool(flags & FLAG_HAS_DTZ)
        if flags & ~FLAG_HAS_DTZ:
            raise ValueError("unsupported production table flags")
        if dtz_encoding != (DTZ_ENCODING_UINT16_LE if has_dtz else DTZ_ENCODING_NONE):
            raise ValueError("production DTZ metadata mismatch")
        if require_dtz and not has_dtz:
            raise ValueError("production table has no DTZ payload")
        if struct.unpack_from("<H", header_bytes, 24)[0] != SQUARE_COUNT:
            raise ValueError("production square-count metadata mismatch")
        if header_bytes[26] != TURN_COUNT or header_bytes[27] != RULES_ID:
            raise ValueError("production turn/rules metadata mismatch")
        if struct.unpack_from("<I", header_bytes, 28)[0] != INDEX_ID:
            raise ValueError("production index metadata mismatch")
        if tuple(header_bytes[80:84]) != spec.pieces or tuple(header_bytes[84:88]) != spec.roles:
            raise ValueError("production labelled-piece order mismatch")
        if tuple(header_bytes[88:90]) != (ROLE_ZERO, ROLE_ONE):
            raise ValueError("production side-to-move role metadata mismatch")
        if tuple(header_bytes[90:96]) != WDL_CODES:
            raise ValueError("production WDL code map mismatch")
        if any(header_bytes[RESERVED_OFFSET:HEADER_SIZE]):
            raise ValueError("production reserved header bytes are nonzero")

        state_count = struct.unpack_from("<Q", header_bytes, 32)[0]
        legal_count = struct.unpack_from("<Q", header_bytes, 40)[0]
        wdl_offset = struct.unpack_from("<Q", header_bytes, 48)[0]
        wdl_size = struct.unpack_from("<Q", header_bytes, 56)[0]
        dtz_offset = struct.unpack_from("<Q", header_bytes, 64)[0]
        dtz_size = struct.unpack_from("<Q", header_bytes, 72)[0]
        if state_count != spec.state_count or legal_count != spec.legal_count:
            raise ValueError("production state-count metadata mismatch")
        if wdl_offset != HEADER_SIZE or wdl_size != spec.state_count:
            raise ValueError("production WDL offset/size mismatch")
        expected_dtz_offset = HEADER_SIZE + wdl_size if has_dtz else 0
        expected_dtz_size = state_count * 2 if has_dtz else 0
        if dtz_offset != expected_dtz_offset or dtz_size != expected_dtz_size:
            raise ValueError("production DTZ offset/size mismatch")

        rules = header_bytes[RULES_OFFSET:RULES_OFFSET + 32]
        canonical_rules = rules_fingerprint(spec.name)
        requested_rules = canonical_rules if expected_rules_fingerprint is None \
            else bytes(expected_rules_fingerprint)
        if rules != requested_rules:
            raise ValueError("production rules fingerprint mismatch")
        if rules != canonical_rules:
            raise ValueError("unsupported production rules fingerprint")

        outcome_counts = tuple(
            struct.unpack_from("<Q", header_bytes, 96 + index * 8)[0]
            for index in range(6)
        )
        wdl = stream.read(wdl_size)
        dtz_bytes = stream.read(dtz_size)
        if len(wdl) != wdl_size or len(dtz_bytes) != dtz_size or stream.read(1):
            raise ValueError("production file size mismatch")
        payload_hasher = hashlib.sha256()
        payload_hasher.update(wdl)
        payload_hasher.update(dtz_bytes)
        payload_hash = payload_hasher.digest()
        if payload_hash != header_bytes[PAYLOAD_HASH_OFFSET:PAYLOAD_HASH_OFFSET + 32]:
            raise ValueError("production payload checksum mismatch")
        actual_counts = _validate_payload_semantics(spec, wdl, dtz_bytes)
        if actual_counts != outcome_counts:
            raise ValueError("production WDL outcome counts mismatch")

    dtz_values = dtz_bytes if has_dtz else None
    header = Header(
        spec.name, state_count, legal_count, outcome_counts, has_dtz,
        wdl_offset, wdl_size, dtz_offset, dtz_size, rules, payload_hash,
        stored_header_hash,
    )
    return Table(header, wdl, dtz_values)
