"""Session CRUD router — list, detail, rename, delete transcription sessions."""

from __future__ import annotations

import json as json_mod
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db import get_db
from app.db.models import User, Vault
from app.db.models.transcription_session import TranscriptionSession
from app.db.models.transcription_segment import TranscriptionSegment
from app.db.models.transcription_claim import TranscriptionClaim
from app.core.auth.deps import get_current_user, require_csrf
from app.core.logger import setup_logger

from app.api.routers.transcription.schemas import (
    SessionListResponse,
    SessionDetailResponse,
    SessionSegmentResponse,
    SessionClaimResponse,
    SessionUpdateRequest,
)

logger = setup_logger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", summary="List transcription sessions for the current user")
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionListResponse]:
    """List all transcription sessions owned by the current user.

    Returns sessions in reverse chronological order (newest first).
    Soft-deleted sessions are excluded.

    Args:
        current_user: The authenticated user.
        db: The database session.

    Returns:
        list[SessionListResponse]: Summary list of sessions.
    """
    result = await db.execute(
        select(TranscriptionSession, Vault.name)
        .join(Vault, Vault.id == TranscriptionSession.vault_id)
        .where(
            TranscriptionSession.user_id == current_user.id,
            TranscriptionSession.deleted_at == None,  # noqa: E711
        )
        .order_by(TranscriptionSession.started_at.desc())
    )
    rows = result.all()

    return [
        SessionListResponse(
            id=session.id,
            vault_id=session.vault_id,
            vault_name=vault_name,
            title=session.title,
            status=session.status,
            duration_seconds=session.duration_seconds,
            speaker_count=session.speaker_count,
            segment_count=session.segment_count,
            claim_count=session.claim_count,
            started_at=session.started_at,
            ended_at=session.ended_at,
        )
        for session, vault_name in rows
    ]


@router.get("/{session_id}", summary="Get transcription session detail")
async def get_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    """Get full details for a transcription session including segments and claims.

    Args:
        session_id: The session identifier.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        SessionDetailResponse: Full session with segments and claims.
    """
    result = await db.execute(
        select(TranscriptionSession, Vault.name)
        .join(Vault, Vault.id == TranscriptionSession.vault_id)
        .where(
            TranscriptionSession.id == session_id,
            TranscriptionSession.user_id == current_user.id,
            TranscriptionSession.deleted_at == None,  # noqa: E711
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    session, vault_name = row

    # Fetch segments ordered by index
    seg_result = await db.execute(
        select(TranscriptionSegment)
        .where(TranscriptionSegment.session_id == session_id)
        .order_by(TranscriptionSegment.segment_index)
    )
    segments = seg_result.scalars().all()

    # Fetch claims ordered by timestamp
    claim_result = await db.execute(
        select(TranscriptionClaim)
        .where(TranscriptionClaim.session_id == session_id)
        .order_by(TranscriptionClaim.timestamp_start)
    )
    claims = claim_result.scalars().all()

    return SessionDetailResponse(
        id=session.id,
        vault_id=session.vault_id,
        vault_name=vault_name,
        title=session.title,
        status=session.status,
        duration_seconds=session.duration_seconds,
        speaker_count=session.speaker_count,
        segment_count=session.segment_count,
        claim_count=session.claim_count,
        started_at=session.started_at,
        ended_at=session.ended_at,
        segments=[
            SessionSegmentResponse(
                id=s.id,
                text=s.text,
                speaker=s.speaker,
                start=s.start,
                end=s.end,
                confidence=s.confidence,
                segment_index=s.segment_index,
            )
            for s in segments
        ],
        claims=[
            SessionClaimResponse(
                id=c.id,
                text=c.text,
                speaker=c.speaker,
                timestamp_start=c.timestamp_start,
                timestamp_end=c.timestamp_end,
                context=c.context,
                verdict=c.verdict,
                confidence=c.confidence,
                explanation=c.explanation,
                evidence=json_mod.loads(c.evidence_json) if c.evidence_json else [],
            )
            for c in claims
        ],
    )


@router.patch(
    "/{session_id}",
    dependencies=[Depends(require_csrf)],
    summary="Update session title",
)
async def update_session(
    session_id: UUID,
    body: SessionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    """Rename a transcription session.

    Args:
        session_id: The session identifier.
        body: Request body with the new title.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        SessionListResponse: Updated session summary.
    """
    result = await db.execute(
        select(TranscriptionSession, Vault.name)
        .join(Vault, Vault.id == TranscriptionSession.vault_id)
        .where(
            TranscriptionSession.id == session_id,
            TranscriptionSession.user_id == current_user.id,
            TranscriptionSession.deleted_at == None,  # noqa: E711
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    session, vault_name = row
    session.title = body.title
    await db.commit()
    await db.refresh(session)

    return SessionListResponse(
        id=session.id,
        vault_id=session.vault_id,
        vault_name=vault_name,
        title=session.title,
        status=session.status,
        duration_seconds=session.duration_seconds,
        speaker_count=session.speaker_count,
        segment_count=session.segment_count,
        claim_count=session.claim_count,
        started_at=session.started_at,
        ended_at=session.ended_at,
    )


@router.delete(
    "/{session_id}",
    dependencies=[Depends(require_csrf)],
    summary="Delete a transcription session",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a transcription session.

    Args:
        session_id: The session identifier.
        current_user: The authenticated user.
        db: The database session.
    """
    from datetime import datetime, timezone

    result = await db.execute(
        select(TranscriptionSession).where(
            TranscriptionSession.id == session_id,
            TranscriptionSession.user_id == current_user.id,
            TranscriptionSession.deleted_at == None,  # noqa: E711
        )
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    session.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
