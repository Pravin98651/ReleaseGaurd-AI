"""Repeatable Phase 2 Live Proof runner for Feature-Flag Cleanup Lifecycle (Dev only).

Targets Docker Compose (http://localhost:8000) + real GitHub (Pravin98651/ReleaseGaurd-AI)
+ real Flagsmith (#56055, Development) and NEVER mutates GitHub ``main`` or Flagsmith Production.

Demonstrates:
  1. Post-promotion dependency check: blocked when partial/active dependencies exist
     (PROMOTED + blockers) vs safe -> CLEANUP_RECOMMENDED.
  2. Real Cleanup Draft PR creation for ``checkout_v2`` on
     ``Pravin98651/ReleaseGaurd-AI`` (real AST/TSX diff, ``draft=True``), verified via
     GitHub API and then cleanly closed + branch deleted so ``OPEN_GITHUB_PRS: 0``.
  3. Pre-retirement gate blocking (409 CLEANUP_PRECONDITION_UNMET) when code references
     remain and no cleanup PR is merged, then passing (202 Category C approval in
     ``development``) when ready, followed by independent reviewer approval ->
     Flagsmith archive execution -> ``CLEANED_UP`` + clean ephemeral teardown.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, select, update

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.tools.flag_tools import transition_flag_post_promotion
from app.core.config import get_settings
from app.core.database import dispose_engine, get_session_factory
from app.integrations.flagsmith_client import FlagsmithClient
from app.integrations.github_client import GitHubClient
from app.models import Approval, CodeChange, FeatureFlag, FlagLifecycleStatus

REPO_OWNER = "Pravin98651"
REPO_NAME = "ReleaseGaurd-AI"
TARGET_FLAG_KEY = "checkout_v2"
TARGET_ENV = "development"
REVIEWER_EMAIL = "reviewer@releaseguard.local"
REVIEWER_NAME = "ReleaseGuard Reviewer"
REVIEWER_PASSWORD = os.environ.get("PHASE1_REVIEWER_PASSWORD", "Phase1Reviewer!2026")


def _resolve_host_db_url(raw_url: str) -> str:
    if "@postgres:5432" in raw_url and not Path("/.dockerenv").exists():
        return raw_url.replace("@postgres:5432", "@localhost:5432")
    return raw_url


def _assert_non_prod(env_name: str) -> None:
    norm = env_name.strip().lower()
    if norm in {"prod", "production", "live", "prd"} or "prod" in norm or norm != "development":
        raise RuntimeError(
            f"BLOCKED: phase2_cleanup_proof.py strictly permits 'development' only (got '{env_name}')."
        )


def _snapshot_flag_state(states: list[dict[str, Any]]) -> dict[str, Any]:
    base = next((s for s in states if s.get("feature_segment") is None), None) or (
        states[0] if states else {}
    )
    mv_vals = {
        str(m.get("multivariate_feature_option")): float(m.get("percentage_allocation", 0.0))
        for m in (base.get("multivariate_feature_state_values") or [])
    }
    return {
        "enabled": bool(base.get("enabled", False)),
        "feature_state_value": base.get("feature_state_value"),
        "mv_allocations_by_option_id": mv_vals,
    }


async def _ensure_reviewer_and_tokens(
    api_client: httpx.AsyncClient,
    *,
    operator_email: str,
    operator_password: str,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
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


async def _close_and_delete_pr(
    gh: GitHubClient,
    *,
    pr_number: int,
    branch_name: str | None,
) -> dict[str, Any]:
    closed_resp = await gh._request(
        "PATCH",
        f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}",
        json_body={"state": "closed"},
        retry_safe=False,
    )
    del_status: Any = None
    if branch_name:
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


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    _assert_non_prod(args.environment)
    cfg = get_settings()
    os.environ["DATABASE_URL"] = _resolve_host_db_url(cfg.effective_database_url)
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
    prod_env_key = cfg.FLAGSMITH_PROD_ENV_KEY
    if not prod_env_key or not cfg.FLAGSMITH_ORG_TOKEN or not cfg.GITHUB_PAT:
        raise RuntimeError("Missing required Flagsmith or GitHub credentials in environment.")

    target_fs_flag = await fs.find_flag_by_key(project_id, TARGET_FLAG_KEY)
    if target_fs_flag is None:
        raise RuntimeError(f"Flag '{TARGET_FLAG_KEY}' not found in Flagsmith project {project_id}.")
    target_fs_id = int(target_fs_flag["id"])
    prod_baseline = _snapshot_flag_state(
        await fs.get_environment_feature_states(prod_env_key, feature_id=target_fs_id)
    )
    main_before = str(
        (await gh._request("GET", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/ref/heads/main")).json()[
            "object"
        ]["sha"]
    )

    run_tag = uuid.uuid4().hex[:8]
    ephemeral_key = f"phase2_cleanup_proof_{run_tag}"
    ephemeral_fs_id: int | None = None
    ephemeral_db_id: uuid.UUID | None = None
    created_pr_number: int | None = None
    created_branch_name: str | None = None
    created_code_change_id: str | None = None
    target_flag_prev_status: FlagLifecycleStatus | None = None
    target_flag_prev_details: dict[str, Any] | None = None

    try:
        async with httpx.AsyncClient(base_url=args.api_url.rstrip("/"), timeout=240.0) as api_client:
            _op_user, op_token, _rev_user, rev_token = await _ensure_reviewer_and_tokens(
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

            # 1. Create ephemeral Flagsmith flag & sync to prove post-promotion + retirement flow
            created_fs = await fs.create_flag(
                project_id=project_id,
                key=ephemeral_key,
                description="Ephemeral Phase 2 cleanup lifecycle proof flag (auto-cleaned)",
            )
            ephemeral_fs_id = int(created_fs["id"])

            sync_resp = await api_client.post(
                f"/api/v1/feature-flags/sync?project_id={project_id}",
                headers=op_headers,
            )
            sync_resp.raise_for_status()

            async with session_factory() as session:
                target_flag = (
                    await session.execute(
                        select(FeatureFlag).where(FeatureFlag.key == TARGET_FLAG_KEY).limit(1)
                    )
                ).scalar_one()
                target_flag_id = target_flag.id
                target_flag_prev_status = target_flag.lifecycle_status
                target_flag_prev_details = dict(target_flag.lifecycle_details or {})

                eph_flag = (
                    await session.execute(
                        select(FeatureFlag).where(FeatureFlag.key == ephemeral_key).limit(1)
                    )
                ).scalar_one()
                ephemeral_db_id = eph_flag.id

                # 1a. Post-promotion blocked when partial env (<100%) exists
                eph_flag.env_states = {
                    "development": {"enabled": True, "rollout_percentage": 100},
                    "staging": {"enabled": True, "rollout_percentage": 25},
                }
                blocked_report = await transition_flag_post_promotion(
                    session,
                    eph_flag,
                    source="phase2_proof",
                    environment_name=TARGET_ENV,
                    winning_variation="one_page_checkout",
                )
                if blocked_report.safe_for_cleanup or eph_flag.lifecycle_status != FlagLifecycleStatus.PROMOTED:
                    raise RuntimeError(f"Expected PROMOTED with blockers, got {eph_flag.lifecycle_status}")

                # 1b. Post-promotion safe -> CLEANUP_RECOMMENDED
                eph_flag.env_states = {
                    "development": {"enabled": True, "rollout_percentage": 100},
                    "staging": {"enabled": True, "rollout_percentage": 100},
                }
                safe_report = await transition_flag_post_promotion(
                    session,
                    eph_flag,
                    source="phase2_proof",
                    environment_name=TARGET_ENV,
                    winning_variation="one_page_checkout",
                )
                if not safe_report.safe_for_cleanup or eph_flag.lifecycle_status != FlagLifecycleStatus.CLEANUP_RECOMMENDED:
                    raise RuntimeError(f"Expected CLEANUP_RECOMMENDED, got {eph_flag.lifecycle_status}")
                await session.commit()

            # 2. Scan repo flag health (>40 files) & open real Cleanup Draft PR for checkout_experience_v2
            scan_resp = await api_client.post(
                "/api/v1/flag-health/scan",
                headers=op_headers,
                json={"project_id": project_id, "repository_id": repo_id},
            )
            scan_resp.raise_for_status()

            pr_resp = await api_client.post(
                f"/api/v1/flag-health/{target_flag_id}/cleanup-pr",
                headers=op_headers,
                json={
                    "repository_id": repo_id,
                    "keep_branch": "enabled",
                    "open_draft_pr": True,
                },
            )
            if pr_resp.status_code not in (200, 202):
                raise RuntimeError(f"cleanup-pr failed: {pr_resp.status_code} {pr_resp.text}")
            pr_json = pr_resp.json()
            created_code_change_id = pr_json["data"]["id"]
            if pr_resp.status_code == 202:
                approval_id = pr_json["approval"]["id"]
                app_resp = await api_client.post(
                    f"/api/v1/approvals/{approval_id}/approve",
                    headers=rev_headers,
                    json={"comment": "Phase 2 live proof: approve cleanup Draft PR"},
                )
                app_resp.raise_for_status()
                raw_exec = app_resp.json()["approval"]["execution_result"] or {}
                exec_res = raw_exec.get("execution") if isinstance(raw_exec.get("execution"), dict) else raw_exec
                created_pr_number = int(exec_res["pr_number"])
                created_branch_name = exec_res.get("branch_name")
            else:
                created_pr_number = int(pr_json["data"]["draft_pr_number"])
                created_branch_name = pr_json["data"].get("draft_branch_name")

            live_pr = await gh.get_pull_request(REPO_OWNER, REPO_NAME, created_pr_number)
            if not live_pr.get("draft"):
                raise RuntimeError(f"Expected Draft PR (draft=True), got {live_pr}")

            # Close & delete temporary Draft PR immediately after verification
            gh_cleanup = await _close_and_delete_pr(
                gh, pr_number=created_pr_number, branch_name=created_branch_name
            )
            created_pr_number = None
            created_branch_name = None

            # 3. Pre-retirement gate: 409 when refs remain -> 202 Category C when refs=0 -> CLEANED_UP
            async with session_factory() as session:
                await session.execute(
                    update(FeatureFlag)
                    .where(FeatureFlag.id == ephemeral_db_id)
                    .values(code_references=[{"file_path": "demo-app/backend/app/main.py", "line_number": 1}])
                )
                await session.commit()

            gate_blocked = await api_client.post(
                f"/api/v1/flag-health/{ephemeral_db_id}/request-delete",
                headers=op_headers,
                json={
                    "action": "archive",
                    "environment_name": TARGET_ENV,
                    "reason": "Phase 2 gate test while refs remain",
                },
            )
            if gate_blocked.status_code != 409:
                raise RuntimeError(f"Expected 409 gate block, got {gate_blocked.status_code} {gate_blocked.text}")

            async with session_factory() as session:
                await session.execute(
                    update(FeatureFlag)
                    .where(FeatureFlag.id == ephemeral_db_id)
                    .values(code_references=[])
                )
                await session.commit()

            gate_ok = await api_client.post(
                f"/api/v1/flag-health/{ephemeral_db_id}/request-delete",
                headers=op_headers,
                json={
                    "action": "archive",
                    "environment_name": TARGET_ENV,
                    "reason": "Phase 2 proof: all references removed",
                },
            )
            if gate_ok.status_code != 202:
                raise RuntimeError(f"Expected 202 Category C approval, got {gate_ok.status_code} {gate_ok.text}")
            cat_c_approval = gate_ok.json()["approval"]
            if cat_c_approval["policy_category"] != "C" or cat_c_approval["environment_name"] != TARGET_ENV:
                raise RuntimeError(f"Unexpected Category C approval payload: {cat_c_approval}")

            cat_c_exec = await api_client.post(
                f"/api/v1/approvals/{cat_c_approval['id']}/approve",
                headers=rev_headers,
                json={"comment": "Phase 2 live proof: approve Category C flag archive"},
            )
            cat_c_exec.raise_for_status()
            executed_approval = cat_c_exec.json()["approval"]
            if executed_approval["status"] != "executed":
                raise RuntimeError(f"Expected executed approval, got {executed_approval}")

            async with session_factory() as session:
                final_eph = await session.get(FeatureFlag, ephemeral_db_id)
                final_status = final_eph.lifecycle_status.value if final_eph else None
                final_archived = final_eph.is_archived if final_eph else None

            if final_status != FlagLifecycleStatus.CLEANED_UP.value or not final_archived:
                raise RuntimeError(
                    f"Expected CLEANED_UP & archived=True, got status={final_status}, archived={final_archived}"
                )

            open_prs = await gh.list_pull_requests(REPO_OWNER, REPO_NAME, state="open")
            prod_after = _snapshot_flag_state(
                await fs.get_environment_feature_states(prod_env_key, feature_id=target_fs_id)
            )
            main_after = str(
                (await gh._request("GET", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/ref/heads/main")).json()[
                    "object"
                ]["sha"]
            )
            if prod_after != prod_baseline:
                raise RuntimeError("PRODUCTION MUTATION DETECTED!")
            if main_after != main_before:
                raise RuntimeError("MAIN BRANCH MUTATION DETECTED!")

            return {
                "executed_at": datetime.now(UTC).isoformat(),
                "environment": TARGET_ENV,
                "post_promotion": {
                    "blocked_status": FlagLifecycleStatus.PROMOTED.value,
                    "blocked_reasons": blocked_report.blockers,
                    "safe_status": FlagLifecycleStatus.CLEANUP_RECOMMENDED.value,
                },
                "cleanup_draft_pr": {
                    "pr_number": live_pr.get("number"),
                    "html_url": live_pr.get("html_url"),
                    "draft": live_pr.get("draft"),
                    "changed_files": live_pr.get("changed_files"),
                    "cleanup": gh_cleanup,
                },
                "pre_retirement_gate": {
                    "blocked_status_code": gate_blocked.status_code,
                    "ready_status_code": gate_ok.status_code,
                    "category_c_approval_id": cat_c_approval["id"],
                    "category_c_environment": cat_c_approval["environment_name"],
                    "approval_execution_status": executed_approval["status"],
                    "final_lifecycle_status": final_status,
                    "is_archived": final_archived,
                },
                "OPEN_GITHUB_PRS": len(open_prs),
                "production_unchanged": True,
                "main_branch_unchanged": True,
            }
    finally:
        if created_pr_number is not None:
            with contextlib.suppress(Exception):
                await _close_and_delete_pr(
                    gh, pr_number=created_pr_number, branch_name=created_branch_name
                )
        if ephemeral_fs_id is not None:
            with contextlib.suppress(Exception):
                await fs.delete_flag(project_id, ephemeral_fs_id)
        async with session_factory() as session:
            if target_flag_prev_status is not None:
                await session.execute(
                    update(FeatureFlag)
                    .where(FeatureFlag.key == TARGET_FLAG_KEY)
                    .values(
                        lifecycle_status=target_flag_prev_status,
                        lifecycle_details=target_flag_prev_details or {},
                    )
                )
            if created_code_change_id is not None:
                await session.execute(
                    delete(CodeChange).where(CodeChange.id == uuid.UUID(created_code_change_id))
                )
            if ephemeral_db_id is not None:
                await session.execute(
                    delete(Approval).where(Approval.target_resource_id == ephemeral_key)
                )
                await session.execute(
                    delete(FeatureFlag).where(FeatureFlag.id == ephemeral_db_id)
                )
            await session.commit()
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute Phase 2 Cleanup Lifecycle Live Proof (Dev only).")
    parser.add_argument("--environment", default="development", help="Must be 'development'.")
    parser.add_argument("--api-url", default="http://localhost:8000", help="ReleaseGuard API base URL.")
    parser.add_argument("--output-json", default="", help="Optional path to write JSON proof output.")
    args = parser.parse_args()

    result = asyncio.run(main_async(args))
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
