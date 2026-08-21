"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.api.routers import root
from app.api.routers.auth.auth import router as auth_router
from app.api.routers.vault.vault import router as vault_router
from app.api.routers.documents.documents import router as documents_router
from app.api.routers.query.query import router as query_router
from app.api.routers.transcription.transcription import router as transcription_router
from app.api.routers.transcription.sessions import router as sessions_router

from app.api.lifespan import lifespan
from app.core.config import Settings
from app.api.extensions import validation_exception_handler


def create_app(settings: Settings) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        lifespan=lifespan,
    )

    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=settings.CORS_METHODS,
        allow_headers=settings.CORS_HEADERS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    )

    for r in (
        root.router, auth_router, vault_router, documents_router,
        query_router, transcription_router, sessions_router,
    ):
        app.include_router(r)

    return app
