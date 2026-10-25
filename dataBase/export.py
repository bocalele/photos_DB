#!/usr/bin/env python3
"""Export de `photos.db` (schéma v4) en DataFrames et fichiers CSV.

Le script ne modifie jamais la base. Il expose les tables normalisées et une
vue ``photos_complete`` (une ligne par photo) qui regroupe personnes, mots-clés
et taxons.

Exemples :
    python dataBase/export.py --all
    python dataBase/export.py --all --out /tmp/photos.csv
    python dataBase/export.py --all --per-table --out-dir /tmp/export
    python dataBase/export.py --table people --out /tmp/personnes.csv
    python dataBase/export.py --query "SELECT * FROM photos WHERE rating >= 4"
    python dataBase/export.py --list
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import photos_db  # noqa: E402

KNOWN_TABLES = (
    "photos",
    "people",
    "photo_people",
    "keywords",
    "photo_keywords",
    "taxa",
    "photo_taxa",
    "locations",
)


def existing_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows if not row[0].startswith("sqlite_")]


def _normalize_ids(frame: pd.DataFrame) -> pd.DataFrame:
    for column in frame.columns:
        if not (column.startswith("id_") or column in {"id", "photo_id", "person_id", "keyword_id", "taxon_id", "location_id"}):
            continue
        values = frame[column].dropna()
        if values.empty:
            continue
        try:
            if all(float(value).is_integer() for value in values):
                frame[column] = frame[column].astype("Int64")
        except (TypeError, ValueError):
            continue
    return frame


def load_tables(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    tables = set(existing_tables(conn))
    frames = {}
    for table in KNOWN_TABLES:
        if table in tables:
            frames[table] = _normalize_ids(pd.read_sql_query(f'SELECT * FROM "{table}"', conn))
    return frames


def _concatenate(series: pd.Series) -> str:
    values = sorted({str(value) for value in series.dropna() if str(value).strip()})
    return " ; ".join(values)


def _people_labels(people: pd.DataFrame) -> pd.DataFrame:
    names = people.copy()
    for column in ("prenom", "nom"):
        if column not in names:
            names[column] = ""
        names[column] = names[column].fillna("").astype(str).str.strip()
    names["label"] = (names["prenom"] + " " + names["nom"]).str.strip()
    broken = names["label"] == ""
    names.loc[broken, "label"] = "Personne " + names.loc[broken, "id"].astype(str)
    return names


def _aggregate(
    frames: dict[str, pd.DataFrame],
    link_table: str,
    label_table: str,
    label_column: str,
    output: str,
) -> pd.DataFrame:
    links = frames.get(link_table)
    labels = frames.get(label_table)
    if links is None or links.empty or labels is None or labels.empty:
        return pd.DataFrame(columns=["photo_id", output])
    if label_table == "people":
        labels = _people_labels(labels)
        merged = links.merge(
            labels[["id", "label"]], left_on="person_id", right_on="id", how="left"
        )
        label_column = "label"
    elif label_table == "keywords":
        merged = links.merge(
            labels[["id", "label"]], left_on="keyword_id", right_on="id", how="left"
        )
    elif label_table == "taxa":
        merged = links.merge(
            labels[["id", "name", "scientific_name"]], left_on="taxon_id",
            right_on="id", how="left",
        )
        merged["label"] = merged["scientific_name"].fillna(merged["name"])
        label_column = "label"
    else:
        return pd.DataFrame(columns=["photo_id", output])
    return (
        merged.groupby("photo_id")[label_column]
        .apply(_concatenate)
        .rename(output)
        .reset_index()
    )


def build_frames(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    frames = load_tables(conn)
    photos = frames.get("photos")
    if photos is not None and not photos.empty:
        photos = photos.copy()
        locations = frames.get("locations")
        if locations is not None and not locations.empty:
            renamed = locations.rename(columns={
                "id": "location_id",
                "country": "location_country",
                "city": "location_city",
                "latitude": "location_latitude",
                "longitude": "location_longitude",
                "path": "location_path",
            })
            columns = [
                "location_id", "location_country", "location_city",
                "location_latitude", "location_longitude", "location_path",
            ]
            photos = photos.merge(
                renamed[columns].drop_duplicates("location_id"),
                on="location_id", how="left",
            )
        for output, table, label_table, column in (
            ("people", "photo_people", "people", "label"),
            ("keywords", "photo_keywords", "keywords", "label"),
            ("taxa", "photo_taxa", "taxa", "label"),
        ):
            extra = _aggregate(frames, table, label_table, column, output)
            if not extra.empty:
                photos = photos.merge(extra, left_on="id", right_on="photo_id", how="left")
                photos = photos.drop(columns=[c for c in ("photo_id", "photo_id_x", "photo_id_y") if c in photos.columns])
        for column in ("people", "keywords", "taxa"):
            if column not in photos:
                photos[column] = ""
            photos[column] = photos[column].fillna("")
        frames["photos_complete"] = photos

    people = frames.get("people")
    if people is not None and not people.empty:
        people = _people_labels(people)
        counts = (
            frames.get("photo_people", pd.DataFrame(columns=["person_id"]))
            .groupby("person_id").size().rename("nb_photos")
        )
        people = people.merge(counts, left_on="id", right_index=True, how="left")
        people["nb_photos"] = people["nb_photos"].fillna(0).astype(int)
        frames["people"] = people
        links = frames.get("photo_people")
        if links is not None and not links.empty:
            frames["photo_people"] = links.merge(
                people[["id", "label", "prenom", "nom"]], left_on="person_id",
                right_on="id", how="left", suffixes=("", "_personne"),
            )

    keywords = frames.get("keywords")
    if keywords is not None and not keywords.empty:
        counts = (
            frames.get("photo_keywords", pd.DataFrame(columns=["keyword_id"]))
            .groupby("keyword_id").size().rename("nb_photos")
        )
        keywords = keywords.merge(counts, left_on="id", right_index=True, how="left")
        keywords["nb_photos"] = keywords["nb_photos"].fillna(0).astype(int)
        frames["keywords"] = keywords
        links = frames.get("photo_keywords")
        if links is not None and not links.empty:
            frames["photo_keywords"] = links.merge(
                keywords[["id", "label"]], left_on="keyword_id",
                right_on="id", how="left", suffixes=("", "_keyword"),
            )

    taxa = frames.get("taxa")
    if taxa is not None and not taxa.empty:
        counts = (
            frames.get("photo_taxa", pd.DataFrame(columns=["taxon_id"]))
            .groupby("taxon_id").size().rename("nb_photos")
        )
        taxa = taxa.merge(counts, left_on="id", right_index=True, how="left")
        taxa["nb_photos"] = taxa["nb_photos"].fillna(0).astype(int)
        frames["taxa"] = taxa
        links = frames.get("photo_taxa")
        if links is not None and not links.empty:
            frames["photo_taxa"] = links.merge(
                taxa[["id", "name", "scientific_name", "path"]], left_on="taxon_id",
                right_on="id", how="left", suffixes=("", "_taxon"),
            )

    locations = frames.get("locations")
    if locations is not None and not locations.empty:
        counts = (
            frames.get("photos", pd.DataFrame(columns=["location_id"]))
            .groupby("location_id").size().rename("nb_photos")
        )
        locations = locations.merge(counts, left_on="id", right_index=True, how="left")
        locations["nb_photos"] = locations["nb_photos"].fillna(0).astype(int)
        frames["locations"] = locations

    return frames


def write_frame(frame: pd.DataFrame, destination: Path, separator: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False, sep=separator, encoding="utf-8-sig")
    print(f"{destination} ({len(frame)} ligne(s), {len(frame.columns)} colonne(s))")


def choose_frame(frames: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    normalized = name.strip().lower()
    if normalized in frames:
        return frames[normalized]
    available = ", ".join(sorted(frames))
    raise SystemExit(f"Table inconnue : {name}. Disponibles : {available}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Base à lire (défaut : celle des préférences).")
    parser.add_argument("--all", action="store_true", help="Exporter la vue complète des photos.")
    parser.add_argument("--per-table", action="store_true", help="Avec --all : un CSV par DataFrame.")
    parser.add_argument("--table", help="Exporter un DataFrame précis.")
    parser.add_argument("--query", help="Requête SQL à exporter (ou chemin d'un fichier .sql).")
    parser.add_argument("--out", help="Fichier CSV de sortie.")
    parser.add_argument("--out-dir", help="Dossier des CSV avec --per-table.")
    parser.add_argument("--sep", default=",", help="Séparateur CSV (défaut : virgule).")
    parser.add_argument("--list", action="store_true", help="Lister les DataFrames disponibles.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    database = Path(args.db).expanduser() if args.db else photos_db.path()
    if not database.is_file():
        print(f"Base introuvable : {database}", file=sys.stderr)
        return 1
    conn = photos_db.connect(database, readonly=True)
    try:
        frames = build_frames(conn)
        if args.list:
            for name in sorted(frames):
                print(f"{name:18} {len(frames[name]):6} ligne(s)")
            return 0
        if args.query:
            query = args.query
            candidate = Path(query).expanduser()
            if candidate.is_file():
                query = candidate.read_text(encoding="utf-8")
            frame = pd.read_sql_query(query, conn)
            destination = Path(args.out).expanduser() if args.out else Path("query_export.csv")
            write_frame(frame, destination, args.sep)
            return 0
        if args.table:
            frame = choose_frame(frames, args.table)
            destination = Path(args.out).expanduser() if args.out else Path(f"{args.table.lower()}.csv")
            write_frame(frame, destination, args.sep)
            return 0
        if not args.all:
            print("Choisissez --all, --table, --query ou --list (voir --help).", file=sys.stderr)
            return 2
        complete = frames.get("photos_complete")
        if complete is None:
            print("La table photos est absente ou vide.", file=sys.stderr)
            return 1
        destination = Path(args.out).expanduser() if args.out else Path("photos_export.csv")
        write_frame(complete, destination, args.sep)
        if args.per_table:
            directory = Path(args.out_dir).expanduser() if args.out_dir else Path("export_photos")
            for name in sorted(frames):
                write_frame(frames[name], directory / f"{name}.csv", args.sep)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
