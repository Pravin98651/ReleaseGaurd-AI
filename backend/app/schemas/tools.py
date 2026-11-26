"""Pydantic v2 input/output schemas for all 34 Gemini manual tools (docs/api.md Sec 3)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.enums import AnalyticsMode, FlagType, RiskLevel
from app.schemas.flags import MultivariateOptionSchema
from app.schemas.repositories import (
    ConfirmedRiskSchema,
    FileDiffItem,
    FlagOpportunitySchema,
    GeneratedTestItem,
    PotentialRiskSchema,
)
from app.schemas.settings import GuardrailThresholdsSchema

# ==============================================================================
# 3.1 pr_agent Tools (11 Tools)
# ==============================================================================


class GitHubListRepositoriesInput(BaseModel):
    visibility: Literal["all", "public", "private"] = "all"
    limit: int = Field(default=30, ge=1, le=100)


class GitHubListRepositoriesOutput(BaseModel):
    repositories: list[dict[str, Any]] = Field(default_factory=list)


class GitHubListBranchesInput(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)


class GitHubListBranchesOutput(BaseModel):
    branches: list[dict[str, Any]] = Field(default_factory=list)


class GitHubListPullRequestsInput(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    state: Literal["open", "closed", "all"] = "open"


class GitHubListPullRequestsOutput(BaseModel):
    pull_requests: list[dict[str, Any]] = Field(default_factory=list)


class GitHubGetPullRequestDiffInput(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_number: int = Field(ge=1)
    max_files: int = Field(default=50, ge=1, le=200)


class GitHubGetPullRequestDiffOutput(BaseModel):
    pr_number: int
    title_sandboxed: str
    body_sandboxed: str
    diff_sandboxed: str
    changed_files: list[str] = Field(default_factory=list)


class GitHubGetFileContentsInput(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    path: str = Field(min_length=1)
    ref: str | None = None


class GitHubGetFileContentsOutput(BaseModel):
    path: str
    sha: str
    content_sandboxed: str


class GitHubSearchRepoCodeInput(BaseModel):
    repository_id: UUID
    query_pattern: str = Field(min_length=1)


class GitHubSearchRepoCodeOutput(BaseModel):
    matches: list[dict[str, Any]] = Field(default_factory=list)


class AnalyzePRRiskAndOpportunitiesInput(BaseModel):
    pull_request_id: UUID
    risk_score: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    summary: str = Field(min_length=1)
    confirmed_risks: list[ConfirmedRiskSchema] = Field(default_factory=list)
    potential_risks: list[PotentialRiskSchema] = Field(default_factory=list)
    flag_opportunities: list[FlagOpportunitySchema] = Field(default_factory=list)


class AnalyzePRRiskAndOpportunitiesOutput(BaseModel):
    pull_request_id: UUID
    risk_score: int
    risk_level: RiskLevel
    confirmed_risks_count: int
    potential_risks_count: int
    flag_opportunities_count: int


class GenerateFlagWrappingPatchInput(BaseModel):
    repository_id: UUID
    pull_request_id: UUID | None = None
    flag_key: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    files_diff: list[FileDiffItem] = Field(min_length=1)
    generated_tests: list[GeneratedTestItem] = Field(default_factory=list)


class GenerateFlagWrappingPatchOutput(BaseModel):
    code_change_id: UUID
    status: str = "draft"
    files_count: int
    tests_count: int


class ValidatePatchIsolatedInput(BaseModel):
    code_change_id: UUID


class ValidatePatchIsolatedOutput(BaseModel):
    code_change_id: UUID
    validation_status: Literal["passed", "failed"]
    validation_report: dict[str, Any]


class GitHubCreateBranchAndDraftPRInput(BaseModel):
    code_change_id: UUID
    base_branch: str = Field(min_length=1)
    pr_title: str = Field(min_length=1, max_length=500)
    pr_body: str = ""


class GitHubCreateBranchAndDraftPROutput(BaseModel):
    code_change_id: UUID
    pr_number: int
    html_url: str
    branch_name: str
    draft: bool = True


class GenerateFlagCleanupPatchInput(BaseModel):
    repository_id: UUID
    feature_flag_id: UUID
    keep_branch: Literal["enabled", "disabled"]
    title: str | None = None
    description: str | None = None
    files_diff: list[FileDiffItem] = Field(min_length=1)
    generated_tests: list[GeneratedTestItem] = Field(default_factory=list)


class GenerateFlagCleanupPatchOutput(BaseModel):
    code_change_id: UUID
    status: str = "draft"
    keep_branch: Literal["enabled", "disabled"]


# ==============================================================================
# 3.2 flag_agent Tools (13 Tools)
# ==============================================================================


class FlagsmithListProjectsInput(BaseModel):
    pass


class FlagsmithListProjectsOutput(BaseModel):
    projects: list[dict[str, Any]] = Field(default_factory=list)


class FlagsmithListEnvironmentsInput(BaseModel):
    project_id: int = Field(ge=1)


class FlagsmithListEnvironmentsOutput(BaseModel):
    environments: list[dict[str, Any]] = Field(default_factory=list)


class FlagsmithListFlagsInput(BaseModel):
    project_id: int = Field(ge=1)
    environment_name: str | None = None


class FlagsmithListFlagsOutput(BaseModel):
    flags: list[dict[str, Any]] = Field(default_factory=list)


class FlagsmithGetFlagStateInput(BaseModel):
    flag_key: str = Field(min_length=1)
    environment_name: str = Field(min_length=1)
    identity_id: str | None = None


class FlagsmithGetFlagStateOutput(BaseModel):
    flag_key: str
    environment_name: str
    management_state: dict[str, Any] = Field(default_factory=dict)
    edge_state: dict[str, Any] | None = None


class FlagsmithCreateFlagInput(BaseModel):
    project_id: int = Field(ge=1)
    key: str = Field(min_length=1, max_length=128)
    description: str = ""
    flag_type: FlagType = FlagType.STANDARD
    initial_value: str | None = None
    mv_options: list[MultivariateOptionSchema] = Field(default_factory=list)


class FlagsmithCreateFlagOutput(BaseModel):
    feature_id: int
    key: str
    flag_type: FlagType


class FlagsmithUpsertPercentageSegmentInput(BaseModel):
    project_id: int = Field(ge=1)
    segment_name: str = Field(min_length=1)
    percentage: int = Field(ge=0, le=100)


class FlagsmithUpsertPercentageSegmentOutput(BaseModel):
    segment_id: int
    segment_name: str
    percentage: int


class FlagsmithToggleFlagInput(BaseModel):
    flag_key: str = Field(min_length=1)
    environment_name: str = Field(min_length=1)
    enabled: bool
    reason: str = Field(min_length=1)


class FlagsmithToggleFlagOutput(BaseModel):
    flag_key: str
    environment_name: str
    enabled: bool
    verified: bool


class FlagsmithSetGradualRolloutStageInput(BaseModel):
    flag_key: str = Field(min_length=1)
    environment_name: str = Field(min_length=1)
    target_percentage: Literal[0, 10, 25, 50, 75, 100]
    strategy: Literal["percentage_split_segment", "multivariate"] = "percentage_split_segment"
    reason: str = Field(min_length=1)


class FlagsmithSetGradualRolloutStageOutput(BaseModel):
    flag_key: str
    environment_name: str
    target_percentage: int
    strategy: str
    verified: bool


class FlagsmithConfigureMultivariateSplitInput(BaseModel):
    flag_key: str = Field(min_length=1)
    environment_name: str = Field(min_length=1)
    allocations: dict[str, int]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_allocations(self) -> FlagsmithConfigureMultivariateSplitInput:
        if not self.allocations:
            raise ValueError("allocations must not be empty.")
        for k, pct in self.allocations.items():
            if pct < 0 or pct > 100:
                raise ValueError(f"Allocation for '{k}' must be between 0 and 100.")
        if sum(self.allocations.values()) != 100:
            raise ValueError("Multivariate allocations must sum to exactly 100.")
        return self


class FlagsmithConfigureMultivariateSplitOutput(BaseModel):
    flag_key: str
    environment_name: str
    allocations: dict[str, int]
    verified: bool


class FlagsmithRollbackFlagInput(BaseModel):
    flag_key: str = Field(min_length=1)
    environment_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class FlagsmithRollbackFlagOutput(BaseModel):
    flag_key: str
    environment_name: str
    rolled_back: bool
    verified: bool


class FlagsmithVerifyLiveStateInput(BaseModel):
    flag_key: str = Field(min_length=1)
    environment_name: str = Field(min_length=1)
    expected_enabled: bool | None = None
    expected_percentage: int | None = Field(default=None, ge=0, le=100)


class FlagsmithVerifyLiveStateOutput(BaseModel):
    matches: bool
    actual_state: dict[str, Any] = Field(default_factory=dict)
    mismatches: list[str] = Field(default_factory=list)


class ScanFlagHealthInput(BaseModel):
    project_id: int | None = Field(default=None, ge=1)
    repository_id: UUID | None = None


class ScanFlagHealthOutput(BaseModel):
    flags_scanned: int
    unhealthy_flags: int
    results: list[dict[str, Any]] = Field(default_factory=list)


class FlagsmithDeleteOrArchiveFlagInput(BaseModel):
    flag_key: str = Field(min_length=1)
    action: Literal["archive", "delete"]
    reason: str = Field(min_length=1)


class FlagsmithDeleteOrArchiveFlagOutput(BaseModel):
    flag_key: str
    action: Literal["archive", "delete"]
    completed: bool


# ==============================================================================
# 3.3 experiment_agent Tools (10 Tools)
# ==============================================================================


class FlagsmithProbeExperimentCapabilityInput(BaseModel):
    environment_key: str | None = None


class FlagsmithProbeExperimentCapabilityOutput(BaseModel):
    analytics_mode: AnalyticsMode
    native_supported: bool
    details: dict[str, Any] = Field(default_factory=dict)


class CreateExperimentPlanInput(BaseModel):
    flag_key: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)
    hypothesis: str = Field(min_length=1)
    environment_name: str = Field(min_length=1, max_length=64)
    control_variation: str = "control"
    treatment_variation: str = "treatment"
    traffic_allocation: dict[str, int] = Field(
        default_factory=lambda: {"control": 50, "treatment": 50}
    )
    primary_metric: str = "checkout_completed"
    guardrail_metrics: list[str] = Field(
        default_factory=lambda: ["payment_failed", "checkout_abandoned"]
    )
    guardrail_thresholds: GuardrailThresholdsSchema | None = None


class CreateExperimentPlanOutput(BaseModel):
    experiment_id: UUID
    status: str = "draft"
    analytics_mode: AnalyticsMode


class ActivateExperimentInput(BaseModel):
    experiment_id: UUID


class ActivateExperimentOutput(BaseModel):
    experiment_id: UUID
    status: str = "running"


class StopOrConcludeExperimentInput(BaseModel):
    experiment_id: UUID
    outcome: Literal["completed", "paused", "rolled_back"]
    reason: str = Field(min_length=1)


class StopOrConcludeExperimentOutput(BaseModel):
    experiment_id: UUID
    status: str


class FetchExperimentEventsSummaryInput(BaseModel):
    experiment_id: UUID | None = None
    flag_key: str | None = None
    environment_name: str | None = None
    window_minutes: int = Field(default=1440, ge=1, le=43200)


class FetchExperimentEventsSummaryOutput(BaseModel):
    counts_by_variation: dict[str, dict[str, int]] = Field(default_factory=dict)
    total_events: int = 0


class ComputeBayesianExperimentAnalysisInput(BaseModel):
    experiment_id: UUID
    mc_samples: int = Field(default=50000, ge=100, le=500000)


class ComputeBayesianExperimentAnalysisOutput(BaseModel):
    experiment_id: UUID
    analysis: dict[str, Any] = Field(default_factory=dict)


class EvaluateReleaseRecommendationInput(BaseModel):
    experiment_id: UUID


class EvaluateReleaseRecommendationOutput(BaseModel):
    experiment_id: UUID
    recommendation: str
    triggers: list[str] = Field(default_factory=list)


class InvestigateReleaseIncidentInput(BaseModel):
    flag_key: str | None = None
    environment_name: str = Field(min_length=1)
    window_minutes: int = Field(default=60, ge=5, le=10080)


class InvestigateReleaseIncidentOutput(BaseModel):
    correlation_not_causation: bool = True
    observed_metrics: dict[str, Any] = Field(default_factory=dict)
    temporal_correlations: list[dict[str, Any]] = Field(default_factory=list)
    confounding_checks: list[dict[str, Any]] = Field(default_factory=list)


class GetRolloutTimelineInput(BaseModel):
    flag_key: str | None = None
    environment_name: str | None = None
    limit: int = Field(default=20, ge=1, le=200)


class GetRolloutTimelineOutput(BaseModel):
    rollout_entries: list[dict[str, Any]] = Field(default_factory=list)
    audit_entries: list[dict[str, Any]] = Field(default_factory=list)


class ProposeReleaseActionInput(BaseModel):
    flag_key: str = Field(min_length=1)
    environment_name: str = Field(min_length=1)
    proposed_action: Literal["expand", "reduce", "rollback"]
    target_percentage: int | None = Field(default=None, ge=0, le=100)
    rationale: str = Field(min_length=1)


class ProposeReleaseActionOutput(BaseModel):
    approval_id: UUID
    policy_category: str
    action_hash: str
    status: str = "pending"


# ==============================================================================
# Registry Mapping All 34 Tools
# ==============================================================================

class FlagsmithExplainIdentityInput(BaseModel):
    identifier: str = Field(min_length=1, max_length=128)
    environment_name: str = Field(default="development", min_length=1, max_length=64)
    traits: dict[str, Any] = Field(default_factory=dict)


class FlagsmithExplainIdentityOutput(BaseModel):
    identifier: str
    environment_name: str
    consistent_on_repeat_evaluation: bool
    flag_explanations: list[dict[str, Any]] = Field(default_factory=list)
    markdown_summary: str = ""
    evidence_note: str = ""


class PlanFeatureRequestRolloutInput(BaseModel):
    prompt: str = Field(min_length=8, max_length=4000)
    environment_name: str = Field(default="development", min_length=1, max_length=64)
    repository_id: UUID | None = None
    create_release: bool = True


class PlanFeatureRequestRolloutOutput(BaseModel):
    recommended_flag_key: str
    flag_type: str
    control_variation: str
    treatment_variation: str
    release_id: str | None = None
    release_key: str | None = None
    evidence_class: str = "AI_INFERRED"


TOOL_INPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    # pr_agent (11)
    "github_list_repositories": GitHubListRepositoriesInput,
    "github_list_branches": GitHubListBranchesInput,
    "github_list_pull_requests": GitHubListPullRequestsInput,
    "github_get_pull_request_diff": GitHubGetPullRequestDiffInput,
    "github_get_file_contents": GitHubGetFileContentsInput,
    "github_search_repo_code": GitHubSearchRepoCodeInput,
    "analyze_pr_risk_and_opportunities": AnalyzePRRiskAndOpportunitiesInput,
    "generate_flag_wrapping_patch": GenerateFlagWrappingPatchInput,
    "validate_patch_isolated": ValidatePatchIsolatedInput,
    "github_create_branch_and_draft_pr": GitHubCreateBranchAndDraftPRInput,
    "generate_flag_cleanup_patch": GenerateFlagCleanupPatchInput,
    # flag_agent (13)
    "flagsmith_list_projects": FlagsmithListProjectsInput,
    "flagsmith_list_environments": FlagsmithListEnvironmentsInput,
    "flagsmith_list_flags": FlagsmithListFlagsInput,
    "flagsmith_get_flag_state": FlagsmithGetFlagStateInput,
    "flagsmith_create_flag": FlagsmithCreateFlagInput,
    "flagsmith_upsert_percentage_segment": FlagsmithUpsertPercentageSegmentInput,
    "flagsmith_toggle_flag": FlagsmithToggleFlagInput,
    "flagsmith_set_gradual_rollout_stage": FlagsmithSetGradualRolloutStageInput,
    "flagsmith_configure_multivariate_split": FlagsmithConfigureMultivariateSplitInput,
    "flagsmith_rollback_flag": FlagsmithRollbackFlagInput,
    "flagsmith_verify_live_state": FlagsmithVerifyLiveStateInput,
    "scan_flag_health": ScanFlagHealthInput,
    "flagsmith_delete_or_archive_flag": FlagsmithDeleteOrArchiveFlagInput,
    "flagsmith_explain_identity": FlagsmithExplainIdentityInput,
    "plan_feature_request_rollout": PlanFeatureRequestRolloutInput,
    # experiment_agent (10)
    "flagsmith_probe_experiment_capability": FlagsmithProbeExperimentCapabilityInput,
    "create_experiment_plan": CreateExperimentPlanInput,
    "activate_experiment": ActivateExperimentInput,
    "stop_or_conclude_experiment": StopOrConcludeExperimentInput,
    "fetch_experiment_events_summary": FetchExperimentEventsSummaryInput,
    "compute_bayesian_experiment_analysis": ComputeBayesianExperimentAnalysisInput,
    "evaluate_release_recommendation": EvaluateReleaseRecommendationInput,
    "investigate_release_incident": InvestigateReleaseIncidentInput,
    "get_rollout_timeline": GetRolloutTimelineInput,
    "propose_release_action": ProposeReleaseActionInput,
}

TOOL_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "github_list_repositories": GitHubListRepositoriesOutput,
    "github_list_branches": GitHubListBranchesOutput,
    "github_list_pull_requests": GitHubListPullRequestsOutput,
    "github_get_pull_request_diff": GitHubGetPullRequestDiffOutput,
    "github_get_file_contents": GitHubGetFileContentsOutput,
    "github_search_repo_code": GitHubSearchRepoCodeOutput,
    "analyze_pr_risk_and_opportunities": AnalyzePRRiskAndOpportunitiesOutput,
    "generate_flag_wrapping_patch": GenerateFlagWrappingPatchOutput,
    "validate_patch_isolated": ValidatePatchIsolatedOutput,
    "github_create_branch_and_draft_pr": GitHubCreateBranchAndDraftPROutput,
    "generate_flag_cleanup_patch": GenerateFlagCleanupPatchOutput,
    "flagsmith_list_projects": FlagsmithListProjectsOutput,
    "flagsmith_list_environments": FlagsmithListEnvironmentsOutput,
    "flagsmith_list_flags": FlagsmithListFlagsOutput,
    "flagsmith_get_flag_state": FlagsmithGetFlagStateOutput,
    "flagsmith_create_flag": FlagsmithCreateFlagOutput,
    "flagsmith_upsert_percentage_segment": FlagsmithUpsertPercentageSegmentOutput,
    "flagsmith_toggle_flag": FlagsmithToggleFlagOutput,
    "flagsmith_set_gradual_rollout_stage": FlagsmithSetGradualRolloutStageOutput,
    "flagsmith_configure_multivariate_split": FlagsmithConfigureMultivariateSplitOutput,
    "flagsmith_rollback_flag": FlagsmithRollbackFlagOutput,
    "flagsmith_verify_live_state": FlagsmithVerifyLiveStateOutput,
    "scan_flag_health": ScanFlagHealthOutput,
    "flagsmith_delete_or_archive_flag": FlagsmithDeleteOrArchiveFlagOutput,
    "flagsmith_explain_identity": FlagsmithExplainIdentityOutput,
    "plan_feature_request_rollout": PlanFeatureRequestRolloutOutput,
    "flagsmith_probe_experiment_capability": FlagsmithProbeExperimentCapabilityOutput,
    "create_experiment_plan": CreateExperimentPlanOutput,
    "activate_experiment": ActivateExperimentOutput,
    "stop_or_conclude_experiment": StopOrConcludeExperimentOutput,
    "fetch_experiment_events_summary": FetchExperimentEventsSummaryOutput,
    "compute_bayesian_experiment_analysis": ComputeBayesianExperimentAnalysisOutput,
    "evaluate_release_recommendation": EvaluateReleaseRecommendationOutput,
    "investigate_release_incident": InvestigateReleaseIncidentOutput,
    "get_rollout_timeline": GetRolloutTimelineOutput,
    "propose_release_action": ProposeReleaseActionOutput,
}


def validate_tool_args(tool_name: str, args: dict[str, Any]) -> BaseModel:
    """Validate proposed tool arguments against the canonical Pydantic v2 schema."""
    schema_cls = TOOL_INPUT_SCHEMAS.get(tool_name)
    if schema_cls is None:
        raise ValueError(f"Unknown or unallowlisted tool '{tool_name}'.")
    return schema_cls.model_validate(args)
