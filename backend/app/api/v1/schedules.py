"""API router for Persistent Scheduling, Cron/TZ Preview, Pause/Resume/Run, and History."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, update

from app.api.deps import (
    AuthenticatedOperator,
    DBSession,
    DefaultOperator,
    build_gemini_client_from_app,
    rate_limit_dependency,
)
from app.api.v1.errors import APIException
from app.core.database import ensure_utc, utcnow
from app.models import (
    ActorType,
    AuditStatus,
    JobExecution,
    JobExecutionStatus,
    JobTriggerType,
    PolicyCategory,
    ScheduledJob,
    ScheduleFrequency,
    ScheduleJobType,
    User,
    UserRole,
)
from app.schemas import (
    JobExecutionOut,
    PaginatedResponse,
    ScheduleCreateRequest,
    ScheduleOut,
    SchedulePreviewRequest,
    ScheduleUpdateRequest,
)
from app.services.cron_utils import (
    build_cron_from_frequency,
    compute_next_run_at,
    describe_schedule,
    parse_nl_schedule_plan,
    preview_next_runs,
    validate_custom_schedule_prompt,
    validate_iana_timezone,
)
from app.services.policy_engine import PolicyEngine
from app.services.scheduler import JobAlreadyRunningError, get_scheduler_service

schedules_router = APIRouter(prefix="/schedules", tags=["schedules"])


def _assert_schedule_modify_access(operator: User, job: ScheduledJob) -> None:
    if operator.role in (UserRole.ADMIN, UserRole.RELEASE_MANAGER):
        return
    if job.owner_id is not None and job.owner_id != operator.id:
        raise APIException(
            403,
            "FORBIDDEN_SCHEDULE_ACCESS",
            "Only the schedule creator, a Release Manager, or an Admin can modify this schedule.",
        )


def _normalize_dt(value: Any) -> Any:
    return ensure_utc(value)


def _schedule_to_out(job: ScheduledJob) -> ScheduleOut:
    tz = job.timezone or "UTC"
    try:
        human_desc = describe_schedule(
            frequency=job.frequency.value,
            cron_expression=job.cron_expression,
            interval_minutes=job.interval_minutes,
            start_at=job.start_at,
            timezone=tz,
        )
    except ValueError:
        human_desc = f"{job.frequency.value} ({tz})"

    runs_preview: list[dict[str, str]] = []
    if job.enabled:
        try:
            runs_preview = preview_next_runs(
                frequency=job.frequency.value,
                cron_expression=job.cron_expression,
                interval_minutes=job.interval_minutes,
                start_at=job.start_at,
                end_at=job.end_at,
                timezone=tz,
                count=5,
            )
        except ValueError:
            runs_preview = []

    return ScheduleOut(
        id=job.id,
        name=job.name,
        description=job.description,
        job_type=job.job_type,
        frequency=job.frequency,
        cron_expression=job.cron_expression,
        interval_minutes=job.interval_minutes,
        timezone=tz,
        start_at=job.start_at,
        end_at=job.end_at,
        configuration=dict(job.configuration or {}),
        retry_policy=dict(job.retry_policy or {}),
        notification_preference=dict(job.notification_preference or {}),
        enabled=job.enabled,
        owner_id=job.owner_id,
        last_run_at=job.last_run_at,
        next_run_at=job.next_run_at,
        last_status=job.last_status,
        consecutive_failures=job.consecutive_failures,
        missed_runs_count=job.missed_runs_count,
        human_description=human_desc,
        next_runs_preview=runs_preview,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _validate_and_resolve_schedule_timing(
    *,
    frequency: ScheduleFrequency,
    cron_expression: str | None,
    interval_minutes: int | None,
    timezone: str,
    start_at: Any,
    end_at: Any,
    enabled: bool,
    job_type: ScheduleJobType,
    configuration: dict[str, Any],
) -> tuple[str | None, int | None, Any]:
    try:
        validate_iana_timezone(timezone)
    except ValueError as exc:
        raise APIException(400, "INVALID_TIMEZONE", str(exc)) from exc

    start_utc = _normalize_dt(start_at)
    end_utc = _normalize_dt(end_at)
    if start_utc is not None and end_utc is not None and end_utc <= start_utc:
        raise APIException(400, "INVALID_SCHEDULE_WINDOW", "end_at must be later than start_at.")

    if job_type == ScheduleJobType.CUSTOM_AGENT_TASK:
        prompt = str(configuration.get("prompt") or configuration.get("nl_prompt") or "").strip()
        ok, err_msg = validate_custom_schedule_prompt(prompt)
        if not ok:
            raise APIException(400, "UNSAFE_SCHEDULE_PROMPT", err_msg or "Invalid custom agent prompt.")

    try:
        resolved_cron = build_cron_from_frequency(
            frequency=frequency.value,
            cron_expression=cron_expression,
            interval_minutes=interval_minutes,
            start_at=start_utc,
            timezone=timezone,
        )
        next_run = (
            compute_next_run_at(
                frequency=frequency.value,
                cron_expression=resolved_cron,
                interval_minutes=interval_minutes,
                start_at=start_utc,
                end_at=end_utc,
                timezone=timezone,
                initial=True,
            )
            if enabled
            else None
        )
    except ValueError as exc:
        raise APIException(400, "INVALID_CRON_SCHEDULE", str(exc)) from exc

    resolved_interval = interval_minutes if frequency == ScheduleFrequency.INTERVAL_MINUTES else None
    return resolved_cron, resolved_interval, next_run


@schedules_router.get("", response_model=PaginatedResponse[ScheduleOut])
async def list_schedules(
    session: DBSession,
    _: AuthenticatedOperator,
    job_type: Annotated[ScheduleJobType | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    owner_id: Annotated[uuid.UUID | None, Query()] = None,
    last_status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[ScheduleOut]:
    """List persistent scheduled jobs filterable by type, status, environment, and owner."""
    stmt = select(ScheduledJob)
    if job_type is not None:
        stmt = stmt.where(ScheduledJob.job_type == job_type)
    if enabled is not None:
        stmt = stmt.where(ScheduledJob.enabled.is_(enabled))
    if owner_id is not None:
        stmt = stmt.where(ScheduledJob.owner_id == owner_id)
    if last_status:
        stmt = stmt.where(ScheduledJob.last_status == last_status.strip().lower())

    rows = list((await session.execute(stmt.order_by(ScheduledJob.created_at.desc()))).scalars().all())
    if environment:
        env_norm = environment.strip().lower()
        rows = [
            r
            for r in rows
            if str((r.configuration or {}).get("environment_name") or (r.configuration or {}).get("environment") or "")
            .strip()
            .lower()
            == env_norm
        ]

    total = len(rows)
    page_rows = rows[offset : offset + limit]
    return PaginatedResponse(
        items=[_schedule_to_out(r) for r in page_rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@schedules_router.post("/preview")
async def preview_schedule(
    body: SchedulePreviewRequest,
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Validate cron/timezone/interval or translate natural-language automation into safe plan & next 5 runs."""
    try:
        validate_iana_timezone(body.timezone)
    except ValueError as exc:
        raise APIException(400, "INVALID_TIMEZONE", str(exc)) from exc

    nl_plan: dict[str, Any] | None = None
    freq_val = body.frequency.value
    cron_val = body.cron_expression
    interval_val = body.interval_minutes

    if body.nl_prompt and body.nl_prompt.strip():
        ws = await PolicyEngine.get_workspace_settings(session)
        gemini = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)
        try:
            nl_plan = await parse_nl_schedule_plan(
                body.nl_prompt,
                timezone=body.timezone,
                gemini_client=gemini,
            )
            freq_val = str(nl_plan.get("frequency") or freq_val)
            cron_val = nl_plan.get("cron_expression")
            interval_val = nl_plan.get("interval_minutes")
        except ValueError as exc:
            raise APIException(400, "UNSAFE_SCHEDULE_PROMPT", str(exc)) from exc

    try:
        resolved_cron = build_cron_from_frequency(
            frequency=freq_val,
            cron_expression=cron_val,
            interval_minutes=interval_val,
            start_at=_normalize_dt(body.start_at),
            timezone=body.timezone,
        )
        human_description = describe_schedule(
            frequency=freq_val,
            cron_expression=resolved_cron,
            interval_minutes=interval_val,
            start_at=_normalize_dt(body.start_at),
            timezone=body.timezone,
        )
        next_runs = preview_next_runs(
            frequency=freq_val,
            cron_expression=resolved_cron,
            interval_minutes=interval_val,
            start_at=_normalize_dt(body.start_at),
            end_at=_normalize_dt(body.end_at),
            timezone=body.timezone,
            count=5,
        )
    except ValueError as exc:
        raise APIException(400, "INVALID_CRON_SCHEDULE", str(exc)) from exc

    return {
        "valid": True,
        "frequency": freq_val,
        "cron_expression": resolved_cron,
        "interval_minutes": interval_val if freq_val == "interval_minutes" else None,
        "timezone": body.timezone,
        "human_description": human_description,
        "next_runs": next_runs,
        "nl_plan": nl_plan,
        "policy_notice": "Scheduled jobs cannot bypass Category B/C human approvals or execute shell commands.",
    }


