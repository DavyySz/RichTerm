#!/usr/bin/env python3
"""
RichTerm — Chat mit Claude Code wie im Browser, plus Terminal, in einem Fenster.

Tab „Chat“:      Gespräch mit Claude Code. Antworten werden gerendert: Markdown, LaTeX (KaTeX),
                 Mermaid-Diagramme, Bilder, SVG und HTML-Blöcke als Live-Vorschau (Animationen).
                 Claude arbeitet im gewählten Ordner und kann dort Dateien lesen und schreiben.
Tabs „Terminal“: vollwertiges Terminal (VTE, dieselbe Engine wie GNOME Terminal).

Der Befehl `rt` (bin/rt) schickt Inhalte aus jedem Terminal in den Chat.

Tastenkürzel:
  Ctrl+Shift+T  neues Terminal      Ctrl+Shift+W  Terminal schließen
  Ctrl+PgUp/PgDn  Tab wechseln      Ctrl+Shift+C / Ctrl+Shift+V  kopieren / einfügen (Terminal)
  Ctrl+Shift++ / Ctrl+Shift+-  Terminal-Schrift   Ctrl+Shift+Enter  zum Chat springen
"""
import datetime
import json
import mimetypes
import os
import shlex
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Vte', '2.91')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, Gdk, GLib, Vte, Pango, WebKit2  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser('~')
CONFIG_PATH = os.path.join(GLib.get_user_config_dir(), 'richterm.json')
PORT_FILE = os.path.join(GLib.get_user_runtime_dir(), 'richterm.port')
HTML_DIR = os.path.join(GLib.get_user_cache_dir(), 'richterm', 'html')

DEFAULTS = {
    'font': 'DejaVu Sans Mono 11',
    'scrollback': 100000,
    'window': [1400, 900],
    'cwd': HOME,
    'model': '',
    'perm': 'default',
}

PALETTE = {
    'fg': '#e8e6e1', 'bg': '#2b2e33',
    'colors': ['#3c4148', '#e5736f', '#8fc48a', '#e0b96a', '#79a8e0', '#c08ad6', '#6fc0c8', '#d6d3cc',
               '#5a6270', '#f09590', '#a9d9a4', '#efd08f', '#9dc0f0', '#d5a8e6', '#93d6dd', '#f2f0eb'],
}

# Was Claude über diese Oberfläche wissen soll
SYSTEM_NOTE = """Du antwortest in RichTerm, einer Chat-Oberfläche, die deine Antworten als Markdown rendert.
Nutze das aktiv, wie in einem Lehrbuch oder einem guten Browser-Chat:
- Formeln in LaTeX: inline $…$, abgesetzt $$…$$. Immer echtes LaTeX, nie Unicode-Mathematik.
- ```mermaid-Blöcke werden als Diagramm gezeichnet, ```svg-Blöcke inline angezeigt.
- ```html-Blöcke werden als Live-Vorschau mit laufendem JavaScript angezeigt. Nutze sie für Animationen,
  interaktive Visualisierungen, Canvas-Grafiken und Simulationen (vollständige HTML-Dokumente mit eigenem
  Script, keine externen Ressourcen; Höhe per Kommentar <!-- height: 500 --> steuerbar).
- Bilder, die du mit matplotlib o.ä. als Datei erzeugst, zeigst du mit ![Beschreibung](pfad/zur/datei.png).
- Tabellen, Überschriften und Codeblöcke wie üblich in Markdown.
Der Nutzer lernt mit dir; erkläre schrittweise und zeige Rechnungen als Formeln."""


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding='utf-8') as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
    if not os.path.isdir(cfg.get('cwd') or ''):
        cfg['cwd'] = HOME
    return cfg


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as fh:
            json.dump(cfg, fh, indent=2)
    except OSError:
        pass


def rgba(hexstr):
    c = Gdk.RGBA()
    c.parse(hexstr)
    return c


# ----------------------------------------------------------------------------
# Profil: richterm.md im Arbeitsordner (Einstellungen + Anweisungen an die KI)
# ----------------------------------------------------------------------------
PROFILE_NAME = 'richterm.md'
TEMPLATE = os.path.join(HERE, 'profile_template.md')


def load_profile(cwd):
    """Liest richterm.md aus cwd; legt sie aus der Vorlage an, falls sie fehlt.
    Gibt (settings, body, neu_angelegt) zurück."""
    path = os.path.join(cwd, PROFILE_NAME)
    created = False
    if not os.path.exists(path):
        try:
            shutil.copyfile(TEMPLATE, path)
            created = True
        except OSError:
            return {}, '', False
    try:
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
    except OSError:
        return {}, '', created
    settings, body = {}, text
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            head, body = text[3:end], text[end + 4:]
            for line in head.splitlines():
                line = line.strip()
                if not line or line.startswith('#') or ':' not in line:
                    continue
                key, _, value = line.partition(':')
                settings[key.strip().lower()] = value.strip().strip('"\'')
    return settings, body.strip(), created


def update_profile_settings(cwd, **changes):
    """Setzt Schlüssel im Einstellungsblock von richterm.md (legt Datei/Schlüssel bei Bedarf an)."""
    path = os.path.join(cwd, PROFILE_NAME)
    if not os.path.exists(path):
        load_profile(cwd)
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    if not text.startswith('---'):
        text = '---\n---\n' + text
    end = text.find('\n---', 3)
    head, rest = text[3:end], text[end:]
    lines = head.split('\n')
    for key, value in changes.items():
        done = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith('#') and stripped.split(':', 1)[0].strip().lower() == key:
                lines[i] = '%s: %s' % (key, value)
                done = True
                break
        if not done:
            lines.append('%s: %s' % (key, value))
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('---' + '\n'.join(lines) + rest)


