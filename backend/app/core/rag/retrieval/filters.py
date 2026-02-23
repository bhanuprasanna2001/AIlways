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
    parent_content: str | None = None
