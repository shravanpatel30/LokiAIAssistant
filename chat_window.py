"""Chat window UI for the assistant."""
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import (
    QTextCursor, QKeySequence, QShortcut, QFont,
    QTextBlockFormat, QIcon
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QTextBrowser, QLineEdit, QPushButton, QLabel,
)
import markdown

HISTORY_FILE = Path(__file__).parent / "chat_history.jsonl"
WINDOW_HISTORY_LIMIT = 50  # messages kept in the visible window
DISK_HISTORY_LIMIT = 1000  # most recent N messages persisted

class Throbber:
    """Cycles status text to show the assistant is working."""
    FRAMES = ["Loki is thinking", "Loki is thinking.", "Loki is thinking..", "Loki is thinking..."]

    def __init__(self, status_label):
        self.status_label = status_label
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.frame = 0

    def start(self):
        self.frame = 0
        self.status_label.setText(self.FRAMES[0])
        self.timer.start(400)  # ms between frames

    def stop(self):
        self.timer.stop()
        self.status_label.setText("Ready")

    def _tick(self):
        self.frame = (self.frame + 1) % len(self.FRAMES)
        self.status_label.setText(self.FRAMES[self.frame])


class ChatBridge(QObject):
    user_said = Signal(str)
    assistant_said = Signal(str)
    system_message = Signal(str)
    show_window_requested = Signal()
    quit_requested = Signal()
    thinking_started = Signal()
    thinking_stopped = Signal()


