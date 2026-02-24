"""Request/Response schemas for the transcription router."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.core.claims.base import Evidence


# ---------------------------------------------------------------------------
# Response schemas
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


class WSErrorMessage(BaseModel):
    """WebSocket message: error notification."""

    type: Literal["error"] = "error"
    message: str
