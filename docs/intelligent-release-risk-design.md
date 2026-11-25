# ReleaseGuard AI — Intelligent Release Risk Engine Design

## 1. Existing Risk Scoring Architecture
Currently, ReleaseGuard AI performs PR-level static regex/AST signal extraction (`extract_static_pr_signals` in `backend/app/agents/specialists/pr_agent.py`) combined with optional Gemini interpretation (`analyze_pr_with_static_and_gemini`). It outputs a single `risk_score` (0–100), `risk_level` (`low`, `medium`, `high`, `critical`), `confirmed_risks`, `potential_risks`, and `flag_opportunities` stored on `PullRequest`.

## 2. Existing PR Analysis Workflow
`POST /api/v1/pull-requests/{id}/analyze` or `ReleaseOrchestrator.run_pr_analysis_stage()` retrieves PR files via `GitHubClient.get_pull_request_files()`, runs static pattern checks + Gemini prompt-injection-hardened analysis, persists results on `PullRequest` and `ReleaseArtifact`, and updates `Release.risk_level`.

## 3. Existing Release Risk Fields and APIs
- `PullRequest`: `risk_score`, `risk_level`, `analysis_summary`, `confirmed_risks`, `potential_risks`, `flag_opportunities`.
- `Release`: `risk_level`, `readiness_score`, `readiness_status`, `readiness_summary`.
- `ReleaseReadinessService`: Evaluates 5 readiness checks (`pr_analysis`, `patch_validation`, `flag_readiness`, `experiment_guardrails`, `approval_gate`).

## 4. Existing Repository Analysis Capabilities
`GitHubClient` supports listing repos, branches, PRs, PR files (diffs/additions/deletions), and file contents. No repository-wide dependency graph or historical failure pattern analyzer exists yet.

## 5. Existing Test and Patch Validation Capabilities
`PatchValidator` (`backend/app/services/patch_validator.py`) validates generated `CodeChange` patches in an isolated temp directory (syntax/AST/unit test checks). Repository-wide coverage reports (e.g. `coverage.xml` / `lcov.info`) are not ingested unless explicitly provided; therefore, coverage percentages must be reported as unavailable when absent.

## 6. Available Historical Data
PostgreSQL already stores `releases`, `release_events`, `pull_requests`, `rollout_history`, `experiments`, `experiment_events`, `agent_executions`, and `audit_logs`. These provide real historical outcomes (completed, failed, rolled back, guardrail breaches) without fabricating incidents.

## 7. Existing Feature-Flag Integrations
`FeatureFlag` and `FlagsmithClient` track Flagsmith flags, `env_states`, `flag_type`, and `RolloutHistory` across `development`, `staging`, and `production`.

## 8. Missing Risk Intelligence Capabilities & Implementation Matrix

| Capability | Classification | Plan |
|---|---|---|
| PR static regex/AST + Gemini analysis | Already implemented and verified | Reuse as input signal in `CodeImpactAnalyzer` |
| GitHub PR & file retrieval | Already implemented and verified | Reuse `GitHubClient` with graceful fallback |
| Flagsmith flag & experiment state | Already implemented and verified | Reuse read-only in `FeatureFlagSuitabilityAnalyzer` |
| `ReleaseReadinessService` & `ReleaseOrchestrator` | Requires extension | Link latest `ReleaseRiskAssessment` & surface critical mitigations |
| `AgentExecutionService` (Command Center) | Requires extension | Record observable risk analysis execution & steps |
| 7-Dimension Risk Analyzers & Dependency Graph | Missing | Implement modular analyzers in `app/services/risk_engine/` |
| Configurable Scoring, Overrides & Completeness | Missing | Implement `RiskScoringEngine` + `RiskEngineConfig` |
| Failure Scenarios & Mitigation Plan Tracker | Missing | Implement `RiskScenario` & `RiskMitigation` with verification |
| Persistent Assessment History (`0007`) | Missing | Add 7 tables in `0007_intelligent_release_risk.py` |
| Risk Intelligence UI (`/risk-intelligence`) | Missing | Build overview, detail cockpit, and release/PR links |

## 9. Proposed Risk Engine Architecture
`IntelligentReleaseRiskService` orchestrates 7 independent analyzers + scoring + scenario/mitigation generators:
1. `CodeImpactAnalyzer`
2. `DependencyImpactAnalyzer`
3. `HistoricalFailureAnalyzer`
4. `TestCoverageAnalyzer`
5. `ProductionEnvironmentAnalyzer`
6. `FeatureFlagSuitabilityAnalyzer`
7. `OperationalReadinessAnalyzer`
8. `RiskScoringEngine`
9. `FailureScenarioGenerator`
10. `MitigationPlanGenerator`

