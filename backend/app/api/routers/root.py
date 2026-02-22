from fastapi import APIRouter

from dataclasses import dataclass

from app.core.config import get_settings

@dataclass
class Health:
    title: str
    version: str
    description: str
    status: str

router = APIRouter()


@router.get("/health", response_model=Health, tags=["Health Check"], summary="Check the health of the application")
async def health():
    """Endpoint to check the health of the application.

    Args:
        None

    Returns:
        Health: An object containing the health status and application information.
    """
    settings = get_settings()
    return Health(
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        status="I'm fine, fine, fine! :D"
    )
