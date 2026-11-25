"""Persistent Notification Centre, unread count, resolve/read, and preferences router."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import and_, delete, func, or_, select, update

from app.api.deps import AuthenticatedOperator, DBSession
from app.api.v1.errors import APIException
from app.core.database import utcnow
from app.models import (
    Notification,
    NotificationCategory,
    NotificationPreference,
    User,
    UserRole,
)
from app.schemas import (
    NotificationOut,
    NotificationPreferenceOut,
    NotificationPreferenceUpdate,
    NotificationUnreadCountOut,
    PaginatedResponse,
)
from app.services.notification_service import (
    broadcast_workspace_event,
    get_effective_notification_preference,
)

notifications_router = APIRouter(prefix="/notifications", tags=["notifications"])


def _visible_to_operator(operator: User) -> Any:
    if operator.role == UserRole.ADMIN:
        return True
    return or_(Notification.user_id.is_(None), Notification.user_id == operator.id)


def _assert_notification_access(operator: User, row: Notification) -> None:
    if operator.role == UserRole.ADMIN:
        return
    if row.user_id is not None and row.user_id != operator.id:
        raise APIException(
            403,
            "FORBIDDEN_NOTIFICATION_ACCESS",
            "You are not authorized to access or modify another operator's private notification.",
        )


@notifications_router.get("", response_model=PaginatedResponse[NotificationOut])
async def list_notifications(
    session: DBSession,
    operator: AuthenticatedOperator,
    category: Annotated[NotificationCategory | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    unread_only: Annotated[bool, Query()] = False,
    unresolved_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[NotificationOut]:
    """List persisted operator/workspace notifications newest first."""
    conds: list[Any] = [_visible_to_operator(operator)]
    if category is not None:
        conds.append(Notification.category == category)
    if severity:
        conds.append(Notification.severity == severity.strip().lower())
    if unread_only:
        conds.append(Notification.is_read.is_(False))
    if unresolved_only:
        conds.append(Notification.is_resolved.is_(False))

    where_clause = and_(*conds)
    total = int(
        (await session.execute(select(func.count(Notification.id)).where(where_clause))).scalar_one()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(Notification)
                .where(where_clause)
                .order_by(Notification.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse(
        items=[NotificationOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@notifications_router.get("/unread-count", response_model=NotificationUnreadCountOut)
async def get_unread_count(
    session: DBSession,
    operator: AuthenticatedOperator,
) -> NotificationUnreadCountOut:
    """Return unread and critical unread notification counts for header bell badge."""
    vis = _visible_to_operator(operator)
    unread = int(
        (
            await session.execute(
                select(func.count(Notification.id)).where(vis, Notification.is_read.is_(False))
            )
        ).scalar_one()
        or 0
    )
    critical_unread = int(
        (
            await session.execute(
                select(func.count(Notification.id)).where(
                    vis,
                    Notification.is_read.is_(False),
                    Notification.severity == "critical",
                )
            )
        ).scalar_one()
        or 0
    )
    return NotificationUnreadCountOut(
        unread_count=unread,
        critical_unread_count=critical_unread,
    )


@notifications_router.patch("/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: uuid.UUID,
    session: DBSession,
    operator: AuthenticatedOperator,
) -> NotificationOut:
    row = await session.get(Notification, notification_id)
    if row is None:
        raise APIException(404, "NOTIFICATION_NOT_FOUND", f"Notification {notification_id} not found.")
    _assert_notification_access(operator, row)

    now = utcnow()
    if not row.is_read:
        row.is_read = True
        row.read_at = now
        await session.commit()
        await session.refresh(row)
        await broadcast_workspace_event("notification.read", {"notification_id": str(row.id)})
    return NotificationOut.model_validate(row)


@notifications_router.patch("/{notification_id}/resolve", response_model=NotificationOut)
async def mark_notification_resolved(
    notification_id: uuid.UUID,
    session: DBSession,
    operator: AuthenticatedOperator,
) -> NotificationOut:
    row = await session.get(Notification, notification_id)
    if row is None:
        raise APIException(404, "NOTIFICATION_NOT_FOUND", f"Notification {notification_id} not found.")
    _assert_notification_access(operator, row)

    now = utcnow()
    row.is_read = True
    row.read_at = row.read_at or now
    row.is_resolved = True
    row.resolved_at = now
    await session.commit()
    await session.refresh(row)
    await broadcast_workspace_event("notification.resolved", {"notification_id": str(row.id)})
    return NotificationOut.model_validate(row)


@notifications_router.post("/read-all")
async def mark_all_notifications_read(
    session: DBSession,
    operator: AuthenticatedOperator,
) -> dict[str, Any]:
    now = utcnow()
    vis = _visible_to_operator(operator)
    res = await session.execute(
        update(Notification)
        .where(vis, Notification.is_read.is_(False))
        .values(is_read=True, read_at=now)
    )
    updated = int(res.rowcount or 0)
    await session.commit()
    if updated > 0:
        await broadcast_workspace_event("notification.read_all", {"count": updated})
    return {"marked_read": updated}


@notifications_router.post("/clear-resolved")
async def clear_resolved_notifications(
    session: DBSession,
    operator: AuthenticatedOperator,
) -> dict[str, Any]:
    vis = _visible_to_operator(operator)
    res = await session.execute(
        delete(Notification).where(
            vis,
            or_(Notification.is_resolved.is_(True), Notification.is_read.is_(True)),
        )
    )
    removed = int(res.rowcount or 0)
    await session.commit()
    if removed > 0:
        await broadcast_workspace_event("notification.cleared", {"count": removed})
    return {"cleared_count": removed}


@notifications_router.get("/preferences", response_model=NotificationPreferenceOut)
async def get_notification_preferences(
    session: DBSession,
    operator: AuthenticatedOperator,
) -> NotificationPreferenceOut:
    pref = await get_effective_notification_preference(session, user_id=operator.id)
    if pref is None:
        return NotificationPreferenceOut()
    return NotificationPreferenceOut(
        in_app_enabled=pref.in_app_enabled,
        severity_threshold=pref.severity_threshold,  # type: ignore[arg-type]
        muted_categories=list(pref.muted_categories or []),
        updated_at=pref.updated_at,
    )


@notifications_router.patch("/preferences", response_model=NotificationPreferenceOut)
async def update_notification_preferences(
    body: NotificationPreferenceUpdate,
    session: DBSession,
    operator: AuthenticatedOperator,
) -> NotificationPreferenceOut:
    pref = (
        await session.execute(
            select(NotificationPreference).where(NotificationPreference.user_id == operator.id)
        )
    ).scalar_one_or_none()
    if pref is None:
        pref = NotificationPreference(user_id=operator.id)
        session.add(pref)

    if body.in_app_enabled is not None:
        pref.in_app_enabled = body.in_app_enabled
    if body.severity_threshold is not None:
        pref.severity_threshold = body.severity_threshold
    if body.muted_categories is not None:
        pref.muted_categories = [str(c) for c in body.muted_categories]
    pref.updated_at = utcnow()
    await session.commit()
    await session.refresh(pref)
    return NotificationPreferenceOut(
        in_app_enabled=pref.in_app_enabled,
        severity_threshold=pref.severity_threshold,  # type: ignore[arg-type]
        muted_categories=list(pref.muted_categories or []),
        updated_at=pref.updated_at,
    )
