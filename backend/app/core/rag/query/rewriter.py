from __future__ import annotations

import asyncio
import re

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.core.config import get_settings
from app.core.utils import singleton, normalize_numbers
from app.core.logger import setup_logger

logger = setup_logger(__name__)

SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a query rewriter for a document search system.

Your job is to rewrite the user's LATEST QUESTION into a standalone, self-contained search query
that does NOT rely on conversation history to be understood.

Rules:
1. Resolve ALL pronouns (it, they, this, that, these, those, its) and references
   (the invoice, the order, the customer, that one) using the conversation history.
2. Preserve the user's original intent — do NOT add information they didn't ask about.
3. Preserve ALL specific identifiers (invoice numbers, order IDs, amounts, dates) exactly.
4. Normalize numbers: remove thousand separators (10,248 → 10248).
5. If the query is ALREADY self-contained (no unresolved references), return it unchanged.
6. Output ONLY the rewritten query — no explanation, no quotes, no prefix.

Examples:
  History: "Tell me about invoice 10248" → "Invoice 10248 has a total of $440"
  Query: "who are the customers of it?"
  Output: who are the customers of invoice 10248

  History: "What products are in order 10535?" → "Order 10535 contains ..."
  Query: "what's the total price?"
  Output: what is the total price of order 10535

  History: (none)
  Query: "list all invoices for customer MEREP"
  Output: list all invoices for customer MEREP"""

_USER_TEMPLATE = """CONVERSATION HISTORY:
{history}

LATEST QUESTION: {query}

Rewrite the latest question as a standalone search query:"""


# ---------------------------------------------------------------------------
# Rewriter
# ---------------------------------------------------------------------------

class QueryRewriter:
    """Rewrites follow-up queries into standalone search queries.

    Uses a lightweight LLM call (gpt-4o-mini) to resolve pronouns
    and coreferences from conversation history.

    Args:
        model: OpenAI model name.
        temperature: Sampling temperature (low for determinism).
        api_key: OpenAI API key.
        max_history_turns: Maximum conversation turns to include.
    """

    def __init__(
        self,
        model: str,
        temperature: float,
        api_key: str,
        max_history_turns: int = 10,
    ) -> None:
        self._llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key,
        )
        self._max_turns = max_history_turns
        logger.info(f"Initialised query rewriter: model={model}")

    async def rewrite(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Rewrite a query using conversation history.

        Returns the original query unchanged when:
          - History is empty or None (first message).
          - Rewriting is disabled in config.
          - The LLM call fails (graceful degradation).

        Args:
            query: The user's current question.
            history: List of ``{"role": "user"|"assistant", "content": "..."}``
                     dicts representing the conversation so far.

        Returns:
            str: Standalone, self-contained query.
        """
        if not query or not query.strip():
            return query

        # No history → query is already standalone
        if not history:
            return normalize_numbers(query.strip())

        # Trim to max turns (most recent)
        trimmed = history[-self._max_turns * 2:]

        # Format history for the prompt
        history_text = self._format_history(trimmed)
        if not history_text.strip():
            return normalize_numbers(query.strip())

        user_message = _USER_TEMPLATE.format(
            history=history_text,
            query=query.strip(),
        )

        try:
            response = await asyncio.wait_for(
                self._llm.ainvoke([
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(content=user_message),
                ]),
                timeout=SETTINGS.API_TIMEOUT_S,
            )
            rewritten = response.content.strip()

            # Sanity check: if the LLM returned empty or something weird
            if not rewritten or len(rewritten) > 2000:
                logger.warning("Query rewriter returned unusable output, using original")
                return normalize_numbers(query.strip())

            # Strip any quotes the LLM might wrap around the output
            rewritten = rewritten.strip('"\'')

            logger.info(f"Query rewritten: '{query.strip()[:50]}' → '{rewritten[:50]}'")
            return normalize_numbers(rewritten)

        except asyncio.TimeoutError:
            logger.warning("Query rewrite timed out, using original query")
            return normalize_numbers(query.strip())
        except Exception as e:
            logger.warning(f"Query rewrite failed: {e}, using original query")
            return normalize_numbers(query.strip())

    @staticmethod
    def _format_history(history: list[dict[str, str]]) -> str:
        """Format history messages into a readable block."""
        lines: list[str] = []
        for msg in history:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "").strip()
            if content:
                # Truncate very long assistant responses to save tokens
                if role == "Assistant" and len(content) > 500:
                    content = content[:500] + "..."
                lines.append(f"{role}: {content}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entity ID extraction
# ---------------------------------------------------------------------------

# Patterns that indicate an entity identifier worth doing exact-ID search for.
# Works for invoices, orders, POs, shipping references, etc.
_ENTITY_ID_PATTERN = re.compile(r"\b\d{4,}\b")

# More specific patterns for structured entity references
_NAMED_ENTITY_PATTERN = re.compile(
    r"(?:invoice|order|po|purchase\s*order|shipping|delivery|receipt|ticket|case)"
    r"\s*(?:number|#|id|no\.?)?\s*:?\s*([A-Z]*-?\d{3,})",
    re.IGNORECASE,
)


def extract_entity_ids(text: str) -> list[str]:
    """Extract numeric entity identifiers from a query.

    Finds invoice numbers, order IDs, and similar numeric identifiers
    that warrant a direct SQL lookup (entity-aware retrieval).

    Returns up to ``ENTITY_SEARCH_MAX_IDS`` unique IDs, prioritising
    named references (e.g. "invoice 10248") over bare numbers.

    Args:
        text: Query text (ideally already rewritten to be standalone).

    Returns:
        list[str]: Unique entity ID strings, e.g. ``["10248", "10535"]``.
    """
    max_ids = SETTINGS.ENTITY_SEARCH_MAX_IDS

    # Priority 1: Named entity references ("invoice 10248")
    named = [m.group(1) for m in _NAMED_ENTITY_PATTERN.finditer(text)]

    # Priority 2: Bare numeric IDs (4+ digits)
    bare = [m.group(0) for m in _ENTITY_ID_PATTERN.finditer(text)]

    # Deduplicate preserving order, named first
    seen: set[str] = set()
    result: list[str] = []
    for eid in named + bare:
        if eid not in seen:
            seen.add(eid)
            result.append(eid)
        if len(result) >= max_ids:
            break

    return result


# ---------------------------------------------------------------------------
# Factory + convenience function
# ---------------------------------------------------------------------------

@singleton
def get_rewriter() -> QueryRewriter:
    """Return the shared query rewriter instance (lazily initialised)."""
    settings = get_settings()
    model = settings.QUERY_REWRITE_MODEL or settings.OPENAI_QUERY_MODEL
    return QueryRewriter(
        model=model,
        temperature=0.0,
        api_key=settings.OPENAI_API_KEY,
        max_history_turns=settings.QUERY_HISTORY_MAX_TURNS,
    )


async def rewrite_query(
    query: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Convenience wrapper — delegates to the shared rewriter.

    When rewriting is disabled in config, returns the original query
    (with number normalisation) without making an LLM call.
    """
    if not SETTINGS.QUERY_REWRITE_ENABLED:
        return normalize_numbers(query.strip()) if query else query
    return await get_rewriter().rewrite(query, history)
