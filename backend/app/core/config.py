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

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        if self.ENV == "production":
            return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@db:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        else:
            return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@localhost:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @computed_field
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        if self.ENV == "production":
            return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@db:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        else:
            return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@localhost:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_urls(self) -> "Settings":
        if not self.DATABASE_URL or "@db" in self.DATABASE_URL and self.ENV != "production":
            raise ValueError("Invalid Database URL for current environment")
        return self

@lru_cache()
def get_settings() -> Settings:
    """Get settings instance with caching."""
    return Settings()
