"""Accès centralisé à `photos.db` (schéma v4).

La base est un catalogue de photos annotées, pas un cache. Elle ne contient
donc ni vignette, ni aperçu, ni « icône » : ces fichiers sont dérivés à la
demande dans le dossier de prévisualisation.

Principes du schéma :

- ``photos.id`` est un identifiant entier stable pour les jointures ; le chemin
  relatif (``relpath``) est la clé naturelle unique, indépendante du disque.
  Le nom de fichier n'est pas dupliqué : il se déduit de ``relpath`` ;
- les personnes, mots-clés, taxons et lieux sont normalisés et reliés aux
  photos par des tables de liaison ;
- les mots-clés sont **atomiques et plats** : une liste ancienne collée avec
  des espaces est éclatée, et une photo peut pointer vers plusieurs mots-clés ;
- les taxons forment une hiérarchie (``taxa.parent_id``, ``taxa.path``) qui
  permet de sélectionner une famille ou un ordre et d'inclure ses descendants ;
- un terme du thésaurus n'est pas dupliqué dans ``keywords`` : il vit dans
  ``taxa`` et ``photo_taxa`` ;
- les albums ne sont pas persistés : une sélection de photos est volatile.

Les chemins de la machine viennent de ``preferences.json`` via
``tools.environment``.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import annotations  # noqa: E402
from tools import environment  # noqa: E402


SCHEMA_VERSION = "4"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prenom TEXT NOT NULL DEFAULT '',
    nom TEXT NOT NULL DEFAULT '',
    surnom TEXT NOT NULL DEFAULT '',
    id_pere INTEGER REFERENCES people(id) ON DELETE SET NULL,
    id_mere INTEGER REFERENCES people(id) ON DELETE SET NULL,
    UNIQUE(prenom, nom, surnom)
);
CREATE INDEX IF NOT EXISTS idx_people_name ON people(nom, prenom);

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    country TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    latitude REAL,
    longitude REAL,
    path TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_locations_country ON locations(country, city);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relpath TEXT NOT NULL UNIQUE,
    taken_at TEXT,
    year INTEGER,
    camera_model TEXT,
    lens TEXT,
    focal_mm REAL,
    aperture REAL,
    shutter_speed REAL,
    iso INTEGER,
    exposure_compensation REAL,
    flash INTEGER,
    width INTEGER,
    height INTEGER,
    orientation INTEGER,
    rating INTEGER,
    author TEXT,
    groupe INTEGER NOT NULL DEFAULT 1,
    location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    file_size INTEGER,
    mtime_ns INTEGER,
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_photos_taken_at ON photos(taken_at);
CREATE INDEX IF NOT EXISTS idx_photos_year ON photos(year);
CREATE INDEX IF NOT EXISTS idx_photos_model ON photos(camera_model);
CREATE INDEX IF NOT EXISTS idx_photos_lens ON photos(lens);
CREATE INDEX IF NOT EXISTS idx_photos_iso ON photos(iso);
CREATE INDEX IF NOT EXISTS idx_photos_focal ON photos(focal_mm);
CREATE INDEX IF NOT EXISTS idx_photos_aperture ON photos(aperture);
CREATE INDEX IF NOT EXISTS idx_photos_shutter ON photos(shutter_speed);
CREATE INDEX IF NOT EXISTS idx_photos_correction ON photos(exposure_compensation);
CREATE INDEX IF NOT EXISTS idx_photos_rating ON photos(rating);
CREATE INDEX IF NOT EXISTS idx_photos_groupe ON photos(groupe);
CREATE INDEX IF NOT EXISTS idx_photos_location ON photos(location_id);

CREATE TABLE IF NOT EXISTS photo_people (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'xmp',
    PRIMARY KEY (photo_id, person_id)
);
CREATE INDEX IF NOT EXISTS idx_photo_people_person ON photo_people(person_id, photo_id);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    normalized TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_keywords_label ON keywords(label COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS photo_keywords (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    PRIMARY KEY (photo_id, keyword_id)
);
CREATE INDEX IF NOT EXISTS idx_photo_keywords_keyword ON photo_keywords(keyword_id, photo_id);

CREATE TABLE IF NOT EXISTS taxa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    scientific_name TEXT,
    parent_id INTEGER REFERENCES taxa(id) ON DELETE CASCADE,
    depth INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_taxa_parent ON taxa(parent_id);
CREATE INDEX IF NOT EXISTS idx_taxa_path ON taxa(path);
CREATE INDEX IF NOT EXISTS idx_taxa_scientific ON taxa(scientific_name);

CREATE TABLE IF NOT EXISTS photo_taxa (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    taxon_id INTEGER NOT NULL REFERENCES taxa(id) ON DELETE CASCADE,
    is_primary INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (photo_id, taxon_id)
);
CREATE INDEX IF NOT EXISTS idx_photo_taxa_taxon ON photo_taxa(taxon_id, photo_id);
"""


class SchemaError(RuntimeError):
    """La base existe mais n'est pas au schéma attendu."""


_DEFER_COMMITS = False


def _commit(conn: sqlite3.Connection) -> None:
    """Commit immédiat, sauf pendant un import groupé.

    Les helpers historiques faisaient un commit par photo. Pour une mise à
    jour de dossier, on regroupe les écritures dans une transaction par lot.
    """
    if not _DEFER_COMMITS:
        conn.commit()


class deferred_commits:
    """Context manager utilisé par les imports par lots."""

    def __enter__(self):
        global _DEFER_COMMITS
        self.previous = _DEFER_COMMITS
        _DEFER_COMMITS = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        global _DEFER_COMMITS
        _DEFER_COMMITS = self.previous
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path() -> Path:
    return environment.photos_db()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('created_at', ?) "
        "ON CONFLICT(key) DO NOTHING",
        (now_iso(),),
    )
    _commit(conn)


