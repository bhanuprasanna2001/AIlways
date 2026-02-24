"""Ingestion orchestrator.

Coordinates the full ingestion pipeline for a single document:

    parse → chunk → embed → store

Designed for both Kafka-driven (async worker) and sync-fallback
(direct API call) paths. The caller provides the database session
and embedder so that resources can be shared across invocations.

Supports two modes:
  - **Single-doc:** ``ingest_document()`` — parse, chunk, embed, store
    in one call. Used by the sync-fallback path.
  - **Batch-optimised:** ``prepare_document()`` + ``batch_embed_and_store()``
    — parse+chunk up-front for N documents, then embed ALL chunks across
    all documents in a single OpenAI API call and bulk-insert. Used by
    the batching ingestion worker for ~5-10× throughput.

Key design decisions:
  - Parsing runs in a thread pool (CPU-bound pdfplumber).
  - All chunks for a document are bulk-inserted in one flush.
  - Embeddings use the shared embedder instance (connection reuse).
  - On failure, the document is marked 'failed' and the error is recorded.
  - A pre-flight deletion check prevents wasted work if the document
    was deleted while queued.
"""

from __future__ import annotations

from uuid import UUID
from dataclasses import dataclass, field

from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, Chunk
from app.db.models.utils import touch_vault_updated_at
from app.core.utils import utcnow
from app.core.rag.parsing import get_parser
from app.core.rag.chunking import get_chunker
from app.core.rag.chunking.base import ChunkData
from app.core.rag.embedding.base import Embedder
from app.core.rag.exceptions import IngestionError
from app.core.logger import setup_logger

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures for batch processing
# ---------------------------------------------------------------------------

@dataclass
class PreparedDoc:
    """A document that has been parsed and chunked, ready for embedding.

    Attributes:
        doc_id: Database document UUID.
        vault_id: Owning vault UUID.
        chunks: List of ChunkData from the chunker.
    """
    doc_id: UUID
    vault_id: UUID
    chunks: list[ChunkData] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def ingest_document(
    doc_id: UUID,
    file_content: bytes,
    filename: str,
    file_type: str,
    vault_id: UUID,
    db: AsyncSession,
    embedder: Embedder,
) -> int:
    """Run the full ingestion pipeline for one document.

    Args:
        doc_id: Document UUID (must already exist in DB with status 'pending').
        file_content: Raw file bytes.
        filename: Original filename.
        file_type: Lowercase file extension ('pdf', 'txt', 'md').
        vault_id: Vault that owns this document.
        db: Async database session (caller manages the transaction boundary
            for the sync-fallback path; the worker path commits here).
        embedder: Shared embedder instance (satisfies Embedder protocol).

    Returns:
        int: Number of chunks created.

    Raises:
        IngestionError: If any stage fails. The document status is set to
            'failed' with the error message before re-raising.
    """
    try:
        # Mark document as ingesting
        await _set_status(db, doc_id, "ingesting")

        # 1. Parse
        parser = get_parser(file_type)
        markdown = await parser.parse(file_content, filename)

        # 2. Chunk
        chunker = get_chunker()
        chunk_data = chunker.chunk(markdown, filename)

        if not chunk_data:
            await _set_status(db, doc_id, "failed", error_message="No text content could be extracted")
            raise IngestionError("No text content could be extracted")

        # 3. Pre-flight check — abort if document was deleted while queued
        doc = await _get_doc(db, doc_id)
        if doc.deleted_at is not None or doc.status in ("pending_delete", "deleted"):
            logger.info(f"Document {doc_id} was deleted during ingestion — aborting")
            return 0

        # 4. Embed — batch all chunks in one API call
        texts = [cd.content_with_header for cd in chunk_data]
        embeddings = await embedder.embed_documents(texts)

        # 5. Bulk insert chunks
        chunk_records = [
            Chunk(
                doc_id=doc_id,
                vault_id=vault_id,
                content=cd.content,
                content_with_header=cd.content_with_header,
                content_hash=cd.content_hash,
                token_count=cd.token_count,
                chunk_index=cd.chunk_index,
                chunk_type="child",
                embedding=emb,
                chunk_version=1,
            )
            for cd, emb in zip(chunk_data, embeddings)
        ]
        db.add_all(chunk_records)
        await db.flush()

        # 6. Update document metadata
        doc = await _get_doc(db, doc_id)
        doc.status = "active"
        doc.error_message = None
        doc.updated_at = _now()
        db.add(doc)

        # 7. Touch vault so "Latest Activity" reflects ingestion completion
        await touch_vault_updated_at(db, vault_id)

        await db.commit()

        logger.info(f"Ingestion complete for {doc_id}: {len(chunk_records)} chunks")
        return len(chunk_records)

    except IngestionError:
        raise
    except Exception as e:
        logger.error(f"Ingestion failed for {doc_id}: {e}")
        await db.rollback()
        await _set_status(db, doc_id, "failed", error_message=str(e)[:500])
        await db.commit()
        raise IngestionError(f"Ingestion failed: {e}") from e


# ---------------------------------------------------------------------------
# Batch-optimised API (for worker)
# ---------------------------------------------------------------------------

