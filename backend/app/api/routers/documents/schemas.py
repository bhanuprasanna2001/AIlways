from uuid import UUID
from datetime import datetime
from pydantic import BaseModel


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
