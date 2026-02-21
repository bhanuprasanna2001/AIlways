import pytest
import fakeredis
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.db import get_db


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
async def db_engine():
    """In-memory SQLite engine — one shared connection via StaticPool."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


@pytest.fixture()
async def db_session(db_engine):
    """Provide a database session for direct DB operations in tests."""
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session


# ---------------------------------------------------------------------------
# Redis fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
async def fake_redis():
    """Fake Redis client backed by fakeredis."""
    async with fakeredis.FakeAsyncRedis(decode_responses=True) as client:
        yield client
        await client.flushall()


# ---------------------------------------------------------------------------
# HTTP client fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
async def client(db_engine, fake_redis):
    """AsyncClient wired to the FastAPI app with test DB and fake Redis."""
    from fastapi_limiter.depends import RateLimiter

    # 1. Disable rate limiting
    original_call = RateLimiter.__call__

    async def _noop_rate_limit(self):
        """No-op so FastAPI sees no injectable params."""
        return None

    RateLimiter.__call__ = _noop_rate_limit

    # 2. Point redis helpers at fake redis
    import app.core.tools.redis as redis_mod
    original_redis = redis_mod.redis_client
    redis_mod.redis_client = fake_redis

    # 3. Build app
    from app.api.app import create_app
    from app.core.config import get_settings

    app = create_app(get_settings())

    # 4. Override DB dependency
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 5. Yield client
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # 6. Cleanup
    app.dependency_overrides.clear()
    redis_mod.redis_client = original_redis
    RateLimiter.__call__ = original_call
