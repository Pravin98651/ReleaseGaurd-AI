"""Shared FastAPI dependencies, authentication/RBAC guards, rate limiter, and integration factories."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.gemini_client import GeminiClient
from app.agents.supervisor import ReleaseGuardSupervisor
from app.agents.tools.context import ToolExecutionContext
from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.security import decrypt_token, hash_password, verify_auth_token
from app.integrations.flagsmith_client import FlagsmithClient
from app.integrations.github_client import GitHubClient
from app.models import (
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    User,
    UserRole,
)

AUTH_COOKIE_NAME = "rg_session"


def resolve_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    """Return app-bound session_factory if overridden in tests, else global factory."""
    factory = getattr(app.state, "session_factory", None)
    return factory if factory is not None else get_session_factory()


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession bound to the active FastAPI app engine/factory."""
    factory = resolve_session_factory(request.app)
    async with factory() as session:
        yield session


def is_auth_required(request: Request) -> bool:
    """Resolve whether Control Plane auth is strictly enforced for this app/request."""
    override = getattr(request.app.state, "require_auth_override", None)
    if override is not None:
        return bool(override)
    cfg = get_settings()
    if cfg.APP_ENV == "production":
        return True
    return bool(cfg.REQUIRE_AUTH)


def extract_auth_token(request: Request) -> str | None:
    """Extract bearer token from Authorization header, HttpOnly cookie, or SSE query token."""
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        tok = auth_header[7:].strip()
        if tok:
            return tok
    cookie_tok = request.cookies.get(AUTH_COOKIE_NAME)
    if cookie_tok and cookie_tok.strip():
        return cookie_tok.strip()
    query_tok = request.query_params.get("token")
    if query_tok and query_tok.strip():
        return query_tok.strip()
    return None


async def resolve_optional_token_user(
    request: Request,
    session: AsyncSession,
) -> User | None:
    """Verify session/bearer token if present; raise 401 on invalid/expired/revoked token."""
    raw_token = extract_auth_token(request)
    if not raw_token:
        return None

    try:
        payload = verify_auth_token(raw_token)
        user_id = uuid.UUID(str(payload["sub"]))
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_AUTH_TOKEN", "message": str(exc)},
        ) from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "USER_INACTIVE_OR_MISSING",
                "message": "Authenticated operator not found or inactive.",
            },
        )
    if int(payload.get("ver", 0)) != int(user.token_version):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "SESSION_REVOKED",
                "message": "Authentication session has been logged out or revoked.",
            },
        )
    return user


async def get_default_operator(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """Return active default operator user for unauthenticated dev/test mode only."""
    cfg = get_settings()
    stmt = select(User).where(User.is_active.is_(True)).order_by(User.created_at.asc()).limit(1)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is not None:
        return user
    pw_hash = hash_password(cfg.AUTH_OPERATOR_PASSWORD.strip()) if cfg.AUTH_OPERATOR_PASSWORD.strip() else None
    user = User(
        email=cfg.AUTH_OPERATOR_EMAIL.strip() or "operator@releaseguard.local",
        display_name="ReleaseGuard Operator",
        role=UserRole.ADMIN,
        password_hash=pw_hash,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_current_operator(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """Authenticate caller via token/cookie; enforce 401 when REQUIRE_AUTH or APP_ENV=production."""
    user = await resolve_optional_token_user(request, session)
    if user is not None:
        return user

    if is_auth_required(request):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHENTICATED",
                "message": "Authentication required. Sign in via /api/v1/auth/login or GitHub OAuth.",
            },
        )
    return await get_default_operator(session)


def require_roles(*allowed_roles: UserRole) -> Callable[..., Awaitable[User]]:
    """Build a backend RBAC FastAPI dependency enforcing allowed UserRole values."""
    allowed = frozenset(allowed_roles)

    async def _role_dependency(
        operator: Annotated[User, Depends(get_current_operator)],
    ) -> User:
        if operator.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "FORBIDDEN_ROLE",
                    "message": f"Role '{operator.role.value}' is not authorized for this operation.",
                    "details": {
                        "current_role": operator.role.value,
                        "required_roles": sorted(r.value for r in allowed),
                    },
                },
            )
        return operator

    return _role_dependency


