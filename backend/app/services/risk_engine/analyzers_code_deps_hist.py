"""Code Impact, Dependency Impact, and Historical Failure Analyzers."""

from __future__ import annotations

import ast
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.specialists.pr_agent import extract_static_pr_signals
from app.models import (
    DataSufficiency,
    DimensionStatus,
    Experiment,
    ExperimentStatus,
    PullRequest,
    Release,
    ReleaseRecommendation,
    ReleaseStatus,
    RiskClassification,
    RolloutAction,
    RolloutHistory,
)
from app.services.risk_engine.contracts import (
    AnalyzerResult,
    EvidenceSpec,
    FindingSpec,
    MitigationSpec,
    score_to_classification,
)

_CATEGORY_RULES: list[tuple[str, re.Pattern[str], int]] = [
    ("payment", re.compile(r"(checkout|payment|billing|stripe|refund|invoice|promo)", re.IGNORECASE), 25),
    ("authentication", re.compile(r"(auth|login|oauth|jwt|session|password)", re.IGNORECASE), 22),
    ("authorization", re.compile(r"(rbac|permission|policy|acl|role)", re.IGNORECASE), 22),
    ("database", re.compile(r"(alembic|migration|models|schema|\.sql$)", re.IGNORECASE), 20),
    ("security", re.compile(r"(secret|encrypt|crypto|token|security|sanitize)", re.IGNORECASE), 22),
    ("api", re.compile(r"(api|routes|router|endpoint|controller|schema)", re.IGNORECASE), 14),
    ("infrastructure", re.compile(r"(dockerfile|compose|k8s|helm|terraform|\.ya?ml$)", re.IGNORECASE), 15),
    ("configuration", re.compile(r"(config|settings|\.env|flagsmith)", re.IGNORECASE), 12),
    ("frontend", re.compile(r"(frontend|components|pages|app/.*\.tsx?$)", re.IGNORECASE), 8),
    ("testing", re.compile(r"(test_|_test\.|\.test\.|\.spec\.|tests/)", re.IGNORECASE), 0),
    ("documentation", re.compile(r"(\.md$|docs/|readme)", re.IGNORECASE), 0),
]

_PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", re.MULTILINE)
_JS_IMPORT_RE = re.compile(r"""(?:import\s+.*?\s+from\s+|require\()['"]([^'"]+)['"]""")


def classify_file_categories(filename: str, patch: str = "") -> list[str]:
    cats: list[str] = []
    combined = f"{filename}\n{patch[:1500]}"
    for cat, pat, _ in _CATEGORY_RULES:
        if pat.search(combined):
            cats.append(cat)
    return cats or ["core_logic"]


