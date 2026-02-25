from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.kafka.consumer import KafkaConsumer
from app.core.kafka.producer import KafkaProducer
from app.core.kafka.dlq import DLQHandler
from app.core.logger import setup_logger

logger = setup_logger(__name__)


class BaseWorker(ABC):
    """Abstract base for Kafka consumer workers.

    Subclasses implement ``handle_event()`` for single-event processing.
    Set ``batch_mode = True`` and override ``handle_batch()`` for
    cross-event optimisations (e.g. batched embedding).
    """

    batch_mode: bool = False

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

    @abstractmethod
    async def handle_event(self, event: dict, db: AsyncSession) -> None:
        """Process a single event. Override in subclasses."""
        ...

    async def handle_batch(self, events: list[dict], db: AsyncSession) -> None:
        """Process a batch of events. Override when ``batch_mode = True``."""
        for event in events:
            await self.handle_event(event, db)

    async def _dispatch(self, event: dict) -> None:
        async with self._session_factory() as db:
            await self.handle_event(event, db)

    async def _dispatch_batch(self, events: list[dict]) -> None:
        async with self._session_factory() as db:
            await self.handle_batch(events, db)

    async def run(self) -> None:
        """Start consuming events from Kafka."""
        logger.info(f"{self.__class__.__name__} starting (batch_mode={self.batch_mode})")
        if self.batch_mode:
            await self._consumer.consume_batches(
                handler=self._dispatch_batch, dlq=self._dlq,
            )
        else:
            await self._consumer.consume(handler=self._dispatch, dlq=self._dlq)

    def shutdown(self) -> None:
        """Signal the consumer to stop after the current event."""
        logger.info(f"{self.__class__.__name__} shutting down")
        self._consumer.request_shutdown()
