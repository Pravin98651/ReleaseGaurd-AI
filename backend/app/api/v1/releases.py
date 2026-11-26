"""Release rollout overview and incident investigation endpoints."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.incidents import investigate_incident
from app.api.deps import (
    AuthenticatedOperator,
    DBSession,
    DefaultOperator,
    DeveloperOperator,
    build_gemini_client_from_app,
    build_github_client,
    build_tool_context,
)
from app.api.v1.errors import APIException
from app.core.database import utcnow
from app.models import (
    ActorType,
    Approval,
    AuditStatus,
    Experiment,
    ExperimentEvent,
    FeatureFlag,
    FlagType,
    PolicyCategory,
    PullRequest,
    Release,
    ReleaseArtifact,
    ReleaseCheck,
    ReleaseEvent,
    ReleaseReadinessStatus,
    ReleaseStage,
    ReleaseStatus,
    RiskLevel,
    RolloutHistory,
)
from app.schemas import (
    ApprovalOut,
    ConfoundingCheckItem,
    IncidentInvestigateRequest,
    IncidentInvestigateResponse,
    PaginatedResponse,
    ReleaseApprovalCreateRequest,
    ReleaseArtifactOut,
    ReleaseCheckOut,
    ReleaseCreateRequest,
    ReleaseDetailOut,
    ReleaseEventOut,
    ReleaseLifecycleActionRequest,
    ReleaseOut,
    ReleaseStageOut,
    ReleaseUpdateRequest,
    RolloutHistoryOut,
    TemporalCorrelationItem,
)
from app.services.policy_engine import PolicyEngine, is_production_environment
from app.services.release_orchestrator import ReleaseOrchestrator
from app.services.release_readiness import ReleaseReadinessService
from app.services.release_state_machine import ReleaseTransitionError

releases_router = APIRouter(prefix="/releases", tags=["releases"])


@releases_router.get("/overview")
async def get_releases_overview(
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Return active gradual rollouts, recent history, recommendations, and incident alerts."""
    flags = (
        (await session.execute(select(FeatureFlag).where(FeatureFlag.is_archived.is_(False))))
        .scalars()
        .all()
    )
    flag_by_id = {f.id: f for f in flags}

    experiments = (
        (await session.execute(select(Experiment).order_by(Experiment.updated_at.desc())))
        .scalars()
        .all()
    )
    latest_exp_by_flag_env: dict[tuple[Any, str], Experiment] = {}
    for exp in experiments:
        key = (exp.feature_flag_id, exp.environment_name.lower())
        if key not in latest_exp_by_flag_env:
            latest_exp_by_flag_env[key] = exp

    rollouts = (
        (
            await session.execute(
                select(RolloutHistory).order_by(RolloutHistory.created_at.desc()).limit(30)
            )
        )
        .scalars()
        .all()
    )
    latest_rh_by_flag_env: dict[tuple[Any, str], RolloutHistory] = {}
    for rh in rollouts:
        key = (rh.feature_flag_id, rh.environment_name.lower())
        if key not in latest_rh_by_flag_env:
            latest_rh_by_flag_env[key] = rh

    active_rollouts: list[dict[str, Any]] = []
    for f in flags:
        for env_name, st in (f.env_states or {}).items():
            if not isinstance(st, dict):
                continue
            env_norm = env_name.strip().lower()
            pct = int(st.get("rollout_percentage") or (100 if st.get("enabled") else 0))
            exp = latest_exp_by_flag_env.get((f.id, env_norm))
            rh = latest_rh_by_flag_env.get((f.id, env_norm))
            is_prod = bool(
                st.get("is_production")
                if "is_production" in st
                else is_production_environment(env_norm, f.env_states)
            )
            active_rollouts.append(
                {
                    "feature_flag_id": str(f.id),
                    "flag_key": f.key,
                    "environment_name": env_norm,
                    "is_production": is_prod,
                    "enabled": bool(st.get("enabled", False)),
                    "current_percentage": pct,
                    "strategy": "multivariate"
                    if f.flag_type == FlagType.MULTIVARIATE
                    else "percentage_split_segment",
                    "latest_recommendation": exp.latest_recommendation.value
                    if exp and exp.latest_recommendation
                    else None,
                    "last_updated_at": (
                        rh.created_at.isoformat() if rh else f.updated_at.isoformat()
                    ),
                    "verified": rh.verified if rh else None,
                }
            )

    recent_history: list[dict[str, Any]] = []
    for rh in rollouts[:15]:
        d = RolloutHistoryOut.model_validate(rh).model_dump(mode="json")
        flag_obj = flag_by_id.get(rh.feature_flag_id)
        d["flag_key"] = flag_obj.key if flag_obj else None
        recent_history.append(d)

    recommendations: list[dict[str, Any]] = []
    for exp in experiments:
        if exp.latest_recommendation is not None:
            flag_obj = flag_by_id.get(exp.feature_flag_id)
            recommendations.append(
                {
                    "experiment_id": str(exp.id),
                    "flag_key": flag_obj.key if flag_obj else None,
                    "environment_name": exp.environment_name,
                    "recommendation": exp.latest_recommendation.value,
                    "rationale": exp.recommendation_rationale,
                    "narrative": exp.recommendation_rationale,
                }
            )

    # Surface error alerts if any recent flag environment exceeds guardrail ceiling
    ws = await PolicyEngine.get_workspace_settings(session)
    max_err = float((ws.default_guardrails or {}).get("guardrail_max_error_rate", 0.05))
    cutoff = utcnow() - timedelta(minutes=60)
    recent_events = (
        (
            await session.execute(
                select(ExperimentEvent).where(ExperimentEvent.occurred_at >= cutoff)
            )
        )
        .scalars()
        .all()
    )
    by_flag_env: dict[tuple[str, str], dict[str, int]] = {}
    for ev in recent_events:
        bucket = by_flag_env.setdefault(
            (ev.flag_key, ev.environment_name), {"exposures": 0, "failed": 0}
        )
        if ev.event_type.value == "feature_variation_assigned":
            bucket["exposures"] += 1
        elif ev.event_type.value == "payment_failed":
            bucket["failed"] += 1

    incident_flags: list[dict[str, Any]] = []
    for (fk, env_n), counts in by_flag_env.items():
        if counts["exposures"] >= 10 and counts["failed"] > 0:
            rate = counts["failed"] / counts["exposures"]
            if rate >= max_err:
                incident_flags.append(
                    {
                        "flag_key": fk,
                        "environment_name": env_n,
                        "error_rate": round(rate, 4),
                        "severity": "critical" if rate >= max_err * 2 else "high",
                        "summary": f"{counts['failed']} payment_failed across {counts['exposures']} exposures ({rate:.1%}).",
                    }
                )

    return {
        "active_rollouts": active_rollouts,
        "recent_history": recent_history,
        "recent_rollouts": recent_history,
        "recommendations": recommendations,
        "incident_alerts": incident_flags,
        "incident_flags": incident_flags,
    }


