"""System health and executive dashboard summary endpoints."""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthenticatedOperator, DBSession, build_gemini_client_from_app
from app.core.config import get_settings
from app.core.database import ensure_utc, utcnow
from app.core.security import redact_dict
from app.models import (
    AgentTask,
    AgentTaskStatus,
    Approval,
    ApprovalStatus,
    AuditLog,
    AuditStatus,
    Experiment,
    ExperimentStatus,
    FeatureFlag,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    JobExecution,
    JobExecutionStatus,
    PullRequest,
    PullRequestState,
    Repository,
    RiskLevel,
    RolloutHistory,
    ScheduledJob,
)
from app.schemas import (
    ActivityFeedItem,
    AuditLogOut,
    DashboardChartsOut,
    DashboardCounts,
    DashboardMetricsOut,
    DashboardOverviewOut,
    DashboardSummaryResponse,
    HealthResponse,
    PendingActionItem,
    RolloutHistoryOut,
)
from app.schemas.analytics import TimeRangePreset
from app.services.analytics_queries import (
    compute_dashboard_charts,
    compute_dashboard_metrics,
    compute_pending_actions,
    compute_recent_activity,
    load_scoped_dataset,
    resolve_time_window,
)
from app.services.cron_utils import describe_schedule
from app.services.policy_engine import PolicyEngine

system_router = APIRouter(tags=["system"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])
system_v1_router = APIRouter(prefix="/system", tags=["system"])


async def _integration_flags(session: AsyncSession) -> tuple[bool, bool]:
    stmt = select(IntegrationConnection.provider).where(
        IntegrationConnection.status == IntegrationStatus.CONNECTED
    )
    providers = {row[0] for row in (await session.execute(stmt)).all()}
    cfg = get_settings()
    github_connected = (IntegrationProvider.GITHUB in providers) or bool(cfg.GITHUB_PAT.strip())
    flagsmith_connected = (IntegrationProvider.FLAGSMITH in providers) or bool(
        cfg.FLAGSMITH_ORG_TOKEN.strip()
    )
    return github_connected, flagsmith_connected


@system_router.get("/health/live")
async def get_liveness() -> dict[str, Any]:
    """Process liveness probe (zero DB/external dependencies)."""
    return {"status": "alive", "timestamp": utcnow().isoformat()}


@system_router.get("/health/ready")
async def get_readiness(session: DBSession) -> JSONResponse:
    """Database readiness probe distinguishing DB unavailability (503) from process liveness."""
    t0 = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "database": "ok", "latency_ms": latency_ms},
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": "unavailable",
                "error": type(exc).__name__,
            },
        )


@system_router.get("/health", response_model=HealthResponse)
async def get_health(
    request: Request,
    session: DBSession,
) -> HealthResponse:
    """Return runtime database, integration, Gemini, and analytics mode status."""
    cfg = get_settings()
    ws = await PolicyEngine.get_workspace_settings(session)
    await session.commit()
    github_connected, flagsmith_connected = await _integration_flags(session)
    gemini_client = build_gemini_client_from_app(request.app)

    return HealthResponse(
        status="ok",
        database=cfg.database_backend,
        gemini_configured=gemini_client.is_configured,
        github_connected=github_connected,
        flagsmith_connected=flagsmith_connected,
        analytics_mode=ws.flagsmith_analytics_mode.value,
    )


