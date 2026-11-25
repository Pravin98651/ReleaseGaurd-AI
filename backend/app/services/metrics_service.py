"""Detailed Metrics & Analytics computation helpers (Section 4 & 12 honest definitions)."""

from __future__ import annotations

from typing import Any

from app.core.database import ensure_utc
from app.models import (
    AgentTask,
    AgentTaskStatus,
    AnalysisStatus,
    Approval,
    ApprovalStatus,
    AuditLog,
    CodeChange,
    ExperimentEvent,
    FeatureFlag,
    PullRequest,
    PullRequestState,
    Repository,
    RolloutAction,
    RolloutHistory,
    ValidationStatus,
)
from app.schemas.analytics import FilterWindowMeta
from app.services.analytics_queries import (
    _env_state_summary,
    _in_window,
    build_experiment_performance_points,
    compute_dashboard_charts,
    compute_dashboard_metrics,
    get_metric_definitions_docs,
    is_simulator_event,
)


def compute_metrics_overview(data: dict[str, Any], flt: FilterWindowMeta) -> dict[str, Any]:
    """Assemble overview metrics, filter catalog options, definitions, and source labels."""
    summary = compute_dashboard_metrics(data, flt)
    charts = compute_dashboard_charts(data, flt)
    repos: list[Repository] = data["repos"]
    flags: list[FeatureFlag] = data["flags"]
    exps = data["experiments"]

    return {
        "filter": flt.model_dump(mode="json"),
        "summary": summary.model_dump(mode="json"),
        "telemetry_totals": charts.telemetry_totals,
        "metric_definitions": [d.model_dump(mode="json") for d in get_metric_definitions_docs()],
        "filter_options": {
            "environments": sorted(
                {
                    env_name.lower()
                    for f in flags
                    for env_name in (f.env_states or {})
                    if isinstance(env_name, str)
                }
                or {"development", "staging", "production"}
            ),
            "repositories": [{"id": str(r.id), "full_name": r.full_name} for r in repos],
            "flags": [{"id": str(f.id), "key": f.key} for f in flags],
            "experiments": [
                {"id": str(e.id), "name": e.name, "environment_name": e.environment_name}
                for e in exps
            ],
        },
        "data_sources": {
            "repositories": "live_external",
            "pull_requests": "live_external",
            "feature_flags": "live_external",
            "rollout_history": "live_external",
            "experiments": "calculated",
            "events": "simulator_generated"
            if flt.event_source == "simulator_generated"
            else (
                "application_telemetry"
                if flt.event_source == "application_telemetry"
                else "mixed_telemetry_and_simulator"
            ),
            "agents": "database",
        },
    }


