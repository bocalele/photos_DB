#!/usr/bin/env python3
"""Correspondance entre les noms des métadonnées et la table `Peoples`."""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

import face_db


def normalize_name(text: str) -> str:
    """Comparaison insensible à la casse, aux accents et aux espaces multiples."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.casefold().replace("’", "'").split())


def load_people(path: Path | str = face_db.DEFAULT_PHOTOS_DB) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Base photos introuvable : {path}. Renseignez PHOTOS_DB ou --photos-db."
        )
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id_people,
                   COALESCE(prenom, '') AS prenom,
                   COALESCE(nom, '') AS nom,
                   COALESCE(surnom, '') AS surnom
            FROM Peoples
            ORDER BY id_people
            """
        ).fetchall()
    people = []
    for row in rows:
        label = " ".join(part for part in (row["prenom"], row["nom"]) if part).strip()
        people.append(
            {
                "id_people": row["id_people"],
                "prenom": row["prenom"],
                "nom": row["nom"],
                "surnom": row["surnom"],
                "label": label or f"#{row['id_people']}",
            }
        )
    return people


def build_index(people: list[dict]) -> dict[str, int]:
    """Index des écritures possibles d'un nom vers `id_people`.

    Les métadonnées XMP contiennent le nom complet tel que saisi dans Lightroom,
    par exemple « Simone METRAL - EITENSCHENCK ». On accepte aussi les variantes
    avec le surnom, entre parenthèses ou non.
    """
    index: dict[str, int] = {}
    for person in people:
        prenom, nom, surnom = person["prenom"], person["nom"], person["surnom"]
        variants = {
            f"{prenom} {nom}",
            f"{nom} {prenom}",
            f"{prenom} {surnom}",
            f"{prenom} {nom} ({surnom})",
            person["label"],
        }
        for variant in variants:
            variant = variant.strip()
            if variant:
                index.setdefault(normalize_name(variant), person["id_people"])
    return index
