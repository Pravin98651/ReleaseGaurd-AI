# ReleaseGuard AI — Installation & Environment Setup Guide

This guide covers setting up **ReleaseGuard AI** locally (using the built-in SQLite fallback) or via Docker Compose with PostgreSQL 16, as well as configuring **Gemini (`google-genai`)**, **GitHub (OAuth / PAT)**, and **Flagsmith (v1 & v2 environments)**.

---

## 1. Prerequisites

| Tool | Required Version | Purpose |
| :--- | :--- | :--- |
| **Python** | 3.14+ (tested on 3.14.3) | ReleaseGuard Backend & NovaShop Demo Backend |
| **Node.js & npm** | Node 22/24+, npm 10+ | ReleaseGuard Frontend & NovaShop Demo Frontend |
| **Git** | 2.40+ | Repository checkout & diff tooling |
| **Docker Desktop** *(optional)* | Compose v2+ | 5-service containerized PostgreSQL stack |

---

## 2. Environment Configuration (`.env`)

Copy `.env.example` to `.env` in the repository root:

```powershell
Copy-Item .env.example .env
```

### Generate Encryption & Signing Secrets

```powershell
python -c "import secrets; from cryptography.fernet import Fernet; print('SECRET_KEY=' + secrets.token_urlsafe(32)); print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
```

Paste `SECRET_KEY` and `ENCRYPTION_KEY` into `.env`.

> **Important:** In `APP_ENV=production`, `ENCRYPTION_KEY` is strictly required and startup fails fast if omitted or if `SECRET_KEY` is left as a placeholder. In `APP_ENV=development`, omitting `ENCRYPTION_KEY` deterministically derives a dev Fernet key from `SECRET_KEY` with a warning.

### Required & Optional Variables

| Variable | Description |
| :--- | :--- |
| `USE_SQLITE_FALLBACK` | Set `true` for local dev/tests without PostgreSQL; `false` for Docker/PostgreSQL |
| `GEMINI_API_KEY` | Google AI Studio / Gemini API key for `google-genai` |
| `GEMINI_MODEL` | Default `gemini-2.5-flash` (editable at runtime in `/settings`; live fallback automatically tries `gemini-3.5-flash-lite` -> `gemini-3.8-flash` -> `gemini-3.1-flash-lite` -> `gemini-2.5-flash-lite` if a model is overloaded or deprecated) |
| `EVENT_INGEST_KEY` | Shared secret sent in `X-Ingest-Key` by `demo-app/backend` (`RELEASEGUARD_INGEST_KEY`); default placeholder fails startup in `APP_ENV=production` |
| `REQUIRE_AUTH` | Require Control Plane sign-in (`false` allowed in dev; enforced `true` when `APP_ENV=production`) |
| `AUTH_SESSION_TTL_SECONDS` | Operator session TTL in seconds (default `28800`) |
| `AUTH_OPERATOR_EMAIL` / `AUTH_OPERATOR_PASSWORD` | Bootstrap operator credentials (optional password enables initial sign-in) |
| `GITHUB_PAT` | *(Optional)* Personal Access Token bootstrapped into encrypted storage on startup |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | *(Optional)* GitHub OAuth App credentials for `/settings` OAuth flow |
| `FLAGSMITH_ORG_TOKEN` | *(Optional)* Flagsmith Organisation API token (`Settings -> API Keys`) |
| `FLAGSMITH_PROJECT_ID` | *(Optional)* Numeric Flagsmith Project ID |
| `FLAGSMITH_DEV_ENV_KEY`, `FLAGSMITH_STAGING_ENV_KEY`, `FLAGSMITH_PROD_ENV_KEY` | *(Optional)* Environment API keys for Management/Edge verification |
| `FLAGSMITH_SERVER_ENV_KEY` & `NEXT_PUBLIC_FLAGSMITH_ENV_KEY` | *(Optional)* Flagsmith environment key for `demo-app` live flag evaluation |

---

## 3. External Integration Setup

