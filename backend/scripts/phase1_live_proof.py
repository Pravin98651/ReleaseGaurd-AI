"""Repeatable 3-run Phase 1 Live Proof runner for ReleaseGuard AI (Development-only).

Strictly targets Development / non-production environments and NEVER mutates
Flagsmith Production or GitHub ``main``.

Exercises across 3 complete repeatable runs from clean scoped dev state:
  (a) Real isolated GitHub demo PR on Pravin98651/ReleaseGaurd-AI -> sync -> PR
      analysis + 7-dimension release risk + Flagsmith opportunity check.
  (b) Live Flagsmith #503 (checkout_experience_v2, Development #120019) via
      NovaShop + live Flagsmith Edge + /api/v1/events/ingest across:
        - Scenario C: Insufficient Data (< 100 unique participants/arm) -> 409
        - Scenario B: Guardrail Breach (>= 100/arm, high payment_failed) -> 409
        - Scenario A: Winning Variant (>= 100/arm, healthy lift) -> promotion
  (c) Full 12-step Winner Promotion workflow with second human reviewer
      (reviewer@releaseguard.local) for non-self-approval, precondition drift
      check, live Flagsmith mutation, live readback verification, audit log,
      and single-use replay rejection.
  (d) Clean restoration of Flagsmith Dev #503 (34/33/33) and GitHub PR/branch
      cleanup between runs, asserting Production and main remain untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, select, update

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.core.database import dispose_engine, get_session_factory  # noqa: E402
from app.integrations.flagsmith_client import FlagsmithClient  # noqa: E402
from app.integrations.github_client import GitHubClient  # noqa: E402
from app.models import (  # noqa: E402
    Approval,
    Experiment,
    ExperimentEvent,
    ExperimentStatus,
    FeatureFlag,
    PullRequest,
    PullRequestState,
)

REPO_OWNER = "Pravin98651"
REPO_NAME = "ReleaseGaurd-AI"
TARGET_FLAG_KEY = "checkout_experience_v2"
TARGET_EXP_ID_FS = "503"
TARGET_ENV = "development"
CONTROL_VAR = "control"
TREATMENT_VAR = "one_page_checkout"
THIRD_VAR = "simplified_checkout"
BASELINE_SPLIT = {
    "control": 34,
    "one_page_checkout": 33,
    "simplified_checkout": 33,
}
REVIEWER_EMAIL = "reviewer@releaseguard.local"
REVIEWER_NAME = "ReleaseGuard Reviewer"
REVIEWER_PASSWORD = os.environ.get("PHASE1_REVIEWER_PASSWORD", "Phase1Reviewer!2026")


def _resolve_host_db_url(raw_url: str) -> str:
    """Translate docker-internal @postgres:5432 to @localhost:5432 when running on host."""
    if "@postgres:5432" in raw_url and not Path("/.dockerenv").exists():
        return raw_url.replace("@postgres:5432", "@localhost:5432")
    return raw_url


def _assert_non_prod_environment(env_name: str) -> None:
    norm = env_name.strip().lower()
    if norm in {"prod", "production", "live", "prd"} or "prod" in norm or norm != "development":
        raise RuntimeError(
            f"BLOCKED: phase1_live_proof.py strictly permits 'development' only (got '{env_name}')."
        )


def _snapshot_flag_state(states: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract canonical comparable snapshot of Flagsmith environment feature states."""
    base = next((s for s in states if s.get("feature_segment") is None), None) or (states[0] if states else {})
    mv_vals = {
        str(m.get("multivariate_feature_option")): float(m.get("percentage_allocation", 0.0))
        for m in (base.get("multivariate_feature_state_values") or [])
    }
    return {
        "enabled": bool(base.get("enabled", False)),
        "feature_state_value": base.get("feature_state_value"),
        "version_uuid": base.get("version_uuid") or base.get("environment_feature_version"),
        "mv_allocations_by_option_id": mv_vals,
    }


async def _ensure_reviewer_and_tokens(
    api_client: httpx.AsyncClient,
    *,
    operator_email: str,
    operator_password: str,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    """Authenticate operator and ensure independent reviewer@releaseguard.local exists."""
    op_resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": operator_email, "password": operator_password},
    )
    op_resp.raise_for_status()
    op_data = op_resp.json()
    op_token = op_data["access_token"]
    op_user = op_data["user"]
    op_headers = {"Authorization": f"Bearer {op_token}"}

    users_resp = await api_client.get("/api/v1/auth/users", headers=op_headers)
    users_resp.raise_for_status()
    existing = next((u for u in users_resp.json() if u["email"] == REVIEWER_EMAIL), None)
    if existing is None:
        create_resp = await api_client.post(
            "/api/v1/auth/users",
            headers=op_headers,
            json={
                "email": REVIEWER_EMAIL,
                "display_name": REVIEWER_NAME,
                "password": REVIEWER_PASSWORD,
                "role": "release_manager",
            },
        )
        create_resp.raise_for_status()

    rev_resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": REVIEWER_EMAIL, "password": REVIEWER_PASSWORD},
    )
    rev_resp.raise_for_status()
    rev_data = rev_resp.json()
    rev_token = rev_data["access_token"]
    rev_user = rev_data["user"]
    if rev_user["id"] == op_user["id"]:
        raise RuntimeError("Reviewer account must be distinct from operator account.")
    return op_user, op_token, rev_user, rev_token


