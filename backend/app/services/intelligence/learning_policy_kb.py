"""Release Learning Engine, Policy Intelligence, and Knowledge Base Services (Phase 5)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models import (
    ActorType,
    AnalysisRunStatus,
    Approval,
    ApprovalCategory,
    ApprovalStatus,
    AuditLog,
    AuditStatus,
    EvidenceClassification,
    IntelligenceAnalysisRun,
    NotificationCategory,
    PolicyArea,
    PolicyCategory,
    PolicyRecommendation,
    PolicyReviewStatus,
    Release,
    ReleaseFailurePattern,
    ReleaseInsight,
    Repository,
    RepositoryIntelligenceProfile,
    RiskClassification,
    RiskEngineConfiguration,
    WorkspaceSettings,
)
from app.services.intelligence.normalizer_analytics import (
    HistoricalReleaseAnalyticsService,
    ReleaseOutcomeNormalizer,
)
from app.services.intelligence.patterns_rollbacks import (
    FailurePatternAnalyzer,
    RollbackIntelligenceService,
)
from app.services.intelligence.recommendation_eval import (
    RecommendationEvaluationService,
)
from app.services.intelligence.repo_dependency import (
    DependencyIntelligenceService,
    RepositoryIntelligenceService,
)
from app.services.notification_service import create_notification


class PolicyIntelligenceError(Exception):
    """Domain error for policy recommendation governance."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class ReleasePolicyIntelligenceService:
    """Generates and governs human-reviewed policy improvement recommendations."""

    @staticmethod
    async def generate_recommendations(
        session: AsyncSession,
        *,
        patterns: list[ReleaseFailurePattern],
        profiles: list[RepositoryIntelligenceProfile],
    ) -> list[PolicyRecommendation]:
        created_or_updated: list[PolicyRecommendation] = []
        ws = (
            await session.execute(select(WorkspaceSettings).limit(1))
        ).scalar_one_or_none()
        current_guardrails = dict(ws.default_guardrails) if ws else {"guardrail_max_error_rate": 0.05}

        # 1. If payment_failure patterns exist, recommend tighter payment_failed guardrail
        payment_patterns = [p for p in patterns if p.category.value == "payment_failure"]
        if payment_patterns:
            top = payment_patterns[0]
            title = "Tighten payment_failed guardrail & extend checkout observation window"
            existing = (
                await session.execute(
                    select(PolicyRecommendation).where(PolicyRecommendation.title == title).limit(1)
                )
            ).scalar_one_or_none()
            if existing is None:
                rec = PolicyRecommendation(
                    title=title,
                    description=(
                        f"Detected {top.failure_count} payment-related failures out of {top.sample_size} releases "
                        f"(rate={top.observed_rate:.1%}). Recommend stricter guardrail threshold and longer observation."
                    ),
                    policy_area=PolicyArea.GUARDRAIL_THRESHOLDS,
                    current_configuration={
                        "guardrail_max_error_rate": current_guardrails.get("guardrail_max_error_rate", 0.05),
                        "minimum_observation_seconds": 900,
                    },
                    proposed_configuration={
                        "guardrail_max_error_rate": 0.02,
                        "minimum_observation_seconds": 1200,
                    },
                    supporting_evidence={
                        "pattern_key": top.pattern_key,
                        "sample_size": top.sample_size,
                        "failure_count": top.failure_count,
                        "supporting_release_ids": top.supporting_release_ids,
                    },
                    affected_repositories=[str(top.repository_id)] if top.repository_id else [],
                    expected_benefit="Earlier automatic pause/rollback before payment failures affect 100% traffic.",
                    tradeoffs="Slightly longer rollout duration (+5 minutes per stage) and higher sensitivity to transient spikes.",
                    confidence_data=top.confidence_data,
                    review_status=PolicyReviewStatus.PENDING_REVIEW,
                )
                session.add(rec)
                created_or_updated.append(rec)
            else:
                created_or_updated.append(existing)

        # 2. If any repository has high failure rate (>= 25% with >= 2 releases), recommend lower initial rollout %
        risky_repos = [p for p in profiles if p.release_count >= 2 and p.historical_failure_rate >= 0.25]
        if risky_repos:
            top_repo = risky_repos[0]
            repo = await session.get(Repository, top_repo.repository_id)
            repo_label = repo.full_name if repo else str(top_repo.repository_id)
            title = f"Reduce initial canary percentage & require tests for {repo_label}"
            existing = (
                await session.execute(
                    select(PolicyRecommendation).where(PolicyRecommendation.title == title).limit(1)
                )
            ).scalar_one_or_none()
            if existing is None:
                rec = PolicyRecommendation(
                    title=title,
                    description=(
                        f"Repository {repo_label} recorded {top_repo.failure_count} failures in "
                        f"{top_repo.release_count} releases ({top_repo.historical_failure_rate:.1%})."
                    ),
                    policy_area=PolicyArea.INITIAL_ROLLOUT_PERCENTAGE,
                    current_configuration={"initial_stage_percentage": 1, "require_integration_tests": False},
                    proposed_configuration={"initial_stage_percentage": 1, "require_integration_tests": True},
                    supporting_evidence={
                        "repository_id": str(top_repo.repository_id),
                        "release_count": top_repo.release_count,
                        "failure_count": top_repo.failure_count,
                    },
                    affected_repositories=[str(top_repo.repository_id)],
                    expected_benefit="Catch regressions prior to production rollout.",
                    tradeoffs="Adds CI validation time before rollout approval.",
                    confidence_data={"sample_size": top_repo.release_count},
                    review_status=PolicyReviewStatus.PENDING_REVIEW,
                )
                session.add(rec)
                created_or_updated.append(rec)
            else:
                created_or_updated.append(existing)

        await session.flush()
        return created_or_updated

    @staticmethod
    async def review_recommendation(
        session: AsyncSession,
        rec: PolicyRecommendation,
        *,
        reviewer_id: uuid.UUID,
        decision: str,
        notes: str | None = None,
        modified_configuration: dict[str, Any] | None = None,
    ) -> PolicyRecommendation:
        d = decision.strip().lower()
        mapping = {
            "accept": PolicyReviewStatus.ACCEPTED,
            "accepted": PolicyReviewStatus.ACCEPTED,
            "reject": PolicyReviewStatus.REJECTED,
            "rejected": PolicyReviewStatus.REJECTED,
            "modify": PolicyReviewStatus.MODIFIED,
            "modified": PolicyReviewStatus.MODIFIED,
        }
        if d not in mapping:
            raise PolicyIntelligenceError(f"Invalid review decision: {decision}")

        rec.review_status = mapping[d]
        rec.reviewer_id = reviewer_id
        rec.review_notes = notes
        rec.reviewed_at = utcnow()
        if modified_configuration is not None:
            rec.approved_configuration = modified_configuration
        elif rec.review_status == PolicyReviewStatus.ACCEPTED:
            rec.approved_configuration = dict(rec.proposed_configuration)

        session.add(
            AuditLog(
                actor_type=ActorType.USER,
                actor_id=str(reviewer_id),
                policy_category=PolicyCategory.C,
                event_action=f"intelligence.policy.{rec.review_status.value}",
                resource_type="policy_recommendation",
                resource_id=str(rec.id),
                status=AuditStatus.SUCCESS,
                details={"title": rec.title, "notes": notes},
            )
        )
        await session.flush()
        return rec

    @staticmethod
    async def approve_recommendation(
        session: AsyncSession,
        rec: PolicyRecommendation,
        *,
        approver_id: uuid.UUID,
        notes: str | None = None,
    ) -> PolicyRecommendation:
        if rec.review_status == PolicyReviewStatus.REJECTED:
            raise PolicyIntelligenceError("Cannot approve a rejected policy recommendation.")

        now = utcnow()
        action_hash = hashlib.sha256(f"policy:{rec.id}".encode()).hexdigest()
        approval = Approval(
            policy_category=ApprovalCategory.C,
            action_type="policy_update",
            target_resource_type="policy_recommendation",
            target_resource_id=str(rec.id),
            environment_name="production",
            title=f"Approve policy: {rec.title[:200]}",
            rationale=notes or rec.description,
            action_payload=rec.approved_configuration or rec.proposed_configuration,
            expected_precondition=rec.current_configuration,
            action_hash=action_hash,
            idempotency_key=f"policy-approve-{rec.id}",
            requested_by_id=approver_id,
            decided_by_id=approver_id,
            status=ApprovalStatus.EXECUTED,
            decided_at=now,
            expires_at=now + timedelta(hours=24),
        )
        session.add(approval)
        await session.flush()

        rec.approval_id = approval.id
        rec.reviewer_id = approver_id
        rec.review_status = PolicyReviewStatus.ACCEPTED
        rec.approved_configuration = rec.approved_configuration or dict(rec.proposed_configuration)
        rec.reviewed_at = utcnow()
        if notes:
            rec.review_notes = notes

        session.add(
            AuditLog(
                actor_type=ActorType.USER,
                actor_id=str(approver_id),
                policy_category=PolicyCategory.C,
                event_action="intelligence.policy.approved",
                resource_type="policy_recommendation",
                resource_id=str(rec.id),
                status=AuditStatus.SUCCESS,
                details={"approval_id": str(approval.id)},
            )
        )
        await session.flush()
        return rec

    @staticmethod
    async def implement_recommendation(
        session: AsyncSession,
        rec: PolicyRecommendation,
        *,
        actor_id: uuid.UUID,
    ) -> PolicyRecommendation:
        if rec.approval_id is None or rec.review_status not in (
            PolicyReviewStatus.ACCEPTED,
            PolicyReviewStatus.MODIFIED,
        ):
            raise PolicyIntelligenceError(
                "Policy recommendation must be explicitly approved before implementation.",
                status_code=403,
            )

        cfg = rec.approved_configuration or rec.proposed_configuration
        if rec.policy_area == PolicyArea.GUARDRAIL_THRESHOLDS:
            ws = (await session.execute(select(WorkspaceSettings).limit(1))).scalar_one_or_none()
            if ws and "guardrail_max_error_rate" in cfg:
                updated = dict(ws.default_guardrails or {})
                updated["guardrail_max_error_rate"] = float(cfg["guardrail_max_error_rate"])
                ws.default_guardrails = updated
        elif rec.policy_area == PolicyArea.RISK_THRESHOLDS:
            rc = (
                await session.execute(
                    select(RiskEngineConfiguration).where(RiskEngineConfiguration.is_active.is_(True)).limit(1)
                )
            ).scalar_one_or_none()
            if rc and isinstance(cfg.get("risk_thresholds"), dict):
                rc.risk_thresholds = dict(cfg["risk_thresholds"])

        rec.review_status = PolicyReviewStatus.IMPLEMENTED
        rec.implemented_at = utcnow()
        session.add(
            AuditLog(
                actor_type=ActorType.USER,
                actor_id=str(actor_id),
                policy_category=PolicyCategory.C,
                event_action="intelligence.policy.implemented",
                resource_type="policy_recommendation",
                resource_id=str(rec.id),
                status=AuditStatus.SUCCESS,
                details={"applied_configuration": cfg},
            )
        )
        await session.flush()
        return rec

    @staticmethod
    async def verify_recommendation(
        session: AsyncSession,
        rec: PolicyRecommendation,
        *,
        actor_id: uuid.UUID,
    ) -> PolicyRecommendation:
        if rec.review_status != PolicyReviewStatus.IMPLEMENTED:
            raise PolicyIntelligenceError("Policy recommendation must be implemented before verification.")

        perf = await HistoricalReleaseAnalyticsService.compute_performance(session, days=30)
        rec.review_status = PolicyReviewStatus.VERIFIED
        rec.verified_at = utcnow()
        rec.verification_evidence = {
            "verified_at": utcnow().isoformat(),
            "post_implementation_change_failure_rate": perf["change_failure_rate"],
            "qualifying_releases": perf["qualifying_releases"],
        }
        session.add(
            AuditLog(
                actor_type=ActorType.USER,
                actor_id=str(actor_id),
                policy_category=PolicyCategory.C,
                event_action="intelligence.policy.verified",
                resource_type="policy_recommendation",
                resource_id=str(rec.id),
                status=AuditStatus.SUCCESS,
                details=rec.verification_evidence,
            )
        )
        await session.flush()
        return rec


