"""Request/Response schemas for the transcription router."""

from __future__ import annotations

from typing import Literal
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.core.copilot.base import Evidence


# ---------------------------------------------------------------------------
# Response schemas — pre-recorded transcription
# ---------------------------------------------------------------------------

class TranscriptSegmentResponse(BaseModel):
    """A single speaker-diarized transcript segment."""

    text: str
    speaker: int
    start: float
    end: float
    confidence: float


class ClaimResponse(BaseModel):
    """A detected claim from the transcript."""

    id: str
    text: str
    speaker: int
    timestamp_start: float
    timestamp_end: float
    context: str


class ClaimVerdictResponse(BaseModel):
    """Verification result for a detected claim."""

    claim_id: str
    claim_text: str
    verdict: Literal["supported", "contradicted", "unverifiable"]
    confidence: float
    explanation: str
    evidence: list[Evidence] = []
    verification_path: str | None = None
    latency_ms: int | None = None
    cache_hit: bool = False


class TranscriptionResponse(BaseModel):
    """Complete transcription + claim verification response for pre-recorded audio."""

    segments: list[TranscriptSegmentResponse]
    full_text: str
    speakers: int
    duration: float
    claims: list[ClaimResponse] = []
    verdicts: list[ClaimVerdictResponse] = []
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Response schemas — transcription sessions (history)
# ---------------------------------------------------------------------------

class SessionListResponse(BaseModel):
    """Summary of a transcription session for list views."""

    id: UUID
    vault_id: UUID
    vault_name: str
    title: str
    status: str
    duration_seconds: float | None
    speaker_count: int
    segment_count: int
    claim_count: int
    started_at: datetime
    ended_at: datetime | None


class SessionSegmentResponse(BaseModel):
    """A persisted transcript segment belonging to a session."""

    id: UUID
    text: str
    speaker: int
    start: float
    end: float
    confidence: float
    segment_index: int


class SessionClaimResponse(BaseModel):
    """A persisted claim with its verification verdict."""

    id: UUID
    text: str
    speaker: int
    timestamp_start: float
    timestamp_end: float
    context: str
    verdict: str
    confidence: float
    explanation: str | None
    evidence: list[Evidence] = []
    verification_path: str | None = None
    latency_ms: int | None = None
    cache_hit: bool = False


class SessionDetailResponse(BaseModel):
    """Full transcription session with segments and claims."""

    id: UUID
    vault_id: UUID
    vault_name: str
    title: str
    status: str
    duration_seconds: float | None
    speaker_count: int
    segment_count: int
    claim_count: int
    started_at: datetime
    ended_at: datetime | None
    segments: list[SessionSegmentResponse]
    claims: list[SessionClaimResponse]


class SessionUpdateRequest(BaseModel):
    """Request body for renaming a transcription session."""

    title: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Session title cannot be empty")
        if len(v) > 255:
            raise ValueError("Session title must be at most 255 characters")
        return v


# ---------------------------------------------------------------------------
# WebSocket message schemas (JSON)
# ---------------------------------------------------------------------------

class WSTranscriptMessage(BaseModel):
    """WebSocket message: transcript segment update."""

    type: Literal["transcript"] = "transcript"
    text: str
    speaker: int
    start: float
    end: float
    confidence: float
    is_final: bool


class WSClaimDetectedMessage(BaseModel):
    """WebSocket message: a new claim was detected."""

    type: Literal["claim_detected"] = "claim_detected"
    claim_id: str
    text: str
    speaker: int
    status: Literal["verifying"] = "verifying"


class WSClaimVerifiedMessage(BaseModel):
    """WebSocket message: a claim was verified against vault."""

    type: Literal["claim_verified"] = "claim_verified"
    claim_id: str
    claim_text: str
    verdict: Literal["supported", "contradicted", "unverifiable"]
    confidence: float
    explanation: str
    evidence: list[Evidence] = []
    verification_path: str | None = None
    latency_ms: int | None = None
    cache_hit: bool = False


class WSSessionStartedMessage(BaseModel):
    """WebSocket message: session has been created and recording started."""

    type: Literal["session_started"] = "session_started"
    session_id: str


class WSSessionEndedMessage(BaseModel):
    """WebSocket message: session has been finalized."""

    type: Literal["session_ended"] = "session_ended"
    session_id: str
    duration_seconds: float


class WSErrorMessage(BaseModel):
    """WebSocket message: error notification."""

    type: Literal["error"] = "error"
    message: str
