"""Compare local models on Loki-style tasks: equations → LaTeX, reading plots,
tables and circuits, logic puzzles, and physics questions.

Usage (Ollama running, from the project folder):
    python model_eval/run_eval.py                        # default model setups
    python model_eval/run_eval.py --only images          # just the image tests
    python model_eval/run_eval.py --only reasoning       # just the text tests
    python model_eval/run_eval.py --models "qwen3-vl:8b,qwen3-vl:8b+think"

A model name ending in "+think" runs with thinking mode on.
Results go to model_eval/report.html (open it in your browser).
"""
import argparse
import base64
import json
import re
import sys
import time
from datetime import datetime
from html import escape
from pathlib import Path

import requests

OLLAMA = "http://localhost:11434"
HERE = Path(__file__).parent
DEFAULT_SETUPS = "qwen3:8b,qwen3:8b+think,qwen3-vl:8b"


def ollama(path, payload, timeout=600):
    r = requests.post(f"{OLLAMA}{path}", json=payload, timeout=timeout)
    data = r.json()
    if r.status_code != 200 or "error" in data:
        raise RuntimeError(data.get("error", f"HTTP {r.status_code}"))
    return data


def installed_models():
    return {m["name"] for m in requests.get(f"{OLLAMA}/api/tags", timeout=10).json()["models"]}


def capabilities(model):
    try:
        return set(ollama("/api/show", {"model": model}, timeout=30).get("capabilities", []))
    except Exception:
        return set()


def unload(model):
    """Free the VRAM before the next model (8 GB fits only one at a time)."""
    try:
        requests.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
    except Exception:
        pass


def is_installed(model, names):
    return model in names or f"{model}:latest" in names


def plain(text):
    """Flatten LaTeX so the auto-checks see what a human reads:
    \\boxed{\\frac{1}{3}} → 1/3,  50\\ \\text{ns} → 50 ns,  \\mu\\text{s} → μs."""
    t = text
    for _ in range(3):   # nested \boxed{\frac{..}{..}}
        t = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", t)
        t = re.sub(r"\\(?:boxed|text|mathrm|textbf|mathbf|operatorname)\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\mu\s*", "μ", t)
    t = re.sub(r"\\[,;:! ]|~", " ", t)
    return t


def compact(tex):
    """Same LaTeX, written the same way: drop spaces and grouping braces and
    use \\cos for \\mathrm{cos}, so  \\sqrt {\\pi }  and  2 m  match  \\sqrt{\\pi}  and  2m."""
    t = re.sub(r"\\(?:mathrm|operatorname)\{([a-zA-Z]+)\}", r"\\\1", tex)
    t = re.sub(r"\s+", "", t)
    return re.sub(r"(?<!\\)[{}]", "", t)


def passes(q, answer):
    # raw (for the LaTeX tests) + flattened prose + compacted LaTeX
    text = "\n".join([answer, plain(answer), compact(answer)])
    return all(re.search(p, text, re.I) for p in q["checks"])


def _ask(model, messages, think, num_ctx=None):
    payload = {"model": model, "messages": messages, "stream": False, "think": think}
    if num_ctx:
        payload["options"] = {"num_ctx": num_ctx}
    start = time.perf_counter()
    data = ollama("/api/chat", payload)
    data["_wall"] = time.perf_counter() - start
    return data


