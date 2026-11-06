#!/usr/bin/env python3
"""Classement : déplacer photos et dossiers, avec mise à jour des bases.

L'application est volontairement autonome, comme les autres apps d'annotation.
Elle affiche l'arborescence du fonds photo, permet de glisser-déposer des
photos ou des dossiers, et met à jour les chemins dans ``photos.db`` et
``face.db`` après chaque déplacement réussi.

    python organize/server.py /Volumes/WD/PHOTOS/2026/Angleterre/PSD --port 8770

Les métadonnées des fichiers ne sont jamais modifiées : seul le chemin relatif
stocké dans les index dérivés suit le déplacement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, urlparse

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
for candidate in (PROJECT_DIR, APP_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from dataBase import face_db, photos_db  # noqa: E402
from dataBase.relocate import RelocationError, clean_relpath, update_paths  # noqa: E402
from tools import environment, preferences  # noqa: E402
from tools.setMetaData import set_metadata  # noqa: E402


PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".psd", ".webp",
    ".nef", ".nrw", ".cr2", ".cr3", ".arw", ".dng", ".raf", ".rw2",
    ".orf", ".pef", ".sr2", ".srf", ".heic", ".heif", ".avif",
}
SIDECAR_EXTENSIONS = {".xmp"}
IMPORT_EXTENSIONS = PHOTO_EXTENSIONS | SIDECAR_EXTENSIONS
IGNORED_DIRECTORIES = {"_DATA", "__pycache__"}
# Seuls les PSD et JPEG/JPG sont suivis par les couleurs, quel que soit le nom
# du dossier qui les contient. Les fichiers RAW et les autres formats restent
# neutres.
STATUS_EXTENSIONS = {".psd", ".jpg", ".jpeg"}
SIPS = shutil.which("sips")
QLMANAGE = shutil.which("qlmanage")


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _placeholder_svg() -> bytes:
    return """<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360" viewBox="0 0 480 360">
<rect width="480" height="360" fill="#151823"/>
<text x="240" y="178" fill="#8f96b3" font-family="sans-serif" font-size="17" text-anchor="middle">Aperçu indisponible</text>
<text x="240" y="204" fill="#646b88" font-family="sans-serif" font-size="12" text-anchor="middle">Format non convertible</text>
</svg>""".encode("utf-8")


def _ignored_name(name: str) -> bool:
    return name.startswith(".") or name in IGNORED_DIRECTORIES


def _normalize_rel(value: str | None) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if text in ("", "."):
        return ""
    return clean_relpath(text)


def _visible_rel(rel: str) -> None:
    if any(_ignored_name(part) for part in PurePosixPath(rel).parts):
        raise ValueError("Ce dossier est réservé à l'application.")


def _upload_rel(value: str) -> str:
    """Valide un chemin relatif reçu du navigateur (drag and drop Finder)."""
    text = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ValueError("Nom de fichier vide.")
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts:
        raise ValueError("Chemin de fichier invalide.")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Chemin de fichier invalide.")
    if any(_ignored_name(part) for part in path.parts):
        raise ValueError("Ce fichier ou dossier est réservé à l'application.")
    if path.suffix.lower() not in IMPORT_EXTENSIONS:
        raise ValueError(f"Format non importable : {path.name}")
    return path.as_posix()


def _status_eligible(relpath: str) -> bool:
    """Vrai pour un PSD ou un JPEG, dans n'importe quel dossier."""
    return PurePosixPath(relpath).suffix.lower() in STATUS_EXTENSIONS


def parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], list[tuple[str, str, bytes]]]:
    """Analyse un formulaire multipart sans dépendre du module ``cgi``."""
    if "multipart/form-data" not in content_type.lower():
        raise ValueError("Le formulaire multipart est invalide.")
    envelope = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + body
    )
    message = BytesParser(policy=email_policy).parsebytes(envelope)
    if not message.is_multipart():
        raise ValueError("Le formulaire multipart est vide.")
    fields: dict[str, str] = {}
    files: list[tuple[str, str, bytes]] = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename is not None:
            files.append((str(name or "files"), filename, payload))
        elif name:
            charset = part.get_content_charset() or "utf-8"
            fields[str(name)] = payload.decode(charset, errors="replace")
    return fields, files


