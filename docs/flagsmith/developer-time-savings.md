# Developer Productivity & Time-Savings Benchmarks

This document reports **ReleaseGuard AI** developer productivity measurements with strict provenance separation between **Verified Measured** automated runs, **Estimated** manual engineering baselines, and **Simulated** local runs.

> [!IMPORTANT]
> Automated execution times and manual engineering baselines are never treated as equivalent evidence. Percentage time savings (`pct_saved`) are computed only when a documented baseline is explicitly attached, and omitted when no baseline is present.

---

## 1. Evidence Classification & Methodology

Every benchmark record persisted in `benchmark_runs` / `benchmark_stages` (and returned by `/api/v1/benchmarks/*` and `backend/scripts/run_benchmark.py`) carries one of four provenance classifications:

| Classification | Meaning |
|---|---|
| `VERIFIED_MEASURED` | Executed against live external APIs (e.g. Flagsmith Cloud Project `#56055` in `development`) with `time.monotonic()` start/end and per-stage timings and automatic cleanup. |
| `ESTIMATED` | Documented manual engineering effort baselines (ranges + midpoint) used for comparison; **not** timed human trials. |
| `SIMULATED` | Deterministic local workflow execution when external credentials are unavailable or local failure/retry scenarios are tested. |
| `INCOMPLETE` | Failed run or `--require-live` run aborted due to missing integration credentials. |

### Active Developer Time vs. Unattended Execution Time

- **Unattended Execution Time (`unattended_execution_seconds`)**: Wall-clock `time.monotonic()` duration spent by ReleaseGuard AI executing API calls, flag provisioning, verification, and cleanup without developer input.
- **Active Developer Time (`active_developer_seconds`) / Human Intervention (`human_intervention_seconds`)**: Time spent by an engineer reviewing or approving a Category B/C gate.

---

## 2. Live Measured Benchmark B (`flag_setup`, 3 Trials)

Executed via the repeatable CLI runner in `development` against **Flagsmith Cloud Project `#56055`**:

```powershell
$env:USE_SQLITE_FALLBACK="true"
Push-Location backend
.\.venv\Scripts\python.exe scripts/run_benchmark.py --workflow flag_setup --environment development --scenario live-flagsmith-b --trials 3 --require-live
Pop-Location
```

- **Environment**: `development` (`production` / `prod` is strictly blocked in both the CLI and `POST /api/v1/benchmarks/run`).
- **Scope**: Validate environment & project (`2` API calls) -> Create temporary multivariate flag with `control`/`variant_a` 50/50 split (`3` API calls) -> List segments & verify live environment feature states (`2` API calls) -> Delete temporary benchmark flag (`1` API call, `Cleanup: deleted`).
- **Aggregate Results (3 trials, `VERIFIED_MEASURED`)**:
  - **Mean**: `5.0180 s`
  - **Median (`p50`)**: `5.0145 s`
  - **p95**: `5.1057 s`
  - **API Calls / Trial**: `8` | **Retries**: `0` | **Failures**: `0`
  - **Active Developer Time**: `0.0000 s` (unattended API provisioning; no approval gate in `development`)
  - **Unattended Execution Time**: `4.9236 s` – `5.1156 s`

| Trial | Benchmark ID | Total (s) | `validate_env` | `provision_flag` | `configure_targeting` | `cleanup` | Cleanup Status |
|---|---|---:|---:|---:|---:|---:|---|
| Trial 1 | `bench-flag_setup-ec26b81caa` | `5.0145` | `1.6212 s` | `1.6025 s` | `0.9812 s` | `0.8094 s` | `deleted` |
| Trial 2 | `bench-flag_setup-9132d98164` | `4.9237` | `1.3550 s` | `1.8863 s` | `0.9954 s` | `0.6869 s` | `deleted` |
| Trial 3 | `bench-flag_setup-c2e228f40f` | `5.1158` | `1.1087 s` | `2.0458 s` | `1.2343 s` | `0.7268 s` | `deleted` |

