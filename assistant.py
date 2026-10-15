"""Loki — a local AI assistant for Windows.

How a message flows:
    front end (text REPL / voice / chat window)
      -> utility commands (exit, reset, voice on/off, clear fired)
      -> dispatch(): calculator -> calculus -> LLM router -> intent handler
      -> a Reply that each front end displays its own way

Run:
    python assistant.py           text mode
    python assistant.py --voice   push-to-talk in the terminal
    python assistant.py --tray    tray icon + chat window (normal use)
"""
import atexit
import ctypes
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
import psutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import dateparser
import keyboard
import requests
from PySide6.QtWidgets import QApplication
from simpleeval import simple_eval

import chat_window
import db
import pdf_handler
import pdf_rag
import reminders
import symbolic_math
import system_info
import tray as tray_module
import voice

if sys.platform == "win32":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Loki.LocalAssistant.1.0")


# ============================================================================
# Configuration
# ============================================================================
BASE_DIR = Path(__file__).parent
APPS_FILE = BASE_DIR / "apps.json"
MANUAL_APPS_FILE = BASE_DIR / "manual_apps.json"
LATEX_PREFS_FILE = BASE_DIR / "latex_preferences.md"
LOCKFILE = os.path.join(tempfile.gettempdir(), "loki_assistant.lock")

ROUTER_MODEL = "qwen3:8b"
CHAT_MODEL = "qwen3:8b"
OLLAMA_URL = "http://localhost:11434/api/chat"

VOICE_HOTKEY = "f9"     # press and hold to talk
VOICE_OUTPUT = False    # speak replies out loud

MAX_HISTORY_TURNS = 10  # chat turns the model remembers

LOKI_IDENTITY = """You are Loki, a local AI assistant running privately on the user's PC.
Your features include: chat and Q&A, searching the web (Google, Maps, YouTube, Wikipedia,
etc.), opening apps and websites, setting one-off and recurring reminders, reading PDFs and
answering questions about them, converting text to LaTeX, exact symbolic calculus (integrate,
differentiate), quick arithmetic, and system info queries (CPU, RAM, disk, battery, uptime).
When users ask what you can do, describe these specific capabilities."""

# Set in tray_mode() so code running in background threads can post to the window
_window_bridge = None

# In tray mode, all output goes to a daily log file
if "--tray" in sys.argv:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"log_{datetime.now().strftime('%m%d%Y')}.txt"
    sys.stdout = open(log_path, "a", buffering=1, encoding="utf-8")
    sys.stderr = sys.stdout
    print(f"\n--- Assistant started at {datetime.now()} ---")


# ============================================================================
# Single-instance lock (tray mode)
# ============================================================================
def _pid_is_running(pid):
    """True if pid is a live Python process, i.e. probably another Loki.

    Checking the name matters: Windows reuses PIDs, so after Loki exits
    uncleanly its old PID can belong to an unrelated program."""
    try:
        name = psutil.Process(pid).name().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False
    return "python" in name


def release_lock():
    try:
        if os.path.exists(LOCKFILE):
            with open(LOCKFILE) as f:
                if f.read().strip() == str(os.getpid()):
                    os.unlink(LOCKFILE)
    except Exception:
        pass


