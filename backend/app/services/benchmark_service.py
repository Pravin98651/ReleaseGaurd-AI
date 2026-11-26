"""Developer productivity benchmark service, baselines, impact calculator, and runner."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.integrations.flagsmith_client import FlagsmithAPIError, FlagsmithClient
from app.models.benchmarks import (
    BenchmarkEvidenceClassification,
    BenchmarkRun,
    BenchmarkStage,
    BenchmarkWorkflowType,
)

ALLOWED_BENCHMARK_ENVIRONMENTS = frozenset({"development", "staging", "test", "local"})
BLOCKED_PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})


class ProductionEnvironmentBlockedError(ValueError):
    """Raised when a benchmark is attempted against a production environment."""


def validate_benchmark_environment(environment: str) -> str:
    """Validate that benchmark execution never targets production."""
    normalized = (environment or "").strip().lower()
    if not normalized:
        raise ValueError("Benchmark environment must be specified.")
    if normalized in BLOCKED_PRODUCTION_ENVIRONMENTS or normalized.startswith("prod"):
        raise ProductionEnvironmentBlockedError(
            f"Benchmark execution is strictly blocked in '{environment}' environment. "
            "Allowed environments: development, staging, test, local."
        )
    if normalized not in ALLOWED_BENCHMARK_ENVIRONMENTS:
        raise ValueError(
            f"Unsupported benchmark environment '{environment}'. "
            f"Allowed: {sorted(ALLOWED_BENCHMARK_ENVIRONMENTS)}"
        )
    return normalized


@dataclass(frozen=True)
class StageBaselineSpec:
    stage_key: str
    stage_name: str
    manual_baseline_seconds: float | None
    rationale: str


@dataclass(frozen=True)
class WorkflowBaselineSpec:
    workflow_type: BenchmarkWorkflowType
    label: str
    manual_baseline_seconds: float | None
    manual_range_seconds: tuple[float, float] | None
    manual_baseline_source: str
    rationale: str
    evidence_classification: BenchmarkEvidenceClassification
    stages: tuple[StageBaselineSpec, ...]


MANUAL_BASELINES: dict[BenchmarkWorkflowType, WorkflowBaselineSpec] = {
    BenchmarkWorkflowType.PR_REVIEW: WorkflowBaselineSpec(
        workflow_type=BenchmarkWorkflowType.PR_REVIEW,
        label="Workflow A: PR Review & Risk Assessment",
        manual_baseline_seconds=1080.0,
        manual_range_seconds=(900.0, 1500.0),
        manual_baseline_source=(
            "Estimated engineering baseline (15-25 min, midpoint 18 min): "
            "manual GitHub PR diff review, blast-radius check, and risk summary."
        ),
        rationale=(
            "Accounts for opening PR diff, inspecting changed modules/migrations, "
            "cross-referencing release policies, and posting review findings."
        ),
        evidence_classification=BenchmarkEvidenceClassification.ESTIMATED,
        stages=(
            StageBaselineSpec("fetch_pr_context", "Fetch PR & Diff Context", 180.0, "Open PR & read changed files"),
            StageBaselineSpec("risk_analysis", "Risk & Blast-Radius Analysis", 600.0, "Manual code & dependency impact review"),
            StageBaselineSpec("review_summary", "Publish Review Findings", 300.0, "Draft & post structured review comment"),
        ),
    ),
    BenchmarkWorkflowType.FLAG_SETUP: WorkflowBaselineSpec(
        workflow_type=BenchmarkWorkflowType.FLAG_SETUP,
        label="Workflow B: Multivariate Flag & Targeting Setup",
        manual_baseline_seconds=1200.0,
        manual_range_seconds=(900.0, 1500.0),
        manual_baseline_source=(
            "Estimated engineering baseline (15-25 min, midpoint 20 min): "
            "manual Flagsmith UI flag creation, multivariate variants, segment rules, and verification."
        ),
        rationale=(
            "Accounts for navigating Flagsmith dashboard, creating feature + MV options, "
            "configuring targeting/percentage splits, and verifying environment state."
        ),
        evidence_classification=BenchmarkEvidenceClassification.ESTIMATED,
        stages=(
            StageBaselineSpec("validate_env", "Validate Environment & Project", 120.0, "Verify project & target env"),
            StageBaselineSpec("provision_flag", "Create Multivariate Feature Flag", 480.0, "Create flag & MV variant weights"),
            StageBaselineSpec("configure_targeting", "Configure Targeting & Verify State", 420.0, "Add segment/rules & verify live state"),
            StageBaselineSpec("cleanup", "Cleanup Temporary Benchmark Artifacts", 180.0, "Remove temporary test flag/rules"),
        ),
    ),
    BenchmarkWorkflowType.EXPERIMENT_SETUP: WorkflowBaselineSpec(
        workflow_type=BenchmarkWorkflowType.EXPERIMENT_SETUP,
        label="Workflow C: Experiment Setup & Bayesian Evaluation",
        manual_baseline_seconds=900.0,
        manual_range_seconds=(600.0, 1200.0),
        manual_baseline_source=(
            "Estimated engineering baseline (10-20 min, midpoint 15 min): "
            "manual experiment configuration, metric binding, and statistical evaluation."
        ),
        rationale=(
            "Accounts for defining primary/guardrail metrics, validating variant splits, "
            "collecting sample counts, and computing Bayesian win probabilities."
        ),
        evidence_classification=BenchmarkEvidenceClassification.ESTIMATED,
        stages=(
            StageBaselineSpec("configure_experiment", "Configure Experiment & Metrics", 360.0, "Define variants & metrics"),
            StageBaselineSpec("evaluate_cohort", "Evaluate Traffic & Telemetry", 240.0, "Verify exposures & conversions"),
            StageBaselineSpec("bayesian_analysis", "Bayesian & Guardrail Evaluation", 300.0, "Compute credible intervals & guardrails"),
        ),
    ),
    BenchmarkWorkflowType.RELEASE_DECISION: WorkflowBaselineSpec(
        workflow_type=BenchmarkWorkflowType.RELEASE_DECISION,
        label="Workflow D: Release Readiness & Go/No-Go Decision",
        manual_baseline_seconds=600.0,
        manual_range_seconds=(420.0, 900.0),
        manual_baseline_source=(
            "Estimated engineering baseline (7-15 min, midpoint 10 min): "
            "manual aggregation of PR status, flag state, experiment guardrails, and approval."
        ),
        rationale=(
            "Accounts for checking CI/PR checks, Flagsmith rollout state, experiment guardrails, "
            "and recording an auditable go/no-go approval decision."
        ),
        evidence_classification=BenchmarkEvidenceClassification.ESTIMATED,
        stages=(
            StageBaselineSpec("gather_evidence", "Gather Release Evidence", 240.0, "Collect PR, flag, & guardrail signals"),
            StageBaselineSpec("policy_check", "Evaluate Release Policies", 180.0, "Verify approval & risk thresholds"),
            StageBaselineSpec("decision_gate", "Record Release Decision", 180.0, "Human review & approval sign-off"),
        ),
    ),
    BenchmarkWorkflowType.COMPLETE_RELEASE: WorkflowBaselineSpec(
        workflow_type=BenchmarkWorkflowType.COMPLETE_RELEASE,
        label="Workflow E: Complete End-to-End Release Pipeline",
        manual_baseline_seconds=2700.0,
        manual_range_seconds=(2100.0, 3600.0),
        manual_baseline_source=(
            "Estimated engineering baseline (35-60 min, midpoint 45 min): "
            "full manual release coordination from PR review through flag, experiment, decision, and rollout."
        ),
        rationale=(
            "Conservative sum of non-overlapping manual release engineering steps across "
            "PR review, flag setup, experiment verification, approval gate, and rollout check."
        ),
        evidence_classification=BenchmarkEvidenceClassification.ESTIMATED,
        stages=(
            StageBaselineSpec("pr_review", "PR Review & Risk Assessment", 720.0, "Diff & risk inspection"),
            StageBaselineSpec("flag_setup", "Feature Flag & Targeting Setup", 720.0, "Create & verify flag configuration"),
            StageBaselineSpec("experiment_eval", "Experiment & Guardrail Check", 540.0, "Verify experiment & guardrails"),
            StageBaselineSpec("release_decision", "Approval & Progressive Rollout", 480.0, "Go/no-go gate & rollout"),
            StageBaselineSpec("cleanup", "Verify & Cleanup Artifacts", 240.0, "Post-run verification & cleanup"),
        ),
    ),
}


def get_manual_baseline(workflow_type: BenchmarkWorkflowType) -> WorkflowBaselineSpec:
    """Return the documented manual baseline specification for a workflow."""
    return MANUAL_BASELINES[workflow_type]


def list_manual_baselines() -> list[dict[str, Any]]:
    """Serialize all documented manual baselines for API/UI consumption."""
    items: list[dict[str, Any]] = []
    for wf in BenchmarkWorkflowType:
        spec = MANUAL_BASELINES[wf]
        items.append(
            {
                "workflow_type": spec.workflow_type.value,
                "label": spec.label,
                "manual_baseline_seconds": spec.manual_baseline_seconds,
                "manual_range_seconds": (
                    list(spec.manual_range_seconds) if spec.manual_range_seconds else None
                ),
                "manual_baseline_source": spec.manual_baseline_source,
                "rationale": spec.rationale,
                "evidence_classification": spec.evidence_classification.value,
                "stages": [
                    {
                        "stage_key": st.stage_key,
                        "stage_name": st.stage_name,
                        "manual_baseline_seconds": st.manual_baseline_seconds,
                        "rationale": st.rationale,
                    }
                    for st in spec.stages
                ],
            }
        )
    return items


def percentile(values: list[float], pct: float) -> float | None:
    """Compute linear-interpolated percentile (0-100) on a list of floats."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return round(ordered[low], 4)
    weight = rank - low
    interpolated = ordered[low] + (ordered[high] - ordered[low]) * weight
    return round(interpolated, 4)


