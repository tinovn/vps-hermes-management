"""Embedding backends.

``FastEmbedEmbedder`` runs a local ONNX model on CPU via fastembed (no API key,
offline). ``HashEmbedder`` is a deterministic stand-in used by the test suite so
the pipeline can be exercised without downloading a model.

All embedders return L2-normalized float32 vectors, so the store can treat the
dot product as cosine similarity.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


def _normalize(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat.reshape(1, -1)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedEmbedder:
    """Local fastembed model. Applies e5 prefixes when ``use_e5_prefix``."""

    def __init__(
        self,
        model_name: str,
        use_e5_prefix: bool = False,
        cache_dir: str | None = None,
    ) -> None:
        from fastembed import TextEmbedding  # lazy: heavy import + optional dep

        self.model_name = model_name
        self.use_e5_prefix = use_e5_prefix
        # Pin cache so downloads survive reboots instead of landing in /tmp.
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        # Probe dimensionality once with a throwaway embedding.
        probe = next(iter(self._model.embed(["dimension probe"])))
        self.dim = int(np.asarray(probe).shape[-1])

    def _embed(self, texts: list[str]) -> np.ndarray:
        vecs = list(self._model.embed(texts))
        return _normalize(np.asarray(vecs, dtype=np.float32))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if self.use_e5_prefix:
            texts = [f"passage: {t}" for t in texts]
        return self._embed(texts)

    def embed_query(self, text: str) -> np.ndarray:
        payload = f"query: {text}" if self.use_e5_prefix else text
        return self._embed([payload])[0]


class HashEmbedder:
    """Deterministic, dependency-free embeddings for tests.

    Hashes whitespace tokens into a fixed-width bag-of-words vector. Identical
    text yields identical vectors and lexical overlap raises cosine similarity,
    which is enough to validate chunking/storage/retrieval plumbing.
    """

    def __init__(self, dim: int = 64) -> None:
        self.model_name = f"hash-{dim}"
        self.dim = dim

    def _embed_one(self, text: str) -> np.ndarray:
        import hashlib

        vec = np.zeros(self.dim, dtype=np.float32)
        for token in text.lower().split():
            # Stable across processes (unlike builtin hash() under PYTHONHASHSEED).
            digest = hashlib.md5(token.encode("utf-8")).digest()
            vec[int.from_bytes(digest[:4], "little") % self.dim] += 1.0
        return vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _normalize(np.vstack([self._embed_one(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(self._embed_one(text))[0]


def build_embedder(
    model_name: str, use_e5_prefix: bool = False, cache_dir: str | None = None
) -> Embedder:
    return FastEmbedEmbedder(
        model_name, use_e5_prefix=use_e5_prefix, cache_dir=cache_dir
    )
