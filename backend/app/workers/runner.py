"""CLI entry point for running Kafka workers.

Usage:
    python -m app.workers.runner --worker ingestion
    python -m app.workers.runner --worker deletion
    python -m app.workers.runner --worker audit
    python -m app.workers.runner --worker all
"""

import signal
import asyncio
import argparse
from datetime import datetime, timezone, timedelta

from sqlmodel import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import get_settings
from app.core.logger import setup_logger
from app.core.kafka.producer import KafkaProducer
from app.core.kafka.consumer import KafkaConsumer
from app.core.kafka.topics import FILE_EVENTS, AUDIT_EVENTS, FileUploadedEvent
from app.db.models import Document

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Recovery: find documents stuck in pending/ingesting
# ---------------------------------------------------------------------------

async def recover_stuck_documents(
    session_factory: async_sessionmaker[AsyncSession],
    producer: KafkaProducer,
    recovery_minutes: int,
) -> int:
    """Re-produce events for documents stuck in pending/ingesting.

    Args:
        session_factory: Database session factory.
        producer: Kafka producer for re-producing events.
        recovery_minutes: Minutes after which a document is considered stuck.

    Returns:
        int: Number of recovered documents.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=recovery_minutes)

    async with session_factory() as db:
        result = await db.execute(
            select(Document).where(
                Document.status.in_(["pending", "ingesting"]),
                Document.updated_at < cutoff,
                Document.deleted_at == None,
            )
        )
        stuck = result.scalars().all()

        for doc in stuck:
            event = FileUploadedEvent(
                doc_id=doc.id,
                vault_id=doc.vault_id,
                file_type=doc.file_type,
                storage_path=doc.storage_path,
                original_filename=doc.original_filename,
                uploaded_by=doc.uploaded_by,
                timestamp=datetime.now(timezone.utc),
            )
            await producer.send_event(FILE_EVENTS, event, key=str(doc.vault_id))
            logger.info(f"Recovery: re-produced event for stuck document {doc.id}")

        return len(stuck)


# ---------------------------------------------------------------------------
# Worker factory
# ---------------------------------------------------------------------------

def _build_workers(
    worker_type: str,
    settings,
    producer: KafkaProducer,
    session_factory: async_sessionmaker[AsyncSession],
) -> list:
    """Create worker instances based on the requested type.

    Args:
        worker_type: One of "ingestion", "deletion", "audit", or "all".
        settings: Application settings.
        producer: Shared Kafka producer.
        session_factory: Database session factory.

    Returns:
        list: Worker instances to run.
    """
    from app.workers.ingestion_worker import IngestionWorker
    from app.workers.deletion_worker import DeletionWorker
    from app.workers.audit_worker import AuditWorker

    workers = []

    if worker_type in ("ingestion", "all"):
        consumer = KafkaConsumer(
            topics=[FILE_EVENTS],
            group_id=f"{settings.KAFKA_CONSUMER_GROUP}-ingestion",
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            max_poll_interval_ms=settings.KAFKA_CONSUMER_MAX_POLL_INTERVAL_MS,
        )
        workers.append(IngestionWorker(consumer, producer, session_factory))

    if worker_type in ("deletion", "all"):
        consumer = KafkaConsumer(
            topics=[FILE_EVENTS],
            group_id=f"{settings.KAFKA_CONSUMER_GROUP}-deletion",
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            max_poll_interval_ms=settings.KAFKA_CONSUMER_MAX_POLL_INTERVAL_MS,
        )
        workers.append(DeletionWorker(consumer, producer, session_factory))

    if worker_type in ("audit", "all"):
        consumer = KafkaConsumer(
            topics=[AUDIT_EVENTS],
            group_id=f"{settings.KAFKA_CONSUMER_GROUP}-audit",
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            max_poll_interval_ms=settings.KAFKA_CONSUMER_MAX_POLL_INTERVAL_MS,
        )
        workers.append(AuditWorker(consumer, producer, session_factory))

    return workers


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(worker_type: str) -> None:
    """Initialize infrastructure and run the requested workers.

    Args:
        worker_type: One of "ingestion", "deletion", "audit", or "all".
    """
    settings = get_settings()

    # Database
    engine = create_async_engine(settings.ASYNC_DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Kafka producer (shared for DLQ + audit events)
    producer = KafkaProducer(settings.KAFKA_BOOTSTRAP_SERVERS, client_id="ailways-worker")
    await producer.start()

    # Recovery scan for stuck documents
    recovered = await recover_stuck_documents(
        session_factory, producer, settings.KAFKA_RECOVERY_INTERVAL_MINUTES,
    )
    if recovered:
        logger.info(f"Recovered {recovered} stuck document(s)")

    # Build workers
    workers = _build_workers(worker_type, settings, producer, session_factory)
    if not workers:
        logger.error(f"Unknown worker type: {worker_type}")
        return

    # Start consumers
    for w in workers:
        await w._consumer.start()

    # Graceful shutdown on signals
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        for w in workers:
            w.shutdown()
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    # Run all workers concurrently
    logger.info(f"Running worker(s): {worker_type}")
    try:
        await asyncio.gather(*(w.run() for w in workers))
    except asyncio.CancelledError:
        pass
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        for w in workers:
            await w._consumer.stop()
        await producer.stop()
        await engine.dispose()
        logger.info("Worker shutdown complete")


def main() -> None:
    """Parse CLI args and run the worker event loop."""
    parser = argparse.ArgumentParser(description="AIlways Kafka workers")
    parser.add_argument(
        "--worker",
        choices=["ingestion", "deletion", "audit", "all"],
        default="all",
        help="Which worker to run (default: all)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.worker))


if __name__ == "__main__":
    main()
