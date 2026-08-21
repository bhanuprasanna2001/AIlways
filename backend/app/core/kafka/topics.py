from enum import Enum
from typing import Annotated, Literal, Union
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field, TypeAdapter

from app.core.utils import utcnow_aware as utcnow


# ---------------------------------------------------------------------------
# Topic names
# ---------------------------------------------------------------------------

FILE_EVENTS = "file.events"
INGESTION_DLQ = "ingestion.dlq"
AUDIT_EVENTS = "audit.events"


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """Discriminator for events on the file.events topic."""
    FILE_UPLOADED = "file.uploaded"
    FILE_DELETED = "file.deleted"


# ---------------------------------------------------------------------------
# Event schemas
# ---------------------------------------------------------------------------

class FileUploadedEvent(BaseModel):
    """Produced when a file is uploaded and ready for ingestion."""
    event_type: Literal["file.uploaded"] = EventType.FILE_UPLOADED
    doc_id: UUID
    vault_id: UUID
    file_type: str
    storage_path: str
    original_filename: str
    uploaded_by: UUID
    timestamp: datetime


class FileDeletedEvent(BaseModel):
    """Produced when a user requests document deletion."""
    event_type: Literal["file.deleted"] = EventType.FILE_DELETED
    doc_id: UUID
    vault_id: UUID
    deleted_by: UUID
    timestamp: datetime


# Discriminated union — parse a raw dict into the correct event type.
FileEvent = Annotated[
    Union[FileUploadedEvent, FileDeletedEvent],
    Field(discriminator="event_type"),
]

_file_event_adapter: TypeAdapter[FileEvent] = TypeAdapter(FileEvent)


def parse_file_event(raw: dict) -> FileUploadedEvent | FileDeletedEvent:
    """Validate a raw dict into the correct ``FileEvent`` variant."""
    return _file_event_adapter.validate_python(raw)

class AuditEvent(BaseModel):
    """Produced for async audit logging."""
    event_type: str
    vault_id: UUID | None = None
    doc_id: UUID | None = None
    user_id: UUID | None = None
    payload: dict | None = None
    latency_ms: int | None = None
    timestamp: datetime


class DLQEnvelope(BaseModel):
    """Wrapper for events that failed processing."""
    original_topic: str
    original_event: dict
    error_message: str
    error_type: str
    retry_count: int = 0
    failed_at: datetime


