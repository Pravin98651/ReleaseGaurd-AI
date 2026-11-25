"""GitHub App, Webhook, Status Check, and PR Comment services (Phase 6)."""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.security import decrypt_token, encrypt_token, scrub_secrets
from app.integrations.github_client import GitHubAPIError, GitHubClient
from app.models import (
    AnalysisStatus,
    GitHubCheckConclusion,
    GitHubCheckRun,
    GitHubCheckStatus,
    GitHubInstallation,
    GitHubWebhookEvent,
    IntegrationStatus,
    NotificationCategory,
    PullRequest,
    PullRequestAnalysisComment,
    PullRequestState,
    Repository,
    RepositoryIntelligenceProfile,
    RiskLevel,
    WebhookProcessingStatus,
)
from app.services.notification_service import create_notification

logger = logging.getLogger("releaseguard.dx.github")

PR_COMMENT_MARKER = "<!-- releaseguard-ai:pr-summary -->"
MAX_COMMENT_LENGTH = 6000


class DXIntegrationError(Exception):
    """Domain error for Phase 6 Developer Experience integrations."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubIntegrationService:
    """Manages GitHub App installations and verified repository mappings."""

    @staticmethod
    async def connect_installation(
        session: AsyncSession,
        *,
        installation_id: int,
        account_login: str,
        account_type: str = "Organization",
        repositories: list[dict[str, Any]] | None = None,
        permissions: dict[str, str] | None = None,
        access_token: str | None = None,
        webhook_secret: str | None = None,
    ) -> GitHubInstallation:
        existing = (
            await session.execute(
                select(GitHubInstallation)
                .where(GitHubInstallation.installation_id == installation_id)
                .limit(1)
            )
        ).scalar_one_or_none()

        enc_tok = encrypt_token(access_token) if access_token else None
        enc_sec = encrypt_token(webhook_secret) if webhook_secret else None

        if existing is None:
            existing = GitHubInstallation(
                installation_id=installation_id,
                account_login=account_login,
                account_type=account_type,
                repositories=repositories or [],
                permissions=permissions or {"checks": "write", "pull_requests": "write", "contents": "read"},
                encrypted_access_token=enc_tok,
                webhook_secret_encrypted=enc_sec,
                status=IntegrationStatus.CONNECTED,
            )
            session.add(existing)
        else:
            existing.account_login = account_login
            existing.account_type = account_type
            if repositories is not None:
                existing.repositories = repositories
            if permissions is not None:
                existing.permissions = permissions
            if enc_tok is not None:
                existing.encrypted_access_token = enc_tok
            if enc_sec is not None:
                existing.webhook_secret_encrypted = enc_sec
            existing.status = IntegrationStatus.CONNECTED
        await session.flush()
        return existing

    @staticmethod
    async def remove_installation(session: AsyncSession, installation_id: int) -> bool:
        inst = (
            await session.execute(
                select(GitHubInstallation)
                .where(GitHubInstallation.installation_id == installation_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if inst is None:
            return False
        inst.status = IntegrationStatus.DISCONNECTED
        inst.encrypted_access_token = None
        await session.flush()
        return True


class GitHubStatusCheckService:
    """Publishes policy-aware GitHub Check Runs and supersedes stale commits."""

    @staticmethod
    def resolve_conclusion(
        *,
        risk_level: RiskLevel | None,
        analysis_status: AnalysisStatus,
        policy_mode: str,
    ) -> tuple[GitHubCheckStatus, GitHubCheckConclusion | None]:
        if analysis_status in (AnalysisStatus.PENDING, AnalysisStatus.ANALYZING):
            return GitHubCheckStatus.IN_PROGRESS, None
        if analysis_status == AnalysisStatus.FAILED:
            return GitHubCheckStatus.COMPLETED, GitHubCheckConclusion.FAILURE

        if policy_mode == "informational":
            return GitHubCheckStatus.COMPLETED, GitHubCheckConclusion.NEUTRAL
        if policy_mode == "block_critical" and risk_level == RiskLevel.CRITICAL:
            return GitHubCheckStatus.COMPLETED, GitHubCheckConclusion.FAILURE
        if policy_mode == "require_remediation_high" and risk_level in (
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ):
            return GitHubCheckStatus.COMPLETED, GitHubCheckConclusion.FAILURE
        if policy_mode == "warn_medium" and risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH):
            return GitHubCheckStatus.COMPLETED, GitHubCheckConclusion.NEUTRAL
        if policy_mode == "warn_medium" and risk_level == RiskLevel.CRITICAL:
            return GitHubCheckStatus.COMPLETED, GitHubCheckConclusion.FAILURE
        return GitHubCheckStatus.COMPLETED, GitHubCheckConclusion.SUCCESS

    @staticmethod
    async def publish_for_pr(
        session: AsyncSession,
        *,
        repository: Repository,
        pr: PullRequest,
        policy_mode: str = "warn_medium",
        github_client: GitHubClient | None = None,
    ) -> GitHubCheckRun:
        # Supersede any prior check runs on different commit SHAs for this PR
        prior = list(
            (
                await session.execute(
                    select(GitHubCheckRun).where(
                        GitHubCheckRun.pull_request_id == pr.id,
                        GitHubCheckRun.commit_sha != pr.head_sha,
                        GitHubCheckRun.is_superseded.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        for old in prior:
            old.is_superseded = True

        status, conclusion = GitHubStatusCheckService.resolve_conclusion(
            risk_level=pr.risk_level,
            analysis_status=pr.analysis_status,
            policy_mode=policy_mode,
        )
        risk_str = pr.risk_level.value.upper() if pr.risk_level else "UNKNOWN"
        score_str = f"{pr.risk_score}/100" if pr.risk_score is not None else "N/A"
        summary = (
            f"Risk Level: **{risk_str}** (Score: {score_str}) | "
            f"Policy: `{policy_mode}` | Findings: {len(pr.confirmed_risks or [])} confirmed."
        )

        existing = (
            await session.execute(
                select(GitHubCheckRun)
                .where(
                    GitHubCheckRun.pull_request_id == pr.id,
                    GitHubCheckRun.commit_sha == pr.head_sha,
                    GitHubCheckRun.is_superseded.is_(False),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        if existing is None:
            existing = GitHubCheckRun(
                repository_id=repository.id,
                pull_request_id=pr.id,
                commit_sha=pr.head_sha,
                status=status,
                conclusion=conclusion,
                policy_mode=policy_mode,
                summary=summary,
                details_url=f"/pull-requests?pr={pr.github_pr_number}",
            )
            session.add(existing)
        else:
            existing.status = status
            existing.conclusion = conclusion
            existing.policy_mode = policy_mode
            existing.summary = summary

        if github_client is not None and github_client.token:
            try:
                if existing.external_check_run_id is None:
                    ext = await github_client.create_check_run(
                        owner=repository.owner,
                        repo=repository.name,
                        name=existing.name,
                        head_sha=pr.head_sha,
                        status=status.value,
                        conclusion=conclusion.value if conclusion else None,
                        title=f"ReleaseGuard AI ({risk_str})",
                        summary=summary,
                    )
                    existing.external_check_run_id = ext.get("id")
                else:
                    await github_client.update_check_run(
                        owner=repository.owner,
                        repo=repository.name,
                        check_run_id=existing.external_check_run_id,
                        status=status.value,
                        conclusion=conclusion.value if conclusion else None,
                        title=f"ReleaseGuard AI ({risk_str})",
                        summary=summary,
                    )
            except GitHubAPIError as exc:
                logger.warning("GitHub Check Run API failed: %s", exc.message)

        await session.flush()
        return existing


class GitHubPullRequestCommentService:
    """Builds and updates marker-identified PR risk summary comments."""

    @staticmethod
    async def render_markdown(
        session: AsyncSession,
        *,
        repository: Repository,
        pr: PullRequest,
        include_history: bool = True,
    ) -> str:
        safe_title = html.escape(pr.title[:200])
        risk_str = pr.risk_level.value.upper() if pr.risk_level else "UNKNOWN"
        score_str = f"{pr.risk_score}/100" if pr.risk_score is not None else "N/A"
        lines = [
            "### ReleaseGuard AI — Pull Request Risk Analysis",
            "",
            f"- **Repository:** `{repository.full_name}`",
            f"- **PR:** #{pr.github_pr_number} — {safe_title}",
            f"- **Commit SHA:** `{pr.head_sha[:12]}`",
            f"- **Risk Level:** `{risk_str}` (Score: `{score_str}`)",
            "",
            "#### Key Findings",
        ]
        if pr.confirmed_risks:
            for r in pr.confirmed_risks[:5]:
                desc = html.escape(str(r.get("description") or r.get("title") or r.get("file_path") or "Risk detected")[:220])
                lines.append(f"- {desc}")
        else:
            lines.append("- No confirmed high-risk findings recorded yet.")

        if include_history:
            prof = (
                await session.execute(
                    select(RepositoryIntelligenceProfile)
                    .where(RepositoryIntelligenceProfile.repository_id == repository.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if prof and prof.release_count > 0:
                lines.extend(
                    [
                        "",
                        "#### Historical Intelligence (Observed)",
                        f"- Repository failure rate: `{(prof.historical_failure_rate * 100):.1f}%` across `{prof.release_count}` qualifying releases.",
                    ]
                )

        if pr.flag_opportunities:
            lines.extend(["", "#### Recommended Feature Flags"])
            for fo in pr.flag_opportunities[:3]:
                lines.append(f"- `{html.escape(str(fo.get('flag_key') or 'flag'))}`")

        body = PR_COMMENT_MARKER + "\n" + scrub_secrets("\n".join(lines))
        return body[:MAX_COMMENT_LENGTH]

    @staticmethod
    async def publish_or_update(
        session: AsyncSession,
        *,
        repository: Repository,
        pr: PullRequest,
        config: dict[str, Any] | None = None,
        github_client: GitHubClient | None = None,
    ) -> PullRequestAnalysisComment:
        cfg = dict(config or {})
        include_history = bool(cfg.get("include_historical_intelligence", True))
        body = await GitHubPullRequestCommentService.render_markdown(
            session, repository=repository, pr=pr, include_history=include_history
        )
        existing = (
            await session.execute(
                select(PullRequestAnalysisComment)
                .where(PullRequestAnalysisComment.pull_request_id == pr.id)
                .limit(1)
            )
        ).scalar_one_or_none()

        if existing is None:
            existing = PullRequestAnalysisComment(
                repository_id=repository.id,
                pull_request_id=pr.id,
                commit_sha=pr.head_sha,
                marker_id=PR_COMMENT_MARKER,
                body_markdown=body,
                config=cfg,
                publish_status="prepared",
            )
            session.add(existing)
        else:
            existing.commit_sha = pr.head_sha
            existing.body_markdown = body
            existing.config = cfg

        if github_client is not None and github_client.token:
            try:
                if existing.external_comment_id is None:
                    res = await github_client.create_issue_comment(
                        owner=repository.owner,
                        repo=repository.name,
                        issue_number=pr.github_pr_number,
                        body=body,
                    )
                    existing.external_comment_id = res.get("id")
                else:
                    await github_client.update_issue_comment(
                        owner=repository.owner,
                        repo=repository.name,
                        comment_id=existing.external_comment_id,
                        body=body,
                    )
                existing.publish_status = "published"
                existing.last_error = None
            except GitHubAPIError as exc:
                existing.publish_status = "failed"
                existing.last_error = exc.message

        await session.flush()
        return existing


class GitHubWebhookService:
    """Verifies HMAC signatures, deduplicates deliveries, and processes PR events."""

    @staticmethod
    def verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
        if not secret or not signature_header:
            return False
        if not signature_header.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    @staticmethod
    async def receive_webhook(
        session: AsyncSession,
        *,
        delivery_id: str,
        event_type: str,
        raw_body: bytes,
        payload: dict[str, Any],
        signature_header: str | None,
        webhook_secret: str | None,
    ) -> GitHubWebhookEvent:
        # Idempotency check by delivery_id
        dup = (
            await session.execute(
                select(GitHubWebhookEvent)
                .where(GitHubWebhookEvent.delivery_id == delivery_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if dup is not None:
            return dup

        inst_id = (payload.get("installation") or {}).get("id")
        secret = webhook_secret or ""
        if not secret and inst_id is not None:
            inst = (
                await session.execute(
                    select(GitHubInstallation)
                    .where(GitHubInstallation.installation_id == int(inst_id))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if inst and inst.webhook_secret_encrypted:
                secret = decrypt_token(inst.webhook_secret_encrypted)

        if not GitHubWebhookService.verify_signature(raw_body, signature_header, secret):
            raise DXIntegrationError("Invalid or missing GitHub webhook signature.", status_code=401)

        repo_data = payload.get("repository") or {}
        gh_repo_id = repo_data.get("id")
        repo: Repository | None = None
        if gh_repo_id is not None:
            repo = (
                await session.execute(
                    select(Repository).where(Repository.github_repo_id == int(gh_repo_id)).limit(1)
                )
            ).scalar_one_or_none()

        pr_data = payload.get("pull_request") or {}
        pr_number = pr_data.get("number")
        commit_sha = (pr_data.get("head") or {}).get("sha") or payload.get("after")

        ev = GitHubWebhookEvent(
            delivery_id=delivery_id,
            event_type=event_type,
            action=payload.get("action"),
            installation_id=int(inst_id) if inst_id is not None else None,
            repository_id=repo.id if repo else None,
            pr_number=int(pr_number) if pr_number is not None else None,
            commit_sha=str(commit_sha)[:64] if commit_sha else None,
            signature_verified=True,
            processing_status=WebhookProcessingStatus.QUEUED,
            correlation_id=str(uuid.uuid4())[:16],
            payload_summary={
                "repo_full_name": repo_data.get("full_name"),
                "pr_title": pr_data.get("title"),
                "sender": (payload.get("sender") or {}).get("login"),
            },
        )
        try:
            async with session.begin_nested():
                session.add(ev)
                await session.flush()
            return ev
        except IntegrityError:
            # Concurrent delivery with the same delivery_id already persisted
            existing = (
                await session.execute(
                    select(GitHubWebhookEvent)
                    .where(GitHubWebhookEvent.delivery_id == delivery_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            raise

    @staticmethod
    async def process_event(
        session: AsyncSession,
        ev: GitHubWebhookEvent,
        *,
        payload: dict[str, Any] | None = None,
        github_client: GitHubClient | None = None,
    ) -> GitHubWebhookEvent:
        ev.processing_status = WebhookProcessingStatus.PROCESSING
        try:
            if ev.event_type == "pull_request" and ev.repository_id and ev.pr_number:
                repo = await session.get(Repository, ev.repository_id)
                if repo is None:
                    raise DXIntegrationError("Mapped repository not found.")
                pr = (
                    await session.execute(
                        select(PullRequest)
                        .where(
                            PullRequest.repository_id == repo.id,
                            PullRequest.github_pr_number == ev.pr_number,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()

                pr_data = (payload or {}).get("pull_request") or {}
                if pr is None:
                    pr = PullRequest(
                        repository_id=repo.id,
                        github_pr_number=ev.pr_number,
                        title=str(pr_data.get("title") or f"PR #{ev.pr_number}"),
                        author_login=str((pr_data.get("user") or {}).get("login") or "github-user"),
                        head_branch=str((pr_data.get("head") or {}).get("ref") or "feature"),
                        base_branch=str((pr_data.get("base") or {}).get("ref") or repo.default_branch),
                        head_sha=ev.commit_sha or "unknown",
                        state=PullRequestState.CLOSED if ev.action == "closed" else PullRequestState.OPEN,
                        html_url=str(pr_data.get("html_url") or f"https://github.com/{repo.full_name}/pull/{ev.pr_number}"),
                        analysis_status=AnalysisStatus.COMPLETED,
                        risk_level=RiskLevel.MEDIUM,
                        risk_score=35,
                    )
                    session.add(pr)
                    await session.flush()
                elif ev.commit_sha and pr.head_sha != ev.commit_sha:
                    pr.head_sha = ev.commit_sha

                await GitHubStatusCheckService.publish_for_pr(
                    session, repository=repo, pr=pr, github_client=github_client
                )
                await GitHubPullRequestCommentService.publish_or_update(
                    session, repository=repo, pr=pr, github_client=github_client
                )

            ev.processing_status = WebhookProcessingStatus.PROCESSED
            ev.processed_at = utcnow()
            ev.error_message = None
        except (DXIntegrationError, GitHubAPIError, ValueError, RuntimeError) as exc:
            ev.retry_count += 1
            ev.error_message = str(exc)[:500]
            ev.processing_status = (
                WebhookProcessingStatus.DEAD_LETTER
                if ev.retry_count >= 3
                else WebhookProcessingStatus.FAILED
            )
            await create_notification(
                session,
                category=NotificationCategory.WEBHOOK_FAILED,
                severity="warning",
                title=f"GitHub Webhook Failed ({ev.event_type})",
                message=ev.error_message,
                resource_type="webhook_event",
                resource_id=str(ev.id),
                action_href="/integrations",
            )
        await session.flush()
        return ev
