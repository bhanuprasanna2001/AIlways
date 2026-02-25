"""Dense search — pgvector cosine similarity."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.retrieval.base import SearchResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)


async def dense_search(
    query_embedding: list[float],
    vault_id: UUID,
    db: AsyncSession,
    top_k: int = 20,
) -> list[SearchResult]:
    """Perform dense vector search using pgvector cosine distance.

    Args:
        query_embedding: Query vector from the embedder.
        vault_id: Scope search to this vault.
        db: Async database session.
        top_k: Maximum results to return.

    Returns:
        list[SearchResult]: Ranked by cosine similarity (descending).
    """
    query = text("""
        SELECT c.id, c.doc_id, c.content, c.content_with_header,
               1 - (c.embedding <=> :query_vec) AS score,
               c.section_heading, c.page_number,
               d.original_filename,
               c.embedding::text AS embedding_text
        FROM chunks c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.vault_id = :vault_id
          AND c.is_deleted = FALSE
          AND d.status = 'active'
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> :query_vec
        LIMIT :top_k
    """)

    result = await db.execute(query, {
        "query_vec": str(query_embedding),
        "vault_id": str(vault_id),
        "top_k": top_k,
    })

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

def _parse_vector(raw: str | None) -> list[float] | None:
    """Parse a pgvector text representation back to a float list.

    Args:
        raw: Vector as text (e.g. ``'[0.1, 0.2, ...]'``).

    Returns:
        list[float] or None: Parsed vector.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
