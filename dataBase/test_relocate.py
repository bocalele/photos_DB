#!/usr/bin/env python3
"""Tests de la mise à jour des chemins dans les bases après un déplacement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dataBase import face_db, photos_db
from dataBase.relocate import RelocationError, update_paths


class RelocateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.photos_path = Path(self.tmp.name) / "photos.db"
        self.face_path = Path(self.tmp.name) / "face.db"

        conn = photos_db.connect(self.photos_path)
        try:
            conn.execute(
                "INSERT INTO photos(relpath, updated_at) VALUES (?, ?)",
                ("ancien/photo.jpg", ""),
            )
            conn.execute(
                "INSERT INTO photos(relpath, updated_at) VALUES (?, ?)",
                ("ancien/autre.jpg", ""),
            )
            conn.execute(
                "INSERT INTO photos(relpath, updated_at) VALUES (?, ?)",
                ("ancien/voisin.jpg", ""),
            )
            conn.commit()
        finally:
            conn.close()

        conn = face_db.connect(self.face_path)
        try:
            face_db.create_schema(conn)
            for relpath in ("ancien/photo.jpg", "ancien/autre.jpg", "ancien/voisin.jpg"):
                conn.execute(
                    "INSERT INTO Photo(relpath, width, height, size, mtime, n_faces, indexed_at) "
                    "VALUES (?, 100, 100, 10, 1.0, 1, 'now')",
                    (relpath,),
                )
                conn.execute(
                    "INSERT INTO Face(relpath, face_idx, x1, y1, x2, y2, det_score, embedding) "
                    "VALUES (?, 0, 0.1, 0.1, 0.2, 0.2, 0.9, ?)",
                    (relpath, b"embedding"),
                )
            conn.execute(
                "INSERT INTO RefPerson(id_people, name, n_faces, centroid, seuil, updated_at) "
                "VALUES (1, 'Alice', 1, ?, 0.4, 'now')",
                (b"centroid",),
            )
            conn.execute(
                "INSERT INTO RefFace(id_people, relpath, embedding) VALUES (1, ?, ?)",
                ("ancien/photo.jpg", b"embedding"),
            )
            conn.commit()
        finally:
            conn.close()

    def _photos_paths(self) -> set[str]:
        conn = photos_db.connect(self.photos_path, readonly=True)
        try:
            return {row["relpath"] for row in conn.execute("SELECT relpath FROM photos")}
        finally:
            conn.close()

    def _face_paths(self) -> tuple[set[str], set[str], set[str]]:
        conn = face_db.connect(self.face_path)
        try:
            photos = {row[0] for row in conn.execute("SELECT relpath FROM Photo")}
            faces = {row[0] for row in conn.execute("SELECT relpath FROM Face")}
            refs = {row[0] for row in conn.execute("SELECT relpath FROM RefFace")}
            return photos, faces, refs
        finally:
            conn.close()

    def test_single_file_move_updates_both_databases(self) -> None:
        counts = update_paths(
            self.photos_path,
            self.face_path,
            [("ancien/photo.jpg", "nouveau/photo.jpg", False)],
        )

        self.assertEqual(counts["photos"], 1)
        self.assertEqual(counts["face_photos"], 1)
        self.assertEqual(counts["faces"], 1)
        self.assertEqual(counts["reference_faces"], 1)
        self.assertIn("nouveau/photo.jpg", self._photos_paths())
        face_photos, faces, refs = self._face_paths()
        self.assertIn("nouveau/photo.jpg", face_photos)
        self.assertIn("nouveau/photo.jpg", faces)
        self.assertIn("nouveau/photo.jpg", refs)

    def test_folder_move_respects_path_boundaries(self) -> None:
        counts = update_paths(
            self.photos_path,
            self.face_path,
            [("ancien", "nouveau", True)],
        )

        self.assertEqual(counts["photos"], 3)
        paths = self._photos_paths()
        self.assertIn("nouveau/photo.jpg", paths)
        self.assertIn("nouveau/autre.jpg", paths)
        self.assertIn("nouveau/voisin.jpg", paths)
        self.assertNotIn("ancien/photo.jpg", paths)
        face_photos, faces, refs = self._face_paths()
        self.assertEqual(face_photos, {"nouveau/photo.jpg", "nouveau/autre.jpg", "nouveau/voisin.jpg"})
        self.assertEqual(faces, face_photos)
        self.assertEqual(refs, {"nouveau/photo.jpg"})

    def test_existing_destination_in_database_is_rejected(self) -> None:
        with self.assertRaises(RelocationError):
            update_paths(
                self.photos_path,
                self.face_path,
                [("ancien/photo.jpg", "ancien/autre.jpg", False)],
            )
        self.assertIn("ancien/photo.jpg", self._photos_paths())
        self.assertIn("ancien/autre.jpg", self._photos_paths())

    def test_missing_databases_are_ignored(self) -> None:
        counts = update_paths(
            Path(self.tmp.name) / "absente-photos.db",
            Path(self.tmp.name) / "absente-face.db",
            [("ancien/photo.jpg", "nouveau/photo.jpg", False)],
        )
        self.assertEqual(
            counts,
            {"photos": 0, "face_photos": 0, "faces": 0, "reference_faces": 0},
        )


if __name__ == "__main__":
    unittest.main()
