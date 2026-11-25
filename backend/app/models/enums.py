"""Portable StrEnum definitions and default payload factories for ORM models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    RELEASE_MANAGER = "release_manager"
    ENGINEER = "engineer"
    VIEWER = "viewer"


class IntegrationProvider(StrEnum):
    GITHUB = "github"
    FLAGSMITH = "flagsmith"
    SLACK = "slack"
    DISCORD = "discord"


class IntegrationAuthMode(StrEnum):
    OAUTH = "oauth"
    PAT = "pat"
    ORG_TOKEN = "org_token"
    GITHUB_APP = "github_app"
    WEBHOOK = "webhook"


class IntegrationStatus(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    EXPIRED = "expired"


class PullRequestState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SpecialistRole(StrEnum):
    SUPERVISOR = "supervisor"
    PR_AGENT = "pr_agent"
    FLAG_AGENT = "flag_agent"
    EXPERIMENT_AGENT = "experiment_agent"


class AgentSessionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED_APPROVAL = "paused_approval"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class AgentTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CodeChangeType(StrEnum):
    FLAG_WRAP = "flag_wrap"
    FLAG_CLEANUP = "flag_cleanup"
    EXPERIMENT_INSTRUMENTATION = "experiment_instrumentation"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class CodeChangeStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    VALIDATION_FAILED = "validation_failed"
    PR_OPENED = "pr_opened"
    DISCARDED = "discarded"


class FlagType(StrEnum):
    STANDARD = "STANDARD"
    MULTIVARIATE = "MULTIVARIATE"


class AnalyticsMode(StrEnum):
    FLAGSMITH_NATIVE = "flagsmith_native"
    TRACKED_EVENT_FALLBACK = "tracked_event_fallback"


class ExperimentStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"


class ReleaseRecommendation(StrEnum):
    CONTINUE_MONITORING = "Continue Monitoring"
    EXPAND_ROLLOUT = "Expand Rollout"
    MAINTAIN_CURRENT_ROLLOUT = "Maintain Current Rollout"
    REDUCE_ROLLOUT = "Reduce Rollout"
    RECOMMEND_ROLLBACK = "Recommend Rollback"


class ExperimentEventType(StrEnum):
    FEATURE_VARIATION_ASSIGNED = "feature_variation_assigned"
    CHECKOUT_STARTED = "checkout_started"
    CHECKOUT_COMPLETED = "checkout_completed"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_COMPLETED = "payment_completed"
    PRODUCT_CLICKED_FROM_RECOMMENDATION = "product_clicked_from_recommendation"
    ADD_TO_CART_FROM_RECOMMENDATION = "add_to_cart_from_recommendation"
    SEARCH_RESULT_CLICKED = "search_result_clicked"
    PRODUCT_ADDED_AFTER_SEARCH = "product_added_after_search"


class ApprovalCategory(StrEnum):
    B = "B"
    C = "C"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVALIDATION_FAILED = "revalidation_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RolloutAction(StrEnum):
    ENABLE = "enable"
    DISABLE = "disable"
    ROLLOUT_STEP = "rollout_step"
    MULTIVARIATE_UPDATE = "multivariate_update"
    SEGMENT_OVERRIDE = "segment_override"
    ROLLBACK = "rollback"
    VERSION_PUBLISH = "version_publish"


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    DEMO_EMITTER = "demo_emitter"


class PolicyCategory(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    DENY = "DENY"


class AuditStatus(StrEnum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    PENDING = "pending"


class ScheduleJobType(StrEnum):
    REPO_SYNC = "repo_sync"
    PR_ANALYSIS = "pr_analysis"
    FLAG_HEALTH_SCAN = "flag_health_scan"
    EXPERIMENT_MONITOR = "experiment_monitor"
    RELEASE_MONITOR = "release_monitor"
    AI_MAINTENANCE_REPORT = "ai_maintenance_report"
    CUSTOM_AGENT_TASK = "custom_agent_task"


class ScheduleFrequency(StrEnum):
    ONCE = "once"
    INTERVAL_MINUTES = "interval_minutes"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CRON = "cron"


class JobExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class JobTriggerType(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    MISFIRE_RECOVERY = "misfire_recovery"
    RETRY = "retry"


class NotificationCategory(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DECIDED = "approval_decided"
    GUARDRAIL_BREACH = "guardrail_breach"
    ROLLBACK_TRIGGERED = "rollback_triggered"
    PR_HIGH_RISK = "pr_high_risk"
    DRAFT_PR_CREATED = "draft_pr_created"
    AGENT_FAILED = "agent_failed"
    FLAG_HEALTH_ALERT = "flag_health_alert"
    SCHEDULE_COMPLETED = "schedule_completed"
    SCHEDULE_FAILED = "schedule_failed"
    INTEGRATION_ERROR = "integration_error"
    SYSTEM_ALERT = "system_alert"
    RELEASE_LIFECYCLE = "release_lifecycle"
    RELEASE_BLOCKED = "release_blocked"
    RELEASE_COMPLETED = "release_completed"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_PAUSED = "agent_paused"
    AGENT_RESUMED = "agent_resumed"
    AGENT_RECOVERED = "agent_recovered"
    AGENT_TOOL_FAILED = "agent_tool_failed"
    RISK_CRITICAL = "risk_critical"
    RISK_ASSESSMENT_OUTDATED = "risk_assessment_outdated"
    ROLLOUT_STAGE_ACTIVATED = "rollout_stage_activated"
    ROLLOUT_GUARDRAIL_BREACH = "rollout_guardrail_breach"
    ROLLOUT_COMPLETED = "rollout_completed"
    INTELLIGENCE_INSIGHT = "intelligence_insight"
    POLICY_RECOMMENDATION = "policy_recommendation"
    WEBHOOK_FAILED = "webhook_failed"
    REPORT_SHARED = "report_shared"


class ReleaseStatus(StrEnum):
    DRAFT = "draft"
    INITIALIZING = "initializing"
    ANALYZING = "analyzing"
    AWAITING_PATCH = "awaiting_patch"
    VALIDATING = "validating"
    READY_FOR_FLAG = "ready_for_flag"
    FLAG_PROVISIONING = "flag_provisioning"
    EXPERIMENT_RUNNING = "experiment_running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ROLLING_OUT = "rolling_out"
    MONITORING = "monitoring"
    COMPLETED = "completed"
    PAUSED = "paused"
    BLOCKED = "blocked"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class ReleaseType(StrEnum):
    FEATURE = "feature"
    HOTFIX = "hotfix"
    EXPERIMENT = "experiment"
    CONFIG = "config"
    ROLLBACK_RECOVERY = "rollback_recovery"


class ReleaseStageKey(StrEnum):
    DRAFT = "draft"
    PR_ANALYSIS = "pr_analysis"
    PATCH_VALIDATION = "patch_validation"
    FLAG_SETUP = "flag_setup"
    EXPERIMENT_MONITORING = "experiment_monitoring"
    APPROVAL_GATE = "approval_gate"
    ROLLOUT_EXECUTION = "rollout_execution"
    POST_RELEASE_VERIFICATION = "post_release_verification"
    COMPLETED = "completed"


class ReleaseStageStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ReleaseReadinessStatus(StrEnum):
    READY = "ready"
    NEEDS_ATTENTION = "needs_attention"
    BLOCKED = "blocked"
    NOT_READY = "not_ready"


class ReleaseCheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    PENDING = "pending"
    SKIPPED = "skipped"


class ReleaseArtifactType(StrEnum):
    PR_ANALYSIS = "pr_analysis"
    CODE_PATCH = "code_patch"
    VALIDATION_REPORT = "validation_report"
    FLAG_CONFIG = "flag_config"
    EXPERIMENT_REPORT = "experiment_report"
    APPROVAL_SNAPSHOT = "approval_snapshot"
    RELEASE_REPORT = "release_report"
    INCIDENT_REPORT = "incident_report"


class AgentExecutionStatus(StrEnum):
    QUEUED = "queued"
    INITIALIZING = "initializing"
    PLANNING = "planning"
    RUNNING = "running"
    TOOL_RUNNING = "tool_running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_INPUT = "waiting_for_input"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    RESUMING = "resuming"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    RECOVERING = "recovering"


class AgentStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class AgentToolCallStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class AgentDecisionSource(StrEnum):
    AI_RECOMMENDATION = "ai_recommendation"
    DETERMINISTIC_RULE = "deterministic_rule"
    VERIFIED_FACT = "verified_fact"
    HUMAN_DECISION = "human_decision"


class AgentDecisionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class RiskAssessmentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    OUTDATED = "outdated"


class RiskClassification(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


class DimensionStatus(StrEnum):
    EVALUATED = "evaluated"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class DataSufficiency(StrEnum):
    SUFFICIENT = "sufficient"
    LIMITED = "limited"
    INSUFFICIENT_DATA = "insufficient_data"
    UNAVAILABLE = "unavailable"


class MitigationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"


class FlagSuitability(StrEnum):
    HIGHLY_SUITABLE = "highly_suitable"
    SUITABLE_WITH_CHANGES = "suitable_with_changes"
    LIMITED_SUITABILITY = "limited_suitability"
    NOT_SUITABLE = "not_suitable"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class ProgressiveRolloutStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    READY = "ready"
    AWAITING_APPROVAL = "awaiting_approval"
    SCHEDULED = "scheduled"
    INITIALIZING = "initializing"
    ROLLING_OUT = "rolling_out"
    OBSERVING = "observing"
    EVALUATING = "evaluating"
    ADVANCE_RECOMMENDED = "advance_recommended"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    ROLLBACK_RECOMMENDED = "rollback_recommended"
    AWAITING_ROLLBACK_APPROVAL = "awaiting_rollback_approval"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class ProgressiveStageStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ACTIVATING = "activating"
    ACTIVE = "active"
    OBSERVING = "observing"
    EVALUATING = "evaluating"
    PASSED = "passed"
    FAILED = "failed"
    PAUSED = "paused"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class GuardrailOperator(StrEnum):
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    EQUALS = "equals"
    NOT_EQUAL = "not_equal"
    RATE_INCREASE = "rate_increase"
    PERCENTAGE_CHANGE = "percentage_change"
    ABSOLUTE_CHANGE = "absolute_change"


class GuardrailEvalStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    BREACH = "breach"
    INSUFFICIENT_DATA = "insufficient_data"
    STALE_DATA = "stale_data"
    EVALUATION_ERROR = "evaluation_error"


class RolloutDecisionType(StrEnum):
    ADVANCE_RECOMMENDED = "advance_recommended"
    CONTINUE_OBSERVATION = "continue_observation"
    PAUSE_RECOMMENDED = "pause_recommended"
    ROLLBACK_RECOMMENDED = "rollback_recommended"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class RollbackOperationStatus(StrEnum):
    RECOMMENDED = "recommended"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ReleaseOutcomeType(StrEnum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    ROLLBACK = "rollback"
    CANCELLED_PRE_DEPLOY = "cancelled_pre_deploy"
    CANCELLED_POST_DEPLOY = "cancelled_post_deploy"
    UNKNOWN = "unknown"


class FailureCategory(StrEnum):
    CODE_REGRESSION = "code_regression"
    DEPENDENCY_INCOMPATIBILITY = "dependency_incompatibility"
    MISSING_TESTS = "missing_tests"
    INSUFFICIENT_TEST_COVERAGE = "insufficient_test_coverage"
    CONFIGURATION_ERROR = "configuration_error"
    FEATURE_FLAG_MISCONFIGURATION = "feature_flag_misconfiguration"
    DATABASE_MIGRATION_PROBLEM = "database_migration_problem"
    API_CONTRACT_BREAKAGE = "api_contract_breakage"
    AUTH_FAILURE = "auth_failure"
    PERFORMANCE_REGRESSION = "performance_regression"
    MEMORY_RESOURCE_EXHAUSTION = "memory_resource_exhaustion"
    PAYMENT_FAILURE = "payment_failure"
    INTEGRATION_FAILURE = "integration_failure"
    INCORRECT_ROLLOUT_CONFIG = "incorrect_rollout_config"
    INADEQUATE_MONITORING = "inadequate_monitoring"
    INCOMPLETE_ROLLBACK_STRATEGY = "incomplete_rollback_strategy"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    UNKNOWN = "unknown"


class EvidenceClassification(StrEnum):
    VERIFIED = "verified"
    OBSERVED = "observed"
    HUMAN_REPORTED = "human_reported"
    AI_INFERRED = "ai_inferred"
    SIMULATED = "simulated"
    UNKNOWN = "unknown"


class RecommendationOutcomeClass(StrEnum):
    VERIFIED_BENEFIT = "verified_benefit"
    OBSERVED_POSITIVE_OUTCOME = "observed_positive_outcome"
    NO_MEASURABLE_IMPACT = "no_measurable_impact"
    NEGATIVE_OUTCOME = "negative_outcome"
    INCONCLUSIVE = "inconclusive"
    NOT_EVALUABLE = "not_evaluable"


class PolicyReviewStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"


class PolicyArea(StrEnum):
    RISK_THRESHOLDS = "risk_thresholds"
    REQUIRED_TESTS = "required_tests"
    APPROVAL_REQUIREMENTS = "approval_requirements"
    FEATURE_FLAG_USAGE = "feature_flag_usage"
    INITIAL_ROLLOUT_PERCENTAGE = "initial_rollout_percentage"
    OBSERVATION_WINDOWS = "observation_windows"
    GUARDRAIL_THRESHOLDS = "guardrail_thresholds"
    DEPENDENCY_VALIDATION = "dependency_validation"
    ROLLBACK_REQUIREMENTS = "rollback_requirements"
    MONITORING_REQUIREMENTS = "monitoring_requirements"


class RollbackCauseCategory(StrEnum):
    FUNCTIONAL_REGRESSION = "functional_regression"
    PAYMENT_FAILURE = "payment_failure"
    PERFORMANCE_DEGRADATION = "performance_degradation"
    INCREASED_ERROR_RATE = "increased_error_rate"
    DATA_INTEGRITY_ISSUE = "data_integrity_issue"
    CONFIGURATION_PROBLEM = "configuration_problem"
    DEPENDENCY_INCOMPATIBILITY = "dependency_incompatibility"
    SECURITY_ACCESS_ISSUE = "security_access_issue"
    FEATURE_FLAG_PROBLEM = "feature_flag_problem"
    INSUFFICIENT_TESTING = "insufficient_testing"
    UNEXPECTED_INTEGRATION = "unexpected_integration"
    UX_DEGRADATION = "ux_degradation"
    UNKNOWN = "unknown"


class AnalysisRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INSUFFICIENT_DATA = "insufficient_data"


class GitHubCheckStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class GitHubCheckConclusion(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    NEUTRAL = "neutral"
    CANCELLED = "cancelled"
    ACTION_REQUIRED = "action_required"


class WebhookProcessingStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    IGNORED_DUPLICATE = "ignored_duplicate"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"
    RATE_LIMITED = "rate_limited"


class ReportReadinessOutcome(StrEnum):
    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class DependencyType(StrEnum):
    LIBRARY_DEPENDENCY = "library_dependency"
    API_DEPENDENCY = "api_dependency"
    DATABASE_DEPENDENCY = "database_dependency"
    EVENT_DEPENDENCY = "event_dependency"
    SERVICE_DEPENDENCY = "service_dependency"
    SHARED_CONFIGURATION = "shared_configuration"
    UNKNOWN = "unknown"


class DependencyVerificationStatus(StrEnum):
    DISCOVERED = "discovered"
    VERIFIED = "verified"
    REJECTED = "rejected"


class CoordinatedPlanStatus(StrEnum):
    DRAFT = "draft"
    ANALYZING = "analyzing"
    READY = "ready"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RepoHealthCategory(StrEnum):
    HEALTHY = "healthy"
    STABLE = "stable"
    NEEDS_ATTENTION = "needs_attention"
    HIGH_RISK = "high_risk"
    INSUFFICIENT_DATA = "insufficient_data"


def str_enum(enum_cls: type[StrEnum], length: int = 50, **kwargs: Any) -> SAEnum:
    """Create a portable non-native SQLAlchemy Enum storing StrEnum values."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
        **kwargs,
    )


def default_guardrail_thresholds() -> dict[str, Any]:
    """Unified guardrail threshold keys per docs/database-and-policies.md & #9."""
    return {
        "min_exposures": 100,
        "max_expected_loss": 0.005,
        "guardrail_max_error_rate": 0.05,
        "soft_error_delta": 0.015,
        "hard_error_delta": 0.030,
    }


def default_health_thresholds() -> dict[str, int]:
    """Default Flag Health Monitor lookback thresholds."""
    return {
        "stale_days": 30,
        "unused_days": 14,
        "always_on_days": 30,
        "always_off_days": 30,
    }


def default_traffic_allocation() -> dict[str, int]:
    """Default 50/50 split allocation."""
    return {"control": 50, "treatment": 50}


def default_guardrail_metrics() -> list[str]:
    """Default guardrail metric event types."""
    return ["payment_failed", "checkout_abandoned"]
