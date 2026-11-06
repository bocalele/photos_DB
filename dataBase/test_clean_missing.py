#!/usr/bin/env python3
"""Tests du nettoyage des photos absentes de ``photos.db``."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from dataBase import clean_missing, photos_db


class CleanMissingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "PHOTOS"
        self.root.mkdir()
        (self.root / "Present").mkdir()
        (self.root / "Present" / "photo.jpg").write_bytes(b"present")
        self.db = base / "_DATA" / "photos.db"

        conn = photos_db.connect(self.db)
        try:
            photos_db.create_schema(conn)
            photos_db.set_meta(conn, "source_root", str(self.root))
            self.present_id = self._insert(
                conn, "Present/photo.jpg"
            )
            self.missing_jpeg_id = self._insert(
                conn, "Repro/JPEG/absente.jpg"
            )
            self.missing_psd_id = self._insert(
                conn, "Repro/Groupe1/PSD/absente.psd"
            )
            keyword_id = photos_db.get_or_create_keyword(conn, "test")
            conn.execute(
                "INSERT INTO photo_keywords(photo_id, keyword_id) VALUES (?, ?)",
                (self.missing_psd_id, keyword_id),
            )
            conn.commit()
            photos_db.set_meta(conn, "photo_count", "3")
        finally:
            conn.close()

    def _insert(self, conn, relpath: str) -> int:
        cursor = conn.execute(
            "INSERT INTO photos(relpath, updated_at) VALUES (?, '')",
            (relpath,),
        )
        return int(cursor.lastrowid)

    def _ids(self) -> set[int]:
        conn = photos_db.connect(self.db, readonly=True)
        try:
            return {int(row["id"]) for row in conn.execute("SELECT id FROM photos")}
        finally:
            conn.close()

    def _run(self, *args: str) -> dict:
        return clean_missing.run(
            clean_missing.parse_args(
                ["--db", str(self.db), "--root", str(self.root), *args]
            )
        )

    def test_missing_photos_lists_only_absent_rows(self) -> None:
        conn = photos_db.connect(self.db, readonly=True)
        try:
            missing = clean_missing.missing_photos(conn, self.root)
        finally:
            conn.close()

        self.assertEqual(
            {row["id"] for row in missing},
            {self.missing_jpeg_id, self.missing_psd_id},
        )
        self.assertNotIn(self.present_id, {row["id"] for row in missing})

    def test_dry_run_lists_and_does_not_delete(self) -> None:
        result = self._run()

        self.assertEqual(result["mode"], "dry-run")
        self.assertTrue(result["dryRun"])
        self.assertEqual(
            {row["id"] for row in result["missing"]},
            {self.missing_jpeg_id, self.missing_psd_id},
        )
        self.assertEqual(result["deletedCount"], 0)
        self.assertEqual(self._ids(), {
            self.present_id, self.missing_jpeg_id, self.missing_psd_id
        })

    def test_dry_run_output_file_can_feed_ids_file(self) -> None:
        output = Path(self.tmp.name) / "missing.txt"
        result = self._run("--output-file", str(output))

        self.assertEqual(result["outputFile"], str(output))
        lines = output.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "# id\tpath")
        self.assertIn(
            f"{self.missing_jpeg_id}\tRepro/JPEG/absente.jpg",
            lines,
        )
        self.assertEqual(
            clean_missing.parse_ids_file(output),
            [self.missing_jpeg_id, self.missing_psd_id],
        )

        deleted = self._run("--ids-file", str(output), "--yes")
        self.assertEqual(deleted["deletedCount"], 2)
        self.assertEqual(self._ids(), {self.present_id})

    def test_dry_run_stdout_can_be_redirected_to_ids_file(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = clean_missing.main(
                ["--db", str(self.db), "--root", str(self.root), "--dry-run"]
            )

        self.assertEqual(code, 0)
        self.assertTrue(stdout.getvalue().startswith("# id\tpath\n"))
        self.assertIn(
            f"{self.missing_psd_id}\tRepro/Groupe1/PSD/absente.psd\n",
            stdout.getvalue(),
        )
        self.assertNotIn("Absentes :", stdout.getvalue())
        self.assertIn("Absentes : 2 / 3 photo(s).", stderr.getvalue())

    def test_output_file_is_only_for_dry_run(self) -> None:
        with self.assertRaises(ValueError):
            self._run(
                "--all",
                "--yes",
                "--output-file",
                str(Path(self.tmp.name) / "missing.txt"),
            )

    def test_output_file_refuses_to_overwrite_database(self) -> None:
        with self.assertRaises(ValueError):
            self._run("--output-file", str(self.db))

    def test_delete_ids_keeps_present_and_unknown_ids(self) -> None:
        result = self._run(
            "--ids",
            str(self.missing_psd_id),
            str(self.present_id),
            "999999",
            "--yes",
        )

        self.assertEqual(result["mode"], "ids")
        self.assertEqual(result["deletedCount"], 1)
        self.assertEqual(
            [row["id"] for row in result["deleted"]],
            [self.missing_psd_id],
        )
        self.assertEqual(
            [row["id"] for row in result["skippedPresent"]],
            [self.present_id],
        )
        self.assertEqual(result["unknownIds"], [999999])
        self.assertEqual(result["remaining"], 2)
        self.assertEqual(
            self._ids(),
            {self.present_id, self.missing_jpeg_id},
        )

        conn = photos_db.connect(self.db, readonly=True)
        try:
            link = conn.execute(
                "SELECT COUNT(*) FROM photo_keywords WHERE photo_id = ?",
                (self.missing_psd_id,),
            ).fetchone()[0]
            count = photos_db.get_meta(conn, "photo_count")
        finally:
            conn.close()
        self.assertEqual(link, 0)
        self.assertEqual(count, "2")

    def test_delete_all_removes_every_missing_row(self) -> None:
        result = self._run("--all", "--yes")

        self.assertEqual(result["mode"], "all")
        self.assertEqual(result["deletedCount"], 2)
        self.assertEqual(self._ids(), {self.present_id})
        self.assertEqual(result["remaining"], 1)

    def test_ids_file_accepts_table_and_comments(self) -> None:
        ids_file = Path(self.tmp.name) / "ids.txt"
        ids_file.write_text(
            "ID  Chemin\n"
            f"{self.missing_jpeg_id}  Repro/JPEG/absente.jpg\n"
            "# commentaire\n"
            f"{self.missing_psd_id}\n",
            encoding="utf-8",
        )

        self.assertEqual(
            clean_missing.parse_ids_file(ids_file),
            [self.missing_jpeg_id, self.missing_psd_id],
        )

    def test_missing_root_is_refused(self) -> None:
        with self.assertRaises(FileNotFoundError):
            clean_missing.run(
                clean_missing.parse_args(
                    [
                        "--db",
                        str(self.db),
                        "--root",
                        str(Path(self.tmp.name) / "absent"),
                    ]
                )
            )

    def test_delete_all_refuses_a_fully_missing_database_without_force(self) -> None:
        base = Path(self.tmp.name) / "all-missing"
        root = base / "PHOTOS"
        root.mkdir(parents=True)
        db = base / "_DATA" / "photos.db"
        conn = photos_db.connect(db)
        try:
            photos_db.create_schema(conn)
            photos_db.set_meta(conn, "source_root", str(root))
            for relpath in ("a.jpg", "b.psd"):
                conn.execute(
                    "INSERT INTO photos(relpath, updated_at) VALUES (?, '')",
                    (relpath,),
                )
            conn.commit()
        finally:
            conn.close()

        args = [
            "--db", str(db),
            "--root", str(root),
            "--all",
            "--yes",
        ]
        with self.assertRaises(ValueError):
            clean_missing.run(clean_missing.parse_args(args))

        result = clean_missing.run(
            clean_missing.parse_args([*args, "--force"])
        )
        self.assertEqual(result["deletedCount"], 2)
