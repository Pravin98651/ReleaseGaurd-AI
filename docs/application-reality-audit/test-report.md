# Backend, Frontend & Demo-App Test Report (Post-Fix)

**Verification Date:** 2026-11-26

| Suite | Command | Result |
|---|---|---|
| **Backend Pytest** | `docker run --rm -v "${PWD}/backend:/app" releaseguardai-backend python -m pytest -q` | **PASS — 108 passed** (`106.09s`) |
| **Demo-App Pytest** | `docker run --rm -e PYTHONPATH=/app/backend -v "${PWD}/demo-app:/app" releaseguardai-demo-backend python -m pytest -q /app/tests` | **PASS — 12 passed** (`1.33s`) |
| **Frontend TypeScript** | `cd frontend && npx tsc --noEmit` | **PASS — 0 errors** |
| **Frontend ESLint** | `cd frontend && npm run lint` | **PASS — 0 errors** |
| **Frontend Vitest** | `cd frontend && npx vitest run` | **PASS — 9 files, 24 tests passed** |
| **Frontend Next Build** | `docker compose up -d --build frontend` | **PASS — 31/31 routes built** |
| **Live HTTP Checks** | `http://localhost:3000/experiments`, `/dashboard`, `3001`, `8001/api/health`, `8000/api/v1/system/health` | **PASS — all HTTP 200** |
