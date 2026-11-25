"""Deterministic Release Readiness & Verification Engine."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models import (
    ActorType,
    AnalysisStatus,
    Approval,
    ApprovalStatus,
    CodeChange,
    Experiment,
    ExperimentStatus,
    FeatureFlag,
    PullRequest,
    Release,
    ReleaseCheck,
    ReleaseCheckStatus,
    ReleaseReadinessStatus,
    ReleaseRecommendation,
    ReleaseType,
    RiskLevel,
    ValidationStatus,
)
from app.services.notification_service import broadcast_workspace_event
from app.services.policy_engine import is_production_environment
from app.services.release_state_machine import record_release_event


class ReleaseReadinessService:
    """Computes explainable readiness scores, checks, and blockers for a Release."""

    @staticmethod
    async def evaluate_release(
        session: AsyncSession,
        release: Release,
        *,
        actor_id: uuid.UUID | None = None,
        actor_type: ActorType = ActorType.SYSTEM,
        emit_event: bool = True,
    ) -> dict[str, Any]:
        now = utcnow()

        pr: PullRequest | None = None
        code_changes: list[CodeChange] = []
        if release.pull_request_id is not None:
            pr = (
                await session.execute(
                    select(PullRequest).where(PullRequest.id == release.pull_request_id)
                )
            ).scalar_one_or_none()
            code_changes = list(
                (
                    await session.execute(
                        select(CodeChange)
                        .where(CodeChange.pull_request_id == release.pull_request_id)
                        .order_by(CodeChange.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )

        flag: FeatureFlag | None = None
        if release.feature_flag_id is not None:
            flag = (
                await session.execute(
                    select(FeatureFlag).where(FeatureFlag.id == release.feature_flag_id)
                )
            ).scalar_one_or_none()

        experiment: Experiment | None = None
        if release.experiment_id is not None:
            experiment = (
                await session.execute(
                    select(Experiment).where(Experiment.id == release.experiment_id)
                )
            ).scalar_one_or_none()

        approvals = list(
            (
                await session.execute(
                    select(Approval)
                    .where(Approval.release_id == release.id)
                    .order_by(Approval.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        current_approvals = [
            a
            for a in approvals
            if a.release_version is None or a.release_version == release.version
        ]

        checks_spec: list[dict[str, Any]] = []

        # 1. PR & Risk Analysis (20 pts)
        if pr is None:
            if release.release_type in {ReleaseType.CONFIG, ReleaseType.ROLLBACK_RECOVERY}:
                checks_spec.append(
                    {
                        "check_key": "pr_analysis",
                        "category": "pr",
                        "title": "PR & Risk Analysis",
                        "status": ReleaseCheckStatus.SKIPPED,
                        "is_blocking": False,
                        "weight": 20,
                        "message": "No pull request required for this release type.",
                        "details_json": {},
                    }
                )
            else:
                checks_spec.append(
                    {
                        "check_key": "pr_analysis",
                        "category": "pr",
                        "title": "PR & Risk Analysis",
                        "status": ReleaseCheckStatus.WARNING,
                        "is_blocking": False,
                        "weight": 20,
                        "message": "No pull request linked yet; manual release context only.",
                        "details_json": {},
                    }
                )
        elif pr.analysis_status == AnalysisStatus.FAILED:
            checks_spec.append(
                {
                    "check_key": "pr_analysis",
                    "category": "pr",
                    "title": "PR & Risk Analysis",
                    "status": ReleaseCheckStatus.FAILED,
                    "is_blocking": True,
                    "weight": 20,
                    "message": f"PR #{pr.github_pr_number} analysis failed.",
                    "details_json": {"pr_number": pr.github_pr_number, "analysis_status": pr.analysis_status.value},
                }
            )
        elif pr.analysis_status != AnalysisStatus.COMPLETED:
            checks_spec.append(
                {
                    "check_key": "pr_analysis",
                    "category": "pr",
                    "title": "PR & Risk Analysis",
                    "status": ReleaseCheckStatus.PENDING,
                    "is_blocking": True,
                    "weight": 20,
                    "message": f"PR #{pr.github_pr_number} analysis has not completed yet.",
                    "details_json": {"pr_number": pr.github_pr_number, "analysis_status": pr.analysis_status.value},
                }
            )
        elif pr.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            checks_spec.append(
                {
                    "check_key": "pr_analysis",
                    "category": "pr",
                    "title": "PR & Risk Analysis",
                    "status": ReleaseCheckStatus.WARNING,
                    "is_blocking": False,
                    "weight": 20,
                    "message": f"PR #{pr.github_pr_number} analyzed with {pr.risk_level.value} risk; safeguard recommended.",
                    "details_json": {"pr_number": pr.github_pr_number, "risk_level": pr.risk_level.value},
                }
            )
        else:
            checks_spec.append(
                {
                    "check_key": "pr_analysis",
                    "category": "pr",
                    "title": "PR & Risk Analysis",
                    "status": ReleaseCheckStatus.PASSED,
                    "is_blocking": False,
                    "weight": 20,
                    "message": f"PR #{pr.github_pr_number} analysis completed ({pr.risk_level.value} risk).",
                    "details_json": {"pr_number": pr.github_pr_number, "risk_level": pr.risk_level.value},
                }
            )

        # 2. Code Patch Validation (20 pts)
        if not code_changes:
            checks_spec.append(
                {
                    "check_key": "patch_validation",
                    "category": "validation",
                    "title": "Code Patch Validation",
                    "status": ReleaseCheckStatus.SKIPPED,
                    "is_blocking": False,
                    "weight": 20,
                    "message": "No generated code patch attached to this release.",
                    "details_json": {},
                }
            )
        else:
            latest_change = code_changes[0]
            if latest_change.validation_status == ValidationStatus.PASSED:
                checks_spec.append(
                    {
                        "check_key": "patch_validation",
                        "category": "validation",
                        "title": "Code Patch Validation",
                        "status": ReleaseCheckStatus.PASSED,
                        "is_blocking": False,
                        "weight": 20,
                        "message": "Isolated patch validation passed.",
                        "details_json": {"code_change_id": str(latest_change.id)},
                    }
                )
            elif latest_change.validation_status == ValidationStatus.FAILED:
                checks_spec.append(
                    {
                        "check_key": "patch_validation",
                        "category": "validation",
                        "title": "Code Patch Validation",
                        "status": ReleaseCheckStatus.FAILED,
                        "is_blocking": True,
                        "weight": 20,
                        "message": "Isolated patch validation failed; fix code change before rollout.",
                        "details_json": {"code_change_id": str(latest_change.id)},
                    }
                )
            else:
                checks_spec.append(
                    {
                        "check_key": "patch_validation",
                        "category": "validation",
                        "title": "Code Patch Validation",
                        "status": ReleaseCheckStatus.PENDING,
                        "is_blocking": True,
                        "weight": 20,
                        "message": "Generated code patch has not been validated yet.",
                        "details_json": {"code_change_id": str(latest_change.id)},
                    }
                )

        # 3. Feature Flag Readiness (20 pts)
        is_prod = is_production_environment(release.target_environment)
        if flag is None:
            checks_spec.append(
                {
                    "check_key": "flag_readiness",
                    "category": "flag",
                    "title": "Feature Flag Protection",
                    "status": ReleaseCheckStatus.PENDING if is_prod else ReleaseCheckStatus.WARNING,
                    "is_blocking": is_prod,
                    "weight": 20,
                    "message": (
                        "Production release requires a linked Flagsmith feature flag."
                        if is_prod
                        else "No feature flag linked; consider wrapping risky changes."
                    ),
                    "details_json": {"target_environment": release.target_environment},
                }
            )
        elif flag.is_archived:
            checks_spec.append(
                {
                    "check_key": "flag_readiness",
                    "category": "flag",
                    "title": "Feature Flag Protection",
                    "status": ReleaseCheckStatus.FAILED,
                    "is_blocking": True,
                    "weight": 20,
                    "message": f"Linked feature flag '{flag.key}' is archived.",
                    "details_json": {"flag_key": flag.key},
                }
            )
        else:
            checks_spec.append(
                {
                    "check_key": "flag_readiness",
                    "category": "flag",
                    "title": "Feature Flag Protection",
                    "status": ReleaseCheckStatus.PASSED,
                    "is_blocking": False,
                    "weight": 20,
                    "message": f"Feature flag '{flag.key}' is active and ready.",
                    "details_json": {"flag_key": flag.key, "flag_id": str(flag.id)},
                }
            )

        # 4. Experiment & Guardrails (15 pts)
        if experiment is None:
            checks_spec.append(
                {
                    "check_key": "experiment_guardrails",
                    "category": "experiment",
                    "title": "Experiment & Guardrail Health",
                    "status": ReleaseCheckStatus.SKIPPED,
                    "is_blocking": False,
                    "weight": 15,
                    "message": "No experiment attached to this release.",
                    "details_json": {},
                }
            )
        elif (
            experiment.latest_recommendation == ReleaseRecommendation.RECOMMEND_ROLLBACK
            or experiment.status == ExperimentStatus.ROLLED_BACK
        ):
            checks_spec.append(
                {
                    "check_key": "experiment_guardrails",
                    "category": "experiment",
                    "title": "Experiment & Guardrail Health",
                    "status": ReleaseCheckStatus.FAILED,
                    "is_blocking": True,
                    "weight": 15,
                    "message": (
                        f"Experiment '{experiment.name}' recommends ROLLBACK due to guardrail or regression signal."
                    ),
                    "details_json": {
                        "experiment_id": str(experiment.id),
                        "recommendation": experiment.latest_recommendation.value
                        if experiment.latest_recommendation
                        else None,
                    },
                }
            )
        elif experiment.latest_recommendation == ReleaseRecommendation.EXPAND_ROLLOUT:
            checks_spec.append(
                {
                    "check_key": "experiment_guardrails",
                    "category": "experiment",
                    "title": "Experiment & Guardrail Health",
                    "status": ReleaseCheckStatus.PASSED,
                    "is_blocking": False,
                    "weight": 15,
                    "message": f"Experiment '{experiment.name}' recommends Expand Rollout with healthy guardrails.",
                    "details_json": {"experiment_id": str(experiment.id)},
                }
            )
        else:
            checks_spec.append(
                {
                    "check_key": "experiment_guardrails",
                    "category": "experiment",
                    "title": "Experiment & Guardrail Health",
                    "status": ReleaseCheckStatus.WARNING,
                    "is_blocking": False,
                    "weight": 15,
                    "message": f"Experiment '{experiment.name}' is still collecting data ({experiment.status.value}).",
                    "details_json": {"experiment_id": str(experiment.id)},
                }
            )

        # 5. Human Approval & Policy Gate (25 pts)
        approval_required = is_prod or release.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        approved = next(
            (
                a
                for a in current_approvals
                if a.status == ApprovalStatus.EXECUTED
                and (a.decided_by_id is None or a.decided_by_id != a.requested_by_id)
            ),
            None,
        )
        rejected = next(
            (a for a in current_approvals if a.status == ApprovalStatus.REJECTED), None
        )
        pending_appr = next(
            (a for a in current_approvals if a.status == ApprovalStatus.PENDING), None
        )

        if approved is not None:
            checks_spec.append(
                {
                    "check_key": "approval_gate",
                    "category": "approval",
                    "title": "Human Approval Gate",
                    "status": ReleaseCheckStatus.PASSED,
                    "is_blocking": False,
                    "weight": 25,
                    "message": f"Approval {approved.id} granted for release version v{release.version}.",
                    "details_json": {"approval_id": str(approved.id)},
                }
            )
        elif rejected is not None:
            checks_spec.append(
                {
                    "check_key": "approval_gate",
                    "category": "approval",
                    "title": "Human Approval Gate",
                    "status": ReleaseCheckStatus.FAILED,
                    "is_blocking": True,
                    "weight": 25,
                    "message": "Latest release approval request was rejected.",
                    "details_json": {"approval_id": str(rejected.id)},
                }
            )
        elif approval_required:
            checks_spec.append(
                {
                    "check_key": "approval_gate",
                    "category": "approval",
                    "title": "Human Approval Gate",
                    "status": ReleaseCheckStatus.PENDING,
                    "is_blocking": True,
                    "weight": 25,
                    "message": (
                        "Approval request is pending human decision."
                        if pending_appr
                        else "Human approval required before rollout."
                    ),
                    "details_json": {
                        "pending_approval_id": str(pending_appr.id) if pending_appr else None
                    },
                }
            )
        else:
            checks_spec.append(
                {
                    "check_key": "approval_gate",
                    "category": "approval",
                    "title": "Human Approval Gate",
                    "status": ReleaseCheckStatus.PASSED,
                    "is_blocking": False,
                    "weight": 25,
                    "message": "Low-risk non-production release does not require elevated approval.",
                    "details_json": {},
                }
            )

        # Persist ReleaseCheck rows
        existing_checks = {
            c.check_key: c
            for c in (
                await session.execute(
                    select(ReleaseCheck).where(ReleaseCheck.release_id == release.id)
                )
            )
            .scalars()
            .all()
        }
        for item in checks_spec:
            chk = existing_checks.get(item["check_key"])
            if chk is None:
                chk = ReleaseCheck(release_id=release.id, check_key=item["check_key"])
                session.add(chk)
            chk.category = item["category"]
            chk.title = item["title"]
            chk.status = item["status"]
            chk.is_blocking = item["is_blocking"]
            chk.weight = item["weight"]
            chk.message = item["message"]
            chk.details_json = item["details_json"]
            chk.evaluated_at = now
        await session.flush()

        # Compute deterministic weighted score & summary
        active_checks = [c for c in checks_spec if c["status"] != ReleaseCheckStatus.SKIPPED]
        total_weight = sum(c["weight"] for c in active_checks)
        earned = 0.0
        for c in active_checks:
            if c["status"] == ReleaseCheckStatus.PASSED:
                earned += c["weight"]
            elif c["status"] == ReleaseCheckStatus.WARNING:
                earned += c["weight"] * 0.6
        raw_score = round((earned / total_weight) * 100) if total_weight > 0 else 50

        blocking_failures = [
            c for c in checks_spec if c["status"] == ReleaseCheckStatus.FAILED and c["is_blocking"]
        ]
        blocking_pending = [
            c for c in checks_spec if c["status"] == ReleaseCheckStatus.PENDING and c["is_blocking"]
        ]
        warnings = [c for c in checks_spec if c["status"] == ReleaseCheckStatus.WARNING]
        passed = [c for c in checks_spec if c["status"] == ReleaseCheckStatus.PASSED]

        if blocking_failures:
            readiness_status = ReleaseReadinessStatus.BLOCKED
            score = min(raw_score, 49)
        elif blocking_pending:
            readiness_status = ReleaseReadinessStatus.NOT_READY
            score = min(raw_score, 69)
        elif warnings:
            readiness_status = ReleaseReadinessStatus.NEEDS_ATTENTION
            score = raw_score
        else:
            readiness_status = ReleaseReadinessStatus.READY
            score = raw_score

        recommended_actions: list[str] = []
        for c in blocking_failures + blocking_pending + warnings:
            if c["message"]:
                recommended_actions.append(c["message"])

        summary = {
            "score": score,
            "status": readiness_status.value,
            "blocking_issues": [c["message"] for c in blocking_failures],
            "missing_checks": [c["message"] for c in blocking_pending],
            "warnings": [c["message"] for c in warnings],
            "passed_checks": [c["check_key"] for c in passed],
            "failed_checks": [c["check_key"] for c in blocking_failures],
            "recommended_actions": recommended_actions,
            "evaluated_at": now.isoformat(),
        }

        release.readiness_score = score
        release.readiness_status = readiness_status
        release.readiness_summary = summary
        release.updated_at = now

        if emit_event:
            await record_release_event(
                session,
                release,
                event_type="release.readiness_evaluated",
                title=f"Readiness evaluated: {readiness_status.value.upper()} ({score}/100)",
                message="; ".join(recommended_actions[:3]) or "All checks passed.",
                actor_id=actor_id,
                actor_type=actor_type,
                payload={"score": score, "status": readiness_status.value},
            )
            await broadcast_workspace_event(
                "release.readiness_updated",
                {
                    "release_id": str(release.id),
                    "release_key": release.release_key,
                    "readiness_score": score,
                    "readiness_status": readiness_status.value,
                },
            )

        return summary
