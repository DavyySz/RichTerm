#!/usr/bin/env python3
"""
RichTerm — ein Terminal mit Anzeige für Formeln, Bilder, Diagramme und Dokumente.

Links:  vollwertiges Terminal (VTE, dieselbe Engine wie GNOME Terminal), mit Tabs.
Rechts: Anzeige (WebKit) für LaTeX, Markdown, Bilder, GIFs, Videos, Mermaid, HTML.

Inhalte schickt man mit dem Befehl `rt` aus dem Terminal heraus (siehe bin/rt).
Die App startet dafür einen kleinen HTTP-Empfänger auf 127.0.0.1 und setzt die
Umgebungsvariable RT_PORT in jeder Shell, die sie öffnet.

Tastenkürzel:
  Ctrl+Shift+T  neuer Tab          Ctrl+Shift+W  Tab schließen
  Ctrl+PgUp/PgDn  Tab wechseln     Ctrl+Shift+C / Ctrl+Shift+V  kopieren / einfügen
  Ctrl+Shift++ / Ctrl+Shift+-  Terminal-Schrift   Ctrl+Shift+F  Anzeige ein/aus
"""
import json
import mimetypes
import os
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
PANEL = os.path.join(HERE, 'panel', 'index.html')
CONFIG_PATH = os.path.join(GLib.get_user_config_dir(), 'richterm.json')
PORT_FILE = os.path.join(GLib.get_user_runtime_dir(), 'richterm.port')
HTML_DIR = os.path.join(GLib.get_user_cache_dir(), 'richterm', 'html')

DEFAULTS = {
    'font': 'DejaVu Sans Mono 11',
    'scrollback': 100000,
    'panel_width': 0.45,      # Anteil der Anzeige an der Fensterbreite
    'window': [1500, 900],
    'theme': 'dark',
}

# Farbschema (Terminal)
PALETTE_DARK = {
    'fg': '#e6e4df', 'bg': '#1b1e23',
    'colors': ['#2b3038', '#e5736f', '#8fc48a', '#e0b96a', '#79a8e0', '#c08ad6', '#6fc0c8', '#d6d3cc',
               '#5a6270', '#f09590', '#a9d9a4', '#efd08f', '#9dc0f0', '#d5a8e6', '#93d6dd', '#f2f0eb'],
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding='utf-8') as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
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
# HTTP-Empfänger: POST /show {kind, content, title, ...}
# ----------------------------------------------------------------------------
class Receiver(BaseHTTPRequestHandler):
    app = None
    counter = 0

    def log_message(self, *a):   # kein Log-Rauschen im Terminal
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
        if path.startswith('/file/'):                      # beliebige lokale Datei (nur localhost erreichbar)
            return self._send_file('/' + path[len('/file/'):])
        if path.startswith('/html/'):                      # abgelegte HTML-Karten
            return self._send_file(os.path.join(HTML_DIR, os.path.basename(path)))
        if path.startswith(('/panel/', '/vendor/')):       # die Anzeige selbst
            full = os.path.normpath(os.path.join(HERE, path.lstrip('/')))
            if full.startswith(HERE):
                return self._send_file(full)
        self._reply(404, {'ok': False})

    def do_POST(self):
        if self.path != '/show':
            return self._reply(404, {'ok': False})
        n = int(self.headers.get('Content-Length', 0))
        try:
            msg = json.loads(self.rfile.read(n).decode('utf-8'))
        except ValueError:
            return self._reply(400, {'ok': False, 'error': 'kein gültiges JSON'})
        if msg.get('kind') == 'html' and not str(msg.get('content', '')).startswith('file://'):
            # HTML als Datei ablegen: WebKit lädt eingebettete Seiten zuverlässiger per URI als per srcdoc
            os.makedirs(HTML_DIR, exist_ok=True)
            Receiver.counter += 1
            path = os.path.join(HTML_DIR, 'seite-%d.html' % Receiver.counter)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(msg['content'])
            msg['content'] = '/html/' + os.path.basename(path)
        GLib.idle_add(self.app.panel_add, msg)
        if msg.get('show', True):
            GLib.idle_add(self.app.show_panel, True)
        self._reply(200, {'ok': True})


