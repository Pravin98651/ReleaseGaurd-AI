"""Deterministic shopper journey simulator generating real telemetry batches."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from app.flagsmith_service import FlagsmithService
from app.schemas import SimulateRequest, SimulateResponse, VariantMetrics
from app.telemetry import TelemetryEmitter, build_deterministic_event_id

# (conversion_rate, payment_failed_rate)
_SCENARIO_RATES: dict[str, dict[str, tuple[float, float]]] = {
    "healthy_v2": {
        "v1": (0.12, 0.012),
        "v2": (0.20, 0.012),
    },
    "winning_variant": {
        "v1": (0.12, 0.012),
        "v2": (0.23, 0.011),
    },
    "healthy_control": {
        "v1": (0.20, 0.010),
        "v2": (0.11, 0.015),
    },
    "underperforming_variant": {
        "v1": (0.18, 0.012),
        "v2": (0.09, 0.016),
    },
    "buggy_v2": {
        "v1": (0.12, 0.012),
        "v2": (0.09, 0.145),
    },
    "guardrail_breach": {
        "v1": (0.12, 0.012),
        "v2": (0.14, 0.155),
    },
    "neutral": {
        "v1": (0.12, 0.012),
        "v2": (0.125, 0.013),
    },
    "insufficient_sample": {
        "v1": (0.12, 0.012),
        "v2": (0.25, 0.012),
    },
}


@dataclass
class _VariantAccumulator:
    variation_key: str
    exposures: int = 0
    started: int = 0
    completed: int = 0
    abandoned: int = 0
    payment_failed: int = 0
    revenue_total: float = 0.0

    def to_metrics(self) -> VariantMetrics:
        conv = round(self.completed / self.exposures, 4) if self.exposures else 0.0
        err = round(self.payment_failed / self.exposures, 4) if self.exposures else 0.0
        aov = round(self.revenue_total / self.completed, 2) if self.completed else 0.0
        return VariantMetrics(
            variation_key=self.variation_key,
            exposures=self.exposures,
            started=self.started,
            completed=self.completed,
            abandoned=self.abandoned,
            payment_failed=self.payment_failed,
            conversion_rate=conv,
            error_rate=err,
            avg_order_value=aov,
        )


def _build_cohort_outcomes(
    count: int,
    conv_rate: float,
    fail_rate: float,
    rng: random.Random,
) -> list[str]:
    """Generate stratified deterministic outcomes so cohort proportions stay realistic."""
    if count <= 0:
        return []
    fail_count = max(0, min(count, round(count * fail_rate)))
    if fail_rate > 0.05 and count >= 10:
        fail_count = max(fail_count, 2)
    conv_count = max(0, min(count - fail_count, round(count * conv_rate)))
    abandon_count = max(0, count - fail_count - conv_count)

    outcomes = (
        ["payment_failed"] * fail_count
        + ["checkout_completed"] * conv_count
        + ["checkout_abandoned"] * abandon_count
    )
    rng.shuffle(outcomes)
    return outcomes


async def run_simulation(
    *,
    req: SimulateRequest,
    flagsmith_service: FlagsmithService,
    telemetry: TelemetryEmitter,
    default_environment: str,
) -> SimulateResponse:
    """Run deterministic shopper journeys and emit real event batches to ReleaseGuard."""
    env_name = req.environment_name or default_environment
    rng = random.Random(req.seed)
    rates = _SCENARIO_RATES[req.scenario]
    effective_users = min(req.users_count, 40) if req.scenario == "insufficient_sample" else req.users_count
    execution_logs: list[str] = [
        f"Starting synthetic simulation scenario={req.scenario} seed={req.seed} "
        f"start_index={req.start_index} target_users={effective_users} env={env_name}"
    ]

    use_live_flagsmith = (
        req.split_mode != "simulated_50_50" and flagsmith_service.is_configured
    )
    evaluation_mode: Literal["flagsmith_live", "simulated_split"] = (
        "flagsmith_live" if use_live_flagsmith else "simulated_split"
    )

    live_connected = flagsmith_service.flagsmith_connected
    assignments: list[tuple[str, Literal["v1", "v2"], str]] = []

    end_index = req.start_index + effective_users
    for idx in range(req.start_index, end_index):
        user_id = f"sim_{req.scenario}_{req.seed}_{idx:04d}"
        if use_live_flagsmith:
            eval_res = await flagsmith_service.evaluate_checkout_flag(user_id=user_id)
            live_connected = eval_res.flagsmith_connected
            if not eval_res.flagsmith_connected and req.split_mode == "auto" and idx == req.start_index:
                use_live_flagsmith = False
                evaluation_mode = "simulated_split"
                checkout_version: Literal["v1", "v2"] = "v2" if idx % 2 == 0 else "v1"
                variation_key = "treatment" if checkout_version == "v2" else "control"
                execution_logs.append("Flagsmith unreachable on first identity; switched to simulated_split fallback.")
            else:
                checkout_version = eval_res.checkout_version
                variation_key = eval_res.variation_key
        else:
            checkout_version = "v2" if idx % 2 == 0 else "v1"
            variation_key = "treatment" if checkout_version == "v2" else "control"
        assignments.append((user_id, checkout_version, variation_key))

    v1_count = sum(1 for _, ver, _ in assignments if ver == "v1")
    v2_count = len(assignments) - v1_count
    execution_logs.append(f"Assigned {len(assignments)} identities via {evaluation_mode}: v1={v1_count}, v2={v2_count}.")

    v1_outcomes = _build_cohort_outcomes(v1_count, rates["v1"][0], rates["v1"][1], rng)
    v2_outcomes = _build_cohort_outcomes(v2_count, rates["v2"][0], rates["v2"][1], rng)
    v1_iter = iter(v1_outcomes)
    v2_iter = iter(v2_outcomes)

    control_acc = _VariantAccumulator(variation_key="control")
    treatment_acc = _VariantAccumulator(variation_key="treatment")
    all_events: list[dict] = []
    now = datetime.now(UTC)

    for offset, (user_id, checkout_version, variation_key) in enumerate(assignments, start=1):
        session_key = f"sim_{req.scenario}_{req.seed}_{user_id}"
        cart_total = round(rng.uniform(38.0, 249.0), 2)

        if checkout_version == "v1":
            acc = control_acc
            acc.variation_key = variation_key
            outcome_event = next(v1_iter)
        else:
            acc = treatment_acc
            acc.variation_key = variation_key
            outcome_event = next(v2_iter)

        if outcome_event == "payment_failed":
            error_code = (
                "ERR_V2_PROMO_WALLET_GATEWAY_502"
                if checkout_version == "v2" and req.scenario in ("buggy_v2", "guardrail_breach")
                else "ERR_CARD_DECLINED"
            )
            promo_code = (
                "PROMO-BUG"
                if checkout_version == "v2" and req.scenario in ("buggy_v2", "guardrail_breach")
                else "NOVA10"
            )
        elif outcome_event == "checkout_completed":
            error_code = None
            promo_code = "EXPRESS15" if checkout_version == "v2" and rng.random() < 0.35 else None
        else:
            error_code = None
            promo_code = None

        # Spread simulated events across the last 25 minutes for realistic time-series binning.
        seconds_ago = int(((effective_users - offset) / max(effective_users, 1)) * 1500)
        base_time = now - timedelta(seconds=seconds_ago)

        acc.exposures += 1
        acc.started += 1
        if outcome_event == "checkout_completed":
            acc.completed += 1
            acc.revenue_total += cart_total
        elif outcome_event == "payment_failed":
            acc.payment_failed += 1
        else:
            acc.abandoned += 1

        payment_method = (
            rng.choice(["apple_pay", "novapay_wallet", "card"])
            if checkout_version == "v2"
            else "card"
        )

        common_props = {
            "simulated": True,
            "synthetic_test_traffic": True,
            "source": "novashop_simulator",
            "scenario": req.scenario,
            "seed": req.seed,
            "checkout_version": checkout_version,
            "evaluation_mode": evaluation_mode,
            "payment_method": payment_method,
            "promo_code": promo_code,
        }

        all_events.extend(
            [
                telemetry.build_event(
                    event_type="feature_variation_assigned",
                    user_id=user_id,
                    variation_key=variation_key,
                    environment_name=env_name,
                    session_key=session_key,
                    numeric_value=cart_total,
                    properties={**common_props},
                    occurred_at=base_time,
                    event_id=build_deterministic_event_id(
                        flag_key=flagsmith_service.flag_key,
                        event_type="feature_variation_assigned",
                        user_id=user_id,
                        session_key=session_key,
                    ),
                ),
                telemetry.build_event(
                    event_type="checkout_started",
                    user_id=user_id,
                    variation_key=variation_key,
                    environment_name=env_name,
                    session_key=session_key,
                    numeric_value=cart_total,
                    properties={**common_props},
                    occurred_at=base_time + timedelta(seconds=8),
                    event_id=build_deterministic_event_id(
                        flag_key=flagsmith_service.flag_key,
                        event_type="checkout_started",
                        user_id=user_id,
                        session_key=session_key,
                    ),
                ),
                telemetry.build_event(
                    event_type=outcome_event,
                    user_id=user_id,
                    variation_key=variation_key,
                    environment_name=env_name,
                    session_key=session_key,
                    numeric_value=cart_total,
                    properties={**common_props, "error_code": error_code},
                    occurred_at=base_time + timedelta(seconds=24),
                    event_id=build_deterministic_event_id(
                        flag_key=flagsmith_service.flag_key,
                        event_type=outcome_event,
                        user_id=user_id,
                        session_key=session_key,
                    ),
                ),
            ]
        )

    ingest_summary = await telemetry.emit_events(all_events)
    execution_logs.append(
        f"Emitted {len(all_events)} events: accepted={ingest_summary['accepted']}, "
        f"deduplicated={ingest_summary['deduplicated']}, failed_batches={ingest_summary['failed_batches']}."
    )

    return SimulateResponse(
        synthetic_test_traffic=True,
        scenario=req.scenario,
        users_count=effective_users,
        seed=req.seed,
        start_index=req.start_index,
        evaluation_mode=evaluation_mode,
        flagsmith_connected=live_connected if evaluation_mode == "flagsmith_live" else False,
        environment_name=env_name,
        flag_key=flagsmith_service.flag_key,
        events_generated=len(all_events),
        batches_sent=ingest_summary["batches_sent"],
        ingest_accepted=ingest_summary["accepted"],
        ingest_deduplicated=ingest_summary["deduplicated"],
        ingest_failed_batches=ingest_summary["failed_batches"],
        ingest_error=ingest_summary["error"],
        control=control_acc.to_metrics(),
        treatment=treatment_acc.to_metrics(),
        execution_logs=execution_logs,
        sample_events=all_events[:5],
    )
