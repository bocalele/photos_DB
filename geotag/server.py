from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import shutil
import subprocess
import sys
import threading
import time
import uuid
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from geopy.exc import GeocoderServiceError
from geopy.geocoders import Nominatim


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools.getMetaData import getGPS, get_metadata_many
from tools.setMetaData import set_metadata


PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".psd",
    ".nef", ".nrw", ".cr2", ".cr3", ".arw", ".dng", ".raf",
    ".rw2", ".orf", ".pef",
}
IMAGE_MIME_TYPES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",
}
PAGE_SIZE = 200
METADATA_WORKERS = 10
location_jobs = {}
location_jobs_lock = threading.Lock()
preview_extraction_lock = threading.Lock()
metadata_read_lock = threading.Lock()


def location_from_address(address):
    sub_location = next(
        (
            address[key]
            for key in ("place", "neighbourhood", "quarter", "hamlet", "suburb", "road")
            if address.get(key)
        ),
        "",
    )
    return {"subLocation": sub_location, "country": address.get("country", "")}


@lru_cache(maxsize=32)
def _extract_preview(path, modified_time):
    executable = shutil.which("exiftool")
    if executable is None:
        raise RuntimeError("ExifTool est requis pour afficher les vignettes des fichiers PSD et RAW.")
    for tag in ("PhotoshopThumbnail", "PreviewImage", "JpgFromRaw", "ThumbnailImage"):
        result = subprocess.run(
            [executable, "-b", f"-{tag}", str(path)],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    return b""


def extract_preview(path, modified_time):
    with preview_extraction_lock:
        return _extract_preview(path, modified_time)


@lru_cache(maxsize=4)
def _read_photo_metadata(snapshot):
    return get_metadata_many([path for path, _, _ in snapshot])


def read_photo_metadata(paths):
    snapshot = tuple(
        (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        for path in paths
    )
    with metadata_read_lock:
        cached_reads = _read_photo_metadata.cache_info().hits
        metadata = _read_photo_metadata(snapshot)
        if _read_photo_metadata.cache_info().hits > cached_reads:
            print("Métadonnées réutilisées depuis le cache.", flush=True)
        return metadata


class GeotagHandler(BaseHTTPRequestHandler):
    photos_dir = None

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")

    def send_body(self, body, content_type, status=200, cache_control=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            print(
                f"Client {self.address_string()} déconnecté avant la fin de l’envoi.",
                flush=True,
            )

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_body(body, "application/json; charset=utf-8", status)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)
        if parsed_url.path == "/api/photos":
            self.list_photos(query)
        elif parsed_url.path == "/api/thumbnail":
            self.send_thumbnail(query)
        elif parsed_url.path == "/api/search":
            self.search_places(query)
        elif parsed_url.path == "/api/reverse":
            self.reverse_location(query)
        elif parsed_url.path == "/api/save-location":
            self.location_job_status(query)
        elif parsed_url.path == "/":
            self.send_static("index.html", "text/html; charset=utf-8")
        elif parsed_url.path in ("/app.js", "/styles.css"):
            content_type = "application/javascript; charset=utf-8" if parsed_url.path.endswith(".js") else "text/css; charset=utf-8"
            self.send_static(parsed_url.path[1:], content_type)
        else:
            self.send_error(404)

    def do_POST(self):
        route = urlparse(self.path).path
        if route != "/api/save-location":
            self.send_error(404)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 1_000_000:
                raise ValueError("La requête est vide ou trop volumineuse.")
            data = json.loads(self.rfile.read(content_length))
            names, latitude, longitude, sub_location, country = self.validate_payload(data)
            job_id = self.start_location_job(
                names, latitude, longitude, sub_location, country
            )
            self.send_json({"jobId": job_id}, 202)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": str(error)}, 500)

    def start_location_job(self, names, latitude, longitude, sub_location, country):
        job_id = uuid.uuid4().hex
        job = {
            "total": len(names),
            "done": 0,
            "updated": [],
            "errors": [],
            "complete": False,
        }
        with location_jobs_lock:
            completed_jobs = [
                key for key, value in location_jobs.items() if value["complete"]
            ]
            while len(location_jobs) >= 20 and completed_jobs:
                location_jobs.pop(completed_jobs.pop(0), None)
            location_jobs[job_id] = job
        threading.Thread(
            target=self.run_location_job,
            args=(job_id, names, latitude, longitude, sub_location, country),
            daemon=True,
        ).start()
        return job_id

    def location_job_status(self, query):
        job_id = query.get("jobId", [""])[0]
        with location_jobs_lock:
            job = location_jobs.get(job_id)
            if job is not None:
                job = {**job, "updated": list(job["updated"]), "errors": list(job["errors"])}
        if job is None:
            self.send_json({"error": "Cette sauvegarde est introuvable."}, 404)
            return
        self.send_json({"jobId": job_id, **job})

    def run_location_job(self, job_id, names, latitude, longitude, sub_location, country):
        with ThreadPoolExecutor(max_workers=METADATA_WORKERS) as executor:
            futures = {
                executor.submit(
                    self.write_photo_location,
                    name,
                    latitude,
                    longitude,
                    sub_location,
                    country,
                ): name
                for name in names
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                    with location_jobs_lock:
                        location_jobs[job_id]["updated"].append(name)
                except Exception as error:
                    with location_jobs_lock:
                        location_jobs[job_id]["errors"].append({
                            "filename": name,
                            "message": str(error),
                        })
                finally:
                    with location_jobs_lock:
                        location_jobs[job_id]["done"] += 1
        _read_photo_metadata.cache_clear()
        with location_jobs_lock:
            location_jobs[job_id]["complete"] = True

    def write_photo_location(self, filename, latitude, longitude, sub_location, country):
        path = self.photo_path(filename)
        if path is None:
            raise ValueError("Fichier absent ou chemin invalide.")
        set_metadata(
            path,
            {
                "EXIF": {
                    "GPSLatitude": abs(latitude),
                    "GPSLatitudeRef": "S" if latitude < 0 else "N",
                    "GPSLongitude": abs(longitude),
                    "GPSLongitudeRef": "W" if longitude < 0 else "E",
                    "GPSVersionID": "2 3 0 0",
                },
                "IPTC": {
                    "Sub-location": sub_location,
                    "Country-PrimaryLocationName": country,
                },
                "XMP": {
                    "Location": sub_location,
                    "Country": country,
                },
            },
        )

    def send_static(self, filename, content_type):
        path = APP_DIR / filename
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_body(body, content_type, cache_control="no-cache")

    def list_photos(self, query):
        try:
            offset = max(0, int(query.get("offset", ["0"])[0]))
            limit = min(500, max(1, int(query.get("limit", [str(PAGE_SIZE)])[0])))
        except ValueError:
            self.send_json({"error": "Paramètres de pagination invalides."}, 400)
            return
        files = sorted(
            (
                path
                for path in self.photos_dir.iterdir()
                if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
        page = files[offset : offset + limit]
        resolved_paths = [path.resolve() for path in page]
        metadata_error = None
        metadata_started = time.monotonic()
        print(f"Lecture des métadonnées de {len(resolved_paths)} photo(s)…", flush=True)
        try:
            metadata_by_path = read_photo_metadata(resolved_paths)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            metadata_by_path = {}
            metadata_error = str(error)
        print(
            f"Lecture des métadonnées terminée en {time.monotonic() - metadata_started:.1f} s.",
            flush=True,
        )
        self.send_json(
            {
                "photos": [
                    {
                        "name": path.name,
                        "thumbnail": f"/api/thumbnail?name={quote(path.name)}",
                        **self.metadata_presence(metadata_by_path.get(str(path.resolve()))),
                    }
                    for path in page
                ],
                "metadataError": metadata_error,
                "total": len(files),
                "offset": offset,
                "limit": limit,
            }
        )

    @staticmethod
    def metadata_presence(categories):
        if categories is None:
            return {
                "gpsPresent": None,
                "locationPresent": None,
                "metadataRead": False,
                "subLocation": "",
                "country": "",
            }
        gps = getGPS(categories.get("EXIF", {}))
        iptc = categories.get("IPTC", {})
        xmp = categories.get("XMP", {})
        sub_location = (
            iptc.get("Sub-location")
            or iptc.get("Application2.SubLocation")
            or xmp.get("Location")
            or ""
        )
        country = (
            iptc.get("Country-PrimaryLocationName")
            or iptc.get("Application2.CountryName")
            or xmp.get("Country")
            or ""
        )
        gps_present = (
            gps.get("GPSLatitude") is not None
            and gps.get("GPSLongitude") is not None
            and gps.get("GPSLatitudeRef") in ("N", "S")
            and gps.get("GPSLongitudeRef") in ("E", "W")
        )
        return {
            "gpsPresent": gps_present,
            "locationPresent": bool(sub_location and country),
            "metadataRead": True,
            "subLocation": str(sub_location),
            "country": str(country),
        }

    def photo_path(self, name):
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
            or path.suffix.lower() not in PHOTO_EXTENSIONS
        ):
            return None
        return resolved_path

    def send_thumbnail(self, query):
        name = query.get("name", [""])[0]
        path = self.photo_path(name)
        if path is None:
            self.send_error(404)
            return
        try:
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif"}:
                body = path.read_bytes()
            else:
                preview_started = time.monotonic()
                print(f"Extraction de l’aperçu : {name}", flush=True)
                body = extract_preview(str(path), path.stat().st_mtime_ns)
                print(
                    f"Aperçu traité en {time.monotonic() - preview_started:.1f} s : {name}",
                    flush=True,
                )
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            self.send_json({"error": f"Impossible de lire l’aperçu de {name} : {error}"}, 500)
            return
        if not body:
            self.send_error(404, "Aucun aperçu intégré dans ce fichier.")
            return
        content_type = next(
            (mime for signature, mime in IMAGE_MIME_TYPES.items() if body.startswith(signature)),
            "application/octet-stream",
        )
        self.send_body(body, content_type, cache_control="public, max-age=3600")

    def search_places(self, query):
        place = query.get("q", [""])[0].strip()
        if len(place) < 3:
            self.send_json({"places": []})
            return
        try:
            results = Nominatim(user_agent="photos_geotag_editor", timeout=10).geocode(
                place,
                exactly_one=False,
                limit=6,
                addressdetails=True,
                language="fr",
            )
        except GeocoderServiceError as error:
            self.send_json({"error": f"Le service de recherche est indisponible : {error}"}, 502)
            return
        self.send_json(
            {
                "places": [
                    {
                        "label": result.address,
                        "latitude": result.latitude,
                        "longitude": result.longitude,
                        "bounds": (
                            [
                                [float(result.raw["boundingbox"][0]), float(result.raw["boundingbox"][2])],
                                [float(result.raw["boundingbox"][1]), float(result.raw["boundingbox"][3])],
                            ]
                            if result.raw.get("boundingbox")
                            else None
                        ),
                        **location_from_address(result.raw.get("address", {})),
                    }
                    for result in results or []
                ]
            }
        )

    def reverse_location(self, query):
        try:
            latitude = float(query["latitude"][0])
            longitude = float(query["longitude"][0])
            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                raise ValueError
        except (KeyError, ValueError):
            self.send_json({"error": "Coordonnées GPS invalides."}, 400)
            return
        try:
            result = Nominatim(user_agent="photos_geotag_editor", timeout=10).reverse(
                (latitude, longitude),
                exactly_one=True,
                addressdetails=True,
                language="fr",
            )
        except GeocoderServiceError as error:
            self.send_json({"error": f"Le service de géolocalisation est indisponible : {error}"}, 502)
            return
        if result is None:
            self.send_json({"error": "Aucun lieu trouvé pour ces coordonnées."}, 404)
            return
        self.send_json(location_from_address(result.raw.get("address", {})))

    def validate_payload(self, data):
        if not isinstance(data, dict):
            raise ValueError("Les données de localisation sont invalides.")
        try:
            latitude = float(data["latitude"])
            longitude = float(data["longitude"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Latitude et longitude sont obligatoires.") from error
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise ValueError("Coordonnées GPS hors limites.")
        names = data.get("photoNames")
        if not isinstance(names, list) or not names or len(names) > 500:
            raise ValueError("Sélectionnez de 1 à 500 photos.")
        if any(not isinstance(name, str) for name in names):
            raise ValueError("La sélection contient un fichier absent ou invalide.")
        names = list(dict.fromkeys(names))
        if any(self.photo_path(name) is None for name in names):
            raise ValueError("La sélection contient un fichier absent ou invalide.")
        sub_location = str(data.get("subLocation") or "").strip()
        country = str(data.get("country") or "").strip()
        if any(character in sub_location + country for character in ("\0", "\n", "\r")):
            raise ValueError("Les champs de lieu ne peuvent pas contenir de retours à la ligne.")
        return names, latitude, longitude, sub_location, country


def initial_directory_from_preferences():
    preferences_path = APP_DIR / "preferences.json"
    try:
        preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Path.cwd()
    except (OSError, json.JSONDecodeError) as error:
        print(f"Préférences illisibles ({preferences_path}) : {error}. Utilisation du dossier courant.")
        return Path.cwd()

    if not isinstance(preferences, dict):
        print(f"Préférences invalides ({preferences_path}) : un objet JSON est attendu. Utilisation du dossier courant.")
        return Path.cwd()
    configured_path = preferences.get("initial_directory")
    if not isinstance(configured_path, str) or not configured_path.strip():
        return Path.cwd()
    initial_directory = Path(configured_path).expanduser()
    if not initial_directory.is_dir():
        print(
            f"Le dossier initial configuré n’existe pas ({initial_directory}). "
            "Utilisation du dossier courant."
        )
        return Path.cwd()
    return initial_directory.resolve()


def choose_photos_directory(initial_directory):
    script = """
on run argv
    set initialFolder to POSIX file (item 1 of argv) as alias
    set chosenFolder to choose folder with prompt "Choisir le dossier contenant les photos" default location initialFolder
    return POSIX path of chosenFolder
end run
"""
    print("Ouverture du sélecteur de dossier macOS…", flush=True)
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script, str(initial_directory)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise RuntimeError(f"Impossible de démarrer le sélecteur de dossier macOS : {error}") from error

    if result.returncode:
        if "(-128)" in result.stderr:
            return None
        message = result.stderr.strip() or result.stdout.strip() or "Erreur AppleScript."
        raise RuntimeError(f"Impossible de choisir le dossier de photos : {message}")
    selected_directory = result.stdout.strip()
    if not selected_directory:
        raise RuntimeError("Le sélecteur macOS n’a renvoyé aucun dossier.")
    print("Sélecteur de dossier fermé ; initialisation du serveur…", flush=True)
    return Path(selected_directory)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Interface web indépendante pour géolocaliser les photos d’un dossier."
    )
    parser.add_argument(
        "photos_dir",
        nargs="?",
        default=None,
        help="Dossier contenant les photos (si omis, une fenêtre de sélection s’ouvre).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Adresse d’écoute (défaut : 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="Port HTTP (défaut : 8000).")
    args = parser.parse_args()
    if args.photos_dir is None:
        if sys.platform == "darwin":
            try:
                selected_directory = choose_photos_directory(
                    initial_directory_from_preferences()
                )
            except RuntimeError as error:
                parser.error(str(error))
            if selected_directory is None:
                print("Aucun dossier sélectionné ; arrêt du serveur.", flush=True)
                return
            photos_dir = selected_directory.expanduser().resolve()
        else:
            try:
                import tkinter as tk
                from tkinter import filedialog
            except ImportError as error:
                parser.error(
                    "Tkinter n’est pas disponible pour ouvrir la fenêtre de choix du dossier. "
                    f"Passez le dossier en argument de ligne de commande ({error})."
                )
            try:
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                try:
                    selected_directory = filedialog.askdirectory(
                        title="Choisir le dossier contenant les photos",
                        initialdir=str(initial_directory_from_preferences()),
                        mustexist=True,
                    )
                finally:
                    root.destroy()
            except tk.TclError as error:
                parser.error(
                    "Impossible d’ouvrir la fenêtre de choix du dossier. "
                    f"Passez le dossier en argument de ligne de commande ({error})."
                )
            if not selected_directory:
                print("Aucun dossier sélectionné ; arrêt du serveur.")
                return
            photos_dir = Path(selected_directory).expanduser().resolve()
    else:
        photos_dir = Path(args.photos_dir).expanduser().resolve()
    if not photos_dir.is_dir():
        parser.error(f"Le dossier de photos n’existe pas : {photos_dir}")
    GeotagHandler.photos_dir = photos_dir
    server = ThreadingHTTPServer((args.host, args.port), GeotagHandler)
    print(f"Dossier de photos : {photos_dir}", flush=True)
    print(f"Interface : http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
