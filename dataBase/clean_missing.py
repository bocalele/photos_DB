#!/usr/bin/env python3
"""Liste ou supprime les photos de ``photos.db`` absentes du disque.

Trois modes, sans jamais toucher aux fichiers photo :

    # Mode dry-run (par défaut) : affiche les identifiants et les chemins
    python -m dataBase.clean_missing
    python -m dataBase.clean_missing --output-file ids.txt

    # Supprime une liste d'identifiants, uniquement s'ils sont absents
    python -m dataBase.clean_missing --ids 12 34 56 --yes
    python -m dataBase.clean_missing --ids-file ids.txt --yes

    # Supprime toutes les photos absentes
    python -m dataBase.clean_missing --all --yes

Le dry-run écrit une liste ``id<TAB>chemin`` sur la sortie standard (le résumé
va sur la sortie d'erreur), donc ``> ids.txt`` produit directement un fichier
utilisable par ``--ids-file``. L'option ``--output-file`` écrit la même liste
dans un fichier sans redirection.

Si ``--all`` supprimerait toute la base (racine suspecte, disque vide), le
script refuse sans ``--force``.

La racine servant à vérifier l'existence des fichiers est lue dans
``meta.source_root`` de la base, ou fournie avec ``--root``. Si elle n'existe
pas, le script refuse d'agir : un disque externe débranché ne doit pas être
pris pour un disque vide.

Les suppressions ne touchent que ``photos`` ; les liens ``photo_people``,
``photo_keywords`` et ``photo_taxa`` sont supprimés en cascade par SQLite.
``meta.photo_count`` est remis à jour à la fin.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import photos_db  # noqa: E402
from tools import environment  # noqa: E402


# --------------------------------------------------------------------------- #
# Lecture de la base
# --------------------------------------------------------------------------- #


def _database_path(value: str | None) -> Path:
    target = Path(value).expanduser() if value else environment.photos_db()
    target = target.expanduser()
    if not target.is_file():
        raise FileNotFoundError(f"Base photos.db introuvable : {target}")
    return target


def _resolve_root(conn: sqlite3.Connection, value: str | None) -> Path:
    if value:
        root = Path(value).expanduser().resolve()
    else:
        stored = photos_db.get_meta(conn, "source_root")
        root = (
            Path(stored).expanduser().resolve()
            if stored
            else environment.photos_root().expanduser().resolve()
        )
    if not root.is_dir():
        raise FileNotFoundError(
            f"Racine photo introuvable : {root}. "
            "Vérifiez le disque ou utilisez --root."
        )
    return root


def _is_missing(root: Path, relpath: str) -> bool:
    try:
        return not (root / relpath).is_file()
    except OSError:
        return True


def missing_photos(conn: sqlite3.Connection, root: Path) -> list[dict]:
    """Retourne les photos de la base dont le fichier n'existe pas."""
    rows = conn.execute("SELECT id, relpath FROM photos ORDER BY id").fetchall()
    return [
        {"id": int(row["id"]), "relpath": str(row["relpath"])}
        for row in rows
        if _is_missing(root, str(row["relpath"]))
    ]


def _total_photos(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])


