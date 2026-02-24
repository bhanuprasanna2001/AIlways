from uuid import UUID, uuid4
from datetime import datetime, timezone
from app.db.models.utils import _utcnow_naive
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Document(SQLModel, table=True):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("vault_id", "file_hash_sha256", name="uq_documents_vault_hash"),
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
