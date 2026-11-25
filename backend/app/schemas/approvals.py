"""Pydantic v2 schemas for approvals, audit logs, and dashboard summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    ActorType,
    ApprovalCategory,
    ApprovalStatus,
    AuditStatus,
    PolicyCategory,
)
from app.schemas.common import ORMBaseModel
from app.schemas.experiments import RolloutHistoryOut


class ApprovalOut(ORMBaseModel):
    """Single-use hash-bound approval request summary."""

    id: UUID
    agent_task_id: UUID | None = None
    requested_by_id: UUID | None = None
    decided_by_id: UUID | None = None
    policy_category: ApprovalCategory
    action_type: str
    target_resource_type: str
    target_resource_id: str
    environment_name: str | None = None
    title: str
    rationale: str | None = None
    action_payload: dict[str, Any]
    expected_precondition: dict[str, Any]
    action_hash: str
    idempotency_key: str
    status: ApprovalStatus
    decision_comment: str | None = None
    execution_result: dict[str, Any] | None = None
    expires_at: datetime
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ApprovalApproveRequest(BaseModel):
    """Approve a pending Category B/C action."""

    comment: str | None = Field(default=None, max_length=1000)


class ApprovalRejectRequest(BaseModel):
    """Reject a pending Category B/C action."""

    comment: str = Field(min_length=1, max_length=1000)


class ApprovalDecisionResponse(BaseModel):
    """Outcome of approving or rejecting an approval request."""

    approval: ApprovalOut
    resumed_task_id: UUID | None = None


class AuditLogOut(ORMBaseModel):
    """Compliance audit log entry."""

    id: UUID
    actor_type: ActorType
    actor_id: str | None = None
    policy_category: PolicyCategory
    event_action: str
    resource_type: str
    resource_id: str | None = None
    environment_name: str | None = None
    approval_id: UUID | None = None
    action_hash: str | None = None
    status: AuditStatus
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DashboardCounts(BaseModel):
    """Executive overview KPI counts."""

    repositories: int = 0
    open_prs: int = 0
    high_risk_prs: int = 0
    active_flags: int = 0
    active_experiments: int = 0
    pending_approvals: int = 0
    unhealthy_flags: int = 0


class DashboardSummaryResponse(BaseModel):
    """Response for GET /api/v1/dashboard/summary."""

    counts: DashboardCounts
    integration_status: dict[str, Any] = Field(default_factory=dict)
    recent_rollouts: list[RolloutHistoryOut] = Field(default_factory=list)
    recent_audits: list[AuditLogOut] = Field(default_factory=list)
