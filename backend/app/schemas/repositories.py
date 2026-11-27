"""Pydantic v2 schemas for repositories, pull requests, and generated code changes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.enums import (
    AnalysisStatus,
    CodeChangeStatus,
    CodeChangeType,
    FlagType,
    PullRequestState,
    RiskLevel,
    ValidationStatus,
)
from app.schemas.common import ORMBaseModel


class RepositoryCreate(BaseModel):
    """Payload to connect a GitHub repository."""

    owner: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    flagsmith_project_id: int | None = Field(default=None, ge=1)


class RepositoryOut(ORMBaseModel):
    """Connected GitHub repository response."""

    id: UUID
    connection_id: UUID | None = None
    github_repo_id: int
    owner: str
    name: str
    full_name: str
    default_branch: str
    is_private: bool
    flagsmith_project_id: int | None = None
    language: str | None = None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BranchOut(BaseModel):
    """Live GitHub branch summary."""

    name: str
    commit_sha: str
    protected: bool = False


class FlagCodeReferenceOut(BaseModel):
    """Detected Flagsmith SDK reference in a repository file."""

    flag_key: str
    file_path: str
    line_number: int = Field(ge=1)
    snippet: str
    sdk_method: str | None = None


class FlagCodeScanResponse(BaseModel):
    """Result of scanning a repository for Flagsmith SDK references."""

    repository_id: UUID
    files_scanned: int = Field(ge=0)
    references_found: int = Field(ge=0)
    references_by_flag: dict[str, list[FlagCodeReferenceOut]] = Field(default_factory=dict)


class ConfirmedRiskSchema(BaseModel):
    """Risk directly observed in changed files with concrete file/line evidence."""

    title: str
    severity: RiskLevel
    file_path: str
    line_range: str
    evidence: str
    mitigation: str


class PotentialRiskSchema(BaseModel):
    """Unconfirmed hypothesis / edge case flagged for operator verification."""

    title: str
    severity: RiskLevel
    assumption: str
    verification_needed: str


class FlagOpportunitySchema(BaseModel):
    """Suggested feature flag insertion opportunity."""

    suggested_flag_key: str
    flag_type: FlagType = FlagType.STANDARD
    target_files: list[str] = Field(default_factory=list)
    rationale: str
    recommended_rollout: str = "10% -> 25% -> 50% -> 75% -> 100%"

    @field_validator("flag_type", mode="before")
    @classmethod
    def _normalize_legacy_flag_type(cls, value: Any) -> FlagType:
        if isinstance(value, FlagType):
            return value
        norm = str(value or "STANDARD").strip().upper()
        if norm in {"MULTIVARIATE", "MV", "VARIANT", "AB_TEST"}:
            return FlagType.MULTIVARIATE
        return FlagType.STANDARD


class PullRequestOut(ORMBaseModel):
    """Pull request list/summary schema."""

    id: UUID
    repository_id: UUID
    github_pr_number: int
    title: str
    body: str | None = None
    author_login: str
    head_branch: str
    base_branch: str
    head_sha: str
    state: PullRequestState
    is_draft: bool
    html_url: str
    analysis_status: AnalysisStatus
    risk_score: int | None = None
    risk_level: RiskLevel | None = None
    analysis_summary: str | None = None
    confirmed_risks: list[ConfirmedRiskSchema] = Field(default_factory=list)
    potential_risks: list[PotentialRiskSchema] = Field(default_factory=list)
    flag_opportunities: list[FlagOpportunitySchema] = Field(default_factory=list)
    analyzed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FileDiffItem(BaseModel):
    """Single file patch entry within a CodeChange."""

    file_path: str = Field(min_length=1)
    action: Literal["create", "modify"]
    before_code: str = ""
    after_code: str
    unified_diff: str = ""


class GeneratedTestItem(BaseModel):
    """Generated test file payload within a CodeChange."""

    file_path: str = Field(min_length=1)
    content: str


class ValidationCheckItem(BaseModel):
    """Individual check result from Isolated Temp-Dir Patch Validator."""

    name: str
    status: Literal["passed", "failed", "skipped"]
    detail: str = ""
    skipped_reason: str | None = None


class ValidationReportSchema(BaseModel):
    """Structured report matching docs/database-and-policies.md Sec 3.7."""

    validation_status: Literal["passed", "failed"]
    ast_ok: bool
    path_safety_ok: bool
    overwrite_ok: bool
    ruff_ok: bool | None = None
    pytest_ok: bool | None = None
    eslint_ok: bool | None = None
    checks: list[ValidationCheckItem] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


class GeneratePatchRequest(BaseModel):
    """Request to generate a Flagsmith-wrapping code patch for a PR."""

    flag_key: str = Field(min_length=1, max_length=128)
    flag_type: FlagType = FlagType.STANDARD
    target_files: list[str] | None = None


class CodeChangeOut(ORMBaseModel):
    """Code change patch, generated tests, validation report, and Draft PR metadata."""

    id: UUID
    repository_id: UUID
    pull_request_id: UUID | None = None
    feature_flag_id: UUID | None = None
    agent_task_id: UUID | None = None
    change_type: CodeChangeType
    flag_key: str
    title: str
    description: str | None = None
    files_diff: list[FileDiffItem] = Field(default_factory=list)
    generated_tests: list[GeneratedTestItem] = Field(default_factory=list)
    validation_status: ValidationStatus
    validation_report: dict[str, Any] = Field(default_factory=dict)
    draft_pr_number: int | None = None
    draft_pr_url: str | None = None
    draft_branch_name: str | None = None
    status: CodeChangeStatus
    created_at: datetime
    updated_at: datetime


class PullRequestDetailOut(PullRequestOut):
    """Pull request detail including linked generated code changes."""

    code_changes: list[CodeChangeOut] = Field(default_factory=list)
    diff_summary: dict[str, Any] = Field(default_factory=dict)
    changed_files: list[dict[str, Any]] = Field(default_factory=list)


class PullRequestAnalyzeResponse(BaseModel):
    """Result of PR risk analysis and flag opportunity detection."""

    pull_request: PullRequestOut
    static_signals: list[str] = Field(default_factory=list)


class CreateDraftPRRequest(BaseModel):
    """Payload to open a Draft PR from a validated CodeChange."""

    base_branch: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None


class DraftPRResult(BaseModel):
    """Result returned when a Draft PR is created on GitHub."""

    code_change_id: UUID
    pr_number: int
    html_url: str
    branch_name: str
    commit_sha: str
    draft: bool = True
