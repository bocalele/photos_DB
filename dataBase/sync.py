#!/usr/bin/env python3
"""Réinitialise ou met à jour `photos.db` depuis les métadonnées XMP/EXIF.

Deux commandes, un seul script :

    python -m dataBase.sync reset
    python -m dataBase.sync update /Volumes/WD/PHOTOS/2026/Angleterre/PSD

``reset`` sauvegarde l'ancienne base, crée le schéma courant et reprend le
catalogue des personnes (noms et liens familiaux) par défaut. Toutes les
photos, mots-clés, taxons et lieux sont supprimés.

``update`` ne parcourt que le dossier ou le fichier demandé. Il met à jour les
photos existantes, recrée leurs liens personnes/mots-clés/taxons/lieux et
supprime les mots-clés devenus inutiles. Les autres dossiers de la base ne sont
pas relus.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import annotations, photos_db  # noqa: E402
from tools import environment, preferences  # noqa: E402


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #


def _target(args) -> Path:
    return Path(args.db).expanduser().resolve() if args.db else photos_db.path()


def _root(args) -> Path:
    return (
        Path(args.root).expanduser().resolve()
        if getattr(args, "root", None)
        else environment.photos_root().resolve()
    )


def _backup(source: Path) -> Path | None:
    if not source.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = source.with_name(f"{source.stem}.backup-{stamp}{source.suffix}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_conn, sqlite3.connect(destination) as destination_conn:
        source_conn.backup(destination_conn)
    return destination


def _remove_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def _people_rows(database: Path) -> list[sqlite3.Row]:
    if not database.is_file():
        return []
    try:
        conn = sqlite3.connect(database)
        conn.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "people" in tables:
            rows = conn.execute("SELECT * FROM people ORDER BY id").fetchall()
        elif "Peoples" in tables:
            rows = conn.execute("SELECT * FROM Peoples ORDER BY id_people").fetchall()
        else:
            rows = []
        conn.close()
        return rows
    except sqlite3.Error:
        return []


def _copy_people(conn: sqlite3.Connection, database: Path) -> int:
    rows = _people_rows(database)
    if not rows:
        return 0
    inserted = 0
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for row in rows:
            keys = set(row.keys())
            person_id = int(row["id_people"] if "id_people" in keys else row["id"])
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO people(id, prenom, nom, surnom, id_pere, id_mere) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        person_id,
                        str(row["prenom"] or ""),
                        str(row["nom"] or ""),
                        str(row["surnom"] or ""),
                        row["id_pere"] if "id_pere" in keys else None,
                        row["id_mere"] if "id_mere" in keys else None,
                    ),
                )
                inserted += 1
            except (sqlite3.Error, TypeError, ValueError):
                continue
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    return inserted


def _scope_files(scope: Path, root: Path, limit: int = 0):
    from dataBase import rebuild  # pandas/ExifTool uniquement pour update

    if scope.is_file():
        if scope.suffix.lower() not in rebuild.IMAGE_EXTENSIONS:
            raise ValueError(f"Format d'image non pris en charge : {scope.name}")
        try:
            scope.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{scope} n'est pas sous {root}") from exc
        return [scope]
    if not scope.is_dir():
        raise FileNotFoundError(scope)
    try:
        scope.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{scope} n'est pas sous {root}") from exc
    return list(rebuild.iter_files(scope, limit))


def _scope_relpath(scope: Path, root: Path) -> str:
    try:
        value = scope.relative_to(root).as_posix()
    except ValueError:
        return ""
    return "" if value == "." else value


def _purge_taxonomy_keywords(conn: sqlite3.Connection, thesaurus, taxonomy=None) -> int:
    if thesaurus is None and taxonomy is None:
        return 0
    taxa_keys = set()
    for row in conn.execute("SELECT name, scientific_name FROM taxa"):
        for value in (row["name"], row["scientific_name"]):
            key = annotations.normalize_name(value)
            if key:
                taxa_keys.add(key)
    removed = 0
    for row in conn.execute("SELECT id, label FROM keywords").fetchall():
        key = annotations.normalize_name(row["label"])
        if key in taxa_keys or annotations.is_taxonomy_text(
            thesaurus, row["label"], taxonomy
        ):
            conn.execute("DELETE FROM photo_keywords WHERE keyword_id = ?", (row["id"],))
            conn.execute("DELETE FROM keywords WHERE id = ?", (row["id"],))
            removed += 1
    conn.commit()
    return removed


# --------------------------------------------------------------------------- #
# Commandes
# --------------------------------------------------------------------------- #


def reset(args) -> dict:
    target = _target(args)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.is_file() and not args.no_backup:
        backup = _backup(target)
    _remove_database(target)

    conn = photos_db.connect(target)
    try:
        photos_db.create_schema(conn)
        people = 0 if args.empty_people else _copy_people(conn, backup) if backup else 0
        photos_db.set_meta(conn, "reset_at", photos_db.now_iso())
        photos_db.set_meta(conn, "source_root", str(_root(args)))
        if backup is not None:
            photos_db.set_meta(conn, "reset_from", str(backup))
        conn.commit()
    finally:
        conn.close()

    return {
        "database": str(target),
        "backup": str(backup) if backup else None,
        "people": people,
        "photos": 0,
        "keywords": 0,
        "taxa": 0,
        "locations": 0,
    }


def update(args) -> dict:
    from dataBase import rebuild  # pandas/ExifTool uniquement pour update

    target = _target(args)
    root = _root(args)
    if not target.is_file():
        raise FileNotFoundError(
            f"Base introuvable : {target}. Exécutez d'abord `python -m dataBase.sync reset`."
        )
    if not root.is_dir():
        raise FileNotFoundError(f"Racine photo introuvable : {root}")

    raw_scope = Path(args.path).expanduser()
    if raw_scope.is_absolute():
        scope = raw_scope.resolve()
    else:
        local = (Path.cwd() / raw_scope).resolve()
        under_root = (root / raw_scope).resolve()
        scope = local if local.exists() else under_root
    metadata_revision = preferences.metadata_revision()
    files = _scope_files(scope, root, args.limit)
    taxonomy = annotations.load_taxonomy(
        args.taxref or environment.taxref_dir(),
        args.thesaurus or environment.thesaurus_dir(),
    )
    thesaurus = getattr(taxonomy, "thesaurus", None) if taxonomy is not None else None
    if thesaurus is None:
        thesaurus = annotations.load_thesaurus(
            args.thesaurus or environment.thesaurus_dir()
        )

    conn = photos_db.connect(target)
    stats = {
        "photos": 0,
        "errors": 0,
        "people_links": 0,
        "keyword_links": 0,
        "taxa_links": 0,
        "photos_with_people": 0,
        "photos_with_taxa": 0,
        "pruned_photos": 0,
        "pruned_keywords": 0,
        "taxonomy_keywords_removed": 0,
    }
    try:
        if args.dry_run:
            return {
                "database": str(target),
                "scope": str(scope),
                "root": str(root),
                "files": len(files),
                "thesaurus": thesaurus is not None,
                "taxref": taxonomy is not None,
                "dry_run": True,
            }

        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA cache_size = -65536")
        person_index = photos_db.build_index(
            photos_db.load_people_from_connection(conn)
        )
        started = time.monotonic()
        batch: list[Path] = []
        for index, path in enumerate(files, start=1):
            batch.append(path)
            if len(batch) >= 100:
                _process_batch(
                    conn, batch, root, thesaurus, person_index, stats,
                    taxonomy=taxonomy,
                )
                batch = []
                _progress(args, index, len(files), stats, started)
        if batch:
            _process_batch(
                conn, batch, root, thesaurus, person_index, stats,
                taxonomy=taxonomy,
            )

        if args.prune:
            stats["pruned_photos"] = _prune_missing(conn, scope, root)
        stats["taxonomy_keywords_removed"] = _purge_taxonomy_keywords(
            conn, thesaurus, taxonomy
        )
        stats["pruned_keywords"] = photos_db.prune_unused_keywords(conn)

        photo_count = int(conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
        photos_db.set_meta(conn, "source_root", str(root))
        photos_db.set_meta(conn, "last_update_at", photos_db.now_iso())
        photos_db.set_meta(conn, "last_update_scope", _scope_relpath(scope, root))
        # La révision est lue avant le parcours : une écriture qui arrive
        # pendant la synchronisation laisse volontairement la base « sale ».
        photos_db.set_meta(conn, "synced_revision", str(metadata_revision))
        photos_db.set_meta(conn, "synced_scope", _scope_relpath(scope, root))
        photos_db.set_meta(conn, "last_sync_at", photos_db.now_iso())
        photos_db.set_meta(
            conn, "sync_complete", "1" if stats["errors"] == 0 else "0"
        )
        photos_db.set_meta(conn, "sync_errors", str(stats["errors"]))
        photos_db.set_meta(conn, "photo_count", str(photo_count))
        conn.commit()
    finally:
        conn.close()

    return {
        "database": str(target),
        "scope": str(scope),
        "root": str(root),
        "files": len(files),
        "thesaurus": thesaurus is not None,
        "taxref": taxonomy is not None,
        "photos": stats["photos"],
        "errors": stats["errors"],
        "people_links": stats["people_links"],
        "keyword_links": stats["keyword_links"],
        "taxa_links": stats["taxa_links"],
        "pruned_photos": stats["pruned_photos"],
        "pruned_keywords": stats["pruned_keywords"],
        "taxonomy_keywords_removed": stats["taxonomy_keywords_removed"],
        "total_photos": photo_count,
        "synced_revision": metadata_revision,
        "synced_scope": _scope_relpath(scope, root),
        "sync_complete": stats["errors"] == 0,
    }


def _process_batch(
    conn, batch, root, thesaurus, person_index, stats, taxonomy=None
) -> None:
    from dataBase import rebuild

    try:
        with photos_db.deferred_commits():
            rebuild.process_batch(
                conn, batch, root, thesaurus, person_index, {}, stats,
                taxonomy=taxonomy,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _progress(args, index: int, total: int, stats: dict, started: float) -> None:
    if not total:
        return
    elapsed = max(time.monotonic() - started, 0.001)
    message = (
        f"  {index}/{total} photos - {index / elapsed:.1f} fichiers/s "
        f"- {stats['errors']} erreur(s)"
    )
    print(message, file=sys.stderr if args.json else sys.stdout, flush=True)


def _prune_missing(conn: sqlite3.Connection, scope: Path, root: Path) -> int:
    rel_scope = _scope_relpath(scope, root)
    if rel_scope:
        rows = conn.execute(
            "SELECT id, relpath FROM photos WHERE relpath = ? OR relpath LIKE ? ESCAPE '\\'",
            (rel_scope, _like_escape(rel_scope) + "/%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT id, relpath FROM photos").fetchall()
    missing = []
    for row in rows:
        if not (root / row["relpath"]).is_file():
            missing.append(int(row["id"]))
    if missing:
        conn.executemany("DELETE FROM photos WHERE id = ?", [(value,) for value in missing])
        conn.commit()
    return len(missing)


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    reset_parser = subparsers.add_parser(
        "reset", help="Sauvegarde puis réinitialise la base (personnes conservées)."
    )
    reset_parser.add_argument("--db", help="Base cible (défaut : preferences.json).")
    reset_parser.add_argument("--root", help="Racine photo (défaut : preferences.json).")
    reset_parser.add_argument("--no-backup", action="store_true", help="Ne pas sauvegarder l'ancienne base.")
    reset_parser.add_argument(
        "--empty-people", action="store_true", help="Ne pas reprendre le catalogue des personnes."
    )
    reset_parser.add_argument("--json", action="store_true", help="Sortie JSON.")

    update_parser = subparsers.add_parser(
        "update", help="Met à jour la base pour un dossier ou un fichier."
    )
    update_parser.add_argument("path", help="Dossier ou fichier photo à synchroniser.")
    update_parser.add_argument("--db", help="Base cible (défaut : preferences.json).")
    update_parser.add_argument("--root", help="Racine photo (défaut : preferences.json).")
    update_parser.add_argument("--thesaurus", help="Dossier des thésaurus.")
    update_parser.add_argument("--taxref", help="Dossier TAXREF.")
    update_parser.add_argument("--limit", type=int, default=0, help="Limite le nombre de fichiers (test).")
    update_parser.add_argument(
        "--prune", action="store_true", help="Retire de la base les photos absentes du disque dans la portée."
    )
    update_parser.add_argument("--dry-run", action="store_true", help="Compte les fichiers sans écrire.")
    update_parser.add_argument("--json", action="store_true", help="Sortie JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = reset(args) if args.command == "reset" else update(args)
    except (FileNotFoundError, ValueError, photos_db.SchemaError) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "reset":
        print(f"Base réinitialisée : {result['database']}")
        if result["backup"]:
            print(f"Sauvegarde         : {result['backup']}")
        print(f"Personnes reprises : {result['people']}")
    else:
        print(
            f"Mise à jour terminée : {result['photos']} photo(s), "
            f"{result['keyword_links']} lien(s) mot-clé, "
            f"{result['taxa_links']} lien(s) taxon, {result['errors']} erreur(s)."
        )
        print(f"Base : {result['database']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
