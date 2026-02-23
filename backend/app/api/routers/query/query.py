from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status

from app.db import get_db
from app.db.models import User
from app.core.config import get_settings
from app.core.auth.deps import get_current_user, require_vault_member
from app.core.rag.embedding.openai import OpenAIEmbedder
from app.core.rag.retrieval.hybrid_search import hybrid_search
from app.core.rag.retrieval.reranker import get_reranker
from app.core.rag.retrieval.mmr import maximal_marginal_relevance
from app.core.rag.retrieval.parent_expander import expand_to_parents
from app.core.rag.reasoning.crag import reason
from app.core.rag.query.query_rewriter import rewrite_query
from app.core.logger import setup_logger

from app.api.routers.query.schemas import QueryRequest, QueryResponse


logger = setup_logger(__name__)
router = APIRouter(prefix="/vaults/{vault_id}", tags=["query"])
SETTINGS = get_settings()


@router.post("/query", summary="Query a vault and get a cited answer")
async def query_vault(
    vault_id: UUID,
    body: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query documents in a vault and receive a grounded, cited answer.

    Pipeline: rewrite → embed → hybrid search (dense + BM25) → RRF →
    rerank → MMR → parent expand → reason → cite.

    Args:
        vault_id: The vault to query.
        body: Query request with query text and top_k.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        QueryResponse: The answer with citations, confidence, and metadata.
    """
    await require_vault_member(vault_id, current_user, db)

    # 1. Rewrite vague queries
    rewritten = await rewrite_query(
        query=body.query,
        api_key=SETTINGS.OPENAI_API_KEY,
        model=SETTINGS.OPENAI_QUERY_MODEL,
    )
    was_rewritten = rewritten != body.query

    # 2. Embed the (rewritten) query
    embedder = OpenAIEmbedder(
        api_key=SETTINGS.OPENAI_API_KEY,
        model=SETTINGS.OPENAI_EMBEDDING_MODEL,
        dims=SETTINGS.OPENAI_EMBEDDING_DIMENSIONS,
    )
    query_vectors = await embedder.embed([rewritten])
    query_embedding = query_vectors[0]

    # 3. Hybrid search (dense + BM25 concurrent, fused with RRF)
    fused_results = await hybrid_search(
        query_text=rewritten,
        query_embedding=query_embedding,
        vault_id=vault_id,
        db=db,
        top_k=30,
    )

    # 4. Rerank
    reranker = get_reranker(SETTINGS.COHERE_API_KEY)
    reranked = await reranker.rerank(
        query=rewritten,
        results=fused_results,
        top_k=20,
    )

    # 5. MMR for diversity
    diverse = maximal_marginal_relevance(
        query_embedding=query_embedding,
        results=reranked,
        top_k=body.top_k,
        lambda_param=0.7,
    )

    # 6. Parent expansion
    expanded = await expand_to_parents(diverse, db)

    # 7. Reason
    reasoning = await reason(
        query=rewritten,
        search_results=expanded,
        api_key=SETTINGS.OPENAI_API_KEY,
        model=SETTINGS.OPENAI_REASONING_MODEL,
    )

    return QueryResponse(
        answer=reasoning.answer,
        citations=reasoning.citations,
        confidence=reasoning.confidence,
        has_sufficient_evidence=reasoning.has_sufficient_evidence,
        chunks_used=len(expanded),
        rewritten_query=rewritten if was_rewritten else None,
        retrieval_method="hybrid",
    )
