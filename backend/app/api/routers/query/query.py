import asyncio
import time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status

from app.db import get_db
from app.db.models import User
from app.core.auth.deps import get_current_user, require_vault_member
from app.core.config import get_settings
from app.core.rag.embedding import get_embedder
from app.core.rag.retrieval import hybrid_search
from app.core.rag.generation import generate_answer
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

    Pipeline: embed → hybrid search (dense + BM25 + RRF + MMR) → generate.
    """
    await require_vault_member(vault_id, current_user, db)

    start = time.monotonic()

    # 1. Embed query
    embedder = get_embedder()
    try:
        query_embedding = await asyncio.wait_for(
            embedder.embed_query(body.query),
            timeout=SETTINGS.API_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Query embedding timed out",
        )

    # 2. Hybrid search (dense + sparse + RRF + MMR)
    results = await hybrid_search(
        query_text=body.query,
        query_embedding=query_embedding,
        vault_id=vault_id,
        db=db,
        top_k=body.top_k,
    )

    # 3. Generate answer
    if not results:
        return QueryResponse(
            answer="No relevant documents were found for your query.",
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    try:
        answer = await asyncio.wait_for(
            generate_answer(body.query, results),
            timeout=SETTINGS.API_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Answer generation timed out",
        )

    return QueryResponse(
        answer=answer.answer,
        citations=answer.citations,
        confidence=answer.confidence,
        has_sufficient_evidence=answer.has_sufficient_evidence,
        chunks_used=len(results),
        retrieval_method="hybrid",
        latency_ms=int((time.monotonic() - start) * 1000),
    )
