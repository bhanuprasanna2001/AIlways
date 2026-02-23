import json
import asyncio
from typing import Callable, Awaitable

from aiokafka import AIOKafkaConsumer

from app.core.kafka.dlq import DLQHandler
from app.core.logger import setup_logger

logger = setup_logger(__name__)


class KafkaConsumer:
    """Async Kafka consumer with manual offset commit and DLQ routing.

    Supports two consumption modes:
      - ``consume()``: per-message handler (original behaviour).
      - ``consume_batches()``: batch handler — accumulates up to
        ``max_batch_size`` messages (or waits ``batch_timeout_s``),
        then calls the handler with the full list. Offsets are committed
        once after the entire batch succeeds.
    """

    def __init__(
        self,
        topics: list[str],
        group_id: str,
        bootstrap_servers: str,
        max_poll_interval_ms: int = 600000,
        max_batch_size: int = 20,
        batch_timeout_s: float = 2.0,
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
        self._max_batch_size = max_batch_size
        self._batch_timeout_s = batch_timeout_s

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
        poll_timeout_ms: int = 500,
    ) -> None:
        """Main consume loop.

        For each message:
        1. Deserialize JSON value (already done by aiokafka).
        2. Call handler(event_dict).
        3. Commit offset on success.
        4. On handler failure: route to DLQ, commit offset, continue.
        5. On deserialization failure: route to DLQ, commit offset, continue.

        Uses getmany() with a short timeout so the shutdown flag is checked
        even when no messages are arriving, making Ctrl-C / SIGTERM respond
        immediately instead of hanging until the next message.

        Args:
            handler: Async callable that processes a single event dict.
            dlq: Dead letter queue handler for failed events.
            poll_timeout_ms: Max milliseconds to wait for messages per poll.
        """
        try:
            while self._running:
                records = await self._consumer.getmany(
                    timeout_ms=poll_timeout_ms, max_records=10
                )
                for tp, messages in records.items():
                    for msg in messages:
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

    async def consume_batches(
        self,
        handler: Callable[[list[dict]], Awaitable[None]],
        dlq: DLQHandler,
        poll_timeout_ms: int = 200,
    ) -> None:
        """Batch consume loop — accumulates messages then calls handler with the full list.

        Accumulates up to ``max_batch_size`` messages or waits up to
        ``batch_timeout_s`` seconds (whichever comes first), then calls
        the handler with all accumulated events. Offsets are committed
        once after the batch handler returns.

        If the batch handler fails, all events in the batch are routed
        to the DLQ and offsets are still committed to avoid re-processing
        the same poison-pill batch forever.

        Args:
            handler: Async callable that processes a list of event dicts.
            dlq: Dead letter queue handler for failed batches.
            poll_timeout_ms: Max milliseconds per poll iteration.
        """
        try:
            while self._running:
                batch: list[dict] = []
                deadline = asyncio.get_event_loop().time() + self._batch_timeout_s

                # Accumulate until batch is full or timeout expires
                while len(batch) < self._max_batch_size and self._running:
                    remaining_ms = max(
                        int((deadline - asyncio.get_event_loop().time()) * 1000),
                        0,
                    )
                    if remaining_ms <= 0:
                        break

                    timeout = min(poll_timeout_ms, remaining_ms)
                    records = await self._consumer.getmany(
                        timeout_ms=timeout,
                        max_records=self._max_batch_size - len(batch),
                    )
                    for tp, messages in records.items():
                        for msg in messages:
                            if msg.value and isinstance(msg.value, dict):
                                batch.append(msg.value)

                if not batch:
                    continue

                logger.debug(f"Batch of {len(batch)} messages ready")

                try:
                    await handler(batch)
                except Exception as e:
                    logger.error(f"Batch handler failed for {len(batch)} events: {e}")
                    for event in batch:
                        await dlq.send(
                            original_topic="file.events",
                            original_event=event,
                            error=e,
                        )

                # Commit after batch (success or DLQ)
                await self._consumer.commit()

        except Exception as e:
            if self._running:
                logger.error(f"Batch consumer loop error: {e}")
                raise

    def request_shutdown(self) -> None:
        """Signal the consume loop to stop after the current message."""
        self._running = False