@schedules_router.get("/executions", response_model=PaginatedResponse[JobExecutionOut])
async def list_all_job_executions(
    session: DBSession,
    _: AuthenticatedOperator,
    scheduled_job_id: Annotated[uuid.UUID | None, Query()] = None,
    job_type: Annotated[ScheduleJobType | None, Query()] = None,
    status: Annotated[JobExecutionStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[JobExecutionOut]:
    """List global schedule execution history (preserved even after schedule deletion)."""
    stmt = select(JobExecution)
    count_stmt = select(func.count(JobExecution.id))

    if scheduled_job_id is not None:
        stmt = stmt.where(JobExecution.scheduled_job_id == scheduled_job_id)
        count_stmt = count_stmt.where(JobExecution.scheduled_job_id == scheduled_job_id)
    if job_type is not None:
        stmt = stmt.where(JobExecution.job_type == job_type)
        count_stmt = count_stmt.where(JobExecution.job_type == job_type)
    if status is not None:
        stmt = stmt.where(JobExecution.execution_status == status)
        count_stmt = count_stmt.where(JobExecution.execution_status == status)

    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(JobExecution.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse(
        items=[JobExecutionOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@schedules_router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    body: ScheduleCreateRequest,
    session: DBSession,
    operator: DefaultOperator,
) -> ScheduleOut:
    """Create a persistent scheduled automation job in PostgreSQL/SQLite."""
    config = dict(body.configuration or {})
    if body.job_type == ScheduleJobType.CUSTOM_AGENT_TASK and not config.get("prompt") and body.description:
        config["prompt"] = body.description

    resolved_cron, resolved_interval, next_run = _validate_and_resolve_schedule_timing(
        frequency=body.frequency,
        cron_expression=body.cron_expression,
        interval_minutes=body.interval_minutes,
        timezone=body.timezone,
        start_at=body.start_at,
        end_at=body.end_at,
        enabled=body.enabled,
        job_type=body.job_type,
        configuration=config,
    )

    job = ScheduledJob(
        name=body.name.strip(),
        description=body.description,
        job_type=body.job_type,
        frequency=body.frequency,
        cron_expression=resolved_cron,
        interval_minutes=resolved_interval,
        timezone=body.timezone.strip(),
        start_at=_normalize_dt(body.start_at),
        end_at=_normalize_dt(body.end_at),
        configuration=config,
        retry_policy=body.retry_policy.model_dump(mode="json"),
        notification_preference=body.notification_preference.model_dump(mode="json"),
        enabled=body.enabled,
        owner_id=operator.id,
        next_run_at=next_run,
        last_status="scheduled" if body.enabled else "paused",
    )
    session.add(job)
    await session.flush()

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="schedule.created",
        resource_type="scheduled_job",
        resource_id=str(job.id),
        status=AuditStatus.SUCCESS,
        details={
            "name": job.name,
            "job_type": job.job_type.value,
            "frequency": job.frequency.value,
            "cron_expression": job.cron_expression,
            "timezone": job.timezone,
            "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        },
    )
    await session.commit()
    await session.refresh(job)
    return _schedule_to_out(job)


@schedules_router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(
    schedule_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> ScheduleOut:
    job = await session.get(ScheduledJob, schedule_id)
    if job is None:
        raise APIException(404, "SCHEDULE_NOT_FOUND", f"Schedule {schedule_id} not found.")
    return _schedule_to_out(job)


@schedules_router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: uuid.UUID,
    body: ScheduleUpdateRequest,
    session: DBSession,
    operator: DefaultOperator,
) -> ScheduleOut:
    """Edit schedule configuration, timing, retry policy, or notification preferences."""
    job = await session.get(ScheduledJob, schedule_id)
    if job is None:
        raise APIException(404, "SCHEDULE_NOT_FOUND", f"Schedule {schedule_id} not found.")
    _assert_schedule_modify_access(operator, job)

    new_name = body.name.strip() if body.name is not None else job.name
    new_desc = body.description if body.description is not None else job.description
    new_type = body.job_type or job.job_type
    new_freq = body.frequency or job.frequency
    new_cron = body.cron_expression if body.cron_expression is not None else job.cron_expression
    new_interval = body.interval_minutes if body.interval_minutes is not None else job.interval_minutes
    new_tz = body.timezone.strip() if body.timezone is not None else job.timezone
    new_start = body.start_at if body.start_at is not None else job.start_at
    new_end = body.end_at if body.end_at is not None else job.end_at
    new_cfg = dict(body.configuration) if body.configuration is not None else dict(job.configuration or {})
    new_enabled = body.enabled if body.enabled is not None else job.enabled

    resolved_cron, resolved_interval, next_run = _validate_and_resolve_schedule_timing(
        frequency=new_freq,
        cron_expression=new_cron,
        interval_minutes=new_interval,
        timezone=new_tz,
        start_at=new_start,
        end_at=new_end,
        enabled=new_enabled,
        job_type=new_type,
        configuration=new_cfg,
    )

    job.name = new_name
    job.description = new_desc
    job.job_type = new_type
    job.frequency = new_freq
    job.cron_expression = resolved_cron
    job.interval_minutes = resolved_interval
    job.timezone = new_tz
    job.start_at = _normalize_dt(new_start)
    job.end_at = _normalize_dt(new_end)
    job.configuration = new_cfg
    if body.retry_policy is not None:
        job.retry_policy = body.retry_policy.model_dump(mode="json")
    if body.notification_preference is not None:
        job.notification_preference = body.notification_preference.model_dump(mode="json")
    job.enabled = new_enabled
    job.next_run_at = next_run
    if not new_enabled and job.last_status == "scheduled":
        job.last_status = "paused"
    elif new_enabled and job.last_status == "paused":
        job.last_status = "scheduled"
    job.updated_at = utcnow()

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="schedule.updated",
        resource_type="scheduled_job",
        resource_id=str(job.id),
        status=AuditStatus.SUCCESS,
        details={"enabled": job.enabled, "cron_expression": job.cron_expression, "timezone": job.timezone},
    )
    await session.commit()
    await session.refresh(job)
    return _schedule_to_out(job)


@schedules_router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: uuid.UUID,
    session: DBSession,
    operator: DefaultOperator,
) -> dict[str, Any]:
    """Delete schedule while strictly preserving historical JobExecution records."""
    job = await session.get(ScheduledJob, schedule_id)
    if job is None:
        raise APIException(404, "SCHEDULE_NOT_FOUND", f"Schedule {schedule_id} not found.")
    _assert_schedule_modify_access(operator, job)

    job_name = job.name
    # Explicitly decouple execution history before row deletion for SQLite & PostgreSQL safety
    await session.execute(
        update(JobExecution)
        .where(JobExecution.scheduled_job_id == job.id)
        .values(scheduled_job_id=None)
    )
    await session.delete(job)

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="schedule.deleted",
        resource_type="scheduled_job",
        resource_id=str(schedule_id),
        status=AuditStatus.SUCCESS,
        details={"name": job_name, "history_preserved": True},
    )
    await session.commit()
    return {"deleted": True, "id": str(schedule_id), "history_preserved": True}


@schedules_router.post("/{schedule_id}/pause", response_model=ScheduleOut)
async def pause_schedule(
    schedule_id: uuid.UUID,
    session: DBSession,
    operator: DefaultOperator,
) -> ScheduleOut:
    job = await session.get(ScheduledJob, schedule_id)
    if job is None:
        raise APIException(404, "SCHEDULE_NOT_FOUND", f"Schedule {schedule_id} not found.")
    _assert_schedule_modify_access(operator, job)

    job.enabled = False
    if job.last_status in (None, "scheduled"):
        job.last_status = "paused"
    job.updated_at = utcnow()

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="schedule.paused",
        resource_type="scheduled_job",
        resource_id=str(job.id),
        status=AuditStatus.SUCCESS,
        details={"name": job.name},
    )
    await session.commit()
    await session.refresh(job)
    return _schedule_to_out(job)


