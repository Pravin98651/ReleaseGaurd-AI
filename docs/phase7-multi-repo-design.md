# ReleaseGuard AI — Phase 7: Multi-Repository Release Intelligence Design

## 1. Architecture & Reuse
- **Reuses Existing Foundations**: Orchestrates existing `Repository`, `Release`, `ReleaseOutcome`, `RepositoryIntelligenceProfile`, `PolicyEngine`, `Approval`, and `AuditLog` models without creating a competing release lifecycle or tenancy engine.
- **Organization Hierarchy**: `Organization -> OrganizationRepository -> Repository -> Release`.
- **Dependency-Aware Coordination**: `CrossRepositoryDependencyService` builds directed `RepositoryDependency` graphs, detects cycles, propagates `CrossRepositoryImpactAssessment`, and computes topological release ordering (`ReleaseCoordinationEngine` -> `CoordinatedReleasePlan`).
- **Transparent Health Scoring**: `RepositoryHealthScoringService` computes `RepositoryHealthSnapshot` (`HEALTHY`, `STABLE`, `NEEDS_ATTENTION`, `HIGH_RISK`, `INSUFFICIENT_DATA`) from persisted `ReleaseOutcome` & `RepositoryIntelligenceProfile` metrics, gating unreliable low-sample repos as `INSUFFICIENT_DATA`.
- **Shared Policies**: `OrganizationReleasePolicyService` enforces mandatory organization policies (`is_mandatory=True`) that cannot be silently weakened by repository overrides without explicit approved exceptions.
