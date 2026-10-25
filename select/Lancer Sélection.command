#!/bin/bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PIXI="${HOME}/.pixi/bin/pixi"
server_pid=""

if [[ ! -x "$PIXI" ]]; then
  PIXI="$(command -v pixi || true)"
fi

if [[ -z "$PIXI" || ! -x "$PIXI" ]]; then
  echo "Pixi est introuvable. Installez Pixi ou ajoutez-le au PATH."
  read -r -p "Appuyez sur Entrée pour fermer."
  exit 1
fi

cd "$PROJECT_DIR" || exit 1

PORT="$("$PIXI" run python -c '
import socket

for port in range(8000, 65536):
    with socket.socket() as candidate:
        try:
            candidate.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        break
else:
    raise SystemExit("Aucun port disponible à partir de 8000.")
')"
if [[ $? -ne 0 || -z "$PORT" ]]; then
  echo "Impossible de trouver un port libre à partir de 8000."
  read -r -p "Appuyez sur Entrée pour fermer."
  exit 1
fi
URL="http://127.0.0.1:${PORT}"

cleanup() {
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

"$PIXI" run python select/server.py --host 127.0.0.1 --port "$PORT" &
server_pid=$!

while kill -0 "$server_pid" 2>/dev/null; do
  if curl --silent --fail --max-time 1 "$URL/" >/dev/null; then
    open "$URL"
    wait "$server_pid"
    status=$?
    server_pid=""
    exit "$status"
  fi
  sleep 0.5
done

wait "$server_pid"
status=$?
server_pid=""
exit "$status"
