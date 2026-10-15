"""Chat window UI for Loki.

The conversation is shown in an embedded web view (web/chat.html) so replies
get proper markdown and KaTeX math rendering. Everything else — input box,
status line, tray behavior, reminder popup — is regular Qt.
"""
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    Qt, Signal, QObject, QTimer, QUrl, QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QFont, QIcon, QColor, QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QFrame, QMenu,
)

BASE_DIR = Path(__file__).parent
CHAT_PAGE = BASE_DIR / "web" / "chat.html"
KATEX_JS = BASE_DIR / "web" / "vendor" / "katex" / "katex.min.js"
HISTORY_FILE = BASE_DIR / "chat_history.jsonl"
ICON_FILE = BASE_DIR / "AI_Icon.png"

WINDOW_HISTORY_LIMIT = 50   # messages shown when the window opens
DISK_HISTORY_LIMIT = 1000   # most recent N messages kept on disk


# ---------------------------------------------------------------------------
# Signals between the assistant (background threads) and the GUI thread
# ---------------------------------------------------------------------------
class ChatBridge(QObject):
    user_said = Signal(str)
    assistant_said = Signal(str)
    system_message = Signal(str)
    show_window_requested = Signal()
    quit_requested = Signal()
    thinking_started = Signal()
    thinking_stopped = Signal()
    pdf_attached = Signal(str)          # filename, or "" when detached
    reminder_fired = Signal(str, str)   # title, body


