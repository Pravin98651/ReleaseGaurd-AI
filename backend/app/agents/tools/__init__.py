"""Registry of all 34 manual agent tools and specialist allowlists."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.genai import types

from app.agents.gemini_client import build_gemini_tools
from app.agents.tools.context import ToolExecutionContext
from app.agents.tools.experiment_tools import (
    EXPERIMENT_TOOL_HANDLERS,
    get_experiment_tool_names,
)
from app.agents.tools.flag_tools import FLAG_TOOL_HANDLERS, get_flag_tool_names
from app.agents.tools.pr_tools import PR_TOOL_HANDLERS, get_pr_tool_names
from app.schemas.tools import TOOL_INPUT_SCHEMAS, validate_tool_args

ToolHandler = Callable[[ToolExecutionContext, Any], Awaitable[dict[str, Any]]]

ALL_TOOL_HANDLERS: dict[str, ToolHandler] = {
    **PR_TOOL_HANDLERS,
    **FLAG_TOOL_HANDLERS,
    **EXPERIMENT_TOOL_HANDLERS,
}

PR_AGENT_TOOLS: tuple[str, ...] = tuple(get_pr_tool_names())
FLAG_AGENT_TOOLS: tuple[str, ...] = tuple(get_flag_tool_names())
EXPERIMENT_AGENT_TOOLS: tuple[str, ...] = tuple(get_experiment_tool_names())

SPECIALIST_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "pr_agent": frozenset(PR_AGENT_TOOLS),
    "flag_agent": frozenset(FLAG_AGENT_TOOLS),
    "experiment_agent": frozenset(EXPERIMENT_AGENT_TOOLS),
}


def get_tool_declarations_for_specialist(specialist: str) -> list[types.Tool]:
    """Return google-genai Tool declarations scoped strictly to the given specialist."""
    allowlist = SPECIALIST_TOOL_ALLOWLIST.get(specialist)
    if allowlist is None:
        raise ValueError(f"Unknown specialist '{specialist}'.")
    scoped_schemas = {
        name: TOOL_INPUT_SCHEMAS[name]
        for name in TOOL_INPUT_SCHEMAS
        if name in allowlist
    }
    return build_gemini_tools(scoped_schemas)


async def execute_tool_call(
    ctx: ToolExecutionContext,
    tool_name: str,
    raw_args: dict[str, Any],
) -> dict[str, Any]:
    """Validate tool arguments against Pydantic v2 schema and run deterministic handler."""
    validated_args = validate_tool_args(tool_name, raw_args)
    handler = ALL_TOOL_HANDLERS.get(tool_name)
    if handler is None:
        raise ValueError(f"Tool '{tool_name}' has no registered handler.")
    return await handler(ctx, validated_args)


__all__ = [
    "ALL_TOOL_HANDLERS",
    "EXPERIMENT_AGENT_TOOLS",
    "FLAG_AGENT_TOOLS",
    "PR_AGENT_TOOLS",
    "SPECIALIST_TOOL_ALLOWLIST",
    "ToolExecutionContext",
    "execute_tool_call",
    "get_tool_declarations_for_specialist",
]
