# RichTerm

Chat mit Claude Code wie im Browser, aber mit Zugriff auf deine lokalen Ordner, und ein Terminal
im selben Fenster. Die Antworten werden direkt im Chat gerendert:

- **LaTeX-Formeln** in Buchqualität (KaTeX), inline `$…$` und abgesetzt `$$…$$`
- **Markdown** mit Überschriften, Tabellen, Codeblöcken
- **Mermaid-Diagramme** und **SVG**
- **HTML-Blöcke als Live-Vorschau**: Animationen, Canvas, interaktive Simulationen laufen direkt in der Antwort
- **Bilder**, die Claude erzeugt (z. B. matplotlib-Plots)

Dazu Tabs mit einem vollwertigen Terminal (VTE, dieselbe Engine wie GNOME Terminal).

Keine Installation nötig: Python 3 plus die auf Linux Mint vorhandenen GTK-, VTE- und WebKit-Bindungen.
Die JavaScript-Bibliotheken (KaTeX, marked, mermaid) liegen in `vendor/`, alles läuft offline.
Claude Code (`claude`) muss installiert sein.

## Starten

Doppelklick auf **RichTerm** auf dem Desktop, über das Anwendungsmenü, oder:
```bash
~/Desktop/RichTerm/start.sh
```

## Chat

1. Oben links mit 📁 den **Arbeitsordner** wählen. Claude sieht und bearbeitet Dateien in diesem Ordner.
2. Frage eintippen, **Enter** sendet, **Shift+Enter** macht einen Zeilenumbruch.
3. Will Claude ein Werkzeug benutzen (Datei schreiben, Befehl ausführen), erscheint eine Frage im Chat
   mit **Erlauben / Ablehnen**. Mit dem Menü „Berechtigungen“ lässt sich das vorab einstellen.
4. **■** bricht eine laufende Antwort ab. **Neuer Chat** beginnt eine frische Sitzung.

Werkzeugaufrufe (gelesene Dateien, Befehle) erscheinen als graue Zeilen; ein Klick zeigt Details und Ergebnis.
Bei HTML-Vorschauen: **Code** zeigt den Quelltext, **Im Browser** öffnet die Seite groß.

Kopfzeile: Modell (Standard/Opus/Sonnet/Haiku), Berechtigungen, ☀/☾ hell/dunkel.
Ctrl + Plus/Minus ändert die Schriftgröße im Chat.

## Terminal

| Taste | Wirkung |
|---|---|
| Ctrl+Shift+T | neues Terminal (im Arbeitsordner) |
| Ctrl+Shift+W | Terminal schließen |
| Ctrl+PgUp / Ctrl+PgDn | Tab wechseln |
| Ctrl+Shift+Enter | zum Chat springen |
| Ctrl+Shift+C / Ctrl+Shift+V | kopieren / einfügen (Rechtsklick fügt auch ein) |
| Ctrl+Shift++ / Ctrl+Shift+- | Terminal-Schrift größer / kleiner |
| Ctrl+Klick auf Link | Link im Browser öffnen |

## `rt`: aus dem Terminal in den Chat

```bash
rt ask 'Erkläre mir Backpropagation'   # Frage an den Chat schicken
rt latex '\frac{1}{1+e^{-z}}'          # Formel als Karte im Chat
rt md notizen.md                       # Markdown-Datei anzeigen
rt image plot.png                      # Bild anzeigen
rt html seite.html                     # HTML-Seite einbetten
rt mermaid 'graph LR; A-->B'           # Diagramm
rt --help                              # alle Möglichkeiten
```

Funktioniert in den RichTerm-Terminals und in jedem anderen Terminal, solange RichTerm läuft.

## Aufbau

```
RichTerm/
├── richterm.py     App: Fenster, Claude-Code-Sitzung (Streaming), HTTP-Server, Terminal-Tabs
├── chat/           Chat-Oberfläche (index.html, chat.css, chat.js)
├── bin/rt          Befehl zum Senden aus dem Terminal
├── vendor/         KaTeX, marked, mermaid (offline)
└── start.sh        Starter
```

Technik: Die App startet `claude -p` im Streaming-Modus (`--input-format/--output-format stream-json`)
und hält den Prozess über das ganze Gespräch offen. Ereignisse (Textstücke, Werkzeugaufrufe,
Berechtigungsfragen) werden an die WebKit-Oberfläche weitergereicht; Antworten gehen über
`control_response` zurück. Ein lokaler HTTP-Server liefert die Oberfläche, lokale Dateien
(`/file/<pfad>`) und abgelegte HTML-Vorschauen (`~/.cache/richterm/html/`). Einstellungen in
`~/.config/richterm.json`.
