"""Tests for Phase 5 Persistent Notifications, Workspace SSE, and Deep System Health."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.database import utcnow
from app.core.security import hash_password, issue_auth_token
from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    AnalyticsMode,
    ApprovalCategory,
    Experiment,
    ExperimentEvent,
    ExperimentEventType,
    ExperimentStatus,
    FeatureFlag,
    FlagType,
    NotificationCategory,
    User,
    UserRole,
)
from app.services.notification_service import create_notification
from app.services.policy_engine import PolicyEngine
from app.services.sse_bus import sse_bus


@pytest.mark.asyncio
async def test_notifications_lifecycle_redaction_ownership_and_preferences(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )

    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)
        user_a = User(
            email="notif-a@example.com",
            display_name="Operator A",
            role=UserRole.ENGINEER,
            password_hash=hash_password("Pass123!"),
            is_active=True,
        )
        user_b = User(
            email="notif-b@example.com",
            display_name="Operator B",
            role=UserRole.ENGINEER,
            password_hash=hash_password("Pass123!"),
            is_active=True,
        )
        session.add_all([user_a, user_b])
        await session.flush()

        # Private notification with sensitive secret token that must be scrubbed
        n_priv = await create_notification(
            session,
            category=NotificationCategory.INTEGRATION_ERROR,
            severity="high",
            title="GitHub Auth Token Failed",
            message="Failed using ghp_SuperSecretTokenValue1234567890",
            user_id=user_a.id,
            resource_type="integration",
            resource_id="github",
            metadata={"token": "ghp_SuperSecretTokenValue1234567890"},
        )
        # Workspace-wide critical alert
        n_ws = await create_notification(
            session,
            category=NotificationCategory.GUARDRAIL_BREACH,
            severity="critical",
            title="Guardrail Breach in Production",
            message="payment_failed delta breached threshold",
            resource_type="experiment",
            resource_id="exp-1",
            environment_name="production",
        )
        await session.commit()
        assert n_priv is not None and n_ws is not None
        assert "SuperSecretToken" not in n_priv.message
        assert "SuperSecretToken" not in str(n_priv.metadata_json)
        priv_id = n_priv.id
        ws_id = n_ws.id

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tok_a = issue_auth_token(
            user_id=str(user_a.id), email=user_a.email, role=user_a.role.value, token_version=user_a.token_version
        )
        tok_b = issue_auth_token(
            user_id=str(user_b.id), email=user_b.email, role=user_b.role.value, token_version=user_b.token_version
        )

        # Operator A sees both; Operator B only sees workspace notification
        r_list_a = await client.get("/api/v1/notifications", headers={"Authorization": f"Bearer {tok_a}"})
        assert r_list_a.status_code == 200
        assert r_list_a.json()["total"] == 2

        r_list_b = await client.get("/api/v1/notifications", headers={"Authorization": f"Bearer {tok_b}"})
        assert r_list_b.status_code == 200
        assert r_list_b.json()["total"] == 1
        assert r_list_b.json()["items"][0]["id"] == str(ws_id)

        # Operator B cannot mark Operator A's private notification read
        r_forbid = await client.patch(
            f"/api/v1/notifications/{priv_id}/read",
            headers={"Authorization": f"Bearer {tok_b}"},
        )
        assert r_forbid.status_code == 403

        # Unread count before read
        r_uc = await client.get("/api/v1/notifications/unread-count", headers={"Authorization": f"Bearer {tok_a}"})
        assert r_uc.status_code == 200
        assert r_uc.json()["unread_count"] == 2
        assert r_uc.json()["critical_unread_count"] == 1

        # Filter unread + mark single read
        r_read = await client.patch(
            f"/api/v1/notifications/{priv_id}/read",
            headers={"Authorization": f"Bearer {tok_a}"},
        )
        assert r_read.status_code == 200
        assert r_read.json()["is_read"] is True

        # Preferences update: mute schedule_completed + raise threshold
        r_pref = await client.patch(
            "/api/v1/notifications/preferences",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={
                "in_app_enabled": True,
                "severity_threshold": "warning",
                "muted_categories": ["schedule_completed"],
            },
        )
        assert r_pref.status_code == 200
        assert r_pref.json()["severity_threshold"] == "warning"

        # Mark all read + clear resolved
        r_all = await client.post("/api/v1/notifications/read-all", headers={"Authorization": f"Bearer {tok_a}"})
        assert r_all.status_code == 200
        assert r_all.json()["marked_read"] >= 1

        r_clr = await client.post(
            "/api/v1/notifications/clear-resolved",
            headers={"Authorization": f"Bearer {tok_a}"},
        )
        assert r_clr.status_code == 200
        assert r_clr.json()["cleared_count"] >= 1

    # Preference suppression check: info notification skipped for user_a while critical passes
    async with session_factory() as session:
        suppressed = await create_notification(
            session,
            category=NotificationCategory.SCHEDULE_COMPLETED,
            severity="info",
            title="Routine Info",
            message="Routine",
            user_id=user_a.id,
        )
        assert suppressed is None
        critical_allowed = await create_notification(
            session,
            category=NotificationCategory.GUARDRAIL_BREACH,
            severity="critical",
            title="Urgent",
            message="Must never be suppressed",
            user_id=user_a.id,
        )
        assert critical_allowed is not None


@pytest.mark.asyncio
async def test_notification_triggers_sse_and_deep_system_health(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify automatic alert triggers on approval & breach plus deep /system/* probes."""
    def fail_demo_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={"status": "down"})

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )
    app.state.demo_probe_transport = httpx.MockTransport(fail_demo_handler)
    app.state.demo_frontend_transport = httpx.MockTransport(lambda req: httpx.Response(200, text="ok"))

    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)
        flag = FeatureFlag(
            flagsmith_project_id=77,
            flagsmith_feature_id=701,
            key="notif_checkout_flag",
            flag_type=FlagType.STANDARD,
            env_states={"production": {"enabled": False, "rollout_percentage": 0}},
        )
        session.add(flag)
        await session.flush()

        exp = Experiment(
            feature_flag_id=flag.id,
            environment_name="production",
            name="Checkout Breach Exp",
            hypothesis="Guardrail test",
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

        for i in range(35):
            session.add(
                ExperimentEvent(
                    event_id=f"ev-n-v1-{i}",
                    experiment_id=exp.id,
                    flag_key=flag.key,
                    environment_name="production",
                    variation_key="v1",
                    user_identifier=f"nu1-{i}",
                    event_type=ExperimentEventType.FEATURE_VARIATION_ASSIGNED,
                    occurred_at=utcnow(),
                )
            )
            session.add(
                ExperimentEvent(
                    event_id=f"ev-n-v2-{i}",
                    experiment_id=exp.id,
                    flag_key=flag.key,
                    environment_name="production",
                    variation_key="v2",
                    user_identifier=f"nu2-{i}",
                    event_type=ExperimentEventType.FEATURE_VARIATION_ASSIGNED,
                    occurred_at=utcnow(),
                )
            )
        for i in range(15):
            session.add(
                ExperimentEvent(
                    event_id=f"ev-n-pf-{i}",
                    experiment_id=exp.id,
                    flag_key=flag.key,
                    environment_name="production",
                    variation_key="v2",
                    user_identifier=f"nu2-{i}",
                    event_type=ExperimentEventType.PAYMENT_FAILED,
                    occurred_at=utcnow(),
                )
            )

        # Creating approval automatically creates APPROVAL_REQUIRED notification
        app_row = await PolicyEngine.create_approval(
            session,
            action_type="flagsmith_toggle_flag",
            target_resource_type="feature_flag",
            target_resource_id=str(flag.id),
            environment_name="production",
            title="Enable notif_checkout_flag in production",
            action_payload={"flag_key": flag.key, "environment_name": "production", "enabled": True, "reason": "Test"},
        )
        assert app_row.policy_category == ApprovalCategory.C
        await session.commit()
        exp_id = exp.id

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Recommendation breach trigger emits GUARDRAIL_BREACH notification once (deduped)
        r_rec = await client.get(f"/api/v1/experiments/{exp_id}/recommendation")
        assert r_rec.status_code == 200
        assert r_rec.json()["recommendation"] == "Recommend Rollback"

        r_notifs = await client.get("/api/v1/notifications")
        assert r_notifs.status_code == 200
        cats = {n["category"] for n in r_notifs.json()["items"]}
        assert "approval_required" in cats
        assert "guardrail_breach" in cats

        # Deep System Health reflects DB ok + Demo Backend degraded (503) + API telemetry + Scheduler
        r_sys = await client.get("/api/v1/system/health")
        assert r_sys.status_code == 200
        body = r_sys.json()
        assert body["database"]["status"] == "ok"
        assert body["database"]["latency_ms"] >= 0
        assert body["demo_app"]["backend"]["status"] == "degraded"
        assert body["demo_app"]["backend"]["http_status"] == 503
        assert body["backend_api"]["requests_total"] >= 1

        r_int = await client.get("/api/v1/system/integrations")
        assert r_int.status_code == 200
        assert "github" in r_int.json() and "flagsmith" in r_int.json() and "gemini" in r_int.json()

        r_wk = await client.get("/api/v1/system/workers")
        assert r_wk.status_code == 200
        assert "agent_tasks" in r_wk.json() and "job_executions" in r_wk.json()

        r_sc = await client.get("/api/v1/system/scheduler")
        assert r_sc.status_code == 200
        assert "worker" in r_sc.json() and "counts" in r_sc.json()

    # Verify workspace SSE bus publishes and replays cleanly
    published = await sse_bus.publish_workspace_event("test.ping", {"ok": True})
    sub = sse_bus.subscribe_workspace(after_seq=max(0, int(published["seq"]) - 1), heartbeat_seconds=1.0)
    ev = await asyncio.wait_for(sub.__anext__(), timeout=2.0)
    assert ev["event_type"] == "test.ping"
    assert ev["payload"]["ok"] is True
    await sub.aclose()


@pytest.mark.asyncio
async def test_e2e_schedule_notification_dashboard_and_prod_gate(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """End-to-end chain: schedule create -> execute -> history -> notification -> dashboard -> prod approval gate."""
    from datetime import timedelta

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )
    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)
        flag = FeatureFlag(
            flagsmith_project_id=88,
            flagsmith_feature_id=801,
            key="e2e_checkout_flag",
            flag_type=FlagType.STANDARD,
            env_states={"production": {"enabled": False, "rollout_percentage": 0}},
        )
        session.add(flag)
        await session.flush()
        exp = Experiment(
            feature_flag_id=flag.id,
            environment_name="production",
            name="E2E Guardrail Exp",
            hypothesis="Check E2E flow",
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
        for i in range(35):
            session.add(
                ExperimentEvent(
                    event_id=f"e2e-v1-{i}",
                    experiment_id=exp.id,
                    flag_key=flag.key,
                    environment_name="production",
                    variation_key="v1",
                    user_identifier=f"eu1-{i}",
                    event_type=ExperimentEventType.FEATURE_VARIATION_ASSIGNED,
                    occurred_at=utcnow(),
                )
            )
            session.add(
                ExperimentEvent(
                    event_id=f"e2e-v2-{i}",
                    experiment_id=exp.id,
                    flag_key=flag.key,
                    environment_name="production",
                    variation_key="v2",
                    user_identifier=f"eu2-{i}",
                    event_type=ExperimentEventType.FEATURE_VARIATION_ASSIGNED,
                    occurred_at=utcnow(),
                )
            )
        for i in range(15):
            session.add(
                ExperimentEvent(
                    event_id=f"e2e-pf-{i}",
                    experiment_id=exp.id,
                    flag_key=flag.key,
                    environment_name="production",
                    variation_key="v2",
                    user_identifier=f"eu2-{i}",
                    event_type=ExperimentEventType.PAYMENT_FAILED,
                    occurred_at=utcnow(),
                )
            )
        await session.commit()
        exp_id = exp.id
        flag_id = flag.id

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create daily experiment monitor schedule with on_success notification enabled
        r_sched = await client.post(
            "/api/v1/schedules",
            json={
                "name": "E2E Daily Guardrail Monitor",
                "job_type": "experiment_monitor",
                "frequency": "daily",
                "cron_expression": "0 9 * * *",
                "timezone": "Asia/Kolkata",
                "start_at": (utcnow() + timedelta(minutes=5)).isoformat(),
                "configuration": {"experiment_id": str(exp_id)},
                "notification_preference": {"on_success": True, "on_failure": True},
            },
        )
        assert r_sched.status_code == 201, r_sched.text
        sched_id = r_sched.json()["id"]

        # 2. Run schedule -> executes Bayesian monitor & detects guardrail breach
        r_run = await client.post(f"/api/v1/schedules/{sched_id}/run")
        assert r_run.status_code == 200, r_run.text
        assert r_run.json()["execution_status"] == "completed"
        assert r_run.json()["result_summary"]["guardrail_breaches"] == 1

        # 3. Verify JobExecution history persisted
        r_execs = await client.get(f"/api/v1/schedules/{sched_id}/executions")
        assert r_execs.status_code == 200
        assert r_execs.json()["total"] == 1

        # 4. Verify Notifications created (guardrail_breach + schedule_completed)
        r_notif = await client.get("/api/v1/notifications")
        assert r_notif.status_code == 200
        notif_cats = {n["category"] for n in r_notif.json()["items"]}
        assert "guardrail_breach" in notif_cats
        assert "schedule_completed" in notif_cats

        # 5. Verify Dashboard Overview reflects schedule + guardrail breach pending action
        r_dash = await client.get("/api/v1/dashboard/overview")
        assert r_dash.status_code == 200
        dash = r_dash.json()
        assert any(s["id"] == sched_id for s in dash["upcoming_schedules"])
        assert any(a["category"] == "guardrail_breach" for a in dash["pending_actions"])

        # 6. Production rollback mutation still strictly requires Category C human approval (202)
        r_prod = await client.post(
            f"/api/v1/feature-flags/{flag_id}/toggle",
            json={
                "environment_name": "production",
                "enabled": False,
                "reason": "Mitigate guardrail breach",
            },
        )
        assert r_prod.status_code == 202
        assert r_prod.json()["execution_status"] == "approval_required"
        assert r_prod.json()["approval"]["policy_category"] == "C"

