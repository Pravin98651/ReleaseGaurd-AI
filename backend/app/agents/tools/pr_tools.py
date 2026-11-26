"""11 pr_agent manual tools for GitHub browsing, PR risk analysis, patch generation, validation, and Draft PRs."""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select

logger = logging.getLogger("releaseguard.agents.pr_tools")

from app.agents.specialists.pr_agent import annotate_flag_opportunities_with_existing
from app.agents.tools.context import ToolExecutionContext
from app.core.database import utcnow
from app.core.security import MAX_FILE_CHARS, wrap_untrusted
from app.models import (
    AnalysisStatus,
    CodeChange,
    CodeChangeStatus,
    CodeChangeType,
    FeatureFlag,
    PullRequest,
    Repository,
    ValidationStatus,
)
from app.schemas.tools import (
    AnalyzePRRiskAndOpportunitiesInput,
    AnalyzePRRiskAndOpportunitiesOutput,
    GenerateFlagCleanupPatchInput,
    GenerateFlagCleanupPatchOutput,
    GenerateFlagWrappingPatchInput,
    GenerateFlagWrappingPatchOutput,
    GitHubCreateBranchAndDraftPRInput,
    GitHubCreateBranchAndDraftPROutput,
    GitHubGetFileContentsInput,
    GitHubGetFileContentsOutput,
    GitHubGetPullRequestDiffInput,
    GitHubGetPullRequestDiffOutput,
    GitHubListBranchesInput,
    GitHubListBranchesOutput,
    GitHubListPullRequestsInput,
    GitHubListPullRequestsOutput,
    GitHubListRepositoriesInput,
    GitHubListRepositoriesOutput,
    GitHubSearchRepoCodeInput,
    GitHubSearchRepoCodeOutput,
    ValidatePatchIsolatedInput,
    ValidatePatchIsolatedOutput,
)
from app.services.patch_validator import PatchValidator

_CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _build_unified_diff(file_path: str, before_code: str, after_code: str) -> str:
    before_lines = before_code.splitlines(keepends=True)
    after_lines = after_code.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    )
    return "".join(diff)


async def handle_github_list_repositories(
    ctx: ToolExecutionContext,
    args: GitHubListRepositoriesInput,
) -> dict[str, Any]:
    client = await ctx.get_github_client()
    raw = await client.list_repositories(visibility=args.visibility, limit=args.limit)
    items = [
        {
            "id": r.get("id"),
            "full_name": r.get("full_name"),
            "default_branch": r.get("default_branch", "main"),
            "is_private": bool(r.get("private", False)),
        }
        for r in raw
    ]
    return GitHubListRepositoriesOutput(repositories=items).model_dump(mode="json")


async def handle_github_list_branches(
    ctx: ToolExecutionContext,
    args: GitHubListBranchesInput,
) -> dict[str, Any]:
    client = await ctx.get_github_client()
    raw = await client.list_branches(args.owner, args.repo)
    branches = [
        {
            "name": b.get("name", ""),
            "sha": (b.get("commit") or {}).get("sha", ""),
            "protected": bool(b.get("protected", False)),
        }
        for b in raw
    ]
    return GitHubListBranchesOutput(branches=branches).model_dump(mode="json")


async def handle_github_list_pull_requests(
    ctx: ToolExecutionContext,
    args: GitHubListPullRequestsInput,
) -> dict[str, Any]:
    client = await ctx.get_github_client()
    raw = await client.list_pull_requests(args.owner, args.repo, state=args.state)
    prs = [
        {
            "number": p.get("number"),
            "title": p.get("title", ""),
            "author": (p.get("user") or {}).get("login", ""),
            "head_branch": (p.get("head") or {}).get("ref", ""),
            "base_branch": (p.get("base") or {}).get("ref", ""),
            "is_draft": bool(p.get("draft", False)),
        }
        for p in raw
    ]
    return GitHubListPullRequestsOutput(pull_requests=prs).model_dump(mode="json")


