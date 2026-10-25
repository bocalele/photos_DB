#!/usr/bin/env python3
"""Crée ou vérifie la base `photos.db` au schéma courant.

L'ancien import CSV (mots-clés numériques, chemins absolus) a été remplacé par
`dataBase.sync`, qui réinitialise et met à jour la base depuis les métadonnées
des fichiers.

    python -m dataBase.sync reset
    python -m dataBase.sync update /chemin/du/dossier
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import photos_db  # noqa: E402


def create_database(database: Path | str | None = None) -> Path:
    target = Path(database).expanduser() if database else photos_db.path()
    conn = photos_db.connect(target)
    try:
        photos_db.create_schema(conn)
        return target
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Base à créer (défaut : celle des préférences).")
    args = parser.parse_args()
    target = create_database(args.db)
    print(f"Base prête : {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
