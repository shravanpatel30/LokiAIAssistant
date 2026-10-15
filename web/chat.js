// Loki chat renderer: markdown via marked, math via KaTeX. Fully offline.
//
// Python talks to this page through two functions:
//   addMessage(role, text)   role = "user" | "assistant" | "system"
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
  const MATH_RE = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?!\$)([^$\n]+?)\$/g;
  // Fenced and inline code are left alone (a $ inside code is not math)
  const CODE_SPLIT_RE = /(```[\s\S]*?(?:```|$)|`[^`\n]+`)/g;
  const PLACEHOLDER_RE = /@@KTX(\d+)@@/g;

  function renderMath(tex, display) {
    try {
      return katex.renderToString(tex.trim(), {
        displayMode: display, throwOnError: false, output: "html",
      });
    } catch (e) {
      return escapeHtml(tex);
    }
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

  root.addMessage = function (role, text) {
    const wrap = document.createElement("div");
    wrap.className = "msg " + role;

    const label = document.createElement("span");
    label.className = "role";
    label.textContent = LABELS[role] || role + ":";

    const body = document.createElement("div");
    body.className = "body";
    if (role === "assistant") body.innerHTML = renderMarkdown(text);
    else body.textContent = text;   // user/system text shown as typed

    wrap.append(label, body);
    document.getElementById("messages").appendChild(wrap);
    // Wait a frame so the new message (and its math) has its final height
    requestAnimationFrame(() => window.scrollTo(0, document.body.scrollHeight));
  };

  root.clearMessages = function () {
    document.getElementById("messages").innerHTML = "";
    snippets.length = 0;
  };

  root.renderMarkdown = renderMarkdown;   // handy for testing
})();
