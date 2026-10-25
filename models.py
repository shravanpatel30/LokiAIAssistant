"""Which Ollama models Loki uses, and what each installed model can do.

Two roles:
  main    — chat, PDF questions, routing, LaTeX, system info and calculus
            translation (and images too, if it can see)
  vision  — answers questions about attached images

Both are picked from dropdowns in the chat window. settings.json is created
the first time you change one; until then the DEFAULTS below are used.
Anything you `ollama pull` shows up in the dropdowns.
"""
import json
import threading
from pathlib import Path

import requests

OLLAMA = "http://127.0.0.1:11434"   # 127.0.0.1, not localhost: on Windows, localhost tries IPv6 first and adds ~2 s per call
SETTINGS_FILE = Path(__file__).parent / "settings.json"

DEFAULTS = {
    "main_model": "qwen3:8b",
    "vision_model": "qwen3-vl:8b-instruct",
}

_lock = threading.Lock()
_settings = None
_caps_cache = {}     # model name -> set of capabilities ("vision", "thinking", ...)


# ---------- settings ----------
def _load():
    global _settings
    if _settings is None:
        _settings = dict(DEFAULTS)
        try:
            _settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return _settings


def get_setting(key):
    with _lock:
        return _load()[key]


def set_setting(key, value):
    with _lock:
        _load()[key] = value
        try:
            SETTINGS_FILE.write_text(json.dumps(_settings, indent=2), encoding="utf-8")
        except OSError as e:
            print(f"(couldn't save settings: {e})", flush=True)


def main_model():
    return get_setting("main_model")


def vision_model():
    return get_setting("vision_model")


# ---------- what's installed ----------
def installed():
    """Names of installed chat models (embedding-only models are left out).
    Returns [] if Ollama isn't reachable."""
    try:
        data = requests.get(f"{OLLAMA}/api/tags", timeout=3).json()
    except Exception:
        return []
    names = []
    for m in data.get("models", []):
        name = m.get("name", "")
        caps = capabilities(name)
        if caps and "completion" not in caps:      # e.g. nomic-embed-text
            continue
        if not caps and "embed" in name:            # older Ollama: no capability list
            continue
        names.append(name)
    return sorted(names)


def capabilities(model):
    """Set of capabilities Ollama reports, e.g. {"completion", "vision", "thinking"}."""
    if model in _caps_cache:
        return _caps_cache[model]
    try:
        r = requests.post(f"{OLLAMA}/api/show", json={"model": model}, timeout=5)
        caps = set(r.json().get("capabilities", []))
    except Exception:
        return set()          # don't cache failures — Ollama may just be starting
    _caps_cache[model] = caps
    return caps


def can_see(model):
    return "vision" in capabilities(model)


def can_think(model):
    return "thinking" in capabilities(model)


def same_model(a, b):
    """'qwen3:8b' and 'qwen3:8b:latest'-style differences don't count."""
    strip = lambda n: n[:-7] if n.endswith(":latest") else n
    return strip(a) == strip(b)
