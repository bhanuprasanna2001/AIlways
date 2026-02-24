"""Claim protocol and shared data models."""

from __future__ import annotations

from typing import Protocol, runtime_checkable, Literal
from uuid import UUID

from pydantic import BaseModel

from app.core.transcription.base import TranscriptSegment


@runtime_checkable
class ClaimDetector(Protocol):
    """Protocol for claim detectors.

    Any class that implements ``detect_claims`` with the correct
    signature satisfies this protocol — no inheritance needed.

    To add a new detector (e.g. OpenAI-based):
        1. Create ``app/core/claims/openai_detector.py``.
        2. Update ``get_claim_detector()`` in ``claims/__init__.py``.
    """

    async def detect_claims(
        self, segments: list[TranscriptSegment],
    ) -> list[Claim]:
        """Extract verifiable factual claims from transcript segments.

        Args:
            segments: Speaker-diarized transcript segments.

        Returns:
            list[Claim]: Extracted claims with speaker attribution.
        """
        ...


@runtime_checkable
class ClaimVerifier(Protocol):
    """Protocol for claim verifiers.

    Any class that implements ``verify_claim`` with the correct
    signature satisfies this protocol — no inheritance needed.
    """

    async def verify_claim(
        self, claim: "Claim", vault_id: UUID, db: object,
    ) -> "ClaimVerdict":
        """Verify a claim against documents in a vault.

        Args:
            claim: The claim to verify.
            vault_id: Vault to search against.
            db: Async database session.

        Returns:
            ClaimVerdict: Verification result with evidence.
        """
        ...


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class Claim(BaseModel):
    """A factual claim extracted from a transcript.

    Represents a verifiable assertion made by a speaker during
    a conversation, with enough context to verify against
    document evidence.
    """

    id: str
    text: str
    speaker: int
    timestamp_start: float
    timestamp_end: float
    context: str


class Evidence(BaseModel):
    """A piece of evidence from the vault supporting or contradicting a claim."""

    doc_title: str
    section: str | None = None
    page: int | None = None
    quote: str
    relevance_score: float


class ClaimVerdict(BaseModel):
    """The result of verifying a claim against vault documents.

    Verdicts:
      - ``supported``: Evidence in the vault confirms the claim.
      - ``contradicted``: Evidence in the vault contradicts the claim.
      - ``unverifiable``: No relevant evidence found in the vault.
    """

    claim_id: str
    claim_text: str
    verdict: Literal["supported", "contradicted", "unverifiable"]
    confidence: float
    explanation: str
    evidence: list[Evidence] = []
