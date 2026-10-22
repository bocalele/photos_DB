#!/usr/bin/env python3
"""Reporte vers `photos.db` les noms de personnes lus dans les métadonnées XMP.

L'application écrit les noms dans les photos, pas dans `photos.db` : le site
reste donc en arrière. Ce script fait le chemin inverse, sans rien inventer :

- il lit les régions `XMP-mwg-rs:RegionInfo` de chaque photo ;
- il relie chaque nom à `Peoples` (comparaison sans casse ni accents) ;
- il ajoute les liens manquants dans `Photo_Peoples`.

Par défaut il n'enlève aucun lien. `--prune` retire aussi les liens des photos
traitées qui ne sont plus nommées dans le XMP (utile après une correction dans
l'application).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import face_db
import metadata
import people as people_module


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronise les noms XMP vers photos.db (table Photo_Peoples)."
    )
    parser.add_argument(
        "--photos-db",
        default=str(face_db.DEFAULT_PHOTOS_DB),
        help="Base du site (défaut : photos.db du fonds).",
    )
    parser.add_argument(
        "--root",
        default=str(face_db.DEFAULT_PHOTO_ROOT),
        help="Racine du fonds photo.",
    )
    parser.add_argument(
        "--scope",
        default="",
        help="Limiter à un sous-dossier (chemin relatif à la racine, ex. 2026/Angleterre).",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Retirer aussi les liens absents du XMP (corrections dans l'application).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Afficher sans écrire.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON, pour le bouton « Mettre à jour photos.db ».",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limiter le nombre de photos.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    photos_db = Path(args.photos_db).expanduser()
    root = Path(args.root).expanduser().resolve()
    if not photos_db.is_file():
        print(f"Base introuvable : {photos_db}", file=sys.stderr)
        return 1

    catalogue = people_module.load_people(photos_db)
    name_index = people_module.build_index(catalogue)

    with sqlite3.connect(photos_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id_photo, path FROM Photos").fetchall()
        id_by_relpath: dict[str, int] = {}
        for row in rows:
            try:
                relpath = face_db.to_relpath(row["path"], root)
            except ValueError:
                continue
            id_by_relpath[relpath] = int(row["id_photo"])

        links: dict[int, set[int]] = {}
        for row in conn.execute("SELECT id_photo, id_people FROM Photo_Peoples"):
            links.setdefault(int(row["id_photo"]), set()).add(int(row["id_people"]))

        wanted = sorted(
            relpath
            for relpath in id_by_relpath
            if relpath.startswith(args.scope) and face_db.abs_path(relpath, root).is_file()
        )
        if args.limit:
            wanted = wanted[: args.limit]
        if not wanted:
            if args.json:
                print(
                    json.dumps(
                        {
                            "photos": 0,
                            "changed": 0,
                            "added": 0,
                            "removed": 0,
                            "unknown": [],
                            "prune": args.prune,
                            "dryRun": args.dry_run,
                        },
                        ensure_ascii=False,
                    )
                )
                return 0
            print("Aucune photo à synchroniser.", file=sys.stderr)
            return 1

        paths = [face_db.abs_path(relpath, root) for relpath in wanted]
        regions = metadata.read_regions(paths)

        unknown: set[str] = set()
        changed = 0
        added = 0
        removed = 0
        operations: list[tuple[str, int, int]] = []
        for relpath, path in zip(wanted, paths):
            id_photo = id_by_relpath[relpath]
            names = {
                region["name"].strip()
                for region in regions.get(str(path.resolve()), [])
                if region.get("name", "").strip()
            }
            ids = set()
            for name in names:
                id_people = name_index.get(people_module.normalize_name(name))
                if id_people is None:
                    unknown.add(name)
                else:
                    ids.add(id_people)
            current = links.get(id_photo, set())
            missing = ids - current
            extra = current - ids if args.prune else set()
            if not missing and not extra:
                continue
            changed += 1
            added += len(missing)
            removed += len(extra)
            for id_people in sorted(missing):
                operations.append(("add", id_photo, id_people))
            for id_people in sorted(extra):
                operations.append(("del", id_photo, id_people))

        if not args.dry_run:
            with conn:
                for action, id_photo, id_people in operations:
                    if action == "add":
                        conn.execute(
                            "INSERT OR IGNORE INTO Photo_Peoples(id_photo, id_people) "
                            "VALUES (?, ?)",
                            (id_photo, id_people),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM Photo_Peoples WHERE id_photo = ? AND id_people = ?",
                            (id_photo, id_people),
                        )

    if args.json:
        print(
            json.dumps(
                {
                    "photos": len(wanted),
                    "changed": changed,
                    "added": added,
                    "removed": removed,
                    "unknown": sorted(unknown),
                    "prune": args.prune,
                    "dryRun": args.dry_run,
                },
                ensure_ascii=False,
            )
        )
        return 0

    prefix = " (simulation)" if args.dry_run else ""
    print(f"Photos examinées : {len(wanted)}{prefix}")
    print(f"Photos modifiées : {changed}")
    print(f"Liens ajoutés    : {added}")
    if args.prune:
        print(f"Liens retirés    : {removed}")
    if unknown:
        listed = ", ".join(sorted(unknown)[:10])
        print(f"Noms inconnus    : {len(unknown)} ({listed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
