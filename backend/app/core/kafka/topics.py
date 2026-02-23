from enum import Enum
from uuid import UUID
from datetime import datetime, timezone

from pydantic import BaseModel


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
    event_type: str = EventType.FILE_UPLOADED
    doc_id: UUID
    vault_id: UUID
    file_type: str
    storage_path: str
    original_filename: str
    uploaded_by: UUID
    timestamp: datetime


class FileDeletedEvent(BaseModel):
    """Produced when a user requests document deletion."""
    event_type: str = EventType.FILE_DELETED
    doc_id: UUID
    vault_id: UUID
    deleted_by: UUID
    timestamp: datetime


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    """Return current UTC datetime.

    Returns:
        datetime: Timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)
