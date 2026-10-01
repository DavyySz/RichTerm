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

Gtk = Gdk = GLib = Vte = Pango = WebKit2 = None


def load_gtk():
    """GTK/VTE/WebKit nur im nativen Modus laden (im Browser-Modus nicht nötig, z.B. auf macOS)."""
    global Gtk, Gdk, GLib, Vte, Pango, WebKit2
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Vte', '2.91')
    gi.require_version('WebKit2', '4.1')
    from gi.repository import Gtk as _Gtk, Gdk as _Gdk, GLib as _GLib, Vte as _Vte, Pango as _Pango, WebKit2 as _WebKit2
    Gtk, Gdk, GLib, Vte, Pango, WebKit2 = _Gtk, _Gdk, _GLib, _Vte, _Pango, _WebKit2


def _xdg(key, default):
    v = os.environ.get(key)
    return v if v else os.path.join(HOME, default)

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser('~')
CONFIG_PATH = os.path.join(_xdg('XDG_CONFIG_HOME', '.config'), 'richterm.json')
PORT_FILE = os.path.join(os.environ.get('XDG_RUNTIME_DIR') or _xdg('XDG_CACHE_HOME', '.cache'), 'richterm.port')
HTML_DIR = os.path.join(_xdg('XDG_CACHE_HOME', '.cache'), 'richterm', 'html')

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
- Diagramme, Flussdiagramme, Ablauf- und Klassendiagramme IMMER als ```mermaid-Block (wird gezeichnet); nie als
  ASCII-Art, Graphviz/DOT oder PlantUML. Mermaid-Regeln: `flowchart LR`/`TD`, `sequenceDiagram`, `classDiagram`,
  `stateDiagram-v2`, `pie`, `gantt`; Knoten-IDs nur Buchstaben/Ziffern; Beschriftungen mit Sonderzeichen oder
  Klammern in doppelte Anführungszeichen, z.B. A["Input (25)"] --> B["Hidden 64"]; keine Markdown-Formatierung
  und kein LaTeX in Beschriftungen; Zeilenumbruch mit <br/>.
- ```svg-Blöcke werden inline angezeigt (für Skizzen und geometrische Zeichnungen).
- ```html-Blöcke werden als Live-Vorschau mit laufendem JavaScript angezeigt. Nutze sie für Animationen,
  interaktive Visualisierungen, Canvas-Grafiken und Simulationen (vollständige HTML-Dokumente mit eigenem
  Script, keine externen Ressourcen; Höhe per Kommentar <!-- height: 500 --> steuerbar).
- Bilder, die du mit matplotlib o.ä. als Datei erzeugst, zeigst du mit ![Beschreibung](pfad/zur/datei.png).
- Tabellen, Überschriften und Codeblöcke wie üblich in Markdown.
Liegen Unterlagen im Ordner rag/, bekommst du zu jeder Frage passende Auszüge daraus mitgeliefert; stütze
dich dann darauf und nenne die Quelle (Datei, Seite).
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
    w = cfg.get('window') or [0, 0]
    if not (isinstance(w, list) and len(w) == 2 and w[0] >= 700 and w[1] >= 450):
        cfg['window'] = list(DEFAULTS['window'])      # kaputte/zu kleine Werte verwerfen
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

    def send_user(self, content):
        self._write({'type': 'user', 'message': {'role': 'user', 'content': content}})

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


class OllamaSession:
    """Lokales Modell über die Ollama-API (/api/chat): echtes Streaming, saubere Rollen,
    Verlauf innerhalb der Sitzung. Wird automatisch statt `ollama run` benutzt."""
    name = 'Ollama'

    def __init__(self, cwd, model, system_prompt, on_message, on_exit):
        self.cwd = cwd
        self.model = model
        self.on_message = on_message
        self.on_exit = on_exit
        self.messages = [{'role': 'system', 'content': system_prompt}]
        self.history = []
        self.stderr_tail = ''
        self.resp = None
        self.stop = False
        exe = find_ollama()
        self.host = OLLAMA_HOST if exe == OLLAMA_LOCAL else os.environ.get('OLLAMA_HOST', '127.0.0.1:11434')

    def send_user(self, content):
        text, images = content_to_text_and_images(content)
        msg = {'role': 'user', 'content': text}
        if images:
            msg['images'] = images            # base64, für multimodale Modelle (z.B. llava, qwen2.5vl)
        self.messages.append(msg)
        self.history.append(('Nutzer', text))
        self.stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import urllib.request
        self.on_message({'type': 'system', 'subtype': 'init', 'model': 'ollama ' + self.model, 'cwd': self.cwd})
        if not ensure_ollama_running():
            self.on_message({'type': 'result', 'is_error': True,
                             'result': 'Ollama ist nicht verfügbar (erwartet unter %s).' % OLLAMA_LOCAL})
            return
        # Alle physischen Kerne nutzen: Ollama erkennt bei Hybrid-CPUs (z.B. i5-1235U) sonst nur die
        # Performance-Kerne und rechnet auf 2 statt 10 Kernen.
        threads = os.cpu_count() or 4
        try:
            import re as _re
            cores = set()
            for d in os.listdir('/sys/devices/system/cpu'):
                if _re.fullmatch(r'cpu\d+', d):
                    try:
                        with open('/sys/devices/system/cpu/%s/topology/core_cpus_list' % d) as fh:
                            cores.add(fh.read().strip())
                    except OSError:
                        pass
            if cores:
                threads = len(cores)
        except OSError:
            pass
        body = json.dumps({'model': self.model, 'messages': self.messages, 'stream': True,
                           'options': {'num_ctx': 8192, 'num_thread': threads}}).encode('utf-8')
        self.on_message({'type': 'system', 'subtype': 'status', 'status': 'ollama_thinking',
                         'text': 'Lokales Modell %s liest die Anfrage (%d Kerne) …' % (self.model, threads)})
        req = urllib.request.Request('http://%s/api/chat' % self.host, data=body,
                                     headers={'Content-Type': 'application/json'})
        ev = lambda e: self.on_message({'type': 'stream_event', 'event': e})  # noqa: E731
        ev({'type': 'message_start'})
        ev({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})
        answer = []
        try:
            self.resp = urllib.request.urlopen(req, timeout=600)
            for line in self.resp:
                if self.stop:
                    break
                try:
                    chunk = json.loads(line.decode('utf-8'))
                except ValueError:
                    continue
                piece = (chunk.get('message') or {}).get('content', '')
                if piece:
                    answer.append(piece)
                    ev({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': piece}})
                if chunk.get('error'):
                    answer.append('\n\nFehler: ' + chunk['error'])
                if chunk.get('done'):
                    break
        except Exception as e:  # noqa: BLE001
            if not self.stop:
                ev({'type': 'content_block_stop', 'index': 0})
                self.on_message({'type': 'result', 'is_error': True,
                                 'result': 'Ollama-Fehler: %s (Modell %s geladen? `ollama list`)' % (e, self.model)})
                return
        ev({'type': 'content_block_stop', 'index': 0})
        full = ''.join(answer).strip()
        self.messages.append({'role': 'assistant', 'content': full})
        self.history.append(('Assistent', full))
        self.on_message({'type': 'result', 'is_error': False})

    def answer_permission(self, *a):
        pass

    def interrupt(self):
        self.stop = True
        try:
            if self.resp:
                self.resp.close()
        except Exception:  # noqa: BLE001
            pass
        # Ollama rechnet sonst weiter, bis es das erste Wort senden will: Modell sofort entladen,
        # das bricht die laufende Berechnung ab (wird beim nächsten Mal neu geladen, ~10-30 s).
        threading.Thread(target=self._unload, daemon=True).start()

    def _unload(self):
        import urllib.request
        try:
            req = urllib.request.Request('http://%s/api/generate' % self.host,
                                         data=json.dumps({'model': self.model, 'keep_alive': 0}).encode(),
                                         headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:  # noqa: BLE001
            pass

    def close(self):
        self.interrupt()


def content_to_text_and_images(content):
    """Nachrichteninhalt (Text oder Claude-Blockliste) -> (Text, [base64-Bilder])."""
    if isinstance(content, str):
        return content, []
    text, images = [], []
    for block in content:
        if block.get('type') == 'text':
            text.append(block.get('text', ''))
        elif block.get('type') == 'image':
            images.append(block.get('source', {}).get('data', ''))
    return '\n'.join(text), images


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

    def send_user(self, content):
        text, _ = content_to_text_and_images(content)
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
# RAG: Unterlagen im Ordner rag/ des Arbeitsordners. Dateien werden in Abschnitte zerlegt
# (PDF seitenweise) und per BM25 durchsucht; die passendsten Abschnitte bekommt das Modell
# zu jeder Frage mit Quellenangabe. Ohne Zusatzbibliotheken; PDF-Text über pdftotext.
# ----------------------------------------------------------------------------
RAG_DIRNAME = 'rag'
RAG_EXTS = {'.pdf', '.md', '.txt', '.markdown', '.html', '.htm', '.docx', '.csv', '.tex', '.json',
            '.py', '.java', '.js', '.ts', '.c', '.cpp', '.h', '.rs', '.go', '.sh', '.sql', '.yaml', '.yml', '.rst'}
RAG_STOP = set("""der die das und oder aber ist sind war waren ein eine einer eines einem einen nicht mit von zu
im in am an auf für den dem des als auch wie bei aus nach über um es er sie ich du wir ihr man kann
dass was wenn dann noch nur so hier da sich dies diese dieser dieses the a an and or of to in is are
was were for with on at by this that it be as from""".split())


def _tokens(text):
    import re
    return [t for t in re.findall(r'[a-zA-ZäöüÄÖÜß0-9_]+', text.lower()) if len(t) > 2 and t not in RAG_STOP]


def _extract_text(path):
    """-> Liste von (seitenlabel, text). Unbekanntes Format: leer."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == '.pdf':
            exe = shutil.which('pdftotext')
            if not exe:
                return [('', '[PDF-Text konnte nicht gelesen werden: pdftotext fehlt (Paket poppler-utils / poppler)]')]
            out = subprocess.run([exe, '-layout', '-enc', 'UTF-8', path, '-'], capture_output=True, text=True, timeout=120).stdout
            return [('S. %d' % (i + 1), p) for i, p in enumerate(out.split('\f')) if p.strip()]
        if ext == '.docx':
            import re
            import zipfile
            with zipfile.ZipFile(path) as z:
                xml = z.read('word/document.xml').decode('utf-8', 'replace')
            xml = re.sub(r'</w:p>', '\n', xml)
            return [('', re.sub(r'<[^>]+>', '', xml))]
        with open(path, encoding='utf-8', errors='replace') as fh:
            text = fh.read()
        if ext in ('.html', '.htm'):
            import re
            text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', text, flags=re.S | re.I)
            text = re.sub(r'<[^>]+>', ' ', text)
        return [('', text)]
    except Exception as e:  # noqa: BLE001
        return [('', '[konnte nicht gelesen werden: %s]' % e)]


def _chunk(text, size=900, overlap=150):
    text = ' '.join(text.split())
    if len(text) <= size:
        return [text] if text else []
    chunks, i = [], 0
    while i < len(text):
        end = min(len(text), i + size)
        cut = text.rfind('. ', i + size // 2, end)
        if cut == -1 or end == len(text):
            cut = end
        else:
            cut += 1
        chunks.append(text[i:cut].strip())
        if cut >= len(text):
            break
        i = max(cut - overlap, i + 1)
    return [c for c in chunks if c]


EMBED_MODEL = 'embeddinggemma'          # mehrsprachig, 622 MB, trennt deutsche Fragen sauber
EMBED_QUERY_PREFIX = 'task: search result | query: '
EMBED_DOC_PREFIX = 'title: none | text: '
EMBED_MIN_SIM = 0.22                    # darunter gilt ein Abschnitt als nicht passend


def ollama_host():
    exe = find_ollama()
    return OLLAMA_HOST if exe == OLLAMA_LOCAL else os.environ.get('OLLAMA_HOST', '127.0.0.1:11434')


def embed_texts(texts, timeout=300, query=False):
    """Einbettungen über Ollama (EMBED_MODEL). -> Liste von Vektoren oder None, wenn nicht verfügbar."""
    import urllib.request
    if not texts or not find_ollama():
        return None
    texts = [(EMBED_QUERY_PREFIX if query else EMBED_DOC_PREFIX) + t for t in texts]
    try:
        if not ensure_ollama_running(timeout=8):
            return None
        body = json.dumps({'model': EMBED_MODEL, 'input': texts}).encode('utf-8')
        req = urllib.request.Request('http://%s/api/embed' % ollama_host(), data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            vecs = json.load(resp).get('embeddings')
        if not vecs or len(vecs) != len(texts):
            return None
        return [[round(x, 5) for x in v] for v in vecs]
    except Exception:  # noqa: BLE001
        return None


def embed_model_available():
    return any(name.split(':')[0] == EMBED_MODEL for name, _ in list_local_models())


def _cosine(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class Rag:
    def __init__(self, cwd, dirname=RAG_DIRNAME, top_k=6, strict=False):
        self.dir = os.path.join(cwd, dirname)
        self.index_path = os.path.join(self.dir, '.richterm-index.json')
        self.top_k = top_k
        self.strict = strict
        self.lock = threading.Lock()
        self.docs = {}            # datei -> {'mtime':..., 'chunks':[{'page':..,'text':..,'vec':[...]}]}
        self.use_embeddings = None   # None = noch nicht geprüft
        self._load_index()

    def exists(self):
        return os.path.isdir(self.dir)

    def _load_index(self):
        try:
            with open(self.index_path, encoding='utf-8') as fh:
                data = json.load(fh)
            if data.get('_embed_model') != EMBED_MODEL:        # anderes Suchmodell: Vektoren neu berechnen
                for d in data.values():
                    if isinstance(d, dict):
                        for c in d.get('chunks', []):
                            c.pop('vec', None)
            data.pop('_embed_model', None)
            self.docs = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.docs = {}

    def _save_index(self):
        try:
            data = dict(self.docs)
            data['_embed_model'] = EMBED_MODEL
            with open(self.index_path, 'w', encoding='utf-8') as fh:
                json.dump(data, fh)
        except OSError:
            pass

    def files(self):
        out = []
        for root, _dirs, names in os.walk(self.dir):
            for n in names:
                if n.startswith('.') or n == 'LIES-MICH.txt':
                    continue
                if os.path.splitext(n)[1].lower() in RAG_EXTS:
                    out.append(os.path.join(root, n))
        return sorted(out)

    def refresh(self, embed=True, progress=None):
        """Index mit dem Ordner abgleichen. embed=False: nur Text (schnell, für die laufende Frage);
        die Einbettungen rechnet der Hintergrund nach. -> (anzahl dateien, anzahl abschnitte, geändert?)"""
        if not self.exists():
            return 0, 0, False
        changed = False
        with self.lock:
            present = {}
            for path in self.files():
                rel = os.path.relpath(path, self.dir)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                present[rel] = mtime
                if rel in self.docs and self.docs[rel].get('mtime') == mtime:
                    continue
                chunks = []
                for page, text in _extract_text(path):
                    for c in _chunk(text):
                        chunks.append({'page': page, 'text': c})
                self.docs[rel] = {'mtime': mtime, 'chunks': chunks}
                changed = True
            for rel in list(self.docs):
                if rel not in present:
                    del self.docs[rel]
                    changed = True
            # Einbettungen für Abschnitte ohne Vektor nachholen (semantische Suche), falls das Modell da ist
            if changed:
                self._save_index()
            n_chunks = sum(len(d['chunks']) for d in self.docs.values())
        if embed:
            changed = self.embed_missing(progress) or changed
        return len(self.docs), n_chunks, changed

    def embed_missing(self, progress=None):
        """Vektoren für Abschnitte ohne Einbettung nachrechnen (Hintergrund). -> etwas geändert?"""
        if self.use_embeddings is None:
            self.use_embeddings = embed_model_available()
        if not self.use_embeddings:
            return False
        with self.lock:
            todo = [c for d in self.docs.values() for c in d['chunks'] if 'vec' not in c]
            total = sum(len(d['chunks']) for d in self.docs.values())
        if not todo:
            return False
        done = total - len(todo)
        for i in range(0, len(todo), 16):
            batch = todo[i:i + 16]
            vecs = embed_texts([c['text'] for c in batch])
            if not vecs:
                break
            with self.lock:
                for c, v in zip(batch, vecs):
                    c['vec'] = v
                done += len(batch)
                if i % 64 == 0 or done == total:
                    self._save_index()
            if progress:
                progress(done, total)
        with self.lock:
            self._save_index()
        return True

    @staticmethod
    def parse_scope(question):
        """'rag pfad/zu/ordner' oder 'rag pfad/datei.pdf' in der Frage -> (bereinigte frage, [pfade]).
        Ein Ordner wird samt Unterordnern durchsucht, eine Datei nur selbst. Mehrere Angaben möglich."""
        import re
        scopes = []

        def grab(m):
            scopes.append(m.group(1).strip('/'))
            return ' '
        cleaned = re.sub(r'(?:^|(?<=\s))rag[: ]+([\w][\w./\-]*)', grab, question, flags=re.I)
        return ' '.join(cleaned.split()), scopes

    def _in_scope(self, rel, scopes):
        if not scopes:
            return True
        r = rel.lower()
        for sc in scopes:
            sc = sc.lower()
            if r == sc or r.startswith(sc + '/') or os.path.splitext(r)[0] == sc:
                return True
            # Ordner- oder Dateiname ohne vollständigen Pfad (z.B. nur 'mcts' oder 'lern.pdf')
            parts = r.split('/')
            if sc in parts or os.path.basename(r) == sc or os.path.splitext(os.path.basename(r))[0] == sc:
                return True
        return False

    def search(self, query, k=None, scopes=None):
        """Hybride Suche: BM25 (Stichwörter) + Einbettungen (Bedeutung), zusammengeführt per Reciprocal Rank Fusion.
        scopes: nur diese Ordner (mit Unterordnern) bzw. Dateien durchsuchen. -> [(score, datei, seite, text)]"""
        k = k or self.top_k
        with self.lock:
            entries = [(rel, c['page'], c['text'], c.get('vec')) for rel, d in self.docs.items()
                       if self._in_scope(rel, scopes) for c in d['chunks']]
        if not entries:
            return []
        bm = self._bm25(query, [(r, p, t) for r, p, t, _ in entries])
        ranks = {}
        if self.use_embeddings is None:
            self.use_embeddings = embed_model_available()
        for rank, (sc, i) in enumerate(bm):
            ranks[i] = ranks.get(i, 0) + 1.0 / (60 + rank)
        if self.use_embeddings and any(v for _, _, _, v in entries):
            qv = embed_texts([query], query=True)
            if qv:
                sims = sorted(((_cosine(qv[0], v), i) for i, (_, _, _, v) in enumerate(entries) if v), reverse=True)
                for rank, (sim, i) in enumerate(sims[:max(k * 4, 20)]):
                    if sim >= EMBED_MIN_SIM:
                        ranks[i] = ranks.get(i, 0) + 1.0 / (60 + rank)
        best = sorted(ranks.items(), key=lambda x: -x[1])[:k]
        return [(sc, entries[i][0], entries[i][1], entries[i][2]) for i, sc in best]

    def full_text(self, name, max_chars):
        """Ganze Datei (per @name angefordert): -> (label, text) oder None"""
        name = name.lower().lstrip('@')
        with self.lock:
            for rel, d in self.docs.items():
                base = os.path.basename(rel).lower()
                if base == name or base.startswith(name) or rel.lower() == name:
                    parts = []
                    for c in d['chunks']:
                        parts.append(('[%s] ' % c['page'] if c['page'] else '') + c['text'])
                    text = '\n\n'.join(parts)
                    if len(text) > max_chars:
                        text = text[:max_chars] + '\n\n[… gekürzt, Datei ist länger …]'
                    return rel, text
        return None

    def _bm25(self, query, entries):
        import math
        q = _tokens(query)
        if not q:
            return []
        toks = [_tokens(t) for _, _, t in entries]
        n = len(entries)
        avgdl = sum(len(t) for t in toks) / n
        df = {}
        for t in toks:
            for w in set(t):
                df[w] = df.get(w, 0) + 1
        scores = []
        for i, t in enumerate(toks):
            if not t:
                continue
            tf = {}
            for w in t:
                tf[w] = tf.get(w, 0) + 1
            score = 0.0
            for w in set(q):
                if w not in tf:
                    continue
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                f = tf[w]
                score += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(t) / avgdl))
            if score > 0:
                scores.append((score, i))
        scores.sort(reverse=True)
        return scores[:max(self.top_k * 4, 20)]

    def context_for(self, question, max_full_chars=60000):
        """-> (kontextblock für das modell, [quellenliste für die anzeige], bereinigte frage)"""
        import re
        question, scopes = self.parse_scope(question)
        parts, sources = [], []
        if scopes:
            sources.append('Suchbereich: ' + ', '.join(scopes))
        # @datei.pdf in der Frage: ganze Datei mitgeben
        for name in re.findall(r'@([\w][\w.\-]*)', question):
            ft = self.full_text(name, max_full_chars)
            if ft:
                rel, text = ft
                parts.append('[Datei %s, vollständig]\n%s' % (rel, text))
                sources.append(rel + ' (ganz)')
        hits = self.search(question, scopes=scopes)
        for n, (sc, rel, page, text) in enumerate(hits, 1):
            label = rel + (' ' + page if page else '')
            if any(src.startswith(rel + ' (ganz)') for src in sources):
                continue
            parts.append('[%d] %s\n%s' % (n, label, text))
            if label not in sources:
                sources.append(label)
        if not parts:
            if self.strict:
                return ('Zu dieser Frage wurden KEINE passenden Stellen in den Unterlagen (Ordner rag/) gefunden. '
                        'Strenger Modus: Antworte, dass die Unterlagen dazu nichts enthalten, und beantworte die Frage '
                        'nicht aus eigenem Wissen (außer der Nutzer bittet ausdrücklich darum).'), sources, question
            return '', sources if scopes else [], question
        if self.strict:
            rule = ('Strenger Modus: Beantworte die Frage AUSSCHLIESSLICH aus diesen Auszügen. Jede Aussage bekommt eine '
                    'Quellenangabe (z.B. [2] oder "laut vorlesung_03.pdf S. 4"). Steht etwas nicht in den Auszügen, '
                    'sag ausdrücklich "Dazu steht nichts in den Unterlagen" statt aus eigenem Wissen zu ergänzen.')
        else:
            rule = ('Nutze sie als Hauptquelle und zitiere die Quelle (z.B. [1] oder "laut vorlesung_03.pdf S. 4"). '
                    'Ergänzt du etwas aus eigenem Wissen, kennzeichne es als solches. Sag es, wenn die Unterlagen die '
                    'Frage nicht abdecken.')
        block = 'Auszüge aus den Unterlagen des Nutzers (Ordner rag/). ' + rule + '\n\n' + '\n\n'.join(parts)
        return block, sources, question

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
        if path == '/events':                               # Browser-Modus: Ereignisstrom (Server-Sent Events)
            return self._serve_events()
        if path.startswith('/file/'):
            return self._send_file('/' + path[len('/file/'):])
        if path.startswith('/html/'):
            return self._send_file(os.path.join(HTML_DIR, os.path.basename(path)))
        if path.startswith(('/chat/', '/vendor/')):
            full = os.path.normpath(os.path.join(HERE, path.lstrip('/')))
            if full.startswith(HERE):
                return self._send_file(full)
        self._reply(404, {'ok': False})

    def _serve_events(self):
        import queue
        q = queue.Queue()
        self.app.add_listener(q)
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            self.wfile.write(b': verbunden\n\n')
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b': ping\n\n')
                    self.wfile.flush()
                    continue
                self.wfile.write(('data: ' + json.dumps(ev) + '\n\n').encode('utf-8'))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.remove_listener(q)

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
        if self.path == '/cmd':                         # Browser-Modus: Befehle der Oberfläche
            try:
                data = json.loads(raw)
            except ValueError:
                return self._reply(400, {'ok': False})
            self.app.ui(self.app.handle_command, data)
            return self._reply(200, {'ok': True})
        if self.path == '/say':                         # Frage aus dem Terminal an den Chat (`rt ask …`)
            try:
                text = json.loads(raw).get('text', '')
            except ValueError:
                text = raw
            self.app.ui(self.app.send_to_claude, text)
            self.app.ui(self.app.show_chat)
            return self._reply(200, {'ok': True})
        if self.path != '/show':
            return self._reply(404, {'ok': False})
        try:
            msg = json.loads(raw)
        except ValueError:
            return self._reply(400, {'ok': False, 'error': 'kein gültiges JSON'})
        if msg.get('kind') == 'html' and not str(msg.get('content', '')).startswith(('file://', 'http')):
            msg['content'] = self.store_html(msg['content'])
        self.app.ui(self.app.chat_event, {'type': 'card', 'msg': msg})
        self.app.ui(self.app.show_chat)
        self._reply(200, {'ok': True})


# ----------------------------------------------------------------------------
# Hauptfenster
# ----------------------------------------------------------------------------
class Core:
    """Alles, was nicht an der Oberfläche hängt: Konfiguration, Profil, Verlauf, Sitzungen, Befehle, Server.
    Die Oberfläche (GTK-Fenster oder Browser) liefert: ui(), chat_event(), show_chat(), choose_folder(),
    open_uri(), set_clipboard(), edit_file()."""

    def init_core(self, start_dir=None):
        self.cfg = load_config()
        if start_dir and os.path.isdir(start_dir):
            # Startordner aus der Kommandozeile (z.B. `richterm` im aktuellen Verzeichnis)
            self.cfg['cwd'] = os.path.abspath(start_dir)
            save_config(self.cfg)
        self.session = None
        self.listeners = []
        self.profile, self.profile_body, self.profile_created = load_profile(self.cfg['cwd'])
        self.profile_loaded_at = self.profile_mtime()
        self.history = self.make_history()
        self.rag = self.make_rag()
        self.recorder = TurnRecorder()
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

    def shutdown_core(self):
        save_config(self.cfg)
        self.end_session()
        try:
            os.remove(PORT_FILE)
        except OSError:
            pass
        self.server.shutdown()

    # Browser-Modus: Ereignis-Abonnenten (SSE)
    def add_listener(self, q):
        self.listeners.append(q)

    def remove_listener(self, q):
        if q in self.listeners:
            self.listeners.remove(q)

    def broadcast(self, ev):
        for q in list(self.listeners):
            q.put(ev)

    # Von der Oberfläche zu liefern
    def ui(self, fn, *args):
        raise NotImplementedError

    def chat_event(self, ev):
        raise NotImplementedError

    def show_chat(self):
        return False

    def choose_folder(self):
        pass

    def open_uri(self, uri):
        import webbrowser
        webbrowser.open(uri)

    def set_clipboard(self, text):
        pass

    def edit_file(self, path):
        opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
        subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def set_folder(self, path):
        path = os.path.expanduser(path or '')
        if not os.path.isdir(path):
            self.chat_event({'type': 'error', 'text': 'Ordner nicht gefunden: ' + path})
            return
        self.cfg['cwd'] = os.path.abspath(path)
        save_config(self.cfg)
        self.reload_profile()
        self.chat_event(self.ready_event('Ordner gewechselt: ' + self.cfg['cwd'] + ' · nächste Nachricht startet dort eine neue Sitzung', replay=True))

    # --- Chat ----------------------------------------------------------------
    def refresh_models_async(self):
        """Lokale Modelle im Hintergrund abfragen (Ollama-Start kann Sekunden dauern) und nachreichen."""
        def work():
            models = self.model_choices()
            self.ui(self.chat_event, {'type': 'models', 'models': models, 'current': self.current_choice()})
        threading.Thread(target=work, daemon=True).start()

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
        ragfiles = None
        if self.rag and self.rag.exists():
            ragfiles = len(self.rag.files())
            note = ('Unterlagen: %d Dateien in rag/ · ' % ragfiles) + note
        if self.profile_created:
            note = 'Profil angelegt: %s – bitte ausfüllen (Knopf „Profil“), dann „Neu laden“. ' % PROFILE_NAME + note
        return {'type': 'ready', 'cwd': self.cfg['cwd'], 'home': HOME, 'user': os.path.basename(HOME),
                'model': self.effective('model'), 'perm': self.effective_perm(),
                'backend': self.profile.get('backend', 'claude'),
                'models': self.model_choices(local=False), 'current': self.current_choice(),
                'locked': {'model': False, 'perm': bool(self.profile_perm())},
                'profile': PROFILE_NAME if os.path.exists(os.path.join(self.cfg['cwd'], PROFILE_NAME)) else '',
                'replay': [{'role': r, 'time': t, 'text': x} for r, t, x in self.history.recent_turns()] if replay else [],
                'ragmode': self.rag_mode(), 'ragfiles': ragfiles,
                'note': note}

    def model_choices(self, local=True):
        choices = [{'id': 'claude:', 'group': 'Claude', 'label': 'Claude (Standard der Claude-Code-Einstellung)'},
                   {'id': 'claude:fable', 'group': 'Claude', 'label': 'Claude Fable 5.1 (am stärksten)'},
                   {'id': 'claude:opus', 'group': 'Claude', 'label': 'Claude Opus'},
                   {'id': 'claude:sonnet', 'group': 'Claude', 'label': 'Claude Sonnet (ausgewogen)'},
                   {'id': 'claude:haiku', 'group': 'Claude', 'label': 'Claude Haiku (schnell, günstig)'}]
        if not local:
            return choices
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
            self.ui(self.chat_event, {'type': 'status', 'text': 'Lade Modell %s … (läuft im Hintergrund)' % name})
            p = subprocess.Popen([exe, 'pull', name], env=ollama_env(os.environ), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            last = ''
            for line in p.stdout:
                import re
                m = re.search(r'(\d+)%', line)
                if m and m.group(1) != last:
                    last = m.group(1)
                    self.ui(self.chat_event, {'type': 'status', 'text': 'Lade Modell %s … %s %%' % (name, last)})
            ok = p.wait() == 0
            if ok:
                self.ui(self.choose_model, 'ollama:' + name)
                self.ui(self.chat_event, {'type': 'status', 'text': 'Modell %s geladen und ausgewählt.' % name})
            else:
                self.ui(self.chat_event, {'type': 'error', 'text': 'Modell %s konnte nicht geladen werden. Name prüfen (ollama.com/library).' % name})
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
        self.rag = self.make_rag()
        self.end_session()
        # Neues Profil = frische Sitzung. Eine fortgesetzte Claude-Sitzung würde an der alten Rolle
        # festhalten, weil das bisherige Gespräch das Verhalten stärker prägt als die neue Anweisung.
        self.forget_session()

    def profile_changed_on_disk(self):
        return self.profile_mtime() != getattr(self, 'profile_loaded_at', 0)

    def make_rag(self):
        enabled = str(self.profile.get('rag', 'true')).lower() not in ('false', 'no', 'nein', '0', 'off')
        try:
            k = int(self.profile.get('rag_chunks') or 6)
        except ValueError:
            k = 6
        strict = str(self.profile.get('rag_strict', 'false')).lower() in ('true', 'yes', 'ja', '1', 'on')
        rag = Rag(self.cfg['cwd'], self.profile.get('rag_dir') or RAG_DIRNAME, k, strict) if enabled else None
        if rag and not rag.exists():
            # Ordner beim Start mit anlegen (wie richterm.md und richterm-verlauf.md), mit kurzer Erklärung
            try:
                os.makedirs(rag.dir, exist_ok=True)
                with open(os.path.join(rag.dir, 'LIES-MICH.txt'), 'w', encoding='utf-8') as fh:
                    fh.write('Unterlagen für RichTerm.\n\nLege hier Vorlesungsfolien, Skripte, Übungen und Notizen ab '
                             '(PDF, Markdown, Text, DOCX, HTML, Code). RichTerm indexiert sie automatisch und gibt der KI '
                             'zu jeder Frage die passenden Abschnitte mit Quellenangabe mit. Wie streng die KI sich daran '
                             'hält, stellst du im Chat oben im Menü „Unterlagen“ ein. Diese Datei kannst du löschen.\n')
            except OSError:
                pass
        if rag and rag.exists():
            threading.Thread(target=self.rag_refresh, args=(rag,), daemon=True).start()
        return rag

    def rag_mode(self):
        if not self.rag:
            return 'off'
        return 'strict' if self.rag.strict else 'on'

    def set_rag_mode(self, mode):
        """Auswahl aus der Kopfzeile in richterm.md eintragen; rag/ bei Bedarf anlegen."""
        if mode == 'off':
            update_profile_settings(self.cfg['cwd'], rag='false')
        else:
            update_profile_settings(self.cfg['cwd'], rag='true', rag_strict='true' if mode == 'strict' else 'false')
            os.makedirs(os.path.join(self.cfg['cwd'], RAG_DIRNAME), exist_ok=True)
        self.profile, self.profile_body, self.profile_created = load_profile(self.cfg['cwd'])
        self.profile_loaded_at = self.profile_mtime()
        self.rag = self.make_rag()
        labels = {'off': 'Unterlagen aus', 'on': 'Unterlagen ergänzend (KI darf eigenes Wissen dazunehmen, kennzeichnet es)',
                  'strict': 'Nur Unterlagen (jede Aussage mit Quelle; sonst „steht nicht in den Unterlagen“)'}
        self.chat_event(self.ready_event(labels.get(mode, mode) + ' · gilt ab der nächsten Frage'))

    def ensure_embed_model(self):
        """Einbettungsmodell für die semantische Suche einmalig laden (klein, offline), falls Ollama da ist."""
        if not find_ollama() or embed_model_available() or getattr(self, '_embed_pulling', False):
            return
        self._embed_pulling = True
        exe = find_ollama()
        self.ui(self.chat_event, {'type': 'status', 'text': 'Lade Suchmodell %s für die Unterlagen (einmalig, ~620 MB) …' % EMBED_MODEL})
        p = subprocess.run([exe, 'pull', EMBED_MODEL], env=ollama_env(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._embed_pulling = False
        if p.returncode == 0 and self.rag:
            self.rag.use_embeddings = True
            self.rag.refresh()
            self.ui(self.chat_event, {'type': 'status', 'text': 'Suchmodell geladen: Unterlagen werden jetzt auch nach Bedeutung durchsucht.'})

    def rag_refresh(self, rag=None):
        rag = rag or self.rag
        if not rag or not rag.exists():
            return
        if find_ollama() and not embed_model_available():
            self.ensure_embed_model()
        files, chunks, changed = rag.refresh(embed=False)
        if changed:
            self.ui(self.chat_event, {'type': 'status', 'text': 'Unterlagen indexiert: %d Dateien, %d Abschnitte (rag/)' % (files, chunks)})

        def progress(done, total):
            self.ui(self.chat_event, {'type': 'status', 'text': 'Unterlagen: Bedeutungssuche wird vorbereitet … %d/%d Abschnitte' % (done, total)})
        if rag.embed_missing(progress):
            self.ui(self.chat_event, {'type': 'status', 'text': 'Unterlagen bereit: %d Dateien, %d Abschnitte, Bedeutungssuche aktiv' % (files, chunks)})

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
        self.ui(self.chat_event, {'type': 'status', 'text': 'Verlauf wird verdichtet (Zusammenfassung in %s) …' % HISTORY_NAME})
        ok = self.history.compact(self.summarizer())
        self.ui(self.chat_event, {'type': 'status', 'text': 'Verlauf verdichtet.' if ok else 'Verlauf: Verdichten nicht möglich, Rohverlauf bleibt.'})

    def handle_command(self, data):
        cmd = data.get('cmd')
        if cmd == 'ready':
            self.chat_event(self.ready_event('Bereit · Ordner: ' + self.cfg['cwd'], replay=True))
        elif cmd == 'send':
            self.send_to_claude(data.get('text', ''), data.get('attachments') or [])
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
            self.refresh_models_async()
        elif cmd == 'set_ragmode':
            self.set_rag_mode(data.get('value', 'on'))
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
            self.edit_file(path)
        elif cmd == 'profile_reload':
            self.reload_profile()
            self.chat_event(self.ready_event('Profil neu geladen · gilt ab der nächsten Nachricht'))
        elif cmd == 'save_note':
            import re
            question = (data.get('question') or '').strip()
            answer = (data.get('text') or '').strip()
            if not answer:
                return
            folder = os.path.join(self.cfg['cwd'], 'lernzettel')
            os.makedirs(folder, exist_ok=True)
            m = re.search(r'^#+\s*(.+)$', answer, re.M)
            title = (m.group(1) if m else question or 'Lernzettel').strip()[:60]
            slug = re.sub(r'[^\w-]+', '-', title).strip('-').lower() or 'lernzettel'
            path = os.path.join(folder, '%s-%s.md' % (datetime.datetime.now().strftime('%Y-%m-%d-%H%M'), slug))
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('# %s\n\n' % title)
                if question:
                    fh.write('> **Frage:** %s\n\n' % question.replace('\n', ' '))
                fh.write(answer.rstrip() + '\n')
            self.chat_event({'type': 'status', 'text': 'Lernzettel gespeichert: ' + os.path.relpath(path, self.cfg['cwd'])})
        elif cmd == 'copy':
            self.set_clipboard(data.get('text', ''))      # Rückfall, falls die Browser-Zwischenablage nicht erlaubt ist
        elif cmd == 'set_folder':
            self.set_folder(data.get('path', ''))
        elif cmd == 'open':
            url = data.get('url', '')
            if url:
                self.open_uri(self.base_url + url if url.startswith('/') else url)

    def store_attachments(self, attachments):
        """Anhänge (Bilder, PDFs, Dateien) im Arbeitsordner unter richterm-anhang/ ablegen.
        Gibt (Textzusatz, Bildblöcke) zurück."""
        import base64
        import re
        import urllib.parse
        notes, blocks = [], []
        folder = os.path.join(self.cfg['cwd'], 'richterm-anhang')
        for a in attachments or []:
            name = os.path.basename(a.get('name') or 'anhang')
            mime = a.get('mime') or mimetypes.guess_type(name)[0] or 'application/octet-stream'
            src_path = a.get('path')
            if src_path and src_path.startswith('file://'):
                src_path = urllib.parse.unquote(src_path[7:])
            data = None
            if src_path and os.path.isfile(src_path):
                path = src_path                       # Datei liegt schon auf der Platte: direkt verwenden
                if mime.startswith('image/'):
                    with open(path, 'rb') as fh:
                        data = base64.b64encode(fh.read()).decode('ascii')
            else:
                raw = a.get('data', '')
                raw = raw.split(',', 1)[1] if ',' in raw[:80] else raw
                os.makedirs(folder, exist_ok=True)
                stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
                safe = re.sub(r'[^\w.-]+', '_', name)
                path = os.path.join(folder, '%s-%s' % (stamp, safe))
                with open(path, 'wb') as fh:
                    fh.write(base64.b64decode(raw))
                if mime.startswith('image/'):
                    data = raw
            rel = os.path.relpath(path, self.cfg['cwd'])
            if mime.startswith('image/') and data:
                blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': data}})
                notes.append('[Bild angehängt: %s]' % rel)
            else:
                notes.append('[Datei angehängt: %s — lies sie bei Bedarf mit dem Read-Werkzeug]' % rel)
        return ('\n'.join(notes), blocks)

    def send_to_claude(self, text, attachments=None):
        if not text.strip() and not attachments:
            return
        note, blocks = self.store_attachments(attachments)
        shown = text if not note else (text + '\n' + note).strip()
        # Unterlagen aus rag/: passende Abschnitte zur Frage mitgeben
        rag_block, sources = '', []
        if self.rag and self.rag.exists():
            files0 = set(self.rag.docs)
            self.rag.refresh(embed=False)                   # neue Dateien sofort per Stichwortsuche nutzbar
            if set(self.rag.docs) != files0:
                threading.Thread(target=self.rag_refresh, daemon=True).start()   # Einbettungen im Hintergrund
            is_cloud = self.profile.get('backend', 'claude').lower() != 'command'
            rag_block, sources, text_clean = self.rag.context_for(text, max_full_chars=80000 if is_cloud else 12000)
            if text_clean != text:
                shown = shown.replace(text, text_clean, 1) if text in shown else text_clean
        model_text = (rag_block + '\n\n---\n\nFrage des Nutzers:\n' + shown) if rag_block else shown
        content = model_text
        if blocks:
            content = [{'type': 'text', 'text': model_text}] + blocks
        if self.profile_changed_on_disk():
            # richterm.md wurde gespeichert: automatisch übernehmen, neue Sitzung mit neuem Profil
            self.reload_profile()
            self.chat_event(self.ready_event('Profil wurde geändert und automatisch übernommen'))
        if self.session is None:
            on_msg = lambda m: self.ui(self.on_claude_message, m)  # noqa: E731
            on_exit = lambda code: self.ui(self.on_session_exit, code)  # noqa: E731
            prompt = build_system_prompt(self.profile, self.profile_body)
            resume = None if self.profile.get('backend', 'claude').lower() == 'command' else self.saved_session()
            memory = '' if resume else self.history.context_block()   # beim Fortsetzen kennt Claude den Verlauf schon
            if memory:
                prompt += '\n\n' + memory
            try:
                if self.profile.get('backend', 'claude').lower() == 'command':
                    command = (self.profile.get('command') or '').strip()
                    if command.startswith('ollama run') or command.startswith('ollama'):
                        self.session = OllamaSession(self.cfg['cwd'], self.effective('model'), prompt, on_msg, on_exit)
                    else:
                        self.session = CommandSession(self.cfg['cwd'], command, self.effective('model'), prompt, on_msg, on_exit)
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
        self.recorder.user = shown
        self.chat_event({'type': 'user_sent', 'text': shown, 'sources': sources,
                         'images': [b['source']['data'] and ('data:%s;base64,%s' % (b['source']['media_type'], b['source']['data'])) for b in blocks]})
        self.session.send_user(content)

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
    # --- Tasten --------------------------------------------------------------


class NativeMixin(Core):
    """Natives Fenster (Linux): GTK + WebKit-Chat + VTE-Terminals. Wird zur Laufzeit mit Gtk.Window verbunden."""

    def __init__(self, start_dir=None):
        Gtk.Window.__init__(self, title='RichTerm')
        self.init_core(start_dir)
        self.set_default_size(*self.cfg['window'])
        self.connect('destroy', self.on_quit)
        self.connect('key-press-event', self.on_key)
        self.connect('size-allocate', self.on_size)      # Fenstergröße laufend merken

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.connect('page-removed', self.on_page_removed)
        self.add(self.notebook)

        self.web = self.build_chat()
        self.notebook.append_page(self.web, Gtk.Label(label='Chat'))
        self.set_title('RichTerm — ' + self.cfg['cwd'].replace(HOME, '~'))
        self.show_all()

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

    def ui(self, fn, *args):
        GLib.idle_add(fn, *args)

    def chat_event(self, ev):
        if ev.get('type') == 'ready':
            self.refresh_models_async()
        js = 'window.chatEvent(JSON.parse(%s));' % json.dumps(json.dumps(ev))
        if hasattr(self.web, 'evaluate_javascript'):
            self.web.evaluate_javascript(js, -1, None, None, None, None, None)
        else:
            self.web.run_javascript(js, None, None, None)
        return False

    def show_chat(self):
        self.notebook.set_current_page(0)
        return False

    def on_js_message(self, ucm, result):
        try:
            data = json.loads(result.get_js_value().to_string())
        except (ValueError, AttributeError):
            return
        self.handle_command(data)

    def choose_folder(self):
        dlg = Gtk.FileChooserDialog(title='Arbeitsordner für Claude wählen', parent=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons('Abbrechen', Gtk.ResponseType.CANCEL, 'Auswählen', Gtk.ResponseType.OK)
        dlg.set_current_folder(self.cfg['cwd'])
        chosen = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        if chosen:
            self.set_folder(chosen)

    def open_uri(self, uri):
        Gtk.show_uri_on_window(self, uri, Gdk.CURRENT_TIME)

    def set_clipboard(self, text):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)

    def edit_file(self, path):
        Gtk.show_uri_on_window(self, 'file://' + path, Gdk.CURRENT_TIME)

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

    def on_size(self, widget, allocation):
        w, h = self.get_size()
        if w >= 700 and h >= 450 and not (self.get_window() and
                                           self.get_window().get_state() & Gdk.WindowState.MAXIMIZED):
            self.cfg['window'] = [w, h]

    def on_quit(self, *a):
        self.shutdown_core()
        Gtk.main_quit()


class WebApp(Core):
    """Browser-Modus (macOS, Windows, Linux ohne GTK): der Chat läuft im Browser, Python liefert ihn aus."""

    def __init__(self, start_dir=None):
        self.lock = threading.RLock()
        self.ever_connected = False
        self.init_core(start_dir)

    def ui(self, fn, *args):
        with self.lock:
            fn(*args)

    def chat_event(self, ev):
        if ev.get('type') == 'ready':
            self.refresh_models_async()
        self.broadcast(ev)
        return False

    def choose_folder(self):
        self.chat_event({'type': 'ask_folder', 'current': self.cfg['cwd']})

    def add_listener(self, q):
        self.ever_connected = True
        Core.add_listener(self, q)

    def run(self, open_browser=True):
        url = self.base_url + '/chat/index.html'
        print('RichTerm (Browser-Modus): ' + url, flush=True)
        print('Ordner: ' + self.cfg['cwd'] + '   ·   Beenden mit Strg+C oder durch Schließen des Browserfensters', flush=True)
        if open_browser:
            self.open_app_window(url)
        import time
        idle = 0
        try:
            while True:
                time.sleep(2)
                if self.ever_connected and not self.listeners:
                    idle += 2
                    if idle >= 90:                          # Browserfenster seit 90 s zu: beenden
                        break
                else:
                    idle = 0
        except KeyboardInterrupt:
            pass
        self.shutdown_core()

    @staticmethod
    def open_app_window(url):
        """Chat als eigenes Fenster ohne Adressleiste öffnen (Chrome/Chromium/Edge), sonst Standardbrowser."""
        import webbrowser
        candidates = []
        if sys.platform == 'darwin':
            for app in ('Google Chrome', 'Chromium', 'Microsoft Edge', 'Brave Browser'):
                p = '/Applications/%s.app/Contents/MacOS/%s' % (app, app)
                if os.path.exists(p):
                    candidates.append([p])
        else:
            for exe in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge', 'brave-browser'):
                if shutil.which(exe):
                    candidates.append([shutil.which(exe)])
        for c in candidates:
            try:
                subprocess.Popen(c + ['--app=' + url, '--window-size=1400,900'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return
            except OSError:
                continue
        webbrowser.open(url)


def gtk_available():
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Vte', '2.91')
        gi.require_version('WebKit2', '4.1')
        from gi.repository import Gtk, Vte, WebKit2  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    if '-h' in sys.argv or '--help' in sys.argv:
        print(__doc__)
        print('Aufruf: richterm [--web] [ORDNER]   — startet RichTerm mit ORDNER als Arbeitsordner (Standard: aktueller Ordner)')
        print('  --web   Browser-Modus (macOS/Windows oder Linux ohne GTK): Chat im Browser statt im eigenen Fenster')
        return 0
    start_dir = args[0] if args else None
    web = '--web' in sys.argv or sys.platform != 'linux' or not gtk_available()
    if web:
        WebApp(start_dir).run(open_browser='--no-browser' not in sys.argv)
        return 0
    load_gtk()
    RichTermWindow = type('RichTerm', (NativeMixin, Gtk.Window), {})
    app = RichTermWindow(start_dir)
    try:
        Gtk.main()
    except KeyboardInterrupt:
        app.on_quit()


if __name__ == '__main__':
    sys.exit(main())
