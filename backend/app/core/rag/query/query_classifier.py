import json

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.rag.query.schemas import QueryType, ClassificationResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_MAX_QUERY_LENGTH = 2000

_DEFAULT = ClassificationResult()

_CLASSIFY_PROMPT = """Classify this query for a document retrieval system containing invoices, purchase orders, and shipping documents.

Types:
- factual_lookup: Direct factual question ("What is the total on INV-001?")
- temporal_claim: Time-related assertion ("XML pipelines are live now")
- comparison: Comparing entities ("Compare Q3 vs Q4 revenue")
- aggregation: Totals, counts, summaries ("Total shipments this quarter")
- existence_check: Checking existence ("Do we have a PO for vendor X?")
- vague_exploratory: Vague or broad ("Something about timelines")

Extract:
- entities: Specific names, IDs, numbers mentioned (invoice numbers, vendor names, dates)
- temporal_refs: Time references (Q3, 2024, last month, now)
- is_multi_part: Whether the query contains multiple distinct claims or comparisons

Respond ONLY with JSON:
{{"query_type": "factual_lookup", "is_multi_part": false, "entities": [], "temporal_refs": [], "confidence": 0.9}}

Query: {query}"""


async def classify_query(
    query: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> ClassificationResult:
    """Classify a query to determine the optimal retrieval strategy.

    Uses GPT-4o-mini with structured output to detect query intent,
    extract entities, and identify multi-part claims. The classification
    drives downstream routing: expansion strategy, decomposition, and
    CRAG confidence thresholds.

    Gracefully degrades: returns FACTUAL_LOOKUP default on any failure.

    Args:
        query: The user's query text.
        api_key: OpenAI API key. Empty string skips classification.
        model: LLM model for classification.

    Returns:
        ClassificationResult: Query type, entities, and multi-part flag.
    """
    if not api_key:
        logger.warning("No OpenAI API key — skipping query classification")
        return _DEFAULT

    trimmed = query.strip()[:_MAX_QUERY_LENGTH]
    if not trimmed:
        return ClassificationResult(
            query_type=QueryType.VAGUE_EXPLORATORY, confidence=0.0
        )

    try:
        raw = await _call_classify(trimmed, api_key, model)
        return _parse_classification(raw)
    except Exception as e:
        logger.warning(f"Query classification failed, using default: {e}")
        return _DEFAULT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
)
async def _call_classify(query: str, api_key: str, model: str) -> str:
    """Call the LLM to classify the query.

    Args:
        query: Query text.
        api_key: OpenAI API key.
        model: Model name.

    Returns:
        str: Raw JSON response from the LLM.
    """
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": _CLASSIFY_PROMPT.format(query=query)},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=256,
    )
    return response.choices[0].message.content or ""


def _parse_classification(raw: str) -> ClassificationResult:
    """Parse the LLM JSON response into a ClassificationResult.

    Args:
        raw: Raw JSON string.

    Returns:
        ClassificationResult: Parsed result, or default on parse error.
    """
    try:
        data = json.loads(raw)
        query_type = data.get("query_type", "factual_lookup")

        try:
            qt = QueryType(query_type)
        except ValueError:
            qt = QueryType.FACTUAL_LOOKUP

        return ClassificationResult(
            query_type=qt,
            is_multi_part=bool(data.get("is_multi_part", False)),
            entities=data.get("entities", []) or [],
            temporal_refs=data.get("temporal_refs", []) or [],
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
        )
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.warning(f"Failed to parse classification: {e}")
        return _DEFAULT
