"""Async SQLAlchemy 2.0 engine, session factory, Base metadata, and portable column types."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, MetaData, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from app.core.config import Settings, get_settings

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

JSONBCompat = JSONB().with_variant(JSON(), "sqlite")


def utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Normalize naive datetimes (e.g. from SQLite) to timezone-aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class TZDateTime(TypeDecorator[datetime]):
    """Portable TIMESTAMPTZ type returning aware UTC datetimes on both PostgreSQL and SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base with cross-dialect metadata naming conventions."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_async_engine_from_settings(
    cfg: Settings | None = None,
    url_override: str | None = None,
) -> AsyncEngine:
    """Create an async SQLAlchemy 2.0 engine configured for PostgreSQL or SQLite."""
    active_settings = cfg or get_settings()
    url = url_override or active_settings.effective_database_url
    is_sqlite = url.lower().startswith("sqlite")

    engine_kwargs: dict[str, Any] = {"echo": False, "future": True}
    if is_sqlite:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        engine_kwargs["pool_pre_ping"] = True

    engine = create_async_engine(url, **engine_kwargs)

    if is_sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return lazily initialized singleton AsyncEngine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine_from_settings()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return lazily initialized async_sessionmaker bound to the active engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def dispose_engine() -> None:
    """Dispose active global engine and reset session factory (useful in lifespan/tests)."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an AsyncSession."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session
