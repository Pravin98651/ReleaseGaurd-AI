"""System health and executive dashboard summary endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DBSession, build_gemini_client_from_app
from app.core.config import get_settings
from app.models import (
    Approval,
    ApprovalStatus,
    AuditLog,
    Experiment,
    ExperimentStatus,
    FeatureFlag,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    PullRequest,
    PullRequestState,
    Repository,
    RiskLevel,
    RolloutHistory,
)
from app.schemas import (
    AuditLogOut,
    DashboardCounts,
    DashboardSummaryResponse,
    HealthResponse,
    RolloutHistoryOut,
)
from app.services.policy_engine import PolicyEngine

system_router = APIRouter(tags=["system"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])


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
