"""Préférences communes aux applications d'annotation.

Un seul fichier, `preferences.json` à la racine du projet, est partagé par
Annotations, Géotag, Faune, Visages, Mots-clés et l'application de sélection.
Il contient :

- `initial_directory` : le dossier de photos courant ;
- `active_app` : dernier onglet utilisé dans Annotations ;
- `default_active_app` : onglet ouvert au lancement d'Annotations ;
- `default_select_app` : outil ouvert au lancement de l'application Sélection ;
- `metadata_revision` : compteur incrémenté à chaque écriture de métadonnées ;
- `environment` : les chemins propres à la machine (voir `tools/environment.py`) ;
- `apps` : les réglages propres à une application, par exemple
  `{"motscles": {"default_author": "Pierre", "default_rating": 3,
  "default_group": 1}}`.

Les écritures sont protégées par un verrou (`preferences.json.lock`) et
remplacées atomiquement : plusieurs serveurs peuvent tourner en même temps
sans écraser mutuellement leurs réglages.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PREFERENCES_PATH = PROJECT_DIR / "preferences.json"


def path() -> Path:
    """Chemin du fichier de préférences (surchargeable pour les tests)."""
    configured = os.environ.get("PHOTOS_PREFERENCES")
    return Path(configured).expanduser() if configured else PREFERENCES_PATH


def _read() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


@contextmanager
def _locked():
    """Verrou inter-processus : les applications écrivent le même fichier."""
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f"{target.name}.lock")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load() -> dict:
    """Contenu actuel (dictionnaire vide si le fichier est absent ou invalide)."""
    return _read()


def save(data: dict) -> None:
    """Remplace le contenu du fichier."""
    if not isinstance(data, dict):
        raise TypeError("Les préférences doivent être un objet JSON.")
    with _locked():
        _write(data)


def update(changes: dict | None = None, **values) -> dict:
    """Fusionne des clés au premier niveau et renvoie le résultat."""
    with _locked():
        data = _read()
        if changes:
            data.update(changes)
        data.update(values)
        _write(data)
        return data


def value(name: str, default=None):
    configured = load().get(name)
    return default if configured is None or configured == "" else configured


def initial_directory(default: Path | str | None = None) -> Path | None:
    """Dossier de photos courant, s'il existe encore."""
    configured = value("initial_directory")
    if isinstance(configured, str) and configured.strip():
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    if default is None:
        return None
    fallback = Path(default).expanduser()
    return fallback


def set_initial_directory(directory: Path | str) -> dict:
    resolved = Path(directory).expanduser().resolve()
    return update(initial_directory=str(resolved))


def active_app(default: str = "geotag") -> str:
    configured = value("active_app")
    return configured if isinstance(configured, str) and configured else default


def default_active_app(default: str = "geotag") -> str:
    """Onglet ouvert au lancement, indépendant du dernier onglet utilisé."""
    configured = value("default_active_app")
    if isinstance(configured, str) and configured:
        return configured
    # Compatibilité avec les préférences créées avant l'ajout du défaut.
    return active_app(default)


def default_select_app(default: str = "faune") -> str:
    """Outil ouvert au lancement de l'application Sélection."""
    configured = value("default_select_app")
    if isinstance(configured, str) and configured:
        return configured
    # Sans réglage propre, on reprend le défaut d'Annotations.
    return default_active_app(default)


def metadata_revision() -> int:
    """Compteur global des écritures de métadonnées dans les fichiers."""
    configured = value("metadata_revision", 0)
    try:
        return max(0, int(configured))
    except (TypeError, ValueError):
        return 0


def touch_metadata_revision() -> int:
    """Incrémente le compteur après une écriture XMP/EXIF réussie."""
    with _locked():
        data = _read()
        try:
            revision = max(0, int(data.get("metadata_revision", 0))) + 1
        except (TypeError, ValueError):
            revision = 1
        data["metadata_revision"] = revision
        _write(data)
        return revision


def app_settings(app_id: str) -> dict:
    apps = load().get("apps")
    if not isinstance(apps, dict):
        return {}
    section = apps.get(app_id)
    return section if isinstance(section, dict) else {}


def update_app(app_id: str, changes: dict | None = None, **values) -> dict:
    """Met à jour la section `apps.<app_id>` sans toucher au reste."""
    with _locked():
        data = _read()
        apps = data.get("apps")
        if not isinstance(apps, dict):
            apps = {}
            data["apps"] = apps
        section = apps.get(app_id)
        if not isinstance(section, dict):
            section = {}
            apps[app_id] = section
        if changes:
            section.update(changes)
        section.update(values)
        _write(data)
        return section