class ReleaseLearningEngine:
    """Orchestrates end-to-end historical learning & insight generation."""

    ANALYSIS_VERSION = "1.0.0"

    @staticmethod
    async def run_analysis(
        session: AsyncSession,
        *,
        analysis_type: str = "full_learning",
        repository_id: uuid.UUID | None = None,
    ) -> IntelligenceAnalysisRun:
        run = IntelligenceAnalysisRun(
            analysis_type=analysis_type,
            scope={"repository_id": str(repository_id) if repository_id else "workspace"},
            status=AnalysisRunStatus.RUNNING,
            analysis_version=ReleaseLearningEngine.ANALYSIS_VERSION,
        )
        session.add(run)
        await session.flush()

        outcomes = await ReleaseOutcomeNormalizer.normalize_all(session, repository_id=repository_id)
        patterns = await FailurePatternAnalyzer.analyze_patterns(session, repository_id=repository_id)
        profiles = await RepositoryIntelligenceService.refresh_all(session)
        rec_outcomes = await RecommendationEvaluationService.evaluate_all(session)
        perf = await HistoricalReleaseAnalyticsService.compute_performance(
            session, repository_id=repository_id, days=30
        )
        policy_recs = await ReleasePolicyIntelligenceService.generate_recommendations(
            session, patterns=patterns, profiles=profiles
        )

        # Generate top-level ReleaseInsights grounded in actual counts
        insights_created = 0
        if perf["qualifying_releases"] > 0:
            insight_key = f"insight:perf:{repository_id or 'workspace'}"
            existing = (
                await session.execute(
                    select(ReleaseInsight).where(ReleaseInsight.insight_key == insight_key).limit(1)
                )
            ).scalar_one_or_none()
            cfr_pct = f"{(perf['change_failure_rate'] or 0.0):.1%}"
            desc = (
                f"Across {perf['qualifying_releases']} qualifying releases in the last 30 days, "
                f"{perf['successful_releases']} succeeded and {perf['failed_releases']} failed/rolled back "
                f"(Change Failure Rate: {cfr_pct})."
            )
            sev = (
                RiskClassification.HIGH
                if (perf["change_failure_rate"] or 0.0) >= 0.25
                else RiskClassification.LOW
            )
            if existing is None:
                session.add(
                    ReleaseInsight(
                        insight_key=insight_key,
                        title="30-Day Release Reliability Summary",
                        description=desc,
                        insight_category="historical_performance",
                        evidence=perf,
                        evidence_classification=EvidenceClassification.OBSERVED,
                        affected_repositories=[str(repository_id)] if repository_id else [],
                        affected_releases=[],
                        sample_size=perf["qualifying_releases"],
                        confidence_data=perf["change_failure_rate_ci"],
                        severity=sev,
                        status="active",
                        generated_by="learning_engine",
                    )
                )
            else:
                existing.description = desc
                existing.evidence = perf
                existing.sample_size = perf["qualifying_releases"]
                existing.confidence_data = perf["change_failure_rate_ci"]
                existing.severity = sev
            insights_created += 1

        if not outcomes:
            run.status = AnalysisRunStatus.INSUFFICIENT_DATA
        else:
            run.status = AnalysisRunStatus.COMPLETED
        run.input_record_count = len(outcomes)
        run.output_record_count = len(patterns) + len(profiles) + len(rec_outcomes) + len(policy_recs)
        run.summary = {
            "outcomes_normalized": len(outcomes),
            "patterns_detected": len(patterns),
            "repository_profiles_updated": len(profiles),
            "recommendations_evaluated": len(rec_outcomes),
            "policy_recommendations_total": len(policy_recs),
            "insights_updated": insights_created,
        }
        run.completed_at = utcnow()
        await session.flush()

        if policy_recs:
            await create_notification(
                session,
                category=NotificationCategory.INTELLIGENCE_INSIGHT,
                severity="info",
                title="Release Intelligence Analysis Completed",
                message=(
                    f"Analyzed {len(outcomes)} release outcomes, {len(patterns)} failure patterns, "
                    f"and {len(policy_recs)} policy recommendations."
                ),
                resource_type="intelligence",
                action_href="/intelligence",
            )
        return run


