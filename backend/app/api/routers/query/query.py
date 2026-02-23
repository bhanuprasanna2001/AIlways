from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends

from app.db import get_db
from app.db.models import User
from app.core.auth.deps import get_current_user, require_vault_member
from app.core.rag.query.pipeline import execute_query
from app.core.logger import setup_logger

from app.api.routers.query.schemas import QueryRequest, QueryResponse


logger = setup_logger(__name__)
router = APIRouter(prefix="/vaults/{vault_id}", tags=["query"])


@router.post("/query", summary="Query a vault and get a cited answer")
async def query_vault(
    vault_id: UUID,
    body: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query documents in a vault and receive a grounded, cited answer.

    Pipeline: classify → rewrite → expand → decompose → hybrid search
    → RRF → rerank → MMR → parent expand → CRAG evaluate → reason.

    Args:
        vault_id: The vault to query.
        body: Query request with query text and top_k.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        QueryResponse: The answer with citations, confidence, and metadata.
    """
    await require_vault_member(vault_id, current_user, db)

    result = await execute_query(
        query=body.query,
        vault_id=vault_id,
        db=db,
        top_k=body.top_k,
    )

    return QueryResponse(
        answer=result.answer,
        citations=result.citations,
        confidence=result.confidence,
        has_sufficient_evidence=result.has_sufficient_evidence,
        chunks_used=result.chunks_used,
        rewritten_query=result.rewritten_query,
        retrieval_method=result.retrieval_method,
        query_type=result.query_type,
        quality_score=result.quality_score,
        corrective_action_taken=result.corrective_action_taken,
        queries_used=result.queries_used,
        latency_ms=result.latency_ms,
    )
