"""Comprehensive tests for Phase 7 Multi-Repository Release Intelligence."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    CoordinatedPlanStatus,
    DependencyType,
    Release,
    ReleaseOutcome,
    ReleaseOutcomeType,
    ReleaseStatus,
    ReleaseType,
    RepoHealthCategory,
    Repository,
    User,
    UserRole,
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


@pytest.mark.asyncio
async def test_novashop_multi_repo_coordination_health_and_policies(db_session) -> None:
    user = User(
        email="org-admin@releaseguard.test",
        display_name="Org Admin",
        role=UserRole.ADMIN,
        password_hash="not-used",
    )
    db_session.add(user)
    await db_session.flush()

    org = await OrganizationManagementService.create_organization(
        db_session, name="NovaShop", slug="novashop"
    )

    repos = {}
    for idx, name in enumerate(["novashop-shared", "novashop-auth", "novashop-checkout", "novashop-frontend"]):
        r = Repository(
            github_repo_id=5000 + idx,
            owner="novashop",
            name=name,
            full_name=f"novashop/{name}",
        )
        db_session.add(r)
        await db_session.flush()
        await OrganizationManagementService.register_repository(
            db_session, organization_id=org.id, repository_id=r.id
        )
        repos[name] = r

    # Dependency chain: frontend -> checkout -> auth -> shared
    await CrossRepositoryDependencyService.add_dependency(
        db_session,
        organization_id=org.id,
        source_repository_id=repos["novashop-auth"].id,
        target_repository_id=repos["novashop-shared"].id,
        dependency_type=DependencyType.LIBRARY_DEPENDENCY,
        verified=True,
    )
    await CrossRepositoryDependencyService.add_dependency(
        db_session,
        organization_id=org.id,
        source_repository_id=repos["novashop-checkout"].id,
        target_repository_id=repos["novashop-auth"].id,
        dependency_type=DependencyType.SERVICE_DEPENDENCY,
        verified=True,
    )
    await CrossRepositoryDependencyService.add_dependency(
        db_session,
        organization_id=org.id,
        source_repository_id=repos["novashop-frontend"].id,
        target_repository_id=repos["novashop-checkout"].id,
        dependency_type=DependencyType.API_DEPENDENCY,
        verified=True,
    )

    graph = await CrossRepositoryDependencyService.build_graph_and_order(
        db_session, organization_id=org.id
    )
    assert graph["has_cycles"] is False
    order = graph["recommended_order"]
    assert order.index(str(repos["novashop-shared"].id)) < order.index(str(repos["novashop-auth"].id))
    assert order.index(str(repos["novashop-auth"].id)) < order.index(str(repos["novashop-checkout"].id))
    assert order.index(str(repos["novashop-checkout"].id)) < order.index(str(repos["novashop-frontend"].id))

    # Create releases & assess cross-repo impact from novashop-shared
    rel_shared = Release(
        release_key="REL-ORG-1",
        title="Shared SDK v2",
        release_type=ReleaseType.FEATURE,
        target_environment="staging",
        status=ReleaseStatus.DRAFT,
        repository_id=repos["novashop-shared"].id,
    )
    rel_auth = Release(
        release_key="REL-ORG-2",
        title="Auth Service v2",
        release_type=ReleaseType.FEATURE,
        target_environment="staging",
        status=ReleaseStatus.DRAFT,
        repository_id=repos["novashop-auth"].id,
    )
    db_session.add_all([rel_shared, rel_auth])
    await db_session.flush()

    impact = await CrossRepositoryDependencyService.assess_release_impact(
        db_session, release=rel_shared
    )
    assert len(impact.affected_repositories) == 1
    assert impact.affected_repositories[0]["repository_name"] == "novashop/novashop-auth"

    # Coordinated release plan requires approval before start
    plan = await ReleaseCoordinationEngine.create_plan(
        db_session,
        organization_id=org.id,
        name="NovaShop Q4 Coordinated Release",
        release_ids=[rel_shared.id, rel_auth.id],
        created_by_id=user.id,
    )
    assert plan.status == CoordinatedPlanStatus.READY
    with pytest.raises(OrganizationError):
        await ReleaseCoordinationEngine.start_plan(db_session, plan=plan)

    await ReleaseCoordinationEngine.approve_plan(db_session, plan=plan, approver_id=user.id)
    await ReleaseCoordinationEngine.start_plan(db_session, plan=plan)
    assert plan.status == CoordinatedPlanStatus.IN_PROGRESS

    # Health scoring: < 2 outcomes -> INSUFFICIENT_DATA; >= 2 -> HEALTHY
    h_empty = await RepositoryHealthScoringService.compute_health(
        db_session, repository_id=repos["novashop-frontend"].id
    )
    assert h_empty.health_category == RepoHealthCategory.INSUFFICIENT_DATA
    assert h_empty.health_score is None

    for i in range(2):
        r_done = Release(
            release_key=f"REL-H-{i}",
            title="Healthy release",
            release_type=ReleaseType.FEATURE,
            target_environment="production",
            status=ReleaseStatus.COMPLETED,
            repository_id=repos["novashop-shared"].id,
        )
        db_session.add(r_done)
        await db_session.flush()
        db_session.add(
            ReleaseOutcome(
                release_id=r_done.id,
                repository_id=repos["novashop-shared"].id,
                outcome_type=ReleaseOutcomeType.SUCCESS,
                final_status="completed",
                environment="production",
                verified=True,
            )
        )
    await db_session.flush()
    h_ok = await RepositoryHealthScoringService.compute_health(
        db_session, repository_id=repos["novashop-shared"].id
    )
    assert h_ok.health_category == RepoHealthCategory.HEALTHY
    assert h_ok.health_score == 100.0

    # Shared mandatory policy cannot be weakened without approved exception
    pol = await OrganizationReleasePolicyService.create_policy(
        db_session,
        organization_id=org.id,
        name="Mandatory Canary Policy",
        policy_type="rollout_guardrails",
        configuration={"min_observation_seconds": 900},
        is_mandatory=True,
        created_by_id=user.id,
    )
    await OrganizationReleasePolicyService.publish_policy(
        db_session, policy=pol, approver_id=user.id
    )
    with pytest.raises(OrganizationError):
        await OrganizationReleasePolicyService.assign_or_override(
            db_session,
            repository_id=repos["novashop-checkout"].id,
            policy=pol,
            override_configuration={"min_observation_seconds": 60},
            exception_approved_by_id=None,
        )

    # Org risk & assistant query
    qa = await OrganizationRiskIntelligenceService.query_organization(
        db_session, organization_id=org.id, question="How is our org doing?"
    )
    assert "4 repositories" in qa["direct_answer"]


@pytest.mark.asyncio
async def test_organization_rest_api(async_engine, session_factory) -> None:
    async with session_factory() as s:
        await bootstrap_workspace_defaults(s, bootstrap_env_tokens=False)
        await s.commit()

    app = create_app(engine=async_engine, session_factory=session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_res = await client.post(
            "/api/v1/organizations",
            json={"name": "Acme Corp", "slug": "acme-corp"},
        )
        assert create_res.status_code == 201
        org_id = create_res.json()["id"]
        assert (await client.get(f"/api/v1/organizations/{org_id}/release-overview")).status_code == 200
        assert (await client.get(f"/api/v1/organizations/{org_id}/dependency-graph")).status_code == 200
        assert (await client.get(f"/api/v1/organizations/{org_id}/repository-rankings")).status_code == 200
