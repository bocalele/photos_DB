#!/usr/bin/env python3
"""Tests ciblés des décisions de travail stockées dans `face.db`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from dataBase import face_db


class FaceDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "face.db"
        self.conn = face_db.connect(self.path)
        self.addCleanup(self._close)
        face_db.create_schema(self.conn)
        self.embedding = np.zeros(8, dtype=np.float32)

    def _close(self) -> None:
        self.conn.close()

    def _save(self, faces: list[dict], labels: list[dict] | None = None) -> None:
        face_db.save_photo(
            self.conn,
            "photo.jpg",
            width=100,
            height=100,
            size=1234,
            mtime=1.0,
            faces=faces,
            labels=labels,
        )

    def _face(self, bbox: tuple[float, float, float, float], **flags) -> dict:
        return {
            "bbox": bbox,
            "det_score": 0.9,
            "embedding": self.embedding,
            **flags,
        }

    def test_schema_migrates_old_face_table(self) -> None:
        old = face_db.connect(Path(self.tmp.name) / "old.db")
        self.addCleanup(old.close)
        old.executescript(
            """
            CREATE TABLE Meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE Photo(
                relpath TEXT PRIMARY KEY, width INTEGER NOT NULL,
                height INTEGER NOT NULL, size INTEGER NOT NULL,
                mtime REAL NOT NULL, n_faces INTEGER NOT NULL,
                indexed_at TEXT NOT NULL, error TEXT
            );
            CREATE TABLE Face(
                id INTEGER PRIMARY KEY, relpath TEXT NOT NULL,
                face_idx INTEGER NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,
                x2 REAL NOT NULL, y2 REAL NOT NULL, det_score REAL NOT NULL,
                embedding BLOB NOT NULL, UNIQUE(relpath, face_idx)
            );
            """
        )
        face_db.create_schema(old)
        columns = {row["name"] for row in old.execute("PRAGMA table_info(Face)")}
        self.assertIn("ignored", columns)
        self.assertIn("invalid", columns)
        version = old.execute(
            "SELECT value FROM Meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        self.assertEqual(version, face_db.SCHEMA_VERSION)

    def test_invalid_is_excluded_from_counts_and_pending(self) -> None:
        self._save(
            [
                self._face((0.1, 0.1, 0.2, 0.2)),
                self._face((0.3, 0.3, 0.4, 0.4), ignored=True),
                self._face((0.5, 0.5, 0.6, 0.6), invalid=True),
            ]
        )
        status = face_db.status_map(self.conn)["photo.jpg"]
        self.assertEqual(status["n_faces"], 3)
        self.assertEqual(status["n_ignored"], 1)
        self.assertEqual(status["n_invalid"], 1)
        self.assertEqual(len(face_db.unlabelled_faces(self.conn)), 1)
        summary = face_db.summary(self.conn)
        self.assertEqual(summary["visages"], 2)
        self.assertEqual(summary["visages_invalides"], 1)

    def test_invalid_and_ignored_are_exclusive(self) -> None:
        self._save([self._face((0.1, 0.1, 0.2, 0.2))])
        face_db.set_face_invalid(self.conn, "photo.jpg", 0, True)
        row = face_db.photo_faces(self.conn, "photo.jpg")[0]
        self.assertEqual((row["ignored"], row["invalid"]), (0, 1))

        face_db.set_face_ignored(self.conn, "photo.jpg", 0, True)
        row = face_db.photo_faces(self.conn, "photo.jpg")[0]
        self.assertEqual((row["ignored"], row["invalid"]), (1, 0))

        face_db.set_face_invalid(self.conn, "photo.jpg", 0, True)
        row = face_db.photo_faces(self.conn, "photo.jpg")[0]
        self.assertEqual((row["ignored"], row["invalid"]), (0, 1))

    def test_invalid_wins_if_both_flags_are_submitted(self) -> None:
        self._save([self._face((0.1, 0.1, 0.2, 0.2), ignored=True, invalid=True)])
        row = face_db.photo_faces(self.conn, "photo.jpg")[0]
        self.assertEqual((row["ignored"], row["invalid"]), (0, 1))

    def test_invalid_face_never_gets_a_label(self) -> None:
        self.conn.execute(
            """
            INSERT INTO RefPerson(id_people, name, n_faces, centroid, seuil, updated_at)
            VALUES (1, 'Alice', 1, ?, 0.43, 'now')
            """,
            (face_db.pack(self.embedding),),
        )
        self._save(
            [self._face((0.1, 0.1, 0.2, 0.2), invalid=True)],
            labels=[
                {
                    "face_idx": 0,
                    "id_people": 1,
                    "name": "Alice",
                    "score": 0.8,
                    "source": "xmp",
                }
            ],
        )
        self.assertEqual(face_db.labelled_faces(self.conn), [])
        self.assertEqual(len(face_db.unlabelled_faces(self.conn)), 0)


if __name__ == "__main__":
    unittest.main()
