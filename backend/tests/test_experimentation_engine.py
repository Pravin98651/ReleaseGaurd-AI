"""Comprehensive unit & integration tests for the Flagsmith Experimentation Engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport

from app.analytics.bayesian import (
    ExperimentOutcome,
    GuardrailThresholds,
    aggregate_experiment_events,
    analyze_experiment_events,
    run_bayesian_analysis,
)
from app.analytics.recommendations import (
    evaluate_experiment_readiness,
    map_flagsmith_native_results,
)
from app.main import bootstrap_workspace_defaults, create_app
from app.models import Experiment, ExperimentStatus, FeatureFlag, FlagType
from app.services.policy_engine import PolicyEngine


def _make_events(
    *,
    control_n: int,
    control_k: int,
    treatment_n: int,
    treatment_k: int,
    treatment_err: int = 0,
    duplicate_exposures_per_user: int = 1,
    extra_conversions_per_converter: int = 1,
) -> list[dict]:
    now = datetime.now(UTC)
    events: list[dict] = []
    for i in range(control_n):
        uid = f"u_ctrl_{i}"
        for d in range(duplicate_exposures_per_user):
            events.append(
                {
                    "event_id": f"exp_c_{i}_{d}",
                    "variation_key": "control",
                    "event_type": "feature_variation_assigned",
                    "user_identifier": uid,
                    "occurred_at": now - timedelta(minutes=10),
                }
            )
        if i < control_k:
            for c in range(extra_conversions_per_converter):
                events.append(
                    {
                        "event_id": f"conv_c_{i}_{c}",
                        "variation_key": "control",
                        "event_type": "checkout_completed",
                        "user_identifier": uid,
                        "occurred_at": now - timedelta(minutes=5),
                    }
                )
    for i in range(treatment_n):
        uid = f"u_trt_{i}"
        for d in range(duplicate_exposures_per_user):
            events.append(
                {
                    "event_id": f"exp_t_{i}_{d}",
                    "variation_key": "treatment",
                    "event_type": "feature_variation_assigned",
                    "user_identifier": uid,
                    "occurred_at": now - timedelta(minutes=10),
                }
            )
        if i < treatment_err:
            events.append(
                {
                    "event_id": f"err_t_{i}",
                    "variation_key": "treatment",
                    "event_type": "payment_failed",
                    "user_identifier": uid,
                    "occurred_at": now - timedelta(minutes=4),
                }
            )
        elif i < treatment_err + treatment_k:
            for c in range(extra_conversions_per_converter):
                events.append(
                    {
                        "event_id": f"conv_t_{i}_{c}",
                        "variation_key": "treatment",
                        "event_type": "checkout_completed",
                        "user_identifier": uid,
                        "occurred_at": now - timedelta(minutes=4),
                    }
                )
    return events


def test_participant_vs_exposure_vs_conversion_deduplication() -> None:
    events = _make_events(
        control_n=10,
        control_k=2,
        treatment_n=10,
        treatment_k=4,
        duplicate_exposures_per_user=3,
        extra_conversions_per_converter=2,
    )
    # Also inject exact duplicate event_id
    events.append(dict(events[0]))

    counts = aggregate_experiment_events(events)
    assert counts.control_exposures == 10
    assert counts.treatment_exposures == 10
    assert counts.control_raw_exposures == 30
    assert counts.treatment_raw_exposures == 30
    assert counts.control_conversions == 2
    assert counts.treatment_conversions == 4
    assert counts.control_total_conversions == 4
    assert counts.treatment_total_conversions == 8
    assert counts.deduplicated_events == 1


def test_bayesian_explicit_outcomes_and_frequentist_separation() -> None:
    insufficient = run_bayesian_analysis(
        control_exposures=25,
        control_conversions=3,
        treatment_exposures=25,
        treatment_conversions=8,
    )
    assert insufficient.experiment_outcome == ExperimentOutcome.INSUFFICIENT_DATA
    assert insufficient.sample_adequacy.is_adequate is False
    assert insufficient.frequentist is not None

    variant_wins = run_bayesian_analysis(
        control_exposures=150,
        control_conversions=15,
        treatment_exposures=150,
        treatment_conversions=42,
    )
    assert variant_wins.experiment_outcome == ExperimentOutcome.VARIANT_WINS
    assert variant_wins.comparison.prob_treatment_beats_control >= 0.95

    control_wins = run_bayesian_analysis(
        control_exposures=150,
        control_conversions=38,
        treatment_exposures=150,
        treatment_conversions=12,
    )
    assert control_wins.experiment_outcome == ExperimentOutcome.CONTROL_WINS

    no_clear = run_bayesian_analysis(
        control_exposures=150,
        control_conversions=20,
        treatment_exposures=150,
        treatment_conversions=21,
    )
    assert no_clear.experiment_outcome == ExperimentOutcome.NO_CLEAR_WINNER

    breached = run_bayesian_analysis(
        control_exposures=150,
        control_conversions=18,
        treatment_exposures=150,
        treatment_conversions=30,
        control_errors=1,
        treatment_errors=24,
        thresholds=GuardrailThresholds(guardrail_max_error_rate=0.05),
    )
    assert breached.experiment_outcome == ExperimentOutcome.GUARDRAIL_BREACHED
    assert breached.guardrails.payment_failed.breached is True


def test_readiness_and_native_staleness_detection() -> None:
    events = _make_events(control_n=120, control_k=15, treatment_n=120, treatment_k=36)
    analysis = analyze_experiment_events(events, experiment_id="exp-1")
    stale_native = map_flagsmith_native_results(
        {
            "available": True,
            "status_code": 200,
            "results": {
                "results": {
                    "is_final": False,
                    "as_of": (datetime.now(UTC) - timedelta(hours=48)).isoformat(),
                    "payload": {"metrics": [{"metric_id": 484, "variants": {}}]},
                }
            },
        }
    )
    assert stale_native["stale"] is True

    readiness = evaluate_experiment_readiness(
        analysis=analysis,
        experiment_status="running",
        analytics_mode="flagsmith_native",
        flagsmith_experiment_id="503",
        started_at=datetime.now(UTC) - timedelta(hours=1),
        latest_event_at=datetime.now(UTC),
        native_summary=stale_native,
    )
    assert readiness.is_ready_for_winner_decision is False
    assert any("stale" in r.lower() for r in readiness.blocking_reasons)


@pytest.mark.asyncio
async def test_promote_winner_blocked_on_guardrail_breach_and_insufficient_data(
    async_engine, session_factory
) -> None:
    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )
    async with session_factory() as db_session:
        await bootstrap_workspace_defaults(db_session, bootstrap_env_tokens=False)
        await PolicyEngine.get_workspace_settings(db_session)
        flag = FeatureFlag(
            key="promo_guard_flag",
            flag_type=FlagType.MULTIVARIATE,
            flagsmith_project_id=56055,
            flagsmith_feature_id=999,
            mv_options=["control", "treatment"],
            env_states={"development": {"enabled": True, "rollout_percentage": 100}},
        )
        db_session.add(flag)
        await db_session.flush()

        exp = Experiment(
            feature_flag_id=flag.id,
            name="Promo Guard Test",
            hypothesis="Verify promotion safety",
            environment_name="development",
            status=ExperimentStatus.RUNNING,
            control_variation="control",
            treatment_variation="treatment",
            primary_metric="checkout_completed",
            started_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db_session.add(exp)
        await db_session.commit()
        exp_id = exp.id
        flag_key = flag.key

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1) Insufficient data -> 409 EXPERIMENT_NOT_READY
        resp_unready = await client.post(
            f"/api/v1/experiments/{exp_id}/promote-winner?winning_variation=treatment"
        )
        assert resp_unready.status_code == 409
        assert resp_unready.json()["error"]["code"] == "EXPERIMENT_NOT_READY"

        # 2) Ingest guardrail-breached events -> 409 GUARDRAIL_BREACHED
        breach_events = _make_events(
            control_n=110, control_k=15, treatment_n=110, treatment_k=22, treatment_err=22
        )
        ingest_payload = {
            "events": [
                {
                    "event_id": ev["event_id"],
                    "flag_key": flag_key,
                    "environment_name": "development",
                    "variation_key": ev["variation_key"],
                    "event_type": ev["event_type"],
                    "user_identifier": ev["user_identifier"],
                    "properties": {},
                    "occurred_at": ev["occurred_at"].isoformat(),
                }
                for ev in breach_events
            ]
        }
        ing_resp = await client.post(
            "/api/v1/events/ingest",
            json=ingest_payload,
            headers={"X-Ingest-Key": "dev-ingest-key-change-me"},
        )
        assert ing_resp.status_code == 200

        resp_breach = await client.post(
            f"/api/v1/experiments/{exp_id}/promote-winner?winning_variation=treatment"
        )
        assert resp_breach.status_code == 409
        assert resp_breach.json()["error"]["code"] == "GUARDRAIL_BREACHED"