async def handle_github_get_pull_request_diff(
    ctx: ToolExecutionContext,
    args: GitHubGetPullRequestDiffInput,
) -> dict[str, Any]:
    client = await ctx.get_github_client()
    diff_data = await client.get_pull_request_diff(
        args.owner, args.repo, args.pr_number, max_files=args.max_files
    )
    sandboxed = diff_data["sandboxed"]
    return GitHubGetPullRequestDiffOutput(
        pr_number=args.pr_number,
        title_sandboxed=sandboxed["title"],
        body_sandboxed=sandboxed["body"],
        diff_sandboxed=sandboxed["diff"],
        changed_files=diff_data["changed_files"],
    ).model_dump(mode="json")


async def handle_github_get_file_contents(
    ctx: ToolExecutionContext,
    args: GitHubGetFileContentsInput,
) -> dict[str, Any]:
    client = await ctx.get_github_client()
    file_data = await client.get_file_contents(
        args.owner,
        args.repo,
        args.path,
        ref=args.ref,
        max_chars=MAX_FILE_CHARS,
    )
    return GitHubGetFileContentsOutput(
        path=file_data["path"],
        sha=file_data["sha"],
        content_sandboxed=file_data["sandboxed_content"],
    ).model_dump(mode="json")


async def handle_github_search_repo_code(
    ctx: ToolExecutionContext,
    args: GitHubSearchRepoCodeInput,
) -> dict[str, Any]:
    repo = await ctx.session.get(Repository, args.repository_id)
    if repo is None:
        raise ValueError(f"Repository {args.repository_id} not found.")

    pattern = re.compile(re.escape(args.query_pattern), re.IGNORECASE)
    matches: list[dict[str, Any]] = []

    if ctx.repo_base_files is not None:
        files_to_scan = list(ctx.repo_base_files.items())
    else:
        client = await ctx.get_github_client()
        tree = await client.get_repo_tree(
            repo.owner, repo.name, tree_sha=repo.default_branch, recursive=True
        )
        candidate_paths = [
            str(entry.get("path", ""))
            for entry in tree
            if entry.get("type") == "blob"
            and str(entry.get("path", "")).lower().endswith(_CODE_EXTS)
        ][:40]
        files_to_scan = []
        for path in candidate_paths:
            file_info = await client.get_file_contents(
                repo.owner, repo.name, path, ref=repo.default_branch
            )
            files_to_scan.append((path, str(file_info.get("content", ""))))

    for path, content in files_to_scan:
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                matches.append(
                    {
                        "file_path": path,
                        "line_number": lineno,
                        "snippet_sandboxed": wrap_untrusted(
                            "github_code_snippet",
                            {"repo": repo.full_name, "file_path": path, "line": lineno},
                            line.strip(),
                            max_chars=500,
                        ),
                    }
                )
                if len(matches) >= 50:
                    return GitHubSearchRepoCodeOutput(matches=matches).model_dump(mode="json")

    return GitHubSearchRepoCodeOutput(matches=matches).model_dump(mode="json")


