import numpy as np
from langchain_core.vectorstores.utils import (
    _cosine_similarity as lc_cosine_similarity,
)

from app.core.rag.retrieval.filters import SearchResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)


def maximal_marginal_relevance(
    query_embedding: list[float],
    results: list[SearchResult],
    top_k: int = 7,
    lambda_param: float = 0.7,
) -> list[SearchResult]:
    """Select diverse results using MMR with reranker-aware relevance.

    Uses LangChain's vectorised cosine similarity for efficient
    inter-document diversity computation, but keeps the reranker /
    RRF score for the relevance signal — critical for correctness
    when Cohere cross-encoder scores differ from cosine similarity.

    MMR score = lambda * relevance(score) - (1-lambda) * max_sim(cosine)

    lambda_param controls the relevance vs diversity tradeoff:
      - 1.0 = pure relevance (no diversity penalty)
      - 0.0 = pure diversity (maximum dissimilarity)
      - 0.7 = default balance (biased toward relevance)

    Results without embeddings compete on relevance alone (no diversity
    penalty). LangChain's cosine helper handles the matrix maths.

    Args:
        query_embedding: The query embedding vector.
        results: Candidate search results (should have embedding field).
        top_k: Maximum number of results to select.
        lambda_param: Balance between relevance (1.0) and diversity (0.0).

    Returns:
        list[SearchResult]: Diverse subset of results.
    """
    if not results:
        return []

    if len(results) <= top_k:
        return list(results)

    n = len(results)

    # Pre-compute inter-document similarity matrix using LangChain's
    # vectorised cosine. For results without embeddings, rows / cols
    # default to 0 (no diversity penalty).
    embeddings: list[list[float] | None] = [r.embedding for r in results]
    has_emb = [e is not None for e in embeddings]

    emb_indices = [i for i, h in enumerate(has_emb) if h]
    if emb_indices:
        emb_matrix = np.array(
            [embeddings[i] for i in emb_indices], dtype=np.float32
        )
        # Full pairwise cosine similarity (k×k matrix)
        sim_matrix_small = lc_cosine_similarity(emb_matrix, emb_matrix)

        # Expand to n×n — non-embedded rows/cols stay 0
        sim_matrix = np.zeros((n, n), dtype=np.float32)
        for r_idx, gi in enumerate(emb_indices):
            for c_idx, gj in enumerate(emb_indices):
                sim_matrix[gi][gj] = sim_matrix_small[r_idx][c_idx]
    else:
        sim_matrix = np.zeros((n, n), dtype=np.float32)

    # Greedy MMR selection
    selected: list[int] = []
    remaining = set(range(n))

    while len(selected) < top_k and remaining:
        best_idx = -1
        best_mmr = float("-inf")

        for idx in remaining:
            relevance = results[idx].score

            if selected:
                max_sim = float(max(sim_matrix[idx][s] for s in selected))
            else:
                max_sim = 0.0

            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim

            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = idx

        if best_idx == -1:
            break

        selected.append(best_idx)
        remaining.discard(best_idx)

    output = [results[i] for i in selected]

    logger.debug(
        f"MMR selected {len(output)}/{n} results "
        f"({len(emb_indices)} with embeddings, {n - len(emb_indices)} without)"
    )

    return output
