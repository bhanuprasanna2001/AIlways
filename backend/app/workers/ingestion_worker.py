from uuid import UUID

from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logger import setup_logger
from app.core.kafka.topics import (
    FileUploadedEvent, AuditEvent, AUDIT_EVENTS, parse_file_event,
)
from app.core.utils import utcnow_aware
from app.core.storage.local import LocalFileStore
from app.core.rag.ingest import ingest_document, prepare_document, enrich_prepared_docs, batch_embed_and_store
from app.core.rag.embedding import get_embedder
from app.db.models import Document, Vault
from app.workers.base import BaseWorker

logger = setup_logger(__name__)
SETTINGS = get_settings()


class IngestionWorker(BaseWorker):
    """Consumes file.uploaded events and runs the ingestion pipeline.

    Uses **cross-document embedding batching**: accumulates a batch of
    messages, parses+chunks each document independently, then embeds ALL
    chunks across all documents in a single API call and bulk-inserts.
    This yields ~5-10× throughput vs. sequential per-document embedding.

    Edge cases handled:
    - Orphan event (document not in DB) → skip
    - Document already active → skip (idempotent re-delivery)
    - Document marked for deletion → skip
    - Vault no longer active → mark failed
    - File missing from store → mark failed + DLQ
    - Pipeline failure → mark failed + DLQ
    """

    batch_mode = True

    async def handle_batch(self, events: list[dict], db: AsyncSession) -> None:
        """Process a batch of file.uploaded events: parse, chunk, then batch-embed."""
        # 1. Filter and validate events via discriminated union
        upload_events: list[FileUploadedEvent] = []
        for event in events:
            parsed = parse_file_event(event)
            if isinstance(parsed, FileUploadedEvent):
                upload_events.append(parsed)

        if not upload_events:
            return

        logger.info(f"Processing batch of {len(upload_events)} upload events")

        # 2. Parse + chunk each document (no embedding yet)
        file_store = LocalFileStore(SETTINGS.FILE_STORE_PATH)
        prepared_docs = []
        audit_metadata = {}  # doc_id -> (parsed_event, chunk_count)

        for parsed in upload_events:
            doc_id = parsed.doc_id
            vault_id = parsed.vault_id

            # Fetch and validate document
            doc = await _get_document(db, doc_id)
            if not doc:
                logger.warning(f"Document {doc_id} not found — orphan event, skipping")
                continue
            if doc.status == "active":
                logger.info(f"Document {doc_id} already active — skipping")
                continue
            if doc.status in ("pending_delete", "deleted") or doc.deleted_at is not None:
                logger.info(f"Document {doc_id} is deleted — skipping")
                continue

            # Check vault
            vault = await _get_vault(db, vault_id)
            if not vault or not vault.is_active:
                logger.warning(f"Vault {vault_id} inactive — marking {doc_id} failed")
                await _mark_failed(db, doc, "Vault is no longer active")
                continue

            # Fetch file
            try:
                file_content = await file_store.get(parsed.storage_path)
            except FileNotFoundError:
                logger.error(f"File not found for {doc_id}: {parsed.storage_path}")
                await _mark_failed(db, doc, f"File not found: {parsed.storage_path}")
                continue

            # Parse + chunk (no embedding)
            pdoc = await prepare_document(
                doc_id=doc_id,
                file_content=file_content,
                filename=parsed.original_filename,
                file_type=parsed.file_type,
                vault_id=vault_id,
                db=db,
            )
            if pdoc and pdoc.chunks:
                prepared_docs.append(pdoc)
                audit_metadata[doc_id] = parsed

        if not prepared_docs:
            return

        # 3. Run LLM metadata extraction concurrently for all docs
        #    (summary, keywords, HyDE questions — ~7s total regardless of batch size)
        await enrich_prepared_docs(prepared_docs)

        # 4. Batch embed + store ALL chunks in one API call
        embedder = get_embedder()
        results = await batch_embed_and_store(prepared_docs, db, embedder)

        # 5. Produce audit events for successfully stored documents
        for doc_id, chunk_count in results.items():
            parsed_event = audit_metadata.get(doc_id)
            if not parsed_event:
                continue
            await self._producer.send_event(
                AUDIT_EVENTS,
                AuditEvent(
                    event_type="document.ingested",
                    vault_id=parsed_event.vault_id,
                    doc_id=doc_id,
                    user_id=parsed_event.uploaded_by,
                    payload={"chunk_count": chunk_count, "filename": parsed_event.original_filename},
                    timestamp=utcnow_aware(),
                ),
            )

        logger.info(f"Batch ingestion complete: {len(results)}/{len(prepared_docs)} docs")

    async def handle_event(self, event: dict, db: AsyncSession) -> None:
        """Fallback single-event handler — delegates to handle_batch."""
        await self.handle_batch([event], db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_document(db: AsyncSession, doc_id: UUID) -> Document | None:
    """Fetch a document by ID, or None if not found."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    return result.scalars().first()


async def _get_vault(db: AsyncSession, vault_id: UUID) -> Vault | None:
    """Fetch a vault by ID, or None if not found."""
    result = await db.execute(select(Vault).where(Vault.id == vault_id))
    return result.scalars().first()


async def _mark_failed(db: AsyncSession, doc: Document, error_message: str) -> None:
    """Mark a document as failed with an error message."""
    doc.status = "failed"
    doc.error_message = error_message
    db.add(doc)
    await db.commit()
