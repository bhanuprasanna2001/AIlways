"""Copilot tools — LangGraph-compatible tools wrapping existing retrieval.

Each tool accepts a query string and retrieves documents from the vault.
The ``vault_id`` is passed at runtime via LangGraph's ``RunnableConfig``
under ``config["configurable"]["vault_id"]``.

Tools acquire their own short-lived DB sessions via ``get_db_session()``
(the same pattern used by ``RAGClaimVerifier`` and ``SessionPersistence``).
"""

from __future__ import annotations

import re

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from app.core.rag.embedding import get_embedder
from app.core.rag.retrieval import hybrid_search, entity_id_search
from app.core.rag.retrieval.base import SearchResult, build_retrieval_context
from app.core.utils import normalize_numbers
from app.core.config import get_settings
from app.core.logger import setup_logger
from app.db import get_db_session

logger = setup_logger(__name__)

SETTINGS = get_settings()

# Re-usable pattern for entity IDs (4+ digit numbers)
_ENTITY_ID_PATTERN = re.compile(r"\b\d{4,}\b")


def _format_results(results: list[SearchResult]) -> str:
    """Format search results into a context string for the LLM."""
    if not results:
        return "No documents found."
    return build_retrieval_context(results)


@tool
async def search_documents(query: str, config: RunnableConfig) -> str:
    """Search vault documents using hybrid search (semantic + keyword).

    Best for general questions, topical queries, and finding relevant
    content by meaning. Combines dense vector search with BM25 keyword
    matching, then reranks with Reciprocal Rank Fusion and MMR.

    Args:
        query: Natural language search query.
    """
    vault_id = config["configurable"]["vault_id"]
    top_k = config["configurable"].get("top_k", SETTINGS.RAG_SEARCH_TOP_K)

    query = normalize_numbers(query.strip())
    if not query:
        return "No documents found."

    embedder = get_embedder()
    query_embedding = await embedder.embed_query(query)

    async with get_db_session() as db:
        results = await hybrid_search(
            query_text=query,
            query_embedding=query_embedding,
            vault_id=vault_id,
            db=db,
            top_k=top_k,
        )

    logger.info(f"search_documents: query='{query[:60]}', results={len(results)}")
    return _format_results(results)


@tool
async def lookup_entity(entity_ids: str, config: RunnableConfig) -> str:
    """Look up specific entities by their identifiers (numbers, IDs, codes).

    Best for queries referencing specific entities like invoice numbers,
    order IDs, or reference codes. Does a direct database lookup rather
    than semantic search, so it finds exact matches even when many
    similar documents exist.

    Args:
        entity_ids: Comma-separated entity identifiers (e.g. "10248,10535").
    """
    vault_id = config["configurable"]["vault_id"]

    ids = [eid.strip() for eid in entity_ids.split(",") if eid.strip()]
    if not ids:
        return "No entity IDs provided."

    # Limit to prevent abuse
    ids = ids[: SETTINGS.ENTITY_SEARCH_MAX_IDS]

    async with get_db_session() as db:
        results = await entity_id_search(ids, vault_id, db)

    logger.info(f"lookup_entity: IDs={ids}, results={len(results)}")

    if not results:
        return f"No documents found containing entity IDs: {', '.join(ids)}"

    return _format_results(results)


# ---------------------------------------------------------------------------
# Tool list for graph construction
# ---------------------------------------------------------------------------

COPILOT_TOOLS = [search_documents, lookup_entity]
