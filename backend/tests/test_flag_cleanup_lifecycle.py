"""Tests for Phase 2 Feature-Flag Cleanup Lifecycle, AST/TS patch generation, >40-file scan, and gates."""

from __future__ import annotations

import base64
import itertools
import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.specialists.pr_agent import generate_cleanup_patch_payload
from app.agents.tools.context import ToolExecutionContext
from app.agents.tools.flag_tools import (
    check_flag_retirement_preconditions,
    handle_scan_flag_health,
    transition_flag_post_promotion,
)
from app.analytics.flag_health import (
    HealthIssueType,
    evaluate_flag_cleanup_readiness,
    is_valid_flag_lifecycle_transition,
)
from app.core.security import hash_password
from app.integrations.github_client import GitHubClient
from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    ApprovalStatus,
    Experiment,
    ExperimentStatus,
    FeatureFlag,
    FlagLifecycleStatus,
    FlagType,
    PolicyCategory,
    ProgressiveRollout,
    ProgressiveRolloutStatus,
    PullRequest,
    PullRequestState,
    Repository,
    User,
    UserRole,
)
from app.schemas.tools import ScanFlagHealthInput
from app.services.patch_validator import PatchValidator


def _build_cleanup_mock_github_transport(
    repo_files: dict[str, str],
    *,
    head_sha: str = "sha_main_000",
    pr_merged: bool = False,
) -> tuple[httpx.MockTransport, dict[str, Any]]:
    state: dict[str, Any] = {
        "head_sha": head_sha,
        "pr_merged": pr_merged,
        "draft_prs": [],
        "branches_created": [],
        "blobs": {},
        "commits": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        if method == "GET" and path.endswith("/branches"):
            return httpx.Response(
                200,
                json=[{"name": "main", "commit": {"sha": state["head_sha"]}}],
            )

        if method == "GET" and "/git/trees/" in path:
            tree = [
                {"path": fp, "type": "blob", "sha": f"blob_{idx}"}
                for idx, fp in enumerate(repo_files.keys())
            ]
            return httpx.Response(200, json={"sha": state["head_sha"], "tree": tree})

        if method == "GET" and "/contents/" in path:
            file_path = path.split("/contents/", 1)[1]
            if file_path not in repo_files:
                return httpx.Response(404, json={"message": "Not Found"})
            encoded = base64.b64encode(repo_files[file_path].encode("utf-8")).decode("ascii")
            return httpx.Response(
                200,
                json={
                    "path": file_path,
                    "encoding": "base64",
                    "content": encoded,
                    "sha": f"sha_{file_path}",
                },
            )

        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": state["head_sha"]}})

        if method == "GET" and "/git/commits/" in path:
            return httpx.Response(200, json={"sha": state["head_sha"], "tree": {"sha": "tree_base_000"}})

        if method == "POST" and path.endswith("/git/refs"):
            body = json.loads(request.content.decode("utf-8"))
            state["branches_created"].append(body)
            return httpx.Response(201, json={"ref": body["ref"], "object": {"sha": body["sha"]}})

        if method == "POST" and path.endswith("/git/blobs"):
            body = json.loads(request.content.decode("utf-8"))
            sha = f"blob_new_{len(state['blobs']) + 1}"
            state["blobs"][sha] = body.get("content", "")
            return httpx.Response(201, json={"sha": sha})

        if method == "POST" and path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree_new_111"})

        if method == "POST" and path.endswith("/git/commits"):
            body = json.loads(request.content.decode("utf-8"))
            state["commits"].append(body)
            return httpx.Response(201, json={"sha": "commit_new_222"})

        if method == "PATCH" and "/git/refs/heads/" in path:
            return httpx.Response(200, json={"object": {"sha": "commit_new_222"}})

        if method == "POST" and path.endswith("/pulls"):
            body = json.loads(request.content.decode("utf-8"))
            pr_num = 900 + len(state["draft_prs"]) + 1
            pr_obj = {
                "number": pr_num,
                "html_url": f"https://github.com/octocat/shop/pull/{pr_num}",
                "draft": bool(body.get("draft")),
                "title": body.get("title"),
                "head": {"ref": body.get("head")},
                "base": {"ref": body.get("base")},
                "state": "open",
                "merged": False,
            }
            state["draft_prs"].append(pr_obj)
            return httpx.Response(201, json=pr_obj)

        if method == "GET" and "/pulls/" in path:
            pr_num = int(path.rsplit("/", 1)[-1])
            return httpx.Response(
                200,
                json={
                    "number": pr_num,
                    "state": "closed" if state["pr_merged"] else "open",
                    "merged": state["pr_merged"],
                },
            )

        return httpx.Response(404, json={"message": f"Unhandled mock route: {method} {path}"})

    return httpx.MockTransport(handler), state


def _build_mock_flagsmith_transport() -> tuple[httpx.MockTransport, dict[str, Any]]:
    calls: dict[str, Any] = {"archived": [], "deleted": [], "toggled": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "PATCH" and "/features/" in path:
            body = json.loads(request.content.decode("utf-8"))
            calls["archived"].append({"path": path, "body": body})
            return httpx.Response(200, json={"id": 101, "is_archived": True, **body})
        if method == "DELETE" and "/features/" in path:
            calls["deleted"].append({"path": path})
            return httpx.Response(204)
        if method == "GET" and "/environments/" in path and "/featurestates/" in path:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 1,
                            "feature": 101,
                            "enabled": True,
                            "feature_state_value": "one_page_checkout",
                            "feature_segment": None,
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    return httpx.MockTransport(handler), calls


async def _make_test_app(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    *,
    gh_transport: httpx.MockTransport | None = None,
    fs_transport: httpx.MockTransport | None = None,
) -> Any:
    monkeypatch.setenv("REQUIRE_AUTH", "false")
    monkeypatch.setenv("GITHUB_PAT", "ghp_test_token")
    monkeypatch.setenv("FLAGSMITH_ORG_TOKEN", "fs_test_org_token")
    monkeypatch.setenv("FLAGSMITH_PROJECT_ID", "56055")
    monkeypatch.setenv("FLAGSMITH_DEV_ENV_KEY", "dev_env_key_123")
    monkeypatch.setenv("FLAGSMITH_PROD_ENV_KEY", "prod_env_key_456")

    app = create_app()
    app.state.db_engine = async_engine
    app.state.session_factory = session_factory
    async with session_factory() as session:
        await bootstrap_workspace_defaults(session)

    if gh_transport is not None:
        app.state.github_transport = gh_transport
    if fs_transport is not None:
        app.state.flagsmith_transport = fs_transport
    return app


def test_lifecycle_transition_matrix_and_invalid_jumps() -> None:
    chain = [
        FlagLifecycleStatus.PROPOSED,
        FlagLifecycleStatus.CREATED,
        FlagLifecycleStatus.ACTIVE,
        FlagLifecycleStatus.EXPERIMENTING,
        FlagLifecycleStatus.PROMOTED,
        FlagLifecycleStatus.CLEANUP_RECOMMENDED,
        FlagLifecycleStatus.CLEANUP_PR_CREATED,
        FlagLifecycleStatus.CLEANUP_APPROVED,
        FlagLifecycleStatus.CLEANED_UP,
    ]
    for cur, nxt in itertools.pairwise(chain):
        assert is_valid_flag_lifecycle_transition(cur, nxt) is True
        assert is_valid_flag_lifecycle_transition(cur.value, nxt.value) is True

    assert is_valid_flag_lifecycle_transition(
        FlagLifecycleStatus.PROPOSED, FlagLifecycleStatus.CLEANED_UP
    ) is False
    assert is_valid_flag_lifecycle_transition(
        FlagLifecycleStatus.CLEANED_UP, FlagLifecycleStatus.ACTIVE
    ) is False
    assert is_valid_flag_lifecycle_transition(
        FlagLifecycleStatus.CLEANUP_PR_CREATED, FlagLifecycleStatus.PROPOSED
    ) is False


def test_non_stub_cleanup_patch_preserves_winner_and_passes_validator() -> None:
    py_source = (
        '"""Checkout module."""\n\n\n'
        "def render_checkout(flags, user_id: str) -> str:\n"
        '    if flags.is_feature_enabled("checkout_v2"):\n'
        '        return "one_page_checkout"\n'
        "    else:\n"
        '        return "legacy_checkout"\n'
    )
    tsx_source = (
        "export function CheckoutBanner({ flagsmith }: { flagsmith: any }) {\n"
        '  const mode = flagsmith.hasFeature("checkout_v2") ? "one_page_checkout" : "legacy_checkout";\n'
        "  return mode;\n"
        "}\n"
    )
    base_files = {
        "app/checkout.py": py_source,
        "frontend/CheckoutBanner.tsx": tsx_source,
    }
    payload = generate_cleanup_patch_payload(
        flag_key="checkout_v2",
        keep_branch="enabled",
        winning_variation="one_page_checkout",
        code_references=[
            {"file_path": "app/checkout.py", "line_number": 5},
            {"file_path": "frontend/CheckoutBanner.tsx", "line_number": 2},
        ],
        base_files=base_files,
    )

    diffs = {item["file_path"]: item for item in payload["files_diff"]}
    assert set(diffs.keys()) == {"app/checkout.py", "frontend/CheckoutBanner.tsx"}
    assert diffs["app/checkout.py"]["action"] == "modify"
    assert diffs["frontend/CheckoutBanner.tsx"]["action"] == "modify"

    py_after = diffs["app/checkout.py"]["after_code"]
    assert "checkout_v2" not in py_after
    assert "one_page_checkout" in py_after
    assert "legacy_checkout" not in py_after

    tsx_after = diffs["frontend/CheckoutBanner.tsx"]["after_code"]
    assert "checkout_v2" not in tsx_after
    assert "one_page_checkout" in tsx_after
    assert "hasFeature" not in tsx_after

    report = PatchValidator.validate(
        files_diff=payload["files_diff"],
        generated_tests=payload["generated_tests"],
        base_files=base_files,
        run_linters=False,
    )
    assert report["validation_status"] == "passed", report
    assert report["path_safety_ok"] is True
    assert report["overwrite_ok"] is True
    assert report["ast_ok"] is True


@pytest.mark.asyncio
async def test_scan_flag_health_scans_over_40_files_without_false_orphans(
    db_session: AsyncSession,
) -> None:
    repo = Repository(
        github_repo_id=1001,
        owner="octocat",
        name="shop",
        full_name="octocat/shop",
        default_branch="main",
        flagsmith_project_id=56055,
    )
    db_session.add(repo)
    await db_session.flush()

    late_flag = FeatureFlag(
        key="late_file_flag_v2",
        flagsmith_project_id=56055,
        flagsmith_feature_id=509,
        flag_type=FlagType.STANDARD,
        env_states={"development": {"enabled": True, "rollout_percentage": 100}},
        code_references=[],
        lifecycle_status=FlagLifecycleStatus.ACTIVE,
    )
    db_session.add(late_flag)
    await db_session.commit()

    repo_files: dict[str, str] = {
        f"src/mod_{idx:02d}.py": f"def helper_{idx:02d}() -> int:\n    return {idx}\n"
        for idx in range(1, 52)
    }
    repo_files["src/mod_52.py"] = (
        "def late_checkout(flags) -> str:\n"
        '    if flags.is_feature_enabled("late_file_flag_v2"):\n'
        '        return "new"\n'
        '    return "old"\n'
    )
    repo_files["node_modules/pkg/index.js"] = "flagsmith.hasFeature('ignored_vendor_flag');\n"

    gh_transport, _ = _build_cleanup_mock_github_transport(repo_files)
    ctx = ToolExecutionContext(
        session=db_session,
        github_client=GitHubClient("ghp_test_token", transport=gh_transport),
    )

    out = await handle_scan_flag_health(
        ctx,
        ScanFlagHealthInput(project_id=56055, repository_id=repo.id),
    )
    assert out["flags_scanned"] == 1
    res = out["results"][0]
    assert res["key"] == "late_file_flag_v2"
    assert res["code_references_count"] == 1
    issue_types = {iss["issue_type"] for iss in res["health_issues"]}
    assert HealthIssueType.ORPHANED.value not in issue_types


@pytest.mark.asyncio
async def test_post_promotion_dependency_check_safe_and_blocked(
    db_session: AsyncSession,
) -> None:
    safe_report = evaluate_flag_cleanup_readiness()
    assert safe_report.safe_for_cleanup is True
    assert safe_report.blockers == []

    unsafe_report = evaluate_flag_cleanup_readiness(
        active_experiments=["exp-1"],
        active_rollouts=["rollout-1 (production)"],
        partial_environments=["staging"],
    )
    assert unsafe_report.safe_for_cleanup is False
    assert len(unsafe_report.blockers) == 3

    flag = FeatureFlag(
        key="promo_dep_flag",
        flagsmith_project_id=56055,
        flagsmith_feature_id=510,
        flag_type=FlagType.MULTIVARIATE,
        env_states={
            "development": {"enabled": True, "rollout_percentage": 100},
            "staging": {"enabled": True, "rollout_percentage": 25},
        },
        lifecycle_status=FlagLifecycleStatus.EXPERIMENTING,
    )
    db_session.add(flag)
    await db_session.flush()

    exp = Experiment(
        feature_flag_id=flag.id,
        name="Active Blocking Exp",
        hypothesis="Test hypothesis",
        environment_name="development",
        status=ExperimentStatus.RUNNING,
    )
    db_session.add(exp)
    await db_session.commit()

    # Blocked by active experiment + partial staging rollout
    blocked = await transition_flag_post_promotion(
        db_session,
        flag,
        source="experiment_promotion",
        environment_name="development",
        winning_variation="one_page_checkout",
    )
    assert blocked.safe_for_cleanup is False
    assert flag.lifecycle_status == FlagLifecycleStatus.PROMOTED
    assert len(flag.lifecycle_details["blockers"]) == 2

    # Clear blockers -> transitions to CLEANUP_RECOMMENDED
    exp.status = ExperimentStatus.COMPLETED
    flag.env_states = {
        "development": {"enabled": True, "rollout_percentage": 100},
        "staging": {"enabled": True, "rollout_percentage": 100},
    }
    await db_session.flush()
    ready = await transition_flag_post_promotion(
        db_session,
        flag,
        source="experiment_promotion",
        environment_name="development",
        winning_variation="one_page_checkout",
    )
    assert ready.safe_for_cleanup is True
    assert flag.lifecycle_status == FlagLifecycleStatus.CLEANUP_RECOMMENDED
    assert flag.lifecycle_details["cleanup_ready"] is True

    # Active progressive rollout also blocks cleanup readiness, then unblocks on completion
    rollout_flag = FeatureFlag(
        key="rollout_100_flag",
        flagsmith_project_id=56055,
        flagsmith_feature_id=511,
        flag_type=FlagType.STANDARD,
        env_states={"development": {"enabled": True, "rollout_percentage": 100}},
        lifecycle_status=FlagLifecycleStatus.ACTIVE,
    )
    db_session.add(rollout_flag)
    await db_session.flush()

    rollout = ProgressiveRollout(
        rollout_key="ro_100",
        name="Rollout 100%",
        feature_flag_id=rollout_flag.id,
        feature_flag_key="rollout_100_flag",
        environment="development",
        status=ProgressiveRolloutStatus.ROLLING_OUT,
    )
    db_session.add(rollout)
    await db_session.commit()

    blocked_ro = await transition_flag_post_promotion(
        db_session,
        rollout_flag,
        source="progressive_rollout_completed",
        environment_name="development",
    )
    assert blocked_ro.safe_for_cleanup is False
    assert rollout_flag.lifecycle_status == FlagLifecycleStatus.PROMOTED
    assert blocked_ro.active_rollouts == ["ro_100 (development)"]

    rollout.status = ProgressiveRolloutStatus.COMPLETED
    await db_session.flush()
    ready_ro = await transition_flag_post_promotion(
        db_session,
        rollout_flag,
        source="progressive_rollout_completed",
        environment_name="development",
    )
    assert ready_ro.safe_for_cleanup is True
    assert rollout_flag.lifecycle_status == FlagLifecycleStatus.CLEANUP_RECOMMENDED


@pytest.mark.asyncio
async def test_full_cleanup_api_lifecycle_draft_pr_gate_and_retirement(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_files = {
        "app/checkout.py": (
            '"""Checkout service."""\n\n\n'
            "def choose_flow(flags) -> str:\n"
            '    if flags.is_feature_enabled("checkout_cleanup_demo"):\n'
            '        return "one_page_checkout"\n'
            '    return "legacy_checkout"\n'
        )
    }
    gh_transport, gh_state = _build_cleanup_mock_github_transport(repo_files)
    fs_transport, fs_calls = _build_mock_flagsmith_transport()
    app = await _make_test_app(
        async_engine,
        session_factory,
        monkeypatch,
        gh_transport=gh_transport,
        fs_transport=fs_transport,
    )

    async with session_factory() as session:
        reviewer = User(
            email="reviewer2@releaseguard.local",
            display_name="Reviewer Two",
            role=UserRole.RELEASE_MANAGER,
            password_hash=hash_password("ReviewerPass!123"),
            is_active=True,
        )
        session.add(reviewer)

        repo = Repository(
            github_repo_id=1002,
            owner="octocat",
            name="shop",
            full_name="octocat/shop",
            default_branch="main",
            flagsmith_project_id=56055,
        )
        session.add(repo)
        await session.flush()

        flag = FeatureFlag(
            key="checkout_cleanup_demo",
            flagsmith_project_id=56055,
            flagsmith_feature_id=101,
            flag_type=FlagType.STANDARD,
            env_states={"development": {"enabled": True, "rollout_percentage": 100}},
            code_references=[{"file_path": "app/checkout.py", "line_number": 5}],
            lifecycle_status=FlagLifecycleStatus.CLEANUP_RECOMMENDED,
            lifecycle_details={"winning_variation": "one_page_checkout", "environment_name": "development"},
        )
        session.add(flag)
        await session.commit()
        repo_id = repo.id
        flag_id = flag.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Pre-retirement gate blocks when code_references remain & no merged cleanup PR
        blocked_resp = await client.post(
            f"/api/v1/flag-health/{flag_id}/request-delete",
            json={
                "action": "archive",
                "environment_name": "development",
                "reason": "Too early",
            },
        )
        assert blocked_resp.status_code == 409
        assert "CLEANUP_PRECONDITION_UNMET" in blocked_resp.text

        # 2. Generate cleanup Draft PR (Category B approval or direct execution -> draft=True)
        pr_resp = await client.post(
            f"/api/v1/flag-health/{flag_id}/cleanup-pr",
            json={
                "repository_id": str(repo_id),
                "keep_branch": "enabled",
                "open_draft_pr": True,
            },
        )
        assert pr_resp.status_code in (200, 202), pr_resp.text
        pr_body = pr_resp.json()
        if pr_resp.status_code == 202:
            monkeypatch.setenv("REQUIRE_AUTH", "true")
            login_pr = await client.post(
                "/api/v1/auth/login",
                json={"email": "reviewer2@releaseguard.local", "password": "ReviewerPass!123"},
            )
            assert login_pr.status_code == 200, login_pr.text
            rev_token_pr = login_pr.json()["access_token"]
            pr_approve = await client.post(
                f"/api/v1/approvals/{pr_body['approval']['id']}/approve",
                headers={"Authorization": f"Bearer {rev_token_pr}"},
                json={"comment": "Approved Draft PR creation"},
            )
            assert pr_approve.status_code == 200, pr_approve.text
            assert pr_approve.json()["approval"]["status"] == ApprovalStatus.EXECUTED.value
            monkeypatch.setenv("REQUIRE_AUTH", "false")
        else:
            assert pr_body["lifecycle_status"] == FlagLifecycleStatus.CLEANUP_PR_CREATED.value
            assert pr_body["data"]["draft_pr_number"] == 901

        assert len(gh_state["draft_prs"]) == 1
        assert gh_state["draft_prs"][0]["draft"] is True

        # 3. Still blocked while Draft PR is unmerged and references remain
        still_blocked = await client.post(
            f"/api/v1/flag-health/{flag_id}/request-delete",
            json={
                "action": "archive",
                "environment_name": "development",
                "reason": "PR not merged yet",
            },
        )
        assert still_blocked.status_code == 409

        # 4. Mark cleanup PR merged -> request-delete succeeds (Category C approval in development)
        gh_state["pr_merged"] = True
        async with session_factory() as session:
            session.add(
                PullRequest(
                    repository_id=repo_id,
                    github_pr_number=901,
                    title="chore(flags): remove retired flag `checkout_cleanup_demo`",
                    author_login="releaseguard-bot",
                    base_branch="main",
                    head_branch="releaseguard/cleanup",
                    head_sha="commit_new_222",
                    html_url="https://github.com/octocat/shop/pull/901",
                    state=PullRequestState.MERGED,
                )
            )
            await session.commit()

        req_del = await client.post(
            f"/api/v1/flag-health/{flag_id}/request-delete",
            json={
                "action": "archive",
                "environment_name": "development",
                "reason": "Cleanup PR merged and verified",
            },
        )
        assert req_del.status_code == 202, req_del.text
        approval = req_del.json()["approval"]
        assert approval["policy_category"] == PolicyCategory.C.value
        assert approval["environment_name"] == "development"
        approval_id = approval["id"]

        # 5. Approve with distinct reviewer -> executes Flagsmith archive & transitions to CLEANED_UP
        # Re-enable auth header or use reviewer user via approve endpoint
        monkeypatch.setenv("REQUIRE_AUTH", "true")
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "reviewer2@releaseguard.local", "password": "ReviewerPass!123"},
        )
        assert login_resp.status_code == 200, login_resp.text
        rev_token = login_resp.json()["access_token"]

        approve_resp = await client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers={"Authorization": f"Bearer {rev_token}"},
            json={"comment": "Approved retirement after cleanup PR merge"},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        approved = approve_resp.json()["approval"]
        assert approved["status"] == ApprovalStatus.EXECUTED.value
        assert len(fs_calls["archived"]) == 1

    async with session_factory() as session:
        updated_flag = await session.get(FeatureFlag, flag_id)
        assert updated_flag is not None
        assert updated_flag.lifecycle_status == FlagLifecycleStatus.CLEANED_UP
        assert updated_flag.is_archived is True

        # Also verify zero-references path passes check_flag_retirement_preconditions directly
        zero_ref_flag = FeatureFlag(
            key="zero_ref_flag",
            flagsmith_project_id=56055,
            flag_type=FlagType.STANDARD,
            code_references=[],
            lifecycle_status=FlagLifecycleStatus.CLEANUP_RECOMMENDED,
        )
        session.add(zero_ref_flag)
        await session.flush()
        ok_zero, details_zero = await check_flag_retirement_preconditions(session, zero_ref_flag)
        assert ok_zero is True
        assert details_zero["reason"] == "zero_code_references"
