#!/usr/bin/env python3
"""Tests de la conservation des décisions lors d'une réindexation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import index
except ModuleNotFoundError as exc:  # environnement racine sans OpenCV
    index = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skipIf(index is None, f"environnement Visages requis : {IMPORT_ERROR}")
class PreviousDecisionTests(unittest.TestCase):
    def test_decisions_are_reapplied_by_box(self) -> None:
        faces = [
            {"bbox": (0.10, 0.10, 0.20, 0.20), "ignored": False, "invalid": False},
            {"bbox": (0.50, 0.50, 0.60, 0.60), "ignored": False, "invalid": False},
            {"bbox": (0.80, 0.80, 0.90, 0.90), "ignored": False, "invalid": False},
        ]
        previous = [
            {"x1": 0.105, "y1": 0.105, "x2": 0.205, "y2": 0.205,
             "ignored": 0, "invalid": 1},
            {"x1": 0.505, "y1": 0.505, "x2": 0.605, "y2": 0.605,
             "ignored": 1, "invalid": 0},
        ]
        index.apply_previous_decisions(faces, previous)
        self.assertEqual((faces[0]["ignored"], faces[0]["invalid"]), (False, True))
        self.assertEqual((faces[1]["ignored"], faces[1]["invalid"]), (True, False))
        self.assertEqual((faces[2]["ignored"], faces[2]["invalid"]), (False, False))

    def test_each_face_receives_at_most_one_decision(self) -> None:
        faces = [
            {"bbox": (0.10, 0.10, 0.20, 0.20), "ignored": False, "invalid": False},
        ]
        previous = [
            {"x1": 0.10, "y1": 0.10, "x2": 0.20, "y2": 0.20,
             "ignored": 1, "invalid": 0},
            {"x1": 0.11, "y1": 0.11, "x2": 0.21, "y2": 0.21,
             "ignored": 0, "invalid": 1},
        ]
        index.apply_previous_decisions(faces, previous)
        # La décision au meilleur recouvrement gagne, sans cumul.
        self.assertEqual((faces[0]["ignored"], faces[0]["invalid"]), (True, False))

    def test_no_previous_decision_changes_nothing(self) -> None:
        faces = [
            {"bbox": (0.10, 0.10, 0.20, 0.20), "ignored": False, "invalid": False},
        ]
        index.apply_previous_decisions(faces, [])
        self.assertEqual((faces[0]["ignored"], faces[0]["invalid"]), (False, False))


if __name__ == "__main__":
    unittest.main()
