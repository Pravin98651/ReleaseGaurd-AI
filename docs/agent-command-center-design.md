# ReleaseGuard AI — Autonomous Release Command Center Design

## 1. Existing Agent Architecture

ReleaseGuard AI's existing agent system (`backend/app/agents/`) is built around a LangGraph `StateGraph` supervisor (`AgentSupervisor` in `supervisor.py`) coordinating three specialist roles (`SpecialistRole`):
- `pr_agent` — PR diff risk analysis, Flagsmith patch generation, isolated patch validation, and Draft PR creation.
- `flag_agent` — Flagsmith feature-flag provisioning, gradual rollouts, multivariate splits, flag health scans, and rollbacks.
- `experiment_agent` — Bayesian Beta-Binomial experiment evaluation, guardrail checks, and temporal incident investigation.

All specialists invoke tools through `execute_tool_call` (`backend/app/agents/tools/__init__.py`) after passing `PolicyEngine` governance (`policy_gate` node) and workspace-scope validation (`_check_scope_violation`).

---

## 2. Existing Execution Lifecycle

1. User or module creates an `AgentSession` and dispatches a prompt via `POST /api/v1/agent/sessions/{id}/messages`.
2. An `AgentTask` row (`status=queued`) is persisted and launched asynchronously via `AgentSupervisor.run_task()`.
3. `_execute_and_sync_db()` marks the task `running`, invokes the LangGraph graph, and emits step events (`task.started`, `agent.thinking`, `agent.tool_proposed`, `policy.evaluated`, `tool.executed`, `approval.required`, `agent.completed`, `task.failed`).
4. Category B/C mutations hit `create_approval_and_interrupt`, create a hash-bound `Approval`, pause the graph via `langgraph.types.interrupt()`, and set `AgentTask.status = awaiting_approval`.
5. Approving or rejecting via `/api/v1/approvals/{id}/approve` or `/reject` calls `supervisor.resume_task(...)` with `Command(resume=...)`.

---

## 3. Existing Agent and Execution Models

- **`AgentSession` (`agent_sessions`):** Stores `user_id`, `title`, `thread_id`, `active_specialist`, `status`, `context_scope`, `message_history`.
- **`AgentTask` (`agent_tasks`):** Stores `session_id`, `specialist`, `user_prompt`, `status` (`AgentTaskStatus`: 6 states), `steps_log` (JSONB list), `final_response`, `error_message`, `gemini_model`, `token_usage`, `started_at`, `completed_at`.
- **`LangGraphCheckpoint` (`langgraph_checkpoints`):** Stores serialized LangGraph state checkpoints for pause/resume across Postgres and SQLite.

---

## 4. Existing Tool Execution Infrastructure

- `ToolExecutionContext` (`backend/app/agents/tools/context.py`) provides bounded access to SQLAlchemy `AsyncSession`, `GitHubClient`, `FlagsmithClient`, `GeminiClient`, and `PolicyEngine`.
- `SPECIALIST_TOOL_ALLOWLIST` enforces per-specialist tool permissions.
- `validate_tool_args()` validates Pydantic input schemas before policy evaluation or tool execution.

---

## 5. Existing Streaming Capabilities

- `SSEBus` (`backend/app/services/sse_bus.py`) provides:
  - Per-task ordered streaming (`publish(task_id, ...)` + `subscribe(task_id, after_seq, session)` with DB-backed replay from `AgentTask.steps_log`).
  - Workspace-wide domain event streaming (`publish_workspace_event()` + `subscribe_workspace()` via `GET /api/v1/events/stream`).
- All payloads pass through `redact_dict()` before persistence and broadcast.

---

## 6. Existing Approval Integration

- `PolicyEngine` classifies tools into Category A (read/safe), B (staging/non-prod mutation), C (production mutation / destructive), or FORBIDDEN.
- `ApprovalExecutor` (`backend/app/agents/approval_executor.py`) captures preconditions, computes SHA-256 `action_hash`, enforces single-use atomic locks, revalidates live drift before execution, and records `AuditLog` + `RolloutHistory`.

---

## 7. Existing Release Lifecycle Integration

- Phase 1 introduced `releases`, `release_stages`, `release_events`, `release_artifacts`, and `release_checks`, plus `ReleaseOrchestrator` and `ReleaseReadinessService`.
- Currently, `AgentTask` has **no foreign-key association** to `Release`, `Repository`, `PullRequest`, `Experiment`, or `ScheduledJob`, and release-triggered actions (`analyze`, `validate`) do not surface as first-class observable executions in a Command Center.

