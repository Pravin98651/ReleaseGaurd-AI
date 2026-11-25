"""AI Agent Recommendation Outcome Evaluation Service (Phase 5)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models import (
    AgentDecision,
    AgentDecisionStatus,
    AgentExecution,
    RecommendationOutcome,
    RecommendationOutcomeClass,
    ReleaseOutcome,
    ReleaseOutcomeType,
)


def _categorize_decision(decision: AgentDecision) -> str:
    txt = f"{decision.decision_type} {decision.recommendation or ''} {decision.decision_summary}".lower()
    if "rollback" in txt:
        return "rollback"
    if "guardrail" in txt:
        return "add_guardrail"
    if "test" in txt:
        return "add_tests"
    if "observation" in txt or "window" in txt:
        return "increase_observation"
    if "dependen" in txt or "package" in txt:
        return "review_dependency"
    if "flag" in txt or "rollout" in txt or "percentage" in txt:
        return "modify_flag_rollout"
    if "approval" in txt:
        return "request_approval"
    return decision.decision_type or "general"


class RecommendationEvaluationService:
    """Evaluates AI AgentDecision recommendations against verified ReleaseOutcomes."""

    @staticmethod
    async def evaluate_decision(
        session: AsyncSession,
        decision: AgentDecision,
        *,
        implemented_override: bool | None = None,
    ) -> RecommendationOutcome:
        execution = await session.get(AgentExecution, decision.execution_id)
        release_id = execution.release_id if execution else None
        repo_id = execution.repository_id if execution else None

        outcome: ReleaseOutcome | None = None
        if release_id is not None:
            outcome = (
                await session.execute(
                    select(ReleaseOutcome).where(ReleaseOutcome.release_id == release_id).limit(1)
                )
            ).scalar_one_or_none()

        accepted: bool | None = None
        if decision.decision_status == AgentDecisionStatus.ACCEPTED:
            accepted = True
        elif decision.decision_status == AgentDecisionStatus.REJECTED:
            accepted = False

        implemented = (
            implemented_override
            if implemented_override is not None
            else (decision.decision_status == AgentDecisionStatus.ACCEPTED)
        )
        verified = bool(outcome is not None and outcome.verified)

        # Classify outcome honestly — acceptance alone NEVER implies positive outcome
        if outcome is None or outcome.outcome_type == ReleaseOutcomeType.UNKNOWN:
            classification = RecommendationOutcomeClass.NOT_EVALUABLE
            actual_outcome = "No normalized terminal release outcome available yet."
        elif not implemented:
            classification = RecommendationOutcomeClass.INCONCLUSIVE
            actual_outcome = (
                f"Recommendation was {'rejected' if accepted is False else 'not implemented'}; "
                f"release ended as {outcome.outcome_type.value}."
            )
        elif outcome.outcome_type == ReleaseOutcomeType.SUCCESS and outcome.verified:
            classification = RecommendationOutcomeClass.VERIFIED_BENEFIT
            actual_outcome = (
                "Recommendation implemented and release reached verified completion without failure."
            )
        elif outcome.outcome_type in (ReleaseOutcomeType.SUCCESS, ReleaseOutcomeType.PARTIAL_SUCCESS):
            classification = RecommendationOutcomeClass.OBSERVED_POSITIVE_OUTCOME
            actual_outcome = (
                f"Recommendation implemented; release completed as {outcome.outcome_type.value} (unverified)."
            )
        elif outcome.outcome_type in (ReleaseOutcomeType.FAILURE, ReleaseOutcomeType.ROLLBACK):
            classification = RecommendationOutcomeClass.NEGATIVE_OUTCOME
            actual_outcome = (
                f"Recommendation implemented, but release resulted in {outcome.outcome_type.value}."
            )
        else:
            classification = RecommendationOutcomeClass.INCONCLUSIVE
            actual_outcome = f"Release ended as {outcome.outcome_type.value}."

        category = _categorize_decision(decision)
        evidence = {
            "decision_status": decision.decision_status.value,
            "model_name": decision.model_name,
            "agent_type": execution.agent_type if execution else None,
            "release_outcome_type": outcome.outcome_type.value if outcome else None,
            "release_verified": verified,
        }

        existing = (
            await session.execute(
                select(RecommendationOutcome)
                .where(RecommendationOutcome.recommendation_id == decision.id)
                .limit(1)
            )
        ).scalar_one_or_none()

        now = utcnow()
        if existing is None:
            existing = RecommendationOutcome(
                recommendation_id=decision.id,
                release_id=release_id,
                repository_id=repo_id,
                recommendation_category=category,
                outcome_classification=classification,
                accepted=accepted,
                implemented=implemented,
                verified=verified,
                actual_outcome=actual_outcome,
                evidence=evidence,
                evaluation_method="deterministic_outcome_match",
                evaluated_at=now,
            )
            session.add(existing)
        else:
            existing.release_id = release_id
            existing.repository_id = repo_id
            existing.recommendation_category = category
            existing.outcome_classification = classification
            existing.accepted = accepted
            existing.implemented = implemented
            existing.verified = verified
            existing.actual_outcome = actual_outcome
            existing.evidence = evidence
            existing.evaluated_at = now

        await session.flush()
        return existing

    @staticmethod
    async def evaluate_all(session: AsyncSession) -> list[RecommendationOutcome]:
        decisions = list(
            (await session.execute(select(AgentDecision).order_by(AgentDecision.created_at.desc()).limit(300)))
            .scalars()
            .all()
        )
        results: list[RecommendationOutcome] = []
        for d in decisions:
            results.append(await RecommendationEvaluationService.evaluate_decision(session, d))
        return results

    @staticmethod
    async def compute_accuracy_metrics(
        session: AsyncSession,
        *,
        repository_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        stmt = select(RecommendationOutcome)
        if repository_id is not None:
            stmt = stmt.where(RecommendationOutcome.repository_id == repository_id)
        rows = list((await session.execute(stmt)).scalars().all())

        total = len(rows)
        responded = [r for r in rows if r.accepted is not None]
        accepted = [r for r in rows if r.accepted is True]
        rejected = [r for r in rows if r.accepted is False]
        implemented = [r for r in rows if r.implemented]
        verified = [r for r in rows if r.verified]

        evaluable = [
            r
            for r in rows
            if r.outcome_classification
            in (
                RecommendationOutcomeClass.VERIFIED_BENEFIT,
                RecommendationOutcomeClass.OBSERVED_POSITIVE_OUTCOME,
                RecommendationOutcomeClass.NO_MEASURABLE_IMPACT,
                RecommendationOutcomeClass.NEGATIVE_OUTCOME,
            )
        ]
        positive = [
            r
            for r in evaluable
            if r.outcome_classification
            in (
                RecommendationOutcomeClass.VERIFIED_BENEFIT,
                RecommendationOutcomeClass.OBSERVED_POSITIVE_OUTCOME,
            )
        ]
        negative = [
            r
            for r in evaluable
            if r.outcome_classification == RecommendationOutcomeClass.NEGATIVE_OUTCOME
        ]
        inconclusive = [
            r
            for r in rows
            if r.outcome_classification
            in (RecommendationOutcomeClass.INCONCLUSIVE, RecommendationOutcomeClass.NOT_EVALUABLE)
        ]

        by_category: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "accepted": 0, "positive": 0, "negative": 0}
        )
        by_model: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "accepted": 0, "positive": 0, "negative": 0}
        )
        by_agent: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "accepted": 0, "positive": 0, "negative": 0}
        )

        for r in rows:
            cat = r.recommendation_category
            model = str((r.evidence or {}).get("model_name") or "unknown")
            agent = str((r.evidence or {}).get("agent_type") or "unknown")
            for bucket in (by_category[cat], by_model[model], by_agent[agent]):
                bucket["total"] += 1
                if r.accepted is True:
                    bucket["accepted"] += 1
                if r.outcome_classification in (
                    RecommendationOutcomeClass.VERIFIED_BENEFIT,
                    RecommendationOutcomeClass.OBSERVED_POSITIVE_OUTCOME,
                ):
                    bucket["positive"] += 1
                elif r.outcome_classification == RecommendationOutcomeClass.NEGATIVE_OUTCOME:
                    bucket["negative"] += 1

        return {
            "total_recommendations": total,
            "responded_count": len(responded),
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "implemented_count": len(implemented),
            "verified_count": len(verified),
            "evaluable_count": len(evaluable),
            "positive_outcome_count": len(positive),
            "negative_outcome_count": len(negative),
            "inconclusive_or_not_evaluable_count": len(inconclusive),
            "acceptance_rate": round(len(accepted) / len(responded), 4) if responded else None,
            "implementation_rate": round(len(implemented) / len(accepted), 4) if accepted else None,
            "verification_rate": round(len(verified) / len(implemented), 4) if implemented else None,
            "positive_outcome_rate": round(len(positive) / len(evaluable), 4) if evaluable else None,
            "by_category": dict(by_category),
            "by_model": dict(by_model),
            "by_agent": dict(by_agent),
            "methodology_note": (
                "Acceptance rate uses human-responded recommendations as denominator; "
                "positive_outcome_rate uses only evaluable implemented recommendations."
            ),
        }
