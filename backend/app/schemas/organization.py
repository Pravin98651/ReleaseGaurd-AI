"""Pydantic v2 schemas for Phase 7 Multi-Repository Release Intelligence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    CoordinatedPlanStatus,
    DependencyType,
    DependencyVerificationStatus,
    RepoHealthCategory,
)


class OrganizationCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    slug: str = Field(..., min_length=2, max_length=80)
    github_org_id: int | None = None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    github_org_id: int | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class OrgRepoRegisterRequest(BaseModel):
    repository_id: uuid.UUID


class DependencyCreateRequest(BaseModel):
    source_repository_id: uuid.UUID
    target_repository_id: uuid.UUID
    dependency_type: DependencyType = DependencyType.SERVICE_DEPENDENCY
    version_constraint: str | None = None
    evidence_source: str = "manual"
    verified: bool = False


class RepositoryDependencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    source_repository_id: uuid.UUID
    target_repository_id: uuid.UUID
    dependency_type: DependencyType
    version_constraint: str | None = None
    confidence: float
    evidence_source: str
    verification_status: DependencyVerificationStatus
    last_verified_at: datetime | None = None
    created_at: datetime


class CoordinatedPlanCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    release_ids: list[uuid.UUID] = Field(..., min_length=1)


class CoordinatedReleasePlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    status: CoordinatedPlanStatus
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    recommended_order: list[str] = Field(default_factory=list)
    blocking_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    created_by_id: uuid.UUID | None = None
    approved_by_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class OrganizationPolicyCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    policy_type: str = Field(..., min_length=2, max_length=64)
    configuration: dict[str, Any] = Field(default_factory=dict)
    is_mandatory: bool = True


class OrganizationReleasePolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    policy_type: str
    configuration: dict[str, Any] = Field(default_factory=dict)
    is_mandatory: bool
    version: int
    status: str
    created_by_id: uuid.UUID | None = None
    approved_by_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class RepositoryHealthSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    health_score: float | None = None
    health_category: RepoHealthCategory
    evidence: dict[str, Any] = Field(default_factory=dict)
    sample_size: int
    data_completeness: float
    calculated_at: datetime


class CrossRepoImpactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_release_id: uuid.UUID
    source_repository_id: uuid.UUID
    affected_repositories: list[dict[str, Any]] = Field(default_factory=list)
    dependency_evidence: list[dict[str, Any]] = Field(default_factory=list)
    risk_findings: list[str] = Field(default_factory=list)
    confidence: float
    created_at: datetime


class OrgQueryRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=1000)
