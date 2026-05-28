"""Document loading + ingestion pipeline.

Walks a file or directory, reads supported documents, chunks them, embeds the
chunks, and upserts into the store. Ingestion is idempotent: a file whose
content hash is unchanged since last run is skipped.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .chunker import chunk_text
from .config import Settings
from .embedder import Embedder
from .store import VectorStore

TEXT_EXTS = {".md", ".markdown", ".txt", ".text", ".rst"}
PDF_EXTS = {".pdf"}
SUPPORTED_EXTS = TEXT_EXTS | PDF_EXTS


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader  # lazy: optional-ish heavy dep

    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PDF_EXTS:
        return _read_pdf(path)
    return path.read_text(encoding="utf-8", errors="replace")


def iter_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED_EXTS else []
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    )


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def ingest_path(
    store: VectorStore,
    embedder: Embedder,
    settings: Settings,
    target: Path,
) -> dict:
    """Ingest a file or directory. Returns a summary of what changed."""
    store.ensure_model(embedder.model_name, embedder.dim)

    files = iter_files(target)
    ingested, skipped, total_chunks = 0, 0, 0
    for path in files:
        key = str(path.resolve())
        doc_hash = _file_hash(path)
        if store.get_doc_hash(key) == doc_hash:
            skipped += 1
            continue
        text = load_text(path)
        chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            # Empty/unreadable: drop any stale rows so the store stays accurate.
            store.delete_document(key)
            skipped += 1
            continue
        embeddings = embedder.embed_documents(chunks)
        store.add_document(key, doc_hash, chunks, embeddings)
        ingested += 1
        total_chunks += len(chunks)

    return {
        "target": str(target),
        "files_seen": len(files),
        "ingested": ingested,
        "skipped": skipped,
        "chunks_added": total_chunks,
    }