async def prepare_document(
    doc_id: UUID,
    file_content: bytes,
    filename: str,
    file_type: str,
    vault_id: UUID,
    db: AsyncSession,
) -> PreparedDoc | None:
    """Parse and chunk a document without embedding.

    This is the first stage of the two-phase batch pipeline. Multiple
    documents can be prepared independently, then all their chunks are
    embedded together in a single ``batch_embed_and_store()`` call.

    Args:
        doc_id: Document UUID.
        file_content: Raw file bytes.
        filename: Original filename.
        file_type: File extension.
        vault_id: Owning vault UUID.
        db: Async database session.

    Returns:
        PreparedDoc with parsed chunks, or None if skipped (deleted, empty).
    """
    try:
        await _set_status(db, doc_id, "ingesting")

        # Parse
        parser = get_parser(file_type)
        markdown = await parser.parse(file_content, filename)

        # Chunk
        chunker = get_chunker()
        chunk_data = chunker.chunk(markdown, filename)

        if not chunk_data:
            await _set_status(db, doc_id, "failed", error_message="No text content could be extracted")
            return None

        # Pre-flight deletion check
        doc = await _get_doc(db, doc_id)
        if doc.deleted_at is not None or doc.status in ("pending_delete", "deleted"):
            logger.info(f"Document {doc_id} was deleted during ingestion — aborting")
            return None

        return PreparedDoc(doc_id=doc_id, vault_id=vault_id, chunks=chunk_data)

    except Exception as e:
        logger.error(f"Prepare failed for {doc_id}: {e}")
        try:
            await db.rollback()
            await _set_status(db, doc_id, "failed", error_message=str(e)[:500])
            await db.commit()
        except Exception:
            pass
        return None


async def batch_embed_and_store(
    prepared: list[PreparedDoc],
    db: AsyncSession,
    embedder: Embedder,
) -> dict[UUID, int]:
    """Embed and store chunks for multiple documents in one API call.

    Collects all chunk texts across all prepared documents, calls the
    embedder once, then bulk-inserts all Chunk records and marks each
    document as active.

    Args:
        prepared: List of PreparedDoc from ``prepare_document()``.
        db: Async database session.
        embedder: Shared embedder instance.

    Returns:
        dict mapping doc_id → chunk count for successfully stored documents.
    """
    if not prepared:
        return {}

    # Flatten all texts and track ownership
    all_texts: list[str] = []
    doc_offsets: list[tuple[PreparedDoc, int, int]] = []  # (prepared, start, end)

    for pdoc in prepared:
        start = len(all_texts)
        all_texts.extend(cd.content_with_header for cd in pdoc.chunks)
        end = len(all_texts)
        doc_offsets.append((pdoc, start, end))

    total_chunks = len(all_texts)
    logger.info(f"Batch embedding {total_chunks} chunks across {len(prepared)} documents")

    # Single embedding API call for ALL chunks
    try:
        all_embeddings = await embedder.embed_documents(all_texts)
    except Exception as e:
        logger.error(f"Batch embedding failed: {e}")
        # Mark all docs as failed
        for pdoc in prepared:
            try:
                await _set_status(db, pdoc.doc_id, "failed", error_message=f"Embedding failed: {e}"[:500])
            except Exception:
                pass
        await db.commit()
        return {}

    # Build and store chunks per document (using savepoints for isolation)
    results: dict[UUID, int] = {}
    for pdoc, start, end in doc_offsets:
        try:
            async with db.begin_nested():
                doc_embeddings = all_embeddings[start:end]
                chunk_records = [
                    Chunk(
                        doc_id=pdoc.doc_id,
                        vault_id=pdoc.vault_id,
                        content=cd.content,
                        content_with_header=cd.content_with_header,
                        content_hash=cd.content_hash,
                        token_count=cd.token_count,
                        chunk_index=cd.chunk_index,
                        chunk_type="child",
                        embedding=emb,
                        chunk_version=1,
                    )
                    for cd, emb in zip(pdoc.chunks, doc_embeddings)
                ]
                db.add_all(chunk_records)
                await db.flush()

                doc = await _get_doc(db, pdoc.doc_id)
                doc.status = "active"
                doc.error_message = None
                doc.updated_at = _now()
                db.add(doc)

            results[pdoc.doc_id] = len(chunk_records)

        except Exception as e:
            logger.error(f"Store failed for {pdoc.doc_id}: {e}")
            # Savepoint rollback is automatic — only this doc's changes are undone
            try:
                await _set_status(db, pdoc.doc_id, "failed", error_message=str(e)[:500])
            except Exception:
                pass

    # Touch vault timestamps for all vaults that had successful ingestions
    updated_vault_ids = {pdoc.vault_id for pdoc, _, _ in doc_offsets if pdoc.doc_id in results}
    for vid in updated_vault_ids:
        await touch_vault_updated_at(db, vid)

    await db.commit()
    logger.info(f"Batch complete: {len(results)}/{len(prepared)} documents stored, {total_chunks} chunks")
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_doc(db: AsyncSession, doc_id: UUID) -> Document:
    """Fetch a Document by ID. Raises if not found."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalars().one_or_none()
    if doc is None:
        raise IngestionError(f"Document {doc_id} not found in database")
    return doc


async def _set_status(
    db: AsyncSession,
    doc_id: UUID,
    status: str,
    error_message: str | None = None,
) -> None:
    """Update the status of a document.

    Args:
        db: Database session.
        doc_id: Document UUID.
        status: New status string.
        error_message: Optional error message.
    """
    doc = await _get_doc(db, doc_id)
    doc.status = status
    doc.error_message = error_message
    doc.updated_at = utcnow()
    db.add(doc)
    await db.flush()