def acquire_lock():
    if os.path.exists(LOCKFILE):
        try:
            with open(LOCKFILE) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            pid = -1
        if _pid_is_running(pid):
            print(f"Already running (PID {pid}). Exiting.")
            sys.exit(0)
        # otherwise it's a stale lock — overwrite it
    with open(LOCKFILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(release_lock)


# ============================================================================
# Apps and websites
# ============================================================================
def load_apps():
    apps = {}
    if APPS_FILE.exists():
        with open(APPS_FILE, encoding="utf-8") as f:
            apps = json.load(f)
    if MANUAL_APPS_FILE.exists():
        with open(MANUAL_APPS_FILE, encoding="utf-8") as f:
            apps.update(json.load(f))
    return apps


APPS = load_apps()
APP_NAMES = sorted(APPS.keys())


def _launch_exe(path, args=None):
    subprocess.Popen(
        [path, *(args or [])],
        cwd=os.path.dirname(path),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def _launch_store(appid):
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{appid}"])


def _launch_elevated(path, args=None):
    arg_str = f' -ArgumentList "{" ".join(args)}"' if args else ""
    subprocess.Popen(
        ["powershell", "-Command", f'Start-Process -FilePath "{path}" -Verb RunAs{arg_str}']
    )


def open_app(name, url_arg=None):
    """Launch an app from apps.json. Returns (ok, message)."""
    info = APPS.get(name)
    if not info:
        return False, f"I don't know an app called '{name}'."
    try:
        args = list(info.get("args", []))
        if url_arg:
            args.append(url_arg)
        note = ""
        if info["type"] == "exe":
            if info.get("elevated"):
                _launch_elevated(info["path"], args)
            else:
                _launch_exe(info["path"], args)
        else:
            _launch_store(info["appid"])
            if url_arg:
                note = f" ({name} is a Store app, so the link wasn't passed.)"
        return True, f"Opening {name}.{note}"
    except Exception as e:
        return False, f"Failed to open {name}: {e}"


def open_url(browser, url):
    """Open a URL in a named browser, or the system default. Returns (ok, message)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if browser:
        ok, msg = open_app(browser, url_arg=url)
        return ok, (f"Opening {url} in {browser}." if ok else msg)
    webbrowser.open(url)
    return True, f"Opening {url} in default browser."


def close_app(name):
    """Close an app by killing its exe. Returns (ok, message)."""
    info = APPS.get(name)
    if not info or info["type"] != "exe":
        return False, f"Can't auto-close '{name}'."
    exe = os.path.basename(info["path"])
    subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)
    return True, f"Closed {name}."


def _clean_browser(raw):
    """The router sometimes sends "null"/"none" as a string — treat as no browser."""
    if not raw or str(raw).strip().lower() in ("null", "none"):
        return None
    return str(raw).strip().lower()


# ============================================================================
# Router: the LLM classifies each message into an intent
# ============================================================================
ROUTER_SYSTEM_PROMPT = """ You are an intent classifier for a desktop assistant. \
Given a user message, respond with a SINGLE JSON object and nothing else.

The intent field MUST be exactly one of these values — no other values are allowed:
- "open_app"        — launch an installed application
- "open_url"        — open a website (optionally in a specific browser)
- "close_app"       — quit an application
- "add_reminder"    — schedule a one-off reminder ("remind me to ___ at ___")
- "add_recurring"   — schedule a recurring event (birthday, anniversary, daily/weekly/monthly)
- "list_reminders"  — show all upcoming reminders/events
- "cancel_reminder" — cancel a reminder by id
- "chat"            — anything else
- "attach_pdf"      — load a PDF for the assistant to discuss ("read this PDF", "load paper.pdf")
- "detach_pdf"      — unload the currently attached PDF ("detach", "forget the pdf", "unload")
- "to_latex"        — convert pasted text/equations to LaTeX ("convert to latex: ...", "latex this equation")
- "system_info"     — questions about this computer's status (CPU, RAM, disk, GPU, battery, uptime, IP)
- "web_search"      — search a service like Google, Maps, YouTube, Wikipedia for something

JSON shapes by intent:
- {"intent": "open_app", "app": "<name>"}
- {"intent": "open_url", "browser": "<name or null>", "url": "<url>"}
- {"intent": "close_app", "app": "<name>"}
- {"intent": "add_reminder", "text": "<what to remind about, without the time>", "when": "<the time phrase from the user's message>"}
- {"intent": "add_recurring", "text": "<event description, without the time>", "when": "<the date/time phrase>", "kind": "yearly|monthly|weekly|daily"}
- {"intent": "list_reminders"}
- {"intent": "cancel_reminder", "ids": [<integer>, ...]}
- {"intent": "chat"}
- {"intent": "attach_pdf", "path": "<file path from the user's message>"}
- {"intent": "detach_pdf"}
- {"intent": "to_latex", "text": "<the text/equation to convert>", "mode": "display|inline"}
- {"intent": "system_info"}
- {"intent": "web_search", "service": "<google|maps|youtube|wikipedia|amazon|github|stackoverflow|images>", "query": "<the search query>", "browser": "<browser name or null>"}

Installed apps:
%APP_LIST%

Rules:
- For open/close intents, pick the closest matching app from the list above.
- For browser field, use the lowercase short name from the installed apps list ("chrome", "firefox", "edge"). If no specific browser is mentioned, use JSON null (without quotes), NOT the string "null". Example: {"intent": "open_url", "browser": null, "url": "youtube.com"}
- "remind me to X at/on/in Y" -> add_reminder with text=X and when=Y.
- "my anniversary is October 12" or "Sarah's birthday is March 4" -> add_recurring with kind=yearly.
- "every monday at 9am, X" -> add_recurring with kind=weekly.
- For add_reminder and add_recurring, separate the action from the time:
  - "text" gets only what to remind about (no time)
  - "when" gets only the time phrase, copied from the user's message
  - Example: "remind me to call mom tomorrow at 5pm" -> {"intent": "add_reminder", "text": "call mom", "when": "tomorrow at 5pm"}
- Copy time phrases from the user's message faithfully. If they wrote "in 30 secs", put "in 30 secs" — not "30 minutes" or "in half a minute", but normalize common decimal-time notations: "9.20 pm" -> "9:20 pm", "5.30" -> "5:30", "9 27 pm" -> "9:27 pm". Use colons, NOT any other delimiter between hours and minutes.
- "what reminders do I have", "show my reminders", "list events" -> list_reminders.
- "cancel reminder 3", "delete reminder 5" -> cancel_reminder with the number as id.
- "cancel reminder 3" -> {"intent": "cancel_reminder", "ids": [3]}
- "delete reminders 4, 5, 6" -> {"intent": "cancel_reminder", "ids": [4, 5, 6]} or "delete reminders 4, 5 and 6" -> {"intent": "cancel_reminder", "ids": [4, 5, 6]}
- "cancel all reminders" -> {"intent": "cancel_reminder", "ids": "all"}
- For "chat" intent, the JSON is EXACTLY {"intent": "chat"} — no other fields. Do NOT include "response", "message", "answer", or any other fields. Output only the four characters of valid JSON: { " : } and the literal text "intent" and "chat". Stop after the closing brace.
- Respond with ONLY the JSON. No markdown, no code fences, no commentary.
- "read /path/to/file.pdf", "load this PDF: ...", "open paper.pdf" -> attach_pdf with the path.
- "detach", "unload pdf", "forget the pdf", "close pdf" -> detach_pdf.
- For attach_pdf, copy the path EXACTLY as the user wrote it, even if it contains spaces or unusual characters.
- attach_pdf is ONLY for when the user provides an actual file path with a .pdf extension. Examples that trigger attach_pdf:
  - "read C:/Users/me/paper.pdf"
  - "load /home/me/thesis.pdf"
  - "open ~/Downloads/article.pdf"
- Do NOT use attach_pdf for these — they are chat:
  - "summarize this paper" (no path given)
  - "what does the paper say about X" (no path given)
  - "tell me about this document" (no path given)
- If there's already an attached PDF, questions about it without an explicit path are "chat" intent, NOT attach_pdf.
- For attach_pdf, the path field MUST contain a string ending in .pdf. If no .pdf path is in the message, do not use attach_pdf.
- "convert to latex: <equation>", "latex this: <text>", "turn this into latex: <text>" -> to_latex with the text after the colon.
- mode is "inline" if the user says "inline", otherwise "display".
- Example: "convert to latex: x squared plus y squared" -> {"intent": "to_latex", "text": "x squared plus y squared", "mode": "display"}
- "how much disk space do I have", "what's my RAM usage", "cpu usage", "battery level", "how long has my PC been on", "what's my IP", "am I running low on memory" -> system_info.
- "open the map of <X>", "show me <X> on maps", "find <X> on google maps" -> web_search with service="maps".
- "search youtube for <X>", "find <X> on youtube" -> web_search with service="youtube".
- "look up <X> on wikipedia", "wikipedia <X>" -> web_search with service="wikipedia".
- "google <X>", "search for <X>", "search google for <X>" -> web_search with service="google".
- "find <X> on amazon" -> web_search with service="amazon".
- "search github for <X>" -> web_search with service="github".
- "find images of <X>", "google images of <X>" -> web_search with service="images".
- The browser field is ONLY for when the user explicitly names a browser ("in chrome", "on firefox", "using edge"). If the user does not name a specific browser, set browser to null. Do NOT default to "chrome" or any other browser. The user's system default browser will be used when browser is null.
- Always include the browser field in the JSON, even when null. Example: {"intent": "web_search", "service": "maps", "query": "...", "browser": null}
- For the query field, strip the search-command words and keep only the subject. Example: "open the map of chicago illinois in chrome" -> service="maps", query="chicago illinois", browser="chrome".
- Use web_search ONLY when the user explicitly wants to search, browse, or open a page, otherwise default to chat. Trigger phrases examples: "search for", "look up X on <service>", "open the map of", "find X on", "google X", "show me X on youtube".
"""

VALID_INTENTS = {
    "open_app", "open_url", "close_app",
    "add_reminder", "add_recurring", "list_reminders", "cancel_reminder",
    "attach_pdf", "detach_pdf",
    "to_latex",
    "system_info",
    "web_search",
    "chat",
}


def classify_intent(user_text):
    system = ROUTER_SYSTEM_PROMPT.replace(
        "%APP_LIST%", "\n".join(f"- {n}" for n in APP_NAMES)
    )
    t_start = time.perf_counter()
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": ROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
                "think": False,
            },
            timeout=60,
        )
        elapsed = time.perf_counter() - t_start
        content = r.json()["message"]["content"]
        print(f"[DEBUG] {ROUTER_MODEL} routed in {elapsed*1000:.0f}ms → {content}")

        parsed = json.loads(content)
        if parsed.get("intent") not in VALID_INTENTS:
            return {"intent": "chat"}
        return parsed
    except Exception as e:
        print(f"(router failed: {e}, falling back to chat)")
        return {"intent": "chat"}


# ============================================================================
# Chat (with PDF context when a PDF is attached)
# ============================================================================
chat_history = []

SUMMARY_TRIGGERS = (
    "summarize", "summary", "overview", "what is this paper about",
    "what's this paper about", "tl;dr", "main points", "key findings", "conclusion",
)


def _remember(role, content):
    chat_history.append({"role": role, "content": content})
    if len(chat_history) > MAX_HISTORY_TURNS * 2:
        del chat_history[: len(chat_history) - MAX_HISTORY_TURNS * 2]


def _build_chat_messages(message):
    """Messages to send for one chat turn. Records the user message in history.

    With a PDF attached, the model gets the document excerpts (or the whole
    document for summary requests) instead of the chat history."""
    _remember("user", message)

    if not pdf_handler.is_attached():
        return [{"role": "system", "content": LOKI_IDENTITY}, *chat_history]

    info = pdf_handler.get_info()
    if any(t in message.lower() for t in SUMMARY_TRIGGERS):
        context = pdf_handler.get_context()
    else:
        context = "\n\n---\n\n".join(pdf_rag.retrieve(message))

    context_message = (
        f"You are answering a question about the document '{info['filename']}'. "
        f"Below are the most relevant excerpts. Answer using ONLY these excerpts. "
        f"Quote exact numbers and values precisely as written. If the excerpts don't "
        f"contain the answer, say the document doesn't appear to specify it.\n\n"
        f"--- RELEVANT EXCERPTS ---\n{context}\n--- END EXCERPTS ---"
    )
    return [
        {"role": "system", "content": context_message},
        {"role": "user", "content": message},
    ]


def _strip_leading_junk(reply):
    """qwen3 occasionally starts a reply with stray tokens like ','\"+ — drop them."""
    return re.sub(r'^[\s\'",+\-•·\u0080-￿]{1,10}(?=[A-Z])', '', reply).strip()


def chat(message, stream=False):
    """Ask the chat model. With stream=True the reply is printed to the
    terminal as it arrives (text and voice modes). Returns the full reply."""
    payload = {
        "model": CHAT_MODEL,
        "messages": _build_chat_messages(message),
        "stream": stream,
        "think": False,
    }
    if stream:
        reply = ""
        with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=120) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                piece = chunk.get("message", {}).get("content", "")
                print(piece, end="", flush=True)
                reply += piece
                if chunk.get("done"):
                    print()
                    break
    else:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        reply = r.json()["message"]["content"]

    reply = _strip_leading_junk(reply)
    _remember("assistant", reply)
    return reply


# ============================================================================
# Reminders
# ============================================================================
def _normalize_time_string(s):
    """Fix Whisper's spoken-time artifacts: '9 27 pm', '9.27 pm', '9,27 pm' → '9:27 pm'."""
    return re.sub(
        r'\b(\d{1,2})[^\w]+(\d{2})\s*(a\.?m\.?|p\.?m\.?)\b',
        r'\1:\2 \3',
        s,
        flags=re.IGNORECASE,
    )


def parse_when(when_str):
    if not when_str:
        return None
    return dateparser.parse(
        _normalize_time_string(when_str),
        settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
    )


def add_reminder(text, when_str):
    """Returns (ok, message)."""
    fire_at = parse_when(when_str)
    if not fire_at:
        return False, (f"Couldn't understand the time '{when_str}'. "
                       f"Try 'in 5 minutes', 'tomorrow at 8pm', etc.")
    if fire_at <= datetime.now():
        return False, f"That time ({fire_at.strftime('%a %b %d at %I:%M %p')}) is already past."
    text = text.strip()
    if text.lower().startswith("to "):
        text = text[3:]
    text = text or "(unspecified)"
    rid = reminders.add_and_schedule(text, fire_at, kind="once")
    return True, f"✓ Reminder #{rid} set for {fire_at.strftime('%A %b %d at %I:%M %p')}: {text}"


def add_recurring(text, when_str, kind):
    """Returns (ok, message)."""
    if kind not in ("yearly", "monthly", "weekly", "daily"):
        return False, f"Unknown recurrence kind '{kind}'."
    fire_at = parse_when(when_str)
    if not fire_at:
        return False, f"Couldn't understand '{when_str}'."
    if fire_at <= datetime.now() and kind == "yearly":
        fire_at = fire_at.replace(year=datetime.now().year + 1)
    text = text.strip() or "(unspecified)"
    rid = reminders.add_and_schedule(text, fire_at, kind=kind)
    return True, (f"✓ Recurring ({kind}) #{rid} set for "
                  f"{fire_at.strftime('%A %b %d at %I:%M %p')}: {text}")


def format_reminders():
    rows = db.list_pending()
    if not rows:
        return "No upcoming reminders."
    lines = [f"Reminders ({len(rows)}):"]
    now = datetime.now()
    for r in rows:
        when = datetime.fromisoformat(r["fire_at"])
        kind_tag = f" [{r['kind']}]" if r["kind"] != "once" else ""
        if r["fired_at"]:
            status = "✓ fired"
        elif when < now:
            status = "⚠ overdue"
        else:
            status = "  pending"
        lines.append(f"  #{r['id']}{kind_tag} {status}  "
                     f"{when.strftime('%a %b %d %I:%M %p')}  — {r['text']}")
    return "\n".join(lines)


def cancel_reminders(ids):
    """Cancel reminders by id (or "all"). Returns a message."""
    if ids == "all":
        ids = [r["id"] for r in db.list_pending(include_recent=False)]
    if not ids:
        return "No reminders to cancel."
    cancelled = []
    for rid in ids:
        if db.cancel(int(rid)):
            job_id = f"reminder_{rid}"
            if reminders.scheduler.get_job(job_id):
                reminders.scheduler.remove_job(job_id)
            cancelled.append(rid)
    if not cancelled:
        return "No matching reminders found."
    plural = "s" if len(cancelled) > 1 else ""
    return f"✓ Cancelled reminder{plural}: {', '.join(f'#{i}' for i in cancelled)}"


def clear_fired_reminders():
    cleared = 0
    for r in db.list_pending():
        if r["fired_at"] and r["kind"] == "once":
            db.cancel(r["id"])
            cleared += 1
    return f"Cleared {cleared} fired reminder(s)."


# ============================================================================
# PDFs
# ============================================================================
def attach_pdf(path):
    """Load a PDF for Q&A. Returns a message."""
    if not path or not path.lower().endswith(".pdf"):
        return ("To attach a PDF, give me the full file path ending in .pdf. "
                "Or drag a PDF onto the window.")
    try:
        info = pdf_handler.attach(path)
    except FileNotFoundError:
        return f"File not found: {path}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to read PDF: {e}"

    filename = Path(info["path"]).name
    msg = (f"Loaded {filename} ({info['pages']} pages, ~{info['estimated_tokens']:,} tokens). "
           f"Ask me anything about it. Say 'detach' to unload.")
    if info.get("truncated"):
        msg += " (Document was long and got truncated — later pages may be incomplete.)"
    return msg


def detach_pdf():
    if pdf_handler.detach():
        return "PDF unloaded. Back to general chat."
    return "No PDF was attached."


# ============================================================================
# Text → LaTeX
# ============================================================================
LATEX_SYSTEM_PROMPT = """You are a LaTeX conversion tool. Convert the user's input into correct LaTeX.

Rules:
- Output ONLY the LaTeX code. No explanations, no commentary, no markdown code fences.
- Use standard LaTeX math notation.
- For display mode, wrap in \\[ ... \\]. For inline mode, wrap in \\( ... \\).
- Preserve the mathematical meaning exactly. Do not solve, simplify, or alter the math.
- Use proper LaTeX commands: \\frac{}{}, \\int, \\sum, \\sqrt{}, Greek letters as \\alpha etc., \\partial, \\nabla, subscripts with _ and superscripts with ^.
- If the input is already LaTeX, clean it up and fix any errors rather than double-wrapping it."""


def convert_to_latex(text, mode="display"):
    """Convert text/equations to LaTeX. Returns (latex, error)."""
    if not text or not text.strip():
        return None, "What should I convert? Paste the text or equation."

    system = LATEX_SYSTEM_PROMPT
    if LATEX_PREFS_FILE.exists():
        prefs = LATEX_PREFS_FILE.read_text(encoding="utf-8").strip()
        if prefs:
            system += f"\n\nUser style preferences:\n{prefs}"

    mode_instruction = ("Use display mode: wrap in \\[ ... \\]." if mode == "display"
                        else "Use inline mode: wrap in \\( ... \\).")
    r = requests.post(
        OLLAMA_URL,
        json={
            "model": CHAT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"{mode_instruction}\n\nConvert this to LaTeX:\n{text}"},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1},
        },
        timeout=60,
    )
    latex = r.json()["message"]["content"].strip()
    latex = re.sub(r"^```[\w]*\n?", "", latex)
    latex = re.sub(r"\n?```$", "", latex)
    return latex.strip(), None


