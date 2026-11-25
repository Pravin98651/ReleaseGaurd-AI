"""Test Coverage, Production Environment, Feature-Flag Suitability, and Operational Readiness Analyzers."""

from __future__ import annotations

import re
from typing import Any

from app.models import (
    Approval,
    ApprovalStatus,
    CodeChange,
    DataSufficiency,
    DimensionStatus,
    Experiment,
    ExperimentStatus,
    FeatureFlag,
    FlagSuitability,
    Release,
    ReleaseRecommendation,
    RiskClassification,
    ValidationStatus,
)
from app.services.policy_engine import is_production_environment
from app.services.risk_engine.contracts import (
    AnalyzerResult,
    FindingSpec,
    MitigationSpec,
    score_to_classification,
)

_DESTRUCTIVE_MIGRATION_RE = re.compile(
    r"(drop_table|drop_column|drop\s+table|drop\s+column|alter\s+column.*type|nullable\s*=\s*False)",
    re.IGNORECASE,
)
_FLAG_MARKER_RE = re.compile(
    r"(is_feature_enabled|get_feature_value|hasFeature|getValue|flagsmith)", re.IGNORECASE
)


class TestCoverageAnalyzer:
    """Inspects modified test files, isolated patch validation, and missing failure-path coverage."""

    @staticmethod
    def analyze(
        files_data: list[dict[str, Any]],
        code_changes: list[CodeChange],
        coverage_report: dict[str, Any] | None = None,
    ) -> AnalyzerResult:
        prod_files = [
            str(f.get("filename") or "")
            for f in files_data
            if f.get("filename")
            and not re.search(r"(test_|_test\.|\.test\.|\.spec\.|tests/|\.md$)", str(f.get("filename")))
        ]
        test_files = [
            str(f.get("filename") or "")
            for f in files_data
            if f.get("filename")
            and re.search(r"(test_|_test\.|\.test\.|\.spec\.|tests/)", str(f.get("filename")))
        ]

        findings: list[FindingSpec] = []
        mitigations: list[MitigationSpec] = []
        limitations: list[str] = []

        if coverage_report is None:
            limitations.append(
                "Repository-wide line/branch coverage report unavailable; no coverage percentage fabricated."
            )

        failed_validation = any(c.validation_status == ValidationStatus.FAILED for c in code_changes)
        passed_validation = any(c.validation_status == ValidationStatus.PASSED for c in code_changes)

        score = 20
        if failed_validation:
            score = 88
            findings.append(
                FindingSpec(
                    dimension_key="test_coverage",
                    finding_type="validation_failed",
                    title="Isolated patch validation failed",
                    description="Linked CodeChange failed isolated syntax/test validation.",
                    severity=RiskClassification.CRITICAL,
                    source_reference="code_changes",
                )
            )
            mitigations.append(
                MitigationSpec(
                    dimension_key="test_coverage",
                    title="Fix failing patch validation tests",
                    description="Resolve isolated patch validation failures and re-run validation.",
                    priority=RiskClassification.CRITICAL,
                    mitigation_type="tests",
                    verification_criteria="CodeChange validation_status == passed.",
                )
            )
        elif prod_files and not test_files and not passed_validation:
            score = 74
            payment_or_auth = [
                f
                for f in prod_files
                if re.search(r"(payment|checkout|auth|migration)", f, re.IGNORECASE)
            ]
            findings.append(
                FindingSpec(
                    dimension_key="test_coverage",
                    finding_type="missing_regression_tests",
                    title="Production modules modified without corresponding test changes",
                    description=(
                        f"{len(prod_files)} production file(s) modified with 0 test file updates"
                        + (f", including critical path(s): {', '.join(payment_or_auth[:3])}." if payment_or_auth else ".")
                    ),
                    severity=RiskClassification.HIGH if payment_or_auth else RiskClassification.ELEVATED,
                    affected_module=prod_files[0],
                    source_reference=prod_files[0],
                )
            )
            mitigations.append(
                MitigationSpec(
                    dimension_key="test_coverage",
                    title="Add failure-path & regression tests",
                    description=(
                        f"Add unit/integration tests covering success and failure paths for {', '.join(prod_files[:3])}."
                    ),
                    priority=RiskClassification.HIGH,
                    mitigation_type="tests",
                    verification_criteria="Tests added and executed with passing results.",
                )
            )
        elif passed_validation and not test_files:
            score = 35
        elif test_files:
            score = 18

        return AnalyzerResult(
            dimension_key="test_coverage",
            dimension_name="Test Coverage & Missing Tests",
            status=DimensionStatus.EVALUATED if (files_data or code_changes) else DimensionStatus.UNAVAILABLE,
            data_sufficiency=DataSufficiency.LIMITED if coverage_report is None else DataSufficiency.SUFFICIENT,
            score=score if (files_data or code_changes) else None,
            severity=score_to_classification(score),
            evidence_quality="medium" if (test_files or code_changes) else "low",
            explanations=[
                (
                    f"Found {len(prod_files)} modified production file(s), {len(test_files)} modified test file(s), "
                    f"and {len(code_changes)} patch validation record(s)."
                )
            ],
            limitations=limitations,
            findings=findings,
            mitigations=mitigations,
            details={
                "production_files": prod_files,
                "test_files": test_files,
                "validation_passed": passed_validation,
                "validation_failed": failed_validation,
                "coverage_percent": coverage_report.get("line_coverage") if coverage_report else None,
            },
        )