def compute_run_impact(
    *,
    total_duration_seconds: float,
    active_developer_seconds: float,
    unattended_execution_seconds: float,
    manual_baseline_seconds: float | None,
    stage_count: int,
    automated_stage_count: int,
    human_intervention_seconds: float,
    success: bool,
) -> dict[str, Any]:
    """Compute productivity impact metrics; omit pct_saved/time_saved if baseline is missing."""
    automation_coverage_pct = (
        round((automated_stage_count / stage_count) * 100.0, 2) if stage_count > 0 else 0.0
    )
    impact: dict[str, Any] = {
        "has_manual_baseline": bool(manual_baseline_seconds and manual_baseline_seconds > 0),
        "total_duration_seconds": round(total_duration_seconds, 4),
        "active_developer_seconds": round(active_developer_seconds, 4),
        "unattended_execution_seconds": round(unattended_execution_seconds, 4),
        "human_intervention_seconds": round(human_intervention_seconds, 4),
        "automation_coverage_pct": automation_coverage_pct,
        "success": success,
    }
    if manual_baseline_seconds is not None and manual_baseline_seconds > 0:
        time_saved = round(manual_baseline_seconds - total_duration_seconds, 4)
        pct_saved = round((time_saved / manual_baseline_seconds) * 100.0, 2)
        active_saved = round(manual_baseline_seconds - active_developer_seconds, 4)
        impact["manual_baseline_seconds"] = round(manual_baseline_seconds, 4)
        impact["time_saved_seconds"] = time_saved
        impact["pct_saved"] = pct_saved
        impact["active_developer_time_saved_seconds"] = active_saved
    else:
        impact["baseline_missing_reason"] = (
            "No manual baseline provided; time_saved_seconds and pct_saved are omitted."
        )
    return impact


