import sqlite3
import pandas as pd
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools.getMetaData import get_metadata
from tools.image_files import as_list, get_image_files, with_progress
import networkx as nx
from bokeh.plotting import figure, show, from_networkx
from bokeh.models import HoverTool, LabelSet, ColumnDataSource

def split_name(full_name):
    parts = full_name.strip().split()
    
    prenom = parts[0]
    nom = " ".join(parts[1:]) if len(parts) > 1 else ""
    surnom = ''
    return prenom, nom, surnom

def add_people( db, path, clean=False):

    conn = None
    if clean:
        try:
            conn = sqlite3.connect(db)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM Peoples")
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='Peoples'")
            conn.commit()
            conn.close()
        except:
            pass

    peoples =[]
    files = list(get_image_files(path))
    for file in with_progress(files, desc="Traitement des fichiers", unit="photo"):
        C= get_metadata (file)
        try:
            peoples.extend(as_list(C["XMP"].get("RegionName", [])))
        except:
            pass
    peoples = list(set(peoples))
    conn = None
    try:
        conn = sqlite3.connect(db)
        cursor = conn.cursor()
        for p in peoples:
            prenom, nom, surnom = split_name (p)
            cursor.execute("""INSERT INTO Peoples (prenom, nom, surnom) VALUES (?, ?, ?) ON CONFLICT(prenom, nom, surnom) DO NOTHING""", (prenom, nom, surnom))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Erreur SQLite : {e}")

    return 

def show_peoples (db):
    conn = sqlite3.connect(db)
    df = pd.read_sql_query("SELECT * FROM Peoples", conn)

    G = nx.DiGraph()
    for _, row in df.iterrows():
        G.add_node(row["id_people"], nom=f'{row["nom"]}', prenom=f'{row["prenom"]}')
    for _, row in df.iterrows():
        enfant = int(row["id_people"])
        if pd.notna(row["id_pere"]):
            G.add_edge(int(row["id_pere"]), enfant, relation="pere")
        if pd.notna(row["id_mere"]):
            G.add_edge(int(row["id_mere"]), enfant, relation="mere")

    pos = nx.nx_pydot.graphviz_layout(G, prog="dot")
    node_ids = list(G.nodes)
    names = [G.nodes[n]["nom"] for n in node_ids]
    pnames = [G.nodes[n]["prenom"] for n in node_ids]
    x, y = zip(*[pos[n] for n in node_ids])

    graph_renderer = from_networkx(G, pos)
    graph_renderer.node_renderer.data_source.data = {"index": node_ids,"nom": names,"prenom":pnames}

    plot = figure( width=6400, height=1200)
    plot.add_tools(HoverTool(tooltips=[("Nom ", "@nom"), ("Prénom ", "@prenom")]))
    plot.renderers.append(graph_renderer)
    source = ColumnDataSource(data=dict( x=x, y=y, nom=pnames))
    labels = LabelSet( x="x", y="y", text="nom", source=source,
        text_font_size="10px", x_offset=-20,y_offset=30 )
    plot.add_layout(labels)

    edge_colors = []
    for u, v in G.edges():
        if G[u][v]["relation"] == "pere":
            edge_colors.append("blue")
        else:
            edge_colors.append("red")
    graph_renderer.edge_renderer.data_source.data["color"] = edge_colors
    graph_renderer.edge_renderer.glyph.line_color = "color"
    graph_renderer.node_renderer.glyph.size = 40
    show(plot)

def choose_action():
    while True:
        action = input("Action (show/add) ? ").strip().lower()
        if action in ("show", "add"):
            return action
        print("Choix invalide. Tapez 'show' ou 'add'.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataBase - People ")
    parser.add_argument("action", nargs="?", choices=["show", "add"], help="Action a executer")
    parser.add_argument('--files', default='/Users/pc/PHOTOS/REPRODUCTION/Repro/iMac de pc/',  help='Photos path')
    parser.add_argument('--db', default='/Users/pc/PHOTOS/_DATA',  help='DataBase path')
    parser.add_argument('--clean', action="store_true", help='Vide Peoples avant ajout')
    args = parser.parse_args()

    action = args.action or choose_action()
    db_path = args.db + '/photos.db'
    if action == "add":
        add_people(db_path, args.files, clean=args.clean)
    else:
        show_peoples(db_path)
