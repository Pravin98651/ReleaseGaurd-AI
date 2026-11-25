# ReleaseGuard AI

**Your Autonomous AI Release Engineer — Ship Faster. Release Safer. Let AI Handle the Complexity.**

ReleaseGuard AI unifies **GitHub Pull Request Intelligence**, **Flagsmith Feature Flag Governance**, and **Pure-Python Bayesian Experimentation & Rollout Decisioning** behind one central Gemini-powered LangGraph supervisor with fail-safe Human-in-the-Loop (HITL) controls.

> Built for the **Flagsmith Experimentation Hackathon (Track 02 – AI Agents and Feature Flags)**.

---

## The Problem

Shipping new code directly to production exposes engineering teams to silent regressions, manual feature flag boilerplate, ad-hoc gradual rollouts, disconnected A/B analytics, orphaned flags, and risky late-night rollbacks. Switching between GitHub, Flagsmith, analytics tools, and IDE scripts slows releases and increases deployment risk.

ReleaseGuard AI connects the entire pre-release and post-release lifecycle in a single governed control plane:

1. **Module 1 — AI PR Intelligence (`pr_agent`)**: Connects GitHub repositories (OAuth or least-privilege PAT), analyzes pull requests into evidence-backed `confirmed_risks` vs `potential_risks`, recommends feature flag gates, generates Flagsmith-guarded code patches + unit tests, validates patches in an isolated temporary directory (`ast.parse`, path/env guards, `ruff`, `pytest`), and opens GitHub **Draft PRs only** (`draft: true` — never merges).
2. **Module 2 — Feature Flag Governance (`flag_agent`)**: Syncs Flagsmith projects, environments (`use_v2_feature_versioning` v1/v2 dual adapter), multivariate options, and `PERCENTAGE_SPLIT` rollout segments; translates natural-language commands into validated Flagsmith mutations; scans repositories for SDK usage; and computes explainable 7-detector **Flag Health** scores.
3. **Module 3 — Experimentation & Bayesian Release Decisioning (`experiment_agent`)**: Plans A/B experiments from natural language, auto-probes Flagsmith native experimentation (`flagsmith_native`) with automatic fallback to ingested application events (`tracked_event_fallback`), runs deterministic pure-Python **Beta-Binomial Monte Carlo** Bayesian posteriors (`50,000` samples) + `payment_failed` guardrails, outputs deterministic 5-state rollout recommendations (`Recommend Rollback`, `Reduce Rollout`, `Continue Monitoring`, `Expand Rollout`, `Maintain Current Rollout`), and correlates incidents against rollout history & PRs with explicit `correlation_not_causation: true`.

---

## Safety & Architecture Invariants

- **Manual Function Calling Only (`AFC = Disabled`) & `thought_signature` Preservation**: Every `google-genai` call explicitly sets `AutomaticFunctionCallingConfig(disable=True)`, retries transient `429`/`503`/timeout errors with exponential backoff (`gemini-3.5-flash-lite` / `gemini-3.8-flash` / `gemini-3.1-flash-lite` / `gemini-2.5-flash-lite` fallback chain), and roundtrips Gemini 3.x `thought_signature` bytes safely through LangGraph checkpoints across multi-turn tool calls.
- **Control Plane Authentication & RBAC**: Supports cookie + Bearer HMAC-SHA256 sessions with PBKDF2-SHA256 operator passwords and GitHub OAuth sign-in, `token_version` revocation on logout, and strict RBAC enforcement (`viewer`, `engineer`/`developer`, `release_manager`, `admin`). In `APP_ENV=production`, `REQUIRE_AUTH=true` and non-default secrets are enforced at startup.
- **LangGraph `StateGraph` + `policy_gate`**: Every proposed tool invocation is schema-validated via Pydantic v2, classified by the Policy Engine into **Category A** (read/analyze), **Category B** (non-prod mutation / draft PR / flag creation, configurable confirmation), **Category C** (all production mutations, rollouts, rollbacks, flag archives — **always** pauses via LangGraph `interrupt()` for single-use human approval), or **FORBIDDEN** (auto-merge PR, auto-delete prod flags — no tool or endpoint exists).
- **Single-Use Atomic Approvals**: Category B/C approvals bind a SHA-256 `action_hash`, TTL (`APPROVAL_TTL_SECONDS`), atomic SQL conditional `pending -> executing` lock, live Flagsmith/GitHub precondition revalidation (`409 Conflict` on drift), and post-mutation live state verification before resuming LangGraph via `Command(resume=...)`.
- **Untrusted Input Isolation & Secret Safety**: GitHub repo/PR metadata and Flagsmith text are wrapped in XML sandbox delimiters (`wrap_untrusted()`) before entering prompts. OAuth/PAT/Org tokens are encrypted at rest with **Fernet** (`AES-128-CBC` + HMAC), masked via `token_hint`, and scrubbed from structured JSON logs and isolated `PatchValidator` subprocess environments.
- **Flagsmith Is Source of Truth**: Live flag state, rollout percentages, and v2 feature versions always reside in Flagsmith; PostgreSQL (or SQLite fallback) stores metadata, approvals, audit logs, rollout history, checkpoints, and telemetry events.

