#!/usr/bin/env python3
"""Tests du serveur Classement sur un fonds photo temporaire."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dataBase import face_db, photos_db

from organize import server



class FakePreferences:
    def __init__(self, initial: Path):
        self.initial = str(initial)

    def value(self, key: str, default=None):
        return self.initial if key == "initial_directory" else default

    def set_initial_directory(self, directory: Path) -> None:
        self.initial = str(directory)


class OrganizerStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "PHOTOS"
        self.root.mkdir()
        self.photos_db = base / "_DATA" / "photos.db"
        self.face_db = base / "_DATA" / "face.db"
        self.previews = base / "_DATA" / "previews"
        self.photos_db.parent.mkdir(parents=True)
        self.previews.mkdir()

        self.source = self.root / "Source"
        self.sub = self.source / "Sub"
        self.destination = self.root / "Destination"
        self.sub.mkdir(parents=True)
        self.destination.mkdir()
        (self.source / "photo.jpg").write_bytes(b"photo")
        (self.sub / "inner.jpg").write_bytes(b"inner")
        (self.source / "note.txt").write_text("note", encoding="utf-8")
        (self.source / "photo.xmp").write_text("sidecar", encoding="utf-8")

        conn = photos_db.connect(self.photos_db)
        try:
            for relpath in ("Source/photo.jpg", "Source/Sub/inner.jpg"):
                conn.execute(
                    "INSERT INTO photos(relpath, updated_at) VALUES (?, '')",
                    (relpath,),
                )
            conn.commit()
        finally:
            conn.close()

        conn = face_db.connect(self.face_db)
        try:
            face_db.create_schema(conn)
            for relpath in ("Source/photo.jpg", "Source/Sub/inner.jpg"):
                conn.execute(
                    "INSERT INTO Photo(relpath, width, height, size, mtime, n_faces, indexed_at) "
                    "VALUES (?, 100, 100, 10, 1.0, 1, 'now')",
                    (relpath,),
                )
                conn.execute(
                    "INSERT INTO Face(relpath, face_idx, x1, y1, x2, y2, det_score, embedding) "
                    "VALUES (?, 0, .1, .1, .2, .2, .9, ?)",
                    (relpath, b"embedding"),
                )
            conn.execute(
                "INSERT INTO RefPerson(id_people, name, n_faces, centroid, seuil, updated_at) "
                "VALUES (1, 'Alice', 1, ?, .4, 'now')",
                (b"centroid",),
            )
            conn.execute(
                "INSERT INTO RefFace(id_people, relpath, embedding) VALUES (1, ?, ?)",
                ("Source/photo.jpg", b"embedding"),
            )
            conn.commit()
        finally:
            conn.close()

        self.environment = {
            "PHOTOS_ROOT": str(self.root),
            "PHOTOS_DB": str(self.photos_db),
            "PHOTOS_FACE_DB": str(self.face_db),
            "PHOTOS_PREVIEWS_DIR": str(self.previews),
        }

    def _state(self) -> tuple[server.OrganizerState, FakePreferences]:
        preferences = FakePreferences(self.sub)
        with patch.dict(os.environ, self.environment, clear=False):
            state = server.OrganizerState(self.sub)
        state.preferences = preferences
        return state, preferences

    def _photos(self) -> set[str]:
        conn = photos_db.connect(self.photos_db, readonly=True)
        try:
            return {row["relpath"] for row in conn.execute("SELECT relpath FROM photos")}
        finally:
            conn.close()

    def _face_paths(self) -> tuple[set[str], set[str], set[str]]:
        conn = face_db.connect(self.face_db)
        try:
            return (
                {row[0] for row in conn.execute("SELECT relpath FROM Photo")},
                {row[0] for row in conn.execute("SELECT relpath FROM Face")},
                {row[0] for row in conn.execute("SELECT relpath FROM RefFace")},
            )
        finally:
            conn.close()

    def test_folder_move_updates_databases_and_current_directory(self) -> None:
        state, preferences = self._state()
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ):
            result = state.move(["Source"], "Destination")

        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["errors"], 0)
        target = self.destination / "Source"
        self.assertTrue((target / "photo.jpg").is_file())
        self.assertTrue((target / "photo.xmp").is_file())
        self.assertTrue((target / "Sub" / "inner.jpg").is_file())
        self.assertEqual(
            self._photos(),
            {"Destination/Source/photo.jpg", "Destination/Source/Sub/inner.jpg"},
        )
        face_photos, faces, refs = self._face_paths()
        self.assertEqual(face_photos, self._photos())
        self.assertEqual(faces, self._photos())
        self.assertEqual(refs, {"Destination/Source/photo.jpg"})
        self.assertEqual(state.directory, (target / "Sub").resolve())
        self.assertEqual(Path(preferences.initial), (target / "Sub").resolve())

    def test_single_photo_move_moves_sidecar_and_paths(self) -> None:
        state, preferences = self._state()
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ):
            result = state.move(["Source/photo.jpg"], "Destination")

        self.assertEqual(result["moved"], 1)
        self.assertTrue((self.destination / "photo.jpg").is_file())
        self.assertTrue((self.destination / "photo.xmp").is_file())
        self.assertFalse((self.source / "photo.jpg").exists())
        self.assertIn("Destination/photo.jpg", self._photos())
        face_photos, faces, refs = self._face_paths()
        self.assertIn("Destination/photo.jpg", face_photos)
        self.assertIn("Destination/photo.jpg", faces)
        self.assertIn("Destination/photo.jpg", refs)

    def test_rename_and_create_folder(self) -> None:
        state, preferences = self._state()
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ):
            created = state.create_folder("Destination", "Nouveau")
            renamed = state.rename(created["path"], "Renommé")

        self.assertTrue((self.destination / "Renommé").is_dir())
        self.assertEqual(renamed["status"], "moved")
        self.assertFalse((self.destination / "Nouveau").exists())

    def test_refuses_to_move_folder_into_itself(self) -> None:
        state, preferences = self._state()
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ):
            result = state.move(["Source"], "Source/Sub")

        self.assertEqual(result["moved"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertTrue((self.source / "photo.jpg").is_file())

    def test_status_colors_jpeg_and_psd_in_any_directory(self) -> None:
        status = self.root / "Status"
        jpeg = status / "JPEG"
        psd = status / "PSD"
        raw = status / "RAW"
        other = status / "Divers"
        for directory in (jpeg, psd, raw, other):
            directory.mkdir(parents=True)
        (jpeg / "a.jpg").write_bytes(b"jpeg-a")
        (jpeg / "b.JPG").write_bytes(b"jpeg-b")
        (psd / "a.psd").write_bytes(b"psd-a")
        (raw / "a.nef").write_bytes(b"raw-a")
        (other / "a.jpg").write_bytes(b"other-a")

        conn = photos_db.connect(self.photos_db)
        try:
            for relpath in (
                "Status/JPEG/a.jpg",
                "Status/JPEG/b.JPG",
                "Status/PSD/a.psd",
                "Status/RAW/a.nef",
                "Status/Divers/a.jpg",
            ):
                stat = (self.root / relpath).stat()
                conn.execute(
                    "INSERT INTO photos(relpath, file_size, mtime_ns, updated_at) "
                    "VALUES (?, ?, ?, '')",
                    (relpath, stat.st_size, stat.st_mtime_ns),
                )
            conn.commit()
        finally:
            conn.close()

        state, preferences = self._state()
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ):
            children = {item["path"]: item for item in state.children("")}
            self.assertEqual(children["Status"]["status"]["status"], "ok")
            self.assertIsNone(state.subtree_status("Status/RAW"))
            self.assertEqual(state.subtree_status("Status/Divers")["status"], "ok")

            (jpeg / "extra.jpg").write_bytes(b"extra")
            state.invalidate_status()
            self.assertEqual(state.subtree_status("Status")["status"], "stale")

            (jpeg / "a.jpg").write_bytes(b"changed")
            state.invalidate_status()
            self.assertEqual(state.subtree_status("Status")["status"], "stale")

            (jpeg / "b.JPG").unlink()
            state.invalidate_status()
            self.assertEqual(state.subtree_status("Status")["status"], "missing")

            (self.root / "nouvelle.jpg").write_bytes(b"nouvelle")
            state.invalidate_status()
            direct = state.direct_status("")
            self.assertEqual(direct["status"], "unindexed")
            self.assertEqual(direct["total"], 0)
            self.assertEqual(direct["unindexed"], 1)

    def test_status_marks_never_indexed_folders_pink(self) -> None:
        new_folder = self.root / "Nouveau" / "SousDossier"
        new_folder.mkdir(parents=True)
        (new_folder / "a.jpg").write_bytes(b"jpeg")
        (new_folder / "b.psd").write_bytes(b"psd")

        state, preferences = self._state()
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ):
            folder = state.subtree_status("Nouveau/SousDossier")
            self.assertEqual(folder["status"], "unindexed")
            self.assertEqual(folder["total"], 0)
            self.assertEqual(folder["unindexed"], 2)

            parent = state.subtree_status("Nouveau")
            self.assertEqual(parent["status"], "unindexed")
            self.assertEqual(parent["unindexed"], 2)

            children = {item["path"]: item for item in state.children("")}
            self.assertEqual(children["Nouveau"]["status"]["status"], "unindexed")
            self.assertEqual(state.tree("Nouveau")["status"]["status"], "unindexed")

    def test_tree_status_keeps_deep_missing_database_paths(self) -> None:
        (self.root / "Repro").mkdir()
        conn = photos_db.connect(self.photos_db)
        try:
            conn.execute(
                "INSERT INTO photos(relpath, file_size, mtime_ns, updated_at) "
                "VALUES ('Repro/Disparu/JPEG/absente.jpg', 1, 1, '')"
            )
            conn.commit()
        finally:
            conn.close()

        state, preferences = self._state()
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ):
            deep = state.subtree_status("Repro")
            self.assertEqual(deep["status"], "missing")
            tree = state.tree("Repro")
            self.assertEqual(tree["status"]["status"], "missing")
            self.assertEqual(tree["status"]["missing"], 1)
            children = {item["path"]: item for item in state.children("")}
            self.assertEqual(children["Repro"]["status"]["status"], "missing")

    def test_import_files_refuses_collisions(self) -> None:
        state, preferences = self._state()
        (self.destination / "photo.jpg").write_bytes(b"existant")
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ):
            result = state.import_files(
                "Destination",
                [
                    ("Vacances/photo.jpg", b"nouvelle", 1_700_000_000_000),
                    ("photo.jpg", b"collision", None),
                    ("notes.txt", b"refus", None),
                ],
            )

        self.assertEqual(result["imported"], ["Destination/Vacances/photo.jpg"])
        self.assertEqual(len(result["skipped"]), 2)
        target = self.destination / "Vacances" / "photo.jpg"
        self.assertEqual(target.read_bytes(), b"nouvelle")
        self.assertEqual(target.stat().st_mtime, 1_700_000_000)

    def test_rating_updates_metadata_and_database(self) -> None:
        state, preferences = self._state()
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ), patch.object(server, "set_metadata") as set_metadata:
            result = state.set_rating("Source/photo.jpg", 4)

        self.assertEqual(result["rating"], 4)
        set_metadata.assert_called_once()
        conn = photos_db.connect(self.photos_db, readonly=True)
        try:
            row = conn.execute(
                "SELECT rating, file_size, mtime_ns FROM photos WHERE relpath = ?",
                ("Source/photo.jpg",),
            ).fetchone()
        finally:
            conn.close()
        stat = (self.source / "photo.jpg").stat()
        self.assertEqual(row["rating"], 4)
        self.assertEqual(row["file_size"], stat.st_size)
        self.assertEqual(row["mtime_ns"], stat.st_mtime_ns)

    def test_trash_removes_file_sidecar_and_database_rows(self) -> None:
        state, preferences = self._state()
        with patch.object(server, "preferences", preferences), patch.dict(
            os.environ, self.environment, clear=False
        ), patch.object(
            state,
            "_move_to_trash",
            side_effect=lambda paths: [path.unlink() or path.name for path in paths],
        ):
            result = state.trash("Source/photo.jpg")

        self.assertEqual(result["trashed"], ["photo.jpg", "photo.xmp"])
        self.assertFalse((self.source / "photo.jpg").exists())
        self.assertFalse((self.source / "photo.xmp").exists())
        self.assertNotIn("Source/photo.jpg", self._photos())
        face_photos, faces, refs = self._face_paths()
        self.assertNotIn("Source/photo.jpg", face_photos)
        self.assertNotIn("Source/photo.jpg", faces)
        self.assertNotIn("Source/photo.jpg", refs)


if __name__ == "__main__":
    unittest.main()
