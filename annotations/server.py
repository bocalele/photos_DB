#!/usr/bin/env python3
"""Application globale d'annotation.

Une seule fenêtre, une barre en haut pour passer d'une app à l'autre :

    Géotag · Faune · Visages · Mots-clés

Chaque app reste autonome (son propre serveur, son propre environnement) :
ce programme les lance sur des ports locaux, avec **le même dossier de photos**,
et affiche leur interface dans un cadre. Il ne réimplémente rien : il orchestre.

Points importants :

- le dossier courant est unique et propagé aux quatre apps ;
- changer de dossier dans une app est détecté (via le `preferences.json`
  partagé) et répercuté sur les autres ;
- l'application globale ne dépend que de la bibliothèque standard.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
LOG_DIR = APP_DIR / "logs"
HOST = "127.0.0.1"
START_TIMEOUT = 120.0

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools import environment, preferences  # noqa: E402

# Ordre demandé : Géotag, Faune, Visages, Mots-clés.
APPS = [
    {
        "id": "geotag",
        "label": "Géotag",
        "icon": "pin",
        "directory": PROJECT_DIR / "geotag",
        "environment": PROJECT_DIR,
        "description": "Lieu, coordonnées GPS et pays des photos",
    },
    {
        "id": "faune",
        "label": "Faune",
        "icon": "paw",
        "directory": PROJECT_DIR / "faune",
        "environment": PROJECT_DIR / "faune",
        "description": "Espèce animale ou végétale (BioCLIP + TAXREF)",
    },
    {
        "id": "face",
        "label": "Visages",
        "icon": "user",
        "directory": PROJECT_DIR / "face",
        "environment": PROJECT_DIR / "face",
        "description": "Identification des personnes",
    },
    {
        "id": "motscles",
        "label": "Mots-clés",
        "icon": "tag",
        "directory": PROJECT_DIR / "motscles",
        "environment": PROJECT_DIR,
        "description": "Mots-clés, note et auteur",
    },
]


# --------------------------------------------------------------------------- #
# Petites fonctions
# --------------------------------------------------------------------------- #


def read_preferences() -> dict:
    return preferences.load()


def write_preferences(preferences_data: dict) -> None:
    try:
        preferences.save(preferences_data)
    except OSError as error:
        print(f"Préférences non enregistrées : {error}", flush=True)


def ensure_environment_defaults() -> None:
    """Complète la section `environment` sans écraser les valeurs choisies."""
    data = read_preferences()
    section = data.get("environment")
    if not isinstance(section, dict):
        section = {}
    defaults = {
        "photos_root": str(environment.photos_root()),
        "data_dir": str(environment.data_dir()),
        "photos_db": str(environment.photos_db()),
        "face_db": str(environment.face_db()),
        "previews_dir": str(environment.previews_dir()),
        "thesaurus_dir": str(environment.thesaurus_dir()),
        "taxref_dir": str(environment.taxref_dir()),
        "legacy_photo_roots": [str(path) for path in environment.legacy_photo_roots()],
        "geocoding": "nominatim",
        "exiftool": "",
    }
    changed = False
    for key, value in defaults.items():
        if key not in section:
            section[key] = value
            changed = True
    if changed:
        data["environment"] = section
        write_preferences(data)


def default_directory() -> Path:
    configured = preferences.value("initial_directory")
    if configured:
        candidate = Path(str(configured)).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    fallback = environment.photos_root()
    return fallback if fallback.is_dir() else Path.cwd()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind((HOST, 0))
        return int(candidate.getsockname()[1])


def choose_directory(start: Path) -> Path | None:
    script = """
on run argv
    set initialFolder to POSIX file (item 1 of argv) as alias
    set chosenFolder to choose folder with prompt "Choisir le dossier contenant les photos" default location initialFolder
    return POSIX path of chosenFolder
