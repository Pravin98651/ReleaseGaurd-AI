"""Unified Flagsmith Command Center, Catalog Lifecycle, Identity Debugger & Test Harness API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.agents.approval_executor import capture_expected_precondition
from app.api.deps import AuthenticatedOperator, DBSession, DefaultOperator, build_tool_context
from app.api.v1.errors import APIException
from app.core.database import utcnow
from app.integrations.flagsmith_client import FlagsmithAPIError
from app.models import (
    ActorType,
    AnalyticsMode,
    AuditStatus,
    Experiment,
    FeatureFlag,
    FlagType,
    PolicyCategory,
)
from app.services.flagsmith_service import (
    FLAG_CATALOG,
    TARGETING_PRESETS,
    FlagsmithGovernanceService,
)
from app.services.policy_engine import PolicyEngine, is_production_environment

flagsmith_hub_router = APIRouter(prefix="/flagsmith", tags=["flagsmith"])


class IdentityExplainRequest(BaseModel):
    """Evaluate and explain Flagsmith variation assignment for an identity + traits."""

    environment_name: str = Field(default="development", min_length=1, max_length=64)
    identifier: str = Field(min_length=1, max_length=128)
    traits: dict[str, Any] = Field(default_factory=dict)


class CatalogProvisionRequest(BaseModel):
    """Idempotently provision missing governed catalog flags into Flagsmith project."""

    keys: list[str] = Field(default_factory=list)
    include_targeting_segments: bool = True


class TargetingSegmentApplyRequest(BaseModel):
    """Apply or propose a Flagsmith targeting segment override."""

    flag_key: str = Field(min_length=1, max_length=128)
    environment_name: str = Field(default="development", min_length=1, max_length=64)
    segment_name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    feature_state_value: Any = None


class TestHarnessRunRequest(BaseModel):
    """Synthetic test traffic generation request (strictly blocked on production)."""

    identities_per_arm: int = Field(default=30, ge=5, le=200)
    scenario: str = Field(
        default="winning_variant",
        description="winning_variant | healthy_control | underperforming_variant | neutral | guardrail_breach | insufficient_sample",
    )
    seed: int = Field(default=42, ge=1, le=1_000_000)
    start_index: int = Field(default=0, ge=0, le=100_000)
    profile: dict[str, dict[str, float]] | None = None


@flagsmith_hub_router.get("/command-center")
async def get_flagsmith_command_center(
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Return authoritative Flagsmith Command Center overview, catalog, experiments, approvals, and history."""
    return await FlagsmithGovernanceService.get_command_center_summary(
        session, app=request.app
    )


