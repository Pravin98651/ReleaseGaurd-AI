"""Identity, encrypted integration connection, and workspace settings ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
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
    AnalyticsMode,
    IntegrationAuthMode,
    IntegrationProvider,
    IntegrationStatus,
    UserRole,
    default_guardrail_thresholds,
    default_health_thresholds,
    str_enum,
)

if TYPE_CHECKING:
    from app.models.agents import AgentSession
    from app.models.github import Repository
    from app.models.governance import Approval


class User(Base):
    """Control-plane operators and workspace identities."""

    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        str_enum(UserRole, length=50),
        nullable=False,
        default=UserRole.ADMIN,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    integrations: Mapped[list[IntegrationConnection]] = relationship(back_populates="user")
    agent_sessions: Mapped[list[AgentSession]] = relationship(back_populates="user")
    requested_approvals: Mapped[list[Approval]] = relationship(
        foreign_keys="Approval.requested_by_id",
        back_populates="requested_by",
    )
    decided_approvals: Mapped[list[Approval]] = relationship(
        foreign_keys="Approval.decided_by_id",
        back_populates="decided_by",
    )


class IntegrationConnection(Base):
    """Encrypted external credentials for GitHub and Flagsmith."""

    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_account_id", name="uq_integration_provider_account"
        ),
        Index("ix_integration_provider_status", "provider", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[IntegrationProvider] = mapped_column(
        str_enum(IntegrationProvider, length=50), nullable=False
    )
    auth_mode: Mapped[IntegrationAuthMode] = mapped_column(
        str_enum(IntegrationAuthMode, length=50), nullable=False
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        str_enum(IntegrationStatus, length=50),
        nullable=False,
        default=IntegrationStatus.CONNECTED,
    )
    external_account_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_hint: Mapped[str] = mapped_column(String(32), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    user: Mapped[User | None] = relationship(back_populates="integrations")
    repositories: Mapped[list[Repository]] = relationship(back_populates="connection")


class WorkspaceSettings(Base):
    """Singleton workspace policy, model, and threshold configuration."""

    __tablename__ = "workspace_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    singleton_key: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, default="default"
    )
    category_b_requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    approval_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    gemini_model: Mapped[str] = mapped_column(
        String(100), nullable=False, default="gemini-2.5-flash"
    )
    flagsmith_analytics_mode: Mapped[AnalyticsMode] = mapped_column(
        str_enum(AnalyticsMode, length=50),
        nullable=False,
        default=AnalyticsMode.TRACKED_EVENT_FALLBACK,
    )
    flagsmith_capability_probed_at: Mapped[datetime | None] = mapped_column(
        TZDateTime, nullable=True
    )
    default_guardrails: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=default_guardrail_thresholds
    )
    health_thresholds: Mapped[dict[str, int]] = mapped_column(
        JSONBCompat, nullable=False, default=default_health_thresholds
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )
