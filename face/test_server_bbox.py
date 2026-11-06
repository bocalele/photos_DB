#!/usr/bin/env python3
"""Tests de normalisation des boîtes de visage hors image."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import server
except ModuleNotFoundError as exc:  # environnement racine sans OpenCV
    server = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skipIf(server is None, f"environnement Visages requis : {IMPORT_ERROR}")
class ClampBBoxTests(unittest.TestCase):
    def test_inside_box_is_unchanged(self) -> None:
        box = (0.10, 0.20, 0.80, 0.90)
        self.assertEqual(server.clamp_bbox(box), box)

    def test_partially_outside_box_is_brought_back_inside(self) -> None:
        x1, y1, x2, y2 = server.clamp_bbox((-0.02, 0.20, 1.03, 0.90))
        self.assertGreaterEqual(x1, server.BOX_INSET)
        self.assertGreaterEqual(y1, 0.0)
        self.assertLessEqual(x2, 1.0 - server.BOX_INSET)
        self.assertLessEqual(y2, 1.0)
        self.assertGreater(x2 - x1, server.BOX_MIN_SIZE)

    def test_fully_outside_box_keeps_a_small_visible_area(self) -> None:
        x1, y1, x2, y2 = server.clamp_bbox((1.10, 0.20, 1.20, 0.30))
        self.assertTrue(0.0 <= x1 < x2 <= 1.0)
        self.assertTrue(0.0 <= y1 < y2 <= 1.0)
        self.assertGreaterEqual(x2 - x1, server.BOX_MIN_SIZE)

    def test_non_finite_box_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            server.clamp_bbox((math.nan, 0.1, 0.2, 0.3))


if __name__ == "__main__":
    unittest.main()
