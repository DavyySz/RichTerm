#!/usr/bin/env bash
# RichTerm einrichten (Linux). Legt die Befehle `richterm` und `rt`, einen Menüeintrag und
# (bei Nemo) einen Rechtsklick-Eintrag an. Lädt auf Wunsch ein lokales Ollama nach ~/.local.
#
#   git clone <repo> RichTerm && cd RichTerm && ./install.sh
#
# Keine Root-Rechte nötig. Rückgängig: ./install.sh --uninstall
set -euo pipefail
DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; }

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$BIN/richterm" "$BIN/rt" "$APPS/richterm.desktop" "$HOME/Desktop/RichTerm.desktop" \
        "$HOME/.local/share/nemo/actions/richterm.nemo_action" "$HOME/.local/share/nemo/actions/richterm-folder.nemo_action"
  echo "Verknüpfungen entfernt. Der Ordner $DIR und ~/.local/share/ollama bleiben; bei Bedarf von Hand löschen."
  exit 0
fi

echo "RichTerm einrichten in: $DIR"
echo
echo "1. Voraussetzungen prüfen"
missing=0
if [ "$(uname -s)" != "Linux" ]; then
  fail "RichTerm läuft nur unter Linux (GTK, VTE und WebKitGTK gibt es für macOS/Windows nicht als nutzbare Pakete)."
  exit 1
fi
command -v python3 >/dev/null && ok "python3 $(python3 --version 2>&1 | cut -d' ' -f2)" || { fail "python3 fehlt"; missing=1; }
check_gi() {  # $1 = Namespace, $2 = Version, $3 = Paketname (Debian/Ubuntu/Mint)
  if python3 -c "import gi; gi.require_version('$1','$2'); from gi.repository import $1" 2>/dev/null; then
    ok "$1 $2"
  else
    fail "$1 $2 fehlt  →  sudo apt install $3"; missing=1
  fi
}
check_gi Gtk 3.0 gir1.2-gtk-3.0
check_gi Vte 2.91 gir1.2-vte-2.91
check_gi WebKit2 4.1 gir1.2-webkit2-4.1
python3 -c "import gi" 2>/dev/null || { fail "python3-gi fehlt  →  sudo apt install python3-gi"; missing=1; }
if command -v claude >/dev/null; then ok "Claude Code $(claude --version 2>/dev/null | head -1)"; else
  warn "Claude Code (claude) nicht gefunden. Ohne es funktioniert nur der Chat mit lokalen Modellen."
  warn "Installation: https://docs.anthropic.com/claude-code  (danach einmal 'claude' starten und anmelden)"
fi
if [ "$missing" = 1 ]; then
  echo
  echo "Bitte die fehlenden Pakete installieren (Fedora: python3-gobject gtk3 vte291 webkit2gtk4.1; Arch: python-gobject gtk3 vte3 webkit2gtk-4.1) und das Skript erneut ausführen."
  exit 1
fi

echo
echo "2. Befehle und Menüeinträge"
mkdir -p "$BIN" "$APPS"
chmod +x "$DIR/start.sh" "$DIR/bin/rt" "$DIR/richterm.py"
ln -sf "$DIR/start.sh" "$BIN/richterm" && ok "$BIN/richterm"
ln -sf "$DIR/bin/rt" "$BIN/rt" && ok "$BIN/rt"
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN ist nicht im PATH. In ~/.bashrc ergänzen:  export PATH=\"\$HOME/.local/bin:\$PATH\"";; esac
cat > "$APPS/richterm.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=RichTerm
Comment=Chat mit Claude Code: Formeln, Diagramme, Animationen – plus Terminal
Exec=$DIR/start.sh $HOME
Icon=utilities-terminal
Terminal=false
Categories=Development;Education;TerminalEmulator;
EOF
ok "Menüeintrag ($APPS/richterm.desktop)"
if [ -d "$HOME/Desktop" ]; then
  cp "$APPS/richterm.desktop" "$HOME/Desktop/RichTerm.desktop" && chmod +x "$HOME/Desktop/RichTerm.desktop" && ok "Desktop-Icon"
