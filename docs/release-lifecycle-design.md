# ReleaseGuard AI — Autonomous Release Lifecycle Engine Design

## 1. Executive Overview

The **Autonomous Release Lifecycle Engine** transforms ReleaseGuard AI from a collection of independent PR analysis, feature flag, experiment, and approval tools into a unified release control plane. Every software release is tracked as a persistent, auditable, state-driven lifecycle (`Release`) with structured stages (`ReleaseStage`), immutable event history (`ReleaseEvent`), linked artifacts (`ReleaseArtifact`), deterministic verification checks (`ReleaseCheck`), and an explainable readiness gate (`ReleaseReadinessService`).

### Core Objectives
1. **Single Source of Truth:** Unify `Repository`, `PullRequest`, `CodeChange`, `FeatureFlag`, `Experiment`, `Approval`, and `AuditLog` under one cohesive `Release` record.
2. **Formal State Machine:** Enforce all lifecycle transitions through an authoritative transition matrix with optimistic concurrency (`version`), idempotent operations, and strict RBAC/policy gates.
3. **Explainable Readiness:** Compute deterministic readiness scores, blocking issues, warnings, and recommended actions before rollout or production approval.
4. **Orchestrated Execution & Recovery:** Reuse existing specialist agents, isolated patch validation, Flagsmith integration, Bayesian analytics, and `PolicyEngine` governance, with startup recovery for interrupted stages.
5. **Full Control Plane Visibility:** Expose `/releases`, `/releases/[id]`, `/pull-requests` release creation, Dashboard Release Operations KPIs, Markdown release reports, and real-time SSE updates.

---

## 2. Existing Reusable Architecture & Integration Map

Rather than duplicating models or services, the Release Lifecycle Engine composes existing ReleaseGuard AI primitives:

| Existing Module / Model | Role in Release Lifecycle Engine |
|---|---|
| `Repository`, `PullRequest`, `CodeChange` (`app.models.github`) | Source PR context, risk level, patch diff, and validation status linked via `repository_id` & `pull_request_id`. |
| `FeatureFlag`, `RolloutHistory` (`app.models.flags`) | Feature flag protection, target environment, rollout percentage, and rollback execution linked via `feature_flag_id`. |
| `Experiment`, `ExperimentEvent` (`app.models.experiments`) | Bayesian experimentation, guardrail metrics, and release recommendation linked via `experiment_id`. |
| `Approval`, `AuditLog` (`app.models.governance`) | Human-in-the-loop Category B/C approval gates and immutable compliance audit trail. `Approval` gains nullable `release_id` + `release_version` for direct linkage and stale approval invalidation. |
| `Notification`, `ScheduledJob` (`app.models.operations`) | Operator notifications and periodic release monitoring (`release_monitor` job type). |
| `pr_agent` (`analyze_pr_with_static_and_gemini`, `generate_context_aware_flag_patch`) | Automated PR risk analysis and context-aware feature-flag wrapping. |
| `handle_validate_patch_isolated` (`app.agents.tools.pr_tools`) | Isolated syntax/test/diff validation producing structured `ReleaseCheck` & `ReleaseArtifact` records. |
| `PolicyEngine` (`app.services.policy_engine`) | Enforces Category A/B/C governance, blocks self-approval, and prevents AI/job auto-approval. |
| `notification_service` + `SSEBus` | Emits persistent notifications and `release.*` workspace SSE events for live UI refresh. |

---

## 3. Release Lifecycle Domain Model

Five new tables are introduced in `backend/app/models/releases.py` and migrated in `0005_release_lifecycle.py` (revising `0004_notifications`), plus two nullable columns (`release_id`, `release_version`) on `approvals`:

### 3.1 `releases`
- **Identity & Linkage:** `id` (UUID PK), `release_key` (unique human-readable key e.g. `REL-2026-0001`), `title`, `description`, `release_type` (`feature`, `hotfix`, `experiment`, `config`, `rollback_recovery`), `target_environment` (default `development`), `status` (`ReleaseStatus`, 18 states), `previous_status` (for clean resume from `PAUSED`), `version` (integer optimistic concurrency lock, starting at `1`).
- **Foreign Keys:** `repository_id` (`repositories.id`), `pull_request_id` (`pull_requests.id`), `feature_flag_id` (`feature_flags.id`), `experiment_id` (`experiments.id`), `owner_id` (`users.id`), `created_by_id` (`users.id`).
- **Lifecycle Metrics & Context:** `risk_level` (`RiskLevel`), `readiness_score` (0–100 integer), `readiness_status` (`ReleaseReadinessStatus`: `READY`, `NEEDS_ATTENTION`, `BLOCKED`, `NOT_READY`), `readiness_summary` (JSONB breakdown), `target_rollout_percentage` (int 0–100), `current_rollout_percentage` (int 0–100), `ai_summary` (Text), `metadata_json` (JSONB), `error_message` (Text), `scheduled_for`, `started_at`, `completed_at`, `paused_at`, `cancelled_at`, `created_at`, `updated_at`.

### 3.2 `release_stages`
Tracks each ordered stage of a release (`ReleaseStageKey`):
1. `draft`
2. `pr_analysis`
3. `patch_validation`
4. `flag_setup`
5. `experiment_monitoring`
6. `approval_gate`
7. `rollout_execution`
8. `post_release_verification`
9. `completed`

Columns: `id`, `release_id` (FK `releases.id` CASCADE), `stage_key`, `stage_order`, `title`, `status` (`ReleaseStageStatus`: `pending`, `in_progress`, `waiting`, `passed`, `failed`, `skipped`, `blocked`), `attempt_count`, `max_attempts`, `summary`, `details_json`, `error_message`, `started_at`, `completed_at`, `created_at`, `updated_at`. Unique constraint on `(release_id, stage_key)`.

### 3.3 `release_events`
Immutable chronological timeline of every state change, check, orchestration step, approval request, rollout, and operator action:
`id`, `release_id` (FK `releases.id` CASCADE), `event_type`, `from_status`, `to_status`, `stage_key`, `actor_id` (FK `users.id` SET NULL), `actor_type` (`ActorType`), `title`, `message`, `payload_json`, `created_at`.

### 3.4 `release_artifacts`
Links generated or external evidence to a release:
`id`, `release_id` (FK `releases.id` CASCADE), `artifact_type` (`ReleaseArtifactType`: `pr_analysis`, `code_patch`, `validation_report`, `flag_config`, `experiment_report`, `approval_snapshot`, `release_report`, `incident_report`), `title`, `source_table`, `source_id`, `uri`, `summary`, `content_json`, `created_by_id`, `created_at`.

### 3.5 `release_checks`
Deterministic verification gates evaluated by `ReleaseReadinessService`:
`id`, `release_id` (FK `releases.id` CASCADE), `check_key`, `category` (`pr`, `validation`, `flag`, `experiment`, `approval`, `guardrail`, `policy`), `title`, `status` (`ReleaseCheckStatus`: `passed`, `failed`, `warning`, `pending`, `skipped`), `is_blocking` (bool), `weight` (int), `message`, `details_json`, `evaluated_at`, `created_at`, `updated_at`. Unique constraint on `(release_id, check_key)`.

---

## 4. Formal State Machine & Transition Matrix

`ReleaseStatus` defines 18 authoritative states:
`DRAFT`, `INITIALIZING`, `ANALYZING`, `AWAITING_PATCH`, `VALIDATING`, `READY_FOR_FLAG`, `FLAG_PROVISIONING`, `EXPERIMENT_RUNNING`, `AWAITING_APPROVAL`, `APPROVED`, `ROLLING_OUT`, `MONITORING`, `COMPLETED`, `PAUSED`, `BLOCKED`, `FAILED`, `ROLLED_BACK`, `CANCELLED`.

