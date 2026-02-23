import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.retrieval.dense_search import dense_search
from app.core.rag.retrieval.sparse_search import sparse_search
from app.core.rag.retrieval.rrf import reciprocal_rank_fusion
from app.core.rag.retrieval.filters import SearchResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)


async def hybrid_search(
    query_text: str,
    query_embedding: list[float],
    vault_id: UUID,
    db: AsyncSession,
    top_k: int = 30,
) -> list[SearchResult]:
    """Run dense + BM25 search concurrently and fuse with RRF.

    Dense search captures semantic similarity. BM25 captures exact keyword
    matches (invoice numbers, PO IDs, vendor names). RRF merges both ranked
    lists so items appearing in both get boosted. If one search returns
    nothing, the other's results pass through gracefully.

    Args:
        query_text: The raw query text (for BM25).
        query_embedding: The query embedding vector (for dense).
        vault_id: The vault to search in.
        db: The database session.
        top_k: Maximum results per search method (pre-fusion).

    Returns:
        list[SearchResult]: Fused results sorted by RRF score descending.
    """
    dense_results, sparse_results = await asyncio.gather(
        dense_search(query_embedding, vault_id, db, top_k),
        sparse_search(query_text, vault_id, db, top_k),
    )

    logger.info(
        f"Hybrid search: dense={len(dense_results)}, "
        f"sparse={len(sparse_results)} results"
    )

    fused = reciprocal_rank_fusion([dense_results, sparse_results])
    return fused
