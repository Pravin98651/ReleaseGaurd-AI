"""Comprehensive tests for the Intelligent Release Risk Engine."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    DataSufficiency,
    DimensionStatus,
    FeatureFlag,
    FlagSuitability,
    FlagType,
    MitigationStatus,
    PullRequest,
    PullRequestState,
    Repository,
    RiskAssessmentStatus,
    RiskClassification,
    RiskLevel,
)
from app.services.release_orchestrator import ReleaseOrchestrator
from app.services.risk_engine.analyzers_code_deps_hist import (
    CodeImpactAnalyzer,
    DependencyImpactAnalyzer,
    HistoricalFailureAnalyzer,
)


@pytest.mark.asyncio
async def test_analyzers_honesty_and_insufficient_history(db_session) -> None:
    # Empty diff -> honest unavailable/partial, no fabricated score
    empty_code = CodeImpactAnalyzer.analyze([])
    assert empty_code.status == DimensionStatus.UNAVAILABLE
    assert empty_code.score is None

    # Sensitive payment + migration files
    files = [
        {
            "filename": "backend/payment/validation.py",
            "status": "modified",
            "additions": 35,
            "deletions": 8,
            "patch": "from payment.gateway import verify_card\n+def validate_checkout_v2(): pass",
        },
        {
            "filename": "backend/alembic/versions/0099_drop.py",
            "status": "added",
            "additions": 15,
            "deletions": 0,
            "patch": "+op.drop_column('orders', 'legacy_total')",
        },
        {
            "filename": "assets/logo.png",
            "status": "modified",
            "additions": 0,
            "deletions": 0,
            "binary": True,
        },
    ]
    code_res = CodeImpactAnalyzer.analyze(files)
    assert code_res.status == DimensionStatus.EVALUATED
    assert code_res.score is not None and code_res.score >= 50
    assert "payment" in code_res.details["affected_categories"]
    assert any("Binary" in lim for lim in code_res.limitations)

    dep_res = DependencyImpactAnalyzer.analyze(
        files,
        repo_files={"backend/checkout/service.py": "from payment.validation import validate_checkout_v2"},
    )
    assert "backend/checkout/service.py" in dep_res.details["indirect_consumers"]
    assert any("vulnerability" in lim.lower() for lim in dep_res.limitations)

    # Zero historical records -> INSUFFICIENT_DATA, score is None
    hist_res = await HistoricalFailureAnalyzer.analyze(db_session)
    assert hist_res.data_sufficiency == DataSufficiency.INSUFFICIENT_DATA
    assert hist_res.score is None


@pytest.mark.asyncio
async def test_risk_assessment_api_and_novashop_checkout_v2_e2e(
    async_engine, session_factory, db_session
) -> None:
    repo = Repository(
        github_repo_id=777,
        owner="novashop",
        name="demo-app",
        full_name="novashop/demo-app",
    )
    db_session.add(repo)
    await db_session.flush()

    pr = PullRequest(
        repository_id=repo.id,
        github_pr_number=101,
        title="Checkout v2 payment validation",
        author_login="eng",
        head_branch="feat/checkout-v2",
        base_branch="main",
        head_sha="sha_v1",
        state=PullRequestState.OPEN,
        html_url="https://github.com/novashop/demo-app/pull/101",
        risk_level=RiskLevel.HIGH,
    )
    flag = FeatureFlag(
        flagsmith_project_id=56055,
        repository_id=repo.id,
        key="checkout_v2",
        flag_type=FlagType.STANDARD,
    )
    db_session.add_all([pr, flag])
    await db_session.flush()

    rel = await ReleaseOrchestrator.create_release(
        db_session,
        title="NovaShop Checkout v2",
        repository_id=repo.id,
        pull_request_id=pr.id,
        feature_flag_id=flag.id,
        target_environment="production",
    )
    await db_session.commit()

    async with session_factory() as boot_sess:
        await bootstrap_workspace_defaults(boot_sess, bootstrap_env_tokens=False)
        await boot_sess.commit()

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        bootstrap_env_tokens=False,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post(
            "/api/v1/risk-assessments",
            json={
                "release_id": str(rel.id),
                "target_environment": "production",
                "files_override": [
                    {
                        "filename": "checkout/payment_service.py",
                        "status": "modified",
                        "additions": 42,
                        "deletions": 5,
                        "patch": "+if is_feature_enabled('checkout_v2'):\n+    process_payment()",
                    },
                    {
                        "filename": "alembic/versions/001_drop_old.py",
                        "status": "added",
                        "additions": 10,
                        "deletions": 0,
                        "patch": "+op.drop_column('checkout', 'old_col')",
                    },
                ],
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        detail = create_resp.json()
        assess = detail["assessment"]
        assert assess["assessment_key"].startswith("RSK-")
        # Destructive migration in production triggers critical override
        assert assess["risk_level"] == RiskClassification.CRITICAL.value
        assert "destructive_migration_in_production" in assess["critical_overrides"]
        assert assess["flag_suitability"] in {
            FlagSuitability.HIGHLY_SUITABLE.value,
            FlagSuitability.LIMITED_SUITABILITY.value,
        }
        assert len(detail["dimensions"]) == 7
        assert len(detail["scenarios"]) >= 1
        assert len(detail["mitigations"]) >= 1

        # Verify mitigation completion does NOT silently change stored risk_score
        mit_id = detail["mitigations"][0]["id"]
        ver_resp = await client.post(
            f"/api/v1/risk-assessments/{assess['id']}/mitigations/{mit_id}/verify",
            json={"verification_evidence": "Verified in staging"},
        )
        assert ver_resp.status_code == 200
        assert ver_resp.json()["status"] == MitigationStatus.COMPLETED.value

        # Re-analyze creates a new assessment version and links to Command Center
        re_resp = await client.post(f"/api/v1/risk-assessments/{assess['id']}/reanalyze")
        assert re_resp.status_code == 200
        assert re_resp.json()["assessment"]["id"] != assess["id"]

        # Configuration update is versioned
        cfg_resp = await client.patch(
            "/api/v1/risk-engine/configuration",
            json={"dimension_weights": {"code_impact": 0.25, "production_environment": 0.25}},
        )
        assert cfg_resp.status_code == 200
        assert cfg_resp.json()["config_version"] >= 2

        # Repository dependency graph & historical risk endpoints
        dg_resp = await client.get(f"/api/v1/repositories/{repo.id}/dependency-graph")
        assert dg_resp.status_code == 200
        hr_resp = await client.get(f"/api/v1/repositories/{repo.id}/historical-risk")
        assert hr_resp.status_code == 200
        assert hr_resp.json()["data_sufficiency"] == "insufficient_data"

        # Mark commit SHA changed -> old assessment marked OUTDATED
        async with session_factory() as s2:
            pr_obj = await s2.get(PullRequest, pr.id)
            assert pr_obj is not None
            pr_obj.head_sha = "sha_v2"
            await s2.commit()

        r2_resp = await client.post(
            "/api/v1/risk-assessments",
            json={"release_id": str(rel.id), "create_execution": False},
        )
        assert r2_resp.status_code == 201
        old_check = await client.get(f"/api/v1/risk-assessments/{assess['id']}")
        assert old_check.json()["assessment"]["assessment_status"] == RiskAssessmentStatus.OUTDATED.value
