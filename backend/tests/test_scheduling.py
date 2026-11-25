"""Tests for Phase 4 Persistent Scheduling, Cron/TZ Conversion, Retries, Locks & Policy Safety."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.database import utcnow
from app.core.security import hash_password, issue_auth_token
from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    AnalyticsMode,
    Approval,
    AuditLog,
    Experiment,
    ExperimentEvent,
    ExperimentEventType,
    ExperimentStatus,
    FeatureFlag,
    FlagType,
    JobExecution,
    JobExecutionStatus,
    JobTriggerType,
    PullRequest,
    PullRequestState,
    Repository,
    ScheduledJob,
    ScheduleFrequency,
    ScheduleJobType,
    User,
    UserRole,
)
from app.services.cron_utils import ParsedCron, validate_custom_schedule_prompt
from app.services.scheduler import JobAlreadyRunningError, SchedulerService


class _FakeResponse:
    def __init__(self, text: str = "", function_calls: list[dict[str, Any]] | None = None) -> None:
        self.text = text
        self.candidates = []
        self.usage_metadata = None
        if function_calls:
            parts = []
            for fc in function_calls:
                parts.append(
                    type(
                        "Part",
                        (),
                        {
                            "text": None,
                            "function_call": type(
                                "FC", (), {"name": fc["name"], "args": fc.get("args") or {}}
                            )(),
                        },
                    )()
                )
            self.candidates = [type("Cand", (), {"content": type("Content", (), {"parts": parts})()})()]


def test_cron_timezone_and_dst_conversion() -> None:
    """Verify IST (+05:30) and US Eastern DST spring-forward conversions to UTC."""
    from zoneinfo import ZoneInfo

    parsed = ParsedCron("0 9 * * *")
    # 09:00 Asia/Kolkata -> 03:30 UTC
    ist_ref = datetime(2026, 6, 1, 2, 0, tzinfo=UTC)  # 07:30 IST
    next_ist = parsed.next_run_after(ist_ref, ZoneInfo("Asia/Kolkata"))
    assert next_ist.hour == 3 and next_ist.minute == 30

    # America/New_York: 2026-03-08 is spring-forward (02:00 -> 03:00 EST->EDT)
    ny_tz = ZoneInfo("America/New_York")
    before_dst = datetime(2026, 3, 7, 13, 0, tzinfo=UTC)  # 08:00 EST
    est_run = parsed.next_run_after(before_dst, ny_tz)
    assert est_run.hour == 14 and est_run.minute == 0  # 09:00 EST == 14:00 UTC

    after_dst = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)  # 08:00 EDT
    edt_run = parsed.next_run_after(after_dst, ny_tz)
    assert edt_run.hour == 13 and edt_run.minute == 0  # 09:00 EDT == 13:00 UTC


def test_nl_custom_schedule_safety_validator() -> None:
    ok_safe, err_safe = validate_custom_schedule_prompt(
        "Every weekday at 9am scan feature flag health and summarize open high-risk PRs"
    )
    assert ok_safe and err_safe is None

    for unsafe in (
        "Run rm -rf /tmp && restart container",
        "Auto-merge every open pull request nightly",
        "Auto-approve pending production approvals at midnight",
        "Delete production flags automatically every week",
    ):
        ok, err = validate_custom_schedule_prompt(unsafe)
        assert not ok and err


@pytest.mark.asyncio
async def test_schedule_crud_pause_resume_run_delete_history_and_rbac(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify CRUD, RBAC ownership, manual GitHub sync run, and history preservation on delete."""
    def gh_handler(request: httpx.Request) -> httpx.Response:
        if "/pulls" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 42,
                        "title": "Add promo code discount",
                        "body": "Checkout update",
                        "user": {"login": "alice"},
                        "head": {"ref": "feat/promo", "sha": "abc1234567890" * 3 + "a"},
                        "base": {"ref": "main"},
                        "state": "open",
                        "draft": False,
                        "html_url": "https://github.com/acme/novashop/pull/42",
                    }
                ],
            )
        return httpx.Response(200, json={})

    from types import SimpleNamespace

    class _PrSummaryGemini:
        def __init__(self) -> None:
            self.models = SimpleNamespace(generate_content=self._generate_content)

        def _generate_content(self, **kwargs: Any) -> Any:
            del kwargs
            return _FakeResponse(text='{"analysis_summary": "Flag checkout changes.", "confirmed_risks": [], "potential_risks": [], "flag_opportunities": []}')

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
        github_transport=httpx.MockTransport(gh_handler),
        flagsmith_transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"results": []})),
        gemini_raw_client=_PrSummaryGemini(),
    )

    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)
        viewer = User(
            email="viewer-sched@example.com",
            display_name="Viewer",
            role=UserRole.VIEWER,
            password_hash=hash_password("ViewerPass123!"),
            is_active=True,
        )
        eng_a = User(
            email="eng-a@example.com",
            display_name="Engineer A",
            role=UserRole.ENGINEER,
            password_hash=hash_password("EngAPass123!"),
            is_active=True,
        )
        eng_b = User(
            email="eng-b@example.com",
            display_name="Engineer B",
            role=UserRole.ENGINEER,
            password_hash=hash_password("EngBPass123!"),
            is_active=True,
        )
        repo = Repository(
            github_repo_id=9101,
            owner="acme",
            name="novashop",
            full_name="acme/novashop",
            default_branch="main",
            is_private=False,
        )
        session.add_all([viewer, eng_a, eng_b, repo])
        await session.commit()
        await session.refresh(repo)
        repo_id = repo.id

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        viewer_tok = issue_auth_token(
            user_id=str(viewer.id), email=viewer.email, role=viewer.role.value, token_version=viewer.token_version
        )
        eng_a_tok = issue_auth_token(
            user_id=str(eng_a.id), email=eng_a.email, role=eng_a.role.value, token_version=eng_a.token_version
        )
        eng_b_tok = issue_auth_token(
            user_id=str(eng_b.id), email=eng_b.email, role=eng_b.role.value, token_version=eng_b.token_version
        )

        # Viewer forbidden to create schedule
        r_forbid = await client.post(
            "/api/v1/schedules",
            headers={"Authorization": f"Bearer {viewer_tok}"},
            json={
                "name": "Blocked Viewer Job",
                "job_type": "repo_sync",
                "frequency": "daily",
                "timezone": "UTC",
            },
        )
        assert r_forbid.status_code == 403

        # Preview invalid timezone rejected
        r_bad_tz = await client.post(
            "/api/v1/schedules/preview",
            headers={"Authorization": f"Bearer {eng_a_tok}"},
            json={"frequency": "daily", "timezone": "Mars/Olympus"},
        )
        assert r_bad_tz.status_code == 400

        # Valid preview
        r_prev = await client.post(
            "/api/v1/schedules/preview",
            headers={"Authorization": f"Bearer {eng_a_tok}"},
            json={
                "frequency": "daily",
                "cron_expression": "30 9 * * *",
                "timezone": "Asia/Kolkata",
                "nl_prompt": "Every weekday at 9am check flag health",
            },
        )
        assert r_prev.status_code == 200
        prev_body = r_prev.json()
        assert prev_body["valid"] is True
        assert len(prev_body["next_runs"]) == 5

        # Engineer A creates Repo Sync schedule
        r_create = await client.post(
            "/api/v1/schedules",
            headers={"Authorization": f"Bearer {eng_a_tok}"},
            json={
                "name": "Nightly NovaShop Sync",
                "job_type": "repo_sync",
                "frequency": "daily",
                "cron_expression": "0 2 * * *",
                "timezone": "Asia/Kolkata",
                "configuration": {"repository_id": str(repo_id)},
                "retry_policy": {"max_retries": 1, "retry_delay_seconds": 1, "timeout_seconds": 30},
            },
        )
        assert r_create.status_code == 201, r_create.text
        sched = r_create.json()
        sched_id = sched["id"]
        assert sched["next_run_at"] is not None
        assert "Asia/Kolkata" in sched["human_description"]

        # Engineer B cannot modify Engineer A's schedule
        r_other = await client.post(
            f"/api/v1/schedules/{sched_id}/pause",
            headers={"Authorization": f"Bearer {eng_b_tok}"},
        )
        assert r_other.status_code == 403

        # Pause & Resume by owner
        r_pause = await client.post(
            f"/api/v1/schedules/{sched_id}/pause",
            headers={"Authorization": f"Bearer {eng_a_tok}"},
        )
        assert r_pause.status_code == 200
        assert r_pause.json()["enabled"] is False

        r_resume = await client.post(
            f"/api/v1/schedules/{sched_id}/resume",
            headers={"Authorization": f"Bearer {eng_a_tok}"},
        )
        assert r_resume.status_code == 200
        assert r_resume.json()["enabled"] is True

        # Manual Run executes real GitHub PR sync and stores JobExecution
        r_run = await client.post(
            f"/api/v1/schedules/{sched_id}/run",
            headers={"Authorization": f"Bearer {eng_a_tok}"},
        )
        assert r_run.status_code == 200, r_run.text
        exec_data = r_run.json()
        assert exec_data["execution_status"] == "completed"
        assert exec_data["trigger_type"] == "manual"
        assert exec_data["result_summary"]["pull_requests_synced"] == 1

        # Dashboard overview surfaces upcoming schedule
        r_ov = await client.get(
            "/api/v1/dashboard/overview",
            headers={"Authorization": f"Bearer {viewer_tok}"},
        )
        assert r_ov.status_code == 200
        assert any(s["id"] == sched_id for s in r_ov.json()["upcoming_schedules"])

        # Delete schedule -> preserves historical execution record
        r_del = await client.delete(
            f"/api/v1/schedules/{sched_id}",
            headers={"Authorization": f"Bearer {eng_a_tok}"},
        )
        assert r_del.status_code == 200
        assert r_del.json()["history_preserved"] is True

        r_hist = await client.get(
            "/api/v1/schedules/executions",
            headers={"Authorization": f"Bearer {viewer_tok}"},
        )
        assert r_hist.status_code == 200
        hist_items = r_hist.json()["items"]
        assert len(hist_items) >= 1
        assert hist_items[0]["scheduled_job_id"] is None
        assert hist_items[0]["schedule_name"] == "Nightly NovaShop Sync"


