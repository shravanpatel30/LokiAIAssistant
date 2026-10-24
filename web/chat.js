// Loki chat renderer: markdown via marked, math via KaTeX. Fully offline.
//
// Python talks to this page through two functions:
//   addMessage(role, text, reasoning, images)   role = "user" | "assistant" | "system"
//   clearMessages()
(function () {
  const root = typeof window !== "undefined" ? window : globalThis;
  const snippets = [];   // code-block text, indexed by the copy buttons

  function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ---------- Math ----------
  // Display:  $$...$$   \[...\]      Inline:  \(...\)   $...$
  // An escaped \$ is a literal dollar sign, never a delimiter — hence (?<!\\)
  const MATH_RE = /(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<!\\)\$(?!\$)([^$\n]+?)(?<!\\)\$/g;
  // Fenced and inline code are left alone (a $ inside code is not math)
  const CODE_SPLIT_RE = /(```[\s\S]*?(?:```|$)|`[^`\n]+`)/g;
  const PLACEHOLDER_RE = /@@KTX(\d+)@@/g;

  // Each equation is wrapped in <span class="math" data-tex="..."> so the copy
  // handler (bottom of this file) can put the LaTeX source on the clipboard.
  function renderMath(tex, display) {
    tex = tex.trim();
    let html;
    try {
      html = katex.renderToString(tex, {
        displayMode: display, throwOnError: false, output: "html",
      });
    } catch (e) {
      html = escapeHtml(tex);
    }
    return '<span class="math" data-display="' + (display ? 1 : 0) +
           '" data-tex="' + escapeHtml(tex) + '">' + html + "</span>";
  }

  // Render math to HTML *before* markdown runs, and swap it for placeholders.
  // Otherwise marked eats the backslashes and underscores inside equations.
  function protectMath(src) {
    const stash = [];
    const parts = src.split(CODE_SPLIT_RE);   // even = prose, odd = code
    for (let i = 0; i < parts.length; i += 2) {
      parts[i] = parts[i].replace(MATH_RE, (m, dd, sq, paren, dollar) => {
        let html;
        if (dd !== undefined) html = renderMath(dd, true);
        else if (sq !== undefined) html = renderMath(sq, true);
        else if (paren !== undefined) html = renderMath(paren, false);
        else {
          // "$5 and $10" is money, not math: starts with a digit, ends in a space
          if (/^\d/.test(dollar) && /\s$/.test(dollar)) return m;
          html = renderMath(dollar, false);
        }
        stash.push(html);
        return "@@KTX" + (stash.length - 1) + "@@";
      });
      // Neutralize any raw HTML the model emits in prose
      parts[i] = parts[i].replace(/</g, "&lt;");
    }
    return [parts.join(""), stash];
  }

  // ---------- Markdown ----------
  // If web/vendor/ is missing (setup_web_assets.py not run), fall back to
  // plain text instead of showing nothing.
  const LIBS_OK = typeof marked !== "undefined" && typeof katex !== "undefined";
  root.LIBS_OK = LIBS_OK;

  if (LIBS_OK) marked.use({
    gfm: true,
    breaks: false,
    renderer: {
      // marked v12 passes (text, lang); v13+ passes a token object. Handle both.
      code(arg, infostring) {
        const text = typeof arg === "object" ? arg.text : arg;
        const lang = ((typeof arg === "object" ? arg.lang : infostring) || "")
          .trim().split(/\s+/)[0];
        const id = snippets.push(text) - 1;
        const label = lang ? escapeHtml(lang.toUpperCase()) : "CODE";
        return (
          '<div class="code-block">' +
            '<div class="code-header">' +
              '<span class="code-lang">' + label + "</span>" +
              '<button class="copy-btn" onclick="copySnippet(' + id + ', this)">📋 Copy</button>' +
            "</div>" +
            "<pre><code>" + escapeHtml(text) + "</code></pre>" +
          "</div>"
        );
      },
    },
  });

  function renderMarkdown(src) {
    if (!LIBS_OK) return "<p style='white-space:pre-wrap'>" + escapeHtml(src) + "</p>";
    const [safe, stash] = protectMath(src);
    const html = marked.parse(safe);
    return html.replace(PLACEHOLDER_RE, (_, i) => stash[+i]);
  }

  // ---------- Clipboard ----------
  function fallbackCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    ta.remove();
    return ok;
  }

  root.copySnippet = function (id, btn) {
    const text = snippets[id];
    const done = () => {
      btn.textContent = "✓ Copied";
      setTimeout(() => { btn.textContent = "📋 Copy"; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () => { if (fallbackCopy(text)) done(); });
    } else if (fallbackCopy(text)) {
      done();
    }
  };

  // ---------- Messages ----------
  const LABELS = { user: "you:", assistant: "assistant:", system: "·:" };

  // Thinking-mode reasoning, collapsed above the answer
  function collapsible(content, what) {
    const details = document.createElement("details");
    details.className = "reasoning";
    const summary = document.createElement("summary");
    summary.textContent = "Show " + what;
    const inner = document.createElement("div");
    inner.className = "reasoning-body";
    inner.innerHTML = renderMarkdown(content);
    details.append(summary, inner);
    details.addEventListener("toggle", () => {
      summary.textContent = (details.open ? "Hide " : "Show ") + what;
    });
    return details;
  }

  root.addMessage = function (role, text, reasoning, images) {
    const wrap = document.createElement("div");
    wrap.className = "msg " + role;

    const label = document.createElement("span");
    label.className = "role";
    label.textContent = LABELS[role] || role + ":";

    const body = document.createElement("div");
    body.className = "body";
    if (role === "assistant") body.innerHTML = renderMarkdown(text);
    else body.textContent = text;   // user/system text shown as typed

    wrap.append(label);
    if (role === "assistant" && reasoning) wrap.append(collapsible(reasoning, "reasoning"));
    wrap.append(body);
    if (images && images.length) {            // thumbnails of attached images
      const thumbs = document.createElement("div");
      thumbs.className = "thumbs";
      for (const url of images) {
        const a = document.createElement("a");
        a.href = url;                           // click: opens in your image viewer
        a.title = "Open image";
        const img = document.createElement("img");
        img.src = url;
        img.addEventListener("load", () => window.scrollTo(0, document.body.scrollHeight));
        a.append(img);
        thumbs.append(a);
      }
      wrap.append(thumbs);
    }
    document.getElementById("messages").appendChild(wrap);
    // Wait a frame so the new message (and its math) has its final height
    requestAnimationFrame(() => window.scrollTo(0, document.body.scrollHeight));
  };

  root.clearMessages = function () {
    document.getElementById("messages").innerHTML = "";
    snippets.length = 0;
  };

  // ---------- Copy as LaTeX ----------
  // When a selection includes rendered math, the clipboard gets plain text
  // with every equation as LaTeX ($...$ inline, $$...$$ on its own line).
  // Only plain text is set, so every app — Overleaf, Word, a chat box —
  // pastes the same LaTeX instead of the rendered symbols.
  function closestMath(node) {
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    return el ? el.closest(".math") : null;
  }

  function selectionAsLatex(range) {
    // Grow the selection to whole equations (selecting half of one counts)
    const r = range.cloneRange();
    const startMath = closestMath(r.startContainer);
    const endMath = closestMath(r.endContainer);
    if (startMath) r.setStartBefore(startMath);
    if (endMath) r.setEndAfter(endMath);

    const frag = r.cloneContents();
    if (!frag.querySelector(".math")) return null;    // no math: normal copy

    // Collapsed reasoning isn't copied; expanded reasoning is (minus its toggle)
    frag.querySelectorAll("details.reasoning").forEach((d) => {
      if (!d.hasAttribute("open")) d.remove();
      else d.querySelectorAll("summary").forEach((s) => s.remove());
    });
    frag.querySelectorAll(".math").forEach((m) => {
      const tex = m.getAttribute("data-tex");
      if (m.getAttribute("data-display") === "1") {
        const block = document.createElement("div");
        block.textContent = "$$" + tex + "$$";
        m.replaceWith(block);
      } else {
        m.replaceWith("$" + tex + "$");
      }
    });
    // Code blocks paste as fenced code, without the "PYTHON 📋 Copy" header
    frag.querySelectorAll(".code-block").forEach((cb) => {
      const lang = (cb.querySelector(".code-lang") || {}).textContent || "";
      const code = (cb.querySelector("pre") || cb).textContent.replace(/\n$/, "");
      const block = document.createElement("p");
      block.style.whiteSpace = "pre";
      block.textContent = "```" + (lang === "CODE" ? "" : lang.toLowerCase()) + "\n" + code + "\n```";
      cb.replaceWith(block);
    });
    frag.querySelectorAll("ol").forEach((ol) => {
      const start = parseInt(ol.getAttribute("start") || "1", 10);
      [...ol.children].forEach((li, i) => li.prepend(start + i + ". "));
    });
    frag.querySelectorAll("ul > li").forEach((li) => li.prepend("- "));

    // innerText gives proper line breaks between paragraphs, list items, etc.,
    // but only for elements that are on the page — so lay it out off-screen.
    const box = document.createElement("div");
    box.style.cssText = "position:fixed; left:-100000px; top:0; width:800px;";
    box.appendChild(frag);
    document.body.appendChild(box);
    const text = box.innerText;
    box.remove();
    return text.replace(/\n{3,}/g, "\n\n").trim();
  }

  if (typeof document !== "undefined") {
    document.addEventListener("copy", (event) => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !event.clipboardData) return;
      const text = selectionAsLatex(sel.getRangeAt(0));
      if (text === null) return;
      event.clipboardData.setData("text/plain", text);
      event.preventDefault();
    });
  }

  root.renderMarkdown = renderMarkdown;   // handy for testing
})();
