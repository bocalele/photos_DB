#!/usr/bin/env python3
"""Local browser interface for classifying selected photos with BioCLIP 2."""

from __future__ import annotations

from collections import OrderedDict
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
import time
import uuid
from urllib.parse import parse_qs, quote, urlparse

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from classify_photo import (
    TaxrefIndex,
    find_taxref_path,
    load_photo,
    prediction_details,
)
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
thumbnail_lock = threading.Lock()
jobs: OrderedDict[str, dict] = OrderedDict()
jobs_lock = threading.Lock()
classification_lock = threading.Lock()
result_cache: dict[tuple[str, int, int], list[dict]] = {}
MAX_SELECTED_PHOTOS = 500
CLASSIFICATION_METADATA_TAGS = [
    "-IPTC:Keywords",
    "-XMP-dc:Subject",
    "-XMP-lr:HierarchicalSubject",
]


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


def photo_taxonomy(metadata: dict, taxref: TaxrefIndex) -> dict:
    iptc = metadata.get("IPTC", {})
    xmp = metadata.get("XMP", {})
    keywords = metadata_values(
        iptc.get("Keywords"),
        xmp.get("dc.subject"),
        xmp.get("Subject"),
    )
    hierarchical_keywords = metadata_values(
        xmp.get("lr.hierarchicalSubject"),
        xmp.get("HierarchicalSubject"),
    )
    vernacular_taxon = next(
        (
            taxon
            for keyword in keywords
            if (taxon := taxref.resolve_vernacular(keyword)) is not None
        ),
        None,
    )
    keyword_taxon = vernacular_taxon or next(
        (
            taxon
            for keyword in keywords
            if (taxon := taxref.resolve_species(keyword)) is not None
        ),
        None,
    )
    hierarchy_taxon = next(
        (
            taxon
            for path in hierarchical_keywords
            if (taxon := taxref.resolve_hierarchical_path(path)) is not None
        ),
        None,
    )
    return {
        "keywords": keywords,
        "hierarchicalKeywords": hierarchical_keywords,
        "keywordTaxon": keyword_taxon,
        "hierarchyTaxon": hierarchy_taxon,
        "keywordPresent": vernacular_taxon is not None,
        "hierarchyPresent": hierarchy_taxon is not None,
        "metadataRead": True,
    }


def write_photo_taxon(
    path: Path, taxon: dict, validated: bool, taxref: TaxrefIndex
) -> dict:
    records = get_metadata_many([str(path)], tags=CLASSIFICATION_METADATA_TAGS)
    metadata = records.get(str(path))
    if metadata is None:
        raise RuntimeError(f"ExifTool n’a renvoyé aucune métadonnée pour {path.name}.")
    current = photo_taxonomy(metadata, taxref)
    keywords = current["keywords"]
    hierarchies = current["hierarchicalKeywords"]
    taxon_keywords = {
        value.casefold()
        for value in (taxon["scientificName"], taxon.get("vernacularName", ""))
        if value
    }
    hierarchy_path = "|".join(taxon["lineage"])

    if validated:
        keywords = [
            keyword
            for keyword in keywords
            if not (
                taxref.resolve_vernacular(keyword)
                or taxref.resolve_species(keyword)
            )
        ]
        keywords = metadata_values(
            keywords, taxon["scientificName"], taxon.get("vernacularName", "")
        )
        hierarchies = [
            hierarchy
            for hierarchy in hierarchies
            if taxref.resolve_hierarchical_path(hierarchy) is None
        ]
        hierarchies = metadata_values(hierarchies, hierarchy_path)
    else:
        keywords = [
            keyword for keyword in keywords if keyword.casefold() not in taxon_keywords
        ]
        hierarchies = [
            hierarchy
            for hierarchy in hierarchies
            if not (
                (match := taxref.resolve_hierarchical_path(hierarchy))
                and match["scientificName"] == taxon["scientificName"]
            )
        ]

    set_metadata(
        path,
        {
            "IPTC": {"Keywords": keywords or [""]},
            "XMP": {
                "Subject": keywords or [""],
                "HierarchicalSubject": hierarchies or [""],
            },
        },
    )
    updated_records = get_metadata_many([str(path)], tags=CLASSIFICATION_METADATA_TAGS)
    updated = updated_records.get(str(path))
    if updated is None:
        raise RuntimeError(f"ExifTool n’a pas pu relire les métadonnées de {path.name}.")
    return photo_taxonomy(updated, taxref)