def run_one(setup, q, num_ctx=None):
    """One question, one model setup."""
    user = {"role": "user", "content": q["prompt"]}
    if q.get("image"):
        img = base64.b64encode((HERE / "images" / q["image"]).read_bytes()).decode()
        user["images"] = [img]
    messages = [user]

    data = _ask(setup["model"], messages, setup["think"], num_ctx)
    answer = data["message"].get("content", "").strip()
    reasoning = (data["message"].get("thinking") or "").strip()
    tokens = data.get("eval_count", 0)
    gen_s = data.get("eval_duration", 0) / 1e9
    return {
        "answer": answer, "reasoning": reasoning,
        "seconds": round(data["_wall"], 1),
        "load_s": round(data.get("load_duration", 0) / 1e9, 1),
        "tokens": tokens, "prompt_tokens": data.get("prompt_eval_count", 0),
        "tok_per_s": round(tokens / gen_s, 1) if gen_s else None,
        # "length" = stopped because the context window filled up
        "cut_off": data.get("done_reason") == "length" or (not answer and bool(reasoning)),
        "pass": passes(q, answer),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default=DEFAULT_SETUPS,
                    help=f"comma-separated, '+think' for thinking mode (default: {DEFAULT_SETUPS})")
    ap.add_argument("--only", choices=["images", "reasoning"], help="run one group of tests")
    ap.add_argument("--ids", help="comma-separated question ids to run (e.g. bat_ball,muon)")
    ap.add_argument("--ctx", type=int, help="context window in tokens (e.g. 8192); default: Ollama's")
    ap.add_argument("--rescore", action="store_true",
                    help="don't run anything: re-check results.json and rebuild the report")
    args = ap.parse_args()

    questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    if args.rescore:
        return rescore(questions)
    if args.only == "images":
        questions = [q for q in questions if q.get("image")]
    elif args.only == "reasoning":
        questions = [q for q in questions if not q.get("image")]
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    try:
        names = installed_models()
    except Exception:
        sys.exit("Can't reach Ollama at localhost:11434 — is it running?")

    setups = []
    for spec in [x.strip() for x in args.models.split(",") if x.strip()]:
        body, think = (spec[:-6], True) if spec.endswith("+think") else (spec, False)
        model = body
        if not is_installed(model, names):
            print(f"!! {model} isn't installed — run:  ollama pull {model}   (skipping {spec})")
            continue
        caps = capabilities(model)
        if think and caps and "thinking" not in caps:
            print(f"!! {model} doesn't support thinking mode (skipping {spec})")
            continue
        setups.append({"label": spec, "model": model, "think": think,
                       "sees": "vision" in caps})
    if not setups:
        sys.exit("No models to test.")

    results = {}   # (setup label, question id) -> result
    for s in setups:
        how = "images: yes" if s["sees"] else "images: no"
        print(f"\n=== {s['label']}  ({how})")
        for q in questions:
            if q.get("image") and not s["sees"]:
                results[(s["label"], q["id"])] = {"skipped": "no vision"}
                continue
            print(f"  {q['id']:16}", end=" ", flush=True)
            try:
                r = run_one(s, q, args.ctx)
                print(f"{'✓' if r['pass'] else '?'}  {r['seconds']:6.1f}s"
                      + ("  ⚠ CUT OFF (context full)" if r["cut_off"] else "")
                      + (f"  (incl. {r['load_s']}s loading)" if r["load_s"] > 1 else ""), flush=True)
            except Exception as e:
                r = {"error": str(e)}
                print(f"ERROR: {e}", flush=True)
            results[(s["label"], q["id"])] = r
        unload(s["model"])

    (HERE / "results.json").write_text(json.dumps(
        {f"{k[0]} | {k[1]}": v for k, v in results.items()}, indent=2, ensure_ascii=False), encoding="utf-8")
    finish([s["label"] for s in setups], questions, results)


def finish(labels, questions, results):
    out = HERE / "report.html"
    out.write_text(build_report(labels, questions, results), encoding="utf-8")
    print("\nAuto-check summary (✓ = answer contains the expected value — still read it!):")
    for label in labels:
        ran = [results[(label, q["id"])] for q in questions
               if "answer" in results.get((label, q["id"]), {})]
        times = sorted(r["seconds"] for r in ran)
        cut = sum(r.get("cut_off", False) for r in ran)
        print(f"  {label:24} {sum(r['pass'] for r in ran):2}/{len(ran)} ✓"
              + (f"   median {times[len(times) // 2]:.1f}s" if times else "")
              + (f"   ⚠ {cut} cut off" if cut else ""))
    print(f"\nReport: {out}")


