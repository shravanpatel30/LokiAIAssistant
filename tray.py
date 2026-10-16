"""System tray icon for the assistant."""
import threading
from pathlib import Path

import pystray
from PIL import Image

ICON_FILE = Path(__file__).parent / "AI_Icon.png"


class TrayApp:
    def __init__(self, on_quit, on_voice_toggle, get_voice_status, on_open_chat):
        self.on_quit = on_quit
        self.on_voice_toggle = on_voice_toggle
        self.get_voice_status = get_voice_status
        self.on_open_chat = on_open_chat
        self.icon = None
        self.thread = None

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("Open chat", self._open_chat, default=True),
            pystray.MenuItem(
                lambda item: f"Voice output: {'ON' if self.get_voice_status() else 'OFF'}",
                self._toggle_voice,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    def _open_chat(self, icon, item):
        self.on_open_chat()

    def _toggle_voice(self, icon, item):
        self.on_voice_toggle()
        icon.update_menu()

    def _quit(self, icon, item):
        icon.stop()
        self.on_quit()

    def run(self):
        """Blocks until stop(). Use run_in_thread() instead from the app."""
        self.icon = pystray.Icon(
            "Loki",
            Image.open(ICON_FILE),
            "Loki AI Assistant",
            menu=self._build_menu(),
        )
        self.icon.run()

    def run_in_thread(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self.thread

    def stop(self, timeout=1.0):
        """Remove the icon from the taskbar. Safe to call more than once."""
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
        if self.thread is not None:
            self.thread.join(timeout=timeout)
