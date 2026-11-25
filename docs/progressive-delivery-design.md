# ReleaseGuard AI — Progressive Delivery Engine Design

## 1. Existing Feature-Flag Architecture
`FeatureFlag` (`backend/app/models/flags.py`) stores Flagsmith feature keys, `flag_type` (`STANDARD`, `MULTIVARIATE`), `env_states` JSONB snapshots, and `RolloutHistory` audit records. `flag_tools.py` provides 13 tools including `handle_flagsmith_set_gradual_rollout_stage`, `handle_flagsmith_upsert_percentage_segment`, `handle_flagsmith_rollback_flag`, and `handle_flagsmith_verify_live_state`.

## 2. Existing Flagsmith Capabilities and Limitations
`FlagsmithClient` (`backend/app/integrations/flagsmith_client.py`) supports Management API v1 and v2 Feature Versioning, environment lookup, percentage-split segment rules (`PERCENTAGE_SPLIT`), multivariate splits, and live state readback. Flagsmith open-source/standard environments implement percentage rollouts via `PERCENTAGE_SPLIT` segment rules; stable identity-based targeting requires passing an identity key to the Edge/SDK evaluation endpoint.

## 3. Existing Experiment and Metrics Infrastructure
`Experiment` & `ExperimentEvent` (`backend/app/models/experiments.py`) track `checkout_completed`, `payment_failed`, `checkout_abandoned`, `checkout_started`, and `feature_variation_assigned`, alongside `analyze_experiment_events` (`app/analytics/bayesian.py`) and `evaluate_recommendation` (`app/analytics/recommendations.py`).

## 4. Existing Release Orchestration
Phase 1 (`Release`, `ReleaseStage`, `ReleaseOrchestrator`, `ReleaseReadinessService`), Phase 2 (`AgentExecutionService`), and Phase 3 (`IntelligentReleaseRiskService`) are already implemented and tested.

## 5. Existing Approval System
`PolicyEngine` and `ApprovalExecutor` enforce Category A/B/C governance, SHA-256 `action_hash` binding, live drift revalidation, single-use execution locks, and non-self-approval.

## 6. Existing Telemetry Collection
NovaShop emits `ExperimentEvent` telemetry (`POST /api/v1/events/ingest`). Latency/5xx percentiles (`api_error_rate`, `checkout_latency_p95`) are only available when explicitly ingested in event `metadata` or external telemetry; otherwise they are reported as `INSUFFICIENT_DATA` / `UNAVAILABLE`.

## 7. Existing Risk Assessment
`IntelligentReleaseRiskService` evaluates 7 dimensions and produces `ReleaseRiskAssessment` + `RiskMitigation` records that inform initial rollout stage size, observation strictness, and approval requirements.

## 8. Existing Scheduler and Workers
`SchedulerService` (`backend/app/services/scheduler.py`) provides persistent, locked, misfire-recoverable background jobs (`ScheduleJobType.RELEASE_MONITOR`).

## 9. Missing Progressive-Delivery Functionality & Implementation Matrix

| Capability | Classification | Plan |
|---|---|---|
| Flagsmith Management API & percentage segments | Already implemented and verified | Reuse `FlagsmithClient` & `flag_tools` |
| Bayesian Beta-Binomial & `ExperimentEvent` telemetry | Already implemented and verified | Reuse in `MetricCollectionService` & `RolloutDecisionEngine` |
| `PolicyEngine`, `ApprovalExecutor`, `SchedulerService`, `SSEBus` | Already implemented and verified | Reuse without duplicating |
| Release Lifecycle, Command Center, Risk Engine | Requires extension | Link rollout events, agent executions, and risk freshness checks |
| Persistent Rollout Plan & 1%->5%->10%->25%->50%->100% Stage Engine | Missing | Add `ProgressiveRollout` & `ProgressiveRolloutStage` (`0008`) |
| Observation Windows, Guardrails & Consecutive Breaches | Missing | Implement `RolloutGuardrail`, `GuardrailEvaluation`, `RolloutMetricSnapshot` |
| Explainable Decision Engine & Rollback Operations | Missing | Implement `RolloutDecisionEngine` & `RollbackOperation` |
| Progressive Delivery UI (`/progressive-delivery`) | Missing | Build overview, stage visualizer, metrics, guardrails, approvals, rollback |

