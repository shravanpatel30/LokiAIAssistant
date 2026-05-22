"""Retrieval-augmented PDF querying using local embeddings."""
import re
import requests
import numpy as np

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"

CHUNK_SIZE = 500       # approx words per chunk
CHUNK_OVERLAP = 100    # word overlap between chunks for context continuity
TOP_K = 7              # how many chunks to retrieve per question

# State for the currently attached document
_chunks = []           # list of chunk texts
_embeddings = None      # numpy array of chunk embeddings


def _embed(text):
    """Get an embedding vector for a piece of text."""
    r = requests.post(
        OLLAMA_EMBED_URL,
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    return np.array(r.json()["embedding"], dtype=np.float32)


def _split_into_chunks(text):
    """Split text into overlapping word-based chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk_words = words[i:i + CHUNK_SIZE]
        chunks.append(" ".join(chunk_words))
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def index_document(text):
    """Chunk and embed a document. Call once when a PDF is attached."""
    global _chunks, _embeddings
    _chunks = _split_into_chunks(text)
    vectors = [_embed(c) for c in _chunks]
    _embeddings = np.vstack(vectors)
    return len(_chunks)


def clear():
    global _chunks, _embeddings
    _chunks = []
    _embeddings = None


def retrieve(question, top_k=TOP_K):
    """Return the top_k most relevant chunks for a question."""
    if _embeddings is None or len(_chunks) == 0:
        return []
    q_vec = _embed(question)
    # Cosine similarity
    norms = np.linalg.norm(_embeddings, axis=1) * np.linalg.norm(q_vec)
    norms[norms == 0] = 1e-8
    sims = (_embeddings @ q_vec) / norms
    top_idx = np.argsort(sims)[::-1][:top_k]
    return [_chunks[i] for i in top_idx]