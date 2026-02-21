from typing import Optional
import redis.asyncio as aioredis
from app.core.config import get_settings

SETTINGS = get_settings()


redis_client: Optional[aioredis.Redis] = None


async def init_redis_client() -> None:
    """Initialize the Redis client.

    Args:
        None

    Returns:
        None
    """
    global redis_client
    redis_client = aioredis.from_url(url=SETTINGS.REDIS_URL, decode_responses=True)
    await redis_client.ping()


async def get_redis_client() -> aioredis.Redis:
    """Get the Redis client instance.

    Args:
        None

    Returns:
        aioredis.Redis: The Redis client instance.
    """
    return redis_client


async def store_session(session_id: str, user_id: str) -> None:
    """Store a session in Redis.

    Args:
        session_id (str): The session ID.
        user_id (str): The user ID associated with the session.

    Returns:
        None
    """
    redis_client = await get_redis_client()
    await redis_client.setex(session_id, SETTINGS.REDIS_SESSION_TTL_SECONDS, user_id)


async def get_session(session_id: str) -> Optional[str]:
    """Get a session from Redis.

    Args:
        session_id (str): The session ID to retrieve.

    Returns:
        Optional[str]: The user ID associated with the session, or None if not found.
    """
    redis_client = await get_redis_client()
    user_id = await redis_client.get(session_id)
    return user_id


async def delete_session(session_id: str) -> None:
    """Delete a session from Redis.

    Args:
        session_id (str): The session ID to delete.

    Returns:
        None
    """
    redis_client = await get_redis_client()
    await redis_client.delete(session_id)