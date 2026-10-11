"""Embedding backends: Gemini (default) and a deterministic hash embedder for tests/offline use."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Protocol

import numpy as np

from config import Settings

log = logging.getLogger("rfp.embed")


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _normalize(a: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(a, axis=1, keepdims=True)
    return (a / np.clip(n, 1e-12, None)).astype("float32")


class GeminiEmbedder:
    BATCH = 50

    def __init__(self, settings: Settings):
        if not settings.google_api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set (needed for Gemini embeddings).")
        from google import genai
        from google.genai import types

        self._types = types
        self.client = genai.Client(api_key=settings.google_api_key)
        self.model = settings.embed_model
        self.dim = settings.embed_dim
        self.max_retries = settings.llm_max_retries
        self._cache: dict[str, np.ndarray] = {}

    def _embed(self, texts: list[str], task: str) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = texts[i : i + self.BATCH]
            last = None
            for attempt in range(self.max_retries + 1):
                try:
                    resp = self.client.models.embed_content(
                        model=self.model, contents=batch,
                        config=self._types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim))
                    out.extend(list(e.values) for e in resp.embeddings)
                    break
                except Exception as e:  # noqa: BLE001
                    last = e
                    msg = str(e)
                    delay = 15 * (attempt + 1) if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) else 2 ** (attempt + 1)
                    log.warning("embed_content failed (%d): %s", attempt + 1, msg[:160])
                    if attempt < self.max_retries:
                        time.sleep(delay)
            else:
                raise RuntimeError(f"Embedding failed after retries: {last!r}")
        return _normalize(np.array(out, dtype="float32"))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> np.ndarray:
        if text not in self._cache:
            self._cache[text] = self._embed([text], "RETRIEVAL_QUERY")[0]
        return self._cache[text]


class HashEmbedder:
    """Bag-of-hashed-tokens embedding. Deterministic, offline, no API – for unit tests only."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype="float32")
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 64) % 2 == 0 else -1.0
        return v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        return _normalize(np.stack([self._vec(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(self._vec(text)[None, :])[0]


def get_embedder(settings: Settings) -> Embedder:
    if settings.embedder_backend == "hash":
        return HashEmbedder()
    return GeminiEmbedder(settings)
