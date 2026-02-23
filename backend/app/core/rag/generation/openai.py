"""OpenAI answer generator — wraps langchain-openai ChatOpenAI."""

from __future__ import annotations

import json

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.core.rag.retrieval.base import SearchResult
from app.core.rag.generation.base import Citation, AnswerResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are AIlways, a document truth copilot.

Your job is to answer questions using ONLY the provided context. Follow these rules strictly:

1. Use ONLY information from the CONTEXT below. Do not use prior knowledge.
2. If the context does not contain enough information to answer, set has_sufficient_evidence to false.
3. Always cite your sources with document title, section, page number, and an exact quote.
4. Be precise and factual. Never fabricate information.

Respond ONLY with valid JSON matching this schema:
{
    "answer": "Your answer here",
    "citations": [
        {
            "doc_title": "Document title",
            "section": "Section heading or null",
            "page": 1,
            "quote": "Exact quote from the context"
        }
    ],
    "confidence": 0.0,
    "has_sufficient_evidence": true
}

- confidence is a float between 0.0 and 1.0
- If you cannot answer, set answer to "Insufficient evidence in vault.", confidence to 0.0, has_sufficient_evidence to false, and citations to []
"""

_USER_TEMPLATE = """CONTEXT:
{context}

QUESTION: {query}"""

_INSUFFICIENT = AnswerResult(
    answer="Insufficient evidence in vault.",
    confidence=0.0,
    has_sufficient_evidence=False,
)


class OpenAIGenerator:
    """Generates grounded answers using OpenAI ChatGPT.

    Uses JSON-mode structured output to ensure consistent response
    format. Wraps ``langchain_openai.ChatOpenAI``.

    Args:
        model: OpenAI model name (e.g. ``'gpt-4o-mini'``).
        temperature: Sampling temperature.
        api_key: OpenAI API key.
    """

    def __init__(self, model: str, temperature: float, api_key: str) -> None:
        self._llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        logger.info(f"Initialised generator: model={model}")

    async def generate(self, query: str, results: list[SearchResult]) -> AnswerResult:
        """Generate a grounded answer from retrieved context.

        Args:
            query: The user's question.
            results: Search results from the retrieval module.

        Returns:
            AnswerResult: Structured answer with citations and confidence.
        """
        if not query or not query.strip():
            return _INSUFFICIENT
        if not results:
            return _INSUFFICIENT

        context = _build_context(results)
        user_message = _USER_TEMPLATE.format(context=context, query=query)

        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            return _parse_response(response.content)
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            return _INSUFFICIENT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_context(results: list[SearchResult]) -> str:
    """Build the context string from search results.

    Args:
        results: Retrieved chunks.

    Returns:
        str: Formatted context for the LLM prompt.
    """
    parts: list[str] = []
    for i, r in enumerate(results, 1):
        parts.append(f"--- Chunk {i} (score: {r.score:.3f}) ---")
        parts.append(r.content_with_header)
        parts.append("")
    return "\n".join(parts)


def _parse_response(raw: str) -> AnswerResult:
    """Parse the LLM JSON response into an AnswerResult.

    Args:
        raw: Raw JSON string from the LLM.

    Returns:
        AnswerResult: Parsed result, or insufficient if parsing fails.
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

        return AnswerResult(
            answer=data.get("answer", ""),
            citations=citations,
            confidence=confidence,
            has_sufficient_evidence=data.get("has_sufficient_evidence", False),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse LLM response: {e}")
        return _INSUFFICIENT
