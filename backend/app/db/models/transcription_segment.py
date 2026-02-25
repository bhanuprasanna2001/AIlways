from uuid import UUID, uuid4
from datetime import datetime
from app.db.models.utils import _utcnow_naive
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class TranscriptionSegment(SQLModel, table=True):
    __tablename__ = "transcription_segments"
    __table_args__ = (
        UniqueConstraint("session_id", "segment_index", name="uq_segments_session_index"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    session_id: UUID = Field(foreign_key="transcription_sessions.id", nullable=False)
    text: str = Field(nullable=False)
    speaker: int = Field(nullable=False)
    start: float = Field(nullable=False)
    end: float = Field(nullable=False)
    confidence: float = Field(nullable=False)
    segment_index: int = Field(nullable=False)
    created_at: datetime = Field(default_factory=_utcnow_naive)
