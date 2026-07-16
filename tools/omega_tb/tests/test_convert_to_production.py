import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from convert_to_production import (
    FOUR_MAN_ORDERS,
    KCKW_CAPTURE_POLICY_SHA256,
    KCKW_RULES,
    KRKN_RULES,
    KWKN_CAPTURE_POLICY_SHA256,
    KWKN_RULES,
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


def source_krkn_payload():
    spec = material_spec("KRKN")
    invalid = spec.state_count - spec.legal_count
    return bytes([SOURCE_CODES["invalid"]]) * invalid + bytes([SOURCE_CODES["draw"]]) * spec.legal_count


def write_krkn_source(path: Path, payload: bytes, krk_payload_sha256: str, **changes):
    spec = material_spec("KRKN")
    counts = {name: payload.count(code) for name, code in SOURCE_CODES.items()}
    header = {
        "magic": "OMTB4WDL",
        "version": 1,
        "material": "KRKN",
        "labelled_order": FOUR_MAN_ORDERS["KRKN"],
        "index": "D4-first-piece-v1",
        "square_count": 104,
        "dense_state_count": spec.state_count,
        "state_count": spec.state_count,
        "complete": True,
        "boundary": "full",
        "legal_count": spec.legal_count,
        "rules": KRKN_RULES,
        "rules_sha256": hashlib.sha256(KRKN_RULES.encode("ascii")).hexdigest(),
        "krk_payload_sha256": krk_payload_sha256,
        "codes": SOURCE_CODES,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "counts": counts,
    }
    for name, count in counts.items():
        header[f"{name}_count"] = count
    header.update(changes)
    path.write_bytes(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii")
        + b"\n" + payload
    )


def source_kwkn_payload():
    spec = material_spec("KWKN")
    invalid = spec.state_count - spec.legal_count
    return bytes([SOURCE_CODES["invalid"]]) * invalid + bytes([SOURCE_CODES["draw"]]) * spec.legal_count


def write_kwkn_source(path: Path, payload: bytes, **changes):
    spec = material_spec("KWKN")
    counts = {name: payload.count(code) for name, code in SOURCE_CODES.items()}
    header = {
        "magic": "OMTB4WDL",
        "version": 1,
        "material": "KWKN",
        "labelled_order": FOUR_MAN_ORDERS["KWKN"],
        "index": "D4-first-piece-v1",
        "square_count": 104,
        "dense_state_count": spec.state_count,
        "state_count": spec.state_count,
        "complete": True,
        "boundary": "full",
        "legal_count": spec.legal_count,
        "rules": KWKN_RULES,
        "rules_sha256": hashlib.sha256(KWKN_RULES.encode("ascii")).hexdigest(),
        "capture_policy_sha256": KWKN_CAPTURE_POLICY_SHA256,
        "codes": SOURCE_CODES,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "counts": counts,
    }
    for name, count in counts.items():
        header[f"{name}_count"] = count
    header.update(changes)
    path.write_bytes(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii")
        + b"\n" + payload
    )


def source_kckw_payload():
    spec = material_spec("KCKW")
    invalid = spec.state_count - spec.legal_count
    return bytes([SOURCE_CODES["invalid"]]) * invalid + bytes([SOURCE_CODES["draw"]]) * spec.legal_count


def write_kckw_source(path: Path, payload: bytes, **changes):
    spec = material_spec("KCKW")
    counts = {name: payload.count(code) for name, code in SOURCE_CODES.items()}
    header = {
        "magic": "OMTB4WDL",
        "version": 1,
        "material": "KCKW",
        "labelled_order": FOUR_MAN_ORDERS["KCKW"],
        "index": "D4-first-piece-v1",
        "square_count": 104,
        "dense_state_count": spec.state_count,
        "state_count": spec.state_count,
        "complete": True,
        "boundary": "full",
        "legal_count": spec.legal_count,
        "rules": KCKW_RULES,
        "rules_sha256": hashlib.sha256(KCKW_RULES.encode("ascii")).hexdigest(),
        "capture_policy_sha256": KCKW_CAPTURE_POLICY_SHA256,
        "codes": SOURCE_CODES,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "counts": counts,
    }
    for name, count in counts.items():
        header[f"{name}_count"] = count
    header.update(changes)
    path.write_bytes(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii")
        + b"\n" + payload
    )


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

    def test_full_krkn_conversion_dependency_and_code_remap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependency = root / "omega-krk-wdl-v1.omtb3"
            dependency_payload = source_krk_payload()
            write_source(dependency, dependency_payload)

            source = root / "omega-krkn-wdl-v1.omtb4"
            payload = source_krkn_payload()
            write_krkn_source(
                source, payload, hashlib.sha256(dependency_payload).hexdigest()
            )
            artifact = read_source_artifact(source)
            self.assertEqual("KRKN", artifact.material)

            output = root / "omega-krkn-wdl-v1.omtb"
            convert_artifact(source, output, dependency)
            table = read_table(output, "KRKN")
            self.assertEqual(
                (4_560_350, 0, 0, 23_034_346, 0, 0),
                table.header.outcome_counts,
            )

            wrong_dependency = root / "wrong-krk.omtb3"
            changed_dependency = bytearray(dependency_payload)
            changed_dependency[-1] = SOURCE_CODES["loss"]
            # Preserve legal source codes while changing the hash.
            changed_dependency[-2] = SOURCE_CODES["win"]
            write_source(wrong_dependency, bytes(changed_dependency))
            with self.assertRaisesRegex(ValueError, "different KRK dependency"):
                convert_artifact(source, output, wrong_dependency)

    def test_full_kwkn_conversion_has_no_krk_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "omega-kwkn-wdl-v1.omtb4"
            payload = source_kwkn_payload()
            write_kwkn_source(source, payload)
            artifact = read_source_artifact(source)
            self.assertEqual("KWKN", artifact.material)

            output = root / "omega-kwkn-wdl-v1.omtb"
            convert_artifact(source, output)
            table = read_table(output, "KWKN")
            self.assertEqual(
                (3_516_341, 0, 0, 24_078_355, 0, 0),
                table.header.outcome_counts,
            )

            write_kwkn_source(source, payload, capture_policy_sha256="0" * 64)
            with self.assertRaisesRegex(ValueError, "capture-policy"):
                read_source_artifact(source)

    def test_full_kckw_conversion_has_no_krk_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "omega-kckw-wdl-v1.omtb4"
            payload = source_kckw_payload()
            write_kckw_source(source, payload)
            artifact = read_source_artifact(source)
            self.assertEqual("KCKW", artifact.material)

            output = root / "omega-kckw-wdl-v1.omtb"
            convert_artifact(source, output)
            table = read_table(output, "KCKW")
            self.assertEqual(
                (3_943_481, 0, 0, 23_651_215, 0, 0),
                table.header.outcome_counts,
            )

            write_kckw_source(source, payload, capture_policy_sha256="0" * 64)
            with self.assertRaisesRegex(ValueError, "capture-policy"):
                read_source_artifact(source)


if __name__ == "__main__":
    unittest.main()
