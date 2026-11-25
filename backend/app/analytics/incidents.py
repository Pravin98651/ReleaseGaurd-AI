"""Incident Investigation analytics engine.

Bins error/abandonment events into 5-minute windows, correlates spike onset
against rollout_history & PR timestamps within +/-30 minutes, runs variation-
stratified confounding checks, and ALWAYS enforces correlation_not_causation=True.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, Field


class WindowVariationMetrics(BaseModel):
    """Per-variation metrics inside a single 5-minute time bin."""

    variation_key: str
    exposures: int
    checkout_started: int
    checkout_completed: int
    payment_failed: int
    checkout_abandoned: int
    attempts: int
    payment_failed_rate: float
    abandonment_rate: float


class FiveMinuteWindowMetrics(BaseModel):
    """Aggregated telemetry inside a single 5-minute window."""

    window_start: datetime
    window_end: datetime
    exposures: int
    checkout_started: int
    checkout_completed: int
    payment_failed: int
    checkout_abandoned: int
    attempts: int
    payment_failed_rate: float
    abandonment_rate: float
    is_spike: bool = False
    by_variation: dict[str, WindowVariationMetrics] = Field(default_factory=dict)


class VariationObservedSummary(BaseModel):
    """Overall and spike-window error/abandonment rates for a variation."""

    variation_key: str
    exposures: int
    attempts: int
    payment_failed: int
    checkout_abandoned: int
    payment_failed_rate: float
    abandonment_rate: float
    spike_attempts: int = 0
    spike_payment_failed: int = 0
    spike_payment_failed_rate: float = 0.0
    spike_abandonment_rate: float = 0.0


class ObservedMetrics(BaseModel):
    """Factual observed telemetry summary across 5-minute windows."""

    flag_key: str | None = None
    environment_name: str | None = None
    bin_minutes: int = 5
    window_minutes: int = 60
    total_exposures: int
    total_attempts: int
    total_payment_failed: int
    total_checkout_abandoned: int
    overall_payment_failed_rate: float
    overall_abandonment_rate: float
    baseline_error_rate: float
    peak_error_rate: float
    spike_detected: bool
    spike_metric: str | None = None
    spike_onset_at: datetime | None = None
    peak_window_at: datetime | None = None
    by_variation: dict[str, VariationObservedSummary] = Field(default_factory=dict)
    windows: list[FiveMinuteWindowMetrics] = Field(default_factory=list)


class TemporalCorrelation(BaseModel):
    """Ranked rollout or PR event within +/-30 minutes of spike onset."""

    source_type: Literal["rollout_history", "pull_request"]
    event_id: str
    title: str
    timestamp: datetime
    delta_minutes_from_onset: float
    relation: Literal["before_onset", "at_onset", "after_onset"]
    temporal_score: float = Field(ge=0.0, le=1.0)
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_not_causation: Literal[True] = True


class ConfoundingCheck(BaseModel):
    """Variation-stratified or operational confounding assessment."""

    check_name: str
    status: str
    confounder_detected: bool
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class IncidentInvestigationResult(BaseModel):
    """Structured incident investigation response with strict causal disclaimer."""

    flag_key: str | None = None
    environment_name: str | None = None
    correlation_not_causation: Literal[True] = True
    causal_disclaimer: str = (
        "Temporal proximity to a rollout or pull request indicates correlation only "
        "and does not establish causal certainty."
    )
    observed_metrics: ObservedMetrics
    temporal_correlations: list[TemporalCorrelation] = Field(default_factory=list)
    confounding_checks: list[ConfoundingCheck] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _parse_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _floor_bin(dt: datetime, bin_minutes: int) -> datetime:
    bin_seconds = bin_minutes * 60
    epoch = int(math.floor(dt.timestamp() / bin_seconds) * bin_seconds)
    return datetime.fromtimestamp(epoch, tz=UTC)


def _compute_attempts(
    exposures: int,
    started: int,
    completed: int,
    failed: int,
    abandoned: int,
) -> int:
    return max(exposures, started, completed + failed + abandoned)


def _classify_control_treatment_keys(
    variations: dict[str, VariationObservedSummary],
) -> tuple[str | None, str | None]:
    keys = list(variations.keys())
    if len(keys) < 2:
        return (keys[0] if keys else None), None
    control_aliases = ("control", "v1", "false", "baseline", "off")
    treatment_aliases = ("treatment", "v2", "true", "variant", "on")

    ctrl = next((k for k in keys if k.lower() in control_aliases), None)
    trt = next((k for k in keys if k.lower() in treatment_aliases and k != ctrl), None)
    if not ctrl:
        ctrl = next(
            (k for k in keys if any(alias in k.lower() for alias in ("control", "v1", "baseline"))),
            None,
        )
    if not trt:
        trt = next(
            (
                k
                for k in keys
                if k != ctrl and any(alias in k.lower() for alias in ("treatment", "v2", "variant"))
            ),
            None,
        )
    if ctrl and trt:
        return ctrl, trt
    sorted_keys = sorted(keys)
    return sorted_keys[0], sorted_keys[1]


def _score_temporal_proximity(
    *,
    event_ts: datetime,
    onset_ts: datetime,
    correlation_window_minutes: int,
    source_weight: float,
) -> tuple[float, float, Literal["before_onset", "at_onset", "after_onset"]]:
    delta_minutes = (event_ts - onset_ts).total_seconds() / 60.0
    abs_delta = abs(delta_minutes)
    proximity = max(0.0, 1.0 - (abs_delta / float(correlation_window_minutes)))

    if abs_delta <= 1.0:
        relation: Literal["before_onset", "at_onset", "after_onset"] = "at_onset"
        timing_factor = 0.65 + 0.35 * proximity
    elif delta_minutes < 0:
        relation = "before_onset"
        timing_factor = 0.60 + 0.40 * proximity
    elif delta_minutes <= 5.0:
        relation = "after_onset"
        timing_factor = 0.55 + 0.35 * proximity
    else:
        relation = "after_onset"
        timing_factor = 0.40 * proximity

    score = round(min(1.0, max(0.0, timing_factor * source_weight)), 4)
    return score, round(delta_minutes, 2), relation


def _build_confounding_checks(
    *,
    by_variation: dict[str, VariationObservedSummary],
    spike_detected: bool,
    spike_metric: str | None,
    correlations: list[TemporalCorrelation],
    failed_events_during_spike: list[dict[str, Any]],
) -> tuple[list[ConfoundingCheck], bool]:
    """Run variation-stratified, concurrent-change, and property confounding checks."""
    checks: list[ConfoundingCheck] = []
    external_confounder_suspected = False

    if not spike_detected:
        checks.append(
            ConfoundingCheck(
                check_name="variation_stratification",
                status="no_spike_detected",
                confounder_detected=False,
                summary="No statistically notable error or abandonment spike detected in the window.",
                evidence={
                    "variations": {
                        k: v.model_dump() for k, v in by_variation.items()
                    }
                },
            )
        )
        return checks, False

    if len(by_variation) < 2:
        only_var = next(iter(by_variation.keys()), None)
        checks.append(
            ConfoundingCheck(
                check_name="variation_stratification",
                status="insufficient_variation_data",
                confounder_detected=False,
                summary=(
                    f"Only variation '{only_var}' was observed during the window; "
                    "cannot isolate flag-specific effects from shared infrastructure issues."
                ),
                evidence={"observed_variations": list(by_variation.keys())},
            )
        )
    else:
        ctrl_key, trt_key = _classify_control_treatment_keys(by_variation)
        assert ctrl_key is not None and trt_key is not None
        ctrl = by_variation[ctrl_key]
        trt = by_variation[trt_key]

        use_abandonment = spike_metric == "checkout_abandoned"
        ctrl_rate = ctrl.spike_abandonment_rate if use_abandonment else ctrl.spike_payment_failed_rate
        trt_rate = trt.spike_abandonment_rate if use_abandonment else trt.spike_payment_failed_rate
        metric_label = "Abandonment" if use_abandonment else "Error"
        floor_rate = 0.10 if use_abandonment else 0.04
        both_elevated = ctrl_rate >= floor_rate and trt_rate >= floor_rate
        comparable_rates = abs(trt_rate - ctrl_rate) <= floor_rate or (
            ctrl_rate > 0 and 0.5 <= (trt_rate / ctrl_rate) <= 2.0
        )

        if both_elevated and comparable_rates:
            external_confounder_suspected = True
            checks.append(
                ConfoundingCheck(
                    check_name="variation_stratification",
                    status="likely_external_confounder",
                    confounder_detected=True,
                    summary=(
                        f"{metric_label} spike occurred in both '{ctrl_key}' ({ctrl_rate:.1%}) and "
                        f"'{trt_key}' ({trt_rate:.1%}); pattern suggests an external confounder "
                        f"(such as a payment gateway outage) rather than a '{trt_key}'-only defect."
                    ),
                    evidence={
                        "metric": spike_metric or "payment_failed",
                        "control_variation": ctrl_key,
                        "control_spike_rate": round(ctrl_rate, 4),
                        "treatment_variation": trt_key,
                        "treatment_spike_rate": round(trt_rate, 4),
                    },
                )
            )
        elif trt_rate >= floor_rate and trt_rate > ctrl_rate + 0.03:
            checks.append(
                ConfoundingCheck(
                    check_name="variation_stratification",
                    status="variation_isolated",
                    confounder_detected=False,
                    summary=(
                        f"{metric_label} spike is concentrated in '{trt_key}' ({trt_rate:.1%}) while "
                        f"'{ctrl_key}' remained low ({ctrl_rate:.1%}). Consistent with a "
                        f"'{trt_key}'-specific regression, though non-causal."
                    ),
                    evidence={
                        "metric": spike_metric or "payment_failed",
                        "control_variation": ctrl_key,
                        "control_spike_rate": round(ctrl_rate, 4),
                        "treatment_variation": trt_key,
                        "treatment_spike_rate": round(trt_rate, 4),
                    },
                )
            )
        else:
            checks.append(
                ConfoundingCheck(
                    check_name="variation_stratification",
                    status="mixed_variation_signal",
                    confounder_detected=False,
                    summary=(
                        f"Observed {metric_label.lower()} rates during spike: "
                        f"'{ctrl_key}'={ctrl_rate:.1%}, '{trt_key}'={trt_rate:.1%}."
                    ),
                    evidence={
                        "metric": spike_metric or "payment_failed",
                        "control_variation": ctrl_key,
                        "control_spike_rate": round(ctrl_rate, 4),
                        "treatment_variation": trt_key,
                        "treatment_spike_rate": round(trt_rate, 4),
                    },
                )
            )

    # Concurrent changes confounder
    if len(correlations) >= 2:
        checks.append(
            ConfoundingCheck(
                check_name="concurrent_changes",
                status="multiple_overlapping_changes",
                confounder_detected=True,
                summary=(
                    f"{len(correlations)} rollout/PR changes occurred within +/-30m of spike onset; "
                    "overlapping deployments confound attributing the spike to a single change."
                ),
                evidence={
                    "correlated_event_ids": [c.event_id for c in correlations],
                },
            )
        )

    # Segment / property concentration check (e.g., promo_code, payment_method, error_code)
    if failed_events_during_spike:
        total_failed = len(failed_events_during_spike)
        for prop_key in ("error_code", "promo_code", "payment_method"):
            counts: dict[str, int] = {}
            for props in failed_events_during_spike:
                val = props.get(prop_key)
                if val is not None and str(val).strip() != "":
                    val_str = str(val)
                    counts[val_str] = counts.get(val_str, 0) + 1
            if counts:
                top_val, top_count = max(counts.items(), key=lambda kv: kv[1])
                share = top_count / total_failed
                if share >= 0.60 and top_count >= 2:
                    checks.append(
                        ConfoundingCheck(
                            check_name=f"property_concentration_{prop_key}",
                            status="concentrated_segment",
                            confounder_detected=True,
                            summary=(
                                f"{top_count}/{total_failed} ({share:.0%}) of spike failures "
                                f"share {prop_key}='{top_val}'."
                            ),
                            evidence={
                                "property": prop_key,
                                "dominant_value": top_val,
                                "count": top_count,
                                "total_failed": total_failed,
                                "share": round(share, 4),
                            },
                        )
                    )

    return checks, external_confounder_suspected


def investigate_incident(
    *,
    events: Iterable[Mapping[str, Any] | Any],
    rollout_history: Iterable[Mapping[str, Any] | Any] = (),
    pull_requests: Iterable[Mapping[str, Any] | Any] = (),
    flag_key: str | None = None,
    feature_flag_id: str | None = None,
    flag_id_to_key: Mapping[str, str] | None = None,
    environment_name: str | None = None,
    window_minutes: int = 60,
    bin_minutes: int = 5,
    correlation_window_minutes: int = 30,
    spike_rate_threshold: float = 0.05,
    now: datetime | None = None,
) -> IncidentInvestigationResult:
    """Investigate error/abandonment spikes across 5-minute windows without causal claims."""
    parsed_events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for raw in events:
        event_id = _attr(raw, "event_id")
        if event_id is not None:
            eid = str(event_id)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)

        ev_flag = _attr(raw, "flag_key")
        ev_env = _attr(raw, "environment_name")
        if flag_key and ev_flag and str(ev_flag) != flag_key:
            continue
        if environment_name and ev_env and str(ev_env) != environment_name:
            continue

        ts = _parse_utc(_attr(raw, "occurred_at"))
        if ts is None:
            continue

        parsed_events.append(
            {
                "timestamp": ts,
                "variation_key": str(_attr(raw, "variation_key", "unknown")),
                "event_type": str(_attr(raw, "event_type", "")),
                "user_identifier": str(_attr(raw, "user_identifier", "")),
                "properties": dict(_attr(raw, "properties") or {}),
            }
        )

    ref_now = _parse_utc(now) or (
        max(e["timestamp"] for e in parsed_events) if parsed_events else datetime.now(UTC)
    )
    window_start_cutoff = ref_now - timedelta(minutes=window_minutes)
    filtered_events = [
        e
        for e in parsed_events
        if window_start_cutoff <= e["timestamp"] <= ref_now
    ]

    # Bin into 5-minute buckets
    buckets: dict[datetime, list[dict[str, Any]]] = {}
    for ev in filtered_events:
        b_start = _floor_bin(ev["timestamp"], bin_minutes)
        buckets.setdefault(b_start, []).append(ev)

    sorted_bucket_starts = sorted(buckets.keys())
    window_list: list[FiveMinuteWindowMetrics] = []

    for b_start in sorted_bucket_starts:
        b_events = buckets[b_start]
        b_end = b_start + timedelta(minutes=bin_minutes)

        var_groups: dict[str, list[dict[str, Any]]] = {}
        for ev in b_events:
            var_groups.setdefault(ev["variation_key"], []).append(ev)

        var_metrics_map: dict[str, WindowVariationMetrics] = {}
        tot_exp = 0
        tot_started = 0
        tot_completed = 0
        tot_failed = 0
        tot_abandoned = 0

        for var_key, v_events in sorted(var_groups.items()):
            exp_users = {
                e["user_identifier"]
                for e in v_events
                if e["event_type"] == "feature_variation_assigned" and e["user_identifier"]
            }
            v_exp = len(exp_users) or sum(
                1 for e in v_events if e["event_type"] == "feature_variation_assigned"
            )
            v_started = sum(1 for e in v_events if e["event_type"] == "checkout_started")
            v_completed = sum(1 for e in v_events if e["event_type"] == "checkout_completed")
            v_failed = sum(1 for e in v_events if e["event_type"] == "payment_failed")
            v_abandoned = sum(1 for e in v_events if e["event_type"] == "checkout_abandoned")
            v_attempts = _compute_attempts(v_exp, v_started, v_completed, v_failed, v_abandoned)

            var_metrics_map[var_key] = WindowVariationMetrics(
                variation_key=var_key,
                exposures=v_exp,
                checkout_started=v_started,
                checkout_completed=v_completed,
                payment_failed=v_failed,
                checkout_abandoned=v_abandoned,
                attempts=v_attempts,
                payment_failed_rate=(v_failed / v_attempts) if v_attempts > 0 else 0.0,
                abandonment_rate=(v_abandoned / v_attempts) if v_attempts > 0 else 0.0,
            )

            tot_exp += v_exp
            tot_started += v_started
            tot_completed += v_completed
            tot_failed += v_failed
            tot_abandoned += v_abandoned

        tot_attempts = _compute_attempts(
            tot_exp, tot_started, tot_completed, tot_failed, tot_abandoned
        )
        window_list.append(
            FiveMinuteWindowMetrics(
                window_start=b_start,
                window_end=b_end,
                exposures=tot_exp,
                checkout_started=tot_started,
                checkout_completed=tot_completed,
                payment_failed=tot_failed,
                checkout_abandoned=tot_abandoned,
                attempts=tot_attempts,
                payment_failed_rate=(tot_failed / tot_attempts) if tot_attempts > 0 else 0.0,
                abandonment_rate=(tot_abandoned / tot_attempts) if tot_attempts > 0 else 0.0,
                by_variation=var_metrics_map,
            )
        )

    # Spike detection across 5-minute windows
    err_rates = [w.payment_failed_rate for w in window_list if w.attempts > 0]
    aband_rates = [w.abandonment_rate for w in window_list if w.attempts > 0]
    baseline_err = (
        float(median(sorted(err_rates)[: max(1, len(err_rates) // 2)]))
        if len(err_rates) >= 2
        else 0.0
    )
    baseline_aband = (
        float(median(sorted(aband_rates)[: max(1, len(aband_rates) // 2)]))
        if len(aband_rates) >= 2
        else 0.0
    )
    peak_err = max(err_rates) if err_rates else 0.0

    spike_onset_at: datetime | None = None
    peak_window_at: datetime | None = None
    spike_metric: str | None = None

    err_spike_windows: list[FiveMinuteWindowMetrics] = []
    for w in window_list:
        if w.attempts == 0:
            continue
        is_significant_count = w.payment_failed >= 2 or (
            w.payment_failed >= 1 and w.attempts <= 5 and w.payment_failed_rate >= 0.20
        )
        exceeds_threshold = w.payment_failed_rate >= max(
            spike_rate_threshold, baseline_err * 2.0
        ) and (w.payment_failed_rate - baseline_err >= 0.03 or w.payment_failed_rate >= spike_rate_threshold)
        if is_significant_count and exceeds_threshold:
            w.is_spike = True
            err_spike_windows.append(w)

    if err_spike_windows:
        spike_metric = "payment_failed"
        spike_onset_at = err_spike_windows[0].window_start
        peak_window = max(err_spike_windows, key=lambda w: (w.payment_failed_rate, w.payment_failed))
        peak_window_at = peak_window.window_start
    else:
        aband_spike_windows: list[FiveMinuteWindowMetrics] = []
        aband_threshold = max(spike_rate_threshold * 2.0, baseline_aband * 2.0)
        for w in window_list:
            if w.attempts == 0:
                continue
            if w.checkout_abandoned >= 2 and w.abandonment_rate >= aband_threshold:
                w.is_spike = True
                aband_spike_windows.append(w)
        if aband_spike_windows:
            spike_metric = "checkout_abandoned"
            spike_onset_at = aband_spike_windows[0].window_start
            peak_window = max(
                aband_spike_windows, key=lambda w: (w.abandonment_rate, w.checkout_abandoned)
            )
            peak_window_at = peak_window.window_start

    spike_detected = spike_onset_at is not None

    # Per-variation observed summaries
    var_summary_accum: dict[str, dict[str, Any]] = {}
    for w in window_list:
        for var_key, vm in w.by_variation.items():
            acc = var_summary_accum.setdefault(
                var_key,
                {
                    "exposures": 0,
                    "attempts": 0,
                    "payment_failed": 0,
                    "checkout_abandoned": 0,
                    "spike_attempts": 0,
                    "spike_payment_failed": 0,
                    "spike_abandoned": 0,
                },
            )
            acc["exposures"] += vm.exposures
            acc["attempts"] += vm.attempts
            acc["payment_failed"] += vm.payment_failed
            acc["checkout_abandoned"] += vm.checkout_abandoned
            if w.is_spike or not spike_detected:
                acc["spike_attempts"] += vm.attempts
                acc["spike_payment_failed"] += vm.payment_failed
                acc["spike_abandoned"] += vm.checkout_abandoned

    by_variation: dict[str, VariationObservedSummary] = {}
    for var_key, acc in sorted(var_summary_accum.items()):
        attempts = acc["attempts"]
        sp_attempts = acc["spike_attempts"]
        by_variation[var_key] = VariationObservedSummary(
            variation_key=var_key,
            exposures=acc["exposures"],
            attempts=attempts,
            payment_failed=acc["payment_failed"],
            checkout_abandoned=acc["checkout_abandoned"],
            payment_failed_rate=(acc["payment_failed"] / attempts) if attempts > 0 else 0.0,
            abandonment_rate=(acc["checkout_abandoned"] / attempts) if attempts > 0 else 0.0,
            spike_attempts=sp_attempts,
            spike_payment_failed=acc["spike_payment_failed"],
            spike_payment_failed_rate=(
                (acc["spike_payment_failed"] / sp_attempts) if sp_attempts > 0 else 0.0
            ),
            spike_abandonment_rate=(
                (acc["spike_abandoned"] / sp_attempts) if sp_attempts > 0 else 0.0
            ),
        )

    total_exposures = sum(w.exposures for w in window_list)
    total_attempts = sum(w.attempts for w in window_list)
    total_failed = sum(w.payment_failed for w in window_list)
    total_abandoned = sum(w.checkout_abandoned for w in window_list)

    observed_metrics = ObservedMetrics(
        flag_key=flag_key,
        environment_name=environment_name,
        bin_minutes=bin_minutes,
        window_minutes=window_minutes,
        total_exposures=total_exposures,
        total_attempts=total_attempts,
        total_payment_failed=total_failed,
        total_checkout_abandoned=total_abandoned,
        overall_payment_failed_rate=(total_failed / total_attempts) if total_attempts > 0 else 0.0,
        overall_abandonment_rate=(total_abandoned / total_attempts) if total_attempts > 0 else 0.0,
        baseline_error_rate=baseline_err,
        peak_error_rate=peak_err,
        spike_detected=spike_detected,
        spike_metric=spike_metric,
        spike_onset_at=spike_onset_at,
        peak_window_at=peak_window_at,
        by_variation=by_variation,
        windows=window_list,
    )

    # Correlate against rollout_history and pull_requests within +/-30m of spike_onset_at
    correlations: list[TemporalCorrelation] = []
    if spike_onset_at is not None:
        max_delta_secs = correlation_window_minutes * 60

        for rh in rollout_history:
            rh_env = _attr(rh, "environment_name")
            if environment_name and rh_env and str(rh_env) != environment_name:
                continue
            rh_ts = _parse_utc(_attr(rh, "created_at"))
            if rh_ts is None:
                continue
            if abs((rh_ts - spike_onset_at).total_seconds()) > max_delta_secs:
                continue

            rh_flag_id = _attr(rh, "feature_flag_id")
            rh_flag = _attr(rh, "flag_key")
            if not rh_flag and rh_flag_id is not None and flag_id_to_key:
                rh_flag = flag_id_to_key.get(str(rh_flag_id))
            same_flag = True
            if flag_key and rh_flag:
                same_flag = str(rh_flag) == flag_key
            elif feature_flag_id and rh_flag_id is not None:
                same_flag = str(rh_flag_id) == str(feature_flag_id)
            weight = 1.0 if same_flag else 0.65
            score, delta_min, relation = _score_temporal_proximity(
                event_ts=rh_ts,
                onset_ts=spike_onset_at,
                correlation_window_minutes=correlation_window_minutes,
                source_weight=weight,
            )
            action = str(_attr(rh, "action", "rollout_step"))
            correlations.append(
                TemporalCorrelation(
                    source_type="rollout_history",
                    event_id=str(_attr(rh, "id", "")),
                    title=f"Flag rollout {action} in {rh_env or 'unknown'}",
                    timestamp=rh_ts,
                    delta_minutes_from_onset=delta_min,
                    relation=relation,
                    temporal_score=score,
                    details={
                        "flag_key": rh_flag,
                        "feature_flag_id": str(rh_flag_id) if rh_flag_id is not None else None,
                        "environment_name": rh_env,
                        "action": action,
                        "previous_state": _attr(rh, "previous_state", {}),
                        "new_state": _attr(rh, "new_state", {}),
                        "reason": _attr(rh, "reason"),
                    },
                    correlation_not_causation=True,
                )
            )

        for pr in pull_requests:
            pr_ts = (
                _parse_utc(_attr(pr, "merged_at"))
                or _parse_utc(_attr(pr, "updated_at"))
                or _parse_utc(_attr(pr, "created_at"))
            )
            if pr_ts is None:
                continue
            if abs((pr_ts - spike_onset_at).total_seconds()) > max_delta_secs:
                continue

            pr_state = str(_attr(pr, "state", "open")).lower()
            weight = 0.85 if pr_state == "merged" else 0.65
            score, delta_min, relation = _score_temporal_proximity(
                event_ts=pr_ts,
                onset_ts=spike_onset_at,
                correlation_window_minutes=correlation_window_minutes,
                source_weight=weight,
            )
            pr_num = _attr(pr, "github_pr_number", "")
            pr_title = str(_attr(pr, "title", "Pull Request"))
            correlations.append(
                TemporalCorrelation(
                    source_type="pull_request",
                    event_id=str(_attr(pr, "id", pr_num)),
                    title=f"PR #{pr_num}: {pr_title}" if pr_num != "" else pr_title,
                    timestamp=pr_ts,
                    delta_minutes_from_onset=delta_min,
                    relation=relation,
                    temporal_score=score,
                    details={
                        "github_pr_number": pr_num,
                        "state": pr_state,
                        "head_branch": _attr(pr, "head_branch"),
                        "risk_level": _attr(pr, "risk_level"),
                        "html_url": _attr(pr, "html_url"),
                    },
                    correlation_not_causation=True,
                )
            )

        correlations.sort(key=lambda c: (-c.temporal_score, abs(c.delta_minutes_from_onset)))

    spike_window_starts = {w.window_start for w in window_list if w.is_spike}
    failed_during_spike = [
        e["properties"]
        for e in filtered_events
        if e["event_type"] == "payment_failed"
        and _floor_bin(e["timestamp"], bin_minutes) in spike_window_starts
    ]

    confounding_checks, external_confounder = _build_confounding_checks(
        by_variation=by_variation,
        spike_detected=spike_detected,
        spike_metric=spike_metric,
        correlations=correlations,
        failed_events_during_spike=failed_during_spike,
    )

    next_steps: list[str] = []
    if not spike_detected:
        next_steps.append(
            "No error or abandonment spike detected in the analyzed window; continue routine monitoring."
        )
    elif external_confounder:
        next_steps.append(
            "Investigate shared dependencies (e.g., payment gateway, database, upstream API) before rolling back the feature flag, as both control and treatment spiked."
        )
        next_steps.append(
            "Review correlated rollout and PR timelines as temporal context only (correlation != causation)."
        )
    else:
        next_steps.append(
            "Inspect failing transaction logs and concentrated request attributes (e.g., promo code or payment method) for treatment vs control."
        )
        next_steps.append(
            "Verify live Flagsmith state and consider proposing a Category C rollback approval if treatment-specific errors persist in production."
        )

    return IncidentInvestigationResult(
        flag_key=flag_key,
        environment_name=environment_name,
        correlation_not_causation=True,
        observed_metrics=observed_metrics,
        temporal_correlations=correlations,
        confounding_checks=confounding_checks,
        recommended_next_steps=next_steps,
    )
