# ReleaseGuard AI — Phase 5: Release Intelligence and Learning Engine Design

## 1. Existing Historical Data Sources

The ReleaseGuard AI database (`0008_progressive_delivery`) already persists rich operational and lifecycle evidence across PostgreSQL 16 (and SQLite fallback for tests):

- **Releases & Lifecycle (`releases`, `release_stages`, `release_events`, `release_artifacts`, `release_checks`)**: Release keys, types, target environments, status transitions, stage durations, readiness scores, verification timestamps, and artifact summaries.
- **Pull Requests & Code Changes (`repositories`, `pull_requests`, `code_changes`)**: Changed file lists, risk scores/levels, confirmed vs. potential risks, generated Flagsmith patches, and isolated patch validation reports (`validation_report`).
- **Risk Engine (`release_risk_assessments`, `risk_dimension_results`, `risk_findings`, `risk_evidence`, `risk_mitigations`, `risk_scenarios`)**: 6-dimension risk scores, dependency graphs, historical failure analyzer placeholders, and mitigation statuses.
- **Feature Flags & Rollouts (`feature_flags`, `rollout_history`, `progressive_rollouts`, `progressive_rollout_stages`, `rollout_metric_snapshots`, `rollout_guardrails`, `guardrail_evaluations`, `rollout_decisions`, `progressive_rollout_events`, `rollback_operations`)**: Multi-environment flag state, rollout percentages, guardrail breach streaks, rollback reasons, and post-rollback verification.
- **Experiments & Telemetry (`experiments`, `experiment_events`)**: Conversion/error/latency events (`checkout_completed`, `payment_failed`, `checkout_abandoned`), Bayesian posteriors, and release recommendations.
- **Agent Command Center (`agent_executions`, `agent_execution_steps`, `agent_tool_calls`, `agent_decisions`, `agent_sessions`, `agent_tasks`)**: Tool calls, AI recommendations (`AgentDecision`), model names, and decision statuses.
- **Governance & Operations (`approvals`, `audit_logs`, `scheduled_jobs`, `job_executions`, `notifications`)**: Approval latency, actor decisions, and scheduled job history.

## 2. Existing Release Analytics

`backend/app/services/analytics_queries.py` and `backend/app/services/metrics_service.py` provide dashboard KPIs, PR risk charts, and experiment conversion charts. However, they do **not** compute longitudinal DORA-style release engineering metrics (`deployment_frequency`, `change_failure_rate`, `mean_time_to_recovery`, `lead_time_for_changes`, `rollback_rate`, `approval_latency`, `rollout_stability`) with explicit denominators, time-period comparisons, or data-quality flags.

## 3. Existing Risk Assessment Data

`ReleaseRiskAssessment` (`backend/app/models/risk_engine.py`) evaluates 6 dimensions (`code_impact`, `dependency_impact`, `historical_failures`, `test_coverage`, `environment_risk`, `flag_suitability`). Currently `HistoricalFailureAnalyzer` only queries basic `RolloutHistory`/`PullRequest` counts and has no normalized `ReleaseOutcome`, `RepositoryIntelligenceProfile`, or `ReleaseFailurePattern` backing it.

## 4. Existing Recommendation Records

`AgentDecision` (`backend/app/models/command_center.py`) and `RiskMitigation` (`backend/app/models/risk_engine.py`) persist recommendations, supporting evidence, and acceptance/implementation statuses (`AgentDecisionStatus`, `MitigationStatus`). Neither currently tracks post-release **outcome verification** (`VERIFIED_BENEFIT`, `OBSERVED_POSITIVE_OUTCOME`, `NO_MEASURABLE_IMPACT`, `NEGATIVE_OUTCOME`, `INCONCLUSIVE`, `NOT_EVALUABLE`).

## 5. Existing Release Outcome Records

`Release.status` (`completed`, `failed`, `rolled_back`, `cancelled`) and `RolloutHistory` exist, but there is no normalized `ReleaseOutcome` record capturing canonical failure categorization, recovery duration, verification status, or evidence quality classification (`VERIFIED`, `OBSERVED`, `HUMAN_REPORTED`, `AI_INFERRED`, `SIMULATED`, `UNKNOWN`).

## 6. Available Incident and Rollback Evidence

`RollbackOperation` (Phase 4) and `RolloutHistory` (Phase 1/Core) capture rollback triggers, reasons, previous/requested configs, and verification results, while `IncidentInvestigateResponse` correlates `ExperimentEvent` error spikes. There is no longitudinal `RollbackIntelligenceService` aggregating rollback causes, detection latency, or guardrail effectiveness.

## 7. Repository-Specific Information

