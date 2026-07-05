"""Async database wiring for order-db (SQLAlchemy async + asyncpg, D-030)."""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all order-service ORM models."""


def create_engine() -> AsyncEngine:
    """Build the async engine from settings (asyncpg driver)."""
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


# Process-wide engine + session factory. expire_on_commit=False keeps ORM
# objects usable after the session closes (we serialize them into the response).
engine: AsyncEngine = create_engine()
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_models() -> None:
    """Create tables if they do not exist (D-033; called from the lifespan)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped async session."""
    async with async_session() as session:
        yield session
