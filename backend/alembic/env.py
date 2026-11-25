"""Alembic async migration environment supporting PostgreSQL and SQLite fallback."""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, event, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Resolve explicit sqlalchemy.url override or active Settings database URL."""
    configured = (config.get_main_option("sqlalchemy.url") or "").strip()
    if configured:
        return configured
    return Settings().effective_database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_database_url()
    render_as_batch = url.lower().startswith("sqlite")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=render_as_batch,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Execute migrations on a synchronous SQLAlchemy connection."""
    render_as_batch = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=render_as_batch,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations online."""
    url = get_database_url()
    section = dict(config.get_section(config.config_ini_section, {}))
    section["sqlalchemy.url"] = url

    connect_args: dict[str, Any] = {}
    is_sqlite = url.lower().startswith("sqlite")
    if is_sqlite:
        connect_args["check_same_thread"] = False

    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    if is_sqlite:

        @event.listens_for(connectable.sync_engine, "connect")
        def _enable_sqlite_fk(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode, supporting both sync CLI and nested async callers."""
    provided_connection = config.attributes.get("connection")
    if provided_connection is not None:
        do_run_migrations(provided_connection)
        return

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_async_migrations())
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(asyncio.run, run_async_migrations()).result()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
