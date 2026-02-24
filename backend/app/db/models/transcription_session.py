from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


def _utcnow_naive() -> datetime:
    """Return current UTC time without tzinfo.

    Returns:
        datetime: Current UTC time without tzinfo.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TranscriptionSession(SQLModel, table=True):
    __tablename__ = "transcription_sessions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    vault_id: UUID = Field(foreign_key="vaults.id", nullable=False)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    title: str = Field(max_length=255, nullable=False)
    status: str = Field(default="recording", max_length=20, nullable=False)
    duration_seconds: float | None = Field(default=None)
    speaker_count: int = Field(default=0)
    segment_count: int = Field(default=0)
    claim_count: int = Field(default=0)
    started_at: datetime = Field(default_factory=_utcnow_naive)
    ended_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow_naive)
    deleted_at: datetime | None = Field(default=None)