def serialize_benchmark_stage(stage: BenchmarkStage) -> dict[str, Any]:
    """Serialize a BenchmarkStage ORM row to a JSON-ready dict."""
    return {
        "id": str(stage.id),
        "stage_order": stage.stage_order,
        "stage_key": stage.stage_key,
        "stage_name": stage.stage_name,
        "started_at": stage.started_at.isoformat() if stage.started_at else None,
        "completed_at": stage.completed_at.isoformat() if stage.completed_at else None,
        "duration_seconds": round(stage.duration_seconds, 4),
        "active_developer_seconds": round(stage.active_developer_seconds, 4),
        "unattended_execution_seconds": round(stage.unattended_execution_seconds, 4),
        "api_call_count": stage.api_call_count,
        "agent_call_count": stage.agent_call_count,
        "human_intervention_seconds": round(stage.human_intervention_seconds, 4),
        "retry_count": stage.retry_count,
        "failure_count": stage.failure_count,
        "success": stage.success,
        "evidence_classification": (
            stage.evidence_classification.value
            if hasattr(stage.evidence_classification, "value")
            else str(stage.evidence_classification)
        ),
        "manual_baseline_seconds": stage.manual_baseline_seconds,
        "details": stage.details or {},
    }


def serialize_benchmark_run(run: BenchmarkRun) -> dict[str, Any]:
    """Serialize a BenchmarkRun ORM row (including stages) to a JSON-ready dict."""
    return {
        "id": str(run.id),
        "benchmark_id": run.benchmark_id,
        "workflow_type": (
            run.workflow_type.value
            if hasattr(run.workflow_type, "value")
            else str(run.workflow_type)
        ),
        "execution_id": run.execution_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "total_duration_seconds": round(run.total_duration_seconds, 4),
        "active_developer_seconds": round(run.active_developer_seconds, 4),
        "unattended_execution_seconds": round(run.unattended_execution_seconds, 4),
        "api_call_count": run.api_call_count,
        "agent_call_count": run.agent_call_count,
        "human_intervention_seconds": round(run.human_intervention_seconds, 4),
        "human_intervention_count": run.human_intervention_count,
        "approval_latency_seconds": (
            round(run.approval_latency_seconds, 4)
            if run.approval_latency_seconds is not None
            else None
        ),
        "retry_count": run.retry_count,
        "failure_count": run.failure_count,
        "success": run.success,
        "environment": run.environment,
        "scenario": run.scenario,
        "evidence_classification": (
            run.evidence_classification.value
            if hasattr(run.evidence_classification, "value")
            else str(run.evidence_classification)
        ),
        "manual_baseline_seconds": run.manual_baseline_seconds,
        "manual_baseline_source": run.manual_baseline_source,
        "impact_metrics": run.impact_metrics or {},
        "metadata_json": run.metadata_json or {},
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "stages": [serialize_benchmark_stage(st) for st in (run.stages or [])],
    }


