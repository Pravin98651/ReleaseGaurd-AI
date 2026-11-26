"""Feature flags, gradual rollouts, NL commands, and Flag Health Monitor endpoints."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.approval_executor import capture_expected_precondition
from app.agents.specialists.pr_agent import generate_cleanup_patch_payload
from app.agents.tools.flag_tools import (
    handle_flagsmith_create_flag,
    handle_flagsmith_rollback_flag,
    handle_flagsmith_set_gradual_rollout_stage,
    handle_flagsmith_toggle_flag,
    handle_scan_flag_health,
)
from app.agents.tools.pr_tools import (
    handle_github_create_branch_and_draft_pr,
    handle_validate_patch_isolated,
)
from app.api.deps import (
    AuthenticatedOperator,
    DBSession,
    DefaultOperator,
    build_flagsmith_client,
    build_gemini_client_from_app,
    build_supervisor,
    build_tool_context,
)
from app.api.v1.errors import APIException
from app.core.database import utcnow
from app.integrations.flagsmith_client import FlagsmithAPIError, FlagsmithClient
from app.models import (
    ActorType,
    AgentSession,
    AgentSessionStatus,
    AgentTask,
    AgentTaskStatus,
    AnalyticsMode,
    Approval,
    ApprovalStatus,
    AuditStatus,
    CodeChange,
    CodeChangeStatus,
    CodeChangeType,
    Experiment,
    ExperimentStatus,
    FeatureFlag,
    PolicyCategory,
    Repository,
    SpecialistRole,
    User,
    ValidationStatus,
)
from app.models.enums import default_traffic_allocation
from app.schemas import (
    ApprovalOut,
    CleanupPRRequest,
    CodeChangeOut,
    ExperimentOut,
    FeatureFlagCreate,
    FeatureFlagOut,
    FlagDeleteRequest,
    FlagHealthItem,
    FlagHealthScanResponse,
    FlagNLCommandRequest,
    FlagRollbackRequest,
    FlagRolloutRequest,
    FlagToggleRequest,
    PaginatedResponse,
)

logger = logging.getLogger("releaseguard.api.flags")
from app.schemas.tools import (
    FlagsmithCreateFlagInput,
    FlagsmithRollbackFlagInput,
    FlagsmithSetGradualRolloutStageInput,
    FlagsmithToggleFlagInput,
    GitHubCreateBranchAndDraftPRInput,
    ScanFlagHealthInput,
    ValidatePatchIsolatedInput,
)
from app.services.policy_engine import PolicyEngine

flags_router = APIRouter(prefix="/feature-flags", tags=["feature-flags"])
flag_health_router = APIRouter(prefix="/flag-health", tags=["flag-health"])


@flags_router.get("", response_model=PaginatedResponse[FeatureFlagOut])
async def list_feature_flags(
    session: DBSession,
    _: AuthenticatedOperator,
    project_id: Annotated[int | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    health_issue: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[FeatureFlagOut]:
    """List synced Flagsmith feature flags with optional filtering."""
    stmt = select(FeatureFlag).order_by(FeatureFlag.key.asc())
    if project_id is not None:
        stmt = stmt.where(FeatureFlag.flagsmith_project_id == project_id)
    if search:
        stmt = stmt.where(FeatureFlag.key.ilike(f"%{search.strip()}%"))

    rows = list((await session.execute(stmt)).scalars().all())
    if environment:
        env_norm = environment.strip().lower()
        rows = [
            f
            for f in rows
            if isinstance(f.env_states, dict)
            and any(k.strip().lower() == env_norm for k in f.env_states)
        ]
    if health_issue:
        issue_norm = health_issue.strip().lower()
        rows = [
            f
            for f in rows
            if any(
                str(iss.get("issue_type", "")).lower() == issue_norm
                for iss in (f.health_issues or [])
                if isinstance(iss, dict)
            )
        ]

    total = len(rows)
    sliced = rows[offset : offset + limit]
    return PaginatedResponse[FeatureFlagOut](
        items=[FeatureFlagOut.model_validate(f) for f in sliced],
        total=total,
        limit=limit,
        offset=offset,
    )


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _map_flagsmith_experiment_status(raw_status: Any) -> ExperimentStatus:
    norm = str(raw_status or "").strip().lower()
    if norm in {"running", "active", "in_progress"}:
        return ExperimentStatus.RUNNING
    if norm == "paused":
        return ExperimentStatus.PAUSED
    if norm in {"completed", "stopped", "concluded", "finished"}:
        return ExperimentStatus.COMPLETED
    if norm == "rolled_back":
        return ExperimentStatus.ROLLED_BACK
    return ExperimentStatus.DRAFT


def _resolve_experiment_flag(
    raw_exp: dict[str, Any],
    *,
    flag_by_fs_id: dict[int, FeatureFlag],
    flag_by_key: dict[str, FeatureFlag],
) -> FeatureFlag | None:
    feat = raw_exp.get("feature")
    if isinstance(feat, dict):
        if feat.get("id") is not None:
            matched = flag_by_fs_id.get(int(feat["id"]))
            if matched is not None:
                return matched
        if feat.get("name"):
            matched = flag_by_key.get(str(feat["name"]).strip().lower())
            if matched is not None:
                return matched
    elif feat is not None:
        try:
            matched = flag_by_fs_id.get(int(feat))
            if matched is not None:
                return matched
        except (TypeError, ValueError):
            pass

    if raw_exp.get("feature_id") is not None:
        try:
            matched = flag_by_fs_id.get(int(raw_exp["feature_id"]))
            if matched is not None:
                return matched
        except (TypeError, ValueError):
            pass

    exp_name = str(raw_exp.get("name") or "").strip().lower()
    if exp_name and exp_name in flag_by_key:
        return flag_by_key[exp_name]
    return None


def _extract_experiment_variations_and_allocation(
    raw_exp: dict[str, Any],
    flag: FeatureFlag,
    env_norm: str,
) -> tuple[str, str, dict[str, int]]:
    mv_opts = [opt for opt in (flag.mv_options or []) if isinstance(opt, dict)]
    opt_name_by_id = {
        int(opt["id"]): str(opt.get("value") or "")
        for opt in mv_opts
        if opt.get("id") is not None and opt.get("value")
    }

    allocation: dict[str, int] = {}
    rollout_cfg = raw_exp.get("experiment_rollout")
    if isinstance(rollout_cfg, dict):
        for item in rollout_cfg.get("multivariate_feature_state_values") or []:
            if not isinstance(item, dict):
                continue
            opt_id = item.get("multivariate_feature_option")
            opt_val = item.get("value") or (
                opt_name_by_id.get(int(opt_id)) if opt_id is not None else None
            )
            if opt_val:
                allocation[str(opt_val)] = round(float(item.get("percentage_allocation") or 0))

    if not allocation and isinstance(flag.env_states, dict):
        env_state = flag.env_states.get(env_norm)
        if isinstance(env_state, dict):
            for item in env_state.get("mv_options") or []:
                if isinstance(item, dict) and item.get("value"):
                    allocation[str(item["value"])] = round(
                        float(item.get("percentage_allocation") or 0)
                    )

    if not allocation and mv_opts:
        for opt in mv_opts:
            val = str(opt.get("value") or "").strip()
            if val:
                allocation[val] = round(float(opt.get("default_percentage_allocation") or 0))

    if not allocation:
        allocation = default_traffic_allocation()

    keys = list(allocation.keys())
    control = next((k for k in keys if "control" in k.lower()), keys[0] if keys else "control")
    treatment = next((k for k in keys if k != control), "treatment")
    return control, treatment, allocation


def _extract_primary_metric(raw_exp: dict[str, Any], default: str = "checkout_completed") -> str:
    metrics = raw_exp.get("metrics")
    if isinstance(metrics, list) and metrics:
        first = metrics[0]
        if isinstance(first, dict):
            metric_obj = first.get("metric")
            if isinstance(metric_obj, dict) and metric_obj.get("name"):
                return str(metric_obj["name"])[:64]
            for key in ("name", "metric_name", "event_name"):
                if first.get(key):
                    return str(first[key])[:64]
    return default


async def _sync_native_experiments_for_project(
    session: AsyncSession,
    fs: FlagsmithClient,
    project_id: int,
    synced_flags: list[FeatureFlag],
) -> list[Experiment]:
    """Sync Flagsmith native experiments across project environments idempotently."""
    try:
        envs = await fs.list_environments(project_id)
    except (FlagsmithAPIError, Exception) as exc:  # noqa: BLE001
        logger.debug("Skipping Flagsmith environment listing for experiment sync: %s", exc)
        return []

    all_project_flags = list(
        (
            await session.execute(
                select(FeatureFlag).where(FeatureFlag.flagsmith_project_id == project_id)
            )
        )
        .scalars()
        .all()
    )
    flag_by_fs_id: dict[int, FeatureFlag] = {}
    flag_by_key: dict[str, FeatureFlag] = {}
    for flag in [*all_project_flags, *synced_flags]:
        if flag.flagsmith_feature_id is not None:
            flag_by_fs_id[int(flag.flagsmith_feature_id)] = flag
        if flag.key:
            flag_by_key[flag.key.strip().lower()] = flag

    synced_experiments: list[Experiment] = []
    for env in envs:
        env_name = str(env.get("name") or "").strip().lower()
        env_key = str(env.get("api_key") or "").strip()
        if not env_name or not env_key:
            continue
        try:
            raw_experiments = await fs.list_experiments(env_key)
        except (FlagsmithAPIError, Exception) as exc:  # noqa: BLE001
            logger.debug(
                "Skipping native experiments sync for env '%s': %s", env_name, exc
            )
            continue

        for raw_exp in raw_experiments:
            if not isinstance(raw_exp, dict) or raw_exp.get("id") is None:
                continue
            fs_exp_id = str(raw_exp["id"])
            flag = _resolve_experiment_flag(
                raw_exp, flag_by_fs_id=flag_by_fs_id, flag_by_key=flag_by_key
            )
            if flag is None:
                logger.debug(
                    "Skipping Flagsmith experiment #%s (%s): no matching local FeatureFlag.",
                    fs_exp_id,
                    raw_exp.get("name"),
                )
                continue

            exp_name = str(raw_exp.get("name") or flag.key)[:255]
            hypothesis = str(
                raw_exp.get("hypothesis") or f"Flagsmith native experiment for {flag.key}."
            )
            status = _map_flagsmith_experiment_status(raw_exp.get("status"))
            control, treatment, allocation = _extract_experiment_variations_and_allocation(
                raw_exp, flag, env_name
            )
            primary_metric = _extract_primary_metric(raw_exp)
            started_at = _parse_iso_datetime(raw_exp.get("started_at")) or (
                utcnow() if status == ExperimentStatus.RUNNING else None
            )
            ended_at = _parse_iso_datetime(raw_exp.get("ended_at") or raw_exp.get("stopped_at"))

            existing = (
                await session.execute(
                    select(Experiment)
                    .where(Experiment.flagsmith_experiment_id == fs_exp_id)
                    .limit(1)
                )
            ).scalar_one_or_none()

            if existing is None:
                existing = Experiment(
                    feature_flag_id=flag.id,
                    name=exp_name,
                    hypothesis=hypothesis,
                    environment_name=env_name,
                    environment_key=env_key,
                    analytics_mode=AnalyticsMode.FLAGSMITH_NATIVE,
                    flagsmith_experiment_id=fs_exp_id,
                    status=status,
                    control_variation=control,
                    treatment_variation=treatment,
                    traffic_allocation=allocation,
                    primary_metric=primary_metric,
                    started_at=started_at,
                    ended_at=ended_at,
                )
                session.add(existing)
            else:
                existing.feature_flag_id = flag.id
                existing.name = exp_name
                existing.hypothesis = hypothesis
                existing.environment_name = env_name
                existing.environment_key = env_key
                existing.analytics_mode = AnalyticsMode.FLAGSMITH_NATIVE
                existing.status = status
                existing.control_variation = control
                existing.treatment_variation = treatment
                existing.traffic_allocation = allocation
                if primary_metric:
                    existing.primary_metric = primary_metric
                if started_at is not None:
                    existing.started_at = existing.started_at or started_at
                if ended_at is not None:
                    existing.ended_at = ended_at

            await session.flush()
            synced_experiments.append(existing)

    return synced_experiments


@flags_router.post("/sync")
async def sync_feature_flags(
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
    project_id: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    """Sync Flagsmith projects, environments, features, mv-options, feature states, and native experiments."""
    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    fs = await build_flagsmith_client(request.app, session)

    pid = await ctx.resolve_flagsmith_project_id(project_id)
    if pid is None:
        projects = await fs.list_projects()
        if not projects:
            raise APIException(
                400, "FLAGSMITH_NO_PROJECTS", "No Flagsmith projects accessible for this token."
            )
        pid = int(projects[0]["id"])

    raw_flags = await fs.list_flags(pid)
    synced_flag_objs: list[FeatureFlag] = []
    synced_items: list[dict[str, Any]] = []
    for raw in raw_flags:
        key = str(raw.get("name", ""))
        if not key:
            continue
        flag_obj = await ctx.sync_flag_from_flagsmith(key, project_id=pid)
        synced_flag_objs.append(flag_obj)
        synced_items.append(FeatureFlagOut.model_validate(flag_obj).model_dump(mode="json"))

    synced_exp_objs = await _sync_native_experiments_for_project(
        session, fs, pid, synced_flag_objs
    )
    synced_experiments = [
        ExperimentOut.model_validate(exp).model_dump(mode="json")
        for exp in synced_exp_objs
    ]

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="flagsmith.synced",
        resource_type="feature_flag",
        status=AuditStatus.SUCCESS,
        details={
            "project_id": pid,
            "synced_count": len(synced_items),
            "experiments_synced_count": len(synced_experiments),
        },
    )
    await session.commit()
    return {
        "synced_count": len(synced_items),
        "items": synced_items,
        "experiments_synced_count": len(synced_experiments),
        "experiments": synced_experiments,
    }


@flags_router.post("", status_code=201)
async def create_feature_flag(
    body: FeatureFlagCreate,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> Any:
    """Category B: Create a Flagsmith feature flag (starts disabled across production)."""
    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    pid = await ctx.resolve_flagsmith_project_id(body.project_id)
    if pid is None:
        raise APIException(
            400,
            "FLAGSMITH_PROJECT_REQUIRED",
            "Specify project_id or connect Flagsmith with a project_id in /settings.",
        )

    _, requires_approval = await PolicyEngine.evaluate_action(
        session,
        action_type="flagsmith_create_flag",
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        resource_type="feature_flag",
        resource_id=body.key,
    )

    action_payload = {
        "project_id": pid,
        "key": body.key,
        "description": body.description,
        "flag_type": body.flag_type.value,
        "initial_value": body.initial_value,
        "mv_options": [m.model_dump(mode="json") for m in body.mv_options],
    }

    if requires_approval:
        res_type, res_id, expected_pre, _ = await capture_expected_precondition(
            ctx, action_type="flagsmith_create_flag", action_payload=action_payload
        )
        approval = await PolicyEngine.create_approval(
            session,
            action_type="flagsmith_create_flag",
            target_resource_type=res_type,
            target_resource_id=res_id,
            environment_name=None,
            title=f"Create Flagsmith flag '{body.key}'",
            rationale="Category B flag creation (starts disabled in production).",
            action_payload=action_payload,
            expected_precondition=expected_pre,
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

    await handle_flagsmith_create_flag(
        ctx, FlagsmithCreateFlagInput.model_validate(action_payload)
    )
    flag_obj = await ctx.find_feature_flag(flag_key=body.key, project_id=pid)
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="flagsmith.flag_created",
        resource_type="feature_flag",
        resource_id=str(flag_obj.id) if flag_obj else body.key,
        status=AuditStatus.SUCCESS,
        details={"key": body.key, "project_id": pid},
    )
    await session.commit()
    if flag_obj is not None:
        await session.refresh(flag_obj)
    return {
        "execution_status": "executed",
        "data": FeatureFlagOut.model_validate(flag_obj).model_dump(mode="json")
        if flag_obj
        else {"key": body.key},
    }


@flags_router.post("/nl-command")
async def run_flag_nl_command(
    body: FlagNLCommandRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> Any:
    """Dispatch a natural-language flag command through the flag_agent LangGraph pipeline."""
    ws = await PolicyEngine.get_workspace_settings(session)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)
    if not gemini_client.is_configured:
        raise APIException(
            503,
            "GEMINI_NOT_CONFIGURED",
            "GEMINI_API_KEY is required to execute natural-language flag commands.",
        )

    agent_sess: AgentSession | None = None
    if body.session_id is not None:
        agent_sess = await session.get(AgentSession, body.session_id)
    if agent_sess is None:
        scope: dict[str, Any] = {}
        if body.project_id is not None:
            scope["flagsmith_project_id"] = body.project_id
        if body.environment_name:
            scope["environment"] = body.environment_name
        agent_sess = AgentSession(
            user_id=operator.id,
            title=f"Flag NL: {body.command[:60]}",
            thread_id=f"rg-nl-{uuid.uuid4().hex}",
            active_specialist=SpecialistRole.FLAG_AGENT,
            status=AgentSessionStatus.ACTIVE,
            context_scope=scope,
            message_history=[
                {
                    "role": "user",
                    "content": body.command,
                    "timestamp": utcnow().isoformat(),
                }
            ],
        )
        session.add(agent_sess)
        await session.flush()

    task = AgentTask(
        session_id=agent_sess.id,
        specialist=SpecialistRole.FLAG_AGENT,
        user_prompt=body.command,
        status=AgentTaskStatus.QUEUED,
        gemini_model=ws.gemini_model,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    supervisor = build_supervisor(request.app, model_override=ws.gemini_model)
    result = await supervisor.run_task(
        task_id=task.id,
        session_id=agent_sess.id,
        thread_id=agent_sess.thread_id,
        user_prompt=body.command,
        specialist_hint="flag_agent",
        context_scope=dict(agent_sess.context_scope or {}),
        user_id=operator.id,
    )

    await session.refresh(task)
    stream_url = f"/api/v1/agent/tasks/{task.id}/stream"

    if task.status == AgentTaskStatus.AWAITING_APPROVAL:
        app_row = (
            await session.execute(
                select(Approval)
                .where(
                    Approval.agent_task_id == task.id,
                    Approval.status == ApprovalStatus.PENDING,
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return JSONResponse(
            status_code=202,
            content={
                "execution_status": "approval_required",
                "task_id": str(task.id),
                "stream_url": stream_url,
                "approval": ApprovalOut.model_validate(app_row).model_dump(mode="json")
                if app_row
                else None,
            },
        )

    return {
        "execution_status": "executed",
        "task_id": str(task.id),
        "stream_url": stream_url,
        "data": {
            "final_response": task.final_response or result.get("final_response"),
            "tool_results": result.get("tool_results", []),
        },
    }


async def _handle_flag_mutation(
    *,
    request: Request,
    session: AsyncSession,
    operator: User,
    flag_id: uuid.UUID,
    action_type: str,
    environment_name: str,
    action_payload: dict[str, Any],
    title: str,
    rationale: str,
) -> Any:
    flag = await session.get(FeatureFlag, flag_id)
    if flag is None:
        raise APIException(404, "FEATURE_FLAG_NOT_FOUND", f"Feature flag {flag_id} not found.")

    category, requires_approval = await PolicyEngine.evaluate_action(
        session,
        action_type=action_type,
        environment_name=environment_name,
        env_states=dict(flag.env_states or {}),
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        resource_type="feature_flag",
        resource_id=str(flag.id),
    )

    ctx = await build_tool_context(request.app, session, user_id=operator.id)

    if requires_approval:
        res_type, res_id, expected_pre, env_states = await capture_expected_precondition(
            ctx, action_type=action_type, action_payload=action_payload
        )
        approval = await PolicyEngine.create_approval(
            session,
            action_type=action_type,
            target_resource_type=res_type,
            target_resource_id=res_id,
            environment_name=environment_name.strip().lower(),
            title=title[:255],
            rationale=rationale,
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

    if action_type == "flagsmith_toggle_flag":
        res = await handle_flagsmith_toggle_flag(
            ctx, FlagsmithToggleFlagInput.model_validate(action_payload)
        )
    elif action_type == "flagsmith_set_gradual_rollout_stage":
        res = await handle_flagsmith_set_gradual_rollout_stage(
            ctx, FlagsmithSetGradualRolloutStageInput.model_validate(action_payload)
        )
    else:
        res = await handle_flagsmith_rollback_flag(
            ctx, FlagsmithRollbackFlagInput.model_validate(action_payload)
        )

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=category,
        event_action=f"flagsmith.{action_type}",
        resource_type="feature_flag",
        resource_id=str(flag.id),
        environment_name=environment_name.strip().lower(),
        status=AuditStatus.SUCCESS,
        details=res,
    )
    from app.models import NotificationCategory
    from app.services.notification_service import (
        broadcast_workspace_event,
        create_notification,
    )

    if action_type == "flagsmith_rollback_flag":
        await create_notification(
            session,
            category=NotificationCategory.ROLLBACK_TRIGGERED,
            severity="high",
            title=f"Rollback Executed: `{flag.key}` ({environment_name.strip().lower()})",
            message=rationale or f"Flag `{flag.key}` rolled back to disabled/0%.",
            resource_type="feature_flag",
            resource_id=str(flag.id),
            environment_name=environment_name.strip().lower(),
            action_href="/releases",
            metadata=res,
        )
    else:
        await broadcast_workspace_event(
            "flag.mutated",
            {"flag_id": str(flag.id), "flag_key": flag.key, "environment": environment_name.strip().lower()},
        )
    await session.commit()
    await session.refresh(flag)
    return {
        "execution_status": "executed",
        "data": FeatureFlagOut.model_validate(flag).model_dump(mode="json"),
        "verification": res,
    }


@flags_router.post("/{flag_id}/toggle")
async def toggle_feature_flag(
    flag_id: uuid.UUID,
    body: FlagToggleRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> Any:
    """Toggle a feature flag ON/OFF (Category B in non-prod; Category C in prod)."""
    flag = await session.get(FeatureFlag, flag_id)
    if flag is None:
        raise APIException(404, "FEATURE_FLAG_NOT_FOUND", f"Feature flag {flag_id} not found.")

    state_label = "enable" if body.enabled else "disable"
    return await _handle_flag_mutation(
        request=request,
        session=session,
        operator=operator,
        flag_id=flag_id,
        action_type="flagsmith_toggle_flag",
        environment_name=body.environment_name,
        action_payload={
            "flag_key": flag.key,
            "environment_name": body.environment_name.strip().lower(),
            "enabled": body.enabled,
            "reason": body.reason,
        },
        title=f"{state_label.capitalize()} '{flag.key}' in {body.environment_name}",
        rationale=body.reason,
    )


@flags_router.post("/{flag_id}/rollout")
async def rollout_feature_flag(
    flag_id: uuid.UUID,
    body: FlagRolloutRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> Any:
    """Set gradual rollout stage (0/10/25/50/75/100%); Category C in production."""
    flag = await session.get(FeatureFlag, flag_id)
    if flag is None:
        raise APIException(404, "FEATURE_FLAG_NOT_FOUND", f"Feature flag {flag_id} not found.")

    return await _handle_flag_mutation(
        request=request,
        session=session,
        operator=operator,
        flag_id=flag_id,
        action_type="flagsmith_set_gradual_rollout_stage",
        environment_name=body.environment_name,
        action_payload={
            "flag_key": flag.key,
            "environment_name": body.environment_name.strip().lower(),
            "target_percentage": body.target_percentage,
            "strategy": body.strategy,
            "reason": body.reason,
        },
        title=f"Roll out '{flag.key}' to {body.target_percentage}% in {body.environment_name}",
        rationale=body.reason,
    )


@flags_router.post("/{flag_id}/rollback")
async def rollback_feature_flag(
    flag_id: uuid.UUID,
    body: FlagRollbackRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> Any:
    """Rollback feature flag to 0% / disabled; Category C in production."""
    flag = await session.get(FeatureFlag, flag_id)
    if flag is None:
        raise APIException(404, "FEATURE_FLAG_NOT_FOUND", f"Feature flag {flag_id} not found.")

    return await _handle_flag_mutation(
        request=request,
        session=session,
        operator=operator,
        flag_id=flag_id,
        action_type="flagsmith_rollback_flag",
        environment_name=body.environment_name,
        action_payload={
            "flag_key": flag.key,
            "environment_name": body.environment_name.strip().lower(),
            "reason": body.reason,
        },
        title=f"Emergency rollback '{flag.key}' in {body.environment_name} to 0%",
        rationale=body.reason,
    )


@flag_health_router.get("")
async def get_flag_health(
    session: DBSession,
    _: AuthenticatedOperator,
    issue_type: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """List flags sorted by health_score ASC with explainable detector breakdown."""
    rows = list(
        (await session.execute(select(FeatureFlag).order_by(FeatureFlag.health_score.asc())))
        .scalars()
        .all()
    )
    detector_counts: dict[str, int] = {}
    for f in rows:
        for iss in f.health_issues or []:
            if isinstance(iss, dict) and iss.get("issue_type"):
                it = str(iss["issue_type"])
                detector_counts[it] = detector_counts.get(it, 0) + 1

    if issue_type:
        norm = issue_type.strip().lower()
        filtered = [
            f
            for f in rows
            if any(
                isinstance(iss, dict) and str(iss.get("issue_type", "")).lower() == norm
                for iss in (f.health_issues or [])
            )
        ]
    else:
        filtered = rows

    avg = round(sum(f.health_score for f in filtered) / len(filtered), 2) if filtered else 100.0
    unhealthy = sum(1 for f in filtered if f.health_issues)
    return {
        "average_health_score": avg,
        "total_flags": len(filtered),
        "unhealthy_count": unhealthy,
        "detector_counts": detector_counts,
        "items": [FeatureFlagOut.model_validate(f).model_dump(mode="json") for f in filtered],
    }


@flag_health_router.post("/scan", response_model=FlagHealthScanResponse)
async def scan_flag_health_endpoint(
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
    body: dict[str, Any] | None = None,
) -> FlagHealthScanResponse:
    """Category A: Run all 7 deterministic Flag Health detectors and persist explainable scores."""
    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    scan_input = ScanFlagHealthInput.model_validate(body or {})
    out = await handle_scan_flag_health(ctx, scan_input)

    results = list(out.get("results") or [])
    flags_items = [FlagHealthItem.model_validate(r) for r in results]
    issues_by_type: dict[str, int] = {}
    for item in flags_items:
        for iss in item.health_issues:
            issues_by_type[iss.issue_type] = issues_by_type.get(iss.issue_type, 0) + 1

    avg_score = (
        round(sum(item.health_score for item in flags_items) / len(flags_items), 2)
        if flags_items
        else 100.0
    )

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="flag_health.scanned",
        resource_type="feature_flag",
        status=AuditStatus.SUCCESS,
        details={
            "flags_scanned": len(flags_items),
            "unhealthy_flags": out["unhealthy_flags"],
            "average_health_score": avg_score,
        },
    )
    await session.commit()

    return FlagHealthScanResponse(
        flags_scanned=len(flags_items),
        unhealthy_flags=int(out["unhealthy_flags"]),
        average_health_score=avg_score,
        issues_by_type=issues_by_type,
        flags=flags_items,
    )


@flag_health_router.post("/{flag_id}/cleanup-pr")
async def generate_flag_cleanup_pr(
    flag_id: uuid.UUID,
    body: CleanupPRRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> Any:
    """Generate & validate a flag cleanup patch, optionally opening a Draft PR (Category B)."""
    flag = await session.get(FeatureFlag, flag_id)
    if flag is None:
        raise APIException(404, "FEATURE_FLAG_NOT_FOUND", f"Feature flag {flag_id} not found.")
    repo = await session.get(Repository, body.repository_id)
    if repo is None:
        raise APIException(404, "REPOSITORY_NOT_FOUND", f"Repository {body.repository_id} not found.")

    patch_payload = generate_cleanup_patch_payload(
        flag_key=flag.key,
        keep_branch=body.keep_branch,
        code_references=list(flag.code_references or []),
    )
    change = CodeChange(
        repository_id=repo.id,
        feature_flag_id=flag.id,
        change_type=CodeChangeType.FLAG_CLEANUP,
        flag_key=flag.key,
        title=patch_payload["title"],
        description=patch_payload["description"],
        files_diff=patch_payload["files_diff"],
        generated_tests=patch_payload["generated_tests"],
        validation_status=ValidationStatus.PENDING,
        status=CodeChangeStatus.DRAFT,
    )
    session.add(change)
    await session.flush()

    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    await handle_validate_patch_isolated(ctx, ValidatePatchIsolatedInput(code_change_id=change.id))
    await session.refresh(change)

    if body.open_draft_pr and change.validation_status == ValidationStatus.PASSED:
        _, requires_approval = await PolicyEngine.evaluate_action(
            session,
            action_type="github_create_branch_and_draft_pr",
            actor_type=ActorType.USER,
            actor_id=str(operator.id),
            resource_type="code_change",
            resource_id=str(change.id),
        )
        action_payload = {
            "code_change_id": str(change.id),
            "base_branch": repo.default_branch,
            "pr_title": change.title,
            "pr_body": change.description or "",
        }
        if requires_approval:
            res_type, res_id, expected_pre, _ = await capture_expected_precondition(
                ctx,
                action_type="github_create_branch_and_draft_pr",
                action_payload=action_payload,
            )
            approval = await PolicyEngine.create_approval(
                session,
                action_type="github_create_branch_and_draft_pr",
                target_resource_type=res_type,
                target_resource_id=res_id,
                environment_name=None,
                title=f"Open Cleanup Draft PR for '{flag.key}'",
                rationale=f"Cleanup Draft PR keeping '{body.keep_branch}' branch.",
                action_payload=action_payload,
                expected_precondition=expected_pre,
                requested_by_id=operator.id,
            )
            await session.commit()
            await session.refresh(approval)
            return JSONResponse(
                status_code=202,
                content={
                    "execution_status": "approval_required",
                    "data": CodeChangeOut.model_validate(change).model_dump(mode="json"),
                    "approval": ApprovalOut.model_validate(approval).model_dump(mode="json"),
                },
            )
        await handle_github_create_branch_and_draft_pr(
            ctx, GitHubCreateBranchAndDraftPRInput.model_validate(action_payload)
        )
        await session.refresh(change)

    await session.commit()
    return {
        "execution_status": "executed",
        "data": CodeChangeOut.model_validate(change).model_dump(mode="json"),
    }


@flag_health_router.post("/{flag_id}/request-delete")
async def request_flag_delete(
    flag_id: uuid.UUID,
    body: FlagDeleteRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> JSONResponse:
    """Always Category C: queue mandatory single-use human approval to archive/delete a flag."""
    flag = await session.get(FeatureFlag, flag_id)
    if flag is None:
        raise APIException(404, "FEATURE_FLAG_NOT_FOUND", f"Feature flag {flag_id} not found.")

    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    action_payload = {
        "flag_key": flag.key,
        "action": body.action,
        "reason": body.reason,
    }
    res_type, res_id, expected_pre, env_states = await capture_expected_precondition(
        ctx,
        action_type="flagsmith_delete_or_archive_flag",
        action_payload=action_payload,
    )
    approval = await PolicyEngine.create_approval(
        session,
        action_type="flagsmith_delete_or_archive_flag",
        target_resource_type=res_type,
        target_resource_id=res_id,
        environment_name="production",
        title=f"{body.action.capitalize()} Flagsmith flag '{flag.key}'",
        rationale=body.reason,
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
