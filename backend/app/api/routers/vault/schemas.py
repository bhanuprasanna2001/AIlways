from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class VaultCreate(BaseModel):
    name: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Vault name cannot be empty")
        if len(v) > 255:
            raise ValueError("Vault name must be at most 255 characters")
        return v


class VaultUpdate(BaseModel):
    name: str | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Vault name cannot be empty")
        if len(v) > 255:
            raise ValueError("Vault name must be at most 255 characters")
        return v


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class VaultResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    is_active: bool
    role: str
    document_count: int
    created_at: datetime
    updated_at: datetime
