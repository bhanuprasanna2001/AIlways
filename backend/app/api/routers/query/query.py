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
from app.core.copilot import query_vault_agent, stream_vault_agent
from app.core.rag.generation import Citation
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
    vault_id: UUID,
    history_dicts: list[dict[str, str]] | None,
    top_k: int,
    start: float,
) -> AsyncIterator[str]:
    """Yield Server-Sent Events from the copilot agent stream.

    Event types:
      - ``retrieval``: agent called a search tool, includes ``chunks_used``.
      - ``token``: raw LLM content delta.
      - ``done``: final structured response with answer, citations, etc.
      - ``error``: generation failed, includes fallback answer.
    """
    try:
        async for event in stream_vault_agent(
            query=query,
            vault_id=vault_id,
            history=history_dicts,
            top_k=top_k,
        ):
            event_type = event.get("type", "")

            if event_type == "retrieval":
                yield f"data: {json.dumps({'type': 'retrieval', 'chunks_used': event.get('chunks_used', 0)})}\n\n"

            elif event_type == "token":
                yield f"data: {json.dumps({'type': 'token', 'content': event.get('content', '')})}\n\n"

            elif event_type == "done":
                event["latency_ms"] = int((time.monotonic() - start) * 1000)
                yield f"data: {json.dumps(event)}\n\n"

            elif event_type == "error":
                event["latency_ms"] = int((time.monotonic() - start) * 1000)
                yield f"data: {json.dumps(event)}\n\n"

    except Exception as e:
        logger.error(f"SSE stream error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'answer': 'Stream failed.', 'confidence': 0.0})}\n\n"


@router.post("/query", summary="Query a vault and get a cited answer")
async def query_vault(
    vault_id: UUID,
    body: QueryRequest,
    stream: bool = Query(default=False, description="Stream response as SSE events"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query documents in a vault using the agentic copilot.

    Pipeline (LangGraph ReAct agent):
        1. Rewrite query (resolve pronouns/coreferences from history)
        2. Agent decides which tools to use:
           - ``search_documents``: hybrid search (dense + BM25 + RRF + MMR)
           - ``lookup_entity``: direct SQL lookup for entity IDs
        3. Agent may do multiple search rounds if needed
        4. Generate grounded answer with citations

    When ``stream=true``, returns a ``text/event-stream`` response with
    token-by-token deltas followed by a final structured ``done`` event.
    """
    await require_vault_member(vault_id, current_user, db)

    start = time.monotonic()

    # Convert history to plain dicts for the copilot module
    history_dicts: list[dict[str, str]] | None = None
    if body.history:
        history_dicts = [{"role": m.role, "content": m.content} for m in body.history]

    # Streaming path — SSE with token deltas from the agent
    if stream:
        return StreamingResponse(
            _sse_stream(body.query, vault_id, history_dicts, body.top_k, start),
            media_type="text/event-stream",
        )

    # Non-streaming path — full JSON response
    # The agent manages its own timeout internally (COPILOT.AGENT_TIMEOUT_S)
    # and returns a graceful CopilotAnswer on expiry. The outer guard here
    # uses a slightly larger budget so the inner handler fires first,
    # avoiding a raw 504 that hides the agent's partial-answer fallback.
    agent_timeout = SETTINGS.COPILOT.AGENT_TIMEOUT_S + 10.0
    try:
        answer = await asyncio.wait_for(
            query_vault_agent(
                query=body.query,
                vault_id=vault_id,
                history=history_dicts,
                top_k=body.top_k,
            ),
            timeout=agent_timeout,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Query timed out",
        )

    return QueryResponse(
        answer=answer.answer,
        citations=[
            Citation(
                doc_title=e.doc_title,
                section=e.section,
                page=e.page,
                quote=e.quote,
            )
            for e in answer.citations
        ],
        confidence=answer.confidence,
        has_sufficient_evidence=answer.has_sufficient_evidence,
        chunks_used=len(answer.citations),
        retrieval_method="agent",
        latency_ms=int((time.monotonic() - start) * 1000),
    )
