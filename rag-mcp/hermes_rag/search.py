"""Query-time retrieval and result formatting shared by CLI and MCP server."""

from __future__ import annotations

from .config import Settings
from .embedder import Embedder
from .store import SearchHit, VectorStore


class Retriever:
    def __init__(
        self, settings: Settings, store: VectorStore, embedder: Embedder
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder

    def search(self, query: str, top_k: int | None = None) -> list[SearchHit]:
        k = top_k or self.settings.top_k
        query_vec = self.embedder.embed_query(query)
        return self.store.search(query_vec, top_k=k)

    def format_context(self, hits: list[SearchHit], max_chars: int | None = None) -> str:
        """Render hits as a citation-prefixed context block for the LLM."""
        if not hits:
            return "No relevant documents found."
        budget = max_chars or self.settings.max_context_chars
        parts: list[str] = []
        used = 0
        for rank, hit in enumerate(hits, start=1):
            header = f"[{rank}] {hit.path} (chunk {hit.chunk_index}, score {hit.score:.3f})"
            body = hit.text.strip()
            block = f"{header}\n{body}"
            if used + len(block) > budget and parts:
                break
            parts.append(block)
            used += len(block)
        return "\n\n---\n\n".join(parts)