def _compute_group_metrics(runs: list[BenchmarkRun]) -> dict[str, Any]:
    """Compute aggregate statistics for a homogeneous or filtered list of BenchmarkRun."""
    total = len(runs)
    if total == 0:
        return {
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "completion_rate": 0.0,
            "failure_rate": 0.0,
            "retry_rate": 0.0,
            "total_retries": 0,
            "total_failures": 0,
            "human_intervention_frequency": 0.0,
            "mean_human_intervention_seconds": 0.0,
            "mean_approval_latency_seconds": None,
            "automation_coverage_pct": 0.0,
            "mean_active_developer_seconds": 0.0,
            "mean_unattended_execution_seconds": 0.0,
            "duration_stats": {"mean": None, "median": None, "p95": None, "min": None, "max": None},
            "baseline_comparison": None,
        }

    durations = [float(r.total_duration_seconds) for r in runs]
    successes = sum(1 for r in runs if r.success)
    failures = total - successes
    runs_with_retries = sum(1 for r in runs if r.retry_count > 0)
    total_retries = sum(r.retry_count for r in runs)
    total_failures = sum(r.failure_count for r in runs)
    interventions = sum(
        1 for r in runs if r.human_intervention_count > 0 or r.human_intervention_seconds > 0
    )
    approval_latencies = [
        float(r.approval_latency_seconds)
        for r in runs
        if r.approval_latency_seconds is not None
    ]
    coverages = [
        float((r.impact_metrics or {}).get("automation_coverage_pct", 0.0))
        for r in runs
    ]

    mean_duration = round(sum(durations) / total, 4)
    median_duration = percentile(durations, 50.0)
    p95_duration = percentile(durations, 95.0)

    baseline_runs = [
        r for r in runs if r.manual_baseline_seconds is not None and r.manual_baseline_seconds > 0
    ]
    baseline_comparison: dict[str, Any] | None = None
    if baseline_runs:
        mean_baseline = round(
            sum(float(r.manual_baseline_seconds or 0.0) for r in baseline_runs)
            / len(baseline_runs),
            4,
        )
        mean_auto = round(
            sum(float(r.total_duration_seconds) for r in baseline_runs) / len(baseline_runs),
            4,
        )
        time_saved = round(mean_baseline - mean_auto, 4)
        pct_saved = round((time_saved / mean_baseline) * 100.0, 2) if mean_baseline > 0 else None
        baseline_comparison = {
            "runs_with_baseline": len(baseline_runs),
            "mean_manual_baseline_seconds": mean_baseline,
            "mean_automated_seconds": mean_auto,
            "mean_time_saved_seconds": time_saved,
            "pct_saved": pct_saved,
        }

    return {
        "total_runs": total,
        "successful_runs": successes,
        "failed_runs": failures,
        "completion_rate": round(successes / total, 4),
        "failure_rate": round(failures / total, 4),
        "retry_rate": round(runs_with_retries / total, 4),
        "total_retries": total_retries,
        "total_failures": total_failures,
        "human_intervention_frequency": round(interventions / total, 4),
        "mean_human_intervention_seconds": round(
            sum(float(r.human_intervention_seconds) for r in runs) / total, 4
        ),
        "mean_approval_latency_seconds": (
            round(sum(approval_latencies) / len(approval_latencies), 4)
            if approval_latencies
            else None
        ),
        "automation_coverage_pct": round(sum(coverages) / total, 2),
        "mean_active_developer_seconds": round(
            sum(float(r.active_developer_seconds) for r in runs) / total, 4
        ),
        "mean_unattended_execution_seconds": round(
            sum(float(r.unattended_execution_seconds) for r in runs) / total, 4
        ),
        "duration_stats": {
            "mean": mean_duration,
            "median": median_duration,
            "p95": p95_duration,
            "min": round(min(durations), 4),
            "max": round(max(durations), 4),
        },
        "baseline_comparison": baseline_comparison,
    }


def compute_aggregate_summary(runs: list[BenchmarkRun]) -> dict[str, Any]:
    """Compute productivity summary with strict evidence-classification separation."""
    by_evidence: dict[str, list[BenchmarkRun]] = {}
    by_workflow: dict[str, list[BenchmarkRun]] = {}
    stage_buckets: dict[str, dict[str, Any]] = {}

    for run in runs:
        ev = (
            run.evidence_classification.value
            if hasattr(run.evidence_classification, "value")
            else str(run.evidence_classification)
        )
        wf = (
            run.workflow_type.value
            if hasattr(run.workflow_type, "value")
            else str(run.workflow_type)
        )
        by_evidence.setdefault(ev, []).append(run)
        by_workflow.setdefault(wf, []).append(run)

        for st in run.stages or []:
            bucket = stage_buckets.setdefault(
                st.stage_key,
                {
                    "stage_key": st.stage_key,
                    "stage_name": st.stage_name,
                    "durations": [],
                    "manual_baselines": [],
                    "api_calls": 0,
                    "agent_calls": 0,
                    "retries": 0,
                    "failures": 0,
                    "runs": 0,
                },
            )
            bucket["durations"].append(float(st.duration_seconds))
            if st.manual_baseline_seconds is not None and st.manual_baseline_seconds > 0:
                bucket["manual_baselines"].append(float(st.manual_baseline_seconds))
            bucket["api_calls"] += st.api_call_count
            bucket["agent_calls"] += st.agent_call_count
            bucket["retries"] += st.retry_count
            bucket["failures"] += st.failure_count
            bucket["runs"] += 1

    stage_comparison: list[dict[str, Any]] = []
    for bucket in stage_buckets.values():
        durs = bucket["durations"]
        mans = bucket["manual_baselines"]
        mean_auto = round(sum(durs) / len(durs), 4) if durs else 0.0
        mean_man = round(sum(mans) / len(mans), 4) if mans else None
        stage_comparison.append(
            {
                "stage_key": bucket["stage_key"],
                "stage_name": bucket["stage_name"],
                "sample_count": bucket["runs"],
                "mean_duration_seconds": mean_auto,
                "median_duration_seconds": percentile(durs, 50.0),
                "p95_duration_seconds": percentile(durs, 95.0),
                "manual_baseline_seconds": mean_man,
                "time_saved_seconds": (
                    round(mean_man - mean_auto, 4) if mean_man is not None else None
                ),
                "api_calls": bucket["api_calls"],
                "agent_calls": bucket["agent_calls"],
                "retries": bucket["retries"],
                "failures": bucket["failures"],
            }
        )

    evidence_classifications_present = sorted(by_evidence.keys())
    mixed_evidence_warning: str | None = None
    if len(evidence_classifications_present) > 1:
        mixed_evidence_warning = (
            "Multiple evidence classifications present "
            f"({', '.join(evidence_classifications_present)}). "
            "Inspect by_evidence_classification for unmixed metrics."
        )

    return {
        "overall": _compute_group_metrics(runs),
        "evidence_classifications_present": evidence_classifications_present,
        "mixed_evidence_warning": mixed_evidence_warning,
        "by_evidence_classification": {
            ev: _compute_group_metrics(group) for ev, group in sorted(by_evidence.items())
        },
        "by_workflow_type": {
            wf: _compute_group_metrics(group) for wf, group in sorted(by_workflow.items())
        },
        "stage_comparison": stage_comparison,
        "manual_baselines": list_manual_baselines(),
    }


