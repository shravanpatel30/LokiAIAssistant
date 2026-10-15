"""Step 1 proof of concept: render a few sample messages in the new web-based
chat view. Doesn't touch the real Loki window.

Run:  python poc_katex.py
"""
import json
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

PAGE = Path(__file__).parent / "web" / "chat.html"

SAMPLES = [
    ("user", "Can you explain when I should use an inverse distribution to draw samples from?"),
    ("assistant",
     "The inverse distribution is the inverse of the CDF. If $ F(x) $ is the CDF of a "
     "random variable $ X $, then the inverse function $ F^{-1}(u) $ is defined as:\n\n"
     "$$ F^{-1}(u) = \\inf \\{ x \\in \\mathbb{R} : F(x) \\geq u \\} $$\n\n"
     "for $ u \\in [0, 1] $. Mapping $ U \\sim \\text{Uniform}(0, 1) $ through it gives\n\n"
     "\\[ X = F^{-1}(U) \\]\n\n"
     "This is **inverse transform sampling**. Use it when:\n\n"
     "1. The CDF has a closed-form inverse (exponential: $F^{-1}(u) = -\\ln(1-u)/\\lambda$)\n"
     "2. You need exact samples, not approximate ones\n\n"
     "```python\nimport numpy as np\nu = np.random.uniform(size=1000)\nx = -np.log(1 - u) / 2.0\n```"),
    ("user", "integrate \\frac{1}{1+x^2}"),
    ("assistant", "Indefinite integral:\n\n```latex\n\\[ \\operatorname{atan}{\\left(x \\right)} + C \\]\n```"),
    ("system", "✓ Reminder #30 set for Tuesday Oct 13 at 10:15 PM: check batteries"),
    ("assistant", "Edge cases: it costs $5 and $10 (not math), but $x_1 + y_2$ is. "
                  "Inline code like `$HOME` stays as-is."),
]


def main():
    app = QApplication(sys.argv)

    if not PAGE.exists():
        sys.exit(f"Missing {PAGE}")
    if not (PAGE.parent / "vendor" / "katex" / "katex.min.js").exists():
        sys.exit("KaTeX not found. Run:  python setup_web_assets.py")

    view = QWebEngineView()
    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

    def on_loaded(ok):
        if not ok:
            print("Page failed to load")
            return
        for role, text in SAMPLES:
            # json.dumps gives a safe JavaScript string literal
            view.page().runJavaScript(f"addMessage({json.dumps(role)}, {json.dumps(text)});")

    view.loadFinished.connect(on_loaded)
    view.load(QUrl.fromLocalFile(str(PAGE.resolve())))
    view.setWindowTitle("Loki — KaTeX test")
    view.resize(820, 720)
    view.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()