---

## Tech Stack

| Layer | Technologies |
| :--- | :--- |
| **Control Plane Frontend** | Next.js 16.4, React 19, TypeScript, Tailwind CSS 4, Recharts, Lucide Icons (15 App Router routes incl. `/metrics`, `/schedules`, `/notifications`, `/system-health`) |
| **AI & Orchestration Backend** | Python 3.14, FastAPI, LangGraph (`StateGraph` + custom JSON `SqlAlchemyCheckpointSaver`), `google-genai` SDK |
| **Database & Migrations** | PostgreSQL 16 (`asyncpg`) primary + SQLite (`aiosqlite`) local fallback (`USE_SQLITE_FALLBACK=true`), SQLAlchemy 2.0, Alembic |
| **Integrations** | GitHub REST & Git Data APIs (`httpx`), Flagsmith Management & Edge APIs (`httpx`, v1 + v2 versioning adapter) |
| **Statistical Engine** | Pure-Python Beta-Binomial Bayesian Monte Carlo (`random.Random(seed)`), guardrail posteriors, 5-state recommendation engine |
| **Demo Application (`demo-app/`)** | NovaShop Checkout v1 (3-step) vs v2 (1-page express + promo regression toggle), FastAPI + `flagsmith` Python SDK, Next.js + `@flagsmith/flagsmith` JS SDK |

---

## Monorepo Structure

```text
ReleaseGuard AI/
├── backend/                  # FastAPI + LangGraph + Policy Engine + Bayesian Analytics
│   ├── alembic/              # Portable PostgreSQL & SQLite migrations (15 tables)
│   ├── app/
│   │   ├── agents/           # Gemini client (AFC=Disabled), LangGraph supervisor, checkpointer, 3 specialists, 34 tools
│   │   ├── analytics/        # Pure-Python Bayesian, recommendations, 7-detector flag health, incident correlation
│   │   ├── api/v1/           # REST & SSE routers matching all 11 frontend pages + /events/ingest
│   │   ├── core/             # Settings, async SQLAlchemy engine, Fernet security, secret-scrubbing logger
│   │   ├── integrations/     # GitHub REST/Git Data client & Flagsmith v1/v2 Management + Edge client
│   │   ├── models/           # 15 portable SQLAlchemy 2.0 ORM models
│   │   ├── schemas/          # Pydantic v2 request/response + 34 Gemini tool contracts
│   │   └── services/         # PolicyEngine, isolated PatchValidator, SSEBus
│   └── tests/                # 76 unit, integration, resilience, RBAC & Checkout v2 E2E tests
├── frontend/                 # ReleaseGuard AI Next.js Control Plane (port 3000) + Vitest suite
├── demo-app/                 # NovaShop Checkout v1 vs v2 Flagsmith Demo App (ports 8001 & 3001)
├── docs/                     # Architecture, database/policy spec, API contracts, setup, demo & verification reports
├── docker-compose.yml        # 5-service stack: postgres, backend, frontend, demo-backend, demo-frontend
└── .env.example              # Complete environment variable template
```

