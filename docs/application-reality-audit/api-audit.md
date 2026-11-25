# API & Backend Audit (Stage A)

**Audit Date:** 2026-11-26  
**Router Root:** `backend/app/api/v1/__init__.py` (`25` sub-routers mounted under `/api/v1` + `/health`)

## Router Inventory & Verification Status

1. `auth_router` (`/auth`): Cookie + Bearer HMAC-SHA256 sessions, PBKDF2-SHA256 passwords, GitHub OAuth, `token_version` revocation. **Verified (DB + live auth).**
2. `dashboard_router` & `system_v1_router` (`/dashboard`, `/system/health`): Real DB aggregation + live health probe (`0.94ms` DB, `1093` requests, `0` 5xx). **Verified live.**
3. `flags_router` & `flag_health_router` (`/flags`, `/flag-health`): Syncs Flagsmith v1/v2 flags, creates flags/segments, computes 7-detector Flag Health. **Verified against Flagsmith `#56055`.**
4. `experiments_router` (`/experiments`): Plans NL experiments, activates traffic splits, computes pure-Python Beta-Binomial posteriors + recommendations. **Partial (Stage D gap):** missing native Flagsmith experiment/metric creation, native results retrieval, and winner promotion endpoint.
5. `events_router` (`/events/ingest`): Ingests NovaShop & SDK telemetry (`2,647` rows in PostgreSQL). **Verified.**
6. `approvals_router` (`/approvals`): Single-use atomic SHA-256 `action_hash` approvals with precondition revalidation (`15` rows in DB). **Verified.**
7. `releases_router` (`/releases`): Phase 1 Release Lifecycle state machine, readiness, stages, orchestration. **Implemented; 0 live rows in DB yet.**
8. `agent_router` & `agent_executions_router` (`/agent`, `/agent-executions`): Phase 2 LangGraph supervisor + `SqlAlchemyCheckpointSaver` + Command Center timeline/pause/resume/cancel/retry. **Implemented; 0 live executions yet.**
9. `risk_assessments_router` (`/risk-assessments`, `/risk-engine`, `/repo-risk`): Phase 3 multi-dimensional risk scoring. **Implemented.**
10. `rollouts_router` (`/progressive-delivery`): Phase 4 staged rollouts (`1->5->10->25->50->100%`), guardrails, Flagsmith mutations & approvals. **Implemented.**
11. `intelligence_router` (`/intelligence`): Phase 5 historical patterns, failure rates, policy recommendations. **Implemented.**
12. `dx_router` (`/dx`): Phase 6 GitHub webhooks, check runs, PR comments, readiness reports, share links, Slack/Discord dispatch. **Implemented; Slack/Discord unverified externally.**
13. `org_router` (`/organization`): Phase 7 multi-repo registry, dependency graph, org policies. **Implemented.**
14. `repositories_router`, `pull_requests_router`, `code_changes_router`, `schedules_router`, `notifications_router`, `audit_router`, `metrics_router`, `settings_router`: **Implemented; backed by PostgreSQL.**

## Backend Packaging Defect (P1)
- `backend/requirements.txt` omits `pytest-asyncio`, causing `python -m pytest` inside the `backend` Docker container to fail at `backend/tests/conftest.py:11` (`ModuleNotFoundError: No module named 'pytest_asyncio'`).