class ReleaseKnowledgeBaseService:
    """Evidence-linked structured knowledge retrieval & grounded Q&A."""

    @staticmethod
    async def query(
        session: AsyncSession,
        *,
        question: str,
        repository_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        q = (question or "").strip()
        perf = await HistoricalReleaseAnalyticsService.compute_performance(
            session, repository_id=repository_id, days=90
        )
        patterns = list(
            (await session.execute(select(ReleaseFailurePattern).order_by(ReleaseFailurePattern.failure_count.desc()).limit(5)))
            .scalars()
            .all()
        )
        rollbacks = await RollbackIntelligenceService.analyze_rollbacks(session)
        rec_acc = await RecommendationEvaluationService.compute_accuracy_metrics(
            session, repository_id=repository_id
        )
        deps = await DependencyIntelligenceService.analyze_dependencies(session)
        recent_releases = list(
            (await session.execute(select(Release).order_by(Release.created_at.desc()).limit(10)))
            .scalars()
            .all()
        )

        ql = q.lower()
        evidence_refs: list[dict[str, Any]] = []
        if "rollback" in ql:
            top_c = rollbacks["top_causes"][0] if rollbacks["top_causes"] else None
            direct_answer = (
                f"Recorded {rollbacks['total_rollbacks']} rollback(s). "
                + (
                    f"Most frequent cause: {top_c['category']} ({top_c['count']} occurrences)."
                    if top_c
                    else "No rollback operations have been recorded yet."
                )
            )
            observed_pattern = f"Rollback causes: {rollbacks['top_causes']}"
            recommended_action = (
                "Review triggering guardrails and require pre-release integration tests for top rollback causes."
            )
            evidence_refs = [{"type": "rollback", "id": r["id"], "reason": r["reason"]} for r in rollbacks["rollbacks"][:5]]
        elif "dependen" in ql or "package" in ql:
            top_d = deps[0] if deps else None
            direct_answer = (
                f"Found {len(deps)} dependency manifest/package association(s). "
                + (
                    f"Highest failure association: {top_d['dependency']} ({top_d['failure_count']}/{top_d['release_count']} failed)."
                    if top_d
                    else "No dependency failures recorded in historical releases."
                )
            )
            observed_pattern = f"Top dependencies: {deps[:3]}"
            recommended_action = "Run compatibility validation on dependency manifest upgrades prior to staging."
            evidence_refs = [{"type": "dependency", "name": d["dependency"], "failures": d["failure_count"]} for d in deps[:5]]
        elif "recommend" in ql or "agent" in ql or "accurate" in ql:
            direct_answer = (
                f"Evaluated {rec_acc['total_recommendations']} AI recommendation(s): "
                f"{rec_acc['accepted_count']} accepted, {rec_acc['positive_outcome_count']} positive outcome(s), "
                f"{rec_acc['inconclusive_or_not_evaluable_count']} inconclusive/not evaluable."
            )
            observed_pattern = f"Category breakdown: {rec_acc['by_category']}"
            recommended_action = "Track verified release outcomes after accepting agent recommendations to expand evaluable coverage."
        else:
            direct_answer = (
                f"Over the last 90 days ({perf['qualifying_releases']} qualifying releases), "
                f"{perf['successful_releases']} succeeded and {perf['failed_releases']} failed/rolled back "
                f"(Change Failure Rate: {perf['change_failure_rate'] if perf['change_failure_rate'] is not None else 'N/A'})."
            )
            observed_pattern = (
                f"Top failure patterns: {[p.name for p in patterns[:3]]}"
                if patterns
                else "No recurring failure patterns detected yet."
            )
            recommended_action = (
                patterns[0].recommended_mitigation
                if patterns and patterns[0].recommended_mitigation
                else "Continue recording verified release outcomes to strengthen pattern confidence."
            )

        limitations = []
        if perf["qualifying_releases"] < 5:
            limitations.append(
                f"Small sample size ({perf['qualifying_releases']} qualifying releases); treat patterns as exploratory."
            )
        if perf["incomplete_outcomes"] > 0:
            limitations.append(f"{perf['incomplete_outcomes']} release outcome(s) have incomplete metadata.")

        return {
            "question": q,
            "direct_answer": direct_answer,
            "supporting_evidence": {
                "window_days": 90,
                "qualifying_releases": perf["qualifying_releases"],
                "sample_size": perf["total_records"],
                "references": evidence_refs,
            },
            "relevant_releases": [
                {"id": str(r.id), "release_key": r.release_key, "title": r.title, "status": r.status.value}
                for r in recent_releases[:5]
            ],
            "observed_pattern": observed_pattern,
            "confidence_and_limitations": limitations or ["Grounded in persisted ReleaseGuard AI records."],
            "recommended_action": recommended_action,
            "evidence_classification": EvidenceClassification.OBSERVED.value,
        }
