from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings


class HealthResponse(BaseModel):
    """Health check response schema."""

    title: str
    version: str
    description: str
    status: str


router = APIRouter()

SETTINGS = get_settings()


@router.get("/health", response_model=HealthResponse, tags=["Health Check"], summary="Check the health of the application")
async def health():
    """Endpoint to check the health of the application.

    Returns:
        HealthResponse: Application metadata and status.
    """
    return HealthResponse(
        title=SETTINGS.APP_TITLE,
        version=SETTINGS.APP_VERSION,
        description=SETTINGS.APP_DESCRIPTION,
        status="healthy",
    )
