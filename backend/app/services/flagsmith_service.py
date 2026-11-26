"""Central Flagsmith Governance, Catalog, Identity Debugger, CLI Bridge & Experimentation Service."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.context import ToolExecutionContext
from app.core.config import get_settings
from app.core.database import ensure_utc, utcnow
from app.integrations.flagsmith_client import FlagsmithAPIError, FlagsmithClient
from app.models import (
    AnalyticsMode,
    Approval,
    ApprovalStatus,
    AuditLog,
    Experiment,
    ExperimentEvent,
    ExperimentEventType,
    ExperimentStatus,
    FeatureFlag,
    FlagType,
    ProgressiveRollout,
    ProgressiveRolloutStatus,
    Release,
)

logger = logging.getLogger("releaseguard.flagsmith_governance")


@dataclass(frozen=True)
class CatalogFlagSpec:
    """Authoritative governance definition for a verified ReleaseGuard/NovaShop flag."""

    key: str
    category: str  # "novashop_app" | "operational_kill_switch" | "releaseguard_capability"
    flag_type: str  # "MULTIVARIATE" | "STANDARD"
    purpose: str
    owner: str
    default_enabled: bool
    safe_default_value: Any
    variants: list[str]
    code_references: list[str]
    linked_tests: list[str]
    primary_metric: str | None = None
    guardrail_metrics: list[str] = field(default_factory=list)
    review_days: int = 30
    permanent: bool = False
    is_security_boundary: bool = False  # Always False; flags never authorize security/policy


# Meaningful non-duplicate catalog where every flag gates genuine code paths.
FLAG_CATALOG: dict[str, CatalogFlagSpec] = {
    # --- A. NovaShop Application & Experimentation Flags ---
    "checkout_v2": CatalogFlagSpec(
        key="checkout_v2",
        category="novashop_app",
        flag_type="STANDARD",
        purpose="Controls NovaShop Classic v1 3-step checkout vs Express v2 rollout path",
        owner="checkout-platform",
        default_enabled=False,
        safe_default_value="v1",
        variants=["v1", "v2"],
        code_references=[
            "demo-app/backend/app/flagsmith_service.py",
            "demo-app/backend/app/checkout.py",
            "demo-app/frontend/components/NovaShopStorefront.tsx",
        ],
        linked_tests=["demo-app/tests/test_demo_app.py", "backend/tests/test_flagsmith_framework.py"],
        primary_metric="checkout_completed",
        guardrail_metrics=["payment_failed", "checkout_abandoned"],
        review_days=30,
    ),
    "checkout_experience_v2": CatalogFlagSpec(
        key="checkout_experience_v2",
        category="novashop_app",
        flag_type="MULTIVARIATE",
        purpose="Multivariate checkout experiment comparing control (3-step), simplified_checkout (2-step), and one_page_checkout (express)",
        owner="checkout-platform",
        default_enabled=False,
        safe_default_value="control",
        variants=["control", "simplified_checkout", "one_page_checkout"],
        code_references=[
            "demo-app/backend/app/checkout.py",
            "demo-app/frontend/components/CheckoutV1Form.tsx",
            "demo-app/frontend/components/CheckoutV2Express.tsx",
        ],
        linked_tests=["demo-app/tests/test_demo_app.py", "backend/tests/test_flagsmith_framework.py"],
        primary_metric="checkout_completed",
        guardrail_metrics=["payment_failed", "checkout_abandoned"],
        review_days=30,
    ),
    "recommendation_engine_v2": CatalogFlagSpec(
        key="recommendation_engine_v2",
        category="novashop_app",
        flag_type="MULTIVARIATE",
        purpose="Multivariate product recommendation algorithm test: control (popularity), collaborative_v2 (cart affinity), personalized_v2 (trait-personalized)",
        owner="discovery-ml",
        default_enabled=False,
        safe_default_value="control",
        variants=["control", "collaborative_v2", "personalized_v2"],
        code_references=[
            "demo-app/backend/app/features.py",
            "demo-app/backend/app/main.py",
            "demo-app/frontend/components/NovaShopStorefront.tsx",
        ],
        linked_tests=["demo-app/tests/test_demo_app.py", "backend/tests/test_flagsmith_framework.py"],
        primary_metric="product_clicked_from_recommendation",
        guardrail_metrics=["payment_failed"],
        review_days=30,
    ),
    "smart_product_search": CatalogFlagSpec(
        key="smart_product_search",
        category="novashop_app",
        flag_type="MULTIVARIATE",
        purpose="Multivariate product search ranking test: control (substring), ranked_v2 (multi-field weighted), semantic_v2 (synonym & intent tags)",
        owner="search-core",
        default_enabled=False,
        safe_default_value="control",
        variants=["control", "ranked_v2", "semantic_v2"],
        code_references=[
            "demo-app/backend/app/features.py",
            "demo-app/backend/app/main.py",
            "demo-app/frontend/components/NovaShopStorefront.tsx",
        ],
        linked_tests=["demo-app/tests/test_demo_app.py", "backend/tests/test_flagsmith_framework.py"],
        primary_metric="search_result_clicked",
        guardrail_metrics=["checkout_abandoned"],
        review_days=30,
    ),
    "new_payment_flow": CatalogFlagSpec(
        key="new_payment_flow",
        category="novashop_app",
        flag_type="MULTIVARIATE",
        purpose="Multivariate payment gateway test: control (standard card gateway) vs express_token_v2 (idempotent single-token wallet)",
        owner="payments-core",
        default_enabled=False,
        safe_default_value="control",
        variants=["control", "express_token_v2"],
        code_references=[
            "demo-app/backend/app/checkout.py",
            "demo-app/backend/app/features.py",
        ],
        linked_tests=["demo-app/tests/test_demo_app.py", "backend/tests/test_flagsmith_framework.py"],
        primary_metric="payment_completed",
        guardrail_metrics=["payment_failed"],
        review_days=30,
    ),
    # --- B. Operational & Safety Kill Switches ---
    "enable_ai_recommendations": CatalogFlagSpec(
        key="enable_ai_recommendations",
        category="operational_kill_switch",
        flag_type="STANDARD",
        purpose="Operational kill switch for AI recommendation algorithms; falls back safely to deterministic popularity ranking",
        owner="sre-release-eng",
        default_enabled=True,
        safe_default_value=True,
        variants=[],
        code_references=["demo-app/backend/app/features.py", "demo-app/backend/app/main.py"],
        linked_tests=["demo-app/tests/test_demo_app.py", "backend/tests/test_flagsmith_framework.py"],
        permanent=True,
    ),
    "enable_payment_v2": CatalogFlagSpec(
        key="enable_payment_v2",
        category="operational_kill_switch",
        flag_type="STANDARD",
        purpose="Operational kill switch for Payment Flow v2; forces immediate fallback to classic card authorization",
        owner="payments-core",
        default_enabled=True,
        safe_default_value=True,
        variants=[],
        code_references=["demo-app/backend/app/checkout.py", "demo-app/backend/app/features.py"],
        linked_tests=["demo-app/tests/test_demo_app.py", "backend/tests/test_flagsmith_framework.py"],
        permanent=True,
    ),
    "enable_new_search_backend": CatalogFlagSpec(
        key="enable_new_search_backend",
        category="operational_kill_switch",
        flag_type="STANDARD",
        purpose="Operational kill switch for ranked/semantic search backends; falls back to deterministic substring matching",
        owner="search-core",
        default_enabled=True,
        safe_default_value=True,
        variants=[],
        code_references=["demo-app/backend/app/features.py"],
        linked_tests=["demo-app/tests/test_demo_app.py", "backend/tests/test_flagsmith_framework.py"],
        permanent=True,
    ),
    # --- C. ReleaseGuard AI Capability Flags (never bypass Auth/RBAC/PolicyEngine) ---
    "enable_ai_pr_review": CatalogFlagSpec(
        key="enable_ai_pr_review",
        category="releaseguard_capability",
        flag_type="STANDARD",
        purpose="Controls optional Gemini AI PR review enrichment; falls back to static AST analyzer cleanly",
        owner="releaseguard-ai",
        default_enabled=True,
        safe_default_value=True,
        variants=[],
        code_references=["backend/app/agents/specialists/pr_agent.py", "backend/app/services/flagsmith_service.py"],
        linked_tests=["backend/tests/test_flagsmith_framework.py"],
        permanent=True,
    ),
    "enable_progressive_delivery": CatalogFlagSpec(
        key="enable_progressive_delivery",
        category="releaseguard_capability",
        flag_type="STANDARD",
        purpose="Controls progressive multi-stage rollout engine capability without altering manual PolicyEngine gates",
        owner="releaseguard-ai",
        default_enabled=True,
        safe_default_value=True,
        variants=[],
        code_references=["backend/app/api/v1/progressive_delivery.py", "backend/app/services/flagsmith_service.py"],
        linked_tests=["backend/tests/test_flagsmith_framework.py"],
        permanent=True,
    ),
}

# Standard recommended targeting segments for NovaShop & ReleaseGuard testing workflows
TARGETING_PRESETS: list[dict[str, Any]] = [
    {
        "segment_name": "rg_internal_testers",
        "description": "Internal engineering testers (`is_internal_tester == true` or `user_type == internal`)",
        "match_type": "ANY",
        "conditions": [
            {"property": "is_internal_tester", "operator": "EQUAL", "value": "true"},
            {"property": "user_type", "operator": "EQUAL", "value": "internal"},
        ],
    },
    {
        "segment_name": "rg_qa_and_beta",
        "description": "QA engineers and beta subscription cohorts",
        "match_type": "ANY",
        "conditions": [
            {"property": "user_type", "operator": "EQUAL", "value": "qa"},
            {"property": "subscription_plan", "operator": "EQUAL", "value": "beta"},
        ],
    },
    {
        "segment_name": "rg_returning_enterprise",
        "description": "Returning enterprise customers (`subscription_plan == enterprise`)",
        "match_type": "ALL",
        "conditions": [
            {"property": "subscription_plan", "operator": "EQUAL", "value": "enterprise"},
        ],
    },
]


def get_flag_safe_default(flag_key: str) -> tuple[bool, Any]:
    """Return explicit per-flag safe default (never a single blind global fallback)."""
    spec = FLAG_CATALOG.get(flag_key)
    if spec is not None:
        return spec.default_enabled, spec.safe_default_value
    return False, None


def detect_cli_capabilities() -> dict[str, Any]:
    """Detect installed Flagsmith CLI binary cleanly without exposing credentials."""
    bin_path = shutil.which("flagsmith")
    npx_path = shutil.which("npx")
    version_text: str | None = None
    cli_available = False

    if bin_path:
        try:
            proc = subprocess.run(
                [bin_path, "--version"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if proc.returncode == 0:
                version_text = proc.stdout.strip()
                cli_available = True
        except (OSError, subprocess.SubprocessError):
            pass

    return {
        "cli_binary_found": bool(bin_path),
        "cli_version": version_text,
        "npx_available": bool(npx_path),
        "supported_cli_commands": [
            "flagsmith auth status",
            "flagsmith project list",
            "flagsmith environment list",
            "flagsmith feature list -p <project_id> --json",
            "flagsmith feature create <name> --type MULTIVARIATE",
            "flagsmith evaluate -e <env_key> --sdk-api-url https://edge.api.flagsmith.com --json",
            "flagsmith api <path> -X <method> --json",
        ],
        "auth_note": (
            "Admin CLI (`feature`/`project`) requires a Flagsmith Master API Key (`FLAGSMITH_API_KEY`) "
            "or `flagsmith login`; SDK `evaluate` uses the environment SDK key; ReleaseGuard backend Management API "
            "operates cleanly with `FLAGSMITH_ORG_TOKEN`."
        ),
        "verified": cli_available or bool(npx_path),
    }


class FlagsmithGovernanceService:
    """Unified service orchestrating catalog lifecycle, CLI/API sync, identity debugging, and experiments."""

    @staticmethod
    async def evaluate_capability_flag(
        *,
        app: FastAPI | None,
        session: AsyncSession,
        flag_key: str,
    ) -> dict[str, Any]:
        """Evaluate an operational/capability flag from Flagsmith Edge with explicit safe default."""
        default_enabled, safe_val = get_flag_safe_default(flag_key)
        cfg = get_settings()
        env_key = (
            cfg.FLAGSMITH_DEV_ENV_KEY
            or cfg.FLAGSMITH_STAGING_ENV_KEY
            or cfg.FLAGSMITH_PROD_ENV_KEY
            or ""
        ).strip()
        if not env_key or not cfg.FLAGSMITH_ORG_TOKEN.strip():
            return {
                "flag_key": flag_key,
                "enabled": default_enabled,
                "value": safe_val,
                "source": "safe_default",
                "authoritative_security_unchanged": True,
            }
        try:
            transport = getattr(app.state, "flagsmith_transport", None) if app else None
            client = FlagsmithClient(
                org_token=cfg.FLAGSMITH_ORG_TOKEN.strip(),
                api_url=cfg.FLAGSMITH_API_URL,
                edge_api_url=cfg.FLAGSMITH_EDGE_API_URL,
                transport=transport,
            )
            edge_flags = await client.get_edge_flags(env_key)
            for item in edge_flags:
                feat = item.get("feature") or {}
                if feat.get("name") == flag_key:
                    return {
                        "flag_key": flag_key,
                        "enabled": bool(item.get("enabled", default_enabled)),
                        "value": item.get("feature_state_value", safe_val),
                        "source": "flagsmith_edge",
                        "authoritative_security_unchanged": True,
                    }
        except (FlagsmithAPIError, OSError, ValueError) as exc:
            logger.debug("Capability flag '%s' fallback to safe default: %s", flag_key, exc)

        return {
            "flag_key": flag_key,
            "enabled": default_enabled,
            "value": safe_val,
            "source": "safe_default",
            "authoritative_security_unchanged": True,
        }

    @staticmethod
    async def get_catalog_report(session: AsyncSession) -> list[dict[str, Any]]:
        """Merge Flagsmith DB flags, FlagHealth, catalog ownership/review dates, and lifecycle status."""
        db_flags = list(
            (await session.execute(select(FeatureFlag).order_by(FeatureFlag.key.asc())))
            .scalars()
            .all()
        )
        by_key = {f.key: f for f in db_flags}
        all_keys = sorted(set(FLAG_CATALOG.keys()) | set(by_key.keys()))
        now = utcnow()
        items: list[dict[str, Any]] = []

        for key in all_keys:
            spec = FLAG_CATALOG.get(key)
            db_flag = by_key.get(key)
            created_at = ensure_utc(db_flag.created_at) if db_flag else None
            review_days = spec.review_days if spec else 30
            review_due_at = (
                (created_at + timedelta(days=review_days)).isoformat()
                if created_at and not (spec and spec.permanent)
                else None
            )
            is_stale = bool(
                db_flag
                and created_at
                and not (spec and spec.permanent)
                and (now - created_at) > timedelta(days=review_days)
            )
            env_states = dict(db_flag.env_states or {}) if db_flag else {}
            inconsistent_envs = False
            if len(env_states) >= 2:
                enabled_set = {
                    bool(st.get("enabled"))
                    for st in env_states.values()
                    if isinstance(st, dict)
                }
                inconsistent_envs = len(enabled_set) > 1

            health_status_val = "unprovisioned"
            if db_flag:
                hs = db_flag.health_score
                if hs is None:
                    health_status_val = "unknown"
                elif hs >= 80:
                    health_status_val = "healthy"
                elif hs >= 50:
                    health_status_val = "warning"
                else:
                    health_status_val = "critical"

            cleanup_reasons: list[str] = []
            if db_flag and not spec:
                cleanup_reasons.append("not_in_governed_catalog")
            if db_flag and (not db_flag.code_references):
                cleanup_reasons.append("no_code_references_scanned")
            if spec and not spec.linked_tests:
                cleanup_reasons.append("missing_linked_tests")
            if is_stale:
                cleanup_reasons.append("review_date_expired")
            if db_flag and health_status_val in ("warning", "critical"):
                cleanup_reasons.append(f"health:{health_status_val}")

            items.append(
                {
                    "key": key,
                    "registered_in_flagsmith": bool(db_flag and db_flag.flagsmith_feature_id),
                    "flagsmith_feature_id": db_flag.flagsmith_feature_id if db_flag else None,
                    "flag_type": (
                        db_flag.flag_type.value
                        if db_flag
                        else (spec.flag_type if spec else "STANDARD")
                    ),
                    "category": spec.category if spec else "discovered_external",
                    "owner": spec.owner if spec else "unassigned",
                    "purpose": (
                        db_flag.description
                        if db_flag and db_flag.description
                        else (spec.purpose if spec else "Discovered Flagsmith feature")
                    ),
                    "default_enabled": spec.default_enabled if spec else False,
                    "safe_default_value": spec.safe_default_value if spec else None,
                    "variants": spec.variants if spec else list((db_flag.mv_options or []) if db_flag else []),
                    "code_references": (
                        list(db_flag.code_references)
                        if db_flag and db_flag.code_references
                        else (spec.code_references if spec else [])
                    ),
                    "linked_tests": spec.linked_tests if spec else [],
                    "primary_metric": spec.primary_metric if spec else None,
                    "guardrail_metrics": spec.guardrail_metrics if spec else [],
                    "env_states": env_states,
                    "inconsistent_environments": inconsistent_envs,
                    "health_score": db_flag.health_score if db_flag else None,
                    "health_status": health_status_val,
                    "permanent": spec.permanent if spec else False,
                    "review_due_at": review_due_at,
                    "stale": is_stale,
                    "cleanup_recommendations": cleanup_reasons,
                    "never_auto_delete": True,
                }
            )
        return items

    @staticmethod
    async def explain_identity(
        ctx: ToolExecutionContext,
        *,
        environment_name: str,
        identifier: str,
        traits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate and explain what a specific identity sees in Flagsmith Edge + repeat stability."""
        _SENSITIVE_TRAIT_KEYS = {
            "password",
            "token",
            "secret",
            "api_key",
            "apikey",
            "ssn",
            "credit_card",
            "card_number",
            "authorization",
            "cookie",
        }

        def _redact_traits(raw: dict[str, Any] | None) -> dict[str, Any]:
            if not raw:
                return {}
            out: dict[str, Any] = {}
            for k, v in raw.items():
                if any(s in str(k).lower() for s in _SENSITIVE_TRAIT_KEYS):
                    out[str(k)] = "[REDACTED]"
                else:
                    out[str(k)] = v
            return out

        env_norm = environment_name.strip().lower()
        client = await ctx.get_flagsmith_client()
        env_key = await ctx.resolve_env_key(env_norm)
        first = await client.evaluate_identity_detailed(
            env_key, identifier=identifier, traits=traits
        )
        second = await client.evaluate_identity_detailed(
            env_key, identifier=identifier, traits=traits
        )
        consistent = first["flags"] == second["flags"]
        safe_traits = _redact_traits(first.get("traits") if isinstance(first.get("traits"), dict) else traits)

        explanations: list[dict[str, Any]] = []
        table_lines = [
            f"**Identity:** `{first['identifier']}`",
            f"**Environment:** `{env_norm}`",
            "",
            "| Feature Flag | Observed Result | Fallback Default | Explanation (Inferred vs. Observed) |",
            "|---|---|---|---|",
        ]
        for flag_key, info in sorted(first["flags"].items()):
            enabled = bool(info.get("enabled", False))
            val = info.get("value")
            default_enabled, safe_default = get_flag_safe_default(flag_key)
            if val is not None and str(val).strip() not in ("", "None"):
                result_label = str(val) if enabled else f"Disabled ({val})"
            else:
                result_label = "Enabled" if enabled else "Disabled"

            if enabled and val is not None and str(val) not in ("control", "v1", "false", ""):
                why = (
                    "Observed via Flagsmith Edge API: variant/feature enabled for this identity "
                    "(matches multivariate split or targeting rule)."
                )
            elif enabled:
                why = "Observed via Flagsmith Edge API: enabled in environment default or matching segment."
            else:
                why = "Observed via Flagsmith Edge API: disabled (no matching targeting rule or environment default is off)."

            explanations.append(
                {
                    "flag_key": flag_key,
                    "enabled": enabled,
                    "value": val,
                    "result": result_label,
                    "fallback_default": {"enabled": default_enabled, "value": safe_default},
                    "evaluation_source": "OBSERVED_EDGE_API",
                    "explanation_source": "INFERRED_FROM_EDGE_RESPONSE",
                    "explanation": why,
                }
            )
            table_lines.append(
                f"| `{flag_key}` | {result_label} | `enabled={default_enabled}, value={safe_default}` | {why} |"
            )

        return {
            "identifier": first["identifier"],
            "environment_name": env_norm,
            "traits": safe_traits,
            "consistent_on_repeat_evaluation": consistent,
            "flags": list(first["flags"].values()),
            "flag_explanations": explanations,
            "markdown_summary": "\n".join(table_lines),
            "evidence_note": (
                "Flag enabled/value states are directly OBSERVED from Flagsmith Edge API; "
                "human-readable rule attribution is INFERRED from the returned state because Edge API "
                "does not expose internal segment IDs."
            ),
            "security_note": "Client traits are used strictly for product targeting, never for RBAC or security authorization.",
        }

    @staticmethod
    async def run_experiment_traffic_harness(
        ctx: ToolExecutionContext,
        *,
        experiment: Experiment,
        profile: dict[str, dict[str, float]] | None = None,
        identities_per_arm: int = 30,
        scenario: str = "winning_variant",
        seed: int = 42,
        start_index: int = 0,
    ) -> dict[str, Any]:
        """Run repeatable synthetic test traffic through Flagsmith identity evaluation + telemetry."""
        flag = await ctx.session.get(FeatureFlag, experiment.feature_flag_id)
        if flag is None:
            raise ValueError("Experiment feature flag not found.")

        env_name = experiment.environment_name.strip().lower()
        if env_name == "production":
            raise ValueError(
                "Synthetic test traffic harness is strictly restricted to non-production environments."
            )

        client = await ctx.get_flagsmith_client()
        env_key = experiment.environment_key or await ctx.resolve_env_key(
            env_name, flag=flag, project_id=flag.flagsmith_project_id
        )

        scenario_profiles: dict[str, dict[str, dict[str, float]]] = {
            "winning_variant": {
                experiment.control_variation: {"conv_prob": 0.12, "err_prob": 0.01},
                experiment.treatment_variation: {"conv_prob": 0.26, "err_prob": 0.01},
            },
            "healthy_control": {
                experiment.control_variation: {"conv_prob": 0.22, "err_prob": 0.01},
                experiment.treatment_variation: {"conv_prob": 0.12, "err_prob": 0.012},
            },
            "underperforming_variant": {
                experiment.control_variation: {"conv_prob": 0.18, "err_prob": 0.01},
                experiment.treatment_variation: {"conv_prob": 0.08, "err_prob": 0.015},
            },
            "neutral": {
                experiment.control_variation: {"conv_prob": 0.14, "err_prob": 0.012},
                experiment.treatment_variation: {"conv_prob": 0.145, "err_prob": 0.013},
            },
            "guardrail_breach": {
                experiment.control_variation: {"conv_prob": 0.12, "err_prob": 0.01},
                experiment.treatment_variation: {"conv_prob": 0.15, "err_prob": 0.16},
            },
            "insufficient_sample": {
                experiment.control_variation: {"conv_prob": 0.12, "err_prob": 0.01},
                experiment.treatment_variation: {"conv_prob": 0.28, "err_prob": 0.01},
            },
        }
        arm_cfg = profile or scenario_profiles.get(scenario, scenario_profiles["winning_variant"])
        per_arm = max(5, min(int(identities_per_arm), 200))
        if scenario == "insufficient_sample":
            per_arm = min(per_arm, 20)
        total_identities = per_arm * len(arm_cfg)
        batch_id = hashlib.sha256(
            f"{experiment.id}:{scenario}:{seed}:{start_index}:{total_identities}".encode()
        ).hexdigest()[:8]
        rng = random.Random(seed)
        exposures_recorded = 0
        conversions_recorded = 0
        guardrail_errors_recorded = 0
        deduplicated_skipped = 0
        observed_assignments: dict[str, int] = {}
        execution_logs: list[str] = [
            f"Starting harness batch={batch_id} scenario={scenario} seed={seed} "
            f"start_index={start_index} total_identities={total_identities} env={env_name}"
        ]
        now = utcnow()
        sem = asyncio.Semaphore(8)

        async def _eval_one(idx: int) -> tuple[str, int, str]:
            uid = f"sim_{scenario}_{seed}_{idx:04d}"
            async with sem:
                try:
                    eval_res = await client.evaluate_identity_detailed(
                        env_key,
                        identifier=uid,
                        traits={"synthetic_test_traffic": True, "scenario": scenario},
                    )
                    assigned_info = eval_res["flags"].get(flag.key)
                    raw_val = (
                        str(assigned_info.get("value"))
                        if assigned_info and assigned_info.get("value") is not None
                        else ""
                    )
                    if raw_val in arm_cfg:
                        observed_var = raw_val
                    elif assigned_info and assigned_info.get("enabled") is True:
                        observed_var = experiment.treatment_variation
                    else:
                        observed_var = experiment.control_variation
                except FlagsmithAPIError:
                    observed_var = (
                        experiment.treatment_variation if idx % 2 == 0 else experiment.control_variation
                    )
            return uid, idx, observed_var

        evaluated = await asyncio.gather(
            *[_eval_one(idx) for idx in range(start_index, start_index + total_identities)]
        )

        candidate_ids = [
            suffix_id
            for _, idx, _ in evaluated
            for suffix_id in (
                f"sim_{batch_id}_{flag.key}_{idx}_exp",
                f"sim_{batch_id}_{flag.key}_{idx}_err",
                f"sim_{batch_id}_{flag.key}_{idx}_conv",
            )
        ]
        existing_ids = set(
            (
                await ctx.session.execute(
                    select(ExperimentEvent.event_id).where(
                        ExperimentEvent.event_id.in_(candidate_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

        for uid, idx, observed_var in evaluated:
            observed_assignments[observed_var] = observed_assignments.get(observed_var, 0) + 1
            probs = arm_cfg.get(observed_var, arm_cfg[experiment.control_variation])
            conv_prob = float(probs.get("conv_prob", 0.1))
            err_prob = float(probs.get("err_prob", 0.01))

            common_props = {
                "synthetic_test_traffic": True,
                "simulator": True,
                "source": "synthetic_test_harness",
                "scenario": scenario,
                "seed": seed,
                "batch_id": batch_id,
                "observed_flagsmith_variation": observed_var,
            }
            exp_event_id = f"sim_{batch_id}_{flag.key}_{idx}_exp"
            if exp_event_id in existing_ids:
                deduplicated_skipped += 1
                continue

            ctx.session.add(
                ExperimentEvent(
                    event_id=exp_event_id,
                    experiment_id=experiment.id,
                    flag_key=flag.key,
                    environment_name=env_name,
                    variation_key=observed_var,
                    event_type=ExperimentEventType.FEATURE_VARIATION_ASSIGNED,
                    user_identifier=uid,
                    session_key=f"sim_{batch_id}_{uid}",
                    properties=common_props,
                    occurred_at=now,
                )
            )
            exposures_recorded += 1

            roll = rng.random()
            if roll < err_prob:
                guardrail_metric = (
                    experiment.guardrail_metrics[0]
                    if experiment.guardrail_metrics
                    else "payment_failed"
                )
                err_enum = (
                    ExperimentEventType(guardrail_metric)
                    if guardrail_metric in {e.value for e in ExperimentEventType}
                    else ExperimentEventType.PAYMENT_FAILED
                )
                ctx.session.add(
                    ExperimentEvent(
                        event_id=f"sim_{batch_id}_{flag.key}_{idx}_err",
                        experiment_id=experiment.id,
                        flag_key=flag.key,
                        environment_name=env_name,
                        variation_key=observed_var,
                        event_type=err_enum,
                        user_identifier=uid,
                        session_key=f"sim_{batch_id}_{uid}",
                        properties=common_props,
                        occurred_at=now,
                    )
                )
                guardrail_errors_recorded += 1
            elif roll < err_prob + conv_prob:
                primary_enum = (
                    ExperimentEventType(experiment.primary_metric)
                    if experiment.primary_metric in {e.value for e in ExperimentEventType}
                    else ExperimentEventType.CHECKOUT_COMPLETED
                )
                ctx.session.add(
                    ExperimentEvent(
                        event_id=f"sim_{batch_id}_{flag.key}_{idx}_conv",
                        experiment_id=experiment.id,
                        flag_key=flag.key,
                        environment_name=env_name,
                        variation_key=observed_var,
                        event_type=primary_enum,
                        user_identifier=uid,
                        session_key=f"sim_{batch_id}_{uid}",
                        numeric_value=149.0,
                        properties=common_props,
                        occurred_at=now,
                    )
                )
                conversions_recorded += 1

        await ctx.session.commit()
        execution_logs.append(
            f"Recorded exposures={exposures_recorded}, conversions={conversions_recorded}, "
            f"guardrail_errors={guardrail_errors_recorded}, deduplicated={deduplicated_skipped}; "
            f"assignments={observed_assignments}"
        )

        native_refresh: dict[str, Any] | None = None
        native_results: dict[str, Any] | None = None
        if experiment.flagsmith_experiment_id:
            try:
                native_refresh = await client.refresh_experiment_results(
                    env_key, experiment.flagsmith_experiment_id
                )
                native_results = await client.get_experiment_results(
                    env_key, experiment.flagsmith_experiment_id
                )
                execution_logs.append(
                    f"Flagsmith native results refreshed for exp #{experiment.flagsmith_experiment_id}."
                )
            except FlagsmithAPIError as exc:
                native_results = {"available": False, "error": exc.message}
                execution_logs.append(f"Flagsmith native refresh failed: {exc.message}")

        return {
            "synthetic_test_traffic": True,
            "scenario": scenario,
            "seed": seed,
            "start_index": start_index,
            "batch_id": batch_id,
            "experiment_id": str(experiment.id),
            "flag_key": flag.key,
            "environment_name": env_name,
            "identities_evaluated": exposures_recorded,
            "deduplicated_skipped": deduplicated_skipped,
            "observed_flagsmith_assignments": observed_assignments,
            "conversions_recorded": conversions_recorded,
            "guardrail_errors_recorded": guardrail_errors_recorded,
            "execution_logs": execution_logs,
            "flagsmith_native_refresh": native_refresh,
            "flagsmith_native_results": native_results,
        }

    @staticmethod
    async def get_command_center_summary(
        session: AsyncSession,
        *,
        app: FastAPI | None,
    ) -> dict[str, Any]:
        """Build complete Flagsmith Command Center payload backed strictly by DB + live Flagsmith."""
        catalog = await FlagsmithGovernanceService.get_catalog_report(session)
        active_exps = list(
            (await session.execute(select(Experiment).order_by(Experiment.updated_at.desc()).limit(50)))
            .scalars()
            .all()
        )
        pending_approvals = list(
            (
                await session.execute(
                    select(Approval)
                    .where(Approval.status == ApprovalStatus.PENDING)
                    .order_by(Approval.created_at.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        active_rollouts = list(
            (
                await session.execute(
                    select(ProgressiveRollout)
                    .where(
                        ProgressiveRollout.status.in_(
                            [
                                ProgressiveRolloutStatus.OBSERVING,
                                ProgressiveRolloutStatus.AWAITING_APPROVAL,
                                ProgressiveRolloutStatus.PAUSED,
                            ]
                        )
                    )
                    .order_by(ProgressiveRollout.updated_at.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        recent_mutations = list(
            (
                await session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.resource_type.in_(["feature_flag", "experiment", "progressive_rollout"])
                    )
                    .order_by(AuditLog.created_at.desc())
                    .limit(40)
                )
            )
            .scalars()
            .all()
        )

        # Evaluate capability flags cleanly with safe fallbacks
        capability_states = [
            await FlagsmithGovernanceService.evaluate_capability_flag(
                app=app, session=session, flag_key=k
            )
            for k in ("enable_ai_pr_review", "enable_progressive_delivery")
        ]

        cfg = get_settings()
        return {
            "project_id": cfg.flagsmith_project_id_int,
            "environments": ["development", "staging", "production"],
            "cli": detect_cli_capabilities(),
            "metrics": {
                "catalog_flags_total": len(catalog),
                "provisioned_in_flagsmith": sum(1 for c in catalog if c["registered_in_flagsmith"]),
                "flags_with_warnings": sum(1 for c in catalog if c["cleanup_recommendations"]),
                "stale_flags": sum(1 for c in catalog if c["stale"]),
                "active_experiments": sum(1 for e in active_exps if e.status == ExperimentStatus.RUNNING),
                "total_experiments": len(active_exps),
                "pending_approvals": len(pending_approvals),
                "rollouts_in_progress": len(active_rollouts),
            },
            "catalog": catalog,
            "capability_flags": capability_states,
            "targeting_presets": TARGETING_PRESETS,
            "experiments": [
                {
                    "id": str(e.id),
                    "name": e.name,
                    "hypothesis": e.hypothesis,
                    "environment_name": e.environment_name,
                    "status": e.status.value,
                    "analytics_mode": e.analytics_mode.value,
                    "flagsmith_experiment_id": e.flagsmith_experiment_id,
                    "control_variation": e.control_variation,
                    "treatment_variation": e.treatment_variation,
                    "traffic_allocation": e.traffic_allocation,
                    "primary_metric": e.primary_metric,
                    "guardrail_metrics": e.guardrail_metrics,
                    "latest_bayesian_result": e.latest_bayesian_result,
                    "latest_recommendation": (
                        e.latest_recommendation.value if e.latest_recommendation else None
                    ),
                }
                for e in active_exps
            ],
            "pending_approvals": [
                {
                    "id": str(a.id),
                    "title": a.title,
                    "action_type": a.action_type,
                    "environment_name": a.environment_name,
                    "policy_category": a.policy_category.value,
                    "status": a.status.value,
                    "rationale": a.rationale,
                    "expected_precondition": a.expected_precondition,
                    "action_payload": a.action_payload,
                    "created_at": a.created_at.isoformat(),
                }
                for a in pending_approvals
            ],
            "recent_executions": [
                {
                    "id": str(m.id),
                    "event_action": m.event_action,
                    "resource_type": m.resource_type,
                    "resource_id": m.resource_id,
                    "environment_name": m.environment_name,
                    "status": m.status.value,
                    "policy_category": m.policy_category.value,
                    "approval_id": str(m.approval_id) if m.approval_id else None,
                    "created_at": m.created_at.isoformat(),
                }
                for m in recent_mutations
            ],
        }
