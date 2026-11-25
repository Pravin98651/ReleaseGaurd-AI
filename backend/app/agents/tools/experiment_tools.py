"""10 experiment_agent manual tools for capability probe, Bayesian analysis, recommendations, incidents, and proposals."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from app.agents.specialists.experiment_agent import canonicalize_primary_metric
from app.agents.tools.context import ToolExecutionContext
from app.analytics.bayesian import analyze_experiment_events
from app.analytics.incidents import investigate_incident
from app.analytics.recommendations import evaluate_recommendation
from app.core.config import get_settings
from app.core.database import utcnow
from app.models import (
    AnalyticsMode,
    AuditLog,
    Experiment,
    ExperimentEvent,
    ExperimentStatus,
    FeatureFlag,
    FlagType,
    PullRequest,
    ReleaseRecommendation,
    RolloutHistory,
)
from app.schemas.tools import (
    ActivateExperimentInput,
    ActivateExperimentOutput,
    ComputeBayesianExperimentAnalysisInput,
    ComputeBayesianExperimentAnalysisOutput,
    CreateExperimentPlanInput,
    CreateExperimentPlanOutput,
    EvaluateReleaseRecommendationInput,
    EvaluateReleaseRecommendationOutput,
    FetchExperimentEventsSummaryInput,
    FetchExperimentEventsSummaryOutput,
    FlagsmithProbeExperimentCapabilityInput,
    FlagsmithProbeExperimentCapabilityOutput,
    GetRolloutTimelineInput,
    GetRolloutTimelineOutput,
    InvestigateReleaseIncidentInput,
    InvestigateReleaseIncidentOutput,
    ProposeReleaseActionInput,
    ProposeReleaseActionOutput,
    StopOrConcludeExperimentInput,
    StopOrConcludeExperimentOutput,
)
from app.services.policy_engine import PolicyEngine


def build_variation_map(exp: Experiment) -> dict[str, str]:
    """Build variation alias map bridging demo-app variations (v1/v2/true/false) to experiment arms."""
    ctrl = exp.control_variation
    trt = exp.treatment_variation
    vmap: dict[str, str] = {ctrl: ctrl, trt: trt}
    for alias in ("control", "v1", "false", "baseline", "off"):
        if alias != trt and alias not in vmap:
            vmap[alias] = ctrl
    for alias in ("treatment", "v2", "true", "variant", "on"):
        if alias != ctrl and alias not in vmap:
            vmap[alias] = trt
    return vmap


async def load_experiment_events_for_experiment(
    ctx: ToolExecutionContext,
    exp: Experiment,
    *,
    window_minutes: int | None = None,
) -> list[ExperimentEvent]:
    """Load events attributed directly to exp.id OR matching (flag_key, environment_name)."""
    flag = await ctx.session.get(FeatureFlag, exp.feature_flag_id)
    cond = ExperimentEvent.experiment_id == exp.id
    if flag is not None:
        cond = or_(
            ExperimentEvent.experiment_id == exp.id,
            (
                (ExperimentEvent.flag_key == flag.key)
                & (ExperimentEvent.environment_name == exp.environment_name)
            ),
        )
    stmt = select(ExperimentEvent).where(cond)
    if window_minutes is not None:
        cutoff = utcnow() - timedelta(minutes=window_minutes)
        stmt = stmt.where(ExperimentEvent.occurred_at >= cutoff)
    stmt = stmt.order_by(ExperimentEvent.occurred_at.asc())
    return list((await ctx.session.execute(stmt)).scalars().all())


async def handle_flagsmith_probe_experiment_capability(
    ctx: ToolExecutionContext,
    args: FlagsmithProbeExperimentCapabilityInput,
) -> dict[str, Any]:
    client = await ctx.get_flagsmith_client()
    env_key = args.environment_key
    if not env_key:
        env_key = await ctx.resolve_env_key("development")

    probe = await client.probe_experiment_capability(env_key)
    mode = AnalyticsMode(probe["analytics_mode"])

    ws = await PolicyEngine.get_workspace_settings(ctx.session)
    ws.flagsmith_analytics_mode = mode
    ws.flagsmith_capability_probed_at = utcnow()
    await ctx.session.commit()

    return FlagsmithProbeExperimentCapabilityOutput(
        analytics_mode=mode,
        native_supported=bool(probe.get("native_supported", False)),
        details=dict(probe.get("details") or {}),
    ).model_dump(mode="json")


async def handle_create_experiment_plan(
    ctx: ToolExecutionContext,
    args: CreateExperimentPlanInput,
) -> dict[str, Any]:
    pid = await ctx.resolve_flagsmith_project_id()
    flag = await ctx.find_feature_flag(flag_key=args.flag_key, project_id=pid)
    if flag is None and pid is not None:
        try:
            flag = await ctx.sync_flag_from_flagsmith(args.flag_key, project_id=pid)
        except Exception:  # noqa: BLE001
            flag = None
    if flag is None:
        raise ValueError(
            f"Feature flag '{args.flag_key}' not found in DB or Flagsmith. Create or sync the flag first."
        )

    ws = await PolicyEngine.get_workspace_settings(ctx.session)
    env_norm = args.environment_name.strip().lower()
    env_key: str | None = None
    env_meta = (flag.env_states or {}).get(env_norm)
    if isinstance(env_meta, dict) and env_meta.get("env_key"):
        env_key = str(env_meta["env_key"])

    thresholds = (
        args.guardrail_thresholds.model_dump(mode="json")
        if args.guardrail_thresholds is not None
        else dict(ws.default_guardrails or {})
    )

    exp = Experiment(
        feature_flag_id=flag.id,
        name=args.name,
        hypothesis=args.hypothesis,
        environment_name=env_norm,
        environment_key=env_key,
        analytics_mode=ws.flagsmith_analytics_mode,
        status=ExperimentStatus.DRAFT,
        control_variation=args.control_variation,
        treatment_variation=args.treatment_variation,
        traffic_allocation=dict(args.traffic_allocation),
        primary_metric=canonicalize_primary_metric(args.primary_metric),
        guardrail_metrics=list(args.guardrail_metrics),
        guardrail_thresholds=thresholds,
    )
    ctx.session.add(exp)
    await ctx.session.commit()
    await ctx.session.refresh(exp)

    return CreateExperimentPlanOutput(
        experiment_id=exp.id,
        status=exp.status.value,
        analytics_mode=exp.analytics_mode,
    ).model_dump(mode="json")


async def handle_activate_experiment(
    ctx: ToolExecutionContext,
    args: ActivateExperimentInput,
) -> dict[str, Any]:
    exp = await ctx.session.get(Experiment, args.experiment_id)
    if exp is None:
        raise ValueError(f"Experiment {args.experiment_id} not found.")
    flag = await ctx.session.get(FeatureFlag, exp.feature_flag_id)
    if flag is None:
        raise ValueError(f"FeatureFlag {exp.feature_flag_id} for experiment not found.")

    # Apply configured split in Flagsmith when connected or linked to a Flagsmith feature
    has_flagsmith_conn = (
        ctx.flagsmith_client is not None or (await ctx.get_flagsmith_connection()) is not None
    )
    if flag.flagsmith_feature_id is not None or has_flagsmith_conn:
        try:
            client = await ctx.get_flagsmith_client()
            env_key = exp.environment_key or await ctx.resolve_env_key(
                exp.environment_name, flag=flag, project_id=flag.flagsmith_project_id
            )
            exp.environment_key = env_key
            if flag.flagsmith_feature_id is not None:
                alloc = dict(exp.traffic_allocation or {"control": 50, "treatment": 50})
                if flag.flag_type == FlagType.MULTIVARIATE:
                    await client.configure_multivariate_split(
                        project_id=flag.flagsmith_project_id,
                        env_key=env_key,
                        feature_id=int(flag.flagsmith_feature_id),
                        flag_key=flag.key,
                        allocations=alloc,
                        verify_edge=ctx.verify_edge_on_mutation,
                    )
                else:
                    trt_pct = int(
                        alloc.get(exp.treatment_variation, alloc.get("treatment", 50))
                    )
                    await client.set_gradual_rollout_stage(
                        project_id=flag.flagsmith_project_id,
                        env_key=env_key,
                        feature_id=int(flag.flagsmith_feature_id),
                        flag_key=flag.key,
                        target_percentage=trt_pct,
                        strategy="percentage_split_segment",
                        verify_edge=ctx.verify_edge_on_mutation,
                    )
                await ctx.sync_flag_from_flagsmith(flag.key, project_id=flag.flagsmith_project_id)
        except ValueError:
            # Allow DB-only activation strictly in offline scenarios with no linked Flagsmith feature
            if ctx.flagsmith_client is not None or flag.flagsmith_feature_id is not None:
                raise

    exp.status = ExperimentStatus.RUNNING
    exp.started_at = exp.started_at or utcnow()
    await ctx.session.commit()
    await ctx.session.refresh(exp)

    return ActivateExperimentOutput(
        experiment_id=exp.id,
        status=exp.status.value,
    ).model_dump(mode="json")


async def handle_stop_or_conclude_experiment(
    ctx: ToolExecutionContext,
    args: StopOrConcludeExperimentInput,
) -> dict[str, Any]:
    exp = await ctx.session.get(Experiment, args.experiment_id)
    if exp is None:
        raise ValueError(f"Experiment {args.experiment_id} not found.")
    flag = await ctx.session.get(FeatureFlag, exp.feature_flag_id)

    if args.outcome == "rolled_back" and flag is not None and flag.flagsmith_feature_id is not None:
        client = await ctx.get_flagsmith_client()
        env_key = exp.environment_key or await ctx.resolve_env_key(
            exp.environment_name, flag=flag, project_id=flag.flagsmith_project_id
        )
        await client.rollback_flag(
            project_id=flag.flagsmith_project_id,
            env_key=env_key,
            feature_id=int(flag.flagsmith_feature_id),
            flag_key=flag.key,
            verify_edge=ctx.verify_edge_on_mutation,
        )
        await ctx.sync_flag_from_flagsmith(flag.key, project_id=flag.flagsmith_project_id)

    exp.status = ExperimentStatus(args.outcome)
    exp.ended_at = utcnow()
    await ctx.session.commit()
    await ctx.session.refresh(exp)

    return StopOrConcludeExperimentOutput(
        experiment_id=exp.id,
        status=exp.status.value,
    ).model_dump(mode="json")


async def handle_fetch_experiment_events_summary(
    ctx: ToolExecutionContext,
    args: FetchExperimentEventsSummaryInput,
) -> dict[str, Any]:
    cutoff = utcnow() - timedelta(minutes=args.window_minutes)
    stmt = select(ExperimentEvent).where(ExperimentEvent.occurred_at >= cutoff)
    if args.experiment_id is not None:
        exp = await ctx.session.get(Experiment, args.experiment_id)
        if exp is not None:
            flag = await ctx.session.get(FeatureFlag, exp.feature_flag_id)
            if flag is not None:
                stmt = stmt.where(
                    or_(
                        ExperimentEvent.experiment_id == exp.id,
                        (
                            (ExperimentEvent.flag_key == flag.key)
                            & (ExperimentEvent.environment_name == exp.environment_name)
                        ),
                    )
                )
            else:
                stmt = stmt.where(ExperimentEvent.experiment_id == args.experiment_id)
        else:
            stmt = stmt.where(ExperimentEvent.experiment_id == args.experiment_id)
    if args.flag_key:
        stmt = stmt.where(ExperimentEvent.flag_key == args.flag_key)
    if args.environment_name:
        stmt = stmt.where(
            ExperimentEvent.environment_name == args.environment_name.strip().lower()
        )

    rows = (await ctx.session.execute(stmt)).scalars().all()
    counts_by_variation: dict[str, dict[str, int]] = {}
    for ev in rows:
        var_bucket = counts_by_variation.setdefault(ev.variation_key, {})
        et = ev.event_type.value
        var_bucket[et] = var_bucket.get(et, 0) + 1

    return FetchExperimentEventsSummaryOutput(
        counts_by_variation=counts_by_variation,
        total_events=len(rows),
    ).model_dump(mode="json")


async def handle_compute_bayesian_experiment_analysis(
    ctx: ToolExecutionContext,
    args: ComputeBayesianExperimentAnalysisInput,
) -> dict[str, Any]:
    exp = await ctx.session.get(Experiment, args.experiment_id)
    if exp is None:
        raise ValueError(f"Experiment {args.experiment_id} not found.")

    ws = await PolicyEngine.get_workspace_settings(ctx.session)
    cfg = get_settings()
    merged_thresholds = dict(ws.default_guardrails or {})
    merged_thresholds.update(dict(exp.guardrail_thresholds or {}))
    if "min_exposures" not in merged_thresholds:
        merged_thresholds["min_exposures"] = cfg.BAYESIAN_MIN_EXPOSURES

    events = await load_experiment_events_for_experiment(ctx, exp)
    result = analyze_experiment_events(
        events,
        experiment_id=str(exp.id),
        analytics_mode=exp.analytics_mode.value,
        control_variation=exp.control_variation,
        treatment_variation=exp.treatment_variation,
        primary_metric=exp.primary_metric,
        variation_map=build_variation_map(exp),
        mc_samples=args.mc_samples or cfg.BAYESIAN_MC_SAMPLES,
        seed=cfg.BAYESIAN_MC_SEED,
        thresholds=merged_thresholds,
    )
    analysis_dict = result.model_dump(mode="json")
    exp.latest_bayesian_result = analysis_dict
    await ctx.session.commit()
    await ctx.session.refresh(exp)

    return ComputeBayesianExperimentAnalysisOutput(
        experiment_id=exp.id,
        analysis=analysis_dict,
    ).model_dump(mode="json")


async def handle_evaluate_release_recommendation(
    ctx: ToolExecutionContext,
    args: EvaluateReleaseRecommendationInput,
) -> dict[str, Any]:
    exp = await ctx.session.get(Experiment, args.experiment_id)
    if exp is None:
        raise ValueError(f"Experiment {args.experiment_id} not found.")
    flag = await ctx.session.get(FeatureFlag, exp.feature_flag_id)

    ws = await PolicyEngine.get_workspace_settings(ctx.session)
    cfg = get_settings()
    merged_thresholds = dict(ws.default_guardrails or {})
    merged_thresholds.update(dict(exp.guardrail_thresholds or {}))

    events = await load_experiment_events_for_experiment(ctx, exp)
    analysis = analyze_experiment_events(
        events,
        experiment_id=str(exp.id),
        analytics_mode=exp.analytics_mode.value,
        control_variation=exp.control_variation,
        treatment_variation=exp.treatment_variation,
        primary_metric=exp.primary_metric,
        variation_map=build_variation_map(exp),
        mc_samples=cfg.BAYESIAN_MC_SAMPLES,
        seed=cfg.BAYESIAN_MC_SEED,
        thresholds=merged_thresholds,
    )
    current_pct: int | None = None
    if flag is not None and isinstance(flag.env_states, dict):
        env_st = flag.env_states.get(exp.environment_name)
        if isinstance(env_st, dict) and env_st.get("rollout_percentage") is not None:
            current_pct = int(env_st["rollout_percentage"])

    rec = evaluate_recommendation(
        analysis,
        thresholds=merged_thresholds,
        environment_name=exp.environment_name,
        current_rollout_percentage=current_pct,
    )
    exp.latest_bayesian_result = analysis.model_dump(mode="json")
    exp.latest_recommendation = ReleaseRecommendation(rec.recommendation.value)
    exp.recommendation_rationale = rec.rationale
    await ctx.session.commit()

    return EvaluateReleaseRecommendationOutput(
        experiment_id=exp.id,
        recommendation=rec.recommendation.value,
        triggers=[t.description for t in rec.triggers if t.met]
        or [t.description for t in rec.triggers],
    ).model_dump(mode="json")


async def handle_investigate_release_incident(
    ctx: ToolExecutionContext,
    args: InvestigateReleaseIncidentInput,
) -> dict[str, Any]:
    env_norm = args.environment_name.strip().lower()
    cutoff = utcnow() - timedelta(minutes=args.window_minutes)

    ev_stmt = select(ExperimentEvent).where(
        ExperimentEvent.environment_name == env_norm,
        ExperimentEvent.occurred_at >= cutoff,
    )
    if args.flag_key:
        ev_stmt = ev_stmt.where(ExperimentEvent.flag_key == args.flag_key)
    events = list((await ctx.session.execute(ev_stmt)).scalars().all())

    rh_stmt = select(RolloutHistory).where(RolloutHistory.environment_name == env_norm)
    rollouts = list((await ctx.session.execute(rh_stmt)).scalars().all())

    pr_stmt = select(PullRequest)
    prs = list((await ctx.session.execute(pr_stmt)).scalars().all())

    flags = list((await ctx.session.execute(select(FeatureFlag))).scalars().all())
    flag_id_to_key = {str(f.id): f.key for f in flags}

    inv = investigate_incident(
        events=events,
        rollout_history=rollouts,
        pull_requests=prs,
        flag_key=args.flag_key,
        flag_id_to_key=flag_id_to_key,
        environment_name=env_norm,
        window_minutes=args.window_minutes,
    )
    d = inv.model_dump(mode="json")
    return InvestigateReleaseIncidentOutput(
        correlation_not_causation=True,
        observed_metrics=d["observed_metrics"],
        temporal_correlations=d["temporal_correlations"],
        confounding_checks=d["confounding_checks"],
    ).model_dump(mode="json")


async def handle_get_rollout_timeline(
    ctx: ToolExecutionContext,
    args: GetRolloutTimelineInput,
) -> dict[str, Any]:
    rh_stmt = select(RolloutHistory).order_by(RolloutHistory.created_at.desc())
    flag_id = None
    if args.flag_key:
        flag = await ctx.find_feature_flag(flag_key=args.flag_key)
        if flag is not None:
            flag_id = flag.id
            rh_stmt = rh_stmt.where(RolloutHistory.feature_flag_id == flag.id)
    if args.environment_name:
        rh_stmt = rh_stmt.where(
            RolloutHistory.environment_name == args.environment_name.strip().lower()
        )
    rh_stmt = rh_stmt.limit(args.limit)
    rollout_rows = (await ctx.session.execute(rh_stmt)).scalars().all()

    audit_stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if args.environment_name:
        audit_stmt = audit_stmt.where(
            AuditLog.environment_name == args.environment_name.strip().lower()
        )
    if flag_id is not None:
        audit_stmt = audit_stmt.where(AuditLog.resource_id == str(flag_id))
    audit_stmt = audit_stmt.limit(args.limit)
    audit_rows = (await ctx.session.execute(audit_stmt)).scalars().all()

    return GetRolloutTimelineOutput(
        rollout_entries=[
            {
                "id": str(r.id),
                "feature_flag_id": str(r.feature_flag_id),
                "environment_name": r.environment_name,
                "action": r.action.value,
                "previous_state": r.previous_state,
                "new_state": r.new_state,
                "verified": r.verified,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in rollout_rows
        ],
        audit_entries=[
            {
                "id": str(a.id),
                "actor_type": a.actor_type.value,
                "policy_category": a.policy_category.value,
                "event_action": a.event_action,
                "status": a.status.value,
                "environment_name": a.environment_name,
                "created_at": a.created_at.isoformat(),
            }
            for a in audit_rows
        ],
    ).model_dump(mode="json")


async def handle_propose_release_action(
    ctx: ToolExecutionContext,
    args: ProposeReleaseActionInput,
) -> dict[str, Any]:
    flag = await ctx.find_feature_flag(flag_key=args.flag_key)
    if flag is None:
        pid = await ctx.resolve_flagsmith_project_id()
        if pid is not None:
            flag = await ctx.sync_flag_from_flagsmith(args.flag_key, project_id=pid)
    if flag is None:
        raise ValueError(f"Feature flag '{args.flag_key}' not found.")

    env_norm = args.environment_name.strip().lower()
    env_states = dict(flag.env_states or {})
    current_env_state = dict(env_states.get(env_norm) or {})

    # Refresh live precondition snapshot from Flagsmith when connected
    expected_precondition: dict[str, Any] = {
        "flag_key": flag.key,
        "environment_name": env_norm,
        "enabled": current_env_state.get("enabled", False),
        "rollout_percentage": current_env_state.get("rollout_percentage", 0),
        "version_uuid": current_env_state.get("version_uuid"),
    }
    try:
        client = await ctx.get_flagsmith_client()
        if flag.flagsmith_feature_id is not None:
            env_key = await ctx.resolve_env_key(
                env_norm, flag=flag, project_id=flag.flagsmith_project_id
            )
            live = await client.verify_live_state(
                env_key=env_key,
                feature_id=int(flag.flagsmith_feature_id),
                flag_key=flag.key,
                project_id=flag.flagsmith_project_id,
            )
            actual = live.get("actual_state") or {}
            if actual:
                expected_precondition["enabled"] = actual.get(
                    "enabled", expected_precondition["enabled"]
                )
                if actual.get("rollout_percentage") is not None:
                    expected_precondition["rollout_percentage"] = actual["rollout_percentage"]
    except ValueError:
        pass

    if args.proposed_action == "rollback":
        action_type = "flagsmith.rollback_flag"
        payload: dict[str, Any] = {
            "flag_key": flag.key,
            "environment_name": env_norm,
            "reason": args.rationale,
        }
        title = f"Rollback '{flag.key}' in {env_norm} to 0%"
    else:
        target_pct = args.target_percentage
        if target_pct is None:
            curr = int(expected_precondition.get("rollout_percentage") or 0)
            ladder = [0, 10, 25, 50, 75, 100]
            if args.proposed_action == "expand":
                next_stages = [s for s in ladder if s > curr]
                target_pct = next_stages[0] if next_stages else 100
            else:
                prev_stages = [s for s in ladder if s < curr]
                target_pct = prev_stages[-1] if prev_stages else 0
        action_type = "flagsmith.set_gradual_rollout_stage"
        payload = {
            "flag_key": flag.key,
            "environment_name": env_norm,
            "target_percentage": int(target_pct),
            "strategy": "multivariate"
            if flag.flag_type == FlagType.MULTIVARIATE
            else "percentage_split_segment",
            "reason": args.rationale,
        }
        title = f"{args.proposed_action.capitalize()} '{flag.key}' in {env_norm} to {target_pct}%"

    approval = await PolicyEngine.create_approval(
        ctx.session,
        action_type=action_type,
        target_resource_type="feature_flag",
        target_resource_id=str(flag.id),
        environment_name=env_norm,
        title=title,
        rationale=args.rationale,
        action_payload=payload,
        expected_precondition=expected_precondition,
        env_states=env_states,
        agent_task_id=ctx.agent_task_id,
        requested_by_id=ctx.user_id,
    )
    await ctx.session.commit()
    await ctx.session.refresh(approval)

    return ProposeReleaseActionOutput(
        approval_id=approval.id,
        policy_category=approval.policy_category.value,
        action_hash=approval.action_hash,
        status=approval.status.value,
    ).model_dump(mode="json")


EXPERIMENT_TOOL_HANDLERS: dict[str, Any] = {
    "flagsmith_probe_experiment_capability": handle_flagsmith_probe_experiment_capability,
    "create_experiment_plan": handle_create_experiment_plan,
    "activate_experiment": handle_activate_experiment,
    "stop_or_conclude_experiment": handle_stop_or_conclude_experiment,
    "fetch_experiment_events_summary": handle_fetch_experiment_events_summary,
    "compute_bayesian_experiment_analysis": handle_compute_bayesian_experiment_analysis,
    "evaluate_release_recommendation": handle_evaluate_release_recommendation,
    "investigate_release_incident": handle_investigate_release_incident,
    "get_rollout_timeline": handle_get_rollout_timeline,
    "propose_release_action": handle_propose_release_action,
}


def get_experiment_tool_names() -> list[str]:
    """Return ordered list of all 10 experiment_agent tool names."""
    return list(EXPERIMENT_TOOL_HANDLERS.keys())
