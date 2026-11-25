"""Historical release outcome normalization and DORA/rollout analytics (Phase 5)."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models import (
    Approval,
    ApprovalStatus,
    EvidenceClassification,
    FailureCategory,
    GuardrailEvalStatus,
    GuardrailEvaluation,
    ProgressiveRollout,
    PullRequest,
    Release,
    ReleaseOutcome,
    ReleaseOutcomeType,
    ReleaseStatus,
    RollbackOperation,
    RollbackOperationStatus,
)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def wilson_interval(k: int, n: int, z: float = 1.96) -> dict[str, Any]:
    """Compute Wilson 95% binomial confidence interval for k/n."""
    if n <= 0:
        return {"lower": None, "upper": None, "sample_size": 0, "sufficient": False}
    p = k / n
    denom = 1.0 + (z * z) / n
    center = (p + (z * z) / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * n)) / n)) / denom
    return {
        "lower": round(max(0.0, center - half), 4),
        "upper": round(min(1.0, center + half), 4),
        "sample_size": n,
        "sufficient": n >= 3,
    }


def _infer_failure_categories(
    release: Release,
    pr: PullRequest | None,
    rollbacks: list[RollbackOperation],
) -> tuple[FailureCategory | None, list[str]]:
    categories: list[str] = []
    text = " ".join(
        filter(
            None,
            [
                release.title,
                release.description,
                pr.title if pr else None,
                *(rb.reason for rb in rollbacks),
            ],
        )
    ).lower()

    if "payment" in text or "stripe" in text or "card" in text:
        categories.append(FailureCategory.PAYMENT_FAILURE.value)
    if "checkout" in text or "regression" in text:
        categories.append(FailureCategory.CODE_REGRESSION.value)
    if "dependen" in text or "package" in text or "upgrade" in text or "version" in text:
        categories.append(FailureCategory.DEPENDENCY_INCOMPATIBILITY.value)
    if "migrat" in text or "schema" in text or "alembic" in text:
        categories.append(FailureCategory.DATABASE_MIGRATION_PROBLEM.value)
    if "latency" in text or "timeout" in text or "slow" in text or "perf" in text:
        categories.append(FailureCategory.PERFORMANCE_REGRESSION.value)
    if "flag" in text or "rollout" in text:
        categories.append(FailureCategory.FEATURE_FLAG_MISCONFIGURATION.value)
    if "test" in text:
        categories.append(FailureCategory.INSUFFICIENT_TEST_COVERAGE.value)
    if "auth" in text or "permission" in text or "403" in text or "401" in text:
        categories.append(FailureCategory.AUTH_FAILURE.value)

    if not categories:
        categories.append(FailureCategory.UNKNOWN.value)
    primary = FailureCategory(categories[0])
    return primary, categories


class ReleaseOutcomeNormalizer:
    """Normalizes persisted Release lifecycle records into ReleaseOutcome rows."""

    @staticmethod
    async def normalize_release(session: AsyncSession, release: Release) -> ReleaseOutcome:
        existing = (
            await session.execute(
                select(ReleaseOutcome).where(ReleaseOutcome.release_id == release.id).limit(1)
            )
        ).scalar_one_or_none()

        pr = (
            await session.get(PullRequest, release.pull_request_id)
            if release.pull_request_id
            else None
        )
        rollouts = list(
            (
                await session.execute(
                    select(ProgressiveRollout).where(ProgressiveRollout.release_id == release.id)
                )
            )
            .scalars()
            .all()
        )
        rollout_ids = [r.id for r in rollouts]
        rollbacks: list[RollbackOperation] = []
        if rollout_ids:
            rollbacks = list(
                (
                    await session.execute(
                        select(RollbackOperation).where(
                            RollbackOperation.rollout_id.in_(rollout_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )

        approvals = list(
            (
                await session.execute(
                    select(Approval).where(Approval.release_id == release.id)
                )
            )
            .scalars()
            .all()
        )

        # Determine canonical outcome_type
        rollback_required = (
            release.status == ReleaseStatus.ROLLED_BACK
            or any(rb.status == RollbackOperationStatus.COMPLETED for rb in rollbacks)
        )
        if rollback_required:
            outcome_type = ReleaseOutcomeType.ROLLBACK
        elif release.status == ReleaseStatus.COMPLETED:
            outcome_type = ReleaseOutcomeType.SUCCESS
        elif release.status == ReleaseStatus.FAILED:
            outcome_type = ReleaseOutcomeType.FAILURE
        elif release.status == ReleaseStatus.CANCELLED:
            started = _ensure_utc(getattr(release, "started_at", None))
            outcome_type = (
                ReleaseOutcomeType.CANCELLED_POST_DEPLOY
                if started is not None
                else ReleaseOutcomeType.CANCELLED_PRE_DEPLOY
            )
        else:
            outcome_type = ReleaseOutcomeType.UNKNOWN

        failure_cat: FailureCategory | None = None
        contributing: list[str] = []
        if outcome_type in (ReleaseOutcomeType.FAILURE, ReleaseOutcomeType.ROLLBACK):
            failure_cat, contributing = _infer_failure_categories(release, pr, rollbacks)

        created_at = _ensure_utc(release.created_at)
        started_at = _ensure_utc(getattr(release, "started_at", None)) or created_at
        completed_at = _ensure_utc(getattr(release, "completed_at", None))
        pr_created = _ensure_utc(pr.created_at) if pr else None

        release_duration = (
            max(0, int((completed_at - started_at).total_seconds()))
            if completed_at and started_at
            else None
        )
        lead_time = (
            max(0, int((completed_at - (pr_created or created_at)).total_seconds()))
            if completed_at and (pr_created or created_at)
            else None
        )

        recovery_duration: int | None = None
        completed_rbs = [rb for rb in rollbacks if rb.completed_at and rb.created_at]
        if completed_rbs:
            rb_durations = [
                max(0, int((_ensure_utc(rb.completed_at) - _ensure_utc(rb.created_at)).total_seconds()))
                for rb in completed_rbs
            ]
            recovery_duration = int(sum(rb_durations) / len(rb_durations))

        approval_latency: int | None = None
        decided_apps = [
            a for a in approvals if a.decided_at is not None and a.status != ApprovalStatus.PENDING
        ]
        if decided_apps:
            latencies = [
                max(0, int((_ensure_utc(a.decided_at) - _ensure_utc(a.created_at)).total_seconds()))
                for a in decided_apps
            ]
            approval_latency = int(sum(latencies) / len(latencies))

        quality_flags: list[str] = []
        if release.repository_id is None:
            quality_flags.append("missing_repository_association")
        if completed_at is None and outcome_type != ReleaseOutcomeType.UNKNOWN:
            quality_flags.append("incomplete_completion_timestamp")
        if outcome_type == ReleaseOutcomeType.UNKNOWN:
            quality_flags.append("non_terminal_or_unknown_outcome")

        verified = (
            release.status == ReleaseStatus.COMPLETED
            or any(rb.status == RollbackOperationStatus.COMPLETED for rb in rollbacks)
        )
        ev_class = (
            EvidenceClassification.VERIFIED if verified else EvidenceClassification.OBSERVED
        )

        payload = {
            "release_key": release.release_key,
            "release_type": release.release_type.value,
            "pr_number": pr.github_pr_number if pr else None,
            "rollback_count": len(rollbacks),
            "rollout_count": len(rollouts),
        }

        if existing is None:
            existing = ReleaseOutcome(
                release_id=release.id,
                repository_id=release.repository_id,
                outcome_type=outcome_type,
                failure_category=failure_cat,
                contributing_categories=contributing,
                final_status=release.status.value,
                environment=release.target_environment,
                release_duration_seconds=release_duration,
                recovery_duration_seconds=recovery_duration,
                lead_time_seconds=lead_time,
                approval_latency_seconds=approval_latency,
                rollback_required=rollback_required,
                evidence_classification=ev_class,
                evidence=payload,
                data_quality_flags=quality_flags,
                verified=verified,
                recorded_at=completed_at or utcnow(),
            )
            session.add(existing)
        else:
            existing.repository_id = release.repository_id
            existing.outcome_type = outcome_type
            existing.failure_category = failure_cat
            existing.contributing_categories = contributing
            existing.final_status = release.status.value
            existing.environment = release.target_environment
            existing.release_duration_seconds = release_duration
            existing.recovery_duration_seconds = recovery_duration
            existing.lead_time_seconds = lead_time
            existing.approval_latency_seconds = approval_latency
            existing.rollback_required = rollback_required
            existing.evidence_classification = ev_class
            existing.evidence = payload
            existing.data_quality_flags = quality_flags
            existing.verified = verified
        await session.flush()
        return existing

    @staticmethod
    async def normalize_all(
        session: AsyncSession, *, repository_id: uuid.UUID | None = None
    ) -> list[ReleaseOutcome]:
        stmt = select(Release)
        if repository_id is not None:
            stmt = stmt.where(Release.repository_id == repository_id)
        releases = list((await session.execute(stmt.order_by(Release.created_at.desc()).limit(500))).scalars().all())
        outcomes: list[ReleaseOutcome] = []
        for rel in releases:
            outcomes.append(await ReleaseOutcomeNormalizer.normalize_release(session, rel))
        return outcomes


class HistoricalReleaseAnalyticsService:
    """Computes DORA & progressive rollout KPIs with explicit denominators and data-quality metadata."""

    @staticmethod
    async def compute_performance(
        session: AsyncSession,
        *,
        repository_id: uuid.UUID | None = None,
        environment: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        since = utcnow() - timedelta(days=max(1, days))
        stmt = select(ReleaseOutcome).where(ReleaseOutcome.recorded_at >= since)
        if repository_id is not None:
            stmt = stmt.where(ReleaseOutcome.repository_id == repository_id)
        if environment:
            stmt = stmt.where(ReleaseOutcome.environment == environment.strip().lower())
        outcomes = list((await session.execute(stmt)).scalars().all())

        # Exclude draft/unknown/pre-deploy-cancelled from executed deployment denominator
        qualifying = [
            o
            for o in outcomes
            if o.outcome_type
            in (
                ReleaseOutcomeType.SUCCESS,
                ReleaseOutcomeType.PARTIAL_SUCCESS,
                ReleaseOutcomeType.FAILURE,
                ReleaseOutcomeType.ROLLBACK,
                ReleaseOutcomeType.CANCELLED_POST_DEPLOY,
            )
        ]
        succeeded = [
            o
            for o in qualifying
            if o.outcome_type in (ReleaseOutcomeType.SUCCESS, ReleaseOutcomeType.PARTIAL_SUCCESS)
        ]
        failed = [
            o
            for o in qualifying
            if o.outcome_type in (ReleaseOutcomeType.FAILURE, ReleaseOutcomeType.ROLLBACK)
        ]
        rolled_back = [o for o in qualifying if o.rollback_required]
        verified_count = sum(1 for o in outcomes if o.verified)
        incomplete_count = sum(1 for o in outcomes if o.data_quality_flags)

        denom = len(qualifying)
        change_failure_rate = round(len(failed) / denom, 4) if denom > 0 else None
        rollback_rate = round(len(rolled_back) / denom, 4) if denom > 0 else None
        release_success_rate = round(len(succeeded) / denom, 4) if denom > 0 else None

        rec_vals = [o.recovery_duration_seconds for o in failed if o.recovery_duration_seconds is not None]
        mttr_sec = round(sum(rec_vals) / len(rec_vals), 1) if rec_vals else None

        lead_vals = [o.lead_time_seconds for o in succeeded if o.lead_time_seconds is not None]
        lead_time_sec = round(sum(lead_vals) / len(lead_vals), 1) if lead_vals else None

        app_vals = [o.approval_latency_seconds for o in outcomes if o.approval_latency_seconds is not None]
        approval_latency_sec = round(sum(app_vals) / len(app_vals), 1) if app_vals else None

        # Guardrail breach rate across progressive rollout evaluations
        eval_stmt = select(GuardrailEvaluation).where(GuardrailEvaluation.evaluated_at >= since)
        evals = list((await session.execute(eval_stmt)).scalars().all())
        breaches = sum(1 for e in evals if e.status == GuardrailEvalStatus.BREACH)
        guardrail_breach_rate = round(breaches / len(evals), 4) if evals else None

        return {
            "window_days": days,
            "total_records": len(outcomes),
            "qualifying_releases": denom,
            "successful_releases": len(succeeded),
            "failed_releases": len(failed),
            "rolled_back_releases": len(rolled_back),
            "verified_outcomes": verified_count,
            "incomplete_outcomes": incomplete_count,
            "deployment_frequency_per_week": round((len(succeeded) / max(1, days)) * 7.0, 2),
            "change_failure_rate": change_failure_rate,
            "change_failure_rate_ci": wilson_interval(len(failed), denom),
            "rollback_rate": rollback_rate,
            "release_success_rate": release_success_rate,
            "mean_time_to_recovery_seconds": mttr_sec,
            "lead_time_seconds": lead_time_sec,
            "approval_latency_seconds": approval_latency_sec,
            "guardrail_breach_rate": guardrail_breach_rate,
            "guardrail_evaluations_count": len(evals),
            "data_sufficiency": "sufficient" if denom >= 5 else "insufficient_sample",
            "definitions": {
                "qualifying_denominator": "Excludes non-terminal releases and pre-deployment cancellations.",
                "change_failure_rate": "failed_or_rolled_back / qualifying_releases",
                "lead_time": "completed_at - (pr.created_at or release.created_at)",
            },
        }

    @staticmethod
    async def compute_trends(
        session: AsyncSession,
        *,
        repository_id: uuid.UUID | None = None,
        environment: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        current = await HistoricalReleaseAnalyticsService.compute_performance(
            session, repository_id=repository_id, environment=environment, days=days
        )
        now = utcnow()
        prev_start = now - timedelta(days=days * 2)
        prev_end = now - timedelta(days=days)
        stmt = select(ReleaseOutcome).where(
            ReleaseOutcome.recorded_at >= prev_start,
            ReleaseOutcome.recorded_at < prev_end,
        )
        if repository_id is not None:
            stmt = stmt.where(ReleaseOutcome.repository_id == repository_id)
        if environment:
            stmt = stmt.where(ReleaseOutcome.environment == environment.strip().lower())
        prev_outcomes = list((await session.execute(stmt)).scalars().all())
        prev_qual = [
            o
            for o in prev_outcomes
            if o.outcome_type
            in (
                ReleaseOutcomeType.SUCCESS,
                ReleaseOutcomeType.PARTIAL_SUCCESS,
                ReleaseOutcomeType.FAILURE,
                ReleaseOutcomeType.ROLLBACK,
            )
        ]
        prev_failed = [
            o
            for o in prev_qual
            if o.outcome_type in (ReleaseOutcomeType.FAILURE, ReleaseOutcomeType.ROLLBACK)
        ]
        prev_cfr = round(len(prev_failed) / len(prev_qual), 4) if prev_qual else None

        direction = "insufficient_history"
        if current["change_failure_rate"] is not None and prev_cfr is not None:
            delta = current["change_failure_rate"] - prev_cfr
            if abs(delta) < 0.02:
                direction = "stable"
            elif delta < 0:
                direction = "improving"
            else:
                direction = "degrading"

        return {
            "current_period": current,
            "previous_period": {
                "window_days": days,
                "qualifying_releases": len(prev_qual),
                "failed_releases": len(prev_failed),
                "change_failure_rate": prev_cfr,
            },
            "trend_direction": direction,
        }
