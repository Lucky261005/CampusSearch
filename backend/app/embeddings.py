"""Chunking and the shared embedding model (loaded once per process, lazily)."""

import hashlib
import logging
import threading
from typing import Protocol

log = logging.getLogger("campussearch")

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
CHUNK_WORDS = 150
OVERLAP_WORDS = 30


class Embedder(Protocol):
    name: str

    def encode(self, texts: list[str]) -> list[list[float]]: ...


def chunk_text(content: str, size: int = CHUNK_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    """Split into overlapping word windows. Empty content -> no chunks."""
    if not 0 <= overlap < size:
        raise ValueError("require 0 <= overlap < size")
    words = content.split()
    chunks = []
    for start in range(0, len(words), size - overlap):
        chunks.append(" ".join(words[start : start + size]))
        if start + size >= len(words):
            break
    return chunks


def embed_input(title: str, chunk: str) -> str:
    # title gives short chunks topical context
    return f"{title}. {chunk}"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


class SentenceTransformerEmbedder:
    def __init__(self, name: str = MODEL_NAME) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = name
        self.model = SentenceTransformer(name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode(texts, normalize_embeddings=True, batch_size=32).tolist()
        if vecs and len(vecs[0]) != DIM:
            raise RuntimeError(f"expected {DIM}-dim embeddings, got {len(vecs[0])}")
        return vecs


_lock = threading.Lock()
_embedder: Embedder | None = None
_error: str | None = None


def get_embedder() -> tuple[Embedder | None, str | None]:
    """Load the model once. A failure is cached too; restart the process to retry."""
    global _embedder, _error
    with _lock:
        if _embedder is None and _error is None:
            try:
                _embedder = SentenceTransformerEmbedder()
            except Exception as e:  # ImportError, download/network errors, bad weights
                log.warning("embedding model unavailable: %s", e)
                _error = "embedding model could not be loaded"
        return _embedder, _error
