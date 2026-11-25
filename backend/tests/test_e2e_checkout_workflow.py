"""End-to-end Checkout v2 workflow test: PR Analysis -> Validated Draft PR -> Flagsmith v2 -> Bayesian -> Cat C Rollout."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.main import bootstrap_workspace_defaults, create_app

CHECKOUT_BASE_CODE = '''"""Checkout pricing module."""


def compute_checkout_total(subtotal: float, promo_code: str | None = None) -> float:
    """Compute legacy v1 checkout total."""
    if promo_code == "SAVE10":
        return round(subtotal * 0.9, 2)
    return round(subtotal, 2)
'''

CHECKOUT_PR_PATCH = """@@ -1,6 +1,11 @@
 def compute_checkout_total(subtotal: float, promo_code: str | None = None) -> float:
     \"\"\"Compute legacy v1 checkout total.\"\"\"
+    # Express checkout v2 inline discount without flag guard
+    if promo_code == "VIP20":
+        return round(subtotal * 0.8, 2)
     if promo_code == "SAVE10":
         return round(subtotal * 0.9, 2)
     return round(subtotal, 2)
"""


def _build_e2e_github_transport() -> tuple[httpx.MockTransport, dict[str, Any]]:
    gh_state: dict[str, Any] = {"draft_prs": [], "merge_called": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method.upper()
        if path.endswith("/merge"):
            gh_state["merge_called"] = True
            return httpx.Response(403, json={"message": "Merge forbidden"})
        if path == "/user":
            return httpx.Response(
                200,
                headers={"X-OAuth-Scopes": "read:user, public_repo"},
                json={"id": 1001, "login": "releaseguard-demo"},
            )
        if path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 555,
                        "name": "demo-checkout",
                        "full_name": "acme/demo-checkout",
                        "owner": {"login": "acme"},
                        "default_branch": "main",
                        "private": False,
                        "language": "Python",
                    }
                ],
            )
        if path == "/repos/acme/demo-checkout/pulls" and method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 42,
                        "title": "feat: express checkout-v2 discount flow",
                        "body": "Adds VIP20 express checkout v2 flow",
                        "state": "open",
                        "draft": False,
                        "html_url": "https://github.com/acme/demo-checkout/pull/42",
                        "user": {"login": "checkout-dev"},
                        "head": {"ref": "checkout-v2", "sha": "sha42"},
                        "base": {"ref": "main"},
                    }
                ],
            )
        if path == "/repos/acme/demo-checkout/pulls/42/files":
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "app/checkout.py",
                        "status": "modified",
                        "additions": 4,
                        "deletions": 0,
                        "patch": CHECKOUT_PR_PATCH,
                    }
                ],
            )
        if path == "/repos/acme/demo-checkout/contents/app/checkout.py":
            encoded = base64.b64encode(CHECKOUT_BASE_CODE.encode()).decode()
            return httpx.Response(200, json={"sha": "blob0", "encoding": "base64", "content": encoded})
        if path == "/repos/acme/demo-checkout/git/trees/main":
            return httpx.Response(200, json={"tree": [{"path": "app/checkout.py", "type": "blob"}]})
        if path == "/repos/acme/demo-checkout/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "main_sha_1"}})
        if path == "/repos/acme/demo-checkout/git/commits/main_sha_1":
            return httpx.Response(200, json={"tree": {"sha": "tree_sha_1"}})
        if path == "/repos/acme/demo-checkout/git/refs" and method == "POST":
            return httpx.Response(201, json={"ref": request.content.decode()})
        if path == "/repos/acme/demo-checkout/git/blobs" and method == "POST":
            return httpx.Response(201, json={"sha": "blob_new_1"})
        if path == "/repos/acme/demo-checkout/git/trees" and method == "POST":
            return httpx.Response(201, json={"sha": "tree_new_1"})
        if path == "/repos/acme/demo-checkout/git/commits" and method == "POST":
            return httpx.Response(201, json={"sha": "commit_new_1"})
        if path.startswith("/repos/acme/demo-checkout/git/refs/heads/") and method == "PATCH":
            return httpx.Response(200, json={"object": {"sha": "commit_new_1"}})
        if path == "/repos/acme/demo-checkout/pulls" and method == "POST":
            body = json.loads(request.content.decode())
            gh_state["draft_prs"].append(body)
            return httpx.Response(
                201,
                json={
                    "number": 108,
                    "html_url": "https://github.com/acme/demo-checkout/pull/108",
                    "draft": body.get("draft", False),
                },
            )
        return httpx.Response(404, json={"message": f"Unhandled GH {method} {path}"})

    return httpx.MockTransport(handler), gh_state


def _build_e2e_flagsmith_transport() -> tuple[httpx.MockTransport, dict[str, Any]]:
    fs_state: dict[str, Any] = {
        "features": [],
        "segments": [],
        "env_states": {
            "env_dev": {"enabled": False, "seg_enabled": False, "pct": 0},
            "env_prod": {"enabled": False, "seg_enabled": False, "pct": 0, "version": "v-init"},
        },
        "version_seq": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method.upper()
        if path.endswith("/projects/200/environments/") and method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 11,
                        "name": "development",
                        "api_key": "env_dev",
                        "is_production": False,
                        "use_v2_feature_versioning": False,
                    },
                    {
                        "id": 12,
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
                json={"id": 11, "name": "development", "api_key": "env_dev", "use_v2_feature_versioning": False},
            )
        if path.endswith("/environments/env_prod/") and method == "GET":
            return httpx.Response(
                200,
                json={"id": 12, "name": "production", "api_key": "env_prod", "use_v2_feature_versioning": True},
            )
        if path.endswith("/experiments/") and method == "GET":
            return httpx.Response(403, json={"detail": "Enterprise warehouse required"})
        if path.endswith("/projects/200/features/") and method == "GET":
            return httpx.Response(200, json=fs_state["features"])
        if path.endswith("/projects/200/features/") and method == "POST":
            body = json.loads(request.content.decode())
            feat = {
                "id": 700 + len(fs_state["features"]) + 1,
                "name": body["name"],
                "type": body.get("type", "STANDARD"),
                "description": body.get("description", ""),
                "is_archived": False,
            }
            fs_state["features"].append(feat)
            return httpx.Response(201, json=feat)
        if path.endswith("/projects/200/segments/") and method == "GET":
            return httpx.Response(200, json=fs_state["segments"])
        if path.endswith("/projects/200/segments/") and method == "POST":
            body = json.loads(request.content.decode())
            pct = int(body["rules"][0]["rules"][0]["conditions"][0]["value"])
            seg = {"id": 301, "name": body["name"], "rules": body["rules"]}
            fs_state["segments"].append(seg)
            fs_state["segment_pct"] = pct
            return httpx.Response(201, json=seg)
        if path.endswith("/projects/200/segments/301/") and method == "PUT":
            body = json.loads(request.content.decode())
            pct = int(body["rules"][0]["rules"][0]["conditions"][0]["value"])
            fs_state["segments"][0]["rules"] = body["rules"]
            fs_state["segment_pct"] = pct
            return httpx.Response(200, json=fs_state["segments"][0])

        for env_key in ("env_dev", "env_prod"):
            if f"/environments/{env_key}/featurestates/" in path and method == "GET":
                st = fs_state["env_states"][env_key]
                states = [
                    {
                        "id": 4001,
                        "feature": 701,
                        "enabled": st["enabled"],
                        "feature_segment": None,
                        "version_uuid": st.get("version"),
                    }
                ]
                if fs_state["segments"]:
                    states.append(
                        {
                            "id": 4002,
                            "feature": 701,
                            "enabled": st["seg_enabled"],
                            "feature_segment": {"segment": 301, "priority": 0},
                        }
                    )
                return httpx.Response(200, json=states)

        # v1 non-prod path
        if "/environments/env_dev/featurestates/" in path and method in ("PATCH", "POST"):
            body = json.loads(request.content.decode())
            st = fs_state["env_states"]["env_dev"]
            if path.endswith("/featurestates/") and method == "POST":
                st["seg_enabled"] = bool(body.get("enabled", True))
                st["pct"] = fs_state.get("segment_pct", 50) if st["seg_enabled"] else 0
                return httpx.Response(201, json={"id": 4002, "enabled": st["seg_enabled"]})
            if path.endswith("/4001/"):
                st["enabled"] = bool(body.get("enabled", st["enabled"]))
                return httpx.Response(200, json={"id": 4001, "enabled": st["enabled"]})
            if path.endswith("/4002/"):
                st["seg_enabled"] = bool(body.get("enabled", st["seg_enabled"]))
                st["pct"] = fs_state.get("segment_pct", 50) if st["seg_enabled"] else 0
                return httpx.Response(200, json={"id": 4002, "enabled": st["seg_enabled"]})

        # v2 prod path
        if path.endswith("/environments/env_prod/features/701/versions/") and method == "POST":
            fs_state["version_seq"] += 1
            ver = f"v2-ver-{fs_state['version_seq']}"
            fs_state["draft_ver"] = ver
            return httpx.Response(201, json={"uuid": ver})
        if "/environments/env_prod/features/701/versions/" in path:
            st = fs_state["env_states"]["env_prod"]
            if path.endswith("/featurestates/") and method == "GET":
                states = [
                    {"id": 5001, "feature": 701, "enabled": st["enabled"], "feature_segment": None}
                ]
                if st["seg_enabled"] or fs_state.get("had_prod_seg"):
                    states.append(
                        {
                            "id": 5002,
                            "feature": 701,
                            "enabled": st["seg_enabled"],
                            "feature_segment": {"segment": 301, "priority": 0},
                        }
                    )
                return httpx.Response(200, json=states)
            if path.endswith("/featurestates/5001/") and method == "PATCH":
                body = json.loads(request.content.decode())
                if "enabled" in body:
                    st["enabled"] = bool(body["enabled"])
                return httpx.Response(200, json={"id": 5001, "enabled": st["enabled"]})
            if path.endswith("/featurestates/5002/") and method == "PATCH":
                body = json.loads(request.content.decode())
                if "enabled" in body:
                    st["seg_enabled"] = bool(body["enabled"])
                return httpx.Response(200, json={"id": 5002, "enabled": st["seg_enabled"]})
            if path.endswith("/featurestates/") and method == "POST":
                body = json.loads(request.content.decode())
                fs_state["had_prod_seg"] = True
                st["seg_enabled"] = bool(body.get("enabled", True))
                return httpx.Response(201, json={"id": 5002, "enabled": st["seg_enabled"]})
            if path.endswith("/publish/") and method == "POST":
                st["version"] = fs_state.get("draft_ver")
                st["pct"] = fs_state.get("segment_pct", 0) if st["seg_enabled"] else (100 if st["enabled"] else 0)
                return httpx.Response(200, json={"uuid": st["version"], "live_from": None})

        return httpx.Response(404, json={"detail": f"Unhandled FS {method} {path}"})

    return httpx.MockTransport(handler), fs_state


@pytest.mark.asyncio
async def test_full_checkout_v2_release_governance_e2e(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Full end-to-end Checkout v2 workflow using real SQLite, PatchValidator, Bayesian, and Policy Engine."""
    gh_transport, gh_state = _build_e2e_github_transport()
    fs_transport, fs_state = _build_e2e_flagsmith_transport()

    gemini_json = json.dumps(
        {
            "summary": "PR introduces unflagged VIP20 discount logic in app/checkout.py.",
            "risk_score": 68,
            "risk_level": "high",
            "confirmed_risks": [
                {
                    "title": "Unflagged checkout pricing change",
                    "severity": "high",
                    "file_path": "app/checkout.py",
                    "line_range": "L4-L6",
                    "evidence": "Direct VIP20 20% discount added without feature flag guard.",
                    "mitigation": "Gate behind Flagsmith flag `checkout_v2`.",
                }
            ],
            "potential_risks": [
                {
                    "title": "Promo stacking assumption",
                    "severity": "medium",
                    "assumption": "Assumes promo codes are mutually exclusive.",
                    "verification_needed": "Verify promo interaction in staging.",
                }
            ],
            "flag_opportunities": [
                {
                    "suggested_flag_key": "checkout_v2",
                    "flag_type": "STANDARD",
                    "target_files": ["app/checkout.py"],
                    "rationale": "Enables gradual rollout and instant rollback.",
                    "recommended_rollout": "10% -> 25% -> 50% -> 75% -> 100%",
                }
            ],
        }
    )

    def fake_gen(*, model: str, contents: Any, config: Any) -> Any:
        assert config.automatic_function_calling.disable is True
        mime = getattr(config, "response_mime_type", None)
        if mime == "application/json" and "github_pr_diff" in str(contents):
            return SimpleNamespace(text=gemini_json, function_calls=[], candidates=[], usage_metadata=None)
        if mime == "application/json":
            return SimpleNamespace(text="{}", function_calls=[], candidates=[], usage_metadata=None)
        return SimpleNamespace(
            text="Recommend expanding checkout_v2 rollout in production based on verified Bayesian lift.",
            function_calls=[],
            candidates=[],
            usage_metadata=None,
        )

    fake_genai = SimpleNamespace(models=SimpleNamespace(generate_content=fake_gen))

    app = create_app(
        engine=async_engine,
        session_factory=session_factory,
        github_transport=gh_transport,
        flagsmith_transport=fs_transport,
        gemini_raw_client=fake_genai,
        repo_base_files={"app/checkout.py": CHECKOUT_BASE_CODE},
        bootstrap_env_tokens=False,
    )
    async with session_factory() as session:
        await bootstrap_workspace_defaults(session, bootstrap_env_tokens=False)

    ingest_key = get_settings().EVENT_INGEST_KEY

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Connect GitHub PAT & Flagsmith v2 + Probe capability
        assert (
            await client.post("/api/v1/integrations/github/pat", json={"token": "ghp_demo_e2e_1234"})
        ).status_code == 200
        assert (
            await client.post(
                "/api/v1/integrations/flagsmith",
                json={
                    "org_token": "fs_demo_e2e_5678",
                    "project_id": 200,
                    "environment_keys": {"development": "env_dev", "production": "env_prod"},
                },
            )
        ).status_code == 200
        assert (await client.get("/health")).json()["analytics_mode"] == "tracked_event_fallback"

        # Step 2: Sync repo & checkout-v2 PR
        r_repos = await client.post("/api/v1/repositories/sync")
        assert r_repos.status_code == 200
        repo_id = r_repos.json()["items"][0]["id"]

        r_prs = await client.post(f"/api/v1/repositories/{repo_id}/pull-requests/sync")
        assert r_prs.status_code == 200
        pr_id = r_prs.json()["items"][0]["id"]

        # Step 3: Analyze Risk (confirmed vs potential risks)
        r_ana = await client.post(f"/api/v1/pull-requests/{pr_id}/analyze")
        assert r_ana.status_code == 200
        pr_data = r_ana.json()["pull_request"]
        assert pr_data["risk_level"] == "high"
        assert len(pr_data["confirmed_risks"]) >= 1
        assert len(pr_data["potential_risks"]) >= 1
        assert pr_data["flag_opportunities"][0]["suggested_flag_key"] == "checkout_v2"

        # Step 4: Generate Flagsmith wrapping patch & unit tests
        r_patch = await client.post(
            f"/api/v1/pull-requests/{pr_id}/generate-patch",
            json={"flag_key": "checkout_v2", "flag_type": "STANDARD", "target_files": ["app/checkout.py"]},
        )
        assert r_patch.status_code == 201
        change_id = r_patch.json()["id"]

        # Step 5: Real Isolated Temp-Dir Patch Validation (AST + ruff + pytest in scrubbed env)
        r_val = await client.post(f"/api/v1/code-changes/{change_id}/validate")
        assert r_val.status_code == 200
        val_json = r_val.json()
        assert val_json["validation_status"] == "passed"
        assert val_json["validation_report"]["ast_ok"] is True
        assert val_json["validation_report"]["ruff_ok"] is True
        assert val_json["validation_report"]["pytest_ok"] is True

        # Step 6: Open Draft PR (Category B confirmation queued -> approved -> draft=True, no merge)
        r_dpr = await client.post(f"/api/v1/code-changes/{change_id}/create-draft-pr", json={})
        assert r_dpr.status_code == 202
        dpr_approval_id = r_dpr.json()["approval"]["id"]
        r_dpr_app = await client.post(f"/api/v1/approvals/{dpr_approval_id}/approve", json={})
        assert r_dpr_app.status_code == 200
        assert len(gh_state["draft_prs"]) == 1
        assert gh_state["draft_prs"][0]["draft"] is True
        assert gh_state["merge_called"] is False

        # Step 7: Create `checkout_v2` Flag (Category B -> approved -> starts disabled in prod)
        r_flag = await client.post(
            "/api/v1/feature-flags",
            json={"project_id": 200, "key": "checkout_v2", "flag_type": "STANDARD"},
        )
        assert r_flag.status_code == 202
        flag_app_id = r_flag.json()["approval"]["id"]
        assert (await client.post(f"/api/v1/approvals/{flag_app_id}/approve", json={})).status_code == 200
        flags_list = (await client.get("/api/v1/feature-flags")).json()["items"]
        flag_id = flags_list[0]["id"]

        # Step 8: Initial prod rollout to 10% (Category C -> approved)
        r_roll10 = await client.post(
            f"/api/v1/feature-flags/{flag_id}/rollout",
            json={"environment_name": "production", "target_percentage": 10, "reason": "Initial canary 10%"},
        )
        assert r_roll10.status_code == 202
        app10_id = r_roll10.json()["approval"]["id"]
        r_app10 = await client.post(f"/api/v1/approvals/{app10_id}/approve", json={"comment": "Start 10% canary"})
        assert r_app10.status_code == 200
        assert fs_state["env_states"]["env_prod"]["pct"] == 10

        # Step 9: Plan & Activate Experiment in production (Category C -> approved)
        r_plan = await client.post(
            "/api/v1/experiments/plan",
            json={
                "prompt": "Test checkout_v2 50/50 conversion lift vs checkout v1",
                "feature_flag_id": flag_id,
                "environment_name": "production",
            },
        )
        assert r_plan.status_code == 201
        exp_id = r_plan.json()["id"]

        # Step 10: Ingest real demo checkout events (120 control @ 10% conv / 1% err; 120 treatment @ 28% conv / 1% err)
        now = datetime.now(UTC)
        events_batch: list[dict[str, Any]] = []
        for i in range(120):
            ts = (now - timedelta(minutes=20) + timedelta(seconds=i * 5)).isoformat()
            uid = f"ctrl_{i}"
            events_batch.append(
                {
                    "event_id": f"e_c_assign_{i}",
                    "flag_key": "checkout_v2",
                    "environment_name": "production",
                    "variation_key": "v1",
                    "event_type": "feature_variation_assigned",
                    "user_identifier": uid,
                    "occurred_at": ts,
                }
            )
            if i < 12:
                events_batch.append(
                    {
                        "event_id": f"e_c_conv_{i}",
                        "flag_key": "checkout_v2",
                        "environment_name": "production",
                        "variation_key": "v1",
                        "event_type": "checkout_completed",
                        "user_identifier": uid,
                        "occurred_at": ts,
                    }
                )
            if i == 119:
                events_batch.append(
                    {
                        "event_id": f"e_c_err_{i}",
                        "flag_key": "checkout_v2",
                        "environment_name": "production",
                        "variation_key": "v1",
                        "event_type": "payment_failed",
                        "user_identifier": uid,
                        "occurred_at": ts,
                    }
                )

        for i in range(120):
            ts = (now - timedelta(minutes=20) + timedelta(seconds=i * 5)).isoformat()
            uid = f"trt_{i}"
            events_batch.append(
                {
                    "event_id": f"e_t_assign_{i}",
                    "flag_key": "checkout_v2",
                    "environment_name": "production",
                    "variation_key": "v2",
                    "event_type": "feature_variation_assigned",
                    "user_identifier": uid,
                    "occurred_at": ts,
                }
            )
            if i < 34:
                events_batch.append(
                    {
                        "event_id": f"e_t_conv_{i}",
                        "flag_key": "checkout_v2",
                        "environment_name": "production",
                        "variation_key": "v2",
                        "event_type": "checkout_completed",
                        "user_identifier": uid,
                        "occurred_at": ts,
                    }
                )
            if i == 119:
                events_batch.append(
                    {
                        "event_id": f"e_t_err_{i}",
                        "flag_key": "checkout_v2",
                        "environment_name": "production",
                        "variation_key": "v2",
                        "event_type": "payment_failed",
                        "user_identifier": uid,
                        "occurred_at": ts,
                    }
                )

        r_ing = await client.post(
            "/api/v1/events/ingest",
            json={"events": events_batch},
            headers={"X-Ingest-Key": ingest_key},
        )
        assert r_ing.status_code == 200
        assert r_ing.json()["accepted"] == len(events_batch)

        # Step 11: Real Bayesian Analysis & Deterministic `Expand Rollout` Recommendation
        r_bayes = await client.get(f"/api/v1/experiments/{exp_id}/analysis")
        assert r_bayes.status_code == 200
        b_data = r_bayes.json()
        assert b_data["analytics_mode"] == "tracked_event_fallback"
        assert b_data["sample_adequacy"]["is_adequate"] is True
        assert b_data["comparison"]["prob_treatment_beats_control"] >= 0.99
        assert b_data["guardrails"]["payment_failed"]["breached"] is False

        r_rec = await client.get(f"/api/v1/experiments/{exp_id}/recommendation")
        assert r_rec.status_code == 200
        assert r_rec.json()["recommendation"] == "Expand Rollout"

        # Step 12: Category C Prod Rollout 10% -> 25% Approval -> Live Revalidation & Execution
        r_roll25 = await client.post(
            f"/api/v1/feature-flags/{flag_id}/rollout",
            json={
                "environment_name": "production",
                "target_percentage": 25,
                "reason": "Expand Rollout recommended by Bayesian analysis",
            },
        )
        assert r_roll25.status_code == 202
        app25 = r_roll25.json()["approval"]
        assert app25["policy_category"] == "C"
        assert app25["expected_precondition"]["rollout_percentage"] == 10

        r_app25 = await client.post(
            f"/api/v1/approvals/{app25['id']}/approve",
            json={"comment": "Verified Bayesian lift, proceed to 25%"},
        )
        assert r_app25.status_code == 200
        assert r_app25.json()["approval"]["status"] == "executed"
        assert fs_state["env_states"]["env_prod"]["pct"] == 25

        # Step 13: Verify Audit Trail, Rollout History, Flag Health Scan, and Incident Contract
        r_hist = await client.get("/api/v1/rollout-history", params={"environment_name": "production"})
        assert r_hist.status_code == 200
        hist_items = r_hist.json()["items"]
        assert len(hist_items) == 2
        assert all(item["verified"] is True for item in hist_items)
        assert hist_items[0]["new_state"]["target_percentage"] == 25

        r_aud = await client.get("/api/v1/audit-logs", params={"limit": 50})
        assert r_aud.status_code == 200
        assert r_aud.json()["total"] >= 5

        r_health_scan = await client.post("/api/v1/flag-health/scan", json={"repository_id": repo_id})
        assert r_health_scan.status_code == 200
        assert r_health_scan.json()["flags_scanned"] == 1

        r_inc = await client.post(
            "/api/v1/releases/investigate",
            json={"flag_key": "checkout_v2", "environment_name": "production", "window_minutes": 60},
        )
        assert r_inc.status_code == 200
        assert r_inc.json()["correlation_not_causation"] is True
