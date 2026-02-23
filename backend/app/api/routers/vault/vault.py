from uuid import UUID
from sqlmodel import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status

from app.db import get_db
from app.db.models import User, Vault, VaultMember, Document
from app.core.auth.deps import get_current_user, require_csrf, require_vault_member
from app.api.routers.vault.schemas import VaultCreate, VaultUpdate, VaultResponse


router = APIRouter(prefix="/vaults", tags=["vaults"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _vault_response(vault: Vault, role: str, db: AsyncSession) -> VaultResponse:
    """Build a VaultResponse with the document count.

    Args:
        vault: The vault model instance.
        role: The caller's role in this vault.
        db: The database session.

    Returns:
        VaultResponse: The serialized vault with doc count.
    """
    result = await db.execute(
        select(func.count(Document.id)).where(
            Document.vault_id == vault.id,
            Document.deleted_at == None,
        )
    )
    doc_count = result.scalar() or 0

    return VaultResponse(
        id=vault.id,
        name=vault.name,
        description=vault.description,
        is_active=vault.is_active,
        role=role,
        document_count=doc_count,
        created_at=vault.created_at,
        updated_at=vault.updated_at,
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
    """Create a new vault. The caller becomes the owner.

    Args:
        body: Vault creation payload.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        VaultResponse: The created vault.
    """
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all vaults where the current user holds membership.

    Args:
        current_user: The authenticated user.
        db: The database session.

    Returns:
        list[VaultResponse]: List of vaults.
    """
    result = await db.execute(
        select(Vault, VaultMember.role)
        .join(VaultMember, VaultMember.vault_id == Vault.id)
        .where(
            VaultMember.user_id == current_user.id,
            Vault.is_active == True,
            Vault.deleted_at == None,
        )
    )
    rows = result.all()
    return [await _vault_response(vault, role, db) for vault, role in rows]


@router.get("/{vault_id}", summary="Get vault details")
async def get_vault(
    vault_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get details for a single vault.

    Args:
        vault_id: The vault identifier.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        VaultResponse: The vault details.
    """
    vault, member = await require_vault_member(vault_id, current_user, db)
    return await _vault_response(vault, member.role, db)


@router.patch("/{vault_id}", dependencies=[Depends(require_csrf)], summary="Update vault")
async def update_vault(
    vault_id: UUID,
    body: VaultUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update vault name or description. Requires editor or owner role.

    Args:
        vault_id: The vault identifier.
        body: Fields to update.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        VaultResponse: The updated vault.
    """
    vault, member = await require_vault_member(vault_id, current_user, db, min_role="editor")

    if body.name is not None:
        vault.name = body.name
    if body.description is not None:
        vault.description = body.description

    from datetime import datetime, timezone
    vault.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

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
    """Soft-delete a vault. Only the owner can do this.

    Args:
        vault_id: The vault identifier.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        dict: Success message.
    """
    vault, _ = await require_vault_member(vault_id, current_user, db, min_role="owner")

    from datetime import datetime, timezone
    vault.is_active = False
    vault.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.add(vault)
    await db.commit()

    return {"message": "Vault deleted"}
