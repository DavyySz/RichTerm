#!/usr/bin/env bash
# RichTerm: Abhängigkeiten prüfen und bei Bedarf einrichten. Wird von start.sh automatisch
# aufgerufen; `richterm` startet also entweder sofort oder holt erst nach, was fehlt.
#
#   setup.sh --check              still prüfen, Exit 0 = alles da
#   setup.sh --auto [ORDNER]      fehlendes einrichten (fragt nach, braucht ggf. sudo), danach RichTerm starten
#   setup.sh --full               alles einrichten, ohne danach zu starten (= install.sh)
#   setup.sh --uninstall          Verknüpfungen entfernen
set -u
realpath_py() { python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1"; }   # macOS hat kein readlink -f
DIR="$(cd "$(dirname "$(realpath_py "${BASH_SOURCE[0]}")")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
CFG="$HOME/.config/richterm.json"
OLLAMA_BIN="$HOME/.local/share/ollama/dist/bin/ollama"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; }
ask()  { local a; read -r -p "  $1 [j/N] " a </dev/tty || a=n; [ "${a:-n}" = j ] || [ "${a:-n}" = J ]; }

# ---------------------------------------------------------------- Prüfungen
have_gi() { python3 -c "import gi; gi.require_version('$1','$2'); from gi.repository import $1" 2>/dev/null; }
missing_packages() {           # gibt die fehlenden Komponenten als Wörter aus
  command -v python3 >/dev/null || echo python
  python3 -c "import gi" 2>/dev/null || echo gi
  have_gi Gtk 3.0      || echo gtk
  have_gi Vte 2.91     || echo vte
  have_gi WebKit2 4.1  || echo webkit
}
links_ok() { [ -e "$BIN/richterm" ] && [ "$(realpath_py "$BIN/richterm")" = "$DIR/start.sh" ] && [ -e "$BIN/rt" ] && [ "$(realpath_py "$BIN/rt")" = "$DIR/bin/rt" ]; }

is_mac() { [ "$(uname -s)" = Darwin ]; }
check_quiet() {
  command -v python3 >/dev/null || return 1
  if ! is_mac; then
    # Linux: natives Fenster braucht GTK/VTE/WebKit; fehlt es, geht der Browser-Modus trotzdem
    [ -z "$(missing_packages)" ] || [ -n "${RICHTERM_WEB:-}" ] || return 1
  fi
  links_ok || return 1
  return 0
}

# ---------------------------------------------------------------- Pakete
pm_detect() {
  if command -v apt-get >/dev/null; then echo apt
  elif command -v dnf >/dev/null; then echo dnf
  elif command -v pacman >/dev/null; then echo pacman
  elif command -v zypper >/dev/null; then echo zypper
  else echo none; fi
}
pkg_names() {                  # $1 = Paketmanager, Rest = fehlende Komponenten
  local pm="$1"; shift
  for c in "$@"; do
    case "$pm:$c" in
      apt:python)    echo python3 ;;
      apt:gi)        echo python3-gi ;;
      apt:gtk)       echo gir1.2-gtk-3.0 ;;
      apt:vte)       echo gir1.2-vte-2.91 ;;
      apt:webkit)    echo gir1.2-webkit2-4.1 ;;
      apt:zstd)      echo zstd ;;
      dnf:python)    echo python3 ;;
      dnf:gi)        echo python3-gobject ;;
      dnf:gtk)       echo gtk3 ;;
      dnf:vte)       echo vte291 ;;
      dnf:webkit)    echo webkit2gtk4.1 ;;
      dnf:zstd)      echo zstd ;;
      pacman:python) echo python ;;
      pacman:gi)     echo python-gobject ;;
      pacman:gtk)    echo gtk3 ;;
      pacman:vte)    echo vte3 ;;
      pacman:webkit) echo webkit2gtk-4.1 ;;
      pacman:zstd)   echo zstd ;;
      zypper:python) echo python3 ;;
      zypper:gi)     echo python3-gobject ;;
      zypper:gtk)    echo typelib-1_0-Gtk-3_0 ;;
      zypper:vte)    echo typelib-1_0-Vte-2.91 ;;
      zypper:webkit) echo typelib-1_0-WebKit2-4_1 ;;
      zypper:zstd)   echo zstd ;;
    esac
  done
}
install_packages() {           # $@ = Komponenten
  local pm; pm="$(pm_detect)"
  local pkgs; pkgs="$(pkg_names "$pm" "$@" | tr '\n' ' ')"
  if [ "$pm" = none ] || [ -z "$pkgs" ]; then
    fail "Kein bekannter Paketmanager. Bitte von Hand installieren: Python-GObject, GTK 3, VTE 2.91, WebKitGTK 4.1"
    return 1
  fi
  echo "  Folgende Pakete werden installiert (Administrator-Passwort nötig): $pkgs"
  case "$pm" in
    apt)    sudo apt-get update -qq && sudo apt-get install -y $pkgs ;;
    dnf)    sudo dnf install -y $pkgs ;;
    pacman) sudo pacman -S --needed --noconfirm $pkgs ;;
    zypper) sudo zypper install -y $pkgs ;;
  esac
}

