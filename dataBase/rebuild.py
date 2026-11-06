#!/usr/bin/env python3
"""Reconstruit `photos.db` (schéma v4) depuis les métadonnées XMP/EXIF.

La base n'est plus alimentée par l'ancien import CSV : elle est reconstruite à
partir des fichiers réels, en conservant le catalogue des personnes (et leurs
liens familiaux) de l'ancienne base lorsqu'elle existe. Les mots-clés
numériques de l'ancienne base sont volontairement ignorés.

Exemples :

    python -m dataBase.rebuild --limit 200 --db /tmp/photos-test.db
    python -m dataBase.rebuild --replace
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dataBase import annotations, photos_db  # noqa: E402
from tools import environment  # noqa: E402
from tools.getMetaData import getGPS, get_metadata_many  # noqa: E402


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".psd", ".dng",
    ".cr2", ".cr3", ".nef", ".arw", ".raf", ".heic", ".webp",
}
EXCLUDE_PARTS = {
    "_data", "thumbnail", "thumbnails", "previews", ".git", "@eadir",
    "__pycache__", ".pixi",
}


def iter_files(root: Path, limit: int = 0) -> Iterable[Path]:
    root = root.expanduser().resolve()
    count = 0
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        lower_parts = {part.casefold() for part in candidate.relative_to(root).parts}
        if lower_parts & EXCLUDE_PARTS:
            continue
        yield candidate
        count += 1
        if limit and count >= limit:
            return


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_values(item))
        return result
    text = str(value).strip()
    return [text] if text else []


_OPEN_QUOTES = {"'": "'", '"': '"', "‘": "’", "“": "”"}
_CLOSING_QUOTES = set("'\"’”")


def _strip_outer_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] in "'\"‘“" and text[-1] in _CLOSING_QUOTES:
        return text[1:-1].strip()
    return text


def _list_items(text: str) -> list[tuple[str, bool]]:
    """Décode une valeur qui ressemble à une liste Python, même tronquée.

    Certains anciens outils ont écrit ``"['A', 'B']"`` dans un champ XMP/IPTC
    au lieu d'écrire deux valeurs distinctes. ExifTool renvoie alors cette
    chaîne telle quelle. On récupère les éléments complets et on signale le
    dernier élément lorsqu'il a été coupé (absence de guillemet fermant).
    """
    body = text.strip()
    if body.startswith("["):
        body = body[1:]
    if "]" in body:
        body = body.split("]", 1)[0]
    items: list[tuple[str, bool]] = []
    index = 0
    length = len(body)
    while index < length:
        while index < length and (body[index].isspace() or body[index] == ","):
            index += 1
        if index >= length:
            break
        if body[index] in _OPEN_QUOTES:
            opener = body[index]
            closer = _OPEN_QUOTES[opener]
            index += 1
            chars: list[str] = []
            truncated = True
            while index < length:
                char = body[index]
                if char == closer:
                    lookahead = index + 1
                    while lookahead < length and body[lookahead].isspace():
                        lookahead += 1
                    if lookahead >= length or body[lookahead] in ",]":
                        index = lookahead
                        truncated = False
                        break
                chars.append(char)
                index += 1
            value = "".join(chars).strip()
            if truncated and value and value[-1] in _CLOSING_QUOTES:
                value = value[:-1].rstrip()
                truncated = False
            if value:
                items.append((value, truncated))
        else:
            start = index
            while index < length and body[index] != ",":
                index += 1
            value = body[start:index].strip()
            if value.endswith("]"):
                value = value[:-1].strip()
            if value:
                items.append((value, False))
    return items


def _keyword_items(value: Any) -> list[tuple[str, bool]]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result: list[tuple[str, bool]] = []
        for item in value:
            result.extend(_keyword_items(item))
        return result
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and (
        text.endswith("]") or any(quote in text for quote in "'\"‘’“”")
    ):
        return _list_items(text)
    return [(_strip_outer_quotes(text), False)]


def _keyword_values(value: Any, thesaurus=None) -> list[str]:
    """Valeurs de mots-clés utilisables, sans représentation de liste Python."""
    result: list[str] = []
    for text, truncated in _keyword_items(value):
        text = text.strip()
        if not text:
            continue
        if truncated:
            completed = _complete_keyword(text, thesaurus)
            if completed is None:
                # Le dernier élément d'une liste tronquée est inutilisable :
                # mieux vaut l'ignorer que créer un mot-clé « Passérifor ».
                continue
            text = completed
        result.append(text)
    return result


def is_malformed_keyword_label(label: str) -> bool:
    """Vrai si un libellé en base est encore une liste Python ou une chaîne citée."""
    text = str(label or "").strip()
    if not text:
        return True
    items = _keyword_items(text)
    if len(items) != 1:
        return True
    value, truncated = items[0]
    return bool(truncated or value.strip() != text)


def _first(values: dict, *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_text(values: dict, *keys: str) -> str:
    value = _first(values, *keys)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", ".")
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", text)
    year_match = re.match(r"^(\d{4})", text)
    if year_match:
        year_value = int(year_match.group(1))
        if year_value < 1826 or year_value > 2100:
            return None
    text = text.replace("T", " ")
    for pattern in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
    ):
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.strptime(text[:19], pattern))
        except ValueError:
            continue
    match = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)} 00:00:00"
    return None


def load_thesaurus(directory: Path | str | None):
    return annotations.load_thesaurus(directory)


def load_taxonomy(
    taxref_directory: Path | str | None,
    thesaurus_directory: Path | str | None = None,
):
    return annotations.load_taxonomy(taxref_directory, thesaurus_directory)


def _choose_node(candidates):
    if not candidates:
        return None
    leaves = [node for node in candidates if node.scientific and not node.children]
    pool = leaves or candidates
    return min(pool, key=lambda node: (len(node.lineage()), node.name.casefold()))


def _node_taxon(node) -> dict:
    return {
        "scientificName": node.scientific or None,
        "vernacularName": node.name,
        "lineage": node.lineage(),
    }


def _most_specific(taxa: list[dict]) -> list[dict]:
    kept = []
    seen = set()
    for taxon in taxa:
        lineage = tuple(taxon.get("lineage") or [])
        if not lineage or lineage in seen:
            continue
        seen.add(lineage)
        if any(
            len(other.get("lineage") or []) > len(lineage)
            and tuple((other.get("lineage") or [])[: len(lineage)]) == lineage
            for other in taxa
        ):
            continue
        kept.append(taxon)
    return kept


def parse_record(
    categories: dict,
    thesaurus=None,
    preserve_keyword=None,
) -> tuple[dict, dict]:
    exif = categories.get("EXIF", {})
    iptc = categories.get("IPTC", {})
    xmp = categories.get("XMP", {})

    date_value = _first(exif, "DateTimeOriginal", "Photo.DateTimeOriginal")
    if date_value is None:
        date_value = _first(iptc, "DateCreated", "Application2.DateCreated")
    if date_value is None:
        date_value = _first(xmp, "DateCreated", "CreateDate", "xmp.CreateDate")
    taken_at = normalize_date(date_value)

    rating = _int(_first(xmp, "Rating"))
    if rating is None:
        rating = _int(_first(exif, "Rating", "Photo.Rating"))

    author = _first_text(xmp, "Creator", "Artist")
    if not author:
        author = _first_text(iptc, "By-line", "Creator")
    if not author:
        author = _first_text(exif, "Artist")

    record = {
        "taken_at": taken_at,
        "year": int(taken_at[:4]) if taken_at else None,
        "camera_model": _first_text(exif, "Model", "Image.Model") or None,
        "lens": _first_text(exif, "LensModel", "Photo.LensModel") or _first_text(xmp, "Lens") or None,
        "focal_mm": _float(
            _first(exif, "FocalLengthIn35mmFormat", "Photo.FocalLengthIn35mmFormat",
                   "FocalLengthIn35mmFilm", "FocalLength", "Photo.FocalLength")
        ),
        "aperture": _float(_first(exif, "FNumber", "Photo.FNumber")),
        "shutter_speed": _float(_first(exif, "ExposureTime", "Photo.ExposureTime")),
        "iso": _int(_first(exif, "ISO", "ISOSpeedRatings", "Photo.ISO", "Photo.ISOSpeedRatings")),
        "exposure_compensation": _float(
            _first(exif, "ExposureCompensation", "Photo.ExposureBiasValue",
                   "ExposureBiasValue", "Photo.ExposureCompensation")
        ),
        "flash": _int(_first(exif, "Flash", "Photo.Flash")),
        "width": _int(_first(exif, "ExifImageWidth", "ImageWidth", "Photo.ExifImageWidth")),
        "height": _int(_first(exif, "ExifImageHeight", "ImageHeight", "Photo.ExifImageHeight")),
        "orientation": _int(_first(exif, "Orientation")),
        "rating": rating,
        "author": author or None,
    }

    gps = getGPS(exif)
    latitude = _float(gps.get("GPSLatitude"))
    longitude = _float(gps.get("GPSLongitude"))
    if latitude is not None:
        latitude = abs(latitude)
        if str(gps.get("GPSLatitudeRef") or "").upper() in {"S", "SOUTH"}:
            latitude = -latitude
    if longitude is not None:
        longitude = abs(longitude)
        if str(gps.get("GPSLongitudeRef") or "").upper() in {"W", "WEST"}:
            longitude = -longitude

    city = _first_text(xmp, "Location") or _first_text(iptc, "Sub-location", "SubLocation")
    country = _first_text(xmp, "Country") or _first_text(
        iptc, "Country-PrimaryLocationName", "CountryName"
    )
    def preserve(text: str) -> bool:
        if preserve_keyword is not None and preserve_keyword(text):
            return True
        return annotations.taxon_for_text(thesaurus, text) is not None

    keywords = []
    # Seuls IPTC:Keywords et XMP-dc:Subject font foi. Les champs dérivés de
    # Lightroom (WeightedFlatSubject, LastKeywordXMP) peuvent conserver des
    # mots-clés supprimés et ne doivent pas les réintroduire lors d'une
    # reconstruction de photos.db.
    for category, keys in (
        (iptc, ("Keywords",)),
        (xmp, ("Subject",)),
    ):
        for key in keys:
            keywords.extend(
                annotations.keyword_values(
                    category.get(key), thesaurus, preserve=preserve
                )
            )
    hierarchies = []
    for key in ("HierarchicalSubject", "lr.hierarchicalSubject"):
        hierarchies.extend(annotations.hierarchy_values(xmp.get(key), thesaurus))
    regions = _values(xmp.get("RegionName"))

    return record, {
        "keywords": keywords,
        "hierarchies": hierarchies,
        "regions": regions,
        "location": {
            "latitude": latitude,
            "longitude": longitude,
            "city": city,
            "country": country,
        },
    }


def _relpath_from_legacy(path_text: str, roots: Iterable[Path], root: Path) -> str | None:
    raw = str(path_text or "").strip()
    if not raw:
        return None
    path = Path(raw)
    for candidate_root in (root, *roots):
        try:
            return path.relative_to(candidate_root).as_posix()
        except ValueError:
            continue
    marker = "/PHOTOS/"
    if marker in raw:
        return raw.split(marker, 1)[1]
    return None


def legacy_records(db_path: Path | str | None, root: Path) -> dict[str, dict]:
    if not db_path:
        return {}
    db_path = Path(db_path).expanduser()
    if not db_path.is_file():
        return {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        roots = environment.legacy_photo_roots()
        result = {}
        if "Photos" in tables:
            for row in conn.execute("SELECT * FROM Photos"):
                relpath = _relpath_from_legacy(row["path"], roots, root)
                if relpath:
                    result[relpath] = dict(row)
        elif "photos" in tables:
            for row in conn.execute("SELECT * FROM photos"):
                result[row["relpath"]] = {
                    "date": row["taken_at"],
                    "model": row["camera_model"],
                    "Lens": row["lens"],
                    "focal": row["focal_mm"],
                    "aperture": row["aperture"],
                    "speed": row["shutter_speed"],
                    "iso": row["iso"],
                    "correction": row["exposure_compensation"],
                    "rating": row["rating"],
                    "author": row["author"],
                    "groupe": (
                        row["groupe"]
                        if "groupe" in row.keys()
                        else 1
                    ),
                    "largeur": row["width"],
                    "hauteur": row["height"],
                }
        conn.close()
        return result
    except sqlite3.Error:
        return {}


def copy_legacy_people(conn: sqlite3.Connection, db_path: Path | str | None) -> int:
    if not db_path:
        return 0
    db_path = Path(db_path).expanduser()
    if not db_path.is_file():
        return 0
    try:
        old = sqlite3.connect(db_path)
        old.row_factory = sqlite3.Row
        tables = {
            row[0] for row in old.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "Peoples" in tables:
            rows = old.execute("SELECT * FROM Peoples").fetchall()
        elif "people" in tables:
            rows = old.execute("SELECT * FROM people").fetchall()
        else:
            old.close()
            return 0
        old.close()
    except sqlite3.Error:
        return 0
    def row_id(row) -> int:
        keys = set(row.keys())
        return int(row["id_people"] if "id_people" in keys else row["id"])

    inserted = 0
    for row in rows:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO people(id, prenom, nom, surnom) VALUES (?, ?, ?, ?)",
                (
                    row_id(row),
                    str(row["prenom"] or ""),
                    str(row["nom"] or ""),
                    str(row["surnom"] or ""),
                ),
            )
            inserted += 1
        except (sqlite3.Error, TypeError, ValueError):
            continue
    conn.commit()
    for row in rows:
        try:
            conn.execute(
                "UPDATE people SET id_pere = ?, id_mere = ? WHERE id = ?",
                (row["id_pere"], row["id_mere"], row_id(row)),
            )
        except (sqlite3.Error, TypeError, ValueError):
            continue
    conn.commit()
    return inserted


def _legacy_value(old: dict | None, *keys: str):
    if not old:
        return None
    for key in keys:
        value = old.get(key)
        if value not in (None, ""):
            return value
    return None


def _search_norm(text: str) -> str:
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", str(text).casefold())
    return " ".join(
        "".join(char for char in decomposed if not unicodedata.combining(char)).split()
    )


def _complete_keyword(text: str, thesaurus) -> str | None:
    """Complète un nom tronqué si le thésaurus ne propose qu'une suite."""
    if thesaurus is None:
        return None
    needle = _search_norm(text)
    if not needle:
        return None
    exact = thesaurus.by_vernacular.get(needle)
    if exact:
        return exact[0].name
    scientific_key = photos_db.normalize_name(text)
    exact_scientific = thesaurus.by_scientific.get(scientific_key)
    if exact_scientific is not None:
        return exact_scientific.name

    matches: dict[str, str] = {}
    for key, nodes in thesaurus.by_vernacular.items():
        if key.startswith(needle):
            for node in nodes:
                matches.setdefault(key, node.name)
    if scientific_key:
        for key, node in thesaurus.by_scientific.items():
            if key.startswith(scientific_key):
                matches.setdefault(key, node.name)
    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