---

## 8. Missing Observability Capabilities

1. **Unified Execution Record:** No relational `AgentExecution` linking `AgentTask`, `Release`, `Repository`, `PullRequest`, `Experiment`, and `ScheduledJob` with heartbeat timestamps, duration, priority, correlation ID, and optimistic `version`.
2. **Structured Steps & Tool Calls:** `steps_log` is an untyped JSONB array; no queryable `AgentExecutionStep` or `AgentToolCall` records with start/end timestamps, duration, retry count, or error codes.
3. **Explainable Decision Records:** No `AgentDecision` model distinguishing AI recommendations, deterministic policy/readiness rules, verified external facts, and human decisions with evidence references.
4. **Ordered Trace & Checkpoints:** No `AgentExecutionTrace` sequence table or human-inspectable `AgentExecutionSnapshot` checkpoint summary.

---

## 9. Missing Execution-Control Capabilities

1. **Formal 18-State Execution State Machine:** `AgentTaskStatus` lacks `INITIALIZING`, `PLANNING`, `TOOL_RUNNING`, `WAITING_FOR_INPUT`, `PAUSE_REQUESTED`, `PAUSED`, `RESUMING`, `RETRYING`, `CANCELLATION_REQUESTED`, `TIMED_OUT`, `INTERRUPTED`, and `RECOVERING`.
2. **Safe Checkpoint Pause / Resume / Retry / Step-Retry:** Existing `/api/v1/agent/tasks/{id}/cancel` only marks `cancelled` if `queued`/`running`, without checkpoint-aware pause/resume, concurrency version locking, or retry limits.
3. **Startup Recovery for Interrupted Executions:** Backend restarts do not automatically transition active in-flight agent executions to `INTERRUPTED` with recoverable checkpoint metadata.

---

## 10. Implementation Matrix

| Capability | Classification | Plan |
|---|---|---|
| LangGraph Supervisor & Specialists | Already implemented and verified | Reuse `AgentSupervisor` and instrument hooks |
| `SSEBus` & `/api/v1/events/stream` | Already implemented and verified | Reuse for `execution.*` workspace & execution streams |
| `PolicyEngine`, `ApprovalExecutor`, `AuditLog` | Already implemented and verified | Reuse without creating a parallel approval system |
| `NotificationService` & `redact_dict`/`scrub_secrets` | Already implemented and verified | Reuse for execution alerts & pre-persistence redaction |
| `AgentTask` & `AgentSession` | Requires extension | Keep intact; link `AgentExecution.agent_task_id` |
| Release Lifecycle Integration | Requires extension | Link `AgentExecution.release_id`; display on `/releases/[id]` |
| Formal 18-State Execution State Machine | Missing | Add `agent_execution_service.py` + `AgentExecutionStatus` |
| Structured Steps, Tool Calls, Decisions, Traces, Snapshots | Missing | Add 6 ORM tables in `0006_agent_command_center.py` |
| Pause / Resume / Cancel / Retry Controls & Locking | Missing | Implement optimistic `version` + safe checkpoint controls |
| Command Center UI (`/command-center`, `/command-center/[executionId]`) | Missing | Build operational overview, detail cockpit, and history |

---

## 11. Proposed Architecture & Database Changes (`0006_agent_command_center`)

Add 6 relational tables in `backend/app/models/command_center.py` (with `0006_agent_command_center.py` revising `0005_release_lifecycle`):
1. `agent_executions`: Core execution record (`execution_key`, `agent_type`, `agent_name`, `task_name`, `task_description`, `status` [`AgentExecutionStatus`, 18 states], `priority`, `execution_mode`, `initiated_by_id`, `actor_type`, `agent_task_id`, `repository_id`, `pull_request_id`, `release_id`, `experiment_id`, `schedule_id`, `parent_execution_id`, `current_stage`, `current_activity`, `completed_steps`, `total_steps`, `started_at`, `completed_at`, `last_heartbeat_at`, `timeout_at`, `duration_ms`, `error_code`, `error_message`, `retry_count`, `max_retries`, `pause_requested`, `cancel_requested`, `correlation_id`, `idempotency_key`, `execution_metadata`, `version`).
2. `agent_execution_steps`: Ordered stages (`execution_id`, `step_key`, `step_name`, `step_type`, `sequence`, `status`, `description`, `input_summary`, `output_summary`, `started_at`, `completed_at`, `duration_ms`, `retry_count`, `error_details`).
3. `agent_tool_calls`: Instrumented tool invocations (`execution_id`, `step_id`, `tool_name`, `tool_category`, `invocation_id`, `status`, `input_summary`, `output_summary`, `started_at`, `completed_at`, `duration_ms`, `retry_count`, `error_code`, `error_message`, `idempotency_key`).
4. `agent_decisions`: Explainable recommendations & facts (`execution_id`, `step_id`, `decision_type`, `decision_source` [`ai_recommendation` | `deterministic_rule` | `verified_fact` | `human_decision`], `decision_summary`, `recommendation`, `confidence`, `decision_status`, `supporting_evidence`, `evidence_references`, `model_name`).
5. `agent_execution_traces`: Canonical ordered timeline (`execution_id`, `sequence_number`, `event_type`, `category`, `stage`, `actor_type`, `actor_id`, `message`, `payload`, `correlation_id`).
6. `agent_execution_snapshots`: Safe recoverable checkpoints (`execution_id`, `checkpoint_number`, `checkpoint_type`, `resumable_state`, `current_step_id`, `completed_step_ids`, `pending_operation`, `context_reference`).

