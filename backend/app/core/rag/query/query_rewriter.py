from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.logger import setup_logger

logger = setup_logger(__name__)

_MAX_QUERY_LENGTH = 2000

_REWRITE_PROMPT = """Rewrite this question to be specific and search-friendly.
Preserve any mentions of document numbers, names, dates, or amounts.
Do not add information not present in the original question.
If the question is already specific, return it unchanged.

Original: {query}
Rewritten:"""


async def rewrite_query(
    query: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> str:
    """Rewrite a vague query into a precise, retrieval-friendly form.

    Uses a lightweight LLM (GPT-4o-mini) to transform ambiguous queries
    into specific search queries. Already-specific queries (containing
    proper nouns, numbers, dates) are returned unchanged by the LLM.

    Gracefully degrades: if the API call fails or returns empty, the
    original query is returned unchanged.

    Args:
        query: The user's original query text.
        api_key: OpenAI API key. Empty string skips rewriting.
        model: LLM model to use for rewriting.

    Returns:
        str: Rewritten query, or original if rewriting fails/skipped.
    """
    if not api_key:
        logger.warning("No OpenAI API key — skipping query rewrite")
        return query

    trimmed = query.strip()[:_MAX_QUERY_LENGTH]
    if not trimmed:
        return query

    try:
        rewritten = await _call_rewrite(trimmed, api_key, model)
        result = rewritten.strip()
        if not result:
            return trimmed
        return result
    except Exception as e:
        logger.warning(f"Query rewrite failed, using original: {e}")
        return trimmed


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
)
async def _call_rewrite(query: str, api_key: str, model: str) -> str:
    """Call the LLM to rewrite the query.

    Args:
        query: Original query text.
        api_key: OpenAI API key.
        model: Model name.

    Returns:
        str: Raw rewritten query from the LLM.
    """
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": _REWRITE_PROMPT.format(query=query)},
        ],
        temperature=0.0,
        max_tokens=256,
    )
    return response.choices[0].message.content or ""