class ProductionEnvironmentAnalyzer:
    """Evaluates target environment criticality and destructive migration patterns."""

    @staticmethod
    def analyze(
        target_environment: str,
        files_data: list[dict[str, Any]],
        flag: FeatureFlag | None = None,
    ) -> AnalyzerResult:
        env_norm = (target_environment or "development").strip().lower()
        is_prod = is_production_environment(env_norm)
        is_staging = env_norm in {"staging", "stage", "qa", "preprod"}

        destructive_migrations: list[str] = []
        for item in files_data:
            fname = str(item.get("filename") or "")
            patch = str(item.get("patch") or "")
            if ("migration" in fname.lower() or "alembic" in fname.lower()) and _DESTRUCTIVE_MIGRATION_RE.search(patch):
                destructive_migrations.append(fname)

        score = 60 if is_prod else (38 if is_staging else 18)
        findings: list[FindingSpec] = []
        mitigations: list[MitigationSpec] = []

        if destructive_migrations:
            score = min(100, score + 30)
            findings.append(
                FindingSpec(
                    dimension_key="production_environment",
                    finding_type="destructive_migration",
                    title="Potentially destructive database migration detected",
                    description=f"Migration file(s) {', '.join(destructive_migrations)} contain drop/alter/non-null operations.",
                    severity=RiskClassification.CRITICAL if is_prod else RiskClassification.HIGH,
                    affected_module=destructive_migrations[0],
                    source_reference=destructive_migrations[0],
                )
            )
            mitigations.append(
                MitigationSpec(
                    dimension_key="production_environment",
                    title="Use expand-and-contract migration pattern",
                    description="Split destructive schema changes into backward-compatible expand and contract releases.",
                    priority=RiskClassification.CRITICAL if is_prod else RiskClassification.HIGH,
                    mitigation_type="migration_safeguard",
                    verification_criteria="Schema supports both old and new application versions.",
                )
            )

        if is_prod and flag is None:
            score = min(100, score + 15)
            findings.append(
                FindingSpec(
                    dimension_key="production_environment",
                    finding_type="prod_without_flag",
                    title="Production target lacks linked feature flag",
                    description="Deploying directly to production without a Flagsmith kill-switch increases blast radius.",
                    severity=RiskClassification.HIGH,
                )
            )

        return AnalyzerResult(
            dimension_key="production_environment",
            dimension_name="Production Environment Risk",
            status=DimensionStatus.EVALUATED,
            data_sufficiency=DataSufficiency.SUFFICIENT,
            score=score,
            severity=score_to_classification(score),
            evidence_quality="high",
            explanations=[
                f"Target environment '{env_norm}' criticality evaluated (prod={is_prod}, destructive_migrations={len(destructive_migrations)})."
            ],
            findings=findings,
            mitigations=mitigations,
            details={
                "target_environment": env_norm,
                "is_production": is_prod,
                "destructive_migrations": destructive_migrations,
            },
        )


