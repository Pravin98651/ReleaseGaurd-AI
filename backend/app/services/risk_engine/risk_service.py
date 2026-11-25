"""IntelligentReleaseRiskService: orchestration, scoring, overrides, scenarios, and mitigations."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.security import redact_dict, scrub_secrets
from app.models import (
    ActorType,
    AgentDecisionSource,
    AgentDecisionStatus,
    AgentExecutionStatus,
    Approval,
    AuditLog,
    AuditStatus,
    CodeChange,
    DimensionStatus,
    Experiment,
    FeatureFlag,
    MitigationStatus,
    NotificationCategory,
    PolicyCategory,
    PullRequest,
    Release,
    ReleaseRiskAssessment,
    Repository,
    RiskAssessmentStatus,
    RiskClassification,
    RiskDimensionResult,
    RiskEngineConfiguration,
    RiskEvidence,
    RiskFinding,
    RiskMitigation,
    RiskScenario,
    default_dimension_weights,
    default_risk_thresholds,
)
from app.services.agent_execution_service import AgentExecutionService
from app.services.notification_service import (
    broadcast_workspace_event,
    create_notification,
)
from app.services.risk_engine.analyzers_code_deps_hist import (
    CodeImpactAnalyzer,
    DependencyImpactAnalyzer,
    HistoricalFailureAnalyzer,
)
from app.services.risk_engine.analyzers_test_env_flag import (
    FeatureFlagSuitabilityAnalyzer,
    OperationalReadinessAnalyzer,
    ProductionEnvironmentAnalyzer,
    TestCoverageAnalyzer,
)
from app.services.risk_engine.contracts import AnalyzerResult, ScenarioSpec

logger = logging.getLogger("releaseguard.risk_engine")

ANALYZER_VERSION = "1.0.0"


class RiskEngineError(Exception):
    def __init__(self, message: str, *, status_code: int = 422, code: str = "risk_engine_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def classify_with_thresholds(score: int, thresholds: dict[str, int]) -> RiskClassification:
    if score <= int(thresholds.get("low_max", 19)):
        return RiskClassification.LOW
    if score <= int(thresholds.get("moderate_max", 39)):
        return RiskClassification.MODERATE
    if score <= int(thresholds.get("elevated_max", 59)):
        return RiskClassification.ELEVATED
    if score <= int(thresholds.get("high_max", 79)):
        return RiskClassification.HIGH
    return RiskClassification.CRITICAL


class IntelligentReleaseRiskService:
    """Coordinates 7-dimension risk analysis, configurable scoring, scenarios, and mitigations."""

    @staticmethod
    async def get_active_configuration(session: AsyncSession) -> RiskEngineConfiguration:
        cfg = (
            await session.execute(
                select(RiskEngineConfiguration)
                .where(RiskEngineConfiguration.is_active.is_(True))
                .order_by(RiskEngineConfiguration.config_version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if cfg is None:
            cfg = RiskEngineConfiguration(
                config_version=1,
                is_active=True,
                dimension_weights=default_dimension_weights(),
                risk_thresholds=default_risk_thresholds(),
            )
            session.add(cfg)
            await session.flush()
        return cfg

    @staticmethod
    async def update_configuration(
        session: AsyncSession,
        *,
        actor_id: uuid.UUID,
        dimension_weights: dict[str, float] | None = None,
        risk_thresholds: dict[str, int] | None = None,
        critical_override_rules: list[str] | None = None,
    ) -> RiskEngineConfiguration:
        current = await IntelligentReleaseRiskService.get_active_configuration(session)
        weights = dict(dimension_weights or current.dimension_weights)
        if not weights or any(float(v) < 0 for v in weights.values()) or sum(weights.values()) <= 0:
            raise RiskEngineError("Dimension weights must be non-negative and sum > 0.")

        thresholds = dict(risk_thresholds or current.risk_thresholds)
        current.is_active = False
        new_cfg = RiskEngineConfiguration(
            config_version=current.config_version + 1,
            is_active=True,
            dimension_weights=weights,
            risk_thresholds=thresholds,
            critical_override_rules=list(
                critical_override_rules
                if critical_override_rules is not None
                else current.critical_override_rules
            ),
            updated_by_id=actor_id,
        )
        session.add(new_cfg)
        session.add(
            AuditLog(
                actor_type=ActorType.USER,
                actor_id=str(actor_id),
                policy_category=PolicyCategory.B,
                event_action="risk_engine.configuration_updated",
                resource_type="risk_engine_configuration",
                resource_id=str(new_cfg.config_version),
                status=AuditStatus.SUCCESS,
                details=redact_dict(
                    {"config_version": new_cfg.config_version, "weights": weights}
                ),
            )
        )
        await session.flush()
        return new_cfg

    @staticmethod
    async def _next_assessment_key(session: AsyncSession) -> str:
        year = utcnow().year
        count = int(
            (await session.execute(select(func.count(ReleaseRiskAssessment.id)))).scalar_one()
            or 0
        )
        seq = count + 1
        while True:
            cand = f"RSK-{year}-{seq:04d}"
            exists = (
                await session.execute(
                    select(ReleaseRiskAssessment.id)
                    .where(ReleaseRiskAssessment.assessment_key == cand)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if exists is None:
                return cand
            seq += 1

    @staticmethod
    async def assess_release(
        session: AsyncSession,
        *,
        release_id: uuid.UUID | None = None,
        pull_request_id: uuid.UUID | None = None,
        repository_id: uuid.UUID | None = None,
        target_environment: str | None = None,
        actor_id: uuid.UUID | None = None,
        github_client: Any | None = None,
        gemini_client: Any | None = None,
        files_override: list[dict[str, Any]] | None = None,
        coverage_report: dict[str, Any] | None = None,
        create_execution: bool = True,
    ) -> ReleaseRiskAssessment:
        release = await session.get(Release, release_id) if release_id else None
        if release_id and release is None:
            raise RiskEngineError("Release not found.", status_code=404, code="release_not_found")

        pr_id = pull_request_id or (release.pull_request_id if release else None)
        pr = await session.get(PullRequest, pr_id) if pr_id else None
        if pull_request_id and pr is None:
            raise RiskEngineError("Pull request not found.", status_code=404, code="pr_not_found")

        repo_id = (
            repository_id
            or (release.repository_id if release else None)
            or (pr.repository_id if pr else None)
        )
        repo = await session.get(Repository, repo_id) if repo_id else None
        env_norm = (
            (target_environment or (release.target_environment if release else "development"))
            .strip()
            .lower()
        )
        commit_sha = pr.head_sha if pr else None
        rel_ver = release.version if release else None

        # Mark prior current assessments for this release/PR as not current, and any stale commit/version as OUTDATED
        prior_stmt = select(ReleaseRiskAssessment)
        if release is not None:
            prior_stmt = prior_stmt.where(ReleaseRiskAssessment.release_id == release.id)
        elif pr is not None:
            prior_stmt = prior_stmt.where(ReleaseRiskAssessment.pull_request_id == pr.id)
        else:
            prior_stmt = None

        if prior_stmt is not None:
            for prev in (await session.execute(prior_stmt)).scalars().all():
                prev.is_current = False
                if (commit_sha and prev.commit_sha != commit_sha) or (
                    rel_ver and prev.release_version != rel_ver
                ):
                    prev.assessment_status = RiskAssessmentStatus.OUTDATED

        cfg = await IntelligentReleaseRiskService.get_active_configuration(session)
        assess_key = await IntelligentReleaseRiskService._next_assessment_key(session)

        exe = None
        if create_execution:
            exe = await AgentExecutionService.create_execution(
                session,
                agent_type="risk_engine",
                agent_name="Intelligent Release Risk Engine",
                task_name=f"Risk Assessment {assess_key}"
                + (f" ({release.release_key})" if release else ""),
                initiated_by_id=actor_id,
                actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
                repository_id=repo_id,
                pull_request_id=pr.id if pr else None,
                release_id=release.id if release else None,
                planned_steps=[
                    {"step_key": "collect_context", "step_name": "Collect PR & Release Context"},
                    {"step_key": "run_analyzers", "step_name": "Run 7 Risk Dimension Analyzers"},
                    {"step_key": "score_and_plan", "step_name": "Score & Generate Mitigations"},
                ],
            )
            await AgentExecutionService.transition_execution(
                session,
                exe,
                AgentExecutionStatus.RUNNING,
                stage="collect_context",
                activity="Collecting PR diffs, flags, and historical records",
            )

        assessment = ReleaseRiskAssessment(
            assessment_key=assess_key,
            release_id=release.id if release else None,
            pull_request_id=pr.id if pr else None,
            repository_id=repo_id,
            execution_id=exe.id if exe else None,
            target_environment=env_norm,
            commit_sha=commit_sha,
            release_version=rel_ver,
            assessment_status=RiskAssessmentStatus.RUNNING,
            scoring_version=cfg.config_version,
            analyzer_version=ANALYZER_VERSION,
            created_by_id=actor_id,
        )
        session.add(assessment)
        await session.flush()

        # Gather PR changed files (from override or GitHubClient)
        files_data: list[dict[str, Any]] = list(files_override or [])
        limitations: list[str] = []
        if (
            not files_data
            and pr is not None
            and repo is not None
            and github_client is not None
            and getattr(github_client, "is_configured", False)
        ):
            try:
                files_data = await github_client.get_pull_request_files(
                    repo.owner, repo.name, pr.github_pr_number
                )
            except Exception as exc:  # noqa: BLE001
                limitations.append(f"GitHub PR file retrieval failed: {scrub_secrets(str(exc))}")

        code_changes = (
            list(
                (
                    await session.execute(
                        select(CodeChange).where(CodeChange.pull_request_id == pr.id)
                    )
                )
                .scalars()
                .all()
            )
            if pr is not None
            else []
        )
        flag = (
            await session.get(FeatureFlag, release.feature_flag_id)
            if release and release.feature_flag_id
            else None
        )
        experiment = (
            await session.get(Experiment, release.experiment_id)
            if release and release.experiment_id
            else None
        )
        approvals = (
            list(
                (
                    await session.execute(
                        select(Approval).where(Approval.release_id == release.id)
                    )
                )
                .scalars()
                .all()
            )
            if release is not None
            else []
        )

        # Run 7 analyzers
        r_code = CodeImpactAnalyzer.analyze(files_data, pr)
        r_deps = DependencyImpactAnalyzer.analyze(files_data)
        r_hist = await HistoricalFailureAnalyzer.analyze(
            session,
            repository_id=repo_id,
            feature_flag_id=flag.id if flag else None,
            changed_files=[str(f.get("filename") or "") for f in files_data],
        )
        r_test = TestCoverageAnalyzer.analyze(files_data, code_changes, coverage_report)
        r_env = ProductionEnvironmentAnalyzer.analyze(env_norm, files_data, flag)
        r_flag, flag_suitability = FeatureFlagSuitabilityAnalyzer.analyze(files_data, flag)
        r_ops = OperationalReadinessAnalyzer.analyze(
            target_environment=env_norm,
            flag=flag,
            experiment=experiment,
            approvals=approvals,
            release=release,
        )

        dim_results: list[AnalyzerResult] = [
            r_code,
            r_deps,
            r_hist,
            r_test,
            r_env,
            r_flag,
            r_ops,
        ]

        weights = cfg.dimension_weights
        evaluated_dims: list[str] = []
        unavailable_dims: list[str] = []
        weighted_sum = 0.0
        weight_total = 0.0
        evidence_count = 0

        for dr in dim_results:
            w = float(weights.get(dr.dimension_key, 0.1))
            limitations.extend(dr.limitations)
            if dr.score is not None and dr.status != DimensionStatus.UNAVAILABLE:
                evaluated_dims.append(dr.dimension_key)
                contrib = round(dr.score * w, 2)
                weighted_sum += dr.score * w
                weight_total += w
            else:
                unavailable_dims.append(dr.dimension_key)
                contrib = None

            session.add(
                RiskDimensionResult(
                    assessment_id=assessment.id,
                    dimension_key=dr.dimension_key,
                    dimension_name=dr.dimension_name,
                    score=dr.score,
                    weight=w,
                    weighted_contribution=contrib,
                    severity=dr.severity,
                    status=dr.status,
                    data_sufficiency=dr.data_sufficiency,
                    evidence_quality=dr.evidence_quality,
                    explanations=dr.explanations,
                    limitations=dr.limitations,
                    details_json=redact_dict(dr.details),
                )
            )

            for fspec in dr.findings:
                finding = RiskFinding(
                    assessment_id=assessment.id,
                    dimension_key=fspec.dimension_key,
                    finding_type=fspec.finding_type,
                    title=fspec.title,
                    description=fspec.description,
                    severity=fspec.severity,
                    is_ai_inferred=fspec.is_ai_inferred,
                    affected_module=fspec.affected_module,
                    source_reference=fspec.source_reference,
                    evidence_summary=fspec.evidence_summary,
                )
                session.add(finding)
                await session.flush()
                for ev in fspec.evidence:
                    session.add(
                        RiskEvidence(
                            assessment_id=assessment.id,
                            finding_id=finding.id,
                            evidence_type=ev.evidence_type,
                            source_type=ev.source_type,
                            source_reference=ev.source_reference,
                            title=ev.title,
                            summary=ev.summary,
                            structured_data=redact_dict(ev.structured_data),
                        )
                    )
                    evidence_count += 1

            for mspec in dr.mitigations:
                session.add(
                    RiskMitigation(
                        assessment_id=assessment.id,
                        dimension_key=mspec.dimension_key,
                        title=mspec.title,
                        description=mspec.description,
                        priority=mspec.priority,
                        mitigation_type=mspec.mitigation_type,
                        status=MitigationStatus.PENDING,
                        required=mspec.required,
                        suggested_role=mspec.suggested_role,
                        verification_criteria=mspec.verification_criteria,
                    )
                )

        agg_score = round(weighted_sum / weight_total) if weight_total > 0 else 50
        risk_level = classify_with_thresholds(agg_score, cfg.risk_thresholds)

        # Critical Overrides
        overrides: list[str] = []
        if r_env.details.get("destructive_migrations") and r_env.details.get("is_production"):
            overrides.append("destructive_migration_in_production")
            risk_level = RiskClassification.CRITICAL
            agg_score = max(agg_score, 85)
        if r_test.details.get("validation_failed"):
            overrides.append("failed_mandatory_validation")
            if risk_level in {RiskClassification.LOW, RiskClassification.MODERATE}:
                risk_level = RiskClassification.HIGH
            agg_score = max(agg_score, 70)
        if any(
            f.finding_type == "guardrail_breach"
            for dr in dim_results
            for f in dr.findings
        ):
            overrides.append("unresolved_experiment_guardrail_breach")
            risk_level = RiskClassification.CRITICAL
            agg_score = max(agg_score, 85)

        # Potential Failure Scenarios (grounded in findings, labeled is_potential=True)
        scenarios = IntelligentReleaseRiskService._build_scenarios(dim_results)
        for sc in scenarios:
            session.add(
                RiskScenario(
                    assessment_id=assessment.id,
                    title=sc.title,
                    description=sc.description,
                    trigger_condition=sc.trigger_condition,
                    affected_component=sc.affected_component,
                    potential_impact=sc.potential_impact,
                    severity=sc.severity,
                    likelihood=sc.likelihood,
                    is_potential=True,
                    supporting_evidence=sc.supporting_evidence,
                    detection_strategy=sc.detection_strategy,
                    mitigation_summary=sc.mitigation_summary,
                    related_dimensions=sc.related_dimensions,
                )
            )

        # Optional Gemini interpretation (never alters deterministic score)
        ai_interp: str | None = None
        fallback_mode = False
        if gemini_client is not None and getattr(gemini_client, "is_configured", False):
            try:
                prompt = (
                    f"Summarize technical release risk (score={agg_score}/100, level={risk_level.value}, "
                    f"env={env_norm}, evaluated={evaluated_dims}, unavailable={unavailable_dims}) in 2 factual sentences."
                )
                ai_resp = await gemini_client.generate_text(prompt)
                if ai_resp:
                    ai_interp = scrub_secrets(str(ai_resp).strip())[:2000]
            except Exception as exc:  # noqa: BLE001
                fallback_mode = True
                limitations.append(f"Gemini interpretation unavailable (fallback mode): {scrub_secrets(str(exc))}")
        else:
            fallback_mode = True

        completeness = round(len(evaluated_dims) / len(dim_results), 2)
        confidence = (
            round(min(0.95, 0.35 + completeness * 0.55), 2)
            if len(evaluated_dims) >= 3
            else None
        )

        assessment.risk_score = agg_score
        assessment.risk_level = risk_level
        assessment.data_completeness = completeness
        assessment.assessment_confidence = confidence
        assessment.flag_suitability = flag_suitability
        assessment.evaluated_dimensions = evaluated_dims
        assessment.unavailable_dimensions = unavailable_dims
        assessment.limitations = list(dict.fromkeys(limitations))
        assessment.critical_overrides = overrides
        assessment.dependency_graph = r_deps.details
        assessment.evidence_summary = {
            "evidence_count": evidence_count,
            "evaluated_count": len(evaluated_dims),
            "unavailable_count": len(unavailable_dims),
        }
        assessment.ai_interpretation = ai_interp
        assessment.fallback_mode = fallback_mode
        assessment.assessment_status = (
            RiskAssessmentStatus.PARTIAL if unavailable_dims else RiskAssessmentStatus.COMPLETED
        )
        assessment.completed_at = utcnow()

        if exe is not None:
            await AgentExecutionService.record_decision(
                session,
                exe,
                decision_type="release_risk_assessment",
                decision_source=AgentDecisionSource.DETERMINISTIC_RULE,
                decision_summary=f"Computed risk score {agg_score}/100 ({risk_level.value}), completeness {int(completeness * 100)}%.",
                recommendation="Address required mitigations before production rollout."
                if risk_level in {RiskClassification.HIGH, RiskClassification.CRITICAL}
                else "Proceed with standard release readiness gates.",
                confidence=confidence,
                decision_status=AgentDecisionStatus.ACCEPTED,
                supporting_evidence=assessment.limitations[:3] or ["All active dimensions evaluated."],
                evidence_references=[
                    {"type": "risk_assessment", "id": str(assessment.id), "key": assessment.assessment_key}
                ],
            )
            await AgentExecutionService.transition_execution(
                session,
                exe,
                AgentExecutionStatus.COMPLETED,
                stage="completed",
                activity=f"Risk assessment completed ({agg_score}/100)",
            )

        if risk_level == RiskClassification.CRITICAL:
            await create_notification(
                session,
                category=NotificationCategory.RISK_CRITICAL,
                severity="critical",
                title=f"Critical Release Risk: {assessment.assessment_key} ({agg_score}/100)",
                message=f"Target environment '{env_norm}' flagged CRITICAL risk.",
                resource_type="risk_assessment",
                resource_id=str(assessment.id),
                environment_name=env_norm,
                action_href=f"/risk-intelligence/{assessment.id}",
            )

        await broadcast_workspace_event(
            "risk.assessment_completed",
            {
                "assessment_id": str(assessment.id),
                "assessment_key": assessment.assessment_key,
                "release_id": str(release.id) if release else None,
                "risk_score": agg_score,
                "risk_level": risk_level.value,
            },
        )
        return assessment

    @staticmethod
    def _build_scenarios(dim_results: list[AnalyzerResult]) -> list[ScenarioSpec]:
        scenarios: list[ScenarioSpec] = []
        for dr in dim_results:
            for f in dr.findings[:2]:
                scenarios.append(
                    ScenarioSpec(
                        title=f"Potential Failure: {f.title}",
                        description=f.description,
                        trigger_condition=f"Triggered when modified component '{f.affected_module or dr.dimension_key}' encounters edge-case or invalid input.",
                        affected_component=f.affected_module or dr.dimension_key,
                        potential_impact="Degraded user flow, elevated error rate, or rollback requirement.",
                        severity=f.severity,
                        likelihood="medium" if f.severity in {RiskClassification.HIGH, RiskClassification.CRITICAL} else "low",
                        supporting_evidence=[f.evidence_summary or f.description],
                        detection_strategy="Monitor payment_failed, checkout_abandoned, and error telemetry.",
                        mitigation_summary="Complete linked mitigation recommendations before rollout.",
                        related_dimensions=[dr.dimension_key],
                    )
                )
        return scenarios[:8]
