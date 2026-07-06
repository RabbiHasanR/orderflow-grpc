"""Alembic environment — async-aware (order-db runs on asyncpg, D-030).

The default Alembic template uses a *sync* engine. Because our engine is
asyncpg, online migrations run the sync migration routine inside an async
connection via ``connection.run_sync(...)``. The DB URL and target metadata are
sourced from the app itself so there is a single source of truth.
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.core.database import Base

# Import every module's models so their tables register on Base.metadata and
# `--autogenerate` can see them. Add new module models here as they appear.
from app.modules.orders import models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a DB connection (``alembic upgrade --sql``)."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations against a live (sync-facing) connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async engine and drive the sync migration routine through it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online mode — bridges asyncio to Alembic's sync API."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
