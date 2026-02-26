"""OpenAI answer generator — wraps langchain-openai ChatOpenAI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.core.config import get_settings
from app.core.rag.retrieval.base import SearchResult, build_retrieval_context
from app.core.rag.generation.base import Citation, AnswerResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)
SETTINGS = get_settings()


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

_USER_TEMPLATE_WITH_HISTORY = """CONVERSATION HISTORY (for reference — answer the CURRENT QUESTION):
{history}

CONTEXT:
{context}

CURRENT QUESTION: {query}"""

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

    async def generate(
        self,
        query: str,
        results: list[SearchResult],
        history: list[dict[str, str]] | None = None,
    ) -> AnswerResult:
        """Generate a grounded answer from retrieved context.

        Args:
            query: The user's question.
            results: Search results from the retrieval module.
            history: Optional conversation history for multi-turn context.
                Each dict has ``role`` and ``content`` keys.

        Returns:
            AnswerResult: Structured answer with citations and confidence.
        """
        if not query or not query.strip():
            return _INSUFFICIENT
        if not results:
            return _INSUFFICIENT

        context = build_retrieval_context(results)
        user_message = _build_user_message(query, context, history)

        try:
            response = await asyncio.wait_for(
                self._llm.ainvoke([
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(content=user_message),
                ]),
                timeout=SETTINGS.API_TIMEOUT_S,
            )
            return parse_response(response.content)
        except asyncio.TimeoutError:
            logger.error("Generation timed out")
            return _INSUFFICIENT
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            return _INSUFFICIENT

    async def stream(
        self,
        query: str,
        results: list[SearchResult],
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """Stream raw LLM response tokens.

        Yields each content delta as a string. The caller accumulates
        the full response and passes it to ``parse_response()`` for
        structured parsing after the stream ends.

        Falls back to a single yield of the full response on error.
        """
        if not query or not query.strip() or not results:
            yield json.dumps(_INSUFFICIENT.model_dump())
            return

        context = build_retrieval_context(results)
        user_message = _build_user_message(query, context, history)

        try:
            async for chunk in self._llm.astream([
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ]):
                if chunk.content:
                    yield chunk.content
        except Exception as e:
            logger.error(f"Streaming generation failed: {e}")
            yield json.dumps(_INSUFFICIENT.model_dump())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_response(raw: str) -> AnswerResult:
    """Parse the LLM JSON response into an AnswerResult."""
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


def _build_user_message(
    query: str,
    context: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Build the user message, optionally including conversation history.

    When history is present, uses a template that includes the
    conversation context so the LLM can maintain continuity.
    """
    if history:
        lines: list[str] = []
        for msg in history[-SETTINGS.QUERY_HISTORY_MAX_TURNS * 2:]:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "").strip()
            if content:
                # Truncate long assistant messages to save tokens
                if role == "Assistant" and len(content) > 500:
                    content = content[:500] + "..."
                lines.append(f"{role}: {content}")
        history_text = "\n".join(lines)
        if history_text.strip():
            return _USER_TEMPLATE_WITH_HISTORY.format(
                history=history_text,
                context=context,
                query=query,
            )
    return _USER_TEMPLATE.format(context=context, query=query)