# ---------------------------------------------------------------- Verknüpfungen
make_links() {
  mkdir -p "$BIN" "$APPS"
  chmod +x "$DIR/start.sh" "$DIR/bin/rt" "$DIR/richterm.py" 2>/dev/null
  ln -sf "$DIR/start.sh" "$BIN/richterm"
  ln -sf "$DIR/bin/rt" "$BIN/rt"
  ok "Befehle richterm und rt in $BIN"
  case ":$PATH:" in *":$BIN:"*) ;; *)
    warn "$BIN ist nicht im PATH. Zeile in ~/.bashrc ergänzen:  export PATH=\"\$HOME/.local/bin:\$PATH\""
    grep -q 'local/bin' "$HOME/.bashrc" 2>/dev/null || { echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"; ok "in ~/.bashrc eingetragen (gilt für neue Terminals)"; } ;;
  esac
  if is_mac; then
    grep -q 'local/bin' "$HOME/.zshrc" 2>/dev/null || { echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"; ok "PATH in ~/.zshrc eingetragen (gilt für neue Terminals)"; }
    claude_md_hint
    return 0
  fi
  # Icon ins Icon-Theme des Nutzers, dann ist es überall (Menü, Taskleiste, Fenster) verfügbar
  for sz in 16 32 48 64 128 256 512; do
    mkdir -p "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps"
    cp "$DIR/icon/richterm-$sz.png" "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps/richterm.png" 2>/dev/null
  done
  mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
  cp "$DIR/icon/richterm.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/richterm.svg" 2>/dev/null
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null
  # Projektordner bekommt dasselbe Icon im Dateimanager
  command -v gio >/dev/null && gio set "$DIR" metadata::custom-icon "file://$DIR/icon/richterm-256.png" 2>/dev/null
  cat > "$APPS/richterm.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=RichTerm
Comment=Chat mit Claude Code: Formeln, Diagramme, Animationen – plus Terminal
Exec=$DIR/start.sh $HOME
Icon=richterm
Terminal=false
Categories=Development;Education;TerminalEmulator;
EOF
  [ -d "$HOME/Desktop" ] && cp "$APPS/richterm.desktop" "$HOME/Desktop/RichTerm.desktop" && chmod +x "$HOME/Desktop/RichTerm.desktop"
  ok "Menü- und Desktop-Eintrag"
  if command -v nemo >/dev/null; then
    mkdir -p "$HOME/.local/share/nemo/actions"
    printf '[Nemo Action]\nName=RichTerm hier öffnen\nComment=Claude-Chat mit diesem Ordner starten\nExec=<richterm %%F>\nIcon-Name=richterm\nSelection=none\nExtensions=dir;\nQuote=double\n' > "$HOME/.local/share/nemo/actions/richterm.nemo_action"
    printf '[Nemo Action]\nName=RichTerm in diesem Ordner öffnen\nComment=Claude-Chat mit dem gewählten Ordner starten\nExec=<richterm %%F>\nIcon-Name=richterm\nSelection=s\nExtensions=dir;\nQuote=double\n' > "$HOME/.local/share/nemo/actions/richterm-folder.nemo_action"
    ok "Rechtsklick-Eintrag im Dateimanager (Nemo)"
  fi
  claude_md_hint
}
claude_md_hint() {
  mkdir -p "$HOME/.claude"
  if ! grep -q "RichTerm" "$HOME/.claude/CLAUDE.md" 2>/dev/null; then
    cat >> "$HOME/.claude/CLAUDE.md" <<'EOF'

## RichTerm (Umgebungsvariable `RICHTERM=1`)
In einem RichTerm-Terminal-Tab läuft Claude Code im Terminal; daneben gibt es den Chat-Tab, der
Markdown, LaTeX, Mermaid, Bilder und HTML rendert. Formeln/Bilder/Diagramme aus dem Terminal dorthin
schicken: `rt latex '…'`, `rt md datei.md`, `rt image bild.png`, `rt mermaid '…'`, `rt html seite.html`.
Im RichTerm-Chat selbst (claude -p, Streaming) werden Antworten direkt gerendert: Formeln als LaTeX,
Diagramme als ```mermaid, Animationen als ```html, Plots als PNG + `![…](pfad)`.
EOF
    ok "Hinweis für Claude Code in ~/.claude/CLAUDE.md"
  fi
}

# ---------------------------------------------------------------- Claude Code & Ollama
setup_claude() {
  if command -v claude >/dev/null; then ok "Claude Code $(claude --version 2>/dev/null | head -1)"; return 0; fi
  warn "Claude Code (claude) ist nicht installiert. Es wird für den Chat mit Claude gebraucht."
  if ask "Claude Code jetzt installieren (offizielles Installationsskript von claude.ai)?"; then
    curl -fsSL https://claude.ai/install.sh | bash && hash -r
    export PATH="$HOME/.local/bin:$PATH"
    if command -v claude >/dev/null; then
      ok "Claude Code installiert"
      echo "  Jetzt einmal anmelden: Es öffnet sich Claude Code. Dort /login eingeben, Anweisungen folgen, danach mit /exit beenden."
      read -r -p "  Weiter mit Enter … " _ </dev/tty || true
      claude </dev/tty >/dev/tty 2>&1 || true
    else
      fail "Installation fehlgeschlagen. Siehe https://docs.anthropic.com/claude-code"
    fi
  else
    warn "übersprungen: ohne Claude Code funktioniert nur der Chat mit lokalen Modellen"
  fi
}
cfg_flag() { python3 - "$1" "${2:-}" <<'EOF'
import json, os, sys
p = os.path.expanduser('~/.config/richterm.json'); key, val = sys.argv[1], sys.argv[2]
try: c = json.load(open(p))
except Exception: c = {}
if val:
    c[key] = True; os.makedirs(os.path.dirname(p), exist_ok=True); json.dump(c, open(p, 'w'), indent=2)
else:
    sys.exit(0 if c.get(key) else 1)
EOF
}
setup_ollama() {
  if [ -x "$OLLAMA_BIN" ] || command -v ollama >/dev/null; then ok "Ollama (lokale Modelle) vorhanden"; return 0; fi
  cfg_flag ollama_asked && return 0            # schon einmal verneint: nicht wieder fragen
  if is_mac; then
    if ask "Lokale, kostenlose KI-Modelle möglich machen? Installiert Ollama (brew install ollama, sonst App von ollama.com)."; then
      if command -v brew >/dev/null; then brew install ollama && ok "Ollama installiert"; else
        warn "Kein Homebrew: bitte die Ollama-App von https://ollama.com laden und einmal starten"; fi
    else cfg_flag ollama_asked set; warn "übersprungen"; fi
    return 0
  fi
  if ask "Lokale, kostenlose KI-Modelle möglich machen? Lädt Ollama nach ~/.local/share/ollama (ca. 1,4 GB)."; then
    command -v zstd >/dev/null || install_packages zstd
    mkdir -p "$HOME/.local/share/ollama/dist"
    local url
    url="$(curl -fsSL https://api.github.com/repos/ollama/ollama/releases/latest | python3 -c "import json,sys; print([a['browser_download_url'] for a in json.load(sys.stdin)['assets'] if a['name']=='ollama-linux-amd64.tar.zst'][0])" 2>/dev/null)"
    if [ -n "$url" ] && curl -fL -o /tmp/ollama.tar.zst "$url" && tar --zstd -xf /tmp/ollama.tar.zst -C "$HOME/.local/share/ollama/dist"; then
      rm -f /tmp/ollama.tar.zst; ok "Ollama installiert. Modelle lädst du im RichTerm-Modellmenü."
    else
      warn "Download fehlgeschlagen; später erneut über ./install.sh"
    fi
  else
    cfg_flag ollama_asked set; warn "übersprungen (nachholbar mit ./install.sh)"
  fi
}

# ---------------------------------------------------------------- Ablauf
run_setup() {                  # $1 = "start" → danach RichTerm starten, $2 = Ordner
  echo "RichTerm einrichten ($DIR)"
  echo; echo "1. Systempakete"
  if is_mac; then
    command -v python3 >/dev/null && ok "python3 $(python3 --version 2>&1 | cut -d' ' -f2) (macOS: Browser-Modus, kein GTK nötig)" || {
      if command -v brew >/dev/null; then brew install python3; else
        fail "python3 fehlt. Homebrew installieren (https://brew.sh) und 'brew install python3', oder Python von python.org"; return 1; fi; }
  else
    local miss; miss="$(missing_packages | tr '\n' ' ')"
    if [ -n "$miss" ]; then
      warn "fehlt: $miss"
      if ! install_packages $miss; then
        warn "Systempakete nicht installierbar: RichTerm läuft dann im Browser-Modus (richterm --web)."
      fi
      miss="$(missing_packages | tr '\n' ' ')"
      [ -n "$miss" ] && warn "ohne $miss kein natives Fenster; Browser-Modus wird benutzt"
    fi
    [ -z "$(missing_packages)" ] && ok "Python, GTK 3, VTE, WebKitGTK"
  fi
  command -v pdftotext >/dev/null && ok "pdftotext (PDFs im rag/-Ordner)" || warn "pdftotext fehlt: PDFs in rag/ werden nicht gelesen. Linux: sudo apt install poppler-utils · macOS: brew install poppler"
  echo; echo "2. Befehle und Menüeinträge"; make_links
  echo; echo "3. Claude Code"; setup_claude
  echo; echo "4. Lokale KI (optional)"; setup_ollama
  echo; ok "Fertig."
  if [ "${1:-}" = start ]; then
    echo "  RichTerm startet …"; sleep 1
    exec "$DIR/start.sh" "${2:-$HOME}"
  else
    echo "  Starten:  cd <dein Ordner> && richterm     Anleitung: $DIR/docs/ANLEITUNG.pdf"
  fi
}

case "${1:-}" in
  --check)     check_quiet ;;
  --auto)      run_setup start "${2:-$HOME}" ;;
  --full|"")   run_setup ;;
  --uninstall)
    rm -f "$BIN/richterm" "$BIN/rt" "$APPS/richterm.desktop" "$HOME/Desktop/RichTerm.desktop" \
          "$HOME/.local/share/nemo/actions/richterm.nemo_action" "$HOME/.local/share/nemo/actions/richterm-folder.nemo_action"
    echo "Verknüpfungen entfernt. Der Ordner $DIR und ~/.local/share/ollama bleiben; bei Bedarf von Hand löschen." ;;
  *) echo "Aufruf: setup.sh [--check | --auto [ORDNER] | --full | --uninstall]"; exit 2 ;;
esac
