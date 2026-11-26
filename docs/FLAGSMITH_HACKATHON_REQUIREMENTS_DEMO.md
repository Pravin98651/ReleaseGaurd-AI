# ReleaseGuard AI — Flagsmith Hackathon Requirements Demo Guide

**Track:** WeMakeDevs Flagsmith Hackathon — Track 02 (AI Agents and Feature Flags)  
**Target Stack:** ReleaseGuard AI Control Plane (`http://localhost:3000`) + NovaShop Storefront (`http://localhost:3001`) + Flagsmith Cloud Project `#56055` (`Development #120019`) + GitHub (`Pravin98651/ReleaseGaurd-AI`).

---

## Cohesive 11-Step Judge Walkthrough (All 5 Hackathon Ideas in One System)

1. **Submit a Natural-Language Feature Request (`/command-center` or `/dashboard`)**
   - Click the **Feature Request** quick-launcher in `/command-center` (or call `POST /api/v1/releases/plan-from-prompt`).
   - Prompt: *"Add a new recommendation engine using personalized recommendations. Protect it with a feature flag, compare it against the existing recommendation engine, and gradually release it if the new version performs better."*
2. **Inspect the AI's Structured Implementation Plan**
   - `feature_request_planner.py` returns `recommended_flag_key: personalized_recommendation_v2`, `flag_type: MULTIVARIATE`, `safe_default: {enabled: false, value: "control"}`, repo implementation points, canonical experiment metrics (`checkout_completed` + `payment_failed` / `checkout_abandoned` guardrails), and a `Release` record (`REL-2026-...`).
3. **Show PR Analysis & Flag-Wrapped Code Proposal (`/pull-requests`)**
   - `pr_agent` analyzes real GitHub PRs with 7-dimension risk scoring, detects flag opportunities, validates patches with `PatchValidator`, and opens **Draft PRs (`draft=True`)** only—never auto-merging.
4. **Show the Flagsmith Feature Flag & Experiment (`/flagsmith` & `:3001`)**
   - Inspect live Project `#56055` flags (`checkout_experience_v2` `#304842`, `recommendation_engine_v2` `#304844`, kill switches, trait segments) and NovaShop's live Flagsmith JS & Python SDK evaluations.
5. **Show Conversational Identity Evaluation & Explanation (`/command-center` or `/flagsmith`)**
   - Click **Identity Explanation** in `/command-center` (*"Explain which feature flags user_123 receives in the development environment"*).
   - `flagsmith_explain_identity` evaluates live against Flagsmith Edge `POST /identities/`, redacts sensitive traits, and outputs an observed-vs-inferred table.
6. **Show Experiment Results & Deterministic Bayesian Decision (`/experiments`)**
   - Pure-Python Beta-Binomial engine (`50,000` MC draws) demonstrates:
     - `INSUFFICIENT_DATA` (`25/arm` → `409 EXPERIMENT_NOT_READY`)
     - `GUARDRAIL_BREACHED` (`payment_failed` spike → `409 GUARDRAIL_BREACHED`)
     - `VARIANT_WINS` (`120/arm`, `P=0.99934`, `loss=7e-06` → `202 Accepted`)
7. **Show the Category C Human Approval Gate (`/approvals`)**
   - Requester (`operator@releaseguard.local`) cannot self-approve; independent reviewer (`reviewer@releaseguard.local`) approves the `SHA-256` hash-bound, single-use request (replay returns `409 Conflict`).
8. **Show the Approved Progressive Rollout (`/progressive-delivery`)**
   - `approval_executor` checks for live precondition drift, updates Flagsmith, and calls `verify_live_state()` readback across staged canary percentages (`1% → 5% → 10% → 25% → 50% → 100%`).
9. **Show Stale-Flag Cleanup Recommendation (`/flag-health`)**
   - Post-promotion dependency checks block `CLEANUP_RECOMMENDED` if any environment is `<100%`, and recommend cleanup only when safe.
10. **Show the Resulting Cleanup Draft PR & Pre-Retirement Gate**
    - `POST /api/v1/flag-health/{id}/cleanup-pr` opens a validated AST/TSX GitHub Draft PR (proven live with PR `#17`), while `request-delete` blocks with `409` until code references are removed and Category C approval archives the flag in Flagsmith.
11. **Show Real Measured Time-Savings Evidence (`/metrics`)**
    - `run_benchmark.py --workflow flag_setup --trials 3 --require-live` recorded `VERIFIED_MEASURED` timings (`mean=5.7719s`, `median=5.5493s`, `p95=6.5291s`) alongside clearly labeled `ESTIMATED` manual baselines.
