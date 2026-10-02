/* RichTerm Chat: Oberfläche für Claude Code. Die App (Python) schickt Ereignisse über
   window.chatEvent(obj); die Oberfläche schickt Befehle über send({...}) an die App. */
(function () {
  const $ = id => document.getElementById(id);
  const messages = $('messages'), input = $('input'), statusText = $('status-text');
  let theme = localStorage.getItem('rt-theme') || 'dark';
  let fontSize = parseFloat(localStorage.getItem('rt-font') || '17');
  let cwd = '~';
  let counter = 0;
  let current = null;          // laufende Assistenten-Nachricht {el, bubble, text, blocks:{index:{kind,text,el}}}
  let lastQuestion = '';
  let attachments = [];        // {name, mime, data?|path?}
  let busy = false;

  // ---------- Kommunikation mit der App ----------
  // Natives Fenster: WebKit-Nachrichtenkanal. Browser-Modus: HTTP (/cmd) hin, Server-Sent Events (/events) zurück.
  const NATIVE = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.app);
  function send(obj) {
    if (NATIVE) {
      try { window.webkit.messageHandlers.app.postMessage(JSON.stringify(obj)); } catch (e) { console.error('kein App-Kanal', e); }
    } else {
      fetch('/cmd', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj) })
        .catch(() => setStatus('Verbindung zu RichTerm verloren. Bitte richterm neu starten.'));
    }
  }
  function connectEvents(onReady) {
    if (NATIVE) { onReady(); return; }
    const es = new EventSource('/events');
    let opened = false;
    es.onopen = () => { if (!opened) { opened = true; onReady(); } };
    es.onmessage = (e) => { try { window.chatEvent(JSON.parse(e.data)); } catch (err) { console.error(err); } };
    es.onerror = () => setStatus('Verbindung unterbrochen … versuche erneut');
  }

  // ---------- Hilfen ----------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function applyPrefs() {
    document.documentElement.style.setProperty('--font-size', fontSize + 'px');
    if (theme === 'light') document.documentElement.setAttribute('data-theme', 'light');
    else document.documentElement.removeAttribute('data-theme');
    $('btn-theme').textContent = theme === 'light' ? '☾' : '☀';
    $('hljs-dark').disabled = theme === 'light';
    $('hljs-light').disabled = theme !== 'light';
    const mv = theme === 'light'
      ? { background: '#f1f1ee', primaryColor: '#d6e4e6', primaryTextColor: '#1d2126', primaryBorderColor: '#19606a',
          secondaryColor: '#e3e8ea', secondaryTextColor: '#1d2126', secondaryBorderColor: '#7f837f',
          tertiaryColor: '#ececea', tertiaryTextColor: '#1d2126', tertiaryBorderColor: '#7f837f',
          lineColor: '#30363d', textColor: '#1d2126', edgeLabelBackground: '#f1f1ee',
          noteBkgColor: '#f3e9c6', noteTextColor: '#1d2126', actorBkg: '#d6e4e6', actorBorder: '#19606a', actorTextColor: '#1d2126',
          signalColor: '#30363d', signalTextColor: '#1d2126', labelBoxBkgColor: '#e3e8ea', labelTextColor: '#1d2126', loopTextColor: '#1d2126',
          clusterBkg: '#e3e8ea', clusterBorder: '#7f837f', titleColor: '#1d2126', fontFamily: 'DejaVu Sans, sans-serif', fontSize: '15px' }
      : { background: '#33373d', primaryColor: '#3f4d5a', primaryTextColor: '#e8e6e1', primaryBorderColor: '#7cc9d1',
          secondaryColor: '#4a5563', secondaryTextColor: '#e8e6e1', secondaryBorderColor: '#8b94a0',
          tertiaryColor: '#3c4148', tertiaryTextColor: '#e8e6e1', tertiaryBorderColor: '#8b94a0',
          lineColor: '#c9d1d9', textColor: '#e8e6e1', edgeLabelBackground: '#2b2e33',
          noteBkgColor: '#5a5033', noteTextColor: '#f2f0eb', actorBkg: '#3f4d5a', actorBorder: '#7cc9d1', actorTextColor: '#e8e6e1',
          signalColor: '#c9d1d9', signalTextColor: '#e8e6e1', labelBoxBkgColor: '#4a5563', labelTextColor: '#e8e6e1', loopTextColor: '#e8e6e1',
          clusterBkg: '#3c4148', clusterBorder: '#8b94a0', titleColor: '#e8e6e1', fontFamily: 'DejaVu Sans, sans-serif', fontSize: '15px' };
    mermaid.initialize({ startOnLoad: false, theme: 'base', themeVariables: mv, suppressErrorRendering: true });
    localStorage.setItem('rt-theme', theme); localStorage.setItem('rt-font', fontSize);
  }
  function nearBottom() { return messages.scrollHeight - messages.scrollTop - messages.clientHeight < 120; }
  function scrollDown(force) { if (force || nearBottom()) messages.scrollTop = messages.scrollHeight; }
  function toUrl(p) {
    if (/^(https?:|data:|\/file\/|\/html\/)/.test(p)) return p;
    if (p.startsWith('file://')) return '/file' + p.slice(7);
    if (p.startsWith('~/')) return '/file' + p.slice(1).replace(/^\//, '/home/' + (window.RT_USER || '') + '/');
    if (p.startsWith('/')) return '/file' + p;
    return '/file' + (cwd === '~' ? '' : cwd) + '/' + p;
  }
  function setStatus(s) { statusText.textContent = s || ''; }
  function setBusy(b) { busy = b; $('btn-stop').hidden = !b; $('btn-send').hidden = b; }

  // ---------- Hintergrundbild ----------
  let bgId = '', bgDim = 0.5;
  function applyBackground() {
    const layer = $('bg-layer');
    document.body.classList.toggle('has-bg', !!bgId);
    layer.style.backgroundImage = bgId ? 'url("/bg/' + encodeURIComponent(bgId).replace(/%2F/g, '/') + '")' : '';
    document.documentElement.style.setProperty('--bg-dim', bgDim);
    $('bg-dim').hidden = !bgId;
    $('bg-dim').value = Math.round(bgDim * 100);
    $('bg').value = bgId;
  }
  async function loadBackgrounds() {
    try {
      const r = await fetch('/bg/'); const j = await r.json();
      const sel = $('bg');
      sel.innerHTML = '<option value="">Hintergrund: Standard</option>';
      for (const b of j.backgrounds || []) { const o = document.createElement('option'); o.value = b.id; o.textContent = b.label; sel.appendChild(o); }
      sel.value = bgId;
    } catch (e) { /* kein Server (sollte nicht passieren) */ }
  }
  $('bg').onchange = () => { bgId = $('bg').value; applyBackground(); send({ cmd: 'set_background', value: bgId, dim: bgDim }); };
  $('bg-dim').oninput = () => { bgDim = $('bg-dim').value / 100; applyBackground(); };
  $('bg-dim').onchange = () => send({ cmd: 'set_background', value: bgId, dim: bgDim });

  // ---------- Modellmenü ----------
  function fillModels(models, current) {
    const sel = $('model');
    sel.innerHTML = '';
    const groups = {};
    for (const m of models) {
      if (!groups[m.group]) { groups[m.group] = document.createElement('optgroup'); groups[m.group].label = m.group; sel.appendChild(groups[m.group]); }
      const o = document.createElement('option'); o.value = m.id; o.textContent = m.label; groups[m.group].appendChild(o);
    }
    const more = document.createElement('optgroup'); more.label = 'Weitere';
    const o = document.createElement('option'); o.value = '__pull__'; o.textContent = 'Lokales Modell herunterladen …'; more.appendChild(o);
    sel.appendChild(more);
    if (![...sel.options].some(x => x.value === current)) {
      // aktuelles Modell ist (noch) nicht in der Liste, z. B. gerade erst eingetragen
      const g = groups['Lokal (Ollama, kostenlos, privat)'] || groups['Claude'] || sel;
      const x = document.createElement('option'); x.value = current; x.textContent = current.replace(/^\w+:/, '') || 'Claude (Standard)'; g.appendChild(x);
    }
    sel.value = current;
  }

  // ---------- Markdown → HTML mit Formeln, Diagrammen, Vorschauen ----------
  const MATH_RE = /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$(?!\s)[^$\n]+?(?<!\s)\$/g;

  function renderMarkdown(src, target, final = true) {
    const stash = [];
    // Formeln vor dem Markdown-Parser schützen; Codeblöcke bleiben, wie sie sind
    const protectedSrc = src.replace(/(```[\s\S]*?```)|(`[^`\n]+`)|(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$(?!\s)[^$\n]+?(?<!\s)\$)/g,
        (m, code, inline, math) => {
          if (math) { stash.push(math); return '\u0000M' + (stash.length - 1) + '\u0000'; }
          return m;
        });
    const renderer = new marked.Renderer();
    renderer.image = function (href, title, text) {
      return '<img src="' + escapeHtml(toUrl(href)) + '" alt="' + escapeHtml(text || '') + '">';
    };
    renderer.code = function (code, lang) {
      lang = (lang || '').trim().toLowerCase();
      if (!final) return '<pre><code class="language-' + escapeHtml(lang) + '">' + escapeHtml(code) + '</code></pre>';
      if (lang === 'mermaid') return '<div class="mermaid-src">' + escapeHtml(code) + '</div>';
      if (lang === 'svg' && code.trim().startsWith('<svg')) return '<div class="svg-inline">' + code + '</div>';
      if (lang === 'html') {
        return '<div class="preview" data-code="' + escapeHtml(code) + '">' +
          '<div class="preview-head"><span>Live-Vorschau</span><span class="grow"></span>' +
          '<button class="pv-code">Code</button><button class="pv-copy">Kopieren</button><button class="pv-reload">Neu laden</button>' +
          '<button class="pv-open">Im Browser</button></div>' +
          '<iframe sandbox="allow-scripts allow-same-origin"></iframe>' +
          '<pre hidden><code>' + escapeHtml(code) + '</code></pre></div>';
      }
      const label = '<span class="code-tools">' + (lang ? '<span class="code-lang">' + escapeHtml(lang) + '</span>' : '') +
        '<button class="copy-btn" title="Code kopieren">Kopieren</button></span>';
      return '<pre>' + label + '<code class="language-' + escapeHtml(lang) + '">' + escapeHtml(code) + '</code></pre>';
    };
    let html = marked.parse(protectedSrc, { gfm: true, breaks: false, renderer });
    html = html.replace(/\u0000M(\d+)\u0000/g, (_, i) => escapeHtml(stash[+i]));
    target.innerHTML = html;
    renderMathInElement(target, {
      delimiters: [
        { left: '$$', right: '$$', display: true }, { left: '\\[', right: '\\]', display: true },
        { left: '\\(', right: '\\)', display: false }, { left: '$', right: '$', display: false },
      ],
      throwOnError: false, macros: { '\\R': '\\mathbb{R}', '\\N': '\\mathbb{N}' },
    });
    target.querySelectorAll('.preview').forEach(setupPreview);
    if (final) highlightCode(target);      // beim Streamen noch nicht (wird bei jedem Textstück neu gerendert)
    setupCopyButtons(target);
    renderMermaidIn(target);
  }

  // Kopierknopf an Codeblöcken
  function copyText(text, btn) {
    const done = () => { btn.textContent = 'Kopiert ✓'; setTimeout(() => { btn.textContent = 'Kopieren'; }, 1500); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => { send({ cmd: 'copy', text }); done(); });
    } else { send({ cmd: 'copy', text }); done(); }
  }
  function setupCopyButtons(root) {
    root.querySelectorAll('pre .copy-btn').forEach(btn => {
      if (btn.dataset.ready) return;
      btn.dataset.ready = '1';
      btn.onclick = (e) => { e.stopPropagation(); const code = btn.closest('pre').querySelector('code'); copyText(code ? code.textContent : '', btn); };
    });
  }

  // Syntax-Hervorhebung (highlight.js, ~190 Sprachen; unbekannte Sprache -> automatische Erkennung)
  const LANG_ALIAS = { js: 'javascript', ts: 'typescript', py: 'python', sh: 'bash', shell: 'bash', zsh: 'bash',
    yml: 'yaml', md: 'markdown', 'c++': 'cpp', 'c#': 'csharp', cs: 'csharp', rs: 'rust', kt: 'kotlin', jsx: 'javascript',
    tsx: 'typescript', dockerfile: 'dockerfile', plaintext: 'plaintext', text: 'plaintext', txt: 'plaintext', console: 'bash' };
  function highlightCode(root) {
    if (!window.hljs) return;
    root.querySelectorAll('pre code').forEach(el => {
      if (el.dataset.highlighted) return;
      let lang = (el.className.match(/language-([\w+#.-]+)/) || [])[1] || '';
      lang = LANG_ALIAS[lang.toLowerCase()] || lang.toLowerCase();
      try {
        if (lang && hljs.getLanguage(lang)) {
          el.innerHTML = hljs.highlight(el.textContent, { language: lang, ignoreIllegals: true }).value;
        } else if (!lang && el.textContent.length < 20000) {
          el.innerHTML = hljs.highlightAuto(el.textContent).value;
        }
      } catch (e) { /* unverändert lassen */ }
      el.dataset.highlighted = '1';
    });
  }

  // Häufige Fehler in Mermaid-Quelltext von KI-Modellen automatisch beheben
  function repairMermaid(src) {
    let s = src.replace(/^\s*```(mermaid)?\s*|\s*```\s*$/g, '').trim();
    // Beschriftungen mit Sonderzeichen in Anführungszeichen setzen:  A[Input (25)]  ->  A["Input (25)"]
    const quote = (open, close) => {
      const re = new RegExp('([A-Za-z0-9_]+)\\' + open + '([^' + '\\' + open + '\\' + close + '"\\n]*?[()\\[\\]{}:;,#%&<>=+*/\\\\][^' + '\\' + open + '\\' + close + '"\\n]*?)\\' + close, 'g');
      s = s.replace(re, (m, id, label) => id + open + '"' + label.replace(/"/g, "'") + '"' + close);
    };
    quote('[', ']'); quote('{', '}');
    // Kantenbeschriftungen mit Sonderzeichen:  -->|a (b)|  ->  -->|"a (b)"|
    s = s.replace(/\|([^|"\n]*[()\[\]{}:;#%&<>][^|"\n]*)\|/g, (m, l) => '|"' + l + '"|');
    // Mehrzeilige Labels: <br/> vereinheitlichen
    s = s.replace(/<br\s*\/?>/gi, '<br/>');
    return s;
  }

  async function renderMermaidIn(el) {
    for (const b of el.querySelectorAll('.mermaid-src')) {
      const src = b.textContent;
      const holder = document.createElement('div'); holder.className = 'mermaid';
      b.replaceWith(holder);
      let lastErr = null;
      for (const candidate of [src, repairMermaid(src)]) {
        const id = 'mm-' + (++counter);
        try {
          holder.innerHTML = (await mermaid.render(id, candidate)).svg;
          lastErr = null; break;
        } catch (e) {
          lastErr = e;
          // Mermaid hängt bei Fehlern ein "Bomben"-SVG an den Seitenkörper: entfernen
          document.querySelectorAll('body > #' + id + ', body > #d' + id + ', body > svg[id^="mm-"], body > div[id^="dmm-"]').forEach(n => n.remove());
        }
      }
      if (lastErr) {
        const msg = String(lastErr && lastErr.message || lastErr).split('\n')[0].slice(0, 200);
        holder.innerHTML = '<div class="diagram-fail"><div class="diagram-fail-head">Diagramm konnte nicht gezeichnet werden: ' + escapeHtml(msg) +
          ' <button class="fix-btn">Reparieren lassen</button></div><pre><code class="language-mermaid">' + escapeHtml(src) + '</code></pre></div>';
        holder.querySelector('.fix-btn').onclick = () => {
          if (busy) return;
          send({ cmd: 'send', text: 'Dein Mermaid-Diagramm ließ sich nicht zeichnen (Fehler: ' + msg + '). Gib es bitte als korrigierten ```mermaid-Block erneut aus. Regeln: Beschriftungen mit Sonderzeichen in doppelte Anführungszeichen setzen, nur flowchart/graph/sequenceDiagram/classDiagram/stateDiagram, keine Markdown-Formatierung in Beschriftungen.\n\n```mermaid\n' + src + '\n```' });
        };
      }
    }
  }

  async function setupPreview(box) {
    const code = box.dataset.code;
    const frame = box.querySelector('iframe');
    const pre = box.querySelector('pre');
    const load = async () => {
      // HTML über den eingebauten Server ablegen, damit Skripte, Canvas und Animationen zuverlässig laufen
      try {
        const r = await fetch('/store', { method: 'POST', body: code, headers: { 'Content-Type': 'text/html' } });
        const j = await r.json();
        frame.src = j.url;
        box.dataset.url = j.url;
      } catch (e) { frame.srcdoc = code; }
    };
    box.querySelector('.pv-code').onclick = () => { pre.hidden = !pre.hidden; frame.hidden = !pre.hidden; };
    box.querySelector('.pv-copy').onclick = (e) => copyText(code, e.target);
    box.querySelector('.pv-reload').onclick = load;
    box.querySelector('.pv-open').onclick = () => send({ cmd: 'open', url: box.dataset.url || '' });
    const h = /data-height="(\d+)"|<!--\s*height:\s*(\d+)/i.exec(code);
    if (h) frame.style.height = (h[1] || h[2]) + 'px';
    load();
  }

  // ---------- Nachrichten ----------
  function addMessage(role) {
    $('welcome').hidden = true;
    const el = document.createElement('div');
    el.className = 'msg ' + role;
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    el.appendChild(bubble);
    messages.appendChild(el);
    return { el, bubble };
  }

  function addUser(text, images, sources) {
    const m = addMessage('user');
    m.bubble.textContent = text;
    for (const src of images || []) {
      const img = document.createElement('img'); img.src = src; img.className = 'user-img';
      m.bubble.appendChild(img);
    }
    if (sources && sources.length) {
      const s = document.createElement('div'); s.className = 'sources';
      s.textContent = '📚 Unterlagen: ' + sources.join(' · ');
      m.el.appendChild(s);
    }
    scrollDown(true);
  }

  function ensureAssistant() {
    if (!current) {
      const m = addMessage('assistant');
      current = { el: m.el, bubble: m.bubble, blocks: {}, order: [] };
    }
    return current;
  }

  let renderTimer = null;
  function scheduleRender(block) {
    if (renderTimer) return;
    renderTimer = setTimeout(() => {
      renderTimer = null;
      if (block.el && !block.done) renderMarkdown(block.text, block.el, false);
      scrollDown();
    }, 60);
  }
  function cancelRender() { if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; } }
  function finalizeBlock(b) {
    // endgültige Darstellung (mit Diagrammen, Vorschauen, Hervorhebung); keine verzögerte Zwischen-Darstellung mehr danach
    cancelRender();
    b.done = true;
    if (b.el) renderMarkdown(b.text, b.el, true);
  }

  function blockFor(index, kind) {
    const a = ensureAssistant();
    let b = a.blocks[index];
    if (!b) {
      b = { kind, text: '', el: null };
      a.blocks[index] = b;
      if (kind === 'text') { b.el = document.createElement('div'); a.bubble.appendChild(b.el); }
      if (kind === 'thinking') {
        b.el = document.createElement('details'); b.el.className = 'thinking-box';
        b.el.innerHTML = '<summary><span class="thinking">denkt nach</span></summary><div class="thinking-text"></div>';
        a.bubble.appendChild(b.el);
      }
    }
    return b;
  }

  function addTool(name, inputObj) {
    const a = ensureAssistant();
    const el = document.createElement('div');
    el.className = 'tool';
    let summary = name;
    const i = inputObj || {};
    if (name === 'Bash' && i.command) summary = 'Befehl: ' + i.command.split('\n')[0].slice(0, 120);
    else if (i.file_path) summary = name + ': ' + i.file_path;
    else if (i.pattern) summary = name + ': ' + i.pattern;
    else if (i.description) summary = name + ': ' + i.description;
    el.innerHTML = '<span class="name">' + escapeHtml(name) + '</span> <span class="sum">' + escapeHtml(summary.replace(/^[A-Za-z]+: /, '')) +
      '</span><div class="detail">' + escapeHtml(JSON.stringify(i, null, 1).slice(0, 4000)) + '</div>';
    el.onclick = () => el.classList.toggle('open');
    a.bubble.appendChild(el);
    scrollDown();
    return el;
  }

  function finishAssistant() {
    if (!current) return;
    const texts = [];
    for (const k of Object.keys(current.blocks).sort((a, b) => a - b)) {
      const b = current.blocks[k];
      if (b.kind === 'thinking' && b.el) { if (b.text.trim()) { b.el.querySelector('summary').innerHTML = 'Gedankengang (' + b.text.length + ' Zeichen)'; b.el.open = false; } else b.el.remove(); }
      if (b.kind === 'text' && b.el) { if (!b.done) finalizeBlock(b); texts.push(b.text); }
    }
    cancelRender();
    const md = texts.join('\n\n').trim();
    if (md) addActions(current.el, md, lastQuestion);
    current = null;
  }

  function addActions(msgEl, md, question) {
    const t = $('tpl-actions').content.cloneNode(true);
    const bar = t.querySelector('.actions');
    bar.querySelectorAll('button[data-prompt]').forEach(b => { b.onclick = () => { if (!busy) send({ cmd: 'send', text: b.dataset.prompt }); }; });
    bar.querySelector('.save-note').onclick = (e) => { send({ cmd: 'save_note', text: md, question }); e.target.textContent = 'Gespeichert ✓'; };
    msgEl.appendChild(bar);
  }

  // ---------- Karten von `rt` ----------
  function addCard(msg) {
    const a = addMessage('assistant');
    const card = document.createElement('div');
    card.className = 'card ' + msg.kind;
    card.innerHTML = '<div class="card-head">' + escapeHtml(msg.kind.toUpperCase()) + (msg.title ? ' · ' + escapeHtml(msg.title) : '') + '</div><div class="card-body"></div>';
    const body = card.querySelector('.card-body');
    const c = msg.content || '';
    if (msg.kind === 'latex') {
      for (const p of c.split(/\n\s*\n/).map(s => s.trim()).filter(Boolean)) {
        const d = document.createElement('div');
        try { katex.render(p.replace(/^\$\$|\$\$$/g, ''), d, { displayMode: true, throwOnError: true }); }
        catch (e) { d.innerHTML = '<div class="error">' + escapeHtml(e.message) + '</div>'; }
        body.appendChild(d);
      }
    } else if (msg.kind === 'md') renderMarkdown(c, body);
    else if (msg.kind === 'image') body.innerHTML = '<img src="' + escapeHtml(toUrl(c)) + '?t=' + Date.now() + '">';
    else if (msg.kind === 'video') body.innerHTML = '<video src="' + escapeHtml(toUrl(c)) + '" controls autoplay loop muted style="max-width:100%"></video>';
    else if (msg.kind === 'html' || msg.kind === 'url') body.innerHTML = '<iframe src="' + escapeHtml(toUrl(c)) + '" style="height:' + (msg.height || 480) + 'px"></iframe>';
    else if (msg.kind === 'mermaid') { body.innerHTML = '<div class="mermaid-src">' + escapeHtml(c) + '</div>'; renderMermaidIn(body); }
    else body.textContent = c;
    a.bubble.appendChild(card);
    scrollDown(true);
  }

  // ---------- Berechtigungsfrage ----------
  function askPermission(req) {
    const a = ensureAssistant();
    const t = $('tpl-permission').content.cloneNode(true);
    const box = t.querySelector('.permission');
    const inp = req.input || {};
    let desc = req.tool_name || '?';
    if (inp.command) desc += '\n' + inp.command; else desc += '\n' + JSON.stringify(inp, null, 1).slice(0, 2000);
    box.querySelector('.perm-body').textContent = desc;
    const done = (allow) => {
      send({ cmd: 'permission', request_id: req.request_id, allow, input: inp });
      box.querySelector('.perm-actions').innerHTML = '<span style="color:var(--ink-soft);font-size:12px">' + (allow ? 'erlaubt' : 'abgelehnt') + '</span>';
    };
    box.querySelector('.allow').onclick = () => done(true);
    box.querySelector('.deny').onclick = () => done(false);
    a.bubble.appendChild(box);
    scrollDown(true);
  }

  // ---------- Ereignisse von der App ----------
  window.chatEvent = function (ev) {
    if (typeof ev === 'string') ev = JSON.parse(ev);
    switch (ev.type) {
      case 'ready':
        cwd = ev.cwd; $('cwd').textContent = ev.cwd.replace(ev.home, '~');
        window.RT_USER = ev.user;
        fillModels(ev.models || [], ev.current || 'claude:');
        if (ev.background !== undefined) { bgId = ev.background || ''; bgDim = parseFloat(ev.background_dim ?? 0.5); loadBackgrounds().then(applyBackground); }
        $('ragmode').value = ev.ragmode || 'on';
        $('think').value = ev.think || 'auto';
        $('think').hidden = ev.backend !== 'command';     // nur bei lokalen Modellen sinnvoll
        $('ragmode').title = ev.ragfiles != null && ev.ragmode !== 'off'
          ? ('Ordner rag/: ' + ev.ragfiles + ' Datei(en). Lege Folien, Skripte, Notizen hinein.')
          : 'Unterlagen aus dem Ordner rag/ im Arbeitsordner (gilt für diesen Ordner)';
        $('perm').value = ev.perm || 'default';
        $('perm').disabled = !!(ev.locked && ev.locked.perm) || ev.backend === 'command';
        $('perm').title = $('perm').disabled ? (ev.backend === 'command' ? 'Lokale Modelle haben keine Werkzeuge' : 'im Profil (richterm.md) festgelegt') : 'Berechtigungen';
        $('btn-profile').textContent = ev.profile ? 'Profil' : 'Profil anlegen';
        if (ev.replay && ev.replay.length && !messages.querySelector('.msg')) {
          // letzte Einträge aus richterm-verlauf.md zur Orientierung anzeigen
          const sep = document.createElement('div'); sep.className = 'replay-sep';
          sep.textContent = 'Aus dem Verlauf (richterm-verlauf.md)';
          $('welcome').hidden = true; messages.appendChild(sep);
          for (const t of ev.replay) {
            const m = addMessage(t.role); m.el.classList.add('replay');
            if (t.role === 'user') { m.bubble.textContent = t.text; lastQuestion = t.text; }
            else { renderMarkdown(t.text, m.bubble); addActions(m.el, t.text, lastQuestion); }
          }
          const sep2 = document.createElement('div'); sep2.className = 'replay-sep'; sep2.textContent = 'Jetzt';
          messages.appendChild(sep2);
          scrollDown(true);
        }
        $('btn-profile').title = 'richterm.md in ' + ev.cwd.replace(ev.home, '~') + (ev.backend === 'command' ? ' · Backend: Kommandozeile' : ' · Backend: Claude Code');
        setStatus(ev.note || '');
        break;
      case 'card': addCard(ev.msg); break;
      case 'ask_folder': {
        const p = window.prompt('Arbeitsordner (Pfad):', ev.current || '');
        if (p) send({ cmd: 'set_folder', path: p });
        break;
      }
      case 'models': fillModels(ev.models || [], ev.current || 'claude:'); break;
      case 'user_sent': addUser(ev.text, ev.images, ev.sources); lastQuestion = ev.text; setBusy(true); setStatus('Die KI arbeitet …'); break;
      case 'status': setStatus(ev.text); break;
      case 'error': { const m = addMessage('assistant'); m.bubble.innerHTML = '<div class="error">' + escapeHtml(ev.text) + '</div>'; setBusy(false); break; }
      case 'claude': handleClaude(ev.msg); break;
    }
  };

  function handleClaude(m) {
    if (m.type === 'system' && m.subtype === 'status' && m.text) { setStatus(m.text); return; }
    if (m.type === 'system' && m.subtype === 'init') {
      setStatus('Sitzung ' + (m.model || '') + ' · ' + (m.cwd || ''));
      return;
    }
    if (m.type === 'stream_event') {
      const e = m.event;
      if (e.type === 'message_start') { if (current) current.blocks = {}; return; }
      if (e.type === 'content_block_start') {
        const cb = e.content_block;
        if (cb.type === 'text') blockFor(e.index, 'text');
        else if (cb.type === 'thinking') blockFor(e.index, 'thinking');
      } else if (e.type === 'content_block_delta') {
        const d = e.delta;
        if (d.type === 'text_delta') { const b = blockFor(e.index, 'text'); b.text += d.text; scheduleRender(b); }
        else if (d.type === 'thinking_delta' && d.thinking) {
          const b = blockFor(e.index, 'thinking'); b.text += d.thinking;
          const t = b.el && b.el.querySelector('.thinking-text'); if (t) { t.textContent = b.text; scrollDown(); }
        }
      } else if (e.type === 'content_block_stop') {
        const b = current && current.blocks[e.index];
        if (b && b.kind === 'thinking' && b.el) {
          if (b.text.trim()) { b.el.querySelector('summary').innerHTML = 'Gedankengang (' + b.text.length + ' Zeichen)'; b.el.open = false; }
          else b.el.remove();
        }
        if (b && b.kind === 'text' && b.el) finalizeBlock(b);
      }
      return;
    }
    if (m.type === 'assistant') {
      // vollständige Nachricht: Werkzeugaufrufe daraus anzeigen
      for (const c of (m.message && m.message.content) || []) {
        if (c.type === 'tool_use') addTool(c.name, c.input);
      }
      return;
    }
    if (m.type === 'user') {
      // Werkzeug-Ergebnisse: an den letzten Werkzeug-Eintrag hängen
      const content = (m.message && m.message.content) || [];
      for (const c of (Array.isArray(content) ? content : [])) {
        if (c.type === 'tool_result') {
          const tools = current ? current.bubble.querySelectorAll('.tool') : [];
          const last = tools[tools.length - 1];
          const txt = typeof c.content === 'string' ? c.content : (c.content || []).map(x => x.text || '').join('\n');
          if (last && txt) {
            const d = last.querySelector('.detail');
            d.textContent += '\n\n— Ergebnis —\n' + txt.slice(0, 4000);
          }
        }
      }
      return;
    }
    if (m.type === 'control_request' && m.request && m.request.subtype === 'can_use_tool') {
      askPermission({ request_id: m.request_id, tool_name: m.request.tool_name, input: m.request.input });
      return;
    }
    if (m.type === 'result') {
      finishAssistant();
      setBusy(false);
      const cost = m.total_cost_usd ? ' · ' + m.total_cost_usd.toFixed(3) + ' $' : '';
      const secs = m.duration_ms ? (m.duration_ms / 1000).toFixed(1) + ' s' : '';
      setStatus((m.is_error ? 'Fehler: ' + (m.result || '') : 'Fertig') + (secs ? ' · ' + secs : '') + cost);
      if (m.is_error && m.result) { const mm = addMessage('assistant'); mm.bubble.innerHTML = '<div class="error">' + escapeHtml(m.result) + '</div>'; }
      return;
    }
  }

  // ---------- Anhänge ----------
  function renderAttachments() {
    const box = $('attachments');
    box.innerHTML = '';
    attachments.forEach((a, i) => {
      const chip = document.createElement('span'); chip.className = 'chip';
      if (a.mime && a.mime.startsWith('image/') && a.data) {
        const img = document.createElement('img'); img.src = a.data; chip.appendChild(img);
      } else { chip.appendChild(document.createTextNode('📄 ')); }
      chip.appendChild(document.createTextNode(a.name));
      const x = document.createElement('button'); x.textContent = '✕'; x.title = 'Entfernen';
      x.onclick = () => { attachments.splice(i, 1); renderAttachments(); };
      chip.appendChild(x);
      box.appendChild(chip);
    });
    box.hidden = attachments.length === 0;
  }
  function addFile(file) {
    if (!file) return;
    if (file.size > 25 * 1024 * 1024) { setStatus('Datei zu groß (max. 25 MB): ' + file.name); return; }
    const reader = new FileReader();
    reader.onload = () => { attachments.push({ name: file.name || 'bild.png', mime: file.type || 'application/octet-stream', data: reader.result }); renderAttachments(); };
    reader.readAsDataURL(file);
  }
  function addPaths(uriList) {
    for (const line of uriList.split(/\r?\n/)) {
      const u = line.trim();
      if (!u || u.startsWith('#') || !u.startsWith('file://')) continue;
      const name = decodeURIComponent(u.split('/').pop());
      attachments.push({ name, mime: '', path: u });
    }
    renderAttachments();
  }
  $('btn-attach').onclick = () => $('file-input').click();
  $('file-input').onchange = (e) => { [...e.target.files].forEach(addFile); e.target.value = ''; };
  document.addEventListener('paste', (e) => {
    const items = [...(e.clipboardData && e.clipboardData.items || [])];
    const files = items.filter(it => it.kind === 'file').map(it => it.getAsFile()).filter(Boolean);
    if (files.length) { e.preventDefault(); files.forEach(addFile); input.focus(); }
  });
  ['dragenter', 'dragover'].forEach(ev => document.addEventListener(ev, e => { e.preventDefault(); document.body.classList.add('dragging'); }));
  ['dragleave', 'drop'].forEach(ev => document.addEventListener(ev, e => { if (ev === 'drop' || e.target === document.body) document.body.classList.remove('dragging'); }));
  document.addEventListener('drop', (e) => {
    e.preventDefault();
    const dt = e.dataTransfer; if (!dt) return;
    const uris = dt.getData('text/uri-list');
    if (uris && uris.includes('file://')) addPaths(uris);        // echte Pfade: Datei bleibt, wo sie ist
    else [...(dt.files || [])].forEach(addFile);
    input.focus();
  });

  // ---------- Eingabe ----------
  function submit() {
    const text = input.value.trim();
    if ((!text && !attachments.length) || busy) return;
    input.value = ''; autosize();
    send({ cmd: 'send', text, attachments });
    attachments = []; renderAttachments();
  }
  function autosize() {
    // Höhe an den Inhalt anpassen: erst zurücksetzen, dann auf die Inhaltshöhe setzen (max. 40 % des Fensters)
    input.style.height = '0px';
    const h = Math.min(input.scrollHeight, Math.floor(window.innerHeight * 0.4));
    input.style.height = Math.max(h, 42) + 'px';
  }
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
  });
  input.addEventListener('input', autosize);
  window.addEventListener('resize', autosize);
  $('btn-send').onclick = submit;
  $('btn-stop').onclick = () => send({ cmd: 'interrupt' });
  $('btn-new').onclick = () => { send({ cmd: 'new' }); messages.querySelectorAll('.msg').forEach(m => m.remove()); $('welcome').hidden = false; current = null; setBusy(false); };
  $('btn-folder').onclick = () => send({ cmd: 'choose_folder' });
  $('btn-profile').onclick = () => send({ cmd: 'profile_edit' });
  $('btn-reload').onclick = () => send({ cmd: 'profile_reload' });
  // Beim Öffnen des Menüs die Liste der lokalen Modelle aktualisieren (neu geladene erscheinen sofort)
  $('model').addEventListener('focus', () => send({ cmd: 'refresh_models' }));
  $('model').onchange = () => {
    if ($('model').value === '__pull__') { $('pull-box').hidden = false; $('pull-name').focus(); return; }
    send({ cmd: 'choose_model', value: $('model').value });
  };
  const doPull = () => { const n = $('pull-name').value.trim(); if (n) { send({ cmd: 'pull_model', name: n }); $('pull-box').hidden = true; $('pull-name').value = ''; } };
  $('btn-pull').onclick = doPull;
  $('pull-name').addEventListener('keydown', e => { if (e.key === 'Enter') doPull(); if (e.key === 'Escape') $('btn-pull-cancel').click(); });
  $('btn-pull-cancel').onclick = () => { $('pull-box').hidden = true; send({ cmd: 'ready' }); };
  $('perm').onchange = () => send({ cmd: 'set', key: 'perm', value: $('perm').value });
  $('ragmode').onchange = () => send({ cmd: 'set_ragmode', value: $('ragmode').value });
  $('think').onchange = () => send({ cmd: 'set_think', value: $('think').value });
  $('btn-theme').onclick = () => { theme = theme === 'light' ? 'dark' : 'light'; applyPrefs(); };
  document.addEventListener('keydown', e => {
    if (e.ctrlKey && (e.key === '+' || e.key === '=')) { fontSize = Math.min(fontSize + 1, 40); applyPrefs(); e.preventDefault(); }
    if (e.ctrlKey && e.key === '-') { fontSize = Math.max(fontSize - 1, 9); applyPrefs(); e.preventDefault(); }
  });
  applyPrefs();
  input.focus();
  connectEvents(() => send({ cmd: 'ready' }));
})();
