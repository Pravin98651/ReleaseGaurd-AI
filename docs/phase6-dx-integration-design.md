# ReleaseGuard AI — Phase 6: Developer Experience Integration Design

## 1. Existing Infrastructure Reused
- **GitHub Client (`backend/app/integrations/github_client.py`)**: Extended with GitHub Check Runs (`POST/PATCH /repos/{owner}/{repo}/check-runs`), PR Issue Comments (`POST/PATCH /repos/{owner}/{repo}/issues/comments`), and GitHub App installation token helpers. Never exposes PR merge APIs.
- **Notifications (`backend/app/services/notification_service.py`)**: Reused for workspace notifications, preferences, and SSE broadcast; extended with `DeveloperNotificationService` and `NotificationDelivery` for Slack & Discord delivery tracking.
- **Settings & Secrets (`backend/app/models/identity.py`, `backend/app/core/security.py`)**: Reuses `IntegrationConnection` and Fernet `encrypt_token`/`decrypt_token` so tokens and webhook secrets are never exposed to the frontend.
- **Release Readiness & Risk (`ReleaseReadinessService`, `ReleaseRiskService`, `HistoricalReleaseAnalyticsService`)**: Authoritative source for GitHub checks, PR comments, and `ReleaseReadinessReport`.

## 2. New Database Models (`0010_dx_integration`)
1. `github_installations`: GitHub App installations, repository mappings, permissions, and encrypted token/webhook metadata.
2. `github_webhook_events`: Raw-body HMAC-verified webhook deliveries with `delivery_id` idempotency, retry count, dead-letter state, and correlation ID.
3. `github_check_runs`: Commit-SHA-scoped GitHub Check Runs (`QUEUED`, `IN_PROGRESS`, `COMPLETED` -> `SUCCESS`/`FAILURE`/`NEUTRAL`/`CANCELLED`) with `is_superseded` protection against stale runs.
4. `pull_request_analysis_comments`: Stable marker-identified PR comments (`<!-- releaseguard-ai:pr-comment -->`) updated in place rather than duplicated.
5. `notification_deliveries`: Slack & Discord delivery log with dedupe keys, retry counts, HTTP status, and error tracking.
6. `release_readiness_reports`: Authoritative persisted readiness reports (JSON + Markdown) backed by `ReleaseReadinessService`.
7. `shareable_reports` & `share_link_access_logs`: Cryptographic SHA-256 token-hashed share links (`secrets.token_urlsafe(32)`), expiration, immediate revocation, and access audit logs.

## 3. Security & Governance Guarantees
- Webhook endpoint (`POST /api/v1/webhooks/github`) verifies `X-Hub-Signature-256` against the raw request body using `hmac.compare_digest` and rejects invalid/missing signatures with HTTP 401.
- Shareable reports exclude credentials, internal agent traces, and raw diffs; revoked/expired tokens return HTTP 410/404 immediately.
- GitHub status checks integrate with `PolicyEngine` (`informational`, `warn_medium`, `require_remediation_high`, `block_critical`) and never bypass Category C approvals.