### Transition Rules (`backend/app/services/release_state_machine.py`)
- **Terminal States:** `COMPLETED`, `ROLLED_BACK`, `CANCELLED` (no outbound operational transitions).
- **Pause/Resume:** Any active non-terminal state (except `DRAFT`, `COMPLETED`, `ROLLED_BACK`, `CANCELLED`) can transition to `PAUSED`, recording `previous_status`. Resuming restores `previous_status` (or `INITIALIZING` if unset).
- **Failure & Retry:** Active execution states (`INITIALIZING`, `ANALYZING`, `VALIDATING`, `FLAG_PROVISIONING`, `ROLLING_OUT`, `MONITORING`) can transition to `FAILED` or `BLOCKED`; `retry` transitions `FAILED`/`BLOCKED` back to the failed stage's recovery state.
- **Approval Gate:** Transitioning into `APPROVED` requires a verified `Approval` record (human-decided, non-self-approved) or non-production Category A policy pass; transitioning into `ROLLING_OUT` or `COMPLETED` for `production` strictly enforces `PolicyEngine` & readiness checks (no critical blockers).
- **Optimistic Concurrency:** Mutations accept `expected_version`; mismatches return HTTP `409 Conflict` with the current `version`.

---

## 5. Stage Orchestration & Reliability (`ReleaseOrchestrator`)

`ReleaseOrchestrator` drives releases forward idempotently:
- **PR-Based & Manual Creation:** `create_release()` initializes all 9 `ReleaseStage` rows, creates initial `ReleaseCheck` placeholders, emits a `release.created` event, and prevents duplicate active releases for the same `pull_request_id` unless `allow_duplicate=True`.
- **AI-Assisted Preparation:** Optionally calls `GeminiClient` with a structured schema to draft `ai_summary`, suggested `risk_level`, and recommended `target_rollout_percentage` without overwriting explicit human inputs.
- **Stage Execution:** Each stage invokes existing domain services (`pr_agent`, `handle_validate_patch_isolated`, Flagsmith provisioning, Bayesian experiment evaluation, `PolicyEngine.create_approval`), records `ReleaseArtifact` + `ReleaseCheck` rows, and updates `ReleaseReadinessService`.
- **Startup Recovery:** On FastAPI lifespan startup, `recover_interrupted_releases()` inspects releases stuck in transient states (`INITIALIZING`, `ANALYZING`, `VALIDATING`, `FLAG_PROVISIONING`, `ROLLING_OUT`) with stale `in_progress` stages and transitions them safely to `FAILED` (recoverable via `/retry`) with an explanatory `ReleaseEvent`, preventing silent hangs across restarts.

---

## 6. Release Readiness & Scoring Engine (`ReleaseReadinessService`)

`ReleaseReadinessService.evaluate_release()` aggregates:
1. **PR & Risk Analysis (20 pts):** PR synced, analysis completed, risk level assessed (`CRITICAL` without mitigation = warning/blocker).
2. **Code Patch & Validation (20 pts):** Linked `CodeChange` validated (`ValidationStatus.PASSED`) when patch required.
3. **Feature Flag Readiness (20 pts):** Linked `FeatureFlag` provisioned and healthy in `target_environment`.
4. **Experiment & Guardrails (15 pts):** Linked `Experiment` has no active guardrail breaches (`error_rate` / `latency` regressions trigger critical blocker).
5. **Approval & Policy Gate (25 pts):** Required `Approval` exists for current `release.version` and is `APPROVED` (for production or high-risk releases).

**Hard Blocker Rule:** Any failed check with `is_blocking=True` (e.g., failed validation, guardrail breach, unapproved production gate) forces `readiness_status = BLOCKED` (or `NOT_READY`) and caps `readiness_score <= 49`, regardless of other scores.

---

## 7. Governance, Stale Approval Invalidation & RBAC

- **No Self-Approval / No Auto-Approval:** Preserves `PolicyEngine` rules; scheduled jobs (`release_monitor`) and AI agents may evaluate readiness or request approvals, but never approve or execute production rollouts autonomously.
- **Stale Approval Invalidation:** When material release attributes change (`pull_request_id`, `feature_flag_id`, `experiment_id`, `target_environment`, `target_rollout_percentage`), `release.version` increments and any `PENDING` or `APPROVED` `Approval` bound to an older `release_version` is automatically marked `CANCELLED` with a `ReleaseEvent` (`approval.invalidated_stale`), requiring fresh human sign-off.
- **RBAC:** `viewer` (read-only), `engineer` (create/edit draft, analyze, validate, pause/resume/retry, request approval), `release_manager` / `admin` (approve/reject Category B/C gates, execute rollouts/rollbacks).

---

