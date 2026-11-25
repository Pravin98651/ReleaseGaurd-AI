"""Shared execution context and DB/client helpers for the 34 manual agent tools."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.security import decrypt_token
from app.integrations.flagsmith_client import FlagsmithClient
from app.integrations.github_client import GitHubClient
from app.models import (
    FeatureFlag,
    FlagType,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
)
from app.services.policy_engine import is_production_environment


@dataclass
class ToolExecutionContext:
    """Runtime dependencies injected into tool handlers during agent or API runs."""

    session: AsyncSession
    user_id: uuid.UUID | None = None
    agent_task_id: uuid.UUID | None = None
    context_scope: dict[str, Any] = field(default_factory=dict)
    github_client: GitHubClient | None = None
    flagsmith_client: FlagsmithClient | None = None
    repo_base_files: dict[str, str] | None = None
    verify_edge_on_mutation: bool = False

    async def get_github_client(self) -> GitHubClient:
        """Return injected GitHubClient or build one from encrypted DB / env token."""
        if self.github_client is not None:
            return self.github_client

        stmt = (
            select(IntegrationConnection)
            .where(
                IntegrationConnection.provider == IntegrationProvider.GITHUB,
                IntegrationConnection.status == IntegrationStatus.CONNECTED,
            )
            .order_by(IntegrationConnection.updated_at.desc())
            .limit(1)
        )
        conn = (await self.session.execute(stmt)).scalar_one_or_none()
        if conn is not None and conn.access_token_encrypted:
            token = decrypt_token(conn.access_token_encrypted)
            return GitHubClient(token=token)

        env_pat = get_settings().GITHUB_PAT.strip()
        if env_pat:
            return GitHubClient(token=env_pat)

        raise ValueError(
            "GitHub integration is not connected. Connect GitHub via /settings before calling GitHub tools."
        )

    async def get_flagsmith_connection(self) -> IntegrationConnection | None:
        """Return the latest connected Flagsmith IntegrationConnection if present."""
        stmt = (
            select(IntegrationConnection)
            .where(
                IntegrationConnection.provider == IntegrationProvider.FLAGSMITH,
                IntegrationConnection.status == IntegrationStatus.CONNECTED,
            )
            .order_by(IntegrationConnection.updated_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_flagsmith_client(self) -> FlagsmithClient:
        """Return injected FlagsmithClient or build one from encrypted DB / env token."""
        if self.flagsmith_client is not None:
            return self.flagsmith_client

        cfg = get_settings()
        conn = await self.get_flagsmith_connection()
        if conn is not None and conn.access_token_encrypted:
            token = decrypt_token(conn.access_token_encrypted)
            return FlagsmithClient(
                org_token=token,
                api_url=cfg.FLAGSMITH_API_URL,
                edge_api_url=cfg.FLAGSMITH_EDGE_API_URL,
            )

        env_token = cfg.FLAGSMITH_ORG_TOKEN.strip()
        if env_token:
            return FlagsmithClient(
                org_token=env_token,
                api_url=cfg.FLAGSMITH_API_URL,
                edge_api_url=cfg.FLAGSMITH_EDGE_API_URL,
            )

        raise ValueError(
            "Flagsmith integration is not connected. Connect Flagsmith via /settings before calling Flagsmith tools."
        )

    async def resolve_flagsmith_project_id(self, explicit_project_id: int | None = None) -> int | None:
        """Resolve project ID from explicit arg, context scope, DB connection, or env."""
        if explicit_project_id is not None:
            return int(explicit_project_id)
        if self.context_scope.get("flagsmith_project_id") is not None:
            return int(self.context_scope["flagsmith_project_id"])
        conn = await self.get_flagsmith_connection()
        if conn is not None and isinstance(conn.metadata_json, dict):
            pid = conn.metadata_json.get("project_id")
            if pid is not None:
                return int(pid)
        return get_settings().flagsmith_project_id_int

    async def resolve_env_key(
        self,
        environment_name: str,
        flag: FeatureFlag | None = None,
        project_id: int | None = None,
    ) -> str:
        """Resolve Flagsmith environment API key from flag cache, integration metadata, env, or API."""
        env_norm = environment_name.strip().lower()
        if flag is not None and isinstance(flag.env_states, dict):
            for k, state in flag.env_states.items():
                if k.strip().lower() == env_norm and isinstance(state, dict):
                    ek = str(state.get("env_key") or "").strip()
                    if ek:
                        return ek

        conn = await self.get_flagsmith_connection()
        if conn is not None and isinstance(conn.metadata_json, dict):
            env_map = conn.metadata_json.get("environment_keys") or {}
            if isinstance(env_map, dict):
                for k, v in env_map.items():
                    if str(k).strip().lower() == env_norm and str(v).strip():
                        return str(v).strip()

        cfg_keys = get_settings().flagsmith_env_keys
        for k, v in cfg_keys.items():
            if k.strip().lower() == env_norm and v.strip():
                return v.strip()

        pid = project_id or (flag.flagsmith_project_id if flag else None)
        if pid is not None:
            client = await self.get_flagsmith_client()
            envs = await client.list_environments(pid)
            for env in envs:
                if str(env.get("name", "")).strip().lower() == env_norm and env.get("api_key"):
                    return str(env["api_key"])

        raise ValueError(
            f"Could not resolve Flagsmith environment key for '{environment_name}'."
        )

    async def find_feature_flag(
        self,
        *,
        flag_key: str | None = None,
        flag_id: uuid.UUID | None = None,
        project_id: int | None = None,
    ) -> FeatureFlag | None:
        """Locate a synced FeatureFlag ORM row by UUID or (project_id, key)."""
        if flag_id is not None:
            return await self.session.get(FeatureFlag, flag_id)
        if not flag_key:
            return None

        stmt = select(FeatureFlag).where(FeatureFlag.key == flag_key)
        if project_id is not None:
            stmt = stmt.where(FeatureFlag.flagsmith_project_id == project_id)
        elif self.context_scope.get("flagsmith_project_id") is not None:
            stmt = stmt.where(
                FeatureFlag.flagsmith_project_id == int(self.context_scope["flagsmith_project_id"])
            )
        row = (await self.session.execute(stmt.limit(1))).scalar_one_or_none()
        return row

    async def sync_flag_from_flagsmith(
        self,
        flag_key: str,
        *,
        project_id: int | None = None,
    ) -> FeatureFlag:
        """Fetch live Flagsmith feature & environment states and upsert local FeatureFlag row."""
        pid = await self.resolve_flagsmith_project_id(project_id)
        if pid is None:
            raise ValueError("Cannot locate Flagsmith project_id to sync feature flag.")
        client = await self.get_flagsmith_client()
        feature_data = await client.find_flag_by_key(pid, flag_key)
        if feature_data is None:
            raise ValueError(f"Feature flag '{flag_key}' not found in Flagsmith project {pid}.")

        feature_id = int(feature_data["id"])
        raw_type = str(feature_data.get("type", "STANDARD")).upper()
        flag_type = FlagType.MULTIVARIATE if raw_type == "MULTIVARIATE" else FlagType.STANDARD

        mv_options: list[dict[str, Any]] = []
        if flag_type == FlagType.MULTIVARIATE:
            mv_raw = await client.list_mv_options(pid, feature_id)
            mv_options = [
                {
                    "id": opt.get("id"),
                    "value": str(opt.get("string_value") or opt.get("value") or ""),
                    "default_percentage_allocation": int(
                        opt.get("default_percentage_allocation", 0)
                    ),
                }
                for opt in mv_raw
            ]

        envs = await client.list_environments(pid)
        segments = await client.list_segments(pid)
        seg_map = {int(s["id"]): s for s in segments if "id" in s}

        env_states: dict[str, Any] = {}
        for env in envs:
            env_name = str(env.get("name", "")).strip()
            env_key = str(env.get("api_key", "")).strip()
            if not env_name or not env_key:
                continue
            is_prod = bool(
                env.get("is_production")
                if "is_production" in env
                else is_production_environment(env_name)
            )
            use_v2 = bool(env.get("use_v2_feature_versioning", False))
            states = await client.get_environment_feature_states(env_key, feature_id=feature_id)
            base_fs, _ = client._partition_feature_states(states)

            enabled = bool(base_fs.get("enabled", False)) if base_fs else False
            val = base_fs.get("feature_state_value") if base_fs else None
            seg_overrides: list[dict[str, Any]] = []
            rollout_pct = 100 if enabled else 0

            for fs in states:
                feat_seg = fs.get("feature_segment")
                if feat_seg is None:
                    continue
                seg_id = feat_seg.get("segment") if isinstance(feat_seg, dict) else feat_seg
                seg_obj = seg_map.get(int(seg_id)) if seg_id is not None else None
                pct = _extract_pct_from_seg(seg_obj) if seg_obj else None
                seg_name = seg_obj.get("name") if seg_obj else None
                seg_enabled = bool(fs.get("enabled", False))
                seg_overrides.append(
                    {
                        "segment_id": seg_id,
                        "segment_name": seg_name,
                        "enabled": seg_enabled,
                        "percentage": pct,
                    }
                )
                if seg_enabled and pct is not None and not enabled:
                    rollout_pct = pct

            mv_state_list: list[dict[str, Any]] = []
            if base_fs and base_fs.get("multivariate_feature_state_values"):
                opt_name_by_id = {
                    int(o["id"]): o["value"] for o in mv_options if o.get("id") is not None
                }
                for item in base_fs["multivariate_feature_state_values"]:
                    opt_id = item.get("multivariate_feature_option")
                    opt_val = item.get("value") or (
                        opt_name_by_id.get(int(opt_id), str(opt_id))
                        if opt_id is not None
                        else ""
                    )
                    mv_state_list.append(
                        {
                            "value": str(opt_val),
                            "percentage_allocation": int(item.get("percentage_allocation", 0)),
                        }
                    )

            env_states[env_name.lower()] = {
                "env_key": env_key,
                "environment_name": env_name.lower(),
                "is_production": is_prod,
                "use_v2_versioning": use_v2,
                "enabled": enabled,
                "value": val,
                "rollout_percentage": rollout_pct,
                "mv_options": mv_state_list,
                "segment_overrides": seg_overrides,
                "version_uuid": (
                    base_fs.get("version_uuid") or base_fs.get("environment_feature_version")
                )
                if base_fs
                else None,
            }

        existing = await self.find_feature_flag(flag_key=flag_key, project_id=pid)
        repo_stmt = (
            select(Repository)
            .where(Repository.flagsmith_project_id == pid)
            .limit(1)
        )
        linked_repo = (await self.session.execute(repo_stmt)).scalar_one_or_none()
        now = utcnow()
        if existing is None:
            existing = FeatureFlag(
                flagsmith_feature_id=feature_id,
                flagsmith_project_id=pid,
                repository_id=linked_repo.id if linked_repo else None,
                key=flag_key,
                description=feature_data.get("description") or "",
                flag_type=flag_type,
                is_archived=bool(feature_data.get("is_archived", False)),
                env_states=env_states,
                mv_options=mv_options,
                last_synced_at=now,
            )
            self.session.add(existing)
        else:
            existing.flagsmith_feature_id = feature_id
            existing.description = feature_data.get("description") or existing.description
            existing.flag_type = flag_type
            existing.is_archived = bool(feature_data.get("is_archived", False))
            existing.env_states = env_states
            existing.mv_options = mv_options
            if linked_repo and existing.repository_id is None:
                existing.repository_id = linked_repo.id
            existing.last_synced_at = now

        await self.session.flush()
        return existing


def _extract_pct_from_seg(seg_obj: dict[str, Any] | None) -> int | None:
    if not seg_obj:
        return None
    for cond in seg_obj.get("conditions", []) or []:
        if str(cond.get("operator", "")).upper() == "PERCENTAGE_SPLIT":
            try:
                return int(float(str(cond.get("value", 0))))
            except (TypeError, ValueError):
                return None
    for sub in seg_obj.get("rules", []) or []:
        found = _extract_pct_from_seg(sub)
        if found is not None:
            return found
    return None
