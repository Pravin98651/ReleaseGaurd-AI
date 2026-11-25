"""Release rollout overview and incident investigation endpoints."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.analytics.incidents import investigate_incident
from app.api.deps import DBSession
from app.core.database import utcnow
from app.models import (
    ActorType,
    AuditStatus,
    Experiment,
    ExperimentEvent,
    FeatureFlag,
    FlagType,
    PolicyCategory,
    PullRequest,
    RolloutHistory,
)
from app.schemas import (
    ConfoundingCheckItem,
    IncidentInvestigateRequest,
    IncidentInvestigateResponse,
    RolloutHistoryOut,
    TemporalCorrelationItem,
)
from app.services.policy_engine import PolicyEngine, is_production_environment

releases_router = APIRouter(prefix="/releases", tags=["releases"])


@releases_router.get("/overview")
async def get_releases_overview(
    session: DBSession,
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