# ============================================================================
# System info
# ============================================================================
SYSTEM_INFO_PROMPT = """You are answering a question about the user's computer. \
Below is the current system data as JSON. Answer the user's specific question \
concisely using this data. Only mention what they asked about — don't dump every stat \
unless they asked for a full overview. Use friendly units (GB, %, etc.).

System data:
%SYSTEM_DATA%"""


def answer_system_question(user_text):
    data = system_info.gather_all()
    system = SYSTEM_INFO_PROMPT.replace("%SYSTEM_DATA%", json.dumps(data, indent=2))
    r = requests.post(
        OLLAMA_URL,
        json={
            "model": CHAT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.2},
        },
        timeout=60,
    )
    return r.json()["message"]["content"].strip()


# ============================================================================
# Calculator (pure arithmetic, no LLM)
# ============================================================================
_MATH_NAMES = {
    "pi": math.pi, "e": math.e, "tau": math.tau,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "ln": math.log, "exp": math.exp,
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "factorial": math.factorial,
}
_MATH_RE = re.compile(r'^[\d\s+\-*/().,%^]+$')


def try_calculator(text):
    """If text is a pure arithmetic expression, return its value as a string;
    otherwise None."""
    expr = text.strip().rstrip("=?").strip()
    if not re.search(r'\d', expr) or not re.search(r'[+\-*/^%]', expr):
        return None
    leftover = re.sub(r'[a-z_]+', '', expr.lower())   # allow sqrt(), pi, etc.
    if not _MATH_RE.match(leftover if leftover.strip() else "0"):
        return None
    try:
        return str(simple_eval(expr.replace("^", "**"), names=_MATH_NAMES, functions=_MATH_NAMES))
    except Exception:
        return None


