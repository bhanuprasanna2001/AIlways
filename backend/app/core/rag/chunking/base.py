"""Chunker protocol and shared data model."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class Chunker(Protocol):
    """Protocol for text chunking strategies.

    Any class that implements ``chunk`` with the correct signature
    satisfies this protocol — no inheritance needed.

    To add a new chunker (e.g. semantic):
        1. Create ``app/core/rag/chunking/semantic.py``.
        2. Register it in ``chunking/__init__.py`` or update ``get_chunker()``.
    """

    def chunk(self, text: str, filename: str) -> list[ChunkData]:
        """Split text into chunks with contextual headers.

        Args:
            text: Parsed markdown text from the parser.
            filename: Original filename for source labelling.

        Returns:
            list[ChunkData]: Ordered list of chunks.
        """
        ...


class ChunkData(BaseModel):
    """Single chunk with metadata ready for storage.

    Not a DB model — consumed by the ingestion orchestrator to build
    ``Chunk`` records.
    """

    content: str
    content_with_header: str
    content_hash: str
    token_count: int
    chunk_index: int
