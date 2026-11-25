"""Metrics & Analytics REST endpoints (Release Eng, PR, Flags, Experiments, Agents, Events & CSV)."""

from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response

from app.api.deps import AuthenticatedOperator, DBSession
from app.schemas.analytics import FilterWindowMeta, TimeRangePreset
from app.services.analytics_queries import load_scoped_dataset, resolve_time_window
from app.services.metrics_service import (
    compute_agent_metrics,
    compute_event_metrics,
    compute_experiment_metrics,
    compute_flag_metrics,
    compute_metrics_overview,
    compute_pr_metrics,
    compute_release_metrics,
)

metrics_router = APIRouter(prefix="/metrics", tags=["metrics"])


def _build_filter(
    *,
    range_preset: TimeRangePreset,
    start_date: str | None,
    end_date: str | None,
    environment: str | None,
    repository_id: uuid.UUID | None,
    flag_key: str | None,
    experiment_id: uuid.UUID | None,
    event_source: Literal["all", "simulator_generated", "application_telemetry"],
) -> FilterWindowMeta:
    return resolve_time_window(
        range_preset=range_preset,
        start_raw=start_date,
        end_raw=end_date,
        environment=environment,
        repository_id=repository_id,
        flag_key=flag_key,
        experiment_id=experiment_id,
        event_source=event_source,
    )


@metrics_router.get("/overview")
async def get_metrics_overview(
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "30d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    flag_key: Annotated[str | None, Query()] = None,
    experiment_id: Annotated[uuid.UUID | None, Query()] = None,
    event_source: Annotated[
        Literal["all", "simulator_generated", "application_telemetry"], Query()
    ] = "all",
) -> dict[str, Any]:
    """Return global analytics overview, filter catalog options, definitions, and data sources."""
    flt = _build_filter(
        range_preset=range_preset,
        start_date=start_date,
        end_date=end_date,
        environment=environment,
        repository_id=repository_id,
        flag_key=flag_key,
        experiment_id=experiment_id,
        event_source=event_source,
    )
    data = await load_scoped_dataset(session, flt)
    return compute_metrics_overview(data, flt)


@metrics_router.get("/releases")
async def get_release_metrics(
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "30d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    flag_key: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Return release engineering velocity, failure, rollback, and MTTR metrics."""
    flt = _build_filter(
        range_preset=range_preset,
        start_date=start_date,
        end_date=end_date,
        environment=environment,
        repository_id=repository_id,
        flag_key=flag_key,
        experiment_id=None,
        event_source="all",
    )
    data = await load_scoped_dataset(session, flt)
    return compute_release_metrics(data, flt)


@metrics_router.get("/pull-requests")
async def get_pr_metrics(
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "30d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    """Return PR analysis throughput, risk distribution, validation pass rate, and repo stats."""
    flt = _build_filter(
        range_preset=range_preset,
        start_date=start_date,
        end_date=end_date,
        environment=None,
        repository_id=repository_id,
        flag_key=None,
        experiment_id=None,
        event_source="all",
    )
    data = await load_scoped_dataset(session, flt)
    return compute_pr_metrics(data, flt)


@metrics_router.get("/experiments")
async def get_experiment_metrics(
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "30d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    flag_key: Annotated[str | None, Query()] = None,
    experiment_id: Annotated[uuid.UUID | None, Query()] = None,
    event_source: Annotated[
        Literal["all", "simulator_generated", "application_telemetry"], Query()
    ] = "all",
) -> dict[str, Any]:
    """Return Bayesian Beta-Binomial experimentation comparisons and statistical clarity."""
    flt = _build_filter(
        range_preset=range_preset,
        start_date=start_date,
        end_date=end_date,
        environment=environment,
        repository_id=repository_id,
        flag_key=flag_key,
        experiment_id=experiment_id,
        event_source=event_source,
    )
    data = await load_scoped_dataset(session, flt)
    return compute_experiment_metrics(data, flt)


@metrics_router.get("/feature-flags")
async def get_feature_flag_metrics(
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "30d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    flag_key: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Return environment-specific Flagsmith configurations, rollout history, and health metrics."""
    flt = _build_filter(
        range_preset=range_preset,
        start_date=start_date,
        end_date=end_date,
        environment=environment,
        repository_id=repository_id,
        flag_key=flag_key,
        experiment_id=None,
        event_source="all",
    )
    data = await load_scoped_dataset(session, flt)
    return compute_flag_metrics(data, flt)


@metrics_router.get("/agents")
async def get_agent_metrics(
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "30d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Return AI agent run reliability, duration, tool invocation rate, and exact token usage."""
    flt = _build_filter(
        range_preset=range_preset,
        start_date=start_date,
        end_date=end_date,
        environment=None,
        repository_id=None,
        flag_key=None,
        experiment_id=None,
        event_source="all",
    )
    data = await load_scoped_dataset(session, flt)
    return compute_agent_metrics(data, flt)


@metrics_router.get("/events")
async def get_event_metrics(
    session: DBSession,
    _: AuthenticatedOperator,
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "30d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    flag_key: Annotated[str | None, Query()] = None,
    experiment_id: Annotated[uuid.UUID | None, Query()] = None,
    event_source: Annotated[
        Literal["all", "simulator_generated", "application_telemetry"], Query()
    ] = "all",
) -> dict[str, Any]:
    """Return telemetry event explorer data explicitly distinguishing simulator vs storefront traffic."""
    flt = _build_filter(
        range_preset=range_preset,
        start_date=start_date,
        end_date=end_date,
        environment=environment,
        repository_id=repository_id,
        flag_key=flag_key,
        experiment_id=experiment_id,
        event_source=event_source,
    )
    data = await load_scoped_dataset(session, flt)
    return compute_event_metrics(data, flt)


@metrics_router.get("/export")
async def export_metrics_csv(
    session: DBSession,
    _: AuthenticatedOperator,
    section: Annotated[
        Literal["releases", "pull_requests", "feature_flags", "experiments", "agents", "events"],
        Query(),
    ] = "releases",
    range_preset: Annotated[TimeRangePreset, Query(alias="range")] = "30d",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    environment: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    flag_key: Annotated[str | None, Query()] = None,
    experiment_id: Annotated[uuid.UUID | None, Query()] = None,
    event_source: Annotated[
        Literal["all", "simulator_generated", "application_telemetry"], Query()
    ] = "all",
) -> Response:
    """Export filtered analytical rows as downloadable CSV."""
    flt = _build_filter(
        range_preset=range_preset,
        start_date=start_date,
        end_date=end_date,
        environment=environment,
        repository_id=repository_id,
        flag_key=flag_key,
        experiment_id=experiment_id,
        event_source=event_source,
    )
    data = await load_scoped_dataset(session, flt)

    rows: list[dict[str, Any]] = []
    if section == "releases":
        rows = compute_release_metrics(data, flt)["recent_operations"]
    elif section == "pull_requests":
        rows = compute_pr_metrics(data, flt)["by_repository"]
    elif section == "feature_flags":
        for item in compute_flag_metrics(data, flt)["flags"]:
            flat = dict(item)
            flat["environments"] = json.dumps(flat.get("environments") or {})
            rows.append(flat)
    elif section == "experiments":
        rows = compute_experiment_metrics(data, flt)["experiments"]
    elif section == "agents":
        rows = compute_agent_metrics(data, flt)["recent_runs"]
    elif section == "events":
        rows = compute_event_metrics(data, flt)["recent_events"]

    buf = io.StringIO()
    if rows:
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()})
    else:
        buf.write("section,status\n")
        buf.write(f"{section},no_records\n")

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="releaseguard-metrics-{section}.csv"'},
    )
