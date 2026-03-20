from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

# Shared engine for the FastAPI app (uses connection pool)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def task_session():
    """Yield a DB session for Celery tasks.

    Uses NullPool so no connections are cached across event loops.
    Each call to _run() in a Celery task creates a brand-new asyncio event
    loop; a pooled engine would hand out asyncpg connections tied to the
    previous (closed) loop, causing 'Future attached to a different loop'.
    NullPool creates and closes a fresh connection on every use.
    """
    task_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(task_engine, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await task_engine.dispose()


class Base(DeclarativeBase):
    pass


async def create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