fi
if command -v nemo >/dev/null; then
  mkdir -p "$HOME/.local/share/nemo/actions"
  cat > "$HOME/.local/share/nemo/actions/richterm.nemo_action" <<'EOF'
[Nemo Action]
Name=RichTerm hier öffnen
Comment=Claude-Chat mit diesem Ordner als Arbeitsordner starten
Exec=<richterm %F>
Icon-Name=utilities-terminal
Selection=none
Extensions=dir;
Quote=double
EOF
  cat > "$HOME/.local/share/nemo/actions/richterm-folder.nemo_action" <<'EOF'
[Nemo Action]
Name=RichTerm in diesem Ordner öffnen
Comment=Claude-Chat mit dem gewählten Ordner als Arbeitsordner starten
Exec=<richterm %F>
Icon-Name=utilities-terminal
Selection=s
Extensions=dir;
Quote=double
EOF
  ok "Rechtsklick-Eintrag im Dateimanager (Nemo)"
fi

echo
echo "3. Hinweis für Claude Code (global, ~/.claude/CLAUDE.md)"
mkdir -p "$HOME/.claude"
if grep -q "RichTerm" "$HOME/.claude/CLAUDE.md" 2>/dev/null; then
  ok "bereits vorhanden"
else
  cat >> "$HOME/.claude/CLAUDE.md" <<'EOF'

## RichTerm (Umgebungsvariable `RICHTERM=1`)
In einem RichTerm-Terminal-Tab läuft Claude Code im Terminal; daneben gibt es den Chat-Tab, der
Markdown, LaTeX, Mermaid, Bilder und HTML rendert. Formeln/Bilder/Diagramme aus dem Terminal dorthin
schicken: `rt latex '…'`, `rt md datei.md`, `rt image bild.png`, `rt mermaid '…'`, `rt html seite.html`.
Im RichTerm-Chat selbst (claude -p, Streaming) werden Antworten direkt gerendert: Formeln als LaTeX,
Diagramme als ```mermaid, Animationen als ```html, Plots als PNG + `![…](pfad)`.
EOF
  ok "ergänzt"
fi

echo
echo "4. Lokale KI (optional)"
if [ -x "$HOME/.local/share/ollama/dist/bin/ollama" ] || command -v ollama >/dev/null; then
  ok "Ollama vorhanden"
else
  read -r -p "  Ollama für lokale Modelle nach ~/.local/share/ollama laden (ca. 1,4 GB)? [j/N] " a || true
  if [ "${a:-n}" = j ] || [ "${a:-n}" = J ]; then
    mkdir -p "$HOME/.local/share/ollama/dist"
    url="$(curl -fsSL https://api.github.com/repos/ollama/ollama/releases/latest | python3 -c "import json,sys; print([a['browser_download_url'] for a in json.load(sys.stdin)['assets'] if a['name']=='ollama-linux-amd64.tar.zst'][0])")"
    curl -fL -o /tmp/ollama.tar.zst "$url" && tar --zstd -xf /tmp/ollama.tar.zst -C "$HOME/.local/share/ollama/dist" && rm -f /tmp/ollama.tar.zst \
      && ok "Ollama installiert (Modelle lädst du im RichTerm-Modellmenü)" || warn "Download fehlgeschlagen; später im RichTerm-Modellmenü oder von ollama.com nachholen"
  else
    warn "übersprungen (jederzeit nachholbar: ./install.sh erneut ausführen)"
  fi
fi

echo
echo "Fertig. Starten:  cd <dein Ordner> && richterm     (oder über das Menü / Desktop-Icon)"
echo "Anleitung: $DIR/docs/ANLEITUNG.pdf"
