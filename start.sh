#!/usr/bin/env bash
# RichTerm starten.
#   richterm            → im aktuellen Ordner
#   richterm ORDNER     → in ORDNER
# Prüft beim Start, ob alles Nötige da ist; fehlt etwas, wird es eingerichtet (fragt nach, ggf. mit sudo).
# Läuft losgelöst vom aufrufenden Terminal, das Terminal bleibt frei und darf geschlossen werden.
DIR="$(cd "$(dirname "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")")" && pwd)"
TARGET="$PWD"
for a in "$@"; do case "$a" in --*) ;; *) TARGET="$a" ;; esac; done
if [ ! -d "$TARGET" ]; then
  echo "richterm: Ordner nicht gefunden: $TARGET" >&2
  exit 1
fi
TARGET="$(cd "$TARGET" && pwd)"

WEB=""
case " $* " in *" --web "*) WEB=1 ;; esac
[ "$(uname -s)" = Darwin ] && WEB=1                      # macOS: immer Browser-Modus
if ! RICHTERM_WEB="$WEB" "$DIR/setup.sh" --check; then
  if [ -t 0 ] && [ -t 1 ]; then
    exec "$DIR/setup.sh" --auto "$TARGET"               # im Terminal: direkt hier einrichten, dann starten
  fi
  # ohne Terminal (Menü/Desktop-Icon): ein Terminalfenster für die Einrichtung öffnen
  for t in x-terminal-emulator gnome-terminal konsole xfce4-terminal mate-terminal xterm; do
    if command -v "$t" >/dev/null; then
      case "$t" in
        gnome-terminal|mate-terminal) exec "$t" -- "$DIR/setup.sh" --auto "$TARGET" ;;
        *)                            exec "$t" -e "$DIR/setup.sh --auto '$TARGET'" ;;
      esac
    fi
  done
  echo "richterm: Einrichtung nötig, aber kein Terminal gefunden. Bitte im Terminal ausführen: $DIR/setup.sh" >&2
  exit 1
fi

export PATH="$DIR/bin:$HOME/.local/share/ollama/dist/bin:$PATH"
# eigenes Ollama (ohne sudo) auf eigenem Port, Modelle unter ~/.local/share/ollama/models
if [ -x "$HOME/.local/share/ollama/dist/bin/ollama" ]; then export OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MODELS="$HOME/.local/share/ollama/models"; fi
if [ -n "$WEB" ] || ! python3 -c "import gi; gi.require_version('Gtk','3.0'); gi.require_version('Vte','2.91'); gi.require_version('WebKit2','4.1'); from gi.repository import Gtk, Vte, WebKit2" 2>/dev/null; then
  # Browser-Modus: Server im Hintergrund, Chat öffnet sich im Browser (App-Fenster)
  if command -v setsid >/dev/null; then
    setsid nohup python3 "$DIR/richterm.py" --web "$TARGET" >/dev/null 2>&1 < /dev/null &
  else
    nohup python3 "$DIR/richterm.py" --web "$TARGET" >/dev/null 2>&1 < /dev/null &
  fi
  exit 0
fi
setsid nohup python3 "$DIR/richterm.py" "$TARGET" >/dev/null 2>&1 < /dev/null &
