#!/usr/bin/env python3
"""Catalogue des personnes : import XMP et affichage de l'arbre familial.

L'import ne crée plus de mots-clés numériques. Il relie les noms de personnes
(`XMP:RegionName`) à la table `people` et aux photos par `photo_people`.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import photos_db  # noqa: E402
from tools import environment  # noqa: E402
from tools.getMetaData import get_metadata  # noqa: E402


def _database_path(database: str | Path | None) -> Path:
    target = Path(database).expanduser() if database else environment.photos_db()
    if target.is_dir():
        target = target / "photos.db"
    return target


def _image_files(folder: Path):
    extensions = {
        ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".psd", ".heic",
        ".webp", ".nef", ".nrw", ".cr2", ".cr3", ".arw", ".dng", ".raf",
        ".rw2", ".orf", ".pef",
    }
    for candidate in sorted(folder.rglob("*")):
        if candidate.is_file() and candidate.suffix.lower() in extensions:
            yield candidate


def add_people(database: str | Path | None, folder: str | Path, clean: bool = False) -> int:
    """Ajoute les personnes trouvées dans les régions XMP des photos.

    ``clean`` est conservé pour compatibilité : il ne supprime plus le catalogue
    (les liens familiaux seraient perdus) ; les liens de chaque photo traitée
    sont simplement réécrits.
    """
    del clean
    db_path = _database_path(database)
    root = environment.photos_root().resolve()
    folder_path = Path(folder).expanduser().resolve()
    conn = photos_db.connect(db_path)
    index = photos_db.build_index(photos_db.load_people_from_connection(conn))
    count = 0
    try:
        for photo in _image_files(folder_path):
            try:
                relative = photos_db.relpath_for(photo, root)
            except ValueError:
                continue
            metadata = get_metadata(str(photo))
            regions = metadata.get("XMP", {}).get("RegionName", [])
            if isinstance(regions, str):
                regions = [regions]
            names = [str(name).strip() for name in regions if str(name).strip()]
            if not names:
                continue
            photo_id = photos_db.ensure_photo(conn, relative, root)
            person_ids = []
            for name in names:
                person = photos_db.insert_person(conn, name)
                person_ids.append(person["id_people"])
                for variant in (person.get("label"), name):
                    key = photos_db.normalize_name(variant)
                    if key:
                        index.setdefault(key, person["id_people"])
            photos_db.set_photo_people(conn, photo_id, person_ids, replace=True)
            count += 1
    finally:
        conn.close()
    return count


def show_peoples(database: str | Path | None) -> None:
    import networkx as nx
    import pandas as pd
    from bokeh.models import ColumnDataSource, HoverTool, LabelSet
    from bokeh.plotting import figure, from_networkx, show

    conn = sqlite3.connect(_database_path(database))
    try:
        frame = pd.read_sql_query(
            "SELECT id AS id_people, prenom, nom, surnom, id_pere, id_mere FROM people",
            conn,
        )
    finally:
        conn.close()

    graph = nx.DiGraph()
    for _, row in frame.iterrows():
        graph.add_node(
            row["id_people"], nom=f"{row['nom'] or ''}", prenom=f"{row['prenom'] or ''}"
        )
    for _, row in frame.iterrows():
        child = int(row["id_people"])
        if pd.notna(row["id_pere"]):
            graph.add_edge(int(row["id_pere"]), child, relation="pere")
        if pd.notna(row["id_mere"]):
            graph.add_edge(int(row["id_mere"]), child, relation="mere")

    if not graph.nodes:
        print("Aucune personne dans la base.")
        return
    positions = nx.nx_pydot.graphviz_layout(graph, prog="dot")
    node_ids = list(graph.nodes)
    names = [graph.nodes[node]["nom"] for node in node_ids]
    first_names = [graph.nodes[node]["prenom"] for node in node_ids]
    x_values, y_values = zip(*(positions[node] for node in node_ids))
    renderer = from_networkx(graph, positions)
    renderer.node_renderer.data_source.data = {
        "index": node_ids, "nom": names, "prenom": first_names,
    }
    plot = figure(width=6400, height=1200)
    plot.add_tools(HoverTool(tooltips=[("Nom ", "@nom"), ("Prénom ", "@prenom")]))
    plot.renderers.append(renderer)
    labels = LabelSet(
        x="x", y="y", text="nom",
        source=ColumnDataSource(data=dict(x=x_values, y=y_values, nom=first_names)),
        text_font_size="10px", x_offset=-20, y_offset=30,
    )
    plot.add_layout(labels)
    colors = ["blue" if graph[u][v]["relation"] == "pere" else "red" for u, v in graph.edges()]
    renderer.edge_renderer.data_source.data["color"] = colors
    renderer.edge_renderer.glyph.line_color = "color"
    renderer.node_renderer.glyph.size = 40
    show(plot)


def choose_action() -> str:
    while True:
        action = input("Action (show/add) ? ").strip().lower()
        if action in ("show", "add"):
            return action
        print("Choix invalide. Tapez 'show' ou 'add'.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalogue des personnes")
    parser.add_argument("action", nargs="?", choices=["show", "add"])
    parser.add_argument("--files", default=str(environment.photos_root()))
    parser.add_argument("--db", default=str(environment.photos_db()))
    parser.add_argument("--clean", action="store_true", help="Compatibilité (sans effet destructif)")
    args = parser.parse_args()
    action = args.action or choose_action()
    if action == "add":
        count = add_people(args.db, args.files, clean=args.clean)
        print(f"{count} photo(s) avec personnes importée(s).")
    else:
        show_peoples(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
