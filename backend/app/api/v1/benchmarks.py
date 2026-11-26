"""Developer Productivity & Benchmark REST endpoints (/api/v1/benchmarks/*)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import AuthenticatedOperator, DBSession
from app.models.benchmarks import (
    BenchmarkEvidenceClassification,
    BenchmarkRun,
    BenchmarkWorkflowType,
)
from app.services.benchmark_service import (
    ProductionEnvironmentBlockedError,
    compute_aggregate_summary,
    execute_benchmark_workflow,
    list_benchmark_runs,
    list_manual_baselines,
    serialize_benchmark_run,
    validate_benchmark_environment,
)

benchmarks_router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


class RunBenchmarkRequest(BaseModel):
    """Payload to trigger a non-production developer productivity benchmark."""

    workflow_type: BenchmarkWorkflowType = BenchmarkWorkflowType.FLAG_SETUP
    environment: str = Field(default="development", min_length=1, max_length=40)
    scenario: str = Field(default="default", min_length=1, max_length=120)
    require_live: bool = False
    human_intervention_seconds: float = Field(default=0.0, ge=0.0, le=86400.0)
    include_baseline: bool = True


@benchmarks_router.get("/summary")
async def get_benchmark_summary(
    session: DBSession,
    _: AuthenticatedOperator,
    workflow_type: Annotated[BenchmarkWorkflowType | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    evidence_classification: Annotated[BenchmarkEvidenceClassification | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    """Return aggregate productivity benchmarks, percentiles, baselines, and stage comparisons."""
    runs = await list_benchmark_runs(
        session,
        workflow_type=workflow_type,
        environment=environment,
        evidence_classification=evidence_classification,
        limit=limit,
    )
    summary = compute_aggregate_summary(runs)
    summary["filters"] = {
        "workflow_type": workflow_type.value if workflow_type else None,
        "environment": environment,
        "evidence_classification": (
            evidence_classification.value if evidence_classification else None
        ),
        "limit": limit,
    }
    return summary


@benchmarks_router.get("/baselines")
async def get_benchmark_baselines(
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Return documented manual engineering baselines and per-stage rationales."""
    return {"baselines": list_manual_baselines()}


@benchmarks_router.get("/runs")
async def get_benchmark_runs(
    session: DBSession,
    _: AuthenticatedOperator,
    workflow_type: Annotated[BenchmarkWorkflowType | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    evidence_classification: Annotated[BenchmarkEvidenceClassification | None, Query()] = None,
    success: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """List persisted historical benchmark runs with their stage breakdowns."""
    runs = await list_benchmark_runs(
        session,
        workflow_type=workflow_type,
        environment=environment,
        evidence_classification=evidence_classification,
        success=success,
        limit=limit,
        offset=offset,
    )
    return {
        "runs": [serialize_benchmark_run(r) for r in runs],
        "count": len(runs),
        "limit": limit,
        "offset": offset,
    }


@benchmarks_router.get("/runs/{benchmark_id}")
async def get_benchmark_run_detail(
    benchmark_id: str,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Return a single benchmark run and its stages by benchmark_id."""
    stmt = (
        select(BenchmarkRun)
        .options(selectinload(BenchmarkRun.stages))
        .where(BenchmarkRun.benchmark_id == benchmark_id)
    )
    run = (await session.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "BENCHMARK_RUN_NOT_FOUND",
                "message": f"Benchmark run '{benchmark_id}' not found.",
            },
        )
    return {"run": serialize_benchmark_run(run)}


@benchmarks_router.post("/run")
async def trigger_benchmark_run(
    payload: RunBenchmarkRequest,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Execute a benchmark workflow in development/staging and persist the timing record."""
    try:
        safe_env = validate_benchmark_environment(payload.environment)
    except ProductionEnvironmentBlockedError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "PRODUCTION_BENCHMARK_BLOCKED", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_BENCHMARK_ENVIRONMENT", "message": str(exc)},
        ) from exc

    run_data = await execute_benchmark_workflow(
        session,
        workflow_type=payload.workflow_type,
        environment=safe_env,
        scenario=payload.scenario,
        require_live=payload.require_live,
        human_intervention_seconds=payload.human_intervention_seconds,
        include_baseline=payload.include_baseline,
        persist=True,
    )
    return {"run": run_data}
