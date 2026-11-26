# ReleaseGuard AI — Phase 1 Live Proof Report (3 Repeatable Runs)

**Executed At:** `2026-11-26T13:57:43.030365+00:00`  
**Environment Scope:** `development` ONLY (Flagsmith Project `#56055`, Development `#120019` — Production `#120020` and GitHub `main` strictly untouched)  
**Target Flag / Experiment:** `checkout_experience_v2` (Flag `#304842`, Flagsmith Experiment `#503`)  
**Repeatable Driver:** [`backend/scripts/phase1_live_proof.py`](../backend/scripts/phase1_live_proof.py) (`--runs 3`)  
**Raw JSON Evidence:** `phase1_live_proof_results.json` (`runs_completed: 3`, `open_github_prs_after: 0`)

---

## 1. Infrastructure, Migrations & Pre-Run Fixes Verified

### Docker Compose Stack (All 5 Services Running & Healthy)
| Service | Port | Status |
|---|---|---|
| `backend` (`releaseguardai-backend-1`) | `8000` | Up (healthy) |
| `demo-backend` (`releaseguardai-demo-backend-1`) | `8001` | Up (healthy) |
| `frontend` (`releaseguardai-frontend-1`) | `3000` | Up |
| `demo-frontend` (`releaseguardai-demo-frontend-1`) | `3001` | Up |
| `postgres` (`releaseguardai-postgres-1`) | `5432` | Up (healthy) |

### Database Migrations & Code Fixes Landed During Live Proof
- **Alembic Head:** `0013_bigint_github_ids` (on top of `0012_productivity_benchmarks`).
- **GitHub 64-bit ID Overflow Fix:** Widened `installation_id`, `external_check_run_id`, `external_comment_id`, `github_org_id`, and `github_repository_id` from `Integer` (int32) to `BigInteger` (int64) in [`backend/app/models/dx_integration.py`](../backend/app/models/dx_integration.py), [`backend/app/models/organization.py`](../backend/app/models/organization.py), and [`backend/alembic/versions/0013_bigint_github_ids.py`](../backend/alembic/versions/0013_bigint_github_ids.py) after live GitHub comment IDs (`7193669365+`) exceeded int32 max (`2,147,483,647`).
- **Gemini Enum Normalization Fix:** Added `_normalize_flag_type()` and `_normalize_risk_severity()` in [`backend/app/agents/specialists/pr_agent.py`](../backend/app/agents/specialists/pr_agent.py) so LLM outputs (e.g. `"boolean"` -> `"STANDARD"`) conform to `FlagType` / `RiskLevel` Pydantic schemas.
- **Flagsmith Edge Eventual-Consistency Poll:** Added brief polling (up to 10 attempts x 2s) on Edge identity evaluation in [`backend/scripts/phase1_live_proof.py`](../backend/scripts/phase1_live_proof.py) after Management API v2 version publish so Flagsmith Cloud Edge DynamoDB propagation is cleanly verified.

### Distinct Human Approval Identities (Non-Self-Approval Enforced)
| Role | Email | User ID |
|---|---|---|
| Requester (Operator) | `operator@releaseguard.local` | `76eda754-2263-40b0-bd36-bfadd1fe9fe2` (`admin`) |
| Approver (Independent Reviewer) | `reviewer@releaseguard.local` | `19fcc38c-322c-4d19-81b5-ad4643cc08c0` (`release_manager`) |

---

## 2. Summary Across All 3 Repeatable Runs

