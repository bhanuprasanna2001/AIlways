from typing import Protocol

from pydantic import BaseModel

from app.core.rag.parsing.ir import ParsedDocument


class ChunkData(BaseModel):
    """Intermediate chunk output — not the DB model.

    Produced by a Chunker, consumed by the ingestion orchestrator.
    """
    content: str
    content_with_header: str
    content_hash: str
    token_count: int
    chunk_index: int
    chunk_type: str = "child"
    parent_index: int | None = None
    section_heading: str | None = None
    section_level: int | None = None
    page_number: int | None = None
    char_start: int = 0
    char_end: int = 0


class Chunker(Protocol):
    """Protocol for document chunking strategies."""

    def chunk(self, parsed: ParsedDocument) -> list[ChunkData]:
        """Split a parsed document into chunks.

        Args:
            parsed: The parsed document intermediate representation.

        Returns:
            list[ChunkData]: Ordered list of chunks.
        """
        ...
