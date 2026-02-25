from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.utils import utcnow as _utcnow_naive

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def touch_vault_updated_at(db: AsyncSession, vault_id: UUID) -> None:
    """Set the vault's updated_at to the current UTC time."""
    from sqlmodel import select
    from app.db.models.vault import Vault

    result = await db.execute(select(Vault).where(Vault.id == vault_id))
    vault = result.scalars().first()
    if vault:
        vault.updated_at = _utcnow_naive()
        db.add(vault)
        await db.flush()