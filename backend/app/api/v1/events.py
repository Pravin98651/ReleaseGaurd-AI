"""Telemetry event ingestion (X-Ingest-Key protected) and summary endpoints."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select

from app.api.deps import AuthenticatedOperator, DBSession
from app.api.v1.errors import APIException
from app.core.database import utcnow
from app.core.security import verify_ingest_key
from app.models import Experiment, ExperimentEvent, ExperimentStatus, FeatureFlag
from app.schemas import (
    EventIngestRequest,
    EventIngestResponse,
    EventSummaryBucket,
    EventSummaryResponse,
)

events_router = APIRouter(prefix="/events", tags=["events"])


@events_router.post("/ingest", response_model=EventIngestResponse)
async def ingest_events(
    body: EventIngestRequest,
    session: DBSession,
    x_ingest_key: Annotated[str | None, Header(alias="X-Ingest-Key")] = None,
) -> EventIngestResponse:
    """Ingest and deduplicate demo-app checkout telemetry authenticated by X-Ingest-Key."""
    if not verify_ingest_key(x_ingest_key):
        raise APIException(
            401,
            "INVALID_INGEST_KEY",
            "Missing or invalid X-Ingest-Key header for telemetry ingestion.",
        )

    batch_event_ids = [item.event_id for item in body.events]
    existing_ids = set(
        (
            await session.execute(
                select(ExperimentEvent.event_id).where(
                    ExperimentEvent.event_id.in_(batch_event_ids)
                )
            )
        )
        .scalars()
        .all()
    )

    # Resolve matching experiments per (flag_key, environment_name)
    flags = (await session.execute(select(FeatureFlag))).scalars().all()
    flag_id_by_key = {f.key: f.id for f in flags}
    exps = (
        (await session.execute(select(Experiment).order_by(Experiment.created_at.desc())))
        .scalars()
        .all()
    )
    exp_lookup: dict[tuple[uuid.UUID, str], uuid.UUID] = {}
    for exp in exps:
        pair = (exp.feature_flag_id, exp.environment_name.lower())
        if pair not in exp_lookup or exp.status == ExperimentStatus.RUNNING:
            exp_lookup[pair] = exp.id

    seen_in_batch: set[str] = set()
    accepted = 0
    deduplicated = 0

    for item in body.events:
        if item.event_id in existing_ids or item.event_id in seen_in_batch:
            deduplicated += 1
            continue
        seen_in_batch.add(item.event_id)

        env_norm = item.environment_name.strip().lower()
        flag_id = flag_id_by_key.get(item.flag_key)
        resolved_exp_id = exp_lookup.get((flag_id, env_norm)) if flag_id else None

        ev = ExperimentEvent(
            event_id=item.event_id,
            experiment_id=resolved_exp_id,
            flag_key=item.flag_key,
            environment_name=env_norm,
            variation_key=item.variation_key,
            event_type=item.event_type,
            user_identifier=item.user_identifier,
            session_key=item.session_key,
            numeric_value=item.numeric_value,
            properties=dict(item.properties or {}),
            occurred_at=item.occurred_at,
        )
        session.add(ev)
        accepted += 1

    await session.commit()
    return EventIngestResponse(accepted=accepted, deduplicated=deduplicated)


@events_router.get("/summary", response_model=EventSummaryResponse)
async def get_events_summary(
    session: DBSession,
    _: AuthenticatedOperator,
    flag_key: Annotated[str | None, Query()] = None,
    environment_name: Annotated[str | None, Query()] = None,
    experiment_id: Annotated[uuid.UUID | None, Query()] = None,
    window_minutes: Annotated[int, Query(ge=1, le=43200)] = 1440,
) -> EventSummaryResponse:
    """Return grouped event counts by variation and 5-minute time-series buckets."""
    cutoff = utcnow() - timedelta(minutes=window_minutes)
    stmt = select(ExperimentEvent).where(ExperimentEvent.occurred_at >= cutoff)
    if flag_key:
        stmt = stmt.where(ExperimentEvent.flag_key == flag_key)
    if environment_name:
        stmt = stmt.where(
            ExperimentEvent.environment_name == environment_name.strip().lower()
        )
    if experiment_id is not None:
        stmt = stmt.where(ExperimentEvent.experiment_id == experiment_id)

    rows = (await session.execute(stmt.order_by(ExperimentEvent.occurred_at.asc()))).scalars().all()

    counts_by_variation: dict[str, dict[str, int]] = {}
    bucket_counts: dict[tuple[datetime, str, Any], int] = {}

    for ev in rows:
        var_map = counts_by_variation.setdefault(ev.variation_key, {})
        et_val = ev.event_type.value
        var_map[et_val] = var_map.get(et_val, 0) + 1

        epoch = int(math.floor(ev.occurred_at.timestamp() / 300) * 300)
        b_start = datetime.fromtimestamp(epoch, tz=UTC)
        key = (b_start, ev.variation_key, ev.event_type)
        bucket_counts[key] = bucket_counts.get(key, 0) + 1

    buckets = [
        EventSummaryBucket(
            bucket_start=b_start,
            variation_key=var_key,
            event_type=ev_type,
            count=cnt,
        )
        for (b_start, var_key, ev_type), cnt in sorted(bucket_counts.items(), key=lambda x: x[0][0])
    ]

    return EventSummaryResponse(
        counts_by_variation=counts_by_variation,
        buckets=buckets,
        total_events=len(rows),
    )


@events_router.get("/stream")
async def stream_workspace_events(
    request: Request,
    _: AuthenticatedOperator,
    after_seq: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    """Stream real-time workspace-wide operational events (notifications, PRs, flags, schedules) via SSE."""
    import json

    from fastapi.responses import StreamingResponse

    from app.services.sse_bus import sse_bus

    last_id_hdr = request.headers.get("last-event-id")
    cursor = after_seq
    if last_id_hdr and last_id_hdr.isdigit():
        cursor = max(cursor, int(last_id_hdr))

    async def event_generator() -> Any:
        async for ev in sse_bus.subscribe_workspace(after_seq=cursor, heartbeat_seconds=15.0):
            if await request.is_disconnected():
                break
            seq = ev.get("seq")
            data_str = json.dumps(ev, default=str)
            if seq is not None:
                yield f"id: {seq}\nevent: workspace\ndata: {data_str}\n\n"
            else:
                yield f"event: heartbeat\ndata: {data_str}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
