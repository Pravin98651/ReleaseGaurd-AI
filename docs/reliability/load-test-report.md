# Local Load & Performance Test Report (Phase 10)

**Date:** 2026-11-26  
**Environment:** Local Docker Compose (`releaseguardai-backend` + `postgres:16-alpine` + `demo-backend` + `demo-frontend`, Windows host)  
**Workload:** Mixed safe read-only GET traffic cycling evenly across `/health/live`, `/health/ready`, and `/api/v1/system/health` (`10` requests per concurrent worker) using `httpx.AsyncClient`.

## Measured Results (`task-3540`)

| Concurrency | Total Requests | Duration (s) | Throughput (RPS) | p50 Latency (ms) | p95 Latency (ms) | p99 Latency (ms) | Errors | Error Rate |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **1** | 10 | 0.551 | 18.1 | 4.58 | 72.38 | 72.38 | 0 | **0.0%** |
| **10** | 100 | 1.503 | 66.5 | 56.26 | 421.43 | 488.45 | 0 | **0.0%** |
| **25** | 250 | 3.419 | 73.1 | 135.71 | 943.89 | 1251.36 | 0 | **0.0%** |
| **50** | 500 | 6.560 | 76.2 | 307.03 | 1665.15 | 1743.80 | 0 | **0.0%** |

## Analysis & Honest Limitations
- **Zero Errors:** `860/860` concurrent requests completed with `0.0%` HTTP/connection errors up to `50` concurrent workers.
- **Latency Driver:** Roughly one-third of all requests hit `/api/v1/system/health`, which synchronously performs outbound HTTP checks against `demo-backend` (~`68ms`) and `demo-frontend` Next.js SSR (~`384ms`), inflating p95/p99 above `1s` at 50 concurrency. Lightweight `/health/live` and `/health/ready` respond in single-digit milliseconds at low concurrency.
- **Scope Limitation:** Load tests excluded mutating Flagsmith/GitHub/Gemini calls to avoid external provider rate-limiting and unwanted mutations. Single Uvicorn worker topology tested.