# ============================================================================
# Web search (builds a search URL — no API, nothing leaves your browser)
# ============================================================================
SEARCH_SERVICES = {
    "google":        "https://www.google.com/search?q={q}",
    "maps":          "https://www.google.com/maps/search/{q}",
    "youtube":       "https://www.youtube.com/results?search_query={q}",
    "wikipedia":     "https://en.wikipedia.org/wiki/Special:Search?search={q}",
    "amazon":        "https://www.amazon.com/s?k={q}",
    "github":        "https://github.com/search?q={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
    "images":        "https://www.google.com/search?tbm=isch&q={q}",
}


def web_search(service, query, browser):
    """Returns (ok, message)."""
    if not query or not query.strip():
        return False, "What should I search for?"
    service = (service or "google").lower()
    if service not in SEARCH_SERVICES:
        return False, (f"Don't know the service '{service}'. Try "
                       f"{', '.join(SEARCH_SERVICES)}.")
    url = SEARCH_SERVICES[service].format(q=quote_plus(query.strip()))
    ok, msg = open_url(browser, url)
    return ok, (f"Searching {service} for '{query}'." if ok else msg)


# ============================================================================
# Dispatcher — shared by the text, voice and window front ends
# ============================================================================
@dataclass
class Reply:
    text: str
    kind: str = "system"            # "system" = short status line, "assistant" = rendered reply
    speak: Optional[str] = None     # what voice mode says (default: the text itself)
    intent: Optional[str] = None
    streamed: bool = False          # already printed to the terminal while streaming

    @property
    def spoken(self):
        return self.speak if self.speak is not None else self.text


