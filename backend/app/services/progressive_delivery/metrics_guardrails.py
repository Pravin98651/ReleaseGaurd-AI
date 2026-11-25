"""Metric Collection and Guardrail Evaluation Services for Progressive Delivery."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import ensure_utc, utcnow
from app.models import (
    ExperimentEvent,
    ExperimentEventType,
    GuardrailEvalStatus,
    GuardrailEvaluation,
    GuardrailOperator,
    ProgressiveRollout,
    ProgressiveRolloutStage,
    RolloutGuardrail,
    RolloutMetricSnapshot,
)


class MetricCollectionService:
    """Collects actual ExperimentEvent telemetry for a rollout stage with freshness checks."""

    @staticmethod
    async def collect_stage_metrics(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        stage: ProgressiveRolloutStage,
        *,
        window_minutes: int = 60,
    ) -> list[RolloutMetricSnapshot]:
        now = utcnow()
        window_start = ensure_utc(stage.observation_started_at) or (
            now - timedelta(minutes=window_minutes)
        )
        cond = (ExperimentEvent.flag_key == rollout.feature_flag_key) & (
            ExperimentEvent.environment_name == rollout.environment
        )
        if rollout.experiment_id is not None:
            cond = or_(ExperimentEvent.experiment_id == rollout.experiment_id, cond)

        events = list(
            (
                await session.execute(
                    select(ExperimentEvent)
                    .where(cond, ExperimentEvent.occurred_at >= window_start)
                    .order_by(ExperimentEvent.occurred_at.asc())
                )
            )
            .scalars()
            .all()
        )

        obs_cfg = rollout.observation_configuration or {}
        max_age_sec = int(obs_cfg.get("maximum_metric_age_seconds", 180))
        if not events:
            return []

        latest_ts = max(
            (ensure_utc(ev.occurred_at) for ev in events if ev.occurred_at is not None),
            default=None,
        )
        freshness = "fresh"
        if latest_ts is not None and (now - latest_ts).total_seconds() > max_age_sec:
            freshness = "stale"

        exposures = sum(
            1 for e in events if e.event_type == ExperimentEventType.FEATURE_VARIATION_ASSIGNED
        )
        started = sum(1 for e in events if e.event_type == ExperimentEventType.CHECKOUT_STARTED)
        denom = max(exposures, started, len(events))
        completed = sum(
            1 for e in events if e.event_type == ExperimentEventType.CHECKOUT_COMPLETED
        )
        pay_failed = sum(1 for e in events if e.event_type == ExperimentEventType.PAYMENT_FAILED)
        abandoned = sum(
            1 for e in events if e.event_type == ExperimentEventType.CHECKOUT_ABANDONED
        )

        stage.actual_sample_size = denom

        snapshots: list[RolloutMetricSnapshot] = [
            RolloutMetricSnapshot(
                rollout_id=rollout.id,
                stage_id=stage.id,
                metric_key="checkout_completed",
                metric_value=round(completed / denom, 4),
                sample_size=denom,
                numerator=float(completed),
                denominator=float(denom),
                unit="rate",
                window_start=window_start,
                window_end=now,
                data_source="experiment_events",
                freshness_status=freshness,
            ),
            RolloutMetricSnapshot(
                rollout_id=rollout.id,
                stage_id=stage.id,
                metric_key="payment_failed",
                metric_value=round(pay_failed / denom, 4),
                sample_size=denom,
                numerator=float(pay_failed),
                denominator=float(denom),
                unit="rate",
                window_start=window_start,
                window_end=now,
                data_source="experiment_events",
                freshness_status=freshness,
            ),
            RolloutMetricSnapshot(
                rollout_id=rollout.id,
                stage_id=stage.id,
                metric_key="checkout_abandoned",
                metric_value=round(abandoned / denom, 4),
                sample_size=denom,
                numerator=float(abandoned),
                denominator=float(denom),
                unit="rate",
                window_start=window_start,
                window_end=now,
                data_source="experiment_events",
                freshness_status=freshness,
            ),
        ]

        # Collect latency/API error metrics ONLY when present in event properties
        latencies = [
            float((e.properties or {}).get("latency_ms"))
            for e in events
            if isinstance(e.properties, dict) and e.properties.get("latency_ms") is not None
        ]
        if latencies:
            latencies.sort()
            p95_idx = min(len(latencies) - 1, int(0.95 * (len(latencies) - 1)))
            snapshots.append(
                RolloutMetricSnapshot(
                    rollout_id=rollout.id,
                    stage_id=stage.id,
                    metric_key="checkout_latency_p95",
                    metric_value=round(latencies[p95_idx], 2),
                    sample_size=len(latencies),
                    unit="ms",
                    window_start=window_start,
                    window_end=now,
                    data_source="experiment_events_metadata",
                    freshness_status=freshness,
                )
            )

        for snap in snapshots:
            session.add(snap)
        await session.flush()
        return snapshots


class GuardrailEvaluationService:
    """Evaluates configured RolloutGuardrail rules against collected metric snapshots."""

    @staticmethod
    def _compare(op: GuardrailOperator, observed: float, threshold: float) -> bool:
        if op == GuardrailOperator.GREATER_THAN:
            return observed > threshold
        if op == GuardrailOperator.GREATER_THAN_OR_EQUAL:
            return observed >= threshold
        if op == GuardrailOperator.LESS_THAN:
            return observed < threshold
        if op == GuardrailOperator.LESS_THAN_OR_EQUAL:
            return observed <= threshold
        if op == GuardrailOperator.EQUALS:
            return observed == threshold
        if op == GuardrailOperator.NOT_EQUAL:
            return observed != threshold
        if op in {
            GuardrailOperator.RATE_INCREASE,
            GuardrailOperator.PERCENTAGE_CHANGE,
            GuardrailOperator.ABSOLUTE_CHANGE,
        }:
            return observed > threshold
        return False

    @staticmethod
    async def evaluate_guardrails(
        session: AsyncSession,
        rollout: ProgressiveRollout,
        stage: ProgressiveRolloutStage,
        snapshots: list[RolloutMetricSnapshot],
        guardrails: list[RolloutGuardrail],
    ) -> list[GuardrailEvaluation]:
        by_key = {s.metric_key: s for s in snapshots}
        evals: list[GuardrailEvaluation] = []

        for gr in guardrails:
            if not gr.enabled:
                continue
            snap = by_key.get(gr.metric_key)
            if snap is None or snap.sample_size < gr.minimum_sample_size:
                ev = GuardrailEvaluation(
                    rollout_id=rollout.id,
                    stage_id=stage.id,
                    guardrail_id=gr.id,
                    observed_value=snap.metric_value if snap else None,
                    threshold=gr.threshold,
                    sample_size=snap.sample_size if snap else 0,
                    status=GuardrailEvalStatus.INSUFFICIENT_DATA,
                    severity=gr.severity,
                    evidence={
                        "reason": f"Sample size {snap.sample_size if snap else 0} < minimum {gr.minimum_sample_size}."
                    },
                )
            elif snap.freshness_status == "stale":
                ev = GuardrailEvaluation(
                    rollout_id=rollout.id,
                    stage_id=stage.id,
                    guardrail_id=gr.id,
                    observed_value=snap.metric_value,
                    threshold=gr.threshold,
                    sample_size=snap.sample_size,
                    status=GuardrailEvalStatus.STALE_DATA,
                    severity=gr.severity,
                    evidence={"reason": "Latest telemetry exceeded maximum metric age."},
                )
            else:
                breached = GuardrailEvaluationService._compare(
                    gr.operator, snap.metric_value, gr.threshold
                )
                if breached:
                    gr.current_breach_streak += 1
                    status = (
                        GuardrailEvalStatus.BREACH
                        if gr.current_breach_streak >= gr.consecutive_breach_count
                        else GuardrailEvalStatus.WARNING
                    )
                else:
                    gr.current_breach_streak = 0
                    status = GuardrailEvalStatus.PASS
                ev = GuardrailEvaluation(
                    rollout_id=rollout.id,
                    stage_id=stage.id,
                    guardrail_id=gr.id,
                    observed_value=snap.metric_value,
                    threshold=gr.threshold,
                    sample_size=snap.sample_size,
                    status=status,
                    severity=gr.severity,
                    evidence={
                        "operator": gr.operator.value,
                        "streak": gr.current_breach_streak,
                        "required_streak": gr.consecutive_breach_count,
                        "data_source": snap.data_source,
                    },
                )

            session.add(ev)
            evals.append(ev)

        await session.flush()
        return evals