@dashboard_router.get("/summary")
async def get_dashboard_summary(
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, object]:
    """Return executive overview KPIs, integration status, recent rollouts, and recent audits."""
    ws = await PolicyEngine.get_workspace_settings(session)
    await session.commit()

    repo_count = int(
        (await session.execute(select(func.count(Repository.id)))).scalar_one() or 0
    )
    open_prs = int(
        (
            await session.execute(
                select(func.count(PullRequest.id)).where(PullRequest.state == PullRequestState.OPEN)
            )
        ).scalar_one()
        or 0
    )
    high_risk_prs = int(
        (
            await session.execute(
                select(func.count(PullRequest.id)).where(
                    PullRequest.risk_level.in_([RiskLevel.HIGH, RiskLevel.CRITICAL])
                )
            )
        ).scalar_one()
        or 0
    )
    active_flags = int(
        (
            await session.execute(
                select(func.count(FeatureFlag.id)).where(FeatureFlag.is_archived.is_(False))
            )
        ).scalar_one()
        or 0
    )
    active_experiments = int(
        (
            await session.execute(
                select(func.count(Experiment.id)).where(
                    Experiment.status == ExperimentStatus.RUNNING
                )
            )
        ).scalar_one()
        or 0
    )
    pending_approvals = int(
        (
            await session.execute(
                select(func.count(Approval.id)).where(Approval.status == ApprovalStatus.PENDING)
            )
        ).scalar_one()
        or 0
    )
    unhealthy_flags = int(
        (
            await session.execute(
                select(func.count(FeatureFlag.id)).where(FeatureFlag.health_score < 100)
            )
        ).scalar_one()
        or 0
    )
    avg_score_val = (
        await session.execute(select(func.avg(FeatureFlag.health_score)))
    ).scalar_one()

    recent_rollouts_rows = (
        (
            await session.execute(
                select(RolloutHistory).order_by(RolloutHistory.created_at.desc()).limit(10)
            )
        )
        .scalars()
        .all()
    )
    flag_ids = {r.feature_flag_id for r in recent_rollouts_rows}
    flag_key_map: dict[object, str] = {}
    if flag_ids:
        flags = (
            await session.execute(select(FeatureFlag).where(FeatureFlag.id.in_(flag_ids)))
        ).scalars().all()
        flag_key_map = {f.id: f.key for f in flags}

    recent_rollouts = []
    for r in recent_rollouts_rows:
        item = RolloutHistoryOut.model_validate(r).model_dump(mode="json")
        item["flag_key"] = flag_key_map.get(r.feature_flag_id)
        recent_rollouts.append(item)

    recent_audits_rows = (
        (
            await session.execute(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)
            )
        )
        .scalars()
        .all()
    )
    recent_audits = [
        AuditLogOut.model_validate(a).model_dump(mode="json") for a in recent_audits_rows
    ]

    github_connected, flagsmith_connected = await _integration_flags(session)
    gemini_client = build_gemini_client_from_app(request.app)

    base_resp = DashboardSummaryResponse(
        counts=DashboardCounts(
            repositories=repo_count,
            open_prs=open_prs,
            high_risk_prs=high_risk_prs,
            active_flags=active_flags,
            active_experiments=active_experiments,
            pending_approvals=pending_approvals,
            unhealthy_flags=unhealthy_flags,
        ),
        integration_status={
            "github_connected": github_connected,
            "flagsmith_connected": flagsmith_connected,
            "gemini_configured": gemini_client.is_configured,
            "analytics_mode": ws.flagsmith_analytics_mode.value,
        },
        recent_rollouts=[],
        recent_audits=[],
    ).model_dump(mode="json")
    base_resp["recent_rollouts"] = recent_rollouts
    base_resp["recent_audits"] = recent_audits
    base_resp["average_health_score"] = (
        round(float(avg_score_val), 2) if avg_score_val is not None else 100.0
    )
    return base_resp


def _dashboard_filter_from_params(
    range_preset: TimeRangePreset,
    start_date: str | None,
    end_date: str | None,
    environment: str | None,
    repository_id: uuid.UUID | None,
    flag_key: str | None,
    experiment_id: uuid.UUID | None,
    event_source: Literal["all", "simulator_generated", "application_telemetry"],
) -> Any:
    return resolve_time_window(
        range_preset=range_preset,
        start_raw=start_date,
        end_raw=end_date,
        environment=environment,
        repository_id=repository_id,
        flag_key=flag_key,
        experiment_id=experiment_id,
        event_source=event_source,
    )


@dashboard_router.get("/metrics", response_model=DashboardMetricsOut)
async def get_dashboard_metrics(
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "7d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    flag_key: Annotated[str | None, Query()] = None,
    experiment_id: Annotated[uuid.UUID | None, Query()] = None,
    event_source: Annotated[
        Literal["all", "simulator_generated", "application_telemetry"], Query()
    ] = "all",
) -> DashboardMetricsOut:
    """Return all 6 operational metric domains computed strictly from database state."""
    flt = _dashboard_filter_from_params(
        range_preset, start_date, end_date, environment, repository_id, flag_key, experiment_id, event_source
    )
    dataset = await load_scoped_dataset(session, flt)
    return compute_dashboard_metrics(dataset, flt)