def _person_id(name: str, conn: sqlite3.Connection, index: dict[str, int]) -> int | None:
    key = photos_db.normalize_name(name)
    if not key:
        return None
    if key in index:
        return index[key]
    person = photos_db.insert_person(conn, name)
    index[key] = person["id_people"]
    for variant in (person.get("label"), f"{person.get('nom','')} {person.get('prenom','')}"):
        variant_key = photos_db.normalize_name(variant)
        if variant_key:
            index.setdefault(variant_key, person["id_people"])
    return person["id_people"]


def classify(
    facets: dict,
    conn: sqlite3.Connection,
    thesaurus,
    person_index: dict[str, int],
    taxonomy=None,
) -> tuple[list[int], list[dict], list[str]]:
    person_ids: list[int] = []
    for name in facets.get("regions") or []:
        person_id = _person_id(name, conn, person_index)
        if person_id is not None:
            person_ids.append(person_id)

    keyword_person_ids, taxa, keyword_labels = annotations.classify_keywords(
        facets.get("keywords") or [],
        facets.get("hierarchies") or [],
        thesaurus,
        person_index,
        taxonomy=taxonomy,
    )
    person_ids.extend(keyword_person_ids)
    return sorted(set(person_ids)), taxa, keyword_labels


def _apply_legacy_fallback(record: dict, old: dict | None) -> None:
    if not old:
        return
    fallback = {
        "taken_at": normalize_date(_legacy_value(old, "date")),
        "camera_model": _legacy_value(old, "model"),
        "lens": _legacy_value(old, "Lens", "lens"),
        "focal_mm": _float(_legacy_value(old, "focal")),
        "aperture": _float(_legacy_value(old, "aperture")),
        "shutter_speed": _float(_legacy_value(old, "speed")),
        "iso": _int(_legacy_value(old, "iso")),
        "exposure_compensation": _float(_legacy_value(old, "correction")),
        "width": _int(_legacy_value(old, "largeur")),
        "height": _int(_legacy_value(old, "hauteur")),
        "rating": _int(_legacy_value(old, "rating")),
        "author": _legacy_value(old, "author"),
        "groupe": _int(_legacy_value(old, "groupe")),
    }
    for key, value in fallback.items():
        if record.get(key) is None and value not in (None, ""):
            record[key] = value
    if record.get("taken_at") and record.get("year") is None:
        record["year"] = int(str(record["taken_at"])[:4])


