"""Runtime configuration, read from environment with sensible defaults.

Every value is overridable via an ``RAG_*`` env var so the systemd unit (and
local dev) can tune model, paths, and ports without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Default embedding model: light multilingual, good enough for Vietnamese +
# English on a 4GB box and does NOT require e5-style query/passage prefixes.
# Swap to "intfloat/multilingual-e5-large" for higher retrieval quality if RAM
# allows (set RAG_EMBED_MODEL).
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def _env_str(key: str, default: str) -> str:
    val = os.environ.get(key)
    return val if val is not None and val != "" else default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    docs_dir: Path
    model_cache_dir: Path
    embed_model: str
    host: str
    port: int
    top_k: int
    chunk_size: int
    chunk_overlap: int
    max_context_chars: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rag.db"

    @property
    def uses_e5_prefix(self) -> bool:
        """e5 family expects 'query:'/'passage:' prefixes; others do not.

        Override with RAG_EMBED_PREFIX=on|off when using a non-standard model.
        """
        override = os.environ.get("RAG_EMBED_PREFIX")
        if override is not None:
            return override.strip().lower() in {"1", "on", "true", "yes"}
        return "e5" in self.embed_model.lower()


def load_settings() -> Settings:
    data_dir = Path(_env_str("RAG_DATA_DIR", "/opt/hermes-rag/data"))
    docs_dir = Path(_env_str("RAG_DOCS_DIR", "/opt/hermes-rag/docs"))
    model_cache_dir = Path(_env_str("RAG_MODEL_CACHE", "/opt/hermes-rag/models"))
    return Settings(
        data_dir=data_dir,
        docs_dir=docs_dir,
        model_cache_dir=model_cache_dir,
        embed_model=_env_str("RAG_EMBED_MODEL", DEFAULT_MODEL),
        host=_env_str("RAG_HOST", "127.0.0.1"),
        port=_env_int("RAG_PORT", 9998),
        top_k=_env_int("RAG_TOP_K", 5),
        chunk_size=_env_int("RAG_CHUNK_SIZE", 1000),
        chunk_overlap=_env_int("RAG_CHUNK_OVERLAP", 150),
        max_context_chars=_env_int("RAG_MAX_CONTEXT_CHARS", 6000),
    )