| Metric | Run 1 (`74d687`) | Run 2 (`dc7074`) | Run 3 (`fe8b3c`) |
|---|---|---|---|
| **Duration** | `375.95s` | `368.40s` | `358.55s` |
| **GitHub Demo PR** | `#11` (`demo/phase1-proof-r1-74d687`) | `#12` (`demo/phase1-proof-r2-dc7074`) | `#13` (`demo/phase1-proof-r3-fe8b3c`) |
| **PR UUID** | `5d49c834-6f26-4ae5-bcf7-b2b498dc0717` | `1dd64398-5f68-4a96-bad2-cc75f735c760` | `e5642c93-de13-4b55-af58-5d2c9e036a77` |
| **Risk Assessment Key** | `RSK-2026-0003` | `RSK-2026-0004` | `RSK-2026-0005` |
| **PR Risk / 7-Dim Score** | `95` (`critical`) / `37` (7 dims) | `95` (`critical`) / `37` (7 dims) | `95` (`critical`) / `37` (7 dims) |
| **DX Comment ID (64-bit)** | `7193669365` (`published`) | `7193817422` (`published`) | `7193957150` (`published`) |
| **NovaShop Live Check** | `ok` (4 checkouts, 1 tracked batch) | `ok` (4 checkouts, 1 tracked batch) | `ok` (4 checkouts, 1 tracked batch) |
| **Scenario C (25/arm)** | `INSUFFICIENT_DATA` -> `409 EXPERIMENT_NOT_READY` | `INSUFFICIENT_DATA` -> `409 EXPERIMENT_NOT_READY` | `INSUFFICIENT_DATA` -> `409 EXPERIMENT_NOT_READY` |
| **Scenario B (110/arm)** | `GUARDRAIL_BREACHED` (`0.1636` vs `0.0091`) -> `409` | `GUARDRAIL_BREACHED` (`0.1636` vs `0.0091`) -> `409` | `GUARDRAIL_BREACHED` (`0.1636` vs `0.0091`) -> `409` |
| **Scenario A (120/arm)** | `VARIANT_WINS` (`P=0.99934`, loss `7e-06`) -> `202` | `VARIANT_WINS` (`P=0.99934`, loss `7e-06`) -> `202` | `VARIANT_WINS` (`P=0.99934`, loss `7e-06`) -> `202` |
| **Approval ID (Cat C)** | `18574bfb-0854-4704-aec4-ad5b32287df7` | `8e250e9d-5c17-4aa1-87fe-d5a63817b8f8` | `8b75b3a8-bb27-40f4-934b-8671043322d8` |
| **Single-Use Replay** | `409 Conflict` | `409 Conflict` | `409 Conflict` |
| **Edge Winner Readback** | `one_page_checkout` (`100%`) | `one_page_checkout` (`100%`) | `one_page_checkout` (`100%`) |
| **Dev #503 Restored** | `34 / 33 / 33` (`matches: true`) | `34 / 33 / 33` (`matches: true`) | `34 / 33 / 33` (`matches: true`) |
| **PR Closed & Branch Deleted** | PR `#11` closed, `204` | PR `#12` closed, `204` | PR `#13` closed, `204` |
| **Prod #120020 & `main` Untouched** | `true` / `true` | `true` / `true` | `true` / `true` |

---

## 3. Per-Run Detailed Evidence

### Run 1 (`run_tag: 74d687`, `375.95s`)
1. **Part (a) Real GitHub PR + Analysis:**
   - Opened PR `#11` (`https://github.com/Pravin98651/ReleaseGaurd-AI/pull/11`, head SHA `f884d1d35ad7fe40430fcef134b9aa4423efddd7`) on branch `demo/phase1-proof-r1-74d687`.
   - Initial sync: `10` flags, `4` experiments.
   - Analysis (`RSK-2026-0003`): `risk_score=95` (`critical`), 7-dim score `37` across `7` dimensions, `1` confirmed risk, `1` flag opportunity (`express_checkout_promo`, `STANDARD`, target `demo-app/backend/app/phase1_demo_promo_r1.py`).
   - DX output: comment `7193669365` (`published`), check run `completed` / `failure` (`warn_medium`).
2. **Part (b) NovaShop + Scenarios C, B, A:**
   - NovaShop (`http://localhost:8001`): `flagsmith_connected=true`, `environment_name=development`, 4 checkouts `completed`, `/events/track` accepted 1 batch.
   - **Scenario C:** 100 Edge evaluations -> 25/arm (150 events ingested/accepted) -> `INSUFFICIENT_DATA`, `is_adequate=false`, `Continue Monitoring`, promote blocked with `409 EXPERIMENT_NOT_READY`.
   - **Scenario B:** 440 Edge evaluations -> 110/arm (660 events ingested/accepted), control error rate `0.009091`, treatment error rate `0.163636` -> `GUARDRAIL_BREACHED`, `guardrail_breached=true`, `Recommend Rollback`, promote blocked with `409 GUARDRAIL_BREACHED`.
   - **Scenario A:** 480 Edge evaluations -> 120/arm (`14/120` control conversions vs `34/120` treatment conversions, 720 events ingested/accepted) -> `VARIANT_WINS`, `prob_treatment_beats_control=0.99934`, `expected_loss_choose_treatment=7e-06`, `Expand Rollout`, Flagsmith results refresh `202`.