def list_local_models():
    """Lokal verfügbare Ollama-Modelle: [(name, größe_gb)]."""
    import urllib.request
    exe = find_ollama()
    if not exe:
        return []
    host = OLLAMA_HOST if exe == OLLAMA_LOCAL else os.environ.get('OLLAMA_HOST', '127.0.0.1:11434')
    try:
        if not ensure_ollama_running(timeout=8):
            return []
        with urllib.request.urlopen('http://%s/api/tags' % host, timeout=5) as resp:
            data = json.load(resp)
        return [(m['name'], round(m.get('size', 0) / 1e9, 1)) for m in data.get('models', [])]
    except Exception:  # noqa: BLE001
        return []


def build_system_prompt(settings, body):
    parts = [SYSTEM_NOTE]
    lang = settings.get('language')
    if lang:
        parts.append('Antworte auf ' + lang + '.')
    if body:
        parts.append('# Profil des Nutzers für diesen Ordner (verbindlich)\n\n' + body)
    return '\n\n'.join(parts)


def split_tools(value):
    if not value:
        return []
    return [t.strip() for t in value.split(',')] if ',' in value else shlex.split(value)


# ----------------------------------------------------------------------------
# Claude-Code-Sitzung: ein `claude -p`-Prozess im Streaming-Modus
# ----------------------------------------------------------------------------
class ClaudeSession:
    name = 'Claude Code'

    def __init__(self, cwd, model, perm, port, on_message, on_exit, system_prompt=SYSTEM_NOTE,
                 allowed_tools=(), disallowed_tools=(), resume=None):
        self.on_message = on_message
        self.on_exit = on_exit
        self.proc = None
        self.stderr_tail = ''
        self.lock = threading.Lock()
        exe = shutil.which('claude')
        if not exe:
            raise RuntimeError('Der Befehl `claude` wurde nicht gefunden. Ist Claude Code installiert?')
        cmd = [exe, '-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
               '--verbose', '--include-partial-messages', '--permission-prompt-tool', 'stdio',
               '--append-system-prompt', system_prompt]
        if model:
            cmd += ['--model', model]
        if perm and perm != 'default':
            cmd += ['--permission-mode', perm]
        if allowed_tools:
            cmd += ['--allowedTools'] + list(allowed_tools)
        if disallowed_tools:
            cmd += ['--disallowedTools'] + list(disallowed_tools)
        if resume:
            cmd += ['--resume', resume]
        self.cmd = cmd
        env = dict(os.environ)
        env['RICHTERM'] = '1'
        env['RT_PORT'] = str(port)
        self.proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._err_reader, daemon=True).start()

    def _reader(self):
        proc = self.proc
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self.on_message(msg)
        self.on_exit(proc.wait())

    def _err_reader(self):
        tail = []
        for line in self.proc.stderr:
            tail.append(line.rstrip())
            tail = tail[-30:]
            self.stderr_tail = '\n'.join(tail)

    def _write(self, obj):
        with self.lock:
            if self.proc and self.proc.stdin:
                try:
                    self.proc.stdin.write(json.dumps(obj) + '\n')
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    pass

    def send_user(self, text):
        self._write({'type': 'user', 'message': {'role': 'user', 'content': text}})

    def answer_permission(self, request_id, allow, updated_input=None):
        if allow:
            resp = {'behavior': 'allow', 'updatedInput': updated_input or {}}
        else:
            resp = {'behavior': 'deny', 'message': 'Vom Nutzer abgelehnt'}
        self._write({'type': 'control_response',
                     'response': {'subtype': 'success', 'request_id': request_id, 'response': resp}})

    def interrupt(self):
        self._write({'type': 'control_request', 'request_id': 'int-%d' % os.getpid(),
                     'request': {'subtype': 'interrupt'}})

    def close(self):
        proc, self.proc = self.proc, None
        if proc:
            for fn in (proc.stdin.close, proc.terminate):
                try:
                    fn()
                except OSError:
                    pass


# ----------------------------------------------------------------------------
# Beliebige andere KI mit Kommandozeile (z.B. `ollama run modell`): reiner Chat.
# Pro Nachricht wird der Befehl gestartet, bekommt Profil + bisherigen Verlauf auf
# stdin und antwortet auf stdout. Die Ausgabe wird in dasselbe Ereignisformat
# übersetzt, das die Oberfläche von Claude Code kennt.
# ----------------------------------------------------------------------------
# Eigenes Ollama unter ~/.local (ohne sudo installiert), auf eigenem Port, damit es einem
# evtl. vorhandenen (älteren) Systemdienst auf 11434 nicht in die Quere kommt.
OLLAMA_DIR = os.path.join(HOME, '.local', 'share', 'ollama')
OLLAMA_LOCAL = os.path.join(OLLAMA_DIR, 'dist', 'bin', 'ollama')
OLLAMA_HOST = '127.0.0.1:11435'
OLLAMA_MODELS = os.path.join(OLLAMA_DIR, 'models')


def find_ollama():
    if os.path.exists(OLLAMA_LOCAL):
        return OLLAMA_LOCAL
    return shutil.which('ollama')


def ollama_env(env):
    """Umgebung für Client und Server des eigenen Ollama."""
    env = dict(env)
    if find_ollama() == OLLAMA_LOCAL:
        env['OLLAMA_HOST'] = OLLAMA_HOST
        env['OLLAMA_MODELS'] = OLLAMA_MODELS
        env['PATH'] = os.path.dirname(OLLAMA_LOCAL) + os.pathsep + env.get('PATH', '')
    return env