async def _clear_scoped_dev_events_and_reset_exp(
    session_factory: Any,
    *,
    exp_db_id: uuid.UUID,
) -> None:
    """Clear only scoped dev telemetry for checkout_experience_v2 and reset #503 DB status to running."""
    async with session_factory() as session:
        await session.execute(
            delete(ExperimentEvent).where(
                ExperimentEvent.flag_key == TARGET_FLAG_KEY,
                ExperimentEvent.environment_name == TARGET_ENV,
            )
        )
        await session.execute(
            update(Experiment)
            .where(Experiment.id == exp_db_id)
            .values(status=ExperimentStatus.RUNNING, ended_at=None)
        )
        await session.commit()


async def _restore_flagsmith_dev_503(
    fs: FlagsmithClient,
    *,
    project_id: int,
    dev_env_key: str,
    feature_id: int,
) -> dict[str, Any]:
    """Restore Flagsmith Development #120019 checkout_experience_v2 to clean 34/33/33 split."""
    res = await fs.configure_multivariate_split(
        project_id=project_id,
        env_key=dev_env_key,
        feature_id=feature_id,
        flag_key=TARGET_FLAG_KEY,
        allocations=dict(BASELINE_SPLIT),
        verify_edge=True,
    )
    if not res["verification"]["matches"]:
        raise RuntimeError(f"Failed to restore Flagsmith Dev #503 split: {res['verification']}")
    return res


