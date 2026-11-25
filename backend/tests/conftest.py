"""Shared pytest fixtures for backend-core unit and SQLite migration tests."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Ensure test runs never depend on Postgres/Docker.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("USE_SQLITE_FALLBACK", "true")

from app.core.database import create_async_engine_from_settings
from app.models import Base


@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> Path:
    """Return a clean temporary SQLite file path."""
    return tmp_path / "releaseguard_test.db"


@pytest.fixture
def sqlite_async_url(sqlite_db_path: Path) -> str:
    """Return an async aiosqlite URL pointing to sqlite_db_path."""
    return f"sqlite+aiosqlite:///{sqlite_db_path.as_posix()}"


@pytest_asyncio.fixture
async def async_engine(sqlite_async_url: str) -> AsyncGenerator[AsyncEngine, None]:
    """Create a fresh SQLite AsyncEngine with all 15 ORM tables created."""
    engine = create_async_engine_from_settings(url_override=sqlite_async_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def session_factory(async_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an async_sessionmaker bound to the per-test SQLite engine."""
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@pytest_asyncio.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession per test."""
    async with session_factory() as session:
        yield session
