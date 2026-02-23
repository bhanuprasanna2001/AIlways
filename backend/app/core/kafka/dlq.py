from datetime import datetime, timezone

from pydantic import BaseModel

from app.core.kafka.producer import KafkaProducer, KafkaProducerError
from app.core.kafka.topics import INGESTION_DLQ, DLQEnvelope
from app.core.logger import setup_logger

logger = setup_logger(__name__)


class DLQHandler:
    """Routes failed events to the dead letter queue topic.

    Failures in DLQ production itself are logged but never re-raised —
    a broken DLQ must not crash the consumer.
    """

    def __init__(self, producer: KafkaProducer, dlq_topic: str = INGESTION_DLQ) -> None:
        self._producer = producer
        self._dlq_topic = dlq_topic

    async def send(
        self,
        original_topic: str,
        original_event: dict,
        error: Exception,
        retry_count: int = 0,
    ) -> None:
        """Send a failed event to the dead letter queue.

        Args:
            original_topic: The topic the event originally came from.
            original_event: The raw event payload that failed.
            error: The exception that caused the failure.
            retry_count: How many times processing has been attempted.
        """
        envelope = DLQEnvelope(
            original_topic=original_topic,
            original_event=original_event,
            error_message=str(error),
            error_type=type(error).__name__,
            retry_count=retry_count,
            failed_at=datetime.now(timezone.utc),
        )

        try:
            await self._producer.send_event(self._dlq_topic, envelope)
            logger.info(f"Sent failed event to DLQ: {type(error).__name__}: {error}")
        except KafkaProducerError as e:
            # DLQ failure must never crash the consumer
            logger.error(f"Failed to send to DLQ (dropping silently): {e}")
