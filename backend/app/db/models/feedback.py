from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


def _utcnow_naive() -> datetime:
    """Return current UTC time without tzinfo.

    Returns:
        datetime: Current UTC time without tzinfo.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Feedback(SQLModel, table=True):
    __tablename__ = "feedback"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    vault_id: UUID = Field(foreign_key="vaults.id", nullable=False)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    query_text: str = Field(nullable=False)
    alert_text: str | None = Field(default=None)
    rating: str = Field(max_length=10, nullable=False)
    comment: str | None = Field(default=None)
    trace_id: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(default_factory=_utcnow_naive)
