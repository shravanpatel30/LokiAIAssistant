"""One-time setup: download KaTeX and marked into web/vendor/ so the chat
window renders math and markdown fully offline.

Run once:   python setup_web_assets.py
Then commit the web/vendor/ folder so a fresh clone works without internet.
"""
import io
import shutil
import tarfile
import urllib.request
from pathlib import Path

KATEX_VERSION = "0.16.11"
MARKED_VERSION = "12.0.2"   # pinned: marked v13+ changed its renderer API

VENDOR = Path(__file__).parent / "web" / "vendor"


def _download_tarball(url):
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as r:
        return tarfile.open(fileobj=io.BytesIO(r.read()), mode="r:gz")


def _extract(tar, member_name, dest):
    data = tar.extractfile(member_name).read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def main():
    if VENDOR.exists():
        shutil.rmtree(VENDOR)

    # KaTeX: the JS, the CSS, and the .woff2 fonts the CSS points to
    tar = _download_tarball(
        f"https://registry.npmjs.org/katex/-/katex-{KATEX_VERSION}.tgz"
    )
    katex_dir = VENDOR / "katex"
    for m in tar.getmembers():
        name = m.name
        if name in ("package/dist/katex.min.js", "package/dist/katex.min.css"):
            _extract(tar, name, katex_dir / Path(name).name)
        elif name.startswith("package/dist/fonts/") and name.endswith(".woff2"):
            _extract(tar, name, katex_dir / "fonts" / Path(name).name)

    # marked: markdown -> HTML
    tar = _download_tarball(
        f"https://registry.npmjs.org/marked/-/marked-{MARKED_VERSION}.tgz"
    )
    _extract(tar, "package/marked.min.js", VENDOR / "marked.min.js")

    n_fonts = len(list((katex_dir / "fonts").glob("*.woff2")))
    print(f"Done. KaTeX {KATEX_VERSION} ({n_fonts} fonts) and marked "
          f"{MARKED_VERSION} saved to {VENDOR}")


if __name__ == "__main__":
    main()
