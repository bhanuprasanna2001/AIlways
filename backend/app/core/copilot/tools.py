"""Copilot tools — LangGraph-compatible tools wrapping existing retrieval.

Each tool accepts a query string and retrieves documents from the vault.
The ``vault_id`` is passed at runtime via LangGraph's ``RunnableConfig``
under ``config["configurable"]["vault_id"]``.

Tools acquire their own short-lived DB sessions via ``get_db_session()``
(the same pattern used by ``RAGClaimVerifier`` and ``SessionPersistence``).
"""

from __future__ import annotations

from datetime import date

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from sqlmodel import select, col
from sqlalchemy import func

from app.core.rag.embedding import get_embedder
from app.core.rag.retrieval import hybrid_search, entity_id_search
from app.core.rag.retrieval.base import SearchResult, build_retrieval_context
from app.core.utils import normalize_numbers
from app.core.config import get_settings
from app.core.logger import setup_logger
from app.core.copilot.filters import (
    parse_document_type,
    parse_date_range,
    parse_customer_id,
    build_filter_description,
)
from app.db import get_db_session
from app.db.models.chunk import Chunk
from app.db.models.document import Document

logger = setup_logger(__name__)

SETTINGS = get_settings()



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


@tool
async def get_full_document(document_title: str, config: RunnableConfig) -> str:
    """Retrieve the COMPLETE content of a specific document by its title.

    Use this tool after search_documents or lookup_entity identifies a
    relevant document but only returns partial data. This gets ALL
    chunks of the document concatenated in order, so you can read the
    entire content including full tables, all line items, and complete
    data.

    Args:
        document_title: The document filename or title (e.g. "StockReport_2016-07.pdf", "invoice_10248.md").
                        Use the exact title shown in search results.
    """
    vault_id = config["configurable"]["vault_id"]

    async with get_db_session() as db:
        # Find the document by title (case-insensitive partial match)
        stmt = (
            select(Document)
            .where(Document.vault_id == vault_id)
            .where(Document.deleted_at.is_(None))  # type: ignore[union-attr]
            .where(col(Document.original_filename).ilike(f"%{document_title}%"))
        )
        result = await db.execute(stmt)
        docs = result.scalars().all()

        if not docs:
            return f"No document found matching title '{document_title}' in this vault."

        # Use the first (best) match
        doc = docs[0]

        # Get ALL chunks for this document, ordered by chunk_index
        chunk_stmt = (
            select(Chunk)
            .where(Chunk.doc_id == doc.id)
            .where(Chunk.is_deleted == False)  # noqa: E712
            .order_by(Chunk.chunk_index)
        )
        chunk_result = await db.execute(chunk_stmt)
        chunks = chunk_result.scalars().all()

    if not chunks:
        return f"Document '{doc.original_filename}' exists but has no content chunks."

    # Concatenate all chunks with their headers
    parts: list[str] = []
    parts.append(f"=== FULL DOCUMENT: {doc.original_filename} ===")
    parts.append(f"(Total chunks: {len(chunks)}, Pages: {doc.page_count or 'unknown'})")
    parts.append("")
    for chunk in chunks:
        parts.append(chunk.content_with_header or chunk.content)
        parts.append("")

    full_content = "\n".join(parts)

    # Truncate if extremely large (>30k chars) to stay within LLM context
    if len(full_content) > 30000:
        full_content = full_content[:30000] + "\n\n... [Document truncated at 30,000 characters]"

    logger.info(
        f"get_full_document: title='{document_title}', "
        f"doc='{doc.original_filename}', chunks={len(chunks)}, "
        f"chars={len(full_content)}"
    )
    return full_content


@tool
async def compute(expression: str, config: RunnableConfig) -> str:
    """Evaluate a mathematical expression and return the result.

    Use this tool to calculate totals, sums, averages, counts, or any
    arithmetic from data extracted from documents. Supports standard
    Python math syntax.

    Args:
        expression: A mathematical expression to evaluate.
                    Examples: "12*14.0 + 10*9.80 + 5*34.80",
                              "sum([440.0, 2233.6, 1500.0])",
                              "len([1,2,3,4,5])", "round(1234.567, 2)"
    """
    # Safe evaluation — only allow math operations
    allowed_names = {
        "sum": sum,
        "len": len,
        "min": min,
        "max": max,
        "round": round,
        "abs": abs,
        "int": int,
        "float": float,
        "sorted": sorted,
    }

    # Basic security: reject dangerous patterns
    expr_clean = expression.strip()
    dangerous = ["import", "__", "exec", "eval", "open", "os.", "sys.", "subprocess"]
    for d in dangerous:
        if d in expr_clean.lower():
            return f"Error: expression contains disallowed term '{d}'"

    try:
        result = eval(expr_clean, {"__builtins__": {}}, allowed_names)  # noqa: S307
        logger.info(f"compute: '{expr_clean[:60]}' = {result}")
        return str(result)
    except Exception as e:
        logger.warning(f"compute failed: '{expr_clean[:60]}' — {e}")
        return f"Error evaluating expression: {e}"


