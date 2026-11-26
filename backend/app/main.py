"""ReleaseGuard AI FastAPI application factory, lifespan bootstrap, CORS, and routers."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.deps import resolve_session_factory
from app.api.v1 import api_v1_router, system_router
from app.api.v1.errors import register_exception_handlers
from app.core.config import Settings, get_settings
from app.core.database import Base, dispose_engine, get_engine
from app.core.logging import configure_logging
from app.core.security import encrypt_token, hash_password, make_token_hint
from app.models import (
    IntegrationAuthMode,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    User,
    UserRole,
)
from app.services.policy_engine import PolicyEngine

logger = logging.getLogger("releaseguard.main")


def validate_startup_auth(cfg: Settings, *, allow_test_bypass: bool = True) -> None:
    """Validate Control Plane auth settings at startup and warn on open dev mode."""
    if not cfg.REQUIRE_AUTH:
        logger.warning(
            "REQUIRE_AUTH=false: Control Plane authentication is disabled (opt-in dev/demo mode). "
            "Set REQUIRE_AUTH=true and configure AUTH_OPERATOR_PASSWORD or GitHub OAuth before exposing this service."
        )
        return

    if allow_test_bypass and cfg.APP_ENV == "test":
        return

    if not cfg.has_auth_provider:
        raise RuntimeError(
            "REQUIRE_AUTH=true requires at least one sign-in method configured at startup: "
            "set AUTH_OPERATOR_PASSWORD or both GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET."
        )


def should_create_tables_on_startup(cfg: Settings, *, has_explicit_engine: bool) -> bool:
    """Return True only for test engines, dev SQLite fallback, or explicit non-prod opt-in."""
    if has_explicit_engine or cfg.APP_ENV == "test":
        return True
    if cfg.APP_ENV == "production":
        return False
    return cfg.is_sqlite or cfg.AUTO_CREATE_TABLES


async def bootstrap_workspace_defaults(
    session: AsyncSession,
    *,
    bootstrap_env_tokens: bool = True,
) -> None:
    """Ensure singleton WorkspaceSettings, default operator User, and optional env tokens."""
    await PolicyEngine.get_workspace_settings(session)

    cfg = get_settings()
    op_email = cfg.AUTH_OPERATOR_EMAIL.strip().lower() or "operator@releaseguard.local"
    user_stmt = select(User).where(User.email == op_email).limit(1)
    operator = (await session.execute(user_stmt)).scalar_one_or_none()
    pw_val = cfg.AUTH_OPERATOR_PASSWORD.strip()
    if operator is None:
        operator = User(
            email=op_email,
            display_name="ReleaseGuard Operator",
            role=UserRole.ADMIN,
            password_hash=hash_password(pw_val) if pw_val else None,
            is_active=True,
        )
        session.add(operator)
        await session.flush()
    elif pw_val and not operator.password_hash:
        operator.password_hash = hash_password(pw_val)
        await session.flush()

    if bootstrap_env_tokens:
        if cfg.GITHUB_PAT.strip():
            gh_exists = (
                await session.execute(
                    select(IntegrationConnection)
                    .where(IntegrationConnection.provider == IntegrationProvider.GITHUB)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if gh_exists is None:
                pat = cfg.GITHUB_PAT.strip()
                session.add(
                    IntegrationConnection(
                        user_id=operator.id,
                        provider=IntegrationProvider.GITHUB,
                        auth_mode=IntegrationAuthMode.PAT,
                        status=IntegrationStatus.CONNECTED,
                        external_account_id="env-bootstrap",
                        external_account_name="Environment GitHub PAT",
                        scopes=[],
                        access_token_encrypted=encrypt_token(pat),
                        token_hint=make_token_hint(pat),
                        metadata_json={"source": "env"},
                    )
                )

        if cfg.FLAGSMITH_ORG_TOKEN.strip():
            fs_exists = (
                await session.execute(
                    select(IntegrationConnection)
                    .where(IntegrationConnection.provider == IntegrationProvider.FLAGSMITH)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if fs_exists is None:
                org_tok = cfg.FLAGSMITH_ORG_TOKEN.strip()
                pid = cfg.flagsmith_project_id_int
                session.add(
                    IntegrationConnection(
                        user_id=operator.id,
                        provider=IntegrationProvider.FLAGSMITH,
                        auth_mode=IntegrationAuthMode.ORG_TOKEN,
                        status=IntegrationStatus.CONNECTED,
                        external_account_id=str(pid) if pid is not None else "env-bootstrap",
                        external_account_name=(
                            f"Flagsmith Project #{pid}" if pid is not None else "Environment Flagsmith"
                        ),
                        scopes=["management_api"],
                        access_token_encrypted=encrypt_token(org_tok),
                        token_hint=make_token_hint(org_tok),
                        metadata_json={
                            "source": "env",
                            "project_id": pid,
                            "environment_keys": cfg.flagsmith_env_keys,
                        },
                    )
                )

    await session.commit()


def create_app(
    *,
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    github_transport: Any | None = None,
    flagsmith_transport: Any | None = None,
    gemini_raw_client: Any | None = None,
    repo_base_files: dict[str, str] | None = None,
    bootstrap_env_tokens: bool = True,
) -> FastAPI:
    """Create configured ReleaseGuard AI FastAPI application."""
    cfg = get_settings()

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        configure_logging(cfg.LOG_LEVEL)
        validate_startup_auth(cfg)
        explicit_engine = getattr(app_instance.state, "engine", None)
        active_engine = explicit_engine or get_engine()
        if should_create_tables_on_startup(cfg, has_explicit_engine=explicit_engine is not None):
            async with active_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        else:
            logger.info(
                "Skipping Base.metadata.create_all for %s (%s); relying on Alembic migrations.",
                cfg.database_backend,
                cfg.APP_ENV,
            )

        factory = resolve_session_factory(app_instance)
        async with factory() as session:
            await bootstrap_workspace_defaults(
                session, bootstrap_env_tokens=bootstrap_env_tokens
            )
        from app.services.agent_execution_service import AgentExecutionService
        from app.services.release_orchestrator import ReleaseOrchestrator
        from app.services.scheduler import get_scheduler_service

        async with factory() as recovery_session:
            await ReleaseOrchestrator.recover_interrupted_releases(recovery_session)
            await AgentExecutionService.recover_interrupted_executions(recovery_session)

        scheduler_svc = get_scheduler_service(app_instance)
        await scheduler_svc.recover_stale_runs()
        if cfg.APP_ENV != "test":
            await scheduler_svc.start()
        try:
            yield
        finally:
            await scheduler_svc.stop()
            if getattr(app_instance.state, "engine", None) is None:
                await dispose_engine()

    fastapi_app = FastAPI(
        title="ReleaseGuard AI",
        version="1.0.0",
        description="AI-orchestrated PR Intelligence, Feature Flag Control, and Bayesian Release Governance.",
        lifespan=lifespan,
    )

    if cfg.APP_ENV != "test":
        github_transport = None
        flagsmith_transport = None
        gemini_raw_client = None
        repo_base_files = None

    fastapi_app.state.engine = engine
    fastapi_app.state.session_factory = session_factory
    fastapi_app.state.github_transport = github_transport
    fastapi_app.state.flagsmith_transport = flagsmith_transport
    fastapi_app.state.gemini_raw_client = gemini_raw_client
    fastapi_app.state.repo_base_files = repo_base_files
    fastapi_app.state.background_tasks = set()
    fastapi_app.state.api_metrics = {
        "requests_total": 0,
        "errors_5xx": 0,
        "latency_ms_samples": [],
        "last_error": None,
    }

    explicit_origins = cfg.cors_origins_list or ["http://localhost:3000", "http://localhost:3001"]
    if "*" in explicit_origins:
        explicit_origins = ["http://localhost:3000", "http://localhost:3001"]

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=explicit_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Ingest-Key", "X-Request-ID"],
    )

    @fastapi_app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Any:
        import time

        req_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id
        t0 = time.perf_counter()
        metrics_store = request.app.state.api_metrics
        metrics_store["requests_total"] = int(metrics_store.get("requests_total", 0)) + 1
        try:
            response = await call_next(request)
        except Exception as exc:
            metrics_store["errors_5xx"] = int(metrics_store.get("errors_5xx", 0)) + 1
            metrics_store["last_error"] = type(exc).__name__
            raise
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        samples: list[float] = metrics_store.setdefault("latency_ms_samples", [])
        samples.append(elapsed_ms)
        if len(samples) > 200:
            del samples[: len(samples) - 200]
        if response.status_code >= 500:
            metrics_store["errors_5xx"] = int(metrics_store.get("errors_5xx", 0)) + 1
        response.headers["X-Request-ID"] = req_id
        return response

    register_exception_handlers(fastapi_app)
    fastapi_app.include_router(system_router)
    fastapi_app.include_router(api_v1_router)
    return fastapi_app


app = create_app()
