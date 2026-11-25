"""Comprehensive tests for the Progressive Delivery Engine (Phase 4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.v1.progressive_delivery import _build_detail
from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    Approval,
    ApprovalStatus,
    Experiment,
    ExperimentEvent,
    ExperimentEventType,
    ExperimentStatus,
    FeatureFlag,
    FlagType,
    GuardrailEvalStatus,
    ProgressiveRolloutStage,
    ProgressiveRolloutStatus,
    ProgressiveStageStatus,
    RollbackOperationStatus,
    RolloutDecisionType,
    RolloutHistory,
    User,
    UserRole,
)
from app.services.progressive_delivery import (
    DEFAULT_STAGE_PERCENTAGES,
    ProgressiveDeliveryError,
    ProgressiveDeliveryService,
)


async def _seed_flag_and_experiment(db_session, *, env: str = "development"):
    user = User(
        email=f"operator-{env}@releaseguard.test",
        display_name="Test Operator",
        role=UserRole.ADMIN,
        password_hash="not-used-in-test",
    )
    db_session.add(user)
    await db_session.flush()

    flag = FeatureFlag(
        key="checkout_v2_progressive",
        flagsmith_project_id="test-proj-1",
        description="Progressive checkout v2 flag",
        flag_type=FlagType.MULTIVARIATE,
        env_states={env: {"enabled": True, "percentage_split": 0}},
    )
    db_session.add(flag)
    await db_session.flush()

    exp = Experiment(
        name="Checkout v2 Progressive Exp",
        hypothesis="Controlled progressive rollout improves checkout conversion without increasing payment failures.",
        feature_flag_id=flag.id,
        environment_name=env,
        status=ExperimentStatus.RUNNING,
    )
    db_session.add(exp)
    await db_session.flush()
    return flag, exp, user.id


async def _seed_events(
    db_session,
    *,
    exp: Experiment,
    flag_key: str,
    completed: int,
    failed: int,
    abandoned: int = 0,
):
    now = datetime.now(UTC)
    for i in range(completed):
        db_session.add(
            ExperimentEvent(
                event_id=f"ok-{uuid.uuid4()}",
                experiment_id=exp.id,
                flag_key=flag_key,
                environment_name=exp.environment_name,
                variation_key="treatment",
                event_type=ExperimentEventType.CHECKOUT_COMPLETED,
                user_identifier=f"user-ok-{i}",
                properties={"latency_ms": 140 + (i % 30)},
                occurred_at=now,
            )
        )
    for i in range(failed):
        db_session.add(
            ExperimentEvent(
                event_id=f"fail-{uuid.uuid4()}",
                experiment_id=exp.id,
                flag_key=flag_key,
                environment_name=exp.environment_name,
                variation_key="treatment",
                event_type=ExperimentEventType.PAYMENT_FAILED,
                user_identifier=f"user-fail-{i}",
                properties={"latency_ms": 420},
                occurred_at=now,
            )
        )
    for i in range(abandoned):
        db_session.add(
            ExperimentEvent(
                event_id=f"ab-{uuid.uuid4()}",
                experiment_id=exp.id,
                flag_key=flag_key,
                environment_name=exp.environment_name,
                variation_key="treatment",
                event_type=ExperimentEventType.CHECKOUT_ABANDONED,
                user_identifier=f"user-ab-{i}",
                properties={},
                occurred_at=now,
            )
        )
    await db_session.flush()


@pytest.mark.asyncio
async def test_stage_sequence_validation_and_observation_gating(db_session) -> None:
    flag, exp, actor_id = await _seed_flag_and_experiment(db_session, env="development")

    # Invalid non-increasing stage sequence must be rejected
    with pytest.raises(ProgressiveDeliveryError) as exc_info:
        await ProgressiveDeliveryService.create_rollout(
            db_session,
            name="Bad Stages",
            feature_flag_id=flag.id,
            environment="development",
            stage_percentages=[10, 5, 100],
        )
    assert "strictly increasing" in str(exc_info.value)

    # Valid default 1 -> 5 -> 10 -> 25 -> 50 -> 100 rollout with fast test observation config
    rollout = await ProgressiveDeliveryService.create_rollout(
        db_session,
        name="Checkout v2 Controlled Rollout",
        feature_flag_id=flag.id,
        experiment_id=exp.id,
        environment="development",
        observation_config={
            "minimum_observation_seconds": 60,
            "maximum_observation_seconds": 300,
            "minimum_sample_size": 10,
            "maximum_metric_age_seconds": 300,
            "required_healthy_evaluations": 1,
        },
        guardrails_spec=[
            {
                "metric_key": "payment_failed",
                "operator": "greater_than",
                "threshold": 0.05,
                "severity": "critical",
                "minimum_sample_size": 10,
                "consecutive_breach_count": 1,
                "action": "recommend_rollback",
            }
        ],
    )
    detail = await _build_detail(db_session, rollout)
    assert [s.target_percentage for s in detail.stages] == DEFAULT_STAGE_PERCENTAGES

    # Start rollout -> activates stage 1 (1%)
    started_rollout = await ProgressiveDeliveryService.start_rollout(
        db_session, rollout, actor_id=actor_id
    )
    started = await _build_detail(db_session, started_rollout)
    assert started.rollout.status == ProgressiveRolloutStatus.OBSERVING
    assert started.rollout.current_percentage == 1
    stage1 = started.stages[0]
    assert stage1.status == ProgressiveStageStatus.OBSERVING

    # Evaluate with zero samples -> must NOT advance; must emit CONTINUE_OBSERVATION
    decision_empty = await ProgressiveDeliveryService.evaluate_stage(
        db_session, started_rollout, stage1
    )
    assert decision_empty.decision_type == RolloutDecisionType.CONTINUE_OBSERVATION
    assert len(decision_empty.missing_evidence) > 0

    # Add 20 healthy events, but stage observation window (60s) hasn't elapsed yet
    await _seed_events(db_session, exp=exp, flag_key=flag.key, completed=20, failed=0)
    decision_early = await ProgressiveDeliveryService.evaluate_stage(
        db_session, started_rollout, stage1
    )
    assert decision_early.decision_type == RolloutDecisionType.CONTINUE_OBSERVATION

    # Backdate observation_started_at on the ORM row to simulate elapsed observation window (>= 900s)
    stage1_orm = await db_session.get(ProgressiveRolloutStage, stage1.id)
    assert stage1_orm is not None
    stage1_orm.observation_started_at = datetime.now(UTC) - timedelta(seconds=1000)
    await db_session.flush()

    decision_ready = await ProgressiveDeliveryService.evaluate_stage(
        db_session, started_rollout, stage1_orm
    )
    assert decision_ready.decision_type == RolloutDecisionType.ADVANCE_RECOMMENDED, (
        f"summary={decision_ready.decision_summary} missing={decision_ready.missing_evidence}"
    )

    # Advance from 1% -> 5%
    advanced_rollout = await ProgressiveDeliveryService.advance_stage(
        db_session, started_rollout, actor_id=actor_id
    )
    advanced = await _build_detail(db_session, advanced_rollout)
    assert advanced.rollout.current_percentage == 5
    assert advanced.stages[0].status == ProgressiveStageStatus.PASSED
    assert advanced.stages[1].status == ProgressiveStageStatus.OBSERVING


@pytest.mark.asyncio
async def test_guardrail_breach_triggers_rollback_recommendation_and_nonprod_rollback(
    db_session,
) -> None:
    flag, exp, actor_id = await _seed_flag_and_experiment(db_session, env="staging")
    rollout = await ProgressiveDeliveryService.create_rollout(
        db_session,
        name="Staging Guardrail Breach Rollout",
        feature_flag_id=flag.id,
        experiment_id=exp.id,
        environment="staging",
        stage_percentages=[1, 5, 100],
        observation_config={
            "minimum_observation_seconds": 0,
            "minimum_sample_size": 10,
            "maximum_metric_age_seconds": 300,
            "required_healthy_evaluations": 1,
        },
        guardrails_spec=[
            {
                "metric_key": "payment_failed",
                "operator": "greater_than",
                "threshold": 0.05,
                "severity": "critical",
                "minimum_sample_size": 10,
                "consecutive_breach_count": 1,
                "action": "recommend_rollback",
            }
        ],
    )
    actor_id_unused = actor_id
    _ = actor_id_unused
    started_rollout = await ProgressiveDeliveryService.start_rollout(
        db_session, rollout, actor_id=actor_id
    )
    started = await _build_detail(db_session, started_rollout)
    stage1 = started.stages[0]

    # Inject 40% failure rate (8 failed / 20 total) -> breaches > 0.05 threshold
    await _seed_events(db_session, exp=exp, flag_key=flag.key, completed=12, failed=8)
    decision = await ProgressiveDeliveryService.evaluate_stage(
        db_session, started_rollout, stage1
    )
    assert decision.decision_type == RolloutDecisionType.ROLLBACK_RECOMMENDED

    after_breach = await _build_detail(db_session, started_rollout)
    assert after_breach.rollout.status == ProgressiveRolloutStatus.ROLLBACK_RECOMMENDED
    assert any(ev.status == GuardrailEvalStatus.BREACH for ev in after_breach.evaluations)
    assert len(after_breach.rollbacks) == 1

    # Execute non-prod rollback -> resets flag percentage to 0 & records RolloutHistory
    rb_op = await ProgressiveDeliveryService.execute_rollback(
        db_session,
        started_rollout,
        actor_id=actor_id,
        reason="Critical payment_failed breach",
    )
    assert rb_op.status == RollbackOperationStatus.COMPLETED

    final_detail = await _build_detail(db_session, started_rollout)
    assert final_detail.rollout.status == ProgressiveRolloutStatus.ROLLED_BACK
    assert final_detail.rollout.current_percentage == 0
    assert flag.env_states["staging"]["percentage_split"] == 0

    histories = list(
        (await db_session.execute(select(RolloutHistory).where(RolloutHistory.feature_flag_id == flag.id)))
        .scalars()
        .all()
    )
    assert len(histories) >= 2


@pytest.mark.asyncio
async def test_production_approval_gate_and_rest_api(
    async_engine, session_factory, db_session
) -> None:
    flag, exp, _ = await _seed_flag_and_experiment(db_session, env="production")
    await db_session.commit()

    async with session_factory() as s:
        await bootstrap_workspace_defaults(s, bootstrap_env_tokens=False)
        await s.commit()

    app = create_app(engine=async_engine, session_factory=session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_res = await client.post(
            "/api/v1/rollouts",
            json={
                "name": "Prod Checkout Rollout",
                "feature_flag_id": str(flag.id),
                "experiment_id": str(exp.id),
                "environment": "production",
            },
        )
        assert create_res.status_code == 201
        rollout_id = create_res.json()["rollout"]["id"]

        # Starting in production without approval creates Category C Approval & AWAITING_APPROVAL
        start_res = await client.post(f"/api/v1/rollouts/{rollout_id}/start", json={})
        assert start_res.status_code == 200
        assert start_res.json()["rollout"]["status"] == "awaiting_approval"

        # Unauthorized production rollback must return 403
        rb_res = await client.post(
            f"/api/v1/rollouts/{rollout_id}/rollback/execute",
            json={"reason": "Test unauthorized rollback"},
        )
        assert rb_res.status_code == 403

        # List rollouts returns the created rollout
        list_res = await client.get("/api/v1/rollouts?environment=production")
        assert list_res.status_code == 200
        assert list_res.json()["total"] >= 1

    async with session_factory() as s:
        approvals = list(
            (
                await s.execute(
                    select(Approval).where(
                        Approval.environment_name == "production",
                        Approval.status == ApprovalStatus.PENDING,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(approvals) >= 1
