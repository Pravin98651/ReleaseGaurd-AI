"""Multi-Repository & Organization Release Intelligence ORM models (Phase 7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import (
    CoordinatedPlanStatus,
    DependencyType,
    DependencyVerificationStatus,
    RepoHealthCategory,
    str_enum,
)


class Organization(Base):
    """Organization container unifying repositories, shared policies, and coordinated releases."""

    __tablename__ = "organizations"
    __table_args__ = (Index("ix_organizations_slug_unique", "slug", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    github_org_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class OrganizationRepository(Base):
    """Maps a Repository into an Organization with monitoring state."""

    __tablename__ = "organization_repositories"
    __table_args__ = (
        Index("ix_org_repos_org_repo_unique", "organization_id", "repository_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    github_repository_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class RepositoryDependency(Base):
    """Directed dependency edge between two repositories (source depends on target)."""

    __tablename__ = "repository_dependencies"
    __table_args__ = (
        Index(
            "ix_repo_deps_src_tgt_type_unique",
            "source_repository_id",
            "target_repository_id",
            "dependency_type",
            unique=True,
        ),
        Index("ix_repo_deps_org", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    source_repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    target_repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    dependency_type: Mapped[DependencyType] = mapped_column(
        str_enum(DependencyType, length=40), nullable=False, default=DependencyType.SERVICE_DEPENDENCY
    )
    version_constraint: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    evidence_source: Mapped[str] = mapped_column(String(120), nullable=False, default="manual")
    verification_status: Mapped[DependencyVerificationStatus] = mapped_column(
        str_enum(DependencyVerificationStatus, length=32),
        nullable=False,
        default=DependencyVerificationStatus.DISCOVERED,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class CoordinatedReleasePlan(Base):
    """Dependency-aware coordinated release plan across multiple repositories."""

    __tablename__ = "coordinated_release_plans"
    __table_args__ = (Index("ix_coord_plans_org_status", "organization_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[CoordinatedPlanStatus] = mapped_column(
        str_enum(CoordinatedPlanStatus, length=32),
        nullable=False,
        default=CoordinatedPlanStatus.DRAFT,
    )
    risk_summary: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    recommended_order: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    blocking_dependencies: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class CoordinatedReleaseItem(Base):
    """Links an individual Release to a CoordinatedReleasePlan with execution order."""

    __tablename__ = "coordinated_release_items"
    __table_args__ = (
        Index("ix_coord_items_plan_order", "coordinated_plan_id", "execution_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    coordinated_plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("coordinated_release_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    execution_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dependency_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    coordination_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")


class OrganizationReleasePolicy(Base):
    """Shared organization-wide release policy with versioned governance."""

    __tablename__ = "organization_release_policies"
    __table_args__ = (Index("ix_org_policies_org_status", "organization_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_type: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class RepositoryPolicyAssignment(Base):
    """Assigns or requests an exception for an OrganizationReleasePolicy on a Repository."""

    __tablename__ = "repository_policy_assignments"
    __table_args__ = (
        Index("ix_repo_policy_assign_unique", "repository_id", "policy_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_release_policies.id", ondelete="CASCADE"),
        nullable=False,
    )
    override_configuration: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompat, nullable=True)
    exception_status: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class OrganizationRiskSnapshot(Base):
    """Periodic organization-wide release risk & reliability snapshot."""

    __tablename__ = "organization_risk_snapshots"
    __table_args__ = (Index("ix_org_risk_snap_org_calc", "organization_id", "calculated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    analysis_period: Mapped[str] = mapped_column(String(32), nullable=False, default="30d")
    repository_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    release_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rollback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_summary: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    calculated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class RepositoryHealthSnapshot(Base):
    """Transparent repository health score & category snapshot."""

    __tablename__ = "repository_health_snapshots"
    __table_args__ = (Index("ix_repo_health_snap_repo_calc", "repository_id", "calculated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    health_category: Mapped[RepoHealthCategory] = mapped_column(
        str_enum(RepoHealthCategory, length=32),
        nullable=False,
        default=RepoHealthCategory.INSUFFICIENT_DATA,
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    calculated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class CrossRepositoryImpactAssessment(Base):
    """Impact propagation assessment when a source release affects downstream repositories."""

    __tablename__ = "cross_repository_impact_assessments"
    __table_args__ = (Index("ix_cross_impact_release", "source_release_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), nullable=False
    )
    source_repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    affected_repositories: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    dependency_evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    risk_findings: Mapped[list[str]] = mapped_column(JSONBCompat, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