def _rows_by_id(conn: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    found: dict[int, dict] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, relpath FROM photos WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            found[int(row["id"])] = {
                "id": int(row["id"]),
                "relpath": str(row["relpath"]),
            }
    return found


def _dedupe_ids(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def parse_ids(values: list[str]) -> list[int]:
    """Accepte ``12,34``, ``12 34`` ou plusieurs occurrences de --ids."""
    result: list[int] = []
    for value in values:
        for token in re.split(r"[\s,;]+", str(value).strip()):
            if not token:
                continue
            try:
                result.append(int(token))
            except ValueError as error:
                raise ValueError(f"Identifiant invalide : {token!r}") from error
    return _dedupe_ids(result)


def parse_ids_file(path: Path) -> list[int]:
    """Lit un fichier ``id<TAB>chemin``, ``id chemin`` ou ``id`` par ligne.

    C'est le format produit par le dry-run et par ``--output-file``. Les
    lignes vides, les commentaires ``#`` et l'en-tête ``id path`` sont ignorés.
    Une ligne peut aussi contenir plusieurs identifiants séparés par des
    espaces ou des virgules.
    """
    try:
        lines = path.expanduser().read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise FileNotFoundError(f"Fichier d'identifiants introuvable : {path}") from error

    result: list[int] = []
    for number, raw_line in enumerate(lines, start=1):
        line = raw_line.lstrip("\ufeff").split("#", 1)[0].strip()
        if not line:
            continue
        # Format attendu : ``id<TAB>chemin``, ``id chemin`` ou simplement ``id``.
        first = re.split(r"[\s,;]+", line)[0]
        if first.casefold() in {"id", "identifiant"}:
            continue
        if re.fullmatch(r"[\d\s,;]+", line):
            tokens = re.split(r"[\s,;]+", line)
        else:
            tokens = [first]
        for token in tokens:
            if not token:
                continue
            try:
                result.append(int(token))
            except ValueError as error:
                raise ValueError(
                    f"Identifiant invalide ligne {number} : {token!r} "
                    "(format attendu : id<TAB>chemin)"
                ) from error
    return _dedupe_ids(result)


# --------------------------------------------------------------------------- #
# Suppression
# --------------------------------------------------------------------------- #


def _update_photo_count(conn: sqlite3.Connection) -> int:
    total = _total_photos(conn)
    photos_db.set_meta(conn, "photo_count", str(total))
    return total


def _confirm(count: int, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise ValueError(
            "Suppression non interactive : ajoutez --yes pour confirmer."
        )
    print(
        f"Supprimer {count} photo(s) absente(s) de photos.db ? [y/N] ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    answer = sys.stdin.readline().strip().casefold()
    return answer in {"y", "yes", "o", "oui"}


def _delete_rows(
    conn: sqlite3.Connection,
    rows: list[dict],
    assume_yes: bool,
) -> tuple[list[dict], bool]:
    if not rows:
        return [], False
    if not _confirm(len(rows), assume_yes):
        return [], True
    conn.executemany(
        "DELETE FROM photos WHERE id = ?",
        [(int(row["id"]),) for row in rows],
    )
    conn.commit()
    return rows, False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", help="Base photos.db (défaut : preferences.json).")
    parser.add_argument(
        "--root",
        help="Racine photo utilisée pour vérifier les chemins "
             "(défaut : meta.source_root, puis preferences.json).",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche les photos absentes sans rien supprimer (défaut).",
    )
    modes.add_argument(
        "--ids",
        nargs="+",
        metavar="ID",
        help="Identifiants à supprimer s'ils sont absents du disque.",
    )
    modes.add_argument(
        "--ids-file",
        type=Path,
        metavar="FICHIER",
        help="Fichier d'identifiants à supprimer (un par ligne).",
    )
    modes.add_argument(
        "--all",
        action="store_true",
        help="Supprime toutes les photos absentes du disque.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Ne pas demander de confirmation avant suppression.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Autorise --all même si toutes les photos de la base sont absentes.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        metavar="FICHIER",
        help="Écrit la liste id<TAB>chemin du dry-run dans FICHIER.",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON.")
    return parser.parse_args(argv)


def _result_base(database: Path, root: Path) -> dict:
    return {
        "database": str(database),
        "root": str(root),
    }


def _id_path_lines(rows: list[dict]) -> list[str]:
    return ["# id\tpath", *[f"{row['id']}\t{row['relpath']}" for row in rows]]


def _write_id_paths(path: Path, rows: list[dict]) -> Path:
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(_id_path_lines(rows)) + "\n", encoding="utf-8")
    return target


def _run_dry_run(conn: sqlite3.Connection, root: Path, base: dict) -> dict:
    missing = missing_photos(conn, root)
    return {
        **base,
        "mode": "dry-run",
        "dryRun": True,
        "total": _total_photos(conn),
        "missing": missing,
        "missingCount": len(missing),
        "deleted": [],
        "deletedCount": 0,
        "skippedPresent": [],
        "unknownIds": [],
        "cancelled": False,
        "remaining": _total_photos(conn),
    }


def _run_ids(
    database: Path,
    root: Path,
    base: dict,
    ids: list[int],
    assume_yes: bool,
) -> dict:
    conn = photos_db.connect(database)
    try:
        total_before = _total_photos(conn)
        all_missing = missing_photos(conn, root)
        rows = _rows_by_id(conn, ids)
        unknown = [value for value in ids if value not in rows]
        present = []
        missing = []
        for value in ids:
            row = rows.get(value)
            if row is None:
                continue
            if _is_missing(root, row["relpath"]):
                missing.append(row)
            else:
                present.append(row)
        deleted, cancelled = _delete_rows(conn, missing, assume_yes)
        remaining = _update_photo_count(conn) if deleted else total_before
        return {
            **base,
            "mode": "ids",
            "dryRun": False,
            "requestedIds": ids,
            "total": total_before,
            "missing": all_missing,
            "missingCount": len(all_missing),
            "deleted": deleted,
            "deletedCount": len(deleted),
            "skippedPresent": present,
            "unknownIds": unknown,
            "cancelled": cancelled,
            "remaining": remaining,
        }
    finally:
        conn.close()


def _run_all(
    database: Path,
    root: Path,
    base: dict,
    assume_yes: bool,
    force: bool,
) -> dict:
    conn = photos_db.connect(database)
    try:
        total_before = _total_photos(conn)
        all_missing = missing_photos(conn, root)
        if total_before and len(all_missing) == total_before and not force:
            raise ValueError(
                "Toutes les photos de la base seraient supprimées. "
                "Vérifiez --root ; ajoutez --force si c'est vraiment voulu."
            )
        deleted, cancelled = _delete_rows(conn, all_missing, assume_yes)
        remaining = _update_photo_count(conn) if deleted else total_before
        return {
            **base,
            "mode": "all",
            "dryRun": False,
            "total": total_before,
            "missing": all_missing,
            "missingCount": len(all_missing),
            "deleted": deleted,
            "deletedCount": len(deleted),
            "skippedPresent": [],
            "unknownIds": [],
            "cancelled": cancelled,
            "remaining": remaining,
        }
    finally:
        conn.close()


def run(args: argparse.Namespace) -> dict:
    if args.output_file and (args.ids or args.ids_file or args.all):
        raise ValueError("--output-file n'est disponible qu'en mode dry-run.")

    database = _database_path(args.db)
    if (
        args.output_file
        and args.output_file.expanduser().resolve() == database.resolve()
    ):
        raise ValueError("--output-file ne peut pas écraser photos.db.")
    ids: list[int] = []
    if args.ids:
        ids = parse_ids(args.ids)
    elif args.ids_file:
        ids = parse_ids_file(args.ids_file)

    # Lecture seule pour le dry-run et pour ne pas ouvrir la base en écriture
    # lorsqu'aucune suppression n'est demandée.
    conn = photos_db.connect(database, readonly=True)
    try:
        root = _resolve_root(conn, args.root)
        base = _result_base(database, root)
        if not args.ids and not args.ids_file and not args.all:
            result = _run_dry_run(conn, root, base)
            if args.output_file:
                target = _write_id_paths(args.output_file, result["missing"])
                result["outputFile"] = str(target)
            return result
    finally:
        conn.close()

    if args.all:
        return _run_all(database, root, base, args.yes, args.force)
    return _run_ids(database, root, base, ids, args.yes)


def _print_dry_run_summary(result: dict) -> None:
    print(f"Base   : {result['database']}", file=sys.stderr)
    print(f"Racine : {result['root']}", file=sys.stderr)
    print(
        f"Absentes : {result['missingCount']} / {result['total']} photo(s).",
        file=sys.stderr,
    )
    if result.get("outputFile"):
        print(f"Fichier : {result['outputFile']}", file=sys.stderr)
    elif not result["missing"]:
        print("Aucune photo absente.", file=sys.stderr)


def _print_result(result: dict) -> None:
    print(f"Base   : {result['database']}")
    print(f"Racine : {result['root']}")
    if result["cancelled"]:
        print("Suppression annulée.")
        return

    print(f"Supprimées : {result['deletedCount']} photo(s) absente(s).")
    if result["mode"] == "ids":
        if result["skippedPresent"]:
            print(
                "Ignorées (fichier présent) : "
                + ", ".join(str(row["id"]) for row in result["skippedPresent"])
            )
        if result["unknownIds"]:
            print(
                "Identifiants inconnus : "
                + ", ".join(str(value) for value in result["unknownIds"])
            )
    print(f"Restantes  : {result['remaining']} photo(s) dans la base.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (FileNotFoundError, ValueError, photos_db.SchemaError, sqlite3.Error) as error:
        if args.json:
            print(json.dumps({"error": str(error)}, ensure_ascii=False))
        else:
            print(f"Erreur : {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["mode"] == "dry-run":
        _print_dry_run_summary(result)
        if not result.get("outputFile"):
            print("\n".join(_id_path_lines(result["missing"])))
    else:
        _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
