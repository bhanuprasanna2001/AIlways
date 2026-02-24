"""RAG-based claim verifier — verifies claims against vault documents."""

from __future__ import annotations

import json
import re
from uuid import UUID

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.claims.base import Claim, ClaimVerdict, Evidence
from app.core.claims.exceptions import ClaimVerificationError
from app.core.rag.embedding import get_embedder
from app.core.rag.retrieval import hybrid_search
from app.core.rag.retrieval.base import SearchResult
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)
SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Verification prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a fact-checking assistant. Your job is to verify whether a claim is supported or contradicted by the provided document evidence.

Analyze the claim against the context and determine:
1. Is the claim SUPPORTED by the evidence? (Evidence confirms it)
2. Is the claim CONTRADICTED by the evidence? (Evidence directly refutes it)
3. Is the claim UNVERIFIABLE? (Evidence is insufficient or irrelevant)

CRITICAL RULES — read carefully:
- Use ONLY the provided context. Do not use prior knowledge.
- Be precise about numbers, dates, and specific details.
- The context below is a SAMPLE of documents retrieved from the vault. It is NOT the complete vault.
- Do NOT assume that absence from this context means absence from the vault.

Verdict guidelines:
- SUPPORTED: The context contains a document about the SAME specific entity (same invoice number, same order, same product) AND the facts match the claim.
- CONTRADICTED: The context contains a document about the SAME specific entity AND the facts DIFFER from what the claim states. For example, if the claim says "invoice 10248 total is $440" and the context contains invoice 10248 showing a total of $500, that is CONTRADICTED.
- UNVERIFIABLE: The context does NOT contain the specific entity mentioned in the claim, OR the context does not address the specific fact being claimed. If you find invoices 10253, 10259, etc. but the claim is about invoice 10248, that is UNVERIFIABLE because the RIGHT document was not retrieved — it does NOT mean the claim is wrong.

Common mistake to avoid: Finding OTHER entities (different invoice numbers, different orders) and concluding the claim is contradicted. Different entities are irrelevant — they neither support nor contradict a claim about a specific entity.

Respond ONLY with valid JSON matching this schema:
{
    "verdict": "supported" | "contradicted" | "unverifiable",
    "confidence": 0.0,
    "explanation": "Clear explanation of why this verdict was reached",
    "evidence": [
        {
            "doc_title": "Document title",
            "section": "Section heading or null",
            "page": 1,
            "quote": "Exact quote from the context",
            "relevance_score": 0.9
        }
    ]
}

- confidence is a float between 0.0 and 1.0
- If unverifiable, set confidence to 0.0 and evidence to []"""

_USER_TEMPLATE = """CLAIM: {claim}

CONTEXT FROM VAULT DOCUMENTS:
{context}

