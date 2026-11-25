"""Pydantic v2 schemas for Phase 6 Developer Experience Integration."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    DeliveryStatus,
    GitHubCheckConclusion,
    GitHubCheckStatus,
    IntegrationStatus,
    ReportReadinessOutcome,
    WebhookProcessingStatus,
)


class GitHubAppConnectRequest(BaseModel):
    installation_id: int = Field(..., ge=1)
    account_login: str = Field(..., min_length=1, max_length=120)
    account_type: str = Field(default="Organization", max_length=32)
    repositories: list[dict[str, Any]] = Field(default_factory=list)
    permissions: dict[str, str] = Field(default_factory=dict)
    access_token: str | None = None
    webhook_secret: str | None = None


class GitHubInstallationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    installation_id: int
    account_login: str
    account_type: str
    repository_selection: str
    repositories: list[dict[str, Any]] = Field(default_factory=list)
    permissions: dict[str, str] = Field(default_factory=dict)
    status: IntegrationStatus
    token_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class GitHubWebhookEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    delivery_id: str
    event_type: str
    action: str | None = None
    installation_id: int | None = None
    repository_id: uuid.UUID | None = None
    pr_number: int | None = None
    commit_sha: str | None = None
    signature_verified: bool
    processing_status: WebhookProcessingStatus
    retry_count: int
    error_message: str | None = None
    correlation_id: str
    payload_summary: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime
    processed_at: datetime | None = None


class GitHubCheckRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    pull_request_id: uuid.UUID
    commit_sha: str
    external_check_run_id: int | None = None
    name: str
    status: GitHubCheckStatus
    conclusion: GitHubCheckConclusion | None = None
    policy_mode: str
    summary: str
    details_url: str | None = None
    is_superseded: bool
    created_at: datetime
    updated_at: datetime


class PullRequestCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    pull_request_id: uuid.UUID
    commit_sha: str
    external_comment_id: int | None = None
    marker_id: str
    body_markdown: str
    publish_status: str
    last_error: str | None = None
    updated_at: datetime


class NotificationWebhookConnectRequest(BaseModel):
    webhook_url: str = Field(..., min_length=10, max_length=500)
    channel_label: str = Field(default="#releases", max_length=128)


class NotificationDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    channel_target: str
    event_type: str
    severity: str
    status: DeliveryStatus
    retry_count: int
    response_status: int | None = None
    error_message: str | None = None
    sent_at: datetime | None = None
    created_at: datetime


class ReleaseReadinessReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    report_key: str
    title: str
    readiness_outcome: ReportReadinessOutcome
    report_data: dict[str, Any] = Field(default_factory=dict)
    markdown_content: str
    created_by_id: uuid.UUID | None = None
    created_at: datetime


class ShareLinkCreateRequest(BaseModel):
    expires_in_hours: int = Field(default=24, ge=1, le=720)


class ShareableReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_id: uuid.UUID
    expires_at: datetime
    revoked_at: datetime | None = None
    access_count: int
    last_accessed_at: datetime | None = None
    created_at: datetime
    share_token: str | None = None
    share_url: str | None = None


class PRPublishRequest(BaseModel):
    policy_mode: str = Field(default="warn_medium", max_length=64)
    include_historical_intelligence: bool = True
