"""GitHub repository, pull request, and generated code change ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
    AnalysisStatus,
    CodeChangeStatus,
    CodeChangeType,
    PullRequestState,
    RiskLevel,
    ValidationStatus,
    str_enum,
)

if TYPE_CHECKING:
    from app.models.agents import AgentTask
    from app.models.flags import FeatureFlag
    from app.models.identity import IntegrationConnection


class Repository(Base):
    """Connected GitHub repository monitored by PR Intelligence and Flag Health scanning."""

    __tablename__ = "repositories"
    __table_args__ = (
        Index("ix_repositories_full_name", "full_name"),
        Index("ix_repositories_owner_name", "owner", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    github_repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    owner: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    default_branch: Mapped[str] = mapped_column(String(120), nullable=False, default="main")
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flagsmith_project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    connection: Mapped[IntegrationConnection | None] = relationship(back_populates="repositories")
    pull_requests: Mapped[list[PullRequest]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    code_changes: Mapped[list[CodeChange]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    feature_flags: Mapped[list[FeatureFlag]] = relationship(back_populates="repository")


class PullRequest(Base):
    """GitHub pull request synced with AI risk assessment and flag opportunity findings."""

    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "github_pr_number", name="uq_pr_repo_number"),
        Index("ix_pr_risk_level", "risk_level"),
        Index("ix_pr_analysis_status", "analysis_status"),
        CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
            name="risk_score_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    github_pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_login: Mapped[str] = mapped_column(String(120), nullable=False)
    head_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[PullRequestState] = mapped_column(
        str_enum(PullRequestState, length=50),
        nullable=False,
        default=PullRequestState.OPEN,
    )
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    html_url: Mapped[str] = mapped_column(String(500), nullable=False)
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        str_enum(AnalysisStatus, length=50),
        nullable=False,
        default=AnalysisStatus.PENDING,
    )
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[RiskLevel | None] = mapped_column(
        str_enum(RiskLevel, length=50), nullable=True
    )
    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_risks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    potential_risks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    flag_opportunities: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    analyzed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    repository: Mapped[Repository] = relationship(back_populates="pull_requests")
    code_changes: Mapped[list[CodeChange]] = relationship(back_populates="pull_request")


class CodeChange(Base):
    """Generated Flagsmith wrapping/cleanup patch, tests, validation report, and Draft PR info."""

    __tablename__ = "code_changes"
    __table_args__ = (
        Index("ix_code_changes_repo_status", "repository_id", "status"),
        Index("ix_code_changes_pr", "pull_request_id"),
        Index("ix_code_changes_flag", "feature_flag_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    pull_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pull_requests.id", ondelete="SET NULL"), nullable=True
    )
    feature_flag_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("feature_flags.id", ondelete="SET NULL"), nullable=True
    )
    agent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    change_type: Mapped[CodeChangeType] = mapped_column(
        str_enum(CodeChangeType, length=50), nullable=False
    )
    flag_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    files_diff: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    generated_tests: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    validation_status: Mapped[ValidationStatus] = mapped_column(
        str_enum(ValidationStatus, length=50),
        nullable=False,
        default=ValidationStatus.PENDING,
    )
    validation_report: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    draft_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    draft_pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    draft_branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[CodeChangeStatus] = mapped_column(
        str_enum(CodeChangeStatus, length=50),
        nullable=False,
        default=CodeChangeStatus.DRAFT,
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    repository: Mapped[Repository] = relationship(back_populates="code_changes")
    pull_request: Mapped[PullRequest | None] = relationship(back_populates="code_changes")
    feature_flag: Mapped[FeatureFlag | None] = relationship(back_populates="code_changes")
    agent_task: Mapped[AgentTask | None] = relationship(back_populates="code_changes")
