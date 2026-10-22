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


def read_photo_keywords_rating_author(metadata: dict) -> dict:
    iptc = metadata.get("IPTC", {})
    xmp = metadata.get("XMP", {})
    exif = metadata.get("EXIF", {})

    keywords = metadata_values(
        iptc.get("Keywords"),
        xmp.get("dc.subject"),
        xmp.get("Subject"),
    )

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
) -> dict:
    iptc_data: dict = {}
    xmp_data: dict = {}
    exif_data: dict = {}

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
    set_metadata(path, categories)

    updated_records = get_metadata_many([str(path)], tags=ALL_METADATA_TAGS)
    updated = updated_records.get(str(path))
    if updated is None:
        raise RuntimeError(f"ExifTool n’a pas pu relire les métadonnées de {path.name}.")
    return read_photo_keywords_rating_author(updated)


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
    preferences_path = APP_DIR / "preferences.json"
    try:
        data = json.loads(preferences_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        print(f"Préférences illisibles ({preferences_path}) : {error}", flush=True)
        return {}
    return data if isinstance(data, dict) else {}


def write_preferences(preferences: dict) -> None:
    preferences_path = APP_DIR / "preferences.json"
    preferences_path.write_text(
        json.dumps(preferences, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def initial_directory_from_preferences() -> Path:
    configured = read_preferences().get("initial_directory")
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
        elif parsed.path == "/api/preferences":
            self.get_preferences()
        else:
            self.send_error(404)

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/save-metadata":
            self.handle_save_metadata()
        elif route == "/api/update-photo":
            self.handle_update_photo()
        elif route == "/api/change-directory":
            self.handle_change_directory()
        elif route == "/api/directory":
            self.handle_set_directory()
        elif route == "/api/preferences":
            self.save_preferences()
        else:
            self.send_error(404)

    # ------------------------------------------------------------------ #
    # Preferences
    # ------------------------------------------------------------------ #
    def get_preferences(self):
        preferences = read_preferences()
        author = preferences.get("default_author")
        rating = preferences.get("default_rating")
        if not isinstance(author, str) or not author.strip():
            author = DEFAULT_AUTHOR
        if not isinstance(rating, int) or not 1 <= rating <= 5:
            rating = DEFAULT_RATING
        self.send_json({
            "default_author": author,
            "default_rating": rating,
            "initial_directory": preferences.get("initial_directory", ""),
        })

    def save_preferences(self):
        try:
            data = self.read_request_json()
            preferences = read_preferences()
            if "default_author" in data:
                author = str(data["default_author"]).strip()
                if author:
                    preferences["default_author"] = author
            if "default_rating" in data:
                rating = data["default_rating"]
                if isinstance(rating, (int, float)) and 1 <= rating <= 5:
                    preferences["default_rating"] = int(rating)
            write_preferences(preferences)
            self.send_json({"preferences": preferences})
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
            preferences = read_preferences()
            preferences["initial_directory"] = str(new_dir)
            write_preferences(preferences)
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
    def list_photos(self, query: dict[str, list[str]]):
        try:
            offset = max(0, int(query.get("offset", ["0"])[0]))
            limit = min(500, max(1, int(query.get("limit", ["200"])[0])))
        except ValueError:
            self.send_json({"error": "Paramètres de pagination invalides."}, 400)
            return
        files = sorted(
            (
                path for path in self.photos_dir.iterdir()
                if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
        page = files[offset:offset + limit]
        metadata_by_path = {}
        metadata_error = ""
        try:
            metadata_by_path = read_photo_metadata([path.resolve() for path in page])
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            metadata_error = str(error)
        self.send_json({
            "photos": [
                self.photo_listing(path, metadata_by_path, metadata_error)
                for path in page
            ],
            "offset": offset,
            "limit": limit,
            "total": len(files),
            "directory": str(self.photos_dir),
        })

    def photo_listing(self, path: Path, metadata_by_path: dict, metadata_error: str) -> dict:
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
            rating = self.validate_rating(data.get("rating")) if "rating" in data else None
            author = self.validate_author(data.get("author")) if "author" in data else None
            if keywords is None and rating is None and author is None:
                raise ValueError("Aucune modification fournie.")
            metadata = write_photo_metadata(
                self.photo_path(name), keywords, rating, author
            )
            _read_photo_metadata.cache_clear()
            self.send_json({"photoName": name, "metadata": metadata})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": str(error)}, 500)

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
            rating = self.validate_rating(data.get("rating")) if "rating" in data else None
            author = self.validate_author(data.get("author")) if "author" in data else None
            if keywords is None and rating is None and author is None:
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
                args=(job_id, paths, keywords, rating, author),
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

    def save_job_status(self, query: dict[str, list[str]]):
        job_id = query.get("jobId", [""])[0]
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                job = {
                    **job,
                    "updated": list(job["updated"]),
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
    ):
        try:
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {
                    executor.submit(
                        write_photo_metadata, path, keywords, rating, author
                    ): path.name
                    for path in paths
                }
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        future.result()
                        with jobs_lock:
                            jobs[job_id]["updated"].append(name)
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
