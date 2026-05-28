"""MCP server exposing the local RAG knowledge base to Hermes.

Runs over StreamableHTTP at ``http://{host}:{port}/mcp``. Register it in Hermes
``config.yaml`` under ``mcp_servers`` and the agent can call ``rag_search`` /
``rag_stats`` like any built-in tool.
"""

from __future__ import annotations

import json
import logging

from .config import Settings, load_settings
from .embedder import build_embedder
from .search import Retriever
from .store import VectorStore

logger = logging.getLogger("hermes_rag.mcp")


def build_server(settings: Settings):
    from mcp.server.fastmcp import FastMCP  # lazy: optional dep

    store = VectorStore(settings.db_path)
    stored_model = store.get_meta("embed_model")
    if stored_model and stored_model != settings.embed_model:
        logger.warning(
            "Configured embed model '%s' differs from the store's '%s'. "
            "Retrieval will be inaccurate until you re-ingest after `hermes-rag reset`.",
            settings.embed_model,
            stored_model,
        )

    # Heavy: downloads/loads the ONNX model. Done once at startup.
    embedder = build_embedder(
        settings.embed_model,
        settings.uses_e5_prefix,
        cache_dir=str(settings.model_cache_dir),
    )
    retriever = Retriever(settings, store, embedder)

    mcp = FastMCP("hermes-rag", host=settings.host, port=settings.port)

    @mcp.tool()
    def rag_search(query: str, top_k: int = 5) -> str:
        """Search the user's private knowledge base (ingested documents) and
        return the most relevant passages with their source path and similarity
        score. Use this whenever the question may be answerable from the user's
        own documents, notes, or manuals rather than general knowledge.

        Args:
            query: Natural-language question or keywords.
            top_k: How many passages to return (1-20).
        """
        top_k = max(1, min(int(top_k), 20))
        hits = retriever.search(query, top_k)
        return retriever.format_context(hits)

    @mcp.tool()
    def rag_stats() -> str:
        """Report the knowledge base size: number of documents, chunks, and the
        embedding model in use. Useful to check whether anything is ingested."""
        return json.dumps(store.stats(), ensure_ascii=False, indent=2)

    return mcp


def serve(settings: Settings | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    settings = settings or load_settings()
    logger.info(
        "Starting hermes-rag MCP server on http://%s:%d/mcp (model=%s)",
        settings.host,
        settings.port,
        settings.embed_model,
    )
    server = build_server(settings)
    server.run(transport="streamable-http")


if __name__ == "__main__":
    serve()
