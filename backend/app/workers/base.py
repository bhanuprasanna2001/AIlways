from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.kafka.consumer import KafkaConsumer
from app.core.kafka.producer import KafkaProducer
from app.core.kafka.dlq import DLQHandler
from app.core.logger import setup_logger

logger = setup_logger(__name__)


class BaseWorker(ABC):
    """Abstract base for Kafka consumer workers.

    Supports two processing modes:
      - **Single-event:** ``handle_event()`` processes one event at a time.
        Each event gets its own DB session.
      - **Batch-event:** ``handle_batch()`` processes a list of events
        together. All events share one DB session. Subclasses that
        override ``handle_batch()`` enable cross-event optimisations
        (e.g. cross-document embedding batching).

    The default ``handle_batch()`` falls back to calling ``handle_event()``
    for each event, preserving backwards compatibility.
    """

    def __init__(
        self,
        consumer: KafkaConsumer,
        producer: KafkaProducer,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._consumer = consumer
        self._producer = producer
        self._session_factory = session_factory
        self._dlq = DLQHandler(producer)
        self._batch_mode = hasattr(type(self), 'handle_batch') and type(self).handle_batch is not BaseWorker.handle_batch

    @abstractmethod
    async def handle_event(self, event: dict, db: AsyncSession) -> None:
        """Process a single event. Override in subclasses."""
        ...

    async def handle_batch(self, events: list[dict], db: AsyncSession) -> None:
        """Process a batch of events. Override for batch optimisations.

        Default implementation calls handle_event() sequentially.
        """
        for event in events:
            await self.handle_event(event, db)

    async def _dispatch(self, event: dict) -> None:
        """Wrap handle_event with a database session (single-event mode)."""
        async with self._session_factory() as db:
            await self.handle_event(event, db)

    async def _dispatch_batch(self, events: list[dict]) -> None:
        """Wrap handle_batch with a database session (batch mode)."""
        async with self._session_factory() as db:
            await self.handle_batch(events, db)

    async def run(self) -> None:
        """Start consuming events from Kafka."""
        logger.info(f"{self.__class__.__name__} starting (batch_mode={self._batch_mode})")
        if self._batch_mode:
            await self._consumer.consume_batches(
                handler=self._dispatch_batch, dlq=self._dlq,
            )
        else:
            await self._consumer.consume(handler=self._dispatch, dlq=self._dlq)

    def shutdown(self) -> None:
        """Signal the consumer to stop after the current event."""
        logger.info(f"{self.__class__.__name__} shutting down")
        self._consumer.request_shutdown()
