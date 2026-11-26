"""Pure-Python Bayesian beta-binomial analytics and guardrail evaluation engine.

Completely decoupled from SQLAlchemy/ORM models and LLM logic. Uses Python's
standard library (`math`, `random.Random`) and Pydantic v2 models.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class GuardrailThresholds(BaseModel):
    """Unified guardrail and decision thresholds matching workspace/experiment schemas."""

    min_exposures: int = Field(default=100, ge=1)
    max_expected_loss: float = Field(default=0.005, ge=0.0, le=1.0)
    guardrail_max_error_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    soft_error_delta: float = Field(default=0.015, ge=0.0, le=1.0)
    hard_error_delta: float = Field(default=0.030, ge=0.0, le=1.0)


class BayesianCountsInput(BaseModel):
    """Validated aggregated counts for a two-arm Bayesian Beta-Binomial evaluation."""

    experiment_id: str = ""
    analytics_mode: str = "tracked_event_fallback"
    control_exposures: int = Field(ge=0)
    control_conversions: int = Field(ge=0)
    treatment_exposures: int = Field(ge=0)
    treatment_conversions: int = Field(ge=0)
    control_errors: int = Field(default=0, ge=0)
    treatment_errors: int = Field(default=0, ge=0)
    control_abandoned: int = Field(default=0, ge=0)
    treatment_abandoned: int = Field(default=0, ge=0)
    alpha_0: float = Field(default=1.0, gt=0.0)
    beta_0: float = Field(default=1.0, gt=0.0)
    mc_samples: int = Field(default=50_000, ge=100)
    seed: int = 42
    thresholds: GuardrailThresholds = Field(default_factory=GuardrailThresholds)

    @model_validator(mode="after")
    def validate_binomial_bounds(self) -> BayesianCountsInput:
        """Enforce 0 <= k <= n and 0 <= e <= n for both variants."""
        if self.control_conversions > self.control_exposures:
            raise ValueError(
                f"control_conversions ({self.control_conversions}) cannot exceed "
                f"control_exposures ({self.control_exposures})"
            )
        if self.treatment_conversions > self.treatment_exposures:
            raise ValueError(
                f"treatment_conversions ({self.treatment_conversions}) cannot exceed "
                f"treatment_exposures ({self.treatment_exposures})"
            )
        if self.control_errors > self.control_exposures:
            raise ValueError(
                f"control_errors ({self.control_errors}) cannot exceed "
                f"control_exposures ({self.control_exposures})"
            )
        if self.treatment_errors > self.treatment_exposures:
            raise ValueError(
                f"treatment_errors ({self.treatment_errors}) cannot exceed "
                f"treatment_exposures ({self.treatment_exposures})"
            )
        return self


class SampleAdequacy(BaseModel):
    """Sample size adequacy check against configured minimum exposures."""

    control_exposures: int
    treatment_exposures: int
    min_required: int
    is_adequate: bool


class VariantPosterior(BaseModel):
    """Beta-Binomial posterior summary for a single variant."""

    exposures: int
    conversions: int
    posterior_alpha: float
    posterior_beta: float
    mean_rate: float
    credible_interval_95: list[float]


class ComparisonMetrics(BaseModel):
    """Monte-Carlo comparison between treatment and control conversion posteriors."""

    prob_treatment_beats_control: float
    absolute_lift_mean: float
    absolute_lift_cri_95: list[float]
    relative_lift_mean: float
    relative_lift_cri_95: list[float]
    expected_loss_choose_treatment: float
    expected_loss_choose_control: float


class PaymentFailedGuardrail(BaseModel):
    """Separate Beta-Binomial guardrail evaluation for payment_failed rates."""

    control_errors: int
    control_rate: float
    treatment_errors: int
    treatment_rate: float
    control_posterior_alpha: float
    control_posterior_beta: float
    control_posterior_mean: float
    control_credible_interval_95: list[float]
    treatment_posterior_alpha: float
    treatment_posterior_beta: float
    treatment_posterior_mean: float
    treatment_credible_interval_95: list[float]
    prob_treatment_exceeds_soft_delta: float
    prob_treatment_exceeds_hard_delta: float
    prob_treatment_exceeds_max_error_rate: float
    breached: bool
    breach_severity: Literal["none", "soft", "hard"] = "none"


class AbandonmentGuardrail(BaseModel):
    """Observed checkout abandonment counts and rates per variant."""

    control_count: int
    treatment_count: int
    control_rate: float = 0.0
    treatment_rate: float = 0.0


class GuardrailsSummary(BaseModel):
    """Container for all guardrail evaluations matching docs/api.md Sec 2.7."""

    payment_failed: PaymentFailedGuardrail
    checkout_abandoned: AbandonmentGuardrail


class ExperimentOutcome(StrEnum):
    """Explicit Bayesian + guardrail experiment outcome states."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NO_CLEAR_WINNER = "NO_CLEAR_WINNER"
    CONTROL_WINS = "CONTROL_WINS"
    VARIANT_WINS = "VARIANT_WINS"
    GUARDRAIL_BREACHED = "GUARDRAIL_BREACHED"
    INCONCLUSIVE = "INCONCLUSIVE"


