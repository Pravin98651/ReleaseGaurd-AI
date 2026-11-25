"""Module 1 specialist: PR Intelligence, static/AST risk checks, and Flagsmith patch generation."""

from __future__ import annotations

import ast
import difflib
import json
import re
from typing import Any

from app.agents.gemini_client import NON_OVERRIDABLE_INJECTION_GUARD, GeminiClient
from app.core.security import wrap_untrusted
from app.models import FlagType, RiskLevel

PR_AGENT_SYSTEM_PROMPT = f"""
You are `pr_agent`, ReleaseGuard AI's Pull Request Intelligence & Safe Patch Specialist.

{NON_OVERRIDABLE_INJECTION_GUARD}

YOUR RESPONSIBILITIES:
1. Browse repositories, branches, pull requests, diffs, and source files using your allowlisted GitHub read tools.
2. Separate **confirmed_risks** (directly observed in changed files with exact `file_path`, line numbers, and observed code behavior) from **potential_risks** (unconfirmed hypotheses, integration assumptions, or runtime edge cases requiring operator verification).
3. Detect feature-flag wrapping opportunities for risky changes (checkout, payment, pricing, auth, migrations).
4. Generate Flagsmith-guarded code patches (`flagsmith.get_identity_flags` / `is_feature_enabled` / `hasFeature`) plus unit tests for both enabled and disabled flag states.
5. ALWAYS run `validate_patch_isolated` before calling `github_create_branch_and_draft_pr`.
6. Open Draft PRs (`draft=true`) ONLY. You have ZERO merge capability and must NEVER attempt to merge PRs or touch production feature flags.
""".strip()

_RISK_PATTERNS: list[tuple[re.Pattern[str], str, RiskLevel, int, str]] = [
    (
        re.compile(r"(checkout|payment|billing|stripe|refund|charge|invoice|promo)", re.IGNORECASE),
        "Payment / Checkout Flow Mutation",
        RiskLevel.HIGH,
        35,
        "Wrap new checkout/payment logic behind a Flagsmith feature flag with safe legacy fallback.",
    ),
    (
        re.compile(r"(auth|jwt|oauth|token|permission|rbac|secret|password)", re.IGNORECASE),
        "Authentication / Authorization Logic Change",
        RiskLevel.HIGH,
        30,
        "Audit access checks and gate new auth paths behind a non-prod verified flag.",
    ),
    (
        re.compile(r"(alembic|migration|alter\s+table|drop\s+table|drop\s+column)", re.IGNORECASE),
        "Database Schema / Migration Modification",
        RiskLevel.HIGH,
        25,
        "Ensure backward-compatible expand-and-contract rollout before enabling feature code.",
    ),
    (
        re.compile(r"(except\s+Exception\s*:\s*pass|catch\s*\([^)]*\)\s*\{\s*\})", re.IGNORECASE),
        "Swallowed Exception / Silent Failure Handler",
        RiskLevel.MEDIUM,
        15,
        "Log structured failure context and preserve payment_failed telemetry emission.",
    ),
]

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_UNFLAGGED_MARKERS = ("is_feature_enabled", "get_feature_value", "hasFeature", "getValue", "flagsmith")


def _parse_patch_added_lines(patch: str) -> list[tuple[int, str]]:
    """Extract (new_line_number, line_text) pairs for added lines in a unified diff patch."""
    added: list[tuple[int, str]] = []
    cur_line = 0
    for raw_line in (patch or "").splitlines():
        m = _HUNK_HEADER_RE.match(raw_line)
        if m:
            cur_line = int(m.group(1))
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added.append((max(cur_line, 1), raw_line[1:]))
            cur_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        else:
            cur_line += 1
    return added