class FeatureFlagSuitabilityAnalyzer:
    """Assesses whether changes are suitable for feature-flag isolation and verifies linked flags."""

    @staticmethod
    def analyze(
        files_data: list[dict[str, Any]],
        flag: FeatureFlag | None = None,
    ) -> tuple[AnalyzerResult, FlagSuitability]:
        has_migrations = any(
            "alembic" in str(f.get("filename", "")).lower() or "migration" in str(f.get("filename", "")).lower()
            for f in files_data
        )
        has_flag_code = any(_FLAG_MARKER_RE.search(str(f.get("patch") or "")) for f in files_data)

        if not files_data and flag is None:
            return (
                AnalyzerResult(
                    dimension_key="feature_flag_suitability",
                    dimension_name="Feature-Flag Suitability",
                    status=DimensionStatus.UNAVAILABLE,
                    data_sufficiency=DataSufficiency.INSUFFICIENT_DATA,
                    score=None,
                    severity=RiskClassification.MODERATE,
                    explanations=["No changed files or feature flag linked."],
                    limitations=["Cannot evaluate code-to-flag binding without PR files or flag."],
                ),
                FlagSuitability.INSUFFICIENT_INFORMATION,
            )

        findings: list[FindingSpec] = []
        mitigations: list[MitigationSpec] = []

        if has_migrations and not has_flag_code:
            suitability = FlagSuitability.LIMITED_SUITABILITY
            score = 55
            findings.append(
                FindingSpec(
                    dimension_key="feature_flag_suitability",
                    finding_type="flag_isolation_limitation",
                    title="Schema migrations cannot be fully isolated by feature flags",
                    description="Database schema changes apply globally; use backward-compatible migrations alongside flags.",
                    severity=RiskClassification.ELEVATED,
                )
            )
        elif flag is not None and has_flag_code:
            suitability = FlagSuitability.HIGHLY_SUITABLE
            score = 15
        elif flag is not None:
            suitability = FlagSuitability.SUITABLE_WITH_CHANGES
            score = 42
            findings.append(
                FindingSpec(
                    dimension_key="feature_flag_suitability",
                    finding_type="unverified_flag_code_binding",
                    title=f"Flag '{flag.key}' linked, but diff lacks explicit Flagsmith SDK guard",
                    description="Verify that new code paths actually evaluate the linked feature flag.",
                    severity=RiskClassification.ELEVATED,
                )
            )
        else:
            suitability = FlagSuitability.SUITABLE_WITH_CHANGES
            score = 68
            findings.append(
                FindingSpec(
                    dimension_key="feature_flag_suitability",
                    finding_type="missing_feature_flag",
                    title="No feature flag protects modified runtime behavior",
                    description="Wrap user-facing or checkout changes behind a Flagsmith flag before rollout.",
                    severity=RiskClassification.HIGH,
                )
            )
            mitigations.append(
                MitigationSpec(
                    dimension_key="feature_flag_suitability",
                    title="Protect change with a Flagsmith feature flag",
                    description="Create/link a Flagsmith flag, guard the new path, and start with a 10%-25% staged rollout.",
                    priority=RiskClassification.HIGH,
                    mitigation_type="feature_flag",
                    verification_criteria="Code diff evaluates flag and legacy fallback is preserved.",
                )
            )

        return (
            AnalyzerResult(
                dimension_key="feature_flag_suitability",
                dimension_name="Feature-Flag Suitability",
                status=DimensionStatus.EVALUATED,
                data_sufficiency=DataSufficiency.SUFFICIENT,
                score=score,
                severity=score_to_classification(score),
                evidence_quality="high" if flag is not None else "medium",
                explanations=[
                    f"Suitability={suitability.value}; linked_flag={flag.key if flag else 'none'}, code_guard_detected={has_flag_code}."
                ],
                findings=findings,
                mitigations=mitigations,
                details={
                    "suitability": suitability.value,
                    "flag_key": flag.key if flag else None,
                    "code_guard_detected": has_flag_code,
                    "has_migrations": has_migrations,
                },
            ),
            suitability,
        )


class OperationalReadinessAnalyzer:
    """Evaluates guardrail monitoring, experiment health, and rollback/approval readiness."""

    @staticmethod
    def analyze(
        *,
        target_environment: str,
        flag: FeatureFlag | None = None,
        experiment: Experiment | None = None,
        approvals: list[Approval] | None = None,
        release: Release | None = None,
    ) -> AnalyzerResult:
        is_prod = is_production_environment(target_environment)
        score = 20
        findings: list[FindingSpec] = []
        mitigations: list[MitigationSpec] = []

        if experiment is not None and (
            experiment.latest_recommendation == ReleaseRecommendation.RECOMMEND_ROLLBACK
            or experiment.status == ExperimentStatus.ROLLED_BACK
        ):
            score = 90
            findings.append(
                FindingSpec(
                    dimension_key="operational_readiness",
                    finding_type="guardrail_breach",
                    title="Experiment guardrail breach / rollback signal active",
                    description=f"Experiment '{experiment.name}' recommends rollback.",
                    severity=RiskClassification.CRITICAL,
                )
            )
        elif experiment is None and is_prod:
            score += 20

        if flag is None and is_prod:
            score += 25
            mitigations.append(
                MitigationSpec(
                    dimension_key="operational_readiness",
                    title="Verify production rollback & monitoring path",
                    description="Attach a feature flag kill-switch and configure payment_failed / checkout_abandoned guardrails.",
                    priority=RiskClassification.HIGH,
                    mitigation_type="rollback_preparation",
                    verification_criteria="Rollback path verified in staging.",
                )
            )

        has_approved = any(a.status == ApprovalStatus.EXECUTED for a in (approvals or []))
        if is_prod and not has_approved:
            score = min(100, score + 15)

        return AnalyzerResult(
            dimension_key="operational_readiness",
            dimension_name="Operational & Rollback Readiness",
            status=DimensionStatus.EVALUATED,
            data_sufficiency=DataSufficiency.SUFFICIENT,
            score=min(100, score),
            severity=score_to_classification(min(100, score)),
            evidence_quality="high" if (experiment or flag or release) else "medium",
            explanations=[
                f"Operational readiness evaluated (env={target_environment}, flag={bool(flag)}, experiment={bool(experiment)}, approved={has_approved})."
            ],
            findings=findings,
            mitigations=mitigations,
            details={
                "has_flag_kill_switch": flag is not None,
                "has_experiment_guardrails": experiment is not None,
                "has_human_approval": has_approved,
            },
        )
