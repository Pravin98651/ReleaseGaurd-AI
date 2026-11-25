"""Shared FastAPI dependencies and injectable integration/supervisor factories."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.gemini_client import GeminiClient
from app.agents.supervisor import ReleaseGuardSupervisor
from app.agents.tools.context import ToolExecutionContext
from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.security import decrypt_token
from app.integrations.flagsmith_client import FlagsmithClient
from app.integrations.github_client import GitHubClient
from app.models import (
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    User,
    UserRole,
)


def resolve_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    """Return app-bound session_factory if overridden in tests, else global factory."""
    factory = getattr(app.state, "session_factory", None)
    return factory if factory is not None else get_session_factory()


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession bound to the active FastAPI app engine/factory."""
    factory = resolve_session_factory(request.app)
    async with factory() as session:
        yield session


async def get_default_operator(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """Return active default operator user, creating one if absent."""
    stmt = select(User).where(User.is_active.is_(True)).order_by(User.created_at.asc()).limit(1)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        email="operator@releaseguard.local",
        display_name="ReleaseGuard Operator",
        role=UserRole.ADMIN,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


DBSession = Annotated[AsyncSession, Depends(get_db_session)]
DefaultOperator = Annotated[User, Depends(get_default_operator)]


def build_gemini_client_from_app(app: FastAPI, model_override: str | None = None) -> GeminiClient:
    """Construct GeminiClient honoring optional test `app.state.gemini_raw_client`."""
    raw_client = getattr(app.state, "gemini_raw_client", None)
    return GeminiClient(model=model_override, raw_client=raw_client)


async def build_github_client(app: FastAPI, session: AsyncSession, token: str | None = None) -> GitHubClient:
    """Build GitHubClient from explicit/DB/env token and optional `app.state.github_transport`."""
    transport = getattr(app.state, "github_transport", None)
    resolved_token = token
    if resolved_token is None:
        stmt = (
            select(IntegrationConnection)
            .where(
                IntegrationConnection.provider == IntegrationProvider.GITHUB,
                IntegrationConnection.status == IntegrationStatus.CONNECTED,
            )
            .order_by(IntegrationConnection.updated_at.desc())
            .limit(1)
        )
        conn = (await session.execute(stmt)).scalar_one_or_none()
        if conn is not None and conn.access_token_encrypted and conn.access_token_encrypted != "revoked":
            resolved_token = decrypt_token(conn.access_token_encrypted)
        else:
            env_pat = get_settings().GITHUB_PAT.strip()
            resolved_token = env_pat or None
    return GitHubClient(token=resolved_token, transport=transport)


async def build_flagsmith_client(
    app: FastAPI,
    session: AsyncSession,
    org_token: str | None = None,
) -> FlagsmithClient:
    """Build FlagsmithClient from explicit/DB/env token and optional `app.state.flagsmith_transport`."""
    cfg = get_settings()
    transport = getattr(app.state, "flagsmith_transport", None)
    resolved_token = org_token
    if resolved_token is None:
        stmt = (
            select(IntegrationConnection)
            .where(
                IntegrationConnection.provider == IntegrationProvider.FLAGSMITH,
                IntegrationConnection.status == IntegrationStatus.CONNECTED,
            )
            .order_by(IntegrationConnection.updated_at.desc())
            .limit(1)
        )
        conn = (await session.execute(stmt)).scalar_one_or_none()
        if conn is not None and conn.access_token_encrypted and conn.access_token_encrypted != "revoked":
            resolved_token = decrypt_token(conn.access_token_encrypted)
        else:
            resolved_token = cfg.FLAGSMITH_ORG_TOKEN.strip() or None
    return FlagsmithClient(
        org_token=resolved_token,
        api_url=cfg.FLAGSMITH_API_URL,
        edge_api_url=cfg.FLAGSMITH_EDGE_API_URL,
        transport=transport,
    )


async def build_tool_context(
    app: FastAPI,
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    agent_task_id: uuid.UUID | None = None,
    context_scope: dict[str, Any] | None = None,
) -> ToolExecutionContext:
    """Create a ToolExecutionContext wired to app transports & optional base files."""
    gh_transport = getattr(app.state, "github_transport", None)
    fs_transport = getattr(app.state, "flagsmith_transport", None)
    repo_base_files = getattr(app.state, "repo_base_files", None)

    gh_client: GitHubClient | None = None
    if gh_transport is not None:
        try:
            gh_client = await build_github_client(app, session)
        except Exception:  # noqa: BLE001
            gh_client = GitHubClient(token="mock", transport=gh_transport)

    fs_client: FlagsmithClient | None = None
    if fs_transport is not None:
        try:
            fs_client = await build_flagsmith_client(app, session)
        except Exception:  # noqa: BLE001
            fs_client = FlagsmithClient(org_token="mock", transport=fs_transport)

    return ToolExecutionContext(
        session=session,
        user_id=user_id,
        agent_task_id=agent_task_id,
        context_scope=dict(context_scope or {}),
        github_client=gh_client,
        flagsmith_client=fs_client,
        repo_base_files=repo_base_files,
    )


def build_supervisor(app: FastAPI, model_override: str | None = None) -> ReleaseGuardSupervisor:
    """Construct ReleaseGuardSupervisor bound to the active app session factory & transports."""
    override = getattr(app.state, "supervisor_override", None)
    if override is not None:
        return override

    factory = resolve_session_factory(app)
    gemini_client = build_gemini_client_from_app(app, model_override=model_override)
    gh_transport = getattr(app.state, "github_transport", None)
    fs_transport = getattr(app.state, "flagsmith_transport", None)
    repo_base_files = getattr(app.state, "repo_base_files", None)
    cfg = get_settings()

    def _ctx_factory(session: AsyncSession, state: Any) -> ToolExecutionContext:
        raw_user = state.get("user_id")
        raw_task = state.get("task_id")
        gh_client = (
            GitHubClient(token=cfg.GITHUB_PAT or "mock", transport=gh_transport)
            if gh_transport is not None
            else None
        )
        fs_client = (
            FlagsmithClient(
                org_token=cfg.FLAGSMITH_ORG_TOKEN or "mock",
                api_url=cfg.FLAGSMITH_API_URL,
                edge_api_url=cfg.FLAGSMITH_EDGE_API_URL,
                transport=fs_transport,
            )
            if fs_transport is not None
            else None
        )
        return ToolExecutionContext(
            session=session,
            user_id=uuid.UUID(raw_user) if raw_user else None,
            agent_task_id=uuid.UUID(raw_task) if raw_task else None,
            context_scope=dict(state.get("context_scope") or {}),
            github_client=gh_client,
            flagsmith_client=fs_client,
            repo_base_files=repo_base_files,
        )

    return ReleaseGuardSupervisor(
        session_factory=factory,
        gemini_client=gemini_client,
        context_factory=_ctx_factory,
    )
