import numpy as np

from app.core.rag.retrieval.filters import SearchResult


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        float: Cosine similarity in [-1, 1].
    """
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


def maximal_marginal_relevance(
    query_embedding: list[float],
    results: list[SearchResult],
    top_k: int = 7,
    lambda_param: float = 0.7,
) -> list[SearchResult]:
    """Select diverse results using Maximum Marginal Relevance.

    Prevents redundancy — you do not want 5 chunks all saying the same
    thing. Iteratively selects results that are both relevant to the
    query and dissimilar to already-selected results.

    lambda_param controls the relevance vs diversity tradeoff:
      - 1.0 = pure relevance (no diversity penalty)
      - 0.0 = pure diversity (maximum dissimilarity)
      - 0.7 = default balance (biased toward relevance)

    Results without embeddings are appended at the end in their original
    order (they cannot participate in diversity computation).

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

    # Separate results with and without embeddings
    with_emb: list[tuple[int, SearchResult]] = []
    without_emb: list[SearchResult] = []

    for i, r in enumerate(results):
        if r.embedding is not None:
            with_emb.append((i, r))
        else:
            without_emb.append(r)

    # If no embeddings available, fall back to original order
    if not with_emb:
        return results[:top_k]

    # Pre-compute query similarity for each candidate
    query_sims = {
        i: _cosine_similarity(query_embedding, r.embedding)
        for i, r in with_emb
    }

    selected: list[SearchResult] = []
    selected_embeddings: list[list[float]] = []
    remaining = dict(with_emb)

    while len(selected) < top_k and remaining:
        best_idx = -1
        best_score = float("-inf")

        for idx, result in remaining.items():
            relevance = query_sims[idx]

            # Max similarity to any already-selected result
            if selected_embeddings:
                max_sim = max(
                    _cosine_similarity(result.embedding, sel_emb)
                    for sel_emb in selected_embeddings
                )
            else:
                max_sim = 0.0

            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx

        if best_idx == -1:
            break

        winner = remaining.pop(best_idx)
        selected.append(winner)
        selected_embeddings.append(winner.embedding)

    # Fill remaining slots with results that had no embeddings
    slots_left = top_k - len(selected)
    if slots_left > 0:
        selected.extend(without_emb[:slots_left])

    return selected
