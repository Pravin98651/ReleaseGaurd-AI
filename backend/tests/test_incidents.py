"""Standalone tests for backend/app/analytics/incidents.py."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.analytics.incidents import investigate_incident


def _make_window_events(
    *,
    start: datetime,
    variation: str,
    completed: int,
    failed: int,
    abandoned: int = 0,
    properties: dict | None = None,
) -> list[dict]:
    events: list[dict] = []
    idx = 0
    for _ in range(completed):
        idx += 1
        uid = f"{variation}_ok_{start.timestamp()}_{idx}"
        events.append(
            {
                "event_id": f"e_{uid}_assign",
                "flag_key": "checkout_v2",
                "environment_name": "production",
                "variation_key": variation,
                "event_type": "feature_variation_assigned",
                "user_identifier": uid,
                "occurred_at": start + timedelta(seconds=idx),
            }
        )
        events.append(
            {
                "event_id": f"e_{uid}_done",
                "flag_key": "checkout_v2",
                "environment_name": "production",
                "variation_key": variation,
                "event_type": "checkout_completed",
                "user_identifier": uid,
                "occurred_at": start + timedelta(seconds=idx + 1),
            }
        )
    for _ in range(failed):
        idx += 1
        uid = f"{variation}_fail_{start.timestamp()}_{idx}"
        events.append(
            {
                "event_id": f"e_{uid}_assign",
                "flag_key": "checkout_v2",
                "environment_name": "production",
                "variation_key": variation,
                "event_type": "feature_variation_assigned",
                "user_identifier": uid,
                "occurred_at": start + timedelta(seconds=idx),
            }
        )
        events.append(
            {
                "event_id": f"e_{uid}_fail",
                "flag_key": "checkout_v2",
                "environment_name": "production",
                "variation_key": variation,
                "event_type": "payment_failed",
                "user_identifier": uid,
                "properties": properties or {},
                "occurred_at": start + timedelta(seconds=idx + 1),
            }
        )
    for _ in range(abandoned):
        idx += 1
        uid = f"{variation}_ab_{start.timestamp()}_{idx}"
        events.append(
            {
                "event_id": f"e_{uid}_assign",
                "flag_key": "checkout_v2",
                "environment_name": "production",
                "variation_key": variation,
                "event_type": "feature_variation_assigned",
                "user_identifier": uid,
                "occurred_at": start + timedelta(seconds=idx),
            }
        )
        events.append(
            {
                "event_id": f"e_{uid}_ab",
                "flag_key": "checkout_v2",
                "environment_name": "production",
                "variation_key": variation,
                "event_type": "checkout_abandoned",
                "user_identifier": uid,
                "occurred_at": start + timedelta(seconds=idx + 1),
            }
        )
    return events


def test_incident_bins_5m_windows_correlates_within_30m_and_detects_v2_spike() -> None:
    t0 = datetime(2026, 11, 25, 14, 0, tzinfo=UTC)
    events: list[dict] = []

    # 3 healthy baseline 5-minute windows (14:00, 14:05, 14:10)
    for m in (0, 5, 10):
        events.extend(
            _make_window_events(start=t0 + timedelta(minutes=m), variation="v1", completed=20, failed=0)
        )
        events.extend(
            _make_window_events(start=t0 + timedelta(minutes=m), variation="v2", completed=20, failed=0)
        )

    # Spike onset at 14:15 ONLY in v2 (8/20 = 40% payment_failed) with concentrated promo_code
    events.extend(
        _make_window_events(start=t0 + timedelta(minutes=15), variation="v1", completed=20, failed=0)
    )
    events.extend(
        _make_window_events(
            start=t0 + timedelta(minutes=15),
            variation="v2",
            completed=12,
            failed=8,
            properties={"promo_code": "PROMO-BUG", "error_code": "CARD_TOKEN_ERR"},
        )
    )

    rollouts = [
        {
            "id": "rh-near",
            "flag_key": "checkout_v2",
            "environment_name": "production",
            "action": "rollout_step",
            "previous_state": {"percentage": 10},
            "new_state": {"percentage": 50},
            "created_at": t0 + timedelta(minutes=12),  # 3m before onset -> high score
        },
        {
            "id": "rh-far",
            "flag_key": "checkout_v2",
            "environment_name": "production",
            "action": "rollout_step",
            "created_at": t0 - timedelta(minutes=45),  # 60m before onset -> outside +-30m
        },
    ]
    prs = [
        {
            "id": "pr-1",
            "github_pr_number": 42,
            "title": "Enable promo code on checkout v2",
            "state": "merged",
            "merged_at": t0 + timedelta(minutes=8),  # 7m before onset
        }
    ]

    result = investigate_incident(
        events=events,
        rollout_history=rollouts,
        pull_requests=prs,
        flag_key="checkout_v2",
        environment_name="production",
        window_minutes=60,
        now=t0 + timedelta(minutes=25),
    )

    assert result.correlation_not_causation is True
    assert result.observed_metrics.spike_detected is True
    assert result.observed_metrics.spike_metric == "payment_failed"
    assert result.observed_metrics.spike_onset_at == t0 + timedelta(minutes=15)
    assert len(result.observed_metrics.windows) == 4

    # Only events within +-30m appear, ranked with closest preceding rollout first
    corr_ids = [c.event_id for c in result.temporal_correlations]
    assert corr_ids == ["rh-near", "pr-1"]
    assert all(c.correlation_not_causation is True for c in result.temporal_correlations)
    assert result.temporal_correlations[0].temporal_score > result.temporal_correlations[1].temporal_score

    # Confounding checks: variation_isolated + concurrent_changes + promo_code concentration
    checks_by_name = {c.check_name: c for c in result.confounding_checks}
    assert checks_by_name["variation_stratification"].status == "variation_isolated"
    assert checks_by_name["variation_stratification"].confounder_detected is False
    assert checks_by_name["concurrent_changes"].confounder_detected is True
    assert checks_by_name["property_concentration_promo_code"].evidence["dominant_value"] == "PROMO-BUG"


def test_confounding_check_flags_gateway_outage_when_v1_and_v2_both_spike() -> None:
    t0 = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)
    events: list[dict] = []

    for m in (0, 5):
        events.extend(
            _make_window_events(start=t0 + timedelta(minutes=m), variation="v1", completed=25, failed=0)
        )
        events.extend(
            _make_window_events(start=t0 + timedelta(minutes=m), variation="v2", completed=25, failed=0)
        )

    # Both v1 and v2 spike at 15:10 (~25% payment_failed in BOTH)
    events.extend(
        _make_window_events(start=t0 + timedelta(minutes=10), variation="v1", completed=15, failed=5)
    )
    events.extend(
        _make_window_events(start=t0 + timedelta(minutes=10), variation="v2", completed=15, failed=5)
    )

    result = investigate_incident(
        events=events,
        rollout_history=[
            {
                "id": "rh-1",
                "flag_key": "checkout_v2",
                "environment_name": "production",
                "action": "rollout_step",
                "created_at": t0 + timedelta(minutes=7),
            }
        ],
        flag_key="checkout_v2",
        environment_name="production",
        now=t0 + timedelta(minutes=20),
    )

    assert result.correlation_not_causation is True
    strat_check = next(
        c for c in result.confounding_checks if c.check_name == "variation_stratification"
    )
    assert strat_check.status == "likely_external_confounder"
    assert strat_check.confounder_detected is True
    assert "gateway" in strat_check.summary.lower()
    assert any("shared dependencies" in step.lower() for step in result.recommended_next_steps)


def test_incident_no_spike_returns_clean_result_with_causal_disclaimer() -> None:
    t0 = datetime(2026, 11, 25, 16, 0, tzinfo=UTC)
    events = _make_window_events(start=t0, variation="v1", completed=20, failed=0) + _make_window_events(
        start=t0, variation="v2", completed=20, failed=0
    )
    result = investigate_incident(
        events=events,
        flag_key="checkout_v2",
        environment_name="staging",
        now=t0 + timedelta(minutes=10),
    )
    assert result.correlation_not_causation is True
    assert result.observed_metrics.spike_detected is False
    assert result.temporal_correlations == []
    assert result.confounding_checks[0].status == "no_spike_detected"


def test_abandonment_spike_and_after_onset_ranking() -> None:
    t0 = datetime(2026, 11, 25, 17, 0, tzinfo=UTC)
    events: list[dict] = []
    events.extend(_make_window_events(start=t0, variation="v1", completed=20, failed=0, abandoned=1))
    events.extend(_make_window_events(start=t0, variation="v2", completed=20, failed=0, abandoned=1))
    # Abandonment spike at 17:10 in v2
    events.extend(
        _make_window_events(start=t0 + timedelta(minutes=10), variation="v1", completed=20, failed=0, abandoned=1)
    )
    events.extend(
        _make_window_events(start=t0 + timedelta(minutes=10), variation="v2", completed=8, failed=0, abandoned=12)
    )

    rollouts = [
        {
            "id": "rh-after",
            "flag_key": "checkout_v2",
            "environment_name": "production",
            "action": "rollout_step",
            "created_at": t0 + timedelta(minutes=20),  # 10m AFTER onset
        },
        {
            "id": "rh-before",
            "flag_key": "checkout_v2",
            "environment_name": "production",
            "action": "rollout_step",
            "created_at": t0 + timedelta(minutes=8),  # 2m BEFORE onset
        },
    ]
    result = investigate_incident(
        events=events,
        rollout_history=rollouts,
        flag_key="checkout_v2",
        environment_name="production",
        now=t0 + timedelta(minutes=25),
    )
    assert result.correlation_not_causation is True
    assert result.observed_metrics.spike_detected is True
    assert result.observed_metrics.spike_metric == "checkout_abandoned"
    assert [c.event_id for c in result.temporal_correlations] == ["rh-before", "rh-after"]
    assert result.temporal_correlations[0].relation == "before_onset"
    assert result.temporal_correlations[1].relation == "after_onset"
    assert result.temporal_correlations[0].temporal_score > result.temporal_correlations[1].temporal_score
    strat_check = next(
        c for c in result.confounding_checks if c.check_name == "variation_stratification"
    )
    assert strat_check.status == "variation_isolated"


def test_rollout_history_matches_by_feature_flag_id_when_flag_key_absent() -> None:
    t0 = datetime(2026, 11, 25, 18, 0, tzinfo=UTC)
    events = _make_window_events(start=t0, variation="v1", completed=20, failed=0) + _make_window_events(
        start=t0 + timedelta(minutes=10), variation="v2", completed=10, failed=5
    )
    rollouts = [
        {
            "id": "rh-other-flag",
            "feature_flag_id": "flag-uuid-999",
            "environment_name": "production",
            "action": "rollout_step",
            "created_at": t0 + timedelta(minutes=8),
        },
        {
            "id": "rh-target-flag",
            "feature_flag_id": "flag-uuid-123",
            "environment_name": "production",
            "action": "rollout_step",
            "created_at": t0 + timedelta(minutes=8),
        },
    ]
    result = investigate_incident(
        events=events,
        rollout_history=rollouts,
        flag_key="checkout_v2",
        feature_flag_id="flag-uuid-123",
        flag_id_to_key={"flag-uuid-123": "checkout_v2", "flag-uuid-999": "other_flag"},
        environment_name="production",
        now=t0 + timedelta(minutes=20),
    )
    assert result.correlation_not_causation is True
    assert result.temporal_correlations[0].event_id == "rh-target-flag"
    assert result.temporal_correlations[0].details["flag_key"] == "checkout_v2"
    assert result.temporal_correlations[0].temporal_score > result.temporal_correlations[1].temporal_score
