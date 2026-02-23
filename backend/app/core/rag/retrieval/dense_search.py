from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.retrieval.filters import SearchResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)


async def dense_search(
    query_embedding: list[float],
    vault_id: UUID,
    db: AsyncSession,
    top_k: int = 5,
) -> list[SearchResult]:
    """Perform dense vector search using pgvector cosine distance.

    Args:
        query_embedding: The query embedding vector.
        vault_id: The vault to search in.
        db: The database session.
        top_k: Maximum number of results to return.

    Returns:
        list[SearchResult]: Search results ordered by similarity (descending).
    """
    # Set HNSW search parameters for better recall
    await db.execute(text("SET hnsw.ef_search = 100"))

    query = text("""
        SELECT c.id, c.doc_id, c.content, c.content_with_header,
               1 - (c.embedding <=> :query_vec) AS score,
               c.section_heading, c.page_number,
               d.original_filename,
               c.embedding::float[] AS embedding
        FROM chunks c
        JOIN documents d ON c.doc_id = d.id
        WHERE c.vault_id = :vault_id
          AND c.is_deleted = FALSE
          AND d.status = 'active'
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> :query_vec
        LIMIT :top_k
    """)

    result = await db.execute(
        query,
        {
            "query_vec": str(query_embedding),
            "vault_id": str(vault_id),
            "top_k": top_k,
        },
    )

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
            embedding=list(row[8]) if row[8] else None,
        )
        for row in rows
    ]