def compute_release_metrics(data: dict[str, Any], flt: FilterWindowMeta) -> dict[str, Any]:
    """Release engineering metrics distinguishing Draft PRs, rollouts, and unavailable CD deploys."""
    start, end = flt.start_at, flt.end_at
    rollouts: list[RolloutHistory] = [
        r for r in data["rollouts"] if _in_window(r.created_at, start, end)
    ]
    approvals: list[Approval] = [
        a for a in data["approvals"] if _in_window(a.created_at, start, end)
    ]
    changes: list[CodeChange] = [
        c for c in data["changes"] if _in_window(c.created_at, start, end)
    ]
    flags_by_id = {f.id: f for f in data["flags"]}

    verified_non_rb = sum(
        1 for r in rollouts if r.verified and r.action != RolloutAction.ROLLBACK
    )
    failed_ops = sum(1 for r in rollouts if not r.verified) + sum(
        1
        for a in approvals
        if a.status == ApprovalStatus.FAILED and a.target_resource_type == "feature_flag"
    )
    rollbacks = sum(1 for r in rollouts if r.action == RolloutAction.ROLLBACK)
    total_completed = verified_non_rb + failed_ops

    release_success_rate = (
        round((verified_non_rb / total_completed) * 100.0, 2) if total_completed > 0 else None
    )
    change_failure_rate = (
        round((failed_ops / total_completed) * 100.0, 2) if total_completed > 0 else None
    )
    rollback_rate = round((rollbacks / len(rollouts)) * 100.0, 2) if rollouts else None

    approval_durations: list[float] = []
    for a in approvals:
        c_at = ensure_utc(a.created_at)
        d_at = ensure_utc(a.decided_at)
        if c_at and d_at and d_at >= c_at:
            approval_durations.append((d_at - c_at).total_seconds())
    avg_approval_sec = (
        round(sum(approval_durations) / len(approval_durations), 2)
        if approval_durations
        else None
    )

    # Calculate MTTR only when a verified ROLLBACK follows a prior rollout step/enable on same flag+env
    mttr_samples: list[float] = []
    by_flag_env: dict[tuple[str, str], list[RolloutHistory]] = {}
    for r in sorted(rollouts, key=lambda x: ensure_utc(x.created_at) or start):
        by_flag_env.setdefault((str(r.feature_flag_id), r.environment_name.lower()), []).append(r)
    for seq in by_flag_env.values():
        last_step_at = None
        for r in seq:
            r_ts = ensure_utc(r.created_at)
            if r.action != RolloutAction.ROLLBACK and r_ts is not None:
                last_step_at = r_ts
            elif r.action == RolloutAction.ROLLBACK and r.verified and last_step_at and r_ts and r_ts >= last_step_at:
                mttr_samples.append((r_ts - last_step_at).total_seconds())
                last_step_at = None
    mttr_seconds = round(sum(mttr_samples) / len(mttr_samples), 2) if mttr_samples else None

    by_env: dict[str, dict[str, Any]] = {}
    for r in rollouts:
        row = by_env.setdefault(
            r.environment_name.lower(),
            {"environment": r.environment_name.lower(), "total": 0, "verified": 0, "failed": 0, "rollbacks": 0},
        )
        row["total"] += 1
        if r.action == RolloutAction.ROLLBACK:
            row["rollbacks"] += 1
        elif r.verified:
            row["verified"] += 1
        else:
            row["failed"] += 1

    recent_rows = [
        {
            "id": str(r.id),
            "flag_key": flags_by_id[r.feature_flag_id].key if r.feature_flag_id in flags_by_id else None,
            "environment_name": r.environment_name,
            "action": r.action.value,
            "verified": r.verified,
            "reason": r.reason,
            "created_at": r.created_at.isoformat(),
        }
        for r in rollouts[:30]
    ]

    return {
        "source": "live_external",
        "metrics": {
            "deployment_frequency": {
                "value": None,
                "status": "unavailable",
                "reason": "Deployment webhook events are not tracked; feature flag rollouts and Draft PR preparation are reported separately.",
            },
            "lead_time_for_changes_seconds": {
                "value": None,
                "status": "unavailable",
                "reason": "Unavailable without production CD deployment timestamps.",
            },
            "release_frequency_rollouts": len(rollouts),
            "release_preparation_draft_prs": sum(1 for c in changes if c.draft_pr_number is not None),
            "release_success_rate": release_success_rate,
            "change_failure_rate": change_failure_rate,
            "rollback_rate": rollback_rate,
            "mean_time_to_recovery_seconds": mttr_seconds,
            "average_approval_duration_seconds": avg_approval_sec,
        },
        "by_environment": list(by_env.values()),
        "recent_operations": recent_rows,
    }


