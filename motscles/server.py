#!/usr/bin/env python3
"""Local browser interface for editing keywords, stars, and authors of photos."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
from urllib.parse import parse_qs, quote, urlparse

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import annotations, photos_db
from tools import environment, preferences
from tools.getMetaData import get_metadata_many
from tools.setMetaData import set_metadata


PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".psd",
    ".nef", ".nrw", ".cr2", ".cr3", ".arw", ".dng", ".raf",
    ".rw2", ".orf", ".pef",
}
PREVIEW_TAGS = ("PhotoshopThumbnail", "PreviewImage", "JpgFromRaw", "ThumbnailImage")
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
)

# Keywords + rating + author only. Hierarchical subjects are left untouched so
# the TAXREF hierarchy written by the Faune app is preserved.
KEYWORDS_TAGS = ["-IPTC:Keywords", "-XMP-dc:Subject"]
RATING_TAGS = ["-XMP-xmp:Rating", "-EXIF:Rating"]
AUTHOR_TAGS = ["-IPTC:By-line", "-XMP-dc:Creator", "-EXIF:Artist"]
ALL_METADATA_TAGS = KEYWORDS_TAGS + RATING_TAGS + AUTHOR_TAGS

MAX_SELECTED_PHOTOS = 500
DEFAULT_AUTHOR = "Pierre"
DEFAULT_RATING = 3
DEFAULT_GROUP = 1

thumbnail_lock = threading.Lock()
preview_cache: OrderedDict[tuple[str, int, int], tuple[bytes, str]] = OrderedDict()
jobs: OrderedDict[str, dict] = OrderedDict()
jobs_lock = threading.Lock()
metadata_read_lock = threading.Lock()


def metadata_values(*values) -> list[str]:
    flattened = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(metadata_values(*value))
        elif isinstance(value, str) and value.strip():
            flattened.append(value.strip())
    unique = {}
    for value in flattened:
        unique.setdefault(value.casefold(), value)
    return list(unique.values())


def merge_keyword_operations(current: list[str], operations: dict) -> list[str]:
    """Applique des ajouts/retraits à la liste réellement présente dans XMP.

    Le client ne renvoie plus la liste complète des mots-clés : celle-ci peut
    être devenue obsolète si une autre application (Faune, Visages…) a modifié
    le fichier entre-temps. On relit donc toujours l'état courant avant de
    fusionner les opérations demandées.
    """
    result = [] if operations.get("clear") else metadata_values(current)
    removed = {
        annotations.normalize_name(value)
        for value in operations.get("remove", [])
        if annotations.normalize_name(value)
    }
    if removed:
        result = [
            value for value in result
            if annotations.normalize_name(value) not in removed
        ]
    seen = {annotations.normalize_name(value) for value in result}
    for value in operations.get("add", []):
        cleaned = str(value or "").strip()
        key = annotations.normalize_name(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _photo_relpath(path: Path) -> str | None:
    """Chemin relatif à la racine photo, pour interroger les index dérivés."""
    root = environment.photos_root().expanduser().resolve()
    try:
        return path.expanduser().resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def identified_person_paths(paths: list[str]) -> set[str]:
    """Photos du lot ayant au moins une personne identifiée.

    On interroge les deux index disponibles : ``photos.db`` pour les liens
    dérivés des métadonnées, et ``face.db`` pour les noms attribués dans
    Visages et pas encore synchronisés. Un visage validé « sans nom » ou
    invalidé ne compte pas comme une identification.
    """
    unique = [value for value in dict.fromkeys(paths) if value]
    if not unique:
        return set()
    result: set[str] = set()

    def chunks(values: list[str], size: int = 400):
        for start in range(0, len(values), size):
            yield values[start:start + size]

    for chunk in chunks(unique):
        placeholders = ",".join("?" for _ in chunk)

        face_path = environment.face_db()
        if face_path.is_file():
            try:
                conn = sqlite3.connect(
                    f"file:{quote(str(face_path))}?mode=ro",
                    uri=True,
                    timeout=2,
                )
                try:
                    rows = conn.execute(
                        "SELECT DISTINCT f.relpath FROM Face f "
                        "JOIN FaceLabel l ON l.id_face = f.id "
                        f"WHERE f.invalid = 0 AND f.relpath IN ({placeholders})",
                        chunk,
                    ).fetchall()
                finally:
                    conn.close()
                result.update(str(row[0]) for row in rows)
            except sqlite3.Error:
                pass

        photos_path = environment.photos_db()
        if photos_path.is_file():
            try:
                conn = sqlite3.connect(
                    f"file:{quote(str(photos_path))}?mode=ro",
                    uri=True,
                    timeout=2,
                )
                try:
                    rows = conn.execute(
                        "SELECT DISTINCT p.relpath FROM photos p "
                        "JOIN photo_people pp ON pp.photo_id = p.id "
                        f"WHERE p.relpath IN ({placeholders})",
                        chunk,
                    ).fetchall()
                finally:
                    conn.close()
                result.update(str(row[0]) for row in rows)
            except sqlite3.Error:
                pass
    return result


def photo_groups(paths: list[str], default: int = DEFAULT_GROUP) -> dict[str, int]:
    """Groupes d'accès stockés dans ``photos.db`` pour les chemins demandés."""
    unique = [value for value in dict.fromkeys(paths) if value]
    if not unique:
        return {}
    result: dict[str, int] = {}
    photos_path = environment.photos_db()
    if not photos_path.is_file():
        return result
    try:
        conn = sqlite3.connect(
            f"file:{quote(str(photos_path))}?mode=ro",
            uri=True,
            timeout=2,
        )
        try:
            for start in range(0, len(unique), 400):
                chunk = unique[start:start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    "SELECT relpath, groupe FROM photos "
                    f"WHERE relpath IN ({placeholders})",
                    chunk,
                ).fetchall()
                for relpath, group in rows:
                    try:
                        value = int(group)
                    except (TypeError, ValueError):
                        value = default
                    result[str(relpath)] = max(1, value)
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return result


