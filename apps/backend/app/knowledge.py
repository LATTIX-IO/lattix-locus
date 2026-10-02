"""Knowledge-base chunking helpers (reference-plan Phase C).

Retrieval reuses the existing pgvector long-term memory store (embed-on-write +
cosine search), so this module only owns the document→chunk transform. The
staged pipeline mirrors Dify's design (extract → split → index → retrieve) but
is implemented independently.
"""

from __future__ import annotations

import re

DEFAULT_CHUNK_CHARS = 1200
DEFAULT_CHUNK_OVERLAP = 150
MAX_CHUNKS_PER_DOCUMENT = 400


def chunk_text(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph/sentence breaks.

    Greedy paragraph packing keeps related content together; oversize paragraphs
    fall back to a sliding character window with overlap so no content is lost.
    """
    normalized = re.sub(r"\r\n?", "\n", str(text or "")).strip()
    if not normalized:
        return []
    chunk_chars = max(200, int(chunk_chars))
    overlap = max(0, min(int(overlap), chunk_chars // 2))

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", normalized) if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        if len(paragraph) > chunk_chars:
            flush()
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + chunk_chars].strip())
                start += chunk_chars - overlap
                if len(chunks) >= MAX_CHUNKS_PER_DOCUMENT:
                    break
            continue
        if len(current) + len(paragraph) + 2 > chunk_chars:
            flush()
        current = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(chunks) >= MAX_CHUNKS_PER_DOCUMENT:
            break
    flush()
    return chunks[:MAX_CHUNKS_PER_DOCUMENT]


def collection_bucket(collection_id: str) -> str:
    return f"knowledge:{str(collection_id).strip()}"