def check_integration_readiness(cfg: Settings | None = None) -> dict[str, Any]:
    """Inspect configured integrations without leaking secrets."""
    settings = cfg or get_settings()
    flagsmith_ready = bool(
        settings.FLAGSMITH_ORG_TOKEN.strip()
        and settings.FLAGSMITH_PROJECT_ID.strip()
        and (
            settings.FLAGSMITH_DEV_ENV_KEY.strip()
            or settings.FLAGSMITH_STAGING_ENV_KEY.strip()
        )
    )
    github_ready = bool(settings.GITHUB_PAT.strip() or settings.GITHUB_CLIENT_ID.strip())
    gemini_ready = bool(settings.GEMINI_API_KEY.strip())
    return {
        "flagsmith_configured": flagsmith_ready,
        "flagsmith_project_id": settings.FLAGSMITH_PROJECT_ID or None,
        "github_configured": github_ready,
        "gemini_configured": gemini_ready,
        "app_env": settings.APP_ENV,
    }


@dataclass
class _StageDraft:
    stage_order: int
    stage_key: str
    stage_name: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    active_developer_seconds: float
    unattended_execution_seconds: float
    api_call_count: int = 0
    agent_call_count: int = 0
    human_intervention_seconds: float = 0.0
    retry_count: int = 0
    failure_count: int = 0
    success: bool = True
    evidence_classification: BenchmarkEvidenceClassification = (
        BenchmarkEvidenceClassification.VERIFIED_MEASURED
    )
    manual_baseline_seconds: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


