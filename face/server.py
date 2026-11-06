#!/usr/bin/env python3
"""Interface web locale pour identifier les personnes sur les photos.

L'interface montre les photos d'un dossier, détecte les visages, propose une
identité pour chacun (galerie construite par `reference.py`), et écrit dans les
métadonnées XMP de la photo les visages validés — régions MWG et mots-clés,
au format Lightroom.

Usage :
    pixi run python server.py                 # ouvre le sélecteur de dossier
    pixi run python server.py "/chemin/vers/le/dossier-de-photos"
"""

from __future__ import annotations

import json
import math
import mimetypes
import subprocess
import sys
import threading
import time
import uuid
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import cv2
import numpy as np

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import face_db, photos_db  # noqa: E402
import index  # noqa: E402
import metadata  # noqa: E402
from tools import preferences  # noqa: E402

PHOTO_ROOT = face_db.DEFAULT_PHOTO_ROOT
PAGE_SIZE = 200
PREVIEW_SIZE = 1600
# Incrémenter à chaque changement de la façon de rendre les images : cela change
# l'URL et invalide le cache du navigateur (qui garde les JPEG 24 h).
IMAGE_VERSION = "3"
# Les détecteurs peuvent renvoyer des boîtes qui dépassent légèrement l'image.
# On les ramène juste à l'intérieur (quelques pixels de marge) pour qu'elles
# restent sélectionnables et validables dans l'interface.
BOX_INSET = 0.002
BOX_MIN_SIZE = 0.004


def clamp_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = (float(value) for value in bbox)
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
        raise ValueError("Boîte de visage invalide.")
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    inside = x1 >= 0.0 and y1 >= 0.0 and x2 <= 1.0 and y2 <= 1.0
    if inside:
        return (x1, y1, x2, y2)

    low = BOX_INSET
    high = 1.0 - BOX_INSET
    x1 = min(max(x1, low), high)
    y1 = min(max(y1, low), high)
    x2 = min(max(x2, low), high)
    y2 = min(max(y2, low), high)

    if x2 - x1 < BOX_MIN_SIZE:
        center = min(max((x1 + x2) / 2, low + BOX_MIN_SIZE / 2), high - BOX_MIN_SIZE / 2)
        x1, x2 = center - BOX_MIN_SIZE / 2, center + BOX_MIN_SIZE / 2
    if y2 - y1 < BOX_MIN_SIZE:
        center = min(max((y1 + y2) / 2, low + BOX_MIN_SIZE / 2), high - BOX_MIN_SIZE / 2)
        y1, y2 = center - BOX_MIN_SIZE / 2, center + BOX_MIN_SIZE / 2
    return (x1, y1, x2, y2)


STATE: "AppState"


def image_version(path: Path) -> str:
    """Version d'une image pour l'URL : invalide le cache quand le fichier change."""
    try:
        return f"{path.stat().st_mtime_ns}-{IMAGE_VERSION}"
    except OSError:
        return IMAGE_VERSION


def relpath_for(path: Path) -> str:
    try:
        return face_db.to_relpath(path, PHOTO_ROOT)
    except ValueError:
        return "abs:" + str(path)


def path_for(key: str) -> Path:
    if key.startswith("abs:"):
        return Path(key[4:])
    return face_db.abs_path(key, PHOTO_ROOT)


def relative_parent(path: Path, directory: Path | None) -> str:
    if directory is None:
        return ""
    try:
        parent = path.parent.relative_to(directory)
    except ValueError:
        return ""
    return "" if parent == Path(".") else parent.as_posix()


@lru_cache(maxsize=800)
def _thumbnail_bytes(path_str: str, modified: int, size: int) -> bytes:
    return _render(path_str, size, 78)


@lru_cache(maxsize=48)
def _image_bytes(path_str: str, modified: int, size: int) -> bytes:
    return _render(path_str, size, 88)


@lru_cache(maxsize=1200)
def _crop_bytes(
    path_str: str, modified: int, x1: float, y1: float, x2: float, y2: float, size: int
) -> bytes:
    path = Path(path_str)
    image, _ = metadata.best_image(path)
    if image is None:
        raise ValueError("Image illisible.")
    height, width = image.shape[:2]
    scale = min(1.0, 1024 / max(height, width))
    if scale < 1.0:
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        height, width = image.shape[:2]
    margin_x = (x2 - x1) * 0.28
    margin_y = (y2 - y1) * 0.28
    left = max(0, round((x1 - margin_x) * width))
    top = max(0, round((y1 - margin_y) * height))
    right = min(width, round((x2 + margin_x) * width))
    bottom = min(height, round((y2 + margin_y) * height))
    if right <= left or bottom <= top:
        raise ValueError("Cadrage de visage invalide.")
    crop = image[top:bottom, left:right]
    crop_height, crop_width = crop.shape[:2]
    zoom = size / max(crop_height, crop_width)
    if zoom != 1:
        crop = cv2.resize(
            crop,
            (max(1, round(crop_width * zoom)), max(1, round(crop_height * zoom))),
            interpolation=cv2.INTER_AREA if zoom < 1 else cv2.INTER_CUBIC,
        )
    ok, buffer = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise ValueError("Encodage JPEG impossible.")
    return buffer.tobytes()


