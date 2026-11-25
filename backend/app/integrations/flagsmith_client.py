"""Async Flagsmith Management API (v1 + v2 Feature Versioning), Edge API, and Capability Probe."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

import httpx

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503})
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class FlagsmithAPIError(Exception):
    """Structured error raised by FlagsmithClient."""

    def __init__(
        self,
        status_code: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details or {}


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [dict(item) for item in payload["results"]]
    return []


def _extract_percentage_split(segment_data: dict[str, Any]) -> int | None:
    """Recursively inspect Flagsmith segment rules for a PERCENTAGE_SPLIT condition."""
    for cond in segment_data.get("conditions", []) or []:
        if str(cond.get("operator", "")).upper() == "PERCENTAGE_SPLIT":
            try:
                return int(float(str(cond.get("value", 0))))
            except (TypeError, ValueError):
                return None
    for sub in segment_data.get("rules", []) or []:
        found = _extract_percentage_split(sub)
        if found is not None:
            return found
    return None


def _normalize_feature_state_value(raw_val: Any) -> Any:
    """Flatten Flagsmith v2 feature_state_value dict when safe while keeping custom strings."""
    if not isinstance(raw_val, dict):
        return raw_val
    val_type = str(raw_val.get("type", "")).lower()
    if val_type == "int":
        return raw_val.get("integer_value")
    if val_type == "bool":
        return raw_val.get("boolean_value")
    if "string_value" in raw_val:
        return raw_val.get("string_value")
    return raw_val


class FlagsmithClient:
    """Async httpx client for Flagsmith Management API, Edge API, v1/v2 mutations, and Probe."""

    def __init__(
        self,
        org_token: str | None = None,
        *,
        api_url: str = "https://api.flagsmith.com/api/v1",
        edge_api_url: str = "https://edge.api.flagsmith.com/api/v1",
        timeout: float = 20.0,
        max_retries: int = 2,
        retry_delay_base: float = 0.2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.org_token = org_token or ""
        self.api_url = api_url.rstrip("/")
        self.edge_api_url = edge_api_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay_base = retry_delay_base
        self._transport = transport
        self._env_v2_cache: dict[str, bool] = {}
        self._env_id_cache: dict[str, int] = {}

        parsed = urlsplit(self.api_url)
        self.host_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else ""

    def _mgmt_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.org_token:
            headers["Authorization"] = f"Token {self.org_token}"
        return headers

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_safe: bool | None = None,
        allow_statuses: set[int] | None = None,
    ) -> httpx.Response:
        allow_retry = (
            retry_safe if retry_safe is not None else method.upper() in _IDEMPOTENT_METHODS
        )
        attempts = (self.max_retries + 1) if allow_retry else 1

        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout,
            transport=self._transport,
        ) as client:
            for attempt in range(attempts):
                try:
                    resp = await client.request(
                        method=method.upper(),
                        url=url,
                        params=params,
                        json=json_body,
                    )
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    if attempt < attempts - 1:
                        if self.retry_delay_base > 0:
                            await asyncio.sleep(self.retry_delay_base * (2**attempt))
                        continue
                    raise FlagsmithAPIError(504, f"Flagsmith network error: {exc}") from exc

                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts - 1:
                    retry_after_raw = resp.headers.get("Retry-After")
                    delay = self.retry_delay_base * (2**attempt)
                    if retry_after_raw:
                        try:
                            parsed_ra = float(retry_after_raw)
                            if parsed_ra > 10.0:
                                raise FlagsmithAPIError(
                                    resp.status_code,
                                    f"Flagsmith rate limit exceeded; Retry-After={parsed_ra}s.",
                                    {"retry_after_seconds": parsed_ra},
                                )
                            delay = max(0.0, min(parsed_ra, 5.0))
                        except ValueError:
                            pass
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
                if allow_statuses and resp.status_code in allow_statuses:
                    return resp
                if resp.status_code >= 400:
                    detail: dict[str, Any]
                    try:
                        parsed = resp.json() if resp.content else {}
                        detail = parsed if isinstance(parsed, dict) else {"errors": parsed}
                    except ValueError:
                        detail = {"raw": resp.text}
                    msg = (
                        detail.get("detail")
                        or detail.get("message")
                        or f"Flagsmith API HTTP {resp.status_code}"
                    )
                    raise FlagsmithAPIError(resp.status_code, str(msg), detail)
                return resp

        raise FlagsmithAPIError(500, "Flagsmith request exhausted retries.")

    async def _mgmt(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_statuses: set[int] | None = None,
    ) -> httpx.Response:
        return await self._request(
            method,
            f"{self.api_url}/{path.lstrip('/')}",
            headers=self._mgmt_headers(),
            params=params,
            json_body=json_body,
            allow_statuses=allow_statuses,
        )

    async def _future(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        base = self.host_origin or self.api_url.replace("/api/v1", "")
        return await self._request(
            method,
            f"{base.rstrip('/')}/api/__future__/{path.lstrip('/')}",
            headers=self._mgmt_headers(),
            json_body=json_body,
        )

    async def _edge(
        self,
        method: str,
        path: str,
        *,
        env_key: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return await self._request(
            method,
            f"{self.edge_api_url}/{path.lstrip('/')}",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Environment-Key": env_key,
            },
            params=params,
            json_body=json_body,
        )

    # ------------------------------------------------------------------
    # Projects & Environments (Caching use_v2_feature_versioning)
    # ------------------------------------------------------------------

    async def list_projects(self) -> list[dict[str, Any]]:
        """List Flagsmith projects accessible to the org token."""
        resp = await self._mgmt("GET", "/projects/")
        return _extract_items(resp.json())

    async def list_environments(self, project_id: int) -> list[dict[str, Any]]:
        """List environments for a project and cache use_v2_feature_versioning per env_key."""
        resp = await self._mgmt("GET", f"/projects/{project_id}/environments/")
        envs = _extract_items(resp.json())
        for env in envs:
            api_key = env.get("api_key")
            if api_key:
                key_str = str(api_key)
                self._env_v2_cache[key_str] = bool(
                    env.get("use_v2_feature_versioning", False)
                )
                if env.get("id") is not None:
                    self._env_id_cache[key_str] = int(env["id"])
        return envs

    async def get_environment(self, env_key: str) -> dict[str, Any]:
        """Fetch single environment details and update use_v2_feature_versioning cache."""
        resp = await self._mgmt("GET", f"/environments/{env_key}/")
        env = dict(resp.json())
        self._env_v2_cache[env_key] = bool(env.get("use_v2_feature_versioning", False))
        if env.get("id") is not None:
            self._env_id_cache[env_key] = int(env["id"])
        return env

    async def get_environment_id(self, env_key: str) -> int | None:
        """Resolve Flagsmith numeric environment ID from cache or environment endpoint."""
        if env_key in self._env_id_cache:
            return self._env_id_cache[env_key]
        try:
            env = await self.get_environment(env_key)
        except FlagsmithAPIError:
            return None
        if env.get("id") is not None:
            return int(env["id"])
        return None

    async def is_v2_environment(self, env_key: str, *, refresh: bool = False) -> bool:
        """Return cached use_v2_feature_versioning boolean, probing Flagsmith if uncached."""
        if not refresh and env_key in self._env_v2_cache:
            return self._env_v2_cache[env_key]
        env = await self.get_environment(env_key)
        return bool(env.get("use_v2_feature_versioning", False))

    # ------------------------------------------------------------------
    # Feature Flags CRUD & Multivariate Options
    # ------------------------------------------------------------------

    async def list_flags(self, project_id: int) -> list[dict[str, Any]]:
        """List features in a Flagsmith project."""
        resp = await self._mgmt("GET", f"/projects/{project_id}/features/")
        return _extract_items(resp.json())

    async def get_flag(self, project_id: int, feature_id: int) -> dict[str, Any]:
        """Get single project feature definition."""
        resp = await self._mgmt("GET", f"/projects/{project_id}/features/{feature_id}/")
        return dict(resp.json())

    async def find_flag_by_key(self, project_id: int, flag_key: str) -> dict[str, Any] | None:
        """Locate a project feature by its exact name/key."""
        flags = await self.list_flags(project_id)
        for flag in flags:
            if flag.get("name") == flag_key:
                return flag
        return None

    async def list_mv_options(self, project_id: int, feature_id: int) -> list[dict[str, Any]]:
        """List multivariate options for a feature."""
        resp = await self._mgmt(
            "GET", f"/projects/{project_id}/features/{feature_id}/mv-options/"
        )
        return _extract_items(resp.json())

    async def create_mv_option(
        self,
        project_id: int,
        feature_id: int,
        *,
        value: str,
        default_percentage_allocation: int,
    ) -> dict[str, Any]:
        """Create a multivariate option on a MULTIVARIATE feature."""
        resp = await self._mgmt(
            "POST",
            f"/projects/{project_id}/features/{feature_id}/mv-options/",
            json_body={
                "feature": feature_id,
                "type": "unicode",
                "string_value": value,
                "default_percentage_allocation": default_percentage_allocation,
            },
        )
        return dict(resp.json())

    async def create_flag(
        self,
        *,
        project_id: int,
        key: str,
        description: str = "",
        flag_type: str = "STANDARD",
        initial_value: str | None = None,
        default_enabled: bool = False,
        mv_options: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a feature flag (defaults disabled across environments)."""
        body: dict[str, Any] = {
            "name": key,
            "description": description,
            "type": flag_type,
            "default_enabled": default_enabled,
        }
        if initial_value is not None:
            body["initial_value"] = initial_value

        resp = await self._mgmt("POST", f"/projects/{project_id}/features/", json_body=body)
        created = dict(resp.json())

        if flag_type == "MULTIVARIATE" and mv_options:
            feature_id = int(created["id"])
            created_options: list[dict[str, Any]] = []
            for opt in mv_options:
                created_options.append(
                    await self.create_mv_option(
                        project_id,
                        feature_id,
                        value=str(opt["value"]),
                        default_percentage_allocation=int(opt["default_percentage_allocation"]),
                    )
                )
            created["multivariate_options"] = created_options
        return created

    async def archive_flag(self, project_id: int, feature_id: int) -> dict[str, Any]:
        """Archive a feature flag (Category C operation)."""
        resp = await self._mgmt(
            "PATCH",
            f"/projects/{project_id}/features/{feature_id}/",
            json_body={"is_archived": True},
        )
        return dict(resp.json())

    async def delete_flag(self, project_id: int, feature_id: int) -> None:
        """Delete a feature flag permanently (Category C operation)."""
        await self._mgmt("DELETE", f"/projects/{project_id}/features/{feature_id}/")

    # ------------------------------------------------------------------
    # Segments (PERCENTAGE_SPLIT)
    # ------------------------------------------------------------------

    async def list_segments(self, project_id: int) -> list[dict[str, Any]]:
        """List segments for a project."""
        resp = await self._mgmt("GET", f"/projects/{project_id}/segments/")
        return _extract_items(resp.json())

    async def upsert_percentage_split_segment(
        self,
        *,
        project_id: int,
        segment_name: str,
        percentage: int,
    ) -> dict[str, Any]:
        """Create or update a Flagsmith PERCENTAGE_SPLIT segment (0..100)."""
        if percentage < 0 or percentage > 100:
            raise ValueError("Percentage split must be between 0 and 100.")

        payload = {
            "name": segment_name,
            "project": project_id,
            "rules": [
                {
                    "type": "ALL",
                    "conditions": [],
                    "rules": [
                        {
                            "type": "ANY",
                            "conditions": [
                                {
                                    "property": None,
                                    "operator": "PERCENTAGE_SPLIT",
                                    "value": str(percentage),
                                }
                            ],
                            "rules": [],
                        }
                    ],
                }
            ],
        }
        segments = await self.list_segments(project_id)
        existing = next((s for s in segments if s.get("name") == segment_name), None)
        if existing is not None:
            seg_id = int(existing["id"])
            resp = await self._mgmt(
                "PUT",
                f"/projects/{project_id}/segments/{seg_id}/",
                json_body=payload,
            )
            return dict(resp.json())

        resp = await self._mgmt(
            "POST",
            f"/projects/{project_id}/segments/",
            json_body=payload,
        )
        return dict(resp.json())

    # ------------------------------------------------------------------
    # Environment Feature States & Edge Evaluation Reads
    # ------------------------------------------------------------------

    async def get_environment_feature_states(
        self,
        env_key: str,
        *,
        feature_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read live Management API feature states (including v2 published version overrides)."""
        params = {"feature": feature_id} if feature_id is not None else None
        resp = await self._mgmt("GET", f"/environments/{env_key}/featurestates/", params=params)
        items = _extract_items(resp.json())

        # On Flagsmith v2 environments, /environments/{env_key}/featurestates/ only returns
        # the base state (omitting segment overrides). When environment_feature_version is
        # present, also read the live version's full featurestates (base + segment overrides).
        if feature_id is not None and items:
            ver_uuid = next(
                (
                    str(it.get("environment_feature_version") or it.get("version_uuid"))
                    for it in items
                    if it.get("environment_feature_version") or it.get("version_uuid")
                ),
                None,
            )
            if ver_uuid:
                env_id = await self.get_environment_id(env_key)
                candidate_refs: list[str] = []
                if env_id is not None:
                    candidate_refs.append(str(env_id))
                if env_key not in candidate_refs:
                    candidate_refs.append(env_key)
                for env_ref in candidate_refs:
                    ver_resp = await self._mgmt(
                        "GET",
                        f"/environments/{env_ref}/features/{feature_id}/versions/{ver_uuid}/featurestates/",
                        allow_statuses={404, 405},
                    )
                    if ver_resp.status_code == 200:
                        ver_items = _extract_items(ver_resp.json())
                        if ver_items:
                            for vit in ver_items:
                                vit["environment_feature_version"] = ver_uuid
                                vit["version_uuid"] = ver_uuid
                                vit["feature_state_value"] = _normalize_feature_state_value(
                                    vit.get("feature_state_value")
                                )
                            return ver_items
                        break

        for it in items:
            if it.get("environment_feature_version") and not it.get("version_uuid"):
                it["version_uuid"] = it["environment_feature_version"]
            it["feature_state_value"] = _normalize_feature_state_value(it.get("feature_state_value"))
        return items

    async def get_edge_flags(self, env_key: str) -> list[dict[str, Any]]:
        """Read SDK-equivalent environment flags from Flagsmith Edge Flags API."""
        resp = await self._edge("GET", "/flags/", env_key=env_key)
        payload = resp.json()
        return payload if isinstance(payload, list) else _extract_items(payload)

    async def get_edge_identity_flags(
        self,
        env_key: str,
        identifier: str,
        *,
        traits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate flags for a specific identity via Edge Flags API."""
        resp = await self._edge(
            "POST",
            "/identities/",
            env_key=env_key,
            json_body={"identifier": identifier, "traits": traits or []},
        )
        return dict(resp.json())

    # ------------------------------------------------------------------
    # Dual v1 / v2 Mutation Adapter
    # ------------------------------------------------------------------

    @staticmethod
    def _partition_feature_states(
        states: list[dict[str, Any]],
        segment_id: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        base_fs: dict[str, Any] | None = None
        segment_fs: dict[str, Any] | None = None
        for fs in states:
            feat_seg = fs.get("feature_segment")
            if feat_seg is None:
                base_fs = fs
            elif segment_id is not None:
                seg_ref = feat_seg.get("segment") if isinstance(feat_seg, dict) else feat_seg
                if seg_ref == segment_id:
                    segment_fs = fs
        return base_fs, segment_fs

    @staticmethod
    def _prepare_base_updates(
        base_fs: dict[str, Any], base_updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge existing multivariate_feature_state_values IDs required by Flagsmith PATCH."""
        payload = dict(base_updates)
        if "multivariate_feature_state_values" not in payload:
            return payload
        existing_by_opt = {
            int(item["multivariate_feature_option"]): int(item["id"])
            for item in (base_fs.get("multivariate_feature_state_values") or [])
            if item.get("id") is not None and item.get("multivariate_feature_option") is not None
        }
        merged: list[dict[str, Any]] = []
        seen_opts: set[int] = set()
        for item in payload["multivariate_feature_state_values"]:
            opt_id = int(item["multivariate_feature_option"])
            seen_opts.add(opt_id)
            entry = dict(item)
            if "id" not in entry and opt_id in existing_by_opt:
                entry["id"] = existing_by_opt[opt_id]
            merged.append(entry)
        for opt_id, row_id in existing_by_opt.items():
            if opt_id not in seen_opts:
                merged.append(
                    {
                        "id": row_id,
                        "multivariate_feature_option": opt_id,
                        "percentage_allocation": 0,
                    }
                )
        payload["multivariate_feature_state_values"] = merged
        return payload

    async def _mutate_v1(
        self,
        env_key: str,
        feature_id: int,
        *,
        base_updates: dict[str, Any] | None = None,
        segment_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        states = await self.get_environment_feature_states(env_key, feature_id=feature_id)
        seg_id = segment_override["segment_id"] if segment_override else None
        base_fs, seg_fs = self._partition_feature_states(states, segment_id=seg_id)

        result: dict[str, Any] = {"mode": "v1_featurestates"}
        if base_updates is not None:
            if base_fs is None:
                raise FlagsmithAPIError(
                    404, f"Base feature state not found for feature {feature_id} in {env_key}."
                )
            prepared_updates = self._prepare_base_updates(base_fs, base_updates)
            resp = await self._mgmt(
                "PATCH",
                f"/environments/{env_key}/featurestates/{base_fs['id']}/",
                json_body=prepared_updates,
            )
            result["base_feature_state"] = dict(resp.json())

        if segment_override is not None:
            seg_payload = {
                "enabled": segment_override["enabled"],
            }
            if "feature_state_value" in segment_override:
                seg_payload["feature_state_value"] = segment_override["feature_state_value"]
            if "multivariate_feature_state_values" in segment_override:
                seg_payload["multivariate_feature_state_values"] = segment_override[
                    "multivariate_feature_state_values"
                ]

            if seg_fs is not None:
                resp = await self._mgmt(
                    "PATCH",
                    f"/environments/{env_key}/featurestates/{seg_fs['id']}/",
                    json_body=seg_payload,
                )
                result["segment_feature_state"] = dict(resp.json())
            elif segment_override.get("create_if_missing", True):
                create_body = {
                    "feature": feature_id,
                    "feature_segment": {
                        "segment": seg_id,
                        "priority": segment_override.get("priority", 0),
                    },
                    **seg_payload,
                }
                resp = await self._mgmt(
                    "POST",
                    f"/environments/{env_key}/featurestates/",
                    json_body=create_body,
                )
                result["segment_feature_state"] = dict(resp.json())
        return result

    async def _mutate_v2(
        self,
        env_key: str,
        feature_id: int,
        *,
        base_updates: dict[str, Any] | None = None,
        segment_override: dict[str, Any] | None = None,
        use_future_shorthand: bool = False,
    ) -> dict[str, Any]:
        if use_future_shorthand:
            body: dict[str, Any] = dict(base_updates or {})
            if segment_override is not None:
                body["segment_override"] = segment_override
            resp = await self._future(
                "PATCH",
                f"/environments/{env_key}/features/{feature_id}/",
                json_body=body,
            )
            return {
                "mode": "v2_future_shorthand",
                "published": True,
                "response": dict(resp.json()) if resp.content else {},
            }

        # Explicit v2 Version Lifecycle: numeric environment id first (Flagsmith Cloud),
        # with env_key fallback for mocks and /api/__future__/ shorthand when 404/405.
        env_id = await self.get_environment_id(env_key)
        candidate_refs: list[str] = []
        if env_id is not None:
            candidate_refs.append(str(env_id))
        if env_key not in candidate_refs:
            candidate_refs.append(env_key)

        active_ref: str | None = None
        create_ver_resp: httpx.Response | None = None
        for env_ref in candidate_refs:
            resp = await self._mgmt(
                "POST",
                f"/environments/{env_ref}/features/{feature_id}/versions/",
                json_body={},
                allow_statuses={404, 405},
            )
            if resp.status_code not in {404, 405}:
                active_ref = env_ref
                create_ver_resp = resp
                break

        if active_ref is None or create_ver_resp is None:
            return await self._mutate_v2(
                env_key,
                feature_id,
                base_updates=base_updates,
                segment_override=segment_override,
                use_future_shorthand=True,
            )

        version_data = dict(create_ver_resp.json())
        raw_uuid = version_data.get("uuid") or version_data.get("version_uuid")
        if not raw_uuid:
            raise FlagsmithAPIError(
                502, "Flagsmith v2 version creation response did not include version uuid."
            )
        version_uuid = str(raw_uuid)

        draft_states_resp = await self._mgmt(
            "GET",
            f"/environments/{active_ref}/features/{feature_id}/versions/{version_uuid}/featurestates/",
        )
        draft_states = _extract_items(draft_states_resp.json())
        seg_id = segment_override["segment_id"] if segment_override else None
        base_fs, seg_fs = self._partition_feature_states(draft_states, segment_id=seg_id)

        if base_updates is not None:
            if base_fs is None:
                raise FlagsmithAPIError(
                    404, f"Draft base feature state missing in v2 version {version_uuid}."
                )
            prepared_updates = self._prepare_base_updates(base_fs, base_updates)
            await self._mgmt(
                "PATCH",
                f"/environments/{active_ref}/features/{feature_id}/versions/{version_uuid}/featurestates/{base_fs['id']}/",
                json_body=prepared_updates,
            )

        if segment_override is not None:
            seg_payload: dict[str, Any] = {"enabled": segment_override["enabled"]}
            if "feature_state_value" in segment_override:
                seg_payload["feature_state_value"] = segment_override["feature_state_value"]
            if "multivariate_feature_state_values" in segment_override:
                seg_payload["multivariate_feature_state_values"] = segment_override[
                    "multivariate_feature_state_values"
                ]

            if seg_fs is not None:
                await self._mgmt(
                    "PATCH",
                    f"/environments/{active_ref}/features/{feature_id}/versions/{version_uuid}/featurestates/{seg_fs['id']}/",
                    json_body=seg_payload,
                )
            elif segment_override.get("create_if_missing", True):
                if "feature_state_value" not in seg_payload:
                    default_fsv = (
                        base_fs.get("feature_state_value")
                        if base_fs and isinstance(base_fs.get("feature_state_value"), dict)
                        else {
                            "type": "unicode",
                            "string_value": None,
                            "integer_value": None,
                            "boolean_value": None,
                        }
                    )
                    seg_payload["feature_state_value"] = default_fsv
                await self._mgmt(
                    "POST",
                    f"/environments/{active_ref}/features/{feature_id}/versions/{version_uuid}/featurestates/",
                    json_body={
                        "feature": feature_id,
                        "feature_segment": {
                            "segment": seg_id,
                            "priority": segment_override.get("priority", 0),
                        },
                        **seg_payload,
                    },
                )

        pub_resp = await self._mgmt(
            "POST",
            f"/environments/{active_ref}/features/{feature_id}/versions/{version_uuid}/publish/",
            json_body={},
        )
        pub_data = dict(pub_resp.json()) if pub_resp.content else {}
        return {
            "mode": "v2_versions",
            "version_uuid": version_uuid,
            "published": True,
            "publish_response": pub_data,
        }

    async def apply_feature_state_mutation(
        self,
        env_key: str,
        feature_id: int,
        *,
        base_updates: dict[str, Any] | None = None,
        segment_override: dict[str, Any] | None = None,
        use_future_shorthand: bool = False,
    ) -> dict[str, Any]:
        """Route mutation via v1 or v2 path with automatic HTTP 400 fallback to v2."""
        is_v2 = await self.is_v2_environment(env_key)
        if is_v2:
            return await self._mutate_v2(
                env_key,
                feature_id,
                base_updates=base_updates,
                segment_override=segment_override,
                use_future_shorthand=use_future_shorthand,
            )

        try:
            return await self._mutate_v1(
                env_key,
                feature_id,
                base_updates=base_updates,
                segment_override=segment_override,
            )
        except FlagsmithAPIError as exc:
            if exc.status_code == 400:
                self._env_v2_cache[env_key] = True
                return await self._mutate_v2(
                    env_key,
                    feature_id,
                    base_updates=base_updates,
                    segment_override=segment_override,
                    use_future_shorthand=use_future_shorthand,
                )
            raise

    # ------------------------------------------------------------------
    # High-Level Rollout, Toggle, Multivariate, Rollback & Verification
    # ------------------------------------------------------------------

    async def verify_live_state(
        self,
        *,
        env_key: str,
        feature_id: int,
        flag_key: str | None = None,
        project_id: int | None = None,
        expected_enabled: bool | None = None,
        expected_percentage: int | None = None,
        expected_mv_allocations: dict[str, int] | None = None,
        verify_edge: bool = False,
    ) -> dict[str, Any]:
        """Read back live Flagsmith Management (and optional Edge) state and assert invariants."""
        states = await self.get_environment_feature_states(env_key, feature_id=feature_id)
        base_fs, _ = self._partition_feature_states(states)

        mismatches: list[str] = []
        actual_enabled = bool(base_fs.get("enabled", False)) if base_fs else None
        actual_value = base_fs.get("feature_state_value") if base_fs else None
        actual_percentage: int | None = None

        if expected_enabled is not None and actual_enabled != expected_enabled:
            mismatches.append(
                f"Expected base enabled={expected_enabled}, got {actual_enabled}."
            )

        if expected_percentage is not None:
            if expected_percentage == 100:
                actual_percentage = 100 if actual_enabled else 0
                if not actual_enabled:
                    mismatches.append("Expected 100% rollout (base enabled=True), got False.")
            elif expected_percentage == 0:
                segment_enabled = any(
                    bool(s.get("enabled")) for s in states if s.get("feature_segment") is not None
                )
                actual_percentage = 0 if (not actual_enabled and not segment_enabled) else 100
                if actual_enabled or segment_enabled:
                    mismatches.append("Expected 0% rollback (base and segment overrides disabled).")
            else:
                rollout_seg_name = f"rg_rollout_{flag_key}" if flag_key else None
                seg_by_id: dict[int, dict[str, Any]] = {}
                if project_id is not None:
                    for seg in await self.list_segments(project_id):
                        seg_by_id[int(seg["id"])] = seg

                matched_pct: int | None = None
                for fs in states:
                    feat_seg = fs.get("feature_segment")
                    if feat_seg is None or not fs.get("enabled"):
                        continue
                    seg_id = (
                        feat_seg.get("segment") if isinstance(feat_seg, dict) else feat_seg
                    )
                    seg_obj = seg_by_id.get(int(seg_id)) if seg_id is not None else None
                    if rollout_seg_name and seg_obj and seg_obj.get("name") != rollout_seg_name:
                        continue
                    if seg_obj is not None:
                        matched_pct = _extract_percentage_split(seg_obj)
                        if matched_pct is not None:
                            break
                actual_percentage = matched_pct
                if matched_pct != expected_percentage:
                    mismatches.append(
                        f"Expected rollout percentage={expected_percentage}%, got {matched_pct}."
                    )

        actual_mv: dict[str, int] = {}
        if expected_mv_allocations is not None and base_fs is not None:
            mv_values = base_fs.get("multivariate_feature_state_values") or []
            mv_opt_map: dict[int, str] = {}
            if project_id is not None:
                for opt in await self.list_mv_options(project_id, feature_id):
                    mv_opt_map[int(opt["id"])] = str(
                        opt.get("string_value") or opt.get("value") or ""
                    )
            for item in mv_values:
                opt_id = item.get("multivariate_feature_option")
                opt_val = item.get("value") or (
                    mv_opt_map.get(int(opt_id), str(opt_id)) if opt_id is not None else ""
                )
                actual_mv[str(opt_val)] = int(item.get("percentage_allocation", 0))
            for k, expected_pct in expected_mv_allocations.items():
                if actual_mv.get(k) != expected_pct:
                    mismatches.append(
                        f"Expected multivariate '{k}'={expected_pct}%, got {actual_mv.get(k)}."
                    )

        edge_state: dict[str, Any] | None = None
        if verify_edge and flag_key:
            edge_flags = await self.get_edge_flags(env_key)
            for ef in edge_flags:
                feat_info = ef.get("feature") or {}
                if feat_info.get("name") == flag_key:
                    edge_state = dict(ef)
                    break

        return {
            "matches": len(mismatches) == 0,
            "mismatches": mismatches,
            "actual_state": {
                "enabled": actual_enabled,
                "feature_state_value": actual_value,
                "rollout_percentage": actual_percentage,
                "mv_allocations": actual_mv,
                "states_count": len(states),
            },
            "edge_state": edge_state,
        }

    async def toggle_flag(
        self,
        *,
        env_key: str,
        feature_id: int,
        enabled: bool,
        flag_key: str | None = None,
        verify_edge: bool = False,
    ) -> dict[str, Any]:
        """Toggle a flag on/off in an environment and verify live state."""
        mutation = await self.apply_feature_state_mutation(
            env_key, feature_id, base_updates={"enabled": enabled}
        )
        verification = await self.verify_live_state(
            env_key=env_key,
            feature_id=feature_id,
            flag_key=flag_key,
            expected_enabled=enabled,
            verify_edge=verify_edge,
        )
        return {"mutation": mutation, "verification": verification}

    async def set_gradual_rollout_stage(
        self,
        *,
        project_id: int,
        env_key: str,
        feature_id: int,
        flag_key: str,
        target_percentage: int,
        strategy: str = "percentage_split_segment",
        allocations: dict[str, int] | None = None,
        verify_edge: bool = False,
    ) -> dict[str, Any]:
        """Execute a gradual rollout stage (0/10/25/50/75/100%) and verify live state."""
        if strategy == "multivariate":
            if allocations is None:
                allocations = {
                    "treatment": target_percentage,
                    "control": 100 - target_percentage,
                }
            return await self.configure_multivariate_split(
                project_id=project_id,
                env_key=env_key,
                feature_id=feature_id,
                flag_key=flag_key,
                allocations=allocations,
                verify_edge=verify_edge,
            )

        segment_name = f"rg_rollout_{flag_key}"
        if target_percentage == 0:
            segments = await self.list_segments(project_id)
            existing = next((s for s in segments if s.get("name") == segment_name), None)
            seg_override = (
                {"segment_id": int(existing["id"]), "enabled": False, "create_if_missing": False}
                if existing is not None
                else None
            )
            mutation = await self.apply_feature_state_mutation(
                env_key,
                feature_id,
                base_updates={"enabled": False},
                segment_override=seg_override,
            )
            verification = await self.verify_live_state(
                env_key=env_key,
                feature_id=feature_id,
                flag_key=flag_key,
                project_id=project_id,
                expected_enabled=False,
                expected_percentage=0,
                verify_edge=verify_edge,
            )
            return {"mutation": mutation, "verification": verification}

        if target_percentage == 100:
            segments = await self.list_segments(project_id)
            existing = next((s for s in segments if s.get("name") == segment_name), None)
            seg_override = (
                {"segment_id": int(existing["id"]), "enabled": False, "create_if_missing": False}
                if existing is not None
                else None
            )
            mutation = await self.apply_feature_state_mutation(
                env_key,
                feature_id,
                base_updates={"enabled": True},
                segment_override=seg_override,
            )
            verification = await self.verify_live_state(
                env_key=env_key,
                feature_id=feature_id,
                flag_key=flag_key,
                project_id=project_id,
                expected_enabled=True,
                expected_percentage=100,
                verify_edge=verify_edge,
            )
            return {"mutation": mutation, "verification": verification}

        # Partial percentage (10, 25, 50, 75, etc.): base disabled + PERCENTAGE_SPLIT segment enabled
        segment = await self.upsert_percentage_split_segment(
            project_id=project_id,
            segment_name=segment_name,
            percentage=target_percentage,
        )
        mutation = await self.apply_feature_state_mutation(
            env_key,
            feature_id,
            base_updates={"enabled": False},
            segment_override={
                "segment_id": int(segment["id"]),
                "enabled": True,
                "priority": 0,
                "create_if_missing": True,
            },
        )
        verification = await self.verify_live_state(
            env_key=env_key,
            feature_id=feature_id,
            flag_key=flag_key,
            project_id=project_id,
            expected_enabled=False,
            expected_percentage=target_percentage,
            verify_edge=verify_edge,
        )
        return {"mutation": mutation, "verification": verification, "segment": segment}

    async def configure_multivariate_split(
        self,
        *,
        project_id: int,
        env_key: str,
        feature_id: int,
        flag_key: str,
        allocations: dict[str, int],
        verify_edge: bool = False,
    ) -> dict[str, Any]:
        """Configure multivariate percentage allocations summing to 100% and verify."""
        if sum(allocations.values()) != 100:
            raise ValueError("Multivariate allocations must sum to 100.")

        options = await self.list_mv_options(project_id, feature_id)
        option_by_value = {
            str(opt.get("string_value") or opt.get("value") or ""): int(opt["id"])
            for opt in options
        }
        mv_state_values: list[dict[str, Any]] = []
        for var_name, pct in allocations.items():
            if var_name not in option_by_value:
                created = await self.create_mv_option(
                    project_id,
                    feature_id,
                    value=var_name,
                    default_percentage_allocation=pct,
                )
                option_by_value[var_name] = int(created["id"])
            mv_state_values.append(
                {
                    "multivariate_feature_option": option_by_value[var_name],
                    "percentage_allocation": pct,
                }
            )

        mutation = await self.apply_feature_state_mutation(
            env_key,
            feature_id,
            base_updates={
                "enabled": True,
                "multivariate_feature_state_values": mv_state_values,
            },
        )
        verification = await self.verify_live_state(
            env_key=env_key,
            feature_id=feature_id,
            flag_key=flag_key,
            project_id=project_id,
            expected_enabled=True,
            expected_mv_allocations=allocations,
            verify_edge=verify_edge,
        )
        return {"mutation": mutation, "verification": verification}

    async def rollback_flag(
        self,
        *,
        project_id: int,
        env_key: str,
        feature_id: int,
        flag_key: str,
        verify_edge: bool = False,
    ) -> dict[str, Any]:
        """Emergency rollback setting rollout to 0% and base state enabled=False."""
        return await self.set_gradual_rollout_stage(
            project_id=project_id,
            env_key=env_key,
            feature_id=feature_id,
            flag_key=flag_key,
            target_percentage=0,
            strategy="percentage_split_segment",
            verify_edge=verify_edge,
        )

    # ------------------------------------------------------------------
    # Native Flagsmith Experimentation & Metrics APIs
    # ------------------------------------------------------------------

    async def list_experiment_metrics(self, env_key: str) -> list[dict[str, Any]]:
        """List experiment metrics configured for a Flagsmith environment."""
        resp = await self._mgmt("GET", f"/environments/{env_key}/experiment-metrics/")
        return _extract_items(resp.json())

    async def create_experiment_metric(
        self,
        env_key: str,
        *,
        name: str,
        event_name: str,
        aggregation: str = "occurrence",
        direction: str = "up",
        description: str = "",
    ) -> dict[str, Any]:
        """Create an experiment metric in Flagsmith for a tracked event."""
        existing = await self.list_experiment_metrics(env_key)
        for item in existing:
            defn = item.get("definition") or {}
            ev_key = defn.get("event") or defn.get("event_name")
            if item.get("name") == name or ev_key == event_name:
                return item
        resp = await self._mgmt(
            "POST",
            f"/environments/{env_key}/experiment-metrics/",
            json_body={
                "name": name,
                "description": description or f"Metric for {event_name}",
                "aggregation": aggregation,
                "direction": direction,
                "definition": {"version": 1, "event": event_name},
            },
        )
        return dict(resp.json())

    async def list_experiments(self, env_key: str) -> list[dict[str, Any]]:
        """List native Flagsmith experiments in an environment."""
        resp = await self._mgmt("GET", f"/environments/{env_key}/experiments/")
        return _extract_items(resp.json())

    async def get_experiment(self, env_key: str, experiment_id: int | str) -> dict[str, Any]:
        """Retrieve a single native Flagsmith experiment."""
        resp = await self._mgmt("GET", f"/environments/{env_key}/experiments/{experiment_id}/")
        return dict(resp.json())

    async def create_experiment(
        self,
        env_key: str,
        *,
        feature_id: int,
        name: str,
        hypothesis: str,
        metrics: list[dict[str, Any]] | None = None,
        experiment_rollout: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a native Flagsmith experiment linked to a multivariate feature flag."""
        payload: dict[str, Any] = {
            "feature": feature_id,
            "name": name[:255],
            "hypothesis": hypothesis,
        }
        if metrics:
            payload["metrics"] = metrics
        if experiment_rollout is not None:
            payload["experiment_rollout"] = experiment_rollout
        resp = await self._mgmt(
            "POST",
            f"/environments/{env_key}/experiments/",
            json_body=payload,
        )
        return dict(resp.json())

    async def start_experiment(self, env_key: str, experiment_id: int | str) -> dict[str, Any]:
        """Start a native Flagsmith experiment."""
        resp = await self._mgmt(
            "POST", f"/environments/{env_key}/experiments/{experiment_id}/start/"
        )
        return dict(resp.json()) if resp.content else {"status": "running"}

    async def stop_experiment(self, env_key: str, experiment_id: int | str) -> dict[str, Any]:
        """Stop/conclude a native Flagsmith experiment."""
        resp = await self._mgmt(
            "POST", f"/environments/{env_key}/experiments/{experiment_id}/stop/"
        )
        return dict(resp.json()) if resp.content else {"status": "completed"}

    async def refresh_experiment_results(
        self, env_key: str, experiment_id: int | str
    ) -> dict[str, Any]:
        """Trigger Flagsmith experiment warehouse results refresh (may be rate-limited)."""
        resp = await self._mgmt(
            "POST",
            f"/environments/{env_key}/experiments/{experiment_id}/results/refresh/",
            allow_statuses={200, 202, 429},
        )
        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            payload = {"raw": resp.text}
        return {"status_code": resp.status_code, "data": payload}

    async def get_experiment_results(
        self, env_key: str, experiment_id: int | str
    ) -> dict[str, Any]:
        """Retrieve real Bayesian results from Flagsmith native experimentation."""
        resp = await self._mgmt(
            "GET",
            f"/environments/{env_key}/experiments/{experiment_id}/results/",
            allow_statuses={200, 404},
        )
        if resp.status_code == 404:
            return {"available": False, "status_code": 404, "results": None}
        payload = resp.json() if resp.content else {}
        return {"available": True, "status_code": 200, "results": payload}

    async def promote_experiment_winner(
        self,
        *,
        project_id: int,
        env_key: str,
        feature_id: int,
        flag_key: str,
        winning_variation: str,
        verify_edge: bool = False,
    ) -> dict[str, Any]:
        """Promote winning experiment variant to 100% on the same Flagsmith flag and verify."""
        options = await self.list_mv_options(project_id, feature_id)
        if options:
            allocations: dict[str, int] = {}
            matched = False
            for opt in options:
                val = str(opt.get("string_value") or opt.get("value") or "")
                if val == winning_variation:
                    allocations[val] = 100
                    matched = True
                else:
                    allocations[val] = 0
            if not matched:
                allocations[winning_variation] = 100
            return await self.configure_multivariate_split(
                project_id=project_id,
                env_key=env_key,
                feature_id=feature_id,
                flag_key=flag_key,
                allocations=allocations,
                verify_edge=verify_edge,
            )

        target_enabled = winning_variation.strip().lower() not in {"control", "v1", "false", "off", "0"}
        segments = await self.list_segments(project_id)
        rollout_seg = next((s for s in segments if s.get("name") == f"rg_rollout_{flag_key}"), None)
        seg_override = (
            {"segment_id": int(rollout_seg["id"]), "enabled": False, "create_if_missing": False}
            if rollout_seg is not None
            else None
        )
        mutation = await self.apply_feature_state_mutation(
            env_key,
            feature_id,
            base_updates={"enabled": target_enabled, "feature_state_value": winning_variation},
            segment_override=seg_override,
        )
        verification = await self.verify_live_state(
            env_key=env_key,
            feature_id=feature_id,
            flag_key=flag_key,
            project_id=project_id,
            expected_enabled=target_enabled,
            verify_edge=verify_edge,
        )
        return {"mutation": mutation, "verification": verification}

    # ------------------------------------------------------------------
    # Identities, Traits, Targeting Segments & Feature Metadata
    # ------------------------------------------------------------------

    async def list_edge_identities(
        self,
        env_key: str,
        *,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        """List identities recorded in a Flagsmith environment."""
        params = {"q": q} if q else None
        resp = await self._mgmt(
            "GET",
            f"/environments/{env_key}/edge-identities/",
            params=params,
            allow_statuses={200, 404},
        )
        if resp.status_code == 404:
            return []
        return _extract_items(resp.json())

    async def evaluate_identity_detailed(
        self,
        env_key: str,
        *,
        identifier: str,
        traits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate flags for an identity with traits via Flagsmith Edge POST /identities/."""
        trait_list = [
            {
                "trait_key": str(k),
                "trait_value": v,
                "transient": False,
            }
            for k, v in (traits or {}).items()
            if v is not None
        ]
        resp = await self._edge(
            "POST",
            "/identities/",
            env_key=env_key,
            json_body={"identifier": identifier, "traits": trait_list},
        )
        payload = dict(resp.json())
        normalized_flags: dict[str, dict[str, Any]] = {}
        for item in payload.get("flags") or []:
            feat = item.get("feature") or {}
            fname = str(feat.get("name") or "")
            if not fname:
                continue
            normalized_flags[fname] = {
                "flag_key": fname,
                "feature_id": feat.get("id"),
                "type": feat.get("type"),
                "enabled": bool(item.get("enabled", False)),
                "value": _normalize_feature_state_value(item.get("feature_state_value")),
                "reason": item.get("reason") or "DEFAULT",
                "variant": item.get("variant"),
            }
        return {
            "identifier": payload.get("identifier", identifier),
            "traits": payload.get("traits") or trait_list,
            "flags": normalized_flags,
        }

    async def upsert_trait_segment(
        self,
        *,
        project_id: int,
        segment_name: str,
        trait_conditions: list[dict[str, Any]],
        match_type: str = "ALL",
        description: str = "",
    ) -> dict[str, Any]:
        """Create or update a trait-based targeting segment in Flagsmith."""
        conditions = [
            {
                "property": str(c["property"]),
                "operator": str(c.get("operator", "EQUAL")),
                "value": str(c["value"]),
            }
            for c in trait_conditions
        ]
        payload = {
            "name": segment_name,
            "description": description or f"Managed targeting segment {segment_name}",
            "project": project_id,
            "rules": [
                {
                    "type": "ALL",
                    "conditions": [],
                    "rules": [
                        {
                            "type": match_type.upper() if match_type.upper() in {"ALL", "ANY"} else "ALL",
                            "conditions": conditions,
                            "rules": [],
                        }
                    ],
                }
            ],
        }
        segments = await self.list_segments(project_id)
        existing = next((s for s in segments if s.get("name") == segment_name), None)
        if existing is not None:
            seg_id = int(existing["id"])
            resp = await self._mgmt("PUT", f"/projects/{project_id}/segments/{seg_id}/", json_body=payload)
            return dict(resp.json())
        resp = await self._mgmt("POST", f"/projects/{project_id}/segments/", json_body=payload)
        return dict(resp.json())

    async def apply_segment_override(
        self,
        *,
        project_id: int,
        env_key: str,
        feature_id: int,
        segment_id: int,
        enabled: bool,
        feature_state_value: Any = None,
        priority: int = 0,
    ) -> dict[str, Any]:
        """Enable or update a targeting segment override on a feature in an environment."""
        override: dict[str, Any] = {
            "segment_id": segment_id,
            "enabled": enabled,
            "priority": priority,
            "create_if_missing": True,
        }
        if feature_state_value is not None:
            override["feature_state_value"] = feature_state_value
        return await self.apply_feature_state_mutation(
            env_key,
            feature_id,
            segment_override=override,
        )

    async def update_flag_metadata(
        self,
        project_id: int,
        feature_id: int,
        *,
        description: str | None = None,
        is_archived: bool | None = None,
    ) -> dict[str, Any]:
        """Update non-environment feature metadata such as description or archive state."""
        body: dict[str, Any] = {}
        if description is not None:
            body["description"] = description
        if is_archived is not None:
            body["is_archived"] = is_archived
        resp = await self._mgmt(
            "PATCH", f"/projects/{project_id}/features/{feature_id}/", json_body=body
        )
        return dict(resp.json())

    # ------------------------------------------------------------------
    # Experiment Capability Probe
    # ------------------------------------------------------------------

    async def probe_experiment_capability(self, env_key: str) -> dict[str, Any]:
        """Probe Flagsmith native experiments endpoint and choose native vs fallback mode."""
        resp = await self._mgmt(
            "GET",
            f"/environments/{env_key}/experiments/",
            allow_statuses={200, 400, 401, 403, 404, 501},
        )
        details: dict[str, Any]
        try:
            parsed = resp.json() if resp.content else {}
            details = parsed if isinstance(parsed, dict) else {"results": parsed}
        except ValueError:
            details = {"raw": resp.text}

        if resp.status_code == 200:
            detail_str = str(details).lower()
            warehouse_missing = (
                details.get("warehouse_configured") is False
                or "warehouse" in detail_str
                and ("not configured" in detail_str or "missing" in detail_str)
            )
            if not warehouse_missing:
                return {
                    "analytics_mode": "flagsmith_native",
                    "native_supported": True,
                    "status_code": resp.status_code,
                    "details": details,
                }

        return {
            "analytics_mode": "tracked_event_fallback",
            "native_supported": False,
            "status_code": resp.status_code,
            "details": details,
        }