class FrequentistComparison(BaseModel):
    """Complementary two-proportion z-test (distinct from Bayesian probability of superiority)."""

    z_statistic: float
    p_value: float
    significant_at_95: bool
    note: str = (
        "Frequentist two-proportion z-test p-value is distinct from Bayesian P(Treatment > Control)."
    )


class ParticipantMetricsSummary(BaseModel):
    """Explicit distinction between participants, exposures, total conversions, and unique conversions."""

    control_unique_participants: int
    treatment_unique_participants: int
    control_raw_exposures: int
    treatment_raw_exposures: int
    control_total_conversions: int
    treatment_total_conversions: int
    control_unique_conversions: int
    treatment_unique_conversions: int
    control_conversion_rate: float
    treatment_conversion_rate: float
    deduplicated_events: int
    total_events_processed: int
    unattributed_events: int


class BayesianAnalysisResult(BaseModel):
    """Complete Bayesian Beta-Binomial output matching docs/api.md Sec 2.7 /analysis."""

    experiment_id: str
    analytics_mode: str
    sample_adequacy: SampleAdequacy
    control: VariantPosterior
    treatment: VariantPosterior
    comparison: ComparisonMetrics
    frequentist: FrequentistComparison | None = None
    participant_metrics: ParticipantMetricsSummary | None = None
    experiment_outcome: ExperimentOutcome = ExperimentOutcome.INSUFFICIENT_DATA
    outcome_rationale: str = ""
    guardrails: GuardrailsSummary
    thresholds: GuardrailThresholds
    mc_samples: int
    seed: int


class AggregatedExperimentCounts(BaseModel):
    """Deduplicated exposure, conversion, error, and abandonment counts."""

    control_exposures: int
    control_conversions: int
    treatment_exposures: int
    treatment_conversions: int
    control_errors: int
    treatment_errors: int
    control_abandoned: int
    treatment_abandoned: int
    control_raw_exposures: int = 0
    treatment_raw_exposures: int = 0
    control_total_conversions: int = 0
    treatment_total_conversions: int = 0
    total_events_processed: int
    deduplicated_events: int
    unattributed_events: int


def _quantile(sorted_values: list[float], q: float) -> float:
    """Compute linear-interpolated quantile from a pre-sorted non-empty list."""
    n = len(sorted_values)
    if n == 0:
        raise ValueError("Cannot compute quantile of empty sequence")
    if n == 1:
        return sorted_values[0]
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def _ci95(sorted_values: list[float]) -> list[float]:
    """Return 95% equal-tailed credible interval [2.5th, 97.5th percentile]."""
    return [_quantile(sorted_values, 0.025), _quantile(sorted_values, 0.975)]


def compute_frequentist_z_test(
    *,
    control_exposures: int,
    control_conversions: int,
    treatment_exposures: int,
    treatment_conversions: int,
) -> FrequentistComparison:
    """Compute a two-proportion z-test p-value (complementary to Bayesian posteriors)."""
    if control_exposures <= 0 or treatment_exposures <= 0:
        return FrequentistComparison(z_statistic=0.0, p_value=1.0, significant_at_95=False)
    p1 = control_conversions / control_exposures
    p2 = treatment_conversions / treatment_exposures
    p_pool = (control_conversions + treatment_conversions) / (
        control_exposures + treatment_exposures
    )
    se = math.sqrt(
        max(
            0.0,
            p_pool * (1.0 - p_pool) * (1.0 / control_exposures + 1.0 / treatment_exposures),
        )
    )
    if se <= 1e-12:
        return FrequentistComparison(z_statistic=0.0, p_value=1.0, significant_at_95=False)
    z = (p2 - p1) / se
    p_val = math.erfc(abs(z) / math.sqrt(2.0))
    return FrequentistComparison(
        z_statistic=round(z, 6),
        p_value=round(min(1.0, max(0.0, p_val)), 6),
        significant_at_95=p_val < 0.05,
    )


