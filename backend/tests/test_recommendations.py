"""Standalone tests for backend/app/analytics/recommendations.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.analytics.bayesian import GuardrailThresholds, run_bayesian_analysis
from app.analytics.recommendations import (
    RecommendationStatus,
    evaluate_recommendation,
)


@pytest.fixture
def default_thresholds() -> GuardrailThresholds:
    return GuardrailThresholds(
        min_exposures=100,
        max_expected_loss=0.005,
        guardrail_max_error_rate=0.05,
        soft_error_delta=0.015,
        hard_error_delta=0.030,
    )


def test_priority_1_recommend_rollback_on_max_error_rate_even_below_min_exposures(
    default_thresholds: GuardrailThresholds,
) -> None:
    # n_t = 35 (< min_exposures 100, >= 30), treatment errors = 10 -> ~29% error rate
    analysis = run_bayesian_analysis(
        control_exposures=40,
        control_conversions=5,
        treatment_exposures=35,
        treatment_conversions=8,
        control_errors=0,
        treatment_errors=10,
        mc_samples=10_000,
        seed=42,
        thresholds=default_thresholds,
    )
    rec = evaluate_recommendation(analysis, environment_name="production", current_rollout_percentage=25)
    assert rec.recommendation == RecommendationStatus.RECOMMEND_ROLLBACK
    assert rec.priority_rank == 1
    assert rec.suggested_action == "rollback"
    assert rec.requires_prod_approval is True
    assert any(t.metric == "prob_treatment_exceeds_max_error_rate" and t.met for t in rec.triggers)
    assert rec.preconditions
    assert rec.potential_consequences
    assert "P(Treatment > Control)" in rec.confidence_summary


def test_priority_1_recommend_rollback_on_severe_conversion_loss(
    default_thresholds: GuardrailThresholds,
) -> None:
    # Both >= 100 exposures, control 25% vs treatment 5%, guardrails clean
    analysis = run_bayesian_analysis(
        control_exposures=150,
        control_conversions=38,
        treatment_exposures=150,
        treatment_conversions=8,
        control_errors=1,
        treatment_errors=1,
        mc_samples=15_000,
        seed=42,
        thresholds=default_thresholds,
    )
    rec = evaluate_recommendation(analysis, environment_name="production")
    assert rec.recommendation == RecommendationStatus.RECOMMEND_ROLLBACK
    assert rec.priority_rank == 1
    assert any(t.metric == "prob_treatment_beats_control" and t.met for t in rec.triggers)
    assert any(t.metric == "expected_loss_choose_control" and t.met for t in rec.triggers)


def test_priority_2_reduce_rollout_on_soft_error_delta(
    default_thresholds: GuardrailThresholds,
) -> None:
    # n_t = 60 (< 100, >= 50): soft delta warning triggers Reduce Rollout before Continue Monitoring
    # Control ~1% error, treatment 4/60 = 6.7% (posterior mean ~7.8%, wait: keep max_error_rate higher
    # so Priority 1 max_error_rate doesn't fire first)
    thresholds = default_thresholds.model_copy(
        update={"guardrail_max_error_rate": 0.15, "hard_error_delta": 0.12}
    )
    analysis = run_bayesian_analysis(
        control_exposures=120,
        control_conversions=15,
        treatment_exposures=60,
        treatment_conversions=10,
        control_errors=1,
        treatment_errors=3,
        mc_samples=15_000,
        seed=42,
        thresholds=thresholds,
    )
    # Verify prob_soft >= 0.80 while prob_hard < 0.95
    assert analysis.guardrails.payment_failed.prob_treatment_exceeds_soft_delta >= 0.80
    assert analysis.guardrails.payment_failed.prob_treatment_exceeds_hard_delta < 0.95

    rec = evaluate_recommendation(analysis, thresholds=thresholds, environment_name="staging")
    assert rec.recommendation == RecommendationStatus.REDUCE_ROLLOUT
    assert rec.priority_rank == 2
    assert rec.suggested_action == "reduce"


def test_priority_2_reduce_rollout_on_weak_conversion(
    default_thresholds: GuardrailThresholds,
) -> None:
    # Adequate sample, P(T > C) < 0.25 but not < 0.05 (or loss_ctrl >= 0.001)
    analysis = run_bayesian_analysis(
        control_exposures=120,
        control_conversions=18,
        treatment_exposures=120,
        treatment_conversions=12,
        control_errors=1,
        treatment_errors=1,
        mc_samples=15_000,
        seed=42,
        thresholds=default_thresholds,
    )
    assert 0.05 <= analysis.comparison.prob_treatment_beats_control < 0.25
    rec = evaluate_recommendation(analysis)
    assert rec.recommendation == RecommendationStatus.REDUCE_ROLLOUT
    assert rec.priority_rank == 2


def test_priority_3_continue_monitoring_when_underpowered(
    default_thresholds: GuardrailThresholds,
) -> None:
    analysis = run_bayesian_analysis(
        control_exposures=60,
        control_conversions=7,
        treatment_exposures=60,
        treatment_conversions=14,
        control_errors=1,
        treatment_errors=1,
        mc_samples=10_000,
        seed=42,
        thresholds=default_thresholds,
    )
    rec = evaluate_recommendation(analysis)
    assert rec.recommendation == RecommendationStatus.CONTINUE_MONITORING
    assert rec.priority_rank == 3
    assert rec.suggested_action == "monitor"
    assert rec.requires_prod_approval is False


def test_priority_4_expand_rollout_when_confident_and_safe(
    default_thresholds: GuardrailThresholds,
) -> None:
    analysis = run_bayesian_analysis(
        control_exposures=240,
        control_conversions=29,
        treatment_exposures=245,
        treatment_conversions=44,
        control_errors=3,
        treatment_errors=4,
        mc_samples=20_000,
        seed=42,
        thresholds=default_thresholds,
    )
    rec = evaluate_recommendation(analysis, environment_name="production", current_rollout_percentage=25)
    assert rec.recommendation == RecommendationStatus.EXPAND_ROLLOUT
    assert rec.priority_rank == 4
    assert rec.suggested_action == "expand"
    assert rec.requires_prod_approval is True


def test_priority_5_maintain_current_rollout_when_inconclusive(
    default_thresholds: GuardrailThresholds,
) -> None:
    analysis = run_bayesian_analysis(
        control_exposures=150,
        control_conversions=20,
        treatment_exposures=150,
        treatment_conversions=22,
        control_errors=2,
        treatment_errors=2,
        mc_samples=15_000,
        seed=42,
        thresholds=default_thresholds,
    )
    rec = evaluate_recommendation(analysis)
    assert rec.recommendation == RecommendationStatus.MAINTAIN_CURRENT_ROLLOUT
    assert rec.priority_rank == 5
    assert rec.suggested_action == "maintain"
    assert rec.requires_prod_approval is False


def test_priority_1_hard_error_delta_overrides_high_conversion_lift(
    default_thresholds: GuardrailThresholds,
) -> None:
    # Even with huge conversion lift, hard error delta breach (7.5% vs 0% with higher ceiling)
    # triggers Rollback before Expand Rollout
    thresholds = default_thresholds.model_copy(update={"guardrail_max_error_rate": 0.20})
    analysis = run_bayesian_analysis(
        control_exposures=200,
        control_conversions=20,
        treatment_exposures=200,
        treatment_conversions=60,
        control_errors=0,
        treatment_errors=15,
        mc_samples=15_000,
        seed=42,
        thresholds=thresholds,
    )
    assert analysis.comparison.prob_treatment_beats_control > 0.99
    assert analysis.guardrails.payment_failed.prob_treatment_exceeds_hard_delta >= 0.95
    rec = evaluate_recommendation(analysis, thresholds=thresholds, environment_name="production")
    assert rec.recommendation == RecommendationStatus.RECOMMEND_ROLLBACK
    assert rec.priority_rank == 1
