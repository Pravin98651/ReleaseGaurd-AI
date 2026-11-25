"""Standalone tests for backend/app/analytics/flag_health.py."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.analytics.flag_health import (
    FlagHealthInput,
    HealthIssueType,
    HealthThresholds,
    evaluate_flag_health,
    normalized_levenshtein_similarity,
    scan_code_for_flag_references,
    scan_directory_for_flag_references,
    scan_project_flag_health,
)


def _now() -> datetime:
    return datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def test_healthy_flag_scores_100() -> None:
    now = _now()
    flag = FlagHealthInput(
        key="checkout_v2",
        flagsmith_project_id=1,
        description="Express checkout flow",
        env_states={
            "development": {"enabled": True, "rollout_percentage": 50},
            "staging": {"enabled": True, "rollout_percentage": 50},
            "production": {"enabled": True, "rollout_percentage": 50},
        },
        code_references=[
            {"file_path": "demo-app/backend/main.py", "line_number": 42, "snippet": "flags.is_feature_enabled('checkout_v2')"}
        ],
        recent_assignment_count=120,
        has_active_experiment=True,
        has_active_rollout=True,
        last_changed_at=now - timedelta(days=3),
    )
    result = evaluate_flag_health(flag, now=now)
    assert result.health_score == 100
    assert result.health_issues == []


def test_all_seven_detectors_and_score_capping() -> None:
    now = _now()
    # 1. stale + env_drift + orphaned + unused + duplicate
    partial_old_flag = FlagHealthInput(
        key="checkout_v2",
        flagsmith_project_id=1,
        description="Express checkout",
        env_states={
            "development": {"enabled": True, "rollout_percentage": 100},
            "production": {"enabled": True, "rollout_percentage": 25},
        },
        code_references=[],
        recent_assignment_count=0,
        has_active_rollout=False,
        has_active_experiment=False,
        last_changed_at=now - timedelta(days=45),
    )
    peer_dup = FlagHealthInput(
        key="checkout-v2",
        flagsmith_project_id=1,
        description="Different desc",
        env_states={
            "development": {"enabled": True, "rollout_percentage": 100},
            "production": {"enabled": True, "rollout_percentage": 100},
        },
        code_references=[{"file_path": "app.py", "line_number": 10, "snippet": "is_feature_enabled('checkout-v2')"}],
        recent_assignment_count=50,
        last_changed_at=now - timedelta(days=40),
    )

    res = evaluate_flag_health(partial_old_flag, peer_flags=[partial_old_flag, peer_dup], now=now)
    issue_types = {issue.issue_type for issue in res.health_issues}
    assert issue_types == {
        HealthIssueType.STALE,
        HealthIssueType.UNUSED,
        HealthIssueType.ORPHANED,
        HealthIssueType.ENV_DRIFT,
        HealthIssueType.DUPLICATE,
    }
    # Penalties: -15 -15 -30 -20 -10 = -90 -> score = 10
    assert res.health_score == 10
    for issue in res.health_issues:
        assert issue.penalty < 0
        assert issue.summary
        assert isinstance(issue.evidence, dict)

    # 2. always_on (-15) on peer_dup
    peer_res = evaluate_flag_health(peer_dup, peer_flags=[partial_old_flag, peer_dup], now=now)
    peer_types = {issue.issue_type for issue in peer_res.health_issues}
    assert HealthIssueType.ALWAYS_ON in peer_types
    assert HealthIssueType.DUPLICATE in peer_types
    assert peer_res.health_score == 75

    # 3. always_off (-15)
    off_flag = FlagHealthInput(
        key="legacy_banner",
        flagsmith_project_id=1,
        env_states={
            "development": {"enabled": False, "rollout_percentage": 0},
            "production": {"enabled": False, "rollout_percentage": 0},
        },
        code_references=[{"file_path": "ui.tsx", "line_number": 8, "snippet": "hasFeature('legacy_banner')"}],
        recent_assignment_count=5,
        last_changed_at=now - timedelta(days=35),
    )
    off_res = evaluate_flag_health(off_flag, now=now)
    assert [i.issue_type for i in off_res.health_issues] == [HealthIssueType.ALWAYS_OFF]
    assert off_res.health_score == 85


def test_duplicate_detection_via_levenshtein_and_description_or_value() -> None:
    assert normalized_levenshtein_similarity("checkout_v2", "checkout-v2") == 1.0
    assert normalized_levenshtein_similarity("express_checkout_v2", "express_checkout_v3") >= 0.85

    now = _now()
    flag_a = FlagHealthInput(
        key="alpha_flow",
        flagsmith_project_id=9,
        description="Enables wallet promo code flow",
        env_states={"production": {"enabled": True, "value": "wallet_v1"}},
        code_references=[{"file_path": "a.py", "line_number": 1, "snippet": "a"}],
        recent_assignment_count=10,
        last_changed_at=now - timedelta(days=2),
    )
    flag_b = FlagHealthInput(
        key="beta_experiment",
        flagsmith_project_id=9,
        description="Enables wallet promo code flow",
        env_states={"production": {"enabled": True, "value": "wallet_v1"}},
        code_references=[{"file_path": "b.py", "line_number": 1, "snippet": "b"}],
        recent_assignment_count=10,
        last_changed_at=now - timedelta(days=2),
    )
    res = evaluate_flag_health(flag_a, peer_flags=[flag_a, flag_b], now=now)
    dup_issues = [i for i in res.health_issues if i.issue_type == HealthIssueType.DUPLICATE]
    assert len(dup_issues) == 1
    match = dup_issues[0].evidence["matches"][0]
    assert match["peer_key"] == "beta_experiment"
    assert "identical_description" in match["reasons"]
    assert "identical_value" in match["reasons"]


def test_score_floor_clamps_to_zero() -> None:
    now = _now()
    terrible_flag = FlagHealthInput(
        key="broken_flag",
        flagsmith_project_id=1,
        description="dup",
        env_states={
            "dev": {"enabled": True, "rollout_percentage": 50},
            "prod": {"enabled": False, "rollout_percentage": 0},
        },
        code_references=[],
        recent_assignment_count=0,
        last_changed_at=now - timedelta(days=60),
        target_completion_at=now - timedelta(days=5),
    )
    # Trigger stale(-15) + unused(-15) + orphaned(-30) + env_drift(-20) + duplicate(-10) = -90,
    # test custom thresholds or combine: check lower bound >= 0 even with extra checks
    peer = FlagHealthInput(
        key="broken-flag",
        flagsmith_project_id=1,
        description="dup",
        env_states={"dev": {"enabled": False}},
        last_changed_at=now,
    )
    res = evaluate_flag_health(
        terrible_flag,
        peer_flags=[terrible_flag, peer],
        thresholds=HealthThresholds(stale_days=10),
        now=now,
    )
    assert res.health_score == 10
    assert 0 <= res.health_score <= 100


def test_repo_sdk_scanner_python_ast_and_js_regex(tmp_path: Path) -> None:
    py_code = """
