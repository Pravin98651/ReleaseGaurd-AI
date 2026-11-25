# Stage B — Prioritized Fix Roadmap

## P0 — Critical (Hackathon & Verification Blockers)
1. **Flagsmith Native Experimentation & Metrics Client (`backend/app/integrations/flagsmith_client.py`)**
   - Implement `list_experiment_metrics`, `create_experiment_metric`, `list_experiments`, `create_experiment`, `start_experiment`, `stop_experiment`, `refresh_experiment_results`, `get_experiment_results` matching live Flagsmith v1 Management API `OPTIONS` schemas.
2. **Wire Native Flagsmith Experiments + Winner Promotion (`backend/app/agents/tools/experiment_tools.py`, `backend/app/api/v1/experiments.py`, `backend/app/models/experiments.py`)**
   - Persist `flagsmith_experiment_id` & `flagsmith_metric_id` (or in experiment metadata / JSONB so no risky DB reset is needed).
   - On `activate_experiment` / `get_experiment_analysis`, sync with Flagsmith native experiments when `flagsmith_native` is active, retrieve real Flagsmith results/win probability when available, and truthfully indicate `flagsmith_native` vs `tracked_event_fallback`.
   - Add human-approved `POST /api/v1/experiments/{exp_id}/promote-winner` (gated by `PolicyEngine` Category B/C `Approval` -> promotes winning variant on the same Flagsmith flag -> `verify_live_state` readback).
3. **NovaShop Flagsmith SDK Event Tracking (`demo-app/backend/app/flagsmith_service.py`)**
   - Emit `track_exposure_event` and `track_event` (+ `flush_events`) via `flagsmith==6.7.0` alongside ReleaseGuard `/api/v1/events/ingest`, and ensure simulator traffic is clearly labelled as simulated test traffic.

## P1 — High
4. **Fix Backend Test Runner (`backend/requirements.txt`)**
   - Add `pytest-asyncio` to `backend/requirements.txt` and run the full `backend/tests/` suite + `ruff check .`.
5. **Experiments UI Clarity (`frontend/app/experiments/page.tsx`)**
   - Surface Flagsmith native experiment ID/status, native vs fallback attribution, and a **Promote Winner (Requires Approval)** button wired to the approval queue.

## P2 — Medium
6. **External Notification & GitHub Live Verification**
   - Document Slack/Discord as `CONFIGURED_BUT_NOT_VERIFIED` when no webhook URL is supplied; keep truthful delivery status tracking.
