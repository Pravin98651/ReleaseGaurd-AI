"""Comprehensive tests for the Autonomous Release Lifecycle Engine."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    ApprovalStatus,
    CodeChange,
    CodeChangeStatus,
    CodeChangeType,
    Experiment,
    ExperimentStatus,
    FeatureFlag,
    FlagType,
    PullRequest,
    PullRequestState,
    ReleaseReadinessStatus,
    ReleaseRecommendation,
    ReleaseStage,
    ReleaseStageKey,
    ReleaseStageStatus,
    ReleaseStatus,
    ReleaseType,
    Repository,
    RiskLevel,
    User,
    UserRole,
    ValidationStatus,
)
from app.services.release_orchestrator import ReleaseOrchestrator
from app.services.release_readiness import ReleaseReadinessService
from app.services.release_state_machine import (
    ReleaseTransitionError,
    can_transition,
    transition_release,
)


@pytest.mark.asyncio
async def test_state_machine_matrix_and_terminal_immutability(db_session) -> None:
    assert can_transition(ReleaseStatus.DRAFT, ReleaseStatus.ANALYZING)
    assert can_transition(ReleaseStatus.AWAITING_APPROVAL, ReleaseStatus.APPROVED)
    assert not can_transition(ReleaseStatus.COMPLETED, ReleaseStatus.DRAFT)
    assert not can_transition(ReleaseStatus.CANCELLED, ReleaseStatus.PAUSED)
    assert not can_transition(ReleaseStatus.ROLLED_BACK, ReleaseStatus.ROLLING_OUT)

    rel = await ReleaseOrchestrator.create_release(
        db_session,
        title="State Machine Test",
        release_type=ReleaseType.CONFIG,
        target_environment="development",
    )
    assert rel.status == ReleaseStatus.DRAFT
    assert rel.version == 1

    await transition_release(db_session, rel, ReleaseStatus.PAUSED)
    assert rel.status == ReleaseStatus.PAUSED
    assert rel.previous_status == ReleaseStatus.DRAFT

    await ReleaseOrchestrator.resume_release(db_session, rel)
    assert rel.status == ReleaseStatus.DRAFT

    with pytest.raises(ReleaseTransitionError) as conflict_exc:
        await transition_release(
            db_session, rel, ReleaseStatus.CANCELLED, expected_version=999
        )
    assert conflict_exc.value.status_code == 409

    await ReleaseOrchestrator.cancel_release(db_session, rel, reason="Done")
    assert rel.status == ReleaseStatus.CANCELLED

    with pytest.raises(ReleaseTransitionError) as term_exc:
        await transition_release(db_session, rel, ReleaseStatus.DRAFT)
    assert term_exc.value.status_code == 422


@pytest.mark.asyncio
async def test_readiness_blocker_capping_and_stale_approval_invalidation(db_session) -> None:
    user1 = User(email="eng1@test.local", display_name="Eng 1", role=UserRole.ENGINEER)
    repo = Repository(
        github_repo_id=9001,
        owner="novashop",
        name="checkout",
        full_name="novashop/checkout",
    )
    db_session.add_all([user1, repo])
    await db_session.flush()

    pr = PullRequest(
        repository_id=repo.id,
        github_pr_number=42,
        title="Add checkout_v2",
        author_login="eng1",
        head_branch="feat/checkout-v2",
        base_branch="main",
        head_sha="abc123deadbeef",
        state=PullRequestState.OPEN,
        html_url="https://github.com/novashop/checkout/pull/42",
        risk_level=RiskLevel.HIGH,
    )
    flag = FeatureFlag(
        flagsmith_project_id=1,
        repository_id=repo.id,
        key="checkout_v2",
        flag_type=FlagType.STANDARD,
    )
    db_session.add_all([pr, flag])
    await db_session.flush()

    bad_change = CodeChange(
        repository_id=repo.id,
        pull_request_id=pr.id,
        feature_flag_id=flag.id,
        change_type=CodeChangeType.FLAG_WRAP,
        flag_key="checkout_v2",
        title="Wrap checkout_v2",
        files_diff=[{"path": "app.py", "diff": "--- a/app.py\n+++ b/app.py"}],
        validation_status=ValidationStatus.FAILED,
        status=CodeChangeStatus.VALIDATION_FAILED,
    )
    bad_exp = Experiment(
        feature_flag_id=flag.id,
        name="Checkout V2 Exp",
        hypothesis="Improves checkout conversion without increasing failures.",
        environment_name="staging",
        status=ExperimentStatus.RUNNING,
        latest_recommendation=ReleaseRecommendation.RECOMMEND_ROLLBACK,
    )
    db_session.add_all([bad_change, bad_exp])
    await db_session.flush()

    rel = await ReleaseOrchestrator.create_release(
        db_session,
        title="Checkout V2 Release",
        pull_request_id=pr.id,
        feature_flag_id=flag.id,
        experiment_id=bad_exp.id,
        target_environment="staging",
        created_by_id=user1.id,
    )
    # Critical blockers (failed patch + rollback-recommended exp) must cap readiness <= 49 and BLOCKED
    assert rel.readiness_status == ReleaseReadinessStatus.BLOCKED
    assert rel.readiness_score <= 49
    assert len(rel.readiness_summary["blocking_issues"]) >= 2

    # Fix blockers so approval can be requested
    bad_change.validation_status = ValidationStatus.PASSED
    bad_change.status = CodeChangeStatus.VALIDATED
    bad_exp.latest_recommendation = ReleaseRecommendation.EXPAND_ROLLOUT
    await ReleaseReadinessService.evaluate_release(db_session, rel)
    assert not rel.readiness_summary["blocking_issues"]

    approval = await ReleaseOrchestrator.request_release_approval(
        db_session, rel, actor_id=user1.id, rationale="Ready for staging"
    )
    assert approval.status == ApprovalStatus.PENDING
    assert approval.release_version == rel.version

    # Material update increments version and invalidates stale pending approval
    await ReleaseOrchestrator.update_release(
        db_session,
        rel,
        expected_version=rel.version,
        actor_id=user1.id,
        target_rollout_percentage=50,
    )
    await db_session.refresh(approval)
    assert approval.status == ApprovalStatus.CANCELLED


@pytest.mark.asyncio
async def test_startup_recovery_and_retry(db_session) -> None:
    rel = await ReleaseOrchestrator.create_release(
        db_session,
        title="Interrupted Release",
        release_type=ReleaseType.CONFIG,
    )
    await transition_release(db_session, rel, ReleaseStatus.ANALYZING)
    stage = (
        await db_session.execute(
            select(ReleaseStage).where(
                ReleaseStage.release_id == rel.id,
                ReleaseStage.stage_key == ReleaseStageKey.PR_ANALYSIS,
            )
        )
    ).scalar_one()
    stage.status = ReleaseStageStatus.IN_PROGRESS
    await db_session.commit()

    recovered = await ReleaseOrchestrator.recover_interrupted_releases(db_session)
    assert recovered == 1
    await db_session.refresh(rel)
    await db_session.refresh(stage)
    assert rel.status == ReleaseStatus.FAILED
    assert stage.status == ReleaseStageStatus.FAILED

    await ReleaseOrchestrator.retry_release(db_session, rel)
    assert rel.status == ReleaseStatus.ANALYZING


@pytest.mark.asyncio
async def test_release_lifecycle_api_end_to_end(async_engine, session_factory, db_session) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import bootstrap_workspace_defaults, create_app

    async with session_factory() as boot_sess:
        await bootstrap_workspace_defaults(boot_sess, bootstrap_env_tokens=False)
        await boot_sess.commit()

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Ensure static /overview is NOT shadowed by /{release_id}
        ov_resp = await client.get("/api/v1/releases/overview")
        assert ov_resp.status_code == 200

        # Create manual release via API
        create_resp = await client.post(
            "/api/v1/releases",
            json={
                "title": "NovaShop checkout_v2 E2E Release",
                "release_type": "feature",
                "target_environment": "development",
                "risk_level": "low",
                "target_rollout_percentage": 25,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        detail = create_resp.json()
        rel_id = detail["release"]["id"]
        assert detail["release"]["release_key"].startswith("REL-")
        assert len(detail["stages"]) == 9

        # List & filter
        list_resp = await client.get("/api/v1/releases", params={"q": "NovaShop"})
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] >= 1

        # Evaluate readiness
        eval_resp = await client.post(f"/api/v1/releases/{rel_id}/readiness/evaluate")
        assert eval_resp.status_code == 200

        # Pause & Resume with optimistic concurrency
        ver = eval_resp.json()["release"]["version"]
        pause_resp = await client.post(
            f"/api/v1/releases/{rel_id}/pause",
            json={"expected_version": ver, "reason": "Hold for QA"},
        )
        assert pause_resp.status_code == 200
        assert pause_resp.json()["release"]["status"] == "paused"

        ver2 = pause_resp.json()["release"]["version"]
        resume_resp = await client.post(
            f"/api/v1/releases/{rel_id}/resume",
            json={"expected_version": ver2},
        )
        assert resume_resp.status_code == 200
        assert resume_resp.json()["release"]["status"] == "draft"

        # Request approval & approve via approvals API (verify Release syncs to APPROVED)
        appr_resp = await client.post(
            f"/api/v1/releases/{rel_id}/approval-request",
            json={"rationale": "Approve NovaShop release"},
        )
        assert appr_resp.status_code == 201
        approval_id = appr_resp.json()["id"]

        dec_resp = await client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"comment": "LGTM"},
        )
        assert dec_resp.status_code == 200, dec_resp.text

        detail_after = (await client.get(f"/api/v1/releases/{rel_id}")).json()
        assert detail_after["release"]["status"] == "approved"

        # Report JSON + Markdown export
        rep_resp = await client.get(f"/api/v1/releases/{rel_id}/report")
        assert rep_resp.status_code == 200
        assert rep_resp.json()["release_id"] == rel_id

        md_resp = await client.get(f"/api/v1/releases/{rel_id}/report/export")
        assert md_resp.status_code == 200
        assert "text/markdown" in md_resp.headers["content-type"]
        assert "# Release Report:" in md_resp.text