def ensure_ollama_running(timeout=30):
    """Startet `ollama serve` im Hintergrund, falls der Dienst nicht erreichbar ist."""
    import urllib.request
    exe = find_ollama()
    host = OLLAMA_HOST if exe == OLLAMA_LOCAL else os.environ.get('OLLAMA_HOST', '127.0.0.1:11434')
    url = 'http://%s/api/version' % host

    def alive():
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:  # noqa: BLE001
            return False
    if alive():
        return True
    if not exe:
        return False
    env = ollama_env(os.environ)
    os.makedirs(OLLAMA_MODELS, exist_ok=True)
    log = open(os.path.join(OLLAMA_DIR, 'serve.log'), 'a')
    subprocess.Popen([exe, 'serve'], env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    import time
    for _ in range(timeout * 2):
        if alive():
            return True
        time.sleep(0.5)
    return False


class CommandSession:
    name = 'Kommandozeilen-KI'

    def __init__(self, cwd, command, model, system_prompt, on_message, on_exit):
        self.cwd = cwd
        self.command = command.replace('{model}', model or '')
        self.system_prompt = system_prompt
        self.on_message = on_message
        self.on_exit = on_exit
        self.history = []
        self.proc = None
        self.stderr_tail = ''
        self.closed = False

    def send_user(self, text):
        self.history.append(('Nutzer', text))
        threading.Thread(target=self._run, daemon=True).start()

    def _transcript(self):
        lines = ['SYSTEM:\n' + self.system_prompt, '']
        for role, msg in self.history:
            lines.append(role + ':\n' + msg + '\n')
        lines.append('Assistent:')
        return '\n'.join(lines)

    def _run(self):
        self.on_message({'type': 'system', 'subtype': 'init', 'model': self.command, 'cwd': self.cwd})
        argv = shlex.split(self.command)
        env = dict(os.environ)
        if argv and os.path.basename(argv[0]) == 'ollama':
            exe = find_ollama()
            if exe:
                argv[0] = exe
                env = ollama_env(env)
            self.on_message({'type': 'system', 'subtype': 'status', 'status': 'ollama'})
            if not ensure_ollama_running():
                self.on_message({'type': 'result', 'is_error': True,
                                 'result': 'Ollama ist nicht installiert oder startet nicht (erwartet `ollama` im PATH oder unter %s).' % OLLAMA_LOCAL})
                return
        try:
            self.proc = subprocess.Popen(argv, cwd=self.cwd, env=env, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except OSError as e:
            self.on_message({'type': 'result', 'is_error': True, 'result': 'Befehl konnte nicht gestartet werden: %s' % e})
            return
        threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True).start()
        try:
            self.proc.stdin.write(self._transcript())
            self.proc.stdin.close()
        except OSError:
            pass
        ev = lambda e: self.on_message({'type': 'stream_event', 'event': e})
        ev({'type': 'message_start'})
        ev({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})
        answer = []
        import re
        ansi = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
        pending = ''
        while True:
            chunk = self.proc.stdout.read(64)
            if not chunk:
                break
            pending += chunk
            # unvollständige Escape-Sequenz am Ende zurückhalten, bis der Rest da ist
            cut = pending.rfind('\x1b')
            if cut != -1 and not re.match(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]', pending[cut:]):
                out, pending = pending[:cut], pending[cut:]
            else:
                out, pending = pending, ''
            out = ansi.sub('', out)
            if out:
                answer.append(out)
                ev({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': out}})
        if pending:
            out = ansi.sub('', pending)
            if out:
                answer.append(out)
                ev({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': out}})
        ev({'type': 'content_block_stop', 'index': 0})
        code = self.proc.wait()
        full = ''.join(answer).strip()
        self.history.append(('Assistent', full))
        if code != 0 and not full:
            self.on_message({'type': 'result', 'is_error': True, 'result': 'Befehl endete mit Code %s\n%s' % (code, self.stderr_tail)})
        else:
            self.on_message({'type': 'result', 'is_error': False})

    def _drain_stderr(self, proc):
        tail = []
        for line in proc.stderr:
            tail.append(line.rstrip())
            self.stderr_tail = '\n'.join(tail[-20:])

    def answer_permission(self, *a):
        pass

    def interrupt(self):
        if self.proc:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def close(self):
        self.closed = True
        self.interrupt()


# ----------------------------------------------------------------------------
# Verlauf: richterm-verlauf.md im Arbeitsordner.
# Jede abgeschlossene Frage/Antwort wird angehängt. Oben steht eine Zusammenfassung,
# die automatisch vom Modell gepflegt wird, sobald der Rohverlauf das Kontextbudget
# überschreitet; die verdichteten Rohteile wandern in richterm-verlauf.archiv.md.
# Beim Start einer Sitzung bekommt die KI Zusammenfassung + jüngsten Verlauf mit.
# ----------------------------------------------------------------------------
HISTORY_NAME = 'richterm-verlauf.md'
ARCHIVE_NAME = 'richterm-verlauf.archiv.md'
HISTORY_HEAD = """# RichTerm-Verlauf

<!-- Diese Datei pflegt RichTerm automatisch. Sie dient der KI als Gedächtnis über Sitzungen
und Modellwechsel hinweg. Du darfst sie bearbeiten: Die Zusammenfassung kannst du selbst
kürzen oder ergänzen; der Verlauf darunter ist das Rohprotokoll (neueste Einträge unten).
Format: "### <Zeit> · Nutzer" und "### <Zeit> · Assistent (<Backend>)", dazwischen der Text. -->

## Zusammenfassung

(noch leer)

## Verlauf
"""
SUMMARY_PROMPT = """Du pflegst das Langzeitgedächtnis eines Lern- und Arbeitsassistenten. Unten stehen die bisherige
Zusammenfassung und ein Stück Rohverlauf, das jetzt verdichtet werden soll. Schreibe eine NEUE, vollständige
Zusammenfassung in Markdown, die beides zusammenführt. Behalte alles, was für die weitere Arbeit zählt:
Thema und Ziel des Nutzers, getroffene Entscheidungen, Erkenntnisse und Ergebnisse, Definitionen und Formeln
(in LaTeX), offene Fragen, Dateien und Orte, Vorlieben des Nutzers. Lass Höflichkeiten und Wiederholungen weg.
Stichpunkte sind gut, Zwischenüberschriften höchstens als "###". Antworte NUR mit der Zusammenfassung, ohne Vor- oder Nachwort.

## Bisherige Zusammenfassung
{summary}

## Zu verdichtender Rohverlauf
{raw}
"""


class History:
    def __init__(self, cwd, enabled=True, budget=20000):
        self.cwd = cwd
        self.enabled = enabled
        self.budget = max(2000, int(budget or 20000))
        self.path = os.path.join(cwd, HISTORY_NAME)
        self.lock = threading.Lock()

    # --- Datei lesen/schreiben --------------------------------------------
    def _read(self):
        try:
            with open(self.path, encoding='utf-8') as fh:
                return fh.read()
        except OSError:
            return ''

    def _split(self, text):
        """-> (zusammenfassung, rohverlauf)"""
        if '## Verlauf' not in text:
            return '', ''
        head, raw = text.split('## Verlauf', 1)
        summary = ''
        if '## Zusammenfassung' in head:
            summary = head.split('## Zusammenfassung', 1)[1].strip()
            if summary == '(noch leer)':
                summary = ''
        return summary, raw.strip()

    def _write(self, summary, raw):
        text = HISTORY_HEAD.replace('(noch leer)', summary.strip() or '(noch leer)') + '\n' + raw.strip() + '\n'
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(text)
        os.replace(tmp, self.path)

    def ensure_file(self):
        """Legt die Verlaufsdatei an, falls sie fehlt (beim Start, nicht erst bei der ersten Antwort)."""
        if self.enabled and not os.path.exists(self.path):
            try:
                with open(self.path, 'w', encoding='utf-8') as fh:
                    fh.write(HISTORY_HEAD)
            except OSError:
                pass

    def recent_turns(self, limit=8):
        """Die letzten Einträge als Liste von (rolle, zeit, text) für die Anzeige beim Start."""
        _, raw = self._split(self._read())
        turns = []
        for block in raw.split('\n### ')[1:]:
            head, _, body = block.partition('\n')
            stamp, _, who = head.partition(' · ')
            role = 'user' if who.startswith('Nutzer') else 'assistant'
            turns.append((role, stamp, body.strip()))
        return turns[-limit:]

    # --- Anhängen -----------------------------------------------------------
    def append_turn(self, user_text, assistant_text, tools, backend_label):
        if not self.enabled or not user_text.strip():
            return
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        entry = '\n### %s · Nutzer\n%s\n\n### %s · Assistent (%s)\n%s\n' % (
            stamp, user_text.strip(), stamp, backend_label, assistant_text.strip() or '(keine Textantwort)')
        if tools:
            entry += '> Werkzeuge: ' + '; '.join(tools[:12]) + ('; …' if len(tools) > 12 else '') + '\n'
        with self.lock:
            exists = bool(self._read().strip())
            with open(self.path, 'a', encoding='utf-8') as fh:
                if not exists:
                    fh.write(HISTORY_HEAD)
                fh.write(entry)

    # --- Kontext für den Start einer Sitzung ---------------------------------
    def context_block(self):
        if not self.enabled:
            return ''
        summary, raw = self._split(self._read())
        if not summary and not raw:
            return ''
        tail = raw[-self.budget:] if len(raw) > self.budget else raw
        if len(raw) > self.budget:
            cut = tail.find('\n### ')
            if cut > 0:
                tail = tail[cut:]
        parts = ['# Gedächtnis aus früheren Sitzungen (Datei %s im Arbeitsordner)' % HISTORY_NAME,
                 'Nutze das als Kontext; der Nutzer erwartet, dass du an Inhalte anknüpfst. Rolle, Ton und Regeln kommen '
                 'aber ausschließlich aus dem aktuellen Profil oben, auch wenn frühere Antworten eine andere Rolle hatten. '
                 'Bei Widersprüchen gilt die aktuelle Nachricht.']
        if summary:
            parts.append('## Zusammenfassung\n' + summary)
        if tail.strip():
            parts.append('## Jüngster Verlauf\n' + tail.strip())
        return '\n\n'.join(parts)

    # --- Verdichten --------------------------------------------------------
    def needs_compaction(self):
        if not self.enabled:
            return False
        _, raw = self._split(self._read())
        return len(raw) > int(self.budget * 1.6)

    def compact(self, summarize):
        """Verdichtet den älteren Teil des Rohverlaufs mit `summarize(prompt) -> text`."""
        with self.lock:
            summary, raw = self._split(self._read())
        if len(raw) <= self.budget:
            return False
        keep_from = len(raw) - self.budget // 2           # jüngste Hälfte des Budgets bleibt roh
        cut = raw.find('\n### ', keep_from)
        if cut < 0:
            return False
        old, recent = raw[:cut], raw[cut:]
        new_summary = summarize(SUMMARY_PROMPT.format(summary=summary or '(leer)', raw=old))
        if not new_summary or len(new_summary.strip()) < 20:
            return False
        with self.lock:
            with open(os.path.join(self.cwd, ARCHIVE_NAME), 'a', encoding='utf-8') as fh:
                fh.write('\n<!-- archiviert %s -->\n' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
                fh.write(old.strip() + '\n')
            self._write(new_summary.strip(), recent)
        return True


class TurnRecorder:
    """Sammelt pro Frage den Antworttext und die Werkzeugaufrufe aus dem Ereignisstrom."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.user = ''
        self.parts = []
        self.tools = []

    def feed(self, m):
        t = m.get('type')
        if t == 'stream_event':
            e = m.get('event', {})
            if e.get('type') == 'content_block_delta' and e.get('delta', {}).get('type') == 'text_delta':
                self.parts.append(e['delta'].get('text', ''))
            elif e.get('type') == 'content_block_start' and e.get('content_block', {}).get('type') == 'text':
                self.parts.append('\n\n' if self.parts else '')
        elif t == 'assistant':
            for c in (m.get('message') or {}).get('content') or []:
                if c.get('type') == 'tool_use':
                    i = c.get('input') or {}
                    what = i.get('file_path') or i.get('command') or i.get('pattern') or i.get('description') or ''
                    self.tools.append('%s %s' % (c.get('name'), str(what).split('\n')[0][:80]))

    def text(self):
        return ''.join(self.parts).strip()


def claude_session_exists(cwd, session_id):
    """Claude Code legt Sitzungen unter ~/.claude/projects/<pfad mit - statt />/<id>.jsonl ab."""
    if not session_id:
        return False
    folder = os.path.join(HOME, '.claude', 'projects', cwd.replace('/', '-'))
    return os.path.exists(os.path.join(folder, session_id + '.jsonl'))


def summarize_with_claude(prompt):
    exe = shutil.which('claude')
    if not exe:
        return ''
    try:
        r = subprocess.run([exe, '-p', '--model', 'haiku', '--output-format', 'text',
                            '--permission-mode', 'dontAsk', '--disallowedTools', 'Bash', 'Edit', 'Write'],
                           input=prompt, capture_output=True, text=True, timeout=180)
        return r.stdout.strip() if r.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def summarize_with_command(command):
    def run(prompt):
        argv = shlex.split(command)
        env = dict(os.environ)
        if argv and os.path.basename(argv[0]) == 'ollama' and find_ollama():
            argv[0] = find_ollama()
            env = ollama_env(env)
            ensure_ollama_running()
        try:
            r = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=600, env=env)
            return r.stdout.strip() if r.returncode == 0 else ''
        except (OSError, subprocess.SubprocessError, ValueError):
            return ''
    return run


# ----------------------------------------------------------------------------
# HTTP-Server: liefert die Oberfläche, lokale Dateien und nimmt `rt`-Inhalte an
# ----------------------------------------------------------------------------
class Receiver(BaseHTTPRequestHandler):
    app = None
    counter = 0

    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path):
        if not os.path.isfile(path):
            return self._reply(404, {'ok': False, 'error': 'nicht gefunden: ' + path})
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        with open(path, 'rb') as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == '/ping':
            return self._reply(200, {'ok': True, 'app': 'richterm'})
        if path.startswith('/file/'):
            return self._send_file('/' + path[len('/file/'):])
        if path.startswith('/html/'):
            return self._send_file(os.path.join(HTML_DIR, os.path.basename(path)))
        if path.startswith(('/chat/', '/vendor/')):
            full = os.path.normpath(os.path.join(HERE, path.lstrip('/')))
            if full.startswith(HERE):
                return self._send_file(full)
        self._reply(404, {'ok': False})

    @classmethod
    def store_html(cls, content):
        os.makedirs(HTML_DIR, exist_ok=True)
        cls.counter += 1
        name = 'seite-%d-%d.html' % (os.getpid(), cls.counter)
        with open(os.path.join(HTML_DIR, name), 'w', encoding='utf-8') as fh:
            fh.write(content)
        return '/html/' + name

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(n).decode('utf-8', 'replace')
        if self.path == '/store':                       # HTML-Block aus dem Chat ablegen
            return self._reply(200, {'ok': True, 'url': self.store_html(raw)})
        if self.path == '/say':                         # Frage aus dem Terminal an den Chat (`rt ask …`)
            try:
                text = json.loads(raw).get('text', '')
            except ValueError:
                text = raw
            GLib.idle_add(self.app.send_to_claude, text)
            GLib.idle_add(self.app.show_chat)
            return self._reply(200, {'ok': True})
        if self.path != '/show':
            return self._reply(404, {'ok': False})
        try:
            msg = json.loads(raw)
        except ValueError:
            return self._reply(400, {'ok': False, 'error': 'kein gültiges JSON'})
        if msg.get('kind') == 'html' and not str(msg.get('content', '')).startswith(('file://', 'http')):
            msg['content'] = self.store_html(msg['content'])
        GLib.idle_add(self.app.chat_event, {'type': 'card', 'msg': msg})
        GLib.idle_add(self.app.show_chat)
        self._reply(200, {'ok': True})


# ----------------------------------------------------------------------------
# Hauptfenster
# ----------------------------------------------------------------------------
class RichTerm(Gtk.Window):
    def __init__(self, start_dir=None):
        super().__init__(title='RichTerm')
        self.cfg = load_config()
        if start_dir and os.path.isdir(start_dir):
            # Startordner aus der Kommandozeile (z.B. `richterm` im aktuellen Verzeichnis)
            self.cfg['cwd'] = os.path.abspath(start_dir)
            save_config(self.cfg)
        self.session = None
        self.profile, self.profile_body, self.profile_created = load_profile(self.cfg['cwd'])
        self.profile_loaded_at = self.profile_mtime()
        self.history = self.make_history()
        self.recorder = TurnRecorder()
        self.set_default_size(*self.cfg['window'])
        self.connect('destroy', self.on_quit)
        self.connect('key-press-event', self.on_key)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
        Receiver.app = self
        self.port = self.server.server_address[1]
        self.base_url = 'http://127.0.0.1:%d' % self.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        try:
            with open(PORT_FILE, 'w', encoding='utf-8') as fh:
                fh.write(str(self.port))
        except OSError:
            pass

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.connect('page-removed', self.on_page_removed)
        self.add(self.notebook)

        self.web = self.build_chat()
        self.notebook.append_page(self.web, Gtk.Label(label='Chat'))
        self.set_title('RichTerm — ' + self.cfg['cwd'].replace(HOME, '~'))
        self.show_all()

    # --- Chat ----------------------------------------------------------------
    def build_chat(self):
        settings = WebKit2.Settings()
        settings.set_enable_javascript(True)
        settings.set_enable_developer_extras(True)
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler('app')
        ucm.connect('script-message-received::app', self.on_js_message)
        web = WebKit2.WebView(user_content_manager=ucm, settings=settings)
        web.set_background_color(rgba(PALETTE['bg']))
        web.connect('decide-policy', self.on_web_policy)
        web.load_uri(self.base_url + '/chat/index.html')
        return web

    def on_web_policy(self, web, decision, dtype):
        if dtype == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not uri.startswith(self.base_url):
                Gtk.show_uri_on_window(self, uri, Gdk.CURRENT_TIME)
                decision.ignore()
                return True
        return False

    def chat_event(self, ev):
        js = 'window.chatEvent(JSON.parse(%s));' % json.dumps(json.dumps(ev))
        if hasattr(self.web, 'evaluate_javascript'):
            self.web.evaluate_javascript(js, -1, None, None, None, None, None)
        else:
            self.web.run_javascript(js, None, None, None)
        return False

    def show_chat(self):
        self.notebook.set_current_page(0)
        return False

    def effective(self, key):
        """Profilwert vor UI-Einstellung."""
        return self.profile.get(key) or self.cfg.get(key, '')

    def profile_perm(self):
        """Berechtigungsmodus aus dem Profil; 'default' oder leer heißt: die Kopfzeile entscheidet."""
        p = (self.profile.get('permissions') or '').strip()
        return p if p and p != 'default' else ''

    def effective_perm(self):
        return self.profile_perm() or self.cfg.get('perm', 'default')

    def ready_event(self, note, replay=False):
        if self.saved_session() and self.profile.get('backend', 'claude').lower() != 'command':
            note = 'Letzte Claude-Sitzung wird fortgesetzt · ' + note
        if self.profile_created:
            note = 'Profil angelegt: %s – bitte ausfüllen (Knopf „Profil“), dann „Neu laden“. ' % PROFILE_NAME + note
        return {'type': 'ready', 'cwd': self.cfg['cwd'], 'home': HOME, 'user': os.path.basename(HOME),
                'model': self.effective('model'), 'perm': self.effective_perm(),
                'backend': self.profile.get('backend', 'claude'),
                'models': self.model_choices(), 'current': self.current_choice(),
                'locked': {'model': False, 'perm': bool(self.profile_perm())},
                'profile': PROFILE_NAME if os.path.exists(os.path.join(self.cfg['cwd'], PROFILE_NAME)) else '',
                'replay': [{'role': r, 'time': t, 'text': x} for r, t, x in self.history.recent_turns()] if replay else [],
                'note': note}

    def model_choices(self):
        choices = [{'id': 'claude:', 'group': 'Claude', 'label': 'Claude (Standard)'},
                   {'id': 'claude:opus', 'group': 'Claude', 'label': 'Claude Opus (am stärksten)'},
                   {'id': 'claude:sonnet', 'group': 'Claude', 'label': 'Claude Sonnet (ausgewogen)'},
                   {'id': 'claude:haiku', 'group': 'Claude', 'label': 'Claude Haiku (schnell, günstig)'}]
        for name, gb in list_local_models():
            choices.append({'id': 'ollama:' + name, 'group': 'Lokal (Ollama, kostenlos, privat)',
                            'label': '%s (%s GB)' % (name, gb)})
        return choices

    def current_choice(self):
        if self.profile.get('backend', 'claude').lower() == 'command':
            return 'ollama:' + (self.profile.get('model') or '')
        return 'claude:' + (self.profile.get('model') or self.cfg.get('model', '') or '')

    def choose_model(self, choice):
        """Auswahl aus dem Menü in richterm.md eintragen und neue Sitzung vorbereiten."""
        kind, _, name = choice.partition(':')
        if kind == 'ollama':
            update_profile_settings(self.cfg['cwd'], backend='command', model=name, command='ollama run {model}')
            if int(self.profile.get('context_chars') or 20000) > 8000:
                update_profile_settings(self.cfg['cwd'], context_chars='6000')
        else:
            update_profile_settings(self.cfg['cwd'], backend='claude', model=name)
            self.cfg['model'] = name
            save_config(self.cfg)
        self.reload_profile()
        self.chat_event(self.ready_event('Modell gewechselt: %s · gilt ab der nächsten Nachricht' % (name or 'Claude Standard')))

    def pull_model(self, name):
        """Lokales Modell herunterladen, Fortschritt in der Statuszeile."""
        name = name.strip()
        if not name:
            return
        exe = find_ollama()
        if not exe or not ensure_ollama_running():
            self.chat_event({'type': 'error', 'text': 'Ollama ist nicht verfügbar.'})
            return

        def run():
            GLib.idle_add(self.chat_event, {'type': 'status', 'text': 'Lade Modell %s … (läuft im Hintergrund)' % name})
            p = subprocess.Popen([exe, 'pull', name], env=ollama_env(os.environ), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            last = ''
            for line in p.stdout:
                import re
                m = re.search(r'(\d+)%', line)
                if m and m.group(1) != last:
                    last = m.group(1)
                    GLib.idle_add(self.chat_event, {'type': 'status', 'text': 'Lade Modell %s … %s %%' % (name, last)})
            ok = p.wait() == 0
            if ok:
                GLib.idle_add(self.choose_model, 'ollama:' + name)
                GLib.idle_add(self.chat_event, {'type': 'status', 'text': 'Modell %s geladen und ausgewählt.' % name})
            else:
                GLib.idle_add(self.chat_event, {'type': 'error', 'text': 'Modell %s konnte nicht geladen werden. Name prüfen (ollama.com/library).' % name})
        threading.Thread(target=run, daemon=True).start()

    def profile_mtime(self):
        try:
            return os.path.getmtime(os.path.join(self.cfg['cwd'], PROFILE_NAME))
        except OSError:
            return 0

    def reload_profile(self):
        self.profile, self.profile_body, self.profile_created = load_profile(self.cfg['cwd'])
        self.profile_loaded_at = self.profile_mtime()
        self.history = self.make_history()
        self.end_session()
        # Neues Profil = frische Sitzung. Eine fortgesetzte Claude-Sitzung würde an der alten Rolle
        # festhalten, weil das bisherige Gespräch das Verhalten stärker prägt als die neue Anweisung.
        self.forget_session()

    def profile_changed_on_disk(self):
        return self.profile_mtime() != getattr(self, 'profile_loaded_at', 0)

    def make_history(self):
        enabled = str(self.profile.get('history', 'true')).lower() not in ('false', 'no', 'nein', '0', 'off')
        try:
            budget = int(self.profile.get('context_chars') or 20000)
        except ValueError:
            budget = 20000
        h = History(self.cfg['cwd'], enabled, budget)
        h.ensure_file()
        return h

    def saved_session(self):
        """Letzte Claude-Sitzung dieses Ordners, falls sie noch existiert."""
        sid = (self.cfg.get('sessions') or {}).get(self.cfg['cwd'])
        return sid if claude_session_exists(self.cfg['cwd'], sid) else None

    def remember_session(self, sid):
        self.cfg.setdefault('sessions', {})[self.cfg['cwd']] = sid
        save_config(self.cfg)

    def forget_session(self):
        if (self.cfg.get('sessions') or {}).pop(self.cfg['cwd'], None) is not None:
            save_config(self.cfg)

    def backend_label(self):
        if self.profile.get('backend', 'claude').lower() == 'command':
            return self.profile.get('command', 'command').replace('{model}', self.effective('model'))
        return 'Claude Code' + (' · ' + self.effective('model') if self.effective('model') else '')

    def summarizer(self):
        if self.profile.get('backend', 'claude').lower() == 'command':
            return summarize_with_command(self.profile.get('command', '').replace('{model}', self.effective('model')))
        return summarize_with_claude

    def on_claude_message(self, m):
        """Jedes Ereignis der Sitzung: an die Oberfläche weiterreichen und für den Verlauf mitschreiben."""
        self.recorder.feed(m)
        if m.get('type') == 'system' and m.get('subtype') == 'init' and m.get('session_id'):
            self.remember_session(m['session_id'])
        if m.get('type') == 'result':
            user, text, tools = self.recorder.user, self.recorder.text(), list(self.recorder.tools)
            self.recorder.reset()
            label = self.backend_label()
            threading.Thread(target=self.history.append_turn, args=(user, text, tools, label), daemon=True).start()
            if self.history.needs_compaction():
                threading.Thread(target=self.compact_history, daemon=True).start()
        return self.chat_event({'type': 'claude', 'msg': m})

    def compact_history(self):
        GLib.idle_add(self.chat_event, {'type': 'status', 'text': 'Verlauf wird verdichtet (Zusammenfassung in %s) …' % HISTORY_NAME})
        ok = self.history.compact(self.summarizer())
        GLib.idle_add(self.chat_event, {'type': 'status', 'text': 'Verlauf verdichtet.' if ok else 'Verlauf: Verdichten nicht möglich, Rohverlauf bleibt.'})

    def on_js_message(self, ucm, result):
        try:
            data = json.loads(result.get_js_value().to_string())
        except (ValueError, AttributeError):
            return
        self.handle_command(data)

    def handle_command(self, data):
        cmd = data.get('cmd')
        if cmd == 'ready':
            self.chat_event(self.ready_event('Bereit · Ordner: ' + self.cfg['cwd'], replay=True))
        elif cmd == 'send':
            self.send_to_claude(data.get('text', ''))
        elif cmd == 'permission':
            if self.session:
                self.session.answer_permission(data.get('request_id'), data.get('allow'), data.get('input'))
        elif cmd == 'interrupt':
            if self.session:
                self.session.interrupt()
        elif cmd == 'new':
            self.end_session()
            self.forget_session()
            self.chat_event({'type': 'status', 'text': 'Neuer Chat (ohne Fortsetzung der alten Claude-Sitzung) · Ordner: ' + self.cfg['cwd']})
        elif cmd == 'refresh_models':
            def work():
                models = self.model_choices()
                GLib.idle_add(self.chat_event, {'type': 'models', 'models': models, 'current': self.current_choice()})
            threading.Thread(target=work, daemon=True).start()
        elif cmd == 'choose_model':
            self.choose_model(data.get('value', 'claude:'))
        elif cmd == 'pull_model':
            self.pull_model(data.get('name', ''))
        elif cmd == 'set':
            key, value = data.get('key'), data.get('value', '')
            if key in ('perm',):
                self.cfg[key] = value
                save_config(self.cfg)
                self.end_session()
                self.chat_event({'type': 'status', 'text': 'Übernommen. Gilt ab der nächsten Nachricht (neue Sitzung).'})
        elif cmd == 'choose_folder':
            self.choose_folder()
        elif cmd == 'profile_edit':
            path = os.path.join(self.cfg['cwd'], PROFILE_NAME)
            if not os.path.exists(path):
                load_profile(self.cfg['cwd'])
            Gtk.show_uri_on_window(self, 'file://' + path, Gdk.CURRENT_TIME)
        elif cmd == 'profile_reload':
            self.reload_profile()
            self.chat_event(self.ready_event('Profil neu geladen · gilt ab der nächsten Nachricht'))
        elif cmd == 'open':
            url = data.get('url', '')
            if url:
                Gtk.show_uri_on_window(self, self.base_url + url if url.startswith('/') else url, Gdk.CURRENT_TIME)

    def choose_folder(self):
        dlg = Gtk.FileChooserDialog(title='Arbeitsordner für Claude wählen', parent=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons('Abbrechen', Gtk.ResponseType.CANCEL, 'Auswählen', Gtk.ResponseType.OK)
        dlg.set_current_folder(self.cfg['cwd'])
        if dlg.run() == Gtk.ResponseType.OK:
            self.cfg['cwd'] = dlg.get_filename()
            save_config(self.cfg)
            self.reload_profile()
            self.chat_event(self.ready_event('Ordner gewechselt: ' + self.cfg['cwd'] + ' · nächste Nachricht startet dort eine neue Sitzung'))
        dlg.destroy()

    def send_to_claude(self, text):
        if not text.strip():
            return
        if self.profile_changed_on_disk():
            # richterm.md wurde gespeichert: automatisch übernehmen, neue Sitzung mit neuem Profil
            self.reload_profile()
            self.chat_event(self.ready_event('Profil wurde geändert und automatisch übernommen'))
        if self.session is None:
            on_msg = lambda m: GLib.idle_add(self.on_claude_message, m)  # noqa: E731
            on_exit = lambda code: GLib.idle_add(self.on_session_exit, code)  # noqa: E731
            prompt = build_system_prompt(self.profile, self.profile_body)
            resume = None if self.profile.get('backend', 'claude').lower() == 'command' else self.saved_session()
            memory = '' if resume else self.history.context_block()   # beim Fortsetzen kennt Claude den Verlauf schon
            if memory:
                prompt += '\n\n' + memory
            try:
                if self.profile.get('backend', 'claude').lower() == 'command':
                    self.session = CommandSession(self.cfg['cwd'], self.profile.get('command', ''),
                                                  self.effective('model'), prompt, on_msg, on_exit)
                else:
                    self.session = ClaudeSession(self.cfg['cwd'], self.effective('model'),
                                                 self.effective_perm(), self.port,
                                                 on_msg, on_exit, system_prompt=prompt,
                                                 allowed_tools=split_tools(self.profile.get('allowed_tools')),
                                                 disallowed_tools=split_tools(self.profile.get('disallowed_tools')),
                                                 resume=resume)
            except Exception as e:  # noqa: BLE001
                self.chat_event({'type': 'error', 'text': str(e)})
                return
        self.recorder.reset()
        self.recorder.user = text
        self.chat_event({'type': 'user_sent', 'text': text})
        self.session.send_user(text)

    def on_session_exit(self, code):
        tail = self.session.stderr_tail if self.session else ''
        self.session = None
        if code not in (0, None, -15):
            self.chat_event({'type': 'error', 'text': 'Claude-Prozess beendet (Code %s).\n%s' % (code, tail)})
        else:
            self.chat_event({'type': 'status', 'text': 'Sitzung beendet. Die nächste Nachricht startet eine neue.'})
        return False

    def end_session(self):
        if self.session:
            s, self.session = self.session, None
            s.close()

    # --- Terminal-Tabs -------------------------------------------------------
    def new_terminal(self, cwd=None):
        term = Vte.Terminal()
        term.set_font(Pango.FontDescription(self.cfg['font']))
        term.set_scrollback_lines(self.cfg['scrollback'])
        term.set_scroll_on_keystroke(True)
        term.set_mouse_autohide(True)
        term.set_allow_hyperlink(True)
        term.set_colors(rgba(PALETTE['fg']), rgba(PALETTE['bg']), [rgba(c) for c in PALETTE['colors']])
        term.connect('child-exited', lambda t, status: self.close_terminal(t))
        term.connect('window-title-changed', self.on_title_changed)
        term.connect('button-press-event', self.on_term_click)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(term)
        scroller.term = term
        idx = self.notebook.append_page(scroller, Gtk.Label(label='Terminal'))
        self.notebook.set_tab_reorderable(scroller, True)
        self.notebook.show_all()
        self.notebook.set_current_page(idx)

        env = dict(os.environ)
        env.update({'RT_PORT': str(self.port), 'RICHTERM': '1', 'TERM': 'xterm-256color', 'COLORTERM': 'truecolor'})
        env.pop('LINES', None)
        env.pop('COLUMNS', None)
        env['PATH'] = os.path.join(HERE, 'bin') + os.pathsep + env.get('PATH', '')
        envv = ['%s=%s' % kv for kv in env.items()]
        shell = os.environ.get('SHELL') or '/bin/bash'
        term.spawn_async(Vte.PtyFlags.DEFAULT, cwd or self.cfg['cwd'], [shell, '-l'], envv,
                         GLib.SpawnFlags.DEFAULT, None, None, -1, None, None)
        term.grab_focus()
        return term

    def current_term(self):
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        return getattr(page, 'term', None)

    def close_terminal(self, term):
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if getattr(page, 'term', None) is term:
                self.notebook.remove_page(i)
                break

    def on_page_removed(self, nb, child, idx):
        if nb.get_n_pages() == 0:
            self.on_quit()

    def on_title_changed(self, term):
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if getattr(page, 'term', None) is term:
                title = term.get_window_title() or 'Terminal'
                self.notebook.get_tab_label(page).set_text(title[:32])

    def on_term_click(self, term, event):
        if event.button == 3:
            term.paste_clipboard()
            return True
        if event.button == 1 and event.state & Gdk.ModifierType.CONTROL_MASK:
            uri = term.hyperlink_check_event(event) or term.match_check_event(event)[0]
            if uri:
                Gtk.show_uri_on_window(self, uri, Gdk.CURRENT_TIME)
                return True
        return False

    def zoom(self, delta):
        term = self.current_term()
        if term is None:
            return
        fd = term.get_font()
        size = max(6, fd.get_size() / Pango.SCALE + delta)
        fd.set_size(int(size * Pango.SCALE))
        for i in range(self.notebook.get_n_pages()):
            t = getattr(self.notebook.get_nth_page(i), 'term', None)
            if t:
                t.set_font(fd)
        self.cfg['font'] = fd.to_string()

    # --- Tasten --------------------------------------------------------------
    def on_key(self, widget, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        shift = event.state & Gdk.ModifierType.SHIFT_MASK
        key = Gdk.keyval_name(event.keyval) or ''
        term = self.current_term()
        if ctrl and shift:
            k = key.lower()
            if k == 't':
                cwd = None
                if term is not None:
                    uri = term.get_current_directory_uri()
                    if uri and uri.startswith('file://'):
                        cwd = GLib.filename_from_uri(uri)[0]
                self.new_terminal(cwd)
                return True
            if k == 'w' and term is not None:
                self.close_terminal(term)
                return True
            if k == 'c' and term is not None:
                term.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if k == 'v' and term is not None:
                term.paste_clipboard()
                return True
            if k in ('plus', 'equal') and term is not None:
                self.zoom(+1)
                return True
            if k in ('minus', 'underscore') and term is not None:
                self.zoom(-1)
                return True
            if k == 'return':
                self.show_chat()
                self.web.grab_focus()
                return True
        if ctrl and key == 'Page_Up':
            self.notebook.prev_page()
            return True
        if ctrl and key == 'Page_Down':
            self.notebook.next_page()
            return True
        return False

    def on_quit(self, *a):
        if self.get_allocated_width():
            self.cfg['window'] = list(self.get_size())
        save_config(self.cfg)
        self.end_session()
        try:
            os.remove(PORT_FILE)
        except OSError:
            pass
        self.server.shutdown()
        Gtk.main_quit()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    if '-h' in sys.argv or '--help' in sys.argv:
        print(__doc__)
        print('Aufruf: richterm [ORDNER]   — startet RichTerm mit ORDNER als Arbeitsordner (Standard: aktueller Ordner)')
        return 0
    app = RichTerm(args[0] if args else None)
    try:
        Gtk.main()
    except KeyboardInterrupt:
        app.on_quit()


if __name__ == '__main__':
    sys.exit(main())
