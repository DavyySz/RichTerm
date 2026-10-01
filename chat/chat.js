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
  let busy = false;

  // ---------- Kommunikation mit der App ----------
  function send(obj) {
    try { window.webkit.messageHandlers.app.postMessage(JSON.stringify(obj)); }
    catch (e) { console.error('kein App-Kanal', e); }
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
    mermaid.initialize({ startOnLoad: false, theme: theme === 'light' ? 'default' : 'dark' });
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
          '<button class="pv-code">Code</button><button class="pv-reload">Neu laden</button>' +
          '<button class="pv-open">Im Browser</button></div>' +
          '<iframe sandbox="allow-scripts allow-same-origin"></iframe>' +
          '<pre hidden><code>' + escapeHtml(code) + '</code></pre></div>';
      }
      return '<pre><code class="language-' + escapeHtml(lang) + '">' + escapeHtml(code) + '</code></pre>';
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
    renderMermaidIn(target);
  }

  async function renderMermaidIn(el) {
    for (const b of el.querySelectorAll('.mermaid-src')) {
      const src = b.textContent;
      const holder = document.createElement('div'); holder.className = 'mermaid';
      b.replaceWith(holder);
      try { holder.innerHTML = (await mermaid.render('mm-' + (++counter), src)).svg; }
      catch (e) { holder.innerHTML = '<pre class="error">Mermaid: ' + escapeHtml(String(e)) + '\n' + escapeHtml(src) + '</pre>'; }
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

  function addUser(text) {
    const m = addMessage('user');
    m.bubble.textContent = text;
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
      if (block.el) renderMarkdown(block.text, block.el, false);
      scrollDown();
    }, 60);
  }

  function blockFor(index, kind) {
    const a = ensureAssistant();
    let b = a.blocks[index];
    if (!b) {
      b = { kind, text: '', el: null };
      a.blocks[index] = b;
      if (kind === 'text') { b.el = document.createElement('div'); a.bubble.appendChild(b.el); }
      if (kind === 'thinking') { b.el = document.createElement('div'); b.el.className = 'thinking'; b.el.textContent = 'denkt nach'; a.bubble.appendChild(b.el); }
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
    for (const k in current.blocks) {
      const b = current.blocks[k];
      if (b.kind === 'thinking' && b.el) b.el.remove();
      if (b.kind === 'text' && b.el) renderMarkdown(b.text, b.el);
    }
    current = null;
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
        if (ev.model) $('model').value = ev.model;
        if (ev.perm) $('perm').value = ev.perm;
        setStatus(ev.note || '');
        break;
      case 'card': addCard(ev.msg); break;
      case 'user_sent': addUser(ev.text); setBusy(true); setStatus('Claude arbeitet …'); break;
      case 'status': setStatus(ev.text); break;
      case 'error': { const m = addMessage('assistant'); m.bubble.innerHTML = '<div class="error">' + escapeHtml(ev.text) + '</div>'; setBusy(false); break; }
      case 'claude': handleClaude(ev.msg); break;
    }
  };

  function handleClaude(m) {
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
      } else if (e.type === 'content_block_stop') {
        const b = current && current.blocks[e.index];
        if (b && b.kind === 'thinking' && b.el) b.el.remove();
        if (b && b.kind === 'text' && b.el) renderMarkdown(b.text, b.el);
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

  // ---------- Eingabe ----------
  function submit() {
    const text = input.value.trim();
    if (!text || busy) return;
    input.value = ''; autosize();
    send({ cmd: 'send', text });
  }
  function autosize() { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, window.innerHeight * 0.4) + 'px'; }
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
  });
  input.addEventListener('input', autosize);
  $('btn-send').onclick = submit;
  $('btn-stop').onclick = () => send({ cmd: 'interrupt' });
  $('btn-new').onclick = () => { send({ cmd: 'new' }); messages.querySelectorAll('.msg').forEach(m => m.remove()); $('welcome').hidden = false; current = null; setBusy(false); };
  $('btn-folder').onclick = () => send({ cmd: 'choose_folder' });
  $('model').onchange = () => send({ cmd: 'set', key: 'model', value: $('model').value });
  $('perm').onchange = () => send({ cmd: 'set', key: 'perm', value: $('perm').value });
  $('btn-theme').onclick = () => { theme = theme === 'light' ? 'dark' : 'light'; applyPrefs(); };
  document.addEventListener('keydown', e => {
    if (e.ctrlKey && (e.key === '+' || e.key === '=')) { fontSize = Math.min(fontSize + 1, 40); applyPrefs(); e.preventDefault(); }
    if (e.ctrlKey && e.key === '-') { fontSize = Math.max(fontSize - 1, 9); applyPrefs(); e.preventDefault(); }
  });
  applyPrefs();
  input.focus();
  send({ cmd: 'ready' });
})();