@dashboard_router.get("/charts", response_model=DashboardChartsOut)
async def get_dashboard_charts(
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "7d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    flag_key: Annotated[str | None, Query()] = None,
    experiment_id: Annotated[uuid.UUID | None, Query()] = None,
    event_source: Annotated[
        Literal["all", "simulator_generated", "application_telemetry"], Query()
    ] = "all",
) -> DashboardChartsOut:
    """Return Chart A–F series backed directly by database rows (no hardcoded chart arrays)."""
    flt = _dashboard_filter_from_params(
        range_preset, start_date, end_date, environment, repository_id, flag_key, experiment_id, event_source
    )
    dataset = await load_scoped_dataset(session, flt)
    return compute_dashboard_charts(dataset, flt)


@dashboard_router.get("/activity", response_model=list[ActivityFeedItem])
async def get_dashboard_activity(
    session: DBSession,
    _: AuthenticatedOperator,
    environment: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[ActivityFeedItem]:
    """Return chronological activity feed derived from audit_logs and rollout_history."""
    flt = resolve_time_window("30d", environment=environment)
    dataset = await load_scoped_dataset(session, flt)
    return compute_recent_activity(dataset, limit=limit)


async def _load_dashboard_schedules(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = select(ScheduledJob).order_by(ScheduledJob.next_run_at.asc(), ScheduledJob.created_at.desc())
    jobs = list((await session.execute(stmt)).scalars().all())
    upcoming_schedules: list[dict[str, Any]] = []
    for j in jobs:
        if j.enabled or j.consecutive_failures > 0 or j.last_status == "failed":
            try:
                human_desc = describe_schedule(
                    frequency=j.frequency.value,
                    cron_expression=j.cron_expression,
                    interval_minutes=j.interval_minutes,
                    start_at=j.start_at,
                    timezone=j.timezone,
                )
            except ValueError:
                human_desc = f"{j.frequency.value} ({j.timezone})"
            attention_needed = bool(j.consecutive_failures > 0 or j.last_status == "failed")
            upcoming_schedules.append(
                {
                    "id": str(j.id),
                    "name": j.name,
                    "job_type": j.job_type.value,
                    "enabled": j.enabled,
                    "timezone": j.timezone,
                    "human_description": human_desc,
                    "next_run_at": ensure_utc(j.next_run_at).isoformat() if j.next_run_at else None,
                    "last_run_at": ensure_utc(j.last_run_at).isoformat() if j.last_run_at else None,
                    "last_status": j.last_status,
                    "consecutive_failures": j.consecutive_failures,
                    "attention_needed": attention_needed,
                    "attention_reason": (
                        f"Last status `{j.last_status}` ({j.consecutive_failures} consecutive failure(s))."
                        if attention_needed
                        else None
                    ),
                    "updated_at": ensure_utc(j.updated_at).isoformat() if j.updated_at else utcnow().isoformat(),
                }
            )
    return upcoming_schedules


@dashboard_router.get("/pending-actions", response_model=list[PendingActionItem])
async def get_dashboard_pending_actions(
    session: DBSession,
    _: AuthenticatedOperator,
    environment: Annotated[str | None, Query()] = None,
) -> list[PendingActionItem]:
    """Return prioritized pending approvals, failures, breaches, and integration issues."""
    flt = resolve_time_window("30d", environment=environment)
    dataset = await load_scoped_dataset(session, flt)
    upcoming = await _load_dashboard_schedules(session)
    return compute_pending_actions(dataset, upcoming_schedules=upcoming)


@dashboard_router.get("/overview", response_model=DashboardOverviewOut)
async def get_dashboard_overview(
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "7d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    flag_key: Annotated[str | None, Query()] = None,
    experiment_id: Annotated[uuid.UUID | None, Query()] = None,
    event_source: Annotated[
        Literal["all", "simulator_generated", "application_telemetry"], Query()
    ] = "all",
) -> DashboardOverviewOut:
    """Unified Control Plane overview payload combining metrics, charts A–F, feed, and actions."""
    ws = await PolicyEngine.get_workspace_settings(session)
    await session.commit()

    flt = _dashboard_filter_from_params(
        range_preset, start_date, end_date, environment, repository_id, flag_key, experiment_id, event_source
    )
    dataset = await load_scoped_dataset(session, flt)
    github_connected, flagsmith_connected = await _integration_flags(session)
    gemini_client = build_gemini_client_from_app(request.app)
    upcoming_schedules = await _load_dashboard_schedules(session)

    metrics = compute_dashboard_metrics(dataset, flt)
    charts = compute_dashboard_charts(dataset, flt)
    recent_activity = compute_recent_activity(dataset, limit=25)
    pending_actions = compute_pending_actions(dataset, upcoming_schedules=upcoming_schedules)

    return DashboardOverviewOut(
        metrics=metrics,
        charts=charts,
        recent_activity=recent_activity,
        pending_actions=pending_actions,
        integration_status={
            "github_connected": github_connected,
            "flagsmith_connected": flagsmith_connected,
            "gemini_configured": gemini_client.is_configured,
            "analytics_mode": ws.flagsmith_analytics_mode.value,
            "gemini_model": ws.gemini_model,
        },
        upcoming_schedules=upcoming_schedules[:8],
        data_sources={
            "telemetry_totals": charts.telemetry_totals,
            "classifications": metrics.sources,
        },
    )


async def _probe_http_service(
    url: str,
    *,
    transport: Any | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=1.5, transport=transport) as client:
            resp = await client.get(url)
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            ok = 200 <= resp.status_code < 400
            return {
                "status": "ok" if ok else "degraded",
                "http_status": resp.status_code,
                "latency_ms": latency_ms,
                "url": url,
            }
    except Exception as exc:  # noqa: BLE001
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "status": "unreachable",
            "latency_ms": latency_ms,
            "url": url,
            "error": type(exc).__name__,
        }


async def _collect_integration_diagnostics(
    request: Request,
    session: AsyncSession,
) -> dict[str, Any]:
    cfg = get_settings()
    ws = await PolicyEngine.get_workspace_settings(session)
    rows = list((await session.execute(select(IntegrationConnection))).scalars().all())
    by_provider = {r.provider: r for r in rows}

    err_audits = list(
        (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.status == AuditStatus.FAILED,
                    AuditLog.event_action.ilike("integration%"),
                )
                .order_by(AuditLog.created_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )

    gh_conn = by_provider.get(IntegrationProvider.GITHUB)
    fs_conn = by_provider.get(IntegrationProvider.FLAGSMITH)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)

    return {
        "github": {
            "status": gh_conn.status.value
            if gh_conn
            else ("connected" if cfg.GITHUB_PAT.strip() else "disconnected"),
            "auth_mode": gh_conn.auth_mode.value
            if gh_conn
            else ("pat" if cfg.GITHUB_PAT.strip() else "none"),
            "account": gh_conn.external_account_name if gh_conn else None,
            "last_verified_at": ensure_utc(gh_conn.last_verified_at).isoformat()
            if gh_conn and gh_conn.last_verified_at
            else None,
            "configured": bool(
                (gh_conn and gh_conn.status == IntegrationStatus.CONNECTED) or cfg.GITHUB_PAT.strip()
            ),
        },
        "flagsmith": {
            "status": fs_conn.status.value
            if fs_conn
            else ("connected" if cfg.FLAGSMITH_ORG_TOKEN.strip() else "disconnected"),
            "account": fs_conn.external_account_name if fs_conn else None,
            "analytics_mode": ws.flagsmith_analytics_mode.value,
            "capability_probed_at": ensure_utc(ws.flagsmith_capability_probed_at).isoformat()
            if ws.flagsmith_capability_probed_at
            else None,
            "last_verified_at": ensure_utc(fs_conn.last_verified_at).isoformat()
            if fs_conn and fs_conn.last_verified_at
            else None,
            "configured": bool(
                (fs_conn and fs_conn.status == IntegrationStatus.CONNECTED)
                or cfg.FLAGSMITH_ORG_TOKEN.strip()
            ),
        },
        "gemini": {
            "status": "configured" if gemini_client.is_configured else "unconfigured",
            "configured": gemini_client.is_configured,
            "model": ws.gemini_model,
        },
        "recent_errors": [
            {
                "id": str(a.id),
                "event_action": a.event_action,
                "created_at": ensure_utc(a.created_at).isoformat() if a.created_at else None,
                "details": redact_dict(a.details or {}),
            }
            for a in err_audits
        ],
    }


@system_v1_router.get("/health")
async def get_deep_system_health(
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Deep health diagnostics probing DB latency, integrations, scheduler, workers, and demo app."""
    cfg = get_settings()

    # 1. Database probe
    db_t0 = time.perf_counter()
    db_status = "ok"
    db_error: str | None = None
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_status = "error"
        db_error = type(exc).__name__
    db_latency_ms = round((time.perf_counter() - db_t0) * 1000.0, 2)

    # 2. Scheduler & worker status
    from app.services.scheduler import get_scheduler_service

    sched_status = get_scheduler_service(request.app).get_status()
    active_agents = int(
        (
            await session.execute(
                select(func.count(AgentTask.id)).where(
                    AgentTask.status.in_([AgentTaskStatus.QUEUED, AgentTaskStatus.RUNNING])
                )
            )
        ).scalar_one()
        or 0
    )
    failed_agents = int(
        (
            await session.execute(
                select(func.count(AgentTask.id)).where(AgentTask.status == AgentTaskStatus.FAILED)
            )
        ).scalar_one()
        or 0
    )
    running_jobs = int(
        (
            await session.execute(
                select(func.count(JobExecution.id)).where(
                    JobExecution.execution_status.in_(
                        [JobExecutionStatus.RUNNING, JobExecutionStatus.RETRYING]
                    )
                )
            )
        ).scalar_one()
        or 0
    )
    failed_jobs = int(
        (
            await session.execute(
                select(func.count(JobExecution.id)).where(
                    JobExecution.execution_status == JobExecutionStatus.FAILED
                )
            )
        ).scalar_one()
        or 0
    )
    attention_schedules = int(
        (
            await session.execute(
                select(func.count(ScheduledJob.id)).where(
                    ScheduledJob.consecutive_failures > 0
                )
            )
        ).scalar_one()
        or 0
    )

    # 3. Demo backend & frontend probes (test transports honored; skips external socket hang in unit tests unless configured)
    demo_backend_transport = (
        getattr(request.app.state, "demo_probe_transport", None) if cfg.APP_ENV == "test" else None
    )
    demo_frontend_transport = (
        getattr(request.app.state, "demo_frontend_transport", None) if cfg.APP_ENV == "test" else None
    )
    if cfg.APP_ENV == "test" and demo_backend_transport is None:
        demo_backend = {"status": "not_probed_in_test", "latency_ms": None}
    else:
        demo_url = f"{cfg.NEXT_PUBLIC_DEMO_API_URL.rstrip('/')}/api/health"
        if demo_url.startswith("http://localhost:8001") and cfg.APP_ENV != "test":
            demo_backend = await _probe_http_service("http://demo-backend:8001/api/health")
            if demo_backend["status"] == "unreachable":
                demo_backend = await _probe_http_service(demo_url)
        else:
            demo_backend = await _probe_http_service(demo_url, transport=demo_backend_transport)

    if cfg.APP_ENV == "test" and demo_frontend_transport is None:
        demo_frontend = {"status": "not_probed_in_test", "latency_ms": None}
    else:
        fe_url = cfg.NEXT_PUBLIC_DEMO_APP_URL.rstrip("/")
        if fe_url.startswith("http://localhost:3001") and cfg.APP_ENV != "test":
            demo_frontend = await _probe_http_service("http://demo-frontend:3001")
            if demo_frontend["status"] == "unreachable":
                demo_frontend = await _probe_http_service(fe_url)
        else:
            demo_frontend = await _probe_http_service(fe_url, transport=demo_frontend_transport)

    integrations = await _collect_integration_diagnostics(request, session)

    # 4. API Latency & Error Metrics
    api_metrics = getattr(request.app.state, "api_metrics", None) or {}
    samples = list(api_metrics.get("latency_ms_samples") or [])
    avg_lat = round(sum(samples) / len(samples), 2) if samples else None
    errors_5xx = int(api_metrics.get("errors_5xx", 0))

    has_integration_err = any(
        integrations[k]["status"] in ("error", "expired") for k in ("github", "flagsmith")
    )
    degraded = (
        db_status != "ok"
        or has_integration_err
        or attention_schedules > 0
        or (sched_status.get("last_error") is not None)
        or (demo_backend["status"] == "unreachable" and cfg.APP_ENV != "test")
    )
    overall = "unhealthy" if db_status != "ok" else ("degraded" if degraded else "healthy")

    return {
        "status": overall,
        "checked_at": utcnow().isoformat(),
        "database": {
            "status": db_status,
            "latency_ms": db_latency_ms,
            "error": db_error,
        },
        "backend_api": {
            "status": "ok" if errors_5xx == 0 else "degraded",
            "requests_total": int(api_metrics.get("requests_total", 0)),
            "errors_5xx": errors_5xx,
            "avg_latency_ms": avg_lat,
            "last_error": api_metrics.get("last_error"),
        },
        "scheduler": {
            **sched_status,
            "attention_schedules": attention_schedules,
        },
        "workers": {
            "active_agent_tasks": active_agents,
            "failed_agent_tasks": failed_agents,
            "running_job_executions": running_jobs,
            "failed_job_executions": failed_jobs,
        },
        "integrations": integrations,
        "demo_app": {
            "backend": demo_backend,
            "frontend": demo_frontend,
        },
    }


@system_v1_router.get("/integrations")
async def get_system_integrations_diagnostics(
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    return await _collect_integration_diagnostics(request, session)


@system_v1_router.get("/workers")
async def get_system_workers_status(
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    recent_tasks = list(
        (
            await session.execute(
                select(AgentTask).order_by(AgentTask.created_at.desc()).limit(15)
            )
        )
        .scalars()
        .all()
    )
    recent_jobs = list(
        (
            await session.execute(
                select(JobExecution).order_by(JobExecution.created_at.desc()).limit(15)
            )
        )
        .scalars()
        .all()
    )
    return {
        "agent_tasks": [
            {
                "id": str(t.id),
                "specialist": t.specialist.value,
                "status": t.status.value,
                "gemini_model": t.gemini_model,
                "error_message": t.error_message,
                "started_at": ensure_utc(t.started_at).isoformat() if t.started_at else None,
                "completed_at": ensure_utc(t.completed_at).isoformat() if t.completed_at else None,
            }
            for t in recent_tasks
        ],
        "job_executions": [
            {
                "id": str(j.id),
                "schedule_name": j.schedule_name,
                "job_type": j.job_type.value,
                "status": j.execution_status.value,
                "trigger_type": j.trigger_type.value,
                "retry_count": j.retry_count,
                "error_details": j.error_details,
                "started_at": ensure_utc(j.started_at).isoformat() if j.started_at else None,
                "completed_at": ensure_utc(j.completed_at).isoformat() if j.completed_at else None,
            }
            for j in recent_jobs
        ],
    }


@system_v1_router.get("/scheduler")
async def get_system_scheduler_status(
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    from app.services.scheduler import get_scheduler_service

    svc = get_scheduler_service(request.app)
    jobs = list(
        (await session.execute(select(ScheduledJob).order_by(ScheduledJob.next_run_at.asc()))).scalars().all()
    )
    return {
        "worker": svc.get_status(),
        "counts": {
            "total": len(jobs),
            "enabled": sum(1 for j in jobs if j.enabled),
            "paused": sum(1 for j in jobs if not j.enabled),
            "failing": sum(1 for j in jobs if j.consecutive_failures > 0 or j.last_status == "failed"),
        },
        "upcoming": [
            {
                "id": str(j.id),
                "name": j.name,
                "job_type": j.job_type.value,
                "next_run_at": ensure_utc(j.next_run_at).isoformat() if j.next_run_at else None,
                "last_status": j.last_status,
                "consecutive_failures": j.consecutive_failures,
            }
            for j in jobs
            if j.enabled
        ][:10],
    }
