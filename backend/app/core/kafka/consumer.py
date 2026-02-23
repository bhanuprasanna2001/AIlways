import json
from typing import Callable, Awaitable

from aiokafka import AIOKafkaConsumer

from app.core.kafka.dlq import DLQHandler
from app.core.logger import setup_logger

logger = setup_logger(__name__)


class KafkaConsumer:
    """Async Kafka consumer with manual offset commit and DLQ routing.

    Deserializes JSON messages, calls the handler, and commits offsets
    only after successful processing. Malformed or failed events are
    routed to the DLQ so the consumer never blocks on a poison pill.
    """

    def __init__(
        self,
        topics: list[str],
        group_id: str,
        bootstrap_servers: str,
        max_poll_interval_ms: int = 600000,
    ) -> None:
        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            max_poll_interval_ms=max_poll_interval_ms,
        )
        self._running = False

    async def start(self) -> None:
        """Start the consumer and subscribe to topics.

        Raises:
            Exception: If connection to Kafka fails.
        """
        await self._consumer.start()
        self._running = True
        logger.info("Kafka consumer started")

    async def stop(self) -> None:
        """Commit final offsets and stop the consumer."""
        self._running = False
        await self._consumer.stop()
        logger.info("Kafka consumer stopped")

    async def consume(
        self,
        handler: Callable[[dict], Awaitable[None]],
        dlq: DLQHandler,
    ) -> None:
        """Main consume loop.

        For each message:
        1. Deserialize JSON value (already done by aiokafka).
        2. Call handler(event_dict).
        3. Commit offset on success.
        4. On handler failure: route to DLQ, commit offset, continue.
        5. On deserialization failure: route to DLQ, commit offset, continue.

        Args:
            handler: Async callable that processes a single event dict.
            dlq: Dead letter queue handler for failed events.
        """
        try:
            async for msg in self._consumer:
                if not self._running:
                    break

                event = msg.value
                topic = msg.topic

                try:
                    await handler(event)
                except Exception as e:
                    logger.error(f"Handler failed for event on {topic}: {e}")
                    await dlq.send(
                        original_topic=topic,
                        original_event=event if isinstance(event, dict) else {"raw": str(event)},
                        error=e,
                    )

                # Commit after processing (success or DLQ routing)
                await self._consumer.commit()
        except Exception as e:
            if self._running:
                logger.error(f"Consumer loop error: {e}")
                raise

    def request_shutdown(self) -> None:
        """Signal the consume loop to stop after the current message."""
        self._running = False
