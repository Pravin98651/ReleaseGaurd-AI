"""Developer Experience Integration REST & Webhook API (Phase 6)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import (
    AdminOperator,
    AuthenticatedOperator,
    DBSession,
    DeveloperOperator,
    build_github_client,
)
from app.core.config import get_settings
from app.models import (
    GitHubCheckRun,
    GitHubInstallation,
    GitHubWebhookEvent,
    IntegrationConnection,
    IntegrationProvider,
    NotificationDelivery,
    PullRequest,
    PullRequestAnalysisComment,
    Release,
    ReleaseReadinessReport,
    Repository,
    ShareableReport,
)
from app.schemas.dx_integration import (
    GitHubAppConnectRequest,
    GitHubCheckRunOut,
    GitHubInstallationOut,
    GitHubWebhookEventOut,
    NotificationDeliveryOut,
    NotificationWebhookConnectRequest,
    PRPublishRequest,
    PullRequestCommentOut,
    ReleaseReadinessReportOut,
    ShareableReportOut,
    ShareLinkCreateRequest,
)
from app.services.dx import (
    DeveloperNotificationService,
    DXIntegrationError,
    GitHubIntegrationService,
    GitHubPullRequestCommentService,
    GitHubStatusCheckService,
    GitHubWebhookService,
    ReleaseReadinessReportService,
    ShareableReportService,
)

dx_router = APIRouter(tags=["developer-experience"])


@dx_router.post("/integrations/github/app/connect", response_model=GitHubInstallationOut)
async def connect_github_app(
    body: GitHubAppConnectRequest,
    session: DBSession,
    _user: AdminOperator,
) -> GitHubInstallationOut:
    inst = await GitHubIntegrationService.connect_installation(
        session,
        installation_id=body.installation_id,
        account_login=body.account_login,
        account_type=body.account_type,
        repositories=body.repositories,
        permissions=body.permissions,
        access_token=body.access_token,
        webhook_secret=body.webhook_secret,
    )
    await session.commit()
    return GitHubInstallationOut.model_validate(inst)


@dx_router.get("/integrations/github/installations", response_model=list[GitHubInstallationOut])
async def list_github_installations(
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[GitHubInstallationOut]:
    rows = list(
        (await session.execute(select(GitHubInstallation).order_by(GitHubInstallation.created_at.desc())))
        .scalars()
        .all()
    )
    return [GitHubInstallationOut.model_validate(r) for r in rows]


@dx_router.delete("/integrations/github/installations/{installation_id}")
async def remove_github_installation(
    installation_id: int,
    session: DBSession,
    _user: AdminOperator,
) -> dict[str, bool]:
    ok = await GitHubIntegrationService.remove_installation(session, installation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="GitHub installation not found.")
    await session.commit()
    return {"disconnected": True}


@dx_router.get("/integrations/github/events", response_model=list[GitHubWebhookEventOut])
async def list_github_webhook_events(
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[GitHubWebhookEventOut]:
    rows = list(
        (
            await session.execute(
                select(GitHubWebhookEvent).order_by(GitHubWebhookEvent.received_at.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [GitHubWebhookEventOut.model_validate(r) for r in rows]


@dx_router.post("/integrations/github/events/{event_id}/retry", response_model=GitHubWebhookEventOut)
async def retry_github_webhook_event(
    event_id: uuid.UUID,
    session: DBSession,
    _user: DeveloperOperator,
) -> GitHubWebhookEventOut:
    ev = await session.get(GitHubWebhookEvent, event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="Webhook event not found.")
    gh = await build_github_client(session)
    updated = await GitHubWebhookService.process_event(session, ev, github_client=gh)
    await session.commit()
    return GitHubWebhookEventOut.model_validate(updated)


@dx_router.post("/webhooks/github", response_model=GitHubWebhookEventOut)
async def receive_github_webhook(
    request: Request,
    session: DBSession,
) -> GitHubWebhookEventOut:
    raw_body = await request.body()
    delivery_id = request.headers.get("X-GitHub-Delivery") or str(uuid.uuid4())
    event_type = request.headers.get("X-GitHub-Event") or "ping"
    signature = request.headers.get("X-Hub-Signature-256")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc

    cfg = get_settings()
    secret = getattr(cfg, "GITHUB_WEBHOOK_SECRET", "") or ""
    try:
        ev = await GitHubWebhookService.receive_webhook(
            session,
            delivery_id=delivery_id,
            event_type=event_type,
            raw_body=raw_body,
            payload=payload,
            signature_header=signature,
            webhook_secret=secret or None,
        )
        gh = await build_github_client(session)
        await GitHubWebhookService.process_event(session, ev, payload=payload, github_client=gh)
        await session.commit()
        return GitHubWebhookEventOut.model_validate(ev)
    except DXIntegrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@dx_router.post("/github/pull-requests/{repository_id}/{pull_number}/analyze")
async def analyze_and_publish_pr(
    repository_id: uuid.UUID,
    pull_number: int,
    session: DBSession,
    _user: DeveloperOperator,
    body: PRPublishRequest | None = None,
) -> dict[str, Any]:
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    pr = (
        await session.execute(
            select(PullRequest)
            .where(
                PullRequest.repository_id == repository_id,
                PullRequest.github_pr_number == pull_number,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Pull request not found.")
    req = body or PRPublishRequest()
    gh = await build_github_client(session)
    chk = await GitHubStatusCheckService.publish_for_pr(
        session, repository=repo, pr=pr, policy_mode=req.policy_mode, github_client=gh
    )
    cmt = await GitHubPullRequestCommentService.publish_or_update(
        session,
        repository=repo,
        pr=pr,
        config={"include_historical_intelligence": req.include_historical_intelligence},
        github_client=gh,
    )
    await session.commit()
    return {
        "check_run": GitHubCheckRunOut.model_validate(chk),
        "comment": PullRequestCommentOut.model_validate(cmt),
    }


@dx_router.get("/github/pull-requests/{repository_id}/{pull_number}/status")
async def get_pr_dx_status(
    repository_id: uuid.UUID,
    pull_number: int,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    pr = (
        await session.execute(
            select(PullRequest)
            .where(
                PullRequest.repository_id == repository_id,
                PullRequest.github_pr_number == pull_number,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Pull request not found.")
    checks = list(
        (
            await session.execute(
                select(GitHubCheckRun)
                .where(GitHubCheckRun.pull_request_id == pr.id)
                .order_by(GitHubCheckRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    comment = (
        await session.execute(
            select(PullRequestAnalysisComment)
            .where(PullRequestAnalysisComment.pull_request_id == pr.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "check_runs": [GitHubCheckRunOut.model_validate(c) for c in checks],
        "comment": PullRequestCommentOut.model_validate(comment) if comment else None,
    }


@dx_router.post("/github/pull-requests/{repository_id}/{pull_number}/publish-summary", response_model=PullRequestCommentOut)
async def publish_pr_summary_comment(
    repository_id: uuid.UUID,
    pull_number: int,
    session: DBSession,
    _user: DeveloperOperator,
) -> PullRequestCommentOut:
    repo = await session.get(Repository, repository_id)
    pr = (
        await session.execute(
            select(PullRequest)
            .where(
                PullRequest.repository_id == repository_id,
                PullRequest.github_pr_number == pull_number,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if repo is None or pr is None:
        raise HTTPException(status_code=404, detail="Repository or pull request not found.")
    gh = await build_github_client(session)
    cmt = await GitHubPullRequestCommentService.publish_or_update(
        session, repository=repo, pr=pr, github_client=gh
    )
    await session.commit()
    return PullRequestCommentOut.model_validate(cmt)


@dx_router.post("/releases/{release_id}/reports", response_model=ReleaseReadinessReportOut)
async def generate_release_readiness_report(
    release_id: uuid.UUID,
    session: DBSession,
    user: DeveloperOperator,
) -> ReleaseReadinessReportOut:
    release = await session.get(Release, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found.")
    report = await ReleaseReadinessReportService.generate_report(
        session, release, created_by_id=user.id
    )
    await session.commit()
    return ReleaseReadinessReportOut.model_validate(report)


@dx_router.get("/releases/{release_id}/reports", response_model=list[ReleaseReadinessReportOut])
async def list_release_readiness_reports(
    release_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[ReleaseReadinessReportOut]:
    rows = list(
        (
            await session.execute(
                select(ReleaseReadinessReport)
                .where(ReleaseReadinessReport.release_id == release_id)
                .order_by(ReleaseReadinessReport.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ReleaseReadinessReportOut.model_validate(r) for r in rows]


@dx_router.get("/reports/{report_id}", response_model=ReleaseReadinessReportOut)
async def get_release_readiness_report(
    report_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> ReleaseReadinessReportOut:
    report = await session.get(ReleaseReadinessReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Readiness report not found.")
    return ReleaseReadinessReportOut.model_validate(report)


@dx_router.post("/reports/{report_id}/share", response_model=ShareableReportOut)
async def create_report_share_link(
    report_id: uuid.UUID,
    body: ShareLinkCreateRequest,
    session: DBSession,
    user: DeveloperOperator,
) -> ShareableReportOut:
    report = await session.get(ReleaseReadinessReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Readiness report not found.")
    share, raw_token = await ShareableReportService.create_share_link(
        session, report=report, created_by_id=user.id, expires_in_hours=body.expires_in_hours
    )
    await session.commit()
    out = ShareableReportOut.model_validate(share)
    out.share_token = raw_token
    out.share_url = f"/shared/{raw_token}"
    return out


@dx_router.get("/reports/{report_id}/shares", response_model=list[ShareableReportOut])
async def list_report_share_links(
    report_id: uuid.UUID,
    session: DBSession,
    _user: AuthenticatedOperator,
) -> list[ShareableReportOut]:
    rows = list(
        (
            await session.execute(
                select(ShareableReport)
                .where(ShareableReport.report_id == report_id)
                .order_by(ShareableReport.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ShareableReportOut.model_validate(r) for r in rows]


@dx_router.delete("/reports/{report_id}/shares/{share_id}", response_model=ShareableReportOut)
async def revoke_report_share_link(
    report_id: uuid.UUID,
    share_id: uuid.UUID,
    session: DBSession,
    _user: DeveloperOperator,
) -> ShareableReportOut:
    share = await session.get(ShareableReport, share_id)
    if share is None or share.report_id != report_id:
        raise HTTPException(status_code=404, detail="Share link not found.")
    updated = await ShareableReportService.revoke_share_link(session, share)
    await session.commit()
    return ShareableReportOut.model_validate(updated)


@dx_router.get("/shared/{token}", response_model=ReleaseReadinessReportOut)
async def access_shared_report(
    token: str,
    request: Request,
    session: DBSession,
) -> ReleaseReadinessReportOut:
    try:
        report = await ShareableReportService.resolve_shared_report(
            session,
            raw_token=token,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
        )
        await session.commit()
        return ReleaseReadinessReportOut.model_validate(report)
    except DXIntegrationError as exc:
        await session.commit()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@dx_router.get("/integrations/notifications")
async def list_notification_integrations_and_deliveries(
    session: DBSession,
    _user: AuthenticatedOperator,
) -> dict[str, Any]:
    conns = list(
        (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.provider.in_(
                        [IntegrationProvider.SLACK, IntegrationProvider.DISCORD]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    deliveries = list(
        (
            await session.execute(
                select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(30)
            )
        )
        .scalars()
        .all()
    )
    return {
        "connections": [
            {
                "id": str(c.id),
                "provider": c.provider.value,
                "status": c.status.value,
                "account_label": c.external_account_name,
                "token_hint": c.token_hint,
                "verified_at": c.last_verified_at.isoformat() if c.last_verified_at else None,
            }
            for c in conns
        ],
        "deliveries": [NotificationDeliveryOut.model_validate(d) for d in deliveries],
    }


@dx_router.post("/integrations/slack/connect")
async def connect_slack_webhook(
    body: NotificationWebhookConnectRequest,
    session: DBSession,
    user: AdminOperator,
) -> dict[str, Any]:
    try:
        conn = await DeveloperNotificationService.connect_provider(
            session,
            provider=IntegrationProvider.SLACK,
            webhook_url=body.webhook_url,
            channel_label=body.channel_label,
            connected_by_id=user.id,
        )
        await session.commit()
        return {"id": str(conn.id), "provider": conn.provider.value, "status": conn.status.value}
    except DXIntegrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@dx_router.post("/integrations/discord/connect")
async def connect_discord_webhook(
    body: NotificationWebhookConnectRequest,
    session: DBSession,
    user: AdminOperator,
) -> dict[str, Any]:
    try:
        conn = await DeveloperNotificationService.connect_provider(
            session,
            provider=IntegrationProvider.DISCORD,
            webhook_url=body.webhook_url,
            channel_label=body.channel_label,
            connected_by_id=user.id,
        )
        await session.commit()
        return {"id": str(conn.id), "provider": conn.provider.value, "status": conn.status.value}
    except DXIntegrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@dx_router.delete("/integrations/notifications/{integration_id}")
async def disconnect_notification_integration(
    integration_id: uuid.UUID,
    session: DBSession,
    _user: AdminOperator,
) -> dict[str, bool]:
    conn = await session.get(IntegrationConnection, integration_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Notification integration not found.")
    await session.delete(conn)
    await session.commit()
    return {"deleted": True}
