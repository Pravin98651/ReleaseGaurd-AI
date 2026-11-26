"""Experiments planning, activation, Bayesian Beta-Binomial analysis, and recommendation endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.agents.approval_executor import capture_expected_precondition
from app.agents.specialists.experiment_agent import (
    build_experiment_plan_draft,
    generate_recommendation_narrative,
)
from app.agents.tools.experiment_tools import (
    build_variation_map,
    handle_activate_experiment,
    handle_compute_bayesian_experiment_analysis,
    handle_create_experiment_plan,
    handle_stop_or_conclude_experiment,
    load_experiment_events_for_experiment,
)
from app.analytics.bayesian import ExperimentOutcome, analyze_experiment_events
from app.analytics.recommendations import (
    evaluate_experiment_readiness,
    evaluate_recommendation,
    map_flagsmith_native_results,
)
from app.api.deps import (
    AuthenticatedOperator,
    DBSession,
    DefaultOperator,
    build_gemini_client_from_app,
    build_tool_context,
)
from app.api.v1.errors import APIException
from app.core.config import get_settings
from app.models import (
    ActorType,
    AuditStatus,
    Experiment,
    FeatureFlag,
    PolicyCategory,
    ReleaseRecommendation,
)
from app.schemas import (
    ApprovalOut,
    BayesianAnalysisResponse,
    ExperimentActivateRequest,
    ExperimentOut,
    ExperimentPlanRequest,
    ExperimentStopRequest,
    GuardrailThresholdsSchema,
    RecommendationResponse,
)
from app.schemas.tools import (
    ActivateExperimentInput,
    ComputeBayesianExperimentAnalysisInput,
    CreateExperimentPlanInput,
    StopOrConcludeExperimentInput,
)
from app.services.policy_engine import PolicyEngine

experiments_router = APIRouter(prefix="/experiments", tags=["experiments"])


def _serialize_experiment(exp: Experiment, flag_key: str | None) -> dict[str, Any]:
    d = ExperimentOut.model_validate(exp).model_dump(mode="json")
    d["flag_key"] = flag_key
    return d


@experiments_router.get("")
async def list_experiments(
    session: DBSession,
    _: AuthenticatedOperator,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """List experiments with analytics mode, Bayesian results, and recommendations."""
    total = int((await session.execute(select(func.count(Experiment.id)))).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                select(Experiment)
                .order_by(Experiment.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    flag_ids = {e.feature_flag_id for e in rows}
    flag_map: dict[uuid.UUID, str] = {}
    if flag_ids:
        flags = (
            await session.execute(select(FeatureFlag).where(FeatureFlag.id.in_(flag_ids)))
        ).scalars().all()
        flag_map = {f.id: f.key for f in flags}

    items = [_serialize_experiment(e, flag_map.get(e.feature_flag_id)) for e in rows]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@experiments_router.post("/plan", status_code=201)
async def plan_experiment(
    body: ExperimentPlanRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> dict[str, Any]:
    """Category A: Convert NL specification into a persisted Experiment plan."""
    flag: FeatureFlag | None = None
    if body.feature_flag_id is not None:
        flag = await session.get(FeatureFlag, body.feature_flag_id)
    else:
        all_flags = (await session.execute(select(FeatureFlag))).scalars().all()
        prompt_lower = body.prompt.lower()
        flag = next((f for f in all_flags if f.key.lower() in prompt_lower), None)
        if flag is None and all_flags:
            flag = all_flags[0]

    if flag is None:
        raise APIException(
            404,
            "FEATURE_FLAG_NOT_FOUND",
            "No synced feature flag found to attach experiment. Create or sync a flag first.",
        )

    env_name = (body.environment_name or "development").strip().lower()
    ws = await PolicyEngine.get_workspace_settings(session)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)

    draft = await build_experiment_plan_draft(
        prompt=body.prompt,
        flag_key=flag.key,
        environment_name=env_name,
        gemini_client=gemini_client,
    )

    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    out = await handle_create_experiment_plan(
        ctx,
        CreateExperimentPlanInput(
            flag_key=flag.key,
            name=draft["name"],
            hypothesis=draft["hypothesis"],
            environment_name=env_name,
            control_variation=draft["control_variation"],
            treatment_variation=draft["treatment_variation"],
            traffic_allocation=draft["traffic_allocation"],
            primary_metric=draft["primary_metric"],
            guardrail_metrics=draft["guardrail_metrics"],
        ),
    )
    exp = await session.get(Experiment, uuid.UUID(str(out["experiment_id"])))
    assert exp is not None
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="experiment.planned",
        resource_type="experiment",
        resource_id=str(exp.id),
        environment_name=env_name,
        status=AuditStatus.SUCCESS,
        details={"flag_key": flag.key, "name": exp.name},
    )
    await session.commit()
    await session.refresh(exp)
    return _serialize_experiment(exp, flag.key)


@experiments_router.post("/{exp_id}/activate")
async def activate_experiment(
    exp_id: uuid.UUID,
    body: ExperimentActivateRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> Any:
    """Activate experiment traffic split in Flagsmith (Category B non-prod / C prod)."""
    exp = await session.get(Experiment, exp_id)
    if exp is None:
        raise APIException(404, "EXPERIMENT_NOT_FOUND", f"Experiment {exp_id} not found.")
    flag = await session.get(FeatureFlag, exp.feature_flag_id)
    if flag is None:
        raise APIException(404, "FEATURE_FLAG_NOT_FOUND", "Underlying feature flag not found.")

    env_norm = body.environment_name.strip().lower()
    exp.environment_name = env_norm
    await session.flush()

    category, requires_approval = await PolicyEngine.evaluate_action(
        session,
        action_type="activate_experiment",
        environment_name=env_norm,
        env_states=dict(flag.env_states or {}),
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        resource_type="experiment",
        resource_id=str(exp.id),
    )
    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    action_payload = {"experiment_id": str(exp.id)}

    if requires_approval:
        res_type, res_id, expected_pre, env_states = await capture_expected_precondition(
            ctx, action_type="activate_experiment", action_payload=action_payload
        )
        approval = await PolicyEngine.create_approval(
            session,
            action_type="activate_experiment",
            target_resource_type=res_type,
            target_resource_id=res_id,
            environment_name=env_norm,
            title=f"Activate experiment '{exp.name}' in {env_norm}",
            rationale=f"Apply traffic split {exp.traffic_allocation} for '{flag.key}'.",
            action_payload=action_payload,
            expected_precondition=expected_pre,
            env_states=env_states,
            requested_by_id=operator.id,
        )
        await session.commit()
        await session.refresh(approval)
        return JSONResponse(
            status_code=202,
            content={
                "execution_status": "approval_required",
                "approval": ApprovalOut.model_validate(approval).model_dump(mode="json"),
            },
        )

    await handle_activate_experiment(ctx, ActivateExperimentInput(experiment_id=exp.id))
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=category,
        event_action="experiment.activated",
        resource_type="experiment",
        resource_id=str(exp.id),
        environment_name=env_norm,
        status=AuditStatus.SUCCESS,
        details={"flag_key": flag.key},
    )
    await session.commit()
    await session.refresh(exp)
    return {
        "execution_status": "executed",
        "data": _serialize_experiment(exp, flag.key),
    }


@experiments_router.post("/{exp_id}/stop")
async def stop_experiment(
    exp_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
    body: ExperimentStopRequest | None = None,
) -> Any:
    """Stop or conclude a running experiment (Category B non-prod / C prod)."""
    exp = await session.get(Experiment, exp_id)
    if exp is None:
        raise APIException(404, "EXPERIMENT_NOT_FOUND", f"Experiment {exp_id} not found.")
    flag = await session.get(FeatureFlag, exp.feature_flag_id)

    req = body or ExperimentStopRequest()
    category, requires_approval = await PolicyEngine.evaluate_action(
        session,
        action_type="stop_or_conclude_experiment",
        environment_name=exp.environment_name,
        env_states=dict(flag.env_states or {}) if flag else None,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        resource_type="experiment",
        resource_id=str(exp.id),
    )
    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    action_payload = {
        "experiment_id": str(exp.id),
        "outcome": req.outcome,
        "reason": req.reason,
    }

    if requires_approval:
        res_type, res_id, expected_pre, env_states = await capture_expected_precondition(
            ctx, action_type="stop_or_conclude_experiment", action_payload=action_payload
        )
        approval = await PolicyEngine.create_approval(
            session,
            action_type="stop_or_conclude_experiment",
            target_resource_type=res_type,
            target_resource_id=res_id,
            environment_name=exp.environment_name,
            title=f"Stop experiment '{exp.name}' ({req.outcome})",
            rationale=req.reason,
            action_payload=action_payload,
            expected_precondition=expected_pre,
            env_states=env_states,
            requested_by_id=operator.id,
        )
        await session.commit()
        await session.refresh(approval)
        return JSONResponse(
            status_code=202,
            content={
                "execution_status": "approval_required",
                "approval": ApprovalOut.model_validate(approval).model_dump(mode="json"),
            },
        )

    await handle_stop_or_conclude_experiment(
        ctx, StopOrConcludeExperimentInput.model_validate(action_payload)
    )
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=category,
        event_action="experiment.stopped",
        resource_type="experiment",
        resource_id=str(exp.id),
        environment_name=exp.environment_name,
        status=AuditStatus.SUCCESS,
        details={"outcome": req.outcome, "reason": req.reason},
    )
    await session.commit()
    await session.refresh(exp)
    return {
        "execution_status": "executed",
        "data": _serialize_experiment(exp, flag.key if flag else None),
    }


@experiments_router.get("/{exp_id}/analysis", response_model=BayesianAnalysisResponse)
async def get_experiment_analysis(
    exp_id: uuid.UUID,
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> BayesianAnalysisResponse:
    """Category A: Compute pure-Python Bayesian Beta-Binomial posterior and guardrails."""
    exp = await session.get(Experiment, exp_id)
    if exp is None:
        raise APIException(404, "EXPERIMENT_NOT_FOUND", f"Experiment {exp_id} not found.")

    ctx = await build_tool_context(request.app, session)
    cfg = get_settings()
    out = await handle_compute_bayesian_experiment_analysis(
        ctx,
        ComputeBayesianExperimentAnalysisInput(
            experiment_id=exp.id, mc_samples=cfg.BAYESIAN_MC_SAMPLES
        ),
    )
    analysis = out["analysis"]
    return BayesianAnalysisResponse.model_validate(analysis)


@experiments_router.get("/{exp_id}/recommendation")
async def get_experiment_recommendation(
    exp_id: uuid.UUID,
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Category A: Compute deterministic 5-state release recommendation + plain-English narrative."""
    exp = await session.get(Experiment, exp_id)
    if exp is None:
        raise APIException(404, "EXPERIMENT_NOT_FOUND", f"Experiment {exp_id} not found.")
    flag = await session.get(FeatureFlag, exp.feature_flag_id)

    ctx = await build_tool_context(request.app, session)
    ws = await PolicyEngine.get_workspace_settings(session)
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
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)
    narrative = await generate_recommendation_narrative(
        rec=rec,
        flag_key=flag.key if flag else "feature_flag",
        environment_name=exp.environment_name,
        gemini_client=gemini_client,
    )

    exp.latest_bayesian_result = analysis.model_dump(mode="json")
    exp.latest_recommendation = ReleaseRecommendation(rec.recommendation.value)
    exp.recommendation_rationale = narrative
    if (
        analysis.guardrails.payment_failed.breached
        or exp.latest_recommendation == ReleaseRecommendation.RECOMMEND_ROLLBACK
    ):
        from app.models import NotificationCategory
        from app.services.notification_service import create_notification

        await create_notification(
            session,
            category=NotificationCategory.GUARDRAIL_BREACH,
            severity="critical",
            title=f"Guardrail Breach / Rollback Advised: {exp.name}",
            message=rec.rationale[:300],
            resource_type="experiment",
            resource_id=str(exp.id),
            environment_name=exp.environment_name,
            action_href=f"/experiments?id={exp.id}",
            metadata={"recommendation": rec.recommendation.value},
            dedupe_minutes=15,
        )
    await session.commit()

    readiness = evaluate_experiment_readiness(
        analysis=analysis,
        experiment_status=exp.status.value,
        analytics_mode=exp.analytics_mode.value,
        flagsmith_experiment_id=exp.flagsmith_experiment_id,
        started_at=exp.started_at,
        latest_event_at=events[-1].occurred_at if events else None,
        native_summary=(exp.latest_bayesian_result or {}).get("flagsmith_native_summary"),
    )
    trigger_strings = [t.description for t in rec.triggers if t.met] or [
        t.description for t in rec.triggers
    ]
    resp = RecommendationResponse(
        experiment_id=exp.id,
        recommendation=ReleaseRecommendation(rec.recommendation.value),
        experiment_outcome=analysis.experiment_outcome.value,
        readiness=readiness.model_dump(mode="json"),
        triggers=trigger_strings,
        confidence_summary=rec.confidence_summary,
        preconditions=rec.preconditions,
        potential_consequences=rec.potential_consequences,
        narrative=narrative,
        guardrail_thresholds=GuardrailThresholdsSchema.model_validate(merged_thresholds),
    ).model_dump(mode="json")
    resp["rationale"] = rec.rationale
    return resp