## 10. Proposed Rollout Architecture
`ProgressiveDeliveryService` coordinates:
- `RolloutPlanService` & `RolloutStageService` (default `1 -> 5 -> 10 -> 25 -> 50 -> 100`, custom stages supported)
- `FeatureFlagController` (Flagsmith readback verification & environment isolation)
- `MetricCollectionService` (collects `ExperimentEvent` rates + metadata latency/errors with freshness & sample counts)
- `GuardrailEvaluationService` (evaluates operators + consecutive breach thresholds)
- `RolloutDecisionEngine` (`ADVANCE_RECOMMENDED`, `CONTINUE_OBSERVATION`, `PAUSE_RECOMMENDED`, `ROLLBACK_RECOMMENDED`, `MANUAL_REVIEW_REQUIRED`, `BLOCKED`, `COMPLETED`)
- `RollbackRecommendationService` & `RolloutOrchestrator` (approval-bound stage activation & rollback with readback verification + restart recovery).

## 11. Rollout & Stage State Machines
- **Rollout Statuses:** `draft`, `validating`, `ready`, `awaiting_approval`, `scheduled`, `initializing`, `rolling_out`, `observing`, `evaluating`, `advance_recommended`, `pause_requested`, `paused`, `rollback_recommended`, `awaiting_rollback_approval`, `rolling_back`, `rolled_back`, `completed`, `failed`, `cancelled`, `blocked`.
- **Stage Statuses:** `pending`, `ready`, `awaiting_approval`, `approved`, `activating`, `active`, `observing`, `evaluating`, `passed`, `failed`, `paused`, `skipped`, `cancelled`.
All transitions validate optimistic `version`, record `ProgressiveRolloutEvent` + `AuditLog`, and publish SSE events.

## 12–15. Progression, Guardrails, Observation & Rollback
- Stages NEVER advance on timer alone; minimum observation time, minimum sample size, metric freshness, guardrail pass, and required approval must all hold.
- Critical guardrail breaches automatically pause progression, preserve flag state (never mutating production without authorization), emit notifications/SSE, and create a `RollbackOperation` recommendation requiring human approval in production.
- Disabling a feature flag does not roll back database schema migrations; rollback limitations are explicitly surfaced.

## 16–20. Database (`0008_progressive_delivery`), APIs, Frontend, Security & Testing
- **Tables (`0008_progressive_delivery.py`):** `progressive_rollouts`, `progressive_rollout_stages`, `rollout_metric_snapshots`, `rollout_guardrails`, `guardrail_evaluations`, `rollout_decisions`, `progressive_rollout_events`, `rollback_operations` (using `progressive_rollout_events` to avoid any collision with existing `rollout_history`).
- **APIs (`/api/v1/rollouts`):** CRUD, `/validate`, `/start`, `/stages`, `/stages/{id}/evaluate`, `/stages/{id}/advance`, `/metrics`, `/guardrails`, `/decisions`, `/timeline`, `/pause`, `/resume`, `/cancel`, `/rollback/recommend`, `/rollback/request`, `/rollback/execute`, `/stream`.
- **Frontend:** `/progressive-delivery` and `/progressive-delivery/[rolloutId]` plus links from `/releases/[id]`.
- **Security:** RBAC (`viewer` read, `engineer` create/validate/pause/resume/request, `release_manager`/`admin` + `PolicyEngine` for prod activation & rollback).
- **Testing:** `backend/tests/test_progressive_delivery.py` & `frontend/__tests__/progressive-delivery.test.tsx`.
