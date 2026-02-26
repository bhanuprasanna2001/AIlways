"""Verification graph — Adaptive Corrective-RAG for statement verification.

Implements an adaptive retrieval pattern that classifies statements
before choosing a retrieval strategy:

    classify -> route
        -> point:     retrieve(top_k) -> grade -> [retry?] -> synthesise -> END
        -> aggregate: aggregate_fast(SQL) -> END (optional fallback: retrieve -> synthesise)

Key design decisions:
  - Aggregate queries ("all invoices from July 2016") use a deterministic
    SQL fast path for exact counts and low latency.
  - Point queries use the corrective-RAG loop with transform + retry.
  - Classification is rule-based (no LLM call) for zero latency.
"""

from __future__ import annotations

import asyncio
import json
import re
import hashlib
import time
from typing import Literal, TypedDict
from uuid import UUID

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from sqlmodel import select, col
from sqlalchemy import func

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
from app.core.copilot.classification import classify_query_type, infer_aggregate_intent
from app.core.copilot.filters import (
    parse_document_type,
    parse_date_range,
    parse_customer_id,
    build_filter_description,
)
from app.core.config import get_settings
from app.core.logger import setup_logger
from app.db import get_db_session
from app.db.models.chunk import Chunk
from app.db.models.document import Document
from app.db.models.vault import Vault
from app.core.tools.redis import get_redis_client

logger = setup_logger(__name__)

SETTINGS = get_settings()


def classify_statement(text: str) -> Literal["point", "aggregate"]:
    """Classify a statement as point or aggregate. Rule-based, zero latency."""
    qtype = classify_query_type(text)
    if qtype in ("aggregate", "compute"):
        return "aggregate"
    return "point"


_DOC_INVENTORY_RE = re.compile(
    r"\b(?:types of documents|what documents|what do we have|documents available)\b",
    re.IGNORECASE,
)


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
    aggregate_fallback: bool
    verification_path: str
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
        "verification_path": "crag",
    }