def rate_limit_dependency(
    scope: str,
    *,
    max_requests: int = 30,
    window_seconds: int = 60,
) -> Callable[[Request], Awaitable[None]]:
    """In-memory per-IP/session sliding-window rate limiter for expensive AI/auth endpoints."""

    async def _limiter(request: Request) -> None:
        cfg = get_settings()
        enforce = getattr(request.app.state, "enforce_rate_limit", None)
        if enforce is None and cfg.APP_ENV == "test":
            return
        if enforce is False:
            return

        buckets: dict[str, deque[float]] | None = getattr(request.app.state, "rate_limit_buckets", None)
        if buckets is None:
            buckets = defaultdict(deque)
            request.app.state.rate_limit_buckets = buckets

        client_host = request.client.host if request.client else "unknown"
        key = f"{scope}:{client_host}"
        now = time.monotonic()
        q = buckets[key]
        cutoff = now - window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= max_requests:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Too many requests for '{scope}'. Slow down and retry after {window_seconds}s.",
                    "details": {"scope": scope, "limit": max_requests, "window_seconds": window_seconds},
                },
            )
        q.append(now)

    return _limiter


DBSession = Annotated[AsyncSession, Depends(get_db_session)]
AuthenticatedOperator = Annotated[
    User,
    Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.RELEASE_MANAGER,
            UserRole.ENGINEER,
            UserRole.VIEWER,
        )
    ),
]
DeveloperOperator = Annotated[
    User,
    Depends(require_roles(UserRole.ADMIN, UserRole.RELEASE_MANAGER, UserRole.ENGINEER)),
]
ApproverOperator = Annotated[
    User,
    Depends(require_roles(UserRole.ADMIN, UserRole.RELEASE_MANAGER)),
]
AdminOperator = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
# Backwards-compatible alias for write/developer endpoints unless restricted to Approver/Admin
DefaultOperator = DeveloperOperator


def _resolve_test_transport(app: FastAPI, attr: str) -> Any | None:
    """Permit injected HTTP MockTransports strictly when APP_ENV=='test'."""
    if get_settings().APP_ENV != "test":
        return None
    return getattr(app.state, attr, None)


def build_gemini_client_from_app(app: FastAPI, model_override: str | None = None) -> GeminiClient:
    """Construct GeminiClient honoring optional test `app.state.gemini_raw_client`."""
    raw_client = getattr(app.state, "gemini_raw_client", None) if get_settings().APP_ENV == "test" else None
    return GeminiClient(model=model_override, raw_client=raw_client)


async def build_github_client(app: FastAPI, session: AsyncSession, token: str | None = None) -> GitHubClient:
    """Build GitHubClient from explicit/DB/env token and test-only `app.state.github_transport`."""
    transport = _resolve_test_transport(app, "github_transport")
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
    """Build FlagsmithClient from explicit/DB/env token and test-only `app.state.flagsmith_transport`."""
    cfg = get_settings()
    transport = _resolve_test_transport(app, "flagsmith_transport")
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
    """Create a ToolExecutionContext wired to DB/env clients & test-only transports."""
    gh_transport = _resolve_test_transport(app, "github_transport")
    fs_transport = _resolve_test_transport(app, "flagsmith_transport")
    repo_base_files = (
        getattr(app.state, "repo_base_files", None)
        if get_settings().APP_ENV == "test"
        else None
    )

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
    override = getattr(app.state, "supervisor_override", None) if get_settings().APP_ENV == "test" else None
    if override is not None:
        return override

    factory = resolve_session_factory(app)
    gemini_client = build_gemini_client_from_app(app, model_override=model_override)
    gh_transport = _resolve_test_transport(app, "github_transport")
    fs_transport = _resolve_test_transport(app, "flagsmith_transport")
    repo_base_files = (
        getattr(app.state, "repo_base_files", None)
        if get_settings().APP_ENV == "test"
        else None
    )
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
