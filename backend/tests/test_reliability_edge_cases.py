"""Regression tests for reliability, concurrency, SSRF safety, SSE pruning, and error envelopes."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.analytics.bayesian import analyze_experiment_events
from app.analytics.recommendations import evaluate_recommendation
from app.main import create_app
from app.models import IntegrationProvider, PolicyCategory
from app.services.dx.github_dx import DXIntegrationError, GitHubWebhookService
from app.services.dx.reports_notifications import DeveloperNotificationService
from app.services.policy_engine import PolicyEngine, classify_action
from app.services.sse_bus import SSEBus


@pytest.mark.asyncio
async def test_policy_classify_winner_promotion_and_unknown_denial() -> None:
    """Winner promotion is always Category C; unknown actions fail closed to DENY."""
    assert classify_action("promote_experiment_winner", "development") == PolicyCategory.C
    assert classify_action("experiment_promote_winner", "production") == PolicyCategory.C
    assert classify_action("release_rollout_approve", "production") == PolicyCategory.C
    assert classify_action("arbitrary_unregistered_action", "production") == PolicyCategory.DENY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "bad_url"),
    [
        (IntegrationProvider.SLACK, "http://hooks.slack.com/services/T0/B0/X"),
        (IntegrationProvider.SLACK, "https://localhost/hooks/abc"),
        (IntegrationProvider.SLACK, "https://127.0.0.1/services/T0"),
        (IntegrationProvider.SLACK, "https://169.254.169.254/latest/meta-data"),
        (IntegrationProvider.SLACK, "https://evil.example.com/services/T0"),
        (IntegrationProvider.DISCORD, "https://10.0.0.5/api/webhooks/1"),
        (IntegrationProvider.DISCORD, "https://evil.example.com/api/webhooks/1"),
    ],
)
async def test_ssrf_webhook_validation_blocks_unsafe_targets(
    db_session,
    provider: IntegrationProvider,
    bad_url: str,
) -> None:
    """Reject HTTP, private/loopback/link-local IPs, and non-provider domains."""
    with pytest.raises(DXIntegrationError):
        await DeveloperNotificationService.connect_provider(
            db_session,
            provider=provider,
            webhook_url=bad_url,
            channel_label="#alerts",
        )


@pytest.mark.asyncio
async def test_concurrent_duplicate_webhook_delivery_idempotency(
    async_engine,
    session_factory,
) -> None:
    """Concurrent deliveries with identical delivery_id do not raise unhandled IntegrityError."""
    payload = {"action": "opened", "pull_request": {"number": 7, "title": "Safe checkout"}}
    raw = json.dumps(payload).encode("utf-8")
    secret = "whsec_concurrent_test"
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    async def deliver() -> uuid.UUID:
        async with session_factory() as s:
            ev = await GitHubWebhookService.receive_webhook(
                s,
                delivery_id="deliv-concurrent-001",
                event_type="pull_request",
                raw_body=raw,
                payload=payload,
                signature_header=sig,
                webhook_secret=secret,
            )
            await s.commit()
            return ev.id

    ids = await asyncio.gather(deliver(), deliver())
    assert ids[0] == ids[1]


@pytest.mark.asyncio
async def test_sse_bus_lock_pruning_and_workspace_secret_redaction() -> None:
    """SSEBus cleans up idle task locks and redacts tokens in workspace broadcasts."""
    bus = SSEBus()
    task_id = uuid.uuid4()

    async def consume_once() -> None:
        async for _ev in bus.subscribe(task_id, heartbeat_seconds=0.5, stop_on_terminal=True):
            pass

    consumer = asyncio.create_task(consume_once())
    await asyncio.sleep(0.05)
    await bus.publish(task_id, "agent.completed", {"token": "ghp_secretvalue123"})
    await consumer

    assert str(task_id) not in bus._subscribers
    assert str(task_id) not in bus._locks

    # Concurrent workspace publishes redact secrets and stay ordered
    ws_events = await asyncio.gather(
        *[
            bus.publish_workspace_event("test.evt", {"api_key": "sk-secret-token-xyz", "idx": i})
            for i in range(10)
        ]
    )
    assert len({e["seq"] for e in ws_events}) == 10
    assert "sk-secret" not in json.dumps(ws_events)


@pytest.mark.asyncio
async def test_health_probes_and_error_envelope_contract(async_engine, session_factory) -> None:
    """/health/live, /health/ready, and 422 validation envelope return retryable + timestamp."""
    app = create_app(engine=async_engine, session_factory=session_factory, bootstrap_env_tokens=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/health/live")
        assert live.status_code == 200
        assert live.json()["status"] == "alive"

        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["database"] == "ok"

        # Invalid UUID hits FastAPI RequestValidationError -> structured envelope
        bad = await client.get("/api/v1/experiments/not-a-uuid/analysis")
        assert bad.status_code in (401, 422)
        body = bad.json()
        assert "error" in body
        assert "retryable" in body["error"]
        assert "timestamp" in body["error"]
        assert body["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_insufficient_experiment_data_does_not_invent_winner() -> None:
    """Insufficient telemetry must not emit EXPAND_ROLLOUT winner claim."""
    analysis = analyze_experiment_events(
        [],
        experiment_id=str(uuid.uuid4()),
        analytics_mode="tracked_event_fallback",
        control_variation="control",
        treatment_variation="treatment",
        primary_metric="checkout_completed",
        mc_samples=1000,
        seed=42,
        thresholds={"min_exposures": 30},
    )
    rec = evaluate_recommendation(analysis, thresholds={"min_exposures": 30}, environment_name="production")
    assert analysis.sample_adequacy.is_adequate is False
    assert rec.recommendation.value != "Expand Rollout"


@pytest.mark.asyncio
async def test_expired_approval_blocked_before_execution(db_session) -> None:
    """Expired approval cannot execute production mutations."""
    from datetime import timedelta
    from app.core.database import utcnow
    from app.core.security import compute_action_hash
    from app.models import Approval, ApprovalCategory, ApprovalStatus
    from app.services.policy_engine import ApprovalExpiredError

    payload = {"flag_key": "checkout_v2", "environment_name": "production"}
    app = Approval(
        policy_category=ApprovalCategory.C,
        action_type="flagsmith_rollback_flag",
        target_resource_type="feature_flag",
        target_resource_id="checkout_v2",
        environment_name="production",
        title="Expired rollback test",
        rationale="test",
        action_payload=payload,
        expected_precondition={},
        action_hash=compute_action_hash(
            "flagsmith_rollback_flag", "feature_flag", "checkout_v2", "production", payload
        ),
        idempotency_key=f"test-exp-{uuid.uuid4().hex[:8]}",
        status=ApprovalStatus.PENDING,
        expires_at=utcnow() - timedelta(minutes=5),
    )
    db_session.add(app)
    await db_session.commit()

    async def noop(*_a, **_kw):
        return {"ok": True}

    with pytest.raises(ApprovalExpiredError):
        await PolicyEngine.execute_approved_action(
            db_session,
            app.id,
            decided_by_id=None,
            revalidator=noop,
            executor=noop,
            post_verifier=noop,
        )
