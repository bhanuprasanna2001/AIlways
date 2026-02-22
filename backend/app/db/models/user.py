from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


def _utcnow_naive() -> datetime:
    """Return current UTC time without tzinfo (matches TIMESTAMP WITHOUT TIME ZONE).

    Args:
        None

    Returns:
        datetime: Current UTC time without tzinfo.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    email: str = Field(unique=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow_naive)