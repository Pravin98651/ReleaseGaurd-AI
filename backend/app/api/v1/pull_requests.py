"""Pull request browsing, AI/static risk analysis, Flagsmith patch generation, validation, and Draft PR endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.agents.approval_executor import capture_expected_precondition
from app.agents.specialists.pr_agent import (
    analyze_pr_with_static_and_gemini,
    generate_flag_patch_payload,
)
from app.agents.tools.pr_tools import (
    handle_github_create_branch_and_draft_pr,
    handle_validate_patch_isolated,
)
from app.api.deps import (
    DBSession,
    DefaultOperator,
    build_gemini_client_from_app,
    build_github_client,
    build_tool_context,
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
    session: DBSession,
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
    pr_out = PullRequestOut.model_validate(pr).model_dump()
    return PullRequestDetailOut(
        **pr_out,
        code_changes=[CodeChangeOut.model_validate(c) for c in changes],
        diff_summary={
            "confirmed_risks_count": len(pr.confirmed_risks or []),
            "potential_risks_count": len(pr.potential_risks or []),
            "flag_opportunities_count": len(pr.flag_opportunities or []),
        },
    )


@pull_requests_router.post("/{pr_id}/analyze", response_model=PullRequestAnalyzeResponse)
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
        },
    )
    await session.commit()
    await session.refresh(pr)

    return PullRequestAnalyzeResponse(
        pull_request=PullRequestOut.model_validate(pr),
        static_signals=list(analysis.get("static_signals") or []),
    )


@pull_requests_router.post("/{pr_id}/generate-patch", response_model=CodeChangeOut, status_code=201)
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

    base_files: dict[str, str] = dict(getattr(request.app.state, "repo_base_files", None) or {})
    if target_files and target_files[0] not in base_files:
        try:
            gh = await build_github_client(request.app, session)
            file_info = await gh.get_file_contents(
                repo.owner, repo.name, target_files[0], ref=pr.head_branch
            )
            base_files[target_files[0]] = str(file_info.get("content") or "")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping remote base file fetch for %s: %s", target_files[0], exc)

    patch_payload = generate_flag_patch_payload(
        flag_key=body.flag_key,
        flag_type=body.flag_type,
        target_files=target_files,
        base_files=base_files,
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
        details={"flag_key": body.flag_key, "pull_request_id": str(pr.id)},
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
    await session.commit()
    await session.refresh(change)
    return {
        "execution_status": "executed",
        "data": CodeChangeOut.model_validate(change).model_dump(mode="json"),
    }
