import json

from aiokafka import AIOKafkaProducer
from pydantic import BaseModel

from app.core.logger import setup_logger

logger = setup_logger(__name__)


class KafkaProducerError(Exception):
    """Raised when producing to Kafka fails."""
    pass


class KafkaProducer:
    """Async Kafka producer with JSON serialization.

    Wraps aiokafka. Messages are serialized as JSON via Pydantic.
    Keys are UTF-8 encoded strings (typically vault_id for partition affinity).
    """

    def __init__(self, bootstrap_servers: str, client_id: str = "ailways-api") -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            client_id=client_id,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        self._started = False

    async def start(self) -> None:
        """Start the producer and connect to the Kafka cluster.

        Raises:
            KafkaProducerError: If connection fails.
        """
        try:
            await self._producer.start()
            self._started = True
            logger.info("Kafka producer started")
        except Exception as e:
            raise KafkaProducerError(f"Failed to start producer: {e}") from e

    async def stop(self) -> None:
        """Flush pending messages and stop the producer."""
        if self._started:
            await self._producer.stop()
            self._started = False
            logger.info("Kafka producer stopped")

    @property
    def is_connected(self) -> bool:
        """Whether the producer is started and connected."""
        return self._started

    async def send_event(self, topic: str, event: BaseModel, key: str | None = None) -> None:
        """Serialize and send a Pydantic event to a Kafka topic.

        Uses send_and_wait for delivery confirmation. The optional key
        (typically ``str(vault_id)``) controls partition affinity.

        Raises:
            KafkaProducerError: If the produce fails.
        """
        if not self._started:
            raise KafkaProducerError("Producer is not started")

        payload = json.loads(event.model_dump_json())

        try:
            await self._producer.send_and_wait(topic, value=payload, key=key)
            logger.debug(f"Produced event to {topic}: {event.__class__.__name__}")
        except Exception as e:
            logger.error(f"Failed to produce to {topic}: {e}")
            raise KafkaProducerError(f"Failed to produce: {e}") from e
