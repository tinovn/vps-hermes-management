import numpy as np
import pytest

from hermes_rag.embedder import HashEmbedder
from hermes_rag.store import VectorStore


@pytest.fixture()
def store(tmp_path):
    s = VectorStore(tmp_path / "rag.db")
    yield s
    s.close()


def _add(store, emb, path, texts):
    vecs = emb.embed_documents(texts)
    return store.add_document(path, f"hash-{path}", texts, vecs)


def test_add_and_stats(store):
    emb = HashEmbedder(dim=32)
    store.ensure_model(emb.model_name, emb.dim)
    n = _add(store, emb, "/docs/a.md", ["the cat sat", "on the mat"])
    assert n == 2
    stats = store.stats()
    assert stats["documents"] == 1
    assert stats["chunks"] == 2
    assert stats["embed_model"] == emb.model_name


def test_reingest_replaces_not_duplicates(store):
    emb = HashEmbedder(dim=32)
    _add(store, emb, "/docs/a.md", ["one", "two", "three"])
    _add(store, emb, "/docs/a.md", ["only one now"])
    assert store.stats()["chunks"] == 1
    assert store.stats()["documents"] == 1


def test_search_ranks_relevant_chunk_first(store):
    emb = HashEmbedder(dim=128)
    _add(
        store,
        emb,
        "/docs/a.md",
        [
            "python programming language tutorial",
            "banana smoothie recipe with milk",
            "how to train a dog to sit",
        ],
    )
    hits = store.search(emb.embed_query("python tutorial"), top_k=3)
    assert hits
    assert "python" in hits[0].text
    # Scores are sorted descending.
    assert all(hits[i].score >= hits[i + 1].score for i in range(len(hits) - 1))


def test_search_empty_store_returns_empty(store):
    emb = HashEmbedder(dim=16)
    assert store.search(emb.embed_query("anything"), top_k=5) == []


def test_model_mismatch_raises(store):
    store.ensure_model("model-a", 32)
    with pytest.raises(ValueError):
        store.ensure_model("model-b", 64)


def test_delete_document(store):
    emb = HashEmbedder(dim=16)
    _add(store, emb, "/docs/a.md", ["alpha", "beta"])
    store.delete_document("/docs/a.md")
    assert store.stats()["chunks"] == 0
    assert store.get_doc_hash("/docs/a.md") is None
