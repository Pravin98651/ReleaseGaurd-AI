"""Pydantic v2 schemas for Persistent Scheduling, Preview, and Execution History."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    JobExecutionStatus,
    JobTriggerType,
    ScheduleFrequency,
    ScheduleJobType,
)
from app.schemas.common import ORMBaseModel


class ScheduleRetryPolicy(BaseModel):
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_delay_seconds: int = Field(default=30, ge=1, le=3600)
    timeout_seconds: int = Field(default=120, ge=5, le=1800)


class ScheduleNotificationPref(BaseModel):
    on_success: bool = False
    on_failure: bool = True


class ScheduleCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    job_type: ScheduleJobType
    frequency: ScheduleFrequency = ScheduleFrequency.DAILY
    cron_expression: str | None = Field(default=None, max_length=120)
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    start_at: datetime | None = None
    end_at: datetime | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    retry_policy: ScheduleRetryPolicy = Field(default_factory=ScheduleRetryPolicy)
    notification_preference: ScheduleNotificationPref = Field(default_factory=ScheduleNotificationPref)
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    job_type: ScheduleJobType | None = None
    frequency: ScheduleFrequency | None = None
    cron_expression: str | None = Field(default=None, max_length=120)
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    start_at: datetime | None = None
    end_at: datetime | None = None
    configuration: dict[str, Any] | None = None
    retry_policy: ScheduleRetryPolicy | None = None
    notification_preference: ScheduleNotificationPref | None = None
    enabled: bool | None = None


class SchedulePreviewRequest(BaseModel):
    frequency: ScheduleFrequency = ScheduleFrequency.DAILY
    cron_expression: str | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    timezone: str = "UTC"
    start_at: datetime | None = None
    end_at: datetime | None = None
    nl_prompt: str | None = None


class ScheduleOut(ORMBaseModel):
    id: UUID
    name: str
    description: str | None = None
    job_type: ScheduleJobType
    frequency: ScheduleFrequency
    cron_expression: str | None = None
    interval_minutes: int | None = None
    timezone: str
    start_at: datetime | None = None
    end_at: datetime | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    notification_preference: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    owner_id: UUID | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_status: str | None = None
    consecutive_failures: int = 0
    missed_runs_count: int = 0
    human_description: str = ""
    next_runs_preview: list[dict[str, str]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class JobExecutionOut(ORMBaseModel):
    id: UUID
    scheduled_job_id: UUID | None = None
    schedule_name: str
    job_type: ScheduleJobType
    execution_status: JobExecutionStatus
    trigger_type: JobTriggerType
    started_at: datetime | None = None
    completed_at: datetime | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    result_summary: dict[str, Any] | None = None
    error_details: str | None = None
    retry_count: int = 0
    agent_task_id: UUID | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    created_at: datetime
