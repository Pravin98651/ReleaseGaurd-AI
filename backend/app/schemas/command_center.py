"""Pydantic v2 schemas for the Autonomous Release Command Center."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActorType,
    AgentDecisionSource,
    AgentDecisionStatus,
    AgentExecutionStatus,
    AgentStepStatus,
    AgentToolCallStatus,
)
from app.schemas.approvals import ApprovalOut


class AgentExecutionStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: uuid.UUID
    step_key: str
    step_name: str
    step_type: str
    sequence: int
    status: AgentStepStatus
    description: str | None = None
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    retry_count: int = 0
    error_details: str | None = None
    created_at: datetime


class AgentToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: uuid.UUID
    step_id: uuid.UUID | None = None
    tool_name: str
    tool_category: str
    invocation_id: str
    status: AgentToolCallStatus
    is_reversible: bool
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    retry_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime


class AgentDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: uuid.UUID
    step_id: uuid.UUID | None = None
    decision_type: str
    decision_source: AgentDecisionSource
    decision_summary: str
    recommendation: str | None = None
    confidence: float | None = None
    decision_status: AgentDecisionStatus
    supporting_evidence: list[str] = Field(default_factory=list)
    evidence_references: list[dict[str, Any]] = Field(default_factory=list)
    model_name: str | None = None
    created_at: datetime


class AgentExecutionTraceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: uuid.UUID
    sequence_number: int
    event_type: str
    category: str
    stage: str | None = None
    actor_type: ActorType
    actor_id: uuid.UUID | None = None
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    created_at: datetime


class AgentExecutionSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: uuid.UUID
    checkpoint_number: int
    checkpoint_type: str
    resumable_state: AgentExecutionStatus
    current_step_id: uuid.UUID | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    pending_operation: str | None = None
    context_reference: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_key: str
    agent_type: str
    agent_name: str
    task_name: str
    task_description: str | None = None
    status: AgentExecutionStatus
    previous_status: AgentExecutionStatus | None = None
    priority: int
    execution_mode: str
    initiated_by_id: uuid.UUID | None = None
    actor_type: ActorType
    agent_task_id: uuid.UUID | None = None
    repository_id: uuid.UUID | None = None
    pull_request_id: uuid.UUID | None = None
    release_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    parent_execution_id: uuid.UUID | None = None
    current_stage: str | None = None
    current_activity: str | None = None
    completed_steps: int
    total_steps: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    timeout_at: datetime | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int
    max_retries: int
    pause_requested: bool
    cancel_requested: bool
    correlation_id: str
    execution_metadata: dict[str, Any] = Field(default_factory=dict)
    version: int
    created_at: datetime
    updated_at: datetime


class AgentExecutionDetailOut(BaseModel):
    execution: AgentExecutionOut
    steps: list[AgentExecutionStepOut] = Field(default_factory=list)
    tool_calls: list[AgentToolCallOut] = Field(default_factory=list)
    decisions: list[AgentDecisionOut] = Field(default_factory=list)
    traces: list[AgentExecutionTraceOut] = Field(default_factory=list)
    checkpoints: list[AgentExecutionSnapshotOut] = Field(default_factory=list)
    approvals: list[ApprovalOut] = Field(default_factory=list)


class AgentExecutionCreateRequest(BaseModel):
    agent_type: str = Field(default="pr_agent", max_length=64)
    agent_name: str | None = Field(default=None, max_length=128)
    task_name: str = Field(..., min_length=2, max_length=255)
    task_description: str | None = None
    execution_mode: str = Field(default="interactive", max_length=32)
    priority: int = Field(default=5, ge=1, le=10)
    repository_id: uuid.UUID | None = None
    pull_request_id: uuid.UUID | None = None
    release_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    start_immediately: bool = False


class AgentExecutionControlRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class AgentExecutionOverviewOut(BaseModel):
    total_executions: int = 0
    active_executions: int = 0
    queued_executions: int = 0
    waiting_for_approval: int = 0
    paused_executions: int = 0
    failed_executions: int = 0
    completed_executions: int = 0
    average_duration_ms: float | None = None
    tool_failure_rate: float | None = None
    total_tool_calls: int = 0
    failed_tool_calls: int = 0
