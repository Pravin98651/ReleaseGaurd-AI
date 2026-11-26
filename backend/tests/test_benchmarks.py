"""Comprehensive tests for developer productivity benchmarks, impact metrics, safety, CLI, and API."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.main import bootstrap_workspace_defaults, create_app
from app.models.benchmarks import (
    BenchmarkEvidenceClassification,
    BenchmarkRun,
    BenchmarkStage,
    BenchmarkWorkflowType,
)
from app.services.benchmark_service import (
    ProductionEnvironmentBlockedError,
    compute_aggregate_summary,
    compute_run_impact,
    execute_benchmark_workflow,
    list_benchmark_runs,
    percentile,
    validate_benchmark_environment,
)
from scripts.run_benchmark import main as cli_main


async def _make_benchmark_app(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> Any:
    fake_genai = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **kwargs: SimpleNamespace(
                text="ok",
                function_calls=[],
                candidates=[],
                usage_metadata=None,
            )
        )
    )
    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        gemini_raw_client=fake_genai,
        bootstrap_env_tokens=False,
    )
    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)
    return app


def _offline_settings() -> Settings:
    return Settings(
        APP_ENV="test",
        USE_SQLITE_FALLBACK=True,
        REQUIRE_AUTH=False,
        FLAGSMITH_ORG_TOKEN="",
        FLAGSMITH_PROJECT_ID="",
        FLAGSMITH_DEV_ENV_KEY="",
        FLAGSMITH_STAGING_ENV_KEY="",
        GITHUB_PAT="",
        GITHUB_CLIENT_ID="",
        GEMINI_API_KEY="",
    )


def test_production_environment_strictly_blocked() -> None:
    for blocked in ("production", "prod", "PRODUCTION", "Prod-US", "production-eu"):
        with pytest.raises(ProductionEnvironmentBlockedError):
            validate_benchmark_environment(blocked)

    with pytest.raises(ValueError):
        validate_benchmark_environment("")

    with pytest.raises(ValueError):
        validate_benchmark_environment("qa-unknown")

    for allowed in ("development", "staging", "test", "local", "  Development "):
        assert validate_benchmark_environment(allowed) in {
            "development",
            "staging",
            "test",
            "local",
        }


def test_missing_baseline_never_fabricates_pct_saved() -> None:
    impact = compute_run_impact(
        total_duration_seconds=5.0,
        active_developer_seconds=1.0,
        unattended_execution_seconds=4.0,
        manual_baseline_seconds=None,
        stage_count=4,
        automated_stage_count=3,
        human_intervention_seconds=1.0,
        success=True,
    )
    assert impact["has_manual_baseline"] is False
    assert "pct_saved" not in impact
    assert "time_saved_seconds" not in impact
    assert "active_developer_time_saved_seconds" not in impact
    assert "baseline_missing_reason" in impact
    assert impact["automation_coverage_pct"] == 75.0


def test_percentage_and_percentile_calculations() -> None:
    impact = compute_run_impact(
        total_duration_seconds=60.0,
        active_developer_seconds=15.0,
        unattended_execution_seconds=45.0,
        manual_baseline_seconds=1200.0,
        stage_count=4,
        automated_stage_count=3,
        human_intervention_seconds=15.0,
        success=True,
    )
    assert impact["has_manual_baseline"] is True
    assert impact["time_saved_seconds"] == 1140.0
    assert impact["pct_saved"] == 95.0
    assert impact["active_developer_time_saved_seconds"] == 1185.0
    assert impact["automation_coverage_pct"] == 75.0

    values = [2.0, 4.0, 6.0, 8.0, 10.0]
    assert percentile(values, 50.0) == 6.0
    assert percentile(values, 95.0) == 9.6
    assert percentile([3.5], 95.0) == 3.5
    assert percentile([], 50.0) is None


@pytest.mark.asyncio
async def test_monotonic_duration_stage_timing_and_db_persistence(
    db_session: AsyncSession,
) -> None:
    cfg = _offline_settings()
    run_data = await execute_benchmark_workflow(
        db_session,
        workflow_type=BenchmarkWorkflowType.RELEASE_DECISION,
        environment="development",
        scenario="persist-test",
        human_intervention_seconds=12.5,
        include_baseline=True,
        persist=True,
        settings=cfg,
    )

    assert run_data["id"] is not None
    assert run_data["total_duration_seconds"] > 0
    assert run_data["evidence_classification"] == BenchmarkEvidenceClassification.SIMULATED.value
    assert run_data["success"] is True
    assert run_data["human_intervention_seconds"] == 12.5
    assert run_data["active_developer_seconds"] == 12.5
    assert run_data["unattended_execution_seconds"] > 0
    assert len(run_data["stages"]) == 3
    for idx, st in enumerate(run_data["stages"], start=1):
        assert st["stage_order"] == idx
        assert st["duration_seconds"] > 0
        assert st["started_at"] <= st["completed_at"]

    persisted = await list_benchmark_runs(db_session, environment="development")
    assert len(persisted) == 1
    assert persisted[0].benchmark_id == run_data["benchmark_id"]
    assert len(persisted[0].stages) == 3


@pytest.mark.asyncio
async def test_failed_workflow_and_require_live_incomplete(
    db_session: AsyncSession,
) -> None:
    cfg = _offline_settings()
    failed_run = await execute_benchmark_workflow(
        db_session,
        workflow_type=BenchmarkWorkflowType.PR_REVIEW,
        environment="staging",
        scenario="simulated-fail",
        simulate_failure=True,
        persist=True,
        settings=cfg,
    )
    assert failed_run["success"] is False
    assert failed_run["failure_count"] >= 1
    assert failed_run["retry_count"] >= 1
    assert failed_run["evidence_classification"] == BenchmarkEvidenceClassification.INCOMPLETE.value

    incomplete_live = await execute_benchmark_workflow(
        db_session,
        workflow_type=BenchmarkWorkflowType.FLAG_SETUP,
        environment="development",
        scenario="missing-creds",
        require_live=True,
        include_baseline=False,
        persist=True,
        settings=cfg,
    )
    assert incomplete_live["success"] is False
    assert incomplete_live["evidence_classification"] == BenchmarkEvidenceClassification.INCOMPLETE.value
    assert "pct_saved" not in incomplete_live["impact_metrics"]

    runs = await list_benchmark_runs(db_session, success=False)
    assert len(runs) == 2


@pytest.mark.asyncio
async def test_repeated_executions_and_evidence_classification_separation(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    runs_to_add = [
        BenchmarkRun(
            benchmark_id="bench-vm-1",
            workflow_type=BenchmarkWorkflowType.FLAG_SETUP,
            started_at=now,
            completed_at=now + timedelta(seconds=4),
            total_duration_seconds=4.0,
            active_developer_seconds=0.0,
            unattended_execution_seconds=4.0,
            api_call_count=8,
            agent_call_count=0,
            human_intervention_seconds=0.0,
            human_intervention_count=0,
            retry_count=0,
            failure_count=0,
            success=True,
            environment="development",
            scenario="trial-1",
            evidence_classification=BenchmarkEvidenceClassification.VERIFIED_MEASURED,
            manual_baseline_seconds=1200.0,
            impact_metrics={"automation_coverage_pct": 100.0},
            stages=[
                BenchmarkStage(
                    stage_order=1,
                    stage_key="provision_flag",
                    stage_name="Create Multivariate Feature Flag",
                    started_at=now,
                    completed_at=now + timedelta(seconds=2),
                    duration_seconds=2.0,
                    active_developer_seconds=0.0,
                    unattended_execution_seconds=2.0,
                    api_call_count=3,
                    manual_baseline_seconds=480.0,
                )
            ],
        ),
        BenchmarkRun(
            benchmark_id="bench-vm-2",
            workflow_type=BenchmarkWorkflowType.FLAG_SETUP,
            started_at=now,
            completed_at=now + timedelta(seconds=6),
            total_duration_seconds=6.0,
            active_developer_seconds=0.0,
            unattended_execution_seconds=6.0,
            api_call_count=8,
            agent_call_count=0,
            human_intervention_seconds=0.0,
            human_intervention_count=0,
            retry_count=1,
            failure_count=0,
            success=True,
            environment="development",
            scenario="trial-2",
            evidence_classification=BenchmarkEvidenceClassification.VERIFIED_MEASURED,
            manual_baseline_seconds=1200.0,
            impact_metrics={"automation_coverage_pct": 100.0},
            stages=[
                BenchmarkStage(
                    stage_order=1,
                    stage_key="provision_flag",
                    stage_name="Create Multivariate Feature Flag",
                    started_at=now,
                    completed_at=now + timedelta(seconds=3),
                    duration_seconds=3.0,
                    active_developer_seconds=0.0,
                    unattended_execution_seconds=3.0,
                    api_call_count=3,
                    retry_count=1,
                    manual_baseline_seconds=480.0,
                )
            ],
        ),
        BenchmarkRun(
            benchmark_id="bench-sim-1",
            workflow_type=BenchmarkWorkflowType.PR_REVIEW,
            started_at=now,
            completed_at=now + timedelta(seconds=1),
            total_duration_seconds=1.0,
            active_developer_seconds=0.0,
            unattended_execution_seconds=1.0,
            api_call_count=0,
            agent_call_count=3,
            human_intervention_seconds=0.0,
            human_intervention_count=0,
            retry_count=0,
            failure_count=1,
            success=False,
            environment="test",
            scenario="sim-fail",
            evidence_classification=BenchmarkEvidenceClassification.SIMULATED,
            manual_baseline_seconds=None,
            impact_metrics={"automation_coverage_pct": 100.0},
            stages=[],
        ),
    ]
    db_session.add_all(runs_to_add)
    await db_session.commit()

    persisted = await list_benchmark_runs(db_session)
    summary = compute_aggregate_summary(persisted)

    assert summary["mixed_evidence_warning"] is not None
    assert summary["evidence_classifications_present"] == ["SIMULATED", "VERIFIED_MEASURED"]
    assert "VERIFIED_MEASURED" in summary["by_evidence_classification"]
    assert "SIMULATED" in summary["by_evidence_classification"]

    vm = summary["by_evidence_classification"]["VERIFIED_MEASURED"]
    assert vm["total_runs"] == 2
    assert vm["completion_rate"] == 1.0
    assert vm["retry_rate"] == 0.5
    assert vm["duration_stats"]["mean"] == 5.0
    assert vm["duration_stats"]["median"] == 5.0
    assert vm["duration_stats"]["p95"] == 5.9
    assert vm["baseline_comparison"]["pct_saved"] == 99.58

    sim = summary["by_evidence_classification"]["SIMULATED"]
    assert sim["total_runs"] == 1
    assert sim["failure_rate"] == 1.0
    assert sim["baseline_comparison"] is None

    assert len(summary["stage_comparison"]) == 1
    assert summary["stage_comparison"][0]["mean_duration_seconds"] == 2.5


def test_cli_runner_json_report_repeated_trials_and_prod_block(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Production strictly blocked with exit code 2
    prod_exit = cli_main(["--workflow", "flag_setup", "--environment", "production", "--no-persist"])
    assert prod_exit == 2

    out_json = tmp_path / "bench_report.json"
    exit_code = cli_main(
        [
            "--workflow",
            "pr_review",
            "--environment",
            "test",
            "--scenario",
            "cli-test",
            "--trials",
            "3",
            "--no-persist",
            "--no-baseline",
            "--output-json",
            str(out_json),
            "--json",
        ]
    )
    assert exit_code == 0
    assert out_json.exists()

    report = json.loads(out_json.read_text(encoding="utf-8"))
    stdout_report = json.loads(capsys.readouterr().out)
    assert report["trials"] == 3
    assert report["persisted"] is False
    assert len(report["runs"]) == 3
    assert report["aggregate"]["all_succeeded"] is True
    assert report["aggregate"]["p95_seconds"] is not None
    assert stdout_report["trials"] == 3
    for run in report["runs"]:
        assert run["manual_baseline_seconds"] is None
        assert "pct_saved" not in run["impact_metrics"]


@pytest.mark.asyncio
async def test_benchmark_dashboard_api_endpoints_and_prod_block(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = await _make_benchmark_app(async_engine, session_factory)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Baselines endpoint
        r_base = await client.get("/api/v1/benchmarks/baselines")
        assert r_base.status_code == 200
        baselines = r_base.json()["baselines"]
        assert len(baselines) == 5

        # 2. Production block via API
        r_prod = await client.post(
            "/api/v1/benchmarks/run",
            json={"workflow_type": "flag_setup", "environment": "production"},
        )
        assert r_prod.status_code == 400
        assert r_prod.json()["error"]["code"] == "PRODUCTION_BENCHMARK_BLOCKED"

        # 3. Trigger non-prod run (with baseline, then without baseline)
        r_run1 = await client.post(
            "/api/v1/benchmarks/run",
            json={
                "workflow_type": "release_decision",
                "environment": "development",
                "scenario": "api-test-1",
                "human_intervention_seconds": 8.0,
                "include_baseline": True,
            },
        )
        assert r_run1.status_code == 200
        run1 = r_run1.json()["run"]
        assert run1["human_intervention_seconds"] == 8.0
        assert "pct_saved" in run1["impact_metrics"]

        r_run2 = await client.post(
            "/api/v1/benchmarks/run",
            json={
                "workflow_type": "experiment_setup",
                "environment": "staging",
                "scenario": "api-test-2",
                "include_baseline": False,
            },
        )
        assert r_run2.status_code == 200
        run2 = r_run2.json()["run"]
        assert "pct_saved" not in run2["impact_metrics"]

        # 4. List runs + detail + 404
        r_runs = await client.get("/api/v1/benchmarks/runs")
        assert r_runs.status_code == 200
        assert r_runs.json()["count"] == 2

        r_detail = await client.get(f"/api/v1/benchmarks/runs/{run1['benchmark_id']}")
        assert r_detail.status_code == 200
        assert r_detail.json()["run"]["benchmark_id"] == run1["benchmark_id"]

        r_missing = await client.get("/api/v1/benchmarks/runs/bench-does-not-exist")
        assert r_missing.status_code == 404

        # 5. Summary endpoint
        r_sum = await client.get("/api/v1/benchmarks/summary")
        assert r_sum.status_code == 200
        summary = r_sum.json()
        assert summary["overall"]["total_runs"] == 2
        assert "duration_stats" in summary["overall"]
        assert "by_evidence_classification" in summary
        assert "by_workflow_type" in summary
        assert "stage_comparison" in summary
