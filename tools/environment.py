"""Configuration d'environnement partagée par les applications.

Tout ce qui dépend de la machine (fonds photo, bases, aperçus, TAXREF,
ExifTool) est décrit une seule fois dans `annotations/preferences.json`, dans
la section `environment` :

```json
{
  "environment": {
    "photos_root": "/Volumes/WD/PHOTOS",
    "data_dir": "/Volumes/WD/PHOTOS/_DATA",
    "photos_db": "/Volumes/WD/PHOTOS/_DATA/photos.db",
    "face_db": "/Volumes/WD/PHOTOS/_DATA/face.db",
    "previews_dir": "/Volumes/WD/PHOTOS/_DATA/previews",
    "thesaurus_dir": "/Volumes/WD/PHOTOS/_DATA/Thesaurus",
    "taxref_dir": "/Users/pc/DVPT/photos/faune/TAXREF",
    "exiftool": "",
    "geocoding": "nominatim"
  }
}
```

`geocoding` vaut `nominatim` (défaut) ou `off` : dans ce dernier cas, Géotag
n'envoie ni texte ni coordonnées au service de géocodage.

Les variables d'environnement (`PHOTOS_ROOT`, `PHOTOS_DATA_DIR`, `PHOTOS_DB`,
`PHOTOS_FACE_DB`, `PHOTOS_PREVIEWS_DIR`, `PHOTOS_TAXREF`, `EXIFTOOL`)
restent prioritaires : elles servent aux tests et aux lancements ponctuels.

Les chemins vides sont déduits des précédents (`data_dir` de `photos_root`,
`photos_db` de `data_dir`, etc.).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PREFERENCES_PATH = PROJECT_DIR / "annotations" / "preferences.json"

DEFAULT_PHOTOS_ROOT = Path("/Volumes/WD/PHOTOS")
DEFAULT_TAXREF_DIR = PROJECT_DIR / "faune" / "TAXREF"


def section() -> dict:
    """Section `environment` du fichier de préférences (ou dictionnaire vide)."""
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    environment = data.get("environment") if isinstance(data, dict) else None
    return environment if isinstance(environment, dict) else {}


def value(name: str, default=None):
    """Valeur configurée, ou `default` si absente ou vide."""
    configured = section().get(name)
    if configured is None or configured == "":
        return default
    return configured


def path(name: str, default: Path | str | None = None) -> Path | None:
    configured = value(name)
    if configured in (None, ""):
        configured = default
    if configured in (None, ""):
        return None
    return Path(str(configured)).expanduser()


def photos_root() -> Path:
    configured = os.environ.get("PHOTOS_ROOT") or value("photos_root") or DEFAULT_PHOTOS_ROOT
    return Path(str(configured)).expanduser()


def data_dir() -> Path:
    configured = os.environ.get("PHOTOS_DATA_DIR") or value("data_dir")
    if configured:
        return Path(str(configured)).expanduser()
    return photos_root() / "_DATA"


def photos_db() -> Path:
    configured = os.environ.get("PHOTOS_DB")
    if configured:
        return Path(configured).expanduser()
    if os.environ.get("PHOTOS_DATA_DIR"):
        return data_dir() / "photos.db"
    return path("photos_db", data_dir() / "photos.db")


def face_db() -> Path:
    configured = os.environ.get("PHOTOS_FACE_DB")
    if configured:
        return Path(configured).expanduser()
    if os.environ.get("PHOTOS_DATA_DIR"):
        return data_dir() / "face.db"
    return path("face_db", data_dir() / "face.db")


def previews_dir() -> Path:
    configured = os.environ.get("PHOTOS_PREVIEWS_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.environ.get("PHOTOS_DATA_DIR"):
        return data_dir() / "previews"
    return path("previews_dir", data_dir() / "previews")


def taxref_dir() -> Path:
    configured = os.environ.get("PHOTOS_TAXREF") or value("taxref_dir")
    if configured:
        return Path(str(configured)).expanduser()
    return DEFAULT_TAXREF_DIR


def exiftool_executable() -> str | None:
    """Chemin d'ExifTool : configuration, variable EXIFTOOL, puis PATH."""
    configured = os.environ.get("EXIFTOOL") or value("exiftool")
    if configured:
        candidate = Path(str(configured)).expanduser()
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(str(configured))
        if found:
            return found
    return shutil.which("exiftool")
