# Flagsmith Live Experiment & Harness Results (Phase 10)

**Execution Date:** 2026-11-26 (`task-3766`)  
**Target Environment:** Flagsmith Cloud Project `#56055` — `Development` (`#120019`, `use_v2_feature_versioning: true`)

| Metric | Observed Output | Notes |
|---|---|---|
| Live Flagsmith Native Experiments Created | **4 Running** (`#503`, `#504`, `#505`, `#506`) | Linked to `checkout_experience_v2`, `recommendation_engine_v2`, `smart_product_search`, `new_payment_flow` |
| Synthetic Test Harness Batch (`#503`) | `50` identities evaluated, `9` conversions | Explicitly labelled `synthetic_test_traffic=True` (blocked on `production`) |
| Live Flagsmith Native Results Refresh | `HTTP 200 OK` (`metric_id: 484`) | Warehouse reports `is_final: false` (aggregating asynchronously) |
| ReleaseGuard Beta-Binomial Posterior | `prob_trt_wins: 0.85608`, `is_adequate: false` | Refuses to auto-declare winner below `min_exposures: 100` |
| Identity Repeat Consistency Check (`shopper_42`) | `10 / 10` catalog flags, `CONSISTENT: True` | Live Edge `POST /identities/` evaluation verified |