@flagsmith_hub_router.get("/catalog")
async def get_flag_catalog(
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Return governed feature flag catalog merged with live Flagsmith state & lifecycle audit."""
    items = await FlagsmithGovernanceService.get_catalog_report(session)
    return {"items": items, "total": len(items)}


@flagsmith_hub_router.post("/catalog/provision")
async def provision_catalog_flags(
    body: CatalogProvisionRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> dict[str, Any]:
    """Category B: Idempotently provision missing catalog flags (disabled in prod) & targeting presets."""
    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    pid = await ctx.resolve_flagsmith_project_id()
    if pid is None:
        raise APIException(
            503,
            "FLAGSMITH_PROJECT_UNCONFIGURED",
            "No Flagsmith project_id configured in Settings or environment.",
            retryable=False,
        )

    client = await ctx.get_flagsmith_client()
    existing_remote = await client.list_flags(pid)
    remote_by_key = {str(f.get("name")): f for f in existing_remote}

    target_keys = body.keys or list(FLAG_CATALOG.keys())
    provisioned: list[str] = []
    skipped_existing: list[str] = []

    for key in target_keys:
        spec = FLAG_CATALOG.get(key)
        if spec is None:
            continue
        if key in remote_by_key:
            remote_feat = remote_by_key[key]
            if spec.flag_type == "MULTIVARIATE" and spec.variants:
                existing_mv = await client.list_mv_options(pid, int(remote_feat["id"]))
                existing_vals = {
                    str(o.get("string_value") or o.get("value") or "")
                    for o in existing_mv
                }
                for v in spec.variants:
                    if v not in existing_vals:
                        await client.create_mv_option(
                            pid,
                            int(remote_feat["id"]),
                            value=v,
                            default_percentage_allocation=0,
                        )
            await ctx.sync_flag_from_flagsmith(key, project_id=pid)
            skipped_existing.append(key)
            continue

        mv_opts = None
        if spec.flag_type == "MULTIVARIATE" and spec.variants:
            base_pct = 100 // len(spec.variants)
            rem = 100 - base_pct * len(spec.variants)
            mv_opts = [
                {
                    "value": v,
                    "default_percentage_allocation": base_pct + (rem if idx == 0 else 0),
                }
                for idx, v in enumerate(spec.variants)
            ]

        await client.create_flag(
            project_id=pid,
            key=spec.key,
            description=spec.purpose,
            flag_type=spec.flag_type,
            default_enabled=False,
            mv_options=mv_opts,
        )
        await ctx.sync_flag_from_flagsmith(key, project_id=pid)
        provisioned.append(key)

    segments_synced: list[str] = []
    if body.include_targeting_segments:
        for preset in TARGETING_PRESETS:
            await client.upsert_trait_segment(
                project_id=pid,
                segment_name=preset["segment_name"],
                trait_conditions=preset["conditions"],
                match_type=preset["match_type"],
                description=preset["description"],
            )
            segments_synced.append(preset["segment_name"])

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="flagsmith.catalog_provisioned",
        resource_type="feature_flag",
        resource_id=str(pid),
        environment_name="development",
        status=AuditStatus.SUCCESS,
        details={
            "provisioned": provisioned,
            "skipped_existing": skipped_existing,
            "segments": segments_synced,
        },
    )
    await session.commit()
    return {
        "project_id": pid,
        "provisioned": provisioned,
        "skipped_existing": skipped_existing,
        "segments_synced": segments_synced,
    }


@flagsmith_hub_router.post("/identities/explain")
async def explain_identity_assignment(
    body: IdentityExplainRequest,
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> dict[str, Any]:
    """Category A: Explain why an identity with given traits receives each Flagsmith variation."""
    ctx = await build_tool_context(request.app, session)
    return await FlagsmithGovernanceService.explain_identity(
        ctx,
        environment_name=body.environment_name,
        identifier=body.identifier,
        traits=body.traits,
    )


@flagsmith_hub_router.post("/targeting/apply")
async def apply_targeting_segment_override(
    body: TargetingSegmentApplyRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> Any:
    """Apply targeting segment override in dev/staging, or require Category C human approval in prod."""
    env_norm = body.environment_name.strip().lower()
    if is_production_environment(env_norm):
        raise APIException(
            403,
            "PRODUCTION_TARGETING_REQUIRES_APPROVAL",
            "Production segment targeting overrides must go through /api/v1/flags/mutate with human approval.",
        )

    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    pid = await ctx.resolve_flagsmith_project_id()
    if pid is None:
        raise APIException(400, "MISSING_PROJECT", "Flagsmith project_id is not configured.")

    client = await ctx.get_flagsmith_client()
    flag_obj = await ctx.find_feature_flag(flag_key=body.flag_key, project_id=pid)
    if flag_obj is None or flag_obj.flagsmith_feature_id is None:
        flag_obj = await ctx.sync_flag_from_flagsmith(body.flag_key, project_id=pid)
    if flag_obj is None or flag_obj.flagsmith_feature_id is None:
        raise APIException(404, "FEATURE_FLAG_NOT_FOUND", f"Flag '{body.flag_key}' not found in Flagsmith.")

    segs = await client.list_segments(pid)
    seg = next((s for s in segs if s.get("name") == body.segment_name), None)
    if seg is None:
        preset = next((p for p in TARGETING_PRESETS if p["segment_name"] == body.segment_name), None)
        if preset is None:
            raise APIException(404, "SEGMENT_NOT_FOUND", f"Segment '{body.segment_name}' not found.")
        seg = await client.upsert_trait_segment(
            project_id=pid,
            segment_name=preset["segment_name"],
            trait_conditions=preset["conditions"],
            match_type=preset["match_type"],
            description=preset["description"],
        )

    env_key = await ctx.resolve_env_key(env_norm, flag=flag_obj, project_id=pid)
    res = await client.apply_segment_override(
        project_id=pid,
        env_key=env_key,
        feature_id=int(flag_obj.flagsmith_feature_id),
        segment_id=int(seg["id"]),
        enabled=body.enabled,
        feature_state_value=body.feature_state_value,
    )
    await ctx.sync_flag_from_flagsmith(body.flag_key, project_id=pid)
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="flagsmith.segment_override_applied",
        resource_type="feature_flag",
        resource_id=str(flag_obj.id),
        environment_name=env_norm,
        status=AuditStatus.SUCCESS,
        details={"flag_key": body.flag_key, "segment_name": body.segment_name, "enabled": body.enabled},
    )
    await session.commit()
    return {"applied": True, "flag_key": body.flag_key, "environment": env_norm, "mutation": res}


@flagsmith_hub_router.post("/experiments/{exp_id}/run-test-harness")
async def run_synthetic_experiment_harness(
    exp_id: uuid.UUID,
    body: TestHarnessRunRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> dict[str, Any]:
    """Run labelled synthetic test traffic harness on non-production Flagsmith experiment."""
    exp = await session.get(Experiment, exp_id)
    if exp is None:
        raise APIException(404, "EXPERIMENT_NOT_FOUND", f"Experiment {exp_id} not found.")
    if is_production_environment(exp.environment_name):
        raise APIException(
            403,
            "SYNTHETIC_TRAFFIC_BLOCKED_IN_PROD",
            "Synthetic test traffic harness cannot run against production experiments.",
        )
    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    report = await FlagsmithGovernanceService.run_experiment_traffic_harness(
        ctx,
        experiment=exp,
        profile=body.profile,
        identities_per_arm=body.identities_per_arm,
        scenario=body.scenario,
        seed=body.seed,
        start_index=body.start_index,
    )
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="experiment.synthetic_test_harness_run",
        resource_type="experiment",
        resource_id=str(exp.id),
        environment_name=exp.environment_name,
        status=AuditStatus.SUCCESS,
        details={"batch_id": report["batch_id"], "identities": report["identities_evaluated"]},
    )
    await session.commit()
    return report
