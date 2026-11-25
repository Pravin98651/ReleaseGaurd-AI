"""Repository Health Scoring, Shared Org Policies, and Org Risk Intelligence (Phase 7)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models import (
    ActorType,
    AuditLog,
    AuditStatus,
    OrganizationReleasePolicy,
    OrganizationRepository,
    OrganizationRiskSnapshot,
    PolicyCategory,
    Release,
    ReleaseOutcome,
    ReleaseOutcomeType,
    ReleaseStatus,
    RepoHealthCategory,
    Repository,
    RepositoryDependency,
    RepositoryHealthSnapshot,
    RepositoryPolicyAssignment,
)
from app.services.organization.org_registry_deps import OrganizationError


class RepositoryHealthScoringService:
    """Computes transparent, deterministic repository health snapshots."""

    @staticmethod
    async def compute_health(
        session: AsyncSession, *, repository_id: uuid.UUID
    ) -> RepositoryHealthSnapshot:
        repo = await session.get(Repository, repository_id)
        if repo is None:
            raise OrganizationError("Repository not found.", status_code=404)

        outcomes = list(
            (
                await session.execute(
                    select(ReleaseOutcome).where(ReleaseOutcome.repository_id == repository_id)
                )
            )
            .scalars()
            .all()
        )
        qual = [
            o
            for o in outcomes
            if o.outcome_type
            in (
                ReleaseOutcomeType.SUCCESS,
                ReleaseOutcomeType.PARTIAL_SUCCESS,
                ReleaseOutcomeType.FAILURE,
                ReleaseOutcomeType.ROLLBACK,
            )
        ]
        n = len(qual)
        if n < 2:
            snap = RepositoryHealthSnapshot(
                repository_id=repository_id,
                health_score=None,
                health_category=RepoHealthCategory.INSUFFICIENT_DATA,
                evidence={
                    "reason": f"Requires at least 2 qualifying releases; found {n}.",
                    "repository_name": repo.full_name,
                },
                sample_size=n,
                data_completeness=round(n / 2.0, 2),
            )
            session.add(snap)
            await session.flush()
            return snap

        failures = sum(
            1 for o in qual if o.outcome_type in (ReleaseOutcomeType.FAILURE, ReleaseOutcomeType.ROLLBACK)
        )
        rollbacks = sum(1 for o in qual if o.rollback_required)
        fail_rate = failures / n
        rb_rate = rollbacks / n

        # Transparent deterministic score: starts at 100, deducts up to 70 for failures + 20 for rollbacks
        score = round(max(0.0, 100.0 - (fail_rate * 70.0) - (rb_rate * 20.0)), 1)
        if score >= 85:
            cat = RepoHealthCategory.HEALTHY
        elif score >= 70:
            cat = RepoHealthCategory.STABLE
        elif score >= 45:
            cat = RepoHealthCategory.NEEDS_ATTENTION
        else:
            cat = RepoHealthCategory.HIGH_RISK

        snap = RepositoryHealthSnapshot(
            repository_id=repository_id,
            health_score=score,
            health_category=cat,
            evidence={
                "repository_name": repo.full_name,
                "qualifying_releases": n,
                "failures": failures,
                "rollbacks": rollbacks,
                "failure_rate": round(fail_rate, 4),
                "rollback_rate": round(rb_rate, 4),
            },
            sample_size=n,
            data_completeness=1.0,
        )
        session.add(snap)
        await session.flush()
        return snap


class OrganizationReleasePolicyService:
    """Manages shared organization-wide release policies and precedence enforcement."""

    @staticmethod
    async def create_policy(
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        name: str,
        policy_type: str,
        configuration: dict[str, Any],
        is_mandatory: bool = True,
        created_by_id: uuid.UUID | None = None,
    ) -> OrganizationReleasePolicy:
        pol = OrganizationReleasePolicy(
            organization_id=organization_id,
            name=name,
            policy_type=policy_type,
            configuration=configuration,
            is_mandatory=is_mandatory,
            status="draft",
            created_by_id=created_by_id,
        )
        session.add(pol)
        await session.flush()
        return pol

    @staticmethod
    async def publish_policy(
        session: AsyncSession, *, policy: OrganizationReleasePolicy, approver_id: uuid.UUID
    ) -> OrganizationReleasePolicy:
        policy.status = "active"
        policy.approved_by_id = approver_id
        session.add(
            AuditLog(
                actor_type=ActorType.USER,
                actor_id=str(approver_id),
                policy_category=PolicyCategory.C,
                event_action="organization.policy.published",
                resource_type="organization_release_policy",
                resource_id=str(policy.id),
                status=AuditStatus.SUCCESS,
                details={"name": policy.name, "version": policy.version},
            )
        )
        await session.flush()
        return policy

    @staticmethod
    async def assign_or_override(
        session: AsyncSession,
        *,
        repository_id: uuid.UUID,
        policy: OrganizationReleasePolicy,
        override_configuration: dict[str, Any] | None = None,
        exception_approved_by_id: uuid.UUID | None = None,
    ) -> RepositoryPolicyAssignment:
        if override_configuration and policy.is_mandatory and exception_approved_by_id is None:
            raise OrganizationError(
                "Mandatory organization policy cannot be weakened without an approved exception.",
                status_code=403,
            )
        existing = (
            await session.execute(
                select(RepositoryPolicyAssignment)
                .where(
                    RepositoryPolicyAssignment.repository_id == repository_id,
                    RepositoryPolicyAssignment.policy_id == policy.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = RepositoryPolicyAssignment(
                repository_id=repository_id,
                policy_id=policy.id,
                override_configuration=override_configuration,
                exception_status="approved" if override_configuration else "none",
                approved_by_id=exception_approved_by_id,
            )
            session.add(existing)
        else:
            existing.override_configuration = override_configuration
            existing.exception_status = "approved" if override_configuration else "none"
            existing.approved_by_id = exception_approved_by_id
        await session.flush()
        return existing


class OrganizationRiskIntelligenceService:
    """Aggregates organization-wide risk snapshots, hotspots, and grounded Q&A."""

    @staticmethod
    async def compute_risk_snapshot(
        session: AsyncSession, *, organization_id: uuid.UUID
    ) -> OrganizationRiskSnapshot:
        links = list(
            (
                await session.execute(
                    select(OrganizationRepository).where(
                        OrganizationRepository.organization_id == organization_id
                    )
                )
            )
            .scalars()
            .all()
        )
        repo_ids = [r.repository_id for r in links]
        releases = (
            list(
                (await session.execute(select(Release).where(Release.repository_id.in_(repo_ids))))
                .scalars()
                .all()
            )
            if repo_ids
            else []
        )
        failures = sum(
            1 for r in releases if r.status in (ReleaseStatus.FAILED, ReleaseStatus.ROLLED_BACK)
        )
        rollbacks = sum(1 for r in releases if r.status == ReleaseStatus.ROLLED_BACK)

        edges = list(
            (
                await session.execute(
                    select(RepositoryDependency).where(
                        RepositoryDependency.organization_id == organization_id
                    )
                )
            )
            .scalars()
            .all()
        )
        dependents_by_target: dict[uuid.UUID, int] = defaultdict(int)
        for e in edges:
            dependents_by_target[e.target_repository_id] += 1
        hotspots = [
            {"repository_id": str(tid), "dependent_count": cnt}
            for tid, cnt in sorted(dependents_by_target.items(), key=lambda kv: kv[1], reverse=True)
            if cnt >= 2
        ]

        snap = OrganizationRiskSnapshot(
            organization_id=organization_id,
            analysis_period="30d",
            repository_count=len(repo_ids),
            release_count=len(releases),
            failure_count=failures,
            rollback_count=rollbacks,
            risk_summary={
                "dependency_hotspots": hotspots,
                "failure_rate": round(failures / len(releases), 4) if releases else None,
            },
            calculated_at=utcnow(),
        )
        session.add(snap)
        await session.flush()
        return snap

    @staticmethod
    async def query_organization(
        session: AsyncSession, *, organization_id: uuid.UUID, question: str
    ) -> dict[str, Any]:
        snap = await OrganizationRiskIntelligenceService.compute_risk_snapshot(
            session, organization_id=organization_id
        )
        hotspots = snap.risk_summary.get("dependency_hotspots") or []
        return {
            "organization_id": str(organization_id),
            "question": question,
            "direct_answer": (
                f"Organization tracks {snap.repository_count} repositories and {snap.release_count} releases "
                f"({snap.failure_count} failures, {snap.rollback_count} rollbacks). "
                f"Identified {len(hotspots)} shared dependency hotspot(s)."
            ),
            "supporting_evidence": {
                "repository_count": snap.repository_count,
                "release_count": snap.release_count,
                "dependency_hotspots": hotspots,
            },
        }
