from functools import lru_cache
from typing import Literal, List
from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    
    # General
    ENV: Literal["development", "production"] = "development"
    LOG_LEVEL: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "TRACE"]  = "INFO"
    
    # Application
    APP_DEBUG: bool = True
    APP_TITLE: str = "AIlways"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = "Meeting Truth & Context Copilot"

    # Server
    SERVER_PORT: int = 8080
    SERVER_RELOAD: bool = True
    SERVER_HOST: str = "0.0.0.0"

    # Auth
    JWT_SECRET_KEY: str = "unsafe"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Database
    POSTGRES_USER: str = "myuser"
    POSTGRES_PASSWORD: str = "mypassword"
    POSTGRES_DB: str = "mydatabase"
    POSTGRES_PORT: int = 5434

    # Pg Admin
    PGADMIN_EMAIL: str | None = None
    PGADMIN_PASSWORD: str | None = None
    PGADMIN_PORT: int | None = None

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173", "http://localhost:8080"]
    CORS_METHODS: List[str] = ["*"]
    CORS_HEADERS: List[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True

    # Redis
    REDIS_PORT: int = 6380
    REDIS_PASSWORD: str = "mypassword"
    REDIS_SESSION_TTL_SECONDS: int = 60 * 60 * 24 * 7

    # Cookies
    SESSION_COOKIE_NAME: str = "session_id"
    CSRF_COOKIE_NAME: str = "csrf_token"
    

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        host = "redis" if self.ENV == "production" else "localhost"
        return f"redis://:{self.REDIS_PASSWORD}@{host}:{self.REDIS_PORT}"

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        host = "db" if self.ENV == "production" else "localhost"
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{host}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @computed_field
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        host = "db" if self.ENV == "production" else "localhost"
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{host}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @computed_field
    @property
    def COOKIE_SECURE(self) -> bool:
        return self.ENV == "production"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_config(self) -> "Settings":
        if self.ENV == "production":
            assert "@db:" in self.DATABASE_URL, "Production DB URL must route to 'db' service"
            assert "@redis:" in self.REDIS_URL, "Production Redis URL must route to 'redis' service"
        else:
            assert "@localhost:" in self.DATABASE_URL, "Dev DB URL must route to localhost"
            assert "@localhost:" in self.REDIS_URL, "Dev Redis URL must route to localhost"
        return self


@lru_cache()
def get_settings() -> Settings:
    """Get settings instance with caching."""
    return Settings()