# ----------------------------------------------------------------------------
# Hauptfenster
# ----------------------------------------------------------------------------
class RichTerm(Gtk.Window):
    def __init__(self):
        super().__init__(title='RichTerm')
        self.cfg = load_config()
        self.set_default_size(*self.cfg['window'])
        self.connect('destroy', self.on_quit)
        self.connect('key-press-event', self.on_key)

        # Empfänger starten (freier Port)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
        Receiver.app = self
        self.port = self.server.server_address[1]
        self.base_url = 'http://127.0.0.1:%d' % self.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # Port auch in eine Datei schreiben, damit `rt` aus anderen Terminals heraus funktioniert
        try:
            with open(PORT_FILE, 'w', encoding='utf-8') as fh:
                fh.write(str(self.port))
        except OSError:
            pass

        # Layout: Tabs links, Anzeige rechts
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(self.paned)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.connect('page-removed', self.on_page_removed)
        self.paned.pack1(self.notebook, resize=True, shrink=False)

        self.panel = self.build_panel()
        self.paned.pack2(self.panel, resize=True, shrink=True)

        self.new_tab()
        self.show_all()
        w = self.get_allocated_width() or self.cfg['window'][0]
        self.paned.set_position(int(w * (1 - self.cfg['panel_width'])))

    # --- Anzeige -------------------------------------------------------------
    def build_panel(self):
        settings = WebKit2.Settings()
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)
        settings.set_enable_developer_extras(True)
        settings.set_enable_javascript(True)
        settings.set_enable_write_console_messages_to_stdout(False)
        settings.set_enable_media(True) if hasattr(settings, 'set_enable_media') else None
        self.web = WebKit2.WebView.new_with_settings(settings)
        self.web.set_background_color(rgba(PALETTE_DARK['bg']))
        self.web.load_uri(self.base_url + '/panel/index.html')
        self.web.connect('decide-policy', self.on_web_policy)
        return self.web

    def on_web_policy(self, web, decision, dtype):
        # Links in der Anzeige: externe Seiten im Browser öffnen, nicht im Panel
        if dtype == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not uri.startswith(self.base_url):
                Gtk.show_uri_on_window(self, uri, Gdk.CURRENT_TIME)
                decision.ignore()
                return True
        return False

    def panel_add(self, msg):
        js = 'window.rtAdd(JSON.parse(%s));' % json.dumps(json.dumps(msg))
        if hasattr(self.web, 'evaluate_javascript'):
            self.web.evaluate_javascript(js, -1, None, None, None, None, None)
        else:
            self.web.run_javascript(js, None, None, None)
        return False

    def show_panel(self, visible=None):
        if visible is None:
            visible = not self.panel.get_visible()
        self.panel.set_visible(visible)
        return False

    # --- Terminal-Tabs -------------------------------------------------------
    def new_tab(self, cwd=None):
        term = Vte.Terminal()
        term.set_font(Pango.FontDescription(self.cfg['font']))
        term.set_scrollback_lines(self.cfg['scrollback'])
        term.set_scroll_on_output(False)
        term.set_scroll_on_keystroke(True)
        term.set_mouse_autohide(True)
        term.set_allow_hyperlink(True)
        term.set_colors(rgba(PALETTE_DARK['fg']), rgba(PALETTE_DARK['bg']),
                        [rgba(c) for c in PALETTE_DARK['colors']])
        term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        term.connect('child-exited', lambda t, status: self.close_tab(t))
        term.connect('window-title-changed', self.on_title_changed)
        term.connect('button-press-event', self.on_term_click)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(term)
        scroller.term = term

        label = Gtk.Label(label='Terminal')
        idx = self.notebook.append_page(scroller, label)
        self.notebook.set_tab_reorderable(scroller, True)
        self.notebook.show_all()
        self.notebook.set_current_page(idx)

        env = dict(os.environ)
        env['RT_PORT'] = str(self.port)
        env['RICHTERM'] = '1'
        env['TERM'] = 'xterm-256color'
        env['COLORTERM'] = 'truecolor'
        env.pop('LINES', None)
        env.pop('COLUMNS', None)
        envv = ['%s=%s' % kv for kv in env.items()]
        shell = os.environ.get('SHELL') or '/bin/bash'
        term.spawn_async(Vte.PtyFlags.DEFAULT, cwd or os.path.expanduser('~'), [shell, '-l'], envv,
                         GLib.SpawnFlags.DEFAULT, None, None, -1, None, None)
        term.grab_focus()
        return term

    def current_term(self):
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        return page.term if page is not None else None

    def close_tab(self, term):
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if page.term is term:
                self.notebook.remove_page(i)
                break

    def on_page_removed(self, nb, child, idx):
        if nb.get_n_pages() == 0:
            self.on_quit()

    def on_title_changed(self, term):
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if page.term is term:
                title = term.get_window_title() or 'Terminal'
                self.notebook.get_tab_label(page).set_text(title[:40])
                if page is self.notebook.get_nth_page(self.notebook.get_current_page()):
                    self.set_title(title + ' — RichTerm')

    def on_term_click(self, term, event):
        # Rechtsklick: einfügen; Ctrl+Klick auf Link: öffnen
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
            self.notebook.get_nth_page(i).term.set_font(fd)
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
                self.new_tab(cwd)
                return True
            if k == 'w' and term is not None:
                self.close_tab(term)
                return True
            if k == 'c' and term is not None:
                term.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if k == 'v' and term is not None:
                term.paste_clipboard()
                return True
            if k in ('plus', 'equal'):
                self.zoom(+1)
                return True
            if k in ('minus', 'underscore'):
                self.zoom(-1)
                return True
            if k == 'f':
                self.show_panel()
                return True
        if ctrl and key == 'Page_Up':
            self.notebook.prev_page()
            return True
        if ctrl and key == 'Page_Down':
            self.notebook.next_page()
            return True
        return False

    def on_quit(self, *a):
        w = self.get_allocated_width()
        if w:
            self.cfg['window'] = list(self.get_size())
            if self.panel.get_visible():
                self.cfg['panel_width'] = max(0.15, 1 - self.paned.get_position() / w)
        save_config(self.cfg)
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