def rescore(questions):
    """Re-check saved answers with the current checks; no model calls."""
    saved = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    by_id = {q["id"]: q for q in questions}
    results, labels = {}, []
    for key, r in saved.items():
        label, qid = key.rsplit(" | ", 1)
        if label not in labels:
            labels.append(label)
        if "answer" in r and qid in by_id:
            r["pass"] = passes(by_id[qid], r["answer"])
            r.setdefault("cut_off", not r["answer"] and bool(r.get("reasoning")))
        results[(label, qid)] = r
    questions = [q for q in questions if any((l, q["id"]) in results for l in labels)]
    (HERE / "results.json").write_text(json.dumps(
        {f"{k[0]} | {k[1]}": v for k, v in results.items()}, indent=2, ensure_ascii=False), encoding="utf-8")
    finish(labels, questions, results)


def build_report(labels, questions, results):
    """Side-by-side HTML report. Uses Loki's bundled KaTeX/marked, so it renders offline."""
    data = {
        "setups": labels,
        "questions": questions,
        "results": {f"{k[0]}|{k[1]}": v for k, v in results.items()},
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return REPORT_TEMPLATE.replace("__DATA__", payload)


REPORT_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Loki model comparison</title>
<link rel="stylesheet" href="../web/vendor/fonts/fonts.css">
<link rel="stylesheet" href="../web/vendor/katex/katex.min.css">
<script src="../web/vendor/katex/katex.min.js"></script>
<script src="../web/vendor/marked.min.js"></script>
<style>
  :root { --bg:#131314; --card:#1b1c1d; --text:#E3E3E3; --dim:#9aa0a6; --line:#2a2a2e; --ok:#a6e3a1; --warn:#f9e2af; --accent:#534AB7; }
  body { margin:0; background:var(--bg); color:var(--text); font:11pt/1.5 "Google Sans","Segoe UI",sans-serif; }
  header { padding:20px 24px 8px; } h1 { margin:0 0 4px; font-size:1.4em; } .sub { color:var(--dim); }
  table.sum { border-collapse:collapse; margin:12px 24px 4px; } .sum td, .sum th { border:1px solid var(--line); padding:6px 14px; text-align:left; }
  .q { margin:18px 24px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .cat { color:var(--dim); font-size:.85em; text-transform:uppercase; letter-spacing:.5px; }
  .prompt { margin:4px 0 8px; font-weight:500; } .q img { max-width:520px; max-height:260px; background:#fff; border-radius:6px; display:block; margin:6px 0; }
  .exp { color:var(--ok); margin-bottom:10px; } .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:10px; }
  .ans { border:1px solid var(--line); border-radius:8px; padding:8px 10px; overflow-x:auto; }
  .ans h4 { margin:0 0 4px; font-size:.95em; display:flex; justify-content:space-between; }
  .ok { color:var(--ok); } .chk { color:var(--warn); } .meta { color:var(--dim); font-weight:normal; font-size:.85em; }
  .ans p { margin:.35em 0; } pre { white-space:pre-wrap; background:#111; padding:6px; border-radius:4px; }
  details summary { cursor:pointer; color:var(--dim); font-size:.85em; } details div { color:var(--dim); font-size:.9em; border-left:2px solid var(--accent); padding-left:8px; }
</style></head><body>
<header><h1>Loki model comparison</h1>
<div class="sub" id="sub"></div>
<div class="sub">✓ = answer contains the expected value (auto-check). "?" = didn't match — read it yourself; it may still be right.</div></header>
<table class="sum" id="sum"></table>
<div id="qs"></div>
<script>
const D = __DATA__;
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function md(src) {   // markdown + KaTeX, same idea as the chat window
  const stash = [];
  const RE = /(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<!\\)\$(?!\$)([^$\n]+?)(?<!\\)\$/g;
  src = src.replace(RE, (m, a, b, c, d) => {
    if (d !== undefined && /^\d/.test(d) && /\s$/.test(d)) return m;
    const tex = a ?? b ?? c ?? d, display = a !== undefined || b !== undefined;
    stash.push(katex.renderToString(tex.trim(), {displayMode: display, throwOnError: false}));
    return "@@M" + (stash.length - 1) + "@@";
  }).replace(/</g, "&lt;");
  return marked.parse(src).replace(/@@M(\d+)@@/g, (_, i) => stash[+i]);
}
// Equation tests: show the LaTeX rendered (to compare against the image) + the raw code
const isEq = q => q.category.startsWith("Equation");
function texOf(answer) {
  let t = answer.trim().replace(/^```(?:latex|tex)?\s*/i, "").replace(/```\s*$/, "").trim();
  t = t.replace(/^\$\$([\s\S]*)\$\$$/, "$1").replace(/^\\\[([\s\S]*)\\\]$/, "$1").replace(/^\$([\s\S]*)\$$/, "$1");
  return t.replace(/\\begin\{(equation|align)\*?\}|\\end\{(equation|align)\*?\}/g, "").trim();
}
function texBlock(tex) {
  return `<div>${katex.renderToString(tex.replace(/\\qquad/g, "\\\\[6pt]").replace(/\n+/g, " \\\\ "), {displayMode: true, throwOnError: false})}</div>`
       + `<pre>${esc(tex)}</pre>`;
}
document.getElementById("sub").textContent = `Generated ${D.generated} · ${D.questions.length} questions · ${D.setups.length} model setups`;
let sum = "<tr><th>Model setup</th><th>Auto-check ✓</th><th>Cut off</th><th>Median time</th><th>Avg speed</th></tr>";
for (const s of D.setups) {
  const rs = D.questions.map(q => D.results[s + "|" + q.id]).filter(r => r && r.answer !== undefined);
  const t = rs.map(r => r.seconds).sort((a, b) => a - b), sp = rs.map(r => r.tok_per_s).filter(Boolean);
  sum += `<tr><td>${esc(s)}</td><td>${rs.filter(r => r.pass).length} / ${rs.length}</td><td>${rs.filter(r => r.cut_off).length || "–"}</td><td>${t.length ? t[t.length >> 1].toFixed(1) + " s" : "–"}</td><td>${sp.length ? (sp.reduce((a, b) => a + b) / sp.length).toFixed(0) + " tok/s" : "–"}</td></tr>`;
}
document.getElementById("sum").innerHTML = sum;
let html = "";
for (const q of D.questions) {
  html += `<div class="q"><div class="cat">${esc(q.category)} · ${esc(q.id)}</div><div class="prompt">${esc(q.prompt)}</div>`;
  if (q.image) html += `<img src="images/${esc(q.image)}">`;
  html += isEq(q) ? `<div class="exp">Expected:</div>${texBlock(q.expected)}<div class="grid">`
                  : `<div class="exp">Expected: ${md(q.expected).replace(/^<p>|<\/p>\s*$/g, "")}</div><div class="grid">`;
  for (const s of D.setups) {
    const r = D.results[s + "|" + q.id] || {};
    let head = esc(s), body;
    if (r.skipped) body = `<div class="meta">skipped (${esc(r.skipped)})</div>`;
    else if (r.error) body = `<div class="chk">Error: ${esc(r.error)}</div>`;
    else {
      head += `<span class="${r.pass ? "ok" : "chk"}">${r.pass ? "✓" : "?"} <span class="meta">${r.seconds}s${r.tok_per_s ? " · " + r.tok_per_s + " tok/s" : ""}</span></span>`;
      if (r.cut_off) head += `</h4><div class="chk">⚠ Cut off — ran out of context (${r.tokens} tokens generated) before finishing</div><h4 style="display:none">`;
      body = (r.reasoning ? `<details><summary>Show reasoning</summary><div>${md(r.reasoning)}</div></details>` : "")
           + (isEq(q) && r.answer ? texBlock(texOf(r.answer)) : md(r.answer || "(empty)"));
    }
    html += `<div class="ans"><h4>${head}</h4>${body}</div>`;
  }
  html += "</div></div>";
}
document.getElementById("qs").innerHTML = html;
</script></body></html>
"""

if __name__ == "__main__":
    main()