def _make_image(source: Path, destination: Path, max_size: int = 420) -> bool:
    """Convertit une photo en aperçu JPEG, avec les outils macOS disponibles."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size:
        return True

    fd, temp_name = tempfile.mkstemp(
        prefix=".organize-", suffix=".jpg", dir=destination.parent
    )
    os.close(fd)
    temp = Path(temp_name)
    try:
        if SIPS:
            result = subprocess.run(
                [SIPS, "-Z", str(max_size), "-s", "format", "jpeg",
                 str(source), "--out", str(temp)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0 and temp.is_file() and temp.stat().st_size:
                os.replace(temp, destination)
                return True

        if QLMANAGE:
            temp.unlink(missing_ok=True)
            temp_dir = temp.with_suffix("")
            temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                result = subprocess.run(
                    [QLMANAGE, "-t", "-s", str(max_size), "-o", str(temp_dir), str(source)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                candidates = sorted(temp_dir.glob("*"))
                if result.returncode == 0 and candidates:
                    candidate = candidates[0]
                    if candidate.suffix.lower() in {".jpg", ".jpeg"}:
                        os.replace(candidate, destination)
                        return True
                    if SIPS:
                        result = subprocess.run(
                            [SIPS, "-s", "format", "jpeg", str(candidate), "--out", str(temp)],
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )
                        if result.returncode == 0 and temp.is_file() and temp.stat().st_size:
                            os.replace(temp, destination)
                            return True
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        return False
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        temp.unlink(missing_ok=True)


class OrganizerState:
    def __init__(self, directory: Path):
        self.root = environment.photos_root().expanduser().resolve()
        if not self.root.is_dir():
            self.root = directory.expanduser().resolve()
        self.lock = threading.RLock()
        self.directory = self._resolve_directory(directory)
        previews = environment.previews_dir()
        self.thumbnail_dir = previews / "organizer"
        self.large_dir = previews / "organizer-large"
        self._status_cache: dict[tuple[str, bool], tuple[float, dict | None]] = {}
        self._status_ttl = 15.0
        self._snapshot_cache: dict[
            str,
            tuple[float, dict[str, dict], dict[str, tuple[int, int] | None]],
        ] = {}
        self._snapshot_ttl = 30.0

    # --- chemins ---------------------------------------------------------

    def _resolve_directory(self, directory: Path) -> Path:
        candidate = Path(directory).expanduser().resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            candidate = self.root
        if not candidate.is_dir():
            candidate = self.root
        return candidate

    def relpath(self, path: Path) -> str:
        resolved = Path(path).expanduser().resolve()
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Chemin hors du fonds photo : {path}") from exc
        return "" if relative == Path(".") else relative.as_posix()

    def path_for(self, rel: str | None, *, directory: bool = False, must_exist: bool = True) -> Path:
        normalized = _normalize_rel(rel)
        _visible_rel(normalized)
        candidate = (self.root / normalized).resolve() if normalized else self.root
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Chemin hors du fonds photo : {rel}") from exc
        if must_exist:
            if directory and not candidate.is_dir():
                raise ValueError(f"Dossier introuvable : {rel or self.root.name}")
            if not directory and not candidate.exists():
                raise ValueError(f"Élément introuvable : {rel}")
        return candidate

    # --- état ------------------------------------------------------------

    def state(self) -> dict:
        with self.lock:
            directory = self.directory
            return {
                "root": str(self.root),
                "rootName": self.root.name,
                "directory": str(directory),
                "path": self.relpath(directory),
                "photoCount": self.photo_count(directory),
                "photosDb": str(environment.photos_db()),
                "faceDb": str(environment.face_db()),
                "photosDbExists": environment.photos_db().is_file(),
                "faceDbExists": environment.face_db().is_file(),
            }

    def photo_count(self, directory: Path) -> int:
        try:
            return sum(
                1
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
                and not _ignored_name(path.name)
            )
        except OSError:
            return 0

    # --- état de synchronisation ----------------------------------------

    def invalidate_status(self) -> None:
        with self.lock:
            self._status_cache.clear()
            self._snapshot_cache.clear()

    def _db_photo_rows(self, rel: str) -> dict[str, dict]:
        """Lignes de ``photos.db`` sous ``rel`` (sans créer ni migrer la base)."""
        database = environment.photos_db()
        if not database.is_file():
            return {}
        query = "SELECT relpath, file_size, mtime_ns, rating FROM photos"
        params: tuple[str, ...] = ()
        if rel:
            query += " WHERE relpath LIKE ?"
            params = (f"{rel}/%",)
        try:
            conn = sqlite3.connect(
                f"file:{quote(str(database))}?mode=ro",
                uri=True,
                timeout=10,
            )
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(query, params).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return {}
        return {
            str(row["relpath"]): {
                "size": row["file_size"],
                "mtime": row["mtime_ns"],
                "rating": row["rating"],
            }
            for row in rows
        }

    def _status_photo_rows(self, rel: str) -> dict[str, dict]:
        return {
            path: row
            for path, row in self._db_photo_rows(rel).items()
            if _status_eligible(path)
        }

    def _scan_photo_map(
        self,
        rel: str,
        recursive: bool,
        known_paths: set[str] | None = None,
    ) -> dict[str, tuple[int, int] | None]:
        """PSD/JPEG présents sous ``rel``, avec taille/mtime si indexés.

        Les fichiers inconnus de la base n'ont pas besoin d'un ``stat`` : leur
        simple présence suffit à les compter comme non indexés.
        """
        directory = self.root / rel if rel else self.root
        if not directory.is_dir():
            return {}
        result: dict[str, tuple[int, int] | None] = {}

        def add(path: Path) -> None:
            if (
                not path.is_file()
                or path.is_symlink()
                or _ignored_name(path.name)
            ):
                return
            try:
                key = path.relative_to(self.root).as_posix()
                if not _status_eligible(key):
                    return
                if known_paths is None or key in known_paths:
                    stat = path.stat()
                    result[key] = (stat.st_size, stat.st_mtime_ns)
                else:
                    result[key] = None
            except (OSError, ValueError):
                return

        if not recursive:
            try:
                for path in directory.iterdir():
                    add(path)
            except OSError:
                return {}
            return result

        for current, dirnames, filenames in os.walk(directory, followlinks=False):
            current_path = Path(current)
            dirnames[:] = [
                name for name in dirnames
                if not _ignored_name(name)
                and not current_path.joinpath(name).is_symlink()
            ]
            for filename in filenames:
                add(current_path / filename)
        return result

    @staticmethod
    def _status_from_maps(
        rows: dict[str, dict],
        disk: dict[str, tuple[int, int] | None],
    ) -> dict | None:
        if not rows and not disk:
            return None

        missing = sum(1 for path in rows if path not in disk)
        stale = sum(
            1 for path, row in rows.items()
            if disk.get(path) is not None
            and (
                row["size"] is None
                or row["mtime"] is None
                or int(row["size"]) != disk[path][0]
                or int(row["mtime"]) != disk[path][1]
            )
        )
        # Les fichiers suivis sont limités aux PSD/JPEG par ``_status_eligible``.
        # Un dossier qui contient ces fichiers sans aucune ligne de base est
        # signalé en rose. Cette caractéristique remonte aux parents.
        directories_with_rows = {
            PurePosixPath(path).parent.as_posix()
            for path in rows
        }
        never_indexed = any(
            PurePosixPath(path).parent.as_posix() not in directories_with_rows
            for path in disk
        )
        unindexed = sum(1 for path in disk if path not in rows)
        status = (
            "missing" if missing
            else "unindexed" if never_indexed
            else "stale" if stale or unindexed
            else "ok"
        )
        return {
            "status": status,
            "missing": missing,
            "stale": stale,
            "unindexed": unindexed,
            "total": len(rows),
        }

    def _snapshot_for(
        self,
        rel: str,
    ) -> tuple[dict[str, dict], dict[str, tuple[int, int] | None]]:
        """Lignes et fichiers d'un sous-arbre, réutilisés depuis le dernier scan."""
        now = time.monotonic()
        cached = self._snapshot_cache.get(rel)
        if cached and now - cached[0] < self._snapshot_ttl:
            return cached[1], cached[2]

        for base in sorted(self._snapshot_cache, key=len, reverse=True):
            timestamp, rows, disk = self._snapshot_cache[base]
            if now - timestamp >= self._snapshot_ttl:
                continue
            if base != rel and base and not rel.startswith(f"{base}/"):
                continue
            if not rel:
                return rows, disk
            prefix = f"{rel}/"
            return (
                {path: row for path, row in rows.items() if path.startswith(prefix)},
                {path: value for path, value in disk.items() if path.startswith(prefix)},
            )

        rows = self._status_photo_rows(rel)
        disk = self._scan_photo_map(rel, True, set(rows))
        self._snapshot_cache[rel] = (now, rows, disk)
        if len(self._snapshot_cache) > 8:
            oldest = min(self._snapshot_cache, key=lambda key: self._snapshot_cache[key][0])
            self._snapshot_cache.pop(oldest, None)
        return rows, disk

    def _folder_status(self, rel: str, recursive: bool) -> dict | None:
        key = (rel, recursive)
        now = time.monotonic()
        cached = self._status_cache.get(key)
        if cached and now - cached[0] < self._status_ttl:
            return cached[1]

        if recursive:
            scoped, disk = self._snapshot_for(rel)
        else:
            rows = self._status_photo_rows(rel)
            prefix = f"{rel}/" if rel else ""
            scoped = {}
            for path, row in rows.items():
                if rel:
                    if not path.startswith(prefix):
                        continue
                    remainder = path[len(prefix):]
                else:
                    remainder = path
                if remainder and "/" not in remainder:
                    scoped[path] = row
            disk = self._scan_photo_map(rel, False, set(rows))

        result = self._status_from_maps(scoped, disk)
        self._status_cache[key] = (now, result)
        return result

    def subtree_status(self, rel: str) -> dict | None:
        return self._folder_status(rel, True)

    def direct_status(self, rel: str) -> dict | None:
        return self._folder_status(rel, False)

    def _child_directories(self, rel: str) -> list[dict]:
        directory = self.path_for(rel, directory=True)
        result = []
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            raise ValueError(f"Dossier illisible : {exc}") from exc
        for child in entries:
            if _ignored_name(child.name) or not child.is_dir() or child.is_symlink():
                continue
            result.append({
                "name": child.name,
                "path": self.relpath(child),
                "hasChildren": self._has_visible_child(child),
            })
        result.sort(key=lambda item: item["name"].casefold())
        return result

    def tree(self, rel: str | None) -> dict:
        normalized = _normalize_rel(rel)
        children_info = self._child_directories(normalized)
        rows, disk = self._snapshot_for(normalized)
        # Un seul parcours du disque et une seule requête servent à la fois au
        # dossier courant et à tous ses enfants directs.
        status = self._status_from_maps(rows, disk)
        children = []
        for info in children_info:
            prefix = f"{info['path']}/"
            child_rows = {
                path: row for path, row in rows.items()
                if path.startswith(prefix)
            }
            child_disk = {
                path: value for path, value in disk.items()
                if path.startswith(prefix)
            }
            children.append({
                **info,
                "status": self._status_from_maps(child_rows, child_disk),
            })
        return {
            "path": normalized,
            "children": children,
            "status": status,
        }

    def set_directory(self, directory: Path) -> None:
        candidate = self._resolve_directory(directory)
        with self.lock:
            self.directory = candidate

    # --- arborescence ----------------------------------------------------

    def children(self, rel: str | None) -> list[dict]:
        return self.tree(rel)["children"]

    def _has_visible_child(self, directory: Path) -> bool:
        try:
            for child in directory.iterdir():
                if not _ignored_name(child.name) and child.is_dir():
                    return True
        except OSError:
            return False
        return False

    def directories(self) -> dict:
        items = [{"name": self.root.name, "path": "", "depth": 0}]
        truncated = False
        for current, dirnames, _ in os.walk(self.root, followlinks=False):
            current_path = Path(current)
            dirnames[:] = sorted(
                (
                    name for name in dirnames
                    if not _ignored_name(name)
                    and not current_path.joinpath(name).is_symlink()
                ),
                key=str.casefold,
            )
            if current_path == self.root:
                continue
            rel = self.relpath(current_path)
            if not rel:
                continue
            items.append({
                "name": current_path.name,
                "path": rel,
                "depth": len(PurePosixPath(rel).parts) - 1,
            })
            if len(items) >= 20000:
                truncated = True
                break
        return {"directories": items, "truncated": truncated}

    # --- photos ----------------------------------------------------------

    def photos(self, rel: str | None, offset: int, limit: int) -> dict:
        directory = self.path_for(rel, directory=True)
        files = []
        try:
            for path in directory.iterdir():
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and not _ignored_name(path.name)
                    and path.suffix.lower() in PHOTO_EXTENSIONS
                ):
                    files.append(path)
        except OSError as exc:
            raise ValueError(f"Dossier illisible : {exc}") from exc
        files.sort(key=lambda path: path.name.casefold())
        total = len(files)
        page = files[offset:offset + limit]
        db_rows = self._db_photo_rows(self.relpath(directory))
        items = []
        for path in page:
            try:
                stat = path.stat()
            except OSError:
                continue
            key = self.relpath(path)
            row = db_rows.get(key)
            if row is None:
                photo_status = "unindexed"
            elif (
                row["size"] is None
                or row["mtime"] is None
                or int(row["size"]) != stat.st_size
                or int(row["mtime"]) != stat.st_mtime_ns
            ):
                photo_status = "stale"
            else:
                photo_status = "ok"
            items.append({
                "name": key,
                "displayName": path.name,
                "size": stat.st_size,
                "modified": _iso(stat.st_mtime),
                "rating": int(row["rating"]) if row and row["rating"] is not None else 0,
                "status": photo_status,
                "thumbnail": f"/api/thumbnail?path={quote(key)}&v={stat.st_mtime_ns}",
                "preview": f"/api/image?path={quote(key)}&v={stat.st_mtime_ns}",
            })
        return {
            "directory": str(directory),
            "path": self.relpath(directory),
            "total": total,
            "photos": items,
        }

    def thumbnail(self, rel: str | None) -> tuple[Path | None, str]:
        path = self.path_for(rel, must_exist=True)
        if not path.is_file() or path.suffix.lower() not in PHOTO_EXTENSIONS:
            raise ValueError("Photo introuvable.")
        stat = path.stat()
        key = hashlib.sha1(
            f"{self.relpath(path)}:{stat.st_mtime_ns}:{stat.st_size}".encode("utf-8")
        ).hexdigest()[:24]
        destination = self.thumbnail_dir / f"{key}.jpg"
        if _make_image(path, destination):
            return destination, "image/jpeg"
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return None, "image/svg+xml"

    def image(self, rel: str | None, max_size: int = 1800) -> tuple[Path | None, str]:
        """Aperçu plus grand pour le diaporama."""
        path = self.path_for(rel, must_exist=True)
        if not path.is_file() or path.suffix.lower() not in PHOTO_EXTENSIONS:
            raise ValueError("Photo introuvable.")
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}:
            return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        stat = path.stat()
        key = hashlib.sha1(
            f"{self.relpath(path)}:{stat.st_mtime_ns}:{stat.st_size}:{max_size}".encode("utf-8")
        ).hexdigest()[:24]
        destination = self.large_dir / f"{key}.jpg"
        if _make_image(path, destination, max_size=max_size):
            return destination, "image/jpeg"
        return None, "image/svg+xml"

    # --- notation et corbeille ------------------------------------------

    def _refresh_photo_stat(self, path: Path) -> None:
        try:
            stat = path.stat()
            with closing(photos_db.write_connect()) as conn:
                photo_id = photos_db.ensure_photo(conn, self.relpath(path), self.root)
                conn.execute(
                    "UPDATE photos SET file_size = ?, mtime_ns = ?, updated_at = ? WHERE id = ?",
                    (stat.st_size, stat.st_mtime_ns, photos_db.now_iso(), photo_id),
                )
                conn.commit()
        except (OSError, FileNotFoundError, sqlite3.Error):
            pass

    def set_rating(self, rel: str, rating: int) -> dict:
        value = int(rating)
        if not 0 <= value <= 5:
            raise ValueError("La note doit être comprise entre 0 et 5.")
        with self.lock:
            path = self.path_for(rel, must_exist=True)
            if not path.is_file() or path.suffix.lower() not in PHOTO_EXTENSIONS:
                raise ValueError("Photo introuvable.")
            set_metadata(
                path,
                {
                    "XMP": {"Rating": value},
                    "EXIF": {"Rating": value},
                },
            )
            photos_db.record_keywords_rating_author_for_path(
                self.root, path, None, value, None
            )
            self._refresh_photo_stat(path)
            self.invalidate_status()
            return {"path": self.relpath(path), "rating": value}

    def _trash_fallback(self, paths: list[Path]) -> list[str]:
        trash = Path.home() / ".Trash"
        trash.mkdir(parents=True, exist_ok=True)
        moved = []
        for path in paths:
            if not path.exists():
                moved.append(path.name)
                continue
            target = trash / path.name
            if target.exists():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                target = trash / f"{path.stem} {stamp}{path.suffix}"
                index = 2
                while target.exists():
                    target = trash / f"{path.stem} {stamp}-{index}{path.suffix}"
                    index += 1
            shutil.move(str(path), str(target))
            moved.append(path.name)
        return moved

    def _move_to_trash(self, paths: list[Path]) -> list[str]:
        script = """
on run argv
    repeat with itemPath in argv
        tell application "Finder" to delete POSIX file itemPath
    end repeat
end run
"""
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script, *[str(path) for path in paths]],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode == 0 and not any(path.exists() for path in paths):
                return [path.name for path in paths]
        except (OSError, subprocess.SubprocessError):
            pass
        return self._trash_fallback(paths)

    def _delete_database_paths(self, rel: str) -> dict[str, int]:
        counts = {"photos": 0, "face_photos": 0, "faces": 0, "reference_faces": 0}
        photos_database = environment.photos_db()
        if photos_database.is_file():
            with closing(photos_db.write_connect()) as conn:
                cursor = conn.execute("DELETE FROM photos WHERE relpath = ?", (rel,))
                counts["photos"] = max(0, cursor.rowcount)
                conn.commit()
        face_database = environment.face_db()
        if face_database.is_file():
            conn = face_db.connect(face_database)
            try:
                face_db.create_schema(conn)
                with conn:
                    counts["faces"] = conn.execute(
                        "DELETE FROM Face WHERE relpath = ?", (rel,)
                    ).rowcount
                    counts["reference_faces"] = conn.execute(
                        "DELETE FROM RefFace WHERE relpath = ?", (rel,)
                    ).rowcount
                    counts["face_photos"] = conn.execute(
                        "DELETE FROM Photo WHERE relpath = ?", (rel,)
                    ).rowcount
            finally:
                conn.close()
        return counts

    def trash(self, rel: str) -> dict:
        with self.lock:
            path = self.path_for(rel, must_exist=True)
            if not path.is_file() or path.suffix.lower() not in PHOTO_EXTENSIONS:
                raise ValueError("Photo introuvable.")
            normalized = self.relpath(path)
            sidecars = self._sidecars(path)
            trashed = self._move_to_trash([path, *sidecars])
            database_error = None
            try:
                databases = self._delete_database_paths(normalized)
            except (OSError, sqlite3.Error) as error:
                databases = {}
                database_error = str(error)
            self.invalidate_status()
            return {
                "path": normalized,
                "trashed": trashed,
                "databases": databases,
                "databaseError": database_error,
                "state": self.state(),
            }

    # --- import Finder ---------------------------------------------------

    def import_files(
        self,
        destination_rel: str | None,
        items: list[tuple[str, bytes, int | None]],
    ) -> dict:
        destination = self.path_for(destination_rel, directory=True)
        imported: list[str] = []
        skipped: list[dict] = []
        errors: list[dict] = []
        for raw_path, payload, mtime_ms in items:
            try:
                clean = _upload_rel(raw_path)
            except ValueError as error:
                skipped.append({"path": raw_path, "error": str(error)})
                continue
            target = (destination / clean).resolve()
            try:
                target.relative_to(self.root)
            except ValueError:
                errors.append({"path": raw_path, "error": "Chemin hors du fonds photo."})
                continue
            if target.exists():
                skipped.append({
                    "path": raw_path,
                    "error": f"Un fichier existe déjà : {target.name}",
                })
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(payload)
                if mtime_ms:
                    seconds = float(mtime_ms) / 1000.0
                    os.utime(target, (seconds, seconds))
            except OSError as error:
                errors.append({"path": raw_path, "error": str(error)})
                continue
            imported.append(self.relpath(target))
        self.invalidate_status()
        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "state": self.state(),
        }

    # --- opérations ------------------------------------------------------

    def create_folder(self, parent_rel: str | None, name: str) -> dict:
        parent = self.path_for(parent_rel, directory=True)
        cleaned = self._validate_folder_name(name)
        target = parent / cleaned
        if target.exists():
            raise ValueError(f"Un élément porte déjà ce nom : {cleaned}")
        target.mkdir()
        self.invalidate_status()
        return {"path": self.relpath(target), "name": cleaned}

    def _validate_folder_name(self, name: str) -> str:
        cleaned = str(name or "").strip()
        if not cleaned:
            raise ValueError("Le nom du dossier est vide.")
        if len(cleaned) > 255:
            raise ValueError("Le nom du dossier est trop long.")
        if cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
            raise ValueError("Le nom du dossier contient un caractère interdit.")
        if "\0" in cleaned or "\n" in cleaned or "\r" in cleaned:
            raise ValueError("Le nom du dossier contient un caractère interdit.")
        if cleaned.startswith(".") or cleaned in IGNORED_DIRECTORIES:
            raise ValueError("Ce nom de dossier est réservé.")
        return cleaned

    def _validate_file_name(self, source: Path, name: str) -> str:
        cleaned = str(name or "").strip()
        if not cleaned:
            raise ValueError("Le nouveau nom est vide.")
        if len(cleaned) > 255:
            raise ValueError("Le nouveau nom est trop long.")
        if cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
            raise ValueError("Le nouveau nom contient un caractère interdit.")
        if "\0" in cleaned or "\n" in cleaned or "\r" in cleaned:
            raise ValueError("Le nouveau nom contient un caractère interdit.")
        if cleaned.startswith("."):
            raise ValueError("Un nom de photo ne peut pas commencer par un point.")
        if source.is_file():
            if Path(cleaned).suffix.lower() != source.suffix.lower():
                raise ValueError(
                    f"Conservez l'extension {source.suffix} pour renommer une photo."
                )
            if Path(cleaned).suffix.lower() not in PHOTO_EXTENSIONS:
                raise ValueError("Extension de photo non reconnue.")
        return cleaned

    def _sidecars(self, source: Path) -> list[Path]:
        candidates = [
            source.with_suffix(".xmp"),
            source.with_name(source.name + ".xmp"),
        ]
        result = []
        seen = set()
        for candidate in candidates:
            if candidate == source or candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                result.append(candidate)
        return result

    def _sidecar_target(self, sidecar: Path, source: Path, target: Path) -> Path:
        if sidecar.name == source.stem + ".xmp":
            return target.with_suffix(".xmp")
        if sidecar.name == source.name + ".xmp":
            return target.with_name(target.name + ".xmp")
        return target.parent / sidecar.name

    def _same_path(self, first: Path, second: Path) -> bool:
        try:
            return first.samefile(second)
        except OSError:
            return False

    def _move_payload(self, source: Path, target: Path) -> tuple[Path, list[tuple[Path, Path]]]:
        """Déplace une photo et ses éventuels sidecars, ou un dossier entier.

        Renvoie aussi les couples ``(nouveau, ancien)`` pour pouvoir annuler
        l'opération si la mise à jour des bases échoue.
        """
        if source == target:
            return target, []
        if target.exists() and not self._same_path(source, target):
            raise ValueError(f"Un élément existe déjà : {target.name}")

        if source.is_dir():
            if target == source or source in target.parents:
                raise ValueError("Un dossier ne peut pas être déplacé dans lui-même.")
            shutil.move(str(source), str(target))
            return target, [(target, source)]

        sidecars = self._sidecars(source)
        targets = [(sidecar, self._sidecar_target(sidecar, source, target)) for sidecar in sidecars]
        for sidecar, sidecar_target in targets:
            if sidecar_target.exists() and not self._same_path(sidecar, sidecar_target):
                raise ValueError(f"Un sidecar existe déjà : {sidecar_target.name}")

        moved: list[tuple[Path, Path]] = []
        try:
            shutil.move(str(source), str(target))
            moved.append((target, source))
            for sidecar, sidecar_target in targets:
                shutil.move(str(sidecar), str(sidecar_target))
                moved.append((sidecar_target, sidecar))
        except Exception:
            for current, original in reversed(moved):
                try:
                    shutil.move(str(current), str(original))
                except OSError:
                    pass
            raise
        return target, moved

    def _move_one(self, source_rel: str, destination_rel: str) -> dict:
        destination_rel = _normalize_rel(destination_rel)
        source = self.path_for(source_rel, must_exist=True)
        destination = self.path_for(destination_rel, directory=True)
        if source == self.root:
            raise ValueError("La racine photo ne peut pas être déplacée.")
        target = destination / source.name
        old_rel = self.relpath(source)
        new_rel = (
            (PurePosixPath(destination_rel) / source.name).as_posix()
            if destination_rel else source.name
        )
        if source == target:
            return {"status": "skipped", "source": old_rel, "destination": new_rel}
        if source.is_dir() and (destination == source or source in destination.parents):
            raise ValueError("Un dossier ne peut pas être déplacé dans lui-même.")

        recursive = source.is_dir()
        _, moved = self._move_payload(source, target)
        try:
            databases = update_paths(
                environment.photos_db(),
                environment.face_db(),
                [(old_rel, new_rel, recursive)],
            )
        except Exception:
            for current, original in reversed(moved):
                try:
                    shutil.move(str(current), str(original))
                except OSError:
                    pass
            raise
        return {
            "status": "moved",
            "source": old_rel,
            "destination": new_rel,
            "recursive": recursive,
            "databases": databases,
        }

    def _move_current(self, old_rel: str, new_rel: str) -> None:
        current = self.directory
        old_path = self.root / old_rel if old_rel else self.root
        new_path = self.root / new_rel if new_rel else self.root
        if current == old_path:
            self.directory = new_path
            return
        try:
            relative = current.relative_to(old_path)
        except ValueError:
            return
        candidate = new_path / relative
        if candidate.is_dir():
            self.directory = candidate

    def move(self, sources: list[str], destination_rel: str) -> dict:
        if not sources:
            raise ValueError("Aucun élément à déplacer.")
        with self.lock:
            normalized = []
            seen = set()
            for value in sources:
                rel = _normalize_rel(value)
                if not rel:
                    raise ValueError("La racine photo ne peut pas être déplacée.")
                if rel not in seen:
                    seen.add(rel)
                    normalized.append(rel)

            source_paths = {rel: self.path_for(rel, must_exist=True) for rel in normalized}
            selected_dirs = [path for path in source_paths.values() if path.is_dir()]
            results = []
            for rel in normalized:
                source = source_paths[rel]
                if any(
                    directory != source and directory in source.parents
                    for directory in selected_dirs
                ):
                    results.append({
                        "status": "skipped",
                        "source": rel,
                        "error": "Ce contenu est déjà inclus dans un dossier sélectionné.",
                    })
                    continue
                try:
                    result = self._move_one(rel, destination_rel)
                except Exception as error:  # noqa: BLE001 - une erreur ne bloque pas les autres
                    results.append({"status": "error", "source": rel, "error": str(error)})
                    continue
                if result.get("status") == "moved":
                    self._move_current(result["source"], result["destination"])
                results.append(result)

            current_changed = False
            try:
                stored = preferences.value("initial_directory")
                current_changed = str(self.directory) != stored
            except Exception:
                current_changed = True
            if current_changed:
                preferences.set_initial_directory(self.directory)

            databases = {
                "photos": 0,
                "face_photos": 0,
                "faces": 0,
                "reference_faces": 0,
            }
            for result in results:
                for key, value in (result.get("databases") or {}).items():
                    databases[key] = databases.get(key, 0) + int(value)
            self.invalidate_status()
            return {
                "results": results,
                "moved": sum(1 for item in results if item.get("status") == "moved"),
                "errors": sum(1 for item in results if item.get("status") == "error"),
                "skipped": sum(1 for item in results if item.get("status") == "skipped"),
                "databases": databases,
                "state": self.state(),
            }

    def rename(self, source_rel: str, name: str) -> dict:
        with self.lock:
            source = self.path_for(source_rel, must_exist=True)
            if source == self.root:
                raise ValueError("La racine photo ne peut pas être renommée.")
            cleaned = (
                self._validate_folder_name(name)
                if source.is_dir()
                else self._validate_file_name(source, name)
            )
            target = source.parent / cleaned
            old_rel = self.relpath(source)
            new_rel = self.relpath(target)
            if source == target:
                return {"status": "skipped", "source": old_rel, "destination": new_rel}
            if target.exists() and not self._same_path(source, target):
                raise ValueError(f"Un élément porte déjà ce nom : {cleaned}")
            recursive = source.is_dir()
            _, moved = self._move_payload(source, target)
            try:
                databases = update_paths(
                    environment.photos_db(),
                    environment.face_db(),
                    [(old_rel, new_rel, recursive)],
                )
            except Exception:
                for current, original in reversed(moved):
                    try:
                        shutil.move(str(current), str(original))
                    except OSError:
                        pass
                raise
            self._move_current(old_rel, new_rel)
            preferences.set_initial_directory(self.directory)
            self.invalidate_status()
            return {
                "status": "moved",
                "source": old_rel,
                "destination": new_rel,
                "recursive": recursive,
                "databases": databases,
                "state": self.state(),
            }

    def choose_directory(self) -> Path | None:
        initial = self.directory
        script = """
on run argv
    set initialFolder to POSIX file (item 1 of argv) as alias
    set chosenFolder to choose folder with prompt "Choisir le dossier à afficher" default location initialFolder
    return POSIX path of chosenFolder
end run
"""
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script, str(initial)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            if "(-128)" in result.stderr:
                return None
            raise RuntimeError(result.stderr.strip() or "Erreur AppleScript.")
        selected = result.stdout.strip()
        return Path(selected).expanduser().resolve() if selected else None