### A. Gemini AI (`google-genai`)
1. Create an API key in [Google AI Studio](https://aistudio.google.com/).
2. Set `GEMINI_API_KEY` in `.env`.
3. All Gemini calls use `AutomaticFunctionCallingConfig(disable=True)` so LangGraph validates every tool call via `policy_gate`.

### B. GitHub (PAT or OAuth App)
- **Fastest setup (PAT)**:
  1. Generate a GitHub Personal Access Token (`public_repo`, `read:user`, or `repo` for private repositories).
  2. Either set `GITHUB_PAT` in `.env` or connect it directly inside the UI at `http://localhost:3000/settings` (`POST /api/v1/integrations/github/pat`). Tokens are encrypted with Fernet in `integration_connections` and never returned in API responses.
- **OAuth App (optional)**:
  1. Register a GitHub OAuth App with callback URL `http://localhost:3000/settings?github_callback=1`.
  2. Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` in `.env`.

### C. Flagsmith (Feature Flags & Gradual Rollouts)
1. Create a Flagsmith project (e.g., `NovaShop`) with environments `development`, `staging`, and `production`.
2. Create an Organisation API Key (**Settings -> API Keys**) and note your numeric **Project ID** and each environment's **API Key**.
3. Connect Flagsmith in `.env` or via `http://localhost:3000/settings`.
4. **Feature Versioning v1 & v2**: ReleaseGuard automatically detects `use_v2_feature_versioning` per environment and handles both legacy `/featurestates/` and v2 `/versions/` + `publish/` lifecycles.
5. **Capability Probe**: ReleaseGuard probes `/api/v1/environments/{env_key}/experiments/`. If unavailable (`403`/`404`), it activates `tracked_event_fallback` automatically using real events sent to `/api/v1/events/ingest`.

---

## 4. Running Locally (Without Docker)

### Backend (`http://localhost:8000`)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
$env:USE_SQLITE_FALLBACK="true"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify health: `Invoke-RestMethod http://localhost:8000/health`

### Control Plane Frontend (`http://localhost:3000`)

```powershell
cd frontend
npm install
npm run dev
```

### NovaShop Demo Backend (`http://localhost:8001`) & Frontend (`http://localhost:3001`)

```powershell
# Terminal 3
cd demo-app\backend
..\..\backend\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 4
cd demo-app\frontend
npm install
npm run dev
```

> **Note for local runs outside Docker:** `demo-app/backend/app/config.py` reads `.env` from its own working directory or shell environment variables (`FLAGSMITH_SERVER_ENV_KEY`, `RELEASEGUARD_API_URL=http://localhost:8000`, `RELEASEGUARD_INGEST_KEY=dev-ingest-key-change-me`).

---

## 5. Running with Docker Compose

Ensure Docker Desktop is running, then from the repository root:

```bash
docker compose up --build
```

Services started:
- `postgres` (`5432`) — PostgreSQL 16 with healthcheck and persistent `pgdata` volume
- `backend` (`8000`) — Runs `alembic upgrade head && uvicorn app.main:app` once `postgres` is healthy
- `frontend` (`3000`) — ReleaseGuard Control Plane UI
- `demo-backend` (`8001`) — NovaShop FastAPI backend pointing telemetry to `http://backend:8000`
- `demo-frontend` (`3001`) — NovaShop storefront UI

---

## 6. Running All Verification Suites

```powershell
# 1. ReleaseGuard Backend lint & 76 tests (unit, API, LangGraph supervisor, RBAC, resilience, Checkout v2 E2E)
cd backend
.\.venv\Scripts\ruff.exe check app tests
.\.venv\Scripts\pytest.exe -v

# 2. NovaShop Demo App lint & 12 tests
cd ..\demo-app
..\backend\.venv\Scripts\ruff.exe check backend tests
..\backend\.venv\Scripts\pytest.exe -v tests

# 3. ReleaseGuard Frontend tests, typecheck, lint & production build (15 App Router build routes)
cd ..\frontend
npm test
npx tsc --noEmit
npm run lint
npm run build

# 4. NovaShop Demo Frontend lint & production build
cd ..\demo-app\frontend
npm run lint
npm run build
```