@lru_cache(maxsize=32)
def _extract_preview(path: str, modified_time: int) -> bytes:
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


def image_content_type(body: bytes) -> str | None:
    return next(
        (mime for signature, mime in IMAGE_SIGNATURES if body.startswith(signature)),
        None,
    )


def convert_preview(path: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="faune-preview-") as directory:
        output = Path(directory) / "preview.jpg"
        try:
            result = subprocess.run(
                ["sips", "-s", "format", "jpeg", str(path), "--out", str(output)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"Conversion de l’aperçu impossible : {error}") from error
        if result.returncode != 0 or not output.is_file():
            message = result.stderr.strip() or result.stdout.strip() or "format non pris en charge"
            raise RuntimeError(f"Conversion de l’aperçu impossible pour {path.name} : {message}")
        return output.read_bytes()


def _make_preview(path: str, modified_time: int, size: int) -> bytes:
    embedded = _extract_preview(path, modified_time)
    if embedded:
        return embedded
    return convert_preview(Path(path))


@lru_cache(maxsize=32)
def cached_preview(path: str, modified_time: int, size: int) -> bytes:
    return _make_preview(path, modified_time, size)


def extract_preview(path: Path) -> bytes:
    with thumbnail_lock:
        stat = path.stat()
        return cached_preview(str(path), stat.st_mtime_ns, stat.st_size)


def initial_directory_from_preferences() -> Path:
    preferences_path = APP_DIR / "preferences.json"
    try:
        preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Path.home()
    except (OSError, json.JSONDecodeError) as error:
        print(
            f"Préférences illisibles ({preferences_path}) : {error}. "
            "Utilisation du dossier personnel.",
            flush=True,
        )
        return Path.home()

    configured_path = preferences.get("initial_directory") if isinstance(preferences, dict) else None
    if not isinstance(configured_path, str) or not configured_path.strip():
        print(
            f"Préférences invalides ({preferences_path}) : initial_directory est requis. "
            "Utilisation du dossier personnel.",
            flush=True,
        )
        return Path.home()
    initial_directory = Path(configured_path).expanduser()
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


class ClassifierHandler(BaseHTTPRequestHandler):
    photos_dir: Path
    taxref: TaxrefIndex

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}", flush=True)

    def send_body(self, body: bytes, content_type: str, status: int = 200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
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
        elif parsed.path == "/api/classify":
            self.send_job(query)
        elif parsed.path == "/api/taxa":
            self.search_taxa(query)
        else:
            self.send_error(404)

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/classify":
            self.handle_classify()
        elif route == "/api/confirm":
            self.handle_confirm()
        elif route == "/api/change-directory":
            self.handle_change_directory()
        else:
            self.send_error(404)

    def handle_classify(self):
        try:
            data = self.read_request_json()
            names = data.get("photoNames") if isinstance(data, dict) else None
            if (
                not isinstance(names, list)
                or not names
                or len(names) > MAX_SELECTED_PHOTOS
                or any(not isinstance(name, str) for name in names)
                or len(set(names)) != len(names)
            ):
                raise ValueError(f"Sélectionnez de 1 à {MAX_SELECTED_PHOTOS} photos.")
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
                "status": "En attente du modèle BioCLIP…",
                "results": {},
                "errors": {},
            }
            with jobs_lock:
                jobs[job_id] = job
                while len(jobs) > 20:
                    old_id, old_job = next(iter(jobs.items()))
                    if not old_job["complete"]:
                        break
                    jobs.pop(old_id)
            threading.Thread(
                target=self.run_classification_job,
                args=(job_id, paths),
                daemon=True,
            ).start()
            self.send_json({"jobId": job_id}, 202)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": str(error)}, 500)

    def handle_confirm(self):
        try:
            data = self.read_request_json()
            self.confirm_taxon(data)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": str(error)}, 500)

    def handle_change_directory(self):
        try:
            new_dir = self.choose_new_directory()
            if new_dir is None:
                self.send_json({"error": "Aucun dossier sélectionné."}, 400)
                return
            self.update_preferences(new_dir)
            ClassifierHandler.photos_dir = new_dir
            cached_preview.cache_clear()
            result_cache.clear()
            self.send_json({"directory": str(new_dir)})
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": str(error)}, 500)

    def choose_new_directory(self):
        initial = initial_directory_from_preferences()
        if sys.platform == "darwin":
            return choose_photos_directory(initial)
        else:
            try:
                import tkinter as tk
                from tkinter import filedialog
            except ImportError as error:
                raise RuntimeError(f"Tkinter n'est pas disponible : {error}")
            try:
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
            except tk.TclError as error:
                raise RuntimeError(f"Impossible d'ouvrir le sélecteur : {error}")
            if not selected:
                return None
            return Path(selected).expanduser().resolve()

    def update_preferences(self, new_dir):
        preferences_path = APP_DIR / "preferences.json"
        preferences = {"initial_directory": str(new_dir)}
        preferences_path.write_text(json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8")

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
            metadata_by_path = get_metadata_many(
                [str(path) for path in page],
                tags=CLASSIFICATION_METADATA_TAGS,
            )
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
        metadata = metadata_by_path.get(str(path))
        if metadata is None:
            taxonomy = {
                "keywords": [],
                "hierarchicalKeywords": [],
                "keywordTaxon": None,
                "hierarchyTaxon": None,
                "keywordPresent": False,
                "hierarchyPresent": False,
                "metadataRead": False,
            }
        else:
            taxonomy = photo_taxonomy(metadata, self.taxref)
        return {
            "name": path.name,
            "thumbnail": f"/api/thumbnail?name={quote(path.name)}",
            **taxonomy,
            "metadataError": metadata_error,
        }

    def search_taxa(self, query: dict[str, list[str]]):
        search = query.get("q", [""])[0].strip()
        if len(search) < 2:
            self.send_json({"taxa": []})
            return
        self.send_json({"taxa": self.taxref.search_species(search)})

    def confirm_taxon(self, data: dict):
        filename = data.get("photoName")
        scientific_name = data.get("scientificName")
        if not isinstance(filename, str) or self.photo_path(filename) is None:
            raise ValueError("Photo absente du dossier sélectionné.")
        if not isinstance(scientific_name, str):
            raise ValueError("Le nom scientifique est invalide.")
        validated = data.get("validated")
        if not isinstance(validated, bool):
            raise ValueError("L’état de validation est invalide.")
        taxon = self.taxref.resolve_species(scientific_name)
        if taxon is None:
            raise ValueError("Cette espèce est absente de la taxonomie TAXREF.")
        metadata = write_photo_taxon(
            self.photo_path(filename), taxon, validated, self.taxref
        )
        self.send_json({"taxon": taxon, "validated": validated, "metadata": metadata})

    def send_thumbnail(self, query: dict[str, list[str]]):
        name = query.get("name", [""])[0]
        path = self.photo_path(name)
        if path is None:
            self.send_error(404)
            return
        try:
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif"}:
                body = path.read_bytes()
            else:
                body = extract_preview(path)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": f"Aperçu indisponible pour {name} : {error}"}, 500)
            return
        if not body:
            self.send_json({"error": f"Aucun aperçu intégré disponible pour {name}."}, 404)
            return
        content_type = image_content_type(body) or mimetypes.guess_type(name)[0]
        if content_type is None or not content_type.startswith("image/"):
            self.send_json({"error": f"Format d’aperçu non reconnu pour {name}."}, 415)
            return
        self.send_body(body, content_type)

    def send_job(self, query: dict[str, list[str]]):
        job_id = query.get("jobId", [""])[0]
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                job = {
                    **job,
                    "results": dict(job["results"]),
                    "errors": dict(job["errors"]),
                }
        if job is None:
            self.send_json({"error": "Cette tâche de classification est introuvable."}, 404)
            return
        self.send_json({"jobId": job_id, **job})

    def run_classification_job(self, job_id: str, paths: list[Path]):
        with classification_lock:
            try:
                print("Chargement du modèle BioCLIP 2…", flush=True)
                from bioclip.predict import Rank, TreeOfLifeClassifier

                classifier = TreeOfLifeClassifier(device="mps")
                self.update_job(job_id, status="Modèle prêt ; classification en cours.")
            except Exception as error:
                for path in paths:
                    self.update_job(job_id, path.name, error=str(error))
                    with jobs_lock:
                        jobs[job_id]["done"] += 1
                self.finish_job(job_id)
                return

            for path in paths:
                try:
                    snapshot = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
                    result = result_cache.get(snapshot)
                    if result is None:
                        with tempfile.TemporaryDirectory(prefix="bioclip-") as temp_dir:
                            image = load_photo(path, Path(temp_dir))
                            predictions = classifier.predict(
                                images=[image],
                                rank=Rank.SPECIES,
                                k=5,
                                batch_size=1,
                            )
                        result = [
                            prediction_details(prediction, self.taxref)
                            for prediction in predictions
                        ]
                        result_cache[snapshot] = result
                    self.update_job(job_id, path.name, result=result)
                except Exception as error:
                    self.update_job(job_id, path.name, error=str(error))
                with jobs_lock:
                    job = jobs[job_id]
                    job["done"] += 1
                    job["status"] = f"Classification : {job['done']}/{job['total']} photo(s)."
            self.finish_job(job_id)

    @staticmethod
    def update_job(
        job_id: str,
        name: str | None = None,
        result: list[dict] | None = None,
        error: str | None = None,
        status: str | None = None,
    ):
        with jobs_lock:
            job = jobs[job_id]
            if name is not None:
                if error is not None:
                    job["errors"][name] = error
                elif result is not None:
                    job["results"][name] = result
            if status is not None:
                job["status"] = status

    @staticmethod
    def finish_job(job_id: str):
        with jobs_lock:
            job = jobs[job_id]
            job["complete"] = True
            job["status"] = (
                f"Terminé : {job['done']}/{job['total']} photo(s), "
                f"{len(job['errors'])} erreur(s)."
            )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Interface locale pour classer des photos avec BioCLIP 2."
    )
    parser.add_argument("photos_dir", nargs="?", help="Dossier contenant les photos.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--taxref",
        type=Path,
        help="Fichier TAXREFv*.txt (détecté automatiquement dans faune/TAXREF).",
    )
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
    try:
        taxref_path = args.taxref or find_taxref_path(APP_DIR / "TAXREF")
        ClassifierHandler.taxref = TaxrefIndex(taxref_path)
    except (FileNotFoundError, OSError, ValueError) as error:
        parser.error(str(error))
    ClassifierHandler.photos_dir = photos_dir

    try:
        server = ThreadingHTTPServer((args.host, args.port), ClassifierHandler)
    except OSError as error:
        parser.error(f"Impossible de démarrer le serveur : {error}")
    print(f"Dossier de photos : {photos_dir}", flush=True)
    print(f"TAXREF : {taxref_path}", flush=True)
    print(f"Interface : http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
