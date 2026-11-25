"""Shared real-database aggregation engine for Dashboard Overview and Metrics Analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.experiment_tools import build_variation_map
from app.analytics.bayesian import analyze_experiment_events
from app.core.database import ensure_utc, utcnow
from app.models import (
    AgentTask,
    AgentTaskStatus,
    AnalysisStatus,
    Approval,
    ApprovalStatus,
    AuditLog,
    CodeChange,
    Experiment,
    ExperimentEvent,
    ExperimentStatus,
    FeatureFlag,
    IntegrationConnection,
    IntegrationStatus,
    PullRequest,
    ReleaseRecommendation,
    Repository,
    RolloutAction,
    RolloutHistory,
    SpecialistRole,
)
from app.schemas.analytics import (
    ActivityFeedItem,
    AgentActivityMetricsBlock,
    AgentExecutionPoint,
    ApprovalActivityMetricsBlock,
    DashboardChartsOut,
    DashboardMetricsOut,
    DistributionSlice,
    EngineeringMetricsBlock,
    ExperimentationMetricsBlock,
    ExperimentPerformancePoint,
    FeatureFlagMetricsBlock,
    FilterWindowMeta,
    MetricDefinitionDoc,
    MetricTrend,
    PendingActionItem,
    ReleaseActivityPoint,
    ReleaseOperationsMetricsBlock,
    TelemetryEventPoint,
    TimeRangePreset,
)

_RANGE_DELTAS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


def resolve_time_window(
    range_preset: TimeRangePreset = "7d",
    start_raw: str | None = None,
    end_raw: str | None = None,
    *,
    environment: str | None = None,
    repository_id: UUID | None = None,
    flag_key: str | None = None,
    experiment_id: UUID | None = None,
    event_source: Literal["all", "simulator_generated", "application_telemetry"] = "all",
) -> FilterWindowMeta:
    """Resolve explicit UTC bounds cleanly for presets or custom date filters."""
    now = utcnow()
    end_at = _parse_iso_dt(end_raw) or now
    if range_preset == "custom" and start_raw:
        start_at = _parse_iso_dt(start_raw) or (end_at - timedelta(days=7))
        if start_at > end_at:
            start_at = end_at - timedelta(days=1)
    else:
        delta = _RANGE_DELTAS.get(range_preset, timedelta(days=7))
        start_at = _parse_iso_dt(start_raw) if range_preset == "custom" else (end_at - delta)
        if start_at is None:
            start_at = end_at - delta

    return FilterWindowMeta(
        range=range_preset,
        start_at=start_at,
        end_at=end_at,
        environment=environment.strip().lower() if environment and environment.strip() else None,
        repository_id=repository_id,
        flag_key=flag_key.strip() if flag_key and flag_key.strip() else None,
        experiment_id=experiment_id,
        event_source=event_source,
    )


def _parse_iso_dt(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except ValueError:
        return None


def is_simulator_event(ev: ExperimentEvent) -> bool:
    """Distinguish simulator-generated journeys cleanly from interactive telemetry."""
    sess = (ev.session_key or "").strip()
    uid = (ev.user_identifier or "").strip()
    props = ev.properties or {}
    return sess.startswith("sim_") or uid.startswith("sim_") or bool(props.get("simulator"))


def _compute_trend(current: float, previous: float, prior_history_exists: bool) -> MetricTrend | None:
    """Emit trend only when real historical records exist in the comparison period."""
    if not prior_history_exists or previous == 0:
        return None
    delta = current - previous
    pct = round((delta / previous) * 100.0, 1)
    direction: Literal["up", "down", "flat"] = "up" if delta > 0 else ("down" if delta < 0 else "flat")
    return MetricTrend(
        previous_value=previous,
        delta=round(delta, 2),
        percent_change=pct,
        direction=direction,
    )


def _in_window(dt: datetime | None, start: datetime, end: datetime) -> bool:
    utc_dt = ensure_utc(dt)
    return utc_dt is not None and start <= utc_dt <= end


def _bucket_spec(start: datetime, end: datetime) -> tuple[list[tuple[datetime, datetime, str, str]], bool]:
    """Build chronological time buckets (hourly <= 48h, daily <= 90d, weekly beyond)."""
    span_hours = max(1.0, (end - start).total_seconds() / 3600.0)
    buckets: list[tuple[datetime, datetime, str, str]] = []
    if span_hours <= 48.0:
        step = timedelta(hours=1 if span_hours <= 24.5 else 3)
        cur = start.replace(minute=0, second=0, microsecond=0)
        while cur <= end:
            nxt = cur + step
            iso = cur.isoformat()
            lbl = cur.strftime("%H:%M")
            buckets.append((cur, nxt, iso, lbl))
            cur = nxt
        return buckets, True

    cur_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    step_days = 1 if span_hours <= 24 * 35 else 7
    step = timedelta(days=step_days)
    while cur_day <= end:
        nxt = cur_day + step
        iso = cur_day.date().isoformat()
        lbl = cur_day.strftime("%b %d")
        buckets.append((cur_day, nxt, iso, lbl))
        cur_day = nxt
    return buckets, False


def _bucket_index(
    dt: datetime | None,
    buckets: list[tuple[datetime, datetime, str, str]],
) -> int | None:
    utc_dt = ensure_utc(dt)
    if utc_dt is None or not buckets:
        return None
    for idx, (b_start, b_end, _, _) in enumerate(buckets):
        if b_start <= utc_dt < b_end:
            return idx
    if utc_dt == buckets[-1][1] or (utc_dt >= buckets[-1][0] and utc_dt <= buckets[-1][1] + timedelta(seconds=1)):
        return len(buckets) - 1
    return None


def _map_href_for_resource(resource_type: str, resource_id: str | None) -> str:
    rt = (resource_type or "").lower()
    if rt in ("repository", "repositories"):
        return "/repositories"
    if rt in ("pull_request", "pull_requests", "code_change"):
        return "/pull-requests"
    if rt in ("feature_flag", "flag", "flagsmith_segment"):
        return "/feature-flags"
    if rt in ("experiment", "experiments"):
        return f"/experiments?id={resource_id}" if resource_id else "/experiments"
    if rt in ("approval", "approvals"):
        return "/approvals"
    if rt in ("agent_task", "agent_session", "agent_tool"):
        return "/agent"
    if rt in ("integration", "workspace_settings"):
        return "/settings"
    if rt in ("scheduled_job", "schedule", "job_execution"):
        return "/schedules"
    return "/audit-logs"


async def load_scoped_dataset(
    session: AsyncSession,
    flt: FilterWindowMeta,
) -> dict[str, Any]:
    """Load ORM entities cleanly once so Dashboard and Metrics reuse a single query pass."""
    repos = list((await session.execute(select(Repository))).scalars().all())
    prs = list((await session.execute(select(PullRequest))).scalars().all())
    changes = list((await session.execute(select(CodeChange))).scalars().all())
    flags = list((await session.execute(select(FeatureFlag).where(FeatureFlag.is_archived.is_(False)))).scalars().all())
    rollouts = list((await session.execute(select(RolloutHistory).order_by(RolloutHistory.created_at.desc()))).scalars().all())
    experiments = list((await session.execute(select(Experiment).order_by(Experiment.updated_at.desc()))).scalars().all())
    events = list((await session.execute(select(ExperimentEvent).order_by(ExperimentEvent.occurred_at.asc()))).scalars().all())
    tasks = list((await session.execute(select(AgentTask).order_by(AgentTask.created_at.desc()))).scalars().all())
    approvals = list((await session.execute(select(Approval).order_by(Approval.created_at.desc()))).scalars().all())
    audits = list((await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(300))).scalars().all())
    integrations = list((await session.execute(select(IntegrationConnection))).scalars().all())

    # Apply resource filters consistently
    if flt.repository_id is not None:
        repos = [r for r in repos if r.id == flt.repository_id]
        prs = [p for p in prs if p.repository_id == flt.repository_id]
        changes = [c for c in changes if c.repository_id == flt.repository_id]
        flags = [f for f in flags if f.repository_id == flt.repository_id]

    if flt.flag_key is not None:
        flags = [f for f in flags if f.key == flt.flag_key]
        changes = [c for c in changes if c.flag_key == flt.flag_key]

    flag_ids = {f.id for f in flags}
    if flt.flag_key is not None or flt.repository_id is not None:
        rollouts = [r for r in rollouts if r.feature_flag_id in flag_ids]
        experiments = [e for e in experiments if e.feature_flag_id in flag_ids]

    if flt.experiment_id is not None:
        experiments = [e for e in experiments if e.id == flt.experiment_id]

    if flt.environment is not None:
        rollouts = [r for r in rollouts if r.environment_name.lower() == flt.environment]
        experiments = [e for e in experiments if e.environment_name.lower() == flt.environment]
        approvals = [
            a for a in approvals
            if a.environment_name and a.environment_name.lower() == flt.environment
        ]

    # Filter events by flag/env/source
    flag_key_set = {f.key for f in flags}
    filtered_events: list[ExperimentEvent] = []
    for ev in events:
        if (flt.flag_key is not None or flt.repository_id is not None) and ev.flag_key not in flag_key_set:
            continue
        if flt.environment is not None and ev.environment_name.lower() != flt.environment:
            continue
        if flt.experiment_id is not None and ev.experiment_id != flt.experiment_id:
            continue
        if flt.event_source == "simulator_generated" and not is_simulator_event(ev):
            continue
        if flt.event_source == "application_telemetry" and is_simulator_event(ev):
            continue
        filtered_events.append(ev)

    return {
        "repos": repos,
        "prs": prs,
        "changes": changes,
        "flags": flags,
        "rollouts": rollouts,
        "experiments": experiments,
        "events": filtered_events,
        "all_events": events,
        "tasks": tasks,
        "approvals": approvals,
        "audits": audits,
        "integrations": integrations,
    }


def _env_state_summary(flag: FeatureFlag, env_filter: str | None) -> tuple[bool, bool, bool]:
    """Return (is_enabled_100, is_disabled_0, is_gradual_partial) for a flag."""
    states = flag.env_states or {}
    target_states = (
        [states.get(env_filter)]
        if env_filter and isinstance(states.get(env_filter), dict)
        else [v for v in states.values() if isinstance(v, dict)]
    )
    valid = [s for s in target_states if s]
    if not valid:
        return False, True, False
    pcts = [int(s.get("rollout_percentage") or (100 if s.get("enabled") else 0)) for s in valid]
    is_gradual = any(0 < p < 100 for p in pcts)
    is_enabled = not is_gradual and any(p == 100 or bool(s.get("enabled")) for p, s in zip(pcts, valid))
    is_disabled = not is_gradual and not is_enabled
    return is_enabled, is_disabled, is_gradual


def compute_dashboard_metrics(data: dict[str, Any], flt: FilterWindowMeta) -> DashboardMetricsOut:
    """Compute exact, non-fabricated top-level metrics for all 6 operational domains."""
    start, end = flt.start_at, flt.end_at
    prev_delta = end - start
    prev_start, prev_end = start - prev_delta, start

    repos: list[Repository] = data["repos"]
    prs: list[PullRequest] = data["prs"]
    changes: list[CodeChange] = data["changes"]
    flags: list[FeatureFlag] = data["flags"]
    rollouts: list[RolloutHistory] = data["rollouts"]
    experiments: list[Experiment] = data["experiments"]
    tasks: list[AgentTask] = data["tasks"]
    approvals: list[Approval] = data["approvals"]

    # Historical existence flags so we never fabricate trends
    prior_prs = any(_in_window(p.created_at, prev_start, prev_end) for p in prs)
    prior_rollouts = any(_in_window(r.created_at, prev_start, prev_end) for r in rollouts)

    # 1. Engineering activity
    win_prs = [p for p in prs if _in_window(p.created_at, start, end)]
    prev_win_prs = [p for p in prs if _in_window(p.created_at, prev_start, prev_end)]
    analyzed_prs = sum(1 for p in prs if p.analysis_status == AnalysisStatus.COMPLETED)
    pending_pr_analyses = sum(
        1 for p in prs if p.analysis_status in (AnalysisStatus.PENDING, AnalysisStatus.ANALYZING)
    )
    draft_prs_created = sum(1 for c in changes if c.draft_pr_number is not None)
    failed_pr_tasks = sum(1 for p in prs if p.analysis_status == AnalysisStatus.FAILED) + sum(
        1 for t in tasks if t.specialist == SpecialistRole.PR_AGENT and t.status == AgentTaskStatus.FAILED
    )
    eng_trends: dict[str, MetricTrend] = {}
    t_prs = _compute_trend(len(win_prs), len(prev_win_prs), prior_prs)
    if t_prs:
        eng_trends["synchronized_prs"] = t_prs

    # 2. Release operations (RolloutHistory + pending approvals)
    win_rollouts = [r for r in rollouts if _in_window(r.created_at, start, end)]
    prev_win_rollouts = [r for r in rollouts if _in_window(r.created_at, prev_start, prev_end)]
    in_progress = 0
    for f in flags:
        _, _, is_gradual = _env_state_summary(f, flt.environment)
        if is_gradual:
            in_progress += 1

    successful_releases = sum(
        1 for r in win_rollouts if r.verified and r.action != RolloutAction.ROLLBACK
    )
    failed_releases = sum(1 for r in win_rollouts if not r.verified) + sum(
        1
        for a in approvals
        if _in_window(a.created_at, start, end)
        and a.status == ApprovalStatus.FAILED
        and a.target_resource_type == "feature_flag"
    )
    rollbacks_exec = sum(1 for r in win_rollouts if r.action == RolloutAction.ROLLBACK)
    rel_awaiting = sum(
        1
        for a in approvals
        if a.status == ApprovalStatus.PENDING
        and a.target_resource_type in ("feature_flag", "experiment")
    )
    denom = successful_releases + failed_releases
    rel_success_rate = round((successful_releases / denom) * 100.0, 1) if denom > 0 else None
    rel_trends: dict[str, MetricTrend] = {}
    t_rel = _compute_trend(len(win_rollouts), len(prev_win_rollouts), prior_rollouts)
    if t_rel:
        rel_trends["total_releases"] = t_rel

    # 3. Feature flags
    enabled_flags = 0
    disabled_flags = 0
    gradual_flags = 0
    unhealthy_flags = 0
    for f in flags:
        en, dis, grad = _env_state_summary(f, flt.environment)
        if grad:
            gradual_flags += 1
        elif en:
            enabled_flags += 1
        elif dis:
            disabled_flags += 1
        if f.health_score < 100 or len(f.health_issues or []) > 0:
            unhealthy_flags += 1

    avg_health = (
        round(sum(f.health_score for f in flags) / len(flags), 1) if flags else None
    )
    pending_flag_changes = sum(
        1
        for a in approvals
        if a.status == ApprovalStatus.PENDING and a.target_resource_type == "feature_flag"
    )

    # 4. Experimentation
    running_exps = sum(1 for e in experiments if e.status == ExperimentStatus.RUNNING)
    completed_exps = sum(1 for e in experiments if e.status == ExperimentStatus.COMPLETED)
    paused_exps = sum(1 for e in experiments if e.status == ExperimentStatus.PAUSED)
    pos_exps = sum(
        1 for e in experiments if e.latest_recommendation == ReleaseRecommendation.EXPAND_ROLLOUT
    )
    breached_exps = 0
    for e in experiments:
        res = e.latest_bayesian_result or {}
        pf_breached = bool(((res.get("guardrails") or {}).get("payment_failed") or {}).get("breached"))
        if pf_breached or e.latest_recommendation in (
            ReleaseRecommendation.RECOMMEND_ROLLBACK,
            ReleaseRecommendation.REDUCE_ROLLOUT,
        ):
            breached_exps += 1

    # 5. Approvals
    pending_app = sum(1 for a in approvals if a.status == ApprovalStatus.PENDING)
    approved_app = sum(
        1
        for a in approvals
        if a.status == ApprovalStatus.EXECUTED and _in_window(a.created_at, start, end)
    )
    rejected_app = sum(
        1
        for a in approvals
        if a.status == ApprovalStatus.REJECTED and _in_window(a.created_at, start, end)
    )
    expired_app = sum(
        1
        for a in approvals
        if a.status == ApprovalStatus.EXPIRED and _in_window(a.created_at, start, end)
    )

    # 6. Agents
    win_tasks = [t for t in tasks if _in_window(t.created_at, start, end)]
    succ_tasks = sum(1 for t in win_tasks if t.status == AgentTaskStatus.COMPLETED)
    fail_tasks = sum(1 for t in win_tasks if t.status == AgentTaskStatus.FAILED)
    run_tasks = sum(
        1 for t in win_tasks if t.status in (AgentTaskStatus.RUNNING, AgentTaskStatus.QUEUED)
    )
    cancel_tasks = sum(1 for t in win_tasks if t.status == AgentTaskStatus.CANCELLED)
    await_tasks = sum(1 for t in win_tasks if t.status == AgentTaskStatus.AWAITING_APPROVAL)

    durations: list[float] = []
    tool_calls = 0
    token_sum = 0
    has_tokens = False
    for t in win_tasks:
        s_dt = ensure_utc(t.started_at)
        c_dt = ensure_utc(t.completed_at)
        if s_dt is not None and c_dt is not None and c_dt >= s_dt:
            durations.append((c_dt - s_dt).total_seconds())
        for step in t.steps_log or []:
            if isinstance(step, dict) and step.get("event_type") == "tool.executed":
                tool_calls += 1
        tu = t.token_usage or {}
        if isinstance(tu, dict) and int(tu.get("total_tokens") or 0) > 0:
            token_sum += int(tu.get("total_tokens") or 0)
            has_tokens = True

    avg_duration = round(sum(durations) / len(durations), 2) if durations else None

    return DashboardMetricsOut(
        filter=flt,
        engineering=EngineeringMetricsBlock(
            connected_repositories=len(repos),
            synchronized_prs=len(win_prs),
            prs_analyzed=analyzed_prs,
            pending_pr_analyses=pending_pr_analyses,
            draft_prs_created=draft_prs_created,
            failed_analysis_tasks=failed_pr_tasks,
            trends=eng_trends,
        ),
        releases=ReleaseOperationsMetricsBlock(
            total_releases=len(win_rollouts),
            releases_in_progress=in_progress,
            successful_releases=successful_releases,
            failed_releases=failed_releases,
            rollbacks_executed=rollbacks_exec,
            releases_awaiting_approval=rel_awaiting,
            release_success_rate=rel_success_rate,
            trends=rel_trends,
        ),
        feature_flags=FeatureFlagMetricsBlock(
            total_flags=len(flags),
            enabled_flags=enabled_flags,
            disabled_flags=disabled_flags,
            gradual_rollout_flags=gradual_flags,
            flags_with_health_issues=unhealthy_flags,
            pending_flag_changes=pending_flag_changes,
            average_health_score=avg_health,
        ),
        experiments=ExperimentationMetricsBlock(
            total_experiments=len(experiments),
            running_experiments=running_exps,
            completed_experiments=completed_exps,
            paused_experiments=paused_exps,
            positive_recommendation_experiments=pos_exps,
            breached_guardrail_experiments=breached_exps,
        ),
        approvals=ApprovalActivityMetricsBlock(
            pending_approvals=pending_app,
            approved_operations=approved_app,
            rejected_operations=rejected_app,
            expired_approvals=expired_app,
        ),
        agents=AgentActivityMetricsBlock(
            total_agent_executions=len(win_tasks),
            successful_executions=succ_tasks,
            failed_executions=fail_tasks,
            running_executions=run_tasks,
            cancelled_executions=cancel_tasks,
            awaiting_approval_executions=await_tasks,
            average_execution_duration_seconds=avg_duration,
            agent_tool_calls=tool_calls,
            total_tokens=token_sum if has_tokens else None,
        ),
        sources={
            "engineering": "live_external",
            "releases": "live_external",
            "feature_flags": "live_external",
            "experiments": "calculated",
            "approvals": "database",
            "agents": "database",
        },
    )


def build_experiment_performance_points(
    data: dict[str, Any],
    flt: FilterWindowMeta,
) -> list[ExperimentPerformancePoint]:
    """Assemble Chart B / Metrics Experiment comparisons strictly from DB & Bayesian posteriors."""
    flags_by_id = {f.id: f for f in data["flags"]}
    all_events: list[ExperimentEvent] = data["events"]
    points: list[ExperimentPerformancePoint] = []

    for exp in data["experiments"][:20]:
        flag = flags_by_id.get(exp.feature_flag_id)
        fk = flag.key if flag else None
        exp_events = [
            ev
            for ev in all_events
            if ev.experiment_id == exp.id
            or (fk and ev.flag_key == fk and ev.environment_name.lower() == exp.environment_name.lower())
        ]
        sim_cnt = sum(1 for ev in exp_events if is_simulator_event(ev))
        tel_cnt = len(exp_events) - sim_cnt

        res = exp.latest_bayesian_result
        if not res and exp_events:
            computed = analyze_experiment_events(
                exp_events,
                experiment_id=str(exp.id),
                analytics_mode=exp.analytics_mode.value,
                control_variation=exp.control_variation,
                treatment_variation=exp.treatment_variation,
                primary_metric=exp.primary_metric,
                variation_map=build_variation_map(exp),
                mc_samples=5000,
                thresholds=exp.guardrail_thresholds or None,
            )
            res = computed.model_dump(mode="json")

        ctrl = (res or {}).get("control") or {}
        trt = (res or {}).get("treatment") or {}
        cmp = (res or {}).get("comparison") or {}
        pf = ((res or {}).get("guardrails") or {}).get("payment_failed") or {}
        adeq = (res or {}).get("sample_adequacy") or {}

        points.append(
            ExperimentPerformancePoint(
                experiment_id=exp.id,
                name=exp.name,
                flag_key=fk,
                environment_name=exp.environment_name,
                status=exp.status.value,
                analytics_mode=exp.analytics_mode.value,
                primary_metric=exp.primary_metric,
                recommendation=exp.latest_recommendation.value if exp.latest_recommendation else None,
                hypothesis=exp.hypothesis,
                traffic_allocation=dict(exp.traffic_allocation or {}),
                control_exposures=int(ctrl.get("exposures") or 0),
                control_conversions=int(ctrl.get("conversions") or 0),
                control_rate=float(ctrl.get("mean_rate") or 0.0),
                control_ci_95=list(ctrl.get("credible_interval_95") or []),
                treatment_exposures=int(trt.get("exposures") or 0),
                treatment_conversions=int(trt.get("conversions") or 0),
                treatment_rate=float(trt.get("mean_rate") or 0.0),
                treatment_ci_95=list(trt.get("credible_interval_95") or []),
                absolute_improvement=float(cmp.get("absolute_lift_mean") or 0.0),
                relative_improvement=float(cmp.get("relative_lift_mean") or 0.0),
                prob_treatment_beats_control=float(cmp.get("prob_treatment_beats_control") or 0.5),
                expected_loss_treatment=float(cmp.get("expected_loss_choose_treatment") or 0.0),
                payment_failed_breached=bool(pf.get("breached", False)),
                payment_failed_severity=str(pf.get("breach_severity") or "none"),
                control_error_rate=float(pf.get("control_rate") or 0.0),
                treatment_error_rate=float(pf.get("treatment_rate") or 0.0),
                sample_adequate=bool(adeq.get("is_adequate", False)),
                started_at=exp.started_at,
                ended_at=exp.ended_at,
                simulator_event_count=sim_cnt,
                telemetry_event_count=tel_cnt,
            )
        )
    return points


def compute_dashboard_charts(data: dict[str, Any], flt: FilterWindowMeta) -> DashboardChartsOut:
    """Compute Charts A–F directly from persisted rows inside the requested time window."""
    buckets, _ = _bucket_spec(flt.start_at, flt.end_at)
    rollouts: list[RolloutHistory] = data["rollouts"]
    flags: list[FeatureFlag] = data["flags"]
    tasks: list[AgentTask] = data["tasks"]
    events: list[ExperimentEvent] = data["events"]
    experiments: list[Experiment] = data["experiments"]

    # Chart A: Release Activity
    rel_points = [
        ReleaseActivityPoint(bucket=iso, label=lbl) for _, _, iso, lbl in buckets
    ]
    for r in rollouts:
        if not _in_window(r.created_at, flt.start_at, flt.end_at):
            continue
        idx = _bucket_index(r.created_at, buckets)
        if idx is None:
            continue
        pt = rel_points[idx]
        pt.releases_created += 1
        if r.action == RolloutAction.ROLLBACK:
            pt.rollbacks += 1
        elif r.verified:
            pt.releases_completed += 1
        else:
            pt.releases_failed += 1

    # Chart B: Experiment conversion performance
    exp_points = build_experiment_performance_points(data, flt)

    # Chart C: Feature flag distribution
    c_disabled = c_enabled = c_gradual = c_issues = 0
    for f in flags:
        en, dis, grad = _env_state_summary(f, flt.environment)
        if grad:
            c_gradual += 1
        elif en:
            c_enabled += 1
        elif dis:
            c_disabled += 1
        if f.health_score < 100 or len(f.health_issues or []) > 0:
            c_issues += 1

    flag_dist = [
        DistributionSlice(key="disabled", name="Disabled", count=c_disabled),
        DistributionSlice(key="enabled", name="Enabled (100%)", count=c_enabled),
        DistributionSlice(key="gradual", name="Partial Rollout", count=c_gradual),
        DistributionSlice(key="health_issues", name="Config / Health Issues", count=c_issues),
    ]

    # Chart D: Agent execution activity
    agent_points = [
        AgentExecutionPoint(bucket=iso, label=lbl) for _, _, iso, lbl in buckets
    ]
    dur_buckets: dict[int, list[float]] = {i: [] for i in range(len(buckets))}
    for t in tasks:
        if not _in_window(t.created_at, flt.start_at, flt.end_at):
            continue
        idx = _bucket_index(t.created_at, buckets)
        if idx is None:
            continue
        pt = agent_points[idx]
        if t.status == AgentTaskStatus.COMPLETED:
            pt.successful += 1
        elif t.status == AgentTaskStatus.FAILED:
            pt.failed += 1
        elif t.status == AgentTaskStatus.AWAITING_APPROVAL:
            pt.awaiting_approval += 1
        elif t.status in (AgentTaskStatus.RUNNING, AgentTaskStatus.QUEUED):
            pt.running += 1
        s_dt = ensure_utc(t.started_at)
        c_dt = ensure_utc(t.completed_at)
        if s_dt is not None and c_dt is not None and c_dt >= s_dt:
            dur_buckets[idx].append((c_dt - s_dt).total_seconds())
    for idx, vals in dur_buckets.items():
        if vals:
            agent_points[idx].avg_duration_seconds = round(sum(vals) / len(vals), 2)

    # Chart E: Event & telemetry activity (distinguishing simulator vs interactive)
    tel_points = [
        TelemetryEventPoint(bucket=iso, label=lbl) for _, _, iso, lbl in buckets
    ]
    sim_total = 0
    org_total = 0
    for ev in events:
        if not _in_window(ev.occurred_at, flt.start_at, flt.end_at):
            continue
        idx = _bucket_index(ev.occurred_at, buckets)
        if idx is None:
            continue
        pt = tel_points[idx]
        pt.total_events += 1
        et = ev.event_type.value
        if et == "checkout_started":
            pt.checkout_started += 1
        elif et == "checkout_completed":
            pt.checkout_completed += 1
        elif et == "payment_failed":
            pt.payment_failed += 1
        elif et == "checkout_abandoned":
            pt.checkout_abandoned += 1
        if is_simulator_event(ev):
            pt.simulator_events += 1
            sim_total += 1
        else:
            pt.telemetry_events += 1
            org_total += 1

    # Chart F: Release health breakdown
    healthy_cnt = attention_cnt = breach_cnt = rollback_cnt = 0
    for e in experiments:
        res = e.latest_bayesian_result or {}
        pf_breached = bool(((res.get("guardrails") or {}).get("payment_failed") or {}).get("breached"))
        rec = e.latest_recommendation
        if rec == ReleaseRecommendation.RECOMMEND_ROLLBACK:
            rollback_cnt += 1
        if pf_breached:
            breach_cnt += 1
        elif rec == ReleaseRecommendation.REDUCE_ROLLOUT:
            attention_cnt += 1
        elif rec in (ReleaseRecommendation.EXPAND_ROLLOUT, ReleaseRecommendation.MAINTAIN_CURRENT_ROLLOUT):
            healthy_cnt += 1
    for r in rollouts:
        if _in_window(r.created_at, flt.start_at, flt.end_at) and not r.verified:
            attention_cnt += 1

    release_health = [
        DistributionSlice(key="healthy", name="Healthy Releases", count=healthy_cnt),
        DistributionSlice(key="attention", name="Requires Attention", count=attention_cnt),
        DistributionSlice(key="breaches", name="Guardrail Breaches", count=breach_cnt),
        DistributionSlice(key="rollback_recs", name="Rollback Recommendations", count=rollback_cnt),
    ]

    return DashboardChartsOut(
        filter=flt,
        release_activity=rel_points,
        experiment_performance=exp_points,
        flag_distribution=flag_dist,
        agent_activity=agent_points,
        telemetry_activity=tel_points,
        release_health=release_health,
        telemetry_totals={
            "total_events": sim_total + org_total,
            "simulator_events": sim_total,
            "application_telemetry_events": org_total,
        },
    )


def compute_recent_activity(data: dict[str, Any], *, limit: int = 25) -> list[ActivityFeedItem]:
    """Produce unified real activity feed from audit logs and verified rollouts."""
    flags_by_id = {f.id: f for f in data["flags"]}
    items: list[ActivityFeedItem] = []

    for a in data["audits"][:limit]:
        href = _map_href_for_resource(a.resource_type, a.resource_id)
        action = a.event_action
        details = a.details or {}
        res_lbl = (
            str(details.get("flag_key") or details.get("repo") or details.get("email") or a.resource_id or "")
        )[:80] or None
        desc_parts = [f"Policy {a.policy_category.value}"]
        if a.environment_name:
            desc_parts.append(f"env={a.environment_name}")
        if details.get("target_percentage") is not None:
            desc_parts.append(f"target={details['target_percentage']}%")
        if details.get("recommendation"):
            desc_parts.append(f"rec={details['recommendation']}")
        items.append(
            ActivityFeedItem(
                id=f"audit:{a.id}",
                event_type=action,
                title=action.replace(".", " ").replace("_", " ").title(),
                description=" • ".join(desc_parts),
                timestamp=ensure_utc(a.created_at) or utcnow(),
                resource_type=a.resource_type,
                resource_id=a.resource_id,
                resource_label=res_lbl,
                environment_name=a.environment_name,
                actor=f"{a.actor_type.value}:{str(a.actor_id or 'system')[:16]}",
                status=a.status.value,
                href=href,
            )
        )

    for r in data["rollouts"][:10]:
        fk = flags_by_id[r.feature_flag_id].key if r.feature_flag_id in flags_by_id else None
        items.append(
            ActivityFeedItem(
                id=f"rollout:{r.id}",
                event_type=f"rollout.{r.action.value}",
                title=f"{'Verified' if r.verified else 'Unverified'} Flag {r.action.value.replace('_', ' ').title()}",
                description=r.reason or f"Rollout {r.action.value} on {fk or str(r.feature_flag_id)[:8]}",
                timestamp=ensure_utc(r.created_at) or utcnow(),
                resource_type="feature_flag",
                resource_id=str(r.feature_flag_id),
                resource_label=fk,
                environment_name=r.environment_name,
                actor=f"user:{str(r.actor_id)[:12]}" if r.actor_id else "system",
                status="success" if r.verified else "failed",
                href="/releases",
            )
        )

    items.sort(key=lambda x: x.timestamp, reverse=True)
    return items[:limit]


def compute_pending_actions(
    data: dict[str, Any],
    *,
    upcoming_schedules: list[dict[str, Any]] | None = None,
) -> list[PendingActionItem]:
    """Assemble prioritized, non-fabricated pending operator actions."""
    actions: list[PendingActionItem] = []

    for app in data["approvals"]:
        if app.status == ApprovalStatus.PENDING:
            sev: Literal["critical", "high", "medium", "info"] = (
                "critical" if app.policy_category.value == "C" else "high"
            )
            actions.append(
                PendingActionItem(
                    id=f"approval:{app.id}",
                    category="pending_approval",
                    severity=sev,
                    title=f"Category {app.policy_category.value} Approval: {app.title}",
                    description=app.rationale or f"Action `{app.action_type}` waiting sign-off.",
                    resource_type=app.target_resource_type,
                    resource_id=app.target_resource_id,
                    environment_name=app.environment_name,
                    created_at=ensure_utc(app.created_at) or utcnow(),
                    action_label="Review Approval",
                    href="/approvals",
                )
            )
        elif app.status == ApprovalStatus.FAILED:
            actions.append(
                PendingActionItem(
                    id=f"failed_approval:{app.id}",
                    category="failed_release_operation",
                    severity="high",
                    title=f"Failed Operation: {app.title}",
                    description="Approved mutation failed verification or execution; inspect diagnostics.",
                    resource_type=app.target_resource_type,
                    resource_id=app.target_resource_id,
                    environment_name=app.environment_name,
                    created_at=ensure_utc(app.updated_at) or utcnow(),
                    action_label="Inspect Failure",
                    href="/approvals",
                )
            )

    for exp in data["experiments"]:
        res = exp.latest_bayesian_result or {}
        pf_breached = bool(((res.get("guardrails") or {}).get("payment_failed") or {}).get("breached"))
        if pf_breached or exp.latest_recommendation == ReleaseRecommendation.RECOMMEND_ROLLBACK:
            actions.append(
                PendingActionItem(
                    id=f"guardrail:{exp.id}",
                    category="guardrail_breach",
                    severity="critical",
                    title=f"Guardrail Breach / Rollback Advised: {exp.name}",
                    description=f"Environment `{exp.environment_name}` recommendation: {exp.latest_recommendation.value if exp.latest_recommendation else 'Breach detected'}.",
                    resource_type="experiment",
                    resource_id=str(exp.id),
                    environment_name=exp.environment_name,
                    created_at=ensure_utc(exp.updated_at) or utcnow(),
                    action_label="Inspect Experiment",
                    href=f"/experiments?id={exp.id}",
                )
            )

    for r in data["rollouts"][:20]:
        if not r.verified:
            actions.append(
                PendingActionItem(
                    id=f"rollout_fail:{r.id}",
                    category="failed_release_operation",
                    severity="high",
                    title=f"Unverified Rollout Step ({r.action.value}) in {r.environment_name}",
                    description=r.reason or "Flagsmith post-mutation verification detected drift.",
                    resource_type="feature_flag",
                    resource_id=str(r.feature_flag_id),
                    environment_name=r.environment_name,
                    created_at=ensure_utc(r.created_at) or utcnow(),
                    action_label="Inspect Release",
                    href="/releases",
                )
            )

    for t in data["tasks"][:20]:
        if t.status == AgentTaskStatus.FAILED:
            actions.append(
                PendingActionItem(
                    id=f"agent_fail:{t.id}",
                    category="failed_agent_task",
                    severity="medium",
                    title=f"Agent Execution Failed ({t.specialist.value})",
                    description=(t.error_message or "Agent task encountered an error.")[:160],
                    resource_type="agent_task",
                    resource_id=str(t.id),
                    created_at=ensure_utc(t.created_at) or utcnow(),
                    action_label="Open AI Workspace",
                    href="/agent",
                )
            )

    for conn in data["integrations"]:
        if conn.status in (IntegrationStatus.ERROR, IntegrationStatus.EXPIRED, IntegrationStatus.DISCONNECTED):
            actions.append(
                PendingActionItem(
                    id=f"integration:{conn.id}",
                    category="integration_attention",
                    severity="high",
                    title=f"Integration Attention: {conn.provider.value.upper()} ({conn.status.value})",
                    description="Reconnect or re-verify external API credentials.",
                    resource_type="integration",
                    resource_id=str(conn.id),
                    created_at=ensure_utc(conn.updated_at) or utcnow(),
                    action_label="Configure Integration",
                    href="/settings",
                )
            )

    for pr in data["prs"]:
        if pr.analysis_status == AnalysisStatus.FAILED:
            actions.append(
                PendingActionItem(
                    id=f"pr_fail:{pr.id}",
                    category="failed_pr_analysis",
                    severity="medium",
                    title=f"PR #{pr.github_pr_number} Analysis Failed",
                    description=pr.title[:160],
                    resource_type="pull_request",
                    resource_id=str(pr.id),
                    created_at=ensure_utc(pr.updated_at) or utcnow(),
                    action_label="Retry PR Analysis",
                    href="/pull-requests",
                )
            )

    for sched in upcoming_schedules or []:
        if sched.get("attention_needed"):
            actions.append(
                PendingActionItem(
                    id=f"schedule:{sched.get('id')}",
                    category="scheduled_job_attention",
                    severity="high",
                    title=f"Scheduled Job Attention: {sched.get('name')}",
                    description=str(sched.get("attention_reason") or "Scheduled execution failed or overdue."),
                    resource_type="scheduled_job",
                    resource_id=str(sched.get("id")),
                    created_at=_parse_iso_dt(str(sched.get("updated_at") or "")) or utcnow(),
                    action_label="Open Schedules",
                    href="/schedules",
                )
            )

    sev_rank = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    actions.sort(key=lambda item: (sev_rank.get(item.severity, 9), -item.created_at.timestamp()))
    return actions[:30]


def get_metric_definitions_docs() -> list[MetricDefinitionDoc]:
    """Return authoritative mathematical definitions & honest limitations per metric."""
    return [
        MetricDefinitionDoc(
            name="Release Success Rate",
            definition="Percentage of completed flag rollout/toggle operations whose post-mutation Flagsmith verification passed.",
            formula="(verified non-rollback RolloutHistory rows) / (verified non-rollback + unverified/failed operations) * 100",
            source="Flagsmith API readback persisted in rollout_history & approvals",
            time_window="Selectable filter window (24h, 7d, 30d, 90d, custom UTC)",
            filters=["range", "environment", "repository_id", "flag_key"],
            limitations="Returns null/Unavailable when zero release operations occurred in the selected period. Flagsmith rollouts are distinct from CD deployments.",
        ),
        MetricDefinitionDoc(
            name="Experiment Conversion Rate",
            definition="Posterior mean conversion rate over unique exposed shoppers completing primary_metric (checkout_completed).",
            formula="(alpha_0 + unique converted users) / (alpha_0 + beta_0 + unique exposed users), Beta(1,1) prior",
            source="Calculated Bayesian Beta-Binomial posterior (50,000 Monte Carlo draws)",
            time_window="Per-experiment collection span",
            filters=["experiment_id", "environment", "event_source"],
            limitations="Events emitted by NovaShop /api/simulate carry session_key prefix 'sim_' and are labeled simulator_generated separately from storefront telemetry.",
        ),
        MetricDefinitionDoc(
            name="Agent Success Rate",
            definition="Share of terminal LangGraph agent runs ending in COMPLETED vs FAILED (cancelled runs excluded from denominator and reported separately).",
            formula="completed_tasks / (completed_tasks + failed_tasks) * 100",
            source="ReleaseGuard agent_tasks database table",
            time_window="Selectable filter window",
            filters=["range"],
            limitations="Tasks paused in awaiting_approval are still open HITL executions, not failures.",
        ),
        MetricDefinitionDoc(
            name="Deployment Frequency / Lead Time / MTTR",
            definition="DORA deployment velocity and incident recovery interval metrics.",
            formula="Requires explicit deployment & incident resolution timestamps.",
            source="Unavailable unless deployment webhooks or resolved incident windows exist",
            time_window="Selectable filter window",
            filters=["range", "repository_id"],
            limitations="Draft PRs and feature flag updates are NOT mislabeled as production deployments; reported honestly as Unavailable when deployment records are absent.",
        ),
        MetricDefinitionDoc(
            name="Flag Rollout Percentage",
            definition="Live environment rollout allocation or segment split percentage synchronized from Flagsmith.",
            formula="100% if base enabled; PERCENTAGE_SPLIT segment value if active override; else 0%",
            source="Live Flagsmith Management & Version v2 API",
            time_window="Current snapshot",
            filters=["environment", "flag_key", "repository_id"],
            limitations="Multi-environment summary reports gradual rollout whenever at least one filtered environment is in (0%, 100%).",
        ),
        MetricDefinitionDoc(
            name="Scheduled Job Success Rate",
            definition="Share of completed scheduled executions over non-skipped terminal executions.",
            formula="completed / (completed + failed) * 100",
            source="ReleaseGuard job_executions database table",
            time_window="Selectable filter window",
            filters=["range", "schedule_type", "environment"],
            limitations="Skipped and cancelled executions are reported as separate counts and excluded from failure ratios.",
        ),
    ]
