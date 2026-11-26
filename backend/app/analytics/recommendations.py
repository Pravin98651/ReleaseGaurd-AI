"""Deterministic 5-state Release Recommendation Engine.

Evaluates Bayesian analysis outputs strictly in priority order per
docs/architecture.md Section 6.3.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.analytics.bayesian import (
    BayesianAnalysisResult,
    ExperimentOutcome,
    GuardrailThresholds,
)


class RecommendationStatus(StrEnum):
    """Exact 5-state release recommendation enum."""

    RECOMMEND_ROLLBACK = "Recommend Rollback"
    REDUCE_ROLLOUT = "Reduce Rollout"
    CONTINUE_MONITORING = "Continue Monitoring"
    EXPAND_ROLLOUT = "Expand Rollout"
    MAINTAIN_CURRENT_ROLLOUT = "Maintain Current Rollout"


class RecommendationTrigger(BaseModel):
    """Explicit numeric rule trigger evaluated by the engine."""

    metric: str
    actual_value: float
    threshold: float
    operator: Literal[">=", ">", "<=", "<"]
    met: bool
    description: str


class RecommendationResult(BaseModel):
    """Deterministic recommendation, numeric triggers, uncertainty, preconditions, consequences."""

    recommendation: RecommendationStatus
    priority_rank: int = Field(ge=1, le=5)
    suggested_action: Literal["rollback", "reduce", "monitor", "expand", "maintain"]
    requires_prod_approval: bool
    triggers: list[RecommendationTrigger]
    numeric_summary: dict[str, float | int | bool]
    confidence_summary: str
    preconditions: list[str]
    potential_consequences: list[str]
    rationale: str


def _resolve_thresholds(
    analysis: BayesianAnalysisResult,
    thresholds: GuardrailThresholds | Mapping[str, Any] | None,
) -> GuardrailThresholds:
    if thresholds is None:
        return analysis.thresholds
    if isinstance(thresholds, GuardrailThresholds):
        return thresholds
    return GuardrailThresholds.model_validate(dict(thresholds))


def _format_confidence_summary(analysis: BayesianAnalysisResult, min_exposures: int) -> str:
    ctrl_ci = analysis.control.credible_interval_95
    trt_ci = analysis.treatment.credible_interval_95
    err_ci = analysis.guardrails.payment_failed.treatment_credible_interval_95
    return (
        f"P(Treatment > Control) = {analysis.comparison.prob_treatment_beats_control:.1%}; "
        f"Control rate {analysis.control.mean_rate:.2%} "
        f"(95% CrI [{ctrl_ci[0]:.2%}, {ctrl_ci[1]:.2%}], n={analysis.control.exposures}), "
        f"Treatment rate {analysis.treatment.mean_rate:.2%} "
        f"(95% CrI [{trt_ci[0]:.2%}, {trt_ci[1]:.2%}], n={analysis.treatment.exposures}; "
        f"min_exposures={min_exposures}). "
        f"Expected loss: treat={analysis.comparison.expected_loss_choose_treatment:.4%}, "
        f"control={analysis.comparison.expected_loss_choose_control:.4%}. "
        f"Treatment payment error posterior {analysis.guardrails.payment_failed.treatment_posterior_mean:.2%} "
        f"(95% CrI [{err_ci[0]:.2%}, {err_ci[1]:.2%}])."
    )


def evaluate_recommendation(
    analysis: BayesianAnalysisResult,
    *,
    thresholds: GuardrailThresholds | Mapping[str, Any] | None = None,
    environment_name: str | None = None,
    current_rollout_percentage: int | None = None,
) -> RecommendationResult:
    """Evaluate release recommendation in exact priority order (Sec 6.3).

    Priority order:
      1. Recommend Rollback
      2. Reduce Rollout
      3. Continue Monitoring
      4. Expand Rollout
      5. Maintain Current Rollout
    """
    cfg = _resolve_thresholds(analysis, thresholds)
    n_c = analysis.control.exposures
    n_t = analysis.treatment.exposures
    p_beats = analysis.comparison.prob_treatment_beats_control
    loss_treat = analysis.comparison.expected_loss_choose_treatment
    loss_ctrl = analysis.comparison.expected_loss_choose_control

    pf = analysis.guardrails.payment_failed
    err_mean_t = pf.treatment_posterior_mean
    prob_max_err = pf.prob_treatment_exceeds_max_error_rate
    prob_soft = pf.prob_treatment_exceeds_soft_delta
    prob_hard = pf.prob_treatment_exceeds_hard_delta

    numeric_summary: dict[str, float | int | bool] = {
        "control_exposures": n_c,
        "treatment_exposures": n_t,
        "min_exposures": cfg.min_exposures,
        "prob_treatment_beats_control": p_beats,
        "expected_loss_choose_treatment": loss_treat,
        "expected_loss_choose_control": loss_ctrl,
        "max_expected_loss": cfg.max_expected_loss,
        "treatment_error_rate": pf.treatment_rate,
        "treatment_error_posterior_mean": err_mean_t,
        "guardrail_max_error_rate": cfg.guardrail_max_error_rate,
        "prob_treatment_exceeds_max_error_rate": prob_max_err,
        "soft_error_delta": cfg.soft_error_delta,
        "prob_treatment_exceeds_soft_delta": prob_soft,
        "hard_error_delta": cfg.hard_error_delta,
        "prob_treatment_exceeds_hard_delta": prob_hard,
    }
    confidence_summary = _format_confidence_summary(analysis, cfg.min_exposures)
    env_label = environment_name or "target environment"
    rollout_ctx = (
        f" (currently at {current_rollout_percentage}%)"
        if current_rollout_percentage is not None
        else ""
    )

    # Priority 1: Recommend Rollback
    max_err_breach = (
        n_t >= 30
        and err_mean_t >= cfg.guardrail_max_error_rate
        and prob_max_err >= 0.90
    )
    hard_delta_breach = n_t >= 30 and prob_hard >= 0.95
    severe_conversion_loss = (
        n_c >= cfg.min_exposures
        and n_t >= cfg.min_exposures
        and p_beats < 0.05
        and loss_ctrl < 0.001
    )

    if max_err_breach or hard_delta_breach or severe_conversion_loss:
        triggers: list[RecommendationTrigger] = []
        if max_err_breach:
            triggers.extend(
                [
                    RecommendationTrigger(
                        metric="treatment_exposures",
                        actual_value=float(n_t),
                        threshold=30.0,
                        operator=">=",
                        met=True,
                        description="Minimum sample for emergency guardrail check met (n_t >= 30)",
                    ),
                    RecommendationTrigger(
                        metric="treatment_error_posterior_mean",
                        actual_value=err_mean_t,
                        threshold=cfg.guardrail_max_error_rate,
                        operator=">=",
                        met=True,
                        description="Treatment error posterior mean exceeds guardrail_max_error_rate",
                    ),
                    RecommendationTrigger(
                        metric="prob_treatment_exceeds_max_error_rate",
                        actual_value=prob_max_err,
                        threshold=0.90,
                        operator=">=",
                        met=True,
                        description="High posterior certainty that treatment error exceeds ceiling",
                    ),
                ]
            )
        if hard_delta_breach:
            triggers.extend(
                [
                    RecommendationTrigger(
                        metric="treatment_exposures",
                        actual_value=float(n_t),
                        threshold=30.0,
                        operator=">=",
                        met=True,
                        description="Minimum sample for hard error delta check met (n_t >= 30)",
                    ),
                    RecommendationTrigger(
                        metric="prob_treatment_exceeds_hard_delta",
                        actual_value=prob_hard,
                        threshold=0.95,
                        operator=">=",
                        met=True,
                        description="Treatment error exceeds control + hard_error_delta with >= 95% probability",
                    ),
                ]
            )
        if severe_conversion_loss:
            triggers.extend(
                [
                    RecommendationTrigger(
                        metric="prob_treatment_beats_control",
                        actual_value=p_beats,
                        threshold=0.05,
                        operator="<",
                        met=True,
                        description="Treatment underperforms control with < 5% probability of beating control",
                    ),
                    RecommendationTrigger(
                        metric="expected_loss_choose_control",
                        actual_value=loss_ctrl,
                        threshold=0.001,
                        operator="<",
                        met=True,
                        description="Regret of reverting to control is negligible (< 0.001)",
                    ),
                ]
            )
        return RecommendationResult(
            recommendation=RecommendationStatus.RECOMMEND_ROLLBACK,
            priority_rank=1,
            suggested_action="rollback",
            requires_prod_approval=True,
            triggers=triggers,
            numeric_summary=numeric_summary,
            confidence_summary=confidence_summary,
            preconditions=[
                f"Verify live Flagsmith state in {env_label} before executing rollback.",
                "Production rollback requires single-use Category C human approval and action_hash verification.",
                "Check confounding signals (e.g., whether control also spiked) if external outage is suspected.",
            ],
            potential_consequences=[
                "Immediate reversal to control stops treatment exposure and caps further user-facing failures or conversion loss.",
                "Reverts any positive feature behavior for treatment users until a validated fix is deployed.",
            ],
            rationale=(
                f"Recommend immediate rollback in {env_label}{rollout_ctx}: "
                f"hard guardrail or severe conversion regression detected "
                f"(P(err_t > max)={prob_max_err:.1%}, P(err_t > err_c + hard)={prob_hard:.1%}, "
                f"P(T > C)={p_beats:.1%})."
            ),
        )

    # Priority 2: Reduce Rollout
    soft_delta_warning = n_t >= 50 and prob_soft >= 0.80
    weak_conversion = (
        n_c >= cfg.min_exposures
        and n_t >= cfg.min_exposures
        and p_beats < 0.25
    )

    if soft_delta_warning or weak_conversion:
        triggers = []
        if soft_delta_warning:
            triggers.extend(
                [
                    RecommendationTrigger(
                        metric="treatment_exposures",
                        actual_value=float(n_t),
                        threshold=50.0,
                        operator=">=",
                        met=True,
                        description="Minimum sample for soft error delta warning met (n_t >= 50)",
                    ),
                    RecommendationTrigger(
                        metric="prob_treatment_exceeds_soft_delta",
                        actual_value=prob_soft,
                        threshold=0.80,
                        operator=">=",
                        met=True,
                        description="Treatment error exceeds control + soft_error_delta with >= 80% probability",
                    ),
                ]
            )
        if weak_conversion:
            triggers.append(
                RecommendationTrigger(
                    metric="prob_treatment_beats_control",
                    actual_value=p_beats,
                    threshold=0.25,
                    operator="<",
                    met=True,
                    description="Treatment conversion trails control (P(T > C) < 25%)",
                )
            )
        return RecommendationResult(
            recommendation=RecommendationStatus.REDUCE_ROLLOUT,
            priority_rank=2,
            suggested_action="reduce",
            requires_prod_approval=True,
            triggers=triggers,
            numeric_summary=numeric_summary,
            confidence_summary=confidence_summary,
            preconditions=[
                f"Verify current rollout percentage and live Flagsmith version in {env_label}.",
                "Reducing rollout in production requires Category C human approval.",
            ],
            potential_consequences=[
                "Decreasing traffic allocation limits blast radius while preserving live diagnostic signal.",
                "Slower sample accumulation will extend time needed to reach conclusive Bayesian certainty.",
            ],
            rationale=(
                f"Recommend reducing rollout in {env_label}{rollout_ctx}: "
                f"soft guardrail warning or negative conversion trend observed "
                f"(P(err_t > err_c + soft)={prob_soft:.1%}, P(T > C)={p_beats:.1%})."
            ),
        )

    # Priority 3: Continue Monitoring
    if n_c < cfg.min_exposures or n_t < cfg.min_exposures:
        triggers = [
            RecommendationTrigger(
                metric="control_exposures",
                actual_value=float(n_c),
                threshold=float(cfg.min_exposures),
                operator="<",
                met=n_c < cfg.min_exposures,
                description=f"Control exposures ({n_c}) compared against min_exposures ({cfg.min_exposures})",
            ),
            RecommendationTrigger(
                metric="treatment_exposures",
                actual_value=float(n_t),
                threshold=float(cfg.min_exposures),
                operator="<",
                met=n_t < cfg.min_exposures,
                description=f"Treatment exposures ({n_t}) compared against min_exposures ({cfg.min_exposures})",
            ),
        ]
        return RecommendationResult(
            recommendation=RecommendationStatus.CONTINUE_MONITORING,
            priority_rank=3,
            suggested_action="monitor",
            requires_prod_approval=False,
            triggers=triggers,
            numeric_summary=numeric_summary,
            confidence_summary=confidence_summary,
            preconditions=[
                f"Accumulate at least {cfg.min_exposures} distinct exposures per arm before expansion decisions.",
                "Keep guardrail monitoring active for early emergency triggers (n_t >= 30).",
            ],
            potential_consequences=[
                "Expanding prematurely on under-powered samples increases false-positive rollout risk.",
                "Holding traffic steady allows posterior credible intervals to tighten cleanly.",
            ],
            rationale=(
                f"Continue monitoring in {env_label}{rollout_ctx}: sample size inadequate "
                f"(control={n_c}, treatment={n_t}, min_exposures={cfg.min_exposures})."
            ),
        )

    # Priority 4: Expand Rollout
    guardrail_warning = (
        pf.breached
        or prob_soft >= 0.80
        or prob_hard >= 0.95
        or (err_mean_t >= cfg.guardrail_max_error_rate and prob_max_err >= 0.90)
    )
    if (
        p_beats >= 0.90
        and loss_treat <= cfg.max_expected_loss
        and not guardrail_warning
    ):
        triggers = [
            RecommendationTrigger(
                metric="prob_treatment_beats_control",
                actual_value=p_beats,
                threshold=0.90,
                operator=">=",
                met=True,
                description="Treatment beats control with >= 90% posterior probability",
            ),
            RecommendationTrigger(
                metric="expected_loss_choose_treatment",
                actual_value=loss_treat,
                threshold=cfg.max_expected_loss,
                operator="<=",
                met=True,
                description="Expected loss of choosing treatment is within max_expected_loss",
            ),
            RecommendationTrigger(
                metric="prob_treatment_exceeds_soft_delta",
                actual_value=prob_soft,
                threshold=0.80,
                operator="<",
                met=True,
                description="No payment error guardrail warning active",
            ),
        ]
        return RecommendationResult(
            recommendation=RecommendationStatus.EXPAND_ROLLOUT,
            priority_rank=4,
            suggested_action="expand",
            requires_prod_approval=True,
            triggers=triggers,
            numeric_summary=numeric_summary,
            confidence_summary=confidence_summary,
            preconditions=[
                f"Advance one gradual stage along 10% -> 25% -> 50% -> 75% -> 100% in {env_label}.",
                "Verify live Flagsmith state and obtain Category C approval for production stage progression.",
            ],
            potential_consequences=[
                "Exposes a larger user cohort to treatment's positive conversion lift.",
                "Retains bounded step-wise exposure so subsequent stage telemetry can confirm stability.",
            ],
            rationale=(
                f"Recommend expanding rollout in {env_label}{rollout_ctx}: "
                f"P(T > C)={p_beats:.1%} >= 90.0%, expected loss={loss_treat:.4%} <= "
                f"{cfg.max_expected_loss:.4%}, and all guardrails are clear."
            ),
        )

    # Priority 5: Maintain Current Rollout
    triggers = [
        RecommendationTrigger(
            metric="prob_treatment_beats_control",
            actual_value=p_beats,
            threshold=0.90,
            operator="<",
            met=p_beats < 0.90,
            description="Conversion certainty has not yet crossed the 90% expansion threshold",
        ),
        RecommendationTrigger(
            metric="expected_loss_choose_treatment",
            actual_value=loss_treat,
            threshold=cfg.max_expected_loss,
            operator=">",
            met=loss_treat > cfg.max_expected_loss,
            description="Expected loss compared against max_expected_loss ceiling",
        ),
    ]
    return RecommendationResult(
        recommendation=RecommendationStatus.MAINTAIN_CURRENT_ROLLOUT,
        priority_rank=5,
        suggested_action="maintain",
        requires_prod_approval=False,
        triggers=triggers,
        numeric_summary=numeric_summary,
        confidence_summary=confidence_summary,
        preconditions=[
            "Keep current Flagsmith percentage allocation unchanged while collecting more evidence.",
            "Continue monitoring conversion lift and error guardrails.",
        ],
        potential_consequences=[
            "Avoids prematurely scaling an inconclusive or marginal change.",
            "Preserves existing traffic balance until expected loss drops below threshold or regression emerges.",
        ],
        rationale=(
            f"Maintain current rollout in {env_label}{rollout_ctx}: sample is adequate "
            f"(control={n_c}, treatment={n_t}), guardrails are safe, and evidence is inconclusive "
            f"(P(T > C)={p_beats:.1%}, expected_loss={loss_treat:.4%})."
        ),
    )


class ExperimentReadinessCheck(BaseModel):
    """Single check inside the 7-point Experiment Readiness Report."""

    key: str
    label: str
    passed: bool
    detail: str


class ExperimentReadinessReport(BaseModel):
    """Comprehensive readiness gate before recommending or promoting an experiment winner."""

    is_ready_for_winner_decision: bool
    checks: list[ExperimentReadinessCheck]
    blocking_reasons: list[str]


def map_flagsmith_native_results(
    native_res: Mapping[str, Any] | None,
    *,
    max_stale_hours: float = 24.0,
) -> dict[str, Any]:
    """Normalize Flagsmith native /results/ payload and detect stale warehouse data."""
    if not native_res or not native_res.get("available"):
        return {
            "available": False,
            "is_final": False,
            "as_of": None,
            "stale": True,
            "metric_id": None,
            "srm_p_value": None,
            "variants": {},
            "note": "Flagsmith native results unavailable or not yet refreshed; local Beta-Binomial complements Flagsmith.",
        }

    inner = (native_res.get("results") or {}).get("results") or {}
    as_of_raw = inner.get("as_of")
    is_final = bool(inner.get("is_final", False))
    payload = inner.get("payload") or {}
    metrics = payload.get("metrics") or []
    first_metric = metrics[0] if metrics and isinstance(metrics[0], Mapping) else {}

    stale = False
    if isinstance(as_of_raw, str) and as_of_raw.strip():
        try:
            ts = datetime.fromisoformat(as_of_raw.strip().replace("Z", "+00:00"))
            age_hours = (datetime.now(UTC) - ts).total_seconds() / 3600.0
            stale = age_hours > max_stale_hours
        except ValueError:
            stale = True
    else:
        stale = True

    return {
        "available": True,
        "status_code": native_res.get("status_code"),
        "is_final": is_final,
        "as_of": as_of_raw,
        "stale": stale,
        "metric_id": first_metric.get("metric_id"),
        "srm_p_value": payload.get("srm_p_value"),
        "variants": first_metric.get("variants") or {},
        "note": "Flagsmith native metrics complement ReleaseGuard Bayesian posteriors; neither replaces the other.",
    }


def evaluate_experiment_readiness(
    *,
    analysis: BayesianAnalysisResult,
    experiment_status: str,
    analytics_mode: str,
    flagsmith_experiment_id: str | None,
    started_at: datetime | None,
    latest_event_at: datetime | None,
    native_summary: Mapping[str, Any] | None = None,
    min_observation_minutes: float = 0.0,
) -> ExperimentReadinessReport:
    """Validate 7 readiness criteria before recommending or promoting a winner."""
    now = datetime.now(UTC)
    n_c = analysis.control.exposures
    n_t = analysis.treatment.exposures
    min_req = analysis.sample_adequacy.min_required

    obs_minutes = 0.0
    if started_at is not None:
        st = started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)
        obs_minutes = max(0.0, (now - st).total_seconds() / 60.0)
    obs_ok = (started_at is not None) and (obs_minutes >= min_observation_minutes)

    fresh_ok = False
    fresh_detail = "No telemetry events recorded yet."
    if latest_event_at is not None:
        le = latest_event_at if latest_event_at.tzinfo else latest_event_at.replace(tzinfo=UTC)
        age_hours = max(0.0, (now - le).total_seconds() / 3600.0)
        fresh_ok = age_hours <= 24.0
        fresh_detail = f"Latest event {age_hours:.2f}h ago (limit 24h)."

    pm = analysis.participant_metrics
    unattributed = pm.unattributed_events if pm else 0
    mapping_ok = (n_c + n_t) > 0 and (unattributed <= max(5, n_c + n_t))

    total_convs = analysis.control.conversions + analysis.treatment.conversions
    conv_ok = total_convs > 0

    guardrail_ok = analysis.experiment_outcome != ExperimentOutcome.GUARDRAIL_BREACHED

    fs_ok = experiment_status in ("running", "completed") and (
        analytics_mode != "flagsmith_native" or bool(flagsmith_experiment_id)
    )

    checks = [
        ExperimentReadinessCheck(
            key="min_participants_per_arm",
            label="Minimum Unique Participants Per Arm",
            passed=analysis.sample_adequacy.is_adequate,
            detail=f"control={n_c}, treatment={n_t}, min_required={min_req}",
        ),
        ExperimentReadinessCheck(
            key="min_observation_period",
            label="Minimum Observation Period",
            passed=obs_ok,
            detail=f"observed={obs_minutes:.1f}m (required>={min_observation_minutes:.1f}m)",
        ),
        ExperimentReadinessCheck(
            key="results_freshness",
            label="Results Freshness",
            passed=fresh_ok,
            detail=fresh_detail,
        ),
        ExperimentReadinessCheck(
            key="event_mapping",
            label="Event & Variant Attribution",
            passed=mapping_ok,
            detail=f"attributed={n_c + n_t}, unattributed={unattributed}",
        ),
        ExperimentReadinessCheck(
            key="sufficient_conversion_data",
            label="Conversion Signal Present",
            passed=conv_ok,
            detail=f"total_unique_conversions={total_convs}",
        ),
        ExperimentReadinessCheck(
            key="guardrail_health",
            label="Mandatory Guardrail Health",
            passed=guardrail_ok,
            detail=analysis.outcome_rationale or "Guardrails within bounds.",
        ),
        ExperimentReadinessCheck(
            key="flagsmith_status",
            label="Flagsmith Experiment Status",
            passed=fs_ok,
            detail=f"status={experiment_status}, fs_exp_id={flagsmith_experiment_id or 'none'}",
        ),
    ]
    if native_summary and native_summary.get("available") and native_summary.get("stale"):
        checks.append(
            ExperimentReadinessCheck(
                key="flagsmith_native_freshness",
                label="Flagsmith Native Refresh Freshness",
                passed=False,
                detail="Flagsmith native results are stale (>24h); refresh recommended.",
            )
        )

    blocking = [f"{c.label}: {c.detail}" for c in checks if not c.passed]
    return ExperimentReadinessReport(
        is_ready_for_winner_decision=len(blocking) == 0,
        checks=checks,
        blocking_reasons=blocking,
    )
