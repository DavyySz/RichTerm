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

Der übliche Weg: ein Terminal im gewünschten Ordner öffnen (z.&nbsp;B. dem mit den Vorlesungsfolien) und

```bash
richterm              # RichTerm mit diesem Ordner als Arbeitsordner
richterm ~/Uni/ML     # oder einen Ordner angeben
```

Das Terminal bleibt frei und darf geschlossen werden. Außerdem:
- Rechtsklick im Dateimanager (Nemo) auf einen Ordner oder in einen Ordner → **RichTerm hier öffnen**
- Doppelklick auf **RichTerm** auf dem Desktop oder im Anwendungsmenü (startet im Home-Ordner)

## Profil pro Ordner: `richterm.md`

Beim ersten Start in einem Ordner legt RichTerm dort die Datei **`richterm.md`** an (aus `profile_template.md`).
Sie wird bei jedem Start gelesen und der KI als verbindliche Anweisung mitgegeben. Knopf **Profil** öffnet sie
im Editor, **Neu laden** übernimmt Änderungen.

Oben in der Datei stehen Einstellungen:

| Feld | Bedeutung |
|---|---|
| `backend` | `claude` = Claude Code (voller Agent mit Dateizugriff) · `command` = beliebige andere KI mit Kommandozeile (nur Chat) |
| `model` | Modell, z. B. `opus`, `sonnet`, `haiku`; bei `command` wird es in den Befehl eingesetzt |
| `command` | nur für `command`: z. B. `ollama run {model}`. Die KI bekommt Profil + Verlauf auf stdin, antwortet auf stdout |
| `permissions` | `default` (fragen) · `acceptEdits` · `bypassPermissions` |
| `allowed_tools` / `disallowed_tools` | Werkzeuge einschränken, z. B. `Read Grep Glob` für reines Lesen |
| `language` | Sprache der Antworten |
| `history` / `context_chars` | Gedächtnis an/aus und wie viel Verlauf mitgegeben wird (siehe unten) |

Darunter in normalem Text: **Rolle**, **Themengebiet und Ziel**, **Was im Ordner liegt**, **Verhalten**,
**Genauigkeit und Kreativität**, **Antwortformat**, **Nicht erlaubt**. Die Vorlage enthält zu jedem Abschnitt
Beispiele; schreib sie einfach um. Werte aus dem Profil haben Vorrang vor der Kopfzeile (die Felder sind dann gesperrt).

So verhält sich die KI im Vorlesungsordner wie ein Tutor und im Projektordner wie ein Entwickler, ohne dass du
es jedes Mal erklären musst.

## Gedächtnis: `richterm-verlauf.md`

Jede Frage und Antwort wird in **`richterm-verlauf.md`** im Ordner protokolliert, in Markdown mit
LaTeX-Formeln, Zeitstempel und Modell. Beim Start jeder Sitzung, also auch nach einem Modell- oder
Backend-Wechsel, bekommt die KI die **Zusammenfassung** oben in der Datei und den **jüngsten Verlauf**
mit. So bleibt der Kontext erhalten, egal welche KI gerade antwortet.

Wird der Rohverlauf länger als das Budget (`context_chars` im Profil, Standard 20000 Zeichen),
verdichtet das Modell den älteren Teil automatisch zur Zusammenfassung. Das Original wandert nach
`richterm-verlauf.archiv.md`, nichts geht verloren. Beide Dateien darfst du selbst bearbeiten,
z. B. die Zusammenfassung kürzen oder Wichtiges ergänzen. `history: false` im Profil schaltet das ab.

## Lokale KI mit Ollama

Ollama liegt ohne Installation unter `~/.local/share/ollama/dist/bin` (RichTerm startet den Dienst
bei Bedarf selbst). Im Profil:

```yaml
backend: command
model: qwen2.5:0.5b          # kleinstes brauchbares Modell zum Testen; besser: qwen2.5:7b
command: ollama run {model}
context_chars: 6000          # kleine Modelle vertragen weniger Kontext
```

Neue Modelle laden: `ollama pull qwen2.5:7b` (im RichTerm-Terminal, dort ist `ollama` im PATH).
Lokale Modelle haben keine Dateiwerkzeuge; den Ordner lesen kann nur das Claude-Backend.

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
├── profile_template.md   Vorlage für richterm.md
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
