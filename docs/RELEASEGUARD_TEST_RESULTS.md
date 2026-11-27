# ReleaseGuard AI — Automated & Live Test Results

**Audit Date:** `2026-11-27`

## 1. Automated Test Suites

| Test Suite | Scope Type | Command | Total | Passed | Failed | Skipped | Result |
|---|---|---|---:|---:|---:|---:|---|
| **Backend (`pytest`)** | Unit + Local Integration + Policy + Agent + DB | `backend\.venv\Scripts\pytest.exe -q backend\tests` | 172 | 172 | 0 | 0 | **PASS** |
| **Demo App (`pytest`)** | NovaShop Unit + API Integration | `$env:PYTHONPATH="demo-app/backend"; backend\.venv\Scripts\pytest.exe -q demo-app\tests` | 16 | 16 | 0 | 0 | **PASS** |
| **Frontend (`tsc`)** | TypeScript Static Check | `cd frontend && npx tsc --noEmit` | — | — | 0 | — | **PASS** |
| **Frontend (`eslint`)** | ESLint Check | `cd frontend && npm run lint` | — | — | 0 | — | **PASS** |
| **Frontend (`vitest`)** | UI Component & Hook Tests (Mocked API) | `cd frontend && npx vitest run` | 33 | 33 | 0 | 0 | **PASS** |
| **Frontend (`next build`)** | Production Docker Build (`32/32` routes) | `docker compose up -d --build frontend` | 32 routes | 32 | 0 | 0 | **PASS** |

## 2. Live External Integration & E2E Proofs

| Proof / Audit Run | External Services Exercised | Result | Evidence File |
|---|---|---|---|
| **Live E2E System Audit (`live_e2e_audit.py`)** | Docker Compose (`:8000`, `:8001`, `:3000`, `:3001`, Postgres 16) + Flagsmith `#56055` + GitHub + Gemini `3.5-flash-lite` | **PASS** (`23/23` routes `200`, `403` admin self-approval block, `409` drift detection, `11` Edge flag explanations, `6`-step Supervisor completion, `numeric_bayesian_identical: true`) | [`docs/RELEASEGUARD_AUDIT_EVIDENCE/live_e2e_evidence.json`](RELEASEGUARD_AUDIT_EVIDENCE/live_e2e_evidence.json) |
| **Phase 1 Live Proof (3 Repeatable Runs)** | GitHub PRs `#11`–`#13` + Flagsmith `#503` + NovaShop + Bayesian C/B/A + Category C approval | **PASS (`3/3`)** (`OPEN_GITHUB_PRS: 0`) | [`docs/phase1-live-proof-report.md`](phase1-live-proof-report.md) |
| **Phase 2 Cleanup Lifecycle Proof** | GitHub Draft PR `#17` + Flagsmith `#56055` Dev + `409` gate + Category C archive | **PASS** (`OPEN_GITHUB_PRS: 0`, `cleaned_up`) | [`docs/phase2-cleanup-proof-report.md`](phase2-cleanup-proof-report.md) |
| **Live 3-Trial Productivity Benchmark** | Flagsmith Cloud `#56055` Dev (`flag_setup`) | **PASS (`3/3`)** (`mean=5.7719s`, `median=5.5493s`, `p95=6.5291s`) | `run_benchmark.py` (`VERIFIED_MEASURED`) |
