"""Shared data contracts for the Intelligent Release Risk Engine analyzers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import (
    DataSufficiency,
    DimensionStatus,
    RiskClassification,
)


@dataclass
class EvidenceSpec:
    evidence_type: str
    source_type: str
    source_reference: str
    title: str
    summary: str
    structured_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class FindingSpec:
    dimension_key: str
    finding_type: str
    title: str
    description: str
    severity: RiskClassification
    is_ai_inferred: bool = False
    affected_module: str | None = None
    source_reference: str | None = None
    evidence_summary: str | None = None
    evidence: list[EvidenceSpec] = field(default_factory=list)


@dataclass
class MitigationSpec:
    dimension_key: str
    title: str
    description: str
    priority: RiskClassification
    mitigation_type: str
    required: bool = True
    suggested_role: str = "engineer"
    verification_criteria: str | None = None


@dataclass
class ScenarioSpec:
    title: str
    description: str
    trigger_condition: str
    affected_component: str
    potential_impact: str
    severity: RiskClassification
    likelihood: str | None = None
    supporting_evidence: list[str] = field(default_factory=list)
    detection_strategy: str | None = None
    mitigation_summary: str | None = None
    related_dimensions: list[str] = field(default_factory=list)


@dataclass
class AnalyzerResult:
    dimension_key: str
    dimension_name: str
    status: DimensionStatus
    data_sufficiency: DataSufficiency
    score: int | None
    severity: RiskClassification
    evidence_quality: str = "medium"
    explanations: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    findings: list[FindingSpec] = field(default_factory=list)
    mitigations: list[MitigationSpec] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def score_to_classification(score: int | None) -> RiskClassification:
    if score is None:
        return RiskClassification.MODERATE
    if score <= 19:
        return RiskClassification.LOW
    if score <= 39:
        return RiskClassification.MODERATE
    if score <= 59:
        return RiskClassification.ELEVATED
    if score <= 79:
        return RiskClassification.HIGH
    return RiskClassification.CRITICAL
