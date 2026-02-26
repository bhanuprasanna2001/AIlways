"""Verification graph — Adaptive Corrective-RAG for statement verification.

Implements an adaptive retrieval pattern that classifies statements
before choosing a retrieval strategy:

    classify -> route
        -> point:     retrieve(top_k) -> grade -> [retry?] -> synthesise -> END
        -> aggregate:  exhaustive_retrieve(top_k=30) -> synthesise -> END

Key design decisions:
  - Aggregate queries ("all invoices from July 2016") use high top_k
    to capture ALL matching documents, not just a sample.
  - Point queries use the corrective-RAG loop with transform + retry.
  - Classification is rule-based (no LLM call) for zero latency.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Literal, TypedDict
from uuid import UUID

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from sqlmodel import select

from app.core.copilot.base import Statement, Evidence, Verdict
from app.core.copilot.prompts import (
    GRADING_SYSTEM,
    GRADING_USER,
    TRANSFORM_SYSTEM,
    TRANSFORM_USER,
    VERIFICATION_SYSTEM,
    VERIFICATION_USER,
)
from app.core.rag.embedding import get_embedder
from app.core.rag.retrieval import hybrid_search, entity_id_search
from app.core.rag.retrieval.base import SearchResult, build_retrieval_context
from app.core.utils import normalize_numbers
from app.core.config import get_settings
from app.core.logger import setup_logger
from app.db import get_db_session
from app.db.models.chunk import Chunk
from app.db.models.document import Document

logger = setup_logger(__name__)

SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Statement classification — rule-based, zero latency
# ---------------------------------------------------------------------------

_AGGREGATE_PATTERNS = [
    r"\ball\b.*\b(?:invoice|order|report|document|item|product|shipping|purchase)",
    r"\btotal\b.*\b(?:price|cost|amount|value|number|count|quantity|items)\b.*\b(?:of all|from|in|for|during)\b",
    r"\bhow many\b",
    r"\blist\b.*\b(?:every|all|each)\b",
    r"\bevery\b",
    r"\b(?:invoices?|orders?|reports?|documents?|items?|products?)\b.*\b(?:from|in|of|during|for)\b.*\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|may|june|july|august|september|october|november|december|20\d{2}|q[1-4])\b",
    r"\btypes of documents\b",
    r"\bwhat.*(?:do we have|exist|available)\b",
    r"\b(?:stock|inventory)\b.*\breport",
    r"\b(?:exist|available)\b.*\b(?:in the vault|in the database)\b",
]

_AGGREGATE_RE = re.compile("|".join(_AGGREGATE_PATTERNS), re.IGNORECASE)


def classify_statement(text: str) -> Literal["point", "aggregate"]:
    """Classify a statement as point or aggregate. Rule-based, zero latency."""
    normalized = normalize_numbers(text.lower())
    if _AGGREGATE_RE.search(normalized):
        return "aggregate"
    return "point"


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class VerificationState(TypedDict):
    """State flowing through the verification graph."""

    # Inputs (set before invocation)
    statement_text: str
    statement_id: str
    vault_id: str

    # Classification
    statement_type: str  # "point" | "aggregate"

    # Mutable state (updated by nodes)
    search_query: str
    search_results: list[dict]  # serialised SearchResult dicts
    search_attempts: int
    is_relevant: bool
    verdict: str
    confidence: float
    explanation: str
    evidence: list[dict]


# ---------------------------------------------------------------------------
# LLM instances (lazy, module-level for reuse)
# ---------------------------------------------------------------------------

_grading_llm: ChatOpenAI | None = None
_transform_llm: ChatOpenAI | None = None
_verdict_llm: ChatOpenAI | None = None


def _get_grading_llm() -> ChatOpenAI:
    global _grading_llm
    if _grading_llm is None:
        model = SETTINGS.COPILOT.GRADING_MODEL or SETTINGS.OPENAI_QUERY_MODEL
        _grading_llm = ChatOpenAI(
            model=model,
            temperature=SETTINGS.COPILOT.GRADING_TEMPERATURE,
            api_key=SETTINGS.OPENAI_API_KEY,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
    return _grading_llm


def _get_transform_llm() -> ChatOpenAI:
    global _transform_llm
    if _transform_llm is None:
        model = SETTINGS.COPILOT.GRADING_MODEL or SETTINGS.OPENAI_QUERY_MODEL
        _transform_llm = ChatOpenAI(
            model=model,
            temperature=0.3,
            api_key=SETTINGS.OPENAI_API_KEY,
        )
    return _transform_llm


def _get_verdict_llm() -> ChatOpenAI:
    global _verdict_llm
    if _verdict_llm is None:
        model = SETTINGS.COPILOT.VERIFICATION_MODEL or SETTINGS.OPENAI_QUERY_MODEL
        _verdict_llm = ChatOpenAI(
            model=model,
            temperature=SETTINGS.COPILOT.VERIFICATION_TEMPERATURE,
            api_key=SETTINGS.OPENAI_API_KEY,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
    return _verdict_llm


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

async def classify_node(state: VerificationState) -> dict:
    """Classify the statement type to choose the right retrieval strategy."""
    stype = classify_statement(state["statement_text"])
    logger.info(
        "Verification classify: statement='%s', type=%s",
        state["statement_text"][:50], stype,
    )
    return {"statement_type": stype}


async def retrieve_node(state: VerificationState) -> dict:
    """Retrieve documents for POINT queries using hybrid search + entity lookup.

    On first attempt, builds an enriched search query with entity-ID
    boosting. On subsequent attempts, uses the transformed query from
    ``transform_node``.
    """
    statement = state["statement_text"]
    vault_id = UUID(state["vault_id"])
    attempts = state.get("search_attempts", 0)
    top_k = SETTINGS.COPILOT.VERIFICATION_TOP_K

    # Build search query
    if attempts == 0:
        # First attempt: use statement + entity boosting
        search_text = normalize_numbers(statement)
        entity_ids = re.findall(r"\b\d{4,}\b", search_text)
        if entity_ids:
            id_boost = " ".join(f"ID {eid}" for eid in entity_ids[:3])
            search_text = f"{id_boost} {search_text}"
    else:
        # Retry: use the transformed query
        search_text = state.get("search_query", normalize_numbers(statement))

    # Entity-ID pre-filter (exact SQL lookup)
    entity_ids = re.findall(r"\b\d{4,}\b", normalize_numbers(statement))
    exact_results: list[SearchResult] = []
    if entity_ids:
        async with get_db_session() as db:
            exact_results = await entity_id_search(
                entity_ids[:3], vault_id, db,
            )

    # Hybrid search
    embedder = get_embedder()
    query_embedding = await embedder.embed_query(search_text)

    async with get_db_session() as db:
        hybrid_results = await hybrid_search(
            query_text=search_text,
            query_embedding=query_embedding,
            vault_id=vault_id,
            db=db,
            top_k=top_k,
            mmr_lambda=SETTINGS.COPILOT.VERIFICATION_MMR_LAMBDA,
        )

    # Merge: exact-ID hits first, then hybrid (deduplicated)
    if exact_results:
        seen_ids = {r.chunk_id for r in exact_results}
        merged = list(exact_results)
        for r in hybrid_results:
            if r.chunk_id not in seen_ids:
                merged.append(r)
        results = merged[: top_k + len(exact_results)]
    else:
        results = hybrid_results

    logger.info(
        "Verification retrieve (point): statement='%s', attempt=%d, results=%d",
        statement[:50], attempts + 1, len(results),
    )

    return {
        "search_results": [r.model_dump(mode="json") for r in results],
        "search_query": search_text,
        "search_attempts": attempts + 1,
    }


async def aggregate_retrieve_node(state: VerificationState) -> dict:
    """Retrieve documents for AGGREGATE queries using SQL metadata + hybrid fallback.

    Strategy:
      1. Parse date range and document type from the statement.
      2. If metadata fields are available, do a precise SQL query on
         ``Document.order_date``, ``Document.document_type``, etc.
      3. Fetch ALL chunks from matching document IDs.
      4. If SQL yields nothing (metadata not yet backfilled), fall back
         to the original multi-pass hybrid search.
    """
    statement = state["statement_text"]
    vault_id = UUID(state["vault_id"])
    top_k = SETTINGS.COPILOT.VERIFICATION_AGGREGATE_TOP_K

    all_results: list[SearchResult] = []
    seen_ids: set = set()

    def _merge(new_results: list[SearchResult]) -> None:
        for r in new_results:
            if r.chunk_id not in seen_ids:
                seen_ids.add(r.chunk_id)
                all_results.append(r)

    # ------------------------------------------------------------------
    # Step 1: Try SQL metadata filter (precise, zero false-negatives)
    # ------------------------------------------------------------------
    sql_doc_ids = await _sql_metadata_filter(statement, vault_id)

    if sql_doc_ids:
        # Fetch ALL chunks from matched documents
        async with get_db_session() as db:
            chunk_stmt = (
                select(Chunk)
                .where(Chunk.doc_id.in_(sql_doc_ids))
                .where(Chunk.vault_id == vault_id)
                .where(Chunk.is_deleted == False)  # noqa: E712
                .order_by(Chunk.doc_id, Chunk.chunk_index)
            )
            result = await db.execute(chunk_stmt)
            chunks = result.scalars().all()

        for chunk in chunks:
            if chunk.id not in seen_ids:
                seen_ids.add(chunk.id)
                all_results.append(SearchResult(
                    chunk_id=chunk.id,
                    doc_id=chunk.doc_id,
                    content=chunk.content,
                    content_with_header=chunk.content_with_header,
                    score=1.0,
                    section_heading=chunk.section_heading,
                    page_number=chunk.page_number,
                    original_filename=None,
                ))

        logger.info(
            "Verification retrieve (aggregate/SQL): statement='%s', "
            "sql_docs=%d, chunks=%d",
            statement[:50], len(sql_doc_ids), len(all_results),
        )

        return {
            "search_results": [r.model_dump(mode="json") for r in all_results],
            "search_query": statement,
            "search_attempts": 1,
            "is_relevant": len(all_results) > 0,
        }

    # ------------------------------------------------------------------
    # Step 2: Fallback — multi-pass hybrid search (metadata not available)
    # ------------------------------------------------------------------
    logger.info(
        "Verification retrieve (aggregate/hybrid-fallback): statement='%s'",
        statement[:50],
    )

    # Pass 1: Hybrid search with full statement
    search_text = normalize_numbers(statement)
    embedder = get_embedder()
    query_embedding = await embedder.embed_query(search_text)

    async with get_db_session() as db:
        pass1 = await hybrid_search(
            query_text=search_text,
            query_embedding=query_embedding,
            vault_id=vault_id,
            db=db,
            top_k=top_k,
            mmr_lambda=SETTINGS.COPILOT.VERIFICATION_MMR_LAMBDA,
        )
    _merge(pass1)

    # Pass 2: Focused keyword search with extracted date/entity keywords
    date_keywords = _extract_date_keywords(statement)
    entity_keywords = _extract_entity_type_keywords(statement)
    focused_query = " ".join(entity_keywords + date_keywords)

    if focused_query.strip() and focused_query.strip() != search_text.strip():
        focused_embedding = await embedder.embed_query(focused_query)
        async with get_db_session() as db:
            pass2 = await hybrid_search(
                query_text=focused_query,
                query_embedding=focused_embedding,
                vault_id=vault_id,
                db=db,
                top_k=top_k,
                mmr_lambda=SETTINGS.COPILOT.VERIFICATION_MMR_LAMBDA,
            )
        _merge(pass2)

    # Pass 3: Fetch ALL chunks from discovered document IDs
    if all_results:
        doc_ids = list({r.doc_id for r in all_results})[:20]
        async with get_db_session() as db:
            chunk_stmt = (
                select(Chunk)
                .where(Chunk.doc_id.in_(doc_ids))
                .where(Chunk.vault_id == vault_id)
                .where(Chunk.is_deleted == False)  # noqa: E712
                .order_by(Chunk.doc_id, Chunk.chunk_index)
            )
            result = await db.execute(chunk_stmt)
            chunks = result.scalars().all()

        for chunk in chunks:
            if chunk.id not in seen_ids:
                seen_ids.add(chunk.id)
                all_results.append(SearchResult(
                    chunk_id=chunk.id,
                    doc_id=chunk.doc_id,
                    content=chunk.content,
                    content_with_header=chunk.content_with_header,
                    score=0.5,
                    section_heading=chunk.section_heading,
                    page_number=chunk.page_number,
                    original_filename=None,
                ))

    logger.info(
        "Verification retrieve (aggregate/hybrid): statement='%s', results=%d",
        statement[:50], len(all_results),
    )

    return {
        "search_results": [r.model_dump(mode="json") for r in all_results],
        "search_query": search_text,
        "search_attempts": 1,
        "is_relevant": len(all_results) > 0,
    }


async def grade_node(state: VerificationState) -> dict:
    """Grade whether retrieved documents are relevant to the statement.

    Uses a fast LLM call with structured JSON output. The grading
    step is what makes this a *corrective* RAG — if the retrieved
    documents aren't relevant, the graph retries with a transformed query.
    """
    results = state.get("search_results", [])
    if not results:
        return {"is_relevant": False}

    # Build context from results (first 10 chunks for grading)
    context_parts: list[str] = []
    for i, r in enumerate(results[:10], 1):
        content = r.get("content_with_header", r.get("content", ""))
        score = r.get("score", 0.0)
        context_parts.append(f"--- Document Chunk {i} (relevance: {score:.3f}) ---")
        context_parts.append(content)
        context_parts.append("")
    context = "\n".join(context_parts)

    user_message = GRADING_USER.format(
        statement=state["statement_text"],
        context=context,
    )

    try:
        llm = _get_grading_llm()
        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content=GRADING_SYSTEM),
                HumanMessage(content=user_message),
            ]),
            timeout=SETTINGS.API_TIMEOUT_S,
        )
        data = json.loads(response.content)
        relevant = data.get("relevant", False)
    except Exception as e:
        logger.warning(f"Grading failed: {e} — assuming relevant")
        relevant = True

    logger.info(
        f"Verification grade: statement='{state['statement_text'][:50]}', "
        f"relevant={relevant}"
    )
    return {"is_relevant": relevant}


async def transform_node(state: VerificationState) -> dict:
    """Transform the search query for a retry when grading says 'not relevant'.

    Uses a lightweight LLM call to generate a better search query.
    """
    llm = _get_transform_llm()
    user_message = TRANSFORM_USER.format(
        statement=state["statement_text"],
        previous_query=state.get("search_query", state["statement_text"]),
    )

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content=TRANSFORM_SYSTEM),
                HumanMessage(content=user_message),
            ]),
            timeout=SETTINGS.API_TIMEOUT_S,
        )
        new_query = response.content.strip().strip('"\'')
        if new_query and len(new_query) < 500:
            logger.info(
                f"Verification transform: '{state['search_query'][:40]}' "
                f"→ '{new_query[:40]}'"
            )
            return {"search_query": normalize_numbers(new_query)}
    except Exception as e:
        logger.warning(f"Query transform failed: {e}")

    return {}


async def synthesise_node(state: VerificationState) -> dict:
    """Synthesise a verification verdict from the statement and evidence.

    For aggregate queries, passes ALL retrieved chunks (up to 40) so the
    LLM can enumerate every entity. For point queries, limits to 15.
    """
    results = state.get("search_results", [])

    if not results:
        return {
            "verdict": "unverifiable",
            "confidence": 0.0,
            "explanation": "No relevant documents found in the vault.",
            "evidence": [],
        }

    # For aggregate: include more chunks so the LLM sees everything.
    max_chunks = 40 if state.get("statement_type") == "aggregate" else 15
    context_parts: list[str] = []
    for i, r in enumerate(results[:max_chunks], 1):
        content = r.get("content_with_header", r.get("content", ""))
        score = r.get("score", 0.0)
        context_parts.append(f"--- Document Chunk {i} (relevance: {score:.3f}) ---")
        context_parts.append(content)
        context_parts.append("")
    context = "\n".join(context_parts)

    # For aggregate queries, give the LLM a count hint
    statement_text = normalize_numbers(state["statement_text"])
    if state.get("statement_type") == "aggregate":
        doc_ids = {r.get("doc_id", "") for r in results}
        statement_text = (
            f"{statement_text}\n\n"
            f"NOTE: The search returned {len(results)} document chunks from "
            f"{len(doc_ids)} unique documents. List ALL matching documents "
            f"and their details in the explanation."
        )

    user_message = VERIFICATION_USER.format(
        statement=statement_text,
        context=context,
    )

    try:
        llm = _get_verdict_llm()
        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content=VERIFICATION_SYSTEM),
                HumanMessage(content=user_message),
            ]),
            timeout=SETTINGS.API_TIMEOUT_S,
        )
        return _parse_verdict_response(response.content)

    except asyncio.TimeoutError:
        logger.error(f"Verdict synthesis timed out for: {state['statement_text'][:50]}")
        return {
            "verdict": "unverifiable",
            "confidence": 0.0,
            "explanation": "Verification timed out.",
            "evidence": [],
        }
    except Exception as e:
        logger.error(f"Verdict synthesis failed: {e}")
        return {
            "verdict": "unverifiable",
            "confidence": 0.0,
            "explanation": f"Verification failed: {e}",
            "evidence": [],
        }


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------

def route_by_type(state: VerificationState) -> str:
    """Route to the appropriate retrieval strategy based on statement type."""
    if state.get("statement_type") == "aggregate":
        return "aggregate_retrieve"
    return "retrieve"


def should_retry(state: VerificationState) -> str:
    """Decide whether to retry retrieval or proceed to verdict synthesis.

    Returns:
        "transform" if results are not relevant AND we haven't
        exhausted retries; "synthesise" otherwise.
    """
    is_relevant = state.get("is_relevant", True)
    attempts = state.get("search_attempts", 0)
    max_attempts = SETTINGS.COPILOT.VERIFICATION_MAX_SEARCH_ATTEMPTS

    if not is_relevant and attempts < max_attempts:
        return "transform"
    return "synthesise"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_verification_graph() -> StateGraph:
    """Build the Adaptive Corrective-RAG verification graph.

    Flow::

        START -> classify -> [route_by_type]
            -> "retrieve" (point):
                retrieve -> grade -> [should_retry?]
                    -> "transform": transform -> retrieve -> grade -> ...
                    -> "synthesise": synthesise -> END
            -> "aggregate_retrieve" (aggregate):
                aggregate_retrieve -> synthesise -> END
    """
    graph = StateGraph(VerificationState)

    # Add nodes
    graph.add_node("classify", classify_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("aggregate_retrieve", aggregate_retrieve_node)
    graph.add_node("grade", grade_node)
    graph.add_node("transform", transform_node)
    graph.add_node("synthesise", synthesise_node)

    # Entry: classify first
    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route_by_type,
        {"retrieve": "retrieve", "aggregate_retrieve": "aggregate_retrieve"},
    )

    # Point path: retrieve -> grade -> [retry?] -> synthesise
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        should_retry,
        {"transform": "transform", "synthesise": "synthesise"},
    )
    graph.add_edge("transform", "retrieve")

    # Aggregate path: aggregate_retrieve -> synthesise (no grading)
    graph.add_edge("aggregate_retrieve", "synthesise")

    # Terminal
    graph.add_edge("synthesise", END)

    return graph


# Module-level compiled graph (lazy singleton)
_compiled_graph = None


def get_verification_graph():
    """Return the compiled verification graph (lazily built)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_verification_graph().compile()
    return _compiled_graph


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def verify_statement(
    statement: Statement,
    vault_id: UUID,
) -> Verdict:
    """Verify a single statement against vault documents.

    Invokes the LangGraph verification graph which performs
    self-corrective retrieval (retrieve → grade → retry if needed → verdict).

    Args:
        statement: The statement to verify.
        vault_id: Vault to search against.

    Returns:
        Verdict: Verification result with evidence and explanation.
    """
    graph = get_verification_graph()

    initial_state: VerificationState = {
        "statement_text": statement.text,
        "statement_id": statement.id,
        "vault_id": str(vault_id),
        "statement_type": "point",  # classify_node will update this
        "search_query": "",
        "search_results": [],
        "search_attempts": 0,
        "is_relevant": False,
        "verdict": "unverifiable",
        "confidence": 0.0,
        "explanation": "",
        "evidence": [],
    }

    try:
        result = await asyncio.wait_for(
            graph.ainvoke(initial_state),
            timeout=SETTINGS.API_TIMEOUT_S,
        )

        # Parse evidence list
        evidence_list: list[Evidence] = []
        for e in result.get("evidence", []):
            evidence_list.append(Evidence(
                doc_title=e.get("doc_title", "Unknown"),
                section=e.get("section"),
                page=e.get("page"),
                quote=e.get("quote", ""),
                relevance_score=float(e.get("relevance_score", 0.0)),
            ))

        return Verdict(
            claim_id=statement.id,
            claim_text=statement.text,
            verdict=result.get("verdict", "unverifiable"),
            confidence=result.get("confidence", 0.0),
            explanation=result.get("explanation", ""),
            evidence=evidence_list,
        )

    except asyncio.TimeoutError:
        logger.warning(f"Verification graph timed out for: {statement.text[:50]}")
        return Verdict(
            claim_id=statement.id,
            claim_text=statement.text,
            verdict="unverifiable",
            confidence=0.0,
            explanation="Verification timed out.",
        )
    except Exception as e:
        logger.error(f"Verification graph failed for: {statement.text[:50]}: {e}")
        return Verdict(
            claim_id=statement.id,
            claim_text=statement.text,
            verdict="unverifiable",
            confidence=0.0,
            explanation=f"Verification failed: {e}",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "january": "01", "jan": "01",
    "february": "02", "feb": "02",
    "march": "03", "mar": "03",
    "april": "04", "apr": "04",
    "may": "05",
    "june": "06", "jun": "06",
    "july": "07", "jul": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09",
    "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "december": "12", "dec": "12",
}


