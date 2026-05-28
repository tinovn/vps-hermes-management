import os

import pytest

from hermes_rag.config import load_settings
from hermes_rag.embedder import HashEmbedder
from hermes_rag.ingest import ingest_path, iter_files
from hermes_rag.search import Retriever
from hermes_rag.store import VectorStore


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RAG_DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("RAG_CHUNK_SIZE", "200")
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "20")
    return load_settings()


def _write(root, name, text):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_iter_files_filters_unsupported(tmp_path):
    _write(tmp_path, "a.md", "x")
    _write(tmp_path, "b.txt", "y")
    _write(tmp_path, "c.png", "z")
    _write(tmp_path, "sub/d.markdown", "w")
    files = iter_files(tmp_path)
    names = {p.name for p in files}
    assert names == {"a.md", "b.txt", "d.markdown"}


def test_ingest_dir_then_search(settings):
    docs = settings.docs_dir
    _write(docs, "py.md", "Python is a programming language used for data science.")
    _write(docs, "cook.md", "This banana bread recipe needs flour, sugar and butter.")

    store = VectorStore(settings.db_path)
    emb = HashEmbedder(dim=256)
    summary = ingest_path(store, emb, settings, docs)
    assert summary["ingested"] == 2
    assert summary["chunks_added"] >= 2

    retriever = Retriever(settings, store, emb)
    hits = retriever.search("programming language", top_k=2)
    assert hits
    assert "Python" in hits[0].text


def test_ingest_is_idempotent(settings):
    docs = settings.docs_dir
    _write(docs, "a.md", "stable content that does not change")
    store = VectorStore(settings.db_path)
    emb = HashEmbedder(dim=64)

    first = ingest_path(store, emb, settings, docs)
    assert first["ingested"] == 1
    second = ingest_path(store, emb, settings, docs)
    assert second["ingested"] == 0
    assert second["skipped"] == 1


def test_ingest_reflects_file_change(settings):
    docs = settings.docs_dir
    path = _write(docs, "a.md", "first version")
    store = VectorStore(settings.db_path)
    emb = HashEmbedder(dim=64)
    ingest_path(store, emb, settings, docs)

    path.write_text("a completely different second version", encoding="utf-8")
    summary = ingest_path(store, emb, settings, docs)
    assert summary["ingested"] == 1


def test_format_context_empty():
    settings = load_settings()
    retriever = Retriever(settings, store=None, embedder=None)
    assert retriever.format_context([]) == "No relevant documents found."
