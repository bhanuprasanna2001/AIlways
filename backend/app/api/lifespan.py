from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.db import engine
from app.core.logger import setup_logger
from app.core.config import get_settings


logger = setup_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    logger.info(f"Starting application with settings: {settings.dict()}")

    app.state.db_engine = engine
    logger.info("Database engine initialized")

    try:
        yield
    finally:
        if hasattr(app.state, "db_engine") and app.state.db_engine is not None:
            await app.state.db_engine.dispose()
            logger.info("Database engine disposed")

