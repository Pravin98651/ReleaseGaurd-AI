"""Comprehensive tests for Phase 6 Developer Experience Integration."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import bootstrap_workspace_defaults, create_app
from app.models import (
    AnalysisStatus,
    DeliveryStatus,
    GitHubCheckConclusion,
    IntegrationProvider,
    PullRequest,
    PullRequestState,
    Release,
    ReleaseStatus,
    ReleaseType,
    Repository,
    RiskLevel,
    WebhookProcessingStatus,
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


@pytest.mark.asyncio
async def test_dx_github_checks_comments_webhooks_notifications_and_reports(db_session) -> None:
    repo = Repository(
        github_repo_id=9901,
        owner="novashop",
        name="checkout-dx",
        full_name="novashop/checkout-dx",
    )
    db_session.add(repo)
    await db_session.flush()

    await GitHubIntegrationService.connect_installation(
        db_session,
        installation_id=77001,
        account_login="novashop",
        repositories=[{"id": 9901, "full_name": "novashop/checkout-dx"}],
        webhook_secret="super-secret-hook",
    )

    pr = PullRequest(
        repository_id=repo.id,
        github_pr_number=42,
        title="Checkout DX Risk PR",
        author_login="dev",
        head_branch="feat/dx",
        base_branch="main",
        head_sha="sha_commit_1",
        state=PullRequestState.OPEN,
        html_url="https://github.com/novashop/checkout-dx/pull/42",
        analysis_status=AnalysisStatus.COMPLETED,
        risk_level=RiskLevel.CRITICAL,
        risk_score=85,
        confirmed_risks=[{"file_path": "checkout/pay.py", "description": "Missing payment guard"}],
    )
    db_session.add(pr)
    await db_session.flush()

    # 1. Check run with block_critical -> FAILURE; new commit supersedes old check run
    chk1 = await GitHubStatusCheckService.publish_for_pr(
        db_session, repository=repo, pr=pr, policy_mode="block_critical"
    )
    assert chk1.conclusion == GitHubCheckConclusion.FAILURE
    assert chk1.is_superseded is False

    pr.head_sha = "sha_commit_2"
    pr.risk_level = RiskLevel.LOW
    pr.risk_score = 15
    chk2 = await GitHubStatusCheckService.publish_for_pr(
        db_session, repository=repo, pr=pr, policy_mode="block_critical"
    )
    await db_session.refresh(chk1)
    assert chk1.is_superseded is True
    assert chk2.conclusion == GitHubCheckConclusion.SUCCESS

    # 2. Marker-based PR comment updates in place (no duplicate comment rows)
    cmt1 = await GitHubPullRequestCommentService.publish_or_update(
        db_session, repository=repo, pr=pr
    )
    cmt2 = await GitHubPullRequestCommentService.publish_or_update(
        db_session, repository=repo, pr=pr
    )
    assert cmt1.id == cmt2.id
    assert "<!-- releaseguard-ai:pr-summary -->" in cmt2.body_markdown

    # 3. Webhook HMAC signature verification + deduplication
    payload = {
        "action": "synchronize",
        "installation": {"id": 77001},
        "repository": {"id": 9901, "full_name": "novashop/checkout-dx"},
        "pull_request": {
            "number": 42,
            "title": "Checkout DX Risk PR",
            "head": {"sha": "sha_commit_3"},
        },
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(b"super-secret-hook", raw, hashlib.sha256).hexdigest()

    with pytest.raises(DXIntegrationError):
        await GitHubWebhookService.receive_webhook(
            db_session,
            delivery_id="deliv-bad",
            event_type="pull_request",
            raw_body=raw,
            payload=payload,
            signature_header="sha256=invalid",
            webhook_secret=None,
        )

    ev1 = await GitHubWebhookService.receive_webhook(
        db_session,
        delivery_id="deliv-1",
        event_type="pull_request",
        raw_body=raw,
        payload=payload,
        signature_header=sig,
        webhook_secret=None,
    )
    ev_dup = await GitHubWebhookService.receive_webhook(
        db_session,
        delivery_id="deliv-1",
        event_type="pull_request",
        raw_body=raw,
        payload=payload,
        signature_header=sig,
        webhook_secret=None,
    )
    assert ev1.id == ev_dup.id
    await GitHubWebhookService.process_event(db_session, ev1, payload=payload)
    assert ev1.processing_status == WebhookProcessingStatus.PROCESSED

    # 4. Slack & Discord notification delivery + deduplication
    await DeveloperNotificationService.connect_provider(
        db_session,
        provider=IntegrationProvider.SLACK,
        webhook_url="https://hooks.slack.com/services/T000/B000/XXX",
        channel_label="#releases",
    )
    mock_transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    deliveries = await DeveloperNotificationService.dispatch_event(
        db_session,
        event_type="pr.analyzed",
        title="PR #42 Analyzed",
        message="Risk level LOW",
        dedupe_key="pr-42-sha-3",
        transport=mock_transport,
    )
    assert len(deliveries) == 1
    assert deliveries[0].status == DeliveryStatus.SENT
    # Duplicate dedupe_key is suppressed
    dup_deliveries = await DeveloperNotificationService.dispatch_event(
        db_session,
        event_type="pr.analyzed",
        title="PR #42 Analyzed",
        message="Risk level LOW",
        dedupe_key="pr-42-sha-3",
        transport=mock_transport,
    )
    assert dup_deliveries == []

    # 5. Readiness report + cryptographic share link + revocation
    rel = Release(
        release_key="REL-DX-1",
        title="Checkout DX Release",
        release_type=ReleaseType.FEATURE,
        target_environment="staging",
        status=ReleaseStatus.DRAFT,
        repository_id=repo.id,
        pull_request_id=pr.id,
    )
    db_session.add(rel)
    await db_session.flush()

    report = await ReleaseReadinessReportService.generate_report(db_session, rel)
    assert "REL-DX-1" in report.markdown_content
    share, raw_token = await ShareableReportService.create_share_link(
        db_session, report=report, expires_in_hours=12
    )
    resolved = await ShareableReportService.resolve_shared_report(db_session, raw_token=raw_token)
    assert resolved.id == report.id

    await ShareableReportService.revoke_share_link(db_session, share)
    with pytest.raises(DXIntegrationError) as exc_revoked:
        await ShareableReportService.resolve_shared_report(db_session, raw_token=raw_token)
    assert exc_revoked.value.status_code == 410


@pytest.mark.asyncio
async def test_dx_rest_api_endpoints(async_engine, session_factory) -> None:
    async with session_factory() as s:
        await bootstrap_workspace_defaults(s, bootstrap_env_tokens=False)
        await s.commit()

    app = create_app(engine=async_engine, session_factory=session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/v1/integrations/github/installations")).status_code == 200
        assert (await client.get("/api/v1/integrations/github/events")).status_code == 200
        assert (await client.get("/api/v1/integrations/notifications")).status_code == 200
        # Invalid webhook signature -> 401
        bad_hook = await client.post(
            "/api/v1/webhooks/github",
            json={"action": "opened"},
            headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=bad"},
        )
        assert bad_hook.status_code == 401
