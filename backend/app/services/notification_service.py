"""Persistent Notification creation, preference enforcement, secret scrubbing, and workspace SSE broadcast."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.security import redact_dict, scrub_secrets
from app.models import (
    Notification,
    NotificationCategory,
    NotificationPreference,
)
from app.services.sse_bus import sse_bus

logger = logging.getLogger("releaseguard.notifications")

_SEVERITY_RANK: dict[str, int] = {
    "info": 0,
    "warning": 1,
    "high": 2,
    "critical": 3,
}


async def get_effective_notification_preference(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
) -> NotificationPreference | None:
    """Return user-specific preference if present, else workspace default preference."""
    if user_id is not None:
        pref = (
            await session.execute(
                select(NotificationPreference).where(NotificationPreference.user_id == user_id)
            )
        ).scalar_one_or_none()
        if pref is not None:
            return pref
    return (
        await session.execute(
            select(NotificationPreference).where(NotificationPreference.user_id.is_(None))
        )
    ).scalar_one_or_none()


async def create_notification(
    session: AsyncSession,
    *,
    category: NotificationCategory,
    title: str,
    message: str,
    severity: str = "info",
    user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    environment_name: str | None = None,
    action_href: str | None = None,
    metadata: dict[str, Any] | None = None,
    publish_sse: bool = True,
    dedupe_minutes: int | None = None,
) -> Notification | None:
    """Persist a secret-redacted notification and broadcast workspace SSE update."""
    sev = severity.strip().lower()
    if sev not in _SEVERITY_RANK:
        sev = "info"

    if dedupe_minutes and dedupe_minutes > 0 and resource_id is not None:
        cutoff = utcnow() - timedelta(minutes=dedupe_minutes)
        dup_stmt = (
            select(Notification)
            .where(
                Notification.category == category,
                Notification.resource_id == str(resource_id),
                Notification.is_resolved.is_(False),
                Notification.created_at >= cutoff,
            )
            .limit(1)
        )
        existing_dup = (await session.execute(dup_stmt)).scalar_one_or_none()
        if existing_dup is not None:
            return existing_dup

    pref = await get_effective_notification_preference(session, user_id=user_id)
    if pref is not None:
        if not pref.in_app_enabled and sev != "critical":
            return None
        muted = {str(c) for c in (pref.muted_categories or [])}
        if category.value in muted and sev != "critical":
            return None
        thresh_rank = _SEVERITY_RANK.get(str(pref.severity_threshold or "info").lower(), 0)
        if _SEVERITY_RANK[sev] < thresh_rank and sev != "critical":
            return None

    clean_title = scrub_secrets(title).strip()[:255]
    clean_message = scrub_secrets(message).strip()[:4000]
    clean_meta = redact_dict(dict(metadata or {}))

    row = Notification(
        user_id=user_id,
        category=category,
        severity=sev,
        title=clean_title,
        message=clean_message,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        environment_name=environment_name.strip().lower() if environment_name else None,
        action_href=action_href,
        metadata_json=clean_meta,
    )
    session.add(row)
    await session.flush()

    if publish_sse:
        try:
            await sse_bus.publish_workspace_event(
                "notification.created",
                {
                    "notification_id": str(row.id),
                    "user_id": str(user_id) if user_id else None,
                    "category": category.value,
                    "severity": sev,
                    "title": clean_title,
                    "resource_type": resource_type,
                    "resource_id": str(resource_id) if resource_id is not None else None,
                    "action_href": action_href,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Non-fatal SSE broadcast failure for notification %s: %s", row.id, exc)

    return row


async def broadcast_workspace_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Publish a sanitized real-time domain event to subscribed workspace SSE streams."""
    try:
        await sse_bus.publish_workspace_event(event_type, redact_dict(dict(payload or {})))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Workspace SSE broadcast failed (%s): %s", event_type, exc)
