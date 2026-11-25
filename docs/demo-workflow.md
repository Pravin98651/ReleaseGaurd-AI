# ReleaseGuard AI — End-to-End Demo Workflow Guide

This walkthrough demonstrates the complete **Checkout v1 -> Checkout v2** release engineering lifecycle using **ReleaseGuard AI** (`http://localhost:3000`) and the live **NovaShop Demo Storefront** (`http://localhost:3001`).

> **Automated Verification:** This entire sequence (with mocked GitHub/Flagsmith/Gemini HTTP transports over a real SQLite DB, real isolated `PatchValidator`, real pure-Python Bayesian math, and Category C Policy Engine) is also continuously tested in [test_e2e_checkout_workflow.py](file:///c:/Projects/ReleaseGuard%20AI/backend/tests/test_e2e_checkout_workflow.py).

---

## Step-by-Step Live Demo Sequence

### 1. Verify System Readiness (`/settings`)
1. Open `http://localhost:3000/settings` (if `REQUIRE_AUTH=true`, sign in first via email/password or GitHub OAuth in the auth gate).
2. Connect **GitHub** (PAT or OAuth) and **Flagsmith** (`org_token`, `project_id`, `development`/`staging`/`production` environment keys).
3. Observe encrypted token masking (`token_hint`), automatic Flagsmith v1/v2 versioning detection, **Analytics Mode** (`flagsmith_native` or `tracked_event_fallback`), and **RBAC Workspace Operators** (for `ADMIN` accounts).

### 2. Sync Repository & Pull Request (`/repositories` -> `/pull-requests`)
1. Go to `/repositories`, sync or add your repository (with `flagsmith_project_id` linked), and optionally run **Scan Flags** to detect existing Flagsmith SDK usages.
2. Go to `/pull-requests` and click **Sync Pull Requests**.
3. Select the unflagged `checkout-v2` pull request (see [sample_pr_checkout_v2.diff](file:///c:/Projects/ReleaseGuard%20AI/demo-app/sample_pr_checkout_v2.diff) for reference).

### 3. AI Risk Assessment & Feature-Flagged Patch (`/pull-requests`)
1. Click **Analyze PR** (`POST /api/v1/pull-requests/{id}/analyze`).
   - Review structured `confirmed_risks` (evidence-backed file/line citations), `potential_risks` (explicit assumptions), and `flag_opportunities` recommending `checkout_v2`.
2. Click **Generate Flagged Patch** (`POST /api/v1/pull-requests/{id}/generate-patch`).
   - Inspect the generated side-by-side `CodeDiffViewer` (`before_code` vs `after_code`), unified diff, and generated unit tests.
3. Click **Validate in Sandbox** (`POST /api/v1/code-changes/{id}/validate`).
   - Runs isolated temp-dir checks (`ast.parse`, unsafe path/`.env`/`.git` blocks, `ruff`, `pytest`).
4. Click **Create Draft PR** (`POST /api/v1/code-changes/{id}/create-draft-pr`).
   - Queues a Category B human confirmation (when `CATEGORY_B_REQUIRES_CONFIRMATION=true`); approve in `/approvals` to open a **Draft PR (`draft: true`)** on GitHub. Auto-merge is permanently disabled.

### 4. Create Flag & Natural-Language Rollout Command (`/feature-flags` or `/agent`)
1. In `/feature-flags`, create `checkout_v2` (`POST /api/v1/feature-flags`, starts disabled in `production`) and approve the Category B creation request.
2. In `/agent` (or `/feature-flags` NL bar), submit:
   `"Roll out checkout_v2 to 10% in production with monitoring"`
3. Watch the live SSE stream (`task.started` -> `agent.thinking` -> `agent.tool_proposed` -> `policy.evaluated` -> `approval.required`) pause at the LangGraph `interrupt()` gate.
4. Approve the **Category C** production rollout in `/approvals` (`0% -> 10%`): ReleaseGuard verifies live Flagsmith state before execution, applies the `rg_rollout_checkout_v2` `PERCENTAGE_SPLIT` segment, reads back live Flagsmith state to confirm `verified: true`, and resumes the LangGraph thread.

### 5. Plan & Activate Experiment (`/experiments`)
1. Open `/experiments` and plan an experiment:
   `"Compare checkout_v2 vs checkout v1 conversion rate and payment_failed guardrails in production"`
2. Activate the experiment (Category C approval in production).

### 6. Generate Live Telemetry from NovaShop (`http://localhost:3001`)
1. Open `http://localhost:3001`.
2. Use the storefront directly (evaluating `checkout_v2` via Flagsmith SDK) OR click **Simulate 120 Healthy Checkouts** in the Simulator Bar (`POST http://localhost:8001/api/simulate` with `scenario=healthy_v2`).
3. Confirm in NovaShop's **Telemetry Inspector** that `feature_variation_assigned`, `checkout_started`, `checkout_completed`, and `payment_failed` events were accepted by ReleaseGuard `POST /api/v1/events/ingest`.

### 7. Inspect Bayesian Posteriors & Expand Rollout (`/experiments` -> `/releases` -> `/approvals`)
1. Refresh `/experiments` and run Bayesian Analysis (`GET /api/v1/experiments/{id}/analysis`).
   - Inspect Beta-Binomial posteriors (`50,000` MC samples), 95% credible intervals, `prob_treatment_beats_control`, expected loss, and `payment_failed` guardrail metrics.
   - Deterministic Recommendation reads **`Expand Rollout`**.
2. Trigger a rollout step `10% -> 25%` in `/releases` (`POST /api/v1/feature-flags/{id}/rollout`).
3. Approve the **Category C** action in `/approvals` after checking `action_hash` and `expected_precondition`.

### 8. Simulate Payment Regression, Incident Investigation & Category C Rollback
1. Back on `http://localhost:3001`, click **Simulate Payment Regression** (`scenario=buggy_v2`, or checkout with promo `PROMO-BUG` / regression toggle).
2. Refresh `/experiments` to observe the `payment_failed` guardrail breach and **`Recommend Rollback`** status.
3. Open `/releases` and click **Investigate Incident** (`POST /api/v1/releases/investigate`).
   - Review 5-minute timeline bins, +/-30 minute correlation against rollout history and PRs, variation-stratified confounding checks, and the explicit **`correlation_not_causation: true`** disclaimer.
4. Click **Trigger Rollback** (`POST /api/v1/feature-flags/{id}/rollback`), approve the **Category C** rollback in `/approvals`, and verify live Flagsmith state drops to `0%` / `enabled: false`.

### 9. Flag Health Hygiene & Compliance Audit (`/flag-health` & `/audit-logs`)
1. Open `/flag-health` and click **Run Health Scan** to view the 7-detector scoring breakdown (`stale`, `unused`, `always_on`, `always_off`, `orphaned`, `duplicate`, `env_drift`).
2. Optionally generate a Cleanup Draft PR (`POST /api/v1/flag-health/{id}/cleanup-pr`) or queue a Category C archive request (`POST /api/v1/flag-health/{id}/request-delete`).
3. Open `/audit-logs` to inspect the immutable audit trail and export compliance logs as JSON or CSV (`/api/v1/audit-logs/export`).
