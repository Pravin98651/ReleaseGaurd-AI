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

_RISK_PATTERNS: list[tuple[str, re.Pattern[str], str, RiskLevel, int, str, str]] = [
    (
        "payment",
        re.compile(r"(checkout|payment|billing|stripe|refund|charge|invoice|promo)", re.IGNORECASE),
        "Payment / Checkout Flow Mutation",
        RiskLevel.HIGH,
        35,
        "Wrap new checkout/payment logic behind a Flagsmith feature flag with safe legacy fallback.",
        "Gate payment/checkout code path behind a Flagsmith boolean or multivariate flag and verify fallback totals.",
    ),
    (
        "auth",
        re.compile(r"(auth|jwt|oauth|token|permission|rbac|secret|password|session)", re.IGNORECASE),
        "Authentication / Authorization Logic Change",
        RiskLevel.HIGH,
        30,
        "Audit access checks and gate new auth paths behind a non-prod verified flag.",
        "Review RBAC/token checks and add regression tests for unauthorized and expired-token cases.",
    ),
    (
        "security",
        re.compile(
            r"(eval\s*\(|exec\s*\(|pickle\.loads|shell\s*=\s*True|verify\s*=\s*False|innerHTML|dangerouslySetInnerHTML|sql\s*\+)",
            re.IGNORECASE,
        ),
        "Security / Injection / Unsafe Execution Pattern",
        RiskLevel.CRITICAL,
        40,
        "Eliminate unsafe dynamic execution/TLS bypass and sanitize all untrusted inputs.",
        "Replace unsafe sinks with parameterized queries, safe parsers, and verified TLS.",
    ),
    (
        "migrations",
        re.compile(r"(alembic|migration|alter\s+table|drop\s+table|drop\s+column|truncate\s+table)", re.IGNORECASE),
        "Database Schema / Migration Modification",
        RiskLevel.HIGH,
        25,
        "Ensure backward-compatible expand-and-contract rollout before enabling feature code.",
        "Run migration dry-run on staging snapshot and ensure old application code remains compatible.",
    ),
    (
        "deps",
        re.compile(r"(requirements.*\.txt|pyproject\.toml|package\.json|package-lock\.json|poetry\.lock|Dockerfile)", re.IGNORECASE),
        "Dependency / Build Manifest Change",
        RiskLevel.MEDIUM,
        15,
        "Pin dependency versions/hashes and verify transitive security advisories.",
        "Run dependency audit and verify lockfile integrity before rollout.",
    ),
    (
        "api",
        re.compile(r"(@router\.|APIRouter|response_model|status_code|/api/v\d+|openapi|graphql)", re.IGNORECASE),
        "Public API Contract / Route Modification",
        RiskLevel.MEDIUM,
        18,
        "Verify backward compatibility for existing API consumers and response schemas.",
        "Add API contract tests validating request/response schemas and status codes.",
    ),
    (
        "perf",
        re.compile(r"(time\.sleep\(|while\s+True:|SELECT\s+\*|N\+1|unbounded)", re.IGNORECASE),
        "Potential Performance / Blocking Operation",
        RiskLevel.MEDIUM,
        15,
        "Avoid blocking calls or unbounded queries on hot request paths.",
        "Profile query latency and add pagination, timeouts, or async offloading.",
    ),
    (
        "error_handling",
        re.compile(r"(except\s+Exception\s*:\s*pass|except\s*:\s*pass|catch\s*\([^)]*\)\s*\{\s*\})", re.IGNORECASE),
        "Swallowed Exception / Silent Failure Handler",
        RiskLevel.MEDIUM,
        15,
        "Log structured failure context and preserve payment_failed telemetry emission.",
        "Replace silent exception handlers with structured error logging and explicit fallback telemetry.",
    ),
    (
        "breaking_changes",
        re.compile(r"(deprecated|breaking\s+change|removed\s+endpoint|drop_column|rename_column)", re.IGNORECASE),
        "Potential Breaking Interface or Schema Change",
        RiskLevel.HIGH,
        25,
        "Preserve compatibility shims or versioned endpoints during transition window.",
        "Provide a deprecation window and dual-read/dual-write compatibility before removal.",
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
    recommendations: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    flag_opportunities: list[dict[str, Any]] = []
    categories_detected: list[str] = []

    total_changes = 0
    has_test_changes = False
    has_flagsmith_guard = False
    risky_files: list[str] = []
    score = 10

    for item in files_data:
        filename = str(item.get("filename", ""))
        status = str(item.get("status") or "modified")
        raw_patch = item.get("patch")
        patch = str(raw_patch or "")
        additions = int(item.get("additions") or 0)
        deletions = int(item.get("deletions") or 0)
        total_changes += additions + deletions

        if "test" in filename.lower():
            has_test_changes = True
        if any(tok in patch for tok in _UNFLAGGED_MARKERS):
            has_flagsmith_guard = True

        if (raw_patch is None or (not patch.strip() and (additions + deletions) > 0)) and status != "removed":
            static_signals.append(f"binary_or_truncated_diff:{filename}")
            unknowns.append(
                {
                    "title": f"Uninspected diff for {filename}",
                    "area": "diff_retrieval",
                    "reason": "GitHub returned no unified patch (binary asset or oversized/truncated diff).",
                    "impact": "Line-level security and logic checks could not be performed on this file.",
                }
            )

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
                        if "error_handling" not in categories_detected:
                            categories_detected.append("error_handling")
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

        for category, regex, title, sev, weight, mitigation, rec_action in _RISK_PATTERNS:
            matched_line: tuple[int, str] | None = None
            if regex.search(filename):
                matched_line = added_lines[0] if added_lines else (1, filename)
            else:
                for ln_no, ln_txt in added_lines:
                    if regex.search(ln_txt):
                        matched_line = (ln_no, ln_txt.strip())
                        break

            if matched_line is not None:
                static_signals.append(f"risk_marker:{category}:{title}:{filename}:L{matched_line[0]}")
                if category not in categories_detected:
                    categories_detected.append(category)
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
                recommendations.append(
                    {
                        "title": f"Mitigate {category} risk in {filename}",
                        "priority": "high" if sev in (RiskLevel.HIGH, RiskLevel.CRITICAL) else "medium",
                        "action": rec_action,
                        "target_file": filename,
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
        if "missing_tests" not in categories_detected:
            categories_detected.append("missing_tests")
        score += 12
        potential_risks.append(
            {
                "title": "No accompanying test file updates detected in PR diff",
                "severity": RiskLevel.MEDIUM.value,
                "assumption": "Existing suite might not cover newly introduced branches or failure states.",
                "verification_needed": "Add isolated unit/integration tests covering both control and treatment paths.",
            }
        )
        recommendations.append(
            {
                "title": "Add test coverage for changed modules",
                "priority": "high",
                "action": "Add unit and integration tests covering happy-path, flag-disabled fallback, and failure states.",
                "target_file": risky_files[0] if risky_files else None,
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
        recommendations.append(
            {
                "title": f"Wrap risky changes behind Flagsmith flag `{suggested_key}`",
                "priority": "high",
                "action": "Generate a Flagsmith-guarded patch + tests and provision the flag in non-prod via Flag Agent.",
                "target_file": risky_files[0],
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

    unknowns.append(
        {
            "title": "Runtime environment & unchanged caller behavior",
            "area": "runtime_context",
            "reason": "Static PR diff does not expose production traffic distribution, live Flagsmith overrides, or unretrieved callers.",
            "impact": "Verify in staging with live Flagsmith evaluation and canary telemetry.",
        }
    )

    bounded_score = min(100, max(5, score))
    return {
        "static_signals": static_signals,
        "categories_detected": categories_detected,
        "risk_score": bounded_score,
        "risk_level": _score_to_risk_level(bounded_score).value,
        "confirmed_risks": confirmed_risks,
        "potential_risks": potential_risks,
        "recommendations": recommendations,
        "unknowns": unknowns,
        "flag_opportunities": flag_opportunities,
        "changed_files": [str(f.get("filename", "")) for f in files_data if f.get("filename")],
    }


def annotate_flag_opportunities_with_existing(
    flag_opportunities: list[dict[str, Any]],
    existing_flag_keys: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Check suggested flags against existing Flagsmith/DB flags and emit a Flag Agent handoff."""
    normalized_existing = {k.strip().lower(): k for k in existing_flag_keys if k}
    annotated: list[dict[str, Any]] = []
    for opp in flag_opportunities:
        item = dict(opp)
        raw_key = str(item.get("suggested_flag_key") or item.get("flag_key") or "").strip()
        norm_key = raw_key.lower()
        existing_match = normalized_existing.get(norm_key)
        already_exists = existing_match is not None
        item["suggested_flag_key"] = raw_key
        item["already_exists"] = already_exists
        item["existing_flag_key"] = existing_match
        item["dedup_action"] = "reuse_existing" if already_exists else "create_new"
        annotated.append(item)

    flag_agent_rec: dict[str, Any] | None = None
    if annotated:
        primary = annotated[0]
        exists = bool(primary.get("already_exists"))
        flag_agent_rec = {
            "recommended_agent": "flag_agent",
            "action": "reuse_existing_flag" if exists else "create_non_prod_flag",
            "flag_key": primary["suggested_flag_key"],
            "flag_type": str(primary.get("flag_type") or FlagType.STANDARD.value),
            "already_exists": exists,
            "existing_flag_key": primary.get("existing_flag_key"),
            "target_files": list(primary.get("target_files") or []),
            "recommended_rollout": str(primary.get("recommended_rollout") or "10% -> 25% -> 50% -> 75% -> 100%"),
            "rationale": (
                f"Flag `{primary['suggested_flag_key']}` already exists; reuse it instead of creating a duplicate."
                if exists
                else f"Create non-prod Flagsmith flag `{primary['suggested_flag_key']}` before opening/merging rollout."
            ),
            "requires_approval": not exists,
        }
    return annotated, flag_agent_rec


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
        "recommendations": baseline["recommendations"],
        "unknowns": baseline["unknowns"],
        "categories_detected": baseline["categories_detected"],
        "flag_opportunities": baseline["flag_opportunities"],
        "static_signals": baseline["static_signals"],
        "analysis_mode": "static_only",
        "gemini_error": None,
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
            "Analyze the following sandboxed Pull Request metadata and unified diff across:\n"
            "1) PR objective and modified functionality\n"
            "2) Security, authentication/authorization, payment/checkout, and database migrations\n"
            "3) Dependencies, API contracts, performance, error handling, breaking changes, and missing tests\n"
            "4) Feature flag wrapping opportunities.\n"
            "Return STRICT JSON with keys:\n"
            "  summary (str covering objective, modified functionality, dependencies/API/DB/security impact, "
            "backward compatibility, and performance),\n"
            "  confirmed_risks (list of {title, severity, file_path, line_range, evidence, mitigation}),\n"
            "  potential_risks (list of {title, severity, assumption, verification_needed}),\n"
            "  recommendations (list of {title, priority, action, target_file}),\n"
            "  unknowns (list of {title, area, reason, impact}),\n"
            "  flag_opportunities (list of {suggested_flag_key, flag_type, target_files, rationale, recommended_rollout}).\n"
            "CRITICAL: Do NOT invent numeric risk scores. Only place findings with direct file_path + code evidence "
            "from the diff into confirmed_risks; place assumptions in potential_risks; place uninspected/missing "
            "context in unknowns.\n"
            f"Deterministic static signals already observed: {json.dumps(baseline['static_signals'])}\n\n"
            f"{wrap_untrusted('github_pr_title', meta, pr_title, max_chars=2000)}\n"
            f"{wrap_untrusted('github_pr_body', meta, pr_body or '', max_chars=8000)}\n"
            f"{wrap_untrusted('github_pr_diff', meta, raw_diff)}"
        )
        try:
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

            cleaned_json = raw_text.strip()
            if cleaned_json.startswith("```"):
                cleaned_json = re.sub(
                    r"^```(?:json)?\s*|\s*```$", "", cleaned_json, flags=re.DOTALL
                ).strip()

            if cleaned_json:
                parsed = json.loads(cleaned_json)
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
                    # Never let Gemini invent or override deterministic risk_score / risk_level.
                    if verified_confirmed:
                        result["confirmed_risks"] = verified_confirmed
                    if extra_potential:
                        result["potential_risks"] = extra_potential
                    if isinstance(parsed.get("recommendations"), list) and parsed["recommendations"]:
                        seen_recs = {str(r.get("title", "")).lower() for r in result["recommendations"]}
                        for rec in parsed["recommendations"]:
                            if isinstance(rec, dict) and str(rec.get("title", "")).lower() not in seen_recs:
                                result["recommendations"].append(rec)
                                seen_recs.add(str(rec.get("title", "")).lower())
                    if isinstance(parsed.get("unknowns"), list) and parsed["unknowns"]:
                        seen_unk = {str(u.get("title", "")).lower() for u in result["unknowns"]}
                        for unk in parsed["unknowns"]:
                            if isinstance(unk, dict) and str(unk.get("title", "")).lower() not in seen_unk:
                                result["unknowns"].append(unk)
                                seen_unk.add(str(unk.get("title", "")).lower())
                    if parsed.get("flag_opportunities"):
                        result["flag_opportunities"] = list(parsed["flag_opportunities"])
                    result["analysis_mode"] = "gemini_ai"
        except Exception as exc:  # noqa: BLE001
            from app.core.security import scrub_secrets

            err_msg = scrub_secrets(str(exc))[:240] or type(exc).__name__
            result["analysis_mode"] = "static_fallback"
            result["gemini_error"] = err_msg
            result["analysis_summary"] = (
                f"{result['analysis_summary']} [Static Fallback — Gemini unavailable: {err_msg}]"
            )

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


def _build_python_import_block(primary_file: str, symbol: str) -> str:
    """Return valid Python import lines even when directory names contain hyphens (e.g. demo-app)."""
    module_parts = primary_file[:-3].split("/")
    if all(part.isidentifier() for part in module_parts):
        return f"from {'.'.join(module_parts)} import {symbol}"
    return (
        "import importlib.util\n"
        "from pathlib import Path\n\n"
        f"_MOD_PATH = Path(__file__).resolve().parents[1] / {primary_file!r}\n"
        '_SPEC = importlib.util.spec_from_file_location("_rg_target_mod", _MOD_PATH)\n'
        "_MOD = importlib.util.module_from_spec(_SPEC)\n"
        "assert _SPEC is not None and _SPEC.loader is not None\n"
        "_SPEC.loader.exec_module(_MOD)\n"
        f"{symbol} = _MOD.{symbol}"
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

    fn_name = (
        f"resolve_{safe_key}_variant"
        if flag_type == FlagType.MULTIVARIATE
        else f"is_{safe_key}_enabled"
    )
    import_block = _build_python_import_block(primary_file, fn_name)
    fn_name_resolved = fn_name
    if flag_type == FlagType.MULTIVARIATE:
        test_content = (
            f'"""Isolated tests for `{flag_key}` Flagsmith multivariate wrapper."""\n\n'
            f"{import_block}\n\n\n"
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
            f'    assert {fn_name_resolved}(_FakeClient(True, "v2"), "u1") == "v2"\n'
            f'    assert {fn_name_resolved}(_FakeClient(False, "v2"), "u1") == "v1"\n'
        )
    else:
        test_content = (
            f'"""Isolated tests for `{flag_key}` Flagsmith boolean wrapper."""\n\n'
            f"{import_block}\n\n\n"
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
        "generation_mode": "template_fallback",
    }


def _is_safe_relative_patch_path(raw_path: str) -> bool:
    """Validate relative repository file path before accepting an AI-proposed patch."""
    clean = raw_path.strip()
    if not clean or "\\" in clean or clean.startswith("/") or "\x00" in clean:
        return False
    parts = clean.split("/")
    if any(p in ("", ".", "..", ".git") or p.startswith(".env") for p in parts):
        return False
    return not re.match(r"^[A-Za-z]:", clean)


def _passes_basic_syntax(file_path: str, content: str) -> bool:
    if not content.strip():
        return False
    if file_path.endswith(".py"):
        try:
            ast.parse(content)
        except SyntaxError:
            return False
    return True


async def generate_context_aware_flag_patch(
    *,
    flag_key: str,
    flag_type: FlagType,
    target_files: list[str],
    base_files: dict[str, str] | None = None,
    files_data: list[dict[str, Any]] | None = None,
    pr_title: str = "",
    pr_body: str | None = None,
    repo_full_name: str = "",
    pr_number: int = 0,
    gemini_client: GeminiClient | None = None,
) -> dict[str, Any]:
    """Use Gemini (with AFC=Disabled) for language-aware Flagsmith wrapping, falling back safely."""
    fallback_base = dict(base_files or {})

    if gemini_client is not None and gemini_client.is_configured and (
        fallback_base or files_data or target_files
    ):
        context_blobs: list[str] = []
        for path in target_files[:5]:
            if path in fallback_base and _is_safe_relative_patch_path(path):
                context_blobs.append(
                    wrap_untrusted(
                        "github_source_file",
                        {"repo": repo_full_name, "path": path},
                        fallback_base[path],
                        max_chars=12000,
                    )
                )
        for path, code in list(fallback_base.items())[:3]:
            if path not in target_files and _is_safe_relative_patch_path(path):
                context_blobs.append(
                    wrap_untrusted(
                        "github_source_file",
                        {"repo": repo_full_name, "path": path},
                        code,
                        max_chars=8000,
                    )
                )

        diff_sections = []
        for item in (files_data or [])[:6]:
            fn = str(item.get("filename") or "")
            patch = str(item.get("patch") or "")
            if fn and patch:
                diff_sections.append(f"--- a/{fn}\n+++ b/{fn}\n{patch}")

        meta = {"repo": repo_full_name, "pr_number": pr_number, "flag_key": flag_key}
        sdk_guidance = (
            "Python: use `flagsmith_client.get_identity_flags(identifier=...)` with `is_feature_enabled` / "
            "`get_feature_value` and catch ONLY `(AttributeError, RuntimeError, ValueError, TypeError, OSError)` "
            "(NEVER `except Exception:` which fails Ruff BLE001) with safe legacy fallback on disabled/error.\n"
            "TypeScript/JavaScript/React: use Flagsmith JS SDK (`flagsmith.hasFeature(key)` / `getValue(key)` "
            "or passed flag evaluation props) preserving legacy UI/handler when disabled or unset."
        )
        prompt = (
            f"Generate a minimal, production-safe Feature Flag patch wrapping `{flag_key}` "
            f"(type: {flag_type.value}) for target files {json.dumps(target_files)}.\n"
            f"SDK patterns:\n{sdk_guidance}\n"
            "Requirements:\n"
            "- If a target file's existing source is provided, use `action: 'modify'` and return the COMPLETE "
            "updated file content in `after_code` preserving unrelated logic, types, and imports.\n"
            "- Only touch safe relative paths matching target_files or new helper/test paths.\n"
            "- Always preserve legacy behavior when the flag is disabled, missing, or throws.\n"
            "- Include ruff-clean Python unit tests in `generated_tests` covering enabled, disabled, and error "
            "fallback. IMPORTANT: `generated_tests` run in an isolated temp dir containing ONLY the files in "
            "`files_diff` + `generated_tests`, so stub any unavailable third-party/sibling modules with `sys.modules` "
            "and use relative/file-path loading if directory names have hyphens.\n"
            "Return STRICT JSON with keys:\n"
            "  title (str), description (str),\n"
            "  files_diff (list of {file_path, action ('create'|'modify'), after_code}),\n"
            "  generated_tests (list of {file_path, content}).\n\n"
            f"{wrap_untrusted('github_pr_title', meta, pr_title or 'PR Flag Wrap', max_chars=1000)}\n"
            f"{wrap_untrusted('github_pr_body', meta, pr_body or '', max_chars=3000)}\n"
            + (
                f"{wrap_untrusted('github_pr_diff', meta, chr(10).join(diff_sections), max_chars=14000)}\n"
                if diff_sections
                else ""
            )
            + "\n".join(context_blobs)
        )

        try:
            resp = await gemini_client.generate_content(
                contents=[{"role": "user", "parts": [{"text": prompt}]}],
                system_instruction=PR_AGENT_SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
            )
            raw_text = getattr(resp, "text", None) or ""
            if not raw_text and getattr(resp, "candidates", None):
                parts = getattr(resp.candidates[0].content, "parts", []) or []
                raw_text = "".join(
                    str(getattr(p, "text", "") or "")
                    for p in parts
                    if not bool(getattr(p, "thought", False))
                )

            cleaned = raw_text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(
                    r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL
                ).strip()

            parsed = json.loads(cleaned) if cleaned else None
            if isinstance(parsed, dict) and isinstance(parsed.get("files_diff"), list):
                allowed_base_paths = set(fallback_base.keys()) | set(target_files)
                validated_diffs: list[dict[str, Any]] = []
                for entry in parsed["files_diff"]:
                    if not isinstance(entry, dict):
                        continue
                    fp = str(entry.get("file_path") or "").strip()
                    after_code = str(entry.get("after_code") or "")
                    if not _is_safe_relative_patch_path(fp) or not _passes_basic_syntax(fp, after_code):
                        continue
                    # Constrain modifications strictly to known paths when target/base files exist
                    if allowed_base_paths and fp not in allowed_base_paths and fp not in fallback_base:
                        # Allow new flag helper files created alongside target files
                        action = "create"
                        before_code = ""
                    elif fp in fallback_base:
                        action = "modify"
                        before_code = fallback_base[fp]
                    else:
                        action = "create" if str(entry.get("action")) != "modify" else "modify"
                        before_code = str(fallback_base.get(fp, ""))

                    if action == "modify" and after_code == before_code:
                        continue
                    validated_diffs.append(
                        {
                            "file_path": fp,
                            "action": action,
                            "before_code": before_code,
                            "after_code": after_code,
                            "unified_diff": _unified_diff(fp, before_code, after_code),
                        }
                    )

                validated_tests: list[dict[str, Any]] = []
                for t_entry in parsed.get("generated_tests") or []:
                    if not isinstance(t_entry, dict):
                        continue
                    tfp = str(t_entry.get("file_path") or "").strip()
                    tcontent = str(t_entry.get("content") or "")
                    if _is_safe_relative_patch_path(tfp) and _passes_basic_syntax(tfp, tcontent):
                        validated_tests.append({"file_path": tfp, "content": tcontent})

                if validated_diffs:
                    if not validated_tests:
                        tmpl = generate_flag_patch_payload(
                            flag_key=flag_key,
                            flag_type=flag_type,
                            target_files=target_files,
                            base_files=fallback_base,
                        )
                        validated_tests = tmpl["generated_tests"]
                    return {
                        "title": str(
                            parsed.get("title")
                            or f"feat(flags): guard change behind Flagsmith `{flag_key}`"
                        )[:250],
                        "description": str(
                            parsed.get("description")
                            or f"Context-aware Gemini Flagsmith patch wrapping `{flag_key}` with safe fallback."
                        ),
                        "files_diff": validated_diffs,
                        "generated_tests": validated_tests,
                        "generation_mode": "gemini_context_aware",
                    }
        except (ValueError, RuntimeError, TypeError):
            # Safely fall back to deterministic template if Gemini call fails or emits unusable output
            pass

    return generate_flag_patch_payload(
        flag_key=flag_key,
        flag_type=flag_type,
        target_files=target_files,
        base_files=fallback_base,
    )


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
    import_block = _build_python_import_block(target_path, f"is_{safe_key}_active")
    test_content = (
        f'"""Cleanup verification test for retired flag `{flag_key}`."""\n\n'
        f"{import_block}\n\n\n"
        f"def test_{safe_key}_cleanup_resolved() -> None:\n"
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
