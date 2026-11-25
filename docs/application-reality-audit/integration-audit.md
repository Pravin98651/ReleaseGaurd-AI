# Integration Audit (Stage A)

**Audit Date:** 2026-11-26

| Integration | Classification | Live Evidence Observed | Gaps / Limitations |
|---|---|---|---|
| **PostgreSQL 16** | `VERIFIED_WORKING` | Healthy (`0.94ms` latency); 11 Alembic migrations applied; `50` repos, `1` flag, `5` experiments, `2647` events, `15` approvals, `58` audit logs | None |
| **Flagsmith (Project #56055)** | `WORKING_WITH_LIMITATIONS` | Live API `200 OK` on `/projects/56055/environments/` (3 v2 envs), `/projects/56055/features/` (`checkout_v2` ID `303933`), `/environments/.../experiments/` (`200`), `/environments/.../experiment-metrics/` (`200`) | Missing native experiment/metric CRUD, native results retrieval, and SDK `track_event`/`track_exposure_event` calls in `demo-app` |
| **GitHub REST API** | `VERIFIED_WORKING` | `GitHubClient.verify_token()` succeeded live as `Pravin98651`; 50 repos synced | Live PR webhook & check run write not yet exercised against a live test PR |
| **Google Gemini (`gemini-3.5-flash-lite`)** | `VERIFIED_WORKING` | `GeminiClient.generate_content()` returned `OK` with `AFC=Disabled` | None |
| **Slack Webhook** | `CONFIGURED_BUT_NOT_VERIFIED` | Real `httpx.AsyncClient.post()` in `DeveloperNotificationService` | No Slack webhook configured in `.env` / DB |
| **Discord Webhook** | `CONFIGURED_BUT_NOT_VERIFIED` | Real `httpx.AsyncClient.post()` in `DeveloperNotificationService` | No Discord webhook configured in `.env` / DB |
| **Docker Compose Stack** | `VERIFIED_WORKING` | All 5 containers (`postgres`, `backend`, `frontend`, `demo-backend`, `demo-frontend`) running and healthy | None |