async def _open_isolated_demo_pr(
    gh: GitHubClient,
    *,
    run_idx: int,
    run_tag: str,
) -> dict[str, Any]:
    """Create an isolated feature branch (never main) with a harmless feature + unit test PR."""
    branch_name = f"demo/phase1-proof-r{run_idx}-{run_tag}"
    ref_resp = await gh._request("GET", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/ref/heads/main")
    base_sha = str(ref_resp.json()["object"]["sha"])
    commit_resp = await gh._request("GET", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/commits/{base_sha}")
    base_tree_sha = str(commit_resp.json()["tree"]["sha"])

    await gh._request(
        "POST",
        f"/repos/{REPO_OWNER}/{REPO_NAME}/git/refs",
        json_body={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        retry_safe=False,
    )

    feature_path = f"demo-app/backend/app/phase1_demo_promo_r{run_idx}.py"
    test_path = f"demo-app/tests/test_phase1_demo_promo_r{run_idx}.py"
    feature_code = (
        f'"""Harmless Phase 1 live proof demo promo helper (Run {run_idx})."""\n\n'
        "def apply_express_checkout_promo(\n"
        "    cart_total: float,\n"
        "    promo_code: str | None = None,\n"
        "    payment_method: str = 'card',\n"
        ") -> dict[str, float | str]:\n"
        "    # Unguarded checkout promo path intentionally flagged for Flagsmith gating.\n"
        "    discount = 0.0\n"
        "    if promo_code == 'EXPRESS15' and payment_method in {'apple_pay', 'novapay_wallet'}:\n"
        "        discount = round(cart_total * 0.15, 2)\n"
        "    final_total = round(max(cart_total - discount, 0.0), 2)\n"
        "    return {'discount': discount, 'final_total': final_total, 'payment_gateway': 'express_v2'}\n"
    )
    test_code = (
        f'"""Unit test for Phase 1 live proof demo promo helper (Run {run_idx})."""\n\n'
        f"from app.phase1_demo_promo_r{run_idx} import apply_express_checkout_promo\n\n"
        "def test_express_promo_discount() -> None:\n"
        "    out = apply_express_checkout_promo(100.0, promo_code='EXPRESS15', payment_method='apple_pay')\n"
        "    assert out['discount'] == 15.0\n"
        "    assert out['final_total'] == 85.0\n"
    )

    tree_entries = []
    for path, content in ((feature_path, feature_code), (test_path, test_code)):
        blob_resp = await gh._request(
            "POST",
            f"/repos/{REPO_OWNER}/{REPO_NAME}/git/blobs",
            json_body={"content": content, "encoding": "utf-8"},
            retry_safe=False,
        )
        tree_entries.append(
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": str(blob_resp.json()["sha"]),
            }
        )

    tree_resp = await gh._request(
        "POST",
        f"/repos/{REPO_OWNER}/{REPO_NAME}/git/trees",
        json_body={"base_tree": base_tree_sha, "tree": tree_entries},
        retry_safe=False,
    )
    new_tree_sha = str(tree_resp.json()["sha"])

    new_commit_resp = await gh._request(
        "POST",
        f"/repos/{REPO_OWNER}/{REPO_NAME}/git/commits",
        json_body={
            "message": f"demo(phase1-proof): add unguarded express promo helper (run {run_idx})",
            "tree": new_tree_sha,
            "parents": [base_sha],
        },
        retry_safe=False,
    )
    new_commit_sha = str(new_commit_resp.json()["sha"])

    await gh._request(
        "PATCH",
        f"/repos/{REPO_OWNER}/{REPO_NAME}/git/refs/heads/{branch_name}",
        json_body={"sha": new_commit_sha, "force": False},
        retry_safe=False,
    )

    pr_resp = await gh._request(
        "POST",
        f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls",
        json_body={
            "title": f"[Phase 1 Live Proof Run {run_idx}] Express checkout promo helper ({run_tag})",
            "body": (
                "Automated ephemeral demo PR created by `phase1_live_proof.py`.\n\n"
                "Introduces an unguarded express checkout promo payment path for PR risk analysis, "
                "7-dimension risk scoring, and Flagsmith opportunity verification. Closed & deleted after run."
            ),
            "head": branch_name,
            "base": "main",
            "draft": False,
        },
        retry_safe=False,
    )
    pr_json = pr_resp.json()
    return {
        "pr_number": int(pr_json["number"]),
        "html_url": str(pr_json["html_url"]),
        "branch_name": branch_name,
        "head_sha": new_commit_sha,
        "base_main_sha": base_sha,
    }


async def _close_and_delete_demo_pr(
    gh: GitHubClient,
    *,
    pr_number: int,
    branch_name: str,
) -> dict[str, Any]:
    """Close temporary GitHub PR and delete its temporary branch."""
    closed_resp = await gh._request(
        "PATCH",
        f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}",
        json_body={"state": "closed"},
        retry_safe=False,
    )
    del_status = None
    try:
        del_resp = await gh._request(
            "DELETE",
            f"/repos/{REPO_OWNER}/{REPO_NAME}/git/refs/heads/{branch_name}",
            retry_safe=False,
        )
        del_status = del_resp.status_code
    except Exception as exc:  # noqa: BLE001
        del_status = f"error:{exc}"
    return {
        "pr_number": pr_number,
        "pr_state": closed_resp.json().get("state"),
        "branch_deleted": branch_name,
        "branch_delete_status": del_status,
    }


async def _assign_live_flagsmith_participants(
    fs: FlagsmithClient,
    *,
    dev_env_key: str,
    prefix: str,
    min_per_arm: int,
) -> tuple[list[str], list[str], int]:
    """Evaluate real Flagsmith Edge identities on Development #120019 until both arms >= min_per_arm."""
    control_ids: list[str] = []
    treatment_ids: list[str] = []
    total_evaluated = 0
    sem = asyncio.Semaphore(18)

    async def eval_one(idx: int) -> tuple[str, str | None]:
        uid = f"{prefix}_{idx:04d}"
        async with sem:
            res = await fs.evaluate_identity_detailed(dev_env_key, identifier=uid)
        flag_info = res["flags"].get(TARGET_FLAG_KEY) or {}
        val = str(flag_info.get("value") or "")
        return uid, val

    batch_start = 1
    while len(control_ids) < min_per_arm or len(treatment_ids) < min_per_arm:
        batch_size = max(40, (min_per_arm - min(len(control_ids), len(treatment_ids))) * 4)
        tasks = [eval_one(i) for i in range(batch_start, batch_start + batch_size)]
        results = await asyncio.gather(*tasks)
        batch_start += batch_size
        total_evaluated += len(results)
        for uid, val in results:
            if val == CONTROL_VAR and len(control_ids) < min_per_arm:
                control_ids.append(uid)
            elif val == TREATMENT_VAR and len(treatment_ids) < min_per_arm:
                treatment_ids.append(uid)
        if batch_start > 2500:
            raise RuntimeError(
                f"Exceeded safety cap evaluating Flagsmith identities (ctrl={len(control_ids)}, trt={len(treatment_ids)})."
            )

    return control_ids, treatment_ids, total_evaluated


def _build_cohort_events(
    *,
    control_ids: list[str],
    treatment_ids: list[str],
    ctrl_conv_count: int,
    ctrl_err_count: int,
    trt_conv_count: int,
    trt_err_count: int,
    scenario_label: str,
    run_tag: str,
) -> list[dict[str, Any]]:
    """Build deterministic schema-compliant events for /api/v1/events/ingest."""
    now = datetime.now(UTC)
    events: list[dict[str, Any]] = []

    def add_arm(uids: list[str], var_key: str, conv_n: int, err_n: int) -> None:
        n = len(uids)
        for idx, uid in enumerate(uids):
            if idx < err_n:
                outcome = "payment_failed"
            elif idx < err_n + conv_n:
                outcome = "checkout_completed"
            else:
                outcome = "checkout_abandoned"

            base_t = now - timedelta(seconds=(n - idx) * 2 + 30)
            sess = f"chk_{run_tag}_{scenario_label}_{uid}"
            cart_total = round(75.0 + (idx % 9) * 15.0, 2)

            for step_offset, ev_type in (
                (0, "feature_variation_assigned"),
                (5, "checkout_started"),
                (12, outcome),
            ):
                basis = f"{TARGET_FLAG_KEY}|{ev_type}|{uid}|{sess}|{scenario_label}"
                ev_id = "evt_" + hashlib.sha256(basis.encode()).hexdigest()[:24]
                events.append(
                    {
                        "event_id": ev_id,
                        "flag_key": TARGET_FLAG_KEY,
                        "environment_name": TARGET_ENV,
                        "variation_key": var_key,
                        "event_type": ev_type,
                        "user_identifier": uid,
                        "session_key": sess,
                        "numeric_value": cart_total,
                        "properties": {
                            "source": "phase1_live_proof",
                            "scenario": scenario_label,
                            "flagsmith_env": TARGET_ENV,
                            "synthetic_test_traffic": True,
                        },
                        "occurred_at": (base_t + timedelta(seconds=step_offset))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                )

    add_arm(control_ids, CONTROL_VAR, ctrl_conv_count, ctrl_err_count)
    add_arm(treatment_ids, TREATMENT_VAR, trt_conv_count, trt_err_count)
    return events


async def _ingest_events(
    api_client: httpx.AsyncClient,
    *,
    ingest_key: str,
    events: list[dict[str, Any]],
) -> dict[str, int]:
    accepted = 0
    deduplicated = 0
    for i in range(0, len(events), 500):
        chunk = events[i : i + 500]
        resp = await api_client.post(
            "/api/v1/events/ingest",
            headers={"X-Ingest-Key": ingest_key},
            json={"events": chunk},
        )
        resp.raise_for_status()
        body = resp.json()
        accepted += int(body["accepted"])
        deduplicated += int(body["deduplicated"])
    return {"sent": len(events), "accepted": accepted, "deduplicated": deduplicated}


async def _exercise_novashop_pipeline(
    demo_client: httpx.AsyncClient,
    *,
    run_idx: int,
    run_tag: str,
) -> dict[str, Any]:
    """Exercise live NovaShop http://localhost:8001 health, checkout, and events/track."""
    h = await demo_client.get("/api/health")
    h.raise_for_status()
    health = h.json()
    if not health.get("flagsmith_connected"):
        raise RuntimeError(f"NovaShop Flagsmith not connected: {health}")

    checkout_results = []
    for idx in range(4):
        uid = f"shopper_p1_r{run_idx}_{idx}"
        r = await demo_client.post(
            "/api/checkout",
            json={
                "user_id": uid,
                "cart_total": 129.99,
                "payment_method": "card",
                "environment_name": TARGET_ENV,
            },
        )
        r.raise_for_status()
        checkout_results.append(r.json()["status"])

    track_resp = await demo_client.post(
        "/api/events/track",
        json={
            "user_id": f"shopper_p1_r{run_idx}_track",
            "event_type": "checkout_started",
            "flag_key": TARGET_FLAG_KEY,
            "variation_key": CONTROL_VAR,
            "sku": "NOVA-PRO-1",
        },
    )
    track_resp.raise_for_status()
    return {
        "novashop_health": health,
        "checkout_statuses": checkout_results,
        "events_track_delivery": track_resp.json(),
    }


async def execute_single_run(
    *,
    run_idx: int,
    api_client: httpx.AsyncClient,
    demo_client: httpx.AsyncClient,
    fs: FlagsmithClient,
    gh: GitHubClient,
    session_factory: Any,
    project_id: int,
    dev_env_key: str,
    prod_env_key: str,
    feature_id: int,
    ingest_key: str,
    op_user: dict[str, Any],
    op_headers: dict[str, str],
    rev_user: dict[str, Any],
    rev_headers: dict[str, str],
    repo_id: str,
    exp_db_id: uuid.UUID,
    prod_baseline: dict[str, Any],
) -> dict[str, Any]:
    run_tag = uuid.uuid4().hex[:6]
    run_report: dict[str, Any] = {"run_index": run_idx, "run_tag": run_tag}

    # Pre-run clean scoped state
    await _restore_flagsmith_dev_503(
        fs, project_id=project_id, dev_env_key=dev_env_key, feature_id=feature_id
    )
    await _clear_scoped_dev_events_and_reset_exp(session_factory, exp_db_id=exp_db_id)

    sync_resp = await api_client.post(
        f"/api/v1/feature-flags/sync?project_id={project_id}", headers=op_headers
    )
    sync_resp.raise_for_status()
    sync_body = sync_resp.json()
    run_report["initial_sync"] = {
        "flags_synced": sync_body["synced_count"],
        "experiments_synced": sync_body.get("experiments_synced_count"),
    }

    # --- Part (a): Real Isolated GitHub Demo PR + Sync + Analysis + 7-Dim Risk ---
    demo_pr = await _open_isolated_demo_pr(gh, run_idx=run_idx, run_tag=run_tag)
    try:
        pr_sync_resp = await api_client.post(
            f"/api/v1/repositories/{repo_id}/pull-requests/sync?state=open",
            headers=op_headers,
        )
        pr_sync_resp.raise_for_status()
        synced_prs = pr_sync_resp.json()["items"]
        matched_pr = next(
            (p for p in synced_prs if p["github_pr_number"] == demo_pr["pr_number"]),
            None,
        )
        if matched_pr is None:
            raise RuntimeError(f"Synced PR #{demo_pr['pr_number']} not found in DB.")

        analyze_resp = await api_client.post(
            f"/api/v1/pull-requests/{matched_pr['id']}/analyze",
            headers=op_headers,
        )
        analyze_resp.raise_for_status()
        detail_resp = await api_client.get(
            f"/api/v1/pull-requests/{matched_pr['id']}",
            headers=op_headers,
        )
        detail_resp.raise_for_status()
        detail = detail_resp.json()
        risk_assess = (detail.get("diff_summary") or {}).get("risk_assessment") or {}
        if not risk_assess or len(risk_assess.get("dimensions") or []) != 7:
            raise RuntimeError(f"Expected 7-dim risk assessment, got: {risk_assess}")

        run_report["part_a_pr_analysis"] = {
            "pr_id": matched_pr["id"],
            "github_pr_number": demo_pr["pr_number"],
            "html_url": demo_pr["html_url"],
            "branch_name": demo_pr["branch_name"],
            "head_sha": detail["head_sha"],
            "risk_score": detail["risk_score"],
            "risk_level": detail["risk_level"],
            "assessment_key": risk_assess.get("assessment_key"),
            "seven_dim_score": risk_assess.get("overall_risk_score"),
            "dimensions_count": len(risk_assess.get("dimensions") or []),
            "confirmed_risks_count": len(detail.get("confirmed_risks") or []),
            "flag_opportunities_count": len(detail.get("flag_opportunities") or []),
            "flag_opportunities": detail.get("flag_opportunities") or [],
            "dx_status": (detail.get("diff_summary") or {}).get("dx_status"),
        }

        # --- Part (b): NovaShop + Live Flagsmith #503 across Scenario C, B, A ---
        novashop_check = await _exercise_novashop_pipeline(
            demo_client, run_idx=run_idx, run_tag=run_tag
        )
        run_report["novashop_live_check"] = novashop_check

        # Scenario C: Insufficient Data (< 100/arm -> 25/arm)
        await _clear_scoped_dev_events_and_reset_exp(session_factory, exp_db_id=exp_db_id)
        c_ctrl, c_trt, c_evals = await _assign_live_flagsmith_participants(
            fs, dev_env_key=dev_env_key, prefix=f"p1r{run_idx}c_{run_tag}", min_per_arm=25
        )
        c_events = _build_cohort_events(
            control_ids=c_ctrl,
            treatment_ids=c_trt,
            ctrl_conv_count=3,
            ctrl_err_count=0,
            trt_conv_count=7,
            trt_err_count=0,
            scenario_label="scenario_c",
            run_tag=run_tag,
        )
        c_ingest = await _ingest_events(api_client, ingest_key=ingest_key, events=c_events)
        c_ana = (await api_client.get(f"/api/v1/experiments/{exp_db_id}/analysis", headers=op_headers)).json()
        c_rec = (await api_client.get(f"/api/v1/experiments/{exp_db_id}/recommendation", headers=op_headers)).json()
        c_prom = await api_client.post(
            f"/api/v1/experiments/{exp_db_id}/promote-winner?winning_variation={TREATMENT_VAR}",
            headers=op_headers,
        )
        if c_ana["experiment_outcome"] != "INSUFFICIENT_DATA" or c_prom.status_code != 409:
            raise RuntimeError(
                f"Scenario C assertion failed: outcome={c_ana['experiment_outcome']}, promote={c_prom.status_code}"
            )
        run_report["scenario_c_insufficient_data"] = {
            "edge_identities_evaluated": c_evals,
            "control_participants": c_ana["control"]["exposures"],
            "treatment_participants": c_ana["treatment"]["exposures"],
            "ingest": c_ingest,
            "experiment_outcome": c_ana["experiment_outcome"],
            "is_adequate": c_ana["sample_adequacy"]["is_adequate"],
            "recommendation": c_rec["recommendation"],
            "promote_blocked_status": c_prom.status_code,
            "promote_blocked_code": c_prom.json().get("error", {}).get("code"),
        }

        # Scenario B: Guardrail Breach (>= 100/arm, high payment_failed -> blocked promotion)
        await _clear_scoped_dev_events_and_reset_exp(session_factory, exp_db_id=exp_db_id)
        b_ctrl, b_trt, b_evals = await _assign_live_flagsmith_participants(
            fs, dev_env_key=dev_env_key, prefix=f"p1r{run_idx}b_{run_tag}", min_per_arm=110
        )
        b_events = _build_cohort_events(
            control_ids=b_ctrl,
            treatment_ids=b_trt,
            ctrl_conv_count=13,
            ctrl_err_count=1,
            trt_conv_count=15,
            trt_err_count=18,
            scenario_label="scenario_b",
            run_tag=run_tag,
        )
        b_ingest = await _ingest_events(api_client, ingest_key=ingest_key, events=b_events)
        b_ana = (await api_client.get(f"/api/v1/experiments/{exp_db_id}/analysis", headers=op_headers)).json()
        b_rec = (await api_client.get(f"/api/v1/experiments/{exp_db_id}/recommendation", headers=op_headers)).json()
        b_prom = await api_client.post(
            f"/api/v1/experiments/{exp_db_id}/promote-winner?winning_variation={TREATMENT_VAR}",
            headers=op_headers,
        )
        if b_ana["experiment_outcome"] != "GUARDRAIL_BREACHED" or b_prom.status_code != 409:
            raise RuntimeError(
                f"Scenario B assertion failed: outcome={b_ana['experiment_outcome']}, promote={b_prom.status_code}"
            )
        run_report["scenario_b_guardrail_breach"] = {
            "edge_identities_evaluated": b_evals,
            "control_participants": b_ana["control"]["exposures"],
            "treatment_participants": b_ana["treatment"]["exposures"],
            "control_error_rate": b_ana["guardrails"]["payment_failed"]["control_rate"],
            "treatment_error_rate": b_ana["guardrails"]["payment_failed"]["treatment_rate"],
            "ingest": b_ingest,
            "experiment_outcome": b_ana["experiment_outcome"],
            "guardrail_breached": b_ana["guardrails"]["payment_failed"]["breached"],
            "recommendation": b_rec["recommendation"],
            "promote_blocked_status": b_prom.status_code,
            "promote_blocked_code": b_prom.json().get("error", {}).get("code"),
        }

        # Scenario A: Winning Variant (>= 120/arm, strong conversion lift, safe guardrails)
        await _clear_scoped_dev_events_and_reset_exp(session_factory, exp_db_id=exp_db_id)
        a_ctrl, a_trt, a_evals = await _assign_live_flagsmith_participants(
            fs, dev_env_key=dev_env_key, prefix=f"p1r{run_idx}a_{run_tag}", min_per_arm=120
        )
        a_events = _build_cohort_events(
            control_ids=a_ctrl,
            treatment_ids=a_trt,
            ctrl_conv_count=14,
            ctrl_err_count=1,
            trt_conv_count=34,
            trt_err_count=1,
            scenario_label="scenario_a",
            run_tag=run_tag,
        )
        a_ingest = await _ingest_events(api_client, ingest_key=ingest_key, events=a_events)

        # --- Part (c): Full 12-Step Winner Promotion Workflow ---
        pre_dev_states = await fs.get_environment_feature_states(dev_env_key, feature_id=feature_id)
        pre_dev_snap = _snapshot_flag_state(pre_dev_states)

        refresh_resp = await api_client.post(
            f"/api/v1/experiments/{exp_db_id}/refresh-flagsmith-results", headers=op_headers
        )
        refresh_resp.raise_for_status()

        a_ana = (await api_client.get(f"/api/v1/experiments/{exp_db_id}/analysis", headers=op_headers)).json()
        a_rec = (await api_client.get(f"/api/v1/experiments/{exp_db_id}/recommendation", headers=op_headers)).json()
        if (
            a_ana["experiment_outcome"] != "VARIANT_WINS"
            or not a_ana["sample_adequacy"]["is_adequate"]
            or a_rec["recommendation"] != "Expand Rollout"
            or not a_rec["readiness"]["is_ready_for_winner_decision"]
        ):
            raise RuntimeError(
                f"Scenario A readiness failed: outcome={a_ana['experiment_outcome']}, "
                f"rec={a_rec['recommendation']}, readiness={a_rec['readiness']}"
            )

        prom_req = await api_client.post(
            f"/api/v1/experiments/{exp_db_id}/promote-winner?winning_variation={TREATMENT_VAR}",
            headers=op_headers,
        )
        if prom_req.status_code != 202:
            raise RuntimeError(f"Expected 202 approval_required, got {prom_req.status_code}: {prom_req.text}")
        prom_body = prom_req.json()
        approval = prom_body["approval"]
        approval_id = approval["id"]

        # Non-self-approval check: operator requested, independent reviewer approves
        if approval["requested_by_id"] != op_user["id"]:
            raise RuntimeError("Unexpected approval requested_by_id mismatch.")

        approve_resp = await api_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=rev_headers,
            json={"comment": f"Phase 1 live proof Run {run_idx} independent reviewer approval"},
        )
        approve_resp.raise_for_status()
        approved_data = approve_resp.json()["approval"]
        if (
            approved_data["status"] != "executed"
            or approved_data["decided_by_id"] != rev_user["id"]
            or approved_data["decided_by_id"] == approved_data["requested_by_id"]
        ):
            raise RuntimeError(f"Approval execution/non-self-approval check failed: {approved_data}")

        # Single-use replay protection check (re-approving must fail with 409)
        replay_resp = await api_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=rev_headers,
            json={"comment": "Replay attempt"},
        )
        if replay_resp.status_code != 409:
            raise RuntimeError(f"Expected 409 on approval replay, got {replay_resp.status_code}")

        # Live Flagsmith Dev #120019 readback verification (Management + Edge)
        post_mgmt_verify = await fs.verify_live_state(
            env_key=dev_env_key,
            feature_id=feature_id,
            flag_key=TARGET_FLAG_KEY,
            project_id=project_id,
            expected_enabled=True,
            expected_mv_allocations={CONTROL_VAR: 0, TREATMENT_VAR: 100, THIRD_VAR: 0},
            verify_edge=True,
        )
        if not post_mgmt_verify["matches"]:
            raise RuntimeError(f"Live Flagsmith post-promotion readback mismatch: {post_mgmt_verify}")

        # Edge API is eventually consistent with Management API after v2 version publish; poll briefly for convergence
        edge_winner_val: str | None = None
        edge_attempts = 0
        for edge_attempt in range(1, 11):
            edge_attempts = edge_attempt
            edge_probe = await fs.evaluate_identity_detailed(
                dev_env_key, identifier=f"p1_verify_winner_r{run_idx}_{run_tag}_{edge_attempt}"
            )
            edge_winner_val = (edge_probe["flags"].get(TARGET_FLAG_KEY) or {}).get("value")
            if edge_winner_val == TREATMENT_VAR:
                break
            await asyncio.sleep(2.0)
        if edge_winner_val != TREATMENT_VAR:
            raise RuntimeError(
                f"Expected Edge readback '{TREATMENT_VAR}' after {edge_attempts} attempts, got '{edge_winner_val}'"
            )

        # Audit log verification
        audit_resp = await api_client.get(
            f"/api/v1/audit-logs?resource_id={exp_db_id}&limit=20",
            headers=op_headers,
        )
        audit_resp.raise_for_status()
        audit_actions = [item["event_action"] for item in audit_resp.json()["items"]]
        if "experiment.winner_promotion_requested" not in audit_actions or "approval.executed" not in audit_actions:
            raise RuntimeError(f"Missing expected audit log actions: {audit_actions}")

        run_report["scenario_a_and_12_step_promotion"] = {
            "edge_identities_evaluated": a_evals,
            "control_participants": a_ana["control"]["exposures"],
            "control_conversions": a_ana["control"]["conversions"],
            "treatment_participants": a_ana["treatment"]["exposures"],
            "treatment_conversions": a_ana["treatment"]["conversions"],
            "prob_treatment_beats_control": round(a_ana["comparison"]["prob_treatment_beats_control"], 6),
            "expected_loss_choose_treatment": round(a_ana["comparison"]["expected_loss_choose_treatment"], 6),
            "experiment_outcome": a_ana["experiment_outcome"],
            "recommendation": a_rec["recommendation"],
            "ingest": a_ingest,
            "flagsmith_refresh_status": refresh_resp.json().get("refresh", {}).get("status_code"),
            "pre_promotion_dev_snapshot": pre_dev_snap,
            "approval_id": approval_id,
            "policy_category": approved_data["policy_category"],
            "action_hash": approved_data["action_hash"],
            "requested_by_id": approved_data["requested_by_id"],
            "decided_by_id": approved_data["decided_by_id"],
            "approval_status": approved_data["status"],
            "single_use_replay_status": replay_resp.status_code,
            "live_flagsmith_readback": post_mgmt_verify["actual_state"],
            "edge_readback_value": edge_winner_val,
            "audit_actions_found": audit_actions[:6],
        }

    finally:
        # --- Part (d): Restore Flagsmith Dev #503 clean state & cleanup GitHub PR/branch ---
        restore_res = await _restore_flagsmith_dev_503(
            fs, project_id=project_id, dev_env_key=dev_env_key, feature_id=feature_id
        )
        gh_cleanup = await _close_and_delete_demo_pr(
            gh, pr_number=demo_pr["pr_number"], branch_name=demo_pr["branch_name"]
        )
        # Update synced local PR state to closed so DB matches GitHub
        async with session_factory() as session:
            await session.execute(
                update(PullRequest)
                .where(PullRequest.github_pr_number == demo_pr["pr_number"])
                .values(state=PullRequestState.CLOSED)
            )
            await session.commit()

        # Verify Production #120020 and main branch were NEVER touched
        prod_after = _snapshot_flag_state(
            await fs.get_environment_feature_states(prod_env_key, feature_id=feature_id)
        )
        if prod_after != prod_baseline:
            raise RuntimeError(
                f"PRODUCTION MUTATION DETECTED! Before={prod_baseline}, After={prod_after}"
            )
        main_after = str(
            (await gh._request("GET", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/ref/heads/main")).json()["object"]["sha"]
        )
        if main_after != demo_pr["base_main_sha"]:
            raise RuntimeError(f"MAIN BRANCH MUTATED! Before={demo_pr['base_main_sha']}, After={main_after}")

        run_report["recovery_and_isolation"] = {
            "flagsmith_dev_restored_matches": restore_res["verification"]["matches"],
            "flagsmith_dev_restored_mv": restore_res["verification"]["actual_state"]["mv_allocations"],
            "github_cleanup": gh_cleanup,
            "production_unchanged": True,
            "main_branch_unchanged": True,
        }

    return run_report


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    _assert_non_prod_environment(args.environment)
    cfg = get_settings()
    db_url = _resolve_host_db_url(cfg.effective_database_url)
    os.environ["DATABASE_URL"] = db_url
    get_settings.cache_clear()
    cfg = get_settings()

    session_factory = get_session_factory()
    fs = FlagsmithClient(
        cfg.FLAGSMITH_ORG_TOKEN,
        api_url=cfg.FLAGSMITH_API_URL,
        edge_api_url=cfg.FLAGSMITH_EDGE_API_URL,
    )
    gh = GitHubClient(cfg.GITHUB_PAT)
    project_id = int(cfg.flagsmith_project_id_int or 56055)
    dev_env_key = cfg.FLAGSMITH_DEV_ENV_KEY
    prod_env_key = cfg.FLAGSMITH_PROD_ENV_KEY
    if not dev_env_key or not prod_env_key or not cfg.FLAGSMITH_ORG_TOKEN or not cfg.GITHUB_PAT:
        raise RuntimeError("Missing required Flagsmith or GitHub credentials in environment.")

    flag_obj = await fs.find_flag_by_key(project_id, TARGET_FLAG_KEY)
    if flag_obj is None:
        raise RuntimeError(f"Flag '{TARGET_FLAG_KEY}' not found in Flagsmith project {project_id}.")
    feature_id = int(flag_obj["id"])

    prod_baseline = _snapshot_flag_state(
        await fs.get_environment_feature_states(prod_env_key, feature_id=feature_id)
    )

    try:
        async with (
            httpx.AsyncClient(base_url=args.api_url.rstrip("/"), timeout=60.0) as api_client,
            httpx.AsyncClient(base_url=args.demo_url.rstrip("/"), timeout=30.0) as demo_client,
        ):
            op_user, op_token, rev_user, rev_token = await _ensure_reviewer_and_tokens(
                api_client,
                operator_email=cfg.AUTH_OPERATOR_EMAIL or "operator@releaseguard.local",
                operator_password=cfg.AUTH_OPERATOR_PASSWORD,
            )
            op_headers = {"Authorization": f"Bearer {op_token}"}
            rev_headers = {"Authorization": f"Bearer {rev_token}"}

            repo_resp = await api_client.post(
                "/api/v1/repositories",
                headers=op_headers,
                json={"owner": REPO_OWNER, "name": REPO_NAME, "flagsmith_project_id": project_id},
            )
            repo_resp.raise_for_status()
            repo_id = repo_resp.json()["id"]

            sync_resp = await api_client.post(
                f"/api/v1/feature-flags/sync?project_id={project_id}", headers=op_headers
            )
            sync_resp.raise_for_status()

            async with session_factory() as session:
                exp_row = (
                    await session.execute(
                        select(Experiment)
                        .where(Experiment.flagsmith_experiment_id == TARGET_EXP_ID_FS)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if exp_row is None:
                    raise RuntimeError(f"Synced Flagsmith experiment #{TARGET_EXP_ID_FS} not found in DB.")
                exp_db_id = exp_row.id

            runs: list[dict[str, Any]] = []
            for r_idx in range(1, args.runs + 1):
                t0 = time.perf_counter()
                res = await execute_single_run(
                    run_idx=r_idx,
                    api_client=api_client,
                    demo_client=demo_client,
                    fs=fs,
                    gh=gh,
                    session_factory=session_factory,
                    project_id=project_id,
                    dev_env_key=dev_env_key,
                    prod_env_key=prod_env_key,
                    feature_id=feature_id,
                    ingest_key=cfg.EVENT_INGEST_KEY,
                    op_user=op_user,
                    op_headers=op_headers,
                    rev_user=rev_user,
                    rev_headers=rev_headers,
                    repo_id=repo_id,
                    exp_db_id=exp_db_id,
                    prod_baseline=prod_baseline,
                )
                res["duration_seconds"] = round(time.perf_counter() - t0, 2)
                runs.append(res)

            # Confirm zero open PRs remain on GitHub
            open_prs = await gh.list_pull_requests(REPO_OWNER, REPO_NAME, state="open")

            summary = {
                "executed_at": datetime.now(UTC).isoformat(),
                "environment": TARGET_ENV,
                "flagsmith_project_id": project_id,
                "flagsmith_experiment_id": TARGET_EXP_ID_FS,
                "flag_key": TARGET_FLAG_KEY,
                "operator_user": {"id": op_user["id"], "email": op_user["email"], "role": op_user["role"]},
                "reviewer_user": {"id": rev_user["id"], "email": rev_user["email"], "role": rev_user["role"]},
                "open_github_prs_after": len(open_prs),
                "runs_completed": len(runs),
                "runs": runs,
            }
            if args.output_json:
                out_path = Path(args.output_json)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return summary
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute 3 repeatable Phase 1 Live Proof runs (Dev only).")
    parser.add_argument("--environment", default="development", help="Must be 'development'.")
    parser.add_argument("--runs", type=int, default=3, help="Number of complete repeatable runs (default: 3).")
    parser.add_argument("--api-url", default="http://localhost:8000", help="ReleaseGuard API base URL.")
    parser.add_argument("--demo-url", default="http://localhost:8001", help="NovaShop Demo API base URL.")
    parser.add_argument("--output-json", default="", help="Optional path to write JSON proof output.")
    args = parser.parse_args()

    result = asyncio.run(main_async(args))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
