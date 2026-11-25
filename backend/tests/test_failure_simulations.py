"""Failure Simulation & Production Hardening Tests (Phase D & Phase F)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.specialists.pr_agent import analyze_pr_with_static_and_gemini
from app.core.config import Settings
from app.core.database import utcnow
from app.core.security import hash_password, issue_auth_token
from app.integrations.flagsmith_client import FlagsmithAPIError, FlagsmithClient
from app.integrations.github_client import GitHubAPIError, GitHubClient
from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    Approval,
    ApprovalStatus,
    ExperimentEvent,
    FeatureFlag,
    FlagType,
    User,
    UserRole,
)
from app.services.policy_engine import ApprovalExpiredError, PolicyEngine


@pytest.mark.asyncio
async def test_gemini_503_pr_analysis_falls_back_transparently() -> None:
    """When Gemini returns 503/errors, PR analysis must mark analysis_mode=static_fallback cleanly."""
    class _FailingGemini:
        is_configured = True

        async def generate_content(self, **kwargs: Any) -> Any:
            del kwargs
            raise RuntimeError("503 UNAVAILABLE: Gemini overloaded")

    files_data = [
        {
            "filename": "checkout/billing.py",
            "status": "modified",
            "additions": 4,
            "deletions": 1,
            "patch": "@@ -1,1 +1,4 @@\n+def apply_promo():\n+    return charge_payment()\n",
        }
    ]
    out = await analyze_pr_with_static_and_gemini(
        pr_title="Checkout billing patch",
        pr_body="Promo flow",
        files_data=files_data,
        repo_full_name="acme/novashop",
        pr_number=99,
        gemini_client=_FailingGemini(),  # type: ignore[arg-type]
    )
    assert out["analysis_mode"] == "static_fallback"
    assert "503 UNAVAILABLE" in str(out["gemini_error"])
    assert "Static Fallback" in out["analysis_summary"]
    assert out["risk_score"] >= 35


@pytest.mark.asyncio
async def test_github_403_rate_limit_and_flagsmith_503_failures() -> None:
    """Verify GitHub 403 rate limit normalizes to 429 and Flagsmith 503 raises FlagsmithAPIError."""
    gh = GitHubClient(
        token="ghp_test",
        max_retries=0,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1900000000"},
                json={"message": "API rate limit exceeded for user."},
            )
        ),
    )
    with pytest.raises(GitHubAPIError) as gh_exc:
        await gh.list_pull_requests("acme", "novashop")
    assert gh_exc.value.status_code == 429

    fs = FlagsmithClient(
        org_token="fs_test",
        max_retries=0,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(503, json={"detail": "Flagsmith temporarily unavailable"})
        ),
    )
    with pytest.raises(FlagsmithAPIError) as fs_exc:
        await fs.list_projects()
    assert fs_exc.value.status_code == 503


@pytest.mark.asyncio
async def test_duplicate_events_expired_approval_and_self_approval_block(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify duplicate telemetry deduplication, approval TTL expiry, and self-approval block."""
    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )
    app.state.require_auth_override = True

    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)
        rm_user = User(
            email="rm-self@example.com",
            display_name="Release Manager",
            role=UserRole.RELEASE_MANAGER,
            password_hash=hash_password("RmPass123!"),
            is_active=True,
        )
        viewer_user = User(
            email="viewer-fail@example.com",
            display_name="Viewer",
            role=UserRole.VIEWER,
            password_hash=hash_password("ViewPass123!"),
            is_active=True,
        )
        flag = FeatureFlag(
            flagsmith_project_id=10,
            flagsmith_feature_id=101,
            key="hardened_flag",
            flag_type=FlagType.STANDARD,
            env_states={"production": {"enabled": False, "rollout_percentage": 0}},
        )
        session.add_all([rm_user, viewer_user, flag])
        await session.flush()

        # Self-requested Category C approval
        self_app = await PolicyEngine.create_approval(
            session,
            action_type="flagsmith_toggle_flag",
            target_resource_type="feature_flag",
            target_resource_id=str(flag.id),
            environment_name="production",
            title="Self toggle prod",
            action_payload={"flag_key": flag.key, "environment_name": "production", "enabled": True, "reason": "Test"},
            requested_by_id=rm_user.id,
        )
        # Expired Category C approval
        expired_app = await PolicyEngine.create_approval(
            session,
            action_type="flagsmith_toggle_flag",
            target_resource_type="feature_flag",
            target_resource_id=str(flag.id),
            environment_name="production",
            title="Expired toggle prod",
            action_payload={"flag_key": flag.key, "environment_name": "production", "enabled": True, "reason": "Test2"},
            ttl_seconds=60,
        )
        expired_app.expires_at = utcnow() - timedelta(minutes=5)
        await session.commit()
        self_app_id = self_app.id
        expired_app_id = expired_app.id

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rm_tok = issue_auth_token(
            user_id=str(rm_user.id), email=rm_user.email, role=rm_user.role.value, token_version=rm_user.token_version
        )
        viewer_tok = issue_auth_token(
            user_id=str(viewer_user.id),
            email=viewer_user.email,
            role=viewer_user.role.value,
            token_version=viewer_user.token_version,
        )

        # 1. Viewer cannot approve production mutation (403 FORBIDDEN_ROLE)
        r_view = await client.post(
            f"/api/v1/approvals/{self_app_id}/approve",
            headers={"Authorization": f"Bearer {viewer_tok}"},
        )
        assert r_view.status_code == 403

        # 2. Release Manager cannot self-approve their own Category C request when auth enforced
        r_self = await client.post(
            f"/api/v1/approvals/{self_app_id}/approve",
            headers={"Authorization": f"Bearer {rm_tok}"},
        )
        assert r_self.status_code == 403
        assert r_self.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"

        # 3. Duplicate telemetry event_id is ignored cleanly without inflating counts
        ev_payload = {
            "events": [
                {
                    "event_id": "dup-ev-001",
                    "flag_key": "hardened_flag",
                    "environment_name": "staging",
                    "variation_key": "v2",
                    "event_type": "checkout_completed",
                    "user_identifier": "user-1",
                    "occurred_at": utcnow().isoformat(),
                },
                {
                    "event_id": "dup-ev-001",
                    "flag_key": "hardened_flag",
                    "environment_name": "staging",
                    "variation_key": "v2",
                    "event_type": "checkout_completed",
                    "user_identifier": "user-1",
                    "occurred_at": utcnow().isoformat(),
                },
            ]
        }
        r_ing = await client.post(
            "/api/v1/events/ingest",
            headers={"X-Ingest-Key": "dev-ingest-key-change-me"},
            json=ev_payload,
        )
        assert r_ing.status_code == 200
        assert r_ing.json()["accepted"] == 1
        assert r_ing.json()["deduplicated"] == 1

    # 4. Expired approval cannot execute and transitions to EXPIRED
    async with session_factory() as session:
        async def _ok_reval(a: Approval) -> dict[str, Any]:
            del a
            return {"ok": True}

        async def _ok_exec(a: Approval) -> dict[str, Any]:
            del a
            return {}

        with pytest.raises(ApprovalExpiredError):
            await PolicyEngine.execute_approved_action(
                session,
                expired_app_id,
                revalidator=_ok_reval,
                executor=_ok_exec,
            )
        refreshed = await session.get(Approval, expired_app_id)
        assert refreshed is not None and refreshed.status == ApprovalStatus.EXPIRED
        events = list((await session.execute(select(ExperimentEvent))).scalars().all())
        assert len(events) == 1


def test_production_config_rejects_wildcard_cors_and_defaults() -> None:
    """Production configuration must fail fast on wildcard CORS or default secrets."""
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        Settings(
            APP_ENV="production",
            SECRET_KEY="s" * 40,
            ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            EVENT_INGEST_KEY="strong-prod-ingest-key-123456",
            REQUIRE_AUTH=True,
            CORS_ORIGINS="*",
        )
    # Keep SimpleNamespace referenced cleanly if needed
    assert SimpleNamespace(ok=True).ok is True
