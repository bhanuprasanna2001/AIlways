"""Shared data model for retrieval results."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SearchResult(BaseModel):
    """A single search result from retrieval."""

    chunk_id: UUID
    doc_id: UUID
    content: str
    content_with_header: str
    score: float
    section_heading: str | None = None
    page_number: int | None = None
    original_filename: str | None = None
    embedding: list[float] | None = None


def build_retrieval_context(results: list[SearchResult]) -> str:
    """Format search results into a numbered context block for LLM prompts."""
    parts: list[str] = []
    for i, r in enumerate(results, 1):
        parts.append(f"--- Document Chunk {i} (relevance: {r.score:.3f}) ---")
        parts.append(r.content_with_header)
        parts.append("")
    return "\n".join(parts)
