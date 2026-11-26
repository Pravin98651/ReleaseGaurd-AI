"""Pull request browsing, AI/static risk analysis, Flagsmith patch generation, validation, and Draft PR endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.agents.approval_executor import capture_expected_precondition
from app.agents.specialists.pr_agent import (
    analyze_pr_with_static_and_gemini,
    annotate_flag_opportunities_with_existing,
    generate_context_aware_flag_patch,
)
from app.agents.tools.pr_tools import (
    handle_github_create_branch_and_draft_pr,
    handle_validate_patch_isolated,
)
from app.api.deps import (
    AuthenticatedOperator,
    DBSession,
    DefaultOperator,
    build_flagsmith_client,
    build_gemini_client_from_app,
    build_github_client,
    build_tool_context,
    rate_limit_dependency,
)
from app.api.v1.errors import APIException
from app.core.database import utcnow
from app.models import (
    ActorType,
    AnalysisStatus,
    AuditLog,
    AuditStatus,
    CodeChange,
    CodeChangeStatus,
    CodeChangeType,
    FeatureFlag,
    GitHubCheckRun,
    PolicyCategory,
    PullRequest,
    PullRequestAnalysisComment,
    PullRequestState,
    ReleaseRiskAssessment,
    Repository,
    RiskClassification,
    RiskDimensionResult,
    RiskLevel,
    ValidationStatus,
)

logger = logging.getLogger("releaseguard.api.pull_requests")
from app.schemas import (
    ApprovalOut,
    CodeChangeOut,
    CreateDraftPRRequest,
    GeneratePatchRequest,
    PaginatedResponse,
    PullRequestAnalyzeResponse,
    PullRequestDetailOut,
    PullRequestOut,
)
from app.schemas.tools import (
    GitHubCreateBranchAndDraftPRInput,
    ValidatePatchIsolatedInput,
)
from app.services.policy_engine import PolicyEngine

pull_requests_router = APIRouter(prefix="/pull-requests", tags=["pull-requests"])
code_changes_router = APIRouter(prefix="/code-changes", tags=["code-changes"])

_RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _classification_to_risk_level(
    classification: RiskClassification,
    static_level: RiskLevel,
) -> RiskLevel:
    mapping = {
        RiskClassification.LOW: RiskLevel.LOW,
        RiskClassification.MODERATE: RiskLevel.MEDIUM,
        RiskClassification.ELEVATED: RiskLevel.MEDIUM,
        RiskClassification.HIGH: RiskLevel.HIGH,
        RiskClassification.CRITICAL: RiskLevel.CRITICAL,
    }
    engine_level = mapping.get(classification, RiskLevel.MEDIUM)
    return engine_level if _RISK_RANK[engine_level] >= _RISK_RANK[static_level] else static_level


async def _collect_existing_flag_keys(
    session: DBSession,
    request: Request,
    repo: Repository,
) -> set[str]:
    """Gather existing flag keys from local DB and Flagsmith project (best-effort)."""
    keys = set((await session.execute(select(FeatureFlag.key))).scalars().all())
    if repo.flagsmith_project_id:
        try:
            fs = await build_flagsmith_client(request.app, session)
            if getattr(fs, "org_token", None):
                remote_flags = await fs.list_flags(repo.flagsmith_project_id)
                for rf in remote_flags:
                    name = str(rf.get("name") or "").strip()
                    if name:
                        keys.add(name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping remote Flagsmith flag list for dedup: %s", exc)
    return keys


@pull_requests_router.get("", response_model=PaginatedResponse[PullRequestOut])
async def list_pull_requests(
    session: DBSession,
    _: AuthenticatedOperator,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    state: Annotated[PullRequestState | None, Query()] = None,
    risk_level: Annotated[RiskLevel | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[PullRequestOut]:
    """List synced pull requests filtered by repo, state, or risk level."""
    base_q = select(PullRequest)
    count_q = select(func.count(PullRequest.id))

    if repository_id is not None:
        base_q = base_q.where(PullRequest.repository_id == repository_id)
        count_q = count_q.where(PullRequest.repository_id == repository_id)
    if state is not None:
        base_q = base_q.where(PullRequest.state == state)
        count_q = count_q.where(PullRequest.state == state)
    if risk_level is not None:
        base_q = base_q.where(PullRequest.risk_level == risk_level)
        count_q = count_q.where(PullRequest.risk_level == risk_level)

    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                base_q.order_by(PullRequest.updated_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse[PullRequestOut](
        items=[PullRequestOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@pull_requests_router.get("/{pr_id}", response_model=PullRequestDetailOut)
async def get_pull_request(
    pr_id: uuid.UUID,
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> PullRequestDetailOut:
    """Return full PR details including confirmed/potential risks, opportunities, and code changes."""
    pr = await session.get(PullRequest, pr_id)
    if pr is None:
        raise APIException(404, "PR_NOT_FOUND", f"Pull request {pr_id} not found.")

    changes = (
        (
            await session.execute(
                select(CodeChange)
                .where(CodeChange.pull_request_id == pr.id)
                .order_by(CodeChange.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    changed_files: list[dict[str, Any]] = []
    repo = await session.get(Repository, pr.repository_id)
    if repo is not None:
        try:
            gh = await build_github_client(request.app, session)
            if gh.token:
                raw_files = await gh.get_pull_request_files(repo.owner, repo.name, pr.github_pr_number)
                changed_files = [
                    {
                        "filename": f.get("filename", ""),
                        "status": f.get("status", "modified"),
                        "additions": int(f.get("additions") or 0),
                        "deletions": int(f.get("deletions") or 0),
                        "patch": str(f.get("patch") or ""),
                    }
                    for f in raw_files
                ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping live PR changed_files fetch for PR %s: %s", pr.id, exc)

    assessment = (
        await session.execute(
            select(ReleaseRiskAssessment)
            .where(ReleaseRiskAssessment.pull_request_id == pr.id)
            .order_by(ReleaseRiskAssessment.is_current.desc(), ReleaseRiskAssessment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    risk_assessment_summary: dict[str, Any] | None = None
    if assessment is not None:
        dims = (
            (
                await session.execute(
                    select(RiskDimensionResult)
                    .where(RiskDimensionResult.assessment_id == assessment.id)
                    .order_by(RiskDimensionResult.dimension_key.asc())
                )
            )
            .scalars()
            .all()
        )
        risk_assessment_summary = {
            "id": str(assessment.id),
            "assessment_key": assessment.assessment_key,
            "overall_risk_score": assessment.risk_score,
            "risk_classification": assessment.risk_level.value
            if assessment.risk_level
            else None,
            "commit_sha": assessment.commit_sha,
            "dimensions": [
                {
                    "dimension_key": d.dimension_key,
                    "dimension_name": d.dimension_name,
                    "score": d.score,
                    "weight": d.weight,
                    "weighted_contribution": d.weighted_contribution,
                    "severity": d.severity.value if d.severity else None,
                    "status": d.status.value if d.status else None,
                    "explanations": list(d.explanations or []),
                    "limitations": list(d.limitations or []),
                }
                for d in dims
            ],
        }

    check_run = (
        await session.execute(
            select(GitHubCheckRun)
            .where(GitHubCheckRun.pull_request_id == pr.id, GitHubCheckRun.is_superseded.is_(False))
            .order_by(GitHubCheckRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    pr_comment = (
        await session.execute(
            select(PullRequestAnalysisComment)
            .where(PullRequestAnalysisComment.pull_request_id == pr.id)
            .order_by(PullRequestAnalysisComment.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_audit = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "pull_request",
                AuditLog.resource_id == str(pr.id),
                AuditLog.event_action == "pr.analyzed",
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    audit_details = dict(latest_audit.details or {}) if latest_audit else {}

    pr_out = PullRequestOut.model_validate(pr).model_dump()
    return PullRequestDetailOut(
        **pr_out,
        code_changes=[CodeChangeOut.model_validate(c) for c in changes],
        changed_files=changed_files,
        diff_summary={
            "files_count": len(changed_files),
            "confirmed_risks_count": len(pr.confirmed_risks or []),
            "potential_risks_count": len(pr.potential_risks or []),
            "flag_opportunities_count": len(pr.flag_opportunities or []),
            "recommendations": list(audit_details.get("recommendations") or []),
            "unknowns": list(audit_details.get("unknowns") or []),
            "flag_agent_recommendation": audit_details.get("flag_agent_recommendation"),
            "risk_assessment": risk_assessment_summary,
            "dx_status": {
                "check_run": (
                    {
                        "status": check_run.status.value,
                        "conclusion": check_run.conclusion.value if check_run.conclusion else None,
                        "policy_mode": check_run.policy_mode,
                        "commit_sha": check_run.commit_sha,
                    }
                    if check_run
                    else None
                ),
                "comment": (
                    {
                        "publish_status": pr_comment.publish_status,
                        "commit_sha": pr_comment.commit_sha,
                        "external_comment_id": pr_comment.external_comment_id,
                    }
                    if pr_comment
                    else None
                ),
            },
        },
    )


@pull_requests_router.post(
    "/{pr_id}/analyze",
    response_model=PullRequestAnalyzeResponse,
    dependencies=[Depends(rate_limit_dependency("pr_analyze", max_requests=20, window_seconds=60))],
)
async def analyze_pull_request(
    pr_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> PullRequestAnalyzeResponse:
    """Category A: Analyze PR diff with static/AST checks and sandboxed Gemini reasoning."""
    pr = await session.get(PullRequest, pr_id)
    if pr is None:
        raise APIException(404, "PR_NOT_FOUND", f"Pull request {pr_id} not found.")
    repo = await session.get(Repository, pr.repository_id)
    if repo is None:
        raise APIException(404, "REPOSITORY_NOT_FOUND", "Parent repository not found.")

    gh = await build_github_client(request.app, session)
    extra_signals: list[str] = []
    try:
        live_pr = await gh.get_pull_request(repo.owner, repo.name, pr.github_pr_number)
        live_sha = str((live_pr.get("head") or {}).get("sha") or "")
        if live_sha and live_sha != pr.head_sha:
            pr.head_sha = live_sha
            extra_signals.append("stale_sha_refreshed")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skipping live PR head_sha refresh for PR %s: %s", pr.id, exc)

    files_data = await gh.get_pull_request_files(
        repo.owner, repo.name, pr.github_pr_number, max_files=100
    )

    ws = await PolicyEngine.get_workspace_settings(session)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)

    pr.analysis_status = AnalysisStatus.ANALYZING
    await session.flush()

    analysis = await analyze_pr_with_static_and_gemini(
        pr_title=pr.title,
        pr_body=pr.body,
        files_data=files_data,
        repo_full_name=repo.full_name,
        pr_number=pr.github_pr_number,
        gemini_client=gemini_client,
    )

    existing_flag_keys = await _collect_existing_flag_keys(session, request, repo)
    annotated_opps, flag_agent_rec = annotate_flag_opportunities_with_existing(
        list(analysis["flag_opportunities"]), existing_flag_keys
    )

    from app.services.risk_engine.risk_service import IntelligentReleaseRiskService

    static_level = RiskLevel(str(analysis["risk_level"]).lower())
    static_score = int(analysis["risk_score"])
    assessment = await IntelligentReleaseRiskService.assess_release(
        session,
        pull_request_id=pr.id,
        repository_id=repo.id,
        target_environment="development",
        actor_id=operator.id,
        github_client=gh,
        gemini_client=None,
        files_override=files_data,
        create_execution=True,
    )
    combined_score = max(static_score, int(assessment.risk_score))
    combined_level = _classification_to_risk_level(assessment.risk_level, static_level)

    pr.risk_score = combined_score
    pr.risk_level = combined_level
    pr.analysis_summary = str(analysis["analysis_summary"])
    pr.confirmed_risks = list(analysis["confirmed_risks"])
    pr.potential_risks = list(analysis["potential_risks"])
    pr.flag_opportunities = annotated_opps
    pr.analysis_status = AnalysisStatus.COMPLETED
    pr.analyzed_at = utcnow()
    await session.flush()

    try:
        from app.services.dx.github_dx import (
            GitHubPullRequestCommentService,
            GitHubStatusCheckService,
        )

        await GitHubStatusCheckService.publish_for_pr(
            session, repository=repo, pr=pr, policy_mode="warn_medium", github_client=gh
        )
        await GitHubPullRequestCommentService.publish_or_update(
            session, repository=repo, pr=pr, github_client=gh
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skipping DX Check/Comment publish during PR analyze: %s", exc)

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="pr.analyzed",
        resource_type="pull_request",
        resource_id=str(pr.id),
        status=AuditStatus.SUCCESS,
        details={
            "risk_score": pr.risk_score,
            "risk_level": pr.risk_level.value,
            "assessment_key": assessment.assessment_key,
            "seven_dim_score": assessment.risk_score,
            "confirmed_risks": len(pr.confirmed_risks),
            "potential_risks": len(pr.potential_risks),
            "recommendations": list(analysis.get("recommendations") or []),
            "unknowns": list(analysis.get("unknowns") or []),
            "flag_agent_recommendation": flag_agent_rec,
            "head_sha": pr.head_sha,
        },
    )
    from app.models import NotificationCategory
    from app.services.notification_service import (
        broadcast_workspace_event,
        create_notification,
    )

    if pr.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        await create_notification(
            session,
            category=NotificationCategory.PR_HIGH_RISK,
            severity="critical" if pr.risk_level == RiskLevel.CRITICAL else "high",
            title=f"{pr.risk_level.value.upper()} Risk PR #{pr.github_pr_number}: {pr.title[:160]}",
            message=f"Risk score {pr.risk_score}/100 with {len(pr.confirmed_risks or [])} confirmed risk(s).",
            resource_type="pull_request",
            resource_id=str(pr.id),
            action_href="/pull-requests",
            metadata={"risk_score": pr.risk_score, "risk_level": pr.risk_level.value},
            dedupe_minutes=5,
        )
    else:
        await broadcast_workspace_event(
            "pr.analyzed",
            {"pull_request_id": str(pr.id), "risk_level": pr.risk_level.value},
        )
    await session.commit()
    await session.refresh(pr)

    return PullRequestAnalyzeResponse(
        pull_request=PullRequestOut.model_validate(pr),
        static_signals=[*list(analysis.get("static_signals") or []), *extra_signals],
    )


@pull_requests_router.post(
    "/{pr_id}/generate-patch",
    response_model=CodeChangeOut,
    status_code=201,
    dependencies=[Depends(rate_limit_dependency("pr_generate_patch", max_requests=20, window_seconds=60))],
)
async def generate_pr_patch(
    pr_id: uuid.UUID,
    body: GeneratePatchRequest,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> CodeChangeOut:
    """Category A: Generate a Flagsmith-wrapping code patch and unit tests for a PR."""
    pr = await session.get(PullRequest, pr_id)
    if pr is None:
        raise APIException(404, "PR_NOT_FOUND", f"Pull request {pr_id} not found.")
    repo = await session.get(Repository, pr.repository_id)
    if repo is None:
        raise APIException(404, "REPOSITORY_NOT_FOUND", "Parent repository not found.")

    target_files = list(body.target_files or [])
    if not target_files and pr.flag_opportunities:
        first_opp = pr.flag_opportunities[0]
        if isinstance(first_opp, dict):
            target_files = [str(f) for f in first_opp.get("target_files") or []]
    if not target_files and pr.confirmed_risks:
        target_files = [
            str(r["file_path"])
            for r in pr.confirmed_risks
            if isinstance(r, dict) and r.get("file_path")
        ]

    base_files: dict[str, str] = dict(getattr(request.app.state, "repo_base_files", None) or {})
    files_data: list[dict[str, Any]] = []
    pinned_ref = pr.head_sha or pr.head_branch
    try:
        gh = await build_github_client(request.app, session)
        if gh.token:
            try:
                live_pr = await gh.get_pull_request(repo.owner, repo.name, pr.github_pr_number)
                live_sha = str((live_pr.get("head") or {}).get("sha") or "")
                if live_sha:
                    pr.head_sha = live_sha
                    pinned_ref = live_sha
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping live head_sha check during patch generation: %s", exc)

            try:
                files_data = await gh.get_pull_request_files(
                    repo.owner, repo.name, pr.github_pr_number
                )
                if not target_files and files_data:
                    target_files = [str(f.get("filename") or "") for f in files_data if f.get("filename")][:3]
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping PR files fetch during patch generation: %s", exc)

            for tf in target_files[:3]:
                if tf and tf not in base_files:
                    try:
                        file_info = await gh.get_file_contents(
                            repo.owner, repo.name, tf, ref=pinned_ref
                        )
                        base_files[tf] = str(file_info.get("content") or "")
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Skipping remote base file fetch for %s: %s", tf, exc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skipping GitHub context fetch for patch generation: %s", exc)

    existing_flag_keys = await _collect_existing_flag_keys(session, request, repo)
    flag_exists = body.flag_key.strip().lower() in {k.lower() for k in existing_flag_keys}

    ws = await PolicyEngine.get_workspace_settings(session)
    gemini_client = build_gemini_client_from_app(request.app, model_override=ws.gemini_model)

    patch_payload = await generate_context_aware_flag_patch(
        flag_key=body.flag_key,
        flag_type=body.flag_type,
        target_files=target_files,
        base_files=base_files,
        files_data=files_data,
        pr_title=pr.title,
        pr_body=pr.body,
        repo_full_name=repo.full_name,
        pr_number=pr.github_pr_number,
        gemini_client=gemini_client,
    )

    change = CodeChange(
        repository_id=repo.id,
        pull_request_id=pr.id,
        change_type=CodeChangeType.FLAG_WRAP,
        flag_key=body.flag_key,
        title=patch_payload["title"],
        description=patch_payload["description"],
        files_diff=patch_payload["files_diff"],
        generated_tests=patch_payload["generated_tests"],
        validation_status=ValidationStatus.PENDING,
        validation_report={"target_head_sha": pr.head_sha, "flag_exists": flag_exists},
        status=CodeChangeStatus.DRAFT,
    )
    session.add(change)
    await session.flush()

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="patch.generated",
        resource_type="code_change",
        resource_id=str(change.id),
        status=AuditStatus.SUCCESS,
        details={
            "flag_key": body.flag_key,
            "pull_request_id": str(pr.id),
            "generation_mode": patch_payload.get("generation_mode", "template_fallback"),
        },
    )
    await session.commit()
    await session.refresh(change)
    return CodeChangeOut.model_validate(change)


@code_changes_router.post("/{change_id}/validate", response_model=CodeChangeOut)
async def validate_code_change(
    change_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> CodeChangeOut:
    """Category A: Run Isolated Temp-Dir Patch Validator with scrubbed subprocess environment."""
    change = await session.get(CodeChange, change_id)
    if change is None:
        raise APIException(404, "CODE_CHANGE_NOT_FOUND", f"CodeChange {change_id} not found.")

    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    await handle_validate_patch_isolated(ctx, ValidatePatchIsolatedInput(code_change_id=change.id))
    await session.refresh(change)

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="patch.validated",
        resource_type="code_change",
        resource_id=str(change.id),
        status=AuditStatus.SUCCESS
        if change.validation_status == ValidationStatus.PASSED
        else AuditStatus.FAILED,
        details={"validation_status": change.validation_status.value},
    )
    await session.commit()
    await session.refresh(change)
    return CodeChangeOut.model_validate(change)


@code_changes_router.post("/{change_id}/create-draft-pr")
async def create_draft_pr(
    change_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
    body: CreateDraftPRRequest | None = None,
) -> Any:
    """Category B: Create branch + commit + Draft PR (draft=True) after passing validation."""
    change = await session.get(CodeChange, change_id)
    if change is None:
        raise APIException(404, "CODE_CHANGE_NOT_FOUND", f"CodeChange {change_id} not found.")
    if change.validation_status != ValidationStatus.PASSED:
        raise APIException(
            409,
            "PATCH_VALIDATION_REQUIRED",
            f"CodeChange {change_id} must pass isolated validation before opening a Draft PR.",
            details={"validation_status": change.validation_status.value},
        )
    repo = await session.get(Repository, change.repository_id)
    if repo is None:
        raise APIException(404, "REPOSITORY_NOT_FOUND", "Target repository not found.")

    req = body or CreateDraftPRRequest()
    base_branch = req.base_branch or repo.default_branch
    pr_title = req.pr_title or change.title
    pr_body = req.pr_body or (
        f"ReleaseGuard AI Draft PR guarding `{change.flag_key}`.\n\n"
        f"{change.description or ''}"
    )
    if change.pull_request_id:
        orig_pr = await session.get(PullRequest, change.pull_request_id)
        if orig_pr is not None and f"#{orig_pr.github_pr_number}" not in pr_body:
            pr_body = (
                f"{pr_body}\n\n---\nLinked Original PR: #{orig_pr.github_pr_number} ({orig_pr.html_url})"
            )

    _, requires_approval = await PolicyEngine.evaluate_action(
        session,
        action_type="github_create_branch_and_draft_pr",
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        resource_type="code_change",
        resource_id=str(change.id),
    )

    ctx = await build_tool_context(request.app, session, user_id=operator.id)
    action_payload = {
        "code_change_id": str(change.id),
        "base_branch": base_branch,
        "pr_title": pr_title,
        "pr_body": pr_body,
    }

    if requires_approval:
        res_type, res_id, expected_pre, _ = await capture_expected_precondition(
            ctx,
            action_type="github_create_branch_and_draft_pr",
            action_payload=action_payload,
        )
        approval = await PolicyEngine.create_approval(
            session,
            action_type="github_create_branch_and_draft_pr",
            target_resource_type=res_type,
            target_resource_id=res_id,
            environment_name=None,
            title=f"Open Draft PR: {pr_title[:180]}",
            rationale=f"Category B Draft PR creation for validated CodeChange `{change.flag_key}`.",
            action_payload=action_payload,
            expected_precondition=expected_pre,
            requested_by_id=operator.id,
        )
        await session.commit()
        await session.refresh(approval)
        return JSONResponse(
            status_code=202,
            content={
                "execution_status": "approval_required",
                "approval": ApprovalOut.model_validate(approval).model_dump(mode="json"),
            },
        )

    await handle_github_create_branch_and_draft_pr(
        ctx, GitHubCreateBranchAndDraftPRInput.model_validate(action_payload)
    )
    await session.refresh(change)
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.B,
        event_action="github.draft_pr_created",
        resource_type="code_change",
        resource_id=str(change.id),
        status=AuditStatus.SUCCESS,
        details={
            "draft_pr_number": change.draft_pr_number,
            "draft_branch_name": change.draft_branch_name,
            "draft": True,
        },
    )
    from app.models import NotificationCategory
    from app.services.notification_service import create_notification

    await create_notification(
        session,
        category=NotificationCategory.DRAFT_PR_CREATED,
        severity="info",
        title=f"Draft PR Created for `{change.flag_key}`",
        message=f"Draft PR #{change.draft_pr_number} opened on branch `{change.draft_branch_name}`.",
        resource_type="code_change",
        resource_id=str(change.id),
        action_href="/pull-requests",
        metadata={"draft_pr_number": change.draft_pr_number},
    )
    await session.commit()
    await session.refresh(change)
    return {
        "execution_status": "executed",
        "data": CodeChangeOut.model_validate(change).model_dump(mode="json"),
    }