## 10. Risk Dimensions and Scoring Methodology
Configurable default weights (summing to 100%):
- `code_impact`: **20%** (technical sensitivity: auth, payment, migrations, security, complexity)
- `dependency_impact`: **15%** (direct/indirect Python/TS import graph, downstream consumers, manifest changes)
- `historical_failures`: **15%** (matching failed/rolled-back releases, guardrail breaches, RolloutHistory rollbacks; reports `INSUFFICIENT_DATA` when <2 records)
- `test_coverage`: **15%** (changed test files, `CodeChange` validation status, missing failure-path tests; never invents coverage %)
- `production_environment`: **15%** (environment criticality, destructive migration patterns, rollback/monitoring readiness)
- `feature_flag_suitability`: **10%** (suitability classification, linked flag verification, non-isolatable schema caveats)
- `operational_readiness`: **10%** (guardrails, approval gate, rollback path verification)

**Weighted Aggregation:** Only evaluated dimensions with sufficient data contribute to `R = sum(w_i * r_i) / sum(w_i)`. `dimensions_evaluated`, `dimensions_unavailable`, `data_completeness`, and `assessment_confidence` are reported separately.
**Thresholds:** `0–19 LOW`, `20–39 MODERATE`, `40–59 ELEVATED`, `60–79 HIGH`, `80–100 CRITICAL`. Critical overrides (e.g., destructive migration without strategy, failed mandatory validation, unresolved critical guardrail breach) enforce minimum `HIGH`/`CRITICAL` classification.

## 11. Evidence Collection Strategy
Every `RiskFinding` links to traceable `RiskEvidence` (`source_type`, `source_reference`, `structured_data`). AI-generated interpretations are explicitly tagged and never overwrite deterministic metrics.

## 12. Historical Analysis Methodology
Queries persisted `releases`, `rollout_history`, `experiments`, and `pull_requests` for matching `repository_id`, `feature_flag_id`, or overlapping file/module keywords. Reports exact sample counts and marks `INSUFFICIENT_DATA` when history is sparse.

## 13. Mitigation Recommendation Methodology
Maps findings directly to actionable `RiskMitigation` items with `priority`, `mitigation_type`, `verification_criteria`, and `status` (`pending`, `accepted`, `rejected`, `completed`). Accepting a mitigation does NOT reduce the risk score; only `/reanalyze` with updated evidence recalculates dimensions.

## 14. Database Changes (`0007_intelligent_release_risk.py`)
Creates 7 tables revising `0006_agent_command_center`:
- `release_risk_assessments`
- `risk_dimension_results`
- `risk_findings`
- `risk_evidence`
- `risk_mitigations`
- `risk_scenarios`
- `risk_engine_configurations` (versioned, audited scoring weights & thresholds)

## 15. API Changes
- `POST/GET /api/v1/risk-assessments`, `GET /{id}`, `GET /{id}/summary`, `POST /{id}/reanalyze`
- `GET /{id}/dimensions`, `/findings`, `/evidence`, `/scenarios`, `/mitigations`
- `POST/PATCH /{id}/mitigations`, `POST /{id}/mitigations/{mitigation_id}/verify`
- `GET /api/v1/repositories/{id}/dependency-graph`, `/historical-risk`
- `GET/PATCH /api/v1/risk-engine/configuration` (Admin/Release Manager audited)

## 16. Frontend Changes
- `/risk-intelligence` overview (risk distribution KPIs, incomplete assessments, unresolved critical findings, pending mitigations, assessment table)
- `/risk-intelligence/[assessmentId]` detail cockpit (dimension breakdown, code impact, dependency hierarchy, historical signals, test gaps, environment/migration risk, flag suitability, failure scenarios, mitigation tracker)
- Links from `/releases/[id]` and `/pull-requests`.

## 17–20. Lifecycle, Command Center, Testing & Limitations
- Integrates with `ReleaseOrchestrator` & `ReleaseReadinessService` (separating risk score, policy blocker, and readiness status).
- Records observable `AgentExecution` + steps + traces in `AgentExecutionService`.
- Tested in `backend/tests/test_risk_engine.py` and `frontend/__tests__/risk-intelligence.test.tsx`.
- **Known Limitations:** Repo-wide line coverage percentages and external CVE vulnerability feeds are marked `UNAVAILABLE` unless external CI/advisory data is supplied; dependency graphs cover Python/JS/TS imports in inspected files and report unresolved/unsupported imports explicitly.