def _render(path_str: str, size: int, quality: int) -> bytes:
    path = Path(path_str)
    image, _ = metadata.best_image(path)
    if image is None:
        raise ValueError("Image illisible.")
    height, width = image.shape[:2]
    scale = size / max(height, width)
    if scale < 0.999:
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("Encodage JPEG impossible.")
    return buffer.tobytes()


class AppState:
    def __init__(self):
        self.photos_dir: Path | None = None
        self.lock = threading.RLock()
        self.index_lock = threading.Lock()
        self.app = None
        self.app_lock = threading.Lock()
        self.people: dict[int, dict] = {}
        self.name_index: dict[str, int] = {}
        self.known_names: set[str] = set()
        self.references: dict | None = None
        self.files: list[Path] = []
        self.files_dir: Path | None = None
        self.status: dict[str, dict] = {}
        self.status_dirty = True
        self.jobs: dict[str, dict] = {}
        self.job_running: str | None = None

    # --- catalogue et base -------------------------------------------------

    def load_catalogue(self) -> None:
        self.people = {
            person["id_people"]: person
            for person in photos_db.load_people()
        }
        self.name_index = photos_db.build_index(list(self.people.values()))
        self.known_names = {
            photos_db.normalize_name(person["label"])
            for person in self.people.values()
        }

    def create_person(self, name: str) -> dict:
        """Ajoute une personne au catalogue `photos.db` et la rend utilisable."""
        cleaned = " ".join(str(name or "").split())
        if not cleaned:
            raise ValueError("Le nom est vide.")
        if len(cleaned) > 80:
            raise ValueError("Le nom est trop long (80 caractères maximum).")
        if any(character in cleaned for character in "\0\n\r\t"):
            raise ValueError("Le nom contient des caractères interdits.")
        key = photos_db.normalize_name(cleaned)
        if not key:
            raise ValueError("Nom invalide.")
        for person in self.people.values():
            if photos_db.normalize_name(person["label"]) == key:
                return person

        conn = photos_db.connect()
        try:
            person = photos_db.insert_person(conn, cleaned)
        finally:
            conn.close()
        with self.lock:
            self.people[person["id_people"]] = person
            self.name_index.update(photos_db.build_index([person]))
            self.known_names.add(key)
            self.status_dirty = True
        return person

    def reload_references(self) -> None:
        conn = face_db.connect()
        try:
            self.references = face_db.load_references(conn)
        finally:
            conn.close()

    def get_app(self):
        with self.app_lock:
            if self.app is None:
                print("Chargement du modèle InsightFace…", flush=True)
                self.app = index.create_app(640)
                print("Modèle prêt.", flush=True)
            return self.app

    # --- photos ------------------------------------------------------------

    def set_directory(self, directory: Path) -> None:
        self.photos_dir = directory.expanduser().resolve()
        self.files = []
        self.files_dir = None
        self.status_dirty = True

    def list_files(self) -> list[Path]:
        if self.photos_dir is None:
            return []
        if self.files_dir != self.photos_dir:
            directory = self.photos_dir
            files = []
            for path in directory.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in index.IMAGE_EXTS:
                    continue
                try:
                    parts = path.relative_to(directory).parts
                except ValueError:
                    continue
                if any(part.startswith(".") for part in parts):
                    continue
                if "thumbnail" in parts or "_DATA" in parts:
                    continue
                files.append(path.resolve())
            self.files = sorted(files, key=lambda path: str(path).casefold())
            self.files_dir = self.photos_dir
        return self.files

    def photo_path(self, key: str) -> Path | None:
        if self.photos_dir is None or not key:
            return None
        try:
            path = path_for(key).resolve()
        except (OSError, ValueError):
            return None
        directory = self.photos_dir.resolve()
        if path != directory and directory not in path.parents:
            return None
        if not path.is_file() or path.suffix.lower() not in index.IMAGE_EXTS:
            return None
        return path

    def face_row(self, key: str, face_index: int):
        conn = face_db.connect()
        try:
            return face_db.face_row(conn, key, face_index)
        finally:
            conn.close()

    def status_map(self, conn) -> dict[str, dict]:
        if self.status_dirty:
            self.status = face_db.status_map(conn)
            self.status_dirty = False
        return self.status

    # --- reconnaissance ----------------------------------------------------

    def scores(self, embedding: np.ndarray, exclude: str | None = None) -> list[dict]:
        """Similarité maximale par personne de référence.

        Les visages étalons provenant de la photo elle-même sont écartés :
        sinon la photo se reconnaîtrait elle-même avec un score de 1.
        """
        references = self.references or {
            "ids": [], "names": [], "matrix": np.zeros((0, 512), np.float32), "groups": []
        }
        if not references["groups"]:
            return []
        similarities = references["matrix"] @ embedding
        if exclude is not None and len(references.get("relpaths", ())):
            similarities[references["relpaths"] == exclude] = -1.0
        scored = [
            {
                "id_people": references["ids"][position],
                "name": references["names"][position],
                "score": float(similarities[start:end].max()),
            }
            for position, (start, end) in enumerate(references["groups"])
        ]
        scored.sort(key=lambda item: -item["score"])
        return scored

    def ensure_indexed(self, path: Path, key: str) -> None:
        with self.index_lock:
            conn = face_db.connect()
            try:
                stat = path.stat()
                if face_db.is_current(conn, key, stat.st_size, stat.st_mtime):
                    return
                regions = metadata.read_regions([path]).get(str(path.resolve()), [])
                index.process_photo(
                    self.get_app(),
                    conn,
                    path,
                    key,
                    regions,
                    self.name_index,
                    self.people,
                )
                self.status_dirty = True
            finally:
                conn.close()

    def photo_payload(self, key: str) -> dict:
        path = self.photo_path(key)
        if path is None:
            raise ValueError("Photo introuvable dans le dossier courant.")

        self.ensure_indexed(path, key)

        conn = face_db.connect()
        try:
            rows = face_db.photo_faces(conn, key)
            limits = face_db.photo_dimensions(conn, key)
        finally:
            conn.close()

        regions = metadata.read_regions([path]).get(str(path.resolve()), [])
        boxes = [
            {"bbox": (row["x1"], row["y1"], row["x2"], row["y2"])}
            for row in rows
        ]
        detailed = index.match_regions_detailed(regions, boxes)
        matches = {face_index: (name, score) for face_index, _, name, score in detailed}
        matched_regions = {region_index for _, region_index, _, _ in detailed}

        references = self.references or {}
        suggestion = float(references.get("suggestion", 0.33))
        threshold = float(references.get("threshold", 0.43))

        faces = []
        for position, row in enumerate(rows):
            invalid = bool(row["invalid"])
            alternatives = []
            existing_payload = None
            if not invalid:
                scored = self.scores(face_db.unpack(row["embedding"]), exclude=key)
                alternatives = [
                    {
                        "id_people": item["id_people"],
                        "name": item["name"],
                        "score": round(item["score"], 3),
                    }
                    for item in scored[:3]
                ]
                existing = matches.get(position)
                if existing:
                    name, score = existing
                    existing_payload = {
                        "name": name,
                        "id_people": self.name_index.get(photos_db.normalize_name(name)),
                        "score": round(float(score), 3),
                    }
            faces.append(
                {
                    "index": position,
                    "bbox": [
                        round(value, 6)
                        for value in clamp_bbox(
                            (row["x1"], row["y1"], row["x2"], row["y2"])
                        )
                    ],
                    "detScore": round(float(row["det_score"]), 3),
                    "existing": existing_payload,
                    "ignored": (
                        bool(row["ignored"])
                        and not invalid
                        and existing_payload is None
                    ),
                    "invalid": invalid,
                    "alternatives": alternatives,
                    "proposal": (
                        alternatives[0]
                        if not invalid
                        and alternatives
                        and alternatives[0]["score"] >= suggestion
                        else None
                    ),
                }
            )

        orphans = [
            region
            for position, region in enumerate(regions)
            if position not in matched_regions
        ]

        return {
            "name": key,
            "displayName": path.name,
            "width": int(limits["width"]) if limits else 0,
            "height": int(limits["height"]) if limits else 0,
            "image": (
                f"/api/image?name={quote(key)}&size={PREVIEW_SIZE}"
                f"&v={image_version(path)}"
            ),
            "faces": faces,
            "orphans": orphans,
            "threshold": threshold,
            "suggestion": suggestion,
            "photographed": bool(limits),
        }

    # --- écriture ----------------------------------------------------------

    def save(self, payload: dict) -> dict:
        key = str(payload.get("name") or "")
        path = self.photo_path(key)
        if path is None:
            raise ValueError("Photo introuvable dans le dossier courant.")

        submitted = payload.get("faces")
        if not isinstance(submitted, list) or len(submitted) > 200:
            raise ValueError("Liste de visages invalide.")
        assignments = []
        for face in submitted:
            bbox = face.get("bbox") if isinstance(face, dict) else None
            if not (isinstance(bbox, list) and len(bbox) == 4):
                raise ValueError("Boîte de visage invalide.")
            try:
                bbox = tuple(float(value) for value in bbox)
            except (TypeError, ValueError):
                raise ValueError("Boîte de visage invalide.") from None
            bbox = clamp_bbox(bbox)
            index_value = face.get("index")
            if index_value is None:
                face_index = None
            else:
                try:
                    face_index = int(index_value)
                except (TypeError, ValueError):
                    raise ValueError("Index de visage invalide.") from None
            id_people = face.get("id_people")
            ignored = bool(face.get("ignored"))
            invalid = bool(face.get("invalid"))
            if id_people is not None:
                try:
                    id_people = int(id_people)
                except (TypeError, ValueError):
                    raise ValueError("Personne invalide.") from None
                if id_people not in self.people:
                    raise ValueError("Personne inconnue.")
                # Un visage nommé n'est jamais « sans nom » ni invalide.
                ignored = False
                invalid = False
            elif invalid:
                ignored = False
            # Seuls les visages réellement modifiés par l'utilisateur sont touchés.
            # Les régions existantes que l'app ne sait pas nommer sont conservées.
            assignments.append(
                (
                    face_index,
                    bbox,
                    id_people,
                    bool(face.get("apply")),
                    ignored,
                    invalid,
                )
            )

        existing = metadata.read_regions([path]).get(str(path.resolve()), [])
        touched = [bbox for _, bbox, _, apply, _, _ in assignments if apply]
        # Appariement un-à-un entre les visages modifiés et les régions existantes :
        # une région de Lightroom ne recouvre pas exactement la boîte du détecteur
        # (IoU observé 0,3–0,65), mais elle ne doit être remplacée qu'une fois.
        pairs = []
        for box_index, bbox in enumerate(touched):
            for region_index, region in enumerate(existing):
                score = index.iou(region["bbox"], bbox)
                if score >= index.MIN_REGION_IOU:
                    pairs.append((score, box_index, region_index))
        pairs.sort(reverse=True)
        replaced_regions: set[int] = set()
        used_boxes: set[int] = set()
        for _, box_index, region_index in pairs:
            if box_index in used_boxes or region_index in replaced_regions:
                continue
            used_boxes.add(box_index)
            replaced_regions.add(region_index)
        regions = [
            region
            for position, region in enumerate(existing)
            if position not in replaced_regions
        ]
        regions += [
            {"name": self.people[id_people]["label"], "bbox": bbox}
            for _, bbox, id_people, apply, _, _ in assignments
            if apply and id_people is not None
        ]

        # « Sans nom » sur un visage encore inconnu ne modifie aucune métadonnée :
        # seule la décision dans face.db change. On évite ainsi de réécrire le
        # fichier et d'incrémenter inutilement metadata_revision.
        metadata_changed = bool(replaced_regions) or any(
            apply and id_people is not None
            for _, _, id_people, apply, _, _ in assignments
        )
        if metadata_changed:
            keywords = metadata.read_keywords(path)
            record = metadata.read_metadata(path)
            width = int(record.get("ImageWidth") or 0)
            height = int(record.get("ImageHeight") or 0)
            orientation = int(record.get("Orientation") or 1)
            if orientation in (5, 6, 7, 8):
                width, height = height, width
            if not width or not height:
                conn = face_db.connect()
                try:
                    limits = face_db.photo_dimensions(conn, key)
                finally:
                    conn.close()
                if limits:
                    width, height = int(limits["width"]), int(limits["height"])

            names = [region["name"] for region in regions]

            def merged(values: list[str]) -> list[str]:
                result = []
                for value in values:
                    if photos_db.normalize_name(value) in self.known_names:
                        continue
                    if value not in result:
                        result.append(value)
                for name in names:
                    if name not in result:
                        result.append(name)
                return result

            metadata.write_regions(
                path,
                width,
                height,
                regions,
                subject=merged(keywords["subject"]),
                hierarchical=merged(keywords["hierarchical"]),
                weighted=merged(keywords["weighted"]),
            )

        conn = face_db.connect()
        try:
            # Les décisions de travail sont appliquées avant de recalculer les
            # étiquettes : une détection invalidée ne doit pas être réintroduite
            # par une région XMP, même si elle chevauche une ancienne région.
            for face_index, bbox, id_people, apply, ignored, invalid in assignments:
                if not apply:
                    continue
                if face_index is None:
                    best_position = None
                    best_score = 0.0
                    for position, row in enumerate(face_db.photo_faces(conn, key)):
                        score = index.iou(
                            (row["x1"], row["y1"], row["x2"], row["y2"]), bbox
                        )
                        if score > best_score:
                            best_position = position
                            best_score = score
                    if best_score >= index.MIN_REGION_IOU:
                        face_index = best_position
                if face_index is not None:
                    face_db.set_face_invalid(conn, key, face_index, invalid)
                    if not invalid:
                        face_db.set_face_ignored(conn, key, face_index, ignored)

            rows = face_db.photo_faces(conn, key)
            boxes = [{"bbox": (row["x1"], row["y1"], row["x2"], row["y2"])} for row in rows]
            # Les étalons de cette photo sont réécrits : une correction ne doit pas
            # laisser traîner l'ancien nom dans la galerie de référence.
            face_db.clear_exemplars(conn, key)
            labels = []
            for face_index, name, score in index.match_regions(regions, boxes):
                if face_index >= len(rows) or rows[face_index]["invalid"]:
                    continue
                id_people = self.name_index.get(photos_db.normalize_name(name))
                if id_people is None:
                    continue
                labels.append(
                    {
                        "face_idx": face_index,
                        "id_people": id_people,
                        "name": self.people[id_people]["label"],
                        "score": score,
                        "source": "xmp",
                    }
                )
                face_db.add_exemplar(
                    conn,
                    id_people,
                    self.people[id_people]["label"],
                    key,
                    face_db.unpack(rows[face_index]["embedding"]),
                )
            face_db.replace_labels(conn, key, labels)
            stat = path.stat()
            face_db.mark_current(conn, key, stat.st_size, stat.st_mtime)
            self.status_dirty = True
            self.references = face_db.load_references(conn)
        finally:
            conn.close()

        return self.photo_payload(key)

    # --- analyse du dossier ------------------------------------------------

    def start_analyze(self, names: list[str] | None = None) -> str:
        paths = self.list_files()
        if names:
            selected_paths = []
            for name in names:
                path = self.photo_path(str(name))
                if path is not None:
                    selected_paths.append(path)
            if not selected_paths:
                raise ValueError("Aucune photo sélectionnée n’est disponible.")
            paths = selected_paths
        with self.lock:
            if self.job_running:
                return self.job_running
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {
                "id": job_id,
                "total": 0,
                "done": 0,
                "faces": 0,
                "labels": 0,
                "errors": 0,
                "complete": False,
                "message": "Préparation…",
                "started": time.time(),
            }
            self.job_running = job_id
            while len(self.jobs) > 20:
                self.jobs.pop(next(iter(self.jobs)))
        threading.Thread(
            target=self.run_analyze, args=(job_id, paths), daemon=True
        ).start()
        return job_id

    def run_analyze(self, job_id: str, paths: list[Path]) -> None:
        job = self.jobs[job_id]
        conn = face_db.connect()
        try:
            pending = 0
            for path in paths:
                try:
                    relpath = relpath_for(path)
                    stat = path.stat()
                    if not face_db.is_current(conn, relpath, stat.st_size, stat.st_mtime):
                        pending += 1
                except OSError:
                    continue
            job["total"] = pending
            job["message"] = "Analyse…"

            def progress(done: int, total: int, stats: dict) -> None:
                job.update(
                    done=done,
                    total=total,
                    faces=stats["visages"],
                    labels=stats["etiquetes"],
                    errors=stats["erreurs"],
                )

            index.index_paths(
                paths,
                conn,
                self.get_app(),
                self.name_index,
                self.people,
                root=PHOTO_ROOT,
                progress=progress,
            )
            job["message"] = "Terminé."
        except Exception as error:  # noqa: BLE001
            job["errors"] += 1
            job["message"] = str(error)[:300]
        finally:
            conn.close()
            with self.lock:
                job["complete"] = True
                self.job_running = None
                self.status_dirty = True
                self.files = []
                self.files_dir = None

    def directory_prefix(self) -> str:
        if self.photos_dir is None:
            return ""
        try:
            key = relpath_for(self.photos_dir)
        except ValueError:
            return ""
        if key in ("", "."):
            return ""
        return key.rstrip("/") + "/"

    def pending_faces(self, limit: int = 400) -> list[dict]:
        """Visages du dossier courant qui n'ont encore aucune décision.

        Un visage est « décidé » s'il est nommé ou validé « sans nom ». Les
        autres sont triés du plus incertain au plus sûr. Une proposition n'est
        affichée que si elle dépasse le seuil de suggestion ; les visages sans
        proposition restent dans la liste pour pouvoir être validés sans nom.
        """
        conn = face_db.connect()
        try:
            rows = face_db.unlabelled_faces(conn)
        finally:
            conn.close()

        prefix = self.directory_prefix()
        visible = {relpath_for(path) for path in self.list_files()}
        rows = [
            row
            for row in rows
            if row["relpath"].startswith(prefix) and row["relpath"] in visible
        ]
        if not rows:
            return []

        references = self.references or {}
        groups = references.get("groups") or []
        suggestion = float(references.get("suggestion", 0.33))
        best = np.full(len(rows), -1, dtype=int)
        best_scores = np.zeros(len(rows), dtype=np.float32)

        if groups:
            matrix = np.stack([face_db.unpack(row["embedding"]) for row in rows])
            similarities = matrix @ references["matrix"].T

            by_photo: dict[str, list[int]] = {}
            for position, row in enumerate(rows):
                by_photo.setdefault(row["relpath"], []).append(position)
            for relpath, positions in by_photo.items():
                columns = np.nonzero(references["relpaths"] == relpath)[0]
                if len(columns):
                    similarities[np.ix_(positions, columns)] = -1.0

            starts = [start for start, _ in groups]
            person_scores = np.maximum.reduceat(similarities, starts, axis=1)
            best = person_scores.argmax(axis=1)
            best_scores = person_scores[np.arange(len(rows)), best]

        order = np.argsort(best_scores) if groups else range(len(rows))
        items = []
        for position in order:
            score = float(best_scores[position]) if groups else 0.0
            person = int(best[position]) if groups else -1
            has_proposal = bool(groups) and person >= 0 and score >= suggestion
            row = rows[position]
            items.append(
                {
                    "name": row["relpath"],
                    "faceIndex": int(row["face_idx"]),
                    "score": round(score, 3),
                    "id_people": references["ids"][person] if has_proposal else None,
                    "person": references["names"][person] if has_proposal else "",
                    "detScore": round(float(row["det_score"]), 3),
                    "hasProposal": has_proposal,
                }
            )
            if len(items) >= limit:
                break
        return items

    @property
    def directory(self) -> str:
        return str(self.photos_dir) if self.photos_dir else ""


