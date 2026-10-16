"""Retrieval-augmented PDF querying using local embeddings (Ollama)."""
import numpy as np
import requests

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"

CHUNK_SIZE = 500       # approx words per chunk
CHUNK_OVERLAP = 100    # word overlap between chunks for context continuity
TOP_K = 7              # how many chunks to retrieve per question
EMBED_BATCH = 32       # chunks per embedding request

# State for the currently attached document
_chunks = []           # list of chunk texts
_embeddings = None     # numpy array, one row per chunk


def _embed_many(texts):
    """Embedding vectors for a list of texts, as a 2-D numpy array."""
    vectors = []
    for i in range(0, len(texts), EMBED_BATCH):
        r = requests.post(
            OLLAMA_EMBED_URL,
            json={"model": EMBED_MODEL, "input": texts[i:i + EMBED_BATCH]},
            timeout=120,
        )
        data = r.json()
        if "embeddings" not in data:
            # Most common cause: the model was never pulled
            raise RuntimeError(
                f"Embedding failed ({data.get('error', r.status_code)}). "
                f"Is the model installed? Run: ollama pull {EMBED_MODEL}"
            )
        vectors.extend(data["embeddings"])
    return np.array(vectors, dtype=np.float32)


def _split_into_chunks(text):
    """Split text into overlapping word-based chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + CHUNK_SIZE]))
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def index_document(text):
    """Chunk and embed a document. Call once when a PDF is attached.
    The stored index only changes if everything succeeds."""
    global _chunks, _embeddings
    chunks = _split_into_chunks(text)
    embeddings = _embed_many(chunks)
    _chunks, _embeddings = chunks, embeddings
    return len(chunks)


def clear():
    global _chunks, _embeddings
    _chunks = []
    _embeddings = None


def retrieve(question, top_k=TOP_K):
    """Return the top_k most relevant chunks for a question."""
    if _embeddings is None or len(_chunks) == 0:
        return []
    q_vec = _embed_many([question])[0]
    # Cosine similarity
    norms = np.linalg.norm(_embeddings, axis=1) * np.linalg.norm(q_vec)
    norms[norms == 0] = 1e-8
    sims = (_embeddings @ q_vec) / norms
    top_idx = np.argsort(sims)[::-1][:top_k]
    return [_chunks[i] for i in top_idx]
