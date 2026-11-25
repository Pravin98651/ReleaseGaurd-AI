"""Tests for Phase 2 resilience: Gemini retries/timeouts, context-aware patching, Retry-After, and error propagation."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.agents.gemini_client import (
    GeminiClient,
    GeminiConfigurationError,
    dict_contents_to_genai,
    extract_response_parts,
)
from app.agents.specialists.experiment_agent import (
    build_experiment_plan_draft,
    canonicalize_primary_metric,
)
from app.agents.specialists.pr_agent import generate_context_aware_flag_patch
from app.integrations.flagsmith_client import FlagsmithAPIError, FlagsmithClient
from app.integrations.github_client import GitHubAPIError, GitHubClient
from app.models import FlagType
from app.services.patch_validator import PatchValidator


@pytest.mark.asyncio
async def test_gemini_client_exponential_backoff_retry_and_fail_fast() -> None:
    attempts = {"count": 0}

    class _FlakyRawClient:
        def __init__(self) -> None:
            self.models = SimpleNamespace(generate_content=self._call)

        def _call(self, *, model: str, contents: Any, config: Any) -> Any:
            assert config.automatic_function_calling.disable is True
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: rate limit")
            return SimpleNamespace(text="Recovered OK", function_calls=[], candidates=[])

    client = GeminiClient(
        raw_client=_FlakyRawClient(),
        max_retries=3,
        retry_delay_base=0.0,
        timeout_seconds=5.0,
    )
    resp = await client.generate_content(
        contents="ping",
        system_instruction="sys",
    )
    assert resp.text == "Recovered OK"
    assert attempts["count"] == 3

    # Non-retryable 400 INVALID_ARGUMENT fails immediately without retrying
    bad_attempts = {"count": 0}

    class _InvalidArgClient:
        def __init__(self) -> None:
            self.models = SimpleNamespace(generate_content=self._call)

        def _call(self, **_kwargs: Any) -> Any:
            bad_attempts["count"] += 1
            raise RuntimeError("400 INVALID_ARGUMENT: bad schema")

    bad_client = GeminiClient(
        raw_client=_InvalidArgClient(),
        max_retries=3,
        retry_delay_base=0.0,
    )
    with pytest.raises(RuntimeError, match="Gemini API call failed"):
        await bad_client.generate_content(contents="ping", system_instruction="sys")
    assert bad_attempts["count"] == 1

    # Timeout triggers retry exhaustion cleanly
    class _SlowAsyncClient:
        def __init__(self) -> None:
            self.models = SimpleNamespace(generate_content=self._slow_call)

        async def _slow_call(self, **_kwargs: Any) -> Any:
            await asyncio.sleep(2.0)
            return SimpleNamespace(text="late")

    slow_client = GeminiClient(
        raw_client=_SlowAsyncClient(),
        max_retries=0,
        timeout_seconds=1.0,
    )
    with pytest.raises(RuntimeError, match="Gemini API call failed"):
        await slow_client.generate_content(contents="ping", system_instruction="sys")

    unconfigured = GeminiClient(api_key="", raw_client=None)
    with pytest.raises(GeminiConfigurationError):
        await unconfigured.generate_content(contents="ping", system_instruction="sys")


@pytest.mark.asyncio
async def test_context_aware_patch_generation_typescript_and_path_traversal_fallback() -> None:
    existing_tsx = (
        'import React from "react";\n\n'
        "export function CheckoutButton({ onPay }: { onPay: () => void }) {\n"
        '  return <button onClick={onPay}>Express Pay v2</button>;\n'
        "}\n"
    )
    wrapped_tsx = (
        'import React from "react";\n'
        'import flagsmith from "@flagsmith/flagsmith";\n\n'
        "export function CheckoutButton({ onPay }: { onPay: () => void }) {\n"
        '  const v2Enabled = (() => { try { return flagsmith.hasFeature("checkout_v2"); } catch { return false; } })();\n'
        "  if (!v2Enabled) {\n"
        '    return <button onClick={onPay}>Standard Checkout v1</button>;\n'
        "  }\n"
        '  return <button onClick={onPay}>Express Pay v2</button>;\n'
        "}\n"
    )
    llm_payload = {
        "title": "feat(flags): gate CheckoutButton behind checkout_v2",
        "description": "Wraps React checkout component with @flagsmith/flagsmith safe fallback.",
        "files_diff": [
            {
                "file_path": "components/CheckoutButton.tsx",
                "action": "modify",
                "after_code": wrapped_tsx,
            }
        ],
        "generated_tests": [
            {
                "file_path": "tests/test_checkout_v2_flag.py",
                "content": "def test_placeholder() -> None:\n    assert True\n",
            }
        ],
    }

    fake_genai = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **_kwargs: SimpleNamespace(
                text=json.dumps(llm_payload), function_calls=[], candidates=[]
            )
        )
    )
    gemini = GeminiClient(raw_client=fake_genai, retry_delay_base=0.0)
    patch = await generate_context_aware_flag_patch(
        flag_key="checkout_v2",
        flag_type=FlagType.STANDARD,
        target_files=["components/CheckoutButton.tsx"],
        base_files={"components/CheckoutButton.tsx": existing_tsx},
        files_data=[{"filename": "components/CheckoutButton.tsx", "patch": "@@ -1 +1 @@\n+Express Pay v2"}],
        pr_title="Add CheckoutButton v2",
        repo_full_name="acme/novashop",
        pr_number=7,
        gemini_client=gemini,
    )
    assert patch["generation_mode"] == "gemini_context_aware"
    assert patch["files_diff"][0]["file_path"] == "components/CheckoutButton.tsx"
    assert patch["files_diff"][0]["action"] == "modify"
    assert "@flagsmith/flagsmith" in patch["files_diff"][0]["after_code"]
    assert "--- a/components/CheckoutButton.tsx" in patch["files_diff"][0]["unified_diff"]

    report = PatchValidator.validate(
        files_diff=patch["files_diff"],
        generated_tests=patch["generated_tests"],
        base_files={"components/CheckoutButton.tsx": existing_tsx},
    )
    assert report["validation_status"] == "passed"

    # Path traversal proposed by LLM must be rejected and safely fall back to template
    unsafe_genai = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **_kwargs: SimpleNamespace(
                text=json.dumps(
                    {
                        "title": "bad",
                        "description": "bad",
                        "files_diff": [{"file_path": "../../etc/passwd", "action": "modify", "after_code": "pwned"}],
                        "generated_tests": [],
                    }
                ),
                function_calls=[],
                candidates=[],
            )
        )
    )
    fallback_patch = await generate_context_aware_flag_patch(
        flag_key="checkout_v2",
        flag_type=FlagType.STANDARD,
        target_files=["app/checkout.py"],
        base_files={"app/checkout.py": "def run():\n    return 1\n"},
        gemini_client=GeminiClient(raw_client=unsafe_genai, retry_delay_base=0.0),
    )
    assert fallback_patch["generation_mode"] == "template_fallback"
    assert fallback_patch["files_diff"][0]["file_path"] == "app/checkout.py"


@pytest.mark.asyncio
async def test_github_and_flagsmith_retry_after_rate_limit_handling() -> None:
    gh_calls = {"n": 0}

    def gh_handler(request: httpx.Request) -> httpx.Response:
        gh_calls["n"] += 1
        if gh_calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, json={"message": "rate limit"})
        return httpx.Response(200, json=[{"name": "main", "commit": {"sha": "abc"}, "protected": True}])

    gh = GitHubClient(token="ghp_test", max_retries=2, retry_delay_base=0.0, transport=httpx.MockTransport(gh_handler))
    branches = await gh.list_branches("acme", "app")
    assert len(branches) == 1
    assert gh_calls["n"] == 2

    # Excessively long Retry-After (>10s) fails fast with explicit rate-limit error
    gh_long = GitHubClient(
        token="ghp_test",
        max_retries=2,
        retry_delay_base=0.0,
        transport=httpx.MockTransport(lambda _r: httpx.Response(429, headers={"Retry-After": "120"})),
    )
    with pytest.raises(GitHubAPIError, match="Retry-After"):
        await gh_long.list_branches("acme", "app")

    fs_calls = {"n": 0}

    def fs_handler(_request: httpx.Request) -> httpx.Response:
        fs_calls["n"] += 1
        if fs_calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, json={"detail": "throttled"})
        return httpx.Response(200, json=[{"id": 1, "name": "Demo", "organisation": 1}])

    fs = FlagsmithClient(org_token="fs_tok", max_retries=2, retry_delay_base=0.0, transport=httpx.MockTransport(fs_handler))
    projects = await fs.list_projects()
    assert len(projects) == 1
    assert fs_calls["n"] == 2

    fs_long = FlagsmithClient(
        org_token="fs_tok",
        max_retries=2,
        retry_delay_base=0.0,
        transport=httpx.MockTransport(lambda _r: httpx.Response(429, headers={"Retry-After": "60"})),
    )
    with pytest.raises(FlagsmithAPIError, match="Retry-After"):
        await fs_long.list_projects()


def test_gemini_thought_signature_roundtrip() -> None:
    """Ensure extract_response_parts & dict_contents_to_genai preserve Gemini 3.x thought_signature."""
    from google.genai import types

    sig_bytes = b"\x01\x02thought-sig-xyz\x99"
    fake_resp = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(text="internal reasoning", thought=True, function_call=None, thought_signature=None),
                        SimpleNamespace(
                            text=None,
                            thought=False,
                            function_call=SimpleNamespace(name="flagsmith_list_environments", args={"project_id": 56055}),
                            thought_signature=sig_bytes,
                        ),
                    ]
                )
            )
        ],
        function_calls=[],
        text=None,
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15),
    )

    text, fcs, _usage = extract_response_parts(fake_resp)
    assert text == ""  # internal thought-only text excluded from visible answer
    assert len(fcs) == 1
    assert fcs[0]["name"] == "flagsmith_list_environments"
    assert isinstance(fcs[0]["thought_signature"], str)

    contents = [
        {"role": "user", "parts": [{"text": "Inspect environments"}]},
        {"role": "model", "parts": [{"function_call": fcs[0]}]},
        {"role": "user", "parts": [{"function_response": {"name": "flagsmith_list_environments", "response": {"ok": True}}}]},
    ]
    rebuilt = dict_contents_to_genai(contents)
    assert len(rebuilt) == 3
    model_parts = rebuilt[1].parts
    assert model_parts is not None and len(model_parts) == 1
    assert isinstance(model_parts[0], types.Part)
    assert model_parts[0].function_call is not None
    assert model_parts[0].function_call.name == "flagsmith_list_environments"
    assert model_parts[0].thought_signature == sig_bytes


@pytest.mark.asyncio
async def test_experiment_plan_canonicalizes_conversion_metric_aliases() -> None:
    """Ensure Gemini metric prose ('conversion_rate', 'checkout_success_rate') normalizes to checkout_completed."""
    assert canonicalize_primary_metric("conversion_rate") == "checkout_completed"
    assert canonicalize_primary_metric("checkout_success_rate") == "checkout_completed"
    assert canonicalize_primary_metric("custom_signup_event") == "custom_signup_event"

    fake_genai = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **_kwargs: SimpleNamespace(
                text=json.dumps(
                    {
                        "name": "checkout_v2_test",
                        "hypothesis": "Express improves checkout",
                        "control_variation": "control",
                        "treatment_variation": "treatment",
                        "traffic_allocation": {"control": 50, "treatment": 50},
                        "primary_metric": "checkout_success_rate",
                        "guardrail_metrics": ["payment_failed", "checkout_abandoned"],
                    }
                ),
                function_calls=[],
                candidates=[],
            )
        )
    )
    draft = await build_experiment_plan_draft(
        prompt="Compare conversion rate on checkout_v2",
        flag_key="checkout_v2",
        environment_name="development",
        gemini_client=GeminiClient(raw_client=fake_genai, retry_delay_base=0.0),
    )
    assert draft["primary_metric"] == "checkout_completed"
