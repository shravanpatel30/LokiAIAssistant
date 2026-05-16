"""System tray icon for the assistant."""
import threading
from pathlib import Path
from PIL import Image, ImageDraw
import pystray


def _make_icon():
    """Generate a simple icon programmatically. Replace with a real .ico later."""
    img = Image.new("RGB", (64, 64), color=(30, 30, 40))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill=(80, 200, 120))
    d.text((22, 18), "AI", fill=(255, 255, 255))
    return img


class TrayApp:
    def __init__(self, on_quit, on_voice_toggle, get_voice_status, on_open_chat):
        self.on_quit = on_quit
        self.on_voice_toggle = on_voice_toggle
        self.get_voice_status = get_voice_status
        self.on_open_chat = on_open_chat
        self.icon = None

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
        """Blocks. Run in main thread, or in a daemon thread."""
        icon_path = Path(__file__).parent / "AI_Icon.png"
        self.icon = pystray.Icon(
            "Loki",
            Image.open(icon_path),
            "Loki AI Assistant",
            menu=self._build_menu(),
        )
        self.icon.run()

    def run_in_thread(self):
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t
    
    