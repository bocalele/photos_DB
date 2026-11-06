#!/bin/bash

# Lanceur commun aux bundles macOS. Le serveur est détaché du processus de
# l'application : le Dock n'attend donc pas la fin du serveur et l'icône ne
# clignote plus pendant le démarrage. Si un serveur tourne déjà, on se contente
# de rouvrir son URL.

set -u

LAUNCHER="${1:-}"
LOG_DIR="${2:-}"

if [[ -z "$LAUNCHER" || ! -f "$LAUNCHER" || -z "$LOG_DIR" ]]; then
  /usr/bin/osascript -e 'display alert "Lanceur introuvable" message "Le fichier de lancement de l’application est absent."'
  exit 1
fi

mkdir -p "$LOG_DIR" || exit 1

URL_FILE="$LOG_DIR/server.url"
PID_FILE="$LOG_DIR/server.pid"

running_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s\n' "$pid"
}

if running_pid >/dev/null; then
  # Un serveur tourne déjà : rouvrir son interface s'il est prêt, sinon laisser
  # le lancement en cours terminer son ouverture.
  url="$(cat "$URL_FILE" 2>/dev/null || true)"
  if [[ -n "$url" ]] && /usr/bin/curl --silent --fail --max-time 1 "$url/" >/dev/null; then
    /usr/bin/open "$url"
  fi
  exit 0
fi

# Nettoyer les traces d'une session arrêtée.
rm -f "$URL_FILE" "$PID_FILE"

# nohup + redirections : le shell de l'application peut se terminer tout de
# suite, le serveur continue sa vie et écrit ensuite son PID et son URL.
nohup /bin/bash "$LAUNCHER" >>"$LOG_DIR/launcher.log" 2>&1 </dev/null &
exit 0
