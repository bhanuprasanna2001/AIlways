from uuid import UUID, uuid4
from datetime import date, datetime
from typing import Any
from app.db.models.utils import _utcnow_naive
from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Document(SQLModel, table=True):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("vault_id", "file_hash_sha256", name="uq_documents_vault_hash"),
        Index("ix_documents_vault_doctype", "vault_id", "document_type"),
        Index("ix_documents_vault_orderdate", "vault_id", "order_date"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    vault_id: UUID = Field(foreign_key="vaults.id", nullable=False)
    uploaded_by: UUID = Field(foreign_key="users.id", nullable=False)
    original_filename: str = Field(nullable=False)
    file_type: str = Field(max_length=20, nullable=False)
    file_size_bytes: int | None = Field(default=None)
    file_hash_sha256: str = Field(max_length=64, nullable=False)
    storage_path: str = Field(nullable=False)
    parsed_ir_path: str | None = Field(default=None)
    status: str = Field(default="pending", max_length=20, nullable=False)
    error_message: str | None = Field(default=None)
    version: int = Field(default=1)
    page_count: int | None = Field(default=None)
    language: str = Field(default="en", max_length=10)
    created_at: datetime = Field(default_factory=_utcnow_naive)
    updated_at: datetime = Field(default_factory=_utcnow_naive)
    deleted_at: datetime | None = Field(default=None)

    # --- Structured metadata (extracted at ingestion time) ---
    # Enables SQL-backed filtering for aggregate queries like
    # "all invoices from July 2016" without relying on semantic search.
    document_type: str | None = Field(default=None, max_length=50)
    entity_id: str | None = Field(default=None, max_length=50)
    order_date: date | None = Field(default=None)
    customer_id: str | None = Field(default=None, max_length=50)
    total_price: float | None = Field(default=None)

    # --- LLM-enriched metadata (extracted at ingestion time) ---
    # summary: concise description for context and HyDE retrieval
    # keywords: domain terms for BM25 and faceted search (JSONB array)
    # hypothetical_questions: HyDE questions for dense retrieval (JSONB array)
    # extracted_entities: structured entity map from LLM (JSONB object)
    summary: str | None = Field(default=None)
    keywords: Any | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    hypothetical_questions: Any | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    extracted_entities: Any | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
