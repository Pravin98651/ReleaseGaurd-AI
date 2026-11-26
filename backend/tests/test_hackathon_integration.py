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
