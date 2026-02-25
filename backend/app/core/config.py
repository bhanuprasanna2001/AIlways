from functools import lru_cache
from typing import Literal, List
from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Nested sub-configs — each reads env vars matching its prefix
# ---------------------------------------------------------------------------

class ClaimConfig(BaseSettings):
    """Claim detection & verification settings."""
    model_config = SettingsConfigDict(env_prefix="CLAIM_", env_file=".env", extra="ignore")

    DETECTION_ENABLED: bool = True
    BATCH_INTERVAL_S: float = 6.0
    VERIFICATION_TOP_K: int = 5
    CONTEXT_SEGMENTS: int = 10
    FLUSH_INTERVAL_S: float = 1.0
    IDLE_TIMEOUT_S: float = 2.0
    MAX_BUFFER_SEGMENTS: int = 100
    MIN_SEGMENTS: int = 1
    MIN_CHARS: int = 20
    DEDUP_THRESHOLD: float = 0.8
    SEGMENT_MIN_WORDS: int = 3
    SEGMENT_MIN_CONFIDENCE: float = 0.5
    GROQ_MAX_RETRIES: int = 3
    DRAIN_TIMEOUT_S: float = 3.0
    TASK_TIMEOUT_S: float = 15.0
    MAX_CONCURRENT_TASKS: int = 5
    VERIFICATION_MMR_LAMBDA: float = 1.0
    EXTRACT_FROM_QUESTIONS: bool = True

    # Short segments containing entity anchors bypass word-count filter.
    # Patterns: numeric IDs (4+ digits), dollar amounts, entity keywords.
    SEGMENT_ENTITY_BYPASS: bool = True


class TranscriptionConfig(BaseSettings):
    """Transcription session & audio settings."""
    model_config = SettingsConfigDict(env_prefix="TRANSCRIPTION_", env_file=".env", extra="ignore")

    SESSION_STALE_TIMEOUT_MINUTES: int = 30
    STALE_CLEANUP_INTERVAL_MINUTES: int = 30
    DB_FLUSH_INTERVAL_S: float = 2.0
    DB_FLUSH_BATCH_SIZE: int = 50
    MAX_SESSION_DURATION_S: int = 14400
    SESSION_TITLE_MAX_LENGTH: int = 255
    WS_TICKET_TTL_S: int = 60
    MAX_AUDIO_SIZE_MB: int = 100


class WorkerConfig(BaseSettings):
    """Kafka worker tuning."""
    model_config = SettingsConfigDict(env_prefix="WORKER_", env_file=".env", extra="ignore")

    INGESTION_BATCH_SIZE: int = 20
    INGESTION_BATCH_TIMEOUT_S: float = 2.0
    INGESTION_CONCURRENCY: int = 5


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------

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

    # Database
    POSTGRES_USER: str = "myuser"
    POSTGRES_PASSWORD: str = "mypassword"
    POSTGRES_DB: str = "mydatabase"
    POSTGRES_PORT: int = 5434

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173", "http://localhost:8080", "http://localhost:8501"]
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

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"
    OPENAI_EMBEDDING_DIMENSIONS: int = 1536
    OPENAI_REASONING_MODEL: str = "gpt-4o"
    OPENAI_QUERY_MODEL: str = "gpt-4o-mini"

    # Cohere (optional — empty disables reranking)
    COHERE_API_KEY: str = ""

    # Deepgram (transcription + diarization)
    DEEPGRAM_API_KEY: str = ""
    DEEPGRAM_MODEL: str = "nova-3"
    DEEPGRAM_LANGUAGE: str = "en"
    DEEPGRAM_ENDPOINTING_MS: int = 500
    DEEPGRAM_DIARIZE: bool = True

    # Groq (fast LLM for claim detection)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # File Storage
    FILE_STORE_PATH: str = "./data/uploads"

    # Ingestion
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_FILE_TYPES: list[str] = ["pdf", "txt", "md"]

    # RAG Pipeline
    RAG_CHUNK_SIZE: int = 512
    RAG_CHUNK_OVERLAP: int = 50
    RAG_EMBEDDING_BATCH_SIZE: int = 2048
    RAG_SEARCH_TOP_K: int = 5
    RAG_GENERATION_TEMPERATURE: float = 0.1

    # Query rewriting — resolves pronouns and co-references using
    # conversation history so the retrieval query is always standalone.
    QUERY_REWRITE_ENABLED: bool = True
    QUERY_REWRITE_MODEL: str = ""  # defaults to OPENAI_QUERY_MODEL if empty
    QUERY_HISTORY_MAX_TURNS: int = 10

    # Entity-aware retrieval — direct SQL lookup for entity IDs
    # (invoice numbers, order numbers) before embedding search.
    ENTITY_SEARCH_ENABLED: bool = True
    ENTITY_SEARCH_MAX_IDS: int = 3
    ENTITY_SEARCH_LIMIT: int = 10

    # Kafka / Redpanda
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:19092"
    KAFKA_CONSUMER_GROUP: str = "ailways-workers"
    KAFKA_ENABLED: bool = True
    KAFKA_PRODUCER_TIMEOUT_MS: int = 10000
    KAFKA_CONSUMER_MAX_POLL_INTERVAL_MS: int = 600000
    KAFKA_RECOVERY_INTERVAL_MINUTES: int = 5

    # Database pool
    DB_POOL_SIZE: int = 10
    DB_POOL_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE_S: int = 3600
    DB_POOL_PRE_PING: bool = True

    # External API timeouts
    API_TIMEOUT_S: float = 60.0
    EMBEDDING_TIMEOUT_S: float = 60.0

    # WebSocket
    WS_HEARTBEAT_INTERVAL_S: float = 30.0
    WS_RECEIVE_TIMEOUT_S: float = 300.0
    MAX_CONCURRENT_TRANSCRIPTION_SESSIONS: int = 3

    # Pagination
    PAGINATION_DEFAULT_LIMIT: int = 50
    PAGINATION_MAX_LIMIT: int = 200

    # Sparse search
    SPARSE_SEARCH_MAX_QUERY_LENGTH: int = 500

    # PDF process pool
    PDF_PARSE_WORKERS: int = 2

    # Embedding cache (Redis-backed query dedup)
    EMBEDDING_CACHE_TTL_S: float = 300.0

    # Grouped sub-configs
    CLAIM: ClaimConfig = ClaimConfig()
    TRANSCRIPTION: TranscriptionConfig = TranscriptionConfig()
    WORKER: WorkerConfig = WorkerConfig()

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
            if "@db:" not in self.DATABASE_URL:
                raise ValueError("Production DB URL must route to 'db' service")
            if "@redis:" not in self.REDIS_URL:
                raise ValueError("Production Redis URL must route to 'redis' service")
        else:
            if "@localhost:" not in self.DATABASE_URL:
                raise ValueError("Dev DB URL must route to localhost")
            if "@localhost:" not in self.REDIS_URL:
                raise ValueError("Dev Redis URL must route to localhost")
        return self


@lru_cache()
def get_settings() -> Settings:
    """Get settings instance with caching."""
    return Settings()
