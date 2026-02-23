import json

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.logger import setup_logger

logger = setup_logger(__name__)

_MAX_SUBQUERIES = 5
_MAX_QUERY_LENGTH = 2000

_DECOMPOSE_PROMPT = """Break this complex query into independent sub-questions that can be searched separately.
Each sub-question must be self-contained and specific.
Maximum {max_n} sub-questions.

Query: {query}

Respond ONLY with JSON: {{"sub_queries": ["question 1", "question 2"]}}"""


async def decompose_query(
    query: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> list[str]:
    """Decompose a complex multi-part query into independent sub-queries.

    Splits compound claims or comparisons into atomic sub-questions
    that can be searched and evaluated independently. Each sub-query
    targets a single fact.

    Gracefully degrades: returns [original_query] on any failure.

    Args:
        query: The complex query to decompose.
        api_key: OpenAI API key. Empty string skips decomposition.
        model: LLM model for decomposition.

    Returns:
        list[str]: Independent sub-queries. At least one, at most five.
    """
    trimmed = query.strip()[:_MAX_QUERY_LENGTH]
    if not trimmed or not api_key:
        return [query]

    try:
        raw = await _call_decompose(trimmed, api_key, model)
        sub_queries = _parse_subqueries(raw)
        return sub_queries if sub_queries else [trimmed]
    except Exception as e:
        logger.warning(f"Query decomposition failed: {e}")
        return [trimmed]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
)
async def _call_decompose(query: str, api_key: str, model: str) -> str:
    """Call the LLM to decompose the query.

    Args:
        query: Query text to decompose.
        api_key: OpenAI API key.
        model: Model name.

    Returns:
        str: Raw JSON response.
    """
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": _DECOMPOSE_PROMPT.format(
                    query=query, max_n=_MAX_SUBQUERIES
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=512,
    )
    return response.choices[0].message.content or ""


def _parse_subqueries(raw: str) -> list[str]:
    """Parse the LLM JSON response into sub-queries.

    Handles both {"sub_queries": [...]} and direct list formats.

    Args:
        raw: Raw JSON string.

    Returns:
        list[str]: Parsed sub-queries, or empty list on failure.
    """
    try:
        data = json.loads(raw)

        if isinstance(data, dict):
            items = next(
                (v for v in data.values() if isinstance(v, list)),
                [],
            )
        elif isinstance(data, list):
            items = data
        else:
            return []

        queries = [str(v).strip() for v in items if v and str(v).strip()]
        return queries[:_MAX_SUBQUERIES]
    except (json.JSONDecodeError, TypeError):
        return []
