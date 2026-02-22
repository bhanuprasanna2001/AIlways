from typing import AsyncGenerator
from contextlib import asynccontextmanager
from tenacity import retry, stop_after_attempt, wait_fixed

from fastapi import FastAPI

from app.db import engine
from app.core.config import get_settings
from app.core.logger import setup_logger

from app.core.tools.redis import init_redis_client

logger = setup_logger(__name__)


@retry(stop=stop_after_attempt(5), wait=wait_fixed(2))
async def check_redis_connection() -> None:
    """Check the connection to Redis by initializing the client.
    
    Args:
        None

    Returns:
        None
    """
    await init_redis_client()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for the FastAPI application to handle startup and shutdown events.

    Args:
        app (FastAPI): The FastAPI application instance.

    Returns:
        AsyncGenerator[None, None]: An asynchronous generator for the lifespan context.
    """
    settings = get_settings()
    logger.info(f"Starting application in {settings.ENV}")

    await check_redis_connection()
    logger.info("Connected to Redis successfully")

    app.state.db_engine = engine
    logger.info("Database engine initialized")

    try:
        yield
    finally:
        if hasattr(app.state, "db_engine") and app.state.db_engine is not None:
            await app.state.db_engine.dispose()
            logger.info("Database engine disposed")
