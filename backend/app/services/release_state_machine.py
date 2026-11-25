"""Authoritative Release Lifecycle state machine, optimistic concurrency, and event persistence."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.security import redact_dict, scrub_secrets
from app.models import (
    ActorType,
    AuditLog,
    AuditStatus,
    PolicyCategory,
    Release,
    ReleaseEvent,
    ReleaseStageKey,
    ReleaseStatus,
)
from app.services.notification_service import broadcast_workspace_event


class ReleaseTransitionError(Exception):
    """Raised on invalid state transitions or optimistic concurrency version conflicts."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


TERMINAL_RELEASE_STATES: frozenset[ReleaseStatus] = frozenset(
    {
        ReleaseStatus.COMPLETED,
        ReleaseStatus.ROLLED_BACK,
        ReleaseStatus.CANCELLED,
    }
)

ACTIVE_EXECUTION_STATES: frozenset[ReleaseStatus] = frozenset(
    {
        ReleaseStatus.INITIALIZING,
        ReleaseStatus.ANALYZING,
        ReleaseStatus.VALIDATING,
        ReleaseStatus.FLAG_PROVISIONING,
        ReleaseStatus.ROLLING_OUT,
        ReleaseStatus.MONITORING,
    }
)

ALLOWED_TRANSITIONS: dict[ReleaseStatus, set[ReleaseStatus]] = {
    ReleaseStatus.DRAFT: {
        ReleaseStatus.INITIALIZING,
        ReleaseStatus.ANALYZING,
        ReleaseStatus.AWAITING_PATCH,
        ReleaseStatus.VALIDATING,
        ReleaseStatus.READY_FOR_FLAG,
        ReleaseStatus.FLAG_PROVISIONING,
        ReleaseStatus.EXPERIMENT_RUNNING,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.APPROVED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.PAUSED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.INITIALIZING: {
        ReleaseStatus.ANALYZING,
        ReleaseStatus.AWAITING_PATCH,
        ReleaseStatus.VALIDATING,
        ReleaseStatus.READY_FOR_FLAG,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.ANALYZING: {
        ReleaseStatus.AWAITING_PATCH,
        ReleaseStatus.VALIDATING,
        ReleaseStatus.READY_FOR_FLAG,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.AWAITING_PATCH: {
        ReleaseStatus.ANALYZING,
        ReleaseStatus.VALIDATING,
        ReleaseStatus.READY_FOR_FLAG,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.VALIDATING: {
        ReleaseStatus.AWAITING_PATCH,
        ReleaseStatus.READY_FOR_FLAG,
        ReleaseStatus.FLAG_PROVISIONING,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.READY_FOR_FLAG: {
        ReleaseStatus.FLAG_PROVISIONING,
        ReleaseStatus.EXPERIMENT_RUNNING,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.APPROVED,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.FLAG_PROVISIONING: {
        ReleaseStatus.EXPERIMENT_RUNNING,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.APPROVED,
        ReleaseStatus.ROLLING_OUT,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.EXPERIMENT_RUNNING: {
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.APPROVED,
        ReleaseStatus.ROLLING_OUT,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.ROLLED_BACK,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.AWAITING_APPROVAL: {
        ReleaseStatus.APPROVED,
        ReleaseStatus.ROLLING_OUT,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.APPROVED: {
        ReleaseStatus.ROLLING_OUT,
        ReleaseStatus.MONITORING,
        ReleaseStatus.COMPLETED,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.ROLLED_BACK,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.ROLLING_OUT: {
        ReleaseStatus.MONITORING,
        ReleaseStatus.COMPLETED,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.ROLLED_BACK,
    },
    ReleaseStatus.MONITORING: {
        ReleaseStatus.COMPLETED,
        ReleaseStatus.PAUSED,
        ReleaseStatus.BLOCKED,
        ReleaseStatus.FAILED,
        ReleaseStatus.ROLLED_BACK,
    },
    ReleaseStatus.PAUSED: {
        ReleaseStatus.DRAFT,
        ReleaseStatus.INITIALIZING,
        ReleaseStatus.ANALYZING,
        ReleaseStatus.AWAITING_PATCH,
        ReleaseStatus.VALIDATING,
        ReleaseStatus.READY_FOR_FLAG,
        ReleaseStatus.FLAG_PROVISIONING,
        ReleaseStatus.EXPERIMENT_RUNNING,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.APPROVED,
        ReleaseStatus.ROLLING_OUT,
        ReleaseStatus.MONITORING,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.BLOCKED: {
        ReleaseStatus.INITIALIZING,
        ReleaseStatus.ANALYZING,
        ReleaseStatus.AWAITING_PATCH,
        ReleaseStatus.VALIDATING,
        ReleaseStatus.READY_FOR_FLAG,
        ReleaseStatus.FLAG_PROVISIONING,
        ReleaseStatus.EXPERIMENT_RUNNING,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.APPROVED,
        ReleaseStatus.PAUSED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.FAILED: {
        ReleaseStatus.INITIALIZING,
        ReleaseStatus.ANALYZING,
        ReleaseStatus.AWAITING_PATCH,
        ReleaseStatus.VALIDATING,
        ReleaseStatus.READY_FOR_FLAG,
        ReleaseStatus.FLAG_PROVISIONING,
        ReleaseStatus.EXPERIMENT_RUNNING,
        ReleaseStatus.AWAITING_APPROVAL,
        ReleaseStatus.ROLLING_OUT,
        ReleaseStatus.MONITORING,
        ReleaseStatus.PAUSED,
        ReleaseStatus.CANCELLED,
    },
    ReleaseStatus.COMPLETED: set(),
    ReleaseStatus.ROLLED_BACK: set(),
    ReleaseStatus.CANCELLED: set(),
}


def can_transition(from_status: ReleaseStatus, to_status: ReleaseStatus) -> bool:
    """Return True if the state machine permits transitioning from_status -> to_status."""
    if from_status == to_status:
        return True
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())


async def record_release_event(
    session: AsyncSession,
    release: Release,
    *,
    event_type: str,
    title: str,
    message: str | None = None,
    from_status: ReleaseStatus | None = None,
    to_status: ReleaseStatus | None = None,
    stage_key: ReleaseStageKey | None = None,
    actor_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.SYSTEM,
    payload: dict[str, Any] | None = None,
) -> ReleaseEvent:
    """Append an immutable ReleaseEvent to the timeline."""
    event = ReleaseEvent(
        release_id=release.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        stage_key=stage_key,
        actor_id=actor_id,
        actor_type=actor_type,
        title=scrub_secrets(title)[:255],
        message=scrub_secrets(message) if message else None,
        payload_json=redact_dict(dict(payload or {})),
    )
    session.add(event)
    return event


async def transition_release(
    session: AsyncSession,
    release: Release,
    target_status: ReleaseStatus,
    *,
    actor_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.SYSTEM,
    expected_version: int | None = None,
    event_type: str | None = None,
    stage_key: ReleaseStageKey | None = None,
    title: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Release:
    """Validate and apply a Release state transition, recording ReleaseEvent + AuditLog."""
    if expected_version is not None and release.version != expected_version:
        raise ReleaseTransitionError(
            f"Version conflict on release {release.release_key}: "
            f"expected {expected_version}, current {release.version}.",
            status_code=409,
        )

    from_status = release.status
    if from_status == target_status:
        return release

    if from_status in TERMINAL_RELEASE_STATES:
        raise ReleaseTransitionError(
            f"Release {release.release_key} is in terminal state '{from_status.value}' "
            f"and cannot transition to '{target_status.value}'.",
            status_code=422,
        )

    if not can_transition(from_status, target_status):
        raise ReleaseTransitionError(
            f"Invalid transition for release {release.release_key}: "
            f"'{from_status.value}' -> '{target_status.value}'.",
            status_code=422,
        )

    now = utcnow()
    if target_status == ReleaseStatus.PAUSED:
        release.previous_status = from_status
        release.paused_at = now
    elif from_status == ReleaseStatus.PAUSED and target_status != ReleaseStatus.CANCELLED:
        release.previous_status = None

    if target_status == ReleaseStatus.CANCELLED:
        release.cancelled_at = now
    elif target_status == ReleaseStatus.COMPLETED:
        release.completed_at = now
    elif target_status in ACTIVE_EXECUTION_STATES and release.started_at is None:
        release.started_at = now

    if target_status not in {ReleaseStatus.FAILED, ReleaseStatus.BLOCKED}:
        release.error_message = None

    release.status = target_status
    release.version += 1
    release.updated_at = now

    evt_type = event_type or f"release.status.{target_status.value}"
    evt_title = title or f"Status changed: {from_status.value} -> {target_status.value}"
    await record_release_event(
        session,
        release,
        event_type=evt_type,
        title=evt_title,
        message=message,
        from_status=from_status,
        to_status=target_status,
        stage_key=stage_key,
        actor_id=actor_id,
        actor_type=actor_type,
        payload={**(payload or {}), "version": release.version},
    )

    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=str(actor_id) if actor_id else None,
            event_action=f"release.transition.{target_status.value}",
            policy_category=PolicyCategory.A,
            resource_type="release",
            resource_id=str(release.id),
            environment_name=release.target_environment,
            status=AuditStatus.SUCCESS,
            details=redact_dict(
                {
                    "release_key": release.release_key,
                    "from_status": from_status.value,
                    "to_status": target_status.value,
                    "version": release.version,
                    "message": message,
                }
            ),
        )
    )

    await broadcast_workspace_event(
        "release.updated",
        {
            "release_id": str(release.id),
            "release_key": release.release_key,
            "from_status": from_status.value,
            "status": target_status.value,
            "version": release.version,
        },
    )
    return release