3. **Part (c) 12-Step Winner Promotion:**
   - Pre-promotion Dev snapshot: `enabled=true`, `version_uuid=a3f82693-c131-4459-835b-c4a7f723354c`, MV split `34/33/33`.
   - `POST /api/v1/experiments/73ebeafa-9192-4bb3-98b1-193039c5827d/promote-winner?winning_variation=one_page_checkout` -> `202 Accepted`, Category `C` approval `18574bfb-0854-4704-aec4-ad5b32287df7` (`action_hash=84bce403bd765154be21ad0e8ee837746e8744baf1f6431b2852617f08364e56`).
   - Approved by `reviewer@releaseguard.local` (`19fcc38c-322c-4d19-81b5-ad4643cc08c0`, distinct from requester `76eda754-2263-40b0-bd36-bfadd1fe9fe2`) -> `status=executed`.
   - Replay attempt -> `409 Conflict`.
   - Live Management + Edge readback: `mv_allocations={control: 0, one_page_checkout: 100, simplified_checkout: 0}`, Edge evaluation value `one_page_checkout`.
   - Audit actions verified: `approval.executed`, `experiment.winner_promotion_requested`, `approval.created`, `pr.analyzed`, `agent_execution.completed`, `pull_requests.synced`.
4. **Part (d) Recovery & Isolation:**
   - Flagsmith Dev `#503` restored to `34/33/33` (`matches=true`); PR `#11` closed & branch deleted (`204`); Production `#120020` and `main` unchanged.

---

### Run 2 (`run_tag: dc7074`, `368.40s`)
1. **Part (a):** PR `#12` (`https://github.com/Pravin98651/ReleaseGaurd-AI/pull/12`, SHA `dee381718cc5bd0842aae9cfd2ba241ea50f0f79`, UUID `1dd64398-5f68-4a96-bad2-cc75f735c760`), Assessment `RSK-2026-0004`, `risk_score=95` (`critical`), 7-dim `37`, flag opportunity `express_checkout_promo_v2` (`STANDARD`), DX comment `7193817422`.
2. **Part (b):** NovaShop `ok` (4 checkouts, 1 batch). Scenario C (100 evals, 25/arm, 150 events) -> `409 EXPERIMENT_NOT_READY`. Scenario B (440 evals, 110/arm, 660 events, error `0.163636` vs `0.009091`) -> `409 GUARDRAIL_BREACHED`. Scenario A (480 evals, 120/arm, `14` vs `34` conversions, 720 events) -> `VARIANT_WINS` (`P=0.99934`, loss `7e-06`), refresh `202`.
3. **Part (c):** Pre-promotion `version_uuid=aabf7220-13db-485b-ac4e-aa3f6da41690`. Category `C` approval `8e250e9d-5c17-4aa1-87fe-d5a63817b8f8` requested by operator, executed by `reviewer@releaseguard.local`, replay `409`. Management & Edge readback `one_page_checkout` (`0/100/0`). Audit actions verified.
4. **Part (d):** Dev `#503` restored to `34/33/33` (`matches=true`); PR `#12` closed & branch deleted (`204`); Production `#120020` and `main` unchanged.

---

### Run 3 (`run_tag: fe8b3c`, `358.55s`)
1. **Part (a):** PR `#13` (`https://github.com/Pravin98651/ReleaseGaurd-AI/pull/13`, SHA `e99d540f33cd7435351e811df6891c2627196744`, UUID `e5642c93-de13-4b55-af58-5d2c9e036a77`), Assessment `RSK-2026-0005`, `risk_score=95` (`critical`), 7-dim `37`, flag opportunity `express_checkout_promo` (`STANDARD`), DX comment `7193957150`.
2. **Part (b):** NovaShop `ok` (4 checkouts, 1 batch). Scenario C (100 evals, 25/arm, 150 events) -> `409 EXPERIMENT_NOT_READY`. Scenario B (440 evals, 110/arm, 660 events, error `0.163636` vs `0.009091`) -> `409 GUARDRAIL_BREACHED`. Scenario A (480 evals, 120/arm, `14` vs `34` conversions, 720 events) -> `VARIANT_WINS` (`P=0.99934`, loss `7e-06`), refresh `202`.
3. **Part (c):** Pre-promotion `version_uuid=f64a6460-bbe3-4d3d-a572-d1b60a2ba2f1`. Category `C` approval `8b75b3a8-bb27-40f4-934b-8671043322d8` requested by operator, executed by `reviewer@releaseguard.local`, replay `409`. Management & Edge readback `one_page_checkout` (`0/100/0`). Audit actions verified.
4. **Part (d):** Dev `#503` restored to `34/33/33` (`matches=true`); PR `#13` closed & branch deleted (`204`); Production `#120020` and `main` unchanged.

---

## 4. Final State Verification
- `open_github_prs_after`: `0` (all temporary demo PRs `#11`, `#12`, `#13` closed and branches deleted).
- Flagsmith Development `#120019` (`checkout_experience_v2`): restored to `34 / 33 / 33` (`control` / `one_page_checkout` / `simplified_checkout`).
- Flagsmith Production `#120020` and GitHub `main`: verified unchanged after every run.

---