---

## 12. API Changes (`/api/v1/agent-executions`)

Register `agent_executions_router` (`prefix="/agent-executions"`) with static routes (`/overview`, `/metrics`, `/stream`) registered **above** `/{execution_id}`:
- `POST /api/v1/agent-executions` — Create & optionally start an execution.
- `GET /api/v1/agent-executions` — Search, filter (`agent_type`, `status`, `repository_id`, `release_id`, `initiated_by_id`, `failed_only`, `scheduled_only`, date range), sort, paginate.
- `GET /api/v1/agent-executions/overview` & `/metrics` — Aggregated live KPIs, tool failure rate, average duration, active & pending approval counts.
- `GET /api/v1/agent-executions/{execution_id}` & `/summary`, `/steps`, `/timeline`, `/tools`, `/decisions`, `/checkpoints`.
- `POST /api/v1/agent-executions/{execution_id}/pause`, `/resume`, `/cancel`, `/retry`, and `/steps/{step_id}/retry` (with `expected_version` optimistic locking).
- `GET /api/v1/agent-executions/stream` & `/{execution_id}/stream` — Authenticated SSE streams reusing `SSEBus`.

---

## 13. Frontend Architecture

- **`/command-center` (`frontend/app/command-center/page.tsx`):** KPI overview cards, Active Executions grid with live status/stage/tool/heartbeat & quick controls, Pending Human Decisions panel (reusing `ApprovalActionCard`), and searchable/filterable Execution History table.
- **`/command-center/[executionId]` (`frontend/app/command-center/[executionId]/page.tsx`):** Execution header, Current Stage & Heartbeat panel, Contextual Controls (`Pause`, `Resume`, `Cancel`, `Retry`), Live Filterable Timeline, Tool Call Inspector (expandable redacted I/O + retryable stage action), Decision & Evidence Inspector (clearly labeling AI vs deterministic vs verified vs human), Human Decisions tab, and Metadata/Checkpoints tab.
- **Navigation & Release Detail Integration:** Add `Command Center` (`/command-center`) to `AppShell.tsx` and embed linked Agent Executions on `/releases/[id]`.

---

## 14. Security Model

- **RBAC:** `viewer` (read-only), `engineer` (view, start permitted tasks, pause, resume, retry), `release_manager` / `admin` (all above + cancel + Category B/C approval decisions via `PolicyEngine` / `ApprovalExecutor`).
- **Pre-Persistence Redaction:** Every tool input/output summary, trace payload, and error message passes through `redact_dict()` and `scrub_secrets()` before DB write and SSE broadcast.
- **No Self-Approval / No Bypass:** Agents cannot approve their own actions or mutate production without `ApprovalExecutor`.

---

## 15. Testing Strategy

Dedicated test suite (`backend/tests/test_agent_command_center.py` + `frontend/__tests__/command-center.test.tsx`):
- Full 18-state machine transition matrix & terminal immutability.
- Optimistic concurrency (`409 Conflict` on competing pause/cancel/retry).
- Safe checkpoint pause/resume, non-retryable auth failure guard, and startup `INTERRUPTED` -> `RECOVERING` recovery.
- 20 failure-simulation & security scenarios (Gemini 503, GitHub 403, Flagsmith 503, timeouts, malformed outputs, secret redaction, RBAC denial, duplicate tool/control idempotency).
- Alembic `0006_agent_command_center` SQLite + Postgres parity.
- Frontend Vitest rendering, controls, timeline filtering, and Next.js production build.
