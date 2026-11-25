"""Repository-Specific & Dependency Failure Intelligence Services (Phase 5)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models import (
    PullRequest,
    Release,
    ReleaseOutcome,
    ReleaseOutcomeType,
    ReleaseRiskAssessment,
    Repository,
    RepositoryIntelligenceProfile,
    RiskFinding,
)


class RepositoryIntelligenceService:
    """Maintains longitudinal risk & reliability intelligence profiles per Repository."""

    @staticmethod
    async def refresh_profile(
        session: AsyncSession, repository: Repository
    ) -> RepositoryIntelligenceProfile:
        outcomes = list(
            (
                await session.execute(
                    select(ReleaseOutcome).where(ReleaseOutcome.repository_id == repository.id)
                )
            )
            .scalars()
            .all()
        )
        qualifying = [
            o
            for o in outcomes
            if o.outcome_type
            in (
                ReleaseOutcomeType.SUCCESS,
                ReleaseOutcomeType.PARTIAL_SUCCESS,
                ReleaseOutcomeType.FAILURE,
                ReleaseOutcomeType.ROLLBACK,
            )
        ]
        failures = [
            o
            for o in qualifying
            if o.outcome_type in (ReleaseOutcomeType.FAILURE, ReleaseOutcomeType.ROLLBACK)
        ]
        rollbacks = [o for o in qualifying if o.rollback_required]
        failure_rate = round(len(failures) / len(qualifying), 4) if qualifying else 0.0

        rec_vals = [o.recovery_duration_seconds for o in failures if o.recovery_duration_seconds is not None]
        avg_rec = round(sum(rec_vals) / len(rec_vals), 1) if rec_vals else None

        cat_counts: dict[str, int] = defaultdict(int)
        for f in failures:
            if f.failure_category:
                cat_counts[f.failure_category.value] += 1
        common_cats = [
            {"category": k, "count": v}
            for k, v in sorted(cat_counts.items(), key=lambda kv: kv[1], reverse=True)
        ]

        # Inspect modules from linked PRs
        releases = list(
            (
                await session.execute(
                    select(Release).where(Release.repository_id == repository.id)
                )
            )
            .scalars()
            .all()
        )
        rel_outcome_map = {o.release_id: o for o in outcomes}
        mod_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "failures": 0})
        for rel in releases:
            if not rel.pull_request_id:
                continue
            pr = await session.get(PullRequest, rel.pull_request_id)
            if not pr or not isinstance(pr.confirmed_risks, list):
                continue
            out = rel_outcome_map.get(rel.id)
            is_fail = out is not None and out.outcome_type in (
                ReleaseOutcomeType.FAILURE,
                ReleaseOutcomeType.ROLLBACK,
            )
            modules = {
                str(f.get("file_path") or f.get("filename") or "").split("/")[0]
                for f in pr.confirmed_risks
                if isinstance(f, dict) and (f.get("file_path") or f.get("filename"))
            }
            for mod in modules:
                mod_stats[mod]["total"] += 1
                if is_fail:
                    mod_stats[mod]["failures"] += 1

        high_risk_modules = [
            {
                "module": mod,
                "release_count": st["total"],
                "failure_count": st["failures"],
                "failure_rate": round(st["failures"] / st["total"], 4) if st["total"] else 0.0,
            }
            for mod, st in sorted(
                mod_stats.items(), key=lambda kv: (kv[1]["failures"], kv[1]["total"]), reverse=True
            )[:10]
        ]

        recommendations: list[dict[str, Any]] = []
        if failure_rate >= 0.2 and len(qualifying) >= 2:
            recommendations.append(
                {
                    "title": "Increase progressive rollout observation window",
                    "rationale": f"Historical failure rate is {failure_rate:.0%} across {len(qualifying)} qualifying releases.",
                    "policy_area": "observation_windows",
                }
            )
        if any(c["category"] == "payment_failure" for c in common_cats):
            recommendations.append(
                {
                    "title": "Mandate payment_failed guardrail & checkout integration tests",
                    "rationale": "Historical payment failures observed in this repository.",
                    "policy_area": "guardrail_thresholds",
                }
            )
        if high_risk_modules and high_risk_modules[0]["failure_count"] >= 1:
            top_mod = high_risk_modules[0]["module"]
            recommendations.append(
                {
                    "title": f"Require feature flag & additional review for `{top_mod}/`",
                    "rationale": f"Module `{top_mod}/` appeared in {high_risk_modules[0]['failure_count']} failed releases.",
                    "policy_area": "feature_flag_usage",
                }
            )

        existing = (
            await session.execute(
                select(RepositoryIntelligenceProfile)
                .where(RepositoryIntelligenceProfile.repository_id == repository.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        now = utcnow()
        risk_ind = {
            "sample_size": len(qualifying),
            "data_sufficiency": "sufficient" if len(qualifying) >= 3 else "limited",
            "default_branch": repository.default_branch,
        }

        if existing is None:
            existing = RepositoryIntelligenceProfile(
                repository_id=repository.id,
                release_count=len(qualifying),
                failure_count=len(failures),
                rollback_count=len(rollbacks),
                historical_failure_rate=failure_rate,
                avg_recovery_seconds=avg_rec,
                common_failure_categories=common_cats,
                high_risk_modules=high_risk_modules,
                dependency_patterns=[],
                risk_indicators=risk_ind,
                recommendations=recommendations,
                last_analyzed_at=now,
                version=1,
            )
            session.add(existing)
        else:
            existing.release_count = len(qualifying)
            existing.failure_count = len(failures)
            existing.rollback_count = len(rollbacks)
            existing.historical_failure_rate = failure_rate
            existing.avg_recovery_seconds = avg_rec
            existing.common_failure_categories = common_cats
            existing.high_risk_modules = high_risk_modules
            existing.risk_indicators = risk_ind
            existing.recommendations = recommendations
            existing.last_analyzed_at = now
            existing.version += 1
        await session.flush()
        return existing

    @staticmethod
    async def refresh_all(session: AsyncSession) -> list[RepositoryIntelligenceProfile]:
        repos = list((await session.execute(select(Repository))).scalars().all())
        profiles: list[RepositoryIntelligenceProfile] = []
        for repo in repos:
            profiles.append(await RepositoryIntelligenceService.refresh_profile(session, repo))
        return profiles


class DependencyIntelligenceService:
    """Correlates dependency mentions in PRs and RiskFindings with ReleaseOutcomes."""

    @staticmethod
    async def analyze_dependencies(
        session: AsyncSession,
        *,
        dependency_name: str | None = None,
    ) -> list[dict[str, Any]]:
        outcomes = list((await session.execute(select(ReleaseOutcome))).scalars().all())
        outcome_by_rel = {o.release_id: o for o in outcomes}
        releases = list((await session.execute(select(Release))).scalars().all())

        dep_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "package": "",
                "release_count": 0,
                "failure_count": 0,
                "rollback_count": 0,
                "release_ids": [],
                "repositories": set(),
            }
        )

        for rel in releases:
            out = outcome_by_rel.get(rel.id)
            deps_found: set[str] = set()

            # Check risk assessments for dependency findings
            assessments = list(
                (
                    await session.execute(
                        select(ReleaseRiskAssessment).where(
                            ReleaseRiskAssessment.release_id == rel.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for a in assessments:
                findings = list(
                    (
                        await session.execute(
                            select(RiskFinding).where(RiskFinding.assessment_id == a.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                for f in findings:
                    if f.dimension_key == "dependency_impact" or "package" in f.title.lower() or "dependen" in f.title.lower():
                        # Extract dependency token from evidence or title
                        for pkg_tok in (f.file_path or "", f.title):
                            clean = pkg_tok.strip()
                            if clean and ("requirements" in clean or "package.json" in clean or "@" in clean or "==" in clean):
                                deps_found.add(clean[:80])

            # Check PR confirmed_risks for dependency manifests
            if rel.pull_request_id:
                pr = await session.get(PullRequest, rel.pull_request_id)
                if pr and isinstance(pr.confirmed_risks, list):
                    for cf in pr.confirmed_risks:
                        if isinstance(cf, dict):
                            fname = str(cf.get("file_path") or cf.get("filename") or "")
                            if any(
                                fname.endswith(m)
                                for m in ("requirements.txt", "pyproject.toml", "package.json", "package-lock.json")
                            ):
                                deps_found.add(fname)

            is_fail = out is not None and out.outcome_type in (
                ReleaseOutcomeType.FAILURE,
                ReleaseOutcomeType.ROLLBACK,
            )
            is_rb = out is not None and out.rollback_required
            for dep in deps_found:
                if dependency_name and dependency_name.lower() not in dep.lower():
                    continue
                st = dep_stats[dep]
                st["package"] = dep
                st["release_count"] += 1
                if is_fail:
                    st["failure_count"] += 1
                if is_rb:
                    st["rollback_count"] += 1
                st["release_ids"].append(str(rel.id))
                if rel.repository_id:
                    st["repositories"].add(str(rel.repository_id))

        results: list[dict[str, Any]] = []
        for dep, st in dep_stats.items():
            rc = st["release_count"]
            fc = st["failure_count"]
            results.append(
                {
                    "dependency": dep,
                    "release_count": rc,
                    "failure_count": fc,
                    "rollback_count": st["rollback_count"],
                    "failure_rate": round(fc / rc, 4) if rc > 0 else 0.0,
                    "repositories": sorted(st["repositories"]),
                    "release_ids": st["release_ids"][:20],
                    "recommendation": (
                        "Add compatibility tests and review dependency changes before staging."
                        if fc > 0
                        else "Monitor dependency manifests."
                    ),
                }
            )
        results.sort(key=lambda r: (r["failure_count"], r["release_count"]), reverse=True)
        return results
