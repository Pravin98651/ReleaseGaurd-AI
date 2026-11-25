# Frontend & Backend Audit Summary (Stage A)

## Frontend Audit (`frontend/`)
- **Total Built Routes:** 31 (`next build` static + PPR routes).
- **API Client (`frontend/lib/api.ts`):** 2,164 lines, all routes wired to `/api/v1/...` via `fetch`. Zero hardcoded dashboard/chart/experiment arrays.
- **UI & Theme:** Duplicate `AppShell` wrapper bug in `/notifications`, `/schedules`, `/system-health` fixed; consistent Dark & Light theme with `rg_theme` persistence and contrast rules in `globals.css`.
- **Tests & Checks:** `tsc --noEmit` (0 errors), `eslint` (0 errors), `vitest run` (9 files, 24 tests passed), live HTTP `200` on `/dashboard`, `/notifications`, `/schedules`, `/system-health`.
- **Gap:** `/experiments` UI needs clear display of Flagsmith native experiment linkage, native results/win-probability vs ReleaseGuard Bayesian fallback, and a human-approved **Promote Winner** action.

## Backend Audit (`backend/`)
- **Models & Migrations:** Alembic `0001`–`0011` applied cleanly on PostgreSQL 16.
- **Safety & Governance:** `PolicyEngine` enforces Category A / B / C / FORBIDDEN; `Approval` uses SHA-256 `action_hash` + atomic status lock + live precondition check; `GeminiClient` enforces `AFC=Disabled` and XML untrusted data sandboxing.
- **Gap 1 (P1):** `backend/requirements.txt` missing `pytest-asyncio`, blocking in-container `pytest`.
- **Gap 2 (P0):** Flagsmith native experiment & metric orchestration + native results retrieval + winner promotion flow incomplete for Track 02.