async def handle_analyze_pr_risk_and_opportunities(
    ctx: ToolExecutionContext,
    args: AnalyzePRRiskAndOpportunitiesInput,
) -> dict[str, Any]:
    pr = await ctx.session.get(PullRequest, args.pull_request_id)
    if pr is None:
        raise ValueError(f"PullRequest {args.pull_request_id} not found.")
    repo = await ctx.session.get(Repository, pr.repository_id)

    # Verify cited file paths against actual PR changed files when GitHub is reachable.
    changed_paths: set[str] | None = None
    files_override: list[dict[str, Any]] = []
    if repo is not None:
        try:
            client = await ctx.get_github_client()
            files = await client.get_pull_request_files(
                repo.owner, repo.name, pr.github_pr_number
            )
            files_override = list(files)
            changed_paths = {str(f.get("filename", "")) for f in files if f.get("filename")}
        except Exception:  # noqa: BLE001
            changed_paths = None

    confirmed: list[dict[str, Any]] = []
    potential = [r.model_dump(mode="json") for r in args.potential_risks]

    if changed_paths is None and args.confirmed_risks:
        potential.append(
            {
                "title": "GitHub changed-files verification unavailable",
                "severity": "low",
                "assumption": "Could not verify confirmed_risks file paths against live GitHub PR files.",
                "verification_needed": "Re-run analysis once GitHub API access is available.",
            }
        )

    for risk in args.confirmed_risks:
        risk_dict = risk.model_dump(mode="json")
        if changed_paths is not None and risk.file_path not in changed_paths:
            potential.append(
                {
                    "title": f"{risk.title} (unverified file path citation)",
                    "severity": risk.severity.value
                    if hasattr(risk.severity, "value")
                    else str(risk.severity),
                    "assumption": (
                        f"Cited '{risk.file_path}' which was not found in the live PR changed files."
                    ),
                    "verification_needed": (
                        f"Confirm exact file path and line behavior. Original note: {risk.evidence}"
                    ),
                }
            )
        else:
            confirmed.append(risk_dict)

    existing_keys = set(
        (await ctx.session.execute(select(FeatureFlag.key))).scalars().all()
    )
    raw_opps = [o.model_dump(mode="json") for o in args.flag_opportunities]
    opps, _ = annotate_flag_opportunities_with_existing(raw_opps, existing_keys)

    pr.risk_score = args.risk_score
    pr.risk_level = args.risk_level
    pr.analysis_summary = args.summary
    pr.confirmed_risks = confirmed
    pr.potential_risks = potential
    pr.flag_opportunities = opps
    pr.analysis_status = AnalysisStatus.COMPLETED
    pr.analyzed_at = utcnow()
    await ctx.session.flush()

    try:
        from app.services.risk_engine.risk_service import IntelligentReleaseRiskService

        await IntelligentReleaseRiskService.assess_release(
            ctx.session,
            pull_request_id=pr.id,
            repository_id=pr.repository_id,
            target_environment="development",
            files_override=files_override or None,
            create_execution=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skipping 7-dim risk assessment in pr_tools: %s", exc)

    await ctx.session.commit()
    await ctx.session.refresh(pr)

    return AnalyzePRRiskAndOpportunitiesOutput(
        pull_request_id=pr.id,
        risk_score=args.risk_score,
        risk_level=args.risk_level,
        confirmed_risks_count=len(confirmed),
        potential_risks_count=len(potential),
        flag_opportunities_count=len(opps),
    ).model_dump(mode="json")


async def handle_generate_flag_wrapping_patch(
    ctx: ToolExecutionContext,
    args: GenerateFlagWrappingPatchInput,
) -> dict[str, Any]:
    repo = await ctx.session.get(Repository, args.repository_id)
    if repo is None:
        raise ValueError(f"Repository {args.repository_id} not found.")

    files_diff: list[dict[str, Any]] = []
    for item in args.files_diff:
        d = item.model_dump(mode="json")
        if not d.get("unified_diff"):
            d["unified_diff"] = _build_unified_diff(
                d["file_path"], d.get("before_code", ""), d["after_code"]
            )
        files_diff.append(d)

    generated_tests = [t.model_dump(mode="json") for t in args.generated_tests]

    change = CodeChange(
        repository_id=args.repository_id,
        pull_request_id=args.pull_request_id,
        agent_task_id=ctx.agent_task_id,
        change_type=CodeChangeType.FLAG_WRAP,
        flag_key=args.flag_key,
        title=args.title,
        description=args.description,
        files_diff=files_diff,
        generated_tests=generated_tests,
        validation_status=ValidationStatus.PENDING,
        status=CodeChangeStatus.DRAFT,
    )
    ctx.session.add(change)
    await ctx.session.commit()
    await ctx.session.refresh(change)

    return GenerateFlagWrappingPatchOutput(
        code_change_id=change.id,
        status=change.status.value,
        files_count=len(files_diff),
        tests_count=len(generated_tests),
    ).model_dump(mode="json")


async def handle_validate_patch_isolated(
    ctx: ToolExecutionContext,
    args: ValidatePatchIsolatedInput,
) -> dict[str, Any]:
    change = await ctx.session.get(CodeChange, args.code_change_id)
    if change is None:
        raise ValueError(f"CodeChange {args.code_change_id} not found.")

    base_files = dict(ctx.repo_base_files) if ctx.repo_base_files is not None else None
    if base_files is None:
        # Derive expected base snapshot from non-empty before_code entries if modify action exists
        derived_base: dict[str, str] = {}
        for item in change.files_diff or []:
            if item.get("action") == "modify" and item.get("before_code"):
                derived_base[str(item["file_path"])] = str(item["before_code"])
        if derived_base:
            base_files = derived_base

    expected_head_sha: str | None = (change.validation_report or {}).get("target_head_sha")
    actual_head_sha: str | None = None
    if change.pull_request_id:
        pr = await ctx.session.get(PullRequest, change.pull_request_id)
        if pr is not None:
            actual_head_sha = pr.head_sha
            if expected_head_sha is None:
                expected_head_sha = pr.head_sha
            repo = await ctx.session.get(Repository, change.repository_id)
            if repo is not None:
                try:
                    gh = await ctx.get_github_client()
                    if gh.token:
                        live_pr = await gh.get_pull_request(
                            repo.owner, repo.name, pr.github_pr_number
                        )
                        live_sha = str((live_pr.get("head") or {}).get("sha") or "")
                        if live_sha:
                            actual_head_sha = live_sha
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Skipping live head_sha check in validate_patch_isolated: %s", exc)

    report = PatchValidator.validate(
        files_diff=list(change.files_diff or []),
        generated_tests=list(change.generated_tests or []),
        base_files=base_files,
        expected_head_sha=expected_head_sha,
        actual_head_sha=actual_head_sha,
    )
    if expected_head_sha or actual_head_sha:
        report["target_head_sha"] = actual_head_sha or expected_head_sha
    passed = report.get("validation_status") == "passed"
    change.validation_status = ValidationStatus.PASSED if passed else ValidationStatus.FAILED
    change.validation_report = report
    change.status = CodeChangeStatus.VALIDATED if passed else CodeChangeStatus.VALIDATION_FAILED
    await ctx.session.commit()
    await ctx.session.refresh(change)

    return ValidatePatchIsolatedOutput(
        code_change_id=change.id,
        validation_status="passed" if passed else "failed",
        validation_report=report,
    ).model_dump(mode="json")


async def handle_github_create_branch_and_draft_pr(
    ctx: ToolExecutionContext,
    args: GitHubCreateBranchAndDraftPRInput,
) -> dict[str, Any]:
    change = await ctx.session.get(CodeChange, args.code_change_id)
    if change is None:
        raise ValueError(f"CodeChange {args.code_change_id} not found.")
    if change.validation_status != ValidationStatus.PASSED:
        raise ValueError(
            f"Draft PR creation blocked: CodeChange {change.id} validation_status is "
            f"'{change.validation_status.value}' (must be 'passed')."
        )

    repo = await ctx.session.get(Repository, change.repository_id)
    if repo is None:
        raise ValueError(f"Repository {change.repository_id} not found.")

    pr: PullRequest | None = (
        await ctx.session.get(PullRequest, change.pull_request_id)
        if change.pull_request_id
        else None
    )
    validated_head_sha = (change.validation_report or {}).get("target_head_sha")
    if pr is not None and validated_head_sha and pr.head_sha != validated_head_sha:
        raise ValueError(
            f"Stale PR head SHA: patch was validated for {validated_head_sha[:12]}, "
            f"but PR is now at {pr.head_sha[:12]}. Re-run patch validation."
        )

    files_to_commit: list[dict[str, str]] = [
        {"path": str(item["file_path"]), "content": str(item["after_code"])}
        for item in (change.files_diff or [])
    ]
    for test_item in change.generated_tests or []:
        files_to_commit.append(
            {"path": str(test_item["file_path"]), "content": str(test_item["content"])}
        )

    base_body = args.pr_body or f"Generated by ReleaseGuard AI for flag `{change.flag_key}`."
    if pr is not None and f"#{pr.github_pr_number}" not in base_body:
        base_body = (
            f"{base_body}\n\n"
            f"---\n"
            f"Linked Original PR: #{pr.github_pr_number} ({pr.html_url})"
        )

    client = await ctx.get_github_client()
    result = await client.create_branch_and_draft_pr(
        owner=repo.owner,
        repo=repo.name,
        base_branch=args.base_branch or repo.default_branch,
        files=files_to_commit,
        commit_message=f"chore(releaseguard): {args.pr_title}",
        pr_title=args.pr_title,
        pr_body=base_body,
    )

    change.draft_pr_number = int(result["pr_number"])
    change.draft_pr_url = str(result["html_url"])
    change.draft_branch_name = str(result["branch_name"])
    change.status = CodeChangeStatus.PR_OPENED
    await ctx.session.commit()
    await ctx.session.refresh(change)

    if pr is not None and client.token:
        try:
            await client.create_issue_comment(
                owner=repo.owner,
                repo=repo.name,
                issue_number=pr.github_pr_number,
                body=(
                    f"<!-- releaseguard-ai:draft-pr -->\n"
                    f"ReleaseGuard AI opened Draft PR #{change.draft_pr_number} "
                    f"(`{change.draft_branch_name}`) to guard `{change.flag_key}`: "
                    f"{change.draft_pr_url}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping original PR comment link after Draft PR creation: %s", exc)

    return GitHubCreateBranchAndDraftPROutput(
        code_change_id=change.id,
        pr_number=int(result["pr_number"]),
        html_url=str(result["html_url"]),
        branch_name=str(result["branch_name"]),
        draft=bool(result.get("draft", True)),
    ).model_dump(mode="json")


async def handle_generate_flag_cleanup_patch(
    ctx: ToolExecutionContext,
    args: GenerateFlagCleanupPatchInput,
) -> dict[str, Any]:
    repo = await ctx.session.get(Repository, args.repository_id)
    if repo is None:
        raise ValueError(f"Repository {args.repository_id} not found.")
    flag = await ctx.session.get(FeatureFlag, args.feature_flag_id)
    if flag is None:
        raise ValueError(f"FeatureFlag {args.feature_flag_id} not found.")

    files_diff: list[dict[str, Any]] = []
    for item in args.files_diff:
        d = item.model_dump(mode="json")
        if not d.get("unified_diff"):
            d["unified_diff"] = _build_unified_diff(
                d["file_path"], d.get("before_code", ""), d["after_code"]
            )
        files_diff.append(d)

    generated_tests = [t.model_dump(mode="json") for t in args.generated_tests]
    title = args.title or f"chore(flags): remove '{flag.key}' keeping '{args.keep_branch}' branch"
    change = CodeChange(
        repository_id=args.repository_id,
        feature_flag_id=args.feature_flag_id,
        agent_task_id=ctx.agent_task_id,
        change_type=CodeChangeType.FLAG_CLEANUP,
        flag_key=flag.key,
        title=title,
        description=args.description
        or f"Cleanup patch removing '{flag.key}' references and preserving '{args.keep_branch}' behavior.",
        files_diff=files_diff,
        generated_tests=generated_tests,
        validation_status=ValidationStatus.PENDING,
        status=CodeChangeStatus.DRAFT,
    )
    ctx.session.add(change)
    await ctx.session.commit()
    await ctx.session.refresh(change)

    return GenerateFlagCleanupPatchOutput(
        code_change_id=change.id,
        status=change.status.value,
        keep_branch=args.keep_branch,
    ).model_dump(mode="json")


PR_TOOL_HANDLERS: dict[str, Any] = {
    "github_list_repositories": handle_github_list_repositories,
    "github_list_branches": handle_github_list_branches,
    "github_list_pull_requests": handle_github_list_pull_requests,
    "github_get_pull_request_diff": handle_github_get_pull_request_diff,
    "github_get_file_contents": handle_github_get_file_contents,
    "github_search_repo_code": handle_github_search_repo_code,
    "analyze_pr_risk_and_opportunities": handle_analyze_pr_risk_and_opportunities,
    "generate_flag_wrapping_patch": handle_generate_flag_wrapping_patch,
    "validate_patch_isolated": handle_validate_patch_isolated,
    "github_create_branch_and_draft_pr": handle_github_create_branch_and_draft_pr,
    "generate_flag_cleanup_patch": handle_generate_flag_cleanup_patch,
}


def get_pr_tool_names() -> list[str]:
    """Return ordered list of all 11 pr_agent tool names."""
    return list(PR_TOOL_HANDLERS.keys())


def _unused_uuid_marker() -> type[UUID]:
    return UUID
