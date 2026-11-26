"""Comprehensive tests for PR Intelligence, 7-dim risk, PatchValidator, Draft PRs, and Policy gates."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from app.agents.specialists.pr_agent import (
    _parse_patch_added_lines,
    analyze_pr_with_static_and_gemini,
    annotate_flag_opportunities_with_existing,
    extract_static_pr_signals,
)
from app.agents.tools.context import ToolExecutionContext
from app.agents.tools.pr_tools import (
    handle_generate_flag_wrapping_patch,
    handle_github_create_branch_and_draft_pr,
    handle_validate_patch_isolated,
)
from app.integrations.github_client import GitHubClient
from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    AnalysisStatus,
    FeatureFlag,
    FlagType,
    PolicyCategory,
    PullRequest,
    PullRequestState,
    Repository,
    RiskLevel,
    WebhookProcessingStatus,
)
from app.schemas.tools import (
    FileDiffItem,
    GeneratedTestItem,
    GenerateFlagWrappingPatchInput,
    GitHubCreateBranchAndDraftPRInput,
    ValidatePatchIsolatedInput,
)
from app.services.dx import (
    GitHubIntegrationService,
    GitHubPullRequestCommentService,
    GitHubWebhookService,
)
from app.services.patch_validator import PatchValidator
from app.services.policy_engine import (
    PolicyEngine,
    PolicyViolationError,
    classify_action,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

SAMPLE_CHECKOUT_PY = '''"""Checkout service."""


def process_checkout(amount: float, promo: str | None = None) -> float:
    if promo == "SAVE10":
        return round(amount * 0.9, 2)
    return round(amount, 2)
'''

RISKY_PR_DIFF = """@@ -1,6 +1,12 @@
 def process_checkout(amount: float, promo: str | None = None) -> float:
+    # Unsafe payment + eval + silent except
+    if promo == "VIP20":
+        try:
+            eval("1+1")
+        except Exception: pass
+        return round(amount * 0.8, 2)
     if promo == "SAVE10":
         return round(amount * 0.9, 2)
     return round(amount, 2)
