"""Standalone tests for backend/app/analytics/bayesian.py."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.analytics.bayesian import (
    BayesianAnalysisResult,
    GuardrailThresholds,
    aggregate_experiment_events,
    analyze_experiment_events,
    run_bayesian_analysis,
)


@pytest.fixture
def winning_treatment_counts() -> dict[str, int]:
    return {
        "control_exposures": 240,
        "control_conversions": 29,
        "treatment_exposures": 245,
        "treatment_conversions": 44,
        "control_errors": 3,
        "treatment_errors": 4,
        "control_abandoned": 48,
        "treatment_abandoned": 39,
    }


def test_bayesian_posterior_parameters_and_schema(
    winning_treatment_counts: dict[str, int],
) -> None:
    result = run_bayesian_analysis(
        **winning_treatment_counts,
        experiment_id="exp-001",
        analytics_mode="tracked_event_fallback",
        mc_samples=20_000,
        seed=42,
    )
    assert isinstance(result, BayesianAnalysisResult)
    assert result.experiment_id == "exp-001"
    assert result.analytics_mode == "tracked_event_fallback"

    # Prior (1.0, 1.0) -> posterior alpha = 1 + k, beta = 1 + (n - k)
    assert result.control.posterior_alpha == 30.0
    assert result.control.posterior_beta == 212.0
    assert result.treatment.posterior_alpha == 45.0
    assert result.treatment.posterior_beta == 202.0
    assert result.control.mean_rate == pytest.approx(30.0 / 242.0, abs=1e-9)
    assert result.treatment.mean_rate == pytest.approx(45.0 / 247.0, abs=1e-9)

    assert result.sample_adequacy.is_adequate is True
    assert result.sample_adequacy.min_required == 100

    # 95% Credible intervals ordered around posterior mean
    c_lo, c_hi = result.control.credible_interval_95
    t_lo, t_hi = result.treatment.credible_interval_95
    assert 0.0 < c_lo < result.control.mean_rate < c_hi < 1.0
    assert 0.0 < t_lo < result.treatment.mean_rate < t_hi < 1.0

    # Treatment is clearly winning
    assert result.comparison.prob_treatment_beats_control > 0.94
    assert result.comparison.absolute_lift_mean > 0.05
    assert result.comparison.relative_lift_mean > 0.40
    assert result.comparison.expected_loss_choose_treatment < 0.001
    assert result.comparison.expected_loss_choose_control > 0.05

    # Expected loss identity: E[max(T-C,0)] - E[max(C-T,0)] == E[T-C]
    net_diff = (
        result.comparison.expected_loss_choose_control
        - result.comparison.expected_loss_choose_treatment
    )
    assert net_diff == pytest.approx(result.comparison.absolute_lift_mean, abs=1e-9)

    # Guardrails match docs/api.md Sec 2.7
    pf = result.guardrails.payment_failed
    assert pf.control_errors == 3
    assert pf.control_rate == pytest.approx(3 / 240, abs=1e-9)
    assert pf.treatment_errors == 4
    assert pf.treatment_rate == pytest.approx(4 / 245, abs=1e-9)
    assert pf.breached is False
    assert pf.breach_severity == "none"
    assert result.guardrails.checkout_abandoned.control_count == 48
    assert result.guardrails.checkout_abandoned.treatment_count == 39


def test_bayesian_deterministic_reproducibility() -> None:
    kwargs = {
        "control_exposures": 150,
        "control_conversions": 18,
        "treatment_exposures": 160,
        "treatment_conversions": 28,
        "control_errors": 2,
        "treatment_errors": 6,
        "mc_samples": 10_000,
        "seed": 12345,
    }
    run_a = run_bayesian_analysis(**kwargs)
    run_b = run_bayesian_analysis(**kwargs)
    assert run_a.model_dump() == run_b.model_dump()


def test_bayesian_default_50k_mc_samples() -> None:
    result = run_bayesian_analysis(
        control_exposures=240,
        control_conversions=29,
        treatment_exposures=245,
        treatment_conversions=44,
        control_errors=3,
        treatment_errors=4,
    )
    assert result.mc_samples == 50_000
    assert result.seed == 42
    assert 0.90 < result.comparison.prob_treatment_beats_control < 0.99


def test_bayesian_binomial_bounds_validation() -> None:
    with pytest.raises(ValueError, match="control_conversions"):
        run_bayesian_analysis(
            control_exposures=10,
            control_conversions=11,
            treatment_exposures=10,
            treatment_conversions=5,
        )

    with pytest.raises(ValueError, match="treatment_conversions"):
        run_bayesian_analysis(
            control_exposures=10,
            control_conversions=5,
            treatment_exposures=10,
            treatment_conversions=12,
        )

    with pytest.raises(ValueError, match="treatment_errors"):
        run_bayesian_analysis(
            control_exposures=10,
            control_conversions=5,
            treatment_exposures=10,
            treatment_conversions=5,
            treatment_errors=11,
        )


def test_bayesian_zero_exposures_and_guardrail_breach() -> None:
    zero_res = run_bayesian_analysis(
        control_exposures=0,
        control_conversions=0,
        treatment_exposures=0,
        treatment_conversions=0,
        mc_samples=2_000,
        seed=7,
    )
    assert zero_res.control.mean_rate == pytest.approx(0.5)
    assert zero_res.treatment.mean_rate == pytest.approx(0.5)
    assert zero_res.guardrails.payment_failed.control_rate == 0.0
    assert zero_res.guardrails.payment_failed.treatment_rate == 0.0
    assert zero_res.sample_adequacy.is_adequate is False

    # Hard guardrail breach (treatment error rate 20% vs control 1%)
    breach_res = run_bayesian_analysis(
        control_exposures=120,
        control_conversions=15,
        treatment_exposures=120,
        treatment_conversions=20,
        control_errors=1,
        treatment_errors=24,
        mc_samples=10_000,
        seed=42,
    )
    pf = breach_res.guardrails.payment_failed
    assert pf.prob_treatment_exceeds_max_error_rate > 0.99
    assert pf.prob_treatment_exceeds_hard_delta > 0.99
    assert pf.prob_treatment_exceeds_soft_delta > 0.99
    assert pf.breached is True
    assert pf.breach_severity == "hard"


def test_aggregate_and_analyze_raw_events_deduplicates_and_bounds() -> None:
    base_time = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    events = [
        # Duplicate event_id should be deduplicated
        {
            "event_id": "evt-1",
            "variation_key": "v1",
            "event_type": "feature_variation_assigned",
            "user_identifier": "u1",
            "occurred_at": base_time,
        },
        {
            "event_id": "evt-1",
            "variation_key": "v1",
            "event_type": "feature_variation_assigned",
            "user_identifier": "u1",
            "occurred_at": base_time,
        },
        # Repeated conversions for same exposed user u1 should count once
        {
            "event_id": "evt-2",
            "variation_key": "v1",
            "event_type": "checkout_completed",
            "user_identifier": "u1",
            "occurred_at": base_time + timedelta(seconds=10),
        },
        {
            "event_id": "evt-3",
            "variation_key": "v1",
            "event_type": "checkout_completed",
            "user_identifier": "u1",
            "occurred_at": base_time + timedelta(seconds=20),
        },
        # Unexposed user conversion should NOT create k > n
        {
            "event_id": "evt-4",
            "variation_key": "v2",
            "event_type": "checkout_completed",
            "user_identifier": "ghost_user",
            "occurred_at": base_time + timedelta(seconds=30),
        },
        # Exposed treatment user u2 with payment_failed
        {
            "event_id": "evt-5",
            "variation_key": "v2",
            "event_type": "feature_variation_assigned",
            "user_identifier": "u2",
            "occurred_at": base_time,
        },
        {
            "event_id": "evt-6",
            "variation_key": "v2",
            "event_type": "payment_failed",
            "user_identifier": "u2",
            "occurred_at": base_time + timedelta(seconds=15),
        },
    ]

    counts = aggregate_experiment_events(
        events,
        control_variation="v1",
        treatment_variation="v2",
    )
    assert counts.control_exposures == 1
    assert counts.control_conversions == 1
    assert counts.treatment_exposures == 1
    assert counts.treatment_conversions == 0
    assert counts.treatment_errors == 1
    assert counts.deduplicated_events == 1

    analysis = analyze_experiment_events(
        events,
        experiment_id="exp-raw",
        control_variation="v1",
        treatment_variation="v2",
        mc_samples=2_000,
        thresholds=GuardrailThresholds(min_exposures=1),
    )
    assert analysis.sample_adequacy.is_adequate is True
    assert analysis.control.conversions == 1
    assert analysis.guardrails.payment_failed.treatment_errors == 1