async def aggregate_fast_node(state: VerificationState) -> dict:
    """Deterministic aggregate verification using SQL + metadata evidence.

    Avoids LLM calls for speed and exactness. If metadata is incomplete,
    returns unverifiable unless fallback is explicitly enabled.
    """
    statement = state["statement_text"]
    vault_id = UUID(state["vault_id"])

    if not SETTINGS.COPILOT.AGGREGATE_FASTPATH_ENABLED:
        if SETTINGS.COPILOT.AGGREGATE_FALLBACK_ENABLED:
            return {"aggregate_fallback": True}
        return _unverifiable_response("Aggregate fast path is disabled.", "aggregate_fast")

    intent = infer_aggregate_intent(statement)
    doc_type = parse_document_type(statement)
    date_from, date_to = parse_date_range(statement)
    customer_id = parse_customer_id(statement)

    has_filters = bool(doc_type or date_from or customer_id)
    if not has_filters:
        if _DOC_INVENTORY_RE.search(statement):
            return await _document_type_inventory_verdict(vault_id)
        if SETTINGS.COPILOT.AGGREGATE_FALLBACK_ENABLED:
            return {"aggregate_fallback": True}
        return _unverifiable_response(
            "No structured filters could be parsed from the statement.",
            "aggregate_fast",
        )

    gaps = await _aggregate_metadata_gaps(
        vault_id=vault_id,
        doc_type=doc_type,
        date_from=date_from,
        date_to=date_to,
        customer_id=customer_id,
        intent=intent,
    )
    if gaps and SETTINGS.COPILOT.AGGREGATE_REQUIRE_COMPLETE_METADATA:
        if SETTINGS.COPILOT.AGGREGATE_FALLBACK_ENABLED:
            return {"aggregate_fallback": True}
        return _unverifiable_response(
            _format_metadata_gap_explanation(gaps, doc_type, date_from, date_to, customer_id),
            "aggregate_fast",
        )
    if gaps and intent in ("sum", "average"):
        if SETTINGS.COPILOT.AGGREGATE_FALLBACK_ENABLED:
            return {"aggregate_fallback": True}
        return _unverifiable_response(
            _format_metadata_gap_explanation(gaps, doc_type, date_from, date_to, customer_id),
            "aggregate_fast",
        )

    list_limit = max(
        SETTINGS.COPILOT.AGGREGATE_LIST_MAX_DOCS,
        SETTINGS.COPILOT.AGGREGATE_EVIDENCE_MAX_DOCS,
    )
    docs, total_count = await _fetch_aggregate_documents(
        vault_id=vault_id,
        doc_type=doc_type,
        date_from=date_from,
        date_to=date_to,
        customer_id=customer_id,
        limit=list_limit,
    )

    filter_desc = build_filter_description(doc_type, date_from, date_to, customer_id)
    logger.info(
        "Verification aggregate fast: statement='%s', filters='%s', count=%d",
        statement[:50],
        filter_desc,
        total_count,
    )

    if total_count == 0:
        explanation = (
            f"No documents found matching: {filter_desc}.\n"
            "Count: 0"
        )
        return {
            "verdict": "supported",
            "confidence": 1.0,
            "explanation": explanation,
            "evidence": [],
            "aggregate_fallback": False,
            "verification_path": "aggregate_fast",
        }

    if intent in ("sum", "average"):
        total_sum = await _sum_total_price(
            vault_id=vault_id,
            doc_type=doc_type,
            date_from=date_from,
            date_to=date_to,
            customer_id=customer_id,
        )
        if total_sum is None:
            if SETTINGS.COPILOT.AGGREGATE_FALLBACK_ENABLED:
                return {"aggregate_fallback": True}
            return _unverifiable_response(
                "One or more documents are missing total prices for this query.",
                "aggregate_fast",
            )

        avg = (total_sum / total_count) if total_count else 0.0
        value = avg if intent == "average" else total_sum
        label = "Average Total" if intent == "average" else "Total Sum"

        explanation = _format_aggregate_explanation(
            filter_desc=filter_desc,
            total_count=total_count,
            docs=docs,
            value=value,
            value_label=label,
        )
    else:
        explanation = _format_aggregate_explanation(
            filter_desc=filter_desc,
            total_count=total_count,
            docs=docs,
            value=None,
            value_label=None,
        )

    evidence = await _build_aggregate_evidence(
        vault_id=vault_id,
        docs=docs,
        max_docs=SETTINGS.COPILOT.AGGREGATE_EVIDENCE_MAX_DOCS,
    )

    return {
        "verdict": "supported",
        "confidence": 1.0,
        "explanation": explanation,
        "evidence": evidence,
        "aggregate_fallback": False,
        "verification_path": "aggregate_fast",
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
            "verification_path": "aggregate_fallback",
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
        "verification_path": "aggregate_fallback",
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
        return "aggregate_fast"
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


def route_aggregate_fast(state: VerificationState) -> str:
    """Route aggregate fast-path to END or fallback retrieval."""
    if state.get("aggregate_fallback"):
        return "fallback"
    return "done"


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
            -> "aggregate_fast" (aggregate):
                aggregate_fast -> [route_aggregate_fast]
                    -> done: END
                    -> fallback: aggregate_retrieve -> synthesise -> END
    """
    graph = StateGraph(VerificationState)

    # Add nodes
    graph.add_node("classify", classify_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("aggregate_fast", aggregate_fast_node)
    graph.add_node("aggregate_retrieve", aggregate_retrieve_node)
    graph.add_node("grade", grade_node)
    graph.add_node("transform", transform_node)
    graph.add_node("synthesise", synthesise_node)

    # Entry: classify first
    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route_by_type,
        {"retrieve": "retrieve", "aggregate_fast": "aggregate_fast"},
    )

    # Point path: retrieve -> grade -> [retry?] -> synthesise
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        should_retry,
        {"transform": "transform", "synthesise": "synthesise"},
    )
    graph.add_edge("transform", "retrieve")

    # Aggregate path: deterministic fast path, optional fallback to LLM
    graph.add_conditional_edges(
        "aggregate_fast",
        route_aggregate_fast,
        {"done": END, "fallback": "aggregate_retrieve"},
    )
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
    vault_updated_at: str | None = None,
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
    start = time.monotonic()
    graph = get_verification_graph()

    cache_key: str | None = None
    if SETTINGS.COPILOT.VERIFICATION_CACHE_ENABLED:
        cache_key = await _build_verification_cache_key(
            vault_id=vault_id,
            statement_text=statement.text,
            vault_updated_at=vault_updated_at,
        )
        cached = await _get_cached_verdict(cache_key, statement)
        if cached:
            cached.latency_ms = int((time.monotonic() - start) * 1000)
            cached.verification_path = "cache"
            cached.cache_hit = True
            logger.info("Verification cache hit: '%s'", statement.text[:60])
            return cached

    initial_state: VerificationState = {
        "statement_text": statement.text,
        "statement_id": statement.id,
        "vault_id": str(vault_id),
        "statement_type": "point",  # classify_node will update this
        "search_query": "",
        "search_results": [],
        "search_attempts": 0,
        "is_relevant": False,
        "aggregate_fallback": False,
        "verification_path": "crag",
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

        latency_ms = int((time.monotonic() - start) * 1000)
        verdict_obj = Verdict(
            claim_id=statement.id,
            claim_text=statement.text,
            verdict=result.get("verdict", "unverifiable"),
            confidence=result.get("confidence", 0.0),
            explanation=result.get("explanation", ""),
            evidence=evidence_list,
            verification_path=result.get("verification_path", "crag"),
            latency_ms=latency_ms,
            cache_hit=False,
        )
        if cache_key and _is_cacheable_verdict(verdict_obj):
            await _store_cached_verdict(cache_key, verdict_obj)
        return verdict_obj

    except asyncio.TimeoutError:
        logger.warning(f"Verification graph timed out for: {statement.text[:50]}")
        return Verdict(
            claim_id=statement.id,
            claim_text=statement.text,
            verdict="unverifiable",
            confidence=0.0,
            explanation="Verification timed out.",
            verification_path="crag",
            latency_ms=int((time.monotonic() - start) * 1000),
            cache_hit=False,
        )
    except Exception as e:
        logger.error(f"Verification graph failed for: {statement.text[:50]}: {e}")
        return Verdict(
            claim_id=statement.id,
            claim_text=statement.text,
            verdict="unverifiable",
            confidence=0.0,
            explanation=f"Verification failed: {e}",
            verification_path="crag",
            latency_ms=int((time.monotonic() - start) * 1000),
            cache_hit=False,
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


def _unverifiable_response(reason: str, verification_path: str | None = None) -> dict:
    """Return a standardized unverifiable response."""
    response = {
        "verdict": "unverifiable",
        "confidence": 0.0,
        "explanation": reason,
        "evidence": [],
        "aggregate_fallback": False,
    }
    if verification_path:
        response["verification_path"] = verification_path
    return response


def _normalize_statement_for_cache(text: str) -> str:
    normalized = normalize_numbers(text.lower())
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return " ".join(normalized.split())


async def _get_vault_cache_version(vault_id: UUID) -> str:
    async with get_db_session() as db:
        stmt = select(Vault.updated_at).where(Vault.id == vault_id)
        result = await db.execute(stmt)
        updated_at = result.scalar_one_or_none()
    return updated_at.isoformat() if updated_at else "unknown"


async def _build_verification_cache_key(
    *,
    vault_id: UUID,
    statement_text: str,
    vault_updated_at: str | None,
) -> str:
    version = vault_updated_at or await _get_vault_cache_version(vault_id)
    normalized = _normalize_statement_for_cache(statement_text)
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"verdict_cache:{vault_id}:{version}:{digest}"


async def _get_cached_verdict(
    cache_key: str,
    statement: Statement,
) -> Verdict | None:
    try:
        client = await get_redis_client()
        raw = await client.get(cache_key)
        if not raw:
            return None
        data = json.loads(raw)
    except Exception:
        return None

    evidence_list: list[Evidence] = []
    for e in data.get("evidence", []) or []:
        try:
            evidence_list.append(Evidence(**e))
        except Exception:
            continue

    return Verdict(
        claim_id=statement.id,
        claim_text=statement.text,
        verdict=data.get("verdict", "unverifiable"),
        confidence=float(data.get("confidence", 0.0)),
        explanation=data.get("explanation", ""),
        evidence=evidence_list,
    )


async def _store_cached_verdict(
    cache_key: str,
    verdict: Verdict,
) -> None:
    try:
        client = await get_redis_client()
        payload = {
            "verdict": verdict.verdict,
            "confidence": verdict.confidence,
            "explanation": verdict.explanation,
            "evidence": [
                e.model_dump() if hasattr(e, "model_dump") else e
                for e in (verdict.evidence or [])
            ],
        }
        await client.setex(
            cache_key,
            SETTINGS.COPILOT.VERIFICATION_CACHE_TTL_S,
            json.dumps(payload),
        )
    except Exception:
        return


def _is_cacheable_verdict(verdict: Verdict) -> bool:
    explanation = (verdict.explanation or "").lower()
    if explanation.startswith("verification timed out"):
        return False
    if explanation.startswith("verification failed"):
        return False
    return True


async def _document_type_inventory_verdict(vault_id: UUID) -> dict:
    """Return a summary of document types available in the vault."""
    async with get_db_session() as db:
        stmt = (
            select(Document.document_type, func.count().label("cnt"))
            .where(Document.vault_id == vault_id)
            .where(Document.deleted_at.is_(None))  # type: ignore[union-attr]
            .where(Document.status == "active")
            .group_by(Document.document_type)
            .order_by(Document.document_type)
        )
        result = await db.execute(stmt)
        rows = result.all()

    if not rows:
        return _unverifiable_response("No documents found in the vault.", "aggregate_fast")

    lines = ["Available document types in this vault:"]
    for doc_type, cnt in rows:
        label = doc_type or "unknown"
        lines.append(f"- {label}: {cnt}")

    return {
        "verdict": "supported",
        "confidence": 1.0,
        "explanation": "\n".join(lines),
        "evidence": [],
        "aggregate_fallback": False,
        "verification_path": "aggregate_fast",
    }


def _base_filters(
    vault_id: UUID,
    doc_type: str | None,
    customer_id: str | None,
) -> list:
    filters = [
        Document.vault_id == vault_id,
        Document.deleted_at.is_(None),  # type: ignore[union-attr]
        Document.status == "active",
    ]
    if doc_type:
        filters.append(Document.document_type == doc_type)
    if customer_id:
        filters.append(col(Document.customer_id).ilike(customer_id))
    return filters


def _apply_date_filters(
    filters: list,
    date_from,
    date_to,
) -> list:
    if date_from:
        filters.append(Document.order_date >= date_from)
    if date_to:
        filters.append(Document.order_date <= date_to)
    return filters


async def _count_documents(filters: list) -> int:
    async with get_db_session() as db:
        stmt = select(func.count()).select_from(Document).where(*filters)
        result = await db.execute(stmt)
        return result.scalar() or 0


async def _aggregate_metadata_gaps(
    *,
    vault_id: UUID,
    doc_type: str | None,
    date_from,
    date_to,
    customer_id: str | None,
    intent: str,
) -> list[str]:
    gaps: list[str] = []
    base_filters = _base_filters(vault_id, doc_type, customer_id)

    if date_from or date_to:
        missing_dates = await _count_documents(
            base_filters + [Document.order_date.is_(None)],  # type: ignore[union-attr]
        )
        if missing_dates:
            gaps.append(f"{missing_dates} document(s) missing order date metadata")

    if intent in ("sum", "average"):
        total_filters = _apply_date_filters(base_filters[:], date_from, date_to)
        missing_totals = await _count_documents(
            total_filters + [Document.total_price.is_(None)],  # type: ignore[union-attr]
        )
        if missing_totals:
            gaps.append(f"{missing_totals} document(s) missing total price metadata")

    return gaps


async def _fetch_aggregate_documents(
    *,
    vault_id: UUID,
    doc_type: str | None,
    date_from,
    date_to,
    customer_id: str | None,
    limit: int,
) -> tuple[list[Document], int]:
    filters = _apply_date_filters(
        _base_filters(vault_id, doc_type, customer_id),
        date_from,
        date_to,
    )
    async with get_db_session() as db:
        count_stmt = select(func.count()).select_from(Document).where(*filters)
        count_result = await db.execute(count_stmt)
        total_count = count_result.scalar() or 0

        docs: list[Document] = []
        if limit > 0:
            doc_stmt = (
                select(Document)
                .where(*filters)
                .order_by(Document.order_date, Document.entity_id, Document.original_filename)
                .limit(limit)
            )
            doc_result = await db.execute(doc_stmt)
            docs = doc_result.scalars().all()

    return docs, total_count


async def _sum_total_price(
    *,
    vault_id: UUID,
    doc_type: str | None,
    date_from,
    date_to,
    customer_id: str | None,
) -> float | None:
    filters = _apply_date_filters(
        _base_filters(vault_id, doc_type, customer_id),
        date_from,
        date_to,
    )
    async with get_db_session() as db:
        stmt = select(func.sum(Document.total_price)).where(*filters)
        result = await db.execute(stmt)
        total = result.scalar()
    return float(total) if total is not None else None


def _format_metadata_gap_explanation(
    gaps: list[str],
    doc_type: str | None,
    date_from,
    date_to,
    customer_id: str | None,
) -> str:
    filter_desc = build_filter_description(doc_type, date_from, date_to, customer_id)
    lines = [
        f"Cannot verify an exact result for: {filter_desc}.",
        "Missing metadata:",
    ]
    lines.extend(f"- {gap}" for gap in gaps)
    lines.append("Re-ingest documents or run metadata backfill to fill the gaps.")
    return "\n".join(lines)


def _format_doc_line(doc: Document) -> str:
    line = f"{doc.original_filename}"
    details: list[str] = []
    if doc.order_date:
        details.append(f"Date: {doc.order_date}")
    if doc.customer_id:
        details.append(f"Customer: {doc.customer_id}")
    if doc.total_price is not None:
        details.append(f"Total: ${doc.total_price:,.2f}")
    if doc.entity_id:
        details.append(f"ID: {doc.entity_id}")
    if details:
        line += " — " + ", ".join(details)
    return line


def _format_aggregate_explanation(
    *,
    filter_desc: str,
    total_count: int,
    docs: list[Document],
    value: float | None,
    value_label: str | None,
) -> str:
    lines = [f"Found {total_count} documents matching: {filter_desc}"]
    if value_label and value is not None:
        lines.append(f"{value_label}: ${value:,.2f}")
    lines.append(f"Count: {total_count}")

    if docs:
        lines.append("")
        lines.append("Documents:")
        list_max = SETTINGS.COPILOT.AGGREGATE_LIST_MAX_DOCS
        for i, doc in enumerate(docs[:list_max], 1):
            lines.append(f"{i}. {_format_doc_line(doc)}")
        if total_count > list_max:
            lines.append(f"... (showing first {list_max} of {total_count})")

    return "\n".join(lines)


def _format_doc_metadata_quote(doc: Document) -> str:
    lines = [f"Document: {doc.original_filename}"]
    if doc.document_type:
        lines.append(f"Type: {doc.document_type}")
    if doc.entity_id:
        lines.append(f"ID: {doc.entity_id}")
    if doc.order_date:
        lines.append(f"Order Date: {doc.order_date}")
    if doc.customer_id:
        lines.append(f"Customer ID: {doc.customer_id}")
    if doc.total_price is not None:
        lines.append(f"Total Price: ${doc.total_price:,.2f}")
    return "\n".join(lines)


async def _build_aggregate_evidence(
    *,
    vault_id: UUID,
    docs: list[Document],
    max_docs: int,
) -> list[dict]:
    if not docs or max_docs <= 0:
        return []

    doc_subset = docs[:max_docs]
    doc_ids = [d.id for d in doc_subset]

    chunk_map: dict[UUID, Chunk] = {}
    async with get_db_session() as db:
        stmt = (
            select(Chunk)
            .where(Chunk.doc_id.in_(doc_ids))
            .where(Chunk.vault_id == vault_id)
            .where(Chunk.is_deleted == False)  # noqa: E712
            .where(Chunk.chunk_type == "metadata")
            .order_by(Chunk.doc_id, Chunk.chunk_index)
        )
        result = await db.execute(stmt)
        chunks = result.scalars().all()

    for chunk in chunks:
        if chunk.doc_id not in chunk_map:
            chunk_map[chunk.doc_id] = chunk

    evidence: list[dict] = []
    for doc in doc_subset:
        chunk = chunk_map.get(doc.id)
        if chunk:
            quote = chunk.content_with_header or chunk.content
            section = chunk.section_heading
            page = chunk.page_number
        else:
            quote = _format_doc_metadata_quote(doc)
            section = None
            page = None
        evidence.append({
            "doc_title": doc.original_filename,
            "section": section,
            "page": page,
            "quote": quote,
            "relevance_score": 1.0,
        })

    return evidence


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
