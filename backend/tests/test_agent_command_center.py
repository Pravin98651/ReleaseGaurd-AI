"""Comprehensive tests for the Autonomous Release Command Center."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    AgentDecisionSource,
    AgentDecisionStatus,
    AgentExecutionStatus,
    AgentToolCallStatus,
    User,
    UserRole,
)
from app.services.agent_execution_service import (
    AgentExecutionControlError,
    AgentExecutionService,
    can_transition_execution,
)


@pytest.mark.asyncio
async def test_execution_state_machine_and_safe_tool_pause(db_session) -> None:
    assert can_transition_execution(AgentExecutionStatus.QUEUED, AgentExecutionStatus.INITIALIZING)
    assert can_transition_execution(AgentExecutionStatus.RUNNING, AgentExecutionStatus.TOOL_RUNNING)
    assert not can_transition_execution(AgentExecutionStatus.COMPLETED, AgentExecutionStatus.RUNNING)
    assert not can_transition_execution(AgentExecutionStatus.CANCELLED, AgentExecutionStatus.PAUSED)

    exe = await AgentExecutionService.create_execution(
        db_session,
        agent_type="pr_agent",
        agent_name="ReleaseGuard PR Agent",
        task_name="Analyze NovaShop PR #42",
        planned_steps=[{"step_key": "fetch", "step_name": "Fetch PR"}],
    )
    await AgentExecutionService.transition_execution(
        db_session, exe, AgentExecutionStatus.INITIALIZING
    )
    await AgentExecutionService.transition_execution(
        db_session, exe, AgentExecutionStatus.RUNNING
    )

    step = await AgentExecutionService.start_step(
        db_session, exe, step_key="fetch", step_name="Fetch PR"
    )
    tc = await AgentExecutionService.record_tool_start(
        db_session,
        exe,
        tool_name="github.get_pull_request",
        tool_category="github",
        step_id=step.id,
        input_summary={"token": "ghp_secret1234567890", "pr_number": 42},
    )
    assert exe.status == AgentExecutionStatus.TOOL_RUNNING
    assert "ghp_secret1234567890" not in str(tc.input_summary)

    # Request pause while tool is running -> must wait at PAUSE_REQUESTED
    await AgentExecutionService.request_pause(db_session, exe, reason="Hold for operator")
    assert exe.status == AgentExecutionStatus.PAUSE_REQUESTED
    assert exe.pause_requested is True

    # Completing tool hits safe checkpoint -> transitions to PAUSED + creates snapshot
    await AgentExecutionService.record_tool_result(
        db_session,
        exe,
        tc,
        success=True,
        output_summary={"api_key": "sk-secret", "files": 12},
    )
    assert exe.status == AgentExecutionStatus.PAUSED
    assert tc.status == AgentToolCallStatus.COMPLETED
    assert "sk-secret" not in str(tc.output_summary)

    # Resume -> RUNNING
    await AgentExecutionService.resume_execution(db_session, exe)
    assert exe.status == AgentExecutionStatus.RUNNING

    # Complete and verify terminal immutability
    await AgentExecutionService.complete_step(db_session, exe, step)
    await AgentExecutionService.transition_execution(
        db_session, exe, AgentExecutionStatus.COMPLETED
    )
    assert exe.status == AgentExecutionStatus.COMPLETED

    with pytest.raises(AgentExecutionControlError):
        await AgentExecutionService.resume_execution(db_session, exe)


@pytest.mark.asyncio
async def test_non_retryable_auth_error_and_startup_recovery(db_session) -> None:
    exe = await AgentExecutionService.create_execution(
        db_session,
        agent_type="flag_agent",
        agent_name="ReleaseGuard Flag Agent",
        task_name="Configure Flagsmith flag",
    )
    await AgentExecutionService.transition_execution(
        db_session, exe, AgentExecutionStatus.RUNNING
    )
    await AgentExecutionService.transition_execution(
        db_session,
        exe,
        AgentExecutionStatus.FAILED,
        error_code="AUTH_FORBIDDEN",
        error_message="HTTP 403 Forbidden",
    )
    with pytest.raises(AgentExecutionControlError) as exc_info:
        await AgentExecutionService.retry_execution(db_session, exe)
    assert exc_info.value.code == "execution_non_retryable"

    # Startup recovery on active in-flight execution
    active_exe = await AgentExecutionService.create_execution(
        db_session,
        agent_type="experiment_agent",
        agent_name="ReleaseGuard Experiment Agent",
        task_name="Evaluate checkout_v2",
    )
    await AgentExecutionService.transition_execution(
        db_session, active_exe, AgentExecutionStatus.RUNNING
    )
    await db_session.commit()

    recovered = await AgentExecutionService.recover_interrupted_executions(db_session)
    assert recovered == 1
    await db_session.refresh(active_exe)
    assert active_exe.status == AgentExecutionStatus.INTERRUPTED

    await AgentExecutionService.retry_execution(db_session, active_exe)
    assert active_exe.status == AgentExecutionStatus.RUNNING


@pytest.mark.asyncio
async def test_decisions_and_command_center_api_e2e(async_engine, session_factory, db_session) -> None:
    async with session_factory() as boot_sess:
        await bootstrap_workspace_defaults(boot_sess, bootstrap_env_tokens=False)
        await boot_sess.commit()

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ov_resp = await client.get("/api/v1/agent-executions/overview")
        assert ov_resp.status_code == 200

        create_resp = await client.post(
            "/api/v1/agent-executions",
            json={
                "agent_type": "pr_agent",
                "task_name": "Analyze NovaShop checkout_v2 PR",
                "start_immediately": True,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        data = create_resp.json()
        exe_id = data["execution"]["id"]
        ver = data["execution"]["version"]
        assert data["execution"]["status"] == "running"

        # Record a decision directly in DB to verify decision inspector API
        async with session_factory() as sess:
            from app.models import AgentExecution

            exe_obj = await sess.get(AgentExecution, uuid.UUID(exe_id))
            assert exe_obj is not None
            await AgentExecutionService.record_decision(
                sess,
                exe_obj,
                decision_type="risk_assessment",
                decision_source=AgentDecisionSource.AI_RECOMMENDATION,
                decision_summary="Payment validation modified; high risk.",
                recommendation="Wrap with checkout_v2 flag.",
                decision_status=AgentDecisionStatus.PENDING,
                supporting_evidence=["payment.py changed"],
            )
            await sess.commit()

        # Pause -> Resume -> Cancel with version check
        pause_resp = await client.post(
            f"/api/v1/agent-executions/{exe_id}/pause",
            json={"expected_version": ver, "reason": "Inspecting evidence"},
        )
        assert pause_resp.status_code == 200
        assert pause_resp.json()["execution"]["status"] == "paused"

        # Stale version conflict -> 409
        stale_resp = await client.post(
            f"/api/v1/agent-executions/{exe_id}/resume",
            json={"expected_version": ver},
        )
        assert stale_resp.status_code == 409

        ver2 = pause_resp.json()["execution"]["version"]
        resume_resp = await client.post(
            f"/api/v1/agent-executions/{exe_id}/resume",
            json={"expected_version": ver2},
        )
        assert resume_resp.status_code == 200
        assert resume_resp.json()["execution"]["status"] == "running"

        ver3 = resume_resp.json()["execution"]["version"]
        cancel_resp = await client.post(
            f"/api/v1/agent-executions/{exe_id}/cancel",
            json={"expected_version": ver3, "reason": "Operator stopped"},
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["execution"]["status"] == "cancelled"

        dec_resp = await client.get(f"/api/v1/agent-executions/{exe_id}/decisions")
        assert dec_resp.status_code == 200
        assert len(dec_resp.json()) == 1
        assert dec_resp.json()[0]["decision_source"] == "ai_recommendation"

        tl_resp = await client.get(f"/api/v1/agent-executions/{exe_id}/timeline")
        assert tl_resp.status_code == 200
        assert len(tl_resp.json()) >= 4

    # Unused User import check
    assert User is not None and UserRole.ADMIN is not None
