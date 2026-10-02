# RichTerm

Chat mit Claude Code wie im Browser, aber mit Zugriff auf deine lokalen Ordner, und ein Terminal
im selben Fenster. Die Antworten werden direkt im Chat gerendert:

- **LaTeX-Formeln** in Buchqualität (KaTeX), inline `$…$` und abgesetzt `$$…$$`
- **Markdown** mit Überschriften, Tabellen, Codeblöcken
- **Mermaid-Diagramme** und **SVG**
- **HTML-Blöcke als Live-Vorschau**: Animationen, Canvas, interaktive Simulationen laufen direkt in der Antwort
- **Bilder**, die Claude erzeugt (z. B. matplotlib-Plots)

Dazu Tabs mit einem vollwertigen Terminal (VTE, dieselbe Engine wie GNOME Terminal).

**Ausführliche Bedienungsanleitung:** [`docs/ANLEITUNG.pdf`](docs/ANLEITUNG.pdf) (Quelle `docs/ANLEITUNG.html`, neu bauen mit `docs/build.sh`).

## Installation (Linux und macOS)

```bash
git clone https://github.com/DavyySz/RichTerm.git
cd RichTerm
./richterm
```

Mehr nicht. Beim ersten Start prüft RichTerm selbst, was fehlt, und richtet es ein: Systempakete (fragt
nach dem Administrator-Passwort, `apt`/`dnf`/`pacman`/`zypper`), die Befehle `richterm` und `rt` in
`~/.local/bin`, Menü-/Desktop-Eintrag, Rechtsklick-Eintrag (Nemo), auf Wunsch Claude Code und ein lokales
Ollama für Offline-Modelle. Ist alles da, startet es einfach (die Prüfung dauert eine Viertelsekunde).
Danach überall: `cd <ordner> && richterm`. `./setup.sh --uninstall` entfernt die Verknüpfungen,
`./install.sh` richtet alles ein, ohne zu starten.