@schedules_router.post("/{schedule_id}/resume", response_model=ScheduleOut)
async def resume_schedule(
    schedule_id: uuid.UUID,
    session: DBSession,
    operator: DefaultOperator,
) -> ScheduleOut:
    job = await session.get(ScheduledJob, schedule_id)
    if job is None:
        raise APIException(404, "SCHEDULE_NOT_FOUND", f"Schedule {schedule_id} not found.")
    _assert_schedule_modify_access(operator, job)

    nxt = compute_next_run_at(
        frequency=job.frequency.value,
        cron_expression=job.cron_expression,
        interval_minutes=job.interval_minutes,
        start_at=job.start_at,
        end_at=job.end_at,
        timezone=job.timezone,
        initial=(job.last_run_at is None),
    )
    if nxt is None and job.end_at is not None:
        raise APIException(400, "SCHEDULE_EXPIRED", "Cannot resume schedule past its configured end_at window.")

    job.enabled = True
    job.next_run_at = nxt
    job.last_status = "scheduled"
    job.updated_at = utcnow()

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="schedule.resumed",
        resource_type="scheduled_job",
        resource_id=str(job.id),
        status=AuditStatus.SUCCESS,
        details={"name": job.name, "next_run_at": nxt.isoformat() if nxt else None},
    )
    await session.commit()
    await session.refresh(job)
    return _schedule_to_out(job)


