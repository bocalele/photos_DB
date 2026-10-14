import argparse, os, sqlite3, pandas as pd


def _none_if_empty(value):
    if pd.isna(value) or value == "":
        return None
    return value


def _to_int_or_none(value):
    value = _none_if_empty(value)
    if value is None:
        return None
    return int(value)


def _get_csv_value(row, columns, name):
    column = columns.get(name)
    if column is None:
        return None
    return _none_if_empty(row[column])


def _get_csv_id(row, columns, name):
    column = columns.get(name)
    if column is None:
        return None
    return _to_int_or_none(row[column])


def import_peoples_csv(conn, db_path):
    csv_path = os.path.join(os.path.dirname(db_path), "Peoples.csv")
    if not os.path.exists(csv_path):
        return

    df = pd.read_csv(csv_path, sep=None, engine="python")
    columns = {column.strip().lower(): column for column in df.columns}
    columns["prenom"] = columns.get("prenom") or columns.get("prénom")
    cursor = conn.cursor()

    for _, row in df.iterrows():
        values = {
            "id_people": _get_csv_id(row, columns, "id_people"),
            "nom": _get_csv_value(row, columns, "nom"),
            "prenom": _get_csv_value(row, columns, "prenom"),
            "surnom": _get_csv_value(row, columns, "surnom"),
        }
        insert_columns = [column for column, value in values.items() if value is not None]
        if not insert_columns:
            continue
        placeholders = ", ".join(["?"] * len(insert_columns))
        cursor.execute(
            f'''INSERT OR IGNORE INTO Peoples ({", ".join(insert_columns)})
                VALUES ({placeholders})''',
            tuple(values[column] for column in insert_columns)
        )

    existing_ids = {
        row[0] for row in cursor.execute("SELECT id_people FROM Peoples").fetchall()
    }
    missing_parent_ids = set()
    for _, row in df.iterrows():
        id_people = _get_csv_id(row, columns, "id_people")
        id_pere = _get_csv_id(row, columns, "id_pere")
        id_mere = _get_csv_id(row, columns, "id_mere")
        if id_pere is not None and id_pere not in existing_ids:
            missing_parent_ids.add(id_pere)
            id_pere = None
        if id_mere is not None and id_mere not in existing_ids:
            missing_parent_ids.add(id_mere)
            id_mere = None
        if id_people is not None and (id_pere is not None or id_mere is not None):
            cursor.execute(
                '''UPDATE Peoples
                   SET id_pere = COALESCE(?, id_pere),
                       id_mere = COALESCE(?, id_mere)
                   WHERE id_people = ?''',
                (id_pere, id_mere, id_people)
            )

    if missing_parent_ids:
        print(
            "Parents absents de Peoples.csv, liaisons ignorees : "
            + ", ".join(str(id_parent) for id_parent in sorted(missing_parent_ids))
        )
    print(f"Table Peoples remplie depuis '{csv_path}'.")


def create_database(DB):

    try:
        conn = sqlite3.connect(DB)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # Table  Localisations
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Localisations (
            id_loc INTEGER PRIMARY KEY AUTOINCREMENT,
            latitude REAL, longitude REAL, ville TEXT, pays TEXT)
        ''')

        # Table Photos
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Photos (
            id_photo INTEGER PRIMARY KEY AUTOINCREMENT, 
            filename TEXT NOT NULL, path TEXT NOT NULL UNIQUE, thumbPath TEXT,
            date DATETIME, model TEXT, Lens TEXT, focal TEXT,  aperture REAL, speed TEXT, iso INTEGER, correction TEXT,
            rating INTEGER, author TEXT, 
            hauteur INTEGER, largeur INTEGER, fullsize TEXT,
            unique_id TEXT UNIQUE,
            id_loc INTEGER,
            FOREIGN KEY (id_loc) REFERENCES Localisations(id_loc) ON DELETE SET NULL)
        ''')

        # Table Keywords
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Keywords (
            id_keyword INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT  UNIQUE, Hlabel  TEXT  UNIQUE,
            CHECK ( (label IS NOT NULL AND Hlabel IS NULL) OR
                    (label IS NULL AND Hlabel IS NOT NULL)  )
            )           
        ''')

        # Table des Peoples
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Peoples (
            id_people INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT,
            prenom TEXT,
            surnom TEXT,
            id_pere INTEGER,
            id_mere INTEGER,
            UNIQUE(nom, prenom, surnom)
            FOREIGN KEY (id_pere) REFERENCES Peoples(id_people),
            FOREIGN KEY (id_mere) REFERENCES Peoples(id_people)
            )
        ''')

        # Table des Albums
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Albums (
            id_album INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT)
        ''')

        # Table Thesaurus
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Thesaurus (
            id_term INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            id_parent INTEGER,
            id_root INTEGER, 
            FOREIGN KEY (id_parent) REFERENCES Thesaurus(id_term) ON DELETE CASCADE,
            FOREIGN KEY (id_root) REFERENCES Thesaurus(id_term)
        )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_thesaurus_root ON Thesaurus(id_root)')


        # --- TABLES DE LIAISON (Many-to-Many) ---

        # Liaison Photos <-> Mots-clés
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Photo_Keywords (
            id_photo INTEGER,
            id_keyword INTEGER,
            id_root INTEGER,
            PRIMARY KEY (id_photo, id_keyword, id_root),
            FOREIGN KEY (id_photo) REFERENCES Photos(id_photo) ON DELETE CASCADE,
            FOREIGN KEY (id_keyword) REFERENCES Keywords(id_keyword) ON DELETE CASCADE
            FOREIGN KEY (id_keyword) REFERENCES Thesaurus(id_term) ON DELETE CASCADE        )
        ''')

        # Liaison Photos <-> Personnes
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Photo_Peoples (
            id_photo INTEGER,
            id_people INTEGER,
            PRIMARY KEY (id_photo, id_people),
            FOREIGN KEY (id_photo) REFERENCES Photos(id_photo) ON DELETE CASCADE,
            FOREIGN KEY (id_people) REFERENCES Peoples(id_people) ON DELETE CASCADE
        )
        ''')

        # Liaison Photos <-> Albums
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Photo_Albums (
            id_photo INTEGER,
            id_album INTEGER,
            PRIMARY KEY (id_photo, id_album),
            FOREIGN KEY (id_photo) REFERENCES Photos(id_photo) ON DELETE CASCADE,
            FOREIGN KEY (id_album) REFERENCES Albums(id_album) ON DELETE CASCADE
        )
        ''')
        import_peoples_csv(conn, DB)
        conn.commit()
        conn.close()
        print(f"La base de données '{DB}' a été créée avec succès.")

    except Exception as e:
        print(f"La base de données '{DB}' n a pas pu etre  créée : {e}")


def load_data (db, table):
    conn = sqlite3.connect(db)
    df = pd.read_sql_query("SELECT * FROM "+table, conn)
    conn.close()
    return df

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataBase Creation")
    parser.add_argument('--db', default='/Users/pc/PHOTOS/_DATA',  help='filename or list of filenames or 2 integers given list of files from *_n1_* to *_n2_*')
    args = parser.parse_args()

    create_database(args.db+'/photos.db')
    