Comparing the `5.0180 s` (`VERIFIED_MEASURED`) automated mean against the `1200.0 s` (`20 min` midpoint of `15–25 min`, `ESTIMATED`) manual Flagsmith UI baseline yields `1194.98 s` (`99.58%`) of setup toil avoided on this specific provisioning workflow, subject to the caveats in Section 5.

---

## 3. Documented Manual Engineering Baselines (`ESTIMATED`)

Defined in `backend/app/services/benchmark_service.py` (`MANUAL_BASELINES`) and exposed via `GET /api/v1/benchmarks/baselines`:

| Workflow | Label | Estimated Range | Midpoint (s) | Rationale |
|---|---|---|---|---|
| **A (`pr_review`)** | PR Review & Risk Assessment | `15–25 min` | `1080 s` | Opening PR diff, inspecting changed modules/migrations, cross-referencing release policies, posting findings. |
| **B (`flag_setup`)** | Multivariate Flag & Targeting Setup | `15–25 min` | `1200 s` | Navigating Flagsmith UI, creating feature + MV variants, configuring targeting/percentage splits, verifying state, cleanup. |
| **C (`experiment_setup`)** | Experiment Setup & Bayesian Evaluation | `10–20 min` | `900 s` | Defining primary/guardrail metrics, validating splits, collecting sample counts, computing Bayesian win probabilities. |
| **D (`release_decision`)** | Release Readiness & Go/No-Go Decision | `7–15 min` | `600 s` | Checking CI/PR checks, Flagsmith rollout state, experiment guardrails, and recording an auditable approval decision. |
| **E (`complete_release`)** | Complete End-to-End Release Pipeline | `35–60 min` | `2700 s` | Conservative sum of non-overlapping manual release coordination steps from PR review through rollout check. |

---

## 4. Historical Single-Run Snapshot (`task-3766`)

During earlier `task-3766` integration validation against Flagsmith Cloud Project `#56055`, a one-off multi-step pipeline was timed without the repeatable benchmark harness:

- **Provision 10 Governed Flags + MV Variants + 3 Segments + 5 Dev Kill Switches (`VERIFIED_MEASURED`, n=1)**: `40.84 s` (vs. `ESTIMATED` `~25–35 min` manual baseline).
- **Configure & Start 4 Multivariate Flagsmith Native Experiments + Metrics (`VERIFIED_MEASURED`, n=1)**: `40.82 s` (vs. `ESTIMATED` `~20–30 min` manual baseline).
- **50-Identity Synthetic Traffic Cohort + Flagsmith Refresh + 50k Beta-Binomial Evaluation (`SIMULATED` cohort + `VERIFIED_MEASURED` Flagsmith evaluation, n=1)**: `6.68 s` (vs. `ESTIMATED` `~15–20 min` manual baseline).
- **Combined Single-Run Total**: `89.63 s` (`~1.5 min`).

Because `task-3766` was a single historical run (`n=1`) and its third stage included a 50-identity synthetic cohort, it is recorded here as historical context rather than a multi-trial aggregate benchmark.

---

## 5. Limitations & Guardrails

1. **Manual Baselines Are Estimated, Not Timed Human Studies**: All `manual_baseline_seconds` values are documented engineering estimates (`ESTIMATED`), not controlled stopwatch trials of human engineers.
2. **Human Approval Latency Is Separate**: Production mutations in ReleaseGuard AI enforce `PolicyCategory.C` human approval. Unattended API provisioning (`flag_setup` in `development`) measures `active_developer_seconds = 0.0 s`, whereas real production releases include human review and approval latency.
3. **Network & API Variance**: Live Flagsmith Cloud API latency varies with network conditions and Flagsmith API load; single-run timings should not be generalized without repeated trials (`--trials N`).
4. **Non-Production Only**: Both `run_benchmark.py` and `POST /api/v1/benchmarks/run` strictly reject `production` / `prod` environments to prevent benchmark side effects in production.