def _on_open_app(p, _text):
    app = (p.get("app") or "").lower()
    ok, msg = open_app(app)
    return Reply(msg)


def _on_open_url(p, _text):
    ok, msg = open_url(_clean_browser(p.get("browser")), p.get("url", ""))
    return Reply(msg, speak="Opening it now." if ok else msg)


def _on_close_app(p, _text):
    ok, msg = close_app((p.get("app") or "").lower())
    return Reply(msg)


def _on_add_reminder(p, _text):
    ok, msg = add_reminder(p.get("text", ""), p.get("when", ""))
    return Reply(msg, speak="Reminder set." if ok else "I couldn't set that reminder.")


def _on_add_recurring(p, _text):
    ok, msg = add_recurring(p.get("text", ""), p.get("when", ""), p.get("kind", "yearly"))
    return Reply(msg, speak="Recurring reminder set." if ok else "I couldn't set that reminder.")


def _on_list_reminders(_p, _text):
    return Reply(format_reminders(), speak="Here are your reminders.")


def _on_cancel_reminder(p, _text):
    return Reply(cancel_reminders(p.get("ids", [])))


def _on_attach_pdf(p, _text):
    return Reply(attach_pdf(p.get("path", "")))


def _on_detach_pdf(_p, _text):
    return Reply(detach_pdf())


