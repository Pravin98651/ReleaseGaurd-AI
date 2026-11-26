"""Repeatable CLI benchmark runner for ReleaseGuard AI workflows (non-production only)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, create_async_engine_from_settings, get_session_factory  # noqa: E402
from app.models.benchmarks import BenchmarkWorkflowType  # noqa: E402
from app.services.benchmark_service import (  # noqa: E402
    ProductionEnvironmentBlockedError,
    check_integration_readiness,
    execute_benchmark_workflow,
    percentile,
    validate_benchmark_environment,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run repeatable ReleaseGuard AI developer productivity benchmarks (non-production only)."
    )
    parser.add_argument(
        "--workflow",
        type=str,
        default=BenchmarkWorkflowType.FLAG_SETUP.value,
        choices=[wf.value for wf in BenchmarkWorkflowType],
        help="Workflow type to benchmark (A=pr_review, B=flag_setup, C=experiment_setup, D=release_decision, E=complete_release).",
    )
    parser.add_argument(
        "--environment",
        type=str,
        default="development",
        help="Target environment (development, staging, test, local). Production is strictly blocked.",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default="cli-benchmark",
        help="Scenario label recorded on the benchmark run.",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Number of repeated trials to execute (default: 1).",
    )
    parser.add_argument(
        "--require-live",
        action="store_true",
        help="Fail with INCOMPLETE if live external integration credentials are not configured.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not persist BenchmarkRun records to the database.",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="Omit manual baseline so pct_saved/time_saved are not computed.",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default="",
        help="Optional file path to write the full JSON benchmark report.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report to stdout instead of the human summary.",
    )
    return parser


def _format_human_summary(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=== ReleaseGuard AI Productivity Benchmark Report ===")
    lines.append(f"Workflow:    {report['workflow_type']}")
    lines.append(f"Environment: {report['environment']}")
    lines.append(f"Scenario:    {report['scenario']}")
    lines.append(f"Trials:      {report['trials']}")
    readiness = report.get("integration_readiness", {})
    lines.append(
        "Integrations: "
        f"flagsmith={readiness.get('flagsmith_configured')}, "
        f"github={readiness.get('github_configured')}, "
        f"gemini={readiness.get('gemini_configured')}"
    )
    agg = report.get("aggregate", {})
    if agg:
        lines.append(
            f"Duration (s): mean={agg.get('mean_seconds')} | "
            f"median={agg.get('median_seconds')} | p95={agg.get('p95_seconds')}"
        )
    lines.append("")
    for idx, run in enumerate(report.get("runs", []), start=1):
        impact = run.get("impact_metrics", {})
        pct_saved = impact.get("pct_saved")
        time_saved = impact.get("time_saved_seconds")
        savings_str = (
            f"{time_saved:.2f}s ({pct_saved:.2f}%)"
            if pct_saved is not None and time_saved is not None
            else "omitted (no manual baseline)"
        )
        lines.append(
            f"--- Trial {idx}: {run['benchmark_id']} [{run['evidence_classification']}] "
            f"success={run['success']} ---"
        )
        lines.append(
            f"  Total: {run['total_duration_seconds']:.4f}s | "
            f"Active Dev: {run['active_developer_seconds']:.4f}s | "
            f"Unattended: {run['unattended_execution_seconds']:.4f}s"
        )
        lines.append(
            f"  Manual Baseline: {run.get('manual_baseline_seconds') or 'N/A'}s | "
            f"Time Saved: {savings_str}"
        )
        lines.append(
            f"  Calls: api={run['api_call_count']}, agent={run['agent_call_count']} | "
            f"Retries={run['retry_count']}, Failures={run['failure_count']}, "
            f"Human={run['human_intervention_seconds']:.2f}s"
        )
        cleanup = (run.get("metadata_json") or {}).get("cleanup_status", "unknown")
        lines.append(f"  Cleanup: {cleanup}")
        for st in run.get("stages", []):
            lines.append(
                f"    [{st['stage_order']}] {st['stage_key']}: "
                f"{st['duration_seconds']:.4f}s (api={st['api_call_count']}, ok={st['success']})"
            )
    return "\n".join(lines)


async def _run_async(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    safe_env = validate_benchmark_environment(args.environment)
    cfg = get_settings()
    readiness = check_integration_readiness(cfg)
    workflow = BenchmarkWorkflowType(args.workflow)
    trials = max(1, int(args.trials))

    session_factory = None
    if not args.no_persist:
        if cfg.effective_database_url.lower().startswith("sqlite"):
            engine = create_async_engine_from_settings(cfg)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await engine.dispose()
        session_factory = get_session_factory()

    runs: list[dict[str, Any]] = []
    for trial_idx in range(1, trials + 1):
        scenario_label = args.scenario if trials == 1 else f"{args.scenario}-t{trial_idx}"
        if session_factory is not None:
            async with session_factory() as session:
                run_data = await execute_benchmark_workflow(
                    session,
                    workflow_type=workflow,
                    environment=safe_env,
                    scenario=scenario_label,
                    require_live=bool(args.require_live),
                    include_baseline=not bool(args.no_baseline),
                    persist=True,
                    settings=cfg,
                )
        else:
            run_data = await execute_benchmark_workflow(
                None,
                workflow_type=workflow,
                environment=safe_env,
                scenario=scenario_label,
                require_live=bool(args.require_live),
                include_baseline=not bool(args.no_baseline),
                persist=False,
                settings=cfg,
            )
        runs.append(run_data)

    durations = [float(r["total_duration_seconds"]) for r in runs]
    all_success = all(bool(r["success"]) for r in runs)
    report: dict[str, Any] = {
        "workflow_type": workflow.value,
        "environment": safe_env,
        "scenario": args.scenario,
        "trials": trials,
        "persisted": not bool(args.no_persist),
        "integration_readiness": readiness,
        "aggregate": {
            "mean_seconds": round(sum(durations) / len(durations), 4) if durations else None,
            "median_seconds": percentile(durations, 50.0),
            "p95_seconds": percentile(durations, 95.0),
            "all_succeeded": all_success,
        },
        "runs": runs,
    }

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return (0 if all_success else 1), report


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        validate_benchmark_environment(args.environment)
    except (ProductionEnvironmentBlockedError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    exit_code, report = asyncio.run(_run_async(args))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_format_human_summary(report))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
