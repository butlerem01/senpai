import sys
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from omega_geometry import KNIGHT_MOVES, TRANSFORM, WIZARD_MOVES


class OmegaLeaperGeometryTests(unittest.TestCase):
    def test_wizard_detached_corner_moves_are_frozen(self):
        self.assertEqual({0, 2, 20}, set(WIZARD_MOVES[100]))
        self.assertEqual({70, 90, 92}, set(WIZARD_MOVES[101]))
        self.assertEqual({79, 97, 99}, set(WIZARD_MOVES[102]))
        self.assertEqual({7, 9, 29}, set(WIZARD_MOVES[103]))

    def test_wizard_and_knight_moves_commute_with_every_d4_transform(self):
        for moves in (WIZARD_MOVES, KNIGHT_MOVES):
            for transform in range(8):
                mapping = TRANSFORM[transform]
                for origin, targets in enumerate(moves):
                    transformed_targets = {mapping[target] for target in targets}
                    self.assertEqual(
                        transformed_targets,
                        set(moves[mapping[origin]]),
                        (transform, origin),
                    )


if __name__ == "__main__":
    unittest.main()