async def _run_flag_setup_live(
    *,
    settings: Settings,
    environment: str,
    baseline: WorkflowBaselineSpec,
    benchmark_id: str,
) -> tuple[list[_StageDraft], bool, BenchmarkEvidenceClassification, dict[str, Any]]:
    """Execute Workflow B (flag_setup) against live Flagsmith API in non-prod with cleanup."""
    env_key = (
        settings.FLAGSMITH_STAGING_ENV_KEY
        if environment == "staging" and settings.FLAGSMITH_STAGING_ENV_KEY
        else settings.FLAGSMITH_DEV_ENV_KEY
    )
    project_id = int(settings.FLAGSMITH_PROJECT_ID)
    client = FlagsmithClient(
        org_token=settings.FLAGSMITH_ORG_TOKEN,
        api_url=settings.FLAGSMITH_API_URL,
        edge_api_url=settings.FLAGSMITH_EDGE_API_URL,
    )
    stages: list[_StageDraft] = []
    stage_map = {st.stage_key: st for st in baseline.stages}
    temp_flag_name = f"rg_bench_{benchmark_id.replace('-', '_')[:32]}"
    created_flag_id: int | None = None
    cleanup_status = "not_needed"
    overall_success = True
    metadata: dict[str, Any] = {"mode": "live_flagsmith_api", "temp_flag_name": temp_flag_name}

    try:
        # Stage 1: Validate environment & project
        s1_start_dt = datetime.now(UTC)
        s1_t0 = time.monotonic()
        env_info = await client.get_environment(env_key)
        flags = await client.list_flags(project_id)
        s1_dur = time.monotonic() - s1_t0
        stages.append(
            _StageDraft(
                stage_order=1,
                stage_key="validate_env",
                stage_name=stage_map["validate_env"].stage_name,
                started_at=s1_start_dt,
                completed_at=datetime.now(UTC),
                duration_seconds=s1_dur,
                active_developer_seconds=0.0,
                unattended_execution_seconds=s1_dur,
                api_call_count=2,
                manual_baseline_seconds=stage_map["validate_env"].manual_baseline_seconds,
                details={"environment_name": env_info.get("name"), "existing_flags": len(flags)},
            )
        )

        # Stage 2: Create multivariate feature flag + 2 MV options
        s2_start_dt = datetime.now(UTC)
        s2_t0 = time.monotonic()
        created = await client.create_flag(
            project_id=project_id,
            key=temp_flag_name,
            flag_type="MULTIVARIATE",
            initial_value="control",
            description=f"Temporary benchmark flag {benchmark_id} (auto-cleaned)",
            mv_options=[
                {"value": "control", "default_percentage_allocation": 50},
                {"value": "variant_a", "default_percentage_allocation": 50},
            ],
        )
        created_flag_id = int(created["id"])
        s2_dur = time.monotonic() - s2_t0
        stages.append(
            _StageDraft(
                stage_order=2,
                stage_key="provision_flag",
                stage_name=stage_map["provision_flag"].stage_name,
                started_at=s2_start_dt,
                completed_at=datetime.now(UTC),
                duration_seconds=s2_dur,
                active_developer_seconds=0.0,
                unattended_execution_seconds=s2_dur,
                api_call_count=3,
                manual_baseline_seconds=stage_map["provision_flag"].manual_baseline_seconds,
                details={"flag_id": created_flag_id, "variants": ["control", "variant_a"]},
            )
        )

        # Stage 3: List segments & verify live state
        s3_start_dt = datetime.now(UTC)
        s3_t0 = time.monotonic()
        segments = await client.list_segments(project_id)
        live_states = await client.get_environment_feature_states(env_key)
        s3_dur = time.monotonic() - s3_t0
        stages.append(
            _StageDraft(
                stage_order=3,
                stage_key="configure_targeting",
                stage_name=stage_map["configure_targeting"].stage_name,
                started_at=s3_start_dt,
                completed_at=datetime.now(UTC),
                duration_seconds=s3_dur,
                active_developer_seconds=0.0,
                unattended_execution_seconds=s3_dur,
                api_call_count=2,
                manual_baseline_seconds=stage_map["configure_targeting"].manual_baseline_seconds,
                details={"segment_count": len(segments), "env_state_count": len(live_states)},
            )
        )
    except (FlagsmithAPIError, Exception) as exc:
        overall_success = False
        metadata["error"] = str(exc)
        stages.append(
            _StageDraft(
                stage_order=len(stages) + 1,
                stage_key="live_execution_error",
                stage_name="Live Flagsmith Execution Error",
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                duration_seconds=0.0,
                active_developer_seconds=0.0,
                unattended_execution_seconds=0.0,
                failure_count=1,
                success=False,
                evidence_classification=BenchmarkEvidenceClassification.INCOMPLETE,
                details={"error": str(exc)},
            )
        )
    finally:
        # Stage 4: Cleanup temporary flag
        s4_start_dt = datetime.now(UTC)
        s4_t0 = time.monotonic()
        cleanup_calls = 0
        cleanup_ok = True
        if created_flag_id is not None:
            try:
                await client.delete_flag(project_id=project_id, feature_id=created_flag_id)
                cleanup_calls += 1
                cleanup_status = "deleted"
            except Exception:
                try:
                    await client.archive_flag(project_id=project_id, feature_id=created_flag_id)
                    cleanup_calls += 1
                    cleanup_status = "archived_fallback"
                except Exception as cleanup_exc:
                    cleanup_ok = False
                    cleanup_status = f"failed: {cleanup_exc}"
        s4_dur = time.monotonic() - s4_t0
        stages.append(
            _StageDraft(
                stage_order=4,
                stage_key="cleanup",
                stage_name=stage_map["cleanup"].stage_name,
                started_at=s4_start_dt,
                completed_at=datetime.now(UTC),
                duration_seconds=s4_dur,
                active_developer_seconds=0.0,
                unattended_execution_seconds=s4_dur,
                api_call_count=cleanup_calls,
                failure_count=0 if cleanup_ok else 1,
                success=cleanup_ok,
                manual_baseline_seconds=stage_map["cleanup"].manual_baseline_seconds,
                details={"cleanup_status": cleanup_status, "flag_id": created_flag_id},
            )
        )
        metadata["cleanup_status"] = cleanup_status

    classification = (
        BenchmarkEvidenceClassification.VERIFIED_MEASURED
        if overall_success
        else BenchmarkEvidenceClassification.INCOMPLETE
    )
    return stages, overall_success, classification, metadata


def _run_simulated_stages(
    *,
    baseline: WorkflowBaselineSpec,
    human_intervention_seconds: float,
    simulate_failure: bool,
) -> tuple[list[_StageDraft], bool, BenchmarkEvidenceClassification, dict[str, Any]]:
    """Execute deterministic local stages when external live credentials are unavailable."""
    stages: list[_StageDraft] = []
    overall_success = not simulate_failure

    for idx, st_spec in enumerate(baseline.stages, start=1):
        st_start_dt = datetime.now(UTC)
        st_t0 = time.monotonic()
        # Deterministic lightweight work so monotonic duration is non-zero and measurable
        _ = sum(i * i for i in range(500))
        st_dur = max(time.monotonic() - st_t0, 0.0005)
        is_decision_stage = st_spec.stage_key in {"decision_gate", "release_decision"}
        stage_human = human_intervention_seconds if is_decision_stage else 0.0
        stage_failed = simulate_failure and idx == len(baseline.stages)
        stages.append(
            _StageDraft(
                stage_order=idx,
                stage_key=st_spec.stage_key,
                stage_name=st_spec.stage_name,
                started_at=st_start_dt,
                completed_at=datetime.now(UTC),
                duration_seconds=st_dur,
                active_developer_seconds=stage_human,
                unattended_execution_seconds=st_dur,
                api_call_count=0,
                agent_call_count=1 if not stage_failed else 0,
                human_intervention_seconds=stage_human,
                retry_count=1 if stage_failed else 0,
                failure_count=1 if stage_failed else 0,
                success=not stage_failed,
                evidence_classification=BenchmarkEvidenceClassification.SIMULATED,
                manual_baseline_seconds=st_spec.manual_baseline_seconds,
                details={"mode": "simulated_local", "rationale": st_spec.rationale},
            )
        )

    classification = (
        BenchmarkEvidenceClassification.SIMULATED
        if overall_success
        else BenchmarkEvidenceClassification.INCOMPLETE
    )
    return (
        stages,
        overall_success,
        classification,
        {"mode": "simulated_local", "cleanup_status": "not_applicable"},
    )


