"""Sparse search — ParadeDB BM25 keyword search."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.retrieval.base import SearchResult
from app.core.rag.retrieval.dense import _parse_vector
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_MAX_QUERY_LENGTH = 500


async def sparse_search(
    query_text: str,
    vault_id: UUID,
    db: AsyncSession,
    top_k: int = 20,
) -> list[SearchResult]:
    """Perform BM25 keyword search using ParadeDB pg_search.

    Returns an empty list (no crash) if ParadeDB is not installed or
    the query produces no matches.

    Args:
        query_text: Raw user query string.
        vault_id: Scope search to this vault.
        db: Async database session.
        top_k: Maximum results to return.

    Returns:
        list[SearchResult]: Ranked by BM25 score (descending).
    """
    sanitized = _sanitize_query(query_text)
    if not sanitized:
        return []

    query = text("""
        SELECT c.id, c.doc_id, c.content, c.content_with_header,
               paradedb.score(c.id) AS score,
               c.section_heading, c.page_number,
               d.original_filename,
               c.embedding::text AS embedding_text
        FROM chunks c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.content_with_header @@@ :query_text
          AND c.vault_id = :vault_id
          AND c.is_deleted = FALSE
          AND d.status = 'active'
        ORDER BY paradedb.score(c.id) DESC
        LIMIT :top_k
    """)

    try:
        result = await db.execute(query, {
            "query_text": sanitized,
            "vault_id": str(vault_id),
            "top_k": top_k,
        })
    except Exception as e:
        logger.warning(f"BM25 search failed (ParadeDB may not be installed): {e}")
        return []

    return [
        SearchResult(
            chunk_id=row[0],
            doc_id=row[1],
            content=row[2],
            content_with_header=row[3],
            score=float(row[4]),
            section_heading=row[5],
            page_number=row[6],
            original_filename=row[7],
            embedding=_parse_vector(row[8]),
        )
        for row in result.fetchall()
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_query(query: str) -> str:
    """Sanitize a query string for BM25 search.

    Removes special characters that could break the ParadeDB query
    parser, limits length, and collapses whitespace.

    Args:
        query: Raw user query.

    Returns:
        str: Cleaned query string, or empty if invalid.
    """
    cleaned = query.strip()[:_MAX_QUERY_LENGTH]
    cleaned = re.sub(r"[^\w\s\-.$#@/]", " ", cleaned, flags=re.UNICODE)
    cleaned = " ".join(cleaned.split())
    return cleaned