---

## Quick Start

### Option A: Local Development (No Docker Required, SQLite Fallback)

1. **Configure Environment**
   Copy `.env.example` to `.env` and set `USE_SQLITE_FALLBACK=true`, `GEMINI_API_KEY`, and a 32+ char `SECRET_KEY` (plus optional `GITHUB_PAT`, `FLAGSMITH_ORG_TOKEN`, `FLAGSMITH_PROJECT_ID`, and environment keys).

2. **Start ReleaseGuard Backend (Port 8000)**
   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\pip.exe install -r requirements.txt
   $env:USE_SQLITE_FALLBACK="true"
   .\.venv\Scripts\alembic.exe upgrade head
   .\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

3. **Start ReleaseGuard Control Plane Frontend (Port 3000)**
   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

4. **Start NovaShop Demo App (Ports 8001 & 3001)**
   ```powershell
   # Terminal 3 — Demo Backend
   cd demo-app\backend
   ..\..\backend\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8001 --reload

   # Terminal 4 — Demo Frontend
   cd demo-app\frontend
   npm install
   npm run dev
   ```

5. **Open Control Plane & Demo Storefront**
   - Control Plane: `http://localhost:3000`
   - Backend API Docs: `http://localhost:8000/docs`
   - NovaShop Storefront: `http://localhost:3001`

### Option B: Docker Compose (Full 5-Service Stack with PostgreSQL 16)

```bash
cp .env.example .env
# Populate GEMINI_API_KEY, ENCRYPTION_KEY, SECRET_KEY, and optional GitHub/Flagsmith keys
docker compose up --build
```

For detailed GitHub OAuth/PAT setup, Flagsmith configuration, RBAC authentication, and production environment notes, see [Setup Guide](file:///c:/Projects/ReleaseGuard%20AI/docs/setup.md). For the end-to-end hackathon walkthrough script, see [Demo Workflow Guide](file:///c:/Projects/ReleaseGuard%20AI/docs/demo-workflow.md). For live external verification results and known limitations, see [Verification & Limitations Report](file:///c:/Projects/ReleaseGuard%20AI/docs/verification-and-limitations.md).

---

## Verification & Test Suites

```powershell
# Backend lint & full test suite (84 tests)
cd backend
.\.venv\Scripts\ruff.exe check app tests
.\.venv\Scripts\pytest.exe -v

# Demo app lint & test suite (12 tests)
cd ..\demo-app
..\backend\.venv\Scripts\ruff.exe check backend tests
..\backend\.venv\Scripts\pytest.exe -v tests

# Frontend & demo-frontend tests, typecheck & production builds
cd ..\frontend && npm test && npx tsc --noEmit && npm run lint && npm run build
cd ..\demo-app\frontend && npm run lint && npm run build
```

---

## Known Constraints & Fallback Behavior

- **Flagsmith Experimentation Tier**: Flagsmith's `/api/v1/environments/{env_key}/experiments/` endpoint may require an Enterprise plan or return HTTP `403`/`404`. ReleaseGuard AI probes this automatically (`POST /api/v1/integrations/flagsmith/probe`) and falls back transparently to `tracked_event_fallback`, computing real Bayesian Beta-Binomial statistics over events ingested at `POST /api/v1/events/ingest`.
- **Docker Builds**: Running `docker compose up --build` requires an active Docker daemon; if Docker is unavailable locally, run all 4 application processes directly using `USE_SQLITE_FALLBACK=true`.
- **No Auto-Merge / No Auto-Delete**: ReleaseGuard AI intentionally blocks PR merging and automatic production flag deletion; it only creates verified **Draft PRs** and queues **Category C** human approvals.

## License

MIT — see [LICENSE](file:///c:/Projects/ReleaseGuard%20AI/LICENSE).
