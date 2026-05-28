from hermes_rag.chunker import chunk_text


def test_empty_input_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_is_single_chunk():
    chunks = chunk_text("hello world", chunk_size=1000, overlap=100)
    assert chunks == ["hello world"]


def test_long_text_is_split_under_chunk_size():
    text = "\n\n".join(f"Paragraph number {i} has some content." for i in range(50))
    chunks = chunk_text(text, chunk_size=120, overlap=20)
    assert len(chunks) > 1
    # Allow modest slack for the re-attached separator + overlap tail.
    assert all(len(c) <= 120 + 40 for c in chunks)


def test_overlap_carries_context_between_chunks():
    text = " ".join(f"word{i}" for i in range(200))
    chunks = chunk_text(text, chunk_size=100, overlap=30)
    assert len(chunks) >= 2
    # Some tail of an earlier chunk should reappear at the start of the next.
    tail = chunks[0][-10:]
    assert tail in chunks[1]


def test_prefers_heading_boundaries():
    text = "## Section A\n" + "a " * 60 + "\n## Section B\n" + "b " * 60
    chunks = chunk_text(text, chunk_size=150, overlap=10)
    # Heading markers survive chunking (structure-aware split).
    assert any("## Section A" in c for c in chunks)
    assert any("## Section B" in c for c in chunks)
