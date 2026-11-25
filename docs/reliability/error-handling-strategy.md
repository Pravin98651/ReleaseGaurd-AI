# Error Handling & Diagnostics Strategy (Phase 3 & Phase 7)

## Backend Envelope Standard (`backend/app/api/v1/errors.py`)
Every error response (validation `422`, policy `403`, conflict `409`, external API `502`, database unavailable `503`, timeout `504`, unhandled `500`) emits a uniform scrubbed JSON envelope:

```json
{
  "error": {
    "code": "DATABASE_UNAVAILABLE",
    "message": "Database operation failed; please retry.",
    "details": {},
    "request_id": "req_...",
    "retryable": true,
    "timestamp": "2026-11-26T01:50:00+00:00"
  }
}
```

- **Secret Scrubbing:** All `message` and `details` fields pass through `scrub_secrets()` and `redact_dict()`.
- **Retryability Semantics:** `408`, `429`, `502`, `503`, `504`, `TimeoutError`, and `SQLAlchemyError` set `retryable: true`; validation (`422`), permission (`403`), and state conflicts (`409`) set `retryable: false`.
- **Health Probes Separation (`system.py`):**
  - `GET /health/live`: O(1) process liveness without DB/external calls.
  - `GET /health/ready`: `SELECT 1` DB check returning `200` or `503`.
  - `GET /api/v1/system/health`: Full integration, scheduler, worker, and demo-app diagnostic summary.

## Frontend Resilience (`frontend/lib/api.ts`, `error.tsx`, `global-error.tsx`)
- `requestJson` enforces a `30s` `AbortController` timeout and maps aborts to `ApiError(status=408, code="REQUEST_TIMEOUT", retryable=true)`.
- `401` responses outside `/auth/login` clear stale stored tokens automatically and broadcast `rg:auth-changed`.
- Route-level [error.tsx](file:///c:/Projects/ReleaseGuard%20AI/frontend/app/error.tsx) and root [global-error.tsx](file:///c:/Projects/ReleaseGuard%20AI/frontend/app/global-error.tsx) recover from unexpected runtime exceptions with a retry trigger.
