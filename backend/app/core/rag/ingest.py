from uuid import UUID
from datetime import datetime, timezone

from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, Chunk
from app.core.storage.base import FileStore
from app.core.rag.parsing.base import Parser
from app.core.rag.chunking.base import Chunker
from app.core.rag.embedding.base import Embedder
from app.core.rag.embedding.batch import batch_embed
from app.core.rag.exceptions import IngestionError
from app.core.logger import setup_logger

logger = setup_logger(__name__)


async def ingest_document(
    doc_id: UUID,
    file_content: bytes,
    filename: str,
    file_type: str,
    vault_id: UUID,
    db: AsyncSession,
    file_store: FileStore,
    parser: Parser,
    chunker: Chunker,
    embedder: Embedder,
) -> int:
    """Orchestrate the full ingestion pipeline: parse → chunk → embed → store.

    Args:
        doc_id: The document ID (already created in the DB).
        file_content: Raw file bytes.
        filename: Original filename.
        file_type: Lowercase file extension.
        vault_id: The vault this document belongs to.
        db: The database session.
        file_store: File storage backend.
        parser: Document parser.
        chunker: Document chunker.
        embedder: Embedding service.

    Returns:
        int: Number of chunks created.

    Raises:
        IngestionError: If any step in the pipeline fails.
    """
    try:
        # 1. Mark as ingesting
        await _set_status(db, doc_id, "ingesting")

        # 2. Parse
        logger.info(f"Parsing document {doc_id} ({filename})")
        parsed = await parser.parse(file_content, filename)

        # 3. Save parsed IR to file store
        ir_path = f"{vault_id}/{doc_id}/parsed.json"
        await file_store.save(ir_path, parsed.model_dump_json().encode("utf-8"))

        # 4. Update document metadata
        doc = await _get_doc(db, doc_id)
        doc.parsed_ir_path = ir_path
        doc.page_count = parsed.metadata.page_count
        doc.updated_at = _now()
        db.add(doc)
        await db.flush()

        # 5. Chunk
        logger.info(f"Chunking document {doc_id}")
        chunk_data_list = chunker.chunk(parsed)

        if not chunk_data_list:
            await _set_status(db, doc_id, "failed", error_message="No text content could be extracted")
            raise IngestionError("No text content could be extracted")

        # 6. Embed
        logger.info(f"Embedding {len(chunk_data_list)} chunks for document {doc_id}")
        texts = [c.content_with_header for c in chunk_data_list]
        hashes = [c.content_hash for c in chunk_data_list]

        # Check existing chunk hashes for idempotent re-embedding
        existing_hashes = await _get_existing_hashes(db, doc_id)
        embeddings = await batch_embed(texts, hashes, embedder, existing_hashes)

        # 7. Upsert chunks in a single transaction
        logger.info(f"Storing {len(chunk_data_list)} chunks for document {doc_id}")
        for cd, embedding in zip(chunk_data_list, embeddings):
            # Check if chunk already exists (re-ingestion)
            result = await db.execute(
                select(Chunk).where(
                    Chunk.doc_id == doc_id,
                    Chunk.chunk_index == cd.chunk_index,
                    Chunk.chunk_version == 1,
                )
            )
            existing_chunk = result.scalars().first()

            if existing_chunk:
                # Update if content changed
                if existing_chunk.content_hash != cd.content_hash:
                    existing_chunk.content = cd.content
                    existing_chunk.content_with_header = cd.content_with_header
                    existing_chunk.content_hash = cd.content_hash
                    existing_chunk.token_count = cd.token_count
                    existing_chunk.section_heading = cd.section_heading
                    existing_chunk.page_number = cd.page_number
                    existing_chunk.char_start = cd.char_start
                    existing_chunk.char_end = cd.char_end
                    if embedding is not None:
                        existing_chunk.embedding = embedding
                    db.add(existing_chunk)
            else:
                chunk = Chunk(
                    doc_id=doc_id,
                    vault_id=vault_id,
                    content=cd.content,
                    content_with_header=cd.content_with_header,
                    content_hash=cd.content_hash,
                    token_count=cd.token_count,
                    chunk_index=cd.chunk_index,
                    section_heading=cd.section_heading,
                    page_number=cd.page_number,
                    char_start=cd.char_start,
                    char_end=cd.char_end,
                    embedding=embedding,
                    chunk_version=1,
                )
                db.add(chunk)

        # 8. Mark as active
        await _set_status(db, doc_id, "active")
        await db.commit()

        logger.info(f"Ingestion complete for {doc_id}: {len(chunk_data_list)} chunks")
        return len(chunk_data_list)

    except IngestionError:
        raise
    except Exception as e:
        logger.error(f"Ingestion failed for {doc_id}: {e}")
        await db.rollback()
        await _set_status(db, doc_id, "failed", error_message=str(e))
        await db.commit()
        raise IngestionError(f"Ingestion failed: {e}") from e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Return current UTC time without tzinfo."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _get_doc(db: AsyncSession, doc_id: UUID) -> Document:
    """Fetch a document by ID.

    Args:
        db: The database session.
        doc_id: The document ID.

    Returns:
        Document: The document instance.
    """
    result = await db.execute(select(Document).where(Document.id == doc_id))
    return result.scalars().one()


async def _set_status(db: AsyncSession, doc_id: UUID, status: str, error_message: str | None = None) -> None:
    """Update the document status.

    Args:
        db: The database session.
        doc_id: The document ID.
        status: The new status value.
        error_message: Optional error message (for 'failed' status).
    """
    doc = await _get_doc(db, doc_id)
    doc.status = status
    doc.error_message = error_message
    doc.updated_at = _now()
    db.add(doc)
    await db.flush()


async def _get_existing_hashes(db: AsyncSession, doc_id: UUID) -> set[str]:
    """Get content hashes for existing chunks of a document.

    Args:
        db: The database session.
        doc_id: The document ID.

    Returns:
        set[str]: Set of content hashes that already have embeddings.
    """
    result = await db.execute(
        select(Chunk.content_hash).where(
            Chunk.doc_id == doc_id,
            Chunk.embedding != None,
            Chunk.is_deleted == False,
        )
    )
    return set(result.scalars().all())
