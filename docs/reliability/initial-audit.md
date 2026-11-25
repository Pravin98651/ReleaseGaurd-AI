# ReleaseGuard AI — Initial Reliability & Scalability Audit (Stage A)

**Audit Date:** 2026-11-26  
**Scope:** Full-stack audit across FastAPI backend, SQLAlchemy/PostgreSQL layer, Gemini/LangGraph agents, Flagsmith/GitHub/Webhook integrations, SSE bus, Scheduler, and Next.js frontend.

## 1. Architecture & Critical Request Flows

1. **Control Plane HTTP Flow:** `Next.js UI -> frontend/lib/api.ts -> FastAPI middleware (request_id + api_metrics) -> Auth/RBAC (deps.py) -> Service Layer -> PostgreSQL (SQLAlchemy 2.0 asyncpg) / External API (Flagsmith/GitHub/Gemini) -> Response`
2. **Human-Gated Mutation Flow:** `UI / Agent -> PolicyEngine.classify_action -> Approval (PENDING, action_hash, expected_precondition) -> Operator Approval -> PolicyEngine.execute_approved_action (atomic pending->executing SQL update) -> External Mutation -> Post-Mutation Readback Verification -> AuditLog`
3. **Real-Time SSE & Scheduler Flow:** `SchedulerService (10s DB poll, row lease lock) -> ToolExecutionContext -> SSEBus.publish -> DB steps_log + subscriber queues -> Browser EventSource`

## 2. Prioritized Findings

| ID | Severity | Component / File | Finding | Root Cause / Impact | Recommended Fix |
|---|---|---|---|---|---|
| **R1** | **Critical** | `backend/app/services/policy_engine.py` (`classify_action`) | `promote_experiment_winner` / `experiment_promote_winner` omitted from policy action sets | `classify_action` fails closed (`PolicyCategory.DENY`) when evaluated by policy gate | Register in `ENV_SCOPED_MUTATIONS` (or `ALWAYS_CATEGORY_C_ACTIONS`) and enforce approval |
| **R2** | **High** | `backend/app/agents/tools/experiment_tools.py:258` | Silent `except ...: pass` when Flagsmith native experiment creation fails | Hides Flagsmith API errors during activation | Log structured warning + persist diagnostic note on experiment |
| **R3** | **High** | `backend/app/services/dx/reports_notifications.py:278` | Slack/Discord webhook URL only validates `https://` | SSRF risk against localhost, link-local metadata (`169.254.169.254`), or private RFC-1918 CIDRs | Validate hostname & resolved IPs are public (reject loopback/private/link-local) + enforce provider host check where applicable |
| **R4** | **High** | `backend/app/services/dx/github_dx.py:395` | Race condition on concurrent identical `delivery_id` webhooks | Check-then-insert raises unhandled `IntegrityError` under concurrent requests | Catch `IntegrityError` cleanly and return existing deduplicated `GitHubWebhookEvent` |
| **R5** | **High** | `backend/app/services/analytics_queries.py:211` & `backend/app/api/v1/agent_executions.py:175` | Unbounded full-table `.scalars().all()` scans (`ExperimentEvent`, `AgentExecution`, `AgentToolCall`, etc.) | High latency & OOM risk as rows scale beyond thousands | Push SQL filters + bounded limits (`ExperimentEvent` window/cap) to PostgreSQL; replace `agent_executions` overview scan with SQL `COUNT`/`AVG` aggregates |
| **R6** | **High** | `backend/app/services/sse_bus.py` | Unbounded `_locks` / `_seq_counters` growth + unlocked `_ws_history` mutation | Memory leak over long uptime & race under concurrent workspace SSE broadcasts | Prune idle task locks/counters on unsubscribe; guard `_ws_state` with `asyncio.Lock` + `redact_dict` |
| **R7** | **High** | `backend/app/api/v1/errors.py` | Missing `retryable` & `timestamp` fields, missing `SQLAlchemyError`/`TimeoutError` handlers, silent 500s | Inconsistent client retryability detection & hard-to-diagnose server faults | Add `retryable` + `timestamp`, add DB/timeout handlers, log server errors with `request_id` |
| **R8** | **Medium** | `backend/app/core/database.py` | Default PostgreSQL pool settings (`pool_size`/`max_overflow`/`pool_recycle`/`pool_timeout` unset) | Risk of connection starvation or stale connections under burst concurrency | Configure explicit async pool bounds + settings |
| **R9** | **Medium** | `frontend/lib/api.ts` & `frontend/app/` | No `AbortController` fetch timeout; missing `error.tsx` / `global-error.tsx` | Hung requests block UI indefinitely; uncaught React render errors show raw Next crash | Add bounded `AbortController` timeout + `retryable` metadata + Next App Router error boundaries |
| **R10** | **Medium** | `backend/app/api/v1/system.py` | Missing separate `/health/live` & `/health/ready` endpoints | Probes cannot distinguish process liveness from DB readiness | Add `/health/live` (fast O(1)) and `/health/ready` (DB `SELECT 1`, returns `503` on failure) |
