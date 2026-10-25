#!/usr/bin/env python3
"""Base SQLite des visages : photos indexées, embeddings et personnes de référence.

La base vit à côté de `photos.db` (dossier `_DATA` du fonds photo) et ne contient
que des données reconstructibles : elle peut être supprimée et reconstruite par
`index.py` sans rien perdre d'irremplaçable.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools import environment  # noqa: E402

# Chemins décrits dans `preferences.json` (racine du projet, section
# « environment »), avec surcharge possible par les variables PHOTOS_*.
DEFAULT_PHOTO_ROOT = environment.photos_root()
DATA_DIR = environment.data_dir()
DEFAULT_DB_PATH = environment.face_db()
DEFAULT_PHOTOS_DB = environment.photos_db()
PREVIEWS_DIR = environment.previews_dir()

# Anciennes racines rencontrées dans `photos.db` (avant le disque externe),
# décrites dans `preferences.json` (environment.legacy_photo_roots).
LEGACY_ROOTS = environment.legacy_photo_roots()

SCHEMA_VERSION = "1"
# Version du traitement d'indexation. La changer force la réanalyse des photos
# déjà indexées (préviews plus grandes, nouveau détecteur, correction de bugs…).
PIPELINE_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS Meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Une ligne par photo passée à l'indexeur.
CREATE TABLE IF NOT EXISTS Photo (
    relpath TEXT PRIMARY KEY,       -- chemin relatif à la racine photo
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    size INTEGER NOT NULL,          -- pour invalider sans relire l'image
    mtime REAL NOT NULL,
    n_faces INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL,
    error TEXT,
    pipeline TEXT                   -- version du traitement qui a produit la ligne
);

-- Un visage détecté. Coordonnées normalisées (0..1) pour être indépendantes
-- de la taille de l'image utilisée pour la détection.
CREATE TABLE IF NOT EXISTS Face (
    id INTEGER PRIMARY KEY,
    relpath TEXT NOT NULL REFERENCES Photo(relpath) ON DELETE CASCADE,
    face_idx INTEGER NOT NULL,
    x1 REAL NOT NULL, y1 REAL NOT NULL,
    x2 REAL NOT NULL, y2 REAL NOT NULL,
    det_score REAL NOT NULL,
    embedding BLOB NOT NULL,        -- float32[512], normalisé L2
    UNIQUE(relpath, face_idx)
);
CREATE INDEX IF NOT EXISTS idx_face_relpath ON Face(relpath);

-- Étiquette connue d'un visage (région XMP, correction manuelle, …).
CREATE TABLE IF NOT EXISTS FaceLabel (
    id_face INTEGER PRIMARY KEY REFERENCES Face(id) ON DELETE CASCADE,
    id_people INTEGER NOT NULL,     -- people.id de photos.db
    name TEXT,
    score REAL,                     -- IoU avec la région XMP
    source TEXT NOT NULL            -- 'xmp' | 'manuel' | 'auto'
);
CREATE INDEX IF NOT EXISTS idx_facelabel_people ON FaceLabel(id_people);

-- Galerie de référence : un centroïde par personne reconnue.
CREATE TABLE IF NOT EXISTS RefPerson (
    id_people INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    n_faces INTEGER NOT NULL,
    centroid BLOB NOT NULL,         -- float32[512], normalisé L2
    seuil REAL,
    updated_at TEXT NOT NULL
);

-- Exemplaires de référence : la comparaison se fait sur le meilleur d'entre eux,
-- ce qui tient mieux compte des changements d'âge et de qualité que le centroïde.
CREATE TABLE IF NOT EXISTS RefFace (
    id INTEGER PRIMARY KEY,
    id_people INTEGER NOT NULL REFERENCES RefPerson(id_people) ON DELETE CASCADE,
    relpath TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refface_people ON RefFace(id_people);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(Photo)")}
    if "pipeline" not in columns:
        conn.execute("ALTER TABLE Photo ADD COLUMN pipeline TEXT")
    conn.execute(
        "INSERT INTO Meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def to_relpath(path: Path | str, root: Path | str = DEFAULT_PHOTO_ROOT) -> str:
    """Chemin canonique stocké en base, relatif à la racine du fonds photo."""
    resolved = Path(path).expanduser().resolve()
    for candidate in (Path(root).expanduser().resolve(), *(Path(r) for r in LEGACY_ROOTS)):
        try:
            return resolved.relative_to(candidate).as_posix()
        except ValueError:
            continue
    raise ValueError(f"Chemin hors de la racine photo ({root}) : {path}")


def abs_path(relpath: str, root: Path | str = DEFAULT_PHOTO_ROOT) -> Path:
    return Path(root).expanduser() / relpath


def normalize(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def pack(vector) -> bytes:
    return normalize(vector).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def photo_state(conn: sqlite3.Connection, relpath: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT size, mtime, error, pipeline FROM Photo WHERE relpath = ?", (relpath,)
    ).fetchone()


def is_current(conn: sqlite3.Connection, relpath: str, size: int, mtime: float) -> bool:
    row = photo_state(conn, relpath)
    return (
        bool(row)
        and row["size"] == size
        and abs(row["mtime"] - mtime) < 1e-6
        and row["pipeline"] == PIPELINE_VERSION
    )


def save_photo(
    conn: sqlite3.Connection,
    relpath: str,
    width: int,
    height: int,
    size: int,
    mtime: float,
    faces: list[dict],
    labels: list[dict] | None = None,
    pipeline: str = PIPELINE_VERSION,
) -> None:
    """Enregistre une photo, ses visages et leurs étiquettes, en une transaction."""
    labels_by_index = {label["face_idx"]: label for label in (labels or [])}
    with conn:
        conn.execute("DELETE FROM Face WHERE relpath = ?", (relpath,))
        conn.execute(
            """
            INSERT INTO Photo(relpath, width, height, size, mtime, n_faces, indexed_at, error, pipeline)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(relpath) DO UPDATE SET
                width = excluded.width,
                height = excluded.height,
                size = excluded.size,
                mtime = excluded.mtime,
                n_faces = excluded.n_faces,
                indexed_at = excluded.indexed_at,
                error = NULL,
                pipeline = excluded.pipeline
            """,
            (relpath, width, height, size, mtime, len(faces), now(), pipeline),
        )
        for index, face in enumerate(faces):
            cursor = conn.execute(
                """
                INSERT INTO Face(relpath, face_idx, x1, y1, x2, y2, det_score, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relpath,
                    index,
                    float(face["bbox"][0]),
                    float(face["bbox"][1]),
                    float(face["bbox"][2]),
                    float(face["bbox"][3]),
                    float(face["det_score"]),
                    pack(face["embedding"]),
                ),
            )
            label = labels_by_index.get(index)
            if label is not None:
                conn.execute(
                    """
                    INSERT INTO FaceLabel(id_face, id_people, name, score, source)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        cursor.lastrowid,
                        int(label["id_people"]),
                        label.get("name"),
                        float(label.get("score") or 0.0),
                        label.get("source", "xmp"),
                    ),
                )


def save_error(
    conn: sqlite3.Connection, relpath: str, size: int, mtime: float, message: str
) -> None:
    with conn:
        conn.execute("DELETE FROM Face WHERE relpath = ?", (relpath,))
        conn.execute(
            """
            INSERT INTO Photo(relpath, width, height, size, mtime, n_faces, indexed_at, error)
            VALUES (?, 0, 0, ?, ?, 0, ?, ?)
            ON CONFLICT(relpath) DO UPDATE SET
                size = excluded.size,
                mtime = excluded.mtime,
                n_faces = 0,
                indexed_at = excluded.indexed_at,
                error = excluded.error
            """,
            (relpath, size, mtime, now(), message),
        )


def summary(conn: sqlite3.Connection) -> dict:
    photos = conn.execute("SELECT COUNT(*) FROM Photo").fetchone()[0]
    errors = conn.execute("SELECT COUNT(*) FROM Photo WHERE error IS NOT NULL").fetchone()[0]
    faces = conn.execute("SELECT COUNT(*) FROM Face").fetchone()[0]
    labelled = conn.execute("SELECT COUNT(*) FROM FaceLabel").fetchone()[0]
    people = conn.execute("SELECT COUNT(DISTINCT id_people) FROM FaceLabel").fetchone()[0]
    reference = conn.execute("SELECT COUNT(*) FROM RefPerson").fetchone()[0]
    return {
        "photos": photos,
        "erreurs": errors,
        "visages": faces,
        "visages_etiquetes": labelled,
        "personnes_etiquetees": people,
        "personnes_reference": reference,
    }


def meta_float(conn: sqlite3.Connection, key: str, default: float) -> float:
    row = conn.execute("SELECT value FROM Meta WHERE key = ?", (key,)).fetchone()
    try:
        return float(row["value"]) if row else default
    except (TypeError, ValueError):
        return default


def photo_faces(conn: sqlite3.Connection, relpath: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.id, f.face_idx, f.x1, f.y1, f.x2, f.y2, f.det_score, f.embedding,
               l.id_people, l.name, l.score AS label_score
        FROM Face f
        LEFT JOIN FaceLabel l ON l.id_face = f.id
        WHERE f.relpath = ?
        ORDER BY f.face_idx
        """,
        (relpath,),
    ).fetchall()