STATE: OrganizerState


class OrganizerHandler(BaseHTTPRequestHandler):
    server_version = "PhotosOrganizer/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    # --- réponses --------------------------------------------------------

    def send_bytes(self, body: bytes, content_type: str, status: int = 200, cache: bool = False):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cache:
                self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def send_json(self, payload, status: int = 200):
        self.send_bytes(_json_bytes(payload), "application/json; charset=utf-8", status)

    def send_file(self, path: Path, content_type: str | None = None, cache: bool = False):
        if not path.is_file():
            self.send_json({"error": "Fichier introuvable."}, HTTPStatus.NOT_FOUND)
            return
        try:
            body = path.read_bytes()
        except OSError as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_bytes(
            body,
            content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            cache=cache,
        )

    def read_body(self, limit: int = 2_000_000_000) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > limit:
            raise ValueError("La requête est vide ou trop volumineuse.")
        return self.rfile.read(length)

    def read_json(self) -> dict:
        payload = json.loads(self.read_body(limit=2_000_000))
        if not isinstance(payload, dict):
            raise ValueError("Contenu de requête invalide.")
        return payload

    # --- routes ----------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self.send_static("index.html")
            elif parsed.path in ("/app.js", "/styles.css"):
                self.send_static(parsed.path[1:])
            elif parsed.path == "/api/state":
                if query.get("refresh", ["0"])[0] == "1":
                    STATE.invalidate_status()
                self.send_json(STATE.state())
            elif parsed.path == "/api/tree":
                path = query.get("path", [""])[0]
                self.send_json(STATE.tree(path))
            elif parsed.path == "/api/directories":
                self.send_json(STATE.directories())
            elif parsed.path == "/api/photos":
                self.api_photos(query)
            elif parsed.path == "/api/thumbnail":
                self.api_thumbnail(query)
            elif parsed.path == "/api/image":
                self.api_image(query)
            elif parsed.path == "/api/preferences":
                self.send_json({
                    "initial_directory": preferences.value("initial_directory", ""),
                    "photos_root": str(environment.photos_root()),
                })
            else:
                self.send_error(404)
        except (ValueError, FileNotFoundError, RelocationError) as error:
            self.send_json({"error": str(error)}, 400)
        except Exception as error:  # noqa: BLE001
            print(f"Erreur : {error}", flush=True)
            self.send_json({"error": str(error)}, 500)

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/move":
                payload = self.read_json()
                sources = payload.get("sources")
                if not isinstance(sources, list):
                    raise ValueError("Liste de sources invalide.")
                self.send_json(STATE.move([str(item) for item in sources], str(payload.get("destination") or "")))
            elif route == "/api/create-folder":
                payload = self.read_json()
                self.send_json(
                    STATE.create_folder(payload.get("parent"), payload.get("name", "")),
                    HTTPStatus.CREATED,
                )
            elif route == "/api/rename":
                payload = self.read_json()
                self.send_json(STATE.rename(str(payload.get("source") or ""), str(payload.get("name") or "")))
            elif route == "/api/rating":
                payload = self.read_json()
                self.send_json(
                    STATE.set_rating(
                        str(payload.get("path") or ""),
                        int(payload.get("rating") or 0),
                    )
                )
            elif route == "/api/trash":
                payload = self.read_json()
                self.send_json(STATE.trash(str(payload.get("path") or "")))
            elif route == "/api/import":
                self.api_import()
            elif route == "/api/directory":
                payload = self.read_json()
                directory = Path(str(payload.get("directory") or "")).expanduser().resolve()
                if not directory.is_dir():
                    raise ValueError(f"Dossier introuvable : {directory}")
                STATE.set_directory(directory)
                self.send_json(STATE.state())
            elif route == "/api/select-directory":
                payload = self.read_json()
                directory = Path(str(payload.get("directory") or "")).expanduser().resolve()
                if not directory.is_dir():
                    raise ValueError(f"Dossier introuvable : {directory}")
                STATE.set_directory(directory)
                preferences.set_initial_directory(STATE.directory)
                self.send_json(STATE.state())
            elif route == "/api/change-directory":
                selected = STATE.choose_directory()
                if selected is None:
                    self.send_json({"error": "Aucun dossier sélectionné."}, 400)
                    return
                STATE.set_directory(selected)
                preferences.set_initial_directory(STATE.directory)
                self.send_json(STATE.state())
            elif route == "/api/shutdown":
                self.send_json({"status": "shutting_down"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_error(404)
        except (ValueError, FileNotFoundError, RelocationError) as error:
            self.send_json({"error": str(error)}, 400)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            self.send_json({"error": str(error)}, 500)
        except Exception as error:  # noqa: BLE001
            print(f"Erreur : {error}", flush=True)
            self.send_json({"error": str(error)}, 500)

    def api_photos(self, query):
        try:
            offset = max(0, int(query.get("offset", ["0"])[0]))
            limit = min(1000, max(1, int(query.get("limit", ["500"])[0])))
        except ValueError as error:
            raise ValueError("Paramètres de pagination invalides.") from error
        path = query.get("path", [""])[0]
        self.send_json(STATE.photos(path, offset, limit))

    def api_thumbnail(self, query):
        path = query.get("path", [""])[0]
        target, content_type = STATE.thumbnail(path)
        if target is None:
            self.send_bytes(_placeholder_svg(), "image/svg+xml")
            return
        self.send_file(target, content_type, cache=True)

    def api_image(self, query):
        path = query.get("path", [""])[0]
        target, content_type = STATE.image(path)
        if target is None:
            self.send_bytes(_placeholder_svg(), "image/svg+xml")
            return
        self.send_file(target, content_type, cache=True)

    def api_import(self):
        fields, files = parse_multipart(
            self.read_body(),
            self.headers.get("Content-Type", ""),
        )
        if not files:
            raise ValueError("Aucun fichier à importer.")
        try:
            paths = json.loads(fields.get("paths") or "[]")
            mtimes = json.loads(fields.get("mtimes") or "[]")
        except json.JSONDecodeError as error:
            raise ValueError("Liste de fichiers invalide.") from error
        if not isinstance(paths, list):
            paths = []
        if not isinstance(mtimes, list):
            mtimes = []
        items = []
        for index, (_name, filename, payload) in enumerate(files):
            relative = paths[index] if index < len(paths) and paths[index] else filename
            mtime = mtimes[index] if index < len(mtimes) else None
            try:
                mtime_value = int(float(mtime)) if mtime is not None else None
            except (TypeError, ValueError):
                mtime_value = None
            items.append((str(relative), payload, mtime_value))
        self.send_json(
            STATE.import_files(fields.get("destination") or "", items),
            HTTPStatus.CREATED,
        )

    def send_static(self, filename: str):
        path = APP_DIR / filename
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        self.send_bytes(body, content_type)


class OrganizerServer(ThreadingHTTPServer):
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Déplacer photos et dossiers avec mise à jour des bases.")
    parser.add_argument("directory", nargs="?", help="Dossier de photos courant.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()

    root = environment.photos_root().expanduser().resolve()
    if args.directory:
        directory = Path(args.directory).expanduser().resolve()
    else:
        directory = preferences.initial_directory(root if root.is_dir() else Path.cwd()) or root
    if not directory.is_dir():
        parser.error(f"Le dossier de photos n'existe pas : {directory}")

    global STATE
    STATE = OrganizerState(directory)
    server = OrganizerServer((args.host, args.port), OrganizerHandler)
    print(f"Racine    : {STATE.root}", flush=True)
    print(f"Dossier   : {STATE.directory}", flush=True)
    print(f"Classement : http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
