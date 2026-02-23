from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status

from app.db import get_db
from app.db.models import User
from app.core.config import get_settings
from app.core.auth.deps import get_current_user, require_vault_member
from app.core.rag.embedding.openai import OpenAIEmbedder
from app.core.rag.retrieval.dense_search import dense_search
from app.core.rag.reasoning.crag import reason
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

    Pipeline: embed query → dense search → LLM reasoning → cited response.

    Args:
        vault_id: The vault to query.
        body: Query request with query text and top_k.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        QueryResponse: The answer with citations and confidence.
    """
    await require_vault_member(vault_id, current_user, db)

    # 1. Embed the query
    embedder = OpenAIEmbedder(
        api_key=SETTINGS.OPENAI_API_KEY,
        model=SETTINGS.OPENAI_EMBEDDING_MODEL,
        dims=SETTINGS.OPENAI_EMBEDDING_DIMENSIONS,
    )
    query_vectors = await embedder.embed([body.query])
    query_embedding = query_vectors[0]

    # 2. Search
    results = await dense_search(
        query_embedding=query_embedding,
        vault_id=vault_id,
        db=db,
        top_k=body.top_k,
    )

    # 3. Reason
    reasoning = await reason(
        query=body.query,
        search_results=results,
        api_key=SETTINGS.OPENAI_API_KEY,
        model=SETTINGS.OPENAI_REASONING_MODEL,
    )

    return QueryResponse(
        answer=reasoning.answer,
        citations=reasoning.citations,
        confidence=reasoning.confidence,
        has_sufficient_evidence=reasoning.has_sufficient_evidence,
        chunks_used=len(results),
    )
