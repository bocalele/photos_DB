#!/usr/bin/env python3
"""Lecture et écriture des visages dans les métadonnées XMP des photos.

Le standard utilisé est celui de Lightroom : régions MWG (`XMP-mwg-rs:RegionInfo`),
plus les mots-clés `XMP-dc:Subject`, `XMP-lr:HierarchicalSubject` et
`XMP-lr:WeightedFlatSubject`, qui contiennent les noms des personnes.

Les écritures passent par un fichier JSON intermédiaire : ExifTool y gère
correctement les noms contenant des virgules, des apostrophes ou des accents.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import face_db  # noqa: E402
from tools import preferences  # noqa: E402

REGION_TAGS = (
    "-XMP-mwg-rs:RegionName",
    "-XMP-mwg-rs:RegionType",
    "-XMP-mwg-rs:RegionAreaX",
    "-XMP-mwg-rs:RegionAreaY",
    "-XMP-mwg-rs:RegionAreaW",
    "-XMP-mwg-rs:RegionAreaH",
)

KEYWORD_TAGS = (
    "-XMP-dc:Subject",
    "-XMP-lr:HierarchicalSubject",
    "-XMP-lr:WeightedFlatSubject",
)

PREVIEW_TAGS = (
    "JpgFromRaw",
    "PreviewImage",
    "OtherImage",
    "PhotoshopThumbnail",
    "ThumbnailImage",
)


def exiftool_path() -> str:
    executable = shutil.which("exiftool")
    if executable is None:
        # Repli : exiftool installé dans le même environnement que Python.
        candidate = Path(sys.executable).with_name("exiftool")
        if candidate.is_file():
            executable = str(candidate)
    if executable is None:
        raise RuntimeError(
            "ExifTool est introuvable. Lancez l'application avec « pixi run »."
        )
    return executable


def exiftool_env() -> dict:
    """Environnement d'ExifTool, avec ses modules Perl si installé par conda."""
    env = os.environ.copy()
    try:
        prefix = Path(exiftool_path()).resolve().parent.parent
    except RuntimeError:
        return env
    modules = prefix / "lib" / "perl5" / "site_perl"
    if modules.is_dir():
        current = env.get("PERL5LIB", "")
        env["PERL5LIB"] = os.pathsep.join(filter(None, [str(modules), current]))
    return env


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def run_exiftool(arguments: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [exiftool_path(), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=exiftool_env(),
    )


def read_regions(paths: list[Path]) -> dict[str, list[dict]]:
    """Régions visage XMP, par lots de 100 fichiers.

    Retourne, pour chaque chemin absolu, une liste de
    ``{"name": str, "bbox": (x1, y1, x2, y2)}`` en coordonnées normalisées.
    """
    regions: dict[str, list[dict]] = {}
    for offset in range(0, len(paths), 100):
        chunk = [str(path) for path in paths[offset:offset + 100]]
        result = run_exiftool(["-json", "-n", *REGION_TAGS, *chunk])
        if result.returncode != 0 and not result.stdout.strip():
            print(f"  ExifTool : {result.stderr.strip()[:200]}", flush=True)
            continue
        try:
            records = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            continue
        for record in records:
            source = record.get("SourceFile")
            if not source:
                continue
            names = as_list(record.get("RegionName"))
            types = as_list(record.get("RegionType"))
            xs = as_list(record.get("RegionAreaX"))
            ys = as_list(record.get("RegionAreaY"))
            ws = as_list(record.get("RegionAreaW"))
            hs = as_list(record.get("RegionAreaH"))
            if not (names and xs and ys and ws and hs):
                continue
            if not (len(names) == len(xs) == len(ys) == len(ws) == len(hs)):
                continue
            found = []
            for index, name in enumerate(names):
                region_type = types[index] if index < len(types) else "Face"
                if str(region_type).casefold() != "face":
                    continue
                x, y, w, h = (float(xs[index]), float(ys[index]), float(ws[index]), float(hs[index]))
                # MWG : X et Y désignent le centre du rectangle, en coordonnées normalisées.
                found.append(
                    {
                        "name": str(name).strip(),
                        "bbox": (
                            max(0.0, x - w / 2),
                            max(0.0, y - h / 2),
                            min(1.0, x + w / 2),
                            min(1.0, y + h / 2),
                        ),
                    }
                )
            if found:
                regions[str(Path(source).resolve())] = found
    return regions


def read_metadata(path: Path) -> dict:
    """Dimensions, orientation et mots-clés d'une photo."""
    result = run_exiftool(
        ["-json", "-n", "-ImageWidth", "-ImageHeight", "-Orientation",
         *KEYWORD_TAGS, str(path)],
        timeout=60,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        records = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return records[0] if records else {}


def read_keywords(path: Path) -> dict[str, list[str]]:
    record = read_metadata(path)
    return {
        "subject": [str(value) for value in as_list(record.get("Subject"))],
        "hierarchical": [str(value) for value in as_list(record.get("HierarchicalSubject"))],
        "weighted": [str(value) for value in as_list(record.get("WeightedFlatSubject"))],
    }


def preview_bytes(path: Path) -> bytes:
    """Aperçu intégré d'un fichier RAW, PSD ou HEIC."""
    for tag in PREVIEW_TAGS:
        result = subprocess.run(
            [exiftool_path(), "-b", f"-{tag}", str(path)],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    return b""


def render_large_preview(path: Path, max_side: int = 2048) -> Path | None:
    """Rend un grand aperçu JPEG avec le décodeur macOS, et le met en cache.

    Les fichiers PSD n'exposent souvent qu'une vignette de 160 px dans leurs
    métadonnées : trop peu pour détecter des visages. `sips` (ImageIO) sait
    rendre l'image aplatie complète, y compris pour les RAW et les HEIC.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    digest = hashlib.sha1(f"{path}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()[:24]
    cache_dir = face_db.PREVIEWS_DIR
    target = cache_dir / f"{digest}.jpg"
    if target.is_file() and target.stat().st_size:
        return target
    # Nom unique par thread : le serveur peut demander la même vignette
    # deux fois en parallèle.
    temporary = cache_dir / f".{digest}.{os.getpid()}.{threading.get_ident()}.tmp.jpg"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "/usr/bin/sips",
                "-s", "format", "jpeg",
                "-s", "formatOptions", "90",
                "-Z", str(max_side),
                str(path),
                "--out", str(temporary),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode == 0 and temporary.is_file() and temporary.stat().st_size:
            temporary.replace(target)
            return target
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        temporary.unlink(missing_ok=True)
    return None


def decode(bytes_value: bytes) -> np.ndarray | None:
    if not bytes_value:
        return None
    return cv2.imdecode(np.frombuffer(bytes_value, np.uint8), cv2.IMREAD_COLOR)


def best_image(path: Path, max_side: int = 2048) -> tuple[np.ndarray | None, str]:
    """Meilleure image disponible : (image BGR, origine).

    Origine : ``direct`` (décodée par OpenCV), ``rendered`` (rendu macOS complet)
    ou ``embedded`` (aperçu intégré, éventuellement petit et recadré).
    """
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is not None:
        return image, "direct"

    embedded = decode(preview_bytes(path))
    if embedded is not None and max(embedded.shape[:2]) >= 1400:
        return embedded, "embedded"

    rendered_path = render_large_preview(path, max_side)
    rendered = cv2.imread(str(rendered_path), cv2.IMREAD_COLOR) if rendered_path else None
    if rendered is not None and (
        embedded is None or max(rendered.shape[:2]) > max(embedded.shape[:2])
    ):
        return rendered, "rendered"
    if embedded is not None:
        return embedded, "embedded"
    return None, "embedded"


def write_regions(
    path: Path,
    width: int,
    height: int,
    regions: list[dict],
    subject: list[str] | None = None,
    hierarchical: list[str] | None = None,
    weighted: list[str] | None = None,
) -> None:
    """Écrit la liste complète des visages nommés, plus les mots-clés.

    ``regions`` est une liste de ``{"name": str, "bbox": (x1, y1, x2, y2)}``
    en coordonnées normalisées. Les autres métadonnées du fichier sont
    conservées : seules les balises citées sont remplacées.
    """
    payload: dict = {}
    if regions:
        payload["XMP-mwg-rs:RegionInfo"] = {
            "AppliedToDimensions": {
                "W": int(width),
                "H": int(height),
                "Unit": "pixel",
            },
            "RegionList": [
                {
                    "Name": region["name"],
                    "Type": "Face",
                    "Area": {
                        "X": round((region["bbox"][0] + region["bbox"][2]) / 2, 6),
                        "Y": round((region["bbox"][1] + region["bbox"][3]) / 2, 6),
                        "W": round(region["bbox"][2] - region["bbox"][0], 6),
                        "H": round(region["bbox"][3] - region["bbox"][1], 6),
                    },
                }
                for region in regions
            ],
        }
    if subject is not None:
        payload["XMP-dc:Subject"] = list(subject)
    if hierarchical is not None:
        payload["XMP-lr:HierarchicalSubject"] = list(hierarchical)
    if weighted is not None:
        payload["XMP-lr:WeightedFlatSubject"] = list(weighted)

    if not payload:
        return
    if not regions:
        # La liste vide n'est pas représentable en JSON : on supprime la structure.
        result = run_exiftool(["-overwrite_original", "-XMP-mwg-rs:RegionInfo=", str(path)])
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Échec ExifTool.")

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump([payload], handle, ensure_ascii=False)
        update_path = handle.name
    try:
        result = run_exiftool(
            ["-overwrite_original", f"-json={update_path}", str(path)]
        )
    finally:
        Path(update_path).unlink(missing_ok=True)
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "Erreur ExifTool."
        raise RuntimeError(f"Écriture des métadonnées impossible : {message}")
    # Comme tools.setMetaData : signaler aux applications ouvertes que le
    # fichier a changé et que photos.db doit être resynchronisée.
    try:
        preferences.touch_metadata_revision()
    except OSError:
        pass