def import_record(
    conn: sqlite3.Connection,
    path: Path,
    root: Path,
    categories: dict,
    thesaurus,
    person_index: dict[str, int],
    legacy: dict[str, dict],
    stats: dict,
    taxonomy=None,
) -> None:
    relpath = path.relative_to(root).as_posix()
    record, facets = parse_record(
        categories,
        thesaurus,
        preserve_keyword=lambda text: photos_db.normalize_name(text) in person_index,
    )
    _apply_legacy_fallback(record, legacy.get(relpath))
    if not record.get("taken_at"):
        match = re.match(r"^(\d{4})[-_/]", relpath)
        if match:
            folder_year = int(match.group(1))
            if 1826 <= folder_year <= 2100:
                record["taken_at"] = f"{folder_year}-01-01 00:00:00"
                record["year"] = folder_year
    stat = path.stat()
    record.update({
        "relpath": relpath,
        "file_size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    })
    photo_id = photos_db.upsert_photo(conn, record)
    if record.get("groupe") is not None:
        photos_db.set_photo_group(conn, photo_id, int(record["groupe"]))

    person_ids, taxa, keyword_labels = classify(
        facets, conn, thesaurus, person_index, taxonomy=taxonomy
    )
    photos_db.set_photo_people(conn, photo_id, person_ids, replace=True)
    keyword_ids = photos_db.get_or_create_keywords(conn, keyword_labels)
    photos_db.set_photo_keywords(conn, photo_id, keyword_ids, replace=True)
    taxon_ids = [
        photos_db.get_or_create_taxon_path(
            conn, taxon.get("lineage") or [], taxon.get("scientificName")
        )
        for taxon in taxa
    ]
    photos_db.set_photo_taxa(conn, photo_id, taxon_ids, replace=True)

    location = facets.get("location") or {}
    if any(
        location.get(key) not in (None, "")
        for key in ("country", "city", "latitude", "longitude")
    ):
        location_id = photos_db.get_or_create_location(
            conn,
            location.get("country"),
            location.get("city"),
            location.get("latitude"),
            location.get("longitude"),
        )
        photos_db.set_photo_location(conn, photo_id, location_id)

    stats["photos"] += 1
    stats["people_links"] += len(person_ids)
    stats["keyword_links"] += len([value for value in keyword_ids if value is not None])
    stats["taxa_links"] += len([value for value in taxon_ids if value is not None])
    if person_ids:
        stats["photos_with_people"] += 1
    if taxa:
        stats["photos_with_taxa"] += 1


def process_batch(
    conn: sqlite3.Connection,
    paths: list[Path],
    root: Path,
    thesaurus,
    person_index: dict[str, int],
    legacy: dict[str, dict],
    stats: dict,
    taxonomy=None,
) -> None:
    try:
        metadata = get_metadata_many([str(path) for path in paths])
    except Exception as error:
        if len(paths) == 1:
            stats["errors"] += 1
            print(f"  erreur {paths[0]}: {error}", flush=True)
            return
        for path in paths:
            process_batch(
                conn, [path], root, thesaurus, person_index, legacy, stats,
                taxonomy=taxonomy,
            )
        return
    for path in paths:
        categories = metadata.get(str(path))
        if categories is None:
            stats["errors"] += 1
            continue
        try:
            import_record(
                conn, path, root, categories, thesaurus, person_index, legacy,
                stats, taxonomy=taxonomy,
            )
        except Exception as error:
            stats["errors"] += 1
            print(f"  erreur {path}: {error}", flush=True)


def backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_conn, sqlite3.connect(destination) as destination_conn:
        source_conn.backup(destination_conn)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Base cible (par défaut celle de preferences.json).")
    parser.add_argument("--root", help="Racine des photos (par défaut celle de preferences.json).")
    parser.add_argument("--thesaurus", help="Dossier des thésaurus (par défaut faune/Thesaurus).")
    parser.add_argument("--taxref", help="Dossier TAXREF (par défaut faune/TAXREF).")
    parser.add_argument("--legacy-db", help="Ancienne base pour reprendre les personnes et les champs absents.")
    parser.add_argument("--limit", type=int, default=0, help="Limite le nombre de fichiers (test).")
    parser.add_argument("--replace", action="store_true", help="Remplace la base cible après sauvegarde.")
    parser.add_argument("--dry-run", action="store_true", help="Liste les fichiers sans écrire.")
    parser.add_argument("--no-old-people", action="store_true", help="Ne reprend pas les personnes de l'ancienne base.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target = Path(args.db).expanduser() if args.db else environment.photos_db()
    root = Path(args.root).expanduser().resolve() if args.root else environment.photos_root().resolve()
    if not root.is_dir():
        print(f"Racine introuvable : {root}", file=sys.stderr)
        return 2
    taxonomy = load_taxonomy(
        args.taxref or environment.taxref_dir(),
        args.thesaurus or environment.thesaurus_dir(),
    )
    thesaurus = getattr(taxonomy, "thesaurus", None) if taxonomy is not None else None
    if thesaurus is None:
        thesaurus = load_thesaurus(args.thesaurus or environment.thesaurus_dir())
    if args.dry_run:
        files = list(iter_files(root, args.limit))
        print(f"{len(files)} fichier(s) à importer depuis {root}")
        print(f"Base cible : {target}")
        print(f"Thésaurus : {'oui' if thesaurus is not None else 'non'}")
        print(f"TAXREF : {'oui' if taxonomy is not None else 'non'}")
        return 0

    legacy_source = Path(args.legacy_db).expanduser() if args.legacy_db else (
        target if target.is_file() else None
    )
    if target.exists() and not args.replace:
        print(
            f"{target} existe déjà. Utilisez --replace pour la sauvegarder puis la reconstruire.",
            file=sys.stderr,
        )
        return 2

    backup_path = None
    if target.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = target.with_name(f"{target.stem}.legacy-{stamp}{target.suffix}")
        backup_database(target, backup_path)
        print(f"Sauvegarde : {backup_path}", flush=True)

    temp = target.with_name(f".{target.name}.rebuild-{os.getpid()}")
    for suffix in ("", "-wal", "-shm"):
        Path(str(temp) + suffix).unlink(missing_ok=True)

    conn = photos_db.connect(temp)
    # La base reconstruite est un fichier temporaire : la durabilité n'a
    # aucune utilité pendant l'import. Les nombreux commits par photo étaient
    # le principal coût sur le disque externe.
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -131072")
    stats = {
        "photos": 0, "people_links": 0, "keyword_links": 0, "taxa_links": 0,
        "photos_with_people": 0, "photos_with_taxa": 0, "errors": 0,
    }
    try:
        if legacy_source and not args.no_old_people:
            count = copy_legacy_people(conn, legacy_source)
            print(f"Personnes reprises de l'ancienne base : {count}", flush=True)
        person_index = photos_db.build_index(
            photos_db.load_people_from_connection(conn)
        )
        legacy = legacy_records(legacy_source, root)
        files = list(iter_files(root, args.limit))
        print(
            f"Import de {len(files)} fichier(s) depuis {root} "
            f"(thésaurus : {'oui' if thesaurus is not None else 'non'}, "
            f"TAXREF : {'oui' if taxonomy is not None else 'non'})",
            flush=True,
        )
        started = time.monotonic()
        batch: list[Path] = []
        for index_value, path in enumerate(files, start=1):
            batch.append(path)
            if len(batch) >= 100:
                process_batch(
                    conn, batch, root, thesaurus, person_index, legacy, stats,
                    taxonomy=taxonomy,
                )
                batch = []
                elapsed = max(time.monotonic() - started, 0.001)
                print(
                    f"  {index_value}/{len(files)} photos - {index_value / elapsed:.1f} fichiers/s "
                    f"- {stats['errors']} erreur(s)",
                    flush=True,
                )
        if batch:
            process_batch(
                conn, batch, root, thesaurus, person_index, legacy, stats,
                taxonomy=taxonomy,
            )

        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for key, value in (
            ("photo_count", stats["photos"]),
            ("rebuilt_at", photos_db.now_iso()),
            ("source_root", str(root)),
        ):
            photos_db.set_meta(conn, key, str(value))
        conn.commit()
        conn.close()

        for suffix in ("-wal", "-shm"):
            Path(str(temp) + suffix).unlink(missing_ok=True)
        os.replace(temp, target)
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
    except Exception:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(temp) + suffix).unlink(missing_ok=True)
        raise

    print(
        "Terminé : "
        f"{stats['photos']} photos, {stats['photos_with_people']} avec personnes, "
        f"{stats['photos_with_taxa']} avec faune, {stats['errors']} erreur(s).",
        flush=True,
    )
    print(f"Base reconstruite : {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
