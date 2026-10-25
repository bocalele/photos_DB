#!/usr/bin/env python3
"""Application de sélection de photos (bibliothèque standard uniquement).

Le serveur lit `photos.db` (schéma v4) via `dataBase.photos_db`, sert les
vignettes générées à la demande et expose une API JSON utilisée par
`select/app.js`. Il peut aussi produire un album HTML autonome (aperçu et
export ZIP) via `album_export.py`.

    python select/server.py /Volumes/WD/PHOTOS/2026/Angleterre --port 8765

Aucune dépendance Flask n'est nécessaire.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from contextlib import closing
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
for candidate in (PROJECT_DIR, BASE_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import album_export  # noqa: E402
from dataBase import photos_db  # noqa: E402
from tools import environment  # noqa: E402

DB_PATH = environment.photos_db()
PHOTOS_ROOT = environment.photos_root()
THUMB_DIR = environment.previews_dir() / "selector"
CURRENT_DIRECTORY: Path | None = None
SIPS = shutil.which("sips")
QLMANAGE = shutil.which("qlmanage")


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _int_arg(value, default=None):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_arg(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _photo_record(conn, photo_id: int):
    return conn.execute(
        "SELECT id, relpath, mtime_ns, file_size FROM photos WHERE id = ?",
        (photo_id,),
    ).fetchone()


def _thumb_key(row) -> str:
    value = f"{row['relpath']}:{row['mtime_ns'] or 0}:{row['file_size'] or 0}"
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:24] + ".jpg"


def _placeholder_svg() -> bytes:
    return b"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="420" viewBox="0 0 640 420">
<rect width="640" height="420" fill="#1b1d2b"/>
<text x="320" y="205" fill="#8f96b3" font-family="sans-serif" font-size="22" text-anchor="middle">Apercu indisponible</text>
<text x="320" y="240" fill="#646b88" font-family="sans-serif" font-size="14" text-anchor="middle">Ce format ne peut pas etre converti</text>
</svg>"""


