"""Pydantic v2 schemas for experiments, Bayesian analytics, event ingest, and incidents."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    AnalyticsMode,
    ExperimentEventType,
    ExperimentStatus,
    ReleaseRecommendation,
    RolloutAction,
)
from app.schemas.common import ORMBaseModel
from app.schemas.settings import GuardrailThresholdsSchema


class ExperimentPlanRequest(BaseModel):
    """Natural-language experiment plan request."""

    prompt: str = Field(min_length=1, max_length=2000)
    feature_flag_id: UUID | None = None
    environment_name: str | None = None


class ExperimentActivateRequest(BaseModel):
    """Activate an experiment in a target environment."""

    environment_name: str = Field(min_length=1, max_length=64)


class ExperimentStopRequest(BaseModel):
    """Stop or conclude a running experiment."""

    outcome: Literal["completed", "paused", "rolled_back"] = "completed"
    reason: str = Field(default="Operator concluded experiment", min_length=1)


class ExperimentOut(ORMBaseModel):
    """Experiment record representation."""

    id: UUID
    feature_flag_id: UUID
    name: str
    hypothesis: str
    environment_name: str
    environment_key: str | None = None
    analytics_mode: AnalyticsMode
    flagsmith_experiment_id: str | None = None
    status: ExperimentStatus
    control_variation: str
    treatment_variation: str
    traffic_allocation: dict[str, int]
    primary_metric: str
    guardrail_metrics: list[str]
    guardrail_thresholds: dict[str, Any]
    latest_bayesian_result: dict[str, Any] | None = None
    latest_recommendation: ReleaseRecommendation | None = None
    recommendation_rationale: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SampleAdequacySchema(BaseModel):
    """Sample adequacy status against min_exposures."""

    control_exposures: int = Field(ge=0)
    treatment_exposures: int = Field(ge=0)
    min_required: int = Field(ge=1)
    is_adequate: bool


class VariantPosteriorSchema(BaseModel):
    """Beta-Binomial posterior metrics for one experiment arm."""

    exposures: int = Field(ge=0)
    conversions: int = Field(ge=0)
    posterior_alpha: float = Field(gt=0.0)
    posterior_beta: float = Field(gt=0.0)
    mean_rate: float = Field(ge=0.0, le=1.0)
    credible_interval_95: tuple[float, float]


class BayesianComparisonSchema(BaseModel):
    """Treatment vs Control Bayesian comparison metrics."""

    prob_treatment_beats_control: float = Field(ge=0.0, le=1.0)
    absolute_lift_mean: float
    absolute_lift_cri_95: tuple[float, float]
    relative_lift_mean: float
    relative_lift_cri_95: tuple[float, float]
    expected_loss_choose_treatment: float = Field(ge=0.0)
    expected_loss_choose_control: float = Field(ge=0.0)


class PaymentFailedGuardrailSchema(BaseModel):
    """Error guardrail posterior comparison."""

    control_errors: int = Field(ge=0)
    control_rate: float = Field(ge=0.0, le=1.0)
    treatment_errors: int = Field(ge=0)
    treatment_rate: float = Field(ge=0.0, le=1.0)
    prob_treatment_exceeds_soft_delta: float = Field(ge=0.0, le=1.0)
    prob_treatment_exceeds_hard_delta: float = Field(ge=0.0, le=1.0)
    prob_treatment_exceeds_max_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    breached: bool


class AbandonedGuardrailSchema(BaseModel):
    """Abandonment guardrail summary."""

    control_count: int = Field(ge=0)
    treatment_count: int = Field(ge=0)


class BayesianAnalysisResponse(BaseModel):
    """Bayesian Beta-Binomial output matching docs/api.md Sec 2.7."""

    experiment_id: UUID
    analytics_mode: AnalyticsMode
    sample_adequacy: SampleAdequacySchema
    control: VariantPosteriorSchema
    treatment: VariantPosteriorSchema
    comparison: BayesianComparisonSchema
    guardrails: dict[str, Any]


class RecommendationResponse(BaseModel):
    """Deterministic 5-state release recommendation plus explanation."""

    experiment_id: UUID
    recommendation: ReleaseRecommendation
    triggers: list[str] = Field(default_factory=list)
    confidence_summary: str
    preconditions: list[str] = Field(default_factory=list)
    potential_consequences: list[str] = Field(default_factory=list)
    narrative: str = ""
    guardrail_thresholds: GuardrailThresholdsSchema | None = None


class IngestEventItem(BaseModel):
    """Single demo-app or service telemetry event matching docs/api.md Sec 2.8."""

    event_id: str = Field(min_length=1, max_length=128)
    flag_key: str = Field(min_length=1, max_length=128)
    environment_name: str = Field(min_length=1, max_length=64)
    variation_key: str = Field(min_length=1, max_length=64)
    event_type: ExperimentEventType
    user_identifier: str = Field(min_length=1, max_length=128)
    session_key: str | None = Field(default=None, max_length=128)
    numeric_value: float | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class EventIngestRequest(BaseModel):
    """Batch event ingestion request (1..500 items)."""

    events: list[IngestEventItem] = Field(min_length=1, max_length=500)


class EventIngestResponse(BaseModel):
    """Batch ingestion deduplication counts."""

    accepted: int = Field(ge=0)
    deduplicated: int = Field(ge=0)


class EventSummaryBucket(BaseModel):
    """Time-series event bucket for Recharts charts."""

    bucket_start: datetime
    variation_key: str
    event_type: ExperimentEventType
    count: int = Field(ge=0)


class EventSummaryResponse(BaseModel):
    """Aggregated telemetry counts and time-series buckets."""

    counts_by_variation: dict[str, dict[str, int]] = Field(default_factory=dict)
    buckets: list[EventSummaryBucket] = Field(default_factory=list)
    total_events: int = Field(ge=0)


class IncidentInvestigateRequest(BaseModel):
    """Request to investigate error/abandonment spikes."""

    flag_key: str | None = None
    environment_name: str = Field(min_length=1, max_length=64)
    window_minutes: int = Field(default=60, ge=5, le=10080)


class TemporalCorrelationItem(BaseModel):
    """Ranked rollout or PR event temporally near spike onset."""

    source_type: Literal["rollout", "pull_request"]
    source_id: str
    timestamp: datetime
    delta_minutes: float
    correlation_score: float = Field(ge=0.0, le=1.0)
    summary: str
    correlation_not_causation: bool = True


class ConfoundingCheckItem(BaseModel):
    """Variation-stratified confounder analysis."""

    check_name: str
    likely_confounder: bool
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class IncidentInvestigateResponse(BaseModel):
    """Incident investigation response upholding correlation != causation."""

    environment_name: str
    flag_key: str | None = None
    correlation_not_causation: bool = True
    observed_metrics: dict[str, Any] = Field(default_factory=dict)
    temporal_correlations: list[TemporalCorrelationItem] = Field(default_factory=list)
    confounding_checks: list[ConfoundingCheckItem] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)


class RolloutHistoryOut(ORMBaseModel):
    """Immutable rollout history entry."""

    id: UUID
    feature_flag_id: UUID
    approval_id: UUID | None = None
    actor_id: UUID | None = None
    environment_name: str
    action: RolloutAction
    previous_state: dict[str, Any]
    new_state: dict[str, Any]
    verified: bool
    verification_details: dict[str, Any]
    reason: str | None = None
    created_at: datetime


class ReleasesOverviewResponse(BaseModel):
    """Releases control plane summary."""

    active_rollouts: list[dict[str, Any]] = Field(default_factory=list)
    recent_history: list[RolloutHistoryOut] = Field(default_factory=list)
    recommendations: list[RecommendationResponse] = Field(default_factory=list)
    incident_alerts: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ensure_lists(self) -> ReleasesOverviewResponse:
        return self