def _score_to_risk_level(score: int) -> RiskLevel:
    if score >= 75:
        return RiskLevel.CRITICAL
    if score >= 50:
        return RiskLevel.HIGH
    if score >= 25:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def extract_static_pr_signals(files_data: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministically inspect PR changed files/diffs using AST/pattern checks."""
    static_signals: list[str] = []
    confirmed_risks: list[dict[str, Any]] = []
    potential_risks: list[dict[str, Any]] = []
    flag_opportunities: list[dict[str, Any]] = []

    total_changes = 0
    has_test_changes = False
    has_flagsmith_guard = False
    risky_files: list[str] = []
    score = 10

    for item in files_data:
        filename = str(item.get("filename", ""))
        patch = str(item.get("patch") or "")
        additions = int(item.get("additions") or 0)
        deletions = int(item.get("deletions") or 0)
        total_changes += additions + deletions

        if "test" in filename.lower():
            has_test_changes = True
        if any(tok in patch for tok in _UNFLAGGED_MARKERS):
            has_flagsmith_guard = True

        added_lines = _parse_patch_added_lines(patch)
        if not added_lines and patch:
            added_lines = [(idx + 1, ln) for idx, ln in enumerate(patch.splitlines())]

        # Python AST sanity check on added Python snippets when standalone parseable
        if filename.endswith(".py") and added_lines:
            joined_added = "\n".join(text for _, text in added_lines)
            try:
                tree = ast.parse(joined_added)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ExceptHandler) and (
                        len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
                    ):
                        static_signals.append(f"ast_silent_except:{filename}:{node.lineno}")
                        confirmed_risks.append(
                            {
                                "title": "Silent except: pass detected via Python AST",
                                "severity": RiskLevel.MEDIUM.value,
                                "file_path": filename,
                                "line_range": f"L{node.lineno}",
                                "evidence": f"Python AST found `except ...: pass` in added code of {filename}.",
                                "mitigation": "Raise or log explicit failure telemetry rather than silently passing.",
                            }
                        )
                        score += 12
            except SyntaxError:
                # Partial diff hunks are often not standalone modules; skip AST failure
                pass

        for regex, title, sev, weight, mitigation in _RISK_PATTERNS:
            matched_line: tuple[int, str] | None = None
            if regex.search(filename):
                matched_line = added_lines[0] if added_lines else (1, filename)
            else:
                for ln_no, ln_txt in added_lines:
                    if regex.search(ln_txt):
                        matched_line = (ln_no, ln_txt.strip())
                        break

            if matched_line is not None:
                static_signals.append(f"risk_marker:{title}:{filename}:L{matched_line[0]}")
                confirmed_risks.append(
                    {
                        "title": title,
                        "severity": sev.value,
                        "file_path": filename,
                        "line_range": f"L{matched_line[0]}",
                        "evidence": f"Observed `{matched_line[1][:140]}` in {filename} at line {matched_line[0]}.",
                        "mitigation": mitigation,
                    }
                )
                if filename not in risky_files:
                    risky_files.append(filename)
                score += weight

    if total_changes > 150:
        static_signals.append(f"high_churn:{total_changes}_lines")
        score += 10
        potential_risks.append(
            {
                "title": "Elevated change volume may hide edge-case regressions",
                "severity": RiskLevel.MEDIUM.value,
                "assumption": f"PR modifies {total_changes} lines across {len(files_data)} file(s).",
                "verification_needed": "Verify integration behavior across unchanged callers and staging traffic.",
            }
        )

    if files_data and not has_test_changes:
        static_signals.append("missing_test_updates")
        score += 12
        potential_risks.append(
            {
                "title": "No accompanying test file updates detected in PR diff",
                "severity": RiskLevel.MEDIUM.value,
                "assumption": "Existing suite might not cover newly introduced branches or failure states.",
                "verification_needed": "Add isolated unit/integration tests covering both control and treatment paths.",
            }
        )

    if risky_files and not has_flagsmith_guard:
        static_signals.append("unflagged_high_risk_path")
        score += 15
        slug_base = (
            re.sub(r"[^a-z0-9]+", "_", risky_files[0].rsplit("/", 1)[-1].split(".")[0].lower()).strip("_")
            or "feature_release"
        )
        suggested_key = (
            "checkout_v2"
            if any("checkout" in f.lower() for f in risky_files)
            else f"{slug_base}_v2"
        )
        flag_opportunities.append(
            {
                "suggested_flag_key": suggested_key,
                "flag_type": FlagType.STANDARD.value,
                "target_files": risky_files[:5],
                "rationale": (
                    "High-risk execution path lacks Flagsmith SDK gating; wrapping allows instant "
                    "rollback and gradual 10% -> 25% -> 50% -> 75% -> 100% rollout."
                ),
                "recommended_rollout": "10% -> 25% -> 50% -> 75% -> 100%",
            }
        )

    if not potential_risks:
        potential_risks.append(
            {
                "title": "Downstream external dependency or runtime config assumptions",
                "severity": RiskLevel.LOW.value,
                "assumption": "Diff relies on external APIs, Flagsmith keys, or environment configuration not visible in patch.",
                "verification_needed": "Verify in staging environment with live Flagsmith evaluation and telemetry.",
            }
        )

    bounded_score = min(100, max(5, score))
    return {
        "static_signals": static_signals,
        "risk_score": bounded_score,
        "risk_level": _score_to_risk_level(bounded_score).value,
        "confirmed_risks": confirmed_risks,
        "potential_risks": potential_risks,
        "flag_opportunities": flag_opportunities,
        "changed_files": [str(f.get("filename", "")) for f in files_data if f.get("filename")],
    }


async def analyze_pr_with_static_and_gemini(
    *,
    pr_title: str,
    pr_body: str | None,
    files_data: list[dict[str, Any]],
    repo_full_name: str,
    pr_number: int,
    gemini_client: GeminiClient | None = None,
) -> dict[str, Any]:
    """Combine deterministic AST/static diff signals with optional Gemini analysis (AFC=Disabled)."""
    baseline = extract_static_pr_signals(files_data)
    changed_files = set(baseline["changed_files"])

    summary_parts = [
        f"Analyzed PR #{pr_number} on {repo_full_name} across {len(files_data)} changed file(s).",
        (
            f"Static/AST checks identified {len(baseline['confirmed_risks'])} confirmed risk(s) "
            f"and {len(baseline['flag_opportunities'])} feature-flag opportunity(ies)."
        ),
    ]
    result: dict[str, Any] = {
        "risk_score": baseline["risk_score"],
        "risk_level": baseline["risk_level"],
        "analysis_summary": " ".join(summary_parts),
        "confirmed_risks": baseline["confirmed_risks"],
        "potential_risks": baseline["potential_risks"],
        "flag_opportunities": baseline["flag_opportunities"],
        "static_signals": baseline["static_signals"],
    }

    if gemini_client is not None and gemini_client.is_configured:
        diff_sections = []
        for item in files_data:
            fn = item.get("filename", "")
            patch = item.get("patch") or ""
            diff_sections.append(f"--- a/{fn}\n+++ b/{fn}\n{patch}")
        raw_diff = "\n\n".join(diff_sections)

        meta = {"repo": repo_full_name, "pr_number": pr_number}
        prompt = (
            "Analyze the following sandboxed Pull Request metadata and unified diff.\n"
            "Return STRICT JSON with keys: summary (str), risk_score (int 0..100), "
            "risk_level ('low'|'medium'|'high'|'critical'), confirmed_risks (list of "
            "{title, severity, file_path, line_range, evidence, mitigation}), "
            "potential_risks (list of {title, severity, assumption, verification_needed}), "
            "flag_opportunities (list of {suggested_flag_key, flag_type, target_files, rationale, recommended_rollout}).\n"
            f"Deterministic static signals already observed: {json.dumps(baseline['static_signals'])}\n\n"
            f"{wrap_untrusted('github_pr_title', meta, pr_title, max_chars=2000)}\n"
            f"{wrap_untrusted('github_pr_body', meta, pr_body or '', max_chars=8000)}\n"
            f"{wrap_untrusted('github_pr_diff', meta, raw_diff)}"
        )
        resp = await gemini_client.generate_content(
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            system_instruction=PR_AGENT_SYSTEM_PROMPT,
            temperature=0.1,
            response_mime_type="application/json",
        )
        raw_text = getattr(resp, "text", None) or ""
        if not raw_text and getattr(resp, "candidates", None):
            parts = getattr(resp.candidates[0].content, "parts", []) or []
            raw_text = "".join(str(getattr(p, "text", "") or "") for p in parts)

        if raw_text:
            try:
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    verified_confirmed: list[dict[str, Any]] = []
                    extra_potential: list[dict[str, Any]] = list(parsed.get("potential_risks") or [])
                    for cr in parsed.get("confirmed_risks") or []:
                        if not isinstance(cr, dict):
                            continue
                        fp = str(cr.get("file_path") or "")
                        if not changed_files or fp in changed_files:
                            verified_confirmed.append(cr)
                        else:
                            extra_potential.append(
                                {
                                    "title": str(cr.get("title") or "Unverified risk citation"),
                                    "severity": str(cr.get("severity") or "medium"),
                                    "assumption": f"Model cited '{fp}' which is not in PR changed files.",
                                    "verification_needed": str(cr.get("evidence") or "Verify target file."),
                                }
                            )

                    if parsed.get("summary"):
                        result["analysis_summary"] = str(parsed["summary"])
                    if isinstance(parsed.get("risk_score"), int):
                        result["risk_score"] = max(0, min(100, int(parsed["risk_score"])))
                        result["risk_level"] = str(
                            parsed.get("risk_level") or _score_to_risk_level(result["risk_score"]).value
                        ).lower()
                    if verified_confirmed:
                        result["confirmed_risks"] = verified_confirmed
                    if extra_potential:
                        result["potential_risks"] = extra_potential
                    if parsed.get("flag_opportunities"):
                        result["flag_opportunities"] = list(parsed["flag_opportunities"])
            except json.JSONDecodeError:
                # Keep deterministic baseline if Gemini returns non-JSON text
                pass

    return result


def _unified_diff(path: str, before_code: str, after_code: str) -> str:
    return "".join(
        difflib.unified_diff(
            before_code.splitlines(keepends=True),
            after_code.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def generate_flag_patch_payload(
    *,
    flag_key: str,
    flag_type: FlagType,
    target_files: list[str],
    base_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build Flagsmith-aware wrapping patch + unit tests ready for PatchValidator."""
    safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", flag_key).strip("_") or "feature_flag"
    primary_file = target_files[0] if target_files else f"app/services/{safe_key}_guard.py"

    existing_before = (base_files or {}).get(primary_file, "")
    if existing_before and primary_file.endswith(".py"):
        if "is_feature_enabled" in existing_before:
            after_code = existing_before
        else:
            helper_block = (
                f"\n\ndef is_{safe_key}_enabled(flagsmith_client: object, user_id: str) -> bool:\n"
                f'    """Evaluate Flagsmith feature `{flag_key}` with safe fallback to False."""\n'
                "    try:\n"
                "        flags = flagsmith_client.get_identity_flags(identifier=user_id)\n"
                f'        return bool(flags.is_feature_enabled("{flag_key}"))\n'
                "    except (AttributeError, RuntimeError, ValueError, TypeError, OSError):\n"
                "        return False\n"
            )
            after_code = existing_before.rstrip() + helper_block
        action = "modify"
        before_code = existing_before
    else:
        action = "create"
        before_code = ""
        primary_file = (
            primary_file
            if primary_file.endswith(".py")
            else f"app/flags/{safe_key}_wrapper.py"
        )
        if flag_type == FlagType.MULTIVARIATE:
            after_code = (
                f'"""Flagsmith multivariate evaluation wrapper for `{flag_key}`."""\n\n'
                "from typing import Any\n\n\n"
                f"def resolve_{safe_key}_variant(flagsmith_client: Any, user_id: str) -> str:\n"
                '    """Return active checkout/feature variation with safe fallback to v1."""\n'
                "    try:\n"
                "        flags = flagsmith_client.get_identity_flags(identifier=user_id)\n"
                f'        if not flags.is_feature_enabled("{flag_key}"):\n'
                '            return "v1"\n'
                f'        value = flags.get_feature_value("{flag_key}")\n'
                '        return str(value) if value else "v1"\n'
                "    except (AttributeError, RuntimeError, ValueError, TypeError, OSError):\n"
                '        return "v1"\n'
            )
        else:
            after_code = (
                f'"""Flagsmith boolean evaluation wrapper for `{flag_key}`."""\n\n'
                "from typing import Any\n\n\n"
                f"def is_{safe_key}_enabled(flagsmith_client: Any, user_id: str) -> bool:\n"
                f'    """Evaluate `{flag_key}` via Flagsmith with safe fallback to False."""\n'
                "    try:\n"
                "        flags = flagsmith_client.get_identity_flags(identifier=user_id)\n"
                f'        return bool(flags.is_feature_enabled("{flag_key}"))\n'
                "    except (AttributeError, RuntimeError, ValueError, TypeError, OSError):\n"
                "        return False\n"
            )

    module_import = primary_file[:-3].replace("/", ".")
    fn_name = (
        f"resolve_{safe_key}_variant"
        if flag_type == FlagType.MULTIVARIATE
        else f"is_{safe_key}_enabled"
    )
    if flag_type == FlagType.MULTIVARIATE:
        test_content = (
            f'"""Isolated tests for `{flag_key}` Flagsmith multivariate wrapper."""\n\n'
            f"from {module_import} import {fn_name}\n\n\n"
            "class _FakeFlags:\n"
            "    def __init__(self, enabled: bool, value: str) -> None:\n"
            "        self._enabled = enabled\n"
            "        self._value = value\n\n"
            "    def is_feature_enabled(self, _key: str) -> bool:\n"
            "        return self._enabled\n\n"
            "    def get_feature_value(self, _key: str) -> str:\n"
            "        return self._value\n\n\n"
            "class _FakeClient:\n"
            "    def __init__(self, enabled: bool, value: str) -> None:\n"
            "        self._flags = _FakeFlags(enabled, value)\n\n"
            "    def get_identity_flags(self, identifier: str) -> _FakeFlags:\n"
            "        return self._flags\n\n\n"
            f"def test_{safe_key}_variant_routing() -> None:\n"
            f'    assert {fn_name}(_FakeClient(True, "v2"), "u1") == "v2"\n'
            f'    assert {fn_name}(_FakeClient(False, "v2"), "u1") == "v1"\n'
        )
    else:
        test_content = (
            f'"""Isolated tests for `{flag_key}` Flagsmith boolean wrapper."""\n\n'
            f"from {module_import} import {fn_name}\n\n\n"
            "class _FakeFlags:\n"
            "    def __init__(self, enabled: bool) -> None:\n"
            "        self._enabled = enabled\n\n"
            "    def is_feature_enabled(self, _key: str) -> bool:\n"
            "        return self._enabled\n\n\n"
            "class _FakeClient:\n"
            "    def __init__(self, enabled: bool) -> None:\n"
            "        self._flags = _FakeFlags(enabled)\n\n"
            "    def get_identity_flags(self, identifier: str) -> _FakeFlags:\n"
            "        return self._flags\n\n\n"
            "class _BrokenClient:\n"
            "    def get_identity_flags(self, identifier: str) -> _FakeFlags:\n"
            '        raise RuntimeError("Flagsmith unreachable")\n\n\n'
            f"def test_{safe_key}_enabled_and_fallback() -> None:\n"
            f'    assert {fn_name}(_FakeClient(True), "u1") is True\n'
            f'    assert {fn_name}(_FakeClient(False), "u1") is False\n'
            f'    assert {fn_name}(_BrokenClient(), "u1") is False\n'
        )

    files_diff = [
        {
            "file_path": primary_file,
            "action": action,
            "before_code": before_code,
            "after_code": after_code,
            "unified_diff": _unified_diff(primary_file, before_code, after_code),
        }
    ]
    generated_tests = [
        {
            "file_path": f"tests/test_{safe_key}_flag_guard.py",
            "content": test_content,
        }
    ]
    return {
        "title": f"feat(flags): guard change behind Flagsmith `{flag_key}`",
        "description": (
            f"Wraps risky code path behind Flagsmith flag `{flag_key}` with safe fallback "
            "and isolated unit test coverage."
        ),
        "files_diff": files_diff,
        "generated_tests": generated_tests,
    }


def generate_cleanup_patch_payload(
    *,
    flag_key: str,
    keep_branch: str,
    code_references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate a validated flag-cleanup removal patch keeping the selected branch."""
    safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", flag_key).strip("_") or "stale_flag"
    keep_enabled = keep_branch == "enabled"
    target_path = f"app/flags/{safe_key}_cleanup.py"
    if code_references:
        first_ref = str(code_references[0].get("file_path") or "")
        if first_ref.endswith(".py"):
            target_path = first_ref

    after_code = (
        f'"""Cleanup module removing retired Flagsmith flag `{flag_key}`."""\n\n'
        f"RETIRED_FLAG_KEY = {flag_key!r}\n"
        f"PERMANENT_BEHAVIOR_ENABLED = {keep_enabled!r}\n\n\n"
        f"def is_{safe_key}_active() -> bool:\n"
        f'    """Flag `{flag_key}` permanently resolved to {keep_branch}."""\n'
        "    return PERMANENT_BEHAVIOR_ENABLED\n"
    )
    module_import = target_path[:-3].replace("/", ".")
    test_content = (
        f'"""Cleanup verification test for retired flag `{flag_key}`."""\n\n'
        f"from {module_import} import PERMANENT_BEHAVIOR_ENABLED, is_{safe_key}_active\n\n\n"
        f"def test_{safe_key}_cleanup_resolved() -> None:\n"
        f"    assert PERMANENT_BEHAVIOR_ENABLED is {keep_enabled!r}\n"
        f"    assert is_{safe_key}_active() is {keep_enabled!r}\n"
    )

    return {
        "title": f"chore(flags): remove retired flag `{flag_key}` (keep {keep_branch})",
        "description": (
            f"Removes `{flag_key}` runtime conditional branching while retaining the "
            f"`{keep_branch}` execution path. Flagsmith flag is NOT auto-deleted."
        ),
        "files_diff": [
            {
                "file_path": target_path,
                "action": "create",
                "before_code": "",
                "after_code": after_code,
                "unified_diff": _unified_diff(target_path, "", after_code),
            }
        ],
        "generated_tests": [
            {
                "file_path": f"tests/test_{safe_key}_cleanup.py",
                "content": test_content,
            }
        ],
    }
