# ReleaseGuard AI — Three-Minute Hackathon Demo Script

**Target Duration:** `3:00`  
**Stack:** Live Docker Compose (`http://localhost:3000`, `http://localhost:3001`, `http://localhost:8000`) + Live Flagsmith Project `#56055` (`Development #120019`) + Live GitHub (`Pravin98651/ReleaseGaurd-AI`).  
**Rule:** Show only real application screens, real Flagsmith/GitHub state, and clearly state when synthetic NovaShop traffic is used.

---

### 0:00–0:20 — Introduction (`/dashboard`)
- **Screen:** ReleaseGuard AI Control Plane Dashboard (`http://localhost:3000/dashboard`).
- **Talking Points:**
  - *"Engineering teams ship fast, but releases still break between code review, feature flagging, experimentation, and flag cleanup. ReleaseGuard AI is an autonomous AI release engineer that closes that entire loop behind deterministic math and a human-in-the-loop PolicyEngine."*
  - Point out the **Central Release Workspace** ("Start an AI-powered release") and the 6-screen `DemoPathBar` at the top.

### 0:20–0:45 — GitHub PR Intelligence (`/pull-requests`)
- **Screen:** GitHub PR Analysis (`http://localhost:3000/pull-requests`).
- **Talking Points:**
  - *"Here is a real GitHub pull request synced from `Pravin98651/ReleaseGaurd-AI`. Gemini analyzes the diff with Automatic Function Calling disabled—it proposes findings and flag opportunities, never executes directly."*
  - Highlight the **7-dimension risk score**, **Flagsmith flag recommendation**, `PatchValidator` (`sha_ok`, `binary_ok`), and that ReleaseGuard only ever opens **Draft PRs (`draft=True`)**, never auto-merging to `main`.

### 0:45–1:15 — Flagsmith Integration (`/flagsmith` & `:3001`)
- **Screen:** Flagsmith Command Center (`http://localhost:3000/flagsmith`) + NovaShop (`http://localhost:3001`).
- **Talking Points:**
  - *"ReleaseGuard connects directly to Flagsmith Cloud Project `#56055` using Management v2 versioning and Edge evaluation. Here are our 10 provisioned flags—including multivariate `checkout_experience_v2` (`#304842`) and kill switches—plus our trait targeting segments."*
  - Show the **Identity Variation Explainer** evaluating live against Flagsmith Edge, and switch briefly to **NovaShop** (`:3001`) where the Flagsmith JS & Python SDKs evaluate real variants for shoppers.

### 1:15–1:45 — Experimentation and Bayesian Decisions (`/experiments`)
- **Screen:** Bayesian Experimentation (`http://localhost:3000/experiments`).
- **Talking Points:**
  - *"Notice the `Simulated` badge: NovaShop generates deterministic synthetic shopper cohorts (`synthetic_test_traffic: True`) that flow through our real `/api/v1/events/ingest` pipeline and pure-Python Beta-Binomial engine (`50,000` Monte Carlo draws)."*
  - Show the three deterministic safety states:
    1. **Insufficient Data (`25/arm < 100`)**: Blocks promotion (`409 EXPERIMENT_NOT_READY`).
    2. **Guardrail Breach (`payment_failed` spike)**: Recommends rollback and blocks promotion (`409 GUARDRAIL_BREACHED`).
    3. **Winning Variant (`120/arm`, `P > 0.999`, expected loss `< 0.005`)**: Unlocks `202 Accepted` promotion request.

### 1:45–2:15 — Human Approval (`/approvals`)
- **Screen:** Category C Human Approvals (`http://localhost:3000/approvals`).
- **Talking Points:**
  - *"Promoting an experiment winner or touching production is always `PolicyCategory.C`. The operator who requested promotion cannot self-approve (`requested_by_id != decided_by_id`)."*
  - Show the independent reviewer approving the `SHA-256` hash-bound, single-use request (and note replay returns `409 Conflict`).

### 2:15–2:40 — Progressive Delivery (`/progressive-delivery`)
- **Screen:** Progressive Delivery (`http://localhost:3000/progressive-delivery`).
- **Talking Points:**
  - *"Once approved, `approval_executor` checks for live precondition drift, updates Flagsmith, and calls `verify_live_state()` to read back the live Edge split—while Progressive Delivery tracks staged canary rollouts (`1% -> 5% -> 10% -> 25% -> 50% -> 100%`) with automatic guardrail pausing."*

### 2:40–3:00 — Cleanup Lifecycle (`/releases/[id]` or `/flag-health`)
- **Screen:** Release Report & Flag Cleanup (`http://localhost:3000/releases` / `/flag-health`).
- **Talking Points:**
  - *"Finally, ReleaseGuard doesn't leave stale flags behind. Once a winner is at 100%, it generates a validated AST/TSX Cleanup Draft PR on GitHub (`draft=True`), blocks flag retirement with `409` while code references still exist, and only archives the flag in Flagsmith after independent Category C human approval."*