def _schema_version(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


# Colonnes ajoutées après la première publication du schéma v4. Elles restent
# compatibles avec la version 4 : la base est migrée en place à la première
# ouverture en écriture.
_ADDED_PHOTO_COLUMNS = {
    "flash": "INTEGER",
    "groupe": "INTEGER NOT NULL DEFAULT 1",
}


def _ensure_photo_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(photos)")}
    for name, sql_type in _ADDED_PHOTO_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE photos ADD COLUMN {name} {sql_type}")
    if any(name not in columns for name in _ADDED_PHOTO_COLUMNS):
        conn.commit()
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_photos_groupe ON photos(groupe)"
    )
    conn.commit()


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Supprime ``photos.filename`` : le nom se déduit de ``relpath``."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(photos)")}
    if "filename" in columns:
        conn.execute("ALTER TABLE photos DROP COLUMN filename")
    _ensure_photo_columns(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def ensure_schema(conn: sqlite3.Connection, readonly: bool = False) -> None:
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if not tables:
        if readonly:
            raise SchemaError("La base photos.db est vide.")
        create_schema(conn)
        return

    version = _schema_version(conn)
    if version is None:
        raise SchemaError(
            "photos.db utilise encore l'ancien schéma. "
            "Exécutez `python -m dataBase.sync reset` pour repartir d'une base propre."
        )
    if version == SCHEMA_VERSION:
        if not readonly:
            _ensure_photo_columns(conn)
        return
    if version == "3":
        if readonly:
            # Le code v4 ne lit plus ``filename`` : une base v3 reste lisible
            # jusqu'à la prochaine ouverture en écriture, qui la migre.
            return
        _migrate_v3_to_v4(conn)
        return
    raise SchemaError(
        f"Schéma photos.db {version!r} incompatible avec {SCHEMA_VERSION!r}. "
        "Exécutez `python -m dataBase.sync reset` puis mettez à jour dossier par dossier."
    )


def connect(
    db_path: Path | str | None = None,
    readonly: bool = False,
) -> sqlite3.Connection:
    target = Path(db_path) if db_path is not None else path()
    target = target.expanduser()
    if readonly:
        if not target.is_file():
            raise FileNotFoundError(target)
        conn = sqlite3.connect(
            f"file:{quote(str(target))}?mode=ro",
            uri=True,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(conn, readonly=True)
        return conn

    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    ensure_schema(conn)
    return conn


def write_connect() -> sqlite3.Connection:
    """Connexion d'écriture, sans créer une base absente par accident."""
    target = path()
    if not target.is_file():
        raise FileNotFoundError(f"photos.db introuvable : {target}")
    return connect(target)


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    _commit(conn)


def relpath_for(photo: Path | str, root: Path | str) -> str:
    photo_path = Path(photo).expanduser().resolve()
    root_path = Path(root).expanduser().resolve()
    try:
        return photo_path.relative_to(root_path).as_posix()
    except ValueError as exc:
        raise ValueError(f"{photo_path} n'est pas sous {root_path}") from exc


def absolute_path(relpath: str, root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else environment.photos_root()
    return (base / relpath).resolve()


def normalize_name(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("-", " ")
    return " ".join(text.casefold().split())


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip()


def split_person_name(full_name: str) -> tuple[str, str]:
    parts = str(full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], " ".join(parts[1:])


def person_label(prenom: str, nom: str, id_people: int | None = None) -> str:
    label = " ".join(part for part in (str(prenom or "").strip(), str(nom or "").strip()) if part)
    return label or (f"Personne {id_people}" if id_people is not None else "")


def _person_dict(row: sqlite3.Row) -> dict:
    prenom = row["prenom"] or ""
    nom = row["nom"] or ""
    surnom = row["surnom"] or ""
    label = person_label(prenom, nom, row["id"])
    return {
        "id_people": row["id"],
        "id": row["id"],
        "prenom": prenom,
        "nom": nom,
        "surnom": surnom,
        "nickname": surnom,
        "label": label,
        "name": label,
        "id_pere": row["id_pere"],
        "id_mere": row["id_mere"],
    }


def load_people_from_connection(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, prenom, nom, surnom, id_pere, id_mere "
        "FROM people ORDER BY nom, prenom, id"
    ).fetchall()
    return [_person_dict(row) for row in rows]


def load_people(db_path: Path | str | None = None) -> list[dict]:
    with closing(connect(db_path)) as conn:
        return load_people_from_connection(conn)


def build_index(people: Iterable[dict]) -> dict[str, int]:
    index: dict[str, int] = {}
    for person in people:
        id_people = int(person["id_people"])
        prenom = person.get("prenom") or ""
        nom = person.get("nom") or ""
        surnom = person.get("surnom") or ""
        variants = {
            person.get("label") or "",
            person.get("name") or "",
            f"{prenom} {nom}",
            f"{nom} {prenom}",
            f"{prenom} {surnom}",
            f"{prenom} {nom} {surnom}",
            f"{nom} {prenom} {surnom}",
        }
        for variant in variants:
            key = normalize_name(variant)
            if key and key not in index:
                index[key] = id_people
    return index


def insert_person(conn: sqlite3.Connection, name: str) -> dict:
    prenom, nom = split_person_name(name)
    if not prenom and not nom:
        raise ValueError("Nom de personne vide.")
    row = conn.execute(
        "SELECT id, prenom, nom, surnom, id_pere, id_mere "
        "FROM people WHERE prenom = ? AND nom = ? AND surnom = ''",
        (prenom, nom),
    ).fetchone()
    if row is not None:
        return _person_dict(row)
    try:
        cursor = conn.execute(
            "INSERT INTO people(prenom, nom, surnom) VALUES (?, ?, '')",
            (prenom, nom),
        )
        _commit(conn)
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT id, prenom, nom, surnom, id_pere, id_mere "
            "FROM people WHERE prenom = ? AND nom = ? AND surnom = ''",
            (prenom, nom),
        ).fetchone()
        if row is None:
            raise
        return _person_dict(row)
    row = conn.execute(
        "SELECT id, prenom, nom, surnom, id_pere, id_mere FROM people WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return _person_dict(row)


def set_photo_people(
    conn: sqlite3.Connection,
    photo_id: int,
    person_ids: Iterable[int],
    source: str = "xmp",
    replace: bool = True,
) -> None:
    ids = sorted({int(value) for value in person_ids})
    if replace:
        conn.execute("DELETE FROM photo_people WHERE photo_id = ?", (photo_id,))
    if ids:
        conn.executemany(
            "INSERT OR IGNORE INTO photo_people(photo_id, person_id, source) VALUES (?, ?, ?)",
            [(photo_id, person_id, source) for person_id in ids],
        )
    _commit(conn)


def ensure_photo(conn: sqlite3.Connection, relpath: str, root: Path | str | None = None) -> int:
    row = conn.execute("SELECT id FROM photos WHERE relpath = ?", (relpath,)).fetchone()
    if row is not None:
        return int(row["id"])
    photo_path = absolute_path(relpath, root)
    stat = photo_path.stat() if photo_path.is_file() else None
    cursor = conn.execute(
        "INSERT INTO photos(relpath, file_size, mtime_ns, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (
            relpath,
            stat.st_size if stat else None,
            stat.st_mtime_ns if stat else None,
            now_iso(),
        ),
    )
    _commit(conn)
    return int(cursor.lastrowid)


def photo_id_by_relpath(conn: sqlite3.Connection, relpath: str) -> int | None:
    row = conn.execute("SELECT id FROM photos WHERE relpath = ?", (relpath,)).fetchone()
    return int(row["id"]) if row else None


def taxa_for_path(root: Path | str, photo: Path | str) -> list[dict]:
    """Taxons liés à une photo, avec leur nom et leur lignée.

    Sert notamment à nettoyer les métadonnées Faune avant de retirer toutes
    les relations ``photo_taxa`` d'une photo. Une base absente n'est pas une
    erreur : l'application Faune peut fonctionner sans catalogue.
    """
    try:
        relpath = relpath_for(photo, root)
        conn = connect(readonly=True)
    except (FileNotFoundError, ValueError):
        return []
    with closing(conn) as connection:
        photo_id = photo_id_by_relpath(connection, relpath)
        if photo_id is None:
            return []
        rows = connection.execute(
            "SELECT t.name, t.scientific_name, t.path "
            "FROM photo_taxa pt JOIN taxa t ON t.id = pt.taxon_id "
            "WHERE pt.photo_id = ? ORDER BY t.path",
            (photo_id,),
        ).fetchall()
        return [dict(row) for row in rows]


PHOTO_FIELDS = (
    "taken_at", "year", "camera_model", "lens", "focal_mm", "aperture",
    "shutter_speed", "iso", "exposure_compensation", "flash", "width", "height",
    "orientation", "rating", "author", "location_id", "file_size", "mtime_ns",
)


def upsert_photo(conn: sqlite3.Connection, record: dict) -> int:
    relpath = str(record["relpath"])
    row = conn.execute("SELECT id FROM photos WHERE relpath = ?", (relpath,)).fetchone()
    if row is not None:
        sets = ["updated_at = ?"]
        values: list[Any] = [now_iso()]
        for field in PHOTO_FIELDS:
            value = record.get(field)
            if value is not None:
                sets.append(f"{field} = ?")
                values.append(value)
        values.append(int(row["id"]))
        conn.execute(f"UPDATE photos SET {', '.join(sets)} WHERE id = ?", values)
        _commit(conn)
        return int(row["id"])

    columns = ["relpath", *PHOTO_FIELDS, "updated_at"]
    values = [relpath, *[record.get(field) for field in PHOTO_FIELDS], now_iso()]
    placeholders = ", ".join("?" for _ in columns)
    cursor = conn.execute(
        f"INSERT INTO photos({', '.join(columns)}) VALUES ({placeholders})", values
    )
    _commit(conn)
    return int(cursor.lastrowid)


def photo_paths(conn: sqlite3.Connection, only_labelled: bool = False) -> list[str]:
    sql = "SELECT relpath FROM photos"
    if only_labelled:
        sql += " WHERE EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id = photos.id)"
    sql += " ORDER BY relpath"
    return [row["relpath"] for row in conn.execute(sql)]


def photo_ids_by_relpath(conn: sqlite3.Connection, root: Path | str | None = None) -> dict[str, int]:
    del root  # conservé pour compatibilité avec les anciens appels
    return {
        row["relpath"]: int(row["id"])
        for row in conn.execute("SELECT id, relpath FROM photos")
    }


def people_links(conn: sqlite3.Connection) -> dict[int, set[int]]:
    links: dict[int, set[int]] = {}
    for row in conn.execute("SELECT photo_id, person_id FROM photo_people"):
        links.setdefault(int(row["photo_id"]), set()).add(int(row["person_id"]))
    return links


def apply_link_operations(conn: sqlite3.Connection, operations: Iterable[tuple]) -> None:
    for operation in operations:
        action, photo_id, person_id = operation
        if action == "add":
            conn.execute(
                "INSERT OR IGNORE INTO photo_people(photo_id, person_id) VALUES (?, ?)",
                (int(photo_id), int(person_id)),
            )
        elif action in {"remove", "del", "delete"}:
            conn.execute(
                "DELETE FROM photo_people WHERE photo_id = ? AND person_id = ?",
                (int(photo_id), int(person_id)),
            )
        else:
            raise ValueError(f"Opération inconnue : {action}")
    _commit(conn)


def family_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT p.id, p.prenom, p.nom, p.surnom, p.id_pere, p.id_mere, "
        "  (SELECT COUNT(*) FROM photo_people pp WHERE pp.person_id = p.id) AS photo_count "
        "FROM people p ORDER BY p.nom, p.prenom, p.id"
    ).fetchall()
    result = []
    for row in rows:
        person = _person_dict(row)
        person["photo_count"] = int(row["photo_count"])
        result.append(person)
    return result


def family_payload(conn: sqlite3.Connection) -> dict:
    rows = family_rows(conn)
    nodes: list[dict] = []
    parents_of: dict[str, list[str]] = {}
    couples: set[tuple[str, str]] = set()
    photo_counts: dict[str, int] = {}
    for person in rows:
        node_id = f"p{person['id_people']}"
        nodes.append({
            "id": node_id,
            "name": person["label"],
            "nickname": person.get("surnom") or "",
            "id_people": person["id_people"],
            "id_pere": person.get("id_pere"),
            "id_mere": person.get("id_mere"),
            "photoCount": person["photo_count"],
        })
        photo_counts[node_id] = person["photo_count"]
        parents: list[str] = []
        father = person.get("id_pere")
        mother = person.get("id_mere")
        if father:
            parents.append(f"p{father}")
        if mother:
            parents.append(f"p{mother}")
        if parents:
            parents_of[node_id] = parents
        if father and mother:
            couples.add((f"p{father}", f"p{mother}"))
    ranked = sorted(
        nodes,
        key=lambda node: (
            node["photoCount"],
            1 if node["id"] in parents_of else 0,
            node["name"],
        ),
        reverse=True,
    )
    default_self = ranked[0]["id"] if ranked else None
    return {
        "cfg": {
            "id": "family",
            "title": "Personnes",
            "subtitle": "Arbre généalogique et photos annotées",
            "selfLabel": "Personne",
            "concept": "Personne",
            "expandAllEnabled": True,
            "genSpacing": 180,
            "nodeSpacing": 170,
            "clusterSpacing": 34,
        },
        "nodes": nodes,
        "parentsOf": parents_of,
        "couples": [list(pair) for pair in sorted(couples)],
        "defaultSelf": default_self,
        "photoCounts": photo_counts,
    }


def row_count(conn: sqlite3.Connection, table: str) -> int:
    allowed = {
        "photos", "people", "photo_people", "keywords", "photo_keywords",
        "taxa", "photo_taxa", "locations",
    }
    if table not in allowed:
        raise ValueError(f"Table inconnue : {table}")
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _segments(labels: Iterable[str]) -> list[str]:
    result = []
    for label in labels:
        text = clean_text(label)
        if text:
            result.append(text)
    return result


def get_or_create_keyword(conn: sqlite3.Connection, label: str) -> int | None:
    """Retourne l'id d'un mot-clé atomique, en le créant si nécessaire."""
    text = clean_text(label).strip(" ,;:")
    normalized = annotations.normalize_name(text)
    if not normalized:
        return None
    row = conn.execute(
        "SELECT id FROM keywords WHERE normalized = ?", (normalized,)
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cursor = conn.execute(
        "INSERT OR IGNORE INTO keywords(label, normalized) VALUES (?, ?)",
        (text, normalized),
    )
    if cursor.lastrowid:
        return int(cursor.lastrowid)
    row = conn.execute(
        "SELECT id FROM keywords WHERE normalized = ?", (normalized,)
    ).fetchone()
    return int(row["id"]) if row is not None else None


def get_or_create_keywords(conn: sqlite3.Connection, labels: Iterable[str]) -> list[int]:
    ids = []
    for label in labels:
        keyword_id = get_or_create_keyword(conn, label)
        if keyword_id is not None:
            ids.append(keyword_id)
    return sorted(set(ids))


def set_photo_keywords(
    conn: sqlite3.Connection, photo_id: int, keyword_ids: Iterable[int], replace: bool = True
) -> None:
    ids = sorted({int(value) for value in keyword_ids if value is not None})
    if replace:
        conn.execute("DELETE FROM photo_keywords WHERE photo_id = ?", (photo_id,))
    if ids:
        conn.executemany(
            "INSERT OR IGNORE INTO photo_keywords(photo_id, keyword_id) VALUES (?, ?)",
            [(photo_id, keyword_id) for keyword_id in ids],
        )
    _commit(conn)


def prune_unused_keywords(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        "DELETE FROM keywords WHERE NOT EXISTS ("
        "  SELECT 1 FROM photo_keywords pk WHERE pk.keyword_id = keywords.id"
        ")"
    )
    _commit(conn)
    return int(cursor.rowcount or 0)


_FAUNA_TAXON_ROOTS = {
    "Animaux",
    "Insectes s.l.",
    "Mammifères",
    "Oiseaux",
    "Papillon",
}


def _keyword_plural_forms(key: str) -> tuple[str, ...]:
    if key.endswith("s") and len(key) > 3:
        return (key, key[:-1])
    return (key, key + "s")


def _taxonomy_keyword_index(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """Libellés taxonomiques connus et genres scientifiques de la base.

    Le sélecteur ne doit pas présenter les identifications Faune comme de
    simples mots-clés. On se limite à la table ``taxa`` (légère) et à quelques
    formes anciennes repérables par leur orthographe.
    """
    keys: set[str] = set()
    genera: set[str] = set()
    for row in conn.execute("SELECT name, scientific_name, path FROM taxa"):
        root = str(row["path"] or "").split("/", 1)[0]
        values = [row["name"], row["scientific_name"]]
        for value in values:
            text = clean_text(value)
            key = annotations.normalize_name(text)
            if not key:
                continue
            keys.add(key)
            # Les anciens mots-clés pouvaient conserver une liste de synonymes
            # dans un seul libellé : chaque variante doit aussi être reconnue.
            variants = [key]
            for part in re.split(r"[,;]", text):
                part_key = annotations.normalize_name(part)
                if part_key:
                    keys.add(part_key)
                    variants.append(part_key)
            # Le thésaurus utilise souvent le pluriel (« Insectes »,
            # « Libellules ») alors que les anciens mots-clés sont au singulier.
            if root in _FAUNA_TAXON_ROOTS:
                for variant in variants:
                    keys.update(_keyword_plural_forms(variant))
        scientific = annotations.normalize_name(row["scientific_name"])
        if scientific:
            genera.add(scientific.split(" ", 1)[0])
    return keys, genera


def _looks_like_fauna_keyword(label: str, keys: set[str], genera: set[str]) -> bool:
    text = clean_text(label)
    key = annotations.normalize_name(text)
    if not key:
        return False
    if key in keys:
        return True
    # Une liste de synonymes dans un seul mot-clé est une trace des anciennes
    # identifications Faune ; les mots-clés v4 sont atomiques.
    if "," in text or ";" in text:
        return True
    tokens = key.split()
    if len(tokens) >= 2 and tokens[1][:1].islower() and text[:1].isupper():
        if tokens[0] in genera:
            return True
        # Noms scientifiques anciens dont le genre n'est plus dans la base
        # courante (Apis mellifera, Equus ferus, Decticus verrucivorus…).
        if tokens[1].endswith((
            "a", "ae", "us", "um", "is", "i", "ii", "orum", "arum",
            "ensis", "ense", "es", "ens", "oides",
        )):
            return True
    # Nom vernaculaire composé, par exemple « Choucas des tours ».
    if len(tokens) >= 3 and text[:1].isupper():
        if any(token in {"de", "des", "du", "d", "la", "le", "les"} for token in tokens[1:]):
            return True
    return False


def keywords_with_counts(
    conn: sqlite3.Connection, exclude_fauna: bool = False
) -> list[dict]:
    rows = conn.execute(
        "SELECT k.id, k.label, COUNT(pk.photo_id) AS count "
        "FROM keywords k LEFT JOIN photo_keywords pk ON pk.keyword_id = k.id "
        "GROUP BY k.id, k.label "
        "ORDER BY count DESC, k.label COLLATE NOCASE"
    ).fetchall()
    keys: set[str] = set()
    genera: set[str] = set()
    if exclude_fauna:
        keys, genera = _taxonomy_keyword_index(conn)
    result = []
    for row in rows:
        if exclude_fauna and _looks_like_fauna_keyword(row["label"], keys, genera):
            continue
        result.append({
            "id": int(row["id"]),
            "label": row["label"],
            "count": int(row["count"]),
        })
    return result


def keyword_rows_for_labels(conn: sqlite3.Connection, labels: Iterable[str]) -> list[sqlite3.Row]:
    normalized = [
        key
        for key in (annotations.normalize_name(clean_text(label)) for label in labels)
        if key
    ]
    normalized = sorted(set(normalized))
    if not normalized:
        return []
    placeholders = ", ".join("?" for _ in normalized)
    return conn.execute(
        f"SELECT id, label FROM keywords WHERE normalized IN ({placeholders})",
        normalized,
    ).fetchall()


def get_or_create_taxon_path(
    conn: sqlite3.Connection,
    lineage: Iterable[str],
    scientific_name: str | None = None,
) -> int | None:
    segments = _segments(lineage)
    if not segments:
        return None
    parent_id = None
    leaf_id = None
    for index, segment in enumerate(segments):
        current_path = "/".join(segments[: index + 1])
        row = conn.execute("SELECT id FROM taxa WHERE path = ?", (current_path,)).fetchone()
        if row is not None:
            leaf_id = int(row["id"])
            if index == len(segments) - 1 and scientific_name:
                conn.execute(
                    "UPDATE taxa SET scientific_name = COALESCE(NULLIF(scientific_name, ''), ?) WHERE id = ?",
                    (clean_text(scientific_name), leaf_id),
                )
        else:
            cursor = conn.execute(
                "INSERT INTO taxa(name, scientific_name, parent_id, depth, path) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    segment,
                    clean_text(scientific_name) if index == len(segments) - 1 else None,
                    parent_id,
                    index,
                    current_path,
                ),
            )
            leaf_id = int(cursor.lastrowid)
        parent_id = leaf_id
    _commit(conn)
    return leaf_id


def set_photo_taxa(
    conn: sqlite3.Connection, photo_id: int, taxon_ids: Iterable[int], replace: bool = True
) -> None:
    ids = sorted({int(value) for value in taxon_ids if value is not None})
    if replace:
        conn.execute("DELETE FROM photo_taxa WHERE photo_id = ?", (photo_id,))
    if ids:
        conn.executemany(
            "INSERT OR IGNORE INTO photo_taxa(photo_id, taxon_id) VALUES (?, ?)",
            [(photo_id, taxon_id) for taxon_id in ids],
        )
    _commit(conn)


def _fauna_ranks(segments: list[str]) -> tuple[int, int, int]:
    """Indices de la classe et de l'ordre dans une lignée taxonomique.

    TAXREF place la classe sous ``Animaux > Chordés``. Les thésaurus locaux
    utilisent souvent la classe comme racine (``Oiseaux``, ``Mammifères``,
    ``Insectes s.l.``). On renvoie aussi l'indice à partir duquel les niveaux
    supérieurs (règne, phylum) doivent être masqués dans le graphe.
    """
    if segments and annotations.normalize_name(segments[0]) == "animaux":
        if len(segments) >= 3:
            return 2, 3 if len(segments) > 3 else 2, 2
        return 0, 1 if len(segments) > 1 else 0, 2
    return 0, 1 if len(segments) > 1 else 0, 0


def taxa_payload(conn: sqlite3.Connection) -> dict:
    cfg = {
        "id": "fauna", "title": "Faune", "subtitle": "Classe › ordre › espèce",
        "selfLabel": "Nœud", "concept": "Taxon", "expandAllEnabled": True,
        "genSpacing": 170, "nodeSpacing": 160, "clusterSpacing": 30,
        "clusterTitle": "Ordres",
    }
    rows = conn.execute(
        "SELECT t.id, t.name, t.scientific_name, t.parent_id, t.depth, t.path "
        "FROM taxa t ORDER BY t.depth, t.name"
    ).fetchall()
    if not rows:
        return {
            "cfg": cfg,
            "nodes": [], "parentsOf": {}, "couples": [], "defaultSelf": None,
            "photoCounts": {}, "classes": [],
        }
    by_id = {int(row["id"]): dict(row) for row in rows}
    path_to_id = {row["path"]: int(row["id"]) for row in rows}
    photo_sets: dict[int, set[int]] = {key: set() for key in by_id}
    for link in conn.execute(
        "SELECT pt.photo_id, t.path FROM photo_taxa pt JOIN taxa t ON t.id = pt.taxon_id"
    ):
        photo_id = int(link["photo_id"])
        segments = str(link["path"]).split("/")
        for index in range(1, len(segments) + 1):
            taxon_id = path_to_id.get("/".join(segments[:index]))
            if taxon_id is not None:
                photo_sets[taxon_id].add(photo_id)

    # Le graphe ne montre que les noms qui ont réellement des photos. Un
    # ancêtre sans photo directe reste utile s'il a des descendants observés :
    # son compteur agrégé est alors supérieur à zéro. Pour les animaux, on
    # masque en plus le règne et le phylum (``Animaux > Chordés``) afin que
    # les classes (Oiseaux, Mammifères…) soient les racines du graphe.
    kept = {key for key, photos in photo_sets.items() if photos}
    display_keys: set[int] = set()
    for key in kept:
        segments = str(by_id[key]["path"]).split("/")
        drop_before = _fauna_ranks(segments)[2]
        if len(segments) - 1 >= drop_before:
            display_keys.add(key)

    display_parent: dict[int, int | None] = {}
    for key in display_keys:
        parent = by_id[key]["parent_id"]
        while parent is not None and int(parent) not in display_keys:
            parent = by_id[int(parent)]["parent_id"]
        display_parent[key] = int(parent) if parent is not None else None

    children: dict[int, list[int]] = {}
    parents_of: dict[str, list[str]] = {}
    for key, parent in display_parent.items():
        if parent is not None and parent != key:
            children.setdefault(parent, []).append(key)
            parents_of[f"t{key}"] = [f"t{parent}"]

    counts = {key: len(photo_sets[key]) for key in display_keys}
    nodes = []
    classes: list[dict] = []
    for key, row in by_id.items():
        if key not in display_keys:
            continue
        segments = str(row["path"]).split("/")
        class_index, order_index, _ = _fauna_ranks(segments)
        class_id = path_to_id.get("/".join(segments[: class_index + 1]))
        order_id = path_to_id.get("/".join(segments[: order_index + 1]))
        class_id = class_id if class_id in display_keys else key
        order_id = order_id if order_id in display_keys else class_id
        class_name = by_id[class_id]["name"]
        order_name = by_id[order_id]["name"]
        is_class = key == class_id
        is_order = key == order_id and order_id != class_id
        if is_class:
            rank = "Classe"
            group_label = class_name
        elif is_order:
            rank = "Ordre"
            group_label = f"{class_name} · {order_name}"
        else:
            order_depth = int(by_id[order_id]["depth"])
            rank = "Famille" if int(row["depth"]) == order_depth + 1 else ""
            group_label = f"{class_name} · {order_name}"
        node = {
            "id": f"t{key}",
            "name": row["name"],
            "scientific": row["scientific_name"] or "",
            "depth": int(row["depth"]),
            "photoCount": counts[key],
            "rank": rank,
            "classId": f"t{class_id}",
            "className": class_name,
            "isClass": is_class,
            "groupId": f"t{order_id}",
            "groupLabel": group_label,
        }
        nodes.append(node)
        if is_class:
            classes.append({
                "id": f"t{class_id}",
                "name": class_name,
                "photoCount": counts[key],
            })

    classes.sort(key=lambda item: (-item["photoCount"], item["name"]))
    roots = [node for node in nodes if node["id"] not in parents_of]
    candidates = [node for node in roots if node.get("isClass")] or roots
    ranked = sorted(candidates, key=lambda node: (-node["photoCount"], node["name"]))
    default_self = ranked[0]["id"] if ranked else nodes[0]["id"] if nodes else None
    return {
        "cfg": cfg,
        "nodes": nodes,
        "parentsOf": parents_of,
        "couples": [],
        "defaultSelf": default_self,
        "photoCounts": {f"t{key}": value for key, value in counts.items()},
        "childrenCount": {f"t{key}": len(value) for key, value in children.items()},
        "classes": classes,
    }


def get_or_create_location(
    conn: sqlite3.Connection,
    country: str | None,
    city: str | None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> int:
    country_text = clean_text(country)
    city_text = clean_text(city)
    if not country_text and not city_text and latitude is not None and longitude is not None:
        country_text = "Coordonnées GPS"
        city_text = f"{float(latitude):.5f}, {float(longitude):.5f}"
    path_value = f"{country_text or 'Inconnu'}/{city_text or 'Lieu inconnu'}"
    row = conn.execute("SELECT id FROM locations WHERE path = ?", (path_value,)).fetchone()
    if row is not None:
        location_id = int(row["id"])
        if latitude is not None and longitude is not None:
            conn.execute(
                "UPDATE locations SET latitude = COALESCE(?, latitude), longitude = COALESCE(?, longitude) "
                "WHERE id = ?",
                (latitude, longitude, location_id),
            )
            _commit(conn)
        return location_id
    cursor = conn.execute(
        "INSERT INTO locations(country, city, latitude, longitude, path) VALUES (?, ?, ?, ?, ?)",
        (country_text, city_text, latitude, longitude, path_value),
    )
    _commit(conn)
    return int(cursor.lastrowid)


def set_photo_location(conn: sqlite3.Connection, photo_id: int, location_id: int | None) -> None:
    conn.execute("UPDATE photos SET location_id = ? WHERE id = ?", (location_id, photo_id))
    _commit(conn)


def locations_with_counts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT l.id, l.country, l.city, l.latitude, l.longitude, l.path, COUNT(p.id) AS photo_count "
        "FROM locations l LEFT JOIN photos p ON p.location_id = l.id "
        "GROUP BY l.id ORDER BY l.country, l.city"
    ).fetchall()
    return [dict(row) for row in rows]


def locations_payload(conn: sqlite3.Connection) -> dict:
    rows = locations_with_counts(conn)
    countries: dict[str, dict] = {}
    for row in rows:
        country = row["country"] or "Inconnu"
        city = row["city"] or "Lieu inconnu"
        country_node = countries.setdefault(country, {
            "id": f"country:{country}",
            "name": country,
            "type": "country",
            "photoCount": 0,
            "latitudes": [],
            "longitudes": [],
            "children": [],
        })
        country_node["photoCount"] += int(row["photo_count"])
        if row["latitude"] is not None:
            country_node["latitudes"].append(float(row["latitude"]))
        if row["longitude"] is not None:
            country_node["longitudes"].append(float(row["longitude"]))
        country_node["children"].append({
            "id": f"l{row['id']}",
            "name": city,
            "type": "city",
            "lat": row["latitude"],
            "lng": row["longitude"],
            "photoCount": int(row["photo_count"]),
            "zoom": 10,
        })
    hierarchy = []
    for node in countries.values():
        latitudes = node.pop("latitudes")
        longitudes = node.pop("longitudes")
        if latitudes and longitudes:
            node["lat"] = sum(latitudes) / len(latitudes)
            node["lng"] = sum(longitudes) / len(longitudes)
        else:
            node["lat"] = None
            node["lng"] = None
        node["zoom"] = 4 if node["lat"] is not None else None
        node["children"].sort(key=lambda child: (-child["photoCount"], child["name"]))
        hierarchy.append(node)
    hierarchy.sort(key=lambda node: (-node["photoCount"], node["name"]))
    return {"hierarchy": hierarchy}


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _placeholders(values: Iterable[Any]) -> str:
    return ", ".join("?" for _ in values)


def _chunks(values: Iterable[int], size: int = 500) -> Iterable[list[int]]:
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _int_values(selections: Iterable[str], prefix: str) -> list[int]:
    result = []
    for value in selections:
        text = str(value)
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            result.append(int(text[len(prefix):]))
    return result


def photos_payload(conn: sqlite3.Connection, rows: Iterable[sqlite3.Row]) -> list[dict]:
    rows = list(rows)
    if not rows:
        return []
    ids = [int(row["id"]) for row in rows]
    people: dict[int, list[str]] = {}
    keywords: dict[int, list[str]] = {}
    taxa: dict[int, list[dict]] = {}
    for chunk in _chunks(ids):
        placeholders = _placeholders(chunk)
        for link in conn.execute(
            f"SELECT pp.photo_id, pe.prenom, pe.nom FROM photo_people pp "
            f"JOIN people pe ON pe.id = pp.person_id WHERE pp.photo_id IN ({placeholders}) "
            "ORDER BY pe.nom, pe.prenom",
            chunk,
        ):
            people.setdefault(int(link["photo_id"]), []).append(
                person_label(link["prenom"], link["nom"])
            )
        for link in conn.execute(
            f"SELECT pk.photo_id, k.label FROM photo_keywords pk "
            f"JOIN keywords k ON k.id = pk.keyword_id WHERE pk.photo_id IN ({placeholders}) "
            "ORDER BY k.label",
            chunk,
        ):
            keywords.setdefault(int(link["photo_id"]), []).append(link["label"])
        for link in conn.execute(
            f"SELECT pt.photo_id, t.name, t.scientific_name, t.path FROM photo_taxa pt "
            f"JOIN taxa t ON t.id = pt.taxon_id WHERE pt.photo_id IN ({placeholders}) "
            "ORDER BY t.scientific_name, t.name",
            chunk,
        ):
            taxa.setdefault(int(link["photo_id"]), []).append({
                "name": link["name"],
                "scientific": link["scientific_name"] or "",
                "path": link["path"],
            })

    result = []
    for row in rows:
        photo_id = int(row["id"])
        keys = set(row.keys())
        record = {
            "id": photo_id,
            "relpath": row["relpath"],
            "filename": Path(row["relpath"]).name,
            "date": row["taken_at"],
            "taken_at": row["taken_at"],
            "year": row["year"],
            "model": row["camera_model"],
            "camera_model": row["camera_model"],
            "lens": row["lens"],
            "focal": row["focal_mm"],
            "focal_mm": row["focal_mm"],
            "aperture": row["aperture"],
            "speed": row["shutter_speed"],
            "shutter_speed": row["shutter_speed"],
            "iso": row["iso"],
            "correction": row["exposure_compensation"],
            "exposure_compensation": row["exposure_compensation"],
            "flash": row["flash"] if "flash" in keys else None,
            "width": row["width"],
            "height": row["height"],
            "orientation": row["orientation"],
            "rating": row["rating"],
            "author": row["author"],
            "groupe": (
                int(row["groupe"])
                if "groupe" in keys and row["groupe"] is not None
                else 1
            ),
            "people": people.get(photo_id, []),
            "keywords": keywords.get(photo_id, []),
            "taxa": taxa.get(photo_id, []),
            "thumbUrl": f"/api/photos/{photo_id}/thumbnail",
            "fileUrl": f"/api/photos/{photo_id}/file",
        }
        if "loc_country" in keys:
            record["location"] = {
                "country": row["loc_country"],
                "city": row["loc_city"],
                "latitude": row["loc_latitude"],
                "longitude": row["loc_longitude"],
            }
        result.append(record)
    return result


PHOTO_ORDER_BY = {
    "date_desc": "p.taken_at IS NULL, p.taken_at DESC, p.id DESC",
    "date_asc": "p.taken_at IS NULL, p.taken_at ASC, p.id ASC",
    "rating_desc": (
        "p.rating IS NULL, p.rating DESC, "
        "p.taken_at IS NULL, p.taken_at DESC, p.id DESC"
    ),
    "rating_asc": (
        "p.rating IS NULL, p.rating ASC, "
        "p.taken_at IS NULL, p.taken_at DESC, p.id DESC"
    ),
    "filename_asc": "p.relpath COLLATE NOCASE ASC, p.id ASC",
    "filename_desc": "p.relpath COLLATE NOCASE DESC, p.id DESC",
    "random": "RANDOM()",
}


def search_photos(
    conn: sqlite3.Connection,
    *,
    mode: str | None = None,
    dataset: str = "family",
    selections: Iterable[str] | None = None,
    min_rating: int | None = 0,
    year_min: int | None = None,
    year_max: int | None = None,
    model: str | None = None,
    lens: str | None = None,
    iso_min: int | None = None,
    iso_max: int | None = None,
    focal_min: float | None = None,
    focal_max: float | None = None,
    aperture_min: float | None = None,
    aperture_max: float | None = None,
    shutter_min: float | None = None,
    shutter_max: float | None = None,
    correction_min: float | None = None,
    correction_max: float | None = None,
    limit: int = 500,
    offset: int = 0,
    sort: str = "date_desc",
    include_ids: bool = True,
) -> dict:
    selections = [str(value) for value in (selections or [])]
    sort = str(sort or "date_desc")
    if sort not in PHOTO_ORDER_BY:
        sort = "date_desc"
    order_by = PHOTO_ORDER_BY[sort]
    where: list[str] = []
    params: list[Any] = []

    if mode == "graph":
        if dataset == "fauna" or str(dataset).startswith("fauna"):
            ids = _int_values(selections, "t")
            if ids:
                rows = conn.execute(
                    f"SELECT path FROM taxa WHERE id IN ({_placeholders(ids)})", ids
                ).fetchall()
                paths = [row["path"] for row in rows]
                if paths:
                    conditions = []
                    for taxon_path in paths:
                        conditions.append("(t.path = ? OR t.path LIKE ? ESCAPE '\\')")
                        params.extend([taxon_path, _like_escape(taxon_path) + "/%"])
                    where.append(
                        "EXISTS (SELECT 1 FROM photo_taxa pt JOIN taxa t ON t.id = pt.taxon_id "
                        "WHERE pt.photo_id = p.id AND (" + " OR ".join(conditions) + "))"
                    )
                else:
                    where.append("1 = 0")
            else:
                where.append("1 = 0")
        else:
            ids = _int_values(selections, "p")
            if ids:
                where.append(
                    f"EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id = p.id "
                    f"AND pp.person_id IN ({_placeholders(ids)}))"
                )
                params.extend(ids)
            else:
                where.append("1 = 0")
    elif mode == "keywords":
        keyword_rows = keyword_rows_for_labels(conn, selections)
        if keyword_rows:
            ids = sorted({int(row["id"]) for row in keyword_rows})
            where.append(
                "EXISTS (SELECT 1 FROM photo_keywords pk "
                f"WHERE pk.photo_id = p.id AND pk.keyword_id IN ({_placeholders(ids)}))"
            )
            params.extend(ids)
        else:
            where.append("1 = 0")
    elif mode == "map":
        conditions = []
        for value in selections:
            if value.startswith("country:"):
                conditions.append("l.country = ?")
                params.append(value.split(":", 1)[1])
            elif value.startswith("l") and value[1:].isdigit():
                conditions.append("l.id = ?")
                params.append(int(value[1:]))
        if conditions:
            where.append("(" + " OR ".join(conditions) + ")")
        else:
            where.append("1 = 0")

    if min_rating:
        where.append("p.rating >= ?")
        params.append(int(min_rating))
    if year_min is not None:
        where.append("p.year >= ?")
        params.append(int(year_min))
    if year_max is not None:
        where.append("p.year <= ?")
        params.append(int(year_max))
    if model:
        where.append("p.camera_model = ?")
        params.append(model)
    if lens:
        where.append("p.lens = ?")
        params.append(lens)
    for column, minimum, maximum in (
        ("p.iso", iso_min, iso_max),
        ("p.focal_mm", focal_min, focal_max),
        ("p.aperture", aperture_min, aperture_max),
        ("p.shutter_speed", shutter_min, shutter_max),
        ("p.exposure_compensation", correction_min, correction_max),
    ):
        if minimum is not None:
            where.append(f"{column} >= ?")
            params.append(minimum)
        if maximum is not None:
            where.append(f"{column} <= ?")
            params.append(maximum)

    base = "FROM photos p LEFT JOIN locations l ON l.id = p.location_id"
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = int(conn.execute(f"SELECT COUNT(*) {base}{clause}", params).fetchone()[0])
    ids: list[int] = []
    if include_ids:
        ids = [
            int(row["id"])
            for row in conn.execute(
                f"SELECT p.id {base}{clause} ORDER BY {order_by}",
                params,
            )
        ]
    limit = max(1, min(int(limit), 2000))
    offset = max(0, int(offset))
    rows = conn.execute(
        f"SELECT p.*, l.country AS loc_country, l.city AS loc_city, "
        f"l.latitude AS loc_latitude, l.longitude AS loc_longitude {base}{clause} "
        f"ORDER BY {order_by} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {
        "photos": photos_payload(conn, rows),
        "total": total,
        "ids": ids,
        "limit": limit,
        "offset": offset,
        "sort": sort,
    }


def technical_facets(conn: sqlite3.Connection) -> dict:
    def values(column: str) -> list[str]:
        return [
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT {column} FROM photos WHERE {column} IS NOT NULL AND {column} != '' "
                f"ORDER BY {column}"
            )
        ]

    def bounds(column: str) -> tuple[Any, Any]:
        row = conn.execute(
            f"SELECT MIN({column}), MAX({column}) FROM photos WHERE {column} IS NOT NULL"
        ).fetchone()
        return row[0], row[1]

    result = {
        "models": values("camera_model"),
        "lenses": values("lens"),
    }
    for name, column in (
        ("year", "year"), ("iso", "iso"), ("focal", "focal_mm"),
        ("aperture", "aperture"), ("shutter", "shutter_speed"),
        ("correction", "exposure_compensation"),
    ):
        result[f"{name}_min"], result[f"{name}_max"] = bounds(column)
    return result


def photos_by_ids(conn: sqlite3.Connection, photo_ids: Iterable[int]) -> list[dict]:
    ids = [int(value) for value in photo_ids]
    if not ids:
        return []
    rows = []
    for chunk in _chunks(ids):
        rows.extend(
            conn.execute(
                f"SELECT p.*, l.country AS loc_country, l.city AS loc_city, "
                f"l.latitude AS loc_latitude, l.longitude AS loc_longitude "
                f"FROM photos p LEFT JOIN locations l ON l.id = p.location_id "
                f"WHERE p.id IN ({_placeholders(chunk)})",
                chunk,
            ).fetchall()
        )
    by_id = {int(row["id"]): row for row in rows}
    ordered = [by_id[value] for value in ids if value in by_id]
    return photos_payload(conn, ordered)


def record_location_for_path(
    root: Path | str,
    photo: Path | str,
    latitude: float,
    longitude: float,
    city: str | None,
    country: str | None,
) -> int:
    relpath = relpath_for(photo, root)
    with closing(write_connect()) as conn:
        photo_id = ensure_photo(conn, relpath, root)
        location_id = get_or_create_location(conn, country, city, latitude, longitude)
        set_photo_location(conn, photo_id, location_id)
        return photo_id


def set_photo_group(conn: sqlite3.Connection, photo_id: int, group: int) -> None:
    """Associe une photo à un groupe d'accès (entier positif)."""
    value = int(group)
    if value < 1:
        raise ValueError("Le groupe doit être un entier positif.")
    conn.execute("UPDATE photos SET groupe = ? WHERE id = ?", (value, photo_id))
    _commit(conn)


def record_group_for_path(
    root: Path | str,
    photo: Path | str,
    group: int,
) -> int:
    """Met à jour le groupe d'accès d'une photo dans ``photos.db``.

    Le groupe est un attribut de l'index, pas une métadonnée XMP : il n'est
    donc pas écrit dans le fichier et survit aux synchronisations qui ne
    fournissent pas de nouvelle valeur.
    """
    value = int(group)
    if value < 1:
        raise ValueError("Le groupe doit être un entier positif.")
    relpath = relpath_for(photo, root)
    with closing(write_connect()) as conn:
        photo_id = ensure_photo(conn, relpath, root)
        set_photo_group(conn, photo_id, value)
    return value


def record_people_for_path(
    root: Path | str,
    photo: Path | str,
    names: Iterable[str],
    source: str = "xmp",
) -> int:
    relpath = relpath_for(photo, root)
    with closing(write_connect()) as conn:
        photo_id = ensure_photo(conn, relpath, root)
        person_ids = []
        for name in names:
            label = clean_text(name)
            if not label:
                continue
            person_ids.append(insert_person(conn, label)["id_people"])
        set_photo_people(conn, photo_id, person_ids, source=source, replace=True)
        return photo_id


def record_taxon_for_path(
    root: Path | str,
    photo: Path | str,
    taxon: dict | None,
    validated: bool = True,
    previous_taxon: dict | None = None,
) -> int:
    relpath = relpath_for(photo, root)
    with closing(write_connect()) as conn:
        photo_id = ensure_photo(conn, relpath, root)
        if not taxon:
            if validated:
                set_photo_taxa(conn, photo_id, [], replace=True)
            return photo_id
        lineage = [
            clean_text(value)
            for value in (taxon.get("lineage") or [])
            if clean_text(value)
        ]
        scientific = clean_text(taxon.get("scientificName"))
        vernacular = clean_text(taxon.get("vernacularName"))
        if not lineage:
            label = vernacular or scientific
            lineage = [label] if label else []
        taxon_id = get_or_create_taxon_path(conn, lineage, scientific or None)
        if taxon_id is None:
            return photo_id
        if validated:
            set_photo_taxa(conn, photo_id, [taxon_id], replace=True)
            # Le XMP est nettoyé par l'app Faune ; on garde la base cohérente
            # immédiatement en retirant les mêmes termes de photo_keywords.
            taxon_labels = {
                annotations.normalize_name(scientific),
                annotations.normalize_name(vernacular),
            }
            if previous_taxon:
                taxon_labels.update({
                    annotations.normalize_name(previous_taxon.get("scientificName")),
                    annotations.normalize_name(previous_taxon.get("vernacularName")),
                })
            taxon_keys = sorted(taxon_labels - {""})
            if taxon_keys:
                placeholders = _placeholders(taxon_keys)
                conn.execute(
                    "DELETE FROM photo_keywords WHERE photo_id = ? AND keyword_id IN ("
                    f"SELECT id FROM keywords WHERE normalized IN ({placeholders})"
                    ")",
                    [photo_id, *taxon_keys],
                )
                _commit(conn)
        else:
            conn.execute(
                "DELETE FROM photo_taxa WHERE photo_id = ? AND taxon_id = ?",
                (photo_id, taxon_id),
            )
            _commit(conn)
        return photo_id


_THESAURUS_CACHE: tuple[str, object] | None = None
_TAXONOMY_CACHE: tuple[str, object | None] | None = None


def _thesaurus_index():
    """Charge le thésaurus une seule fois par dossier configuré."""
    global _THESAURUS_CACHE
    directory = environment.thesaurus_dir()
    key = str(directory)
    if _THESAURUS_CACHE is not None and _THESAURUS_CACHE[0] == key:
        return _THESAURUS_CACHE[1]
    index = annotations.load_thesaurus(directory)
    _THESAURUS_CACHE = (key, index)
    return index


def _taxonomy_index():
    """Charge TAXREF une seule fois par dossier configuré."""
    global _TAXONOMY_CACHE
    directory = environment.taxref_dir()
    key = str(directory)
    if _TAXONOMY_CACHE is not None and _TAXONOMY_CACHE[0] == key:
        return _TAXONOMY_CACHE[1]
    index = annotations.load_taxonomy(
        directory, environment.thesaurus_dir()
    )
    _TAXONOMY_CACHE = (key, index)
    return index


def record_keywords_rating_author_for_path(
    root: Path | str,
    photo: Path | str,
    keywords: Iterable[str] | None,
    rating: int | None,
    author: str | None,
    hierarchies: Iterable[str] | None = None,
) -> int:
    relpath = relpath_for(photo, root)
    with closing(write_connect()) as conn:
        photo_id = ensure_photo(conn, relpath, root)
        if rating is not None:
            conn.execute("UPDATE photos SET rating = ? WHERE id = ?", (int(rating), photo_id))
        if author is not None:
            conn.execute(
                "UPDATE photos SET author = ? WHERE id = ?", (clean_text(author), photo_id)
            )
        _commit(conn)
        if keywords is not None:
            taxonomy = _taxonomy_index()
            thesaurus = (
                getattr(taxonomy, "thesaurus", None)
                if taxonomy is not None
                else None
            )
            if thesaurus is None:
                thesaurus = _thesaurus_index()
            preserve = (
                (
                    lambda text: annotations.resolve_taxon_text(
                        taxonomy, thesaurus, text
                    ) is not None
                )
                if thesaurus is not None or taxonomy is not None
                else None
            )
            flat_keywords: list[str] = []
            for keyword in keywords:
                flat_keywords.extend(
                    annotations.keyword_values(keyword, thesaurus, preserve=preserve)
                )
            person_index = build_index(load_people_from_connection(conn))
            person_ids, _taxa, labels = annotations.classify_keywords(
                flat_keywords, list(hierarchies or []), thesaurus, person_index,
                taxonomy=taxonomy,
            )
            keyword_ids = get_or_create_keywords(conn, labels)
            set_photo_keywords(conn, photo_id, keyword_ids, replace=True)
            if person_ids:
                set_photo_people(conn, photo_id, person_ids, replace=False)
        return photo_id
