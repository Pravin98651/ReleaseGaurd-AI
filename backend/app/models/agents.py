"""AgentSession, AgentTask, and LangGraphCheckpoint ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONBCompat, TZDateTime, utcnow
from app.models.enums import (
    AgentSessionStatus,
    AgentTaskStatus,
    SpecialistRole,
    str_enum,
)

if TYPE_CHECKING:
    from app.models.github import CodeChange
    from app.models.governance import Approval
    from app.models.identity import User


class AgentSession(Base):
    """Conversation and LangGraph thread context for /agent and module runs."""

    __tablename__ = "agent_sessions"
    __table_args__ = (
        Index("ix_agent_sessions_thread_id", "thread_id", unique=True),
        Index("ix_agent_sessions_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New Agent Session")
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    active_specialist: Mapped[SpecialistRole] = mapped_column(
        str_enum(SpecialistRole, length=50),
        nullable=False,
        default=SpecialistRole.SUPERVISOR,
    )
    status: Mapped[AgentSessionStatus] = mapped_column(
        str_enum(AgentSessionStatus, length=50),
        nullable=False,
        default=AgentSessionStatus.ACTIVE,
    )
    context_scope: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    message_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    user: Mapped[User | None] = relationship(back_populates="agent_sessions")
    tasks: Mapped[list[AgentTask]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AgentTask(Base):
    """Individual LangGraph execution runs backing SSE streaming and polling replay."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("ix_agent_tasks_session_created", "session_id", "created_at"),
        Index("ix_agent_tasks_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False
    )
    specialist: Mapped[SpecialistRole] = mapped_column(
        str_enum(SpecialistRole, length=50), nullable=False
    )
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AgentTaskStatus] = mapped_column(
        str_enum(AgentTaskStatus, length=50),
        nullable=False,
        default=AgentTaskStatus.QUEUED,
    )
    steps_log: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemini_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    session: Mapped[AgentSession] = relationship(back_populates="tasks")
    code_changes: Mapped[list[CodeChange]] = relationship(back_populates="agent_task")
    approvals: Mapped[list[Approval]] = relationship(back_populates="agent_task")


class LangGraphCheckpoint(Base):
    """Portable persistence table backing SqlAlchemyCheckpointSaver across Postgres and SQLite."""

    __tablename__ = "langgraph_checkpoints"
    __table_args__ = (
        Index("ix_lg_ckpt_thread_created", "thread_id", "checkpoint_ns", "created_at"),
    )

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        String(128), primary_key=True, default="", server_default=""
    )
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkpoint_json: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    pending_writes_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