"""


def _build_mock_github_transport(
    *,
    head_sha: str = "sha_head_111",
    base_sha: str = "sha_base_000",
    files_payload: list[dict[str, Any]] | None = None,
) -> tuple[httpx.MockTransport, dict[str, Any]]:
    state: dict[str, Any] = {
        "head_sha": head_sha,
        "draft_prs": [],
        "comments": [],
        "check_runs": [],
        "branches_created": [],
        "merge_called": False,
        "force_push_called": False,
    }
    files = files_payload if files_payload is not None else [
        {
            "filename": "app/checkout.py",
            "status": "modified",
            "additions": 7,
            "deletions": 0,
            "patch": RISKY_PR_DIFF,
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method.upper()

        if path.endswith("/merge"):
            state["merge_called"] = True
            return httpx.Response(403, json={"message": "Merge forbidden"})

        if path == "/user":
            return httpx.Response(
                200,
                headers={"X-OAuth-Scopes": "read:user, repo"},
                json={"id": 101, "login": "releaseguard-bot"},
            )
        if path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9001,
                        "name": "demo-shop",
                        "full_name": "acme/demo-shop",
                        "owner": {"login": "acme"},
                        "default_branch": "main",
                        "private": False,
                    }
                ],
            )
        if path == "/repos/acme/demo-shop/pulls" and method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 12,
                        "title": "feat: VIP20 checkout",
                        "state": "open",
                        "draft": False,
                        "html_url": "https://github.com/acme/demo-shop/pull/12",
                        "user": {"login": "dev"},
                        "head": {"ref": "feat/vip20", "sha": state["head_sha"]},
                        "base": {"ref": "main"},
                    }
                ],
            )
        if path == "/repos/acme/demo-shop/pulls/12" and method == "GET":
            return httpx.Response(
                200,
                json={
                    "number": 12,
                    "title": "feat: VIP20 checkout",
                    "body": "Adds VIP20 discount",
                    "state": "open",
                    "draft": False,
                    "html_url": "https://github.com/acme/demo-shop/pull/12",
                    "user": {"login": "dev"},
                    "head": {"ref": "feat/vip20", "sha": state["head_sha"]},
                    "base": {"ref": "main"},
                },
            )
        if path == "/repos/acme/demo-shop/pulls/12/files" and method == "GET":
            return httpx.Response(200, json=files)
        if path == "/repos/acme/demo-shop/contents/app/checkout.py" and method == "GET":
            encoded = base64.b64encode(SAMPLE_CHECKOUT_PY.encode("utf-8")).decode("ascii")
            return httpx.Response(
                200,
                json={
                    "path": "app/checkout.py",
                    "sha": "blob_checkout_1",
                    "size": len(SAMPLE_CHECKOUT_PY),
                    "encoding": "base64",
                    "content": encoded,
                },
            )
        if path == "/repos/acme/demo-shop/git/ref/heads/main" and method == "GET":
            return httpx.Response(200, json={"object": {"sha": base_sha}})
        if path == f"/repos/acme/demo-shop/git/commits/{base_sha}" and method == "GET":
            return httpx.Response(200, json={"tree": {"sha": "tree_base_000"}})
        if path == "/repos/acme/demo-shop/git/refs" and method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            state["branches_created"].append(body["ref"])
            return httpx.Response(201, json={"ref": body["ref"], "object": {"sha": body["sha"]}})
        if path == "/repos/acme/demo-shop/git/blobs" and method == "POST":
            return httpx.Response(201, json={"sha": "blob_new_123"})
        if path == "/repos/acme/demo-shop/git/trees" and method == "POST":
            return httpx.Response(201, json={"sha": "tree_new_123"})
        if path == "/repos/acme/demo-shop/git/commits" and method == "POST":
            return httpx.Response(201, json={"sha": "commit_new_123"})
        if path.startswith("/repos/acme/demo-shop/git/refs/heads/") and method == "PATCH":
            body = json.loads(request.content.decode("utf-8"))
            if body.get("force") is True:
                state["force_push_called"] = True
            return httpx.Response(200, json={"object": {"sha": body["sha"]}})
        if path == "/repos/acme/demo-shop/pulls" and method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            state["draft_prs"].append(body)
            return httpx.Response(
                201,
                json={
                    "number": 99,
                    "html_url": "https://github.com/acme/demo-shop/pull/99",
                    "draft": bool(body.get("draft", False)),
                },
            )
        if path == "/repos/acme/demo-shop/issues/12/comments" and method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            cid = len(state["comments"]) + 1000
            state["comments"].append({"id": cid, "body": body["body"]})
            return httpx.Response(
                201,
                json={"id": cid, "html_url": f"https://github.com/acme/demo-shop/pull/12#issuecomment-{cid}"},
            )
        if path.startswith("/repos/acme/demo-shop/issues/comments/") and method == "PATCH":
            body = json.loads(request.content.decode("utf-8"))
            cid = int(path.rsplit("/", 1)[-1])
            state["comments"].append({"id": cid, "updated_body": body["body"]})
            return httpx.Response(
                200,
                json={"id": cid, "html_url": f"https://github.com/acme/demo-shop/pull/12#issuecomment-{cid}"},
            )
        if path == "/repos/acme/demo-shop/check-runs" and method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            state["check_runs"].append(body)
            return httpx.Response(201, json={"id": 500 + len(state["check_runs"])})
        return httpx.Response(404, json={"message": f"Unmocked {method} {path}"})

    return httpx.MockTransport(handler), state


async def _make_test_app(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    transport: httpx.MockTransport,
):
    app = create_app()
    app.state.engine = async_engine
    app.state.session_factory = session_factory
    app.state.github_transport = transport
    app.state.gemini_raw_client = None
    async with session_factory() as session:
        await bootstrap_workspace_defaults(session)
        await session.commit()
    return app


# ---------------------------------------------------------------------------
# 1. GitHub Auth, Repo/PR Retrieval & Sandbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_auth_and_repo_pr_retrieval() -> None:
    transport, _ = _build_mock_github_transport()
    gh = GitHubClient(token="ghp_test_token_123456", transport=transport)

    auth_info = await gh.verify_token()
    assert auth_info["user"]["login"] == "releaseguard-bot"
    assert "repo" in auth_info["scopes"]

    oauth_url = gh.build_oauth_authorize_url(
        client_id="cid",
        redirect_uri="http://localhost:3000/cb",
        state="st123",
        include_private=True,
    )
    assert "scope=read%3Auser+user%3Aemail+repo" in oauth_url

    repos = await gh.list_repositories()
    assert len(repos) == 1 and repos[0]["full_name"] == "acme/demo-shop"

    prs = await gh.list_pull_requests("acme", "demo-shop")
    assert len(prs) == 1 and prs[0]["number"] == 12

    diff_bundle = await gh.get_pull_request_diff("acme", "demo-shop", 12)
    assert diff_bundle["changed_files"] == ["app/checkout.py"]
    assert "<untrusted_external_data" in diff_bundle["sandboxed"]["diff"]


# ---------------------------------------------------------------------------
# 2. Diff Parsing & Static Risk Categories (Confirmed/Potential/Recs/Unknowns)
# ---------------------------------------------------------------------------


def test_diff_parsing_and_static_risk_separation() -> None:
    added = _parse_patch_added_lines(RISKY_PR_DIFF)
    assert any("eval(" in txt for _, txt in added)

    files_data = [
        {
            "filename": "app/checkout.py",
            "status": "modified",
            "additions": 7,
            "deletions": 0,
            "patch": RISKY_PR_DIFF,
        },
        {
            "filename": "assets/logo.png",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": None,
        },
        {
            "filename": "alembic/versions/0002_drop.py",
            "status": "added",
            "additions": 4,
            "deletions": 0,
            "patch": "@@ -0,0 +1,2 @@\n+op.drop_column('users', 'legacy')\n",
        },
    ]
    signals = extract_static_pr_signals(files_data)
    assert signals["risk_score"] >= 75
    assert signals["risk_level"] == RiskLevel.CRITICAL.value
    assert {"payment", "security", "error_handling", "migrations", "missing_tests"} <= set(
        signals["categories_detected"]
    )
    assert len(signals["confirmed_risks"]) >= 3
    assert len(signals["potential_risks"]) >= 1
    assert len(signals["recommendations"]) >= 1
    assert any(u.get("area") == "diff_retrieval" for u in signals["unknowns"])
    assert signals["flag_opportunities"][0]["suggested_flag_key"] == "checkout_v2"


# ---------------------------------------------------------------------------
# 3. Gemini Analysis (No Invented Scores, Unverified Citations, Fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_analysis_respects_deterministic_score_and_fallbacks() -> None:
    files_data = [
        {
            "filename": "app/checkout.py",
            "status": "modified",
            "additions": 7,
            "deletions": 0,
            "patch": RISKY_PR_DIFF,
        }
    ]
    gemini_payload = {
        "summary": "Modifies checkout VIP20 discount path.",
        "risk_score": 5,  # Must be ignored in favor of deterministic score
        "risk_level": "low",
        "confirmed_risks": [
            {
                "title": "Eval injection in checkout",
                "severity": "critical",
                "file_path": "app/checkout.py",
                "line_range": "L5",
                "evidence": "eval('1+1')",
                "mitigation": "Remove eval",
            },
            {
                "title": "Hallucinated file citation",
                "severity": "high",
                "file_path": "app/nonexistent.py",
                "line_range": "L1",
                "evidence": "not in diff",
                "mitigation": "N/A",
            },
        ],
        "potential_risks": [],
        "recommendations": [{"title": "Add checkout tests", "priority": "high", "action": "Add pytest"}],
        "unknowns": [{"title": "Prod traffic volume", "area": "traffic", "reason": "No metrics", "impact": "High"}],
        "flag_opportunities": [],
    }
    mock_gemini = SimpleNamespace(
        is_configured=True,
        generate_content=lambda **kwargs: _async_return(SimpleNamespace(text=json.dumps(gemini_payload))),
    )
    res = await analyze_pr_with_static_and_gemini(
        pr_title="VIP20",
        pr_body="Adds VIP20",
        files_data=files_data,
        repo_full_name="acme/demo-shop",
        pr_number=12,
        gemini_client=mock_gemini,  # type: ignore[arg-type]
    )
    assert res["analysis_mode"] == "gemini_ai"
    assert res["risk_score"] >= 75  # Never overridden by Gemini's 5
    assert len(res["confirmed_risks"]) == 1
    assert res["confirmed_risks"][0]["file_path"] == "app/checkout.py"
    assert any("app/nonexistent.py" in str(p.get("assumption", "")) for p in res["potential_risks"])
    assert any(u.get("title") == "Prod traffic volume" for u in res["unknowns"])

    # Fallback on Gemini error
    failing_gemini = SimpleNamespace(
        is_configured=True,
        generate_content=lambda **kwargs: _async_raise(RuntimeError("Gemini 503")),
    )
    fallback_res = await analyze_pr_with_static_and_gemini(
        pr_title="VIP20",
        pr_body="Adds VIP20",
        files_data=files_data,
        repo_full_name="acme/demo-shop",
        pr_number=12,
        gemini_client=failing_gemini,  # type: ignore[arg-type]
    )
    assert fallback_res["analysis_mode"] == "static_fallback"
    assert "Gemini 503" in str(fallback_res["gemini_error"])


async def _async_return(val: Any) -> Any:
    return val


async def _async_raise(exc: Exception) -> Any:
    raise exc


# ---------------------------------------------------------------------------
# 4. Flag Opportunity Dedup & Flag Agent Handoff
# ---------------------------------------------------------------------------


def test_flag_opportunity_dedup_and_handoff() -> None:
    opps = [
        {
            "suggested_flag_key": "checkout_v2",
            "flag_type": "standard",
            "target_files": ["app/checkout.py"],
            "rationale": "Gate checkout",
            "recommended_rollout": "10% -> 50% -> 100%",
        }
    ]
    annotated_existing, rec_existing = annotate_flag_opportunities_with_existing(
        opps, {"Checkout_V2", "other_flag"}
    )
    assert annotated_existing[0]["already_exists"] is True
    assert annotated_existing[0]["dedup_action"] == "reuse_existing"
    assert rec_existing is not None
    assert rec_existing["action"] == "reuse_existing_flag"
    assert rec_existing["requires_approval"] is False

    annotated_new, rec_new = annotate_flag_opportunities_with_existing(opps, {"other_flag"})
    assert annotated_new[0]["already_exists"] is False
    assert annotated_new[0]["dedup_action"] == "create_new"
    assert rec_new is not None
    assert rec_new["action"] == "create_non_prod_flag"
    assert rec_new["requires_approval"] is True


# ---------------------------------------------------------------------------
# 5. API PR Analyze -> 7-Dim Risk -> Dedup -> DX Check & Comment -> Patch Gen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_pr_analyze_seven_dim_risk_dx_and_patch_generation(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    transport, gh_state = _build_mock_github_transport(head_sha="sha_live_999")
    app = await _make_test_app(async_engine, session_factory, transport)

    async with session_factory() as session:
        repo = Repository(
            github_repo_id=9001,
            owner="acme",
            name="demo-shop",
            full_name="acme/demo-shop",
            default_branch="main",
        )
        session.add(repo)
        await session.flush()

        existing_flag = FeatureFlag(
            flagsmith_project_id=1,
            key="checkout_v2",
            flag_type=FlagType.STANDARD,
            env_states={"development": {"enabled": False}},
        )
        pr = PullRequest(
            repository_id=repo.id,
            github_pr_number=12,
            title="feat: VIP20 checkout",
            author_login="dev",
            head_branch="feat/vip20",
            base_branch="main",
            head_sha="sha_stale_000",  # Intentionally stale
            state=PullRequestState.OPEN,
            html_url="https://github.com/acme/demo-shop/pull/12",
            analysis_status=AnalysisStatus.PENDING,
        )
        session.add_all([existing_flag, pr])
        await session.commit()
        pr_id = pr.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        analyze_resp = await client.post(f"/api/v1/pull-requests/{pr_id}/analyze")
        assert analyze_resp.status_code == 200, analyze_resp.text
        analyze_data = analyze_resp.json()
        assert "stale_sha_refreshed" in analyze_data["static_signals"]
        assert analyze_data["pull_request"]["head_sha"] == "sha_live_999"
        assert analyze_data["pull_request"]["flag_opportunities"][0]["suggested_flag_key"] == "checkout_v2"

        detail_resp = await client.get(f"/api/v1/pull-requests/{pr_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        risk_assessment = detail["diff_summary"]["risk_assessment"]
        assert risk_assessment is not None
        assert len(risk_assessment["dimensions"]) == 7
        assert detail["diff_summary"]["flag_agent_recommendation"]["action"] == "reuse_existing_flag"
        assert detail["diff_summary"]["dx_status"]["check_run"] is not None
        assert detail["diff_summary"]["dx_status"]["comment"] is not None
        assert len(gh_state["comments"]) >= 1
        assert len(gh_state["check_runs"]) >= 1

        patch_resp = await client.post(
            f"/api/v1/pull-requests/{pr_id}/generate-patch",
            json={"flag_key": "checkout_v2", "flag_type": "STANDARD", "target_files": ["app/checkout.py"]},
        )
        assert patch_resp.status_code == 201, patch_resp.text
        patch_data = patch_resp.json()
        assert patch_data["validation_status"] == "pending"
        assert patch_data["validation_report"]["target_head_sha"] == "sha_live_999"


# ---------------------------------------------------------------------------
# 6. PatchValidator Binary, Traversal, Stale SHA & Draft PR Gate
# ---------------------------------------------------------------------------


def test_patch_validator_rejects_binary_encoded_traversal_and_stale_sha() -> None:
    # Binary null-byte rejection
    rep_bin = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "app/data.py",
                "action": "create",
                "before_code": "",
                "after_code": "x = '\x00binary'",
            }
        ],
        run_linters=False,
    )
    assert rep_bin["binary_ok"] is False
    assert rep_bin["validation_status"] == "failed"

    # URL-encoded traversal rejection
    rep_trav = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "%2e%2e/secret.py",
                "action": "create",
                "before_code": "",
                "after_code": "x = 1\n",
            }
        ],
        run_linters=False,
    )
    assert rep_trav["path_safety_ok"] is False
    assert rep_trav["validation_status"] == "failed"

    # Stale SHA rejection
    rep_sha = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "app/safe.py",
                "action": "create",
                "before_code": "",
                "after_code": "x = 1\n",
            }
        ],
        expected_head_sha="sha_old_111111",
        actual_head_sha="sha_new_222222",
        run_linters=False,
    )
    assert rep_sha["sha_ok"] is False
    assert rep_sha["validation_status"] == "failed"


@pytest.mark.asyncio
async def test_draft_pr_blocks_unvalidated_or_stale_sha_and_publishes_comment(
    db_session: AsyncSession,
) -> None:
    transport, gh_state = _build_mock_github_transport(head_sha="sha_head_111")
    gh = GitHubClient(token="ghp_test_token_123456", transport=transport)

    repo = Repository(
        github_repo_id=9001,
        owner="acme",
        name="demo-shop",
        full_name="acme/demo-shop",
        default_branch="main",
    )
    db_session.add(repo)
    await db_session.flush()

    pr = PullRequest(
        repository_id=repo.id,
        github_pr_number=12,
        title="feat: VIP20 checkout",
        author_login="dev",
        head_branch="feat/vip20",
        base_branch="main",
        head_sha="sha_head_111",
        state=PullRequestState.OPEN,
        html_url="https://github.com/acme/demo-shop/pull/12",
    )
    db_session.add(pr)
    await db_session.flush()

    ctx = ToolExecutionContext(
        session=db_session,
        github_client=gh,
        repo_base_files={"app/checkout.py": SAMPLE_CHECKOUT_PY},
    )

    gen_res = await handle_generate_flag_wrapping_patch(
        ctx,
        GenerateFlagWrappingPatchInput(
            repository_id=repo.id,
            pull_request_id=pr.id,
            flag_key="checkout_v2",
            title="Guard checkout_v2",
            description="Wrap VIP20",
            files_diff=[
                FileDiffItem(
                    file_path="app/checkout.py",
                    action="modify",
                    before_code=SAMPLE_CHECKOUT_PY,
                    after_code=SAMPLE_CHECKOUT_PY + "\n# guarded\n",
                )
            ],
            generated_tests=[
                GeneratedTestItem(
                    file_path="test_checkout_guard.py",
                    content="def test_ok():\n    assert 1 + 1 == 2\n",
                )
            ],
        ),
    )
    change_id = gen_res["code_change_id"]

    # Unvalidated CodeChange cannot open Draft PR
    with pytest.raises(ValueError, match="must be 'passed'"):
        await handle_github_create_branch_and_draft_pr(
            ctx,
            GitHubCreateBranchAndDraftPRInput(
                code_change_id=change_id,
                base_branch="main",
                pr_title="Guard checkout_v2",
                pr_body="Draft guard",
            ),
        )

    # Validate patch
    val_res = await handle_validate_patch_isolated(
        ctx, ValidatePatchIsolatedInput(code_change_id=change_id)
    )
    assert val_res["validation_status"] == "passed"

    # Simulate PR moving to a newer commit after validation -> blocked by stale SHA check
    pr.head_sha = "sha_head_moved_222"
    await db_session.commit()
    with pytest.raises(ValueError, match="Stale PR head SHA"):
        await handle_github_create_branch_and_draft_pr(
            ctx,
            GitHubCreateBranchAndDraftPRInput(
                code_change_id=change_id,
                base_branch="main",
                pr_title="Guard checkout_v2",
                pr_body="Draft guard",
            ),
        )

    # Restore matching SHA -> Draft PR created + original PR receives comment
    pr.head_sha = "sha_head_111"
    await db_session.commit()
    draft_res = await handle_github_create_branch_and_draft_pr(
        ctx,
        GitHubCreateBranchAndDraftPRInput(
            code_change_id=change_id,
            base_branch="main",
            pr_title="Guard checkout_v2",
            pr_body="Draft guard",
        ),
    )
    assert draft_res["draft"] is True
    assert draft_res["pr_number"] == 99
    assert gh_state["draft_prs"][0]["draft"] is True
    assert "#12" in gh_state["draft_prs"][0]["body"]
    assert any("Draft PR #99" in c["body"] for c in gh_state["comments"])
    assert gh_state["merge_called"] is False
    assert gh_state["force_push_called"] is False


# ---------------------------------------------------------------------------
# 7. Webhook Idempotency, Forbidden Ops, Scope Violations & Category B/C
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_idempotency_and_dx_comment_marker(db_session: AsyncSession) -> None:
    transport, gh_state = _build_mock_github_transport()
    gh = GitHubClient(token="ghp_test_token_123456", transport=transport)

    repo = Repository(
        github_repo_id=9001,
        owner="acme",
        name="demo-shop",
        full_name="acme/demo-shop",
    )
    db_session.add(repo)
    await db_session.flush()

    await GitHubIntegrationService.connect_installation(
        db_session,
        installation_id=8801,
        account_login="acme",
        repositories=[{"id": 9001, "full_name": "acme/demo-shop"}],
        webhook_secret="hook-secret",
    )
    pr = PullRequest(
        repository_id=repo.id,
        github_pr_number=12,
        title="feat: VIP20 checkout",
        author_login="dev",
        head_branch="feat/vip20",
        base_branch="main",
        head_sha="sha_head_111",
        state=PullRequestState.OPEN,
        html_url="https://github.com/acme/demo-shop/pull/12",
        analysis_status=AnalysisStatus.COMPLETED,
        risk_level=RiskLevel.HIGH,
        risk_score=72,
    )
    db_session.add(pr)
    await db_session.flush()

    cmt1 = await GitHubPullRequestCommentService.publish_or_update(
        db_session, repository=repo, pr=pr, github_client=gh
    )
    cmt2 = await GitHubPullRequestCommentService.publish_or_update(
        db_session, repository=repo, pr=pr, github_client=gh
    )
    assert cmt1.id == cmt2.id
    assert "<!-- releaseguard-ai:pr-summary -->" in cmt2.body_markdown
    assert len(gh_state["comments"]) == 2  # initial POST + in-place PATCH

    payload = {
        "action": "synchronize",
        "installation": {"id": 8801},
        "repository": {"id": 9001, "full_name": "acme/demo-shop"},
        "pull_request": {"number": 12, "head": {"sha": "sha_head_222"}},
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(b"hook-secret", raw, hashlib.sha256).hexdigest()

    ev1 = await GitHubWebhookService.receive_webhook(
        db_session,
        delivery_id="deliv-pr-12",
        event_type="pull_request",
        raw_body=raw,
        payload=payload,
        signature_header=sig,
        webhook_secret=None,
    )
    ev2 = await GitHubWebhookService.receive_webhook(
        db_session,
        delivery_id="deliv-pr-12",
        event_type="pull_request",
        raw_body=raw,
        payload=payload,
        signature_header=sig,
        webhook_secret=None,
    )
    assert ev1.id == ev2.id
    await GitHubWebhookService.process_event(db_session, ev1, payload=payload)
    assert ev1.processing_status == WebhookProcessingStatus.PROCESSED


@pytest.mark.asyncio
async def test_forbidden_ops_scope_violations_and_category_bc_approvals(
    db_session: AsyncSession,
) -> None:
    # Forbidden ops -> PolicyCategory.DENY + PolicyViolationError
    for forbidden in ("github.merge_pr", "github.push_main", "github.force_push", "unknown.action"):
        assert classify_action(forbidden) == PolicyCategory.DENY
        with pytest.raises(PolicyViolationError):
            await PolicyEngine.evaluate_action(db_session, action_type=forbidden)

    # GitHubClient refuses direct commit/PR to main/master/base_branch
    transport, _ = _build_mock_github_transport()
    gh = GitHubClient(token="ghp_test_token_123456", transport=transport)
    with pytest.raises(ValueError, match="protected/base branch"):
        await gh.create_branch_and_draft_pr(
            owner="acme",
            repo="demo-shop",
            base_branch="main",
            branch_name="main",
            files=[{"path": "a.py", "content": "x = 1\n"}],
            commit_message="bad",
            pr_title="bad",
            pr_body="bad",
        )

    # Category B (non-prod / Draft PR / create_flag) vs Category C (production)
    assert classify_action("github.create_branch_and_draft_pr") == PolicyCategory.B
    assert classify_action("flagsmith.create_flag") == PolicyCategory.B
    assert classify_action("flagsmith.toggle_flag", environment_name="development") == PolicyCategory.B
    assert classify_action("flagsmith.toggle_flag", environment_name="production") == PolicyCategory.C
    assert classify_action("flagsmith.toggle_flag", environment_name=None) == PolicyCategory.C  # fail-closed

    # Approval creation + canonical action_hash + single-use execution
    approval = await PolicyEngine.create_approval(
        db_session,
        action_type="github.create_branch_and_draft_pr",
        target_resource_type="code_change",
        target_resource_id="cc-1",
        environment_name="development",
        title="Open Draft PR for checkout_v2",
        action_payload={"code_change_id": "cc-1", "base_branch": "main"},
    )
    assert approval.policy_category == PolicyCategory.B
    assert len(approval.action_hash) == 64

    prod_approval = await PolicyEngine.create_approval(
        db_session,
        action_type="flagsmith.toggle_flag",
        target_resource_type="feature_flag",
        target_resource_id="checkout_v2",
        environment_name="production",
        title="Enable checkout_v2 in production",
        action_payload={"flag_key": "checkout_v2", "enabled": True},
    )
    assert prod_approval.policy_category == PolicyCategory.C
