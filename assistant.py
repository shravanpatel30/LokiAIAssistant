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

if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Loki.LocalAssistant.1.0")

LOCKFILE = os.path.join(tempfile.gettempdir(), "loki_assistant.lock")

def _pid_is_running(pid):
    """Cross-platform check: is this PID a live process?"""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows: use OpenProcess to check
            import ctypes
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

JSON shapes by intent:
- {"intent": "open_app", "app": "<name>"}
- {"intent": "open_url", "browser": "<name or null>", "url": "<url>"}
- {"intent": "close_app", "app": "<name>"}
- {"intent": "add_reminder", "text": "<what to remind about, without the time>", "when": "<the time phrase from the user's message>"}
- {"intent": "add_recurring", "text": "<event description, without the time>", "when": "<the date/time phrase>", "kind": "yearly|monthly|weekly|daily"}
- {"intent": "list_reminders"}
- {"intent": "cancel_reminder", "ids": [<integer>, ...]}
- {"intent": "chat"}

Installed apps:
%APP_LIST%

Rules:
- For open/close intents, pick the closest matching app from the list above.
- "remind me to X at/on/in Y" -> add_reminder with text=X and when=Y.
- "my anniversary is October 12" or "Sarah's birthday is March 4" -> add_recurring with kind=yearly.
- "every monday at 9am, X" -> add_recurring with kind=weekly.
- For add_reminder and add_recurring, separate the action from the time:
  - "text" gets only what to remind about (no time)
  - "when" gets only the time phrase, copied from the user's message
  - Example: "remind me to call mom tomorrow at 5pm" -> {"intent": "add_reminder", "text": "call mom", "when": "tomorrow at 5pm"}
