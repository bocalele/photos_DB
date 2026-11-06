"""Génération d'albums photo HTML autonomes.

Le module ne dépend d'aucune bibliothèque externe. Il prépare les données
d'un album, puis injecte le CSS et le JavaScript de ``album.css`` /
``album.js`` dans un document unique. L'export final contient donc seulement
``index.html`` et un dossier ``images/`` : il fonctionne sans le serveur.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
MAX_PHOTOS = 2000

TEMPLATES = (
    {
        "id": "grid",
        "name": "Grille",
        "description": "Galerie régulière, sobre et rapide.",
    },
    {
        "id": "masonry",
        "name": "Mosaïque",
        "description": "Hauteurs variées, composition magazine.",
    },
    {
        "id": "slideshow",
        "name": "Diaporama",
        "description": "Plein écran, navigation au clavier.",
    },
    {
        "id": "book",
        "name": "Livre",
        "description": "Deux photos par page, ambiance éditoriale.",
    },
)

THEMES = (
    {"id": "dark", "name": "Sombre", "swatch": "#10131d"},
    {"id": "light", "name": "Clair", "swatch": "#f4f1e8"},
    {"id": "warm", "name": "Chaleureux", "swatch": "#241a15"},
    {"id": "paper", "name": "Papier", "swatch": "#ece8de"},
)

TEMPLATE_IDS = {item["id"] for item in TEMPLATES}
THEME_IDS = {item["id"] for item in THEMES}
MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


def options_payload() -> dict:
    return {
        "templates": [dict(item) for item in TEMPLATES],
        "themes": [dict(item) for item in THEMES],
        "maxPhotos": MAX_PHOTOS,
    }


def _text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _multiline(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    return text[:limit]


def _number(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_config(data: dict | None) -> dict:
    data = data or {}
    template = str(data.get("template") or "grid")
    if template not in TEMPLATE_IDS:
        template = "grid"
    theme = str(data.get("theme") or "dark")
    if theme not in THEME_IDS:
        theme = "dark"

    photo_ids: list[int] = []
    seen: set[int] = set()
    for value in data.get("photo_ids") or []:
        photo_id = _number(value)
        if photo_id <= 0 or photo_id in seen:
            continue
        seen.add(photo_id)
        photo_ids.append(photo_id)
        if len(photo_ids) >= MAX_PHOTOS:
            break

    cover_id = _number(data.get("cover_id"))
    if cover_id not in seen:
        cover_id = photo_ids[0] if photo_ids else 0

    return {
        "title": _text(data.get("title"), 120) or "Mon album",
        "subtitle": _text(data.get("subtitle"), 240),
        "intro": _multiline(data.get("intro"), 1200),
        "author": _text(data.get("author"), 120),
        "template": template,
        "theme": theme,
        "columns": max(2, min(6, _number(data.get("columns")) or 3)),
        "show_captions": bool(data.get("show_captions", True)),
        "show_metadata": bool(data.get("show_metadata", True)),
        "cover_id": cover_id,
        "photo_ids": photo_ids,
    }


def _format_date(value: Any) -> str:
    if not value:
        return ""
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value))
    if not match:
        return str(value)
    year, month, day = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        return f"{day:02d}/{month:02d}/{year}"
    return f"{day} {MONTHS[month - 1]} {year}"


def _place(record: dict) -> str:
    location = record.get("location") or {}
    city = _text(location.get("city"), 80)
    country = _text(location.get("country"), 80)
    if city and country and city.casefold() != country.casefold():
        return f"{city}, {country}"
    return city or country


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _format_shutter(value: Any) -> str:
    number = _as_float(value)
    if number is None or number <= 0:
        return ""
    if number >= 1:
        return f"{number:g} s"
    reciprocal = round(1 / number)
    return f"1/{reciprocal} s" if reciprocal > 0 else ""


def _format_correction(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return ""
    if abs(number) < 0.05:
        return "0 IL"
    return f"{number:+.1f} IL"


FLASH_LABELS = {
    0: "Non déclenché",
    1: "Déclenché",
    5: "Déclenché, retour non détecté",
    7: "Déclenché, retour détecté",
    9: "Activé, non déclenché",
    13: "Activé, retour non détecté",
    15: "Activé, retour détecté",
    16: "Désactivé",
    24: "Auto, non déclenché",
    25: "Auto, déclenché",
    29: "Auto, déclenché, retour non détecté",
    31: "Auto, déclenché, retour détecté",
    32: "Sans flash",
    48: "Désactivé, sans flash",
    65: "Déclenché, réduction des yeux rouges",
    69: "Déclenché, réduction des yeux rouges, retour non détecté",
    71: "Déclenché, réduction des yeux rouges, retour détecté",
    73: "Activé, réduction des yeux rouges",
    77: "Activé, réduction des yeux rouges, retour non détecté",
    79: "Activé, réduction des yeux rouges, retour détecté",
    89: "Auto, déclenché, réduction des yeux rouges",
    93: "Auto, déclenché, réduction des yeux rouges, retour non détecté",
    95: "Auto, déclenché, réduction des yeux rouges, retour détecté",
}


def _format_flash(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return ""
    code = int(number)
    if code < 0:
        return ""
    return FLASH_LABELS.get(code, f"Mode {code}")


def _technical_fields(record: dict) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    model = _text(record.get("camera_model"), 80)
    lens = _text(record.get("lens"), 120)
    focal = _as_float(record.get("focal_mm"))
    aperture = _as_float(record.get("aperture"))
    shutter = _format_shutter(record.get("shutter_speed"))
    iso = record.get("iso")
    correction = _format_correction(record.get("exposure_compensation"))
    flash = _format_flash(record.get("flash"))
    if model:
        fields.append({"label": "Boîtier", "value": model})
    if lens:
        fields.append({"label": "Objectif", "value": lens})
    if focal:
        fields.append({"label": "Focale", "value": f"{focal:g} mm"})
    if aperture:
        fields.append({"label": "Ouverture", "value": f"f/{aperture:g}"})
    if shutter:
        fields.append({"label": "Vitesse", "value": shutter})
    if iso:
        fields.append({"label": "ISO", "value": str(iso)})
    if correction:
        fields.append({"label": "Correction", "value": correction})
    if flash:
        fields.append({"label": "Flash", "value": flash})
    return fields


def _taxon_depth(taxon: dict) -> int:
    path = _text(taxon.get("path"), 300)
    return len([part for part in path.split("/") if part.strip()])


def _album_title(record: dict) -> tuple[str, set[str]]:
    """Choisit un titre lisible et les valeurs déjà utilisées comme titre."""
    taxa = [
        taxon
        for taxon in (record.get("taxa") or [])
        if isinstance(taxon, dict) and _text(taxon.get("name"), 80)
    ]
    if taxa:
        # Le taxon le plus précis (espèce, genre) est plus parlant que la classe.
        taxon = max(taxa, key=lambda item: (_taxon_depth(item), len(_text(item.get("name"), 80))))
        name = _text(taxon.get("name"), 80)
        return name, {name}

    people = [
        _text(name, 80)
        for name in (record.get("people") or [])
        if _text(name, 80)
    ]
    if people:
        shown = people[:2]
        title = ", ".join(shown) + ("…" if len(people) > 2 else "")
        return title, set(shown)

    for keyword in record.get("keywords") or []:
        name = _text(keyword, 60)
        if name:
            return name, {name}

    location = record.get("location") or {}
    city = _text(location.get("city"), 80)
    if city:
        return city, {city}

    filename = Path(str(record.get("relpath") or "")).stem
    return filename.replace("_", " ").strip() or "Photo", set()


def album_item(record: dict, image: str) -> dict:
    """Transforme un enregistrement de ``photos_payload`` en photo d'album."""
    date = _format_date(record.get("taken_at"))
    place = _place(record)
    camera = _text(record.get("camera_model"), 80)

    tags: list[str] = []
    for name in record.get("people") or []:
        label = _text(name, 80)
        if label and label not in tags:
            tags.append(label)
    for taxon in record.get("taxa") or []:
        label = _text(taxon.get("name"), 80) if isinstance(taxon, dict) else ""
        if label and label not in tags:
            tags.append(label)
    for keyword in record.get("keywords") or []:
        label = _text(keyword, 60)
        if label and label not in tags:
            tags.append(label)

    title, title_values = _album_title(record)
    tags = [tag for tag in tags if tag not in title_values]

    meta_parts = [date, place, camera]
    meta = " · ".join(part for part in meta_parts if part)

    return {
        "id": int(record["id"]),
        "src": image,
        "title": title,
        "date": date,
        "place": place,
        "meta": meta,
        "tags": tags[:6],
        "technical": _technical_fields(record),
    }


def _assets() -> tuple[str, str]:
    css = (BASE_DIR / "album.css").read_text(encoding="utf-8")
    javascript = (BASE_DIR / "album.js").read_text(encoding="utf-8")
    return css, javascript


def render_album_html(config: dict, photos: list[dict]) -> str:
    """Construit un document HTML autonome (CSS et JS inclus)."""
    config = normalize_config(config)
    css, javascript = _assets()
    payload = {"config": config, "photos": photos}
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(config["title"] or "Album photo")
    return f"""<!DOCTYPE html>
<html lang="fr" data-theme="{html.escape(config['theme'])}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="Sélection · album HTML">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body data-template="{html.escape(config['template'])}">
<div id="album-root"></div>
<script id="album-data" type="application/json">{data_json}</script>
<script>
{javascript}
</script>
</body>
</html>
"""
