from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.api.routers import root
from app.api.routers.auth.auth import router as auth_router

from app.api.lifespan import lifespan
from app.core.config import Settings, get_settings
from app.api.extensions import validation_exception_handler


SETTINGS = get_settings()


def create_instance(settings: Settings) -> FastAPI:
    """Create a FastAPI instance with the given settings.

    Args:
        settings (Settings): The application settings.

    Returns:
        FastAPI: The created FastAPI instance.
    """
    app = FastAPI(
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        lifespan=lifespan,
    )
    return app


def register_extensions(app: FastAPI) -> FastAPI:
    """Register extensions and exception handlers with the FastAPI instance.

    Args:
        app (FastAPI): The FastAPI instance to register extensions with.

    Returns:
        FastAPI: The FastAPI instance with registered extensions.
    """
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    return app


def register_middlewares(app: FastAPI) -> FastAPI:
    """Register middlewares with the FastAPI instance.

    Args:
        app (FastAPI): The FastAPI instance to register middlewares with.

    Returns:
        FastAPI: The FastAPI instance with registered middlewares.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=SETTINGS.CORS_ORIGINS,
        allow_methods=SETTINGS.CORS_METHODS,
        allow_headers=SETTINGS.CORS_HEADERS,
        allow_credentials=SETTINGS.CORS_ALLOW_CREDENTIALS,
    )
    return app


def register_routers(app: FastAPI) -> FastAPI:
    """Register API routers with the FastAPI instance.

    Args:
        app (FastAPI): The FastAPI instance to register routers with.

    Returns:
        FastAPI: The FastAPI instance with registered routers.
    """
    app.include_router(root.router)
    app.include_router(auth_router)
    return app


def create_app(settings: Settings) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings (Settings): The application settings.

    Returns:
        FastAPI: The configured FastAPI application.
    """
    app = create_instance(settings)
    app = register_extensions(app)
    app = register_middlewares(app)
    app = register_routers(app)
    return app