from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _utcnow_naive() -> datetime:
    """Return current UTC time without tzinfo.

    Returns:
        datetime: Current UTC time without tzinfo.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def touch_vault_updated_at(db: AsyncSession, vault_id: UUID) -> None:
    """Set the vault's updated_at to the current UTC time.

    Call after any vault-level activity (document upload, deletion,
    ingestion completion) to keep the vault timestamp current.

    Safe to call multiple times within the same transaction; the caller
    is responsible for committing.

    Args:
        db: Async database session.
        vault_id: The vault whose timestamp should be refreshed.
    """
    from sqlmodel import select
    from app.db.models.vault import Vault

    result = await db.execute(select(Vault).where(Vault.id == vault_id))
    vault = result.scalars().first()
    if vault:
        vault.updated_at = _utcnow_naive()
        db.add(vault)
        await db.flush()