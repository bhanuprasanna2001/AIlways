from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


def _utcnow_naive() -> datetime:
    """Return current UTC time without tzinfo.

    Returns:
        datetime: Current UTC time without tzinfo.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Vault(SQLModel, table=True):
    __tablename__ = "vaults"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID = Field(foreign_key="users.id", nullable=False)
    name: str = Field(max_length=255, nullable=False)
    description: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow_naive)
    updated_at: datetime = Field(default_factory=_utcnow_naive)
    deleted_at: datetime | None = Field(default=None)


class VaultMember(SQLModel, table=True):
    __tablename__ = "vault_members"

    vault_id: UUID = Field(foreign_key="vaults.id", primary_key=True, nullable=False)
    user_id: UUID = Field(foreign_key="users.id", primary_key=True, nullable=False)
    role: str = Field(default="viewer", max_length=20, nullable=False)
    joined_at: datetime = Field(default_factory=_utcnow_naive)
