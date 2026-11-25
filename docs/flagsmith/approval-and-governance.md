# Approval & Change Governance (Phase 8)

All Flagsmith mutations and winner promotions enforce strict PolicyEngine governance:

1. **Category Classification (`policy_engine.py`):**
   - `Category A`: Flag listing, health scanning, Edge identity explanation, native experiment result reads (`GET /flagsmith/command-center`, `/experiments/{id}/analysis`).
   - `Category B`: Creating disabled flags, non-prod dev/staging targeting segments (`POST /api/v1/flagsmith/catalog/provision`, `/targeting/apply`).
   - `Category C (Always Requires Human Approval)`: ANY production flag toggle/rollout/rollback, flag archive/deletion, AND **`promote_experiment_winner`** / **`experiment_promote_winner`**.
2. **Precondition & Atomic Single-Use Approval (`approval_executor.py`):**
   - Binds SHA-256 `action_hash` + `expected_precondition` live snapshot + `expires_at` TTL.
   - Executes conditionally (`PENDING -> EXECUTING`) and requires `verify_live_state()` readback after Flagsmith mutation before marking `EXECUTED`.
   - Synthetic test traffic (`POST /api/v1/flagsmith/experiments/{id}/run-test-harness`) is strictly rejected with `403` on production experiments.
