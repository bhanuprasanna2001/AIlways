from app.core.rag.retrieval.filters import SearchResult


def reciprocal_rank_fusion(
    result_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion.

    RRF is parameter-free (k=60 is standard). It handles the case where
    dense search finds semantically similar docs while BM25 nails the exact
    term — both contribute, neither dominates. Items appearing in multiple
    lists receive a score boost.

    Args:
        result_lists: List of ranked SearchResult lists (e.g. [dense, sparse]).
        k: RRF constant (default 60, rarely needs changing).

    Returns:
        list[SearchResult]: Merged results sorted by fused score descending.
    """
    if not result_lists:
        return []

    # chunk_id -> fused score
    scores: dict[str, float] = {}
    # chunk_id -> best SearchResult (preserve metadata from highest-ranked)
    best: dict[str, SearchResult] = {}

    for result_list in result_lists:
        for rank, result in enumerate(result_list):
            cid = str(result.chunk_id)
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)

            # Keep the copy with the higher original score for metadata
            if cid not in best or result.score > best[cid].score:
                best[cid] = result

            # Preserve embedding across copies — dense search provides it,
            # sparse search does not. Without this, the embedding gets
            # lost when the sparse copy wins on score, breaking downstream
            # MMR diversity computation.
            if result.embedding is not None and (
                cid not in best or best[cid].embedding is None
            ):
                best[cid] = best[cid].model_copy(
                    update={"embedding": result.embedding}
                )

    # Build output with fused scores
    fused = []
    for cid, fused_score in scores.items():
        result = best[cid].model_copy(update={"score": fused_score})
        fused.append(result)

    fused.sort(key=lambda r: r.score, reverse=True)
    return fused
