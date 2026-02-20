from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.ext.asyncio.session import AsyncSession

from app.core.config import get_settings

SETTINGS = get_settings()

DBSession = AsyncSession
engine = create_async_engine(SETTINGS.ASYNC_DATABASE_URL,)
async_session = async_sessionmaker(engine, class_=DBSession, expire_on_commit=False)