# ---------------------------------------------------------------------------
# Reminder popup (unchanged)
# ---------------------------------------------------------------------------
class ReminderPopup(QWidget):
    """Bottom-right notification popup with top accent bar and fade-in."""

    ACCENT_COLOR = "#534AB7"

    def __init__(self, title, body):
        super().__init__(
            None,
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumSize(520, 220)
        self.resize(520, 220)

        self.setStyleSheet(f"""
            QWidget#popupRoot {{
                background-color: #1a1a1d;
                border-radius: 12px;
            }}
            QFrame#accentBar {{
                background-color: {self.ACCENT_COLOR};
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }}
            QLabel#labelText {{
                color: #8a8a92;
                font-size: 10pt;
                letter-spacing: 1px;
            }}
            QLabel#titleText {{
                color: #E3E3E3;
                font-size: 16pt;
                font-weight: 600;
            }}
            QLabel#bodyText {{
                color: #c0c0c8;
                font-size: 12pt;
            }}
            QLabel#timeText {{
                color: #6c6c76;
                font-size: 12pt;
            }}
            QPushButton#dismissBtn {{
                background-color: {self.ACCENT_COLOR};
                color: #ffffff;
                font-size: 14px;
                font-weight: 500;
                border: none;
                border-radius: 8px;
                padding: 0 24px;
                min-height: 40px;
            }}
            QPushButton#dismissBtn:hover {{
                background-color: #6359C9;
            }}
        """)

        # Root widget — needed to apply rounded corners cleanly
        root = QWidget(self)
        root.setObjectName("popupRoot")
        root.setGeometry(0, 0, 520, 220)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top accent bar (4px)
        accent = QFrame()
        accent.setObjectName("accentBar")
        accent.setFixedHeight(4)
        outer.addWidget(accent)

        # Content area
        content = QVBoxLayout()
        content.setContentsMargins(24, 20, 24, 18)
        content.setSpacing(8)

        # Header: bell emoji + "REMINDER" label
        header = QHBoxLayout()
        header.setSpacing(8)
        bell = QLabel("🔔")
        bell.setFont(QFont("Segoe UI Emoji", 16))
        label = QLabel("REMINDER")
        label.setObjectName("labelText")
        header.addWidget(bell)
        header.addWidget(label)
        header.addStretch()
        content.addLayout(header)

        # The reminder text itself, larger
        title_label = QLabel(body)
        title_label.setObjectName("titleText")
        title_label.setWordWrap(True)
        content.addWidget(title_label)

        content.addStretch()

        # Bottom row: time on left, button on right
        bottom = QHBoxLayout()
        time_label = QLabel(f"🕓  {self._format_time()}")
        time_label.setObjectName("timeText")
        bottom.addWidget(time_label)
        bottom.addStretch()

        dismiss = QPushButton("Dismiss")
        dismiss.setObjectName("dismissBtn")
        dismiss.clicked.connect(self.close)
        dismiss.setCursor(Qt.PointingHandCursor)
        bottom.addWidget(dismiss)

        content.addLayout(bottom)
        outer.addLayout(content)

        self._position_bottom_right()
        self._fade_in()

    def _format_time(self):
        return f"Today {datetime.now().strftime('%I:%M %p').lstrip('0')}"

    def _position_bottom_right(self):
        screen = QApplication.primaryScreen().availableGeometry()
        margin = 20
        x = screen.right() - self.width() - margin
        y = screen.bottom() - self.height() - margin
        self.move(x, y)

    def _fade_in(self):
        self.setWindowOpacity(0.0)
        self._fade_animation = QPropertyAnimation(self, b"windowOpacity")
        self._fade_animation.setDuration(220)
        self._fade_animation.setStartValue(0.0)
        self._fade_animation.setEndValue(1.0)
        self._fade_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_animation.start()


# ---------------------------------------------------------------------------
# "Loki is thinking..." indicator in the status line
# ---------------------------------------------------------------------------
class Throbber:
    FRAMES = ["Loki is thinking", "Loki is thinking.",
              "Loki is thinking..", "Loki is thinking..."]

    def __init__(self, status_label, idle_text):
        self.status_label = status_label
        self.idle_text = idle_text          # function returning the resting text
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.frame = 0

    @property
    def running(self):
        return self.timer.isActive()

    def start(self):
        self.frame = 0
        self.status_label.setText(self.FRAMES[0])
        self.timer.start(400)

    def stop(self):
        self.timer.stop()
        self.status_label.setText(self.idle_text())

    def _tick(self):
        self.frame = (self.frame + 1) % len(self.FRAMES)
        self.status_label.setText(self.FRAMES[self.frame])


# ---------------------------------------------------------------------------
# Web page wrapper: keeps the chat page from navigating away
# ---------------------------------------------------------------------------
class ChatPage(QWebEnginePage):
    """Links open in your normal browser; a PDF dropped onto the chat is
    reported via pdf_dropped instead of being opened in the view."""

    pdf_dropped = Signal(str)

    def __init__(self, home_path, parent=None):
        super().__init__(parent)
        self.home_path = Path(home_path).resolve()

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.isLocalFile():
            path = Path(url.toLocalFile())
            if path.resolve() == self.home_path:
                return True                               # our own chat page
            if path.suffix.lower() == ".pdf":
                self.pdf_dropped.emit(str(path))
        elif url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        return False                                      # never leave the chat

    def createWindow(self, _type):
        return None   # ignore "open in new window" requests

    def javaScriptConsoleMessage(self, level, message, line, source):
        # JS errors end up in the log instead of disappearing
        print(f"[chat.js:{line}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Main chat window
# ---------------------------------------------------------------------------
class ChatWindow(QMainWindow):
    submit_requested = Signal(str)   # user typed something and pressed Enter

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        self.setWindowTitle("Loki")
        self.resize(700, 600)
        self.setWindowIcon(QIcon(str(ICON_FILE)))
        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # --- Conversation view (web page) ---
        self._page_ready = False
        self._pending_js = []      # calls made before the page finished loading

        self.view = QWebEngineView()
        home_url = QUrl.fromLocalFile(str(CHAT_PAGE.resolve()))
        self.page = ChatPage(CHAT_PAGE, self.view)
        self.page.setBackgroundColor(QColor("#131314"))   # no white flash on load
        self.page.pdf_dropped.connect(self._on_pdf_dropped)
        self.page.loadFinished.connect(self._on_page_loaded)
        self.view.setPage(self.page)

        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

        # Let drops fall through to the window (handled in dropEvent below)
        self.view.setAcceptDrops(False)

        # Right-click menu with just "Copy" (the default one has Reload/Back,
        # which would wipe the chat)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)

        self.view.load(home_url)
        layout.addWidget(self.view)

        # --- Input row ---
        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Type a message and press Enter...")
        self.input_box.setStyleSheet("""
            QLineEdit {
                background-color: #1E1F20;
                color: #E3E3E3;
                border: 1px solid #2a2a2e;
                border-radius: 8px;
                padding: 10px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11pt;
            }
        """)
        self.input_box.returnPressed.connect(self._on_submit)
        input_row.addWidget(self.input_box)

        self.send_button = QPushButton("Send")
        self.send_button.setStyleSheet("""
            QPushButton {
                background-color: #89b4fa;
                color: #1e1e2e;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #b4befe; }
        """)
        self.send_button.clicked.connect(self._on_submit)
        input_row.addWidget(self.send_button)
        layout.addLayout(input_row)

        # --- Status line ---
        self._idle_status = "Ready"
        self.status = QLabel(self._idle_status)
        self.status.setStyleSheet("color: #6c7086; font-size: 9pt; padding: 4px;")
        layout.addWidget(self.status)
        self.throbber = Throbber(self.status, lambda: self._idle_status)

        # --- Bridge signals ---
        bridge.user_said.connect(self._append_user)
        bridge.assistant_said.connect(self._append_assistant)
        bridge.system_message.connect(self._append_system)
        bridge.thinking_started.connect(self.throbber.start)
        bridge.thinking_stopped.connect(self.throbber.stop)
        bridge.pdf_attached.connect(lambda f: self.set_pdf_status(f or None))

        # A PDF may already be attached if the window was rebuilt
        try:
            import pdf_handler
            if pdf_handler.is_attached():
                self.set_pdf_status(pdf_handler.get_info()["filename"])
        except ImportError:
            pass

        if not KATEX_JS.exists():
            self._append_system(
                "Math/markdown libraries not found — run "
                "'python setup_web_assets.py' once, then restart Loki."
            )

        self._load_recent_history()

    def closeEvent(self, event):
        """Hide instead of close — the tray icon stays, window can be reopened."""
        event.ignore()
        self.hide()

    # ---- Sending text to the page ----
    def _run_js(self, code):
        if self._page_ready:
            self.page.runJavaScript(code)
        else:
            self._pending_js.append(code)

    def _on_page_loaded(self, ok):
        if not ok:
            print(f"(chat page failed to load: {CHAT_PAGE})", flush=True)
            return
        self._page_ready = True
        for code in self._pending_js:
            self.page.runJavaScript(code)
        self._pending_js.clear()

    def _add_message(self, role, text):
        # json.dumps turns any Python string into a safe JavaScript string literal
        self._run_js(f"addMessage({json.dumps(role)}, {json.dumps(text)});")

    # ---- Message slots (called via bridge signals, on the GUI thread) ----
    def _append_user(self, text):
        self._add_message("user", text)
        self._save_to_disk("user", text)

    def _append_assistant(self, text):
        self._add_message("assistant", text)
        self._save_to_disk("assistant", text)

    def _append_system(self, text):
        self._add_message("system", text)

    # ---- Input ----
    def _on_submit(self):
        text = self.input_box.text().strip()
        if not text:
            return
        self.input_box.clear()
        self.submit_requested.emit(text)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        copy = menu.addAction("Copy")
        copy.setEnabled(self.page.hasSelection())
        copy.triggered.connect(lambda: self.page.triggerAction(QWebEnginePage.Copy))
        menu.exec(self.view.mapToGlobal(pos))

    # ---- Status line ----
    def set_status(self, text):
        self.status.setText(text)

    def set_pdf_status(self, filename=None):
        self._idle_status = f"📄 {filename} attached  •  Ready" if filename else "Ready"
        if not self.throbber.running:
            self.status.setText(self._idle_status)

    # ---- Drag and drop PDFs ----
    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(u.toLocalFile().lower().endswith(".pdf") for u in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                self._on_pdf_dropped(path)
                break
        event.acceptProposedAction()

    def _on_pdf_dropped(self, path):
        # Same as if the user typed "read <path>"
        self.submit_requested.emit(f"read {path}")

    # ---- Persistence ----
    def _save_to_disk(self, role, text):
        """Append one message to the JSONL history file; trim when too long."""
        record = {"ts": datetime.now().isoformat(), "role": role, "text": text}
        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            print(f"(history write failed: {e})")
            return

        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > DISK_HISTORY_LIMIT * 1.2:    # trim when 20% over
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines[-DISK_HISTORY_LIMIT:])
        except Exception:
            pass

    def _load_recent_history(self):
        """Show the last WINDOW_HISTORY_LIMIT saved messages."""
        if not HISTORY_FILE.exists():
            return
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()[-WINDOW_HISTORY_LIMIT:]
            for line in lines:
                rec = json.loads(line)
                if rec.get("role") in ("user", "assistant"):
                    self._add_message(rec["role"], rec.get("text", ""))
        except Exception as e:
            print(f"(history read failed: {e})")
