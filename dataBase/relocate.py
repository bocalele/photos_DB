"""Mise à jour des chemins après un déplacement de photos ou de dossiers.

Les métadonnées restent la source de vérité : déplacer un fichier ne modifie
ni son XMP ni son EXIF. En revanche, les chemins relatifs stockés dans les
index dérivés doivent suivre le fichier :

- ``photos.db`` : ``photos.relpath`` ;
- ``face.db`` : ``Photo.relpath``, ``Face.relpath`` et ``RefFace.relpath``.

Le déplacement physique est fait par l'application Classement. Ce module ne
touche qu'aux bases, et dans une seule transaction lorsque les deux bases sont
présentes. Les chemins sont relatifs à la racine photo et utilisent toujours
la forme POSIX (``/``).
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path, PurePosixPath
from typing import Iterable


class RelocationError(ValueError):
    """Déplacement refusé parce que les chemins de la base seraient incohérents."""


def clean_relpath(value: str) -> str:
    """Normalise un chemin relatif et refuse les chemins absolus ou ambigus."""
    text = str(value or "").strip().replace("\\", "/")
    if not text or text == ".":
        return ""
    path = PurePosixPath(text)
    if path.is_absolute():
        raise RelocationError(f"Chemin absolu interdit : {value}")
    parts = path.parts
    if any(part in ("", ".", "..") for part in parts):
        raise RelocationError(f"Chemin relatif invalide : {value}")
    return path.as_posix()


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _matching_paths(
    conn: sqlite3.Connection,
    schema: str,
    table: str,
    old: str,
    recursive: bool,
) -> list[str]:
    if recursive:
        rows = conn.execute(
            f"SELECT relpath FROM {schema}.{table} "
            "WHERE relpath = ? OR relpath LIKE ? ESCAPE '\\'",
            (old, _like_escape(old) + "/%"),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT relpath FROM {schema}.{table} WHERE relpath = ?",
            (old,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def _path_exists(
    conn: sqlite3.Connection, schema: str, table: str, relpath: str
) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.{table} WHERE relpath = ? LIMIT 1",
        (relpath,),
    ).fetchone()
    return row is not None


def _replacement(old_path: str, old: str, new: str, recursive: bool) -> str:
    if not recursive:
        return new
    if old_path == old:
        return new
    if old_path.startswith(old + "/"):
        return new + old_path[len(old):]
    return old_path


def update_paths(
    photos_db_path: Path | str | None,
    face_db_path: Path | str | None,
    moves: Iterable[tuple[str, str, bool]],
) -> dict[str, int]:
    """Applique une liste de déplacements aux deux bases.

    Chaque déplacement est un triplet ``(ancien, nouveau, récursif)``. Un
    dossier déplacé utilise ``récursif=True`` et met à jour tous ses descendants
    dont le chemin commence par ``ancien + "/"``. Les bases absentes sont
    simplement ignorées.

    La fonction refuse les collisions : un nouveau chemin déjà présent dans
    une base, hors des chemins déplacés, ferait perdre une ligne. Le contrôle
    est fait avant toute écriture, puis les mises à jour sont transactionnelles.
    """
    normalized: list[tuple[str, str, bool]] = []
    for raw_old, raw_new, recursive in moves:
        old = clean_relpath(raw_old)
        new = clean_relpath(raw_new)
        if not old:
            raise RelocationError("La racine photo ne peut pas être déplacée.")
        if old == new:
            continue
        if recursive and (new == old or new.startswith(old + "/")):
            raise RelocationError(
                "Un dossier ne peut pas être déplacé dans lui-même."
            )
        normalized.append((old, new, bool(recursive)))
    if not normalized:
        return {
            "photos": 0,
            "face_photos": 0,
            "faces": 0,
            "reference_faces": 0,
        }

    photos_path = Path(photos_db_path).expanduser() if photos_db_path else None
    face_path = Path(face_db_path).expanduser() if face_db_path else None
    if photos_path is not None and not photos_path.is_file():
        photos_path = None
    if face_path is not None and not face_path.is_file():
        face_path = None
    if photos_path is None and face_path is None:
        return {
            "photos": 0,
            "face_photos": 0,
            "faces": 0,
            "reference_faces": 0,
        }

    conn = sqlite3.connect(":memory:", timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        attached: list[str] = []
        if photos_path is not None:
            conn.execute("ATTACH DATABASE ? AS p", (str(photos_path),))
            attached.append("p")
        if face_path is not None:
            conn.execute("ATTACH DATABASE ? AS f", (str(face_path),))
            attached.append("f")

        tables = [
            ("p", "photos", "photos"),
            ("f", "Photo", "face_photos"),
            ("f", "Face", "faces"),
            ("f", "RefFace", "reference_faces"),
        ]
        available = [
            (schema, table, key)
            for schema, table, key in tables
            if schema in attached and _table_exists(conn, schema, table)
        ]

        replacements: dict[str, str] = {}
        table_counts = {key: 0 for _, _, key in available}
        for old, new, recursive in normalized:
            for schema, table, key in available:
                paths = _matching_paths(conn, schema, table, old, recursive)
                table_counts[key] += len(paths)
                for old_path in paths:
                    replacement = _replacement(old_path, old, new, recursive)
                    previous = replacements.get(old_path)
                    if previous is not None and previous != replacement:
                        raise RelocationError(
                            f"Déplacements contradictoires pour {old_path}."
                        )
                    replacements[old_path] = replacement

        if not replacements:
            return {key: 0 for _, _, key in available} | {
                "photos": 0,
                "face_photos": 0,
                "faces": 0,
                "reference_faces": 0,
            }

        old_paths = set(replacements)
        new_paths = set(replacements.values())
        if len(new_paths) != len(replacements):
            raise RelocationError(
                "Deux éléments distincts arriveraient au même chemin."
            )
        for old_path, new_path in replacements.items():
            if old_path == new_path:
                continue
            if new_path in old_paths:
                raise RelocationError(
                    "Le déplacement croisé de deux éléments n'est pas pris en charge."
                )
            for schema, table, _ in available:
                if _path_exists(conn, schema, table, new_path):
                    raise RelocationError(
                        f"Un chemin existe déjà dans la base : {new_path}"
                    )

        prefix = f"__relocate__{uuid.uuid4().hex}"
        temp_for = {
            old_path: f"{prefix}/{index}"
            for index, old_path in enumerate(replacements)
        }

        conn.execute("BEGIN IMMEDIATE")
        try:
            for schema, table, _ in available:
                rows = [
                    (temp_for[old_path], old_path)
                    for old_path in replacements
                ]
                conn.executemany(
                    f"UPDATE {schema}.{table} SET relpath = ? WHERE relpath = ?",
                    rows,
                )
                rows = [
                    (new_path, temp_for[old_path])
                    for old_path, new_path in replacements.items()
                ]
                conn.executemany(
                    f"UPDATE {schema}.{table} SET relpath = ? WHERE relpath = ?",
                    rows,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        counts = {
            "photos": 0,
            "face_photos": 0,
            "faces": 0,
            "reference_faces": 0,
        }
        counts.update(table_counts)
        return counts
    finally:
        conn.close()
