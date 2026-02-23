import json

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.rag.query.schemas import QueryType, ClassificationResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_MAX_VARIANTS = 5
_MAX_QUERY_LENGTH = 2000

# How many expansion variants to generate per query type
_VARIANT_COUNTS: dict[QueryType, int] = {
    QueryType.FACTUAL_LOOKUP: 2,
    QueryType.TEMPORAL_CLAIM: 3,
    QueryType.COMPARISON: 0,
    QueryType.AGGREGATION: 2,
    QueryType.EXISTENCE_CHECK: 1,
    QueryType.VAGUE_EXPLORATORY: 3,
}

_EXPAND_PROMPT = """Generate {n} alternative search queries capturing different angles of the original question.
Each variant must be precise and search-friendly.
Preserve any document IDs, names, dates, or amounts from the original.
NEVER invent or fabricate specific values (names, IDs, dates, amounts) not in the original query.

Original: {query}

Respond ONLY with JSON: {{"queries": ["variant 1", "variant 2"]}}"""

_HYDE_PROMPT = """Write a short factual paragraph (3-4 sentences) that might appear in a business document answering this question. Write the paragraph directly, no introduction or preamble.

Question: {query}"""


async def expand_query(
    query: str,
    classification: ClassificationResult,
    api_key: str,
    model: str = "gpt-4o-mini",
    force_hyde: bool = False,
) -> list[str]:
    """Generate semantic query variants for multi-query retrieval.

    Produces alternative phrasings of the query to capture different
    semantic angles. Optionally generates a HyDE (Hypothetical Document
    Embedding) passage for vague queries. Always includes the original
    query in the result.

    Args:
        query: The primary query text.
        classification: Query classification result.
        api_key: OpenAI API key. Empty string returns original only.
        model: LLM model for expansion.
        force_hyde: Force HyDE generation regardless of query type.

    Returns:
        list[str]: Deduplicated query variants, always starting with original.
    """
    trimmed = query.strip()[:_MAX_QUERY_LENGTH]
    if not trimmed:
        return [query]

    variants = [trimmed]
    use_hyde = force_hyde or classification.query_type == QueryType.VAGUE_EXPLORATORY
    variant_count = _VARIANT_COUNTS.get(classification.query_type, 2)

    if not api_key or variant_count == 0:
        return variants

    # Multi-query expansion
    try:
        expanded = await _call_expand(trimmed, variant_count, api_key, model)
        variants.extend(expanded)
    except Exception as e:
        logger.warning(f"Query expansion failed: {e}")

    # HyDE passage
    if use_hyde:
        try:
            hyde = await _call_hyde(trimmed, api_key, model)
            if hyde:
                variants.append(hyde)
        except Exception as e:
            logger.warning(f"HyDE generation failed: {e}")

    return _deduplicate(variants)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
)
async def _call_expand(query: str, n: int, api_key: str, model: str) -> list[str]:
    """Call the LLM to generate query variants.

    Args:
        query: Original query text.
        n: Number of variants to generate.
        api_key: OpenAI API key.
        model: Model name.

    Returns:
        list[str]: Generated query variants.
    """
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": _EXPAND_PROMPT.format(query=query, n=n)},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=512,
    )
    raw = response.choices[0].message.content or ""
    return _parse_variants(raw)


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
)
async def _call_hyde(query: str, api_key: str, model: str) -> str:
    """Generate a hypothetical document passage for HyDE.

    The passage is embedded and used for dense search, bridging the
    query-document semantic gap for vague or conceptual queries.

    Args:
        query: Original query text.
        api_key: OpenAI API key.
        model: Model name.

    Returns:
        str: Hypothetical passage, or empty string on failure.
    """
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": _HYDE_PROMPT.format(query=query)},
        ],
        temperature=0.5,
        max_tokens=256,
    )
    return (response.choices[0].message.content or "").strip()


def _parse_variants(raw: str) -> list[str]:
    """Parse the LLM response into a list of variant strings.

    Handles both {"queries": [...]} and direct list formats.

    Args:
        raw: Raw JSON string from the LLM.

    Returns:
        list[str]: Parsed variants, or empty list on failure.
    """
    try:
        data = json.loads(raw)

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = next(
                (v for v in data.values() if isinstance(v, list)),
                [],
            )
        else:
            return []

        return [str(v).strip() for v in items if v and str(v).strip()][:_MAX_VARIANTS]
    except (json.JSONDecodeError, TypeError):
        return []


def _deduplicate(queries: list[str]) -> list[str]:
    """Remove duplicate queries (case-insensitive) preserving order.

    Args:
        queries: List of query strings.

    Returns:
        list[str]: Deduplicated list, capped at _MAX_VARIANTS.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[:_MAX_VARIANTS]
