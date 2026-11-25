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
    AuditStatus,
    CodeChange,
    CodeChangeStatus,
    CodeChangeType,
    PolicyCategory,
    PullRequest,
    PullRequestState,
    Repository,
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
    files_data = await gh.get_pull_request_files(repo.owner, repo.name, pr.github_pr_number)

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

    pr.risk_score = int(analysis["risk_score"])
    pr.risk_level = RiskLevel(str(analysis["risk_level"]).lower())
    pr.analysis_summary = str(analysis["analysis_summary"])
    pr.confirmed_risks = list(analysis["confirmed_risks"])
    pr.potential_risks = list(analysis["potential_risks"])
    pr.flag_opportunities = list(analysis["flag_opportunities"])
    pr.analysis_status = AnalysisStatus.COMPLETED
    pr.analyzed_at = utcnow()

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
            "confirmed_risks": len(pr.confirmed_risks),
            "potential_risks": len(pr.potential_risks),
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
        static_signals=list(analysis.get("static_signals") or []),
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
    try:
        gh = await build_github_client(request.app, session)
        if gh.token:
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
                            repo.owner, repo.name, tf, ref=pr.head_branch
                        )
                        base_files[tf] = str(file_info.get("content") or "")
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Skipping remote base file fetch for %s: %s", tf, exc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skipping GitHub context fetch for patch generation: %s", exc)

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
