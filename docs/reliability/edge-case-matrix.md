# Edge-Case Matrix (Phase 2)

| Domain | Edge Case | Expected Behavior | Handling / Verification |
|---|---|---|---|
| **Auth & RBAC** | Missing, expired, invalid, or revoked Bearer/cookie token | HTTP `401` (`token_version` mismatch invalidates old sessions) | Verified in `test_auth_rbac.py` |
| **Auth & RBAC** | Unregistered or policy-bypassed action (`promote_experiment_winner` / unknown action) | `PolicyEngine.classify_action` enforces `Category C` or fails closed to `DENY` | Fixed & tested in `test_reliability_edge_cases.py` |
| **API Validation** | Malformed UUID, invalid JSON, out-of-range percentages | HTTP `422` structured envelope with `retryable: false` + `timestamp` + `request_id` | Verified in `test_reliability_edge_cases.py` |
| **Release & Approval** | Expired approval execution attempted | Raise `ApprovalExpiredError` (`409 APPROVAL_EXPIRED`); block external mutation | Verified in `test_reliability_edge_cases.py` |
| **Progressive Delivery & Experiments** | Insufficient experiment sample sizes (`0` events / below `min_exposures`) | Report `is_adequate=False`, never emit `Expand Rollout` winner claim | Verified in `test_reliability_edge_cases.py` |
| **Integrations (Webhooks)** | Concurrent duplicate `delivery_id` webhook deliveries | Savepoint (`begin_nested()`) catches `IntegrityError` and returns deduplicated record | Fixed & verified in `test_reliability_edge_cases.py` |
| **Integrations (SSRF)** | `http://`, `localhost`, `127.0.0.1`, `169.254.169.254`, private IPs, off-domain webhook URLs | Reject before saving or dispatching HTTP requests (`DXIntegrationError`) | Fixed & verified in `test_reliability_edge_cases.py` |
| **SSE Streams** | Subscriber disconnect & concurrent workspace broadcasts | Prune idle `_locks`/`_seq_counters`; serialize `_ws_history` via `_ws_lock` & redact payloads | Fixed & verified in `test_reliability_edge_cases.py` |
| **Health Probes** | DB outage vs process crash | `/health/live` returns `200` (O(1) no DB); `/health/ready` returns `503` when DB fails | Fixed & verified in `test_reliability_edge_cases.py` |
| **Frontend Networking** | Backend hang / timeout / React render crash | `requestJson` aborts at `30s` (`REQUEST_TIMEOUT`, `retryable: true`); `error.tsx` & `global-error.tsx` catch UI crashes | Fixed in `frontend/lib/api.ts`, `app/error.tsx`, `app/global-error.tsx` |
