/* Render-Worker: Markdown + KaTeX + Code-Hervorhebung in einem eigenen Thread (anderer CPU-Kern).
   Der Hauptthread bleibt frei zum Zeichnen; er setzt nur noch das fertige HTML ein. */
importScripts('../vendor/marked/marked.min.js', '../vendor/katex/katex.min.js', '../vendor/hljs/highlight.min.js');

const LANG_ALIAS = { js: 'javascript', ts: 'typescript', py: 'python', sh: 'bash', shell: 'bash', zsh: 'bash',
  yml: 'yaml', md: 'markdown', 'c++': 'cpp', 'c#': 'csharp', cs: 'csharp', rs: 'rust', kt: 'kotlin', jsx: 'javascript',
  tsx: 'typescript', dockerfile: 'dockerfile', plaintext: 'plaintext', text: 'plaintext', txt: 'plaintext', console: 'bash' };

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function toUrl(p, cwd, user) {
  if (/^(https?:|data:|\/file\/|\/html\/)/.test(p)) return p;
  if (p.startsWith('file://')) return '/file' + p.slice(7);
  if (p.startsWith('~/')) return '/file/home/' + user + '/' + p.slice(2);
  if (p.startsWith('/')) return '/file' + p;
  return '/file' + (cwd === '~' ? '' : cwd) + '/' + p;
}
function renderMath(seg) {
  let tex, display;
  if (seg.startsWith('$$')) { tex = seg.slice(2, -2); display = true; }
  else if (seg.startsWith('\\[')) { tex = seg.slice(2, -2); display = true; }
  else if (seg.startsWith('\\(')) { tex = seg.slice(2, -2); display = false; }
  else { tex = seg.slice(1, -1); display = false; }
  try {
    return katex.renderToString(tex, { displayMode: display, throwOnError: false, macros: { '\\R': '\\mathbb{R}', '\\N': '\\mathbb{N}' } });
  } catch (e) { return escapeHtml(seg); }
}
function highlight(code, lang) {
  lang = LANG_ALIAS[(lang || '').toLowerCase()] || (lang || '').toLowerCase();
  try {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
    if (!lang && code.length < 20000) return hljs.highlightAuto(code).value;
  } catch (e) { /* unverändert */ }
  return escapeHtml(code);
}

function render(src, opts) {
  const { final, withMath, cwd, user } = opts;
  const stash = [];
  const protectedSrc = src.replace(/(```[\s\S]*?```)|(`[^`\n]+`)|(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$(?!\s)[^$\n]+?(?<!\s)\$)/g,
    (m, code, inline, math) => { if (math) { stash.push(math); return '\u0000M' + (stash.length - 1) + '\u0000'; } return m; });
  const renderer = new marked.Renderer();
  renderer.image = (href, title, text) => '<img src="' + escapeHtml(toUrl(href, cwd, user)) + '" alt="' + escapeHtml(text || '') + '">';
  renderer.code = (code, lang) => {
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
    return '<pre>' + label + '<code class="language-' + escapeHtml(lang) + ' hljs" data-highlighted="1">' + highlight(code, lang) + '</code></pre>';
  };
  let html = marked.parse(protectedSrc, { gfm: true, breaks: false, renderer });
  html = html.replace(/\u0000M(\d+)\u0000/g, (_, i) => (withMath || final) ? renderMath(stash[+i]) : escapeHtml(stash[+i]));
  return html;
}

onmessage = (e) => {
  const { id, src, opts } = e.data;
  try { postMessage({ id, html: render(src, opts) }); }
  catch (err) { postMessage({ id, error: String(err) }); }
};
