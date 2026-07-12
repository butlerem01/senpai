from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import index_prototype as krkc_index  # noqa: E402
import three_man_wdl as tb  # noqa: E402


class IndexingTests(unittest.TestCase):
    def test_exact_index_populations(self) -> None:
        self.assertEqual(tb.INDEX.raw_state_count, 2_185_248)
        self.assertEqual(tb.INDEX.state_count, 273_816)
        self.assertEqual(krkc_index.INDEX.raw_state_count, 220_710_048)
        self.assertEqual(krkc_index.INDEX.state_count, 27_594_696)

    def test_reported_krkc_position_keeps_v1_dense_index(self) -> None:
        state = krkc_index.State(101, 96, 45, 74, krkc_index.ROOK_TO_MOVE)
        self.assertTrue(krkc_index.is_legal(state))
        self.assertEqual(krkc_index.dense_rank(state), 26_750_996)
        for transform in range(8):
            self.assertEqual(
                krkc_index.dense_rank(krkc_index.transform_state(state, transform)),
                26_750_996,
            )

    def test_three_man_rank_round_trip_and_symmetry(self) -> None:
        state = tb.State(45, 96, 100, tb.WEAK_TO_MOVE)
        rank = tb.dense_rank(state)
        self.assertEqual(tb.dense_rank(tb.dense_unrank(rank)), rank)
        for transform in range(8):
            self.assertEqual(tb.dense_rank(tb.transform_state(state, transform)), rank)


class ExactWdlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # These are full-family solves, not reduced fixtures.  Verification
        # checks every Bellman equation after retrograde convergence.
        cls.krk = tb.solve("krk", verify=True)
        cls.kck = tb.solve("kck", verify=True)

    def test_exact_legal_and_wdl_counts(self) -> None:
        self.assertEqual(self.krk.legal_count, 235_033)
        self.assertEqual(
            self.krk.counts,
            {"invalid": 38_783, "loss": 339, "draw": 232_962, "win": 1_732},
        )
        self.assertEqual(self.kck.legal_count, 244_779)
        self.assertEqual(
            self.kck.counts,
            {"invalid": 29_037, "loss": 0, "draw": 244_779, "win": 0},
        )

    def test_concrete_krk_mate_is_loss_for_side_to_move(self) -> None:
        # Weak Ke0 is checked by Ra0.  Strong Ke2 covers d1/e1/f1, and
        # the rook covers d0/f0, leaving no legal successor.
        state = tb.State(42, 0, 40, tb.WEAK_TO_MOVE)
        self.assertTrue(tb.is_legal(state, "krk"))
        self.assertTrue(tb.in_check(state, "krk"))
        self.assertEqual(tb.successor_indices(state, "krk"), (frozenset(), False))
        self.assertEqual(self.krk.status[tb.dense_rank(state)], tb.LOSS)

    def test_detached_corner_refuge_is_exact_draw(self) -> None:
        state = tb.State(45, 96, 100, tb.WEAK_TO_MOVE)
        self.assertTrue(tb.is_legal(state, "krk"))
        self.assertEqual(self.krk.status[tb.dense_rank(state)], tb.DRAW)

        # The reported game's role-normalized KRK successor is also drawn.
        reported = tb.State(101, 96, 45, tb.STRONG_TO_MOVE)
        self.assertTrue(tb.is_legal(reported, "krk"))
        self.assertEqual(self.krk.status[tb.dense_rank(reported)], tb.DRAW)

    def test_reported_capture_successors_are_exact_krk_draws(self) -> None:
        # g099 after Wxi0 Cxi0 Rxi0: white Kw2/Ri0, black Kf6 to move.
        g099 = tb.State(101, 80, 56, tb.WEAK_TO_MOVE)
        # g116 direct game after Kxh1: white Kh1/Re9, black Kf5 to move.
        g116 = tb.State(71, 49, 55, tb.WEAK_TO_MOVE)
        # Rotated g116 after ...Rxc7: black Kw4/Rc7, white Ke3 to move.
        g116_rotated = tb.State(103, 27, 43, tb.WEAK_TO_MOVE)

        for state in (g099, g116, g116_rotated):
            self.assertTrue(tb.is_legal(state, "krk"))
            self.assertEqual(self.krk.status[tb.dense_rank(state)], tb.DRAW)

    def test_kck_matches_current_automatic_draw_policy(self) -> None:
        legal = tb.State(45, 74, 20, tb.STRONG_TO_MOVE)
        self.assertTrue(tb.is_legal(legal, "kck"))
        self.assertEqual(self.kck.status[tb.dense_rank(legal)], tb.DRAW)

    def test_file_round_trip_and_checksum_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "omega-kck-wdl-v1.omtb3"
            tb.write_table(path, self.kck)
            header, payload = tb.read_table(path)
            self.assertEqual(header["material"], "KCK")
            self.assertEqual(header["legal_count"], 244_779)
            self.assertEqual(payload, bytes(self.kck.status))

            damaged = bytearray(path.read_bytes())
            damaged[-1] ^= 1
            path.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, "checksum"):
                tb.read_table(path)

    def test_file_rejects_semantic_header_and_payload_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "omega-kck-wdl-v1.omtb3"
            tb.write_table(path, self.kck)
            header_line, payload = path.read_bytes().split(b"\n", 1)
            header = json.loads(header_line)

            bad_header = dict(header)
            bad_header["material"] = "KQK"
            path.write_bytes(
                json.dumps(bad_header, sort_keys=True, separators=(",", ":")).encode("ascii")
                + b"\n" + payload
            )
            with self.assertRaisesRegex(ValueError, "material"):
                tb.read_table(path)

            bad_header = dict(header)
            bad_header["rules"] = "different-rules"
            path.write_bytes(
                json.dumps(bad_header, sort_keys=True, separators=(",", ":")).encode("ascii")
                + b"\n" + payload
            )
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                tb.read_table(path)

            bad_payload = bytearray(payload)
            bad_payload[-1] = tb.UNKNOWN
            bad_header = dict(header)
            bad_header["payload_sha256"] = hashlib.sha256(bad_payload).hexdigest()
            path.write_bytes(
                json.dumps(bad_header, sort_keys=True, separators=(",", ":")).encode("ascii")
                + b"\n" + bad_payload
            )
            with self.assertRaisesRegex(ValueError, "WDL code"):
                tb.read_table(path)


if __name__ == "__main__":
    unittest.main()