end run
"""
    print("Ouverture du sélecteur de dossier macOS…", flush=True)
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script, str(start)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        if "(-128)" in result.stderr:
            return None
        message = result.stderr.strip() or result.stdout.strip() or "Erreur AppleScript."
        raise RuntimeError(f"Impossible de choisir le dossier de photos : {message}")
    selected = result.stdout.strip()
    if not selected:
        raise RuntimeError("Le sélecteur macOS n'a renvoyé aucun dossier.")
    return Path(selected).expanduser().resolve()


def python_for(spec: dict) -> Path:
    candidate = spec["environment"] / ".pixi" / "envs" / "default" / "bin" / "python"
    return candidate if candidate.is_file() else Path(sys.executable)


def environment_for(spec: dict) -> dict:
    """Environnement de l'app : son Python et son exiftool d'abord sur le PATH."""
    env = os.environ.copy()
    bin_dir = python_for(spec).parent
    env["PATH"] = os.pathsep.join(filter(None, [str(bin_dir), env.get("PATH", "")]))
    modules = bin_dir.parent / "lib" / "perl5" / "site_perl"
    if modules.is_dir():
        env["PERL5LIB"] = os.pathsep.join(
            filter(None, [str(modules), env.get("PERL5LIB", "")])
        )
    return env


# --------------------------------------------------------------------------- #
# Base du sélecteur (photos.db)
# --------------------------------------------------------------------------- #


def sync_photos_database() -> dict:
    """Met à jour photos.db pour le dossier de travail courant.

    Un seul script fait le reset et les mises à jour :
    ``python -m dataBase.sync update <dossier> --json``. On utilise
    l'environnement racine (pandas + ExifTool), pas l'environnement Visages
    qui ne contient pas pandas.
    """
    spec = next(app for app in APPS if app["id"] == "geotag")
    runtime = globals().get("RUNTIME")
    scope = runtime.directory.resolve() if runtime is not None else environment.photos_root()
    command = [
        str(python_for(spec)),
        "-m",
        "dataBase.sync",
        "update",
        str(scope),
        "--json",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_DIR),
            env=environment_for(spec),
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "La mise à jour de photos.db a dépassé trente minutes."
        ) from error
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise RuntimeError(message or "La mise à jour de photos.db a échoué.")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError("Réponse inattendue de dataBase.sync.") from error


# --------------------------------------------------------------------------- #
# Cycle de vie des apps
# --------------------------------------------------------------------------- #


class AppProcess:
    def __init__(self, spec: dict):
        self.spec = spec
        self.id: str = spec["id"]
        self.port: int | None = None
        self.process: subprocess.Popen | None = None
        self.status = "stopped"  # stopped · starting · ready · error
        self.error: str | None = None
        self.started_at = 0.0
        self.directory_sent: str | None = None
        self.log_handle = None

    @property
    def url(self) -> str | None:
        return f"http://{HOST}:{self.port}/" if self.port else None

    def start(self, directory: Path) -> None:
        self.stop()
        self.port = free_port()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"{self.id}.log"
        try:
            self.log_handle = open(log_path, "ab")
            self.log_handle.write(
                f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} · {directory} ===\n".encode()
            )
            self.log_handle.flush()
        except OSError:
            self.log_handle = None

        python = python_for(self.spec)
        script = self.spec["directory"] / "server.py"
        command = [
            str(python),
            str(script),
            str(directory),
            "--host",
            HOST,
            "--port",
            str(self.port),
        ]
        try:
            output = self.log_handle if self.log_handle is not None else subprocess.DEVNULL
            self.process = subprocess.Popen(
                command,
                cwd=str(PROJECT_DIR),
                env=environment_for(self.spec),
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.status = "starting"
            self.error = None
            self.started_at = time.time()
            self.directory_sent = str(directory)
            print(f"[{self.id}] démarrage sur le port {self.port}", flush=True)
        except OSError as error:
            self.status = "error"
            self.error = str(error)
            print(f"[{self.id}] impossible de démarrer : {error}", flush=True)

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                try:
                    process.terminate()
                except OSError:
                    pass
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
        if self.log_handle is not None:
            try:
                self.log_handle.close()
            except OSError:
                pass
            self.log_handle = None

    def check(self) -> None:
        if self.status == "starting":
            if self.port is None:
                return
            try:
                with socket.create_connection((HOST, self.port), timeout=0.3):
                    self.status = "ready"
                    print(f"[{self.id}] prêt", flush=True)
                    return
            except OSError:
                pass
            if time.time() - self.started_at > START_TIMEOUT:
                self.status = "error"
                self.error = "Le serveur n'a pas répondu dans le délai imparti."
                self.stop()
            return

        if self.status == "ready" and self.process is not None:
            if self.process.poll() is not None:
                self.status = "stopped"
                self.error = "Le serveur s'est arrêté."
                self.stop()


# --------------------------------------------------------------------------- #
# Orchestrateur
# --------------------------------------------------------------------------- #


class Runtime:
    def __init__(self, directory: Path):
        self.lock = threading.RLock()
        self.directory = directory
        self.apps = {spec["id"]: AppProcess(spec) for spec in APPS}
        self.active = preferences.active_app()
        if self.active not in self.apps:
            self.active = "geotag"
        self.stop_event = threading.Event()
        self.preferences_lock = threading.Lock()
        # Dernier dossier vu dans le preferences.json partagé.
        self.seen_directory: str | None = str(directory)

    # --- état -------------------------------------------------------------

    def state(self) -> dict:
        with self.lock:
            return {
                "directory": str(self.directory),
                "active": self.active,
                "apps": [
                    {
                        "id": app.id,
                        "label": app.spec["label"],
                        "icon": app.spec["icon"],
                        "description": app.spec["description"],
                        "status": app.status,
                        "url": app.url,
                        "error": app.error,
                    }
                    for app in self.apps.values()
                ],
            }

    def set_active(self, app_id: str) -> None:
        with self.lock:
            if app_id not in self.apps:
                raise ValueError("Application inconnue.")
            self.active = app_id
            preferences.update(active_app=app_id)

    # --- démarrage --------------------------------------------------------

    def start_all(self) -> None:
        with self.lock:
            directory = self.directory
        for app in self.apps.values():
            threading.Thread(
                target=app.start, args=(directory,), daemon=True
            ).start()

    def start_app(self, app_id: str) -> None:
        app = self.apps.get(app_id)
        if app is None:
            raise ValueError("Application inconnue.")
        with self.lock:
            directory = self.directory
        app.start(directory)

    def restart_app(self, app_id: str) -> None:
        self.start_app(app_id)

    # --- dossier ----------------------------------------------------------

    def set_directory(self, directory: Path, skip: str | None = None) -> None:
        directory = directory.expanduser().resolve()
        if not directory.is_dir():
            raise ValueError(f"Dossier introuvable : {directory}")
        with self.lock:
            changed = directory != self.directory
            self.directory = directory
            self.seen_directory = str(directory)
            preferences.set_initial_directory(directory)
        if changed:
            print(f"Dossier : {directory}", flush=True)
        self.propagate(directory, skip=skip)

    def propagate(self, directory: Path, skip: str | None = None) -> None:
        for app in list(self.apps.values()):
            if app.id == skip:
                continue
            self._propagate_one(app, directory)

    # --- surveillance -----------------------------------------------------

    def monitor(self) -> None:
        """Suit l'état des serveurs et les changements de dossier des apps."""
        while not self.stop_event.wait(1.0):
            for app in self.apps.values():
                try:
                    app.check()
                    # Une app démarrée pendant un changement de dossier doit rattraper.
                    if (
                        app.status == "ready"
                        and app.directory_sent != str(self.directory)
                    ):
                        self._propagate_one(app, self.directory)
                except Exception as error:  # noqa: BLE001
                    print(f"[{app.id}] contrôle : {error}", flush=True)

            try:
                # Une app qui change de dossier écrit le preferences.json partagé.
                # On compare la valeur à celle connue pour ne pas boucler sur nos
                # propres écritures.
                with self.preferences_lock:
                    value = preferences.value("initial_directory")
                    if not isinstance(value, str) or not value.strip():
                        continue
                    if value == self.seen_directory:
                        continue
                    candidate = Path(value).expanduser()
                    if not candidate.is_dir():
                        continue
                    candidate = candidate.resolve()
                    if candidate == self.directory:
                        self.seen_directory = value
                        continue
                    # L'utilisateur a changé de dossier dans une app : on aligne les autres.
                    self.set_directory(candidate)
            except Exception as error:  # noqa: BLE001
                print(f"[surveillance] {error}", flush=True)

    def _propagate_one(self, app: AppProcess, directory: Path) -> None:
        if app.status != "ready" or not app.port:
            return
        payload = json.dumps({"directory": str(directory)}).encode("utf-8")
        request = urllib.request.Request(
            f"http://{HOST}:{app.port}/api/directory",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                response.read()
            app.directory_sent = str(directory)
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            print(f"[{app.id}] dossier non propagé : {error}", flush=True)

    # --- arrêt ------------------------------------------------------------

    def shutdown(self) -> None:
        with self.lock:
            if self.stop_event.is_set():
                return
            self.stop_event.set()
        for app in self.apps.values():
            app.stop()
        print("Applications arrêtées.", flush=True)

    def log_tail(self, app_id: str, lines: int = 60) -> str:
        if app_id not in self.apps:
            raise ValueError("Application inconnue.")
        path = LOG_DIR / f"{app_id}.log"
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(content[-max(1, min(lines, 400)):])


RUNTIME: Runtime | None = None


# --------------------------------------------------------------------------- #
# Serveur HTTP de la coquille
# --------------------------------------------------------------------------- #


class ShellHandler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}", flush=True)

    def send_body(self, body: bytes, content_type: str, status: int = 200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def send_json(self, payload, status: int = 200):
        self.send_body(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 100_000:
            raise ValueError("La requête est vide ou trop volumineuse.")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("Contenu de requête invalide.")
        return data

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self.send_static("index.html", "text/html; charset=utf-8")
            elif parsed.path == "/app.js":
                self.send_static("app.js", "application/javascript; charset=utf-8")
            elif parsed.path == "/styles.css":
                self.send_static("styles.css", "text/css; charset=utf-8")
            elif parsed.path == "/api/state":
                self.send_json(RUNTIME.state())
            elif parsed.path == "/api/logs":
                app_id = query.get("app", [""])[0]
                lines = int(query.get("lines", ["60"])[0])
                self.send_json({"log": RUNTIME.log_tail(app_id, lines)})
            else:
                self.send_error(404)
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
        except Exception as error:  # noqa: BLE001
            print(f"Erreur : {error}", flush=True)
            self.send_json({"error": str(error)}, 500)

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/directory":
                payload = self.read_json()
                RUNTIME.set_directory(Path(str(payload.get("directory") or "")))
                self.send_json(RUNTIME.state())
            elif route == "/api/choose-directory":
                selected = choose_directory(RUNTIME.directory)
                if selected is None:
                    self.send_json({"error": "Aucun dossier sélectionné."}, 400)
                    return
                RUNTIME.set_directory(selected)
                self.send_json(RUNTIME.state())
            elif route == "/api/active":
                payload = self.read_json()
                RUNTIME.set_active(str(payload.get("app") or ""))
                self.send_json(RUNTIME.state())
            elif route == "/api/sync-database":
                self.send_json(sync_photos_database())
            elif route == "/api/restart":
                payload = self.read_json()
                RUNTIME.restart_app(str(payload.get("app") or ""))
                self.send_json(RUNTIME.state(), 202)
            else:
                self.send_error(404)
        except (ValueError, FileNotFoundError) as error:
            self.send_json({"error": str(error)}, 400)
        except Exception as error:  # noqa: BLE001
            print(f"Erreur : {error}", flush=True)
            self.send_json({"error": str(error)}, 500)

    def send_static(self, filename: str, content_type: str):
        path = APP_DIR / filename
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_body(body, content_type)


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Application globale d'annotation (Géotag, Faune, Visages, Mots-clés)."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help="Dossier de photos (par défaut : le dernier utilisé).",
    )
    parser.add_argument("--host", default=HOST, help="Adresse d'écoute (défaut : 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8099, help="Port HTTP (défaut : 8099).")
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Ne pas démarrer les apps au lancement (elles démarreront à la demande).",
    )
    args = parser.parse_args()

    directory = (
        Path(args.directory).expanduser().resolve()
        if args.directory
        else default_directory()
    )
    if not directory.is_dir():
        parser.error(f"Le dossier de photos n'existe pas : {directory}")

    global RUNTIME
    RUNTIME = Runtime(directory)
    ensure_environment_defaults()
    preferences.set_initial_directory(directory)

    atexit.register(RUNTIME.shutdown)

    def handle_signal(signum, frame):  # noqa: ARG001
        RUNTIME.shutdown()
        raise SystemExit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)

    if not args.no_start:
        RUNTIME.start_all()
    threading.Thread(target=RUNTIME.monitor, daemon=True).start()

    try:
        server = ThreadingHTTPServer((args.host, args.port), ShellHandler)
    except OSError as error:
        parser.error(f"Impossible de démarrer le serveur : {error}")

    print(f"Dossier de photos : {directory}", flush=True)
    print(f"Interface : http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.", flush=True)
    finally:
        server.server_close()
        RUNTIME.shutdown()


if __name__ == "__main__":
    main()