def read_photo_keywords_rating_author(metadata: dict) -> dict:
    iptc = metadata.get("IPTC", {})
    xmp = metadata.get("XMP", {})
    exif = metadata.get("EXIF", {})

    keywords = []
    seen = set()
    for value in (
        iptc.get("Keywords"),
        xmp.get("dc.subject"),
        xmp.get("Subject"),
    ):
        for keyword in annotations.keyword_values(value):
            key = keyword.casefold()
            if key not in seen:
                seen.add(key)
                keywords.append(keyword)

    rating = None
    for value in (xmp.get("xmp.Rating"), exif.get("Rating")):
        if value is None:
            continue
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= candidate <= 5:
            rating = candidate
            break

    authors = metadata_values(
        iptc.get("By-line"),
        xmp.get("dc.creator"),
        exif.get("Artist"),
    )

    return {
        "keywords": keywords,
        "rating": rating,
        "author": authors[0] if authors else None,
        "metadataRead": True,
    }


def write_photo_metadata(
    path: Path,
    keywords: list[str] | None,
    rating: int | None,
    author: str | None,
    keyword_operations: dict | None = None,
    group: int | None = None,
) -> dict:
    iptc_data: dict = {}
    xmp_data: dict = {}
    exif_data: dict = {}

    if keyword_operations is not None:
        current_records = get_metadata_many([str(path)], tags=KEYWORDS_TAGS)
        current_metadata = current_records.get(str(path))
        if current_metadata is None:
            raise RuntimeError(
                f"ExifTool n’a pas pu relire les mots-clés de {path.name}."
            )
        current_keywords = read_photo_keywords_rating_author(current_metadata)["keywords"]
        keywords = merge_keyword_operations(current_keywords, keyword_operations)

    if keywords is not None:
        cleaned = metadata_values(keywords)
        iptc_data["Keywords"] = cleaned or [""]
        xmp_data["Subject"] = cleaned or [""]

    if rating is not None:
        xmp_data["Rating"] = rating
        exif_data["Rating"] = rating

    if author is not None:
        cleaned_author = author.strip()
        iptc_data["By-line"] = cleaned_author
        xmp_data["Creator"] = [cleaned_author] if cleaned_author else [""]
        exif_data["Artist"] = cleaned_author

    categories = {}
    if iptc_data:
        categories["IPTC"] = iptc_data
    if xmp_data:
        categories["XMP"] = xmp_data
    if exif_data:
        categories["EXIF"] = exif_data

    result: dict = {}
    if categories:
        set_metadata(path, categories)
        updated_records = get_metadata_many([str(path)], tags=ALL_METADATA_TAGS)
        updated = updated_records.get(str(path))
        if updated is None:
            raise RuntimeError(f"ExifTool n’a pas pu relire les métadonnées de {path.name}.")
        result = read_photo_keywords_rating_author(updated)

    if group is not None:
        value = photos_db.record_group_for_path(
            environment.photos_root(), path, int(group)
        )
        result["groupe"] = value
    return result


