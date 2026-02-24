"""Hybrid search — dense + sparse fusion with RRF and MMR."""

from __future__ import annotations

from uuid import UUID

import numpy as np
from langchain_core.vectorstores.utils import maximal_marginal_relevance as _lc_mmr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.retrieval.base import SearchResult
from app.core.rag.retrieval.dense import dense_search
from app.core.rag.retrieval.sparse import sparse_search
from app.core.logger import setup_logger

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def hybrid_search(
    query_text: str,
    query_embedding: list[float],
    vault_id: UUID,
    db: AsyncSession,
    top_k: int = 5,
    mmr_lambda: float = 0.7,
) -> list[SearchResult]:
    """Run dense + BM25 search, fuse with RRF, diversify with MMR.

    Executes dense and sparse searches, merges the ranked lists using
    Reciprocal Rank Fusion, then applies Maximal Marginal Relevance
    for diversity in the final results.

    Args:
        query_text: Raw user query string.
        query_embedding: Query vector from the embedder.
        vault_id: Scope search to this vault.
        db: Async database session.
        top_k: Final number of results after MMR.
        mmr_lambda: Trade-off — 1.0 = pure relevance, 0.0 = pure diversity.
            For claim verification use 1.0 to maximize relevance.

    Returns:
        list[SearchResult]: High-quality results.
    """
    # Fetch more candidates than needed for RRF + MMR to operate on
    fetch_k = max(top_k * 4, 20)

    dense_results = await dense_search(query_embedding, vault_id, db, top_k=fetch_k)
    sparse_results = await sparse_search(query_text, vault_id, db, top_k=fetch_k)

    logger.info(f"Hybrid search: dense={len(dense_results)}, sparse={len(sparse_results)}")

    if not dense_results and not sparse_results:
        return []

    # Fuse with RRF
    fused = reciprocal_rank_fusion([dense_results, sparse_results])

    # Diversify with MMR
    diverse = maximal_marginal_relevance(
        query_embedding, fused, top_k=top_k, lambda_param=mmr_lambda,
    )

    return diverse


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    result_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion.

    Score formula: ``score(d) = Σ 1 / (k + rank + 1)``

    Args:
        result_lists: Ranked result lists from different retrievers.
        k: RRF smoothing constant (standard = 60).

    Returns:
        list[SearchResult]: Merged list sorted by fused score (descending).
    """
    scores: dict[str, float] = {}
    best: dict[str, SearchResult] = {}

    for result_list in result_lists:
        for rank, result in enumerate(result_list):
            cid = str(result.chunk_id)
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            # Keep the copy with the highest original score
            if cid not in best or result.score > best[cid].score:
                best[cid] = result
            # Prefer copies that have an embedding vector
            if result.embedding is not None and best[cid].embedding is None:
                best[cid] = best[cid].model_copy(update={"embedding": result.embedding})

    fused = [
        best[cid].model_copy(update={"score": fused_score})
        for cid, fused_score in scores.items()
    ]
    fused.sort(key=lambda r: r.score, reverse=True)
    return fused


# ---------------------------------------------------------------------------
# Maximal Marginal Relevance
# ---------------------------------------------------------------------------

def maximal_marginal_relevance(
    query_embedding: list[float],
    results: list[SearchResult],
    top_k: int = 5,
    lambda_param: float = 0.7,
) -> list[SearchResult]:
    """Select diverse results using Maximal Marginal Relevance.

    Delegates to ``langchain_core.vectorstores.utils.maximal_marginal_relevance``
    for the core computation. Results without embeddings (e.g. from BM25-only
    retrieval) are appended after the embedding-based selection.

    Args:
        query_embedding: Query vector.
        results: Candidate results (typically from RRF fusion).
        top_k: Number of results to select.
        lambda_param: Trade-off — 1.0 = pure relevance, 0.0 = pure diversity.

    Returns:
        list[SearchResult]: Diverse subset of results.
    """
    if not results:
        return []
    if len(results) <= top_k:
        return list(results)

    # Separate results with and without embeddings
    with_emb = [(i, r) for i, r in enumerate(results) if r.embedding is not None]
    without_emb = [r for r in results if r.embedding is None]

    if not with_emb:
        # No embeddings available — fall back to relevance order
        return results[:top_k]

    embedding_list = [r.embedding for _, r in with_emb]

    selected_indices = _lc_mmr(
        np.array(query_embedding),
        embedding_list,
        lambda_mult=lambda_param,
        k=min(top_k, len(with_emb)),
    )

    selected = [with_emb[idx][1] for idx in selected_indices]

    # Fill remaining slots from results without embeddings
    remaining = top_k - len(selected)
    if remaining > 0:
        selected.extend(without_emb[:remaining])

    return selected
