"""API endpoint integration tests via httpx.AsyncClient + ASGITransport across all 11 routes."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.main import bootstrap_workspace_defaults, create_app
from app.models import Approval, ApprovalStatus, IntegrationConnection


def _make_mock_github_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method.upper()
        if path == "/user":
            return httpx.Response(
                200,
                headers={"X-OAuth-Scopes": "read:user, public_repo"},
                json={"id": 4242, "login": "octo-operator", "avatar_url": "https://example.com/a.png"},
            )
        if path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9001,
                        "name": "checkout-service",
                        "full_name": "acme/checkout-service",
                        "owner": {"login": "acme"},
                        "default_branch": "main",
                        "private": False,
                        "language": "Python",
                    }
                ],
            )
        if path == "/repos/acme/checkout-service":
            return httpx.Response(
                200,
                json={
                    "id": 9001,
                    "name": "checkout-service",
                    "full_name": "acme/checkout-service",
                    "owner": {"login": "acme"},
                    "default_branch": "main",
                    "private": False,
                    "language": "Python",
                },
            )
        if path == "/repos/acme/checkout-service/branches":
            return httpx.Response(
                200,
                json=[{"name": "main", "commit": {"sha": "abc123"}, "protected": True}],
            )
        if path == "/repos/acme/checkout-service/pulls" and method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 12,
                        "title": "Add express checkout v2 pricing",
                        "body": "Faster checkout route",
                        "state": "open",
                        "draft": False,
                        "html_url": "https://github.com/acme/checkout-service/pull/12",
                        "user": {"login": "dev1"},
                        "head": {"ref": "feat/checkout-v2", "sha": "headsha12"},
                        "base": {"ref": "main"},
                    }
                ],
            )
        if path == "/repos/acme/checkout-service/pulls/12/files":
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "app/checkout.py",
                        "status": "modified",
                        "additions": 18,
                        "deletions": 2,
                        "patch": "@@ -1,3 +1,6 @@\n+def process_checkout_v2(total, promo):\n+    return total * 0.9\n",
                    }
                ],
            )
        if path == "/repos/acme/checkout-service/git/trees/main":
            return httpx.Response(
                200,
                json={"tree": [{"path": "app/checkout.py", "type": "blob"}]},
            )
        if path == "/repos/acme/checkout-service/contents/app/checkout.py":
            import base64

            raw = base64.b64encode(
                b'def run(flags):\n    return flags.is_feature_enabled("checkout_v2")\n'
            ).decode()
            return httpx.Response(200, json={"sha": "f1", "encoding": "base64", "content": raw})
        if path == "/repos/acme/checkout-service/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "base111"}})
        if path == "/repos/acme/checkout-service/git/commits/base111":
            return httpx.Response(200, json={"tree": {"sha": "tree111"}})
        if path == "/repos/acme/checkout-service/git/refs" and method == "POST":
            return httpx.Response(201, json={"ref": "refs/heads/releaseguard/patch"})
        if path == "/repos/acme/checkout-service/git/blobs" and method == "POST":
            return httpx.Response(201, json={"sha": "blob222"})
        if path == "/repos/acme/checkout-service/git/trees" and method == "POST":
            return httpx.Response(201, json={"sha": "tree222"})
        if path == "/repos/acme/checkout-service/git/commits" and method == "POST":
            return httpx.Response(201, json={"sha": "commit222"})
        if path.startswith("/repos/acme/checkout-service/git/refs/heads/") and method == "PATCH":
            return httpx.Response(200, json={"object": {"sha": "commit222"}})
        if path == "/repos/acme/checkout-service/pulls" and method == "POST":
            payload = json.loads(request.content.decode())
            assert payload["draft"] is True
            return httpx.Response(
                201,
                json={
                    "number": 99,
                    "html_url": "https://github.com/acme/checkout-service/pull/99",
                    "draft": True,
                },
            )
        return httpx.Response(404, json={"message": f"Mock GitHub 404: {method} {path}"})

    return httpx.MockTransport(handler)


def _make_mock_flagsmith_transport() -> tuple[httpx.MockTransport, dict[str, Any]]:
    state: dict[str, Any] = {
        "features": [
            {"id": 501, "name": "checkout_v2", "type": "STANDARD", "description": "Checkout v2"}
        ],
        "env_states": {
            "env_dev": {"enabled": False, "seg_enabled": False, "pct": 0},
            "env_prod": {"enabled": False, "seg_enabled": False, "pct": 0},
        },
        "segment_pct": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method.upper()
        if path.endswith("/projects/77/environments/") and method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "name": "development",
                        "api_key": "env_dev",
                        "is_production": False,
                        "use_v2_feature_versioning": False,
                    },
                    {
                        "id": 2,
                        "name": "production",
                        "api_key": "env_prod",
                        "is_production": True,
                        "use_v2_feature_versioning": True,
                    },
                ],
            )
        if path.endswith("/environments/env_dev/") and method == "GET":
            return httpx.Response(
                200,
                json={"id": 1, "name": "development", "api_key": "env_dev", "use_v2_feature_versioning": False},
            )
        if path.endswith("/environments/env_prod/") and method == "GET":
            return httpx.Response(
                200,
                json={"id": 2, "name": "production", "api_key": "env_prod", "use_v2_feature_versioning": True},
            )
        if path.endswith("/experiments/") and method == "GET":
            return httpx.Response(404, json={"detail": "Warehouse not configured"})
        if path.endswith("/projects/77/features/") and method == "GET":
            return httpx.Response(200, json=state["features"])
        if path.endswith("/projects/77/segments/") and method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 88,
                        "name": "rg_rollout_checkout_v2",
                        "rules": [
                            {
                                "type": "ALL",
                                "conditions": [],
                                "rules": [
                                    {
                                        "type": "ANY",
                                        "conditions": [
                                            {
                                                "operator": "PERCENTAGE_SPLIT",
                                                "value": str(state["segment_pct"]),
                                            }
                                        ],
                                        "rules": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            )
        if path.endswith("/projects/77/segments/88/") and method == "PUT":
            body = json.loads(request.content.decode())
            state["segment_pct"] = int(body["rules"][0]["rules"][0]["conditions"][0]["value"])
            return httpx.Response(200, json={"id": 88, "name": "rg_rollout_checkout_v2"})
        for env_key in ("env_dev", "env_prod"):
            if f"/environments/{env_key}/featurestates/" in path and method == "GET":
                st = state["env_states"][env_key]
                return httpx.Response(
                    200,
                    json=[
                        {"id": 1001, "feature": 501, "enabled": st["enabled"], "feature_segment": None},
                        {
                            "id": 1002,
                            "feature": 501,
                            "enabled": st["seg_enabled"],
                            "feature_segment": {"segment": 88, "priority": 0},
                        },
                    ],
                )
        if "/environments/env_dev/featurestates/1001/" in path and method == "PATCH":
            body = json.loads(request.content.decode())
            state["env_states"]["env_dev"]["enabled"] = bool(body.get("enabled", False))
            return httpx.Response(200, json={"id": 1001, "enabled": state["env_states"]["env_dev"]["enabled"]})
        if "/environments/env_dev/featurestates/1002/" in path and method == "PATCH":
            body = json.loads(request.content.decode())
            state["env_states"]["env_dev"]["seg_enabled"] = bool(body.get("enabled", False))
            return httpx.Response(200, json={"id": 1002, "enabled": state["env_states"]["env_dev"]["seg_enabled"]})
        if path.endswith("/environments/env_prod/features/501/versions/") and method == "POST":
            return httpx.Response(201, json={"uuid": "v2-prod-1"})
        if "/versions/v2-prod-1/featurestates/" in path and method == "GET":
            st = state["env_states"]["env_prod"]
            return httpx.Response(
                200,
                json=[
                    {"id": 2001, "feature": 501, "enabled": st["enabled"], "feature_segment": None},
                    {
                        "id": 2002,
                        "feature": 501,
                        "enabled": st["seg_enabled"],
                        "feature_segment": {"segment": 88, "priority": 0},
                    },
                ],
            )
        if "/versions/v2-prod-1/featurestates/2001/" in path and method == "PATCH":
            body = json.loads(request.content.decode())
            if "enabled" in body:
                state["env_states"]["env_prod"]["enabled"] = bool(body["enabled"])
            return httpx.Response(200, json={"id": 2001, "enabled": state["env_states"]["env_prod"]["enabled"]})
        if "/versions/v2-prod-1/featurestates/2002/" in path and method == "PATCH":
            body = json.loads(request.content.decode())
            if "enabled" in body:
                state["env_states"]["env_prod"]["seg_enabled"] = bool(body["enabled"])
            return httpx.Response(200, json={"id": 2002, "enabled": state["env_states"]["env_prod"]["seg_enabled"]})
        if path.endswith("/versions/v2-prod-1/publish/") and method == "POST":
            st = state["env_states"]["env_prod"]
            st["pct"] = state["segment_pct"] if st["seg_enabled"] else (100 if st["enabled"] else 0)
            return httpx.Response(200, json={"uuid": "v2-prod-1", "live_from": None})
        return httpx.Response(404, json={"detail": f"Mock Flagsmith 404: {method} {path}"})

    return httpx.MockTransport(handler), state


async def _make_test_app(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Any, dict[str, Any]]:
    gh_transport = _make_mock_github_transport()
    fs_transport, fs_state = _make_mock_flagsmith_transport()
    fake_genai = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **kwargs: SimpleNamespace(
                text="Agent completed successfully.",
                function_calls=[],
                candidates=[],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=10, candidates_token_count=5, total_token_count=15
                ),
            )
        )
    )
    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        github_transport=gh_transport,
        flagsmith_transport=fs_transport,
        gemini_raw_client=fake_genai,
        bootstrap_env_tokens=False,
    )
    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)
    return app, fs_state


@pytest.mark.asyncio
async def test_integrations_encryption_settings_and_11_routes_data(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, _ = await _make_test_app(async_engine, session_factory)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. System /health & /dashboard/summary
        r_health = await client.get("/health")
        assert r_health.status_code == 200
        assert r_health.json()["status"] == "ok"
        assert r_health.json()["database"] == "sqlite"

        r_dash = await client.get("/api/v1/dashboard/summary")
        assert r_dash.status_code == 200
        assert "counts" in r_dash.json()

        # 2. Settings GET & PATCH
        r_set = await client.get("/api/v1/settings")
        assert r_set.status_code == 200
        assert r_set.json()["category_b_requires_confirmation"] is True

        r_patch = await client.patch(
            "/api/v1/settings",
            json={"category_b_requires_confirmation": False, "approval_ttl_seconds": 600},
        )
        assert r_patch.status_code == 200
        assert r_patch.json()["category_b_requires_confirmation"] is False

        # 3. Connect GitHub PAT & Flagsmith (verify token never leaked and Fernet encrypted in DB)
        secret_pat = "ghp_super_secret_pat_998877"
        r_gh = await client.post("/api/v1/integrations/github/pat", json={"token": secret_pat})
        assert r_gh.status_code == 200
        assert secret_pat not in r_gh.text
        assert r_gh.json()["token_hint"].endswith("8877")

        secret_fs = "fs_org_secret_token_4455"
        r_fs = await client.post(
            "/api/v1/integrations/flagsmith",
            json={
                "org_token": secret_fs,
                "project_id": 77,
                "environment_keys": {"development": "env_dev", "production": "env_prod"},
            },
        )
        assert r_fs.status_code == 200
        assert secret_fs not in r_fs.text

        r_ints = await client.get("/api/v1/integrations")
        assert r_ints.status_code == 200
        assert secret_pat not in r_ints.text and secret_fs not in r_ints.text

        # Verify encrypted at rest in DB
        async with session_factory() as session:
            conns = (await session.execute(select(IntegrationConnection))).scalars().all()
            assert len(conns) == 2
            for c in conns:
                assert c.access_token_encrypted.startswith("gAAAAA")
                assert decrypt_token(c.access_token_encrypted) in {secret_pat, secret_fs}

        # 4. Repositories, Scan Flags, Pull Requests, Feature Flags, Health, Releases, Experiments, Agent, Approvals, Audit
        r_sync_repos = await client.post("/api/v1/repositories/sync")
        assert r_sync_repos.status_code == 200
        repo_id = r_sync_repos.json()["items"][0]["id"]

        assert (await client.get(f"/api/v1/repositories/{repo_id}/branches")).status_code == 200
        assert (await client.post(f"/api/v1/repositories/{repo_id}/scan-flags")).status_code == 200
        assert (await client.post(f"/api/v1/repositories/{repo_id}/pull-requests/sync")).status_code == 200
        assert (await client.get("/api/v1/pull-requests")).status_code == 200
        assert (await client.post("/api/v1/feature-flags/sync", params={"project_id": 77})).status_code == 200
        assert (await client.get("/api/v1/feature-flags")).status_code == 200
        assert (await client.post("/api/v1/flag-health/scan")).status_code == 200
        assert (await client.get("/api/v1/flag-health")).status_code == 200
        assert (await client.get("/api/v1/releases/overview")).status_code == 200
        assert (await client.get("/api/v1/experiments")).status_code == 200
        assert (await client.get("/api/v1/approvals")).status_code == 200
        assert (await client.get("/api/v1/rollout-history")).status_code == 200

        # Agent session + dispatch
        r_sess = await client.post("/api/v1/agent/sessions", json={"title": "API Test Session"})
        assert r_sess.status_code == 201
        sess_id = r_sess.json()["id"]
        r_msg = await client.post(
            f"/api/v1/agent/sessions/{sess_id}/messages",
            json={"content": "Summarize flags", "specialist_hint": "flag_agent"},
        )
        assert r_msg.status_code == 202
        task_id = r_msg.json()["task_id"]
        r_task = await client.get(f"/api/v1/agent/tasks/{task_id}", params={"after_seq": 0})
        assert r_task.status_code == 200

        # Export JSON & CSV audit logs
        r_aud = await client.get("/api/v1/audit-logs")
        assert r_aud.status_code == 200 and r_aud.json()["total"] >= 1
        r_exp_json = await client.get("/api/v1/audit-logs/export", params={"format": "json"})
        assert r_exp_json.status_code == 200
        assert "items" in json.loads(r_exp_json.text)
        r_exp_csv = await client.get("/api/v1/audit-logs/export", params={"format": "csv"})
        assert r_exp_csv.status_code == 200
        assert "event_action" in r_exp_csv.text

        # Disconnect provider
        r_del = await client.delete("/api/v1/integrations/github")
        assert r_del.status_code == 200


@pytest.mark.asyncio
async def test_events_ingest_auth_and_deduplication(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, _ = await _make_test_app(async_engine, session_factory)
    ingest_key = get_settings().EVENT_INGEST_KEY

    payload = {
        "events": [
            {
                "event_id": "evt_1001",
                "flag_key": "checkout_v2",
                "environment_name": "development",
                "variation_key": "treatment",
                "event_type": "feature_variation_assigned",
                "user_identifier": "user_1",
                "occurred_at": datetime.now(UTC).isoformat(),
            },
            {
                "event_id": "evt_1001",
                "flag_key": "checkout_v2",
                "environment_name": "development",
                "variation_key": "treatment",
                "event_type": "feature_variation_assigned",
                "user_identifier": "user_1",
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        ]
    }

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Missing/wrong X-Ingest-Key returns standard 401 envelope
        bad = await client.post("/api/v1/events/ingest", json=payload, headers={"X-Ingest-Key": "wrong"})
        assert bad.status_code == 401
        err_body = bad.json()
        assert err_body["error"]["code"] == "INVALID_INGEST_KEY"
        assert err_body["error"]["request_id"]

        # Valid ingest deduplicates in-batch duplicate
        ok1 = await client.post(
            "/api/v1/events/ingest", json=payload, headers={"X-Ingest-Key": ingest_key}
        )
        assert ok1.status_code == 200
        assert ok1.json() == {"accepted": 1, "deduplicated": 1}

        # Re-posting same event_id deduplicates against DB
        ok2 = await client.post(
            "/api/v1/events/ingest", json=payload, headers={"X-Ingest-Key": ingest_key}
        )
        assert ok2.status_code == 200
        assert ok2.json() == {"accepted": 0, "deduplicated": 2}

        r_sum = await client.get("/api/v1/events/summary", params={"flag_key": "checkout_v2"})
        assert r_sum.status_code == 200
        assert r_sum.json()["total_events"] == 1


@pytest.mark.asyncio
async def test_approvals_duplicate_409_and_live_drift_409_and_error_envelope(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, fs_state = await _make_test_app(async_engine, session_factory)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 404 standard error envelope
        r_404 = await client.get(f"/api/v1/approvals/{uuid.uuid4()}")
        assert r_404.status_code == 404
        assert r_404.json()["error"]["code"] == "APPROVAL_NOT_FOUND"
        assert r_404.json()["error"]["request_id"]

        # Connect Flagsmith + sync flag
        await client.post(
            "/api/v1/integrations/flagsmith",
            json={
                "org_token": "fs_org_1234",
                "project_id": 77,
                "environment_keys": {"development": "env_dev", "production": "env_prod"},
            },
        )
        r_sync = await client.post("/api/v1/feature-flags/sync", params={"project_id": 77})
        flag_id = r_sync.json()["items"][0]["id"]

        # 1. Queue Category C prod rollout 10%
        r_roll1 = await client.post(
            f"/api/v1/feature-flags/{flag_id}/rollout",
            json={"environment_name": "production", "target_percentage": 10, "reason": "Stage 10%"},
        )
        assert r_roll1.status_code == 202
        approval_id = r_roll1.json()["approval"]["id"]

        # Approve once -> succeeds
        r_app1 = await client.post(f"/api/v1/approvals/{approval_id}/approve", json={"comment": "go"})
        assert r_app1.status_code == 200
        assert r_app1.json()["approval"]["status"] == "executed"

        # Approve duplicate -> 409 Conflict
        r_app_dup = await client.post(f"/api/v1/approvals/{approval_id}/approve", json={"comment": "dup"})
        assert r_app_dup.status_code == 409
        assert r_app_dup.json()["error"]["code"] == "APPROVAL_CONFLICT"

        # 2. Queue second prod rollout 25%, then mutate Flagsmith out-of-band to simulate drift -> 409!
        r_roll2 = await client.post(
            f"/api/v1/feature-flags/{flag_id}/rollout",
            json={"environment_name": "production", "target_percentage": 25, "reason": "Stage 25%"},
        )
        assert r_roll2.status_code == 202
        drift_approval_id = r_roll2.json()["approval"]["id"]

        # Out-of-band external change in Flagsmith (jumped to 75%)
        fs_state["segment_pct"] = 75
        fs_state["env_states"]["env_prod"]["pct"] = 75

        r_drift = await client.post(
            f"/api/v1/approvals/{drift_approval_id}/approve",
            json={"comment": "attempt after drift"},
        )
        assert r_drift.status_code == 409
        assert r_drift.json()["error"]["code"] == "APPROVAL_REVALIDATION_FAILED"

        async with session_factory() as session:
            app_obj = await session.get(Approval, uuid.UUID(drift_approval_id))
            assert app_obj is not None
            assert app_obj.status == ApprovalStatus.REVALIDATION_FAILED

        # 3. Expired approval -> 409
        r_roll3 = await client.post(
            f"/api/v1/feature-flags/{flag_id}/rollback",
            json={"environment_name": "production", "reason": "Emergency"},
        )
        assert r_roll3.status_code == 202
        exp_id = uuid.UUID(r_roll3.json()["approval"]["id"])
        async with session_factory() as session:
            row = await session.get(Approval, exp_id)
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(minutes=5)
            await session.commit()

        r_exp = await client.post(f"/api/v1/approvals/{exp_id}/approve", json={})
        assert r_exp.status_code == 409
        assert r_exp.json()["error"]["code"] == "APPROVAL_EXPIRED"
