"""Agent Workspace sessions, async task dispatch, SSE streaming, and polling replay endpoints."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    DBSession,
    DefaultOperator,
    build_supervisor,
    resolve_session_factory,
)
from app.api.v1.errors import APIException
from app.core.database import utcnow
from app.models import (
    AgentSession,
    AgentSessionStatus,
    AgentTask,
    AgentTaskStatus,
    Approval,
    ApprovalStatus,
    SpecialistRole,
)
from app.schemas import (
    AgentMessageCreate,
    AgentSessionCreate,
    AgentSessionDetailOut,
    AgentSessionOut,
    AgentStepLogItem,
    AgentTaskDispatchResponse,
    AgentTaskOut,
    ApprovalOut,
)
from app.services.policy_engine import PolicyEngine
from app.services.sse_bus import format_sse, sse_bus

agent_router = APIRouter(prefix="/agent", tags=["agent"])


def _track_background_task(app: Any, coro: Any) -> None:
    tasks: set[asyncio.Task[Any]] | None = getattr(app.state, "background_tasks", None)
    if tasks is None:
        tasks = set()
        app.state.background_tasks = tasks
    task = asyncio.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _task_to_out(session: AsyncSession, task: AgentTask, after_seq: int | None = None) -> AgentTaskOut:
    raw_steps = list(task.steps_log or [])
    if after_seq is not None:
        raw_steps = [s for s in raw_steps if int(s.get("seq", 0)) > after_seq]
    steps = [AgentStepLogItem.model_validate(s) for s in raw_steps]

    app_stmt = (
        select(Approval)
        .where(
            Approval.agent_task_id == task.id,
            Approval.status == ApprovalStatus.PENDING,
        )
        .order_by(Approval.created_at.asc())
    )
    pending_apps = (await session.execute(app_stmt)).scalars().all()
    return AgentTaskOut(
        id=task.id,
        session_id=task.session_id,
        specialist=task.specialist,
        user_prompt=task.user_prompt,
        status=task.status,
        steps_log=steps,
        final_response=task.final_response,
        error_message=task.error_message,
        gemini_model=task.gemini_model,
        token_usage=dict(task.token_usage or {}),
        started_at=task.started_at,
        completed_at=task.completed_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        pending_approvals=[ApprovalOut.model_validate(a) for a in pending_apps],
    )


@agent_router.get("/sessions", response_model=list[AgentSessionOut])
async def list_agent_sessions(
    session: DBSession,
) -> list[AgentSessionOut]:
    """List agent conversation sessions ordered by most recently updated."""
    rows = (
        (
            await session.execute(
                select(AgentSession).order_by(AgentSession.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [AgentSessionOut.model_validate(r) for r in rows]


@agent_router.post("/sessions", response_model=AgentSessionOut, status_code=201)
async def create_agent_session(
    body: AgentSessionCreate,
    session: DBSession,
    operator: DefaultOperator,
) -> AgentSessionOut:
    """Create a new LangGraph-backed agent session with bound context_scope."""
    agent_sess = AgentSession(
        user_id=operator.id,
        title=body.title,
        thread_id=f"rg-{uuid.uuid4().hex}",
        active_specialist=SpecialistRole.SUPERVISOR,
        status=AgentSessionStatus.ACTIVE,
        context_scope=body.context_scope.model_dump(mode="json", exclude_none=True),
        message_history=[],
    )
    session.add(agent_sess)
    await session.commit()
    await session.refresh(agent_sess)
    return AgentSessionOut.model_validate(agent_sess)


@agent_router.get("/sessions/{session_id}", response_model=AgentSessionDetailOut)
async def get_agent_session(
    session_id: uuid.UUID,
    session: DBSession,
) -> AgentSessionDetailOut:
    """Return full agent session details, chat history, and tasks."""
    agent_sess = await session.get(AgentSession, session_id)
    if agent_sess is None:
        raise APIException(404, "AGENT_SESSION_NOT_FOUND", f"Session {session_id} not found.")

    task_rows = (
        (
            await session.execute(
                select(AgentTask)
                .where(AgentTask.session_id == agent_sess.id)
                .order_by(AgentTask.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    tasks_out = [await _task_to_out(session, t) for t in task_rows]
    base = AgentSessionOut.model_validate(agent_sess).model_dump()
    return AgentSessionDetailOut(**base, tasks=tasks_out)


@agent_router.post(
    "/sessions/{session_id}/messages",
    response_model=AgentTaskDispatchResponse,
    status_code=202,
)
async def send_agent_message(
    session_id: uuid.UUID,
    body: AgentMessageCreate,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> AgentTaskDispatchResponse:
    """Queue an AgentTask and launch async LangGraph execution."""
    agent_sess = await session.get(AgentSession, session_id)
    if agent_sess is None:
        raise APIException(404, "AGENT_SESSION_NOT_FOUND", f"Session {session_id} not found.")

    ws = await PolicyEngine.get_workspace_settings(session)
    specialist = body.specialist_hint or SpecialistRole.SUPERVISOR
    task = AgentTask(
        session_id=agent_sess.id,
        specialist=specialist,
        user_prompt=body.content,
        status=AgentTaskStatus.QUEUED,
        gemini_model=ws.gemini_model,
    )
    session.add(task)
    await session.flush()

    history = list(agent_sess.message_history or [])
    history.append(
        {
            "role": "user",
            "content": body.content,
            "timestamp": utcnow().isoformat(),
            "task_id": str(task.id),
        }
    )
    agent_sess.message_history = history
    agent_sess.updated_at = utcnow()
    await session.commit()
    await session.refresh(task)

    supervisor = build_supervisor(request.app, model_override=ws.gemini_model)
    hint = (
        body.specialist_hint.value
        if body.specialist_hint and body.specialist_hint != SpecialistRole.SUPERVISOR
        else None
    )

    _track_background_task(
        request.app,
        supervisor.run_task(
            task_id=task.id,
            session_id=agent_sess.id,
            thread_id=agent_sess.thread_id,
            user_prompt=body.content,
            specialist_hint=hint,
            context_scope=dict(agent_sess.context_scope or {}),
            user_id=operator.id,
        ),
    )

    return AgentTaskDispatchResponse(
        task_id=task.id,
        session_id=agent_sess.id,
        stream_url=f"/api/v1/agent/tasks/{task.id}/stream",
    )


@agent_router.get("/tasks/{task_id}", response_model=AgentTaskOut)
async def get_agent_task(
    task_id: uuid.UUID,
    session: DBSession,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
) -> AgentTaskOut:
    """Polling fallback endpoint returning steps_log after `after_seq` and pending approvals."""
    task = await session.get(AgentTask, task_id)
    if task is None:
        raise APIException(404, "AGENT_TASK_NOT_FOUND", f"Agent task {task_id} not found.")
    return await _task_to_out(session, task, after_seq=after_seq)


@agent_router.post("/tasks/{task_id}/cancel", response_model=AgentTaskOut)
async def cancel_agent_task(
    task_id: uuid.UUID,
    session: DBSession,
) -> AgentTaskOut:
    """Cancel an active or awaiting-approval agent task and void its pending approvals."""
    task = await session.get(AgentTask, task_id)
    if task is None:
        raise APIException(404, "AGENT_TASK_NOT_FOUND", f"Agent task {task_id} not found.")

    if task.status in (AgentTaskStatus.QUEUED, AgentTaskStatus.RUNNING, AgentTaskStatus.AWAITING_APPROVAL):
        task.status = AgentTaskStatus.CANCELLED
        task.completed_at = utcnow()
        task.updated_at = utcnow()
        pending = (
            (
                await session.execute(
                    select(Approval).where(
                        Approval.agent_task_id == task.id,
                        Approval.status == ApprovalStatus.PENDING,
                    )
                )
            )
            .scalars()
            .all()
        )
        for app in pending:
            app.status = ApprovalStatus.CANCELLED
            app.updated_at = utcnow()
        await sse_bus.publish(
            task.id,
            "task.cancelled",
            {"task_id": str(task.id)},
            session=session,
            commit=True,
        )
        await session.refresh(task)

    return await _task_to_out(session, task)


@agent_router.get("/tasks/{task_id}/stream")
async def stream_agent_task(
    task_id: uuid.UUID,
    request: Request,
    session: DBSession,
    after_seq: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """Stream real-time agent events as SSE (`text/event-stream`) with DB replay."""
    task = await session.get(AgentTask, task_id)
    if task is None:
        raise APIException(404, "AGENT_TASK_NOT_FOUND", f"Agent task {task_id} not found.")

    factory = resolve_session_factory(request.app)

    async def event_generator() -> Any:
        async with factory() as replay_session:
            async for event in sse_bus.subscribe(
                task_id,
                after_seq=after_seq,
                session=replay_session,
                heartbeat_seconds=15.0,
                stop_on_terminal=True,
            ):
                if await request.is_disconnected():
                    break
                wire_payload: dict[str, Any] = {
                    "seq": event.get("seq"),
                    "event_type": event.get("event_type"),
                    "timestamp": event.get("timestamp"),
                    **(event.get("payload") or {}),
                }
                yield format_sse(wire_payload)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
