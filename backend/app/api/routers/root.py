from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.tools.redis import redis_health_check


class HealthResponse(BaseModel):
    """Health check response schema."""

    title: str
    version: str
    description: str
    status: str
    redis: str
    database: str


router = APIRouter()

SETTINGS = get_settings()


@router.get("/health", response_model=HealthResponse, tags=["Health Check"], summary="Check application health")
async def health(request: Request):
    """Check application health including Redis and database connectivity."""
    redis_ok = await redis_health_check()

    db_ok = False
    db_engine = getattr(request.app.state, "db_engine", None)
    if db_engine:
        try:
            from sqlalchemy import text as sa_text
            async with db_engine.connect() as conn:
                await conn.execute(sa_text("SELECT 1"))
            db_ok = True
        except Exception:
            pass

    overall = "healthy" if (redis_ok and db_ok) else "degraded"

    return HealthResponse(
        title=SETTINGS.APP_TITLE,
        version=SETTINGS.APP_VERSION,
        description=SETTINGS.APP_DESCRIPTION,
        status=overall,
        redis="ok" if redis_ok else "unavailable",
        database="ok" if db_ok else "unavailable",
    )
