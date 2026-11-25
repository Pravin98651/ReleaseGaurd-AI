"""LangGraph SupervisorState definition using JSON-safe serialized structures."""

from __future__ import annotations

from typing import Any, TypedDict


class SupervisorState(TypedDict, total=False):
    """State persisted across LangGraph checkpoints for each agent task."""

    task_id: str
    session_id: str
    thread_id: str
    user_id: str | None
    user_prompt: str
    specialist_hint: str | None
    active_specialist: str
    context_scope: dict[str, Any]
    conversation_contents: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    approved_tool_calls: list[dict[str, Any]]
    queued_approval: dict[str, Any] | None
    tool_results: list[dict[str, Any]]
    iteration_count: int
    final_response: str | None
    error_message: str | None
    token_usage: dict[str, int]
    status: str
