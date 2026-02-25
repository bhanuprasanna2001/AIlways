"""Session persistence — DB writes for live transcription sessions.

All writes use ``get_db_session()`` so each operation gets its own
short-lived connection — safe for long-running WebSocket handlers
that outlive the request-scoped session.

Segments are buffered in memory and flushed in batches to reduce
DB round-trips during high-frequency transcription.
"""

from __future__ import annotations

import json
import time
from uuid import UUID

from sqlmodel import select, func

from app.core.claims.base import Claim
from app.core.transcription.base import TranscriptSegment
from app.core.utils import utcnow
from app.db import get_db_session
from app.db.models.transcription_session import TranscriptionSession
from app.db.models.transcription_segment import TranscriptionSegment as SegmentModel
from app.db.models.transcription_claim import TranscriptionClaim
from app.core.logger import setup_logger

logger = setup_logger(__name__)


class SessionPersistence:
    """Manages database persistence for a live transcription session.

    Segments are buffered in memory and flushed in configurable batches.
    Claims are persisted individually (low volume, needs immediate ID).
    """

    def __init__(self, vault_id: UUID, user_id: UUID) -> None:
        self.vault_id = vault_id
        self.user_id = user_id
        self.session_id: UUID | None = None

        self._segment_buffer: list[TranscriptSegment] = []
        self._segment_index: int = 0
        self._speakers: set[int] = set()
        self._started_at: float = time.monotonic()

    # Session lifecycle -------------------------------------------------------

    async def create_session(self) -> None:
        """Create a new transcription session row in the database."""
        now = utcnow()
        session = TranscriptionSession(
            vault_id=self.vault_id,
            user_id=self.user_id,
            title=f"Session {now.strftime('%Y-%m-%d %H:%M')}",
            status="recording",
            started_at=now,
        )
        async with get_db_session() as db:
            db.add(session)
            await db.commit()
            await db.refresh(session)
        self.session_id = session.id

    async def finalize_session(self) -> float:
        """Mark session as completed and compute aggregate stats."""
        await self.flush_segments()
        duration = time.monotonic() - self._started_at
        now = utcnow()

        async with get_db_session() as db:
            session = (
                await db.execute(
                    select(TranscriptionSession).where(
                        TranscriptionSession.id == self.session_id
                    )
                )
            ).scalars().first()
            if session is None:
                logger.error(f"Session {self.session_id} not found for finalization")
                return duration

            seg_count = (
                await db.execute(
                    select(func.count()).where(
                        SegmentModel.session_id == self.session_id
                    )
                )
            ).scalar() or 0

            claim_count = (
                await db.execute(
                    select(func.count()).where(
                        TranscriptionClaim.session_id == self.session_id
                    )
                )
            ).scalar() or 0

            session.status = "completed"
            session.duration_seconds = round(duration, 2)
            session.ended_at = now
            session.segment_count = seg_count
            session.claim_count = claim_count
            session.speaker_count = len(self._speakers)
            await db.commit()

        logger.info(
            f"Session {self.session_id} finalized: "
            f"{seg_count} segments, {claim_count} claims, {duration:.1f}s"
        )
        return duration

    async def fail_session(self) -> None:
        """Mark session as failed."""
        if self.session_id is None:
            return
        now = utcnow()
        async with get_db_session() as db:
            session = (
                await db.execute(
                    select(TranscriptionSession).where(
                        TranscriptionSession.id == self.session_id
                    )
                )
            ).scalars().first()
            if session:
                session.status = "failed"
                session.ended_at = now
                await db.commit()

    # Segment buffering -------------------------------------------------------

    def buffer_segment(self, segment: TranscriptSegment) -> None:
        """Add a final segment to the in-memory buffer for later DB flush."""
        if not segment.is_final:
            return
        self._speakers.add(segment.speaker)
        self._segment_buffer.append(segment)

    async def flush_segments(self) -> None:
        """Write buffered segments to the database and clear the buffer.

        On failure the segments are put back so they can be retried.
        """
        if not self._segment_buffer or self.session_id is None:
            return

        batch = self._segment_buffer[:]
        self._segment_buffer.clear()

        rows = [
            SegmentModel(
                session_id=self.session_id,
                text=seg.text,
                speaker=seg.speaker,
                start=seg.start,
                end=seg.end,
                confidence=seg.confidence,
                segment_index=self._segment_index + i,
            )
            for i, seg in enumerate(batch)
        ]
        self._segment_index += len(rows)

        try:
            async with get_db_session() as db:
                db.add_all(rows)
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to flush {len(rows)} segments: {e}")
            self._segment_buffer = batch + self._segment_buffer
            self._segment_index -= len(rows)

    # Claim persistence -------------------------------------------------------

    async def persist_claim(self, claim: Claim) -> UUID:
        """Insert a detected claim with ``pending`` verdict. Returns the DB row ID."""
        row = TranscriptionClaim(
            session_id=self.session_id,
            text=claim.text,
            speaker=claim.speaker,
            timestamp_start=claim.timestamp_start,
            timestamp_end=claim.timestamp_end,
            context=claim.context,
            verdict="pending",
        )
        async with get_db_session() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return row.id

    async def update_verdict(
        self,
        claim_id: UUID,
        verdict: str,
        confidence: float,
        explanation: str,
        evidence: list[dict] | None,
    ) -> None:
        """Update a claim row with the verification result."""
        now = utcnow()
        async with get_db_session() as db:
            row = (
                await db.execute(
                    select(TranscriptionClaim).where(
                        TranscriptionClaim.id == claim_id
                    )
                )
            ).scalars().first()
            if row:
                row.verdict = verdict
                row.confidence = confidence
                row.explanation = explanation
                if evidence:
                    safe = [
                        e.model_dump() if hasattr(e, "model_dump") else e
                        for e in evidence
                    ]
                    row.evidence_json = json.dumps(safe)
                else:
                    row.evidence_json = None
                row.updated_at = now
                await db.commit()
