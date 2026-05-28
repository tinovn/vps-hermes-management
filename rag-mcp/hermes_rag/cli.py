"""Command-line entry point: ingest, search, stats, reset, serve."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_settings
from .ingest import ingest_path
from .search import Retriever
from .store import VectorStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-rag",
        description="Local RAG knowledge base for Hermes (ingest + serve over MCP).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest a file or directory")
    p_ingest.add_argument(
        "path",
        nargs="?",
        default=None,
        help="File or directory to ingest (default: RAG_DOCS_DIR)",
    )

    p_search = sub.add_parser("search", help="Run a one-off retrieval query")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=None)

    sub.add_parser("stats", help="Show knowledge base stats")
    sub.add_parser("reset", help="Delete all ingested data")
    sub.add_parser("serve", help="Run the MCP server (StreamableHTTP)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings()

    if args.command == "serve":
        from .mcp_server import serve

        serve(settings)
        return 0

    if args.command == "stats":
        store = VectorStore(settings.db_path)
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "reset":
        store = VectorStore(settings.db_path)
        store.reset()
        print("Knowledge base reset.")
        return 0

    # ingest + search need the embedding model.
    from .embedder import build_embedder

    store = VectorStore(settings.db_path)
    embedder = build_embedder(
        settings.embed_model,
        settings.uses_e5_prefix,
        cache_dir=str(settings.model_cache_dir),
    )

    if args.command == "ingest":
        target = Path(args.path) if args.path else settings.docs_dir
        if not target.exists():
            print(f"Path not found: {target}", file=sys.stderr)
            return 1
        summary = ingest_path(store, embedder, settings, target)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "search":
        retriever = Retriever(settings, store, embedder)
        hits = retriever.search(args.query, args.top_k)
        print(retriever.format_context(hits))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
