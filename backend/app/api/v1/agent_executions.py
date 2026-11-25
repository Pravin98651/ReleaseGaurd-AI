"""Agent Command Center REST & SSE endpoints (/api/v1/agent-executions)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedOperator,
    DBSession,
    DeveloperOperator,
)
from app.api.v1.errors import APIException
from app.models import (
    ActorType,
    AgentDecision,
    AgentExecution,
    AgentExecutionSnapshot,
    AgentExecutionStatus,
    AgentExecutionStep,
    AgentExecutionTrace,
    AgentToolCall,
    AgentToolCallStatus,
    Approval,
    UserRole,
)
from app.schemas import (
    AgentDecisionOut,
    AgentExecutionControlRequest,
    AgentExecutionCreateRequest,
    AgentExecutionDetailOut,
    AgentExecutionOut,
    AgentExecutionOverviewOut,
    AgentExecutionSnapshotOut,
    AgentExecutionStepOut,
    AgentExecutionTraceOut,
    AgentToolCallOut,
    ApprovalOut,
    PaginatedResponse,
)
from app.services.agent_execution_service import (
    ACTIVE_EXECUTION_STATES,
    AgentExecutionControlError,
    AgentExecutionService,
)
from app.services.sse_bus import format_sse, sse_bus

agent_executions_router = APIRouter(prefix="/agent-executions", tags=["command-center"])


async def _load_execution_or_404(
    session: AsyncSession, execution_id: uuid.UUID
) -> AgentExecution:
    exe = await session.get(AgentExecution, execution_id)
    if exe is None:
        raise APIException(
            status_code=404,
            code="execution_not_found",
            message=f"Agent execution {execution_id} not found.",
        )
    return exe


async def _build_execution_detail(
    session: AsyncSession, exe: AgentExecution
) -> AgentExecutionDetailOut:
    steps = list(
        (
            await session.execute(
                select(AgentExecutionStep)
                .where(AgentExecutionStep.execution_id == exe.id)
                .order_by(AgentExecutionStep.sequence)
            )
        )
        .scalars()
        .all()
    )
    tool_calls = list(
        (
            await session.execute(
                select(AgentToolCall)
                .where(AgentToolCall.execution_id == exe.id)
                .order_by(AgentToolCall.created_at)
            )
        )
        .scalars()
        .all()
    )
    decisions = list(
        (
            await session.execute(
                select(AgentDecision)
                .where(AgentDecision.execution_id == exe.id)
                .order_by(AgentDecision.created_at)
            )
        )
        .scalars()
        .all()
    )
    traces = list(
        (
            await session.execute(
                select(AgentExecutionTrace)
                .where(AgentExecutionTrace.execution_id == exe.id)
                .order_by(AgentExecutionTrace.sequence_number)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    checkpoints = list(
        (
            await session.execute(
                select(AgentExecutionSnapshot)
                .where(AgentExecutionSnapshot.execution_id == exe.id)
                .order_by(AgentExecutionSnapshot.checkpoint_number.desc())
            )
        )
        .scalars()
        .all()
    )
    approvals: list[Approval] = []
    if exe.agent_task_id is not None:
        approvals = list(
            (
                await session.execute(
                    select(Approval)
                    .where(Approval.agent_task_id == exe.agent_task_id)
                    .order_by(Approval.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    elif exe.release_id is not None:
        approvals = list(
            (
                await session.execute(
                    select(Approval)
                    .where(Approval.release_id == exe.release_id)
                    .order_by(Approval.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    return AgentExecutionDetailOut(
        execution=AgentExecutionOut.model_validate(exe),
        steps=[AgentExecutionStepOut.model_validate(s) for s in steps],
        tool_calls=[AgentToolCallOut.model_validate(t) for t in tool_calls],
        decisions=[AgentDecisionOut.model_validate(d) for d in decisions],
        traces=[AgentExecutionTraceOut.model_validate(tr) for tr in traces],
        checkpoints=[AgentExecutionSnapshotOut.model_validate(c) for c in checkpoints],
        approvals=[ApprovalOut.model_validate(a) for a in approvals],
    )


def _raise_control_error(exc: AgentExecutionControlError) -> None:
    raise APIException(status_code=exc.status_code, code=exc.code, message=exc.message) from exc


# Static routes MUST come before /{execution_id}
@agent_executions_router.get("/overview", response_model=AgentExecutionOverviewOut)
async def get_execution_overview(
    session: DBSession,
    _: AuthenticatedOperator,
) -> AgentExecutionOverviewOut:
    rows = list((await session.execute(select(AgentExecution))).scalars().all())
    tool_calls = list((await session.execute(select(AgentToolCall))).scalars().all())
    active = sum(
        1
        for r in rows
        if r.status in ACTIVE_EXECUTION_STATES
        and r.status not in {AgentExecutionStatus.QUEUED, AgentExecutionStatus.WAITING_FOR_APPROVAL}
    )
    queued = sum(1 for r in rows if r.status == AgentExecutionStatus.QUEUED)
    waiting = sum(1 for r in rows if r.status == AgentExecutionStatus.WAITING_FOR_APPROVAL)
    paused = sum(1 for r in rows if r.status == AgentExecutionStatus.PAUSED)
    failed = sum(
        1
        for r in rows
        if r.status in {AgentExecutionStatus.FAILED, AgentExecutionStatus.TIMED_OUT, AgentExecutionStatus.INTERRUPTED}
    )
    completed = sum(1 for r in rows if r.status == AgentExecutionStatus.COMPLETED)
    durations = [r.duration_ms for r in rows if r.duration_ms is not None]
    avg_dur = round(sum(durations) / len(durations), 1) if durations else None
    failed_tools = sum(
        1 for tc in tool_calls if tc.status in {AgentToolCallStatus.FAILED, AgentToolCallStatus.TIMED_OUT}
    )
    tool_fail_rate = round(failed_tools / len(tool_calls), 4) if tool_calls else None
    return AgentExecutionOverviewOut(
        total_executions=len(rows),
        active_executions=active,
        queued_executions=queued,
        waiting_for_approval=waiting,
        paused_executions=paused,
        failed_executions=failed,
        completed_executions=completed,
        average_duration_ms=avg_dur,
        tool_failure_rate=tool_fail_rate,
        total_tool_calls=len(tool_calls),
        failed_tool_calls=failed_tools,
    )


@agent_executions_router.get("/metrics", response_model=AgentExecutionOverviewOut)
async def get_execution_metrics(
    session: DBSession,
    operator: AuthenticatedOperator,
) -> AgentExecutionOverviewOut:
    return await get_execution_overview(session, operator)


@agent_executions_router.get("/stream")
async def stream_all_executions(
    _: AuthenticatedOperator,
) -> StreamingResponse:
    async def event_generator():
        async for evt in sse_bus.subscribe_workspace():
            if str(evt.get("event_type", "")).startswith("execution.") or evt.get("event_type") in {
                "keepalive",
                "workspace.connected",
            }:
                yield format_sse(evt)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@agent_executions_router.post("", response_model=AgentExecutionDetailOut, status_code=201)
async def create_agent_execution(
    body: AgentExecutionCreateRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> AgentExecutionDetailOut:
    exe = await AgentExecutionService.create_execution(
        session,
        agent_type=body.agent_type,
        agent_name=body.agent_name or f"ReleaseGuard {body.agent_type}",
        task_name=body.task_name,
        task_description=body.task_description,
        initiated_by_id=operator.id,
        actor_type=ActorType.USER,
        execution_mode=body.execution_mode,
        priority=body.priority,
        repository_id=body.repository_id,
        pull_request_id=body.pull_request_id,
        release_id=body.release_id,
        experiment_id=body.experiment_id,
        idempotency_key=body.idempotency_key,
    )
    if body.start_immediately and exe.status == AgentExecutionStatus.QUEUED:
        await AgentExecutionService.transition_execution(
            session,
            exe,
            AgentExecutionStatus.INITIALIZING,
            actor_id=operator.id,
            actor_type=ActorType.USER,
            stage="initializing",
            activity="Initializing agent context",
        )
        await AgentExecutionService.transition_execution(
            session,
            exe,
            AgentExecutionStatus.RUNNING,
            actor_id=operator.id,
            actor_type=ActorType.AGENT,
            stage="running",
            activity="Executing task",
        )
    await session.commit()
    return await _build_execution_detail(session, exe)


@agent_executions_router.get("", response_model=PaginatedResponse[AgentExecutionOut])
async def list_agent_executions(
    session: DBSession,
    _: AuthenticatedOperator,
    q: Annotated[str | None, Query(max_length=120)] = None,
    agent_type: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[AgentExecutionStatus | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    release_id: Annotated[uuid.UUID | None, Query()] = None,
    initiated_by_id: Annotated[uuid.UUID | None, Query()] = None,
    failed_only: Annotated[bool, Query()] = False,
    scheduled_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[AgentExecutionOut]:
    base_q = select(AgentExecution)
    count_q = select(func.count(AgentExecution.id))

    if q:
        like = f"%{q.strip()}%"
        cond = or_(
            AgentExecution.execution_key.ilike(like),
            AgentExecution.task_name.ilike(like),
            AgentExecution.agent_name.ilike(like),
        )
        base_q = base_q.where(cond)
        count_q = count_q.where(cond)
    if agent_type:
        base_q = base_q.where(AgentExecution.agent_type == agent_type)
        count_q = count_q.where(AgentExecution.agent_type == agent_type)
    if status is not None:
        base_q = base_q.where(AgentExecution.status == status)
        count_q = count_q.where(AgentExecution.status == status)
    if repository_id is not None:
        base_q = base_q.where(AgentExecution.repository_id == repository_id)
        count_q = count_q.where(AgentExecution.repository_id == repository_id)
    if release_id is not None:
        base_q = base_q.where(AgentExecution.release_id == release_id)
        count_q = count_q.where(AgentExecution.release_id == release_id)
    if initiated_by_id is not None:
        base_q = base_q.where(AgentExecution.initiated_by_id == initiated_by_id)
        count_q = count_q.where(AgentExecution.initiated_by_id == initiated_by_id)
    if failed_only:
        fail_states = [
            AgentExecutionStatus.FAILED,
            AgentExecutionStatus.TIMED_OUT,
            AgentExecutionStatus.INTERRUPTED,
        ]
        base_q = base_q.where(AgentExecution.status.in_(fail_states))
        count_q = count_q.where(AgentExecution.status.in_(fail_states))
    if scheduled_only:
        base_q = base_q.where(AgentExecution.schedule_id.is_not(None))
        count_q = count_q.where(AgentExecution.schedule_id.is_not(None))

    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = (
        await session.execute(
            base_q.order_by(AgentExecution.updated_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return PaginatedResponse[AgentExecutionOut](
        items=[AgentExecutionOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@agent_executions_router.get("/{execution_id}", response_model=AgentExecutionDetailOut)
async def get_agent_execution_detail(
    execution_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> AgentExecutionDetailOut:
    exe = await _load_execution_or_404(session, execution_id)
    return await _build_execution_detail(session, exe)


@agent_executions_router.get("/{execution_id}/summary", response_model=AgentExecutionOut)
async def get_agent_execution_summary(
    execution_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> AgentExecutionOut:
    exe = await _load_execution_or_404(session, execution_id)
    return AgentExecutionOut.model_validate(exe)


@agent_executions_router.get("/{execution_id}/steps", response_model=list[AgentExecutionStepOut])
async def get_agent_execution_steps(
    execution_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> list[AgentExecutionStepOut]:
    await _load_execution_or_404(session, execution_id)
    rows = (
        await session.execute(
            select(AgentExecutionStep)
            .where(AgentExecutionStep.execution_id == execution_id)
            .order_by(AgentExecutionStep.sequence)
        )
    ).scalars().all()
    return [AgentExecutionStepOut.model_validate(r) for r in rows]


@agent_executions_router.get("/{execution_id}/timeline", response_model=list[AgentExecutionTraceOut])
async def get_agent_execution_timeline(
    execution_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
    category: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[AgentExecutionTraceOut]:
    await _load_execution_or_404(session, execution_id)
    stmt = select(AgentExecutionTrace).where(AgentExecutionTrace.execution_id == execution_id)
    if category:
        stmt = stmt.where(AgentExecutionTrace.category == category)
    rows = (
        await session.execute(
            stmt.order_by(AgentExecutionTrace.sequence_number).limit(limit)
        )
    ).scalars().all()
    return [AgentExecutionTraceOut.model_validate(r) for r in rows]


@agent_executions_router.get("/{execution_id}/tools", response_model=list[AgentToolCallOut])
async def get_agent_execution_tools(
    execution_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> list[AgentToolCallOut]:
    await _load_execution_or_404(session, execution_id)
    rows = (
        await session.execute(
            select(AgentToolCall)
            .where(AgentToolCall.execution_id == execution_id)
            .order_by(AgentToolCall.created_at)
        )
    ).scalars().all()
    return [AgentToolCallOut.model_validate(r) for r in rows]


@agent_executions_router.get("/{execution_id}/decisions", response_model=list[AgentDecisionOut])
async def get_agent_execution_decisions(
    execution_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> list[AgentDecisionOut]:
    await _load_execution_or_404(session, execution_id)
    rows = (
        await session.execute(
            select(AgentDecision)
            .where(AgentDecision.execution_id == execution_id)
            .order_by(AgentDecision.created_at)
        )
    ).scalars().all()
    return [AgentDecisionOut.model_validate(r) for r in rows]


@agent_executions_router.get(
    "/{execution_id}/checkpoints", response_model=list[AgentExecutionSnapshotOut]
)
async def get_agent_execution_checkpoints(
    execution_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> list[AgentExecutionSnapshotOut]:
    await _load_execution_or_404(session, execution_id)
    rows = (
        await session.execute(
            select(AgentExecutionSnapshot)
            .where(AgentExecutionSnapshot.execution_id == execution_id)
            .order_by(AgentExecutionSnapshot.checkpoint_number.desc())
        )
    ).scalars().all()
    return [AgentExecutionSnapshotOut.model_validate(r) for r in rows]


@agent_executions_router.post("/{execution_id}/pause", response_model=AgentExecutionDetailOut)
async def pause_agent_execution(
    execution_id: uuid.UUID,
    body: AgentExecutionControlRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> AgentExecutionDetailOut:
    exe = await _load_execution_or_404(session, execution_id)
    try:
        await AgentExecutionService.request_pause(
            session,
            exe,
            actor_id=operator.id,
            expected_version=body.expected_version,
            reason=body.reason,
        )
    except AgentExecutionControlError as exc:
        _raise_control_error(exc)
    await session.commit()
    return await _build_execution_detail(session, exe)


@agent_executions_router.post("/{execution_id}/resume", response_model=AgentExecutionDetailOut)
async def resume_agent_execution(
    execution_id: uuid.UUID,
    body: AgentExecutionControlRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> AgentExecutionDetailOut:
    exe = await _load_execution_or_404(session, execution_id)
    try:
        await AgentExecutionService.resume_execution(
            session,
            exe,
            actor_id=operator.id,
            expected_version=body.expected_version,
        )
    except AgentExecutionControlError as exc:
        _raise_control_error(exc)
    await session.commit()
    return await _build_execution_detail(session, exe)


@agent_executions_router.post("/{execution_id}/cancel", response_model=AgentExecutionDetailOut)
async def cancel_agent_execution(
    execution_id: uuid.UUID,
    body: AgentExecutionControlRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> AgentExecutionDetailOut:
    exe = await _load_execution_or_404(session, execution_id)
    if (
        operator.role not in {UserRole.RELEASE_MANAGER, UserRole.ADMIN}
        and exe.initiated_by_id is not None
        and exe.initiated_by_id != operator.id
    ):
        raise APIException(
            status_code=403,
            code="forbidden_execution_cancel",
            message="Only Release Managers, Admins, or the initiating engineer may cancel this execution.",
        )
    try:
        await AgentExecutionService.request_cancel(
            session,
            exe,
            actor_id=operator.id,
            expected_version=body.expected_version,
            reason=body.reason,
        )
    except AgentExecutionControlError as exc:
        _raise_control_error(exc)
    await session.commit()
    return await _build_execution_detail(session, exe)


@agent_executions_router.post("/{execution_id}/retry", response_model=AgentExecutionDetailOut)
async def retry_agent_execution(
    execution_id: uuid.UUID,
    body: AgentExecutionControlRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> AgentExecutionDetailOut:
    exe = await _load_execution_or_404(session, execution_id)
    try:
        await AgentExecutionService.retry_execution(
            session,
            exe,
            actor_id=operator.id,
            expected_version=body.expected_version,
        )
    except AgentExecutionControlError as exc:
        _raise_control_error(exc)
    await session.commit()
    return await _build_execution_detail(session, exe)


@agent_executions_router.post(
    "/{execution_id}/steps/{step_id}/retry", response_model=AgentExecutionDetailOut
)
async def retry_agent_execution_step(
    execution_id: uuid.UUID,
    step_id: uuid.UUID,
    body: AgentExecutionControlRequest,
    session: DBSession,
    operator: DeveloperOperator,
) -> AgentExecutionDetailOut:
    exe = await _load_execution_or_404(session, execution_id)
    try:
        await AgentExecutionService.retry_execution(
            session,
            exe,
            actor_id=operator.id,
            expected_version=body.expected_version,
            step_id=step_id,
        )
    except AgentExecutionControlError as exc:
        _raise_control_error(exc)
    await session.commit()
    return await _build_execution_detail(session, exe)


@agent_executions_router.get("/{execution_id}/stream")
async def stream_single_execution(
    execution_id: uuid.UUID,
    session: DBSession,
    _: AuthenticatedOperator,
) -> StreamingResponse:
    await _load_execution_or_404(session, execution_id)
    exe_str = str(execution_id)

    async def event_generator():
        async for evt in sse_bus.subscribe_workspace():
            payload: dict[str, Any] = dict(evt.get("payload") or {})
            if payload.get("execution_id") == exe_str or evt.get("event_type") in {
                "keepalive",
                "workspace.connected",
            }:
                yield format_sse(evt)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
