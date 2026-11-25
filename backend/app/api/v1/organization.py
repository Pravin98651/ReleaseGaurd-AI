"""Multi-Repository & Organization Release Intelligence REST API (Phase 7)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import (
    AdminOperator,
    ApproverOperator,
    AuthenticatedOperator,
    DBSession,
    DeveloperOperator,
)
from app.models import (
    CoordinatedReleasePlan,
    Organization,
    OrganizationReleasePolicy,
    OrganizationRepository,
    Release,
    Repository,
    RepositoryDependency,
)
from app.schemas.organization import (
    CoordinatedPlanCreateRequest,
    CoordinatedReleasePlanOut,
    CrossRepoImpactOut,
    DependencyCreateRequest,
    OrganizationCreateRequest,
    OrganizationOut,
    OrganizationPolicyCreateRequest,
    OrganizationReleasePolicyOut,
    OrgQueryRequest,
    OrgRepoRegisterRequest,
    RepositoryDependencyOut,
    RepositoryHealthSnapshotOut,
)
from app.services.organization import (
    CrossRepositoryDependencyService,
    OrganizationError,
    OrganizationManagementService,
    OrganizationReleasePolicyService,
    OrganizationRiskIntelligenceService,
    ReleaseCoordinationEngine,
    RepositoryHealthScoringService,
)

org_router = APIRouter(tags=["organization-intelligence"])


@org_router.get("/organizations", response_model=list[OrganizationOut])
async def list_organizations(
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[OrganizationOut]:
    rows = list(
        (await session.execute(select(Organization).order_by(Organization.created_at.desc())))
        .scalars()
        .all()
    )
    return [OrganizationOut.model_validate(r) for r in rows]


@org_router.post("/organizations", response_model=OrganizationOut, status_code=201)
async def create_organization(
    body: OrganizationCreateRequest,
    session: DBSession,
    _user: AdminOperator,
) -> OrganizationOut:
    try:
        org = await OrganizationManagementService.create_organization(
            session, name=body.name, slug=body.slug, github_org_id=body.github_org_id
        )
        await session.commit()
        return OrganizationOut.model_validate(org)
    except OrganizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@org_router.get("/organizations/{organization_id}", response_model=OrganizationOut)
async def get_organization(
    organization_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> OrganizationOut:
    org = await session.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return OrganizationOut.model_validate(org)


@org_router.get("/organizations/{organization_id}/repositories")
async def list_organization_repositories(
    organization_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[dict[str, Any]]:
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
    items: list[dict[str, Any]] = []
    for lnk in links:
        repo = await session.get(Repository, lnk.repository_id)
        if repo:
            items.append(
                {
                    "id": str(lnk.id),
                    "repository_id": str(repo.id),
                    "full_name": repo.full_name,
                    "default_branch": repo.default_branch,
                    "status": lnk.status,
                    "monitoring_enabled": lnk.monitoring_enabled,
                }
            )
    return items


@org_router.post("/organizations/{organization_id}/repositories")
async def register_organization_repository(
    organization_id: uuid.UUID,
    body: OrgRepoRegisterRequest,
    session: DBSession,
    _user: AdminOperator,
) -> dict[str, Any]:
    try:
        lnk = await OrganizationManagementService.register_repository(
            session, organization_id=organization_id, repository_id=body.repository_id
        )
        await session.commit()
        return {"id": str(lnk.id), "repository_id": str(lnk.repository_id), "status": lnk.status}
    except OrganizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@org_router.get("/organizations/{organization_id}/release-overview")
async def get_organization_release_overview(
    organization_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    return await OrganizationManagementService.get_release_overview(
        session, organization_id=organization_id
    )


@org_router.get("/organizations/{organization_id}/dependency-graph")
async def get_organization_dependency_graph(
    organization_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    return await CrossRepositoryDependencyService.build_graph_and_order(
        session, organization_id=organization_id
    )


@org_router.post("/organizations/{organization_id}/dependencies", response_model=RepositoryDependencyOut)
async def create_organization_dependency(
    organization_id: uuid.UUID,
    body: DependencyCreateRequest,
    session: DBSession,
    _user: DeveloperOperator,
) -> RepositoryDependencyOut:
    try:
        dep = await CrossRepositoryDependencyService.add_dependency(
            session,
            organization_id=organization_id,
            source_repository_id=body.source_repository_id,
            target_repository_id=body.target_repository_id,
            dependency_type=body.dependency_type,
            version_constraint=body.version_constraint,
            evidence_source=body.evidence_source,
            verified=body.verified,
        )
        await session.commit()
        return RepositoryDependencyOut.model_validate(dep)
    except OrganizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@org_router.get("/repositories/{repository_id}/dependents", response_model=list[RepositoryDependencyOut])
async def list_repository_dependents(
    repository_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[RepositoryDependencyOut]:
    rows = list(
        (
            await session.execute(
                select(RepositoryDependency).where(
                    RepositoryDependency.target_repository_id == repository_id
                )
            )
        )
        .scalars()
        .all()
    )
    return [RepositoryDependencyOut.model_validate(r) for r in rows]


@org_router.post("/releases/{release_id}/cross-repository-impact", response_model=CrossRepoImpactOut)
async def assess_cross_repository_impact(
    release_id: uuid.UUID,
    session: DBSession,
    _user: DeveloperOperator,
) -> CrossRepoImpactOut:
    rel = await session.get(Release, release_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Release not found.")
    try:
        impact = await CrossRepositoryDependencyService.assess_release_impact(session, release=rel)
        await session.commit()
        return CrossRepoImpactOut.model_validate(impact)
    except OrganizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@org_router.post("/organizations/{organization_id}/coordinated-releases", response_model=CoordinatedReleasePlanOut)
async def create_coordinated_release_plan(
    organization_id: uuid.UUID,
    body: CoordinatedPlanCreateRequest,
    session: DBSession,
    user: DeveloperOperator,
) -> CoordinatedReleasePlanOut:
    try:
        plan = await ReleaseCoordinationEngine.create_plan(
            session,
            organization_id=organization_id,
            name=body.name,
            release_ids=body.release_ids,
            created_by_id=user.id,
        )
        await session.commit()
        return CoordinatedReleasePlanOut.model_validate(plan)
    except OrganizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@org_router.get("/organizations/{organization_id}/coordinated-releases", response_model=list[CoordinatedReleasePlanOut])
async def list_coordinated_release_plans(
    organization_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[CoordinatedReleasePlanOut]:
    rows = list(
        (
            await session.execute(
                select(CoordinatedReleasePlan)
                .where(CoordinatedReleasePlan.organization_id == organization_id)
                .order_by(CoordinatedReleasePlan.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [CoordinatedReleasePlanOut.model_validate(r) for r in rows]


@org_router.post("/coordinated-releases/{plan_id}/approve", response_model=CoordinatedReleasePlanOut)
async def approve_coordinated_release_plan(
    plan_id: uuid.UUID,
    session: DBSession,
    user: ApproverOperator,
) -> CoordinatedReleasePlanOut:
    plan = await session.get(CoordinatedReleasePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Coordinated release plan not found.")
    try:
        updated = await ReleaseCoordinationEngine.approve_plan(
            session, plan=plan, approver_id=user.id
        )
        await session.commit()
        return CoordinatedReleasePlanOut.model_validate(updated)
    except OrganizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@org_router.post("/coordinated-releases/{plan_id}/start", response_model=CoordinatedReleasePlanOut)
async def start_coordinated_release_plan(
    plan_id: uuid.UUID,
    session: DBSession,
    _user: DeveloperOperator,
) -> CoordinatedReleasePlanOut:
    plan = await session.get(CoordinatedReleasePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Coordinated release plan not found.")
    try:
        updated = await ReleaseCoordinationEngine.start_plan(session, plan=plan)
        await session.commit()
        return CoordinatedReleasePlanOut.model_validate(updated)
    except OrganizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@org_router.get("/organizations/{organization_id}/repository-rankings", response_model=list[RepositoryHealthSnapshotOut])
async def get_organization_repository_rankings(
    organization_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[RepositoryHealthSnapshotOut]:
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
    snaps: list[RepositoryHealthSnapshotOut] = []
    for lnk in links:
        snap = await RepositoryHealthScoringService.compute_health(
            session, repository_id=lnk.repository_id
        )
        snaps.append(RepositoryHealthSnapshotOut.model_validate(snap))
    await session.commit()
    snaps.sort(key=lambda s: (s.health_score is not None, s.health_score or -1.0), reverse=True)
    return snaps


@org_router.get("/repositories/{repository_id}/health", response_model=RepositoryHealthSnapshotOut)
async def get_repository_health(
    repository_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> RepositoryHealthSnapshotOut:
    try:
        snap = await RepositoryHealthScoringService.compute_health(
            session, repository_id=repository_id
        )
        await session.commit()
        return RepositoryHealthSnapshotOut.model_validate(snap)
    except OrganizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@org_router.get("/organizations/{organization_id}/policies", response_model=list[OrganizationReleasePolicyOut])
async def list_organization_policies(
    organization_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[OrganizationReleasePolicyOut]:
    rows = list(
        (
            await session.execute(
                select(OrganizationReleasePolicy)
                .where(OrganizationReleasePolicy.organization_id == organization_id)
                .order_by(OrganizationReleasePolicy.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [OrganizationReleasePolicyOut.model_validate(r) for r in rows]


@org_router.post("/organizations/{organization_id}/policies", response_model=OrganizationReleasePolicyOut)
async def create_organization_policy(
    organization_id: uuid.UUID,
    body: OrganizationPolicyCreateRequest,
    session: DBSession,
    user: AdminOperator,
) -> OrganizationReleasePolicyOut:
    pol = await OrganizationReleasePolicyService.create_policy(
        session,
        organization_id=organization_id,
        name=body.name,
        policy_type=body.policy_type,
        configuration=body.configuration,
        is_mandatory=body.is_mandatory,
        created_by_id=user.id,
    )
    await session.commit()
    return OrganizationReleasePolicyOut.model_validate(pol)


@org_router.post("/organizations/{organization_id}/policies/{policy_id}/publish", response_model=OrganizationReleasePolicyOut)
async def publish_organization_policy(
    organization_id: uuid.UUID,
    policy_id: uuid.UUID,
    session: DBSession,
    user: ApproverOperator,
) -> OrganizationReleasePolicyOut:
    pol = await session.get(OrganizationReleasePolicy, policy_id)
    if pol is None or pol.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Organization policy not found.")
    updated = await OrganizationReleasePolicyService.publish_policy(
        session, policy=pol, approver_id=user.id
    )
    await session.commit()
    return OrganizationReleasePolicyOut.model_validate(updated)


@org_router.get("/organizations/{organization_id}/intelligence")
async def get_organization_intelligence(
    organization_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    snap = await OrganizationRiskIntelligenceService.compute_risk_snapshot(
        session, organization_id=organization_id
    )
    await session.commit()
    return {
        "organization_id": str(snap.organization_id),
        "repository_count": snap.repository_count,
        "release_count": snap.release_count,
        "failure_count": snap.failure_count,
        "rollback_count": snap.rollback_count,
        "risk_summary": snap.risk_summary,
        "calculated_at": snap.calculated_at.isoformat(),
    }


@org_router.post("/organizations/{organization_id}/intelligence/query")
async def query_organization_intelligence(
    organization_id: uuid.UUID,
    body: OrgQueryRequest,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    res = await OrganizationRiskIntelligenceService.query_organization(
        session, organization_id=organization_id, question=body.question
    )
    await session.commit()
    return res