**Voraussetzungen:** Python 3 mit GObject-Bindungen, GTK 3, VTE 2.91, WebKitGTK 4.1 (auf Linux Mint und
Ubuntu vorinstalliert; sonst z. B. `sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 gir1.2-webkit2-4.1`)
und [Claude Code](https://docs.anthropic.com/claude-code) (`claude`, einmal gestartet und angemeldet). Ohne Claude Code
funktioniert nur der Chat mit lokalen Modellen. Die JavaScript-Bibliotheken (KaTeX, marked, mermaid, highlight.js)
liegen in `vendor/`, alles läuft offline.

**macOS:** gleicher Ablauf (`git clone … && cd RichTerm && ./richterm`). Dort läuft RichTerm im **Browser-Modus**:
Python startet den Server, der Chat öffnet sich als eigenes Fenster in Chrome/Chromium/Edge (ohne Adressleiste) oder
im Standardbrowser. Alles außer den Terminal-Tabs funktioniert gleich (Formeln, Diagramme, Animationen, Anhänge,
Lernzettel, Profile, Verlauf, lokale Modelle). Den Ordner wählst du beim Start (`richterm ~/Ordner`) oder über den
Ordnerknopf (Pfad eingeben). Claude Code und Ollama gibt es für macOS; `./richterm` bietet die Installation an.
Unter Linux erzwingt `richterm --web` den Browser-Modus ebenfalls (z. B. ohne GTK). Windows: ungetestet, der
Browser-Modus sollte unter WSL laufen.

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

## Unterlagen: der Ordner `rag/`

Das Herzstück fürs Lernen mit Vorlesungsfolien. Die KI bekommt zu jeder Frage die passenden Stellen aus deinen
Unterlagen mit und zitiert sie, egal ob Claude oder ein lokales Modell antwortet.

**So geht's:**

1. `richterm` im Arbeitsordner starten. Der Ordner `rag/` wird dabei angelegt (mit einer kurzen LIES-MICH.txt).
2. Folien, Skripte, Übungen, Notizen nach `rag/` kopieren: PDF, Markdown, Text, DOCX, HTML, Code. Unterordner sind
   erlaubt und werden immer mit durchsucht, z. B. `rag/maschinelles_lernen/kapitel_3/`.
3. Fragen stellen. Unter deiner Frage steht „📚 Unterlagen: vorlesung_03.pdf S. 4 · notizen.md“, die Antwort sagt
   „laut vorlesung_03.pdf S. 4 …“. Fehlt die 📚-Zeile, wurde nichts Passendes gefunden.

**Wie streng?** Menü **Unterlagen** in der Kopfzeile (gilt pro Ordner, wird gespeichert):

| Einstellung | Verhalten |
|---|---|
| Unterlagen: aus | `rag/` wird ignoriert. |
| Unterlagen: ergänzend *(Standard)* | Unterlagen sind die Hauptquelle, eigenes Wissen darf ergänzt werden und wird gekennzeichnet. |
| Unterlagen: nur daraus | Streng: nur aus den Unterlagen, jede Aussage mit Quelle; sonst „dazu steht nichts in den Unterlagen“. |

**Gezielt fragen:**

| Eingabe | Durchsucht |
|---|---|
| `Was ist die Lernrate?` | alles in `rag/`, inklusive Unterordner |
| `rag kapitel_3 Was ist die Lernrate?` | nur den Ordner `kapitel_3` (mit Unterordnern) |
| `rag kapitel_3/folien.pdf Was ist die Lernrate?` | nur diese Datei |
| `rag folien Was …` / `rag mcts Was …` | Ordner- oder Dateiname reicht, ohne vollen Pfad; mehrere Angaben möglich |
| `Fasse @folien.pdf zusammen` | gibt die **ganze Datei** mit statt nur Treffer (für Zusammenfassungen) |

**Was im Hintergrund passiert:** Dateien werden in Abschnitte zerlegt (PDFs seitenweise) und in `rag/.richterm-index.json`
indexiert; neue oder geänderte Dateien werden automatisch erkannt. Die Suche ist hybrid: Stichwörter (BM25) plus
Bedeutung über ein kleines lokales Einbettungsmodell (`embeddinggemma`, 620 MB, wird beim ersten Mal automatisch
über Ollama geladen und rechnet im Hintergrund; bis dahin läuft die Stichwortsuche). So findet „Schrittweite“ auch
eine Folie mit „Lernrate“, und Fragen ohne Bezug zu den Unterlagen („Hauptstadt von Peru“) liefern keine Treffer.
Die Dateien bleiben, wo sie sind; das Modell sieht nur die passenden Ausschnitte. Inhaltsgleiche Dateien (z. B. dieselbe
PDF in `self_data/` und in einem anderen Ordner) werden nur einmal indexiert und erscheinen nur einmal in den Treffern.

Voraussetzung für PDFs: `pdftotext` (Linux-Paket `poppler-utils`, meist vorhanden; macOS `brew install poppler`).
Im Profil: `rag_chunks` (Standard 6) = Abschnitte pro Frage, `rag: false` schaltet alles ab.

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

Im **Modellmenü** oben im Chat stehen neben den Claude-Modellen alle lokalen Modelle; „Lokales Modell
herunterladen …“ lädt ein neues (Name eintippen, z. B. `qwen2.5:7b`, Laden). Die Auswahl gilt pro Ordner,
RichTerm trägt sie selbst in `richterm.md` ein.

Hintergrund: Ollama liegt ohne Installation unter `~/.local/share/ollama/dist/bin`; RichTerm startet den
Dienst bei Bedarf selbst (Port 11435, Modelle unter `~/.local/share/ollama/models`), unabhängig von einem
evtl. vorhandenen älteren System-Ollama. Lokale Modelle haben keine Dateiwerkzeuge; den Ordner lesen kann
nur das Claude-Backend.

## Sitzungen: zwei Ebenen

1. **Claude-Code-Sitzung.** Claude Code speichert jedes Gespräch selbst (unter `~/.claude/projects/…`).
   RichTerm merkt sich pro Ordner die letzte Sitzung und **setzt sie beim nächsten Start automatisch fort**,
   mit allem, was Claude gelesen und getan hat. **Neuer Chat** beendet das und beginnt frisch.
2. **`richterm-verlauf.md`** im Ordner: das modellunabhängige Gedächtnis (siehe oben). Es wird beim Start
   angelegt, bei jeder Antwort ergänzt und beim Start zur Orientierung im Chat angezeigt. Nur darüber
   kennt ein anderes Modell (Sonnet statt Haiku, ein lokales Modell) den bisherigen Kontext.

## Chat

1. Oben links mit 📁 den **Arbeitsordner** wählen. Claude sieht und bearbeitet Dateien in diesem Ordner.
2. Frage eintippen, **Enter** sendet, **Shift+Enter** macht einen Zeilenumbruch.
3. Will Claude ein Werkzeug benutzen (Datei schreiben, Befehl ausführen), erscheint eine Frage im Chat
   mit **Erlauben / Ablehnen**. Mit dem Menü „Berechtigungen“ lässt sich das vorab einstellen.
4. **■** bricht eine laufende Antwort ab. **Neuer Chat** beginnt eine frische Sitzung.
5. **Anhänge:** Bilder, Screenshots und Dateien ins Fenster ziehen, mit Ctrl+V einfügen oder über 📎 wählen, ohne
   Größenlimit. Alles landet in `rag/self_data/` im Arbeitsordner (wird mit indexiert, steht also auch späteren Fragen
   zur Verfügung). Claude sieht Bilder direkt; sehr große Bilder werden für die Übertragung verkleinert.
6. **Unter jeder Antwort:** Schnellaktionen (Einfacher · Kürzer · Beispiel · Abfragen) und **Lernzettel speichern**
   (legt die Antwort als Markdown in `lernzettel/` ab). An Codeblöcken: **Kopieren**.

Werkzeugaufrufe (gelesene Dateien, Befehle) erscheinen als graue Zeilen; ein Klick zeigt Details und Ergebnis.
Bei HTML-Vorschauen: **Code** zeigt den Quelltext, **Im Browser** öffnet die Seite groß.

Kopfzeile: Modell (Standard/Opus/Sonnet/Haiku), Berechtigungen, ☀/☾ hell/dunkel.
Ctrl + Plus/Minus ändert die Schriftgröße im Chat.

## Hintergrundbilder

Im Menü **Hintergrund** in der Kopfzeile: Standard (grau) oder eines der mitgelieferten Fotos (Nebelwald, Nebelberge,
Nebelhügel, Herbstwald, Nachthimmel, Nordlicht, Wüste bei Nacht; alle gemeinfrei/CC0, Nachweise in
`backgrounds/ATTRIBUTION.md`). Der Regler daneben dunkelt das Bild ab (im hellen Modus hellt er auf), damit Text lesbar
bleibt; Textflächen werden leicht durchscheinend. Eigene Bilder (JPG/PNG/WebP) nach `~/.config/richterm/backgrounds/`
legen, sie erscheinen im Menü als „Eigenes Bild“. Im nativen Fenster liegt das Bild auch hinter den Terminal-Tabs.

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
