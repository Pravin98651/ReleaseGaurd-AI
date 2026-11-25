"""Pydantic v2 schemas for unified Dashboard, Metrics & Analytics, Activity, and Pending Actions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

TimeRangePreset = Literal["24h", "7d", "30d", "90d", "custom"]
DataSourceTag = Literal[
    "live_external",
    "application_telemetry",
    "calculated",
    "simulator_generated",
    "database",
    "unavailable",
]


class MetricTrend(BaseModel):
    """Historical period-over-period delta emitted only when prior history exists."""

    previous_value: float
    delta: float
    percent_change: float | None = None
    direction: Literal["up", "down", "flat"]


class EngineeringMetricsBlock(BaseModel):
    connected_repositories: int = 0
    synchronized_prs: int = 0
    prs_analyzed: int = 0
    pending_pr_analyses: int = 0
    draft_prs_created: int = 0
    failed_analysis_tasks: int = 0
    trends: dict[str, MetricTrend] = Field(default_factory=dict)


class ReleaseOperationsMetricsBlock(BaseModel):
    total_releases: int = 0
    releases_in_progress: int = 0
    successful_releases: int = 0
    failed_releases: int = 0
    rollbacks_executed: int = 0
    releases_awaiting_approval: int = 0
    release_success_rate: float | None = None
    trends: dict[str, MetricTrend] = Field(default_factory=dict)


class FeatureFlagMetricsBlock(BaseModel):
    total_flags: int = 0
    enabled_flags: int = 0
    disabled_flags: int = 0
    gradual_rollout_flags: int = 0
    flags_with_health_issues: int = 0
    pending_flag_changes: int = 0
    average_health_score: float | None = None


class ExperimentationMetricsBlock(BaseModel):
    total_experiments: int = 0
    running_experiments: int = 0
    completed_experiments: int = 0
    paused_experiments: int = 0
    positive_recommendation_experiments: int = 0
    breached_guardrail_experiments: int = 0


class ApprovalActivityMetricsBlock(BaseModel):
    pending_approvals: int = 0
    approved_operations: int = 0
    rejected_operations: int = 0
    expired_approvals: int = 0


class AgentActivityMetricsBlock(BaseModel):
    total_agent_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    running_executions: int = 0
    cancelled_executions: int = 0
    awaiting_approval_executions: int = 0
    average_execution_duration_seconds: float | None = None
    agent_tool_calls: int = 0
    total_tokens: int | None = None


class FilterWindowMeta(BaseModel):
    range: TimeRangePreset = "7d"
    start_at: datetime
    end_at: datetime
    environment: str | None = None
    repository_id: UUID | None = None
    flag_key: str | None = None
    experiment_id: UUID | None = None
    event_source: Literal["all", "simulator_generated", "application_telemetry"] = "all"


class DashboardMetricsOut(BaseModel):
    filter: FilterWindowMeta
    engineering: EngineeringMetricsBlock
    releases: ReleaseOperationsMetricsBlock
    feature_flags: FeatureFlagMetricsBlock
    experiments: ExperimentationMetricsBlock
    approvals: ApprovalActivityMetricsBlock
    agents: AgentActivityMetricsBlock
    sources: dict[str, DataSourceTag] = Field(default_factory=dict)


class ReleaseActivityPoint(BaseModel):
    bucket: str
    label: str
    releases_created: int = 0
    releases_completed: int = 0
    releases_failed: int = 0
    rollbacks: int = 0


class ExperimentPerformancePoint(BaseModel):
    experiment_id: UUID
    name: str
    flag_key: str | None = None
    environment_name: str
    status: str
    analytics_mode: str
    primary_metric: str
    recommendation: str | None = None
    hypothesis: str
    traffic_allocation: dict[str, int] = Field(default_factory=dict)
    control_exposures: int = 0
    control_conversions: int = 0
    control_rate: float = 0.0
    control_ci_95: list[float] = Field(default_factory=list)
    treatment_exposures: int = 0
    treatment_conversions: int = 0
    treatment_rate: float = 0.0
    treatment_ci_95: list[float] = Field(default_factory=list)
    absolute_improvement: float = 0.0
    relative_improvement: float = 0.0
    prob_treatment_beats_control: float = 0.5
    expected_loss_treatment: float = 0.0
    payment_failed_breached: bool = False
    payment_failed_severity: str = "none"
    control_error_rate: float = 0.0
    treatment_error_rate: float = 0.0
    sample_adequate: bool = False
    started_at: datetime | None = None
    ended_at: datetime | None = None
    simulator_event_count: int = 0
    telemetry_event_count: int = 0


class DistributionSlice(BaseModel):
    key: str
    name: str
    count: int


class AgentExecutionPoint(BaseModel):
    bucket: str
    label: str
    successful: int = 0
    failed: int = 0
    running: int = 0
    awaiting_approval: int = 0
    avg_duration_seconds: float | None = None


class TelemetryEventPoint(BaseModel):
    bucket: str
    label: str
    total_events: int = 0
    checkout_started: int = 0
    checkout_completed: int = 0
    payment_failed: int = 0
    checkout_abandoned: int = 0
    simulator_events: int = 0
    telemetry_events: int = 0


class DashboardChartsOut(BaseModel):
    filter: FilterWindowMeta
    release_activity: list[ReleaseActivityPoint] = Field(default_factory=list)
    experiment_performance: list[ExperimentPerformancePoint] = Field(default_factory=list)
    flag_distribution: list[DistributionSlice] = Field(default_factory=list)
    agent_activity: list[AgentExecutionPoint] = Field(default_factory=list)
    telemetry_activity: list[TelemetryEventPoint] = Field(default_factory=list)
    release_health: list[DistributionSlice] = Field(default_factory=list)
    telemetry_totals: dict[str, int] = Field(default_factory=dict)


class ActivityFeedItem(BaseModel):
    id: str
    event_type: str
    title: str
    description: str
    timestamp: datetime
    resource_type: str
    resource_id: str | None = None
    resource_label: str | None = None
    environment_name: str | None = None
    actor: str
    status: str
    href: str


class PendingActionItem(BaseModel):
    id: str
    category: str
    severity: Literal["critical", "high", "medium", "info"]
    title: str
    description: str
    resource_type: str
    resource_id: str | None = None
    environment_name: str | None = None
    created_at: datetime
    action_label: str
    href: str


class DashboardOverviewOut(BaseModel):
    metrics: DashboardMetricsOut
    charts: DashboardChartsOut
    recent_activity: list[ActivityFeedItem] = Field(default_factory=list)
    pending_actions: list[PendingActionItem] = Field(default_factory=list)
    integration_status: dict[str, Any] = Field(default_factory=dict)
    upcoming_schedules: list[dict[str, Any]] = Field(default_factory=list)
    data_sources: dict[str, Any] = Field(default_factory=dict)


class MetricDefinitionDoc(BaseModel):
    name: str
    definition: str
    formula: str
    source: str
    time_window: str
    filters: list[str] = Field(default_factory=list)
    limitations: str
