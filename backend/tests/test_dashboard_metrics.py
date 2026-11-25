"""Tests for Phase 2 & Phase 3 Dashboard Overview, Metrics, Charts, Filters, and RBAC."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.database import utcnow
from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    ActorType,
    AgentSession,
    AgentTask,
    AgentTaskStatus,
    AnalysisStatus,
    AnalyticsMode,
    Approval,
    ApprovalCategory,
    ApprovalStatus,
    AuditLog,
    AuditStatus,
    CodeChange,
    CodeChangeType,
    Experiment,
    ExperimentEvent,
    ExperimentEventType,
    ExperimentStatus,
    FeatureFlag,
    FlagType,
    PolicyCategory,
    PullRequest,
    PullRequestState,
    ReleaseRecommendation,
    Repository,
    RiskLevel,
    RolloutAction,
    RolloutHistory,
    SpecialistRole,
    ValidationStatus,
)


@pytest.mark.asyncio
async def test_dashboard_overview_metrics_charts_empty_and_populated(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )
    async with session_factory() as setup_sess:
        await bootstrap_workspace_defaults(setup_sess, bootstrap_env_tokens=False)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Empty workspace sanity check: no fake data or fabricated trends
        empty_ov = await client.get("/api/v1/dashboard/overview")
        assert empty_ov.status_code == 200
        ev_data = empty_ov.json()
        assert ev_data["metrics"]["engineering"]["connected_repositories"] == 0
        assert ev_data["metrics"]["releases"]["release_success_rate"] is None
        assert ev_data["metrics"]["agents"]["average_execution_duration_seconds"] is None
        assert ev_data["metrics"]["engineering"]["trends"] == {}
        assert ev_data["recent_activity"] == []
        assert ev_data["pending_actions"] == []

        # 2. Populate realistic persisted database records across all domains
        now = utcnow()
        async with session_factory() as s:
            repo = Repository(
                github_repo_id=501,
                owner="acme",
                name="storefront",
                full_name="acme/storefront",
                flagsmith_project_id=12,
            )
            s.add(repo)
            await s.flush()

            pr1 = PullRequest(
                repository_id=repo.id,
                github_pr_number=11,
                title="Checkout v2 express",
                author_login="dev1",
                head_branch="feat/v2",
                base_branch="main",
                head_sha="sha1",
                state=PullRequestState.OPEN,
                html_url="https://github.com/acme/storefront/pull/11",
                analysis_status=AnalysisStatus.COMPLETED,
                risk_score=72,
                risk_level=RiskLevel.HIGH,
                created_at=now - timedelta(hours=3),
            )
            pr2 = PullRequest(
                repository_id=repo.id,
                github_pr_number=10,
                title="Older PR",
                author_login="dev1",
                head_branch="feat/old",
                base_branch="main",
                head_sha="sha0",
                state=PullRequestState.OPEN,
                html_url="https://github.com/acme/storefront/pull/10",
                analysis_status=AnalysisStatus.FAILED,
                created_at=now - timedelta(days=10),
            )
            s.add_all([pr1, pr2])
            await s.flush()

            flag = FeatureFlag(
                flagsmith_project_id=12,
                repository_id=repo.id,
                key="checkout_v2",
                flag_type=FlagType.STANDARD,
                health_score=80,
                health_issues=[{"issue_type": "env_drift"}],
                env_states={
                    "development": {"enabled": False, "rollout_percentage": 50, "is_production": False},
                    "production": {"enabled": False, "rollout_percentage": 0, "is_production": True},
                },
            )
            s.add(flag)
            await s.flush()

            chg = CodeChange(
                repository_id=repo.id,
                pull_request_id=pr1.id,
                feature_flag_id=flag.id,
                change_type=CodeChangeType.FLAG_WRAP,
                flag_key="checkout_v2",
                title="Wrap checkout_v2",
                validation_status=ValidationStatus.PASSED,
                draft_pr_number=12,
                draft_pr_url="https://github.com/acme/storefront/pull/12",
            )
            s.add(chg)

            s.add_all([
                RolloutHistory(
                    feature_flag_id=flag.id,
                    environment_name="development",
                    action=RolloutAction.ROLLOUT_STEP,
                    previous_state={"pct": 0},
                    new_state={"pct": 50},
                    verified=True,
                    reason="Step 50%",
                    created_at=now - timedelta(hours=2),
                ),
                RolloutHistory(
                    feature_flag_id=flag.id,
                    environment_name="development",
                    action=RolloutAction.ROLLBACK,
                    previous_state={"pct": 50},
                    new_state={"pct": 0},
                    verified=True,
                    reason="Guardrail breach",
                    created_at=now - timedelta(hours=1),
                ),
            ])

            exp = Experiment(
                feature_flag_id=flag.id,
                name="Checkout v2 Exp",
                hypothesis="Improves checkout conversion",
                environment_name="development",
                analytics_mode=AnalyticsMode.TRACKED_EVENT_FALLBACK,
                status=ExperimentStatus.RUNNING,
                latest_recommendation=ReleaseRecommendation.RECOMMEND_ROLLBACK,
                started_at=now - timedelta(hours=4),
            )
            s.add(exp)
            await s.flush()

            # Simulator + interactive telemetry events
            for i in range(4):
                uid = f"u_{i}"
                arm = "treatment" if i % 2 == 0 else "control"
                s.add(
                    ExperimentEvent(
                        event_id=f"evt_exp_{i}",
                        experiment_id=exp.id,
                        flag_key="checkout_v2",
                        environment_name="development",
                        variation_key=arm,
                        event_type=ExperimentEventType.FEATURE_VARIATION_ASSIGNED,
                        user_identifier=uid,
                        session_key="sim_healthy_1" if i < 2 else "live_sess_1",
                        occurred_at=now - timedelta(minutes=20 - i),
                    )
                )
                s.add(
                    ExperimentEvent(
                        event_id=f"evt_conv_{i}",
                        experiment_id=exp.id,
                        flag_key="checkout_v2",
                        environment_name="development",
                        variation_key=arm,
                        event_type=(
                            ExperimentEventType.PAYMENT_FAILED
                            if arm == "treatment"
                            else ExperimentEventType.CHECKOUT_COMPLETED
                        ),
                        user_identifier=uid,
                        session_key="sim_healthy_1" if i < 2 else "live_sess_1",
                        occurred_at=now - timedelta(minutes=15 - i),
                    )
                )

            sess = AgentSession(title="Run", thread_id=f"th-{uuid.uuid4().hex}")
            s.add(sess)
            await s.flush()
            s.add(
                AgentTask(
                    session_id=sess.id,
                    specialist=SpecialistRole.FLAG_AGENT,
                    user_prompt="Rollout check",
                    status=AgentTaskStatus.COMPLETED,
                    steps_log=[{"seq": 1, "event_type": "tool.executed"}],
                    token_usage={"total_tokens": 321},
                    started_at=now - timedelta(seconds=10),
                    completed_at=now - timedelta(seconds=4),
                    created_at=now - timedelta(minutes=30),
                )
            )
            s.add(
                Approval(
                    policy_category=ApprovalCategory.C,
                    action_type="flagsmith_rollout",
                    target_resource_type="feature_flag",
                    target_resource_id=str(flag.id),
                    environment_name="production",
                    title="Enable in prod",
                    action_payload={"enabled": True},
                    expected_precondition={},
                    action_hash="a" * 64,
                    idempotency_key=f"idem-{uuid.uuid4().hex}",
                    status=ApprovalStatus.PENDING,
                    expires_at=now + timedelta(minutes=15),
                )
            )
            s.add(
                AuditLog(
                    actor_type=ActorType.USER,
                    actor_id="op1",
                    policy_category=PolicyCategory.B,
                    event_action="flagsmith.rollout",
                    resource_type="feature_flag",
                    resource_id=str(flag.id),
                    environment_name="development",
                    status=AuditStatus.SUCCESS,
                    details={"flag_key": "checkout_v2", "target_percentage": 50},
                )
            )
            await s.commit()

        # 3. Verify populated overview, time filters, event source separation, and pending actions
        ov_7d = (await client.get("/api/v1/dashboard/overview?range=7d")).json()
        eng = ov_7d["metrics"]["engineering"]
        assert eng["connected_repositories"] == 1
        assert eng["synchronized_prs"] == 1  # pr2 was 10 days ago -> outside 7d window
        assert eng["prs_analyzed"] == 1
        assert eng["draft_prs_created"] == 1
        assert eng["failed_analysis_tasks"] == 1
        assert "synchronized_prs" in eng["trends"]  # prior_prs exists 10 days ago!

        rel = ov_7d["metrics"]["releases"]
        assert rel["total_releases"] == 2
        assert rel["rollbacks_executed"] == 1
        assert rel["successful_releases"] == 1
        assert rel["release_success_rate"] == 100.0
        assert rel["releases_in_progress"] == 1

        agents = ov_7d["metrics"]["agents"]
        assert agents["total_agent_executions"] == 1
        assert agents["successful_executions"] == 1
        assert agents["agent_tool_calls"] == 1
        assert agents["total_tokens"] == 321
        assert agents["average_execution_duration_seconds"] == pytest.approx(6.0, abs=0.5)

        charts = ov_7d["charts"]
        assert charts["telemetry_totals"]["total_events"] == 8
        assert charts["telemetry_totals"]["simulator_events"] == 4
        assert charts["telemetry_totals"]["application_telemetry_events"] == 4
        assert len(charts["experiment_performance"]) == 1
        assert charts["experiment_performance"][0]["simulator_event_count"] == 4
        assert charts["experiment_performance"][0]["telemetry_event_count"] == 4

        pending_categories = {p["category"] for p in ov_7d["pending_actions"]}
        assert "pending_approval" in pending_categories
        assert "guardrail_breach" in pending_categories
        assert "failed_pr_analysis" in pending_categories
        assert len(ov_7d["recent_activity"]) >= 2

        # Repository & environment filtering test
        ov_prod = (
            await client.get(f"/api/v1/dashboard/metrics?range=30d&repository_id={repo.id}&environment=production")
        ).json()
        assert ov_prod["engineering"]["synchronized_prs"] == 2
        assert ov_prod["releases"]["total_releases"] == 0
        assert ov_prod["feature_flags"]["disabled_flags"] == 1

        # Phase 3 Metrics & Analytics endpoints + CSV export verification
        m_ov = (await client.get("/api/v1/metrics/overview?range=30d")).json()
        assert len(m_ov["metric_definitions"]) >= 5
        assert m_ov["filter_options"]["repositories"][0]["full_name"] == "acme/storefront"

        m_rel = (await client.get("/api/v1/metrics/releases?range=30d")).json()
        assert m_rel["metrics"]["deployment_frequency"]["status"] == "unavailable"
        assert m_rel["metrics"]["deployment_frequency"]["value"] is None
        assert m_rel["metrics"]["release_frequency_rollouts"] == 2
        assert m_rel["metrics"]["rollback_rate"] == 50.0
        assert m_rel["metrics"]["mean_time_to_recovery_seconds"] is not None

        m_pr = (await client.get("/api/v1/metrics/pull-requests?range=30d")).json()
        assert m_pr["metrics"]["total_prs"] == 2
        assert m_pr["metrics"]["draft_prs_generated"] == 1
        assert m_pr["metrics"]["patch_validation_success_rate"] == 100.0

        m_fl = (await client.get("/api/v1/metrics/feature-flags?range=30d")).json()
        assert m_fl["metrics"]["total_flags"] == 1
        assert "development" in m_fl["flags"][0]["environments"]

        m_exp = (await client.get("/api/v1/metrics/experiments?range=30d")).json()
        assert len(m_exp["experiments"]) == 1

        m_ag = (await client.get("/api/v1/metrics/agents?range=30d")).json()
        assert m_ag["metrics"]["total_runs"] == 1
        assert m_ag["metrics"]["total_tokens"] == 321
        assert m_ag["metrics"]["average_task_cost_usd"] is None

        m_ev_sim = (await client.get("/api/v1/metrics/events?range=30d&event_source=simulator_generated")).json()
        assert m_ev_sim["totals"]["total_events"] == 4
        assert m_ev_sim["totals"]["simulator_events"] == 4
        assert m_ev_sim["totals"]["application_telemetry_events"] == 0

        csv_resp = await client.get("/api/v1/metrics/export?section=releases&range=30d")
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers["content-type"]
        assert "checkout_v2" in csv_resp.text