@tool
async def filter_documents(
    query: str,
    config: RunnableConfig,
) -> str:
    """Filter vault documents by structured criteria and return a summary.

    Best for aggregate queries like "all invoices from July 2016",
    "how many purchase orders for customer VINET", or "total price
    of all orders in Q3 2017". Returns an exhaustive list of matching
    documents with key metadata — NOT full content.

    The query is a natural language description of what to filter.
    The tool parses document_type, date ranges, and customer IDs from
    the query automatically.

    For detailed content of specific documents found here, follow up
    with lookup_entity or get_full_document.

    Args:
        query: Natural language filter description. Examples:
               "all invoices from July 2016"
               "purchase orders for customer VINET"
               "stock reports from 2017"
               "shipping orders from Q1 2018"
    """
    vault_id = config["configurable"]["vault_id"]
    max_results = SETTINGS.COPILOT.FILTER_DOCUMENTS_MAX_RESULTS

    # Parse structured filters from the natural language query
    doc_type = parse_document_type(query)
    date_from, date_to = parse_date_range(query)
    customer_id = parse_customer_id(query)

    async with get_db_session() as db:
        stmt = (
            select(Document)
            .where(Document.vault_id == vault_id)
            .where(Document.deleted_at.is_(None))  # type: ignore[union-attr]
            .where(Document.status == "active")
        )

        if doc_type:
            stmt = stmt.where(Document.document_type == doc_type)
        if date_from:
            stmt = stmt.where(Document.order_date >= date_from)
        if date_to:
            stmt = stmt.where(Document.order_date <= date_to)
        if customer_id:
            stmt = stmt.where(col(Document.customer_id).ilike(customer_id))

        stmt = stmt.order_by(Document.order_date, Document.entity_id)
        stmt = stmt.limit(max_results)

        result = await db.execute(stmt)
        docs = result.scalars().all()

        # Also get total count if we hit the limit
        count_stmt = (
            select(func.count())
            .select_from(Document)
            .where(Document.vault_id == vault_id)
            .where(Document.deleted_at.is_(None))  # type: ignore[union-attr]
            .where(Document.status == "active")
        )
        if doc_type:
            count_stmt = count_stmt.where(Document.document_type == doc_type)
        if date_from:
            count_stmt = count_stmt.where(Document.order_date >= date_from)
        if date_to:
            count_stmt = count_stmt.where(Document.order_date <= date_to)
        if customer_id:
            count_stmt = count_stmt.where(col(Document.customer_id).ilike(customer_id))

        count_result = await db.execute(count_stmt)
        total_count = count_result.scalar_one()

    if not docs:
        # Provide helpful context about what IS available
        return await _no_results_message(vault_id, doc_type, date_from, date_to, customer_id)

    # Build summary
    parts: list[str] = []
    filter_desc = build_filter_description(doc_type, date_from, date_to, customer_id)
    parts.append(f"Found {total_count} documents matching: {filter_desc}")
    if total_count > max_results:
        parts.append(f"(Showing first {max_results} of {total_count})")
    parts.append("")

    grand_total = 0.0
    for i, doc in enumerate(docs, 1):
        line = f"{i}. {doc.original_filename}"
        details: list[str] = []
        if doc.order_date:
            details.append(f"Date: {doc.order_date}")
        if doc.customer_id:
            details.append(f"Customer: {doc.customer_id}")
        if doc.total_price is not None:
            details.append(f"Total: ${doc.total_price:,.2f}")
            grand_total += doc.total_price
        if doc.entity_id:
            details.append(f"ID: {doc.entity_id}")
        if details:
            line += " — " + ", ".join(details)
        if doc.summary:
            line += f"\n   Summary: {doc.summary}"
        parts.append(line)

    if grand_total > 0:
        parts.append("")
        parts.append(f"Grand Total: ${grand_total:,.2f}")
        parts.append(f"Count: {total_count}")

    summary = "\n".join(parts)
    logger.info(
        f"filter_documents: query='{query[:60]}', "
        f"type={doc_type}, date={date_from}..{date_to}, "
        f"customer={customer_id}, results={total_count}"
    )
    return summary


async def _no_results_message(
    vault_id,
    doc_type: str | None,
    date_from: date | None,
    date_to: date | None,
    customer_id: str | None,
) -> str:
    """Return a helpful message when no documents match the filter."""
    filter_desc = build_filter_description(doc_type, date_from, date_to, customer_id)

    # Query for available document types and date range to help the user
    async with get_db_session() as db:
        type_stmt = (
            select(Document.document_type, func.count().label("cnt"))
            .where(Document.vault_id == vault_id)
            .where(Document.deleted_at.is_(None))  # type: ignore[union-attr]
            .where(Document.status == "active")
            .where(Document.document_type.is_not(None))  # type: ignore[union-attr]
            .group_by(Document.document_type)
        )
        type_result = await db.execute(type_stmt)
        type_rows = type_result.all()

    available = ", ".join(f"{row[0]} ({row[1]})" for row in type_rows) if type_rows else "unknown"
    return (
        f"No documents found matching: {filter_desc}.\n"
        f"Available document types in this vault: {available}"
    )


# ---------------------------------------------------------------------------
# Tool list for graph construction
# ---------------------------------------------------------------------------

COPILOT_TOOLS = [search_documents, lookup_entity, filter_documents, get_full_document, compute]
