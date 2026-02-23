import time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logger import setup_logger
from app.core.rag.query.schemas import (
    QueryType,
    ClassificationResult,
    RetrievalQuality,
    RetrievalQualitySignals,
    QueryPipelineResult,
)
from app.core.rag.query.query_classifier import classify_query
from app.core.rag.query.query_rewriter import rewrite_query
from app.core.rag.query.query_expander import expand_query
from app.core.rag.query.query_decomposer import decompose_query
from app.core.rag.embedding.openai import OpenAIEmbedder
from app.core.rag.retrieval.hybrid_search import hybrid_search
from app.core.rag.retrieval.rrf import reciprocal_rank_fusion
from app.core.rag.retrieval.reranker import get_reranker
from app.core.rag.retrieval.mmr import maximal_marginal_relevance
from app.core.rag.retrieval.parent_expander import expand_to_parents
from app.core.rag.retrieval.filters import SearchResult
from app.core.rag.reasoning.crag import evaluate_retrieval_quality, reason

logger = setup_logger(__name__)
SETTINGS = get_settings()

_INSUFFICIENT_RESULT = QueryPipelineResult(
    answer="Insufficient evidence in vault.",
    has_sufficient_evidence=False,
)


async def execute_query(
    query: str,
    vault_id: UUID,
    db: AsyncSession,
    top_k: int = 5,
) -> QueryPipelineResult:
    """Execute the full query intelligence pipeline.

    Orchestrates: classify  rewrite  expand  decompose  hybrid search
     RRF  rerank  MMR  parent expand  CRAG evaluation  reason.

    Includes a single corrective retry when retrieval quality is
    uncertain. Skips the reasoning LLM call entirely when retrieval
    is insufficient, saving cost and preventing hallucination.

    Args:
        query: The user's raw query text.
        vault_id: The vault to search in.
        db: Database session.
        top_k: Number of final chunks to pass to reasoning.

    Returns:
        QueryPipelineResult: Answer, citations, quality signals, metadata.
    """
    start = time.monotonic()

    # 1. Classify query type
    classification = await classify_query(
        query, SETTINGS.OPENAI_API_KEY, SETTINGS.OPENAI_QUERY_MODEL
    )
    logger.info(
        f"Query classified: {classification.query_type} "
        f"(confidence={classification.confidence:.2f})"
    )

    # 2. Rewrite if vague
    primary = query
    was_rewritten = False
    if classification.query_type == QueryType.VAGUE_EXPLORATORY:
        primary = await rewrite_query(
            primary, SETTINGS.OPENAI_API_KEY, SETTINGS.OPENAI_QUERY_MODEL
        )
        was_rewritten = primary != query

    # 3. Expand (multi-query + conditional HyDE)
    variants = await expand_query(
        primary, classification, SETTINGS.OPENAI_API_KEY, SETTINGS.OPENAI_QUERY_MODEL
    )

    # 4. Decompose if multi-part
    if classification.is_multi_part:
        sub_queries = await decompose_query(
            primary, SETTINGS.OPENAI_API_KEY, SETTINGS.OPENAI_QUERY_MODEL
        )
        seen = {v.strip().lower() for v in variants}
        for sq in sub_queries:
            if sq.strip().lower() not in seen:
                variants.append(sq)
                seen.add(sq.strip().lower())

    # 5. Search pipeline (embed  hybrid  RRF  rerank  MMR  parent expand)
    results = await _search_pipeline(primary, variants, vault_id, db, top_k)

    # 6. CRAG: evaluate retrieval quality
    signals = evaluate_retrieval_quality(results, classification)
    logger.debug(
        f"CRAG quality={signals.quality} score={signals.score:.4f} "
        f"top={signals.top_score:.4f} spread={signals.score_spread:.4f} "
        f"entity_overlap={signals.entity_overlap_ratio:.4f} count={signals.result_count}"
    )

    # 7. CRAG: handle quality tiers
    corrective_action = None

    if signals.quality == RetrievalQuality.INSUFFICIENT:
        logger.info("CRAG: retrieval insufficient — returning without LLM call")
        return _build_result(
            _INSUFFICIENT_RESULT, classification, signals,
            variants, 0, was_rewritten, primary, start,
        )

    if signals.quality == RetrievalQuality.UNCERTAIN:
        logger.info("CRAG: retrieval uncertain — attempting corrective retry")
        corrective_action = "rewrite_and_expand"

        # Rewrite from original query for a fresh angle
        corrective_query = await rewrite_query(
            query, SETTINGS.OPENAI_API_KEY, SETTINGS.OPENAI_QUERY_MODEL
        )
        corrective_variants = await expand_query(
            corrective_query,
            classification,
            SETTINGS.OPENAI_API_KEY,
            SETTINGS.OPENAI_QUERY_MODEL,
            force_hyde=True,
        )

        new_results = await _search_pipeline(
            corrective_query, corrective_variants, vault_id, db, top_k
        )
        new_signals = evaluate_retrieval_quality(new_results, classification)

        # Keep the better result set
        if new_signals.score >= signals.score:
            results = new_results
            signals = new_signals
            variants = corrective_variants
            primary = corrective_query
            was_rewritten = primary != query

        signals = signals.model_copy(
            update={"corrective_action": corrective_action}
        )

        if signals.quality == RetrievalQuality.INSUFFICIENT:
            logger.info("CRAG: still insufficient after retry")
            return _build_result(
                _INSUFFICIENT_RESULT, classification, signals,
                variants, 0, was_rewritten, primary, start,
            )

    # 8. Reason (with confidence qualifier if quality is still uncertain)
    reasoning = await reason(
        query=primary,
        search_results=results,
        api_key=SETTINGS.OPENAI_API_KEY,
        model=SETTINGS.OPENAI_REASONING_MODEL,
        confidence_qualifier=signals.quality == RetrievalQuality.UNCERTAIN,
    )

    return _build_result(
        QueryPipelineResult(
            answer=reasoning.answer,
            citations=reasoning.citations,
            confidence=reasoning.confidence,
            has_sufficient_evidence=reasoning.has_sufficient_evidence,
        ),
        classification, signals, variants,
        len(results), was_rewritten, primary, start,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _search_pipeline(
    primary_query: str,
    query_variants: list[str],
    vault_id: UUID,
    db: AsyncSession,
    top_k: int,
) -> list[SearchResult]:
    """Run embed  hybrid search  RRF  rerank  MMR  parent expand.

    Searches with every query variant, merges results via RRF,
    then applies cross-encoder reranking and MMR diversity selection.

    Args:
        primary_query: The main query (for reranking and MMR).
        query_variants: All query variants to search with.
        vault_id: Vault to search.
        db: Database session.
        top_k: Final number of results after MMR.

    Returns:
        list[SearchResult]: Parent-expanded search results.
    """
    if not query_variants:
        query_variants = [primary_query]

    embedder = OpenAIEmbedder(
        api_key=SETTINGS.OPENAI_API_KEY,
        model=SETTINGS.OPENAI_EMBEDDING_MODEL,
        dims=SETTINGS.OPENAI_EMBEDDING_DIMENSIONS,
    )

    all_embeddings = await embedder.embed(query_variants)
    primary_embedding = all_embeddings[0]

    # Search each variant sequentially (DB session safety)
    all_result_lists: list[list[SearchResult]] = []
    for text, embedding in zip(query_variants, all_embeddings):
        results = await hybrid_search(text, embedding, vault_id, db, top_k=30)
        all_result_lists.append(results)
        logger.debug(
            f"Variant '{text[:50]}': {len(results)} results, "
            f"top_score={results[0].score:.6f}" if results else f"Variant '{text[:50]}': 0 results"
        )

    # Merge with RRF (pass through if single query)
    if len(all_result_lists) == 1:
        fused = all_result_lists[0]
    else:
        fused = reciprocal_rank_fusion(all_result_lists)

    logger.debug(
        f"RRF fused: {len(fused)} results, "
        f"top_score={fused[0].score:.6f}" if fused else "RRF: 0 results"
    )

    # Log top 5 RRF results for debugging
    for i, r in enumerate(fused[:5]):
        logger.debug(f"RRF [{i}] score={r.score:.6f} file={r.original_filename}")

    # Rerank with primary query
    reranker = get_reranker(SETTINGS.COHERE_API_KEY)
    reranked = await reranker.rerank(primary_query, fused, top_k=20)

    logger.debug(
        f"Reranked: {len(reranked)} results, "
        f"top_score={reranked[0].score:.6f}" if reranked else "Reranked: 0 results"
    )

    # Log top 5 reranked results for debugging
    for i, r in enumerate(reranked[:5]):
        logger.debug(f"Reranked [{i}] score={r.score:.6f} file={r.original_filename}")

    # MMR for diversity
    diverse = maximal_marginal_relevance(
        primary_embedding, reranked, top_k=top_k, lambda_param=0.7
    )

    # Parent expansion
    return await expand_to_parents(diverse, db)


def _build_result(
    base: QueryPipelineResult,
    classification: ClassificationResult,
    signals: RetrievalQualitySignals,
    queries_used: list[str],
    chunks_used: int,
    was_rewritten: bool,
    primary: str,
    start_time: float,
) -> QueryPipelineResult:
    """Enrich a pipeline result with metadata.

    Args:
        base: The base result (answer + citations).
        classification: Query classification.
        signals: Retrieval quality signals.
        queries_used: All query variants searched.
        chunks_used: Number of chunks passed to reasoning.
        was_rewritten: Whether the query was rewritten.
        primary: The effective query used.
        start_time: Pipeline start time (monotonic).

    Returns:
        QueryPipelineResult: Complete result with all metadata.
    """
    return base.model_copy(
        update={
            "query_type": classification.query_type.value,
            "quality_score": signals.score,
            "quality_signals": signals,
            "corrective_action_taken": signals.corrective_action,
            "queries_used": queries_used,
            "chunks_used": chunks_used,
            "rewritten_query": primary if was_rewritten else None,
            "latency_ms": int((time.monotonic() - start_time) * 1000),
        }
    )