## 5. Bayesian Cohort Accounting & 3-Arm vs. 2-Arm Reconciliation

> **Reconciliation Note:** Sections 1–4 preserve the historical output of `phase1_live_proof.py` (executed when Alembic head was `0013_bigint_github_ids`; current repository head is `0015_gemini_35_default`). This section reconciles the exact participant, evaluation, and variant accounting.

### Why `480` Flagsmith Edge Evaluations Yield `120` Participants per Arm (`720` Events)
1. **3-Arm Flagsmith Split vs. 2-Arm Bayesian Engine (Known Limitation):**
   - Flagsmith Experiment `#503` (`checkout_experience_v2`, Flag `#304842`) is configured in Development (`#120019`) with **3 variants**: `control` (`34%`), `one_page_checkout` (`33%`), and `simplified_checkout` (`33%`).
   - ReleaseGuard AI's pure-Python Bayesian engine (`backend/app/analytics/bayesian.py` — `BayesianCountsInput`, `aggregate_experiment_events()`) is strictly a **two-arm** (`control_variation` vs. `treatment_variation`) Beta-Binomial comparator. It does **not** perform 3-way multi-arm Bayesian comparisons; any unmapped third-arm events (`simplified_checkout`) are counted in `unattributed_events` and excluded from the pairwise posterior.
2. **Identity Assignment & Sampling (`_assign_live_flagsmith_participants`):**
   - For Scenario A (`min_per_arm=120`), `phase1_live_proof.py` evaluates `batch_size = max(40, 120 * 4) = 480` distinct identities against live Flagsmith Edge (`GET /identities/`).
   - Across the 3-way split (~34% / 33% / 33%), roughly ~160 identities land in each arm. The runner selects the first **`120` unique `control` participants** and **`120` unique `one_page_checkout` participants** (`240` unique participants total), discarding `simplified_checkout` and excess evaluations.
3. **Synthetic Event Generation & Deduplication (`_build_cohort_events` & `aggregate_experiment_events`):**
   - For each of the `240` selected participants, `_build_cohort_events()` emits **`3` events** (`feature_variation_assigned`, `checkout_started`, and one terminal outcome: `checkout_completed`, `payment_failed`, or `checkout_abandoned`), all tagged `synthetic_test_traffic: True`.
   - `240 unique participants × 3 events/participant = 720 raw events` ingested via `POST /api/v1/events/ingest`.
   - `aggregate_experiment_events()` deduplicates by `event_id`, attributes each `user_identifier` once to their earliest `feature_variation_assigned` arm, and counts unique participants per outcome (`0 <= k <= n`). Repeat evaluations never inflate `n`.
   - `min_exposures = 100` is enforced on **unique participants per arm** (`control_exposures` and `treatment_exposures`).
   - Live Flagsmith `GET /experiments/503/results` returns `variants: {}` (`is_final: false`); ReleaseGuard's Bayesian calculations run deterministically on the `/api/v1/events/ingest` PostgreSQL cohort.

### Explicit Cohort Accounting Table (Per Run)

| Metric Label | Scenario C (Insufficient Data) | Scenario B (Guardrail Breach) | Scenario A (Winning Variant) |
|---|---:|---:|---:|
| **Total Flagsmith Edge Evaluations** | `100` *(batch=100 across 3 arms)* | `440` *(batch=440 across 3 arms)* | `480` *(batch=480 across 3 arms)* |
| **Unique Participants (Total in 2-Arm Analysis)** | `50` | `220` | `240` |
| **Control Sample Size (`control` unique participants)** | `25` | `110` | `120` |
| **Treatment Sample Size (`one_page_checkout` unique participants)** | `25` | `110` | `120` |
| **Valid Exposures (`feature_variation_assigned`, deduplicated)** | `25` ctrl / `25` trt | `110` ctrl / `110` trt | `120` ctrl / `120` trt |
| **Total Ingested Synthetic Events (`3` per participant)** | `150` | `660` | `720` |
| **Conversion Events (`checkout_completed`, unique users)** | `3` ctrl / `7` trt | — *(blocked by guardrail)* | `14` ctrl (`11.67%`) / `34` trt (`28.33%`) |
| **Guardrail Events (`payment_failed`, unique users)** | `0` ctrl / `0` trt | `1` ctrl (`0.91%`) / `18` trt (`16.36%`) | `<=` threshold *(no breach)* |
| **Bayesian Outcome & Promotion Gate** | `INSUFFICIENT_DATA` (`409 EXPERIMENT_NOT_READY`) | `GUARDRAIL_BREACHED` (`409 GUARDRAIL_BREACHED`) | `VARIANT_WINS` (`P=0.99934`, `loss=7e-06` -> `202`) |
