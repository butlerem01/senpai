import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from convert_to_production import (
    SOURCE_CODES,
    THREE_MAN_ORDER,
    THREE_MAN_RULES,
    convert_artifact,
    read_source_artifact,
)
from production_format import DRAW, LOSS, WIN, material_spec, read_table


def source_krk_payload():
    spec = material_spec("KRK")
    invalid = spec.state_count - spec.legal_count
    return bytes([0]) * invalid + bytes([2]) * 339 + bytes([3]) * 232_962 + bytes([4]) * 1_732


def write_source(path: Path, payload: bytes, **changes):
    spec = material_spec("KRK")
    counts = {name: payload.count(code) for name, code in SOURCE_CODES.items()}
    header = {
        "magic": "OMTB3WDL",
        "version": 1,
        "material": "KRK",
        "labelled_order": THREE_MAN_ORDER,
        "index": "D4-first-piece-v1",
        "square_count": 104,
        "state_count": spec.state_count,
        "legal_count": spec.legal_count,
        "rules": THREE_MAN_RULES,
        "rules_sha256": hashlib.sha256(THREE_MAN_RULES.encode("ascii")).hexdigest(),
        "codes": SOURCE_CODES,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "counts": counts,
    }
    header.update(changes)
    path.write_bytes(json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n" + payload)


class ConvertToProductionTests(unittest.TestCase):
    def test_full_krk_conversion_and_code_remap(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.omtb3"
            output = Path(directory) / "production.omtb"
            write_source(source, source_krk_payload())
            artifact = read_source_artifact(source)
            self.assertEqual("KRK", artifact.material)
            convert_artifact(source, output)
            table = read_table(output, "KRK")
            self.assertEqual((38_783, 339, 0, 232_962, 0, 1_732), table.header.outcome_counts)
            self.assertEqual({0, LOSS, DRAW, WIN}, set(table.wdl))

    def test_unknown_code_and_bad_checksum_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.omtb3"
            payload = bytearray(source_krk_payload())
            payload[-1] = 1
            write_source(source, bytes(payload))
            with self.assertRaisesRegex(ValueError, "UNKNOWN"):
                read_source_artifact(source)

            write_source(source, source_krk_payload(), payload_sha256="0" * 64)
            with self.assertRaisesRegex(ValueError, "checksum"):
                read_source_artifact(source)

    def test_oversized_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.omtb3"
            source.write_bytes(b"{" + b" " * (64 * 1024) + b"}\n")
            with self.assertRaisesRegex(ValueError, "oversized"):
                read_source_artifact(source)


if __name__ == "__main__":
    unittest.main()