def status_map(conn: sqlite3.Connection) -> dict[str, dict]:
    """État de chaque photo indexée : nombre de visages et de visages nommés."""
    status = {
        row["relpath"]: {"n_faces": row["n_faces"], "n_labels": 0, "error": row["error"]}
        for row in conn.execute("SELECT relpath, n_faces, error FROM Photo")
    }
    for row in conn.execute(
        """
        SELECT f.relpath, COUNT(*) AS n_labels
        FROM Face f JOIN FaceLabel l ON l.id_face = f.id
        GROUP BY f.relpath
        """
    ):
        if row["relpath"] in status:
            status[row["relpath"]]["n_labels"] = row["n_labels"]
    return status


def replace_labels(conn: sqlite3.Connection, relpath: str, labels: list[dict]) -> None:
    """Remplace les étiquettes des visages d'une photo."""
    face_ids = {
        row["face_idx"]: row["id"]
        for row in conn.execute(
            "SELECT id, face_idx FROM Face WHERE relpath = ?", (relpath,)
        )
    }
    with conn:
        conn.execute(
            "DELETE FROM FaceLabel WHERE id_face IN (SELECT id FROM Face WHERE relpath = ?)",
            (relpath,),
        )
        for label in labels:
            id_face = face_ids.get(label["face_idx"])
            if id_face is None:
                continue
            conn.execute(
                """
                INSERT INTO FaceLabel(id_face, id_people, name, score, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    id_face,
                    int(label["id_people"]),
                    label.get("name"),
                    float(label.get("score") or 0.0),
                    label.get("source", "xmp"),
                ),
            )


def clear_exemplars(conn: sqlite3.Connection, relpath: str) -> None:
    """Retire les visages étalons issus d'une photo (avant réécriture)."""
    affected = [
        row["id_people"]
        for row in conn.execute(
            "SELECT DISTINCT id_people FROM RefFace WHERE relpath = ?", (relpath,)
        )
    ]
    if not affected:
        return
    with conn:
        conn.execute("DELETE FROM RefFace WHERE relpath = ?", (relpath,))
        for id_people in affected:
            rows = conn.execute(
                "SELECT embedding FROM RefFace WHERE id_people = ?", (id_people,)
            ).fetchall()
            if not rows:
                conn.execute("DELETE FROM RefPerson WHERE id_people = ?", (id_people,))
                continue
            matrix = np.stack([unpack(row["embedding"]) for row in rows])
            conn.execute(
                "UPDATE RefPerson SET n_faces = ?, centroid = ?, updated_at = ? WHERE id_people = ?",
                (len(rows), pack(matrix.mean(axis=0)), now(), id_people),
            )


def add_exemplar(
    conn: sqlite3.Connection,
    id_people: int,
    name: str,
    relpath: str,
    embedding,
) -> None:
    """Ajoute (ou remplace) un visage validé dans la galerie de référence."""
    seuil = meta_float(conn, "reference_threshold", 0.43)
    with conn:
        exists = conn.execute(
            "SELECT 1 FROM RefPerson WHERE id_people = ?", (int(id_people),)
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO RefPerson(id_people, name, n_faces, centroid, seuil, updated_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (int(id_people), name, pack(embedding), seuil, now()),
            )
        conn.execute(
            "DELETE FROM RefFace WHERE relpath = ? AND id_people = ?",
            (relpath, int(id_people)),
        )
        conn.execute(
            "INSERT INTO RefFace(id_people, relpath, embedding) VALUES (?, ?, ?)",
            (int(id_people), relpath, pack(embedding)),
        )
        rows = conn.execute(
            "SELECT embedding FROM RefFace WHERE id_people = ?", (int(id_people),)
        ).fetchall()
        matrix = np.stack([unpack(row["embedding"]) for row in rows])
        conn.execute(
            "UPDATE RefPerson SET n_faces = ?, centroid = ?, updated_at = ? WHERE id_people = ?",
            (len(rows), pack(matrix.mean(axis=0)), now(), int(id_people)),
        )


def load_references(conn: sqlite3.Connection) -> dict:
    """Galerie de référence prête à comparer, en mémoire."""
    rows = conn.execute(
        """
        SELECT f.id_people, r.name, f.relpath, f.embedding
        FROM RefFace f JOIN RefPerson r ON r.id_people = f.id_people
        ORDER BY f.id_people
        """
    ).fetchall()

    ids: list[int] = []
    names: list[str] = []
    vectors: list[np.ndarray] = []
    relpaths: list[str] = []
    groups: list[tuple[int, int]] = []
    for row in rows:
        id_people = int(row["id_people"])
        if not ids or ids[-1] != id_people:
            ids.append(id_people)
            names.append(row["name"])
            groups.append((len(vectors), len(vectors)))
        groups[-1] = (groups[-1][0], len(vectors) + 1)
        vectors.append(unpack(row["embedding"]))
        relpaths.append(row["relpath"])

    matrix = np.stack(vectors) if vectors else np.zeros((0, 512), np.float32)
    return {
        "ids": ids,
        "names": names,
        "matrix": matrix,
        "groups": groups,
        "relpaths": np.array(relpaths, dtype=object),
        "threshold": meta_float(conn, "reference_threshold", 0.43),
        "suggestion": meta_float(conn, "reference_suggestion", 0.33),
    }


# --- lectures et écritures utilisées par l'interface et les outils ----------


def face_row(
    conn: sqlite3.Connection, relpath: str, face_idx: int
) -> sqlite3.Row | None:
    """Un visage indexé, par photo et position."""
    return conn.execute(
        "SELECT * FROM Face WHERE relpath = ? AND face_idx = ?",
        (relpath, face_idx),
    ).fetchone()


def photo_dimensions(
    conn: sqlite3.Connection, relpath: str
) -> sqlite3.Row | None:
    """Largeur et hauteur enregistrées lors de l'indexation d'une photo."""
    return conn.execute(
        "SELECT width, height FROM Photo WHERE relpath = ?", (relpath,)
    ).fetchone()


def unlabelled_faces(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Visages indexés qui n'ont encore aucune étiquette."""
    return conn.execute(
        """
        SELECT f.relpath, f.face_idx, f.det_score, f.embedding
        FROM Face f LEFT JOIN FaceLabel l ON l.id_face = f.id
        WHERE l.id_face IS NULL
        """
    ).fetchall()


def labelled_faces(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Visages étiquetés par XMP ou manuellement, pour la galerie de référence."""
    return conn.execute(
        """
        SELECT f.relpath, f.y1, f.y2, f.det_score, f.embedding,
               l.id_people, l.name, l.score, l.source
        FROM Face f
        JOIN FaceLabel l ON l.id_face = f.id
        WHERE l.source IN ('xmp', 'manuel')
        """
    ).fetchall()


def reference_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Exemplaires de référence avec le nom de leur personne."""
    return conn.execute(
        """
        SELECT r.id_people, r.name, f.embedding
        FROM RefFace f JOIN RefPerson r ON r.id_people = f.id_people
        ORDER BY r.id_people
        """
    ).fetchall()


def meta_values(conn: sqlite3.Connection, keys: list[str]) -> dict[str, str]:
    """Valeurs de Meta pour les clés demandées."""
    if not keys:
        return {}
    placeholders = ", ".join("?" for _ in keys)
    return {
        row["key"]: row["value"]
        for row in conn.execute(
            f"SELECT key, value FROM Meta WHERE key IN ({placeholders})", keys
        )
    }


def replace_reference(
    conn: sqlite3.Connection,
    people: list[tuple],
    faces: list[tuple],
    meta: dict[str, str],
) -> None:
    """Remplace atomiquement la galerie de référence et ses métadonnées."""
    with conn:
        conn.execute("DELETE FROM RefFace")
        conn.execute("DELETE FROM RefPerson")
        conn.executemany(
            """
            INSERT INTO RefPerson(id_people, name, n_faces, centroid, seuil, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            people,
        )
        conn.executemany(
            "INSERT INTO RefFace(id_people, relpath, embedding) VALUES (?, ?, ?)",
            faces,
        )
        conn.executemany(
            "INSERT INTO Meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            list(meta.items()),
        )
