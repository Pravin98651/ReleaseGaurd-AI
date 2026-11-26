"""LangGraph StateGraph supervisor coordinating pr_agent, flag_agent, and experiment_agent."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.approval_executor import capture_expected_precondition
from app.agents.checkpointer import SqlAlchemyCheckpointSaver
from app.agents.gemini_client import (
    GeminiClient,
    GeminiConfigurationError,
    extract_response_parts,
)
from app.agents.specialists import (
    EXPERIMENT_AGENT_SYSTEM_PROMPT,
    FLAG_AGENT_SYSTEM_PROMPT,
    PR_AGENT_SYSTEM_PROMPT,
)
from app.agents.state import SupervisorState
from app.agents.tools import (
    SPECIALIST_TOOL_ALLOWLIST,
    ToolExecutionContext,
    execute_tool_call,
    get_tool_declarations_for_specialist,
)
from app.core.database import get_session_factory, utcnow
from app.core.security import redact_dict, scrub_secrets
from app.models import (
    ActorType,
    AgentSession,
    AgentSessionStatus,
    AgentTask,
    AgentTaskStatus,
    AuditStatus,
    PolicyCategory,
    SpecialistRole,
)
from app.schemas.tools import validate_tool_args
from app.services.policy_engine import (
    PolicyEngine,
    PolicyViolationError,
    is_production_environment,
)
from app.services.sse_bus import sse_bus

MAX_ITERATIONS = 12

_SPECIALIST_PROMPTS: dict[str, str] = {
    "pr_agent": PR_AGENT_SYSTEM_PROMPT,
    "flag_agent": FLAG_AGENT_SYSTEM_PROMPT,
    "experiment_agent": EXPERIMENT_AGENT_SYSTEM_PROMPT,
}


def route_specialist(user_prompt: str, specialist_hint: str | None = None) -> str:
    """Deterministically route to pr_agent, flag_agent, or experiment_agent."""
    if specialist_hint in _SPECIALIST_PROMPTS:
        return specialist_hint
    text = user_prompt.lower()
    if any(
        kw in text
        for kw in (
            "which feature flags",
            "which flags",
            "why is user",
            "seeing the",
            "receives in the",
            "explain identity",
            "identity",
            "protect it with a feature flag",
            "protect with a feature flag",
            "feature request",
        )
    ):
        return "flag_agent"
    if any(
        kw in text
        for kw in (
            "experiment",
            "bayesian",
            "posterior",
            "recommendation",
            "incident",
            "spike",
            "abandoned",
            "payment_failed",
            "conversion",
            "winning version",
            "winner",
        )
    ):
        return "experiment_agent"
    if any(
        kw in text
        for kw in (
            "pull request",
            "pr ",
            "pr#",
            "diff",
            "patch",
            "draft pr",
            "branch",
            "repository",
            "repo",
            "cleanup",
        )
    ):
        return "pr_agent"
    if any(
        kw in text
        for kw in (
            "flag",
            "rollout",
            "rollback",
            "toggle",
            "segment",
            "multivariate",
            "flagsmith",
            "health",
            "stale",
            "orphaned",
        )
    ):
        return "flag_agent"
    return "pr_agent"


def _check_scope_violation(
    tool_name: str,
    args: dict[str, Any],
    context_scope: dict[str, Any],
) -> str | None:
    """Cross-check target repo/project/env against bound session context_scope (Sec 4.4 Layer 3)."""
    if not context_scope:
        return None

    scoped_repo = context_scope.get("repository_id")
    if scoped_repo and args.get("repository_id") and str(args["repository_id"]) != str(scoped_repo):
        return (
            f"Tool '{tool_name}' targeted repository_id={args['repository_id']} outside "
            f"session scope repository_id={scoped_repo}."
        )

    scoped_proj = context_scope.get("flagsmith_project_id")
    if (
        scoped_proj is not None
        and args.get("project_id") is not None
        and int(args["project_id"]) != int(scoped_proj)
    ):
        return (
            f"Tool '{tool_name}' targeted project_id={args['project_id']} outside "
            f"session scope flagsmith_project_id={scoped_proj}."
        )

    scoped_env = context_scope.get("environment")
    tool_env = args.get("environment_name")
    if (
        scoped_env
        and tool_env
        and str(tool_env).strip().lower() != str(scoped_env).strip().lower()
        and is_production_environment(str(tool_env))
    ):
        return (
            f"Tool '{tool_name}' attempted cross-environment escalation into "
            f"'{tool_env}' outside session scope '{scoped_env}'."
        )
    return None


class ReleaseGuardSupervisor:
    """Compiles and executes the LangGraph StateGraph orchestrator."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        gemini_client: GeminiClient | None = None,
        checkpointer: Any | None = None,
        context_factory: Callable[[AsyncSession, SupervisorState], ToolExecutionContext] | None = None,
    ) -> None:
        self.session_factory = session_factory or get_session_factory()
        self.gemini_client = gemini_client or GeminiClient()
        self.checkpointer = checkpointer or SqlAlchemyCheckpointSaver(self.session_factory)
        self.context_factory = context_factory
        self.graph = self._build_graph()

    def _make_tool_context(
        self, session: AsyncSession, state: SupervisorState
    ) -> ToolExecutionContext:
        if self.context_factory is not None:
            return self.context_factory(session, state)
        raw_user_id = state.get("user_id")
        raw_task_id = state.get("task_id")
        return ToolExecutionContext(
            session=session,
            user_id=uuid.UUID(raw_user_id) if raw_user_id else None,
            agent_task_id=uuid.UUID(raw_task_id) if raw_task_id else None,
            context_scope=dict(state.get("context_scope") or {}),
        )

    async def _emit_step(
        self,
        state: SupervisorState,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        task_id = state.get("task_id")
        if not task_id:
            return
        async with self.session_factory() as session:
            try:
                task_uuid = uuid.UUID(str(task_id))
            except ValueError:
                return
            task = await session.get(AgentTask, task_uuid)
            if task is None:
                return
            await sse_bus.publish(
                task_uuid,
                event_type,
                payload,
                session=session,
                commit=True,
            )

    def _build_graph(self) -> Any:
        builder: StateGraph[SupervisorState] = StateGraph(SupervisorState)

        async def supervisor_router(state: SupervisorState) -> dict[str, Any]:
            specialist = route_specialist(
                state.get("user_prompt", ""), state.get("specialist_hint")
            )
            contents = list(state.get("conversation_contents") or [])
            if not contents:
                contents = [
                    {"role": "user", "parts": [{"text": state.get("user_prompt", "")}]}
                ]
            await self._emit_step(
                state,
                "task.started",
                {"task_id": state.get("task_id"), "specialist": specialist},
            )
            await self._emit_step(
                state,
                "agent.thinking",
                {
                    "specialist": specialist,
                    "message": f"Routing task to {specialist} with strict manual function calling.",
                },
            )
            return {
                "active_specialist": specialist,
                "conversation_contents": contents,
                "pending_tool_calls": [],
                "approved_tool_calls": [],
                "tool_results": [],
                "iteration_count": int(state.get("iteration_count") or 0),
                "status": "running",
            }

        async def specialist_llm(state: SupervisorState) -> dict[str, Any]:
            specialist = state.get("active_specialist") or "pr_agent"
            iteration = int(state.get("iteration_count") or 0) + 1
            if iteration > MAX_ITERATIONS:
                return {
                    "pending_tool_calls": [],
                    "final_response": (
                        "Stopped tool execution loop after reaching maximum iteration limit."
                    ),
                    "iteration_count": iteration,
                }

            system_prompt = _SPECIALIST_PROMPTS.get(specialist, PR_AGENT_SYSTEM_PROMPT)
            tools = get_tool_declarations_for_specialist(specialist)
            contents = list(state.get("conversation_contents") or [])

            async with self.session_factory() as session:
                ws = await PolicyEngine.get_workspace_settings(session)
                model_name = ws.gemini_model

            response = await self.gemini_client.generate_content(
                contents=contents,
                system_instruction=system_prompt,
                tools=tools,
                model=model_name,
                temperature=0.1,
            )
            text, function_calls, usage = extract_response_parts(response)

            prev_usage = dict(state.get("token_usage") or {})
            merged_usage = {
                "prompt_tokens": int(prev_usage.get("prompt_tokens", 0))
                + int(usage.get("prompt_tokens", 0)),
                "candidate_tokens": int(prev_usage.get("candidate_tokens", 0))
                + int(usage.get("candidate_tokens", 0)),
                "total_tokens": int(prev_usage.get("total_tokens", 0))
                + int(usage.get("total_tokens", 0)),
            }

            if function_calls:
                model_parts: list[dict[str, Any]] = []
                if text:
                    model_parts.append({"text": text})
                for fc in function_calls:
                    model_parts.append({"function_call": fc})
                contents.append({"role": "model", "parts": model_parts})
                return {
                    "conversation_contents": contents,
                    "pending_tool_calls": function_calls,
                    "approved_tool_calls": [],
                    "iteration_count": iteration,
                    "token_usage": merged_usage,
                }

            if text:
                contents.append({"role": "model", "parts": [{"text": text}]})
            return {
                "conversation_contents": contents,
                "pending_tool_calls": [],
                "final_response": text or "Completed analysis.",
                "iteration_count": iteration,
                "token_usage": merged_usage,
            }

        async def policy_gate(state: SupervisorState) -> dict[str, Any]:
            pending = list(state.get("pending_tool_calls") or [])
            if not pending:
                return {"approved_tool_calls": [], "queued_approval": None}

            current = pending[0]
            remaining = pending[1:]
            tool_name = str(current.get("name", ""))
            raw_args = dict(current.get("args") or {})
            specialist = state.get("active_specialist") or "pr_agent"
            contents = list(state.get("conversation_contents") or [])
            context_scope = dict(state.get("context_scope") or {})

            # Emit proposed tool event before checking policy
            allowlist = SPECIALIST_TOOL_ALLOWLIST.get(specialist, frozenset())
            env_name = (
                str(raw_args["environment_name"])
                if raw_args.get("environment_name")
                else None
            )

            async with self.session_factory() as session:
                # 1. Specialist allowlist enforcement (prompt-injection Layer 4)
                if tool_name not in allowlist:
                    await PolicyEngine.record_audit_log(
                        session,
                        actor_type=ActorType.AGENT,
                        actor_id=specialist,
                        policy_category=PolicyCategory.DENY,
                        event_action="policy.blocked_cross_specialist",
                        resource_type="agent_tool",
                        resource_id=tool_name,
                        environment_name=env_name,
                        status=AuditStatus.BLOCKED,
                        details={"specialist": specialist, "args": redact_dict(raw_args)},
                    )
                    await session.commit()
                    await self._emit_step(
                        state,
                        "agent.tool_proposed",
                        {
                            "tool_name": tool_name,
                            "args_redacted": redact_dict(raw_args),
                            "policy_category": "DENY",
                        },
                    )
                    await self._emit_step(
                        state,
                        "policy.evaluated",
                        {
                            "tool_name": tool_name,
                            "policy_category": "DENY",
                            "action_hash": None,
                            "decision": "deny",
                        },
                    )
                    contents.append(
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "function_response": {
                                        "name": tool_name,
                                        "response": {
                                            "error": "POLICY_DENIED",
                                            "message": (
                                                f"Tool '{tool_name}' is not allowed for '{specialist}'."
                                            ),
                                        },
                                    }
                                }
                            ],
                        }
                    )
                    return {
                        "conversation_contents": contents,
                        "pending_tool_calls": remaining,
                        "approved_tool_calls": [],
                        "queued_approval": None,
                    }

                # 2. Validate tool arguments against Pydantic v2 schema
                try:
                    validate_tool_args(tool_name, raw_args)
                except Exception as exc:  # noqa: BLE001
                    await self._emit_step(
                        state,
                        "agent.tool_proposed",
                        {
                            "tool_name": tool_name,
                            "args_redacted": redact_dict(raw_args),
                            "policy_category": "DENY",
                        },
                    )
                    contents.append(
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "function_response": {
                                        "name": tool_name,
                                        "response": {
                                            "error": "VALIDATION_ERROR",
                                            "message": scrub_secrets(str(exc)),
                                        },
                                    }
                                }
                            ],
                        }
                    )
                    return {
                        "conversation_contents": contents,
                        "pending_tool_calls": remaining,
                        "approved_tool_calls": [],
                        "queued_approval": None,
                    }

                # 3. Resolve env_states if flag_key is targeted
                env_states: dict[str, Any] | None = None
                ctx = self._make_tool_context(session, state)
                if raw_args.get("flag_key"):
                    flag = await ctx.find_feature_flag(flag_key=str(raw_args["flag_key"]))
                    if flag is not None:
                        env_states = dict(flag.env_states or {})

                # 4. Evaluate PolicyEngine classification
                try:
                    category, requires_approval = await PolicyEngine.evaluate_action(
                        session,
                        action_type=tool_name,
                        environment_name=env_name,
                        env_states=env_states,
                        actor_type=ActorType.AGENT,
                        actor_id=specialist,
                        resource_type="agent_tool",
                        resource_id=str(
                            raw_args.get("flag_key")
                            or raw_args.get("code_change_id")
                            or raw_args.get("experiment_id")
                            or tool_name
                        ),
                    )
                    await session.commit()
                except PolicyViolationError as exc:
                    await session.commit()
                    await self._emit_step(
                        state,
                        "agent.tool_proposed",
                        {
                            "tool_name": tool_name,
                            "args_redacted": redact_dict(raw_args),
                            "policy_category": "DENY",
                        },
                    )
                    await self._emit_step(
                        state,
                        "policy.evaluated",
                        {
                            "tool_name": tool_name,
                            "policy_category": "DENY",
                            "action_hash": None,
                            "decision": "deny",
                        },
                    )
                    contents.append(
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "function_response": {
                                        "name": tool_name,
                                        "response": {
                                            "error": "POLICY_DENIED",
                                            "message": exc.message,
                                        },
                                    }
                                }
                            ],
                        }
                    )
                    return {
                        "conversation_contents": contents,
                        "pending_tool_calls": remaining,
                        "approved_tool_calls": [],
                        "queued_approval": None,
                    }

                # 5. Target scope cross-check (Sec 4.4 Layer 3)
                scope_warning = _check_scope_violation(tool_name, raw_args, context_scope)
                if scope_warning is not None and category in (PolicyCategory.B, PolicyCategory.C):
                    category = PolicyCategory.C
                    requires_approval = True

                await self._emit_step(
                    state,
                    "agent.tool_proposed",
                    {
                        "tool_name": tool_name,
                        "args_redacted": redact_dict(raw_args),
                        "policy_category": category.value,
                    },
                )

                if not requires_approval:
                    await self._emit_step(
                        state,
                        "policy.evaluated",
                        {
                            "tool_name": tool_name,
                            "policy_category": category.value,
                            "action_hash": None,
                            "decision": "allow",
                        },
                    )
                    return {
                        "pending_tool_calls": remaining,
                        "approved_tool_calls": [{"name": tool_name, "args": raw_args, "category": category.value}],
                        "queued_approval": None,
                    }

                # Requires human approval (Category C or Category B with confirmation enabled)
                return {
                    "pending_tool_calls": remaining,
                    "approved_tool_calls": [],
                    "queued_approval": {
                        "name": tool_name,
                        "args": raw_args,
                        "policy_category": category.value,
                        "environment_name": env_name,
                        "scope_warning": scope_warning,
                    },
                }

        async def tool_executor(state: SupervisorState) -> dict[str, Any]:
            approved = list(state.get("approved_tool_calls") or [])
            contents = list(state.get("conversation_contents") or [])
            results = list(state.get("tool_results") or [])
            specialist = state.get("active_specialist") or "pr_agent"

            for call in approved:
                tool_name = str(call["name"])
                raw_args = dict(call.get("args") or {})
                category = PolicyCategory(str(call.get("category") or "A"))

                async with self.session_factory() as session:
                    from sqlalchemy import select as sa_select

                    from app.models import AgentExecution
                    from app.services.agent_execution_service import (
                        AgentExecutionService,
                    )

                    exe = (
                        await session.execute(
                            sa_select(AgentExecution)
                            .where(AgentExecution.agent_task_id == uuid.UUID(state["task_id"]))
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    tool_call_rec = None
                    if exe is not None:
                        tool_call_rec = await AgentExecutionService.record_tool_start(
                            session,
                            exe,
                            tool_name=tool_name,
                            tool_category=f"category_{category.value.lower()}",
                            is_reversible=(category == PolicyCategory.A),
                            input_summary=raw_args,
                        )

                    ctx = self._make_tool_context(session, state)
                    try:
                        out = await execute_tool_call(ctx, tool_name, raw_args)
                        status_str = "ok"
                        audit_status = AuditStatus.SUCCESS
                        verified = out.get("verified") if "verified" in out else out.get("matches", True)
                        summary = f"Executed {tool_name} successfully."
                        if exe is not None and tool_call_rec is not None:
                            await AgentExecutionService.record_tool_result(
                                session,
                                exe,
                                tool_call_rec,
                                success=True,
                                output_summary=out if isinstance(out, dict) else {"result": str(out)},
                            )
                    except Exception as exc:  # noqa: BLE001
                        err_msg = scrub_secrets(str(exc))
                        out = {"error": "TOOL_EXECUTION_ERROR", "message": err_msg}
                        status_str = "error"
                        audit_status = AuditStatus.FAILED
                        verified = False
                        summary = f"{tool_name} failed: {err_msg}"
                        if exe is not None and tool_call_rec is not None:
                            await AgentExecutionService.record_tool_result(
                                session,
                                exe,
                                tool_call_rec,
                                success=False,
                                output_summary=out,
                                error_code="TOOL_EXECUTION_ERROR",
                                error_message=err_msg,
                            )

                    await PolicyEngine.record_audit_log(
                        session,
                        actor_type=ActorType.AGENT,
                        actor_id=specialist,
                        policy_category=category,
                        event_action=f"tool.{tool_name}",
                        resource_type="agent_tool",
                        resource_id=str(
                            raw_args.get("flag_key")
                            or raw_args.get("pull_request_id")
                            or raw_args.get("experiment_id")
                            or raw_args.get("code_change_id")
                            or tool_name
                        ),
                        environment_name=raw_args.get("environment_name"),
                        status=audit_status,
                        details={"args": redact_dict(raw_args), "result": redact_dict(out)},
                    )
                    await session.commit()

                await self._emit_step(
                    state,
                    "tool.executed",
                    {
                        "tool_name": tool_name,
                        "status": status_str,
                        "summary": summary,
                        "verified": bool(verified) if verified is not None else True,
                    },
                )
                results.append({"tool_name": tool_name, "output": out})
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "function_response": {
                                    "name": tool_name,
                                    "response": out,
                                }
                            }
                        ],
                    }
                )

            return {
                "conversation_contents": contents,
                "approved_tool_calls": [],
                "tool_results": results,
            }

        async def create_approval_and_interrupt(state: SupervisorState) -> dict[str, Any]:
            queued = dict(state.get("queued_approval") or {})
            tool_name = str(queued["name"])
            raw_args = dict(queued.get("args") or {})
            env_name = queued.get("environment_name")
            scope_warning = queued.get("scope_warning")
            contents = list(state.get("conversation_contents") or [])
            results = list(state.get("tool_results") or [])

            # Persist pending Approval + update AgentTask status BEFORE calling interrupt()
            async with self.session_factory() as session:
                ctx = self._make_tool_context(session, state)
                res_type, res_id, expected_pre, env_states = await capture_expected_precondition(
                    ctx,
                    action_type=tool_name,
                    action_payload=raw_args,
                )
                task_uuid = uuid.UUID(str(state["task_id"])) if state.get("task_id") else None
                user_uuid = uuid.UUID(str(state["user_id"])) if state.get("user_id") else None

                title = f"{tool_name} ({env_name})" if env_name else tool_name
                rationale = str(
                    raw_args.get("reason")
                    or raw_args.get("rationale")
                    or scope_warning
                    or f"Proposed by {state.get('active_specialist', 'agent')}."
                )
                approval = await PolicyEngine.create_approval(
                    session,
                    action_type=tool_name,
                    target_resource_type=res_type,
                    target_resource_id=res_id,
                    environment_name=env_name,
                    title=title[:255],
                    rationale=rationale,
                    action_payload=raw_args,
                    expected_precondition=expected_pre,
                    env_states=env_states,
                    agent_task_id=task_uuid,
                    requested_by_id=user_uuid,
                )
                if task_uuid is not None:
                    task_row = await session.get(AgentTask, task_uuid)
                    if task_row is not None:
                        task_row.status = AgentTaskStatus.AWAITING_APPROVAL
                        task_row.updated_at = utcnow()
                        sess_row = await session.get(AgentSession, task_row.session_id)
                        if sess_row is not None:
                            sess_row.status = AgentSessionStatus.PAUSED_APPROVAL
                            sess_row.updated_at = utcnow()
                await session.commit()
                await session.refresh(approval)

                approval_payload = {
                    "approval_id": str(approval.id),
                    "policy_category": approval.policy_category.value,
                    "title": approval.title,
                    "environment_name": approval.environment_name,
                    "action_hash": approval.action_hash,
                    "expires_at": approval.expires_at.isoformat(),
                }

            await self._emit_step(
                state,
                "policy.evaluated",
                {
                    "tool_name": tool_name,
                    "policy_category": approval_payload["policy_category"],
                    "action_hash": approval_payload["action_hash"],
                    "decision": "require_approval",
                },
            )
            await self._emit_step(
                state,
                "approval.required",
                approval_payload,
            )

            # Suspend execution cleanly and wait for Command(resume=...)
            resume_data: Any = interrupt(approval_payload)
            if not isinstance(resume_data, dict):
                resume_data = {"decision": str(resume_data)}

            decision = str(resume_data.get("decision", "rejected")).lower()
            if decision == "approved":
                if resume_data.get("executed"):
                    # Already executed & verified via /api/v1/approvals/{id}/approve
                    out = dict(resume_data.get("result") or {"approved": True})
                    await self._emit_step(
                        state,
                        "tool.executed",
                        {
                            "tool_name": tool_name,
                            "status": "ok",
                            "summary": f"Approved and executed {tool_name}.",
                            "verified": True,
                        },
                    )
                    results.append({"tool_name": tool_name, "output": out})
                    contents.append(
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "function_response": {
                                        "name": tool_name,
                                        "response": out,
                                    }
                                }
                            ],
                        }
                    )
                    return {
                        "conversation_contents": contents,
                        "queued_approval": None,
                        "approved_tool_calls": [],
                        "tool_results": results,
                        "status": "running",
                    }

                # Direct LangGraph resume in tests where PolicyEngine hasn't executed yet
                return {
                    "queued_approval": None,
                    "approved_tool_calls": [
                        {
                            "name": tool_name,
                            "args": raw_args,
                            "category": approval_payload["policy_category"],
                        }
                    ],
                    "status": "running",
                }

            if decision == "drift":
                drift_out = {
                    "status": "aborted_due_to_live_state_drift",
                    "error": "APPROVAL_REVALIDATION_FAILED",
                    "details": resume_data.get("drift_details") or {},
                }
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {"function_response": {"name": tool_name, "response": drift_out}}
                        ],
                    }
                )
                return {
                    "conversation_contents": contents,
                    "queued_approval": None,
                    "approved_tool_calls": [],
                    "status": "running",
                }

            # Rejected or expired: inform Gemini without retrying the rejected action
            comment = str(resume_data.get("comment") or "Rejected by human operator.")
            rej_out = {
                "status": "rejected_by_human",
                "message": (
                    f"Human operator rejected '{tool_name}': {comment}. "
                    "Do NOT retry this rejected mutation; summarize cleanly for the user."
                ),
            }
            contents.append(
                {
                    "role": "user",
                    "parts": [{"function_response": {"name": tool_name, "response": rej_out}}],
                }
            )
            return {
                "conversation_contents": contents,
                "queued_approval": None,
                "approved_tool_calls": [],
                "status": "running",
            }

        async def finalize_response(state: SupervisorState) -> dict[str, Any]:
            final_text = state.get("final_response") or "Task completed."
            usage = dict(state.get("token_usage") or {})
            await self._emit_step(
                state,
                "agent.completed",
                {"final_response": final_text, "token_usage": usage},
            )
            return {"final_response": final_text, "status": "completed"}

        def after_specialist_llm(state: SupervisorState) -> str:
            if state.get("pending_tool_calls"):
                return "policy_gate"
            return "finalize_response"

        def after_policy_gate(state: SupervisorState) -> str:
            if state.get("queued_approval") is not None:
                return "create_approval_and_interrupt"
            if state.get("approved_tool_calls"):
                return "tool_executor"
            if state.get("pending_tool_calls"):
                return "policy_gate"
            return "specialist_llm"

        def after_tool_executor(state: SupervisorState) -> str:
            if state.get("pending_tool_calls"):
                return "policy_gate"
            return "specialist_llm"

        def after_interrupt(state: SupervisorState) -> str:
            if state.get("approved_tool_calls"):
                return "tool_executor"
            if state.get("pending_tool_calls"):
                return "policy_gate"
            return "specialist_llm"

        builder.add_node("supervisor_router", supervisor_router)
        builder.add_node("specialist_llm", specialist_llm)
        builder.add_node("policy_gate", policy_gate)
        builder.add_node("tool_executor", tool_executor)
        builder.add_node("create_approval_and_interrupt", create_approval_and_interrupt)
        builder.add_node("finalize_response", finalize_response)

        builder.add_edge(START, "supervisor_router")
        builder.add_edge("supervisor_router", "specialist_llm")
        builder.add_conditional_edges(
            "specialist_llm",
            after_specialist_llm,
            {
                "policy_gate": "policy_gate",
                "finalize_response": "finalize_response",
            },
        )
        builder.add_conditional_edges(
            "policy_gate",
            after_policy_gate,
            {
                "tool_executor": "tool_executor",
                "create_approval_and_interrupt": "create_approval_and_interrupt",
                "policy_gate": "policy_gate",
                "specialist_llm": "specialist_llm",
            },
        )
        builder.add_conditional_edges(
            "tool_executor",
            after_tool_executor,
            {
                "policy_gate": "policy_gate",
                "specialist_llm": "specialist_llm",
            },
        )
        builder.add_conditional_edges(
            "create_approval_and_interrupt",
            after_interrupt,
            {
                "tool_executor": "tool_executor",
                "policy_gate": "policy_gate",
                "specialist_llm": "specialist_llm",
            },
        )
        builder.add_edge("finalize_response", END)

        return builder.compile(checkpointer=self.checkpointer)

    async def run_task(
        self,
        *,
        task_id: uuid.UUID,
        session_id: uuid.UUID,
        thread_id: str,
        user_prompt: str,
        specialist_hint: str | None = None,
        context_scope: dict[str, Any] | None = None,
        user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Run a new LangGraph task until completion or Category B/C interrupt."""
        config = {"configurable": {"thread_id": thread_id}}
        initial_state: SupervisorState = {
            "task_id": str(task_id),
            "session_id": str(session_id),
            "thread_id": thread_id,
            "user_id": str(user_id) if user_id else None,
            "user_prompt": user_prompt,
            "specialist_hint": specialist_hint,
            "context_scope": dict(context_scope or {}),
            "conversation_contents": [],
            "token_usage": {"prompt_tokens": 0, "candidate_tokens": 0, "total_tokens": 0},
        }
        return await self._execute_and_sync_db(
            task_id=task_id,
            session_id=session_id,
            invoke_input=initial_state,
            config=config,
        )

    async def resume_task(
        self,
        *,
        task_id: uuid.UUID,
        session_id: uuid.UUID,
        thread_id: str,
        resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Resume a paused LangGraph thread via Command(resume=...)."""
        config = {"configurable": {"thread_id": thread_id}}
        return await self._execute_and_sync_db(
            task_id=task_id,
            session_id=session_id,
            invoke_input=Command(resume=resume_payload),
            config=config,
        )

    async def _execute_and_sync_db(
        self,
        *,
        task_id: uuid.UUID,
        session_id: uuid.UUID,
        invoke_input: Any,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            from sqlalchemy import select as sa_select

            from app.models import AgentExecution, AgentExecutionStatus
            from app.services.agent_execution_service import (
                TERMINAL_EXECUTION_STATES,
                AgentExecutionService,
            )

            task = await session.get(AgentTask, task_id)
            sess = await session.get(AgentSession, session_id)
            if task is not None:
                task.status = AgentTaskStatus.RUNNING
                task.started_at = task.started_at or utcnow()
                task.gemini_model = self.gemini_client.model
            exe = (
                await session.execute(
                    sa_select(AgentExecution).where(AgentExecution.agent_task_id == task_id).limit(1)
                )
            ).scalar_one_or_none()
            if exe is not None and exe.status not in TERMINAL_EXECUTION_STATES:
                if exe.status == AgentExecutionStatus.QUEUED:
                    await AgentExecutionService.transition_execution(
                        session,
                        exe,
                        AgentExecutionStatus.INITIALIZING,
                        stage="initializing",
                        activity="Initializing agent context",
                    )
                if exe.status in {
                    AgentExecutionStatus.INITIALIZING,
                    AgentExecutionStatus.WAITING_FOR_APPROVAL,
                    AgentExecutionStatus.RESUMING,
                }:
                    await AgentExecutionService.transition_execution(
                        session,
                        exe,
                        AgentExecutionStatus.RUNNING,
                        stage="running",
                        activity="Executing specialist workflow",
                    )
            await session.commit()

        try:
            result = await self.graph.ainvoke(invoke_input, config=config)
        except GeminiConfigurationError as exc:
            err = str(exc)
            async with self.session_factory() as session:
                task = await session.get(AgentTask, task_id)
                if task is not None:
                    task.status = AgentTaskStatus.FAILED
                    task.error_message = err
                    task.completed_at = utcnow()
                    await sse_bus.publish(
                        task_id, "task.failed", {"error": err}, session=session, commit=True
                    )
            return {"status": "failed", "error": err}
        except Exception as exc:  # noqa: BLE001
            err = scrub_secrets(str(exc))
            async with self.session_factory() as session:
                task = await session.get(AgentTask, task_id)
                if task is not None:
                    task.status = AgentTaskStatus.FAILED
                    task.error_message = err
                    task.completed_at = utcnow()
                    await sse_bus.publish(
                        task_id, "task.failed", {"error": err}, session=session, commit=True
                    )
            return {"status": "failed", "error": err}

        # Check if graph paused on interrupt()
        interrupted = bool(isinstance(result, dict) and result.get("__interrupt__"))
        async with self.session_factory() as session:
            task = await session.get(AgentTask, task_id)
            sess = await session.get(AgentSession, session_id)
            if task is not None:
                from sqlalchemy import select as sa_select

                from app.models import AgentExecution, AgentExecutionStatus
                from app.services.agent_execution_service import (
                    TERMINAL_EXECUTION_STATES,
                    AgentExecutionService,
                )

                exe = (
                    await session.execute(
                        sa_select(AgentExecution)
                        .where(AgentExecution.agent_task_id == task_id)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if interrupted:
                    task.status = AgentTaskStatus.AWAITING_APPROVAL
                    if sess is not None:
                        sess.status = AgentSessionStatus.PAUSED_APPROVAL
                    if exe is not None and exe.status not in TERMINAL_EXECUTION_STATES:
                        await AgentExecutionService.transition_execution(
                            session,
                            exe,
                            AgentExecutionStatus.WAITING_FOR_APPROVAL,
                            stage="approval_gate",
                            activity="Waiting for human approval",
                        )
                else:
                    task.status = AgentTaskStatus.COMPLETED
                    task.final_response = str(result.get("final_response") or "")
                    task.token_usage = dict(result.get("token_usage") or {})
                    task.completed_at = utcnow()
                    if exe is not None and exe.status not in TERMINAL_EXECUTION_STATES:
                        await AgentExecutionService.transition_execution(
                            session,
                            exe,
                            AgentExecutionStatus.COMPLETED,
                            stage="completed",
                            activity="Execution completed",
                        )
                    if sess is not None:
                        sess.status = AgentSessionStatus.ACTIVE
                        if result.get("active_specialist"):
                            sess.active_specialist = SpecialistRole(
                                str(result["active_specialist"])
                            )
                        history = list(sess.message_history or [])
                        history.append(
                            {
                                "role": "assistant",
                                "content": task.final_response,
                                "timestamp": utcnow().isoformat(),
                                "task_id": str(task.id),
                            }
                        )
                        sess.message_history = history
                await session.commit()

        return dict(result) if isinstance(result, dict) else {"result": result}
