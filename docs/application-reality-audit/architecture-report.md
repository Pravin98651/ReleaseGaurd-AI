# ReleaseGuard AI — Architecture Report (Stage A Audit)

**Audit Date:** 2026-11-26  
**Repository:** `c:\Projects\ReleaseGuard AI`

## 1. Monorepo Architecture Map

- **Control Plane Frontend (`frontend/`)**: Next.js 16.4 (App Router, React 19, TypeScript, Tailwind CSS 4, Recharts, Lucide Icons). 31 built routes including `/dashboard`, `/releases`, `/releases/[id]`, `/command-center`, `/command-center/[executionId]`, `/risk-intelligence`, `/risk-intelligence/[assessmentId]`, `/progressive-delivery`, `/progressive-delivery/[rolloutId]`, `/intelligence`, `/intelligence/repositories/[repositoryId]`, `/organization`, `/integrations`, `/notifications`, `/schedules`, `/system-health`, `/pull-requests`, `/feature-flags`, `/flag-health`, `/experiments`, `/approvals`, `/audit-logs`, `/metrics`, `/repositories`, `/agent`, `/settings`, `/shared/[token]`.
- **Backend (`backend/`)**: FastAPI + Python 3.14 + SQLAlchemy 2.0 (async `asyncpg` / `aiosqlite` fallback) + Alembic (11 migrations: `0001`–`0011`) + LangGraph `StateGraph` (`SqlAlchemyCheckpointSaver`) + `google-genai` (`AFC=Disabled`).
- **Demo Target (`demo-app/`)**:
  - `demo-app/backend/` (port `8001`): FastAPI + official `flagsmith` Python SDK (`6.7.0`) + `TelemetryEmitter` -> ReleaseGuard `/api/v1/events/ingest` + deterministic checkout simulator (`v1` 3-step vs `v2` 1-page express).
  - `demo-app/frontend/` (port `3001`): Next.js 16.4 + `@flagsmith/flagsmith` (`12.6.0`).
- **Database (`postgres`)**: PostgreSQL 16 Alpine (`5432`), healthy.
- **Background Scheduler**: In-process `SchedulerService` ticking every `10s` inside `backend` (`running: true`, `194` ticks observed).

## 2. Startup & Runtime Topology (`docker-compose.yml`)

1. `postgres` -> healthcheck `pg_isready` -> healthy.
2. `backend` -> runs `alembic upgrade head` on startup, serves Uvicorn on `8000` -> healthy.
3. `frontend` -> Next.js production server on `3000` -> HTTP 200.
4. `demo-backend` -> Uvicorn on `8001` -> healthy (`/api/health` HTTP 200).
5. `demo-frontend` -> Next.js on `3001` -> HTTP 200.