def evaluate_experiment_outcome(
    *,
    sample_adequacy: SampleAdequacy,
    comparison: ComparisonMetrics,
    payment_failed: PaymentFailedGuardrail,
    checkout_abandoned: AbandonmentGuardrail,
    thresholds: GuardrailThresholds,
) -> tuple[ExperimentOutcome, str]:
    """Classify explicit experiment outcome with transparent thresholds."""
    if payment_failed.breached:
        return (
            ExperimentOutcome.GUARDRAIL_BREACHED,
            (
                f"Mandatory error guardrail breached ({payment_failed.breach_severity}): "
                f"treatment error rate {payment_failed.treatment_rate:.2%} vs control "
                f"{payment_failed.control_rate:.2%} (ceiling {thresholds.guardrail_max_error_rate:.2%})."
            ),
        )
    if (
        sample_adequacy.treatment_exposures >= 30
        and sample_adequacy.control_exposures >= 30
        and (checkout_abandoned.treatment_rate - checkout_abandoned.control_rate) >= 0.12
    ):
        return (
            ExperimentOutcome.GUARDRAIL_BREACHED,
            (
                f"Abandonment guardrail deteriorated by "
                f"{(checkout_abandoned.treatment_rate - checkout_abandoned.control_rate):.1%} "
                f"(treatment {checkout_abandoned.treatment_rate:.1%} vs control {checkout_abandoned.control_rate:.1%})."
            ),
        )
    if not sample_adequacy.is_adequate:
        return (
            ExperimentOutcome.INSUFFICIENT_DATA,
            (
                f"Insufficient unique participants: control={sample_adequacy.control_exposures}, "
                f"treatment={sample_adequacy.treatment_exposures} (minimum required: {sample_adequacy.min_required} per arm)."
            ),
        )

    p_win = comparison.prob_treatment_beats_control
    loss_t = comparison.expected_loss_choose_treatment
    loss_c = comparison.expected_loss_choose_control
    cri_lo, cri_hi = comparison.absolute_lift_cri_95

    if p_win >= 0.95 and loss_t <= thresholds.max_expected_loss:
        return (
            ExperimentOutcome.VARIANT_WINS,
            (
                f"Variant wins with P(Treatment > Control)={p_win:.2%} (>=95%) "
                f"and expected loss {loss_t:.4%} <= {thresholds.max_expected_loss:.4%}."
            ),
        )
    if p_win <= 0.05 and loss_c <= thresholds.max_expected_loss:
        return (
            ExperimentOutcome.CONTROL_WINS,
            (
                f"Control wins with P(Treatment > Control)={p_win:.2%} (<=5%) "
                f"and expected loss choosing control {loss_c:.4%} <= {thresholds.max_expected_loss:.4%}."
            ),
        )
    if cri_lo <= 0.0 <= cri_hi and 0.20 <= p_win <= 0.80:
        return (
            ExperimentOutcome.NO_CLEAR_WINNER,
            (
                f"No clear winner: P(Treatment > Control)={p_win:.2%} and 95% CrI "
                f"[{cri_lo:.2%}, {cri_hi:.2%}] spans zero."
            ),
        )
    return (
        ExperimentOutcome.INCONCLUSIVE,
        (
            f"Directional signal only (P(Treatment > Control)={p_win:.2%}, "
            f"expected loss={loss_t:.4%} vs limit {thresholds.max_expected_loss:.4%}); collect additional samples."
        ),
    )


def _coerce_thresholds(
    thresholds: GuardrailThresholds | Mapping[str, Any] | None,
) -> GuardrailThresholds:
    if thresholds is None:
        return GuardrailThresholds()
    if isinstance(thresholds, GuardrailThresholds):
        return thresholds
    return GuardrailThresholds.model_validate(dict(thresholds))