@releases_router.post("/investigate")
async def investigate_release_incident(
    body: IncidentInvestigateRequest,
    session: DBSession,
    _: DefaultOperator,
) -> dict[str, Any]:
    """Category A: Investigate error/abandonment spikes with explicit correlation != causation."""
    env_norm = body.environment_name.strip().lower()
    cutoff = utcnow() - timedelta(minutes=body.window_minutes)

    ev_stmt = select(ExperimentEvent).where(
        ExperimentEvent.environment_name == env_norm,
        ExperimentEvent.occurred_at >= cutoff,
    )
    if body.flag_key:
        ev_stmt = ev_stmt.where(ExperimentEvent.flag_key == body.flag_key)
    events = list((await session.execute(ev_stmt)).scalars().all())

    rollouts = list(
        (
            await session.execute(
                select(RolloutHistory).where(RolloutHistory.environment_name == env_norm)
            )
        )
        .scalars()
        .all()
    )
    prs = list((await session.execute(select(PullRequest))).scalars().all())
    flags = list((await session.execute(select(FeatureFlag))).scalars().all())
    flag_id_to_key = {str(f.id): f.key for f in flags}

    inv = investigate_incident(
        events=events,
        rollout_history=rollouts,
        pull_requests=prs,
        flag_key=body.flag_key,
        flag_id_to_key=flag_id_to_key,
        environment_name=env_norm,
        window_minutes=body.window_minutes,
    )

    mapped_correlations = [
        TemporalCorrelationItem(
            source_type="rollout" if c.source_type == "rollout_history" else "pull_request",
            source_id=c.event_id,
            timestamp=c.timestamp,
            delta_minutes=c.delta_minutes_from_onset,
            correlation_score=c.temporal_score,
            summary=c.title,
            correlation_not_causation=True,
        )
        for c in inv.temporal_correlations
    ]
    mapped_confounders = [
        ConfoundingCheckItem(
            check_name=chk.check_name,
            likely_confounder=chk.confounder_detected,
            summary=chk.summary,
            metrics=chk.evidence,
        )
        for chk in inv.confounding_checks
    ]

    resp_model = IncidentInvestigateResponse(
        environment_name=env_norm,
        flag_key=body.flag_key,
        correlation_not_causation=True,
        observed_metrics=inv.observed_metrics.model_dump(mode="json"),
        temporal_correlations=mapped_correlations,
        confounding_checks=mapped_confounders,
        recommended_next_steps=inv.recommended_next_steps,
    )

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=None,
        policy_category=PolicyCategory.A,
        event_action="incident.investigated",
        resource_type="feature_flag",
        resource_id=body.flag_key,
        environment_name=env_norm,
        status=AuditStatus.SUCCESS,
        details={
            "window_minutes": body.window_minutes,
            "spike_detected": inv.observed_metrics.spike_detected,
        },
    )
    await session.commit()

    payload = resp_model.model_dump(mode="json")
    payload["causal_disclaimer"] = inv.causal_disclaimer
    return payload


