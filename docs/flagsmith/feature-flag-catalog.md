# ReleaseGuard AI & NovaShop Feature Flag Catalog (Phase 2)

Every registered flag controls real executable application code, specifies an explicit per-flag safe fallback default (never a global fallback), defines an owner & review SLA, and never acts as an authorization mechanism (Auth, RBAC, and PolicyEngine remain authoritative).

## A. NovaShop Application & Experimentation Flags

| Flag Key | Type | Safe Default | Variants | Owner | Real Code Integration | Primary Metric / Guardrails |
|---|---|---|---|---|---|---|
| `checkout_v2` | `STANDARD` | `enabled=False, value="v1"` | `v1`, `v2` | `checkout-platform` | `demo-app/backend/app/checkout.py`, `NovaShopStorefront.tsx` | `checkout_completed` / `payment_failed`, `checkout_abandoned` |
| `checkout_experience_v2` | `MULTIVARIATE` | `enabled=False, value="control"` | `control`, `simplified_checkout`, `one_page_checkout` | `checkout-platform` | `demo-app/backend/app/checkout.py`, `CheckoutV1Form.tsx`, `CheckoutV2Express.tsx` | `checkout_completed` / `payment_failed`, `checkout_abandoned` |
| `recommendation_engine_v2` | `MULTIVARIATE` | `enabled=False, value="control"` | `control`, `collaborative_v2`, `personalized_v2` | `discovery-ml` | `demo-app/backend/app/features.py`, `NovaShopStorefront.tsx` | `product_clicked_from_recommendation` / `payment_failed` |
| `smart_product_search` | `MULTIVARIATE` | `enabled=False, value="control"` | `control`, `ranked_v2`, `semantic_v2` | `search-core` | `demo-app/backend/app/features.py`, `NovaShopStorefront.tsx` | `search_result_clicked` / `checkout_abandoned` |
| `new_payment_flow` | `MULTIVARIATE` | `enabled=False, value="control"` | `control`, `express_token_v2` | `payments-core` | `demo-app/backend/app/checkout.py`, `features.py` | `payment_completed` / `payment_failed` |

## B. Operational & Safety Kill Switches

| Flag Key | Type | Safe Default | Owner | Effect When Disabled |
|---|---|---|---|---|
| `enable_ai_recommendations` | `STANDARD` | `enabled=True` | `sre-release-eng` | Immediately disables ML/personalized recommenders -> deterministic popularity sort |
| `enable_payment_v2` | `STANDARD` | `enabled=True` | `payments-core` | Immediately forces classic card authorization gateway |
| `enable_new_search_backend` | `STANDARD` | `enabled=True` | `search-core` | Immediately disables ranked/semantic search -> simple substring search |

## C. ReleaseGuard AI Capability Flags

| Flag Key | Type | Safe Default | Owner | Security Rule |
|---|---|---|---|---|
| `enable_ai_pr_review` | `STANDARD` | `enabled=True` | `releaseguard-ai` | Controls optional Gemini enrichment; never weakens PolicyEngine |
| `enable_progressive_delivery` | `STANDARD` | `enabled=True` | `releaseguard-ai` | Gates progressive rollout workflows; never bypasses Category C approvals |