# --- serveur HTTP ---------------------------------------------------------


class FaceHandler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")

    def send_body(self, body: bytes, content_type: str, status: int = 200, cache_control: str | None = None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            print(f"Client {self.address_string()} déconnecté.", flush=True)

    def send_json(self, payload, status: int = 200):
        self.send_body(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("La requête est vide ou trop volumineuse.")
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/state":
                self.api_state()
            elif parsed.path == "/api/people":
                self.api_people()
            elif parsed.path == "/api/photos":
                self.api_photos(query)
            elif parsed.path == "/api/photo":
                self.api_photo(query)
            elif parsed.path == "/api/thumbnail":
                self.api_image(query, thumbnail=True)
            elif parsed.path == "/api/image":
                self.api_image(query, thumbnail=False)
            elif parsed.path == "/api/pending":
                self.api_pending(query)
            elif parsed.path == "/api/crop":
                self.api_crop(query)
            elif parsed.path == "/api/analyze":
                self.api_analyze_status(query)
            elif parsed.path == "/":
                self.send_static("index.html")
            elif parsed.path in ("/app.js", "/styles.css"):
                self.send_static(parsed.path[1:])
            else:
                self.send_error(404)
        except (ValueError, FileNotFoundError) as error:
            self.send_json({"error": str(error)}, 400)
        except Exception as error:  # noqa: BLE001
            print(f"Erreur : {error}", flush=True)
            self.send_json({"error": str(error)}, 500)

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/save":
                self.send_json(STATE.save(self.read_json()))
            elif route == "/api/people":
                payload = self.read_json()
                self.send_json(STATE.create_person(payload.get("name", "")), 201)
            elif route == "/api/directory":
                payload = self.read_json()
                directory = Path(str(payload.get("directory") or "")).expanduser().resolve()
                if not directory.is_dir():
                    raise ValueError(f"Dossier introuvable : {directory}")
                STATE.set_directory(directory)
                _thumbnail_bytes.cache_clear()
                _image_bytes.cache_clear()
                _crop_bytes.cache_clear()
                self.send_json({"directory": str(directory)})
            elif route == "/api/analyze":
                payload = self.read_json()
                names = payload.get("names")
                if names is not None and not isinstance(names, list):
                    raise ValueError("Liste de photos sélectionnées invalide.")
                self.send_json(
                    {"jobId": STATE.start_analyze(
                        [str(name) for name in names] if names else None
                    )},
                    202,
                )
            elif route == "/api/change-directory":
                self.handle_change_directory()
            elif route == "/api/shutdown":
                self.handle_shutdown()
            else:
                self.send_error(404)
        except (ValueError, FileNotFoundError) as error:
            self.send_json({"error": str(error)}, 400)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_json({"error": str(error)}, 500)

    def handle_shutdown(self):
        self.send_json({"status": "shutting_down"})
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    # --- routes ------------------------------------------------------------

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
        self.send_body(body, content_type, cache_control="no-cache")

    def api_state(self):
        references = STATE.references or {}
        self.send_json(
            {
                "directory": STATE.directory,
                "photoCount": len(STATE.list_files()),
                "catalogue": len(STATE.people),
                "gallery": {
                    "people": len(references.get("ids", [])),
                    "exemplars": int(references.get("matrix", np.zeros((0, 0))).shape[0]),
                    "threshold": references.get("threshold", 0.43),
                    "suggestion": references.get("suggestion", 0.33),
                },
            }
        )

    def api_people(self):
        payload = [
            {
                "id_people": person["id_people"],
                "name": person["label"],
                "prenom": person["prenom"],
                "nom": person["nom"],
            }
            for person in sorted(STATE.people.values(), key=lambda item: item["label"].casefold())
        ]
        self.send_json({"people": payload})

    def api_photos(self, query):
        try:
            offset = max(0, int(query.get("offset", ["0"])[0]))
            limit = min(500, max(1, int(query.get("limit", [str(PAGE_SIZE)])[0])))
        except ValueError:
            raise ValueError("Paramètres de pagination invalides.") from None
        wanted = query.get("filter", ["all"])[0]

        conn = face_db.connect()
        try:
            status = dict(STATE.status_map(conn))
        finally:
            conn.close()

        entries = []
        for path in STATE.list_files():
            key = relpath_for(path)
            info = status.get(key)
            indexed = info is not None and not info.get("error")
            n_valid = (
                max(0, int(info["n_faces"]) - int(info.get("n_invalid", 0)))
                if indexed
                else 0
            )
            entry = {
                "name": key,
                "displayName": path.name,
                "relative": relative_parent(path, STATE.photos_dir),
                "thumbnail": (
                    f"/api/thumbnail?name={quote(key)}&size=320"
                    f"&v={image_version(path)}"
                ),
                "indexed": indexed,
                "nFaces": n_valid,
                "nNamed": int(info["n_labels"]) if indexed else 0,
                "nIgnored": int(info.get("n_ignored", 0)) if indexed else 0,
                "nInvalid": int(info.get("n_invalid", 0)) if indexed else 0,
                "nResolved": (
                    int(info["n_labels"]) + int(info.get("n_ignored", 0))
                    if indexed
                    else 0
                ),
                "error": info.get("error") if info else None,
            }
            if wanted == "todo" and not (
                not indexed or entry["nResolved"] < entry["nFaces"]
            ):
                continue
            if wanted == "named" and not (
                entry["nFaces"] > 0 and entry["nResolved"] >= entry["nFaces"]
            ):
                continue
            if wanted == "new" and indexed:
                continue
            entries.append(entry)

        self.send_json(
            {
                "directory": STATE.directory,
                "total": len(entries),
                "photos": entries[offset:offset + limit],
            }
        )

    def api_photo(self, query):
        key = query.get("name", [""])[0]
        self.send_json(STATE.photo_payload(key))

    def api_image(self, query, thumbnail: bool):
        key = query.get("name", [""])[0]
        path = STATE.photo_path(key)
        if path is None:
            self.send_error(404)
            return
        default = 320 if thumbnail else PREVIEW_SIZE
        try:
            size = min(3000, max(64, int(query.get("size", [str(default)])[0])))
        except ValueError:
            size = default
        modified = path.stat().st_mtime_ns
        body = (
            _thumbnail_bytes(str(path), modified, size)
            if thumbnail
            else _image_bytes(str(path), modified, size)
        )
        self.send_body(body, "image/jpeg", cache_control="private, max-age=86400")

    def api_pending(self, query):
        try:
            limit = min(1000, max(1, int(query.get("limit", ["400"])[0])))
        except ValueError:
            limit = 400
        items = STATE.pending_faces(limit)
        for item in items:
            path = STATE.photo_path(item["name"])
            version = image_version(path) if path is not None else IMAGE_VERSION
            item["crop"] = (
                f"/api/crop?name={quote(item['name'])}&index={item['faceIndex']}"
                f"&size=190&v={version}"
            )
        self.send_json({"faces": items, "total": len(items)})

    def api_crop(self, query):
        key = query.get("name", [""])[0]
        path = STATE.photo_path(key)
        if path is None:
            self.send_error(404)
            return
        try:
            face_index = int(query.get("index", ["0"])[0])
            size = min(400, max(64, int(query.get("size", ["190"])[0])))
        except ValueError:
            raise ValueError("Paramètres de cadrage invalides.") from None
        face = STATE.face_row(key, face_index)
        if face is None:
            self.send_error(404)
            return
        body = _crop_bytes(
            str(path),
            path.stat().st_mtime_ns,
            float(face["x1"]),
            float(face["y1"]),
            float(face["x2"]),
            float(face["y2"]),
            size,
        )
        self.send_body(body, "image/jpeg", cache_control="private, max-age=86400")

    def api_analyze_status(self, query):
        job_id = query.get("jobId", [""])[0]
        job = STATE.jobs.get(job_id)
        if job is None:
            self.send_json({"error": "Analyse introuvable."}, 404)
            return
        self.send_json(job)

    def handle_change_directory(self):
        selected = choose_directory(initial_directory())
        if selected is None:
            self.send_json({"error": "Aucun dossier sélectionné."}, 400)
            return
        save_preference(selected)
        STATE.set_directory(selected)
        _thumbnail_bytes.cache_clear()
        _image_bytes.cache_clear()
        self.send_json({"directory": STATE.directory, "photoCount": len(STATE.list_files())})


# --- préférences et sélecteur de dossier ---------------------------------


def initial_directory() -> Path:
    return preferences.initial_directory(PHOTO_ROOT if PHOTO_ROOT.is_dir() else Path.cwd()) or Path.cwd()


def save_preference(directory: Path) -> None:
    try:
        preferences.set_initial_directory(directory)
    except OSError as error:
        print(f"Préférences non enregistrées : {error}", flush=True)


def choose_directory(start: Path) -> Path | None:
    script = """
on run argv
    set initialFolder to POSIX file (item 1 of argv) as alias
    set chosenFolder to choose folder with prompt "Choisir le dossier contenant les photos" default location initialFolder
    return POSIX path of chosenFolder
end run
"""
    print("Ouverture du sélecteur de dossier macOS…", flush=True)
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script, str(start)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        if "(-128)" in result.stderr:
            return None
        message = result.stderr.strip() or result.stdout.strip() or "Erreur AppleScript."
        raise RuntimeError(f"Impossible de choisir le dossier de photos : {message}")
    selected = result.stdout.strip()
    if not selected:
        raise RuntimeError("Le sélecteur macOS n'a renvoyé aucun dossier.")
    return Path(selected).expanduser().resolve()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Interface web locale pour identifier les personnes sur les photos."
    )
    parser.add_argument(
        "photos_dir",
        nargs="?",
        default=None,
        help="Dossier contenant les photos (si omis, une fenêtre de sélection s'ouvre).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Adresse d'écoute (défaut : 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8100, help="Port HTTP (défaut : 8100).")
    args = parser.parse_args()

    global STATE
    startup_conn = face_db.connect()
    try:
        face_db.create_schema(startup_conn)
    finally:
        startup_conn.close()

    STATE = AppState()
    STATE.load_catalogue()
    STATE.reload_references()

    if args.photos_dir is None:
        if sys.platform == "darwin":
            try:
                selected = choose_directory(initial_directory())
            except RuntimeError as error:
                parser.error(str(error))
            if selected is None:
                print("Aucun dossier sélectionné ; arrêt du serveur.", flush=True)
                return
            save_preference(selected)
            directory = selected
        else:
            directory = initial_directory()
    else:
        directory = Path(args.photos_dir).expanduser().resolve()

    if not directory.is_dir():
        parser.error(f"Le dossier de photos n'existe pas : {directory}")
    STATE.set_directory(directory)

    # Précharge le modèle pendant que l'utilisateur choisit sa première photo.
    threading.Thread(target=STATE.get_app, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), FaceHandler)
    print(f"Dossier de photos : {directory}", flush=True)
    print(f"Interface : http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
