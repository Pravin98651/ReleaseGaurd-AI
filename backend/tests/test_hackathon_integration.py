"""Tests for cohesive Flagsmith Hackathon integration (Phases 2, 6, 8, 10)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.supervisor import route_specialist
from app.agents.tools import FLAG_AGENT_TOOLS, execute_tool_call
from app.agents.tools.context import ToolExecutionContext
from app.models import ActorType, PolicyCategory
from app.schemas.tools import validate_tool_args
from app.services.feature_request_planner import plan_feature_request_from_prompt
from app.services.flagsmith_service import FlagsmithGovernanceService
from app.services.policy_engine import PolicyEngine


def test_supervisor_routes_all_six_hackathon_intents() -> None:
    assert (
        route_specialist(
            "Add a new recommendation engine using personalized recommendations. Protect it with a feature flag."
        )
        == "flag_agent"
    )
    assert route_specialist("Review PR 25 and identify changes that should be protected by feature flags.") == "pr_agent"
    assert route_specialist("Compare the new recommendation engine against the existing implementation.") == "experiment_agent"
    assert route_specialist("The experiment has finished. Prepare the winning version for approval.") == "experiment_agent"
    assert route_specialist("Find stale feature flags in this repository and prepare cleanup PRs.") == "pr_agent"
    assert route_specialist("Explain which feature flags user_123 receives in the staging environment.") == "flag_agent"


@pytest.mark.asyncio
async def test_new_tools_registered_and_category_a(db_session: AsyncSession) -> None:
    assert "flagsmith_explain_identity" in FLAG_AGENT_TOOLS
    assert "plan_feature_request_rollout" in FLAG_AGENT_TOOLS
    cat1, req1 = await PolicyEngine.evaluate_action(
        db_session,
        action_type="flagsmith_explain_identity",
        environment_name="development",
        actor_type=ActorType.AGENT,
        actor_id="flag_agent",
    )
    assert cat1 == PolicyCategory.A
    assert req1 is False

    cat2, req2 = await PolicyEngine.evaluate_action(
        db_session,
        action_type="plan_feature_request_rollout",
        environment_name="development",
        actor_type=ActorType.AGENT,
        actor_id="flag_agent",
    )
    assert cat2 == PolicyCategory.A
    assert req2 is False

    validated = validate_tool_args(
        "flagsmith_explain_identity",
        {"identifier": "user_123", "environment_name": "development", "traits": {"plan": "beta"}},
    )
    assert validated.identifier == "user_123"


@pytest.mark.asyncio
async def test_plan_feature_request_from_prompt_valid_and_invalid(
    db_session: AsyncSession,
) -> None:
    plan = await plan_feature_request_from_prompt(
        db_session,
        prompt="Add a new recommendation engine using personalized recommendations. Protect it with a feature flag and compare against control.",
        environment_name="development",
        create_release=True,
    )
    assert plan["flag_required"] is True
    assert plan["recommended_flag_key"] == "personalized_recommendation_v2"
    assert plan["flag_type"] == "MULTIVARIATE"
    assert plan["safe_default"] == {"enabled": False, "value": "control"}
    assert plan["evidence_class"] == "AI_INFERRED"
    assert plan["release_id"] is not None
    assert plan["release_key"].startswith("REL-")

    with pytest.raises(ValueError, match="at least 8 characters"):
        await plan_feature_request_from_prompt(db_session, prompt="short")


@pytest.mark.asyncio
async def test_explain_identity_redacts_sensitive_traits_and_distinguishes_evidence(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeFlagsmithClient:
        async def evaluate_identity_detailed(self, env_key: str, *, identifier: str, traits=None):
            return {
                "identifier": identifier,
                "traits": traits or {},
                "flags": {
                    "checkout_v2": {"feature": "checkout_v2", "enabled": True, "value": "v2"},
                    "smart_product_search": {"feature": "smart_product_search", "enabled": False, "value": "control"},
                },
            }

    ctx = ToolExecutionContext(session=db_session)
    monkeypatch.setattr(ctx, "resolve_env_key", lambda env_name, **kw: _async_val("env_dev_key"))
    monkeypatch.setattr(ctx, "get_flagsmith_client", lambda: _async_val(_FakeFlagsmithClient()))

    res = await FlagsmithGovernanceService.explain_identity(
        ctx,
        environment_name="development",
        identifier="user_123",
        traits={"subscription_plan": "beta", "api_token": "secret-123", "password": "pw"},
    )
    assert res["identifier"] == "user_123"
    assert res["consistent_on_repeat_evaluation"] is True
    assert res["traits"]["subscription_plan"] == "beta"
    assert res["traits"]["api_token"] == "[REDACTED]"
    assert res["traits"]["password"] == "[REDACTED]"
    assert len(res["flag_explanations"]) == 2
    assert res["flag_explanations"][0]["evaluation_source"] == "OBSERVED_EDGE_API"
    assert res["flag_explanations"][0]["explanation_source"] == "INFERRED_FROM_EDGE_RESPONSE"
    assert "| `checkout_v2` |" in res["markdown_summary"]

    tool_out = await execute_tool_call(
        ctx,
        "flagsmith_explain_identity",
        {"identifier": "user_123", "environment_name": "development"},
    )
    assert tool_out["consistent_on_repeat_evaluation"] is True


async def _async_val(val):
    return val


@pytest.mark.asyncio
async def test_admin_cannot_self_approve_category_c(
    async_engine,
    session_factory,
) -> None:
    """Regression test: even ADMIN users must be blocked from self-approving Category C."""
    import uuid
    from datetime import timedelta
    from httpx import ASGITransport, AsyncClient
    from app.core.database import utcnow
    from app.core.security import hash_password
    from app.main import create_app
    from app.models import Approval, ApprovalCategory, ApprovalStatus, User, UserRole

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )
    app.state.require_auth_override = True

    async with session_factory() as session:
        admin = User(
            email=f"admin_{uuid.uuid4().hex[:6]}@releaseguard.local",
            display_name="Admin Operator",
            role=UserRole.ADMIN,
            password_hash=hash_password("AdminPass!123"),
        )
        session.add(admin)
        await session.flush()
        app_req = Approval(
            requested_by_id=admin.id,
            policy_category=ApprovalCategory.C,
            action_type="flagsmith_toggle_flag",
            target_resource_type="feature_flag",
            target_resource_id=str(uuid.uuid4()),
            environment_name="production",
            title="Enable checkout_v2 in production",
            rationale="Regression test",
            action_payload={"flag_key": "checkout_v2", "environment_name": "production", "enabled": True},
            expected_precondition={"enabled": False},
            action_hash=f"hash_{uuid.uuid4().hex}",
            idempotency_key=f"idem_{uuid.uuid4().hex}",
            status=ApprovalStatus.PENDING,
            expires_at=utcnow() + timedelta(minutes=15),
        )
        session.add(app_req)
        await session.commit()
        admin_email = admin.email
        app_id = app_req.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login_r = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_email, "password": "AdminPass!123"},
        )
        assert login_r.status_code == 200
        tok = login_r.json()["access_token"]
        r = await client.post(
            f"/api/v1/approvals/{app_id}/approve",
            headers={"Authorization": f"Bearer {tok}"},
            json={"comment": "Attempting self-approval"},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"


def test_flag_opportunity_schema_normalizes_legacy_boolean_flag_type() -> None:
    """Regression test: legacy DB rows with flag_type='boolean' serialize cleanly."""
    from app.models.enums import FlagType
    from app.schemas.repositories import FlagOpportunitySchema

    item = FlagOpportunitySchema.model_validate(
        {
            "suggested_flag_key": "express_checkout_promo",
            "flag_type": "boolean",
            "target_files": ["demo-app/backend/app/checkout.py"],
            "rationale": "Guard express promo path",
        }
    )
    assert item.flag_type == FlagType.STANDARD

    mv = FlagOpportunitySchema.model_validate(
        {
            "suggested_flag_key": "checkout_exp",
            "flag_type": "multivariate",
            "rationale": "Compare variants",
        }
    )
    assert mv.flag_type == FlagType.MULTIVARIATE
