"""Automated Feature Flag & Flagsmith Testing Framework (Phase 9 & Phase 15)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.core.database import utcnow
from app.core.security import compute_action_hash
from app.integrations.flagsmith_client import FlagsmithAPIError, FlagsmithClient
from app.main import create_app
from app.models import (
    Approval,
    ApprovalCategory,
    ApprovalStatus,
    Experiment,
    ExperimentStatus,
    FeatureFlag,
    FlagType,
)
from app.services.flagsmith_service import (
    FLAG_CATALOG,
    TARGETING_PRESETS,
    FlagsmithGovernanceService,
    get_flag_safe_default,
)
from app.services.policy_engine import (
    ApprovalExpiredError,
    PolicyEngine,
    classify_action,
)


@pytest.mark.parametrize("flag_key", sorted(FLAG_CATALOG.keys()))
def test_every_catalog_flag_has_owner_safe_default_code_and_tests(flag_key: str) -> None:
    """Every registered flag defines real owner, code references, linked tests, and safe default."""
    spec = FLAG_CATALOG[flag_key]
    assert spec.owner
    assert spec.purpose
    assert len(spec.code_references) >= 1
    assert len(spec.linked_tests) >= 1
    assert spec.is_security_boundary is False
    default_enabled, safe_val = get_flag_safe_default(flag_key)
    assert default_enabled == spec.default_enabled
    assert safe_val == spec.safe_default_value


@pytest.mark.asyncio
async def test_capability_flag_falls_back_to_per_flag_safe_default_on_failure(
    async_engine,
    session_factory,
) -> None:
    """When Flagsmith Edge fails/times out, operational kill switches fail safe (not global False)."""
    def failing_transport(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Flagsmith Edge unreachable")

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        flagsmith_transport=httpx.MockTransport(failing_transport),
        bootstrap_env_tokens=False,
    )
    async with session_factory() as s:
        pr_cap = await FlagsmithGovernanceService.evaluate_capability_flag(
            app=app, session=s, flag_key="enable_ai_pr_review"
        )
        checkout_def_enabled, checkout_def_val = get_flag_safe_default("checkout_experience_v2")

    assert pr_cap["enabled"] is True  # Safe operational default preserves PR review capability
    assert pr_cap["source"] == "safe_default"
    assert pr_cap["authoritative_security_unchanged"] is True
    assert checkout_def_enabled is False  # Experimental checkout safely off by default
    assert checkout_def_val == "control"


@pytest.mark.asyncio
async def test_identity_targeting_consistency_and_edge_reason_explanation() -> None:
    """Same identity + traits receive consistent variant and real Flagsmith Edge reason."""
    def edge_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/identities/" and request.method == "POST":
            body = json.loads(request.content.decode())
            ident = body["identifier"]
            traits = {t["trait_key"]: t["trait_value"] for t in body.get("traits", [])}
            is_internal = traits.get("is_internal_tester") in (True, "true")
            var = "one_page_checkout" if is_internal else ("simplified_checkout" if hash(ident) % 2 == 0 else "control")
            return httpx.Response(
                200,
                json={
                    "identifier": ident,
                    "traits": body.get("traits", []),
                    "flags": [
                        {
                            "feature": {"id": 501, "name": "checkout_experience_v2", "type": "MULTIVARIATE"},
                            "enabled": True,
                            "feature_state_value": var,
                            "reason": "TARGETING_MATCH; segment=rg_internal_testers" if is_internal else "SPLIT",
                            "variant": var,
                        }
                    ],
                },
            )
        return httpx.Response(404)

    client = FlagsmithClient(
        org_token="tok",
        transport=httpx.MockTransport(edge_handler),
        retry_delay_base=0.0,
    )
    first = await client.evaluate_identity_detailed(
        "dev_key",
        identifier="qa_tester_1",
        traits={"user_type": "internal", "is_internal_tester": True, "subscription_plan": "enterprise"},
    )
    second = await client.evaluate_identity_detailed(
        "dev_key",
        identifier="qa_tester_1",
        traits={"user_type": "internal", "is_internal_tester": True, "subscription_plan": "enterprise"},
    )
    assert first["flags"]["checkout_experience_v2"]["value"] == "one_page_checkout"
    assert "TARGETING_MATCH" in first["flags"]["checkout_experience_v2"]["reason"]
    assert first["flags"] == second["flags"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 502])
async def test_flagsmith_api_failures_never_fabricate_success(status_code: int) -> None:
    """Unauthorized, forbidden, rate-limited, and server error responses raise FlagsmithAPIError."""
    def err_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": f"Simulated HTTP {status_code}"})

    client = FlagsmithClient(
        org_token="bad_tok",
        transport=httpx.MockTransport(err_handler),
        max_retries=1,
        retry_delay_base=0.0,
    )
    with pytest.raises(FlagsmithAPIError) as exc_info:
        await client.list_flags(56055)
    assert exc_info.value.status_code == status_code


@pytest.mark.asyncio
async def test_lifecycle_manager_detects_stale_and_unprovisioned_flags(db_session) -> None:
    """Lifecycle report detects review_date_expired, missing code refs, and inconsistent envs."""
    stale_flag = FeatureFlag(
        flagsmith_project_id=56055,
        flagsmith_feature_id=999999,
        key="legacy_banner_test",
        description="Old temporary promo flag",
        flag_type=FlagType.STANDARD,
        env_states={
            "development": {"enabled": True},
            "production": {"enabled": False},
        },
        code_references=[],
        created_at=utcnow() - timedelta(days=45),
    )
    db_session.add(stale_flag)
    await db_session.commit()

    report = await FlagsmithGovernanceService.get_catalog_report(db_session)
    legacy = next(item for item in report if item["key"] == "legacy_banner_test")
    assert legacy["stale"] is True
    assert legacy["inconsistent_environments"] is True
    assert legacy["never_auto_delete"] is True
    assert "review_date_expired" in legacy["cleanup_recommendations"]
    assert "no_code_references_scanned" in legacy["cleanup_recommendations"]


@pytest.mark.asyncio
async def test_synthetic_harness_blocked_in_production_and_labeled_in_dev(
    async_engine,
    session_factory,
) -> None:
    """Synthetic harness runs safely in dev with explicit synthetic label and is forbidden in prod."""
    from app.agents.tools.context import ToolExecutionContext

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/identities/":
            body = json.loads(request.content.decode())
            return httpx.Response(200, json={"identifier": body["identifier"], "flags": []})
        return httpx.Response(200, json={"results": []})

    async with session_factory() as session:
        flag = FeatureFlag(
            flagsmith_project_id=56055,
            flagsmith_feature_id=303933,
            key="recommendation_engine_v2",
            flag_type=FlagType.MULTIVARIATE,
        )
        session.add(flag)
        await session.flush()

        dev_exp = Experiment(
            feature_flag_id=flag.id,
            name="Recs Test",
            hypothesis="Test",
            environment_name="development",
            environment_key="dev_key",
            status=ExperimentStatus.RUNNING,
            control_variation="control",
            treatment_variation="collaborative_v2",
            primary_metric="product_clicked_from_recommendation",
        )
        prod_exp = Experiment(
            feature_flag_id=flag.id,
            name="Prod Recs",
            hypothesis="Test",
            environment_name="production",
            environment_key="prod_key",
            status=ExperimentStatus.RUNNING,
            control_variation="control",
            treatment_variation="collaborative_v2",
            primary_metric="product_clicked_from_recommendation",
        )
        session.add_all([dev_exp, prod_exp])
        await session.commit()

        fs_client = FlagsmithClient(
            org_token="tok",
            transport=httpx.MockTransport(handler),
            retry_delay_base=0.0,
        )
        ctx = ToolExecutionContext(session=session, flagsmith_client=fs_client)

        res = await FlagsmithGovernanceService.run_experiment_traffic_harness(
            ctx, experiment=dev_exp, identities_per_arm=5
        )
        assert res["synthetic_test_traffic"] is True
        assert res["identities_evaluated"] == 10

        with pytest.raises(ValueError):
            await FlagsmithGovernanceService.run_experiment_traffic_harness(
                ctx, experiment=prod_exp, identities_per_arm=5
            )
