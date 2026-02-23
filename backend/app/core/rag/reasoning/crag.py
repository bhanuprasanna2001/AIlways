import json

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.rag.retrieval.filters import SearchResult
from app.core.rag.reasoning.schemas import ReasoningResult, Citation
from app.core.rag.reasoning.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from app.core.rag.query.schemas import (
    ClassificationResult,
    RetrievalQuality,
    RetrievalQualitySignals,
)
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_INSUFFICIENT = ReasoningResult(
    answer="Insufficient evidence in vault.",
    citations=[],
    confidence=0.0,
    has_sufficient_evidence=False,
)

_QUALITY_HIGH_THRESHOLD = 0.7
_QUALITY_LOW_THRESHOLD = 0.4

_CONFIDENCE_QUALIFIER = (
    "\n\nNote: The retrieved evidence may be incomplete. "
    "Explicitly state any uncertainty in your answer."
)


# ---------------------------------------------------------------------------
# Retrieval quality evaluation (heuristic — no LLM call)
# ---------------------------------------------------------------------------


def evaluate_retrieval_quality(
    results: list[SearchResult],
    classification: ClassificationResult,
) -> RetrievalQualitySignals:
    """Evaluate retrieval quality using cheap heuristic signals.

    Scores retrieval results without an LLM call. Uses score
    distribution, entity overlap, and result count to decide
    whether to proceed to reasoning, retry with corrections,
    or return insufficient evidence immediately.

    Thresholds:
      > 0.7  HIGH (proceed to reasoning)
      0.4-0.7  UNCERTAIN (corrective retry)
      < 0.4  INSUFFICIENT (return immediately, no LLM cost)

    Args:
        results: Search results after reranking/MMR.
        classification: Query classification with extracted entities.

    Returns:
        RetrievalQualitySignals: Quality tier, score, and component signals.
    """
    if not results:
        return RetrievalQualitySignals(
            quality=RetrievalQuality.INSUFFICIENT,
            score=0.0,
            result_count=0,
        )

    top_score = results[0].score
    last_score = results[-1].score if len(results) > 1 else top_score
    spread = top_score - last_score

    entity_ratio = _compute_entity_overlap(results[:5], classification.entities)

    # Weighted combination of normalized signals
    score = (
        0.40 * min(top_score, 1.0)
        + 0.25 * entity_ratio
        + 0.20 * min(spread * 10, 1.0)
        + 0.15 * min(len(results) / 5, 1.0)
    )
    score = max(0.0, min(1.0, score))

    if score >= _QUALITY_HIGH_THRESHOLD:
        quality = RetrievalQuality.HIGH
    elif score >= _QUALITY_LOW_THRESHOLD:
        quality = RetrievalQuality.UNCERTAIN
    else:
        quality = RetrievalQuality.INSUFFICIENT

    return RetrievalQualitySignals(
        quality=quality,
        score=score,
        top_score=top_score,
        score_spread=spread,
        entity_overlap_ratio=entity_ratio,
        result_count=len(results),
    )


def _compute_entity_overlap(
    results: list[SearchResult], entities: list[str]
) -> float:
    """Compute fraction of query entities found in top search results.

    Uses token-level matching: a multi-word entity like "invoice 10248"
    counts as a match if any of its significant tokens (length >= 3)
    appear in the results. Falls back to full-entity matching when
    all tokens are short.

    Args:
        results: Top search results to check.
        entities: Entities extracted from the query.

    Returns:
        float: Overlap ratio in [0.0, 1.0]. Returns 0.5 if no entities.
    """
    if not entities:
        return 0.5

    combined = " ".join(r.content.lower() for r in results)
    matches = 0
    for entity in entities:
        low = entity.lower()
        # Exact match first
        if low in combined:
            matches += 1
            continue
        # Token-level: check if any significant token appears
        tokens = [t for t in low.split() if len(t) >= 3]
        if tokens and any(t in combined for t in tokens):
            matches += 1
    return matches / len(entities)


# ---------------------------------------------------------------------------
# Reasoning (LLM call)
# ---------------------------------------------------------------------------


async def reason(
    query: str,
    search_results: list[SearchResult],
    api_key: str,
    model: str = "gpt-4o",
    confidence_qualifier: bool = False,
) -> ReasoningResult:
    """Generate a grounded, cited answer from retrieved chunks.

    Args:
        query: The user's question.
        search_results: Retrieved chunks from the search step.
        api_key: OpenAI API key.
        model: The reasoning model to use.
        confidence_qualifier: When True, instructs the LLM to state
            uncertainty explicitly (used after CRAG corrective retry).

    Returns:
        ReasoningResult: Answer with citations and confidence score.
    """
    if not query or not query.strip():
        return _INSUFFICIENT

    if not search_results:
        return _INSUFFICIENT

    context = _build_context(search_results)
    user_message = USER_PROMPT_TEMPLATE.format(context=context, query=query)

    if confidence_qualifier:
        user_message += _CONFIDENCE_QUALIFIER

    client = AsyncOpenAI(api_key=api_key)

    try:
        response = await _call_llm(client, model, user_message)
        return _parse_response(response)
    except Exception as e:
        logger.error(f"Reasoning failed: {e}")
        return _INSUFFICIENT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def _call_llm(client: AsyncOpenAI, model: str, user_message: str) -> str:
    """Call the OpenAI chat completion API.

    Args:
        client: The OpenAI async client.
        model: Model name.
        user_message: The user prompt with context and query.

    Returns:
        str: The raw response content.
    """
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return response.choices[0].message.content or ""


def _build_context(results: list[SearchResult]) -> str:
    """Build the context string from search results.

    Includes parent context when available for richer reasoning.

    Args:
        results: List of search results.

    Returns:
        str: Formatted context for the LLM prompt.
    """
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"--- Chunk {i} (score: {r.score:.3f}) ---")
        parts.append(r.content_with_header)
        if r.parent_content:
            parts.append(f"\n[Parent context]\n{r.parent_content}")
        parts.append("")
    return "\n".join(parts)


def _parse_response(raw: str) -> ReasoningResult:
    """Parse the LLM JSON response into a ReasoningResult.

    Args:
        raw: Raw JSON string from the LLM.

    Returns:
        ReasoningResult: Parsed result. Falls back to insufficient evidence on parse error.
    """
    try:
        data = json.loads(raw)

        citations = []
        for c in data.get("citations", []):
            citations.append(Citation(
                doc_title=c.get("doc_title", "Unknown"),
                section=c.get("section"),
                page=c.get("page"),
                quote=c.get("quote", ""),
            ))

        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        return ReasoningResult(
            answer=data.get("answer", ""),
            citations=citations,
            confidence=confidence,
            has_sufficient_evidence=data.get("has_sufficient_evidence", False),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse LLM response: {e}")
        return _INSUFFICIENT