# ------------------------------------------------------------------------------
# Autonomous Release Lifecycle Engine Endpoints
# ------------------------------------------------------------------------------


async def _load_release_or_404(session: AsyncSession, release_id: uuid.UUID) -> Release:
    release = await session.get(Release, release_id)
    if release is None:
        raise APIException(
            status_code=404,
            code="release_not_found",
            message=f"Release {release_id} not found.",
        )
    return release


async def _build_release_detail(session: AsyncSession, release: Release) -> ReleaseDetailOut:
    stages = list(
        (
            await session.execute(
                select(ReleaseStage)
                .where(ReleaseStage.release_id == release.id)
                .order_by(ReleaseStage.stage_order)
            )
        )
        .scalars()
        .all()
    )
    checks = list(
        (
            await session.execute(
                select(ReleaseCheck)
                .where(ReleaseCheck.release_id == release.id)
                .order_by(ReleaseCheck.check_key)
            )
        )
        .scalars()
        .all()
    )
    events = list(
        (
            await session.execute(
                select(ReleaseEvent)
                .where(ReleaseEvent.release_id == release.id)
                .order_by(ReleaseEvent.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    artifacts = list(
        (
            await session.execute(
                select(ReleaseArtifact)
                .where(ReleaseArtifact.release_id == release.id)
                .order_by(ReleaseArtifact.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    approvals = list(
        (
            await session.execute(
                select(Approval)
                .where(Approval.release_id == release.id)
                .order_by(Approval.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return ReleaseDetailOut(
        release=ReleaseOut.model_validate(release),
        stages=[ReleaseStageOut.model_validate(s) for s in stages],
        checks=[ReleaseCheckOut.model_validate(c) for c in checks],
        events=[ReleaseEventOut.model_validate(e) for e in events],
        artifacts=[ReleaseArtifactOut.model_validate(a) for a in artifacts],
        approvals=[ApprovalOut.model_validate(ap) for ap in approvals],
    )


def _raise_transition_api_error(exc: ReleaseTransitionError) -> None:
    code = (
        "release_version_conflict"
        if exc.status_code == 409
        else "release_not_found"
        if exc.status_code == 404
        else "release_invalid_transition"
    )
    raise APIException(status_code=exc.status_code, code=code, message=exc.message) from exc


@releases_router.get("", response_model=PaginatedResponse[ReleaseOut])
async def list_releases(
    session: DBSession,
    _: AuthenticatedOperator,
    q: Annotated[str | None, Query(max_length=120)] = None,
    status: Annotated[ReleaseStatus | None, Query()] = None,
    readiness_status: Annotated[ReleaseReadinessStatus | None, Query()] = None,
    environment: Annotated[str | None, Query(max_length=64)] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    owner_id: Annotated[uuid.UUID | None, Query()] = None,
    risk_level: Annotated[RiskLevel | None, Query()] = None,
    sort_by: Annotated[str, Query()] = "updated_at",
    sort_dir: Annotated[str, Query()] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[ReleaseOut]:
    """List releases with search, filters, sorting, and pagination."""
    base_q = select(Release)
    count_q = select(func.count(Release.id))

    if q:
        like = f"%{q.strip()}%"
        cond = or_(
            Release.title.ilike(like),
            Release.release_key.ilike(like),
            Release.description.ilike(like),
        )
        base_q = base_q.where(cond)
        count_q = count_q.where(cond)
    if status is not None:
        base_q = base_q.where(Release.status == status)
        count_q = count_q.where(Release.status == status)
    if readiness_status is not None:
        base_q = base_q.where(Release.readiness_status == readiness_status)
        count_q = count_q.where(Release.readiness_status == readiness_status)
    if environment:
        env_norm = environment.strip().lower()
        base_q = base_q.where(Release.target_environment == env_norm)
        count_q = count_q.where(Release.target_environment == env_norm)
    if repository_id is not None:
        base_q = base_q.where(Release.repository_id == repository_id)
        count_q = count_q.where(Release.repository_id == repository_id)
    if owner_id is not None:
        base_q = base_q.where(Release.owner_id == owner_id)
        count_q = count_q.where(Release.owner_id == owner_id)
    if risk_level is not None:
        base_q = base_q.where(Release.risk_level == risk_level)
        count_q = count_q.where(Release.risk_level == risk_level)

    sort_col = {
        "created_at": Release.created_at,
        "updated_at": Release.updated_at,
        "readiness_score": Release.readiness_score,
        "release_key": Release.release_key,
    }.get(sort_by, Release.updated_at)
    order_expr = sort_col.asc() if sort_dir.lower() == "asc" else sort_col.desc()

    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = (
        await session.execute(base_q.order_by(order_expr).limit(limit).offset(offset))
    ).scalars().all()
    return PaginatedResponse[ReleaseOut](
        items=[ReleaseOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@releases_router.post("", response_model=ReleaseDetailOut, status_code=201)
async def create_release(
    body: ReleaseCreateRequest,
    request: Request,
    session: DBSession,
    operator: DeveloperOperator,
) -> ReleaseDetailOut:
    """Create a new Release (manual or PR-linked) with initialized stages and readiness."""
    gemini_client = None
    if body.ai_assist:
        ws = await PolicyEngine.get_workspace_settings(session)
        gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)

    try:
        release = await ReleaseOrchestrator.create_release(
            session,
            title=body.title,
            description=body.description,
            release_type=body.release_type,
            target_environment=body.target_environment,
            repository_id=body.repository_id,
            pull_request_id=body.pull_request_id,
            feature_flag_id=body.feature_flag_id,
            experiment_id=body.experiment_id,
            owner_id=body.owner_id or operator.id,
            created_by_id=operator.id,
            risk_level=body.risk_level,
            target_rollout_percentage=body.target_rollout_percentage,
            scheduled_for=body.scheduled_for,
            ai_assist=body.ai_assist,
            allow_duplicate=body.allow_duplicate,
            gemini_client=gemini_client,
            metadata=body.metadata,
        )
    except ReleaseTransitionError as exc:
        _raise_transition_api_error(exc)

    await session.commit()
    return await _build_release_detail(session, release)


@releases_router.get("/{release_id}", response_model=ReleaseDetailOut)
async def get_release_detail(
    release_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> ReleaseDetailOut:
    """Get full Release detail including stages, checks, timeline events, artifacts, and approvals."""
    release = await _load_release_or_404(session, release_id)
    return await _build_release_detail(session, release)


@releases_router.patch("/{release_id}", response_model=ReleaseDetailOut)
async def update_release(
    release_id: uuid.UUID,
    body: ReleaseUpdateRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> ReleaseDetailOut:
    """Update Release metadata or targets with optimistic concurrency and stale approval invalidation."""
    release = await _load_release_or_404(session, release_id)
    try:
        await ReleaseOrchestrator.update_release(
            session,
            release,
            expected_version=body.expected_version,
            actor_id=operator.id,
            title=body.title,
            description=body.description,
            target_environment=body.target_environment,
            feature_flag_id=body.feature_flag_id,
            experiment_id=body.experiment_id,
            risk_level=body.risk_level,
            target_rollout_percentage=body.target_rollout_percentage,
            scheduled_for=body.scheduled_for,
        )
    except ReleaseTransitionError as exc:
        _raise_transition_api_error(exc)

    await session.commit()
    return await _build_release_detail(session, release)


@releases_router.post("/{release_id}/analyze", response_model=ReleaseDetailOut)
async def analyze_release(
    release_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DeveloperOperator,
) -> ReleaseDetailOut:
    """Run PR & risk analysis stage for a release."""
    release = await _load_release_or_404(session, release_id)
    ws = await PolicyEngine.get_workspace_settings(session)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)
    github_client = await build_github_client(request.app, session)

    try:
        await ReleaseOrchestrator.run_pr_analysis_stage(
            session,
            release,
            actor_id=operator.id,
            github_client=github_client,
            gemini_client=gemini_client,
        )
    except ReleaseTransitionError as exc:
        _raise_transition_api_error(exc)

    await session.commit()
    return await _build_release_detail(session, release)


@releases_router.post("/{release_id}/validate", response_model=ReleaseDetailOut)
async def validate_release(
    release_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DeveloperOperator,
) -> ReleaseDetailOut:
    """Run isolated patch validation stage for a release."""
    release = await _load_release_or_404(session, release_id)
    ctx = await build_tool_context(request.app, session, actor_id=operator.id)
    try:
        await ReleaseOrchestrator.run_patch_validation_stage(
            session, release, ctx=ctx, actor_id=operator.id
        )
    except ReleaseTransitionError as exc:
        _raise_transition_api_error(exc)

    await session.commit()
    return await _build_release_detail(session, release)


@releases_router.post("/{release_id}/readiness/evaluate", response_model=ReleaseDetailOut)
async def evaluate_release_readiness(
    release_id: uuid.UUID,
    session: DBSession,
    operator: DeveloperOperator,
) -> ReleaseDetailOut:
    """Recompute deterministic readiness checks and score for a release."""
    release = await _load_release_or_404(session, release_id)
    await ReleaseReadinessService.evaluate_release(
        session, release, actor_id=operator.id, actor_type=ActorType.USER
    )
    await session.commit()
    return await _build_release_detail(session, release)


@releases_router.get("/{release_id}/stages", response_model=list[ReleaseStageOut])
async def get_release_stages(
    release_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> list[ReleaseStageOut]:
    await _load_release_or_404(session, release_id)
    rows = (
        await session.execute(
            select(ReleaseStage)
            .where(ReleaseStage.release_id == release_id)
            .order_by(ReleaseStage.stage_order)
        )
    ).scalars().all()
    return [ReleaseStageOut.model_validate(s) for s in rows]


@releases_router.get("/{release_id}/timeline", response_model=list[ReleaseEventOut])
async def get_release_timeline(
    release_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
    event_type: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ReleaseEventOut]:
    await _load_release_or_404(session, release_id)
    stmt = select(ReleaseEvent).where(ReleaseEvent.release_id == release_id)
    if event_type:
        stmt = stmt.where(ReleaseEvent.event_type == event_type)
    rows = (
        await session.execute(stmt.order_by(ReleaseEvent.created_at.desc()).limit(limit))
    ).scalars().all()
    return [ReleaseEventOut.model_validate(e) for e in rows]


@releases_router.get("/{release_id}/checks", response_model=list[ReleaseCheckOut])
async def get_release_checks(
    release_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> list[ReleaseCheckOut]:
    await _load_release_or_404(session, release_id)
    rows = (
        await session.execute(
            select(ReleaseCheck)
            .where(ReleaseCheck.release_id == release_id)
            .order_by(ReleaseCheck.check_key)
        )
    ).scalars().all()
    return [ReleaseCheckOut.model_validate(c) for c in rows]


@releases_router.get("/{release_id}/artifacts", response_model=list[ReleaseArtifactOut])
async def get_release_artifacts(
    release_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> list[ReleaseArtifactOut]:
    await _load_release_or_404(session, release_id)
    rows = (
        await session.execute(
            select(ReleaseArtifact)
            .where(ReleaseArtifact.release_id == release_id)
            .order_by(ReleaseArtifact.created_at.desc())
        )
    ).scalars().all()
    return [ReleaseArtifactOut.model_validate(a) for a in rows]


@releases_router.post("/{release_id}/pause", response_model=ReleaseDetailOut)
async def pause_release(
    release_id: uuid.UUID,
    body: ReleaseLifecycleActionRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> ReleaseDetailOut:
    release = await _load_release_or_404(session, release_id)
    try:
        await ReleaseOrchestrator.pause_release(
            session,
            release,
            actor_id=operator.id,
            expected_version=body.expected_version,
            reason=body.reason,
        )
    except ReleaseTransitionError as exc:
        _raise_transition_api_error(exc)
    await session.commit()
    return await _build_release_detail(session, release)


@releases_router.post("/{release_id}/resume", response_model=ReleaseDetailOut)
async def resume_release(
    release_id: uuid.UUID,
    body: ReleaseLifecycleActionRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> ReleaseDetailOut:
    release = await _load_release_or_404(session, release_id)
    try:
        await ReleaseOrchestrator.resume_release(
            session,
            release,
            actor_id=operator.id,
            expected_version=body.expected_version,
        )
    except ReleaseTransitionError as exc:
        _raise_transition_api_error(exc)
    await session.commit()
    return await _build_release_detail(session, release)


@releases_router.post("/{release_id}/retry", response_model=ReleaseDetailOut)
async def retry_release(
    release_id: uuid.UUID,
    body: ReleaseLifecycleActionRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> ReleaseDetailOut:
    release = await _load_release_or_404(session, release_id)
    try:
        await ReleaseOrchestrator.retry_release(
            session,
            release,
            actor_id=operator.id,
            expected_version=body.expected_version,
        )
    except ReleaseTransitionError as exc:
        _raise_transition_api_error(exc)
    await session.commit()
    return await _build_release_detail(session, release)


@releases_router.post("/{release_id}/cancel", response_model=ReleaseDetailOut)
async def cancel_release(
    release_id: uuid.UUID,
    body: ReleaseLifecycleActionRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> ReleaseDetailOut:
    release = await _load_release_or_404(session, release_id)
    try:
        await ReleaseOrchestrator.cancel_release(
            session,
            release,
            actor_id=operator.id,
            expected_version=body.expected_version,
            reason=body.reason,
        )
    except ReleaseTransitionError as exc:
        _raise_transition_api_error(exc)
    await session.commit()
    return await _build_release_detail(session, release)


@releases_router.post("/{release_id}/approval-request", response_model=ApprovalOut, status_code=201)
async def request_release_approval(
    release_id: uuid.UUID,
    body: ReleaseApprovalCreateRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> ApprovalOut:
    release = await _load_release_or_404(session, release_id)
    try:
        approval = await ReleaseOrchestrator.request_release_approval(
            session, release, actor_id=operator.id, rationale=body.rationale
        )
    except ReleaseTransitionError as exc:
        _raise_transition_api_error(exc)
    await session.commit()
    return ApprovalOut.model_validate(approval)


@releases_router.get("/{release_id}/report")
async def get_release_report(
    release_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    release = await _load_release_or_404(session, release_id)
    detail = await _build_release_detail(session, release)
    stages = list(
        (
            await session.execute(
                select(ReleaseStage)
                .where(ReleaseStage.release_id == release.id)
                .order_by(ReleaseStage.stage_order)
            )
        )
        .scalars()
        .all()
    )
    checks = list(
        (await session.execute(select(ReleaseCheck).where(ReleaseCheck.release_id == release.id)))
        .scalars()
        .all()
    )
    events = list(
        (
            await session.execute(
                select(ReleaseEvent)
                .where(ReleaseEvent.release_id == release.id)
                .order_by(ReleaseEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    artifacts = list(
        (
            await session.execute(
                select(ReleaseArtifact).where(ReleaseArtifact.release_id == release.id)
            )
        )
        .scalars()
        .all()
    )
    approvals = list(
        (await session.execute(select(Approval).where(Approval.release_id == release.id)))
        .scalars()
        .all()
    )
    del detail
    return ReleaseOrchestrator.build_release_report(
        release,
        stages=stages,
        checks=checks,
        events=events,
        artifacts=artifacts,
        approvals=approvals,
    )


@releases_router.get("/{release_id}/report/export")
async def export_release_report_markdown(
    release_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> Response:
    report = await get_release_report(release_id, session, _)
    md = ReleaseOrchestrator.render_release_report_markdown(report)
    filename = f"{report['release_key']}-report.md"
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@releases_router.post("/plan-from-prompt", status_code=201)
async def plan_release_from_prompt(
    body: dict[str, Any],
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> dict[str, Any]:
    """Category A: Convert a natural-language feature request into a flagged rollout plan."""
    from app.services.feature_request_planner import plan_feature_request_from_prompt

    prompt = str(body.get("prompt") or "").strip()
    if len(prompt) < 8:
        raise APIException(
            422,
            "INVALID_FEATURE_REQUEST",
            "Feature request prompt must be at least 8 characters.",
        )
    env_name = str(body.get("environment_name") or "development").strip().lower()
    if not env_name:
        env_name = "development"
    repo_raw = body.get("repository_id")
    repo_id = uuid.UUID(str(repo_raw)) if repo_raw else None
    create_release = bool(body.get("create_release", True))

    ws = await PolicyEngine.get_workspace_settings(session)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)
    return await plan_feature_request_from_prompt(
        session,
        prompt=prompt,
        user_id=operator.id,
        repository_id=repo_id,
        environment_name=env_name,
        create_release=create_release,
        gemini_client=gemini_client,
    )
