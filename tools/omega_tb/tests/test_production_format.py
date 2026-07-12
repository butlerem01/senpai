import tempfile
import unittest
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from production_format import (
    DRAW,
    HEADER_SIZE,
    LOSS,
    RULES_FINGERPRINT,
    WIN,
    encode_theoretical_wdl,
    material_spec,
    read_table,
    write_table,
)


def krk_payload():
    spec = material_spec("KRK")
    invalid = spec.state_count - spec.legal_count
    # Exact current theoretical-WDL populations from the verified solver.
    return bytes([0]) * invalid + bytes([LOSS]) * 339 + bytes([DRAW]) * 232_962 + bytes([WIN]) * 1_732


def kck_payload():
    spec = material_spec("KCK")
    return bytes([0]) * (spec.state_count - spec.legal_count) + bytes([DRAW]) * spec.legal_count


class ProductionFormatTests(unittest.TestCase):
    def test_verified_three_man_codes_are_explicitly_remapped(self):
        self.assertEqual(bytes((0, LOSS, DRAW, WIN)), encode_theoretical_wdl(bytes((0, 2, 3, 4))))
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            encode_theoretical_wdl(bytes((1,)))

    def test_krk_round_trip_and_optional_dtz(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "krk.omtb"
            payload = krk_payload()
            header = write_table(path, "krk", payload)
            table = read_table(path, expected_material="KRK")
            self.assertEqual(header, table.header)
            self.assertEqual(payload, table.wdl)
            self.assertIsNone(table.dtz)
            self.assertEqual(RULES_FINGERPRINT, table.header.rules_fingerprint)
            # Shared golden digests also asserted by the native loader test.
            self.assertEqual(
                "9df95c3ab43f6c79c8ee33acd5cb860596f3cb7cbe5981cb986dc478707f5bd3",
                table.header.payload_sha256.hex(),
            )
            self.assertEqual(
                "8d661c6aaffb69c1d9c43333b384217ec4291bba2e70c9d9e636fe25db44a8df",
                table.header.header_sha256.hex(),
            )

            dtz_path = Path(directory) / "krk-dtz.omtb"
            write_table(dtz_path, "KRK", payload, bytes(len(payload) * 2))
            dtz_table = read_table(dtz_path, "KRK", require_dtz=True)
            self.assertTrue(dtz_table.header.has_dtz)
            self.assertEqual(len(payload) * 2, len(dtz_table.dtz))
            self.assertEqual(0, dtz_table.dtz_at(len(payload) - 1))

    def test_kck_policy_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kck.omtb"
            write_table(path, "KCK", kck_payload())
            table = read_table(path, "kck")
            self.assertEqual(table.header.legal_count, table.header.outcome_counts[DRAW])

    def test_corruption_and_semantic_mismatch_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "krk.omtb"
            write_table(path, "KRK", krk_payload())

            data = bytearray(path.read_bytes())
            data[-1] ^= 1
            corrupt_payload = Path(directory) / "payload-corrupt.omtb"
            corrupt_payload.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "payload checksum"):
                read_table(corrupt_payload, "KRK")

            data = bytearray(path.read_bytes())
            data[40] ^= 1
            corrupt_header = Path(directory) / "header-corrupt.omtb"
            corrupt_header.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "header checksum"):
                read_table(corrupt_header, "KRK")

            truncated = Path(directory) / "truncated.omtb"
            truncated.write_bytes(path.read_bytes()[:HEADER_SIZE + 10])
            with self.assertRaises(ValueError):
                read_table(truncated, "KRK")

            with self.assertRaisesRegex(ValueError, "material signature"):
                read_table(path, "KCK")
            with self.assertRaisesRegex(ValueError, "rules fingerprint"):
                read_table(path, "KRK", expected_rules_fingerprint=bytes(32))

    def test_writer_rejects_invalid_codes_and_policy_counts(self):
        payload = bytearray(krk_payload())
        payload[-1] = 0xFF
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "unsupported code"):
                write_table(Path(directory) / "bad.omtb", "KRK", payload)

            kck = bytearray(kck_payload())
            first_legal = material_spec("KCK").state_count - material_spec("KCK").legal_count
            kck[first_legal] = WIN
            with self.assertRaisesRegex(ValueError, "insufficient-material"):
                write_table(Path(directory) / "bad-kck.omtb", "KCK", kck)

    def test_krkc_shape_is_frozen_without_allocating_the_payload(self):
        spec = material_spec("KRKC")
        self.assertEqual(27_594_696, spec.state_count)
        self.assertEqual(22_607_206, spec.legal_count)
        self.assertEqual(4, spec.piece_count)

    def test_krkn_shape_is_frozen_without_allocating_the_payload(self):
        spec = material_spec("KRKN")
        self.assertEqual(27_594_696, spec.state_count)
        self.assertEqual(23_034_346, spec.legal_count)
        self.assertEqual(4, spec.piece_count)


if __name__ == "__main__":
    unittest.main()
