"""Tests for FlagsmithClient v1/v2 mutations, 400 fallback, Edge readback, and capability probe."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.flagsmith_client import FlagsmithAPIError, FlagsmithClient


class _MockFlagsmithBackend:
    """Stateful mock Flagsmith server supporting v1, v2 versions, and __future__ shorthand."""

    def __init__(self, *, v2_enabled: bool = False, future_only: bool = False) -> None:
        self.v2_enabled = v2_enabled
        self.future_only = future_only
        self.env_key = "env_key_prod"
        self.project_id = 10
        self.feature_id = 100
        self.base_fs_id = 500
        self.segment_id: int | None = None
        self.segments: list[dict] = []
        self.mv_options: list[dict] = [
            {"id": 1, "string_value": "v1", "default_percentage_allocation": 100},
            {"id": 2, "string_value": "v2", "default_percentage_allocation": 0},
        ]
        self.states: list[dict] = [
            {
                "id": self.base_fs_id,
                "feature": self.feature_id,
                "feature_segment": None,
                "enabled": False,
                "feature_state_value": None,
                "multivariate_feature_state_values": [],
            }
        ]
        self.draft_versions: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []
        self.native_experiments_status = 403
        self.native_experiments_body: dict = {"detail": "Experiments require enterprise plan"}
        self.report_v2_in_env = v2_enabled

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.calls.append((method, path))
        body = json.loads(request.content.decode("utf-8")) if request.content else {}

        # Edge API
        if request.url.host == "edge.api.flagsmith.com":
            assert request.headers.get("x-environment-key") == self.env_key
            if path == "/api/v1/flags/":
                base = next(s for s in self.states if s["feature_segment"] is None)
                return httpx.Response(
                    200,
                    json=[
                        {
                            "feature": {"id": self.feature_id, "name": "checkout_v2"},
                            "enabled": base["enabled"],
                            "feature_state_value": base["feature_state_value"],
                        }
                    ],
                )
            if path == "/api/v1/identities/":
                return httpx.Response(200, json={"flags": [], "traits": []})

        # Management API auth check
        assert request.headers.get("authorization") == "Token org_secret_token"

        if path == "/api/v1/projects/" and method == "GET":
            return httpx.Response(200, json={"results": [{"id": self.project_id, "name": "Demo"}]})

        if path == f"/api/v1/projects/{self.project_id}/environments/" and method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "name": "production",
                        "api_key": self.env_key,
                        "use_v2_feature_versioning": self.report_v2_in_env,
                    }
                ],
            )

        if path == f"/api/v1/environments/{self.env_key}/" and method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "name": "production",
                    "api_key": self.env_key,
                    "use_v2_feature_versioning": self.report_v2_in_env,
                },
            )

        if path == f"/api/v1/environments/{self.env_key}/experiments/" and method == "GET":
            return httpx.Response(self.native_experiments_status, json=self.native_experiments_body)

        if path == f"/api/v1/projects/{self.project_id}/features/" and method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": self.feature_id,
                    "name": body["name"],
                    "type": body["type"],
                    "default_enabled": body.get("default_enabled", False),
                },
            )

        if path == f"/api/v1/projects/{self.project_id}/features/{self.feature_id}/mv-options/":
            if method == "GET":
                return httpx.Response(200, json=self.mv_options)
            if method == "POST":
                opt = {
                    "id": len(self.mv_options) + 1,
                    "string_value": body["string_value"],
                    "default_percentage_allocation": body["default_percentage_allocation"],
                }
                self.mv_options.append(opt)
                return httpx.Response(201, json=opt)

        if path == f"/api/v1/projects/{self.project_id}/segments/":
            if method == "GET":
                return httpx.Response(200, json=self.segments)
            if method == "POST":
                self.segment_id = 77
                seg = {"id": self.segment_id, **body}
                self.segments.append(seg)
                return httpx.Response(201, json=seg)

        if self.segment_id and path == f"/api/v1/projects/{self.project_id}/segments/{self.segment_id}/":
            self.segments[0] = {"id": self.segment_id, **body}
            return httpx.Response(200, json=self.segments[0])

        # Environment featurestates (v1)
        if path == f"/api/v1/environments/{self.env_key}/featurestates/":
            if method == "GET":
                return httpx.Response(200, json={"results": self.states})
            if method == "POST":
                if self.v2_enabled:
                    return httpx.Response(
                        400,
                        json={"detail": "Cannot mutate featurestates directly with Feature Versioning v2."},
                    )
                new_fs = {
                    "id": 501,
                    "feature": body["feature"],
                    "feature_segment": body["feature_segment"],
                    "enabled": body["enabled"],
                    "feature_state_value": body.get("feature_state_value"),
                    "multivariate_feature_state_values": body.get(
                        "multivariate_feature_state_values", []
                    ),
                }
                self.states.append(new_fs)
                return httpx.Response(201, json=new_fs)

        if path.startswith(f"/api/v1/environments/{self.env_key}/featurestates/") and method == "PATCH":
            if self.v2_enabled:
                return httpx.Response(
                    400,
                    json={"detail": "Use environment feature versions on v2 environments."},
                )
            fs_id = int(path.rstrip("/").split("/")[-1])
            for st in self.states:
                if st["id"] == fs_id:
                    st.update(body)
                    return httpx.Response(200, json=st)
            return httpx.Response(404)

        # v2 versions lifecycle
        versions_prefix = f"/api/v1/environments/{self.env_key}/features/{self.feature_id}/versions/"
        if path == versions_prefix and method == "POST":
            if self.future_only:
                return httpx.Response(404, json={"detail": "Not found"})
            ver_uuid = f"ver-{len(self.draft_versions) + 1}"
            self.draft_versions[ver_uuid] = [dict(s) for s in self.states]
            return httpx.Response(201, json={"uuid": ver_uuid})

        if path.startswith(versions_prefix):
            rest = path[len(versions_prefix) :].strip("/").split("/")
            ver_uuid = rest[0]
            draft_states = self.draft_versions.get(ver_uuid, [])
            if len(rest) == 2 and rest[1] == "featurestates" and method == "GET":
                return httpx.Response(200, json=draft_states)
            if len(rest) == 2 and rest[1] == "featurestates" and method == "POST":
                new_fs = {
                    "id": 600 + len(draft_states),
                    "feature": body["feature"],
                    "feature_segment": body["feature_segment"],
                    "enabled": body["enabled"],
                    "feature_state_value": body.get("feature_state_value"),
                    "multivariate_feature_state_values": body.get(
                        "multivariate_feature_state_values", []
                    ),
                }
                draft_states.append(new_fs)
                return httpx.Response(201, json=new_fs)
            if len(rest) == 3 and rest[1] == "featurestates" and method == "PATCH":
                fs_id = int(rest[2])
                for st in draft_states:
                    if st["id"] == fs_id:
                        st.update(body)
                        return httpx.Response(200, json=st)
                return httpx.Response(404)
            if len(rest) == 2 and rest[1] == "publish" and method == "POST":
                self.states = [dict(s) for s in draft_states]
                return httpx.Response(200, json={"uuid": ver_uuid, "published": True})

        # __future__ shorthand
        if path == f"/api/__future__/environments/{self.env_key}/features/{self.feature_id}/":
            base = next(s for s in self.states if s["feature_segment"] is None)
            if "enabled" in body:
                base["enabled"] = body["enabled"]
            return httpx.Response(200, json={"enabled": base["enabled"]})

        return httpx.Response(404, json={"detail": f"Unhandled {method} {path}"})


@pytest.mark.asyncio
async def test_flagsmith_v1_gradual_rollout_and_multivariate() -> None:
    backend = _MockFlagsmithBackend(v2_enabled=False)
    client = FlagsmithClient(
        org_token="org_secret_token",
        transport=httpx.MockTransport(backend.handler),
        retry_delay_base=0.0,
    )

    envs = await client.list_environments(10)
    assert envs[0]["use_v2_feature_versioning"] is False
    assert await client.is_v2_environment("env_key_prod") is False

    # 25% rollout via PERCENTAGE_SPLIT segment
    res_25 = await client.set_gradual_rollout_stage(
        project_id=10,
        env_key="env_key_prod",
        feature_id=100,
        flag_key="checkout_v2",
        target_percentage=25,
        verify_edge=True,
    )
    assert res_25["mutation"]["mode"] == "v1_featurestates"
    assert res_25["verification"]["matches"] is True
    assert res_25["verification"]["actual_state"]["rollout_percentage"] == 25
    assert res_25["verification"]["edge_state"]["feature"]["name"] == "checkout_v2"

    # 100% rollout
    res_100 = await client.set_gradual_rollout_stage(
        project_id=10,
        env_key="env_key_prod",
        feature_id=100,
        flag_key="checkout_v2",
        target_percentage=100,
    )
    assert res_100["verification"]["matches"] is True
    assert res_100["verification"]["actual_state"]["enabled"] is True

    # Multivariate split 30/70
    mv_res = await client.configure_multivariate_split(
        project_id=10,
        env_key="env_key_prod",
        feature_id=100,
        flag_key="checkout_v2",
        allocations={"v1": 70, "v2": 30},
    )
    assert mv_res["verification"]["matches"] is True
    assert mv_res["verification"]["actual_state"]["mv_allocations"] == {"v1": 70, "v2": 30}

    # Rollback to 0%
    rb_res = await client.rollback_flag(
        project_id=10, env_key="env_key_prod", feature_id=100, flag_key="checkout_v2"
    )
    assert rb_res["verification"]["matches"] is True
    assert rb_res["verification"]["actual_state"]["rollout_percentage"] == 0


@pytest.mark.asyncio
async def test_flagsmith_v2_version_lifecycle_and_400_fallback() -> None:
    # Case A: Environment reports v2 enabled upfront
    backend_v2 = _MockFlagsmithBackend(v2_enabled=True)
    client_v2 = FlagsmithClient(
        org_token="org_secret_token",
        transport=httpx.MockTransport(backend_v2.handler),
        retry_delay_base=0.0,
    )
    res = await client_v2.set_gradual_rollout_stage(
        project_id=10,
        env_key="env_key_prod",
        feature_id=100,
        flag_key="checkout_v2",
        target_percentage=50,
    )
    assert res["mutation"]["mode"] == "v2_versions"
    assert res["mutation"]["published"] is True
    assert res["verification"]["matches"] is True

    # Case B: Environment initially cached as v1, server returns 400 -> automatic fallback to v2!
    backend_400 = _MockFlagsmithBackend(v2_enabled=True)
    backend_400.report_v2_in_env = False  # Simulate stale cache or Flagsmith mismatch
    client_400 = FlagsmithClient(
        org_token="org_secret_token",
        transport=httpx.MockTransport(backend_400.handler),
        retry_delay_base=0.0,
    )
    res_fb = await client_400.toggle_flag(
        env_key="env_key_prod", feature_id=100, enabled=True, flag_key="checkout_v2"
    )
    assert res_fb["mutation"]["mode"] == "v2_versions"
    assert res_fb["verification"]["matches"] is True
    assert client_400._env_v2_cache["env_key_prod"] is True

    # Case C: /versions/ returns 404 -> falls back to /api/__future__/ shorthand
    backend_future = _MockFlagsmithBackend(v2_enabled=True, future_only=True)
    client_future = FlagsmithClient(
        org_token="org_secret_token",
        transport=httpx.MockTransport(backend_future.handler),
        retry_delay_base=0.0,
    )
    res_future = await client_future.toggle_flag(
        env_key="env_key_prod", feature_id=100, enabled=True, flag_key="checkout_v2"
    )
    assert res_future["mutation"]["mode"] == "v2_future_shorthand"
    assert res_future["verification"]["matches"] is True


@pytest.mark.asyncio
async def test_flagsmith_capability_probe_and_verification_mismatch() -> None:
    backend = _MockFlagsmithBackend(v2_enabled=False)
    client = FlagsmithClient(
        org_token="org_secret_token",
        transport=httpx.MockTransport(backend.handler),
        retry_delay_base=0.0,
    )

    # 403 -> tracked_event_fallback
    probe_fb = await client.probe_experiment_capability("env_key_prod")
    assert probe_fb["analytics_mode"] == "tracked_event_fallback"
    assert probe_fb["native_supported"] is False

    # 200 with missing warehouse -> tracked_event_fallback
    backend.native_experiments_status = 200
    backend.native_experiments_body = {"warehouse_configured": False, "results": []}
    probe_no_wh = await client.probe_experiment_capability("env_key_prod")
    assert probe_no_wh["analytics_mode"] == "tracked_event_fallback"

    # 200 with active warehouse -> flagsmith_native
    backend.native_experiments_body = {"warehouse_configured": True, "results": [{"id": "exp_1"}]}
    probe_native = await client.probe_experiment_capability("env_key_prod")
    assert probe_native["analytics_mode"] == "flagsmith_native"
    assert probe_native["native_supported"] is True

    # Verification mismatch check
    mismatch = await client.verify_live_state(
        env_key="env_key_prod",
        feature_id=100,
        flag_key="checkout_v2",
        expected_enabled=True,  # backend starts with enabled=False
    )
    assert mismatch["matches"] is False
    assert len(mismatch["mismatches"]) == 1


@pytest.mark.asyncio
async def test_flagsmith_v2_missing_version_uuid_raises_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/environments/env_key_prod/" and request.method == "GET":
            return httpx.Response(
                200,
                json={"id": 1, "api_key": "env_key_prod", "use_v2_feature_versioning": True},
            )
        if (
            request.url.path == "/api/v1/environments/env_key_prod/features/100/versions/"
            and request.method == "POST"
        ):
            # Missing both uuid and version_uuid
            return httpx.Response(201, json={"status": "draft"})
        return httpx.Response(404)

    client = FlagsmithClient(
        org_token="org_secret_token",
        transport=httpx.MockTransport(handler),
        retry_delay_base=0.0,
    )
    with pytest.raises(FlagsmithAPIError) as exc_info:
        await client.toggle_flag(env_key="env_key_prod", feature_id=100, enabled=True)
    assert exc_info.value.status_code == 502
    assert "version uuid" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_flagsmith_cloud_v2_numeric_env_id_segments_and_version_readback() -> None:
    """Verify Flagsmith Cloud strict v2 contract: numeric env_id, empty publish, property=None segment, v2 readback."""
    env_key = "dev_env_key_abc"
    env_id = 120019
    project_id = 56055
    feature_id = 303933
    live_ver_uuid = "ver-init-1"
    live_states: list[dict] = [
        {
            "id": 1873488,
            "feature": feature_id,
            "feature_segment": None,
            "enabled": False,
            "feature_state_value": {
                "type": "unicode",
                "string_value": None,
                "integer_value": None,
                "boolean_value": None,
            },
            "multivariate_feature_state_values": [],
            "environment_feature_version": live_ver_uuid,
        }
    ]
    drafts: dict[str, list[dict]] = {}
    segments: list[dict] = []

    def cloud_handler(request: httpx.Request) -> httpx.Response:
        nonlocal live_states, live_ver_uuid
        path = request.url.path
        method = request.method
        body = json.loads(request.content.decode("utf-8")) if request.content else {}

        if path == f"/api/v1/environments/{env_key}/" and method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": env_id,
                    "name": "Development",
                    "api_key": env_key,
                    "use_v2_feature_versioning": True,
                },
            )
        if path == f"/api/v1/projects/{project_id}/segments/" and method == "GET":
            return httpx.Response(200, json=segments)
        if path == f"/api/v1/projects/{project_id}/segments/" and method == "POST":
            assert body.get("project") == project_id
            cond = body["rules"][0]["rules"][0]["conditions"][0]
            assert "property" in cond and cond["property"] is None
            seg = {"id": 999, **body}
            segments.append(seg)
            return httpx.Response(201, json=seg)
        if path == f"/api/v1/environments/{env_key}/featurestates/" and method == "GET":
            # Simulate Cloud v2 quirk: only base state returned here with environment_feature_version
            base_only = [dict(s) for s in live_states if s.get("feature_segment") is None]
            return httpx.Response(200, json={"results": base_only})

        ver_prefix = f"/api/v1/environments/{env_id}/features/{feature_id}/versions/"
        if path == ver_prefix and method == "POST":
            ver_uuid = f"ver-{len(drafts) + 2}"
            drafts[ver_uuid] = [dict(s) for s in live_states]
            return httpx.Response(201, json={"uuid": ver_uuid})
        if path.startswith(ver_prefix):
            rest = path[len(ver_prefix) :].strip("/").split("/")
            v_id = rest[0]
            d_states = drafts.get(v_id) if v_id != live_ver_uuid else live_states
            assert d_states is not None
            if len(rest) == 2 and rest[1] == "featurestates" and method == "GET":
                return httpx.Response(200, json=d_states)
            if len(rest) == 2 and rest[1] == "featurestates" and method == "POST":
                assert "feature_state_value" in body
                new_fs = {
                    "id": 700 + len(d_states),
                    "feature": body["feature"],
                    "feature_segment": body["feature_segment"],
                    "enabled": body["enabled"],
                    "feature_state_value": body["feature_state_value"],
                    "multivariate_feature_state_values": [],
                }
                d_states.append(new_fs)
                return httpx.Response(201, json=new_fs)
            if len(rest) == 3 and rest[1] == "featurestates" and method == "PATCH":
                fs_id = int(rest[2])
                for st in d_states:
                    if st["id"] == fs_id:
                        st.update(body)
                        return httpx.Response(200, json=st)
                return httpx.Response(404)
            if len(rest) == 2 and rest[1] == "publish" and method == "POST":
                assert "live_from" not in body or body["live_from"] is not None
                live_ver_uuid = v_id
                live_states = [dict(s, environment_feature_version=v_id) for s in d_states]
                return httpx.Response(200, json={"live_from": "2026-11-25T14:00:00Z"})

        return httpx.Response(404, json={"detail": f"Unexpected {method} {path}"})

    client = FlagsmithClient(
        org_token="org_secret_token",
        transport=httpx.MockTransport(cloud_handler),
        retry_delay_base=0.0,
    )
    res = await client.set_gradual_rollout_stage(
        project_id=project_id,
        env_key=env_key,
        feature_id=feature_id,
        flag_key="checkout_v2",
        target_percentage=25,
    )
    assert res["mutation"]["mode"] == "v2_versions"
    assert res["verification"]["matches"] is True
    assert res["verification"]["actual_state"]["rollout_percentage"] == 25
    assert res["verification"]["actual_state"]["states_count"] == 2
