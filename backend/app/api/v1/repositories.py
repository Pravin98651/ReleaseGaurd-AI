"""GitHub repository connection, branch browsing, flag code scanning, and PR sync endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.flag_health import scan_files_for_flag_references
from app.api.deps import (
    AuthenticatedOperator,
    DBSession,
    DefaultOperator,
    build_github_client,
)
from app.api.v1.errors import APIException
from app.core.database import utcnow
from app.models import (
    ActorType,
    AuditStatus,
    FeatureFlag,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    PolicyCategory,
    PullRequest,
    PullRequestState,
    Repository,
)
from app.schemas import (
    BranchOut,
    FlagCodeReferenceOut,
    FlagCodeScanResponse,
    PaginatedResponse,
    PullRequestOut,
    RepositoryCreate,
    RepositoryOut,
)
from app.services.policy_engine import PolicyEngine

repositories_router = APIRouter(prefix="/repositories", tags=["repositories"])

_CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


async def _upsert_repository(
    session: AsyncSession,
    *,
    repo_data: dict[str, Any],
    connection_id: uuid.UUID | None = None,
    flagsmith_project_id: int | None = None,
) -> Repository:
    gh_id = int(repo_data["id"])
    full_name = str(repo_data["full_name"])
    owner_login = str((repo_data.get("owner") or {}).get("login") or full_name.split("/")[0])
    name = str(repo_data.get("name") or full_name.split("/")[-1])

    stmt = select(Repository).where(Repository.github_repo_id == gh_id)
    repo = (await session.execute(stmt)).scalar_one_or_none()
    now = utcnow()
    if repo is None:
        repo = Repository(
            connection_id=connection_id,
            github_repo_id=gh_id,
            owner=owner_login,
            name=name,
            full_name=full_name,
            default_branch=str(repo_data.get("default_branch") or "main"),
            is_private=bool(repo_data.get("private", False)),
            flagsmith_project_id=flagsmith_project_id,
            language=repo_data.get("language"),
            last_synced_at=now,
        )
        session.add(repo)
    else:
        repo.owner = owner_login
        repo.name = name
        repo.full_name = full_name
        repo.default_branch = str(repo_data.get("default_branch") or repo.default_branch)
        repo.is_private = bool(repo_data.get("private", False))
        repo.language = repo_data.get("language") or repo.language
        if connection_id is not None:
            repo.connection_id = connection_id
        if flagsmith_project_id is not None:
            repo.flagsmith_project_id = flagsmith_project_id
        repo.last_synced_at = now
        repo.updated_at = now

    await session.flush()
    return repo


async def _latest_github_connection_id(session: AsyncSession) -> uuid.UUID | None:
    stmt = (
        select(IntegrationConnection.id)
        .where(
            IntegrationConnection.provider == IntegrationProvider.GITHUB,
            IntegrationConnection.status == IntegrationStatus.CONNECTED,
        )
        .order_by(IntegrationConnection.updated_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


@repositories_router.get("", response_model=PaginatedResponse[RepositoryOut])
async def list_repositories(
    session: DBSession,
    _: AuthenticatedOperator,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedResponse[RepositoryOut]:
    """List connected GitHub repositories with pagination."""
    total = int((await session.execute(select(func.count(Repository.id)))).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                select(Repository)
                .order_by(Repository.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return PaginatedResponse[RepositoryOut](
        items=[RepositoryOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@repositories_router.post("", response_model=RepositoryOut, status_code=201)
async def connect_repository(
    request: Request,
    body: RepositoryCreate,
    session: DBSession,
    operator: DefaultOperator,
) -> RepositoryOut:
    """Verify repository access via GitHub API and register it in ReleaseGuard."""
    gh = await build_github_client(request.app, session)
    repo_data = await gh.get_repository(body.owner.strip(), body.name.strip())
    conn_id = await _latest_github_connection_id(session)
    repo = await _upsert_repository(
        session,
        repo_data=repo_data,
        connection_id=conn_id,
        flagsmith_project_id=body.flagsmith_project_id,
    )
    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="repository.connected",
        resource_type="repository",
        resource_id=str(repo.id),
        status=AuditStatus.SUCCESS,
        details={"full_name": repo.full_name},
    )
    await session.commit()
    await session.refresh(repo)
    return RepositoryOut.model_validate(repo)


@repositories_router.post("/sync")
async def sync_repositories(
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> dict[str, Any]:
    """Sync accessible GitHub repositories for the connected GitHub account."""
    gh = await build_github_client(request.app, session)
    raw_repos = await gh.list_repositories(visibility="all", limit=50)
    conn_id = await _latest_github_connection_id(session)

    synced: list[RepositoryOut] = []
    for item in raw_repos:
        repo = await _upsert_repository(session, repo_data=item, connection_id=conn_id)
        synced.append(RepositoryOut.model_validate(repo))

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="repository.synced",
        resource_type="repository",
        status=AuditStatus.SUCCESS,
        details={"synced_count": len(synced)},
    )
    await session.commit()
    serialized = [s.model_dump(mode="json") for s in synced]
    return {"synced_count": len(serialized), "repositories": serialized, "items": serialized}


@repositories_router.get("/{repo_id}/branches", response_model=list[BranchOut])
async def list_repository_branches(
    repo_id: uuid.UUID,
    request: Request,
    session: DBSession,
    _: AuthenticatedOperator,
) -> list[BranchOut]:
    """List live branches for a connected repository from GitHub."""
    repo = await session.get(Repository, repo_id)
    if repo is None:
        raise APIException(404, "REPOSITORY_NOT_FOUND", f"Repository {repo_id} not found.")
    gh = await build_github_client(request.app, session)
    raw_branches = await gh.list_branches(repo.owner, repo.name)
    return [
        BranchOut(
            name=str(b.get("name", "")),
            commit_sha=str((b.get("commit") or {}).get("sha", "")),
            protected=bool(b.get("protected", False)),
        )
        for b in raw_branches
    ]


@repositories_router.post("/{repo_id}/scan-flags", response_model=FlagCodeScanResponse)
async def scan_repository_flags(
    repo_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
) -> FlagCodeScanResponse:
    """Scan repository source files for Flagsmith SDK references and update feature_flags."""
    repo = await session.get(Repository, repo_id)
    if repo is None:
        raise APIException(404, "REPOSITORY_NOT_FOUND", f"Repository {repo_id} not found.")

    base_files: dict[str, str] | None = getattr(request.app.state, "repo_base_files", None)
    file_map: dict[str, str] = {}
    if base_files is not None:
        file_map = dict(base_files)
    else:
        gh = await build_github_client(request.app, session)
        tree = await gh.get_repo_tree(
            repo.owner, repo.name, tree_sha=repo.default_branch, recursive=True
        )
        for entry in tree:
            path = str(entry.get("path", ""))
            if entry.get("type") == "blob" and path.lower().endswith(_CODE_EXTS):
                info = await gh.get_file_contents(
                    repo.owner, repo.name, path, ref=repo.default_branch
                )
                file_map[path] = str(info.get("content", ""))
            if len(file_map) >= 60:
                break

    scanned = scan_files_for_flag_references(file_map)
    refs_out: dict[str, list[FlagCodeReferenceOut]] = {}
    total_refs = 0

    for flag_key, refs in scanned.items():
        total_refs += len(refs)
        refs_out[flag_key] = [
            FlagCodeReferenceOut(
                flag_key=flag_key,
                file_path=r.file_path,
                line_number=r.line_number,
                snippet=r.snippet,
                sdk_method=r.method or None,
            )
            for r in refs
        ]

    # Update synced FeatureFlag.code_references
    flags = (await session.execute(select(FeatureFlag))).scalars().all()
    for f in flags:
        if f.key in refs_out:
            f.code_references = [
                {
                    "file_path": r.file_path,
                    "line_number": r.line_number,
                    "snippet": r.snippet,
                }
                for r in refs_out[f.key]
            ]
            if f.repository_id is None:
                f.repository_id = repo.id
        elif f.repository_id == repo.id:
            f.code_references = []

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="repository.flags_scanned",
        resource_type="repository",
        resource_id=str(repo.id),
        status=AuditStatus.SUCCESS,
        details={"files_scanned": len(file_map), "references_found": total_refs},
    )
    await session.commit()

    return FlagCodeScanResponse(
        repository_id=repo.id,
        files_scanned=len(file_map),
        references_found=total_refs,
        references_by_flag=refs_out,
    )


async def sync_pull_requests_for_repo(
    session: AsyncSession,
    gh: Any,
    repo: Repository,
    *,
    state: str = "open",
) -> list[PullRequestOut]:
    """Fetch PRs from GitHub and upsert rows in `pull_requests` cleanly."""
    raw_prs = await gh.list_pull_requests(repo.owner, repo.name, state=state)
    synced: list[PullRequestOut] = []

    for p in raw_prs:
        pr_num = int(p["number"])
        stmt = select(PullRequest).where(
            PullRequest.repository_id == repo.id,
            PullRequest.github_pr_number == pr_num,
        )
        pr_row = (await session.execute(stmt)).scalar_one_or_none()
        raw_state = str(p.get("state", "open")).lower()
        if p.get("merged_at"):
            pr_state = PullRequestState.MERGED
        elif raw_state == "closed":
            pr_state = PullRequestState.CLOSED
        else:
            pr_state = PullRequestState.OPEN

        head_info = p.get("head") or {}
        base_info = p.get("base") or {}
        user_info = p.get("user") or {}

        if pr_row is None:
            pr_row = PullRequest(
                repository_id=repo.id,
                github_pr_number=pr_num,
                title=str(p.get("title") or f"PR #{pr_num}"),
                body=p.get("body"),
                author_login=str(user_info.get("login") or "unknown"),
                head_branch=str(head_info.get("ref") or "feature"),
                base_branch=str(base_info.get("ref") or repo.default_branch),
                head_sha=str(head_info.get("sha") or "0" * 40),
                state=pr_state,
                is_draft=bool(p.get("draft", False)),
                html_url=str(p.get("html_url") or f"https://github.com/{repo.full_name}/pull/{pr_num}"),
            )
            session.add(pr_row)
        else:
            pr_row.title = str(p.get("title") or pr_row.title)
            pr_row.body = p.get("body")
            pr_row.head_branch = str(head_info.get("ref") or pr_row.head_branch)
            pr_row.base_branch = str(base_info.get("ref") or pr_row.base_branch)
            pr_row.head_sha = str(head_info.get("sha") or pr_row.head_sha)
            pr_row.state = pr_state
            pr_row.is_draft = bool(p.get("draft", False))
            pr_row.html_url = str(p.get("html_url") or pr_row.html_url)
            pr_row.updated_at = utcnow()

        await session.flush()
        synced.append(PullRequestOut.model_validate(pr_row))

    repo.last_synced_at = utcnow()
    return synced


@repositories_router.post("/{repo_id}/pull-requests/sync")
async def sync_repo_pull_requests(
    repo_id: uuid.UUID,
    request: Request,
    session: DBSession,
    operator: DefaultOperator,
    state: Annotated[str, Query()] = "open",
) -> dict[str, Any]:
    """Fetch pull requests from GitHub and upsert them into `pull_requests`."""
    repo = await session.get(Repository, repo_id)
    if repo is None:
        raise APIException(404, "REPOSITORY_NOT_FOUND", f"Repository {repo_id} not found.")

    gh = await build_github_client(request.app, session)
    synced = await sync_pull_requests_for_repo(session, gh, repo, state=state)

    await PolicyEngine.record_audit_log(
        session,
        actor_type=ActorType.USER,
        actor_id=str(operator.id),
        policy_category=PolicyCategory.A,
        event_action="pull_requests.synced",
        resource_type="repository",
        resource_id=str(repo.id),
        status=AuditStatus.SUCCESS,
        details={"synced_count": len(synced)},
    )
    await session.commit()
    return {
        "synced_count": len(synced),
        "items": [item.model_dump(mode="json") for item in synced],
    }
