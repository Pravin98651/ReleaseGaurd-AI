"""Agent Command Center Execution State Machine, Controls, Instrumentation, and Recovery."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import ensure_utc, utcnow
from app.core.security import redact_dict, scrub_secrets
from app.models import (
    ActorType,
    AgentDecision,
    AgentDecisionSource,
    AgentDecisionStatus,
    AgentExecution,
    AgentExecutionSnapshot,
    AgentExecutionStatus,
    AgentExecutionStep,
    AgentExecutionTrace,
    AgentStepStatus,
    AgentToolCall,
    AgentToolCallStatus,
    AuditLog,
    AuditStatus,
    NotificationCategory,
    PolicyCategory,
)
from app.services.notification_service import (
    broadcast_workspace_event,
    create_notification,
)

logger = logging.getLogger("releaseguard.command_center")


class AgentExecutionControlError(Exception):
    """Raised on invalid execution transitions, concurrency conflicts, or non-retryable states."""

    def __init__(self, message: str, *, status_code: int = 422, code: str = "execution_control_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


TERMINAL_EXECUTION_STATES: frozenset[AgentExecutionStatus] = frozenset(
    {
        AgentExecutionStatus.COMPLETED,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.CANCELLED,
        AgentExecutionStatus.TIMED_OUT,
    }
)

IN_FLIGHT_WORKER_STATES: frozenset[AgentExecutionStatus] = frozenset(
    {
        AgentExecutionStatus.INITIALIZING,
        AgentExecutionStatus.PLANNING,
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.TOOL_RUNNING,
        AgentExecutionStatus.PAUSE_REQUESTED,
        AgentExecutionStatus.RESUMING,
        AgentExecutionStatus.RETRYING,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.RECOVERING,
    }
)
ACTIVE_EXECUTION_STATES = IN_FLIGHT_WORKER_STATES

NON_RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {"AUTH_FORBIDDEN", "POLICY_DENIED", "INVALID_REQUEST", "UNAUTHORIZED"}
)

ALLOWED_EXECUTION_TRANSITIONS: dict[AgentExecutionStatus, set[AgentExecutionStatus]] = {
    AgentExecutionStatus.QUEUED: {
        AgentExecutionStatus.INITIALIZING,
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.PAUSE_REQUESTED,
        AgentExecutionStatus.PAUSED,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.CANCELLED,
        AgentExecutionStatus.FAILED,
    },
    AgentExecutionStatus.INITIALIZING: {
        AgentExecutionStatus.PLANNING,
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.PAUSE_REQUESTED,
        AgentExecutionStatus.PAUSED,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.CANCELLED,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.INTERRUPTED,
    },
    AgentExecutionStatus.PLANNING: {
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.TOOL_RUNNING,
        AgentExecutionStatus.WAITING_FOR_APPROVAL,
        AgentExecutionStatus.WAITING_FOR_INPUT,
        AgentExecutionStatus.PAUSE_REQUESTED,
        AgentExecutionStatus.PAUSED,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.CANCELLED,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.INTERRUPTED,
    },
    AgentExecutionStatus.RUNNING: {
        AgentExecutionStatus.TOOL_RUNNING,
        AgentExecutionStatus.WAITING_FOR_APPROVAL,
        AgentExecutionStatus.WAITING_FOR_INPUT,
        AgentExecutionStatus.PAUSE_REQUESTED,
        AgentExecutionStatus.PAUSED,
        AgentExecutionStatus.COMPLETED,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.TIMED_OUT,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.CANCELLED,
        AgentExecutionStatus.INTERRUPTED,
    },
    AgentExecutionStatus.TOOL_RUNNING: {
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.WAITING_FOR_APPROVAL,
        AgentExecutionStatus.PAUSE_REQUESTED,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.TIMED_OUT,
        AgentExecutionStatus.INTERRUPTED,
    },
    AgentExecutionStatus.WAITING_FOR_APPROVAL: {
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.RESUMING,
        AgentExecutionStatus.PAUSE_REQUESTED,
        AgentExecutionStatus.PAUSED,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.CANCELLED,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.TIMED_OUT,
    },
    AgentExecutionStatus.WAITING_FOR_INPUT: {
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.RESUMING,
        AgentExecutionStatus.PAUSE_REQUESTED,
        AgentExecutionStatus.PAUSED,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.CANCELLED,
    },
    AgentExecutionStatus.PAUSE_REQUESTED: {
        AgentExecutionStatus.PAUSED,
        AgentExecutionStatus.COMPLETED,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.CANCELLED,
        AgentExecutionStatus.INTERRUPTED,
    },
    AgentExecutionStatus.PAUSED: {
        AgentExecutionStatus.RESUMING,
        AgentExecutionStatus.CANCELLATION_REQUESTED,
        AgentExecutionStatus.CANCELLED,
    },
    AgentExecutionStatus.RESUMING: {
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.WAITING_FOR_APPROVAL,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.INTERRUPTED,
    },
    AgentExecutionStatus.RETRYING: {
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.TOOL_RUNNING,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.INTERRUPTED,
    },
    AgentExecutionStatus.CANCELLATION_REQUESTED: {
        AgentExecutionStatus.CANCELLED,
        AgentExecutionStatus.COMPLETED,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.INTERRUPTED,
    },
    AgentExecutionStatus.INTERRUPTED: {
        AgentExecutionStatus.RECOVERING,
        AgentExecutionStatus.RETRYING,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.CANCELLED,
    },
    AgentExecutionStatus.RECOVERING: {
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.PAUSED,
        AgentExecutionStatus.FAILED,
    },
    AgentExecutionStatus.FAILED: {
        AgentExecutionStatus.RETRYING,
        AgentExecutionStatus.RECOVERING,
    },
    AgentExecutionStatus.TIMED_OUT: {
        AgentExecutionStatus.RETRYING,
    },
    AgentExecutionStatus.COMPLETED: set(),
    AgentExecutionStatus.CANCELLED: set(),
}


def can_transition_execution(
    from_status: AgentExecutionStatus, to_status: AgentExecutionStatus
) -> bool:
    if from_status == to_status:
        return True
    return to_status in ALLOWED_EXECUTION_TRANSITIONS.get(from_status, set())


class AgentExecutionService:
    """Central backend controller for AgentExecution lifecycle, tools, decisions, and recovery."""

    @staticmethod
    async def _next_execution_key(session: AsyncSession) -> str:
        year = utcnow().year
        count = int((await session.execute(select(func.count(AgentExecution.id)))).scalar_one() or 0)
        seq = count + 1
        while True:
            candidate = f"EXE-{year}-{seq:04d}"
            exists = (
                await session.execute(
                    select(AgentExecution.id).where(AgentExecution.execution_key == candidate).limit(1)
                )
            ).scalar_one_or_none()
            if exists is None:
                return candidate
            seq += 1

    @staticmethod
    async def _next_trace_seq(session: AsyncSession, execution_id: uuid.UUID) -> int:
        max_seq = (
            await session.execute(
                select(func.max(AgentExecutionTrace.sequence_number)).where(
                    AgentExecutionTrace.execution_id == execution_id
                )
            )
        ).scalar_one()
        return int(max_seq or 0) + 1

    @staticmethod
    async def append_trace(
        session: AsyncSession,
        execution: AgentExecution,
        *,
        event_type: str,
        message: str,
        category: str = "lifecycle",
        stage: str | None = None,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentExecutionTrace:
        seq = await AgentExecutionService._next_trace_seq(session, execution.id)
        trace = AgentExecutionTrace(
            execution_id=execution.id,
            sequence_number=seq,
            event_type=event_type,
            category=category,
            stage=stage or execution.current_stage,
            actor_type=actor_type,
            actor_id=actor_id,
            message=scrub_secrets(message),
            payload=redact_dict(dict(payload or {})),
            correlation_id=execution.correlation_id,
        )
        session.add(trace)
        await session.flush()
        return trace

    @staticmethod
    async def create_execution(
        session: AsyncSession,
        *,
        agent_type: str,
        agent_name: str,
        task_name: str,
        task_description: str | None = None,
        initiated_by_id: uuid.UUID | None = None,
        actor_type: ActorType = ActorType.USER,
        execution_mode: str = "interactive",
        priority: int = 5,
        agent_task_id: uuid.UUID | None = None,
        repository_id: uuid.UUID | None = None,
        pull_request_id: uuid.UUID | None = None,
        release_id: uuid.UUID | None = None,
        experiment_id: uuid.UUID | None = None,
        schedule_id: uuid.UUID | None = None,
        parent_execution_id: uuid.UUID | None = None,
        planned_steps: list[dict[str, str]] | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentExecution:
        if idempotency_key:
            existing = (
                await session.execute(
                    select(AgentExecution)
                    .where(AgentExecution.idempotency_key == idempotency_key)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

        exe_key = await AgentExecutionService._next_execution_key(session)
        corr_id = f"corr_{uuid.uuid4().hex[:16]}"
        exe = AgentExecution(
            execution_key=exe_key,
            agent_type=agent_type,
            agent_name=agent_name,
            task_name=scrub_secrets(task_name)[:255],
            task_description=scrub_secrets(task_description) if task_description else None,
            status=AgentExecutionStatus.QUEUED,
            priority=priority,
            execution_mode=execution_mode,
            initiated_by_id=initiated_by_id,
            actor_type=actor_type,
            agent_task_id=agent_task_id,
            repository_id=repository_id,
            pull_request_id=pull_request_id,
            release_id=release_id,
            experiment_id=experiment_id,
            schedule_id=schedule_id,
            parent_execution_id=parent_execution_id,
            current_stage="queued",
            current_activity="Execution queued",
            total_steps=len(planned_steps) if planned_steps else None,
            last_heartbeat_at=utcnow(),
            correlation_id=corr_id,
            idempotency_key=idempotency_key,
            execution_metadata=redact_dict(dict(metadata or {})),
        )
        session.add(exe)
        await session.flush()

        if planned_steps:
            for idx, st in enumerate(planned_steps, start=1):
                session.add(
                    AgentExecutionStep(
                        execution_id=exe.id,
                        step_key=st["step_key"],
                        step_name=st["step_name"],
                        step_type=st.get("step_type", "stage"),
                        sequence=idx,
                        status=AgentStepStatus.PENDING,
                        description=st.get("description"),
                    )
                )
            await session.flush()

        await AgentExecutionService.append_trace(
            session,
            exe,
            event_type="execution.created",
            category="lifecycle",
            stage="queued",
            actor_type=actor_type,
            actor_id=initiated_by_id,
            message=f"{agent_name} queued task: {exe.task_name}",
            payload={"execution_key": exe.execution_key, "agent_type": agent_type},
        )
        await broadcast_workspace_event(
            "execution.created",
            {
                "execution_id": str(exe.id),
                "execution_key": exe.execution_key,
                "status": exe.status.value,
                "agent_type": exe.agent_type,
                "release_id": str(release_id) if release_id else None,
            },
        )
        return exe

    @staticmethod
    async def transition_execution(
        session: AsyncSession,
        execution: AgentExecution,
        target_status: AgentExecutionStatus,
        *,
        expected_version: int | None = None,
        actor_id: uuid.UUID | None = None,
        actor_type: ActorType = ActorType.SYSTEM,
        stage: str | None = None,
        activity: str | None = None,
        event_type: str | None = None,
        category: str = "lifecycle",
        message: str | None = None,
        payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentExecution:
        if expected_version is not None and execution.version != expected_version:
            raise AgentExecutionControlError(
                f"Version conflict on execution {execution.execution_key}: "
                f"expected v{expected_version}, current v{execution.version}.",
                status_code=409,
                code="execution_version_conflict",
            )

        from_status = execution.status
        if from_status == target_status:
            return execution

        if not can_transition_execution(from_status, target_status):
            raise AgentExecutionControlError(
                f"Invalid execution transition for {execution.execution_key}: "
                f"'{from_status.value}' -> '{target_status.value}'.",
                status_code=422,
                code="execution_invalid_transition",
            )

        now = utcnow()
        if target_status in {AgentExecutionStatus.PAUSED, AgentExecutionStatus.PAUSE_REQUESTED}:
            if from_status not in {AgentExecutionStatus.PAUSED, AgentExecutionStatus.PAUSE_REQUESTED}:
                execution.previous_status = from_status
        elif target_status == AgentExecutionStatus.RUNNING and from_status in {
            AgentExecutionStatus.RESUMING,
            AgentExecutionStatus.PAUSED,
        }:
            execution.previous_status = None
            execution.pause_requested = False

        if target_status in {
            AgentExecutionStatus.INITIALIZING,
            AgentExecutionStatus.PLANNING,
            AgentExecutionStatus.RUNNING,
        } and execution.started_at is None:
            execution.started_at = now

        if target_status in TERMINAL_EXECUTION_STATES:
            execution.completed_at = now
            start_dt = ensure_utc(execution.started_at) or ensure_utc(execution.created_at)
            if start_dt is not None:
                execution.duration_ms = max(0, int((now - start_dt).total_seconds() * 1000))

        if error_code is not None:
            execution.error_code = error_code
        if error_message is not None:
            execution.error_message = scrub_secrets(error_message)
        elif target_status == AgentExecutionStatus.RUNNING:
            execution.error_code = None
            execution.error_message = None

        if stage is not None:
            execution.current_stage = stage
        if activity is not None:
            execution.current_activity = scrub_secrets(activity)[:255]

        execution.status = target_status
        execution.last_heartbeat_at = now
        execution.version += 1
        execution.updated_at = now

        evt_type = event_type or f"execution.{target_status.value}"
        evt_msg = message or f"Status transitioned: {from_status.value} -> {target_status.value}"
        await AgentExecutionService.append_trace(
            session,
            execution,
            event_type=evt_type,
            category=category,
            stage=execution.current_stage,
            actor_type=actor_type,
            actor_id=actor_id,
            message=evt_msg,
            payload={
                **(payload or {}),
                "from_status": from_status.value,
                "to_status": target_status.value,
                "version": execution.version,
            },
        )

        if target_status in {
            AgentExecutionStatus.PAUSED,
            AgentExecutionStatus.CANCELLED,
            AgentExecutionStatus.FAILED,
            AgentExecutionStatus.COMPLETED,
            AgentExecutionStatus.RETRYING,
            AgentExecutionStatus.RECOVERING,
        }:
            session.add(
                AuditLog(
                    actor_type=actor_type,
                    actor_id=str(actor_id) if actor_id else None,
                    policy_category=PolicyCategory.A,
                    event_action=f"agent_execution.{target_status.value}",
                    resource_type="agent_execution",
                    resource_id=str(execution.id),
                    status=AuditStatus.FAILED
                    if target_status == AgentExecutionStatus.FAILED
                    else AuditStatus.SUCCESS,
                    details=redact_dict(
                        {
                            "execution_key": execution.execution_key,
                            "from_status": from_status.value,
                            "to_status": target_status.value,
                            "version": execution.version,
                        }
                    ),
                )
            )

        await broadcast_workspace_event(
            "execution.updated",
            {
                "execution_id": str(execution.id),
                "execution_key": execution.execution_key,
                "status": execution.status.value,
                "current_stage": execution.current_stage,
                "release_id": str(execution.release_id) if execution.release_id else None,
                "version": execution.version,
            },
        )
        return execution

    @staticmethod
    async def start_step(
        session: AsyncSession,
        execution: AgentExecution,
        *,
        step_key: str,
        step_name: str,
        step_type: str = "stage",
        input_summary: dict[str, Any] | None = None,
    ) -> AgentExecutionStep:
        step = (
            await session.execute(
                select(AgentExecutionStep).where(
                    AgentExecutionStep.execution_id == execution.id,
                    AgentExecutionStep.step_key == step_key,
                )
            )
        ).scalar_one_or_none()
        now = utcnow()
        if step is None:
            max_seq = (
                await session.execute(
                    select(func.max(AgentExecutionStep.sequence)).where(
                        AgentExecutionStep.execution_id == execution.id
                    )
                )
            ).scalar_one()
            step = AgentExecutionStep(
                execution_id=execution.id,
                step_key=step_key,
                step_name=step_name,
                step_type=step_type,
                sequence=int(max_seq or 0) + 1,
            )
            session.add(step)

        step.status = AgentStepStatus.RUNNING
        step.started_at = step.started_at or now
        step.error_details = None
        if input_summary:
            step.input_summary = redact_dict(input_summary)
        execution.current_stage = step_key
        execution.current_activity = step_name
        execution.last_heartbeat_at = now
        await session.flush()

        await AgentExecutionService.append_trace(
            session,
            execution,
            event_type="stage.started",
            category="stage",
            stage=step_key,
            actor_type=ActorType.AGENT,
            message=f"Started stage: {step_name}",
        )
        return step

    @staticmethod
    async def complete_step(
        session: AsyncSession,
        execution: AgentExecution,
        step: AgentExecutionStep,
        *,
        output_summary: dict[str, Any] | None = None,
    ) -> AgentExecutionStep:
        now = utcnow()
        step.status = AgentStepStatus.COMPLETED
        step.completed_at = now
        s_at = ensure_utc(step.started_at)
        if s_at is not None:
            step.duration_ms = max(0, int((now - s_at).total_seconds() * 1000))
        if output_summary:
            step.output_summary = redact_dict(output_summary)

        done_count = int(
            (
                await session.execute(
                    select(func.count(AgentExecutionStep.id)).where(
                        AgentExecutionStep.execution_id == execution.id,
                        AgentExecutionStep.status == AgentStepStatus.COMPLETED,
                    )
                )
            ).scalar_one()
            or 0
        )
        execution.completed_steps = done_count
        execution.last_heartbeat_at = now
        await session.flush()

        await AgentExecutionService.append_trace(
            session,
            execution,
            event_type="stage.completed",
            category="stage",
            stage=step.step_key,
            actor_type=ActorType.AGENT,
            message=f"Completed stage: {step.step_name}",
            payload={"duration_ms": step.duration_ms},
        )
        return step

    @staticmethod
    async def record_tool_start(
        session: AsyncSession,
        execution: AgentExecution,
        *,
        tool_name: str,
        tool_category: str = "domain",
        step_id: uuid.UUID | None = None,
        is_reversible: bool = True,
        input_summary: dict[str, Any] | None = None,
    ) -> AgentToolCall:
        now = utcnow()
        call = AgentToolCall(
            execution_id=execution.id,
            step_id=step_id,
            tool_name=tool_name,
            tool_category=tool_category,
            invocation_id=f"inv_{uuid.uuid4().hex[:12]}",
            status=AgentToolCallStatus.RUNNING,
            is_reversible=is_reversible,
            input_summary=redact_dict(dict(input_summary or {})),
            started_at=now,
        )
        session.add(call)
        await session.flush()

        if execution.status == AgentExecutionStatus.RUNNING:
            await AgentExecutionService.transition_execution(
                session,
                execution,
                AgentExecutionStatus.TOOL_RUNNING,
                actor_type=ActorType.AGENT,
                activity=f"Running tool: {tool_name}",
                event_type="tool.started",
                category="tool",
                message=f"Invoked tool {tool_name}",
                payload={"tool_call_id": str(call.id), "tool_name": tool_name},
            )
        else:
            await AgentExecutionService.append_trace(
                session,
                execution,
                event_type="tool.started",
                category="tool",
                actor_type=ActorType.AGENT,
                message=f"Invoked tool {tool_name}",
                payload={"tool_call_id": str(call.id), "tool_name": tool_name},
            )
        return call

    @staticmethod
    async def record_tool_result(
        session: AsyncSession,
        execution: AgentExecution,
        call: AgentToolCall,
        *,
        success: bool,
        output_summary: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentToolCall:
        now = utcnow()
        call.status = AgentToolCallStatus.COMPLETED if success else AgentToolCallStatus.FAILED
        call.completed_at = now
        s_at = ensure_utc(call.started_at)
        if s_at is not None:
            call.duration_ms = max(0, int((now - s_at).total_seconds() * 1000))
        if output_summary:
            call.output_summary = redact_dict(output_summary)
        if error_code:
            call.error_code = error_code
        if error_message:
            call.error_message = scrub_secrets(error_message)
        await session.flush()

        await AgentExecutionService.append_trace(
            session,
            execution,
            event_type="tool.completed" if success else "tool.failed",
            category="tool" if success else "error",
            actor_type=ActorType.AGENT,
            message=(
                f"Tool {call.tool_name} completed ({call.duration_ms}ms)"
                if success
                else f"Tool {call.tool_name} failed: {call.error_message or call.error_code}"
            ),
            payload={
                "tool_call_id": str(call.id),
                "tool_name": call.tool_name,
                "duration_ms": call.duration_ms,
                "error_code": call.error_code,
            },
        )

        if execution.status in {
            AgentExecutionStatus.TOOL_RUNNING,
            AgentExecutionStatus.PAUSE_REQUESTED,
            AgentExecutionStatus.CANCELLATION_REQUESTED,
        }:
            if execution.cancel_requested:
                await AgentExecutionService.transition_execution(
                    session,
                    execution,
                    AgentExecutionStatus.CANCELLED,
                    event_type="execution.cancelled",
                    message="Cancelled safely after active tool completion.",
                )
            elif execution.pause_requested:
                if execution.status != AgentExecutionStatus.PAUSE_REQUESTED:
                    await AgentExecutionService.transition_execution(
                        session,
                        execution,
                        AgentExecutionStatus.PAUSE_REQUESTED,
                    )
                await AgentExecutionService.transition_execution(
                    session,
                    execution,
                    AgentExecutionStatus.PAUSED,
                    event_type="execution.paused",
                    message="Paused at safe checkpoint after tool completion.",
                )
                await AgentExecutionService.create_checkpoint(
                    session,
                    execution,
                    checkpoint_type="pause_checkpoint",
                    pending_operation=f"After tool {call.tool_name}",
                )
            else:
                await AgentExecutionService.transition_execution(
                    session,
                    execution,
                    AgentExecutionStatus.RUNNING,
                    actor_type=ActorType.AGENT,
                    activity=f"Completed tool: {call.tool_name}",
                )

        if not success:
            await create_notification(
                session,
                category=NotificationCategory.AGENT_TOOL_FAILED,
                severity="warning",
                title=f"Tool Failed: {call.tool_name} ({execution.execution_key})",
                message=call.error_message or "External tool invocation failed.",
                resource_type="agent_execution",
                resource_id=str(execution.id),
                action_href=f"/command-center/{execution.id}",
            )
        return call

    @staticmethod
    async def record_decision(
        session: AsyncSession,
        execution: AgentExecution,
        *,
        decision_type: str,
        decision_source: AgentDecisionSource,
        decision_summary: str,
        recommendation: str | None = None,
        confidence: float | None = None,
        decision_status: AgentDecisionStatus = AgentDecisionStatus.PENDING,
        supporting_evidence: list[str] | None = None,
        evidence_references: list[dict[str, Any]] | None = None,
        model_name: str | None = None,
        step_id: uuid.UUID | None = None,
    ) -> AgentDecision:
        dec = AgentDecision(
            execution_id=execution.id,
            step_id=step_id,
            decision_type=decision_type,
            decision_source=decision_source,
            decision_summary=scrub_secrets(decision_summary),
            recommendation=scrub_secrets(recommendation) if recommendation else None,
            confidence=confidence,
            decision_status=decision_status,
            supporting_evidence=[scrub_secrets(e) for e in (supporting_evidence or [])],
            evidence_references=[redact_dict(ref) for ref in (evidence_references or [])],
            model_name=model_name,
        )
        session.add(dec)
        await session.flush()

        await AgentExecutionService.append_trace(
            session,
            execution,
            event_type="decision.created",
            category="decision",
            actor_type=ActorType.AGENT,
            message=f"Decision ({decision_source.value}): {dec.decision_summary[:180]}",
            payload={"decision_id": str(dec.id), "decision_type": decision_type},
        )
        await broadcast_workspace_event(
            "execution.decision",
            {"execution_id": str(execution.id), "decision_id": str(dec.id)},
        )
        return dec

    @staticmethod
    async def create_checkpoint(
        session: AsyncSession,
        execution: AgentExecution,
        *,
        checkpoint_type: str = "safe_point",
        pending_operation: str | None = None,
        context_reference: dict[str, Any] | None = None,
    ) -> AgentExecutionSnapshot:
        max_num = (
            await session.execute(
                select(func.max(AgentExecutionSnapshot.checkpoint_number)).where(
                    AgentExecutionSnapshot.execution_id == execution.id
                )
            )
        ).scalar_one()
        completed_ids = [
            str(sid)
            for sid in (
                await session.execute(
                    select(AgentExecutionStep.id).where(
                        AgentExecutionStep.execution_id == execution.id,
                        AgentExecutionStep.status == AgentStepStatus.COMPLETED,
                    )
                )
            )
            .scalars()
            .all()
        ]
        snap = AgentExecutionSnapshot(
            execution_id=execution.id,
            checkpoint_number=int(max_num or 0) + 1,
            checkpoint_type=checkpoint_type,
            resumable_state=execution.status,
            completed_step_ids=completed_ids,
            pending_operation=pending_operation,
            context_reference=redact_dict(dict(context_reference or {})),
        )
        session.add(snap)
        await session.flush()
        return snap

    @staticmethod
    async def request_pause(
        session: AsyncSession,
        execution: AgentExecution,
        *,
        actor_id: uuid.UUID | None = None,
        expected_version: int | None = None,
        reason: str | None = None,
    ) -> AgentExecution:
        if expected_version is not None and execution.version != expected_version:
            raise AgentExecutionControlError(
                f"Version conflict on execution {execution.execution_key}.",
                status_code=409,
                code="execution_version_conflict",
            )
        if execution.status in TERMINAL_EXECUTION_STATES:
            raise AgentExecutionControlError(
                f"Cannot pause terminal execution {execution.execution_key} ({execution.status.value}).",
                status_code=422,
            )
        if execution.status == AgentExecutionStatus.PAUSED:
            return execution

        execution.pause_requested = True
        if execution.status == AgentExecutionStatus.TOOL_RUNNING:
            return await AgentExecutionService.transition_execution(
                session,
                execution,
                AgentExecutionStatus.PAUSE_REQUESTED,
                actor_id=actor_id,
                actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
                activity="Pause requested — waiting for safe checkpoint",
                event_type="execution.pause_requested",
                message=reason or "Pause requested while tool is running; waiting for safe checkpoint.",
            )

        await AgentExecutionService.transition_execution(
            session,
            execution,
            AgentExecutionStatus.PAUSE_REQUESTED,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            event_type="execution.pause_requested",
            message=reason or "Pause requested by operator.",
        )
        await AgentExecutionService.transition_execution(
            session,
            execution,
            AgentExecutionStatus.PAUSED,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            activity="Paused at safe checkpoint",
            event_type="execution.paused",
            message=reason or "Execution paused safely.",
        )
        await AgentExecutionService.create_checkpoint(
            session, execution, checkpoint_type="operator_pause"
        )
        await create_notification(
            session,
            category=NotificationCategory.AGENT_PAUSED,
            severity="info",
            title=f"Agent Paused: {execution.execution_key}",
            message=reason or f"{execution.agent_name} paused at stage '{execution.current_stage}'.",
            resource_type="agent_execution",
            resource_id=str(execution.id),
            action_href=f"/command-center/{execution.id}",
        )
        return execution

    @staticmethod
    async def resume_execution(
        session: AsyncSession,
        execution: AgentExecution,
        *,
        actor_id: uuid.UUID | None = None,
        expected_version: int | None = None,
    ) -> AgentExecution:
        if expected_version is not None and execution.version != expected_version:
            raise AgentExecutionControlError(
                f"Version conflict on execution {execution.execution_key}.",
                status_code=409,
                code="execution_version_conflict",
            )
        if execution.status not in {
            AgentExecutionStatus.PAUSED,
            AgentExecutionStatus.WAITING_FOR_INPUT,
        }:
            raise AgentExecutionControlError(
                f"Execution {execution.execution_key} cannot be resumed from '{execution.status.value}'.",
                status_code=422,
            )

        target = execution.previous_status or AgentExecutionStatus.RUNNING
        if target in {
            AgentExecutionStatus.PAUSED,
            AgentExecutionStatus.PAUSE_REQUESTED,
            AgentExecutionStatus.TOOL_RUNNING,
            *TERMINAL_EXECUTION_STATES,
        }:
            target = AgentExecutionStatus.RUNNING

        await AgentExecutionService.transition_execution(
            session,
            execution,
            AgentExecutionStatus.RESUMING,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            activity="Restoring execution checkpoint",
            event_type="execution.resuming",
            message="Restoring last safe checkpoint.",
        )
        await AgentExecutionService.transition_execution(
            session,
            execution,
            target,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            activity="Execution resumed",
            event_type="execution.resumed",
            message=f"Resumed execution from checkpoint -> {target.value}.",
        )
        await create_notification(
            session,
            category=NotificationCategory.AGENT_RESUMED,
            severity="info",
            title=f"Agent Resumed: {execution.execution_key}",
            message=f"{execution.agent_name} resumed from checkpoint.",
            resource_type="agent_execution",
            resource_id=str(execution.id),
            action_href=f"/command-center/{execution.id}",
        )
        return execution

    @staticmethod
    async def request_cancel(
        session: AsyncSession,
        execution: AgentExecution,
        *,
        actor_id: uuid.UUID | None = None,
        expected_version: int | None = None,
        reason: str | None = None,
    ) -> AgentExecution:
        if expected_version is not None and execution.version != expected_version:
            raise AgentExecutionControlError(
                f"Version conflict on execution {execution.execution_key}.",
                status_code=409,
                code="execution_version_conflict",
            )
        if execution.status in TERMINAL_EXECUTION_STATES:
            raise AgentExecutionControlError(
                f"Execution {execution.execution_key} is already terminal ({execution.status.value}).",
                status_code=422,
            )

        execution.cancel_requested = True
        if execution.status == AgentExecutionStatus.TOOL_RUNNING:
            return await AgentExecutionService.transition_execution(
                session,
                execution,
                AgentExecutionStatus.CANCELLATION_REQUESTED,
                actor_id=actor_id,
                actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
                activity="Cancellation requested — finishing active tool",
                event_type="execution.cancellation_requested",
                message=reason or "Cancellation requested while tool is running.",
            )

        await AgentExecutionService.transition_execution(
            session,
            execution,
            AgentExecutionStatus.CANCELLATION_REQUESTED,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            event_type="execution.cancellation_requested",
            message=reason or "Cancellation requested by operator.",
        )
        return await AgentExecutionService.transition_execution(
            session,
            execution,
            AgentExecutionStatus.CANCELLED,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            activity="Execution cancelled",
            event_type="execution.cancelled",
            message=reason or "Execution cancelled safely.",
        )

    @staticmethod
    async def retry_execution(
        session: AsyncSession,
        execution: AgentExecution,
        *,
        actor_id: uuid.UUID | None = None,
        expected_version: int | None = None,
        step_id: uuid.UUID | None = None,
    ) -> AgentExecution:
        if expected_version is not None and execution.version != expected_version:
            raise AgentExecutionControlError(
                f"Version conflict on execution {execution.execution_key}.",
                status_code=409,
                code="execution_version_conflict",
            )
        if execution.status not in {
            AgentExecutionStatus.FAILED,
            AgentExecutionStatus.TIMED_OUT,
            AgentExecutionStatus.INTERRUPTED,
        }:
            raise AgentExecutionControlError(
                f"Execution {execution.execution_key} is '{execution.status.value}' and cannot be retried.",
                status_code=422,
            )
        if execution.error_code in NON_RETRYABLE_ERROR_CODES:
            raise AgentExecutionControlError(
                f"Error '{execution.error_code}' is non-retryable without fixing authorization or inputs.",
                status_code=422,
                code="execution_non_retryable",
            )
        if execution.retry_count >= execution.max_retries:
            raise AgentExecutionControlError(
                f"Retry limit reached ({execution.retry_count}/{execution.max_retries}).",
                status_code=422,
                code="execution_retry_limit_exceeded",
            )

        execution.retry_count += 1
        if step_id is not None:
            step = await session.get(AgentExecutionStep, step_id)
            if step is None or step.execution_id != execution.id:
                raise AgentExecutionControlError("Execution step not found.", status_code=404)
            step.status = AgentStepStatus.PENDING
            step.retry_count += 1
            step.error_details = None

        await AgentExecutionService.transition_execution(
            session,
            execution,
            AgentExecutionStatus.RETRYING,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            activity=f"Retrying (attempt {execution.retry_count}/{execution.max_retries})",
            event_type="execution.retrying",
            message=f"Retry #{execution.retry_count} initiated.",
        )
        return await AgentExecutionService.transition_execution(
            session,
            execution,
            AgentExecutionStatus.RUNNING,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            activity="Execution running after retry",
            event_type="execution.resumed",
        )

    @staticmethod
    async def recover_interrupted_executions(session: AsyncSession) -> int:
        """Mark active in-flight executions as INTERRUPTED on backend restart."""
        stuck = list(
            (
                await session.execute(
                    select(AgentExecution).where(
                        AgentExecution.status.in_(list(IN_FLIGHT_WORKER_STATES))
                    )
                )
            )
            .scalars()
            .all()
        )
        recovered = 0
        for exe in stuck:
            await AgentExecutionService.transition_execution(
                session,
                exe,
                AgentExecutionStatus.INTERRUPTED,
                actor_type=ActorType.SYSTEM,
                activity="Interrupted by server restart",
                event_type="execution.interrupted",
                message="Execution interrupted during server restart; checkpoint preserved for recovery.",
                error_code="SERVER_RESTART",
                error_message="Interrupted by backend restart.",
            )
            await create_notification(
                session,
                category=NotificationCategory.AGENT_RECOVERED,
                severity="warning",
                title=f"Execution Interrupted: {exe.execution_key}",
                message="Backend restarted while execution was active. Ready for safe retry.",
                resource_type="agent_execution",
                resource_id=str(exe.id),
                action_href=f"/command-center/{exe.id}",
            )
            recovered += 1
        if recovered:
            await session.commit()
        return recovered