def _make_image(source: Path, destination: Path, max_size: int = 720) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        return True
    fd, temp_name = tempfile.mkstemp(prefix=".image-", suffix=".jpg", dir=destination.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        if SIPS:
            result = subprocess.run(
                [SIPS, "-Z", str(max_size), "-s", "format", "jpeg", str(source), "--out", str(temp)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0 and temp.is_file() and temp.stat().st_size:
                os.replace(temp, destination)
                return True
        if QLMANAGE:
            temp.unlink(missing_ok=True)
            temp_dir = temp.with_suffix("")
            temp_dir.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                [QLMANAGE, "-t", "-s", str(max_size), "-o", str(temp_dir), str(source)],
                capture_output=True, text=True, timeout=120,
            )
            candidates = list(temp_dir.glob("*"))
            if result.returncode == 0 and candidates:
                candidate = candidates[0]
                if candidate.suffix.lower() in {".jpg", ".jpeg"}:
                    os.replace(candidate, destination)
                    return True
                if SIPS:
                    result = subprocess.run(
                        [SIPS, "-s", "format", "jpeg", str(candidate), "--out", str(temp)],
                        capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode == 0 and temp.is_file() and temp.stat().st_size:
                        os.replace(temp, destination)
                        return True
        return False
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        temp.unlink(missing_ok=True)
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)


def _make_thumbnail(source: Path, destination: Path) -> bool:
    return _make_image(source, destination, 720)


def _safe_album_name(title: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9À-ÖØ-öø-ÿ]+", "-", title or "").strip("-").lower()
    return cleaned or "album"


def _album_image(source: Path | None, images_dir: Path, index: int) -> str:
    """Prépare une image web pour l'album et retourne son chemin relatif."""
    images_dir.mkdir(parents=True, exist_ok=True)
    destination = images_dir / f"{index:04d}.jpg"
    if source is not None and _make_image(source, destination, 1600):
        return f"images/{destination.name}"
    if source is not None and source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        suffix = ".jpg" if source.suffix.lower() == ".jpeg" else source.suffix.lower()
        copy_path = images_dir / f"{index:04d}{suffix}"
        try:
            shutil.copy2(source, copy_path)
            return f"images/{copy_path.name}"
        except OSError:
            pass
    placeholder = images_dir / f"{index:04d}-indisponible.svg"
    placeholder.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800">'
        '<rect width="1200" height="800" fill="#171b29"/>'
        '<text x="600" y="390" fill="#a3aac0" font-family="sans-serif" font-size="30" '
        'text-anchor="middle">Photo indisponible</text>'
        '<text x="600" y="432" fill="#646b88" font-family="sans-serif" font-size="20" '
        'text-anchor="middle">Le fichier source n’a pas pu être converti</text></svg>',
        encoding="utf-8",
    )
    return f"images/{placeholder.name}"


class Handler(BaseHTTPRequestHandler):
    server_version = "PhotosSelector/2.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    # ------------------------------------------------------------------ #
    # Réponses
    # ------------------------------------------------------------------ #
    def send_json(self, payload, status=HTTPStatus.OK):
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str, cache: bool = False, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if cache:
            self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str | None = None, cache: bool = False):
        if not path.is_file():
            self.send_json({"error": "Fichier introuvable."}, HTTPStatus.NOT_FOUND)
            return
        content_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            body = path.read_bytes()
        except OSError as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_bytes(body, content_type, cache=cache)

    def send_zip(self, path: Path, download_name: str):
        if not path.is_file():
            self.send_json({"error": "Archive introuvable."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(size))
        self.send_header(
            "Content-Disposition",
            "attachment; filename=\"album.zip\"; "
            f"filename*=UTF-8''{quote(download_name)}",
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with path.open("rb") as archive:
            shutil.copyfileobj(archive, self.wfile, 1024 * 1024)

    def read_json(self) -> dict:
        length = _int_arg(self.headers.get("Content-Length"), 0) or 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("JSON invalide.") from error
        if not isinstance(data, dict):
            raise ValueError("Le corps JSON doit être un objet.")
        return data

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ------------------------------------------------------------------ #
    # Routage
    # ------------------------------------------------------------------ #
    def do_GET(self):
        route = urlparse(self.path).path
        try:
            if route == "/":
                self.send_file(BASE_DIR / "index.html", "text/html; charset=utf-8")
            elif route in {"/app.js", "/styles.css"}:
                self.send_file(BASE_DIR / route.lstrip("/"), cache=False)
            elif route.startswith("/api/"):
                self.handle_api_get(route)
            else:
                relative = unquote(route).lstrip("/")
                candidate = (BASE_DIR / relative).resolve()
                if candidate.is_file() and BASE_DIR in candidate.parents:
                    self.send_file(candidate, cache=True)
                else:
                    self.send_json({"error": "Page introuvable."}, HTTPStatus.NOT_FOUND)
        except KeyError as error:
            self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except BrokenPipeError:
            return
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/filter":
                self.handle_filter()
            elif route == "/api/directory":
                self.handle_directory()
            elif route == "/api/album/preview":
                self.handle_album_preview()
            elif route == "/api/album/export":
                self.handle_album_export()
            else:
                self.send_json({"error": "Route inconnue."}, HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except KeyError as error:
            self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except BrokenPipeError:
            return
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_api_get(self, route: str):
        if route == "/api/health":
            self.api_health()
        elif route == "/api/datasets":
            self.api_datasets()
        elif route == "/api/family":
            self.api_family()
        elif route == "/api/thesaurus/fauna":
            self.api_fauna()
        elif route == "/api/locations":
            self.api_locations()
        elif route == "/api/keywords":
            self.api_keywords()
        elif route == "/api/technical":
            self.api_technical()
        elif route == "/api/album/options":
            self.send_json(album_export.options_payload())
        elif route.startswith("/api/photos/") and route.endswith("/thumbnail"):
            photo_id = _int_arg(route.split("/")[3])
            if photo_id is None:
                raise ValueError("Photo invalide.")
            self.api_thumbnail(photo_id)
        elif route.startswith("/api/photos/") and route.endswith("/file"):
            photo_id = _int_arg(route.split("/")[3])
            if photo_id is None:
                raise ValueError("Photo invalide.")
            self.api_photo_file(photo_id)
        else:
            self.send_json({"error": "Route inconnue."}, HTTPStatus.NOT_FOUND)

    # ------------------------------------------------------------------ #
    # API
    # ------------------------------------------------------------------ #
    def api_health(self):
        payload = {
            "database": str(DB_PATH),
            "databaseExists": DB_PATH.is_file(),
            "photosRoot": str(PHOTOS_ROOT),
            "directory": str(CURRENT_DIRECTORY or PHOTOS_ROOT),
        }
        if DB_PATH.is_file():
            with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
                payload["tables"] = {
                    table: photos_db.row_count(conn, table)
                    for table in (
                        "photos", "people", "photo_people", "keywords",
                        "photo_keywords", "taxa", "photo_taxa", "locations",
                    )
                }
                payload["schemaVersion"] = photos_db.get_meta(conn, "schema_version")
        self.send_json(payload)

    def api_datasets(self):
        thesaurus_list = []
        if DB_PATH.is_file():
            with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
                if photos_db.row_count(conn, "taxa"):
                    thesaurus_list.append({
                        "id": "fauna",
                        "name": "Faune",
                        "description": "Classe › ordre › espèce",
                    })
        self.send_json({
            "thesaurusList": thesaurus_list,
            "defaultThesaurus": "family",
            "thesaurusConfig": {
                "expandAllEnabled": True,
                "genSpacing": 150,
                "nodeRadius": 16,
                "labelGenerations": True,
            },
        })

    def api_family(self):
        with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
            payload = photos_db.family_payload(conn)
        self.send_json({
            "people": payload["nodes"],
            "parentsOf": payload["parentsOf"],
            "couples": payload["couples"],
            "defaultSelf": payload["defaultSelf"],
            "photoCounts": payload["photoCounts"],
        })

    def api_fauna(self):
        with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
            self.send_json(photos_db.taxa_payload(conn))

    def api_locations(self):
        with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
            self.send_json(photos_db.locations_payload(conn))

    def api_keywords(self):
        with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
            self.send_json({"keywords": photos_db.keywords_with_counts(conn)})

    def api_technical(self):
        with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
            self.send_json(photos_db.technical_facets(conn))

    def handle_directory(self):
        global CURRENT_DIRECTORY
        data = self.read_json()
        directory = Path(str(data.get("directory") or "")).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError(f"Dossier introuvable : {directory}")
        CURRENT_DIRECTORY = directory
        self.send_json({"directory": str(directory)})

    def handle_filter(self):
        data = self.read_json()
        with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
            result = photos_db.search_photos(
                conn,
                mode=data.get("mode"),
                dataset=data.get("dataset") or "family",
                selections=data.get("selections") or [],
                min_rating=_int_arg(data.get("min_rating"), 0) or 0,
                year_min=_int_arg(data.get("year_min")),
                year_max=_int_arg(data.get("year_max")),
                model=(data.get("model") or None),
                lens=(data.get("lens") or None),
                iso_min=_int_arg(data.get("iso_min")),
                iso_max=_int_arg(data.get("iso_max")),
                focal_min=_float_arg(data.get("focal_min")),
                focal_max=_float_arg(data.get("focal_max")),
                aperture_min=_float_arg(data.get("aperture_min")),
                aperture_max=_float_arg(data.get("aperture_max")),
                shutter_min=_float_arg(data.get("shutter_min")),
                shutter_max=_float_arg(data.get("shutter_max")),
                correction_min=_float_arg(data.get("correction_min")),
                correction_max=_float_arg(data.get("correction_max")),
                limit=_int_arg(data.get("limit"), 500) or 500,
                offset=_int_arg(data.get("offset"), 0) or 0,
            )
        self.send_json(result)

    def _album_records(self, photo_ids: list[int]) -> list[dict]:
        if not photo_ids:
            return []
        with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
            return photos_db.photos_by_ids(conn, photo_ids)

    def handle_album_preview(self):
        data = self.read_json()
        config = album_export.normalize_config(data)
        records = self._album_records(config["photo_ids"])
        host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
        base_url = f"http://{host}"
        photos = [
            album_export.album_item(record, f"{base_url}/api/photos/{record['id']}/thumbnail")
            for record in records
        ]
        self.send_json({
            "html": album_export.render_album_html(config, photos),
            "count": len(photos),
        })

    def handle_album_export(self):
        data = self.read_json()
        config = album_export.normalize_config(data)
        if not config["photo_ids"]:
            raise ValueError("Sélectionnez au moins une photo pour créer un album.")
        records = self._album_records(config["photo_ids"])
        if not records:
            raise ValueError("Aucune photo valide dans cette sélection.")

        temp_dir = Path(tempfile.mkdtemp(prefix="photos-album-"))
        zip_path: Path | None = None
        try:
            images_dir = temp_dir / "images"
            photos = []
            for index, record in enumerate(records, start=1):
                source = (PHOTOS_ROOT / str(record["relpath"])).resolve()
                try:
                    source.relative_to(PHOTOS_ROOT.resolve())
                except ValueError:
                    source = None
                if source is not None and not source.is_file():
                    source = None
                photos.append(
                    album_export.album_item(record, _album_image(source, images_dir, index))
                )

            index_path = temp_dir / "index.html"
            index_path.write_text(
                album_export.render_album_html(config, photos),
                encoding="utf-8",
            )

            fd, archive_name = tempfile.mkstemp(prefix="photos-album-", suffix=".zip")
            os.close(fd)
            zip_path = Path(archive_name)
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(index_path, "index.html", compress_type=zipfile.ZIP_DEFLATED)
                for image in sorted(images_dir.glob("*")):
                    archive.write(image, f"images/{image.name}", compress_type=zipfile.ZIP_STORED)

            download_name = f"{_safe_album_name(config['title'])}.zip"
            self.send_zip(zip_path, download_name)
        finally:
            if zip_path is not None:
                zip_path.unlink(missing_ok=True)
            shutil.rmtree(temp_dir, ignore_errors=True)

    def api_thumbnail(self, photo_id: int):
        with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
            row = _photo_record(conn, photo_id)
        if row is None:
            self.send_json({"error": "Photo inconnue."}, HTTPStatus.NOT_FOUND)
            return
        source = (PHOTOS_ROOT / row["relpath"]).resolve()
        try:
            source.relative_to(PHOTOS_ROOT.resolve())
        except ValueError:
            self.send_json({"error": "Chemin invalide."}, HTTPStatus.FORBIDDEN)
            return
        if not source.is_file():
            self.send_bytes(_placeholder_svg(), "image/svg+xml")
            return
        destination = THUMB_DIR / _thumb_key(row)
        if not _make_thumbnail(source, destination):
            if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                self.send_file(source, cache=True)
            else:
                self.send_bytes(_placeholder_svg(), "image/svg+xml")
            return
        self.send_file(destination, "image/jpeg", cache=True)

    def api_photo_file(self, photo_id: int):
        with closing(photos_db.connect(DB_PATH, readonly=True)) as conn:
            row = _photo_record(conn, photo_id)
        if row is None:
            self.send_json({"error": "Photo inconnue."}, HTTPStatus.NOT_FOUND)
            return
        source = (PHOTOS_ROOT / row["relpath"]).resolve()
        try:
            source.relative_to(PHOTOS_ROOT.resolve())
        except ValueError:
            self.send_json({"error": "Chemin invalide."}, HTTPStatus.FORBIDDEN)
            return
        self.send_file(source, cache=True)


class SelectorServer(ThreadingHTTPServer):
    daemon_threads = True


def main() -> int:
    import argparse
    global CURRENT_DIRECTORY
    parser = argparse.ArgumentParser(description="Sélection de photos et albums HTML")
    parser.add_argument("directory", nargs="?", help="Dossier de photos courant.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.directory:
        candidate = Path(args.directory).expanduser().resolve()
        if candidate.is_dir():
            CURRENT_DIRECTORY = candidate
    if not DB_PATH.is_file():
        print(f"Base introuvable : {DB_PATH}", file=sys.stderr)
        print("Créez-la avec : python -m dataBase.sync reset", file=sys.stderr)
        print("Puis : python -m dataBase.sync update <dossier>", file=sys.stderr)
        return 1
    server = SelectorServer((args.host, args.port), Handler)
    print(f"Sélection : http://{args.host}:{args.port}/")
    print(f"Base       : {DB_PATH}")
    print(f"Photos     : {PHOTOS_ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
