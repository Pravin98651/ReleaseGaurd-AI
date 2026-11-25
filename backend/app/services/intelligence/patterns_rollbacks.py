"""Failure Pattern Analyzer & Rollback Cause Intelligence (Phase 5)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models import (
    EvidenceClassification,
    FailureCategory,
    ProgressiveRollout,
    PullRequest,
    Release,
    ReleaseFailurePattern,
    ReleaseOutcome,
    ReleaseOutcomeType,
    RollbackCauseCategory,
    RollbackOperation,
)
from app.services.intelligence.normalizer_analytics import wilson_interval

MIN_ESTABLISHED_SAMPLE_SIZE = 3


def _classify_rollback_cause(reason: str) -> RollbackCauseCategory:
    r = (reason or "").lower()
    if "payment" in r or "stripe" in r or "card" in r:
        return RollbackCauseCategory.PAYMENT_FAILURE
    if "latency" in r or "slow" in r or "timeout" in r:
        return RollbackCauseCategory.PERFORMANCE_DEGRADATION
    if "error" in r or "exception" in r or "500" in r:
        return RollbackCauseCategory.INCREASED_ERROR_RATE
    if "dependen" in r or "package" in r:
        return RollbackCauseCategory.DEPENDENCY_INCOMPATIBILITY
    if "flag" in r:
        return RollbackCauseCategory.FEATURE_FLAG_PROBLEM
    if "config" in r or "env" in r:
        return RollbackCauseCategory.CONFIGURATION_PROBLEM
    if "test" in r:
        return RollbackCauseCategory.INSUFFICIENT_TESTING
    if "regression" in r:
        return RollbackCauseCategory.FUNCTIONAL_REGRESSION
    return RollbackCauseCategory.UNKNOWN


class FailurePatternAnalyzer:
    """Detects recurring failure patterns with strict minimum-sample safeguards."""

    @staticmethod
    async def analyze_patterns(
        session: AsyncSession,
        *,
        repository_id: uuid.UUID | None = None,
        min_sample_size: int = MIN_ESTABLISHED_SAMPLE_SIZE,
    ) -> list[ReleaseFailurePattern]:
        stmt = select(ReleaseOutcome)
        if repository_id is not None:
            stmt = stmt.where(ReleaseOutcome.repository_id == repository_id)
        outcomes = list((await session.execute(stmt)).scalars().all())
        if not outcomes:
            return []

        # Group outcomes by (repository_id, failure_category)
        total_by_repo: dict[uuid.UUID | None, list[ReleaseOutcome]] = defaultdict(list)
        failed_groups: dict[tuple[uuid.UUID | None, FailureCategory], list[ReleaseOutcome]] = defaultdict(list)
        for o in outcomes:
            total_by_repo[o.repository_id].append(o)
            if o.outcome_type in (ReleaseOutcomeType.FAILURE, ReleaseOutcomeType.ROLLBACK):
                cat = o.failure_category or FailureCategory.UNKNOWN
                failed_groups[(o.repository_id, cat)].append(o)

        patterns: list[ReleaseFailurePattern] = []
        now = utcnow()
        for (repo_id, cat), failures in failed_groups.items():
            sample_size = len(total_by_repo[repo_id])
            fail_count = len(failures)
            observed_rate = round(fail_count / sample_size, 4) if sample_size > 0 else 0.0
            is_exploratory = sample_size < min_sample_size or fail_count < 2
            pattern_key = f"pat:{repo_id or 'workspace'}:{cat.value}"

            existing = (
                await session.execute(
                    select(ReleaseFailurePattern)
                    .where(ReleaseFailurePattern.pattern_key == pattern_key)
                    .limit(1)
                )
            ).scalar_one_or_none()

            mitigation = {
                FailureCategory.PAYMENT_FAILURE: "Require checkout integration tests and a strict payment_failed < 2% guardrail.",
                FailureCategory.DEPENDENCY_INCOMPATIBILITY: "Run dependency compatibility validation before staging promotion.",
                FailureCategory.DATABASE_MIGRATION_PROBLEM: "Require reversible migration review and staging dry-run.",
                FailureCategory.PERFORMANCE_REGRESSION: "Add checkout_latency_p95 guardrail and extend observation windows.",
                FailureCategory.FEATURE_FLAG_MISCONFIGURATION: "Verify Flagsmith default state and segment split via pre-flight check.",
            }.get(cat, "Increase observation duration and verify automated tests before advancing stages.")

            first_obs = min((f.recorded_at for f in failures), default=now)
            last_obs = max((f.recorded_at for f in failures), default=now)
            rel_ids = [str(f.release_id) for f in failures[:25]]
            conf = {
                **wilson_interval(fail_count, sample_size),
                "min_sample_threshold": min_sample_size,
                "note": "Exploratory hypothesis only — sample size below threshold."
                if is_exploratory
                else "Established historical association (correlation, not proven causation).",
            }

            if existing is None:
                existing = ReleaseFailurePattern(
                    pattern_key=pattern_key,
                    name=f"Recurring {cat.value.replace('_', ' ').title()}",
                    description=(
                        f"Observed {fail_count} failed/rolled-back releases out of {sample_size} "
                        f"associated with {cat.value}."
                    ),
                    category=cat,
                    repository_id=repo_id,
                    sample_size=sample_size,
                    failure_count=fail_count,
                    observed_rate=observed_rate,
                    evidence_quality=EvidenceClassification.OBSERVED,
                    is_exploratory=is_exploratory,
                    confidence_data=conf,
                    supporting_release_ids=rel_ids,
                    recommended_mitigation=mitigation,
                    first_observed_at=first_obs,
                    last_observed_at=last_obs,
                    last_analyzed_at=now,
                )
                session.add(existing)
            else:
                existing.sample_size = sample_size
                existing.failure_count = fail_count
                existing.observed_rate = observed_rate
                existing.is_exploratory = is_exploratory
                existing.confidence_data = conf
                existing.supporting_release_ids = rel_ids
                existing.recommended_mitigation = mitigation
                existing.first_observed_at = first_obs
                existing.last_observed_at = last_obs
                existing.last_analyzed_at = now
            patterns.append(existing)

        # Also detect module-level patterns from PR changed_files when available
        pr_ids = [
            o.evidence.get("pr_number")
            for o in outcomes
            if isinstance(o.evidence, dict)
        ]
        if pr_ids:
            releases = list((await session.execute(select(Release))).scalars().all())
            rel_by_id = {r.id: r for r in releases}
            mod_totals: dict[str, int] = defaultdict(int)
            mod_fails: dict[str, list[str]] = defaultdict(list)
            for o in outcomes:
                rel = rel_by_id.get(o.release_id)
                if rel and rel.pull_request_id:
                    pr = await session.get(PullRequest, rel.pull_request_id)
                    if pr and isinstance(pr.confirmed_risks, list):
                        modules = {
                            str(f.get("file_path") or f.get("filename") or "").split("/")[0]
                            for f in pr.confirmed_risks
                            if isinstance(f, dict) and (f.get("file_path") or f.get("filename"))
                        }
                        for mod in modules:
                            mod_totals[mod] += 1
                            if o.outcome_type in (
                                ReleaseOutcomeType.FAILURE,
                                ReleaseOutcomeType.ROLLBACK,
                            ):
                                mod_fails[mod].append(str(o.release_id))
            for mod, fail_rels in mod_fails.items():
                if not fail_rels:
                    continue
                tot = mod_totals[mod]
                fc = len(fail_rels)
                pkey = f"pat:module:{mod}"
                ex_mod = (
                    await session.execute(
                        select(ReleaseFailurePattern)
                        .where(ReleaseFailurePattern.pattern_key == pkey)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                is_exp = tot < min_sample_size or fc < 2
                conf = {
                    **wilson_interval(fc, tot),
                    "min_sample_threshold": min_sample_size,
                }
                if ex_mod is None:
                    ex_mod = ReleaseFailurePattern(
                        pattern_key=pkey,
                        name=f"Module Risk Concentration: {mod}/",
                        description=f"Module `{mod}` appeared in {fc} failed releases out of {tot} total releases touching it.",
                        category=FailureCategory.CODE_REGRESSION,
                        affected_module=mod,
                        sample_size=tot,
                        failure_count=fc,
                        observed_rate=round(fc / tot, 4),
                        evidence_quality=EvidenceClassification.OBSERVED,
                        is_exploratory=is_exp,
                        confidence_data=conf,
                        supporting_release_ids=fail_rels[:25],
                        recommended_mitigation=f"Require targeted tests and canary guardrails for changes in `{mod}/`.",
                        first_observed_at=now,
                        last_observed_at=now,
                        last_analyzed_at=now,
                    )
                    session.add(ex_mod)
                else:
                    ex_mod.sample_size = tot
                    ex_mod.failure_count = fc
                    ex_mod.observed_rate = round(fc / tot, 4)
                    ex_mod.is_exploratory = is_exp
                    ex_mod.confidence_data = conf
                    ex_mod.supporting_release_ids = fail_rels[:25]
                    ex_mod.last_analyzed_at = now
                patterns.append(ex_mod)

        await session.flush()
        return patterns


class RollbackIntelligenceService:
    """Aggregates rollback causes, detection latency, and recovery metrics."""

    @staticmethod
    async def analyze_rollbacks(
        session: AsyncSession,
        *,
        category: str | None = None,
    ) -> dict[str, Any]:
        rollbacks = list(
            (await session.execute(select(RollbackOperation).order_by(RollbackOperation.created_at.desc())))
            .scalars()
            .all()
        )
        cause_counts: dict[str, int] = defaultdict(int)
        by_env: dict[str, int] = defaultdict(int)
        by_pct: dict[str, int] = defaultdict(int)
        recovery_durations: list[float] = []
        items: list[dict[str, Any]] = []

        for rb in rollbacks:
            cause = _classify_rollback_cause(rb.reason)
            if category and cause.value != category:
                continue
            cause_counts[cause.value] += 1
            by_env[rb.environment] += 1
            prev_pct = (rb.previous_configuration or {}).get("rollout_percentage", "unknown")
            by_pct[str(prev_pct)] += 1

            rec_sec: float | None = None
            if rb.completed_at and rb.created_at:
                rec_sec = max(0.0, (rb.completed_at - rb.created_at).total_seconds())
                recovery_durations.append(rec_sec)

            rollout = await session.get(ProgressiveRollout, rb.rollout_id)
            items.append(
                {
                    "id": str(rb.id),
                    "rollout_id": str(rb.rollout_id),
                    "release_id": str(rollout.release_id) if rollout and rollout.release_id else None,
                    "environment": rb.environment,
                    "cause_category": cause.value,
                    "reason": rb.reason,
                    "status": rb.status.value,
                    "percentage_at_rollback": prev_pct,
                    "recovery_seconds": rec_sec,
                    "created_at": rb.created_at.isoformat(),
                }
            )

        sorted_causes = sorted(cause_counts.items(), key=lambda kv: kv[1], reverse=True)
        return {
            "total_rollbacks": len(items),
            "top_causes": [{"category": k, "count": v} for k, v in sorted_causes],
            "by_environment": dict(by_env),
            "by_rollout_percentage": dict(by_pct),
            "avg_recovery_seconds": (
                round(sum(recovery_durations) / len(recovery_durations), 1)
                if recovery_durations
                else None
            ),
            "rollbacks": items,
        }
