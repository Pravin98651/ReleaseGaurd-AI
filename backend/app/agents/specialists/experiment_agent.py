"""Module 3 specialist: Experiments, Bayesian Analysis, Recommendations, and Incident Investigation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agents.gemini_client import NON_OVERRIDABLE_INJECTION_GUARD, GeminiClient
from app.analytics.recommendations import RecommendationResult
from app.core.security import wrap_untrusted

logger = logging.getLogger("releaseguard.experiment_agent")

EXPERIMENT_AGENT_SYSTEM_PROMPT = f"""
You are `experiment_agent`, ReleaseGuard AI's Experimentation, Bayesian Analytics & Release Governance Specialist.

{NON_OVERRIDABLE_INJECTION_GUARD}

YOUR RESPONSIBILITIES:
1. Probe Flagsmith experimentation capability (`flagsmith_probe_experiment_capability`) and operate transparently in `flagsmith_native` or `tracked_event_fallback` mode.
2. Convert natural-language experiment goals into structured experiment plans (`create_experiment_plan`).
3. Run deterministic Python Beta-Binomial Bayesian analyses (`compute_bayesian_experiment_analysis`) and deterministic 5-state release recommendations (`evaluate_release_recommendation`). NEVER fabricate statistics — always cite exact numbers returned by the analytics tools.
4. Investigate error/abandonment spikes (`investigate_release_incident`) with strict adherence to `correlation_not_causation=True` and variation-stratified confounding checks.
5. Propose production rollout expansions, reductions, or emergency rollbacks via `propose_release_action` so a Category C approval is queued cleanly for human sign-off.
""".strip()


async def build_experiment_plan_draft(
    *,
    prompt: str,
    flag_key: str,
    environment_name: str,
    gemini_client: GeminiClient | None = None,
) -> dict[str, Any]:
    """Parse NL experiment request into structured draft parameters (Gemini + deterministic fallback)."""
    # Deterministic parsing fallback
    env_clean = environment_name.strip().lower()
    pct_match = re.search(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b", prompt)
    if pct_match and int(pct_match.group(1)) + int(pct_match.group(2)) == 100:
        alloc = {"control": int(pct_match.group(1)), "treatment": int(pct_match.group(2))}
    else:
        alloc = {"control": 50, "treatment": 50}

    draft: dict[str, Any] = {
        "name": f"{flag_key} ({env_clean}) Bayesian Experiment",
        "hypothesis": (
            f"Enabling `{flag_key}` treatment variation in `{env_clean}` improves "
            "`checkout_completed` conversion rate without breaching `payment_failed` guardrails."
        ),
        "control_variation": "control",
        "treatment_variation": "treatment",
        "traffic_allocation": alloc,
        "primary_metric": "checkout_completed",
        "guardrail_metrics": ["payment_failed", "checkout_abandoned"],
    }

    if gemini_client is not None and gemini_client.is_configured:
        llm_prompt = (
            "Convert this natural-language experiment request into STRICT JSON with keys: "
            "name (str), hypothesis (str), control_variation (str), treatment_variation (str), "
            "traffic_allocation (dict mapping control/treatment to ints summing to 100), "
            "primary_metric (str), guardrail_metrics (list[str]).\n"
            f"Target flag_key={flag_key}, environment={env_clean}.\n"
            f"{wrap_untrusted('operator_prompt', {'flag_key': flag_key}, prompt, max_chars=2000)}"
        )
        try:
            resp = await gemini_client.generate_content(
                contents=[{"role": "user", "parts": [{"text": llm_prompt}]}],
                system_instruction=EXPERIMENT_AGENT_SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
            )
            raw_text = getattr(resp, "text", None) or ""
            if raw_text:
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    if parsed.get("name"):
                        draft["name"] = str(parsed["name"])[:255]
                    if parsed.get("hypothesis"):
                        draft["hypothesis"] = str(parsed["hypothesis"])
                    if parsed.get("control_variation"):
                        draft["control_variation"] = str(parsed["control_variation"])
                    if parsed.get("treatment_variation"):
                        draft["treatment_variation"] = str(parsed["treatment_variation"])
                    if isinstance(parsed.get("traffic_allocation"), dict):
                        vals = {
                            str(k): int(v) for k, v in parsed["traffic_allocation"].items()
                        }
                        if sum(vals.values()) == 100:
                            draft["traffic_allocation"] = vals
                    if parsed.get("primary_metric"):
                        draft["primary_metric"] = str(parsed["primary_metric"])
                    if isinstance(parsed.get("guardrail_metrics"), list) and parsed["guardrail_metrics"]:
                        draft["guardrail_metrics"] = [str(m) for m in parsed["guardrail_metrics"]]
        except (RuntimeError, ValueError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("Gemini experiment plan refinement skipped: %s", exc)

    return draft


async def generate_recommendation_narrative(
    *,
    rec: RecommendationResult,
    flag_key: str,
    environment_name: str,
    gemini_client: GeminiClient | None = None,
) -> str:
    """Produce plain-English narrative grounded strictly in deterministic Bayesian numbers."""
    deterministic = (
        f"Recommendation: {rec.recommendation.value} for `{flag_key}` in `{environment_name}`. "
        f"{rec.rationale} {rec.confidence_summary}"
    )
    if gemini_client is None or not gemini_client.is_configured:
        return deterministic

    prompt = (
        "Summarize this deterministic Bayesian release recommendation in 2-3 clear sentences for a "
        "release manager. Use ONLY the verified numbers below; do not invent any metrics.\n\n"
        f"Status: {rec.recommendation.value}\n"
        f"Flag: {flag_key} | Environment: {environment_name}\n"
        f"Confidence: {rec.confidence_summary}\n"
        f"Rationale: {rec.rationale}\n"
        f"Preconditions: {'; '.join(rec.preconditions)}\n"
        f"Consequences: {'; '.join(rec.potential_consequences)}"
    )
    try:
        resp = await gemini_client.generate_content(
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            system_instruction=EXPERIMENT_AGENT_SYSTEM_PROMPT,
            temperature=0.1,
        )
        raw_text = getattr(resp, "text", None) or ""
        if raw_text and raw_text.strip():
            return raw_text.strip()
    except (RuntimeError, ValueError, TypeError) as exc:
        logger.warning("Gemini narrative generation fallback used: %s", exc)
    return deterministic