@experiments_router.post("/{exp_id}/refresh-flagsmith-results")
async def refresh_flagsmith_experiment_results(
    exp_id: uuid.UUID,
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Trigger Flagsmith native experiment results refresh and read back latest results."""
    exp = await session.get(Experiment, exp_id)
    if exp is None:
        raise APIException(404, "EXPERIMENT_NOT_FOUND", f"Experiment {exp_id} not found.")
    if not exp.flagsmith_experiment_id or not exp.environment_key:
        return {
            "experiment_id": str(exp.id),
            "flagsmith_experiment_id": exp.flagsmith_experiment_id,
            "available": False,
            "reason": "Experiment is not linked to a Flagsmith native experiment ID.",
        }
    ctx = await build_tool_context(request.app, session)
    client = await ctx.get_flagsmith_client()
    refresh_resp = await client.refresh_experiment_results(
        exp.environment_key, exp.flagsmith_experiment_id
    )
    results_resp = await client.get_experiment_results(
        exp.environment_key, exp.flagsmith_experiment_id
    )
    native_summary = map_flagsmith_native_results(results_resp)
    return {
        "experiment_id": str(exp.id),
        "flagsmith_experiment_id": exp.flagsmith_experiment_id,
        "refresh": refresh_resp,
        "flagsmith_native_summary": native_summary,
        **results_resp,
    }


@experiments_router.post("/{exp_id}/promote-winner")
async def promote_experiment_winner(
    exp_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
    winning_variation: Annotated[str | None, Query(max_length=64)] = None,
) -> Any:
    """Request explicit human approval to promote the winning experiment variant on Flagsmith."""
    exp = await session.get(Experiment, exp_id)
    if exp is None:
        raise APIException(404, "EXPERIMENT_NOT_FOUND", f"Experiment {exp_id} not found.")
    flag = await session.get(FeatureFlag, exp.feature_flag_id)
    if flag is None:
        raise APIException(404, "FEATURE_FLAG_NOT_FOUND", "Underlying feature flag not found.")

    winner = (winning_variation or "").strip() or (
        exp.control_variation
        if exp.latest_recommendation == ReleaseRecommendation.RECOMMEND_ROLLBACK
        else exp.treatment_variation
    )
    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    ws = await PolicyEngine.get_workspace_settings(session)
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
    readiness = evaluate_experiment_readiness(
        analysis=analysis,
        experiment_status=exp.status.value,
        analytics_mode=exp.analytics_mode.value,
        flagsmith_experiment_id=exp.flagsmith_experiment_id,
        started_at=exp.started_at,
        latest_event_at=events[-1].occurred_at if events else None,
        native_summary=(exp.latest_bayesian_result or {}).get("flagsmith_native_summary"),
    )
    if winner != exp.control_variation:
        if analysis.experiment_outcome == ExperimentOutcome.GUARDRAIL_BREACHED or analysis.guardrails.payment_failed.breached:
            raise APIException(
                409,
                "GUARDRAIL_BREACHED",
                "Cannot promote experimental variant while mandatory guardrails are breached.",
                details={"outcome": analysis.experiment_outcome.value, "rationale": analysis.outcome_rationale},
            )
        if not readiness.is_ready_for_winner_decision:
            raise APIException(
                409,
                "EXPERIMENT_NOT_READY",
                "Experiment has not met readiness requirements for winner promotion.",
                details={"blocking_reasons": readiness.blocking_reasons},
            )
    action_payload = {
        "experiment_id": str(exp.id),
        "winning_variation": winner,
        "flag_key": flag.key,
    }
    res_type, res_id, expected_pre, env_states = await capture_expected_precondition(
        ctx, action_type="promote_experiment_winner", action_payload=action_payload
    )
    approval = await PolicyEngine.create_approval(
        session,
        action_type="promote_experiment_winner",
        target_resource_type=res_type,
        target_resource_id=res_id,
        environment_name=exp.environment_name,
        title=f"Promote winning variant '{winner}' for '{flag.key}' in {exp.environment_name}",
        rationale=(
            f"Human approval required before promoting '{winner}' to 100% on Flagsmith "
            f"flag '{flag.key}' ({exp.name})."
        ),
        action_payload=action_payload,
        expected_precondition=expected_pre,
        env_states=env_states,
        requested_by_id=operator.id,
    )
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.C,
        event_action="experiment.winner_promotion_requested",
        resource_type="experiment",
        resource_id=str(exp.id),
        environment_name=exp.environment_name,
        status=AuditStatus.SUCCESS,
        details={"winning_variation": winner, "flag_key": flag.key, "approval_id": str(approval.id)},
    )
    await session.commit()
    await session.refresh(approval)
    return JSONResponse(
        status_code=202,
        content={
            "execution_status": "approval_required",
            "approval": ApprovalOut.model_validate(approval).model_dump(mode="json"),
        },
    )
