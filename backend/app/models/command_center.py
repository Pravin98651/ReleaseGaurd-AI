"""Agent Command Center ORM models: execution, steps, tool calls, decisions, traces, snapshots."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import (
    ActorType,
    AgentDecisionSource,
    AgentDecisionStatus,
    AgentExecutionStatus,
    AgentStepStatus,
    AgentToolCallStatus,
    str_enum,
)

if TYPE_CHECKING:
    from app.models.agents import AgentTask
    from app.models.experiments import Experiment
    from app.models.github import PullRequest, Repository
    from app.models.identity import User
    from app.models.operations import ScheduledJob
    from app.models.releases import Release


class AgentExecution(Base):
    """Persistent, controllable execution record for the Autonomous Release Command Center."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        Index("ix_agent_executions_status_updated", "status", "updated_at"),
        Index("ix_agent_executions_release", "release_id"),
        Index("ix_agent_executions_repo", "repository_id"),
        Index("ix_agent_executions_task", "agent_task_id"),
        Index("ix_agent_executions_corr", "correlation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_key: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    agent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[AgentExecutionStatus] = mapped_column(
        str_enum(AgentExecutionStatus, length=40),
        nullable=False,
        default=AgentExecutionStatus.QUEUED,
    )
    previous_status: Mapped[AgentExecutionStatus | None] = mapped_column(
        str_enum(AgentExecutionStatus, length=40), nullable=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="interactive")

    initiated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_type: Mapped[ActorType] = mapped_column(
        str_enum(ActorType, length=30), nullable=False, default=ActorType.USER
    )

    agent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    pull_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pull_requests.id", ondelete="SET NULL"), nullable=True
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("scheduled_jobs.id", ondelete="SET NULL"), nullable=True
    )
    parent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_executions.id", ondelete="SET NULL"), nullable=True
    )

    current_stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_activity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    completed_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    execution_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    initiated_by: Mapped[User | None] = relationship()
    agent_task: Mapped[AgentTask | None] = relationship()
    repository: Mapped[Repository | None] = relationship()
    pull_request: Mapped[PullRequest | None] = relationship()
    release: Mapped[Release | None] = relationship()
    experiment: Mapped[Experiment | None] = relationship()
    schedule: Mapped[ScheduledJob | None] = relationship()

    steps: Mapped[list[AgentExecutionStep]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="AgentExecutionStep.sequence",
    )
    tool_calls: Mapped[list[AgentToolCall]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="AgentToolCall.created_at",
    )
    decisions: Mapped[list[AgentDecision]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="AgentDecision.created_at",
    )
    traces: Mapped[list[AgentExecutionTrace]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="AgentExecutionTrace.sequence_number",
    )
    snapshots: Mapped[list[AgentExecutionSnapshot]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="AgentExecutionSnapshot.checkpoint_number",
    )


class AgentExecutionStep(Base):
    """Ordered stage inside an AgentExecution."""

    __tablename__ = "agent_execution_steps"
    __table_args__ = (
        UniqueConstraint("execution_id", "step_key", name="uq_agent_exec_steps_key"),
        Index("ix_agent_exec_steps_seq", "execution_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(80), nullable=False)
    step_name: Mapped[str] = mapped_column(String(160), nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False, default="stage")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AgentStepStatus] = mapped_column(
        str_enum(AgentStepStatus, length=32), nullable=False, default=AgentStepStatus.PENDING
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    output_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    execution: Mapped[AgentExecution] = relationship(back_populates="steps")


class AgentToolCall(Base):
    """Instrumented external or internal tool invocation with redacted I/O summaries."""

    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        Index("ix_agent_tool_calls_exec_created", "execution_id", "created_at"),
        Index("ix_agent_tool_calls_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_execution_steps.id", ondelete="SET NULL"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    tool_category: Mapped[str] = mapped_column(String(64), nullable=False, default="domain")
    invocation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AgentToolCallStatus] = mapped_column(
        str_enum(AgentToolCallStatus, length=32),
        nullable=False,
        default=AgentToolCallStatus.RUNNING,
    )
    is_reversible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    input_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    output_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    execution: Mapped[AgentExecution] = relationship(back_populates="tool_calls")


class AgentDecision(Base):
    """Explainable agent decision or recommendation grounded in observable evidence."""

    __tablename__ = "agent_decisions"
    __table_args__ = (
        Index("ix_agent_decisions_exec_created", "execution_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_execution_steps.id", ondelete="SET NULL"), nullable=True
    )
    decision_type: Mapped[str] = mapped_column(String(80), nullable=False)
    decision_source: Mapped[AgentDecisionSource] = mapped_column(
        str_enum(AgentDecisionSource, length=40),
        nullable=False,
        default=AgentDecisionSource.AI_RECOMMENDATION,
    )
    decision_summary: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_status: Mapped[AgentDecisionStatus] = mapped_column(
        str_enum(AgentDecisionStatus, length=32),
        nullable=False,
        default=AgentDecisionStatus.PENDING,
    )
    supporting_evidence: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    evidence_references: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    execution: Mapped[AgentExecution] = relationship(back_populates="decisions")


class AgentExecutionTrace(Base):
    """Canonical ordered execution timeline event."""

    __tablename__ = "agent_execution_traces"
    __table_args__ = (
        UniqueConstraint("execution_id", "sequence_number", name="uq_agent_exec_traces_seq"),
        Index("ix_agent_exec_traces_exec_seq", "execution_id", "sequence_number"),
        Index("ix_agent_exec_traces_type", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="lifecycle")
    stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_type: Mapped[ActorType] = mapped_column(
        str_enum(ActorType, length=30), nullable=False, default=ActorType.SYSTEM
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    execution: Mapped[AgentExecution] = relationship(back_populates="traces")


class AgentExecutionSnapshot(Base):
    """Safe recoverable execution checkpoint (no secrets or raw model reasoning)."""

    __tablename__ = "agent_execution_snapshots"
    __table_args__ = (
        UniqueConstraint("execution_id", "checkpoint_number", name="uq_agent_exec_snap_num"),
        Index("ix_agent_exec_snap_exec", "execution_id", "checkpoint_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False
    )
    checkpoint_number: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_type: Mapped[str] = mapped_column(String(48), nullable=False, default="safe_point")
    resumable_state: Mapped[AgentExecutionStatus] = mapped_column(
        str_enum(AgentExecutionStatus, length=40), nullable=False
    )
    current_step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_execution_steps.id", ondelete="SET NULL"), nullable=True
    )
    completed_step_ids: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    pending_operation: Mapped[str | None] = mapped_column(String(160), nullable=True)
    context_reference: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    execution: Mapped[AgentExecution] = relationship(back_populates="snapshots")
