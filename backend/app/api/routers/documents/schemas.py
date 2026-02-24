from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.db.models import Document


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class DocumentResponse(BaseModel):
    id: UUID
    original_filename: str
    file_type: str
    file_size_bytes: int | None
    status: str
    error_message: str | None
    page_count: int | None
    created_at: datetime

    @classmethod
    def from_model(cls, doc: "Document") -> "DocumentResponse":
        """Build a response from a Document ORM instance.

        Args:
            doc: The SQLModel Document instance.

        Returns:
            DocumentResponse: Serialised document.
        """
        return cls(
            id=doc.id,
            original_filename=doc.original_filename,
            file_type=doc.file_type,
            file_size_bytes=doc.file_size_bytes,
            status=doc.status,
            error_message=doc.error_message,
            page_count=doc.page_count,
            created_at=doc.created_at,
        )


class UploadResponse(BaseModel):
    id: UUID
    original_filename: str
    status: str
    chunk_count: int


class StatusResponse(BaseModel):
    id: UUID
    status: str
    error_message: str | None
    updated_at: datetime


class ContentResponse(BaseModel):
    id: UUID
    original_filename: str
    file_type: str
    markdown: str
    char_count: int
