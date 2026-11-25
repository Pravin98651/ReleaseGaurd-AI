# Scalability Assessment & Bottleneck Fixes (Phase 5)

## Key Bottlenecks Discovered & Remediated
1. **Unbounded Analytics Table Scans (`analytics_queries.py`):**
   - *Before:* `load_scoped_dataset` pulled 100% of `ExperimentEvent`, `RolloutHistory`, `AgentTask`, `Approval`, `PullRequest`, and `CodeChange` into Python memory and filtered in loops.
   - *After:* SQL predicates (`repository_id`, `flag_key`, `environment_name`, `experiment_id`, and trend-preserving `occurred_at >= start_at - span`) execute in PostgreSQL with explicit row bounds (`limit(25000)` on events, `limit(1000)`–`2000` on operational tables).
2. **Command Center Overview Full-Table Scan (`agent_executions.py`):**
   - *Before:* Loaded all `AgentExecution` and `AgentToolCall` ORM objects into Python to compute counts and averages.
   - *After:* Uses SQL `GROUP BY status` (`func.count`) + `func.avg(AgentExecution.duration_ms)` for O(1) memory footprint.
3. **PostgreSQL Connection Pool Bounds (`core/database.py`):**
   - *After:* Configured `pool_size=20`, `max_overflow=30`, `pool_timeout=15`, `pool_recycle=1800` for non-SQLite async engines.
4. **SSE Lock/Sequence Pruning (`services/sse_bus.py`):**
   - *After:* Prunes idle `task_id` entries from `_locks` and `_seq_counters` when subscribers drain and protects `_ws_history` under `_ws_lock`.

## Remaining Architectural Bottlenecks (Single-Worker Scope)
- **SSE Multi-Worker Fanout:** `SSEBus` uses in-process `asyncio.Queue` subscribers. Scaling `uvicorn` beyond `1` worker process would require PostgreSQL `LISTEN/NOTIFY` or Redis Pub/Sub.
- **`/api/v1/system/health` Live Demo Probes:** Probes `demo-backend` + `demo-frontend` on every request (`~68ms` + `~384ms`), causing tail latency spikes (`p95 = 1.66s` at `50` concurrency); caching external dependency probe results for `5–10s` would drop `/system/health` latency dramatically.
