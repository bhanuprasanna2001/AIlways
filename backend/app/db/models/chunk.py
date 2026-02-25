from uuid import UUID, uuid4
from typing import Any
from datetime import datetime
from app.db.models.utils import _utcnow_naive
from sqlalchemy import Column, UniqueConstraint, Text, ARRAY
from sqlmodel import Field, SQLModel

try:
    from pgvector.sqlalchemy import Vector
    VECTOR_TYPE = Vector(1536)
except ImportError:
    VECTOR_TYPE = None


class Chunk(SQLModel, table=True):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", "chunk_version", name="uq_chunks_doc_index_version"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    doc_id: UUID = Field(foreign_key="documents.id", nullable=False)
    vault_id: UUID = Field(foreign_key="vaults.id", nullable=False)
    parent_chunk_id: UUID | None = Field(default=None, foreign_key="chunks.id")
    chunk_type: str = Field(default="child", max_length=20, nullable=False)
    content: str = Field(nullable=False)
    content_with_header: str = Field(nullable=False)
    content_hash: str = Field(max_length=64, nullable=False)
    token_count: int = Field(nullable=False)
    section_heading: str | None = Field(default=None)
    section_level: int | None = Field(default=None)
    page_number: int | None = Field(default=None)
    slide_number: int | None = Field(default=None)
    char_start: int | None = Field(default=None)
    char_end: int | None = Field(default=None)
    chunk_index: int = Field(nullable=False)
    embedding: Any | None = Field(default=None, sa_column=Column(VECTOR_TYPE, nullable=True))
    is_deleted: bool = Field(default=False)
    chunk_version: int = Field(default=1)
    created_at: datetime = Field(default_factory=_utcnow_naive)