def compute_pr_metrics(data: dict[str, Any], flt: FilterWindowMeta) -> dict[str, Any]:
    """Pull request & patch validation metrics backed by pull_requests and code_changes."""
    start, end = flt.start_at, flt.end_at
    prs_all: list[PullRequest] = data["prs"]
    prs = [p for p in prs_all if _in_window(p.created_at, start, end)]
    changes: list[CodeChange] = [c for c in data["changes"] if _in_window(c.created_at, start, end)]
    repos_by_id = {r.id: r for r in data["repos"]}

    risk_dist: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0, "unanalyzed": 0}
    analysis_durations: list[float] = []
    by_repo_map: dict[str, dict[str, Any]] = {}

    for p in prs:
        if p.risk_level is not None:
            risk_dist[p.risk_level.value] = risk_dist.get(p.risk_level.value, 0) + 1
        else:
            risk_dist["unanalyzed"] += 1

        c_dt = ensure_utc(p.created_at)
        a_dt = ensure_utc(p.analyzed_at)
        if c_dt and a_dt and a_dt >= c_dt:
            analysis_durations.append((a_dt - c_dt).total_seconds())

        r_obj = repos_by_id.get(p.repository_id)
        r_name = r_obj.full_name if r_obj else str(p.repository_id)
        rb = by_repo_map.setdefault(
            r_name,
            {
                "repository_id": str(p.repository_id),
                "full_name": r_name,
                "total_prs": 0,
                "analyzed_prs": 0,
                "high_risk_prs": 0,
                "open_prs": 0,
            },
        )
        rb["total_prs"] += 1
        if p.analysis_status == AnalysisStatus.COMPLETED:
            rb["analyzed_prs"] += 1
        if p.risk_level and p.risk_level.value in ("high", "critical"):
            rb["high_risk_prs"] += 1
        if p.state == PullRequestState.OPEN:
            rb["open_prs"] += 1

    val_passed = sum(1 for c in changes if c.validation_status == ValidationStatus.PASSED)
    val_failed = sum(1 for c in changes if c.validation_status == ValidationStatus.FAILED)
    val_denom = val_passed + val_failed
    val_rate = round((val_passed / val_denom) * 100.0, 2) if val_denom > 0 else None

    return {
        "source": "live_external",
        "metrics": {
            "total_prs": len(prs),
            "analyzed_prs": sum(1 for p in prs if p.analysis_status == AnalysisStatus.COMPLETED),
            "high_risk_prs": risk_dist["high"] + risk_dist["critical"],
            "draft_prs_generated": sum(1 for c in changes if c.draft_pr_number is not None),
            "waiting_for_review": sum(1 for p in prs if p.state == PullRequestState.OPEN),
            "failed_analyses": sum(1 for p in prs if p.analysis_status == AnalysisStatus.FAILED),
            "patch_validation_total": len(changes),
            "patch_validation_passed": val_passed,
            "patch_validation_success_rate": val_rate,
            "average_analysis_elapsed_seconds": (
                round(sum(analysis_durations) / len(analysis_durations), 2)
                if analysis_durations
                else None
            ),
        },
        "risk_distribution": [{"level": k, "count": v} for k, v in risk_dist.items()],
        "by_repository": list(by_repo_map.values()),
    }


