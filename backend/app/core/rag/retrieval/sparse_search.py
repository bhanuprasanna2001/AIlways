import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.retrieval.filters import SearchResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)

# Characters that can break the pg_search BM25 parser
_UNSAFE_RE = re.compile(r"[^\w\s\-.$#@/]", re.UNICODE)
_MAX_QUERY_LENGTH = 500


def _sanitize_query(query: str) -> str:
    """Sanitize query text for safe BM25 parsing.

    Strips dangerous characters while preserving meaningful tokens
    like invoice numbers (INV-2024-001), dollar amounts ($5,234.00),
    and PO references (PO#12345).

    Args:
        query: Raw user query text.

    Returns:
        str: Sanitized query safe for pg_search @@@ operator.
    """
    cleaned = query.strip()[:_MAX_QUERY_LENGTH]
    cleaned = _UNSAFE_RE.sub(" ", cleaned)
    # Collapse multiple spaces
    cleaned = " ".join(cleaned.split())
    return cleaned


async def sparse_search(
    query_text: str,
    vault_id: UUID,
    db: AsyncSession,
    top_k: int = 30,
) -> list[SearchResult]:
    """Perform BM25 keyword search using ParadeDB pg_search.

    Uses the idx_chunks_bm25 index on content_with_header. Catches exact
    terms (invoice numbers, vendor names) that dense search misses because
    those tokens have no semantic meaning in embedding space.

    Args:
        query_text: The search query text.
        vault_id: The vault to search in.
        db: The database session.
        top_k: Maximum number of results to return.

    Returns:
        list[SearchResult]: Search results ordered by BM25 score descending.
    """
    if not query_text or not query_text.strip():
        return []

    sanitized = _sanitize_query(query_text)
    if not sanitized:
        return []

    query = text("""
        SELECT c.id, c.doc_id, c.content, c.content_with_header,
               paradedb.score(c.id) AS score,
               c.section_heading, c.page_number,
               d.original_filename
        FROM chunks c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.content_with_header @@@ :query_text
          AND c.vault_id = :vault_id
          AND c.chunk_type = 'child'
          AND c.is_deleted = FALSE
          AND d.status = 'active'
        ORDER BY paradedb.score(c.id) DESC
        LIMIT :top_k
    """)

    try:
        result = await db.execute(
            query,
            {
                "query_text": sanitized,
                "vault_id": str(vault_id),
                "top_k": top_k,
            },
        )
    except Exception as e:
        logger.warning(f"BM25 search failed (query may have no matches): {e}")
        return []

    rows = result.fetchall()

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
        )
        for row in rows
    ]
