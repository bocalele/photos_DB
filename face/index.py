#!/usr/bin/env python3
"""Indexe les visages d'un dossier dans `face.db`.

Pour chaque photo : détection + embedding, et si la photo porte des régions
visage XMP (Lightroom), chaque visage détecté est étiqueté avec le nom
correspondant. Ces étiquettes forment la galerie de référence.

Exemples :
    pixi run python index.py --from-db --only-labelled
    pixi run python index.py "/chemin/vers/le/dossier-de-photos" --max-side 2048
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import face_db, photos_db  # noqa: E402
import metadata  # noqa: E402

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp",
    ".heic", ".heif", ".psd", ".nef", ".cr2", ".cr3", ".dng", ".arw", ".raf", ".orf",
}

MIN_REGION_IOU = 0.25


def iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def match_regions_detailed(
    regions: list[dict], faces: list[dict], minimum: float = MIN_REGION_IOU
) -> list[tuple[int, int, str, float]]:
    """Appariement un-à-un : chaque région et chaque visage servent au plus une fois."""
    pairs = []
    for region_index, region in enumerate(regions):
        for face_index, face in enumerate(faces):
            score = iou(region["bbox"], face["bbox"])
            if score >= minimum:
                pairs.append((score, region_index, face_index))
    pairs.sort(reverse=True)
    used_regions, used_faces, matches = set(), set(), []
    for score, region_index, face_index in pairs:
        if region_index in used_regions or face_index in used_faces:
            continue
        used_regions.add(region_index)
        used_faces.add(face_index)
        matches.append(
            (face_index, region_index, regions[region_index]["name"], float(score))
        )
    return matches


def match_regions(regions: list[dict], faces: list[dict], minimum: float = MIN_REGION_IOU):
    return [
        (face_index, name, score)
        for face_index, _, name, score in match_regions_detailed(regions, faces, minimum)
    ]


def load_image(path: Path, max_side: int) -> tuple[np.ndarray | None, str, int, int]:
    """Charge l'image, en réduisant le grand côté.

    Retourne (image analysée, origine, largeur et hauteur d'origine).
    L'origine vaut ``direct``, ``rendered`` ou ``embedded`` (voir
    ``metadata.best_image``). Les dimensions sont celles de l'image telle
    qu'elle s'affiche, orientation EXIF appliquée.
    """
    image, source = metadata.best_image(path, max_side)
    if image is None:
        return None, source, 0, 0
    original_height, original_width = image.shape[:2]
    height, width = original_height, original_width
    scale = max_side / max(height, width)
    if scale < 1:
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return image, source, original_width, original_height


def create_app(det_size: int = 640):
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name="buffalo_l",
        providers=["CPUExecutionProvider"],
        # On ne charge que la détection et la reconnaissance : ~182 Mo au lieu de 325 Mo.
        allowed_modules=["detection", "recognition"],
    )
    app.prepare(ctx_id=-1, det_size=(det_size, det_size))
    return app


def collect_from_photos_db(database_path: Path, only_labelled: bool) -> list[Path]:
    conn = photos_db.connect(database_path, readonly=True)
    try:
        rows = photos_db.photo_paths(conn, only_labelled)
    finally:
        conn.close()
    paths = []
    for path in rows:
        try:
            relpath = face_db.to_relpath(path)
        except ValueError:
            continue
        candidate = face_db.abs_path(relpath)
        if candidate.is_file():
            paths.append(candidate)
    return paths


def collect_paths(directories: list[str]) -> list[Path]:
    paths = []
    for directory in directories:
        path = Path(directory).expanduser()
        if path.is_file():
            paths.append(path.resolve())
            continue
        if not path.is_dir():
            print(f"Ignoré (introuvable) : {directory}", flush=True)
            continue
        for candidate in sorted(path.rglob("*")):
            if candidate.suffix.lower() in IMAGE_EXTS and "thumbnail" not in candidate.parts:
                paths.append(candidate.resolve())
    return paths


def process_photo(
    app,
    conn,
    path: Path,
    relpath: str,
    regions: list[dict],
    name_index: dict[str, int],
    photo_map: dict[int, dict],
    max_side: int = 2048,
) -> dict:
    """Détecte, enregistre et étiquette une photo. Retourne les compteurs."""
    image, source, width, height = load_image(path, max_side)
    if image is None:
        raise ValueError("Image illisible")
    analysed_height, analysed_width = image.shape[:2]

    faces = []
    for detected in app.get(image):
        faces.append(
            {
                "bbox": (
                    float(detected.bbox[0] / analysed_width),
                    float(detected.bbox[1] / analysed_height),
                    float(detected.bbox[2] / analysed_width),
                    float(detected.bbox[3] / analysed_height),
                ),
                "det_score": float(detected.det_score),
                "embedding": detected.embedding,
            }
        )

    labels = []
    orphans = 0
    unknown = []
    # Un aperçu intégré peut être recadré ou déformé : on ne s'en sert pas pour
    # rattacher les régions XMP. Un rendu macOS, lui, couvre toute l'image.
    if source != "embedded":
        matches = match_regions(regions, faces)
        for face_index, name, score in matches:
            id_people = name_index.get(photos_db.normalize_name(name))
            if id_people is None:
                unknown.append(name)
                continue
            labels.append(
                {
                    "face_idx": face_index,
                    "id_people": id_people,
                    "name": photo_map[id_people]["label"],
                    "score": score,
                    "source": "xmp",
                }
            )
        orphans = len(regions) - len(matches)

    stat = path.stat()
    face_db.save_photo(
        conn,
        relpath,
        width=width,
        height=height,
        size=stat.st_size,
        mtime=stat.st_mtime,
        faces=faces,
        labels=labels,
    )
    return {
        "faces": len(faces),
        "labels": len(labels),
        "regions_sans_visage": orphans,
        "noms_inconnus": unknown,
    }


def index_paths(
    paths: list[Path],
    conn,
    app,
    name_index: dict[str, int],
    photo_map: dict[int, dict],
    root: Path | str = face_db.DEFAULT_PHOTO_ROOT,
    max_side: int = 2048,
    force: bool = False,
    progress=None,
) -> dict:
    """Indexe une liste de photos. Retourne les compteurs globaux.

    ``progress(done, total, stats)`` est appelé après chaque photo.
    """
    root = Path(root).expanduser().resolve()
    todo = []
    for path in paths:
        try:
            relpath = face_db.to_relpath(path, root)
        except ValueError:
            continue
        stat = path.stat()
        if force or not face_db.is_current(conn, relpath, stat.st_size, stat.st_mtime):
            todo.append((path, relpath, stat))

    stats = {
        "photos": 0,
        "visages": 0,
        "etiquetes": 0,
        "regions_sans_visage": 0,
        "noms_inconnus": 0,
        "erreurs": 0,
        "a_indexer": len(todo),
        "ignorees": len(paths) - len(todo),
        "erreurs_detail": [],
        "noms_inconnus_liste": [],
    }
    if not todo:
        return stats

    regions_by_path = metadata.read_regions([path for path, _, _ in todo])
    unknown_names: set[str] = set()

    for count, (path, relpath, stat) in enumerate(todo, start=1):
        try:
            result = process_photo(
                app,
                conn,
                path,
                relpath,
                regions_by_path.get(str(path.resolve()), []),
                name_index,
                photo_map,
                max_side,
            )
            stats["photos"] += 1
            stats["visages"] += result["faces"]
            stats["etiquetes"] += result["labels"]
            stats["regions_sans_visage"] += result["regions_sans_visage"]
            stats["noms_inconnus"] += len(result["noms_inconnus"])
            unknown_names.update(result["noms_inconnus"])
        except Exception as error:  # noqa: BLE001 - on veut continuer le lot
            face_db.save_error(conn, relpath, stat.st_size, stat.st_mtime, str(error)[:300])
            stats["erreurs"] += 1
            stats["erreurs_detail"].append({"photo": relpath, "message": str(error)[:300]})
        if progress is not None:
            progress(count, len(todo), stats)
    stats["noms_inconnus_liste"] = sorted(unknown_names)
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Indexe les visages dans face.db.")
    parser.add_argument("directories", nargs="*", help="Dossiers ou fichiers à indexer.")
    parser.add_argument("--from-db", action="store_true", help="Indexer les photos de photos.db.")
    parser.add_argument(
        "--only-labelled",
        action="store_true",
        help="Avec --from-db : ne prendre que les photos déjà étiquetées avec des personnes.",
    )
    parser.add_argument("--root", default=str(face_db.DEFAULT_PHOTO_ROOT), help="Racine du fonds photo.")
    parser.add_argument("--db", default=str(face_db.DEFAULT_DB_PATH), help="Base face.db.")
    parser.add_argument("--photos-db", default=str(face_db.DEFAULT_PHOTOS_DB), help="Base photos.db.")
    parser.add_argument("--force", action="store_true", help="Réindexer même les photos inchangées.")
    parser.add_argument("--limit", type=int, default=0, help="Limiter le nombre de photos (0 = tout).")
    parser.add_argument("--det-size", type=int, default=640, help="Taille de détection InsightFace.")
    parser.add_argument("--max-side", type=int, default=2048, help="Grand côté maximal de l'image analysée.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()

    print("Chargement du modèle…", flush=True)
    app = create_app(args.det_size)

    paths = (
        collect_from_photos_db(Path(args.photos_db), args.only_labelled)
        if args.from_db
        else collect_paths(args.directories)
    )
    if not paths:
        print("Aucune photo à indexer.", file=sys.stderr)
        return 1
    if args.limit:
        paths = paths[: args.limit]

    photo_map = {person["id_people"]: person for person in photos_db.load_people(args.photos_db)}
    name_index = photos_db.build_index(list(photo_map.values()))

    conn = face_db.connect(args.db)
    face_db.create_schema(conn)

    print(f"{len(paths)} photo(s) examinée(s)…", flush=True)

    from tqdm import tqdm

    bar = tqdm(total=0, desc="Indexation", unit="photo")

    def progress(done, total, stats):
        if bar.total != total:
            bar.total = total
            bar.refresh()
        bar.n = done
        bar.refresh()

    stats = index_paths(
        paths,
        conn,
        app,
        name_index,
        photo_map,
        root=root,
        max_side=args.max_side,
        force=args.force,
        progress=progress,
    )
    bar.close()

    print()
    print(f"Photos indexées      : {stats['photos']}")
    print(f"Visages détectés     : {stats['visages']}")
    print(f"Visages étiquetés    : {stats['etiquetes']}")
    print(f"Régions sans visage  : {stats['regions_sans_visage']}")
    print(f"Noms non reliés      : {stats['noms_inconnus']} ({len(stats['noms_inconnus_liste'])})")
    if stats["noms_inconnus_liste"]:
        print("  " + ", ".join(stats["noms_inconnus_liste"][:10]))
    print(f"Erreurs              : {stats['erreurs']}")
    print(f"Base : {args.db}")
    print(face_db.summary(conn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
