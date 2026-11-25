# Multivariate Experiment Implementation (Phases 5 & 6)

All four experiments run distinct backend/frontend implementations in NovaShop (`demo-app/`), use real Flagsmith `MULTIVARIATE` flags + `experiment-metrics/` (`definition={"version": 1, "event": ...}`), and are active in Flagsmith Project `#56055` (`Development` `#120019`):

| Experiment Name | Flagsmith Flag | Live Flagsmith Exp ID | Variants | Primary Metric | Guardrails |
|---|---|---|---|---|---|
| **NovaShop Checkout Experience V2 Test** | `checkout_experience_v2` (#304842) | **#503** (`running`) | `control` (34%), `simplified_checkout` (33%), `one_page_checkout` (33%) | `checkout_completed` (`metric_id: 484`) | `payment_failed`, `checkout_abandoned` |
| **NovaShop Recommendation Engine V2 Test** | `recommendation_engine_v2` (#304844) | **#504** (`running`) | `control` (popularity), `collaborative_v2` (bundle affinity), `personalized_v2` (trait personalization) | `product_clicked_from_recommendation` | `payment_failed` |
| **NovaShop Smart Product Search Test** | `smart_product_search` (#304845) | **#505** (`running`) | `control` (substring), `ranked_v2` (weighted multi-field), `semantic_v2` (synonym/intent tags) | `search_result_clicked` | `checkout_abandoned` |
| **NovaShop Express Payment Flow Test** | `new_payment_flow` (#304846) | **#506** (`running`) | `control` (standard gateway), `express_token_v2` (single-token wallet) | `payment_completed` | `payment_failed` |

- **Exposure & Conversion Tracking:** `demo-app/backend/app/main.py` calls Flagsmith Python SDK v6.7.0 `track_exposure_event`, `track_event`, and `flush_events`, paired with ReleaseGuard `/api/v1/events/ingest`.
- **Safe Winner Policy:** When Flagsmith warehouse aggregation (`is_final=false`) or Beta-Binomial exposure count (`< min_exposures`) is insufficient, ReleaseGuard reports insufficient evidence and refuses to invent a winner.
