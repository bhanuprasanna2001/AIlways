"""Generator protocol and shared response models."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.core.rag.retrieval.base import SearchResult


@runtime_checkable
class Generator(Protocol):
    """Protocol for answer generators.

    Any class that implements ``generate`` with the correct signature
    satisfies this protocol — no inheritance needed.

    To add a new generator (e.g. Anthropic):
        1. Create ``app/core/rag/generation/anthropic.py``.
        2. Update ``get_generator()`` in ``generation/__init__.py``.
    """

    async def generate(self, query: str, results: list[SearchResult]) -> AnswerResult:
        """Generate a grounded answer from retrieved context.

        Args:
            query: The user's question.
            results: Search results from the retrieval module.

        Returns:
            AnswerResult: Structured answer with citations and confidence.
        """
        ...


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class Citation(BaseModel):
    """A citation pointing back to source content."""

    doc_title: str
    section: str | None = None
    page: int | None = None
    quote: str


class AnswerResult(BaseModel):
    """The output of the generation step."""

    answer: str
    citations: list[Citation] = []
    confidence: float = 0.0
    has_sufficient_evidence: bool = False
