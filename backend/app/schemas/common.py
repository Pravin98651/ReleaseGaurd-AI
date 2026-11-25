"""Common Pydantic v2 base models, pagination, errors, and policy response wrappers."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMBaseModel(BaseModel):
    """Base schema enabled for SQLAlchemy 2.0 ORM attribute serialization."""

    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated list envelope matching docs/api.md Sec 1.1."""

    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ErrorDetail(BaseModel):
    """Structured error body matching docs/api.md Sec 1.1."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    """Top-level error response envelope."""

    error: ErrorDetail


class ExecutionStatus(StrEnum):
    EXECUTED = "executed"
    APPROVAL_REQUIRED = "approval_required"


class PolicyGateResponse(BaseModel, Generic[T]):
    """Standard mutation response indicating immediate execution or queued approval."""

    execution_status: ExecutionStatus
    data: T | None = None
    approval: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    """System health check response for GET /health."""

    status: str = "ok"
    database: str
    gemini_configured: bool
    github_connected: bool
    flagsmith_connected: bool
    analytics_mode: str
