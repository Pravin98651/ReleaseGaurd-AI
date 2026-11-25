# Regression Test Report (Phase 9)

**Execution Date:** 2026-11-26  
**Status:** All verified automated test suites passed after the final code edit.

| Suite | Command | Result | Coverage Notes |
|---|---|---|---|
| **Backend Full Suite** | `docker run --rm -v "${PWD}/backend:/app" releaseguardai-backend python -m pytest -q` | **121 passed** (`113.39s`) | Includes 13 new tests in `tests/test_reliability_edge_cases.py` covering SSRF blocking, concurrent duplicate webhooks, `PolicyEngine` fail-closed/winner promotion, expired approvals, SSE lock pruning + workspace redaction, insufficient experiment data, `/health/live`, `/health/ready`, and structured `422` envelopes |
| **Demo-App Pytest** | `docker run --rm -e PYTHONPATH=/app/backend -v "${PWD}/demo-app:/app" releaseguardai-demo-backend python -m pytest -q /app/tests` | **12 passed** (`1.33s`) | Covers Flagsmith SDK evaluation, v1/v2 checkout, payment bug regression, and telemetry |
| **Frontend Vitest** | `cd frontend && npx vitest run` | **9 files, 24 tests passed** | Covers control-plane components, SSE fallback polling, dashboard operations, intelligence, progressive delivery, command center, organization, integrations, releases |
| **Frontend TypeScript** | `cd frontend && npx tsc --noEmit` | **0 errors** | Full TypeScript strict check |
| **Frontend ESLint** | `cd frontend && npm run lint` | **0 errors** | Next.js ESLint config |
| **Frontend Production Build** | `docker compose up -d --build frontend` (`next build`) | **31/31 routes built** | All static and dynamic routes |
| **Load Benchmark** | Live `httpx.AsyncClient` against `http://backend:8000` (`1/10/25/50` concurrency) | **860/860 OK (`0.0%` errors)** | Measured RPS `18.1 -> 76.2`, p50 `4.58ms -> 307.03ms`, p95 `72.38ms -> 1665.15ms` |
