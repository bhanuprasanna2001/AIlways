import hashlib
from uuid import UUID

from sqlmodel import select, func
from sqlalchemy import delete as sqlalchemy_delete, text, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, File, status
from fastapi.responses import JSONResponse

from app.db import get_db
from app.db.models import User, Document, Chunk
from app.db.models.utils import touch_vault_updated_at
from app.core.config import get_settings
from app.core.utils import utcnow, utcnow_aware
from app.core.auth.deps import get_current_user, require_csrf, require_vault_member
from app.core.storage.local import LocalFileStore
from app.core.kafka.producer import KafkaProducer, KafkaProducerError
from app.core.kafka.topics import FILE_EVENTS, FileUploadedEvent, FileDeletedEvent
from app.core.logger import setup_logger

from app.api.routers.documents.schemas import DocumentResponse, UploadResponse, StatusResponse, ContentResponse


logger = setup_logger(__name__)
router = APIRouter(prefix="/vaults/{vault_id}/documents", tags=["documents"])
SETTINGS = get_settings()

_file_store = LocalFileStore(SETTINGS.FILE_STORE_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_producer(request: Request) -> KafkaProducer | None:
    """Get the Kafka producer from app state, or None if unavailable."""
    producer = getattr(request.app.state, "kafka_producer", None)
    if producer and producer.is_connected and SETTINGS.KAFKA_ENABLED:
        return producer
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/upload", dependencies=[Depends(require_csrf)], summary="Upload a document")
async def upload_document(
    vault_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file for ingestion.

    When Kafka is available: saves file, produces event, returns 202 Accepted.
    When Kafka is disabled: runs synchronous ingestion (Phase 1 fallback).
    """
    vault, _ = await require_vault_member(vault_id, current_user, db, min_role="editor")

    # Validate file type
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Filename is required")

    extension = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if extension not in SETTINGS.ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type: '{extension}'. Allowed: {SETTINGS.ALLOWED_FILE_TYPES}",
        )

    # Read file content
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File is empty")
    if len(content) > SETTINGS.MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File exceeds {SETTINGS.MAX_FILE_SIZE_MB}MB limit",
        )

    # Check for duplicate — advisory lock serialises concurrent uploads of the same file
    file_hash = hashlib.sha256(content).hexdigest()
    lock_key = int(hashlib.md5(f"{vault_id}:{file_hash}".encode()).hexdigest()[:15], 16)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    existing = await db.execute(
        select(Document).where(
            Document.vault_id == vault_id,
            Document.file_hash_sha256 == file_hash,
            Document.deleted_at == None,
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This file already exists in the vault")

    # Remove any soft-deleted record with the same hash to satisfy the DB unique constraint
    stale = await db.execute(
        select(Document).where(
            Document.vault_id == vault_id,
            Document.file_hash_sha256 == file_hash,
            Document.deleted_at != None,
        )
    )
    stale_doc = stale.scalars().first()
    if stale_doc:
        # Delete orphan chunks first (FK constraint prevents deleting the document otherwise)
        await db.execute(
            sqlalchemy_delete(Chunk).where(Chunk.doc_id == stale_doc.id)
        )
        await db.delete(stale_doc)
        await db.flush()

    # Save raw file
    storage_path = f"{vault_id}/{file_hash}/{file.filename}"
    await _file_store.save(storage_path, content)

    # Create document record
    doc = Document(
        vault_id=vault_id,
        uploaded_by=current_user.id,
        original_filename=file.filename,
        file_type=extension,
        file_size_bytes=len(content),
        file_hash_sha256=file_hash,
        storage_path=storage_path,
        status="pending",
    )
    db.add(doc)

    try:
        await db.flush()
        # Touch vault timestamp so "Latest Activity" reflects the upload
        await touch_vault_updated_at(db, vault_id)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        # Clean up the orphan file written before the DB commit
        try:
            await _file_store.delete(storage_path)
        except Exception:
            logger.warning(f"Failed to clean up orphan file: {storage_path}")
        # Race condition: another concurrent upload committed the same hash
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This file already exists in the vault (concurrent upload)",
            )
        raise

    await db.refresh(doc)

    # --- Async path (Kafka available) ---
    producer = _get_producer(request)
    if producer:
        event = FileUploadedEvent(
            doc_id=doc.id,
            vault_id=vault_id,
            file_type=extension,
            storage_path=storage_path,
            original_filename=file.filename,
            uploaded_by=current_user.id,
            timestamp=utcnow_aware(),
        )
        try:
            await producer.send_event(FILE_EVENTS, event, key=str(vault_id))
        except KafkaProducerError:
            logger.warning(f"Kafka unavailable for doc {doc.id} — file saved, will retry via recovery")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="File saved successfully. Background processing temporarily unavailable.",
            )

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=UploadResponse(id=doc.id, original_filename=doc.original_filename, status="pending", chunk_count=0).model_dump(mode="json"),
        )

    # --- Sync fallback (Kafka disabled) ---
    from app.core.rag.ingest import ingest_document
    from app.core.rag.embedding import get_embedder

    try:
        chunk_count = await ingest_document(
            doc_id=doc.id,
            file_content=content,
            filename=file.filename,
            file_type=extension,
            vault_id=vault_id,
            db=db,
            embedder=get_embedder(),
        )
    except Exception as e:
        logger.error(f"Ingestion failed for {doc.id}: {e}")
        await db.refresh(doc)
        return UploadResponse(
            id=doc.id,
            original_filename=doc.original_filename,
            status=doc.status,
            chunk_count=0,
        )

    await db.refresh(doc)
    return UploadResponse(
        id=doc.id,
        original_filename=doc.original_filename,
        status=doc.status,
        chunk_count=chunk_count,
    )


@router.get("", summary="List documents in a vault")
async def list_documents(
    vault_id: UUID,
    response: Response,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=None, ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List non-deleted documents in the vault with optional pagination."""
    await require_vault_member(vault_id, current_user, db)
    effective_limit = min(limit, SETTINGS.PAGINATION_MAX_LIMIT) if limit else SETTINGS.PAGINATION_MAX_LIMIT

    where_clause = [
        Document.vault_id == vault_id,
        Document.deleted_at == None,
    ]

    total = (await db.execute(
        select(func.count()).select_from(Document).where(*where_clause)
    )).scalar() or 0
    response.headers["X-Total-Count"] = str(total)

    result = await db.execute(
        select(Document).where(*where_clause)
        .offset(skip)
        .limit(effective_limit)
    )
    documents = result.scalars().all()

    return [DocumentResponse.model_validate(doc) for doc in documents]


@router.get("/{doc_id}", summary="Get document details")
async def get_document(
    vault_id: UUID,
    doc_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get details for a single document."""
    await require_vault_member(vault_id, current_user, db)

    result = await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.vault_id == vault_id,
            Document.deleted_at == None,
        )
    )
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    return DocumentResponse.model_validate(doc)


@router.get("/{doc_id}/status", summary="Poll document ingestion status")
async def get_document_status(
    vault_id: UUID,
    doc_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Poll the current ingestion status of a document."""
    await require_vault_member(vault_id, current_user, db)

    result = await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.vault_id == vault_id,
        )
    )
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    return StatusResponse(
        id=doc.id,
        status=doc.status,
        error_message=doc.error_message,
        updated_at=doc.updated_at,
    )


@router.get("/{doc_id}/content", summary="Get parsed document content as markdown")
async def get_document_content(
    vault_id: UUID,
    doc_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the raw file from storage, re-parse it to markdown, and return.

    Only active documents can have their content read. The raw file is
    parsed on-demand using the same pipeline that produced the chunks.
    """
    await require_vault_member(vault_id, current_user, db)

    result = await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.vault_id == vault_id,
            Document.deleted_at == None,
        )
    )
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if doc.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document is not ready (status: {doc.status})",
        )

    # Read raw file from storage
    try:
        file_bytes = await _file_store.get(doc.storage_path)
    except (FileNotFoundError, ValueError):
        logger.error(f"File not found on disk for document {doc_id}: {doc.storage_path}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document file not found on disk",
        )

    # Re-parse to markdown
    from app.core.rag.parsing import get_parser

    try:
        parser = get_parser(doc.file_type)
        markdown = await parser.parse(file_bytes, doc.original_filename)
    except (ValueError, Exception) as e:
        logger.error(f"Failed to parse document {doc_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to parse document content",
        )

    return ContentResponse(
        id=doc.id,
        original_filename=doc.original_filename,
        file_type=doc.file_type,
        markdown=markdown,
        char_count=len(markdown),
    )


@router.delete("/{doc_id}", dependencies=[Depends(require_csrf)], summary="Delete a document")
async def delete_document(
    vault_id: UUID,
    doc_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and its chunks.

    When Kafka is available: sets status to pending_delete, produces event.
    When Kafka is disabled: performs synchronous soft-delete.
    """
    await require_vault_member(vault_id, current_user, db, min_role="editor")

    result = await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.vault_id == vault_id,
            Document.deleted_at == None,
        )
    )
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    now = utcnow()

    # --- Async path (Kafka available) ---
    producer = _get_producer(request)
    if producer:
        doc.status = "pending_delete"
        doc.updated_at = now
        db.add(doc)

        # Touch vault timestamp so "Latest Activity" reflects the deletion
        await touch_vault_updated_at(db, vault_id)

        await db.commit()

        event = FileDeletedEvent(
            doc_id=doc_id,
            vault_id=vault_id,
            deleted_by=current_user.id,
            timestamp=utcnow_aware(),
        )
        await producer.send_event(FILE_EVENTS, event, key=str(vault_id))
        return {"message": "Document deletion queued"}

    # --- Sync fallback ---
    doc.status = "deleted"
    doc.deleted_at = now
    doc.updated_at = now
    db.add(doc)

    await db.execute(
        sa_update(Chunk)
        .where(Chunk.doc_id == doc_id, Chunk.is_deleted == False)
        .values(is_deleted=True)
    )

    # Touch vault timestamp so "Latest Activity" reflects the deletion
    await touch_vault_updated_at(db, vault_id)

    await db.commit()
    return {"message": "Document deleted"}


@router.post("/parse", dependencies=[Depends(require_csrf)], summary="Parse a document (preview)")
async def parse_document(
    vault_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse a file and return the extracted markdown without storing anything.

    Useful for previewing parser output before uploading.
    """
    await require_vault_member(vault_id, current_user, db)

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Filename is required",
        )

    extension = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if extension not in SETTINGS.ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type: {extension}. Supported: {', '.join(SETTINGS.ALLOWED_FILE_TYPES)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File is empty",
        )

    from app.core.rag.parsing import get_parser

    try:
        parser = get_parser(extension)
        markdown = await parser.parse(content, file.filename)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    return {
        "filename": file.filename,
        "file_type": extension,
        "markdown": markdown,
        "char_count": len(markdown),
    }
