"""PDF text extraction and attached-document state."""
from pathlib import Path
import pdfplumber
import pdf_rag


# Module-level state for the currently attached PDF
_attached = {
    "path": None,
    "text": None,
    "pages": 0,
}

# Token estimate: ~4 chars per token is a rough but reasonable heuristic
# qwen3:8b has 32K context; keep document under ~16K tokens for quality
CHARS_PER_TOKEN = 4


def extract_text(pdf_path):
    """Extract all text from a PDF. Returns (text, page_count)."""
    pdf_path = Path(pdf_path).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"No file at {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF: {pdf_path}")

    pages = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            pages.append(page_text)
    full_text = "\n\n".join(pages)
    return full_text, len(pages)


def attach(pdf_path):
    text, page_count = extract_text(pdf_path)

    # No truncation needed anymore — RAG handles long docs
    _attached["path"] = str(Path(pdf_path).resolve())
    _attached["text"] = text
    _attached["pages"] = page_count

    # Build the retrieval index
    num_chunks = pdf_rag.index_document(text)

    estimated_tokens = len(text) // CHARS_PER_TOKEN
    return {
        "path": _attached["path"],
        "pages": page_count,
        "chars": len(text),
        "estimated_tokens": estimated_tokens,
        "truncated": False,
        "chunks": num_chunks,
    }


def detach():
    was_attached = _attached["path"] is not None
    _attached["path"] = None
    _attached["text"] = None
    _attached["pages"] = 0
    pdf_rag.clear()   # clear the index too
    return was_attached


def is_attached():
    return _attached["text"] is not None


def get_context():
    """Return the attached document text (for prepending to prompts)."""
    return _attached["text"]


def get_info():
    """Short summary of what's currently attached, for the user."""
    if not is_attached():
        return None
    return {
        "filename": Path(_attached["path"]).name,
        "pages": _attached["pages"],
    }