"""Chat window UI for Loki.

The conversation is shown in an embedded web view (web/chat.html) so replies
get proper markdown and KaTeX math rendering. Everything else — input box,
status line, tray behavior, reminder popup — is regular Qt.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    Qt, Signal, QObject, QTimer, QUrl, QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QColor, QDesktopServices, QKeySequence
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QFrame, QMenu, QSizePolicy, QComboBox, QToolButton,
)

import models

BASE_DIR = Path(__file__).parent
CHAT_PAGE = BASE_DIR / "web" / "chat.html"
KATEX_JS = BASE_DIR / "web" / "vendor" / "katex" / "katex.min.js"
UI_FONT_FILE = BASE_DIR / "web" / "vendor" / "fonts" / "google-sans-latin-wght-normal.woff2"
HISTORY_FILE = BASE_DIR / "chat_history.jsonl"
ICON_FILE = BASE_DIR / "AI_Icon.png"
CHAT_IMAGES_DIR = BASE_DIR / "chat_images"   # copies of pasted/dropped images
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

THINK_LABEL = "🧠 Think"    # text on the thinking-mode button — change the icon here

WINDOW_HISTORY_LIMIT = 50   # messages shown when the window opens
DISK_HISTORY_LIMIT = 1000   # most recent N messages kept on disk


def _load_ui_font():
    """Register the bundled Google Sans with Qt so the input box matches the
    chat text. Returns the family name Qt knows it by (or Segoe UI)."""
    if UI_FONT_FILE.exists():
        font_id = QFontDatabase.addApplicationFont(str(UI_FONT_FILE))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            return families[0]
    return "Segoe UI"


# ---------------------------------------------------------------------------
# Signals between the assistant (background threads) and the GUI thread
# ---------------------------------------------------------------------------
class ChatBridge(QObject):
    user_said = Signal(str, list)            # text, image file paths
    assistant_said = Signal(str, str)        # answer, reasoning ("" unless thinking mode)
    busy_text = Signal(str)                  # status text during slow steps ("Loki is looking")
    system_message = Signal(str)
    show_window_requested = Signal()
    quit_requested = Signal()
    thinking_started = Signal()
    thinking_stopped = Signal()
    pdf_attached = Signal(str)          # filename, or "" when detached
    reminder_fired = Signal(str, str)   # title, body
    thinking_mode_changed = Signal(bool)   # "think on/off" typed or spoken — sync the button


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
    def __init__(self, status_label, idle_text, busy_text):
        self.status_label = status_label
        self.idle_text = idle_text          # function returning the resting text
        self.busy_text = busy_text          # function: "Loki is thinking" / "...reasoning"
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.frame = 0
        self.base = ""

    @property
    def running(self):
        return self.timer.isActive()

    def start(self):
        self.frame = 0
        self.base = self.busy_text()
        self.status_label.setText(self.base)
        self.timer.start(400)

    def set_base(self, text):
        """Change the busy text mid-task, e.g. "Loki is looking (qwen3-vl)"."""
        self.base = text
        if self.running:
            self.status_label.setText(text + "." * self.frame)

    def stop(self):
        self.timer.stop()
        self.status_label.setText(self.idle_text())

    def _tick(self):
        self.frame = (self.frame + 1) % 4
        self.status_label.setText(self.base + "." * self.frame)


# ---------------------------------------------------------------------------
# Web page wrapper: keeps the chat page from navigating away
# ---------------------------------------------------------------------------
class ChatPage(QWebEnginePage):
    """Links open in your normal browser. Files dropped onto the chat are
    reported (pdf_dropped / image_dropped) instead of being opened in the view."""

    pdf_dropped = Signal(str)
    image_dropped = Signal(str)

    def __init__(self, home_path, parent=None):
        super().__init__(parent)
        self.home_path = Path(home_path).resolve()

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.isLocalFile():
            path = Path(url.toLocalFile())
            if path.resolve() == self.home_path:
                return True                               # our own chat page
            if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                QDesktopServices.openUrl(url)             # clicked a thumbnail: open it
            elif path.suffix.lower() == ".pdf":
                self.pdf_dropped.emit(str(path))
            elif path.suffix.lower() in IMAGE_EXTS:
                self.image_dropped.emit(str(path))
        elif url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        return False                                      # never leave the chat

    def createWindow(self, _type):
        return None   # ignore "open in new window" requests

    def javaScriptConsoleMessage(self, level, message, line, source):
        # JS errors end up in the log instead of disappearing
        print(f"[chat.js:{line}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Model dropdown: lists installed models each time it's opened
# ---------------------------------------------------------------------------
class ModelCombo(QComboBox):
    def __init__(self, role, parent=None):
        super().__init__(parent)
        self.role = role                     # "main" or "vision"
        self.setSizeAdjustPolicy(QComboBox.AdjustToContents)

    def showPopup(self):
        self.refresh()                       # pick up models pulled since startup
        super().showPopup()

    def refresh(self):
        current = models.get_setting(f"{self.role}_model")
        names = models.installed()
        if current not in names and not any(models.same_model(current, n) for n in names):
            names = [current] + names        # keep the saved choice even if Ollama is down
        self.blockSignals(True)
        self.clear()
        for name in names:
            label = name + ("  👁" if models.can_see(name) else "")
            if not models.capabilities(name):
                label += "  (not found)"
            self.addItem(label, name)
        for i in range(self.count()):
            if models.same_model(self.itemData(i), current):
                self.setCurrentIndex(i)
        self.blockSignals(False)


# ---------------------------------------------------------------------------
# Main chat window
# ---------------------------------------------------------------------------
class ChatWindow(QMainWindow):
    submit_requested = Signal(str)        # user typed something and pressed Enter
    images_submitted = Signal(str, list)  # text + image file paths
    thinking_toggled = Signal(bool)       # user clicked the Think button
    model_chosen = Signal(str, str)       # role ("main"/"vision"), model name

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        self.setWindowTitle("Loki")
        self.resize(760, 640)
        self.setWindowIcon(QIcon(str(ICON_FILE)))
        self.setAcceptDrops(True)
        self._pending_images = []          # attached, waiting to be sent

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        ui_font = _load_ui_font()   # Google Sans, same as the chat text

        # --- Top bar: model choices ---
        top = QHBoxLayout()
        top.setSpacing(6)
        combo_style = """
            QComboBox {
                background-color: #1E1F20; color: #E3E3E3;
                border: 1px solid #2a2a2e; border-radius: 4px;
                padding: 3px 8px; font-family: '%s', 'Segoe UI'; font-size: 9pt;
            }
            QComboBox:hover { border-color: #534AB7; }
            QComboBox QAbstractItemView {
                background-color: #1E1F20; color: #E3E3E3;
                selection-background-color: #534AB7;
            }
        """ % ui_font
        label_style = "color: #9aa0a6; font-size: 9pt; font-family: '%s', 'Segoe UI';" % ui_font
        top.addStretch()
        self.model_combos = {}
        for role, text, tip in (
            ("main", "Model", "Main model: chat, PDFs, commands, and reasoning about images"),
            ("vision", "Images", "Vision model (👁): looks at images you paste or drop, "
                                 "then the main model answers"),
        ):
            lbl = QLabel(text)
            lbl.setStyleSheet(label_style)
            combo = ModelCombo(role)
            combo.setStyleSheet(combo_style)
            combo.setToolTip(tip)
            combo.setCursor(Qt.PointingHandCursor)
            combo.currentIndexChanged.connect(lambda _i, c=combo: self._on_model_changed(c))
            top.addWidget(lbl)
            top.addWidget(combo)
            self.model_combos[role] = combo
        layout.addLayout(top)

        # --- Conversation view (web page) ---
        self._page_ready = False
        self._pending_js = []      # calls made before the page finished loading

        self.view = QWebEngineView()
        home_url = QUrl.fromLocalFile(str(CHAT_PAGE.resolve()))
        self.page = ChatPage(CHAT_PAGE, self.view)
        self.page.setBackgroundColor(QColor("#131314"))   # no white flash on load
        self.page.pdf_dropped.connect(self._on_pdf_dropped)
        self.page.image_dropped.connect(self.add_image)
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
        layout.addWidget(self.view, 1)      # stretch=1: the chat takes all spare height

        # --- Attached images, waiting to be sent ---
        self.attach_row = QHBoxLayout()
        self.attach_row.setSpacing(6)
        self.attach_row.addStretch()
        self._chip_style = """
            QFrame#chip { background-color: #1E1F20; border: 1px solid #534AB7; border-radius: 10px; }
            QLabel { color: #E3E3E3; font-size: 9pt; font-family: '%s', 'Segoe UI'; border: none; }
            QToolButton { color: #9aa0a6; border: none; font-size: 10pt; padding: 0 2px; }
            QToolButton:hover { color: #ffffff; }
        """ % ui_font
        layout.addLayout(self.attach_row)

        # --- Input row ---
        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Type a message, or paste/drop an image...")
        self.input_box.setStyleSheet("""
            QLineEdit {
                background-color: #1E1F20;
                color: #E3E3E3;
                border: 1px solid #2a2a2e;
                border-radius: 8px;
                padding: 10px;
                font-family: '%s', 'Segoe UI', sans-serif;
                font-size: 11pt;
            }
        """ % ui_font)
        self.input_box.returnPressed.connect(self._on_submit)
        self.input_box.installEventFilter(self)          # Ctrl+V with an image
        input_row.addWidget(self.input_box)

        # Thinking-mode toggle. Sticky: stays on until clicked again.
        self.think_button = QPushButton(THINK_LABEL)
        self.think_button.setCheckable(True)
        self.think_button.setCursor(Qt.PointingHandCursor)
        self.think_button.setStyleSheet("""
            QPushButton {
                background-color: #1E1F20;
                color: #9aa0a6;
                border: 1px solid #2a2a2e;
                border-radius: 4px;
                padding: 8px 12px;
            }
            QPushButton:hover { border-color: #534AB7; color: #E3E3E3; }
            QPushButton:checked {
                background-color: #534AB7;
                color: #ffffff;
                border-color: #534AB7;
            }
            QPushButton:checked:hover { background-color: #6359C9; }
            QPushButton:disabled { color: #4a4d52; border-color: #232426; }
        """)
        self.think_button.toggled.connect(self.thinking_toggled.emit)
        input_row.addWidget(self.think_button)

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
        self.status.setStyleSheet(
            "color: #6c7086; font-size: 9pt; padding: 4px; font-family: '%s', 'Segoe UI';" % ui_font
        )
        self.status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)   # one line, never grows
        layout.addWidget(self.status)
        self.throbber = Throbber(
            self.status,
            idle_text=lambda: self._idle_status,
            busy_text=lambda: ("Loki is reasoning" if self.think_button.isChecked()
                               else "Loki is thinking"),
        )

        # --- Bridge signals ---
        bridge.user_said.connect(self._append_user)
        bridge.assistant_said.connect(self._append_assistant)
        bridge.system_message.connect(self._append_system)
        bridge.thinking_started.connect(self.throbber.start)
        bridge.thinking_stopped.connect(self.throbber.stop)
        bridge.busy_text.connect(self.throbber.set_base)
        bridge.pdf_attached.connect(lambda f: self.set_pdf_status(f or None))
        bridge.thinking_mode_changed.connect(self._sync_think_button)

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
        # Fill the dropdowns once the window is up (asks Ollama what's installed)
        QTimer.singleShot(0, self._refresh_models)

    def closeEvent(self, event):
        """Hide instead of close — the tray icon stays, window can be reopened."""
        event.ignore()
        self.hide()

    # ---- Models ----
    def _refresh_models(self):
        for combo in self.model_combos.values():
            combo.refresh()
        self._update_think_button()

    def _on_model_changed(self, combo):
        name = combo.currentData()
        if not name or models.same_model(name, models.get_setting(f"{combo.role}_model")):
            return
        if combo.role == "vision" and not models.can_see(name):
            self._append_system(f"{name} can't see images — pick a model marked 👁.")
            combo.refresh()                               # snap back to the saved choice
            return
        self.model_chosen.emit(combo.role, name)
        if combo.role == "main":
            self._update_think_button(name)

    def _update_think_button(self, main=None):
        """Thinking mode only exists for models that support it."""
        main = main or models.main_model()
        caps = models.capabilities(main)
        able = "thinking" in caps or not caps      # unknown (Ollama down): leave it usable
        self.think_button.setEnabled(able)
        if able:
            self.think_button.setToolTip(
                "Thinking mode: reason step by step before answering.\n"
                "Better for logic, math and tricky questions, but much slower.\n"
                "Only affects chat, PDF and image questions."
            )
        else:
            self.think_button.setToolTip(f"{main} doesn't support thinking mode.")
            if self.think_button.isChecked():
                self.think_button.setChecked(False)       # also tells the assistant

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

    def _add_message(self, role, text, reasoning="", images=None):
        urls = [QUrl.fromLocalFile(p).toString() for p in (images or []) if Path(p).exists()]
        # json.dumps turns any Python value into a safe JavaScript literal
        self._run_js(f"addMessage({json.dumps(role)}, {json.dumps(text)}, "
                     f"{json.dumps(reasoning)}, {json.dumps(urls)});")

    # ---- Message slots (called via bridge signals, on the GUI thread) ----
    def _append_user(self, text, images=None):
        self._add_message("user", text, images=images)
        self._save_to_disk("user", text, images=images)

    def _append_assistant(self, text, reasoning=""):
        self._add_message("assistant", text, reasoning)
        self._save_to_disk("assistant", text, reasoning)

    def _sync_think_button(self, on):
        """Reflect a typed/spoken "think on/off" without re-announcing it."""
        self.think_button.blockSignals(True)
        self.think_button.setChecked(on)
        self.think_button.blockSignals(False)

    def _append_system(self, text):
        self._add_message("system", text)

    # ---- Input ----
    def _on_submit(self):
        text = self.input_box.text().strip()
        if not text and not self._pending_images:
            return
        self.input_box.clear()
        if self._pending_images:
            images = list(self._pending_images)
            self._clear_images()
            self.images_submitted.emit(text, images)
        else:
            self.submit_requested.emit(text)

    def eventFilter(self, obj, event):
        """Ctrl+V in the input box: attach a copied image (screenshot or file)."""
        if obj is self.input_box and event.type() == event.Type.KeyPress \
                and event.matches(QKeySequence.Paste):
            if self._paste_image():
                return True                               # handled — don't paste text
        return super().eventFilter(obj, event)

    def _paste_image(self):
        data = QApplication.clipboard().mimeData()
        files = [u.toLocalFile() for u in data.urls()] if data.hasUrls() else []
        files = [f for f in files if Path(f).suffix.lower() in IMAGE_EXTS]
        if files:                                         # copied file(s) in Explorer
            for f in files:
                self.add_image(f)
            return True
        if data.hasImage():                               # Win+Shift+S, Print Screen, etc.
            image = QApplication.clipboard().image()
            if not image.isNull():
                CHAT_IMAGES_DIR.mkdir(exist_ok=True)
                path = CHAT_IMAGES_DIR / f"pasted_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
                image.save(str(path), "PNG")
                self._add_chip(str(path), "Pasted image")
                return True
        return False

    def add_image(self, path):
        """Attach an image file (dropped or pasted). A copy is kept in
        chat_images/ so the chat history still shows it later."""
        src = Path(path)
        if not src.is_file() or src.suffix.lower() not in IMAGE_EXTS:
            return
        CHAT_IMAGES_DIR.mkdir(exist_ok=True)
        dest = CHAT_IMAGES_DIR / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{src.name}"
        try:
            shutil.copy2(src, dest)
        except OSError:
            dest = src                                    # fall back to the original
        self._add_chip(str(dest), src.name)
        self.input_box.setFocus()

    def _add_chip(self, path, label):
        self._pending_images.append(path)
        chip = QFrame()
        chip.setObjectName("chip")
        chip.setStyleSheet(self._chip_style)
        row = QHBoxLayout(chip)
        row.setContentsMargins(8, 2, 4, 2)
        name = label if len(label) <= 28 else label[:25] + "…"
        row.addWidget(QLabel(f"🖼 {name}"))
        remove = QToolButton()
        remove.setText("✕")
        remove.setCursor(Qt.PointingHandCursor)
        remove.setToolTip("Remove")
        remove.clicked.connect(lambda: self._remove_chip(chip, path))
        row.addWidget(remove)
        self.attach_row.insertWidget(self.attach_row.count() - 1, chip)

    def _remove_chip(self, chip, path):
        if path in self._pending_images:
            self._pending_images.remove(path)
            if Path(path).parent == CHAT_IMAGES_DIR:      # our copy, never sent — tidy up
                Path(path).unlink(missing_ok=True)
        chip.deleteLater()

    def _clear_images(self):
        self._pending_images.clear()
        while self.attach_row.count() > 1:                # keep the stretch
            item = self.attach_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

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

    # ---- Drag and drop: PDFs and images ----
    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(Path(u.toLocalFile()).suffix.lower() in IMAGE_EXTS | {".pdf"} for u in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            suffix = Path(path).suffix.lower()
            if suffix == ".pdf":
                self._on_pdf_dropped(path)
                break
            if suffix in IMAGE_EXTS:
                self.add_image(path)
        event.acceptProposedAction()

    def _on_pdf_dropped(self, path):
        # Same as if the user typed "read <path>"
        self.submit_requested.emit(f"read {path}")

    # ---- Persistence ----
    def _save_to_disk(self, role, text, reasoning="", images=None):
        """Append one message to the JSONL history file; trim when too long."""
        record = {"ts": datetime.now().isoformat(), "role": role, "text": text}
        if reasoning:
            record["reasoning"] = reasoning
        if images:
            record["images"] = list(images)
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
                    self._add_message(rec["role"], rec.get("text", ""), rec.get("reasoning", ""),
                                      rec.get("images"))
        except Exception as e:
            print(f"(history read failed: {e})")
