import asyncio
import json
import time
from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.db import get_db
from app.db.models import User
from app.core.auth.deps import get_current_user, require_vault_member
from app.core.config import get_settings
from app.core.rag.embedding import get_embedder
from app.core.rag.retrieval import hybrid_search
from app.core.rag.generation import generate_answer, stream_answer, parse_response
from app.core.logger import setup_logger

from app.api.routers.query.schemas import QueryRequest, QueryResponse


logger = setup_logger(__name__)
router = APIRouter(prefix="/vaults/{vault_id}", tags=["query"])
SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# SSE streaming helper
# ---------------------------------------------------------------------------

async def _sse_stream(
    query: str,
    results: list,
    start: float,
) -> AsyncIterator[str]:
    """Yield Server-Sent Events for a streaming query response.

    Event types:
      - ``retrieval``: search complete, includes ``chunks_used``.
      - ``token``: raw LLM content delta.
      - ``done``: final structured response with answer, citations, etc.
      - ``error``: generation failed, includes fallback answer.
    """
    yield f"data: {json.dumps({'type': 'retrieval', 'chunks_used': len(results)})}\n\n"

    full_content = ""
    try:
        async for token in stream_answer(query, results):
            full_content += token
            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

        answer = parse_response(full_content)
    except Exception as e:
        logger.error(f"Streaming generation failed: {e}")
        yield f"data: {json.dumps({'type': 'error', 'answer': 'Generation failed.', 'confidence': 0.0})}\n\n"
        return

    yield (
        f"data: {json.dumps({'type': 'done', **answer.model_dump(mode='json'), 'chunks_used': len(results), 'retrieval_method': 'hybrid', 'latency_ms': int((time.monotonic() - start) * 1000)})}\n\n"
    )


@router.post("/query", summary="Query a vault and get a cited answer")
async def query_vault(
    vault_id: UUID,
    body: QueryRequest,
    stream: bool = Query(default=False, description="Stream response as SSE events"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query documents in a vault and receive a grounded, cited answer.

    Pipeline: embed → hybrid search (dense + BM25 + RRF + MMR) → generate.

    When ``stream=true``, returns a ``text/event-stream`` response with
    token-by-token deltas followed by a final structured ``done`` event.
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
        if stream:
            async def _empty_stream() -> AsyncIterator[str]:
                yield f"data: {json.dumps({'type': 'done', 'answer': 'No relevant documents were found for your query.', 'citations': [], 'confidence': 0.0, 'has_sufficient_evidence': False, 'chunks_used': 0, 'retrieval_method': 'hybrid', 'latency_ms': int((time.monotonic() - start) * 1000)})}\n\n"
            return StreamingResponse(_empty_stream(), media_type="text/event-stream")
        return QueryResponse(
            answer="No relevant documents were found for your query.",
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    # Streaming path — SSE with token deltas + final structured event
    if stream:
        return StreamingResponse(
            _sse_stream(body.query, results, start),
            media_type="text/event-stream",
        )

    # Non-streaming path — full JSON response (default, backward-compatible)
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
