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
    app = FastAPI(
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        lifespan=lifespan,
    )
    return app


def register_extensions(app: FastAPI) -> FastAPI:
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    return app


def register_middlewares(app: FastAPI) -> FastAPI:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=SETTINGS.CORS_ORIGINS,
        allow_methods=SETTINGS.CORS_METHODS,
        allow_headers=SETTINGS.CORS_HEADERS,
        allow_credentials=SETTINGS.CORS_ALLOW_CREDENTIALS,
    )
    return app


def register_routers(app: FastAPI) -> FastAPI:
    app.include_router(root.router)
    app.include_router(auth_router)
    return app


def create_app(settings: Settings) -> FastAPI:
    app = create_instance(settings)
    app = register_extensions(app)
    app = register_middlewares(app)
    app = register_routers(app)
    return app