@lru_cache(maxsize=4)
def _read_photo_metadata(snapshot: tuple[tuple[str, int, int], ...]):
    return get_metadata_many([path for path, _, _ in snapshot])


def read_photo_metadata(paths: list[Path]):
    snapshot = tuple(
        (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        for path in paths
    )
    with metadata_read_lock:
        return _read_photo_metadata(snapshot)


def image_content_type(body: bytes) -> str | None:
    return next(
        (mime for signature, mime in IMAGE_SIGNATURES if body.startswith(signature)),
        None,
    )


def _extract_preview(path: str) -> bytes:
    executable = shutil.which("exiftool")
    if executable is None:
        return b""
    for tag in PREVIEW_TAGS:
        result = subprocess.run(
            [executable, "-b", f"-{tag}", path],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and result.stdout and image_content_type(result.stdout):
            return result.stdout
    return b""


def convert_preview(path: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="motscles-preview-") as directory:
        output = Path(directory) / "preview.jpg"
        result = subprocess.run(
            ["sips", "-s", "format", "jpeg", str(path), "--out", str(output)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0 or not output.is_file():
            message = result.stderr.strip() or result.stdout.strip() or "format non pris en charge"
            raise RuntimeError(f"Conversion de l’aperçu impossible pour {path.name} : {message}")
        return output.read_bytes()


DIRECT_THUMBNAIL_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif"}


def direct_thumbnail(path: Path) -> tuple[bytes, str] | None:
    """Return the file bytes when the format can be served as-is."""
    if path.suffix.lower() not in DIRECT_THUMBNAIL_SUFFIXES:
        return None
    body = path.read_bytes()
    content_type = image_content_type(body) or mimetypes.guess_type(path.name)[0]
    if content_type is None or not content_type.startswith("image/"):
        raise RuntimeError(f"Format d’aperçu non reconnu pour {path.name}.")
    return body, content_type


def build_thumbnail(path: Path) -> tuple[bytes, str]:
    direct = direct_thumbnail(path)
    if direct is not None:
        return direct
    body = _extract_preview(str(path)) or convert_preview(path)
    if not body:
        raise RuntimeError(f"Aucun aperçu disponible pour {path.name}.")
    content_type = image_content_type(body) or mimetypes.guess_type(path.name)[0]
    if content_type is None or not content_type.startswith("image/"):
        raise RuntimeError(f"Format d’aperçu non reconnu pour {path.name}.")
    return body, content_type


def thumbnail_for(path: Path) -> tuple[bytes, str]:
    # Images that ExifTool can read directly are streamed without being kept
    # in memory (the browser caches them via the Cache-Control header). Only
    # extracted RAW/PSD previews are stored in the LRU cache.
    if path.suffix.lower() in DIRECT_THUMBNAIL_SUFFIXES:
        return build_thumbnail(path)

    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with thumbnail_lock:
        cached = preview_cache.get(key)
        if cached is not None:
            preview_cache.move_to_end(key)
            return cached
    result = build_thumbnail(path)
    with thumbnail_lock:
        preview_cache[key] = result
        while len(preview_cache) > 48:
            preview_cache.popitem(last=False)
    return result


def read_preferences() -> dict:
    """Réglages de Mots-clés, dans la section `apps.motscles`."""
    return preferences.app_settings("motscles")


def write_preferences(changes: dict) -> None:
    preferences.update_app("motscles", changes)


def default_author_rating() -> tuple[str, int]:
    """Valeurs par défaut effectives de l'application Mots-clés."""
    settings = read_preferences()
    author = settings.get("default_author")
    rating = settings.get("default_rating")
    if not isinstance(author, str) or not author.strip():
        author = DEFAULT_AUTHOR
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        rating = DEFAULT_RATING
    return author.strip(), rating


def default_group() -> int:
    """Groupe d'accès proposé pour une photo qui n'en a pas encore."""
    configured = read_preferences().get("default_group")
    if isinstance(configured, bool) or not isinstance(configured, int) or configured < 1:
        return DEFAULT_GROUP
    return configured


def initial_directory_from_preferences() -> Path:
    configured = preferences.value("initial_directory")
    if not isinstance(configured, str) or not configured.strip():
        return Path.home()
    initial_directory = Path(configured).expanduser()
    if not initial_directory.is_dir():
        print(
            f"Le dossier initial configuré n’existe pas ({initial_directory}). "
            "Utilisation du dossier personnel.",
            flush=True,
        )
        return Path.home()
    return initial_directory.resolve()


def choose_photos_directory(initial_directory: Path) -> Path | None:
    script = """
on run argv
    set initialFolder to POSIX file (item 1 of argv) as alias
    set chosenFolder to choose folder with prompt "Choisir le dossier contenant les photos" default location initialFolder
    return POSIX path of chosenFolder
end run
"""
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script, str(initial_directory)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        if "(-128)" in result.stderr:
            return None
        message = result.stderr.strip() or result.stdout.strip() or "Erreur AppleScript."
        raise RuntimeError(f"Impossible de choisir le dossier : {message}")
    selected_directory = result.stdout.strip()
    if not selected_directory:
        raise RuntimeError("Le sélecteur macOS n’a renvoyé aucun dossier.")
    return Path(selected_directory).expanduser().resolve()


class MotsclesHandler(BaseHTTPRequestHandler):
    photos_dir: Path

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}", flush=True)

    def send_body(self, body: bytes, content_type: str, status: int = 200, cache_control: str | None = None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control or "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            print(f"Client {self.address_string()} déconnecté.", flush=True)

    def send_json(self, payload: dict, status: int = 200):
        self.send_body(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            self.send_static("index.html", "text/html; charset=utf-8")
        elif parsed.path == "/app.js":
            self.send_static("app.js", "application/javascript; charset=utf-8")
        elif parsed.path == "/styles.css":
            self.send_static("styles.css", "text/css; charset=utf-8")
        elif parsed.path == "/api/photos":
            self.list_photos(query)
        elif parsed.path == "/api/thumbnail":
            self.send_thumbnail(query)
        elif parsed.path == "/api/save-metadata":
            self.save_job_status(query)
        elif parsed.path == "/api/apply-defaults":
            self.defaults_job_status(query)
        elif parsed.path == "/api/preferences":
            self.get_preferences()
        else:
            self.send_error(404)

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/save-metadata":
            self.handle_save_metadata()
        elif route == "/api/apply-defaults":
            self.handle_apply_defaults()
        elif route == "/api/update-photo":
            self.handle_update_photo()
        elif route == "/api/change-directory":
            self.handle_change_directory()
        elif route == "/api/directory":
            self.handle_set_directory()
        elif route == "/api/preferences":
            self.save_preferences()
        elif route == "/api/shutdown":
            self.handle_shutdown()
        else:
            self.send_error(404)

    def handle_shutdown(self):
        self.send_json({"status": "shutting_down"})
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Preferences
    # ------------------------------------------------------------------ #
    def get_preferences(self):
        author, rating = default_author_rating()
        self.send_json({
            "default_author": author,
            "default_rating": rating,
            "default_group": default_group(),
            "initial_directory": preferences.value("initial_directory", ""),
        })

    def save_preferences(self):
        try:
            data = self.read_request_json()
            changes = {}
            if "default_author" in data:
                author = str(data["default_author"]).strip()
                if author:
                    changes["default_author"] = author
            if "default_rating" in data:
                rating = data["default_rating"]
                if isinstance(rating, (int, float)) and 1 <= rating <= 5:
                    changes["default_rating"] = int(rating)
            if "default_group" in data:
                group = data["default_group"]
                if (
                    not isinstance(group, bool)
                    and isinstance(group, (int, float))
                    and int(group) == group
                    and group >= 1
                ):
                    changes["default_group"] = int(group)
            settings = write_preferences(changes)
            self.send_json({"preferences": settings})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)

    # ------------------------------------------------------------------ #
    # Directory
    # ------------------------------------------------------------------ #
    def handle_change_directory(self):
        try:
            new_dir = self.choose_new_directory()
            if new_dir is None:
                self.send_json({"error": "Aucun dossier sélectionné."}, 400)
                return
            preferences.set_initial_directory(new_dir)
            MotsclesHandler.photos_dir = new_dir
            _read_photo_metadata.cache_clear()
            with thumbnail_lock:
                preview_cache.clear()
            self.send_json({"directory": str(new_dir)})
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": str(error)}, 500)

    def handle_set_directory(self):
        """Change le dossier sans dialogue : utilisé par l'application globale."""
        try:
            data = self.read_request_json()
            directory = Path(str(data.get("directory") or "")).expanduser().resolve()
            if not directory.is_dir():
                raise ValueError(f"Dossier introuvable : {directory}")
            MotsclesHandler.photos_dir = directory
            _read_photo_metadata.cache_clear()
            with thumbnail_lock:
                preview_cache.clear()
            self.send_json({"directory": str(directory)})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except OSError as error:
            self.send_json({"error": str(error)}, 500)

    def choose_new_directory(self) -> Path | None:
        initial = initial_directory_from_preferences()
        if sys.platform == "darwin":
            return choose_photos_directory(initial)
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError as error:
            raise RuntimeError(f"Tkinter n’est pas disponible : {error}")
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            selected = filedialog.askdirectory(
                title="Choisir le dossier contenant les photos",
                initialdir=str(initial),
                mustexist=True,
            )
        finally:
            root.destroy()
        if not selected:
            return None
        return Path(selected).expanduser().resolve()

    # ------------------------------------------------------------------ #
    # Photos listing
    # ------------------------------------------------------------------ #
    def photo_files(self) -> list[Path]:
        return sorted(
            (
                path for path in self.photos_dir.iterdir()
                if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )

    def list_photos(self, query: dict[str, list[str]]):
        try:
            offset = max(0, int(query.get("offset", ["0"])[0]))
            limit = min(500, max(1, int(query.get("limit", ["200"])[0])))
        except ValueError:
            self.send_json({"error": "Paramètres de pagination invalides."}, 400)
            return
        files = self.photo_files()
        page = files[offset:offset + limit]
        metadata_by_path = {}
        metadata_error = ""
        try:
            metadata_by_path = read_photo_metadata([path.resolve() for path in page])
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            metadata_error = str(error)
        relpaths = {
            path: _photo_relpath(path)
            for path in page
        }
        indexed_relpaths = [value for value in relpaths.values() if value]
        identified = identified_person_paths(indexed_relpaths)
        groups = photo_groups(indexed_relpaths)
        self.send_json({
            "photos": [
                self.photo_listing(
                    path,
                    metadata_by_path,
                    metadata_error,
                    bool(relpaths[path] and relpaths[path] in identified),
                    groups.get(relpaths[path], DEFAULT_GROUP),
                )
                for path in page
            ],
            "offset": offset,
            "limit": limit,
            "total": len(files),
            "directory": str(self.photos_dir),
        })

    def photo_listing(
        self,
        path: Path,
        metadata_by_path: dict,
        metadata_error: str,
        has_identified_person: bool,
        group: int,
    ) -> dict:
        metadata = metadata_by_path.get(str(path.resolve()))
        if metadata is None:
            photo_data = {
                "keywords": [],
                "rating": None,
                "author": None,
                "metadataRead": False,
            }
        else:
            photo_data = read_photo_keywords_rating_author(metadata)
        return {
            "name": path.name,
            "thumbnail": f"/api/thumbnail?name={quote(path.name)}",
            **photo_data,
            "groupe": group,
            "hasIdentifiedPerson": has_identified_person,
            "metadataError": metadata_error,
        }

    def photo_path(self, name: str) -> Path | None:
        if not name or Path(name).name != name:
            return None
        path = self.photos_dir / name
        try:
            resolved_path = path.resolve(strict=True)
        except OSError:
            return None
        if (
            resolved_path.parent != self.photos_dir
            or not resolved_path.is_file()
            or resolved_path.suffix.lower() not in PHOTO_EXTENSIONS
        ):
            return None
        return resolved_path

    def send_thumbnail(self, query: dict[str, list[str]]):
        name = query.get("name", [""])[0]
        path = self.photo_path(name)
        if path is None:
            self.send_error(404)
            return
        try:
            body, content_type = thumbnail_for(path)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": f"Aperçu indisponible pour {name} : {error}"}, 500)
            return
        self.send_body(body, content_type, cache_control="public, max-age=3600")

    # ------------------------------------------------------------------ #
    # Synchronous single-photo update (inline stars)
    # ------------------------------------------------------------------ #
    def handle_update_photo(self):
        try:
            data = self.read_request_json()
            name = data.get("photoName")
            if not isinstance(name, str) or self.photo_path(name) is None:
                raise ValueError("Photo absente du dossier sélectionné.")
            keywords = self.validate_keywords(data.get("keywords")) if "keywords" in data else None
            keyword_operations = (
                self.validate_keyword_operations(data.get("keywordOperations"))
                if "keywordOperations" in data
                else None
            )
            rating = self.validate_rating(data.get("rating")) if "rating" in data else None
            author = self.validate_author(data.get("author")) if "author" in data else None
            group = self.validate_group(data.get("groupe")) if "groupe" in data else None
            if keywords is not None and keyword_operations is not None:
                raise ValueError("Fournissez soit les mots-clés, soit les opérations, pas les deux.")
            if (
                keywords is None
                and keyword_operations is None
                and rating is None
                and author is None
                and group is None
            ):
                raise ValueError("Aucune modification fournie.")
            metadata = write_photo_metadata(
                self.photo_path(name),
                keywords,
                rating,
                author,
                keyword_operations=keyword_operations,
                group=group,
            )
            _read_photo_metadata.cache_clear()
            self.send_json({"photoName": name, "metadata": metadata})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": str(error)}, 500)

    # ------------------------------------------------------------------ #
    # Default author and rating
    # ------------------------------------------------------------------ #
    def handle_apply_defaults(self):
        try:
            author, rating = default_author_rating()
            paths = self.photo_files()
            job_id = uuid.uuid4().hex
            job = {
                "total": len(paths),
                "done": 0,
                "complete": False,
                "updated": [],
                "skipped": 0,
                "metadata": {},
                "errors": [],
            }
            with jobs_lock:
                completed_jobs = [
                    key for key, value in jobs.items() if value["complete"]
                ]
                while len(jobs) >= 20 and completed_jobs:
                    jobs.pop(completed_jobs.pop(0), None)
                jobs[job_id] = job
            threading.Thread(
                target=self.run_apply_defaults_job,
                args=(job_id, paths, author, rating),
                daemon=True,
            ).start()
            self.send_json({
                "jobId": job_id,
                "total": len(paths),
                "default_author": author,
                "default_rating": rating,
            }, 202)
        except (OSError, RuntimeError) as error:
            self.send_json({"error": str(error)}, 500)

    def run_apply_defaults_job(
        self,
        job_id: str,
        paths: list[Path],
        author: str,
        rating: int,
    ):
        try:
            metadata_by_path = read_photo_metadata([path.resolve() for path in paths])
            pending = []
            for path in paths:
                metadata = metadata_by_path.get(str(path.resolve()))
                if metadata is None:
                    with jobs_lock:
                        jobs[job_id]["errors"].append({
                            "filename": path.name,
                            "message": "Métadonnées illisibles.",
                        })
                        jobs[job_id]["done"] += 1
                    continue
                current = read_photo_keywords_rating_author(metadata)
                missing_author = not current["author"]
                missing_rating = current["rating"] is None
                if not missing_author and not missing_rating:
                    with jobs_lock:
                        jobs[job_id]["skipped"] += 1
                        jobs[job_id]["done"] += 1
                    continue
                pending.append((path, missing_author, missing_rating))

            if pending:
                with ThreadPoolExecutor(max_workers=8) as executor:
                    futures = {
                        executor.submit(
                            write_photo_metadata,
                            path,
                            None,
                            rating if missing_rating else None,
                            author if missing_author else None,
                        ): path.name
                        for path, missing_author, missing_rating in pending
                    }
                    for future in as_completed(futures):
                        name = futures[future]
                        try:
                            metadata = future.result()
                            with jobs_lock:
                                jobs[job_id]["updated"].append(name)
                                jobs[job_id]["metadata"][name] = metadata
                        except Exception as error:
                            with jobs_lock:
                                jobs[job_id]["errors"].append({
                                    "filename": name,
                                    "message": str(error),
                                })
                        finally:
                            with jobs_lock:
                                jobs[job_id]["done"] += 1
        except Exception as error:
            print(f"Job {job_id} interrompu : {error}", flush=True)
            with jobs_lock:
                jobs[job_id]["errors"].append({
                    "filename": "",
                    "message": str(error),
                })
                jobs[job_id]["done"] = jobs[job_id]["total"]
        finally:
            _read_photo_metadata.cache_clear()
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id]["complete"] = True

    def defaults_job_status(self, query: dict[str, list[str]]):
        job_id = query.get("jobId", [""])[0]
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                job = {
                    **job,
                    "updated": list(job["updated"]),
                    "metadata": dict(job["metadata"]),
                    "errors": list(job["errors"]),
                }
        if job is None:
            self.send_json({"error": "Cette application des valeurs par défaut est introuvable."}, 404)
            return
        self.send_json({"jobId": job_id, **job})

    # ------------------------------------------------------------------ #
    # Batch save
    # ------------------------------------------------------------------ #
    def handle_save_metadata(self):
        try:
            data = self.read_request_json()
            names = data.get("photoNames")
            if (
                not isinstance(names, list)
                or not names
                or len(names) > MAX_SELECTED_PHOTOS
                or any(not isinstance(name, str) for name in names)
                or len(set(names)) != len(names)
            ):
                raise ValueError(f"Sélectionnez de 1 à {MAX_SELECTED_PHOTOS} photos.")
            keywords = self.validate_keywords(data.get("keywords")) if "keywords" in data else None
            keyword_operations = (
                self.validate_keyword_operations(data.get("keywordOperations"))
                if "keywordOperations" in data
                else None
            )
            rating = self.validate_rating(data.get("rating")) if "rating" in data else None
            author = self.validate_author(data.get("author")) if "author" in data else None
            group = self.validate_group(data.get("groupe")) if "groupe" in data else None
            if keywords is not None and keyword_operations is not None:
                raise ValueError("Fournissez soit les mots-clés, soit les opérations, pas les deux.")
            if (
                keywords is None
                and keyword_operations is None
                and rating is None
                and author is None
                and group is None
            ):
                raise ValueError("Aucune modification fournie.")
            paths = []
            for name in names:
                path = self.photo_path(name)
                if path is None:
                    raise ValueError(f"Photo absente ou chemin invalide : {name}")
                paths.append(path)

            job_id = uuid.uuid4().hex
            job = {
                "total": len(paths),
                "done": 0,
                "complete": False,
                "updated": [],
                "metadata": {},
                "errors": [],
            }
            with jobs_lock:
                completed_jobs = [
                    key for key, value in jobs.items() if value["complete"]
                ]
                while len(jobs) >= 20 and completed_jobs:
                    jobs.pop(completed_jobs.pop(0), None)
                jobs[job_id] = job

            threading.Thread(
                target=self.run_save_job,
                args=(
                    job_id, paths, keywords, rating, author,
                    keyword_operations, group,
                ),
                daemon=True,
            ).start()
            self.send_json({"jobId": job_id}, 202)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": str(error)}, 500)

    def validate_keywords(self, value) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("Les mots-clés doivent être une liste.")
        cleaned = [kw.strip() for kw in value if isinstance(kw, str) and kw.strip()]
        if len(cleaned) != len(set(kw.casefold() for kw in cleaned)):
            raise ValueError("Mots-clés en double (insensible à la casse).")
        return cleaned

    def validate_keyword_operations(self, value) -> dict | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("Les opérations sur les mots-clés sont invalides.")
        additions = self.validate_keywords(value.get("add", [])) or []
        removals = self.validate_keywords(value.get("remove", [])) or []
        clear = bool(value.get("clear"))
        overlap = {kw.casefold() for kw in additions} & {kw.casefold() for kw in removals}
        if overlap:
            raise ValueError("Un mot-clé ne peut pas être ajouté et retiré à la fois.")
        if not clear and not additions and not removals:
            return None
        return {"add": additions, "remove": removals, "clear": clear}

    @staticmethod
    def validate_rating(value) -> int | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)) or not 1 <= value <= 5:
            raise ValueError("La note doit être un entier entre 1 et 5.")
        return int(value)

    @staticmethod
    def validate_author(value) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("L’auteur doit être une chaîne de caractères.")
        return value.strip()

    @staticmethod
    def validate_group(value) -> int | None:
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or int(value) != value
            or value < 1
        ):
            raise ValueError("Le groupe doit être un entier positif.")
        return int(value)

    def save_job_status(self, query: dict[str, list[str]]):
        job_id = query.get("jobId", [""])[0]
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                job = {
                    **job,
                    "updated": list(job["updated"]),
                    "metadata": dict(job["metadata"]),
                    "errors": list(job["errors"]),
                }
        if job is None:
            self.send_json({"error": "Cette sauvegarde est introuvable."}, 404)
            return
        self.send_json({"jobId": job_id, **job})

    def run_save_job(
        self,
        job_id: str,
        paths: list[Path],
        keywords: list[str] | None,
        rating: int | None,
        author: str | None,
        keyword_operations: dict | None,
        group: int | None,
    ):
        try:
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {
                    executor.submit(
                        write_photo_metadata,
                        path,
                        keywords,
                        rating,
                        author,
                        keyword_operations,
                        group,
                    ): path.name
                    for path in paths
                }
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        metadata = future.result()
                        with jobs_lock:
                            jobs[job_id]["updated"].append(name)
                            jobs[job_id]["metadata"][name] = metadata
                    except Exception as error:
                        with jobs_lock:
                            jobs[job_id]["errors"].append({
                                "filename": name,
                                "message": str(error),
                            })
                    finally:
                        with jobs_lock:
                            jobs[job_id]["done"] += 1
        except Exception as error:
            print(f"Job {job_id} interrompu : {error}", flush=True)
            with jobs_lock:
                jobs[job_id]["errors"].append({
                    "filename": "",
                    "message": str(error),
                })
        finally:
            _read_photo_metadata.cache_clear()
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id]["complete"] = True

    def read_request_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 1_000_000:
            raise ValueError("La requête est vide ou trop volumineuse.")
        data = json.loads(self.rfile.read(content_length))
        if not isinstance(data, dict):
            raise ValueError("Le contenu de la requête est invalide.")
        return data

    def send_static(self, filename: str, content_type: str):
        try:
            body = (APP_DIR / filename).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_body(body, content_type)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Interface locale pour éditer mots-clés, notes et auteurs des photos."
    )
    parser.add_argument("photos_dir", nargs="?", help="Dossier contenant les photos.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.photos_dir:
        photos_dir = Path(args.photos_dir).expanduser().resolve()
    elif sys.platform == "darwin":
        try:
            photos_dir = choose_photos_directory(initial_directory_from_preferences())
        except (OSError, RuntimeError) as error:
            parser.error(str(error))
        if photos_dir is None:
            print("Aucun dossier sélectionné ; arrêt.", flush=True)
            return
    else:
        parser.error("Indiquez le dossier de photos en argument.")
        return

    if not photos_dir.is_dir():
        parser.error(f"Le dossier de photos n’existe pas : {photos_dir}")

    MotsclesHandler.photos_dir = photos_dir

    try:
        server = ThreadingHTTPServer((args.host, args.port), MotsclesHandler)
    except OSError as error:
        parser.error(f"Impossible de démarrer le serveur : {error}")
    print(f"Dossier de photos : {photos_dir}", flush=True)
    print(f"Interface : http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
