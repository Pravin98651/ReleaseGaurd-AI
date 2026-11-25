"""Release Lifecycle Orchestrator: creation, stage execution, AI assist, recovery, and reporting."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.specialists.pr_agent import analyze_pr_with_static_and_gemini
from app.agents.tools.context import ToolExecutionContext
from app.agents.tools.pr_tools import handle_validate_patch_isolated
from app.core.database import utcnow
from app.core.security import compute_action_hash, redact_dict, scrub_secrets
from app.models import (
    ActorType,
    Approval,
    ApprovalCategory,
    ApprovalStatus,
    AuditLog,
    AuditStatus,
    CodeChange,
    Experiment,
    FeatureFlag,
    NotificationCategory,
    PolicyCategory,
    PullRequest,
    Release,
    ReleaseArtifact,
    ReleaseArtifactType,
    ReleaseStage,
    ReleaseStageKey,
    ReleaseStageStatus,
    ReleaseStatus,
    ReleaseType,
    Repository,
    RiskLevel,
    ValidationStatus,
)
from app.schemas.tools import ValidatePatchIsolatedInput
from app.services.notification_service import (
    broadcast_workspace_event,
    create_notification,
)
from app.services.policy_engine import is_production_environment
from app.services.release_readiness import ReleaseReadinessService
from app.services.release_state_machine import (
    ACTIVE_EXECUTION_STATES,
    TERMINAL_RELEASE_STATES,
    ReleaseTransitionError,
    record_release_event,
    transition_release,
)

logger = logging.getLogger("releaseguard.release_orchestrator")

STAGE_DEFINITIONS: list[tuple[ReleaseStageKey, int, str]] = [
    (ReleaseStageKey.DRAFT, 1, "Draft & Scope"),
    (ReleaseStageKey.PR_ANALYSIS, 2, "PR & Risk Analysis"),
    (ReleaseStageKey.PATCH_VALIDATION, 3, "Patch Validation"),
    (ReleaseStageKey.FLAG_SETUP, 4, "Feature Flag Setup"),
    (ReleaseStageKey.EXPERIMENT_MONITORING, 5, "Experiment & Guardrails"),
    (ReleaseStageKey.APPROVAL_GATE, 6, "Human Approval Gate"),
    (ReleaseStageKey.ROLLOUT_EXECUTION, 7, "Rollout Execution"),
    (ReleaseStageKey.POST_RELEASE_VERIFICATION, 8, "Post-Release Verification"),
    (ReleaseStageKey.COMPLETED, 9, "Completed"),
]

_STAGE_TO_RECOVERY_STATUS: dict[ReleaseStageKey, ReleaseStatus] = {
    ReleaseStageKey.DRAFT: ReleaseStatus.DRAFT,
    ReleaseStageKey.PR_ANALYSIS: ReleaseStatus.ANALYZING,
    ReleaseStageKey.PATCH_VALIDATION: ReleaseStatus.VALIDATING,
    ReleaseStageKey.FLAG_SETUP: ReleaseStatus.FLAG_PROVISIONING,
    ReleaseStageKey.EXPERIMENT_MONITORING: ReleaseStatus.EXPERIMENT_RUNNING,
    ReleaseStageKey.APPROVAL_GATE: ReleaseStatus.AWAITING_APPROVAL,
    ReleaseStageKey.ROLLOUT_EXECUTION: ReleaseStatus.ROLLING_OUT,
    ReleaseStageKey.POST_RELEASE_VERIFICATION: ReleaseStatus.MONITORING,
    ReleaseStageKey.COMPLETED: ReleaseStatus.COMPLETED,
}


class ReleaseOrchestrator:
    """Coordinates end-to-end Release lifecycle operations using existing domain services."""

    @staticmethod
    async def _next_release_key(session: AsyncSession) -> str:
        year = utcnow().year
        count = int((await session.execute(select(func.count(Release.id)))).scalar_one() or 0)
        seq = count + 1
        while True:
            candidate = f"REL-{year}-{seq:04d}"
            exists = (
                await session.execute(
                    select(Release.id).where(Release.release_key == candidate).limit(1)
                )
            ).scalar_one_or_none()
            if exists is None:
                return candidate
            seq += 1

    @staticmethod
    async def _get_stage(
        session: AsyncSession, release_id: uuid.UUID, stage_key: ReleaseStageKey
    ) -> ReleaseStage | None:
        return (
            await session.execute(
                select(ReleaseStage).where(
                    ReleaseStage.release_id == release_id,
                    ReleaseStage.stage_key == stage_key,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def create_release(
        session: AsyncSession,
        *,
        title: str | None = None,
        description: str | None = None,
        release_type: ReleaseType = ReleaseType.FEATURE,
        target_environment: str = "development",
        repository_id: uuid.UUID | None = None,
        pull_request_id: uuid.UUID | None = None,
        feature_flag_id: uuid.UUID | None = None,
        experiment_id: uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        created_by_id: uuid.UUID | None = None,
        risk_level: RiskLevel | None = None,
        target_rollout_percentage: int = 100,
        scheduled_for: Any = None,
        ai_assist: bool = False,
        allow_duplicate: bool = False,
        gemini_client: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Release:
        pr: PullRequest | None = None
        if pull_request_id is not None:
            pr = await session.get(PullRequest, pull_request_id)
            if pr is None:
                raise ReleaseTransitionError("Pull request not found.", status_code=404)
            if repository_id is None:
                repository_id = pr.repository_id
            if not allow_duplicate:
                existing_active = (
                    await session.execute(
                        select(Release)
                        .where(
                            Release.pull_request_id == pull_request_id,
                            Release.status.notin_(list(TERMINAL_RELEASE_STATES)),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if existing_active is not None:
                    raise ReleaseTransitionError(
                        f"Active release {existing_active.release_key} already exists for PR #{pr.github_pr_number}. "
                        "Pass allow_duplicate=true to create another release.",
                        status_code=409,
                    )

        if repository_id is not None and await session.get(Repository, repository_id) is None:
            raise ReleaseTransitionError("Repository not found.", status_code=404)
        if feature_flag_id is not None and await session.get(FeatureFlag, feature_flag_id) is None:
            raise ReleaseTransitionError("Feature flag not found.", status_code=404)
        if experiment_id is not None and await session.get(Experiment, experiment_id) is None:
            raise ReleaseTransitionError("Experiment not found.", status_code=404)

        resolved_title = (title or "").strip()
        if not resolved_title:
            if pr is not None:
                resolved_title = f"Release PR #{pr.github_pr_number}: {pr.title}"[:255]
            else:
                resolved_title = "Untitled Release"

        resolved_risk = risk_level or (pr.risk_level if pr is not None else RiskLevel.MEDIUM)
        resolved_ai_summary: str | None = None
        if ai_assist and gemini_client is not None and getattr(gemini_client, "is_configured", False):
            try:
                prompt = (
                    f"Summarize release plan for '{resolved_title}' targeting '{target_environment}' "
                    f"(risk={resolved_risk.value}, type={release_type.value}). Keep it to 2 concise sentences."
                )
                resp = await gemini_client.generate_text(prompt)
                if resp:
                    resolved_ai_summary = scrub_secrets(str(resp).strip())[:2000]
            except Exception as exc:  # noqa: BLE001
                logger.debug("Optional AI release summary skipped: %s", exc)
        elif pr is not None and pr.analysis_summary:
            resolved_ai_summary = pr.analysis_summary

        release_key = await ReleaseOrchestrator._next_release_key(session)
        release = Release(
            release_key=release_key,
            title=resolved_title,
            description=description,
            release_type=release_type,
            target_environment=target_environment.strip().lower() or "development",
            status=ReleaseStatus.DRAFT,
            repository_id=repository_id,
            pull_request_id=pull_request_id,
            feature_flag_id=feature_flag_id,
            experiment_id=experiment_id,
            owner_id=owner_id or created_by_id,
            created_by_id=created_by_id,
            risk_level=resolved_risk,
            target_rollout_percentage=max(0, min(100, int(target_rollout_percentage))),
            ai_summary=resolved_ai_summary,
            scheduled_for=scheduled_for,
            metadata_json=redact_dict(dict(metadata or {})),
        )
        session.add(release)
        await session.flush()

        now = utcnow()
        for stage_key, order, stage_title in STAGE_DEFINITIONS:
            is_draft = stage_key == ReleaseStageKey.DRAFT
            session.add(
                ReleaseStage(
                    release_id=release.id,
                    stage_key=stage_key,
                    stage_order=order,
                    title=stage_title,
                    status=ReleaseStageStatus.PASSED if is_draft else ReleaseStageStatus.PENDING,
                    attempt_count=1 if is_draft else 0,
                    started_at=now if is_draft else None,
                    completed_at=now if is_draft else None,
                    summary="Release draft initialized." if is_draft else None,
                )
            )
        await session.flush()

        await ReleaseReadinessService.evaluate_release(
            session,
            release,
            actor_id=created_by_id,
            actor_type=ActorType.USER if created_by_id else ActorType.SYSTEM,
            emit_event=False,
        )

        await record_release_event(
            session,
            release,
            event_type="release.created",
            to_status=ReleaseStatus.DRAFT,
            stage_key=ReleaseStageKey.DRAFT,
            actor_id=created_by_id,
            actor_type=ActorType.USER if created_by_id else ActorType.SYSTEM,
            title=f"Release {release.release_key} created",
            message=f"Target environment: {release.target_environment} | Risk: {release.risk_level.value}",
            payload={
                "pull_request_id": str(pull_request_id) if pull_request_id else None,
                "feature_flag_id": str(feature_flag_id) if feature_flag_id else None,
                "experiment_id": str(experiment_id) if experiment_id else None,
            },
        )
        session.add(
            AuditLog(
                actor_type=ActorType.USER if created_by_id else ActorType.SYSTEM,
                actor_id=str(created_by_id) if created_by_id else None,
                event_action="release.create",
                policy_category=PolicyCategory.A,
                resource_type="release",
                resource_id=str(release.id),
                environment_name=release.target_environment,
                status=AuditStatus.SUCCESS,
                details=redact_dict(
                    {"release_key": release.release_key, "title": release.title}
                ),
            )
        )
        await broadcast_workspace_event(
            "release.created",
            {
                "release_id": str(release.id),
                "release_key": release.release_key,
                "status": release.status.value,
                "target_environment": release.target_environment,
            },
        )
        return release

    @staticmethod
    async def invalidate_stale_approvals(
        session: AsyncSession,
        release: Release,
        *,
        reason: str,
        actor_id: uuid.UUID | None = None,
        actor_type: ActorType = ActorType.SYSTEM,
    ) -> int:
        approvals = list(
            (
                await session.execute(
                    select(Approval).where(
                        Approval.release_id == release.id,
                        Approval.status.in_(
                            [ApprovalStatus.PENDING, ApprovalStatus.EXECUTED]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        invalidated = 0
        for app in approvals:
            if app.release_version is not None and app.release_version < release.version:
                app.status = ApprovalStatus.CANCELLED
                app.decision_comment = scrub_secrets(reason)
                invalidated += 1

        if invalidated > 0:
            stage = await ReleaseOrchestrator._get_stage(
                session, release.id, ReleaseStageKey.APPROVAL_GATE
            )
            if stage is not None:
                stage.status = ReleaseStageStatus.PENDING
                stage.summary = "Previous approval invalidated after material release update."
            await record_release_event(
                session,
                release,
                event_type="approval.invalidated_stale",
                stage_key=ReleaseStageKey.APPROVAL_GATE,
                actor_id=actor_id,
                actor_type=actor_type,
                title=f"Invalidated {invalidated} stale approval(s)",
                message=reason,
                payload={"version": release.version, "invalidated_count": invalidated},
            )
        return invalidated

    @staticmethod
    async def update_release(
        session: AsyncSession,
        release: Release,
        *,
        expected_version: int,
        actor_id: uuid.UUID | None = None,
        title: str | None = None,
        description: str | None = None,
        target_environment: str | None = None,
        feature_flag_id: uuid.UUID | None = None,
        experiment_id: uuid.UUID | None = None,
        risk_level: RiskLevel | None = None,
        target_rollout_percentage: int | None = None,
        scheduled_for: Any = None,
    ) -> Release:
        if release.version != expected_version:
            raise ReleaseTransitionError(
                f"Version conflict on release {release.release_key}: "
                f"expected {expected_version}, current {release.version}.",
                status_code=409,
            )
        if release.status in TERMINAL_RELEASE_STATES:
            raise ReleaseTransitionError(
                f"Cannot modify terminal release {release.release_key}.",
                status_code=422,
            )

        material_change = False
        if title is not None:
            release.title = title.strip()[:255] or release.title
        if description is not None:
            release.description = description
        if target_environment is not None:
            norm_env = target_environment.strip().lower()
            if norm_env and norm_env != release.target_environment:
                release.target_environment = norm_env
                material_change = True
        if feature_flag_id is not None and feature_flag_id != release.feature_flag_id:
            if await session.get(FeatureFlag, feature_flag_id) is None:
                raise ReleaseTransitionError("Feature flag not found.", status_code=404)
            release.feature_flag_id = feature_flag_id
            material_change = True
        if experiment_id is not None and experiment_id != release.experiment_id:
            if await session.get(Experiment, experiment_id) is None:
                raise ReleaseTransitionError("Experiment not found.", status_code=404)
            release.experiment_id = experiment_id
            material_change = True
        if risk_level is not None and risk_level != release.risk_level:
            release.risk_level = risk_level
            material_change = True
        if (
            target_rollout_percentage is not None
            and target_rollout_percentage != release.target_rollout_percentage
        ):
            release.target_rollout_percentage = max(0, min(100, int(target_rollout_percentage)))
            material_change = True
        if scheduled_for is not None:
            release.scheduled_for = scheduled_for

        release.version += 1
        release.updated_at = utcnow()

        if material_change:
            await ReleaseOrchestrator.invalidate_stale_approvals(
                session,
                release,
                reason=f"Material release properties changed at v{release.version}.",
                actor_id=actor_id,
                actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            )
            if release.status == ReleaseStatus.APPROVED:
                await transition_release(
                    session,
                    release,
                    ReleaseStatus.AWAITING_APPROVAL,
                    actor_id=actor_id,
                    actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
                    event_type="release.reapproval_required",
                    title="Re-approval required after material release edit",
                )

        await ReleaseReadinessService.evaluate_release(
            session,
            release,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            emit_event=True,
        )
        return release

    @staticmethod
    async def run_pr_analysis_stage(
        session: AsyncSession,
        release: Release,
        *,
        actor_id: uuid.UUID | None = None,
        github_client: Any | None = None,
        gemini_client: Any | None = None,
    ) -> Release:
        if release.pull_request_id is None:
            raise ReleaseTransitionError(
                "No pull request is linked to this release.", status_code=422
            )
        pr = await session.get(PullRequest, release.pull_request_id)
        repo = await session.get(Repository, pr.repository_id) if pr else None
        if pr is None or repo is None:
            raise ReleaseTransitionError("Linked pull request or repository not found.", status_code=404)

        stage = await ReleaseOrchestrator._get_stage(
            session, release.id, ReleaseStageKey.PR_ANALYSIS
        )
        await transition_release(
            session,
            release,
            ReleaseStatus.ANALYZING,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            stage_key=ReleaseStageKey.PR_ANALYSIS,
            title=f"Analyzing PR #{pr.github_pr_number}",
        )
        now = utcnow()
        if stage is not None:
            stage.status = ReleaseStageStatus.IN_PROGRESS
            stage.attempt_count += 1
            stage.started_at = stage.started_at or now
            stage.error_message = None

        try:
            from app.models import (
                AgentDecisionSource,
                AgentDecisionStatus,
                AgentExecutionStatus,
            )
            from app.services.agent_execution_service import AgentExecutionService

            exe = await AgentExecutionService.create_execution(
                session,
                agent_type="pr_agent",
                agent_name="ReleaseGuard PR Agent",
                task_name=f"Analyze PR #{pr.github_pr_number} for {release.release_key}",
                initiated_by_id=actor_id,
                actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
                repository_id=repo.id,
                pull_request_id=pr.id,
                release_id=release.id,
                planned_steps=[
                    {"step_key": "fetch_files", "step_name": "Retrieve PR Files"},
                    {"step_key": "analyze_pr", "step_name": "Analyze PR Diff & Risk"},
                ],
            )
            await AgentExecutionService.transition_execution(
                session,
                exe,
                AgentExecutionStatus.RUNNING,
                stage="fetch_files",
                activity=f"Retrieving files for PR #{pr.github_pr_number}",
            )

            files: list[dict[str, Any]] = []
            st_fetch = await AgentExecutionService.start_step(
                session, exe, step_key="fetch_files", step_name="Retrieve PR Files"
            )
            if github_client is not None and getattr(github_client, "is_configured", False):
                tc_gh = await AgentExecutionService.record_tool_start(
                    session,
                    exe,
                    tool_name="github.get_pull_request_files",
                    tool_category="github",
                    step_id=st_fetch.id,
                    input_summary={"repo": repo.full_name, "pr_number": pr.github_pr_number},
                )
                try:
                    files = await github_client.get_pull_request_files(
                        repo.owner, repo.name, pr.github_pr_number
                    )
                    await AgentExecutionService.record_tool_result(
                        session,
                        exe,
                        tc_gh,
                        success=True,
                        output_summary={"files_count": len(files)},
                    )
                except Exception as exc:  # noqa: BLE001
                    await AgentExecutionService.record_tool_result(
                        session,
                        exe,
                        tc_gh,
                        success=False,
                        error_code="GITHUB_ERROR",
                        error_message=str(exc),
                    )
                    logger.debug("GitHub PR files fetch skipped/fallback: %s", exc)
            await AgentExecutionService.complete_step(
                session, exe, st_fetch, output_summary={"files_count": len(files)}
            )

            st_analyze = await AgentExecutionService.start_step(
                session, exe, step_key="analyze_pr", step_name="Analyze PR Diff & Risk"
            )
            tc_ai = await AgentExecutionService.record_tool_start(
                session,
                exe,
                tool_name="pr_agent.analyze_pr_with_static_and_gemini",
                tool_category="ai_analysis",
                step_id=st_analyze.id,
                input_summary={"pr_number": pr.github_pr_number, "files_count": len(files)},
            )
            result = await analyze_pr_with_static_and_gemini(
                session=session,
                repo=repo,
                pr=pr,
                files=files,
                gemini_client=gemini_client,
            )
            await AgentExecutionService.record_tool_result(
                session,
                exe,
                tc_ai,
                success=True,
                output_summary={
                    "risk_level": pr.risk_level.value if pr.risk_level else "medium",
                    "risk_score": result.get("risk_score"),
                },
            )
            await AgentExecutionService.complete_step(
                session, exe, st_analyze, output_summary={"risk_score": result.get("risk_score")}
            )
            await AgentExecutionService.record_decision(
                session,
                exe,
                decision_type="pr_risk_assessment",
                decision_source=AgentDecisionSource.AI_RECOMMENDATION
                if gemini_client and getattr(gemini_client, "is_configured", False)
                else AgentDecisionSource.DETERMINISTIC_RULE,
                decision_summary=pr.analysis_summary or f"PR #{pr.github_pr_number} assessed as {pr.risk_level.value if pr.risk_level else 'medium'} risk.",
                recommendation="Use feature-flag protection and validate patch before rollout."
                if (pr.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL})
                else "Proceed to readiness evaluation.",
                decision_status=AgentDecisionStatus.ACCEPTED,
                supporting_evidence=[
                    f"Risk level: {pr.risk_level.value if pr.risk_level else 'medium'}",
                    f"Files inspected: {len(files)}",
                ],
                evidence_references=[{"type": "pull_request", "id": str(pr.id), "url": pr.html_url}],
                model_name=getattr(gemini_client, "model", None) if gemini_client else None,
                step_id=st_analyze.id,
            )
            await AgentExecutionService.transition_execution(
                session,
                exe,
                AgentExecutionStatus.COMPLETED,
                stage="completed",
                activity="PR risk analysis completed",
            )
            release.risk_level = pr.risk_level or release.risk_level
            if pr.analysis_summary:
                release.ai_summary = pr.analysis_summary

            if stage is not None:
                stage.status = ReleaseStageStatus.PASSED
                stage.completed_at = utcnow()
                stage.summary = f"Risk: {release.risk_level.value} (score {result.get('risk_score', 'n/a')})."
                stage.details_json = redact_dict(dict(result))

            session.add(
                ReleaseArtifact(
                    release_id=release.id,
                    artifact_type=ReleaseArtifactType.PR_ANALYSIS,
                    title=f"PR #{pr.github_pr_number} Risk & Static Analysis",
                    source_table="pull_requests",
                    source_id=str(pr.id),
                    uri=pr.html_url,
                    summary=pr.analysis_summary or f"Risk level: {release.risk_level.value}",
                    content_json=redact_dict(dict(result)),
                    created_by_id=actor_id,
                )
            )
            next_status = (
                ReleaseStatus.READY_FOR_FLAG
                if release.feature_flag_id is None
                else ReleaseStatus.AWAITING_APPROVAL
            )
            await transition_release(
                session,
                release,
                next_status,
                actor_id=actor_id,
                actor_type=ActorType.AGENT,
                stage_key=ReleaseStageKey.PR_ANALYSIS,
                title=f"PR #{pr.github_pr_number} analysis completed ({pr.risk_level.value} risk)",
            )
        except Exception as exc:  # noqa: BLE001
            err_msg = scrub_secrets(str(exc))
            if stage is not None:
                stage.status = ReleaseStageStatus.FAILED
                stage.error_message = err_msg
            release.error_message = err_msg
            await transition_release(
                session,
                release,
                ReleaseStatus.FAILED,
                actor_id=actor_id,
                actor_type=ActorType.SYSTEM,
                stage_key=ReleaseStageKey.PR_ANALYSIS,
                title=f"PR #{pr.github_pr_number} analysis failed",
                message=err_msg,
            )
        await ReleaseReadinessService.evaluate_release(session, release, actor_id=actor_id)
        return release

    @staticmethod
    async def run_patch_validation_stage(
        session: AsyncSession,
        release: Release,
        *,
        ctx: ToolExecutionContext,
        actor_id: uuid.UUID | None = None,
    ) -> Release:
        if release.pull_request_id is None:
            raise ReleaseTransitionError(
                "No pull request is linked to this release.", status_code=422
            )
        change = (
            await session.execute(
                select(CodeChange)
                .where(CodeChange.pull_request_id == release.pull_request_id)
                .order_by(CodeChange.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if change is None:
            raise ReleaseTransitionError(
                "No generated code patch found for the linked pull request.", status_code=404
            )

        stage = await ReleaseOrchestrator._get_stage(
            session, release.id, ReleaseStageKey.PATCH_VALIDATION
        )
        await transition_release(
            session,
            release,
            ReleaseStatus.VALIDATING,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            stage_key=ReleaseStageKey.PATCH_VALIDATION,
            title="Running isolated patch validation",
        )
        if stage is not None:
            stage.status = ReleaseStageStatus.IN_PROGRESS
            stage.attempt_count += 1
            stage.started_at = stage.started_at or utcnow()
            stage.error_message = None

        try:
            out = await handle_validate_patch_isolated(
                ctx, ValidatePatchIsolatedInput(code_change_id=change.id)
            )
            passed = change.validation_status == ValidationStatus.PASSED
            if stage is not None:
                stage.status = (
                    ReleaseStageStatus.PASSED if passed else ReleaseStageStatus.FAILED
                )
                stage.completed_at = utcnow()
                stage.summary = (
                    "Isolated patch validation passed."
                    if passed
                    else "Isolated patch validation failed."
                )
                stage.details_json = redact_dict(dict(out))

            session.add(
                ReleaseArtifact(
                    release_id=release.id,
                    artifact_type=ReleaseArtifactType.VALIDATION_REPORT,
                    title=f"Patch Validation ({change.flag_key})",
                    source_table="code_changes",
                    source_id=str(change.id),
                    summary=stage.summary if stage else None,
                    content_json=redact_dict(dict(out)),
                    created_by_id=actor_id,
                )
            )
            target = ReleaseStatus.READY_FOR_FLAG if passed else ReleaseStatus.FAILED
            if not passed:
                release.error_message = "Patch validation checks failed."
            await transition_release(
                session,
                release,
                target,
                actor_id=actor_id,
                actor_type=ActorType.AGENT,
                stage_key=ReleaseStageKey.PATCH_VALIDATION,
                title="Patch validation passed" if passed else "Patch validation failed",
            )
        except Exception as exc:  # noqa: BLE001
            err_msg = scrub_secrets(str(exc))
            if stage is not None:
                stage.status = ReleaseStageStatus.FAILED
                stage.error_message = err_msg
            release.error_message = err_msg
            await transition_release(
                session,
                release,
                ReleaseStatus.FAILED,
                actor_id=actor_id,
                actor_type=ActorType.SYSTEM,
                stage_key=ReleaseStageKey.PATCH_VALIDATION,
                title="Patch validation error",
                message=err_msg,
            )
        await ReleaseReadinessService.evaluate_release(session, release, actor_id=actor_id)
        return release

    @staticmethod
    async def request_release_approval(
        session: AsyncSession,
        release: Release,
        *,
        actor_id: uuid.UUID,
        rationale: str | None = None,
    ) -> Approval:
        if release.status in TERMINAL_RELEASE_STATES:
            raise ReleaseTransitionError(
                f"Cannot request approval for terminal release {release.release_key}.",
                status_code=422,
            )

        summary = await ReleaseReadinessService.evaluate_release(
            session, release, actor_id=actor_id, actor_type=ActorType.USER, emit_event=False
        )
        if summary.get("blocking_issues"):
            raise ReleaseTransitionError(
                "Release has blocking failures and cannot request approval: "
                + "; ".join(summary["blocking_issues"]),
                status_code=422,
            )

        existing_pending = (
            await session.execute(
                select(Approval)
                .where(
                    Approval.release_id == release.id,
                    Approval.release_version == release.version,
                    Approval.status == ApprovalStatus.PENDING,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_pending is not None:
            return existing_pending

        is_prod = is_production_environment(release.target_environment)
        category = (
            ApprovalCategory.C
            if is_prod or release.risk_level == RiskLevel.CRITICAL
            else ApprovalCategory.B
        )
        await transition_release(
            session,
            release,
            ReleaseStatus.AWAITING_APPROVAL,
            actor_id=actor_id,
            actor_type=ActorType.USER,
            stage_key=ReleaseStageKey.APPROVAL_GATE,
            title=f"Approval requested ({category.value})",
            message=rationale or f"Release approval gate for {release.target_environment}.",
        )

        payload = {
            "release_id": str(release.id),
            "release_key": release.release_key,
            "release_version": release.version,
            "target_environment": release.target_environment,
            "target_rollout_percentage": release.target_rollout_percentage,
        }
        precondition = {
            "release_version": release.version,
            "status": release.status.value,
            "readiness_score": release.readiness_score,
        }
        action_hash = compute_action_hash(
            "release_rollout_approve",
            "release",
            str(release.id),
            release.target_environment,
            payload,
        )
        approval = Approval(
            release_id=release.id,
            release_version=release.version,
            requested_by_id=actor_id,
            policy_category=category,
            action_type="release_rollout_approve",
            target_resource_type="release",
            target_resource_id=str(release.id),
            environment_name=release.target_environment,
            title=f"Approve Release {release.release_key}: {release.title[:160]}",
            rationale=rationale or f"Release approval gate for {release.target_environment} (v{release.version}).",
            action_payload=payload,
            expected_precondition=precondition,
            action_hash=action_hash,
            idempotency_key=f"release:{release.id}:v{release.version}:{uuid.uuid4().hex[:8]}",
            status=ApprovalStatus.PENDING,
            expires_at=utcnow() + timedelta(hours=24),
        )
        session.add(approval)
        await session.flush()

        stage = await ReleaseOrchestrator._get_stage(
            session, release.id, ReleaseStageKey.APPROVAL_GATE
        )
        if stage is not None:
            stage.status = ReleaseStageStatus.WAITING
            stage.started_at = stage.started_at or utcnow()
            stage.summary = f"Approval requested ({category.value}) for v{release.version}."

        await create_notification(
            session,
            category=NotificationCategory.APPROVAL_REQUIRED,
            severity="high" if is_prod else "warning",
            title=f"Release Approval Required: {release.release_key}",
            message=f"{release.title} targets {release.target_environment} ({release.readiness_score}/100 readiness).",
            resource_type="release",
            resource_id=str(release.id),
            environment_name=release.target_environment,
            action_href=f"/releases/{release.id}",
            metadata={"release_id": str(release.id), "approval_id": str(approval.id)},
        )
        await broadcast_workspace_event(
            "release.approval_requested",
            {
                "release_id": str(release.id),
                "release_key": release.release_key,
                "approval_id": str(approval.id),
            },
        )
        return approval

    @staticmethod
    async def sync_approval_decision(
        session: AsyncSession,
        approval: Approval,
        *,
        approved: bool,
        actor_id: uuid.UUID | None = None,
        comment: str | None = None,
    ) -> Release | None:
        if approval.release_id is None:
            return None
        release = await session.get(Release, approval.release_id)
        if release is None or release.status in TERMINAL_RELEASE_STATES:
            return release

        stage = await ReleaseOrchestrator._get_stage(
            session, release.id, ReleaseStageKey.APPROVAL_GATE
        )
        if approved:
            if stage is not None:
                stage.status = ReleaseStageStatus.PASSED
                stage.completed_at = utcnow()
                stage.summary = f"Approved by operator ({comment or 'no comment'})."
            if release.status != ReleaseStatus.APPROVED:
                await transition_release(
                    session,
                    release,
                    ReleaseStatus.APPROVED,
                    actor_id=actor_id,
                    actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
                    stage_key=ReleaseStageKey.APPROVAL_GATE,
                    title="Release approved by human operator",
                    message=comment,
                    payload={"approval_id": str(approval.id)},
                )
        else:
            if stage is not None:
                stage.status = ReleaseStageStatus.BLOCKED
                stage.summary = f"Approval rejected ({comment or 'no comment'})."
            release.error_message = comment or "Release approval rejected."
            await transition_release(
                session,
                release,
                ReleaseStatus.BLOCKED,
                actor_id=actor_id,
                actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
                stage_key=ReleaseStageKey.APPROVAL_GATE,
                title="Release blocked: approval rejected",
                message=comment,
                payload={"approval_id": str(approval.id)},
            )
            await create_notification(
                session,
                category=NotificationCategory.RELEASE_BLOCKED,
                severity="high",
                title=f"Release Blocked: {release.release_key}",
                message=comment or "Approval request was rejected.",
                resource_type="release",
                resource_id=str(release.id),
                environment_name=release.target_environment,
                action_href=f"/releases/{release.id}",
            )

        await ReleaseReadinessService.evaluate_release(session, release, actor_id=actor_id)
        return release

    @staticmethod
    async def pause_release(
        session: AsyncSession,
        release: Release,
        *,
        actor_id: uuid.UUID | None = None,
        expected_version: int | None = None,
        reason: str | None = None,
    ) -> Release:
        return await transition_release(
            session,
            release,
            ReleaseStatus.PAUSED,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            expected_version=expected_version,
            event_type="release.paused",
            title=f"Release {release.release_key} paused",
            message=reason or "Paused by operator.",
        )

    @staticmethod
    async def resume_release(
        session: AsyncSession,
        release: Release,
        *,
        actor_id: uuid.UUID | None = None,
        expected_version: int | None = None,
    ) -> Release:
        if release.status != ReleaseStatus.PAUSED:
            raise ReleaseTransitionError(
                f"Release {release.release_key} is '{release.status.value}', not 'paused'.",
                status_code=422,
            )
        target = release.previous_status or ReleaseStatus.INITIALIZING
        if target == ReleaseStatus.PAUSED or target in TERMINAL_RELEASE_STATES:
            target = ReleaseStatus.INITIALIZING
        return await transition_release(
            session,
            release,
            target,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            expected_version=expected_version,
            event_type="release.resumed",
            title=f"Release {release.release_key} resumed -> {target.value}",
        )

    @staticmethod
    async def retry_release(
        session: AsyncSession,
        release: Release,
        *,
        actor_id: uuid.UUID | None = None,
        expected_version: int | None = None,
    ) -> Release:
        if release.status not in {ReleaseStatus.FAILED, ReleaseStatus.BLOCKED}:
            raise ReleaseTransitionError(
                f"Only failed or blocked releases can be retried (current: '{release.status.value}').",
                status_code=422,
            )
        stages = list(
            (
                await session.execute(
                    select(ReleaseStage)
                    .where(ReleaseStage.release_id == release.id)
                    .order_by(ReleaseStage.stage_order)
                )
            )
            .scalars()
            .all()
        )
        failed_stage = next(
            (s for s in stages if s.status in {ReleaseStageStatus.FAILED, ReleaseStageStatus.BLOCKED}),
            None,
        )
        target = ReleaseStatus.INITIALIZING
        if failed_stage is not None:
            failed_stage.status = ReleaseStageStatus.PENDING
            failed_stage.error_message = None
            target = _STAGE_TO_RECOVERY_STATUS.get(failed_stage.stage_key, ReleaseStatus.INITIALIZING)

        await transition_release(
            session,
            release,
            target,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            expected_version=expected_version,
            event_type="release.retried",
            stage_key=failed_stage.stage_key if failed_stage else None,
            title=f"Release {release.release_key} retried -> {target.value}",
        )
        await ReleaseReadinessService.evaluate_release(session, release, actor_id=actor_id)
        return release

    @staticmethod
    async def cancel_release(
        session: AsyncSession,
        release: Release,
        *,
        actor_id: uuid.UUID | None = None,
        expected_version: int | None = None,
        reason: str | None = None,
    ) -> Release:
        pending_approvals = list(
            (
                await session.execute(
                    select(Approval).where(
                        Approval.release_id == release.id,
                        Approval.status == ApprovalStatus.PENDING,
                    )
                )
            )
            .scalars()
            .all()
        )
        for app in pending_approvals:
            app.status = ApprovalStatus.CANCELLED
            app.decision_comment = reason or "Release cancelled."

        return await transition_release(
            session,
            release,
            ReleaseStatus.CANCELLED,
            actor_id=actor_id,
            actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
            expected_version=expected_version,
            event_type="release.cancelled",
            title=f"Release {release.release_key} cancelled",
            message=reason or "Cancelled by operator.",
        )

    @staticmethod
    async def recover_interrupted_releases(session: AsyncSession) -> int:
        """Mark transient in-progress releases as FAILED (recoverable) after backend restart."""
        stuck = list(
            (
                await session.execute(
                    select(Release).where(Release.status.in_(list(ACTIVE_EXECUTION_STATES)))
                )
            )
            .scalars()
            .all()
        )
        recovered = 0
        for rel in stuck:
            stages = list(
                (
                    await session.execute(
                        select(ReleaseStage).where(
                            ReleaseStage.release_id == rel.id,
                            ReleaseStage.status == ReleaseStageStatus.IN_PROGRESS,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for st in stages:
                st.status = ReleaseStageStatus.FAILED
                st.error_message = "Interrupted by server restart; safe to retry."
            rel.error_message = "Interrupted by server restart; retry to resume."
            await transition_release(
                session,
                rel,
                ReleaseStatus.FAILED,
                actor_type=ActorType.SYSTEM,
                event_type="release.startup_recovery",
                title=f"Recovered interrupted release {rel.release_key}",
                message="Marked as failed after server restart; operator can retry safely.",
            )
            recovered += 1
        if recovered:
            await session.commit()
        return recovered

    @staticmethod
    def build_release_report(
        release: Release,
        *,
        stages: list[ReleaseStage],
        checks: list[Any],
        events: list[Any],
        artifacts: list[ReleaseArtifact],
        approvals: list[Approval],
    ) -> dict[str, Any]:
        return {
            "release_id": str(release.id),
            "release_key": release.release_key,
            "title": release.title,
            "description": release.description,
            "release_type": release.release_type.value,
            "target_environment": release.target_environment,
            "status": release.status.value,
            "risk_level": release.risk_level.value,
            "readiness_score": release.readiness_score,
            "readiness_status": release.readiness_status.value,
            "readiness_summary": release.readiness_summary,
            "target_rollout_percentage": release.target_rollout_percentage,
            "current_rollout_percentage": release.current_rollout_percentage,
            "ai_summary": release.ai_summary,
            "version": release.version,
            "created_at": release.created_at.isoformat() if release.created_at else None,
            "completed_at": release.completed_at.isoformat() if release.completed_at else None,
            "stages": [
                {
                    "stage_key": s.stage_key.value,
                    "title": s.title,
                    "status": s.status.value,
                    "summary": s.summary,
                    "attempt_count": s.attempt_count,
                }
                for s in stages
            ],
            "checks": [
                {
                    "check_key": c.check_key,
                    "title": c.title,
                    "status": c.status.value,
                    "is_blocking": c.is_blocking,
                    "message": c.message,
                }
                for c in checks
            ],
            "approvals": [
                {
                    "id": str(a.id),
                    "status": a.status.value,
                    "category": a.policy_category.value,
                    "title": a.title,
                    "decided_at": a.decided_at.isoformat() if a.decided_at else None,
                }
                for a in approvals
            ],
            "artifacts": [
                {
                    "id": str(art.id),
                    "type": art.artifact_type.value,
                    "title": art.title,
                    "summary": art.summary,
                }
                for art in artifacts
            ],
            "recent_events": [
                {
                    "event_type": e.event_type,
                    "title": e.title,
                    "message": e.message,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events[:25]
            ],
        }

    @staticmethod
    def render_release_report_markdown(report: dict[str, Any]) -> str:
        lines = [
            f"# Release Report: {report['release_key']} — {report['title']}",
            "",
            f"- **Status:** `{report['status']}`",
            f"- **Environment:** `{report['target_environment']}`",
            f"- **Risk Level:** `{report['risk_level']}`",
            f"- **Readiness:** `{report['readiness_status']}` ({report['readiness_score']}/100)",
            f"- **Rollout:** {report['current_rollout_percentage']}% / {report['target_rollout_percentage']}%",
            f"- **Version:** v{report['version']}",
            "",
        ]
        if report.get("ai_summary"):
            lines.extend(["## AI Summary", "", str(report["ai_summary"]), ""])
        lines.extend(["## Stages", ""])
        for st in report.get("stages", []):
            lines.append(f"- **{st['title']}** (`{st['status']}`): {st.get('summary') or '—'}")
        lines.extend(["", "## Readiness Checks", ""])
        for chk in report.get("checks", []):
            blk = " [BLOCKING]" if chk.get("is_blocking") else ""
            lines.append(f"- **{chk['title']}** (`{chk['status']}`{blk}): {chk.get('message') or '—'}")
        lines.extend(["", "## Timeline Highlights", ""])
        for ev in report.get("recent_events", []):
            lines.append(f"- `{ev.get('created_at') or ''}` **{ev['title']}** — {ev.get('message') or ''}")
        lines.append("")
        return "\n".join(lines)
