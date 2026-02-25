from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import setup_logger
from app.core.kafka.topics import (
    FileDeletedEvent, AuditEvent, AUDIT_EVENTS, parse_file_event,
)
from app.core.utils import utcnow, utcnow_aware
from app.db.models import Document, Chunk
from app.db.models.utils import touch_vault_updated_at
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
        """Process a single file.deleted event."""
        parsed = parse_file_event(event)
        if not isinstance(parsed, FileDeletedEvent):
            return

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
        now = utcnow()
        doc.status = "deleted"
        doc.deleted_at = now
        doc.updated_at = now
        db.add(doc)

        # 4. Mark all chunks as deleted (bulk update)
        from sqlalchemy import update as sa_update
        chunk_count_result = await db.execute(
            sa_update(Chunk)
            .where(Chunk.doc_id == doc_id, Chunk.is_deleted == False)
            .values(is_deleted=True)
        )
        chunk_count = chunk_count_result.rowcount

        # 5. Touch vault so "Latest Activity" reflects the deletion
        await touch_vault_updated_at(db, parsed.vault_id)

        await db.commit()
        logger.info(f"Deleted document {doc_id} and {chunk_count} chunk(s)")

        # 6. Produce audit event
        await self._producer.send_event(
            AUDIT_EVENTS,
            AuditEvent(
                event_type="document.deleted",
                vault_id=parsed.vault_id,
                doc_id=doc_id,
                user_id=parsed.deleted_by,
                payload={"chunks_deleted": chunk_count},
                timestamp=utcnow_aware(),
            ),
        )