- Copy time phrases from the user's message faithfully. If they wrote "in 30 secs", put "in 30 secs" — not "30 minutes" or "in half a minute".
- "what reminders do I have", "show my reminders", "list events" -> list_reminders.
- "cancel reminder 3", "delete reminder 5" -> cancel_reminder with the number as id.
- "cancel reminder 3" -> {"intent": "cancel_reminder", "ids": [3]}
- "delete reminders 4, 5, 6" -> {"intent": "cancel_reminder", "ids": [4, 5, 6]} or "delete reminders 4, 5 and 6" -> {"intent": "cancel_reminder", "ids": [4, 5, 6]}
- "cancel all reminders" -> {"intent": "cancel_reminder", "ids": "all"}
- For "chat" intent, the JSON is EXACTLY {"intent": "chat"} — no other fields. Do NOT include "response", "message", "answer", or any other fields. Output only the four characters of valid JSON: { " : } and the literal text "intent" and "chat". Stop after the closing brace.
- Respond with ONLY the JSON. No markdown, no code fences, no commentary."""


VALID_INTENTS = {
    "open_app", "open_url", "close_app",
    "add_reminder", "add_recurring", "list_reminders", "cancel_reminder",
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
    if browser:
        return open_app(browser, url_arg=url)
    # No browser specified: use OS default
    import webbrowser
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
    # Auto-expand vague depth requests
    lower = message.lower()
    if any(trigger in lower for trigger in DEPTH_TRIGGERS):
        message = (
            f"{message}\n\n"
            "(Provide a thorough multi-paragraph answer with concrete examples and mechanisms.)"
        )

    chat_history.append({"role": "user", "content": message})
    if len(chat_history) > MAX_HISTORY_TURNS * 2:
        del chat_history[: len(chat_history) - MAX_HISTORY_TURNS * 2]

    t_start = time.perf_counter()
    t_first_token = None
    token_count = 0

    r = requests.post(
        OLLAMA_URL,
        json={
            "model": CHAT_MODEL,
            "messages": chat_history,
            "stream": True,
        },
        stream=True,
    )

    full_reply = ""
    for line in r.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        piece = chunk.get("message", {}).get("content", "")
        if piece and t_first_token is None:
            t_first_token = time.perf_counter()
            print()
        print(piece, end="", flush=True)
        full_reply += piece
        token_count += 1
        if chunk.get("done"):
            print()
            break

    t_end = time.perf_counter()

    chat_history.append({"role": "assistant", "content": full_reply})

def parse_when(when_str):
    if not when_str:
        return None
    return dateparser.parse(
        when_str,
        settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(),
        },
    )


def handle_add_reminder(text, when_str):
    fire_at = parse_when(when_str)
    if not fire_at:
        print(f"Couldn't understand the time '{when_str}'. Try 'in 5 minutes', 'tomorrow at 8pm', etc.")
        return
    if fire_at <= datetime.now():
        print(f"That time ({fire_at}) is already past.")
        return
    text = text.strip()
    if text.lower().startswith("to "):
        text = text[3:]
    if not text:
        text = "(unspecified)"
    rid = reminders.add_and_schedule(text, fire_at, kind="once")
    print(f"✓ Reminder #{rid} set for {fire_at.strftime('%A %b %d at %I:%M %p')}: {text}")


def handle_add_recurring(text, when_str, kind):
    if kind not in ("yearly", "monthly", "weekly", "daily"):
        print(f"Unknown recurrence kind '{kind}'.")
        return
    fire_at = parse_when(when_str)
    if not fire_at:
        print(f"Couldn't understand '{when_str}'.")
        return
    if fire_at <= datetime.now() and kind == "yearly":
        fire_at = fire_at.replace(year=datetime.now().year + 1)
    text = text.strip()
    if not text:
        text = "(unspecified)"
    rid = reminders.add_and_schedule(text, fire_at, kind=kind)
    print(f"✓ Recurring ({kind}) #{rid} set for {fire_at.strftime('%A %b %d at %I:%M %p')}: {text}")


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
    import tray as tray_module
    global _window_bridge  # NEW

    reminders.start()

    qt_app = QApplication.instance() or QApplication([])
    qt_app.setQuitOnLastWindowClosed(False)

    bridge = chat_window.ChatBridge()
    _window_bridge = bridge  # NEW — handlers can now post to the window
    window = chat_window.ChatWindow(bridge)

    def _show_window():
        window.show()
        window.raise_()
        window.activateWindow()

    def _do_quit():
        qt_app.quit()

    bridge.show_window_requested.connect(_show_window)
    bridge.quit_requested.connect(_do_quit)

    # NEW: when user types in the window, route to handler
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

    import threading
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
    parsed = classify_intent(user_text)
    intent = parsed.get("intent", "chat")

    if intent == "open_app":
        open_app((parsed.get("app") or "").lower())
    elif intent == "open_url":
        open_url(
            (parsed.get("browser") or "").lower() or None,
            parsed.get("url", ""),
        )
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

    parsed = classify_intent(user_text)
    intent = parsed.get("intent", "chat")

    # Non-chat intents — just speak a short confirmation
    if intent == "open_app":
        open_app((parsed.get("app") or "").lower())
        if VOICE_OUTPUT: voice.speak(f"Opening {parsed.get('app', 'app')}.")
    elif intent == "open_url":
        open_url((parsed.get("browser") or "").lower() or None, parsed.get("url", ""))
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
    else:
        # Chat — capture full reply, then speak it
        reply = chat_capture(user_text)
        if VOICE_OUTPUT and reply:
            voice.speak(reply)


def chat_capture(message):
    """Like chat() but returns the full reply string instead of just streaming."""
    chat_history.append({"role": "user", "content": message})
    if len(chat_history) > MAX_HISTORY_TURNS * 2:
        del chat_history[: len(chat_history) - MAX_HISTORY_TURNS * 2]

    r = requests.post(
        OLLAMA_URL,
        json={"model": CHAT_MODEL, "messages": chat_history, "stream": True},
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
    """Like chat_capture but doesn't stream to stdout. Returns full reply."""
    chat_history.append({"role": "user", "content": message})
    if len(chat_history) > MAX_HISTORY_TURNS * 2:
        del chat_history[: len(chat_history) - MAX_HISTORY_TURNS * 2]

    r = requests.post(
        OLLAMA_URL,
        json={
            "model": CHAT_MODEL,
            "messages": chat_history,
            "stream": False,
            "think": False,
        },
        timeout=120,
    )
    reply = r.json()["message"]["content"]
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

    try:
        parsed = classify_intent(user_text)
        intent = parsed.get("intent", "chat")

        if intent == "open_app":
            app = (parsed.get("app") or "").lower()
            open_app(app)
            if _window_bridge:
                _window_bridge.system_message.emit(f"Opening {app}.")
        elif intent == "open_url":
            browser = (parsed.get("browser") or "").lower() or None
            url = parsed.get("url", "")
            open_url(browser, url)
            if _window_bridge:
                _window_bridge.system_message.emit(f"Opening {url}{f' in {browser}' if browser else ''}.")
        elif intent == "close_app":
            app = (parsed.get("app") or "").lower()
            close_app(app)
            if _window_bridge:
                _window_bridge.system_message.emit(f"Closed {app}.")
        elif intent == "add_reminder":
            handle_add_reminder(parsed.get("text", ""), parsed.get("when", ""))
            if _window_bridge:
                _window_bridge.system_message.emit("Reminder set.")
        elif intent == "add_recurring":
            handle_add_recurring(
                parsed.get("text", ""), parsed.get("when", ""),
                parsed.get("kind", "yearly"),
            )
            if _window_bridge:
                _window_bridge.system_message.emit("Recurring reminder set.")
        elif intent == "list_reminders":
            text = format_reminders_for_display()
            if _window_bridge:
                _window_bridge.system_message.emit(text)
        elif intent == "cancel_reminder":
            handle_cancel_reminder(parsed.get("ids", []))
            if _window_bridge:
                _window_bridge.system_message.emit("Cancelled.")
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