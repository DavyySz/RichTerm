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
import json
import mimetypes
import os
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
    'fg': '#e6e4df', 'bg': '#1b1e23',
    'colors': ['#2b3038', '#e5736f', '#8fc48a', '#e0b96a', '#79a8e0', '#c08ad6', '#6fc0c8', '#d6d3cc',
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
# Claude-Code-Sitzung: ein `claude -p`-Prozess im Streaming-Modus
# ----------------------------------------------------------------------------
class ClaudeSession:
    def __init__(self, cwd, model, perm, port, on_message, on_exit):
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
               '--append-system-prompt', SYSTEM_NOTE]
        if model:
            cmd += ['--model', model]
        if perm and perm != 'default':
            cmd += ['--permission-mode', perm]
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
    def __init__(self):
        super().__init__(title='RichTerm')
        self.cfg = load_config()
        self.session = None
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

    def ready_event(self, note):
        return {'type': 'ready', 'cwd': self.cfg['cwd'], 'home': HOME, 'user': os.path.basename(HOME),
                'model': self.cfg['model'], 'perm': self.cfg['perm'], 'note': note}

    def on_js_message(self, ucm, result):
        try:
            data = json.loads(result.get_js_value().to_string())
        except (ValueError, AttributeError):
            return
        cmd = data.get('cmd')
        if cmd == 'ready':
            self.chat_event(self.ready_event('Bereit · Ordner: ' + self.cfg['cwd']))
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
            self.chat_event({'type': 'status', 'text': 'Neuer Chat · Ordner: ' + self.cfg['cwd']})
        elif cmd == 'set':
            key, value = data.get('key'), data.get('value', '')
            if key in ('model', 'perm'):
                self.cfg[key] = value
                save_config(self.cfg)
                self.end_session()
                self.chat_event({'type': 'status', 'text': 'Übernommen. Gilt ab der nächsten Nachricht (neue Sitzung).'})
        elif cmd == 'choose_folder':
            self.choose_folder()
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
            self.end_session()
            self.chat_event(self.ready_event('Ordner gewechselt: ' + self.cfg['cwd'] + ' · nächste Nachricht startet dort eine neue Sitzung'))
        dlg.destroy()

    def send_to_claude(self, text):
        if not text.strip():
            return
        if self.session is None:
            try:
                self.session = ClaudeSession(self.cfg['cwd'], self.cfg['model'], self.cfg['perm'], self.port,
                                             lambda m: GLib.idle_add(self.chat_event, {'type': 'claude', 'msg': m}),
                                             lambda code: GLib.idle_add(self.on_session_exit, code))
            except Exception as e:  # noqa: BLE001
                self.chat_event({'type': 'error', 'text': str(e)})
                return
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
    app = RichTerm()
    try:
        Gtk.main()
    except KeyboardInterrupt:
        app.on_quit()


if __name__ == '__main__':
    sys.exit(main())
