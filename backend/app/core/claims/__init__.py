"""Claims package — claim detection and verification pipeline.

Provides ``get_claim_detector`` and ``get_claim_verifier`` factories
for extracting and verifying factual claims from transcripts.

Usage::

    from app.core.claims import get_claim_detector, get_claim_verifier

    detector = get_claim_detector()
    claims = await detector.detect_claims(segments)

    verifier = get_claim_verifier()
    verdict = await verifier.verify_claim(claim, vault_id, db)
"""

from app.core.claims.base import (
    ClaimDetector,
    ClaimVerifier,
    Claim,
    ClaimVerdict,
    Evidence,
)
from app.core.claims.detector import GroqClaimDetector
from app.core.claims.verifier import RAGClaimVerifier
from app.core.claims.exceptions import ClaimDetectionError, ClaimVerificationError
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_detector: ClaimDetector | None = None
_verifier: ClaimVerifier | None = None


def get_claim_detector() -> ClaimDetector:
    """Return the shared claim detector instance.

    Lazily initialised on first call.

    Returns:
        ClaimDetector: Configured Groq-based claim detector.
    """
    global _detector
    if _detector is None:
        settings = get_settings()
        _detector = GroqClaimDetector(
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
        )
        logger.info(f"Initialised claim detector: model={settings.GROQ_MODEL}")
    return _detector


def get_claim_verifier() -> ClaimVerifier:
    """Return the shared claim verifier instance.

    Lazily initialised on first call. Uses the same OpenAI model
    as the RAG generation pipeline for consistency.

    Returns:
        ClaimVerifier: Configured RAG-based claim verifier.
    """
    global _verifier
    if _verifier is None:
        settings = get_settings()
        _verifier = RAGClaimVerifier(
            model=settings.OPENAI_QUERY_MODEL,
            temperature=settings.RAG_GENERATION_TEMPERATURE,
            api_key=settings.OPENAI_API_KEY,
            top_k=settings.CLAIM_VERIFICATION_TOP_K,
        )
        logger.info("Initialised claim verifier")
    return _verifier


__all__ = [
    "ClaimDetector",
    "ClaimVerifier",
    "Claim",
    "ClaimVerdict",
    "Evidence",
    "GroqClaimDetector",
    "RAGClaimVerifier",
    "ClaimDetectionError",
    "ClaimVerificationError",
    "get_claim_detector",
    "get_claim_verifier",
]
