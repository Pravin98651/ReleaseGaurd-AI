"""Single-use hash-bound approval queue, live drift revalidation, and LangGraph resume endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.approval_executor import execute_approval_request
from app.api.deps import (
    DBSession,
    DefaultOperator,
    build_gemini_client_from_app,
    build_supervisor,
    build_tool_context,
)
from app.api.v1.errors import APIException
from app.models import (
    AgentSession,
    AgentTask,
    Approval,
    ApprovalCategory,
    ApprovalStatus,
)
from app.schemas import (
    ApprovalApproveRequest,
    ApprovalDecisionResponse,
    ApprovalOut,
    ApprovalRejectRequest,
    PaginatedResponse,
)
from app.services.policy_engine import PolicyEngine, PreconditionDriftError

logger = logging.getLogger("releaseguard.api.approvals")

approvals_router = APIRouter(prefix="/approvals", tags=["approvals"])


async def _resume_linked_agent_task(
    request: Request,
    session: AsyncSession,
    approval: Approval,
    resume_payload: dict[str, Any],
) -> uuid.UUID | None:
    if approval.agent_task_id is None:
        return None
    task = await session.get(AgentTask, approval.agent_task_id)
    if task is None:
        return None
    agent_sess = await session.get(AgentSession, task.session_id)
    if agent_sess is None:
        return None

    ws = await PolicyEngine.get_workspace_settings(session)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)
    if not gemini_client.is_configured:
        return None

    supervisor = build_supervisor(request.app, model_override=ws.gemini_model)
    await supervisor.resume_task(
        task_id=task.id,
        session_id=agent_sess.id,
        thread_id=agent_sess.thread_id,
        resume_payload=resume_payload,
    )
    return task.id


@approvals_router.get("", response_model=PaginatedResponse[ApprovalOut])
async def list_approvals(
    session: DBSession,
    status: Annotated[ApprovalStatus | None, Query()] = None,
    policy_category: Annotated[ApprovalCategory | None, Query()] = None,
    environment_name: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[ApprovalOut]:
    """List approval requests with optional status, category, and environment filters."""
    base_q = select(Approval)
    count_q = select(func.count(Approval.id))

    if status is not None:
        base_q = base_q.where(Approval.status == status)
        count_q = count_q.where(Approval.status == status)
    if policy_category is not None:
        base_q = base_q.where(Approval.policy_category == policy_category)
        count_q = count_q.where(Approval.policy_category == policy_category)
    if environment_name:
        env_norm = environment_name.strip().lower()
        base_q = base_q.where(Approval.environment_name == env_norm)
        count_q = count_q.where(Approval.environment_name == env_norm)

    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                base_q.order_by(Approval.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse[ApprovalOut](
        items=[ApprovalOut.model_validate(a) for a in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@approvals_router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: uuid.UUID,
    session: DBSession,
) -> ApprovalOut:
    """Return full approval details including action_hash, payload, and expected_precondition."""
    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise APIException(404, "APPROVAL_NOT_FOUND", f"Approval {approval_id} not found.")
    return ApprovalOut.model_validate(approval)


@approvals_router.post("/{approval_id}/approve", response_model=ApprovalDecisionResponse)
async def approve_action(
    approval_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
    body: ApprovalApproveRequest | None = None,
) -> ApprovalDecisionResponse:
    """Execute single-use hash-verified approval with live drift revalidation and LangGraph resume."""
    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise APIException(404, "APPROVAL_NOT_FOUND", f"Approval {approval_id} not found.")

    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    comment = body.comment if body else None

    try:
        updated = await execute_approval_request(
            ctx,
            approval,
            decided_by_id=operator.id,
            comment=comment,
        )
    except PreconditionDriftError as exc:
        if approval.agent_task_id is not None:
            try:
                await _resume_linked_agent_task(
                    request,
                    session,
                    approval,
                    {
                        "decision": "drift",
                        "approval_id": str(approval.id),
                        "drift_details": exc.details,
                    },
                )
            except (RuntimeError, ValueError) as resume_exc:
                logger.warning("Failed to resume task after drift error: %s", resume_exc)
        raise

    resumed_task_id = await _resume_linked_agent_task(
        request,
        session,
        updated,
        {
            "decision": "approved",
            "approval_id": str(updated.id),
            "executed": True,
            "result": updated.execution_result or {},
        },
    )
    return ApprovalDecisionResponse(
        approval=ApprovalOut.model_validate(updated),
        resumed_task_id=resumed_task_id,
    )


@approvals_router.post("/{approval_id}/reject", response_model=ApprovalDecisionResponse)
async def reject_action(
    approval_id: uuid.UUID,
    body: ApprovalRejectRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> ApprovalDecisionResponse:
    """Reject a pending approval and resume any waiting LangGraph task cleanly."""
    rejected = await PolicyEngine.reject_approval(
        session,
        approval_id,
        decided_by_id=operator.id,
        comment=body.comment,
    )
    resumed_task_id = await _resume_linked_agent_task(
        request,
        session,
        rejected,
        {
            "decision": "rejected",
            "approval_id": str(rejected.id),
            "comment": body.comment,
        },
    )
    return ApprovalDecisionResponse(
        approval=ApprovalOut.model_validate(rejected),
        resumed_task_id=resumed_task_id,
    )
