# Hardcoded Data, Mock & Placeholder Report (Stage A Audit)

**Audit Date:** 2026-11-26  
**Scope:** `backend/app/`, `frontend/app/`, `frontend/components/`, `frontend/lib/`, `demo-app/`

## Summary
- **Class A (Legitimate static config):** Enums, risk labels, default rollout stages (`[1, 5, 10, 25, 50, 100]`), guardrail defaults.
- **Class B (Explicit demo fixtures):** `demo-app/backend/app/simulator.py` & `demo-app/frontend/components/SimulatorBar.tsx` (explicit user-triggered traffic generator), `demo-app/sample_pr_checkout_v2.diff`.
- **Class C (Hardcoded application data):** **0** hardcoded dashboard/chart/experiment arrays found in `frontend/` or `backend/app/api/v1/system.py`.
- **Class D (Mocked integrations):** **0** production mock clients active; however, Flagsmith native experiment results retrieval was *omitted* (falling back to local Beta-Binomial even when `analytics_mode == "flagsmith_native"`).
- **Class E (Placeholder / incomplete functionality):**
  1. `backend/app/integrations/flagsmith_client.py`: Only probes `GET /environments/{env_key}/experiments/`; missing native metric creation, native experiment creation/start/stop, and native results retrieval/refresh.
  2. `demo-app/backend/app/flagsmith_service.py` & `demo-app/frontend/lib/flagsmith.ts`: Evaluates flags via Flagsmith SDK, but does not emit `track_exposure_event` / `track_event` / `trackEvent` to Flagsmith's event pipeline.
  3. `backend/app/api/v1/experiments.py`: Missing explicit human-approved winner promotion endpoint that updates the same Flagsmith flag and verifies readback.

## Detailed Findings Table

| File | Symbol / Line | Description | Class | Affects Prod? | Recommended Fix | Status |
|---|---|---|---|---|---|---|
| `backend/app/integrations/flagsmith_client.py` | `probe_experiment_capability` (L1099) | Probes experiments endpoint but lacks metric/experiment CRUD & results APIs | E | Yes | Implement `list/create_experiment_metric`, `list/create/start/stop_experiment`, `refresh/get_experiment_results` | Pending (Stage D) |
| `demo-app/backend/app/flagsmith_service.py` | `evaluate_checkout_flag` (L98) | Calls `get_identity_flags` without `track_exposure_event` / `track_event` | E | Demo/Hackathon | Add `track_exposure_event` & `track_event` + `flush_events` when SDK connected | Pending (Stage D) |
| `demo-app/frontend/components/SimulatorBar.tsx` | `SimulatorBar` (L27) | Generates deterministic traffic; should visibly badge as simulated test traffic | B | No | Ensure explicit "Simulated Test Traffic" badge and Flagsmith SDK event forwarding | Pending (Stage D) |
| `backend/app/agents/gemini_client.py` | `is_configured` (L253) | Docstring mentions injected mock client for unit tests | A | No | None (testability hook only) | Acceptable |
| `backend/app/api/v1/system.py` | `get_dashboard_charts` (L299) | Queries live PostgreSQL rows for Charts A–F | A | No | None | Verified |
