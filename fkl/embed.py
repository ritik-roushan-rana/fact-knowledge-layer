"""Embeddings over (subject, predicate).

Deliberately a local model: it costs nothing to run, needs no credentials, and
lets the whole matching stage be reproduced by a reviewer who has no API key.
The class is thin on purpose so a hosted embedding API can be dropped in behind
the same two methods.
"""
from __future__ import annotations

import threading

import numpy as np

from .config import CONFIG


class Embedder:
    _lock = threading.Lock()
    _model = None

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or CONFIG.embed_model

    def _load(self):
        if Embedder._model is None:
            with Embedder._lock:
                if Embedder._model is None:
                    from sentence_transformers import SentenceTransformer

                    Embedder._model = SentenceTransformer(self.model_name)
        return Embedder._model

    @staticmethod
    def claim_key(subject: str, predicate: str) -> str:
        """The text we embed. Subject and predicate only -- context is what the
        comparison stage reasons over, so it must not blur the match signal."""
        return f"{subject.strip()} | {predicate.strip()}"

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        model = self._load()
        vecs = model.encode(
            texts, batch_size=64, convert_to_numpy=True, normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vecs.astype(np.float32)


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
