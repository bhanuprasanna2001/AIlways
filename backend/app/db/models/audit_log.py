from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


def _utcnow_naive() -> datetime:
    """Return current UTC time without tzinfo.

    Returns:
        datetime: Current UTC time without tzinfo.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    event_type: str = Field(max_length=50, nullable=False)
    vault_id: UUID | None = Field(default=None)
    doc_id: UUID | None = Field(default=None)
    user_id: UUID | None = Field(default=None)
    payload: str | None = Field(default=None)
    latency_ms: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow_naive)
