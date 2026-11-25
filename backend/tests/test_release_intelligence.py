"""Comprehensive tests for the Release Intelligence & Learning Engine (Phase 5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    AgentDecision,
    AgentDecisionSource,
    AgentDecisionStatus,
    AgentExecution,
    AgentExecutionStatus,
    FeatureFlag,
    FlagType,
    PolicyReviewStatus,
    ProgressiveRollout,
    ProgressiveRolloutStatus,
    PullRequest,
    PullRequestState,
    Release,
    ReleaseStatus,
    ReleaseType,
    Repository,
    RiskLevel,
    RollbackOperation,
    RollbackOperationStatus,
    User,
    UserRole,
)
from app.services.intelligence import (
    FailurePatternAnalyzer,
    HistoricalReleaseAnalyticsService,
    PolicyIntelligenceError,
    RecommendationEvaluationService,
    ReleaseKnowledgeBaseService,
    ReleaseLearningEngine,
    ReleasePolicyIntelligenceService,
    RepositoryIntelligenceService,
    RollbackIntelligenceService,
    wilson_interval,
)


@pytest.mark.asyncio
async def test_wilson_interval_and_empty_analytics(db_session) -> None:
    empty_ci = wilson_interval(0, 0)
    assert empty_ci["lower"] is None and empty_ci["sufficient"] is False

    small_ci = wilson_interval(1, 2)
    assert small_ci["sufficient"] is False
    assert 0.0 <= small_ci["lower"] <= small_ci["upper"] <= 1.0

    perf = await HistoricalReleaseAnalyticsService.compute_performance(db_session, days=30)
    assert perf["qualifying_releases"] == 0
    assert perf["change_failure_rate"] is None
    assert perf["data_sufficiency"] == "insufficient_sample"


@pytest.mark.asyncio
async def test_learning_engine_e2e_patterns_recommendations_and_policy_governance(
    db_session,
) -> None:
    now = datetime.now(UTC)
    user = User(
        email="intel-admin@releaseguard.test",
        display_name="Intel Admin",
        role=UserRole.ADMIN,
        password_hash="not-used",
    )
    repo = Repository(
        github_repo_id=8801,
        owner="novashop",
        name="checkout-service",
        full_name="novashop/checkout-service",
    )
    db_session.add_all([user, repo])
    await db_session.flush()

    flag = FeatureFlag(
        key="checkout_intel_flag",
        flagsmith_project_id=56055,
        repository_id=repo.id,
        description="Checkout intelligence flag",
        flag_type=FlagType.MULTIVARIATE,
    )
    db_session.add(flag)
    await db_session.flush()

    pr_ok = PullRequest(
        repository_id=repo.id,
        github_pr_number=201,
        title="Safe checkout copy update",
        author_login="dev",
        head_branch="feat/copy",
        base_branch="main",
        head_sha="sha_ok",
        state=PullRequestState.MERGED,
        html_url="https://github.com/novashop/checkout-service/pull/201",
        risk_level=RiskLevel.LOW,
        confirmed_risks=[{"file_path": "checkout/copy.py"}],
    )
    pr_fail_1 = PullRequest(
        repository_id=repo.id,
        github_pr_number=202,
        title="Payment gateway v2 retry",
        author_login="dev",
        head_branch="feat/pay-1",
        base_branch="main",
        head_sha="sha_f1",
        state=PullRequestState.OPEN,
        html_url="https://github.com/novashop/checkout-service/pull/202",
        risk_level=RiskLevel.HIGH,
        confirmed_risks=[{"file_path": "checkout/payment.py"}, {"file_path": "requirements.txt"}],
    )
    pr_fail_2 = PullRequest(
        repository_id=repo.id,
        github_pr_number=203,
        title="Payment card validation v2",
        author_login="dev",
        head_branch="feat/pay-2",
        base_branch="main",
        head_sha="sha_f2",
        state=PullRequestState.OPEN,
        html_url="https://github.com/novashop/checkout-service/pull/203",
        risk_level=RiskLevel.HIGH,
        confirmed_risks=[{"file_path": "checkout/payment.py"}],
    )
    db_session.add_all([pr_ok, pr_fail_1, pr_fail_2])
    await db_session.flush()

    # 1 succeeded, 2 rolled back/failed (payment failure), 1 pre-deploy cancelled (excluded from CFR)
    rel_ok = Release(
        release_key="REL-INT-1",
        title="Safe Checkout Release",
        release_type=ReleaseType.FEATURE,
        target_environment="production",
        status=ReleaseStatus.COMPLETED,
        repository_id=repo.id,
        pull_request_id=pr_ok.id,
        feature_flag_id=flag.id,
        started_at=now - timedelta(minutes=30),
        completed_at=now - timedelta(minutes=5),
    )
    rel_fail1 = Release(
        release_key="REL-INT-2",
        title="Payment Gateway Release 1",
        release_type=ReleaseType.FEATURE,
        target_environment="production",
        status=ReleaseStatus.ROLLED_BACK,
        repository_id=repo.id,
        pull_request_id=pr_fail_1.id,
        feature_flag_id=flag.id,
        started_at=now - timedelta(hours=2),
        completed_at=now - timedelta(hours=1, minutes=40),
    )
    rel_fail2 = Release(
        release_key="REL-INT-3",
        title="Payment Gateway Release 2",
        release_type=ReleaseType.FEATURE,
        target_environment="production",
        status=ReleaseStatus.FAILED,
        repository_id=repo.id,
        pull_request_id=pr_fail_2.id,
        feature_flag_id=flag.id,
        started_at=now - timedelta(hours=3),
        completed_at=now - timedelta(hours=2, minutes=45),
    )
    rel_cancelled = Release(
        release_key="REL-INT-4",
        title="Cancelled Pre-Deploy Release",
        release_type=ReleaseType.FEATURE,
        target_environment="staging",
        status=ReleaseStatus.CANCELLED,
        repository_id=repo.id,
    )
    db_session.add_all([rel_ok, rel_fail1, rel_fail2, rel_cancelled])
    await db_session.flush()

    # Rollout + RollbackOperation on rel_fail1
    rollout = ProgressiveRollout(
        rollout_key="PRL-INT-1",
        name="Prod Payment Rollout",
        release_id=rel_fail1.id,
        repository_id=repo.id,
        feature_flag_id=flag.id,
        feature_flag_key=flag.key,
        environment="production",
        status=ProgressiveRolloutStatus.ROLLED_BACK,
        current_percentage=0,
        target_percentage=100,
    )
    db_session.add(rollout)
    await db_session.flush()

    rb = RollbackOperation(
        rollout_id=rollout.id,
        feature_flag_id=flag.id,
        environment="production",
        previous_configuration={"rollout_percentage": 5},
        requested_configuration={"rollout_percentage": 0},
        status=RollbackOperationStatus.COMPLETED,
        reason="payment_failed spike at 5%",
        started_at=now - timedelta(hours=1, minutes=50),
        completed_at=now - timedelta(hours=1, minutes=48),
    )
    db_session.add(rb)

    # AgentExecution + Decisions (one accepted on success -> VERIFIED_BENEFIT, one accepted on failure -> NEGATIVE_OUTCOME)
    exec_ok = AgentExecution(
        execution_key="EXE-INT-1",
        correlation_id="corr-int-1",
        agent_type="release_risk",
        agent_name="Risk Agent",
        task_name="Evaluate Safe Release",
        status=AgentExecutionStatus.COMPLETED,
        repository_id=repo.id,
        release_id=rel_ok.id,
    )
    exec_fail = AgentExecution(
        execution_key="EXE-INT-2",
        correlation_id="corr-int-2",
        agent_type="progressive_delivery",
        agent_name="Rollout Agent",
        task_name="Evaluate Payment Release",
        status=AgentExecutionStatus.COMPLETED,
        repository_id=repo.id,
        release_id=rel_fail1.id,
    )
    db_session.add_all([exec_ok, exec_fail])
    await db_session.flush()

    dec_ok = AgentDecision(
        execution_id=exec_ok.id,
        decision_type="add_tests",
        decision_source=AgentDecisionSource.AI_RECOMMENDATION,
        decision_summary="Add checkout copy regression test",
        decision_status=AgentDecisionStatus.ACCEPTED,
        model_name="gemini-2.5-flash",
    )
    dec_fail = AgentDecision(
        execution_id=exec_fail.id,
        decision_type="modify_flag_rollout",
        decision_source=AgentDecisionSource.AI_RECOMMENDATION,
        decision_summary="Proceed to 5% canary",
        decision_status=AgentDecisionStatus.ACCEPTED,
        model_name="gemini-2.5-flash",
    )
    db_session.add_all([dec_ok, dec_fail])
    await db_session.flush()

    # Run full learning engine
    run = await ReleaseLearningEngine.run_analysis(db_session, analysis_type="full_learning")
    assert run.input_record_count == 4

    # Qualifying releases = 3 (pre-deploy cancelled excluded!) -> 2 failures / 3 = 0.6667
    perf = await HistoricalReleaseAnalyticsService.compute_performance(db_session, days=30)
    assert perf["qualifying_releases"] == 3
    assert perf["failed_releases"] == 2
    assert perf["change_failure_rate"] == pytest.approx(0.6667, abs=0.001)

    # Patterns detected including payment_failure & module checkout/
    patterns = await FailurePatternAnalyzer.analyze_patterns(db_session)
    assert any(p.category.value == "payment_failure" for p in patterns)
    assert any(p.affected_module == "checkout" and not p.is_exploratory for p in patterns)

    # Repository profile updated
    prof = await RepositoryIntelligenceService.refresh_profile(db_session, repo)
    assert prof.release_count == 3
    assert prof.failure_count == 2
    assert prof.rollback_count == 1

    # Rollback analysis
    rb_summary = await RollbackIntelligenceService.analyze_rollbacks(db_session)
    assert rb_summary["total_rollbacks"] == 1
    assert rb_summary["top_causes"][0]["category"] == "payment_failure"

    # Recommendation accuracy distinguishes acceptance (100%) from verified positive outcome (50%)
    acc = await RecommendationEvaluationService.compute_accuracy_metrics(db_session)
    assert acc["acceptance_rate"] == 1.0
    assert acc["positive_outcome_rate"] == 0.5

    # Policy recommendation governance: cannot implement without approval
    from sqlalchemy import select

    from app.models import PolicyRecommendation

    pols = list((await db_session.execute(select(PolicyRecommendation))).scalars().all())
    assert len(pols) >= 1
    pol = pols[0]
    with pytest.raises(PolicyIntelligenceError):
        await ReleasePolicyIntelligenceService.implement_recommendation(
            db_session, pol, actor_id=user.id
        )

    await ReleasePolicyIntelligenceService.approve_recommendation(
        db_session, pol, approver_id=user.id, notes="Approved by Release Lead"
    )
    await ReleasePolicyIntelligenceService.implement_recommendation(
        db_session, pol, actor_id=user.id
    )
    await ReleasePolicyIntelligenceService.verify_recommendation(
        db_session, pol, actor_id=user.id
    )
    assert pol.review_status == PolicyReviewStatus.VERIFIED

    # Knowledge base query returns grounded numbers without fabrication
    kb = await ReleaseKnowledgeBaseService.query(
        db_session, question="What caused our recent rollbacks?"
    )
    assert "1 rollback" in kb["direct_answer"]
    assert kb["supporting_evidence"]["qualifying_releases"] == 3


@pytest.mark.asyncio
async def test_intelligence_rest_api(async_engine, session_factory) -> None:
    async with session_factory() as s:
        await bootstrap_workspace_defaults(s, bootstrap_env_tokens=False)
        await s.commit()

    app = create_app(engine=async_engine, session_factory=session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ov = await client.get("/api/v1/intelligence/overview")
        assert ov.status_code == 200
        assert "performance" in ov.json()

        run_res = await client.post("/api/v1/intelligence/analyze", json={})
        assert run_res.status_code == 200
        assert run_res.json()["analysis_version"] == "1.0.0"

        q_res = await client.post(
            "/api/v1/intelligence/query",
            json={"question": "What is our recent release performance?"},
        )
        assert q_res.status_code == 200
        assert "direct_answer" in q_res.json()