Verify whether this claim is supported, contradicted, or unverifiable based on the above context."""

_UNVERIFIABLE = ClaimVerdict(
    claim_id="",
    claim_text="",
    verdict="unverifiable",
    confidence=0.0,
    explanation="No relevant evidence found in the vault.",
)


class RAGClaimVerifier:
    """Verifies claims against vault documents using the RAG pipeline.

    Pipeline: embed claim → hybrid search vault → LLM verification.

    Uses the existing embedding and retrieval modules to find relevant
    evidence, then a verification-specific LLM prompt to determine
    if the claim is supported, contradicted, or unverifiable.

    Args:
        model: OpenAI model name for verification.
        temperature: Sampling temperature.
        api_key: OpenAI API key.
        top_k: Number of search results per claim.
    """

    def __init__(
        self,
        model: str,
        temperature: float,
        api_key: str,
        top_k: int = 5,
    ) -> None:
        self._llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        self._top_k = top_k
        logger.info(f"Initialised claim verifier: model={model}")

    async def verify_claim(
        self, claim: Claim, vault_id: UUID, db: AsyncSession,
    ) -> ClaimVerdict:
        """Verify a single claim against vault documents.

        1. Embeds the claim text (enriched with context for better retrieval).
        2. Runs hybrid search (dense + BM25 + RRF + MMR) against the vault.
        3. Uses a verification-specific LLM prompt to determine verdict.

        The search query combines ``claim.text`` with ``claim.context``
        so that entity references (e.g. an invoice number mentioned
        earlier in conversation) improve retrieval precision.

        Args:
            claim: The claim to verify.
            vault_id: Vault to search against.
            db: Async database session.

        Returns:
            ClaimVerdict: Verification result with evidence and explanation.
        """
        try:
            # Build enriched search text: claim + context for better retrieval
            search_text = claim.text
            if claim.context:
                search_text = f"{claim.text} {claim.context}"

            # Normalize: collapse thousand-separator commas in numbers
            # so that "10,248" → "10248" matches document text.
            search_text = _normalize_numbers(search_text)

            # Extract entity identifiers (invoice/order numbers) and
            # prepend them to boost targeted retrieval.  Without this,
            # dense search across 800+ near-identical invoices returns
            # random results and the right document gets lost.
            entity_ids = re.findall(r"\b\d{4,}\b", search_text)
            if entity_ids:
                id_boost = " ".join(f"Order ID {eid}" for eid in entity_ids[:3])
                search_text = f"{id_boost} {search_text}"

            # 1. Embed the enriched search text
            embedder = get_embedder()
            claim_embedding = await embedder.embed_query(search_text)

            # 2. Hybrid search for relevant evidence
            #    mmr_lambda=1.0 → pure relevance ranking (no diversity).
            #    For verification we need the EXACT document, not a
            #    diverse sample across invoices.
            results = await hybrid_search(
                query_text=search_text,
                query_embedding=claim_embedding,
                vault_id=vault_id,
                db=db,
                top_k=self._top_k,
                mmr_lambda=SETTINGS.CLAIM_VERIFICATION_MMR_LAMBDA,
            )

            if not results:
                logger.info(f"No evidence found for claim: {claim.text[:50]}...")
                return ClaimVerdict(
                    claim_id=claim.id,
                    claim_text=claim.text,
                    verdict="unverifiable",
                    confidence=0.0,
                    explanation="No relevant documents found in the vault.",
                )

            # 3. Generate verification verdict
            return await self._verify_against_context(claim, results)

        except Exception as e:
            logger.error(f"Claim verification failed for '{claim.text[:50]}...': {e}")
            return ClaimVerdict(
                claim_id=claim.id,
                claim_text=claim.text,
                verdict="unverifiable",
                confidence=0.0,
                explanation=f"Verification failed: {e}",
            )

    async def _verify_against_context(
        self, claim: Claim, results: list[SearchResult],
    ) -> ClaimVerdict:
        """Run the LLM verification prompt against retrieved context.

        Args:
            claim: The claim being verified.
            results: Search results from hybrid retrieval.

        Returns:
            ClaimVerdict: Parsed verification result.
        """
        context = _build_context(results)
        # Normalize numbers in the claim text sent to the LLM so that
        # "10,248" matches "10248" in the document context.
        normalized_claim = _normalize_numbers(claim.text)
        user_message = _USER_TEMPLATE.format(claim=normalized_claim, context=context)

        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            return _parse_verdict(response.content, claim)
        except Exception as e:
            logger.error(f"LLM verification failed: {e}")
            return ClaimVerdict(
                claim_id=claim.id,
                claim_text=claim.text,
                verdict="unverifiable",
                confidence=0.0,
                explanation=f"LLM verification failed: {e}",
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_numbers(text: str) -> str:
    """Collapse thousand-separator commas in numeric strings.

    DeepGram's ``smart_format`` option inserts commas into numbers
    (e.g. ``10,248``), but documents store plain numbers (``10248``).
    This helper strips those commas so that embeddings and BM25
    match the document text.

    Examples:
        >>> _normalize_numbers("invoice 10,248 total $1,500.00")
        'invoice 10248 total $1500.00'
    """
    return re.sub(r"(\d),(\d)", r"\1\2", text)


def _build_context(results: list[SearchResult]) -> str:
    """Build context string from search results for the verification prompt.

    Args:
        results: Retrieved chunks from hybrid search.

    Returns:
        str: Formatted context for the LLM.
    """
    parts: list[str] = []
    for i, r in enumerate(results, 1):
        parts.append(f"--- Document Chunk {i} (relevance: {r.score:.3f}) ---")
        parts.append(r.content_with_header)
        parts.append("")
    return "\n".join(parts)


def _parse_verdict(raw: str, claim: Claim) -> ClaimVerdict:
    """Parse the LLM JSON response into a ClaimVerdict.

    Args:
        raw: Raw JSON string from the LLM.
        claim: The original claim for ID/text reference.

    Returns:
        ClaimVerdict: Parsed verdict, or unverifiable if parsing fails.
    """
    try:
        data = json.loads(raw)

        verdict = data.get("verdict", "unverifiable")
        if verdict not in ("supported", "contradicted", "unverifiable"):
            verdict = "unverifiable"

        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        evidence: list[Evidence] = []
        for e in data.get("evidence", []):
            evidence.append(Evidence(
                doc_title=e.get("doc_title", "Unknown"),
                section=e.get("section"),
                page=e.get("page"),
                quote=e.get("quote", ""),
                relevance_score=float(e.get("relevance_score", 0.0)),
            ))

        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict=verdict,
            confidence=confidence,
            explanation=data.get("explanation", ""),
            evidence=evidence,
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse verification response: {e}")
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="unverifiable",
            confidence=0.0,
            explanation=f"Failed to parse verification result: {e}",
        )
