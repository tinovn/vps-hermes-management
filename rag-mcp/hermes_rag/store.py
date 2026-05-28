"""SQLite-backed vector store with brute-force cosine search.

Embeddings are kept as L2-normalized float32 BLOBs alongside their source text
and metadata. Search loads the matrix into memory and does a single dot-product
pass — no native extension (sqlite-vec/FAISS) required, which keeps deployment
on a bare VPS dependency-free. Fine for corpora up to ~tens of thousands of
chunks; swap in a real ANN index beyond that.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS docs (
    path        TEXT PRIMARY KEY,
    hash        TEXT NOT NULL,
    n_chunks    INTEGER NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text        TEXT NOT NULL,
    embedding   BLOB NOT NULL,
    FOREIGN KEY (path) REFERENCES docs(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
"""


@dataclass
class SearchHit:
    score: float
    text: str
    path: str
    chunk_index: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VectorStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # In-memory cache of (matrix, ids, mtime) for search, lazily built.
        self._cache: tuple[np.ndarray, list[int]] | None = None
        self._cache_token: int | None = None

    def close(self) -> None:
        self._conn.close()

    # ---- meta ----------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def ensure_model(self, model_name: str, dim: int) -> None:
        """Record the embedding model/dim, or raise on mismatch.

        Mixing vectors from different models in one store yields garbage
        similarities, so we refuse rather than silently corrupt retrieval.
        """
        existing_model = self.get_meta("embed_model")
        if existing_model is None:
            self.set_meta("embed_model", model_name)
            self.set_meta("embed_dim", str(dim))
            return
        existing_dim = self.get_meta("embed_dim")
        if existing_model != model_name or existing_dim != str(dim):
            raise ValueError(
                f"Store was built with model '{existing_model}' (dim {existing_dim}) "
                f"but current model is '{model_name}' (dim {dim}). "
                f"Run `hermes-rag reset` to rebuild, or set RAG_EMBED_MODEL back."
            )

    # ---- writes --------------------------------------------------------
    def get_doc_hash(self, path: str) -> str | None:
        row = self._conn.execute(
            "SELECT hash FROM docs WHERE path = ?", (path,)
        ).fetchone()
        return row[0] if row else None

    def delete_document(self, path: str) -> None:
        self._conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
        self._conn.execute("DELETE FROM docs WHERE path = ?", (path,))
        self._conn.commit()
        self._cache = None

    def add_document(
        self, path: str, doc_hash: str, texts: list[str], embeddings: np.ndarray
    ) -> int:
        """Replace any existing rows for ``path`` with the given chunks."""
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings length mismatch")
        self.delete_document(path)
        emb = np.asarray(embeddings, dtype=np.float32)
        # Parent docs row first to satisfy the chunks.path foreign key.
        self._conn.execute(
            "INSERT INTO docs(path, hash, n_chunks, ingested_at) VALUES(?, ?, ?, ?)",
            (path, doc_hash, len(texts), _now()),
        )
        rows = [
            (path, i, text, emb[i].tobytes())
            for i, text in enumerate(texts)
        ]
        self._conn.executemany(
            "INSERT INTO chunks(path, chunk_index, text, embedding) "
            "VALUES(?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        self._cache = None
        return len(texts)

    def reset(self) -> None:
        self._conn.execute("DELETE FROM chunks")
        self._conn.execute("DELETE FROM docs")
        self._conn.execute("DELETE FROM meta")
        self._conn.commit()
        self._cache = None

    # ---- reads ---------------------------------------------------------
    def stats(self) -> dict:
        n_docs = self._conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        n_chunks = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {
            "documents": n_docs,
            "chunks": n_chunks,
            "embed_model": self.get_meta("embed_model"),
            "embed_dim": self.get_meta("embed_dim"),
            "db_path": str(self.db_path),
        }

    def _load_matrix(self) -> tuple[np.ndarray, list[int]]:
        # Cache keyed on chunk count so a fresh ingest invalidates it cheaply.
        token = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if self._cache is not None and self._cache_token == token:
            return self._cache
        ids: list[int] = []
        vecs: list[np.ndarray] = []
        for cid, blob in self._conn.execute("SELECT id, embedding FROM chunks"):
            ids.append(cid)
            vecs.append(np.frombuffer(blob, dtype=np.float32))
        matrix = np.vstack(vecs) if vecs else np.empty((0, 0), dtype=np.float32)
        self._cache = (matrix, ids)
        self._cache_token = token
        return self._cache

    def search(self, query_vec: np.ndarray, top_k: int = 5) -> list[SearchHit]:
        matrix, ids = self._load_matrix()
        if matrix.shape[0] == 0:
            return []
        q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
        scores = matrix @ q  # both sides L2-normalized → cosine similarity
        k = min(top_k, scores.shape[0])
        top_idx = np.argpartition(-scores, k - 1)[:k]
        top_idx = top_idx[np.argsort(-scores[top_idx])]
        hits: list[SearchHit] = []
        for i in top_idx:
            cid = ids[int(i)]
            row = self._conn.execute(
                "SELECT text, path, chunk_index FROM chunks WHERE id = ?", (cid,)
            ).fetchone()
            if row:
                hits.append(
                    SearchHit(
                        score=float(scores[int(i)]),
                        text=row[0],
                        path=row[1],
                        chunk_index=row[2],
                    )
                )
        return hits
