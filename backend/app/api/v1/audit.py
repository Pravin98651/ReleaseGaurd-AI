"""Immutable audit logs query, JSON/CSV export, and rollout history endpoints."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select

from app.api.deps import DBSession
from app.models import (
    AuditLog,
    AuditStatus,
    FeatureFlag,
    PolicyCategory,
    RolloutHistory,
)
from app.schemas import AuditLogOut, PaginatedResponse, RolloutHistoryOut

audit_router = APIRouter(tags=["audit"])


def _parse_date_bound(raw: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        d = datetime.strptime(s, "%Y-%m-%d").date()
        t = time(23, 59, 59, 999999) if end_of_day else time(0, 0, 0, 0)
        return datetime.combine(d, t, tzinfo=UTC)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _apply_audit_filters(
    stmt: Any,
    *,
    policy_category: PolicyCategory | None,
    event_action: str | None,
    resource_type: str | None,
    status: AuditStatus | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> Any:
    if policy_category is not None:
        stmt = stmt.where(AuditLog.policy_category == policy_category)
    if event_action:
        stmt = stmt.where(AuditLog.event_action.ilike(f"%{event_action.strip()}%"))
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type.strip())
    if status is not None:
        stmt = stmt.where(AuditLog.status == status)
    if from_dt is not None:
        stmt = stmt.where(AuditLog.created_at >= from_dt)
    if to_dt is not None:
        stmt = stmt.where(AuditLog.created_at <= to_dt)
    return stmt


@audit_router.get("/audit-logs", response_model=PaginatedResponse[AuditLogOut])
async def list_audit_logs(
    session: DBSession,
    policy_category: Annotated[PolicyCategory | None, Query()] = None,
    event_action: Annotated[str | None, Query()] = None,
    resource_type: Annotated[str | None, Query()] = None,
    status: Annotated[AuditStatus | None, Query()] = None,
    from_raw: Annotated[str | None, Query(alias="from")] = None,
    to_raw: Annotated[str | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[AuditLogOut]:
    """Query append-only compliance audit logs with optional filters and pagination."""
    from_dt = _parse_date_bound(from_raw, end_of_day=False)
    to_dt = _parse_date_bound(to_raw, end_of_day=True)

    count_q = _apply_audit_filters(
        select(func.count(AuditLog.id)),
        policy_category=policy_category,
        event_action=event_action,
        resource_type=resource_type,
        status=status,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    data_q = _apply_audit_filters(
        select(AuditLog),
        policy_category=policy_category,
        event_action=event_action,
        resource_type=resource_type,
        status=status,
        from_dt=from_dt,
        to_dt=to_dt,
    )

    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                data_q.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse[AuditLogOut](
        items=[AuditLogOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@audit_router.get("/audit-logs/export")
async def export_audit_logs(
    session: DBSession,
    format: Annotated[Literal["json", "csv"], Query()] = "json",
    policy_category: Annotated[PolicyCategory | None, Query()] = None,
    event_action: Annotated[str | None, Query()] = None,
    resource_type: Annotated[str | None, Query()] = None,
    status: Annotated[AuditStatus | None, Query()] = None,
    from_raw: Annotated[str | None, Query(alias="from")] = None,
    to_raw: Annotated[str | None, Query(alias="to")] = None,
) -> Response:
    """Export filtered audit logs as downloadable JSON or CSV."""
    from_dt = _parse_date_bound(from_raw, end_of_day=False)
    to_dt = _parse_date_bound(to_raw, end_of_day=True)
    data_q = _apply_audit_filters(
        select(AuditLog),
        policy_category=policy_category,
        event_action=event_action,
        resource_type=resource_type,
        status=status,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    rows = (
        (await session.execute(data_q.order_by(AuditLog.created_at.desc()).limit(5000)))
        .scalars()
        .all()
    )
    items = [AuditLogOut.model_validate(r).model_dump(mode="json") for r in rows]

    if format == "csv":
        buf = io.StringIO()
        fieldnames = [
            "id",
            "created_at",
            "actor_type",
            "actor_id",
            "policy_category",
            "event_action",
            "resource_type",
            "resource_id",
            "environment_name",
            "approval_id",
            "action_hash",
            "status",
            "details",
        ]
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for row in items:
            csv_row = dict(row)
            csv_row["details"] = json.dumps(row.get("details") or {}, ensure_ascii=True)
            writer.writerow(csv_row)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="releaseguard-audit-logs.csv"'
            },
        )

    return Response(
        content=json.dumps({"items": items, "total": len(items)}, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="releaseguard-audit-logs.json"'},
    )


@audit_router.get("/rollout-history")
async def list_rollout_history(
    session: DBSession,
    feature_flag_id: Annotated[uuid.UUID | None, Query()] = None,
    environment_name: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """List immutable Flagsmith rollout transitions with live verification details."""
    base_q = select(RolloutHistory)
    count_q = select(func.count(RolloutHistory.id))

    if feature_flag_id is not None:
        base_q = base_q.where(RolloutHistory.feature_flag_id == feature_flag_id)
        count_q = count_q.where(RolloutHistory.feature_flag_id == feature_flag_id)
    if environment_name:
        env_norm = environment_name.strip().lower()
        base_q = base_q.where(RolloutHistory.environment_name == env_norm)
        count_q = count_q.where(RolloutHistory.environment_name == env_norm)

    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                base_q.order_by(RolloutHistory.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )

    flag_ids = {r.feature_flag_id for r in rows}
    flag_map: dict[uuid.UUID, str] = {}
    if flag_ids:
        flags = (
            await session.execute(select(FeatureFlag).where(FeatureFlag.id.in_(flag_ids)))
        ).scalars().all()
        flag_map = {f.id: f.key for f in flags}

    items: list[dict[str, Any]] = []
    for r in rows:
        d = RolloutHistoryOut.model_validate(r).model_dump(mode="json")
        d["flag_key"] = flag_map.get(r.feature_flag_id)
        items.append(d)

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
