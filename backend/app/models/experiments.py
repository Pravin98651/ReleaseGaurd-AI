"""Experiment and ExperimentEvent (tracked_event_fallback) ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Double, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import (
    AnalyticsMode,
    ExperimentEventType,
    ExperimentStatus,
    ReleaseRecommendation,
    default_guardrail_metrics,
    default_guardrail_thresholds,
    default_traffic_allocation,
    str_enum,
)

if TYPE_CHECKING:
    from app.models.flags import FeatureFlag


class Experiment(Base):
    """Experiment definitions, capability mode, Bayesian summary, and recommendation."""

    __tablename__ = "experiments"
    __table_args__ = (
        Index("ix_experiments_flag_status", "feature_flag_id", "status"),
        Index("ix_experiments_env", "environment_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feature_flag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    environment_name: Mapped[str] = mapped_column(String(64), nullable=False)
    environment_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    analytics_mode: Mapped[AnalyticsMode] = mapped_column(
        str_enum(AnalyticsMode, length=50),
        nullable=False,
        default=AnalyticsMode.TRACKED_EVENT_FALLBACK,
    )
    flagsmith_experiment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[ExperimentStatus] = mapped_column(
        str_enum(ExperimentStatus, length=50),
        nullable=False,
        default=ExperimentStatus.DRAFT,
    )
    control_variation: Mapped[str] = mapped_column(String(64), nullable=False, default="control")
    treatment_variation: Mapped[str] = mapped_column(
        String(64), nullable=False, default="treatment"
    )
    traffic_allocation: Mapped[dict[str, int]] = mapped_column(
        JSONBCompat, nullable=False, default=default_traffic_allocation
    )
    primary_metric: Mapped[str] = mapped_column(
        String(64), nullable=False, default="checkout_completed"
    )
    guardrail_metrics: Mapped[list[str]] = mapped_column(
        JSONBCompat, nullable=False, default=default_guardrail_metrics
    )
    guardrail_thresholds: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=default_guardrail_thresholds
    )
    latest_bayesian_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONBCompat, nullable=True
    )
    latest_recommendation: Mapped[ReleaseRecommendation | None] = mapped_column(
        str_enum(ReleaseRecommendation, length=50), nullable=True
    )
    recommendation_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    feature_flag: Mapped[FeatureFlag] = relationship(back_populates="experiments")
    events: Mapped[list[ExperimentEvent]] = relationship(back_populates="experiment")


class ExperimentEvent(Base):
    """Tracked-Events Fallback Analytics Layer telemetry events."""

    __tablename__ = "experiment_events"
    __table_args__ = (
        Index("ix_exp_events_exp_var_type", "experiment_id", "variation_key", "event_type"),
        Index("ix_exp_events_flag_env_time", "flag_key", "environment_name", "occurred_at"),
        Index("ix_exp_events_user", "user_identifier"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True
    )
    flag_key: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_name: Mapped[str] = mapped_column(String(64), nullable=False)
    variation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[ExperimentEventType] = mapped_column(
        str_enum(ExperimentEventType, length=50), nullable=False
    )
    user_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    session_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    numeric_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    experiment: Mapped[Experiment | None] = relationship(back_populates="events")
