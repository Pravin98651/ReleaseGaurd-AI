"""Pydantic v2 schemas for Control Plane authentication, sessions, and RBAC user management."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import UserRole
from app.schemas.common import ORMBaseModel


class UserOut(ORMBaseModel):
    """Public operator profile without password_hash or secret tokens."""

    id: UUID
    email: str
    display_name: str
    role: UserRole
    github_login: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AuthLoginRequest(BaseModel):
    """Email + password login request."""

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class AuthTokenResponse(BaseModel):
    """Issued bearer/cookie session payload returned on successful sign-in."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in_seconds: int
    user: UserOut


class AuthStatusResponse(BaseModel):
    """Current authentication configuration and caller identity status."""

    require_auth: bool
    authenticated: bool
    github_oauth_enabled: bool
    user: UserOut | None = None


class UserCreateRequest(BaseModel):
    """ADMIN-only request to create a workspace operator with an explicit RBAC role."""

    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=255)
    role: UserRole | Literal["developer"] = UserRole.ENGINEER


class GitHubAuthCallbackRequest(BaseModel):
    """Exchange GitHub OAuth code + CSRF state for a Control Plane user session."""

    code: str = Field(min_length=1)
    state: str = Field(min_length=1)
