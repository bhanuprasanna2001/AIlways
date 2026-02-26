"""Copilot data models — statements, evidence, and verdicts.

These models are the copilot's canonical types. They intentionally
mirror the shapes expected by ``SessionPersistence`` and the WebSocket
schemas so the pipeline.py adapter layer stays thin.

The ``Statement`` model replaces the old ``Claim`` model with identical
field names — ``persist_claim`` in ``SessionPersistence`` reads the
same attributes, so it works without changes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Extraction output
# ---------------------------------------------------------------------------

class Statement(BaseModel):
    """A verifiable factual statement extracted from a transcript.

    Fields intentionally match the old ``Claim`` model so that
    ``SessionPersistence.persist_claim()`` works unchanged.
    """

    id: str
    text: str
    speaker: int
    timestamp_start: float
    timestamp_end: float
    context: str


# ---------------------------------------------------------------------------
# Evidence & verdict
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """A piece of evidence from the vault supporting or contradicting a statement."""

    doc_title: str
    section: str | None = None
    page: int | None = None
    quote: str
    relevance_score: float


class Verdict(BaseModel):
    """The result of verifying a statement against vault documents.

    Verdicts:
      - ``supported``: Evidence in the vault confirms the statement.
      - ``contradicted``: Evidence in the vault contradicts the statement.
      - ``unverifiable``: No relevant evidence found in the vault.
    """

    claim_id: str
    claim_text: str
    verdict: Literal["supported", "contradicted", "unverifiable"]
    confidence: float
    explanation: str
    evidence: list[Evidence] = []
    verification_path: str | None = None
    latency_ms: int | None = None
    cache_hit: bool = False


# ---------------------------------------------------------------------------
# Query agent output
# ---------------------------------------------------------------------------

class CopilotAnswer(BaseModel):
    """Structured answer from the copilot query agent."""

    answer: str
    citations: list[Evidence] = []
    confidence: float = 0.0
    has_sufficient_evidence: bool = False
