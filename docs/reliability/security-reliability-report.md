# Security & Failure-Safety Audit Report (Phase 11)

| Control | Verification Status | Evidence |
|---|---|---|
| **Session Auth & RBAC** | Verified | HMAC-SHA256 tokens, PBKDF2-SHA256 operator hashes, `token_version` revocation, production startup fail-closed on weak secrets. |
| **Policy Gate & Human Approval** | Verified | `promote_experiment_winner` added to `ALWAYS_CATEGORY_C_ACTIONS`; unknown actions fail closed to `DENY`; expired approvals raise `ApprovalExpiredError`. |
| **SSRF Protection on Webhooks** | Hardened | `_validate_webhook_url` enforces `https://`, rejects `localhost`, `127.0.0.1`, `169.254.169.254`, private IPs, and non-Slack/Discord domains. |
| **Prompt Injection Defense** | Verified | `GeminiClient` wraps external GitHub/Flagsmith text in `<untrusted_external_data>` XML blocks and enforces `AFC=Disabled`. |
| **Secret Safety** | Verified | Tokens encrypted at rest via Fernet; logs, errors, Readiness Reports, and SSE workspace payloads pass through `scrub_secrets` / `redact_dict`. |
| **Webhook Signature & Replay** | Verified | HMAC-SHA256 `hmac.compare_digest` verification + `delivery_id` unique constraint & concurrent savepoint deduplication. |
| **Patch Validator Isolation** | Verified | Validates diffs inside temp directories with AST/path checks and sanitized subprocess env. |
