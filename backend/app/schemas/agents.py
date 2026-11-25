"""Pydantic v2 schemas for LangGraph agent sessions, tasks, and SSE events."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import AgentSessionStatus, AgentTaskStatus, SpecialistRole
from app.schemas.approvals import ApprovalOut
from app.schemas.common import ORMBaseModel


class AgentContextScope(BaseModel):
    """Pinned session scope preventing cross-repo/env prompt-injection pivot."""

    repository_id: UUID | None = None
    pull_request_id: UUID | None = None
    flagsmith_project_id: int | None = None
    environment: str | None = None


class AgentSessionCreate(BaseModel):
    """Create a new agent conversation session."""

    title: str = Field(default="New Agent Session", min_length=1, max_length=255)
    context_scope: AgentContextScope = Field(default_factory=AgentContextScope)


class AgentSessionOut(ORMBaseModel):
    """Agent session summary."""

    id: UUID
    user_id: UUID | None = None
    title: str
    thread_id: str
    active_specialist: SpecialistRole
    status: AgentSessionStatus
    context_scope: dict[str, Any] = Field(default_factory=dict)
    message_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AgentStepLogItem(BaseModel):
    """Single step/event emitted on SSE stream and persisted in AgentTask.steps_log."""

    seq: int = Field(ge=1)
    event_type: str
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentTaskOut(ORMBaseModel):
    """Agent task execution status, steps log, and linked approvals."""

    id: UUID
    session_id: UUID
    specialist: SpecialistRole
    user_prompt: str
    status: AgentTaskStatus
    steps_log: list[AgentStepLogItem] = Field(default_factory=list)
    final_response: str | None = None
    error_message: str | None = None
    gemini_model: str | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    pending_approvals: list[ApprovalOut] = Field(default_factory=list)


class AgentSessionDetailOut(AgentSessionOut):
    """Full session details with recent tasks."""

    tasks: list[AgentTaskOut] = Field(default_factory=list)


class AgentMessageCreate(BaseModel):
    """Submit a user prompt to an agent session."""

    content: str = Field(min_length=1, max_length=10000)
    specialist_hint: SpecialistRole | None = None


class AgentTaskDispatchResponse(BaseModel):
    """202 Accepted response when a task is queued for background LangGraph execution."""

    task_id: UUID
    session_id: UUID
    stream_url: str
