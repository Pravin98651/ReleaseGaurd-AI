"""Natural-language Feature Request to Flagged Rollout Planner (Phase 2).

Converts a developer's natural-language feature request into a structured,
flag-guarded implementation & progressive rollout plan backed by ReleaseOrchestrator.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.gemini_client import GeminiClient
from app.core.security import wrap_untrusted
from app.models import (
    ActorType,
    AuditStatus,
    PolicyCategory,
    Release,
    ReleaseType,
    Repository,
    RiskLevel,
)
from app.services.policy_engine import PolicyEngine
from app.services.release_orchestrator import ReleaseOrchestrator


class FeatureRequestPlanResult(BaseModel):
    """Structured plan turning a feature request into a flagged rollout."""

    prompt: str
    feature_summary: str
    flag_required: bool = True
    flag_rationale: str
    recommended_flag_key: str
    flag_type: str = "MULTIVARIATE"
    control_variation: str = "control"
    treatment_variation: str = "treatment_v2"
    safe_default: dict[str, Any] = Field(
        default_factory=lambda: {"enabled": False, "value": "control"}
    )
    target_environments: list[str] = Field(
        default_factory=lambda: ["development", "staging", "production"]
    )
    implementation_points: list[dict[str, str]] = Field(default_factory=list)
    experiment_plan: dict[str, Any] = Field(default_factory=dict)
    rollout_strategy: list[str] = Field(default_factory=list)
    safety_guarantees: list[str] = Field(default_factory=list)
    evidence_class: str = "AI_INFERRED"
    release_id: str | None = None
    release_key: str | None = None
    next_steps: list[str] = Field(default_factory=list)


def _derive_flag_key(prompt: str) -> str:
    text = prompt.lower()
    if "recommendation" in text:
        return "personalized_recommendation_v2"
    if "checkout" in text:
        return "checkout_experience_v2"
    if "payment" in text:
        return "new_payment_flow_v2"
    if "search" in text:
        return "smart_product_search_v2"
    words = [w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2 and w not in {
        "add", "new", "the", "with", "and", "for", "against", "existing", "using",
        "protect", "feature", "flag", "compare", "gradually", "release", "performs", "better",
    }]
    slug = "_".join(words[:4]) or "feature_rollout"
    return f"{slug[:48]}_v2"


def _default_implementation_points(prompt: str) -> list[dict[str, str]]:
    text = prompt.lower()
    if "recommendation" in text:
        return [
            {
                "file_path": "demo-app/backend/app/features.py",
                "role": "Evaluate Flagsmith `recommendation_engine_v2` / proposed flag and fall back to deterministic popularity sort.",
            },
            {
                "file_path": "demo-app/frontend/components/NovaShopStorefront.tsx",
                "role": "Render control vs. personalized recommendation variant with Flagsmith JS SDK.",
            },
        ]
    if "checkout" in text or "payment" in text:
        return [
            {
                "file_path": "demo-app/backend/app/checkout.py",
                "role": "Gate checkout/payment path behind Flagsmith flag with safe v1 fallback.",
            },
            {
                "file_path": "demo-app/frontend/components/CheckoutV2Express.tsx",
                "role": "Serve treatment UI only when Flagsmith variation resolves to treatment.",
            },
        ]
    return [
        {
            "file_path": "demo-app/backend/app/features.py",
            "role": "Wrap new feature execution path in Flagsmith evaluation with safe default fallback.",
        }
    ]


async def plan_feature_request_from_prompt(
    session: AsyncSession,
    *,
    prompt: str,
    user_id: uuid.UUID | None = None,
    repository_id: uuid.UUID | None = None,
    environment_name: str = "development",
    create_release: bool = True,
    gemini_client: GeminiClient | None = None,
) -> dict[str, Any]:
    """Plan a feature-flagged rollout from a natural-language feature request."""
    clean_prompt = prompt.strip()
    if len(clean_prompt) < 8:
        raise ValueError("Feature request prompt must be at least 8 characters.")

    repo: Repository | None = None
    if repository_id is not None:
        repo = await session.get(Repository, repository_id)
    else:
        repo = (await session.execute(select(Repository).limit(1))).scalar_one_or_none()

    env_norm = (environment_name or "development").strip().lower()
    wants_comparison = any(
        kw in clean_prompt.lower()
        for kw in ("compare", "experiment", "a/b", "ab test", "performs better", "versus", "vs")
    )
    flag_key = _derive_flag_key(clean_prompt)
    treatment_var = "personalized_v2" if "personalized" in clean_prompt.lower() else "treatment_v2"
    flag_type = "MULTIVARIATE" if wants_comparison else "STANDARD"
    impl_points = _default_implementation_points(clean_prompt)

    summary = f"Flag-protected rollout plan for: {clean_prompt[:140]}"
    flag_rationale = (
        "Feature flag isolates new behavior behind a safe disabled default, enables "
        "controlled control-vs-treatment experimentation, and allows instant rollback."
    )

    if gemini_client is not None and gemini_client.is_configured:
        llm_prompt = (
            "Convert this feature request into STRICT JSON with keys: "
            "feature_summary (str), recommended_flag_key (snake_case str), "
            "flag_rationale (str), treatment_variation (snake_case str), "
            "flag_type ('MULTIVARIATE' or 'STANDARD').\n"
            f"Repository: {repo.full_name if repo else 'ReleaseGuard-AI'}\n"
            f"{wrap_untrusted('feature_request', {'env': env_norm}, clean_prompt, max_chars=2000)}"
        )
        try:
            resp = await gemini_client.generate_content(
                contents=[{"role": "user", "parts": [{"text": llm_prompt}]}],
                temperature=0.1,
                response_mime_type="application/json",
            )
            text = (resp.text or "").strip()
            if text:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    summary = str(parsed.get("feature_summary") or summary)[:240]
                    raw_key = str(parsed.get("recommended_flag_key") or flag_key)
                    flag_key = re.sub(r"[^a-z0-9_]", "_", raw_key.lower()).strip("_") or flag_key
                    flag_rationale = str(parsed.get("flag_rationale") or flag_rationale)[:400]
                    raw_trt = str(parsed.get("treatment_variation") or treatment_var)
                    treatment_var = re.sub(r"[^a-z0-9_]", "_", raw_trt.lower()).strip("_") or treatment_var
                    if parsed.get("flag_type") in ("STANDARD", "MULTIVARIATE"):
                        flag_type = str(parsed["flag_type"])
        except Exception:  # noqa: BLE001
            pass

    plan = FeatureRequestPlanResult(
        prompt=clean_prompt,
        feature_summary=summary,
        flag_required=True,
        flag_rationale=flag_rationale,
        recommended_flag_key=flag_key,
        flag_type=flag_type,
        control_variation="control",
        treatment_variation=treatment_var,
        safe_default={"enabled": False, "value": "control"},
        target_environments=[env_norm, "staging", "production"]
        if env_norm == "development"
        else [env_norm, "production"],
        implementation_points=impl_points,
        experiment_plan={
            "enabled": wants_comparison,
            "control_variation": "control",
            "treatment_variation": treatment_var,
            "traffic_allocation": {"control": 50, treatment_var: 50},
            "primary_metric": "checkout_completed",
            "guardrail_metrics": ["payment_failed", "checkout_abandoned"],
            "min_exposures_per_arm": 100,
        },
        rollout_strategy=[
            "0% (safe fallback default in all environments)",
            f"50/50 experiment split in {env_norm} via Flagsmith",
            "Bayesian Beta-Binomial evaluation (min 100 unique participants/arm)",
            "Category C human approval required before production promotion",
            "Progressive canary rollout: 1% -> 5% -> 10% -> 25% -> 50% -> 100%",
        ],
        safety_guarantees=[
            "Never auto-merges GitHub PRs (Draft PRs only)",
            "Never mutates production without Category C non-self human approval",
            "Safe fallback default (`enabled=False`) when Flagsmith is unreachable",
            "Live state readback verification after every Flagsmith mutation",
        ],
        next_steps=[
            f"Create Flagsmith flag `{flag_key}` (disabled in production)",
            "Generate & validate feature-flagged Draft PR via `pr_agent`",
            "Run experiment & evaluate Bayesian posteriors before requesting Category C approval",
        ],
    )

    if create_release:
        release: Release = await ReleaseOrchestrator.create_release(
            session,
            title=f"AI Feature Rollout: {flag_key}",
            description=clean_prompt,
            release_type=ReleaseType.FEATURE,
            target_environment=env_norm,
            repository_id=repo.id if repo else None,
            owner_id=user_id,
            created_by_id=user_id,
            risk_level=RiskLevel.MEDIUM,
            target_rollout_percentage=100,
            ai_assist=False,
            allow_duplicate=True,
        )
        plan.release_id = str(release.id)
        plan.release_key = release.release_key

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER if user_id else ActorType.AGENT,
        actor_id=str(user_id) if user_id else "feature_request_planner",
        policy_category=PolicyCategory.A,
        event_action="release.planned_from_prompt",
        resource_type="release",
        resource_id=plan.release_id or flag_key,
        environment_name=env_norm,
        status=AuditStatus.SUCCESS,
        details={
            "recommended_flag_key": flag_key,
            "flag_type": flag_type,
            "release_key": plan.release_key,
        },
    )
    await session.commit()
    return plan.model_dump(mode="json")