`Repository` stores GitHub metadata (`full_name`, `default_branch`, `last_synced_at`), and `PullRequest`/`CodeChange` store file diffs and paths. There is no persistent `RepositoryIntelligenceProfile` aggregating per-repo failure rates, high-risk directories/modules, recurring dependencies, or tailored recommendations.

## 8. Data-Quality Gaps

1. Releases may lack `started_at`/`completed_at` timestamps or explicit `ReleaseOutcome` normalization.
2. Pre-deployment manual cancellations must be excluded from production `change_failure_rate` denominators.
3. Dependency metadata is extracted from PR diffs (`DependencyImpactAnalyzer`) rather than a dedicated SBOM/lockfile scanner; vulnerability claims are omitted unless backed by a verified feed.
4. `AgentDecision` records often lack downstream outcome labels (`NOT_EVALUABLE` / `INCONCLUSIVE` when outcomes are missing).
5. Small sample sizes in early workspaces must prevent premature pattern claims.

## 9. Missing Historical Intelligence Capabilities

- Normalized `ReleaseOutcome` & `IntelligenceAnalysisRun` tracking.
- Statistical `FailurePatternAnalyzer` with minimum sample safeguards and Wilson/uncertainty bounds.
- `RepositoryIntelligenceService` & `DependencyIntelligenceService`.
- `RollbackIntelligenceService` with cause taxonomy & manual correction history.
- `RecommendationEvaluationService` distinguishing acceptance from verified outcome.
- `ReleaseLearningEngine` & `ReleasePolicyIntelligenceService` with human review/approval workflow (`PENDING_REVIEW -> ACCEPTED/MODIFIED/REJECTED -> APPROVED -> IMPLEMENTED -> VERIFIED`).
- Evidence-linked `ReleaseKnowledgeBaseService` + Gemini-assisted natural-language query endpoint.
- `/intelligence` & `/intelligence/repositories/[repositoryId]` frontend center.

## 10. Proposed Architecture

```text
Authoritative Records (Release, PR, RiskAssessment, ProgressiveRollout, RollbackOperation, AgentDecision)
   │
   ▼
ReleaseOutcomeNormalizer  ──► ReleaseOutcome (with EvidenceQuality + DataQuality flags)
   │
   ├──► HistoricalReleaseAnalyticsService (DORA + Rollout KPIs, Trends, Denominators)
   ├──► FailurePatternAnalyzer (Recurring categories, modules, dependencies, min-sample gates)
   ├──► RepositoryIntelligenceService & DependencyIntelligenceService
   ├──► RollbackIntelligenceService (Cause taxonomy, detection/recovery latency, manual overrides)
   ├──► RecommendationEvaluationService (AgentDecision + RiskMitigation -> RecommendationOutcome)
   │
   ▼
ReleaseLearningEngine & ReleasePolicyIntelligenceService
   ├──► ReleaseInsight & PolicyRecommendation (Human-governed review & Category C approval)
   ├──► Feedback into ReleaseRiskAssessment (versioned historical factor) & ProgressiveDelivery
   └──► ReleaseKnowledgeBaseService (Structured retrieval + Gemini grounded synthesis)
```

## 11. Learning and Analysis Methodology

- **Level 1 (Deterministic)**: Reproducible SQL aggregations with explicit numerators, denominators, and exclusion rules (e.g. `draft`/`cancelled` before execution excluded from `change_failure_rate`).
- **Level 2 (Statistical Safeguards)**: Minimum sample thresholds (`min_sample_size >= 3` for established patterns; `< 3` labeled `exploratory` / `insufficient_data`), Wilson 95% confidence intervals for rates, and explicit separation of correlation vs. causation.
- **Level 3 (Gemini Synthesis)**: Bounded structured evidence envelopes passed to existing `gemini_client`; outputs validated against persisted IDs so AI cannot fabricate counts or release keys.
- **Level 4 (Predictive)**: Explicitly gated behind sufficient labeled outcomes (`>= 20` verified outcomes); reports `unavailable` with data-sufficiency reason when below threshold.

## 12. Recommendation Accuracy Methodology

Tracks `RecommendationOutcome` separately from `AgentDecisionStatus`:
- **Acceptance Rate** = `accepted / total_with_human_response`
- **Implementation Rate** = `implemented / accepted`
- **Verification Rate** = `verified / implemented`
- **Positive Outcome Rate** = `(VERIFIED_BENEFIT + OBSERVED_POSITIVE_OUTCOME) / evaluable_recommendations` (excluding `INCONCLUSIVE` / `NOT_EVALUABLE` from numerator and reporting coverage separately).

## 13. Database Changes (`0009_release_intelligence`)