async def _sql_metadata_filter(
    statement: str,
    vault_id: UUID,
) -> list[UUID]:
    """Use Document metadata fields for precise SQL filtering.

    Parses document_type and date range from the statement, then queries
    the ``documents`` table. Returns a list of matching doc IDs, or an
    empty list if metadata isn't populated or no criteria could be parsed.
    """
    from datetime import date as date_type
    from calendar import monthrange

    text_lower = statement.lower()

    # Parse document type
    doc_type: str | None = None
    type_map = {
        "invoice": "invoice",
        "purchase order": "purchase_order",
        "shipping order": "shipping_order",
        "stock report": "stock_report",
        "inventory report": "stock_report",
    }
    for keyword, dtype in type_map.items():
        if keyword in text_lower:
            doc_type = dtype
            break

    # Parse date range (month+year, year-only, quarter)
    date_start: date_type | None = None
    date_end: date_type | None = None

    year_match = re.search(r"\b(20\d{2})\b", statement)
    year = int(year_match.group(1)) if year_match else None

    if year:
        month_num: int | None = None
        for month_name, m_str in _MONTH_MAP.items():
            if month_name in text_lower:
                month_num = int(m_str)
                break

        q_match = re.search(r"\bq([1-4])\b", text_lower)

        if month_num:
            _, last_day = monthrange(year, month_num)
            date_start = date_type(year, month_num, 1)
            date_end = date_type(year, month_num, last_day)
        elif q_match:
            quarter = int(q_match.group(1))
            start_month = (quarter - 1) * 3 + 1
            end_month = start_month + 2
            _, last_day = monthrange(year, end_month)
            date_start = date_type(year, start_month, 1)
            date_end = date_type(year, end_month, last_day)
        else:
            date_start = date_type(year, 1, 1)
            date_end = date_type(year, 12, 31)

    # Must have at least one filter criterion
    if not doc_type and not date_start:
        return []

    # Build and execute query
    async with get_db_session() as db:
        stmt = select(Document.id).where(
            Document.vault_id == vault_id,
            Document.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        if doc_type:
            stmt = stmt.where(Document.document_type == doc_type)
        if date_start and date_end:
            stmt = stmt.where(Document.order_date >= date_start)
            stmt = stmt.where(Document.order_date <= date_end)

        result = await db.execute(stmt)
        doc_ids = list(result.scalars().all())

    if doc_ids:
        logger.info(
            "SQL metadata filter matched %d documents for: '%s'",
            len(doc_ids), statement[:60],
        )
    return doc_ids


def _extract_date_keywords(text: str) -> list[str]:
    """Extract date-related keywords for focused aggregate search.

    E.g., "July 2016" -> ["July", "2016", "2016-07"]
    """
    keywords: list[str] = []
    text_lower = text.lower()

    year_match = re.search(r"\b(20\d{2})\b", text)
    year = year_match.group(1) if year_match else ""

    for month_name, month_num in _MONTH_MAP.items():
        if month_name in text_lower:
            keywords.append(month_name.capitalize())
            if year:
                keywords.append(f"{year}-{month_num}")
            break

    if year:
        keywords.append(year)

    # Quarter support: Q1 2016 -> 2016-01, 2016-02, 2016-03
    q_match = re.search(r"\bq([1-4])\b", text_lower)
    if q_match and year:
        quarter = int(q_match.group(1))
        start_month = (quarter - 1) * 3 + 1
        for m in range(start_month, start_month + 3):
            keywords.append(f"{year}-{m:02d}")

    return keywords


def _extract_entity_type_keywords(text: str) -> list[str]:
    """Extract entity type keywords for focused aggregate search."""
    keywords: list[str] = []
    text_lower = text.lower()

    entity_types = {
        "invoice": ["invoice", "invoices"],
        "order": ["order", "orders", "purchase order", "purchase orders"],
        "stock report": ["stock report", "stock reports", "inventory report"],
        "shipping": ["shipping", "shipping order", "shipment"],
        "purchase": ["purchase", "purchase order"],
    }

    for canonical, variants in entity_types.items():
        for variant in variants:
            if variant in text_lower:
                keywords.append(canonical)
                break

    return keywords


def _parse_verdict_response(raw: str) -> dict:
    """Parse the LLM JSON response into state updates."""
    try:
        data = json.loads(raw)

        verdict = data.get("verdict", "unverifiable")
        if verdict not in ("supported", "contradicted", "unverifiable"):
            verdict = "unverifiable"

        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        evidence: list[dict] = []
        for e in data.get("evidence", []):
            evidence.append({
                "doc_title": e.get("doc_title", "Unknown"),
                "section": e.get("section"),
                "page": e.get("page"),
                "quote": e.get("quote", ""),
                "relevance_score": float(e.get("relevance_score", 0.0)),
            })

        return {
            "verdict": verdict,
            "confidence": confidence,
            "explanation": data.get("explanation", ""),
            "evidence": evidence,
        }

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse verdict response: {e}")
        return {
            "verdict": "unverifiable",
            "confidence": 0.0,
            "explanation": f"Failed to parse verification result: {e}",
            "evidence": [],
        }
