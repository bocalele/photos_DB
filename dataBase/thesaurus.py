import json
import sqlite3
import sys, argparse
from pathlib import Path

parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))
from tools.thesaurus import Thesaurus

def fill_thesaurus(thesaurus, db, clean = False):
    """
    Remplit la table Thesaurus d'une base de données SQLite à partir d'un fichier JSON.

    Args:
        thesaurus (str): Chemin vers le fichier JSON contenant les données du thésaurus.
        db (str): Nom du fichier de la base de données SQLite.
    """
    conn = None
    if clean:
        try:
            conn = sqlite3.connect(db)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM Thesaurus")
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='Thesaurus'")
            conn.commit()
            conn.close()
        except:
            pass

    try:
        conn = sqlite3.connect(db)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT MAX(id_root) FROM Thesaurus")
            result = cursor.fetchone()
            id_root = result[0] +1
        except:
            id_root = 1

        T = Thesaurus (thesaurus)
        id_map = {}

        for key, v in T.tree.nodes.items():
            cursor.execute("INSERT INTO Thesaurus (label, id_root) VALUES (?, ?)", (key,id_root))
            id_map[v.identifier] = cursor.lastrowid 
        conn.commit()

        for key, v in T.tree.nodes.items():
            try:
               id_parent = id_map[T.tree.parent (v.identifier).identifier]
            except:
                id_parent = 0
            cursor.execute(
                "UPDATE Thesaurus SET id_parent = ? WHERE id_term = ?",
                (id_parent, id_map[v.identifier])
            )    
        conn.commit()

        print(f"Données insérées {thesaurus}")


    except sqlite3.Error as e:
        print(f"Erreur SQLite : {e}")
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="photos - Thesaurus ")
    parser.add_argument('--th', default='/Users/pc/PHOTOS/_DATA/Thesaurus/',  help='Thesaurus path')
    parser.add_argument('--db', default='/Users/pc/PHOTOS/_DATA',  help='DataBase path')
    args = parser.parse_args()

    fill_thesaurus(args.th + '/thesaurus-oiseaux-utf8.txt', args.db + '/photos.db', clean=True)
    fill_thesaurus(args.th + '/thesaurus-mammiferes-utf8.txt',args.db + '/photos.db', clean=True)
    fill_thesaurus(args.th + '/thesaurus-insectes-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thesaurus-odonates-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thsaurus-arachnides-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thesaurus-poissons-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thesaurus-plantes-terrestres-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thesaurus-papillons-de-jour-france-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thesaurus-orchidees-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thesaurus-mollusques-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thesaurus-herpeto-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thesaurus-champignons-utf8.txt',args.db + '/photos.db')
    fill_thesaurus(args.th + '/thesaurus-arbres-arbustes-utf8.txt',args.db + '/photos.db')