def compute_agent_metrics(data: dict[str, Any], flt: FilterWindowMeta) -> dict[str, Any]:
    """AI Agent run reliability, latency, tool success, and exact token consumption."""
    start, end = flt.start_at, flt.end_at
    tasks: list[AgentTask] = [t for t in data["tasks"] if _in_window(t.created_at, start, end)]

    completed = sum(1 for t in tasks if t.status == AgentTaskStatus.COMPLETED)
    failed = sum(1 for t in tasks if t.status == AgentTaskStatus.FAILED)
    cancelled = sum(1 for t in tasks if t.status == AgentTaskStatus.CANCELLED)
    running = sum(1 for t in tasks if t.status in (AgentTaskStatus.RUNNING, AgentTaskStatus.QUEUED))
    awaiting = sum(1 for t in tasks if t.status == AgentTaskStatus.AWAITING_APPROVAL)

    denom = completed + failed
    success_rate = round((completed / denom) * 100.0, 2) if denom > 0 else None

    durations: list[float] = []
    tool_total = 0
    tool_ok = 0
    prompt_toks = 0
    cand_toks = 0
    total_toks = 0
    has_token_data = False
    model_stats: dict[str, dict[str, Any]] = {}

    for t in tasks:
        s_dt = ensure_utc(t.started_at)
        c_dt = ensure_utc(t.completed_at)
        if s_dt and c_dt and c_dt >= s_dt:
            durations.append((c_dt - s_dt).total_seconds())

        mdl = t.gemini_model or "unrecorded"
        mb = model_stats.setdefault(mdl, {"model": mdl, "runs": 0, "completed": 0, "failed": 0, "tokens": 0})
        mb["runs"] += 1
        if t.status == AgentTaskStatus.COMPLETED:
            mb["completed"] += 1
        elif t.status == AgentTaskStatus.FAILED:
            mb["failed"] += 1

        tu = t.token_usage or {}
        if isinstance(tu, dict) and int(tu.get("total_tokens") or 0) > 0:
            has_token_data = True
            pt = int(tu.get("prompt_tokens") or 0)
            ct = int(tu.get("candidate_tokens") or 0)
            tt = int(tu.get("total_tokens") or 0)
            prompt_toks += pt
            cand_toks += ct
            total_toks += tt
            mb["tokens"] += tt

        for step in t.steps_log or []:
            if isinstance(step, dict) and step.get("event_type") == "tool.executed":
                tool_total += 1
                st = str((step.get("payload") or {}).get("status") or "ok").lower()
                if st != "error":
                    tool_ok += 1

    return {
        "source": "database",
        "metrics": {
            "total_runs": len(tasks),
            "completed_runs": completed,
            "failed_runs": failed,
            "cancelled_runs": cancelled,
            "running_runs": running,
            "awaiting_approval_runs": awaiting,
            "agent_success_rate": success_rate,
            "average_execution_duration_seconds": (
                round(sum(durations) / len(durations), 2) if durations else None
            ),
            "tool_invocation_count": tool_total,
            "tool_success_rate": round((tool_ok / tool_total) * 100.0, 2) if tool_total > 0 else None,
            "prompt_tokens": prompt_toks if has_token_data else None,
            "candidate_tokens": cand_toks if has_token_data else None,
            "total_tokens": total_toks if has_token_data else None,
            "api_errors": failed,
            "average_task_cost_usd": None,
            "cost_note": "Unavailable without configured per-model pricing table; never fabricated.",
        },
        "by_model": list(model_stats.values()),
        "recent_runs": [
            {
                "id": str(t.id),
                "specialist": t.specialist.value,
                "status": t.status.value,
                "gemini_model": t.gemini_model,
                "total_tokens": (t.token_usage or {}).get("total_tokens"),
                "created_at": t.created_at.isoformat(),
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in tasks[:25]
        ],
    }


def compute_flag_metrics(data: dict[str, Any], flt: FilterWindowMeta) -> dict[str, Any]:
    """Feature flag configuration, rollout percentages, errors, and health inventory."""
    start, end = flt.start_at, flt.end_at
    flags: list[FeatureFlag] = data["flags"]
    rollouts: list[RolloutHistory] = [
        r for r in data["rollouts"] if _in_window(r.created_at, start, end)
    ]
    audits: list[AuditLog] = [
        a for a in data["audits"] if _in_window(a.created_at, start, end)
    ]

    rollouts_by_flag: dict[str, int] = {}
    rollbacks_by_flag: dict[str, int] = {}
    for r in rollouts:
        fid = str(r.feature_flag_id)
        rollouts_by_flag[fid] = rollouts_by_flag.get(fid, 0) + 1
        if r.action == RolloutAction.ROLLBACK:
            rollbacks_by_flag[fid] = rollbacks_by_flag.get(fid, 0) + 1

    active_cnt = inactive_cnt = gradual_cnt = 0
    rows: list[dict[str, Any]] = []
    for f in flags:
        en, dis, grad = _env_state_summary(f, flt.environment)
        if grad:
            gradual_cnt += 1
        elif en:
            active_cnt += 1
        elif dis:
            inactive_cnt += 1

        envs_view: dict[str, Any] = {}
        for env_name, st in (f.env_states or {}).items():
            if not isinstance(st, dict):
                continue
            envs_view[env_name] = {
                "enabled": bool(st.get("enabled", False)),
                "rollout_percentage": int(
                    st.get("rollout_percentage") or (100 if st.get("enabled") else 0)
                ),
                "version_uuid": st.get("version_uuid"),
            }

        rows.append(
            {
                "id": str(f.id),
                "key": f.key,
                "flag_type": f.flag_type.value,
                "health_score": f.health_score,
                "health_issues_count": len(f.health_issues or []),
                "state_changes": rollouts_by_flag.get(str(f.id), 0),
                "rollbacks": rollbacks_by_flag.get(str(f.id), 0),
                "environments": envs_view,
                "last_synced_at": f.last_synced_at.isoformat() if f.last_synced_at else None,
            }
        )

    config_errors = sum(1 for r in rollouts if not r.verified) + sum(
        1 for a in audits if a.event_action == "flagsmith.verification_failed"
    )

    return {
        "source": "live_external",
        "metrics": {
            "total_flags": len(flags),
            "active_flags": active_cnt,
            "inactive_flags": inactive_cnt,
            "gradual_rollout_flags": gradual_cnt,
            "flags_created_in_window": sum(1 for f in flags if _in_window(f.created_at, start, end)),
            "flag_state_changes": len(rollouts),
            "rollback_frequency": sum(rollbacks_by_flag.values()),
            "configuration_errors": config_errors,
            "health_findings_total": sum(len(f.health_issues or []) for f in flags),
        },
        "flags": rows,
    }


def compute_experiment_metrics(data: dict[str, Any], flt: FilterWindowMeta) -> dict[str, Any]:
    """Detailed experiment metrics with source breakdown and statistical clarity."""
    points = build_experiment_performance_points(data, flt)
    return {
        "source": "calculated",
        "experiments": [p.model_dump(mode="json") for p in points],
        "statistical_note": (
            "All posterior rates, 95% credible intervals, expected loss, and guardrail breach probabilities "
            "are computed deterministically via Beta-Binomial Monte Carlo (Beta(1,1) prior). Simulator runs "
            "are counted separately via session_key 'sim_*' telemetry attribution."
        ),
    }


def compute_event_metrics(data: dict[str, Any], flt: FilterWindowMeta) -> dict[str, Any]:
    """Telemetry analytics separating simulator-generated cohorts from application telemetry."""
    start, end = flt.start_at, flt.end_at
    charts = compute_dashboard_charts(data, flt)
    events: list[ExperimentEvent] = [
        ev for ev in data["events"] if _in_window(ev.occurred_at, start, end)
    ]

    by_type: dict[str, int] = {}
    by_variation: dict[str, int] = {}
    for ev in events:
        et = ev.event_type.value
        by_type[et] = by_type.get(et, 0) + 1
        by_variation[ev.variation_key] = by_variation.get(ev.variation_key, 0) + 1

    recent_events = [
        {
            "event_id": ev.event_id,
            "flag_key": ev.flag_key,
            "environment_name": ev.environment_name,
            "variation_key": ev.variation_key,
            "event_type": ev.event_type.value,
            "user_identifier": ev.user_identifier,
            "source_classification": "simulator_generated"
            if is_simulator_event(ev)
            else "application_telemetry",
            "occurred_at": ev.occurred_at.isoformat(),
        }
        for ev in reversed(events[-50:])
    ]

    return {
        "source": "application_telemetry",
        "totals": charts.telemetry_totals,
        "by_event_type": [{"event_type": k, "count": v} for k, v in by_type.items()],
        "by_variation": [{"variation_key": k, "count": v} for k, v in by_variation.items()],
        "time_series": [p.model_dump(mode="json") for p in charts.telemetry_activity],
        "recent_events": recent_events,
    }
