#!/usr/bin/env python3
"""Reconnaît les personnes connues sur des photos, sans rien écrire.

Sert à vérifier la galerie de référence et à mesurer ce qui sera proposé dans
l'interface. Chaque visage est comparé aux exemplaires de référence ; la
meilleure similarité donne une proposition, classée en trois états :

    reconnu      score >= seuil
    à vérifier   score >= seuil de suggestion
    inconnu      en dessous

Exemples :
    pixi run python face/recognize.py "/Volumes/WD/PHOTOS/2015/Queyras"
    pixi run python face/recognize.py --from-db --limit 50
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np

import face_db
import index
import metadata


def load_reference(conn) -> tuple[list[int], list[str], np.ndarray]:
    rows = conn.execute(
        """
        SELECT r.id_people, r.name, f.embedding
        FROM RefFace f JOIN RefPerson r ON r.id_people = f.id_people
        ORDER BY r.id_people
        """
    ).fetchall()
    if not rows:
        raise SystemExit(
            "Galerie vide. Lancez d'abord : pixi run python face/reference.py"
        )
    ids = sorted({row["id_people"] for row in rows})
    names = {row["id_people"]: row["name"] for row in rows}
    by_person = {id_people: [] for id_people in ids}
    for row in rows:
        by_person[row["id_people"]].append(face_db.unpack(row["embedding"]))
    return ids, [names[id_people] for id_people in ids], by_person


def scores_for(embedding, ids, by_person) -> np.ndarray:
    scores = np.empty(len(ids), dtype=np.float32)
    for position, id_people in enumerate(ids):
        reference = np.stack(by_person[id_people])
        scores[position] = float((reference @ embedding).max())
    return scores


def thresholds(conn, args) -> tuple[float, float]:
    values = {
        row["key"]: row["value"]
        for row in conn.execute(
            "SELECT key, value FROM Meta WHERE key IN ('reference_threshold', 'reference_suggestion')"
        )
    }
    validated = float(values.get("reference_threshold", args.threshold))
    suggestion = float(values.get("reference_suggestion", args.suggestion))
    return (args.threshold if args.threshold is not None else validated,
            args.suggestion if args.suggestion is not None else suggestion)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconnaît les personnes connues sur des photos.")
    parser.add_argument("directories", nargs="*", help="Dossiers ou fichiers à analyser.")
    parser.add_argument("--from-db", action="store_true", help="Analyser les photos de photos.db.")
    parser.add_argument("--root", default=str(face_db.DEFAULT_PHOTO_ROOT))
    parser.add_argument("--db", default=str(face_db.DEFAULT_DB_PATH))
    parser.add_argument("--photos-db", default=str(face_db.DEFAULT_PHOTOS_DB))
    parser.add_argument("--threshold", type=float, help="Seuil de reconnaissance (défaut : calibration).")
    parser.add_argument("--suggestion", type=float, help="Seuil « à vérifier » (défaut : calibration).")
    parser.add_argument("--top", type=int, default=1, help="Nombre de propositions affichées par visage.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--det-size", type=int, default=640)
    parser.add_argument("--max-side", type=int, default=2048)
    parser.add_argument("--show-xmp", action="store_true", help="Comparer avec les noms XMP déjà présents.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()

    conn = face_db.connect(args.db)
    ids, names, by_person = load_reference(conn)
    threshold, suggestion = thresholds(conn, args)
    print(f"Galerie : {len(ids)} personnes, {sum(len(v) for v in by_person.values())} exemplaires")
    print(f"Seuils  : reconnu >= {threshold:.2f}   à vérifier >= {suggestion:.2f}\n")

    paths = (
        index.collect_from_photos_db(Path(args.photos_db), only_labelled=False)
        if args.from_db
        else index.collect_paths(args.directories)
    )
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print("Aucune photo à analyser.", file=sys.stderr)
        return 1

    regions_by_path = metadata.read_regions(paths) if args.show_xmp else {}
    app = index.create_app(args.det_size)

    counts = {"reconnu": 0, "à vérifier": 0, "inconnu": 0, "juste": 0, "faux": 0}
    print(f"{'photo':34s} {'#':>2s}  {'proposition':30s} {'score':>5s}  état")
    for path in paths:
        try:
            image, _, _, _ = index.load_image(path, args.max_side)
            if image is None:
                print(f"{path.name:34s} illisible")
                continue
            height, width = image.shape[:2]
            faces = app.get(image)
            regions = regions_by_path.get(str(path.resolve()), [])
            labels = index.match_regions(regions, [
                {"bbox": (f.bbox[0] / width, f.bbox[1] / height,
                          f.bbox[2] / width, f.bbox[3] / height)}
                for f in faces
            ])
            label_by_face = {face_index: name for face_index, name, _ in labels}
        except Exception as error:  # noqa: BLE001
            print(f"{path.name:34s} erreur : {error}")
            continue

        relpath = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        for face_index, detected in enumerate(faces):
            embedding = face_db.normalize(detected.embedding)
            scores = scores_for(embedding, ids, by_person)
            order = np.argsort(-scores)[: max(1, args.top)]
            for rank, position in enumerate(order):
                score = float(scores[position])
                state = "reconnu" if score >= threshold else ("à vérifier" if score >= suggestion else "inconnu")
                if rank == 0:
                    counts[state] += 1
                expected = label_by_face.get(face_index)
                suffix = ""
                if args.show_xmp and rank == 0:
                    if expected is None:
                        suffix = "  (XMP: —)"
                    elif expected == names[position]:
                        counts["juste"] += 1
                        suffix = "  (XMP: ok)"
                    else:
                        counts["faux"] += 1
                        suffix = f"  (XMP: {expected})"
                prefix = f"{relpath[-34:]:34s} {face_index:2d}  " if rank == 0 else " " * 39
                print(f"{prefix}{names[position][:30]:30s} {score:5.2f}  {state}{suffix}")

    print()
    print(f"Visages           : {sum(counts[k] for k in ('reconnu', 'à vérifier', 'inconnu'))}")
    print(f"  reconnus        : {counts['reconnu']}")
    print(f"  à vérifier      : {counts['à vérifier']}")
    print(f"  inconnus        : {counts['inconnu']}")
    if args.show_xmp:
        compared = counts["juste"] + counts["faux"]
        if compared:
            print(f"Accord avec XMP  : {counts['juste']}/{compared} "
                  f"({counts['juste'] / compared:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
