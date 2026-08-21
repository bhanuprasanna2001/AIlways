from typing import Optional
import redis.asyncio as aioredis
from app.core.config import get_settings
from app.core.logger import setup_logger

SETTINGS = get_settings()
logger = setup_logger(__name__)


redis_client: Optional[aioredis.Redis] = None


async def init_redis_client() -> None:
    """Initialize the Redis client and verify connectivity."""
    global redis_client
    redis_client = aioredis.from_url(url=SETTINGS.REDIS_URL, decode_responses=True)
    await redis_client.ping()


async def get_redis_client() -> aioredis.Redis:
    """Get the Redis client, reconnecting if the connection dropped."""
    global redis_client
    if redis_client is None:
        await init_redis_client()
        return redis_client
    try:
        await redis_client.ping()
    except Exception:
        logger.warning("Redis connection lost — reconnecting")
        await init_redis_client()
    return redis_client


async def redis_health_check() -> bool:
    """Return True if Redis is reachable, False otherwise."""
    try:
        client = await get_redis_client()
        await client.ping()
        return True
    except Exception:
        return False


async def store_session(session_id: str, user_id: str) -> None:
    """Store a session in Redis with TTL."""
    redis_client = await get_redis_client()
    await redis_client.setex(session_id, SETTINGS.REDIS_SESSION_TTL_SECONDS, user_id)


async def get_session(session_id: str) -> Optional[str]:
    """Get the user ID for a session, or None if expired/missing."""
    redis_client = await get_redis_client()
    user_id = await redis_client.get(session_id)
    return user_id


async def delete_session(session_id: str) -> None:
    """Delete a session from Redis."""
    redis_client = await get_redis_client()
    await redis_client.delete(session_id)


async def store_ws_ticket(ticket: str, user_id: str) -> None:
    """Store a one-time WS ticket in Redis with TTL."""
    client = await get_redis_client()
    await client.setex(f"ws_ticket:{ticket}", SETTINGS.TRANSCRIPTION.WS_TICKET_TTL_S, user_id)


async def consume_ws_ticket(ticket: str) -> Optional[str]:
    """Consume a one-time WS ticket atomically. Returns user ID or None."""
    client = await get_redis_client()
    pipe = client.pipeline()
    pipe.get(f"ws_ticket:{ticket}")
    pipe.delete(f"ws_ticket:{ticket}")
    results = await pipe.execute()
    return results[0]