import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import setup_logger
from app.core.kafka.topics import AuditEvent
from app.db.models import AuditLog
from app.workers.base import BaseWorker

logger = setup_logger(__name__)


class AuditWorker(BaseWorker):
    """Consumes audit.events and writes them to the audit_log table.

    Simple fire-and-forget: producers emit audit events without
    waiting for the DB write. This worker persists them asynchronously.
    """

    async def handle_event(self, event: dict, db: AsyncSession) -> None:
        """Insert an audit event into the audit_log table.

        Args:
            event: Deserialized JSON event payload.
            db: Database session (scoped to this event).
        """
        parsed = AuditEvent(**event)

        record = AuditLog(
            event_type=parsed.event_type,
            vault_id=parsed.vault_id,
            doc_id=parsed.doc_id,
            user_id=parsed.user_id,
            payload=json.dumps(parsed.payload) if parsed.payload else None,
            latency_ms=parsed.latency_ms,
        )
        db.add(record)
        await db.commit()

        logger.debug(f"Audit logged: {parsed.event_type}")