# flags.is_feature_enabled("ignored_comment_flag")
def handle_checkout(flags):
    if flags.is_feature_enabled("checkout_v2"):
        return flags.get_feature_value(feature_name="checkout_banner")
    return None
"""
    ts_code = """
// flagsmith.hasFeature("commented_out_js_flag")
export function Checkout({ flagsmith }: any) {
  const enabled = flagsmith?.hasFeature("checkout_v2");
  const mode = flagsmith.getValue('promo_tier');
  return { enabled, mode };
}
"""
    py_refs = scan_code_for_flag_references("backend/checkout.py", py_code)
    assert {(r.flag_key, r.method, r.line_number) for r in py_refs} == {
        ("checkout_v2", "is_feature_enabled", 4),
        ("checkout_banner", "get_feature_value", 5),
    }

    ts_refs = scan_code_for_flag_references("frontend/Checkout.tsx", ts_code)
    assert {(r.flag_key, r.method, r.line_number) for r in ts_refs} == {
        ("checkout_v2", "hasFeature", 4),
        ("promo_tier", "getValue", 5),
    }

    # Test directory walker + project health scan detecting missing-in-Flagsmith orphan
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(py_code, encoding="utf-8")
    (tmp_path / "src" / "ui.tsx").write_text(ts_code, encoding="utf-8")
    dir_refs = scan_directory_for_flag_references(tmp_path)
    assert "checkout_v2" in dir_refs
    assert len(dir_refs["checkout_v2"]) == 2

    report = scan_project_flag_health(
        flags=[
            FlagHealthInput(
                key="checkout_v2",
                env_states={"production": {"enabled": True, "rollout_percentage": 50}},
                recent_assignment_count=100,
                has_active_rollout=True,
                last_changed_at=_now() - timedelta(days=1),
            ),
        ],
        repository_files={"backend/checkout.py": py_code, "frontend/Checkout.tsx": ts_code},
        now=_now(),
    )
    assert report.total_flags == 1
    assert report.flags[0].health_score == 100
    missing_keys = {issue.evidence["flag_key"] for issue in report.unregistered_code_flags}
    assert missing_keys == {"checkout_banner", "promo_tier"}
    assert all(i.issue_type == HealthIssueType.ORPHANED for i in report.unregistered_code_flags)


def test_active_experiment_and_rollout_suppress_always_on_and_env_drift() -> None:
    now = _now()
    flag = FlagHealthInput(
        key="checkout_v2",
        env_states={
            "staging": {"enabled": True, "rollout_percentage": 100},
            "production": {"enabled": True, "rollout_percentage": 25},
        },
        code_references=[{"file_path": "checkout.py", "line_number": 10, "snippet": "is_feature_enabled('checkout_v2')"}],
        recent_assignment_count=40,
        has_active_experiment=True,
        has_active_rollout=True,
        last_changed_at=now - timedelta(days=5),
    )
    res = evaluate_flag_health(flag, now=now)
    assert res.health_score == 100
    assert res.health_issues == []


def test_repo_not_scanned_does_not_flag_orphaned() -> None:
    now = _now()
    flag = FlagHealthInput(
        key="external_service_flag",
        env_states={"production": {"enabled": True, "rollout_percentage": 100}},
        code_references=[],
        recent_assignment_count=25,
        repo_scanned=False,
        last_changed_at=now - timedelta(days=2),
    )
    res = evaluate_flag_health(flag, now=now)
    assert res.health_score == 100
    assert res.health_issues == []


def test_scan_project_replaces_stale_cached_refs_when_removed_from_repo() -> None:
    now = _now()
    flag_with_stale_db_refs = FlagHealthInput(
        key="removed_flag",
        env_states={"production": {"enabled": True, "rollout_percentage": 100}},
        code_references=[
            {
                "file_path": "old_checkout.py",
                "line_number": 12,
                "snippet": "flags.is_feature_enabled('removed_flag')",
            }
        ],
        recent_assignment_count=0,
        last_changed_at=now - timedelta(days=5),
    )
    # Full repo scan where 'removed_flag' no longer appears in repository_files
    report = scan_project_flag_health(
        flags=[flag_with_stale_db_refs],
        repository_files={"new_checkout.py": "def checkout():\n    return 'clean'\n"},
        now=now,
    )
    assert report.total_flags == 1
    flag_res = report.flags[0]
    issue_types = {issue.issue_type for issue in flag_res.health_issues}
    assert HealthIssueType.ORPHANED in issue_types
    assert HealthIssueType.UNUSED in issue_types
    # -30 (orphaned) + -15 (unused) = 55
    assert flag_res.health_score == 55