async def persist_benchmark_run(
    session: AsyncSession,
    *,
    benchmark_id: str,
    workflow_type: BenchmarkWorkflowType,
    execution_id: str | None,
    started_at: datetime,
    completed_at: datetime,
    total_duration_seconds: float,
    environment: str,
    scenario: str,
    evidence_classification: BenchmarkEvidenceClassification,
    manual_baseline_seconds: float | None,
    manual_baseline_source: str | None,
    stages: list[_StageDraft],
    success: bool,
    approval_latency_seconds: float | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> BenchmarkRun:
    """Persist a BenchmarkRun and its BenchmarkStage children to the database."""
    active_dev = round(sum(s.active_developer_seconds for s in stages), 4)
    unattended = round(sum(s.unattended_execution_seconds for s in stages), 4)
    api_calls = sum(s.api_call_count for s in stages)
    agent_calls = sum(s.agent_call_count for s in stages)
    human_secs = round(sum(s.human_intervention_seconds for s in stages), 4)
    human_count = sum(1 for s in stages if s.human_intervention_seconds > 0)
    retries = sum(s.retry_count for s in stages)
    failures = sum(s.failure_count for s in stages)
    automated_stages = sum(1 for s in stages if s.human_intervention_seconds <= 0)

    impact = compute_run_impact(
        total_duration_seconds=total_duration_seconds,
        active_developer_seconds=active_dev,
        unattended_execution_seconds=unattended,
        manual_baseline_seconds=manual_baseline_seconds,
        stage_count=len(stages),
        automated_stage_count=automated_stages,
        human_intervention_seconds=human_secs,
        success=success,
    )

    run = BenchmarkRun(
        benchmark_id=benchmark_id,
        workflow_type=workflow_type,
        execution_id=execution_id,
        started_at=started_at,
        completed_at=completed_at,
        total_duration_seconds=round(total_duration_seconds, 4),
        active_developer_seconds=active_dev,
        unattended_execution_seconds=unattended,
        api_call_count=api_calls,
        agent_call_count=agent_calls,
        human_intervention_seconds=human_secs,
        human_intervention_count=human_count,
        approval_latency_seconds=approval_latency_seconds,
        retry_count=retries,
        failure_count=failures,
        success=success,
        environment=environment,
        scenario=scenario,
        evidence_classification=evidence_classification,
        manual_baseline_seconds=manual_baseline_seconds,
        manual_baseline_source=manual_baseline_source,
        impact_metrics=impact,
        metadata_json=metadata_json or {},
    )
    for st in stages:
        run.stages.append(
            BenchmarkStage(
                stage_order=st.stage_order,
                stage_key=st.stage_key,
                stage_name=st.stage_name,
                started_at=st.started_at,
                completed_at=st.completed_at,
                duration_seconds=round(st.duration_seconds, 4),
                active_developer_seconds=round(st.active_developer_seconds, 4),
                unattended_execution_seconds=round(st.unattended_execution_seconds, 4),
                api_call_count=st.api_call_count,
                agent_call_count=st.agent_call_count,
                human_intervention_seconds=round(st.human_intervention_seconds, 4),
                retry_count=st.retry_count,
                failure_count=st.failure_count,
                success=st.success,
                evidence_classification=st.evidence_classification,
                manual_baseline_seconds=st.manual_baseline_seconds,
                details=st.details,
            )
        )
    session.add(run)
    await session.commit()
    await session.refresh(run, attribute_names=["stages"])
    return run


async def execute_benchmark_workflow(
    session: AsyncSession | None,
    *,
    workflow_type: BenchmarkWorkflowType,
    environment: str = "development",
    scenario: str = "default",
    require_live: bool = False,
    human_intervention_seconds: float = 0.0,
    simulate_failure: bool = False,
    include_baseline: bool = True,
    persist: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run a repeatable benchmark workflow, measure monotonic timings, and optionally persist."""
    safe_env = validate_benchmark_environment(environment)
    cfg = settings or get_settings()
    readiness = check_integration_readiness(cfg)
    baseline = get_manual_baseline(workflow_type)

    benchmark_id = f"bench-{workflow_type.value}-{uuid.uuid4().hex[:10]}"
    execution_id = f"exec-{uuid.uuid4().hex[:12]}"
    run_started_at = datetime.now(UTC)
    run_t0 = time.monotonic()

    if workflow_type == BenchmarkWorkflowType.FLAG_SETUP and readiness["flagsmith_configured"] and not simulate_failure:
        stages, success, classification, meta = await _run_flag_setup_live(
            settings=cfg,
            environment=safe_env,
            baseline=baseline,
            benchmark_id=benchmark_id,
        )
    elif require_live and not readiness["flagsmith_configured"]:
        run_dur = max(time.monotonic() - run_t0, 0.0001)
        stages = [
            _StageDraft(
                stage_order=1,
                stage_key="integration_check",
                stage_name="Integration Readiness Check",
                started_at=run_started_at,
                completed_at=datetime.now(UTC),
                duration_seconds=run_dur,
                active_developer_seconds=0.0,
                unattended_execution_seconds=run_dur,
                failure_count=1,
                success=False,
                evidence_classification=BenchmarkEvidenceClassification.INCOMPLETE,
                details={"reason": "Live integration credentials not configured"},
            )
        ]
        success = False
        classification = BenchmarkEvidenceClassification.INCOMPLETE
        meta = {"mode": "live_required_missing", "readiness": readiness}
    else:
        stages, success, classification, meta = _run_simulated_stages(
            baseline=baseline,
            human_intervention_seconds=human_intervention_seconds,
            simulate_failure=simulate_failure,
        )

    total_duration = max(time.monotonic() - run_t0, 0.0005)
    run_completed_at = datetime.now(UTC)
    manual_secs = baseline.manual_baseline_seconds if include_baseline else None
    manual_src = baseline.manual_baseline_source if include_baseline else None
    meta["readiness"] = readiness

    if persist and session is not None:
        run = await persist_benchmark_run(
            session,
            benchmark_id=benchmark_id,
            workflow_type=workflow_type,
            execution_id=execution_id,
            started_at=run_started_at,
            completed_at=run_completed_at,
            total_duration_seconds=total_duration,
            environment=safe_env,
            scenario=scenario,
            evidence_classification=classification,
            manual_baseline_seconds=manual_secs,
            manual_baseline_source=manual_src,
            stages=stages,
            success=success,
            approval_latency_seconds=(
                human_intervention_seconds if human_intervention_seconds > 0 else None
            ),
            metadata_json=meta,
        )
        return serialize_benchmark_run(run)

    # Non-persisted return (e.g. CLI --no-persist)
    active_dev = round(sum(s.active_developer_seconds for s in stages), 4)
    unattended = round(sum(s.unattended_execution_seconds for s in stages), 4)
    human_secs = round(sum(s.human_intervention_seconds for s in stages), 4)
    automated_stages = sum(1 for s in stages if s.human_intervention_seconds <= 0)
    impact = compute_run_impact(
        total_duration_seconds=total_duration,
        active_developer_seconds=active_dev,
        unattended_execution_seconds=unattended,
        manual_baseline_seconds=manual_secs,
        stage_count=len(stages),
        automated_stage_count=automated_stages,
        human_intervention_seconds=human_secs,
        success=success,
    )
    return {
        "id": None,
        "benchmark_id": benchmark_id,
        "workflow_type": workflow_type.value,
        "execution_id": execution_id,
        "started_at": run_started_at.isoformat(),
        "completed_at": run_completed_at.isoformat(),
        "total_duration_seconds": round(total_duration, 4),
        "active_developer_seconds": active_dev,
        "unattended_execution_seconds": unattended,
        "api_call_count": sum(s.api_call_count for s in stages),
        "agent_call_count": sum(s.agent_call_count for s in stages),
        "human_intervention_seconds": human_secs,
        "human_intervention_count": sum(1 for s in stages if s.human_intervention_seconds > 0),
        "approval_latency_seconds": (
            human_intervention_seconds if human_intervention_seconds > 0 else None
        ),
        "retry_count": sum(s.retry_count for s in stages),
        "failure_count": sum(s.failure_count for s in stages),
        "success": success,
        "environment": safe_env,
        "scenario": scenario,
        "evidence_classification": classification.value,
        "manual_baseline_seconds": manual_secs,
        "manual_baseline_source": manual_src,
        "impact_metrics": impact,
        "metadata_json": meta,
        "created_at": run_completed_at.isoformat(),
        "stages": [
            {
                "stage_order": s.stage_order,
                "stage_key": s.stage_key,
                "stage_name": s.stage_name,
                "started_at": s.started_at.isoformat(),
                "completed_at": s.completed_at.isoformat(),
                "duration_seconds": round(s.duration_seconds, 4),
                "active_developer_seconds": round(s.active_developer_seconds, 4),
                "unattended_execution_seconds": round(s.unattended_execution_seconds, 4),
                "api_call_count": s.api_call_count,
                "agent_call_count": s.agent_call_count,
                "human_intervention_seconds": round(s.human_intervention_seconds, 4),
                "retry_count": s.retry_count,
                "failure_count": s.failure_count,
                "success": s.success,
                "evidence_classification": s.evidence_classification.value,
                "manual_baseline_seconds": s.manual_baseline_seconds,
                "details": s.details,
            }
            for s in stages
        ],
    }


async def list_benchmark_runs(
    session: AsyncSession,
    *,
    workflow_type: BenchmarkWorkflowType | None = None,
    environment: str | None = None,
    evidence_classification: BenchmarkEvidenceClassification | None = None,
    success: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[BenchmarkRun]:
    """Query persisted BenchmarkRun records with eager-loaded stages."""
    stmt = (
        select(BenchmarkRun)
        .options(selectinload(BenchmarkRun.stages))
        .order_by(BenchmarkRun.created_at.desc())
    )
    if workflow_type is not None:
        stmt = stmt.where(BenchmarkRun.workflow_type == workflow_type)
    if environment is not None:
        stmt = stmt.where(BenchmarkRun.environment == environment)
    if evidence_classification is not None:
        stmt = stmt.where(BenchmarkRun.evidence_classification == evidence_classification)
    if success is not None:
        stmt = stmt.where(BenchmarkRun.success == success)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())
