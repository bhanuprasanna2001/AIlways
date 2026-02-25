"""Verification graph — LangGraph Corrective-RAG for statement verification.

Implements the self-corrective retrieval pattern:

    retrieve → grade_relevance → [relevant?]
        → yes: synthesise_verdict → END
        → no:  transform_query → retrieve (retry, max N attempts)

Each statement gets its own graph invocation. The pipeline runs
multiple invocations concurrently via ``asyncio.gather``.

This replaces the old ``RAGClaimVerifier`` with a LangGraph-based
approach that automatically retries with reformulated queries when
the first retrieval doesn't find relevant documents.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TypedDict
from uuid import UUID

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

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

logger = setup_logger(__name__)

SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class VerificationState(TypedDict):
    """State flowing through the verification graph."""

    # Inputs (set before invocation)
    statement_text: str
    statement_id: str
    vault_id: str

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

async def retrieve_node(state: VerificationState) -> dict:
    """Retrieve documents from the vault using hybrid search + entity lookup.

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
        f"Verification retrieve: statement='{statement[:50]}', "
        f"attempt={attempts + 1}, results={len(results)}"
    )

    return {
        "search_results": [r.model_dump(mode="json") for r in results],
        "search_query": search_text,
        "search_attempts": attempts + 1,
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

    # Build context from results
    context_parts: list[str] = []
    for i, r in enumerate(results, 1):
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

    This is the final node — produces the verdict (supported/contradicted/
    unverifiable) with confidence, explanation, and evidence quotes.
    """
    results = state.get("search_results", [])

    if not results:
        return {
            "verdict": "unverifiable",
            "confidence": 0.0,
            "explanation": "No relevant documents found in the vault.",
            "evidence": [],
        }

    # Build context
    context_parts: list[str] = []
    for i, r in enumerate(results, 1):
        content = r.get("content_with_header", r.get("content", ""))
        score = r.get("score", 0.0)
        context_parts.append(f"--- Document Chunk {i} (relevance: {score:.3f}) ---")
        context_parts.append(content)
        context_parts.append("")
    context = "\n".join(context_parts)

    user_message = VERIFICATION_USER.format(
        statement=normalize_numbers(state["statement_text"]),
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
# Conditional edge
# ---------------------------------------------------------------------------

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
    """Build the Corrective-RAG verification graph.

    Flow::

        START → retrieve → grade → [should_retry?]
            → "transform": transform_query → retrieve → grade → ...
            → "synthesise": synthesise_verdict → END
    """
    graph = StateGraph(VerificationState)

    # Add nodes
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("grade", grade_node)
    graph.add_node("transform", transform_node)
    graph.add_node("synthesise", synthesise_node)

    # Wire edges
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        should_retry,
        {"transform": "transform", "synthesise": "synthesise"},
    )
    graph.add_edge("transform", "retrieve")
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
