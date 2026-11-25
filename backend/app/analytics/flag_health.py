"""Deterministic 7-detector Flag Health Monitor, explainable scorer, and repo SDK scanner.

Pure Python + Pydantic v2; zero SQLAlchemy ORM dependencies.
Unified output keys: health_score (0-100) and
health_issues: [{issue_type, penalty, summary, evidence}].
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class HealthIssueType(StrEnum):
    """Seven deterministic Flag Health detectors."""

    STALE = "stale"
    UNUSED = "unused"
    ALWAYS_ON = "always_on"
    ALWAYS_OFF = "always_off"
    ORPHANED = "orphaned"
    DUPLICATE = "duplicate"
    ENV_DRIFT = "env_drift"


PENALTY_WEIGHTS: dict[HealthIssueType, int] = {
    HealthIssueType.ORPHANED: -30,
    HealthIssueType.ENV_DRIFT: -20,
    HealthIssueType.STALE: -15,
    HealthIssueType.ALWAYS_ON: -15,
    HealthIssueType.ALWAYS_OFF: -15,
    HealthIssueType.UNUSED: -15,
    HealthIssueType.DUPLICATE: -10,
}

SDK_METHODS: tuple[str, ...] = (
    "is_feature_enabled",
    "get_feature_value",
    "hasFeature",
    "getValue",
)

_SDK_CALL_REGEX = re.compile(
    r"""(?<![.\w])(?:[\w$.]+\??\.)?(is_feature_enabled|get_feature_value|hasFeature|getValue)\s*\(\s*(['"`])([A-Za-z0-9_.\-/]+)\2""",
    re.MULTILINE,
)

_SDK_KWARG_REGEX = re.compile(
    r"""(?<![.\w])(?:[\w$.]+\??\.)?(is_feature_enabled|get_feature_value|hasFeature|getValue)\s*\([^)]*?\b(?:feature_name|feature_key|flag_key|key)\s*=\s*(['"])([A-Za-z0-9_.\-/]+)\2""",
    re.MULTILINE,
)


class HealthThresholds(BaseModel):
    """Configurable lookback thresholds matching workspace_settings.health_thresholds."""

    stale_days: int = Field(default=30, ge=1)
    unused_days: int = Field(default=14, ge=1)
    always_on_days: int = Field(default=30, ge=1)
    always_off_days: int = Field(default=30, ge=1)
    duplicate_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)


class CodeReference(BaseModel):
    """Single Flagsmith SDK call reference discovered in repository code."""

    file_path: str
    line_number: int = Field(ge=1)
    snippet: str
    flag_key: str = ""
    method: str = ""


class HealthIssue(BaseModel):
    """Explainable Flag Health deduction entry."""

    issue_type: HealthIssueType
    penalty: int
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class FlagHealthInput(BaseModel):
    """Normalized flag snapshot passed to deterministic health evaluation."""

    key: str
    flagsmith_project_id: int | None = None
    description: str | None = None
    flag_type: str = "STANDARD"
    is_archived: bool = False
    env_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mv_options: list[dict[str, Any]] = Field(default_factory=list)
    code_references: list[dict[str, Any] | CodeReference] = Field(default_factory=list)
    recent_assignment_count: int = Field(default=0, ge=0)
    has_active_experiment: bool = False
    has_active_rollout: bool = False
    repo_scanned: bool = True
    last_changed_at: datetime | None = None
    target_completion_at: datetime | None = None
    flagsmith_created_at: datetime | None = None
    updated_at: datetime | None = None
    created_at: datetime | None = None


class FlagHealthResult(BaseModel):
    """Health score and explainable issue breakdown for a single flag."""

    flag_key: str
    health_score: int = Field(ge=0, le=100)
    health_issues: list[HealthIssue] = Field(default_factory=list)


class ProjectHealthReport(BaseModel):
    """Aggregated Flag Health scan output across a project + repository."""

    average_health_score: float
    total_flags: int
    healthy_flags: int
    unhealthy_flags: int
    flags: list[FlagHealthResult]
    unregistered_code_flags: list[HealthIssue] = Field(default_factory=list)
    code_references_by_flag: dict[str, list[CodeReference]] = Field(default_factory=dict)


def _normalize_dt(dt: datetime | str | None) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        s = dt.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        parsed = datetime.fromisoformat(s)
    else:
        parsed = dt
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _effective_changed_at(flag: FlagHealthInput) -> datetime | None:
    for candidate in (
        flag.last_changed_at,
        flag.updated_at,
        flag.flagsmith_created_at,
        flag.created_at,
    ):
        dt = _normalize_dt(candidate)
        if dt is not None:
            return dt
    return None


def levenshtein_distance(a: str, b: str) -> int:
    """Compute exact Levenshtein edit distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a

    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        current = [i]
        for j, ch_b in enumerate(b, start=1):
            cost = 0 if ch_a == ch_b else 1
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def normalized_levenshtein_similarity(a: str, b: str) -> float:
    """Return normalized Levenshtein similarity in [0.0, 1.0] for two flag keys."""
    norm_a = a.strip().lower()
    norm_b = b.strip().lower()
    if not norm_a and not norm_b:
        return 1.0
    canon_a = re.sub(r"[-_.\s]+", "_", norm_a)
    canon_b = re.sub(r"[-_.\s]+", "_", norm_b)
    if canon_a == canon_b:
        return 1.0
    max_len = max(len(norm_a), len(norm_b))
    dist = levenshtein_distance(norm_a, norm_b)
    return max(0.0, 1.0 - (dist / max_len))


def _env_effective_percentage(state: Mapping[str, Any]) -> int:
    """Normalize an environment's rollout percentage (0..100)."""
    enabled = bool(state.get("enabled", False))
    pct = state.get("rollout_percentage")
    if pct is not None:
        return int(pct)
    seg_overrides = state.get("segment_overrides") or []
    for override in seg_overrides:
        if isinstance(override, Mapping) and override.get("enabled"):
            ov_pct = override.get("percentage")
            if ov_pct is not None:
                return int(ov_pct)
    return 100 if enabled else 0


def _env_is_100_percent_on(state: Mapping[str, Any], flag_type: str) -> bool:
    enabled = bool(state.get("enabled", False))
    if not enabled:
        return False
    pct = state.get("rollout_percentage")
    if pct is not None and int(pct) < 100:
        return False
    if flag_type.upper() == "MULTIVARIATE":
        mv_vals = state.get("mv_options") or []
        if mv_vals:
            weights = [
                int(item.get("percentage_allocation", item.get("default_percentage_allocation", 0)))
                for item in mv_vals
                if isinstance(item, Mapping)
            ]
            if weights and max(weights) < 100:
                return False
    return True


def _env_is_always_off(state: Mapping[str, Any]) -> bool:
    enabled = bool(state.get("enabled", False))
    pct = state.get("rollout_percentage")
    if enabled and (pct is None or int(pct) > 0):
        return False
    seg_overrides = state.get("segment_overrides") or []
    for override in seg_overrides:
        if isinstance(override, Mapping) and override.get("enabled"):
            ov_pct = override.get("percentage")
            if ov_pct is None or int(ov_pct) > 0:
                return False
    return True


def _canonical_env_signature(state: Mapping[str, Any]) -> tuple[Any, ...]:
    enabled = bool(state.get("enabled", False))
    raw_val = state.get("value")
    norm_val = "" if raw_val is None else str(raw_val)
    pct = _env_effective_percentage(state)
    mv = state.get("mv_options") or []
    mv_sig = tuple(
        sorted(
            (
                str(m.get("value", "")),
                int(m.get("percentage_allocation", m.get("default_percentage_allocation", 0))),
            )
            for m in mv
            if isinstance(m, Mapping)
        )
    )
    segs = state.get("segment_overrides") or []
    seg_sig = tuple(
        sorted(
            (
                str(s.get("segment_name", s.get("segment", ""))),
                bool(s.get("enabled", False)),
                s.get("percentage"),
            )
            for s in segs
            if isinstance(s, Mapping)
        )
    )
    return (enabled, norm_val, pct, mv_sig, seg_sig)


def _flag_non_empty_values(flag: FlagHealthInput) -> set[str]:
    values: set[str] = set()
    for state in flag.env_states.values():
        if isinstance(state, Mapping):
            val = state.get("value")
            if val is not None:
                s = str(val).strip()
                if s:
                    values.add(s)
    return values


def _coerce_thresholds(
    thresholds: HealthThresholds | Mapping[str, Any] | None,
) -> HealthThresholds:
    if thresholds is None:
        return HealthThresholds()
    if isinstance(thresholds, HealthThresholds):
        return thresholds
    return HealthThresholds.model_validate(dict(thresholds))


def _coerce_flag(flag: FlagHealthInput | Mapping[str, Any]) -> FlagHealthInput:
    if isinstance(flag, FlagHealthInput):
        return flag
    return FlagHealthInput.model_validate(dict(flag))


def _detect_duplicates(
    target: FlagHealthInput,
    peers: Iterable[FlagHealthInput],
    similarity_threshold: float,
) -> HealthIssue | None:
    matches: list[dict[str, Any]] = []
    target_desc = (target.description or "").strip().lower()
    target_vals = _flag_non_empty_values(target)

    for peer in peers:
        if peer.key == target.key:
            continue
        if (
            target.flagsmith_project_id is not None
            and peer.flagsmith_project_id is not None
            and target.flagsmith_project_id != peer.flagsmith_project_id
        ):
            continue

        sim = normalized_levenshtein_similarity(target.key, peer.key)
        peer_desc = (peer.description or "").strip().lower()
        peer_vals = _flag_non_empty_values(peer)

        reasons: list[str] = []
        if sim >= similarity_threshold:
            reasons.append(f"key_similarity={sim:.2f}")
        if target_desc and peer_desc and target_desc == peer_desc:
            reasons.append("identical_description")
        shared_vals = sorted(target_vals & peer_vals)
        if shared_vals:
            reasons.append("identical_value")

        if reasons:
            matches.append(
                {
                    "peer_key": peer.key,
                    "similarity": round(sim, 4),
                    "reasons": reasons,
                    "shared_values": shared_vals,
                }
            )

    if not matches:
        return None

    peer_keys = ", ".join(m["peer_key"] for m in matches)
    return HealthIssue(
        issue_type=HealthIssueType.DUPLICATE,
        penalty=PENALTY_WEIGHTS[HealthIssueType.DUPLICATE],
        summary=f"Flag '{target.key}' overlaps with peer flag(s): {peer_keys}.",
        evidence={"matches": matches},
    )


def evaluate_flag_health(
    flag: FlagHealthInput | Mapping[str, Any],
    *,
    peer_flags: Iterable[FlagHealthInput | Mapping[str, Any]] = (),
    thresholds: HealthThresholds | Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> FlagHealthResult:
    """Evaluate all 7 deterministic Flag Health detectors on a single flag."""
    f = _coerce_flag(flag)
    cfg = _coerce_thresholds(thresholds)
    ref_now = _normalize_dt(now) or datetime.now(UTC)

    issues: list[HealthIssue] = []
    changed_at = _effective_changed_at(f)
    days_unchanged = (
        (ref_now - changed_at).total_seconds() / 86400.0
        if changed_at is not None
        else 0.0
    )

    env_items = list(f.env_states.items())
    env_pcts = {env: _env_effective_percentage(st) for env, st in env_items}
    is_partial = any(0 < pct < 100 for pct in env_pcts.values()) or (
        len({bool(st.get("enabled", False)) for _, st in env_items}) > 1
    )
    target_dt = _normalize_dt(f.target_completion_at)
    past_target = target_dt is not None and ref_now > target_dt

    # 1. stale
    if past_target or (changed_at is not None and days_unchanged > cfg.stale_days and is_partial):
        issues.append(
            HealthIssue(
                issue_type=HealthIssueType.STALE,
                penalty=PENALTY_WEIGHTS[HealthIssueType.STALE],
                summary=(
                    f"Flag '{f.key}' has remained unchanged for {days_unchanged:.1f} days "
                    "while partially rolled out or past target completion."
                ),
                evidence={
                    "days_unchanged": round(days_unchanged, 2),
                    "stale_days_threshold": cfg.stale_days,
                    "env_rollout_percentages": env_pcts,
                    "past_target_completion": past_target,
                },
            )
        )

    # 2. unused
    if f.recent_assignment_count == 0 and len(f.code_references) == 0:
        issues.append(
            HealthIssue(
                issue_type=HealthIssueType.UNUSED,
                penalty=PENALTY_WEIGHTS[HealthIssueType.UNUSED],
                summary=(
                    f"Flag '{f.key}' recorded 0 assignment events in the last "
                    f"{cfg.unused_days} days and has 0 repository code references."
                ),
                evidence={
                    "recent_assignment_count": f.recent_assignment_count,
                    "unused_days_window": cfg.unused_days,
                    "code_reference_count": 0,
                },
            )
        )

    # 3. always_on
    if (
        env_items
        and not f.has_active_experiment
        and changed_at is not None
        and days_unchanged > cfg.always_on_days
        and all(_env_is_100_percent_on(st, f.flag_type) for _, st in env_items)
    ):
        issues.append(
            HealthIssue(
                issue_type=HealthIssueType.ALWAYS_ON,
                penalty=PENALTY_WEIGHTS[HealthIssueType.ALWAYS_ON],
                summary=(
                    f"Flag '{f.key}' has been 100% enabled across all {len(env_items)} "
                    f"environments for {days_unchanged:.1f} days with no active experiment."
                ),
                evidence={
                    "days_unchanged": round(days_unchanged, 2),
                    "always_on_days_threshold": cfg.always_on_days,
                    "environments": [env for env, _ in env_items],
                },
            )
        )

    # 4. always_off
    if (
        env_items
        and changed_at is not None
        and days_unchanged > cfg.always_off_days
        and all(_env_is_always_off(st) for _, st in env_items)
    ):
        issues.append(
            HealthIssue(
                issue_type=HealthIssueType.ALWAYS_OFF,
                penalty=PENALTY_WEIGHTS[HealthIssueType.ALWAYS_OFF],
                summary=(
                    f"Flag '{f.key}' has been 0% / disabled across all {len(env_items)} "
                    f"environments for {days_unchanged:.1f} days."
                ),
                evidence={
                    "days_unchanged": round(days_unchanged, 2),
                    "always_off_days_threshold": cfg.always_off_days,
                    "environments": [env for env, _ in env_items],
                },
            )
        )

    # 5. orphaned (present in Flagsmith with 0 references in connected codebase)
    if f.repo_scanned and len(f.code_references) == 0:
        issues.append(
            HealthIssue(
                issue_type=HealthIssueType.ORPHANED,
                penalty=PENALTY_WEIGHTS[HealthIssueType.ORPHANED],
                summary=(
                    f"Flag '{f.key}' exists in Flagsmith but has 0 Flagsmith SDK "
                    "references in the connected repository."
                ),
                evidence={
                    "orphan_kind": "missing_code_references",
                    "code_reference_count": 0,
                },
            )
        )

    # 6. duplicate
    coerced_peers = [_coerce_flag(p) for p in peer_flags]
    dup_issue = _detect_duplicates(f, coerced_peers, cfg.duplicate_similarity_threshold)
    if dup_issue is not None:
        issues.append(dup_issue)

    # 7. env_drift
    if len(env_items) >= 2 and not f.has_active_rollout and not f.has_active_experiment:
        signatures = {env: _canonical_env_signature(st) for env, st in env_items}
        if len(set(signatures.values())) > 1:
            env_summary = {
                env: {
                    "enabled": bool(st.get("enabled", False)),
                    "value": st.get("value"),
                    "rollout_percentage": _env_effective_percentage(st),
                }
                for env, st in env_items
            }
            issues.append(
                HealthIssue(
                    issue_type=HealthIssueType.ENV_DRIFT,
                    penalty=PENALTY_WEIGHTS[HealthIssueType.ENV_DRIFT],
                    summary=(
                        f"Flag '{f.key}' has differing states across environments "
                        "with no active rollout or experiment."
                    ),
                    evidence={"environments": env_summary},
                )
            )

    raw_score = 100 + sum(issue.penalty for issue in issues)
    health_score = max(0, min(100, raw_score))
    return FlagHealthResult(
        flag_key=f.key,
        health_score=health_score,
        health_issues=issues,
    )


def _extract_python_ast_references(file_path: str, source: str) -> list[CodeReference] | None:
    """Use Python AST to detect Flagsmith SDK calls, returning None on SyntaxError."""
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return None

    lines = source.splitlines()
    refs: list[CodeReference] = []
    target_names = set(SDK_METHODS) | {"has_feature"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method_name: str | None = None
        if isinstance(node.func, ast.Attribute) and node.func.attr in target_names:
            method_name = node.func.attr
        elif isinstance(node.func, ast.Name) and node.func.id in target_names:
            method_name = node.func.id

        if method_name is None:
            continue

        flag_key: str | None = None
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            flag_key = node.args[0].value
        else:
            for kw in node.keywords:
                if (
                    kw.arg in {"feature_name", "feature_key", "flag_key", "key"}
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    flag_key = kw.value.value
                    break

        if not flag_key:
            continue

        lineno = getattr(node, "lineno", 1)
        snippet = lines[lineno - 1].strip() if 1 <= lineno <= len(lines) else ""
        refs.append(
            CodeReference(
                file_path=file_path,
                line_number=lineno,
                snippet=snippet,
                flag_key=flag_key,
                method=method_name,
            )
        )
    return refs


def scan_code_for_flag_references(file_path: str, content: str) -> list[CodeReference]:
    """Scan a single source file for Flagsmith SDK references.

    Supports Python AST parsing (for .py files) and regex matching for JS/TS/JSX/TSX
    calls to ``is_feature_enabled``, ``get_feature_value``, ``hasFeature``, and ``getValue``.
    """
    if file_path.endswith(".py"):
        ast_refs = _extract_python_ast_references(file_path, content)
        if ast_refs is not None:
            return ast_refs

    refs: list[CodeReference] = []
    seen: set[tuple[int, str, str]] = set()
    lines = content.splitlines()
    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(("//", "#", "/*", "*")):
            continue
        for regex in (_SDK_CALL_REGEX, _SDK_KWARG_REGEX):
            for match in regex.finditer(line):
                method_name = match.group(1)
                flag_key = match.group(3)
                dedup_key = (lineno, method_name, flag_key)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                refs.append(
                    CodeReference(
                        file_path=file_path,
                        line_number=lineno,
                        snippet=line.strip(),
                        flag_key=flag_key,
                        method=method_name,
                    )
                )
    return refs


def scan_files_for_flag_references(
    files: Mapping[str, str] | Iterable[tuple[str, str]],
) -> dict[str, list[CodeReference]]:
    """Scan a mapping or iterable of (file_path, content) and group refs by flag key."""
    items = files.items() if isinstance(files, Mapping) else files
    by_flag: dict[str, list[CodeReference]] = {}
    for file_path, content in items:
        for ref in scan_code_for_flag_references(file_path, content):
            by_flag.setdefault(ref.flag_key, []).append(ref)
    return by_flag


def scan_directory_for_flag_references(
    root_dir: str | Path,
    extensions: tuple[str, ...] = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
) -> dict[str, list[CodeReference]]:
    """Walk a local directory tree and scan matching source files for SDK refs."""
    root = Path(root_dir)
    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", ".next", "dist", "build"}
    by_flag: dict[str, list[CodeReference]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in extensions:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel_path = path.relative_to(root).as_posix()
        for ref in scan_code_for_flag_references(rel_path, content):
            by_flag.setdefault(ref.flag_key, []).append(ref)
    return by_flag


def scan_project_flag_health(
    flags: Iterable[FlagHealthInput | Mapping[str, Any]],
    *,
    repository_files: Mapping[str, str] | None = None,
    code_references_by_flag: Mapping[str, list[CodeReference | Mapping[str, Any]]] | None = None,
    thresholds: HealthThresholds | Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> ProjectHealthReport:
    """Scan all project flags + optional repo code to produce full ProjectHealthReport.

    Detects both Flagsmith flags missing from code AND SDK-referenced keys in code
    that are missing from Flagsmith (both classified as ``orphaned``).
    """
    coerced_flags = [_coerce_flag(item) for item in flags]
    scanned_refs: dict[str, list[CodeReference]] = {}

    if repository_files is not None:
        scanned_refs = scan_files_for_flag_references(repository_files)
    elif code_references_by_flag is not None:
        for k, refs in code_references_by_flag.items():
            scanned_refs[k] = [
                r if isinstance(r, CodeReference) else CodeReference.model_validate(dict(r))
                for r in refs
            ]

    repo_was_scanned = repository_files is not None or code_references_by_flag is not None
    enriched_flags: list[FlagHealthInput] = []
    for f in coerced_flags:
        if repo_was_scanned:
            refs_for_key = scanned_refs.get(f.key, [])
            enriched_flags.append(
                f.model_copy(
                    update={
                        "code_references": list(refs_for_key),
                        "repo_scanned": True,
                    }
                )
            )
        else:
            enriched_flags.append(f)

    results = [
        evaluate_flag_health(
            f,
            peer_flags=enriched_flags,
            thresholds=thresholds,
            now=now,
        )
        for f in enriched_flags
    ]

    known_keys = {f.key for f in enriched_flags}
    unregistered_issues: list[HealthIssue] = []
    for code_key, refs in sorted(scanned_refs.items()):
        if code_key not in known_keys and refs:
            unregistered_issues.append(
                HealthIssue(
                    issue_type=HealthIssueType.ORPHANED,
                    penalty=PENALTY_WEIGHTS[HealthIssueType.ORPHANED],
                    summary=(
                        f"Flag '{code_key}' is referenced in repository code "
                        f"({len(refs)} location(s)) but missing from Flagsmith."
                    ),
                    evidence={
                        "orphan_kind": "missing_in_flagsmith",
                        "flag_key": code_key,
                        "code_references": [r.model_dump() for r in refs],
                    },
                )
            )

    total = len(results)
    avg_score = sum(r.health_score for r in results) / total if total > 0 else 100.0
    unhealthy = sum(1 for r in results if r.health_issues)
    healthy = total - unhealthy

    return ProjectHealthReport(
        average_health_score=round(avg_score, 2),
        total_flags=total,
        healthy_flags=healthy,
        unhealthy_flags=unhealthy,
        flags=results,
        unregistered_code_flags=unregistered_issues,
        code_references_by_flag=scanned_refs,
    )
