"""Persistent developer productivity benchmark ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import str_enum


class BenchmarkWorkflowType(StrEnum):
    """Canonical benchmark workflow categories (A through E)."""

    PR_REVIEW = "pr_review"
    FLAG_SETUP = "flag_setup"
    EXPERIMENT_SETUP = "experiment_setup"
    RELEASE_DECISION = "release_decision"
    COMPLETE_RELEASE = "complete_release"


class BenchmarkEvidenceClassification(StrEnum):
    """Strict provenance classification for benchmark timing records."""

    VERIFIED_MEASURED = "VERIFIED_MEASURED"
    ESTIMATED = "ESTIMATED"
    SIMULATED = "SIMULATED"
    INCOMPLETE = "INCOMPLETE"


class BenchmarkRun(Base):
    """Persistent record of an executed developer productivity benchmark run."""

    __tablename__ = "benchmark_runs"
    __table_args__ = (
        Index("ix_benchmark_runs_benchmark_id", "benchmark_id", unique=True),
        Index("ix_benchmark_runs_workflow_created", "workflow_type", "created_at"),
        Index("ix_benchmark_runs_env_created", "environment", "created_at"),
        Index("ix_benchmark_runs_evidence", "evidence_classification"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    benchmark_id: Mapped[str] = mapped_column(String(120), nullable=False)
    workflow_type: Mapped[BenchmarkWorkflowType] = mapped_column(
        str_enum(BenchmarkWorkflowType, length=40), nullable=False
    )
    execution_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    total_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    active_developer_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unattended_execution_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    api_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    human_intervention_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    human_intervention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_latency_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    environment: Mapped[str] = mapped_column(String(40), nullable=False, default="development")
    scenario: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    evidence_classification: Mapped[BenchmarkEvidenceClassification] = mapped_column(
        str_enum(BenchmarkEvidenceClassification, length=32),
        nullable=False,
        default=BenchmarkEvidenceClassification.VERIFIED_MEASURED,
    )
    manual_baseline_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    manual_baseline_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    stages: Mapped[list[BenchmarkStage]] = relationship(
        back_populates="benchmark_run",
        cascade="all, delete-orphan",
        order_by="BenchmarkStage.stage_order",
    )


class BenchmarkStage(Base):
    """Per-stage monotonic timing and call/retry breakdown for a BenchmarkRun."""

    __tablename__ = "benchmark_stages"
    __table_args__ = (
        Index("ix_benchmark_stages_run_order", "benchmark_run_id", "stage_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    benchmark_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False
    )
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    stage_name: Mapped[str] = mapped_column(String(160), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    active_developer_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unattended_execution_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    api_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    human_intervention_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    evidence_classification: Mapped[BenchmarkEvidenceClassification] = mapped_column(
        str_enum(BenchmarkEvidenceClassification, length=32),
        nullable=False,
        default=BenchmarkEvidenceClassification.VERIFIED_MEASURED,
    )
    manual_baseline_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)

    benchmark_run: Mapped[BenchmarkRun] = relationship(back_populates="stages")
