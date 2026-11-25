# ReleaseGuard AI — API, Streaming, Gemini Tool & Environment Contracts

> **Document Scope:** Phase 1 authoritative REST API contracts (all 11 frontend pages + demo-app ingestion), SSE/polling agent streaming schema, all 34 Gemini manual tool schemas, and exact environment variables for `.env.example` and `docker-compose.yml`.
> **Related Specs:** [System Architecture](file:///c:/Projects/ReleaseGuard%20AI/docs/architecture.md) | [Database Schema & Policy Engine](file:///c:/Projects/ReleaseGuard%20AI/docs/database-and-policies.md)

---

## 1. API Conventions & Frontend Route Mapping

### 1.1 Standard Conventions
- **Backend Base URL**: `http://localhost:8000` (API prefix `/api/v1`)
- **Content-Type**: `application/json` (except SSE `text/event-stream` and CSV export `text/csv`)
- **Pagination Envelope**:
  ```json
  { "items": [], "total": 42, "limit": 20, "offset": 0 }
  ```
- **Standard Error Envelope**:
  ```json
  {
    "error": {
      "code": "APPROVAL_REVALIDATION_FAILED",
      "message": "Live Flagsmith state drifted since approval was requested.",
      "details": { "expected_percentage": 25, "actual_percentage": 50 },
      "request_id": "req_9f2a..."
    }
  }
  ```
- **Mutation Policy Response Pattern**: Endpoints that may trigger Category B/C policies return either:
  - `200 OK` / `201 Created` (`{"execution_status": "executed", "data": ...}`) for Category A or auto-approved Category B, OR
  - `202 Accepted` (`{"execution_status": "approval_required", "approval": { ... }}`) when a single-use human approval is queued.

### 1.2 All 11 Frontend Routes -> Backend Endpoints Matrix

| Frontend Route | Primary Page Purpose | Primary Backend Endpoints |
| :--- | :--- | :--- |
| `/dashboard` | Executive overview, pending approvals, health & experiment KPIs | `GET /api/v1/dashboard/summary`, `GET /api/v1/approvals?status=pending`, `GET /api/v1/rollout-history?limit=10` |
| `/agent` | LangGraph AI Orchestrator Workspace with live tool/policy stream | `GET/POST /api/v1/agent/sessions`, `POST /api/v1/agent/sessions/{id}/messages`, `GET /api/v1/agent/tasks/{task_id}/stream`, `GET /api/v1/agent/tasks/{task_id}` |
| `/repositories` | Connect & sync GitHub repos, browse branches & code flag scan | `GET/POST /api/v1/repositories`, `POST /api/v1/repositories/sync`, `GET /api/v1/repositories/{id}/branches`, `POST /api/v1/repositories/{id}/scan-flags` |
| `/pull-requests` | PR browser, Risk Assessment, Flag Opportunities, Patch Validator, Draft PRs | `GET /api/v1/pull-requests`, `POST /api/v1/repositories/{id}/pull-requests/sync`, `GET /api/v1/pull-requests/{id}`, `POST /api/v1/pull-requests/{id}/analyze`, `POST /api/v1/pull-requests/{id}/generate-patch`, `POST /api/v1/code-changes/{id}/validate`, `POST /api/v1/code-changes/{id}/create-draft-pr` |
| `/feature-flags` | Flagsmith multi-env matrix, CRUD, NL commands, gradual rollouts | `GET/POST /api/v1/feature-flags`, `POST /api/v1/feature-flags/sync`, `POST /api/v1/feature-flags/nl-command`, `POST /api/v1/feature-flags/{id}/toggle`, `POST /api/v1/feature-flags/{id}/rollout` |
| `/experiments` | Capability probe, NL planning, Bayesian posteriors, guardrail charts | `GET /api/v1/experiments`, `POST /api/v1/experiments/plan`, `POST /api/v1/experiments/{id}/activate`, `POST /api/v1/experiments/{id}/stop`, `GET /api/v1/experiments/{id}/analysis`, `GET /api/v1/experiments/{id}/recommendation` |
| `/releases` | Active gradual rollouts, stage stepping, recommendations, Incident Investigation | `GET /api/v1/releases/overview`, `POST /api/v1/feature-flags/{id}/rollout`, `POST /api/v1/feature-flags/{id}/rollback`, `POST /api/v1/releases/investigate` |
| `/approvals` | Category B/C queue, `action_hash`, diff/precondition preview, Approve/Reject | `GET /api/v1/approvals`, `GET /api/v1/approvals/{id}`, `POST /api/v1/approvals/{id}/approve`, `POST /api/v1/approvals/{id}/reject` |
| `/flag-health` | 7-detector scan, explainable health score breakdown, cleanup Draft PRs | `GET /api/v1/flag-health`, `POST /api/v1/flag-health/scan`, `POST /api/v1/flag-health/{flag_id}/cleanup-pr`, `POST /api/v1/flag-health/{flag_id}/request-delete` |
| `/audit-logs` | Immutable filterable compliance trail & rollout history export | `GET /api/v1/audit-logs`, `GET /api/v1/audit-logs/export`, `GET /api/v1/rollout-history` |
| `/settings` | GitHub OAuth/PAT, Flagsmith tokens & probe, Gemini model, Policy & thresholds | `GET/PATCH /api/v1/settings`, `GET /api/v1/integrations`, `POST /api/v1/integrations/github/pat`, `GET /api/v1/integrations/github/oauth/start`, `POST /api/v1/integrations/github/oauth/callback`, `POST /api/v1/integrations/flagsmith`, `POST /api/v1/integrations/flagsmith/probe`, `DELETE /api/v1/integrations/{provider}` |

---

## 2. Detailed REST Endpoint Contracts

### 2.1 System & Dashboard
- **`GET /health`** -> `200 OK`:
  ```json
  {
    "status": "ok",
    "database": "postgresql|sqlite",
    "gemini_configured": true,
    "github_connected": true,
    "flagsmith_connected": true,
    "analytics_mode": "tracked_event_fallback"
  }
  ```
- **`GET /api/v1/dashboard/summary`** -> `200 OK`:
  Returns `{counts: {repositories, open_prs, high_risk_prs, active_flags, active_experiments, pending_approvals, unhealthy_flags}, integration_status: {...}, recent_rollouts: [...], recent_audits: [...]}`.

### 2.2 Integrations & Settings (`/settings`)
- **`GET /api/v1/settings`** -> `200 OK`: Returns `workspace_settings` fields (`category_b_requires_confirmation`, `approval_ttl_seconds`, `gemini_model`, `flagsmith_analytics_mode`, `default_guardrails`, `health_thresholds`) + masked `integrations` array (only `token_hint`, `scopes`, `status`, `external_account_name`).
- **`PATCH /api/v1/settings`** -> Body: partial update of `category_b_requires_confirmation`, `approval_ttl_seconds`, `gemini_model`, `default_guardrails`, `health_thresholds`. Validates ranges, logs `settings.updated` in `audit_logs`.
- **`GET /api/v1/integrations`** -> `200 OK`: Lists configured `integration_connections` (`id`, `provider`, `auth_mode`, `status`, `external_account_id`, `external_account_name`, `scopes`, `token_hint`, `metadata_json`, `last_verified_at`, `expires_at`); never returns plaintext or encrypted token material.
- **`GET /api/v1/integrations/github/oauth/start`** -> Returns `{authorize_url, state}` (least-privilege scope `read:user user:email public_repo`, or `?include_private=true` -> `repo`).
- **`POST /api/v1/integrations/github/oauth/callback`** -> Body: `{code, state}` -> exchanges code, validates via `GET /user`, encrypts token with Fernet, upserts `integration_connections`.
- **`POST /api/v1/integrations/github/pat`** -> Body: `{token, account_label?}` -> verifies via `GET /user`, records `X-OAuth-Scopes`, encrypts with Fernet, returns masked summary.
- **`POST /api/v1/integrations/flagsmith`** -> Body: `{org_token, project_id, environment_keys: {development?, staging?, production?}}` -> verifies via `GET /api/v1/projects/{project_id}/environments/`, detects `use_v2_feature_versioning`, encrypts `org_token` with Fernet, runs capability probe.
- **`POST /api/v1/integrations/flagsmith/probe`** -> Body: `{environment_key?}` -> probes Flagsmith `/api/v1/environments/{env_key}/experiments/`, updates `workspace_settings.flagsmith_analytics_mode` (`flagsmith_native` or `tracked_event_fallback`).
- **`DELETE /api/v1/integrations/{provider}`** -> Disconnects provider and revokes stored encrypted token.

### 2.3 Repositories & Pull Requests (`/repositories`, `/pull-requests`)
- **`GET /api/v1/repositories`** -> Paginated list of connected `repositories`.
- **`POST /api/v1/repositories`** -> Body: `{owner, name, flagsmith_project_id?}` -> verifies via GitHub API, persists repository.
- **`POST /api/v1/repositories/sync`** -> Syncs accessible GitHub repos for connected account.
- **`GET /api/v1/repositories/{repo_id}/branches`** -> Live branch list from GitHub (`name`, `commit_sha`, `protected`).
- **`POST /api/v1/repositories/{repo_id}/scan-flags`** -> Scans repository tree for Flagsmith SDK references (`is_feature_enabled`, `get_feature_value`, `hasFeature`, `getValue`) and updates `feature_flags.code_references`.
- **`GET /api/v1/pull-requests`** -> Filters: `repository_id`, `state`, `risk_level`, `limit`, `offset`.
- **`POST /api/v1/repositories/{repo_id}/pull-requests/sync`** -> Fetches PRs from GitHub into `pull_requests`.
- **`GET /api/v1/pull-requests/{pr_id}`** -> PR metadata, diff summary, `confirmed_risks`, `potential_risks`, `flag_opportunities`, linked `code_changes`.
- **`POST /api/v1/pull-requests/{pr_id}/analyze`** -> Category A. Runs `pr_agent` analysis (prompt-injection-hardened diff + static signals -> Risk Assessment + Flag Opportunity Detection).
- **`POST /api/v1/pull-requests/{pr_id}/generate-patch`** -> Category A. Body: `{flag_key, flag_type?: "STANDARD"|"MULTIVARIATE", target_files?: string[]}` -> generates `CodeChange` with `before_code`, `after_code`, `unified_diff`, and `generated_tests`.
- **`POST /api/v1/code-changes/{change_id}/validate`** -> Category A. Executes Isolated Temp-Dir Patch Validator (`ast`/`syntax`/`path`/`overwrite`/`ruff`/`pytest`/`eslint` in scrubbed env) and updates `validation_status` + `validation_report`.
- **`POST /api/v1/code-changes/{change_id}/create-draft-pr`** -> Category B. Requires `validation_status == "passed"`. Creates branch + commit + `draft: true` PR in GitHub (or queues Category B approval if `category_b_requires_confirmation == true`).

### 2.4 Agent Workspace & Streaming (`/agent`)
- **`GET /api/v1/agent/sessions`** -> List sessions ordered by `updated_at DESC`.
- **`POST /api/v1/agent/sessions`** -> Body: `{title?, context_scope?: {repository_id?, pull_request_id?, flagsmith_project_id?, environment?}}`.
- **`GET /api/v1/agent/sessions/{session_id}`** -> Full session details, `message_history`, and recent `tasks`.
- **`POST /api/v1/agent/sessions/{session_id}/messages`** -> Body: `{content: string, specialist_hint?: "supervisor"|"pr_agent"|"flag_agent"|"experiment_agent"}`. Creates `agent_tasks` row (`status='queued'`), launches async LangGraph execution background task, returns `202 Accepted` with `{task_id, session_id, stream_url: "/api/v1/agent/tasks/{task_id}/stream"}`.
- **`GET /api/v1/agent/tasks/{task_id}`** -> Polling endpoint. Optional query `?after_seq=N` returns only `steps_log` entries with `seq > N` plus current `status`, `final_response`, and linked `pending_approvals`.
- **`POST /api/v1/agent/tasks/{task_id}/cancel`** -> Cancels a running/paused task and voids its pending approvals.
- **`GET /api/v1/agent/tasks/{task_id}/stream`** -> Server-Sent Events (`text/event-stream`), optional query `?after_seq=0`. Emits:
  - `event: task.started` -> `{seq, task_id, specialist}`
  - `event: agent.thinking` -> `{seq, specialist, message}`
  - `event: agent.tool_proposed` -> `{seq, tool_name, args_redacted, policy_category}`
  - `event: policy.evaluated` -> `{seq, tool_name, policy_category, action_hash, decision: "allow"|"require_approval"|"deny"}`
  - `event: approval.required` -> `{seq, approval_id, policy_category, title, environment_name, action_hash, expires_at}`
  - `event: tool.executed` -> `{seq, tool_name, status: "ok"|"error", summary, verified}`
  - `event: agent.completed` -> `{seq, final_response, token_usage}`
  - `event: task.failed` -> `{seq, error}`
  - `event: heartbeat` -> `{timestamp}` (every 15s while open)

### 2.5 Feature Flags & Gradual Rollouts (`/feature-flags`, `/releases`)
- **`GET /api/v1/feature-flags`** -> Filters: `project_id`, `environment`, `search`, `health_issue`.
- **`POST /api/v1/feature-flags/sync`** -> Pulls projects, environments (including `use_v2_feature_versioning`), features, multivariate options, and feature states from Flagsmith Management API.
- **`POST /api/v1/feature-flags`** -> Category B. Body: `{project_id?, key, description?, flag_type: "STANDARD"|"MULTIVARIATE", initial_value?, mv_options?: [{value, default_percentage_allocation}]}`. Creates flag disabled in prod.
- **`POST /api/v1/feature-flags/nl-command`** -> Dispatches NL flag request (e.g., `"Roll out checkout_v2 to 25% in staging"`) through `flag_agent` LangGraph pipeline and returns `{task_id, stream_url}` (or synchronous result + `approval` if queued).
- **`POST /api/v1/feature-flags/{flag_id}/toggle`** -> Body: `{environment_name, enabled, reason?}`. Category B in non-prod; Category C in prod.
- **`POST /api/v1/feature-flags/{flag_id}/rollout`** -> Body: `{environment_name, target_percentage: 0|10|25|50|75|100, strategy?: "percentage_split_segment"|"multivariate", variation_key?, reason?}`. Category B in non-prod; Category C in prod.
- **`POST /api/v1/feature-flags/{flag_id}/rollback`** -> Body: `{environment_name, reason}`. Sets rollout to `0%` / `enabled=false` (or reverts to prior verified Flagsmith version). Category B in non-prod; Category C in prod.
- **`GET /api/v1/releases/overview`** -> Active rollouts, current stage (`10->25->50->75->100%`), latest recommendations, and recent incident flags.
- **`POST /api/v1/releases/investigate`** -> Category A. Body: `{flag_key?, environment_name, window_minutes?: 60}` -> correlates `payment_failed`/`checkout_abandoned` spikes with `rollout_history` and PR timestamps, explicitly returning `correlation_not_causation: true` + confounding checks.

### 2.6 Flag Health Monitor (`/flag-health`)
- **`GET /api/v1/flag-health`** -> Lists flags sorted by `health_score ASC` with `health_issues` breakdown and filter `?issue_type=stale|unused|always_on|always_off|orphaned|duplicate|env_drift`.
- **`POST /api/v1/flag-health/scan`** -> Category A. Runs all 7 deterministic detectors across synced flags + connected repos, recomputes explainable `health_score`, returns summary metrics.
- **`POST /api/v1/flag-health/{flag_id}/cleanup-pr`** -> Category A (patch + validation) -> optional Category B (Draft PR creation). Body: `{repository_id, keep_branch?: "enabled"|"disabled", open_draft_pr?: false}` -> generates cleanup patch + tests, validates in isolated temp dir, optionally opens Draft PR.
- **`POST /api/v1/flag-health/{flag_id}/request-delete`** -> **Always Category C**. Queues a single-use human approval to archive/delete flag in Flagsmith (never auto-deletes).

### 2.7 Experiments & Bayesian Analytics (`/experiments`)
- **`GET /api/v1/experiments`** -> Lists experiments with `analytics_mode`, current status, sample counts, and latest recommendation.
- **`POST /api/v1/experiments/plan`** -> Category A. Body: `{prompt: string, feature_flag_id?: uuid, environment_name?: string}` -> uses `experiment_agent` to convert NL specification into structured `experiments` draft row.
- **`POST /api/v1/experiments/{exp_id}/activate`** -> Body: `{environment_name}` -> sets multivariate split / segment and marks experiment `running`. Category B in non-prod; Category C in prod.
- **`POST /api/v1/experiments/{exp_id}/stop`** -> Body: `{reason?}` -> stops traffic split / concludes experiment. Category B in non-prod; Category C in prod.
- **`GET /api/v1/experiments/{exp_id}/analysis`** -> Category A. Runs pure-Python Bayesian Beta-Binomial engine over `tracked_event_fallback` (or `flagsmith_native` if enabled), returning:
  ```json
  {
    "experiment_id": "...",
    "analytics_mode": "tracked_event_fallback",
    "sample_adequacy": { "control_exposures": 240, "treatment_exposures": 245, "min_required": 100, "is_adequate": true },
    "control": { "exposures": 240, "conversions": 29, "posterior_alpha": 30.0, "posterior_beta": 212.0, "mean_rate": 0.1239, "credible_interval_95": [0.0858, 0.1682] },
    "treatment": { "exposures": 245, "conversions": 44, "posterior_alpha": 45.0, "posterior_beta": 202.0, "mean_rate": 0.1822, "credible_interval_95": [0.1361, 0.2330] },
    "comparison": {
      "prob_treatment_beats_control": 0.9642,
      "absolute_lift_mean": 0.0583,
      "absolute_lift_cri_95": [-0.0052, 0.1211],
      "relative_lift_mean": 0.4705,
      "relative_lift_cri_95": [-0.038, 1.182],
      "expected_loss_choose_treatment": 0.0004,
      "expected_loss_choose_control": 0.0587
    },
    "guardrails": {
      "payment_failed": {
        "control_errors": 3, "control_rate": 0.0125,
        "treatment_errors": 4, "treatment_rate": 0.0163,
        "prob_treatment_exceeds_soft_delta": 0.142,
        "prob_treatment_exceeds_hard_delta": 0.031,
        "breached": false
      },
      "checkout_abandoned": { "control_count": 48, "treatment_count": 39 }
    }
  }
  ```
- **`GET /api/v1/experiments/{exp_id}/recommendation`** -> Category A. Returns deterministic Recommendation (`Continue Monitoring` | `Expand Rollout` | `Maintain Current Rollout` | `Reduce Rollout` | `Recommend Rollback`), numeric triggers, and Gemini plain-English narrative.

### 2.8 Demo-App Event Ingestion & Demo Service Contracts

#### ReleaseGuard Event Ingestion (`/api/v1/events/*`)
- **`POST /api/v1/events/ingest`** -> Auth header `X-Ingest-Key: <EVENT_INGEST_KEY>`. Accepts `{ "events": [ ... ] }` (1..500 items):
  ```json
  {
    "events": [
      {
        "event_id": "evt_01J...",
        "flag_key": "checkout_v2",
        "environment_name": "development",
        "variation_key": "treatment",
        "event_type": "feature_variation_assigned",
        "user_identifier": "shopper_42",
        "session_key": "chk_991",
        "numeric_value": 89.50,
        "properties": { "step": "checkout_init" },
        "occurred_at": "2026-11-25T14:30:00Z"
      }
    ]
  }
  ```
  - Allowed `event_type` values: `feature_variation_assigned`, `checkout_started`, `checkout_completed`, `checkout_abandoned`, `payment_failed`.
  - Deduplicates on `event_id` (`ON CONFLICT DO NOTHING` / idempotent skip) and resolves active `experiment_id` matching `(flag_key, environment_name)`. Returns `{accepted, deduplicated}`.
- **`GET /api/v1/events/summary`** -> Query: `flag_key`, `environment_name`, `experiment_id`, `window_minutes`. Returns grouped event counts and time-series buckets for Recharts visualizations.

#### Demo App Backend (`http://localhost:8001/api/*`)
- **`GET /api/health`** -> `{status: "ok", flagsmith_connected: bool}`
- **`GET /api/flags?user_id=...`** -> Evaluates Flagsmith SDK / Edge Flags API (`checkout_v2`) for `user_id` and emits `feature_variation_assigned` event to ReleaseGuard backend.
- **`POST /api/checkout`** -> Body: `{user_id, cart_total, payment_method, promo_code?, simulate_payment_bug?: bool}` -> evaluates live flag variation (`v1` vs `v2`), emits `checkout_started` -> executes v1 or v2 checkout logic -> emits `checkout_completed`, `checkout_abandoned`, or `payment_failed` to ReleaseGuard backend.
- **`POST /api/simulate`** -> Body: `{users_count: 100, scenario: "healthy_v2"|"buggy_v2"|"neutral"}` -> executes realistic deterministic cohorts through Flagsmith evaluation + checkout pipeline so `/experiments` and `/releases` immediately show real events and Bayesian posteriors.

### 2.9 Approvals & Audit Logs (`/approvals`, `/audit-logs`)
- **`GET /api/v1/approvals`** -> Filters: `status`, `policy_category`, `environment_name`.
- **`GET /api/v1/approvals/{approval_id}`** -> Full approval details, `action_payload`, `expected_precondition`, `action_hash`, TTL countdown.
- **`POST /api/v1/approvals/{approval_id}/approve`** -> Body: `{comment?: string}`. Executes single-use atomic lock + hash check + live-state revalidation + external mutation + post-mutation verification + LangGraph `Command(resume=...)`.
- **`POST /api/v1/approvals/{approval_id}/reject`** -> Body: `{comment: string}`. Marks `rejected`, writes `audit_logs`, resumes LangGraph with rejection message.
- **`GET /api/v1/audit-logs`** -> Filters: `policy_category`, `event_action`, `resource_type`, `status`, `from`, `to`.
- **`GET /api/v1/audit-logs/export`** -> Query: `format=json|csv` plus same filters.
- **`GET /api/v1/rollout-history`** -> Filters: `feature_flag_id`, `environment_name`, `limit`, `offset`.

---

## 3. Gemini Tool Catalog (34 Manual Tools)

All 34 tools run with `google-genai` `automatic_function_calling=AutomaticFunctionCallingConfig(disable=True)` and pass through `policy_gate` before execution.

### 3.1 `pr_agent` Tools (11 Tools)

| # | Tool Name | Policy Cat | Input Schema (Key Fields) | Output Summary & Invariants |
| :- | :--- | :-: | :--- | :--- |
| 1 | `github_list_repositories` | `A` | `{visibility?: "all"\|"public"\|"private", limit?: int}` | Returns `{repositories: [{id, full_name, default_branch, is_private}]}` |
| 2 | `github_list_branches` | `A` | `{owner: str, repo: str}` | Returns `{branches: [{name, sha, protected}]}` |
| 3 | `github_list_pull_requests` | `A` | `{owner: str, repo: str, state?: "open"\|"closed"\|"all"}` | Returns `{pull_requests: [{number, title, author, head_branch, base_branch, is_draft}]}` |
| 4 | `github_get_pull_request_diff` | `A` | `{owner: str, repo: str, pr_number: int, max_files?: int}` | Fetches PR + diffs, **wraps all untrusted text in `<untrusted_external_data>`** |
| 5 | `github_get_file_contents` | `A` | `{owner: str, repo: str, path: str, ref?: str}` | Fetches file bytes (max 16KB), **wrapped in `<untrusted_external_data>`** |
| 6 | `github_search_repo_code` | `A` | `{repository_id: UUID, query_pattern: str}` | Searches repo tree/files for Flagsmith SDK or target symbol references |
| 7 | `analyze_pr_risk_and_opportunities` | `A` | `{pull_request_id: UUID, risk_score: int, risk_level: str, summary: str, confirmed_risks: list, potential_risks: list, flag_opportunities: list}` | Persists structured risk breakdown & opportunities onto `pull_requests`; verifies cited file paths exist in PR diff |
| 8 | `generate_flag_wrapping_patch` | `A` | `{repository_id: UUID, pull_request_id?: UUID, flag_key: str, title: str, files_diff: list, generated_tests: list}` | Persists `CodeChange(change_type='flag_wrap', status='draft')` in DB |
| 9 | `validate_patch_isolated` | `A` | `{code_change_id: UUID}` | Runs isolated temp-dir check (path safety, overwrite check, AST/syntax, scrubbed-env `ruff`/`pytest`/`eslint`), saves `validation_report` |
| 10 | `github_create_branch_and_draft_pr` | `B` | `{code_change_id: UUID, base_branch: str, pr_title: str, pr_body: str}` | Requires `validation_status == 'passed'`. Creates branch, blobs/tree/commit, opens **Draft PR (`draft=true`)**. Never merges |
| 11 | `generate_flag_cleanup_patch` | `A` | `{repository_id: UUID, feature_flag_id: UUID, keep_branch: "enabled"\|"disabled", files_diff: list, generated_tests: list}` | Persists `CodeChange(change_type='flag_cleanup')` ready for `validate_patch_isolated` |

### 3.2 `flag_agent` Tools (13 Tools)

| # | Tool Name | Policy Cat | Input Schema (Key Fields) | Output Summary & Invariants |
| :- | :--- | :-: | :--- | :--- |
| 12 | `flagsmith_list_projects` | `A` | `{}` | Lists Flagsmith projects `{id, name, organisation_id}` |
| 13 | `flagsmith_list_environments` | `A` | `{project_id: int}` | Lists environments with `api_key`, `is_production`, and `use_v2_feature_versioning` |
| 14 | `flagsmith_list_flags` | `A` | `{project_id: int, environment_name?: str}` | Lists flags synced with live Flagsmith states & rollout percentages |
| 15 | `flagsmith_get_flag_state` | `A` | `{flag_key: str, environment_name: str, identity_id?: str}` | Reads Management API state + optional Edge API identity evaluation |
| 16 | `flagsmith_create_flag` | `B` | `{project_id: int, key: str, description?: str, flag_type: "STANDARD"\|"MULTIVARIATE", initial_value?: str, mv_options?: list}` | Creates project feature definition (starts **disabled** in production) |
| 17 | `flagsmith_upsert_percentage_segment` | `B` | `{project_id: int, segment_name: str, percentage: int}` | Creates/updates `PERCENTAGE_SPLIT` segment (`0..100`) without altering prod overrides directly |
| 18 | `flagsmith_toggle_flag` | `B/C` | `{flag_key: str, environment_name: str, enabled: bool, reason: str}` | `B` in non-prod, **`C` in prod**. Handles v1/v2 publish & verifies state |
| 19 | `flagsmith_set_gradual_rollout_stage` | `B/C` | `{flag_key: str, environment_name: str, target_percentage: 0\|10\|25\|50\|75\|100, strategy: str, reason: str}` | `B` in non-prod, **`C` in prod**. Updates `% split` or `mv-options`, verifies state, logs `rollout_history` |
| 20 | `flagsmith_configure_multivariate_split` | `B/C` | `{flag_key: str, environment_name: str, allocations: dict[str, int], reason: str}` | Validates allocations sum to `100`. `B` in non-prod, **`C` in prod** |
| 21 | `flagsmith_rollback_flag` | `B/C` | `{flag_key: str, environment_name: str, reason: str}` | Reverts to `0%` / `enabled=false` (or prior version). `B` in non-prod, **`C` in prod** |
| 22 | `flagsmith_verify_live_state` | `A` | `{flag_key: str, environment_name: str, expected_enabled?: bool, expected_percentage?: int}` | Queries Management + Edge APIs and reports `{matches: bool, actual_state}` |
| 23 | `scan_flag_health` | `A` | `{project_id?: int, repository_id?: UUID}` | Runs 7 deterministic health detectors + explainable score, persists onto `feature_flags` |
| 24 | `flagsmith_delete_or_archive_flag` | `C` | `{flag_key: str, action: "archive"\|"delete", reason: str}` | **Always `C`** regardless of environment. Explicit human approval mandatory |

### 3.3 `experiment_agent` Tools (10 Tools)

| # | Tool Name | Policy Cat | Input Schema (Key Fields) | Output Summary & Invariants |
| :- | :--- | :-: | :--- | :--- |
| 25 | `flagsmith_probe_experiment_capability` | `A` | `{environment_key?: str}` | Probes `/environments/{env_key}/experiments/`, returns `flagsmith_native` or `tracked_event_fallback` |
| 26 | `create_experiment_plan` | `A` | `{flag_key: str, name: str, hypothesis: str, environment_name: str, control_variation: str, treatment_variation: str, traffic_allocation: dict, primary_metric: str, guardrail_metrics: list, guardrail_thresholds?: dict}` | Persists `Experiment(status='draft')` in DB |
| 27 | `activate_experiment` | `B/C` | `{experiment_id: UUID}` | Applies configured split in Flagsmith & sets status `running`. `B` non-prod, **`C` prod** |
| 28 | `stop_or_conclude_experiment` | `B/C` | `{experiment_id: UUID, outcome: "completed"\|"paused"\|"rolled_back", reason: str}` | Stops traffic split & updates experiment status. `B` non-prod, **`C` prod** |
| 29 | `fetch_experiment_events_summary` | `A` | `{experiment_id?: UUID, flag_key?: str, environment_name?: str, window_minutes?: int}` | Queries `experiment_events` (or native API) returning exact counts per variation & event type |
| 30 | `compute_bayesian_experiment_analysis` | `A` | `{experiment_id: UUID, mc_samples?: int}` | Calls pure-Python Beta-Binomial engine (`posterior alpha/beta`, `P(T>C)`, 95% CrIs, expected loss, guardrails) |
| 31 | `evaluate_release_recommendation` | `A` | `{experiment_id: UUID}` | Calls deterministic Release Recommendation Engine -> exact 1 of 5 statuses + triggers |
| 32 | `investigate_release_incident` | `A` | `{flag_key?: str, environment_name: str, window_minutes?: int}` | Correlates error spikes with `rollout_history` & PRs, outputs `observed_metrics`, `temporal_correlations`, `confounding_checks` |
| 33 | `get_rollout_timeline` | `A` | `{flag_key?: str, environment_name?: str, limit?: int}` | Reads `rollout_history` & `audit_logs` chronological entries |
| 34 | `propose_release_action` | `A` | `{flag_key: str, environment_name: str, proposed_action: "expand"\|"reduce"\|"rollback", target_percentage?: int, rationale: str}` | Enqueues a formal `Approval` (`C` in prod, `B` in non-prod) with `action_hash` & precondition snapshot without executing |

---

## 4. Environment Variables & Docker Compose Contract

### 4.1 Complete `.env.example` Variable Reference

| Variable | Default / Example | Required | Service(s) | Purpose |
| :--- | :--- | :---: | :--- | :--- |
| `APP_ENV` | `development` | Yes | `backend` | Runtime mode (`development\|staging\|production`) |
| `LOG_LEVEL` | `INFO` | No | `backend` | Logging verbosity |
| `BACKEND_HOST` | `0.0.0.0` | No | `backend` | Uvicorn bind host |
| `BACKEND_PORT` | `8000` | No | `backend` | ReleaseGuard FastAPI port |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:3001` | Yes | `backend` | Comma-separated allowed origins |
| `SECRET_KEY` | `change-me-32-char-random-string` | Yes | `backend` | HMAC state signing (GitHub OAuth state nonce) |
| `ENCRYPTION_KEY` | `<Fernet.generate_key()>` | Yes | `backend` | Fernet symmetric key encrypting `integration_connections` tokens |
| `POSTGRES_USER` | `releaseguard` | Docker | `postgres, backend` | PostgreSQL user |
| `POSTGRES_PASSWORD` | `releaseguard` | Docker | `postgres, backend` | PostgreSQL password |
| `POSTGRES_DB` | `releaseguard` | Docker | `postgres, backend` | PostgreSQL database name |
| `POSTGRES_PORT` | `5432` | Docker | `postgres` | Exposed PostgreSQL port |
| `DATABASE_URL` | `postgresql+asyncpg://releaseguard:releaseguard@postgres:5432/releaseguard` | Docker | `backend` | Primary async PostgreSQL URL |
| `USE_SQLITE_FALLBACK` | `false` (`true` for non-Docker local) | No | `backend` | Switches backend + Alembic to `SQLITE_FALLBACK_URL` |
| `SQLITE_FALLBACK_URL` | `sqlite+aiosqlite:///./releaseguard_local.db` | No | `backend` | Local SQLite DB file URL |
| `GEMINI_API_KEY` | `your-gemini-api-key` | Yes (AI) | `backend` | Official `google-genai` API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | No | `backend` | Configurable Gemini model ID |
| `GITHUB_CLIENT_ID` | `` | Optional | `backend` | GitHub OAuth app client ID |
| `GITHUB_CLIENT_SECRET` | `` | Optional | `backend` | GitHub OAuth app client secret |
| `GITHUB_OAUTH_REDIRECT_URI` | `http://localhost:3000/settings?github_callback=1` | Optional | `backend` | OAuth callback URL |
| `GITHUB_PAT` | `` | Optional | `backend` | Optional bootstrap PAT (can connect via `/settings` UI instead) |
| `FLAGSMITH_API_URL` | `https://api.flagsmith.com/api/v1` | Yes | `backend` | Flagsmith Management API base URL |
| `FLAGSMITH_EDGE_API_URL` | `https://edge.api.flagsmith.com/api/v1` | Yes | `backend, demo-*` | Flagsmith Edge Flags API base URL |
| `FLAGSMITH_ORG_TOKEN` | `` | Optional | `backend` | Optional bootstrap Flagsmith Org API token |
| `FLAGSMITH_PROJECT_ID` | `` | Optional | `backend` | Optional bootstrap Flagsmith project ID |
| `FLAGSMITH_DEV_ENV_KEY` | `` | Optional | `backend` | Flagsmith Development environment key |
| `FLAGSMITH_STAGING_ENV_KEY` | `` | Optional | `backend` | Flagsmith Staging environment key |
| `FLAGSMITH_PROD_ENV_KEY` | `` | Optional | `backend` | Flagsmith Production environment key |
| `APPROVAL_TTL_SECONDS` | `900` | No | `backend` | Single-use approval expiration TTL (15m) |
| `CATEGORY_B_REQUIRES_CONFIRMATION` | `true` | No | `backend` | Default confirmation policy for Category B |
| `BAYESIAN_MC_SAMPLES` | `50000` | No | `backend` | Deterministic Monte-Carlo draws |
| `BAYESIAN_MC_SEED` | `42` | No | `backend` | Reproducible RNG seed |
| `BAYESIAN_MIN_EXPOSURES` | `100` | No | `backend` | Minimum exposures per variant before recommendation |
| `EVENT_INGEST_KEY` | `dev-ingest-key-change-me` | Yes | `backend, demo-backend` | Shared key authenticating `/api/v1/events/ingest` |
| `PATCH_VALIDATION_TIMEOUT_SECONDS` | `30` | No | `backend` | Subprocess timeout for isolated temp-dir patch validator |
| `FRONTEND_PORT` | `3000` | No | `frontend` | ReleaseGuard Next.js control-plane port |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Yes | `frontend` | Browser-reachable ReleaseGuard backend URL |
| `NEXT_PUBLIC_DEMO_APP_URL` | `http://localhost:3001` | No | `frontend` | Link from Control Plane to live Demo Storefront |
| `DEMO_BACKEND_PORT` | `8001` | No | `demo-backend` | Demo Checkout API port |
| `DEMO_FRONTEND_PORT` | `3001` | No | `demo-frontend` | Demo Storefront UI port |
| `NEXT_PUBLIC_DEMO_API_URL` | `http://localhost:8001` | Yes | `demo-frontend` | Demo Checkout API URL |
| `NEXT_PUBLIC_FLAGSMITH_ENV_KEY` | `` | Optional | `demo-frontend` | Client-side Flagsmith Edge key for demo storefront |
| `FLAGSMITH_SERVER_ENV_KEY` | `` | Optional | `demo-backend` | Server-side Flagsmith key for demo backend |
| `RELEASEGUARD_API_URL` | `http://backend:8000` (Docker) / `http://localhost:8000` (local) | Yes | `demo-backend` | Target backend URL for demo telemetry events |
| `RELEASEGUARD_INGEST_KEY` | `dev-ingest-key-change-me` | Yes | `demo-backend` | Header `X-Ingest-Key` sent on event ingestion |

### 4.2 `docker-compose.yml` Service Contract
1. `postgres`: `image: postgres:16-alpine`, port `5432:5432`, named volume `pgdata:/var/lib/postgresql/data`, healthcheck `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`.
2. `backend`: builds `./backend`, runs `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000`, `depends_on: postgres (condition: service_healthy)`, port `8000:8000`, healthcheck `GET http://localhost:8000/health`.
3. `frontend`: builds `./frontend`, port `3000:3000`, `depends_on: backend (condition: service_healthy)`, `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`.
4. `demo-backend`: builds `./demo-app/backend`, port `8001:8001`, `depends_on: backend (condition: service_healthy)`, `RELEASEGUARD_API_URL=http://backend:8000`.
5. `demo-frontend`: builds `./demo-app/frontend`, port `3001:3001`, `depends_on: demo-backend`, `NEXT_PUBLIC_DEMO_API_URL=http://localhost:8001`.