@schedules_router.post(
    "/{schedule_id}/run",
    response_model=JobExecutionOut,
    dependencies=[Depends(rate_limit_dependency("schedule_manual_run", max_requests=30, window_seconds=60))],
)
async def run_schedule_now(
    schedule_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> JobExecutionOut:
    """Trigger immediate manual execution of a scheduled job under PolicyEngine & atomic lock."""
    job = await session.get(ScheduledJob, schedule_id)
    if job is None:
        raise APIException(404, "SCHEDULE_NOT_FOUND", f"Schedule {schedule_id} not found.")
    _assert_schedule_modify_access(operator, job)

    svc = get_scheduler_service(request.app)
    try:
        exec_row = await svc.run_job_by_id(
            schedule_id,
            trigger_type=JobTriggerType.MANUAL,
            triggered_by_user_id=operator.id,
        )
    except JobAlreadyRunningError as exc:
        raise APIException(409, "JOB_ALREADY_RUNNING", str(exc)) from exc

    return JobExecutionOut.model_validate(exec_row)


@schedules_router.get("/{schedule_id}/executions", response_model=PaginatedResponse[JobExecutionOut])
async def list_schedule_executions(
    schedule_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[JobExecutionOut]:
    job = await session.get(ScheduledJob, schedule_id)
    if job is None:
        raise APIException(404, "SCHEDULE_NOT_FOUND", f"Schedule {schedule_id} not found.")

    count_stmt = select(func.count(JobExecution.id)).where(JobExecution.scheduled_job_id == schedule_id)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                select(JobExecution)
                .where(JobExecution.scheduled_job_id == schedule_id)
                .order_by(JobExecution.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse(
        items=[JobExecutionOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
