from uuid import UUID
from datetime import datetime
from sqlmodel import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.db import get_db
from app.db.models import User, Vault, VaultMember, Document
from app.core.utils import utcnow
from app.core.config import get_settings
from app.core.auth.deps import get_current_user, require_csrf, require_vault_member
from app.api.routers.vault.schemas import VaultCreate, VaultUpdate, VaultResponse


router = APIRouter(prefix="/vaults", tags=["vaults"])
SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _vault_response(vault: Vault, role: str, db: AsyncSession) -> VaultResponse:
    """Build a VaultResponse with document count and effective updated_at.

    Computes an effective ``updated_at`` as the latest of the vault's own
    timestamp and the most recently modified document.
    """
    result = await db.execute(
        select(
            func.count(Document.id),
            func.max(Document.updated_at),
        ).where(
            Document.vault_id == vault.id,
            Document.deleted_at == None,
        )
    )
    row = result.one()
    doc_count: int = row[0] or 0
    latest_doc_activity: datetime | None = row[1]

    # Effective updated_at: latest of vault-level and doc-level activity
    effective_updated = vault.updated_at
    if latest_doc_activity and latest_doc_activity > vault.updated_at:
        effective_updated = latest_doc_activity

    return VaultResponse(
        id=vault.id,
        name=vault.name,
        description=vault.description,
        is_active=vault.is_active,
        role=role,
        document_count=doc_count,
        created_at=vault.created_at,
        updated_at=effective_updated,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", dependencies=[Depends(require_csrf)], summary="Create a new vault")
async def create_vault(
    body: VaultCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new vault. The caller becomes the owner."""
    vault = Vault(owner_id=current_user.id, name=body.name, description=body.description)
    db.add(vault)
    await db.flush()

    member = VaultMember(vault_id=vault.id, user_id=current_user.id, role="owner")
    db.add(member)
    await db.commit()
    await db.refresh(vault)

    return await _vault_response(vault, "owner", db)


@router.get("", summary="List vaults the current user is a member of")
async def list_vaults(
    response: Response,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=None, ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all vaults where the current user holds membership."""
    effective_limit = min(limit, SETTINGS.PAGINATION_MAX_LIMIT) if limit else SETTINGS.PAGINATION_MAX_LIMIT

    where_clause = [
        VaultMember.user_id == current_user.id,
        Vault.is_active == True,
        Vault.deleted_at == None,
    ]

    total = (await db.execute(
        select(func.count())
        .select_from(Vault)
        .join(VaultMember, VaultMember.vault_id == Vault.id)
        .where(*where_clause)
    )).scalar() or 0
    response.headers["X-Total-Count"] = str(total)

    result = await db.execute(
        select(Vault, VaultMember.role)
        .join(VaultMember, VaultMember.vault_id == Vault.id)
        .where(*where_clause)
        .offset(skip)
        .limit(effective_limit)
    )
    rows = result.all()
    return [await _vault_response(vault, role, db) for vault, role in rows]


@router.get("/{vault_id}", summary="Get vault details")
async def get_vault(
    vault_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get details for a single vault."""
    vault, member = await require_vault_member(vault_id, current_user, db)
    return await _vault_response(vault, member.role, db)


@router.patch("/{vault_id}", dependencies=[Depends(require_csrf)], summary="Update vault")
async def update_vault(
    vault_id: UUID,
    body: VaultUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update vault name or description. Requires editor or owner role."""
    vault, member = await require_vault_member(vault_id, current_user, db, min_role="editor")

    if body.name is not None:
        vault.name = body.name
    if body.description is not None:
        vault.description = body.description

    vault.updated_at = utcnow()

    db.add(vault)
    await db.commit()
    await db.refresh(vault)

    return await _vault_response(vault, member.role, db)


@router.delete("/{vault_id}", dependencies=[Depends(require_csrf)], summary="Soft-delete vault")
async def delete_vault(
    vault_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a vault. Only the owner can do this."""
    vault, _ = await require_vault_member(vault_id, current_user, db, min_role="owner")

    vault.is_active = False
    vault.deleted_at = utcnow()

    db.add(vault)
    await db.commit()

    return {"message": "Vault deleted"}
