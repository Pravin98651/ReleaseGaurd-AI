# Failure Recovery & Resilience Design (Phase 6)

1. **Scheduler Crash & Restart Recovery (`SchedulerService.recover_stale_runs`):**
   - Runs automatically on FastAPI lifespan startup (`main.py:155`), transitions interrupted `RUNNING`/`RETRYING` `JobExecution` rows to `FAILED`, releases stale `locked_by`/`locked_until` leases, and increments `missed_runs_count` without resetting DB state.
2. **Release & Agent Execution Recovery:**
   - `ReleaseOrchestrator.recover_interrupted_releases` and `AgentExecutionService.recover_interrupted_executions` restore interrupted workflows from PostgreSQL `steps_log` and LangGraph `SqlAlchemyCheckpointSaver` checkpoints.
3. **Single-Use Atomic Approvals & External Reconciliation (`PolicyEngine` + `approval_executor.py`):**
   - Uses conditional atomic SQL transition (`PENDING -> EXECUTING`), verifies SHA-256 `action_hash` + `expires_at` + live Flagsmith precondition snapshot before mutation, and requires `verify_live_state()` readback after mutation before marking `EXECUTED`.
4. **Idempotent Event Ingestion & Webhook Deduplication:**
   - `experiment_events.event_id` and `github_webhook_events.delivery_id` enforce unique constraints; `receive_webhook` uses savepoint (`begin_nested()`) + `IntegrityError` recovery for concurrent identical deliveries.
5. **External Provider Rate-Limit & Timeout Backoff:**
   - `FlagsmithClient` and `GitHubClient` retry only idempotent requests (`GET`/`HEAD`/`OPTIONS`) on `429`/`502`/`503`/timeouts with `Retry-After` capping; mutations are never blindly retried without state checks.
