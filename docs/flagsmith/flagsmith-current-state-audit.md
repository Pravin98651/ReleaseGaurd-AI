# Flagsmith Current-State Audit (Phase 1)

**Audit Date:** 2026-11-26  
**Connected Flagsmith Project:** `#56055` (`Development` `#120019`, `Staging` `#120029`, `Production` `#120020`, all `use_v2_feature_versioning: true`)

## 1. Existing Feature Flags Inventory

| Flag key | Purpose | Project | Environments | Application integration | Default behavior | Variants | Targeting | Tests | Status |
|---|---|---|---|---|---|---|---|---|---|
| `checkout_v2` (ID `303933`) | Switches NovaShop between 3-step v1 checkout and 1-page express v2 checkout | `#56055` | `Development`, `Staging`, `Production` (all currently `enabled=false`, `value=null`) | `demo-app/backend/app/flagsmith_service.py` (`get_identity_flags`), `demo-app/frontend/lib/flagsmith.ts` (`hasFeature`/`getValue`) | Fail-safe `v1` classic checkout (`DefaultFlag(enabled=False, value="v1")`) | **None (`STANDARD` flag, `mv: []`)** | 1 percentage-split segment `rg_rollout_checkout_v2` (ID `1204194`) | `demo-app/tests/test_demo_app.py`, `backend/tests/test_flagsmith_client.py` | **Partial** — real SDK evaluation works, but flag is `STANDARD` (not `MULTIVARIATE` for native Flagsmith experiments) |

## 2. Capabilities Audit & Gaps Identified

1. **CLI Integration (`flagsmith`):** Not currently installed on the host or inside `backend` container.
2. **Single Flag Only:** Only 1 flag (`checkout_v2`, `STANDARD`) exists in Flagsmith Project `#56055`. None of the multi-variant experiments (`recommendation_engine_v2`, `smart_product_search`, `new_payment_flow`) or operational kill switches (`enable_ai_recommendations`, `enable_payment_v2`, `enable_new_search_backend`, `enable_ai_pr_review`, etc.) exist yet.
3. **Experiments Not Linked to Flagsmith Native Experiments:** All `5` rows in PostgreSQL `experiments` table have `flagsmith_experiment_id = null` because `checkout_v2` is `STANDARD` (`mv: []`), preventing native Flagsmith multivariate experimentation on `checkout_v2`.
4. **NovaShop Missing Distinct Implementations for Additional Experiments:** NovaShop currently only implements checkout (`v1` vs `v2`) and `/api/simulate`; it lacks recommendation engine, product search, simplified 2-step checkout, and payment-flow variants + corresponding event tracking.
5. **Identity & Trait Targeting Incomplete:** NovaShop only passes `{"plan": plan}` trait on backend evaluation; rich targeting attributes (`user_type`, `subscription_plan`, `app_version`, `is_internal_tester`, `organization_id`) and an Identity Variation Explainer are missing.
6. **Dedicated `/flagsmith` Command Center Missing:** Operations are spread across `/feature-flags`, `/flag-health`, `/experiments`, `/progressive-delivery`, and `/approvals`, with no unified Flagsmith Command Center (`/flagsmith`) or Lifecycle Owner/Review Catalog.
