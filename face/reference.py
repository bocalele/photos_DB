#!/usr/bin/env python3
"""Construit la galerie de référence (les 78 personnes connues) dans face.db.

Les étiquettes viennent des régions visage XMP posées dans Lightroom : chaque
visage détecté a été relié à un nom lors de l'indexation. On nettoie ces
étiquettes, on calcule un centroïde et des exemplaires par personne, puis on
calibre le seuil de reconnaissance.

La calibration est honnête : un visage n'est jamais comparé aux références de sa
propre photo. On mesure, pour chaque seuil, le rappel (visages retrouvés) et la
précision (propositions justes).

Exemple :
    pixi run python face/reference.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import face_db, photos_db  # noqa: E402

DEFAULT_MIN_IOU = 0.30
DEFAULT_MIN_DET = 0.60
DEFAULT_MIN_FACE = 0.03      # hauteur du visage, en fraction de la hauteur d'image
DEFAULT_MIN_FACES = 3        # exemplaires minimaux pour créer une référence
DEFAULT_TARGET_PRECISION = 0.97
DEFAULT_CONFLICT = 0.85      # similarité au-delà de laquelle deux étiquettes se contredisent
SUGGESTION_OFFSET = 0.10     # seuil « à vérifier » = seuil retenu - cet écart


def load_labelled(conn, min_iou: float, min_det: float, min_face: float):
    rows = face_db.labelled_faces(conn)
    kept, filtered = [], Counter()
    for row in rows:
        if float(row["score"] or 0) < min_iou:
            filtered["appariement région/visage faible"] += 1
        elif float(row["det_score"]) < min_det:
            filtered["détection faible"] += 1
        elif float(row["y2"] - row["y1"]) < min_face:
            filtered["visage trop petit"] += 1
        else:
            kept.append(row)
    return kept, filtered


def clean(rows, conflict_threshold: float):
    """Écarte les étiquettes contradictoires et les doublons photo/personne.

    Deux visages quasi identiques (même tirage scanné deux fois) étiquetés avec
    des personnes différentes sont une erreur de saisie : on écarte les deux.
    """
    vectors = np.stack([face_db.unpack(row["embedding"]) for row in rows])
    people = np.array([row["id_people"] for row in rows])
    photos = np.array([row["relpath"] for row in rows])

    similarities = vectors @ vectors.T
    contradictory = (
        (similarities >= conflict_threshold) & (people[:, None] != people[None, :])
    ).any(axis=1)

    seen = set()
    keep = []
    duplicates = 0
    for index, (photo, person) in enumerate(zip(photos, people)):
        if contradictory[index]:
            continue
        key = (photo, person)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        keep.append(index)

    keep = np.array(keep, dtype=int)
    stats = {
        "conflits": int(contradictory.sum()),
        "doublons_photo": duplicates,
    }
    return vectors[keep], people[keep], photos[keep], keep, stats


def scores_per_person(vectors, people, photos, ids):
    """Score de chaque visage pour chaque personne, hors de sa propre photo.

    Le score d'une personne est la meilleure similarité avec l'un de ses
    exemplaires de référence.
    """
    similarities = vectors @ vectors.T
    similarities[photos[:, None] == photos[None, :]] = -np.inf
    return np.stack(
        [similarities[:, people == id_people].max(axis=1) for id_people in ids], axis=1
    )


def calibrate(scores, people, ids, target_precision: float):
    position = {id_people: index for index, id_people in enumerate(ids)}
    ids_array = np.array(ids)
    total = len(people)

    best = scores.argmax(axis=1)
    best_score = scores[np.arange(total), best]
    predicted = ids_array[best]
    correct = predicted == people

    own = scores[np.arange(total), [position[person] for person in people]]
    evaluated = np.isfinite(own)

    table = []
    for threshold in np.arange(0.25, 0.76, 0.01):
        proposed = evaluated & (best_score >= threshold)
        count = int(proposed.sum())
        if not count:
            break
        table.append(
            {
                "seuil": round(float(threshold), 2),
                "propositions": count,
                "rappel": round(float((proposed & correct).sum()) / total, 4),
                "precision": round(float(correct[proposed].mean()), 4),
            }
        )

    eligible = [row for row in table if row["precision"] >= target_precision]
    chosen = min(eligible, key=lambda row: row["seuil"]) if eligible else table[-1]
    return {
        "table": table,
        "top1": float(correct[evaluated].mean()),
        "evaluated": int(evaluated.sum()),
        "threshold": chosen["seuil"],
        "recall": chosen["rappel"],
        "precision": chosen["precision"],
        "propositions": chosen["propositions"],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Construit la galerie de référence des visages.")
    parser.add_argument("--db", default=str(face_db.DEFAULT_DB_PATH), help="Base face.db.")
    parser.add_argument("--photos-db", default=str(face_db.DEFAULT_PHOTOS_DB), help="Base photos.db.")
    parser.add_argument("--min-iou", type=float, default=DEFAULT_MIN_IOU, help="IoU minimal avec la région XMP.")
    parser.add_argument("--min-det", type=float, default=DEFAULT_MIN_DET, help="Score de détection minimal.")
    parser.add_argument("--min-face", type=float, default=DEFAULT_MIN_FACE, help="Hauteur minimale du visage (fraction de l'image).")
    parser.add_argument("--min-faces", type=int, default=DEFAULT_MIN_FACES, help="Exemplaires minimaux pour créer une référence.")
    parser.add_argument("--conflict", type=float, default=DEFAULT_CONFLICT, help="Similarité qui rend deux étiquettes contradictoires.")
    parser.add_argument("--target-precision", type=float, default=DEFAULT_TARGET_PRECISION, help="Précision visée sur les propositions.")
    parser.add_argument("--dry-run", action="store_true", help="Afficher sans écrire dans la base.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = face_db.connect(args.db)
    face_db.create_schema(conn)

    rows, filtered = load_labelled(conn, args.min_iou, args.min_det, args.min_face)
    if not rows:
        print(
            "Aucun visage étiqueté. Lancez d'abord : "
            "pixi run python face/index.py --from-db --only-labelled",
            file=sys.stderr,
        )
        return 1

    vectors, people, photos, keep, clean_stats = clean(rows, args.conflict)
    ids = sorted(set(people))
    print(f"Visages étiquetés : {len(rows)}")
    if filtered:
        print("  écartés : " + ", ".join(f"{key} {value}" for key, value in filtered.items()))
    print(f"  étiquettes contradictoires : {clean_stats['conflits']}")
    print(f"  doublons photo/personne    : {clean_stats['doublons_photo']}")
    print(f"Visages retenus : {len(vectors)}  ({len(ids)} personnes)")

    scores = scores_per_person(vectors, people, photos, ids)
    result = calibrate(scores, people, ids, args.target_precision)

    print()
    print(f"Bonne personne au 1er rang : {result['top1']:.1%} "
          f"(sur {result['evaluated']} visages évaluables)")
    print("Seuil  Rappel  Précision  Propositions")
    for row in result["table"]:
        if abs(row["seuil"] * 100 % 5) < 1e-6:
            print(f"{row['seuil']:.2f}   {row['rappel']:.3f}   {row['precision']:.3f}      {row['propositions']}")
    print()
    print(f"Seuil retenu      : {result['threshold']:.2f} "
          f"(précision visée {args.target_precision:.0%})")
    print(f"  rappel          : {result['recall']:.1%}")
    print(f"  précision       : {result['precision']:.1%}")
    print(f"  propositions    : {result['propositions']} / {len(vectors)}")
    print(f"Seuil « à vérifier » : {max(0.2, result['threshold'] - SUGGESTION_OFFSET):.2f}")

    if result["evaluated"] < len(vectors):
        print(f"\nNote : {len(vectors) - result['evaluated']} visage(s) sans référence "
              "hors de leur propre photo (non évaluables).")

    if args.dry_run:
        print("\nMode --dry-run : rien n'a été écrit.")
        return 0

    counts = Counter(people)
    people_info = {
        person["id_people"]: person for person in photos_db.load_people(args.photos_db)
    }
    weak = sorted(
        (counts[id_people], people_info.get(id_people, {}).get("label", str(id_people)))
        for id_people in ids
        if counts[id_people] < 5
    )
    ignored = [person for person in ids if counts[person] < args.min_faces]

    people_rows: list[tuple] = []
    face_rows: list[tuple] = []
    written_ids: set[int] = set()
    for id_people in ids:
        count = counts[id_people]
        if count < args.min_faces:
            continue
        subset = vectors[people == id_people]
        people_rows.append(
            (
                int(id_people),
                people_info.get(int(id_people), {}).get("label", str(id_people)),
                int(count),
                face_db.pack(subset.mean(axis=0)),
                float(result["threshold"]),
                face_db.now(),
            )
        )
        written_ids.add(int(id_people))
    # Exemplaires : on insère les visages retenus par le nettoyage.
    for position, offset in enumerate(keep):
        id_people = int(people[position])
        if id_people not in written_ids:
            continue
        face_rows.append(
            (id_people, rows[offset]["relpath"], rows[offset]["embedding"])
        )
    stats = {
        "seuil": result["threshold"],
        "rappel": result["recall"],
        "precision": result["precision"],
        "top1": round(result["top1"], 4),
        "visages_reference": len(vectors),
        "calibre_le": face_db.now(),
    }
    metadata = {
        "reference_threshold": f"{result['threshold']:.4f}",
        "reference_suggestion": f"{max(0.2, result['threshold'] - SUGGESTION_OFFSET):.4f}",
        "reference_stats": json.dumps(stats, ensure_ascii=False),
    }
    face_db.replace_reference(conn, people_rows, face_rows, metadata)
    written = len(people_rows)

    print(f"\nGalerie de référence : {written} personne(s) enregistrée(s).")
    if ignored:
        print(f"  {len(ignored)} personne(s) ignorée(s) (< {args.min_faces} visages) : "
              + ", ".join(label for _, label in weak[:6]) + ("…" if len(weak) > 6 else ""))
    print(f"Base : {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