class CodeImpactAnalyzer:
    """Evaluates technical sensitivity, affected categories, and complexity of changed files."""

    @staticmethod
    def analyze(files_data: list[dict[str, Any]], pr: PullRequest | None = None) -> AnalyzerResult:
        if not files_data:
            return AnalyzerResult(
                dimension_key="code_impact",
                dimension_name="Code-Change Impact",
                status=DimensionStatus.UNAVAILABLE if pr is None else DimensionStatus.PARTIAL,
                data_sufficiency=DataSufficiency.INSUFFICIENT_DATA,
                score=None if pr is None else 25,
                severity=RiskClassification.MODERATE,
                evidence_quality="low",
                explanations=["No changed-file diff payload was available for inspection."],
                limitations=["Changed files list is empty or GitHub diff was not fetched."],
            )

        categories: set[str] = set()
        critical_modules: list[str] = []
        findings: list[FindingSpec] = []
        mitigations: list[MitigationSpec] = []
        limitations: list[str] = []
        total_lines = 0
        cat_weight_sum = 0

        static_signals = extract_static_pr_signals(files_data)

        for item in files_data:
            fname = str(item.get("filename") or "")
            patch = str(item.get("patch") or "")
            status = str(item.get("status") or "modified")
            total_lines += int(item.get("additions") or 0) + int(item.get("deletions") or 0)

            if item.get("binary") or (not patch and fname.endswith((".png", ".jpg", ".pdf", ".zip"))):
                limitations.append(f"Binary or non-text file skipped for diff inspection: {fname}")
                continue

            file_cats = classify_file_categories(fname, patch)
            categories.update(file_cats)
            for cat, _, pts in _CATEGORY_RULES:
                if cat in file_cats and pts > 0:
                    cat_weight_sum = max(cat_weight_sum, pts)

            if any(c in file_cats for c in ("payment", "authentication", "authorization", "database", "security")):
                critical_modules.append(fname)
                findings.append(
                    FindingSpec(
                        dimension_key="code_impact",
                        finding_type="sensitive_module_modified",
                        title=f"Sensitive module modified: {fname}",
                        description=(
                            f"File '{fname}' touches {', '.join(file_cats)} logic "
                            f"(status={status}, +{item.get('additions', 0)}/-{item.get('deletions', 0)})."
                        ),
                        severity=RiskClassification.HIGH,
                        affected_module=fname,
                        source_reference=fname,
                        evidence_summary=f"GitHub PR diff for {fname}",
                        evidence=[
                            EvidenceSpec(
                                evidence_type="pr_diff",
                                source_type="github_pr",
                                source_reference=fname,
                                title=f"Changed file {fname}",
                                summary=f"Modified categories: {', '.join(file_cats)}",
                                structured_data={
                                    "additions": item.get("additions", 0),
                                    "deletions": item.get("deletions", 0),
                                    "categories": file_cats,
                                },
                            )
                        ],
                    )
                )

        # Normalize deterministic score (0-100)
        base = 12
        base += min(35, cat_weight_sum + (10 if len(critical_modules) >= 2 else 0))
        base += min(20, len(categories) * 4)
        base += min(18, total_lines // 40)
        if static_signals.get("confirmed_risks"):
            base += min(15, len(static_signals["confirmed_risks"]) * 6)
        score = max(5, min(100, base))
        sev = score_to_classification(score)

        if critical_modules:
            mitigations.append(
                MitigationSpec(
                    dimension_key="code_impact",
                    title="Peer review sensitive module changes",
                    description=f"Require domain-owner review for modified critical files: {', '.join(critical_modules[:4])}.",
                    priority=sev,
                    mitigation_type="code_review",
                    verification_criteria="Domain owner approves PR changes on sensitive paths.",
                )
            )

        return AnalyzerResult(
            dimension_key="code_impact",
            dimension_name="Code-Change Impact",
            status=DimensionStatus.EVALUATED,
            data_sufficiency=DataSufficiency.SUFFICIENT,
            score=score,
            severity=sev,
            evidence_quality="high",
            explanations=[
                f"Inspected {len(files_data)} changed files ({total_lines} changed lines) across {', '.join(sorted(categories))}."
            ],
            limitations=limitations,
            findings=findings,
            mitigations=mitigations,
            details={
                "changed_files": len(files_data),
                "total_lines": total_lines,
                "affected_categories": sorted(categories),
                "critical_modules_modified": critical_modules,
            },
        )


class DependencyImpactAnalyzer:
    """Builds a Python/JS/TS import & downstream impact graph from changed files and repo context."""

    @staticmethod
    def analyze(
        files_data: list[dict[str, Any]],
        repo_files: dict[str, str] | None = None,
    ) -> AnalyzerResult:
        if not files_data:
            return AnalyzerResult(
                dimension_key="dependency_impact",
                dimension_name="Dependency & Affected-Module Impact",
                status=DimensionStatus.UNAVAILABLE,
                data_sufficiency=DataSufficiency.UNAVAILABLE,
                score=None,
                severity=RiskClassification.MODERATE,
                evidence_quality="low",
                explanations=["No changed files available to construct dependency graph."],
                limitations=["Repository dependency graph unavailable without changed files."],
            )

        direct_modules = [str(f.get("filename") or "") for f in files_data if f.get("filename")]
        edges: list[dict[str, str]] = []
        indirect_consumers: set[str] = set()
        unresolved_imports: list[str] = []
        unsupported_langs: list[str] = []
        manifest_changes: list[str] = []
        findings: list[FindingSpec] = []

        for item in files_data:
            fname = str(item.get("filename") or "")
            patch = str(item.get("patch") or "")
            if fname.endswith(("package.json", "package-lock.json", "requirements.txt", "pyproject.toml")):
                manifest_changes.append(fname)
            if fname.endswith(".py"):
                # Parse imports from added/modified lines
                try:
                    tree = ast.parse(patch) if not patch.startswith("@@") else None
                    if tree is not None:
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Import):
                                for alias in node.names:
                                    edges.append({"source": fname, "target": alias.name})
                            elif isinstance(node, ast.ImportFrom) and node.module:
                                edges.append({"source": fname, "target": node.module})
                except SyntaxError:
                    for m in _PY_IMPORT_RE.finditer(patch):
                        mod = m.group(1) or m.group(2)
                        if mod:
                            edges.append({"source": fname, "target": mod})
            elif fname.endswith((".ts", ".tsx", ".js", ".jsx")):
                for m in _JS_IMPORT_RE.finditer(patch):
                    edges.append({"source": fname, "target": m.group(1)})
            elif not fname.endswith((".md", ".json", ".yml", ".yaml", ".txt")):
                unsupported_langs.append(fname)

        # Cross-reference repo_files if available to find downstream consumers importing direct_modules
        if repo_files:
            stem_map = {
                dm: dm.replace("/", ".").removesuffix(".py").removesuffix(".ts").removesuffix(".tsx")
                for dm in direct_modules
            }
            for rpath, content in repo_files.items():
                if rpath in direct_modules:
                    continue
                for dm, mod_stem in stem_map.items():
                    short = dm.rsplit("/", 1)[-1].split(".")[0]
                    trimmed = mod_stem.removeprefix("backend.").removeprefix("frontend.")
                    if (
                        mod_stem in content
                        or trimmed in content
                        or (len(short) > 4 and (f"import {short}" in content or f"from {short}" in content))
                    ):
                        indirect_consumers.add(rpath)
                        edges.append({"source": rpath, "target": dm})

        if manifest_changes:
            findings.append(
                FindingSpec(
                    dimension_key="dependency_impact",
                    finding_type="dependency_manifest_changed",
                    title="External dependency manifest modified",
                    description=f"Package manifest(s) changed: {', '.join(manifest_changes)}. Vulnerability feed verification unavailable offline.",
                    severity=RiskClassification.ELEVATED,
                    affected_module=manifest_changes[0],
                    source_reference=manifest_changes[0],
                )
            )

        score = min(
            100,
            15 + len(direct_modules) * 4 + len(indirect_consumers) * 8 + len(manifest_changes) * 15,
        )
        limitations = [
            "External CVE/advisory feed unavailable; dependency vulnerability status not verified."
        ]
        if unsupported_langs:
            limitations.append(
                f"Static import parsing unsupported for {len(unsupported_langs)} file(s): {', '.join(unsupported_langs[:3])}."
            )

        return AnalyzerResult(
            dimension_key="dependency_impact",
            dimension_name="Dependency & Affected-Module Impact",
            status=DimensionStatus.EVALUATED,
            data_sufficiency=DataSufficiency.SUFFICIENT if repo_files else DataSufficiency.LIMITED,
            score=score,
            severity=score_to_classification(score),
            evidence_quality="high" if repo_files else "medium",
            explanations=[
                f"Identified {len(direct_modules)} directly modified module(s), {len(indirect_consumers)} downstream consumer(s), and {len(edges)} dependency edge(s)."
            ],
            limitations=limitations,
            findings=findings,
            details={
                "direct_modules": direct_modules,
                "indirect_consumers": sorted(indirect_consumers),
                "edges": edges[:100],
                "manifest_changes": manifest_changes,
                "unresolved_imports": unresolved_imports,
            },
        )