def _on_to_latex(p, _text):
    latex, error = convert_to_latex(p.get("text", ""), p.get("mode", "display"))
    if error:
        return Reply(error)
    return Reply(f"```latex\n{latex}\n```", kind="assistant", speak="Converted to LaTeX.")


def _on_system_info(_p, text):
    return Reply(answer_system_question(text), kind="assistant")


def _on_web_search(p, _text):
    ok, msg = web_search(p.get("service"), p.get("query", ""), _clean_browser(p.get("browser")))
    return Reply(msg, speak=msg if ok else "Couldn't search for that.")


INTENT_HANDLERS = {
    "open_app":        _on_open_app,
    "open_url":        _on_open_url,
    "close_app":       _on_close_app,
    "add_reminder":    _on_add_reminder,
    "add_recurring":   _on_add_recurring,
    "list_reminders":  _on_list_reminders,
    "cancel_reminder": _on_cancel_reminder,
    "attach_pdf":      _on_attach_pdf,
    "detach_pdf":      _on_detach_pdf,
    "to_latex":        _on_to_latex,
    "system_info":     _on_system_info,
    "web_search":      _on_web_search,
    # "chat" has no entry: anything unhandled goes to chat()
}


def dispatch(user_text, stream=False):
    """Do whatever the message asks and return a Reply.
    stream=True prints chat replies to the terminal as they're generated."""
    # 1. Pure arithmetic — instant, no LLM
    value = try_calculator(user_text)
    if value is not None:
        return Reply(f"= {value}", speak=f"That's {value}")

    # 2. Calculus commands ("integrate ...", "diff ...") — SymPy, no router
    calc_cmd = symbolic_math.parse_calculus_command(user_text)
    if calc_cmd is not None:
        body, error = symbolic_math.run(calc_cmd)
        if error:
            return Reply(error)
        return Reply(body, kind="assistant", speak="Done. The result is on screen.")

    # 3. Everything else goes through the router
    parsed = classify_intent(user_text)
    intent = parsed.get("intent", "chat")
    handler = INTENT_HANDLERS.get(intent)
    if handler:
        reply = handler(parsed, user_text)
    else:
        # Always send the user's own words — never the router's paraphrase
        reply = Reply(chat(user_text, stream=stream), kind="assistant", streamed=stream)
    reply.intent = intent
    return reply


