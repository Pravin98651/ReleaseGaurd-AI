"""Organization Management, Cross-Repo Dependency Graph, and Coordination Services (Phase 7)."""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models import (
    Approval,
    ApprovalStatus,
    CoordinatedPlanStatus,
    CoordinatedReleaseItem,
    CoordinatedReleasePlan,
    CrossRepositoryImpactAssessment,
    DependencyType,
    DependencyVerificationStatus,
    Organization,
    OrganizationRepository,
    ProgressiveRollout,
    ProgressiveRolloutStatus,
    Release,
    ReleaseOutcome,
    ReleaseOutcomeType,
    ReleaseStatus,
    Repository,
    RepositoryDependency,
)


class OrganizationError(Exception):
    """Domain error for Phase 7 Multi-Repository Release Intelligence."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class OrganizationManagementService:
    """Manages Organizations, RepositoryRegistry, and Cross-Repository Release Overviews."""

    @staticmethod
    async def create_organization(
        session: AsyncSession,
        *,
        name: str,
        slug: str,
        github_org_id: int | None = None,
    ) -> Organization:
        existing = (
            await session.execute(select(Organization).where(Organization.slug == slug).limit(1))
        ).scalar_one_or_none()
        if existing is not None:
            raise OrganizationError(f"Organization slug '{slug}' already exists.", status_code=409)
        org = Organization(name=name, slug=slug, github_org_id=github_org_id)
        session.add(org)
        await session.flush()
        return org

    @staticmethod
    async def register_repository(
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        repository_id: uuid.UUID,
    ) -> OrganizationRepository:
        repo = await session.get(Repository, repository_id)
        if repo is None:
            raise OrganizationError("Repository not found.", status_code=404)
        existing = (
            await session.execute(
                select(OrganizationRepository)
                .where(
                    OrganizationRepository.organization_id == organization_id,
                    OrganizationRepository.repository_id == repository_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        link = OrganizationRepository(
            organization_id=organization_id,
            repository_id=repository_id,
            github_repository_id=repo.github_repo_id,
            last_synced_at=utcnow(),
        )
        session.add(link)
        await session.flush()
        return link

    @staticmethod
    async def get_release_overview(
        session: AsyncSession, *, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        repo_links = list(
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
        repo_ids = [r.repository_id for r in repo_links]
        if not repo_ids:
            return {
                "organization_id": str(organization_id),
                "total_repositories": 0,
                "total_releases": 0,
                "active_releases": 0,
                "successful_releases": 0,
                "failed_releases": 0,
                "blocked_releases": 0,
                "active_rollouts": 0,
                "pending_approvals": 0,
                "change_failure_rate": None,
            }

        releases = list(
            (await session.execute(select(Release).where(Release.repository_id.in_(repo_ids))))
            .scalars()
            .all()
        )
        outcomes = list(
            (
                await session.execute(
                    select(ReleaseOutcome).where(ReleaseOutcome.repository_id.in_(repo_ids))
                )
            )
            .scalars()
            .all()
        )
        rollouts = list(
            (
                await session.execute(
                    select(ProgressiveRollout).where(
                        ProgressiveRollout.repository_id.in_(repo_ids),
                        ProgressiveRollout.status.in_(
                            [
                                ProgressiveRolloutStatus.OBSERVING,
                                ProgressiveRolloutStatus.EVALUATING,
                                ProgressiveRolloutStatus.ADVANCE_RECOMMENDED,
                            ]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        rel_ids = [r.id for r in releases]
        pending_apps = 0
        if rel_ids:
            pending_apps = len(
                list(
                    (
                        await session.execute(
                            select(Approval).where(
                                Approval.release_id.in_(rel_ids),
                                Approval.status == ApprovalStatus.PENDING,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            )

        succeeded = sum(1 for r in releases if r.status == ReleaseStatus.COMPLETED)
        failed = sum(
            1 for r in releases if r.status in (ReleaseStatus.FAILED, ReleaseStatus.ROLLED_BACK)
        )
        blocked = sum(1 for r in releases if r.status == ReleaseStatus.BLOCKED)
        active = sum(
            1
            for r in releases
            if r.status
            not in (
                ReleaseStatus.COMPLETED,
                ReleaseStatus.FAILED,
                ReleaseStatus.ROLLED_BACK,
                ReleaseStatus.CANCELLED,
            )
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
        qual_fail = sum(
            1 for o in qual if o.outcome_type in (ReleaseOutcomeType.FAILURE, ReleaseOutcomeType.ROLLBACK)
        )
        cfr = round(qual_fail / len(qual), 4) if qual else None

        return {
            "organization_id": str(organization_id),
            "total_repositories": len(repo_ids),
            "total_releases": len(releases),
            "active_releases": active,
            "successful_releases": succeeded,
            "failed_releases": failed,
            "blocked_releases": blocked,
            "active_rollouts": len(rollouts),
            "pending_approvals": pending_apps,
            "change_failure_rate": cfr,
        }


class CrossRepositoryDependencyService:
    """Directed repository dependency graph, cycle detection, ordering, and impact propagation."""

    @staticmethod
    async def add_dependency(
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        source_repository_id: uuid.UUID,
        target_repository_id: uuid.UUID,
        dependency_type: DependencyType = DependencyType.SERVICE_DEPENDENCY,
        version_constraint: str | None = None,
        evidence_source: str = "manual",
        verified: bool = False,
    ) -> RepositoryDependency:
        if source_repository_id == target_repository_id:
            raise OrganizationError("A repository cannot depend on itself.")
        existing = (
            await session.execute(
                select(RepositoryDependency)
                .where(
                    RepositoryDependency.source_repository_id == source_repository_id,
                    RepositoryDependency.target_repository_id == target_repository_id,
                    RepositoryDependency.dependency_type == dependency_type,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        dep = RepositoryDependency(
            organization_id=organization_id,
            source_repository_id=source_repository_id,
            target_repository_id=target_repository_id,
            dependency_type=dependency_type,
            version_constraint=version_constraint,
            evidence_source=evidence_source,
            verification_status=(
                DependencyVerificationStatus.VERIFIED
                if verified
                else DependencyVerificationStatus.DISCOVERED
            ),
            last_verified_at=utcnow() if verified else None,
        )
        session.add(dep)
        await session.flush()
        return dep

    @staticmethod
    async def build_graph_and_order(
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        repository_ids: list[uuid.UUID] | None = None,
    ) -> dict[str, Any]:
        repo_links = list(
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
        nodes = repository_ids if repository_ids is not None else [r.repository_id for r in repo_links]
        node_set = set(nodes)
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
        # If source depends on target, target MUST release BEFORE source: edge target -> source
        adj: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        indegree: dict[uuid.UUID, int] = {n: 0 for n in node_set}
        for e in edges:
            if e.source_repository_id in node_set and e.target_repository_id in node_set:
                adj[e.target_repository_id].append(e.source_repository_id)
                indegree[e.source_repository_id] += 1

        q = deque([n for n, deg in indegree.items() if deg == 0])
        order: list[str] = []
        while q:
            cur = q.popleft()
            order.append(str(cur))
            for nxt in adj[cur]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    q.append(nxt)

        has_cycles = len(order) < len(node_set)
        return {
            "nodes": [str(n) for n in nodes],
            "edges": [
                {
                    "id": str(e.id),
                    "source_repository_id": str(e.source_repository_id),
                    "target_repository_id": str(e.target_repository_id),
                    "dependency_type": e.dependency_type.value,
                    "version_constraint": e.version_constraint,
                    "verification_status": e.verification_status.value,
                }
                for e in edges
            ],
            "recommended_order": order,
            "has_cycles": has_cycles,
        }

    @staticmethod
    async def assess_release_impact(
        session: AsyncSession, *, release: Release
    ) -> CrossRepositoryImpactAssessment:
        if release.repository_id is None:
            raise OrganizationError("Release has no associated repository.")

        # Find all repositories that depend on release.repository_id (BFS downstream)
        edges = list(
            (
                await session.execute(
                    select(RepositoryDependency).where(
                        RepositoryDependency.target_repository_id == release.repository_id
                    )
                )
            )
            .scalars()
            .all()
        )
        affected: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        for e in edges:
            dep_repo = await session.get(Repository, e.source_repository_id)
            affected.append(
                {
                    "repository_id": str(e.source_repository_id),
                    "repository_name": dep_repo.full_name if dep_repo else str(e.source_repository_id),
                    "dependency_type": e.dependency_type.value,
                    "verification_status": e.verification_status.value,
                }
            )
            evidence.append(
                {
                    "dependency_id": str(e.id),
                    "evidence_source": e.evidence_source,
                    "version_constraint": e.version_constraint,
                }
            )
            findings.append(
                f"Downstream consumer `{dep_repo.full_name if dep_repo else e.source_repository_id}` "
                f"depends on this repository ({e.dependency_type.value}); coordinate integration validation."
            )

        assessment = CrossRepositoryImpactAssessment(
            source_release_id=release.id,
            source_repository_id=release.repository_id,
            affected_repositories=affected,
            dependency_evidence=evidence,
            risk_findings=findings,
            confidence=0.85 if any(e.verification_status == DependencyVerificationStatus.VERIFIED for e in edges) else 0.6,
        )
        session.add(assessment)
        await session.flush()
        return assessment


class ReleaseCoordinationEngine:
    """Orchestrates multi-repository CoordinatedReleasePlans over existing Releases."""

    @staticmethod
    async def create_plan(
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        name: str,
        release_ids: list[uuid.UUID],
        created_by_id: uuid.UUID | None = None,
    ) -> CoordinatedReleasePlan:
        releases: list[Release] = []
        for rid in release_ids:
            rel = await session.get(Release, rid)
            if rel is None:
                raise OrganizationError(f"Release {rid} not found.", status_code=404)
            if rel.repository_id is None:
                raise OrganizationError(f"Release {rel.release_key} has no repository.")
            releases.append(rel)

        repo_ids = [r.repository_id for r in releases if r.repository_id is not None]
        graph = await CrossRepositoryDependencyService.build_graph_and_order(
            session, organization_id=organization_id, repository_ids=repo_ids
        )
        order_idx = {rid_str: idx + 1 for idx, rid_str in enumerate(graph["recommended_order"])}

        plan = CoordinatedReleasePlan(
            organization_id=organization_id,
            name=name,
            status=(
                CoordinatedPlanStatus.BLOCKED if graph["has_cycles"] else CoordinatedPlanStatus.READY
            ),
            recommended_order=graph["recommended_order"],
            blocking_dependencies=(
                [{"reason": "Dependency cycle detected in repository graph"}]
                if graph["has_cycles"]
                else []
            ),
            risk_summary={
                "total_releases": len(releases),
                "has_cycles": graph["has_cycles"],
                "dependency_edges": len(graph["edges"]),
            },
            created_by_id=created_by_id,
        )
        session.add(plan)
        await session.flush()

        for rel in releases:
            assert rel.repository_id is not None
            session.add(
                CoordinatedReleaseItem(
                    coordinated_plan_id=plan.id,
                    release_id=rel.id,
                    repository_id=rel.repository_id,
                    execution_order=order_idx.get(str(rel.repository_id), len(releases)),
                    dependency_status="blocked" if graph["has_cycles"] else "ready",
                    coordination_status="pending",
                )
            )
        await session.flush()
        return plan

    @staticmethod
    async def approve_plan(
        session: AsyncSession, *, plan: CoordinatedReleasePlan, approver_id: uuid.UUID
    ) -> CoordinatedReleasePlan:
        if plan.status == CoordinatedPlanStatus.BLOCKED:
            raise OrganizationError("Cannot approve a blocked coordinated release plan.")
        plan.status = CoordinatedPlanStatus.APPROVED
        plan.approved_by_id = approver_id
        await session.flush()
        return plan

    @staticmethod
    async def start_plan(
        session: AsyncSession, *, plan: CoordinatedReleasePlan
    ) -> CoordinatedReleasePlan:
        if plan.status != CoordinatedPlanStatus.APPROVED or plan.approved_by_id is None:
            raise OrganizationError(
                "Coordinated release plan requires explicit approval before execution.",
                status_code=403,
            )
        plan.status = CoordinatedPlanStatus.IN_PROGRESS
        await session.flush()
        return plan
