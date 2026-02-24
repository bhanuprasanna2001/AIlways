from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import setup_logger
from app.core.kafka.topics import (
    EventType, FileDeletedEvent, AuditEvent, AUDIT_EVENTS, utcnow,
)
from app.db.models import Document, Chunk
from app.db.models.utils import _utcnow_naive
from app.workers.base import BaseWorker

logger = setup_logger(__name__)


class DeletionWorker(BaseWorker):
    """Consumes file.deleted events and soft-deletes documents + chunks.

    Edge cases handled:
    - Orphan event (document not in DB) → skip
    - Already deleted → skip (idempotent)
    - No chunks exist yet (delete before ingestion) → harmless bulk update
    """

    async def handle_event(self, event: dict, db: AsyncSession) -> None:
        """Process a single event from the file.events topic.

        Only handles file.deleted events.

        Args:
            event: Deserialized JSON event payload.
            db: Database session (scoped to this event).
        """
        event_type = event.get("event_type")
        if event_type != EventType.FILE_DELETED:
            return

        parsed = FileDeletedEvent(**event)
        doc_id = parsed.doc_id

        logger.info(f"Processing deletion for document {doc_id}")

        # 1. Fetch document
        result = await db.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalars().first()

        if not doc:
            logger.warning(f"Document {doc_id} not found — orphan event, skipping")
            return

        # 2. Idempotency: already deleted
        if doc.status == "deleted" and doc.deleted_at is not None:
            logger.info(f"Document {doc_id} already deleted — skipping")
            return

        # 3. Soft-delete the document
        now = _utcnow_naive()
        doc.status = "deleted"
        doc.deleted_at = now
        doc.updated_at = now
        db.add(doc)

        # 4. Mark all chunks as deleted
        chunk_result = await db.execute(
            select(Chunk).where(Chunk.doc_id == doc_id, Chunk.is_deleted == False)
        )
        chunks = chunk_result.scalars().all()
        chunk_count = len(chunks)
        for chunk in chunks:
            chunk.is_deleted = True
            db.add(chunk)

        await db.commit()
        logger.info(f"Deleted document {doc_id} and {chunk_count} chunk(s)")

        # 5. Produce audit event
        await self._producer.send_event(
            AUDIT_EVENTS,
            AuditEvent(
                event_type="document.deleted",
                vault_id=parsed.vault_id,
                doc_id=doc_id,
                user_id=parsed.deleted_by,
                payload={"chunks_deleted": chunk_count},
                timestamp=utcnow(),
            ),
        )