# ============================================================================
# Utility commands (handled before dispatch, no LLM)
# ============================================================================
UTILITY_COMMANDS = {
    "exit":        {"exit", "quit", "bye", "goodbye", "stop", "shut down",
                    "shutdown", "end", "close assistant"},
    "voice_off":   {"voice off", "stop talking", "be quiet", "shush", "mute", "silence"},
    "voice_on":    {"voice on", "speak again", "unmute", "talk to me"},
    "reset":       {"reset", "clear", "new chat", "clear chat",
                    "forget everything", "start over"},
    "clear_fired": {"clear fired", "clear history", "clean up"},
}


def is_utility_command(text):
    """Return the command name ("exit", "reset", ...) or None."""
    normalized = re.sub(r"[.,!?;:\s]+$", "", text.lower().strip())
    for name, variants in UTILITY_COMMANDS.items():
        if normalized in variants:
            return name
    return None


def run_utility(cmd):
    """Run any utility command except "exit" (each front end exits differently)."""
    global VOICE_OUTPUT
    if cmd == "voice_off":
        VOICE_OUTPUT = False
        return Reply("Voice output disabled.")
    if cmd == "voice_on":
        VOICE_OUTPUT = True
        return Reply("Voice output enabled.")
    if cmd == "reset":
        chat_history.clear()
        return Reply("Chat history cleared.")
    if cmd == "clear_fired":
        return Reply(clear_fired_reminders())
    return Reply(f"Unknown command '{cmd}'.")


def _error_reply(e):
    traceback.print_exc()
    return Reply(f"Something went wrong: {e}", speak="Sorry, something went wrong.")


# ============================================================================
# Front end 1: text REPL  (python assistant.py)
# ============================================================================
def handle_text(user_text):
    try:
        reply = dispatch(user_text, stream=True)
    except Exception as e:
        reply = _error_reply(e)
    if not reply.streamed:
        print(reply.text)


def text_mode():
    reminders.start()
    print(f"Loaded {len(APPS)} apps. Router: {ROUTER_MODEL}, Chat: {CHAT_MODEL}\n")
    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        cmd = is_utility_command(user_input)
        if cmd == "exit":
            break
        if cmd:
            print(run_utility(cmd).text)
            continue
        handle_text(user_input)


# ============================================================================
# Front end 2: terminal voice mode  (python assistant.py --voice)
# ============================================================================
def handle_voice(user_text):
    cmd = is_utility_command(user_text)
    if cmd == "exit":
        raise KeyboardInterrupt
    if cmd:
        reply = run_utility(cmd)
    else:
        try:
            reply = dispatch(user_text, stream=True)
        except Exception as e:
            reply = _error_reply(e)
    if not reply.streamed:
        print(reply.text)
    if VOICE_OUTPUT and reply.spoken:
        voice.speak(reply.spoken)


def voice_mode():
    """Push-to-talk: hold the hotkey to record, release to transcribe and run."""
    reminders.start()
    recorder = voice.Recorder()
    print(f"\nVoice mode: hold {VOICE_HOTKEY.upper()} to talk. Release to send.")
    print("Say 'voice off' to disable speech output.\n")

    is_recording = False
    while True:
        try:
            if keyboard.is_pressed(VOICE_HOTKEY):
                if not is_recording:
                    print("🎙  recording...", end="", flush=True)
                    recorder.start()
                    is_recording = True
                time.sleep(0.05)
                continue
            if is_recording:                      # hotkey just released
                audio = recorder.stop()
                is_recording = False
                print(" transcribing...", end="", flush=True)
                text = voice.transcribe(audio)
                print(f"\r🎙  you said: {text}" + " " * 20)
                if text:
                    handle_voice(text)
                continue
            time.sleep(0.05)
        except KeyboardInterrupt:
            break


