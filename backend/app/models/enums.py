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


class IntegrationAuthMode(StrEnum):
    OAUTH = "oauth"
    PAT = "pat"
    ORG_TOKEN = "org_token"


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
