# NovaShop Checkout v1 vs v2 — ReleaseGuard AI Demo App

NovaShop is the target e-commerce checkout application used to demonstrate **ReleaseGuard AI** end-to-end: Flagsmith-gated gradual rollouts (`checkout_v2`), Bayesian A/B conversion & guardrail monitoring, automated regression detection, and Category C human-approved rollbacks.

## Architecture

- **Backend (`demo-app/backend`, port `8001`)**: Python 3.14 + FastAPI integrating the official `flagsmith` Python SDK (`flagsmith.get_identity_flags`) and emitting schema-compliant telemetry events asynchronously via `httpx` to ReleaseGuard AI (`POST /api/v1/events/ingest`).
- **Frontend (`demo-app/frontend`, port `3001`)**: Next.js App Router + TypeScript + Tailwind CSS storefront integrating `@flagsmith/flagsmith` JS SDK against `https://edge.api.flagsmith.com/api/v1`.
- **Tests (`demo-app/tests`)**: Pytest suite covering `/api/health`, `/api/flags`, Checkout v1 vs v2, payment regression demo path, mocked Flagsmith SDK evaluation, simulator batching, and exact `/api/v1/events/ingest` payload schema compliance.

## Checkout Experiences

| Version | Flag Variation | Flow | Notes |
| :--- | :--- | :--- | :--- |
| **Checkout v1** | `control` / `v1` | Classic 3-Step (`Shipping -> Payment -> Review`) | Deterministic fallback when `FLAGSMITH_SERVER_ENV_KEY` is absent. Supports `NOVA10` (10% off). |
| **Checkout v2** | `treatment` / `v2` | Express 1-Page + Instant Wallet + Promos | Supports `NOVA10` (10%) and `EXPRESS15` (15%). Triggers `ERR_V2_PROMO_WALLET_GATEWAY_502` on `PROMO-BUG` or `simulate_payment_bug=true`. |

## Backend Endpoints (`http://localhost:8001/api/*`)

- `GET /api/health` -> `{status, flagsmith_connected, environment_name, flag_key, releaseguard_api_url}`
- `GET /api/flags?user_id=...` -> Evaluates `checkout_v2` via Flagsmith SDK (or safe `v1` fallback) and emits `feature_variation_assigned`.
- `POST /api/checkout` -> Evaluates live flag variation, executes Checkout v1 or v2, and emits `feature_variation_assigned`, `checkout_started`, and terminal outcome (`checkout_completed`, `checkout_abandoned`, or `payment_failed`).
- `POST /api/simulate` -> Runs `users_count` (`1..500`) deterministic shopper journeys (`healthy_v2`, `buggy_v2`, `neutral`) and batches events to ReleaseGuard `/api/v1/events/ingest`.
- `GET /api/telemetry/recent` -> Returns recent buffered telemetry events and ReleaseGuard delivery status for the storefront inspector panel.

## Running Locally (Without Docker)

### 1. Start Demo Backend (Port 8001)
```powershell
cd "demo-app/backend"
# Reuse the root backend venv or create a dedicated venv with requirements.txt
..\..\backend\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8001 --reload
```

Optional environment variables (inherited from root `.env` or shell):
- `FLAGSMITH_SERVER_ENV_KEY` — Flagsmith server/edge environment key (leave blank for safe `v1` fallback)
- `FLAGSMITH_EDGE_API_URL` — defaults to `https://edge.api.flagsmith.com/api/v1`
- `RELEASEGUARD_API_URL` — defaults to `http://localhost:8000`
- `RELEASEGUARD_INGEST_KEY` — defaults to `dev-ingest-key-change-me`

### 2. Start Demo Frontend (Port 3001)
```powershell
cd "demo-app/frontend"
npm install
npm run dev
```

Open `http://localhost:3001` in your browser. Use the **Simulator Bar** (`Run 120 Healthy Checkouts` or `Simulate Payment Regression`) to populate live Bayesian experiment metrics inside ReleaseGuard AI (`http://localhost:3000/experiments`).

### 3. Run Demo Test Suite
```powershell
& "c:\Projects\ReleaseGuard AI\backend\.venv\Scripts\pytest.exe" demo-app/tests -v
```

## Sample `checkout-v2` PR Showcase

See [`sample_pr_checkout_v2.diff`](./sample_pr_checkout_v2.diff) for an inspectable unified diff comparing an unflagged Checkout v2 change against a Flagsmith-protected rollout with ReleaseGuard telemetry. You can also generate an isolated branch from that diff when testing ReleaseGuard's PR Intelligence module on GitHub:

```bash
git checkout -b demo/checkout-v2-pr
git apply demo-app/sample_pr_checkout_v2.diff
```