## 8. REST API & SSE Event Surface

All endpoints live under `/api/v1/releases` (preserving existing `/overview` and `/investigate` routes above `/{release_id}`):
- `GET /api/v1/releases` — Search, filter (`status`, `readiness_status`, `environment`, `repository_id`, `owner_id`, `risk_level`), paginate, sort.
- `POST /api/v1/releases` — Create manual or PR-linked release (with optional `ai_assist=True`).
- `GET /api/v1/releases/{release_id}` — Full release detail with stages, readiness, checks, linked entities, recent events, artifacts.
- `PATCH /api/v1/releases/{release_id}` — Update metadata/targets with optimistic `version` check & stale approval invalidation.
- `POST /api/v1/releases/{release_id}/analyze` — Run PR & risk analysis stage.
- `POST /api/v1/releases/{release_id}/validate` — Run isolated patch validation stage.
- `POST /api/v1/releases/{release_id}/readiness/evaluate` — Recompute `ReleaseReadinessService` checks & score.
- `GET /api/v1/releases/{release_id}/stages` — Ordered stages & attempt diagnostics.
- `GET /api/v1/releases/{release_id}/timeline` — Chronological filterable `ReleaseEvent` feed.
- `GET /api/v1/releases/{release_id}/checks` — Readiness checks & blocker breakdown.
- `GET /api/v1/releases/{release_id}/artifacts` — Linked artifacts.
- `POST /api/v1/releases/{release_id}/pause`, `/resume`, `/retry`, `/cancel` — Lifecycle controls.
- `POST /api/v1/releases/{release_id}/approval-request` — Create `Approval` bound to `release.id` & `release.version`.
- `GET /api/v1/releases/{release_id}/report` & `/report/export` — Structured JSON report & downloadable Markdown release report.

**SSE Events:** `release.created`, `release.updated`, `release.stage_changed`, `release.readiness_updated`, `release.approval_requested`, `release.completed`, `release.rolled_back`.

---

## 9. Frontend Control Plane UX

- **`/releases` (`frontend/app/releases/page.tsx`):** Unified Release Operations workspace featuring searchable/filterable Lifecycle Releases table, readiness & stage badges, manual **Create Release** modal, plus a **Rollout Tracker & Incident Investigation** tab preserving existing Flagsmith rollout/rollback & investigation workflows.
- **`/releases/[id]` (`frontend/app/releases/[id]/page.tsx`):** Full release cockpit with header, status/risk/environment badges, contextual operator actions (`Analyze`, `Validate`, `Evaluate Readiness`, `Request Approval`, `Pause`/`Resume`/`Retry`/`Cancel`, `Export Markdown Report`), backend-driven 9-stage horizontal tracker, Readiness Score & Blocker panel, linked PR/Flag/Experiment/Approval/Artifact tabs, chronological filterable Timeline, and live SSE auto-refresh.
- **`/pull-requests` (`frontend/app/pull-requests/page.tsx`):** Added **Create Release** button on PR detail/list rows linking directly to `/releases/[id]`.
- **`/dashboard` (`frontend/app/dashboard/page.tsx`):** Added Release Lifecycle KPIs (Active Releases, Ready to Ship, Blocked Releases, Completed Releases), Readiness Distribution, Upcoming Releases, and Release Health overview.

---

## 10. Testing, Migration Parity & Verification Plan

1. **Migration Parity:** Verify `0005_release_lifecycle.py` upgrades cleanly from `0004_notifications`, passes `alembic.autogenerate.compare_metadata` with zero diffs on SQLite, downgrades cleanly, and upgrades PostgreSQL 16 in Docker without deleting existing data.
2. **Backend Test Suite (`backend/tests/test_release_lifecycle.py`):** Test all state machine transitions, optimistic concurrency conflicts, stale approval invalidation, `ReleaseReadinessService` blocker capping, `ReleaseOrchestrator` stage execution & startup recovery, RBAC enforcement, Markdown report export, and full NovaShop `checkout_v2` E2E + failure simulation.
3. **Frontend & Docker Verification:** Run Vitest component/page tests, `tsc --noEmit`, `eslint`, `next build`, rebuild Docker Compose, and verify live API + UI flows end-to-end.