class ChatWindow(QMainWindow):
    submit_requested = Signal(str)  # user typed something and pressed Enter

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        self.setWindowTitle("Loki")
        self.resize(700, 600)

        icon_path = str(Path(__file__).parent / "AI_Icon.png")
        self.setWindowIcon(QIcon(icon_path))

        # Central widget + layout
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Chat history view (read-only)
        self.history_view = QTextBrowser()
        self.history_view.setOpenLinks(False)        # we handle clicks ourselves
        self.history_view.setOpenExternalLinks(False)
        self.history_view.anchorClicked.connect(self._on_anchor_clicked)
        self.history_view.setReadOnly(True)
        self.history_view.setStyleSheet("""
            QTextEdit {
                background-color: #131314;
                color: #E3E3E3;
                border: none;
                padding: 12px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11pt;
            }
        """)
        self.history_view.setLineWrapMode(QTextEdit.WidgetWidth)
        layout.addWidget(self.history_view)

        self._code_snippets = {}  # snippet_id → raw code text
        self._next_snippet_id = 0

        # Input row
        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Type a message and press Enter (Shift+Enter for newline)...")
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

        # Status label at the bottom
        self.status = QLabel("Ready")
        self.status.setStyleSheet("color: #6c7086; font-size: 9pt; padding: 4px;")
        layout.addWidget(self.status)

        self.throbber = Throbber(self.status)

        # Wire bridge signals to window slots
        bridge.user_said.connect(self._append_user)
        bridge.assistant_said.connect(self._append_assistant)
        bridge.system_message.connect(self._append_system)

        bridge.thinking_started.connect(self.throbber.start)
        bridge.thinking_stopped.connect(self.throbber.stop)

        # Load recent history from disk
        self._load_recent_history()

    def closeEvent(self, event):
        """Hide instead of close — the tray icon remains, window can be reopened."""
        event.ignore()
        self.hide()

    # ---- Public-ish actions ----
    def _on_submit(self):
        text = self.input_box.text().strip()
        if not text:
            return
        self.input_box.clear()
        self.submit_requested.emit(text)

    def _append_user(self, text):
        self._append_message("you", text, color="#a6e3a1")
        self._save_to_disk("user", text)

    def _append_assistant(self, text):
        self._append_message("assistant", text, color="#89b4fa", as_markdown=True)
        self._save_to_disk("assistant", text)

    def _append_system(self, text):
        self._append_message("·", text, color="#6c7086")

    def _append_message(self, role, text, color="#E3E3E3", as_markdown=False):
        if as_markdown:
            body_html = self._render_markdown_with_code_blocks(text)
        else:
            body_html = (text.replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                            .replace("\n", "<br>"))

        cursor = self.history_view.textCursor()
        cursor.movePosition(QTextCursor.End)

        # Reset block format to clear any leftover list/heading state from prior content
        from PySide6.QtGui import QTextBlockFormat, QTextListFormat
        if not self.history_view.document().isEmpty():
            cursor.insertBlock()
            cursor.insertBlock()

        # Force a clean block format — no list, no indent, no inherited styling
        clean_format = QTextBlockFormat()
        cursor.setBlockFormat(clean_format)

        # Inline role + content on the same logical line
        inline_html = (
            f'<span style="color:{color};font-weight:bold;">{role}:</span> '
            f'<span style="color:#E3E3E3;">{body_html}</span>'
        )
        cursor.insertHtml(inline_html)

        self.history_view.setTextCursor(cursor)
        self.history_view.ensureCursorVisible()


    def _render_markdown_with_code_blocks(self, text):
        """Convert markdown to HTML, replacing code blocks with our styled version."""
        import re

        # First, extract fenced code blocks BEFORE markdown processing
        # so we can render them as raw HTML with our styling.
        placeholders = {}

        def stash_code(match):
            lang = match.group(1) or ""
            code = match.group(2)
            placeholder = f"@@CODE_BLOCK_{len(placeholders)}@@"
            placeholders[placeholder] = (lang, code)
            return placeholder

        # Match ```lang\ncode\n```
        text_no_code = re.sub(
            r"```([\w+\-]*)\n(.*?)\n```",
            stash_code,
            text,
            flags=re.DOTALL,
        )

        # Run markdown on the remaining text
        html = markdown.markdown(text_no_code, extensions=["tables"])

        # Now restore code blocks with our custom HTML
        for placeholder, (lang, code) in placeholders.items():
            snippet_id = self._next_snippet_id
            self._next_snippet_id += 1
            self._code_snippets[snippet_id] = code

            # Escape code for HTML display
            code_escaped = (code.replace("&", "&amp;")
                                .replace("<", "&lt;")
                                .replace(">", "&gt;"))

            lang_label = lang.upper() if lang else "CODE"

            block_html = (
                f'<table cellpadding="0" cellspacing="0" width="100%" '
                f'style="background-color:#1E1F20;margin:8px 0;">'
                f'<tr>'
                f'<td style="background-color:#2a2a2e;padding:6px 10px;">'
                f'<span style="color:#9aa0a6;font-family:Consolas,monospace;'
                f'font-size:9pt;">{lang_label}</span>'
                f'</td>'
                f'<td align="right" style="background-color:#2a2a2e;padding:6px 10px;">'
                f'<a href="copy:{snippet_id}" style="color:#89b4fa;'
                f'text-decoration:none;font-size:9pt;">📋 Copy</a>'
                f'</td>'
                f'</tr>'
                f'<tr><td colspan="2" style="padding:10px;">'
                f'<pre style="margin:0;color:#E3E3E3;font-family:Consolas,monospace;'
                f'font-size:10pt;white-space:pre-wrap;">{code_escaped}</pre>'
                f'</td></tr>'
                f'</table>'
            )
            html = html.replace(placeholder, block_html)

        # Style inline code too
        html = re.sub(
            r"<code>([^<]+)</code>",
            r'<code style="background-color:#1E1F20;padding:1px 5px;'
            r'font-family:Consolas,monospace;color:#E3E3E3;">\1</code>',
            html,
        )

        return html

    def _on_anchor_clicked(self, url):
        """Handle clicks on links in the history view."""
        url_str = url.toString()
        if url_str.startswith("copy:"):
            try:
                snippet_id = int(url_str.split(":", 1)[1])
                code = self._code_snippets.get(snippet_id)
                if code is not None:
                    QApplication.clipboard().setText(code)
                    self.set_status(f"Copied {len(code)} characters to clipboard")
            except (ValueError, IndexError):
                pass

    def set_status(self, text):
        self.status.setText(text)

    # ---- Persistence ----
    def _save_to_disk(self, role, text):
        """Append a single message to the JSONL history file. Trim if too long."""
        record = {
            "ts": datetime.now().isoformat(),
            "role": role,
            "text": text,
        }
        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            print(f"(history write failed: {e})")

        # Trim if over limit (rare path — only when file gets big)
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > DISK_HISTORY_LIMIT * 1.2:  # only trim when 20% over
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines[-DISK_HISTORY_LIMIT:])
        except Exception:
            pass

    def _load_recent_history(self):
        """Load the last WINDOW_HISTORY_LIMIT messages into the view."""
        if not HISTORY_FILE.exists():
            return
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()[-WINDOW_HISTORY_LIMIT:]
            for line in lines:
                rec = json.loads(line)
                role = rec.get("role")
                text = rec.get("text", "")
                if role == "user":
                    self._append_message("you", text, color="#a6e3a1")
                elif role == "assistant":
                    self._append_message("assistant", text, color="#89b4fa", as_markdown=True)
        except Exception as e:
            print(f"(history read failed: {e})")


