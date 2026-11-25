"""Pydantic v2 schemas for Notifications, Preferences, and Deep System Health."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import NotificationCategory
from app.schemas.common import ORMBaseModel


class NotificationOut(ORMBaseModel):
    id: UUID
    user_id: UUID | None = None
    category: NotificationCategory
    severity: Literal["info", "warning", "high", "critical"] | str
    title: str
    message: str
    resource_type: str | None = None
    resource_id: str | None = None
    environment_name: str | None = None
    action_href: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    is_read: bool
    read_at: datetime | None = None
    is_resolved: bool
    resolved_at: datetime | None = None
    created_at: datetime


class NotificationUnreadCountOut(BaseModel):
    unread_count: int
    critical_unread_count: int = 0


class NotificationPreferenceOut(BaseModel):
    in_app_enabled: bool = True
    severity_threshold: Literal["info", "warning", "high", "critical"] = "info"
    muted_categories: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class NotificationPreferenceUpdate(BaseModel):
    in_app_enabled: bool | None = None
    severity_threshold: Literal["info", "warning", "high", "critical"] | None = None
    muted_categories: list[str] | None = None
