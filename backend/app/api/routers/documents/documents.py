import hashlib
from uuid import UUID
from datetime import datetime, timezone

from sqlmodel import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status

from app.db import get_db
from app.db.models import User, Document, Chunk
from app.core.config import get_settings
from app.core.auth.deps import get_current_user, require_csrf, require_vault_member
from app.core.storage.local import LocalFileStore
from app.core.logger import setup_logger

from app.api.routers.documents.schemas import DocumentResponse, UploadResponse


logger = setup_logger(__name__)
router = APIRouter(prefix="/vaults/{vault_id}/documents", tags=["documents"])
SETTINGS = get_settings()

_file_store = LocalFileStore(SETTINGS.FILE_STORE_PATH)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/upload", dependencies=[Depends(require_csrf)], summary="Upload and ingest a document")
async def upload_document(
    vault_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file, parse, chunk, embed, and store it synchronously.

    Args:
        vault_id: The vault to upload into.
        file: The uploaded file.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        UploadResponse: Document ID, filename, status, and chunk count.
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

    # Check for duplicate
    file_hash = hashlib.sha256(content).hexdigest()
    existing = await db.execute(
        select(Document).where(
            Document.vault_id == vault_id,
            Document.file_hash_sha256 == file_hash,
            Document.deleted_at == None,
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This file already exists in the vault")

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
    await db.commit()
    await db.refresh(doc)

    # Run synchronous ingestion pipeline (Phase 1 — blocking)
    from app.core.rag.ingest import ingest_document
    from app.core.rag.parsing.registry import get_parser
    from app.core.rag.chunking.text_splitter import SentenceChunker
    from app.core.rag.embedding.openai import OpenAIEmbedder

    try:
        parser = get_parser(extension)
        chunker = SentenceChunker()
        embedder = OpenAIEmbedder(
            api_key=SETTINGS.OPENAI_API_KEY,
            model=SETTINGS.OPENAI_EMBEDDING_MODEL,
            dims=SETTINGS.OPENAI_EMBEDDING_DIMENSIONS,
        )

        chunk_count = await ingest_document(
            doc_id=doc.id,
            file_content=content,
            filename=file.filename,
            file_type=extension,
            vault_id=vault_id,
            db=db,
            file_store=_file_store,
            parser=parser,
            chunker=chunker,
            embedder=embedder,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all non-deleted documents in the vault.

    Args:
        vault_id: The vault identifier.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        list[DocumentResponse]: List of documents.
    """
    await require_vault_member(vault_id, current_user, db)

    result = await db.execute(
        select(Document).where(
            Document.vault_id == vault_id,
            Document.deleted_at == None,
        )
    )
    documents = result.scalars().all()

    return [
        DocumentResponse(
            id=doc.id,
            original_filename=doc.original_filename,
            file_type=doc.file_type,
            file_size_bytes=doc.file_size_bytes,
            status=doc.status,
            error_message=doc.error_message,
            page_count=doc.page_count,
            created_at=doc.created_at,
        )
        for doc in documents
    ]


@router.get("/{doc_id}", summary="Get document details")
async def get_document(
    vault_id: UUID,
    doc_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get details for a single document.

    Args:
        vault_id: The vault identifier.
        doc_id: The document identifier.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        DocumentResponse: The document details.
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

    return DocumentResponse(
        id=doc.id,
        original_filename=doc.original_filename,
        file_type=doc.file_type,
        file_size_bytes=doc.file_size_bytes,
        status=doc.status,
        error_message=doc.error_message,
        page_count=doc.page_count,
        created_at=doc.created_at,
    )


@router.delete("/{doc_id}", dependencies=[Depends(require_csrf)], summary="Soft-delete a document")
async def delete_document(
    vault_id: UUID,
    doc_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a document and mark all its chunks as deleted.

    Args:
        vault_id: The vault identifier.
        doc_id: The document identifier.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        dict: Success message.
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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    doc.status = "deleted"
    doc.deleted_at = now
    doc.updated_at = now
    db.add(doc)

    # Mark all chunks as deleted
    result = await db.execute(
        select(Chunk).where(Chunk.doc_id == doc_id, Chunk.is_deleted == False)
    )
    for chunk in result.scalars().all():
        chunk.is_deleted = True
        db.add(chunk)

    await db.commit()
    return {"message": "Document deleted"}
