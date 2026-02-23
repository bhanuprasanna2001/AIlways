from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.kafka.consumer import KafkaConsumer
from app.core.kafka.producer import KafkaProducer
from app.core.kafka.dlq import DLQHandler
from app.core.logger import setup_logger

logger = setup_logger(__name__)


class BaseWorker(ABC):
    """Abstract base for Kafka consumer workers.

    Each event gets its own DB session — no session leaks across events.
    Subclasses implement handle_event() with the business logic.
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

    @abstractmethod
    async def handle_event(self, event: dict, db: AsyncSession) -> None:
        """Process a single event. Override in subclasses.

        Args:
            event: Deserialized JSON event payload.
            db: A fresh database session for this event.
        """
        ...

    async def _dispatch(self, event: dict) -> None:
        """Wrap handle_event with a database session.

        Args:
            event: Deserialized JSON event payload.
        """
        async with self._session_factory() as db:
            await self.handle_event(event, db)

    async def run(self) -> None:
        """Start consuming events from Kafka."""
        logger.info(f"{self.__class__.__name__} starting")
        await self._consumer.consume(handler=self._dispatch, dlq=self._dlq)

    def shutdown(self) -> None:
        """Signal the consumer to stop after the current event."""
        logger.info(f"{self.__class__.__name__} shutting down")
        self._consumer.request_shutdown()