class HistoricalFailureAnalyzer:
    """Analyzes persisted Release, RolloutHistory, and Experiment outcomes without inventing history."""

    @staticmethod
    async def analyze(
        session: AsyncSession,
        *,
        repository_id: Any = None,
        feature_flag_id: Any = None,
        changed_files: list[str] | None = None,
    ) -> AnalyzerResult:
        rel_stmt = select(Release)
        if repository_id is not None:
            rel_stmt = rel_stmt.where(Release.repository_id == repository_id)
        releases = list((await session.execute(rel_stmt)).scalars().all())

        roll_stmt = select(RolloutHistory)
        if feature_flag_id is not None:
            roll_stmt = roll_stmt.where(RolloutHistory.feature_flag_id == feature_flag_id)
        rollouts = list((await session.execute(roll_stmt)).scalars().all())

        exp_stmt = select(Experiment)
        if feature_flag_id is not None:
            exp_stmt = exp_stmt.where(Experiment.feature_flag_id == feature_flag_id)
        experiments = list((await session.execute(exp_stmt)).scalars().all())

        total_records = len(releases) + len(rollouts) + len(experiments)
        if total_records < 2:
            return AnalyzerResult(
                dimension_key="historical_failures",
                dimension_name="Historical Failure Patterns",
                status=DimensionStatus.UNAVAILABLE,
                data_sufficiency=DataSufficiency.INSUFFICIENT_DATA,
                score=None,
                severity=RiskClassification.MODERATE,
                evidence_quality="low",
                explanations=[
                    f"Insufficient historical data ({total_records} record(s) found); historical failure rate cannot be computed reliably."
                ],
                limitations=[
                    "Historical failure analysis requires at least 2 historical release, rollout, or experiment records."
                ],
                details={
                    "records_inspected": total_records,
                    "releases_count": len(releases),
                    "rollouts_count": len(rollouts),
                    "experiments_count": len(experiments),
                },
            )

        failed_releases = [
            r for r in releases if r.status in {ReleaseStatus.FAILED, ReleaseStatus.ROLLED_BACK}
        ]
        rollbacks = [r for r in rollouts if r.action == RolloutAction.ROLLBACK]
        breached_exps = [
            e
            for e in experiments
            if e.latest_recommendation == ReleaseRecommendation.RECOMMEND_ROLLBACK
            or e.status == ExperimentStatus.ROLLED_BACK
        ]

        failure_events = len(failed_releases) + len(rollbacks) + len(breached_exps)
        failure_ratio = failure_events / max(1, total_records)
        score = min(100, round(failure_ratio * 85) + (15 if failure_events >= 2 else 0))

        # Enrich with Phase 5 RepositoryIntelligenceProfile & ReleaseFailurePattern if available
        from app.models import ReleaseFailurePattern, RepositoryIntelligenceProfile

        repo_profile = None
        if repository_id is not None:
            repo_profile = (
                await session.execute(
                    select(RepositoryIntelligenceProfile)
                    .where(RepositoryIntelligenceProfile.repository_id == repository_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
        matching_patterns: list[ReleaseFailurePattern] = []
        if repository_id is not None:
            matching_patterns = list(
                (
                    await session.execute(
                        select(ReleaseFailurePattern)
                        .where(ReleaseFailurePattern.repository_id == repository_id)
                        .limit(5)
                    )
                )
                .scalars()
                .all()
            )
        if repo_profile and repo_profile.historical_failure_rate >= 0.25 and repo_profile.release_count >= 2:
            score = min(100, score + 10)

        findings: list[FindingSpec] = []
        if failure_events > 0:
            findings.append(
                FindingSpec(
                    dimension_key="historical_failures",
                    finding_type="historical_failure_pattern",
                    title=f"{failure_events} historical failure/rollback signal(s) observed",
                    description=(
                        f"Across {total_records} historical records ({len(releases)} releases, "
                        f"{len(rollouts)} rollouts, {len(experiments)} experiments), "
                        f"{len(failed_releases)} failed/rolled-back releases, {len(rollbacks)} flag rollbacks, "
                        f"and {len(breached_exps)} guardrail breaches were recorded."
                    ),
                    severity=score_to_classification(score),
                    source_reference="releases/rollout_history/experiments",
                )
            )

        return AnalyzerResult(
            dimension_key="historical_failures",
            dimension_name="Historical Failure Patterns",
            status=DimensionStatus.EVALUATED,
            data_sufficiency=(
                DataSufficiency.SUFFICIENT if total_records >= 5 else DataSufficiency.LIMITED
            ),
            score=score,
            severity=score_to_classification(score),
            evidence_quality="high" if total_records >= 5 else "medium",
            explanations=[
                f"Evaluated {total_records} persisted records: {failure_events} failure/rollback/guardrail events found (correlation != causation)."
            ],
            findings=findings,
            details={
                "records_inspected": total_records,
                "failed_releases": len(failed_releases),
                "rollbacks": len(rollbacks),
                "guardrail_breaches": len(breached_exps),
                "changed_files_context": changed_files or [],
                "repo_intelligence_failure_rate": (
                    repo_profile.historical_failure_rate if repo_profile else None
                ),
                "matching_failure_patterns": [p.pattern_key for p in matching_patterns],
            },
        )