def run_bayesian_analysis(
    *,
    control_exposures: int,
    control_conversions: int,
    treatment_exposures: int,
    treatment_conversions: int,
    control_errors: int = 0,
    treatment_errors: int = 0,
    control_abandoned: int = 0,
    treatment_abandoned: int = 0,
    experiment_id: str = "",
    analytics_mode: str = "tracked_event_fallback",
    alpha_0: float = 1.0,
    beta_0: float = 1.0,
    mc_samples: int = 50_000,
    seed: int = 42,
    thresholds: GuardrailThresholds | Mapping[str, Any] | None = None,
) -> BayesianAnalysisResult:
    """Run deterministic pure-Python Beta-Binomial Monte-Carlo analysis.

    Uses ``random.Random(seed)`` with ``rng.betavariate(alpha, beta)`` for
    both primary conversion rates and ``payment_failed`` error guardrails.
    """
    parsed_thresholds = _coerce_thresholds(thresholds)
    validated = BayesianCountsInput(
        experiment_id=str(experiment_id),
        analytics_mode=analytics_mode,
        control_exposures=control_exposures,
        control_conversions=control_conversions,
        treatment_exposures=treatment_exposures,
        treatment_conversions=treatment_conversions,
        control_errors=control_errors,
        treatment_errors=treatment_errors,
        control_abandoned=control_abandoned,
        treatment_abandoned=treatment_abandoned,
        alpha_0=alpha_0,
        beta_0=beta_0,
        mc_samples=mc_samples,
        seed=seed,
        thresholds=parsed_thresholds,
    )

    n_c = validated.control_exposures
    k_c = validated.control_conversions
    n_t = validated.treatment_exposures
    k_t = validated.treatment_conversions
    e_c = validated.control_errors
    e_t = validated.treatment_errors
    m_draws = validated.mc_samples

    alpha_c = validated.alpha_0 + k_c
    beta_c = validated.beta_0 + (n_c - k_c)
    alpha_t = validated.alpha_0 + k_t
    beta_t = validated.beta_0 + (n_t - k_t)

    mean_c = alpha_c / (alpha_c + beta_c)
    mean_t = alpha_t / (alpha_t + beta_t)

    rng = random.Random(validated.seed)
    betavariate = rng.betavariate

    p_c_samples: list[float] = [0.0] * m_draws
    p_t_samples: list[float] = [0.0] * m_draws
    abs_diff_samples: list[float] = [0.0] * m_draws
    rel_lift_samples: list[float] = [0.0] * m_draws

    wins = 0
    loss_choose_t_sum = 0.0
    loss_choose_c_sum = 0.0
    abs_diff_sum = 0.0
    rel_lift_sum = 0.0

    for i in range(m_draws):
        pc = betavariate(alpha_c, beta_c)
        pt = betavariate(alpha_t, beta_t)
        p_c_samples[i] = pc
        p_t_samples[i] = pt

        diff = pt - pc
        abs_diff_samples[i] = diff
        abs_diff_sum += diff

        rel = diff / max(1e-9, pc)
        rel_lift_samples[i] = rel
        rel_lift_sum += rel

        if pt > pc:
            wins += 1
            loss_choose_c_sum += diff
        elif pc > pt:
            loss_choose_t_sum += -diff

    p_c_samples.sort()
    p_t_samples.sort()
    abs_diff_samples.sort()
    rel_lift_samples.sort()

    # Separate Beta-Binomial posterior for payment_failed guardrail rates.
    err_alpha_c = validated.alpha_0 + e_c
    err_beta_c = validated.beta_0 + (n_c - e_c)
    err_alpha_t = validated.alpha_0 + e_t
    err_beta_t = validated.beta_0 + (n_t - e_t)

    err_mean_c = err_alpha_c / (err_alpha_c + err_beta_c)
    err_mean_t = err_alpha_t / (err_alpha_t + err_beta_t)

    err_c_samples: list[float] = [0.0] * m_draws
    err_t_samples: list[float] = [0.0] * m_draws

    soft_delta = parsed_thresholds.soft_error_delta
    hard_delta = parsed_thresholds.hard_error_delta
    max_err = parsed_thresholds.guardrail_max_error_rate

    exceeds_soft = 0
    exceeds_hard = 0
    exceeds_max = 0

    for i in range(m_draws):
        ec = betavariate(err_alpha_c, err_beta_c)
        et = betavariate(err_alpha_t, err_beta_t)
        err_c_samples[i] = ec
        err_t_samples[i] = et

        delta = et - ec
        if delta > soft_delta:
            exceeds_soft += 1
        if delta > hard_delta:
            exceeds_hard += 1
        if et > max_err:
            exceeds_max += 1

    err_c_samples.sort()
    err_t_samples.sort()

    prob_soft = exceeds_soft / m_draws
    prob_hard = exceeds_hard / m_draws
    prob_max_err = exceeds_max / m_draws

    obs_err_rate_c = (e_c / n_c) if n_c > 0 else 0.0
    obs_err_rate_t = (e_t / n_t) if n_t > 0 else 0.0
    obs_abandoned_rate_c = (validated.control_abandoned / n_c) if n_c > 0 else 0.0
    obs_abandoned_rate_t = (validated.treatment_abandoned / n_t) if n_t > 0 else 0.0

    hard_breach = n_t >= 30 and (
        (err_mean_t >= max_err and prob_max_err >= 0.90) or prob_hard >= 0.95
    )
    soft_breach = n_t >= 50 and prob_soft >= 0.80

    if hard_breach:
        breach_severity: Literal["none", "soft", "hard"] = "hard"
    elif soft_breach:
        breach_severity = "soft"
    else:
        breach_severity = "none"

    is_adequate = (
        n_c >= parsed_thresholds.min_exposures and n_t >= parsed_thresholds.min_exposures
    )
    sample_adequacy = SampleAdequacy(
        control_exposures=n_c,
        treatment_exposures=n_t,
        min_required=parsed_thresholds.min_exposures,
        is_adequate=is_adequate,
    )
    comparison = ComparisonMetrics(
        prob_treatment_beats_control=wins / m_draws,
        absolute_lift_mean=abs_diff_sum / m_draws,
        absolute_lift_cri_95=_ci95(abs_diff_samples),
        relative_lift_mean=rel_lift_sum / m_draws,
        relative_lift_cri_95=_ci95(rel_lift_samples),
        expected_loss_choose_treatment=loss_choose_t_sum / m_draws,
        expected_loss_choose_control=loss_choose_c_sum / m_draws,
    )
    pf_guardrail = PaymentFailedGuardrail(
        control_errors=e_c,
        control_rate=obs_err_rate_c,
        treatment_errors=e_t,
        treatment_rate=obs_err_rate_t,
        control_posterior_alpha=err_alpha_c,
        control_posterior_beta=err_beta_c,
        control_posterior_mean=err_mean_c,
        control_credible_interval_95=_ci95(err_c_samples),
        treatment_posterior_alpha=err_alpha_t,
        treatment_posterior_beta=err_beta_t,
        treatment_posterior_mean=err_mean_t,
        treatment_credible_interval_95=_ci95(err_t_samples),
        prob_treatment_exceeds_soft_delta=prob_soft,
        prob_treatment_exceeds_hard_delta=prob_hard,
        prob_treatment_exceeds_max_error_rate=prob_max_err,
        breached=hard_breach or soft_breach,
        breach_severity=breach_severity,
    )
    aband_guardrail = AbandonmentGuardrail(
        control_count=validated.control_abandoned,
        treatment_count=validated.treatment_abandoned,
        control_rate=obs_abandoned_rate_c,
        treatment_rate=obs_abandoned_rate_t,
    )
    frequentist = compute_frequentist_z_test(
        control_exposures=n_c,
        control_conversions=k_c,
        treatment_exposures=n_t,
        treatment_conversions=k_t,
    )
    exp_outcome, outcome_rationale = evaluate_experiment_outcome(
        sample_adequacy=sample_adequacy,
        comparison=comparison,
        payment_failed=pf_guardrail,
        checkout_abandoned=aband_guardrail,
        thresholds=parsed_thresholds,
    )

    return BayesianAnalysisResult(
        experiment_id=validated.experiment_id,
        analytics_mode=validated.analytics_mode,
        sample_adequacy=sample_adequacy,
        control=VariantPosterior(
            exposures=n_c,
            conversions=k_c,
            posterior_alpha=alpha_c,
            posterior_beta=beta_c,
            mean_rate=mean_c,
            credible_interval_95=_ci95(p_c_samples),
        ),
        treatment=VariantPosterior(
            exposures=n_t,
            conversions=k_t,
            posterior_alpha=alpha_t,
            posterior_beta=beta_t,
            mean_rate=mean_t,
            credible_interval_95=_ci95(p_t_samples),
        ),
        comparison=comparison,
        frequentist=frequentist,
        participant_metrics=ParticipantMetricsSummary(
            control_unique_participants=n_c,
            treatment_unique_participants=n_t,
            control_raw_exposures=n_c,
            treatment_raw_exposures=n_t,
            control_total_conversions=k_c,
            treatment_total_conversions=k_t,
            control_unique_conversions=k_c,
            treatment_unique_conversions=k_t,
            control_conversion_rate=round(k_c / n_c, 6) if n_c > 0 else 0.0,
            treatment_conversion_rate=round(k_t / n_t, 6) if n_t > 0 else 0.0,
            deduplicated_events=0,
            total_events_processed=n_c + n_t,
            unattributed_events=0,
        ),
        experiment_outcome=exp_outcome,
        outcome_rationale=outcome_rationale,
        guardrails=GuardrailsSummary(
            payment_failed=pf_guardrail,
            checkout_abandoned=aband_guardrail,
        ),
        thresholds=parsed_thresholds,
        mc_samples=m_draws,
        seed=validated.seed,
    )


