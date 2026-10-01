# RichTerm

Ein Terminal mit einer Anzeige daneben, die alles darstellen kann, was ein Browser kann:
LaTeX-Formeln in Buchqualität, Markdown, Bilder, GIFs, Videos, Diagramme, HTML mit Animationen.

- **Links:** ein vollwertiges Terminal (VTE, dieselbe Engine wie GNOME Terminal), mit Tabs.
- **Rechts:** die Anzeige (WebKit). Inhalte landen dort als Karten, neueste unten.
- **`rt`:** der Befehl, mit dem du oder Claude Code Inhalte in die Anzeige schicken.

Keine Installation nötig: Es werden nur Python 3 und die auf Linux Mint vorhandenen
GTK-, VTE- und WebKit-Bindungen benutzt. Die JavaScript-Bibliotheken (KaTeX, marked, mermaid)
liegen in `vendor/`, alles läuft offline.

## Starten

- Doppelklick auf **RichTerm** auf dem Desktop, oder über das Anwendungsmenü, oder:
  ```bash
  ~/Desktop/RichTerm/start.sh
  ```

## Inhalte anzeigen

```bash
rt latex '\frac{1}{1+e^{-z}}'          # Formel (mehrere durch Leerzeilen trennen)
rt latex -f formeln.tex                # Formeln aus einer Datei
rt md notizen.md                       # Markdown-Datei, $…$ wird als Formel gesetzt
rt md -t '# Titel\n\nText mit $x^2$'   # Markdown direkt
rt image plot.png                      # Bild (PNG, JPG, SVG, GIF – GIFs animieren)
rt video clip.mp4                      # Video in Schleife
rt html seite.html                     # HTML-Seite mit eigenem JavaScript, Canvas, Animationen
rt html -t '<canvas …>'                # HTML direkt
rt url https://example.org             # Webseite einbetten
rt mermaid 'graph LR; A-->B-->C'       # Diagramm
rt text 'Klartext'
rt clear                               # Anzeige leeren
echo '\sum_{i=1}^n i' | rt latex       # von stdin
```

Optionen: `--title 'Überschrift'`, `--height 600` (für `html`/`url`), `--quiet` (Anzeige nicht einblenden).

`rt` funktioniert auch aus anderen Terminals heraus, solange RichTerm läuft
(es findet die laufende Instanz über eine Port-Datei).

## Tastenkürzel

| Taste | Wirkung |
|---|---|
| Ctrl+Shift+T | neuer Tab (im aktuellen Verzeichnis) |
| Ctrl+Shift+W | Tab schließen |
| Ctrl+PgUp / Ctrl+PgDn | Tab wechseln |
| Ctrl+Shift+C / Ctrl+Shift+V | kopieren / einfügen (Rechtsklick fügt auch ein) |
| Ctrl+Shift++ / Ctrl+Shift+- | Terminal-Schrift größer / kleiner |
| Ctrl+Shift+F | Anzeige ein-/ausblenden |
| Ctrl+Klick auf Link | Link im Browser öffnen |

In der Anzeige: `A−`/`A+` Schriftgröße, ☀/☾ hell/dunkel, `Leeren`, ✕ an jeder Karte.
Die Trennlinie zwischen Terminal und Anzeige lässt sich mit der Maus verschieben.

## Mit Claude Code

Claude Code im RichTerm starten wie gewohnt (`claude`). In der Shell ist `RICHTERM=1` gesetzt,
Claude weiß dann, dass es Formeln, Diagramme und Bilder mit `rt` in die Anzeige schicken kann.

## Aufbau

```
RichTerm/
├── richterm.py        die App (GTK-Fenster, VTE-Tabs, WebKit-Anzeige, HTTP-Empfänger)
├── bin/rt             der Befehl zum Senden
├── panel/             die Anzeige-Seite (index.html, panel.css, panel.js)
├── vendor/            KaTeX, marked, mermaid (offline)
└── start.sh           Starter (setzt bin/ in den PATH)
```

Technik: Die App startet einen HTTP-Server auf `127.0.0.1` mit zufälligem Port und setzt
`RT_PORT` in jeder Shell. `rt` schickt JSON an `POST /show`, die App reicht es per JavaScript
an die Anzeige weiter. Lokale Dateien werden über `/file/<pfad>` ausgeliefert, HTML-Karten
unter `~/.cache/richterm/html/` abgelegt. Einstellungen (Fenstergröße, Schrift, Teilung)
liegen in `~/.config/richterm.json`.