# ============================================================================
# Front end 3: tray + chat window  (python assistant.py --tray)
# ============================================================================
def handle_window(user_text):
    """Runs on a worker thread. Talks to the window only through bridge signals."""
    bridge = _window_bridge
    if bridge is None:
        return

    cmd = is_utility_command(user_text)
    if cmd == "exit":
        bridge.quit_requested.emit()
        return
    if cmd:
        bridge.system_message.emit(run_utility(cmd).text)
        return

    bridge.thinking_started.emit()
    try:
        reply = dispatch(user_text)
    except Exception as e:
        reply = _error_reply(e)
    finally:
        bridge.thinking_stopped.emit()

    if reply.text:
        if reply.kind == "assistant":
            bridge.assistant_said.emit(reply.text)
        else:
            print(reply.text)     # keep status messages in the log too
            bridge.system_message.emit(reply.text)

    if reply.intent in ("attach_pdf", "detach_pdf"):
        name = pdf_handler.get_info()["filename"] if pdf_handler.is_attached() else ""
        bridge.pdf_attached.emit(name)


def _voice_loop_for_tray(running):
    """F9 push-to-talk while in tray mode; results go to the chat window."""
    recorder = voice.Recorder()
    is_recording = False
    while running["alive"]:
        try:
            if keyboard.is_pressed(VOICE_HOTKEY):
                if not is_recording:
                    if _window_bridge:
                        _window_bridge.system_message.emit("🎙 Listening...")
                    recorder.start()
                    is_recording = True
                time.sleep(0.05)
                continue
            if is_recording:
                audio = recorder.stop()
                is_recording = False
                text = voice.transcribe(audio)
                if text:
                    if _window_bridge:
                        _window_bridge.user_said.emit(text)
                    handle_window(text)
                continue
            time.sleep(0.05)
        except Exception as e:
            print(f"Voice loop error: {e}", flush=True)
            is_recording = False


def tray_mode():
    global _window_bridge
    acquire_lock()
    reminders.start()

    qt_app = QApplication.instance() or QApplication([])
    qt_app.setQuitOnLastWindowClosed(False)

    bridge = chat_window.ChatBridge()
    _window_bridge = bridge
    window = chat_window.ChatWindow(bridge)

    # --- Everything below must be set up BEFORE qt_app.exec() ---

    # Window show / quit
    def show_window():
        window.show()
        window.raise_()
        window.activateWindow()

    bridge.show_window_requested.connect(show_window)
    bridge.quit_requested.connect(qt_app.quit)

    # Reminder popups: the scheduler thread calls display_reminder, which
    # emits a signal so the popup is created on the GUI thread.
    reminders.set_display_callback(lambda title, body: bridge.reminder_fired.emit(title, body))
    active_popups = []   # keep references so popups aren't garbage collected

    def show_reminder_popup(title, body):
        try:
            popup = chat_window.ReminderPopup(title, body)
            active_popups.append(popup)
            popup.destroyed.connect(
                lambda: active_popups.remove(popup) if popup in active_popups else None
            )
            popup.show()
            popup.raise_()
            popup.activateWindow()
        except Exception as e:
            print(f"[POPUP] FAILED: {e}", flush=True)
            traceback.print_exc()

    bridge.reminder_fired.connect(show_reminder_popup)

    # Typed messages: show immediately, handle on a worker thread so the
    # window stays responsive while the LLM works
    def on_submit(text):
        bridge.user_said.emit(text)
        threading.Thread(target=handle_window, args=(text,), daemon=True).start()

    window.submit_requested.connect(on_submit)

    # Tray icon
    running = {"alive": True}

    def quit_cb():
        running["alive"] = False
        bridge.quit_requested.emit()

    def voice_toggle_cb():
        global VOICE_OUTPUT
        VOICE_OUTPUT = not VOICE_OUTPUT

    tray = tray_module.TrayApp(
        on_quit=quit_cb,
        on_voice_toggle=voice_toggle_cb,
        get_voice_status=lambda: VOICE_OUTPUT,
        on_open_chat=bridge.show_window_requested.emit,
    )
    tray.run_in_thread()

    voice_thread = threading.Thread(target=_voice_loop_for_tray, args=(running,), daemon=True)
    voice_thread.start()

    qt_app.exec()          # blocks until quit
    running["alive"] = False
    voice_thread.join(timeout=1.0)

    # Leftover background threads (tray icon, scheduler, web engine) can keep
    # Python alive after the window closes. Release the lock and exit for
    # real so restarting Loki always works.
    release_lock()
    sys.stdout.flush()
    os._exit(0)


# ============================================================================
# Entry point
# ============================================================================
def main():
    if "--tray" in sys.argv:
        tray_mode()
    elif "--voice" in sys.argv:
        voice_mode()
    else:
        text_mode()


if __name__ == "__main__":
    main()
