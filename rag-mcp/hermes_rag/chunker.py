"""Recursive, document-structure-aware text chunking with overlap.

Splits on the largest natural boundary that keeps pieces under ``chunk_size``
(markdown headings → paragraphs → lines → sentences → words), then stitches a
fixed-character overlap between adjacent chunks so retrieval doesn't lose
context that straddles a cut.
"""

from __future__ import annotations

# Ordered from coarsest to finest boundary. Heading markers come first so we
# prefer to cut at section boundaries before falling back to paragraphs/lines.
_SEPARATORS = ["\n## ", "\n### ", "\n#### ", "\n\n", "\n", ". ", " ", ""]


def _split_recursive(text: str, chunk_size: int, separators: list[str]) -> list[str]:
    if len(text) <= chunk_size:
        return [text] if text else []

    sep = separators[-1]
    rest = separators
    for i, candidate in enumerate(separators):
        if candidate == "":
            sep = ""
            rest = separators[i:]
            break
        if candidate in text:
            sep = candidate
            rest = separators[i + 1 :]
            break

    if sep == "":
        # No separator left: hard-cut by character window.
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

    pieces = text.split(sep)
    out: list[str] = []
    for idx, piece in enumerate(pieces):
        # Re-attach the separator we split on (except for the first piece) so
        # we don't silently drop heading markers / punctuation.
        seg = piece if idx == 0 else sep + piece
        if len(seg) <= chunk_size:
            out.append(seg)
        else:
            out.extend(_split_recursive(seg, chunk_size, rest))
    return out


def _merge_with_overlap(
    segments: list[str], chunk_size: int, overlap: int
) -> list[str]:
    chunks: list[str] = []
    current = ""
    for seg in segments:
        if not current:
            current = seg
        elif len(current) + len(seg) <= chunk_size:
            current += seg
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap > 0 else ""
            current = tail + seg
    if current.strip():
        chunks.append(current)
    return chunks


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    """Return a list of overlapping chunks. Empty/whitespace input → []."""
    text = text.strip()
    if not text:
        return []
    if overlap >= chunk_size:
        overlap = chunk_size // 5
    segments = _split_recursive(text, chunk_size, _SEPARATORS)
    chunks = _merge_with_overlap(segments, chunk_size, overlap)
    return [c.strip() for c in chunks if c.strip()]
