"""Developer Experience Integration ORM models (Phase 6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import (
    DeliveryStatus,
    GitHubCheckConclusion,
    GitHubCheckStatus,
    IntegrationStatus,
    ReportReadinessOutcome,
    WebhookProcessingStatus,
    str_enum,
)


class GitHubInstallation(Base):
    """GitHub App installation & repository mapping."""

    __tablename__ = "github_installations"
    __table_args__ = (
        Index("ix_gh_installations_ext_id_unique", "installation_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_login: Mapped[str] = mapped_column(String(120), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False, default="Organization")
    repository_selection: Mapped[str] = mapped_column(String(32), nullable=False, default="selected")
    repositories: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    permissions: Mapped[dict[str, str]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[IntegrationStatus] = mapped_column(
        str_enum(IntegrationStatus, length=32), nullable=False, default=IntegrationStatus.CONNECTED
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class GitHubWebhookEvent(Base):
    """Verified GitHub webhook delivery with idempotency and retry tracking."""

    __tablename__ = "github_webhook_events"
    __table_args__ = (
        Index("ix_gh_webhooks_delivery_unique", "delivery_id", unique=True),
        Index("ix_gh_webhooks_status_received", "processing_status", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processing_status: Mapped[WebhookProcessingStatus] = mapped_column(
        str_enum(WebhookProcessingStatus, length=32),
        nullable=False,
        default=WebhookProcessingStatus.RECEIVED,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    received_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class GitHubCheckRun(Base):
    """GitHub PR Status Check / Check Run state linked to commit SHA."""

    __tablename__ = "github_check_runs"
    __table_args__ = (
        Index("ix_gh_check_runs_pr_sha", "pull_request_id", "commit_sha"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    external_check_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="ReleaseGuard AI")
    status: Mapped[GitHubCheckStatus] = mapped_column(
        str_enum(GitHubCheckStatus, length=32), nullable=False, default=GitHubCheckStatus.QUEUED
    )
    conclusion: Mapped[GitHubCheckConclusion | None] = mapped_column(
        str_enum(GitHubCheckConclusion, length=32), nullable=True
    )
    policy_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="warn_medium")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class PullRequestAnalysisComment(Base):
    """Stable marker-identified PR risk summary comment updated in place."""

    __tablename__ = "pull_request_analysis_comments"
    __table_args__ = (
        Index("ix_pr_comments_pr_unique", "pull_request_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    external_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    marker_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default="<!-- releaseguard-ai:pr-summary -->"
    )
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    publish_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class NotificationDelivery(Base):
    """Outbound Slack/Discord notification delivery tracking & retry record."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("ix_notif_deliveries_provider_status", "provider", "status"),
        Index("ix_notif_deliveries_dedupe", "dedupe_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_target: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False, default="info")
    status: Mapped[DeliveryStatus] = mapped_column(
        str_enum(DeliveryStatus, length=32), nullable=False, default=DeliveryStatus.PENDING
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class ReleaseReadinessReport(Base):
    """Authoritative release readiness report generated from persisted ReleaseGuard data."""

    __tablename__ = "release_readiness_reports"
    __table_args__ = (
        Index("ix_readiness_reports_release", "release_id"),
        Index("ix_readiness_reports_key_unique", "report_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), nullable=False
    )
    report_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    readiness_outcome: Mapped[ReportReadinessOutcome] = mapped_column(
        str_enum(ReportReadinessOutcome, length=40),
        nullable=False,
        default=ReportReadinessOutcome.INSUFFICIENT_EVIDENCE,
    )
    report_data: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class ShareableReport(Base):
    """Cryptographic SHA-256 token-hashed share link for ReleaseReadinessReport."""

    __tablename__ = "shareable_reports"
    __table_args__ = (
        Index("ix_shareable_reports_token_hash_unique", "token_hash", unique=True),
        Index("ix_shareable_reports_report", "report_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("release_readiness_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class ShareLinkAccessLog(Base):
    """Audit log of every share link access attempt."""

    __tablename__ = "share_link_access_logs"
    __table_args__ = (Index("ix_share_access_logs_share", "share_id", "accessed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    share_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("shareable_reports.id", ondelete="CASCADE"), nullable=False
    )
    accessed_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