@pytest.mark.asyncio
async def test_scheduler_locks_retries_recovery_pr_skip_and_policy_safety(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify duplicate lock, retry, stale-lock startup recovery, unchanged PR skip, and PolicyEngine gate."""
    def gh_handler(request: httpx.Request) -> httpx.Response:
        if "/files" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "checkout/payment.py",
                        "status": "modified",
                        "additions": 8,
                        "deletions": 1,
                        "patch": "@@ -1,2 +1,5 @@\n+def charge_promo_card():\n+    return process_payment()\n",
                    }
                ],
            )
        return httpx.Response(200, json={})

    from types import SimpleNamespace

    class _PrSummaryGemini:
        def __init__(self) -> None:
            self.models = SimpleNamespace(generate_content=self._generate_content)

        def _generate_content(self, **kwargs: Any) -> Any:
            del kwargs
            return _FakeResponse(text='{"analysis_summary": "Flag checkout changes.", "confirmed_risks": [], "potential_risks": [], "flag_opportunities": []}')

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
        github_transport=httpx.MockTransport(gh_handler),
        flagsmith_transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"results": []})),
        gemini_raw_client=_PrSummaryGemini(),
    )

    attempts = {"count": 0}

    async def flaky_hook(session: AsyncSession, job: ScheduledJob, exec_id: uuid.UUID) -> dict[str, Any]:
        del session, exec_id
        if job.name == "Flaky Job":
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("Transient timeout connecting to upstream")
            return {"summary": {"ok": True, "attempt": attempts["count"]}}
        raise RuntimeError("Should not run hook for other jobs")

    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)
        repo = Repository(
            github_repo_id=9202,
            owner="acme",
            name="core",
            full_name="acme/core",
            default_branch="main",
            is_private=False,
        )
        session.add(repo)
        await session.flush()
        pr = PullRequest(
            repository_id=repo.id,
            github_pr_number=7,
            title="Modify payment flow",
            author_login="dev",
            head_branch="feat/pay",
            base_branch="main",
            head_sha="1" * 40,
            state=PullRequestState.OPEN,
            html_url="https://github.com/acme/core/pull/7",
        )
        flag = FeatureFlag(
            flagsmith_project_id=55,
            flagsmith_feature_id=501,
            key="stale_promo_flag",
            flag_type=FlagType.STANDARD,
            env_states={"development": {"enabled": False, "rollout_percentage": 0}},
        )
        session.add_all([pr, flag])
        await session.flush()

        exp = Experiment(
            feature_flag_id=flag.id,
            environment_name="staging",
            name="Promo Exp",
            hypothesis="Promo discount improves checkout completion without increasing payment errors.",
            analytics_mode=AnalyticsMode.TRACKED_EVENT_FALLBACK,
            primary_metric="checkout_completed",
            control_variation="v1",
            treatment_variation="v2",
            status=ExperimentStatus.RUNNING,
            guardrail_thresholds={
                "guardrail_max_error_rate": 0.05,
                "hard_error_delta": 0.03,
                "soft_error_delta": 0.015,
                "min_exposures": 20,
            },
        )
        session.add(exp)
        await session.flush()

        # Ingest high payment failure rate on treatment (n>=30) to trigger guardrail breach
        for i in range(35):
            session.add(
                ExperimentEvent(
                    event_id=f"ev-v1-exp-{i}",
                    experiment_id=exp.id,
                    flag_key=flag.key,
                    environment_name="staging",
                    variation_key="v1",
                    user_identifier=f"u1-{i}",
                    event_type=ExperimentEventType.FEATURE_VARIATION_ASSIGNED,
                    occurred_at=utcnow(),
                )
            )
            session.add(
                ExperimentEvent(
                    event_id=f"ev-v2-exp-{i}",
                    experiment_id=exp.id,
                    flag_key=flag.key,
                    environment_name="staging",
                    variation_key="v2",
                    user_identifier=f"u2-{i}",
                    event_type=ExperimentEventType.FEATURE_VARIATION_ASSIGNED,
                    occurred_at=utcnow(),
                )
            )
        for i in range(15):
            session.add(
                ExperimentEvent(
                    event_id=f"ev-v2-pf-{i}",
                    experiment_id=exp.id,
                    flag_key=flag.key,
                    environment_name="staging",
                    variation_key="v2",
                    user_identifier=f"u2-{i}",
                    event_type=ExperimentEventType.PAYMENT_FAILED,
                    occurred_at=utcnow(),
                )
            )

        locked_job = ScheduledJob(
            name="Locked Job",
            job_type=ScheduleJobType.FLAG_HEALTH_SCAN,
            frequency=ScheduleFrequency.DAILY,
            cron_expression="0 1 * * *",
            timezone="UTC",
            enabled=True,
            next_run_at=utcnow() - timedelta(minutes=5),
            locked_by="dead-or-active-worker",
            locked_until=utcnow() + timedelta(minutes=10),
        )
        stale_exec = JobExecution(
            scheduled_job_id=None,
            schedule_name="Interrupted Worker Job",
            job_type=ScheduleJobType.REPO_SYNC,
            execution_status=JobExecutionStatus.RUNNING,
            trigger_type=JobTriggerType.SCHEDULED,
            started_at=utcnow() - timedelta(minutes=5),
        )
        flaky_job = ScheduledJob(
            name="Flaky Job",
            job_type=ScheduleJobType.AI_MAINTENANCE_REPORT,
            frequency=ScheduleFrequency.DAILY,
            cron_expression="0 2 * * *",
            timezone="UTC",
            enabled=True,
            retry_policy={"max_retries": 2, "retry_delay_seconds": 1, "timeout_seconds": 30},
            next_run_at=utcnow() + timedelta(hours=1),
        )
        pr_job = ScheduledJob(
            name="PR Analyzer",
            job_type=ScheduleJobType.PR_ANALYSIS,
            frequency=ScheduleFrequency.HOURLY,
            cron_expression="0 * * * *",
            timezone="UTC",
            enabled=True,
            configuration={"repository_id": str(repo.id)},
            next_run_at=utcnow() + timedelta(hours=1),
        )
        exp_job = ScheduledJob(
            name="Exp Monitor",
            job_type=ScheduleJobType.EXPERIMENT_MONITOR,
            frequency=ScheduleFrequency.INTERVAL_MINUTES,
            interval_minutes=15,
            timezone="UTC",
            enabled=True,
            configuration={"experiment_id": str(exp.id)},
            next_run_at=utcnow() + timedelta(minutes=15),
        )
        session.add_all([locked_job, stale_exec, flaky_job, pr_job, exp_job])
        await session.commit()
        locked_id = locked_job.id
        stale_exec_id = stale_exec.id
        flaky_id = flaky_job.id
        pr_job_id = pr_job.id
        exp_job_id = exp_job.id
        pr_id = pr.id

    svc = SchedulerService(app=app, session_factory=session_factory)

    # 1. Duplicate execution prevention rejects locked job
    with pytest.raises(JobAlreadyRunningError):
        await svc.run_job_by_id(locked_id, trigger_type=JobTriggerType.MANUAL)

    # 2. Startup recovery clears stale locks, marks interrupted RUNNING exec FAILED, tracks missed run
    rec = await svc.recover_stale_runs()
    assert rec["recovered_executions"] == 1
    assert rec["cleared_locks"] >= 1
    assert rec["missed_jobs"] >= 1
    async with session_factory() as session:
        st_row = await session.get(JobExecution, stale_exec_id)
        assert st_row is not None and st_row.execution_status == JobExecutionStatus.FAILED

    # 3. Retry recovery succeeds on attempt 2
    app.state.scheduler_job_hook = flaky_hook
    flaky_exec = await svc.run_job_by_id(flaky_id, trigger_type=JobTriggerType.MANUAL)
    assert flaky_exec.execution_status == JobExecutionStatus.COMPLETED
    assert flaky_exec.retry_count == 1
    app.state.scheduler_job_hook = None

    # 4. PR analysis analyzes once then skips unchanged PR until head_sha changes
    pr_exec1 = await svc.run_job_by_id(pr_job_id, trigger_type=JobTriggerType.MANUAL)
    assert pr_exec1.execution_status == JobExecutionStatus.COMPLETED
    assert pr_exec1.result_summary["prs_analyzed"] == 1

    pr_exec2 = await svc.run_job_by_id(pr_job_id, trigger_type=JobTriggerType.MANUAL)
    assert pr_exec2.execution_status == JobExecutionStatus.COMPLETED
    assert pr_exec2.result_summary["prs_analyzed"] == 0
    assert pr_exec2.result_summary["skipped_unchanged"] == 1

    async with session_factory() as session:
        pr_row = await session.get(PullRequest, pr_id)
        assert pr_row is not None
        pr_row.head_sha = "2" * 40
        pr_row.updated_at = utcnow()
        await session.commit()

    pr_exec3 = await svc.run_job_by_id(pr_job_id, trigger_type=JobTriggerType.MANUAL)
    assert pr_exec3.result_summary["prs_analyzed"] == 1

    # 5. Experiment monitor computes Bayesian metrics & spots guardrail breach without mutating prod
    exp_exec = await svc.run_job_by_id(exp_job_id, trigger_type=JobTriggerType.MANUAL)
    assert exp_exec.execution_status == JobExecutionStatus.COMPLETED
    assert exp_exec.result_summary["experiments_evaluated"] == 1
    assert exp_exec.result_summary["guardrail_breaches"] == 1

    # 6. Custom scheduled agent task MUST still pause at PolicyEngine Category C approval

    class _ScriptedGemini:
        def __init__(self) -> None:
            self.models = SimpleNamespace(generate_content=self._generate_content)

        def _generate_content(self, **kwargs: Any) -> Any:
            del kwargs
            return _FakeResponse(
                function_calls=[
                    {
                        "name": "flagsmith_toggle_flag",
                        "args": {
                            "flag_key": "stale_promo_flag",
                            "environment_name": "production",
                            "enabled": True,
                            "reason": "Scheduled rollout test",
                        },
                    }
                ]
            )

    app.state.gemini_raw_client = _ScriptedGemini()
    async with session_factory() as session:
        custom_job = ScheduledJob(
            name="Nightly Custom Flag Agent",
            job_type=ScheduleJobType.CUSTOM_AGENT_TASK,
            frequency=ScheduleFrequency.DAILY,
            cron_expression="0 4 * * *",
            timezone="UTC",
            enabled=True,
            retry_policy={"max_retries": 0, "retry_delay_seconds": 1, "timeout_seconds": 15},
            configuration={"prompt": "Enable stale_promo_flag in production for audit test"},
            next_run_at=utcnow() + timedelta(hours=1),
        )
        session.add(custom_job)
        await session.commit()
        custom_id = custom_job.id

    custom_exec = await svc.run_job_by_id(custom_id, trigger_type=JobTriggerType.MANUAL)
    assert custom_exec.execution_status == JobExecutionStatus.COMPLETED, (
        f"Failed: {custom_exec.error_details}"
    )
    assert custom_exec.agent_task_id is not None
    assert custom_exec.result_summary["awaiting_approval"] is True

    async with session_factory() as session:
        approvals = list((await session.execute(select(Approval))).scalars().all())
        assert len(approvals) >= 1
        audits = list(
            (await session.execute(select(AuditLog).where(AuditLog.resource_id == str(custom_id)))).scalars().all()
        )
        assert len(audits) >= 1
