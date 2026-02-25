from uuid import UUID, uuid4
from datetime import datetime
from app.db.models.utils import _utcnow_naive
from sqlmodel import Field, SQLModel


class TranscriptionClaim(SQLModel, table=True):
    __tablename__ = "transcription_claims"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    session_id: UUID = Field(foreign_key="transcription_sessions.id", nullable=False)
    text: str = Field(nullable=False)
    speaker: int = Field(nullable=False)
    timestamp_start: float = Field(nullable=False)
    timestamp_end: float = Field(nullable=False)
    context: str = Field(default="", nullable=False)
    verdict: str = Field(default="pending", max_length=20, nullable=False)
    confidence: float = Field(default=0.0)
    explanation: str | None = Field(default=None)
    evidence_json: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow_naive)
    updated_at: datetime = Field(default_factory=_utcnow_naive)
