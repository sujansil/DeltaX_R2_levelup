from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

if settings.database_url.startswith("sqlite"):
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=20,
        pool_timeout=30,
    )

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
