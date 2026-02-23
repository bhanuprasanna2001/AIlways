from uuid import UUID

from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logger import setup_logger
from app.core.kafka.topics import (
    EventType, FileUploadedEvent, AuditEvent, AUDIT_EVENTS, utcnow,
)
from app.core.storage.local import LocalFileStore
from app.core.rag.ingest import ingest_document
from app.core.rag.parsing.registry import get_parser
from app.core.rag.chunking.hierarchical import HierarchicalChunker
from app.core.rag.embedding.openai import OpenAIEmbedder
from app.db.models import Document, Vault
from app.workers.base import BaseWorker

logger = setup_logger(__name__)
SETTINGS = get_settings()


class IngestionWorker(BaseWorker):
    """Consumes file.uploaded events and runs the ingestion pipeline.

    Edge cases handled:
    - Orphan event (document not in DB) → skip
    - Document already active → skip (idempotent re-delivery)
    - Document marked for deletion → skip
    - Vault no longer active → mark failed
    - File missing from store → mark failed + DLQ
    - Pipeline failure → mark failed + DLQ
    """

    async def handle_event(self, event: dict, db: AsyncSession) -> None:
        """Process a single event from the file.events topic.

        Only handles file.uploaded events. Other event types are ignored
        so multiple consumer groups can share the topic.

        Args:
            event: Deserialized JSON event payload.
            db: Database session (scoped to this event).
        """
        event_type = event.get("event_type")
        if event_type != EventType.FILE_UPLOADED:
            return

        parsed = FileUploadedEvent(**event)
        doc_id = parsed.doc_id
        vault_id = parsed.vault_id

        logger.info(f"Processing ingestion for document {doc_id}")

        # 1. Fetch document
        doc = await _get_document(db, doc_id)
        if not doc:
            logger.warning(f"Document {doc_id} not found — orphan event, skipping")
            return

        # 2. Idempotency checks
        if doc.status == "active":
            logger.info(f"Document {doc_id} already active — skipping (duplicate event)")
            return

        if doc.status in ("pending_delete", "deleted") or doc.deleted_at is not None:
            logger.info(f"Document {doc_id} is deleted/pending_delete — skipping")
            return

        # 3. Check vault is still active
        vault = await _get_vault(db, vault_id)
        if not vault or not vault.is_active:
            logger.warning(f"Vault {vault_id} is inactive — marking document {doc_id} failed")
            await _mark_failed(db, doc, "Vault is no longer active")
            return

        # 4. Fetch file from store
        file_store = LocalFileStore(SETTINGS.FILE_STORE_PATH)
        try:
            file_content = await file_store.get(parsed.storage_path)
        except FileNotFoundError:
            logger.error(f"File not found for document {doc_id}: {parsed.storage_path}")
            await _mark_failed(db, doc, f"File not found: {parsed.storage_path}")
            raise  # Propagates to DLQ via BaseWorker

        # 5. Run ingestion pipeline
        try:
            parser = get_parser(parsed.file_type)
            chunker = HierarchicalChunker()
            embedder = OpenAIEmbedder(
                api_key=SETTINGS.OPENAI_API_KEY,
                model=SETTINGS.OPENAI_EMBEDDING_MODEL,
                dims=SETTINGS.OPENAI_EMBEDDING_DIMENSIONS,
            )

            # Re-check deletion before committing (race condition guard)
            await db.refresh(doc)
            if doc.deleted_at is not None or doc.status in ("pending_delete", "deleted"):
                logger.info(f"Document {doc_id} was deleted during ingestion — aborting")
                return

            chunk_count = await ingest_document(
                doc_id=doc_id,
                file_content=file_content,
                filename=parsed.original_filename,
                file_type=parsed.file_type,
                vault_id=vault_id,
                db=db,
                file_store=file_store,
                parser=parser,
                chunker=chunker,
                embedder=embedder,
            )

            logger.info(f"Ingestion complete for {doc_id}: {chunk_count} chunks")

        except Exception as e:
            logger.error(f"Ingestion failed for {doc_id}: {e}")
            # ingest_document already marks status='failed' on error
            raise  # Propagates to DLQ via BaseWorker

        # 6. Produce audit event
        await self._producer.send_event(
            AUDIT_EVENTS,
            AuditEvent(
                event_type="document.ingested",
                vault_id=vault_id,
                doc_id=doc_id,
                user_id=parsed.uploaded_by,
                payload={"chunk_count": chunk_count, "filename": parsed.original_filename},
                timestamp=utcnow(),
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_document(db: AsyncSession, doc_id: UUID) -> Document | None:
    """Fetch a document by ID.

    Args:
        db: Database session.
        doc_id: The document ID.

    Returns:
        Document or None if not found.
    """
    result = await db.execute(select(Document).where(Document.id == doc_id))
    return result.scalars().first()


async def _get_vault(db: AsyncSession, vault_id: UUID) -> Vault | None:
    """Fetch a vault by ID.

    Args:
        db: Database session.
        vault_id: The vault ID.

    Returns:
        Vault or None if not found.
    """
    result = await db.execute(select(Vault).where(Vault.id == vault_id))
    return result.scalars().first()


async def _mark_failed(db: AsyncSession, doc: Document, error_message: str) -> None:
    """Mark a document as failed with an error message.

    Args:
        db: Database session.
        doc: The document to update.
        error_message: Description of what went wrong.
    """
    doc.status = "failed"
    doc.error_message = error_message
    db.add(doc)
    await db.commit()
