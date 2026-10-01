/* RichTerm Anzeige: nimmt Inhalte von der App entgegen (rtAdd) und rendert sie als Karten. */
(function () {
  const feed = document.getElementById('feed');
  const empty = document.getElementById('empty');
  let counter = 0;
  let fontSize = parseFloat(localStorage.getItem('rt-font') || '17');
  let theme = localStorage.getItem('rt-theme') || 'dark';

  function applyPrefs() {
    document.documentElement.style.setProperty('--font-size', fontSize + 'px');
    if (theme === 'light') document.documentElement.setAttribute('data-theme', 'light');
    else document.documentElement.removeAttribute('data-theme');
    document.getElementById('btn-theme').textContent = theme === 'light' ? '☾' : '☀';
    mermaid.initialize({ startOnLoad: false, theme: theme === 'light' ? 'default' : 'dark' });
    localStorage.setItem('rt-font', fontSize);
    localStorage.setItem('rt-theme', theme);
  }

  // file://-Pfade über den eingebauten Server laden (WebKit erlaubt kein file:// innerhalb von http://)
  function toUrl(c) { return c.startsWith('file://') ? '/file' + c.slice(7) : c; }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function card(kind, title) {
    counter += 1;
    const el = document.createElement('article');
    el.className = 'card ' + kind;
    el.id = 'card-' + counter;
    const t = new Date();
    const hh = String(t.getHours()).padStart(2, '0'), mm = String(t.getMinutes()).padStart(2, '0');
    el.innerHTML =
      '<div class="card-head"><span class="kind">' + escapeHtml(kind) + '</span>' +
      (title ? '<span class="ttl">' + escapeHtml(title) + '</span>' : '') +
      '<span class="spacer"></span><span>' + hh + ':' + mm + '</span>' +
      '<span class="x" title="Entfernen">✕</span></div><div class="card-body"></div>';
    el.querySelector('.x').onclick = () => el.remove();
    return el;
  }

  function renderMathIn(el) {
    renderMathInElement(el, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '\\[', right: '\\]', display: true },
        { left: '\\(', right: '\\)', display: false },
        { left: '$', right: '$', display: false },
      ],
      throwOnError: false,
      macros: { '\\R': '\\mathbb{R}', '\\N': '\\mathbb{N}' },
    });
  }

  async function renderMermaidIn(el) {
    const blocks = el.querySelectorAll('pre code.language-mermaid, .mermaid-src');
    for (const b of blocks) {
      const src = b.textContent;
      const holder = document.createElement('div');
      holder.className = 'mermaid';
      const target = b.closest('pre') || b;
      target.replaceWith(holder);
      try {
        const { svg } = await mermaid.render('mm-' + (++counter), src);
        holder.innerHTML = svg;
      } catch (e) {
        holder.innerHTML = '<div class="error">Mermaid: ' + escapeHtml(String(e)) + '</div>';
      }
    }
  }

  const renderers = {
    latex(body, c) {
      // mehrere Formeln durch Leerzeilen getrennt
      const parts = c.split(/\n\s*\n/).map(s => s.trim()).filter(Boolean);
      for (const p of parts) {
        const d = document.createElement('div');
        try {
          katex.render(p.replace(/^\$\$|\$\$$/g, ''), d, { displayMode: true, throwOnError: true });
        } catch (e) {
          d.innerHTML = '<div class="error">' + escapeHtml(e.message) + '\n\n' + escapeHtml(p) + '</div>';
        }
        body.appendChild(d);
      }
    },
    md(body, c) {
      // Formeln vor dem Markdown-Parser schützen (sonst werden _ und * darin als Formatierung gelesen)
      const stash = [];
      const protectedSrc = c.replace(/\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^$\n]+?\$/g, m => {
        stash.push(m);
        return '\u0000MATH' + (stash.length - 1) + '\u0000';
      });
      let html = marked.parse(protectedSrc, { breaks: false, gfm: true });
      html = html.replace(/\u0000MATH(\d+)\u0000/g, (_, i) => escapeHtml(stash[+i]));
      body.innerHTML = html;
      renderMathIn(body);
      renderMermaidIn(body);
    },
    text(body, c) { body.textContent = c; },
    image(body, c) {
      const img = document.createElement('img');
      img.src = toUrl(c) + (c.startsWith('file:') ? '?t=' + Date.now() : '');
      img.onerror = () => { body.innerHTML = '<div class="error">Bild konnte nicht geladen werden:\n' + escapeHtml(c) + '</div>'; };
      body.appendChild(img);
    },
    video(body, c) {
      const v = document.createElement('video');
      v.src = toUrl(c); v.controls = true; v.autoplay = true; v.loop = true; v.muted = true;
      body.appendChild(v);
    },
    html(body, c, opts) {
      const f = document.createElement('iframe');
      f.style.height = (opts.height || 480) + 'px';
      if (c.startsWith('file://') || c.startsWith('http') || c.startsWith('/')) f.src = toUrl(c); else f.srcdoc = c;
      body.appendChild(f);
    },
    url(body, c, opts) {
      const f = document.createElement('iframe');
      f.src = c;
      f.style.height = (opts.height || 600) + 'px';
      body.appendChild(f);
    },
    mermaid(body, c) {
      const pre = document.createElement('div');
      pre.className = 'mermaid-src';
      pre.textContent = c;
      body.appendChild(pre);
      renderMermaidIn(body);
    },
  };

  window.rtAdd = function (msg) {
    if (typeof msg === 'string') msg = JSON.parse(msg);
    const kind = msg.kind || 'text';
    if (kind === 'clear') { feed.querySelectorAll('.card').forEach(c => c.remove()); empty.hidden = false; return; }
    empty.hidden = true;
    const el = card(kind, msg.title || '');
    const body = el.querySelector('.card-body');
    const r = renderers[kind] || renderers.text;
    try { r(body, msg.content || '', msg); }
    catch (e) { body.innerHTML = '<div class="error">' + escapeHtml(String(e)) + '</div>'; }
    feed.appendChild(el);
    el.scrollIntoView({ block: 'start', behavior: 'smooth' });
  };

  document.getElementById('btn-clear').onclick = () => rtAdd({ kind: 'clear' });
  document.getElementById('btn-bigger').onclick = () => { fontSize = Math.min(fontSize + 1, 40); applyPrefs(); };
  document.getElementById('btn-smaller').onclick = () => { fontSize = Math.max(fontSize - 1, 9); applyPrefs(); };
  document.getElementById('btn-theme').onclick = () => { theme = theme === 'light' ? 'dark' : 'light'; applyPrefs(); };
  document.addEventListener('keydown', e => {
    if (e.ctrlKey && (e.key === '+' || e.key === '=')) { fontSize += 1; applyPrefs(); e.preventDefault(); }
    if (e.ctrlKey && e.key === '-') { fontSize -= 1; applyPrefs(); e.preventDefault(); }
  });
  applyPrefs();
})();
