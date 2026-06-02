import json
import os
import subprocess
from pathlib import Path
import requests
import time
import dateparser
from datetime import datetime
import db
import reminders
import keyboard
import voice
import sys
import re
import tempfile
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
import chat_window
import atexit
import tray as tray_module
import pdf_handler
import pdf_rag
import system_info
from simpleeval import simple_eval
import math
import ctypes
import webbrowser
import threading
import symbolic_math
from urllib.parse import quote_plus

if sys.platform == "win32":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Loki.LocalAssistant.1.0")

LOCKFILE = os.path.join(tempfile.gettempdir(), "loki_assistant.lock")

def _pid_is_running(pid):
    """Cross-platform check: is this PID a live process?"""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows: use OpenProcess to check
            PROCESS_QUERY_INFORMATION = 0x0400
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION, False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            # Unix: signal 0 trick
            os.kill(pid, 0)
            return True
    except (OSError, ValueError):
        return False
    
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
        # else: stale lock, fall through and overwrite it

    with open(LOCKFILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(release_lock)


APPS_FILE = Path(__file__).parent / "apps.json"
MANUAL_APPS_FILE = Path(__file__).parent / "manual_apps.json"

ROUTER_MODEL = "qwen3:8b"
CHAT_MODEL = "qwen3:8b"
OLLAMA_URL = "http://localhost:11434/api/chat"

VOICE_HOTKEY = "f9"  # press and hold to talk
VOICE_OUTPUT = False  # set to False if you want text-only replies

LOKI_IDENTITY = """You are Loki, a local AI assistant running privately on the user's PC.
Your features include: chat and Q&A, **searching the web (Google,
Maps, YouTube, Wikipedia, etc.)**, opening apps and websites, setting one-off and recurring
reminders, reading PDFs and answering questions about them, converting text to LaTeX, exact
symbolic calculus (integrate, differentiate), quick arithmetic, and system info queries
(CPU, RAM, disk, battery, uptime). When users ask what you can do, describe these specific
capabilities."""

if "--tray" in sys.argv:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"log_{datetime.now().strftime('%m%d%Y')}.txt"
    sys.stdout = open(log_path, "a", buffering=1, encoding="utf-8")
    sys.stderr = sys.stdout
    print(f"\n--- Assistant started at {datetime.now()} ---")


# ----------------------------
# Apps
# ----------------------------
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


# ----------------------------
# Router: small model classifies intent
# ----------------------------
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
- For the query field, strip the search-command words and keep only the subject. Example: "open the map of chicago illinois in chrome" -> service="maps", query="chicago illinois", browser="chrome"."""


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

# ----------------------------
# Actions
# ----------------------------
def launch_exe(path, args=None):
    args = args or []
    subprocess.Popen(
        [path, *args],
        cwd=os.path.dirname(path),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )

def launch_store(appid):
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{appid}"])

def launch_elevated(path, args=None):
    args = args or []
    arg_str = f' -ArgumentList "{" ".join(args)}"' if args else ""
    subprocess.Popen(
        ["powershell", "-Command",
         f'Start-Process -FilePath "{path}" -Verb RunAs{arg_str}']
    )

def open_app(name, url_arg=None):
    info = APPS.get(name)
    if not info:
        print(f"Don't know '{name}'. Got: {APP_NAMES[:5]}{'...' if len(APP_NAMES) > 5 else ''}")
        return False
    try:
        args = list(info.get("args", []))
        if url_arg:
            args.append(url_arg)

        if info["type"] == "exe":
            if info.get("elevated"):
                launch_elevated(info["path"], args)
            else:
                launch_exe(info["path"], args)
        else:
            launch_store(info["appid"])
            if url_arg:
                print(f"(Note: {name} is a Store app; URL not passed.)")

        print(f"Opening {name}{f' ({url_arg})' if url_arg else ''}...")
        return True
    except Exception as e:
        print(f"Failed to open {name}: {e}")
        return False

def open_url(browser, url):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if browser and browser not in ("null", "none"):
        return open_app(browser, url_arg=url)
    # No browser specified: use OS default
    webbrowser.open(url)
    print(f"Opening {url}")
    return True

def close_app(name):
    info = APPS.get(name)
    if not info or info["type"] != "exe":
        print(f"Can't auto-close '{name}'.")
        return False
    exe = os.path.basename(info["path"])
    subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)
    print(f"Closed {name}")
    return True


# ----------------------------
# Chat fallback (big model, streaming)
# Conversation history (in-memory, this session only)
# ----------------------------
chat_history = []
MAX_HISTORY_TURNS = 10

DEPTH_TRIGGERS = (
    "in depth", "in detail", "more detail", "elaborate",
    "explain more", "tell me more", "go deeper",
    "more in depth", "a bit more", "expand on",
)


def chat(message):
    messages = _build_chat_messages(message)

    r = requests.post(
        OLLAMA_URL,
        json={"model": CHAT_MODEL, "messages": messages, "stream": True, "think": False},
        stream=True,
    )
    full_reply = ""
    for line in r.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        piece = chunk.get("message", {}).get("content", "")
        print(piece, end="", flush=True)
        full_reply += piece
        if chunk.get("done"):
            print()
            break
    chat_history.append({"role": "assistant", "content": full_reply})
    return full_reply


def parse_when(when_str):
    if not when_str:
        return None
    when_str = _normalize_time_string(when_str)
    return dateparser.parse(
        when_str,
        settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(),
        },
    )


def _normalize_time_string(s):
    """Fix common Whisper transcription artifacts in spoken times.
    Converts patterns like '9 27 pm', '9.27 pm', '9,27 pm', '9-27 pm' to '9:27 pm'."""
    # Match: 1-2 digits, any non-alphanumeric separator(s), exactly 2 digits, then am/pm
    s = re.sub(
        r'\b(\d{1,2})[^\w]+(\d{2})\s*(a\.?m\.?|p\.?m\.?)\b',
        r'\1:\2 \3',
        s,
        flags=re.IGNORECASE,
    )
    return s


def handle_add_reminder(text, when_str):
    fire_at = parse_when(when_str)
    if not fire_at:
        msg = f"Couldn't understand the time '{when_str}'. Try 'in 5 minutes', 'tomorrow at 8pm', etc."
        print(msg)
        return False, msg
    if fire_at <= datetime.now():
        msg = f"That time ({fire_at.strftime('%a %b %d at %I:%M %p')}) is already past."
        print(msg)
        return False, msg
    text = text.strip()
    if text.lower().startswith("to "):
        text = text[3:]
    if not text:
        text = "(unspecified)"
    rid = reminders.add_and_schedule(text, fire_at, kind="once")
    msg = f"✓ Reminder #{rid} set for {fire_at.strftime('%A %b %d at %I:%M %p')}: {text}"
    print(msg)
    return True, msg


def handle_add_recurring(text, when_str, kind):
    if kind not in ("yearly", "monthly", "weekly", "daily"):
        msg = f"Unknown recurrence kind '{kind}'."
        print(msg)
        return False, msg
    fire_at = parse_when(when_str)
    if not fire_at:
        msg = f"Couldn't understand '{when_str}'."
        print(msg)
        return False, msg
    if fire_at <= datetime.now() and kind == "yearly":
        fire_at = fire_at.replace(year=datetime.now().year + 1)
    text = text.strip()
    if not text:
        text = "(unspecified)"
    rid = reminders.add_and_schedule(text, fire_at, kind=kind)
    msg = f"✓ Recurring ({kind}) #{rid} set for {fire_at.strftime('%A %b %d at %I:%M %p')}: {text}"
    print(msg)
    return True, msg


def handle_list_reminders():
    rows = db.list_pending()
    if not rows:
        print("No upcoming reminders.")
        return
    print(f"Reminders ({len(rows)}):")
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
        print(f"  #{r['id']}{kind_tag}  {status}  {when.strftime('%a %b %d %I:%M %p')}  — {r['text']}")


def handle_cancel_reminder(ids):
    if ids == "all":
        all_pending = db.list_pending(include_recent=False)
        ids = [r["id"] for r in all_pending]
    if not ids:
        print("No reminders to cancel.")
        return
    cancelled = []
    for rid in ids:
        if db.cancel(int(rid)):
            job_id = f"reminder_{rid}"
            if reminders.scheduler.get_job(job_id):
                reminders.scheduler.remove_job(job_id)
            cancelled.append(rid)
    if cancelled:
        print(f"✓ Cancelled reminder{'s' if len(cancelled) > 1 else ''}: {', '.join(f'#{i}' for i in cancelled)}")
    else:
        print("No matching reminders found.")


def voice_input_loop():
    """Push-to-talk: hold F9 to record, release to transcribe and run."""
    recorder = voice.Recorder()
    print(f"\nVoice mode: hold {VOICE_HOTKEY.upper()} to talk. Release to send.")
    print(f"Type or press hotkey. 'voice off' to disable speech output.\n")

    is_recording = False

    while True:
        try:
            # Check for hotkey press (non-blocking)
            if keyboard.is_pressed(VOICE_HOTKEY):
                if not is_recording:
                    print("🎙  recording...", end="", flush=True)
                    recorder.start()
                    is_recording = True
                time.sleep(0.05)
                continue

            if is_recording:
                # Hotkey released
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

def is_utility_command(text):
    """Check if text is a hardcoded utility command. Returns the canonical
    command name or None."""
    # Normalize: lowercase, strip trailing punctuation and whitespace
    normalized = re.sub(r"[.,!?;:\s]+$", "", text.lower().strip())

    UTILITY_COMMANDS = {
        "exit":         {"exit", "quit", "bye", "goodbye", "stop", "shut down",
                         "shutdown", "end", "close assistant"},
        "voice_off":    {"voice off", "stop talking", "be quiet", "shush",
                         "mute", "silence"},
        "voice_on":     {"voice on", "speak again", "unmute", "talk to me"},
        "reset":        {"reset", "clear", "new chat", "clear chat",
                         "forget everything", "start over"},
    }

    for canonical, variants in UTILITY_COMMANDS.items():
        if normalized in variants:
            return canonical
    return None

def tray_mode():
    acquire_lock()
    global _window_bridge  # NEW

    reminders.start()

    qt_app = QApplication.instance() or QApplication([])
    qt_app.setQuitOnLastWindowClosed(False)

    bridge = chat_window.ChatBridge()
    _window_bridge = bridge  # NEW — handlers can now post to the window
    window = chat_window.ChatWindow(bridge)

    # We emit a signal so the popup is created on the GUI thread.
    def display_reminder(title, body):
        bridge.reminder_fired.emit(title, body)
    reminders.set_display_callback(display_reminder)

    def _show_window():
        window.show()
        window.raise_()
        window.activateWindow()

    def _do_quit():
        qt_app.quit()

    bridge.show_window_requested.connect(_show_window)
    bridge.quit_requested.connect(_do_quit)

    _active_popups = []  # keep references so they don't get garbage collected

    def _show_reminder_popup(title, body):
        try:
            popup = chat_window.ReminderPopup(title, body)
            _active_popups.append(popup)
            popup.destroyed.connect(
                lambda: _active_popups.remove(popup) if popup in _active_popups else None
            )
            popup.show()
            popup.raise_()
            popup.activateWindow()
        except Exception as e:
            import traceback
            print(f"[POPUP] FAILED: {e}", flush=True)
            traceback.print_exc()

    bridge.reminder_fired.connect(_show_reminder_popup)

    def _on_submit(text):
        bridge.user_said.emit(text)  # show user message in window immediately
        # Run the handler in a worker thread so the GUI stays responsive
        # while the LLM is generating.
        threading.Thread(target=handle_window, args=(text,), daemon=True).start()

    window.submit_requested.connect(_on_submit)

    running = {"alive": True}

    def quit_cb():
        running["alive"] = False
        bridge.quit_requested.emit()

    def voice_toggle_cb():
        global VOICE_OUTPUT
        VOICE_OUTPUT = not VOICE_OUTPUT

    def voice_status_cb():
        return VOICE_OUTPUT

    def open_chat_cb():
        bridge.show_window_requested.emit()

    app = tray_module.TrayApp(
        on_quit=quit_cb,
        on_voice_toggle=voice_toggle_cb,
        get_voice_status=voice_status_cb,
        on_open_chat=open_chat_cb,
    )
    app.run_in_thread()

    voice_thread = threading.Thread(
        target=_voice_loop_for_tray,
        args=(running,),
        daemon=True,
    )
    voice_thread.start()

    qt_app.exec()
    running["alive"] = False
    voice_thread.join(timeout=1.0)


def _voice_loop_for_tray(running):
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
                    # Show what was heard in the window
                    if _window_bridge:
                        _window_bridge.user_said.emit(text)
                    # Route through window handler — same path as typed input
                    handle_window(text)
                continue
            time.sleep(0.05)
        except Exception as e:
            print(f"Voice loop error: {e}", flush=True)
            is_recording = False


LATEX_SYSTEM_PROMPT = """You are a LaTeX conversion tool. Convert the user's input into correct LaTeX.

Rules:
- Output ONLY the LaTeX code. No explanations, no commentary, no markdown code fences.
- Use standard LaTeX math notation.
- For display mode, wrap in \\[ ... \\]. For inline mode, wrap in \\( ... \\).
- Preserve the mathematical meaning exactly. Do not solve, simplify, or alter the math.
- Use proper LaTeX commands: \\frac{}{}, \\int, \\sum, \\sqrt{}, Greek letters as \\alpha etc., \\partial, \\nabla, subscripts with _ and superscripts with ^.
- If the input is already LaTeX, clean it up and fix any errors rather than double-wrapping it."""

LATEX_PREFS_FILE = Path(__file__).parent / "latex_preferences.md"


def _load_latex_prefs():
    if LATEX_PREFS_FILE.exists():
        return LATEX_PREFS_FILE.read_text(encoding="utf-8").strip()
    return ""


def convert_to_latex(text, mode="display"):
    """Convert text/equations to LaTeX. Returns the LaTeX string."""
    system = LATEX_SYSTEM_PROMPT
    prefs = _load_latex_prefs()
    if prefs:
        system += f"\n\nUser style preferences:\n{prefs}"

    mode_instruction = (
        "Use display mode: wrap in \\[ ... \\]."
        if mode == "display"
        else "Use inline mode: wrap in \\( ... \\)."
    )
    user_prompt = f"{mode_instruction}\n\nConvert this to LaTeX:\n{text}"

    r = requests.post(
        OLLAMA_URL,
        json={
            "model": CHAT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
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
    return latex.strip()


SYSTEM_INFO_PROMPT = """You are answering a question about the user's computer. \
Below is the current system data as JSON. Answer the user's specific question \
concisely using this data. Only mention what they asked about — don't dump every stat \
unless they asked for a full overview. Use friendly units (GB, %, etc.).

System data:
%SYSTEM_DATA%"""


def handle_system_info(user_text):
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


# Allow common math functions/constants in expressions
_MATH_NAMES = {
    "pi": math.pi, "e": math.e, "tau": math.tau,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "ln": math.log, "exp": math.exp,
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "factorial": math.factorial,
}

# A pure-math expression: digits, operators, parens, decimal points,
# and the allowed function/constant names — nothing else.
_MATH_RE = re.compile(r'^[\d\s+\-*/().,%^]+$')


def try_calculator(text):
    """If text is a pure arithmetic expression, evaluate it. Returns
    the result string, or None if it's not pure math."""
    expr = text.strip().rstrip("=?").strip()
    # Quick reject: must contain a digit and an operator
    if not re.search(r'\d', expr) or not re.search(r'[+\-*/^%]', expr):
        return None
    # Allow function-style expressions too (sqrt(2), sin(0.5), etc.)
    test = re.sub(r'[a-z_]+', '', expr.lower())  # strip names to check the rest
    if not _MATH_RE.match(test if test.strip() else "0"):
        return None
    try:
        # simpleeval uses ** for power; convert ^ to **
        expr_py = expr.replace("^", "**")
        result = simple_eval(expr_py, names=_MATH_NAMES, functions=_MATH_NAMES)
        return str(result)
    except Exception:
        return None
    

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


def handle_web_search(service, query, browser):
    if not query or not query.strip():
        return False, "What should I search for?"
    service = (service or "google").lower()
    if service not in SEARCH_SERVICES:
        return False, f"Don't know the service '{service}'. Try google, maps, youtube, wikipedia, amazon, github, stackoverflow, or images."

    url = SEARCH_SERVICES[service].format(q=quote_plus(query.strip()))
    open_url(browser, url)
    return True, f"Searching {service} for '{query}'."
    

# ----------------------------
# Dispatcher
# ----------------------------
def handle(user_text):
    lower = user_text.lower().strip()
    if lower in ("reset", "clear", "new chat"):
        chat_history.clear()
        print("(chat history cleared)")
        return
    if lower in ("clear fired", "clear history", "clean up"):
        rows = db.list_pending()
        cleared = 0
        for r in rows:
            if r["fired_at"] and r["kind"] == "once":
                db.cancel(r["id"])
                cleared += 1
        print(f"Cleared {cleared} fired reminder(s).")
        return
    # Pure-math shortcut — evaluate locally, skip the LLM entirely
    calc = try_calculator(user_text)
    if calc is not None:
        print(f"= {calc}")
        return
    
    calc_cmd = symbolic_math.parse_calculus_command(user_text)
    if calc_cmd is not None:
        body, error = symbolic_math.run(calc_cmd)
        print(error if error else body)
        return
    
    parsed = classify_intent(user_text)
    intent = parsed.get("intent", "chat")

    if intent == "open_app":
        open_app((parsed.get("app") or "").lower())
    elif intent == "open_url":
        raw_browser = parsed.get("browser")
        if raw_browser in (None, "", "null", "none", "None", "Null"):
            browser = None
        else:
            browser = raw_browser.lower()
        url = parsed.get("url", "")
        open_url(browser, url)
    elif intent == "close_app":
        close_app((parsed.get("app") or "").lower())
    elif intent == "add_reminder":
        handle_add_reminder(parsed.get("text", ""), parsed.get("when", ""))
    elif intent == "add_recurring":
        handle_add_recurring(
            parsed.get("text", ""),
            parsed.get("when", ""),
            parsed.get("kind", "yearly"),
        )
    elif intent == "list_reminders":
        handle_list_reminders()
    elif intent == "cancel_reminder":
        handle_cancel_reminder(parsed.get("ids", []))
    elif intent == "attach_pdf":
        path = parsed.get("path", "")
        if not path or not path.lower().endswith(".pdf"):
            print("To attach a PDF, give a full path ending in .pdf.")
        else:
            try:
                info = pdf_handler.attach(path)
                filename = Path(info["path"]).name
                print(f"Loaded {filename} ({info['pages']} pages, "
                    f"~{info['estimated_tokens']:,} tokens, {info['chunks']} chunks).")
            except FileNotFoundError:
                print(f"File not found: {path}")
            except Exception as e:
                print(f"Failed to read PDF: {e}")
    elif intent == "detach_pdf":
        was_attached = pdf_handler.detach()
        print("PDF unloaded." if was_attached else "No PDF was attached.")
    elif intent == "to_latex":
        latex, error = handle_to_latex(
            parsed.get("text", ""),
            parsed.get("mode", "display"),
        )
        print(error if error else f"\n{latex}\n")
    elif intent == "system_info":
        print(handle_system_info(user_text))
    elif intent == "web_search":
        raw_browser = parsed.get("browser")
        browser = None if raw_browser in (None, "", "null", "none", "None", "Null") else raw_browser.lower()
        success, msg = handle_web_search(
            parsed.get("service"),
            parsed.get("query", ""),
            browser,
        )
        print(msg)
    else:
        chat(user_text)


def handle_voice(user_text):
    cmd = is_utility_command(user_text)

    if cmd == "exit":
        raise KeyboardInterrupt
    if cmd == "voice_off":
        global VOICE_OUTPUT
        VOICE_OUTPUT = False
        print("(voice output disabled)")
        return
    if cmd == "voice_on":
        VOICE_OUTPUT = True
        print("(voice output enabled)")
        return
    if cmd == "reset":
        chat_history.clear()
        print("(chat history cleared)")
        return
    calc = try_calculator(user_text)
    if calc is not None:
        print(f"= {calc}")
        if VOICE_OUTPUT:
            voice.speak(f"That's {calc}")
        return

    parsed = classify_intent(user_text)
    intent = parsed.get("intent", "chat")

    # Non-chat intents — just speak a short confirmation
    if intent == "open_app":
        open_app((parsed.get("app") or "").lower())
        if VOICE_OUTPUT: voice.speak(f"Opening {parsed.get('app', 'app')}.")
    elif intent == "open_url":
        raw_browser = parsed.get("browser")
        if raw_browser in (None, "", "null", "none", "None", "Null"):
            browser = None
        else:
            browser = raw_browser.lower()
        url = parsed.get("url", "")
        open_url(browser, url)
        if VOICE_OUTPUT: voice.speak("Opening it now.")
    elif intent == "close_app":
        close_app((parsed.get("app") or "").lower())
        if VOICE_OUTPUT: voice.speak(f"Closed {parsed.get('app', 'app')}.")
    elif intent == "add_reminder":
        handle_add_reminder(parsed.get("text", ""), parsed.get("when", ""))
        if VOICE_OUTPUT: voice.speak("Reminder set.")
    elif intent == "add_recurring":
        handle_add_recurring(parsed.get("text", ""), parsed.get("when", ""), parsed.get("kind", "yearly"))
        if VOICE_OUTPUT: voice.speak("Recurring reminder set.")
    elif intent == "list_reminders":
        handle_list_reminders()
        # Don't speak the full list — would be tedious. Just acknowledge.
        if VOICE_OUTPUT: voice.speak("Listed in the terminal.")
    elif intent == "cancel_reminder":
        handle_cancel_reminder(parsed.get("ids", []))
        if VOICE_OUTPUT: voice.speak("Cancelled.")
    elif intent == "system_info":
        reply = handle_system_info(user_text)
        print(reply)
        if VOICE_OUTPUT:
            voice.speak(reply)
    elif intent == "web_search":
        raw_browser = parsed.get("browser")
        browser = None if raw_browser in (None, "", "null", "none", "None", "Null") else raw_browser.lower()
        success, msg = handle_web_search(
            parsed.get("service"),
            parsed.get("query", ""),
            browser,
        )
        if VOICE_OUTPUT:
            voice.speak(msg if success else "Couldn't search for that.")
    else:
        # Chat — capture full reply, then speak it
        reply = chat_capture(user_text)
        if VOICE_OUTPUT and reply:
            voice.speak(reply)


def handle_attach_pdf(path):
    # Guard: must be a real .pdf path
    if not path or not path.lower().endswith(".pdf"):
        if _window_bridge:
            _window_bridge.system_message.emit(
                "To attach a PDF, give me the full file path ending in .pdf. "
                "Or drag a PDF onto the window."
            )
        return

    # Try to load it
    try:
        info = pdf_handler.attach(path)
    except FileNotFoundError:
        if _window_bridge:
            _window_bridge.system_message.emit(f"File not found: {path}")
        return
    except ValueError as e:
        if _window_bridge:
            _window_bridge.system_message.emit(str(e))
        return
    except Exception as e:
        if _window_bridge:
            _window_bridge.system_message.emit(f"Failed to read PDF: {e}")
        return

    # Build the confirmation message
    filename = Path(info["path"]).name
    msg = (
        f"Loaded {filename} ({info['pages']} pages, ~{info['estimated_tokens']:,} tokens). "
        f"Ask me anything about it. Say 'detach' to unload."
    )
    if info["truncated"]:
        msg += (
            " (Document was long and got truncated — questions about later "
            "pages may be incomplete.)"
        )

    # Update status bar and post the message
    if _window_bridge:
        _window_bridge.pdf_attached.emit(filename)
        _window_bridge.system_message.emit(msg)


def handle_detach_pdf():
    was_attached = pdf_handler.detach()
    if _window_bridge:
        if was_attached:
            _window_bridge.pdf_attached.emit("")
            _window_bridge.system_message.emit("PDF unloaded. Back to general chat.")
        else:
            _window_bridge.system_message.emit("No PDF was attached.")


def handle_to_latex(text, mode):
    if not text or not text.strip():
        return None, "What should I convert? Paste the text or equation."
    latex = convert_to_latex(text, mode)
    return latex, None


def _build_chat_messages(message):
    """Build the message list for a chat call, injecting PDF context if attached.
    Appends the user message to chat_history as a side effect."""
    if pdf_handler.is_attached():
        info = pdf_handler.get_info()
        SUMMARY_TRIGGERS = ("summarize", "summary", "overview", "what is this paper about",
                            "summarize this paper", "what's this paper about", "tl;dr",
                            "main points", "key findings", "conclusion")
        is_summary = any(t in message.lower() for t in SUMMARY_TRIGGERS)
        if is_summary:
            context = pdf_handler.get_context()
        else:
            relevant_chunks = pdf_rag.retrieve(message)
            context = "\n\n---\n\n".join(relevant_chunks)

        context_message = (
            f"You are answering a question about the document '{info['filename']}'. "
            f"Below are the most relevant excerpts. Answer using ONLY these excerpts. "
            f"Quote exact numbers and values precisely as written. If the excerpts don't "
            f"contain the answer, say the document doesn't appear to specify it.\n\n"
            f"--- RELEVANT EXCERPTS ---\n{context}\n--- END EXCERPTS ---"
        )
        chat_history.append({"role": "user", "content": message})
        if len(chat_history) > MAX_HISTORY_TURNS * 2:
            del chat_history[: len(chat_history) - MAX_HISTORY_TURNS * 2]
        return [
            {"role": "system", "content": context_message},
            {"role": "user", "content": message},
        ]
    else:
        chat_history.append({"role": "user", "content": message})
        if len(chat_history) > MAX_HISTORY_TURNS * 2:
            del chat_history[: len(chat_history) - MAX_HISTORY_TURNS * 2]
        return [{"role": "system", "content": LOKI_IDENTITY}, *chat_history]


def chat_capture(message):
    """Like chat() but returns the full reply string instead of just streaming."""
    messages = _build_chat_messages(message)

    r = requests.post(
        OLLAMA_URL,
        json={"model": CHAT_MODEL, "messages": messages, "stream": True, "think": False},
        stream=True,
    )
    full_reply = ""
    for line in r.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        piece = chunk.get("message", {}).get("content", "")
        print(piece, end="", flush=True)
        full_reply += piece
        if chunk.get("done"):
            print()
            break
    chat_history.append({"role": "assistant", "content": full_reply})
    return full_reply


def chat_capture_silent(message):
    messages = _build_chat_messages(message)
    r = requests.post(
        OLLAMA_URL,
        json={"model": CHAT_MODEL, "messages": messages, "stream": False, "think": False},
        timeout=120,
    )
    reply = r.json()["message"]["content"]
    reply = re.sub(r'^[\s\'",+\-•·\u0080-\uFFFF]{1,10}(?=[A-Z])', '', reply).strip()
    chat_history.append({"role": "assistant", "content": reply})
    return reply


# Module-level reference to the bridge so handlers can post to the window.
# Set during tray_mode() startup.
_window_bridge = None

def handle_window(user_text):
    """Like handle()/handle_voice() but routes output through the chat window."""
    cmd = is_utility_command(user_text)

    if cmd == "exit":
        if _window_bridge:
            _window_bridge.quit_requested.emit()
        return
    if cmd == "voice_off":
        global VOICE_OUTPUT
        VOICE_OUTPUT = False
        if _window_bridge:
            _window_bridge.system_message.emit("Voice output disabled.")
        return
    if cmd == "voice_on":
        VOICE_OUTPUT = True
        if _window_bridge:
            _window_bridge.system_message.emit("Voice output enabled.")
        return
    if cmd == "reset":
        chat_history.clear()
        if _window_bridge:
            _window_bridge.system_message.emit("Chat history cleared.")
        return

    if _window_bridge:
        _window_bridge.thinking_started.emit()

    calc = try_calculator(user_text)
    if calc is not None:
        if _window_bridge:
            _window_bridge.system_message.emit(f"= {calc}")
        return
    
    calc_cmd = symbolic_math.parse_calculus_command(user_text)
    if calc_cmd is not None:
        body, error = symbolic_math.run(calc_cmd)
        if _window_bridge:
            _window_bridge.system_message.emit(error) if error else \
                _window_bridge.assistant_said.emit(body)
        return

    try:
        parsed = classify_intent(user_text)
        intent = parsed.get("intent", "chat")

        if intent == "open_app":
            app = (parsed.get("app") or "").lower()
            open_app(app)
            if _window_bridge:
                _window_bridge.system_message.emit(f"Opening {app}.")
        elif intent == "open_url":
            raw_browser = parsed.get("browser")
            if raw_browser in (None, "", "null", "none", "None", "Null"):
                browser = None
            else:
                browser = raw_browser.lower()
            url = parsed.get("url", "")
            open_url(browser, url)
            if _window_bridge:
                location = f"in {browser}" if browser else "in default browser"
                _window_bridge.system_message.emit(f"Opening {url} {location}.")
        elif intent == "close_app":
            app = (parsed.get("app") or "").lower()
            close_app(app)
            if _window_bridge:
                _window_bridge.system_message.emit(f"Closed {app}.")

        elif intent == "attach_pdf":
            handle_attach_pdf(parsed.get("path", ""))
        elif intent == "detach_pdf":
            handle_detach_pdf()

        elif intent == "add_reminder":
            success, msg = handle_add_reminder(parsed.get("text", ""), parsed.get("when", ""))
            if _window_bridge:
                _window_bridge.system_message.emit(msg)
        elif intent == "add_recurring":
            success, msg = handle_add_recurring(
                parsed.get("text", ""), parsed.get("when", ""),
                parsed.get("kind", "yearly"),
            )
            if _window_bridge:
                _window_bridge.system_message.emit(msg)
        elif intent == "list_reminders":
            text = format_reminders_for_display()
            if _window_bridge:
                _window_bridge.system_message.emit(text)
        elif intent == "cancel_reminder":
            handle_cancel_reminder(parsed.get("ids", []))
            if _window_bridge:
                _window_bridge.system_message.emit("Cancelled.")
        elif intent == "to_latex":
            latex, error = handle_to_latex(
                parsed.get("text", ""),
                parsed.get("mode", "display"),
            )
            if _window_bridge:
                if error:
                    _window_bridge.system_message.emit(error)
                else:
                    _window_bridge.assistant_said.emit(f"```latex\n{latex}\n```")
        elif intent == "system_info":
            reply = handle_system_info(user_text)
            if _window_bridge and reply:
                _window_bridge.assistant_said.emit(reply)
        elif intent == "web_search":
            raw_browser = parsed.get("browser")
            if raw_browser in (None, "", "null", "none", "None", "Null"):
                browser = None
            else:
                browser = raw_browser.lower()
            success, msg = handle_web_search(
                parsed.get("service"),
                parsed.get("query", ""),
                browser,
            )
            if _window_bridge:
                _window_bridge.system_message.emit(msg)
        else:
            reply = chat_capture_silent(user_text)
            if _window_bridge and reply:
                _window_bridge.assistant_said.emit(reply)
    finally:
        if _window_bridge:
            _window_bridge.thinking_stopped.emit()


def format_reminders_for_display():
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
        lines.append(f"  #{r['id']}{kind_tag} {status}  {when.strftime('%a %b %d %I:%M %p')}  — {r['text']}")
    return "\n".join(lines)


# ----------------------------
# REPL
# ----------------------------
def main():
    if "--tray" in sys.argv:
        tray_mode()
        return
    
    if "--voice" in sys.argv:
        voice_input_loop()
        return
    
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
        if cmd == "reset":
            chat_history.clear()
            print("(chat history cleared)")
            continue
        
        handle(user_input)

if __name__ == "__main__":
    main()