def _extract_attr(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _parse_timestamp(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        return datetime.fromisoformat(normalized).timestamp()
    return 0.0


def aggregate_experiment_events(
    events: Iterable[Mapping[str, Any] | Any],
    *,
    control_variation: str = "control",
    treatment_variation: str = "treatment",
    primary_metric: str = "checkout_completed",
    error_metric: str = "payment_failed",
    abandonment_metric: str = "checkout_abandoned",
    exposure_event: str = "feature_variation_assigned",
    variation_map: Mapping[str, str] | None = None,
) -> AggregatedExperimentCounts:
    """Deduplicate raw events and compute sound exposure/outcome counts (0 <= k <= n).

    Each user is attributed to their earliest observed ``feature_variation_assigned``
    arm (falling back to the earliest variation if assignment arrived alongside
    another event). Only users with an exposure assignment in that arm can count
    toward conversions, errors, or abandonments in that arm.
    """
    seen_event_ids: set[str] = set()
    dedup_count = 0
    total_count = 0
    unattributed = 0

    normalized_events: list[tuple[float, str, str, str]] = []
    for raw in events:
        total_count += 1
        event_id = _extract_attr(raw, "event_id")
        if event_id is not None:
            event_id_str = str(event_id)
            if event_id_str in seen_event_ids:
                dedup_count += 1
                continue
            seen_event_ids.add(event_id_str)

        raw_var = str(_extract_attr(raw, "variation_key", ""))
        mapped_var = variation_map.get(raw_var, raw_var) if variation_map else raw_var
        if mapped_var not in (control_variation, treatment_variation):
            unattributed += 1
            continue

        user_id = str(_extract_attr(raw, "user_identifier", ""))
        event_type = str(_extract_attr(raw, "event_type", ""))
        ts = _parse_timestamp(_extract_attr(raw, "occurred_at"))
        if not user_id or not event_type:
            unattributed += 1
            continue

        normalized_events.append((ts, user_id, mapped_var, event_type))

    normalized_events.sort(key=lambda row: row[0])

    user_arm: dict[str, str] = {}
    for _, user_id, var_key, event_type in normalized_events:
        if event_type == exposure_event and user_id not in user_arm:
            user_arm[user_id] = var_key

    control_users: set[str] = {u for u, arm in user_arm.items() if arm == control_variation}
    treatment_users: set[str] = {u for u, arm in user_arm.items() if arm == treatment_variation}

    control_conv_users: set[str] = set()
    treatment_conv_users: set[str] = set()
    control_err_users: set[str] = set()
    treatment_err_users: set[str] = set()
    control_aband_users: set[str] = set()
    treatment_aband_users: set[str] = set()
    control_raw_exp = 0
    treatment_raw_exp = 0
    control_total_conv = 0
    treatment_total_conv = 0

    for _, user_id, var_key, event_type in normalized_events:
        assigned_arm = user_arm.get(user_id)
        if assigned_arm is None or assigned_arm != var_key:
            continue
        if assigned_arm == control_variation:
            if event_type == exposure_event:
                control_raw_exp += 1
            elif event_type == primary_metric:
                control_total_conv += 1
                control_conv_users.add(user_id)
            elif event_type == error_metric:
                control_err_users.add(user_id)
            elif event_type == abandonment_metric:
                control_aband_users.add(user_id)
        else:
            if event_type == exposure_event:
                treatment_raw_exp += 1
            elif event_type == primary_metric:
                treatment_total_conv += 1
                treatment_conv_users.add(user_id)
            elif event_type == error_metric:
                treatment_err_users.add(user_id)
            elif event_type == abandonment_metric:
                treatment_aband_users.add(user_id)

    return AggregatedExperimentCounts(
        control_exposures=len(control_users),
        control_conversions=len(control_conv_users),
        treatment_exposures=len(treatment_users),
        treatment_conversions=len(treatment_conv_users),
        control_errors=len(control_err_users),
        treatment_errors=len(treatment_err_users),
        control_abandoned=len(control_aband_users),
        treatment_abandoned=len(treatment_aband_users),
        control_raw_exposures=control_raw_exp,
        treatment_raw_exposures=treatment_raw_exp,
        control_total_conversions=control_total_conv,
        treatment_total_conversions=treatment_total_conv,
        total_events_processed=total_count,
        deduplicated_events=dedup_count,
        unattributed_events=unattributed,
    )


def analyze_experiment_events(
    events: Iterable[Mapping[str, Any] | Any],
    *,
    experiment_id: str = "",
    analytics_mode: str = "tracked_event_fallback",
    control_variation: str = "control",
    treatment_variation: str = "treatment",
    primary_metric: str = "checkout_completed",
    error_metric: str = "payment_failed",
    abandonment_metric: str = "checkout_abandoned",
    variation_map: Mapping[str, str] | None = None,
    alpha_0: float = 1.0,
    beta_0: float = 1.0,
    mc_samples: int = 50_000,
    seed: int = 42,
    thresholds: GuardrailThresholds | Mapping[str, Any] | None = None,
) -> BayesianAnalysisResult:
    """Aggregate raw experiment events and run Bayesian Beta-Binomial analysis."""
    counts = aggregate_experiment_events(
        events,
        control_variation=control_variation,
        treatment_variation=treatment_variation,
        primary_metric=primary_metric,
        error_metric=error_metric,
        abandonment_metric=abandonment_metric,
        variation_map=variation_map,
    )
    res = run_bayesian_analysis(
        control_exposures=counts.control_exposures,
        control_conversions=counts.control_conversions,
        treatment_exposures=counts.treatment_exposures,
        treatment_conversions=counts.treatment_conversions,
        control_errors=counts.control_errors,
        treatment_errors=counts.treatment_errors,
        control_abandoned=counts.control_abandoned,
        treatment_abandoned=counts.treatment_abandoned,
        experiment_id=experiment_id,
        analytics_mode=analytics_mode,
        alpha_0=alpha_0,
        beta_0=beta_0,
        mc_samples=mc_samples,
        seed=seed,
        thresholds=thresholds,
    )
    res.participant_metrics = ParticipantMetricsSummary(
        control_unique_participants=counts.control_exposures,
        treatment_unique_participants=counts.treatment_exposures,
        control_raw_exposures=counts.control_raw_exposures,
        treatment_raw_exposures=counts.treatment_raw_exposures,
        control_total_conversions=counts.control_total_conversions,
        treatment_total_conversions=counts.treatment_total_conversions,
        control_unique_conversions=counts.control_conversions,
        treatment_unique_conversions=counts.treatment_conversions,
        control_conversion_rate=(
            round(counts.control_conversions / counts.control_exposures, 6)
            if counts.control_exposures > 0
            else 0.0
        ),
        treatment_conversion_rate=(
            round(counts.treatment_conversions / counts.treatment_exposures, 6)
            if counts.treatment_exposures > 0
            else 0.0
        ),
        deduplicated_events=counts.deduplicated_events,
        total_events_processed=counts.total_events_processed,
        unattributed_events=counts.unattributed_events,
    )
    return res
