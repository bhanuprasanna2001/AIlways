import json

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.rag.retrieval.filters import SearchResult
from app.core.rag.reasoning.schemas import ReasoningResult, Citation
from app.core.rag.reasoning.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_INSUFFICIENT = ReasoningResult(
    answer="Insufficient evidence in vault.",
    citations=[],
    confidence=0.0,
    has_sufficient_evidence=False,
)


async def reason(
    query: str,
    search_results: list[SearchResult],
    api_key: str,
    model: str = "gpt-4o",
) -> ReasoningResult:
    """Generate a grounded, cited answer from retrieved chunks.

    Args:
        query: The user's question.
        search_results: Retrieved chunks from the search step.
        api_key: OpenAI API key.
        model: The reasoning model to use.

    Returns:
        ReasoningResult: Answer with citations and confidence score.
    """
    if not query or not query.strip():
        return _INSUFFICIENT

    if not search_results:
        return _INSUFFICIENT

    # Build context from search results
    context = _build_context(search_results)

    # Call the LLM
    client = AsyncOpenAI(api_key=api_key)
    user_message = USER_PROMPT_TEMPLATE.format(context=context, query=query)

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
