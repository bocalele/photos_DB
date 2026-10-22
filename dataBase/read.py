import sqlite3
import pandas as pd

# Chemin vers la base SQLite
db_path = "ma_base.db"

# Connexion à la base
conn = sqlite3.connect(db_path)

# Récupération de la liste des tables
query_tables = """
SELECT name
FROM sqlite_master
WHERE type='table'
AND name NOT LIKE 'sqlite_%';
"""

tables = pd.read_sql_query(query_tables, conn)["name"].tolist()

# Chargement des tables dans des DataFrames
dataframes = {}

for table in tables:
    dataframes[table] = pd.read_sql_query(f'SELECT * FROM "{table}"', conn)
    print(f"Table '{table}' chargée : {len(dataframes[table])} lignes")

# Fermeture de la connexion
conn.close()

# Exemple d'utilisation
print("\nTables disponibles :", list(dataframes.keys()))

# Accéder à une table
# df_clients = dataframes["clients"]