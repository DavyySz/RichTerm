#!/usr/bin/env bash
# RichTerm starten.
#   richterm            → im aktuellen Ordner
#   richterm ORDNER     → in ORDNER
# Läuft losgelöst vom aufrufenden Terminal, das Terminal bleibt frei und darf geschlossen werden.
DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
export PATH="$DIR/bin:$HOME/.local/share/ollama/dist/bin:$PATH"
# eigenes Ollama (ohne sudo) auf eigenem Port, Modelle unter ~/.local/share/ollama/models
if [ -x "$HOME/.local/share/ollama/dist/bin/ollama" ]; then export OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MODELS="$HOME/.local/share/ollama/models"; fi
TARGET="${1:-$PWD}"
if [ ! -d "$TARGET" ]; then
  echo "richterm: Ordner nicht gefunden: $TARGET" >&2
  exit 1
fi
setsid nohup python3 "$DIR/richterm.py" "$(cd "$TARGET" && pwd)" >/dev/null 2>&1 < /dev/null &