Seven new tables in `backend/app/models/intelligence.py`:
1. `release_outcomes`
2. `release_failure_patterns`
3. `repository_intelligence_profiles`
4. `recommendation_outcomes`
5. `release_insights`
6. `policy_recommendations`
7. `intelligence_analysis_runs`

## 14. API Changes

Mounted under `/api/v1/intelligence` (`backend/app/api/v1/intelligence.py`):
- Historical analytics (`/overview`, `/release-performance`, `/trends`, `/failure-rates`, `/rollback-analysis`)
- Failure patterns (`/failure-patterns`, `/failure-patterns/{pattern_id}`, `/failure-patterns/analyze`)
- Repository & dependency intelligence (`/repositories`, `/repositories/{id}`, `/repositories/{id}/patterns`, `/repositories/{id}/recommendations`, `/dependencies`, `/dependencies/{name}`)
- Rollback intelligence (`/rollback-causes`, `/rollback-causes/{category}`)
- Recommendation evaluation (`/recommendations`, `/recommendations/accuracy`, `/recommendations/{id}`, `/recommendations/{id}/evaluate`)
- Policy recommendations (`/policy-recommendations`, `/policy-recommendations/{id}`, `/review`, `/approve`, `/implement`, `/verify`)
- Knowledge base (`/query`, `/knowledge`)
- Analysis runs (`/analyze`, `/analysis-runs`, `/analysis-runs/{run_id}`)

## 15. Frontend Architecture

- `/intelligence` (`frontend/app/intelligence/page.tsx`): Tabbed Release Intelligence Center covering Overview KPIs & Data Quality, Historical Performance, Failure Patterns, Rollback Intelligence, Dependency Intelligence, Agent Recommendation Accuracy, Policy Recommendations (review/approve/implement/verify), and AI Knowledge Assistant.
- `/intelligence/repositories/[repositoryId]` (`frontend/app/intelligence/repositories/[repositoryId]/page.tsx`): Dedicated Repository Intelligence profile page.

## 16. Privacy and Access-Control Requirements

- All endpoints require authenticated workspace user (`get_current_user`); mutating/approval/analysis actions enforce `ADMIN`/`OPERATOR` RBAC and `AuditLog` entries.
- Policy implementation (`implement` / `approve`) requires explicit human authorization (`Approval` Category C for production-affecting policy changes) and never silently alters active production guardrails or risk weights.
- Secrets and raw credentials are scrubbed; cross-workspace leakage is prevented by workspace-scoped queries.

## 17. Testing Strategy

- Unit & statistical tests: KPI denominators, zero-failure & small-sample edge cases, Wilson confidence bounds, failure categorization, recommendation outcome classification.
- Integration & E2E tests: Seeded NovaShop releases (successful, failed checkout, dependency regression, rollback, accepted/implemented/verified AI recommendation) -> run `ReleaseLearningEngine` -> verify pattern detection, repo profile, policy recommendation review/approval, and Risk Engine historical factor integration.
- Frontend Vitest tests: Render Intelligence Center tabs, Repository Intelligence page, empty/loading/error states, and policy review controls.

## Capability Matrix

| Capability | Status | Notes |
| :--- | :--- | :--- |
| Release Lifecycle & Stages | Already implemented & verified | `releases`, `release_stages`, `release_events` |
| PR & Code-Change Impact | Already implemented & verified | `pull_requests`, `code_changes`, `CodeImpactAnalyzer` |
| 6-Dimension Risk Engine | Implemented; requires extension | `HistoricalFailureAnalyzer` needs Phase 5 `ReleaseOutcome` & `RepositoryIntelligenceProfile` |
| Progressive Delivery & Rollbacks | Already implemented & verified | `progressive_rollouts`, `guardrail_evaluations`, `rollback_operations` |
| Agent Decisions & Tool Traces | Already implemented & verified | `agent_decisions`, `agent_tool_calls` |
| Normalized Release Outcomes | Missing -> Phase 5 | `ReleaseOutcome` + data-quality checks |
| Failure Pattern & Rollback Cause Analytics | Missing -> Phase 5 | `FailurePatternAnalyzer` + `RollbackIntelligenceService` |
| Repository & Dependency Intelligence | Missing -> Phase 5 | `RepositoryIntelligenceService` + `DependencyIntelligenceService` |
| Recommendation Outcome Evaluation | Missing -> Phase 5 | `RecommendationEvaluationService` + `RecommendationOutcome` |
| Policy Recommendations & Review | Missing -> Phase 5 | `ReleasePolicyIntelligenceService` + `PolicyRecommendation` |
| Knowledge Base & Gemini Assistant | Missing -> Phase 5 | `ReleaseKnowledgeBaseService` |
| Predictive ML Risk Calibration | Unavailable until >=20 verified outcomes | Explicitly gated by data-sufficiency check |
